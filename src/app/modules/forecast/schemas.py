"""Schemas da Sprint 14 (Previsões & Cenários).

Invariante de produto: **toda** projeção carrega intervalo de confiança
(``ic_inferior``/``ic_superior``) — nunca número único. Todo número traz
``source_ref`` + ``as_of`` e a projeção expõe a memória de cálculo (modelo, params).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

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


class EspacoFiscalOut(BaseModel):
    """Quanto ainda cabe (ou quanto falta cortar) na posição projetada.

    ``margem_pp`` e ``margem_rs`` são **sempre positivos**; ``situacao`` é o que diz se
    são folga ou excesso. Um valor negativo rotulado "margem" convidaria à leitura errada
    exatamente no caso em que o erro custa mais caro.
    """

    indicador: str
    sentido: str
    situacao: str  # folga | excedido | nao_aplicavel
    limite_pct: Decimal
    projetado_pct: Decimal
    margem_pp: Decimal
    margem_rs: Decimal | None = None
    base_rs: Decimal | None = None
    base_nome: str = "rcl"
    #: De qual período a base saiu — observada, ao contrário de ``periodo_alvo``.
    base_periodo: str | None = None
    periodo_alvo: str | None = None


class ReconducaoOut(BaseModel):
    """Cronograma de redução que a LRF impõe a quem excedeu o limite de pessoal.

    Art. 23: excesso eliminado em dois quadrimestres, ao menos um terço no primeiro. Não é
    meta de gestão — é obrigação, e o descumprimento aciona as vedações do §3º.
    """

    aplicavel: bool
    excesso_pp: Decimal
    excesso_rs: Decimal | None = None
    primeiro_quadrimestre_pp: Decimal
    primeiro_quadrimestre_rs: Decimal | None = None
    segundo_quadrimestre_pp: Decimal
    segundo_quadrimestre_rs: Decimal | None = None
    fundamento: str


class PremissaObservada(BaseModel):
    """Premissa de cenário como o mundo a reporta — com data, fonte e n de observações.

    ``observado=None`` significa que a série não sustenta o cálculo, e ``motivo`` diz o
    porquê. A tela então **pede** o valor em vez de sugerir um: uma premissa inventada é
    indistinguível de uma medida depois que entra no formulário.
    """

    chave: str
    rotulo: str
    unidade: str
    observado: Decimal | None = None
    motivo: str | None = None
    referencia: str | None = None
    fonte: str | None = None
    n_observacoes: int | None = None


class PremissasResponse(BaseModel):
    cod_ibge: str
    premissas: list[PremissaObservada]
    nota: str


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
    #: Margem até o limite na posição projetada — o número que transforma "cruza em
    #: 2026-B2" numa decisão. Ausente quando o indicador não tem limite comparável.
    espaco_fiscal: EspacoFiscalOut | None = None
    reconducao: ReconducaoOut | None = None
    memoria: dict
    source_ref: SourceRef


class ModeloComparado(BaseModel):
    """Uma das três camadas de projeção, lado a lado com as outras (Sprint 25E)."""

    modelo: str
    rotulo: str
    disponivel: bool
    motivo_indisponivel: str | None = None
    escolhido: bool = False
    valor_final: Decimal | None = None
    ic_inferior_final: Decimal | None = None
    ic_superior_final: Decimal | None = None
    amplitude_ic_media: Decimal | None = None
    erro_padrao: Decimal | None = None
    r2: Decimal | None = None
    n_obs: int | None = None
    cruza_limite: bool = False
    periodo_cruzamento: str | None = None
    memoria: dict = Field(default_factory=dict)


class ComparacaoModelosResponse(BaseModel):
    """Comparação das camadas para o mesmo ente/indicador/horizonte.

    **Não há backtest**: com séries de poucos períodos, separar treino e teste produziria
    um erro medido em um único ponto — ruído apresentado como evidência. O que se compara
    aqui é viabilidade, dispersão do ajuste e o que cada modelo projeta.
    """

    cod_ibge: str
    indicador: str
    descricao: str
    unidade: str
    horizonte: int
    periodos_projetados: list[str] = Field(default_factory=list)
    n_periodos_historicos: int
    criterio_escolha: str
    modelos: list[ModeloComparado] = Field(default_factory=list)
    exogenas_fontes: dict[str, Any] = Field(default_factory=dict)
    aviso: str
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
