"""Conectores de transferências constitucionais (FPM/FPE, FUNDEB, genéricas).

FPM/FPE e transferências genéricas mantêm o parser de planilhas. FUNDEB usa a API
oficial de Transferências Constitucionais do Tesouro, indicada pela própria página de
consultas do FNDE para valores distribuídos por ente, mês e ano.
Consome: Sprint 5 (Receita), Sprint 14 (FPM como exógena), Sprint 11 (base de cálculo).
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.modules.ingestion import repository
from app.modules.ingestion.connectors._file_base import FileConnectorBase
from app.modules.ingestion.connectors._parsing import first, num
from app.modules.ingestion.connectors._spreadsheet import read_table
from app.modules.ingestion.models import (
    FONTE_FPM,
    FONTE_FUNDEB,
    FONTE_TRANSFERENCIA,
    FndeFundebRepasse,
    TesouroFpm,
    TransferenciaGenerica,
)
from app.shared.ingestion.base import BaseConnector, IngestionJob, capture_versao

# A API do Tesouro usa códigos internos (não os códigos IBGE) para filtrar UFs.
TESOURO_ESTADO_POR_PREFIXO_IBGE = {
    "11": 21,
    "12": 1,
    "13": 3,
    "14": 22,
    "15": 14,
    "16": 4,
    "17": 27,
    "21": 10,
    "22": 17,
    "23": 6,
    "24": 20,
    "25": 15,
    "26": 16,
    "27": 2,
    "28": 25,
    "29": 5,
    "31": 11,
    "32": 8,
    "33": 19,
    "35": 26,
    "41": 18,
    "42": 24,
    "43": 23,
    "50": 12,
    "51": 13,
    "52": 9,
    "53": 7,
}

FUNDEB_TRANSFERENCIAS = "10:14"  # FUNDEB + AJUSTE FUNDEB no catálogo oficial.


def _cod_ibge(row: dict[str, Any]) -> str:
    return str(first(row, "cod_ibge", "codigo_ibge", "IBGE", "cod_municipio_ibge") or "").strip()


class FpmConnector(FileConnectorBase):
    fonte = FONTE_FPM
    relatorio = "FPM"
    cadencia = "mensal"

    def parse(self, raw: bytes, job: IngestionJob) -> list[dict[str, Any]]:
        registros = []
        for row in read_table(raw, job.params):
            cod = _cod_ibge(row)
            if not cod:
                continue
            registros.append(
                {
                    "cod_ibge": cod,
                    "decendio": int(first(row, "decendio") or 0) or None,
                    "valor_bruto": num(first(row, "valor_bruto", "fpm_bruto", "bruto")),
                    "deducoes": num(first(row, "deducoes", "deducao")),
                    "valor_liquido": num(first(row, "valor_liquido", "fpm_liquido", "liquido")),
                }
            )
        return registros

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        mes = job.params["num"]
        rows = [
            {**r, "ano": job.ano, "mes": mes, "valid_time": job.valid_time,
             "versao_entrega": versao_entrega}
            for r in payload
        ]
        return repository.replace_silver_rows(
            session, TesouroFpm,
            keys={"ano": job.ano, "mes": mes, "versao_entrega": versao_entrega}, rows=rows,
        )


class FundebConnector(BaseConnector):
    """Materializa os repasses mensais reais do FUNDEB por ente."""

    fonte = FONTE_FUNDEB
    relatorio = "FUNDEB"
    cadencia = "mensal"

    def discover(self, state: dict[str, Any]) -> list[IngestionJob]:
        entes = list(dict.fromkeys(str(cod).strip() for cod in state.get("entes") or []))
        if not entes:
            return []
        anos: list[int] = state.get("anos") or [date.today().year]
        meses: list[int] = state.get("periodos") or list(range(1, 13))
        invalidos = sorted(set(meses) - set(range(1, 13)))
        if invalidos:
            raise ValueError(f"Meses FUNDEB inválidos: {invalidos}; use valores de 1 a 12")
        versao = state.get("versao") or capture_versao()

        jobs: list[IngestionJob] = []
        for ano in anos:
            for mes in meses:
                jobs.append(
                    IngestionJob(
                        fonte=self.fonte,
                        relatorio=self.relatorio,
                        cod_ibge="BR",
                        ano=ano,
                        periodo=f"{ano}-M{mes:02d}",
                        versao=versao,
                        homologada_em=state.get("homologada_em"),
                        valid_time=date(ano, mes, calendar.monthrange(ano, mes)[1]),
                        params={"num": mes, "entes": entes},
                    )
                )
        return jobs

    @staticmethod
    def _estado_tesouro(cod_ibge: str) -> int:
        try:
            return TESOURO_ESTADO_POR_PREFIXO_IBGE[cod_ibge[:2]]
        except KeyError as exc:
            raise ValueError(f"Prefixo IBGE desconhecido para o FUNDEB: {cod_ibge!r}") from exc

    def extract(self, job: IngestionJob) -> list[dict[str, Any]]:
        mes = int(job.params["num"])
        municipios_por_estado: dict[int, set[str]] = defaultdict(set)
        estados: dict[int, str] = {}
        for cod_ibge in job.params["entes"]:
            estado = self._estado_tesouro(cod_ibge)
            if len(cod_ibge) == 2:
                estados[estado] = cod_ibge
            elif len(cod_ibge) == 7 and cod_ibge.isdigit():
                municipios_por_estado[estado].add(cod_ibge)
            else:
                raise ValueError(
                    f"Código IBGE FUNDEB inválido: {cod_ibge!r}; "
                    "use UF com 2 ou município com 7 dígitos"
                )

        common = {
            "p_ano": job.ano,
            "p_mes": mes,
            "p_transferencia": FUNDEB_TRANSFERENCIAS,
        }
        records: list[dict[str, Any]] = []
        for estado, cod_ibge in estados.items():
            for item in self.client.get_records("por_estados", {**common, "p_estado": estado}):
                records.append({**item, "_cod_ibge": cod_ibge})

        for estado, codigos in municipios_por_estado.items():
            items = self.client.get_records(
                "por_estado_municipio", {**common, "p_estado": estado}
            )
            for item in items:
                cod = str(first(item, "CO_IBGE", "co_ibge") or "").strip()
                if cod in codigos:
                    records.append({**item, "_cod_ibge": cod})
        return records

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        mes = int(job.params["num"])
        totais: dict[str, Decimal] = defaultdict(Decimal)
        for item in payload:
            cod_ibge = str(item.get("_cod_ibge") or "").strip()
            valor = num(first(item, "VALOR", "valor"))
            if cod_ibge and valor is not None:
                totais[cod_ibge] += valor
        rows = [
            {
                "cod_ibge": cod_ibge,
                "ano": job.ano,
                "mes": mes,
                "valor_repassado": valor,
                # A rota consolidada não separa VAAF/VAAT/VAAR; não inventar rateio.
                "complementacao_uniao": None,
                "valid_time": job.valid_time,
                "versao_entrega": versao_entrega,
            }
            for cod_ibge, valor in sorted(totais.items())
        ]
        total = 0
        for cod_ibge in job.params["entes"]:
            rows_ente = [row for row in rows if row["cod_ibge"] == cod_ibge]
            total += repository.replace_silver_rows(
                session,
                FndeFundebRepasse,
                keys={
                    "cod_ibge": cod_ibge,
                    "ano": job.ano,
                    "mes": mes,
                    "versao_entrega": versao_entrega,
                },
                rows=rows_ente,
            )
        return total


class TransferenciaGenericaConnector(FileConnectorBase):
    fonte = FONTE_TRANSFERENCIA
    relatorio = "TRANSFERENCIA"
    cadencia = "mensal"

    def parse(self, raw: bytes, job: IngestionJob) -> list[dict[str, Any]]:
        registros = []
        for row in read_table(raw, job.params):
            cod = _cod_ibge(row)
            if not cod:
                continue
            registros.append(
                {
                    "cod_ibge": cod,
                    "tipo": str(first(row, "tipo") or "outros"),
                    "valor": num(first(row, "valor")),
                    "fonte": first(row, "fonte", "origem"),
                }
            )
        return registros

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        mes = job.params["num"]
        rows = [
            {**r, "ano": job.ano, "mes": mes, "valid_time": job.valid_time,
             "versao_entrega": versao_entrega}
            for r in payload
        ]
        return repository.replace_silver_rows(
            session, TransferenciaGenerica,
            keys={"ano": job.ano, "mes": mes, "versao_entrega": versao_entrega}, rows=rows,
        )


CONNECTORS: dict[str, type[BaseConnector]] = {
    FONTE_FPM: FpmConnector,
    FONTE_FUNDEB: FundebConnector,
    FONTE_TRANSFERENCIA: TransferenciaGenericaConnector,
}
