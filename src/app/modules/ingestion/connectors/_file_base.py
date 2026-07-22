"""Base dos conectores baseados em arquivo (planilhas).

Reusa o ``BaseConnector`` sobrescrevendo ``prepare`` para: baixar o arquivo, calcular o
``checksum`` (que vira ``versao_entrega``) e fazer o *parse* tipado antes de gravar o
bronze. O arquivo costuma conter muitos entes; o job representa o **arquivo** (escopo),
e o ``to_silver`` explode em linhas por município.
"""

from __future__ import annotations

import calendar
from abc import abstractmethod
from dataclasses import replace
from datetime import date
from typing import Any

from app.modules.ingestion.connectors._spreadsheet import file_checksum
from app.shared.ingestion.base import BaseConnector, IngestionJob


def _month_end(ano: int, month: int) -> date:
    return date(ano, month, calendar.monthrange(ano, month)[1])


class FileConnectorBase(BaseConnector):
    """Conector de planilha: download → checksum (versão) → parse → bronze/silver.

    ``cadencia`` ∈ {``mensal``, ``bimestral``, ``anual``} define os períodos do ``discover``.
    """

    cadencia: str = "mensal"

    # --- discover genérico por ano × período ---
    def _periodos(self, state: dict[str, Any]) -> list[int]:
        if self.cadencia == "anual":
            return [0]
        if state.get("periodos"):
            return list(state["periodos"])
        return list(range(1, 13)) if self.cadencia == "mensal" else list(range(1, 7))

    def _periodo_str(self, ano: int, num: int) -> tuple[str, date]:
        if self.cadencia == "mensal":
            return f"{ano}-M{num:02d}", _month_end(ano, num)
        if self.cadencia == "bimestral":
            return f"{ano}-B{num}", _month_end(ano, min(num * 2, 12))
        return f"{ano}", date(ano, 12, 31)

    def discover(self, state: dict[str, Any]) -> list[IngestionJob]:
        anos: list[int] = state.get("anos") or [date.today().year]
        escopo: str = state.get("escopo") or "BR"
        versao: str = state.get("versao") or ""  # "" ⇒ usar checksum do arquivo
        url_tmpl: str = state.get("url") or ""
        homologada = state.get("homologada_em")
        jobs: list[IngestionJob] = []
        for ano in anos:
            for num in self._periodos(state):
                periodo, valid_time = self._periodo_str(ano, num)
                url = url_tmpl.format(ano=ano, mes=num, num=num, bimestre=num) if url_tmpl else ""
                jobs.append(
                    IngestionJob(
                        fonte=self.fonte,
                        relatorio=self.relatorio,
                        cod_ibge=escopo,
                        ano=ano,
                        periodo=periodo,
                        versao=versao,
                        homologada_em=homologada,
                        valid_time=valid_time,
                        params={
                            "url": url,
                            "num": num,
                            "formato": state.get("formato"),
                            "sheet": state.get("sheet"),
                            "delimiter": state.get("delimiter", ";"),
                        },
                    )
                )
        return jobs

    # --- ciclo: download + checksum + parse ---
    def prepare(self, job: IngestionJob) -> tuple[IngestionJob, Any]:
        raw = self.client.fetch(job.params)
        checksum = file_checksum(raw)
        new_job = replace(job, versao=job.versao or checksum)
        return new_job, self.parse(raw, new_job)

    def extract(self, job: IngestionJob) -> Any:
        return self.parse(self.client.fetch(job.params), job)

    @abstractmethod
    def parse(self, raw: bytes, job: IngestionJob) -> list[dict[str, Any]]:
        """Lê a planilha e devolve registros normalizados (armazenados no bronze)."""
