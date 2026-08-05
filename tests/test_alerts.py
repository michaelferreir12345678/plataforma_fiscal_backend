"""Testes de Alertas & Conformidade (Módulo 14) — Sprint 15.

Fixtures usam um ano sintético (2090) e um ente 8xxxxxx para isolar do dado real.
Seedamos entregas em ``gold.dim_entrega`` (RREO/RGF/DCA + fontes complementares),
``mart_indicador`` (limite excedido) e ``fato_rcl`` para exercitar todos os ramos do
motor: publicação, calendário por porte, limite, defasagem e agregação de carteira.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.core.db import SessionLocal, admin_session
from app.modules.alerts import service as alerts_service
from app.modules.alerts.models import Alerta, CalendarioObrigacao
from app.modules.catalog.models import DimEnte
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import DimEntrega, SilverEnte
from tests.conftest import auth_header, login

ANO = 2090
ANCORA_RREO = f"{ANO}-B6"


def _ente() -> str:
    return "8" + "".join(random.choices("0123456789", k=6))


@dataclass(frozen=True)
class AlertCase:
    cod_ibge: str
    populacao: int


def _entrega(s, cod: str, relatorio: str, periodo: str, hom: datetime, versao: str = "v1") -> None:
    s.add(
        DimEntrega(
            cod_ibge=cod, relatorio=relatorio, periodo=periodo,
            versao_entrega=versao, homologada_em=hom, vigente=True,
        )
    )


def _seed(case: AlertCase, *, com_siops_defasado: bool) -> None:
    cod = case.cod_ibge
    hom = datetime(ANO + 1, 2, 28, tzinfo=UTC)
    with SessionLocal() as s:
        s.merge(SilverEnte(cod_ibge=cod, nome=f"Ente {cod}", uf="CE", esfera="M"))
        # RREO bimestral B1..B6 (âncora = B6).
        for b in range(1, 7):
            _entrega(s, cod, "RREO", f"{ANO}-B{b}", hom)
            s.add(
                FatoRcl(
                    cod_ibge=cod, periodo_ref=f"{ANO}-B{b}", rcl_12m=Decimal("1000000"),
                    receita_corrente=Decimal("1100000"), versao_entrega="v1", memoria={},
                )
            )
        # RGF quadrimestral Q1..Q3 e DCA do ano anterior.
        for q in range(1, 4):
            _entrega(s, cod, "RGF", f"{ANO}-Q{q}", hom)
        _entrega(s, cod, "DCA", str(ANO - 1), hom)

        # Fontes complementares alinhadas ao RREO (em dia)…
        _entrega(s, cod, "SIOPE", ANCORA_RREO, hom)
        # …SIOPS deliberadamente atrasado (B4 << B6) para provar a defasagem.
        siops_periodo = f"{ANO}-B4" if com_siops_defasado else ANCORA_RREO
        _entrega(s, cod, "SIOPS", siops_periodo, hom)

        # Limite excedido de pessoal (mart_indicador) na âncora.
        s.add(
            MartIndicador(
                cod_ibge=cod, periodo=ANCORA_RREO, indicador="pessoal_executivo",
                valor_rs=Decimal("560000"), valor_pct_rcl=Decimal("56.0"),
                faixa="excedido", teto_pct=Decimal("54"), versao_entrega="v1",
                source_ref={"relatorio": "RREO"},
            )
        )
        s.commit()


def _cleanup(cod: str) -> None:
    with admin_session() as s:
        s.execute(delete(Alerta).where(Alerta.cod_ibge == cod))
        s.execute(delete(CalendarioObrigacao).where(CalendarioObrigacao.cod_ibge == cod))
        s.execute(delete(MartIndicador).where(MartIndicador.cod_ibge == cod))
        s.execute(delete(FatoRcl).where(FatoRcl.cod_ibge == cod))
        s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
        s.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))
        s.execute(delete(SilverEnte).where(SilverEnte.cod_ibge == cod))
        s.commit()


@pytest.fixture
def alert_case() -> Iterator[AlertCase]:
    case = AlertCase(cod_ibge=_ente(), populacao=270000)  # > 50 mil → RGF quadrimestral
    _seed(case, com_siops_defasado=True)
    yield case
    _cleanup(case.cod_ibge)


def _token(client, make_org, cod_ibge: str, *, caps=("ver", "config_alerta")) -> str:
    org = make_org(capacidades=list(caps), entes=[cod_ibge])
    return login(client, org.email, org.senha)


def _fila_ente(client, cod: str, token: str):
    return client.get(
        "/alertas", params={"escopo": "ente", "ente": cod}, headers=auth_header(token)
    )


def test_fila_priorizada_e_ingredientes_obrigatorios(
    client, make_org, alert_case: AlertCase
) -> None:
    """Aceite: fila priorizada; cada alerta com motivo legal + ação + prazo/link."""
    token = _token(client, make_org, alert_case.cod_ibge)
    resp = _fila_ente(client, alert_case.cod_ibge, token)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    alertas = body["alertas"]
    assert alertas, "esperava alertas materializados"
    # Prioridade não-decrescente (crítico → atenção → informativo).
    prioridades = [a["prioridade"] for a in alertas]
    assert prioridades == sorted(prioridades)
    # Ingredientes obrigatórios em todos.
    for a in alertas:
        assert a["motivo_legal"] and a["acao_sugerida"] and a["link"]
    # O limite excedido é crítico e encabeça a fila.
    assert alertas[0]["severidade"] == "critico"
    assert alertas[0]["categoria"] == "limite"
    assert body["contadores"]["critico"] >= 1


def test_alerta_defasagem_de_fonte(client, make_org, alert_case: AlertCase) -> None:
    """Aceite: alerta de defasagem aparece quando a fonte está atrás da âncora."""
    token = _token(client, make_org, alert_case.cod_ibge)
    body = _fila_ente(client, alert_case.cod_ibge, token).json()
    defas = [a for a in body["alertas"] if a["categoria"] == "defasagem_fonte"]
    assert defas, "esperava alerta de defasagem (SIOPS atrasado)"
    siops = next(a for a in defas if "SIOPS" in a["titulo"])
    assert siops["memoria"]["atraso"] == 2  # B6 − B4 = 2 bimestres
    # SIOPE está em dia → não deve gerar defasagem.
    assert not any("SIOPE" in a["titulo"] for a in defas)


def test_alerta_nova_publicacao(client, make_org, alert_case: AlertCase) -> None:
    """Aceite: nova entrega homologada gera alerta informativo de publicação."""
    token = _token(client, make_org, alert_case.cod_ibge)
    resp = _fila_ente(client, alert_case.cod_ibge, token)
    pubs = [a for a in resp.json()["alertas"] if a["categoria"] == "publicacao"]
    titulos = {a["titulo"] for a in pubs}
    assert any("RREO 6º bimestre" in t and "publicado" in t for t in titulos)
    assert all(a["severidade"] == "informativo" for a in pubs)


def test_calendario_por_porte(client, make_org, alert_case: AlertCase) -> None:
    """Aceite: calendário usa periodicidade por porte; publicação marca Entregue."""
    token = _token(client, make_org, alert_case.cod_ibge)
    resp = client.get(f"/entes/{alert_case.cod_ibge}/calendario", headers=auth_header(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["periodicidade_rgf"] == "quadrimestral"  # > 50 mil hab.
    rgf = [i for i in body["itens"] if i["relatorio"] == "RGF"]
    assert len(rgf) == 3  # quadrimestral: 3 entregas/ano
    assert all(i["status"] == "entregue" for i in rgf)  # todas homologadas no seed
    dca = [i for i in body["itens"] if i["relatorio"] == "DCA"]
    assert dca and dca[0]["status"] == "pendente"  # DCA do exercício ainda não entregue


def test_calendario_municipio_pequeno_tem_rgf_semestral(client, make_org) -> None:
    """Porte < 50 mil hab.: RGF semestral (menos obrigações no ano)."""
    cod = _ente()
    case = AlertCase(cod_ibge=cod, populacao=12000)
    _seed(case, com_siops_defasado=False)
    # Fixa a população pequena no dim_ente conformado.
    with admin_session() as s:
        from app.modules.catalog import service as catalog_service
        catalog_service.refresh_dim_ente(s, cod)
        s.execute(
            DimEnte.__table__.update().where(DimEnte.cod_ibge == cod).values(populacao=12000)
        )
        s.commit()
    try:
        token = _token(client, make_org, cod)
        body = client.get(f"/entes/{cod}/calendario", headers=auth_header(token)).json()
        assert body["periodicidade_rgf"] == "semestral"
        rgf = [i for i in body["itens"] if i["relatorio"] == "RGF"]
        assert len(rgf) == 2  # semestral: 2 entregas/ano (< quadrimestral)
    finally:
        _cleanup(cod)


def test_carteira_agrega_e_destaca_ente(client, make_org, alert_case: AlertCase) -> None:
    """Aceite: agregados no nível carteira, com rollup por ente e por categoria."""
    token = _token(client, make_org, alert_case.cod_ibge)
    resp = client.get("/carteira/alertas", headers=auth_header(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_entes"] >= 1
    assert body["contadores"]["total"] >= 1
    assert any(c["categoria"] == "limite" for c in body["por_categoria"])
    ente = next(e for e in body["por_ente"] if e["cod_ibge"] == alert_case.cod_ibge)
    assert ente["pior_severidade"] == "critico"
    assert body["top_alertas"][0]["severidade"] == "critico"


def test_patch_status_preservado_na_reavaliacao(client, make_org, alert_case: AlertCase) -> None:
    """PATCH muda o status; reavaliar (idempotente) não o rebaixa nem duplica."""
    token = _token(client, make_org, alert_case.cod_ibge)
    fila = _fila_ente(client, alert_case.cod_ibge, token).json()
    alvo = fila["alertas"][0]
    patch = client.patch(
        f"/alertas/{alvo['id']}", json={"status": "reconhecida"}, headers=auth_header(token)
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["status"] == "reconhecida"

    # Reavalia (nova leitura) — o status tratado é preservado e não há duplicação.
    fila2 = _fila_ente(client, alert_case.cod_ibge, token).json()
    reencontrado = next(a for a in fila2["alertas"] if a["id"] == alvo["id"])
    assert reencontrado["status"] == "reconhecida"
    ids = [a["id"] for a in fila2["alertas"]]
    assert len(ids) == len(set(ids))  # sem duplicatas


def test_retificacao_que_resolve_fecha_o_alerta_orfao(
    client, make_org, alert_case: AlertCase
) -> None:
    """A21 (Sprint A5): retificação que corrige o indicador para dentro do limite fecha
    o alerta antigo — antes, ``_alertas_limite`` só gravava quando a faixa continuava
    não-nula e nunca "revisitava" o que ficou órfão. Mesma família de A14/A15: versão
    nova chega, vigência não se propaga.
    """
    cod = alert_case.cod_ibge
    token = _token(client, make_org, cod)
    fila = _fila_ente(client, cod, token).json()
    alerta_limite = next(a for a in fila["alertas"] if a["categoria"] == "limite")
    assert alerta_limite["status"] == "nova"
    assert alerta_limite["indicador"] == "pessoal_executivo"

    # Retificação: nova entrega RREO (versão v2), vigente, com pessoal dentro do limite
    # (o seed original tinha 56% > teto de 54%; a retificação traz 40%).
    with SessionLocal() as s:
        s.execute(
            DimEntrega.__table__.update()
            .where(
                DimEntrega.cod_ibge == cod,
                DimEntrega.relatorio == "RREO",
                DimEntrega.periodo == ANCORA_RREO,
            )
            .values(vigente=False)
        )
        s.add(
            DimEntrega(
                cod_ibge=cod, relatorio="RREO", periodo=ANCORA_RREO, versao_entrega="v2",
                homologada_em=datetime(ANO + 1, 3, 15, tzinfo=UTC), vigente=True,
            )
        )
        s.add(
            MartIndicador(
                cod_ibge=cod, periodo=ANCORA_RREO, indicador="pessoal_executivo",
                valor_rs=Decimal("400000"), valor_pct_rcl=Decimal("40.0"),
                faixa="normal", teto_pct=Decimal("54"), versao_entrega="v2",
                source_ref={"relatorio": "RREO"},
            )
        )
        s.commit()

    # A avaliação on-read tem um TTL (30s) — sem esquecer, a reavaliação nem rodaria.
    alerts_service.esquecer_avaliacoes()
    fila2 = _fila_ente(client, cod, token).json()
    assert not any(a["id"] == alerta_limite["id"] for a in fila2["alertas"]), (
        "o alerta órfão continua na fila ativa depois da retificação resolver"
    )

    with admin_session() as s:
        atualizado = s.get(Alerta, uuid.UUID(alerta_limite["id"]))
        assert atualizado is not None
        assert atualizado.status == "resolvida"
        assert atualizado.resolvido_por is None, (
            "ninguém decidiu — foi o dado que mudou (mesma regra de set_status)"
        )
        assert atualizado.resolvido_em is not None


def test_alerta_ja_tratado_pelo_gestor_nao_e_reaberto_nem_retocado(
    client, make_org, alert_case: AlertCase
) -> None:
    """Um alerta que o gestor já descartou continua descartado mesmo que a faixa
    volte a ficar ruim de novo depois — fechar órfão não pode reabrir histórico tratado."""
    cod = alert_case.cod_ibge
    token = _token(client, make_org, cod)
    fila = _fila_ente(client, cod, token).json()
    alerta_limite = next(a for a in fila["alertas"] if a["categoria"] == "limite")
    client.patch(
        f"/alertas/{alerta_limite['id']}",
        json={"status": "descartada"},
        headers=auth_header(token),
    )

    chave = f"limite:{alerta_limite['indicador']}:{alerta_limite['periodo']}"
    with admin_session() as s:
        from app.modules.alerts import repository as alerts_repo

        org_id = s.get(Alerta, uuid.UUID(alerta_limite["id"])).org_id
        fechou = alerts_repo.fechar_alerta_orfao(
            s, org_id=org_id, chave=chave, agora=datetime.now(UTC),
        )
        s.commit()
    assert fechou is False, "alerta descartado não é tocado por fechar_alerta_orfao"

    with admin_session() as s:
        atual = s.get(Alerta, uuid.UUID(alerta_limite["id"]))
        assert atual.status == "descartada"


def test_alertas_403_fora_da_carteira(client, make_org) -> None:
    cod = _ente()
    org = make_org(capacidades=["ver"], entes=[])
    token = login(client, org.email, org.senha)
    resp = _fila_ente(client, cod, token)
    assert resp.status_code == 403
