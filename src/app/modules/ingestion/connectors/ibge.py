"""Conectores IBGE (população e PIB municipal). Cadência anual.

Fontes oficiais:
- https://servicodados.ibge.gov.br/api/v3/agregados/{agregado}/...
- https://servicodados.ibge.gov.br/api/v1/pesquisas/38/periodos/{ano}/indicadores/47001/resultados/{cod_ibge}

- População: agregado 6579 (estimativas), variável 9324.
- PIB municipal: agregado 5938, variável 37 (PIB a preços correntes, em mil reais).
- PIB per capita: API de Pesquisas v1, pesquisa 38, indicador leaf 47001 (reais por
  habitante). O valor é consumido diretamente da fonte; não se combinam anos de PIB
  e população. A variável 513 do agregado 5938 é VAB agropecuário, não PIB per capita.
Consome: Sprint 2 (dim_ente: população/PIB por ano) e Sprint 13 (coortes por porte/PIB).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.modules.ingestion import repository
from app.modules.ingestion.connectors._parsing import num
from app.modules.ingestion.models import (
    FONTE_IBGE_PIB,
    FONTE_IBGE_POPULACAO,
    IbgePib,
    IbgePopulacao,
)
from app.shared.ingestion.base import BaseConnector, IngestionJob, capture_versao


class IbgeConnectorBase(BaseConnector):
    """Base IBGE: um job por (ente, ano); extract nos agregados v3."""

    agregado: int
    variaveis: str

    def discover(self, state: dict[str, Any]) -> list[IngestionJob]:
        entes: list[str] = state.get("entes") or []
        anos: list[int] = state.get("anos") or [date.today().year - 1]
        versao = state.get("versao") or capture_versao()
        jobs: list[IngestionJob] = []
        for cod_ibge in entes:
            for ano in anos:
                jobs.append(
                    IngestionJob(
                        fonte=self.fonte,
                        relatorio=self.relatorio,
                        cod_ibge=cod_ibge,
                        ano=ano,
                        periodo=f"{ano}",
                        versao=versao,
                        homologada_em=state.get("homologada_em"),
                        valid_time=date(ano, 12, 31),
                        params={"ano": ano, "cod_ibge": cod_ibge},
                    )
                )
        return jobs

    def extract(self, job: IngestionJob) -> Any:
        ano = job.params["ano"]
        cod_ibge = job.params["cod_ibge"]
        path = f"v3/agregados/{self.agregado}/periodos/{ano}/variaveis/{self.variaveis}"
        return self.client.get_records(path, {"localidades": f"N6[{cod_ibge}]"})


class IbgePopulacaoConnector(IbgeConnectorBase):
    fonte = FONTE_IBGE_POPULACAO
    relatorio = "IBGE-POP"
    agregado = 6579
    variaveis = "9324"

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        # payload já vem achatado: [{variavel, cod_ibge, ano, valor}]
        valores = (num(r.get("valor")) for r in payload if r.get("valor") not in (None, ""))
        valor = next(valores, None)
        if valor is None:
            return repository.replace_silver_rows(
                session,
                IbgePopulacao,
                keys={
                    "cod_ibge": job.cod_ibge,
                    "ano_ref": job.ano,
                    "versao_entrega": versao_entrega,
                },
                rows=[],
            )
        rows = [
            {
                "cod_ibge": job.cod_ibge,
                "ano_ref": job.ano,
                "populacao": int(valor) if valor is not None else None,
                "fonte": "estimativa",
                "valid_time": job.valid_time,
                "versao_entrega": versao_entrega,
            }
        ]
        return repository.replace_silver_rows(
            session,
            IbgePopulacao,
            keys={"cod_ibge": job.cod_ibge, "ano_ref": job.ano, "versao_entrega": versao_entrega},
            rows=rows,
        )


class IbgePibConnector(IbgeConnectorBase):
    fonte = FONTE_IBGE_PIB
    relatorio = "IBGE-PIB"
    agregado = 5938
    variaveis = "37"  # PIB a preços correntes; unidade oficial do agregado = mil reais
    pesquisa_pib = 38
    indicador_pib_per_capita = 47001

    def extract(self, job: IngestionJob) -> Any:
        """Extrai PIB nominal e per capita oficiais para o mesmo ente/ano.

        O agregado 5938 e a Pesquisa 38 têm contratos JSON diferentes, mas o cliente
        IBGE normaliza ambos para registros planos. Os dois payloads permanecem juntos
        no bronze, tornando a origem de cada coluna reproduzível na mesma entrega.
        """
        pib_nominal = super().extract(job)
        ano = job.params["ano"]
        cod_ibge = job.params["cod_ibge"]
        per_capita_path = (
            f"v1/pesquisas/{self.pesquisa_pib}/periodos/{ano}/indicadores/"
            f"{self.indicador_pib_per_capita}/resultados/{cod_ibge}"
        )
        pib_per_capita = self.client.get_records(per_capita_path, {})
        return {
            "pib_nominal_agregado_5938_variavel_37": pib_nominal,
            "pib_per_capita_pesquisa_38_indicador_47001": pib_per_capita,
        }

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        # Compatibilidade de replay: bronzes anteriores continham apenas a lista do
        # agregado 5938. Eles continuam legíveis, mas não fabricam PIB per capita.
        if isinstance(payload, dict):
            nominal_records = payload.get("pib_nominal_agregado_5938_variavel_37", [])
            per_capita_records = payload.get(
                "pib_per_capita_pesquisa_38_indicador_47001", []
            )
        else:
            nominal_records = payload if isinstance(payload, list) else []
            per_capita_records = []

        pib_nominal = _valor_ibge(nominal_records, codigo="37", ano=job.ano)
        pib_per_capita = _valor_ibge(
            per_capita_records,
            codigo=str(self.indicador_pib_per_capita),
            ano=job.ano,
        )
        if pib_nominal is None and pib_per_capita is None:
            return repository.replace_silver_rows(
                session,
                IbgePib,
                keys={
                    "cod_ibge": job.cod_ibge,
                    "ano_ref": job.ano,
                    "versao_entrega": versao_entrega,
                },
                rows=[],
            )
        rows = [
            {
                "cod_ibge": job.cod_ibge,
                "ano_ref": job.ano,
                "pib_nominal": pib_nominal,
                "pib_per_capita": pib_per_capita,
                "valid_time": job.valid_time,
                "versao_entrega": versao_entrega,
            }
        ]
        return repository.replace_silver_rows(
            session,
            IbgePib,
            keys={"cod_ibge": job.cod_ibge, "ano_ref": job.ano, "versao_entrega": versao_entrega},
            rows=rows,
        )


def _valor_ibge(records: Any, *, codigo: str, ano: int) -> Decimal | None:
    """Seleciona um valor do indicador e ano pedidos sem cruzar exercícios."""
    if not isinstance(records, list):
        return None
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("variavel")) != codigo or str(record.get("ano")) != str(ano):
            continue
        valor = num(record.get("valor"))
        if valor is not None:
            return valor
    return None


CONNECTORS: dict[str, type[BaseConnector]] = {
    FONTE_IBGE_POPULACAO: IbgePopulacaoConnector,
    FONTE_IBGE_PIB: IbgePibConnector,
}
