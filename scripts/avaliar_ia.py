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
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.modules.evaluation import relatorio as relatorio_mod  # noqa: E402
from app.modules.evaluation import runner  # noqa: E402

# A IA-6 é a baseline histórica. Defaults que apontassem para ela fariam uma simples
# reexecução (inclusive paga/Gemini) apagar a evidência usada na comparação.
_PADRAO_MD = "docs/avaliacao_ia_ia7.md"
_PADRAO_JSON = "docs/avaliacao_ia_ia7.json"


def _mesmo_caminho(esquerda: str, direita: str) -> bool:
    """Compara destinos absolutos, resolvendo ``..`` e aliases relativos sem exigir arquivo."""
    return os.path.normcase(str(Path(esquerda).resolve())) == os.path.normcase(
        str(Path(direita).resolve())
    )


def validar_destinos(*, baseline: str | None, json_saida: str) -> None:
    """Impede que o artefato anterior seja destruído depois de servir à comparação."""
    if baseline and _mesmo_caminho(baseline, json_saida):
        raise ValueError(
            "--baseline e --json apontam para o mesmo arquivo; use um destino novo para "
            "preservar a evidência anterior."
        )


def _argumentos() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avaliar_ia", description="Avaliação do assistente (Sprints IA-6/IA-7)."
    )
    parser.add_argument(
        "--provedor",
        choices=(runner.PROVEDOR_LOCAL, runner.PROVEDOR_GEMINI),
        default=runner.PROVEDOR_LOCAL,
        help="Provedor a avaliar. Padrão: local determinístico (offline, reprodutível).",
    )
    parser.add_argument(
        "--modelo",
        default=None,
        help=(
            "Modelo de chat a avaliar (só com --provedor gemini). Ex.: gemini-3.5-flash. "
            "Sem reserva: mede exatamente o modelo pedido."
        ),
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
        nargs="+",
        default=(),
        help="IDs específicos do conjunto (depuração de uma pergunta).",
    )
    return parser


def main() -> int:
    parser = _argumentos()
    args = parser.parse_args()
    if args.modelo is not None and args.provedor != runner.PROVEDOR_GEMINI:
        parser.error("--modelo só pode ser usado com --provedor gemini")
    if args.modelo is not None and not args.modelo.strip():
        parser.error("--modelo não aceita valor vazio")
    try:
        validar_destinos(baseline=args.baseline, json_saida=args.json_saida)
    except ValueError as exc:
        parser.error(str(exc))
    # O console do Windows abre em cp1252 e o relatório tem "→", "⚠️" e acentos. Sem isto
    # o comando morre no `print` **depois** de ter feito o trabalho todo.
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")

    anterior: dict[str, object] | None = None
    if args.baseline:
        try:
            anterior = relatorio_mod.carregar(args.baseline)
            plano = runner.plano_avaliacao(
                provedor=args.provedor,
                incluir_adversarial=not args.sem_adversarial,
                apenas=tuple(args.apenas or ()),
                modelo=args.modelo,
            )
            relatorio_mod.validar_baseline_planejado(anterior, plano)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    if args.provedor == runner.PROVEDOR_LOCAL:
        # Trava o provedor antes de qualquer construção de Settings memorizada: uma
        # avaliação "local" que silenciosamente falasse com o Gemini seria pior que erro.
        os.environ["ASSISTANT_PROVIDER"] = "local"

    resultado = runner.avaliar(
        provedor=args.provedor,
        incluir_adversarial=not args.sem_adversarial,
        apenas=tuple(args.apenas or ()),
        modelo=args.modelo,
    )
    markdown = relatorio_mod.para_markdown(resultado)
    atual = relatorio_mod.para_dict(resultado)
    atual["comparacao"] = None

    def gravar(corpo: str, dados: dict[str, object]) -> None:
        """Persiste o laudo. Chamada ANTES da comparação — e de novo depois dela.

        A medição é o produto caro (uma corrida paga contra o provedor real leva dezenas
        de minutos); a comparação é leitura sobre ela. Gravar só no fim significava perder
        as duas quando a segunda falhasse, e foi o que aconteceu uma vez.
        """
        Path(args.saida).parent.mkdir(parents=True, exist_ok=True)
        Path(args.saida).write_text(corpo, encoding="utf-8")
        Path(args.json_saida).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_saida).write_text(
            json.dumps(dados, ensure_ascii=False, indent=2, sort_keys=False), encoding="utf-8"
        )

    gravar(markdown, atual)

    regressao = False
    if args.baseline and anterior is not None:
        try:
            linhas = relatorio_mod.comparar(anterior, atual)
        except ValueError as exc:
            # Baseline incompatível é achado, não acidente: o laudo declara por que a
            # comparação não pôde ser feita e o veredito segue pelos critérios absolutos.
            atual["comparacao"] = {
                "baseline": str(Path(args.baseline)),
                "natureza": f"não realizada — {exc}",
                "regressao": None,
                "linhas": [],
            }
            markdown += (
                "\n\n## Comparação com a linha de base\n\n"
                f"**Não realizada.** {exc}\n\n"
                "A medição desta execução continua válida e está gravada acima; o que se "
                "perde é a leitura lado a lado, não o dado.\n"
            )
            print(f"[avaliacao] AVISO: {exc}", file=sys.stderr)
        else:
            regressao = relatorio_mod.houve_regressao(linhas)
            atual["comparacao"] = {
                "baseline": str(Path(args.baseline)),
                "natureza": relatorio_mod.natureza_comparacao(anterior, atual),
                "regressao": regressao,
                "linhas": [
                    {
                        "metrica": linha.metrica,
                        "antes": linha.antes,
                        "depois": linha.depois,
                        "comparavel": linha.comparavel,
                        "regrediu": linha.regrediu,
                        "trava": linha.trava,
                        "observacao": linha.observacao,
                    }
                    for linha in linhas
                ],
            }
            markdown += "\n" + relatorio_mod.comparacao_markdown(anterior, atual, linhas)
        gravar(markdown, atual)

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
        if metricas.legibilidade.numerador != metricas.legibilidade.denominador:
            motivos.append(f"legibilidade semântica {metricas.legibilidade.pct()}")
        if resultado.selecao_parcial:
            motivos.append("seleção parcial (--apenas) é diagnóstico, não laudo de troca")
        if not resultado.precondicoes_ok:
            motivos.append(
                "ambiente precisou de seed de referência; execução é apenas diagnóstica"
            )
        if not resultado.metadados_provedor_ok:
            motivos.append("model_version/finish_reason por request ausentes ou inconsistentes")
        if not resultado.sem_truncamento:
            motivos.append("houve resposta truncada pelo provedor")
        if not resultado.controle_negativo.get("detectou", True):
            motivos.append("controle negativo NÃO detectou a alucinação plantada")
        if regressao:
            motivos.append("regressão contra a linha de base")
        print(f"\n[avaliacao] REPROVADO: {'; '.join(motivos)}", file=sys.stderr)
        return 1

    print("\n[avaliacao] APROVADO: critérios de aceite IA-6/IA-7 atendidos.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
