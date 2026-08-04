"""Regras do catálogo: conformação de dim_ente, seed de dimensões, drill de períodos."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.catalog import repository
from app.modules.catalog.models import ESFERA_ESTADUAL, ESFERA_FEDERAL, ESFERA_MUNICIPAL, DimEnte
from app.modules.catalog.schemas import (
    EnteBusca,
    EnteOut,
    EntesBuscaResponse,
    PeriodoDisponivel,
    PeriodosResponse,
)
from app.shared import periodo as periodo_util
from app.shared.envelope import DrillEnvelope, DrillNodeRef
from app.shared.hierarchy import (
    HierarchyNode,
    breadcrumb_of,
    build_drill_envelope,
    ltree_label,
    make_path,
)
from app.shared.source_ref import SourceRef

# --- dim_limite_legal (DADO): tetos/pisos por esfera (§2 CLAUDE.md) ---
# (indicador, esfera, poder, sentido, teto_ou_piso_pct)
_LIMITES: list[tuple[str, str, str, str, str]] = [
    ("pessoal_executivo", ESFERA_MUNICIPAL, "Executivo", "teto", "54"),
    ("pessoal_executivo", ESFERA_ESTADUAL, "Executivo", "teto", "49"),
    ("divida_consolidada_liquida", ESFERA_MUNICIPAL, "", "teto", "120"),
    ("divida_consolidada_liquida", ESFERA_ESTADUAL, "", "teto", "200"),
    ("operacoes_credito", ESFERA_MUNICIPAL, "", "teto", "16"),
    ("operacoes_credito", ESFERA_ESTADUAL, "", "teto", "16"),
    ("garantias", ESFERA_MUNICIPAL, "", "teto", "22"),
    ("garantias", ESFERA_ESTADUAL, "", "teto", "22"),
    ("aro", ESFERA_MUNICIPAL, "", "teto", "7"),
    ("aro", ESFERA_ESTADUAL, "", "teto", "7"),
    ("saude_minimo", ESFERA_MUNICIPAL, "", "piso", "15"),
    ("saude_minimo", ESFERA_ESTADUAL, "", "piso", "12"),
    ("educacao_mde", ESFERA_MUNICIPAL, "", "piso", "25"),
    ("educacao_mde", ESFERA_ESTADUAL, "", "piso", "25"),
    ("fundeb_profissionais", ESFERA_MUNICIPAL, "", "piso", "70"),
    ("fundeb_profissionais", ESFERA_ESTADUAL, "", "piso", "70"),
]

# Relatório que ancora o período default do shell (o RREO é o de maior granularidade).
RELATORIO_PADRAO = "RREO"

_UF_REGIAO = {
    "AC": "NO", "AP": "NO", "AM": "NO", "PA": "NO", "RO": "NO", "RR": "NO", "TO": "NO",
    "AL": "NE", "BA": "NE", "CE": "NE", "MA": "NE", "PB": "NE", "PE": "NE", "PI": "NE",
    "RN": "NE", "SE": "NE",
    "DF": "CO", "GO": "CO", "MT": "CO", "MS": "CO",
    "ES": "SE", "MG": "SE", "RJ": "SE", "SP": "SE",
    "PR": "SU", "RS": "SU", "SC": "SU",
}
_REGIAO_ALIASES = {
    "N": "NO", "NORTE": "NO", "NO": "NO",
    "NE": "NE", "NORDESTE": "NE",
    "CO": "CO", "CENTRO-OESTE": "CO", "CENTRO OESTE": "CO",
    "SE": "SE", "SUDESTE": "SE",
    "S": "SU", "SUL": "SU", "SU": "SU",
}


def seed_limites_legais(session: Session) -> None:
    """Popula ``gold.dim_limite_legal`` (idempotente). Alerta/prudencial = 90%/95% do teto."""
    for indicador, esfera, poder, sentido, pct in _LIMITES:
        teto = Decimal(pct)
        alerta = teto * Decimal("0.90") if sentido == "teto" else None
        prudencial = teto * Decimal("0.95") if sentido == "teto" else None
        repository.upsert_limite(
            session,
            {
                "indicador": indicador,
                "esfera": esfera,
                "poder": poder,
                "sentido": sentido,
                "teto_pct": teto,
                "alerta_pct": alerta,
                "prudencial_pct": prudencial,
            },
        )


def gerar_periodos(anos: list[int]) -> list[dict[str, Any]]:
    """Gera os nós de ``dim_periodo`` (ano → bimestre/quadrimestre → mês)."""
    nodes: list[dict[str, Any]] = []
    for ano in anos:
        ano_cod = str(ano)
        ano_path = ltree_label(ano_cod)
        nodes.append(
            {"codigo": ano_cod, "descricao": f"Exercício {ano}", "parent_codigo": None,
             "nivel": 1, "path": ano_path, "ano": ano}
        )
        for b in range(1, 7):
            cod = f"{ano}-B{b}"
            nodes.append(
                {"codigo": cod, "descricao": f"{b}º bimestre/{ano}", "parent_codigo": ano_cod,
                 "nivel": 2, "path": make_path(ano_path, cod), "ano": ano, "bimestre": b}
            )
        for q in range(1, 4):
            cod = f"{ano}-Q{q}"
            nodes.append(
                {"codigo": cod, "descricao": f"{q}º quadrimestre/{ano}", "parent_codigo": ano_cod,
                 "nivel": 2, "path": make_path(ano_path, cod), "ano": ano, "quadrimestre": q}
            )
        # RGF semestral dos municípios < 50 mil hab. (LRF art. 63) — nível 2 do exercício.
        for s in range(1, 3):
            cod = f"{ano}-S{s}"
            nodes.append(
                {"codigo": cod, "descricao": f"{s}º semestre/{ano}", "parent_codigo": ano_cod,
                 "nivel": 2, "path": make_path(ano_path, cod), "ano": ano, "quadrimestre": None}
            )
        for m in range(1, 13):
            bim = math.ceil(m / 2)
            bim_cod = f"{ano}-B{bim}"
            cod = f"{ano}-M{m:02d}"
            nodes.append(
                {"codigo": cod, "descricao": f"{m:02d}/{ano}", "parent_codigo": bim_cod,
                 "nivel": 3, "path": make_path(make_path(ano_path, bim_cod), cod),
                 "ano": ano, "mes": m, "bimestre": bim}
            )
    return nodes


def seed_periodos(session: Session, anos: list[int]) -> None:
    for node in gerar_periodos(anos):
        repository.upsert_periodo(session, node)


# Horizonte histórico do backfill (Sprint 21): 2021→exercício corrente + 1 (projeções).
HORIZONTE_ANOS: list[int] = list(range(2021, 2028))


def seed_dimensoes(session: Session, *, anos: list[int] | None = None) -> None:
    """Semeia as dimensões que são DADO de referência (limites + períodos).

    O horizonte default cobre 2021→2027 (Sprint 21: profundidade histórica + espaço para
    as projeções). ``anos`` permite restringir em testes.
    """
    seed_limites_legais(session)
    seed_periodos(session, anos or HORIZONTE_ANOS)


def _normalizar_esfera(valor: str | None) -> str | None:
    """Esfera do SICONFI → vocabulário da plataforma.

    A fonte publica **quatro** esferas — ``M`` (5.570 municípios), ``E`` (26 estados),
    ``D`` (Distrito Federal) e ``U`` (União). O mapeamento cobria só as duas primeiras, e
    as outras duas caíam em ``None``: a União e o DF ficavam **sem esfera**, violando a
    invariante nº 1 do domínio — nenhum limite da LRF se aplica sem ela.

    **DF → estadual.** O Distrito Federal acumula competências estaduais e municipais
    (CF, art. 32, §1º), e o teto de pessoal que lhe cabe é o da esfera estadual: 49% da
    RCL no Executivo (LRF, art. 20, II). A classificação é corroborada pelo próprio
    Tesouro, que o publica na CAPAG **dos estados** — são 27 entes ali, os 26 estados
    mais o DF.

    **União → federal.** A plataforma não publica limites federais e não atende a União.
    Marcar a esfera é o que separa "conhecida e sem limite cadastrado" de "desconhecida":
    a primeira é um fato, a segunda é uma falha de catálogo.
    """
    if not valor:
        return None
    v = valor.strip().lower()
    if v in ("m", "municipal"):
        return ESFERA_MUNICIPAL
    if v in ("e", "estadual", "d", "distrital"):
        return ESFERA_ESTADUAL
    if v in ("u", "uniao", "união", "federal"):
        return ESFERA_FEDERAL
    return None


def _normalizar_regiao(valor: str | None, uf: str | None) -> str | None:
    if valor:
        normalized = " ".join(valor.strip().upper().split())
        resolved = _REGIAO_ALIASES.get(normalized)
        if resolved is not None:
            return resolved
    return _UF_REGIAO.get(uf.strip().upper()) if uf else None


def refresh_dim_ente(session: Session, cod_ibge: str) -> DimEnte | None:
    """Conforma ``dim_ente`` a partir do silver (SICONFI + IBGE mais recente)."""
    existing = repository.get_dim_ente(session, cod_ibge)
    silver = repository.get_silver_ente(session, cod_ibge)
    pop = repository.latest_ibge_populacao(session, cod_ibge)
    pib = repository.latest_ibge_pib(session, cod_ibge)

    valores: dict[str, Any] = {}
    if silver is not None:
        if silver.nome is not None:
            valores["nome"] = silver.nome
        if silver.uf is not None:
            valores["uf"] = silver.uf
        regiao = _normalizar_regiao(silver.regiao, silver.uf)
        if regiao is not None:
            valores["regiao"] = regiao
        esfera = _normalizar_esfera(silver.esfera)
        if esfera is not None:
            valores["esfera"] = esfera
        if silver.populacao is not None and pop is None:
            valores["populacao"] = silver.populacao
            valores["pop_source_ref"] = SourceRef(
                relatorio="SICONFI-ENTES",
                anexo="Cadastro de entes",
                versao_entrega=silver.versao_entrega,
            ).model_dump(mode="json")
    if pop is not None:
        valores["populacao"], valores["pop_ano_ref"] = pop[0], pop[1]
        valores["pop_source_ref"] = SourceRef(
            relatorio="IBGE-POP",
            anexo="Agregado 6579 - variavel 9324",
            periodo=str(pop[1]),
            versao_entrega=pop[2],
        ).model_dump(mode="json")
    if pib is not None:
        valores["pib"], valores["pib_ano_ref"] = pib[0], pib[1]
        valores["pib_source_ref"] = SourceRef(
            relatorio="IBGE-PIB",
            anexo="Agregado 5938 - variavel 37 (mil reais)",
            periodo=str(pib[1]),
            versao_entrega=pib[2],
        ).model_dump(mode="json")

    # Um dim_ente sem fontes silver (comum em fixtures e durante backfills parciais)
    # não deve ser sobrescrito nem provocar um UPSERT com SET vazio.
    if not valores:
        return existing
    # GETs analíticos consultam este conformador com frequência. Evite regravar a
    # dimensão (e adquirir locks/WAL) quando as fontes ainda produzem os mesmos
    # atributos já conformados.
    if existing is not None and all(
        getattr(existing, atributo) == valor for atributo, valor in valores.items()
    ):
        return existing
    repository.upsert_dim_ente(session, cod_ibge=cod_ibge, valores=valores)
    # O UPSERT Core não atualiza automaticamente uma instância ORM já presente no
    # identity map. Recarregar evita que a primeira requisição após o ETL ainda veja
    # população/PIB/região antigos e escolha uma coorte incorreta.
    if existing is not None:
        session.refresh(existing)
        return existing
    return repository.get_dim_ente(session, cod_ibge)


def get_ente(session: Session, cod_ibge: str) -> EnteOut:
    """Retorna o ente conformado (refresh-on-read a partir do silver)."""
    ente = refresh_dim_ente(session, cod_ibge)
    if ente is None:
        raise AppError(
            status=404, title="Ente não encontrado",
            detail=f"Sem cadastro para o IBGE {cod_ibge} (ingerir siconfi_entes).",
        )
    return EnteOut.model_validate(ente)


def buscar_entes(
    session: Session,
    *,
    cods_escopo: set[str],
    q: str | None = None,
    uf: str | None = None,
    limit: int = 20,
) -> EntesBuscaResponse:
    """Busca de entes **dentro do escopo** (seletor de ente e ⌘K).

    Cada linha informa se o ente tem dado (``tem_dado``) e qual o período mais recente,
    para que o seletor não ofereça um ente que abriria uma tela vazia.
    """
    rows, total = repository.buscar_entes(
        session, cods_escopo=cods_escopo, q=q, uf=uf, limit=limit
    )
    itens: list[EnteBusca] = []
    for ente in rows:
        entregas = repository.periodos_com_dado(
            session, cod_ibge=ente.cod_ibge, relatorio=RELATORIO_PADRAO
        )
        recente = periodo_util.mais_recente([e.periodo for e in entregas])
        itens.append(
            EnteBusca(
                cod_ibge=ente.cod_ibge,
                nome=ente.nome,
                uf=ente.uf,
                esfera=ente.esfera,
                populacao=ente.populacao,
                tem_dado=recente is not None,
                periodo_mais_recente=recente,
            )
        )
    return EntesBuscaResponse(data=itens, total=total, escopo_total=len(cods_escopo))


def periodos_do_ente(
    session: Session, cod_ibge: str, *, relatorio: str | None = None
) -> PeriodosResponse:
    """Períodos com dado do ente. O ``default`` é o mais recente — nunca um período fixo."""
    entregas = repository.periodos_com_dado(session, cod_ibge=cod_ibge, relatorio=relatorio)
    itens = [
        PeriodoDisponivel(
            periodo=e.periodo,
            relatorio=e.relatorio,
            versao_entrega=e.versao_entrega,
            vigente=e.vigente,
        )
        for e in entregas
    ]
    alvo = relatorio or RELATORIO_PADRAO
    default = periodo_util.mais_recente([i.periodo for i in itens if i.relatorio == alvo])
    if default is None:
        default = periodo_util.mais_recente([i.periodo for i in itens])
    return PeriodosResponse(
        cod_ibge=cod_ibge,
        relatorio=relatorio,
        default=default,
        periodos=sorted(itens, key=lambda i: (i.relatorio, periodo_util.ordenar_chave(i.periodo))),
    )


def periodo_breadcrumb(session: Session, periodo: str) -> list[DrillNodeRef]:
    """Ancestrais do período em ``dim_periodo`` (drill temporal UP), raiz → pai."""
    nodes = [
        HierarchyNode(
            codigo=p.codigo, descricao=p.descricao, parent_codigo=p.parent_codigo,
            nivel=p.nivel, path=p.path,
        )
        for p in repository.list_periodos(session)
    ]
    return [
        DrillNodeRef(codigo=n.codigo, descricao=n.descricao, nivel=n.nivel)
        for n in breadcrumb_of(nodes, periodo)
    ]


def periodos_drill(session: Session, node: str | None) -> DrillEnvelope:
    """Drill temporal (§6.1) sobre ``dim_periodo``."""
    nodes = [
        HierarchyNode(
            codigo=p.codigo,
            descricao=p.descricao,
            parent_codigo=p.parent_codigo,
            nivel=p.nivel,
            path=p.path,
            measures={
                k: v
                for k, v in (
                    ("ano", p.ano), ("mes", p.mes),
                    ("bimestre", p.bimestre), ("quadrimestre", p.quadrimestre),
                )
                if v is not None
            },
        )
        for p in repository.list_periodos(session)
    ]
    if node is not None and not any(n.codigo == node for n in nodes):
        raise AppError(status=404, title="Período inexistente", detail=f"Período '{node}'.")
    return build_drill_envelope(nodes, node)
