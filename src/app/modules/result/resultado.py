"""Resultado fiscal — funções puras de apuração a partir do RREO **Anexo 6** (Sprint 9).

Base legal: LRF (LC 101/2000), art. 4º e 9º; MDF/STN (Demonstrativo do Resultado Primário
e Nominal). O SICONFI não expõe código numérico: cada linha traz descrição (``conta``) e um
slug estável (``cod_conta``). Aqui mapeamos os slugs do Anexo 6 aos componentes do resultado.

Identidades (validadas contra dado real de Fortaleza):
- ``resultado_primario = receita_primaria − despesa_primaria`` (acima da linha);
- ``resultado_nominal = resultado_primario − juros_liquidos``;
- ``resultado_nominal_abaixo (XLIII) = dcl_inicio − dcl_fim`` (= −variação da DCL);
- ``nominal_abaixo_ajustado (L) = XLIII + Σ ajustes`` — reconcilia acima × abaixo.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

_ANEXO = "Anexo 06"

# Medidas do fato, na ordem canônica.
MEDIDAS = (
    "receita_primaria",
    "despesa_primaria",
    "resultado_primario",
    "juros_ativos",
    "juros_passivos",
    "resultado_nominal",
    "dcl_inicio",
    "dcl_fim",
    "resultado_nominal_abaixo",
    "resultado_nominal_abaixo_ajustado",
    "resultado_primario_abaixo",
    "meta_primario",
    "meta_nominal",
)

# Slugs cujo valor vem da coluna "VALOR" (linhas-resumo do Anexo 6).
_ROLE_VALOR: dict[str, str] = {
    "ResultadoPrimarioComRPPSAcimaDaLinha": "resultado_primario",
    "JurosEncargosEVariacoesMonetariasAtivo": "juros_ativos",
    "JurosEncargosEVariacoesMonetariasPassi": "juros_passivos",
    "ResultadoNominalAcimaDaLinhaSemRPPS": "resultado_nominal",
    "ResultadoNominalAbaixoDaLinhaSemRPPS": "resultado_nominal_abaixo",
    "ResultadoNominalAbaixoDaLinhaSemRPPSAj": "resultado_nominal_abaixo_ajustado",
    "ResultadoPrimarioAbaixoDaLinhaSemRPPS": "resultado_primario_abaixo",
    "RREO6MetaDeResultadoPrimarioFixadaNoAn": "meta_primario",
    "MetaDeResultadoNominalFixadaNoAnexoDeM": "meta_nominal",
}

_RECEITA_PRIMARIA_SLUG = "RREO6TotalReceitaPrimaria"  # coluna RECEITAS REALIZADAS
_DESPESA_PRIMARIA_SLUG = "RREO6TotalDespesaPrimaria"  # soma das colunas pagas
_DCL_SLUG = "DividaConsolidadaLiquida"  # colunas início (31/12) e fim (até o bimestre)

# Ajustes metodológicos (slug → descrição canônica), na ordem do demonstrativo.
AJUSTES: dict[str, str] = {
    "VariacaoSaldoRPP": "Variação do saldo de restos a pagar (RPP)",
    "VariacaoCambial": "Variação cambial",
    "VariacaoDoSaldoDePrecatoriosIntegrante": "Variação de precatórios integrantes da DC",
    "VariacaoDoSaldoDasDemaisObrigacoesInte": "Variação das demais obrigações integrantes da DC",
    "OutrosAjustes": "Outros ajustes",
}

# Componentes primários para o drill (cruza receita/despesa). Coluna: realizada/empenhada.
COMPONENTES_RECEITA: dict[str, str] = {
    "RREO6ReceitasTributarias": "Impostos, Taxas e Contribuições de Melhoria",
    "RREO6ReceitasDeContribuicoes": "Contribuições",
    "RREO6ReceitaPatrimonial": "Receita Patrimonial",
    "RREO6TransferenciasCorrentes": "Transferências Correntes",
    "RREO6DemaisReceitasCorrentes": "Demais Receitas Correntes",
}
COMPONENTES_DESPESA: dict[str, str] = {
    "RREO6PessoalEEncargosSociais": "Pessoal e Encargos Sociais",
    "RREO6OutrasDespesasCorrentes": "Outras Despesas Correntes",
    "RREO6Investimentos": "Investimentos",
    "RREO6InversoesFinanceiras": "Inversões Financeiras",
}


def normalizar_texto(texto: str | None) -> str:
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(sem_acento.upper().split())


@dataclass(frozen=True)
class LinhaAnexo6:
    """Linha do Anexo 6 já lida do silver (slug, coluna, valor)."""

    cod_conta: str | None
    coluna: str | None
    valor: Decimal | None


@dataclass
class ComponentePrimario:
    codigo: str
    descricao: str
    valor: Decimal


@dataclass
class Apuracao:
    medidas: dict[str, Decimal]
    ajustes: list[ComponentePrimario]
    receita_componentes: list[ComponentePrimario]
    despesa_componentes: list[ComponentePrimario]
    fontes: dict[str, str] = field(default_factory=dict)  # medida → (conta/coluna) de origem


def _e_valor(coluna: str | None) -> bool:
    return normalizar_texto(coluna) == "VALOR"


def _e_realizada(coluna: str | None) -> bool:
    return "REALIZAD" in normalizar_texto(coluna)


def _e_empenhada(coluna: str | None) -> bool:
    c = normalizar_texto(coluna)
    return "EMPENHAD" in c and "%" not in c


def _e_paga(coluna: str | None) -> bool:
    """Colunas que compõem a despesa primária (pagas + RP processados pagos + pagos)."""
    c = normalizar_texto(coluna)
    return ("PAGAS" in c or "PAGOS" in c) and "%" not in c


def _dcl_tipo(coluna: str | None) -> str | None:
    c = normalizar_texto(coluna)
    if "ATE O BIMESTRE" in c or "ATE O BIM" in c:
        return "fim"
    if "31/12" in c or "EXERCICIO ANTERIOR" in c:
        return "inicio"
    return None


def apurar(linhas: list[LinhaAnexo6]) -> Apuracao:
    """Apura medidas + ajustes + componentes primários a partir das linhas do Anexo 6."""
    medidas: dict[str, Decimal] = {}
    fontes: dict[str, str] = {}
    ajustes: list[ComponentePrimario] = []
    despesa_primaria = Decimal(0)
    tem_despesa = False
    receita_comp: dict[str, ComponentePrimario] = {}
    despesa_comp: dict[str, ComponentePrimario] = {}

    for linha in linhas:
        cod, col, valor = linha.cod_conta, linha.coluna, linha.valor
        if not cod or valor is None:
            continue
        if cod in _ROLE_VALOR and _e_valor(col):
            medidas[_ROLE_VALOR[cod]] = valor
            fontes[_ROLE_VALOR[cod]] = f"{cod} · {col}"
        elif cod == _RECEITA_PRIMARIA_SLUG and _e_realizada(col):
            medidas["receita_primaria"] = valor
            fontes["receita_primaria"] = f"{cod} · {col}"
        elif cod == _DESPESA_PRIMARIA_SLUG and _e_paga(col):
            despesa_primaria += valor
            tem_despesa = True
        elif cod == _DCL_SLUG:
            tipo = _dcl_tipo(col)
            if tipo == "inicio":
                medidas["dcl_inicio"] = valor
                fontes["dcl_inicio"] = f"{cod} · {col}"
            elif tipo == "fim":
                medidas["dcl_fim"] = valor
                fontes["dcl_fim"] = f"{cod} · {col}"
        elif cod in AJUSTES and _e_valor(col):
            ajustes.append(ComponentePrimario(cod, AJUSTES[cod], valor))
        elif cod in COMPONENTES_RECEITA and _e_realizada(col) and cod not in receita_comp:
            receita_comp[cod] = ComponentePrimario(cod, COMPONENTES_RECEITA[cod], valor)
        elif cod in COMPONENTES_DESPESA and _e_empenhada(col) and cod not in despesa_comp:
            despesa_comp[cod] = ComponentePrimario(cod, COMPONENTES_DESPESA[cod], valor)

    if tem_despesa:
        medidas["despesa_primaria"] = despesa_primaria
        fontes["despesa_primaria"] = f"{_DESPESA_PRIMARIA_SLUG} · Σ colunas pagas"

    return Apuracao(
        medidas=medidas,
        ajustes=ajustes,
        receita_componentes=list(receita_comp.values()),
        despesa_componentes=list(despesa_comp.values()),
        fontes=fontes,
    )


def juros_liquidos(medidas: dict[str, Decimal]) -> Decimal | None:
    """Juros líquidos = passivos − ativos; se não reportados, derivados do (primário − nominal)."""
    ativos = medidas.get("juros_ativos")
    passivos = medidas.get("juros_passivos")
    if ativos is not None or passivos is not None:
        return (passivos or Decimal(0)) - (ativos or Decimal(0))
    primario = medidas.get("resultado_primario")
    nominal = medidas.get("resultado_nominal")
    if primario is not None and nominal is not None:
        return primario - nominal
    return None


def variacao_dcl(medidas: dict[str, Decimal]) -> Decimal | None:
    inicio = medidas.get("dcl_inicio")
    fim = medidas.get("dcl_fim")
    if inicio is None or fim is None:
        return None
    return fim - inicio


def soma_ajustes(ajustes: list[ComponentePrimario]) -> Decimal:
    return sum((a.valor for a in ajustes), Decimal(0))
