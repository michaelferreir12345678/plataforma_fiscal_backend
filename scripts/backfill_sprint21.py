"""Backfill âncora Ceará (Sprint 21) — worker/script com checkpoint.

Monta o plano de carga (CE 184 municípios + ente estadual 23 + capitais já carregadas,
2021→atual) e o executa pelo motor resiliente de ``app.workers.backfill`` (checkpoint +
commit por unidade + guarda de disco). **Não** usa o ``POST /run`` síncrono.

Cada unidade é (fonte, ente|escopo, ano). Rodar 2× não duplica (idempotência do medallion
+ checkpoint). Rate-limit ~6 req/s herdado do cliente SICONFI.

Uso::

    python -m scripts.backfill_sprint21 --anos 2021-2024 \
        --fontes siconfi_rreo,siconfi_rgf,siconfi_dca --entes-limit 0
    python -m scripts.backfill_sprint21 --so-estado          # só o ente estadual 23
    python -m scripts.backfill_sprint21 --bcb                # só séries BCB (2019→hoje)
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.core.db import SessionLocal  # noqa: E402
from app.modules.ingestion.models import SilverEnte, SilverRreo  # noqa: E402
from app.modules.ingestion.schemas import RunRequest  # noqa: E402
from app.workers import backfill  # noqa: E402

CE_UF = "CE"
CE_ESTADO = "23"
VAR_DIR = Path(__file__).resolve().parents[1] / "var" / "backfill"
BCB_SERIES = [433, 11, 4390, 189]  # IPCA, Selic diária, Selic mensal, IGP-M


def _municipios_ce(session) -> list[str]:
    return sorted(
        session.scalars(
            select(SilverEnte.cod_ibge).where(
                SilverEnte.uf == CE_UF, SilverEnte.esfera == "M"
            )
        )
    )


def _capitais_carregadas(session) -> list[str]:
    """Capitais NE já carregadas (coorte de comparação) = entes distintos no RREO silver."""
    return sorted(set(session.scalars(select(SilverRreo.cod_ibge))))


def _parse_anos(spec: str) -> list[int]:
    if "-" in spec:
        ini, fim = spec.split("-", 1)
        return list(range(int(ini), int(fim) + 1))
    return [int(a) for a in spec.split(",") if a.strip()]


def _escopo_entes(session, args: argparse.Namespace) -> list[str]:
    if args.so_estado:
        return [CE_ESTADO]
    entes = _municipios_ce(session)
    if args.entes_limit and args.entes_limit > 0:
        entes = entes[: args.entes_limit]
    codigos = [CE_ESTADO, *entes, *_capitais_carregadas(session)]
    # dedup preservando ordem (estado primeiro, depois municípios, depois capitais).
    return list(dict.fromkeys(codigos))


def _u(fonte: str, ente: str, ano: int, **extra) -> backfill.BackfillUnit:  # type: ignore[no-untyped-def]
    req = RunRequest(fonte=fonte, entes=[ente], anos=[ano], **extra)
    return backfill.BackfillUnit(key=f"{fonte}:{ente}:{ano}", req=req)


def _plano(session, args: argparse.Namespace) -> list[backfill.BackfillUnit]:
    anos = _parse_anos(args.anos)
    fontes = [f.strip() for f in args.fontes.split(",") if f.strip()]
    entes = _escopo_entes(session, args)
    units: list[backfill.BackfillUnit] = []

    for ente in entes:
        is_estado = len(ente) == 2
        for ano in anos:
            if "siconfi_rreo" in fontes:
                units.append(_u("siconfi_rreo", ente, ano))
            if "siconfi_rgf" in fontes:
                # Quadrimestral (padrão) para todos; semestral adicional p/ municípios
                # (< 50 mil hab. entregam semestral — o vazio é descartado pela guarda).
                units.append(_u("siconfi_rgf", ente, ano))
                if not is_estado:
                    units.append(
                        backfill.BackfillUnit(
                            key=f"siconfi_rgf_s:{ente}:{ano}",
                            req=RunRequest(
                                fonte="siconfi_rgf",
                                entes=[ente],
                                anos=[ano],
                                params={"periodicidade": "S"},
                            ),
                        )
                    )
            if "siconfi_dca" in fontes:
                units.append(_u("siconfi_dca", ente, ano))
            if "siconfi_extratos" in fontes:
                units.append(_u("siconfi_extratos", ente, ano))

    # MSC: só ente estadual CE + Fortaleza, 24 meses (volume!).
    if "siconfi_msc" in fontes:
        for ente in [CE_ESTADO, "2304400"]:
            for ano in anos[-2:]:  # últimos 2 exercícios do intervalo ≈ 24 meses
                units.append(_u("siconfi_msc", ente, ano))

    # IBGE (população/PIB): host próprio (servicodados.ibge.gov.br), portanto pode rodar
    # em paralelo ao SICONFI sem somar ao rate-limit do Tesouro.
    for fonte_ibge in ("ibge_populacao", "ibge_pib"):
        if fonte_ibge not in fontes:
            continue
        for ente in entes:
            if len(ente) != 7:
                continue  # os agregados usados são municipais (N6)
            for ano in anos:
                units.append(_u(fonte_ibge, ente, ano))

    # BCB: séries nacionais 2019→hoje (unidade única).
    if args.bcb or "bcb" in fontes:
        units.append(
            backfill.BackfillUnit(
                key="bcb:series:2019",
                req=RunRequest(
                    fonte="bcb",
                    series=BCB_SERIES,
                    data_inicial=date(2019, 1, 1),
                    data_final=date.today(),
                ),
            )
        )
    return units


def _progress(i: int, total: int, unit: backfill.BackfillUnit, result) -> None:  # type: ignore[no-untyped-def]
    if i % 10 == 0 or result.silver_rows:
        # ``flush``: o backfill roda por horas em background; sem isso o stdout fica
        # bufferizado e o operador não consegue acompanhar o progresso.
        print(
            f"[{i}/{total}] {unit.key} -> ingeridos={result.ingeridos} "
            f"silver={result.silver_rows} pulados={result.pulados}",
            flush=True,
        )


def run() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--anos", default="2021-2024", help="Ex.: 2021-2024 ou 2021,2022")
    p.add_argument(
        "--fontes",
        default="siconfi_rreo,siconfi_rgf,siconfi_dca,siconfi_extratos,siconfi_msc",
    )
    p.add_argument("--entes-limit", type=int, default=0, help="0 = todos os 184 municípios CE.")
    p.add_argument("--so-estado", action="store_true", help="Somente o ente estadual 23.")
    p.add_argument("--bcb", action="store_true", help="Inclui as séries do BCB (2019→hoje).")
    p.add_argument("--checkpoint", default=str(VAR_DIR / "checkpoint.json"))
    p.add_argument("--min-free-gb", type=float, default=2.0)
    p.add_argument("--disk-guard", default="D:/pg_tablespace_fiscal")
    p.add_argument("--force", action="store_true", help="Ignora o checkpoint (reexecuta tudo).")
    args = p.parse_args()

    with SessionLocal() as session:
        units = _plano(session, args)
    print(f"plano: {len(units)} unidades; checkpoint={args.checkpoint}", flush=True)

    res = backfill.run_backfill(
        units,
        checkpoint_path=args.checkpoint,
        disk_guard_path=args.disk_guard,
        min_free_gb=args.min_free_gb,
        force=args.force,
        on_progress=_progress,
    )
    print(
        f"fim: total={res.total} executados={res.executados} pulados={res.pulados} "
        f"ingeridos={res.ingeridos} silver_rows={res.silver_rows} "
        f"falhas={res.falhas} interrompido={res.interrompido}",
        flush=True,
    )
    if res.erros:
        # Falhas não entram no checkpoint: a próxima corrida as retenta.
        print(f"unidades com falha ({len(res.erros)}), serão retentadas:", flush=True)
        for chave, erro in res.erros[:20]:
            print(f"  {chave}: {erro}", flush=True)
    if res.motivo:
        print(res.motivo, flush=True)


if __name__ == "__main__":
    run()
