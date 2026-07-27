"""Schemas da Sprint 14 (Previsões & Cenários).

Invariante de produto: **toda** projeção carrega intervalo de confiança
(``ic_inferior``/``ic_superior``) — nunca número único. Todo número traz
``source_ref`` + ``as_of`` e a projeção expõe a memória de cálculo (modelo, params).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.shared.source_ref import SourceRef


class PontoHistorico(BaseModel):
    periodo: str
    valor: Decimal
    versao_entrega: str
    as_of: datetime | None = None
    source_ref: SourceRef


class PontoProjecao(BaseModel):
    """Passo do horizonte — sempre com IC (nunca só ``valor_previsto``)."""

    periodo_alvo: str
    passo: int
    valor_previsto: Decimal
    ic_inferior: Decimal
    ic_superior: Decimal
    teto_pct: Decimal | None = None
    faixa: str | None = None
    cruza_limite: bool = False


class CruzamentoLimite(BaseModel):
    """Marcador de cruzamento do limite ao longo do horizonte projetado."""

    aplicavel: bool = Field(description="Indicador tem limite legal comparável (% RCL).")
    cruza: bool = False
    periodo_cruzamento: str | None = None
    passo_cruzamento: int | None = None
    valor_no_cruzamento: Decimal | None = None
    teto_pct: Decimal | None = None
    indicador_limite: str | None = None
    esfera: str | None = None


class ProjecaoResponse(BaseModel):
    """Histórico + projeção + IC + marcador de cruzamento (endpoint GET /projecao)."""

    cod_ibge: str
    indicador: str
    descricao: str
    unidade: str
    modelo: str
    esfera: str | None = None
    nivel_confianca: Decimal
    horizonte: int
    as_of: datetime | None = None
    gerado_em: datetime | None = None
    historico: list[PontoHistorico]
    projecao: list[PontoProjecao]
    cruzamento: CruzamentoLimite
    memoria: dict
    source_ref: SourceRef


# --------------------------------------------------------------------------- #
# Cenário
# --------------------------------------------------------------------------- #
class CenarioSimularRequest(BaseModel):
    """Deltas em linguagem de gestor. Só persiste se ``salvar=True`` (aceite Sprint 14)."""

    nome: str = Field(default="Cenário sem título", max_length=120)
    horizonte: int = Field(default=4, ge=1, le=24)
    modelo: str | None = Field(
        default=None, description="Força um modelo; senão usa o melhor disponível."
    )
    # Premissas macroeconômicas (exógenas): variação anual assumida.
    ipca_aa_pct: float | None = Field(default=None, description="Inflação (IPCA) anual assumida %.")
    selic_aa_pct: float | None = Field(default=None, description="Selic anual assumida %.")
    fpm_variacao_pct: float | None = Field(
        default=None, description="Variação do FPM vs média histórica (%)."
    )
    # Premissas fiscais diretas.
    crescimento_indicador_pct: float | None = Field(
        default=None, description="Choque de crescimento do indicador por período (%)."
    )
    crescimento_rcl_pct: float | None = Field(
        default=None, description="Crescimento da RCL por período (%) — afeta % RCL e mínimos."
    )
    salvar: bool = Field(default=False, description="Persistir o cenário em op.cenario.")


class LimiteImpacto(BaseModel):
    indicador: str
    descricao: str | None = None
    sentido: str  # teto | piso
    limite_pct: Decimal
    valor_limite_rs: Decimal | None = None  # teto/piso em R$ sobre a base projetada
    pct_projetado: Decimal | None = None  # para indicadores expressos em % RCL
    faixa: str | None = None
    cruza: bool = False


class CenarioSimularResponse(BaseModel):
    persistido: bool
    cenario_id: str | None = None
    cod_ibge: str
    indicador: str
    horizonte: int
    base: ProjecaoResponse
    cenario: ProjecaoResponse
    impacto_limites: list[LimiteImpacto]
    impacto_minimos: list[LimiteImpacto]
    memoria: dict
    source_refs: list[SourceRef]


class CenarioSalvo(BaseModel):
    id: str
    ente: str
    indicador: str
    nome: str
    parametros: dict
    criado_em: datetime
