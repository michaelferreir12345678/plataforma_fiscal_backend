"""Materializa um snapshot de benchmark usando exclusivamente a gold real existente.

O script não cria indicadores e não semeia valores: ele exige ``dim_ente`` e
``mart_indicador`` previamente produzidos pelos ETLs/cálculos das Sprints 1/1B/2.

Uso::

    python -m scripts.materialize_benchmark --ente 2304400 \
        --indicador pessoal_executivo --periodo 2024-B6 --coorte porte
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.core.db import admin_session  # noqa: E402
from app.modules.benchmark import service  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ente", required=True, help="Código IBGE do ente destacado.")
    parser.add_argument("--indicador", help="Indicador; ausente usa o primeiro disponível.")
    parser.add_argument("--periodo", help="Período; ausente usa o mais recente do ente.")
    parser.add_argument(
        "--coorte",
        default="porte",
        help="Código/UUID explícito ou porte, regiao, pib (default: porte).",
    )
    parser.add_argument("--as-of", dest="as_of", help="Timestamp ISO-8601 para reprodução.")
    return parser.parse_args()


def run() -> None:
    args = _args()
    as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00")) if args.as_of else None
    with admin_session() as session:
        response = service.build_benchmark(
            session,
            cod_ibge=args.ente,
            indicador=args.indicador,
            coorte=args.coorte,
            periodo=args.periodo,
            as_of=as_of,
        )
    print(response.model_dump_json(indent=2))


if __name__ == "__main__":
    run()
