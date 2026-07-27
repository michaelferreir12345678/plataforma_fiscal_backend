"""Regressões das correções na origem (defeitos achados com dado real).

1. **Receita × Despesa no Anexo 01.** O RREO Anexo 01 é o *Balanço Orçamentário*: traz a
   receita E a despesa. As colunas de despesa também dizem "ATÉ O BIMESTRE"/"NO BIMESTRE",
   e por isso eram classificadas como medidas de arrecadação — a despesa entrava no
   ``fato_receita`` e virava nó da hierarquia de origens de receita.

2. **Haveres opcionais no DDCL.** ``Demais Haveres Financeiros`` só é publicado quando o
   ente o possui; exigi-lo deixava metade dos municípios sem indicador de dívida.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.indicators.divida import LinhaDdcl, calcular_dcl
from app.modules.revenue import natureza

PERIODO_RGF = "2024-Q3"

# Colunas reais do bloco de DESPESA do Anexo 01 (SICONFI, Fortaleza 2024-B6).
COLUNAS_DESPESA = [
    "DESPESAS EMPENHADAS ATÉ O BIMESTRE (f)",
    "DESPESAS EMPENHADAS NO BIMESTRE",
    "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)",
    "DESPESAS LIQUIDADAS NO BIMESTRE",
    "DESPESAS PAGAS ATÉ O BIMESTRE (j)",
    "DOTAÇÃO ATUALIZADA (e)",
    "DOTAÇÃO INICIAL (d)",
    "INSCRITAS EM RESTOS A PAGAR NÃO PROCESSADOS (k)",
]
# Colunas reais do bloco de RECEITA — precisam continuar classificando.
COLUNAS_RECEITA = {
    "PREVISÃO INICIAL": "previsto_inicial",
    "PREVISÃO ATUALIZADA (a)": "previsto_atualizado",
    "No Bimestre (b)": "arrecadado_bimestre",
    "Até o Bimestre (c)": "arrecadado_acum",
}


@pytest.mark.parametrize("coluna", COLUNAS_DESPESA)
def test_coluna_de_despesa_nao_vira_medida_de_receita(coluna: str) -> None:
    assert natureza.e_coluna_de_despesa(coluna) is True
    assert natureza.classificar_coluna(coluna) is None


@pytest.mark.parametrize(("coluna", "medida"), list(COLUNAS_RECEITA.items()))
def test_coluna_de_receita_continua_classificando(coluna: str, medida: str) -> None:
    assert natureza.e_coluna_de_despesa(coluna) is False
    assert natureza.classificar_coluna(coluna) == medida


def test_despesa_nao_entra_na_arvore_de_origens() -> None:
    """Sem medida válida, a linha de despesa nunca chega ao construtor da árvore.

    Reproduz a sequência real do Anexo 01: receitas, depois o bloco de despesa. Antes da
    correção, 'DespesasCorrentes' virava filho de 'ReceitasDeCapital'.
    """
    linhas_com_medida = [
        (seq, cod, desc)
        for seq, cod, desc, coluna in [
            (1, "ReceitasCorrentes", "RECEITAS CORRENTES", "Até o Bimestre (c)"),
            (2, "Impostos", "Impostos", "Até o Bimestre (c)"),
            (3, "ReceitasDeCapital", "RECEITAS DE CAPITAL", "Até o Bimestre (c)"),
            (4, "DespesasCorrentes", "DESPESAS CORRENTES",
             "DESPESAS EMPENHADAS ATÉ O BIMESTRE (f)"),
            (5, "PessoalEEncargosSociais", "Pessoal e Encargos Sociais",
             "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)"),
        ]
        if natureza.classificar_coluna(coluna) is not None
    ]
    nos = natureza.construir_arvore(linhas_com_medida)
    codigos = {n.codigo for n in nos}
    assert codigos == {"ReceitasCorrentes", "Impostos", "ReceitasDeCapital"}
    assert "DespesasCorrentes" not in codigos
    assert "PessoalEEncargosSociais" not in codigos
    # E 'Receitas de Capital' não ganha filhos de despesa.
    filhos_capital = [n.codigo for n in nos if n.parent_codigo == "ReceitasDeCapital"]
    assert filhos_capital == []


# ---------------- DDCL: haveres opcionais ----------------
def _linhas_ddcl(*, com_haveres: bool) -> list[LinhaDdcl]:
    linhas = [
        LinhaDdcl(conta="DÍVIDA CONSOLIDADA (DC) (I)", coluna="Até o 3º Quadrimestre",
                  valor=Decimal("1000")),
        LinhaDdcl(conta="Disponibilidade de Caixa", coluna="Até o 3º Quadrimestre",
                  valor=Decimal("300")),
        LinhaDdcl(conta="RECEITA CORRENTE LÍQUIDA AJUSTADA PARA CÁLCULO DOS LIMITES DE "
                        "ENDIVIDAMENTO", coluna="Até o 3º Quadrimestre", valor=Decimal("2000")),
    ]
    if com_haveres:
        linhas.append(
            LinhaDdcl(conta="Demais Haveres Financeiros", coluna="Até o 3º Quadrimestre",
                      valor=Decimal("100"))
        )
    return linhas


def test_ddcl_sem_haveres_apura_com_zero_e_registra_a_ausencia() -> None:
    """Metade dos municípios não publica a linha; ausência = zero, não erro."""
    ap = calcular_dcl(_linhas_ddcl(com_haveres=False), PERIODO_RGF)
    assert ap.haveres == Decimal(0)
    assert ap.dcl == Decimal("700")  # 1000 − 300 − 0
    # A suposição fica rastreável na memória de cálculo.
    rastro = next(c for c in ap.componentes if c.papel == "haveres")
    assert "ausente" in rastro.conta.lower()


def test_ddcl_com_haveres_continua_deduzindo() -> None:
    ap = calcular_dcl(_linhas_ddcl(com_haveres=True), PERIODO_RGF)
    assert ap.haveres == Decimal("100")
    assert ap.dcl == Decimal("600")  # 1000 − 300 − 100


def test_ddcl_sem_componente_obrigatorio_continua_falhando() -> None:
    """Dívida consolidada e disponibilidade seguem obrigatórias — DDCL quebrado é erro."""
    linhas = [
        LinhaDdcl(conta="Disponibilidade de Caixa", coluna="Até o 3º Quadrimestre",
                  valor=Decimal("300")),
    ]
    with pytest.raises(ValueError, match="dc_bruta"):
        calcular_dcl(linhas, PERIODO_RGF)
