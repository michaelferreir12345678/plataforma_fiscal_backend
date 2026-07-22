"""Conforma em gold.dim_ente todo o cadastro real ja ingerido em silver."""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app.core.db import SessionLocal  # noqa: E402
from app.modules.catalog import service  # noqa: E402
from app.modules.ingestion.models import SilverEnte  # noqa: E402


def run() -> None:
    with SessionLocal() as session:
        codigos = list(session.scalars(select(SilverEnte.cod_ibge).order_by(SilverEnte.cod_ibge)))
        for index, cod_ibge in enumerate(codigos, start=1):
            service.refresh_dim_ente(session, cod_ibge)
            if index % 500 == 0:
                session.commit()
                print(f"dim_ente: {index}/{len(codigos)}")
        session.commit()
        print(f"dim_ente conformada: {len(codigos)} entes reais")


if __name__ == "__main__":
    run()
