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

from app.core.db import SessionLocal
from app.modules.ingestion import service
from app.modules.ingestion.models import IngestionLog
from app.modules.ingestion.schemas import RunRequest
from app.shared.ingestion.client import ClientResolver, RealClientResolver


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
