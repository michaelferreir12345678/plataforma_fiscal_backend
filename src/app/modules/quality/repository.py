"""Acesso a gold.data_quality_check e gold.lineage_edge (Sprint 26)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, not_, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from app.modules.quality.models import DataQualityCheck, LineageEdge


def _somente_vigente(*, as_of: datetime | None = None) -> ColumnElement[bool]:
    """Mantém, por chave de check, o veredito vigente agora ou no corte pedido.

    Desde a E1 (A26) a ``versao_entrega`` faz parte da chave única: uma retificação cria
    **linha nova** em vez de sobrescrever o resultado da versão anterior. O histórico é
    justamente o ganho — mas o painel e o selo mostram estado, não série, e sem este
    filtro um "falha" de uma entrega já retificada continuaria selando a página.

    O desempate por ``id`` garante exatamente uma linha por chave mesmo se duas versões
    forem verificadas no mesmo instante.
    """
    superior = aliased(DataQualityCheck)
    filtros = [
        superior.check_codigo == DataQualityCheck.check_codigo,
        superior.fonte == DataQualityCheck.fonte,
        superior.cod_ibge.is_not_distinct_from(DataQualityCheck.cod_ibge),
        superior.periodo.is_not_distinct_from(DataQualityCheck.periodo),
        or_(
            superior.executado_em > DataQualityCheck.executado_em,
            and_(
                superior.executado_em == DataQualityCheck.executado_em,
                # Ordem de ESCRITA, não ``id`` (migration 0044). O desempate por
                # ``uuid4()`` era sorteio, e o relógio empata com mais frequência do
                # que a E1 supôs — no Windows, 200 chamadas de ``datetime.now(UTC)``
                # medidas com o mesmo valor.
                superior.seq > DataQualityCheck.seq,
            ),
        ),
    ]
    # O alias correlacionado também precisa respeitar o corte. Filtrar só a linha externa
    # faria um veredito posterior ao ``as_of`` esconder o anterior e a consulta devolveria
    # vazio, em vez do estado que de fato vigorava naquele instante.
    if as_of is not None:
        filtros.append(superior.executado_em <= as_of)
    mais_novo = select(superior.id).where(*filtros).exists()
    return not_(mais_novo)


def upsert_check(session: Session, valores: dict[str, Any]) -> None:
    """Grava o estado corrente do check para **aquela entrega**.

    Reexecutar sobre a mesma versão atualiza a linha; reexecutar depois de uma retificação
    grava uma linha nova, porque a ``versao_entrega`` entrou na chave (A26/E1). O painel
    continua mostrando o estado corrente — o que deixou de existir é a sobrescrita muda do
    veredito da versão anterior.
    """
    # ``executado_em`` é gravado pelo relógio da aplicação também na inserção, e não pelo
    # ``server_default``: em PostgreSQL, ``now()`` é o instante da **transação**, então
    # dois vereditos gravados no mesmo commit ficariam com o mesmo carimbo.
    #
    # Isso **não basta** sozinho, e a E1 supôs que bastava: onde o relógio é grosso, dois
    # vereditos consecutivos empatam mesmo em transações separadas (Windows, medido: 200
    # chamadas de ``datetime.now(UTC)`` com o mesmo valor). Por isso a ordem de escrita é
    # gravada como fato próprio em ``seq`` (migration 0044) e é ela que desempata — o
    # carimbo continua sendo o instante real da execução, sem falsificação para forçar
    # unicidade.
    agora = datetime.now(UTC)
    proximo_seq = text("nextval('gold.dq_check_seq')")
    stmt = pg_insert(DataQualityCheck).values(id=uuid.uuid4(), executado_em=agora, **valores)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_data_quality_check_chave_versao",
        set_={
            "job_id": stmt.excluded.job_id,
            "status": stmt.excluded.status,
            "esquerda": stmt.excluded.esquerda,
            "direita": stmt.excluded.direita,
            "diferenca": stmt.excluded.diferenca,
            "tolerancia": stmt.excluded.tolerancia,
            "detalhe": stmt.excluded.detalhe,
            "executado_em": agora,
            # Reescrever é escrever de novo: a linha reexecutada passa a ser a mais
            # recente também na ordem de escrita, senão o empate voltaria a decidir errado.
            "seq": proximo_seq,
        },
    )
    session.execute(stmt)


def listar_checks(
    session: Session,
    *,
    fonte: str | None = None,
    status: str | None = None,
    cod_ibge: str | None = None,
    check_codigo: str | None = None,
    cods_escopo: Iterable[str] | None = None,
    limite: int = 100,
    offset: int = 0,
) -> tuple[list[DataQualityCheck], int]:
    stmt = select(DataQualityCheck).where(_somente_vigente())
    if fonte:
        stmt = stmt.where(DataQualityCheck.fonte == fonte)
    if status:
        stmt = stmt.where(DataQualityCheck.status == status)
    if cod_ibge:
        stmt = stmt.where(DataQualityCheck.cod_ibge == cod_ibge)
    if check_codigo:
        stmt = stmt.where(DataQualityCheck.check_codigo == check_codigo)
    if cods_escopo is not None:
        codigos = list(cods_escopo)
        # Check global (cod_ibge NULL) é visível a todos; o por ente respeita o escopo.
        stmt = stmt.where(
            DataQualityCheck.cod_ibge.is_(None) | DataQualityCheck.cod_ibge.in_(codigos)
        )
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    linhas = list(
        session.scalars(
            stmt.order_by(
                # Falha primeiro: o painel existe para mostrar problema, não catálogo.
                DataQualityCheck.status.desc(),
                DataQualityCheck.executado_em.desc(),
            )
            .limit(limite)
            .offset(offset)
        )
    )
    return linhas, int(total)


def contar_por_status(
    session: Session, *, cods_escopo: Iterable[str] | None = None
) -> dict[str, int]:
    stmt = (
        select(DataQualityCheck.status, func.count())
        .where(_somente_vigente())
        .group_by(DataQualityCheck.status)
    )
    if cods_escopo is not None:
        codigos = list(cods_escopo)
        stmt = stmt.where(
            DataQualityCheck.cod_ibge.is_(None) | DataQualityCheck.cod_ibge.in_(codigos)
        )
    return {str(s): int(n) for s, n in session.execute(stmt).all()}


def checks_abertos(
    session: Session,
    *,
    cod_ibge: str,
    periodo: str | None = None,
    as_of: datetime | None = None,
) -> list[DataQualityCheck]:
    """Checks em falha/aviso do ente vigentes agora ou no ``as_of`` solicitado."""
    stmt = select(DataQualityCheck).where(
        DataQualityCheck.cod_ibge == cod_ibge,
        DataQualityCheck.status.in_(("falha", "aviso")),
        # A entrega retificada não sela a página com o veredito da versão superada.
        _somente_vigente(as_of=as_of),
    )
    if as_of is not None:
        stmt = stmt.where(DataQualityCheck.executado_em <= as_of)
    if periodo:
        stmt = stmt.where(
            DataQualityCheck.periodo.is_(None) | (DataQualityCheck.periodo == periodo)
        )
    return list(session.scalars(stmt.order_by(DataQualityCheck.status.desc())))


def fontes_distintas(session: Session) -> list[str]:
    return [str(f) for f in session.scalars(select(DataQualityCheck.fonte).distinct())]


# --- lineage ---------------------------------------------------------------- #
def upsert_aresta(
    session: Session, *, origem: str, destino: str, tipo: str, detalhe: dict | None
) -> None:
    stmt = pg_insert(LineageEdge).values(
        id=uuid.uuid4(), origem=origem, destino=destino, tipo=tipo, detalhe=detalhe
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_lineage_edge_chave", set_={"detalhe": stmt.excluded.detalhe}
    )
    session.execute(stmt)


def listar_arestas(session: Session) -> Sequence[LineageEdge]:
    return list(session.scalars(select(LineageEdge).order_by(LineageEdge.tipo, LineageEdge.origem)))


def contar_arestas(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(LineageEdge)) or 0)
