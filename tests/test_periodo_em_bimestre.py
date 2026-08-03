"""Tradução de período ao bimestre do RREO — a regra de calendário que estava triplicada.

`gold.mart_indicador` é ancorado no **bimestre**: o denominador de quase todo indicador é
a RCL, que vem do RREO Anexo 03. Mas as telas de pessoal, dívida e caixa trabalham em
**quadrimestre do RGF** e passavam ``2025-Q3`` adiante — período que aquele indicador nunca
terá. O erro dizia "Sem 'divida_consolidada_liquida' para 23 em 2025-Q3", que se lê como
falta de dado quando o dado existe, sob outro nome.

A regra já existia em três lugares (`cash_rap`, `personnel` e a docstring de `debt`), todas
restritas a quadrimestre — e portanto cegas ao RGF **semestral**, que é o de município com
menos de 50 mil habitantes (LRF, art. 63).
"""

from __future__ import annotations

import pytest

from app.shared.periodo import em_bimestre


@pytest.mark.parametrize(
    ("periodo", "esperado"),
    [
        # Quadrimestre n fecha no mês 4n; bimestre 2n fecha no mesmo mês.
        ("2025-Q1", "2025-B2"),
        ("2025-Q2", "2025-B4"),
        ("2025-Q3", "2025-B6"),
        # Semestre n fecha em 6n; bimestre 3n também. É o caso que as três cópias
        # anteriores não cobriam — devolviam None e o cálculo não achava a RCL.
        ("2025-S1", "2025-B3"),
        ("2025-S2", "2025-B6"),
        # Bimestre é o próprio destino: a tradução tem de ser idempotente, senão
        # chamá-la duas vezes na cadeia mudaria o período.
        ("2025-B4", "2025-B4"),
        ("2025-B1", "2025-B1"),
    ],
)
def test_traduz_para_o_bimestre_que_encerra_o_periodo(periodo: str, esperado: str) -> None:
    assert em_bimestre(periodo) == esperado


@pytest.mark.parametrize("periodo", ["2025", "2025-M07", "", "lixo", "2025-Q9"])
def test_periodo_sem_bimestre_correspondente_devolve_none(periodo: str) -> None:
    """Anual e mensal não fecham um bimestre; inválido não vira palpite."""
    assert em_bimestre(periodo) is None


def test_traducao_e_idempotente() -> None:
    """Aplicar duas vezes não pode andar no calendário."""
    for periodo in ("2025-Q3", "2025-S1", "2025-B2"):
        uma = em_bimestre(periodo)
        assert uma is not None
        assert em_bimestre(uma) == uma


def test_coorte_aceita_periodo_de_rgf(client, make_org) -> None:
    """Ponta a ponta: a comparação com os pares deixa de recusar o período da tela.

    A página de dívida trabalha em quadrimestre e é ela que chama o benchmark. Antes,
    qualquer ente abria o cartão "comparação com os pares" com um aviso de dado ausente.
    """
    from app.core.db import admin_session
    from app.modules.benchmark import service

    with admin_session() as s:
        pedido = service.build_benchmark(
            s, cod_ibge="23", indicador="divida_consolidada_liquida", periodo="2025-Q3"
        )
    assert pedido.periodo == "2025-B6", "o quadrimestre tem de resolver no bimestre que o fecha"
    assert pedido.indicador == "divida_consolidada_liquida"
