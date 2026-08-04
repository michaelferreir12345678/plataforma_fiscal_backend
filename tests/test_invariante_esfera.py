"""A esfera do ente é a invariante nº 1 do domínio — e precisa ser verificada por **dado**.

O CLAUDE.md abre as regras invariantes com "a esfera/porte do ente determina o cálculo;
nunca aplicar limite sem checar `dim_ente.esfera`". O código cumpria: `_ente()` levanta 422
quando a esfera é nula. O **dado** não cumpria: União e Distrito Federal estavam com
`esfera IS NULL`, porque o SICONFI publica quatro esferas (`M`, `E`, `D`, `U`) e o
normalizador só conhecia duas.

A consequência não é o 422 — é o que ele esconde. O DF acumula competências estaduais e
municipais (CF, art. 32, §1º) e responde pelo teto **estadual** de pessoal, 49% da RCL no
Executivo (LRF, art. 20, II). Com esfera nula, nenhum limite lhe é aplicável, e a
plataforma trata a maior unidade subnacional do país como ente de esfera desconhecida.

Uma invariante conferida só no código é uma invariante que o dado pode violar em silêncio.
"""

from __future__ import annotations

import pytest

from app.core.db import admin_session
from app.modules.catalog.models import ESFERA_ESTADUAL, ESFERA_FEDERAL, ESFERA_MUNICIPAL
from app.modules.catalog.service import _normalizar_esfera


@pytest.mark.parametrize(
    ("publicado", "esperado"),
    [
        ("M", ESFERA_MUNICIPAL),
        ("E", ESFERA_ESTADUAL),
        # O Tesouro classifica o DF junto dos estados: a CAPAG dos estados tem 27 entes,
        # os 26 estados mais o DF. O teto de pessoal que lhe cabe é o estadual.
        ("D", ESFERA_ESTADUAL),
        # Conhecida e sem limite cadastrado — que é diferente de desconhecida.
        ("U", ESFERA_FEDERAL),
        ("municipal", ESFERA_MUNICIPAL),
        ("estadual", ESFERA_ESTADUAL),
        (" e ", ESFERA_ESTADUAL),
    ],
)
def test_toda_esfera_publicada_pela_fonte_tem_traducao(publicado: str, esperado: str) -> None:
    assert _normalizar_esfera(publicado) == esperado


@pytest.mark.parametrize("valor", [None, "", "   ", "X"])
def test_esfera_desconhecida_continua_nula(valor: str | None) -> None:
    """Inventar uma esfera seria pior que admitir que não se sabe — o teto sairia errado."""
    assert _normalizar_esfera(valor) is None


def test_nenhum_ente_do_catalogo_fica_sem_esfera() -> None:
    """A invariante, verificada no dado e não só no código.

    Este é o teste que faltava: ele falha se uma esfera nova aparecer na fonte sem
    tradução, em vez de deixá-la virar `NULL` e reaparecer como "esfera desconhecida"
    numa tela de limite.
    """
    from sqlalchemy import text

    with admin_session() as s:
        sem_esfera = list(
            s.execute(text("select cod_ibge, nome from gold.dim_ente where esfera is null"))
        )
    assert not sem_esfera, f"entes sem esfera no catálogo: {sem_esfera}"


def test_o_catalogo_cobre_todo_ente_conhecido_pela_fonte() -> None:
    """`dim_ente` é conformado **sob demanda** — e quem nunca foi consultado nunca entrava.

    O catálogo estava com 8 municípios a menos que o silver: 1701051, 4322707, 2610806,
    2205359, 2313252 (Tarrafas), 2402709, 2501153 e 2106805. A consequência visível foi o
    consolidado do Ceará reportar **183 de 184** municípios — um erro de denominador que
    aparece em toda média e todo percentual do painel estadual, sem nada na tela sugerir
    que falta um ente.

    Conformação preguiçosa é uma escolha razoável para desempenho; o que não é razoável é
    não ter quem verifique a completude.
    """
    from sqlalchemy import text

    with admin_session() as s:
        faltantes = list(
            s.execute(
                text(
                    """
                    select e.cod_ibge, e.nome
                    from silver.siconfi_entes e
                    where not exists (
                        select 1 from gold.dim_ente d where d.cod_ibge = e.cod_ibge
                    )
                    """
                )
            )
        )
    assert not faltantes, (
        f"{len(faltantes)} entes existem no silver e não no catálogo: {faltantes[:5]}"
    )


def test_esfera_e_coerente_com_o_codigo_ibge() -> None:
    """Município tem 7 dígitos, UF tem 2, União tem 1. Incoerência aqui é erro de carga."""
    from sqlalchemy import text

    with admin_session() as s:
        incoerentes = list(
            s.execute(
                text(
                    """
                    select cod_ibge, nome, esfera, length(cod_ibge) tam
                    from gold.dim_ente
                    where (esfera = 'municipal' and length(cod_ibge) <> 7)
                       or (esfera = 'estadual'  and length(cod_ibge) <> 2)
                       or (esfera = 'federal'   and length(cod_ibge) <> 1)
                    """
                )
            )
        )
    assert not incoerentes, f"esfera incompatível com o código IBGE: {incoerentes}"


def test_o_distrito_federal_responde_pelo_teto_estadual() -> None:
    """Caso concreto que motivou a correção — 49% no Executivo, não 54%."""
    from sqlalchemy import text

    with admin_session() as s:
        esfera = s.scalar(text("select esfera from gold.dim_ente where cod_ibge = '53'"))
        teto = s.scalar(
            text(
                """select teto_pct from gold.dim_limite_legal
                   where indicador = 'pessoal_executivo' and esfera = :e"""
            ),
            {"e": esfera},
        )
    assert esfera == ESFERA_ESTADUAL
    assert teto == 49, "o DF segue o teto estadual de pessoal (LRF, art. 20, II)"
