"""Sprint H1 — Governança: billing, control plane e licença visível.

Cobre os critérios de aceite da ficha (docs/evolucao_plataforma.md):
1. fatura com organização/assinatura configurada tem valor_total > 0;
2. ação de RBAC (criar usuário, criar papel, alterar capacidades) aparece em
   op.audit_log com o nome do ator;
3. superusuário consulta suas próprias ações em /platform/auditoria (isolamento por
   sessão coberto em test_sprint28_seguranca.py);
4. badge mostra "expirada" quando vencida (frontend — sprint19.test.tsx);
5. tenant vê a própria licença sem precisar de um 403 para descobrir (GET /me/licencas).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.db import admin_session
from app.core.security import hash_password
from app.modules.catalog.models import DimEnte
from app.modules.tenancy import repository as tenancy_repo
from app.modules.tenancy.models import AuditLog, Licenca, Organizacao, Usuario
from tests.conftest import auth_header, login

FORTALEZA = "2304400"
MARACANAU = "2307650"


@pytest.fixture
def superuser():
    """Operador da plataforma — mesma fábrica de test_sprint19_control_plane.py."""
    email = f"plataforma-h1g-{uuid.uuid4().hex}@prumo.gov.br"
    senha = "senha1234"
    with admin_session() as s:
        usuario = tenancy_repo.create_usuario(
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


# --------------------------------------------------------------------------- #
# 1) Billing zerado — POST/PATCH /platform/orgs/{id}/assinatura
# --------------------------------------------------------------------------- #


def test_definir_assinatura_via_control_plane_destrava_fatura_com_preco_real(
    client, superuser, make_org
) -> None:
    """Antes desta sprint, o único endpoint que gravava op.assinatura (POST
    /billing/assinatura) tinha 403 hardcoded, então emitir_fatura sempre calculava
    preco=Decimal('0'). O endpoint do control plane é o que faltava."""
    org = make_org(entes=[FORTALEZA])
    h_super = _token_super(client, superuser)
    h_tenant = auth_header(login(client, org.email, org.senha))

    resposta = client.post(
        f"/platform/orgs/{org.org_id}/assinatura",
        json={"metrica_cobranca": "por_ente", "preco_unitario": "120.00"},
        headers=h_super,
    )
    assert resposta.status_code == 201, resposta.text
    assinatura = resposta.json()
    assert Decimal(assinatura["preco_unitario"]) == Decimal("120.00")
    assert assinatura["metrica_cobranca"] == "por_ente"

    fatura = client.post(
        "/billing/faturas", headers=h_tenant, json={"competencia": "2026-09"}
    )
    assert fatura.status_code == 201, fatura.text
    corpo = fatura.json()
    assert Decimal(corpo["valor_total"]) > Decimal("0")
    assert Decimal(corpo["valor_total"]) == Decimal("120.00")  # 1 ente x R$120


def test_alterar_assinatura_reajusta_so_o_preco_sem_reenviar_tudo(
    client, superuser, make_org
) -> None:
    """PATCH altera só o que veio no corpo — a métrica/plano/ciclo continuam os mesmos."""
    org = make_org(entes=[FORTALEZA])
    h_super = _token_super(client, superuser)

    client.post(
        f"/platform/orgs/{org.org_id}/assinatura",
        json={"plano": "corporativo", "metrica_cobranca": "fixo", "preco_unitario": "500.00"},
        headers=h_super,
    )
    ajustada = client.patch(
        f"/platform/orgs/{org.org_id}/assinatura",
        json={"preco_unitario": "650.00"},
        headers=h_super,
    )
    assert ajustada.status_code == 200, ajustada.text
    corpo = ajustada.json()
    assert Decimal(corpo["preco_unitario"]) == Decimal("650.00")
    assert corpo["metrica_cobranca"] == "fixo"  # preservado
    assert corpo["plano"] == "corporativo"  # preservado


def test_assinatura_do_control_plane_exige_superusuario(client, make_org) -> None:
    org = make_org(entes=[FORTALEZA], capacidades=["ver", "administrar"])
    h_tenant = auth_header(login(client, org.email, org.senha))
    resposta = client.post(
        f"/platform/orgs/{org.org_id}/assinatura",
        json={"metrica_cobranca": "por_ente", "preco_unitario": "10"},
        headers=h_tenant,
    )
    assert resposta.status_code == 403, resposta.text
    assert resposta.json()["type"].endswith("superuser-required")


def test_patch_assinatura_sem_campo_algum_e_422(client, superuser, make_org) -> None:
    org = make_org(entes=[FORTALEZA])
    resposta = client.patch(
        f"/platform/orgs/{org.org_id}/assinatura", json={}, headers=_token_super(client, superuser)
    )
    assert resposta.status_code == 422


# --------------------------------------------------------------------------- #
# Provisionamento coleta métrica + preço (tarefa 7)
# --------------------------------------------------------------------------- #


def test_provisionar_com_metrica_e_preco_cria_a_assinatura_junto(client, superuser) -> None:
    """Hoje toda org nascia com billing indefinido — o formulário agora coleta os dois,
    e o provisionamento cria a assinatura na mesma transação."""
    email_admin = f"admin-h1-{uuid.uuid4().hex}@sefaz.gov.br"
    payload = {
        "nome": "Prefeitura de Teste H1",
        "tipo_conta": "prefeitura",
        "metrica_cobranca": "fixo",
        "preco_unitario": "299.90",
        "admin_email": email_admin,
        "admin_nome": "Admin H1",
        "admin_senha": "senha1234",
    }
    resposta = client.post(
        "/platform/orgs", json=payload, headers=_token_super(client, superuser)
    )
    assert resposta.status_code == 201, resposta.text
    corpo = resposta.json()
    assert corpo["assinatura"] is not None
    assert corpo["assinatura"]["metrica_cobranca"] == "fixo"
    assert Decimal(corpo["assinatura"]["preco_unitario"]) == Decimal("299.90")

    with admin_session() as s:
        assinatura = tenancy_repo.get_assinatura(s, org_id=uuid.UUID(corpo["org_id"]))
        assert assinatura is not None
        assert Decimal(assinatura.preco_unitario) == Decimal("299.90")
        s.execute(delete(AuditLog).where(AuditLog.org_id == uuid.UUID(corpo["org_id"])))
        s.execute(delete(Organizacao).where(Organizacao.id == uuid.UUID(corpo["org_id"])))
        s.execute(delete(Usuario).where(Usuario.id == uuid.UUID(corpo["admin_usuario_id"])))


def test_provisionar_sem_metrica_nao_cria_assinatura(client, superuser) -> None:
    """Sem métrica no formulário, a org continua nascendo sem billing — o campo é
    opcional, mas a ausência tem de ficar visível (assinatura: null), não escondida."""
    email_admin = f"admin-h1b-{uuid.uuid4().hex}@sefaz.gov.br"
    payload = {
        "nome": "Prefeitura sem billing",
        "tipo_conta": "prefeitura",
        "admin_email": email_admin,
        "admin_nome": "Admin",
        "admin_senha": "senha1234",
    }
    resposta = client.post(
        "/platform/orgs", json=payload, headers=_token_super(client, superuser)
    )
    assert resposta.status_code == 201, resposta.text
    corpo = resposta.json()
    assert corpo["assinatura"] is None

    with admin_session() as s:
        s.execute(delete(AuditLog).where(AuditLog.org_id == uuid.UUID(corpo["org_id"])))
        s.execute(delete(Organizacao).where(Organizacao.id == uuid.UUID(corpo["org_id"])))
        s.execute(delete(Usuario).where(Usuario.id == uuid.UUID(corpo["admin_usuario_id"])))


# --------------------------------------------------------------------------- #
# sem_populacao exposto na memória da fatura (tarefa 7)
# --------------------------------------------------------------------------- #


def test_sem_populacao_aparece_na_memoria_da_fatura_por_populacao(
    client, superuser, make_org
) -> None:
    """Ente sem população cadastrada no IBGE conta como zero, silenciosamente — o
    flag já era calculado; faltava aparecer para quem confere a fatura."""
    com_pop = "9100001"
    sem_pop = "9100002"
    with admin_session() as s:
        s.add(DimEnte(cod_ibge=com_pop, nome="Com população", esfera="municipal", uf="CE",
                       populacao=50_000, pop_ano_ref=2022,
                       pop_source_ref={"relatorio": "IBGE-POP", "periodo": "2022"}))
        s.add(DimEnte(cod_ibge=sem_pop, nome="Sem população", esfera="municipal", uf="CE",
                       populacao=None))
    try:
        org = make_org(entes=[com_pop, sem_pop])
        h_tenant = auth_header(login(client, org.email, org.senha))
        client.post(
            f"/platform/orgs/{org.org_id}/assinatura",
            json={"metrica_cobranca": "por_populacao", "preco_unitario": "0.05"},
            headers=_token_super(client, superuser),
        )
        overview = client.get("/billing", headers=h_tenant).json()
        detalhe = overview["preview"]["memoria"]["entes"]
        marcado = {d["cod_ibge"]: d["sem_populacao"] for d in detalhe}
        assert marcado[com_pop] is False
        assert marcado[sem_pop] is True
    finally:
        with admin_session() as s:
            s.execute(delete(DimEnte).where(DimEnte.cod_ibge.in_([com_pop, sem_pop])))


# --------------------------------------------------------------------------- #
# 2) Auditoria de RBAC ausente — create_user / create_papel / update_papel_capacidades
# --------------------------------------------------------------------------- #


def test_criar_usuario_fica_na_auditoria_com_o_ator(client, make_org) -> None:
    org = make_org(entes=[], capacidades=["ver", "administrar"])
    h = auth_header(login(client, org.email, org.senha))
    novo_email = f"novo-{uuid.uuid4().hex}@teste.gov.br"

    resposta = client.post(
        "/users",
        headers=h,
        json={"email": novo_email, "nome": "Fulano", "senha": "senha1234"},
    )
    assert resposta.status_code == 201, resposta.text

    with admin_session() as s:
        linha = s.execute(
            select(AuditLog.acao, AuditLog.recurso, AuditLog.usuario_id).where(
                AuditLog.org_id == org.org_id, AuditLog.acao == "CRIAR_USUARIO"
            )
        ).first()
    assert linha is not None
    acao, recurso, usuario_id = linha
    assert usuario_id == org.usuario_id  # o ator é quem chamou a rota
    assert novo_email in recurso


def test_criar_papel_fica_na_auditoria_com_o_ator_e_as_capacidades(client, make_org) -> None:
    org = make_org(entes=[], capacidades=["ver", "administrar"])
    h = auth_header(login(client, org.email, org.senha))

    resposta = client.post(
        "/papeis", headers=h, json={"nome": "Fiscal", "capacidades": ["ver", "exportar"]}
    )
    assert resposta.status_code == 201, resposta.text
    papel_id = resposta.json()["id"]

    with admin_session() as s:
        linha = s.execute(
            select(AuditLog.recurso, AuditLog.usuario_id).where(
                AuditLog.org_id == org.org_id, AuditLog.acao == "CRIAR_PAPEL"
            )
        ).first()
    assert linha is not None
    recurso, usuario_id = linha
    assert usuario_id == org.usuario_id
    assert papel_id in recurso
    assert "ver" in recurso and "exportar" in recurso


def test_alterar_papel_capacidades_fica_na_auditoria_com_antes_e_depois(
    client, make_org
) -> None:
    """A ficha pede explicitamente antes/depois das capacidades no payload da auditoria —
    é o que permite responder 'o que esse papel podia fazer antes desta mudança?'."""
    org = make_org(entes=[], capacidades=["ver", "administrar"])
    h = auth_header(login(client, org.email, org.senha))

    papel = client.post(
        "/papeis", headers=h, json={"nome": "Analista", "capacidades": ["ver"]}
    ).json()

    resposta = client.patch(
        f"/papeis/{papel['id']}", headers=h, json={"capacidades": ["ver", "exportar", "editar"]}
    )
    assert resposta.status_code == 200, resposta.text

    with admin_session() as s:
        linha = s.execute(
            select(AuditLog.recurso, AuditLog.usuario_id).where(
                AuditLog.org_id == org.org_id, AuditLog.acao == "ALTERAR_PAPEL_CAPACIDADES"
            )
        ).first()
    assert linha is not None
    recurso, usuario_id = linha
    assert usuario_id == org.usuario_id
    assert "antes=ver" in recurso
    assert "depois=" in recurso and "editar" in recurso.split("depois=")[1]


# --------------------------------------------------------------------------- #
# 3) Trilha de auditoria expõe o autor (join com op.usuario)
# --------------------------------------------------------------------------- #


def test_admin_auditoria_expoe_nome_e_email_do_autor(client, make_org) -> None:
    org = make_org(entes=[], capacidades=["ver", "administrar"])
    h = auth_header(login(client, org.email, org.senha))
    client.post(
        "/papeis", headers=h, json={"nome": "Qualquer", "capacidades": ["ver"]}
    )

    pagina = client.get(
        "/admin/auditoria", headers=h, params={"acao": "CRIAR_PAPEL"}
    ).json()
    assert pagina["total"] >= 1
    item = pagina["itens"][0]
    assert item["usuario_id"] == str(org.usuario_id)
    assert item["usuario_nome"] == "Usuário"  # nome fixo da fábrica make_org
    assert item["usuario_email"] == org.email


def test_admin_auditoria_filtra_por_autor_e_por_periodo(client, make_org) -> None:
    org = make_org(entes=[], capacidades=["ver", "administrar"])
    h = auth_header(login(client, org.email, org.senha))
    client.post("/papeis", headers=h, json={"nome": "X", "capacidades": ["ver"]})

    por_autor = client.get(
        "/admin/auditoria", headers=h, params={"usuario_id": str(org.usuario_id)}
    ).json()
    assert por_autor["total"] >= 1

    outro_uuid = str(uuid.uuid4())
    vazio = client.get(
        "/admin/auditoria", headers=h, params={"usuario_id": outro_uuid}
    ).json()
    assert vazio["total"] == 0

    futuro = (date.today() + timedelta(days=1)).isoformat()
    nada_no_futuro = client.get(
        "/admin/auditoria", headers=h, params={"de": f"{futuro}T00:00:00Z"}
    ).json()
    assert nada_no_futuro["total"] == 0


# --------------------------------------------------------------------------- #
# 5/6) Licença visível ao tenant — GET /me/licencas + vigência correta
# --------------------------------------------------------------------------- #


def test_me_licencas_mostra_a_propria_cobertura(client, make_org) -> None:
    org = make_org(entes=[FORTALEZA])
    h = auth_header(login(client, org.email, org.senha))
    resposta = client.get("/me/licencas", headers=h)
    assert resposta.status_code == 200, resposta.text
    licencas = resposta.json()
    assert len(licencas) == 1
    assert licencas[0]["cod_ibge"] == FORTALEZA
    assert licencas[0]["vigente"] is True
    assert licencas[0]["status"] == "ativa"


def test_me_licencas_marca_vigente_falso_quando_o_prazo_venceu(client, make_org) -> None:
    """O mesmo `vigente` que corrige o badge do control plane (tarefa 5) — sem prazo
    vencido escondido atrás de um status que continua 'ativa' no banco."""
    org = make_org(entes=[FORTALEZA])
    ontem = date.today() - timedelta(days=1)
    with admin_session() as s:
        lic = s.scalar(select(Licenca).where(Licenca.org_id == org.org_id))
        assert lic is not None
        lic.vigencia_inicio = ontem - timedelta(days=30)
        lic.vigencia_fim = ontem
        s.flush()

    h = auth_header(login(client, org.email, org.senha))
    resposta = client.get("/me/licencas", headers=h).json()
    assert resposta[0]["status"] == "ativa"  # o banco não transiciona sozinho
    assert resposta[0]["vigente"] is False  # mas o efetivo já é falso


def test_me_licencas_sem_autenticacao_e_401(client) -> None:
    assert client.get("/me/licencas").status_code == 401
