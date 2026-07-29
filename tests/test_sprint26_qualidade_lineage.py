"""Aceites da Sprint 26 — checks de qualidade, lineage e alertas de dado.

Cada check tem caso **ok**, **falha** e **aviso** (não-aplicável), porque os três são
estados diferentes: "verificado e correto", "verificado e errado" e "não deu para
verificar". Confundir os dois últimos é o defeito que esta sprint existe para evitar.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, select

from app.core.db import SessionLocal, admin_session
from app.modules.alerts.models import Alerta
from app.modules.catalog.models import DimEnte
from app.modules.debt.models import FatoDivida
from app.modules.expense.models import FatoDespesa
from app.modules.health_edu.models import FatoSaude
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import DimEntrega, SilverEnte, SilverRreo
from app.modules.personnel.models import FatoPessoal
from app.modules.quality import checks, lineage_seed
from app.modules.quality import service as quality
from app.modules.quality.models import DataQualityCheck, LineageEdge
from app.modules.result.models import FatoResultado
from app.modules.revenue.models import DimOrigemReceita, FatoReceita
from tests.conftest import auth_header, login

ANO = 2086
PERIODO = f"{ANO}-B6"
PERIODO_RGF = f"{ANO}-Q3"
RREO_V = "s26-rreo-v1"
RGF_V = "s26-rgf-v1"

RAIZ = "S26ReceitasCorrentes"
FILHO_A = "S26ReceitaTributaria"
FILHO_B = "S26TransferenciasCorrentes"


def _codigo() -> str:
    while True:
        cod = str(9_400_000 + uuid.uuid4().int % 400_000)
        with SessionLocal() as session:
            if session.scalar(select(DimEnte.cod_ibge).where(DimEnte.cod_ibge == cod)) is None:
                return cod


def _seed_dim_origem(session: Any) -> None:
    for codigo, parent, nivel in (
        (RAIZ, None, 1),
        (FILHO_A, RAIZ, 2),
        (FILHO_B, RAIZ, 2),
    ):
        if session.get(DimOrigemReceita, codigo) is None:
            session.add(
                DimOrigemReceita(
                    codigo=codigo, descricao=codigo, parent_codigo=parent, nivel=nivel,
                    path=codigo if parent is None else f"{RAIZ}.{codigo}",
                )
            )


def _seed(session: Any, cod: str, *, receita_raiz: int = 1000, rcl_publicada: int = 900) -> None:
    session.add(
        SilverEnte(
            cod_ibge=cod, nome="Ente 26", uf="CE", esfera="M", populacao=100_000,
            regiao="NE", capital=False, versao_entrega="s26-ibge",
        )
    )
    session.add(
        DimEnte(
            cod_ibge=cod, nome="Ente 26", esfera="municipal", populacao=100_000,
            rpps=False, possui_tcm=False, uf="CE", regiao="NE",
        )
    )
    session.add_all(
        [
            DimEntrega(
                cod_ibge=cod, relatorio="RREO", periodo=PERIODO, versao_entrega=RREO_V,
                homologada_em=datetime(ANO + 1, 1, 30, tzinfo=UTC), vigente=True,
            ),
            DimEntrega(
                cod_ibge=cod, relatorio="RGF", periodo=PERIODO_RGF, versao_entrega=RGF_V,
                homologada_em=datetime(ANO + 1, 1, 30, tzinfo=UTC), vigente=True,
            ),
        ]
    )
    _seed_dim_origem(session)
    # Receita: pai = 1000, filhos = 600 + 400.
    session.add_all(
        [
            FatoReceita(
                cod_ibge=cod, periodo=PERIODO, origem_codigo=RAIZ,
                previsto_inicial=Decimal(receita_raiz), previsto_atualizado=Decimal(receita_raiz),
                arrecadado_bimestre=Decimal(receita_raiz), arrecadado_acum=Decimal(receita_raiz),
                deducoes=Decimal(0), versao_entrega=RREO_V,
            ),
            FatoReceita(
                cod_ibge=cod, periodo=PERIODO, origem_codigo=FILHO_A,
                previsto_inicial=Decimal(600), previsto_atualizado=Decimal(600),
                arrecadado_bimestre=Decimal(600), arrecadado_acum=Decimal(600),
                deducoes=Decimal(0), versao_entrega=RREO_V,
            ),
            FatoReceita(
                cod_ibge=cod, periodo=PERIODO, origem_codigo=FILHO_B,
                previsto_inicial=Decimal(400), previsto_atualizado=Decimal(400),
                arrecadado_bimestre=Decimal(400), arrecadado_acum=Decimal(400),
                deducoes=Decimal(0), versao_entrega=RREO_V,
            ),
        ]
    )
    # Despesa: 500 empenhado ≥ 400 liquidado ≥ 300 pago.
    session.add(
        FatoDespesa(
            cod_ibge=cod, periodo=PERIODO, funcao_codigo="10", natureza_codigo="*",
            dotacao_inicial=Decimal(600), dotacao_atualizada=Decimal(600),
            empenhado=Decimal(500), liquidado=Decimal(400), pago=Decimal(300),
            inscrito_rap=Decimal(0), versao_entrega=RREO_V,
        )
    )
    # RCL calculada = publicada.
    session.add(
        FatoRcl(
            cod_ibge=cod, periodo_ref=PERIODO, rcl_12m=Decimal(rcl_publicada),
            deducoes=Decimal(0), receita_corrente=Decimal(rcl_publicada),
            versao_entrega=RREO_V, memoria={},
        )
    )
    session.add(
        SilverRreo(
            cod_ibge=cod, periodo=PERIODO, anexo="RREO-Anexo 03",
            conta="Receita Corrente Líquida", cod_conta="RREO3ReceitaCorrenteLiquida",
            coluna="TOTAL (ÚLTIMOS 12 MESES)", linha_seq=1,
            valor=Decimal(rcl_publicada), versao_entrega=RREO_V,
        )
    )
    # DCL do A6 = DCL do RGF.
    session.add(
        FatoResultado(
            cod_ibge=cod, periodo=PERIODO, receita_primaria=Decimal(1000),
            despesa_primaria=Decimal(900), resultado_primario=Decimal(100),
            dcl_inicio=Decimal(200), dcl_fim=Decimal(250), versao_entrega=RREO_V,
        )
    )
    session.add(
        FatoDivida(
            cod_ibge=cod, periodo=PERIODO_RGF, dc_bruta=Decimal(300),
            disponibilidades=Decimal(50), haveres=Decimal(0), dcl=Decimal(250),
            versao_entrega=RGF_V,
        )
    )
    # Pessoal: 450 / RCL 900 = 50% — igual ao mart.
    session.add(
        FatoPessoal(
            cod_ibge=cod, periodo=PERIODO_RGF, poder_codigo="ENTE.EXEC",
            despesa_bruta=Decimal(500), exclusoes=Decimal(50), despesa_liquida=Decimal(450),
            versao_entrega=RGF_V,
        )
    )
    session.add(
        MartIndicador(
            cod_ibge=cod, periodo=PERIODO, indicador="pessoal_executivo",
            valor_rs=Decimal(450), valor_pct_rcl=Decimal(50), faixa="normal",
            teto_pct=Decimal(54), denominador="rcl", base_valor=Decimal(rcl_publicada),
            versao_entrega=RREO_V, source_ref={},
        )
    )
    # Mínimo de saúde coerente: 150 / 1000 = 15%.
    session.add(
        FatoSaude(
            cod_ibge=cod, periodo=PERIODO, base_impostos_transferencias=Decimal(1000),
            despesa_bruta=Decimal(150), deducoes_outras=Decimal(0), rpnp_sem_lastro=Decimal(0),
            despesa_aplicada=Decimal(150), pct_aplicado=Decimal(15), minimo_pct=Decimal(15),
            valor_minimo=Decimal(150), abaixo_do_minimo=False,
            versao_rreo=RREO_V, versao_rgf="nao_aplicavel",
        )
    )


@pytest.fixture
def ente() -> Iterator[str]:
    cod = _codigo()
    with SessionLocal() as session:
        _seed(session, cod)
        session.commit()
    yield cod
    with SessionLocal() as session:
        for modelo in (
            DataQualityCheck, MartIndicador, FatoSaude, FatoPessoal, FatoDivida,
            FatoResultado, FatoRcl, FatoDespesa, FatoReceita, SilverRreo, DimEntrega,
            DimEnte, SilverEnte,
        ):
            session.execute(delete(modelo).where(modelo.cod_ibge.in_([cod])))
        session.commit()
    with admin_session() as session:
        session.execute(delete(Alerta).where(Alerta.cod_ibge == cod))
        session.commit()


def _headers(client, make_org, cod: str):
    org = make_org(capacidades=["ver", "administrar"], entes=[cod])
    return org, auth_header(login(client, org.email, org.senha))


# --------------------------------------------------------------------------- #
# Check 1 — soma dos filhos = pai
# --------------------------------------------------------------------------- #
def test_receita_soma_filhos_ok_falha_e_aviso(ente) -> None:
    with SessionLocal() as s:
        assert checks.receita_soma_filhos(s, ente, PERIODO, RREO_V).status == "ok"

        # falha: um filho é inflado em 300 (muito acima da tolerância de R$ 1,00)
        s.execute(
            FatoReceita.__table__.update()
            .where(FatoReceita.cod_ibge == ente, FatoReceita.origem_codigo == FILHO_A)
            .values(arrecadado_acum=Decimal(900))
        )
        s.commit()
        falha = checks.receita_soma_filhos(s, ente, PERIODO, RREO_V)
        assert falha.status == "falha"
        assert falha.diferenca == Decimal(-300)
        assert falha.detalhe["divergencias"][0]["pai"] == RAIZ

        # aviso: sem fato nenhum, o check não se aplica — não é "passou"
        s.execute(delete(FatoReceita).where(FatoReceita.cod_ibge == ente))
        s.commit()
        aviso = checks.receita_soma_filhos(s, ente, PERIODO, RREO_V)
        assert aviso.status == "aviso"
        assert aviso.detalhe["nao_aplicavel"] is True


def test_linha_de_memoria_nao_quebra_a_soma(ente) -> None:
    """'Saldo de exercícios anteriores' aparece sob receita mas não a compõe."""
    with SessionLocal() as s:
        memoria = next(iter(checks.LINHAS_MEMORIA_RECEITA))
        if s.get(DimOrigemReceita, memoria) is None:
            s.add(
                DimOrigemReceita(
                    codigo=memoria, descricao=memoria, parent_codigo=RAIZ, nivel=2, path=memoria
                )
            )
        s.add(
            FatoReceita(
                cod_ibge=ente, periodo=PERIODO, origem_codigo=memoria,
                previsto_inicial=Decimal(0), previsto_atualizado=Decimal(0),
                arrecadado_bimestre=Decimal(0), arrecadado_acum=Decimal(700),
                deducoes=Decimal(0), versao_entrega=RREO_V,
            )
        )
        s.commit()
        resultado = checks.receita_soma_filhos(s, ente, PERIODO, RREO_V)
        assert resultado.status == "ok"
        assert memoria in resultado.detalhe["linhas_de_memoria_excluidas"]
        s.execute(
            delete(FatoReceita).where(
                FatoReceita.cod_ibge == ente, FatoReceita.origem_codigo == memoria
            )
        )
        s.commit()


# --------------------------------------------------------------------------- #
# Check 2 — estágios da despesa
# --------------------------------------------------------------------------- #
def test_estagios_ok_falha_e_estagio_ausente_nao_reprova(ente) -> None:
    with SessionLocal() as s:
        assert checks.despesa_estagios(s, ente, PERIODO, RREO_V).status == "ok"

        # falha: pago maior que liquidado
        s.execute(
            FatoDespesa.__table__.update()
            .where(FatoDespesa.cod_ibge == ente)
            .values(pago=Decimal(450))
        )
        s.commit()
        falha = checks.despesa_estagios(s, ente, PERIODO, RREO_V)
        assert falha.status == "falha"
        assert "pago > liquidado" in falha.detalhe["violacoes"]

        # estágio não publicado (zero) não é violação — é escolha do demonstrativo
        s.execute(
            FatoDespesa.__table__.update()
            .where(FatoDespesa.cod_ibge == ente)
            .values(pago=Decimal(0))
        )
        s.commit()
        sem_pago = checks.despesa_estagios(s, ente, PERIODO, RREO_V)
        assert sem_pago.status == "ok"
        assert "pago" in sem_pago.detalhe["estagios_nao_publicados"]


# --------------------------------------------------------------------------- #
# Checks 3 a 7 — comparações contra a fonte e reconciliação
# --------------------------------------------------------------------------- #
def test_rcl_calculada_vs_publicada(ente) -> None:
    with SessionLocal() as s:
        assert checks.rcl_calculada_vs_publicada(s, ente, PERIODO, RREO_V).status == "ok"
        s.execute(
            FatoRcl.__table__.update()
            .where(FatoRcl.cod_ibge == ente)
            .values(rcl_12m=Decimal(950))
        )
        s.commit()
        falha = checks.rcl_calculada_vs_publicada(s, ente, PERIODO, RREO_V)
        assert falha.status == "falha" and falha.diferenca == Decimal(50)

        s.execute(delete(SilverRreo).where(SilverRreo.cod_ibge == ente))
        s.commit()
        aviso = checks.rcl_calculada_vs_publicada(s, ente, PERIODO, RREO_V)
        assert aviso.status == "aviso" and aviso.detalhe["nao_aplicavel"] is True


def test_dcl_a6_vs_rgf(ente) -> None:
    with SessionLocal() as s:
        assert checks.dcl_a6_vs_rgf(s, ente, PERIODO, PERIODO_RGF).status == "ok"
        s.execute(
            FatoDivida.__table__.update()
            .where(FatoDivida.cod_ibge == ente)
            .values(dcl=Decimal(400))
        )
        s.commit()
        assert checks.dcl_a6_vs_rgf(s, ente, PERIODO, PERIODO_RGF).status == "falha"
        assert checks.dcl_a6_vs_rgf(s, ente, PERIODO, "2000-Q1").status == "aviso"


def test_minimo_recalculado(ente) -> None:
    with SessionLocal() as s:
        assert (
            checks.minimo_recalculado_vs_materializado(s, ente, PERIODO, "saude").status == "ok"
        )
        # O percentual gravado deixa de refletir os componentes gravados.
        s.execute(
            FatoSaude.__table__.update()
            .where(FatoSaude.cod_ibge == ente)
            .values(pct_aplicado=Decimal(22))
        )
        s.commit()
        falha = checks.minimo_recalculado_vs_materializado(s, ente, PERIODO, "saude")
        assert falha.status == "falha"
        # Educação não foi materializada para este ente: não aplicável.
        aviso = checks.minimo_recalculado_vs_materializado(s, ente, PERIODO, "educacao")
        assert aviso.status == "aviso"


def test_reconciliacao_mart_vs_detalhe(ente) -> None:
    """O risco estrutural da auditoria: mart e detalhe contando histórias diferentes."""
    with SessionLocal() as s:
        assert checks.mart_vs_detalhe_pessoal(s, ente, PERIODO, PERIODO_RGF).status == "ok"
        s.execute(
            MartIndicador.__table__.update()
            .where(MartIndicador.cod_ibge == ente)
            .values(valor_pct_rcl=Decimal("47.5"))
        )
        s.commit()
        falha = checks.mart_vs_detalhe_pessoal(s, ente, PERIODO, PERIODO_RGF)
        assert falha.status == "falha"
        assert falha.esquerda == Decimal(50)  # detalhe
        assert falha.direita == Decimal("47.5")  # mart


# --------------------------------------------------------------------------- #
# Check 8 — freshness por cadência (SLA)
# --------------------------------------------------------------------------- #
def test_freshness_ok_aviso_e_falha_por_cadencia(ente) -> None:
    sla = next(s for s in checks.SLAS if s.relatorio == "RREO")
    with SessionLocal() as s:
        # Em dia: o período seguinte (B1 do ano seguinte) ainda nem venceu.
        em_dia = checks.freshness(s, sla, cod_ibge=ente, hoje=date(ANO + 1, 2, 15))
        assert em_dia.status == "ok"
        # Poucos dias além do prazo ⇒ aviso (atraso é rotina).
        aviso = checks.freshness(s, sla, cod_ibge=ente, hoje=date(ANO + 1, 4, 20))
        assert aviso.status == "aviso"
        # Meses além do prazo ⇒ falha.
        falha = checks.freshness(s, sla, cod_ibge=ente, hoje=date(ANO + 1, 9, 1))
        assert falha.status == "falha"
        assert falha.detalhe["ultimo_periodo_entregue"] == PERIODO
        # Fonte nunca ingerida: aviso com motivo, não falha.
        msc = next(s2 for s2 in checks.SLAS if s2.relatorio == "MSC")
        sem_fonte = checks.freshness(s, msc, cod_ibge=ente, hoje=date(ANO + 1, 9, 1))
        assert sem_fonte.status == "aviso"
        assert sem_fonte.detalhe["nao_aplicavel"] is True


# --------------------------------------------------------------------------- #
# Check 9 — contrato de layout e execução perdida
# --------------------------------------------------------------------------- #
def test_contrato_layout_vira_check_persistido(ente) -> None:
    with SessionLocal() as s:
        quality.registrar_contrato_layout(
            s, fonte="siconfi_rreo_minimos_pdf", cod_ibge=ente, periodo=PERIODO,
            erro="Layout RREO desconhecido: esperado 1 registro 'MDE', encontrados 0",
        )
        s.commit()
        linha = s.scalar(
            select(DataQualityCheck).where(
                DataQualityCheck.cod_ibge == ente,
                DataQualityCheck.check_codigo == "contrato_layout",
            )
        )
        assert linha is not None and linha.status == "falha"
        assert "Layout RREO desconhecido" in linha.detalhe["erro"]


def test_execucao_agendada_perdida_vira_falha() -> None:
    from app.workers import quality_tasks

    with SessionLocal() as s:
        s.execute(
            delete(DataQualityCheck).where(
                DataQualityCheck.check_codigo == "execucao_agendada"
            )
        )
        quality.registrar_execucao_perdida(
            s, fonte="verificacao_agendada",
            esperado_em=datetime.now(UTC) - timedelta(hours=3),
            detalhe="ciclo não ocorreu",
        )
        s.commit()
        linha = s.scalar(
            select(DataQualityCheck).where(
                DataQualityCheck.check_codigo == "execucao_agendada"
            )
        )
        assert linha is not None and linha.status == "falha"
        s.execute(
            delete(DataQualityCheck).where(
                DataQualityCheck.check_codigo == "execucao_agendada"
            )
        )
        s.commit()
    assert quality_tasks.INTERVALO_PADRAO_SEGUNDOS > 0


# --------------------------------------------------------------------------- #
# E2E: dado corrompido ⇒ check falha ⇒ alerta ⇒ painel
# --------------------------------------------------------------------------- #
def test_dado_corrompido_gera_check_alerta_e_aparece_no_painel(client, make_org, ente) -> None:
    org, headers = _headers(client, make_org, ente)
    with SessionLocal() as s:
        # Corrompe a RCL materializada: passa a divergir do que o ente publicou.
        s.execute(
            FatoRcl.__table__.update()
            .where(FatoRcl.cod_ibge == ente)
            .values(rcl_12m=Decimal(5000))
        )
        s.commit()
    with admin_session() as s:
        saida = quality.executar_e_alertar(s, ente, PERIODO, org_id=org.org_id)
        s.commit()
    assert "rcl_calculada_vs_publicada" in saida.codigos_falha
    assert saida.alertas_emitidos >= 1

    painel = client.get("/admin/qualidade", params={"status": "falha"}, headers=headers)
    assert painel.status_code == 200, painel.text
    corpo = painel.json()
    codigos = {i["check_codigo"] for i in corpo["itens"]}
    assert "rcl_calculada_vs_publicada" in codigos
    item = next(i for i in corpo["itens"] if i["check_codigo"] == "rcl_calculada_vs_publicada")
    # Os dois lados aparecem: quem discorda do veredito consegue refazer a conta.
    assert float(item["esquerda"]) == 5000 and float(item["direita"]) == 900
    assert corpo["resumo"]["falha"] >= 1

    fila = client.get("/alertas", params={"escopo": "ente", "ente": ente}, headers=headers)
    assert fila.status_code == 200, fila.text
    alertas = fila.json()["alertas"]
    qualidade = [a for a in alertas if a["categoria"] == "qualidade_dado"]
    assert qualidade, "falha de check tem de virar alerta"
    assert qualidade[0]["link"] == "/central-dados?painel=qualidade"
    assert qualidade[0]["severidade"] == "critico"


def test_check_nao_aplicavel_nao_vira_alerta(client, make_org, ente) -> None:
    """Aviso é convite a olhar, não alarme: só falha aciona a fila."""
    org, headers = _headers(client, make_org, ente)
    with admin_session() as s:
        resultados = quality.executar_para_ente(
            s, ente, PERIODO, hoje=date(ANO + 1, 2, 15)
        )
        quality.persistir(s, resultados)
        avisos = [r for r in resultados if r.status == "aviso"]
        emitidos = quality.emitir_alertas(s, org.org_id, avisos)
        s.commit()
    assert avisos, "o cenário precisa ter ao menos um não-aplicável"
    # emitir_alertas recebe apenas falhas no fluxo real; aqui provamos que o serviço
    # de execução não passa avisos adiante.
    with admin_session() as s:
        saida = quality.executar_e_alertar(
            s, ente, PERIODO, org_id=org.org_id, hoje=date(ANO + 1, 2, 15)
        )
        s.commit()
    assert saida.aviso >= 1
    assert saida.alertas_emitidos == saida.falha
    assert emitidos == len(avisos)  # chamada direta continua honrando o argumento


# --------------------------------------------------------------------------- #
# Lineage
# --------------------------------------------------------------------------- #
def test_lineage_seed_cobre_todas_as_paginas_do_produto() -> None:
    destinos = {a.destino for a in lineage_seed.arestas()}
    faltando = lineage_seed.PAGINAS_DO_PRODUTO - destinos
    assert not faltando, f"páginas fora do mapa de linhagem: {sorted(faltando)}"


def test_lineage_responde_nos_dois_sentidos(client, make_org, ente) -> None:
    _, headers = _headers(client, make_org, ente)
    with SessionLocal() as s:
        quality.seed_lineage(s)
        s.commit()

    # "o que quebra se silver.siconfi_rgf falhar?"
    jusante = client.get(
        "/admin/lineage", params={"no": "silver.siconfi_rgf"}, headers=headers
    )
    assert jusante.status_code == 200, jusante.text
    afetadas = set(jusante.json()["paginas_afetadas"])
    assert {"/pessoal", "/divida", "/caixa", "/limites"} <= afetadas

    # "de onde vem o número da página Pessoal?"
    montante = client.get("/admin/lineage", params={"no": "/pessoal"}, headers=headers)
    assert montante.status_code == 200, montante.text
    corpo = montante.json()
    assert corpo["fontes_de_origem"] == ["SICONFI/tt-rgf"]
    origens = {a["origem"] for a in corpo["montante"]}
    assert {"gold.fato_pessoal", "silver.siconfi_rgf", "bronze.siconfi_rgf"} <= origens


def test_lineage_recusa_no_inexistente(client, make_org, ente) -> None:
    _, headers = _headers(client, make_org, ente)
    resposta = client.get("/admin/lineage", params={"no": "gold.nao_existe"}, headers=headers)
    assert resposta.status_code == 404
    assert "não está no grafo" in resposta.json()["detail"]


def test_lineage_seed_e_idempotente() -> None:
    with SessionLocal() as s:
        primeiro = quality.seed_lineage(s)
        s.commit()
        segundo = quality.seed_lineage(s)
        s.commit()
        assert primeiro == segundo
        assert segundo == s.scalar(
            select(__import__("sqlalchemy").func.count()).select_from(LineageEdge)
        )


# --------------------------------------------------------------------------- #
# Painel e cockpit
# --------------------------------------------------------------------------- #
def test_painel_respeita_o_escopo_do_usuario(client, make_org, ente) -> None:
    org, headers = _headers(client, make_org, ente)
    with admin_session() as s:
        quality.executar_e_alertar(s, ente, PERIODO, org_id=org.org_id)
        s.commit()
    # Outra organização, sem este ente na carteira, não enxerga os checks dele.
    outra = make_org(capacidades=["ver"], entes=[])
    outros_headers = auth_header(login(client, outra.email, outra.senha))
    corpo = client.get("/admin/qualidade", headers=outros_headers).json()
    assert all(i["cod_ibge"] != ente for i in corpo["itens"])


def test_cockpit_sela_o_numero_quando_ha_check_em_falha(client, make_org, ente) -> None:
    org, headers = _headers(client, make_org, ente)
    with SessionLocal() as s:
        s.execute(
            FatoRcl.__table__.update()
            .where(FatoRcl.cod_ibge == ente)
            .values(rcl_12m=Decimal(5000))
        )
        s.commit()
    with admin_session() as s:
        quality.executar_e_alertar(s, ente, PERIODO, org_id=org.org_id)
        s.commit()
    resposta = client.get(
        f"/entes/{ente}/cockpit", params={"periodo": PERIODO}, headers=headers
    )
    assert resposta.status_code == 200, resposta.text
    qualidade = resposta.json()["qualidade"]
    assert qualidade["n_checks_falha"] >= 1
    assert qualidade["confiavel"] is False
    codigos = {c["check_codigo"] for c in qualidade["checks_abertos"]}
    assert "rcl_calculada_vs_publicada" in codigos
