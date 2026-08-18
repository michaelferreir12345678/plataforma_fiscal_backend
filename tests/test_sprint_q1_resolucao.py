"""Sprint Q1 — o fluxo de resolução de uma verificação em falha.

O que se prova aqui não é que os botões existem: é que **a ação oferecida corresponde a
quem é o dono do número**. Oferecer "reprocessar" numa divergência da fonte gasta o tempo
do gestor, não muda o resultado e ensina a desconfiar do botão — e um botão em que ninguém
confia é pior que botão nenhum.

Por isso quase todo teste aqui vem em par: um mostra o que o fluxo permite, o outro mostra
o que ele **recusa**.
"""

from __future__ import annotations

import pytest

from app.modules.quality import causa as causa_mod
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
