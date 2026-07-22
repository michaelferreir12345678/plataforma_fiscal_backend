"""Ingestão real (Sprint 12) de DCA e MSC do SICONFI para os entes-demo.

Roda os conectores ``siconfi_dca`` e ``siconfi_msc`` contra a API pública do Tesouro
(apidatalake) e materializa o silver. Depois basta acessar os endpoints de
``/patrimonio``, ``/msc/*`` e ``/balancos`` — a gold é materializada sob demanda a partir
do silver (idempotente por versão).

Realidade do dado aberto:
- **Fortaleza (2304400)** publica **DCA** (Balanço Patrimonial etc.), mas **não** publica
  MSC no datalake → só balanços anuais.
- **São Paulo/capital (3550308)** publica **DCA e MSC** (mensal) → explorador MSC completo.

Uso:  python -m scripts.ingest_patrimonio
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.core.db import admin_session  # noqa: E402
from app.modules.ingestion import service  # noqa: E402
from app.modules.ingestion.schemas import RunRequest  # noqa: E402
from app.shared.ingestion.client import RealClientResolver  # noqa: E402

FORTALEZA = "2304400"
SAO_PAULO = "3550308"


def _run(resolver: RealClientResolver, req: RunRequest) -> None:
    with admin_session() as session:
        res = service.run(session, resolver, req)
    print(
        f"[ingest] {res.fonte}: jobs={res.total_jobs} ingeridos={res.ingeridos} "
        f"pulados={res.pulados} silver_rows={res.silver_rows} versoes={res.versoes_vigentes}"
    )


def run() -> None:
    resolver = RealClientResolver()
    try:
        # --- DCA (anual): balanços da Declaração de Contas Anuais ---
        _run(resolver, RunRequest(fonte="siconfi_dca", entes=[FORTALEZA], anos=[2022, 2023]))
        _run(resolver, RunRequest(fonte="siconfi_dca", entes=[SAO_PAULO], anos=[2021, 2022]))

        # --- MSC (mensal, por conta PCASP): só São Paulo publica no datalake ---
        # 12 meses × 4 classes × 3 tipos de valor = 144 chamadas/ano (rate-limit ~6 req/s).
        _run(resolver, RunRequest(fonte="siconfi_msc", entes=[SAO_PAULO], anos=[2022]))
    finally:
        resolver.close()
    print("[ingest] concluído — patrimônio (DCA) + MSC disponíveis para materialização gold.")


if __name__ == "__main__":
    run()
