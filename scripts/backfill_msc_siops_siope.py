"""Backfill nacional de MSC/SIOPS/SIOPE com ``--dry-run`` (Sprint A4_MSC/A4_SIOPS).

A A4 deixou estas três fontes com conector completo e testado, mas cobertura real de
**1 único ente cada** (ver ``docs/evolucao_plataforma.md``, seção "Sprint A4_MSC/A4_SIOPS").
Este script monta o plano **nacional** (todos os municípios + estados + DF do cadastro
``silver.siconfi_entes``) para ``siconfi_msc``, ``siops_saude`` e ``siope_educacao``,
reusando o motor resiliente de ``app.workers.backfill`` (checkpoint, commit por unidade,
guarda de disco, e agora ``estimate_backfill``) — sem reimplementar nada disso.

``--dry-run`` mede o plano (unidades, jobs, chamadas HTTP, tempo estimado) **sem** tocar
rede, banco ou checkpoint — ver ``app.workers.backfill.estimate_backfill``. É o modo
pensado para esta sprint: a carga nacional completa ao vivo (~milhões de chamadas de MSC
sozinho) fica para uma decisão humana separada sobre janela/throttle/cota — não é parte
desta sprint (ver "Fora de escopo" na ficha).

Duas formas de montar a unidade, deliberadamente diferentes por fonte:

- **MSC**: uma unidade por ``(ente, ano)`` — mesmo desenho do Sprint 21
  (``backfill_sprint21.py``). O conector **não** agrupa entes: cada job é um ente × mês, e
  o ``extract`` já multiplica por ``classe_conta × id_tv`` (``connectors/siconfi.py``).

- **SIOPS/SIOPE**: uma unidade por ``(UF, ano)``, agrupando o estado (ou DF) e **todos**
  os seus municípios num único ``RunRequest``. Os dois conectores têm ``discover()`` que
  gera 1 job por ``(ano, bimestre)`` e enfileira **vários entes dentro do mesmo job**
  (``extract`` faz 1 chamada por ente, mas o bronze/entrega são gravados sob a chave
  sentinela ``cod_ibge="BR"`` — ver ``connectors/siops.py``/``siope.py``). Se cada ente
  virasse uma unidade própria (como no MSC), duas unidades do MESMO ``(ano, bimestre)``
  executadas no mesmo dia colidiriam na MESMA chave de bronze
  ``(fonte, "BR", período, versão)``: ``upsert_bronze`` faz ``ON CONFLICT DO NOTHING``
  (``repository.py``), então só a **primeira** entraria e as demais seriam puladas em
  silêncio — exatamente o padrão que o campo ``entrega_agregada`` documenta em
  ``connectors/registry.py`` e que o outro orquestrador (``app/workers/ingest_jobs.py``,
  função ``_all_units``) já trata de propósito. ``FONTE_META`` não marca
  ``siops_saude``/``siope_educacao`` com ``entrega_agregada=True`` (pré-existente, fora do
  escopo desta sprint corrigir o registry central — ver nota no documento da sprint), mas
  este script já monta as unidades agrupadas para não cair na mesma armadilha. Agrupar por
  UF (em vez de 1 unidade para o Brasil inteiro) também mantém o raio de uma falha pequeno:
  um erro persistente estraga o commit de uma UF, não do país inteiro.

O cadastro nacional (``silver.siconfi_entes``) tem hoje (ver inspeção real desta sprint):
5.568 municípios (``esfera='M'``) + 26 estados (``esfera='E'``) + 1 Distrito Federal
(``esfera='D'``, ``cod_ibge='53'`` — código de 2 dígitos, tratado como "estado" pelas três
APIs) + 1 União (``esfera='U'``, fora de escopo fiscal subnacional). O agrupamento por UF
usa o **prefixo do código IBGE** (``cod_ibge[:2]``), não a coluna ``uf`` da silver: ela vem
``"BR"`` para os entes estaduais (sentinela da fonte, não a sigla) — o prefixo é a mesma
chave que os próprios conectores usam (``UF_POR_PREFIXO_IBGE`` em ``connectors/siope.py``).

Uso::

    python -m scripts.backfill_msc_siops_siope --dry-run --anos 2021-2026
    python -m scripts.backfill_msc_siops_siope --dry-run --fontes siconfi_msc --anos 2024
    python -m scripts.backfill_msc_siops_siope --dry-run --ufs CE,PI --anos 2022-2024
    # carga real (FORA DE ESCOPO desta sprint — decisão humana de janela/throttle):
    python -m scripts.backfill_msc_siops_siope --anos 2021-2026
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.modules.ingestion.connectors.siope import UF_POR_PREFIXO_IBGE  # noqa: E402
from app.modules.ingestion.models import (  # noqa: E402
    FONTE_MSC,
    FONTE_SIOPE,
    FONTE_SIOPS,
    SilverEnte,
)
from app.modules.ingestion.schemas import RunRequest  # noqa: E402
from app.workers import backfill  # noqa: E402

VAR_DIR = Path(__file__).resolve().parents[1] / "var" / "backfill"
# Sigla -> prefixo IBGE de 2 dígitos, invertendo o mapa que os próprios conectores SIOPE/
# SIOPS já usam (connectors/siope.py) — não duplica a lista de UFs, só inverte a chave.
_PREFIXO_POR_SIGLA_UF: dict[str, str] = {
    sigla: prefixo for prefixo, sigla in UF_POR_PREFIXO_IBGE.items()
}
# Mesmo arquivo default do Sprint 21 (CE): a guarda de idempotência do checkpoint é
# **por chave**, não por script — reusar o arquivo é o que garante que o plano nacional
# não repita unidades que a âncora CE (ou uma corrida anterior deste script) já concluiu.
CHECKPOINT_PADRAO = VAR_DIR / "checkpoint.json"

FONTES_PADRAO = (FONTE_MSC, FONTE_SIOPS, FONTE_SIOPE)
# Fontes cujo conector agrupa vários entes num único RunRequest (ver docstring do módulo).
# Tupla, não set: a ordem do plano fica determinística (mesma razão de FONTES_PADRAO acima).
FONTES_EM_LOTE_POR_UF = (FONTE_SIOPS, FONTE_SIOPE)
# Esferas que compõem o escopo fiscal subnacional (exclui "U" = União).
ESFERAS_SUBNACIONAIS = ("M", "E", "D")


def _parse_anos(spec: str) -> list[int]:
    if "-" in spec:
        ini, fim = spec.split("-", 1)
        return list(range(int(ini), int(fim) + 1))
    return [int(a) for a in spec.split(",") if a.strip()]


def _grupos_por_uf(session: Session) -> dict[str, list[str]]:
    """Todos os entes subnacionais do cadastro nacional, agrupados pelo prefixo IBGE de 2
    dígitos (26 estados + DF = 27 grupos). Cada grupo tem o código do estado/DF (2 dígitos)
    e os códigos dos seus municípios (7 dígitos) — a mesma composição que
    ``UF_POR_PREFIXO_IBGE`` (``connectors/siope.py``) resolveria a partir de qualquer um
    dos códigos do grupo.
    """
    codigos = session.scalars(
        select(SilverEnte.cod_ibge).where(SilverEnte.esfera.in_(ESFERAS_SUBNACIONAIS))
    ).all()
    grupos: dict[str, list[str]] = {}
    for cod in codigos:
        grupos.setdefault(cod[:2], []).append(cod)
    return grupos


def _filtrar_ufs(grupos: dict[str, list[str]], ufs: list[str] | None) -> dict[str, list[str]]:
    """Restringe o escopo a UFs específicas (``--ufs``); ``None``/vazio = nacional.

    Aceita sigla (``CE``) OU prefixo IBGE de 2 dígitos (``23``) — a sigla é traduzida via
    :data:`_PREFIXO_POR_SIGLA_UF`; uma sigla desconhecida não bate com nenhum grupo (o
    plano fica vazio, não é um erro silencioso: o relatório mostra 0 unidades).
    """
    if not ufs:
        return grupos
    prefixos = {
        u if u.isdigit() else _PREFIXO_POR_SIGLA_UF.get(u, u)
        for u in (bruto.strip().upper() for bruto in ufs)
        if u
    }
    return {p: entes for p, entes in grupos.items() if p in prefixos}


def _unit(fonte: str, *, chave: str, entes: list[str], ano: int) -> backfill.BackfillUnit:
    return backfill.BackfillUnit(
        key=f"{fonte}:{chave}:{ano}",
        req=RunRequest(fonte=fonte, entes=entes, anos=[ano]),
    )


def _plano(session: Session, args: argparse.Namespace) -> list[backfill.BackfillUnit]:
    anos = _parse_anos(args.anos)
    fontes = [f.strip() for f in args.fontes.split(",") if f.strip()]
    ufs = [u for u in (args.ufs or "").split(",") if u.strip()] or None
    grupos = _filtrar_ufs(_grupos_por_uf(session), ufs)

    units: list[backfill.BackfillUnit] = []

    if FONTE_MSC in fontes:
        # Uma unidade por (ente, ano): o conector MSC não agrupa (build_job usa
        # ``id_ente`` singular; ver connectors/siconfi.py::MscConnector).
        for _prefixo, entes_do_grupo in sorted(grupos.items()):
            for ente in entes_do_grupo:
                for ano in anos:
                    units.append(_unit(FONTE_MSC, chave=ente, entes=[ente], ano=ano))

    for fonte in FONTES_EM_LOTE_POR_UF:
        if fonte not in fontes:
            continue
        # Uma unidade por (UF, ano): agrupa o estado/DF + seus municípios no MESMO
        # RunRequest — ver docstring do módulo sobre a colisão de chave de bronze.
        for prefixo, entes_do_grupo in sorted(grupos.items()):
            for ano in anos:
                units.append(_unit(fonte, chave=prefixo, entes=entes_do_grupo, ano=ano))

    return units


def _executar_dry_run(units: list[backfill.BackfillUnit]) -> None:
    print(
        f"plano (--dry-run): {len(units)} unidades — nenhuma rede/banco/checkpoint tocado",
        flush=True,
    )
    estimativa = backfill.estimate_backfill(units)
    print(backfill.format_estimate_report(estimativa), flush=True)


def _progress(i: int, total: int, unit: backfill.BackfillUnit, result) -> None:  # type: ignore[no-untyped-def]
    if i % 25 == 0 or result.silver_rows:
        print(
            f"[{i}/{total}] {unit.key} -> ingeridos={result.ingeridos} "
            f"silver={result.silver_rows} pulados={result.pulados}",
            flush=True,
        )


def run() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--anos", default="2021-2026", help="Ex.: 2021-2026 ou 2022,2023")
    p.add_argument("--fontes", default=",".join(FONTES_PADRAO))
    p.add_argument(
        "--ufs",
        default="",
        help="Siglas OU prefixos IBGE separados por vírgula para restringir o escopo "
        "(ex.: CE,PI ou 23,22 — equivalentes). Vazio = nacional (todas as 27 UFs).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Mede o plano (unidades/jobs/chamadas HTTP/tempo estimado) e sai — não toca "
        "rede, banco nem checkpoint.",
    )
    p.add_argument("--checkpoint", default=str(CHECKPOINT_PADRAO))
    p.add_argument("--min-free-gb", type=float, default=2.0)
    p.add_argument("--disk-guard", default="D:/pg_tablespace_fiscal")
    p.add_argument("--force", action="store_true", help="Ignora o checkpoint (reexecuta tudo).")
    args = p.parse_args()

    with SessionLocal() as session:
        units = _plano(session, args)

    if args.dry_run:
        _executar_dry_run(units)
        return

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
        print(f"unidades com falha ({len(res.erros)}), serão retentadas:", flush=True)
        for chave, erro in res.erros[:20]:
            print(f"  {chave}: {erro}", flush=True)
    if res.motivo:
        print(res.motivo, flush=True)


if __name__ == "__main__":
    run()
