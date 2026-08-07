"""Motor de backfill com checkpoint (Sprint 21).

O backfill âncora (CE completo, 2021→atual) faz milhares de requisições ao SICONFI e não
cabe no ``POST /run`` síncrono (estoura timeout HTTP). Este motor executa as unidades de
carga **fora do request**, com:

- **Checkpoint** em arquivo JSON: cada unidade concluída é marcada; re-executar retoma de
  onde parou (além da idempotência do medallion, que já evita duplicar dados).
- **Commit por unidade**: transações pequenas; uma falha isolada não perde o lote inteiro.
- **Guarda de disco**: interrompe com elegância se o volume livre cair abaixo do limite
  (o checkpoint é preservado; basta liberar espaço e retomar).
- **Rate-limit** herdado do cliente (~6 req/s no SICONFI, com backoff).

Cada *unidade* é um ``RunRequest`` com uma chave estável. O CLI (``scripts.backfill_*``)
monta o plano; o motor apenas o executa de forma resiliente.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.modules.ingestion import service
from app.modules.ingestion.connectors.registry import CONNECTOR_REGISTRY
from app.modules.ingestion.models import FONTE_MSC, FONTE_SIOPE, FONTE_SIOPS, IngestionLog
from app.modules.ingestion.schemas import RunRequest
from app.shared.ingestion.base import BaseConnector, EntregaInfo, IngestionJob
from app.shared.ingestion.client import (
    DEFAULT_MAX_PER_SECOND,
    ClientResolver,
    RealClientResolver,
)


@dataclass(frozen=True)
class BackfillUnit:
    """Uma unidade idempotente de carga (chave estável + requisição)."""

    key: str
    req: RunRequest


@dataclass
class Checkpoint:
    """Checkpoint em arquivo: chaves concluídas + agregados de progresso."""

    path: Path
    done: set[str] = field(default_factory=set)
    stats: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> Checkpoint:
        p = Path(path)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return cls(path=p, done=set(data.get("done", [])), stats=data.get("stats", {}))
        return cls(path=p)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"done": sorted(self.done), "stats": self.stats}, ensure_ascii=False),
            encoding="utf-8",
        )

    def add(self, key: str) -> None:
        self.done.add(key)

    def bump(self, **deltas: int) -> None:
        for k, v in deltas.items():
            self.stats[k] = self.stats.get(k, 0) + v


def _free_gb(path: str) -> float:
    try:
        return shutil.disk_usage(path).free / (1024**3)
    except OSError:
        return float("inf")


def _close(resolver: ClientResolver) -> None:
    """Fecha o resolver se ele expuser ``close`` (o ClientResolver-protocol não o exige)."""
    close = getattr(resolver, "close", None)
    if callable(close):
        close()


def _log_falha(unit: BackfillUnit, exc: Exception) -> None:
    """Registra a unidade que falhou em ``gold.ingestion_log`` (status ``erro``).

    A falha precisa ficar visível no mesmo lugar que os sucessos — senão o backfill
    "termina" escondendo o que não entrou.
    """
    try:
        with SessionLocal() as session:
            session.add(
                IngestionLog(
                    fonte=unit.req.fonte,
                    cod_ibge=(unit.req.entes[0] if unit.req.entes else None),
                    periodo=(str(unit.req.anos[0]) if unit.req.anos else None),
                    status="erro",
                    mensagem=f"{unit.key}: {exc.__class__.__name__}: {exc}"[:2000],
                )
            )
            session.commit()
    except Exception:  # noqa: BLE001 — logging nunca pode derrubar o backfill
        pass


@dataclass
class BackfillResult:
    total: int
    executados: int
    pulados: int
    ingeridos: int
    silver_rows: int
    interrompido: bool
    motivo: str | None = None
    falhas: int = 0
    # (chave da unidade, tipo do erro) — para o operador ver o que não entrou.
    erros: list[tuple[str, str]] = field(default_factory=list)


def run_backfill(
    units: Iterable[BackfillUnit],
    *,
    checkpoint_path: str | Path,
    resolver: ClientResolver | None = None,
    disk_guard_path: str | None = None,
    min_free_gb: float = 1.0,
    force: bool = False,
    save_every: int = 5,
    on_progress: Any | None = None,
) -> BackfillResult:
    """Executa as unidades com checkpoint, commit por unidade e guarda de disco."""
    units = list(units)
    ckpt = Checkpoint.load(checkpoint_path)
    own_resolver = resolver is None
    resolver = resolver or RealClientResolver()
    executados = pulados = ingeridos = silver_rows = falhas = 0
    erros: list[tuple[str, str]] = []
    interrompido = False
    motivo: str | None = None
    try:
        for i, unit in enumerate(units, start=1):
            if not force and unit.key in ckpt.done:
                pulados += 1
                continue
            if disk_guard_path is not None and _free_gb(disk_guard_path) < min_free_gb:
                interrompido = True
                motivo = (
                    f"Disco abaixo de {min_free_gb} GB livre em {disk_guard_path}; "
                    "checkpoint preservado — libere espaço e retome."
                )
                break
            try:
                with SessionLocal() as session:
                    result = service.run(session, resolver, unit.req)
                    session.commit()
            except Exception as exc:  # noqa: BLE001
                # Isolamento por unidade: num lote de milhares, um ente/período que a
                # fonte recusa (4xx, layout mudado) não pode derrubar o backfill inteiro.
                # A unidade **não** entra no checkpoint — será retentada na próxima corrida.
                falhas += 1
                erros.append((unit.key, exc.__class__.__name__))
                _log_falha(unit, exc)
                continue
            executados += 1
            ingeridos += result.ingeridos
            silver_rows += result.silver_rows
            ckpt.add(unit.key)
            ckpt.bump(executados=1, ingeridos=result.ingeridos, silver_rows=result.silver_rows)
            if on_progress is not None:
                on_progress(i, len(units), unit, result)
            if executados % save_every == 0:
                ckpt.save()
    finally:
        ckpt.save()
        if own_resolver:
            _close(resolver)
    return BackfillResult(
        total=len(units),
        executados=executados,
        pulados=pulados,
        ingeridos=ingeridos,
        silver_rows=silver_rows,
        interrompido=interrompido,
        motivo=motivo,
        falhas=falhas,
        erros=erros,
    )


# ---------------------------------------------------------------------------------
# --dry-run (Sprint A4_MSC/A4_SIOPS): mede o plano sem tocar rede, banco ou checkpoint.
# ---------------------------------------------------------------------------------


class _DryRunNetworkGuard:
    """``RecordsClient``/``FileClient`` de sentinela: nenhum conector deveria chamar a rede
    durante um dry-run. Se algum ``discover()`` um dia passar a depender do cliente (hoje
    nenhum depende — só ``extract`` usa), a chamada falha alto e explícito em vez de
    silenciosamente sair à rede real dentro de uma medição que promete não fazer isso.
    """

    def get_records(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        raise RuntimeError(
            f"--dry-run tentou chamar a rede (get_records {path!r}); isso não deveria "
            "acontecer — o estimador só chama discover(), nunca extract()."
        )

    def fetch(self, params: dict[str, Any]) -> bytes:
        raise RuntimeError(
            "--dry-run tentou baixar um arquivo (fetch); isso não deveria acontecer — o "
            "estimador só chama discover(), nunca prepare()/extract()."
        )


class _DryRunWriteGuard:
    """``MedallionSink`` de sentinela: nenhuma gravação deveria acontecer durante um
    dry-run (nem bronze, nem ``gold.dim_entrega``, nem ``gold.ingestion_log``).
    """

    def upsert_bronze(
        self, session: Session, job: IngestionJob, payload: Any, hash_: str
    ) -> bool:
        raise RuntimeError("--dry-run tentou gravar bronze (upsert_bronze) — bloqueado.")

    def register_entrega(self, session: Session, job: IngestionJob, hash_: str) -> EntregaInfo:
        raise RuntimeError("--dry-run tentou registrar entrega (register_entrega) — bloqueado.")

    def log_ingestion(
        self, session: Session, job: IngestionJob, status: str, mensagem: str | None = None
    ) -> None:
        raise RuntimeError("--dry-run tentou gravar log (log_ingestion) — bloqueado.")


def _http_calls_per_job(fonte: str, connector: BaseConnector, job: IngestionJob) -> int:
    """Nº de chamadas HTTP que ``connector.extract(job)`` faria de verdade por job.

    Deriva do próprio conector/job — não é um valor solto:

    - **MSC** (``FONTE_MSC``): ``extract`` percorre ``classe_conta × id_tv``
      (``connectors/siconfi.py:321-329``); o número vem de
      ``len(connector.classes) * len(connector.tipos_valor)`` (hoje 4×3=12), então uma
      mudança nos atributos do conector muda a estimativa junto — nunca diverge dele.
    - **SIOPS/SIOPE**: ``extract`` faz 1 chamada por ente em ``job.params["entes"]`` (o
      job pode agrupar vários entes — ver o motivo em
      ``scripts/backfill_msc_siops_siope.py``); o número vem do próprio job, não de uma
      contagem fixa.
    - Demais fontes: ``SiconfiConnectorBase.extract`` (o padrão herdado) faz 1 chamada por
      job — verdadeiro para RREO/DCA/extratos/entes. **Não** é verdadeiro para o RGF (que
      consulta um poder por vez, ``connectors/siconfi.py::RgfConnector.extract``); nenhuma
      das três fontes desta sprint é o RGF, mas o resultado subestimaria o custo dele se
      algum dia entrar no mesmo plano.
    """
    if fonte == FONTE_MSC:
        classes = getattr(connector, "classes", ())
        tipos_valor = getattr(connector, "tipos_valor", ())
        return max(len(classes) * len(tipos_valor), 1)
    if fonte in (FONTE_SIOPS, FONTE_SIOPE):
        entes = job.params.get("entes")
        if isinstance(entes, list) and entes:
            return len(entes)
        return 1
    return 1


@dataclass(frozen=True)
class UnitEstimate:
    """Estimativa de UMA unidade do plano: jobs que ``discover()`` geraria + chamadas HTTP."""

    key: str
    fonte: str
    n_entes: int
    ano: int | None
    jobs: int
    chamadas_http: int


@dataclass(frozen=True)
class BackfillEstimate:
    """Relatório do ``--dry-run``. Nunca resulta de gravação — só leitura/computação pura."""

    unidades: tuple[UnitEstimate, ...]
    max_per_second: float

    @property
    def total_unidades(self) -> int:
        return len(self.unidades)

    @property
    def total_jobs(self) -> int:
        return sum(u.jobs for u in self.unidades)

    @property
    def total_chamadas_http(self) -> int:
        return sum(u.chamadas_http for u in self.unidades)

    @property
    def tempo_estimado_segundos(self) -> float:
        """Piso ingênuo: ``chamadas / rate-limit``. Não soma latência de resposta nem
        espera de backoff em erro — a execução real leva **mais** tempo que isto, nunca
        menos. É deliberadamente uma estimativa conservadora-por-baixo, não uma previsão.
        """
        if self.max_per_second <= 0:
            return 0.0
        return self.total_chamadas_http / self.max_per_second

    def por_fonte(self) -> dict[str, dict[str, float]]:
        """Agregado por fonte: unidades, jobs, chamadas e tempo estimado (segundos)."""
        agg: dict[str, dict[str, float]] = {}
        for u in self.unidades:
            linha = agg.setdefault(
                u.fonte, {"unidades": 0.0, "jobs": 0.0, "chamadas_http": 0.0}
            )
            linha["unidades"] += 1
            linha["jobs"] += u.jobs
            linha["chamadas_http"] += u.chamadas_http
        for linha in agg.values():
            linha["tempo_estimado_segundos"] = (
                linha["chamadas_http"] / self.max_per_second if self.max_per_second > 0 else 0.0
            )
        return dict(sorted(agg.items()))

    def por_fonte_ano(self) -> dict[tuple[str, int | None], dict[str, float]]:
        """Agregado por (fonte, ano) — a granularidade que o documento da sprint pede
        ("nacional e por ano de histórico disponível")."""
        agg: dict[tuple[str, int | None], dict[str, float]] = {}
        for u in self.unidades:
            linha = agg.setdefault(
                (u.fonte, u.ano), {"unidades": 0.0, "jobs": 0.0, "chamadas_http": 0.0}
            )
            linha["unidades"] += 1
            linha["jobs"] += u.jobs
            linha["chamadas_http"] += u.chamadas_http
        for linha in agg.values():
            linha["tempo_estimado_segundos"] = (
                linha["chamadas_http"] / self.max_per_second if self.max_per_second > 0 else 0.0
            )
        return dict(sorted(agg.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)))


def estimate_backfill(
    units: Iterable[BackfillUnit],
    *,
    max_per_second: float = DEFAULT_MAX_PER_SECOND,
) -> BackfillEstimate:
    """Mede o plano (unidades, jobs, chamadas HTTP, tempo estimado) SEM tocar rede, banco
    ou checkpoint — o modo ``--dry-run`` do backfill (Sprint A4_MSC/A4_SIOPS).

    Chama ``connector.discover(state)`` de verdade — a MESMA função que ``service.run``
    chamaria — porque é código puro (enumera jobs a partir de um dict; nenhum conector do
    registry toca rede ou sessão dentro de ``discover``) e é a única forma de saber quantos
    jobs um ``RunRequest`` geraria sem reimplementar a aritmética de período em duplicata
    (que já vive em ``periodo_util``/``periodos_do_ano`` e divergiria com o tempo).

    O cliente e o sink injetados no conector são sentinelas (:class:`_DryRunNetworkGuard`,
    :class:`_DryRunWriteGuard`): se ``extract``/``to_bronze``/``to_silver`` forem chamados
    por engano, a função falha alto em vez de gravar ou sair à rede em silêncio. Não abre
    ``SessionLocal`` nenhuma, não instancia ``Checkpoint`` nenhum, não usa ``ClientResolver``.
    """
    connectors: dict[str, BaseConnector] = {}
    estimativas: list[UnitEstimate] = []
    for unit in units:
        fonte = unit.req.fonte
        connector = connectors.get(fonte)
        if connector is None:
            cls = CONNECTOR_REGISTRY.get(fonte)
            if cls is None:
                raise ValueError(
                    f"Fonte desconhecida no --dry-run: {fonte!r}. "
                    f"Válidas: {sorted(CONNECTOR_REGISTRY)}"
                )
            connector = cls(_DryRunNetworkGuard(), _DryRunWriteGuard())
            connectors[fonte] = connector

        # Mesma preparação de estado que ``service.run`` faz antes de ``discover`` (§6.7) —
        # sem a chave ``session``, que nenhum discover() destas fontes usa; se algum dia
        # usar, o KeyError é um sinal claro de que o dry-run precisa ser revisto.
        state: dict[str, Any] = unit.req.model_dump()
        state.update(state.pop("params", {}) or {})

        jobs = connector.discover(state)
        chamadas = sum(_http_calls_per_job(fonte, connector, job) for job in jobs)
        estimativas.append(
            UnitEstimate(
                key=unit.key,
                fonte=fonte,
                n_entes=len(unit.req.entes),
                ano=unit.req.anos[0] if unit.req.anos else None,
                jobs=len(jobs),
                chamadas_http=chamadas,
            )
        )
    return BackfillEstimate(unidades=tuple(estimativas), max_per_second=max_per_second)


def _fmt_horas(segundos: float) -> str:
    horas = segundos / 3600.0
    if horas < 1:
        return f"{segundos / 60.0:.1f} min"
    return f"{horas:.1f} h"


def format_estimate_report(estimate: BackfillEstimate) -> str:
    """Relatório humano do ``--dry-run`` — texto simples, sem dependência de lib de tabela
    (mesmo espírito do ``_progress`` usado no backfill real: legível direto no terminal e
    fácil de colar no documento da sprint como evidência).
    """
    linhas = [
        f"total: unidades={estimate.total_unidades} jobs={estimate.total_jobs} "
        f"chamadas_http={estimate.total_chamadas_http} "
        f"tempo_estimado={_fmt_horas(estimate.tempo_estimado_segundos)} "
        f"(rate-limit={estimate.max_per_second:g} req/s)",
        "",
        "por fonte:",
    ]
    for fonte, agg in estimate.por_fonte().items():
        linhas.append(
            f"  {fonte}: unidades={int(agg['unidades'])} jobs={int(agg['jobs'])} "
            f"chamadas_http={int(agg['chamadas_http'])} "
            f"tempo_estimado={_fmt_horas(agg['tempo_estimado_segundos'])}"
        )
    linhas.append("")
    linhas.append("por fonte e ano:")
    for (fonte, ano), agg in estimate.por_fonte_ano().items():
        linhas.append(
            f"  {fonte} {ano if ano is not None else '(sem ano)'}: "
            f"unidades={int(agg['unidades'])} jobs={int(agg['jobs'])} "
            f"chamadas_http={int(agg['chamadas_http'])} "
            f"tempo_estimado={_fmt_horas(agg['tempo_estimado_segundos'])}"
        )
    return "\n".join(linhas)


def sweep_retificacoes(
    entes: Iterable[str],
    *,
    resolver: ClientResolver | None = None,
) -> dict[str, int]:
    """Varre retificações (via extratos) de cada ente; retorna agregados por status."""
    own = resolver is None
    resolver = resolver or RealClientResolver()
    agg = {"ingeridos": 0, "pulados": 0, "silver_rows": 0}
    try:
        for ente in entes:
            with SessionLocal() as session:
                res = service.varrer_retificacoes(session, resolver, ente=ente)
                session.commit()
            agg["ingeridos"] += res.ingeridos
            agg["pulados"] += res.pulados
            agg["silver_rows"] += res.silver_rows
    finally:
        if own:
            _close(resolver)
    return agg
