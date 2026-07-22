"""RREO municipal em PDF para os mínimos constitucionais (Anexos 8 e 12).

Alguns portais municipais publicam os demonstrativos oficiais em PDFs separados por
bimestre, enquanto o endpoint ``tt/rreo`` do SICONFI pode omitir exatamente os Anexos
8/12. Este conector complementa a entrega já existente: reutiliza sua mesma versão
``RREO`` e substitui somente esses dois anexos em ``silver.siconfi_rreo``.

O layout suportado é o MDF municipal moderno, com os títulos e números de linhas dos
Anexos 8 (MDE/FUNDEB) e 12 (ASPS). Mudança de layout falha explicitamente.
"""

from __future__ import annotations

import calendar
import hashlib
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from html.parser import HTMLParser
from io import BytesIO
from typing import Any, Literal, cast
from urllib.parse import unquote, urljoin

from pypdf import PdfReader
from sqlalchemy import delete, insert, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.ingestion import repository
from app.modules.ingestion.models import SilverRreo
from app.shared.ingestion.base import BaseConnector, IngestionJob

FONTE_RREO_MINIMOS_PDF = "siconfi_rreo_minimos_pdf"
ANEXO_MDE = "RREO-Anexo 08"
ANEXO_ASPS = "RREO-Anexo 12"

ReportKind = Literal["MDE", "ASPS"]
_BIMESTRES = frozenset(range(1, 7))
_END_MONTH_TO_BIMESTER = {
    "FEVEREIRO": 1,
    "ABRIL": 2,
    "JUNHO": 3,
    "AGOSTO": 4,
    "OUTUBRO": 5,
    "DEZEMBRO": 6,
}
_VALUE_RE = re.compile(r"(?<![\w.,])(?:\(?-?\d[\d.]*,\d{2}\)?|-)(?![\w.,])")
_DECIMAL_RE = re.compile(r"\(?-?\d[\d.]*,\d{2}\)?")


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return " ".join(
        "".join(char for char in normalized if not unicodedata.combining(char))
        .upper()
        .split()
    )


def _kind_from_href(href: str) -> ReportKind | None:
    label = _normalize(unquote(href))
    if "MANUTENCAO E DESENVOLVIMENTO DO ENSINO" in label:
        return "MDE"
    if "ACOES E SERVICOS PUBLICOS DE SAUDE" in label:
        return "ASPS"
    return None


class _AnnualPageParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.current_bimester: int | None = None
        self.links: dict[int, dict[ReportKind, str]] = {}

    def handle_data(self, data: str) -> None:
        match = re.search(r"\b([1-6])\s*(?:°|º|O)?\s*BIMESTRE\b", _normalize(data))
        if match is not None:
            self.current_bimester = int(match.group(1))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self.current_bimester is None:
            return
        href = next((value for name, value in attrs if name.lower() == "href"), None)
        if not href or "downloadrreo" not in href.lower():
            return
        kind = _kind_from_href(href)
        if kind is None:
            return
        absolute = urljoin(self.page_url, href)
        by_kind = self.links.setdefault(self.current_bimester, {})
        previous = by_kind.get(kind)
        if previous is not None and previous != absolute:
            raise ValueError(
                f"Pagina RREO ambigua: mais de um PDF {kind} no "
                f"{self.current_bimester}o bimestre"
            )
        by_kind[kind] = absolute


def discover_minimum_pdf_links(html: str, page_url: str) -> dict[int, dict[ReportKind, str]]:
    """Descobre os PDFs MDE/ASPS, associando cada link ao bimestre da seção HTML."""
    parser = _AnnualPageParser(page_url)
    parser.feed(html)
    return parser.links


def _pdf_text(content: bytes) -> str:
    if not content.startswith(b"%PDF-"):
        raise ValueError("Download de RREO nao e PDF (assinatura %PDF- ausente)")
    try:
        reader = PdfReader(BytesIO(content), strict=False)
        pages: list[str] = []
        for page in reader.pages:
            try:
                text = page.extract_text(extraction_mode="layout")
            except TypeError:  # pragma: no cover - compatibilidade com pypdf antigo
                text = page.extract_text()
            pages.append(text or "")
    except Exception as exc:
        raise ValueError(f"PDF RREO invalido ou ilegivel: {exc}") from exc
    result = "\n".join(pages).strip()
    if not result:
        raise ValueError("PDF RREO sem camada de texto; OCR nao e inferido automaticamente")
    return result


def _reported_bimester(text: str) -> int | None:
    normalized = _normalize(text)
    match = re.search(r"BIMESTRE\s+[A-Z]+\s*[-–]\s*([A-Z]+)", normalized)
    return _END_MONTH_TO_BIMESTER.get(match.group(1)) if match else None


def _logical_lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def _find_line(lines: list[str], pattern: str, label: str) -> str:
    regex = re.compile(pattern)
    matches = [line for line in lines if regex.search(_normalize(line))]
    if len(matches) != 1:
        raise ValueError(
            f"Layout RREO desconhecido: esperado 1 registro '{label}', encontrados {len(matches)}"
        )
    return matches[0]


def _join_numeric_continuation(lines: list[str], line: str) -> str:
    """Reune uma grade que o extrator PDF quebrou logo antes dos valores.

    Alguns PDFs oficiais posicionam os valores da linha 20 em uma linha visual
    separada. So anexamos continuacoes que comecam por moeda/``-`` para nunca engolir
    a proxima conta numerada.
    """
    index = lines.index(line)
    combined = line
    for continuation in lines[index + 1 : index + 3]:
        if re.match(r"^(?:\(?-?\d[\d.]*,\d{2}\)?|-)(?:\s|$)", continuation) is None:
            break
        combined = f"{combined} {continuation}"
    return combined


def _parse_decimal(token: str) -> Decimal | None:
    token = token.strip()
    if token == "-":
        return None
    negative_parentheses = token.startswith("(") and token.endswith(")")
    cleaned = token.strip("()").replace(".", "").replace(",", ".")
    value = Decimal(cleaned)
    return -value if negative_parentheses else value


def _decimal_values(line: str) -> list[Decimal]:
    return [
        value
        for token in _DECIMAL_RE.findall(line)
        if (value := _parse_decimal(token)) is not None
    ]


def _tokens_after_marker(line: str, marker: str) -> list[Decimal | None]:
    position = line.upper().find(f"({marker})")
    if position < 0:
        raise ValueError(f"Layout RREO desconhecido: marcador ({marker}) ausente")
    suffix = line[position + len(marker) + 2 :]
    return [_parse_decimal(token) for token in _VALUE_RE.findall(suffix)]


def _zero(value: Decimal | None) -> Decimal:
    return value if value is not None else Decimal(0)


def _require(values: list[Any], length: int, label: str) -> None:
    if len(values) < length:
        raise ValueError(
            f"Layout RREO desconhecido em '{label}': "
            f"esperadas ao menos {length} colunas, recebidas {len(values)}"
        )


def _row(
    anexo: str,
    codigo: str,
    descricao: str,
    coluna: str,
    valor: Decimal,
) -> dict[str, Any]:
    return {
        "anexo": anexo,
        "conta": descricao,
        "cod_conta": codigo,
        "coluna": coluna,
        "valor": valor,
    }


def _stage_rows(
    anexo: str,
    codigo: str,
    descricao: str,
    empenhado: Decimal | None,
    liquidado: Decimal | None,
    pago: Decimal | None,
) -> list[dict[str, Any]]:
    return [
        _row(anexo, codigo, descricao, "EMPENHADO", _zero(empenhado)),
        _row(anexo, codigo, descricao, "LIQUIDADO", _zero(liquidado)),
        _row(anexo, codigo, descricao, "PAGO", _zero(pago)),
    ]


def _parse_asps(text: str) -> list[dict[str, Any]]:
    lines = _logical_lines(text)
    base_line = _find_line(
        lines,
        r"TOTAL DAS RECEITAS RESULTANTES DE IMPOSTOS.*\(III\)",
        "ASPS base de impostos e transferencias",
    )
    base_values = _tokens_after_marker(base_line, "III")
    _require(base_values, 4, "ASPS base de impostos e transferencias")
    rows = [
        _row(
            ANEXO_ASPS,
            "ASPS_BASE_IMPOSTOS_TRANSFERENCIAS",
            "Receitas resultantes de impostos e transferencias constitucionais e legais",
            "REALIZADO",
            _zero(base_values[-2]),
        )
    ]

    total_line = _find_line(
        lines,
        r"^TOTAL DAS DESPESAS COM ASPS \(XII\)",
        "ASPS total das despesas",
    )
    total = _tokens_after_marker(total_line, "XII")
    # A linha XII e condensada: ao contrario da grade de subfuncoes, traz somente
    # empenhado, liquidado e pago depois do marcador.
    _require(total, 3, "ASPS total das despesas")
    rows.extend(
        _stage_rows(
            ANEXO_ASPS,
            "ASPS_DESPESA_TOTAL",
            "Total das despesas com ASPS",
            total[0],
            total[1],
            total[2],
        )
    )

    deductions: dict[str, list[Decimal | None]] = {}
    for marker, code, description in (
        (
            "XIII",
            "ASPS_RPNP_SEM_LASTRO_REPORTADO",
            "RPNP inscrito sem disponibilidade financeira reportado no RREO",
        ),
        (
            "XIV",
            "ASPS_DEDUCAO_XIV",
            "Despesas vinculadas a minimo nao aplicado em exercicios anteriores",
        ),
        (
            "XV",
            "ASPS_DEDUCAO_XV",
            "Despesas vinculadas a restos a pagar cancelados",
        ),
    ):
        line = _find_line(lines, rf"\({marker}\)", f"ASPS linha {marker}")
        values = _tokens_after_marker(line, marker)
        _require(values, 3, f"ASPS linha {marker}")
        deductions[marker] = values
        rows.extend(_stage_rows(ANEXO_ASPS, code, description, *values[:3]))

    rows.extend(
        _stage_rows(
            ANEXO_ASPS,
            "ASPS_DEDUCOES_OUTRAS",
            "Deducoes dos itens XIV e XV",
            _zero(deductions["XIV"][0]) + _zero(deductions["XV"][0]),
            _zero(deductions["XIV"][1]) + _zero(deductions["XV"][1]),
            _zero(deductions["XIV"][2]) + _zero(deductions["XV"][2]),
        )
    )

    subfunctions = (
        (
            "IV",
            "ASPS_SUBFUNCAO_ATENCAO_BASICA",
            "Atencao basica",
            r"^ATENCAO BASICA \(IV\)",
        ),
        (
            "V",
            "ASPS_SUBFUNCAO_ASSISTENCIA_HOSPITALAR_E_AMBULATORIAL",
            "Assistencia hospitalar e ambulatorial",
            r"^ASSISTENCIA HOSPITALAR E AMBULATORIAL \(V\)",
        ),
        (
            "VI",
            "ASPS_SUBFUNCAO_SUPORTE_PROFILATICO_E_TERAPEUTICO",
            "Suporte profilatico e terapeutico",
            r"^SUPORTE PROFILATICO E TERAPEUTICO \(VI\)",
        ),
        (
            "VII",
            "ASPS_SUBFUNCAO_VIGILANCIA_SANITARIA",
            "Vigilancia sanitaria",
            r"^VIGILANCIA SANITARIA \(VII\)",
        ),
        (
            "VIII",
            "ASPS_SUBFUNCAO_VIGILANCIA_EPIDEMIOLOGICA",
            "Vigilancia epidemiologica",
            r"^VIGILANCIA EPIDEMIOLOGICA \(VIII\)",
        ),
        (
            "IX",
            "ASPS_SUBFUNCAO_ALIMENTACAO_E_NUTRICAO",
            "Alimentacao e nutricao",
            r"^ALIMENTACAO E NUTRICAO \(IX\)",
        ),
        (
            "X",
            "ASPS_SUBFUNCAO_DEMAIS",
            "Outras subfuncoes",
            r"^OUTRAS SUBFUNCOES \(X\)",
        ),
    )
    for marker, code, description, pattern in subfunctions:
        line = _find_line(
            lines,
            pattern,
            f"ASPS subfuncao {marker}",
        )
        values = _tokens_after_marker(line, marker)
        _require(values, 7, f"ASPS subfuncao {marker}")
        rows.extend(
            _stage_rows(
                ANEXO_ASPS,
                code,
                description,
                values[2],
                values[4],
                values[6],
            )
        )
    return rows


def _last_value(line: str) -> Decimal:
    values = _decimal_values(line)
    return values[-1] if values else Decimal(0)


def _parse_mde(text: str) -> list[dict[str, Any]]:
    lines = _logical_lines(text)
    base_line = _find_line(
        lines,
        r"^3\s*-\s*TOTAL DA RECEITA RESULTANTE DE IMPOSTOS",
        "MDE base de impostos e transferencias",
    )
    base = _decimal_values(base_line)
    _require(base, 2, "MDE base de impostos e transferencias")
    rows = [
        _row(
            ANEXO_MDE,
            "MDE_BASE_IMPOSTOS_TRANSFERENCIAS",
            "Receita resultante de impostos e transferencias",
            "REALIZADO",
            base[-1],
        )
    ]

    taxes_line = _find_line(
        lines,
        r"^20\s*-\s*TOTAL DAS DESPESAS COM ACOES TIPICAS DE MDE.*RECEITAS DE IMPOSTOS",
        "MDE despesas custeadas com impostos",
    )
    taxes_line = _join_numeric_continuation(lines, taxes_line)
    taxes = _decimal_values(taxes_line)
    # dotacao atualizada + empenhado + liquidado + pago; o RPNP final pode ser
    # impresso apenas como '-' e, portanto, nao entra em ``_decimal_values``.
    _require(taxes, 4, "MDE despesas custeadas com impostos")
    rows.extend(
        _stage_rows(
            ANEXO_MDE,
            "MDE_DESPESA_IMPOSTOS",
            "Acoes tipicas de MDE custeadas com receitas de impostos",
            taxes[1],
            taxes[2],
            taxes[3],
        )
    )

    transfer_line = _find_line(
        lines,
        r"^23\s*-\s*TOTAL DAS RECEITAS TRANSFERIDAS AO FUNDEB",
        "MDE transferencia ao FUNDEB",
    )
    rows.append(
        _row(
            ANEXO_MDE,
            "MDE_TRANSFERENCIA_FUNDEB",
            "Receitas transferidas ao FUNDEB",
            "VALOR",
            _last_value(transfer_line),
        )
    )

    for pattern, code, description in (
        (
            r"^25\s*-.*SUPERAVIT PERMITIDO NO EXERCICIO IMEDIATAMENTE ANTERIOR",
            "MDE_SUPERAVIT_EXERCICIO_ANTERIOR",
            "Superavit permitido do exercicio anterior nao aplicado",
        ),
        (
            r"^24\s*-.*RECEITAS DO FUNDEB NAO UTILIZADAS",
            "MDE_COMPLEMENTACAO_VAAF_EXERCICIO_ANTERIOR",
            "Receitas do FUNDEB nao utilizadas acima do limite legal",
        ),
        (
            r"^27\s*-.*CANCELAMENTO.*RESTOS A PAGAR",
            "MDE_CANCELAMENTOS",
            "Cancelamentos de restos a pagar vinculados ao ensino",
        ),
        (
            r"^26\s*-.*RESTOS A PAGAR NAO PROCESSADOS.*SEM DISPONIBILIDADE FINANCEIRA",
            "MDE_RPNP_SEM_LASTRO_REPORTADO",
            "RPNP sem disponibilidade financeira reportado no RREO",
        ),
    ):
        line = _find_line(lines, pattern, code)
        rows.append(_row(ANEXO_MDE, code, description, "VALOR", _last_value(line)))

    professionals_line = _find_line(
        lines,
        r"^12\s*-\s*TOTAL DAS DESPESAS DO FUNDEB COM PROFISSIONAIS DA EDUCACAO BASICA",
        "FUNDEB despesas com profissionais",
    )
    professionals = _decimal_values(professionals_line)
    # Esta grade comeca diretamente nos tres estagios (nao ha dotacao atualizada).
    _require(professionals, 3, "FUNDEB despesas com profissionais")
    rows.extend(
        _stage_rows(
            ANEXO_MDE,
            "FUNDEB_PROFISSIONAIS",
            "Despesas do FUNDEB com profissionais da educacao basica",
            professionals[0],
            professionals[1],
            professionals[2],
        )
    )

    minimum_line = _find_line(
        lines,
        r"^15\s*-\s*MINIMO DE 70% DO FUNDEB.*PROFISSIONAIS DA EDUCACAO BASICA",
        "FUNDEB minimo de 70%",
    )
    minimum_values = _decimal_values(minimum_line)
    _require(minimum_values, 4, "FUNDEB minimo de 70%")
    fundeb_base = (minimum_values[0] / Decimal("0.70")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    rows.append(
        _row(
            ANEXO_MDE,
            "FUNDEB_BASE_PROFISSIONAIS",
            "Base principal do FUNDEB sujeita ao minimo de profissionais",
            "VALOR",
            fundeb_base,
        )
    )
    return rows


def parse_minimum_pdf_text(kind: ReportKind, text: str) -> list[dict[str, Any]]:
    """Converte texto extraído do PDF oficial nos códigos canônicos do módulo 10."""
    normalized = _normalize(text)
    expected = r"ANEXO\s+0?8\b" if kind == "MDE" else r"ANEXO\s+12\b"
    if re.search(expected, normalized) is None:
        raise ValueError(f"PDF {kind} nao declara o anexo esperado")
    return _parse_mde(text) if kind == "MDE" else _parse_asps(text)


@dataclass(frozen=True)
class _ExistingDelivery:
    version: str
    homologated_at: datetime | None


class RreoMinimumPdfConnector(BaseConnector):
    fonte = FONTE_RREO_MINIMOS_PDF
    relatorio = "RREO"

    def _existing_delivery(
        self, session: Session, cod_ibge: str, period: str
    ) -> _ExistingDelivery:
        version = repository.resolve_versao(
            session,
            cod_ibge=cod_ibge,
            relatorio=self.relatorio,
            periodo=period,
        )
        if version is None:
            raise ValueError(
                f"RREO base ausente para {cod_ibge}/{period}; "
                "ingira siconfi_rreo antes dos PDFs de minimos"
            )
        homologated_at = repository.entrega_homologada_em(
            session,
            cod_ibge=cod_ibge,
            relatorio=self.relatorio,
            periodo=period,
            versao_entrega=version,
        )
        return _ExistingDelivery(version, homologated_at)

    def discover(self, state: dict[str, Any]) -> list[IngestionJob]:
        session = state.get("session")
        if not isinstance(session, Session):
            raise ValueError("Conector PDF de minimos exige sessao para resolver a versao RREO")
        entes = list(dict.fromkeys(str(code).strip() for code in state.get("entes") or []))
        years: list[int] = state.get("anos") or []
        bimesters: list[int] = state.get("periodos") or list(range(1, 7))
        invalid = sorted(set(bimesters) - _BIMESTRES)
        if invalid:
            raise ValueError(f"Periodos RREO invalidos: {invalid}; use valores de 1 a 6")

        custom_template = state.get("page_url_template")
        if custom_template is None:
            invalid_entities = [
                code for code in entes if code != settings.rreo_minimos_pdf_default_cod_ibge
            ]
            if invalid_entities:
                raise ValueError(
                    "O template padrao pertence a Fortaleza; informe "
                    "params.page_url_template para os entes: " + ", ".join(invalid_entities)
                )
        template = custom_template or settings.rreo_minimos_pdf_page_url_template
        requested_version = state.get("versao")
        jobs: list[IngestionJob] = []
        for code in entes:
            for year in years:
                page_url = str(template).format(ano=year, cod_ibge=code)
                for bimester in bimesters:
                    period = f"{year}-B{bimester}"
                    delivery = self._existing_delivery(session, code, period)
                    if requested_version and requested_version != delivery.version:
                        raise ValueError(
                            f"Versao solicitada {requested_version!r} difere da RREO vigente "
                            f"{delivery.version!r} em {code}/{period}"
                        )
                    jobs.append(
                        IngestionJob(
                            fonte=self.fonte,
                            relatorio=self.relatorio,
                            cod_ibge=code,
                            ano=year,
                            periodo=period,
                            versao=delivery.version,
                            homologada_em=delivery.homologated_at,
                            valid_time=date(
                                year,
                                bimester * 2,
                                calendar.monthrange(year, bimester * 2)[1],
                            ),
                            params={"page_url": page_url, "bimestre": bimester},
                        )
                    )
        return jobs

    def extract(self, job: IngestionJob) -> dict[str, Any]:
        page_url = str(job.params["page_url"])
        bimester = int(job.params["bimestre"])
        html = self.client.fetch_page(page_url)
        links = discover_minimum_pdf_links(html, page_url).get(bimester, {})
        missing = [kind for kind in ("MDE", "ASPS") if kind not in links]
        if missing:
            raise ValueError(
                f"Pagina anual sem PDF {', '.join(missing)} para {job.periodo}: {page_url}"
            )

        documents: list[dict[str, Any]] = []
        for kind in ("MDE", "ASPS"):
            typed_kind = cast(ReportKind, kind)
            url = links[typed_kind]
            content = self.client.fetch_pdf(url, referer=page_url)
            text = _pdf_text(content)
            reported = _reported_bimester(text)
            if reported is not None and reported != bimester:
                raise ValueError(
                    f"PDF {kind} de {job.periodo} declara o bimestre {reported}"
                )
            if str(job.ano) not in text:
                raise ValueError(f"PDF {kind} nao declara o exercicio {job.ano}")
            # Valida o layout antes de persistir o bronze; o parsing final é repetido no
            # ``to_silver`` para manter o replay determinístico somente a partir do payload.
            parse_minimum_pdf_text(typed_kind, text)
            documents.append(
                {
                    "kind": kind,
                    "url": url,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "text": text,
                }
            )
        return {"page_url": page_url, "bimestre": bimester, "documents": documents}

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        documents = payload.get("documents") if isinstance(payload, dict) else None
        if not isinstance(documents, list):
            raise ValueError("Payload PDF RREO invalido: documents ausente")

        parsed: list[dict[str, Any]] = []
        found: set[str] = set()
        for document in documents:
            if not isinstance(document, dict) or document.get("kind") not in ("MDE", "ASPS"):
                raise ValueError("Payload PDF RREO contem documento desconhecido")
            kind = cast(ReportKind, document["kind"])
            found.add(kind)
            parsed.extend(parse_minimum_pdf_text(kind, str(document.get("text") or "")))
        if found != {"MDE", "ASPS"}:
            raise ValueError("Payload PDF RREO deve conter exatamente MDE e ASPS")

        session.execute(
            delete(SilverRreo).where(
                SilverRreo.cod_ibge == job.cod_ibge,
                SilverRreo.periodo == job.periodo,
                SilverRreo.versao_entrega == versao_entrega,
                or_(
                    SilverRreo.anexo.ilike("%Anexo 08%"),
                    SilverRreo.anexo.ilike("%Anexo 8%"),
                    SilverRreo.anexo.ilike("%Anexo 12%"),
                ),
            )
        )
        rows = [
            {
                "id": uuid.uuid4(),
                "cod_ibge": job.cod_ibge,
                "periodo": job.periodo,
                "poder": None,
                "linha_seq": sequence,
                "valid_time": job.valid_time,
                "versao_entrega": versao_entrega,
                **row,
            }
            for sequence, row in enumerate(parsed, start=1)
        ]
        session.execute(insert(SilverRreo), rows)
        return len(rows)


CONNECTORS: dict[str, type[BaseConnector]] = {
    FONTE_RREO_MINIMOS_PDF: RreoMinimumPdfConnector,
}
