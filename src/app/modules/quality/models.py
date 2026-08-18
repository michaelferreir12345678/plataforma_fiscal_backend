"""Modelos da Sprint 26: resultado dos checks e arestas de linhagem."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
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


class QualidadeTratativa(Base):
    """op.qualidade_tratativa — o que se fez com uma falha de qualidade e como terminou.

    A Sprint 26 entregou a metade que **detecta**; esta é a que **resolve**. Sem registro
    de tratativa, o mesmo caso é triado do zero a cada visita — e um aviso permanente que
    ninguém consegue encerrar é um aviso que todos aprendem a ignorar, que é o pior
    desfecho possível para um selo de qualidade.

    Vive em ``op`` por uma razão de fronteira: o **veredito** é dado público e
    compartilhado (a mesma falha do mesmo ente vale para toda organização que o
    acompanha), mas a **decisão sobre o que fazer** é operacional e privada. Duas
    consultorias que acompanham o mesmo município podem ler a mesma divergência de formas
    diferentes, e nenhuma delas escreve na leitura da outra.
    """

    __tablename__ = "qualidade_tratativa"
    __table_args__ = (
        CheckConstraint(
            "status IN ('aberta', 'diagnosticada', 'acao_aplicada', 'resolvida', "
            "'aceita_como_fato')",
            name="ck_qualidade_tratativa_status",
        ),
        CheckConstraint(
            "classe IS NULL OR classe IN ('plataforma', 'fonte', 'misto', 'cobertura')",
            name="ck_qualidade_tratativa_classe",
        ),
        CheckConstraint(
            "status <> 'aceita_como_fato' OR (justificativa IS NOT NULL AND "
            "length(btrim(justificativa)) >= 10)",
            name="ck_qualidade_tratativa_justificativa",
        ),
        UniqueConstraint(
            "org_id",
            "check_codigo",
            "cod_ibge",
            "periodo",
            name="uq_qualidade_tratativa_caso",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: A chave do **caso**, sem ``versao_entrega`` de propósito: uma retificação cria
    #: veredito novo (0044/A26), mas zerar a análise a cada retificação faria o gestor
    #: recomeçar a triagem de um problema que ele já conhece.
    check_codigo: Mapped[str] = mapped_column(Text, nullable=False)
    cod_ibge: Mapped[str | None] = mapped_column(String(7), nullable=True)
    periodo: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="aberta")
    #: De quem é o número que não fechou — é o que decide a ação cabível (``causa.py``).
    classe: Mapped[str | None] = mapped_column(String(12), nullable=True)
    diagnostico: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: Obrigatória para aceitar como fato. Aceitar **não** apaga o selo: ele passa a
    #: exibir este motivo e quem o assinou — esconder divergência conhecida seria pior
    #: que exibi-la.
    justificativa: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Cada ação aplicada e o veredito que ela produziu. Sem isto, uma falha que resiste a
    #: três reprocessamentos parece nunca ter sido tratada.
    tentativas: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
