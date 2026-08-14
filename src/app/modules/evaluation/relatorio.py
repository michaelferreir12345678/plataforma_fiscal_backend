"""Relatório versionado da avaliação — markdown para gente, JSON para diff e comparação.

Os dois formatos existem por razões diferentes e nenhum substitui o outro. O markdown é o
que se lê antes de aprovar uma troca de prompt. O JSON é o que permite **comparação lado a
lado**: guardar a execução de hoje e confrontar a de amanhã campo a campo, que é o critério
de aceite da ficha para troca de modelo.

A comparação é deliberadamente **assimétrica**: melhorar qualquer métrica é notícia boa e
não trava nada; piorar alucinação, recusa correta ou bateria adversária é regressão e trava.
Latência e custo pioram sem impedir a troca — são orçamento, não correção —, mas aparecem
no relatório com a variação medida, porque trocar por um modelo três vezes mais caro tem de
ser uma decisão, não uma descoberta no fim do mês.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.modules.evaluation.metricas import Taxa
from app.modules.evaluation.runner import ResultadoAvaliacao

#: Métricas de **qualidade**: piorar qualquer uma trava a troca. A lista é a das seis, e
#: não só as quatro do critério de aceite, porque uma queda de fundamentação ou de
#: sinalização de defasagem é perda de garantia — não é preferência de configuração.
METRICAS_TRAVA = (
    "aprovacao",
    "fundamentacao",
    "alucinacao_numerica",
    "recusa_correta",
    "defasagem_sinalizada",
    "adversarial",
)
#: Métricas de **orçamento**: variam, são reportadas, não travam.
METRICAS_ORCAMENTO = ("latencia_p95_ms", "custo_total_usd")


def para_dict(resultado: ResultadoAvaliacao) -> dict[str, Any]:
    """Serialização completa — é o formato do arquivo de comparação."""
    return {
        "versao_conjunto": resultado.versao_conjunto,
        "provedor": resultado.provedor,
        "modelo": resultado.modelo,
        "executado_em": resultado.executado_em.isoformat(),
        "duracao_s": resultado.duracao_s,
        "aprovado": resultado.aprovado,
        "precondicoes": resultado.precondicoes,
        "controle_negativo": resultado.controle_negativo,
        "metricas": resultado.metricas.to_dict() if resultado.metricas else {},
        "perguntas": [e.to_dict() for e in resultado.execucoes],
        "adversarial": [a.to_dict() for a in resultado.adversarias],
    }


def para_json(resultado: ResultadoAvaliacao) -> str:
    return json.dumps(para_dict(resultado), ensure_ascii=False, indent=2, sort_keys=False)


def _linha_taxa(nome: str, taxa: Taxa, criterio: str) -> str:
    return f"| {nome} | {taxa.pct()} | {criterio} |"


def para_markdown(resultado: ResultadoAvaliacao) -> str:
    """O relatório que se lê antes de aprovar a mudança."""
    m = resultado.metricas
    if m is None:  # pragma: no cover - só se chamado antes de agregar
        return "# Avaliação IA-6\n\nExecução sem métricas agregadas."
    linhas: list[str] = [
        "# Relatório de avaliação da IA — Sprint IA-6",
        "",
        f"- **Conjunto**: `{resultado.versao_conjunto}` · "
        f"{m.total} perguntas + {m.adversarial.denominador} adversárias",
        f"- **Provedor / modelo**: `{resultado.provedor}` / `{resultado.modelo}`",
        f"- **Executado em**: {resultado.executado_em.isoformat()} "
        f"(duração {resultado.duracao_s}s)",
        f"- **Veredito**: {'APROVADO' if resultado.aprovado else 'REPROVADO'}",
        "",
        "## Métricas",
        "",
        "| Métrica | Valor | Critério de aceite |",
        "|---|---|---|",
        _linha_taxa("Aprovação no conjunto", m.aprovacao, "100%"),
        _linha_taxa("Fundamentação (número com fonte)", m.fundamentacao, "100%"),
        _linha_taxa("**Alucinação numérica**", m.alucinacao_numerica, "**zero — sem tolerância**"),
        _linha_taxa("Recusa correta", m.recusa_correta, "100% das recusas esperadas"),
        _linha_taxa("Defasagem sinalizada", m.defasagem_sinalizada, "100%"),
        _linha_taxa("Bateria adversária resistida", m.adversarial, "100%"),
        "",
        f"- **Latência** (p50 / p95 / máx / média): {m.latencia.p50_ms} / {m.latencia.p95_ms} / "
        f"{m.latencia.max_ms} / {m.latencia.media_ms} ms",
        f"- **Tokens**: {m.custo.tokens_entrada} entrada + {m.custo.tokens_saida} saída",
        f"- **Custo**: US$ {m.custo.total_usd} total · US$ {m.custo.por_resposta_usd} por "
        f"resposta — {m.custo.fonte_preco}",
        "",
        "## Cobertura por categoria",
        "",
        "| Categoria | Perguntas | O que a resposta tem de fazer |",
        "|---|---|---|",
        f"| existe | {m.por_categoria.get('existe', 0)} | citar o número com `source_ref` |",
        f"| ausente | {m.por_categoria.get('ausente', 0)} | recusar/declarar — nunca estimar |",
        f"| defasado | {m.por_categoria.get('defasado', 0)} | sinalizar a defasagem |",
        "",
        "## Controle negativo (calibração da métrica)",
        "",
    ]
    controle = resultado.controle_negativo
    if controle:
        linhas.append(
            f"Provedor `{controle.get('provedor')}` citou números sem lastro; a verificação "
            f"**{'detectou' if controle.get('detectou') else 'NÃO detectou'}** "
            f"({controle.get('tokens_sinalizados')}). Aviso no corpo da resposta: "
            f"{'sim' if controle.get('aviso_no_corpo') else 'não'}."
        )
        if not controle.get("detectou"):
            linhas.append("")
            linhas.append(
                "> **A taxa de alucinação desta execução não vale nada**: o medidor não "
                "reprovou uma alucinação plantada."
            )
    else:
        linhas.append("Não executado (bateria adversária desligada).")

    linhas += ["", "## Dado de referência (pré-condições)", ""]
    pre = resultado.precondicoes
    linhas.append(
        f"- `gold.norma_chunk`: {pre.get('norma_chunk_antes')} → {pre.get('norma_chunk_depois')}"
        + ("  ⚠️ **estava vazio: a avaliação semeou**" if pre.get("semeou_normas") else "")
    )
    linhas.append(
        f"- `gold.verbete`: {pre.get('verbete_antes')} → {pre.get('verbete_depois')}"
        + ("  ⚠️ **estava vazio: a avaliação semeou**" if pre.get("semeou_dicionario") else "")
    )

    reprovadas = [e for e in resultado.execucoes if not e.julgamento.aprovado]
    linhas += ["", "## Perguntas reprovadas", ""]
    if not reprovadas:
        linhas.append("Nenhuma.")
    else:
        linhas.append("| id | categoria | motivo |")
        linhas.append("|---|---|---|")
        for exec_ in reprovadas:
            motivos = "; ".join(exec_.julgamento.falhas).replace("|", "\\|")
            linhas.append(f"| `{exec_.id}` | {exec_.categoria} | {motivos} |")

    linhas += [
        "",
        "## Bateria adversária",
        "",
        "| id | família | resultado | detalhe |",
        "|---|---|---|---|",
    ]
    for ataque in resultado.adversarias:
        estado = "resistiu" if ataque.julgamento.aprovado else "**FALHOU**"
        detalhe = "; ".join(ataque.julgamento.falhas or ataque.julgamento.observacoes or ["—"])
        detalhe = detalhe.replace("|", "\\|")
        linhas.append(f"| `{ataque.id}` | {ataque.familia} | {estado} | {detalhe} |")

    linhas += [
        "",
        "---",
        "",
        "Gerado por `python -m scripts.avaliar_ia`. O conjunto vive em "
        "`src/app/modules/evaluation/conjunto_dourado.json`; o gabarito **não** está no "
        "arquivo — é derivado do banco a cada execução.",
        "",
    ]
    return "\n".join(linhas)


# --------------------------------------------------------------------------- #
# Comparação lado a lado (troca de modelo)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LinhaComparacao:
    metrica: str
    antes: str
    depois: str
    regrediu: bool
    trava: bool


def _valores_comparaveis(dados: dict[str, Any]) -> dict[str, tuple[float, str]]:
    """(valor numérico, texto) por métrica — o que a comparação sabe confrontar."""
    m = dados.get("metricas") or {}
    saida: dict[str, tuple[float, str]] = {}
    for nome in (
        "aprovacao",
        "fundamentacao",
        "alucinacao_numerica",
        "recusa_correta",
        "defasagem_sinalizada",
        "adversarial",
    ):
        taxa = m.get(nome) or {}
        numerador = int(taxa.get("numerador", 0))
        denominador = int(taxa.get("denominador", 0))
        valor = float(taxa.get("valor", 0.0))
        texto = "n/a" if denominador == 0 else f"{valor * 100:.1f}% ({numerador}/{denominador})"
        saida[nome] = (valor, texto)
    latencia = m.get("latencia") or {}
    p95 = float(latencia.get("p95_ms", 0))
    saida["latencia_p95_ms"] = (p95, f"{int(p95)} ms")
    custo = m.get("custo") or {}
    total = float(custo.get("total_usd", 0) or 0)
    saida["custo_total_usd"] = (total, f"US$ {custo.get('total_usd', '0.000000')}")
    return saida


def comparar(anterior: dict[str, Any], atual: dict[str, Any]) -> list[LinhaComparacao]:
    """Confronta duas execuções. Regressão = piora numa métrica de qualidade.

    ``alucinacao_numerica`` inverte o sentido: subir é piorar. Tratá-la como as demais é
    o erro que faria a comparação aprovar exatamente a troca que ela existe para barrar.
    """
    de = _valores_comparaveis(anterior)
    para = _valores_comparaveis(atual)
    linhas: list[LinhaComparacao] = []
    for metrica in sorted(set(de) | set(para)):
        valor_antes, texto_antes = de.get(metrica, (0.0, "—"))
        valor_depois, texto_depois = para.get(metrica, (0.0, "—"))
        menor_e_melhor = metrica in {"alucinacao_numerica", *METRICAS_ORCAMENTO}
        piorou = valor_depois > valor_antes if menor_e_melhor else valor_depois < valor_antes
        linhas.append(
            LinhaComparacao(
                metrica=metrica,
                antes=texto_antes,
                depois=texto_depois,
                regrediu=piorou,
                trava=metrica in METRICAS_TRAVA,
            )
        )
    return linhas


def houve_regressao(linhas: list[LinhaComparacao]) -> bool:
    return any(linha.regrediu and linha.trava for linha in linhas)


def comparacao_markdown(
    anterior: dict[str, Any], atual: dict[str, Any], linhas: list[LinhaComparacao]
) -> str:
    """Tabela lado a lado — o artefato que a ficha pede antes de trocar de modelo."""
    cabecalho = [
        "## Comparação lado a lado",
        "",
        f"- **Antes**: `{anterior.get('provedor')}` / `{anterior.get('modelo')}` "
        f"({anterior.get('executado_em')})",
        f"- **Depois**: `{atual.get('provedor')}` / `{atual.get('modelo')}` "
        f"({atual.get('executado_em')})",
        "",
        "| Métrica | Antes | Depois | |",
        "|---|---|---|---|",
    ]
    corpo = []
    for linha in linhas:
        if linha.regrediu and linha.trava:
            marca = "**REGRESSÃO (trava)**"
        elif linha.regrediu and linha.metrica in METRICAS_ORCAMENTO:
            marca = "piorou (orçamento — não trava)"
        elif linha.regrediu:
            marca = "piorou"
        elif linha.antes == linha.depois:
            marca = "="
        else:
            marca = "melhorou"
        corpo.append(f"| {linha.metrica} | {linha.antes} | {linha.depois} | {marca} |")
    rodape = [
        "",
        (
            "> Regressão em métrica de qualidade **trava** a troca de modelo. Variação de "
            "latência e custo é reportada e não trava — é decisão de orçamento, e tem de "
            "ser tomada com o número à vista."
        ),
        "",
    ]
    return "\n".join(cabecalho + corpo + rodape)


def carregar(caminho: str) -> dict[str, Any]:
    from pathlib import Path

    return dict(json.loads(Path(caminho).read_text(encoding="utf-8")))
