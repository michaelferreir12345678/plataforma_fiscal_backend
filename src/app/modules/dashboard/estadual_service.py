"""Regras da Visão Estadual & Consolidação Territorial da UF (Módulo 2, Sprint 23).

Entrega **dois conceitos que a auditoria pediu para nunca confundir**:

- **Ente estadual** — os dados do próprio Governo do Estado (código IBGE de 2 dígitos),
  servidos pelos endpoints de ente (``/entes/{uf}``). Aqui só aparecem **referenciados**.
- **Consolidado territorial** — o agregado dos **municípios** da UF. A regra invariante é
  ``valor_pct = Σnumerador / Σdenominador`` (ex.: Σdespesa de pessoal / ΣRCL) — **nunca** a
  média dos percentuais municipais.

A cobertura é dado (n/total, ausentes, períodos mistos), e o escopo (§6.4) separa o que cada
conta enxerga: os **agregados** territoriais (número consolidado, distribuição) são públicos
para quem tem a UF no escopo; os **valores por ente nomeados** (ranking, mapa) respeitam a
carteira do usuário — consultoria vê só a sua; a conta estadual vê todos os municípios da UF.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError, ScopeForbiddenError
from app.modules.catalog.models import DimLimiteLegal
from app.modules.dashboard import estadual_repository as repo
from app.modules.dashboard.estadual_schemas import (
    ArvoreUfResponse,
    ConsolidadoUfResponse,
    DistribuicaoUfResponse,
    EnteRef,
    HistogramaBin,
    IndicadorConsolidado,
    MapaUfEnte,
    MapaUfResponse,
    RankingItem,
    RankingUfResponse,
)
from app.modules.dashboard.service import COR_POR_FAIXA
from app.modules.indicators.limites import LimiteLegal, classificar_faixa
from app.shared import scope
from app.shared.envelope import DrillEnvelope
from app.shared.hierarchy import HierarchyNode, build_drill_envelope
from app.shared.scope import EnteNaoLicenciadoError
from app.shared.source_ref import SourceRef

VERSAO_CALCULO = "v1"
ESFERA_MUNICIPAL = "municipal"
_RANKING_CACHE_TTL_SECONDS = 30.0
_RANKING_CACHE_MAX_ENTRIES = 32
_RANKING_CACHE_MIN_ENTES = 100

# --- UF: sigla ↔ código IBGE (2 dígitos) ↔ nome. Dado de referência estável. ---
_UFS: tuple[tuple[str, str, str], ...] = (
    ("11", "RO", "Rondônia"),
    ("12", "AC", "Acre"),
    ("13", "AM", "Amazonas"),
    ("14", "RR", "Roraima"),
    ("15", "PA", "Pará"),
    ("16", "AP", "Amapá"),
    ("17", "TO", "Tocantins"),
    ("21", "MA", "Maranhão"),
    ("22", "PI", "Piauí"),
    ("23", "CE", "Ceará"),
    ("24", "RN", "Rio Grande do Norte"),
    ("25", "PB", "Paraíba"),
    ("26", "PE", "Pernambuco"),
    ("27", "AL", "Alagoas"),
    ("28", "SE", "Sergipe"),
    ("29", "BA", "Bahia"),
    ("31", "MG", "Minas Gerais"),
    ("32", "ES", "Espírito Santo"),
    ("33", "RJ", "Rio de Janeiro"),
    ("35", "SP", "São Paulo"),
    ("41", "PR", "Paraná"),
    ("42", "SC", "Santa Catarina"),
    ("43", "RS", "Rio Grande do Sul"),
    ("50", "MS", "Mato Grosso do Sul"),
    ("51", "MT", "Mato Grosso"),
    ("52", "GO", "Goiás"),
    ("53", "DF", "Distrito Federal"),
)
_SIGLA_PARA_COD = {s: c for c, s, _ in _UFS}
_COD_PARA_NOME = {c: n for c, _, n in _UFS}
_COD_PARA_SIGLA = {c: s for c, s, _ in _UFS}


@dataclass(frozen=True)
class _IndicadorSpec:
    """Metadado de um indicador consolidável (v1: aditivos seguros)."""

    codigo: str
    rotulo: str
    tipo: str  # "ratio" | "absoluto"
    unidade: str  # "PCT_RCL" | "BRL"
    fonte: str  # rótulo humano da origem
    relatorio: str
    sentido: str | None = None  # teto | piso (só para ratio)


_RankingIdentity = tuple[tuple[str, str, str | None], ...]
_RankingCacheKey = tuple[
    str,
    str,
    str,
    str | None,
    str | None,
    str,
    tuple[str, ...],
    _RankingIdentity,
]


@dataclass(frozen=True)
class _CachedRanking:
    stored_at: float
    response: RankingUfResponse


_ranking_cache: OrderedDict[_RankingCacheKey, _CachedRanking] = OrderedDict()
_ranking_cache_lock = threading.Lock()


def _ranking_cache_get(key: _RankingCacheKey) -> RankingUfResponse | None:
    now = time.monotonic()
    with _ranking_cache_lock:
        cached = _ranking_cache.get(key)
        if cached is None:
            return None
        if now - cached.stored_at > _RANKING_CACHE_TTL_SECONDS:
            del _ranking_cache[key]
            return None
        _ranking_cache.move_to_end(key)
        return cached.response


def _ranking_cache_put(key: _RankingCacheKey, response: RankingUfResponse) -> None:
    with _ranking_cache_lock:
        _ranking_cache[key] = _CachedRanking(
            stored_at=time.monotonic(),
            response=response,
        )
        _ranking_cache.move_to_end(key)
        while len(_ranking_cache) > _RANKING_CACHE_MAX_ENTRIES:
            _ranking_cache.popitem(last=False)


# v1 — indicadores aditivos seguros. Exclusão de dupla contagem intra-governamental:
# o consolidado é **só municípios** (o ente estadual fica de fora), então a cota-parte
# estado→município (RREO A1) nunca é somada duas vezes. Ver ``observacao_consolidado``.
INDICADORES_V1: tuple[_IndicadorSpec, ...] = (
    _IndicadorSpec("rcl", "Receita Corrente Líquida", "absoluto", "BRL", "RREO Anexo 03", "RREO"),
    _IndicadorSpec(
        "pessoal_executivo",
        "Despesa de Pessoal (Executivo)",
        "ratio",
        "PCT_RCL",
        "RGF Anexo 01",
        "RGF",
        sentido="teto",
    ),
    _IndicadorSpec(
        "divida_consolidada_liquida",
        "Dívida Consolidada Líquida",
        "ratio",
        "PCT_RCL",
        "RGF Anexo 02",
        "RGF",
        sentido="teto",
    ),
    _IndicadorSpec(
        "disponibilidade",
        "Disponibilidade de Caixa (líquida)",
        "absoluto",
        "BRL",
        "RGF Anexo 05",
        "RGF",
    ),
)
_SPEC = {s.codigo: s for s in INDICADORES_V1}


# --- Normalização de UF e escopo ---
def normalizar_uf(uf: str) -> str:
    """Aceita sigla ('CE') ou código ('23'); devolve o código IBGE de 2 dígitos (prefixo)."""
    u = (uf or "").strip().upper()
    if u in _SIGLA_PARA_COD:
        return _SIGLA_PARA_COD[u]
    if len(u) == 2 and u.isdigit():
        return u
    raise AppError(status=404, title="UF inválida", detail=f"UF '{uf}' não reconhecida.")


def uf_nome(uf_prefixo: str) -> str | None:
    return _COD_PARA_NOME.get(uf_prefixo)


def entes_no_escopo_uf(session: Session, principal: Principal, uf_prefixo: str) -> set[str]:
    """Municípios da UF que estão no escopo do usuário (carteira ∩ UF)."""
    scope_cods = scope.carteira_scope_ibges(session, principal)
    return set(repo.list_ibges_por_prefixo(session, uf_prefixo, cods=scope_cods))


def assert_uf_in_scope(session: Session, principal: Principal, uf_prefixo: str) -> None:
    """403 se o usuário não tem nenhum ente da UF no escopo (nem é conta estadual da UF)."""
    if principal.org_id is None:
        raise ScopeForbiddenError(uf_prefixo)
    if entes_no_escopo_uf(session, principal, uf_prefixo):
        return
    # Conta estadual cuja UF monitorada é esta, mesmo sem municípios carregados ainda.
    if scope._is_estado(session, principal.org_id) and uf_prefixo in scope._estado_prefixes(
        session, principal.org_id
    ):
        return
    raise ScopeForbiddenError(f"UF {uf_prefixo}")


# --- Períodos / limites ---
def rgf_periodo_de(periodo_rreo: str) -> str | None:
    """Mapeia o período RREO (bimestral) ao RGF (quadrimestral): Q = teto(bimestre/2).

    Público (sem ``_``) porque a Sprint A6 (A18) passou a reusá-lo em
    ``cockpit_service.py`` para ancorar o explicador de pessoal no RGF do **mesmo
    ciclo** do período RREO selecionado — antes ele sempre usava o RGF mais recente do
    ente, ignorando o período pedido.
    """
    try:
        ano_s, bim_s = periodo_rreo.split("-B")
        return f"{ano_s}-Q{math.ceil(int(bim_s) / 2)}"
    except (ValueError, AttributeError):
        return None


def _ano_de(periodo: str) -> int | None:
    try:
        return int(periodo.split("-")[0])
    except (ValueError, IndexError):
        return None


def _limite_municipal(session: Session, indicador: str) -> LimiteLegal | None:
    """O teto/piso municipal do indicador (o consolidado é de municípios)."""
    row = session.scalar(
        select(DimLimiteLegal).where(
            DimLimiteLegal.indicador == indicador,
            DimLimiteLegal.esfera == ESFERA_MUNICIPAL,
        )
    )
    if row is None:
        return None
    return LimiteLegal(
        indicador=row.indicador,
        esfera=row.esfera,
        poder=row.poder,
        sentido=row.sentido,
        teto_pct=row.teto_pct,
        alerta_pct=row.alerta_pct,
        prudencial_pct=row.prudencial_pct,
    )


def _faixa_cor(valor_pct: Decimal | None, limite: LimiteLegal | None) -> tuple[str | None, str]:
    if valor_pct is None or limite is None:
        return None, "cinza"
    faixa = classificar_faixa(valor_pct, limite)
    return faixa, COR_POR_FAIXA.get(faixa, "cinza")


# --- Valores por ente para um indicador (numerador; e denominador para razão) ---
def _valores_por_ente(
    session: Session, *, cods: Sequence[str], periodo: str, spec: _IndicadorSpec
) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    """Devolve (numerador_por_ente, denominador_por_ente). Denominador vazio p/ absoluto."""
    if spec.codigo == "rcl":
        num = repo.rcl_uf(session, cods=cods, periodo=periodo)
        return num, {}
    if spec.codigo == "disponibilidade":
        rgf = rgf_periodo_de(periodo)
        num = repo.disponibilidade_uf(session, cods=cods, periodo_rgf=rgf) if rgf else {}
        return num, {}
    # ratio (pessoal/dívida): numerador = valor_rs; denominador = RCL dos mesmos entes.
    vals = repo.mart_valores_uf(session, cods=cods, periodo=periodo, indicador=spec.codigo)
    num = {c: rs for c, (rs, _pct) in vals.items() if rs is not None}
    rcl = repo.rcl_uf(session, cods=cods, periodo=periodo)
    den = {c: rcl[c] for c in num if c in rcl}
    return num, den


def _periodos_no_ano(
    session: Session, *, cods: Sequence[str], spec: _IndicadorSpec, periodo: str
) -> list[str]:
    ano = _ano_de(periodo)
    if ano is None:
        return []
    if spec.codigo == "rcl":
        return repo.periodos_rcl_no_ano(session, cods=cods, ano=ano)
    if spec.codigo == "disponibilidade":
        return repo.periodos_disp_no_ano(session, cods=cods, ano=ano)
    return repo.periodos_mart_no_ano(session, cods=cods, indicador=spec.codigo, ano=ano)


def _fonte_periodo(spec: _IndicadorSpec, periodo: str) -> str:
    """O período da fonte de fato usado (RGF é quadrimestral)."""
    if spec.relatorio == "RGF":
        return rgf_periodo_de(periodo) or periodo
    return periodo


def _consolidar_indicador(
    session: Session,
    *,
    uf_prefixo: str,
    periodo: str,
    spec: _IndicadorSpec,
    municipios: Sequence[str],
) -> dict:
    """Consolida um indicador da UF: Σnum/Σden + cobertura. Retorna o dict do mart."""
    num_por_ente, den_por_ente = _valores_por_ente(
        session, cods=municipios, periodo=periodo, spec=spec
    )
    contribuintes = sorted(num_por_ente)
    ausentes = [c for c in municipios if c not in num_por_ente]

    numerador = sum(num_por_ente.values(), Decimal(0)) if num_por_ente else None
    denominador: Decimal | None = None
    valor_pct: Decimal | None = None
    if spec.tipo == "ratio":
        denominador = sum(den_por_ente.values(), Decimal(0)) if den_por_ente else None
        if numerador is not None and denominador and denominador > 0:
            valor_pct = (numerador / denominador) * Decimal(100)

    n_total = len(municipios)
    n_com = len(contribuintes)
    cobertura = (Decimal(n_com) / Decimal(n_total) * Decimal(100)) if n_total else None
    periodos = _periodos_no_ano(session, cods=municipios, spec=spec, periodo=periodo)

    return {
        "uf": uf_prefixo,
        "periodo": periodo,
        "indicador": spec.codigo,
        "versao_calculo": VERSAO_CALCULO,
        "numerador": numerador,
        "denominador": denominador,
        "valor_pct": valor_pct,
        "n_entes_total": n_total,
        "n_entes_com_dado": n_com,
        "cobertura_pct": cobertura,
        "entes_ausentes": ausentes,
        "periodos_mistos": len(periodos) > 1,
    }


def refresh_consolidado(session: Session, uf: str, periodo: str) -> int:
    """Materializa ``mart_consolidado_uf`` para todos os indicadores v1 da UF/período."""
    uf_prefixo = normalizar_uf(uf)
    municipios = [e.cod_ibge for e in repo.list_municipios_uf(session, uf_prefixo)]
    n = 0
    for spec in INDICADORES_V1:
        valores = _consolidar_indicador(
            session, uf_prefixo=uf_prefixo, periodo=periodo, spec=spec, municipios=municipios
        )
        repo.upsert_consolidado(session, valores)
        n += 1
    return n


def _source_ref(spec: _IndicadorSpec, periodo: str) -> SourceRef:
    return SourceRef(relatorio=spec.relatorio, periodo=_fonte_periodo(spec, periodo))


def _indicador_out(
    session: Session, mart: dict, spec: _IndicadorSpec, periodo: str
) -> IndicadorConsolidado:
    limite = _limite_municipal(session, spec.codigo) if spec.tipo == "ratio" else None
    faixa, cor = _faixa_cor(mart["valor_pct"], limite)
    return IndicadorConsolidado(
        indicador=spec.codigo,
        rotulo=spec.rotulo,
        tipo=spec.tipo,
        unidade=spec.unidade,
        numerador=mart["numerador"],
        denominador=mart["denominador"],
        valor_pct=mart["valor_pct"],
        teto_pct=limite.teto_pct if limite else None,
        sentido=spec.sentido,
        faixa=faixa,
        cor=cor,
        n_entes_total=mart["n_entes_total"],
        n_entes_com_dado=mart["n_entes_com_dado"],
        cobertura_pct=mart["cobertura_pct"],
        entes_ausentes=list(mart["entes_ausentes"]),
        periodos_mistos=mart["periodos_mistos"],
        versao_calculo=VERSAO_CALCULO,
        source_ref=_source_ref(spec, periodo),
    )


_OBSERVACAO = (
    "Consolidado dos municípios da UF por Σnumerador/Σdenominador (nunca média de %). "
    "O ente estadual não entra no consolidado; por isso a cota-parte estado→município "
    "(RREO Anexo 01) não é contada em dobro. v1: indicadores aditivos seguros."
)


def _ref_ente_estadual(
    session: Session, principal: Principal, ente: Any | None
) -> EnteRef | None:
    """Referência ao ente estadual **com** o veredito de acesso já resolvido.

    Ver o consolidado da UF não implica poder abrir o cockpit do Governo do Estado: são
    escopos diferentes (agregado × nominal, §6.4). Resolver aqui evita oferecer um botão
    que responderia 403 — e evita que o 403 chegue à tela como "ente sem período".
    """
    if ente is None:
        return None
    try:
        scope.assert_ente_in_scope(session, principal, ente.cod_ibge)
    except EnteNaoLicenciadoError:
        return EnteRef(
            cod_ibge=ente.cod_ibge,
            nome=ente.nome,
            acessivel=False,
            motivo_indisponivel=(
                "O ente estadual não está coberto pela licença vigente desta organização."
            ),
        )
    except ScopeForbiddenError:
        return EnteRef(
            cod_ibge=ente.cod_ibge,
            nome=ente.nome,
            acessivel=False,
            motivo_indisponivel=(
                "O ente estadual não está na carteira/escopo deste usuário. O consolidado "
                "dos municípios continua disponível."
            ),
        )
    return EnteRef(cod_ibge=ente.cod_ibge, nome=ente.nome)


def build_consolidado(
    session: Session, principal: Principal, uf: str, periodo: str
) -> ConsolidadoUfResponse:
    """Consolidado territorial (Σnum/Σden) + cobertura honesta. Lê o mart; materializa lazy."""
    uf_prefixo = normalizar_uf(uf)
    assert_uf_in_scope(session, principal, uf_prefixo)

    municipios = [e.cod_ibge for e in repo.list_municipios_uf(session, uf_prefixo)]

    rows = {
        r.indicador: r
        for r in repo.get_consolidado(session, uf_prefixo=uf_prefixo, periodo=periodo)
    }
    if not rows:  # materialização lazy na primeira leitura do período
        refresh_consolidado(session, uf_prefixo, periodo)
        session.flush()
        rows = {
            r.indicador: r
            for r in repo.get_consolidado(session, uf_prefixo=uf_prefixo, periodo=periodo)
        }

    indicadores: list[IndicadorConsolidado] = []
    for spec in INDICADORES_V1:
        row = rows.get(spec.codigo)
        mart = (
            {
                "numerador": row.numerador,
                "denominador": row.denominador,
                "valor_pct": row.valor_pct,
                "n_entes_total": row.n_entes_total,
                "n_entes_com_dado": row.n_entes_com_dado,
                "cobertura_pct": row.cobertura_pct,
                "entes_ausentes": row.entes_ausentes,
                "periodos_mistos": row.periodos_mistos,
            }
            if row is not None
            else _consolidar_indicador(
                session, uf_prefixo=uf_prefixo, periodo=periodo, spec=spec, municipios=municipios
            )
        )
        indicadores.append(_indicador_out(session, mart, spec, periodo))

    ente = repo.get_ente_estadual(session, uf_prefixo)
    # Cobertura global do painel: a do RREO (RCL), a fonte mais densa.
    ref = next((i for i in indicadores if i.indicador == "rcl"), None)
    return ConsolidadoUfResponse(
        uf=uf_prefixo,
        uf_nome=uf_nome(uf_prefixo),
        periodo=periodo,
        ente_estadual=_ref_ente_estadual(session, principal, ente),
        n_municipios=len(municipios),
        n_municipios_com_dado=ref.n_entes_com_dado if ref else 0,
        cobertura_pct=ref.cobertura_pct if ref else None,
        indicadores=indicadores,
        observacao=_OBSERVACAO,
        source_ref=SourceRef(relatorio="RREO", periodo=periodo),
    )


# --- Porte / região (para filtros e drill) ---
def _porte(populacao: int | None) -> str | None:
    if populacao is None:
        return None
    if populacao < 50_000:
        return "pequeno"
    if populacao < 200_000:
        return "medio"
    if populacao < 1_000_000:
        return "grande"
    return "metropole"


_PORTE_ROTULO = {
    "pequeno": "Pequeno (< 50 mil)",
    "medio": "Médio (50–200 mil)",
    "grande": "Grande (200 mil–1 mi)",
    "metropole": "Metrópole (> 1 mi)",
}


def _regiao_por_ente(session: Session, uf_prefixo: str) -> dict[str, tuple[str, str]]:
    """``{cod_ibge: (regiao_codigo, regiao_nome)}`` a partir de ``dim_regiao_uf``."""
    mapa: dict[str, tuple[str, str]] = {}
    for reg in repo.list_regioes(session, uf_prefixo):
        for cod in reg.municipios:
            mapa[cod] = (reg.regiao_codigo, reg.nome)
    return mapa


# --- Ranking municipal (valores nomeados: respeita o escopo) ---
def _valor_ranking(
    spec: _IndicadorSpec, num: Decimal | None, den: Decimal | None
) -> tuple[Decimal | None, Decimal | None]:
    """(valor_pct, valor_rs) do ente para o ranking."""
    if spec.tipo == "ratio":
        pct = (num / den * Decimal(100)) if (num is not None and den and den > 0) else None
        return pct, num
    return None, num


def build_ranking(
    session: Session,
    principal: Principal,
    uf: str,
    *,
    indicador: str,
    periodo: str,
    regiao: str | None = None,
    porte: str | None = None,
    ordenar: str = "valor",
) -> RankingUfResponse:
    uf_prefixo = normalizar_uf(uf)
    assert_uf_in_scope(session, principal, uf_prefixo)
    spec = _SPEC.get(indicador)
    if spec is None:
        raise AppError(status=404, title="Indicador inválido", detail=f"'{indicador}'.")

    cods = sorted(entes_no_escopo_uf(session, principal, uf_prefixo))
    cache_key: _RankingCacheKey | None = None
    if len(cods) >= _RANKING_CACHE_MIN_ENTES:
        identity_relatorio = "RGF" if spec.codigo == "disponibilidade" else "RREO"
        identity_periodo = (
            rgf_periodo_de(periodo) or periodo
            if identity_relatorio == "RGF"
            else periodo
        )
        identity = repo.entrega_vigente_identity(
            session,
            cods=cods,
            relatorio=identity_relatorio,
            periodo=identity_periodo,
        )
        cache_key = (
            uf_prefixo,
            spec.codigo,
            periodo,
            regiao,
            porte,
            ordenar,
            tuple(cods),
            identity,
        )
        cached = _ranking_cache_get(cache_key)
        if cached is not None:
            return cached

    dim = {e.cod_ibge: e for e in repo.list_municipios_uf(session, uf_prefixo)}
    regioes = _regiao_por_ente(session, uf_prefixo)
    limite = _limite_municipal(session, spec.codigo) if spec.tipo == "ratio" else None

    num_por_ente, den_por_ente = _valores_por_ente(session, cods=cods, periodo=periodo, spec=spec)

    linhas: list[RankingItem] = []
    for cod in cods:
        d = dim.get(cod)
        reg = regioes.get(cod)
        pop = d.populacao if d else None
        pct, rs = _valor_ranking(spec, num_por_ente.get(cod), den_por_ente.get(cod))
        valor_faixa = pct if spec.tipo == "ratio" else None
        faixa, cor = _faixa_cor(valor_faixa, limite)
        linhas.append(
            RankingItem(
                cod_ibge=cod,
                nome=d.nome if d else None,
                regiao=reg[1] if reg else None,
                porte=_porte(pop),
                populacao=pop,
                valor_pct=pct,
                valor_rs=rs,
                faixa=faixa,
                cor=cor,
                posicao=0,
                percentil=None,
                destaque=faixa in ("prudencial", "excedido"),
            )
        )

    if regiao is not None:
        linhas = [r for r in linhas if (r.regiao or "").lower() == regiao.lower()]
    if porte is not None:
        linhas = [r for r in linhas if r.porte == porte]

    com_valor = [
        r for r in linhas if (r.valor_pct if spec.tipo == "ratio" else r.valor_rs) is not None
    ]

    # Ordenação: por padrão, o "pior" primeiro (maior % para teto; maior R$ para absoluto).
    def chave_valor(r: RankingItem) -> Decimal:
        v = r.valor_pct if spec.tipo == "ratio" else r.valor_rs
        return v if v is not None else Decimal(-1)

    if ordenar == "nome":
        linhas.sort(key=lambda r: ((r.nome or "").lower(), r.cod_ibge))
    elif ordenar == "codigo":
        linhas.sort(key=lambda r: r.cod_ibge)
    else:  # valor
        linhas.sort(key=lambda r: (chave_valor(r), r.cod_ibge), reverse=True)

    # Percentil e posição entre os que têm valor (posição 1 = maior valor).
    ordenados_valor = sorted(com_valor, key=lambda r: chave_valor(r), reverse=True)
    n = len(ordenados_valor)
    for i, r in enumerate(ordenados_valor):
        r.posicao = i + 1
        r.percentil = Decimal(round((n - i) / n * 100, 1)) if n > 0 else None

    response = RankingUfResponse(
        uf=uf_prefixo,
        periodo=periodo,
        indicador=spec.codigo,
        rotulo=spec.rotulo,
        sentido=spec.sentido or "neutro",
        unidade=spec.unidade,
        ordenar=ordenar,
        n_total=len(linhas),
        n_com_valor=len(com_valor),
        itens=linhas,
        source_ref=_source_ref(spec, periodo),
    )
    if cache_key is not None:
        _ranking_cache_put(cache_key, response)
    return response


# --- Distribuição / concentração (estatística anônima: territorial) ---
def _percentil(valores: list[Decimal], p: float) -> Decimal | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    if len(ordenados) == 1:
        return ordenados[0]
    k = (len(ordenados) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return ordenados[int(k)]
    frac = Decimal(str(k - lo))
    return ordenados[lo] + (ordenados[hi] - ordenados[lo]) * frac


def build_distribuicao(
    session: Session,
    principal: Principal,
    uf: str,
    *,
    indicador: str,
    periodo: str,
) -> DistribuicaoUfResponse:
    uf_prefixo = normalizar_uf(uf)
    assert_uf_in_scope(session, principal, uf_prefixo)
    spec = _SPEC.get(indicador)
    if spec is None:
        raise AppError(status=404, title="Indicador inválido", detail=f"'{indicador}'.")

    # Distribuição é territorial (todos os municípios da UF) — resposta a "sou exceção?".
    municipios = [e.cod_ibge for e in repo.list_municipios_uf(session, uf_prefixo)]
    num_por_ente, den_por_ente = _valores_por_ente(
        session, cods=municipios, periodo=periodo, spec=spec
    )
    valores: list[Decimal] = []
    for cod, num in num_por_ente.items():
        pct, rs = _valor_ranking(spec, num, den_por_ente.get(cod))
        v = pct if spec.tipo == "ratio" else rs
        if v is not None:
            valores.append(v)

    # Concentração usa a magnitude absoluta (numerador): "os N maiores têm X% do total".
    magnitudes = sorted((n for n in num_por_ente.values() if n is not None), reverse=True)
    total_mag = sum(magnitudes, Decimal(0))

    def top_share(k: int) -> Decimal | None:
        if not magnitudes or total_mag <= 0:
            return None
        return sum(magnitudes[:k], Decimal(0)) / total_mag * Decimal(100)

    histograma: list[HistogramaBin] = []
    if valores:
        lo, hi = min(valores), max(valores)
        if hi > lo:
            n_bins = min(10, len(valores))
            largura = (hi - lo) / Decimal(n_bins)
            for b in range(n_bins):
                inf = lo + largura * b
                sup = hi if b == n_bins - 1 else lo + largura * (b + 1)
                cont = sum(
                    1 for v in valores if (v >= inf and (v < sup or (b == n_bins - 1 and v <= sup)))
                )
                histograma.append(
                    HistogramaBin(faixa_inferior=inf, faixa_superior=sup, contagem=cont)
                )
        else:
            histograma.append(
                HistogramaBin(faixa_inferior=lo, faixa_superior=hi, contagem=len(valores))
            )

    return DistribuicaoUfResponse(
        uf=uf_prefixo,
        periodo=periodo,
        indicador=spec.codigo,
        rotulo=spec.rotulo,
        unidade=spec.unidade,
        n_com_valor=len(valores),
        minimo=min(valores) if valores else None,
        p10=_percentil(valores, 0.10),
        p25=_percentil(valores, 0.25),
        mediana=_percentil(valores, 0.50),
        p75=_percentil(valores, 0.75),
        p90=_percentil(valores, 0.90),
        maximo=max(valores) if valores else None,
        histograma=histograma,
        concentracao_top5_pct=top_share(5),
        concentracao_top10_pct=top_share(10),
        total=total_mag if magnitudes else None,
        source_ref=_source_ref(spec, periodo),
    )


# --- Mapa coroplético (todos os municípios; valores só no escopo) ---
def build_mapa(
    session: Session,
    principal: Principal,
    uf: str,
    *,
    indicador: str,
    periodo: str,
) -> MapaUfResponse:
    uf_prefixo = normalizar_uf(uf)
    assert_uf_in_scope(session, principal, uf_prefixo)
    spec = _SPEC.get(indicador)
    if spec is None:
        raise AppError(status=404, title="Indicador inválido", detail=f"'{indicador}'.")

    linhas_municipios = repo.list_municipios_uf(session, uf_prefixo)
    municipios = [e.cod_ibge for e in linhas_municipios]
    nomes = {e.cod_ibge: e.nome for e in linhas_municipios if e.nome}
    no_escopo = entes_no_escopo_uf(session, principal, uf_prefixo)
    limite = _limite_municipal(session, spec.codigo) if spec.tipo == "ratio" else None

    # Valores só para os entes no escopo (a cor por ente é dado nominal).
    num_por_ente, den_por_ente = _valores_por_ente(
        session, cods=sorted(no_escopo), periodo=periodo, spec=spec
    )

    entes: list[MapaUfEnte] = []
    for cod in municipios:
        dentro = cod in no_escopo
        pct, _rs = _valor_ranking(spec, num_por_ente.get(cod), den_por_ente.get(cod))
        valor_faixa = pct if spec.tipo == "ratio" else None
        faixa, cor = _faixa_cor(valor_faixa, limite) if dentro else (None, "cinza")
        entes.append(
            MapaUfEnte(
                cod_ibge=cod,
                nome=nomes.get(cod),
                valor_pct=pct if dentro else None,
                faixa=faixa,
                cor=cor,
                no_escopo=not dentro,
            )
        )

    legenda = {
        "normal": "verde",
        "alerta": "amarelo",
        "prudencial": "laranja",
        "excedido": "vermelho",
        "sem dado / fora do escopo": "cinza",
    }
    return MapaUfResponse(
        uf=uf_prefixo,
        periodo=periodo,
        indicador=spec.codigo,
        rotulo=spec.rotulo,
        legenda=legenda,
        malha_ref=f"/geo/malha/{uf_prefixo}",
        entes=entes,
        source_ref=_source_ref(spec, periodo),
    )


# --- Drill §6.1: UF → região/porte → município → cockpit ---
def build_arvore(
    session: Session,
    principal: Principal,
    uf: str,
    *,
    indicador: str,
    periodo: str,
    agrupar: str = "regiao",
    node: str | None = None,
) -> ArvoreUfResponse:
    uf_prefixo = normalizar_uf(uf)
    assert_uf_in_scope(session, principal, uf_prefixo)
    spec = _SPEC.get(indicador)
    if spec is None:
        raise AppError(status=404, title="Indicador inválido", detail=f"'{indicador}'.")
    if agrupar not in ("regiao", "porte"):
        raise AppError(status=422, title="Agrupamento inválido", detail="use regiao|porte.")

    municipios = repo.list_municipios_uf(session, uf_prefixo)
    cods = [e.cod_ibge for e in municipios]
    no_escopo = entes_no_escopo_uf(session, principal, uf_prefixo)
    dim = {e.cod_ibge: e for e in municipios}
    regioes = _regiao_por_ente(session, uf_prefixo)
    limite = _limite_municipal(session, spec.codigo) if spec.tipo == "ratio" else None

    num_por_ente, den_por_ente = _valores_por_ente(session, cods=cods, periodo=periodo, spec=spec)

    # Agrupa os municípios em nós de 1º nível (região ou porte).
    grupos: dict[str, tuple[str, list[str]]] = {}
    for cod in cods:
        if agrupar == "regiao":
            reg = regioes.get(cod)
            chave, rotulo = (reg[0], reg[1]) if reg else ("sem_regiao", "Sem região")
        else:
            porte = _porte(dim[cod].populacao if cod in dim else None) or "sem_porte"
            chave, rotulo = porte, _PORTE_ROTULO.get(porte, "Sem porte")
        code = f"{agrupar}:{chave}"
        grupos.setdefault(code, (rotulo, []))[1].append(cod)

    def agg_grupo(membros: list[str]) -> dict:
        nums = {c: num_por_ente[c] for c in membros if c in num_por_ente}
        num = sum(nums.values(), Decimal(0)) if nums else None
        if spec.tipo == "ratio":
            den = sum((den_por_ente[c] for c in nums if c in den_por_ente), Decimal(0))
            pct = (num / den * Decimal(100)) if (num is not None and den > 0) else None
            return {"valor_pct": _f(pct), "n_entes": len(membros), "n_com_dado": len(nums)}
        return {"valor_rs": _f(num), "n_entes": len(membros), "n_com_dado": len(nums)}

    nodes: list[HierarchyNode] = []
    for code, (rotulo, membros) in sorted(grupos.items()):
        nodes.append(
            HierarchyNode(
                codigo=code,
                descricao=rotulo,
                parent_codigo=None,
                nivel=1,
                measures=agg_grupo(membros),
            )
        )
        for cod in sorted(membros):
            d = dim.get(cod)
            pct, rs = _valor_ranking(spec, num_por_ente.get(cod), den_por_ente.get(cod))
            valor_faixa = pct if spec.tipo == "ratio" else None
            faixa, cor = _faixa_cor(valor_faixa, limite)
            nodes.append(
                HierarchyNode(
                    codigo=cod,
                    descricao=(d.nome if d and d.nome else cod),
                    parent_codigo=code,
                    nivel=2,
                    measures={
                        "valor_pct": _f(pct),
                        "valor_rs": _f(rs),
                        "faixa": faixa,
                        "cor": cor,
                        "no_escopo": cod in no_escopo,
                        "cockpit": f"/entes/{cod}/cockpit",
                    },
                )
            )

    env: DrillEnvelope = build_drill_envelope(
        nodes, node, period=periodo, source_ref=_source_ref(spec, periodo)
    )
    return ArvoreUfResponse(
        **env.model_dump(), uf=uf_prefixo, indicador=spec.codigo, agrupar=agrupar
    )


def _f(v: Decimal | None) -> float | None:
    return float(v) if v is not None else None
