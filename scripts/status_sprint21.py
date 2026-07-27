"""Status do backfill âncora (Sprint 21) — progresso, cobertura e falhas.

Fonte de verdade **durável** (não depende do stdout do worker, que pode ter rolado):
os arquivos de checkpoint + o próprio banco (``mart_cobertura_fonte`` e ``ingestion_log``).

Uso::

    python -m scripts.status_sprint21
    python -m scripts.status_sprint21 --uf 23 --meta 95
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.core.db import SessionLocal  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
VAR_DIR = RAIZ / "var" / "backfill"
# Municípios do Ceará (denominador do aceite de cobertura).
TOTAL_MUNICIPIOS = {"23": 184}


def _checkpoints() -> None:
    print("== Checkpoints (retomáveis) ==")
    arquivos = sorted(VAR_DIR.glob("checkpoint*.json")) if VAR_DIR.exists() else []
    if not arquivos:
        print("  (nenhum — o backfill ainda não rodou)")
        return
    for arq in arquivos:
        try:
            dados = json.loads(arq.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"  {arq.name}: ilegível")
            continue
        stats = dados.get("stats", {})
        print(
            f"  {arq.name}: {len(dados.get('done', []))} unidades concluídas | "
            f"silver_rows={stats.get('silver_rows', 0)}"
        )


def _frescor(session) -> None:  # type: ignore[no-untyped-def]
    """Avisa se a cobertura materializada está atrás do que já entrou no silver.

    ``mart_cobertura_fonte`` é materializada por job: enquanto o backfill roda, ela
    reflete o último refresh, não o instante atual. Sem esse aviso o operador leria
    um número velho como se fosse o estado corrente.
    """
    atualizado = session.scalar(text("select max(atualizado_em) from gold.mart_cobertura_fonte"))
    entes_cob = session.scalar(
        text("select count(distinct cod_ibge) from gold.mart_cobertura_fonte "
             "where fonte='siconfi_rreo'")
    )
    entes_silver = session.scalar(
        text("select count(distinct cod_ibge) from silver.siconfi_rreo")
    )
    print(f"  (materializada em {atualizado})")
    if (entes_silver or 0) > (entes_cob or 0):
        print(
            f"  ATENÇÃO: silver já tem {entes_silver} entes no RREO e a cobertura mostra "
            f"{entes_cob} — rode o refresh para ver o número atual:\n"
            "    python -m scripts.materialize_sprint21 --uf 23"
        )


def _cobertura(uf: str, meta: float) -> bool:
    total = TOTAL_MUNICIPIOS.get(uf)
    print(f"\n== Cobertura UF {uf} (mart_cobertura_fonte) ==")
    atingiu = True
    with SessionLocal() as session:
        _frescor(session)
        linhas = session.execute(
            text(
                "select fonte, ano, count(distinct cod_ibge) entes "
                "from gold.mart_cobertura_fonte where uf = :uf "
                "group by 1,2 order by 1,2"
            ),
            {"uf": uf},
        ).all()
        if not linhas:
            print("  (vazia — rode: python -m scripts.materialize_sprint21)")
            return False
        for fonte, ano, entes in linhas:
            if total:
                pct = 100.0 * entes / total
                alvo = fonte in ("siconfi_rreo", "siconfi_rgf") and ano >= 2022
                marca = ""
                if alvo:
                    marca = "  <-- META OK" if pct >= meta else f"  <-- falta p/ {meta:.0f}%"
                    atingiu = atingiu and pct >= meta
                print(f"  {fonte:24s} {ano}  {entes:4d}/{total} entes ({pct:5.1f}%){marca}")
            else:
                print(f"  {fonte:24s} {ano}  {entes:4d} entes")
    return atingiu


def _lacunas_da_fonte(uf: str) -> None:
    """Municípios que **não entregaram** ao SICONFI — lacuna da fonte, não da plataforma.

    A distinção importa: a cobertura bruta (entes com dado ÷ universo) mistura duas coisas
    diferentes. O que a plataforma controla é ingerir 100% do que a fonte publica; quem não
    publicou não pode ser fabricado. Listar nominalmente torna a lacuna auditável.
    """
    print("\n== Lacunas da fonte (municípios sem entrega no SICONFI) ==")
    # ``uf`` aqui é o prefixo IBGE numérico (ex.: 23), o mesmo que mart_cobertura_fonte usa.
    # ``silver.siconfi_entes.uf`` guarda a SIGLA ('CE'), então o recorte do universo é feito
    # pelo prefixo do próprio código do ente — não pela coluna uf.
    escopo = "substr(e.cod_ibge, 1, 2) = :uf and e.esfera = 'M' and length(e.cod_ibge) = 7"
    with SessionLocal() as session:
        linhas = session.execute(
            text(
                f"select e.cod_ibge, e.nome from silver.siconfi_entes e where {escopo} "
                "  and not exists (select 1 from gold.mart_cobertura_fonte c "
                "    where c.fonte = 'siconfi_rreo' and c.cod_ibge = e.cod_ibge) "
                "order by e.nome"
            ),
            {"uf": uf},
        ).all()
        universo = int(
            session.scalar(
                text(f"select count(*) from silver.siconfi_entes e where {escopo}"),
                {"uf": uf},
            )
            or 0
        )
    if not linhas:
        print("  nenhuma — todos os municípios do universo têm entrega")
        return
    publicaram = universo - len(linhas)
    print(
        f"  {len(linhas)} de {universo} municípios nunca entregaram RREO "
        f"(taxa de publicação da fonte: {100 * publicaram / universo:.1f}%)"
    )
    for cod, nome in linhas:
        print(f"    {cod}  {nome}")
    print(
        "  -> a plataforma ingeriu 100% do que o SICONFI publica; estes entes não têm\n"
        "     linha em mart_cobertura_fonte de propósito (ausência ≠ zero)."
    )


def _falhas() -> None:
    print("\n== Falhas registradas (gold.ingestion_log, status='erro') ==")
    with SessionLocal() as session:
        linhas = session.execute(
            text(
                "select fonte, count(*) n, max(ts) ultimo from gold.ingestion_log "
                "where status = 'erro' group by 1 order by n desc limit 10"
            )
        ).all()
        if not linhas:
            print("  nenhuma")
            return
        for fonte, n, ultimo in linhas:
            print(f"  {fonte:24s} {n:5d} falhas (última: {ultimo})")
        print("  -> unidades que falharam não entram no checkpoint: basta reexecutar o backfill")


def _proximos_passos(atingiu: bool, uf: str) -> None:
    print("\n== Próximos passos ==")
    if not atingiu:
        print("  1) Backfill ainda em curso/incompleto — reexecute (retoma do checkpoint):")
        print("     python -m scripts.backfill_sprint21 --anos 2022-2024 "
              "--fontes siconfi_rreo,siconfi_rgf")
        print("  2) Depois, o histórico e os extratos:")
        print("     python -m scripts.backfill_sprint21 --anos 2021-2024 "
              "--fontes siconfi_dca,siconfi_extratos")
    print(f"  3) Materialize a gold do escopo:  python -m scripts.materialize_sprint21 "
          f"--uf {uf} --benchmark")
    print("  4) Confira aqui de novo:          python -m scripts.status_sprint21")
    print("  5) Atualização contínua já está agendada (tarefa 'PlataformaFiscal-Ingestao');")
    print("     passagem manual: python -m scripts.scheduler --once")


def run() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uf", default="23", help="Prefixo IBGE da UF (default 23 = CE).")
    p.add_argument("--meta", type=float, default=95.0, help="Meta de cobertura (%%).")
    args = p.parse_args()

    _checkpoints()
    atingiu = _cobertura(args.uf, args.meta)
    _lacunas_da_fonte(args.uf)
    _falhas()
    _proximos_passos(atingiu, args.uf)


if __name__ == "__main__":
    run()
