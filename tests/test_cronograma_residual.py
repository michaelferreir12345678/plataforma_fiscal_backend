"""O "Restante a pagar" entra na conta sem entrar na série anual.

O ``/opc-cronograma-pagamentos`` fecha a lista com uma linha-resumo cujo ``ano`` vem como
o texto ``"Restante a pagar"``. O conector a descartava — certo em não fingir um ano, e
errado em não deixar rastro: o total exibido virava só a soma dos anos listados.

Em Fortaleza são R$ 561 milhões fora da conta na fotografia vigente (7,8% do compromisso)
e R$ 848 milhões numa fotografia anterior (16,6%). Dívida informada **para menos** é o erro
que menos denuncia a si mesmo: nada na tela parece estranho.

Duas coisas precisam valer ao mesmo tempo, e é a tensão entre elas que este arquivo fixa:
o residual não pode ser desenhado como um ano, e não pode sumir do total.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.modules.ingestion.connectors.sadipem import SadipemCronogramaConnector


class _JobFalso:
    ano = 2026
    cod_ibge = "2304400"
    valid_time = None
    params: dict[str, Any] = {}


def _to_silver_rows(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Roda só o mapeamento, sem tocar no banco: é o mapeamento que estava errado."""
    capturadas: list[dict[str, Any]] = []
    conector = SadipemCronogramaConnector.__new__(SadipemCronogramaConnector)
    conector._replace = lambda session, job, versao, rows: capturadas.extend(rows) or len(rows)  # type: ignore[method-assign]
    conector.to_silver(None, _JobFalso(), payload, "v1")  # type: ignore[arg-type]
    return capturadas


PAYLOAD = [
    {
        "id_pleito": 64171,
        "ano": "2033",
        "total_amortizacao": 215_301_848.68,
        "total_encargos": 16_256_237.11,
    },
    # A linha que era descartada. O ``ano`` não é um ano — é um rótulo.
    {
        "id_pleito": 64171,
        "ano": "Restante a pagar",
        "total_amortizacao": 720_594_271.60,
        "total_encargos": 127_619_528.81,
    },
]


def test_a_linha_resumo_e_preservada_e_marcada() -> None:
    rows = _to_silver_rows(PAYLOAD)
    assert len(rows) == 2, "a linha-resumo era descartada e não pode mais ser"
    residuais = [r for r in rows if r["residual"]]
    assert len(residuais) == 1
    assert residuais[0]["principal"] == Decimal("720594271.60")
    assert residuais[0]["encargos"] == Decimal("127619528.81")


def test_o_residual_nao_ganha_um_ano_fictício() -> None:
    """Atribuí-lo a um ano seria pior que descartar: viraria uma barra falsa no gráfico."""
    residual = next(r for r in _to_silver_rows(PAYLOAD) if r["residual"])
    assert residual["ano"] is None


def test_o_ano_real_continua_ano_e_nao_e_marcado_como_residual() -> None:
    anual = next(r for r in _to_silver_rows(PAYLOAD) if not r["residual"])
    assert anual["ano"] == 2033
    assert anual["principal"] == Decimal("215301848.68")


def test_cronograma_da_tela_separa_serie_de_compromisso() -> None:
    """Ponta a ponta: a série tem só anos; o compromisso inclui o que vence depois."""
    from app.core.db import admin_session
    from app.modules.debt import service

    with admin_session() as s:
        c = service.build_cronograma(s, "2304400", "2026")

    assert all(item.ano > 0 for item in c.itens), "nenhum item da série pode ser o residual"
    assert c.horizonte_ate == max(item.ano for item in c.itens)
    resto = c.restante_amortizacao + c.restante_encargos
    assert resto > 0, "Fortaleza tem 'Restante a pagar' publicado; sem ele o teste não prova nada"
    assert c.total_com_residual == c.total_valor + resto
    assert c.total_com_residual > c.total_valor, "o residual precisa aumentar o compromisso"
