"""Framework de ingestão reusável (§6.7).

Define o contrato ``BaseConnector`` com o ciclo
``discover → extract → to_bronze → to_silver → mark_done``, idempotência por
``(fonte, ente, periodo, versao)`` e observabilidade. **Toda fonte** (SICONFI e as
complementares da Sprint 1B) implementa esta interface — nenhuma reinventa o framework.

O framework é agnóstico de persistência: fala com um ``MedallionSink`` (porta), cuja
implementação concreta vive em ``modules/ingestion/repository.py``. Assim ``shared`` não
depende de um módulo específico.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class IngestionJob:
    """Unidade idempotente de ingestão. Chave: ``(fonte, cod_ibge, periodo, versao)``."""

    fonte: str
    relatorio: str
    cod_ibge: str
    ano: int
    periodo: str
    versao: str = "1"
    homologada_em: datetime | None = None
    valid_time: date | None = None
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def chave(self) -> tuple[str, str, str, str]:
        return (self.fonte, self.cod_ibge, self.periodo, self.versao)


@dataclass(frozen=True)
class EntregaInfo:
    """Resultado do controle de versões (gold.dim_entrega)."""

    versao_entrega: str
    vigente: bool


@dataclass
class IngestionResult:
    job: IngestionJob
    is_new: bool
    versao_entrega: str
    vigente: bool
    silver_rows: int
    status: str  # "ingested" | "skipped" | "vazio"


def payload_vazio(payload: Any) -> bool:
    """Fonte sem dado publicado: lista vazia (ou dict cujos valores são todos vazios)."""
    if payload is None:
        return True
    if isinstance(payload, list):
        return len(payload) == 0
    if isinstance(payload, dict):
        return all(payload_vazio(v) for v in payload.values())
    return False


def payload_hash(payload: Any) -> str:
    """Hash estável (sha256) do payload — detecta mudança de conteúdo por versão."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def capture_versao(today: date | None = None) -> str:
    """Versão baseada na data de captura (``YYYYMMDD``).

    Fontes que não informam versão própria usam a data de captura como ``versao_entrega``,
    mantendo o modelo bitemporal uniforme (§ regra geral da Sprint 1B).
    """
    from datetime import date as _date

    return (today or _date.today()).strftime("%Y%m%d")


class MedallionSink(Protocol):
    """Porta de persistência do medallion (bronze/gold.dim_entrega/log)."""

    def upsert_bronze(self, session: Session, job: IngestionJob, payload: Any, hash_: str) -> bool:
        """Grava o payload cru (idempotente). Retorna ``True`` se inseriu (novo)."""
        ...

    def register_entrega(self, session: Session, job: IngestionJob, hash_: str) -> EntregaInfo:
        """Registra/atualiza a entrega em gold.dim_entrega (retificação supera vigente)."""
        ...

    def log_ingestion(
        self, session: Session, job: IngestionJob, status: str, mensagem: str | None = None
    ) -> None:
        """Registra o resultado do job em gold.ingestion_log."""
        ...


class BaseConnector(ABC):
    """Conector base. Subclasses definem ``fonte``/``relatorio`` e o parsing silver."""

    fonte: str
    relatorio: str

    def __init__(self, client: Any, sink: MedallionSink) -> None:
        # ``client`` é um RecordsClient (JSON) ou um FileClient (planilhas) conforme a fonte.
        self.client = client
        self.sink = sink

    # --- Passos que cada fonte especializa ---
    @abstractmethod
    def discover(self, state: dict[str, Any]) -> list[IngestionJob]:
        """Enumera os jobs a executar a partir de um estado (entes/períodos/versões alvo)."""

    @abstractmethod
    def extract(self, job: IngestionJob) -> Any:
        """Extrai o payload cru da fonte (usa o cliente HTTP injetado na subclasse)."""

    @abstractmethod
    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        """Materializa o payload no silver tipado (substitui as linhas da versão)."""

    def prepare(self, job: IngestionJob) -> tuple[IngestionJob, Any]:
        """Obtém o payload e ajusta o job antes do ciclo (hook).

        Default: apenas ``extract``. Fontes baseadas em arquivo sobrescrevem para baixar
        o arquivo e fixar ``versao = checksum`` do conteúdo antes de gravar o bronze.
        """
        return job, self.extract(job)

    # --- Template method: orquestra o ciclo idempotente ---
    def run(
        self,
        session: Session,
        job: IngestionJob,
        *,
        force: bool = False,
        payload_pronto: Any | None = None,
    ) -> IngestionResult:
        """Executa ``prepare → to_bronze → to_silver → mark_done`` de forma idempotente.

        - Rodar 2x com a mesma chave não duplica (bronze faz *no-op* e o silver é pulado).
        - Uma nova ``versao`` (retificação) cria nova entrega vigente sem apagar o histórico.
        - ``force=True`` reprocessa o silver mesmo sem novo bronze (usado no replay).
        - Payload **vazio** (fonte ainda não publicou o período) não vira entrega: uma
          entrega vigente sem linha alguma mascararia a lacuna da fonte como "dado zero".
        - ``payload_pronto`` injeta um payload já extraído (a varredura de retificações
          extrai uma vez para comparar o hash e reaproveita aqui, sem 2ª ida à rede).
        """
        if payload_pronto is not None:
            payload = payload_pronto
        else:
            job, payload = self.prepare(job)
        hash_ = payload_hash(payload)

        if payload_vazio(payload):
            self.sink.log_ingestion(session, job, "vazio", "Fonte sem dado para o período.")
            return IngestionResult(
                job=job,
                is_new=False,
                versao_entrega=job.versao,
                vigente=False,
                silver_rows=0,
                status="vazio",
            )

        is_new = self.sink.upsert_bronze(session, job, payload, hash_)
        entrega = self.sink.register_entrega(session, job, hash_)

        silver_rows = 0
        if is_new or force:
            silver_rows = self.to_silver(session, job, payload, entrega.versao_entrega)
            status = "ingested"
        else:
            status = "skipped"

        self.sink.log_ingestion(session, job, status)
        return IngestionResult(
            job=job,
            is_new=is_new,
            versao_entrega=entrega.versao_entrega,
            vigente=entrega.vigente,
            silver_rows=silver_rows,
            status=status,
        )
