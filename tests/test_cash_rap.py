"""Testes de Caixa & Restos a Pagar (Módulo 9) — Sprint 10, formato **real** do SICONFI.

Aceites: suficiência **por fonte** (nunca consolidada) com semáforo; RPNP sem lastro
identificado por fonte; art. 42 só em ano de fim de mandato; e reprodução bitemporal.
Os rótulos de coluna são os do RGF Anexo 5 (letras ``(a)…(h)`` que variam entre layouts);
o parser casa pelo prefixo descritivo.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.core.db import SessionLocal
from app.modules.cash_rap.models import FatoDisponibilidade, FatoRap
from app.modules.catalog.models import DimEnte
from app.modules.ingestion.models import DimEntrega, SilverEnte, SilverRgf, SilverRreo
from tests.conftest import auth_header, login

PERIODO = "2024-Q3"  # RGF quadrimestral (2024 = fim de mandato municipal)
PERIODO_RREO = "2024-B6"
ANEXO5 = "RGF-Anexo 05"
ANEXO7 = "RREO-Anexo 07"

BRUTA = "DISPONIBILIDADE DE CAIXA BRUTA (a)"
ANTES = (
    "DISPONIBILIDADE DE CAIXA LÍQUIDA (ANTES DA INSCRIÇÃO EM RESTOS A PAGAR NÃO "
    "PROCESSADOS DO EXERCÍCIO) (f)=(a-(b+c+d+e))"
)
RPNP = "RESTOS A PAGAR EMPENHADOS E NÃO LIQUIDADOS DO EXERCÍCIO (g)"

SAUDE = "Recursos Vinculados à Saúde"
IMPOSTOS = "Recursos Não Vinculados de Impostos"
EDUCACAO = "Recursos Vinculados à Educação"

# (linha_seq, conta, coluna, poder, valor) — Anexo 5 no formato real, 2 poderes por fonte.
# Saúde: bruta 110, antes 100, rpnp 40 ⇒ após 60 (suficiente, verde), sem lastro 0.
# Impostos: bruta 100, antes 30, rpnp 80 ⇒ após −50 (insuficiente_rpnp, amarelo), sem lastro 50.
# Educação: bruta 20, antes −5, rpnp 3 ⇒ após −8 (deficit, vermelho), sem lastro 3.
_A5: list[tuple[int, str, str, str, str]] = [
    (10, SAUDE, BRUTA, "E", "100"),
    (11, SAUDE, ANTES, "E", "90"),
    (12, SAUDE, RPNP, "E", "40"),
    (13, SAUDE, BRUTA, "L", "10"),
    (14, SAUDE, ANTES, "L", "10"),
    (20, IMPOSTOS, BRUTA, "E", "100"),
    (21, IMPOSTOS, ANTES, "E", "30"),
    (22, IMPOSTOS, RPNP, "E", "80"),
    (30, EDUCACAO, BRUTA, "E", "20"),
    (31, EDUCACAO, ANTES, "E", "-5"),
    (32, EDUCACAO, RPNP, "E", "3"),
    # Totalizador — deve ser ignorado como fonte-folha.
    (90, "TOTAL DOS RECURSOS VINCULADOS (EXCETO AO RPPS) (II)", BRUTA, "E", "130"),
]

# Anexo 7 (RP por poder) — cod_conta slug estável do STN.
_A7: list[tuple[int, str, str, str]] = [
    (10, "PODER EXECUTIVO", "RestosAPagarNaoProcessadosInscritosEmExercicioAnterior", "500"),
    (11, "PODER EXECUTIVO", "RestosAPagarNaoProcessadosPagos", "300"),
    (12, "PODER EXECUTIVO", "RestosAPagarNaoProcessadosAPagar", "180"),
    (13, "PODER EXECUTIVO", "SaldoTotal", "200"),
    (20, "PODER LEGISLATIVO", "RestosAPagarNaoProcessadosInscritosEmExercicioAnterior", "40"),
    (21, "PODER LEGISLATIVO", "SaldoTotal", "20"),
    (90, "TOTAL (III) = (I + II)", "SaldoTotal", "220"),  # ignorado (não é 'PODER ')
]


def _ente() -> str:
    return "2" + "".join(random.choices("0123456789", k=6))


@pytest.fixture
def limpar() -> Iterator[list[str]]:
    usados: list[str] = []
    yield usados
    with SessionLocal() as s:
        for cod in usados:
            s.execute(delete(FatoRap).where(FatoRap.cod_ibge == cod))
            s.execute(delete(FatoDisponibilidade).where(FatoDisponibilidade.cod_ibge == cod))
            s.execute(delete(SilverRgf).where(SilverRgf.cod_ibge == cod))
            s.execute(delete(SilverRreo).where(SilverRreo.cod_ibge == cod))
            s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
            s.execute(delete(SilverEnte).where(SilverEnte.cod_ibge == cod))
            s.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))
        s.commit()


def _seed_rgf(
    cod: str,
    *,
    linhas: list[tuple[int, str, str, str, str]] | None = None,
    versao: str = "1",
    homologada_em: datetime | None = None,
    nova_entrega: bool = True,
    periodo: str = PERIODO,
    esfera: str = "municipal",
) -> None:
    with SessionLocal() as s:
        # refresh_dim_ente conforma dim_ente a partir do silver (esfera-aware).
        s.merge(
            SilverEnte(
                cod_ibge=cod, nome="Teste", uf="CE",
                esfera="E" if esfera == "estadual" else "M",
            )
        )
        if nova_entrega:
            s.add(
                DimEntrega(
                    cod_ibge=cod, relatorio="RGF", periodo=periodo, versao_entrega=versao,
                    homologada_em=homologada_em or datetime(2025, 1, 10, tzinfo=UTC), vigente=True,
                )
            )
        for seq, conta, coluna, poder, valor in linhas or _A5:
            s.add(
                SilverRgf(
                    cod_ibge=cod, periodo=periodo, anexo=ANEXO5, conta=conta, cod_conta=None,
                    coluna=coluna, poder=poder, linha_seq=seq, valor=Decimal(valor),
                    versao_entrega=versao,
                )
            )
        s.commit()


def _seed_rreo_a7(cod: str, *, versao: str = "1") -> None:
    with SessionLocal() as s:
        s.add(
            DimEntrega(
                cod_ibge=cod, relatorio="RREO", periodo=PERIODO_RREO, versao_entrega=versao,
                homologada_em=datetime(2025, 1, 10, tzinfo=UTC), vigente=True,
            )
        )
        for seq, conta, cod_conta, valor in _A7:
            s.add(
                SilverRreo(
                    cod_ibge=cod, periodo=PERIODO_RREO, anexo=ANEXO7, conta=conta,
                    cod_conta=cod_conta, coluna=cod_conta, linha_seq=seq, valor=Decimal(valor),
                    versao_entrega=versao,
                )
            )
        s.commit()


def _auth(client, make_org, cod: str, caps=("ver",)):
    fx = make_org(capacidades=list(caps), entes=[cod])
    return auth_header(login(client, fx.email, fx.senha))


# --- suficiência por fonte (nunca consolidada) + semáforo ---
def test_suficiencia_por_fonte_semaforo(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_rgf(cod)
    h = _auth(client, make_org, cod)

    suf = client.get(
        f"/entes/{cod}/caixa/suficiencia", params={"periodo": PERIODO}, headers=h
    ).json()
    itens = {i["descricao"]: i for i in suf["itens"]}
    # Poderes somados por fonte: saúde bruta = 100 (E) + 10 (L) = 110.
    assert float(itens[SAUDE]["disp_bruta"]) == 110.0
    assert float(itens[SAUDE]["disp_liquida_apos"]) == 60.0  # 100 − 40
    assert itens[SAUDE]["status"] == "suficiente"
    assert itens[SAUDE]["semaforo"] == "verde"
    # Impostos: cobre obrigações (antes 30) mas não todo o RPNP (80) ⇒ amarelo.
    assert itens[IMPOSTOS]["status"] == "insuficiente_rpnp"
    assert itens[IMPOSTOS]["semaforo"] == "amarelo"
    assert float(itens[IMPOSTOS]["disp_liquida_apos"]) == -50.0
    assert itens[IMPOSTOS]["vinculada"] is False
    # Educação: não cobre nem as obrigações correntes (antes −5) ⇒ vermelho.
    assert itens[EDUCACAO]["status"] == "deficit"
    assert itens[EDUCACAO]["semaforo"] == "vermelho"
    # Totalizador não vira fonte-folha.
    assert len(suf["itens"]) == 3
    # Resumo: nunca consolidado — o superávit da saúde NÃO compensa o déficit dos demais.
    r = suf["resumo"]
    assert r["n_fontes"] == 3 and r["n_suficientes"] == 1 and r["n_insuficientes"] == 2
    assert r["n_deficit"] == 1
    assert float(r["total_rpnp_sem_lastro"]) == 53.0  # 50 (impostos) + 3 (educação)
    assert suf["source_ref"]["anexo"] == "Anexo 05"
    assert "fonte a fonte" in suf["observacao"]


def test_nao_consolidada_deficit_nao_e_compensado(client, make_org, limpar) -> None:
    """Mesmo com folga total positiva, cada fonte deficitária permanece sinalizada."""
    cod = _ente()
    limpar.append(cod)
    _seed_rgf(cod)
    h = _auth(client, make_org, cod)
    suf = client.get(
        f"/entes/{cod}/caixa/suficiencia", params={"periodo": PERIODO}, headers=h
    ).json()
    apos_total = sum(float(i["disp_liquida_apos"]) for i in suf["itens"])
    assert apos_total == 2.0  # 60 − 50 − 8 (folga agregada positiva)…
    # …mas ainda há 2 fontes insuficientes (a análise não é consolidada).
    assert suf["resumo"]["n_insuficientes"] == 2


# --- RPNP sem lastro por fonte ---
def test_rpnp_sem_lastro_por_fonte(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_rgf(cod)
    h = _auth(client, make_org, cod)

    rp = client.get(
        f"/entes/{cod}/caixa/rpnp-sem-lastro", params={"periodo": PERIODO}, headers=h
    ).json()
    itens = {i["descricao"]: i for i in rp["itens"]}
    # Só as fontes com RPNP sem lastro > 0 aparecem (saúde, suficiente, fica de fora).
    assert set(itens) == {IMPOSTOS, EDUCACAO}
    assert float(itens[IMPOSTOS]["rpnp_sem_lastro"]) == 50.0  # 80 − 30
    assert float(itens[EDUCACAO]["rpnp_sem_lastro"]) == 3.0  # 3 − max(0, −5)
    assert float(rp["total_rpnp_sem_lastro"]) == 53.0
    assert float(rp["total_vinculada"]) == 3.0  # educação (vinculada)
    assert float(rp["total_nao_vinculada"]) == 50.0  # impostos (não vinculada)


# --- art. 42: só em fim de mandato ---
def test_art42_visivel_em_fim_de_mandato(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_rgf(cod)  # 2024 = fim de mandato municipal; ente sem esfera ⇒ municipal
    h = _auth(client, make_org, cod)

    a = client.get(f"/entes/{cod}/caixa/art42", params={"periodo": PERIODO}, headers=h).json()
    assert a["aplicavel"] is True
    assert a["ano"] == 2024 and a["quadrimestre"] == 3
    assert a["janela_vedacao"] is True  # Q3 ∈ 2 últimos quadrimestres
    assert a["atende"] is False and a["n_descumprimentos"] == 2
    # Lacuna inclui o déficit de obrigações da educação (8), além do RPNP sem lastro.
    assert float(a["total_lacuna"]) == 58.0  # 50 (impostos) + 8 (educação)
    assert len(a["fontes"]) == 3


def test_art42_nao_aplicavel_fora_de_mandato(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_rgf(cod, periodo="2023-Q3")  # 2023 não é fim de mandato
    h = _auth(client, make_org, cod)

    a = client.get(
        f"/entes/{cod}/caixa/art42", params={"periodo": "2023-Q3"}, headers=h
    ).json()
    assert a["aplicavel"] is False
    assert a["fontes"] == []
    assert "não é fim de mandato" in a["observacao"]


def test_art42_esfera_aware_estadual(client, make_org, limpar) -> None:
    """Mesmo ano (2024): fim de mandato municipal, mas não estadual (ano % 4 == 2)."""
    cod = _ente()
    limpar.append(cod)
    _seed_rgf(cod, esfera="estadual")
    h = _auth(client, make_org, cod)

    a = client.get(f"/entes/{cod}/caixa/art42", params={"periodo": PERIODO}, headers=h).json()
    assert a["esfera"] == "estadual"
    assert a["aplicavel"] is False  # 2024 não é fim de mandato estadual


# --- detalhe (suficiência + RP por poder) ---
def test_detalhe_suficiencia_e_rap(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_rgf(cod)
    _seed_rreo_a7(cod)
    h = _auth(client, make_org, cod)

    det = client.get(f"/entes/{cod}/caixa", params={"periodo": PERIODO}, headers=h).json()
    assert det["periodo_rreo"] == PERIODO_RREO
    assert det["art42_aplicavel"] is True
    assert det["resumo"]["n_insuficientes"] == 2
    # Fontes críticas ordenadas por RPNP sem lastro (impostos 50 antes de educação 3).
    assert [c["descricao"] for c in det["fontes_criticas"]] == [IMPOSTOS, EDUCACAO]
    # RP por poder (RREO Anexo 7) + consolidado.
    orgaos = {r["orgao"]: r for r in det["rap_por_orgao"]}
    assert set(orgaos) == {"PODER EXECUTIVO", "PODER LEGISLATIVO"}
    assert float(det["rap_consolidado"]["saldo_total"]) == 220.0  # 200 + 20
    assert det["source_ref_rap"]["anexo"] == "Anexo 07"
    assert [b["codigo"] for b in det["periodo_breadcrumb"]] == ["2024"]


# --- árvore por fonte (drill §6.1) ---
def test_arvore_por_fonte(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_rgf(cod)
    h = _auth(client, make_org, cod)

    # Raiz da hierarquia (node vazio) ⇒ Total dos recursos.
    raiz = client.get(f"/entes/{cod}/caixa/arvore", params={"periodo": PERIODO}, headers=h).json()
    assert [c["codigo"] for c in raiz["children"]] == ["TOTAL_RECURSOS"]
    # Drill DOWN no total ⇒ grupos de vinculação.
    total = client.get(
        f"/entes/{cod}/caixa/arvore",
        params={"periodo": PERIODO, "node": "TOTAL_RECURSOS"}, headers=h,
    ).json()
    grupos = {c["codigo"] for c in total["children"]}
    assert grupos == {"NAO_VINCULADOS", "VINCULADOS"}
    assert total["source_ref"]["relatorio"] == "RGF"


# --- memória rastreável ---
def test_memoria_rastreavel(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_rgf(cod)
    h = _auth(client, make_org, cod)

    mem = client.get(f"/entes/{cod}/caixa/memoria", params={"periodo": PERIODO}, headers=h).json()
    assert float(mem["total_rpnp_sem_lastro"]) == 53.0
    assert "max(0, rpnp_exercicio" in mem["formula_rpnp_sem_lastro"]
    assert len(mem["fontes"]) == 3


# --- bitemporal (as_of) ---
def test_as_of_reproduz_versao_anterior(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_rgf(cod)  # v1
    with SessionLocal() as s:
        s.execute(
            DimEntrega.__table__.update()
            .where(DimEntrega.cod_ibge == cod, DimEntrega.versao_entrega == "1")
            .values(vigente=False)
        )
        s.commit()
    # v2 (retificação): impostos passa a ter caixa suficiente para o RPNP.
    _seed_rgf(
        cod,
        versao="2",
        homologada_em=datetime(2025, 3, 1, tzinfo=UTC),
        linhas=[
            (20, IMPOSTOS, BRUTA, "E", "200"),
            (21, IMPOSTOS, ANTES, "E", "200"),
            (22, IMPOSTOS, RPNP, "E", "80"),
        ],
    )
    h = _auth(client, make_org, cod)

    vig = client.get(
        f"/entes/{cod}/caixa/suficiencia", params={"periodo": PERIODO}, headers=h
    ).json()
    assert vig["versao_entrega"] == "2"
    assert vig["resumo"]["n_insuficientes"] == 0  # retificação sanou o sem-lastro

    hist = client.get(
        f"/entes/{cod}/caixa/suficiencia",
        params={"periodo": PERIODO, "as_of": "2025-02-01T00:00:00Z"}, headers=h,
    ).json()
    assert hist["versao_entrega"] == "1"
    assert hist["resumo"]["n_insuficientes"] == 2  # v1: impostos (amarelo) + educação (vermelho)


# --- escopo multi-tenant ---
def test_escopo_403_fora_da_carteira(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_rgf(cod)
    fx = make_org(capacidades=["ver"], entes=[])  # carteira vazia
    h = auth_header(login(client, fx.email, fx.senha))

    resp = client.get(f"/entes/{cod}/caixa/suficiencia", params={"periodo": PERIODO}, headers=h)
    assert resp.status_code == 403
    assert resp.json()["title"] == "Ente fora do escopo"
