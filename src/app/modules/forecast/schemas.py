"""Schemas da Sprint 14 (Previsões & Cenários).

Invariante de produto: **toda** projeção carrega intervalo de confiança
(``ic_inferior``/``ic_superior``) — nunca número único. Todo número traz
``source_ref`` + ``as_of`` e a projeção expõe a memória de cálculo (modelo, params).
"""

from __future__ import annotations

import uuid
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
class NovoContratoDivida(BaseModel):
    """Premissas de uma operação de crédito hipotética (Sprint G1, escopo mínimo).

    Calcula o impacto do principal no teto de 120%/200% da RCL. **Não persiste**: o
    contrato hipotético não vira linha em nenhuma tabela — uma operação real nasce no
    SADIPEM, não num cenário "e se?".
    """

    principal_rs: float = Field(gt=0, description="Valor do novo contrato de dívida (R$).")
    prazo_meses: int = Field(gt=0, le=480, description="Prazo total da operação, em meses.")
    carencia_meses: int = Field(
        default=0, ge=0, le=120, description="Carência antes da 1ª amortização, em meses."
    )
    taxa_aa_pct: float = Field(
        default=0.0, ge=0, le=100, description="Taxa de juros anual do contrato (%)."
    )


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
    # Robustez (Sprint G1): parâmetros com significado fiscal próprio, distintos dos
    # choques genéricos — cada um audita separado no resultado, em vez de se esconder
    # dentro de "crescimento do indicador".
    fundeb_variacao_pct: float | None = Field(
        default=None,
        description=(
            "Variação assumida do FUNDEB (%), separada do choque de FPM — aplica-se à "
            "projeção de RCL/receita (a série agregada não decompõe por fonte de repasse)."
        ),
    )
    reajuste_folha_pct: float | None = Field(
        default=None,
        description=(
            "Reajuste/variação de folha (revisão salarial, admissões), distinto do choque "
            "genérico de pessoal — só se aplica ao indicador 'pessoal'."
        ),
    )
    novo_contrato_divida: NovoContratoDivida | None = Field(
        default=None,
        description=(
            "Operação de crédito hipotética (principal/prazo/carência/taxa). Calcula o "
            "impacto no teto de 120%/200% da RCL sem persistir o contrato — o SADIPEM "
            "continua sendo a fonte de uma operação real."
        ),
    )
    salvar: bool = Field(default=False, description="Persistir o cenário em op.cenario.")
    #: Salvar sobre um cenário existente **cria versão** — não sobrescreve. Ausente, cria
    #: um cenário novo. A distinção é o que preserva "o que eu levei à reunião de agosto".
    cenario_id: str | None = Field(
        default=None, description="Cenário a versionar; ausente cria um novo."
    )


class LimiteImpacto(BaseModel):
    indicador: str
    descricao: str | None = None
    sentido: str  # teto | piso
    limite_pct: Decimal
    valor_limite_rs: Decimal | None = None  # teto/piso em R$ sobre a base projetada
    pct_projetado: Decimal | None = None  # para indicadores expressos em % RCL
    faixa: str | None = None
    cruza: bool = False


class ImpactoContratoDivida(BaseModel):
    """Quanto um novo contrato hipotético consome do teto de 120%/200% da RCL (DCL).

    A operação conta na Dívida Consolidada Líquida a partir da contratação — a carência
    adia a amortização, não a contagem no teto. É por isso que ``pct_rcl_adicional`` entra
    de uma vez, sem diluir pelo prazo.
    """

    principal_rs: Decimal
    prazo_meses: int
    carencia_meses: int
    taxa_aa_pct: Decimal
    #: ``None`` quando o acervo não tem RCL para converter o principal em pontos
    #: percentuais — ausência, não zero (zero anunciaria impacto nenhum).
    pct_rcl_adicional: Decimal | None = None
    pct_rcl_resultante: Decimal | None = None
    teto_pct: Decimal
    faixa: str | None = None
    cruza: bool = False
    base_rs: Decimal | None = None
    base_periodo: str | None = None
    fundamento: str


class CenarioSimularResponse(BaseModel):
    persistido: bool
    cenario_id: str | None = None
    #: Número da versão criada — salvar sobre um cenário existente incrementa, não substitui.
    versao: int | None = None
    cod_ibge: str
    indicador: str
    horizonte: int
    base: ProjecaoResponse
    cenario: ProjecaoResponse
    impacto_limites: list[LimiteImpacto]
    impacto_minimos: list[LimiteImpacto]
    #: Só presente quando ``indicador == "divida"`` e o gestor informou
    #: ``novo_contrato_divida`` — o simulador estruturado de operação de crédito (Sprint G1).
    impacto_contrato_divida: ImpactoContratoDivida | None = None
    memoria: dict
    source_refs: list[SourceRef]


class CenarioSalvo(BaseModel):
    id: str
    ente: str
    indicador: str
    nome: str
    parametros: dict
    criado_em: datetime


# --------------------------------------------------------------------------- #
# Cenários salvos (Sprint C2) — versão, procedência, comparação
# --------------------------------------------------------------------------- #
class ProcedenciaCenario(BaseModel):
    """Sobre qual dado a versão foi calculada.

    É o que separa **reproduzir** de **recalcular**. Sem isto, reabrir um cenário de agosto
    em outubro mostra o resultado congelado com a mesma cara de um cálculo corrente — e as
    mesmas premissas, hoje, dariam outro número.
    """

    as_of: datetime | None = None
    #: Entregas que alimentaram a série histórica: ``{"2025-B6": "1", ...}``.
    versoes_entrega: dict[str, Any] = Field(default_factory=dict)
    #: Premissas macro vigentes quando o cenário foi salvo. "Aceitei o IPCA observado" muda
    #: de significado quando o observado muda; sem o valor de então, não se reproduz nada.
    premissas_observadas: dict[str, Any] = Field(default_factory=dict)
    registrada: bool = Field(
        default=True,
        description="Falso para versões migradas do formato anterior, sem procedência.",
    )


class VersaoCenario(BaseModel):
    versao: int
    nome: str
    parametros: dict
    modelo: str | None = None
    horizonte: int | None = None
    nota: str | None = None
    procedencia: ProcedenciaCenario
    criado_em: datetime
    #: E-mail de quem gravou esta versão. ``None`` quando o usuário foi removido ou a
    #: versão é anterior ao registro de autoria. Gravado desde a Sprint C2 em
    #: ``op.cenario_versao.criado_por`` e nunca projetado até aqui (Sprint G1).
    criado_por: str | None = None


class CenarioDetalhe(BaseModel):
    """Cabeçalho do cenário mais o histórico de versões."""

    id: str
    ente: str
    indicador: str
    nome: str
    versao_atual: int
    arquivado: bool = False
    criado_em: datetime
    atualizado_em: datetime | None = None
    #: E-mail de quem criou o cenário (Sprint G1) — mesma ausência possível que em
    #: ``VersaoCenario.criado_por``.
    criado_por: str | None = None
    versoes: list[VersaoCenario] = Field(default_factory=list)


class DivergenciaCenario(BaseModel):
    """O que mudou entre o resultado guardado e o mesmo cálculo com o dado de hoje.

    ``diverge=False`` com ``comparavel=False`` não é "está tudo igual": é "não deu para
    comparar". Os dois casos produziriam a mesma tela se colapsados num booleano só.
    """

    comparavel: bool
    diverge: bool = False
    motivo: str | None = None
    valor_guardado: Decimal | None = None
    valor_recalculado: Decimal | None = None
    delta: Decimal | None = None
    entregas_novas: list[str] = Field(default_factory=list)


class CenarioAbertoResponse(BaseModel):
    """Cenário reaberto: o que foi decidido, o que o dado diz hoje, e a diferença."""

    cenario: CenarioDetalhe
    versao: VersaoCenario
    #: Exatamente o que foi salvo — a peça que embasou a decisão.
    guardado: dict | None = None
    #: O mesmo cenário rodado agora. ``None`` quando o recálculo não é possível.
    recalculado: CenarioSimularResponse | None = None
    divergencia: DivergenciaCenario


class CenarioComparadoItem(BaseModel):
    cenario_id: str
    nome: str
    versao: int
    encontrado: bool = True
    motivo_ausencia: str | None = None
    indicador: str | None = None
    unidade: str | None = None
    modelo: str | None = None
    parametros: dict = Field(default_factory=dict)
    #: Série projetada, período a período, para o eixo comum.
    projecao: list[PontoProjecao] = Field(default_factory=list)
    valor_final: Decimal | None = None
    espaco_fiscal: EspacoFiscalOut | None = None
    criado_em: datetime | None = None


class ComparacaoCenariosResponse(BaseModel):
    """Cenários lado a lado no mesmo eixo temporal.

    ``periodos`` é a **interseção** dos horizontes, não a união: comparar um cenário de 4
    períodos com um de 12 num eixo de 12 deixaria o primeiro terminando no meio do gráfico,
    e a leitura natural — "este cenário cai no fim" — seria falsa.
    """

    cod_ibge: str
    indicador: str | None = None
    periodos: list[str] = Field(default_factory=list)
    itens: list[CenarioComparadoItem] = Field(default_factory=list)
    aviso: str | None = None


class CenarioRenomearRequest(BaseModel):
    nome: str = Field(min_length=1, max_length=120)


class CenarioDuplicarRequest(BaseModel):
    """Nome do clone; ausente vira "Cópia de {nome do original}"."""

    nome: str | None = Field(default=None, max_length=120)


class ComparacaoCenariosRequest(BaseModel):
    """Cenários a comparar.

    O limite de 6 não é arbitrário: acima disso o gráfico deixa de ser legível e a paleta
    categórica acaba — comparar dez curvas é não comparar nenhuma.
    """

    cenario_ids: list[uuid.UUID] = Field(min_length=2, max_length=6)
