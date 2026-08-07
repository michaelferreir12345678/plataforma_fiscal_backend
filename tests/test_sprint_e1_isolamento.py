"""Sprint E1 — matriz de isolamento entre organizações e o gate da Central de Dados.

Duas famílias, e as duas nasceram de achados **confirmados no código** pela frente P4 da
auditoria A0R (``docs/evolucao_plataforma.md`` §5.1.2):

1. **404 × 403 com régua.** A convenção certa já existia — o repositório filtra por
   ``org_id`` e o serviço devolve 404, então a existência de um recurso alheio não vaza
   nem pelo código de status. O que faltava era ela ser **exigida**:
   ``test_sprint28_seguranca.py`` aceitava ``403 ou 404``, e uma regressão que passasse a
   responder 403 — revelando que o recurso existe — não quebraria nada. Aqui a asserção é
   ``== 404``, por família de recurso, em **leitura e mutação**.

   O intruso recebe **todas** as capacidades RBAC na própria organização, de propósito:
   assim um 403 só poderia vir da fronteira entre tenants, nunca de permissão faltando —
   o teste mede o que diz medir.

2. **A22 — ``GET /admin/ingestion/data?ente=`` sem gate de escopo.** A rota exigia apenas
   a capacidade ``administrar`` e nunca chamava ``assert_ente_in_scope``. O dado do
   SICONFI é público, então não havia vazamento entre tenants; o que estava furado era o
   **gate de licença**, que vive dentro daquele ``assert``: uma conta licenciada para um
   município lia o silver de qualquer um dos 5.598.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.db import admin_session
from app.modules.alerts.models import Alerta
from app.modules.forecast.models import Cenario
from app.modules.ingestion.jobs_models import STATUS_FALHOU, STATUS_NA_FILA, IngestJob
from app.modules.reports.models import Relatorio, RelatorioAgendamento
from tests.conftest import auth_header, login

FORTALEZA = "2304400"
MARACANAU = "2307650"
PERIODO = "2024-B6"


# --------------------------------------------------------------------------- #
# Semeadura: os artefatos do "dono", criados direto no banco (contexto admin)
# --------------------------------------------------------------------------- #
def _semear_artefatos(org_id: uuid.UUID, usuario_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Um artefato de cada família que a E1 exige cobrir, na organização do dono."""
    agora = datetime.now(UTC)
    ids = {
        "relatorio": uuid.uuid4(),
        "agendamento": uuid.uuid4(),
        "cenario": uuid.uuid4(),
        "alerta": uuid.uuid4(),
        "job": uuid.uuid4(),
    }
    with admin_session() as s:
        s.add(
            Relatorio(
                id=ids["relatorio"], org_id=org_id, lote_id=uuid.uuid4(), modelo="limites",
                formato="pdf", escopo="ente", cod_ibge=FORTALEZA, periodo=PERIODO,
                as_of=agora, status="enfileirado", criado_por=usuario_id,
            )
        )
        s.add(
            RelatorioAgendamento(
                id=ids["agendamento"], org_id=org_id, modelo="limites", formato="pdf",
                escopo="ente", entes=[FORTALEZA], periodo=PERIODO, periodicidade="mensal",
                proxima_execucao=agora + timedelta(days=30), ativo=True,
                criado_por=usuario_id,
            )
        )
        s.add(
            Cenario(
                id=ids["cenario"], org_id=org_id, ente=FORTALEZA, indicador="pessoal",
                nome="Cenário do dono", parametros={"horizonte": 4},
                criado_por=usuario_id,
            )
        )
        s.add(
            Alerta(
                id=ids["alerta"], org_id=org_id, cod_ibge=FORTALEZA,
                chave=f"e1:isolamento:{uuid.uuid4().hex}", categoria="limite",
                severidade="critico", prioridade=1, titulo="Alerta do dono",
                motivo_legal="LRF art. 20", acao_sugerida="Conferir a apuração.",
                status="nova",
            )
        )
        s.add(
            IngestJob(
                id=ids["job"], org_id=org_id, criado_por=usuario_id, fonte="siconfi_rreo",
                tipo="backfill", entes=[FORTALEZA], periodos=["2024"],
                status=STATUS_NA_FILA,
            )
        )
    return ids


@pytest.fixture
def matriz(client, make_org):
    """Duas organizações reais, artefatos do dono e um intruso com RBAC completo."""
    dono = make_org(entes=[FORTALEZA])
    intruso = make_org(entes=[MARACANAU])
    ids = _semear_artefatos(dono.org_id, dono.usuario_id)
    h_dono = auth_header(login(client, dono.email, dono.senha))
    h_intruso = auth_header(login(client, intruso.email, intruso.senha))
    yield ids, h_dono, h_intruso, dono, intruso


def _rotas_de_leitura(ids: dict[str, uuid.UUID]) -> list[tuple[str, str, str]]:
    return [
        ("relatorio", "GET", f"/relatorios/{ids['relatorio']}"),
        ("relatorio", "GET", f"/relatorios/{ids['relatorio']}/arquivo"),
        ("cenario", "GET", f"/cenarios/{ids['cenario']}"),
        ("cenario", "GET", f"/cenarios/{ids['cenario']}/exportar"),
        ("job", "GET", f"/admin/ingestion/jobs/{ids['job']}"),
    ]


def _rotas_de_mutacao(ids: dict[str, uuid.UUID]) -> list[tuple[str, str, str, dict]]:
    return [
        ("agendamento", "PATCH", f"/relatorios/agendamentos/{ids['agendamento']}",
         {"ativo": False}),
        ("agendamento", "DELETE", f"/relatorios/agendamentos/{ids['agendamento']}", {}),
        ("cenario", "PATCH", f"/cenarios/{ids['cenario']}", {"nome": "sequestrado"}),
        ("cenario", "DELETE", f"/cenarios/{ids['cenario']}", {}),
        ("cenario", "DELETE", f"/cenarios/{ids['cenario']}/definitivo", {}),
        ("alerta", "PATCH", f"/alertas/{ids['alerta']}", {"status": "descartada"}),
        ("job", "POST", f"/admin/ingestion/jobs/{ids['job']}/cancelar", {}),
        ("job", "POST", f"/admin/ingestion/jobs/{ids['job']}/retry", {}),
    ]


def _chamar(client, metodo: str, rota: str, headers: dict, corpo: dict | None = None):
    if metodo == "GET":
        return client.get(rota, headers=headers)
    if metodo == "PATCH":
        return client.patch(rota, headers=headers, json=corpo or {})
    if metodo == "DELETE":
        return client.delete(rota, headers=headers)
    return client.post(rota, headers=headers, json=corpo or {})


# --------------------------------------------------------------------------- #
# 1. Leitura entre organizações
# --------------------------------------------------------------------------- #
def test_leitura_de_recurso_de_outra_organizacao_e_exatamente_404(client, matriz) -> None:
    """``in {403, 404}`` deixava passar o vazamento que o teste existia para impedir.

    403 quer dizer "existe, e você não pode"; 404, "não existe para você". Para
    identificador de outro tenant, só o segundo é aceitável — o primeiro confirma ao
    intruso que ele adivinhou um identificador válido.
    """
    ids, _h_dono, h_intruso, dono, _intruso = matriz
    for familia, metodo, rota in _rotas_de_leitura(ids):
        resposta = _chamar(client, metodo, rota, h_intruso)
        assert resposta.status_code == 404, (
            f"{familia}: {metodo} {rota} devolveu {resposta.status_code} "
            f"— existência de recurso alheio não pode vazar pelo status"
        )
        corpo = resposta.text
        assert FORTALEZA not in corpo, f"{familia}: o corpo do erro vazou o ente do outro tenant"
        assert str(dono.org_id) not in corpo, f"{familia}: o corpo do erro vazou o org_id alheio"


# --------------------------------------------------------------------------- #
# 2. Mutação entre organizações
# --------------------------------------------------------------------------- #
def test_mutacao_de_recurso_de_outra_organizacao_e_exatamente_404(client, matriz) -> None:
    """Metade da matriz que faltava: ler o alheio é ruim, **escrever** é pior."""
    ids, _h_dono, h_intruso, dono, _intruso = matriz
    for familia, metodo, rota, corpo in _rotas_de_mutacao(ids):
        resposta = _chamar(client, metodo, rota, h_intruso, corpo)
        assert resposta.status_code == 404, (
            f"{familia}: {metodo} {rota} devolveu {resposta.status_code} "
            f"(corpo: {resposta.text[:200]})"
        )
        assert FORTALEZA not in resposta.text
        assert str(dono.org_id) not in resposta.text


def test_nenhuma_mutacao_do_intruso_tocou_o_dado_do_dono(client, matriz) -> None:
    """O 404 tem de ser recusa, não silêncio: o dado do dono continua como estava.

    Sem esta verificação, um handler que apagasse a linha e **depois** devolvesse 404
    passaria nos dois testes acima.
    """
    ids, _h_dono, h_intruso, _dono, _intruso = matriz
    for _familia, metodo, rota, corpo in _rotas_de_mutacao(ids):
        _chamar(client, metodo, rota, h_intruso, corpo)

    with admin_session() as s:
        agendamento = s.get(RelatorioAgendamento, ids["agendamento"])
        cenario = s.get(Cenario, ids["cenario"])
        alerta = s.get(Alerta, ids["alerta"])
        job = s.get(IngestJob, ids["job"])
        assert agendamento is not None and agendamento.ativo is True
        assert cenario is not None
        assert cenario.nome == "Cenário do dono"
        assert cenario.arquivado_em is None
        assert alerta is not None and alerta.status == "nova"
        assert job is not None and job.status == STATUS_NA_FILA


def test_o_dono_continua_enxergando_o_que_e_dele(client, matriz) -> None:
    """Sem este lado, um 404 universal passaria na matriz inteira e quebraria o produto."""
    ids, h_dono, _h_intruso, _dono, _intruso = matriz
    assert client.get(f"/relatorios/{ids['relatorio']}", headers=h_dono).status_code == 200
    assert (
        client.get(f"/admin/ingestion/jobs/{ids['job']}", headers=h_dono).status_code == 200
    )
    renomear = client.patch(
        f"/cenarios/{ids['cenario']}", headers=h_dono, json={"nome": "Renomeado pelo dono"}
    )
    assert renomear.status_code == 200, renomear.text
    descartar = client.patch(
        f"/alertas/{ids['alerta']}", headers=h_dono, json={"status": "descartada"}
    )
    assert descartar.status_code == 200, descartar.text


def test_um_job_falho_do_dono_nao_pode_ser_reexecutado_pelo_vizinho(
    client, make_org
) -> None:
    """``retry`` é a mutação mais cara da Central: reexecuta carga em nome de outro tenant."""
    dono = make_org(entes=[FORTALEZA])
    intruso = make_org(entes=[MARACANAU])
    job_id = uuid.uuid4()
    with admin_session() as s:
        s.add(
            IngestJob(
                id=job_id, org_id=dono.org_id, criado_por=dono.usuario_id,
                fonte="siconfi_rreo", tipo="backfill", entes=[FORTALEZA], periodos=["2024"],
                status=STATUS_FALHOU, itens_total=1, itens_erro=1,
                resultado={"itens": [{"ente": FORTALEZA, "chave": "2024", "ok": False}]},
            )
        )
    h_intruso = auth_header(login(client, intruso.email, intruso.senha))
    resposta = client.post(f"/admin/ingestion/jobs/{job_id}/retry", headers=h_intruso, json={})
    assert resposta.status_code == 404, resposta.text
    with admin_session() as s:
        job = s.get(IngestJob, job_id)
        assert job is not None
        assert job.status == STATUS_FALHOU, "o vizinho reenfileirou o job do dono"
        assert job.tentativas == 0


# --------------------------------------------------------------------------- #
# 3. A22 — gate de escopo e licença em GET /admin/ingestion/data
# --------------------------------------------------------------------------- #
def test_ingestao_data_recusa_ente_fora_da_carteira(client, make_org) -> None:
    """Antes da E1 esta chamada devolvia 200 com o silver do ente alheio."""
    fx = make_org(entes=[MARACANAU])
    h = auth_header(login(client, fx.email, fx.senha))
    resposta = client.get(
        "/admin/ingestion/data",
        params={"fonte": "siconfi_rreo", "ente": FORTALEZA, "periodo": PERIODO},
        headers=h,
    )
    assert resposta.status_code == 403, resposta.text
    assert resposta.json()["type"].endswith("scope-forbidden"), resposta.text


def test_ingestao_data_recusa_ente_na_carteira_e_fora_da_licenca(client, make_org) -> None:
    """A distinção de causa é o ponto: cadastro e comercial pedem ações opostas."""
    fx = make_org(entes=[FORTALEZA], licenciar=False)
    h = auth_header(login(client, fx.email, fx.senha))
    resposta = client.get(
        "/admin/ingestion/data",
        params={"fonte": "siconfi_rreo", "ente": FORTALEZA, "periodo": PERIODO},
        headers=h,
    )
    assert resposta.status_code == 403, resposta.text
    assert resposta.json()["type"].endswith("ente-nao-licenciado"), resposta.text


def test_ingestao_data_libera_ente_na_carteira_e_licenciado(client, make_org) -> None:
    """O terceiro estado: com carteira e licença, a leitura continua funcionando."""
    fx = make_org(entes=[FORTALEZA])
    h = auth_header(login(client, fx.email, fx.senha))
    resposta = client.get(
        "/admin/ingestion/data",
        params={"fonte": "siconfi_rreo", "ente": FORTALEZA, "periodo": PERIODO},
        headers=h,
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["cod_ibge"] == FORTALEZA
    assert corpo["source_ref"]["relatorio"] == "RREO"


def test_o_gate_de_ingestao_data_vive_tambem_no_servico(client, make_org) -> None:
    """O caminho programático (worker, script) não passa pelo roteador.

    Deixar o ``assert`` só na borda faria o gate valer para o navegador e não para o
    código — que é justamente por onde uma rotina interna leria o silver alheio.
    """
    from app.core.deps import Principal
    from app.core.errors import ScopeForbiddenError
    from app.modules.ingestion import service as ingestion_service

    fx = make_org(entes=[MARACANAU])
    principal = Principal(
        usuario_id=fx.usuario_id,
        org_id=fx.org_id,
        papel="Papel",
        capacidades=frozenset({"administrar", "ver"}),
        escopo_ibges=None,
    )
    with admin_session() as s, pytest.raises(ScopeForbiddenError):
        ingestion_service.read_data(
            s,
            principal=principal,
            fonte="siconfi_rreo",
            ente=FORTALEZA,
            periodo=PERIODO,
        )
