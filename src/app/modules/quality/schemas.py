"""Contratos HTTP de qualidade e linhagem (Sprint 26)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.shared.source_ref import SourceRef

StatusCheck = Literal["ok", "aviso", "falha"]


class CheckOut(BaseModel):
    """Resultado corrente de uma verificação, com os dois lados e a **procedência**.

    A26 (E1): o check devolve número fiscal (``esquerda``/``direita``/``diferenca``) e
    devolvia-o sem ``source_ref`` — a única exceção, junto da reconciliação, à regra §6.3.
    Pior: sem a ``versao_entrega`` conferida, depois de uma retificação ninguém sabia
    sobre qual versão o check passou ou falhou.
    """

    id: uuid.UUID
    job_id: uuid.UUID | None = None
    fonte: str
    cod_ibge: str | None = None
    periodo: str | None = None
    #: Entrega conferida. ``None`` nos checks que não se ancoram numa (atualidade).
    versao_entrega: str | None = None
    check_codigo: str
    rotulo: str
    status: StatusCheck
    esquerda: Decimal | None = None
    direita: Decimal | None = None
    diferenca: Decimal | None = None
    tolerancia: Decimal | None = None
    detalhe: dict[str, Any] = Field(default_factory=dict)
    executado_em: datetime
    source_ref: SourceRef | None = None


class ResumoQualidade(BaseModel):
    total: int = 0
    ok: int = 0
    aviso: int = 0
    falha: int = 0
    fontes_com_falha: list[str] = Field(default_factory=list)
    checks_com_falha: list[str] = Field(default_factory=list)


class QualidadeResponse(BaseModel):
    """Painel de qualidade: o estado corrente de cada verificação."""

    gerado_em: datetime
    resumo: ResumoQualidade
    itens: list[CheckOut] = Field(default_factory=list)
    total: int
    pagina: int
    por_pagina: int
    observacao: str | None = None


class ExecucaoChecksOut(BaseModel):
    """Retorno de uma execução de checks (job ou agendada)."""

    cod_ibge: str | None = None
    periodo: str | None = None
    executados: int = 0
    ok: int = 0
    aviso: int = 0
    falha: int = 0
    alertas_emitidos: int = 0
    codigos_falha: list[str] = Field(default_factory=list)


class LineageNo(BaseModel):
    """Um nó do grafo, com o papel que ele cumpre no caminho do dado."""

    id: str
    camada: Literal["fonte", "bronze", "silver", "gold", "endpoint", "pagina"]


class LineageAresta(BaseModel):
    origem: str
    destino: str
    tipo: str
    detalhe: dict[str, Any] = Field(default_factory=dict)


class LineageResponse(BaseModel):
    """Grafo navegável nos dois sentidos a partir de um nó.

    ``montante`` responde "de onde vem este número?"; ``jusante``, "o que quebra se isto
    falhar?". Sem nó, devolve o grafo inteiro (a Central desenha o mapa).
    """

    no: str | None = None
    camada: str | None = None
    montante: list[LineageAresta] = Field(default_factory=list)
    jusante: list[LineageAresta] = Field(default_factory=list)
    paginas_afetadas: list[str] = Field(default_factory=list)
    fontes_de_origem: list[str] = Field(default_factory=list)
    nos: list[LineageNo] = Field(default_factory=list)
    arestas: list[LineageAresta] = Field(default_factory=list)
    total_arestas: int = 0


# --------------------------------------------------------------------------- #
# Sprint Q1 — resolução: o que se faz com a falha
# --------------------------------------------------------------------------- #
class TratativaResumo(BaseModel):
    """O que já se fez sobre esta ocorrência, e como terminou."""

    status: str = Field(
        description=(
            "aberta | diagnosticada | acao_aplicada | resolvida | aceita_como_fato"
        )
    )
    classe: str | None = None
    justificativa: str | None = Field(
        default=None,
        description=(
            "Motivo do aceite. O selo continua aparecendo com este texto: aceitar uma "
            "divergência não a esconde, apenas registra por que ela é conhecida."
        ),
    )
    tentativas: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Ações aplicadas e o veredito que cada uma produziu. Uma falha que resiste a "
            "três reprocessamentos precisa parecer o que é."
        ),
    )
    atualizado_em: datetime | None = None


class OcorrenciaQualidade(BaseModel):
    """Uma verificação em falha com classe da causa, evidência e ações cabíveis."""

    check_codigo: str
    cod_ibge: str | None = None
    periodo: str | None = None
    fonte: str
    status_check: str
    esquerda: Decimal | None = None
    direita: Decimal | None = None
    diferenca: Decimal | None = None
    tolerancia: Decimal | None = None
    #: De quem é o número que não fechou — decide a ação (ver `quality/causa.py`).
    classe: str = Field(description="plataforma | fonte | misto | cobertura")
    lado_esquerdo: str
    lado_direito: str
    porque: str = Field(description="Por que a classe é essa — sustenta a ação oferecida.")
    diagnostico: dict[str, Any] = Field(default_factory=dict)
    #: **Só** as ações que existem para esta classe. Oferecer "reprocessar" numa
    #: divergência da fonte gastaria o tempo do gestor sem mudar o resultado.
    acoes: list[str] = Field(default_factory=list)
    tratativa: TratativaResumo | None = None


class OcorrenciasResponse(BaseModel):
    data: list[OcorrenciaQualidade] = Field(default_factory=list)
    total: int = 0
    por_classe: dict[str, int] = Field(default_factory=dict)


class AcaoRequest(BaseModel):
    """Pedido de ação sobre uma ocorrência."""

    check_codigo: str
    cod_ibge: str
    periodo: str | None = None
    acao: str = Field(
        description="rematerializar | verificar_na_fonte | aceitar_como_fato",
    )
    justificativa: str | None = Field(
        default=None,
        description="Obrigatória (≥10 caracteres) para 'aceitar_como_fato'.",
    )
