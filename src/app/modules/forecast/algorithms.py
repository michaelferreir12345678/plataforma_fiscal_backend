"""Núcleo de previsão — Python puro, determinístico, sem dependência científica.

Três camadas de modelo, todas devolvendo **intervalo de confiança** (nunca número
único — regra de aceite da Sprint 14):

1. :func:`projecao_fechamento` — projeção de fechamento por *run-rate* (ritmo médio
   de variação período a período). Responde "onde o indicador termina o exercício".
2. :func:`holt_linear` — suavização exponencial dupla de Holt (nível + tendência).
   Captura séries com tendência sem sazonalidade explícita.
3. :func:`regressao_exogenas` — regressão linear múltipla com **variáveis exógenas**
   (FPM, IPCA, Selic) + tendência, resolvida pelas equações normais. É a forma
   reduzida da família SARIMA/regressão dinâmica adequada a séries fiscais curtas;
   **consome de fato** as exógenas de ``silver`` (ver ``series.py``).

O IC sai do erro-padrão dos resíduos, alargando com o horizonte, e nunca degenera
(piso de banda), garantindo o invariante ``ic_inferior < previsto < ic_superior``.

Nota de engenharia (MVP pragmático, §3 CLAUDE.md): estas implementações vivem atrás
de uma fronteira limpa (``ResultadoModelo``); migrar para ``statsmodels`` depois não
afeta os consumidores.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# z ~ N(0,1) para nível de confiança de 95% (bicaudal).
Z_95 = 1.959963984540054
# Piso da meia-banda do IC, como fração de |valor|, para nunca colapsar em ponto.
_PISO_BANDA_REL = 0.01


@dataclass(frozen=True)
class PontoPrevisto:
    """Um passo do horizonte: previsão + intervalo (invariante inf ≤ previsto ≤ sup)."""

    passo: int  # 1 = próximo período
    valor_previsto: float
    ic_inferior: float
    ic_superior: float


@dataclass
class ResultadoModelo:
    """Saída padrão de qualquer modelo — o serviço não sabe qual algoritmo rodou."""

    modelo: str
    pontos: list[PontoPrevisto]
    memoria: dict[str, object] = field(default_factory=dict)


class ModeloInsuficiente(ValueError):
    """Série curta/degenerada demais para o modelo pedido."""


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def _media(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _banda(valor: float, erro_padrao: float, passo: int, z: float = Z_95) -> tuple[float, float]:
    """Meia-banda do IC alargando com o horizonte; nunca degenera em ponto."""
    escala = erro_padrao * math.sqrt(passo)
    meia = z * escala
    piso = abs(valor) * _PISO_BANDA_REL
    if meia < piso:
        meia = piso
    if meia <= 0.0:  # série constante e valor ~0: banda mínima absoluta
        meia = 1.0
    return valor - meia, valor + meia


def _pontos(
    previstos: list[float], erro_padrao: float, z: float = Z_95
) -> list[PontoPrevisto]:
    pontos: list[PontoPrevisto] = []
    for i, valor in enumerate(previstos, start=1):
        inf, sup = _banda(valor, erro_padrao, i, z)
        pontos.append(
            PontoPrevisto(passo=i, valor_previsto=valor, ic_inferior=inf, ic_superior=sup)
        )
    return pontos


# --------------------------------------------------------------------------- #
# 1) Projeção de fechamento (run-rate)
# --------------------------------------------------------------------------- #
def projecao_fechamento(
    serie: list[float], horizonte: int, *, z: float = Z_95
) -> ResultadoModelo:
    """Projeta por ritmo médio de variação (Δ médio período a período).

    Adequada à leitura de gestor "se mantiver o ritmo, fecha o ano em X". O IC nasce
    do desvio dos incrementos observados.
    """
    if len(serie) < 2:
        raise ModeloInsuficiente("Fechamento exige ao menos 2 observações.")
    deltas = [serie[i] - serie[i - 1] for i in range(1, len(serie))]
    delta_medio = _media(deltas)
    var = _media([(d - delta_medio) ** 2 for d in deltas]) if len(deltas) > 1 else 0.0
    erro_padrao = math.sqrt(var)
    ultimo = serie[-1]
    previstos = [ultimo + delta_medio * i for i in range(1, horizonte + 1)]
    return ResultadoModelo(
        modelo="fechamento",
        pontos=_pontos(previstos, erro_padrao, z),
        memoria={
            "metodo": "run-rate (incremento medio periodo a periodo)",
            "delta_medio": delta_medio,
            "erro_padrao_incremento": erro_padrao,
            "ultimo_observado": ultimo,
            "n_obs": len(serie),
        },
    )


# --------------------------------------------------------------------------- #
# 2) Holt (suavização exponencial dupla: nível + tendência)
# --------------------------------------------------------------------------- #
def holt_linear(
    serie: list[float],
    horizonte: int,
    *,
    alpha: float = 0.5,
    beta: float = 0.3,
    z: float = Z_95,
) -> ResultadoModelo:
    """Método de Holt (tendência linear amortecida por suavização exponencial).

    ``alpha`` suaviza o nível; ``beta``, a tendência. O erro-padrão vem dos resíduos
    de ajuste um-passo-à-frente (*in-sample*).
    """
    if len(serie) < 2:
        raise ModeloInsuficiente("Holt exige ao menos 2 observações.")
    nivel = serie[0]
    tendencia = serie[1] - serie[0]
    residuos: list[float] = []
    for t in range(1, len(serie)):
        previsto = nivel + tendencia  # previsão um-passo-à-frente
        residuos.append(serie[t] - previsto)
        nivel_ant = nivel
        nivel = alpha * serie[t] + (1 - alpha) * (nivel + tendencia)
        tendencia = beta * (nivel - nivel_ant) + (1 - beta) * tendencia
    erro_padrao = _erro_padrao(residuos, n_params=2)
    previstos = [nivel + tendencia * h for h in range(1, horizonte + 1)]
    return ResultadoModelo(
        modelo="holt_winters",
        pontos=_pontos(previstos, erro_padrao, z),
        memoria={
            "metodo": "Holt (suavizacao exponencial dupla: nivel + tendencia)",
            "alpha": alpha,
            "beta": beta,
            "nivel_final": nivel,
            "tendencia_final": tendencia,
            "erro_padrao_residuos": erro_padrao,
            "n_obs": len(serie),
        },
    )


def _erro_padrao(residuos: list[float], *, n_params: int) -> float:
    """Erro-padrão dos resíduos com correção de graus de liberdade."""
    gl = max(len(residuos) - n_params, 1)
    return math.sqrt(sum(r * r for r in residuos) / gl)


# --------------------------------------------------------------------------- #
# 3) Regressão linear múltipla com exógenas (equações normais)
# --------------------------------------------------------------------------- #
def _resolver(matriz: list[list[float]], vetor: list[float]) -> list[float]:
    """Resolve ``A·x = b`` por eliminação de Gauss com pivô parcial (A quadrada)."""
    n = len(vetor)
    aug = [row[:] + [vetor[i]] for i, row in enumerate(matriz)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[piv][col]) < 1e-12:
            raise ModeloInsuficiente("Sistema singular (colineares/insuficientes).")
        aug[col], aug[piv] = aug[piv], aug[col]
        pivo = aug[col][col]
        for r in range(n):
            if r == col:
                continue
            fator = aug[r][col] / pivo
            for c in range(col, n + 1):
                aug[r][c] -= fator * aug[col][c]
    return [aug[i][n] / aug[i][i] for i in range(n)]


@dataclass(frozen=True)
class MatrizExogenas:
    """Desenho da regressão: nomes e valores das exógenas alinhados à série.

    ``historico[t]`` e ``futuro[h]`` têm o mesmo comprimento de ``nomes`` (uma coluna
    por exógena). A tendência e o intercepto são adicionados internamente.
    """

    nomes: list[str]
    historico: list[list[float]]
    futuro: list[list[float]]


def regressao_exogenas(
    serie: list[float],
    exog: MatrizExogenas,
    horizonte: int,
    *,
    z: float = Z_95,
) -> ResultadoModelo:
    """Regressão ``y ~ intercepto + tendência + Σ exógenas`` (OLS).

    Só entram as exógenas que couberem nos graus de liberdade (n ≥ k+2); as demais são
    descartadas com registro na memória. Isso mantém a estabilidade numérica em séries
    fiscais curtas sem deixar de **consumir** as fontes (``silver.bcb_indice``,
    ``silver.tesouro_fpm``).
    """
    n = len(serie)
    if n < 3:
        raise ModeloInsuficiente("Regressão exige ao menos 3 observações.")

    # Orçamento de regressores: intercepto + tendência + exógenas ≤ n-2.
    max_exog = max(n - 2 - 2, 0)
    nomes_usados = list(exog.nomes[:max_exog])
    descartadas = list(exog.nomes[max_exog:])

    def linha_hist(t: int) -> list[float]:
        base = [1.0, float(t)]  # intercepto, tendência (0..n-1)
        return base + [exog.historico[t][j] for j in range(len(nomes_usados))]

    k = 2 + len(nomes_usados)
    xt_x = [[0.0] * k for _ in range(k)]
    xt_y = [0.0] * k
    for t in range(n):
        xr = linha_hist(t)
        for a in range(k):
            xt_y[a] += xr[a] * serie[t]
            for b in range(k):
                xt_x[a][b] += xr[a] * xr[b]

    coefs = _resolver(xt_x, xt_y)

    ajustados = [sum(coefs[a] * linha_hist(t)[a] for a in range(k)) for t in range(n)]
    residuos = [serie[t] - ajustados[t] for t in range(n)]
    erro_padrao = _erro_padrao(residuos, n_params=k)

    ss_res = sum(r * r for r in residuos)
    media_y = _media(serie)
    ss_tot = sum((y - media_y) ** 2 for y in serie)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    previstos: list[float] = []
    for h in range(1, horizonte + 1):
        t = n - 1 + h
        linha = [1.0, float(t)] + [exog.futuro[h - 1][j] for j in range(len(nomes_usados))]
        previstos.append(sum(coefs[a] * linha[a] for a in range(k)))

    coef_map = {"intercepto": coefs[0], "tendencia": coefs[1]}
    for j, nome in enumerate(nomes_usados):
        coef_map[nome] = coefs[2 + j]

    return ResultadoModelo(
        modelo="regressao_exogenas",
        pontos=_pontos(previstos, erro_padrao, z),
        memoria={
            "metodo": "OLS: y ~ intercepto + tendencia + exogenas (FPM/IPCA/Selic)",
            "exogenas_usadas": nomes_usados,
            "exogenas_descartadas_por_gl": descartadas,
            "coeficientes": coef_map,
            "r2": r2,
            "erro_padrao_residuos": erro_padrao,
            "n_obs": n,
        },
    )
