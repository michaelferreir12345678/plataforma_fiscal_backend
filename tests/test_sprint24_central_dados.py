"""Sprint 24 — Central de Dados: jobs assíncronos de ingestão.

Exercita o ciclo de vida do job com um **worker fake** (modo eager, sem rede/Redis):
fila→executando→concluído/falhou/cancelado/retry; a confirmação obrigatória acima do limiar;
o isolamento por RLS entre orgs; a auditoria da ação; o contrato de progresso; e a varredura
de retificações. O executor real (RQ/Redis) é o mesmo código — aqui só se troca a unidade de
trabalho por uma função determinística.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select, text

from app.core.db import admin_session
from app.modules.ingestion import jobs_repository
from app.modules.ingestion.jobs_models import IngestJob
from app.modules.ingestion.models import DimEntrega, IngestionLog
from app.modules.tenancy.models import Assinatura, AuditLog, CarteiraEnte
from app.workers import ingest_jobs

from .conftest import auth_header, login

FONTE = "siconfi_rreo"


@pytest.fixture
def eager():
    """Modo eager + sem recálculo de marts (o fake não escreve silver real)."""
    ingest_jobs.set_eager(True)
    ingest_jobs.set_recalcular(False)
    yield
    ingest_jobs.set_eager(False)
    ingest_jobs.set_recalcular(True)
    ingest_jobs.set_unit_runner(None)


def _fake_sempre_ok(session, job, ente, chave):  # noqa: ANN001
    return 7


def _criar(client, tok, **body):
    return client.post("/admin/ingestion/jobs", json=body, headers=auth_header(tok))


# --- Ciclo de vida: concluído ---
def test_job_conclui_com_progresso(client, make_org, eager):
    ingest_jobs.set_unit_runner(_fake_sempre_ok)
    org = make_org(entes=["2304400", "2307650"])
    tok = login(client, org.email, org.senha)

    r = _criar(client, tok, fonte=FONTE, tipo="backfill", entes=["2304400", "2307650"], anos=[2024])
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["precisa_confirmacao"] is False
    job = body["job"]
    # eager roda inline: o job já concluiu com progresso 100 e 2 unidades OK.
    assert job["status"] == "concluido"
    assert job["itens_total"] == 2
    assert job["itens_ok"] == 2
    assert job["itens_erro"] == 0
    assert job["progresso_pct"] == 100
    assert len(job["resultado"]["itens"]) == 2


def test_contrato_de_progresso_no_get(client, make_org, eager):
    ingest_jobs.set_unit_runner(_fake_sempre_ok)
    org = make_org(entes=["2304400"])
    tok = login(client, org.email, org.senha)
    jid = _criar(client, tok, fonte=FONTE, entes=["2304400"], anos=[2024]).json()["job"]["id"]

    got = client.get(f"/admin/ingestion/jobs/{jid}", headers=auth_header(tok)).json()
    assert got["itens_total"] == got["itens_ok"] + got["itens_erro"]
    assert got["progresso_pct"] == 100
    assert got["status"] == "concluido"


# --- Confirmação obrigatória acima do limiar ---
def test_confirmacao_obrigatoria_acima_do_limiar(client, make_org, eager):
    ingest_jobs.set_unit_runner(_fake_sempre_ok)
    org = make_org(entes=["9900001"])
    tok = login(client, org.email, org.senha)
    anos = list(range(1963, 2025))  # 62 anos × 6 bimestres = 372 itens > limiar.

    # sem confirmar ⇒ não cria job, pede confirmação (200)
    r1 = _criar(client, tok, fonte=FONTE, entes=["9900001"], anos=anos)
    assert r1.status_code == 200, r1.text
    assert r1.json()["precisa_confirmacao"] is True
    # O custo inclui os seis bimestres descobertos por ente/ano no RREO.
    assert r1.json()["estimativa_itens"] == 372
    assert r1.json()["job"] is None
    with admin_session() as session:
        audit = session.scalar(
            select(AuditLog)
            .where(
                AuditLog.org_id == org.org_id,
                AuditLog.acao == "ingestao.job.confirmacao_requerida",
            )
            .order_by(AuditLog.ts.desc())
        )
    assert audit is not None
    confirmacao = json.loads(audit.recurso)
    assert confirmacao["confirmar"] is False
    assert confirmacao["estimativa_itens"] == 372
    assert confirmacao["entes"] == ["9900001"]

    # com confirmar ⇒ cria e executa (202)
    r2 = _criar(client, tok, fonte=FONTE, entes=["9900001"], anos=anos, confirmar=True)
    assert r2.status_code == 202, r2.text
    assert r2.json()["job"]["status"] == "concluido"


# --- Retry reexecuta só os itens com erro ---
def test_retry_reexecuta_apenas_erros(client, make_org, eager):
    chamadas: list[tuple[str, str]] = []

    # B falha na 1ª tentativa e passa na 2ª (retry); A sempre OK.
    def fake(session, job, ente, chave):  # noqa: ANN001
        chamadas.append((ente, chave))
        if ente == "2307650" and job.tentativas < 2:
            raise RuntimeError("fonte recusou (simulado)")
        return 3

    ingest_jobs.set_unit_runner(fake)
    org = make_org(entes=["2304400", "2307650"])
    tok = login(client, org.email, org.senha)

    criado = _criar(client, tok, fonte=FONTE, entes=["2304400", "2307650"], anos=[2024]).json()[
        "job"
    ]
    assert criado["status"] == "falhou"  # 1 erro ⇒ falhou (retryável)
    assert criado["itens_erro"] == 1

    chamadas.clear()
    r = client.post(f"/admin/ingestion/jobs/{criado['id']}/retry", headers=auth_header(tok))
    assert r.status_code == 200, r.text
    depois = r.json()
    # o retry reprocessou APENAS a unidade que falhou (B), agora com sucesso ⇒ concluído.
    assert depois["status"] == "concluido"
    # O histórico final preserva A e substitui apenas B; só B foi reexecutado.
    assert depois["itens_total"] == 2
    assert depois["itens_ok"] == 2
    assert depois["tentativas"] == 2
    assert chamadas == [("2307650", "2024")]
    with admin_session() as session:
        audit = session.scalar(
            select(AuditLog)
            .where(
                AuditLog.org_id == org.org_id,
                AuditLog.acao == "ingestao.job.retry",
            )
            .order_by(AuditLog.ts.desc())
        )
    assert audit is not None
    assert json.loads(audit.recurso)["resultado"]["itens_reenfileirados"] == 1


def test_retry_de_falha_sistemica_audita_apenas_pendentes(
    client, make_org, eager
):
    entes = ["2304400", "2307650", "2310001"]
    org = make_org(entes=entes)
    tok = login(client, org.email, org.senha)
    with admin_session() as session:
        job = IngestJob(
            org_id=org.org_id,
            criado_por=org.usuario_id,
            fonte=FONTE,
            tipo="backfill",
            entes=entes,
            periodos=["2024"],
            parametros={"run_request": {"fonte": FONTE, "anos": [2024]}},
            status="falhou",
            itens_total=3,
            itens_ok=1,
            tentativas=1,
            resultado={
                "itens": [
                    {
                        "ente": entes[0],
                        "chave": "2024",
                        "ok": True,
                        "erro": None,
                        "silver_rows": 7,
                    }
                ],
                "erro_sistema": {
                    "fase": f"unidade:{entes[1]}:2024",
                    "erro": "timeout simulado",
                },
            },
        )
        session.add(job)
        session.commit()
        job_id = job.id

    chamadas: list[tuple[str, str]] = []

    def concluir(session, job, ente, chave):  # noqa: ANN001
        chamadas.append((ente, chave))
        return 2

    ingest_jobs.set_unit_runner(concluir)
    response = client.post(
        f"/admin/ingestion/jobs/{job_id}/retry",
        headers=auth_header(tok),
    )
    assert response.status_code == 200, response.text
    assert chamadas == [(entes[1], "2024"), (entes[2], "2024")]

    with admin_session() as session:
        audit = session.scalar(
            select(AuditLog)
            .where(
                AuditLog.org_id == org.org_id,
                AuditLog.acao == "ingestao.job.retry",
            )
            .order_by(AuditLog.ts.desc())
        )
    assert audit is not None
    assert json.loads(audit.recurso)["resultado"]["itens_reenfileirados"] == 2


def test_retry_so_de_falho(client, make_org, eager):
    ingest_jobs.set_unit_runner(_fake_sempre_ok)
    org = make_org(entes=["2304400"])
    tok = login(client, org.email, org.senha)
    jid = _criar(client, tok, fonte=FONTE, entes=["2304400"], anos=[2024]).json()["job"]["id"]
    # o job concluiu; retry deve ser recusado (409).
    r = client.post(f"/admin/ingestion/jobs/{jid}/retry", headers=auth_header(tok))
    assert r.status_code == 409, r.text


def test_retry_revalida_escopo_atual(client, make_org, eager):
    """Reduzir a carteira depois da criação impede reexecutar a unidade removida."""
    alvo = "2307650"

    def falha_no_alvo(session, job, ente, chave):  # noqa: ANN001
        if ente == alvo:
            raise RuntimeError("falha simulada")
        return 3

    ingest_jobs.set_unit_runner(falha_no_alvo)
    org = make_org(entes=["2304400", alvo])
    tok = login(client, org.email, org.senha)
    criado = _criar(
        client,
        tok,
        fonte=FONTE,
        entes=["2304400", alvo],
        anos=[2024],
    ).json()["job"]
    assert criado["status"] == "falhou"

    with admin_session() as session:
        session.execute(
            delete(CarteiraEnte).where(
                CarteiraEnte.org_id == org.org_id,
                CarteiraEnte.cod_ibge == alvo,
            )
        )

    response = client.post(
        f"/admin/ingestion/jobs/{criado['id']}/retry",
        headers=auth_header(tok),
    )
    assert response.status_code == 403, response.text
    assert response.json()["type"].endswith("scope-forbidden")


# --- Cancelar só na fila ---
def _inserir_job(org_id: uuid.UUID, status: str) -> uuid.UUID:
    with admin_session() as s:
        job = IngestJob(
            org_id=org_id,
            fonte=FONTE,
            tipo="backfill",
            entes=["2304400"],
            periodos=["2024"],
            parametros={"anos": [2024]},
            status=status,
            itens_total=1,
        )
        s.add(job)
        s.commit()
        return job.id


def test_cancelar_so_na_fila(client, make_org):
    org = make_org()
    tok = login(client, org.email, org.senha)

    na_fila = _inserir_job(org.org_id, "na_fila")
    r = client.post(f"/admin/ingestion/jobs/{na_fila}/cancelar", headers=auth_header(tok))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelado"
    with admin_session() as session:
        audit = session.scalar(
            select(AuditLog)
            .where(
                AuditLog.org_id == org.org_id,
                AuditLog.acao == "ingestao.job.cancelar",
            )
            .order_by(AuditLog.ts.desc())
        )
    assert audit is not None
    assert json.loads(audit.recurso)["resultado"]["status"] == "cancelado"

    concluido = _inserir_job(org.org_id, "concluido")
    r2 = client.post(f"/admin/ingestion/jobs/{concluido}/cancelar", headers=auth_header(tok))
    assert r2.status_code == 409  # não se cancela um job que já terminou


def test_cancelado_na_fila_nao_executa(client, make_org):
    """Se o job foi cancelado enquanto na fila, o worker não o executa (checa o status)."""
    org = make_org()
    jid = _inserir_job(org.org_id, "cancelado")
    # simula o worker pegando o job cancelado
    ingest_jobs.set_recalcular(False)
    ingest_jobs.set_unit_runner(_fake_sempre_ok)
    try:
        ingest_jobs.run_job_async(str(jid))
    finally:
        ingest_jobs.set_unit_runner(None)
        ingest_jobs.set_recalcular(True)
    with admin_session() as s:
        job = s.get(IngestJob, jid)
        assert job.status == "cancelado"
        assert job.itens_ok == 0  # nada foi processado


# --- RLS: uma org não vê os jobs da outra ---
def test_rls_jobs_entre_orgs(client, make_org, eager):
    ingest_jobs.set_unit_runner(_fake_sempre_ok)
    org_a = make_org(entes=["2304400"])
    org_b = make_org(entes=["2307650"])
    tok_a = login(client, org_a.email, org_a.senha)
    tok_b = login(client, org_b.email, org_b.senha)

    jid_a = _criar(client, tok_a, fonte=FONTE, entes=["2304400"], anos=[2024]).json()["job"]["id"]

    lista_b = client.get("/admin/ingestion/jobs", headers=auth_header(tok_b)).json()
    assert all(j["id"] != jid_a for j in lista_b)
    # e B não consegue abrir o job de A (RLS ⇒ 404)
    assert (
        client.get(f"/admin/ingestion/jobs/{jid_a}", headers=auth_header(tok_b)).status_code == 404
    )


# --- Auditoria ---
def test_auditoria_registra_criacao(client, make_org, eager):
    ingest_jobs.set_unit_runner(_fake_sempre_ok)
    org = make_org(entes=["2304400"])
    tok = login(client, org.email, org.senha)
    _criar(client, tok, fonte=FONTE, entes=["2304400"], anos=[2024])

    with admin_session() as s:
        linhas = s.scalars(
            select(AuditLog).where(
                AuditLog.org_id == org.org_id, AuditLog.acao == "ingestao.job.criar"
            )
        ).all()
    assert linhas, "a criação do job deve ir para op.audit_log"
    assert FONTE in linhas[0].recurso


def test_polling_de_jobs_nao_inunda_auditoria_http(client, make_org):
    org = make_org(capacidades=["ver", "administrar"])
    tok = login(client, org.email, org.senha)

    for _ in range(3):
        response = client.get(
            "/admin/ingestion/jobs",
            headers=auth_header(tok),
        )
        assert response.status_code == 200, response.text

    with admin_session() as session:
        quantidade = int(
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.org_id == org.org_id,
                    AuditLog.acao == "GET /admin/ingestion/jobs",
                )
            )
            or 0
        )
    assert quantidade == 0


# --- Escopo: sem 'administrar' ⇒ 403 ---
def test_sem_administrar_403(client, make_org, eager):
    org = make_org(capacidades=["ver"], entes=["2304400"])
    tok = login(client, org.email, org.senha)
    r = _criar(client, tok, fonte=FONTE, entes=["2304400"], anos=[2024])
    assert r.status_code == 403, r.text


def test_ente_fora_do_escopo_403(client, make_org, eager):
    ingest_jobs.set_unit_runner(_fake_sempre_ok)
    org = make_org(entes=["2304400"])  # só Fortaleza na carteira
    tok = login(client, org.email, org.senha)
    r = _criar(client, tok, fonte=FONTE, entes=["2307650"], anos=[2024])  # ente fora
    assert r.status_code == 403, r.text


# --- Retificações ---
def test_retificacoes_lista_entregas_supersedidas(client, make_org):
    org = make_org(capacidades=["administrar", "ver"])
    tok = login(client, org.email, org.senha)
    cod = "2399999"
    with admin_session() as s:
        s.execute(delete(DimEntrega).where(text("cod_ibge = '2399999'")))
        s.add_all(
            [
                DimEntrega(
                    cod_ibge=cod,
                    relatorio="RREO",
                    periodo="2024-B6",
                    versao_entrega="v1",
                    homologada_em=datetime(2025, 1, 10, tzinfo=UTC),
                    vigente=False,
                ),
                DimEntrega(
                    cod_ibge=cod,
                    relatorio="RREO",
                    periodo="2024-B6",
                    versao_entrega="v2",
                    homologada_em=datetime(2025, 1, 20, tzinfo=UTC),
                    vigente=True,
                ),
            ]
        )
        s.commit()
    try:
        itens = client.get("/admin/ingestion/retificacoes", headers=auth_header(tok)).json()
        alvo = [i for i in itens if i["cod_ibge"] == cod]
        assert alvo, "a entrega retificada deve aparecer"
        assert alvo[0]["versao_entrega"] == "v2"  # a vigente
        assert alvo[0]["versoes_anteriores"] >= 1
    finally:
        with admin_session() as s:
            s.execute(delete(DimEntrega).where(text("cod_ibge = '2399999'")))
            s.commit()


def test_legados_enfileiram_202_preservam_payload_logs_lineage_e_auditoria(
    client, make_org, eager
):
    ingest_jobs.set_unit_runner(_fake_sempre_ok)
    org = make_org(entes=["2304400"])
    tok = login(client, org.email, org.senha)
    headers = auth_header(tok)
    payload = {
        "fonte": FONTE,
        "entes": ["2304400"],
        "anos": [2024],
        "periodos": [2, 4],
        "versao": "v-retificada",
        "homologada_em": "2025-03-12T10:30:00Z",
        "params": {"url": "https://dados.gov.br/arquivo.xlsx", "aba": "RREO"},
        "force": True,
    }

    response = client.post("/admin/ingestion/run", json=payload, headers=headers)
    assert response.status_code == 202, response.text
    job = response.json()["job"]
    assert job["tipo"] == "run"
    assert job["status"] == "concluido"
    assert job["log_ref"] == f"/admin/ingestion/jobs/{job['id']}#logs"
    stored = job["parametros"]["run_request"]
    assert stored["periodos"] == [2, 4]
    assert stored["homologada_em"] == "2025-03-12T10:30:00Z"
    assert stored["params"]["aba"] == "RREO"
    assert stored["force"] is True

    detail = client.get(f"/admin/ingestion/jobs/{job['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    logs = detail.json()["logs"]
    assert logs
    assert {row["job_id"] for row in logs} == {job["id"]}
    assert {"executando", "concluido"}.issubset({row["status"] for row in logs})

    replay = client.post(
        "/admin/ingestion/replay",
        params={"ente": "2304400", "periodo": "2024-B6", "fonte": FONTE},
        headers=headers,
    )
    assert replay.status_code == 202, replay.text
    replay_job = replay.json()["job"]
    assert replay_job["tipo"] == "replay"
    assert replay_job["parametros"]["payload_legado"] == {
        "ente": "2304400",
        "periodo": "2024-B6",
        "fonte": FONTE,
    }

    with admin_session() as session:
        lineage = list(
            session.scalars(
                select(IngestionLog).where(IngestionLog.job_id == uuid.UUID(job["id"]))
            )
        )
        audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.org_id == org.org_id,
                    AuditLog.acao.in_(
                        ("ingestao.job.criar", "ingestao.job.resultado")
                    ),
                )
            )
        )
    assert lineage
    envelopes = [json.loads(row.recurso) for row in audits]
    criacao = next(row for row in envelopes if row.get("parametros", {}).get("force") is True)
    assert criacao["usuario_id"] == str(org.usuario_id)
    assert criacao["org_id"] == str(org.org_id)
    assert criacao["fonte"] == FONTE
    assert criacao["entes"] == ["2304400"]
    resultado = next(
        row
        for row in envelopes
        if row.get("job_id") == job["id"] and "progresso_pct" in row.get("resultado", {})
    )
    assert resultado["resultado"]["status"] == "concluido"
    assert resultado["resultado"]["detalhe"]["itens"][0]["ok"] is True


def test_assinatura_inativa_bloqueia_novo_job(client, make_org, eager):
    ingest_jobs.set_unit_runner(_fake_sempre_ok)
    org = make_org(entes=["2304400"])
    with admin_session() as session:
        session.add(
            Assinatura(
                org_id=org.org_id,
                plano="suspenso",
                metrica_cobranca="por_ente",
                status="suspensa",
            )
        )
    tok = login(client, org.email, org.senha)

    response = _criar(
        client,
        tok,
        fonte=FONTE,
        tipo="backfill",
        entes=["2304400"],
        anos=[2024],
    )
    assert response.status_code == 403, response.text
    assert response.json()["type"].endswith("inactive-subscription")


def test_pos_job_publica_delta_e_falha_inesperada_termina_falhou(
    client, make_org, eager, monkeypatch
):
    ingest_jobs.set_unit_runner(_fake_sempre_ok)
    ingest_jobs.set_recalcular(True)
    org = make_org(entes=["2304400"])
    tok = login(client, org.email, org.senha)

    monkeypatch.setattr(
        ingest_jobs,
        "_recalcular",
        lambda session, job, entes: {
            "indicadores_recalculados": ["gold.mart_indicador"],
            "cobertura_antes": 3,
            "cobertura_depois": 5,
            "delta_cobertura": 2,
        },
    )
    sucesso = _criar(client, tok, fonte=FONTE, entes=["2304400"], anos=[2024]).json()[
        "job"
    ]
    assert sucesso["resultado"]["indicadores_recalculados"] == ["gold.mart_indicador"]
    assert sucesso["resultado"]["delta_cobertura"] == 2

    def quebrar_pos_job(session, job, entes):  # noqa: ANN001
        raise RuntimeError("materialização indisponível")

    monkeypatch.setattr(ingest_jobs, "_recalcular", quebrar_pos_job)
    falho = _criar(client, tok, fonte=FONTE, entes=["2304400"], anos=[2025]).json()["job"]
    assert falho["status"] == "falhou"
    assert falho["terminado_em"] is not None
    assert falho["resultado"]["erro_sistema"]["fase"] == "pos_job"
    assert "materialização indisponível" in falho["erro_resumo"]
    ingest_jobs.set_recalcular(False)


def test_transicoes_condicionais_impedem_claim_e_retry_duplicados(make_org):
    org = make_org()
    cancel_id = _inserir_job(org.org_id, "na_fila")
    with admin_session() as first:
        cancelled = jobs_repository.cancel_job(
            first, cancel_id, terminado_em=datetime.now(UTC)
        )
        assert cancelled is not None
    with admin_session() as second:
        assert (
            jobs_repository.claim_job(second, cancel_id, iniciado_em=datetime.now(UTC))
            is None
        )

    retry_id = _inserir_job(org.org_id, "falhou")
    with admin_session() as first:
        assert jobs_repository.retry_job(first, retry_id, itens_total=1) is not None
    with admin_session() as second:
        assert jobs_repository.retry_job(second, retry_id, itens_total=1) is None
