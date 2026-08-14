"""As métricas da ficha da IA-6, com o denominador de cada uma declarado.

Métrica sem denominador explícito é onde a avaliação mente sem querer: "95% de
fundamentação" pode significar coisas opostas conforme se conte sobre todas as respostas
ou só sobre as que citam número. Aqui cada taxa carrega o par (numerador, denominador) no
próprio laudo, e o relatório imprime os dois.

Uma escolha que vale explicar: **a taxa de alucinação é medida sobre as respostas que
citam número**, não sobre o conjunto todo. Diluí-la nas recusas — que não citam número e
portanto não podem alucinar — produziria uma taxa que melhora sozinha quando se acrescenta
pergunta impossível ao conjunto. O critério de aceite ("zero") é o mesmo nos dois
denominadores; o que muda é a honestidade de reportá-la.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from statistics import median
from typing import Any

from app.modules.evaluation.adversarial import JulgamentoAdversario
from app.modules.evaluation.conjunto import CATEGORIAS, Preco
from app.modules.evaluation.criterios import Julgamento


@dataclass(frozen=True)
class Taxa:
    """Uma taxa e os dois números que a produziram. Nunca só o percentual."""

    numerador: int
    denominador: int

    @property
    def valor(self) -> float:
        """Fração 0..1. Denominador zero devolve 0.0 — e o relatório mostra 'n/a'."""
        return 0.0 if self.denominador == 0 else self.numerador / self.denominador

    @property
    def aplicavel(self) -> bool:
        return self.denominador > 0

    def pct(self) -> str:
        if not self.aplicavel:
            return "n/a"
        return f"{self.valor * 100:.1f}% ({self.numerador}/{self.denominador})"

    def to_dict(self) -> dict[str, Any]:
        return {"numerador": self.numerador, "denominador": self.denominador, "valor": self.valor}


@dataclass(frozen=True)
class Latencia:
    p50_ms: int
    p95_ms: int
    max_ms: int
    media_ms: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Custo:
    """Custo da execução. ``preco_declarado=False`` ⇒ o número é 0 por falta de tabela."""

    tokens_entrada: int
    tokens_saida: int
    total_usd: str
    por_resposta_usd: str
    preco_declarado: bool
    fonte_preco: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Metricas:
    total: int
    por_categoria: dict[str, int]
    aprovacao: Taxa
    fundamentacao: Taxa
    #: O critério que não admite tolerância. Numerador tem de ser 0.
    alucinacao_numerica: Taxa
    recusa_correta: Taxa
    defasagem_sinalizada: Taxa
    adversarial: Taxa
    latencia: Latencia
    custo: Custo
    falhas: list[str] = field(default_factory=list)

    @property
    def alucinacao_zero(self) -> bool:
        return self.alucinacao_numerica.numerador == 0

    @property
    def recusas_todas_corretas(self) -> bool:
        return self.recusa_correta.numerador == self.recusa_correta.denominador

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "por_categoria": dict(self.por_categoria),
            "aprovacao": self.aprovacao.to_dict(),
            "fundamentacao": self.fundamentacao.to_dict(),
            "alucinacao_numerica": self.alucinacao_numerica.to_dict(),
            "recusa_correta": self.recusa_correta.to_dict(),
            "defasagem_sinalizada": self.defasagem_sinalizada.to_dict(),
            "adversarial": self.adversarial.to_dict(),
            "latencia": self.latencia.to_dict(),
            "custo": self.custo.to_dict(),
            "falhas": list(self.falhas),
        }


def _percentil(valores: list[int], fracao: float) -> int:
    """Percentil por posição (sem interpolação) — amostra pequena não merece mais que isso."""
    if not valores:
        return 0
    ordenados = sorted(valores)
    indice = min(len(ordenados) - 1, int(round(fracao * (len(ordenados) - 1))))
    return ordenados[indice]


def latencia_de(amostras: list[int]) -> Latencia:
    if not amostras:
        return Latencia(p50_ms=0, p95_ms=0, max_ms=0, media_ms=0)
    return Latencia(
        p50_ms=int(median(amostras)),
        p95_ms=_percentil(amostras, 0.95),
        max_ms=max(amostras),
        media_ms=int(sum(amostras) / len(amostras)),
    )


def custo_de(
    *, tokens_entrada: int, tokens_saida: int, respostas: int, preco: Preco | None
) -> Custo:
    if preco is None:
        return Custo(
            tokens_entrada=tokens_entrada,
            tokens_saida=tokens_saida,
            total_usd="0.000000",
            por_resposta_usd="0.000000",
            preco_declarado=False,
            fonte_preco="sem preço declarado para o modelo — custo não calculado",
        )
    total = preco.custo_usd(tokens_entrada, tokens_saida)
    por_resposta = total / Decimal(respostas) if respostas else Decimal(0)
    return Custo(
        tokens_entrada=tokens_entrada,
        tokens_saida=tokens_saida,
        total_usd=f"{total:.6f}",
        por_resposta_usd=f"{por_resposta:.6f}",
        preco_declarado=True,
        fonte_preco=f"{preco.fonte} (declarado em {preco.declarado_em})",
    )


def agregar(
    julgamentos: list[Julgamento],
    adversarios: list[JulgamentoAdversario],
    *,
    latencias_ms: list[int],
    tokens_entrada: int,
    tokens_saida: int,
    citaram_numero: int,
    preco: Preco | None,
) -> Metricas:
    """Consolida os laudos. Nenhuma média de média: tudo sai de contagem bruta."""
    total = len(julgamentos)
    por_categoria = {cat: sum(1 for j in julgamentos if j.categoria == cat) for cat in CATEGORIAS}
    fundamentadas = sum(1 for j in julgamentos if j.fundamentada is True)
    alucinaram = sum(1 for j in julgamentos if j.alucinou)
    recusa_avaliada = [j for j in julgamentos if j.recusa_correta is not None]
    defasagem_avaliada = [j for j in julgamentos if j.defasagem_sinalizada is not None]
    falhas = [f"{j.id}: {motivo}" for j in julgamentos for motivo in j.falhas] + [
        f"{a.id}: {motivo}" for a in adversarios for motivo in a.falhas
    ]
    return Metricas(
        total=total,
        por_categoria=por_categoria,
        aprovacao=Taxa(sum(1 for j in julgamentos if j.aprovado), total),
        fundamentacao=Taxa(fundamentadas, citaram_numero),
        alucinacao_numerica=Taxa(alucinaram, citaram_numero),
        recusa_correta=Taxa(
            sum(1 for j in recusa_avaliada if j.recusa_correta), len(recusa_avaliada)
        ),
        defasagem_sinalizada=Taxa(
            sum(1 for j in defasagem_avaliada if j.defasagem_sinalizada),
            len(defasagem_avaliada),
        ),
        adversarial=Taxa(sum(1 for a in adversarios if a.aprovado), len(adversarios)),
        latencia=latencia_de(latencias_ms),
        custo=custo_de(
            tokens_entrada=tokens_entrada,
            tokens_saida=tokens_saida,
            respostas=total + len(adversarios),
            preco=preco,
        ),
        falhas=falhas,
    )
