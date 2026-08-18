"""Cobertura honesta: a página declara para quantos entes ela de fato responde.

O achado que originou esta funcionalidade: os mínimos constitucionais estão apurados para
**1** ente contra 180 com RREO ingerido, e a página de Saúde & Educação abria para
qualquer um sem dizer isso. O gestor que via "sem dado para este ente" concluía que o
**ente** não entregou — quando o que falta é a nossa carga.

As duas leituras levam a ações opostas: a primeira faz cobrar o setor contábil do
município; a segunda, a plataforma. Só a segunda é verdade.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, text

from app.core.db import admin_session
from app.core.errors import AppError
from app.modules.coverage import service
from app.modules.ingestion.models import MartCoberturaFonte


def _escopo_ce() -> set[str]:
    """Escopo de uma Sefaz do Ceará: os 184 municípios mais o estado."""
    with admin_session() as s:
        municipios = {
            r[0]
            for r in s.execute(
                text("select cod_ibge from gold.dim_ente where uf='CE' and esfera='municipal'")
            )
        }
    return municipios | {"23"}


def test_o_numero_em_destaque_reflete_o_produto_da_pagina_nao_o_insumo() -> None:
    """O caso que motivou tudo: Saúde & Educação responde para 1, não para 171.

    171 entes têm RREO — o insumo. Os mínimos, que são o que a página **apresenta**, estão
    apurados para 1. Usar o máximo entre as fontes daria "171 de 185" em destaque: a
    cobertura do insumo passando por cobertura do produto.
    """
    escopo = _escopo_ce()
    with admin_session() as s:
        c = service.build_cobertura_pagina(
            s, pagina="saude-educacao", cod_ibge="2304400", periodo="2025-B6",
            entes_do_escopo=escopo,
        )
    rreo = next(f for f in c.fontes if f.fonte == "siconfi_rreo")
    assert rreo.entes_com_dado > 100, "o insumo alcança quase todo o escopo"
    assert c.escopo.entes_com_dado <= 2, (
        f"o produto alcança quase ninguém; em destaque tem de ir o produto "
        f"(veio {c.escopo.entes_com_dado})"
    )
    assert c.escopo.entes_no_escopo == len(escopo)


def test_lacuna_diz_de_quem_e_a_ausencia() -> None:
    """A frase é o produto desta funcionalidade, não um enfeite."""
    with admin_session() as s:
        c = service.build_cobertura_pagina(
            s, pagina="saude-educacao", cod_ibge="2304400", periodo="2025-B6",
            entes_do_escopo=_escopo_ce(),
        )
    assert set(c.lacunas) >= {"saude_minimo", "educacao_mde", "fundeb_profissionais"}
    assert c.observacao is not None
    assert "da nossa carga" in c.observacao, "tem de atribuir a ausência a quem ela pertence"


def test_pagina_bem_coberta_nao_alarma() -> None:
    """Falso alarme gasta a atenção que a lacuna real precisa."""
    with admin_session() as s:
        c = service.build_cobertura_pagina(
            s, pagina="patrimonio", cod_ibge="2304400", periodo="2024",
            entes_do_escopo=_escopo_ce(),
        )
    assert c.escopo.entes_com_dado > 100
    assert not c.lacunas


def test_escopo_vazio_nao_e_cobertura_zero() -> None:
    """Sem carteira não há cobertura a medir — dizer "0 de 0" sugeriria falta de dado."""
    with admin_session() as s:
        c = service.build_cobertura_pagina(
            s, pagina="receita", cod_ibge="2304400", periodo="2025-B6", entes_do_escopo=set()
        )
    assert c.escopo.entes_no_escopo == 0
    assert c.observacao is not None
    assert "carteira" in c.observacao


def test_periodo_restringe_contagens_e_selo_do_ente() -> None:
    """Dado em outro período não pode preencher uma tela vazia no período solicitado."""
    cod_ibge = f"9{uuid.uuid4().int % 1_000_000:06d}"
    periodo_com_dado = "2088-B5"
    periodo_sem_dado = "2088-B6"
    try:
        with admin_session() as s:
            s.add(
                MartCoberturaFonte(
                    fonte="siconfi_rreo",
                    cod_ibge=cod_ibge,
                    periodo=periodo_com_dado,
                    uf="CE",
                    ano=2088,
                    n_registros=1,
                    versao_entrega_vigente="v1",
                )
            )

        with admin_session() as s:
            com_dado = service.build_cobertura_pagina(
                s,
                pagina="receita",
                cod_ibge=cod_ibge,
                periodo=periodo_com_dado,
                entes_do_escopo={cod_ibge},
            )
            sem_dado = service.build_cobertura_pagina(
                s,
                pagina="receita",
                cod_ibge=cod_ibge,
                periodo=periodo_sem_dado,
                entes_do_escopo={cod_ibge},
            )
    finally:
        with admin_session() as s:
            s.execute(
                delete(MartCoberturaFonte).where(MartCoberturaFonte.cod_ibge == cod_ibge)
            )

    fonte_com_dado = next(f for f in com_dado.fontes if f.fonte == "siconfi_rreo")
    fonte_sem_dado = next(f for f in sem_dado.fontes if f.fonte == "siconfi_rreo")
    assert com_dado.ente.tem_dado is True
    assert com_dado.ente.periodo_mais_recente == periodo_com_dado
    assert fonte_com_dado.entes_com_dado == 1
    assert sem_dado.ente.tem_dado is False
    assert sem_dado.ente.periodo_mais_recente is None
    assert fonte_sem_dado.entes_com_dado == 0


def test_o_mapa_de_paginas_vem_do_catalogo_de_fontes() -> None:
    """Sem segunda lista: `FONTE_META.paginas_impactadas` invertido (§6 do CLAUDE.md).

    Uma lista paralela de "quais fontes cada página usa" envelheceria em silêncio na
    primeira fonte nova.
    """
    mapa = service.fontes_por_pagina()
    assert "siconfi_rreo" in mapa["receita"]
    assert "tesouro_capag" in mapa["divida"]
    assert "siconfi_msc" in mapa["patrimonio"]


def test_pagina_desconhecida_lista_as_conhecidas() -> None:
    with admin_session() as s, pytest.raises(AppError) as exc:
        service.build_cobertura_pagina(
            s, pagina="inexistente", cod_ibge="2304400", periodo=None, entes_do_escopo={"23"}
        )
    assert exc.value.status == 404
    assert "receita" in str(exc.value.detail), "o erro tem de ensinar as chaves válidas"
