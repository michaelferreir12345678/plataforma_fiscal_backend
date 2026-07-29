"""Regras puras da Sprint 15: prazos LRF, cadência por porte, defasagem, severidade.

Sem I/O — só domínio. Reusa a aritmética de período do forecast (``forecast.periodos``)
para não reinventar parsing/ordinais. As bases legais são **dado de referência**
(apontam o dispositivo; não decidem — §9 CLAUDE.md).
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

from app.modules.catalog.models import ESFERA_MUNICIPAL
from app.modules.forecast.periodos import parse_periodo

# Porte: municípios com menos de 50 mil habitantes têm RGF semestral (LRF art. 63, II).
PORTE_LIMIAR_RGF = 50_000

# Severidades e pesos (menor prioridade = topo da fila).
SEV_CRITICO = "critico"
SEV_ATENCAO = "atencao"
SEV_INFORMATIVO = "informativo"
_SEV_PESO = {SEV_CRITICO: 0, SEV_ATENCAO: 1, SEV_INFORMATIVO: 2}

# Categorias e desempate de prioridade dentro da mesma severidade.
_CAT_PESO = {
    "limite": 0,
    # Sprint 26: um número errado é pior que um número atrasado — qualidade vem antes
    # do prazo, porque o gestor pode estar decidindo com ele agora.
    "qualidade_dado": 1,
    "prazo": 2,
    "falha_ingestao": 3,
    "defasagem_fonte": 4,
    "preditivo": 5,
    "publicacao": 6,
    "anomalia": 7,
}

# Faixa do limite → severidade do alerta.
_FAIXA_SEVERIDADE = {
    "excedido": SEV_CRITICO,
    "insuficiente": SEV_CRITICO,
    "prudencial": SEV_ATENCAO,
    "alerta": SEV_INFORMATIVO,
}

# Fontes complementares monitoradas para defasagem (relatorio em dim_entrega).
@dataclass(frozen=True)
class FonteComplementar:
    relatorio: str  # como aparece em gold.dim_entrega
    rotulo: str
    cadencia: str  # "bimestral" | "anual"
    base_legal: str
    link: str


FONTES_COMPLEMENTARES: tuple[FonteComplementar, ...] = (
    FonteComplementar(
        "SIOPS", "SIOPS (saúde)", "bimestral",
        "SIOPS/Ministério da Saúde — dado auxiliar da apuração da saúde (LC 141/2012).",
        "/saude-educacao",
    ),
    FonteComplementar(
        "SIOPE", "SIOPE (educação)", "bimestral",
        "SIOPE/FNDE — dado auxiliar da apuração da educação (Lei 14.113/2020).",
        "/saude-educacao",
    ),
    FonteComplementar(
        "CAPAG", "CAPAG (Tesouro)", "anual",
        "Prévia da CAPAG/STN — capacidade de pagamento (nota A–D).",
        "/divida",
    ),
)

# Relatórios principais monitorados no calendário.
REL_RREO = "RREO"
REL_RGF = "RGF"
REL_DCA = "DCA"
REL_MSC = "MSC"


@dataclass(frozen=True)
class ObrigacaoEsperada:
    relatorio: str
    periodo: str
    periodicidade: str
    prazo: date | None
    base_legal: str


def cadencia_rgf(esfera: str | None, populacao: int | None) -> str:
    """RGF é quadrimestral, salvo município < 50 mil hab. (semestral) — LRF art. 63, II."""
    if esfera == ESFERA_MUNICIPAL and populacao is not None and populacao < PORTE_LIMIAR_RGF:
        return "semestral"
    return "quadrimestral"


def _fim_periodo(codigo: str) -> date:
    """Último dia-calendário coberto pelo período fiscal."""
    p = parse_periodo(codigo)
    ultimo_mes = max(p.meses())
    return date(p.ano, ultimo_mes, monthrange(p.ano, ultimo_mes)[1])


def prazo_obrigacao(relatorio: str, periodo: str) -> date | None:
    """Prazo legal de entrega (LRF art. 52 RREO; art. 55 RGF; STN 30/04 para DCA)."""
    ano = parse_periodo(periodo).ano
    if relatorio == REL_DCA:
        return date(ano + 1, 4, 30)  # DCA: 30 de abril do exercício seguinte (STN).
    # RREO/RGF/MSC: até 30 dias após o encerramento do período.
    return _fim_periodo(periodo) + timedelta(days=30)


def base_legal_calendario(relatorio: str) -> str:
    return {
        REL_RREO: "LRF (LC 101/2000) art. 52 — RREO em até 30 dias após o bimestre.",
        REL_RGF: "LRF art. 55, §2º — RGF em até 30 dias após o quadrimestre/semestre.",
        REL_DCA: "Portaria STN — DCA (balanços anuais) até 30 de abril do ano seguinte.",
        REL_MSC: "Portaria STN — MSC mensal, até o fim do mês seguinte.",
    }.get(relatorio, "Prazos de transparência da LRF.")


def obrigacoes_do_ano(
    ano: int, esfera: str | None, populacao: int | None
) -> list[ObrigacaoEsperada]:
    """Calendário esperado do ano, com periodicidade sensível ao porte/esfera."""
    obrig: list[ObrigacaoEsperada] = []

    def add(relatorio: str, periodo: str, periodicidade: str) -> None:
        obrig.append(
            ObrigacaoEsperada(
                relatorio=relatorio,
                periodo=periodo,
                periodicidade=periodicidade,
                prazo=prazo_obrigacao(relatorio, periodo),
                base_legal=base_legal_calendario(relatorio),
            )
        )

    # RREO — bimestral (todos).
    for b in range(1, 7):
        add(REL_RREO, f"{ano}-B{b}", "bimestral")

    # RGF — quadrimestral (>=50k / estadual) ou semestral (< 50k municipal).
    cad = cadencia_rgf(esfera, populacao)
    quadris = (1, 2, 3) if cad == "quadrimestral" else (2, 3)  # semestral: 2 entregas/ano
    for q in quadris:
        add(REL_RGF, f"{ano}-Q{q}", cad)

    # DCA — anual.
    add(REL_DCA, str(ano), "anual")
    return obrig


def severidade_faixa(faixa: str | None) -> str | None:
    """Severidade do alerta de limite a partir da faixa classificada (None = sem alerta)."""
    return _FAIXA_SEVERIDADE.get(faixa or "")


def prioridade(severidade: str, categoria: str) -> int:
    """Chave de ordenação da fila (menor = mais urgente)."""
    return _SEV_PESO.get(severidade, 3) * 100 + _CAT_PESO.get(categoria, 9)


@dataclass(frozen=True)
class Defasagem:
    """Resultado da avaliação de defasagem de uma fonte complementar."""

    fonte: FonteComplementar
    disponivel: bool
    periodo_fonte: str | None
    periodo_ancora: str
    atraso: int  # em bimestres (bimestral) ou anos (anual); 0 = em dia
    severidade: str


def avaliar_defasagem(
    fonte: FonteComplementar, periodo_fonte: str | None, periodo_ancora: str
) -> Defasagem | None:
    """Compara o período mais recente da fonte com a âncora (RREO) e classifica o atraso.

    Retorna ``None`` quando a fonte está em dia (sem defasagem relevante).
    """
    if periodo_fonte is None:
        return Defasagem(fonte, False, None, periodo_ancora, atraso=-1, severidade=SEV_ATENCAO)
    ancora = parse_periodo(periodo_ancora)
    fonte_p = parse_periodo(periodo_fonte)
    if fonte.cadencia == "anual":
        atraso = ancora.ano - fonte_p.ano
    else:
        atraso = ancora.ordinal - fonte_p.ordinal
    if atraso <= 0:
        return None
    severidade = SEV_ATENCAO if atraso >= 2 else SEV_INFORMATIVO
    return Defasagem(
        fonte, True, periodo_fonte, periodo_ancora, atraso=atraso, severidade=severidade
    )
