"""Conectores de transferências constitucionais (FPM/FPE, FUNDEB, genéricas).

Os três consomem a **API oficial de Transferências Constitucionais do Tesouro**, que serve
os dezoito tipos do catálogo por ente, mês e ano — muda apenas o código em
``p_transferencia``. FPM e genéricas eram planilhas cuja URL nunca foi configurada e
falhavam em toda execução; migrá-las para a API elimina o endereço a manter.
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
from app.modules.ingestion.connectors._parsing import first, num
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

# Códigos do catálogo oficial (``/transferencias`` da API de Transferências
# Constitucionais). Consultados na própria API para não fixar número adivinhado.
FUNDEB_TRANSFERENCIAS = "10:14"  # FUNDEB + AJUSTE FUNDEB.
FPM_TRANSFERENCIAS = "3:7:18"  # FPM + FPE + FPM 1%.
#: "Demais transferências": tudo o que tem conector próprio fica de fora — FPM/FPE (3,7,18)
#: e FUNDEB (10,14) —, senão o mesmo repasse seria contado duas vezes na receita.
OUTRAS_TRANSFERENCIAS = "1:2:4:5:6:8:9:11:12:13:15:16:17"


def _cod_ibge(row: dict[str, Any]) -> str:
    return str(first(row, "cod_ibge", "codigo_ibge", "IBGE", "cod_municipio_ibge") or "").strip()


class TransferenciasApiConnector(BaseConnector):
    """Base das transferências constitucionais servidas pela **API oficial do Tesouro**.

    FPM e "demais transferências" eram conectores de planilha cuja URL nunca foi
    configurada: falhavam em toda execução com "fonte de arquivo sem URL utilizável". A
    própria API que o FUNDEB já consumia serve todos os dezoito tipos do catálogo — bastava
    trocar o código em ``p_transferencia``. Nada de planilha, nada de endereço para manter.

    Subclasses definem ``transferencias`` (códigos do catálogo) e o ``to_silver``.
    """

    cadencia = "mensal"
    #: Códigos de ``/transferencias`` separados por ``:``.
    transferencias: str

    def discover(self, state: dict[str, Any]) -> list[IngestionJob]:
        entes = list(dict.fromkeys(str(cod).strip() for cod in state.get("entes") or []))
        if not entes:
            return []
        anos: list[int] = state.get("anos") or [date.today().year]
        meses: list[int] = state.get("periodos") or list(range(1, 13))
        invalidos = sorted(set(meses) - set(range(1, 13)))
        if invalidos:
            raise ValueError(f"Meses inválidos: {invalidos}; use valores de 1 a 12")
        versao = state.get("versao") or capture_versao()
        return [
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
            for ano in anos
            for mes in meses
        ]

    @staticmethod
    def _estado_tesouro(cod_ibge: str) -> int:
        try:
            return TESOURO_ESTADO_POR_PREFIXO_IBGE[cod_ibge[:2]]
        except KeyError as exc:
            raise ValueError(f"Prefixo IBGE desconhecido: {cod_ibge!r}") from exc

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
                    f"Código IBGE inválido: {cod_ibge!r}; "
                    "use UF com 2 ou município com 7 dígitos"
                )

        common = {"p_ano": job.ano, "p_mes": mes, "p_transferencia": self.transferencias}
        records: list[dict[str, Any]] = []
        for estado, cod_ibge in estados.items():
            for item in self.client.get_records("por_estados", {**common, "p_estado": estado}):
                records.append({**item, "_cod_ibge": cod_ibge})
        for estado, codigos in municipios_por_estado.items():
            for item in self.client.get_records(
                "por_estado_municipio", {**common, "p_estado": estado}
            ):
                cod = str(first(item, "CO_IBGE", "co_ibge") or "").strip()
                if cod in codigos:
                    records.append({**item, "_cod_ibge": cod})
        return records


class FpmConnector(TransferenciasApiConnector):
    """FPM, FPE e FPM 1% — o repasse que sustenta o caixa da maioria dos municípios."""

    fonte = FONTE_FPM
    relatorio = "FPM"
    transferencias = FPM_TRANSFERENCIAS

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        mes = int(job.params["num"])
        totais: dict[str, Decimal] = defaultdict(Decimal)
        for item in payload:
            cod_ibge = str(item.get("_cod_ibge") or "").strip()
            valor = num(first(item, "valor", "VALOR"))
            if cod_ibge and valor is not None:
                totais[cod_ibge] += valor
        rows = [
            {
                "cod_ibge": cod_ibge,
                "ano": job.ano,
                "mes": mes,
                # A API entrega o repasse consolidado do mês: não separa decêndio nem
                # bruto/deduções. Deixar nulo é dizer que não sabemos, e não zero.
                "decendio": None,
                "valor_bruto": None,
                "deducoes": None,
                "valor_liquido": valor,
                "valid_time": job.valid_time,
                "versao_entrega": versao_entrega,
            }
            for cod_ibge, valor in sorted(totais.items())
        ]
        return repository.replace_silver_rows(
            session,
            TesouroFpm,
            keys={"ano": job.ano, "mes": mes, "versao_entrega": versao_entrega},
            rows=rows,
        )


class FundebConnector(TransferenciasApiConnector):
    """Materializa os repasses mensais reais do FUNDEB por ente."""

    fonte = FONTE_FUNDEB
    relatorio = "FUNDEB"
    transferencias = FUNDEB_TRANSFERENCIAS

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


class TransferenciaGenericaConnector(TransferenciasApiConnector):
    """Demais transferências constitucionais: CIDE, ITR, Lei Kandir, royalties, IPI-Exp…

    Diferente do FPM e do FUNDEB, aqui o **tipo importa**: royalties e Lei Kandir têm
    naturezas distintas e o gestor precisa distingui-los na receita. Por isso o valor não é
    somado por ente — é somado por (ente, tipo), preservando o nome que o Tesouro publica.
    """

    fonte = FONTE_TRANSFERENCIA
    relatorio = "TRANSFERENCIA"
    transferencias = OUTRAS_TRANSFERENCIAS

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        mes = int(job.params["num"])
        totais: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        for item in payload:
            cod_ibge = str(item.get("_cod_ibge") or "").strip()
            tipo = str(first(item, "transferencia", "TRANSFERENCIA") or "").strip()
            valor = num(first(item, "valor", "VALOR"))
            if cod_ibge and tipo and valor is not None:
                totais[(cod_ibge, tipo)] += valor
        rows = [
            {
                "cod_ibge": cod_ibge,
                "tipo": tipo,
                "ano": job.ano,
                "mes": mes,
                "valor": valor,
                "fonte": "Tesouro Nacional — Transferências Constitucionais",
                "valid_time": job.valid_time,
                "versao_entrega": versao_entrega,
            }
            for (cod_ibge, tipo), valor in sorted(totais.items())
        ]
        return repository.replace_silver_rows(
            session,
            TransferenciaGenerica,
            keys={"ano": job.ano, "mes": mes, "versao_entrega": versao_entrega},
            rows=rows,
        )


CONNECTORS: dict[str, type[BaseConnector]] = {
    FONTE_FPM: FpmConnector,
    FONTE_FUNDEB: FundebConnector,
    FONTE_TRANSFERENCIA: TransferenciaGenericaConnector,
}
