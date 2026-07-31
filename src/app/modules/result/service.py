"""Regras do Resultado Fiscal (Módulo 8): apuração do Anexo 6, cascata e reconciliação.

Fonte primária: ``silver.siconfi_rreo`` (Anexo 6, real). Todo número é lido do dado do
SICONFI e reconciliado: primário = receitas − despesas primárias; nominal = primário −
juros = −variação da DCL (abaixo da linha), com os ajustes metodológicos explicando a
divergência. Cruza com a Sprint 8 (Dívida) para conferir a DCL. Rastreável (source_ref +
memória + as_of).
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError
from app.modules.catalog import service as catalog_service
from app.modules.debt import service as debt_service
from app.modules.indicators import repository as indicators_repo
from app.modules.indicators import serie_ajuste
from app.modules.indicators.schemas import SerieAjuste
from app.modules.result import repository, resultado
from app.modules.result.models import FatoResultado, MetaFiscal
from app.modules.result.schemas import (
    CascataOut,
    CascataPasso,
    ComparacaoResultado,
    ComponenteOut,
    MemoriaResultado,
    MetaCadastro,
    MetaFiscalUpsert,
    MetaOut,
    MetaResumo,
    ReconAjuste,
    ReconciliacaoOut,
    ResultadoDetalhe,
    ResultadoValores,
    SerieResultadoItem,
)
from app.modules.tenancy import repository as tenancy_repo
from app.shared import periodo as periodo_util
from app.shared.ausencia import ausencia_de_entrega
from app.shared.source_ref import SourceRef

_ANEXO = "Anexo 06"
_PERIODO_BIMESTRAL_RE = re.compile(r"^(\d{4})-B([1-6])$")
_TOL_IDENTIDADE = Decimal("100")  # R$ (rounding do demonstrativo) nas identidades internas
_TOL_SPRINT8_PCT = Decimal("5")  # % de divergência tolerada no cruzamento com a Dívida

_OBS_RECONCILIACAO = (
    "Acima da linha (orçamentário) e abaixo da linha (variação da dívida) são duas "
    "apurações do mesmo resultado nominal; os ajustes metodológicos explicam a divergência. "
    "Valores oficiais do RREO Anexo 6 — não recalculados."
)


def _source_ref(periodo: str, versao: str) -> SourceRef:
    return SourceRef(relatorio="RREO", anexo=_ANEXO, periodo=periodo, versao_entrega=versao)


def _resolve_versao(
    session: Session, cod_ibge: str, periodo: str, as_of: datetime | None
) -> str:
    versao = indicators_repo.resolve_versao_rreo(
        session, cod_ibge=cod_ibge, periodo=periodo, as_of=as_of
    )
    if versao is None:
        raise ausencia_de_entrega(
            session,
            cod_ibge=cod_ibge,
            relatorio="RREO",
            periodo=periodo,
            title="RREO ausente",
            detail=f"Sem RREO vigente para {cod_ibge} em {periodo}.",
        )
    return versao


def _bimestre(periodo: str) -> int:
    m = _PERIODO_BIMESTRAL_RE.match(periodo)
    if m is None:
        raise AppError(
            status=422, title="Período inválido",
            detail=f"Resultado exige período bimestral 'AAAA-Bn'; recebido '{periodo}'.",
        )
    return int(m.group(2))


def _periodo_rgf(periodo: str) -> str | None:
    """Período RGF (quadrimestral) correspondente, para cruzar a DCL (só bimestres pares)."""
    m = _PERIODO_BIMESTRAL_RE.match(periodo)
    if m is None:
        return None
    b = int(m.group(2))
    return f"{m.group(1)}-Q{b // 2}" if b % 2 == 0 else None


def _decimal(v: object) -> Decimal | None:
    return Decimal(str(v)) if v is not None else None


# --- materialização silver → gold ---
def _apurar(session: Session, cod_ibge: str, periodo: str, versao: str) -> resultado.Apuracao:
    linhas = [
        resultado.LinhaAnexo6(cod_conta=r.cod_conta, coluna=r.coluna, valor=_decimal(r.valor))
        for r in repository.read_anexo6(
            session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
        )
    ]
    return resultado.apurar(linhas)


def materializar_resultado(
    session: Session, cod_ibge: str, periodo: str, versao: str
) -> tuple[FatoResultado, resultado.Apuracao]:
    """ETL do RREO Anexo 6 → fato_resultado + fato_ajuste_metodologico (idempotente)."""
    ap = _apurar(session, cod_ibge, periodo, versao)
    repository.upsert_fato(
        session,
        {
            "cod_ibge": cod_ibge, "periodo": periodo, "versao_entrega": versao,
            **{m: ap.medidas.get(m) for m in resultado.MEDIDAS},
        },
    )
    repository.replace_ajustes(
        session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao,
        ajustes=[
            {
                "cod_ibge": cod_ibge, "periodo": periodo, "versao_entrega": versao,
                "ajuste_codigo": a.codigo, "descricao": a.descricao, "valor": a.valor,
            }
            for a in ap.ajustes
        ],
    )
    fato = repository.get_fato(
        session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
    )
    assert fato is not None
    return fato, ap


def _obter(
    session: Session, cod_ibge: str, periodo: str, as_of: datetime | None
) -> tuple[FatoResultado, resultado.Apuracao, str]:
    versao = _resolve_versao(session, cod_ibge, periodo, as_of)
    fato, ap = materializar_resultado(session, cod_ibge, periodo, versao)
    return fato, ap, versao


# --- derivações ---
def _valores(ap: resultado.Apuracao) -> ResultadoValores:
    m = ap.medidas
    return ResultadoValores(
        receita_primaria=m.get("receita_primaria"),
        despesa_primaria=m.get("despesa_primaria"),
        resultado_primario=m.get("resultado_primario"),
        juros_liquidos=resultado.juros_liquidos(m),
        resultado_nominal=m.get("resultado_nominal"),
        dcl_inicio=m.get("dcl_inicio"),
        dcl_fim=m.get("dcl_fim"),
        variacao_dcl=resultado.variacao_dcl(m),
        resultado_nominal_abaixo=m.get("resultado_nominal_abaixo"),
        resultado_primario_abaixo=m.get("resultado_primario_abaixo"),
        meta_primario=m.get("meta_primario"),
        meta_nominal=m.get("meta_nominal"),
    )


def _identidade_primario(m: dict[str, Decimal]) -> bool | None:
    rec = m.get("receita_primaria")
    desp = m.get("despesa_primaria")
    res = m.get("resultado_primario")
    if rec is None or desp is None or res is None:
        return None
    return abs(res - (rec - desp)) <= _TOL_IDENTIDADE


def _identidade_nominal_dcl(m: dict[str, Decimal]) -> bool | None:
    abaixo, inicio, fim = (
        m.get("resultado_nominal_abaixo"), m.get("dcl_inicio"), m.get("dcl_fim")
    )
    if abaixo is None or inicio is None or fim is None:
        return None
    return abs(abaixo - (inicio - fim)) <= _TOL_IDENTIDADE


def _meta_resumo(ap: resultado.Apuracao) -> MetaResumo:
    m = ap.medidas
    meta_p = m.get("meta_primario")
    real_p = m.get("resultado_primario")
    esforco = (meta_p - real_p) if (meta_p is not None and real_p is not None) else None
    atingido = (real_p >= meta_p) if (meta_p is not None and real_p is not None) else None
    return MetaResumo(
        informada=meta_p is not None or m.get("meta_nominal") is not None,
        meta_primario=meta_p,
        realizado_primario=real_p,
        esforco_primario=esforco,
        atingido_primario=atingido,
        meta_nominal=m.get("meta_nominal"),
        realizado_nominal=m.get("resultado_nominal"),
    )


# --- série e comparação ---
def _serie(
    session: Session, cod_ibge: str, base_periodo: str
) -> tuple[list[SerieResultadoItem], SerieAjuste]:
    """Série multi-exercício + insumos de comparabilidade (real por IPCA e per capita)."""
    serie: list[SerieResultadoItem] = []
    for periodo in repository.distinct_periodos_fato(session, cod_ibge=cod_ibge):
        versao = indicators_repo.resolve_versao_rreo(
            session, cod_ibge=cod_ibge, periodo=periodo
        )
        if versao is None:
            continue
        fato = repository.get_fato(
            session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
        )
        if fato is None:
            continue
        serie.append(
            SerieResultadoItem(
                periodo=periodo,
                resultado_primario=fato.resultado_primario,
                resultado_nominal=fato.resultado_nominal,
            )
        )
    ajuste = serie_ajuste.calcular(
        session, cod_ibge, [s.periodo for s in serie], base_periodo
    )
    por_periodo = serie_ajuste.indexar(ajuste)
    for item in serie:
        a = por_periodo.get(item.periodo)
        item.resultado_primario_real = serie_ajuste.real(item.resultado_primario, a)
        item.resultado_primario_per_capita = serie_ajuste.per_capita(
            item.resultado_primario, a
        )
        item.populacao = a.populacao if a else None
    return serie, ajuste


def _comparacao(
    serie: list[SerieResultadoItem], periodo: str, atual: Decimal | None
) -> ComparacaoResultado | None:
    m = _PERIODO_BIMESTRAL_RE.match(periodo)
    if m is None:
        return None
    anterior_cod = f"{int(m.group(1)) - 1}-B{m.group(2)}"
    anterior = next((s for s in serie if s.periodo == anterior_cod), None)
    if anterior is None:
        return None
    delta = (
        atual - anterior.resultado_primario
        if atual is not None and anterior.resultado_primario is not None
        else None
    )
    return ComparacaoResultado(
        periodo_anterior=anterior_cod,
        resultado_primario_anterior=anterior.resultado_primario,
        delta_rs=delta,
    )


def _componentes(itens: list[resultado.ComponentePrimario]) -> list[ComponenteOut]:
    return [
        ComponenteOut(codigo=c.codigo, descricao=c.descricao, valor=c.valor)
        for c in sorted(itens, key=lambda x: x.valor, reverse=True)
    ]


def _recon_ajustes(ap: resultado.Apuracao) -> list[ReconAjuste]:
    return [ReconAjuste(codigo=a.codigo, descricao=a.descricao, valor=a.valor) for a in ap.ajustes]


# --- endpoints ---
def build_detalhe(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> ResultadoDetalhe:
    """Cabeçalho (resultado × meta) + componentes primários + série (Padrão de Detalhe)."""
    fato, ap, versao = _obter(session, cod_ibge, periodo, as_of)
    valores = _valores(ap)
    serie, ajuste = _serie(session, cod_ibge, periodo)
    return ResultadoDetalhe(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        valores=valores,
        # Detalhe é a via consumida por relatórios e agregados: **só meta oficial (A6)**.
        # A meta declarada pela organização vive apenas em ``/resultado/meta``.
        meta=_meta_resumo(ap),
        receita_componentes=_componentes(ap.receita_componentes),
        despesa_componentes=_componentes(ap.despesa_componentes),
        serie=serie,
        serie_ajuste=ajuste,
        comparacao=_comparacao(serie, periodo, valores.resultado_primario),
        periodo_breadcrumb=catalog_service.periodo_breadcrumb(session, periodo),
        source_ref=_source_ref(periodo, versao),
    )


def build_cascata(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> CascataOut:
    """Waterfall: receita→−despesa→primário→−juros→nominal (acima) e DCL início→fim (abaixo)."""
    _, ap, versao = _obter(session, cod_ibge, periodo, as_of)
    m = ap.medidas
    rec = m.get("receita_primaria")
    desp = m.get("despesa_primaria")
    primario = m.get("resultado_primario")
    juros = resultado.juros_liquidos(m)
    nominal = m.get("resultado_nominal")
    acima = [
        CascataPasso(rotulo="Receita primária", valor=rec, tipo="base", acumulado=rec),
        CascataPasso(
            rotulo="(−) Despesa primária", valor=(-desp if desp is not None else None),
            tipo="subtrai",
            acumulado=(rec - desp) if (rec is not None and desp is not None) else None,
        ),
        CascataPasso(
            rotulo="= Resultado primário", valor=primario, tipo="subtotal", acumulado=primario
        ),
        CascataPasso(
            rotulo="(−) Juros líquidos", valor=(-juros if juros is not None else None),
            tipo="subtrai",
            acumulado=(primario - juros) if (primario is not None and juros is not None) else None,
        ),
        CascataPasso(
            rotulo="= Resultado nominal", valor=nominal, tipo="resultado", acumulado=nominal
        ),
    ]
    inicio = m.get("dcl_inicio")
    fim = m.get("dcl_fim")
    var = resultado.variacao_dcl(m)
    abaixo = [
        CascataPasso(rotulo="DCL início do exercício", valor=inicio, tipo="base", acumulado=inicio),
        CascataPasso(
            rotulo="(+) Variação da DCL no período", valor=var, tipo="soma", acumulado=fim
        ),
        CascataPasso(rotulo="= DCL até o bimestre", valor=fim, tipo="subtotal", acumulado=fim),
        CascataPasso(
            rotulo="Resultado nominal (abaixo da linha) = −variação",
            valor=m.get("resultado_nominal_abaixo"), tipo="resultado",
            acumulado=m.get("resultado_nominal_abaixo"),
        ),
    ]
    return CascataOut(
        cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao,
        acima_da_linha=acima, abaixo_da_linha=abaixo, source_ref=_source_ref(periodo, versao),
    )


def build_reconciliacao(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> ReconciliacaoOut:
    """Acima × abaixo da linha, ajustes metodológicos e cruzamento da DCL com a Sprint 8."""
    _, ap, versao = _obter(session, cod_ibge, periodo, as_of)
    m = ap.medidas
    ajustes = _recon_ajustes(ap)
    soma = resultado.soma_ajustes(ap.ajustes)
    abaixo = m.get("resultado_nominal_abaixo")
    abaixo_aj = m.get("resultado_nominal_abaixo_ajustado")
    if abaixo_aj is None and abaixo is not None:
        abaixo_aj = abaixo + soma
    acima = m.get("resultado_nominal")
    diverg = (acima - abaixo_aj) if (acima is not None and abaixo_aj is not None) else None

    # Cruzamento com a Sprint 8 (Dívida) — DCL do RGF no quadrimestre correspondente.
    dcl_s8: Decimal | None = None
    concilia_s8: bool | None = None
    periodo_rgf = _periodo_rgf(periodo)
    if periodo_rgf is not None:
        fato_divida = debt_service.obter_dcl(session, cod_ibge, periodo_rgf, as_of=as_of)
        if fato_divida is not None:
            dcl_s8 = fato_divida.dcl
            fim = m.get("dcl_fim")
            if fim is not None and fim != 0:
                concilia_s8 = abs((dcl_s8 - fim) / fim * Decimal(100)) <= _TOL_SPRINT8_PCT

    return ReconciliacaoOut(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        nominal_acima=acima,
        nominal_abaixo=abaixo,
        ajustes=ajustes,
        soma_ajustes=soma,
        nominal_abaixo_ajustado=abaixo_aj,
        divergencia_acima_abaixo=diverg,
        variacao_dcl=resultado.variacao_dcl(m),
        dcl_inicio=m.get("dcl_inicio"),
        dcl_fim=m.get("dcl_fim"),
        dcl_sprint8=dcl_s8,
        dcl_sprint8_periodo=periodo_rgf,
        concilia_com_sprint8=concilia_s8,
        identidade_primario_ok=_identidade_primario(m),
        identidade_nominal_dcl_ok=_identidade_nominal_dcl(m),
        observacao=_OBS_RECONCILIACAO,
        source_ref=_source_ref(periodo, versao),
    )


def _aplicar_meta_manual(
    resumo: MetaResumo, cadastros: list[MetaFiscal]
) -> tuple[MetaResumo, list[MetaCadastro]]:
    """Completa o resumo com a meta declarada pela organização (nunca sobrepõe o A6)."""
    por_indicador = {c.indicador: c for c in cadastros}
    primario = por_indicador.get("primario")
    nominal = por_indicador.get("nominal")
    meta_p = Decimal(primario.valor) if primario is not None else None
    real_p = resumo.realizado_primario
    comparavel = meta_p is not None and real_p is not None
    novo = MetaResumo(
        informada=True,
        meta_primario=meta_p,
        realizado_primario=real_p,
        esforco_primario=(meta_p - real_p) if comparavel else None,  # type: ignore[operator]
        atingido_primario=(real_p >= meta_p) if comparavel else None,  # type: ignore[operator]
        meta_nominal=Decimal(nominal.valor) if nominal is not None else None,
        realizado_nominal=resumo.realizado_nominal,
    )
    return novo, [_meta_cadastro(c) for c in cadastros]


def _meta_cadastro(row: MetaFiscal) -> MetaCadastro:
    return MetaCadastro(
        id=row.id,
        exercicio=row.exercicio,
        indicador=row.indicador,
        valor=Decimal(row.valor),
        fonte_declarada=row.fonte_declarada,
        observacao=row.observacao,
        atualizado_em=row.atualizado_em,
        atualizado_por=row.atualizado_por,
    )


def build_meta(
    session: Session,
    cod_ibge: str,
    periodo: str,
    *,
    as_of: datetime | None = None,
    org_id: uuid.UUID | None = None,
) -> MetaOut:
    """Realizado × meta + projeção de fechamento (linear) e esforço necessário.

    A meta oficial é a do **RREO Anexo 6**. Quando o ente não a publica, cai para a meta da
    LDO **declarada pela organização** (``op.meta_fiscal``) — passando ``org_id``. Essa via
    é exclusiva da tela do ente: agregados e relatórios institucionais chamam sem ``org_id``
    e continuam vendo apenas dado oficial (decisão §11.5 da auditoria).
    """
    _, ap, versao = _obter(session, cod_ibge, periodo, as_of)
    resumo = _meta_resumo(ap)
    bimestre = _bimestre(periodo)
    fracao = Decimal(bimestre) / Decimal(6)
    real_p = resumo.realizado_primario
    projecao = (real_p / fracao) if (real_p is not None and fracao > 0) else None

    origem = "a6" if resumo.informada else "ausente"
    cadastros: list[MetaCadastro] = []
    if not resumo.informada and org_id is not None:
        exercicio = periodo_util.parse(periodo)[0]
        registros = repository.list_metas_fiscais(
            session, org_id=org_id, cod_ibge=cod_ibge, exercicio=exercicio
        )
        if registros:
            resumo, cadastros = _aplicar_meta_manual(resumo, registros)
            origem = "manual"

    esforco = (
        resumo.meta_primario - projecao
        if resumo.meta_primario is not None and projecao is not None
        else None
    )
    obs = {
        "a6": (
            "Meta publicada pelo ente no RREO Anexo 6. Projeção linear de fechamento "
            "(realizado ÷ fração do exercício); esforço = meta − projeção."
        ),
        "manual": (
            "Meta da LDO cadastrada nesta organização — o ente não a publicou no Anexo 6. "
            "Vale só nas telas deste ente: não entra em comparações agregadas nem em "
            "relatório institucional."
        ),
        "ausente": "Meta fiscal não informada no Anexo 6 deste ente/período.",
    }[origem]
    return MetaOut(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        resumo=resumo,
        bimestre=bimestre,
        fracao_exercicio=fracao,
        projecao_primario=projecao,
        esforco_necessario=esforco,
        origem=origem,
        restrita_ao_ente=origem == "manual",
        cadastros=cadastros,
        observacao=obs,
        source_ref=_source_ref(periodo, versao),
    )


# --- cadastro da meta da LDO (op.meta_fiscal) ---
def listar_metas_fiscais(
    session: Session, principal: Principal, cod_ibge: str
) -> list[MetaCadastro]:
    if principal.org_id is None:
        return []
    return [
        _meta_cadastro(m)
        for m in repository.list_metas_fiscais(
            session, org_id=principal.org_id, cod_ibge=cod_ibge
        )
    ]


def salvar_meta_fiscal(
    session: Session, principal: Principal, cod_ibge: str, body: MetaFiscalUpsert
) -> MetaCadastro:
    """Cadastra/atualiza a meta da LDO do ente nesta organização (auditado)."""
    if principal.org_id is None:
        raise AppError(
            status=403, title="Sem organização ativa",
            detail="Cadastro de meta exige uma organização ativa.",
        )
    row = repository.upsert_meta_fiscal(
        session,
        {
            "org_id": principal.org_id,
            "cod_ibge": cod_ibge,
            "exercicio": body.exercicio,
            "indicador": body.indicador,
            "valor": body.valor,
            "fonte_declarada": body.fonte_declarada,
            "observacao": body.observacao,
            "atualizado_por": principal.usuario_id,
        },
    )
    tenancy_repo.insert_audit_log(
        session,
        org_id=principal.org_id,
        usuario_id=principal.usuario_id,
        acao="meta_fiscal.salvar",
        recurso=json.dumps(
            {
                "cod_ibge": cod_ibge,
                "exercicio": body.exercicio,
                "indicador": body.indicador,
                "valor": str(body.valor),
                "fonte_declarada": body.fonte_declarada,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return _meta_cadastro(row)


def excluir_meta_fiscal(
    session: Session, principal: Principal, cod_ibge: str, meta_id: uuid.UUID
) -> None:
    if principal.org_id is None or not repository.delete_meta_fiscal(
        session, org_id=principal.org_id, meta_id=meta_id
    ):
        raise AppError(
            status=404, title="Meta inexistente",
            detail="Não há meta fiscal com esse identificador nesta organização.",
        )
    tenancy_repo.insert_audit_log(
        session,
        org_id=principal.org_id,
        usuario_id=principal.usuario_id,
        acao="meta_fiscal.excluir",
        recurso=json.dumps(
            {"cod_ibge": cod_ibge, "meta_id": str(meta_id)}, ensure_ascii=False, sort_keys=True
        ),
    )


def build_memoria(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> MemoriaResultado:
    """Memória rastreável: componentes, fórmulas, identidades e origem de cada número."""
    _, ap, versao = _obter(session, cod_ibge, periodo, as_of)
    m = ap.medidas
    return MemoriaResultado(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        valores=_valores(ap),
        ajustes=_recon_ajustes(ap),
        formula_primario="resultado_primario = receita_primaria − despesa_primaria",
        formula_nominal="resultado_nominal = resultado_primario − juros_liquidos",
        formula_nominal_abaixo="resultado_nominal abaixo = dcl_inicio − dcl_fim",
        identidade_primario_ok=_identidade_primario(m),
        identidade_nominal_dcl_ok=_identidade_nominal_dcl(m),
        fontes=ap.fontes,
        detalhes={
            "anexo": _ANEXO,
            "regra_despesa_primaria": "soma das colunas pagas (pagas + RP proc. pagos + pagos)",
            "juros_liquidos": "passivos − ativos; se não reportados, primário − nominal",
            "componentes_receita": len(ap.receita_componentes),
            "componentes_despesa": len(ap.despesa_componentes),
        },
        source_ref=_source_ref(periodo, versao),
    )
