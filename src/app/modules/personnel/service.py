"""Regras da Despesa com Pessoal (Módulo 6): apuração líquida (RPPS-sensível) e faixa.

Fonte: ``silver.siconfi_rgf`` (Demonstrativo da Despesa com Pessoal — Anexo 1). O RGF é
**quadrimestral** (``AAAA-Qn``); a RCL e a faixa do limite vêm do RREO (Sprint 2), então
mapeamos o quadrimestre ao bimestre correspondente (Q1→B2, Q2→B4, Q3→B6) só para buscar
a RCL/limite. O cálculo da líquida (bruta − exclusões, exclusões sensíveis a
``dim_ente.rpps``) fica em :mod:`app.modules.personnel.pessoal`; a classificação de faixa
reusa ``indicators`` (fonte única de verdade), persistindo o ``mart_indicador`` do
``pessoal_executivo`` que alimenta o semáforo (Sprint 3).
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.catalog import service as catalog_service
from app.modules.catalog.models import DimEnte
from app.modules.indicators import serie_ajuste
from app.modules.indicators import service as indicators_service
from app.modules.indicators.schemas import IndicadorOut, SerieAjuste
from app.modules.ingestion import repository as ingestion_repo
from app.modules.ingestion.models import SilverRgf
from app.modules.personnel import pessoal, repository
from app.modules.personnel.models import FatoPessoal
from app.modules.personnel.pessoal import MEDIDAS, ROOT_CODIGO, ROOT_DESCRICAO, PoderInfo
from app.modules.personnel.schemas import (
    ComparacaoPessoal,
    ExclusaoItem,
    MemoriaPessoal,
    PessoalDetalhe,
    PessoalTotais,
    PoderItem,
    PorPoderOut,
    SeriePessoalItem,
)
from app.shared import periodo as periodo_util
from app.shared.ausencia import ausencia_de_entrega
from app.shared.envelope import DrillEnvelope, Measures
from app.shared.hierarchy import HierarchyNode, build_drill_envelope
from app.shared.source_ref import SourceRef

_RELATORIO = "RGF"
_ANEXO = "Anexo 01"
_QUAD_RE = re.compile(r"^(\d{4})-Q([1-3])$")
_BIM_RE = re.compile(r"^(\d{4})-B([1-6])$")

Medidas = dict[str, Decimal]

_MOTIVO_RPPS_INCLUIDA = "Ente com RPPS: inativos/pensionistas com recursos vinculados excluídos."
_MOTIVO_RPPS_EXCLUIDA = "Ente sem RPPS: parcela não excluída (não há fundo vinculado)."


def _source_ref(periodo: str, versao: str) -> SourceRef:
    return SourceRef(relatorio="RGF", anexo=_ANEXO, periodo=periodo, versao_entrega=versao)


def _rreo_periodo(periodo: str) -> str | None:
    """Período RREO correspondente (base da RCL/limite). Regra canônica em §6.6.

    A cópia local cobria só quadrimestre e devolvia ``None`` para o RGF semestral — o de
    município com menos de 50 mil habitantes —, deixando o limite de pessoal sem
    denominador justamente na faixa mais numerosa do país.
    """
    return periodo_util.em_bimestre(periodo)


def _resolve_versao(session: Session, cod_ibge: str, periodo: str, as_of: datetime | None) -> str:
    versao = ingestion_repo.resolve_versao(
        session, cod_ibge=cod_ibge, relatorio=_RELATORIO, periodo=periodo, as_of=as_of
    )
    if versao is None:
        raise ausencia_de_entrega(
            session,
            cod_ibge=cod_ibge,
            relatorio=_RELATORIO,
            periodo=periodo,
            title="RGF ausente",
            detail=f"Sem RGF vigente para {cod_ibge} em {periodo}.",
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


def _rcl_12m(
    session: Session, cod_ibge: str, periodo: str, as_of: datetime | None
) -> Decimal | None:
    """RCL (Sprint 2) do período RREO correspondente — ausência não bloqueia a tela."""
    rreo = _rreo_periodo(periodo)
    if rreo is None:
        return None
    try:
        return indicators_service.obter_fato_rcl(session, cod_ibge, rreo, as_of=as_of).rcl_12m
    except AppError:
        return None


#: Linha em que o Anexo 01 publica o **denominador do limite**. O texto completo é
#: "= RECEITA CORRENTE LÍQUIDA AJUSTADA PARA CÁLCULO DOS LIMITES DA DESPESA COM
#: PESSOAL"; casamos pelo núcleo para sobreviver a variações de layout entre exercícios.
_TRECHO_RCL_AJUSTADA = "RECEITA CORRENTE L"
_TRECHO_LIMITES_PESSOAL = "LIMITES DA DESPESA COM PESSOAL"


def _rcl_ajustada_publicada(
    session: Session, cod_ibge: str, periodo: str, versao: str
) -> Decimal | None:
    """Denominador oficial do limite de pessoal, como o ente o publicou.

    O teto do art. 20 da LRF incide sobre a **RCL ajustada**, não sobre a RCL cheia: a
    EC 105/2019 manda deduzir as transferências recebidas por emenda individual. O
    demonstrativo já traz a conta pronta — reconstruí-la aqui significaria reimplementar
    uma regra que muda com a legislação e errar em silêncio quando ela mudar.
    """
    linha = session.execute(
        select(SilverRgf.valor)
        .where(
            SilverRgf.cod_ibge == cod_ibge,
            SilverRgf.periodo == periodo,
            SilverRgf.versao_entrega == versao,
            SilverRgf.anexo.like("RGF-Anexo 01%"),
            SilverRgf.conta.ilike(f"%{_TRECHO_RCL_AJUSTADA}%"),
            SilverRgf.conta.ilike(f"%{_TRECHO_LIMITES_PESSOAL}%"),
            SilverRgf.valor.is_not(None),
        )
        .limit(1)
    ).scalar()
    return Decimal(linha) if linha is not None else None


def _pct(parte: Decimal | None, todo: Decimal | None) -> Decimal | None:
    if parte is None or not todo:
        return None
    return parte / todo * Decimal(100)


# --- leitura + apuração dos componentes por poder ---
def _ler_componentes(
    session: Session, cod_ibge: str, periodo: str, versao: str
) -> dict[PoderInfo, dict[str, Decimal]]:
    """Componentes (bruta/exclusões/líquida reportada) por poder, a partir do RGF."""
    por_poder: dict[PoderInfo, dict[str, Decimal]] = {}
    for row in repository.read_rgf_pessoal(
        session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
    ):
        info = pessoal.resolver_poder(row.poder)
        papel = pessoal.classificar_conta(row.conta)
        if info is None or papel is None or not pessoal.classificar_valor_coluna(row.coluna):
            continue
        comp = por_poder.setdefault(info, {})
        comp[papel] = comp.get(papel, Decimal(0)) + Decimal(row.valor or 0)
    return por_poder


# --- materialização silver → gold ---
def _ensure_dim(session: Session, infos: list[PoderInfo]) -> None:
    repository.upsert_poder(
        session,
        {
            "codigo": ROOT_CODIGO, "descricao": ROOT_DESCRICAO,
            "parent_codigo": None, "nivel": 1, "path": ROOT_CODIGO,
        },
        sobrescrever_descricao=False,
    )
    for info in infos:
        repository.upsert_poder(
            session,
            {
                "codigo": info.codigo, "descricao": info.descricao,
                "parent_codigo": pessoal.parent_de(info.codigo),
                "nivel": pessoal.nivel_de(info.codigo), "path": info.codigo,
            },
            sobrescrever_descricao=True,
        )


def materializar_pessoal(
    session: Session,
    cod_ibge: str,
    periodo: str,
    versao: str,
    *,
    rpps: bool,
    rcl: Decimal | None,
) -> list[FatoPessoal]:
    """ETL do RGF Anexo 1 → dim_poder_orgao + fato_pessoal (idempotente, RPPS-sensível)."""
    por_poder = _ler_componentes(session, cod_ibge, periodo, versao)
    _ensure_dim(session, list(por_poder))
    # O limite do art. 20 é sobre a RCL **ajustada** publicada no próprio anexo. Só onde
    # o ente não a publicou é que a RCL cheia serve de aproximação — e nesse caso a linha
    # guarda ``rcl_ajustada = NULL``, que é como se sabe depois qual base foi usada.
    ajustada = _rcl_ajustada_publicada(session, cod_ibge, periodo, versao)
    denominador = ajustada if ajustada else rcl
    for info, comp in por_poder.items():
        ap = pessoal.apurar(comp, rpps=rpps)
        repository.upsert_fato(
            session,
            {
                "cod_ibge": cod_ibge,
                "periodo": periodo,
                "poder_codigo": info.codigo,
                "despesa_bruta": ap.despesa_bruta,
                "exclusoes": ap.exclusoes,
                "despesa_liquida": ap.despesa_liquida,
                "rcl_ajustada": ajustada,
                "pct_rcl": _pct(ap.despesa_liquida, denominador),
                "versao_entrega": versao,
            },
        )
    return repository.list_fatos(
        session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
    )


def _ensure_gold(
    session: Session,
    cod_ibge: str,
    periodo: str,
    versao: str,
    *,
    rpps: bool,
    rcl: Decimal | None,
    forcar: bool = False,
) -> list[FatoPessoal]:
    """Fatos do período — materializando na primeira leitura, ou sempre que ``forcar``.

    A guarda de "materializa uma vez" mantém a página rápida, mas ela também congela o
    fato quando a **regra** muda (foi o que aconteceu quando o denominador do limite
    passou a ser a RCL ajustada: o mart recalculava e o detalhe não, e as duas telas
    passaram a discordar do mesmo limite).

    Quem sabe que a regra mudou é o job de materialização — por isso ele força, e a
    leitura não paga por isso.
    """
    if not forcar:
        fatos = repository.list_fatos(
            session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
        )
        if fatos:
            return fatos
    return materializar_pessoal(
        session, cod_ibge, periodo, versao, rpps=rpps, rcl=rcl
    )


# --- agregação (nó com linha própria = fato; sem linha = soma dos filhos) ---
def _medidas_efetivas(
    nodes: list[HierarchyNode], proprias: dict[str, Medidas]
) -> dict[str, Medidas]:
    filhos: dict[str | None, list[str]] = {}
    for n in nodes:
        filhos.setdefault(n.parent_codigo, []).append(n.codigo)
    efetivas: dict[str, Medidas] = {}

    def resolver(codigo: str) -> Medidas:
        if codigo in efetivas:
            return efetivas[codigo]
        if codigo in proprias:
            efetivas[codigo] = dict(proprias[codigo])
            return efetivas[codigo]
        soma: Medidas = {}
        for filho in filhos.get(codigo, []):
            for m, v in resolver(filho).items():
                soma[m] = soma.get(m, Decimal(0)) + v
        efetivas[codigo] = soma
        return soma

    for n in nodes:
        resolver(n.codigo)
    return efetivas


def _carregar_arvore(
    session: Session,
    cod_ibge: str,
    periodo: str,
    versao: str,
    *,
    rpps: bool,
    rcl: Decimal | None,
    forcar: bool = False,
) -> tuple[list[HierarchyNode], dict[str, Medidas]]:
    fatos = _ensure_gold(
        session, cod_ibge, periodo, versao, rpps=rpps, rcl=rcl, forcar=forcar
    )
    proprias: dict[str, Medidas] = {
        f.poder_codigo: {m: Decimal(v) for m in MEDIDAS if (v := getattr(f, m)) is not None}
        for f in fatos
    }
    codigos = set(proprias) | {ROOT_CODIGO}
    for codigo in list(codigos):
        pai = pessoal.parent_de(codigo)
        while pai is not None:
            codigos.add(pai)
            pai = pessoal.parent_de(pai)
    dim_rows = repository.list_poderes(session, codigos)
    nodes = [
        HierarchyNode(
            codigo=d.codigo, descricao=d.descricao, parent_codigo=d.parent_codigo,
            nivel=d.nivel, path=d.path,
        )
        for d in dim_rows
    ]
    medidas = _medidas_efetivas(nodes, proprias)
    return nodes, medidas


def _totais(medidas: dict[str, Medidas]) -> PessoalTotais:
    m = medidas.get(ROOT_CODIGO, {})
    return PessoalTotais(**{k: m.get(k) for k in MEDIDAS})


def _measures_map(medidas: dict[str, Medidas]) -> dict[str, Measures]:
    return {c: dict(m) for c, m in medidas.items()}


# --- faixa do Executivo (reusa indicators; persiste mart p/ o semáforo da Sprint 3) ---
def _faixa_executivo(
    session: Session, cod_ibge: str, periodo: str, liquida: Decimal, as_of: datetime | None
) -> IndicadorOut | None:
    rreo = _rreo_periodo(periodo)
    if rreo is None:
        return None
    try:
        versao_rgf = ingestion_repo.resolve_versao(
            session,
            cod_ibge=cod_ibge,
            relatorio=_RELATORIO,
            periodo=periodo,
            as_of=as_of,
        )
        versao_rreo = ingestion_repo.resolve_versao(
            session,
            cod_ibge=cod_ibge,
            relatorio="RREO",
            periodo=rreo,
            as_of=as_of,
        )
        if versao_rgf is None or versao_rreo is None:
            return None
        rgf_ref = SourceRef(
            relatorio="RGF",
            anexo=_ANEXO,
            periodo=periodo,
            versao_entrega=versao_rgf,
        )
        rreo_ref = SourceRef(
            relatorio="RREO",
            anexo="Anexo 03",
            periodo=rreo,
            versao_entrega=versao_rreo,
        )
        ajustada = _rcl_ajustada_publicada(session, cod_ibge, periodo, versao_rgf)
        if ajustada:
            # Mesmo denominador do ``fato_pessoal``: se o mart redividisse pela RCL
            # cheia, o semáforo mostraria uma faixa mais folgada que a página de
            # detalhe — duas telas do mesmo produto discordando sobre o mesmo limite.
            return indicators_service.classificar_sobre_base(
                session, cod_ibge, rreo, "pessoal_executivo", liquida, ajustada,
                denominador="rcl_ajustada", versao_entrega=versao_rreo,
                poder="Executivo", as_of=as_of,
                source_ref=SourceRef(
                    relatorio="RGF/RREO",
                    anexo="RGF Anexo 01 / RREO Anexo 03",
                    periodo=f"{periodo} / {rreo}",
                    versao_entrega=f"RGF:{versao_rgf};RREO:{versao_rreo}",
                ),
                source_components=(rgf_ref, rreo_ref),
            )
        return indicators_service.classificar_limite(
            session, cod_ibge, rreo, "pessoal_executivo", liquida,
            poder="Executivo", as_of=as_of,
            source_ref=SourceRef(
                relatorio="RGF/RREO",
                anexo="RGF Anexo 01 / RREO Anexo 03",
                periodo=f"{periodo} / {rreo}",
                versao_entrega=f"RGF:{versao_rgf};RREO:{versao_rreo}",
            ),
            source_components=(rgf_ref, rreo_ref),
        )
    except AppError:
        return None


def _poder_item(
    codigo: str, medidas: Medidas, *, faixa: IndicadorOut | None = None
) -> PoderItem:
    info = pessoal.poder_por_codigo(codigo)
    descricao = info.descricao if info else (ROOT_DESCRICAO if codigo == ROOT_CODIGO else codigo)
    return PoderItem(
        poder_codigo=codigo,
        descricao=descricao,
        despesa_bruta=medidas.get("despesa_bruta"),
        exclusoes=medidas.get("exclusoes"),
        despesa_liquida=medidas.get("despesa_liquida"),
        pct_rcl=medidas.get("pct_rcl"),
        faixa=faixa.faixa if faixa else None,
        teto_pct=faixa.teto_pct if faixa else None,
        indicador=info.indicador if info else None,
    )


def _item_executivo(
    session: Session,
    cod_ibge: str,
    periodo: str,
    medidas: dict[str, Medidas],
    as_of: datetime | None,
) -> PoderItem | None:
    exec_info = pessoal.resolver_poder("E")
    assert exec_info is not None
    med = medidas.get(exec_info.codigo)
    if med is None:
        return None
    liquida = med.get("despesa_liquida", Decimal(0))
    faixa = _faixa_executivo(session, cod_ibge, periodo, liquida, as_of)
    return _poder_item(exec_info.codigo, med, faixa=faixa)


# --- série e comparação temporal ---
def _serie(
    session: Session, cod_ibge: str, base_periodo: str
) -> tuple[list[SeriePessoalItem], SerieAjuste]:
    """Série RGF multi-exercício + comparabilidade (folha a preços do período e por habitante)."""
    serie: list[SeriePessoalItem] = []
    for periodo in repository.distinct_periodos_fato(session, cod_ibge=cod_ibge):
        versao = ingestion_repo.resolve_versao(
            session, cod_ibge=cod_ibge, relatorio=_RELATORIO, periodo=periodo
        )
        if versao is None:
            continue
        fatos = repository.list_fatos(
            session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
        )
        if not fatos:
            continue
        liquida = sum((f.despesa_liquida or Decimal(0) for f in fatos), Decimal(0))
        pct = sum((f.pct_rcl or Decimal(0) for f in fatos), Decimal(0))
        serie.append(
            SeriePessoalItem(
                periodo=periodo, despesa_liquida=liquida, pct_rcl=pct or None
            )
        )
    ajuste = serie_ajuste.calcular(
        session, cod_ibge, [s.periodo for s in serie], base_periodo
    )
    por_periodo = serie_ajuste.indexar(ajuste)
    for item in serie:
        a = por_periodo.get(item.periodo)
        item.despesa_liquida_real = serie_ajuste.real(item.despesa_liquida, a)
        item.despesa_liquida_per_capita = serie_ajuste.per_capita(item.despesa_liquida, a)
        item.populacao = a.populacao if a else None
    return serie, ajuste


def _comparacao(
    serie: list[SeriePessoalItem], periodo: str, atual: Decimal | None
) -> ComparacaoPessoal | None:
    m = _QUAD_RE.match(periodo) or _BIM_RE.match(periodo)
    if m is None:
        return None
    marca = periodo.split("-", 1)[1]
    anterior_cod = f"{int(m.group(1)) - 1}-{marca}"
    anterior = next((s for s in serie if s.periodo == anterior_cod), None)
    if anterior is None:
        return None
    delta = None
    if atual is not None and anterior.despesa_liquida:
        delta = (atual - anterior.despesa_liquida) / anterior.despesa_liquida * Decimal(100)
    return ComparacaoPessoal(
        periodo_anterior=anterior_cod,
        despesa_liquida_anterior=anterior.despesa_liquida,
        delta_pct=delta,
    )


# --- endpoints ---
def build_detalhe(
    session: Session,
    cod_ibge: str,
    periodo: str,
    *,
    as_of: datetime | None = None,
    forcar: bool = False,
) -> PessoalDetalhe:
    """Cabeçalho + composição (por poder) + série + comparação (Padrão de Detalhe).

    ``forcar`` é usado pelo job de materialização, que reapura mesmo com fato existente.
    """
    versao = _resolve_versao(session, cod_ibge, periodo, as_of)
    ente = _ente(session, cod_ibge)
    rcl = _rcl_12m(session, cod_ibge, periodo, as_of)
    nodes, medidas = _carregar_arvore(
        session, cod_ibge, periodo, versao, rpps=ente.rpps, rcl=rcl, forcar=forcar
    )
    # Composição = poderes (filhos do consolidado); o drill inicia no nó ENTE.
    consolidado = build_drill_envelope(
        nodes, ROOT_CODIGO, period=periodo, node_measures=_measures_map(medidas)
    )
    serie, ajuste = _serie(session, cod_ibge, periodo)
    totais = _totais(medidas)
    return PessoalDetalhe(
        cod_ibge=cod_ibge,
        periodo=periodo,
        periodo_rreo=_rreo_periodo(periodo),
        versao_entrega=versao,
        esfera=ente.esfera,
        rpps=ente.rpps,
        totais=totais,
        rcl_12m=rcl,
        executivo=_item_executivo(session, cod_ibge, periodo, medidas, as_of),
        composicao=consolidado.children,
        serie=serie,
        serie_ajuste=ajuste,
        comparacao=_comparacao(serie, periodo, totais.despesa_liquida),
        periodo_breadcrumb=catalog_service.periodo_breadcrumb(session, periodo),
        source_ref=_source_ref(periodo, versao),
    )


def build_arvore(
    session: Session,
    cod_ibge: str,
    periodo: str,
    node: str | None,
    *,
    as_of: datetime | None = None,
) -> DrillEnvelope:
    """Drill DOWN/UP por poder/órgão (§6.1)."""
    versao = _resolve_versao(session, cod_ibge, periodo, as_of)
    ente = _ente(session, cod_ibge)
    rcl = _rcl_12m(session, cod_ibge, periodo, as_of)
    nodes, medidas = _carregar_arvore(
        session, cod_ibge, periodo, versao, rpps=ente.rpps, rcl=rcl
    )
    if node is not None and not any(n.codigo == node for n in nodes):
        raise AppError(
            status=404, title="Nó inexistente",
            detail=f"Poder/órgão '{node}' sem dado para {cod_ibge} em {periodo}.",
        )
    return build_drill_envelope(
        nodes, node, period=periodo,
        source_ref=_source_ref(periodo, versao), node_measures=_measures_map(medidas),
    )


def build_memoria(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> MemoriaPessoal:
    """Memória rastreável: bruta, exclusões (com/sem RPPS), líquida e cross-check reportado."""
    versao = _resolve_versao(session, cod_ibge, periodo, as_of)
    ente = _ente(session, cod_ibge)
    rcl = _rcl_12m(session, cod_ibge, periodo, as_of)
    por_poder = _ler_componentes(session, cod_ibge, periodo, versao)

    # Consolidado (soma dos poderes) dos componentes.
    consolidado: dict[str, Decimal] = {}
    for comp in por_poder.values():
        for papel, valor in comp.items():
            consolidado[papel] = consolidado.get(papel, Decimal(0)) + valor
    apur = pessoal.apurar(consolidado, rpps=ente.rpps)

    detalhe_excl = [
        ExclusaoItem(
            componente=comp, valor=consolidado.get(comp, Decimal(0)), incluida=True, motivo=None
        )
        for comp in pessoal.EXCLUSOES_INCONDICIONAIS
    ]
    detalhe_excl.append(
        ExclusaoItem(
            componente=pessoal.EXCLUSAO_CONDICIONAL_RPPS,
            valor=apur.exclusao_rpps_disponivel,
            incluida=ente.rpps,
            motivo=_MOTIVO_RPPS_INCLUIDA if ente.rpps else _MOTIVO_RPPS_EXCLUIDA,
        )
    )
    return MemoriaPessoal(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        rpps=ente.rpps,
        despesa_bruta=apur.despesa_bruta,
        exclusoes=apur.exclusoes,
        despesa_liquida=apur.despesa_liquida,
        despesa_liquida_reportada=consolidado.get(pessoal.LIQUIDA_REPORTADA) or None,
        exclusoes_reportadas=consolidado.get(pessoal.EXCL_TOTAL_REPORTADO) or None,
        rcl_12m=rcl,
        pct_rcl=_pct(apur.despesa_liquida, rcl),
        formula_liquida="despesa_liquida = despesa_bruta − exclusoes (exclusões sensíveis a RPPS)",
        formula_pct="pct_rcl = despesa_liquida / rcl_12m × 100",
        exclusoes_detalhe=detalhe_excl,
        hierarquia="Ente → Poder → Órgão → Unidade",
        detalhes={
            "poderes": [info.descricao for info in por_poder],
            "base_legal": "LRF art. 18–20; exclusões art. 19, §1º",
            "periodo_rcl": _rreo_periodo(periodo),
        },
        source_ref=_source_ref(periodo, versao),
    )


def build_por_poder(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> PorPoderOut:
    """Despesa com pessoal por poder, com faixa do Executivo (teto 54%/49% da RCL)."""
    versao = _resolve_versao(session, cod_ibge, periodo, as_of)
    ente = _ente(session, cod_ibge)
    rcl = _rcl_12m(session, cod_ibge, periodo, as_of)
    nodes, medidas = _carregar_arvore(
        session, cod_ibge, periodo, versao, rpps=ente.rpps, rcl=rcl
    )
    itens: list[PoderItem] = []
    for node in sorted(nodes, key=lambda n: n.codigo):
        if node.parent_codigo is None:  # raiz é o consolidado, não um poder
            continue
        info = pessoal.poder_por_codigo(node.codigo)
        faixa = None
        if info is not None and info.indicador is not None:
            liquida = medidas.get(node.codigo, {}).get("despesa_liquida", Decimal(0))
            faixa = _faixa_executivo(session, cod_ibge, periodo, liquida, as_of)
        itens.append(_poder_item(node.codigo, medidas.get(node.codigo, {}), faixa=faixa))
    return PorPoderOut(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        esfera=ente.esfera,
        rpps=ente.rpps,
        consolidado=_poder_item(ROOT_CODIGO, medidas.get(ROOT_CODIGO, {})),
        itens=itens,
        source_ref=_source_ref(periodo, versao),
    )
