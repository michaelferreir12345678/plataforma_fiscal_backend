"""Conector BCB/SGS (índices econômicos). Cadência mensal/diária.

Fonte: https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados?formato=json
Códigos: IPCA=433, Selic diária=11, Selic mensal=4390, Selic anualizada=4189, IGP-M=189.
**Sempre** usa ``dataInicial``/``dataFinal`` (obrigatório em séries longas desde 03/2025).
Guarda a última data por série (lida do próprio silver) para o delta. Long format.
Consome: Sprint 14 (variáveis exógenas) e Sprint 5 (deflação).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.ingestion import repository
from app.modules.ingestion.connectors._parsing import num, parse_date
from app.modules.ingestion.models import FONTE_BCB, BcbIndice
from app.shared.ingestion.base import BaseConnector, IngestionJob, capture_versao

# Códigos de série padrão (SGS/BCB).
SERIES_PADRAO = (433, 11, 4390, 4189, 189)
_PERIODO = "SGS"  # a série é identificada por cod_ibge=codigo; período constante.


def _fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


class BcbSgsConnector(BaseConnector):
    fonte = FONTE_BCB
    relatorio = "BCB"

    def _ultima_data(self, session: Session, codigo: int) -> date | None:
        return session.scalar(
            select(func.max(BcbIndice.data_ref)).where(BcbIndice.codigo_serie == codigo)
        )

    def discover(self, state: dict[str, Any]) -> list[IngestionJob]:
        session: Session | None = state.get("session")
        series: list[int] = state.get("series") or list(SERIES_PADRAO)
        versao = state.get("versao") or capture_versao()
        data_final: date = state.get("data_final") or date.today()
        default_inicial: date = state.get("data_inicial") or date(data_final.year - 5, 1, 1)

        jobs: list[IngestionJob] = []
        for codigo in series:
            data_inicial = default_inicial
            if state.get("data_inicial") is None and session is not None:
                ultima = self._ultima_data(session, codigo)
                if ultima is not None:
                    data_inicial = ultima + timedelta(days=1)
            jobs.append(
                IngestionJob(
                    fonte=self.fonte,
                    relatorio=self.relatorio,
                    cod_ibge=str(codigo),
                    ano=data_final.year,
                    periodo=_PERIODO,
                    versao=versao,
                    homologada_em=state.get("homologada_em"),
                    valid_time=data_final,
                    params={
                        "codigo": codigo,
                        "formato": "json",
                        "dataInicial": _fmt(data_inicial),
                        "dataFinal": _fmt(data_final),
                    },
                )
            )
        return jobs

    def extract(self, job: IngestionJob) -> Any:
        codigo = job.params["codigo"]
        path = f"dados/serie/bcdata.sgs.{codigo}/dados"
        params = {k: v for k, v in job.params.items() if k != "codigo"}
        return self.client.get_records(path, params)

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        codigo = int(job.cod_ibge)
        rows = []
        for it in payload:
            data_ref = parse_date(it.get("data"))
            if data_ref is None:
                continue
            rows.append(
                {
                    "codigo_serie": codigo,
                    "data_ref": data_ref,
                    "valor": num(it.get("valor")),
                    "valid_time": data_ref,
                    "versao_entrega": versao_entrega,
                }
            )
        return repository.replace_silver_rows(
            session,
            BcbIndice,
            keys={"codigo_serie": codigo, "versao_entrega": versao_entrega},
            rows=rows,
        )


CONNECTORS: dict[str, type[BaseConnector]] = {FONTE_BCB: BcbSgsConnector}
