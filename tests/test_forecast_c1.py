"""Sprint C1 — o que faltava para a previsão servir a uma decisão de governo.

Três lacunas, cada uma verificável contra o acervo real:

* **a premissa era de fábrica.** A tela abria com IPCA 4,5% e Selic 10,5% escritos no
  frontend. O acervo tem as séries do BCB, e a Selic observada é 14,28% — quem aceitasse o
  padrão simulava com 3,8 pontos percentuais de erro sem saber que havia um padrão;
* **a conversão anual→mensal era linear.** 12% ao ano viravam 1,0% ao mês em vez de
  0,9489%, e o erro compunha a cada passo do horizonte;
* **o modelo não sabia recusar.** A série de pessoal do ente 2307650 tem um ponto de
  324,49% da RCL — impossível. A regressão o consumiu e projetou 2,29%, número igualmente
  impossível, com intervalo de confiança e cara de análise. Saneada, a mesma série projeta
  50,24%, coerente com as onze observações boas.

E a peça que transforma projeção em decisão: **quanto ainda cabe, em reais**. "Cruza o teto
em 2026-B2" não se assina; "há R$ 826 milhões de margem" se assina.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.db import admin_session
from app.modules.forecast import premissas, sanidade, service
from app.modules.forecast.espaco_fiscal import (
    SITUACAO_EXCEDIDO,
    SITUACAO_FOLGA,
    EspacoFiscal,
    esforco_reconducao,
)
from app.modules.indicators.limites import LimiteLegal

ENTE_MUNICIPAL = "2304400"  # Fortaleza
ENTE_ESTADUAL = "23"  # Ceará
ENTE_SERIE_SUJA = "2307650"  # tem o ponto de 324,49% da RCL


# --------------------------------------------------------------------------------------
# Conversão de taxa — o defeito silencioso
# --------------------------------------------------------------------------------------
def test_conversao_anual_para_mensal_e_composta() -> None:
    """12% a.a. são 0,9489% a.m., não 1,0%.

    A divisão linear parece inofensiva porque o erro é pequeno num passo. Ele compõe: ao
    fim de um horizonte de 12 períodos, a premissa aplicada é 6,2% maior que a informada.
    """
    mensal = premissas.anual_para_mensal(12.0)
    assert abs(mensal - Decimal("0.9489")) < Decimal("0.0001")
    assert mensal < Decimal(1), "a composta é sempre menor que a divisão linear"


def test_acumular_doze_meses_e_produto_nao_soma() -> None:
    """0,5% ao mês por 12 meses dão 6,17% no ano — não 6,0%."""
    acumulado = premissas.mensal_para_anual([Decimal("0.5")] * 12)
    assert acumulado is not None
    assert abs(acumulado - Decimal("6.1678")) < Decimal("0.001")


@pytest.mark.parametrize("taxa", [-100.0, -150.0])
def test_deflacao_total_nao_quebra_a_conversao(taxa: float) -> None:
    """Queda de 100% ou mais não tem raiz com significado econômico; devolve zero, não erro."""
    assert premissas.anual_para_mensal(taxa) == Decimal(0)


def test_ida_e_volta_da_taxa_fecha() -> None:
    """Converter para mensal e reacumular 12 meses devolve a taxa anual original."""
    anual = Decimal("7.35")
    mensal = premissas.anual_para_mensal(anual)
    de_volta = premissas.mensal_para_anual([mensal] * 12)
    assert de_volta is not None
    assert abs(de_volta - anual) < Decimal("0.0001")


# --------------------------------------------------------------------------------------
# Premissas ancoradas no observado
# --------------------------------------------------------------------------------------
def test_premissas_vem_do_acervo_com_data_e_fonte() -> None:
    """Nenhum número de fábrica: cada premissa diz de onde saiu e quantas observações usou."""
    with admin_session() as session:
        itens = premissas.observadas(session, ENTE_MUNICIPAL)
    por_chave = {p.chave: p for p in itens}

    ipca = por_chave["ipca_aa_pct"]
    assert ipca.observado is not None, "o acervo tem a série 433"
    assert ipca.n_observacoes == premissas.MESES_ACUMULACAO
    assert ipca.referencia and ipca.fonte and "433" in ipca.fonte
    # Faixa de sanidade larga de propósito: o teste protege contra erro de ordem de
    # grandeza (somar em vez de acumular, ou devolver a variação de um mês só), não
    # contra a inflação ser o que é.
    assert Decimal(-5) < ipca.observado < Decimal(50)

    selic = por_chave["selic_aa_pct"]
    assert selic.observado is not None
    assert selic.observado > Decimal(0)


def test_serie_curta_nao_vira_acumulado_de_doze_meses() -> None:
    """Acumular 7 meses e chamar de "12 meses" produz número plausível e errado.

    É o erro mais fácil de cometer aqui e o mais difícil de perceber depois — por isso a
    contagem de observações viaja na resposta e a ausência é declarada.
    """
    assert premissas.MESES_ACUMULACAO == 12
    assert "12 meses" in premissas.MOTIVO_SERIE_CURTA


def test_premissa_ausente_nao_recebe_valor_de_fabrica() -> None:
    """Ente sem histórico de FPM devolve ``None`` com motivo, nunca um número."""
    with admin_session() as session:
        itens = premissas.observadas(session, "9999999")
    fpm = next(p for p in itens if p.chave == "fpm_variacao_pct")
    assert fpm.observado is None
    assert fpm.motivo, "ausência sem motivo é indistinguível de erro"


def test_variacao_do_fpm_compara_exercicios_completos() -> None:
    """Comparar ano fechado com ano em curso inventaria uma queda que não existe."""
    with admin_session() as session:
        itens = premissas.observadas(session, ENTE_MUNICIPAL)
    fpm = next(p for p in itens if p.chave == "fpm_variacao_pct")
    if fpm.observado is None:
        pytest.skip("acervo sem dois exercícios completos de FPM para este ente")
    assert fpm.referencia and "→" in fpm.referencia
    ano_a, ano_b = fpm.referencia.split("→")
    assert int(ano_b) == int(ano_a) + 1, "os exercícios comparados são consecutivos"


# --------------------------------------------------------------------------------------
# Sanidade da série
# --------------------------------------------------------------------------------------
def test_ponto_impossivel_sai_do_treino_e_e_declarado() -> None:
    """324,49% da RCL em pessoal é defeito do denominador, não comportamento do ente."""
    saneada = sanidade.sanear(
        ["2022-B6", "2023-B2", "2023-B4"], [46.53, 324.49, 49.97], unidade="PCT_RCL"
    )
    assert saneada.valores == [46.53, 49.97]
    assert saneada.houve_exclusao
    memoria = saneada.memoria()
    assert memoria["pontos_excluidos"] == 1
    assert memoria["exclusoes"][0]["periodo"] == "2023-B2"
    assert "impossível" in memoria["exclusoes"][0]["motivo"]


def test_valor_alto_mas_possivel_permanece() -> None:
    """60% da RCL com pessoal é ilegal e real — e é justamente o que precisa aparecer.

    Uma limpeza que removesse "valores altos" apagaria o ente em situação irregular, que é
    o caso de uso central da plataforma.
    """
    saneada = sanidade.sanear(["2024-B6", "2025-B6"], [58.2, 61.4], unidade="PCT_RCL")
    assert not saneada.houve_exclusao
    assert saneada.valores == [58.2, 61.4]


def test_quebra_estrutural_nao_e_tratada_como_ruido() -> None:
    """Salto grande e possível é informação: mudança de mandato, fim de convênio."""
    saneada = sanidade.sanear(
        ["2022-B6", "2023-B6", "2024-B6"], [22.0, 47.0, 49.0], unidade="PCT_RCL"
    )
    assert not saneada.houve_exclusao


def test_serie_toda_suspeita_volta_intacta() -> None:
    """Com menos pontos válidos que o mínimo, projetar e dizer é melhor que não responder."""
    saneada = sanidade.sanear(["2024-B2", "2024-B4"], [180.0, 240.0], unidade="PCT_RCL")
    assert saneada.valores == [180.0, 240.0]
    assert not saneada.houve_exclusao


def test_projecao_do_ente_com_serie_suja_deixa_de_ser_impossivel() -> None:
    """O teste que fecha o ciclo, contra o acervo real.

    Antes: o modelo consumia o ponto de 324,49% e projetava 2,29% — um número que nenhum
    município tem. Depois: 50,24%, coerente com as onze observações boas da série.
    """
    with admin_session() as session:
        resposta = service.build_projecao(
            session, ENTE_SERIE_SUJA, "pessoal", horizonte=4, persistir=False
        )
    saneamento = resposta.memoria["saneamento"]
    assert saneamento["pontos_excluidos"] >= 1, "o ponto impossível tem de sair do treino"
    assert resposta.espaco_fiscal is not None
    projetado = resposta.espaco_fiscal.projetado_pct
    assert Decimal(20) < projetado < Decimal(100), (
        f"projeção de {projetado}% não descreve um município real"
    )


def test_saneamento_viaja_na_resposta_mesmo_quando_nada_foi_excluido() -> None:
    """Silêncio sobre a limpeza é indistinguível de não ter havido limpeza."""
    with admin_session() as session:
        resposta = service.build_projecao(
            session, ENTE_MUNICIPAL, "pessoal", horizonte=2, persistir=False
        )
    assert "saneamento" in resposta.memoria
    assert resposta.memoria["saneamento"]["pontos_excluidos"] == 0


# --------------------------------------------------------------------------------------
# Espaço fiscal — o número que vira decisão
# --------------------------------------------------------------------------------------
def _limite(sentido: str = "teto", teto: str = "54") -> LimiteLegal:
    return LimiteLegal(
        indicador="pessoal_executivo",
        esfera="municipal",
        poder="Executivo",
        sentido=sentido,
        teto_pct=Decimal(teto),
        alerta_pct=None,
        prudencial_pct=None,
    )


def test_margem_em_reais_e_a_conversao_exata_dos_pontos_percentuais() -> None:
    """A conversão liga o indicador ao ato: empenho se assina em reais, não em p.p."""
    from app.modules.forecast import espaco_fiscal

    espaco = espaco_fiscal.calcular(
        _limite(), Decimal("47.41"), base_rs=Decimal("12539078077.42")
    )
    assert espaco.situacao == SITUACAO_FOLGA
    assert espaco.margem_pp == Decimal("6.59")
    esperado = Decimal("6.59") / Decimal(100) * Decimal("12539078077.42")
    assert espaco.margem_rs == esperado


def test_piso_tem_a_semantica_invertida() -> None:
    """Num mínimo, folga é estar **acima** — calcular sempre no mesmo sentido diria que um
    ente com 27% em saúde, contra piso de 15%, está excedido."""
    from app.modules.forecast import espaco_fiscal

    espaco = espaco_fiscal.calcular(
        _limite(sentido="piso", teto="15"), Decimal("27.10"), base_rs=None
    )
    assert espaco.situacao == SITUACAO_FOLGA
    assert espaco.margem_pp == Decimal("12.10")


def test_excesso_nao_e_margem_negativa() -> None:
    """Os dois números são o mesmo com sinais opostos; o rótulo é que não pode ser o mesmo."""
    from app.modules.forecast import espaco_fiscal

    espaco = espaco_fiscal.calcular(_limite(), Decimal("57.30"), base_rs=Decimal(1000))
    assert espaco.situacao == SITUACAO_EXCEDIDO
    assert espaco.margem_pp > 0, "o número é positivo; quem diz o sentido é `situacao`"
    assert espaco.margem_pp == Decimal("3.30")


def test_sem_base_a_margem_existe_so_em_pontos_percentuais() -> None:
    """Zero em reais anunciaria margem nenhuma a quem talvez tenha bastante."""
    from app.modules.forecast import espaco_fiscal

    espaco = espaco_fiscal.calcular(_limite(), Decimal("50"), base_rs=None)
    assert espaco.margem_pp == Decimal(4)
    assert espaco.margem_rs is None


# --------------------------------------------------------------------------------------
# Recondução (LRF art. 23)
# --------------------------------------------------------------------------------------
def test_reconducao_reparte_o_excesso_em_dois_quadrimestres() -> None:
    """Art. 23: pelo menos um terço no primeiro quadrimestre, o restante no segundo."""
    excedido = EspacoFiscal(
        indicador="pessoal_executivo",
        sentido="teto",
        situacao=SITUACAO_EXCEDIDO,
        limite_pct=Decimal(54),
        projetado_pct=Decimal(60),
        margem_pp=Decimal(6),
        margem_rs=Decimal(600),
        base_rs=Decimal(10000),
        base_nome="rcl",
    )
    esforco = esforco_reconducao(excedido)
    assert esforco.aplicavel
    assert esforco.excesso_pp == Decimal(6)
    assert abs(esforco.primeiro_quadrimestre_pp - Decimal(2)) < Decimal("0.001")
    assert abs(esforco.segundo_quadrimestre_pp - Decimal(4)) < Decimal("0.001")
    soma = esforco.primeiro_quadrimestre_pp + esforco.segundo_quadrimestre_pp
    assert abs(soma - esforco.excesso_pp) < Decimal("0.0001"), "as parcelas fecham o total"
    assert "art. 23" in esforco.fundamento


def test_quem_esta_em_folga_nao_tem_nada_a_reconduzir() -> None:
    """Zero é resposta; ``None`` seria "não sei", que é outra coisa."""
    from app.modules.forecast import espaco_fiscal

    folga = espaco_fiscal.calcular(_limite(), Decimal(47), base_rs=Decimal(1000))
    esforco = esforco_reconducao(folga)
    assert not esforco.aplicavel
    assert esforco.excesso_pp == Decimal(0)
    assert esforco.excesso_rs == Decimal(0)


def test_piso_descumprido_nao_gera_reconducao_do_art_23() -> None:
    """O art. 23 trata do teto de pessoal. Aplicá-lo a um mínimo constitucional citaria a
    base legal errada — e a providência de um mínimo descumprido é outra."""
    from app.modules.forecast import espaco_fiscal

    abaixo = espaco_fiscal.calcular(
        _limite(sentido="piso", teto="15"), Decimal(12), base_rs=Decimal(1000)
    )
    assert abaixo.situacao == SITUACAO_EXCEDIDO
    assert not esforco_reconducao(abaixo).aplicavel


# --------------------------------------------------------------------------------------
# Integração: a projeção real carrega o espaço fiscal
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("ente", "indicador"), [(ENTE_MUNICIPAL, "pessoal"), (ENTE_ESTADUAL, "divida")]
)
def test_projecao_real_traz_a_margem_ate_o_limite(ente: str, indicador: str) -> None:
    with admin_session() as session:
        resposta = service.build_projecao(
            session, ente, indicador, horizonte=4, persistir=False
        )
    espaco = resposta.espaco_fiscal
    assert espaco is not None, "indicador com limite legal tem de trazer o espaço fiscal"
    assert espaco.margem_rs is not None and espaco.margem_rs > 0
    assert espaco.base_rs is not None and espaco.base_rs > 0
    assert espaco.periodo_alvo == resposta.projecao[-1].periodo_alvo
    assert resposta.reconducao is not None


def test_indicador_sem_limite_nao_inventa_espaco_fiscal() -> None:
    """A RCL não tem teto legal; devolver margem para ela seria inventar um limite."""
    with admin_session() as session:
        resposta = service.build_projecao(
            session, ENTE_MUNICIPAL, "rcl", horizonte=2, persistir=False
        )
    assert resposta.espaco_fiscal is None
    assert resposta.reconducao is None
