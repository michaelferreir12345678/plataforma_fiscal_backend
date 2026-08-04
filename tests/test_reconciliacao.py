"""Reconciliação com o valor oficial: onde a plataforma discorda de quem publicou.

Um número calculado corretamente e um número **conferido** não são a mesma coisa. A
plataforma computa a RCL somando os 12 meses móveis do RREO Anexo 03 e subtraindo as
deduções; o ente publica, no RGF Anexo 02, a RCL que ele próprio apurou. Dois caminhos
independentes para o mesmo fato — e é por isso que compará-los prova algo.

Medido no acervo: 1.982 pares no escopo do Ceará, **94,2% batem à centavo**, 115 divergem.
Nenhuma dessas divergências aparecia em lugar nenhum da plataforma antes disto.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.db import admin_session
from app.modules.reconciliation import service


def _escopo_ce() -> set[str]:
    with admin_session() as s:
        return {
            r[0]
            for r in s.execute(
                text("select cod_ibge from gold.dim_ente where uf='CE' and esfera='municipal'")
            )
        } | {"23"}


def test_a_rcl_da_plataforma_bate_com_a_publicada_na_esmagadora_maioria() -> None:
    """A coincidência é a evidência: dois caminhos independentes chegando ao mesmo número.

    O limiar de 90% não é meta de qualidade — é sentinela. Se cair abaixo disso, algo
    sistêmico quebrou no cálculo da RCL, e a RCL é o denominador de quase todo limite.
    """
    with admin_session() as s:
        r = service.build_reconciliacao(s, codigo="rcl_rgf", entes=_escopo_ce())
    assert r.resumo.pares > 1000, "sem massa comparada o teste não prova nada"
    assert r.resumo.taxa_conferencia > 0.90, (
        f"apenas {r.resumo.taxa_conferencia:.1%} dos valores batem com o publicado pelo ente"
    )


def test_a_tolerancia_e_de_um_centavo_e_nao_esconde_diferenca() -> None:
    """Alargar a tolerância seria esconder divergência por arredondamento."""
    assert Decimal("0.01") == service.TOLERANCIA


def test_divergencia_traz_os_dois_valores_e_uma_causa_provavel() -> None:
    """Mostrar só a diferença obrigaria o analista a reabrir os dois lados na mão."""
    with admin_session() as s:
        r = service.build_reconciliacao(s, codigo="rcl_rgf", entes=_escopo_ce(), limite=5)
    assert r.divergencias, "o acervo tem divergências conhecidas; sem elas o teste é vazio"
    d = r.divergencias[0]
    assert d.valor_oficial and d.valor_plataforma
    assert d.diferenca == d.valor_plataforma - d.valor_oficial
    assert d.causa_provavel


def test_maiores_primeiro_e_o_corte_e_declarado() -> None:
    """Ordem por dano: é onde a investigação rende mais por hora gasta."""
    with admin_session() as s:
        r = service.build_reconciliacao(s, codigo="rcl_rgf", entes=_escopo_ce(), limite=3)
    assert len(r.divergencias) == 3
    assert r.truncado is True, "truncar em silêncio faria o painel parecer completo"
    diffs = [abs(d.diferenca) for d in r.divergencias]
    assert diffs == sorted(diffs, reverse=True)


def test_plataforma_com_zero_e_apontada_como_defeito_nosso() -> None:
    """RCL zero não é "o ente aplicou zero" — é bimestre sem entrega virando zero na gold.

    São 32 das 4.141 linhas de `fato_rcl`, em 8 entes. A causa provável precisa dizer de
    quem é a falha, senão o analista vai cobrar o município por um defeito de carga nosso.
    """
    causa = service._causa_provavel(Decimal("59120515.50"), Decimal("0"))
    assert "zero" in causa
    assert "defeito nosso" in causa


def test_valor_oficial_zero_nao_vira_percentual_infinito() -> None:
    """Dividir por zero daria infinito; devolver zero se leria como "sem diferença"."""
    with admin_session() as s:
        r = service.build_reconciliacao(s, codigo="rcl_rgf", entes=_escopo_ce())
    for d in r.divergencias:
        if d.valor_oficial == 0:
            assert d.diferenca_pct is None


def test_escopo_vazio_nao_inventa_conferencia() -> None:
    with admin_session() as s:
        r = service.build_reconciliacao(s, codigo="rcl_rgf", entes=set())
    assert r.resumo.pares == 0
    assert r.resumo.taxa_conferencia == 0.0
    assert not r.divergencias


def test_a_metodologia_e_declarada_junto_do_resultado() -> None:
    """Um painel de divergência sem metodologia é uma acusação sem processo."""
    with admin_session() as s:
        r = service.build_reconciliacao(s, codigo="rcl_rgf", entes={"2304400"})
    assert "Anexo 02" in r.fonte_oficial
    assert "12 meses móveis" in r.metodologia


@pytest.mark.parametrize("codigo", sorted(service.COMPARACOES))
def test_toda_comparacao_declara_fonte_e_metodologia(codigo: str) -> None:
    comp = service.COMPARACOES[codigo]
    assert comp.fonte_oficial and comp.metodologia and comp.titulo
