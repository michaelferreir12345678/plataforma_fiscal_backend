"""Materializa os limites de endividamento — garantias e operações de crédito.

``gold.dim_limite_legal`` declara quatro tetos de endividamento e, até a Sprint B2, só a
dívida consolidada líquida era calculada. Garantias (22% da RCL Ajustada) e operações de
crédito (16%) existiam como regra e não como número: a plataforma sabia o limite e nunca
dizia se o ente estava dentro dele.

Este script percorre as entregas **vigentes** de RGF e chama
:func:`app.modules.indicators.endividamento.materializar_limites_endividamento`, que grava
no ``gold.mart_indicador``. Ele não inventa nada:

* o denominador é a **RCL Ajustada publicada pelo próprio ente** (Res. 43/2001 do Senado);
  sem ela, o ente é pulado — um limite apurado sobre denominador inventado é pior que
  limite ausente, porque *parece* conformidade;
* anexo não entregue produz ausência, não zero. Anexo entregue sem a linha de garantia é
  o ente declarando que não concedeu nenhuma, e aí zero é a resposta honesta.

É idempotente: reexecutar sobre o mesmo período reescreve as mesmas linhas.

Uso::

    python -m scripts.materialize_endividamento                  # tudo o que houver
    python -m scripts.materialize_endividamento --uf CE          # só o Ceará
    python -m scripts.materialize_endividamento --periodo 2025-Q3
    python -m scripts.materialize_endividamento --ente 2304400
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text  # noqa: E402

from app.core.db import admin_session  # noqa: E402
from app.modules.indicators import endividamento  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ente", help="Código IBGE; ausente processa todos.")
    parser.add_argument("--uf", help="Sigla da UF (ex.: CE) para restringir o alcance.")
    parser.add_argument("--periodo", help="Período RGF (ex.: 2025-Q3); ausente processa todos.")
    parser.add_argument("--as-of", dest="as_of", help="Timestamp ISO-8601 para reprodução.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Lista o que seria materializado sem gravar.",
    )
    return parser.parse_args()


#: Entregas vigentes de RGF. A bitemporalidade importa aqui: uma retificação supera a
#: versão anterior, e materializar sobre a superada reintroduziria o número corrigido.
_ENTREGAS = """
    select e.cod_ibge, e.periodo, e.versao_entrega
      from gold.dim_entrega e
      join gold.dim_ente d on d.cod_ibge = e.cod_ibge
     where e.relatorio = 'RGF'
       and e.vigente is true
       and (:ente is null or e.cod_ibge = :ente)
       and (:periodo is null or e.periodo = :periodo)
       and (:uf is null or d.uf = :uf)
     order by e.periodo, e.cod_ibge
"""


def run() -> None:
    args = _args()
    as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00")) if args.as_of else None
    filtros = {"ente": args.ente, "periodo": args.periodo, "uf": args.uf}

    with admin_session() as session:
        # **Filtro que não casa tem de gritar.** `gold.dim_ente.uf` guarda a sigla (`CE`),
        # não o código do IBGE: `--uf 23` casava com nada e o script terminava anunciando
        # sucesso sobre zero ente. Um erro de digitação não pode passar por "nada a fazer".
        if args.uf and not session.scalar(
            text("select 1 from gold.dim_ente where uf = :uf limit 1"), {"uf": args.uf}
        ):
            sys.exit(f"UF '{args.uf}' não existe no catálogo — informe a sigla (ex.: CE).")
        entregas = session.execute(text(_ENTREGAS), filtros).all()

    print(f"{len(entregas)} entrega(s) vigente(s) de RGF no alcance.")
    if args.dry_run:
        for cod, periodo, versao in entregas[:20]:
            print(f"  {cod} {periodo} v{versao}")
        if len(entregas) > 20:
            print(f"  … e mais {len(entregas) - 20}")
        return

    gravados = 0
    # Entes pulados **não** são falha: é o ente que não publicou a RCL Ajustada ou o anexo.
    # Contá-los à parte é o que separa "não apuramos" de "apuramos e deu zero".
    sem_base = 0
    erros: list[tuple[str, str, str]] = []

    for cod, periodo, versao in entregas:
        try:
            with admin_session() as session:
                feitos = endividamento.materializar_limites_endividamento(
                    session, cod_ibge=cod, periodo_rgf=periodo, versao=versao, as_of=as_of
                )
                session.commit()
            if feitos:
                gravados += len(feitos)
            else:
                sem_base += 1
        except Exception as exc:  # noqa: BLE001 — um ente que falha não derruba o lote
            erros.append((cod, periodo, str(exc)[:120]))

    print(f"\n{gravados} indicador(es) materializado(s).")
    print(f"{sem_base} entrega(s) sem RCL Ajustada ou sem os anexos — ausência, não zero.")
    if erros:
        print(f"\n{len(erros)} erro(s):")
        for cod, periodo, msg in erros[:10]:
            print(f"  {cod} {periodo}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    run()
