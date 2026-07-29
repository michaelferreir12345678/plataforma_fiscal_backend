"""Sprint 28 — defesas de borda e isolamento entre organizações.

Duas famílias de teste com propósitos distintos:

* **Borda** — cabeçalhos e freio no autenticador. São garantias que valem para quem
  ainda não provou ser ninguém.
* **Vazamento (pen-test de RLS)** — a pergunta que decide um SaaS multi-tenant: *o
  cliente A consegue, de alguma forma, ver o dado do cliente B?* Testar só pela API
  responde metade; a outra metade é atacar o banco com o contexto de A e tentar ler as
  linhas de B — porque é isso que sobra se uma rota nova esquecer o filtro.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.core.db import SessionLocal, admin_session, apply_context
from app.modules.alerts.models import Alerta
from app.modules.reports.models import Relatorio
from app.modules.tenancy.models import CarteiraEnte, Fatura
from app.shared.seguranca import JanelaDeslizante
from tests.conftest import auth_header, login


def _alerta(org_id, cod_ibge: str, chave: str, titulo: str) -> Alerta:
    """Alerta mínimo válido — o modelo exige motivo legal e ação, e com razão: alerta
    sem base normativa e sem próximo passo é ruído."""
    return Alerta(
        org_id=org_id, cod_ibge=cod_ibge, chave=chave, categoria="limite",
        severidade="critico", prioridade=1, titulo=titulo,
        motivo_legal="LRF art. 20", acao_sugerida="Conferir a apuração.",
        status="nova",
    )


FORTALEZA = "2304400"
MARACANAU = "2307650"
PERIODO = "2024-B6"


# --------------------------------------------------------------------------- #
# Cabeçalhos
# --------------------------------------------------------------------------- #


def test_toda_resposta_carrega_os_cabecalhos_de_seguranca(client) -> None:
    resposta = client.get("/health")
    assert resposta.status_code == 200
    assert resposta.headers["X-Content-Type-Options"] == "nosniff"
    assert resposta.headers["X-Frame-Options"] == "DENY"
    assert resposta.headers["Referrer-Policy"] == "no-referrer"
    assert "default-src 'none'" in resposta.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in resposta.headers["Content-Security-Policy"]


def test_cabecalhos_acompanham_tambem_a_resposta_de_erro(client) -> None:
    """Um 401 sem cabeçalho é uma resposta desprotegida — e erro é o que mais vaza."""
    resposta = client.get("/entes", params={"limit": 1})
    assert resposta.status_code == 401
    assert resposta.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" in resposta.headers


def test_hsts_nao_e_enviado_fora_de_https(client) -> None:
    """Anunciar HSTS em HTTP puro é ruído: o navegador ignora e nós fingimos proteção."""
    resposta = client.get("/health")
    assert "Strict-Transport-Security" not in resposta.headers


def test_hsts_e_enviado_sob_https() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, base_url="https://testserver") as https:
        resposta = https.get("/health")
    assert resposta.headers["Strict-Transport-Security"].startswith("max-age=31536000")
    assert "includeSubDomains" in resposta.headers["Strict-Transport-Security"]


# --------------------------------------------------------------------------- #
# Freio no autenticador
# --------------------------------------------------------------------------- #


@pytest.fixture
def freio(client):
    """Zera a janela antes e depois: o contador é de processo e atravessaria testes."""
    from app.main import app

    alvo = None
    for mw in getattr(app, "user_middleware", []):
        if getattr(mw.cls, "__name__", "") == "AuthRateLimitMiddleware":
            alvo = mw
    assert alvo is not None, "AuthRateLimitMiddleware não está registrado"
    # A instância viva fica na cadeia construída; alcançá-la pelo app é o único jeito
    # de limpar o estado sem reconstruir a aplicação inteira.
    janelas = [
        obj
        for obj in _percorrer_middlewares(app)
        if isinstance(getattr(obj, "janela", None), JanelaDeslizante)
    ]
    for m in janelas:
        m.janela.limpar()
    yield
    for m in janelas:
        m.janela.limpar()


def _percorrer_middlewares(app) -> list[object]:
    saida: list[object] = []
    atual = getattr(app, "middleware_stack", None)
    visitados = 0
    while atual is not None and visitados < 40:
        saida.append(atual)
        atual = getattr(atual, "app", None)
        visitados += 1
    return saida


def test_senha_errada_em_serie_leva_a_429_com_retry_after(client, make_org, freio) -> None:
    org = make_org(entes=[FORTALEZA])
    ultimo = None
    for _ in range(14):
        ultimo = client.post(
            "/auth/login", data={"username": org.email, "password": "senha-errada"}
        )
        if ultimo.status_code == 429:
            break
    assert ultimo is not None
    assert ultimo.status_code == 429, "o freio não entrou"
    assert int(ultimo.headers["Retry-After"]) > 0
    assert ultimo.json()["type"].endswith("rate-limit")


def test_o_freio_e_por_identidade_e_nao_derruba_o_vizinho(client, make_org, freio) -> None:
    """Atrás de um NAT institucional, o IP é o mesmo para a prefeitura inteira."""
    vitima = make_org(entes=[FORTALEZA])
    vizinho = make_org(entes=[MARACANAU])
    for _ in range(14):
        client.post("/auth/login", data={"username": vitima.email, "password": "errada"})

    bloqueada = client.post(
        "/auth/login", data={"username": vitima.email, "password": "errada"}
    )
    assert bloqueada.status_code == 429

    # O vizinho, mesma origem, continua entrando.
    ok = client.post(
        "/auth/login", data={"username": vizinho.email, "password": vizinho.senha}
    )
    assert ok.status_code == 200, ok.text


def test_acertar_a_senha_devolve_a_cota(client, make_org, freio) -> None:
    """Errar duas vezes e acertar na terceira não pode aproximar ninguém do bloqueio."""
    org = make_org(entes=[FORTALEZA])
    for _ in range(5):
        client.post("/auth/login", data={"username": org.email, "password": "errada"})
    assert (
        client.post(
            "/auth/login", data={"username": org.email, "password": org.senha}
        ).status_code
        == 200
    )
    for _ in range(5):
        client.post("/auth/login", data={"username": org.email, "password": "errada"})
    # Sem o reset, estas 10 falhas somadas já teriam estourado o limite.
    assert (
        client.post(
            "/auth/login", data={"username": org.email, "password": org.senha}
        ).status_code
        == 200
    )


def test_janela_deslizante_expira_o_evento_antigo() -> None:
    janela = JanelaDeslizante(limite=2, janela_segundos=0.05)
    janela.registrar("k")
    janela.registrar("k")
    assert janela.bloqueado("k")
    import time

    time.sleep(0.08)
    assert not janela.bloqueado("k"), "eventos fora da janela não podem contar"


# --------------------------------------------------------------------------- #
# Vazamento entre organizações — pela API
# --------------------------------------------------------------------------- #


def test_organizacao_nao_ve_ente_de_outra_em_nenhuma_rota_de_escopo(
    client, make_org
) -> None:
    make_org(entes=[FORTALEZA])
    intruso = make_org(entes=[MARACANAU])
    h = auth_header(login(client, intruso.email, intruso.senha))

    rotas = [
        f"/entes/{FORTALEZA}/dashboard",
        f"/entes/{FORTALEZA}/limites",
        f"/entes/{FORTALEZA}/receita",
        f"/entes/{FORTALEZA}/despesa",
        f"/entes/{FORTALEZA}/resultado",
        f"/entes/{FORTALEZA}/patrimonio",
    ]
    for rota in rotas:
        resposta = client.get(rota, params={"periodo": PERIODO}, headers=h)
        assert resposta.status_code == 403, f"{rota} devolveu {resposta.status_code}"


def test_identificador_de_outra_organizacao_nao_e_acessivel_por_id(
    client, make_org
) -> None:
    """Adivinhar o identificador de um relatório alheio não pode dar acesso a ele.

    O artefato é criado **pela API**, como o cliente cria: montá-lo à mão no banco
    testaria um objeto que talvez o produto nunca produza.
    """
    dono = make_org(entes=[FORTALEZA])
    intruso = make_org(entes=[MARACANAU])
    h_dono = auth_header(login(client, dono.email, dono.senha))

    criado = client.post(
        "/relatorios",
        headers=h_dono,
        json={"modelo": "limites", "escopo": "ente", "ente": FORTALEZA, "periodo": PERIODO},
    )
    if criado.status_code not in {200, 201, 202}:
        pytest.skip(f"geração de relatório indisponível neste ambiente: {criado.status_code}")
    corpo = criado.json()
    relatorios = corpo.get("relatorios") or [corpo]
    alvo = relatorios[0]
    relatorio_id = alvo.get("id")
    assert relatorio_id, corpo

    # O dono enxerga o que criou.
    assert client.get(f"/relatorios/{relatorio_id}", headers=h_dono).status_code == 200

    # O vizinho, com o mesmo identificador em mãos, não.
    h_intruso = auth_header(login(client, intruso.email, intruso.senha))
    resposta = client.get(f"/relatorios/{relatorio_id}", headers=h_intruso)
    assert resposta.status_code in {403, 404}, resposta.text
    assert FORTALEZA not in resposta.text, "o corpo do erro vazou o ente do outro tenant"


# --------------------------------------------------------------------------- #
# Vazamento entre organizações — atacando o banco direto
# --------------------------------------------------------------------------- #


def _contar_com_contexto(org_id: uuid.UUID, modelo) -> int:
    """Conta linhas visíveis com o contexto de RLS fixado em ``org_id``."""
    with SessionLocal() as session:
        apply_context(session, org_id=org_id, user_id=None, is_admin=False)
        return int(session.scalar(select(func.count()).select_from(modelo)) or 0)


def test_rls_isola_as_tabelas_do_op_mesmo_sem_filtro_na_consulta(client, make_org) -> None:
    """A consulta sem ``where org_id`` é o teste que importa: é o esquecimento típico.

    Se a RLS estiver correta, a linha da outra organização nem chega ao Python — e uma
    rota nova que esqueça o filtro continua isolada por construção.
    """
    a = make_org(entes=[FORTALEZA])
    b = make_org(entes=[MARACANAU])
    with admin_session() as s:
        s.add(_alerta(a.org_id, FORTALEZA, "teste:a", "alerta de A"))
        s.add(_alerta(b.org_id, MARACANAU, "teste:b", "alerta de B"))

    for modelo in (Alerta, CarteiraEnte, Fatura, Relatorio):
        vistos_por_a = _contar_com_contexto(a.org_id, modelo)
        vistos_por_b = _contar_com_contexto(b.org_id, modelo)
        total = _contar_com_contexto(a.org_id, modelo) + _contar_com_contexto(b.org_id, modelo)
        with admin_session() as s:
            real = int(s.scalar(select(func.count()).select_from(modelo)) or 0)
        assert vistos_por_a <= real and vistos_por_b <= real
        assert total <= real, (
            f"{modelo.__tablename__}: A e B juntos enxergam mais do que existe — "
            "alguma linha está visível para os dois."
        )

    with SessionLocal() as session:
        apply_context(session, org_id=a.org_id, user_id=None, is_admin=False)
        titulos = list(session.scalars(select(Alerta.titulo)))
    assert "alerta de A" in titulos
    assert "alerta de B" not in titulos, "vazamento: A leu o alerta de B"


def test_sessao_sem_contexto_nao_le_nada_do_op(client, make_org) -> None:
    """*Default-deny*: sem organização fixada, a resposta correta é o vazio."""
    a = make_org(entes=[FORTALEZA])
    with admin_session() as s:
        s.add(_alerta(a.org_id, FORTALEZA, "teste:sem-ctx", "não deve aparecer"))
    with SessionLocal() as session:
        apply_context(session)  # sem org, sem usuário, sem admin
        assert int(session.scalar(select(func.count()).select_from(Alerta)) or 0) == 0


def test_a_role_da_aplicacao_nao_pode_desligar_a_rls(client, make_org) -> None:
    """Se a role do runtime tivesse ``BYPASSRLS`` ou fosse dona da tabela, o isolamento
    seria decorativo: dono de tabela ignora *policy* por padrão."""
    with SessionLocal() as session:
        papel = session.execute(text("select current_user")).scalar()
        bypass = session.execute(
            text("select rolbypassrls from pg_roles where rolname = current_user")
        ).scalar()
        superusuario = session.execute(
            text("select rolsuper from pg_roles where rolname = current_user")
        ).scalar()
    assert bypass is False, f"a role {papel} pode ignorar a RLS"
    assert superusuario is False, f"a role {papel} é superusuária"


# --------------------------------------------------------------------------- #
# Coerência entre o fato e o mart (defeito achado na validação em lote)
# --------------------------------------------------------------------------- #


def test_mart_e_fato_de_pessoal_nao_podem_divergir(client, make_org) -> None:
    """O semáforo e a página de detalhe têm de dizer o mesmo percentual.

    Este é o defeito que a Sprint 28 encontrou em três camadas seguidas: o mart passou
    a dividir pela RCL ajustada, o ``fato_pessoal`` continuou na RCL cheia, e a guarda
    "materializa uma vez" impediu que a rematerialização corrigisse — cada camada
    isoladamente parecia certa, e o produto mostrava dois números para o mesmo limite.
    """
    from sqlalchemy import select as _select

    from app.modules.indicators.models import MartIndicador
    from app.modules.personnel.models import FatoPessoal

    with admin_session() as session:
        pares = session.execute(
            _select(
                FatoPessoal.cod_ibge,
                FatoPessoal.periodo,
                FatoPessoal.pct_rcl,
                FatoPessoal.rcl_ajustada,
            ).where(
                FatoPessoal.poder_codigo == "ENTE.EXEC",
                FatoPessoal.pct_rcl.is_not(None),
            ).limit(40)
        ).all()

    if not pares:
        pytest.skip("sem fato_pessoal materializado neste ambiente")

    divergentes: list[str] = []
    for cod_ibge, periodo_rgf, pct_fato, ajustada in pares:
        # O mart ancora no bimestre RREO correspondente (Q1→B2, Q2→B4, Q3→B6).
        marca = periodo_rgf.split("-")[-1]
        if not marca.startswith("Q"):
            continue
        periodo_mart = f"{periodo_rgf[:4]}-B{2 * int(marca[1:])}"
        with admin_session() as session:
            linha = session.execute(
                _select(MartIndicador.valor_pct_rcl, MartIndicador.denominador).where(
                    MartIndicador.cod_ibge == cod_ibge,
                    MartIndicador.periodo == periodo_mart,
                    MartIndicador.indicador == "pessoal_executivo",
                ).limit(1)
            ).first()
        if linha is None or linha[0] is None:
            continue
        if abs(Decimal(linha[0]) - Decimal(pct_fato)) > Decimal("0.01"):
            divergentes.append(
                f"{cod_ibge}/{periodo_rgf}: fato={pct_fato:.4f} "
                f"(rcl_ajustada={'sim' if ajustada else 'não'}) × "
                f"mart={Decimal(linha[0]):.4f} (denominador={linha[1]})"
            )

    assert not divergentes, (
        "o percentual de pessoal difere entre fato e mart:\n  " + "\n  ".join(divergentes[:8])
    )
