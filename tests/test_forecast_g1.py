"""Sprint G1 — Cenários "E se?" em padrão de memorando técnico governamental.

Dois defeitos críticos e um pacote de robustez, testados juntos porque são a mesma tela.

**A19.** ``forecast/router.py`` já condicionava ``PATCH``/``DELETE /cenarios/{id}`` a
``require_capability("editar")`` desde a Sprint C2 — mas "editar" nunca existiu no enum
de capacidades nem no ``CheckConstraint`` do banco. Nenhum papel, de nenhuma organização,
jamais conseguiu receber essa capacidade: renomear e arquivar cenário devolviam 403 para
todo mundo, sempre. A migration 0040 fecha o buraco; os testes abaixo provam os dois lados
(sem a capacidade → 403; com ela → sucesso), porque um teste que só prova o "depois" não
distingue "nunca funcionou" de "sempre funcionou".

**A20.** ``_impacto_cenario`` só usava ``crescimento_rcl_pct`` no ramo ``BRL`` — o ramo
``PCT_RCL`` (Pessoal, Dívida: os dois indicadores com teto mais severo da LRF) ignorava o
slider por inteiro. O teste prova resultado diferente para ``crescimento_rcl_pct != 0``.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.db import SessionLocal, admin_session, apply_context
from app.modules.alerts import engine
from app.modules.alerts.models import Alerta
from app.modules.catalog.models import DimEnte
from app.modules.forecast import service
from app.modules.forecast.models import FatoProjecao
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import BcbIndice, DimEntrega, SilverEnte, TesouroFpm
from tests.conftest import auth_header, login

# Reusa a semeadura sintética da Sprint 14 (`test_forecast.py`) em vez de duplicá-la —
# importa só as *funções*, não a fixture: importar `forecast_case` por nome faria o ruff
# marcar cada parâmetro de teste homônimo como "redefinição" da importação (F811), porque
# o mecanismo de fixture do pytest e a análise estática do ruff enxergam esse nome de
# jeitos diferentes. A fixture abaixo é local de propósito, mesma limpeza do original.
from tests.test_forecast import VERSAO, ForecastCase, _ente, _seed, _token

ENTE_ESTADUAL = "23"  # Ceará — série real de dívida (fora do universo sintético de G1)


@pytest.fixture
def forecast_case() -> Iterator[ForecastCase]:
    case = ForecastCase(cod_ibge=_ente())
    _seed(case)
    yield case
    cod = case.cod_ibge
    with SessionLocal() as s:
        s.execute(delete(FatoProjecao).where(FatoProjecao.cod_ibge == cod))
        s.execute(delete(FatoRcl).where(FatoRcl.cod_ibge == cod))
        s.execute(delete(MartIndicador).where(MartIndicador.cod_ibge == cod))
        s.execute(delete(TesouroFpm).where(TesouroFpm.cod_ibge == cod))
        s.execute(delete(BcbIndice).where(BcbIndice.versao_entrega == VERSAO))
        s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
        s.execute(
            delete(DimEntrega).where(
                DimEntrega.cod_ibge == "BR",
                DimEntrega.relatorio == "FPM",
                DimEntrega.versao_entrega == VERSAO,
            )
        )
        s.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))
        s.execute(delete(SilverEnte).where(SilverEnte.cod_ibge == cod))
        s.commit()


def _simular(client, token, cod: str, indicador: str, body: dict) -> dict:
    resp = client.post(
        f"/entes/{cod}/cenario/simular",
        params={"indicador": indicador},
        json=body,
        headers=auth_header(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _salvar(client, token, cod: str, indicador: str = "rcl") -> str:
    body = _simular(client, token, cod, indicador, {"nome": "G1", "horizonte": 2, "salvar": True})
    assert body["persistido"] is True
    return body["cenario_id"]


# --------------------------------------------------------------------------------------
# A19 — a capacidade "editar"
# --------------------------------------------------------------------------------------
def test_renomear_sem_capacidade_editar_e_403(
    client, make_org, forecast_case: ForecastCase
) -> None:
    cod = forecast_case.cod_ibge
    org = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, org.email, org.senha)
    cenario_id = _salvar(client, token, cod)

    resp = client.patch(
        f"/cenarios/{cenario_id}", json={"nome": "Novo nome"}, headers=auth_header(token)
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == "urn:plataforma-fiscal:error:missing-capability"


def test_renomear_com_capacidade_editar_funciona(
    client, make_org, forecast_case: ForecastCase
) -> None:
    cod = forecast_case.cod_ibge
    org = make_org(capacidades=["ver", "editar"], entes=[cod])
    token = login(client, org.email, org.senha)
    cenario_id = _salvar(client, token, cod)

    resp = client.patch(
        f"/cenarios/{cenario_id}", json={"nome": "Renomeado"}, headers=auth_header(token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["nome"] == "Renomeado"


def test_arquivar_sem_capacidade_editar_e_403(
    client, make_org, forecast_case: ForecastCase
) -> None:
    cod = forecast_case.cod_ibge
    org = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, org.email, org.senha)
    cenario_id = _salvar(client, token, cod)

    resp = client.delete(f"/cenarios/{cenario_id}", headers=auth_header(token))
    assert resp.status_code == 403, resp.text


def test_arquivar_com_capacidade_editar_funciona(
    client, make_org, forecast_case: ForecastCase
) -> None:
    cod = forecast_case.cod_ibge
    org = make_org(capacidades=["ver", "editar"], entes=[cod])
    token = login(client, org.email, org.senha)
    cenario_id = _salvar(client, token, cod)

    resp = client.delete(f"/cenarios/{cenario_id}", headers=auth_header(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["arquivado"] is True


# --------------------------------------------------------------------------------------
# A20 — crescimento_rcl_pct no ramo PCT_RCL
# --------------------------------------------------------------------------------------
def test_crescimento_rcl_pct_dilui_pessoal_quando_rcl_cai(
    client, make_org, forecast_case: ForecastCase
) -> None:
    """Antes: mesmo resultado com ou sem a premissa. Depois: RCL menor infla o % (A20)."""
    cod = forecast_case.cod_ibge
    token = _token(client, make_org, cod)

    sem = _simular(client, token, cod, "pessoal", {"nome": "sem RCL", "horizonte": 2})
    com = _simular(
        client, token, cod, "pessoal",
        {"nome": "RCL -10%", "horizonte": 2, "crescimento_rcl_pct": -10.0},
    )
    pct_sem = Decimal(str(sem["impacto_limites"][0]["pct_projetado"]))
    pct_com = Decimal(str(com["impacto_limites"][0]["pct_projetado"]))

    assert pct_com != pct_sem, "A20: crescimento_rcl_pct continua no-op no ramo PCT_RCL"
    assert pct_com > pct_sem, "RCL menor deveria AUMENTAR o indicador em % da RCL"
    # Relação exata: dividir pelo fator de crescimento, não somar/ignorar.
    esperado = pct_sem / Decimal("0.9")
    assert abs(pct_com - esperado) < Decimal("0.02"), (pct_com, esperado)


def test_impacto_cenario_service_prova_o_ramo_pct_rcl_isoladamente() -> None:
    """Mesmo teste, direto na função (`_impacto_cenario`), com dado real (Ceará/dívida)."""
    from app.modules.forecast.schemas import CenarioSimularRequest
    from app.modules.forecast.series import carregar_serie

    with admin_session() as session:
        serie = carregar_serie(session, "divida", ENTE_ESTADUAL)
        cenario = service.build_projecao(
            session, ENTE_ESTADUAL, "divida", horizonte=2, persistir=False
        )
        req_zero = CenarioSimularRequest(horizonte=2)
        req_choque = CenarioSimularRequest(horizonte=2, crescimento_rcl_pct=-10.0)
        tetos_zero, _ = service._impacto_cenario(session, serie, cenario, "estadual", req_zero)
        tetos_choque, _ = service._impacto_cenario(
            session, serie, cenario, "estadual", req_choque
        )
    assert tetos_zero[0].pct_projetado != tetos_choque[0].pct_projetado
    assert tetos_choque[0].pct_projetado > tetos_zero[0].pct_projetado


# --------------------------------------------------------------------------------------
# Robustez — FUNDEB separado de FPM
# --------------------------------------------------------------------------------------
def test_fundeb_variacao_pct_move_a_projecao_de_rcl(
    client, make_org, forecast_case: ForecastCase
) -> None:
    cod = forecast_case.cod_ibge
    token = _token(client, make_org, cod)

    sem = _simular(client, token, cod, "rcl", {"nome": "sem FUNDEB", "horizonte": 2})
    com = _simular(
        client, token, cod, "rcl",
        {"nome": "FUNDEB +10%", "horizonte": 2, "fundeb_variacao_pct": 10.0},
    )
    final_sem = Decimal(str(sem["cenario"]["projecao"][-1]["valor_previsto"]))
    final_com = Decimal(str(com["cenario"]["projecao"][-1]["valor_previsto"]))
    assert final_com != final_sem, "fundeb_variacao_pct não pode ser no-op na projeção de RCL"
    assert final_com > final_sem


def test_fundeb_informado_fora_de_rcl_receita_e_avisado_nao_silenciado(
    client, make_org, forecast_case: ForecastCase
) -> None:
    """FUNDEB não se aplica a Pessoal/Dívida — mas o pedido não pode sumir sem explicação
    (a mesma armadilha do A20: um parâmetro informado que não faz nada, sem aviso)."""
    cod = forecast_case.cod_ibge
    token = _token(client, make_org, cod)
    body = _simular(
        client, token, cod, "pessoal",
        {"nome": "FUNDEB em pessoal", "horizonte": 2, "fundeb_variacao_pct": 15.0},
    )
    avisos = body["memoria"]["avisos_premissas"]
    assert any("fundeb_variacao_pct" in a for a in avisos)


# --------------------------------------------------------------------------------------
# Robustez — reajuste de folha distinto do choque genérico de pessoal
# --------------------------------------------------------------------------------------
def test_reajuste_folha_pct_move_a_projecao_de_pessoal_alem_do_choque_generico(
    client, make_org, forecast_case: ForecastCase
) -> None:
    cod = forecast_case.cod_ibge
    token = _token(client, make_org, cod)

    so_generico = _simular(
        client, token, cod, "pessoal",
        {"nome": "só choque genérico", "horizonte": 2, "crescimento_indicador_pct": 2.0},
    )
    com_folha = _simular(
        client, token, cod, "pessoal",
        {
            "nome": "choque + folha", "horizonte": 2,
            "crescimento_indicador_pct": 2.0, "reajuste_folha_pct": 5.0,
        },
    )
    final_generico = Decimal(str(so_generico["cenario"]["projecao"][-1]["valor_previsto"]))
    final_folha = Decimal(str(com_folha["cenario"]["projecao"][-1]["valor_previsto"]))
    assert final_folha != final_generico, "reajuste_folha_pct precisa somar um efeito próprio"


# --------------------------------------------------------------------------------------
# Robustez — simulador de novo contrato de dívida (escopo mínimo: só o impacto)
# --------------------------------------------------------------------------------------
def test_novo_contrato_divida_calcula_impacto_no_teto(client, make_org) -> None:
    org = make_org(capacidades=["ver"], entes=[ENTE_ESTADUAL], tipo_conta="estado")
    token = login(client, org.email, org.senha)

    body = _simular(
        client, token, ENTE_ESTADUAL, "divida",
        {
            "nome": "novo contrato",
            "horizonte": 2,
            "novo_contrato_divida": {
                # Ceará: RCL ~R$ 39,6 bi, dívida atual ~26,5% da RCL, teto estadual 200%.
                # R$ 130 bi de principal sozinho já é ~328% da RCL — deliberadamente muito
                # acima do teto, para o teste não depender de casas decimais da série real.
                "principal_rs": 130_000_000_000,
                "prazo_meses": 120,
                "carencia_meses": 24,
                "taxa_aa_pct": 9.5,
            },
        },
    )
    impacto = body["impacto_contrato_divida"]
    assert impacto is not None
    assert impacto["principal_rs"] is not None
    assert Decimal(str(impacto["pct_rcl_adicional"])) > 0
    assert impacto["cruza"] is True, "um principal desproporcional precisa cruzar o teto de DCL"
    assert "LRF" in impacto["fundamento"]


def test_novo_contrato_divida_nao_persiste(client, make_org) -> None:
    """Escopo mínimo (ficha): calcula o impacto, não grava o contrato hipotético em lugar
    nenhum — nem em op.cenario (parâmetro viaja em `parametros`, não vira operação real)."""
    org = make_org(capacidades=["ver"], entes=[ENTE_ESTADUAL], tipo_conta="estado")
    token = login(client, org.email, org.senha)
    body = _simular(
        client, token, ENTE_ESTADUAL, "divida",
        {
            "nome": "sem salvar", "horizonte": 2,
            "novo_contrato_divida": {"principal_rs": 1_000_000, "prazo_meses": 12},
        },
    )
    assert body["persistido"] is False


def test_novo_contrato_divida_ignorado_fora_do_indicador_divida(
    client, make_org, forecast_case: ForecastCase
) -> None:
    cod = forecast_case.cod_ibge
    token = _token(client, make_org, cod)
    body = _simular(
        client, token, cod, "pessoal",
        {
            "nome": "contrato em pessoal", "horizonte": 2,
            "novo_contrato_divida": {"principal_rs": 1_000_000, "prazo_meses": 12},
        },
    )
    assert body["impacto_contrato_divida"] is None
    assert any(
        "novo_contrato_divida" in a for a in body["memoria"]["avisos_premissas"]
    ), "premissa informada e ignorada precisa ser dita, não silenciada"


# --------------------------------------------------------------------------------------
# CRUD — duplicar, excluir definitivamente, criado_por
# --------------------------------------------------------------------------------------
def test_duplicar_cenario_cria_registro_independente(
    client, make_org, forecast_case: ForecastCase
) -> None:
    cod = forecast_case.cod_ibge
    token = _token(client, make_org, cod)
    original_id = _salvar(client, token, cod)

    resp = client.post(
        f"/cenarios/{original_id}/duplicar", json={}, headers=auth_header(token)
    )
    assert resp.status_code == 201, resp.text
    clone = resp.json()
    assert clone["id"] != original_id
    assert clone["nome"].startswith("Cópia de")
    assert clone["versao_atual"] == 1

    listados = client.get(f"/entes/{cod}/cenarios", headers=auth_header(token)).json()
    assert {c["id"] for c in listados} >= {original_id, clone["id"]}


def test_excluir_definitivo_remove_o_cenario_e_exige_editar(
    client, make_org, forecast_case: ForecastCase
) -> None:
    cod = forecast_case.cod_ibge
    org_sem = make_org(capacidades=["ver"], entes=[cod])
    token_sem = login(client, org_sem.email, org_sem.senha)
    cid_sem = _salvar(client, token_sem, cod)
    bloqueado = client.delete(f"/cenarios/{cid_sem}/definitivo", headers=auth_header(token_sem))
    assert bloqueado.status_code == 403, bloqueado.text

    org_com = make_org(capacidades=["ver", "editar"], entes=[cod])
    token_com = login(client, org_com.email, org_com.senha)
    cid_com = _salvar(client, token_com, cod)

    apagado = client.delete(f"/cenarios/{cid_com}/definitivo", headers=auth_header(token_com))
    assert apagado.status_code == 204, apagado.text

    sumiu = client.get(
        f"/entes/{cod}/cenarios",
        params={"incluir_arquivados": True},
        headers=auth_header(token_com),
    ).json()
    assert cid_com not in {c["id"] for c in sumiu}

    reabrir = client.get(f"/cenarios/{cid_com}", headers=auth_header(token_com))
    assert reabrir.status_code == 404


def test_excluir_definitivo_distingue_se_de_arquivar(
    client, make_org, forecast_case: ForecastCase
) -> None:
    """Arquivar continua reversível; excluir definitivamente não é a mesma ação."""
    cod = forecast_case.cod_ibge
    org = make_org(capacidades=["ver", "editar"], entes=[cod])
    token = login(client, org.email, org.senha)
    arquivado_id = _salvar(client, token, cod)
    client.delete(f"/cenarios/{arquivado_id}", headers=auth_header(token))  # arquiva

    ainda_existe = client.get(f"/cenarios/{arquivado_id}", headers=auth_header(token))
    assert ainda_existe.status_code == 200, "arquivar não pode ter apagado o registro"
    assert ainda_existe.json()["cenario"]["arquivado"] is True


def test_criado_por_exposto_no_detalhe_e_na_versao(
    client, make_org, forecast_case: ForecastCase
) -> None:
    cod = forecast_case.cod_ibge
    org = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, org.email, org.senha)
    cenario_id = _salvar(client, token, cod)

    resp = client.get(f"/entes/{cod}/cenarios", headers=auth_header(token))
    assert resp.status_code == 200, resp.text
    detalhe = next(c for c in resp.json() if c["id"] == cenario_id)
    assert detalhe["criado_por"] == org.email
    assert detalhe["versoes"][0]["criado_por"] == org.email


# --------------------------------------------------------------------------------------
# Seletor de modelo — já era aceito pelo backend; prova que chega intacto ao resultado
# --------------------------------------------------------------------------------------
def test_modelo_forcado_na_simulacao_e_respeitado(
    client, make_org, forecast_case: ForecastCase
) -> None:
    cod = forecast_case.cod_ibge
    token = _token(client, make_org, cod)
    body = _simular(
        client, token, cod, "rcl",
        {"nome": "modelo forçado", "horizonte": 2, "modelo": "fechamento"},
    )
    assert body["cenario"]["modelo"] == "fechamento"


# --------------------------------------------------------------------------------------
# memoria.observacao_minimos viaja até a resposta (a tela agora a renderiza)
# --------------------------------------------------------------------------------------
def test_observacao_minimos_viaja_na_memoria(client, make_org, forecast_case: ForecastCase) -> None:
    cod = forecast_case.cod_ibge
    token = _token(client, make_org, cod)
    body = _simular(client, token, cod, "rcl", {"nome": "aviso legal", "horizonte": 2})
    assert isinstance(body["memoria"].get("observacao_minimos"), str)
    assert "não substitui a apuração oficial" in body["memoria"]["observacao_minimos"]


def test_alerta_preditivo_leva_o_indicador_de_origem(client, make_org, forecast_case) -> None:
    """Antes: `link` sempre "/previsoes", sem indicador — quem clicava caía em RCL genérico
    e tinha que reencontrar Pessoal/Dívida à mão. O ente sintético cruza o teto de 54% de
    pessoal já na série histórica (fixture de test_forecast.py), então o motor emite o
    alerta preditivo com certeza, sem depender de heurística de projeção."""
    cod = forecast_case.cod_ibge
    org = make_org(capacidades=["ver"], entes=[cod])
    with SessionLocal() as session:
        apply_context(session, org_id=org.org_id, user_id=None, is_admin=True)
        engine.avaliar_ente(session, org.org_id, cod, incluir_preditivo=True)
        session.commit()
    with SessionLocal() as session:
        apply_context(session, org_id=org.org_id, user_id=None, is_admin=True)
        alerta = session.scalar(
            select(Alerta).where(
                Alerta.cod_ibge == cod,
                Alerta.categoria == "preditivo",
                Alerta.indicador == "pessoal",
            )
        )
    assert alerta is not None, "cenário sintético cruza 54% — o preditivo tem de disparar"
    assert alerta.link == "/previsoes?indicador=pessoal"
