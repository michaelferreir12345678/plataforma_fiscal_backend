"""Sanidade da série antes de projetar — porque o modelo não sabe recusar.

Um modelo estatístico aceita qualquer entrada e devolve saída com a mesma cara de
legitimidade. A série de despesa com pessoal do ente 2307650 tem um ponto de **324,49% da
RCL** — impossível: nenhum ente gasta três vezes a receita corrente líquida com folha. A
regressão consumiu esse ponto sem reclamar e projetou 2,29%, um número igualmente
impossível, agora com intervalo de confiança e aparência de análise.

A causa está a montante: o ente publicou a RCL do 1º quadrimestre de 2023 como R$ 152,1
milhões e, na entrega do quadrimestre seguinte, republicou o mesmo acumulado como R$
1.031,3 milhões — corrigiu. **O RGF republica os quadrimestres anteriores a cada entrega, e
é assim que a retificação chega**, não como versão nova do mesmo período. A plataforma
materializou o primeiro número e não voltou. São 63 quadrimestres republicados com valor
diferente no acervo, 5 deles com o maior valendo mais que o dobro do menor.

Corrigir isso é trabalho de ingestão e está registrado como achado. O que **este** módulo
faz é impedir que a projeção transforme o defeito em previsão: um ponto impossível é
excluído do treino e o fato é registrado na memória do cálculo, visível na resposta.

## Excluir, não recusar — e nunca em silêncio

Recusar a projeção inteira porque 1 de 12 pontos está corrompido deixaria o gestor sem
resposta onde há 11 observações boas. Aceitar em silêncio é pior: produz número plausível
a partir de lixo. O meio-termo defensável é excluir o ponto, projetar com o resto e
**dizer** — quem lê a projeção precisa saber que ela ignorou uma observação, qual, e por quê.

## O que conta como impossível

Só o que o domínio garante ser impossível, nunca o que é apenas incomum:

* indicador expresso em % da RCL fora de ``[0, 100]`` — um ente pode gastar 60% da RCL com
  pessoal (ilegal, mas real e importante de mostrar); não pode gastar 324%;
* valor não-positivo onde a grandeza é estritamente positiva (RCL, receita).

Um ponto meramente distante da tendência **não** é excluído. Série fiscal tem quebra
estrutural legítima — mudança de mandato, fim de convênio, recomposição de folha — e
suavizar isso seria apagar justamente o que o gestor precisa ver.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Teto do que é fisicamente possível num indicador expresso em % da RCL. Acima disso o
#: número não descreve o ente: descreve um defeito no denominador.
MAX_PCT_RCL = 100.0
MIN_PCT_RCL = 0.0

#: Mínimo de observações para projetar. Abaixo disso qualquer modelo é extrapolação de
#: dois pontos, e a exclusão de um ponto ruim pode nos levar a este piso.
MIN_OBSERVACOES = 2


@dataclass(frozen=True)
class PontoExcluido:
    """Uma observação que o treino não pode usar, com a razão dita em português."""

    periodo: str
    valor: float
    motivo: str


@dataclass
class SerieSaneada:
    """Série pronta para o treino, com o registro do que foi deixado de fora."""

    periodos: list[str]
    valores: list[float]
    excluidos: list[PontoExcluido] = field(default_factory=list)

    @property
    def houve_exclusao(self) -> bool:
        return bool(self.excluidos)

    def memoria(self) -> dict:
        """O que a resposta precisa dizer sobre a limpeza — nunca omitir que houve uma."""
        if not self.excluidos:
            return {"pontos_excluidos": 0}
        return {
            "pontos_excluidos": len(self.excluidos),
            "exclusoes": [
                {"periodo": p.periodo, "valor": p.valor, "motivo": p.motivo}
                for p in self.excluidos
            ],
            "aviso": (
                "A projeção ignorou observação(ões) impossível(is) da série histórica. "
                "O valor consta do acervo e aparece nas telas de indicador; aqui foi "
                "excluído do treino para não contaminar a projeção."
            ),
        }


def _motivo_pct_rcl(valor: float) -> str | None:
    if valor > MAX_PCT_RCL:
        return (
            f"{valor:.2f}% da RCL é impossível (acima de {MAX_PCT_RCL:.0f}%). "
            "Denominador provavelmente corrompido na entrega — ver a RCL do período."
        )
    if valor < MIN_PCT_RCL:
        return f"{valor:.2f}% da RCL é negativo, o que a grandeza não admite."
    return None


def _motivo_positiva(valor: float) -> str | None:
    if valor <= 0:
        return "Valor não-positivo numa grandeza estritamente positiva (receita/RCL)."
    return None


def sanear(
    periodos: list[str], valores: list[float], *, unidade: str
) -> SerieSaneada:
    """Remove do treino as observações que o domínio garante serem impossíveis.

    Não toca em outliers meramente distantes: quebra estrutural em série fiscal é
    informação, não ruído. Se a limpeza deixar menos observações do que qualquer modelo
    exige, a série volta **intacta** — nesse caso é melhor projetar sobre dado suspeito e
    dizer, do que devolver ausência de resposta com a mesma justificativa.
    """
    motivo_de = _motivo_pct_rcl if unidade == "PCT_RCL" else _motivo_positiva

    mantidos_p: list[str] = []
    mantidos_v: list[float] = []
    excluidos: list[PontoExcluido] = []
    for periodo, valor in zip(periodos, valores, strict=True):
        motivo = motivo_de(valor)
        if motivo is None:
            mantidos_p.append(periodo)
            mantidos_v.append(valor)
        else:
            excluidos.append(PontoExcluido(periodo, valor, motivo))

    if not excluidos:
        return SerieSaneada(periodos, valores)

    if len(mantidos_v) < MIN_OBSERVACOES:
        intacta = SerieSaneada(periodos, valores)
        intacta.excluidos = []
        # A série inteira é suspeita e ainda assim é tudo o que existe. Devolvê-la sem o
        # registro seria esconder; registrar como exclusão seria mentir sobre o que o
        # treino usou. O aviso vai por outro caminho: o chamador vê `houve_exclusao=False`
        # com menos pontos válidos do que o mínimo e decide.
        return intacta

    return SerieSaneada(mantidos_p, mantidos_v, excluidos)


def contagem_suspeita(valores: list[float], *, unidade: str) -> int:
    """Quantas observações da série são impossíveis — para quem só quer saber se há."""
    motivo_de = _motivo_pct_rcl if unidade == "PCT_RCL" else _motivo_positiva
    return sum(1 for v in valores if motivo_de(v) is not None)
