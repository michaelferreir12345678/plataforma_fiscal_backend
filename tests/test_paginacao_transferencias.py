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


# --------------------------------------------------------------------------------------
# Completude no acervo: os testes acima provam que o cliente pagina; estes provam que a
# carga **chegou inteira**. São perguntas diferentes, e só a segunda pega uma reingestão
# que parou no meio — o modo de falha do A12 não deixa rastro, então a defesa tem de ser
# afirmativa: contar o que deveria existir e exigir que exista.
#
# Escrever este teste rendeu o achado **A14**: a contagem não fechava por 12 linhas, e as
# 12 eram Fortaleza com duas versões de entrega para os mesmos meses. As tabelas guardam
# `versao_entrega` e não guardam qual vence — e os leitores da previsão e da conciliação
# somam todas.
# --------------------------------------------------------------------------------------

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import admin_session  # noqa: E402

#: 184 municípios do Ceará + o próprio estado. É o universo que a âncora carrega.
ENTES_ANCORA = 185
MESES_NO_ANO = 12
TABELAS_MENSAIS = ["tesouro_fpm", "fnde_fundeb_repasse"]


@pytest.mark.parametrize("tabela", TABELAS_MENSAIS)
def test_transferencias_cobrem_todos_os_entes_da_ancora(tabela: str) -> None:
    """Uma fonte mensal por ente tem de alcançar os 185 entes da âncora.

    Com o conector sem paginação, o Ceará tinha 11 e a falta passava despercebida porque
    *havia* dado. Contar entes distintos é o menor teste que teria pegado o A12.
    """
    with admin_session() as session:
        entes = session.scalar(text(f"select count(distinct cod_ibge) from silver.{tabela}"))
    assert entes == ENTES_ANCORA, (
        f"{tabela}: {entes} entes no acervo, esperados {ENTES_ANCORA}. "
        "Carga parcial — provavelmente ingestão interrompida ou sem paginação."
    )


@pytest.mark.parametrize("tabela", TABELAS_MENSAIS)
def test_exercicio_fechado_tem_os_doze_meses_de_todo_ente(tabela: str) -> None:
    """Ente com menos de 12 meses num exercício encerrado é lacuna, não sazonalidade.

    A contagem é feita **por versão de entrega**: somar versões diferentes mascararia
    exatamente a incompletude que este teste procura. A carga completa (`pag2024`) tem de
    ter os 12 meses de cada ente — as parciais superadas são tratadas no A14.
    """
    with admin_session() as session:
        incompletos = session.execute(
            text(
                f"""select cod_ibge, versao_entrega, count(distinct mes) as meses
                      from silver.{tabela}
                     where ano = 2024 and versao_entrega = 'pag2024'
                     group by 1, 2
                    having count(distinct mes) <> :meses
                     order by 3
                     limit 5"""
            ),
            {"meses": MESES_NO_ANO},
        ).all()
    assert not incompletos, (
        f"{tabela}: entrega com mês faltando em 2024 (amostra): "
        + ", ".join(f"{c}/{v}={m} meses" for c, v, m in incompletos)
    )


@pytest.mark.xfail(
    reason="A14: as tabelas de transferência não declaram qual versão de entrega vence, "
    "e os leitores da previsão e da conciliação somam todas. Correção pendente.",
    strict=True,
)
@pytest.mark.parametrize("tabela", TABELAS_MENSAIS)
def test_uma_competencia_nao_tem_duas_versoes_somaveis(tabela: str) -> None:
    """Duas versões para a mesma competência dobram o valor de quem soma sem filtrar.

    Marcado ``xfail(strict=True)``: **falha registrada enquanto o defeito existir, e o
    próprio teste passa a acusar erro no dia em que for corrigido sem que este marcador
    seja removido.** Um teste que apenas registrasse o número duplicado como esperado
    transformaria o defeito em contrato — que é como defeito de dado sobrevive a
    refatoração.
    """
    with admin_session() as session:
        duplicadas = session.execute(
            text(
                f"""select cod_ibge, ano, mes, count(distinct versao_entrega) as versoes
                      from silver.{tabela}
                     group by 1, 2, 3
                    having count(distinct versao_entrega) > 1
                     order by 4 desc
                     limit 5"""
            )
        ).all()
    assert not duplicadas, (
        f"{tabela}: competências com mais de uma versão (amostra): "
        + ", ".join(f"{c}/{a}-{m}: {v} versões" for c, a, m, v in duplicadas)
    )
