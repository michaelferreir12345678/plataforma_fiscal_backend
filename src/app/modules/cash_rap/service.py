"""Regras de Caixa & Restos a Pagar (Módulo 9): suficiência por fonte, RPNP sem lastro, art. 42.

Fontes: ``silver.siconfi_rgf`` (Anexo 5 — Disponibilidade de Caixa e Restos a Pagar,
**quadrimestral** ``AAAA-Qn``) e ``silver.siconfi_rreo`` (Anexo 7 — Restos a Pagar,
**bimestral**; mapeamos Q1→B2, Q2→B4, Q3→B6). Todo número é lido do dado real do SICONFI,
materializado na gold e servido com ``source_ref`` + memória + ``as_of`` (auditável).

Regra invariante (§2.5): a suficiência é **fonte a fonte** — nunca consolidada. O RPNP
inscrito sem disponibilidade de caixa (``rpnp_sem_lastro``) é exposto para a Sprint 11
(mínimos) expurgar da base dos pisos de saúde/educação.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.cash_rap import caixa, repository
from app.modules.cash_rap.caixa import Disponibilidade, FonteNode
from app.modules.cash_rap.schemas import (
    Art42FonteItem,
    Art42Out,
    CaixaDetalhe,
    CaixaMemoria,
    ComparacaoCaixa,
    FonteSuficienciaItem,
    GrupoSubtotal,
    RapOrgaoItem,
    RpnpSemLastroItem,
    RpnpSemLastroOut,
    SerieCaixaItem,
    SuficienciaMatriz,
    SuficienciaResumo,
)
from app.modules.catalog import service as catalog_service
from app.modules.catalog.models import DimEnte
from app.modules.ingestion import repository as ingestion_repo
from app.shared.envelope import DrillEnvelope, Measures
from app.shared.hierarchy import HierarchyNode, build_drill_envelope
from app.shared.source_ref import SourceRef

_RELATORIO_RGF = "RGF"
_RELATORIO_RREO = "RREO"
_ANEXO_A5 = "Anexo 05"
_ANEXO_A7 = "Anexo 07"
_QUAD_RE = re.compile(r"^(\d{4})-Q([1-3])$")

_OBS_SUFICIENCIA = (
    "Análise fonte a fonte (LRF art. 8º, § único): a disponibilidade vinculada a uma fonte "
    "só cobre obrigações da própria vinculação — nunca se compensa o superávit de uma fonte "
    "com o déficit de outra. Subtotais por grupo são apenas informativos. "
    "Valores oficiais do RGF Anexo 5 — não recalculados."
)
_OBS_RPNP = (
    "RPNP inscrito sem disponibilidade de caixa (por fonte). Não conta na apuração dos "
    "mínimos de saúde/educação (LRF; consumido pela Sprint 11)."
)


def _source_a5(periodo: str, versao: str) -> SourceRef:
    return SourceRef(relatorio="RGF", anexo=_ANEXO_A5, periodo=periodo, versao_entrega=versao)


def _source_a7(periodo: str, versao: str) -> SourceRef:
    return SourceRef(relatorio="RREO", anexo=_ANEXO_A7, periodo=periodo, versao_entrega=versao)


def _ano_quad(periodo: str) -> tuple[int, int]:
    m = _QUAD_RE.match(periodo)
    if m is None:
        raise AppError(
            status=422, title="Período inválido",
            detail=f"Caixa/RP exige período RGF quadrimestral 'AAAA-Qn'; recebido '{periodo}'.",
        )
    return int(m.group(1)), int(m.group(2))


def rreo_periodo_de_rgf(periodo: str) -> str | None:
    """RREO bimestral correspondente ao quadrimestre RGF (Q1→B2, Q2→B4, Q3→B6)."""
    m = _QUAD_RE.match(periodo)
    return f"{m.group(1)}-B{2 * int(m.group(2))}" if m else None


def rgf_periodo_de_rreo(periodo_rreo: str) -> str | None:
    """Quadrimestre RGF correspondente ao bimestre RREO (só bimestres pares: B2→Q1…)."""
    m = re.match(r"^(\d{4})-B([1-6])$", periodo_rreo)
    if m is None:
        return None
    b = int(m.group(2))
    return f"{m.group(1)}-Q{b // 2}" if b % 2 == 0 else None


def _resolve_versao_rgf(
    session: Session, cod_ibge: str, periodo: str, as_of: datetime | None
) -> str:
    versao = ingestion_repo.resolve_versao(
        session, cod_ibge=cod_ibge, relatorio=_RELATORIO_RGF, periodo=periodo, as_of=as_of
    )
    if versao is None:
        raise AppError(
            status=404, title="RGF ausente",
            detail=f"Sem RGF (Anexo 5) vigente para {cod_ibge} em {periodo}.",
        )
    return versao


def _ente(session: Session, cod_ibge: str) -> DimEnte:
    ente = catalog_service.refresh_dim_ente(session, cod_ibge)
    if ente is None:
        raise AppError(
            status=404, title="Ente não encontrado",
            detail=f"Sem cadastro para o IBGE {cod_ibge} (ingerir siconfi_entes).",
        )
    return ente


# --- leitura + apuração (silver → objetos de domínio) ---
def _carregar(
    session: Session, cod_ibge: str, periodo: str, versao: str
) -> tuple[list[Disponibilidade], list[FonteNode]]:
    linhas = [
        caixa.LinhaAnexo5(conta=r.conta, coluna=r.coluna, valor=r.valor)
        for r in repository.read_rgf_anexo5(
            session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
        )
    ]
    disps = caixa.apurar_disponibilidades(linhas)
    if not disps:
        raise AppError(
            status=404, title="Anexo 5 ausente",
            detail=f"RGF sem Anexo 5 (Disponibilidade de Caixa) para {cod_ibge} em {periodo}.",
        )
    return disps, caixa.montar_hierarquia(disps)


def _disp_valores(d: Disponibilidade) -> dict[str, Decimal | None]:
    return {
        "disp_bruta": d.disp_bruta,
        "obrigacoes": d.obrigacoes,
        "disp_liquida_antes": d.disp_liquida_antes,
        "rpnp_exercicio": d.rpnp_exercicio,
        "disp_liquida_apos": d.disp_liquida_apos,
        "rpnp_sem_lastro": d.rpnp_sem_lastro,
    }


# --- materialização silver → gold (idempotente) ---
def materializar_disponibilidade(
    session: Session, cod_ibge: str, periodo: str, versao: str
) -> list[Disponibilidade]:
    """ETL do RGF Anexo 5 → dim_fonte_recurso + fato_disponibilidade."""
    disps, nodes = _carregar(session, cod_ibge, periodo, versao)
    for node in nodes:
        repository.upsert_fonte(
            session,
            {
                "codigo": node.codigo, "descricao": node.descricao,
                "parent_codigo": node.parent_codigo, "nivel": node.nivel,
                "path": node.path, "vinculada": node.vinculada,
            },
        )
    for d in disps:
        repository.upsert_disponibilidade(
            session,
            {
                "cod_ibge": cod_ibge, "periodo": periodo,
                "fonte_codigo": caixa.slug_fonte(d.fonte_descricao),
                "versao_entrega": versao, **_disp_valores(d),
            },
        )
    return disps


def materializar_rap(
    session: Session, cod_ibge: str, periodo_rreo: str, versao_rreo: str
) -> list[caixa.RapOrgao]:
    """ETL do RREO Anexo 7 → fato_rap (por poder/órgão)."""
    linhas = [
        caixa.LinhaAnexo7(conta=r.conta, cod_conta=r.cod_conta, valor=r.valor)
        for r in repository.read_rreo_anexo7(
            session, cod_ibge=cod_ibge, periodo=periodo_rreo, versao_entrega=versao_rreo
        )
    ]
    orgaos = caixa.apurar_rap(linhas)
    repository.replace_raps(
        session, cod_ibge=cod_ibge, periodo=periodo_rreo, versao_entrega=versao_rreo,
        rows=[
            {
                "cod_ibge": cod_ibge, "periodo": periodo_rreo, "orgao": r.orgao,
                "versao_entrega": versao_rreo,
                **{m: r.medidas.get(m) for m in caixa.MEDIDAS_RAP},
            }
            for r in orgaos
        ],
    )
    return orgaos


def _obter(
    session: Session, cod_ibge: str, periodo: str, as_of: datetime | None
) -> tuple[list[Disponibilidade], list[FonteNode], str]:
    versao = _resolve_versao_rgf(session, cod_ibge, periodo, as_of)
    disps = materializar_disponibilidade(session, cod_ibge, periodo, versao)
    return disps, caixa.montar_hierarquia(disps), versao


# --- derivações de apresentação ---
def _item_suficiencia(d: Disponibilidade) -> FonteSuficienciaItem:
    grupo, vinculada = caixa.classificar_fonte(d.fonte_descricao)
    return FonteSuficienciaItem(
        fonte_codigo=caixa.slug_fonte(d.fonte_descricao),
        descricao=d.fonte_descricao,
        vinculada=vinculada,
        grupo_codigo=grupo,
        grupo_descricao=caixa.grupo_descricao(grupo),
        disp_bruta=d.disp_bruta,
        obrigacoes=d.obrigacoes,
        disp_liquida_antes=d.disp_liquida_antes,
        rpnp_exercicio=d.rpnp_exercicio,
        disp_liquida_apos=d.disp_liquida_apos,
        rpnp_sem_lastro=d.rpnp_sem_lastro,
        status=d.status,
        semaforo=d.semaforo,
        suficiente=d.suficiente,
    )


def _resumo(disps: list[Disponibilidade]) -> SuficienciaResumo:
    zero = Decimal(0)
    total_sem_lastro = sum((d.rpnp_sem_lastro or zero for d in disps), zero)
    total_apos_pos = sum(
        ((d.disp_liquida_apos or zero) for d in disps if (d.disp_liquida_apos or zero) > 0), zero
    )
    return SuficienciaResumo(
        n_fontes=len(disps),
        n_suficientes=sum(1 for d in disps if d.suficiente),
        n_insuficientes=sum(1 for d in disps if not d.suficiente),
        n_deficit=sum(1 for d in disps if d.status == caixa.STATUS_DEFICIT),
        total_rpnp_sem_lastro=total_sem_lastro,
        total_disp_liquida_apos_positiva=total_apos_pos,
    )


def _measures_map(efetivas: dict[str, dict[str, Decimal]]) -> dict[str, Measures]:
    return {c: dict(m) for c, m in efetivas.items()}


def _efetivas(
    disps: list[Disponibilidade], nodes: list[FonteNode]
) -> dict[str, dict[str, Decimal]]:
    proprias = {
        caixa.slug_fonte(d.fonte_descricao): {
            k: v for k, v in _disp_valores(d).items() if v is not None
        }
        for d in disps
    }
    return caixa.agregar_medidas(nodes, proprias)


def _grupos(
    nodes: list[FonteNode], efetivas: dict[str, dict[str, Decimal]]
) -> list[GrupoSubtotal]:
    filhos: dict[str, int] = {}
    for n in nodes:
        if n.nivel == 3 and n.parent_codigo is not None:
            filhos[n.parent_codigo] = filhos.get(n.parent_codigo, 0) + 1
    grupos: list[GrupoSubtotal] = []
    for n in sorted((n for n in nodes if n.nivel == 2), key=lambda x: x.codigo):
        m = efetivas.get(n.codigo, {})
        grupos.append(
            GrupoSubtotal(
                grupo_codigo=n.codigo,
                descricao=n.descricao,
                vinculada=n.vinculada,
                disp_liquida_antes=m.get("disp_liquida_antes"),
                rpnp_exercicio=m.get("rpnp_exercicio"),
                disp_liquida_apos=m.get("disp_liquida_apos"),
                rpnp_sem_lastro=m.get("rpnp_sem_lastro"),
                n_fontes=filhos.get(n.codigo, 0),
            )
        )
    return grupos


def _hierarchy_nodes(nodes: list[FonteNode]) -> list[HierarchyNode]:
    return [
        HierarchyNode(
            codigo=n.codigo, descricao=n.descricao, parent_codigo=n.parent_codigo,
            nivel=n.nivel, path=n.path, measures={"vinculada": n.vinculada},
        )
        for n in nodes
    ]


# --- RAP (RREO Anexo 7) ---
def _rap_item(orgao: str, medidas: dict[str, Decimal]) -> RapOrgaoItem:
    return RapOrgaoItem(orgao=orgao, **{m: medidas.get(m) for m in caixa.MEDIDAS_RAP})


def _carregar_rap(
    session: Session, cod_ibge: str, periodo: str, as_of: datetime | None
) -> tuple[list[RapOrgaoItem], RapOrgaoItem | None, SourceRef | None]:
    periodo_rreo = rreo_periodo_de_rgf(periodo)
    if periodo_rreo is None:
        return [], None, None
    versao_rreo = ingestion_repo.resolve_versao(
        session, cod_ibge=cod_ibge, relatorio=_RELATORIO_RREO, periodo=periodo_rreo, as_of=as_of
    )
    if versao_rreo is None:
        return [], None, None
    orgaos = materializar_rap(session, cod_ibge, periodo_rreo, versao_rreo)
    if not orgaos:
        return [], None, _source_a7(periodo_rreo, versao_rreo)
    itens = [_rap_item(r.orgao, r.medidas) for r in orgaos]
    consolidado = _rap_item("CONSOLIDADO", caixa.consolidar_rap(orgaos))
    return itens, consolidado, _source_a7(periodo_rreo, versao_rreo)


# --- série e comparação temporal ---
def _serie(session: Session, cod_ibge: str) -> list[SerieCaixaItem]:
    serie: list[SerieCaixaItem] = []
    for periodo in repository.distinct_periodos_silver_a5(session, cod_ibge=cod_ibge):
        versao = ingestion_repo.resolve_versao(
            session, cod_ibge=cod_ibge, relatorio=_RELATORIO_RGF, periodo=periodo
        )
        if versao is None:
            continue
        try:
            disps, _ = _carregar(session, cod_ibge, periodo, versao)
        except AppError:
            continue
        zero = Decimal(0)
        serie.append(
            SerieCaixaItem(
                periodo=periodo,
                disp_liquida_apos_total=sum((d.disp_liquida_apos or zero for d in disps), zero),
                rpnp_sem_lastro_total=sum((d.rpnp_sem_lastro or zero for d in disps), zero),
            )
        )
    return serie


def _comparacao(
    serie: list[SerieCaixaItem], periodo: str, atual: Decimal | None
) -> ComparacaoCaixa | None:
    m = _QUAD_RE.match(periodo)
    if m is None:
        return None
    anterior_cod = f"{int(m.group(1)) - 1}-Q{m.group(2)}"
    anterior = next((s for s in serie if s.periodo == anterior_cod), None)
    if anterior is None:
        return None
    delta = (
        atual - anterior.rpnp_sem_lastro_total
        if atual is not None and anterior.rpnp_sem_lastro_total is not None
        else None
    )
    return ComparacaoCaixa(
        periodo_anterior=anterior_cod,
        rpnp_sem_lastro_anterior=anterior.rpnp_sem_lastro_total,
        delta_rs=delta,
    )


# --- endpoints ---
def build_detalhe(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> CaixaDetalhe:
    """Cabeçalho (suficiência + RP) + fontes críticas + série (Padrão de Detalhe)."""
    disps, _, versao = _obter(session, cod_ibge, periodo, as_of)
    ente = _ente(session, cod_ibge)
    ano, quad = _ano_quad(periodo)
    resumo = _resumo(disps)
    itens = [_item_suficiencia(d) for d in disps]
    criticas = [i for i in itens if not i.suficiente]
    rap_itens, rap_consolidado, source_rap = _carregar_rap(session, cod_ibge, periodo, as_of)
    serie = _serie(session, cod_ibge)
    zero = Decimal(0)
    return CaixaDetalhe(
        cod_ibge=cod_ibge,
        periodo=periodo,
        periodo_rreo=rreo_periodo_de_rgf(periodo),
        as_of=as_of.isoformat() if as_of else None,
        versao_entrega=versao,
        esfera=ente.esfera,
        resumo=resumo,
        disp_liquida_apos_total=sum((d.disp_liquida_apos or zero for d in disps), zero),
        fontes_criticas=sorted(criticas, key=lambda i: i.rpnp_sem_lastro or zero, reverse=True),
        rap_consolidado=rap_consolidado,
        rap_por_orgao=rap_itens,
        art42_aplicavel=caixa.fim_de_mandato(ano, ente.esfera),
        serie=serie,
        comparacao=_comparacao(serie, periodo, resumo.total_rpnp_sem_lastro),
        periodo_breadcrumb=catalog_service.periodo_breadcrumb(session, periodo),
        source_ref=_source_a5(periodo, versao),
        source_ref_rap=source_rap,
    )


def build_suficiencia(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> SuficienciaMatriz:
    """Matriz de suficiência **por fonte** com semáforo — nunca consolidada."""
    disps, nodes, versao = _obter(session, cod_ibge, periodo, as_of)
    ente = _ente(session, cod_ibge)
    efetivas = _efetivas(disps, nodes)
    return SuficienciaMatriz(
        cod_ibge=cod_ibge,
        periodo=periodo,
        as_of=as_of.isoformat() if as_of else None,
        versao_entrega=versao,
        esfera=ente.esfera,
        itens=sorted(
            (_item_suficiencia(d) for d in disps),
            key=lambda i: (i.grupo_codigo, i.descricao),
        ),
        grupos=_grupos(nodes, efetivas),
        resumo=_resumo(disps),
        observacao=_OBS_SUFICIENCIA,
        source_ref=_source_a5(periodo, versao),
    )


def build_arvore(
    session: Session,
    cod_ibge: str,
    periodo: str,
    node: str | None,
    *,
    as_of: datetime | None = None,
) -> DrillEnvelope:
    """Drill DOWN/UP por fonte de recurso (§6.1)."""
    disps, nodes, versao = _obter(session, cod_ibge, periodo, as_of)
    if node is not None and not any(n.codigo == node for n in nodes):
        raise AppError(
            status=404, title="Nó inexistente",
            detail=f"Fonte '{node}' sem dado para {cod_ibge} em {periodo}.",
        )
    efetivas = _efetivas(disps, nodes)
    return build_drill_envelope(
        _hierarchy_nodes(nodes), node, period=periodo,
        source_ref=_source_a5(periodo, versao), node_measures=_measures_map(efetivas),
    )


def build_rpnp_sem_lastro(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> RpnpSemLastroOut:
    """RPNP inscrito sem disponibilidade de caixa, **por fonte** (base do expurgo dos mínimos)."""
    disps, _, versao = _obter(session, cod_ibge, periodo, as_of)
    zero = Decimal(0)
    itens: list[RpnpSemLastroItem] = []
    total_vinc = total_nvinc = zero
    for d in sorted(disps, key=lambda x: x.rpnp_sem_lastro or zero, reverse=True):
        sem_lastro = d.rpnp_sem_lastro or zero
        if sem_lastro <= 0:
            continue
        _, vinculada = caixa.classificar_fonte(d.fonte_descricao)
        itens.append(
            RpnpSemLastroItem(
                fonte_codigo=caixa.slug_fonte(d.fonte_descricao),
                descricao=d.fonte_descricao,
                vinculada=vinculada,
                rpnp_exercicio=d.rpnp_exercicio,
                disp_liquida_antes=d.disp_liquida_antes,
                rpnp_sem_lastro=sem_lastro,
            )
        )
        if vinculada:
            total_vinc += sem_lastro
        else:
            total_nvinc += sem_lastro
    return RpnpSemLastroOut(
        cod_ibge=cod_ibge,
        periodo=periodo,
        as_of=as_of.isoformat() if as_of else None,
        versao_entrega=versao,
        itens=itens,
        total_rpnp_sem_lastro=total_vinc + total_nvinc,
        total_vinculada=total_vinc,
        total_nao_vinculada=total_nvinc,
        observacao=_OBS_RPNP,
        source_ref=_source_a5(periodo, versao),
    )


def build_art42(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> Art42Out:
    """Painel do art. 42 LRF — só é aplicável em ano de fim de mandato."""
    ente = _ente(session, cod_ibge)
    ano, quad = _ano_quad(periodo)
    aplicavel = caixa.fim_de_mandato(ano, ente.esfera)
    if not aplicavel:
        return Art42Out(
            cod_ibge=cod_ibge, periodo=periodo,
            as_of=as_of.isoformat() if as_of else None,
            esfera=ente.esfera, ano=ano, quadrimestre=quad,
            aplicavel=False, janela_vedacao=caixa.janela_art42(quad),
            observacao=(
                f"O art. 42 LRF só se aplica no último ano de mandato "
                f"({'estadual' if ente.esfera == 'estadual' else 'municipal'}); "
                f"{ano} não é fim de mandato."
            ),
        )

    disps, _, versao = _obter(session, cod_ibge, periodo, as_of)
    zero = Decimal(0)
    fontes: list[Art42FonteItem] = []
    total_lacuna = zero
    descumpre = 0
    for d in sorted(disps, key=lambda x: x.fonte_descricao):
        apos = d.disp_liquida_apos
        cumpre = apos is None or apos >= 0
        lacuna = -apos if (apos is not None and apos < 0) else zero
        obrig_fim = None
        if d.obrigacoes is not None and d.rpnp_exercicio is not None:
            obrig_fim = d.obrigacoes + d.rpnp_exercicio
        _, vinculada = caixa.classificar_fonte(d.fonte_descricao)
        fontes.append(
            Art42FonteItem(
                fonte_codigo=caixa.slug_fonte(d.fonte_descricao),
                descricao=d.fonte_descricao,
                vinculada=vinculada,
                disp_bruta=d.disp_bruta,
                obrigacoes_ate_fim=obrig_fim,
                lastro=apos,
                cumpre=cumpre,
                lacuna=lacuna,
            )
        )
        total_lacuna += lacuna
        if not cumpre:
            descumpre += 1
    return Art42Out(
        cod_ibge=cod_ibge,
        periodo=periodo,
        as_of=as_of.isoformat() if as_of else None,
        versao_entrega=versao,
        esfera=ente.esfera,
        ano=ano,
        quadrimestre=quad,
        aplicavel=True,
        janela_vedacao=caixa.janela_art42(quad),
        atende=descumpre == 0,
        n_descumprimentos=descumpre,
        total_lacuna=total_lacuna,
        fontes=fontes,
        observacao=(
            "Nos 2 últimos quadrimestres do mandato é vedado contrair obrigação sem "
            "disponibilidade de caixa por fonte (art. 42 LRF; descumprir pode configurar "
            "crime — art. 359-C CP). Lacuna = obrigações a lastrear − disponibilidade, por fonte."
        ),
        source_ref=_source_a5(periodo, versao),
    )


def build_memoria(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> CaixaMemoria:
    """Memória rastreável: fórmulas, identidades e origem de cada número (auditável)."""
    disps, _, versao = _obter(session, cod_ibge, periodo, as_of)
    resumo = _resumo(disps)
    return CaixaMemoria(
        cod_ibge=cod_ibge,
        periodo=periodo,
        as_of=as_of.isoformat() if as_of else None,
        versao_entrega=versao,
        fontes=[_item_suficiencia(d) for d in disps],
        total_rpnp_sem_lastro=resumo.total_rpnp_sem_lastro,
        formula_liquida_antes="disp_liquida_antes (f) = disp_bruta (a) − obrigacoes (b+c+d+e)",
        formula_liquida_apos="disp_liquida_apos (h) = disp_liquida_antes (f) − rpnp_exercicio (g)",
        formula_rpnp_sem_lastro=(
            "rpnp_sem_lastro = max(0, rpnp_exercicio − max(0, disp_liquida_antes))"
        ),
        regra_suficiencia=(
            "suficiente ⇔ disp_liquida_apos ≥ 0 · insuficiente_rpnp ⇔ antes ≥ 0 e após < 0 · "
            "deficit ⇔ antes < 0 (nunca compensada entre fontes)"
        ),
        detalhes={
            "anexo": _ANEXO_A5,
            "n_fontes": resumo.n_fontes,
            "colunas_letra_variam": "layouts do STN usam (a)…(i); mapeamos pelo prefixo descritivo",
            "poderes_somados": "Anexo 5 vem por poder (E/L); somado por fonte na visão do ente",
        },
        source_ref=_source_a5(periodo, versao),
    )


# --- API pública consumida pela Sprint 11 (mínimos) ---
def disponibilidades_por_fonte(
    session: Session,
    cod_ibge: str,
    periodo_rgf: str,
    *,
    as_of: datetime | None = None,
    soft: bool = True,
) -> list[Disponibilidade]:
    """Disponibilidades por fonte (com ``rpnp_sem_lastro``). ``soft`` ⇒ [] se faltar RGF.

    A Sprint 11 (mínimos) mapeia o bimestre RREO → quadrimestre RGF
    (:func:`rgf_periodo_de_rreo`) e expurga o RPNP sem lastro das fontes de saúde/educação.
    """
    versao = ingestion_repo.resolve_versao(
        session, cod_ibge=cod_ibge, relatorio=_RELATORIO_RGF, periodo=periodo_rgf, as_of=as_of
    )
    if versao is None:
        if soft:
            return []
        raise AppError(
            status=404, title="RGF ausente",
            detail=f"Sem RGF (Anexo 5) vigente para {cod_ibge} em {periodo_rgf}.",
        )
    try:
        return materializar_disponibilidade(session, cod_ibge, periodo_rgf, versao)
    except AppError:
        if soft:
            return []
        raise
