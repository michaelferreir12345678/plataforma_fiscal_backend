"""Semeadura da Visão Estadual (Sprint 23): regiões + malha real do IBGE + consolidado.

Popula, para uma UF, os três assets territoriais com **dado real** (nada inventado):

1. ``gold.dim_regiao_uf`` — regiões a partir da API oficial de localidades do IBGE
   (nível *região geográfica imediata*, determinístico e genérico para qualquer UF).
2. ``gold.geo_malha_uf`` — a malha municipal real (GeoJSON do IBGE, qualidade mínima),
   servida por ``GET /geo/malha/{uf}`` e usada pelo coroplético.
3. ``gold.mart_consolidado_uf`` — o consolidado (Σnum/Σden) de todos os indicadores v1,
   para todos os períodos RREO com dado da UF.

Nota sobre as regiões: a divisão preferida do produto para o Ceará são as **14 Regiões de
Planejamento do IPECE**; como não há feed do IPECE conectado, usa-se a divisão oficial do
IBGE (região imediata), real e genérica. Quando o feed do IPECE entrar, basta repovoar
``dim_regiao_uf`` — o schema e os endpoints não mudam (mesmo padrão da cota-parte do ICMS
na Sprint 21).

Uso::

    python -m scripts.seed_estadual --uf 23              # regiões + malha + consolidado
    python -m scripts.seed_estadual --uf 23 --so-malha
    python -m scripts.seed_estadual --uf 23 --periodos 2024-B6,2024-B4
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import func, select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.modules.dashboard import estadual_repository as repo  # noqa: E402
from app.modules.dashboard import estadual_service as svc  # noqa: E402
from app.modules.indicators.models import FatoRcl  # noqa: E402

_IBGE = "https://servicodados.ibge.gov.br"
_TIMEOUT = 90.0


def _get_json(url: str, params: dict | None = None) -> object:
    ultimo: Exception | None = None
    for tentativa in range(4):
        try:
            r = httpx.get(url, params=params, timeout=_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # backoff simples: a API do IBGE às vezes recusa em rajada
            ultimo = e
            print(f"  tentativa {tentativa + 1} falhou: {e}", flush=True)
    raise RuntimeError(f"IBGE não respondeu: {url}") from ultimo


def seed_regioes(uf: str) -> int:
    """Regiões geográficas imediatas do IBGE como ``dim_regiao_uf``."""
    print(f"[regiões] baixando localidades da UF {uf}…", flush=True)
    municipios = _get_json(f"{_IBGE}/api/v1/localidades/estados/{uf}/municipios")
    assert isinstance(municipios, list)
    grupos: dict[str, dict] = {}
    for m in municipios:
        cod = str(m["id"])
        imediata = m.get("regiao-imediata") or {}
        rid = str(imediata.get("id") or "sem_regiao")
        nome = imediata.get("nome") or "Sem região"
        g = grupos.setdefault(rid, {"nome": nome, "municipios": []})
        g["municipios"].append(cod)

    with SessionLocal() as session:
        for rid, g in grupos.items():
            repo.upsert_regiao(
                session,
                {
                    "uf": uf,
                    "regiao_codigo": rid,
                    "nome": g["nome"],
                    "municipios": sorted(g["municipios"]),
                    "nivel_fonte": "regiao_imediata",
                    "fonte": "IBGE — API de localidades v1",
                },
            )
        session.commit()
    print(f"[regiões] {len(grupos)} regiões, {len(municipios)} municípios.", flush=True)
    return len(grupos)


def seed_malha(uf: str, qualidade: str = "minima") -> int:
    """Malha municipal real (GeoJSON do IBGE) em ``geo_malha_uf``."""
    print(f"[malha] baixando malha municipal da UF {uf} (qualidade {qualidade})…", flush=True)
    geojson = _get_json(
        f"{_IBGE}/api/v3/malhas/estados/{uf}",
        params={
            "intrarregiao": "municipio",
            "formato": "application/vnd.geo+json",
            "qualidade": qualidade,
        },
    )
    assert isinstance(geojson, dict)
    feats = geojson.get("features", [])
    with SessionLocal() as session:
        repo.upsert_malha(
            session,
            {
                "uf": uf,
                "formato": "geojson",
                "malha": geojson,
                "simplificacao": qualidade,
                "fonte": "IBGE — API de malhas v3",
                "ano": 2022,
                "n_areas": len(feats),
            },
        )
        session.commit()
    print(f"[malha] {len(feats)} polígonos gravados.", flush=True)
    return len(feats)


def _periodos_com_dado(uf: str) -> list[str]:
    """Períodos RREO (bimestrais) com RCL para a UF — o universo a consolidar."""
    with SessionLocal() as session:
        stmt = (
            select(FatoRcl.periodo_ref)
            .where(func.substr(FatoRcl.cod_ibge, 1, 2) == uf, func.length(FatoRcl.cod_ibge) == 7)
            .distinct()
        )
        return sorted({p for p in session.scalars(stmt) if p})


def seed_consolidado(uf: str, periodos: list[str]) -> int:
    """Materializa ``mart_consolidado_uf`` para cada período."""
    print(f"[consolidado] materializando {len(periodos)} períodos…", flush=True)
    total = 0
    with SessionLocal() as session:
        for p in periodos:
            n = svc.refresh_consolidado(session, uf, p)
            total += n
            session.commit()
            print(f"  {p}: {n} indicadores", flush=True)
    print(f"[consolidado] {total} linhas de indicador materializadas.", flush=True)
    return total


def run() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uf", required=True, help="Sigla ('CE') ou código IBGE ('23') da UF.")
    p.add_argument("--so-regioes", action="store_true")
    p.add_argument("--so-malha", action="store_true")
    p.add_argument("--so-consolidado", action="store_true")
    p.add_argument(
        "--qualidade", default="minima", help="Qualidade da malha (minima|intermediaria)."
    )
    p.add_argument("--periodos", default=None, help="Lista de períodos; padrão = todos com dado.")
    args = p.parse_args()

    uf = svc.normalizar_uf(args.uf)
    so = args.so_regioes or args.so_malha or args.so_consolidado

    if not so or args.so_regioes:
        seed_regioes(uf)
    if not so or args.so_malha:
        seed_malha(uf, qualidade=args.qualidade)
    if not so or args.so_consolidado:
        periodos = (
            [x.strip() for x in args.periodos.split(",") if x.strip()]
            if args.periodos
            else _periodos_com_dado(uf)
        )
        seed_consolidado(uf, periodos)

    print(f"pronto (UF {uf}).", flush=True)


if __name__ == "__main__":
    run()
