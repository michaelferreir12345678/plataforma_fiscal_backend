"""Cobertura de dados como DADO consultável (Sprint 21, instrumento da Sprint 20).

Materializa ``gold.mart_cobertura_fonte`` a partir de ``gold.dim_entrega`` (a verdade de
versão/retificação) e das tabelas silver (contagem de registros). Uma linha por
fonte×ente×período: o município que **não** entregou ao SICONFI simplesmente não tem
linha — a lacuna fica explícita como falha da fonte, não da plataforma.

Também calcula a **defasagem em períodos** pela cadência do próprio período (marcador
B/Q/S/M ou anual), e semeia ``gold.catalogo_fonte`` do ``FONTE_META``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import String, cast, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.ingestion.connectors.registry import (
    FONTE_META,
    FONTE_RELATORIO,
)  # noqa: F401  (FONTE_RELATORIO reexportado p/ conveniência de callers/testes)
from app.modules.ingestion.models import (
    BcbIndice,
    CatalogoFonte,
    DimEntrega,
    FndeFundebRepasse,
    IbgePib,
    IbgePopulacao,
    MartCoberturaFonte,
    RawPayload,
    SilverDca,
    SilverMsc,
    SilverRgf,
    SilverRreo,
    SiopeEducacao,
    SiopsSaude,
    TesouroCapag,
    TesouroFpm,
    TransferenciaGenerica,
)

_FREQ_POR_MARCA = {"M": 12, "B": 6, "Q": 3, "S": 2}
_BULK_UPSERT_SIZE = 500


def _freq_e_indice(periodo: str) -> tuple[int, int, int]:
    """(freq_no_ano, indice_1based, ano) do período canônico. Anual ⇒ freq=1, indice=1."""
    try:
        ano = int(periodo[:4])
    except (ValueError, IndexError):
        return 1, 1, 0
    if "-" not in periodo:
        return 1, 1, ano
    marca = periodo.split("-", 1)[1]
    tipo = marca[:1]
    try:
        num = int(marca[1:])
    except ValueError:
        return 1, 1, ano
    return _FREQ_POR_MARCA.get(tipo, 1), num, ano


def defasagem_periodos(periodo: str, hoje: date | None = None) -> int:
    """Quantos períodos (da granularidade do próprio período) ele está atrás do atual.

    O período mais recente já **fechado** tem defasagem 0; cada período fechado a mais
    entre ele e hoje soma 1. Independe da fonte — deriva do marcador do período.
    """
    hoje = hoje or date.today()
    freq, num, ano = _freq_e_indice(periodo)
    if ano == 0:
        return 0
    ordinal = ano * freq + (num - 1)
    meses_por = 12 // freq
    indice_atual = (hoje.month - 1) // meses_por  # 0-based do período em curso
    hoje_ordinal = hoje.year * freq + indice_atual
    # ``-1``: o período em curso ainda não fechou; o de referência é o anterior.
    return max(hoje_ordinal - 1 - ordinal, 0)


# --- catálogo de fontes (seed do FONTE_META) ---
def seed_catalogo(session: Session) -> int:
    """Semeia/atualiza ``gold.catalogo_fonte`` a partir do FONTE_META (idempotente)."""
    n = 0
    for fonte, meta in FONTE_META.items():
        valores = {
            "familia": meta.familia,
            "descricao": meta.descricao,
            "cadencia": meta.cadencia,
            "orgao": meta.orgao,
            "url_origem": meta.url_origem,
            "escopo": meta.escopo,
            "parser_versao": meta.parser_versao,
            "paginas_impactadas": list(meta.paginas_impactadas),
            "dependencias": list(meta.dependencias),
            "atualizado_em": datetime.now(),
        }
        stmt = pg_insert(CatalogoFonte).values(
            fonte=fonte,
            relatorio=FONTE_RELATORIO.get(fonte, fonte),
            **valores,
        )
        stmt = stmt.on_conflict_do_update(index_elements=["fonte"], set_=valores)
        session.execute(stmt)
        n += 1
    return n


# --- contadores silver por fonte (n_registros de uma versão de entrega) ---
@dataclass(frozen=True)
class _SilverConta:
    model: Any  # classe declarativa SQLAlchemy (silver)
    cod_ibge_col: Any
    periodo_expr: Any  # expressão SQL que reconstrói o período canônico


def _fpm_periodo() -> Any:
    return func.concat(
        cast(TesouroFpm.ano, String), "-M", func.lpad(cast(TesouroFpm.mes, String), 2, "0")
    )


def _fundeb_periodo() -> Any:
    return func.concat(
        cast(FndeFundebRepasse.ano, String),
        "-M",
        func.lpad(cast(FndeFundebRepasse.mes, String), 2, "0"),
    )


def _transf_periodo() -> Any:
    return func.concat(
        cast(TransferenciaGenerica.ano, String),
        "-M",
        func.lpad(cast(TransferenciaGenerica.mes, String), 2, "0"),
    )


def _siops_periodo() -> Any:
    return func.concat(cast(SiopsSaude.ano, String), "-B", cast(SiopsSaude.bimestre, String))


def _siope_periodo() -> Any:
    return func.concat(cast(SiopeEducacao.ano, String), "-B", cast(SiopeEducacao.bimestre, String))


# Fontes cuja cobertura por ente vem de contar linhas silver diretamente (não via
# dim_entrega, que para essas fontes registra a entrega nacional 'BR').
_SILVER_POR_ENTE: dict[str, _SilverConta] = {
    "tesouro_fpm": _SilverConta(TesouroFpm, TesouroFpm.cod_ibge, _fpm_periodo()),
    "fnde_fundeb_repasse": _SilverConta(
        FndeFundebRepasse, FndeFundebRepasse.cod_ibge, _fundeb_periodo()
    ),
    "transferencia_generica": _SilverConta(
        TransferenciaGenerica, TransferenciaGenerica.cod_ibge, _transf_periodo()
    ),
    "siops_saude": _SilverConta(SiopsSaude, SiopsSaude.cod_ibge, _siops_periodo()),
    "siope_educacao": _SilverConta(SiopeEducacao, SiopeEducacao.cod_ibge, _siope_periodo()),
    "tesouro_capag": _SilverConta(
        TesouroCapag, TesouroCapag.cod_ibge, cast(TesouroCapag.ano_ref, String)
    ),
}

# Contagem silver para as fontes por-ente registradas em dim_entrega (SICONFI + IBGE).
_SILVER_ENTREGA_MODEL: dict[str, tuple[Any, Any]] = {
    "siconfi_rreo": (SilverRreo, SilverRreo.periodo),
    "siconfi_rgf": (SilverRgf, SilverRgf.periodo),
    "siconfi_dca": (SilverDca, SilverDca.periodo),
    "siconfi_msc": (SilverMsc, SilverMsc.periodo),
    "ibge_populacao": (IbgePopulacao, cast(IbgePopulacao.ano_ref, String)),
    "ibge_pib": (IbgePib, cast(IbgePib.ano_ref, String)),
}


def _uf_de(cod_ibge: str) -> str | None:
    return cod_ibge[:2] if cod_ibge and cod_ibge[:2].isdigit() else None


_IngestionKey = tuple[str, str, str, str]


def _ingestion_times(
    session: Session,
    fontes: Iterable[str],
) -> dict[_IngestionKey, datetime]:
    """Prefetch do primeiro payload por chave, substituindo uma consulta por linha."""
    fontes = list(dict.fromkeys(fontes))
    if not fontes:
        return {}
    rows = session.execute(
        select(
            RawPayload.fonte,
            RawPayload.cod_ibge,
            RawPayload.periodo,
            RawPayload.versao,
            func.min(RawPayload.ingerido_em),
        )
        .where(RawPayload.fonte.in_(fontes))
        .group_by(
            RawPayload.fonte,
            RawPayload.cod_ibge,
            RawPayload.periodo,
            RawPayload.versao,
        )
    )
    return {
        (str(fonte), str(cod_ibge), str(periodo), str(versao)): ingerido_em
        for fonte, cod_ibge, periodo, versao, ingerido_em in rows
        if ingerido_em is not None
    }


def _resolve_ingestion_time(
    times: dict[_IngestionKey, datetime],
    *,
    fonte: str,
    cod_ibge: str,
    periodo: str,
    versao: str,
) -> datetime | None:
    """Mantém a semântica antiga: menor instante entre payload do ente e nacional."""
    candidates = (
        times.get((fonte, cod_ibge, periodo, versao)),
        times.get((fonte, "BR", periodo, versao)),
    )
    return min((value for value in candidates if value is not None), default=None)


def _upsert_many(
    session: Session,
    valores: Sequence[dict[str, Any]],
) -> None:
    """UPSERT multi-values limitado para não exceder parâmetros do PostgreSQL."""
    update_columns = (
        "uf",
        "ano",
        "n_registros",
        "versao_entrega_vigente",
        "ingerido_em",
        "defasagem_periodos",
        "atualizado_em",
    )
    for offset in range(0, len(valores), _BULK_UPSERT_SIZE):
        batch = valores[offset : offset + _BULK_UPSERT_SIZE]
        insert_stmt = pg_insert(MartCoberturaFonte).values(list(batch))
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=["fonte", "cod_ibge", "periodo"],
            set_={
                column: getattr(insert_stmt.excluded, column)
                for column in update_columns
            },
        )
        session.execute(stmt)


def refresh_cobertura(session: Session, *, hoje: date | None = None) -> int:
    """Materializa ``gold.mart_cobertura_fonte`` do estado atual (idempotente). Retorna linhas."""
    hoje = hoje or date.today()
    agora = datetime.now()
    valores_cobertura: list[dict[str, Any]] = []
    # Rematerialização completa: uma cobertura que sumiu (fonte retirou o período) deve
    # deixar de existir como linha, não persistir como zumbi de um refresh anterior.
    session.execute(delete(MartCoberturaFonte))
    ingestion_times = _ingestion_times(session, _SILVER_ENTREGA_MODEL)

    # (1) Fontes por-ente registradas em dim_entrega (SICONFI/IBGE): cobertura da versão vigente.
    for fonte, (model, periodo_col) in _SILVER_ENTREGA_MODEL.items():
        relatorio = FONTE_RELATORIO[fonte]
        # A subconsulta correlacionada preserva o zero de entregas sem silver e conduz
        # o PostgreSQL ao índice composto (cod_ibge, período, versão). O LEFT JOIN
        # agregado anterior podia virar Seq Scan integral quando estatísticas/autovacuum
        # estavam atrasados após grandes backfills.
        silver_count = (
            select(func.count(model.id))
            .where(
                model.cod_ibge == DimEntrega.cod_ibge,
                periodo_col == DimEntrega.periodo,
                model.versao_entrega == DimEntrega.versao_entrega,
            )
            .correlate(DimEntrega)
            .scalar_subquery()
        )
        rows = session.execute(
            select(
                DimEntrega.cod_ibge,
                DimEntrega.periodo,
                DimEntrega.versao_entrega,
                silver_count,
            )
            .where(DimEntrega.relatorio == relatorio, DimEntrega.vigente.is_(True))
        ).all()
        for cod_ibge, periodo, versao, n in rows:
            if cod_ibge == "BR":
                continue
            ano = int(str(periodo)[:4]) if str(periodo)[:4].isdigit() else 0
            valores_cobertura.append(
                {
                    "fonte": fonte,
                    "cod_ibge": cod_ibge,
                    "periodo": periodo,
                    "uf": _uf_de(cod_ibge),
                    "ano": ano,
                    "n_registros": int(n or 0),
                    "versao_entrega_vigente": versao,
                    "ingerido_em": _resolve_ingestion_time(
                        ingestion_times,
                        fonte=fonte,
                        cod_ibge=str(cod_ibge),
                        periodo=str(periodo),
                        versao=str(versao),
                    ),
                    "defasagem_periodos": defasagem_periodos(periodo, hoje),
                    "atualizado_em": agora,
                },
            )

    # (2) Fontes nacionais ('BR' em dim_entrega) cujo dado por-ente vive no silver.
    # A entrega dessas fontes é 'BR' (arquivo/API nacional); a versão corrente é a última
    # materializada no próprio silver (replace por versão), não uma linha de dim_entrega.
    for fonte, spec in _SILVER_POR_ENTE.items():
        vigente = session.scalar(select(func.max(spec.model.versao_entrega)))
        conditions = [spec.model.versao_entrega == vigente] if vigente is not None else []
        rows = session.execute(
            select(spec.cod_ibge_col, spec.periodo_expr, func.count())
            .where(*conditions)
            .group_by(spec.cod_ibge_col, spec.periodo_expr)
        ).all()
        for cod_ibge, periodo, n in rows:
            periodo = str(periodo)
            ano = int(periodo[:4]) if periodo[:4].isdigit() else 0
            valores_cobertura.append(
                {
                    "fonte": fonte,
                    "cod_ibge": cod_ibge,
                    "periodo": periodo,
                    "uf": _uf_de(cod_ibge),
                    "ano": ano,
                    "n_registros": int(n or 0),
                    "versao_entrega_vigente": vigente,
                    "ingerido_em": None,
                    "defasagem_periodos": defasagem_periodos(periodo, hoje),
                    "atualizado_em": agora,
                },
            )

    # (3) BCB (séries nacionais, sem ente): uma linha por série.
    bcb_vigente = session.scalar(
        select(DimEntrega.versao_entrega)
        .where(DimEntrega.relatorio == "BCB", DimEntrega.vigente.is_(True))
        .order_by(DimEntrega.homologada_em.desc())
        .limit(1)
    )
    for serie, n, ultima in session.execute(
        select(BcbIndice.codigo_serie, func.count(), func.max(BcbIndice.data_ref)).group_by(
            BcbIndice.codigo_serie
        )
    ).all():
        ano = ultima.year if ultima is not None else hoje.year
        valores_cobertura.append(
            {
                "fonte": "bcb",
                "cod_ibge": str(serie),
                "periodo": f"SGS-{serie}",
                "uf": None,
                "ano": ano,
                "n_registros": int(n or 0),
                "versao_entrega_vigente": bcb_vigente,
                "ingerido_em": None,
                "defasagem_periodos": 0,
                "atualizado_em": agora,
            },
        )

    _upsert_many(session, valores_cobertura)
    return len(valores_cobertura)
