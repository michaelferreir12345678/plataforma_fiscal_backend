"""Conector da API oficial de consulta publica do SIOPS/Ministerio da Saude.

Cadencia bimestral e dado declaratorio. A API usa codigos proprios para o periodo
(``12, 14, 1, 18, 20, 2``); o silver preserva o bimestre canonico ``1..6``.
O SIOPS e somente enriquecimento: a base do minimo de ASPS continua no RREO Anexo 12.
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.modules.ingestion import repository
from app.modules.ingestion.connectors._parsing import first, num
from app.modules.ingestion.models import FONTE_SIOPS, SiopsSaude
from app.shared.ingestion.base import BaseConnector, IngestionJob, capture_versao

# Codigos aceitos pelo ultimo segmento da API SIOPS para cada bimestre civil.
PERIODO_API_POR_BIMESTRE = {1: 12, 2: 14, 3: 1, 4: 18, 5: 20, 6: 2}


def _fim_bimestre(ano: int, bimestre: int) -> date:
    mes = bimestre * 2
    return date(ano, mes, calendar.monthrange(ano, mes)[1])


def _valor_siops(value: Any) -> Decimal | None:
    """Converte ``24,06 %`` e valores monetarios pt-BR sem passar por float."""
    if value in (None, ""):
        return None
    if isinstance(value, int | float | Decimal):
        return num(value)
    text = str(value).replace("\xa0", " ").strip().replace("R$", "").replace("%", "")
    text = text.replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    return num(text)


class SiopsConnector(BaseConnector):
    fonte = FONTE_SIOPS
    relatorio = "SIOPS"
    cadencia = "bimestral"

    def discover(self, state: dict[str, Any]) -> list[IngestionJob]:
        entes = list(dict.fromkeys(str(cod).strip() for cod in state.get("entes") or []))
        if not entes:
            return []
        anos: list[int] = state.get("anos") or [date.today().year]
        bimestres: list[int] = state.get("periodos") or list(range(1, 7))
        invalidos = sorted(set(bimestres) - set(PERIODO_API_POR_BIMESTRE))
        if invalidos:
            raise ValueError(f"Bimestres SIOPS invalidos: {invalidos}; use valores de 1 a 6")
        versao = state.get("versao") or capture_versao()

        return [
            IngestionJob(
                fonte=self.fonte,
                relatorio=self.relatorio,
                cod_ibge="BR",
                ano=ano,
                periodo=f"{ano}-B{bimestre}",
                versao=versao,
                homologada_em=state.get("homologada_em"),
                valid_time=_fim_bimestre(ano, bimestre),
                params={"num": bimestre, "entes": entes},
            )
            for ano in anos
            for bimestre in bimestres
        ]

    def extract(self, job: IngestionJob) -> list[dict[str, Any]]:
        codigo_periodo = PERIODO_API_POR_BIMESTRE[int(job.params["num"])]
        records: list[dict[str, Any]] = []
        for cod_ibge in job.params["entes"]:
            if cod_ibge == "53":
                path = f"indicador/df/{job.ano}/{codigo_periodo}"
            elif len(cod_ibge) == 2:
                path = f"indicador/estadual/{cod_ibge}/{job.ano}/{codigo_periodo}"
            elif len(cod_ibge) == 7 and cod_ibge.isdigit():
                # O SIOPS omite o digito verificador municipal do codigo IBGE.
                path = f"indicador/municipal/{cod_ibge[:6]}/{job.ano}/{codigo_periodo}"
            else:
                raise ValueError(
                    f"Codigo IBGE SIOPS invalido: {cod_ibge!r}; "
                    "use UF com 2 ou municipio com 7 digitos"
                )
            for item in self.client.get_records(path, {}):
                records.append({**item, "_cod_ibge": cod_ibge})
        return records

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        bimestre = int(job.params["num"])
        rows = []
        for item in payload:
            codigo = first(item, "numero_indicador", "indicador_codigo")
            if codigo in (None, "") or not item.get("_cod_ibge"):
                continue
            valor_original = first(
                item, "indicador_calculado", "valor", "valor_indicador"
            )
            rows.append(
                {
                    "cod_ibge": str(item["_cod_ibge"]),
                    "ano": job.ano,
                    "bimestre": bimestre,
                    "indicador_codigo": str(codigo).strip(),
                    "indicador_descricao": first(
                        item, "ds_indicador", "indicador_descricao", "descricao"
                    ),
                    "unidade": (
                        "percentual" if "%" in str(valor_original or "") else None
                    ),
                    "valor": _valor_siops(valor_original),
                    "valid_time": job.valid_time,
                    "versao_entrega": versao_entrega,
                }
            )
        total = 0
        for cod_ibge in job.params["entes"]:
            rows_ente = [row for row in rows if row["cod_ibge"] == cod_ibge]
            total += repository.replace_silver_rows(
                session,
                SiopsSaude,
                keys={
                    "cod_ibge": cod_ibge,
                    "ano": job.ano,
                    "bimestre": bimestre,
                    "versao_entrega": versao_entrega,
                },
                rows=rows_ente,
            )
        return total


CONNECTORS: dict[str, type[BaseConnector]] = {FONTE_SIOPS: SiopsConnector}
