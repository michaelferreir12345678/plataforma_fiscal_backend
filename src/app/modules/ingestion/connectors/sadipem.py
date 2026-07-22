"""Conector SADIPEM (Tesouro — dívida e operações de crédito). Cadência diária.

Fonte: API REST pública do Tesouro —
https://apidatalake.tesouro.gov.br/ords/cdwhprd/sadipem/tt/

A API real expõe operações contratadas dentro de ``/pvl`` (flag
``pvl_contratado_credor``; alguns payloads usam a grafia incorreta
``pvl_contradado_credor``). O cronograma é obtido por ``id_pleito`` em
``/opc-cronograma-pagamentos``. Mantemos aliases dos layouts antigos no parser.
Consome: Sprint 8 (Dívida).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.modules.ingestion import repository
from app.modules.ingestion.connectors._parsing import boolean, first, num, parse_date
from app.modules.ingestion.models import (
    FONTE_SADIPEM_CDP,
    FONTE_SADIPEM_CRONOGRAMA,
    FONTE_SADIPEM_OP,
    FONTE_SADIPEM_PVL,
    SadipemCdp,
    SadipemCronogramaPgto,
    SadipemOpContratada,
    SadipemPvl,
)
from app.shared.ingestion.base import BaseConnector, IngestionJob, capture_versao

_PVL_PATH = "pvl"
_CRONOGRAMA_PATH = "opc-cronograma-pagamentos"


def _id_pleito(item: dict[str, Any]) -> Any:
    """Identificador do pleito nos layouts real e legado."""
    return first(item, "id_pleito", "id_pvl", "id_operacao", "id")


def _operacao_contratada(item: dict[str, Any]) -> bool:
    """Reconhece somente PVL contratado, tolerando a grafia publicada pela API.

    O endpoint legado dedicado a operações não trazia a flag. Para manter replay e
    fixtures antigas, registros inequivocamente legados (``id_operacao`` ou
    ``valor_contratado``) continuam aceitos.
    """
    flag = first(item, "pvl_contratado_credor", "pvl_contradado_credor")
    if flag is not None:
        return boolean(flag) is True
    return "id_operacao" in item or "valor_contratado" in item


class SadipemConnectorBase(BaseConnector):
    """Base SADIPEM: extract ORDS por ente/ano; versão = data de captura (ou informada)."""

    path: str
    silver_model: type
    silver_keys: tuple[str, ...] = ("cod_ibge", "valid_time", "versao_entrega")

    def extract(self, job: IngestionJob) -> Any:
        return self.client.get_records(self.path, job.params)

    def discover(self, state: dict[str, Any]) -> list[IngestionJob]:
        entes: list[str] = state.get("entes") or []
        anos: list[int] = state.get("anos") or [date.today().year]
        versao = state.get("versao") or capture_versao()
        homologada_em = state.get("homologada_em")
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
                        homologada_em=homologada_em,
                        valid_time=date(ano, 12, 31),
                        # O filtro oficial do SADIPEM é ``id_ente``. ``ano`` identifica
                        # a fotografia/entrega no medallion, não é filtro do endpoint.
                        params={"id_ente": cod_ibge},
                    )
                )
        return jobs

    def _replace(self, session: Session, job: IngestionJob, versao: str, rows: list[dict]) -> int:
        # A mesma captura pode materializar mais de um ano de referência. Sem
        # ``valid_time`` na chave, processar o ano seguinte apagava silenciosamente
        # a fotografia do ano anterior.
        keys = {
            "cod_ibge": job.cod_ibge,
            "valid_time": job.valid_time,
            "versao_entrega": versao,
        }
        return repository.replace_silver_rows(session, self.silver_model, keys=keys, rows=rows)


class SadipemPvlConnector(SadipemConnectorBase):
    fonte = FONTE_SADIPEM_PVL
    relatorio = "SADIPEM-PVL"
    path = _PVL_PATH
    silver_model = SadipemPvl

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        rows = [
            {
                "id_pvl": str(_id_pleito(it) or ""),
                "cod_ibge": job.cod_ibge,
                "tipo_operacao": first(it, "tipo_operacao", "tipo"),
                "valor": num(first(it, "valor", "vl_operacao")),
                "status": first(it, "status", "situacao"),
                "decisao": first(it, "decisao", "resultado"),
                "data_analise": parse_date(first(it, "data_analise", "data_status")),
                "valid_time": job.valid_time,
                "versao_entrega": versao_entrega,
            }
            for it in payload
        ]
        return self._replace(session, job, versao_entrega, rows)


class SadipemOpContratadaConnector(SadipemConnectorBase):
    fonte = FONTE_SADIPEM_OP
    relatorio = "SADIPEM-OP"
    path = _PVL_PATH
    silver_model = SadipemOpContratada

    def extract(self, job: IngestionJob) -> Any:
        """Lê ``/pvl?id_ente=...`` e retém apenas operações contratadas."""
        payload = super().extract(job)
        return [item for item in payload if _operacao_contratada(item)]

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        rows = [
            {
                "id_operacao": str(_id_pleito(it) or ""),
                "cod_ibge": job.cod_ibge,
                "tipo_operacao": first(it, "tipo_operacao", "tipo"),
                "credor": first(it, "credor", "no_credor"),
                "moeda": first(it, "moeda", "no_moeda"),
                "valor_contratado": num(first(it, "valor_contratado", "valor")),
                # O /pvl não publica data contratual dedicada; ``data_status`` é
                # preservada como melhor marco temporal disponível no dataset oficial.
                "data_contratacao": parse_date(
                    first(it, "data_contratacao", "data", "data_status")
                ),
                "valid_time": job.valid_time,
                "versao_entrega": versao_entrega,
            }
            for it in payload
        ]
        return self._replace(session, job, versao_entrega, rows)


class SadipemCronogramaConnector(SadipemConnectorBase):
    fonte = FONTE_SADIPEM_CRONOGRAMA
    relatorio = "SADIPEM-CRONOGRAMA"
    path = _CRONOGRAMA_PATH
    silver_model = SadipemCronogramaPgto

    def extract(self, job: IngestionJob) -> Any:
        """Busca o cronograma de cada PVL contratado do ente.

        ``/opc-cronograma-pagamentos`` exige ``id_pleito``; por isso a descoberta
        começa em ``/pvl?id_ente=...`` e faz uma consulta apenas para cada operação
        marcada como contratada.
        """
        pvls = self.client.get_records(_PVL_PATH, job.params)
        contratados = [pvl for pvl in pvls if _operacao_contratada(pvl)]
        # Cada PVL contém uma fotografia do cronograma consolidado do ente. Somar
        # fotografias de análises distintas multiplicaria a mesma dívida. Quando a
        # API fornece ``data_status``, usa somente a fotografia contratada mais recente.
        datados = [
            (data, pvl)
            for pvl in contratados
            if (data := parse_date(first(pvl, "data_status", "data_analise"))) is not None
        ]
        if datados:
            contratados = [max(datados, key=lambda item: item[0])[1]]
        rows: list[dict[str, Any]] = []
        for pvl in contratados:
            pleito = _id_pleito(pvl)
            if pleito is None:
                continue
            for item in self.client.get_records(self.path, {"id_pleito": pleito}):
                enriched = dict(item)
                # Layouts atuais já trazem id_pleito; o campo normalizado também
                # permite reprocessar respostas/fixtures antigas sem esse identificador.
                enriched.setdefault("id_operacao", str(pleito))
                rows.append(enriched)
        return rows

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        rows: list[dict[str, Any]] = []
        for it in payload:
            ano_raw = first(it, "ano")
            # A API oficial acrescenta uma linha-resumo ``Restante a pagar``. Ela
            # não é um vencimento anual e não pode ser atribuída a um ano fictício.
            try:
                ano = int(ano_raw or job.ano)
            except (TypeError, ValueError):
                continue
            rows.append(
                {
                "id_operacao": str(_id_pleito(it) or ""),
                "cod_ibge": job.cod_ibge,
                "ano": ano,
                "mes": int(first(it, "mes") or 0) or None,
                "principal": num(
                    first(
                        it,
                        "principal",
                        "vl_principal",
                        # A documentação e versões do payload divergem na
                        # grafia; ambas representam a amortização total.
                        "total_amorizacao",
                        "total_amortizacao",
                    )
                ),
                "juros": num(first(it, "juros", "vl_juros", "total_juros")),
                "encargos": num(first(it, "encargos", "vl_encargos", "total_encargos")),
                "valid_time": job.valid_time,
                "versao_entrega": versao_entrega,
                }
            )
        return self._replace(session, job, versao_entrega, rows)


class SadipemCdpConnector(SadipemConnectorBase):
    fonte = FONTE_SADIPEM_CDP
    relatorio = "SADIPEM-CDP"
    path = "res-cdp"
    silver_model = SadipemCdp

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        rows = [
            {
                "cod_ibge": job.cod_ibge,
                "data_ref": parse_date(first(it, "data_ref", "data_base", "data")),
                "situacao": first(it, "situacao", "situacao_ente", "status"),
                "motivo": first(it, "motivo", "descricao", "status"),
                "valid_time": job.valid_time,
                "versao_entrega": versao_entrega,
            }
            for it in payload
        ]
        return self._replace(session, job, versao_entrega, rows)


CONNECTORS: dict[str, type[BaseConnector]] = {
    FONTE_SADIPEM_PVL: SadipemPvlConnector,
    FONTE_SADIPEM_OP: SadipemOpContratadaConnector,
    FONTE_SADIPEM_CRONOGRAMA: SadipemCronogramaConnector,
    FONTE_SADIPEM_CDP: SadipemCdpConnector,
}
