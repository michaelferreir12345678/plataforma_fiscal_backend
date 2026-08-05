"""Garantias e operações de crédito — os dois limites que a plataforma nunca apurava.

`dim_limite_legal` declara quatro tetos de endividamento e só um era calculado: a dívida
consolidada líquida. Garantias (22% da RCL Ajustada) e operações de crédito (16%) existiam
como regra e não como número — a plataforma sabia o limite e nunca dizia se o ente estava
dentro dele.

Dois defeitos foram achados no caminho, ambos por conferir contra o que o ente publica:

* **o Anexo 04 nomeia a coluna de outro jeito** ("Até o Quadrimestre de Referência (a)",
  não "Até o 3º Quadrimestre"). Com o nome dos outros anexos, Fortaleza aparecia com R$ 0
  de operações de crédito quando tem R$ 495 milhões — folga inventada num limite;
* **a contraprova comparava períodos diferentes**: sem filtrar a coluna, vinha o "SALDO DO
  EXERCÍCIO ANTERIOR" (0,43% no Ceará) contra o nosso 0,28% do quadrimestre corrente.
  Alarme falso gasta a confiança que o alarme verdadeiro vai precisar.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.db import admin_session
from app.modules.indicators import endividamento as e

ENTE_ESTADUAL = "23"
ENTE_MUNICIPAL = "2304400"
PERIODO = "2025-Q3"


def _versao(cod_ibge: str) -> str | None:
    with admin_session() as s:
        return s.scalar(
            text(
                """select versao_entrega from gold.dim_entrega
                   where cod_ibge = :e and relatorio = 'RGF' and periodo = :p
                     and vigente is true limit 1"""
            ),
            {"e": cod_ibge, "p": PERIODO},
        )


def test_o_denominador_e_a_rcl_ajustada_e_nao_a_rcl() -> None:
    """Res. 43/2001 do Senado. Usar a RCL cheia infla o denominador e **subestima** o
    percentual — o ente aparece com mais folga do que tem."""
    versao = _versao(ENTE_ESTADUAL)
    assert versao
    with admin_session() as s:
        ajustada = e.rcl_ajustada(s, cod_ibge=ENTE_ESTADUAL, periodo=PERIODO, versao=versao)
        cheia = s.scalar(
            text(
                """select valor from silver.siconfi_rgf
                   where cod_ibge = :e and periodo = :p and versao_entrega = :v
                     and cod_conta = 'RGF2ReceitaCorrenteLiquida'
                     and coluna = 'Até o 3º Quadrimestre' limit 1"""
            ),
            {"e": ENTE_ESTADUAL, "p": PERIODO, "v": versao},
        )
    assert ajustada is not None and cheia is not None
    assert ajustada < Decimal(cheia), "a ajustada deduz as emendas individuais; é menor"


def test_operacoes_de_credito_usam_a_coluna_do_anexo_04() -> None:
    """O defeito que inventava folga: coluna errada devolvia `None` em silêncio."""
    assert e.coluna_acumulada(PERIODO, anexo="04") == "Até o Quadrimestre de Referência (a)"
    assert e.coluna_acumulada(PERIODO) == "Até o 3º Quadrimestre"

    versao = _versao(ENTE_MUNICIPAL)
    assert versao
    with admin_session() as s:
        total = e.total_operacoes_credito(
            s, cod_ibge=ENTE_MUNICIPAL, periodo=PERIODO, versao=versao
        )
    assert total is not None and total > 0, "Fortaleza tem operações contratadas em 2025"


def test_o_percentual_calculado_bate_com_o_publicado_pelo_ente() -> None:
    """A contraprova que vem de graça: o Anexo 03 publica o mesmo percentual."""
    versao = _versao(ENTE_ESTADUAL)
    assert versao
    with admin_session() as s:
        garantias = e.total_garantias(s, cod_ibge=ENTE_ESTADUAL, periodo=PERIODO, versao=versao)
        base = e.rcl_ajustada(s, cod_ibge=ENTE_ESTADUAL, periodo=PERIODO, versao=versao)
        publicado = e.percentual_publicado_garantias(
            s, cod_ibge=ENTE_ESTADUAL, periodo=PERIODO, versao=versao
        )
    assert garantias is not None and base and publicado is not None
    calculado = garantias / base * 100
    # O ente publica com 2 casas; a coincidência tem de valer nessa precisão.
    assert abs(calculado - Decimal(publicado)) < Decimal("0.01"), (
        f"calculado {calculado:.4f}% × publicado {publicado}%"
    )


def test_anexo_entregue_sem_linha_de_garantia_e_zero_declarado() -> None:
    """Conceder garantia é raro: 8 de 179 entes têm a linha.

    Mas o Anexo 03 **é** o demonstrativo das garantias. Entregá-lo sem linha é o ente
    declarando que não concedeu nenhuma — informação, não ausência dela. Tratar como
    ausência esconderia o cumprimento; tratar ausência do anexo como zero inventaria um
    cumprimento que ninguém declarou.
    """
    versao = _versao(ENTE_MUNICIPAL)
    assert versao
    with admin_session() as s:
        total = e.total_garantias(s, cod_ibge=ENTE_MUNICIPAL, periodo=PERIODO, versao=versao)
    assert total == Decimal(0), "Fortaleza entrega o Anexo 03 e não concedeu garantias"


def test_sem_o_anexo_nao_se_inventa_zero() -> None:
    """Ente sem o anexo entregue devolve `None` — a distinção que sustenta o teste anterior."""
    with admin_session() as s:
        total = e.total_garantias(
            s, cod_ibge="9999999", periodo=PERIODO, versao="inexistente"
        )
    assert total is None


@pytest.mark.parametrize("periodo", ["2025", "2025-B6", "", "lixo"])
def test_periodo_fora_do_padrao_rgf_nao_gera_coluna(periodo: str) -> None:
    assert e.coluna_acumulada(periodo) is None


# --------------------------------------------------------------------------------- #
# A15 (Sprint A5) — o RGF republica os quadrimestres já decorridos a cada entrega
# nova, e a leitura travava no primeiro valor publicado.
# --------------------------------------------------------------------------------- #

#: 2307650 (Maracanaú/CE), RGF de 2023: a entrega do 1º quadrimestre publicou uma RCL
#: Ajustada de R$ 152,1 mi; a entrega do 2º (repetida no 3º) republicou a MESMA coluna
#: "Até o 1º Quadrimestre" corrigida para R$ 1.022,4 mi — mais de 6x. A materialização
#: lia só a entrega do próprio período e travava no primeiro número.
ENTE_REPUBLICACAO = "2307650"


def _versao_rgf(cod_ibge: str, periodo: str) -> str | None:
    with admin_session() as s:
        return s.scalar(
            text(
                """select versao_entrega from gold.dim_entrega
                   where cod_ibge = :e and relatorio = 'RGF' and periodo = :p
                     and vigente is true limit 1"""
            ),
            {"e": cod_ibge, "p": periodo},
        )


def test_rcl_ajustada_usa_o_valor_mais_recentemente_republicado() -> None:
    """A15: 1º quadrimestre de 2023 — R$ 152,1 mi (1ª entrega) vira R$ 1.022,4 mi.

    A correção chegou como republicação na entrega do quadrimestre seguinte, não como
    versão nova do mesmo período (CLAUDE.md §2, regra 3: bitemporalidade). Ler só a
    entrega de 2023-Q1 nunca via essa correção.
    """
    versao = _versao_rgf(ENTE_REPUBLICACAO, "2023-Q1")
    assert versao
    with admin_session() as s:
        ajustada = e.rcl_ajustada(
            s, cod_ibge=ENTE_REPUBLICACAO, periodo="2023-Q1", versao=versao
        )
    assert ajustada == Decimal("1022418338.43")
    assert ajustada != Decimal("152100786.24"), "não pode ter travado no primeiro valor"


def test_valor_vigente_sem_republicacao_le_so_a_propria_entrega() -> None:
    """Sem `republicavel`, o comportamento é idêntico ao de antes da Sprint A5 — usado
    pelo Anexo 01 (pessoal) e pelo Anexo 04 (operações de crédito), que não têm coluna
    comparativa por quadrimestre (ver docstring de `_valor_vigente`)."""
    versao = _versao_rgf(ENTE_REPUBLICACAO, "2023-Q1")
    assert versao
    with admin_session() as s:
        valor = e._valor_vigente(
            s, cod_ibge=ENTE_REPUBLICACAO, periodo="2023-Q1", versao=versao,
            conta=e.CONTA_RCL_AJUSTADA, marca_anexo="02",
            coluna=e.coluna_acumulada("2023-Q1"), republicavel=False,
        )
    assert valor == Decimal("152100786.24"), "sem republicavel=True, lê só a própria entrega"


def test_ultimo_quadrimestre_do_exercicio_nao_tem_para_onde_olhar() -> None:
    """No 3º quadrimestre (terminal do exercício) não há entrega seguinte no mesmo ano —
    a leitura republication-aware degenera para a leitura direta, sem efeito algum.
    Prova que a Sprint A5 não alterou o resultado dos períodos já corretos (ver também
    os testes pré-existentes deste arquivo, todos em PERIODO = "2025-Q3")."""
    versao = _versao_rgf(ENTE_REPUBLICACAO, "2023-Q3")
    assert versao
    with admin_session() as s:
        ajustada = e.rcl_ajustada(
            s, cod_ibge=ENTE_REPUBLICACAO, periodo="2023-Q3", versao=versao
        )
        direta = e._valor(
            s, cod_ibge=ENTE_REPUBLICACAO, periodo="2023-Q3", versao=versao,
            conta=e.CONTA_RCL_AJUSTADA, marca_anexo="02", coluna="Até o 3º Quadrimestre",
        )
    assert ajustada == direta
