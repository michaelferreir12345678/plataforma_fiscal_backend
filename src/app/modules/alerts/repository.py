"""Acesso a dados da Sprint 15: op.alerta (upsert dedup) e gold.calendario_obrigacao."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import false, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.alerts.models import Alerta, CalendarioObrigacao

# Campos de conteúdo atualizados na reavaliação (o ``status`` do gestor é preservado).
_ALERTA_UPDATE = (
    "cod_ibge",
    "categoria",
    "severidade",
    "prioridade",
    "titulo",
    "motivo_legal",
    "acao_sugerida",
    "prazo",
    "link",
    "indicador",
    "periodo",
    "source_ref",
    "memoria",
    "atualizado_em",
)


def upsert_alerta(session: Session, valores: dict[str, Any]) -> None:
    """Insere/atualiza por (org_id, chave). Não rebaixa o ``status`` já tratado pelo gestor."""
    stmt = pg_insert(Alerta).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["org_id", "chave"],
        set_={k: valores[k] for k in _ALERTA_UPDATE if k in valores},
    )
    session.execute(stmt)


def list_alertas(
    session: Session,
    *,
    org_id: uuid.UUID,
    cods_ibge: Iterable[str] | None = None,
    incluir_status: Iterable[str] | None = None,
) -> list[Alerta]:
    stmt = select(Alerta).where(Alerta.org_id == org_id)
    if cods_ibge is not None:
        cods = list(cods_ibge)
        stmt = stmt.where(Alerta.cod_ibge.in_(cods)) if cods else stmt.where(false())
    if incluir_status is not None:
        stmt = stmt.where(Alerta.status.in_(list(incluir_status)))
    return list(session.scalars(stmt.order_by(Alerta.prioridade, Alerta.prazo, Alerta.criado_em)))


def get_alerta(session: Session, *, org_id: uuid.UUID, alerta_id: uuid.UUID) -> Alerta | None:
    return session.scalar(
        select(Alerta).where(Alerta.id == alerta_id, Alerta.org_id == org_id)
    )


def list_historico(
    session: Session,
    *,
    org_id: uuid.UUID,
    cods_ibge: Iterable[str] | None = None,
    categoria: str | None = None,
    limite: int = 100,
) -> list[Alerta]:
    """Alertas já tratados, do mais recentemente resolvido para o mais antigo."""
    stmt = select(Alerta).where(
        Alerta.org_id == org_id, Alerta.status.in_(("resolvida", "descartada"))
    )
    if cods_ibge is not None:
        cods = list(cods_ibge)
        stmt = stmt.where(Alerta.cod_ibge.in_(cods)) if cods else stmt.where(false())
    if categoria:
        stmt = stmt.where(Alerta.categoria == categoria)
    stmt = stmt.order_by(
        Alerta.resolvido_em.desc().nullslast(), Alerta.atualizado_em.desc()
    ).limit(limite)
    return list(session.scalars(stmt))


def set_status(
    session: Session, *, org_id: uuid.UUID, alerta_id: uuid.UUID, status: str, agora: datetime,
    usuario_id: uuid.UUID | None = None,
) -> int:
    tratado = status in ("resolvida", "descartada")
    result = session.execute(
        update(Alerta)
        .where(Alerta.id == alerta_id, Alerta.org_id == org_id)
        .values(
            status=status,
            atualizado_em=agora,
            # Reabrir um alerta apaga a assinatura: manter quem o fechou seria atribuir
            # a essa pessoa um estado que ela não escolheu.
            resolvido_em=agora if tratado else None,
            resolvido_por=usuario_id if tratado else None,
        )
    )
    return result.rowcount


def fechar_alerta_orfao(
    session: Session, *, org_id: uuid.UUID, chave: str, agora: datetime
) -> bool:
    """Fecha um alerta ativo cuja causa deixou de existir (A21/Sprint A5).

    Uma retificação que corrige o indicador para dentro do limite não gera uma nova
    escrita em ``_alertas_limite`` — a faixa some do que é avaliado, e sem isto o alerta
    antigo ficava órfão, ativo para sempre. Só mexe em alertas ainda não tratados pelo
    gestor (``nova``/``reconhecida``): um alerta já ``resolvida``/``descartada`` não é
    reaberto nem re-tocado. ``resolvido_por`` fica nulo — foi o dado que mudou, não uma
    decisão de alguém (mesma regra de "reabrir apaga assinatura" de :func:`set_status`).
    Devolve ``True`` se fechou algo.
    """
    result = session.execute(
        update(Alerta)
        .where(
            Alerta.org_id == org_id,
            Alerta.chave == chave,
            Alerta.status.in_(("nova", "reconhecida")),
        )
        .values(
            status="resolvida",
            atualizado_em=agora,
            resolvido_em=agora,
            resolvido_por=None,
        )
    )
    return result.rowcount > 0


def contar_por_severidade(
    session: Session, *, org_id: uuid.UUID, cods_ibge: Iterable[str] | None = None
) -> dict[str, int]:
    stmt = (
        select(Alerta.severidade, func.count())
        .where(Alerta.org_id == org_id, Alerta.status != "descartada")
        .group_by(Alerta.severidade)
    )
    if cods_ibge is not None:
        cods = list(cods_ibge)
        stmt = stmt.where(Alerta.cod_ibge.in_(cods)) if cods else stmt.where(false())
    return {str(sev): int(n) for sev, n in session.execute(stmt).all()}


# --- gold.calendario_obrigacao ---
_CAL_UPDATE = (
    "periodicidade",
    "prazo",
    "status",
    "entregue_em",
    "versao_entrega",
    "source_ref",
    "atualizado_em",
)


def upsert_calendario(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(CalendarioObrigacao).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cod_ibge", "relatorio", "periodo"],
        set_={k: valores[k] for k in _CAL_UPDATE if k in valores},
    )
    session.execute(stmt)


def list_calendario(session: Session, *, cod_ibge: str) -> list[CalendarioObrigacao]:
    return list(
        session.scalars(
            select(CalendarioObrigacao)
            .where(CalendarioObrigacao.cod_ibge == cod_ibge)
            .order_by(CalendarioObrigacao.prazo, CalendarioObrigacao.relatorio)
        )
    )
