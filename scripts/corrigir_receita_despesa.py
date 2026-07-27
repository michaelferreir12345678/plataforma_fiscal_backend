"""Expurga a contaminação de DESPESA na gold da receita e re-materializa (correção 1).

O RREO Anexo 01 é o *Balanço Orçamentário* (receita **e** despesa). Até a correção em
``revenue/natureza.py``, as colunas de despesa ("DESPESAS EMPENHADAS ATÉ O BIMESTRE (f)"…)
casavam com as regras de arrecadação e a despesa entrava no ``fato_receita`` — e, por
tabela, virava nó de ``dim_origem_receita`` pendurado sob ``ReceitasDeCapital``.

Com a origem corrigida, este script remove o que já havia sido gravado errado e manda
re-materializar. Idempotente: rodar de novo não faz nada além de reconferir.

Uso::

    python -m scripts.corrigir_receita_despesa            # expurga e re-materializa
    python -m scripts.corrigir_receita_despesa --dry-run  # só relata
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.core.db import SessionLocal  # noqa: E402
from app.modules.revenue import natureza  # noqa: E402


def _codigos_espurios(session) -> list[str]:  # type: ignore[no-untyped-def]
    """Nós de ``dim_origem_receita`` que **só existem** por causa do bloco de despesa.

    Critério: o nó tem linhas no Anexo 01 e **nenhuma** delas está numa coluna de receita.
    Não basta "não ter coluna de receita": um nó **sem linha alguma** é órfão de carga
    antiga (ex.: ``OperacoesDeCredito``, que é receita de capital legítima) e não pode ser
    confundido com despesa — por isso a exigência de ter ao menos uma linha de despesa.

    A classificação usa ``natureza.classificar_coluna``, a mesma função da materialização.
    Reescrever esse vocabulário em SQL foi o que permitiu a divergência original.
    """
    pares = session.execute(
        text(
            "select cod_conta, coluna, count(*) from silver.siconfi_rreo "
            "where anexo ilike '%01%' and cod_conta is not null group by 1, 2"
        )
    ).all()
    com_receita: set[str] = set()
    com_despesa: set[str] = set()
    for cod, coluna, _n in pares:
        if natureza.classificar_coluna(coluna) is not None:
            com_receita.add(cod)
        elif natureza.e_coluna_de_despesa(coluna):
            com_despesa.add(cod)

    existentes = list(session.execute(text("select codigo from gold.dim_origem_receita")).scalars())
    return sorted(c for c in existentes if c in com_despesa and c not in com_receita)


def run() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Apenas relata o que removeria.")
    args = p.parse_args()

    with SessionLocal() as session:
        codigos = _codigos_espurios(session)
        n_fato = int(
            session.scalar(
                text(
                    "select count(*) from gold.fato_receita where origem_codigo = any(:c)"
                ),
                {"c": codigos},
            )
            or 0
        ) if codigos else 0
        print(f"nós espúrios em dim_origem_receita: {len(codigos)}")
        for c in sorted(codigos):
            print(f"   {c}")
        print(f"linhas de fato_receita atribuídas a eles: {n_fato}")

        if args.dry_run or not codigos:
            print("nada alterado." if args.dry_run else "nada a corrigir.")
            return

        # Ordem importa: o fato referencia a dimensão (FK).
        session.execute(
            text("delete from gold.fato_receita where origem_codigo = any(:c)"),
            {"c": codigos},
        )
        session.execute(
            text("delete from gold.dim_origem_receita where codigo = any(:c)"),
            {"c": codigos},
        )
        session.commit()
        print(f"removidos: {n_fato} linhas de fato e {len(codigos)} nós de dimensão")

    print(
        "\nAgora re-materialize a receita para reconstruir a árvore correta:\n"
        "    python -m scripts.materialize_sprint21 --uf 23"
    )


if __name__ == "__main__":
    run()
