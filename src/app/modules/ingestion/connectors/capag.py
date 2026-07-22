"""Conector CAPAG (Tesouro) — planilha anual com nota A–D e subindicadores.

Cadência anual. ``versao_entrega`` = checksum do arquivo. O parser **falha explicitamente**
(``SpreadsheetLayoutError`` → HTTP 422) se o layout mudar — nunca adivinha, pois um CAPAG
errado silencioso contaminaria a Sprint 8 (Dívida / fato_capag).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.modules.ingestion import repository
from app.modules.ingestion.connectors._file_base import FileConnectorBase
from app.modules.ingestion.connectors._parsing import first, num
from app.modules.ingestion.connectors._spreadsheet import (
    SpreadsheetLayoutError,
    read_table,
    read_xlsx,
    require_columns,
)
from app.modules.ingestion.models import FONTE_CAPAG, TesouroCapag
from app.shared.ingestion.base import BaseConnector, IngestionJob

# Colunas mínimas exigidas (metodologia MF/STN vigente).
COLUNAS_NORMALIZADAS = (
    "cod_ibge",
    "nota_final",
    "ind_endividamento",
    "ind_poupanca",
    "ind_liquidez",
)

# Layout publicado pelo Tesouro: aba fixa, duas linhas de título e cabeçalho na linha 3.
ABA_OFICIAL = "Prévia da CAPAG"
LINHA_CABECALHO_OFICIAL = 3
COLUNAS_OFICIAIS = (
    "Código Município Completo",
    "CAPAG",
    "Indicador 1",
    "Indicador 2",
    "Indicador 3",
)


def _tem_colunas(registros: list[dict[str, Any]], colunas: tuple[str, ...]) -> bool:
    return bool(registros) and set(colunas).issubset(registros[0])


def _codigo_ibge(value: Any) -> str:
    """Normaliza o código lido do Excel, que pode chegar como inteiro ou ``2304400.0``."""
    if value in (None, ""):
        return ""
    texto = str(value).strip()
    if texto.endswith(".0") and texto[:-2].isdigit():
        texto = texto[:-2]
    return texto


class CapagConnector(FileConnectorBase):
    fonte = FONTE_CAPAG
    relatorio = "CAPAG"
    cadencia = "anual"

    def parse(self, raw: bytes, job: IngestionJob) -> list[dict[str, Any]]:
        if raw[:4] == b"PK\x03\x04":
            # Prioriza o contrato oficial: o arquivo público é grande e não deve ser
            # carregado uma vez pela aba ativa e outra vez pela aba correta.
            try:
                registros_oficiais = read_xlsx(
                    raw,
                    sheet=ABA_OFICIAL,
                    header_row=LINHA_CABECALHO_OFICIAL,
                )
            except SpreadsheetLayoutError:
                registros_oficiais = None

            if registros_oficiais is not None:
                require_columns(
                    registros_oficiais[0].keys() if registros_oficiais else (),
                    COLUNAS_OFICIAIS,
                )
                registros = registros_oficiais
                layout = "oficial"
            else:
                # Compatibilidade com o layout normalizado usado por cargas legadas/testes.
                registros = read_table(raw, job.params)
                if not registros:
                    return []
                require_columns(registros[0].keys(), COLUNAS_NORMALIZADAS)
                layout = "normalizado"
        else:
            registros = read_table(raw, job.params)
            if not registros:
                return []
            if _tem_colunas(registros, COLUNAS_NORMALIZADAS):
                layout = "normalizado"
            elif _tem_colunas(registros, COLUNAS_OFICIAIS):
                layout = "oficial"
            else:
                # CSVs oficiais têm cabeçalho na primeira linha; não há segunda posição a tentar.
                presentes = registros[0].keys()
                raise SpreadsheetLayoutError(
                    "Layout CAPAG não reconhecido. "
                    f"Esperadas colunas normalizadas {list(COLUNAS_NORMALIZADAS)} ou "
                    f"oficiais {list(COLUNAS_OFICIAIS)}. Presentes: {sorted(presentes)}"
                )

        mapeados = []
        for row in registros:
            if layout == "oficial":
                cod = _codigo_ibge(first(row, "Código Município Completo"))
                nota_final = first(row, "CAPAG")
                endividamento = first(row, "Indicador 1")
                poupanca = first(row, "Indicador 2")
                liquidez = first(row, "Indicador 3")
                metodologia = first(
                    row,
                    "Metodologia",
                    "Metodologia Versão",
                    "Versão da metodologia",
                )
            else:
                cod = _codigo_ibge(first(row, "cod_ibge"))
                nota_final = first(row, "nota_final")
                endividamento = first(row, "ind_endividamento")
                poupanca = first(row, "ind_poupanca")
                liquidez = first(row, "ind_liquidez")
                metodologia = first(row, "metodologia_versao", "metodologia")
            if not cod:
                continue
            mapeados.append(
                {
                    "cod_ibge": cod,
                    "nota_final": (str(nota_final or "").strip() or None),
                    # Indicador 1 da metodologia CAPAG = DC bruta / RCL.
                    "ind_endividamento": num(endividamento),
                    "ind_poupanca": num(poupanca),
                    "ind_liquidez": num(liquidez),
                    "metodologia_versao": metodologia,
                }
            )
        return mapeados

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        rows = [
            {**r, "ano_ref": job.ano, "valid_time": job.valid_time,
             "versao_entrega": versao_entrega}
            for r in payload
        ]
        return repository.replace_silver_rows(
            session, TesouroCapag,
            keys={"ano_ref": job.ano, "versao_entrega": versao_entrega}, rows=rows,
        )


CONNECTORS: dict[str, type[BaseConnector]] = {FONTE_CAPAG: CapagConnector}
