"""Regras do Módulo 7 — dívida líquida, CAPAG bruta e serviço futuro."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.catalog import repository as catalog_repo
from app.modules.catalog import service as catalog_service
from app.modules.catalog.models import DimEnte
from app.modules.debt import repository
from app.modules.debt.models import FatoCapag, FatoDivida
from app.modules.debt.schemas import (
    CapagHero,
    CapagMemoria,
    CapagResponse,
    CdpSituacao,
    ComparacaoDivida,
    ComponenteMemoria,
    CronogramaResponse,
    DclHero,
    DividaArvoreOut,
    DividaDetalhe,
    MemoriaDivida,
    OperacaoDetalhe,
    PosicaoSimulada,
    PvlItem,
    PvlOut,
    SerieDividaItem,
    SimulacaoResponse,
    SimularOperacaoRequest,
    VencimentoItem,
)
from app.modules.indicators import divida as calculos
from app.modules.indicators import serie_ajuste
from app.modules.indicators.limites import LimiteLegal, classificar_faixa
from app.modules.indicators.schemas import SerieAjuste
from app.modules.ingestion import repository as ingestion_repo
from app.modules.ingestion.models import DimEntrega, SadipemOpContratada, SilverRgf
from app.shared.ausencia import ausencia_de_entrega, rotulo_humano
from app.shared.envelope import DrillEnvelope, Measures
from app.shared.hierarchy import HierarchyNode, build_drill_envelope
from app.shared.source_ref import SourceRef

_REL_RGF = "RGF"
_REL_CAPAG = "CAPAG"
_REL_CAPAG_ESTADUAL = "CAPAG-EST"
_REL_SADIPEM_PVL = "SADIPEM-PVL"
_REL_SADIPEM_CDP = "SADIPEM-CDP"
_REL_SADIPEM_OP = "SADIPEM-OP"
_REL_SADIPEM_CRONO = "SADIPEM-CRONOGRAMA"
_ANEXO_DDCL = "Anexo 02 — DDCL"
_ANO_RE = re.compile(r"^(\d{4})")
_ZERO = Decimal(0)


def _ano(periodo: str) -> int:
    match = _ANO_RE.match(periodo)
    if match is None:
        raise AppError(
            status=422,
            title="Período inválido",
            detail=f"Período RGF inválido: {periodo}.",
        )
    return int(match.group(1))


def _source_rgf(periodo: str, versao: str, anexo: str = _ANEXO_DDCL) -> SourceRef:
    return SourceRef(
        relatorio=_REL_RGF,
        anexo=anexo,
        periodo=periodo,
        versao_entrega=versao,
    )


def _source_capag(ano_ref: int, versao: str, cod_ibge: str) -> SourceRef:
    """Fonte real da nota. O anexo muda com o escopo: "Prévia da CAPAG" é a aba da planilha
    dos municípios; a dos estados é um CSV com a classificação por UF. Repetir o nome da aba
    municipal num ente estadual apontaria a auditoria para um documento que não tem a linha."""
    municipal = len(cod_ibge) == 7
    return SourceRef(
        relatorio=_REL_CAPAG if municipal else _REL_CAPAG_ESTADUAL,
        anexo="Prévia da CAPAG" if municipal else "Classificação da CAPAG (estados)",
        periodo=str(ano_ref),
        versao_entrega=versao,
    )


def _source_sadipem(relatorio: str, periodo: str, versao: str) -> SourceRef:
    anexo = {
        _REL_SADIPEM_OP: "Operações contratadas",
        _REL_SADIPEM_PVL: "Pedidos de verificação de limites",
        _REL_SADIPEM_CDP: "Cadastro da Dívida Pública (base nacional)",
    }.get(relatorio, "Cronograma de pagamentos")
    return SourceRef(
        relatorio=relatorio,
        anexo=anexo,
        periodo=periodo,
        versao_entrega=versao,
    )


def _resolve_rgf(session: Session, cod_ibge: str, periodo: str, as_of: datetime | None) -> str:
    versao = ingestion_repo.resolve_versao(
        session,
        cod_ibge=cod_ibge,
        relatorio=_REL_RGF,
        periodo=periodo,
        as_of=as_of,
    )
    if versao is None:
        raise ausencia_de_entrega(
            session,
            cod_ibge=cod_ibge,
            relatorio=_REL_RGF,
            periodo=periodo,
            title="RGF DDCL ausente",
            detail=f"Sem RGF vigente para {cod_ibge} em {periodo}.",
        )
    return versao


def _resolve_global(
    session: Session,
    *,
    relatorio: str,
    periodo: str,
    as_of: datetime | None,
) -> str:
    """Resolve fonte nacional: a entrega é ``BR``, não o IBGE da linha silver."""
    versao = ingestion_repo.resolve_versao(
        session,
        cod_ibge="BR",
        relatorio=relatorio,
        periodo=periodo,
        as_of=as_of,
    )
    if versao is None:
        raise AppError(
            status=404,
            title=f"{relatorio} ausente",
            detail=f"Sem entrega nacional {relatorio} para {periodo}.",
        )
    return versao


def _resolve_latest_ente(
    session: Session,
    *,
    cod_ibge: str,
    relatorio: str,
    as_of: datetime | None,
) -> tuple[str, str]:
    entrega = ingestion_repo.resolve_latest_entrega(
        session,
        cod_ibge=cod_ibge,
        relatorio=relatorio,
        as_of=as_of,
    )
    if entrega is None:
        raise AppError(
            status=404,
            title=f"{relatorio} ausente",
            detail=f"Sem fotografia {relatorio} para {cod_ibge}.",
        )
    return entrega.periodo, entrega.versao_entrega


def _effective_as_of(
    session: Session,
    *,
    cod_ibge: str,
    relatorio: str,
    periodo: str,
    versao: str,
    requested: datetime | None,
) -> datetime:
    if requested is not None:
        return requested
    homologada = ingestion_repo.entrega_homologada_em(
        session,
        cod_ibge=cod_ibge,
        relatorio=relatorio,
        periodo=periodo,
        versao_entrega=versao,
    )
    if homologada is None:  # defensivo: resolve_versao só retorna entrega existente
        raise AppError(
            status=500,
            title="Entrega sem data de auditoria",
            detail=f"{relatorio}/{cod_ibge}/{periodo}/{versao} sem homologada_em.",
        )
    return homologada


def _ente(session: Session, cod_ibge: str) -> DimEnte:
    ente = catalog_service.refresh_dim_ente(session, cod_ibge)
    if ente is None or ente.esfera is None:
        raise AppError(
            status=422,
            title="Esfera desconhecida",
            detail=f"Sem dim_ente/esfera para {cod_ibge}.",
        )
    return ente


def _limite(session: Session, *, indicador: str, esfera: str) -> LimiteLegal:
    row = catalog_repo.get_limite(session, indicador=indicador, esfera=esfera, poder="")
    if row is None:
        raise AppError(
            status=404,
            title="Limite legal ausente",
            detail=f"Sem dim_limite_legal para {indicador}/{esfera}.",
        )
    return LimiteLegal(
        indicador=row.indicador,
        esfera=row.esfera,
        poder=row.poder,
        sentido=row.sentido,
        teto_pct=row.teto_pct,
        alerta_pct=row.alerta_pct,
        prudencial_pct=row.prudencial_pct,
    )


def _linhas_ddcl(
    session: Session, cod_ibge: str, periodo: str, versao: str
) -> list[calculos.LinhaDdcl]:
    rows = repository.read_rgf_anexo(
        session,
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        numero="02",
    )
    if not rows:
        raise AppError(
            status=422,
            title="DDCL ausente",
            detail=f"RGF {periodo}/{versao} não contém o Anexo 02 (DDCL).",
        )
    return [
        calculos.LinhaDdcl(
            conta=row.conta or "",
            coluna=row.coluna,
            valor=Decimal(row.valor or 0),
        )
        for row in rows
    ]


def materializar_divida(session: Session, cod_ibge: str, periodo: str, versao: str) -> FatoDivida:
    """Materializa ``silver.siconfi_rgf`` Anexo 02 em ``gold.fato_divida``."""
    try:
        apuracao = calculos.calcular_dcl(_linhas_ddcl(session, cod_ibge, periodo, versao), periodo)
    except ValueError as exc:
        raise AppError(
            status=422,
            title="DDCL incompleto",
            detail=str(exc),
        ) from exc
    repository.upsert_fato_divida(
        session,
        {
            "cod_ibge": cod_ibge,
            "periodo": periodo,
            "dc_bruta": apuracao.dc_bruta,
            "disponibilidades": apuracao.disponibilidades,
            "haveres": apuracao.haveres,
            "dcl": apuracao.dcl,
            "dcl_reportada": apuracao.dcl_reportada,
            "diferenca_reconciliacao": apuracao.diferenca_reconciliacao,
            "rcl_ajustada": apuracao.rcl_ajustada,
            "pct_rcl": apuracao.pct_rcl,
            "saldo_interno": apuracao.saldo_interno,
            "saldo_externo": apuracao.saldo_externo,
            "versao_entrega": versao,
        },
    )
    fato = repository.get_fato_divida(
        session,
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
    )
    assert fato is not None
    return fato


def _ensure_divida(session: Session, cod_ibge: str, periodo: str, versao: str) -> FatoDivida:
    fato = repository.get_fato_divida(
        session,
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
    )
    return fato or materializar_divida(session, cod_ibge, periodo, versao)


def obter_dcl(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> FatoDivida | None:
    """DCL (Sprint 8) do ente/período RGF, para reconciliação cruzada (Sprint 9).

    Retorna ``None`` quando não há RGF/DDCL vigente — a reconciliação segue com o dado
    do próprio Anexo 6, apenas sem o cruzamento com a Dívida.
    """
    try:
        versao = _resolve_rgf(session, cod_ibge, periodo, as_of)
        return _ensure_divida(session, cod_ibge, periodo, versao)
    except AppError:
        return None


def _capag_ausente(session: Session, cod_ibge: str, ano_ref: int) -> AppError:
    """Explica **por que** não há CAPAG para o exercício, em vez de só dizer que não há.

    A CAPAG não é apurada continuamente: o Tesouro publica uma vez por exercício, a partir
    das contas do ano anterior já encerradas e homologadas. Consultar um quadrimestre de um
    ano em curso e receber "sem dado" faz o gestor procurar defeito na plataforma — quando
    a resposta é que a nota daquele exercício ainda não saiu.
    """
    escopo = "municípios" if len(cod_ibge) == 7 else "estados"
    disponiveis = sorted(
        session.scalars(
            select(FatoCapag.ano_ref).where(FatoCapag.cod_ibge == cod_ibge).distinct()
        )
    )
    extras: dict[str, Any] = {}
    # Anteriores ao pedido: o exercício pedido é, por definição, o que não resolveu — e um
    # posterior seria ainda mais recente que o que a tela já não conseguiu abrir.
    anteriores = [ano for ano in disponiveis if ano < ano_ref]
    if anteriores:
        ultimo = anteriores[-1]
        onde = (
            f"A mais recente disponível é a de {ultimo} "
            f"(apurada sobre o exercício de {ultimo - 1})."
        )
        extras["ano_disponivel"] = ultimo
        # O período **navegável** da tela, não só o ano: a página de dívida trabalha em
        # quadrimestre do RGF, e mandar o gestor "para 2025" não é um lugar onde ele possa
        # clicar. Aqui devolvemos o último RGF vigente daquele exercício.
        periodo_sugerido = _ultimo_rgf_do_ano(session, cod_ibge, ultimo)
        if periodo_sugerido is not None:
            # Sem RGF naquele exercício não há para onde navegar; emitir o rótulo mesmo
            # assim daria à tela um botão que promete um destino que não existe.
            extras["periodo_sugerido"] = periodo_sugerido
            extras["rotulo_sugerido"] = f"Ir para {rotulo_humano(periodo_sugerido)}"
    elif disponiveis:
        onde = (
            f"As publicações carregadas para este ente vão de {disponiveis[0]} a "
            f"{disponiveis[-1]}, e nenhuma antecede {ano_ref}."
        )
    else:
        onde = "Não há nenhuma publicação da CAPAG carregada para este ente."
    return AppError(
        status=404,
        title=f"CAPAG de {ano_ref} ainda não publicada",
        detail=(
            f"A CAPAG é publicada uma vez por exercício pelo Tesouro Nacional, a partir "
            f"das contas do ano anterior já encerradas — não acompanha o quadrimestre. "
            f"A nota de {ano_ref} para {escopo} ainda não foi divulgada. {onde} "
            f"Enquanto a nova não sai, a nota vigente continua sendo a última publicada."
        ),
        type_="urn:plataforma-fiscal:error:capag-nao-publicada",
        extras=extras,
    )


def _ultimo_rgf_do_ano(session: Session, cod_ibge: str, ano: int) -> str | None:
    """Último período RGF vigente do exercício — o destino do "ir para o último período"."""
    return session.scalar(
        select(DimEntrega.periodo)
        .where(
            DimEntrega.cod_ibge == cod_ibge,
            DimEntrega.relatorio == _REL_RGF,
            DimEntrega.periodo.like(f"{ano}-%"),
            DimEntrega.vigente.is_(True),
        )
        .order_by(DimEntrega.periodo.desc())
        .limit(1)
    )


def _relatorio_capag(cod_ibge: str) -> str:
    """Município e estado têm publicações CAPAG distintas — e entregas distintas.

    O Tesouro publica dois conjuntos separados; na ``dim_entrega`` eles chegam como duas
    entregas nacionais, e usar o mesmo rótulo faria uma superar a outra. Resolver a versão
    pelo rótulo errado devolve o checksum do arquivo dos municípios para um ente estadual,
    e a CAPAG do Governo do Estado some da tela sem erro nenhum.
    """
    return _REL_CAPAG if len(cod_ibge) == 7 else _REL_CAPAG_ESTADUAL


def _ensure_capag(
    session: Session,
    cod_ibge: str,
    ano_ref: int,
    *,
    as_of: datetime | None,
) -> FatoCapag:
    periodo = str(ano_ref)
    try:
        versao = _resolve_global(
            session,
            relatorio=_relatorio_capag(cod_ibge),
            periodo=periodo,
            as_of=as_of,
        )
    except AppError as exc:
        if exc.status != 404:
            raise
        # "Sem entrega nacional CAPAG-EST para 2026" não diz nada ao gestor; a mensagem
        # do domínio diz por que não há e qual é a nota que continua valendo.
        raise _capag_ausente(session, cod_ibge, ano_ref) from exc
    fato = repository.get_fato_capag(
        session,
        cod_ibge=cod_ibge,
        ano_ref=ano_ref,
        versao_entrega=versao,
    )
    if fato is not None:
        return fato
    silver = repository.read_capag(
        session,
        cod_ibge=cod_ibge,
        ano_ref=ano_ref,
        versao_entrega=versao,
    )
    if silver is None:
        # A publicação daquele exercício existe, mas não traz este ente — acontece com
        # município que não entregou as contas a tempo. Também merece explicação, e não
        # a chave técnica da entrega.
        raise AppError(
            status=404,
            title=f"Ente sem CAPAG em {ano_ref}",
            detail=(
                f"A publicação da CAPAG de {ano_ref} não inclui este ente. Isso ocorre "
                "quando as contas do exercício anterior não foram entregues ou "
                "homologadas a tempo da apuração do Tesouro."
            ),
            type_="urn:plataforma-fiscal:error:capag-ente-ausente",
        )
    repository.upsert_fato_capag(
        session,
        {
            "cod_ibge": cod_ibge,
            "ano_ref": ano_ref,
            "nota_final": silver.nota_final,
            "ind_endividamento": silver.ind_endividamento,
            "ind_poupanca": silver.ind_poupanca,
            "ind_liquidez": silver.ind_liquidez,
            "metodologia_versao": silver.metodologia_versao,
            "versao_entrega": versao,
        },
    )
    fato = repository.get_fato_capag(
        session,
        cod_ibge=cod_ibge,
        ano_ref=ano_ref,
        versao_entrega=versao,
    )
    assert fato is not None
    return fato


def _capag_hero(fato: FatoCapag, *, as_of: datetime | None) -> CapagHero:
    return CapagHero(
        ano_ref=fato.ano_ref,
        nota_final=fato.nota_final,
        ind_endividamento=fato.ind_endividamento,
        endividamento_pct=calculos.endividamento_capag_pct(fato.ind_endividamento),
        ind_poupanca=fato.ind_poupanca,
        ind_liquidez=fato.ind_liquidez,
        metodologia_versao=fato.metodologia_versao,
        as_of=as_of,
        source_ref=_source_capag(fato.ano_ref, fato.versao_entrega, fato.cod_ibge),
    )


def _dcl_hero(
    session: Session,
    fato: FatoDivida,
    *,
    esfera: str,
    as_of: datetime | None,
) -> DclHero:
    limite = _limite(
        session,
        indicador="divida_consolidada_liquida",
        esfera=esfera,
    )
    faixa = classificar_faixa(fato.pct_rcl, limite) if fato.pct_rcl is not None else None
    return DclHero(
        dc_bruta=fato.dc_bruta,
        disponibilidades=fato.disponibilidades,
        haveres=fato.haveres,
        dcl=fato.dcl,
        rcl_ajustada=fato.rcl_ajustada,
        pct_rcl=fato.pct_rcl,
        limite_pct=limite.teto_pct,
        faixa=faixa,
        as_of=as_of,
        source_ref=_source_rgf(fato.periodo, fato.versao_entrega),
    )


def _seed_origens(session: Session) -> list[HierarchyNode]:
    rows = (
        ("DIVIDA", "Dívida consolidada bruta", None, 1),
        ("DIVIDA.INTERNA", "Dívida interna", "DIVIDA", 2),
        ("DIVIDA.EXTERNA", "Dívida externa", "DIVIDA", 2),
        ("DIVIDA.OUTRAS", "Outros componentes da DC", "DIVIDA", 2),
    )
    for codigo, descricao, parent, nivel in rows:
        repository.upsert_origem(
            session,
            {
                "codigo": codigo,
                "descricao": descricao,
                "parent_codigo": parent,
                "nivel": nivel,
                "path": codigo,
            },
        )
    return [
        HierarchyNode(
            codigo=row.codigo,
            descricao=row.descricao,
            parent_codigo=row.parent_codigo,
            nivel=row.nivel,
            path=row.path,
        )
        for row in repository.list_origens(session)
    ]


def _origem_measures(fato: FatoDivida) -> dict[str, Measures]:
    interna = fato.saldo_interno or _ZERO
    externa = fato.saldo_externo or _ZERO
    outras = fato.dc_bruta - interna - externa
    return {
        "DIVIDA": {
            "saldo_divida": fato.dc_bruta,
            "pct_rcl": (
                calculos.percentual_sobre_rcl(fato.dc_bruta, fato.rcl_ajustada)
                if fato.rcl_ajustada
                else None
            ),
        },
        "DIVIDA.INTERNA": {"saldo_divida": interna},
        "DIVIDA.EXTERNA": {"saldo_divida": externa},
        "DIVIDA.OUTRAS": {"saldo_divida": outras},
    }


def _origem_envelope(session: Session, fato: FatoDivida, node: str | None) -> DrillEnvelope:
    nodes = _seed_origens(session)
    if node is not None and not any(item.codigo == node for item in nodes):
        raise AppError(
            status=404,
            title="Nó de origem inexistente",
            detail=f"Nó '{node}' não existe no drill de origem.",
        )
    return build_drill_envelope(
        nodes,
        node,
        period=fato.periodo,
        source_ref=_source_rgf(fato.periodo, fato.versao_entrega),
        node_measures=_origem_measures(fato),
    )


def _origem_operacao(tipo_operacao: str | None) -> Literal["INTERNA", "EXTERNA", "NAO_INFORMADA"]:
    texto = calculos.normalizar_texto(tipo_operacao)
    if "EXTERNA" in texto:
        return "EXTERNA"
    if "INTERNA" in texto:
        return "INTERNA"
    return "NAO_INFORMADA"


def _credor_codigo(origem: str, descricao: str) -> str:
    digest = hashlib.sha1(descricao.casefold().encode("utf-8"), usedforsecurity=False).hexdigest()[
        :12
    ]
    return f"CREDORES.{origem}.{digest}"


def _credor_tree(
    session: Session,
    operacoes: list[SadipemOpContratada],
) -> tuple[list[HierarchyNode], dict[str, Measures]]:
    raiz = {
        "codigo": "CREDORES",
        "descricao": "Operações contratadas",
        "parent_codigo": None,
        "nivel": 1,
        "path": "CREDORES",
        "origem": None,
    }
    repository.upsert_credor(session, raiz)
    codigos_ativos = {"CREDORES"}
    for origem, descricao in (
        ("INTERNA", "Operações internas"),
        ("EXTERNA", "Operações externas"),
        ("NAO_INFORMADA", "Origem não informada"),
    ):
        codigo_origem = f"CREDORES.{origem}"
        codigos_ativos.add(codigo_origem)
        repository.upsert_credor(
            session,
            {
                "codigo": codigo_origem,
                "descricao": descricao,
                "parent_codigo": "CREDORES",
                "nivel": 2,
                "path": f"CREDORES.{origem}",
                "origem": origem,
            },
        )

    medidas: dict[str, Measures] = defaultdict(dict)
    medidas["CREDORES"] = {"valor_contratado": _ZERO, "operacoes": 0}
    for operacao in operacoes:
        origem = _origem_operacao(operacao.tipo_operacao)
        pai = f"CREDORES.{origem}"
        descricao = (operacao.credor or "Credor não informado").strip()
        codigo = _credor_codigo(origem, descricao)
        codigos_ativos.add(codigo)
        repository.upsert_credor(
            session,
            {
                "codigo": codigo,
                "descricao": descricao,
                "parent_codigo": pai,
                "nivel": 3,
                "path": codigo,
                "origem": origem,
            },
        )
        valor = Decimal(operacao.valor_contratado or 0)
        atual = medidas.get(codigo, {"valor_contratado": _ZERO, "operacoes": 0})
        atual["valor_contratado"] = Decimal(atual["valor_contratado"]) + valor
        atual["operacoes"] = int(atual["operacoes"]) + 1
        medidas[codigo] = atual
        for agregado in (pai, "CREDORES"):
            base = medidas.get(agregado, {"valor_contratado": _ZERO, "operacoes": 0})
            base["valor_contratado"] = Decimal(base["valor_contratado"]) + valor
            base["operacoes"] = int(base["operacoes"]) + 1
            medidas[agregado] = base

    # ``dim_credor`` é global; o envelope desta consulta deve conter somente os
    # nós ativos do ente/versão corrente, não credores de outras entidades.
    dim = repository.list_credores(session, codigos_ativos)
    nodes = [
        HierarchyNode(
            codigo=row.codigo,
            descricao=row.descricao,
            parent_codigo=row.parent_codigo,
            nivel=row.nivel,
            path=row.path,
        )
        for row in dim
    ]
    return nodes, dict(medidas)


def _serie(
    session: Session,
    cod_ibge: str,
    base_periodo: str,
    *,
    as_of: datetime | None,
) -> tuple[list[SerieDividaItem], SerieAjuste]:
    """Série RGF multi-exercício + comparabilidade (DCL a preços do período e per capita)."""
    serie: list[SerieDividaItem] = []
    for periodo in repository.distinct_periodos_ddcl(session, cod_ibge=cod_ibge):
        versao = ingestion_repo.resolve_versao(
            session,
            cod_ibge=cod_ibge,
            relatorio=_REL_RGF,
            periodo=periodo,
            as_of=as_of,
        )
        if versao is None:
            continue
        try:
            fato = _ensure_divida(session, cod_ibge, periodo, versao)
        except AppError:
            continue
        serie.append(
            SerieDividaItem(
                periodo=periodo,
                as_of=_effective_as_of(
                    session,
                    cod_ibge=cod_ibge,
                    relatorio=_REL_RGF,
                    periodo=periodo,
                    versao=versao,
                    requested=as_of,
                ),
                dcl=fato.dcl,
                pct_rcl=fato.pct_rcl,
                dc_bruta=fato.dc_bruta,
                source_ref=_source_rgf(periodo, versao),
            )
        )
    ajuste = serie_ajuste.calcular(
        session, cod_ibge, [s.periodo for s in serie], base_periodo
    )
    por_periodo = serie_ajuste.indexar(ajuste)
    for item in serie:
        a = por_periodo.get(item.periodo)
        item.dcl_real = serie_ajuste.real(item.dcl, a)
        item.dcl_per_capita = serie_ajuste.per_capita(item.dcl, a)
        item.populacao = a.populacao if a else None
    return serie, ajuste


def _comparacao(
    serie: list[SerieDividaItem], periodo: str, atual: Decimal
) -> ComparacaoDivida | None:
    anteriores = [item for item in serie if item.periodo < periodo]
    if not anteriores:
        return None
    anterior = anteriores[-1]
    variacao = atual - anterior.dcl
    variacao_pct = variacao / anterior.dcl * Decimal(100) if anterior.dcl else None
    return ComparacaoDivida(
        periodo_anterior=anterior.periodo,
        dcl_anterior=anterior.dcl,
        variacao_rs=variacao,
        variacao_pct=variacao_pct,
    )


def build_detalhe(
    session: Session,
    cod_ibge: str,
    periodo: str,
    *,
    as_of: datetime | None = None,
) -> DividaDetalhe:
    versao = _resolve_rgf(session, cod_ibge, periodo, as_of)
    rgf_as_of = _effective_as_of(
        session,
        cod_ibge=cod_ibge,
        relatorio=_REL_RGF,
        periodo=periodo,
        versao=versao,
        requested=as_of,
    )
    ente = _ente(session, cod_ibge)
    fato = _ensure_divida(session, cod_ibge, periodo, versao)
    capag = _ensure_capag(session, cod_ibge, _ano(periodo), as_of=as_of)
    capag_as_of = _effective_as_of(
        session,
        cod_ibge="BR",
        relatorio=_relatorio_capag(cod_ibge),
        periodo=str(capag.ano_ref),
        versao=capag.versao_entrega,
        requested=as_of,
    )
    envelope = _origem_envelope(session, fato, "DIVIDA")
    serie, ajuste = _serie(session, cod_ibge, periodo, as_of=as_of)
    assert ente.esfera is not None
    return DividaDetalhe(
        cod_ibge=cod_ibge,
        periodo=periodo,
        as_of=rgf_as_of,
        versao_entrega=versao,
        esfera=ente.esfera,
        dcl=_dcl_hero(session, fato, esfera=ente.esfera, as_of=rgf_as_of),
        capag=_capag_hero(capag, as_of=capag_as_of),
        composicao=envelope.children,
        serie=serie,
        serie_ajuste=ajuste,
        comparacao=_comparacao(serie, periodo, fato.dcl),
        periodo_breadcrumb=catalog_service.periodo_breadcrumb(session, periodo),
        source_ref=_source_rgf(periodo, versao),
    )


def build_memoria(
    session: Session,
    cod_ibge: str,
    periodo: str,
    *,
    as_of: datetime | None = None,
) -> MemoriaDivida:
    versao = _resolve_rgf(session, cod_ibge, periodo, as_of)
    effective_as_of = _effective_as_of(
        session,
        cod_ibge=cod_ibge,
        relatorio=_REL_RGF,
        periodo=periodo,
        versao=versao,
        requested=as_of,
    )
    try:
        apuracao = calculos.calcular_dcl(_linhas_ddcl(session, cod_ibge, periodo, versao), periodo)
    except ValueError as exc:
        raise AppError(status=422, title="DDCL incompleto", detail=str(exc)) from exc
    rastros = {item.papel: item for item in apuracao.componentes}

    def componente(nome: str, operador: Literal["+", "-"], valor: Decimal) -> ComponenteMemoria:
        rastro = rastros.get(nome)
        return ComponenteMemoria(
            componente=nome,
            operador=operador,
            valor=valor,
            conta_origem=rastro.conta if rastro else None,
            coluna_origem=rastro.coluna if rastro else None,
        )

    diferenca = apuracao.diferenca_reconciliacao
    return MemoriaDivida(
        cod_ibge=cod_ibge,
        periodo=periodo,
        as_of=effective_as_of,
        versao_entrega=versao,
        componentes=[
            componente("dc_bruta", "+", apuracao.dc_bruta),
            componente("disponibilidades", "-", apuracao.disponibilidades),
            componente("haveres", "-", apuracao.haveres),
        ],
        dc_bruta=apuracao.dc_bruta,
        disponibilidades=apuracao.disponibilidades,
        haveres=apuracao.haveres,
        dcl=apuracao.dcl,
        dcl_reportada=apuracao.dcl_reportada,
        rcl_ajustada=apuracao.rcl_ajustada,
        pct_rcl=apuracao.pct_rcl,
        formula_dcl="DCL = dívida consolidada (DC bruta) − disponibilidades − haveres",
        formula_pct="% DCL/RCL = DCL ÷ RCL ajustada para endividamento × 100",
        reconciliacao_ok=(abs(diferenca) <= Decimal("0.01") if diferenca is not None else None),
        diferenca_reconciliacao=diferenca,
        detalhes={
            "linha_reportada_usada_no_calculo": False,
            "disponibilidade_bruta_ignorada": True,
            "deducoes_subtotal_ignorado": True,
            "base_legal": "LRF art. 30–31; Resolução SF 40/2001",
        },
        source_ref=_source_rgf(periodo, versao),
    )


def build_arvore(
    session: Session,
    cod_ibge: str,
    periodo: str,
    eixo: Literal["origem", "credor"],
    node: str | None,
    *,
    as_of: datetime | None = None,
) -> DividaArvoreOut:
    if eixo == "origem":
        versao = _resolve_rgf(session, cod_ibge, periodo, as_of)
        fato = _ensure_divida(session, cod_ibge, periodo, versao)
        envelope = _origem_envelope(session, fato, node)
        source = _source_rgf(periodo, versao)
        effective_as_of = _effective_as_of(
            session,
            cod_ibge=cod_ibge,
            relatorio=_REL_RGF,
            periodo=periodo,
            versao=versao,
            requested=as_of,
        )
    else:
        periodo_sadipem, versao = _resolve_latest_ente(
            session,
            cod_ibge=cod_ibge,
            relatorio=_REL_SADIPEM_OP,
            as_of=as_of,
        )
        operacoes = repository.read_operacoes(
            session,
            cod_ibge=cod_ibge,
            valid_time=date(_ano(periodo_sadipem), 12, 31),
            versao_entrega=versao,
        )
        if not operacoes:
            raise AppError(
                status=404,
                title="Operações SADIPEM ausentes",
                detail=f"Sem operações contratadas para {cod_ibge}/{periodo_sadipem}.",
            )
        nodes, medidas = _credor_tree(session, operacoes)
        if node is not None and not any(item.codigo == node for item in nodes):
            raise AppError(
                status=404,
                title="Nó de credor inexistente",
                detail=f"Nó '{node}' não existe no drill de credor.",
            )
        source = _source_sadipem(_REL_SADIPEM_OP, periodo_sadipem, versao)
        effective_as_of = _effective_as_of(
            session,
            cod_ibge=cod_ibge,
            relatorio=_REL_SADIPEM_OP,
            periodo=periodo_sadipem,
            versao=versao,
            requested=as_of,
        )
        envelope = build_drill_envelope(
            nodes,
            node,
            period=periodo,
            source_ref=source,
            node_measures=medidas,
        )
    return DividaArvoreOut(
        eixo=eixo,
        node=envelope.node,
        breadcrumb=envelope.breadcrumb,
        children=envelope.children,
        measures=envelope.measures,
        period=periodo,
        as_of=effective_as_of,
        source_ref=source,
    )


def build_capag(
    session: Session,
    cod_ibge: str,
    periodo: str,
    *,
    as_of: datetime | None = None,
) -> CapagResponse:
    fato = _ensure_capag(session, cod_ibge, _ano(periodo), as_of=as_of)
    effective_as_of = _effective_as_of(
        session,
        cod_ibge="BR",
        relatorio=_relatorio_capag(cod_ibge),
        periodo=str(fato.ano_ref),
        versao=fato.versao_entrega,
        requested=as_of,
    )
    source = _source_capag(fato.ano_ref, fato.versao_entrega, fato.cod_ibge)
    return CapagResponse(
        cod_ibge=cod_ibge,
        periodo=periodo,
        as_of=effective_as_of,
        hero=_capag_hero(fato, as_of=effective_as_of),
        memoria=CapagMemoria(
            formula_endividamento="Indicador 1 = dívida consolidada (DC bruta) ÷ RCL",
            base_numerador="Dívida Consolidada bruta publicada na planilha CAPAG",
            base_denominador="Receita Corrente Líquida publicada na planilha CAPAG",
            escala="razão na fonte; endividamento_pct = razão × 100",
            observacoes=[
                "A CAPAG não usa a DCL líquida do herói DDCL.",
                "Nota e três subindicadores são copiados de silver.tesouro_capag.",
            ],
        ),
        source_ref=source,
    )


def _materializar_vencimentos(
    session: Session,
    *,
    cod_ibge: str,
    periodo_ref: str,
    versao: str,
    as_of: datetime | None,
) -> None:
    operacao_versao = ingestion_repo.resolve_versao(
        session,
        cod_ibge=cod_ibge,
        relatorio=_REL_SADIPEM_OP,
        periodo=periodo_ref,
        as_of=as_of,
    )
    operacoes = (
        repository.read_operacoes(
            session,
            cod_ibge=cod_ibge,
            valid_time=date(_ano(periodo_ref), 12, 31),
            versao_entrega=operacao_versao,
        )
        if operacao_versao is not None
        else []
    )
    _, _ = _credor_tree(session, operacoes)
    por_id: dict[str, str] = {}
    for operacao in operacoes:
        origem = _origem_operacao(operacao.tipo_operacao)
        descricao = (operacao.credor or "Credor não informado").strip()
        if operacao.id_operacao:
            por_id[str(operacao.id_operacao)] = _credor_codigo(origem, descricao)

    rows: list[dict[str, Any]] = []
    for item in repository.read_cronograma(
        session,
        cod_ibge=cod_ibge,
        valid_time=date(_ano(periodo_ref), 12, 31),
        versao_entrega=versao,
    ):
        if item.ano is None:
            continue
        id_operacao = str(item.id_operacao or "não-informada")
        principal = Decimal(item.principal or 0)
        encargos = Decimal(item.encargos or 0)
        rows.append(
            {
                "cod_ibge": cod_ibge,
                "periodo_ref": periodo_ref,
                "id_operacao": id_operacao,
                "credor_codigo": por_id.get(id_operacao),
                "ano": item.ano,
                "principal": principal,
                "encargos": encargos,
                "dc_amortizacao": item.dc_amortizacao,
                "dc_encargos": item.dc_encargos,
                "oc_amortizacao": item.oc_amortizacao,
                "oc_encargos": item.oc_encargos,
                # O total do ano é amortização + encargos. Antes somava-se também um
                # ``juros`` que a fonte nunca publicou e que era sempre zero — inofensivo
                # na conta, enganoso na tela.
                "valor": principal + encargos,
                "versao_entrega": versao,
            }
        )
    repository.replace_vencimentos(
        session,
        cod_ibge=cod_ibge,
        periodo_ref=periodo_ref,
        versao_entrega=versao,
        rows=rows,
    )


def _fotografia_unica(fatos: list[Any]) -> list[Any]:
    """Reduz o cronograma a **uma** fotografia, quando a entrega trouxe mais de uma.

    O ``/opc-cronograma-pagamentos`` do SADIPEM não devolve a parcela de uma operação:
    devolve o cronograma consolidado do ente **como estava** na análise daquele pleito.
    Duas análises do mesmo ente produzem duas fotografias parecidas do mesmo estoque — e
    somá-las dobra a dívida.

    Critério: fica a operação com o maior número de anos cobertos e, no empate, a de maior
    identificador (a análise mais recente). Preferir a de maior valor seria escolher
    justamente a que mais infla; preferir a mais recente sozinha descartaria a fotografia
    completa quando a última análise cobre menos anos.
    """
    por_operacao: dict[str, list[Any]] = defaultdict(list)
    for fato in fatos:
        por_operacao[str(fato.id_operacao)].append(fato)
    if len(por_operacao) <= 1:
        return fatos

    def _chave(item: tuple[str, list[Any]]) -> tuple[int, str]:
        operacao, linhas = item
        return (len({linha.ano for linha in linhas}), operacao)

    return max(por_operacao.items(), key=_chave)[1]


def build_operacao(session: Session, cod_ibge: str, id_pleito: str) -> OperacaoDetalhe:
    """Uma operação de crédito inteira: pedido, situação cadastral e cronograma.

    Reúne três endpoints do SADIPEM que viviam separados. O CDP entra aqui pela primeira
    vez — a plataforma o ingeria e nenhuma tela o consumia —, casado **por processo**, que
    é o único vínculo possível: a base do CDP é nacional e não tem código de ente.

    Cada seção vazia diz por quê. Um cronograma ausente pode significar "a operação não foi
    contratada" ou "o Tesouro não publicou"; espaço em branco não distingue os dois, e o
    gestor conclui o que for pior.
    """
    pleito = repository.read_pvl_por_pleito(session, cod_ibge=cod_ibge, id_pleito=id_pleito)
    if pleito is None:
        raise AppError(
            status=404,
            title="Operação não encontrada",
            detail=(
                f"Não há pleito {id_pleito} para {cod_ibge} no SADIPEM. Ele pode pertencer "
                f"a outro ente ou ter saído da publicação vigente."
            ),
        )

    observacoes: dict[str, str] = {}
    sources = [
        _source_sadipem(
            _REL_SADIPEM_PVL, str(_ano_de(pleito.valid_time)), pleito.versao_entrega
        )
    ]

    cdp_rows = repository.read_cdp_do_processo(
        session, num_pvl=pleito.num_pvl, id_pleito=id_pleito
    )
    if not cdp_rows:
        observacoes["cdp"] = (
            "O Cadastro da Dívida Pública não traz este processo. O CDP é uma base "
            "nacional publicada pelo Tesouro; a ausência aqui é da fonte, não da carga."
        )
    else:
        sources.append(_source_sadipem(_REL_SADIPEM_CDP, "BR", cdp_rows[0].versao_entrega))

    crono_rows = repository.read_cronograma_do_pleito(
        session, cod_ibge=cod_ibge, id_pleito=id_pleito
    )
    if not crono_rows:
        observacoes["cronograma"] = (
            "Sem cronograma publicado para este pleito. O SADIPEM só publica cronograma de "
            "operação contratada — um pedido em tramitação ainda não tem vencimentos."
        )
    else:
        # O que a fonte devolve aqui **não** são as parcelas desta operação: é o
        # cronograma consolidado do ente inteiro, como estava na análise deste pleito.
        # Apresentá-lo colado ao valor da operação faria o gestor comparar R$ 6,6 bi de
        # serviço com R$ 450 mi de pedido e concluir que a conta não fecha.
        observacoes["cronograma_escopo"] = (
            "Este cronograma é do **ente inteiro**, na fotografia tirada quando este "
            "pleito foi analisado — não são as parcelas desta operação. O SADIPEM não "
            "publica cronograma por operação: cada análise devolve o serviço consolidado "
            "da dívida do ente naquele momento."
        )
        sources.append(
            _source_sadipem(
                _REL_SADIPEM_CRONO,
                str(_ano_de(crono_rows[0].valid_time)),
                crono_rows[0].versao_entrega,
            )
        )

    residual = (
        repository.read_residual_cronograma(
            session,
            cod_ibge=cod_ibge,
            versao_entrega=crono_rows[0].versao_entrega,
            id_operacao=id_pleito,
        )
        if crono_rows
        else None
    )
    resto_amort = Decimal(residual.principal or 0) if residual else _ZERO
    resto_enc = Decimal(residual.encargos or 0) if residual else _ZERO
    itens = [
        VencimentoItem(
            ano=row.ano or 0,
            principal=Decimal(row.principal or 0),
            encargos=Decimal(row.encargos or 0),
            valor=Decimal(row.principal or 0) + Decimal(row.encargos or 0),
            dc_amortizacao=row.dc_amortizacao,
            dc_encargos=row.dc_encargos,
            oc_amortizacao=row.oc_amortizacao,
            oc_encargos=row.oc_encargos,
            operacoes=1,
        )
        for row in crono_rows
    ]
    return OperacaoDetalhe(
        cod_ibge=cod_ibge,
        pleito=PvlItem(
            id_pvl=pleito.id_pvl,
            num_pvl=pleito.num_pvl,
            num_processo=pleito.num_processo,
            tipo_operacao=pleito.tipo_operacao,
            finalidade=pleito.finalidade,
            credor=pleito.credor,
            tipo_credor=pleito.tipo_credor,
            moeda=pleito.moeda,
            valor=pleito.valor,
            status=pleito.status,
            data_protocolo=pleito.data_protocolo,
            data_analise=pleito.data_analise,
        ),
        cdp=[
            CdpSituacao(
                num_pvl=row.num_pvl,
                num_processo=row.num_processo,
                data_ref=row.data_ref,
                situacao=row.situacao,
                motivo=row.motivo,
            )
            for row in cdp_rows
        ],
        cronograma=itens,
        total_amortizacao=sum((i.principal for i in itens), _ZERO),
        total_encargos=sum((i.encargos for i in itens), _ZERO),
        restante_amortizacao=resto_amort,
        restante_encargos=resto_enc,
        horizonte_ate=itens[-1].ano if itens else None,
        observacoes=observacoes,
        source_refs=sources,
    )


def _ano_de(valor: date | None) -> int:
    return valor.year if valor is not None else date.today().year


def build_cronograma(
    session: Session,
    cod_ibge: str,
    periodo: str,
    *,
    as_of: datetime | None = None,
) -> CronogramaResponse:
    periodo_ref, versao = _resolve_latest_ente(
        session,
        cod_ibge=cod_ibge,
        relatorio=_REL_SADIPEM_CRONO,
        as_of=as_of,
    )
    effective_as_of = _effective_as_of(
        session,
        cod_ibge=cod_ibge,
        relatorio=_REL_SADIPEM_CRONO,
        periodo=periodo_ref,
        versao=versao,
        requested=as_of,
    )
    _materializar_vencimentos(
        session,
        cod_ibge=cod_ibge,
        periodo_ref=periodo_ref,
        versao=versao,
        as_of=as_of,
    )
    fatos = repository.list_vencimentos(
        session,
        cod_ibge=cod_ibge,
        periodo_ref=periodo_ref,
        versao_entrega=versao,
    )
    if not fatos:
        raise AppError(
            status=404,
            title="Cronograma vazio",
            detail=f"SADIPEM não publicou vencimentos para {cod_ibge}/{periodo_ref}.",
        )
    # Cada PVL carrega uma fotografia do cronograma **consolidado do ente**, não a parcela
    # daquela operação. Somar duas fotografias multiplicaria a mesma dívida: o ente
    # apareceria devendo o dobro sem que nada na tela denunciasse. A ingestão já retém
    # apenas a fotografia contratada mais recente, mas depender disso deixa a correção do
    # número a cargo de outra camada — uma entrega antiga com oito operações produzia
    # exatamente esse erro, e só não chegou à tela porque uma retificação a superou.
    fatos = _fotografia_unica(fatos)
    grupos: dict[int, dict[str, Any]] = {}
    for fato in fatos:
        grupo = grupos.setdefault(
            fato.ano,
            {
                "principal": _ZERO,
                "encargos": _ZERO,
                "valor": _ZERO,
                "dc_amortizacao": _ZERO,
                "dc_encargos": _ZERO,
                "oc_amortizacao": _ZERO,
                "oc_encargos": _ZERO,
                "operacoes": set(),
            },
        )
        for campo in ("principal", "encargos", "valor"):
            grupo[campo] += Decimal(getattr(fato, campo))
        for campo in ("dc_amortizacao", "dc_encargos", "oc_amortizacao", "oc_encargos"):
            grupo[campo] += Decimal(getattr(fato, campo) or 0)
        grupo["operacoes"].add(fato.id_operacao)
    itens = [
        VencimentoItem(
            ano=ano,
            principal=grupo["principal"],
            encargos=grupo["encargos"],
            valor=grupo["valor"],
            dc_amortizacao=grupo["dc_amortizacao"],
            dc_encargos=grupo["dc_encargos"],
            oc_amortizacao=grupo["oc_amortizacao"],
            oc_encargos=grupo["oc_encargos"],
            operacoes=len(grupo["operacoes"]),
        )
        for ano, grupo in sorted(grupos.items())
    ]
    residual = repository.read_residual_cronograma(
        session,
        cod_ibge=cod_ibge,
        versao_entrega=versao,
        valid_time=date(_ano(periodo_ref), 12, 31),
    )
    resto_amort = Decimal(residual.principal or 0) if residual else _ZERO
    resto_enc = Decimal(residual.encargos or 0) if residual else _ZERO
    total_valor = sum((item.valor for item in itens), _ZERO)
    source = _source_sadipem(_REL_SADIPEM_CRONO, periodo_ref, versao)
    return CronogramaResponse(
        cod_ibge=cod_ibge,
        periodo_ref=periodo_ref,
        as_of=effective_as_of,
        versao_entrega=versao,
        itens=itens,
        total_principal=sum((item.principal for item in itens), _ZERO),
        total_encargos=sum((item.encargos for item in itens), _ZERO),
        total_dc=sum(
            ((item.dc_amortizacao or _ZERO) + (item.dc_encargos or _ZERO) for item in itens),
            _ZERO,
        ),
        total_oc=sum(
            ((item.oc_amortizacao or _ZERO) + (item.oc_encargos or _ZERO) for item in itens),
            _ZERO,
        ),
        restante_amortizacao=resto_amort,
        restante_encargos=resto_enc,
        horizonte_ate=itens[-1].ano if itens else None,
        total_com_residual=total_valor + resto_amort + resto_enc,
        total_valor=sum((item.valor for item in itens), _ZERO),
        source_ref=source,
    )


def _valor_rgf(
    rows: list[SilverRgf],
    *,
    contas: tuple[str, ...],
    colunas: tuple[str, ...],
) -> Decimal | None:
    for row in rows:
        conta = calculos.normalizar_texto(row.conta)
        coluna = calculos.normalizar_texto(row.coluna)
        if all(marca in conta for marca in contas) and any(marca in coluna for marca in colunas):
            return Decimal(row.valor or 0)
    return None


def _baselines_simulacao(
    session: Session,
    *,
    cod_ibge: str,
    periodo: str,
    versao: str,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    anexo4 = repository.read_rgf_anexo(
        session,
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        numero="04",
    )
    anexo3 = repository.read_rgf_anexo(
        session,
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        numero="03",
    )
    operacoes = _valor_rgf(
        anexo4,
        contas=("TOTAL CONSIDERADO", "CUMPRIMENTO DO LIMITE"),
        colunas=("VALOR",),
    )
    garantias = _valor_rgf(
        anexo3,
        contas=("TOTAL", "GARANTIAS CONCEDIDAS"),
        colunas=("VALOR", "ATE O"),
    )
    aro = None
    for row in anexo4:
        conta = calculos.normalizar_texto(row.conta)
        coluna = calculos.normalizar_texto(row.coluna)
        if (
            "ANTECIPACAO DA RECEITA ORCAMENTARIA" in conta
            and "LIMITE" not in conta
            and ("VALOR" in coluna or "ATE O" in coluna)
        ):
            aro = Decimal(row.valor or 0)
            break
    return operacoes, garantias, aro


def _posicao(
    *,
    indicador: str,
    rotulo: str,
    atual: Decimal | None,
    incremento: Decimal,
    rcl: Decimal,
    limite: LimiteLegal,
) -> PosicaoSimulada:
    projetado = atual + incremento if atual is not None else None
    pct_atual = calculos.percentual_sobre_rcl(atual, rcl) if atual is not None else None
    pct_projetado = calculos.percentual_sobre_rcl(projetado, rcl) if projetado is not None else None
    return PosicaoSimulada(
        indicador=indicador,
        rotulo=rotulo,
        valor_atual=atual,
        incremento=incremento,
        valor_projetado=projetado,
        pct_atual=pct_atual,
        pct_projetado=pct_projetado,
        teto_pct=limite.teto_pct,
        faixa_atual=classificar_faixa(pct_atual, limite) if pct_atual is not None else None,
        faixa_projetada=(
            classificar_faixa(pct_projetado, limite) if pct_projetado is not None else None
        ),
        posicao_atual_conhecida=atual is not None,
    )


def simular_operacao(
    session: Session,
    cod_ibge: str,
    periodo: str,
    req: SimularOperacaoRequest,
    *,
    as_of: datetime | None = None,
) -> SimulacaoResponse:
    """Simula sem persistir; valores ausentes no RGF não são convertidos em zero."""
    versao = _resolve_rgf(session, cod_ibge, periodo, as_of)
    effective_as_of = _effective_as_of(
        session,
        cod_ibge=cod_ibge,
        relatorio=_REL_RGF,
        periodo=periodo,
        versao=versao,
        requested=as_of,
    )
    fato = _ensure_divida(session, cod_ibge, periodo, versao)
    ente = _ente(session, cod_ibge)
    if fato.rcl_ajustada is None:
        raise AppError(
            status=422,
            title="RCL ajustada ausente",
            detail="O DDCL não traz RCL ajustada para calcular os limites.",
        )
    rcl = fato.rcl_ajustada
    try:
        calculos.percentual_sobre_rcl(_ZERO, rcl)
    except ValueError as exc:
        raise AppError(status=422, title="RCL inválida", detail=str(exc)) from exc
    assert ente.esfera is not None
    op_atual, garantia_rgf, aro_rgf = _baselines_simulacao(
        session,
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao=versao,
    )
    garantia_atual = req.garantias_atuais if req.garantias_atuais is not None else garantia_rgf
    aro_atual = req.aro_atual if req.aro_atual is not None else aro_rgf
    limites = {
        indicador: _limite(session, indicador=indicador, esfera=ente.esfera)
        for indicador in (
            "divida_consolidada_liquida",
            "operacoes_credito",
            "garantias",
            "aro",
        )
    }
    posicoes = [
        _posicao(
            indicador="divida_consolidada_liquida",
            rotulo="DCL líquida",
            atual=fato.dcl,
            incremento=req.valor_operacao,
            rcl=rcl,
            limite=limites["divida_consolidada_liquida"],
        ),
        _posicao(
            indicador="operacoes_credito",
            rotulo="Operações de crédito no exercício",
            atual=op_atual,
            incremento=req.valor_operacao,
            rcl=rcl,
            limite=limites["operacoes_credito"],
        ),
        _posicao(
            indicador="garantias",
            rotulo="Garantias concedidas",
            atual=garantia_atual,
            incremento=req.valor_garantia,
            rcl=rcl,
            limite=limites["garantias"],
        ),
        _posicao(
            indicador="aro",
            rotulo="Antecipação de Receita Orçamentária (ARO)",
            atual=aro_atual,
            incremento=req.valor_aro,
            rcl=rcl,
            limite=limites["aro"],
        ),
    ]
    return SimulacaoResponse(
        cod_ibge=cod_ibge,
        periodo=periodo,
        as_of=effective_as_of,
        rcl_ajustada=rcl,
        posicoes=posicoes,
        memoria={
            "premissa_dcl": "a nova operação é integralmente incorporada à DCL",
            "formula": "posição projetada = posição atual + incremento; % = posição / RCL × 100",
            "garantias_override": req.garantias_atuais is not None,
            "aro_override": req.aro_atual is not None,
            "ausencia_na_fonte_permanece_desconhecida": True,
            "base_legal": "Resoluções SF 40/2001 e 43/2001",
        },
        source_refs=[
            _source_rgf(periodo, versao, _ANEXO_DDCL),
            _source_rgf(periodo, versao, "Anexo 03 — Garantias"),
            _source_rgf(periodo, versao, "Anexo 04 — Operações de crédito"),
            SourceRef(
                relatorio="DIM_LIMITE_LEGAL",
                periodo=periodo,
                versao_entrega="Sprint 8",
            ),
        ],
    )


def build_pvl(session: Session, cod_ibge: str) -> PvlOut:
    """PVL/CDP do ente (SADIPEM) — pedidos de verificação de limites e suas decisões.

    O silver desta fonte pode estar vazio (nunca ingerido para o ente): nesse caso a
    resposta é explícita quanto à lacuna, em vez de sugerir "nenhum pedido existente".
    """
    linhas = repository.read_pvl(session, cod_ibge=cod_ibge)
    itens = [
        PvlItem(
            id_pvl=linha.id_pvl,
            num_pvl=linha.num_pvl,
            num_processo=linha.num_processo,
            tipo_operacao=linha.tipo_operacao,
            finalidade=linha.finalidade,
            credor=linha.credor,
            tipo_credor=linha.tipo_credor,
            moeda=linha.moeda,
            valor=linha.valor,
            status=linha.status,
            data_protocolo=linha.data_protocolo,
            data_analise=linha.data_analise,
        )
        for linha in linhas
    ]
    total = sum((i.valor or Decimal(0) for i in itens), Decimal(0)) if itens else None
    versao = linhas[0].versao_entrega if linhas else None
    observacao = (
        None
        if itens
        else (
            "Sem PVL/CDP em silver.sadipem_pvl para este ente: a fonte SADIPEM ainda não "
            "foi ingerida para ele. Ausência de ingestão não significa ausência de pedidos."
        )
    )
    return PvlOut(
        cod_ibge=cod_ibge,
        itens=itens,
        total_valor=total,
        versao_entrega=versao,
        observacao=observacao,
        source_ref=SourceRef(
            relatorio="SADIPEM", anexo="PVL/CDP", periodo=None, versao_entrega=versao
        ),
    )
