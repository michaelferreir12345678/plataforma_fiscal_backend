"""Ente de abertura da sessão e a explicação da CAPAG ainda não publicada.

Dois pedidos com a mesma raiz: **o sistema sabe algo que não estava contando ao gestor**.
No primeiro, qual ente faz sentido abrir (o backend conhece o tipo da conta; o frontend
tinha uma variável de ambiente única e todo mundo abria em Fortaleza). No segundo, por que
a nota não aparece num exercício em curso — "sem dado" faz procurar defeito na plataforma
quando a resposta é que o Tesouro ainda não publicou.
"""

from __future__ import annotations

from app.core.db import admin_session
from app.core.errors import AppError
from app.modules.debt import service as debt_service
from app.modules.tenancy import repository as tenancy_repo
from app.modules.tenancy import service as tenancy_service


def _me(fixture):
    with admin_session() as s:
        views = tenancy_repo.membership_views_for_user(s, fixture.usuario_id)
        return tenancy_service.build_me(fixture.usuario_id, views[0].org_id if views else None)


def test_conta_estadual_abre_no_governo_do_estado(make_org) -> None:
    """Uma Sefaz monitora o estado inteiro, mas a sessão é dela: abre no próprio estado."""
    org = make_org(tipo_conta="estado", entes=["23", "2304400", "2303709"])
    padrao = _me(org).ente_padrao
    assert padrao is not None
    assert padrao.cod_ibge == "23", "conta estadual deve abrir no ente estadual, não num município"


def test_prefeitura_abre_no_proprio_municipio(make_org) -> None:
    org = make_org(tipo_conta="prefeitura", entes=["2304400"])
    padrao = _me(org).ente_padrao
    assert padrao is not None
    assert padrao.cod_ibge == "2304400"


def test_membro_restrito_nao_abre_fora_do_seu_escopo(make_org) -> None:
    """Conta estadual, mas o usuário está limitado a um município por membership_escopo.

    Abrir no ente estadual daria 403 na primeira tela — o padrão respeita a restrição.
    """
    org = make_org(tipo_conta="estado", entes=["23", "2304400"], escopo=["2304400"])
    padrao = _me(org).ente_padrao
    assert padrao is not None
    assert padrao.cod_ibge == "2304400"


def test_organizacao_sem_carteira_nao_inventa_ente(make_org) -> None:
    org = make_org(tipo_conta="consultoria", entes=[])
    assert _me(org).ente_padrao is None


def test_capag_nao_publicada_explica_o_motivo() -> None:
    """A CAPAG é anual e sai depois do exercício encerrado; o erro precisa dizer isso.

    Antes: "Sem entrega nacional CAPAG-EST para 2099" — verdadeiro e inútil para o gestor.
    """
    with admin_session() as s:
        try:
            debt_service._ensure_capag(s, "23", 2099, as_of=None)
        except AppError as exc:
            assert exc.status == 404
            assert exc.type == "urn:plataforma-fiscal:error:capag-nao-publicada"
            detalhe = str(exc.detail)
            # O gestor precisa dos três fatos: cadência, escopo e o que continua valendo.
            assert "uma vez por exercício" in detalhe
            assert "estados" in detalhe
            assert "não acompanha o quadrimestre" in detalhe
        else:  # pragma: no cover - só se alguém publicar a CAPAG de 2099
            raise AssertionError("esperava AppError para exercício sem publicação")
