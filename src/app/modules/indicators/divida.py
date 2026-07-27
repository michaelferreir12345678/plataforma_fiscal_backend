"""Cálculos fiscais puros da dívida (DDCL/CAPAG/simulação).

Base legal: LRF, art. 30–31; Resoluções do Senado 40/2001 e 43/2001.
O DDCL do RGF (Anexo 02) é a fonte primária da dívida e a apuração não usa a
linha pronta como atalho: ``DCL = DC − disponibilidade de caixa − haveres``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class LinhaDdcl:
    conta: str
    coluna: str | None
    valor: Decimal


@dataclass(frozen=True)
class ComponenteDdcl:
    papel: str
    conta: str
    coluna: str
    valor: Decimal


@dataclass(frozen=True)
class ApuracaoDivida:
    dc_bruta: Decimal
    disponibilidades: Decimal
    haveres: Decimal
    dcl: Decimal
    dcl_reportada: Decimal | None
    diferenca_reconciliacao: Decimal | None
    rcl_ajustada: Decimal | None
    pct_rcl: Decimal | None
    saldo_interno: Decimal | None
    saldo_externo: Decimal | None
    componentes: tuple[ComponenteDdcl, ...]


_QUAD = re.compile(r"^\d{4}-Q([1-3])$")
_SEM = re.compile(r"^\d{4}-S([1-2])$")


def normalizar_texto(valor: str | None) -> str:
    """Normaliza rótulos do SICONFI sem presumir código numérico inexistente."""
    if not valor:
        return ""
    decomposto = unicodedata.normalize("NFKD", valor)
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    return " ".join(sem_acento.upper().split())


def coluna_do_periodo(periodo: str, coluna: str | None) -> bool:
    """Seleciona somente a coluna acumulada correspondente ao período RGF."""
    texto = normalizar_texto(coluna)
    quad = _QUAD.match(periodo)
    if quad is not None:
        numero = quad.group(1)
        return any(
            marca in texto
            for marca in (
                f"ATE O {numero}º QUADRIMESTRE",
                f"ATE O {numero}O QUADRIMESTRE",
                f"ATE O {numero} QUADRIMESTRE",
            )
        )
    semestre = _SEM.match(periodo)
    if semestre is not None:
        numero = semestre.group(1)
        return any(
            marca in texto
            for marca in (
                f"ATE O {numero}º SEMESTRE",
                f"ATE O {numero}O SEMESTRE",
                f"ATE O {numero} SEMESTRE",
            )
        )
    return texto == "VALOR"


def _papel(conta: str) -> str | None:
    texto = normalizar_texto(conta)
    if "DIVIDA CONSOLIDADA LIQUIDA" in texto:
        return "dcl_reportada"
    if (
        "DIVIDA CONSOLIDADA" in texto
        and "DC" in texto
        and "%" not in texto
        and "LIMITE" not in texto
    ):
        return "dc_bruta"
    if texto.startswith("DISPONIBILIDADE DE CAIXA") and "BRUTA" not in texto:
        return "disponibilidades"
    if "DEMAIS HAVERES FINANCEIROS" in texto:
        return "haveres"
    if "RECEITA CORRENTE LIQUIDA AJUSTADA" in texto and "ENDIVIDAMENTO" in texto:
        return "rcl_ajustada"
    if texto == "INTERNOS":
        return "saldo_interno"
    if texto == "EXTERNOS":
        return "saldo_externo"
    return None


def _selecionar(linhas: list[LinhaDdcl], periodo: str) -> list[LinhaDdcl]:
    selecionadas = [linha for linha in linhas if coluna_do_periodo(periodo, linha.coluna)]
    # Fixtures/alguns layouts consolidados publicam apenas uma coluna ``VALOR``.
    if not selecionadas:
        selecionadas = [linha for linha in linhas if normalizar_texto(linha.coluna) == "VALOR"]
    return selecionadas


def calcular_dcl(linhas: list[LinhaDdcl], periodo: str) -> ApuracaoDivida:
    """Apura DCL a partir dos três componentes, sem dupla contagem de subtotais.

    A linha ``DEDUÇÕES (II)`` e a ``Disponibilidade de Caixa Bruta`` são
    deliberadamente ignoradas. A DCL pode ser negativa; não há truncamento em zero.
    """
    valores: dict[str, Decimal] = {}
    rastros: list[ComponenteDdcl] = []
    for linha in _selecionar(linhas, periodo):
        papel = _papel(linha.conta)
        if papel is None:
            continue
        valores[papel] = valores.get(papel, Decimal(0)) + linha.valor
        rastros.append(
            ComponenteDdcl(
                papel=papel,
                conta=linha.conta,
                coluna=linha.coluna or "",
                valor=linha.valor,
            )
        )

    # ``Demais Haveres Financeiros`` é uma dedução **opcional** do DDCL: o RGF só publica a
    # linha quando o ente tem esses haveres (metade dos municípios do CE não tem). Ausência
    # significa zero — não "impossível apurar". Tratá-la como obrigatória deixava esses entes
    # sem indicador de dívida algum. A ausência fica registrada no rastro, e o zero é a
    # leitura **conservadora** (sem a dedução, a DCL apurada é maior, nunca menor).
    obrigatorios = ("dc_bruta", "disponibilidades")
    faltantes = [nome for nome in obrigatorios if nome not in valores]
    if faltantes:
        raise ValueError(f"Componentes DDCL ausentes no período: {', '.join(faltantes)}")

    dc = valores["dc_bruta"]
    disponibilidades = valores["disponibilidades"]
    haveres = valores.get("haveres", Decimal(0))
    if "haveres" not in valores:
        rastros.append(
            ComponenteDdcl(
                papel="haveres",
                conta="(linha ausente no DDCL — assumido zero)",
                coluna="",
                valor=Decimal(0),
            )
        )
    dcl = dc - disponibilidades - haveres
    reportada = valores.get("dcl_reportada")
    diferenca = dcl - reportada if reportada is not None else None
    rcl = valores.get("rcl_ajustada")
    pct = percentual_sobre_rcl(dcl, rcl) if rcl is not None else None
    return ApuracaoDivida(
        dc_bruta=dc,
        disponibilidades=disponibilidades,
        haveres=haveres,
        dcl=dcl,
        dcl_reportada=reportada,
        diferenca_reconciliacao=diferenca,
        rcl_ajustada=rcl,
        pct_rcl=pct,
        saldo_interno=valores.get("saldo_interno"),
        saldo_externo=valores.get("saldo_externo"),
        componentes=tuple(rastros),
    )


def percentual_sobre_rcl(valor: Decimal, rcl: Decimal) -> Decimal:
    """Percentual de um valor sobre a RCL ajustada; a base deve ser positiva."""
    if rcl <= 0:
        raise ValueError("RCL ajustada deve ser positiva para calcular limites de dívida.")
    return valor / rcl * Decimal(100)


def endividamento_capag_pct(ind_endividamento: Decimal | None) -> Decimal | None:
    """CAPAG publica o Indicador 1 como razão DC bruta/RCL; expõe também em %."""
    return ind_endividamento * Decimal(100) if ind_endividamento is not None else None
