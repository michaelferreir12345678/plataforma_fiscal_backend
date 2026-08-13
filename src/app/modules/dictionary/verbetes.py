"""Verbetes dos indicadores — a definição como DADO, mantida em código (Sprint IA-2).

Por que em código e não em cadastro: pelo mesmo motivo do ``lineage_seed`` da Sprint 26.
Um cadastro manual vira documentação desatualizada com aparência de verdade; mantido aqui,
o verbete entra no mesmo *commit* que muda a regra, e a catraca de completude (``service``)
reprova a suíte quando um indicador novo chega ao mart sem definição.

**Nenhum percentual de limite mora aqui.** Os tetos e pisos por esfera já são dado em
``gold.dim_limite_legal`` (§2 do CLAUDE.md); repeti-los no verbete criaria uma segunda
régua para o mesmo limite, e a segunda régua é a que fica errada. O verbete diz *o que é*
e *sobre o que incide*; quanto é, pergunta-se à dimensão.

Cada campo tem uma razão de existir:

- ``formula`` — legível por gente, na ordem em que a conta é feita.
- ``denominador`` / ``denominador_fallback`` — o 100% do percentual, e o que a plataforma
  usa quando o ente não publica o canônico. É a lacuna que custou a migration ``0035``.
- ``sentido`` — ``teto`` (acima é ruim), ``piso`` (abaixo é ruim) ou ``gerencial`` (não há
  limite legal — e isso precisa ser dito, porque "sem faixa" lido como "dentro do limite"
  é falso conforto).
- ``sinonimos`` — o vocabulário do gestor ("gasto com pessoal", "folha") mapeado ao código.
- ``armadilha`` — o que erra quem lê a coluna sem contexto.
- ``fonte_definicao`` / ``atualizado_em`` — de onde saiu a definição e quando. Um
  dicionário que envelhece em silêncio é pior que não existir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

#: Data da curadoria desta revisão do dicionário (viaja em cada verbete).
REVISAO = date(2026, 8, 13)


@dataclass(frozen=True)
class Verbete:
    """Uma entrada do dicionário de indicadores."""

    codigo: str
    rotulo: str
    definicao: str
    formula: str
    denominador: str
    denominador_definicao: str
    unidade: str
    sentido: str
    base_legal: str
    tabela_origem: str
    coluna_valor: str
    fonte_definicao: str
    coluna_base: str | None = None
    denominador_fallback: str | None = None
    armadilha: str | None = None
    sinonimos: tuple[str, ...] = field(default_factory=tuple)
    atualizado_em: date = REVISAO


_MART = "gold.mart_indicador"

#: Definição da RCL Ajustada, repetida por referência nos quatro indicadores que a usam.
#: É o verbete mais importante do dicionário: foi exatamente esta distinção que a
#: plataforma errou até a Sprint 28, exibindo percentuais **menores** que os publicados.
_RCL_AJUSTADA = (
    "Receita Corrente Líquida Ajustada para cálculo dos limites: é a RCL do art. 2º, IV da "
    "LRF (12 meses móveis, consolidada) deduzidas as transferências obrigatórias da União "
    "recebidas por emenda parlamentar individual e de bancada, que por determinação "
    "constitucional não integram a base dos limites (CF art. 166-A, §2º, incluído pela EC "
    "105/2019, e art. 166, §16). O próprio demonstrativo publica essa linha; a plataforma a "
    "guarda ao lado da apuração (gold.fato_pessoal.rcl_ajustada e gold.fato_divida."
    "rcl_ajustada) e NÃO a recalcula. Dividir pela RCL cheia produz percentual menor que o "
    "publicado pelo ente — erro para menos, que num monitor de limites é o pior sentido "
    "possível, porque esconde o risco que o produto existe para mostrar."
)

_RCL_CHEIA = (
    "Receita Corrente Líquida: somatório das receitas correntes dos doze meses anteriores "
    "ao de referência, deduzidas as contribuições dos servidores ao RPPS, a compensação "
    "previdenciária e as transferências constitucionais entregues a outros entes. É "
    "materializada em gold.fato_rcl.rcl_12m e versionada por entrega — nunca recalculada "
    "em requisição."
)

_BASE_IMPOSTOS = (
    "Receita resultante de impostos próprios e das transferências constitucionais "
    "recebidas, apurada no próprio demonstrativo do mínimo. NÃO é a RCL: a base dos "
    "mínimos constitucionais exclui receitas que a RCL inclui, e rotular o percentual "
    "como '% da RCL' erra a leitura do cumprimento."
)

VERBETES: tuple[Verbete, ...] = (
    # ------------------------------------------------------------------ tetos (LRF)
    Verbete(
        codigo="pessoal_executivo",
        rotulo="Pessoal do Executivo",
        definicao=(
            "Despesa total com pessoal do Poder Executivo, líquida das exclusões legais "
            "(indenizações, incentivos à demissão voluntária, decisões judiciais de "
            "exercícios anteriores e inativos custeados pelo RPPS), medida contra o teto "
            "do art. 20 da LRF. É o indicador de conformidade mais consultado da "
            "plataforma."
        ),
        formula=(
            "despesa_total_com_pessoal_liquida ÷ rcl_ajustada × 100 — a despesa líquida é "
            "a DTP publicada no RGF Anexo 01 (quando o ente a publica, ela manda; as "
            "exclusões viram derivadas)."
        ),
        denominador="rcl_ajustada",
        denominador_fallback="rcl",
        denominador_definicao=_RCL_AJUSTADA,
        unidade="pct",
        sentido="teto",
        base_legal=(
            "LRF (LC 101/2000) art. 19, II e III (limite global de 60% da RCL para Estados "
            "e Municípios) e art. 20, III, 'b' (54% para o Executivo municipal) / art. 20, II, "
            "'c' (49% para o Executivo estadual); art. 22, parágrafo único (limite "
            "prudencial de 95% do teto); art. 59, §1º, II (alerta em 90%); art. 23 "
            "(recondução em dois quadrimestres); CF art. 169. A base do denominador segue "
            "a CF art. 166-A, §2º (EC 105/2019)."
        ),
        tabela_origem=_MART,
        coluna_valor="valor_pct_rcl",
        coluna_base="base_valor",
        sinonimos=(
            "gasto com pessoal",
            "despesa com pessoal",
            "despesa total com pessoal",
            "folha de pagamento",
            "folha",
            "dtp",
            "limite de pessoal",
            "pessoal do executivo",
            "prudencial de pessoal",
        ),
        armadilha=(
            "Apurado do RGF (quadrimestral, ou semestral em municípios com menos de 50 mil "
            "habitantes) mas gravado sob o período do RREO (bimestral) — juntar "
            "gold.fato_pessoal a gold.mart_indicador por 'periodo' não casa nada. O teto "
            "muda por esfera (54% município, 49% estado): comparar percentuais sem olhar "
            "gold.dim_ente.esfera compara réguas diferentes. E ente sem RCL Ajustada "
            "publicada cai para a RCL cheia — a coluna 'denominador' diz qual foi usada."
        ),
        fonte_definicao=(
            "LRF art. 19-23; app/modules/personnel/service.py; migration "
            "0035_sprint28_rcl_ajustada"
        ),
    ),
    Verbete(
        codigo="divida_consolidada_liquida",
        rotulo="Dívida consolidada líquida",
        definicao=(
            "Dívida consolidada bruta do ente deduzidas as disponibilidades de caixa e os "
            "demais haveres financeiros — o estoque de endividamento líquido medido contra "
            "o limite do Senado Federal."
        ),
        formula=(
            "(divida_consolidada_bruta − disponibilidades − haveres_financeiros) ÷ "
            "rcl_ajustada × 100"
        ),
        denominador="rcl_ajustada",
        denominador_fallback="rcl",
        denominador_definicao=_RCL_AJUSTADA,
        unidade="pct",
        sentido="teto",
        base_legal=(
            "LRF art. 30 e 31 (limites e recondução em três quadrimestres, com 25% no "
            "primeiro); Resolução do Senado Federal nº 40/2001, art. 3º, I (200% da RCL "
            "para Estados) e II (120% para Municípios)."
        ),
        tabela_origem=_MART,
        coluna_valor="valor_pct_rcl",
        coluna_base="base_valor",
        sinonimos=(
            "divida",
            "divida consolidada",
            "divida liquida",
            "dcl",
            "endividamento",
            "estoque da divida",
        ),
        armadilha=(
            "gold.fato_divida guarda três números parecidos e diferentes: 'dcl' é a "
            "apurada pela plataforma, 'dcl_reportada' é a que o ente publicou e "
            "'diferenca_reconciliacao' é o desvio entre as duas. O mart usa a apurada. "
            "Somar as três, ou trocar uma pela outra, produz número que não existe."
        ),
        fonte_definicao=(
            "LRF art. 30-31; Res. SF 40/2001; app/modules/debt/service.py; "
            "app/workers/materialize.py::_materializar_dcl_mart"
        ),
    ),
    Verbete(
        codigo="operacoes_credito",
        rotulo="Operações de crédito",
        definicao=(
            "Montante das operações de crédito internas e externas realizadas no exercício "
            "(exceto ARO), medido contra o limite anual do Senado Federal."
        ),
        formula="total_operacoes_credito_do_exercicio ÷ rcl_ajustada × 100",
        denominador="rcl_ajustada",
        denominador_definicao=_RCL_AJUSTADA,
        unidade="pct",
        sentido="teto",
        base_legal=(
            "Resolução do Senado Federal nº 43/2001, art. 7º, I (16% da RCL); LRF art. 32; "
            "CF art. 167, III (regra de ouro — a operação de crédito não pode exceder as "
            "despesas de capital)."
        ),
        tabela_origem=_MART,
        coluna_valor="valor_pct_rcl",
        coluna_base="base_valor",
        sinonimos=(
            "operacoes de credito",
            "operacao de credito",
            "contratacao de credito",
            "emprestimos contratados",
        ),
        armadilha=(
            "Não é o estoque da dívida (isso é divida_consolidada_liquida) e não inclui a "
            "ARO, que tem limite próprio de 7% e cadência diferente. Sem RCL Ajustada "
            "publicada o indicador NÃO é materializado — a ausência é deliberada: limite "
            "apurado sobre denominador inventado parece conformidade."
        ),
        fonte_definicao="Res. SF 43/2001 art. 7º; app/modules/indicators/endividamento.py",
    ),
    Verbete(
        codigo="garantias",
        rotulo="Garantias e contragarantias",
        definicao=(
            "Saldo das garantias concedidas pelo ente a operações de crédito de terceiros "
            "(autarquias, empresas estatais, consórcios), medido contra o limite do Senado."
        ),
        formula="saldo_garantias_concedidas ÷ rcl_ajustada × 100",
        denominador="rcl_ajustada",
        denominador_definicao=_RCL_AJUSTADA,
        unidade="pct",
        sentido="teto",
        base_legal=(
            "Resolução do Senado Federal nº 43/2001, art. 9º (22% da RCL); LRF art. 40 "
            "(exigência de contragarantia)."
        ),
        tabela_origem=_MART,
        coluna_valor="valor_pct_rcl",
        coluna_base="base_valor",
        sinonimos=(
            "garantias",
            "garantias concedidas",
            "contragarantias",
            "aval do municipio",
        ),
        armadilha=(
            "É apurado do RGF Anexo 03, não do RREO — o carimbo de fonte que a página de "
            "limites herdava do Anexo 03 do RREO erra a procedência deste indicador. A "
            "ferramenta lê a fonte gravada na linha do mart, que é a correta."
        ),
        fonte_definicao="Res. SF 43/2001 art. 9º; app/modules/indicators/endividamento.py",
    ),
    # ------------------------------------------------------- pisos (mínimos constitucionais)
    Verbete(
        codigo="saude_minimo",
        rotulo="Aplicação em saúde (ASPS)",
        definicao=(
            "Percentual da receita de impostos e transferências aplicado em ações e "
            "serviços públicos de saúde. É um PISO: abaixo do mínimo há descumprimento "
            "constitucional — a semântica é invertida em relação aos tetos da LRF."
        ),
        formula=(
            "(despesa_propria_em_ASPS − restos_a_pagar_nao_processados_sem_lastro) ÷ "
            "receita_de_impostos_e_transferencias × 100"
        ),
        denominador="impostos_transferencias",
        denominador_definicao=_BASE_IMPOSTOS,
        unidade="pct",
        sentido="piso",
        base_legal=(
            "CF art. 198, §2º, II (12% para Estados/DF) e III (15% para Municípios); LC "
            "141/2012, arts. 6º a 8º (o que é despesa em ASPS) e art. 24 (restos a pagar "
            "inscritos sem disponibilidade financeira não contam como aplicação)."
        ),
        tabela_origem=_MART,
        coluna_valor="valor_pct_rcl",
        coluna_base="base_valor",
        sinonimos=(
            "saude",
            "minimo da saude",
            "asps",
            "aplicacao em saude",
            "15% da saude",
            "acoes e servicos publicos de saude",
        ),
        armadilha=(
            "A base NÃO é a RCL — apesar do nome histórico da coluna 'valor_pct_rcl', o "
            "percentual é sobre impostos e transferências, e a coluna 'denominador' declara "
            "isso. O mínimo é de apuração ANUAL: percentual baixo nos primeiros bimestres é "
            "sazonalidade, não descumprimento — só há risco a partir do 5º bimestre. Fonte: "
            "RREO Anexo 12, que a API do SICONFI não publica (vem do PDF do portal do ente), "
            "por isso a cobertura é menor que a dos demais indicadores."
        ),
        fonte_definicao="CF art. 198; LC 141/2012; app/modules/health_edu/service.py",
    ),
    Verbete(
        codigo="educacao_mde",
        rotulo="Aplicação em educação (MDE)",
        definicao=(
            "Percentual da receita de impostos e transferências aplicado em manutenção e "
            "desenvolvimento do ensino. É um PISO: abaixo de 25% há descumprimento."
        ),
        formula=(
            "(despesa_propria_em_MDE − restos_a_pagar_nao_processados_sem_lastro) ÷ "
            "receita_de_impostos_e_transferencias × 100"
        ),
        denominador="impostos_transferencias",
        denominador_definicao=_BASE_IMPOSTOS,
        unidade="pct",
        sentido="piso",
        base_legal=(
            "CF art. 212 (mínimo de 25% para Estados, DF e Municípios); Lei 9.394/1996 "
            "(LDB), arts. 70 e 71 (o que é e o que não é despesa de MDE)."
        ),
        tabela_origem=_MART,
        coluna_valor="valor_pct_rcl",
        coluna_base="base_valor",
        sinonimos=(
            "educacao",
            "mde",
            "minimo da educacao",
            "manutencao e desenvolvimento do ensino",
            "25% da educacao",
            "aplicacao em ensino",
        ),
        armadilha=(
            "Não confunda com fundeb_profissionais: são bases e mínimos diferentes no mesmo "
            "demonstrativo (RREO Anexo 08). A base aqui é impostos e transferências; a do "
            "FUNDEB são as receitas do próprio Fundo."
        ),
        fonte_definicao="CF art. 212; LDB arts. 70-71; app/modules/health_edu/service.py",
    ),
    Verbete(
        codigo="fundeb_profissionais",
        rotulo="FUNDEB — profissionais da educação",
        definicao=(
            "Parcela dos recursos anuais do FUNDEB destinada ao pagamento da remuneração "
            "dos profissionais da educação básica em efetivo exercício. PISO de 70%."
        ),
        formula=(
            "despesa_com_remuneracao_de_profissionais_da_educacao ÷ "
            "receitas_anuais_do_FUNDEB × 100"
        ),
        denominador="fundeb",
        denominador_definicao=(
            "Receitas anuais totais recebidas do FUNDEB pelo ente (base própria do fundo, "
            "publicada no RREO Anexo 08). Não é a RCL nem a base de impostos do MDE: "
            "publicar este percentual sobre a base de impostos daria um número diferente do "
            "que a lei manda medir."
        ),
        unidade="pct",
        sentido="piso",
        base_legal=(
            "CF art. 212-A, XI (EC 108/2020) — mínimo de 70% dos recursos anuais dos Fundos "
            "em remuneração dos profissionais da educação básica; Lei 14.113/2020, art. 26."
        ),
        tabela_origem=_MART,
        coluna_valor="valor_pct_rcl",
        coluna_base="base_valor",
        sinonimos=(
            "fundeb",
            "70% do fundeb",
            "profissionais da educacao",
            "remuneracao dos professores",
            "fundeb profissionais",
        ),
        armadilha=(
            "O mínimo é anual e sobre os recursos recebidos no exercício; percentuais "
            "parciais no meio do ano não indicam descumprimento."
        ),
        fonte_definicao="CF art. 212-A, XI; Lei 14.113/2020; app/modules/health_edu/service.py",
    ),
    # ------------------------------------------------------------------ gerenciais (sem limite)
    Verbete(
        codigo="investimento_rcl",
        rotulo="Investimento sobre a RCL",
        definicao=(
            "Quanto o ente empenhou em investimentos (obras, equipamentos, instalações) em "
            "relação à sua receita corrente líquida. Mede capacidade de investir, não "
            "conformidade."
        ),
        formula="despesa_empenhada_na_natureza_Investimentos ÷ rcl_12m × 100",
        denominador="rcl",
        denominador_definicao=_RCL_CHEIA,
        unidade="pct",
        sentido="gerencial",
        base_legal=(
            "SEM LIMITE LEGAL — não há teto nem piso para investimento na LRF. A "
            "classificação da despesa segue a Lei 4.320/1964, art. 12, §4º (investimentos "
            "como despesa de capital), no eixo de natureza do RREO Anexo 02."
        ),
        tabela_origem=_MART,
        coluna_valor="valor_pct_rcl",
        coluna_base="base_valor",
        sinonimos=(
            "investimento",
            "investimentos",
            "obras",
            "capacidade de investimento",
            "investimento sobre a rcl",
        ),
        armadilha=(
            "Vem SEM faixa e SEM teto no mart (faixa e teto_pct nulos) porque não há limite "
            "legal — ler 'sem faixa' como 'dentro do limite' é falso conforto. O valor é "
            "empenhado (estágio da despesa), não pago: comparar com liquidado/pago de outra "
            "fonte compara estágios diferentes."
        ),
        fonte_definicao="Lei 4.320/1964 art. 12; app/modules/indicators/gerenciais.py",
    ),
    Verbete(
        codigo="rcl_per_capita",
        rotulo="RCL por habitante",
        definicao=(
            "Receita corrente líquida dividida pela população estimada do ente — a medida "
            "de porte fiscal que torna comparáveis municípios de tamanhos diferentes."
        ),
        formula="rcl_12m ÷ populacao_estimada",
        denominador="populacao",
        denominador_definicao=(
            "População estimada pelo IBGE para o exercício do ponto; sem estimativa daquele "
            "ano, cai para a população conformada em gold.dim_ente.populacao (o ano usado "
            "fica em pop_ano_ref). É contagem de pessoas, não valor financeiro."
        ),
        unidade="brl_per_capita",
        sentido="gerencial",
        base_legal=(
            "SEM LIMITE LEGAL — indicador gerencial. A RCL segue a LRF art. 2º, IV; a "
            "população é a estimativa oficial do IBGE, a mesma que define a cadência do RGF "
            "(LRF art. 63, II — municípios com menos de 50 mil habitantes podem publicar "
            "semestralmente)."
        ),
        tabela_origem=_MART,
        coluna_valor="valor_rs",
        coluna_base="base_valor",
        sinonimos=(
            "rcl per capita",
            "rcl por habitante",
            "receita por habitante",
            "arrecadacao por habitante",
        ),
        armadilha=(
            "É o único indicador do mart cujo valor está em 'valor_rs' e cujo "
            "'valor_pct_rcl' é NULL — ler a coluna de percentual aqui devolve vazio, não "
            "zero. E 'base_valor' guarda a POPULAÇÃO, não um valor em reais: somar "
            "base_valor entre indicadores mistura pessoas com dinheiro."
        ),
        fonte_definicao="LRF art. 2º, IV; IBGE (estimativas); app/modules/indicators/gerenciais.py",
    ),
    Verbete(
        codigo="resultado_primario_rcl",
        rotulo="Resultado primário sobre a RCL",
        definicao=(
            "Resultado primário (receitas primárias menos despesas primárias, isto é, "
            "excluídas as financeiras) em proporção da RCL. Mede o esforço fiscal do "
            "exercício: positivo é superávit, negativo é déficit."
        ),
        formula="resultado_primario ÷ rcl_12m × 100",
        denominador="rcl",
        denominador_definicao=_RCL_CHEIA,
        unidade="pct",
        sentido="gerencial",
        base_legal=(
            "SEM LIMITE LEGAL de faixa — a meta é fixada pelo próprio ente na LDO (LRF art. "
            "4º, §1º), e o descumprimento aciona a limitação de empenho do art. 9º. "
            "Demonstrado no RREO Anexo 06."
        ),
        tabela_origem=_MART,
        coluna_valor="valor_pct_rcl",
        coluna_base="base_valor",
        sinonimos=(
            "resultado primario",
            "superavit primario",
            "deficit primario",
            "esforco fiscal",
            "resultado primario sobre a rcl",
        ),
        armadilha=(
            "PODE SER NEGATIVO, e o sinal é a informação — ordenar ou agregar por módulo "
            "inverte a leitura (o pior vira o melhor). Não confunda com o resultado "
            "nominal, que mede a variação da dívida fiscal líquida e vive no mesmo Anexo 06 "
            "(gold.fato_resultado.resultado_nominal)."
        ),
        fonte_definicao="LRF art. 4º e 9º; RREO Anexo 06; app/modules/indicators/gerenciais.py",
    ),
    # ------------------------------------------------------------------ o denominador em si
    Verbete(
        codigo="rcl",
        rotulo="Receita Corrente Líquida",
        definicao=(
            "Receita corrente líquida dos 12 meses móveis, consolidada. É o denominador de "
            "quase todos os limites da LRF e a medida de capacidade financeira do ente."
        ),
        formula=(
            "Σ receitas_correntes_dos_ultimos_12_meses − deducoes (contribuições dos "
            "servidores ao RPPS, compensação previdenciária e transferências "
            "constitucionais entregues a outros entes)"
        ),
        denominador="",
        denominador_definicao=(
            "Não é uma razão: é um valor absoluto em reais. Quando aparece como denominador "
            "de outro indicador, a coluna 'denominador' do mart diz se foi a RCL cheia "
            "('rcl') ou a Ajustada dos limites ('rcl_ajustada')."
        ),
        unidade="brl",
        sentido="gerencial",
        base_legal=(
            "LRF (LC 101/2000) art. 2º, IV e §§ 1º a 3º; publicada no RREO Anexo 03."
        ),
        tabela_origem="gold.fato_rcl",
        coluna_valor="rcl_12m",
        sinonimos=(
            "rcl",
            "receita corrente liquida",
            "receita liquida",
            "denominador dos limites",
        ),
        armadilha=(
            "NÃO é a mesma coisa que a RCL Ajustada usada nos limites de pessoal, dívida, "
            "garantias e operações de crédito: a Ajustada deduz as transferências recebidas "
            "por emenda parlamentar. Trocar uma pela outra produz percentual menor que o "
            "publicado pelo ente (foi o defeito corrigido na Sprint 28)."
        ),
        fonte_definicao="LRF art. 2º, IV; app/modules/indicators/rcl.py",
    ),
)

#: Códigos que a plataforma materializa hoje em ``gold.mart_indicador`` — medidos no banco,
#: não estimados. Existe para que a catraca de completude continue significando alguma
#: coisa num banco vazio (CI recém-migrada), onde a consulta ao mart não devolveria nada.
CODIGOS_MATERIALIZADOS: frozenset[str] = frozenset(
    {
        "pessoal_executivo",
        "divida_consolidada_liquida",
        "operacoes_credito",
        "garantias",
        "saude_minimo",
        "educacao_mde",
        "fundeb_profissionais",
        "investimento_rcl",
        "rcl_per_capita",
        "resultado_primario_rcl",
    }
)


def por_codigo() -> dict[str, Verbete]:
    """Verbetes indexados pelo código canônico do indicador."""
    return {v.codigo: v for v in VERBETES}
