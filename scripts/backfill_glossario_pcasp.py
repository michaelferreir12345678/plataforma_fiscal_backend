"""Aplica o glossário PCASP estático às contas já materializadas (Sprint D1).

A MSC do SICONFI publica saldo por conta PCASP mas **não publica o nome da conta**: até
esta sprint, ~90% das folhas de nível 6-7 (Item/Subitem) ficavam com o rótulo genérico
"código · Subitem" em ``gold.dim_conta_pcasp``/``gold.mart_msc_rollup`` — o Explorador MSC
mostrava código cru sem descrição legível. A Sprint D1 adiciona
``app.modules.accounting.pcasp_glossario`` (dicionário estático da Portaria STN, ~3.500
contas nível 6-7, classes 1-4 — ver docstring do módulo para fonte e data) e o consome em
``accounting.service._upsert_conta`` para toda materialização **futura**.

Este script cobre o que já foi materializado **antes** desta sprint: sem ele, só quem
recarregasse a MSC do zero ganharia os nomes novos. É um backfill de metadado puro — não
recalcula saldo algum, não toca ``fato_msc_saldo``, só substitui o texto de exibição onde
ele é **exatamente** o fallback genérico do próprio código (nunca uma descrição vinda da
DCA, que é sempre preservada). Idempotente: reexecutar depois da 1ª vez não altera nada,
porque o texto já não é mais o fallback.

Uso::

    python -m scripts.backfill_glossario_pcasp
    python -m scripts.backfill_glossario_pcasp --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.core.db import admin_session  # noqa: E402
from app.modules.accounting import (  # noqa: E402
    pcasp,
    pcasp_glossario,
    service,  # noqa: E402
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Só conta quantos códigos do glossário têm o fallback genérico gravado hoje.",
    )
    return parser.parse_args()


def run() -> None:
    args = _args()
    print(f"{len(pcasp_glossario.GLOSSARIO)} código(s) no glossário PCASP.")

    if args.dry_run:
        from sqlalchemy import text

        with admin_session() as session:
            atualizacoes = [
                {"codigo": codigo, "fallback": pcasp.nome_no(codigo)}
                for codigo in pcasp_glossario.GLOSSARIO
            ]
            n = 0
            for lote_ini in range(0, len(atualizacoes), 500):
                lote = atualizacoes[lote_ini : lote_ini + 500]
                codigos = [a["codigo"] for a in lote]
                fallbacks = {a["codigo"]: a["fallback"] for a in lote}
                rows = session.execute(
                    text(
                        "select codigo, descricao from gold.dim_conta_pcasp "
                        "where codigo = any(:codigos)"
                    ),
                    {"codigos": codigos},
                ).all()
                n += sum(1 for cod, desc in rows if desc == fallbacks.get(cod))
        print(f"{n} conta(s) em dim_conta_pcasp ainda com o rótulo genérico — seriam atualizadas.")
        return

    with admin_session() as session:
        atualizados = service.backfill_glossario_pcasp(session)
        session.commit()
    print(f"{atualizados} linha(s) atualizada(s) em dim_conta_pcasp + mart_msc_rollup.")


if __name__ == "__main__":
    run()
