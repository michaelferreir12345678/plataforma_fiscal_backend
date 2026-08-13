"""Modelos da Sprint 26: resultado dos checks e arestas de linhagem."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DataQualityCheck(Base):
    """gold.data_quality_check — uma verificação executada sobre um recorte de dado.

    Guarda os **dois lados** da comparação e a tolerância aplicada, não só o veredito:
    um gestor que discorda do check precisa poder refazer a conta.

    A chave única é ``(check, fonte, ente, período, versão da entrega)``. A
    ``versao_entrega`` entrou na E1 (A26): antes, reexecutar o check depois de uma
    retificação **sobrescrevia** o resultado da versão anterior, e ninguém sabia sobre
    qual entrega o "ok" guardado havia sido dado. Com a versão na chave, a retificação
    cria linha nova e o histórico de vereditos acompanha o histórico do dado —
    bitemporalidade (§6.5) valendo também para a verificação, não só para o valor.
    """

    __tablename__ = "data_quality_check"
    __table_args__ = (
        CheckConstraint("status IN ('ok', 'aviso', 'falha')", name="ck_data_quality_check_status"),
        UniqueConstraint(
            "check_codigo",
            "fonte",
            "cod_ibge",
            "periodo",
            "versao_entrega",
            name="uq_data_quality_check_chave_versao",
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    fonte: Mapped[str] = mapped_column(Text, nullable=False)
    cod_ibge: Mapped[str | None] = mapped_column(String(7), nullable=True)
    periodo: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: ``'-'`` (e não NULL) quando o check não se ancora numa entrega: NULL numa UNIQUE do
    #: PostgreSQL é distinto de NULL, e o *upsert* deixaria de conflitar.
    versao_entrega: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="-", default="-"
    )
    check_codigo: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    esquerda: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    direita: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    diferenca: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    tolerancia: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    detalhe: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    executado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: Ordem de escrita — o desempate da eleição do veredito vigente (migration 0044).
    #: ``executado_em`` sozinho não basta: onde o relógio é grosso (Windows: 200 chamadas
    #: de ``datetime.now(UTC)`` medidas com o mesmo valor), dois vereditos da mesma chave
    #: empatam e o desempate anterior — por ``id``, um ``uuid4()`` — era sorteio.
    seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("nextval('gold.dq_check_seq')")
    )


class LineageEdge(Base):
    """gold.lineage_edge — uma aresta do caminho fonte→bronze→silver→gold→endpoint→página.

    Mantido por **código** (seed idempotente), não por cadastro manual: o grafo tem de
    ser derivado do que o sistema realmente faz, senão vira documentação desatualizada
    com aparência de verdade.
    """

    __tablename__ = "lineage_edge"
    __table_args__ = (
        CheckConstraint(
            "tipo IN ('fonte_bronze', 'bronze_silver', 'bronze_gold', 'silver_gold', "
            "'gold_endpoint', 'endpoint_pagina')",
            name="ck_lineage_edge_tipo",
        ),
        UniqueConstraint("origem", "destino", "tipo", name="uq_lineage_edge_chave"),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    origem: Mapped[str] = mapped_column(Text, nullable=False)
    destino: Mapped[str] = mapped_column(Text, nullable=False)
    tipo: Mapped[str] = mapped_column(String(24), nullable=False)
    detalhe: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
