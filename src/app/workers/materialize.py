"""Materialização da gold por worker (Sprint 21).

A materialização **lazy** (por request) das Sprints 5–11 vira aqui um passo de worker: a
gold é pré-calculada para todo o escorpo/histórico do backfill, e o request só recai no
lazy como *fallback*. Reusa os ``build_detalhe`` idempotentes de cada módulo (fonte única
de verdade — nada é recalculado aqui) e persiste também o ``mart_indicador`` da DCL (que o
lazy dos módulos não gravava) para alimentar o semáforo/carteira em todos os períodos.

Ordem por ente:
- RREO (bimestral): RCL, receita, despesa, resultado, saúde, educação, ``mart_carteira``.
- RGF (quadrimestral/semestral): pessoal (+mart pessoal_executivo), dívida (+mart DCL),
  caixa & RP.
- CAPAG: materialização em massa silver→gold para **todos** os entes (INSERT…SELECT).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.errors import AppError
from app.modules.accounting import service as accounting_service
from app.modules.benchmark import service as benchmark_service
from app.modules.cash_rap import service as cash_rap_service
from app.modules.dashboard import carteira_service
from app.modules.debt import repository as debt_repo
from app.modules.debt import service as debt_service
from app.modules.expense import service as expense_service
from app.modules.health_edu import service as health_edu_service
from app.modules.indicators import service as indicators_service
from app.modules.ingestion import repository as ingestion_repo
from app.modules.ingestion.models import DimEntrega
from app.modules.personnel import service as personnel_service
from app.modules.result import service as result_service
from app.modules.revenue import service as revenue_service
from app.shared.source_ref import SourceRef

_REL_RREO = "RREO"
_REL_RGF = "RGF"
_REL_DCA = "DCA"


def _periodos_vigentes(session: Session, cod_ibge: str, relatorio: str) -> list[str]:
    return sorted(
        session.scalars(
            select(DimEntrega.periodo).where(
                DimEntrega.cod_ibge == cod_ibge,
                DimEntrega.relatorio == relatorio,
                DimEntrega.vigente.is_(True),
            )
        )
    )


def _rreo_de_rgf(periodo_rgf: str) -> str | None:
    """RGF→RREO correspondente: Qn→B(2n); Sn→B(3n). Anual/desconhecido ⇒ None."""
    try:
        ano, marca = periodo_rgf.split("-", 1)
        tipo, num = marca[0], int(marca[1:])
    except (ValueError, IndexError):
        return None
    if tipo == "Q":
        return f"{ano}-B{2 * num}"
    if tipo == "S":
        return f"{ano}-B{3 * num}"
    return None


def _safe_kw(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> bool:
    """Como :func:`_safe`, para materializadores com argumentos nomeados."""
    try:
        fn(*args, **kwargs)
        return True
    except (AppError, ValueError):
        return False


def _safe(fn: Callable[..., Any], *args: Any) -> bool:
    """Executa um materializador tolerando dado-fonte insuficiente (não bloqueia os demais).

    Dois sinais são esperados e não são erro de programa: ``AppError`` (anexo/entrega
    ausente) e ``ValueError`` do domínio (ex.: RCL não-positiva num ente cujo Anexo 03 veio
    zerado). Num backfill de 184 municípios, um ente com entrega incompleta não pode
    derrubar a materialização dos demais.
    """
    try:
        fn(*args)
        return True
    except (AppError, ValueError):
        return False


def _materializar_dcl_mart(session: Session, cod_ibge: str, periodo_rgf: str) -> None:
    """Grava o ``mart_indicador`` da DCL (divida_consolidada_liquida) sob o período RREO.

    O ``build_detalhe`` da dívida materializa ``fato_divida`` mas não escrevia a linha de
    ``mart_indicador`` — sem ela o semáforo/carteira mostravam a DCL como 'sem dado'.
    """
    rreo = _rreo_de_rgf(periodo_rgf)
    if rreo is None:
        return
    versao_rgf = ingestion_repo.resolve_versao(
        session, cod_ibge=cod_ibge, relatorio=_REL_RGF, periodo=periodo_rgf
    )
    versao_rreo = ingestion_repo.resolve_versao(
        session, cod_ibge=cod_ibge, relatorio=_REL_RREO, periodo=rreo
    )
    if versao_rgf is None or versao_rreo is None:
        return
    fato = debt_repo.get_fato_divida(
        session, cod_ibge=cod_ibge, periodo=periodo_rgf, versao_entrega=versao_rgf
    )
    if fato is None or fato.dcl is None:
        return
    indicators_service.classificar_limite(
        session,
        cod_ibge,
        rreo,
        "divida_consolidada_liquida",
        Decimal(fato.dcl),
        source_ref=SourceRef(
            relatorio="RGF", anexo="Anexo 02 — DDCL", periodo=periodo_rgf,
            versao_entrega=versao_rgf,
        ),
        source_components=(
            SourceRef(relatorio="RGF", anexo="Anexo 02 — DDCL", periodo=periodo_rgf,
                      versao_entrega=versao_rgf),
            SourceRef(relatorio="RREO", anexo="Anexo 03", periodo=rreo,
                      versao_entrega=versao_rreo),
        ),
    )


def materialize_ente(session: Session, cod_ibge: str) -> dict[str, int]:
    """Materializa toda a gold disponível do ente (idempotente). Retorna contadores."""
    stats = {"rreo": 0, "rgf": 0, "carteira": 0, "dca": 0}
    rreo_periodos = _periodos_vigentes(session, cod_ibge, _REL_RREO)
    rgf_periodos = _periodos_vigentes(session, cod_ibge, _REL_RGF)

    for periodo in rreo_periodos:
        _safe(indicators_service.calcular_rcl, session, cod_ibge, periodo)
        _safe(revenue_service.build_detalhe, session, cod_ibge, periodo)
        _safe(expense_service.build_detalhe, session, cod_ibge, periodo)
        _safe(result_service.build_detalhe, session, cod_ibge, periodo)
        _safe(health_edu_service.build_saude, session, cod_ibge, periodo)
        _safe(health_edu_service.build_educacao, session, cod_ibge, periodo)
        stats["rreo"] += 1

    for periodo in rgf_periodos:
        _safe(personnel_service.build_detalhe, session, cod_ibge, periodo)
        _safe(debt_service.build_detalhe, session, cod_ibge, periodo)
        _safe(cash_rap_service.build_detalhe, session, cod_ibge, periodo)
        _safe(_materializar_dcl_mart, session, cod_ibge, periodo)
        stats["rgf"] += 1

    for periodo in rreo_periodos:
        if _safe(carteira_service.refresh_mart_carteira, session, cod_ibge, periodo):
            stats["carteira"] += 1

    # DCA (anual) → balanços patrimoniais. Chave própria: o exercício, não o período RREO.
    for periodo_dca in _periodos_vigentes(session, cod_ibge, _REL_DCA):
        try:
            ano = int(str(periodo_dca)[:4])
        except ValueError:
            continue
        if _safe_kw(
            accounting_service.ensure_materializado, session, cod_ibge, ano=ano, as_of=None
        ):
            stats["dca"] += 1
    return stats


def materialize_scope(
    entes: Iterable[str], *, on_progress: Callable[..., None] | None = None
) -> dict[str, int]:
    """Materializa a gold para vários entes, com commit por ente (resiliente).

    Um ente que falhe por completo é contado em ``falhas`` e o lote segue — a alternativa
    (abortar) perderia o trabalho já feito de centenas de entes.
    """
    total = {"entes": 0, "rreo": 0, "rgf": 0, "carteira": 0, "dca": 0, "falhas": 0}
    entes = list(entes)
    for i, cod in enumerate(entes, start=1):
        try:
            with SessionLocal() as session:
                stats = materialize_ente(session, cod)
                session.commit()
        except Exception as exc:  # noqa: BLE001 — isola o ente, não derruba o lote
            total["falhas"] += 1
            if on_progress is not None:
                on_progress(i, len(entes), cod, {"erro": exc.__class__.__name__})
            continue
        total["entes"] += 1
        for k in ("rreo", "rgf", "carteira", "dca"):
            total[k] += stats[k]
        if on_progress is not None:
            on_progress(i, len(entes), cod, stats)
    return total


_BENCHMARK_INDICADORES = ("pessoal_executivo", "divida_consolidada_liquida")


def materialize_benchmark(
    entes: Iterable[str],
    *,
    coorte: str = "porte",
    indicadores: Iterable[str] = _BENCHMARK_INDICADORES,
) -> int:
    """Materializa ``mart_benchmark`` multi-período (todos os períodos com mart_indicador).

    Para cada ente×indicador, roda o benchmark em **cada período** disponível em
    ``mart_indicador`` — não só o mais recente. Snapshots são imutáveis (idempotente).
    """
    snapshots = 0
    for cod in list(entes):
        with SessionLocal() as session:
            periodos = _mart_periodos(session, cod)
            for indicador in indicadores:
                for periodo in periodos:
                    if _safe_benchmark(session, cod, indicador, coorte, periodo):
                        snapshots += 1
            session.commit()
    return snapshots


def _mart_periodos(session: Session, cod_ibge: str) -> list[str]:
    return sorted(
        session.scalars(
            text(
                "SELECT DISTINCT periodo FROM gold.mart_indicador WHERE cod_ibge = :c"
            ).bindparams(c=cod_ibge)
        )
    )


def _safe_benchmark(
    session: Session, cod_ibge: str, indicador: str, coorte: str, periodo: str
) -> bool:
    try:
        benchmark_service.build_benchmark(
            session, cod_ibge=cod_ibge, indicador=indicador, coorte=coorte, periodo=periodo
        )
        return True
    except AppError:
        return False


def materialize_capag_todos(session: Session, *, versao: str | None = None) -> int:
    """Materializa ``fato_capag`` para **todos** os entes de uma versão CAPAG (bulk).

    INSERT…SELECT direto do silver → gold: eficiente para os ~5.569 entes. Sem ``versao``,
    usa a vigente de ``gold.dim_entrega`` (relatório CAPAG, entrega nacional 'BR');
    informá-la explicitamente permite materializar uma entrega específica.
    """
    vigente = versao or session.scalar(
        select(DimEntrega.versao_entrega)
        .where(DimEntrega.relatorio == "CAPAG", DimEntrega.vigente.is_(True))
        .order_by(DimEntrega.homologada_em.desc())
        .limit(1)
    )
    if vigente is None:
        return 0
    result = session.execute(
        text(
            """
            INSERT INTO gold.fato_capag
                (id, cod_ibge, ano_ref, nota_final, ind_endividamento, ind_poupanca,
                 ind_liquidez, metodologia_versao, versao_entrega)
            SELECT gen_random_uuid(), s.cod_ibge, s.ano_ref, s.nota_final,
                   s.ind_endividamento, s.ind_poupanca, s.ind_liquidez,
                   s.metodologia_versao, s.versao_entrega
            FROM silver.tesouro_capag s
            WHERE s.versao_entrega = :versao
            ON CONFLICT ON CONSTRAINT uq_fato_capag_chave DO NOTHING
            """
        ),
        {"versao": vigente},
    )
    return int(getattr(result, "rowcount", 0) or 0)
