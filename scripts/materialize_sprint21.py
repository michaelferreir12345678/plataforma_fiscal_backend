"""Materialização completa da gold no escopo do backfill (Sprint 21).

Troca a materialização **lazy** (por request) por materialização **por worker**: percorre
os entes do escopo e pré-calcula todos os fatos/marts de todos os períodos com dado
(o lazy dos módulos permanece apenas como *fallback*). Inclui a derivação da cota-parte
de transferências estaduais (ICMS) do RREO A1, a CAPAG em massa e o refresh da cobertura.

Uso::

    python -m scripts.materialize_sprint21 --uf 23            # todos os entes da UF
    python -m scripts.materialize_sprint21 --entes 23,2304400
    python -m scripts.materialize_sprint21 --uf 23 --benchmark
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.core.db import SessionLocal  # noqa: E402
from app.modules.ingestion import cobertura as cobertura_mod  # noqa: E402
from app.modules.ingestion import derivacoes  # noqa: E402
from app.modules.ingestion.models import DimEntrega  # noqa: E402
from app.workers import materialize  # noqa: E402


def _entes_com_entrega(uf: str | None) -> list[str]:
    """Entes que têm alguma entrega vigente (opcionalmente restritos ao prefixo de UF)."""
    with SessionLocal() as session:
        stmt = select(DimEntrega.cod_ibge).where(DimEntrega.vigente.is_(True)).distinct()
        if uf:
            stmt = stmt.where(func.substr(DimEntrega.cod_ibge, 1, 2) == uf)
        return sorted(c for c in session.scalars(stmt) if c and c != "BR")


def _progress(i: int, total: int, cod: str, stats: dict[str, int]) -> None:
    print(f"[{i}/{total}] {cod} -> {stats}", flush=True)


def run() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uf", default=None, help="Prefixo IBGE da UF (ex.: 23 = CE).")
    p.add_argument("--entes", default=None, help="Lista explícita separada por vírgula.")
    p.add_argument("--benchmark", action="store_true", help="Materializa mart_benchmark também.")
    p.add_argument("--sem-icms", action="store_true", help="Pula a derivação da cota-parte.")
    args = p.parse_args()

    entes = (
        [e.strip() for e in args.entes.split(",") if e.strip()]
        if args.entes
        else _entes_com_entrega(args.uf)
    )
    print(f"materializando {len(entes)} entes", flush=True)

    total = materialize.materialize_scope(entes, on_progress=_progress)
    print(f"fatos/marts: {total}", flush=True)

    if not args.sem_icms:
        derivadas = 0
        for cod in entes:
            with SessionLocal() as session:
                derivadas += derivacoes.derivar_icms_ente(session, cod_ibge=cod)
                session.commit()
        print(f"cota-parte (ICMS) derivada do RREO A1: {derivadas} linhas", flush=True)

    with SessionLocal() as session:
        n_capag = materialize.materialize_capag_todos(session)
        session.commit()
    print(f"fato_capag materializados: {n_capag}", flush=True)

    if args.benchmark:
        n_bench = materialize.materialize_benchmark(entes)
        print(f"snapshots de benchmark: {n_bench}", flush=True)

    with SessionLocal() as session:
        cobertura_mod.seed_catalogo(session)
        n_cob = cobertura_mod.refresh_cobertura(session)
        session.commit()
    print(f"cobertura materializada: {n_cob} linhas", flush=True)


if __name__ == "__main__":
    run()
