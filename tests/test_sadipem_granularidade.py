"""SADIPEM na granularidade da fonte, e sem somar o que não se pode somar.

Três defeitos que conviviam sem se denunciar:

* a **cobertura** não contava nenhuma fonte do SADIPEM — a Central de Dados dizia "0
  registros" para quatro fontes que tinham 117 mil linhas ingeridas;
* o **cronograma** somava fotografias distintas do mesmo estoque, e só não dobrou o número
  na tela porque uma retificação superou a entrega que trazia oito operações;
* o **CDP** gravava a base nacional sob o código do ente consultado, porque ``res-cdp``
  ignora ``id_ente`` — o Brasil inteiro passando por dado de uma prefeitura.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.modules.debt.service import _fotografia_unica
from app.modules.ingestion.cobertura import _SILVER_ENTREGA_MODEL
from app.modules.ingestion.connectors.registry import CONNECTOR_REGISTRY
from app.modules.ingestion.connectors.sadipem import SadipemCdpConnector, _bandeira


@dataclass
class _Fato:
    """Só o que a agregação lê."""

    id_operacao: str
    ano: int
    principal: Decimal = Decimal(0)
    encargos: Decimal = Decimal(0)
    valor: Decimal = Decimal(0)


def _fotografia(operacao: str, anos: range) -> list[_Fato]:
    return [_Fato(id_operacao=operacao, ano=ano, valor=Decimal(100)) for ano in anos]


def test_cobertura_conta_as_fontes_do_sadipem() -> None:
    """A causa direta do "0 registros": nenhuma fonte SADIPEM constava no mapa.

    O teste guarda a regra geral — toda fonte por-ente registrada precisa ser contável —,
    porque o modo de falha é silencioso: a ingestão funciona, a tela mostra zero, e a
    leitura natural é "a ingestão falhou".
    """
    por_ente = {
        fonte
        for fonte in CONNECTOR_REGISTRY
        if fonte.startswith("sadipem_") and fonte != "sadipem_cdp"
    }
    assert por_ente <= set(_SILVER_ENTREGA_MODEL), (
        f"fontes SADIPEM fora do mapa de cobertura: {por_ente - set(_SILVER_ENTREGA_MODEL)}"
    )


def test_cdp_e_nacional_e_nao_do_ente_consultado() -> None:
    """``res-cdp`` ignora ``id_ente``; gravar sob o ente faz o país virar dado da prefeitura."""
    assert SadipemCdpConnector.cod_ibge_entrega == "BR"


def test_cronograma_nao_soma_fotografias_do_mesmo_estoque() -> None:
    """Duas análises do mesmo ente trazem o mesmo cronograma consolidado — somar dobra."""
    completa = _fotografia("9001", range(2026, 2036))  # 10 anos
    parcial = _fotografia("9002", range(2026, 2029))  # 3 anos
    escolhida = _fotografia_unica(completa + parcial)

    assert {f.id_operacao for f in escolhida} == {"9001"}, "deve ficar a fotografia mais completa"
    assert sum(f.valor for f in escolhida) == Decimal(1000), "sem somar as duas"


def test_empate_de_cobertura_fica_com_a_analise_mais_recente() -> None:
    """Mesma quantidade de anos: vence o maior identificador — a análise posterior.

    Escolher pela soma seria escolher justamente a fotografia que mais infla o número.
    """
    antiga = _fotografia("100", range(2026, 2029))
    nova = _fotografia("900", range(2026, 2029))
    assert {f.id_operacao for f in _fotografia_unica(antiga + nova)} == {"900"}


def test_fotografia_unica_nao_mexe_no_caso_normal() -> None:
    """Uma operação só é o caso corrente; a guarda não pode alterar nada aí."""
    unica = _fotografia("42", range(2026, 2031))
    assert _fotografia_unica(unica) == unica
    assert _fotografia_unica([]) == []


def test_bandeira_tolera_o_espaco_que_a_api_manda() -> None:
    """A API devolve ``"1  "`` com espaços; comparar com ``"1"`` cru marcaria tudo falso."""
    assert _bandeira("1  ") is True
    assert _bandeira("0") is False
    assert _bandeira(None) is None
    assert _bandeira("qualquer coisa") is None, "desconhecido é None, não False"
