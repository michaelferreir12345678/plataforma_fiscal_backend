"""Schemas do job de ingestão (Central de Dados, Sprint 24)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IngestJobCreate(BaseModel):
    """Pedido de execução. ``anos`` para run/backfill (SICONFI); ``periodos`` para replay."""

    fonte: str = Field(description="Ex.: siconfi_rreo, siconfi_rgf, ibge_populacao, ...")
    tipo: str = Field(default="backfill", description="run | backfill | replay.")
    entes: list[str] = Field(default_factory=list, description="Códigos IBGE.")
    anos: list[int] = Field(default_factory=list, description="Exercícios (run/backfill).")
    periodos: list[str] = Field(
        default_factory=list, description="Períodos canônicos (replay, ex.: 2024-B6)."
    )
    versao: str | None = None
    parametros: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Parâmetros completos do conector. Chaves conhecidas de RunRequest ficam no "
            "topo; parâmetros específicos de arquivo ficam em `params`."
        ),
    )
    incluir_municipios: bool = Field(
        default=False,
        description=(
            "Para cada ente **estadual** da lista, inclui também os municípios daquela UF "
            "que estão no escopo do usuário. Municípios fora do escopo não entram, e a "
            "contagem do que ficou de fora volta na resposta."
        ),
    )
    confirmar: bool = Field(
        default=False, description="Obrigatório quando a estimativa passa do limiar."
    )


class IngestJobItem(BaseModel):
    """Resultado de uma unidade de trabalho (ente × período/ano)."""

    ente: str
    chave: str  # ano ou período
    ok: bool
    erro: str | None = None
    silver_rows: int = 0
    detalhe: dict[str, Any] | None = None


class IngestJobResultado(BaseModel):
    """Resumo pós-job: o que recalculou e o delta de cobertura."""

    itens: list[IngestJobItem] = Field(default_factory=list)
    indicadores_recalculados: list[str] = Field(default_factory=list)
    cobertura_antes: int | None = None
    cobertura_depois: int | None = None
    delta_cobertura: int | None = None
    erro_sistema: dict[str, Any] | None = None
    resumo_execucao: dict[str, Any] | None = None


class IngestionLogOut(BaseModel):
    """Entrada estruturada de ``gold.ingestion_log`` ligada ao job."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    fonte: str
    cod_ibge: str | None = None
    periodo: str | None = None
    versao: str | None = None
    status: str
    mensagem: str | None = None
    ts: datetime


class IngestJobOut(BaseModel):
    """Estado completo de um job (contrato de progresso do frontend)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    criado_por: uuid.UUID | None = None
    fonte: str
    tipo: str
    entes: list[str] = Field(default_factory=list)
    periodos: list[str] = Field(default_factory=list)
    parametros: dict | None = None
    status: str
    progresso_pct: int = Field(ge=0, le=100)
    itens_total: int = Field(ge=0)
    itens_ok: int = Field(ge=0)
    itens_erro: int = Field(ge=0)
    tentativas: int = Field(ge=0)
    erro_resumo: str | None = None
    log_ref: str | None = None
    resultado: IngestJobResultado | None = None
    logs: list[IngestionLogOut] = Field(default_factory=list)
    criado_em: datetime | None = None
    iniciado_em: datetime | None = None
    terminado_em: datetime | None = None


class IngestJobCreateResult(BaseModel):
    """Resposta de criação: ou pede confirmação (ação custosa), ou devolve o job (202)."""

    precisa_confirmacao: bool = False
    estimativa_itens: int
    limiar: int
    job: IngestJobOut | None = None
    #: Quantos municípios a expansão por UF deixou de fora por não estarem no escopo.
    #: Voltar esse número é o que impede a exclusão de ser silenciosa: quem pediu "todos
    #: os municípios" precisa saber que recebeu um subconjunto, e quantos faltaram.
    municipios_fora_do_escopo: int = 0
    #: Quantos entram na carga depois da expansão — a conta que o gestor confirma.
    entes_incluidos: int = 0


class SaudeFila(BaseModel):
    """Estado do consumidor da fila — o que faltava para 'Na fila' não mentir.

    Um job aceito e entregue ao Redis fica ``na_fila`` para sempre quando nenhum worker
    está escutando, e a tela não tinha como distinguir isso de "aguardando a vez".
    """

    consumidores: int = Field(description="Workers RQ registrados na fila de ingestão.")
    consumidores_vivos: int = Field(
        default=0,
        description=(
            "Workers com heartbeat recente. Um worker morto no meio de um job mantém o "
            "registro até o timeout (1h), então contar registro não prova consumo."
        ),
    )
    aguardando: int = Field(description="Jobs persistidos em na_fila.")
    executando: int = Field(description="Jobs com claim ativo.")
    fila_redis: int | None = Field(
        default=None, description="Entregas pendentes no Redis; nulo se indisponível."
    )
    redis_disponivel: bool = True
    detalhe: str | None = Field(
        default=None, description="Explicação legível quando algo impede o consumo."
    )

    @property
    def parada(self) -> bool:
        """Há trabalho esperando e nada indica que alguém vá pegá-lo."""
        return self.aguardando > 0 and self.consumidores_vivos == 0 and self.executando == 0


class RetificacaoItem(BaseModel):
    """Uma entrega que superou a versão anterior (retificação bitemporal)."""

    cod_ibge: str
    relatorio: str
    periodo: str
    versao_entrega: str
    homologada_em: datetime | None = None
    versoes_anteriores: int
