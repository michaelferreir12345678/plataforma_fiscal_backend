"""Diagnóstico (SOMENTE LEITURA) do efeito da Sprint A5/A15 sobre os limites de
endividamento já materializados — garantias e RCL Ajustada.

O RGF republica os quadrimestres já decorridos a cada entrega nova (é assim que a
retificação chega — CLAUDE.md §2, regra 3: "retificação supera a versão anterior, não
apaga"). Antes da Sprint A5, ``indicators/endividamento.py`` lia só a entrega do próprio
período e travava no primeiro valor publicado; a Sprint A5 corrigiu isso (ver
``_valor_vigente``), mas **o gold já materializado com o número antigo não se corrige
sozinho** — só uma nova rodada de ``materialize_endividamento.py`` grava por cima.

Este script **nunca escreve**. Ele compara, para cada entrega vigente de RGF:

* o que está **hoje** em ``gold.mart_indicador`` (indicador ``garantias``, com o
  ``rcl_ajustada`` que foi usado como base — ``base_valor`` da mesma linha);
* o que ``indicators/endividamento.py`` (já corrigido) calcularia **agora**.

Onde os dois divergem (>2%, o mesmo corte que a Sprint A5 usou para contar os "63
quadrimestres"), imprime o antes/depois. Não aplica nada — a decisão de rodar
``materialize_endividamento.py`` de novo sobre esses períodos (o que sobrescreveria o
gold) é do usuário, não deste script. Números já materializados podem ter sido citados
ou exportados; ver CLAUDE.md e a ficha da Sprint A5 em docs/evolucao_plataforma.md.

Uso::

    python -m scripts.reprocessar_rgf_republicado                  # tudo o que houver
    python -m scripts.reprocessar_rgf_republicado --uf CE          # só o Ceará
    python -m scripts.reprocessar_rgf_republicado --ente 2307650
    python -m scripts.reprocessar_rgf_republicado --limiar-pct 2   # corte de materialidade
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text  # noqa: E402

from app.core.db import admin_session  # noqa: E402
from app.modules.indicators import endividamento  # noqa: E402
from app.shared import periodo as periodo_util  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ente", help="Código IBGE; ausente processa todos.")
    parser.add_argument("--uf", help="Sigla da UF (ex.: CE) para restringir o alcance.")
    parser.add_argument("--periodo", help="Período RGF (ex.: 2023-Q1); ausente processa todos.")
    parser.add_argument(
        "--limiar-pct", type=float, default=2.0,
        help="Diferença relativa mínima (%%) para contar como divergência (padrão: 2).",
    )
    parser.add_argument(
        "--mostrar", type=int, default=8, help="Quantos exemplos concretos imprimir (padrão: 8).",
    )
    return parser.parse_args()


#: Entregas vigentes de RGF — mesmo filtro de materialize_endividamento.py (B2).
_ENTREGAS = """
    select e.cod_ibge, e.periodo, e.versao_entrega, d.nome, d.uf
      from gold.dim_entrega e
      join gold.dim_ente d on d.cod_ibge = e.cod_ibge
     where e.relatorio = 'RGF'
       and e.vigente is true
       and (:ente is null or e.cod_ibge = :ente)
       and (:periodo is null or e.periodo = :periodo)
       and (:uf is null or d.uf = :uf)
     order by e.periodo, e.cod_ibge
"""

#: Linha hoje materializada para um indicador de endividamento (traz o rcl_ajustada
#: usado como base_valor, sem recalcular nada).
_MART_INDICADOR = """
    select valor_rs, base_valor, valor_pct_rcl, faixa
      from gold.mart_indicador
     where cod_ibge = :cod and periodo = :periodo_mart
       and indicador = :indicador and versao_entrega = :versao
"""


@dataclass
class Diff:
    cod_ibge: str
    nome: str
    uf: str | None
    periodo_rgf: str
    periodo_mart: str
    garantias_antes: Decimal | None
    garantias_depois: Decimal | None
    rcl_ajustada_antes: Decimal | None
    rcl_ajustada_depois: Decimal | None
    pct_antes: Decimal | None
    pct_depois: Decimal | None
    #: operações de crédito: numerador não muda (Anexo 04 não republica), mas o
    #: percentual muda porque divide pela MESMA RCL Ajustada corrigida.
    oc_pct_antes: Decimal | None
    oc_pct_depois: Decimal | None


def _pct_dif(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    """Diferença relativa |b-a|/|a|, em %; ``None`` se não dá para comparar."""
    if a is None or b is None:
        return None
    if a == 0:
        # De zero para algo: 100% é pouco informativo, mas não é ausência.
        return None if b == 0 else Decimal(100)
    return abs(b - a) / abs(a) * Decimal(100)


def _formata(v: Decimal | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")


def run() -> None:
    args = _args()
    filtros = {"ente": args.ente, "periodo": args.periodo, "uf": args.uf}
    limiar = Decimal(str(args.limiar_pct))

    with admin_session() as session:
        if args.uf and not session.scalar(
            text("select 1 from gold.dim_ente where uf = :uf limit 1"), {"uf": args.uf}
        ):
            sys.exit(f"UF '{args.uf}' não existe no catálogo — informe a sigla (ex.: CE).")
        entregas = session.execute(text(_ENTREGAS), filtros).all()

    print(f"{len(entregas)} entrega(s) vigente(s) de RGF no alcance.")
    print("SOMENTE LEITURA — nada será gravado.\n")

    diffs: list[Diff] = []
    sem_base_hoje_e_agora = 0
    erros: list[tuple[str, str, str]] = []

    with admin_session() as session:
        for cod, periodo_rgf, versao, nome, uf in entregas:
            try:
                periodo_mart = periodo_util.em_bimestre(periodo_rgf) or periodo_rgf

                # O que está hoje no gold (sem recalcular nada).
                atual_gar = session.execute(
                    text(_MART_INDICADOR),
                    {"cod": cod, "periodo_mart": periodo_mart, "versao": versao,
                     "indicador": "garantias"},
                ).first()
                atual_oc = session.execute(
                    text(_MART_INDICADOR),
                    {"cod": cod, "periodo_mart": periodo_mart, "versao": versao,
                     "indicador": "operacoes_credito"},
                ).first()
                garantias_antes = Decimal(atual_gar[0]) if atual_gar else None
                rcl_antes = (
                    Decimal(atual_gar[1]) if atual_gar and atual_gar[1] is not None else None
                )
                pct_antes = (
                    Decimal(atual_gar[2]) if atual_gar and atual_gar[2] is not None else None
                )
                oc_pct_antes = (
                    Decimal(atual_oc[2]) if atual_oc and atual_oc[2] is not None else None
                )

                # O que a leitura corrigida (Sprint A5) calcularia agora.
                rcl_depois = endividamento.rcl_ajustada(
                    session, cod_ibge=cod, periodo=periodo_rgf, versao=versao
                )
                garantias_depois = endividamento.total_garantias(
                    session, cod_ibge=cod, periodo=periodo_rgf, versao=versao
                )
                pct_depois = (
                    (garantias_depois / rcl_depois * 100)
                    if garantias_depois is not None and rcl_depois
                    else None
                )
                # Operações de crédito: numerador não muda (Anexo 04 não republica — ver
                # docstring de total_operacoes_credito), mas o percentual muda se a base
                # (RCL Ajustada, compartilhada) mudou.
                oc_numerador = endividamento.total_operacoes_credito(
                    session, cod_ibge=cod, periodo=periodo_rgf, versao=versao
                )
                oc_pct_depois = (
                    (oc_numerador / rcl_depois * 100)
                    if oc_numerador is not None and rcl_depois
                    else None
                )

                # `materializar_limites_endividamento` só grava quando a base é positiva
                # **e** pelo menos um dos dois indicadores tem insumo (Anexo 03 ou 04
                # entregue) — base positiva sozinha não basta: sem nenhum dos dois anexos,
                # a função devolve `[]` e nada é gravado (achado desta verificação: os
                # dois faltando ao mesmo tempo produzia falso positivo aqui, contando como
                # "mudaria" uma entrega que a materialização real nunca escreveria).
                escreveria_hoje = atual_gar is not None or atual_oc is not None
                escreveria_depois = (
                    rcl_depois is not None and rcl_depois > 0
                    and (garantias_depois is not None or oc_numerador is not None)
                )
                if not escreveria_hoje and not escreveria_depois:
                    sem_base_hoje_e_agora += 1
                    continue
                if not escreveria_hoje:
                    garantias_antes = pct_antes = oc_pct_antes = None
                if not escreveria_depois:
                    garantias_depois = pct_depois = oc_pct_depois = None

                dif_garantias = _pct_dif(garantias_antes, garantias_depois)
                dif_rcl = _pct_dif(rcl_antes, rcl_depois)
                dif_oc_pct = _pct_dif(oc_pct_antes, oc_pct_depois)
                maior_dif = max(
                    (d for d in (dif_garantias, dif_rcl, dif_oc_pct) if d is not None),
                    default=None,
                )
                # Um lado aparecendo/sumindo (não escrevia -> escreveria, ou o contrário)
                # também conta: é o insumo passando a existir ou deixar de existir.
                apareceu_ou_sumiu = escreveria_hoje != escreveria_depois
                if (maior_dif is not None and maior_dif > limiar) or apareceu_ou_sumiu:
                    diffs.append(
                        Diff(
                            cod_ibge=cod, nome=nome, uf=uf,
                            periodo_rgf=periodo_rgf, periodo_mart=periodo_mart,
                            garantias_antes=garantias_antes, garantias_depois=garantias_depois,
                            rcl_ajustada_antes=rcl_antes, rcl_ajustada_depois=rcl_depois,
                            pct_antes=pct_antes, pct_depois=pct_depois,
                            oc_pct_antes=oc_pct_antes, oc_pct_depois=oc_pct_depois,
                        )
                    )
            except Exception as exc:  # noqa: BLE001 — um ente que falha não derruba o lote
                erros.append((cod, periodo_rgf, str(exc)[:160]))

    print(f"{len(diffs)} linha(s) de garantias/RCL-ajustada mudariam de valor (>{limiar}%).")
    print(f"{sem_base_hoje_e_agora} entrega(s) sem base nos dois lados — nada a comparar.")
    if erros:
        print(f"{len(erros)} erro(s) durante a comparação:")
        for cod, periodo, msg in erros[:10]:
            print(f"  {cod} {periodo}: {msg}")

    if not diffs:
        print("\nNenhuma linha materializada mudaria — o gold de garantias/endividamento já")
        print("reflete a leitura vigência-consciente (ou nenhuma foi afetada pela republicação).")
        return

    diffs.sort(
        key=lambda d: max(
            (
                x
                for x in (
                    _pct_dif(d.rcl_ajustada_antes, d.rcl_ajustada_depois),
                    _pct_dif(d.garantias_antes, d.garantias_depois),
                    _pct_dif(d.oc_pct_antes, d.oc_pct_depois),
                )
                if x is not None
            ),
            default=Decimal(0),
        ),
        reverse=True,
    )

    print(f"\nExemplos concretos (as {min(args.mostrar, len(diffs))} maiores diferenças):\n")
    for d in diffs[: args.mostrar]:
        print(f"  {d.cod_ibge} ({d.nome}/{d.uf}) — RGF {d.periodo_rgf} (mart {d.periodo_mart})")
        print(
            f"    RCL Ajustada:  R$ {_formata(d.rcl_ajustada_antes)}  ->  "
            f"R$ {_formata(d.rcl_ajustada_depois)}"
        )
        print(
            f"    Garantias:     R$ {_formata(d.garantias_antes)}  ->  "
            f"R$ {_formata(d.garantias_depois)}"
        )
        pct_antes = f"{d.pct_antes:.4f}%" if d.pct_antes is not None else "—"
        pct_depois = f"{d.pct_depois:.4f}%" if d.pct_depois is not None else "—"
        print(f"    Garantias % da RCL Ajustada:          {pct_antes}  ->  {pct_depois}")
        oc_antes = f"{d.oc_pct_antes:.4f}%" if d.oc_pct_antes is not None else "—"
        oc_depois = f"{d.oc_pct_depois:.4f}%" if d.oc_pct_depois is not None else "—"
        print(f"    Op. de crédito % da RCL Ajustada:     {oc_antes}  ->  {oc_depois}\n")

    print(
        "Nada foi gravado. Para aplicar, rode `python -m scripts.materialize_endividamento`\n"
        "sobre os períodos acima — ele já usa a leitura corrigida (Sprint A5) e sobrescreve\n"
        "as linhas existentes de forma idempotente."
    )


if __name__ == "__main__":
    run()
