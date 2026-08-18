"""Modelos da Sprint 17 (assistente) e IA-1a (camada de ferramentas).

- ``gold.norma_chunk`` — corpo normativo fatiado por dispositivo + embedding (RAG).
  Dado público/compartilhado (sem RLS).
- ``op.conversa`` — histórico do assistente, privado da organização (RLS por org_id).
- ``op.conversa_uso`` — telemetria por chamada (tokens/latência), alimenta a cota do plano.
- ``op.ia_tool_call`` — auditoria de **cada chamada de ferramenta** (IA-1a, guardrail G7).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

FONTES_NORMA: tuple[str, ...] = ("LRF", "CF", "MDF")
TIPOS_CONVERSA: tuple[str, ...] = ("pergunta", "resumo_executivo")


class NormaChunk(Base):
    """gold.norma_chunk — dispositivo normativo + embedding portável (pgvector em prod)."""

    __tablename__ = "norma_chunk"
    __table_args__ = (
        CheckConstraint("fonte IN ('LRF', 'CF', 'MDF')", name="ck_norma_chunk_fonte"),
        UniqueConstraint("fonte", "dispositivo", name="uq_norma_chunk_fonte_dispositivo"),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    fonte: Mapped[str] = mapped_column(String(8), nullable=False)
    dispositivo: Mapped[str] = mapped_column(Text, nullable=False)
    titulo: Mapped[str | None] = mapped_column(Text, nullable=True)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    indicadores: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    modelo_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    source_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Conversa(Base):
    """op.conversa — uma interação com o assistente (RLS por org_id)."""

    __tablename__ = "conversa"
    __table_args__ = (
        CheckConstraint("tipo IN ('pergunta', 'resumo_executivo')", name="ck_conversa_tipo"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Fio da conversa (Sprint IA-7): todos os turnos de um mesmo diálogo compartilham este
    #: valor, e o primeiro turno o recebe igual ao próprio ``id``. É o que permite
    #: reconstruir o histórico numa consulta só, em vez de subir a cadeia turno a turno.
    thread_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversa.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: A âncora causal desta continuação. O fio agrupa; o pai impede que dois ramos criados
    #: a partir do mesmo turno sejam tratados como uma sequência única.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversa.id", ondelete="CASCADE"), nullable=True, index=True
    )
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )
    tipo: Mapped[str] = mapped_column(String(24), nullable=False, default="pergunta")
    cod_ibge: Mapped[str | None] = mapped_column(String(7), nullable=True)
    periodo: Mapped[str | None] = mapped_column(Text, nullable=True)
    pergunta: Mapped[str] = mapped_column(Text, nullable=False)
    resposta: Mapped[str] = mapped_column(Text, nullable=False)
    recusa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dado_disponivel: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    modelo: Mapped[str | None] = mapped_column(Text, nullable=True)
    fontes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    fatos: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    dados_incompletos: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    verificacao: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: O gestor **pediu** o corte, ou este é só o instante resolvido da leitura?
    #: ``as_of`` é sempre preenchido (auditoria: qual fotografia respondeu), então ele
    #: sozinho não distingue as duas coisas — e herdar o instante resolvido fazia toda
    #: continuação virar reprodução histórica presa ao relógio do primeiro turno.
    as_of_explicito: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IaToolCall(Base):
    """op.ia_tool_call — uma chamada de ferramenta da camada de IA (RLS por org_id).

    Guardrail **G7**: "como a IA chegou nisso?" tem de ser consultável, não reconstituído.
    Cada linha guarda quem chamou, o quê, com quais argumentos, em que ``as_of``, quanto
    demorou e **como terminou** — inclusive quando terminou em 403.

    A chamada que falhou é a mais importante das duas: é ela que mostra tentativa de
    acesso fora do escopo, argumento inventado por um modelo e ferramenta pedida por um
    nome que não existe. Por isso a gravação acontece em transação própria (ver
    ``shared/tooling/envelope.py``): o *rollback* da requisição que falhou não pode apagar
    o registro de que ela foi feita.
    """

    __tablename__ = "ia_tool_call"
    __table_args__ = (CheckConstraint("status IN ('ok', 'erro')", name="ck_ia_tool_call_status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )
    #: Conversa que originou a chamada — liga a cadeia de ferramentas à resposta gerada.
    conversa_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversa.id", ondelete="SET NULL"), nullable=True
    )
    ferramenta: Mapped[str] = mapped_column(Text, nullable=False)
    #: De onde veio a chamada: ``assistente``, ``mcp``, ``api``…
    origem: Mapped[str] = mapped_column(String(24), nullable=False, default="api")
    cod_ibge: Mapped[str | None] = mapped_column(String(7), nullable=True)
    argumentos: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="ok")
    #: ``type`` do Problem Details quando falhou — é o que distingue escopo de licença.
    erro_tipo: Mapped[str | None] = mapped_column(Text, nullable=True)
    erro_detalhe: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    linhas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duracao_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Fontes dos números devolvidos — o lastro do que o modelo recebeu.
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConversaUso(Base):
    """op.conversa_uso — telemetria por chamada de LLM (cota do plano, RLS por org_id)."""

    __tablename__ = "conversa_uso"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversa_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversa.id", ondelete="CASCADE"), nullable=True
    )
    modelo: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_entrada: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_saida: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latencia_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
