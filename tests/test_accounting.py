"""Testes de Patrimônio (DCA) & Explorador MSC (Módulo 11) — Sprint 12.

Aceites: árvore **lazy** (só filhos diretos, com ``has_children``); **rollup** correto
(pai = Σ filhos); **conciliação** sinaliza divergência (Ativo=Passivo, MSC↔DCA, identidade
do encerramento); consulta de conta **< 300 ms** no seed.

Os dados replicam a forma **real** do SICONFI: a MSC traz o saldo por conta contábil (9
dígitos) já na convenção **devedor líquido** (débito +, crédito −, como o conector grava no
silver); a DCA traz o Balanço Patrimonial (Anexo I-AB) com ``cod_conta`` PCASP ``P1.1.…``.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.core.db import SessionLocal
from app.modules.accounting.models import FatoBalanco, FatoMscSaldo, MartMscRollup
from app.modules.catalog.models import DimEnte
from app.modules.ingestion.models import DimEntrega, SilverDca, SilverEnte, SilverMsc
from tests.conftest import auth_header, login

ANO = 2022
M11 = f"{ANO}-M11"
M12 = f"{ANO}-M12"

# Folhas da MSC (código de 9 dígitos, saldo em convenção devedor líquido).
#   Ativo(1)=200  →  1.1.1.1.1.01(100)+.02(60)=160 sob 1.1; 1.2.3.1.1.01(40) sob 1.2
#   Passivo+PL(2)=−150 (natural 150)  ·  VPD(3)=80  ·  VPA(4)=−130 (natural 130)
#   Resultado = VPA−VPD = 50  →  DCA Passivo+PL = 150 + 50 = 200 = Ativo (fecha).
_MSC_M12: list[tuple[str, str, str]] = [
    ("111110100", "D", "100"),  # Caixa
    ("111110200", "D", "60"),   # Bancos
    ("123110100", "D", "40"),   # Imobilizado (1.2.3.1.1.01.00)
    ("211110100", "C", "-120"), # Fornecedores
    ("237110100", "C", "-30"),  # Patrimônio Líquido (2.3.7.1.1.01.00)
    ("311110100", "D", "80"),   # VPD
    ("411110100", "C", "-130"), # VPA
]
# Mês anterior (M11): Ativo menor (Caixa 90) — para a matriz mensal ter 2 pontos distintos.
_MSC_M11 = [("111110100", "D", "90")] + _MSC_M12[1:]

# DCA Anexo I-AB (Balanço Patrimonial), valores naturais reportados (pós-encerramento).
_DCA_IAB: list[tuple[str, str, str]] = [
    ("P1.0.0.0.0.00.00", "1.0.0.0.0.00.00 - Ativo", "200"),
    ("P1.1.0.0.0.00.00", "1.1.0.0.0.00.00 - Ativo Circulante", "160"),
    ("P1.2.0.0.0.00.00", "1.2.0.0.0.00.00 - Ativo Não Circulante", "40"),
    ("P2.0.0.0.0.00.00", "2.0.0.0.0.00.00 - Passivo e Patrimônio Líquido", "200"),
    ("P2.3.0.0.0.00.00", "2.3.0.0.0.00.00 - Patrimônio Líquido", "50"),
    # Quadro suplementar Lei 4.320 (Financeiro) — deve ser IGNORADO (sufixo F).
    ("P1.0.0.0.0.00.00F", "1.0.0.0.0.00.00 - Ativo Financeiro", "70"),
]


def _ente() -> str:
    # Prefixo 99 (inexistente no IBGE) garante que a limpeza nunca toque um ente real
    # (Fortaleza 2304400 / São Paulo 3550308). A UF é semeada explicitamente (CE) no silver.
    import random

    return "99" + "".join(random.choices("0123456789", k=5))


@pytest.fixture
def limpar() -> Iterator[list[str]]:
    usados: list[str] = []
    yield usados
    with SessionLocal() as s:
        for cod in usados:
            s.execute(delete(FatoMscSaldo).where(FatoMscSaldo.cod_ibge == cod))
            s.execute(delete(MartMscRollup).where(MartMscRollup.cod_ibge == cod))
            s.execute(delete(FatoBalanco).where(FatoBalanco.cod_ibge == cod))
            s.execute(delete(SilverMsc).where(SilverMsc.cod_ibge == cod))
            s.execute(delete(SilverDca).where(SilverDca.cod_ibge == cod))
            s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
            s.execute(delete(SilverEnte).where(SilverEnte.cod_ibge == cod))
            s.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))
        s.commit()


def _seed(cod: str, *, com_dca: bool = True, com_msc: bool = True, dca_iab=None) -> None:
    with SessionLocal() as s:
        s.merge(SilverEnte(cod_ibge=cod, nome="Município Teste", uf="CE", esfera="M"))
        if com_msc:
            for periodo, linhas in ((M11, _MSC_M11), (M12, _MSC_M12)):
                s.add(
                    DimEntrega(
                        cod_ibge=cod, relatorio="MSC", periodo=periodo, versao_entrega="1",
                        homologada_em=datetime(2023, 2, 1, tzinfo=UTC), vigente=True,
                    )
                )
                for conta, natureza, valor in linhas:
                    s.add(
                        SilverMsc(
                            cod_ibge=cod, periodo=periodo, cod_conta_pcasp=conta,
                            natureza=natureza, saldo_inicial=Decimal(0),
                            saldo_final=Decimal(valor), versao_entrega="1",
                        )
                    )
        if com_dca:
            s.add(
                DimEntrega(
                    cod_ibge=cod, relatorio="DCA", periodo=str(ANO), versao_entrega="1",
                    homologada_em=datetime(2023, 6, 1, tzinfo=UTC), vigente=True,
                )
            )
            for seq, (cod_conta, conta, valor) in enumerate(dca_iab or _DCA_IAB):
                s.add(
                    SilverDca(
                        cod_ibge=cod, periodo=str(ANO), anexo="DCA-Anexo I-AB",
                        conta=conta, cod_conta=cod_conta, coluna="31/12/2022",
                        linha_seq=seq, valor=Decimal(valor), versao_entrega="1",
                    )
                )
        s.commit()


def _auth(client, make_org, cod: str, caps=("ver",)):
    fx = make_org(capacidades=list(caps), entes=[cod])
    return auth_header(login(client, fx.email, fx.senha))


# --- árvore lazy + rollup (pai = Σ filhos) ---
def test_arvore_lazy_e_rollup(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod)
    h = _auth(client, make_org, cod)

    # Raízes: as 4 classes patrimoniais, cada uma com o saldo natural rolled-up.
    raiz = client.get(f"/entes/{cod}/msc/arvore", params={"periodo": M12}, headers=h).json()
    por_cod = {c["codigo"]: c for c in raiz["children"]}
    assert set(por_cod) == {
        "1.0.0.0.0.00.00", "2.0.0.0.0.00.00", "3.0.0.0.0.00.00", "4.0.0.0.0.00.00"
    }
    assert float(por_cod["1.0.0.0.0.00.00"]["measures"]["saldo"]) == 200.0  # Ativo
    assert float(por_cod["2.0.0.0.0.00.00"]["measures"]["saldo"]) == 150.0  # Passivo+PL (natural)
    assert por_cod["1.0.0.0.0.00.00"]["has_children"] is True
    assert raiz["source_ref"]["relatorio"] == "MSC"

    # Drill DOWN no Ativo: LAZY — só os filhos DIRETOS (1.1 e 1.2), não as folhas.
    ativo = client.get(
        f"/entes/{cod}/msc/arvore", params={"periodo": M12, "node": "1.0.0.0.0.00.00"}, headers=h
    ).json()
    filhos = {c["codigo"]: c for c in ativo["children"]}
    assert set(filhos) == {"1.1.0.0.0.00.00", "1.2.0.0.0.00.00"}
    # Rollup: pai (200) = soma dos filhos (160 + 40).
    assert ativo["node"]["codigo"] == "1.0.0.0.0.00.00"
    assert float(ativo["measures"]["saldo"]) == 200.0  # agregado do próprio node (§6.1)
    soma = sum(float(c["measures"]["saldo"]) for c in ativo["children"])
    assert soma == 200.0
    assert float(filhos["1.1.0.0.0.00.00"]["measures"]["saldo"]) == 160.0
    assert filhos["1.1.0.0.0.00.00"]["has_children"] is True

    # Drill mais fundo: 1.1.1.1.1 → folhas (100 + 60 = 160).
    n5 = client.get(
        f"/entes/{cod}/msc/arvore",
        params={"periodo": M12, "node": "1.1.1.1.1.00.00"}, headers=h,
    ).json()
    folhas = {c["codigo"]: float(c["measures"]["saldo"]) for c in n5["children"]}
    assert folhas == {"1.1.1.1.1.01.00": 100.0, "1.1.1.1.1.02.00": 60.0}
    assert all(not c["has_children"] for c in n5["children"])  # folhas
    # Breadcrumb (drill UP) reconstrói os ancestrais até a classe.
    assert [b["codigo"] for b in n5["breadcrumb"]] == [
        "1.0.0.0.0.00.00", "1.1.0.0.0.00.00", "1.1.1.0.0.00.00", "1.1.1.1.0.00.00"
    ]


# --- matriz mensal + desempenho (< 300 ms warm) ---
def test_matriz_mensal_e_desempenho(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod)
    h = _auth(client, make_org, cod)

    # Primeira chamada materializa (silver → gold); as demais leem a gold pronta.
    r0 = client.get(
        f"/entes/{cod}/msc/conta/1.0.0.0.0.00.00/saldos", params={"ano": ANO}, headers=h
    )
    assert r0.status_code == 200, r0.text
    mat = r0.json()
    meses = {m["periodo"]: m for m in mat["meses"]}
    assert set(meses) == {M11, M12}
    assert float(meses[M11]["saldo_final"]) == 190.0  # Caixa 90 no M11
    assert float(meses[M12]["saldo_final"]) == 200.0
    assert mat["classe"] == 1 and mat["natureza"] == "D"

    # Consulta de conta warm < 300 ms (aceite).
    t = time.perf_counter()
    resp = client.get(
        f"/entes/{cod}/msc/conta/1.1.1.1.1.01.00/saldos", params={"ano": ANO}, headers=h
    )
    dt = time.perf_counter() - t
    assert resp.status_code == 200
    assert dt < 0.3, f"consulta de conta levou {dt * 1000:.0f}ms (> 300ms)"
    assert float(resp.json()["meses"][-1]["saldo_final"]) == 100.0  # Caixa dez


# --- conciliação: rollup + Ativo=Passivo + MSC↔DCA + identidade do encerramento ---
def test_conciliacao_reconciliada(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod)
    h = _auth(client, make_org, cod)

    conc = client.get(f"/entes/{cod}/msc/conciliacao", params={"ano": ANO}, headers=h).json()
    checks = {c["chave"]: c for c in conc["checks"]}
    assert set(checks) >= {"rollup_msc", "balanco_fecha", "msc_dca_ativo", "msc_dca_encerramento"}
    assert conc["tem_msc"] and conc["tem_dca"]
    assert conc["conciliado"] is True and conc["n_divergencias"] == 0
    # Ativo = Passivo + PL (DCA).
    assert float(checks["balanco_fecha"]["esquerda"]) == 200.0
    assert float(checks["balanco_fecha"]["direita"]) == 200.0
    assert checks["balanco_fecha"]["divergente"] is False
    # Identidade do encerramento: MSC(Passivo+PL 150) + Resultado(VPA−VPD = 50) = DCA 200.
    enc = checks["msc_dca_encerramento"]
    assert float(enc["esquerda"]) == 200.0 and float(enc["direita"]) == 200.0
    assert enc["divergente"] is False
    # Rollup interno sem divergências.
    assert checks["rollup_msc"]["divergente"] is False


def test_conciliacao_sinaliza_divergencia(client, make_org, limpar) -> None:
    """Balanço que NÃO fecha (Passivo ≠ Ativo) deve ser sinalizado como divergência."""
    cod = _ente()
    limpar.append(cod)
    dca_quebrado = [
        ("P1.0.0.0.0.00.00", "1.0.0.0.0.00.00 - Ativo", "200"),
        ("P2.0.0.0.0.00.00", "2.0.0.0.0.00.00 - Passivo e Patrimônio Líquido", "190"),  # não fecha
    ]
    _seed(cod, com_msc=False, dca_iab=dca_quebrado)
    h = _auth(client, make_org, cod)

    conc = client.get(f"/entes/{cod}/msc/conciliacao", params={"ano": ANO}, headers=h).json()
    checks = {c["chave"]: c for c in conc["checks"]}
    assert checks["balanco_fecha"]["divergente"] is True
    assert float(checks["balanco_fecha"]["diferenca"]) == 10.0  # 200 − 190
    assert conc["conciliado"] is False and conc["n_divergencias"] >= 1


# --- balanços (DCA) ---
def test_balancos_index_e_patrimonial(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod)
    h = _auth(client, make_org, cod)

    idx = client.get(f"/entes/{cod}/balancos", params={"ano": ANO}, headers=h).json()
    tipos = {t["tipo"]: t for t in idx["tipos"]}
    assert tipos["patrimonial"]["disponivel"] is True
    assert tipos["patrimonial"]["anexo"] == "DCA-Anexo I-AB"

    pat = client.get(
        f"/entes/{cod}/balancos", params={"ano": ANO, "tipo": "patrimonial"}, headers=h
    ).json()
    assert float(pat["destaques"]["ativo"]) == 200.0
    assert float(pat["destaques"]["passivo_pl"]) == 200.0
    codigos = {ln["cod_conta"] for ln in pat["linhas"]}
    # O quadro suplementar (Ativo Financeiro, sufixo F) NÃO entra como conta PCASP.
    assert "1.0.0.0.0.00.00" in codigos
    assert all(not c.endswith("F") for c in codigos)


# --- MSC ausente: árvore honesta (Fortaleza real não publica MSC) ---
def test_sem_msc_arvore_vazia_e_matriz_404(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod, com_msc=False)  # só DCA
    h = _auth(client, make_org, cod)

    arv = client.get(f"/entes/{cod}/msc/arvore", params={"ano": ANO}, headers=h).json()
    assert arv["children"] == []  # sem MSC, mas responde (fonte DCA)
    r = client.get(f"/entes/{cod}/msc/conta/1.0.0.0.0.00.00/saldos", params={"ano": ANO}, headers=h)
    assert r.status_code == 404  # sem MSC → matriz mensal indisponível (honesto)


# --- bitemporal (as_of) ---
def test_as_of_reproduz_versao(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod)
    # v2 (retificação) do Balanço: Ativo passa a 250 (homologada depois).
    with SessionLocal() as s:
        s.execute(
            DimEntrega.__table__.update()
            .where(DimEntrega.cod_ibge == cod, DimEntrega.relatorio == "DCA")
            .values(vigente=False)
        )
        s.add(
            DimEntrega(
                cod_ibge=cod, relatorio="DCA", periodo=str(ANO), versao_entrega="2",
                homologada_em=datetime(2023, 9, 1, tzinfo=UTC), vigente=True,
            )
        )
        s.add(
            SilverDca(
                cod_ibge=cod, periodo=str(ANO), anexo="DCA-Anexo I-AB",
                conta="1.0.0.0.0.00.00 - Ativo", cod_conta="P1.0.0.0.0.00.00",
                coluna="31/12/2022", linha_seq=0, valor=Decimal("250"), versao_entrega="2",
            )
        )
        s.commit()
    h = _auth(client, make_org, cod)

    vig = client.get(
        f"/entes/{cod}/balancos", params={"ano": ANO, "tipo": "patrimonial"}, headers=h
    ).json()
    assert vig["versao_entrega"] == "2" and float(vig["destaques"]["ativo"]) == 250.0

    hist = client.get(
        f"/entes/{cod}/balancos",
        params={"ano": ANO, "tipo": "patrimonial", "as_of": "2023-07-01T00:00:00Z"}, headers=h,
    ).json()
    assert hist["versao_entrega"] == "1" and float(hist["destaques"]["ativo"]) == 200.0


# --- escopo multi-tenant ---
def test_escopo_403_fora_da_carteira(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod)
    fx = make_org(capacidades=["ver"], entes=[])  # carteira vazia
    h = auth_header(login(client, fx.email, fx.senha))

    resp = client.get(f"/entes/{cod}/msc/conciliacao", params={"ano": ANO}, headers=h)
    assert resp.status_code == 403
    assert resp.json()["title"] == "Ente fora do escopo"
