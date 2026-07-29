"""Sprint 19 — control plane: superuser, licenças e identidade visual.

Os critérios de aceite da sprint, um a um: usuário comum (mesmo com ``administrar``)
recebe 403 em ``/platform``; licença ``uf`` libera os municípios do território sem
cadastrá-los; suspender bloqueia **na hora** sem apagar dado; escopo continua
respeitando carteira e membership; e a RLS do tenant segue intacta fora de ``/platform``.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select

from app.core.db import admin_session
from app.core.security import hash_password
from app.modules.tenancy import repository
from app.modules.tenancy.models import AuditLog, Licenca, Organizacao, Usuario
from tests.conftest import auth_header, login

UF_CE = "23"
FORTALEZA = "2304400"
MARACANAU = "2307650"
CAPITAL_FORA = "2611606"  # Recife — serve para provar que a licença de UF não vaza.
# O gate de escopo roda na dependência da rota, mas a validação de query vem antes:
# sem ``periodo`` a resposta é 422 e o teste nunca chegaria a exercitar a licença.
PERIODO = "2024-B6"


@pytest.fixture
def superuser():
    """Operador da plataforma: existe fora de qualquer organização (sem membership)."""
    email = f"plataforma-{uuid.uuid4().hex}@prumo.gov.br"
    senha = "senha1234"
    with admin_session() as s:
        usuario = repository.create_usuario(
            s, email=email, nome="Operador", senha_hash=hash_password(senha), mfa_ativo=False
        )
        usuario.is_superuser = True
        s.flush()
        usuario_id = usuario.id
    yield {"email": email, "senha": senha, "id": usuario_id}
    with admin_session() as s:
        s.execute(delete(AuditLog).where(AuditLog.usuario_id == usuario_id))
        s.execute(delete(Usuario).where(Usuario.id == usuario_id))


def _token_super(client, superuser) -> dict[str, str]:
    return auth_header(login(client, superuser["email"], superuser["senha"]))


# --- A porta ----------------------------------------------------------------


def test_usuario_de_tenant_com_administrar_nao_entra_no_platform(client, make_org) -> None:
    """`administrar` é o topo do RBAC **dentro** da org — não é a porta do control plane."""
    org = make_org(capacidades=["ver", "administrar"], entes=[FORTALEZA])
    headers = auth_header(login(client, org.email, org.senha))

    for metodo, rota in (
        ("get", "/platform/orgs"),
        ("get", "/platform/uso"),
        ("get", f"/platform/orgs/{org.org_id}/licencas"),
    ):
        resposta = getattr(client, metodo)(rota, headers=headers)
        assert resposta.status_code == 403, f"{rota}: {resposta.text}"
        assert resposta.json()["type"].endswith("superuser-required")


def test_sem_autenticacao_platform_responde_401(client) -> None:
    assert client.get("/platform/orgs").status_code == 401


def test_superuser_enxerga_o_control_plane(client, superuser, make_org) -> None:
    make_org(entes=[FORTALEZA])
    resposta = client.get("/platform/orgs", headers=_token_super(client, superuser))
    assert resposta.status_code == 200, resposta.text
    assert len(resposta.json()) >= 1


# --- Provisionamento --------------------------------------------------------


def test_provisionar_cria_org_admin_e_licenca_de_uma_vez(client, superuser) -> None:
    """Org sem papel não cria usuário; usuário sem licença não vê nada. Ou tudo, ou nada."""
    headers = _token_super(client, superuser)
    email_admin = f"admin-{uuid.uuid4().hex}@sefaz.gov.br"
    payload = {
        "nome": "Sefaz de Teste",
        "tipo_conta": "estado",
        "admin_email": email_admin,
        "admin_nome": "Admin Sefaz",
        "admin_senha": "senha1234",
        "licencas": [{"tipo": "uf", "uf": UF_CE}],
    }
    resposta = client.post("/platform/orgs", json=payload, headers=headers)
    assert resposta.status_code == 201, resposta.text
    corpo = resposta.json()
    assert corpo["admin_email"] == email_admin
    assert len(corpo["licencas"]) == 1
    assert corpo["licencas"][0]["tipo"] == "uf"
    assert corpo["licencas"][0]["vigente"] is True

    # O admin recém-criado consegue entrar e já enxerga o território licenciado.
    token = auth_header(login(client, email_admin, "senha1234"))
    entes = client.get("/entes", params={"limit": 5}, headers=token)
    assert entes.status_code == 200, entes.text

    with admin_session() as s:
        s.execute(delete(AuditLog).where(AuditLog.org_id == uuid.UUID(corpo["org_id"])))
        s.execute(delete(Organizacao).where(Organizacao.id == uuid.UUID(corpo["org_id"])))
        s.execute(delete(Usuario).where(Usuario.id == uuid.UUID(corpo["admin_usuario_id"])))


def test_provisionar_recusa_email_ja_existente(client, superuser, make_org) -> None:
    org = make_org(entes=[FORTALEZA])
    resposta = client.post(
        "/platform/orgs",
        json={
            "nome": "Duplicada",
            "tipo_conta": "consultoria",
            "admin_email": org.email,
            "admin_nome": "X",
            "admin_senha": "senha1234",
        },
        headers=_token_super(client, superuser),
    )
    assert resposta.status_code == 409


def test_licenca_recusa_alvo_incoerente_com_o_tipo(client, superuser, make_org) -> None:
    """Licença de ente sem IBGE não libera nada — e ninguém descobriria até o 403."""
    org = make_org(entes=[FORTALEZA])
    headers = _token_super(client, superuser)
    for payload in (
        {"tipo": "ente"},
        {"tipo": "uf"},
        {"tipo": "ente", "uf": UF_CE},
        {"tipo": "global", "cod_ibge": FORTALEZA},
    ):
        resposta = client.post(
            f"/platform/orgs/{org.org_id}/licencas", json=payload, headers=headers
        )
        assert resposta.status_code == 422, f"{payload}: {resposta.text}"


# --- O efeito no escopo -----------------------------------------------------


def test_licenca_de_uf_libera_o_territorio_sem_cadastrar_ente_a_ente(
    client, superuser, make_org
) -> None:
    """Conta estadual com licença de UF alcança município que não está na carteira."""
    org = make_org(tipo_conta="estado", entes=[UF_CE], licenciar=False)
    client.post(
        f"/platform/orgs/{org.org_id}/licencas",
        json={"tipo": "uf", "uf": UF_CE},
        headers=_token_super(client, superuser),
    )
    headers = auth_header(login(client, org.email, org.senha))

    assert client.get(


        f"/entes/{MARACANAU}/dashboard", params={"periodo": PERIODO}, headers=headers


    ).status_code != 403
    # E não vaza para fora do território.
    fora = client.get(
     f"/entes/{CAPITAL_FORA}/dashboard", params={"periodo": PERIODO}, headers=headers
 )
    assert fora.status_code == 403


def test_ente_na_carteira_sem_licenca_da_403_com_causa_propria(client, make_org) -> None:
    """403 de licença ≠ 403 de escopo: uma é comercial, a outra é cadastro do cliente."""
    org = make_org(entes=[FORTALEZA], licenciar=False)
    headers = auth_header(login(client, org.email, org.senha))
    resposta = client.get(
     f"/entes/{FORTALEZA}/dashboard", params={"periodo": PERIODO}, headers=headers
 )
    assert resposta.status_code == 403
    assert resposta.json()["type"].endswith("ente-nao-licenciado")


def test_suspender_licenca_bloqueia_na_hora_e_preserva_os_dados(
    client, superuser, make_org
) -> None:
    org = make_org(entes=[FORTALEZA])
    headers = auth_header(login(client, org.email, org.senha))
    assert client.get(

        f"/entes/{FORTALEZA}/dashboard", params={"periodo": PERIODO}, headers=headers

    ).status_code != 403

    super_headers = _token_super(client, superuser)
    licencas = client.get(f"/platform/orgs/{org.org_id}/licencas", headers=super_headers).json()
    licenca_id = licencas[0]["id"]

    patch = client.patch(
        f"/platform/licencas/{licenca_id}", json={"status": "suspensa"}, headers=super_headers
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["vigente"] is False

    # Efeito imediato, sem novo login e sem tocar na carteira do cliente.
    assert client.get(

        f"/entes/{FORTALEZA}/dashboard", params={"periodo": PERIODO}, headers=headers

    ).status_code == 403
    with admin_session() as s:
        assert (
            repository.get_carteira_ente(s, org_id=org.org_id, cod_ibge=FORTALEZA) is not None
        ), "suspender licença não pode apagar a carteira"
        assert s.get(Licenca, uuid.UUID(licenca_id)) is not None, "histórico preservado"


def test_reativar_devolve_o_acesso(client, superuser, make_org) -> None:
    org = make_org(entes=[FORTALEZA])
    headers = auth_header(login(client, org.email, org.senha))
    super_headers = _token_super(client, superuser)
    licenca_id = client.get(
        f"/platform/orgs/{org.org_id}/licencas", headers=super_headers
    ).json()[0]["id"]

    client.patch(
        f"/platform/licencas/{licenca_id}", json={"status": "suspensa"}, headers=super_headers
    )
    assert client.get(

        f"/entes/{FORTALEZA}/dashboard", params={"periodo": PERIODO}, headers=headers

    ).status_code == 403
    client.patch(
        f"/platform/licencas/{licenca_id}", json={"status": "ativa"}, headers=super_headers
    )
    assert client.get(

        f"/entes/{FORTALEZA}/dashboard", params={"periodo": PERIODO}, headers=headers

    ).status_code != 403


def test_licenca_expirada_por_data_nao_vale_sem_precisar_de_job(client, make_org) -> None:
    """Vigência é conferida por data: o acesso não depende de um job ter passado por lá."""
    org = make_org(entes=[FORTALEZA])
    ontem = date.today() - timedelta(days=1)
    with admin_session() as s:
        lic = s.scalar(select(Licenca).where(Licenca.org_id == org.org_id))
        assert lic is not None
        lic.vigencia_inicio = ontem - timedelta(days=30)
        lic.vigencia_fim = ontem
        s.flush()

    headers = auth_header(login(client, org.email, org.senha))
    resposta = client.get(
     f"/entes/{FORTALEZA}/dashboard", params={"periodo": PERIODO}, headers=headers
 )
    assert resposta.status_code == 403
    assert resposta.json()["type"].endswith("ente-nao-licenciado")


def test_licenca_nao_amplia_o_escopo_do_membership(client, superuser, make_org) -> None:
    """Licença é teto, não piso: continua valendo a restrição do usuário (§6.4)."""
    org = make_org(entes=[FORTALEZA, MARACANAU], escopo=[FORTALEZA])
    headers = auth_header(login(client, org.email, org.senha))
    assert client.get(

        f"/entes/{FORTALEZA}/dashboard", params={"periodo": PERIODO}, headers=headers

    ).status_code != 403
    assert client.get(

        f"/entes/{MARACANAU}/dashboard", params={"periodo": PERIODO}, headers=headers

    ).status_code == 403


def test_visao_agregada_conta_so_o_que_esta_licenciado(client, superuser, make_org) -> None:
    """Senão a carteira mostraria total que o drill devolve como 403."""
    org = make_org(entes=[FORTALEZA, MARACANAU])
    super_headers = _token_super(client, superuser)
    licencas = client.get(
        f"/platform/orgs/{org.org_id}/licencas", headers=super_headers
    ).json()
    alvo = next(lic for lic in licencas if lic["cod_ibge"] == MARACANAU)
    client.patch(
        f"/platform/licencas/{alvo['id']}", json={"status": "suspensa"}, headers=super_headers
    )

    headers = auth_header(login(client, org.email, org.senha))
    resposta = client.get(
        "/carteira/entes", params={"periodo": PERIODO}, headers=headers
    )
    assert resposta.status_code == 200, resposta.text
    codigos = {item["cod_ibge"] for item in resposta.json().get("data", [])}
    assert MARACANAU not in codigos


# --- Auditoria e isolamento -------------------------------------------------


def test_toda_acao_de_superuser_fica_no_audit_log_com_marcador(
    client, superuser, make_org
) -> None:
    org = make_org(entes=[FORTALEZA])
    client.post(
        f"/platform/orgs/{org.org_id}/licencas",
        json={"tipo": "ente", "cod_ibge": MARACANAU},
        headers=_token_super(client, superuser),
    )
    with admin_session() as s:
        acoes = list(
            s.scalars(
                select(AuditLog.acao).where(
                    AuditLog.org_id == org.org_id, AuditLog.usuario_id == superuser["id"]
                )
            )
        )
    assert any(a.startswith("plataforma:") for a in acoes), acoes


def test_rls_do_tenant_continua_intacta_fora_do_platform(client, make_org) -> None:
    """O bypass mora na dependência do control plane e não vaza para rota de tenant."""
    alvo = make_org(entes=[FORTALEZA])
    intruso = make_org(entes=[MARACANAU])
    headers = auth_header(login(client, intruso.email, intruso.senha))

    resposta = client.get(
        "/carteira/entes", params={"periodo": PERIODO}, headers=headers
    )
    assert resposta.status_code == 200
    codigos = {item["cod_ibge"] for item in resposta.json().get("data", [])}
    assert FORTALEZA not in codigos
    assert client.get(

        f"/entes/{FORTALEZA}/dashboard", params={"periodo": PERIODO}, headers=headers

    ).status_code == 403
    assert alvo.org_id != intruso.org_id
