"""Glossário estático de nomes PCASP (Sprint D1) — snapshot + backfill de metadado.

`app.modules.accounting.pcasp_glossario` é um dicionário estático (código PCASP canônico
→ nome oficial), extraído da Portaria STN/MDF ("PCASP — Estendido — 2024", ver a docstring
do módulo para fonte/data/recorte exatos). Este arquivo não valida o conteúdo linha a linha
contra o Tesouro (isso já foi feito na extração, e é documentado no módulo) — protege contra
**regressão**: formato inválido, encolhimento silencioso do dicionário, fora do recorte
declarado (nível 6-7, classes 1-4), e a amostra conhecida que também aparece nos fixtures
de ``test_accounting.py``.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import text

from app.core.db import admin_session
from app.modules.accounting import pcasp, pcasp_glossario, service

_MASCARA_RE = re.compile(r"^\d\.\d\.\d\.\d\.\d\.\d{2}\.\d{2}$")

# Amostra conhecida (conferida contra a planilha do Tesouro na extração) — regressão.
_AMOSTRA: dict[str, str] = {
    "1.1.1.1.1.01.00": "Caixa",
    "1.1.1.1.1.02.00": "Conta Única",
    "2.3.7.1.1.01.00": "Superávits ou Déficits do Exercício",
}


def test_amostra_conhecida() -> None:
    for codigo, esperado in _AMOSTRA.items():
        assert pcasp_glossario.nome_oficial(codigo) == esperado


def test_codigo_fora_do_recorte_devolve_none() -> None:
    assert pcasp_glossario.nome_oficial("9.9.9.9.9.99.99") is None
    assert pcasp_glossario.nome_oficial("") is None


def test_tamanho_nao_regride() -> None:
    """Documentado no módulo: ~3.500 contas nível 6-7 (classes 1-4). Uma queda grande
    denunciaria truncamento silencioso do arquivo — não travamos num número exato porque
    o dicionário pode só crescer numa atualização de portaria."""
    assert len(pcasp_glossario.GLOSSARIO) >= 3000


def test_todo_codigo_e_mascara_canonica_nivel_6_ou_7_classe_1_a_4() -> None:
    """Recorte declarado na docstring do módulo (§ "Recorte"): nível 6 (Item) ou 7
    (Subitem), classes 1-4 (Ativo, Passivo/PL, VPD, VPA) — as únicas que aparecem na MSC
    Patrimonial/Variações que o Explorador MSC exibe."""
    for codigo in pcasp_glossario.GLOSSARIO:
        assert _MASCARA_RE.match(codigo), f"{codigo!r} não é a máscara canônica C.G.SG.T.ST.II.SS"
        conta = pcasp.parse(codigo)
        assert conta is not None, f"{codigo!r} não é um código PCASP válido"
        assert conta.nivel in (6, 7), f"{codigo!r} está no nível {conta.nivel}, fora do recorte"
        assert conta.classe in (1, 2, 3, 4), (
            f"{codigo!r} é da classe {conta.classe}, fora do recorte"
        )


def test_nomes_nao_sao_vazios() -> None:
    assert all(nome.strip() for nome in pcasp_glossario.GLOSSARIO.values())


def test_sem_codigo_duplicado_por_construcao() -> None:
    # dict já garante chave única; a checagem serve de documentação executável do invariante.
    assert len(pcasp_glossario.GLOSSARIO) == len(set(pcasp_glossario.GLOSSARIO))


# --------------------------------------------------------------------------------- #
# Backfill de metadado (contas já materializadas antes da Sprint D1)
# --------------------------------------------------------------------------------- #
def test_backfill_substitui_so_o_fallback_generico_do_proprio_codigo() -> None:
    """Simula o estado "pré-D1": uma conta materializada com o rótulo genérico
    ``pcasp.nome_no`` (o que toda folha 6-7 sem descrição da DCA tinha antes desta
    sprint). O backfill deve trocar pelo nome oficial — e só isso, nunca uma descrição
    já vinda da DCA (``descricao_autoritativa``).

    ``gold.dim_conta_pcasp`` é uma dimensão **compartilhada** (não é por ente) — entes
    reais já materializados (ex.: São Paulo) podem já ter linhas para códigos do
    glossário. Os dois códigos usados aqui são escolhidos **dinamicamente**, checando
    que ainda não existem na tabela, para o teste nunca sobrescrever/apagar uma linha de
    dado real ao limpar seus próprios fixtures.
    """
    with admin_session() as s:
        ja_existentes = {
            row[0]
            for row in s.execute(text("select codigo from gold.dim_conta_pcasp")).all()
        }
    # As entes reais materializados podem já cobrir boa parte dos códigos de nível
    # baixo (1-3) de tanto materializar ancestrais — por isso os dois códigos usados
    # aqui vêm do próprio glossário (nível 6-7), onde a chance de colisão é muito menor
    # (nenhum ente real usa as ~3.500 contas possíveis, só um subconjunto).
    livres = [c for c in pcasp_glossario.GLOSSARIO if c not in ja_existentes]
    if len(livres) < 2:
        pytest.skip("menos de 2 códigos do glossário livres neste banco para o cenário")
    codigo_generico = livres[0]
    nome_oficial = pcasp_glossario.GLOSSARIO[codigo_generico]
    fallback = pcasp.nome_no(codigo_generico)

    # Um código com descrição já autoritativa (ex.: viria da DCA) — não deve mudar. Não
    # precisa estar no glossário como tal; usa-se outro código livre só para simular o
    # cenário "nome já veio de fonte autoritativa" sem tocar o nome real do glossário.
    codigo_autoritativo = livres[1]
    nome_dca = "Ativo Circulante (DCA)"
    conta_generica = pcasp.require(codigo_generico)
    conta_autoritativa = pcasp.require(codigo_autoritativo)

    with admin_session() as s:
        s.execute(
            text(
                """
                insert into gold.dim_conta_pcasp
                    (codigo, descricao, parent_codigo, nivel, path, classe, natureza, fonte_dado)
                values (:cod, :desc, null, :nivel, :cod, :classe, :nat, 'msc')
                """
            ),
            {
                "cod": codigo_generico, "desc": fallback, "nivel": conta_generica.nivel,
                "classe": conta_generica.classe, "nat": conta_generica.natureza,
            },
        )
        s.execute(
            text(
                """
                insert into gold.dim_conta_pcasp
                    (codigo, descricao, parent_codigo, nivel, path, classe, natureza, fonte_dado)
                values (:cod, :desc, null, :nivel, :cod, :classe, :nat, 'dca')
                """
            ),
            {
                "cod": codigo_autoritativo, "desc": nome_dca, "nivel": conta_autoritativa.nivel,
                "classe": conta_autoritativa.classe, "nat": conta_autoritativa.natureza,
            },
        )
        # Também no rollup denormalizado (mart_msc_rollup), que carrega sua própria
        # cópia de `descricao` — o backfill precisa atualizar as duas tabelas.
        ente_teste = "9999998"
        s.execute(
            text(
                """
                insert into gold.mart_msc_rollup
                    (id, cod_ibge, uf, ano, periodo, mes, cod_conta, parent_conta, nivel,
                     classe, natureza, descricao, has_children, versao_entrega)
                values (:id, :cod_ibge, 'CE', 2024, '2024-M01', 1, :cod, null, :nivel,
                        :classe, :nat, :desc, false, '1')
                """
            ),
            {
                "id": uuid.uuid4(), "cod_ibge": ente_teste, "cod": codigo_generico,
                "desc": fallback, "nivel": conta_generica.nivel,
                "classe": conta_generica.classe, "nat": conta_generica.natureza,
            },
        )
        s.commit()

    try:
        with admin_session() as s:
            atualizados = service.backfill_glossario_pcasp(s)
            s.commit()
        assert atualizados >= 2  # a linha de dim_conta_pcasp + a de mart_msc_rollup

        with admin_session() as s:
            pos_generico = s.scalar(
                text("select descricao from gold.dim_conta_pcasp where codigo = :c"),
                {"c": codigo_generico},
            )
            pos_autoritativo = s.scalar(
                text("select descricao from gold.dim_conta_pcasp where codigo = :c"),
                {"c": codigo_autoritativo},
            )
            pos_rollup = s.scalar(
                text(
                    "select descricao from gold.mart_msc_rollup "
                    "where cod_ibge = :e and cod_conta = :c"
                ),
                {"e": ente_teste, "c": codigo_generico},
            )
        assert pos_generico == nome_oficial
        assert pos_autoritativo == nome_dca, "descrição vinda da DCA nunca é sobrescrita"
        assert pos_rollup == nome_oficial

        # Idempotente: a linha já não é mais o fallback, então uma 2ª passada não a toca
        # (a asserção é sobre a linha em si, não sobre a contagem global — o backfill
        # varre a tabela inteira, e outras linhas fora deste teste podem legitimamente
        # ainda estar pendentes num banco de desenvolvimento real).
        with admin_session() as s:
            service.backfill_glossario_pcasp(s)
            s.commit()
        with admin_session() as s:
            ainda = s.scalar(
                text("select descricao from gold.dim_conta_pcasp where codigo = :c"),
                {"c": codigo_generico},
            )
        assert ainda == nome_oficial
    finally:
        with admin_session() as s:
            s.execute(
                text("delete from gold.dim_conta_pcasp where codigo in (:a, :b)"),
                {"a": codigo_generico, "b": codigo_autoritativo},
            )
            s.execute(
                text("delete from gold.mart_msc_rollup where cod_ibge = :e"),
                {"e": ente_teste},
            )
            s.commit()


@pytest.mark.parametrize("codigo", ["1.1.1.1.1.01.00", "2.3.7.1.1.01.00", "4.9.9.6.1.02.00"])
def test_amostra_bate_com_pcasp_nome_no_diferente(codigo: str) -> None:
    """O glossário deve divergir do fallback genérico — senão não agrega nada."""
    oficial = pcasp_glossario.nome_oficial(codigo)
    assert oficial is not None
    assert oficial != pcasp.nome_no(codigo)
