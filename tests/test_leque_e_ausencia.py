"""Fontes do leque, ausência que não é falha, e retry que não repete o irrepetível.

Os três defeitos vinham do mesmo hábito: **tratar coisas diferentes como iguais**. Fonte
sem configuração igual a fonte pronta; ausência de publicação igual a erro; 404 igual a
5xx. Cada igualdade dessas produz falha previsível — e falha previsível ensina o operador
a ignorar o painel de erros, que é quando o erro real passa.
"""

from __future__ import annotations

import httpx
import pytest

from app.modules.ingestion.connectors.registry import FONTE_META
from app.shared.ingestion.client import _RespostaTransitoria, recurso_ausente


def _resposta(status: int) -> httpx.Response:
    req = httpx.Request("GET", "https://exemplo.gov.br/indicador/estadual/23/2026/1")
    return httpx.Response(status, request=req)


def _erro(status: int, transitoria: bool = False) -> httpx.HTTPStatusError:
    classe = _RespostaTransitoria if transitoria else httpx.HTTPStatusError
    resp = _resposta(status)
    return classe(f"{status}", request=resp.request, response=resp)


class TestClassificacaoDeAusencia:
    def test_404_e_ausencia(self) -> None:
        """A fonte dizendo "não tenho esse período" — típico do bimestre em curso."""
        assert recurso_ausente(_erro(404)) is True

    @pytest.mark.parametrize("status", [400, 401, 403, 422])
    def test_erro_do_pedido_nao_e_ausencia(self, status: int) -> None:
        """Credencial ou pedido malformado não autorizam concluir que o dado não existe."""
        assert recurso_ausente(_erro(status)) is False

    @pytest.mark.parametrize("status", [500, 502, 503, 429])
    def test_indisponibilidade_nao_e_ausencia(self, status: int) -> None:
        """5xx/429 é a fonte fora do ar. Marcar como "sem dado" apagaria a lacuna."""
        assert recurso_ausente(_erro(status, transitoria=True)) is False

    def test_erro_que_nao_e_http_nao_e_ausencia(self) -> None:
        assert recurso_ausente(ValueError("qualquer outra coisa")) is False


class TestLeque:
    def test_fonte_sem_configuracao_fica_de_fora(self) -> None:
        """Ela falharia em toda execução, sempre pelo mesmo motivo."""
        pdf = FONTE_META["siconfi_rreo_minimos_pdf"]
        assert pdf.requer_configuracao, "o fallback por portal municipal exige template"

    def test_entrega_nacional_nao_implica_consulta_sem_ente(self) -> None:
        """O cadastro de entes e o FPM registram entrega ``BR`` mas perguntam por ente.

        Confundir os dois fazia o leque mandá-los sem ente nenhum, e o ``discover``
        devolvia zero unidades — "Nenhuma unidade de ingestão encontrada" a cada execução.
        """
        for fonte in ("siconfi_entes", "tesouro_fpm", "fnde_fundeb_repasse"):
            meta = FONTE_META[fonte]
            assert meta.escopo == "nacional", f"{fonte}: a entrega é nacional"
            assert meta.consulta_por_ente is True, f"{fonte}: mas a consulta é por ente"

    def test_fonte_realmente_nacional_dispensa_entes(self) -> None:
        for fonte in ("bcb", "tesouro_capag"):
            assert FONTE_META[fonte].consulta_por_ente is False


class TestTransferenciasPelaApi:
    def test_fpm_e_genericas_deixaram_de_ser_planilha(self) -> None:
        """Eram conectores de arquivo sem URL: falhavam em toda execução."""
        from app.shared.ingestion.client import FILE_FONTES

        assert "tesouro_fpm" not in FILE_FONTES
        assert "transferencia_generica" not in FILE_FONTES

    def test_codigos_de_transferencia_nao_se_sobrepoem(self) -> None:
        """Contar o mesmo repasse em duas fontes inflaria a receita do ente."""
        from app.modules.ingestion.connectors.transferencias import (
            FPM_TRANSFERENCIAS,
            FUNDEB_TRANSFERENCIAS,
            OUTRAS_TRANSFERENCIAS,
        )

        conjuntos = [
            set(codigos.split(":"))
            for codigos in (FPM_TRANSFERENCIAS, FUNDEB_TRANSFERENCIAS, OUTRAS_TRANSFERENCIAS)
        ]
        for i, a in enumerate(conjuntos):
            for b in conjuntos[i + 1 :]:
                assert not (a & b), f"códigos repetidos entre conectores: {a & b}"
