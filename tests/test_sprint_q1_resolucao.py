"""Sprint Q1 — o fluxo de resolução de uma verificação em falha.

O que se prova aqui não é que os botões existem: é que **a ação oferecida corresponde a
quem é o dono do número**. Oferecer "reprocessar" numa divergência da fonte gasta o tempo
do gestor, não muda o resultado e ensina a desconfiar do botão — e um botão em que ninguém
confia é pior que botão nenhum.

Por isso quase todo teste aqui vem em par: um mostra o que o fluxo permite, o outro mostra
o que ele **recusa**.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from app.modules.quality import causa as causa_mod
from app.modules.quality import checks as checks_mod
from app.modules.quality.causa import ACOES_POR_CLASSE, causa_do_check
from app.modules.quality.checks import SLAS


# --------------------------------------------------------------------------- #
# 1. A classificação: de quem é o número que não fechou
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("check_codigo", "classe"),
    [
        # Dois lados nossos ⇒ defeito nosso, há o que corrigir.
        ("mart_vs_detalhe_pessoal", "plataforma"),
        ("minimo_saude_recalculado", "plataforma"),
        ("minimo_educacao_recalculado", "plataforma"),
        # Dois lados do ente ⇒ a publicação é que está inconsistente.
        ("dcl_a6_vs_rgf", "fonte"),
        ("receita_soma_filhos", "fonte"),
        ("despesa_estagios_monotonicos", "fonte"),
        ("msc_vs_dca", "fonte"),
        # Um de cada ⇒ descarta-se a hipótese nossa primeiro.
        ("rcl_calculada_vs_publicada", "misto"),
        # Defasagem não diz de quem é a falta.
        ("freshness_rreo", "cobertura"),
        ("freshness_rgf", "cobertura"),
        ("freshness_dca", "cobertura"),
        ("freshness_msc", "cobertura"),
    ],
)
def test_classe_sai_do_que_o_check_compara(check_codigo: str, classe: str) -> None:
    assert causa_do_check(check_codigo).classe == classe


def test_todo_check_do_catalogo_esta_classificado() -> None:
    """Um check sem classe cairia no default e nunca ofereceria a ação certa.

    O teste amarra o catálogo real (``SLAS`` + ``CAUSA_POR_CHECK``) à classificação: um
    check novo que entre no produto sem passar por aqui é pego na suíte, não em produção.
    """
    do_catalogo = set(causa_mod.CAUSA_POR_CHECK) | {
        f"freshness_{s.relatorio.lower()}" for s in SLAS
    }
    for codigo in do_catalogo:
        assert causa_do_check(codigo).classe in {"plataforma", "fonte", "misto", "cobertura"}


def test_check_desconhecido_nao_acusa_a_fonte() -> None:
    """Default conservador: sem classificação, a hipótese nossa se descarta primeiro.

    O contrário seria pior — um check novo começaria dizendo ao gestor que o ente publicou
    errado, com base em nada.
    """
    causa = causa_do_check("check_que_ainda_nao_existe")
    assert causa.classe == "misto"
    assert "rematerializar" in ACOES_POR_CLASSE[causa.classe]


# --------------------------------------------------------------------------- #
# 2. A ação cabível — e, sobretudo, a que NÃO cabe
# --------------------------------------------------------------------------- #
def test_falha_da_fonte_nao_oferece_reprocessamento() -> None:
    """O ponto central da sprint.

    ``dcl_a6_vs_rgf`` compara dois demonstrativos que o **ente** publicou. Rematerializar
    lê os mesmos dois números e chega ao mesmo resultado: a ação não existe para essa
    classe, e oferecê-la seria uma promessa que a plataforma não pode cumprir.
    """
    acoes = ACOES_POR_CLASSE[causa_do_check("dcl_a6_vs_rgf").classe]
    assert "rematerializar" not in acoes
    assert acoes == ("aceitar_como_fato",)


def test_falha_da_plataforma_oferece_reprocessamento_e_nao_aceite() -> None:
    """Controle negativo do teste acima: onde o defeito é nosso, aceitar não é opção.

    Se ``aceitar_como_fato`` aparecesse aqui, o fluxo permitiria arquivar como "fato da
    fonte" uma divergência entre dois números que a própria plataforma produziu — que é
    exatamente o modo de esconder um defeito nosso com aparência de processo.
    """
    acoes = ACOES_POR_CLASSE[causa_do_check("mart_vs_detalhe_pessoal").classe]
    assert acoes == ("rematerializar",)
    assert "aceitar_como_fato" not in acoes


def test_defasagem_nao_promete_reingestao_antes_de_olhar_a_fonte() -> None:
    """A classe ``cobertura`` não tem ação fixa: ela depende do diagnóstico.

    "O RGF está com 80 dias" pode ser o ente que não publicou ou nós que não ingerimos.
    Oferecer "reingerir" antes de saber seria prometer que existe o que ingerir.
    """
    assert ACOES_POR_CLASSE[causa_do_check("freshness_rgf").classe] == ()


# --------------------------------------------------------------------------- #
# 3. A régua não pode carregar o vício que existe para achar
#
# Achado por uso, e é o pior tipo de defeito num sistema de verificação: o check
# `mart_vs_detalhe_pessoal` dividia pela RCL **cheia**, enquanto o indicador (corrigido na
# Sprint 28, migration 0035) divide pela RCL **Ajustada**. A régua acusava de errado
# exatamente o valor correto — 8 falhas em produção, todas falso positivo, e o painel de
# resolução mandando reprocessar dado que já estava certo.
#
# Medido no ente 23 em 2026-B2:
#   R$ 16.679.957.857,72 ÷ RCL Ajustada R$ 40.690.096.057,23 = 40,9927%  (o mart)
#   R$ 16.679.957.857,72 ÷ RCL cheia    R$ 40.899.706.794,11 = 40,7826%  (o check errado)
# --------------------------------------------------------------------------- #
def test_check_de_pessoal_usa_a_rcl_ajustada_e_nao_a_cheia() -> None:
    """O denominador do check tem de ser o mesmo do indicador — fonte única.

    O teste lê o código: chamar ``endividamento.rcl_ajustada`` é o que garante que uma
    mudança futura na regra do denominador chegue aos dois lados juntos. Foi a divergência
    entre eles que produziu o falso positivo.
    """
    fonte = inspect.getsource(checks_mod.mart_vs_detalhe_pessoal)
    # Sem a docstring: ela cita `rcl_12m` de propósito, ao contar por que o denominador
    # errado esteve ali. Documentar o defeito não pode reprovar a correção.
    doc = checks_mod.mart_vs_detalhe_pessoal.__doc__ or ""
    corpo = fonte.replace(doc, "")
    assert "rcl_ajustada" in corpo, "o check tem de usar a RCL Ajustada"
    assert "rcl_12m" not in corpo, (
        "a RCL cheia é o denominador errado destes limites (CLAUDE.md §2, Sprint 28)"
    )


def test_reconciliacao_de_pessoal_bate_quando_o_denominador_e_o_mesmo() -> None:
    """Com o denominador certo, os números reais de produção fecham dentro da tolerância."""
    despesa = Decimal("16679957857.72")
    rcl_ajustada = Decimal("40690096057.23")
    rcl_cheia = Decimal("40899706794.11")
    mart_gravado = Decimal("40.99267260087047587826243186")

    com_ajustada = despesa / rcl_ajustada * Decimal(100)
    com_cheia = despesa / rcl_cheia * Decimal(100)

    assert abs(com_ajustada - mart_gravado) < Decimal("0.01")
    # E o controle negativo: com a RCL cheia a diferença estoura a tolerância — que é
    # exatamente a falha que o gestor via na tela.
    assert abs(com_cheia - mart_gravado) > Decimal("0.01")


def test_lados_do_check_de_pessoal_estao_rotulados_na_ordem_certa() -> None:
    """O rótulo tem de dizer de onde veio cada número.

    Estavam trocados: o painel mostrava "semáforo: 40,78 / detalhe: 40,99" quando é o
    inverso. Atribuir o número à origem errada é pior que não mostrar origem nenhuma —
    manda o gestor investigar o lado que não tem problema.
    """
    causa = causa_do_check("mart_vs_detalhe_pessoal")
    # O check passa `esquerda=recalculado` e `direita=mart` (ver checks.py).
    assert "detalhe" in causa.esquerda
    assert "mart_indicador" in causa.direita
