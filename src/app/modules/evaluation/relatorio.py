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
from hashlib import sha256
from typing import Any

from app.modules.evaluation.metricas import Taxa
from app.modules.evaluation.runner import SCHEMA_RELATORIO, ResultadoAvaliacao

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
    "legibilidade",
)
#: Métricas de **orçamento**: variam, são reportadas, não travam.
METRICAS_ORCAMENTO = ("latencia_p95_ms", "custo_total_usd")


def para_dict(resultado: ResultadoAvaliacao) -> dict[str, Any]:
    """Serialização completa — é o formato do arquivo de comparação."""
    return {
        "schema_relatorio": resultado.escopo.get("schema", SCHEMA_RELATORIO),
        "versao_conjunto": resultado.versao_conjunto,
        "provedor": resultado.provedor,
        "modelo": resultado.modelo,
        "provedor_solicitado": resultado.provedor_solicitado,
        "modelo_solicitado": resultado.modelo_solicitado,
        "modelos_efetivos": dict(resultado.modelos_efetivos),
        "escopo": dict(resultado.escopo),
        "selecao": {
            "parcial": resultado.selecao_parcial,
            "ids_solicitados": list(resultado.ids_solicitados),
            "ids_perguntas_executadas": [item.id for item in resultado.execucoes],
            "ids_adversariais_executados": [item.id for item in resultado.adversarias],
        },
        "executado_em": resultado.executado_em.isoformat(),
        "duracao_s": resultado.duracao_s,
        "tipo_laudo": resultado.tipo_laudo,
        "aprovado": resultado.aprovado,
        "validade": {
            "precondicoes_ok": resultado.precondicoes_ok,
            "metadados_provedor_ok": resultado.metadados_provedor_ok,
            "sem_truncamento": resultado.sem_truncamento,
        },
        "precondicoes": resultado.precondicoes,
        "retentativas": resultado.retentativas,
        "controle_negativo": resultado.controle_negativo,
        "metricas": resultado.metricas.to_dict() if resultado.metricas else {},
        "perguntas": [e.to_dict() for e in resultado.execucoes],
        "adversarial": [a.to_dict() for a in resultado.adversarias],
    }


def para_json(resultado: ResultadoAvaliacao) -> str:
    return json.dumps(para_dict(resultado), ensure_ascii=False, indent=2, sort_keys=False)


def _linha_taxa(nome: str, taxa: Taxa, criterio: str) -> str:
    return f"| {nome} | {taxa.pct()} | {criterio} |"


def _usd(valor: str | None) -> str:
    return "n/a" if valor is None else f"US$ {valor}"


def para_markdown(resultado: ResultadoAvaliacao) -> str:
    """O relatório que se lê antes de aprovar a mudança."""
    m = resultado.metricas
    if m is None:  # pragma: no cover - só se chamado antes de agregar
        return "# Avaliação IA-6\n\nExecução sem métricas agregadas."
    linhas: list[str] = [
        "# Relatório de avaliação da IA — Sprints IA-6/IA-7",
        "",
        f"- **Conjunto**: `{resultado.versao_conjunto}` · "
        f"{m.total} perguntas + {m.adversarial.denominador} adversárias",
        f"- **Provedor / modelo**: `{resultado.provedor}` / `{resultado.modelo}`",
        f"- **Pedido**: provedor `{resultado.provedor_solicitado}` · modelo "
        f"`{resultado.modelo_solicitado or 'padrão configurado'}`",
        f"- **Model versions declaradas pelo provedor**: `{resultado.modelos_efetivos}`",
        "- **Escopo funcional**: `assistant.perguntar`; "
        "`assistant.resumo_executivo` **não foi avaliado por este A/B**",
        f"- **Valor probatório**: {resultado.escopo.get('evidencia_modelo')}",
        f"- **Executado em**: {resultado.executado_em.isoformat()} "
        f"(duração {resultado.duracao_s}s)",
        f"- **Tipo de laudo**: {resultado.tipo_laudo}",
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
        _linha_taxa(
            "Legibilidade (IA-7: significado antes, rótulo e implicação/ação)",
            m.legibilidade,
            "100% — trava",
        ),
        "",
        f"- **Latência** (p50 / p95 / máx / média): {m.latencia.p50_ms} / {m.latencia.p95_ms} / "
        f"{m.latencia.max_ms} / {m.latencia.media_ms} ms",
        f"- **Tokens (perguntas + ataques que chegaram ao provedor)**: "
        f"{m.custo.tokens_entrada} entrada + {m.custo.tokens_saida} saída",
        f"- **Custo**: {_usd(m.custo.total_usd)} total · "
        f"{_usd(m.custo.por_resposta_usd)} por resposta "
        f"({m.custo.respostas_cobradas} respostas; "
        f"{m.custo.requests_provedor} requests ao provedor) — {m.custo.fonte_preco}",
        f"- **Maior entrada por request**: {m.custo.max_tokens_entrada_por_request} tokens"
        + (
            f" · limite da faixa: {m.custo.limite_tokens_entrada_por_request}"
            if m.custo.limite_tokens_entrada_por_request is not None
            else " · faixa sem limite declarado"
        ),
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

    linhas += ["", "## Estabilidade do provedor", ""]
    if not resultado.retentativas:
        linhas.append("Nenhuma retentativa: todas as chamadas responderam de primeira.")
    else:
        # Aparece no laudo de propósito. Uma corrida que só fechou porque foi refeita
        # três vezes descreve um provedor instável, e isso é informação de produção —
        # não detalhe de execução a ser varrido para baixo do tapete.
        linhas.append(
            f"⚠️ {len(resultado.retentativas)} chamada(s) falharam de forma transitória "
            "e foram refeitas. O número abaixo é o da tentativa que respondeu."
        )
        linhas += ["", "| tentativa | pergunta | falha |", "|---|---|---|"]
        for item in resultado.retentativas:
            detalhe = str(item.get("detalhe", "")).replace("|", "/")[:160]
            pergunta = str(item.get("pergunta", "")).replace("|", "/")
            linhas.append(f"| {item.get('tentativa')} | {pergunta} | {detalhe} |")

    reprovadas = [e for e in resultado.execucoes if not e.julgamento.aprovado or not e.legivel]
    linhas += ["", "## Perguntas reprovadas", ""]
    if not reprovadas:
        linhas.append("Nenhuma.")
    else:
        linhas.append("| id | categoria | motivo |")
        linhas.append("|---|---|---|")
        for exec_ in reprovadas:
            motivos = "; ".join(
                [*exec_.julgamento.falhas, *exec_.legibilidade_falhas]
            ).replace("|", "\\|")
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
    comparavel: bool = True
    observacao: str | None = None


_TAXAS = (
    "aprovacao",
    "fundamentacao",
    "alucinacao_numerica",
    "recusa_correta",
    "defasagem_sinalizada",
    "adversarial",
    "legibilidade",
)

#: Taxas cujo denominador **mede comportamento** em vez de descrever o conjunto: as duas
#: têm por denominador "quantas respostas citaram número", que é resultado da execução.
#:
#: A distinção existe porque a guarda de compatibilidade já recusou, uma vez, uma
#: comparação legítima: a execução nova citou número em 66 respostas contra 62 da anterior
#: — precisamente porque passou a **poder** citar a faixa de alerta com fonte em vez de
#: descrevê-la em palavras — e a melhora foi lida como incompatibilidade. Exigir
#: denominador idêntico aqui é exigir que o comportamento não mude, que é o oposto do que
#: um A/B de prompt existe para detectar.
#:
#: Nas outras cinco o denominador é fixado pelo conjunto dourado (74 perguntas, 25 recusas
#: esperadas, 12 defasagens, 12 ataques): ali, divergência significa que mudou o conjunto
#: ou o gabarito, e a recusa continua correta.
DENOMINADORES_COMPORTAMENTAIS = ("fundamentacao", "alucinacao_numerica")


def _ids(dados: dict[str, Any], chave: str) -> tuple[str, ...]:
    bruto = dados.get(chave)
    if not isinstance(bruto, list):
        raise ValueError(f"Baseline incompatível: campo {chave!r} ausente ou inválido.")
    ids = tuple(str(item.get("id", "")) for item in bruto if isinstance(item, dict))
    if len(ids) != len(bruto) or any(not item for item in ids):
        raise ValueError(f"Baseline incompatível: {chave!r} contém item sem ID.")
    if len(set(ids)) != len(ids):
        raise ValueError(f"Baseline incompatível: {chave!r} contém IDs repetidos.")
    return ids


def _denominador(dados: dict[str, Any], nome: str) -> int | None:
    taxa = (dados.get("metricas") or {}).get(nome)
    if not isinstance(taxa, dict) or "denominador" not in taxa:
        return None
    return int(taxa["denominador"])


def _sha256_contrato(contrato: dict[str, Any]) -> str:
    bruto = json.dumps(
        contrato, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(bruto).hexdigest()


def _schema_e_contrato(
    dados: dict[str, Any], *, plano: bool = False
) -> tuple[str, dict[str, Any], str]:
    schema = str(dados.get("schema_relatorio") or "")
    if schema != SCHEMA_RELATORIO:
        raise ValueError(
            "Baseline incompatível: schema de relatório ausente, legado ou não suportado "
            f"({schema or 'ausente'}; esperado {SCHEMA_RELATORIO})."
        )
    origem = dados if plano else dados.get("escopo")
    if not isinstance(origem, dict):
        raise ValueError("Baseline incompatível: escopo de medição ausente.")
    contrato = origem.get("contrato_medicao")
    assinatura = str(origem.get("contrato_medicao_sha256") or "")
    if not isinstance(contrato, dict) or not contrato or not assinatura:
        raise ValueError(
            "Baseline incompatível: definição/fingerprint da medição ausente; "
            "artefato legado não é A/B válido."
        )
    calculada = _sha256_contrato(contrato)
    if assinatura != calculada:
        raise ValueError(
            "Baseline incompatível: fingerprint da medição não corresponde à definição."
        )
    return schema, contrato, assinatura


def _validar_schema_e_medicao(
    anterior: dict[str, Any], atual_ou_plano: dict[str, Any], *, plano: bool = False
) -> None:
    schema_anterior, contrato_anterior, assinatura_anterior = _schema_e_contrato(anterior)
    schema_atual, contrato_atual, assinatura_atual = _schema_e_contrato(
        atual_ou_plano, plano=plano
    )
    if (
        schema_anterior != schema_atual
        or assinatura_anterior != assinatura_atual
        or contrato_anterior != contrato_atual
    ):
        raise ValueError(
            "Baseline incompatível: schema ou definição da medição diverge entre "
            "as execuções."
        )


def _validar_consistencia_interna(
    dados: dict[str, Any], ids_perguntas: tuple[str, ...], ids_adversariais: tuple[str, ...]
) -> None:
    metricas = dados.get("metricas")
    if not isinstance(metricas, dict):
        raise ValueError("Baseline incompatível: objeto 'metricas' ausente.")
    total = int(metricas.get("total", -1))
    if total != len(ids_perguntas):
        raise ValueError(
            "Baseline incompatível: total de perguntas não corresponde aos IDs "
            f"serializados ({total} != {len(ids_perguntas)})."
        )
    for nome in _TAXAS:
        denominador = _denominador(dados, nome)
        if denominador is None:
            raise ValueError(
                f"Baseline incompatível: métrica obrigatória {nome!r} ausente."
            )
    for nome in ("aprovacao", "legibilidade"):
        denominador = _denominador(dados, nome)
        if denominador != total:
            raise ValueError(
                f"Baseline incompatível: denominador de {nome!r} ({denominador}) "
                f"não corresponde ao total ({total})."
            )
    denominador_adversarial = _denominador(dados, "adversarial")
    if denominador_adversarial != len(ids_adversariais):
        raise ValueError(
            "Baseline incompatível: denominador adversarial não corresponde aos IDs "
            f"serializados ({denominador_adversarial} != {len(ids_adversariais)})."
        )
    denominador_fundamentacao = _denominador(dados, "fundamentacao")
    denominador_alucinacao = _denominador(dados, "alucinacao_numerica")
    if denominador_fundamentacao != denominador_alucinacao:
        raise ValueError(
            "Baseline incompatível: fundamentação e alucinação numérica não usam o mesmo "
            "denominador de respostas que citaram número."
        )


def validar_baseline_planejado(anterior: dict[str, Any], plano: dict[str, Any]) -> None:
    """Pré-voo barato: barra conjunto/provedor incompatível antes da chamada paga."""
    _validar_schema_e_medicao(anterior, plano, plano=True)
    versao_anterior = str(anterior.get("versao_conjunto") or "")
    versao_planejada = str(plano.get("versao_conjunto") or "")
    if not versao_anterior or versao_anterior != versao_planejada:
        raise ValueError(
            "Baseline incompatível: versões do conjunto divergem "
            f"({versao_anterior or 'ausente'} != {versao_planejada or 'ausente'})."
        )

    escopo_anterior = anterior.get("escopo") or {}
    familia_anterior = str(
        escopo_anterior.get("familia_provedor") or anterior.get("provedor") or ""
    )
    familia_planejada = str(plano.get("familia_provedor") or "")
    if not familia_anterior or familia_anterior != familia_planejada:
        raise ValueError(
            "Baseline incompatível: famílias de provedor divergem "
            f"({familia_anterior or 'ausente'} != {familia_planejada or 'ausente'}). "
            "Saída local/template não é evidência comparável à saída de LLM."
        )
    solicitado_anterior = str(
        anterior.get("provedor_solicitado") or anterior.get("provedor") or ""
    )
    solicitado_planejado = str(plano.get("provedor_solicitado") or "")
    if not solicitado_anterior or solicitado_anterior != solicitado_planejado:
        raise ValueError(
            "Baseline incompatível: provedores solicitados divergem "
            f"({solicitado_anterior or 'ausente'} != {solicitado_planejado or 'ausente'})."
        )

    if set(_ids(anterior, "perguntas")) != set(plano.get("ids_perguntas") or []):
        raise ValueError("Baseline incompatível: IDs de perguntas divergem entre execuções.")
    if set(_ids(anterior, "adversarial")) != set(plano.get("ids_adversariais") or []):
        raise ValueError("Baseline incompatível: IDs adversariais divergem entre execuções.")


def validar_compatibilidade(anterior: dict[str, Any], atual: dict[str, Any]) -> None:
    """Recusa A/B cujo conjunto ou universo medido mudou entre as execuções."""
    _validar_schema_e_medicao(anterior, atual)
    versao_anterior = str(anterior.get("versao_conjunto") or "")
    versao_atual = str(atual.get("versao_conjunto") or "")
    if not versao_anterior or versao_anterior != versao_atual:
        raise ValueError(
            "Baseline incompatível: versões do conjunto divergem "
            f"({versao_anterior or 'ausente'} != {versao_atual or 'ausente'})."
        )

    escopo_anterior = anterior.get("escopo") or {}
    escopo_atual = atual.get("escopo") or {}
    familia_anterior = str(
        escopo_anterior.get("familia_provedor") or anterior.get("provedor") or ""
    )
    familia_atual = str(escopo_atual.get("familia_provedor") or atual.get("provedor") or "")
    if not familia_anterior or familia_anterior != familia_atual:
        raise ValueError(
            "Baseline incompatível: famílias de provedor divergem "
            f"({familia_anterior or 'ausente'} != {familia_atual or 'ausente'}). "
            "Saída local/template não é evidência comparável à saída de LLM."
        )
    provedor_anterior = str(anterior.get("provedor_solicitado") or anterior.get("provedor") or "")
    provedor_atual = str(atual.get("provedor_solicitado") or atual.get("provedor") or "")
    if not provedor_anterior or provedor_anterior != provedor_atual:
        raise ValueError(
            "Baseline incompatível: provedores solicitados divergem "
            f"({provedor_anterior or 'ausente'} != {provedor_atual or 'ausente'})."
        )
    efetivo_anterior = str(anterior.get("provedor") or "")
    efetivo_atual = str(atual.get("provedor") or "")
    if not efetivo_anterior or efetivo_anterior != efetivo_atual:
        raise ValueError(
            "Baseline incompatível: provedores efetivos divergem "
            f"({efetivo_anterior or 'ausente'} != {efetivo_atual or 'ausente'})."
        )

    perguntas_antes = _ids(anterior, "perguntas")
    perguntas_depois = _ids(atual, "perguntas")
    adversarial_antes = _ids(anterior, "adversarial")
    adversarial_depois = _ids(atual, "adversarial")
    if set(perguntas_antes) != set(perguntas_depois):
        raise ValueError("Baseline incompatível: IDs de perguntas divergem entre execuções.")
    if set(adversarial_antes) != set(adversarial_depois):
        raise ValueError("Baseline incompatível: IDs adversariais divergem entre execuções.")

    _validar_consistencia_interna(anterior, perguntas_antes, adversarial_antes)
    _validar_consistencia_interna(atual, perguntas_depois, adversarial_depois)
    for nome in _TAXAS:
        antes = _denominador(anterior, nome)
        depois = _denominador(atual, nome)
        if antes is None or depois is None:
            raise ValueError(
                f"Baseline incompatível: métrica {nome!r} ausente em uma das execuções."
            )
        if antes != depois and nome not in DENOMINADORES_COMPORTAMENTAIS:
            raise ValueError(
                f"Baseline incompatível: denominador de {nome!r} diverge "
                f"({antes} != {depois})."
            )


def natureza_comparacao(anterior: dict[str, Any], atual: dict[str, Any]) -> str:
    """Descreve diferenças observáveis; nunca transforma correlação em causalidade."""
    hash_anterior = (anterior.get("escopo") or {}).get("prompt_efetivo_sha256")
    hash_atual = (atual.get("escopo") or {}).get("prompt_efetivo_sha256")
    execucao_anterior = (anterior.get("escopo") or {}).get("execucao_fingerprint_sha256")
    execucao_atual = (atual.get("escopo") or {}).get("execucao_fingerprint_sha256")
    modelo_mudou = (
        anterior.get("modelo") != atual.get("modelo")
        or anterior.get("modelo_solicitado") != atual.get("modelo_solicitado")
        or anterior.get("modelos_efetivos") != atual.get("modelos_efetivos")
    )

    def manifesto_sem_identidade_do_modelo(dados: dict[str, Any]) -> dict[str, Any] | None:
        manifesto = (dados.get("escopo") or {}).get("manifesto_execucao")
        if not isinstance(manifesto, dict):
            return None
        normalizado = json.loads(json.dumps(manifesto))
        selecao = normalizado.get("selecao_modelo")
        if isinstance(selecao, dict) and selecao.get("modelo_explicito") is not None:
            # O valor continua no manifesto/fingerprint original para auditoria; a cópia
            # serve apenas para perguntar se **todo o resto** ficou igual no A/B.
            selecao["modelo_explicito"] = "<MODELO_COMPARADO>"
        return normalizado

    if not hash_anterior or not hash_atual or not execucao_anterior or not execucao_atual:
        return (
            "indeterminada — falta fingerprint observável de prompt/geração/tools/SDK "
            "em uma execução"
        )
    if hash_anterior != hash_atual and modelo_mudou:
        return (
            "mudança observada de prompt + modelo; efeitos não podem ser atribuídos "
            "isoladamente"
        )
    if hash_anterior != hash_atual:
        return (
            "mudança observada de prompt/configuração; o A/B não demonstra causalidade"
        )
    if execucao_anterior != execucao_atual:
        manifesto_anterior = manifesto_sem_identidade_do_modelo(anterior)
        manifesto_atual = manifesto_sem_identidade_do_modelo(atual)
        if (
            modelo_mudou
            and manifesto_anterior is not None
            and manifesto_anterior == manifesto_atual
        ):
            return (
                "modelo foi a única diferença observável do manifesto; associação não "
                "prova causalidade externa"
            )
        return (
            "prompt textual igual, mas configuração observável de geração/tools/SDK "
            "mudou; efeitos não são atribuíveis ao modelo"
        )
    if modelo_mudou:
        return (
            "modelo declarado mudou sob fingerprint observável igual; associação não "
            "prova causalidade e o backend externo ainda pode variar"
        )
    return (
        "reexecução sob o mesmo fingerprint observável; infraestrutura externa ainda "
        "pode variar"
    )


def _valores_comparaveis(dados: dict[str, Any]) -> dict[str, tuple[float | None, str]]:
    """(valor numérico, texto) por métrica — o que a comparação sabe confrontar."""
    m = dados.get("metricas") or {}
    saida: dict[str, tuple[float | None, str]] = {}
    for nome in _TAXAS:
        taxa = m.get(nome)
        if not isinstance(taxa, dict) or "denominador" not in taxa:
            saida[nome] = (None, "n/a")
            continue
        numerador = int(taxa.get("numerador", 0))
        denominador = int(taxa["denominador"])
        valor = None if denominador == 0 else float(taxa.get("valor", numerador / denominador))
        texto = "n/a" if valor is None else f"{valor * 100:.1f}% ({numerador}/{denominador})"
        saida[nome] = (valor, texto)
    latencia = m.get("latencia") or {}
    if "p95_ms" in latencia:
        p95 = float(latencia["p95_ms"])
        saida["latencia_p95_ms"] = (p95, f"{int(p95)} ms")
    else:
        saida["latencia_p95_ms"] = (None, "n/a")
    custo = m.get("custo") or {}
    bruto_total = custo.get("total_usd")
    if bruto_total is None:
        saida["custo_total_usd"] = (None, "n/a")
    else:
        saida["custo_total_usd"] = (float(bruto_total), f"US$ {bruto_total}")
    return saida


def comparar(anterior: dict[str, Any], atual: dict[str, Any]) -> list[LinhaComparacao]:
    """Confronta duas execuções. Regressão = piora numa métrica de qualidade.

    ``alucinacao_numerica`` inverte o sentido: subir é piorar. Tratá-la como as demais é
    o erro que faria a comparação aprovar exatamente a troca que ela existe para barrar.
    """
    validar_compatibilidade(anterior, atual)
    de = _valores_comparaveis(anterior)
    para = _valores_comparaveis(atual)
    linhas: list[LinhaComparacao] = []
    for metrica in sorted(set(de) | set(para)):
        valor_antes, texto_antes = de.get(metrica, (None, "n/a"))
        valor_depois, texto_depois = para.get(metrica, (None, "n/a"))
        comparavel = valor_antes is not None and valor_depois is not None
        menor_e_melhor = metrica in {"alucinacao_numerica", *METRICAS_ORCAMENTO}
        piorou = False
        if comparavel:
            assert valor_antes is not None and valor_depois is not None
            piorou = valor_depois > valor_antes if menor_e_melhor else valor_depois < valor_antes
        # Taxa continua comparável quando o denominador comportamental muda (0/66 é
        # melhor que 8/62 em qualquer leitura), mas a mudança tem de aparecer: quem lê
        # "12.9% → 0.0%" precisa saber que as duas frações não têm a mesma base.
        observacao = None if comparavel else "sem valor comparável"
        if comparavel and metrica in DENOMINADORES_COMPORTAMENTAIS:
            den_antes = _denominador(anterior, metrica)
            den_depois = _denominador(atual, metrica)
            if den_antes != den_depois:
                observacao = (
                    f"denominador mudou ({den_antes} → {den_depois} respostas citaram "
                    "número): a taxa compara, a população não é a mesma"
                )
        linhas.append(
            LinhaComparacao(
                metrica=metrica,
                antes=texto_antes,
                depois=texto_depois,
                regrediu=piorou,
                trava=metrica in METRICAS_TRAVA,
                comparavel=comparavel,
                observacao=observacao,
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
        f"- **Natureza da comparação**: {natureza_comparacao(anterior, atual)}",
        "",
        "| Métrica | Antes | Depois | |",
        "|---|---|---|---|",
    ]
    corpo = []
    for linha in linhas:
        if not linha.comparavel:
            marca = "não comparável"
        elif linha.regrediu and linha.trava:
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
