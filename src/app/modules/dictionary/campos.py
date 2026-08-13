"""Descrição de coluna das tabelas **consultáveis** (Sprint IA-2).

O §3 do plano de MCP nomeia o risco com o exemplo desta plataforma: ``gold.mart_indicador``
tem ``valor_rs``, ``valor_pct_rcl``, ``base_valor`` e ``denominador``, e escolher a coluna
plausível em vez da correta produz um número com sintaxe perfeita e semântica errada. Este
módulo é a resposta: cada coluna diz o que guarda e — mais importante — o que **não** é.

**O que "consultável" significa.** A tabela pode aparecer numa consulta analítica governada
(a consulta guiada da IA-1b hoje; o SQL da IA-4 amanhã). O recorte segue o §4.1 do plano:

- ``gold`` — sim, é a fonte de resposta (dado calculado, versionado, com ``source_ref``);
- ``silver``/``bronze`` — fora daqui: normalizado mas não conciliado, ou payload cru;
- ``op`` — **nunca**, é dado da organização. A restrição é do banco (``CHECK`` da migration
  ``0045``), não desta lista: uma seed distraída não consegue declarar ``op`` consultável.

Fora do recorte por decisão, não por esquecimento: ``mart_benchmark`` e ``dim_coorte`` (a
comparação com a coorte tem ferramenta própria e a coorte é versionada — merece verbete
próprio antes de entrar em consulta livre), ``fato_msc_saldo`` e suas 60 partições, e os
fatos de receita/despesa, cujo drill hierárquico já é servido por ferramenta.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.modules.dictionary.verbetes import REVISAO


@dataclass(frozen=True)
class Campo:
    """Descrição de uma coluna de tabela consultável."""

    schema_nome: str
    tabela: str
    coluna: str
    descricao: str
    unidade: str | None = None
    chave: bool = False
    consultavel: bool = True
    armadilha: str | None = None
    fonte_definicao: str = "curadoria Sprint IA-2 sobre os modelos SQLAlchemy da gold"
    atualizado_em: date = REVISAO


#: ``(coluna, descrição, unidade, chave, consultável, armadilha)`` por tabela.
_Linha = tuple[str, str, str | None, bool, bool, str | None]

_ID = (
    "id",
    "Chave técnica (UUID) da linha. Não tem significado fiscal e não é estável entre "
    "rematerializações.",
    None,
    True,
    False,
    "Não use como chave de negócio nem para ordenar: a chave real é a UNIQUE da tabela.",
)

_TABELAS: dict[str, tuple[_Linha, ...]] = {
    # ------------------------------------------------------------------ mart_indicador
    "gold.mart_indicador": (
        _ID,
        (
            "cod_ibge",
            "Código IBGE de 7 dígitos do ente (2 dígitos quando é o governo estadual).",
            None,
            True,
            True,
            None,
        ),
        (
            "periodo",
            "Período fiscal canônico do indicador, sempre o BIMESTRE do RREO (ex.: "
            "'2024-B6'), mesmo quando o insumo vem do RGF quadrimestral.",
            None,
            True,
            True,
            "Não casa com gold.fato_pessoal.periodo nem com gold.fato_divida.periodo, que "
            "guardam o período do RGF ('2024-Q3'). Juntar por 'periodo' entre mart e esses "
            "fatos devolve zero linha — silenciosamente.",
        ),
        (
            "indicador",
            "Código canônico do indicador; é a chave para gold.dicionario_indicador.codigo.",
            None,
            True,
            True,
            None,
        ),
        (
            "valor_rs",
            "Numerador do indicador em reais (a despesa, o estoque, o resultado). Em "
            "rcl_per_capita é o próprio valor final, em R$/habitante.",
            "BRL",
            False,
            True,
            "É o NUMERADOR, não o valor do limite nem a base. Somar valor_rs entre "
            "indicadores diferentes soma grandezas distintas.",
        ),
        (
            "valor_pct_rcl",
            "Percentual do indicador sobre a base declarada em 'denominador'. O nome tem "
            "'rcl' por herança da Sprint 2, quando todos os indicadores eram sobre a RCL.",
            "pct",
            False,
            True,
            "NÃO é sempre percentual da RCL: nos mínimos constitucionais é sobre impostos e "
            "transferências (ou sobre o FUNDEB), e nos limites da LRF é sobre a RCL "
            "AJUSTADA. Leia 'denominador' antes de rotular. É NULL em rcl_per_capita.",
        ),
        (
            "faixa",
            "Classificação em relação ao limite legal: normal/alerta/prudencial/excedido "
            "nos tetos, adequado/insuficiente nos pisos.",
            None,
            False,
            True,
            "NULL nos indicadores gerenciais (não há limite legal). NULL não significa "
            "'dentro do limite' — significa 'não há limite a classificar'.",
        ),
        (
            "teto_pct",
            "Teto (ou piso) legal aplicado ao ente naquele período, copiado de "
            "gold.dim_limite_legal conforme a esfera.",
            "pct",
            False,
            True,
            "NULL nos gerenciais. Nunca compare o percentual de dois entes de esferas "
            "diferentes sem comparar também este teto: 54% (município) e 49% (estado) são "
            "réguas distintas.",
        ),
        (
            "source_ref",
            "Procedência da linha (relatório, anexo, período, versão da entrega) em JSONB. "
            "É a fonte que a resposta exibe — manda sobre qualquer carimbo derivado.",
            None,
            False,
            True,
            "Pode conter 'source_refs_componentes' quando o indicador cruza duas entregas "
            "(pessoal: RGF + RREO). Nesse caso 'versao_entrega' é uma chave composta.",
        ),
        (
            "versao_entrega",
            "Versão da entrega que fundamenta a linha. Com a retificação, a versão nova "
            "convive com a anterior — a superada não é apagada.",
            None,
            True,
            True,
            "Filtrar por período sem resolver a versão VIGENTE (via gold.dim_entrega) "
            "conta a mesma apuração duas vezes e pode devolver o número já retificado.",
        ),
        (
            "denominador",
            "Nome do 100% do percentual: 'rcl_ajustada', 'rcl', 'impostos_transferencias', "
            "'fundeb' ou 'populacao'. É o campo que impede rotular ASPS como '% da RCL'.",
            None,
            False,
            True,
            "Um mesmo indicador pode ter denominadores diferentes entre entes: sem RCL "
            "Ajustada publicada, pessoal e DCL caem para a RCL cheia. Agregue por "
            "denominador ou declare a mistura.",
        ),
        (
            "base_valor",
            "Valor absoluto do denominador usado no cálculo — permite refazer a conta sem "
            "voltar ao fato de origem.",
            "BRL",
            False,
            True,
            "Em rcl_per_capita guarda a POPULAÇÃO (contagem de pessoas), não reais. Somar "
            "base_valor entre indicadores mistura dinheiro com gente.",
        ),
    ),
    # ------------------------------------------------------------ dicionario_indicador
    # O dicionário é consultável de propósito: a consulta que devolve um indicador pode
    # trazer, na mesma linha, a fórmula e o denominador corretos dele — em vez de deixar
    # quem lê (pessoa ou modelo) inferir o significado da coluna pelo nome.
    "gold.dicionario_indicador": (
        (
            "codigo",
            "Código canônico do indicador — junta com gold.mart_indicador.indicador.",
            None,
            True,
            True,
            None,
        ),
        ("rotulo", "Nome de exibição do indicador (fonte única de rótulo).", None, False, True,
         None),
        ("definicao", "O que o indicador mede, em linguagem de gestor.", None, False, True, None),
        ("formula", "Fórmula legível, na ordem em que a conta é feita.", None, False, True, None),
        (
            "denominador",
            "Nome do denominador canônico do indicador (o mesmo vocabulário de "
            "gold.mart_indicador.denominador).",
            None,
            False,
            True,
            "String vazia quando o indicador não é uma razão (ex.: 'rcl').",
        ),
        (
            "denominador_fallback",
            "Denominador usado quando o ente não publica o canônico.",
            None,
            False,
            True,
            "NULL não significa 'não há alternativa' e sim 'não há queda prevista': aí a "
            "ausência do insumo impede a materialização.",
        ),
        (
            "denominador_definicao",
            "O que é esse denominador e por que é ele — inclui a distinção RCL × RCL "
            "Ajustada.",
            None,
            False,
            True,
            None,
        ),
        ("unidade", "Unidade do valor: 'pct', 'brl' ou 'brl_per_capita'.", None, False, True,
         None),
        (
            "sentido",
            "'teto', 'piso' ou 'gerencial' (sem limite legal).",
            None,
            False,
            True,
            "Coerente com gold.dim_limite_legal.sentido quando há limite; 'gerencial' é "
            "justamente o caso em que não existe linha lá.",
        ),
        ("base_legal", "Dispositivos que fundamentam o indicador e o seu limite.", None, False,
         True, None),
        (
            "tabela_origem",
            "Onde o indicador é materializado (quase sempre gold.mart_indicador).",
            None,
            False,
            True,
            None,
        ),
        ("coluna_valor", "Coluna que guarda o valor principal do indicador.", None, False, True,
         None),
        ("coluna_base", "Coluna que guarda o valor do denominador.", None, False, True, None),
        (
            "sinonimos",
            "Vocabulário de negócio que resolve para este código ('gasto com pessoal').",
            None,
            False,
            True,
            None,
        ),
        ("armadilha", "O que erra quem lê o indicador sem contexto.", None, False, True, None),
        (
            "fonte_definicao",
            "De onde saiu a definição (dispositivo legal e/ou módulo que a implementa).",
            None,
            False,
            True,
            None,
        ),
        (
            "atualizado_em",
            "Data da última curadoria do verbete — um dicionário que envelhece em silêncio "
            "é pior que não existir.",
            None,
            False,
            True,
            None,
        ),
    ),
    # ------------------------------------------------------------------ dim_ente
    "gold.dim_ente": (
        (
            "cod_ibge",
            "Código IBGE do ente — chave primária e a chave de junção com todos os fatos.",
            None,
            True,
            True,
            None,
        ),
        ("nome", "Nome do ente conformado (SICONFI + IBGE).", None, False, True, None),
        (
            "esfera",
            "'municipal', 'estadual' ou 'federal'. Determina o teto de cada limite legal.",
            None,
            False,
            True,
            "Regra invariante do domínio: nenhum limite pode ser aplicado sem checar a "
            "esfera. 'federal' existe para dizer 'conhecida e inaplicável', não 'ausente'.",
        ),
        (
            "populacao",
            "População conformada do ente (estimativa IBGE do ano em pop_ano_ref).",
            "habitantes",
            False,
            True,
            "Define também a cadência do RGF: municípios com menos de 50 mil habitantes "
            "podem publicar semestralmente (LRF art. 63).",
        ),
        (
            "rpps",
            "Se o ente tem Regime Próprio de Previdência — muda as exclusões da despesa "
            "com pessoal.",
            None,
            False,
            True,
            None,
        ),
        (
            "possui_tcm",
            "Se o município é jurisdicionado a Tribunal de Contas dos Municípios próprio "
            "(muda a repartição do limite do Legislativo).",
            None,
            False,
            True,
            None,
        ),
        ("uf", "Sigla da unidade federativa (2 letras).", None, False, True, None),
        (
            "regiao",
            "Região geográfica conformada (NO, NE, CO, SE, SU).",
            None,
            False,
            True,
            None,
        ),
        (
            "pib",
            "PIB municipal do ano em pib_ano_ref (IBGE).",
            "BRL",
            False,
            True,
            "Ano de referência costuma ser mais antigo que o do dado fiscal — não compare "
            "PIB e receita do mesmo exercício sem olhar pib_ano_ref.",
        ),
        (
            "pop_ano_ref",
            "Exercício da estimativa populacional usada.",
            "ano",
            False,
            True,
            None,
        ),
        ("pib_ano_ref", "Exercício do PIB informado.", "ano", False, True, None),
        (
            "pop_source_ref",
            "Procedência da população (JSONB).",
            None,
            False,
            False,
            None,
        ),
        ("pib_source_ref", "Procedência do PIB (JSONB).", None, False, False, None),
        (
            "brasao_url",
            "URL do brasão do ente, usada no cabeçalho institucional dos relatórios.",
            None,
            False,
            False,
            "Atributo de apresentação, sem valor analítico.",
        ),
    ),
    # ------------------------------------------------------------------ dim_periodo
    "gold.dim_periodo": (
        (
            "codigo",
            "Código canônico do período: '2024' (ano), '2024-B6' (bimestre), '2024-Q3' "
            "(quadrimestre), '2024-S2' (semestre), '2024-M12' (mês).",
            None,
            True,
            True,
            None,
        ),
        ("descricao", "Rótulo legível do período.", None, False, True, None),
        (
            "parent_codigo",
            "Período pai na hierarquia (drill UP): o mês aponta para o bimestre, o "
            "bimestre para o exercício.",
            None,
            False,
            True,
            None,
        ),
        ("nivel", "Nível na hierarquia temporal (1 = exercício).", None, False, True, None),
        (
            "path",
            "Caminho materializado (ltree) do período — sustenta o drill hierárquico.",
            None,
            False,
            True,
            "Tipo ltree: comparar como texto puro não usa o índice nem os operadores de "
            "ancestralidade.",
        ),
        ("ano", "Exercício do período.", "ano", False, True, None),
        ("mes", "Mês (1-12) quando o período é mensal; NULL nos demais.", None, False, True, None),
        (
            "bimestre",
            "Bimestre (1-6) do período; NULL quando não se aplica.",
            None,
            False,
            True,
            None,
        ),
        (
            "quadrimestre",
            "Quadrimestre (1-3) do período; NULL nos bimestres, meses e semestres.",
            None,
            False,
            True,
            "Períodos semestrais ('2024-S1') existem para o RGF dos municípios pequenos e "
            "têm quadrimestre NULL — filtrar por quadrimestre os exclui.",
        ),
    ),
    # ------------------------------------------------------------------ dim_limite_legal
    "gold.dim_limite_legal": (
        _ID,
        (
            "indicador",
            "Código do indicador ao qual o limite se aplica.",
            None,
            True,
            True,
            None,
        ),
        (
            "esfera",
            "Esfera à qual este limite se aplica ('municipal' ou 'estadual').",
            None,
            True,
            True,
            "Junção sem esfera casa as duas linhas do mesmo indicador e duplica o "
            "resultado, com dois tetos diferentes.",
        ),
        (
            "poder",
            "Poder/órgão do limite ('Executivo' no limite de pessoal; string vazia quando "
            "o limite é do ente inteiro).",
            None,
            True,
            True,
            "É string vazia, não NULL — comparar com IS NULL não casa nada.",
        ),
        (
            "sentido",
            "'teto' (acima é descumprimento) ou 'piso' (abaixo é descumprimento).",
            None,
            False,
            True,
            "A semântica é invertida entre os dois: a mesma comparação '>' significa "
            "conformidade num e violação no outro.",
        ),
        (
            "teto_pct",
            "Percentual do limite legal — teto nos limites da LRF, piso nos mínimos "
            "constitucionais (o nome da coluna é histórico).",
            "pct",
            False,
            True,
            "Nos pisos este campo é o MÍNIMO exigido, apesar do nome.",
        ),
        (
            "alerta_pct",
            "90% do teto (LRF art. 59, §1º, II). NULL nos pisos.",
            "pct",
            False,
            True,
            None,
        ),
        (
            "prudencial_pct",
            "95% do teto (LRF art. 22, parágrafo único). NULL nos pisos.",
            "pct",
            False,
            True,
            None,
        ),
    ),
    # ------------------------------------------------------------------ dim_entrega
    "gold.dim_entrega": (
        _ID,
        ("cod_ibge", "Ente que entregou o relatório.", None, True, True, None),
        (
            "relatorio",
            "Relatório da entrega: 'RREO', 'RGF', 'DCA', 'MSC', 'CAPAG'…",
            None,
            True,
            True,
            None,
        ),
        (
            "periodo",
            "Período fiscal da entrega, na cadência do próprio relatório (bimestre no "
            "RREO, quadrimestre/semestre no RGF, exercício na DCA).",
            None,
            True,
            True,
            None,
        ),
        (
            "versao_entrega",
            "Identificador da entrega/retificação. É a chave que resolve a bitemporalidade.",
            None,
            True,
            True,
            None,
        ),
        (
            "homologada_em",
            "Instante em que a entrega passou a valer — é o que o parâmetro 'as_of' compara "
            "para reproduzir um relatório como ele era.",
            None,
            False,
            True,
            None,
        ),
        (
            "vigente",
            "Se esta é a versão vigente do par (ente, período, relatório). A retificação "
            "supera a anterior; a anterior não é apagada.",
            None,
            False,
            True,
            "Filtrar 'vigente = true' no FIM da consulta não é equivalente a resolver a "
            "vigência no JOIN: fatos de versões superadas entram no agregado antes do "
            "filtro. Parta da entrega vigente e junte o fato pela chave composta.",
        ),
        (
            "hash_payload",
            "Hash do payload da fonte — é o que torna a ingestão idempotente.",
            None,
            False,
            False,
            None,
        ),
    ),
    # ------------------------------------------------------------------ fato_rcl
    "gold.fato_rcl": (
        _ID,
        ("cod_ibge", "Ente da apuração.", None, True, True, None),
        (
            "periodo_ref",
            "Bimestre de referência da RCL de 12 meses móveis.",
            None,
            True,
            True,
            "A coluna chama-se 'periodo_ref' aqui e 'periodo' nas demais tabelas — a "
            "junção por 'periodo' falha com erro de coluna inexistente.",
        ),
        (
            "rcl_12m",
            "Receita Corrente Líquida dos 12 meses móveis, consolidada (RREO Anexo 03).",
            "BRL",
            False,
            True,
            "É a RCL CHEIA. Os limites de pessoal, dívida, garantias e operações de crédito "
            "incidem sobre a RCL AJUSTADA, que vive em gold.fato_pessoal.rcl_ajustada e "
            "gold.fato_divida.rcl_ajustada — usar esta coluna no lugar daquela reproduz o "
            "defeito corrigido na Sprint 28.",
        ),
        (
            "deducoes",
            "Deduções da receita corrente (contribuições ao RPPS, compensação "
            "previdenciária, transferências constitucionais a outros entes).",
            "BRL",
            False,
            True,
            None,
        ),
        (
            "receita_corrente",
            "Receita corrente bruta dos 12 meses, antes das deduções.",
            "BRL",
            False,
            True,
            "receita_corrente − deducoes = rcl_12m. Usar a bruta como denominador infla o "
            "denominador e subestima todo limite.",
        ),
        ("versao_entrega", "Entrega do RREO que fundamenta a apuração.", None, True, True, None),
        (
            "memoria",
            "Memória de cálculo em JSONB (fórmula aplicada e componentes).",
            None,
            False,
            False,
            "Texto explicativo: os números de dentro não têm source_ref próprio e não devem "
            "ser citados como valores apurados.",
        ),
    ),
    # ------------------------------------------------------------------ fato_pessoal
    "gold.fato_pessoal": (
        _ID,
        ("cod_ibge", "Ente da apuração.", None, True, True, None),
        (
            "periodo",
            "Período do RGF (quadrimestre '2024-Q3' ou semestre '2024-S2').",
            None,
            True,
            True,
            "NÃO é o período do mart (bimestre do RREO). A tradução RGF→RREO é Qn→B(2n) e "
            "Sn→B(3n).",
        ),
        (
            "poder_codigo",
            "Poder/órgão da linha (Executivo, Legislativo, consolidado…).",
            None,
            True,
            True,
            "A tabela tem uma linha por poder: somar sem filtrar o poder soma o "
            "consolidado com as partes e dobra a despesa.",
        ),
        (
            "despesa_bruta",
            "Despesa com pessoal antes das exclusões legais.",
            "BRL",
            False,
            True,
            None,
        ),
        (
            "exclusoes",
            "Exclusões do art. 19, §1º da LRF (indenizações, IDV, decisões judiciais de "
            "exercícios anteriores, inativos custeados pelo RPPS).",
            "BRL",
            False,
            True,
            "Quando o ente publica a DTP, ela manda: as exclusões viram derivadas "
            "(bruta − DTP), e não a soma das linhas de exclusão.",
        ),
        (
            "despesa_liquida",
            "Despesa Total com Pessoal (DTP) — o numerador do limite do art. 20.",
            "BRL",
            False,
            True,
            None,
        ),
        (
            "pct_rcl",
            "Percentual da despesa líquida sobre a RCL Ajustada (o limite do art. 20).",
            "pct",
            False,
            True,
            "Apesar do nome, o denominador é a rcl_ajustada desta mesma linha quando ela "
            "existe — não a rcl_12m de gold.fato_rcl.",
        ),
        ("versao_entrega", "Entrega do RGF que fundamenta a apuração.", None, True, True, None),
        (
            "rcl_ajustada",
            "RCL Ajustada para cálculo dos limites da despesa com pessoal, publicada pelo "
            "ente no RGF Anexo 01. É o denominador correto do art. 20.",
            "BRL",
            False,
            True,
            "NULL quando o ente não publicou a linha — nesse caso a apuração cai para a RCL "
            "cheia e o mart registra denominador='rcl'. Nunca preencha o NULL com a RCL "
            "cheia por conta própria.",
        ),
    ),
    # ------------------------------------------------------------------ fato_divida
    "gold.fato_divida": (
        _ID,
        ("cod_ibge", "Ente da apuração.", None, True, True, None),
        ("periodo", "Período do RGF (quadrimestre/semestre).", None, True, True, None),
        (
            "dc_bruta",
            "Dívida consolidada bruta (RGF Anexo 02).",
            "BRL",
            False,
            True,
            None,
        ),
        (
            "disponibilidades",
            "Disponibilidades de caixa deduzidas da dívida bruta.",
            "BRL",
            False,
            True,
            None,
        ),
        (
            "haveres",
            "Demais haveres financeiros deduzidos da dívida bruta.",
            "BRL",
            False,
            True,
            None,
        ),
        (
            "dcl",
            "Dívida Consolidada Líquida apurada pela plataforma "
            "(dc_bruta − disponibilidades − haveres).",
            "BRL",
            False,
            True,
            "É esta — e não a reportada — que alimenta o mart e o semáforo.",
        ),
        (
            "dcl_reportada",
            "DCL como o próprio ente a publicou no demonstrativo.",
            "BRL",
            False,
            True,
            "Existe para conciliação. Trocá-la pela apurada muda o número exibido sem que "
            "nada estoure.",
        ),
        (
            "diferenca_reconciliacao",
            "Diferença entre a DCL apurada e a reportada (dcl − dcl_reportada).",
            "BRL",
            False,
            True,
            "É um DESVIO, não um componente da dívida: somá-lo à DCL conta a diferença "
            "duas vezes.",
        ),
        (
            "rcl_ajustada",
            "RCL Ajustada publicada no RGF Anexo 02 — denominador do limite da Resolução "
            "40/2001.",
            "BRL",
            False,
            True,
            "NULL quando não publicada; nesse caso a apuração cai para a RCL cheia.",
        ),
        (
            "pct_rcl",
            "Percentual da DCL sobre a RCL Ajustada.",
            "pct",
            False,
            True,
            None,
        ),
        (
            "saldo_interno",
            "Parcela interna da dívida consolidada.",
            "BRL",
            False,
            True,
            None,
        ),
        (
            "saldo_externo",
            "Parcela externa da dívida consolidada.",
            "BRL",
            False,
            True,
            "saldo_interno + saldo_externo referem-se à dívida BRUTA; não somam para a DCL.",
        ),
        ("versao_entrega", "Entrega do RGF que fundamenta a apuração.", None, True, True, None),
    ),
    # ------------------------------------------------------------------ mart_carteira
    "gold.mart_carteira": (
        _ID,
        ("cod_ibge", "Ente monitorado.", None, True, True, None),
        ("periodo", "Bimestre do RREO ao qual o snapshot se refere.", None, True, True, None),
        ("indicador", "Código do indicador resumido.", None, True, True, None),
        ("faixa", "Faixa do indicador no período (copiada do mart).", None, False, True, None),
        (
            "valor_pct",
            "Percentual do indicador sobre a sua base declarada.",
            "pct",
            False,
            True,
            "Snapshot de conformidade: para o número com procedência completa, a fonte é "
            "gold.mart_indicador.",
        ),
        (
            "conformidade_status",
            "Resumo de conformidade do ente naquele indicador (o que o semáforo da carteira "
            "mostra).",
            None,
            False,
            True,
            None,
        ),
        ("versao_entrega", "Entrega que fundamentou o snapshot.", None, True, True, None),
        (
            "atualizado_em",
            "Quando o snapshot foi recalculado — é metadado de processo, não data fiscal.",
            None,
            False,
            True,
            "Ordenar a série por esta coluna ordena por data de processamento, não por "
            "período fiscal.",
        ),
    ),
    # ------------------------------------------------------------------ mart_consolidado_uf
    "gold.mart_consolidado_uf": (
        ("uf", "Unidade federativa consolidada.", None, True, True, None),
        ("periodo", "Bimestre do RREO consolidado.", None, True, True, None),
        ("indicador", "Código do indicador consolidado.", None, True, True, None),
        (
            "numerador",
            "Soma dos numeradores dos municípios que têm o indicador no período.",
            "BRL",
            False,
            True,
            None,
        ),
        (
            "denominador",
            "Soma dos denominadores dos mesmos municípios. Aqui 'denominador' é um VALOR em "
            "reais — diferente de gold.mart_indicador.denominador, que é o NOME da base.",
            "BRL",
            False,
            True,
            "Duas colunas com o mesmo nome e naturezas distintas em tabelas vizinhas: "
            "conferir sempre de qual tabela se está falando.",
        ),
        (
            "valor_pct",
            "Σnumerador ÷ Σdenominador × 100 — a razão dos agregados.",
            "pct",
            False,
            True,
            "NUNCA é a média dos percentuais municipais: a média trataria um município de "
            "mil habitantes como igual à capital. E o consolidado da UF NÃO é o governo do "
            "estado — são conjuntos diferentes (Sprint 26).",
        ),
        (
            "n_entes_total",
            "Municípios do território no período.",
            "entes",
            False,
            True,
            None,
        ),
        (
            "n_entes_com_dado",
            "Municípios que efetivamente têm o indicador materializado.",
            "entes",
            False,
            True,
            None,
        ),
        (
            "cobertura_pct",
            "n_entes_com_dado ÷ n_entes_total × 100 — a confiança do agregado.",
            "pct",
            False,
            True,
            "Consolidado com cobertura baixa não é comparável entre períodos: a variação "
            "pode ser de quem entregou, não do fisco.",
        ),
        (
            "entes_ausentes",
            "Códigos IBGE que faltam no agregado — a lacuna é dado, não silêncio.",
            None,
            False,
            True,
            None,
        ),
        (
            "periodos_mistos",
            "Marca que os municípios contribuíram com períodos diferentes (cadências "
            "distintas do RGF).",
            None,
            False,
            True,
            "Quando verdadeiro, o consolidado não é uma fotografia de uma data só.",
        ),
        (
            "versao_calculo",
            "Versão do algoritmo de consolidação que produziu a linha.",
            None,
            True,
            True,
            None,
        ),
        (
            "atualizado_em",
            "Quando a consolidação rodou (metadado de processo).",
            None,
            False,
            True,
            None,
        ),
    ),
    # ------------------------------------------------------------------ mart_cobertura_fonte
    "gold.mart_cobertura_fonte": (
        ("fonte", "Fonte externa (chave para gold.catalogo_fonte).", None, True, True, None),
        ("cod_ibge", "Ente coberto.", None, True, True, None),
        ("uf", "UF do ente (desnormalizada para filtro).", None, False, True, None),
        ("ano", "Exercício do período coberto.", "ano", False, True, None),
        ("periodo", "Período coberto, na cadência da fonte.", None, True, True, None),
        (
            "n_registros",
            "Quantas linhas a silver guarda para o recorte.",
            "linhas",
            False,
            True,
            "Contagem técnica de carga, não medida fiscal.",
        ),
        (
            "versao_entrega_vigente",
            "Versão vigente do recorte no momento da materialização.",
            None,
            False,
            True,
            None,
        ),
        ("ingerido_em", "Quando o recorte foi ingerido.", None, False, True, None),
        (
            "defasagem_periodos",
            "Quantos períodos de atraso o recorte tem em relação à cadência da fonte.",
            "periodos",
            False,
            True,
            "Mede atraso de publicação/carga; não se ancora em nenhuma entrega fiscal.",
        ),
        (
            "atualizado_em",
            "Quando a cobertura foi recalculada (metadado de processo).",
            None,
            False,
            True,
            None,
        ),
        # A ausência de linha é informação: município sem entrega no SICONFI simplesmente
        # não aparece aqui — a lacuna é da fonte, não um zero fabricado.
    ),
}

#: Tabelas liberadas para consulta analítica governada, na forma ``schema.tabela``.
TABELAS_CONSULTAVEIS: tuple[str, ...] = tuple(sorted(_TABELAS))


def campos() -> list[Campo]:
    """Todos os campos descritos, prontos para o seed idempotente."""
    saida: list[Campo] = []
    for qualificada, linhas in _TABELAS.items():
        schema_nome, tabela = qualificada.split(".", 1)
        for coluna, descricao, unidade, chave, consultavel, armadilha in linhas:
            saida.append(
                Campo(
                    schema_nome=schema_nome,
                    tabela=tabela,
                    coluna=coluna,
                    descricao=descricao,
                    unidade=unidade,
                    chave=chave,
                    consultavel=consultavel,
                    armadilha=armadilha,
                )
            )
    return saida
