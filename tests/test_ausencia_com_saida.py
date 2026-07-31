"""Ausência de relatório com saída navegável — o 404 que orienta em vez de só recusar.

O gestor que pede o quadrimestre em curso recebia "Sem RGF vigente para 2304400 em
2026-Q2" e um botão "tentar de novo". As duas coisas estão erradas juntas: a frase não diz
que o prazo do RGF ainda não venceu, e repetir a consulta não faz o ente publicar. Aqui
provamos que o erro passa a carregar (a) a cadência do relatório e (b) o último período que
**tem** dado, em campos de extensão do Problem Details — o que o front vira botão.

Casos de borda que importam: nunca sugerir o próprio período que falhou (mandaria a tela de
volta à mesma parede) e nunca sugerir um período de **outro** ente ou **outro** relatório.
"""

from __future__ import annotations

import pytest

from app.core.db import admin_session
from app.core.errors import AppError
from app.modules.personnel import service as personnel_service
from app.modules.revenue import service as revenue_service
from app.shared.ausencia import extras_com_saida, rotulo_humano, ultimo_periodo_com_dado

# Fortaleza tem RREO e RGF reais no banco de teste (dado do SICONFI, não fixture sintética).
FORTALEZA = "2304400"


@pytest.mark.parametrize(
    ("periodo", "esperado"),
    [
        ("2025-Q3", "3º quadrimestre de 2025"),
        ("2024-B6", "6º bimestre de 2024"),
        ("2023-S1", "1º semestre de 2023"),
        ("2024", "2024"),
        ("2024-X9", "2024-X9"),  # tipo desconhecido: devolve o canônico, não inventa nome
    ],
)
def test_rotulo_fala_a_lingua_do_gestor(periodo: str, esperado: str) -> None:
    """O botão diz "3º quadrimestre de 2025"; ``2025-Q3`` é identificador, não rótulo."""
    assert rotulo_humano(periodo) == esperado


def test_sugere_o_ultimo_periodo_que_tem_o_relatorio() -> None:
    with admin_session() as s:
        ultimo = ultimo_periodo_com_dado(s, cod_ibge=FORTALEZA, relatorio="RGF")
        assert ultimo is not None, "Fortaleza tem RGF ingerido; sem isso o teste não prova nada"
        extras = extras_com_saida(s, cod_ibge=FORTALEZA, relatorio="RGF", periodo="2099-Q1")
        assert extras["periodo_sugerido"] == ultimo
        assert extras["rotulo_sugerido"] == f"Ir para {rotulo_humano(ultimo)}"
        assert "quadrimestral" in str(extras["explicacao"])


def test_nao_sugere_o_periodo_que_acabou_de_falhar() -> None:
    """Se o único período com dado é o pedido, não há saída — e o botão não deve aparecer.

    Sugerir o mesmo período faria a tela navegar para si mesma e falhar de novo, agora com
    a aparência de defeito da plataforma.
    """
    with admin_session() as s:
        ultimo = ultimo_periodo_com_dado(s, cod_ibge=FORTALEZA, relatorio="RGF")
        assert ultimo is not None
        extras = extras_com_saida(s, cod_ibge=FORTALEZA, relatorio="RGF", periodo=ultimo)
        assert "periodo_sugerido" not in extras
        # A explicação continua: ela não depende de haver alternativa.
        assert "explicacao" in extras


def test_nao_cruza_relatorio_nem_ente() -> None:
    """O RREO de Fortaleza não é saída para uma ausência de RGF, nem o de outro ente."""
    with admin_session() as s:
        rreo = ultimo_periodo_com_dado(s, cod_ibge=FORTALEZA, relatorio="RREO")
        rgf = ultimo_periodo_com_dado(s, cod_ibge=FORTALEZA, relatorio="RGF")
        assert rreo is not None and rgf is not None
        assert rreo != rgf, "bimestre e quadrimestre não coincidem; se coincidirem o teste é fraco"
        assert ultimo_periodo_com_dado(s, cod_ibge="9999999", relatorio="RGF") is None


def test_ordena_por_calendario_e_nao_por_texto() -> None:
    """``2024-S1`` é texto maior que ``2024-Q3``, mas semestre anterior no calendário.

    O caso é real: município que cruza os 50 mil habitantes muda a cadência do RGF (LRF,
    art. 63) e passa a ter os dois tipos no mesmo exercício. Com ``ORDER BY periodo DESC``
    o botão mandaria o gestor de volta para junho achando que ia para dezembro.
    """
    from app.shared import periodo as periodo_util

    assert "2024-S1" > "2024-Q3", "premissa do teste: como texto, o semestre vem depois"
    assert periodo_util.mais_recente(["2024-S1", "2024-Q3"]) == "2024-Q3"
    assert periodo_util.mais_recente(["2024-Q1", "2024-S1"]) == "2024-S1"


def test_relatorio_desconhecido_nao_inventa_explicacao() -> None:
    with admin_session() as s:
        extras = extras_com_saida(s, cod_ibge=FORTALEZA, relatorio="INEXISTENTE", periodo="2025-B1")
        assert "explicacao" not in extras
        assert "periodo_sugerido" not in extras


def test_rgf_ausente_do_pessoal_chega_com_saida() -> None:
    """Ponta a ponta no serviço: o 404 de pessoal carrega os extras, não só a frase."""
    with admin_session() as s:
        with pytest.raises(AppError) as exc:
            personnel_service._resolve_versao(s, FORTALEZA, "2099-Q1", None)
        assert exc.value.status == 404
        assert exc.value.extras.get("periodo_sugerido")
        assert "LRF, art. 63" in str(exc.value.extras.get("explicacao"))


def test_capag_nunca_sugere_o_exercicio_pedido_ou_posterior() -> None:
    """A saída da CAPAG tem de ser um exercício **anterior** ao pedido.

    Fortaleza tem CAPAG carregada até 2026. Pedindo 2026, a lista de exercícios disponíveis
    começa com o próprio 2026 no fim — e a versão anterior deste código oferecia "Ir para
    2026", o exercício que a tela acabou de não conseguir abrir.
    """
    from app.modules.debt import service as debt_service

    with admin_session() as s:
        extras = debt_service._capag_ausente(s, FORTALEZA, 2026).extras
        sugerido = extras.get("periodo_sugerido")
        assert sugerido is not None
        assert int(str(sugerido)[:4]) < 2026
        assert int(str(extras["ano_disponivel"])) < 2026


def test_rreo_ausente_da_receita_chega_com_saida() -> None:
    with admin_session() as s:
        with pytest.raises(AppError) as exc:
            revenue_service._resolve_versao(s, FORTALEZA, "2099-B1", None)
        assert exc.value.status == 404
        assert exc.value.extras.get("periodo_sugerido")
        assert "LRF, art. 52" in str(exc.value.extras.get("explicacao"))
