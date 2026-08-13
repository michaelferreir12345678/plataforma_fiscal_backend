"""Schemas Pydantic (entrada/saída) do assistente de IA."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.shared.source_ref import SourceRef


# ------------------------------- Entrada -------------------------------- #
class PerguntaRequest(BaseModel):
    ente: str = Field(description="Código IBGE do ente (7 dígitos, ou 2 para UF).")
    pergunta: str = Field(min_length=3, max_length=2000)
    periodo: str | None = Field(
        default=None, description="Período RREO (ex.: 2024-B6). Default: última entrega vigente."
    )
    as_of: datetime | None = Field(
        default=None, description="Reprodução bitemporal; default = versão vigente."
    )
    pagina: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Rota da tela de onde veio a pergunta (ex.: '/pessoal'). Usada só quando a "
            "pergunta não nomeia indicador — 'pergunte sobre esta tela' (Sprint 25E)."
        ),
    )


class ResumoExecutivoRequest(BaseModel):
    ente: str = Field(description="Código IBGE do ente.")
    periodo: str | None = Field(default=None, description="Período RREO. Default: última entrega.")
    as_of: datetime | None = None
    foco: str | None = Field(
        default=None, description="Instrução opcional de foco (ex.: 'riscos de pessoal')."
    )


# ------------------------------- Saída ---------------------------------- #
class FatoResposta(BaseModel):
    """Um indicador calculado usado na resposta, com rastreabilidade total."""

    codigo: str
    rotulo: str
    valor_formatado: str
    valor: str | None = None
    unidade: str
    status: str
    faixa: str | None = None
    disponivel: bool
    periodo: str
    as_of: datetime | None = None
    source_ref: SourceRef | None = None
    memoria: dict = Field(default_factory=dict)


class NormaResposta(BaseModel):
    fonte: str
    dispositivo: str
    titulo: str | None = None
    trecho: str
    score: float


class FonteChip(BaseModel):
    """Chip de fonte exibido junto à resposta (calculado × norma)."""

    tipo: str  # "indicador" | "norma"
    rotulo: str
    detalhe: str | None = None
    source_ref: SourceRef | None = None


class DadoIncompleto(BaseModel):
    tipo: str
    codigo: str
    mensagem: str
    periodo_esperado: str | None = None
    periodo_encontrado: str | None = None


class UsoInfo(BaseModel):
    modelo: str
    tokens_entrada: int
    tokens_saida: int
    latencia_ms: int


class VerificacaoOut(BaseModel):
    """Laudo do guardrail G6 — todo número da prosa foi casado com o que a plataforma deu.

    Estruturado **e** visível: o aviso correspondente também é anexado ao texto da
    resposta. Um campo que a tela pode ignorar seria exatamente "publicar em silêncio".
    """

    status: str = Field(description="'ok' | 'sinalizado'.")
    total_citados: int = Field(description="Números fiscais encontrados na prosa.")
    com_lastro: int = Field(description="Quantos casaram com valor devolvido pela plataforma.")
    sem_lastro: list[str] = Field(
        default_factory=list,
        description="Os que não casaram — sinalizados, nunca publicados em silêncio.",
    )


class RespostaOut(BaseModel):
    """Resposta fundamentada do assistente (perguntar ou resumo-executivo)."""

    conversa_id: uuid.UUID
    tipo: str
    ente: str
    ente_nome: str | None = None
    periodo: str | None = None
    as_of: datetime | None = None
    titulo: str | None = None
    pergunta: str
    resposta: str
    recusa: bool
    dado_disponivel: bool
    fatos: list[FatoResposta] = Field(default_factory=list)
    normas: list[NormaResposta] = Field(default_factory=list)
    fontes: list[FonteChip] = Field(default_factory=list)
    dados_incompletos: list[DadoIncompleto] = Field(default_factory=list)
    uso: UsoInfo
    source_refs: list[SourceRef] = Field(default_factory=list)
    verificacao: VerificacaoOut | None = Field(
        default=None,
        description=(
            "Verificação de saída (G6). Ausente na recusa honesta, que não cita número."
        ),
    )
    gerado_em: datetime


class ConversaResumo(BaseModel):
    id: uuid.UUID
    tipo: str
    cod_ibge: str | None = None
    periodo: str | None = None
    pergunta: str
    resposta: str
    recusa: bool
    modelo: str | None = None
    criado_em: datetime


class ConversasOut(BaseModel):
    itens: list[ConversaResumo] = Field(default_factory=list)


class UsoResumoOut(BaseModel):
    """Consumo de IA da organização no mês (alimenta a cota do plano)."""

    mes: str
    consultas: int
    tokens_entrada: int
    tokens_saida: int
    gerado_em: datetime
