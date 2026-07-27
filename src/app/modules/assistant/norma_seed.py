"""Corpo normativo curado do assistente (LRF/CF/MDF) + indexação no *vector store*.

São textos públicos (Lei Complementar 101/2000, Constituição Federal e Manual de
Demonstrativos Fiscais/STN), fatiados por dispositivo e resumidos com fidelidade. Cada
*chunk* declara os ``indicadores`` a que se aplica (para casar com os fatos calculados) e
``tags`` (rede de segurança lexical). A seed embeda com o *embedder* ativo e faz *upsert*
por ``(fonte, dispositivo)`` — idempotente.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.modules.assistant import repository
from app.modules.assistant.embeddings import Embedder

# Cada item: fonte, dispositivo, titulo, texto, indicadores, tags.
CORPUS: list[dict[str, Any]] = [
    # ---------------------- LRF (LC 101/2000) ----------------------
    {
        "fonte": "LRF",
        "dispositivo": "LRF art. 2º, IV",
        "titulo": "Receita Corrente Líquida (RCL)",
        "texto": (
            "A Receita Corrente Líquida é o somatório das receitas correntes arrecadadas "
            "nos doze meses anteriores ao de referência, deduzidas as contribuições dos "
            "servidores ao custeio do RPPS, as receitas de compensação previdenciária e, "
            "nos Estados e Municípios, as parcelas de transferências constitucionais "
            "entregues a outros entes. É o denominador dos limites fiscais da LRF."
        ),
        "indicadores": ["rcl"],
        "tags": ["rcl", "receita", "corrente", "liquida", "denominador", "12 meses"],
    },
    {
        "fonte": "LRF",
        "dispositivo": "LRF art. 19",
        "titulo": "Limite global de despesa com pessoal",
        "texto": (
            "A despesa total com pessoal não pode exceder, em relação à RCL: 50% para a "
            "União e 60% para Estados e Municípios. Os percentuais são apurados ao final "
            "de cada quadrimestre."
        ),
        "indicadores": ["pessoal_executivo"],
        "tags": ["pessoal", "limite", "60", "50", "rcl", "despesa"],
    },
    {
        "fonte": "LRF",
        "dispositivo": "LRF art. 20",
        "titulo": "Repartição do limite de pessoal por Poder",
        "texto": (
            "O limite global reparte-se por Poder. Nos Municípios: 6% para o Legislativo "
            "(incluído o Tribunal de Contas, quando houver) e 54% para o Executivo. Nos "
            "Estados: 3% para o Legislativo/TCE, 6% para o Judiciário, 49% para o "
            "Executivo e 2% para o Ministério Público. Por isso o teto de pessoal do "
            "Executivo é 54% (município) e 49% (estado) da RCL."
        ),
        "indicadores": ["pessoal_executivo"],
        "tags": ["pessoal", "executivo", "54", "49", "poder", "limite", "legislativo"],
    },
    {
        "fonte": "LRF",
        "dispositivo": "LRF art. 22, parágrafo único",
        "titulo": "Limite prudencial (95%)",
        "texto": (
            "Atingidos 95% do limite de pessoal (limite prudencial), ficam vedados ao "
            "Poder ou órgão que houver incorrido no excesso: concessão de vantagem ou "
            "aumento de remuneração, criação de cargo/função, contratação de horas extras "
            "e provimento de cargo público, salvo reposição decorrente de aposentadoria/"
            "falecimento em áreas de educação, saúde e segurança."
        ),
        "indicadores": ["pessoal_executivo"],
        "tags": ["prudencial", "95", "pessoal", "vedacao", "aumento"],
    },
    {
        "fonte": "LRF",
        "dispositivo": "LRF art. 23",
        "titulo": "Recondução da despesa de pessoal ao limite",
        "texto": (
            "Ultrapassado o limite de pessoal, o excedente deve ser eliminado nos dois "
            "quadrimestres seguintes, sendo pelo menos um terço no primeiro, adotando-se, "
            "entre outras, as providências dos §§ 3º e 4º do art. 169 da Constituição "
            "(redução de cargos em comissão e exoneração de servidores)."
        ),
        "indicadores": ["pessoal_executivo"],
        "tags": ["recondução", "prazo", "quadrimestre", "pessoal", "excesso"],
    },
    {
        "fonte": "LRF",
        "dispositivo": "LRF art. 31",
        "titulo": "Recondução da dívida ao limite",
        "texto": (
            "Se a dívida consolidada de um ente ultrapassar o respectivo limite ao final "
            "de um quadrimestre, deverá ser a ele reconduzida até o término dos três "
            "quadrimestres subsequentes, reduzindo o excedente em pelo menos 25% no "
            "primeiro. Enquanto perdurar o excesso, o ente fica proibido de realizar "
            "operação de crédito interna ou externa."
        ),
        "indicadores": ["divida_consolidada_liquida"],
        "tags": ["divida", "recondução", "prazo", "operação de crédito", "limite"],
    },
    {
        "fonte": "LRF",
        "dispositivo": "LRF art. 42",
        "titulo": "Restos a pagar em fim de mandato",
        "texto": (
            "É vedado ao titular de Poder ou órgão, nos dois últimos quadrimestres do "
            "mandato, contrair obrigação de despesa que não possa ser cumprida "
            "integralmente dentro dele, ou que tenha parcelas a pagar no exercício "
            "seguinte sem suficiente disponibilidade de caixa para esse efeito."
        ),
        "indicadores": ["resultado_primario"],
        "tags": ["restos a pagar", "fim de mandato", "caixa", "art 42", "disponibilidade"],
    },
    {
        "fonte": "LRF",
        "dispositivo": "LRF art. 52",
        "titulo": "Relatório Resumido da Execução Orçamentária (RREO)",
        "texto": (
            "O RREO é bimestral e deve ser publicado até trinta dias após o encerramento "
            "de cada bimestre. Abrange o balanço orçamentário e demonstrativos de "
            "receitas e despesas, evidenciando o cumprimento das metas fiscais."
        ),
        "indicadores": ["rcl", "resultado_primario"],
        "tags": ["rreo", "bimestral", "prazo", "publicação", "30 dias"],
    },
    {
        "fonte": "LRF",
        "dispositivo": "LRF art. 55",
        "titulo": "Relatório de Gestão Fiscal (RGF)",
        "texto": (
            "O RGF é emitido ao final de cada quadrimestre (ou semestre, para Municípios "
            "com menos de cinquenta mil habitantes que optem por essa periodicidade), "
            "contendo os demonstrativos de despesa com pessoal, dívida consolidada, "
            "garantias e operações de crédito, com a apuração dos limites e a indicação "
            "das medidas corretivas quando ultrapassados."
        ),
        "indicadores": ["pessoal_executivo", "divida_consolidada_liquida"],
        "tags": ["rgf", "quadrimestre", "semestre", "pessoal", "divida", "publicação"],
    },
    # ---------------------- Constituição Federal ----------------------
    {
        "fonte": "CF",
        "dispositivo": "CF art. 198, §2º",
        "titulo": "Aplicação mínima em saúde (ASPS)",
        "texto": (
            "Os entes aplicam anualmente em ações e serviços públicos de saúde recursos "
            "mínimos derivados da receita de impostos e transferências: os Municípios, no "
            "mínimo 15%; os Estados e o Distrito Federal, no mínimo 12%. A base considera "
            "impostos próprios e as transferências constitucionais recebidas."
        ),
        "indicadores": ["saude_asps"],
        "tags": ["saúde", "asps", "15", "12", "mínimo", "impostos", "transferências"],
    },
    {
        "fonte": "CF",
        "dispositivo": "CF art. 212",
        "titulo": "Manutenção e Desenvolvimento do Ensino (MDE)",
        "texto": (
            "Os Estados, o Distrito Federal e os Municípios aplicarão, no mínimo, 25% da "
            "receita resultante de impostos, compreendida a proveniente de transferências, "
            "na manutenção e desenvolvimento do ensino. A União aplica no mínimo 18%."
        ),
        "indicadores": ["educacao_mde"],
        "tags": ["educação", "mde", "25", "ensino", "mínimo", "impostos"],
    },
    {
        "fonte": "CF",
        "dispositivo": "CF art. 212-A, XI (FUNDEB)",
        "titulo": "FUNDEB — 70% em remuneração de profissionais",
        "texto": (
            "No âmbito do FUNDEB, pelo menos 70% dos recursos anuais totais dos Fundos "
            "devem ser destinados ao pagamento da remuneração dos profissionais da "
            "educação básica em efetivo exercício, conforme o art. 212-A da Constituição "
            "(EC 108/2020) e a Lei 14.113/2020."
        ),
        "indicadores": ["educacao_mde"],
        "tags": ["fundeb", "70", "profissionais", "remuneração", "educação básica"],
    },
    {
        "fonte": "CF",
        "dispositivo": "CF art. 167, III",
        "titulo": "Regra de ouro (operações de crédito)",
        "texto": (
            "É vedada a realização de operações de crédito que excedam o montante das "
            "despesas de capital, ressalvadas as autorizadas mediante créditos "
            "suplementares ou especiais com finalidade precisa, aprovados pelo Legislativo "
            "por maioria absoluta (regra de ouro)."
        ),
        "indicadores": ["divida_consolidada_liquida"],
        "tags": ["operação de crédito", "regra de ouro", "despesa de capital", "167"],
    },
    {
        "fonte": "CF",
        "dispositivo": "CF art. 169",
        "titulo": "Despesa de pessoal nos limites da lei complementar",
        "texto": (
            "A despesa com pessoal ativo e inativo e pensionistas dos entes não pode "
            "exceder os limites estabelecidos em lei complementar (a LRF). O "
            "descumprimento acarreta as medidas de ajuste previstas nos §§ 3º e 4º, "
            "incluindo redução de despesas com cargos em comissão e exoneração."
        ),
        "indicadores": ["pessoal_executivo"],
        "tags": ["pessoal", "limite", "lei complementar", "ajuste", "169"],
    },
    # ---------------------- Manual de Demonstrativos Fiscais (STN) ----------------------
    {
        "fonte": "MDF",
        "dispositivo": "MDF — Dívida Consolidada Líquida",
        "titulo": "Cálculo e limite da DCL",
        "texto": (
            "A Dívida Consolidada Líquida é a dívida consolidada bruta deduzidas as "
            "disponibilidades de caixa e os demais haveres financeiros. Por resolução do "
            "Senado Federal (nº 40/2001 e 43/2001), o limite da DCL é de 120% da RCL para "
            "Municípios e 200% da RCL para Estados; as operações de crédito limitam-se a "
            "16% e as garantias a 22% da RCL."
        ),
        "indicadores": ["divida_consolidada_liquida"],
        "tags": ["dcl", "divida", "120", "200", "16", "22", "haveres", "disponibilidades"],
    },
    {
        "fonte": "MDF",
        "dispositivo": "MDF — Despesa Total com Pessoal (exclusões)",
        "titulo": "DTP e exclusões da despesa de pessoal",
        "texto": (
            "A Despesa Total com Pessoal é a despesa bruta com pessoal deduzidas as "
            "exclusões legais: indenizações por demissão, incentivos à demissão "
            "voluntária, decisões judiciais de exercícios anteriores, despesa de inativos "
            "e pensionistas custeada com recursos do RPPS e a convocação para "
            "convocatória extraordinária. A existência de RPPS altera as exclusões "
            "aplicáveis."
        ),
        "indicadores": ["pessoal_executivo"],
        "tags": ["pessoal", "exclusões", "rpps", "inativos", "dtp", "indenizações"],
    },
    {
        "fonte": "MDF",
        "dispositivo": "MDF — Restos a Pagar sem lastro nos mínimos",
        "titulo": "RP não processados sem disponibilidade de caixa",
        "texto": (
            "Na apuração dos mínimos constitucionais de saúde e educação, os restos a "
            "pagar não processados inscritos sem a correspondente disponibilidade "
            "financeira (sem lastro) são deduzidos da despesa aplicada — não contam como "
            "aplicação no exercício até que haja cobertura de caixa."
        ),
        "indicadores": ["saude_asps", "educacao_mde"],
        "tags": ["restos a pagar", "sem lastro", "mínimo", "saúde", "educação", "caixa"],
    },
    {
        "fonte": "MDF",
        "dispositivo": "MDF — Resultado primário e nominal (RREO Anexo 6)",
        "titulo": "Resultado fiscal",
        "texto": (
            "O resultado primário é a diferença entre receitas e despesas primárias (não "
            "financeiras) e mede o esforço fiscal do ente; o resultado nominal reflete a "
            "variação da dívida fiscal líquida. Ambos são demonstrados no Anexo 6 do RREO, "
            "confrontando as metas da LDO."
        ),
        "indicadores": ["resultado_primario"],
        "tags": ["resultado", "primário", "nominal", "anexo 6", "meta", "ldo"],
    },
]


def _source_ref(fonte: str, dispositivo: str) -> dict[str, str]:
    nome = {
        "LRF": "Lei de Responsabilidade Fiscal (LC 101/2000)",
        "CF": "Constituição Federal de 1988",
        "MDF": "Manual de Demonstrativos Fiscais (STN)",
    }[fonte]
    return {"relatorio": nome, "anexo": dispositivo}


def seed_norma_corpus(session: Session, embedder: Embedder, *, force: bool = False) -> int:
    """Indexa (ou reindexa) o corpo normativo com o *embedder* ativo. Idempotente.

    Recalcula os embeddings quando o modelo do *embedder* difere do já gravado (ou quando
    ``force``). Retorna o número de dispositivos gravados/atualizados.
    """
    if (
        not force
        and repository.count_norma_chunks(session) >= len(CORPUS)
        and repository.count_norma_com_modelo(session, embedder.model_id) >= len(CORPUS)
    ):
        return 0

    textos = [f"{item['titulo']}. {item['texto']}" for item in CORPUS]
    vetores = embedder.embed(textos)
    gravados = 0
    for item, vetor in zip(CORPUS, vetores, strict=True):
        repository.upsert_norma_chunk(
            session,
            fonte=item["fonte"],
            dispositivo=item["dispositivo"],
            titulo=item.get("titulo"),
            texto=item["texto"],
            tags=list(item.get("tags", [])),
            indicadores=list(item.get("indicadores", [])),
            modelo_embedding=embedder.model_id,
            dim=embedder.dim,
            embedding=vetor,
            source_ref=_source_ref(item["fonte"], item["dispositivo"]),
        )
        gravados += 1
    return gravados
