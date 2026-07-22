"""Despesa com pessoal — funções puras de classificação e cálculo (Sprint 7).

Base legal: LRF (LC 101/2000), art. 18–20 (despesa com pessoal e limites) e art. 19,
§1º (parcelas **não computadas** — as exclusões). A apuração usa a **Despesa Total com
Pessoal (DTP)** dos últimos 12 meses (liquidadas + inscritas em RP não processados) do
RGF, Demonstrativo da Despesa com Pessoal (Anexo 1).

Regra invariante do domínio (§2 CLAUDE.md): as **exclusões dependem do RPPS**. A parcela
"inativos e pensionistas com recursos vinculados" (art. 19, §1º, IV) só é excluída quando
o ente mantém **regime próprio de previdência** (``dim_ente.rpps``); sem RPPS, não há
fundo vinculado que justifique a exclusão.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from decimal import Decimal

# Nó raiz (consolidado do ente) e medidas do fato, na ordem canônica.
ROOT_CODIGO = "ENTE"
ROOT_DESCRICAO = "Ente (Consolidado)"
MEDIDAS = ("despesa_bruta", "exclusoes", "despesa_liquida", "pct_rcl")


@dataclass(frozen=True)
class PoderInfo:
    """Poder da federação: nó na dimensão + rótulo do limite legal (quando houver)."""

    codigo: str  # nó em dim_poder_orgao (ex.: 'ENTE.EXEC')
    descricao: str
    limite_poder: str  # rótulo em gold.dim_limite_legal ('Executivo', …)
    indicador: str | None  # indicador de limite (só o Executivo tem teto cadastrado no MVP)


# co_poder (SICONFI) → poder. Executivo é o único com teto de pessoal cadastrado (54/49%).
_PODERES: dict[str, PoderInfo] = {
    "E": PoderInfo("ENTE.EXEC", "Poder Executivo", "Executivo", "pessoal_executivo"),
    "L": PoderInfo("ENTE.LEG", "Poder Legislativo", "Legislativo", None),
    "J": PoderInfo("ENTE.JUD", "Poder Judiciário", "Judiciário", None),
    "M": PoderInfo("ENTE.MP", "Ministério Público", "Ministério Público", None),
    "D": PoderInfo("ENTE.DEF", "Defensoria Pública", "Defensoria", None),
}

# Papéis das linhas do Anexo (conta) na apuração.
BRUTA = "despesa_bruta"
DTP = "despesa_total_dtp"  # Despesa Total com Pessoal (VI) — valor oficial do limite
LIQUIDA_REPORTADA = "despesa_liquida_reportada"  # Despesa Líquida (III)
EXCL_TOTAL_REPORTADO = "exclusoes_reportadas"
EXCL_INDENIZACAO = "indenizacoes"
EXCL_DECISAO_JUDICIAL = "decisao_judicial"
EXCL_EXERC_ANTERIORES = "exercicios_anteriores"
EXCL_INATIVOS_RPPS = "inativos_pensionistas_rpps"

# Componentes de exclusão somados (art. 19, §1º). O de RPPS é condicional.
EXCLUSOES_INCONDICIONAIS = (EXCL_INDENIZACAO, EXCL_DECISAO_JUDICIAL, EXCL_EXERC_ANTERIORES)
EXCLUSAO_CONDICIONAL_RPPS = EXCL_INATIVOS_RPPS


def normalizar_texto(texto: str | None) -> str:
    """Maiúsculas, sem acentos, espaços colapsados — para comparação robusta."""
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(sem_acento.upper().split())


def resolver_poder(raw: str | None) -> PoderInfo | None:
    """Resolve ``co_poder`` (letra ou nome) para um :class:`PoderInfo` (``None`` se ignoto)."""
    t = normalizar_texto(raw)
    if not t:
        return None
    if t in _PODERES:
        return _PODERES[t]
    if "EXECUTIVO" in t:
        return _PODERES["E"]
    if "LEGISLATIVO" in t or "CAMARA" in t:
        return _PODERES["L"]
    if "JUDICIARIO" in t:
        return _PODERES["J"]
    if "MINISTERIO PUBLICO" in t:
        return _PODERES["M"]
    if "DEFENSORIA" in t:
        return _PODERES["D"]
    return None


def poder_por_codigo(codigo: str) -> PoderInfo | None:
    return next((p for p in _PODERES.values() if p.codigo == codigo), None)


def parent_de(codigo: str) -> str | None:
    segmentos = codigo.split(".")
    return ".".join(segmentos[:-1]) if len(segmentos) > 1 else None


def nivel_de(codigo: str) -> int:
    return len(codigo.split("."))


def classificar_valor_coluna(coluna: str | None) -> bool:
    """Colunas que carregam valor da apuração (RGF real e layout sintético).

    Real: ``TOTAL (ÚLTIMOS 12 MESES)`` + ``INSCRITAS EM RESTOS A PAGAR`` (componentes)
    e ``Valor`` (linhas-resumo: líquida/DTP). Ignora saldos, percentuais e mensais ``<MR>``.
    """
    c = normalizar_texto(coluna)
    if not c:
        return False
    if "%" in c or "PERCENTUAL" in c or "SALDO" in c:
        return False
    if "12 MESES" in c:
        return True
    if "RESTOS A PAGAR" in c and "INSCRIT" in c:
        return True
    return c == "VALOR" or "LIQUIDADAS" in c


def classificar_conta(conta: str | None) -> str | None:
    """Classifica a linha (conta) do Anexo de Despesa com Pessoal em seu papel."""
    c = normalizar_texto(conta)
    if not c:
        return None
    if "DESPESA BRUTA COM PESSOAL" in c:
        return BRUTA
    if "DESPESA TOTAL COM PESSOAL" in c:  # DTP (VI) — valor do limite
        return DTP
    if "DESPESA LIQUIDA COM PESSOAL" in c:  # líquida (III)
        return LIQUIDA_REPORTADA
    if "NAO COMPUTADAS" in c or "NAO-COMPUTADAS" in c:
        return EXCL_TOTAL_REPORTADO
    if "INDENIZAC" in c or ("INCENTIVO" in c and "DEMISSAO" in c):
        return EXCL_INDENIZACAO
    if "DECISAO JUDICIAL" in c:
        return EXCL_DECISAO_JUDICIAL
    if "EXERCICIOS ANTERIORES" in c:
        return EXCL_EXERC_ANTERIORES
    if ("INATIVOS" in c or "PENSIONISTAS" in c) and "VINCULAD" in c:
        return EXCL_INATIVOS_RPPS
    return None


@dataclass(frozen=True)
class ApuracaoPessoal:
    """Resultado da apuração de um poder (ou consolidado)."""

    despesa_bruta: Decimal
    exclusoes: Decimal
    despesa_liquida: Decimal
    exclusao_rpps_aplicada: Decimal  # parcela de RPPS efetivamente excluída (0 se sem RPPS)
    exclusao_rpps_disponivel: Decimal  # parcela de RPPS reportada (excluída ou não)


def apurar(componentes: dict[str, Decimal], *, rpps: bool) -> ApuracaoPessoal:
    """Apura bruta/exclusões/líquida.

    Quando o RGF reporta a Despesa Total com Pessoal (DTP, VI) ou a Líquida (III), esse
    é o **valor oficial** (o limite é medido sobre ele); as exclusões viram derivadas
    (``bruta − líquida``). Sem valor reportado (layout sintético), calcula
    ``bruta − exclusões``, com a parcela de inativos/pensionistas com recursos vinculados
    excluída **apenas se há RPPS** (art. 19, §1º, IV).
    """
    bruta = componentes.get(BRUTA, Decimal(0))
    rpps_disponivel = componentes.get(EXCLUSAO_CONDICIONAL_RPPS, Decimal(0))
    rpps_aplicada = rpps_disponivel if rpps else Decimal(0)

    reportada = componentes.get(DTP)
    if reportada is None:
        reportada = componentes.get(LIQUIDA_REPORTADA)
    if reportada is not None:
        liquida = reportada
        exclusoes = bruta - liquida
    else:
        exclusoes = sum(
            (componentes.get(k, Decimal(0)) for k in EXCLUSOES_INCONDICIONAIS), Decimal(0)
        ) + rpps_aplicada
        liquida = bruta - exclusoes
    if liquida < 0:
        liquida = Decimal(0)
    return ApuracaoPessoal(
        despesa_bruta=bruta,
        exclusoes=exclusoes,
        despesa_liquida=liquida,
        exclusao_rpps_aplicada=rpps_aplicada,
        exclusao_rpps_disponivel=rpps_disponivel,
    )
