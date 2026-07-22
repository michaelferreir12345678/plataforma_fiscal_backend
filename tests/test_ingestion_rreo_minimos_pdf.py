"""Conector dos PDFs oficiais RREO Anexos 8/12 (sem rede)."""

# ruff: noqa: E501 -- as linhas longas reproduzem as linhas fisicas do layout PDF.

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.modules.ingestion import repository
from app.modules.ingestion.connectors import siconfi_rreo_minimos_pdf as pdf_connector
from app.modules.ingestion.connectors.registry import CONNECTOR_REGISTRY
from app.modules.ingestion.connectors.siconfi_rreo_minimos_pdf import (
    FONTE_RREO_MINIMOS_PDF,
    RreoMinimumPdfConnector,
    discover_minimum_pdf_links,
    parse_minimum_pdf_text,
)
from app.shared.ingestion.base import IngestionJob
from app.shared.ingestion.client import MunicipalRreoPdfClient, RealClientResolver

PAGE_URL = "https://transparencia.example/rreo/2024"
MDE_URL = "https://transparencia.example/downloadRREO/MDE.pdf"
ASPS_URL = "https://transparencia.example/downloadRREO/ASPS.pdf"

ANNUAL_HTML = f"""
<html><body>
  <h3>5o Bimestre</h3>
  <a href="/downloadRREO/relatorio-antigo.pdf">Outro demonstrativo</a>
  <h3>6o Bimestre</h3>
  <a href="{MDE_URL}/MANUTEN%C3%87%C3%83O%20E%20DESENVOLVIMENTO%20DO%20ENSINO-MDE">
    Anexo 8
  </a>
  <a href="{ASPS_URL}/A%C3%87%C3%95ES%20E%20SERVI%C3%87OS%20P%C3%9ABLICOS%20DE%20SA%C3%9ADE">
    Anexo 12
  </a>
</body></html>
"""

MDE_TEXT = """
RREO - ANEXO 8
2024
6o BIMESTRE NOVEMBRO - DEZEMBRO
3- TOTAL DA RECEITA RESULTANTE DE IMPOSTOS (1 + 2) 10.000,00 8.000,00
12- TOTAL DAS DESPESAS DO FUNDEB COM PROFISSIONAIS DA EDUCACAO BASICA 700,00 690,00 680,00 -
15- MINIMO DE 70% DO FUNDEB NA REMUNERACAO DOS PROFISSIONAIS DA EDUCACAO BASICA 560,00 700,00 690,00 86,25
20- TOTAL DAS DESPESAS COM ACOES TIPICAS DE MDE CUSTEADAS COM RECEITAS DE IMPOSTOS (D) (E) (F) (G)
1.000,00 800,00 790,00 780,00 -
23- TOTAL DAS RECEITAS TRANSFERIDAS AO FUNDEB = (L4) 200,00
24- (-) RECEITAS DO FUNDEB NAO UTILIZADAS NO EXERCICIO, EM VALOR SUPERIOR A 10% = L18(Q) 20,00
25- (-) SUPERAVIT PERMITIDO NO EXERCICIO IMEDIATAMENTE ANTERIOR NAO APLICADO NO EXERCICIO ATUAL = L19.1(X) 10,00
26- (-) RESTOS A PAGAR NAO PROCESSADOS INSCRITOS NO EXERCICIO SEM DISPONIBILIDADE FINANCEIRA DE RECURSOS DE IMPOSTOS 5,00
27- (-) CANCELAMENTO, NO EXERCICIO, DE RESTOS A PAGAR INSCRITOS COM DISPONIBILIDADE FINANCEIRA DE RECURSOS DE IMPOSTOS VINCULADOS AO ENSINO 3,00
"""

ASPS_TEXT = """
RREO - ANEXO 12
2024
6o BIMESTRE NOVEMBRO - DEZEMBRO
TOTAL DAS RECEITAS RESULTANTES DE IMPOSTOS E TRANSFERENCIAS CONSTITUCIONAIS E LEGAIS - (III) = (I) + (II) 10.000,00 9.000,00 8.000,00 88,89
ATENCAO BASICA (IV) 1.000,00 1.100,00 900,00 81,82 800,00 72,73 700,00 63,64 100,00
ASSISTENCIA HOSPITALAR E AMBULATORIAL (V) 2.000,00 2.100,00 1.900,00 90,48 1.800,00 85,71 1.700,00 80,95 100,00
SUPORTE PROFILATICO E TERAPEUTICO (VI) 100,00 110,00 90,00 81,82 80,00 72,73 70,00 63,64 10,00
VIGILANCIA SANITARIA (VII) 100,00 110,00 90,00 81,82 80,00 72,73 70,00 63,64 10,00
VIGILANCIA EPIDEMIOLOGICA (VIII) 100,00 110,00 90,00 81,82 80,00 72,73 70,00 63,64 10,00
ALIMENTACAO E NUTRICAO (IX) 100,00 110,00 90,00 81,82 80,00 72,73 70,00 63,64 10,00
OUTRAS SUBFUNCOES (X) 100,00 110,00 90,00 81,82 80,00 72,73 70,00 63,64 10,00
TOTAL DAS DESPESAS COM ASPS (XII) = (XI) 3.250,00 3.000,00 2.800,00
(-) RESTOS A PAGAR NAO PROCESSADOS INSCRITOS INDEVIDAMENTE NO EXERCICIO SEM DISPONIBILIDADE FINANCEIRA (XIII) 5,00 4,00 3,00
(-) DESPESAS CUSTEADAS COM RECURSOS VINCULADOS A PARCELA DO PERCENTUAL MINIMO QUE NAO FOI APLICADA EM ASPS EM EXERCICIOS ANTERIORES (XIV) 10,00 9,00 8,00
(-) DESPESAS CUSTEADAS COM DISPONIBILIDADE DE CAIXA VINCULADA AOS RESTOS A PAGAR CANCELADOS (XV) 2,00 1,00 -
Nota de rodape: disponibilidade financeira (v) = caixa liquido.
"""


class FakePdfClient:
    def __init__(self) -> None:
        self.page_calls: list[str] = []
        self.pdf_calls: list[tuple[str, str]] = []

    def fetch_page(self, url: str) -> str:
        self.page_calls.append(url)
        return ANNUAL_HTML

    def fetch_pdf(self, url: str, *, referer: str) -> bytes:
        self.pdf_calls.append((url, referer))
        return b"%PDF-MDE" if "MDE" in url else b"%PDF-ASPS"


def _values(rows: list[dict[str, Any]]) -> dict[tuple[str, str], Decimal]:
    return {(row["cod_conta"], row["coluna"]): row["valor"] for row in rows}


def test_pagina_anual_associa_links_ao_bimestre() -> None:
    links = discover_minimum_pdf_links(ANNUAL_HTML, PAGE_URL)

    assert links == {
        6: {
            "MDE": f"{MDE_URL}/MANUTEN%C3%87%C3%83O%20E%20DESENVOLVIMENTO%20DO%20ENSINO-MDE",
            "ASPS": f"{ASPS_URL}/A%C3%87%C3%95ES%20E%20SERVI%C3%87OS%20P%C3%9ABLICOS%20DE%20SA%C3%9ADE",
        }
    }


def test_parser_mde_emite_codigos_canonicos_e_aceita_linha_20_quebrada() -> None:
    values = _values(parse_minimum_pdf_text("MDE", MDE_TEXT))

    assert values[("MDE_BASE_IMPOSTOS_TRANSFERENCIAS", "REALIZADO")] == Decimal("8000.00")
    assert values[("MDE_DESPESA_IMPOSTOS", "EMPENHADO")] == Decimal("800.00")
    assert values[("MDE_DESPESA_IMPOSTOS", "LIQUIDADO")] == Decimal("790.00")
    assert values[("MDE_RPNP_SEM_LASTRO_REPORTADO", "VALOR")] == Decimal("5.00")
    assert values[("FUNDEB_PROFISSIONAIS", "PAGO")] == Decimal("680.00")
    assert values[("FUNDEB_BASE_PROFISSIONAIS", "VALOR")] == Decimal("800.00")


def test_parser_asps_emite_estagios_e_ignora_marcador_de_nota() -> None:
    rows = parse_minimum_pdf_text("ASPS", ASPS_TEXT)
    values = _values(rows)

    assert len(rows) == 37
    assert values[("ASPS_BASE_IMPOSTOS_TRANSFERENCIAS", "REALIZADO")] == Decimal("8000.00")
    assert values[("ASPS_DESPESA_TOTAL", "EMPENHADO")] == Decimal("3250.00")
    assert values[("ASPS_SUBFUNCAO_ASSISTENCIA_HOSPITALAR_E_AMBULATORIAL", "PAGO")] == Decimal(
        "1700.00"
    )
    assert values[("ASPS_DEDUCOES_OUTRAS", "LIQUIDADO")] == Decimal("10.00")


def test_connector_reusa_versao_rreo_e_cliente_falso_preserva_referer(monkeypatch) -> None:
    homologated_at = datetime(2025, 1, 31, tzinfo=UTC)
    monkeypatch.setattr(repository, "resolve_versao", lambda *args, **kwargs: "rreo-v7")
    monkeypatch.setattr(
        repository, "entrega_homologada_em", lambda *args, **kwargs: homologated_at
    )
    monkeypatch.setattr(
        pdf_connector,
        "_pdf_text",
        lambda content: MDE_TEXT if content == b"%PDF-MDE" else ASPS_TEXT,
    )
    fake = FakePdfClient()
    connector = RreoMinimumPdfConnector(fake, object())

    session = Session()
    try:
        jobs = connector.discover(
            {
                "session": session,
                "entes": ["2304400"],
                "anos": [2024],
                "periodos": [6],
                "page_url_template": PAGE_URL,
            }
        )
    finally:
        session.close()

    assert len(jobs) == 1
    job = jobs[0]
    assert (job.periodo, job.versao, job.homologada_em) == (
        "2024-B6",
        "rreo-v7",
        homologated_at,
    )
    assert job.valid_time is not None and job.valid_time.isoformat() == "2024-12-31"

    payload = connector.extract(job)
    assert fake.page_calls == [PAGE_URL]
    assert len(fake.pdf_calls) == 2
    assert all(referer == PAGE_URL for _, referer in fake.pdf_calls)
    assert {document["kind"] for document in payload["documents"]} == {"MDE", "ASPS"}


class _CaptureSession:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def execute(self, *args: Any) -> None:
        self.calls.append(args)


def test_silver_remove_somente_anexos_08_12_na_mesma_versao() -> None:
    connector = RreoMinimumPdfConnector(FakePdfClient(), object())
    job = IngestionJob(
        fonte=FONTE_RREO_MINIMOS_PDF,
        relatorio="RREO",
        cod_ibge="2304400",
        ano=2024,
        periodo="2024-B6",
        versao="rreo-v7",
    )
    payload = {
        "documents": [
            {"kind": "MDE", "text": MDE_TEXT},
            {"kind": "ASPS", "text": ASPS_TEXT},
        ]
    }
    session = _CaptureSession()

    assert connector.to_silver(session, job, payload, "rreo-v7") == 50  # type: ignore[arg-type]
    assert len(session.calls) == 2
    delete_sql = str(session.calls[0][0])
    assert all(column in delete_sql for column in ("cod_ibge", "periodo", "versao_entrega", "anexo"))
    inserted = session.calls[1][1]
    assert len(inserted) == 50
    assert {row["anexo"] for row in inserted} == {"RREO-Anexo 08", "RREO-Anexo 12"}
    assert {row["versao_entrega"] for row in inserted} == {"rreo-v7"}


def test_registry_e_resolver_usam_cliente_pdf_antes_do_prefixo_siconfi() -> None:
    assert CONNECTOR_REGISTRY[FONTE_RREO_MINIMOS_PDF] is RreoMinimumPdfConnector
    resolver = RealClientResolver()
    try:
        assert isinstance(resolver.get(FONTE_RREO_MINIMOS_PDF), MunicipalRreoPdfClient)
    finally:
        resolver.close()
