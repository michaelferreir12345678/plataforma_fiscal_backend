"""Acesso a dados do módulo assistant (SQL/ORM). Sem regra de negócio (§7)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, literal, select, update
from sqlalchemy.orm import Session, aliased

from app.modules.assistant.models import Conversa, ConversaUso, IaToolCall, NormaChunk


# --------------------------------------------------------------------------- #
# gold.norma_chunk (vector store normativo — compartilhado)
# --------------------------------------------------------------------------- #
def list_norma_chunks(session: Session, *, fontes: Sequence[str] | None = None) -> list[NormaChunk]:
    stmt = select(NormaChunk)
    if fontes:
        stmt = stmt.where(NormaChunk.fonte.in_(list(fontes)))
    return list(session.scalars(stmt.order_by(NormaChunk.fonte, NormaChunk.dispositivo)))


def get_norma_chunk(session: Session, *, fonte: str, dispositivo: str) -> NormaChunk | None:
    return session.scalar(
        select(NormaChunk).where(NormaChunk.fonte == fonte, NormaChunk.dispositivo == dispositivo)
    )


def count_norma_chunks(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(NormaChunk)) or 0)


def count_norma_com_modelo(session: Session, modelo_embedding: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(NormaChunk)
            .where(NormaChunk.modelo_embedding == modelo_embedding)
        )
        or 0
    )


def upsert_norma_chunk(
    session: Session,
    *,
    fonte: str,
    dispositivo: str,
    titulo: str | None,
    texto: str,
    tags: list[str],
    indicadores: list[str],
    modelo_embedding: str,
    dim: int,
    embedding: list[float],
    source_ref: dict | None,
) -> NormaChunk:
    row = get_norma_chunk(session, fonte=fonte, dispositivo=dispositivo)
    if row is None:
        row = NormaChunk(fonte=fonte, dispositivo=dispositivo)
        session.add(row)
    row.titulo = titulo
    row.texto = texto
    row.tags = tags
    row.indicadores = indicadores
    row.modelo_embedding = modelo_embedding
    row.dim = dim
    row.embedding = embedding
    row.source_ref = source_ref
    session.flush()
    return row


# --------------------------------------------------------------------------- #
# op.conversa / op.conversa_uso (privados do tenant, RLS por org_id)
# --------------------------------------------------------------------------- #
def insert_conversa(
    session: Session,
    *,
    org_id: uuid.UUID,
    usuario_id: uuid.UUID | None,
    tipo: str,
    cod_ibge: str | None,
    periodo: str | None,
    pergunta: str,
    resposta: str,
    recusa: bool,
    dado_disponivel: bool,
    modelo: str | None,
    fontes: list[dict[str, Any]],
    fatos: list[dict[str, Any]],
    source_refs: list[dict[str, Any]],
    dados_incompletos: list[dict[str, Any]],
    as_of: datetime | None,
    as_of_explicito: bool = False,
    thread_id: uuid.UUID | None = None,
    parent_id: uuid.UUID | None = None,
    verificacao: dict[str, Any] | None = None,
) -> Conversa:
    # Sem fio informado, a conversa **é** o fio: o id é gerado aqui (em vez de deixado ao
    # default da coluna) justamente para que ``thread_id`` possa apontar para ele na mesma
    # inserção. Toda linha nasce com fio; nenhuma precisa de um segundo UPDATE.
    novo_id = uuid.uuid4()
    if parent_id is not None and thread_id is None:
        raise ValueError("parent_id exige thread_id: um turno causal precisa pertencer a um fio")
    row = Conversa(
        id=novo_id,
        thread_id=thread_id or novo_id,
        parent_id=parent_id,
        org_id=org_id,
        usuario_id=usuario_id,
        tipo=tipo,
        cod_ibge=cod_ibge,
        periodo=periodo,
        pergunta=pergunta,
        resposta=resposta,
        recusa=recusa,
        dado_disponivel=dado_disponivel,
        modelo=modelo,
        fontes=fontes,
        fatos=fatos,
        source_refs=source_refs,
        dados_incompletos=dados_incompletos,
        verificacao=verificacao,
        as_of=as_of,
        as_of_explicito=as_of_explicito,
    )
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def insert_conversa_uso(
    session: Session,
    *,
    org_id: uuid.UUID,
    conversa_id: uuid.UUID | None,
    modelo: str,
    tokens_entrada: int,
    tokens_saida: int,
    latencia_ms: int,
) -> ConversaUso:
    row = ConversaUso(
        org_id=org_id,
        conversa_id=conversa_id,
        modelo=modelo,
        tokens_entrada=tokens_entrada,
        tokens_saida=tokens_saida,
        latencia_ms=latencia_ms,
    )
    session.add(row)
    session.flush()
    return row


def insert_ia_tool_call(
    session: Session,
    *,
    org_id: uuid.UUID,
    usuario_id: uuid.UUID | None,
    conversa_id: uuid.UUID | None,
    ferramenta: str,
    origem: str,
    cod_ibge: str | None,
    argumentos: dict[str, Any],
    as_of: datetime | None,
    status: str,
    erro_tipo: str | None,
    erro_detalhe: str | None,
    http_status: int | None,
    linhas: int,
    duracao_ms: int,
    source_refs: list[dict[str, Any]],
) -> IaToolCall:
    """Grava a auditoria de uma chamada de ferramenta (G7) — sucesso **ou** falha."""
    row = IaToolCall(
        org_id=org_id,
        usuario_id=usuario_id,
        conversa_id=conversa_id,
        ferramenta=ferramenta,
        origem=origem,
        cod_ibge=cod_ibge,
        argumentos=argumentos,
        as_of=as_of,
        status=status,
        erro_tipo=erro_tipo,
        erro_detalhe=erro_detalhe,
        http_status=http_status,
        linhas=linhas,
        duracao_ms=duracao_ms,
        source_refs=source_refs,
    )
    session.add(row)
    session.flush()
    return row


def list_ia_tool_calls(session: Session, *, org_id: uuid.UUID, limit: int = 50) -> list[IaToolCall]:
    """Chamadas recentes da organização (auditoria consultável, RLS por org_id)."""
    return list(
        session.scalars(
            select(IaToolCall)
            .where(IaToolCall.org_id == org_id)
            .order_by(IaToolCall.criado_em.desc())
            .limit(limit)
        )
    )


def get_conversa(session: Session, *, conversa_id: uuid.UUID, org_id: uuid.UUID) -> Conversa | None:
    """Uma conversa da organização. O ``org_id`` no WHERE é redundante com a RLS — e fica.

    Redundância aqui é barata e o modo de falha do contrário é caríssimo: bastaria uma
    consulta feita por sessão administrativa (``admin_session``, que a RLS deixa passar)
    para o ``conversa_id`` de outra organização virar histórico desta.
    """
    return session.scalar(
        select(Conversa).where(Conversa.id == conversa_id, Conversa.org_id == org_id)
    )


def list_ancestrais_da_conversa(
    session: Session,
    *,
    conversa_id: uuid.UUID,
    thread_id: uuid.UUID,
    org_id: uuid.UUID,
    limit: int,
) -> list[Conversa]:
    """Ancestrais causais da âncora, do mais antigo ao turno informado.

    O CTE sobe por ``parent_id`` e nunca seleciona um irmão criado depois em outra aba.
    ``thread_id`` e ``org_id`` ficam em cada passo como defesa contra linha corrompida ou
    sessão administrativa que contorne a RLS.
    """
    if limit <= 0:
        return []
    cadeia = (
        select(
            Conversa.id.label("id"),
            Conversa.parent_id.label("parent_id"),
            literal(0).label("profundidade"),
        )
        .where(
            Conversa.id == conversa_id,
            Conversa.thread_id == thread_id,
            Conversa.org_id == org_id,
        )
        .cte("ancestrais_conversa", recursive=True)
    )
    pai = aliased(Conversa)
    cadeia = cadeia.union_all(
        select(
            pai.id,
            pai.parent_id,
            (cadeia.c.profundidade + 1).label("profundidade"),
        )
        .join(cadeia, pai.id == cadeia.c.parent_id)
        .where(
            pai.thread_id == thread_id,
            pai.org_id == org_id,
            cadeia.c.profundidade + 1 < limit,
        )
    )
    return list(
        session.scalars(
            select(Conversa)
            .join(cadeia, Conversa.id == cadeia.c.id)
            .order_by(cadeia.c.profundidade.desc())
        )
    )


def list_conversas(
    session: Session, *, org_id: uuid.UUID, cod_ibges: set[str], limit: int = 20
) -> list[Conversa]:
    """Histórico da organização já restrito aos entes licenciados do principal."""
    # Fail closed: linha legada sem ente não tem classificação que permita decidir se o
    # usuário pode vê-la. Todos os fluxos atuais gravam ``cod_ibge``; até que exista um
    # tipo explicitamente global, ``NULL`` não é atalho para conteúdo org-wide.
    if not cod_ibges:
        return []
    return list(
        session.scalars(
            select(Conversa)
            .where(Conversa.org_id == org_id, Conversa.cod_ibge.in_(cod_ibges))
            .order_by(Conversa.criado_em.desc())
            .limit(limit)
        )
    )


def associar_tool_calls_a_conversa(
    session: Session,
    *,
    org_id: uuid.UUID,
    call_ids: Sequence[uuid.UUID],
    conversa_id: uuid.UUID,
) -> None:
    """Fecha o elo G7 depois que a resposta recebe seu id definitivo."""
    ids = list(dict.fromkeys(call_ids))
    if not ids:
        return
    session.execute(
        update(IaToolCall)
        .where(IaToolCall.org_id == org_id, IaToolCall.id.in_(ids))
        .values(conversa_id=conversa_id)
    )


@dataclass(frozen=True)
class UsoSummary:
    consultas: int
    tokens_entrada: int
    tokens_saida: int


def usage_summary(session: Session, *, org_id: uuid.UUID, desde: datetime) -> UsoSummary:
    row = session.execute(
        select(
            func.count(ConversaUso.id),
            func.coalesce(func.sum(ConversaUso.tokens_entrada), 0),
            func.coalesce(func.sum(ConversaUso.tokens_saida), 0),
        ).where(ConversaUso.org_id == org_id, ConversaUso.ts >= desde)
    ).one()
    return UsoSummary(
        consultas=int(row[0] or 0),
        tokens_entrada=int(row[1] or 0),
        tokens_saida=int(row[2] or 0),
    )
