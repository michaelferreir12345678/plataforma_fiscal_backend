"""Avaliação e verificação contínua da IA (Sprint IA-6) — o comando único.

Roda o conjunto dourado inteiro (as três respostas difíceis), a bateria adversária e o
controle negativo da métrica, e escreve o relatório versionado.

Uso::

    python -m scripts.avaliar_ia
    python -m scripts.avaliar_ia --saida docs/avaliacao_ia.md --json docs/avaliacao_ia.json
    python -m scripts.avaliar_ia --provedor gemini --json /tmp/gemini.json
    python -m scripts.avaliar_ia --baseline docs/avaliacao_ia.json   # comparação lado a lado

O padrão é o **provedor local determinístico**: reprodutível, gratuito e offline. Avaliar
contra o Gemini é uma decisão explícita (``--provedor gemini``), do jeito que tem de ser —
uma suíte que gasta token e depende de rede não roda a cada mudança de prompt, e uma que
não roda não protege nada.

Código de saída:

* ``0`` — critérios de aceite atendidos (e, com ``--baseline``, sem regressão);
* ``1`` — alucinação numérica não-zero, recusa esperada que não aconteceu, ataque que
  passou, controle negativo não detectado, ou regressão contra a linha de base.

**Troca de modelo:** rode com o modelo atual salvando o JSON, troque, rode de novo com
``--baseline <json anterior>``. A tabela lado a lado sai no relatório e a regressão
trava — antes de ir para produção, não depois.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.modules.evaluation import relatorio as relatorio_mod  # noqa: E402
from app.modules.evaluation import runner  # noqa: E402

_PADRAO_MD = "docs/avaliacao_ia.md"
_PADRAO_JSON = "docs/avaliacao_ia.json"


def _argumentos() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avaliar_ia", description="Avaliação do assistente (Sprint IA-6)."
    )
    parser.add_argument(
        "--provedor",
        choices=(runner.PROVEDOR_LOCAL, runner.PROVEDOR_GEMINI),
        default=runner.PROVEDOR_LOCAL,
        help="Provedor a avaliar. Padrão: local determinístico (offline, reprodutível).",
    )
    parser.add_argument("--saida", default=_PADRAO_MD, help="Relatório markdown.")
    parser.add_argument("--json", dest="json_saida", default=_PADRAO_JSON, help="Relatório JSON.")
    parser.add_argument(
        "--baseline",
        default=None,
        help="JSON de uma execução anterior: gera a comparação lado a lado e trava regressão.",
    )
    parser.add_argument(
        "--sem-adversarial",
        action="store_true",
        help=(
            "Pula a bateria adversária e o controle negativo "
            "(diagnóstico; não é execução válida)."
        ),
    )
    parser.add_argument(
        "--apenas",
        nargs="*",
        default=(),
        help="IDs específicos do conjunto (depuração de uma pergunta).",
    )
    return parser


def main() -> int:
    args = _argumentos().parse_args()
    # O console do Windows abre em cp1252 e o relatório tem "→", "⚠️" e acentos. Sem isto
    # o comando morre no `print` **depois** de ter feito o trabalho todo.
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    if args.provedor == runner.PROVEDOR_LOCAL:
        # Trava o provedor antes de qualquer construção de Settings memorizada: uma
        # avaliação "local" que silenciosamente falasse com o Gemini seria pior que erro.
        os.environ["ASSISTANT_PROVIDER"] = "local"

    resultado = runner.avaliar(
        provedor=args.provedor,
        incluir_adversarial=not args.sem_adversarial,
        apenas=tuple(args.apenas or ()),
    )
    markdown = relatorio_mod.para_markdown(resultado)
    atual = relatorio_mod.para_dict(resultado)

    regressao = False
    if args.baseline:
        anterior = relatorio_mod.carregar(args.baseline)
        linhas = relatorio_mod.comparar(anterior, atual)
        regressao = relatorio_mod.houve_regressao(linhas)
        markdown += "\n" + relatorio_mod.comparacao_markdown(anterior, atual, linhas)

    Path(args.saida).parent.mkdir(parents=True, exist_ok=True)
    Path(args.saida).write_text(markdown, encoding="utf-8")
    Path(args.json_saida).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_saida).write_text(relatorio_mod.para_json(resultado), encoding="utf-8")

    metricas = resultado.metricas
    assert metricas is not None
    print(markdown)
    print(f"[avaliacao] relatório: {args.saida} · dados: {args.json_saida}", file=sys.stderr)

    if not resultado.aprovado or regressao:
        motivos = []
        if not metricas.alucinacao_zero:
            motivos.append(
                f"alucinação numérica {metricas.alucinacao_numerica.pct()} (exigido: zero)"
            )
        if not metricas.recusas_todas_corretas:
            motivos.append(f"recusa correta {metricas.recusa_correta.pct()} (exigido: todas)")
        if metricas.adversarial.numerador != metricas.adversarial.denominador:
            motivos.append(f"bateria adversária {metricas.adversarial.pct()}")
        if metricas.aprovacao.numerador != metricas.aprovacao.denominador:
            motivos.append(f"aprovação {metricas.aprovacao.pct()}")
        if not resultado.controle_negativo.get("detectou", True):
            motivos.append("controle negativo NÃO detectou a alucinação plantada")
        if regressao:
            motivos.append("regressão contra a linha de base")
        print(f"\n[avaliacao] REPROVADO: {'; '.join(motivos)}", file=sys.stderr)
        return 1

    print("\n[avaliacao] APROVADO: critérios de aceite da IA-6 atendidos.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
