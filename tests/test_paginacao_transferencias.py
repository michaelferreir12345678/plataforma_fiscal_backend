"""A API de transferências pagina de 10 em 10 — e o conector não paginava.

Consequência medida: uma consulta por estado trazia os **10 primeiros municípios em ordem
alfabética** e nada denunciava o corte. O Ceará ingeria de Abaiara a Aquiraz; os outros 174
municípios simplesmente não existiam no acervo. Cinco por cento do dado, com aparência de
cem — e a página de Receita mostrava a conciliação FPM como se fosse completa.

O modo de falha é o pior possível: silencioso, plausível e estável. Ninguém desconfia de
uma lista que sempre volta com dado.
"""

from __future__ import annotations

from typing import Any

from app.shared.ingestion.client import JsonEnvelopeRecordsClient


class _ClienteFalso(JsonEnvelopeRecordsClient):
    """Instancia sem rede: substitui apenas o transporte."""

    def __init__(self, paginas: list[dict[str, Any]]) -> None:  # noqa: D107
        self._paginas = paginas
        self.chamadas: list[dict[str, Any]] = []

    def _get_json(self, path: str, params: dict[str, Any]) -> Any:  # type: ignore[override]
        self.chamadas.append(dict(params))
        indice = int(params.get("page", 1)) - 1
        return self._paginas[indice] if 0 <= indice < len(self._paginas) else {}


def _pagina(inicio: int, fim: int, *, tem_proxima: bool) -> dict[str, Any]:
    return {
        "registros": [{"CO_IBGE": 2300000 + i, "VALOR": i} for i in range(inicio, fim)],
        "next": "http://exemplo/proxima" if tem_proxima else "",
        "page": inicio // 10 + 1,
        "pageSize": 10,
    }


def test_percorre_todas_as_paginas() -> None:
    cliente = _ClienteFalso(
        [
            _pagina(0, 10, tem_proxima=True),
            _pagina(10, 20, tem_proxima=True),
            _pagina(20, 25, tem_proxima=False),
        ]
    )
    registros = cliente.get_records("por_estado_municipio", {"p_estado": 6})
    assert len(registros) == 25, "sem paginar, viriam só os 10 primeiros"
    assert cliente.chamadas[0].get("page") is None, "a primeira página não pede `page`"
    assert [c.get("page") for c in cliente.chamadas[1:]] == [2, 3]


def test_para_quando_next_esvazia_e_nao_pelo_tamanho_da_pagina() -> None:
    """A última página cheia pararia o laço uma página cedo demais se olhássemos o tamanho."""
    cliente = _ClienteFalso(
        [_pagina(0, 10, tem_proxima=True), _pagina(10, 20, tem_proxima=False)]
    )
    assert len(cliente.get_records("x", {})) == 20


def test_pagina_vazia_encerra() -> None:
    cliente = _ClienteFalso([{"registros": [], "next": "http://exemplo/proxima"}])
    assert cliente.get_records("x", {}) == []


def test_next_que_nunca_esvazia_nao_gira_para_sempre() -> None:
    """Servidor com cursor em laço precisa parar, não consumir a máquina."""
    cliente = _ClienteFalso([_pagina(0, 10, tem_proxima=True)] * 2000)
    registros = cliente.get_records("x", {})
    assert len(cliente.chamadas) <= JsonEnvelopeRecordsClient.MAX_PAGINAS
    assert registros, "o teto interrompe, mas não descarta o que já veio"


def test_resposta_fora_do_formato_nao_quebra() -> None:
    class _Torto(_ClienteFalso):
        def _get_json(self, path: str, params: dict[str, Any]) -> Any:
            return ["lista", "em", "vez", "de", "objeto"]

    assert _Torto([]).get_records("x", {}) == []
