# Evolução da Plataforma Prumo — auditoria técnica, funcional e conceitual

> Documento vivo. Fonte central de acompanhamento da evolução da plataforma.
> **Vive em `backend_plataforma_fiscal/docs/`** e é versionado com o código. Ficou
> fora do git de 2026-08-03 a 2026-08-04, na raiz do projeto, onde nenhum dos dois
> repositórios o rastreava.
> **Iniciado em:** 2026-08-03 · **Última atualização:** 2026-08-05
> **Estado:** auditoria em andamento. Sprints concluídas: **B0, A1, A2, A3, A3a, A3b, A5,
> A6, B1, B2, C1, C2, F1, F2, G1** e, agora, **A0R**; **A4 parcial** (mínimos bloqueados na
> fonte). As três frentes de diagnóstico interrompidas (§20, P2–P4) foram **reexecutadas e
> têm relatório em §5.1.2**, com seis achados novos (**A22–A27**) e a **P5/DTP
> rediagnosticada** — todos por leitura de código, sem banco nesta rodada: leia a ressalva
> de método no início da §5.1.2 antes de agir sobre qualquer um deles.
> **Segunda rodada (2026-08-04, §12):** 8 frentes paralelas de leitura de código cobriram as
> 23 páginas de novo, com foco em profundidade/drill-down, rastreabilidade (`as_of`) e
> legendas — achou 6 achados críticos novos (**A16–A21**, um deles regressão sobre a U1) e
> 16 de clareza (**U19–U34**), e produziu as **8 fichas de sprint** (A5 estendida, A6, F1,
> F2, G1, D1, H1, B3) que o plano de §9 previa e ainda não detalhava. **Não verificados
> contra o banco** — ver a ressalva de método no início do §12 antes de agir sobre eles.

---

## 1. Resumo executivo

A Prumo transforma dados abertos do SICONFI em painéis, indicadores, alertas e previsões
para o gestor público técnico. Hoje tem **23 páginas**, **26 rotas**, **22 módulos de
backend** e **~140 endpoints**, com dado real do SICONFI para 180 entes.

A base técnica é sólida em pontos que costumam ser frágeis: medallion bitemporal com
retificação que supera sem apagar, `source_ref` em quase toda resposta, procedência de
fonte com endpoint clicável, drill até a linha bruta do relatório com reconciliação, e
uma suíte que já pegou defeitos reais antes de produção.

**O problema central desta auditoria não é a qualidade do que existe — é a distância
entre o que a interface promete e o que o dado sustenta.** A plataforma se apresenta como
um produto de cobertura nacional; a cobertura efetiva de vários indicadores é de **um
único ente**. Uma tela que funciona para 1 de 180 entes, sem dizer isso, não é uma tela
incompleta: é uma tela que engana.

O segundo eixo é **clareza conceitual**. O vocabulário fiscal (RCL, RPPS, DTP, RPNP, DCL,
CAPAG, MDE, ASPS, primário × nominal) aparece em rótulos sem que a tela explique o que
significa, o que entra no cálculo e qual demonstrativo o fundamenta. O gestor técnico
conhece os termos; ele **não** tem como saber qual metodologia a plataforma escolheu.

### Onde a auditoria chegou até aqui

Os dois eixos foram atacados e estão fechados no que dependia de nós:

* **Cobertura (A1)** — cada página declara para quantos entes do escopo de quem pergunta
  ela responde, medindo o **produto** e não o insumo. Saúde & Educação diz "1 de 185".
* **Reconciliação (A2)** — a RCL calculada é confrontada com a publicada pelo ente: 1.982
  pares no CE, 94,2% batem à centavo.
* **Invariantes (A3)** — 7 regras do domínio verificadas contra o banco, todas limpas.
* **Ingestão (A4)** — o achado mais grave da auditoria: a API de transferências pagina de
  10 em 10 e o conector lia uma página. **11 de 185 municípios — 5% do dado com aparência
  de 100%.** Corrigido em desenvolvimento e em produção. Os mínimos (Anexos 8/12) ficaram
  **bloqueados na fonte**: o SICONFI não os publica.
* **Clareza (B1)** — dez rótulos que faziam o gestor concluir o oposto do dado.
* **Limites (B2)** — garantias e operações de crédito passam a existir como número, sobre
  a RCL Ajustada. A primeira apuração já achou um teto estourado.
* **Previsão (C1)** — premissa ancorada no observado (a Selic de fábrica estava 3,8 p.p.
  fora), conversão de taxa composta, saneamento da série antes do treino e **espaço fiscal
  em reais**: Fortaleza tem R$ 826 milhões de margem no limite de pessoal.
* **Cenários (C2)** — editar cria versão em vez de destruir, cada versão grava sobre qual
  entrega foi calculada, e reabrir mostra o guardado **e** o de hoje, lado a lado.

**E uma frente nova, aberta e crítica.** O último teste escrito nesta rodada — completude
da carga de transferências — não fechou por 12 linhas, e as 12 revelaram o **A14**: as
tabelas de transferência guardam `versao_entrega` e não guardam **qual versão vence**. A
previsão e a conciliação da Receita somam todas. Fortaleza aparece com R$ 3,095 bi de FPM
em 2024 onde o real é R$ 1,547 bi — exatamente o dobro; em 2025 são 185 de 185 entes com
versão duplicada. Está quantificado e localizado, e é a próxima sprint (§9, A5).

O que **não** está fechado está em §20 e no A14, com o motivo. Nada aqui foi dado por
concluído sem verificação por dado — e o A14 é a prova de que a régua vale: ele foi achado
por um teste escrito para provar que a correção anterior tinha funcionado.

### Achados já confirmados nesta auditoria

| # | Achado | Evidência | Gravidade |
|---|---|---|---|
| A1 | **União e Distrito Federal sem esfera** em `gold.dim_ente` | consulta direta: `esfera IS NULL` para `1` e `53` | **Crítica** — viola a invariante nº 1 do domínio (a esfera decide o teto). O DF segue os tetos de estado (pessoal 49%); sem esfera, nenhum limite se aplica corretamente |
| A2 | **Mínimos de saúde/educação apurados para 1 ente**, contra 180 com RREO ingerido | `mart_indicador` — `saude_minimo`, `educacao_mde`, `fundeb_profissionais`: 1 ente, só 2024 | **Crítica** — a página Saúde & Educação existe para todos e responde para um |
| A3 | **MSC com 1 ente e um único exercício (2022)** | `mart_cobertura_fonte` | Alta — a seção de Patrimônio/MSC é inoperante para 179 entes |
| A4 | **SIOPS e SIOPE com 1 ente, só 2024** | `mart_cobertura_fonte` | Alta — a contraprova dos mínimos não existe na prática |
| **A14** | **Versões de ingestão somando umas às outras** nas transferências | Fortaleza: FPM 2024 lido como R$ 3,095 bi contra R$ 1,547 bi reais. 185 de 185 entes com versão duplicada em 2025 | **Crítica** — números dobrados na previsão e na conciliação da Receita. **Aberto** |
| A5 | **FPM e FUNDEB com 4 e 1 registros** | `mart_cobertura_fonte` | Alta — a conciliação de transferências é nominal |

> Os achados **A22–A27**, da retomada das frentes interrompidas (A0R), estão em §5.1.2 e
> **não** nesta tabela de propósito: aqui só entra o que foi confirmado **contra o dado**.
> Aqueles foram confirmados **no código**, e a distinção é a única coisa que impede uma
> hipótese bem escrita de virar fato por repetição.

---

## 2. Diagnóstico geral

### 2.1 O que está bem

- **Bitemporalidade real.** `dim_entrega` com `versao_entrega`/`vigente`; retificação
  supera sem apagar. Provado em produção: uma entrega com 8 fotografias de cronograma foi
  superada por uma retificação, e a tela mostrou o número certo.
- **Rastreabilidade de origem.** `source_ref` no valor, procedência do endpoint na Central
  de Dados (20 fontes, com exemplo clicável verificado contra a API real), e drill até a
  linha bruta do RREO com conferência mart × entrega.
- **Ausência tratada como ausência.** 404 com extensão RFC 7807 que explica a cadência do
  relatório e oferece o último período com dado. Não há preenchimento com zero nos
  caminhos auditados até aqui.
- **Testes que pegam defeito de domínio.** Casos reais: o eixo natureza da despesa não
  reconciliava (R$ 6,02 bi × R$ 6,89 bi) e o teste barrou antes do deploy.

### 2.2 O que está mal

- **Cobertura muito abaixo do prometido** (A2–A5). É o achado estruturante.
- **Clareza conceitual insuficiente.** Rótulos densos sem nota metodológica.
- **Ano-base × exercício × publicação confundidos.** A CAPAG de um exercício usa dados do
  anterior; a tela não separa os três conceitos.
- **Previsões e cenários "E se?" em nível de protótipo** perante o que uma ferramenta de
  planejamento fiscal governamental exige.
- **Invariantes do domínio não verificadas por dado**, só por código (A1 é o exemplo: o
  código exige esfera, o dado tem NULL).

---

## 3. Inventário das páginas

| Página | Rota | Módulo backend | Finalidade | Cobertura de dado |
|---|---|---|---|---|
| Cockpit | `/dashboard` | `dashboard` | Visão de abertura do ente — 7 camadas | 179 entes |
| Carteira / Estadual | `/carteira` | `dashboard` | Monitorar vários entes; consolidado UF | 179 |
| Limites | `/limites` | `limits` | Conformidade LRF por indicador | 179 |
| Receita | `/receita` | `revenue` | Composição e realização da receita | 180 |
| Despesa | `/despesa` | `expense` | Estágios, função e natureza | 180 |
| Pessoal | `/pessoal` | `personnel` | Limite de pessoal por poder | 179 |
| Dívida e crédito | `/divida` | `debt` | DCL, CAPAG, operações, cronograma | 179 |
| Resultado | `/resultado` | `result` | Primário e nominal | 178 |
| Caixa e RP | `/caixa` | `cash_rap` | Suficiência por fonte, RP sem lastro | 179 |
| Patrimônio | `/patrimonio` | `accounting` | Balanços (DCA) e MSC | DCA 194 · **MSC 1** |
| Saúde e Educação | `/saude-educacao` | `health_edu` | Mínimos constitucionais | **1** |
| Previsões | `/previsoes` | `forecast` | Projeções e cenários "E se?" | 179 |
| Benchmarking | `/benchmarking` | `benchmark` | Comparação com pares | 179 |
| Alertas | `/alertas` | `alerts` | Limites, prazos, defasagem | 179 |
| Assistente | `/assistente` | `assistant` | RAG sobre indicadores + normas | — |
| Relatórios | `/relatorios` | `reports` | Geração e agendamento | 179 |
| Central de Dados | `/central-dados` | `ingestion`/`quality` | Jobs, cobertura, qualidade, lineage | — |
| Procedência da fonte | `/central-dados/fontes/:fonte` | `ingestion` | Endpoints e parâmetros de origem | 20 fontes |
| Operação de crédito | `/divida/operacao/:id` | `debt` | Ficha PVL + CDP + cronograma | 2 entes |
| Linha bruta | `/receita/linha/:cod`, `/despesa/linha/:eixo/:cod` | `revenue`/`expense` | Fundo do drill: linha do RREO | 180 |
| Admin | `/admin` | `tenancy` | Organização, usuários, RBAC, billing | — |
| Plataforma | `/plataforma` | `platform` | Superusuário: licenças | — |
| Perfil | `/perfil` | `tenancy` | Sessão e preferências | — |

---

## 4. Inventário dos indicadores

Cobertura real medida em `gold.mart_indicador` (2026-08-03):

| Indicador | Entes | Períodos | Intervalo | Observação |
|---|---:|---:|---|---|
| `divida_consolidada_liquida` | 179 | 12 | 2022-B2 .. 2025-B6 | Só bimestres pares (origem RGF) |
| `investimento_rcl` | 179 | 24 | 2022-B1 .. 2025-B6 | Gerencial — sem teto legal |
| `pessoal_executivo` | 179 | 12 | 2022-B2 .. 2025-B6 | |
| `rcl_per_capita` | 179 | 24 | 2022-B1 .. 2025-B6 | Gerencial · R$/hab |
| `resultado_primario_rcl` | 178 | 18 | 2023-B1 .. 2025-B6 | |
| `saude_minimo` | **1** | 6 | 2024-B1 .. 2024-B6 | **Lacuna crítica** |
| `educacao_mde` | **1** | 6 | 2024-B1 .. 2024-B6 | **Lacuna crítica** |
| `fundeb_profissionais` | **1** | 6 | 2024-B1 .. 2024-B6 | **Lacuna crítica** — bloqueada na fonte (A13) |
| `garantias` | 178 | 12 | 2022-B2 .. 2025-B6 | ✅ **Novo (B2)** · % da **RCL Ajustada** |
| `operacoes_credito` | 178 | 12 | 2022-B2 .. 2025-B6 | ✅ **Novo (B2)** · % da **RCL Ajustada** |

**Ainda ausente do mart:** dívida consolidada **bruta**. Garantias e operações de crédito
eram as outras duas lacunas e foram fechadas na Sprint B2 (3.866 linhas).

### Cobertura por fonte

| Fonte | Entes | Registros | Exercícios |
|---|---:|---:|---|
| `siconfi_rreo` | 180 | 6.897.484 | 2022–2025 |
| `siconfi_rgf` | 179 | 739.055 | 2022–2025 |
| `siconfi_dca` | 194 | 850.145 | 2021–2024 |
| `tesouro_capag` | 5.569 | 5.569 | 2024 |
| `ibge_populacao` | 192 | 393 | 2021–2024 |
| `ibge_pib` | 192 | 576 | 2021–2023 |
| `transferencia_generica` | **185** | **17.352** | 2022–2024 |
| `sadipem_cdp` | 1 (BR) | 19.521 | 2026 |
| `sadipem_pvl` | 2 | 145 | 2021–2026 |
| `sadipem_cronograma_pgto` | 2 | 84 | 2021–2026 |
| `sadipem_op_contratada` | 2 | 20 | 2021–2026 |
| `siconfi_msc` | **1** | 8.285 | **2022** |
| `siops_saude` | **1** | 84 | **2024** |
| `siope_educacao` | **1** | 276 | **2024** |
| `tesouro_fpm` | **185** | **4.758** | 2022–2025 |
| `fnde_fundeb_repasse` | **185** | **4.566** | 2022–2025 |
| `bcb` | 4 séries | 2.205 | — |

`gold.dim_ente`: **5.570 municipais + 27 estaduais (26 + DF) + 1 federal = 5.598** —
exatamente o total publicado pelo SICONFI. Zero entes sem esfera. Os números anteriores
(5.563 / 26 / 2 sem esfera) eram os achados A1, A9 e A10, todos corrigidos.

> Os três números de transferências acima são o achado **A12**: a API pagina de 10 em 10 e
> o conector lia uma página só. Antes da correção: FPM com 2 entes e 4 registros,
> FUNDEB com 1 e 1, transferências com 170. **5% do dado com aparência de 100%.**

---

## 5. Problemas encontrados

*(Consolidação em curso — as quatro frentes de auditoria reportam nesta seção.)*

### 5.1 Confirmados por consulta direta ao banco

| # | Problema | Impacto |
|---|---|---|
| A1 | União e DF sem `esfera` | Limite da LRF não aplicável ao DF, que segue os tetos de estado |
| A2 | Mínimos constitucionais apurados para 1 de 180 entes | Página Saúde & Educação inoperante na prática |
| A3 | MSC com 1 ente / 1 exercício | Seção de Patrimônio inoperante para 179 entes |
| A4 | SIOPS/SIOPE com 1 ente | Contraprova dos mínimos inexistente |
| A5 | FPM (4 registros) e FUNDEB (1) | Conciliação de transferências nominal |
| A6 | **Correção do próprio A6:** garantias e operações de crédito **estão** em `dim_limite_legal`; o que falta é o cálculo em `mart_indicador` | Dois limites da LRF declarados e nunca apurados |

### 5.1.1 Causa raiz de A1, e dois achados que ela revelou

**A1 resolvido.** O SICONFI publica **quatro** esferas: `M` (5.570 municípios), `E` (26
estados), `D` (Distrito Federal) e `U` (União). O normalizador da plataforma conhecia
apenas as duas primeiras; as outras caíam em `NULL`.

A classificação do DF foi decidida por evidência, não por suposição: o próprio Tesouro o
publica na **CAPAG dos estados** — são 27 entes ali, os 26 estados mais o DF. Isso, com o
art. 20, II da LRF (49% da RCL no Executivo) e o art. 32, §1º da CF (o DF acumula
competências estaduais e municipais), sustenta `D → estadual`. A União recebeu esfera
`federal`: conhecida e sem limite cadastrado, que é diferente de desconhecida.

Resultado: **zero entes sem esfera**, e a invariante passou a ser verificada por dado
(`tests/test_invariante_esfera.py`, 14 casos) e não só por código.

| # | Achado revelado no caminho | Evidência | Gravidade | Situação |
|---|---|---|---|---|
| A7 | **`dim_entrega.cod_ibge` guarda códigos de série do BCB** (`11`, `189`, `4390`) numa coluna que em todo o resto significa código IBGE do ente. As demais fontes nacionais (FPM, CAPAG, CDP) usam `'BR'` — o BCB é a exceção | consulta direta; 3 códigos de série presentes | Média — qualquer junção `dim_entrega → dim_ente` descarta ou desencontra essas linhas | Registrado |
| A9 | **Catálogo de entes incompleto: 8 municípios faltando.** `dim_ente` é conformado **sob demanda**; quem nunca foi consultado nunca entrou. Faltavam 1701051, 4322707, 2610806, 2205359, **2313252 (Tarrafas)**, 2402709, 2501153 e 2106805 | O consolidado do Ceará reportava **183 de 184** municípios — erro de denominador em toda média e todo percentual do painel estadual | **Crítica** | ✅ **Corrigido** |
| A10 | **A ingestão do cadastro de entes estava com 5.597 de 5.598.** Um ente (Tarrafas) nunca chegou ao silver | Origem do A9 | Alta | ✅ **Corrigido** (reingestão) |
| A8 | **Resíduo de teste no schema operacional, inclusive em produção**: 3 organizações `Org <hex>` e 6 entes com código sintético `9NNNNNN` (o padrão que o helper de teste gera) | verificado no banco local **e** na EC2 | Alta — polui contagens e mistura dado de teste com operacional | **Aguardando decisão** (ver §20) |

**Sobre A8, com franqueza:** a origem é minha. A produção foi inicializada a partir de um
dump do banco local, e eu não verifiquei o conteúdo operacional antes de transferir. O
dado fiscal (gold/silver) não é afetado — o resíduo está só em `op.organizacao` e
`op.carteira_ente`.

### 5.1.2 Frentes de auditoria interrompidas — reexecutadas (Sprint A0R, 2026-08-05)

As frentes **fiscal/contábil**, **dados/rastreabilidade** e **arquitetura/segurança**
foram interrompidas por limite de sessão antes de produzirem relatório, e ficaram como
P2, P3 e P4 no §20. Esta subseção é o relatório que faltava. Cada frente tem status
próprio, e o que não coube nesta rodada está dito como pendência, não como conclusão.

> **Ressalva de método — leia antes de agir sobre qualquer achado abaixo.** A sessão que
> produziu este relatório rodou num ambiente **sem shell e sem banco**: nenhuma consulta
> SQL, nenhum `pytest` e nenhum comando foram executados aqui. Tudo abaixo é **leitura de
> código com evidência `arquivo:linha`**, mais a consulta ou o comando exato para
> confirmar contra o dado. "Confirmado" nesta seção significa **confirmado no código** —
> a afirmação é sobre o que o programa faz, não sobre quantos entes ele afeta. Onde o
> alcance depende do dado, o item está marcado **hipótese**, com a consulta que a decide.
> É a mesma régua do §12, e ela existe porque a A14 nasceu de uma contagem que só o banco
> sabia. **Nenhuma correção foi aplicada:** sprint de diagnóstico.

#### P2 — frente fiscal/contábil · **relatório concluído** (execução contra o dado pendente)

Escopo pedido: RCL × RCL Ajustada, exclusões do art. 19 §1º, DTP como composição ×
subtração, resultado acima × abaixo da linha.

| Item | Achado | Evidência | Status |
|---|---|---|---|
| RCL × RCL Ajustada — pessoal | O denominador do art. 20 é a **RCL Ajustada publicada no próprio Anexo 01**, com queda para a RCL cheia só quando o ente não a publica — e a linha guarda `rcl_ajustada = NULL` para dizer qual base foi usada. O mart usa **a mesma** base do detalhe, de propósito | `personnel/service.py:134-170,231-248,395-403` | ✅ Correto — e a razão está escrita no código |
| RCL × RCL Ajustada — endividamento | Garantias, operações de crédito e DCL sobre a **RCL Ajustada** (Res. 43/2001), com `denominador='rcl_ajustada'` viajando na linha | Sprint B2, §5.1.4 | ✅ Correto |
| **Leitura da RCL Ajustada** | A consulta que lê o denominador **não filtra a coluna** e usa `limit(1)` **sem `order by`**: casa `conta ilike '%RECEITA CORRENTE L%' and conta ilike '%LIMITES DA DESPESA COM PESSOAL%'` e aceita qualquer linha que volte primeiro. O Anexo 01 publica, para a mesma conta, `Valor` **e** `% sobre a RCL Ajustada` — a contraprova da B2 já usa `coluna like 'Valor'` explicitamente, sinal de que a coluna importa | `personnel/service.py:157-169` × `scripts/validacao_fiscal.py:218-238` (que filtra) | ⚠️ **A24 — confirmado no código**; alcance a medir (consulta em *Como confirmar*) |
| Exclusões do art. 19 §1º | As quatro parcelas estão mapeadas (indenização/incentivo à demissão, decisão judicial de período anterior, exercícios anteriores, inativos com recursos vinculados) e a de inativos é **condicional ao RPPS** | `personnel/pessoal.py:50-57,155-187` | ✅ Correto |
| Exclusões × valor publicado | Havendo DTP (VI) ou Líquida (III) publicadas, elas mandam, e as exclusões viram `bruta − líquida`; a condicionalidade de RPPS **não se aplica** nesse ramo | `personnel/pessoal.py:164-178` | ✅ **Decisão de domínio, não defeito** — ver *falsos positivos* |
| **DTP como composição × subtração** | O MDF define `DTP (VI) = (IIIa + IIIb)` — liquidadas + inscritas em RP não processados —, isto é, a DTP **é** a líquida repartida por estágio, não uma terceira grandeza. A plataforma trata a linha da DTP como o valor oficial da líquida, o que é consistente com o MDF. A P5 não é, portanto, "composição em vez de subtração" no código de hoje | `personnel/pessoal.py:47,127-128,168-173`; rótulo oficial em `scripts/validacao_fiscal.py:209` | 🔎 **P5 rediagnosticada** — ver §20 |
| Resultado acima × abaixo da linha | As duas apurações convivem com identidade verificada (`primário = receitas − despesas primárias`; `nominal abaixo = −Δ DCL`), ajustes metodológicos declarados e tolerância explícita de R$ 100 | `result/service.py:53-60,183-198,319-360,370-412` | ✅ Correto |
| Assimetria RPPS no resultado | O primário usa uma apuração e o nominal outra; a tela explica (U8/B1, U27/F2) | §5.1.5, §12.2 | ✅ Fechado |

#### P3 — frente de dados/rastreabilidade · **relatório concluído** (nove checks **não executados**)

Escopo pedido: `source_ref` endpoint a endpoint, varredura de valores fixos, execução dos
nove checks de qualidade.

**`source_ref`, endpoint a endpoint.** São **160 rotas** em 27 roteadores; **22 módulos de
contrato** declaram `source_ref` (122 ocorrências). O inventário virou catraca executável
em `tests/test_auditoria_a0r.py::test_nenhum_contrato_perde_o_source_ref_que_ja_tinha` —
perder o que já se tem passa a quebrar a suíte. Duas lacunas reais e uma dispensa:

| Contrato | Números que devolve | Traz `source_ref`? | Leitura |
|---|---|---|---|
| `reconciliation/schemas.py` (`GET /reconciliacao/rcl_rgf`) | `valor_plataforma`, `valor_oficial`, `diferenca` | ❌ — traz `fonte_oficial` e `metodologia`, **não** a `versao_entrega` de cada lado | **A26** — é a tela que existe para dizer "confere ou diverge"; sem a versão dos dois lados, a divergência não é reproduzível depois de uma retificação |
| `quality/schemas.py::CheckOut` | `esquerda`, `direita`, `diferenca` | ❌ — e `gold.data_quality_check` **não guarda `versao_entrega`**: a chave única é `(check, fonte, cod_ibge, periodo)` | **A26** — o check roda sobre uma versão vigente (`quality/service.py:72-80`) e o resultado sobrevive à retificação sem dizer sobre qual versão foi apurado |
| `coverage/schemas.py` | contagens de entes/períodos | ❌ | **Dispensado** — cobertura mede o *produto*, não é número lido de demonstrativo; exigir `source_ref` aqui seria ritual |

**Varredura de valores fixos.** Nenhum teto da LRF aparece codificado no backend: a busca
por `54|49|120|200|16|22|15|25|70` como literal de limite não retorna **nenhuma**
ocorrência em `src/app` — os tetos vêm de `gold.dim_limite_legal`, como a §2 exige. As
faixas 90%/95% são derivadas em um único lugar, com override do banco
(`indicators/limites.py:36-48`). Duas ocorrências merecem registro, nenhuma delas defeito:

- `ingestion/connectors/siconfi_rreo_minimos_pdf.py:486` divide por `0.70` para
  reconstruir a base do FUNDEB a partir do mínimo publicado no PDF. É regra legal
  embutida em conector — aceitável enquanto for a única forma de recuperar a base, mas
  **é o único percentual legal fora de `dim_limite_legal`** e deve ser citado como tal.
- IPCA 4,5% / Selic 10,5% de fábrica no frontend — o valor fixo mais caro já encontrado —
  já foi corrigido na C1 (§5.1.6); a varredura confirma que não voltou.

**Os nove checks de qualidade.** Inventariados e auditados por código; **não executados**
(sem banco nesta sessão). São nove códigos por ente/período — `receita_soma_filhos`,
`despesa_estagios_monotonicos`, `rcl_calculada_vs_publicada`, `minimo_saude_recalculado`,
`minimo_educacao_recalculado`, `dcl_a6_vs_rgf`, `mart_vs_detalhe_pessoal`, `msc_vs_dca` e
`freshness_*` (que se desdobra em 4 SLAs, totalizando 12 linhas por execução), mais
`contrato_layout` e `execucao_agendada`, que são registrados por evento e não por varredura
(`quality/service.py:100-136`, `checks.py`). A auditoria do código encontrou um defeito:

> **A23 — o check de pessoal reconcilia contra um denominador que o produto não usa.**
> `mart_vs_detalhe_pessoal` recalcula `despesa_liquida ÷ fato_rcl.rcl_12m × 100` e compara
> com `mart_indicador.valor_pct_rcl`, que é apurado sobre a **RCL Ajustada** publicada
> (`checks.py:486-510` × `personnel/service.py:395-402`). Onde o ente publica RCL Ajustada
> diferente da RCL cheia — que é o caso normal, e é a razão de a B2 existir — os dois lados
> **têm** de divergir. Com `TOL_PONTOS = 0,01` e falha acima de 0,1 p.p., isso vira `falha`,
> e falha vira alerta na fila da organização (`quality/service.py:205-268`). É ruído
> estrutural produzido pelo próprio verificador, exatamente sobre o indicador mais sensível
> do produto. Confirmado no código; o número de entes afetados sai da consulta abaixo.

#### P4 — frente de arquitetura/segurança · **relatório concluído** (execução empírica pendente)

Escopo pedido: isolamento entre organizações (404 × 403), regras fiscais duplicadas,
endpoints sem `assert_ente_in_scope`.

**Isolamento entre organizações.** A convenção está certa e é coerente onde existe:
recurso de outra organização **não vaza pelo código de status** porque o repositório já
filtra por `org_id` e o serviço devolve **404** — o registro simplesmente não existe para
quem pergunta (`forecast/service.py:951-958`, `forecast/cenarios.py:169-174,306-310`,
`alerts/repository.py:52,63,97,126,145`). Ente fora da carteira dá **403** com causa
distinta de "sem licença" (`shared/scope.py:150-174`), que é a distinção certa: uma se
resolve no cadastro do cliente, a outra no comercial. **O que falta é a régua ser
exigida:** `tests/test_sprint28_seguranca.py:248` aceita `403 ou 404` para identificador
de outra organização. Com essa asserção, no dia em que uma rota passar a responder 403 —
vazando a existência do recurso alheio — a suíte continua verde. É o primeiro critério
objetivo da E1.

**Endpoints sem gate de escopo.** Varredura dos 27 roteadores: 19 recebem ente na URL
(`{cod_ibge}`) ou na query (`ente=`). Destes, **17 validam** escopo no roteador ou no
serviço; **2 não**:

| Rota | Situação | Leitura |
|---|---|---|
| `GET /ingestao/data?fonte=&ente=&periodo=` | Exige só a capacidade `administrar`; **nenhuma** chamada a `assert_ente_in_scope` no roteador (`ingestion/router.py:185-197`) nem no serviço (`ingestion/service.py:377-407`) | **A22 — confirmado no código.** O dado é público (SICONFI), então não é vazamento de dado de tenant; o que ele fura é o **gate de licença**, que vive dentro do `assert_ente_in_scope`. Uma organização licenciada para um município lê o silver de qualquer um dos 5.598 |
| `modules/platform/*` | Control plane, sessão de superusuário, sem carteira a respeitar | ✅ Exceção legítima — registrada para que a catraca não a confunda com defeito |

Verificados e **limpos** no mesmo varredor (eram os candidatos óbvios): `POST /relatorios`
em lote valida cada ente contra o escopo antes de gerar (`reports/service.py:147-157`);
`POST /estadual/{uf}/consolidado/refresh` chama `assert_uf_in_scope` antes de materializar
(`estadual_router.py:116`); `/ingestao/run` e `/ingestao/replay` passam por
`_validar_entes_no_escopo` (`ingestion/jobs_service.py:115-123,231-239`); o painel de
qualidade filtra por escopo em vez de confiar no parâmetro (`quality/service.py:334-350`).

**Regras fiscais duplicadas.** Uma, grave pela repetição:

> **A25 — a tradução "bimestre do RREO → quadrimestre do RGF" existe seis vezes, em duas
> semânticas diferentes.** `quality/service.py:83-90`, `cash_rap/service.py:96-102` e
> `result/service.py:95-101` devolvem `None` para bimestre ímpar; `benchmark/service.py:817-826`,
> `dashboard/estadual_service.py:227-237` e `reports/service.py:391` devolvem o quadrimestre
> por teto, inclusive para ímpar. Ou seja: **para o mesmo período, metade da plataforma diz
> "não há RGF correspondente" e a outra metade aponta um.** Nenhuma das seis conhece o RGF
> **semestral** do município com menos de 50 mil habitantes (LRF art. 63) — a mesma lacuna
> que a A6 já registrou de passagem e que `shared/periodo.py::em_bimestre` resolve na direção
> inversa. A fonte única declarada (§6.6) não tem a conversão neste sentido: é por isso que
> cada módulo escreveu a sua.

A duplicação de vocabulário de faixa (faixa legal → farol/severidade) também sobrevive em
quatro mapas no backend — `dashboard/service.py:24-32`, `cockpit_service.py:78,187-188`,
`carteira_service.py:49-61`, `estadual_service.py:810-811` —, com dois nomes para o mesmo
estado (`conforme` × `normal`). A U11 unificou o lado do frontend; o backend ainda não.
Gravidade baixa (é apresentação), registrada para não ser redescoberta uma terceira vez.

#### Falsos positivos verificados e descartados

Registrar o que **não** é defeito custa pouco e evita que a próxima rodada gaste a mesma
hora: cada linha abaixo parecia achado e não é.

| Suspeita | Por que não é |
|---|---|
| "A DTP ignora a condicionalidade de RPPS quando há valor publicado" | É a decisão certa: publicada a DTP, o limite é medido sobre o número do **ente**. Recalcular pelos componentes trocaria o oficial por uma reconstrução nossa. Coberto por teste de caracterização |
| "A linha de percentual do Anexo 01 entra no numerador" | Não entra: `classificar_valor_coluna` recusa coluna com `%`/`PERCENTUAL`/`SALDO` (`pessoal.py:102-117`), e o percentual do Anexo 01 é **coluna**, não conta (`% sobre a RCL Ajustada`) |
| "A soma por papel dupla-conta colunas (12 meses + RP inscritos)" | O somatório em `personnel/service.py:193` de fato aceita mais de uma coluna de valor, mas as 4.168 linhas de Anexo 01 do acervo usam só `Valor` (medido na A5) — **risco latente, não defeito ativo**. A consulta que o vigia está no bloco *Como confirmar*, e a regra está travada por teste |
| "Relatório em lote gera para ente fora da carteira" | Valida ente a ente (`reports/service.py:155-157`) |
| "Refresh do consolidado estadual roda para UF alheia" | Valida antes (`estadual_router.py:116`) |
| "O painel de qualidade aceita `?ente=` fora do escopo" | Aceita o parâmetro e **filtra pelo escopo**, devolvendo vazio em vez de 403 — não vaza |
| "A varredura da carteira é N+1 na leitura" | A leitura é por conjunto (`carteira_repo.list_mart_by_scope`); o N+1 está na **materialização**, e é outro problema (ver E1) |

#### Achados novos desta rodada

| # | Achado | Evidência | Gravidade | Status |
|---|---|---|---|---|
| **A22** | `GET /ingestao/data?ente=` sem gate de escopo/licença | `ingestion/router.py:185-197`, `ingestion/service.py:377-407` | Alta (comercial/conformidade; o dado é público) | ✅ **Corrigido na E1** — `assert_ente_in_scope` no roteador **e** no serviço; três estados cobertos por teste |
| **A23** | Check `mart_vs_detalhe_pessoal` compara o mart (RCL Ajustada) com um recálculo pela RCL cheia — falha falsa que vira alerta | `quality/checks.py:486-510` × `personnel/service.py:395-402` | Alta | Confirmado no código · alcance a medir |
| **A24** | Leitura da RCL Ajustada do Anexo 01 sem filtro de coluna e com `limit(1)` sem ordenação | `personnel/service.py:157-169` | Alta se materializar (denominador do limite de pessoal) | Confirmado no código · **hipótese** de alcance |
| **A25** | Conversão bimestre→quadrimestre reimplementada 6× em 2 semânticas; nenhuma cobre RGF semestral | `quality/service.py:83`, `cash_rap/service.py:96`, `result/service.py:95`, `benchmark/service.py:817`, `estadual_service.py:227`, `reports/service.py:391` | Média | ✅ **Consolidado na E1** em `shared/periodo.py::em_periodo_rgf`, com as duas semânticas **nomeadas** e cadência semestral; caracterização provou equivalência antes de trocar |
| **A26** | Reconciliação e checks de qualidade devolvem número fiscal sem `source_ref`, e o check não guarda a `versao_entrega` conferida | `reconciliation/schemas.py`, `quality/schemas.py:15-29`, `quality/models.py:38-48` | Média | ✅ **Corrigido na E1** — `source_ref` nos dois contratos (nos **dois lados** da reconciliação) e `versao_entrega` na chave de `gold.data_quality_check` (migration 0041) |
| **A27** | Gate de escopo faz N+1 por requisição em conta estadual: `_estado_prefixes` percorre a carteira e consulta `dim_ente` ente a ente, **sem** o cache de sessão que a cobertura de licença já usa | `shared/scope.py:126-142` × `:96-117` | Média (desempenho na rota mais quente) | ✅ **Corrigido na E1** — consulta em lote + memorização em `session.info`; orçamento de ≤ 5 consultas preso por teste |

#### Como confirmar cada item contra o dado (nada disto foi executado aqui)

```sql
-- A24: o Anexo 01 publica a linha da RCL Ajustada em mais de uma coluna?
--      Mais de uma linha por (ente, período, versão) ⇒ o limit(1) sem order by escolhe.
select cod_ibge, periodo, versao_entrega, count(*) as linhas,
       array_agg(distinct coluna) as colunas
  from silver.siconfi_rgf
 where anexo like 'RGF-Anexo 01%'
   and conta ilike '%RECEITA CORRENTE L%'
   and conta ilike '%LIMITES DA DESPESA COM PESSOAL%'
 group by 1,2,3 having count(*) > 1
 order by linhas desc limit 50;

-- Falso positivo "dupla contagem de coluna": alguma conta do Anexo 01 publica
-- mais de uma coluna de valor na mesma entrega?
select coluna, count(*) from silver.siconfi_rgf
 where anexo like 'RGF-Anexo 01%' group by 1 order by 2 desc;

-- A23: quantos pares o check reprovaria só por causa do denominador? (aproxima o que
--      mart_vs_detalhe_pessoal faz: o apurado usa a RCL Ajustada, o check refaz na cheia)
select count(*) as pares,
       count(*) filter (
         where abs(f.pct_rcl - f.despesa_liquida / r.rcl_12m * 100) > 0.1
       ) as reprovariam_so_pelo_denominador
  from gold.fato_pessoal f
  join gold.fato_rcl r
    on r.cod_ibge = f.cod_ibge
   and r.periodo_ref = left(f.periodo, 4) || '-B' ||
       (2 * cast(right(f.periodo, 1) as int))::text
 where f.poder_codigo = 'ENTE.EXEC'
   and f.periodo like '%-Q%'
   and f.rcl_ajustada is not null
   and r.rcl_12m > 0;

-- P5/DTP: divergências entre o nosso percentual e o publicado pelo próprio ente.
select f.cod_ibge, f.periodo, f.pct_rcl, s.valor as pct_publicado,
       f.pct_rcl - s.valor as diferenca
  from gold.fato_pessoal f
  join silver.siconfi_rgf s
    on s.cod_ibge = f.cod_ibge and s.periodo = f.periodo
   and s.anexo like 'RGF-Anexo 01%'
   and s.conta = 'DESPESA TOTAL COM PESSOAL - DTP (VI) = (IIIa + IIIb)'
   and s.coluna = '% sobre a RCL Ajustada'
 where f.poder_codigo = 'ENTE.EXEC' and abs(f.pct_rcl - s.valor) > 0.01
 order by abs(f.pct_rcl - s.valor) desc;
```

```bash
# P2/P5 — validação contra o publicado, amostra estratificada (7 entes, 6 indicadores)
python -m scripts.validacao_fiscal --exercicios 2024 2025 --saida docs/validacao_a0r.md

# P3 — os nove checks, sobre um ente/período com dado real
python - <<'PY'
from app.core.db import admin_session
from app.modules.quality import service as q
with admin_session() as s:
    for r in q.executar_para_ente(s, "2304400", "2024-B6"):
        print(r.check_codigo, r.status, r.diferenca, r.detalhe.get("motivo", ""))
PY

# P4 — isolamento entre organizações, empírico
pytest tests/test_sprint28_seguranca.py tests/test_scope.py tests/test_rls.py -q

# Catracas desta auditoria (não precisam de dado, só do schema da suíte)
pytest tests/test_auditoria_a0r.py -q
```

### 5.1.3 Sprints A3 e A4 — invariantes e lacunas de ingestão

#### A12 — **A API de transferências pagina de 10 em 10, e o conector não paginava**

O achado mais grave desta rodada. `JsonEnvelopeRecordsClient` lia uma única página; a API
de transferências constitucionais devolve **10 registros por vez** e sinaliza continuação
em `next`. Consequência medida no Ceará:

| | antes | depois |
|---|---:|---:|
| municípios com FPM em junho/2025 | **11** | **185** |

Ingeríamos de Abaiara a Aquiraz — os dez primeiros em ordem alfabética — e os outros 174
municípios simplesmente não existiam. **Cinco por cento do dado, com aparência de cem.** A
conciliação de receita da página de Receita rodava sobre isso.

O modo de falha é o pior possível: silencioso, plausível e estável. Uma lista que sempre
volta com dado não desperta suspeita. Gravidade: **Crítica**. Situação: ✅ **corrigido**,
com 5 testes que cobrem inclusive o `next` que nunca esvazia.

#### A11 — RCL zero: causa raiz corrigida

A materialização chamava `_calcular_rcl_puro([])` quando a entrega existia sem o Anexo 03,
e gravava zero. Agora recusa com 404 explícito — *"apurar zero seria afirmar que o ente não
arrecadou"*. As 32 linhas falsas foram removidas após prova de que nada dependia delas: os
indicadores usavam `base_valor` igual à RCL publicada no RGF (R$ 57.301.035,70), não o zero.

#### Verificador de invariantes (A3)

Sete invariantes estruturais rodam contra o banco inteiro, não contra o código:

| Invariante | Situação |
|---|---|
| `esfera_obrigatoria` | ✅ |
| `esfera_coerente_com_codigo` | ✅ |
| `catalogo_cobre_o_silver` | ✅ |
| `rcl_nunca_zero` | ✅ (era 32) |
| `indicador_tem_origem` | ✅ |
| `uma_entrega_vigente_por_periodo` | ✅ |
| `faixa_coerente_com_o_sentido` | ✅ |

**Uma correção ao meu próprio trabalho:** a invariante de faixa acusou 5 violações falsas
porque eu inventei o vocabulário `abaixo_minimo`. O real é `adequado`/`insuficiente` para
piso e `normal`/`alerta`/`prudencial`/`excedido` para teto. Corrigida e tornada
bidirecional — tão grave quanto não marcar quem estourou é marcar quem não estourou.

#### A13 — **Os mínimos constitucionais estão bloqueados na fonte**

Testei a API do SICONFI para três entes: os Anexos publicados são 01, 02, 03, 04, 06, 07,
09, 10, 11 e 14. **Os Anexos 08 (educação) e 12 (saúde) não vêm.** A lacuna dos mínimos
não é da nossa carga e **não se fecha ingerindo mais** — só existe via raspagem do PDF que
cada ente publica no próprio portal, com endereço e layout distintos.

Situação: **bloqueado**. O que dá para fazer sem desbloqueio: o selo de cobertura (A1) já
declara "1 de 185" nessa página, e a lacuna deixou de ser invisível.

| Lacuna da A4 | Fonte tem? | Situação |
|---|---|---|
| Mínimos (Anexos 8/12) | **Não** | 🚫 **Bloqueado na fonte** |
| FPM / FUNDEB / transferências | Sim | ✅ **Corrigido** (paginação) — carga completa: **2.220 linhas por fonte/ano**, exatamente 185 entes × 12 meses |
| MSC | Sim (3/3 testados) | ⏳ Closável — adiado por volume (184 entes × 12 meses × 4 classes × 3 tipos ≈ 26 mil chamadas) |
| SIOPS / SIOPE | Sim (HTTP 200) | ⏳ Closável — não executado nesta rodada |

### 5.1.4 Sprint B2 — os dois limites que nunca eram apurados

`dim_limite_legal` declara quatro tetos de endividamento; só a DCL era calculada.
**Garantias (22%) e operações de crédito (16%) existiam como regra e não como número** —
a plataforma sabia o limite e nunca dizia se o ente estava dentro dele.

**3.866 indicadores materializados, 178 entes, 2022-B2 a 2025-B6, zero erros.** E a
primeira apuração já encontrou o que ninguém podia ver:

| Ente | Período | Indicador | % | Teto | Faixa |
|---|---|---|---:|---:|---|
| 2304285 | 2023-Q3 | operações de crédito | **16,80%** | 16% | **excedido** |
| 2611606 | 2024-Q3 | operações de crédito | 15,62% | 16% | prudencial |

#### O denominador é a RCL **Ajustada**

Res. 43/2001 do Senado: estes limites se apuram sobre a RCL Ajustada, que deduz as
transferências de emendas individuais (CF art. 166-A, §1º). Usar a RCL cheia infla o
denominador e **subestima** o percentual — o ente aparece com mais folga do que tem. O RGF
publica a Ajustada explicitamente, então é o número do ente que serve de base, não um
recálculo nosso. O campo `denominador='rcl_ajustada'` viaja em todas as 3.866 linhas para
que nenhuma tela rotule isto como "% da RCL".

#### Três defeitos meus, achados por conferir contra o publicado

| # | Defeito | Efeito | Como apareceu |
|---|---|---|---|
| B2-a | O **Anexo 04 nomeia a coluna de outro jeito** (`"Até o Quadrimestre de Referência (a)"`) | Fortaleza aparecia com **R$ 0** de operações de crédito tendo R$ 495 milhões — folga inventada num limite | Comparação com o valor que eu tinha visto na sondagem |
| B2-b | A contraprova **não filtrava a coluna** | Pegava o "SALDO DO EXERCÍCIO ANTERIOR" (0,43%) contra o nosso 0,28% e acusava divergência falsa. O cálculo estava certo | Investigar a divergência em vez de aceitá-la |
| B2-c | Gravava sob o período do **RGF** (`2025-Q3`) | `mart_indicador` é ancorado no bimestre do RREO: o indicador existia e **não aparecia em tela nenhuma** | Conferir o semáforo depois de materializar |

#### Uma decisão de domínio, registrada

Conceder garantia é raro: 8 de 179 entes têm a linha. Mas o Anexo 03 **é** o demonstrativo
das garantias, e 179 entes o entregam. Entregá-lo sem linha de garantia é o ente
**declarando** que não concedeu nenhuma. Portanto: sem o anexo → `None`; com o anexo e sem
a linha → `0`. Tratar os dois casos igual inventaria um cumprimento que ninguém declarou.

Providências legais cadastradas para as três faixas de cada um (Res. 43/2001 arts. 7º e 9º,
LRF arts. 32, 33 e 40).

#### A materialização virou script versionado

A primeira apuração foi ad-hoc — o que significa que **produção não teria os indicadores**.
`scripts/materialize_endividamento.py` reproduz a carga, é idempotente (reexecutar 2025-Q3
regravou 326 linhas e o total permaneceu em 3.866) e distingue no relatório final "entrega
sem RCL Ajustada" de "erro": a primeira é ausência legítima, a segunda é defeito nosso.

Um quarto defeito apareceu escrevendo o script: `gold.dim_ente.uf` guarda a **sigla**
(`CE`), não o código do IBGE. `--uf 23` casava com nada e o script terminava anunciando
sucesso sobre zero ente. Agora um filtro que não casa aborta com mensagem — erro de
digitação não pode passar por "nada a fazer".

### 5.1.5 Sprint B1 — clareza conceitual: dizer o que o número significa

Veredito da frente de UX: *"a plataforma é honesta sobre **ausência** de dado; ainda não é
honesta sobre **significado**"*. A B1 fecha essa segunda metade. Dez achados, e o critério
para entrar aqui foi um só: **o rótulo fazia o gestor concluir o oposto do dado**.

| # | O que o gestor lia | O que o dado dizia | Correção |
|---|---|---|---|
| U3 | "27,10% / **teto** 15%" sob o título "Impacto nos mínimos (pisos)" | 27,10% contra piso de 15% é **cumprimento com folga** | `sentido` propagado ao `LimitImpactTable`; faixa e cor invertem para piso |
| U4 | Duas colunas "Computado", lado a lado | Uma inclui RPNP sem lastro, a outra não — é a diferença que decide o cumprimento do mínimo | "Computado (válido)" × "Bruto (antes do expurgo de RP sem lastro)" |
| U5 | Uma coluna "Divergência" com dois números incompatíveis, mesma cor de risco | Participação no agregado ≠ divergência percentual | Separadas em "Participação" e "Divergência" |
| U6 | Valor-herói prometendo o caixa do ente | É a soma **só das fontes superavitárias**; as deficitárias não abatem (LRF art. 50, I) | Rótulo explícito + `total_disp_liquida_apos_negativa` novo no backend, com o déficit exibido ao lado |
| U7 | Participação de 21% exibida como **34%** | Denominador era a soma dos filhos com negativos zerados por clamp | Agregado do nó como denominador; participação **omitida** quando há negativo no nível; cabeçalho "% do nível" |
| U8 | "com RPPS"/"sem RPPS" sem expansão | O primário usa uma apuração e o nominal outra — parte da divergência acima × abaixo da linha vem daí, não de erro do ente | `NotaRpps` explicando a assimetria |
| U9 | Selo verde de qualidade em página de RGF | O selo consultava o período **RREO**; e falha de rede produzia a mesma tela que "nenhuma divergência" | Período da página como parâmetro; falha passa a dizer "não foi possível verificar" |
| U10 | `PCT_RCL`, `holt_winters`, `rcl_ajustada` na tela | Vocabulário do banco, não do domínio | `rotuloUnidade` e `rotuloModelo`; `BASE_ROTULO` ganhou a chave que faltava |
| U11 | O mesmo estado legal como "Limite excedido" no cockpit, "Crítico" na carteira e "Acima do teto" nos limites | São a mesma faixa da LRF | Os três mapas locais eliminados; `rotuloFaixa` é a régua única, e ganhou `critico`/`sem_dados`, os códigos do farol que faltavam |
| U18 | "Sprint 15", "drill §6.1", "com dados reais" | Vocabulário de desenvolvimento, sem significado para o gestor | Reescritos em linguagem de domínio |
| B12 | Cabeçalho de coluna literalmente `—`, sobre a coluna que diz `confere`/`DIVERGE` | É a conferência que transforma o drill em prova | "Conferência" |

#### Duas peças novas, reusáveis

`NotaMetodologica` (recolhida por padrão, com o dispositivo legal que fundamenta a escolha)
e `Termo` (glossário inline). A decisão de projeto: **a dúvida e a resposta ficam no mesmo
lugar**. Uma página de metodologia separada obriga o gestor a abandonar o número para
entender o número — e quem sai raramente volta ao mesmo ponto.

`rotuloModelo` diz o que o modelo **assume**, não o nome do método: "Tendência com
sazonalidade", não "holt_winters". É a premissa que decide se a projeção serve à pergunta.

#### O que a B1 deliberadamente **não** fez

Não trocou nenhum número. Toda alteração é de rótulo, de denominador de percentual exibido,
ou de campo novo no backend para permitir dizer a verdade completa (`total_disp_liquida_apos_negativa`).
Onde o rótulo estava errado porque o **cálculo** estava errado — como o denominador da
árvore de drill — o cálculo foi corrigido e coberto por teste.

**Cobertura:** 9 testes novos em `clareza-conceitual.test.tsx`; suíte do front em 181
testes; `tsc` limpo.

### 5.1.6 Sprint C1 — a previsão em nível de decisão de governo

O diagnóstico dizia *"previsões e cenários em nível de protótipo perante o que uma
ferramenta de planejamento fiscal governamental exige"*. Três coisas faltavam, e cada uma
produzia um erro que a tela não deixava ver.

#### 1. A premissa era de fábrica

A tela abria com **IPCA 4,5%** e **Selic 10,5%** escritos no código do frontend. Não eram
premissas do gestor nem projecoes de mercado: dois números que alguém digitou uma vez e que
apareciam com a mesma aparência de qualquer valor informado. O acervo tem as séries do BCB
desde sempre.

| Premissa | Valor de fábrica | **Observado no acervo** | Fonte |
|---|---:|---:|---|
| IPCA (12 meses) | 4,50% | **4,64%** | BCB/SGS 433, ate jun/2026 |
| Selic (12 meses) | 10,50% | **14,28%** | BCB/SGS 4390, ate jul/2026 |
| Variação do FPM | 0% (fixo) | **+10,51%** (Fortaleza) | Tesouro, 2024->2025 |

**A Selic estava 3,8 pontos percentuais fora.** Quem aceitasse o padrão — que é o que quase
todo mundo faz — simulava sobre suposição alheia sem saber que era uma suposição. Agora cada
controle mostra o observado, a data, a fonte e quantas observações entraram, com um botão
"voltar ao observado" para depois de mexer. Onde a série não sustenta o cálculo, o controle
**não sugere**: mostra o motivo e espera o valor.

#### 2. A conversão anual -> mensal era linear

`_overrides_exogenas` fazia `taxa_anual / 12`. As séries do BCB são variações **mensais**, e
a conversão correta é composta:

| Taxa anual | Divisão por 12 | **Composta** | Erro |
|---:|---:|---:|---:|
| 4,5% | 0,3750% | 0,3675% | +2,0% |
| 10,5% | 0,8750% | 0,8355% | +4,7% |
| 12,0% | 1,0000% | 0,9489% | +5,4% |

O erro não fica no primeiro passo: **compoe a cada período do horizonte**. Corrigido, com
teste de ida e volta (converter para mensal e reacumular 12 meses devolve a taxa original).

#### 3. O modelo não sabia recusar

Um modelo estatístico aceita qualquer entrada e devolve saída com a mesma cara de
legitimidade. A série de pessoal do ente 2307650 tem o ponto de 324,49% da RCL do **A15**:

| | Projeção para 2026 |
|---|---:|
| Antes (série crua) | **2,29%** da RCL — igualmente impossível |
| Depois (série saneada) | **50,24%** — coerente com as 11 observações boas |

A regra do saneamento é estreita de propósito: sai so o que o domínio **garante** ser
impossível (% da RCL fora de [0, 100], valor nao-positivo em grandeza positiva). Um ente com
60% da RCL em pessoal é ilegal e real — e é justamente o caso de uso central da plataforma;
uma limpeza que removesse "valores altos" apagaria quem mais precisa aparecer. Quebra
estrutural (mudança de mandato, fim de convênio) também fica: e informação, não ruído.

**Excluir sem dizer trocaria um número errado por outro que ninguém pode auditar.** A
resposta carrega `memoria.saneamento` com o período, o valor e o motivo de cada exclusão, e
a tela exibe. Quando nada foi excluido, o campo vem com zero — silêncio sobre a limpeza e
indistinguível de não ter havido limpeza.

#### 4. E o que faltava para virar decisão: **espaço fiscal**

A projeção respondia *quando* o limite seria cruzado. Faltava *quanto ainda cabe* — que e a
metade que se assina, porque empenho não se assina em pontos percentuais.

| Ente | Indicador | Projetado | Teto | Margem |
|---|---|---:|---:|---|
| Fortaleza | pessoal | 47,41% | 54% | 6,59 p.p. = **R$ 826,0 mi** |
| Ceará | dívida | 26,02% | 200% | 173,98 p.p. = **R$ 68,9 bi** |

Decisões de projeto registradas:

* **margem é sempre positiva; quem diz o sentido é `situacao`.** "Margem −3,3 p.p." convida
  a leitura errada exatamente no caso em que o erro custa caro. Excedido tem outro rótulo:
  *excesso a eliminar*;
* **o piso inverte.** Num mínimo, folga e estar acima. Calcular a diferença sempre no mesmo
  sentido diria "excedido" para um ente que cumpre o mínimo de saúde com sobra;
* **a base e a RCL observada, não a projetada.** Projetar o denominador junto com o
  numerador embutiria duas incertezas no mesmo número e faria a margem oscilar por razão que
  o gestor não consegue atribuir. Quem quer o efeito de outra RCL usa o cenário, onde essa
  premissa e explícita;
* **sem base, a margem existe so em pontos percentuais.** Zero em reais anunciaria margem
  nenhuma a quem talvez tenha bastante.

#### 5. O que a premissa custa da margem

O fecho do ciclo. A pergunta do gestor não é "qual o percentual projetado" — é *"posso
fazer isto?"*. Com a margem da base e a do cenário lado a lado, a resposta sai na unidade
da decisão: **o cenário consome R$ X da margem**, e se a projeção atravessa o limite, diz.

A subtração exige **margem assinada** (folga positiva, excesso negativo). Sem o sinal, ir
de 6,59 p.p. de folga para 2,00 p.p. de excesso pareceria custar 4,59 p.p. quando custa
8,59 — o erro apareceria exatamente no caso que mais importa. *Descobri isso por teste de
mutação: a primeira versão do teste passava com o sinal removido.*

#### 6. Recondução (LRF art. 23)

Excedido o teto de pessoal, a lei não pede "melhorar": pede eliminar o excesso em dois
quadrimestres, ao menos um terço no primeiro, sob as vedações do §3o. A tela agora diz de
quanto tem de ser cada parcela, em p.p. e em reais. **Não se aplica a piso** — o art. 23
trata do teto, e citá-lo num mínimo descumprido seria dar a base legal errada.

**Cobertura:** 25 testes no backend, 11 no frontend (suíte em 192).

### 5.1.7 Sprint C2 — o cenário salvo vira registro de decisão

Um cenário guardava as premissas e o resultado do momento, e nada mais. Duas coisas
falhavam, as duas em silêncio.

#### Editar destruía

Ajustar uma premissa sobrescrevia o registro. Mas cenário é peça de decisão: *"o que eu
levei à reunião de agosto"* precisa sobreviver ao ajuste de outubro, ou não há como
reconstruir por que a decisão foi aquela. Agora `op.cenario` é o cabeçalho e
`op.cenario_versao` guarda cada salvamento — a versão anterior permanece íntegra.

Renomear o cenário **não** reescreve o nome das versões: cada uma guarda o nome que tinha
quando foi salva, porque apagar isso apagaria o rastro de que o cenário se chamava outra
coisa quando embasou a decisão.

E arquivar substituiu apagar. Um cenário que fundamentou uma escolha não deve sumir porque
alguém quis limpar a lista; sai da tela, permanece auditável, e o arquivamento é reversível.

#### Reabrir mentia por omissão

O resultado congelado era exibido com a mesma cara de um cálculo corrente. Se o ente
entregou RGF novo no intervalo, as mesmas premissas produzem outro número hoje — e a tela
não tinha como dizer qual dos dois estava mostrando.

`GET /cenarios/{id}` passa a devolver **os dois lados**: o guardado e o recalculado com o
dado de hoje, mais a divergência entre eles e **quais entregas apareceram desde então**.
Não escolhe por ninguém: "com o que eu decidi" e "isso ainda vale?" são perguntas
diferentes, as duas legítimas, e a segunda só existe se os dois números estiverem à vista.

A divergência tem **três** estados, não dois:

| Estado | O que significa |
|---|---|
| `comparavel=true, diverge=false` | Recalculado e bate — o cenário continua valendo |
| `comparavel=true, diverge=true` | Recalculado e mudou, com o motivo e as entregas novas |
| `comparavel=false` | **Não deu para comparar** — versão sem procedência registrada |

Colapsar os dois últimos num booleano só faria uma versão migrada do formato antigo
aparecer como conferida. É o defeito que o teste de mutação do frontend trava.

#### A procedência inclui a premissa observada

A coluna menos óbvia de `op.cenario_versao` é `premissas_observadas`. *"Aceitei o IPCA
observado"* muda de significado quando o observado muda: o cenário de agosto rodou com IPCA
de 4,54%, e o mesmo botão em dezembro significa outro número. Sem gravar o valor vigente à
época, a premissa registrada não reproduz coisa alguma.

Junto vão `as_of` e `versoes_entrega` — a impressão digital do dado. É o que permite dizer
*o que* mudou, e não apenas *que* mudou: "o ente entregou o RGF de 2026-Q1 depois que você
salvou" em vez de "o número está diferente".

#### Comparação: interseção, não união

`POST /entes/{ibge}/cenarios/comparar` põe até seis cenários no mesmo eixo. O eixo é a
**interseção** dos horizontes: comparar um cenário de 4 períodos com um de 12 num eixo de
12 deixaria o primeiro terminando no meio do gráfico, e a leitura natural — *"este cenário
despenca no fim"* — seria falsa.

Cenário pedido e não encontrado **permanece na resposta** com `encontrado=false`. Sumir da
lista faria o gestor comparar três curvas achando que são as quatro que escolheu. E
comparar indicadores de escalas diferentes (% da RCL contra reais) devolve a comparação com
aviso — quem pediu pode ter razão, mas a tela diz que as escalas não são a mesma.

#### Exportação com procedência junto

`GET /cenarios/{id}/exportar` sai em CSV ou JSON com **as premissas e a procedência no
mesmo arquivo da curva**. Exportar só a série produziria algo que ninguém audita seis meses
depois: sem saber sob quais premissas e sobre qual entrega foi construída, a curva é um
desenho. Exportar uma versão específica é possível — exportar sempre a última tornaria o
histórico decorativo.

#### Migração dos cenários existentes

Cada cenário atual virou sua versão 1, preservando premissas e resultado. `as_of` ficou
**nulo**: não há como saber a que instante o cálculo antigo se referia, e carimbar `now()`
daria a esses registros uma reprodutibilidade que eles não têm. A tela mostra "procedência
não registrada" e a reabertura declara que não deu para comparar.

**Cobertura:** 22 testes no backend, 13 no frontend, incluindo isolamento entre
organizações verificado por dado (cenário de outra org devolve 404, não 403 — existência
alheia não deve vazar nem pelo código de status). Três mutações confirmadas: versionar que
sobrescreve, divergência que colapsa estados, e comparação que usa a união.

### 5.2 Frente de UX e clareza conceitual — verificados por leitura de código

A auditoria de UX percorreu as 23 páginas, 25 componentes e o shell, cruzando com o backend
onde o rótulo nasce lá. Veredito da frente: *"a plataforma é honesta sobre **ausência** de
dado; ainda não é honesta sobre **significado**"*.

Os achados abaixo foram **verificados por mim, independentemente do relatório**, antes de
entrar aqui.

| # | Achado | Verificação | Gravidade | Situação |
|---|---|---|---|---|
| U1 | **Mínimo constitucional exibido como teto estourado.** O cockpit escolhe o medidor por `temLimite && !porHabitante`, sem consultar `k.sentido`; o `RadialMeter` só importava `classifyCeiling`. Um ente com 27% em saúde (piso 15%) recebia mostrador vermelho e leitura "Acima do teto", contradizendo a legenda 40px abaixo | Confirmado: `CockpitPage.tsx:286`, `RadialMeter.tsx:43`; `dim_limite_legal` tem 7 tetos e 3 pisos — o backend sempre soube | **Crítica** | ✅ **Corrigido** |
| U2 | **Formatação en-US no consolidado de UF.** `toFixed` produz ponto decimal; em pt-BR ponto é separador de milhar. `R$ 1234.56 bi` é ambíguo em três ordens de grandeza | Confirmado: `CarteiraPage.tsx:59-69`. A própria função era inconsistente — a última linha já usava `toLocaleString('pt-BR')` | **Crítica** | ✅ **Corrigido** |
| U3 | Previsões rotula piso como "teto": sob o título "Impacto nos mínimos (pisos)", cada linha lê "27,10% / teto 15%" | `PrevisoesPage.tsx:327,347` | Alta | ✅ **Corrigido** (B1) |
| U4 | Saúde & Educação: duas colunas com o cabeçalho idêntico "Computado", medindo coisas diferentes (com e sem expurgo de RPNP sem lastro) | `SaudeEducacaoPage.tsx:346-353` | Alta | ✅ **Corrigido** (B1) |
| U5 | Receita: a coluna "Divergência" carrega duas métricas incompatíveis — participação no agregado e divergência percentual, com a mesma cor de risco | `ReceitaPage.tsx:409 × 478-489` | Alta | ✅ **Corrigido** (B1) |
| U6 | Caixa: valor-herói promete o caixa total e entrega a soma só das fontes superavitárias; a ressalva vive em 11px depois do número | `CaixaPage.tsx:112-119` | Alta | ✅ **Corrigido** (B1) |
| U7 | Árvore de drill: a coluna "%" usa como denominador a soma dos filhos com negativos zerados por clamp — participações somam >100% na página de Caixa | `ArvoreDrill.tsx:115-121` | Alta | ✅ **Corrigido** (B1) |
| U8 | Resultado: "com RPPS"/"sem RPPS" sem expansão da sigla, com rótulos em `snake_case` do banco, e a assimetria primário-COM × abaixo-da-linha-SEM não é sinalizada | `ResultadoPage.tsx:431-434` | Alta | ✅ **Corrigido** (B1) |
| U9 | Selo de qualidade usa sempre o período RREO, mesmo nas páginas que exibem RGF; e `if (!res.data) return null` faz erro de rede e "tudo certo" produzirem a mesma tela | `SeloQualidade.tsx:70-77` | Alta | ✅ **Corrigido** (B1) |
| U10 | Vocabulário do banco na tela em 9 páginas (`ABAIXO_MINIMO`, `pessoal_executivo`, `PCT_RCL`, `holt_winters`) — a regra existe em `utils/rotulos.ts` e não é aplicada | 10 pontos mapeados | Média | ✅ **Corrigido** (B1) |
| U11 | Três vocabulários para a mesma faixa legal (`Folga`/`Conforme`/código cru) | `theme.ts` × `CockpitPage` × `LimitesPage` | Média | ✅ **Corrigido** (B1) |
| U12 | Infraestrutura de impressão completa (`@page`, `.no-print`, materialização de tabela virtualizada) e **zero** gatilhos: `grep "window.print"` → 0 | `global.css:342-405` | Média | Planejado (B3) |
| U13 | 6 de 13 links "relatório completo" passam modelo inexistente e caem silenciosamente em "Resumo Executivo" | `reports/models.py:26-32` × 4 páginas | Média | Planejado (B3) |
| U14 | `AccessibleChart` (205 linhas, com figure/figcaption e alternativa tabular) tem **zero importações** | — | Média | Planejado (B3) |
| U15 | Escalas de gráfico com dois padrões opostos: `SerieChart` ancora em zero, `TendenciaChart`/`PrevisoesPage` truncam o eixo sem avisar sobre série monetária | `TendenciaChart.tsx:60-72` | Média | Planejado (B3) |
| U16 | Previsões: IPCA 4,5% e Selic 10,5% são `useState` fixos apresentados como premissas — e o backend já ingere as duas séries reais | `PrevisoesPage.tsx:236-237` | Média | ✅ **Corrigido** (C1) — a Selic observada é 14,28% |
| U17 | Seletor de período visível e inerte em 7 rotas; em Patrimônio compete com um seletor próprio | `AppShell.tsx:257` | Baixa | Planejado (B3) |
| U18 | Vocabulário de desenvolvimento vazado ao gestor: "Sprint 15", "drill §6.1", "com dados reais" | 9 ocorrências | Baixa | ✅ **Corrigido** (B1) |

---

### A14 — Versões de ingestão somando umas às outras nas transferências

**Descoberto escrevendo o teste de completude da A4** — a contagem não fechava: `tesouro_fpm`
tinha 2.232 linhas em 2024 onde 185 entes × 12 meses = 2.220. As 12 sobrando eram Fortaleza,
com **duas versões de entrega para os mesmos meses**.

`silver.tesouro_fpm`, `silver.fnde_fundeb_repasse` e `silver.transferencia_generica` guardam
`versao_entrega` e **não guardam qual versão vence**. Não há `vigente`, não há `ingerido_em`,
e `valid_time` é a competência (fim do mês), não a data da carga. Os leitores somam tudo:

```python
# forecast/series.py — _fpm_periodo
select(func.sum(TesouroFpm.valor_liquido)).where(
    TesouroFpm.cod_ibge == cod_ibge, TesouroFpm.ano == p.ano, TesouroFpm.mes.in_(p.meses())
)   # sem filtro de versão
```

**Efeito medido.** Fortaleza, FPM de 2024:

| | |
|---|---:|
| versão `pag2024` (carga paginada, completa) | R$ 1.547,50 mi |
| versão `20260722` (carga anterior, superada) | R$ 1.547,50 mi |
| **o que a previsão e a conciliação leem** | **R$ 3.095,00 mi** |

Não é caso isolado. Em 2025, **185 de 185 entes** têm mais de uma versão no FPM — resíduo
acumulado de `pag2025`, `20260729`, `20260804` e uma `pag-teste` que é resíduo meu.

**Alcance.** Dois consumidores duplicam; um está correto:

| Consumidor | Filtra versão? |
|---|---|
| `forecast/series.py::_fpm_periodo` — exógena das projeções | ❌ **soma todas** |
| `revenue/repository.py` — conciliação de transferências da página de Receita | ❌ **soma todas** (3 leitores) |
| `health_edu/repository.py` | ✅ filtra por `versao_entrega` |
| `ingestion/cobertura.py` | ✅ imune (conta distintos) |

Ironia registrada: **a conciliação é justamente a tela que existe para dizer "confere ou
diverge"**, e ela é uma das que leem dobrado.

**Causa raiz.** Estas tabelas foram desenhadas com `versao_entrega` mas sem a contrapartida
de vigência que o RREO/RGF têm em `gold.dim_entrega`. Enquanto só existia uma carga por
ente/ano, o defeito era invisível — e a correção da paginação (A12), ao criar uma segunda
versão para todo mundo, o tornou universal. **A A12 não causou o defeito; expôs.**

**Rota de correção (viável, sem apagar dado).** `bronze.raw_payload_tesouro_fpm` tem
`ingerido_em`. A ordem de recência é recuperável por junção `(fonte, versao)`, o que permite
eleger a vigente sem migration destrutiva e sem violar a regra de que retificação supera,
não apaga. Alternativa mais limpa: coluna `vigente` nas três tabelas, alinhando-as ao padrão
bitemporal do resto do acervo.

Gravidade: **Crítica** — números dobrados em duas telas. Situação: **identificado,
quantificado e localizado**; correção não iniciada. É a próxima frente.

---

### A15 — O RGF republica os quadrimestres anteriores, e a plataforma ignora a correção

**Descoberto pela Sprint C1**, procurando um ente com pessoal acima do teto para testar a
recondução. Apareceu um com **324,49% da RCL** em despesa de pessoal. Nenhum ente gasta
três vezes a receita corrente líquida com folha; o número descreve um defeito, não um ente.

A causa está na entrega. O ente 2307650 publicou, no RGF de 2023:

| Entrega | Coluna | RCL publicada |
|---|---|---:|
| `2023-Q1` | Até o 1o Quadrimestre | **R$ 152,1 mi** |
| `2023-Q2` | Até o 1o Quadrimestre | **R$ 1.031,3 mi** |
| `2023-Q3` | Até o 1o Quadrimestre | R$ 1.031,3 mi |

O ente **corrigiu o próprio número** — e a correção veio na entrega do quadrimestre
seguinte, não como versão nova do mesmo período. **E assim que o RGF funciona:** cada
entrega republica os acumulados anteriores, e essa republicação e a retificação de fato.

O modelo bitemporal da plataforma cobre "o ente reenviou o mesmo período" (`versão_entrega`
nova). Não cobre "o ente corrigiu o quadrimestre anterior dentro da entrega seguinte" — que
e o caso comum. A materialização usou o primeiro número e nunca voltou.

**Alcance medido:** 63 quadrimestres republicados com valor diferente (>2%) no acervo,
**5 deles com o maior valendo mais que o dobro do menor**:

| Ente | Ano | Coluna | Menor | Maior |
|---|---|---|---:|---:|
| 2307650 | 2023 | Até o 1o Quadrimestre | R$ 152,1 mi | R$ 1.031,3 mi |
| 2309706 | 2025 | Até o 1o Quadrimestre | R$ 52,7 mi | R$ 326,3 mi |
| 2304202 | 2022 | Até o 1o Quadrimestre | R$ 140,2 mi | R$ 393,8 mi |
| 2300903 | 2022 | Até o 3o Quadrimestre | R$ 16,5 mi | R$ 54,3 mi |
| 2309102 | 2022 | Até o 3o Quadrimestre | R$ 18,5 mi | R$ 50,9 mi |

Gravidade: **Alta** — a RCL e o denominador de quase todo limite da LRF, e um denominador
errado produz percentual errado em todas as telas que o consomem. Situação: **identificado,
quantificado e contido na previsão** (a C1 exclui o ponto impossível do treino e declara).
A correção na ingestão — eleger o valor republicado mais recente como vigente — **não foi
feita** e e a próxima frente junto com o A14, que e da mesma família: *versão que existe,
vigência que não se declara*.

> **Um alarme que verifiquei antes de reportar.** A mesma investigação mostrou 4.061 chaves
> `(ente, período, indicador)` com mais de uma linha em `gold.mart_indicador`. **Não e
> defeito:** é a chave bitemporal `cmp:v1:<sha256>` documentada em `indicators/service.py`
> para indicadores derivados de mais de uma entrega, mais a projeção pela versão simples do
> RREO para compatibilidade. Registro aqui porque a contagem assusta e a explicação não e
> óbvia para quem topar com ela depois.

---

## 6. Divergências com dados oficiais

**Reconciliação executada (Sprint A2).** A plataforma calcula a RCL somando os 12 meses
móveis do RREO Anexo 03 e subtraindo as deduções; o ente publica, no RGF Anexo 02, a RCL
que ele próprio apurou. Dois caminhos independentes para o mesmo fato.

**Resultado no escopo do Ceará: 1.982 pares, 1.867 batem à centavo (94,2%), 115 divergem.**
Nenhuma dessas divergências aparecia em lugar nenhum da plataforma.

| Indicador | Ente/Período | Valor da plataforma | Valor oficial | Diferença | Fonte oficial | Causa provável | Status |
|---|---|---:|---:|---:|---|---|---|
| RCL 12m | 2307650 · 2023-Q1 | 1.031.258.960,43 | 152.100.786,24 | **+578,0%** | RGF A02, RCL (IV) | Publicação parcial do ente no RGF — plataforma estável em 4 bimestres | Em investigação |
| RCL 12m | 2305803 · 2023-Q1 | 259.405.765,93 | 166.695.193,71 | +55,6% | RGF A02, RCL (IV) | idem | Em investigação |
| RCL 12m | 2313203 · 2023-Q3 | 26.370.700,15 | 114.690.477,31 | −77,0% | RGF A02, RCL (IV) | Janela de 12 meses incompleta — faltam bimestres do RREO no acervo | Em investigação |
| RCL 12m | 2313500 · 2022-Q3 e 2023-Q1/Q2 | ~153.900.000 | ~236.300.000 | −35% | RGF A02, RCL (IV) | idem | Em investigação |
| RCL 12m | 2300150 · 2022-Q2, 2023-Q1 | **0,00** | 57.301.035,70 / 59.120.515,50 | −100% | RGF A02, RCL (IV) | **Defeito nosso**: o ente não entregou o Anexo 03 daquele bimestre e a materialização gravou zero em vez de não gravar | **Confirmado** |
| *(mais 108 divergências)* | | | | | | visíveis no painel `/reconciliacao/rcl_rgf` | Em investigação |

### A11 — Ausência virando zero em `gold.fato_rcl`

**32 de 4.141 linhas (0,77%), em 8 entes**, têm `rcl_12m = 0`. Em `2300150/2022-B4` a lista
de entregas do Anexo 03 pula de B3 para B5: o ente não entregou, e a plataforma materializou
zero. É exatamente o anti-padrão que o CLAUDE.md proíbe, gravado na gold, **no denominador
de quase todo limite da LRF**.

Gravidade: **Crítica**. Situação: ✅ **Corrigido** na Sprint A3. A causa raiz está fechada —
`indicators.calcular_rcl` agora **recusa-se a gravar** quando o Anexo 03 não traz linhas,
devolvendo 404 com saída em vez de materializar zero. As 32 linhas órfãs foram removidas
**depois** de provado que nada dependia delas: os indicadores usavam `base_valor`, que é a
RCL publicada pelo próprio ente no RGF, não a linha zerada. Apagar antes dessa prova teria
sido apagar sem saber o que quebrava.

**Reconciliações já executadas e aprovadas** (evidência registrada em sessões anteriores):

| Verificação | Resultado |
|---|---|
| RCL da CAPAG (Tesouro) × `gold.fato_rcl` — Fortaleza | Idênticos à centavo: R$ 12.539.078.077,42 |
| CAPAG re-lida da fonte × silver (2022–2026) | 27.705 linhas, zero divergências |
| Cronograma SADIPEM: DC + OC × total publicado | Bate à centavo |
| Drill receita/despesa: soma das colunas × mart | Bate à centavo nos dois eixos |
| MSC ↔ DCA (identidade do encerramento) | Exata |

---

## 7. Riscos identificados

| Risco | Probabilidade | Impacto | Mitigação | Situação |
|---|---|---|---|---|
| Gestor decide com base em página cuja cobertura é de 1 ente | Alta | Alto | Declarar cobertura por página (Sprint A1) | ✅ Mitigado |
| Limite aplicado ao DF com esfera errada ou nenhuma | Média | Alto | Corrigir `dim_ente` + teste de invariante | ✅ Mitigado (A1/A3) |
| Metodologia escolhida pela plataforma não é a que o gestor supõe | Alta | Alto | Notas metodológicas (Sprint B1) | ✅ Mitigado |
| Divergência com valor oficial passa despercebida | Média | Alto | Painel de reconciliação (Sprint A2) | ✅ Mitigado |
| Limite declarado e nunca apurado (garantias, operações de crédito) | Alta | Alto | Apuração sobre a RCL Ajustada (Sprint B2) | ✅ Mitigado |
| Cenário "E se?" tratado como previsão | Média | Alto | Premissas com procedência declarada (C1) | ✅ Mitigado |
| **Modelo consumindo dado impossível e devolvendo projeção plausível** | Média | **Crítico** | Saneamento da série antes do treino, com a exclusão declarada na resposta (C1) | ✅ Mitigado |
| **Ingestão parcial passando por completa** | Média | **Crítico** | O A12 mostrou que uma API paginada lida pela metade não deixa rastro: o dado parece íntegro. Cobertura declarada (A1) detecta o sintoma; falta um teste de completude **por fonte**, comparando o que a API diz existir com o que carregamos | ⏳ Aberto |
| Dado publicado só em PDF fora da API (mínimos, Anexos 8/12) | **Certa** | Alto | Nenhuma mitigação por ingestão — o SICONFI não publica. A cobertura declara "1 de 185" e a lacuna deixou de ser invisível | 🚫 Bloqueado na fonte |
| **Existência de recurso de outra organização vazando pelo código de status** | Baixa | **Crítico** | A convenção (404, não 403) já estava certa no código; faltava ser exigida — o teste aceitava os dois. Matriz de isolamento com `== 404` em leitura **e** mutação, cinco famílias de recurso (E1) | ✅ Mitigado |
| **Licença conferida só na leitura fiscal, não na Central de Dados** | Média | Alto | `GET /ingestao/data` exigia apenas `administrar`: uma conta licenciada para um município lia o silver de qualquer um dos 5.598. Gate de escopo/licença no roteador e no serviço (A22/E1) | ✅ Mitigado |
| **Custo por requisição que cresce com o tamanho do cliente** | Alta | Médio | O gate de escopo fazia N+1 em `dim_ente` e o `/carteira/refresh` percorria o escopo no request — os dois pioram exatamente no cliente que paga mais. Consulta em lote + cache de sessão, e refresh como job durável, com orçamento de consultas preso por teste (A27/E1) | ✅ Mitigado |

---

## 8. Arquitetura atual

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic (38 migrations), Postgres 16.
  Schemas `bronze` → `silver` → `gold` (analítico) e `op` (operacional, com RLS).
  Camadas `router → service → repository`. Workers RQ.
- **Frontend:** Vite + React + TypeScript. `services/backend.ts` (um fetcher por endpoint),
  `AppContext` (sessão, ente, período) e `useResource`.
- **Ingestão:** `BaseConnector` com ciclo `discover → extract → to_bronze → to_silver →
  mark_done`, idempotente por `(fonte, ente, período, versão)`. 20 fontes.
- **Produção:** EC2 (Ubuntu, m7i-flex.large), Docker Compose, nginx, HTTPS via sslip.io,
  acesso administrativo por AWS SSM (sem porta 22 necessária).

---

## 9. Plano de sprints

Prioridade conforme a ordem pedida: correção dos dados → fórmulas e regras → rastreabilidade
→ divergências → segurança → perda de dados → funcionalidades incompletas → UX → visual.

| Sprint | Objetivo | Agentes | Dependências | Status |
|---|---|---|---|---|
| **A0** | Diagnóstico completo e este documento | fiscal, dados, UX, arquitetura | — | ✅ **Concluído na A0R** — as três frentes interrompidas (P2, P3, P4) têm relatório em §5.1.2, com status individual e 6 achados novos (A22–A27). Resta a execução contra o banco, listada em §20 |
| **A0R** | Retomada das frentes interrompidas: fiscal/contábil, dados/rastreabilidade, arquitetura/segurança | fiscal, dados, arquitetura | A0 | ✅ **Concluída** (diagnóstico) — §5.1.2; P5 rediagnosticada; ficha da E1 escrita; catracas em `tests/test_auditoria_a0r.py`. **Nenhuma correção aplicada, por desenho** |
| **B0** | Correções de sentido já verificadas (U1, U2) | UX | A0 | ✅ **Concluída** |
| **A3a** | Invariante da esfera verificada por dado (A1) | fiscal, testes | A0 | ✅ **Concluída** |
| **A3b** | Completude do catálogo de entes (A9, A10) | dados, testes | A3a | ✅ **Concluída** |
| **A1** | Cobertura honesta: cada página declara para quantos entes/períodos responde | dados, UX | A0 | ✅ **Concluída** |
| **A2** | Reconciliação com fonte oficial: painel de divergências | fiscal, dados | A0 | ✅ **Concluída** (motor + endpoint; triagem persistida fica para A2b) |
| **A3** | Invariantes do domínio verificadas por dado (esfera, denominador, faixa) | fiscal, testes | A0 | ✅ **Concluída** — 7 invariantes, 0 violadas |
| **A4** | Fechar as lacunas de ingestão (mínimos, MSC, SIOPS/SIOPE, FPM) | dados | A1 | ⚠️ **Parcialmente concluída** — FPM/transferências corrigidos (11→185); mínimos **bloqueados na fonte**; MSC/SIOPS/SIOPE têm conector pronto e `--dry-run` medido (A4_MSC/A4_SIOPS, em produção) — carga nacional ao vivo (~242h/5,2M chamadas) segue como decisão humana separada |
| **A5** | **A14 + A15 + A21** — eleger a versão vigente (transferências, RGF republicado, alertas órfãos): a mesma família, fechada de uma vez | dados, fiscal | A4 | ✅ **Concluída** — A14/A21 fechados desde a primeira rodada; A15 corrigido no cálculo on-read **e** o `gold.mart_indicador` reprocessado nacionalmente (autorizado pelo usuário), com 0 divergência residual real confirmada e suíte completa verde. Achado adicional: o Anexo 01 do RGF (pessoal) não tem como ser corrigido por este mecanismo — ver ficha |
| **B1** | Clareza conceitual: notas metodológicas, glossário, ano-base × publicação | UX, fiscal | A0 | ✅ **Concluída** — 10 achados de leitura invertida fechados; `NotaMetodologica` e `Termo` como peças reusáveis |
| **B2** | Limites ausentes: garantias e operações de crédito | fiscal | A3 | ✅ **Concluída** — 3.866 indicadores, 178 entes; achou 1 ente com o teto de operações de crédito **excedido** |
| **C1** | Previsões e cenários "E se?" em nível governamental | previsão, fiscal, UX | B1 | ✅ **Concluída** — premissas ancoradas no observado (a Selic de fábrica estava 3,8 p.p. fora), conversão composta, saneamento da série e **espaço fiscal em reais**; achou o **A15** |
| **C2** | Cenários salvos: persistência, versão, comparação, exportação | previsão, arquitetura | C1 | ✅ **Concluída** — versão em vez de sobrescrita, procedência do dado gravada, reabertura que compara guardado × hoje, comparação por interseção e exportação com premissas junto (migration 0039) |
| **A6** | Regressões de leitura crítica: medidor de piso do cockpit, indicador gerencial em Limites, período do explicador de pessoal (**A16–A18**) | UX, fiscal | B0, B2 | ✅ **Concluída** — A16/A17/A18 corrigidos, com teste de regressão para os três; `make lint && make test` e `npm run test` verdes |
| **F1** | `as_of` e memória de cálculo visíveis em toda tela (fechar a regra R3 onde ainda falta) | dados, UX | B1 | ✅ **Concluída** — `as_of` propagado a Receita/Despesa/Linha Bruta/Pessoal/Resultado/Patrimônio/Limites/Cockpit; padrão de "pin" replicado nos sub-cards; `make lint && make mypy && make test` (backend) e `eslint` + `tsc --noEmit` + `vitest` (frontend, 229 testes) verdes, verificados diretamente (não por relato) |
| **F2** | Legendas: fechar o que a B1 não terminou (RPPS no ponto de leitura, CAPAG, semântica de piso, hierarquias) | UX, fiscal | B1 | ✅ **Concluída** — 15 de 16 achados fechados (U19–U34); U29/U30 (FUNDEB) revisado — achado de cálculo, não de rótulo, documentado sem correção nesta sprint |
| **G1** | Cenários "E se?" em padrão de memorando técnico: RBAC morto (**A19**), `crescimento_rcl_pct` no-op (**A20**), parâmetros fiscais reais, CRUD completo | previsão, fiscal, RBAC | C2 | ✅ **Concluída** — "editar" adicionada ao enum RBAC (migration 0040); `crescimento_rcl_pct` propagado ao ramo PCT_RCL; FUNDEB/reajuste de folha/contrato de dívida como parâmetros próprios; duplicar/excluir definitivamente/exportar comparação/`criado_por`/seletor de modelo; alerta preditivo leva `?indicador=` |
| **D1** | Drill-down por órgão, fonte de recurso, programa/ação | dados, UX | A4 | ✅ **Concluída e em produção** — as 7 tarefas da ficha (§12.3); glossário PCASP, painel de Limites, cartão de garantias/OC, deep-links, crosslinks do Cockpit/Carteira |
| **H1** | Governança: billing zerado, auditoria de RBAC, control plane sem auditoria própria, licença invisível ao tenant | arquitetura, billing | A0 | ✅ **Concluída e em produção** — billing real, auditoria de RBAC/control plane, licença visível ao tenant |
| **B3** | Funcionalidades construídas e nunca ligadas: impressão, links de relatório, gráficos acessíveis, sinalização do Assistente | UX | B1 | ✅ **Concluída e em produção** — impressão, `AccessibleChart`, seletor de período, sinalização do Assistente |
| **E1** | Segurança, isolamento entre organizações e desempenho | arquitetura, segurança | A0R | ✅ **Concluída e verificada** (ruff/mypy/pytest e eslint/tsc/vitest 228/228, todos verdes) — A22, A25, A26, A27, a asserção frouxa de 404 × 403 e a materialização síncrona do `/carteira/refresh` fechados; migration `0041`; baseline em `docs/baseline_desempenho_e1.md` |

*(Cada sprint recebe ficha detalhada — objetivo, problema, justificativa, páginas afetadas,
tarefas, riscos, critérios de aceite, testes, evidências — quando entra em execução.)*

---

## 12. Segunda rodada — auditoria de profundidade, rastreabilidade e robustez (2026-08-04)

> **Método distinto do resto deste documento — leia antes de agir sobre qualquer achado
> abaixo.** As seções 1–11 foram construídas verificando contra o banco (consulta direta,
> reconciliação executada, teste de regressão). Esta rodada usou **8 frentes paralelas de
> leitura de código** (uma por grupo de páginas), cruzando com o que já estava registrado
> aqui para não redescobrir nada — mas **sem consulta ao banco de desenvolvimento ou
> produção**. Trate os achados abaixo como *"localizados no código, com evidência de
> arquivo:linha"*, não como *"confirmados por dado"*, até que a ficha da sprint
> correspondente rode a verificação. Onde um achado já tratado (U1, U2, o seletor-demo
> SP/Fortaleza, o defeito do Anexo 01) foi só confirmado como fechado, não reaparece abaixo.
>
> Escopo: as 23 páginas, agrupadas em 8 frentes (Receita/Despesa; Pessoal/Dívida;
> Resultado/Caixa/Saúde&Educação; Patrimônio/Limites/Benchmarking; Cockpit/Carteira;
> Previsões/Alertas/Relatórios; Assistente/Central de Dados; Admin/Plataforma/Perfil).

### 12.1 Novos achados críticos (mesma régua de A1–A15)

| # | Achado | Página(s) | Evidência | Gravidade |
|---|---|---|---|---|
| A16 | Medidor de piso do Cockpit dobra a margem de tolerância: `RadialMeter` recebe `max = teto*1.1` como parâmetro `piso` de `classifyFloor`, que multiplica de novo por 1,05/1,10 internamente. Um ente exatamente no mínimo de saúde (15,00%, que o backend classifica `adequado`) aparece "Abaixo do mínimo", cor de pior severidade. **Regressão sobre a U1**, que corrigiu o mesmo componente para tetos sem cobrir pisos com o mesmo rigor. | Cockpit | `CockpitPage.tsx:298`, `RadialMeter.tsx:59`, `theme.ts:119-124` | **Crítica** |
| A17 | Indicadores gerenciais sem teto legal (`rcl_per_capita`) renderizam "teto 0%" e o valor em R$ milhões **1.000.000× menor** que o real (Fortaleza: R$ 4.870,66/hab tratado como R$ e dividido por 1e6). `build_limites` devolve todo `mart_indicador` do período sem filtrar por `dim_limite_legal`. | Limites | `LimitesPage.tsx:104,140`, `limits/service.py:139-167`, `indicators/gerenciais.py:125-136` | **Crítica** |
| A18 | O explicador "Pessoal por poder" do Cockpit sempre usa o RGF **mais recente que o ente já teve**, não o correspondente ao período RREO selecionado — abrir um período antigo mistura dois períodos na mesma tela sem aviso. Existe mapeamento B→Q correto em `estadual_service.py:231`, não reaproveitado aqui. | Cockpit | `cockpit_service.py:640-645` | Alta |
| A19 | A capacidade RBAC `"editar"`, exigida por `PATCH`/`DELETE /cenarios/{id}` (renomear/arquivar cenário), **não existe** no enum de capacidades nem passaria o `CheckConstraint` do banco — os dois botões retornam 403 para **todo** usuário, sempre, desde o deploy da Sprint C2. | Previsões | `forecast/router.py:135,148`, `tenancy/models.py:37-44,112` | **Crítica** (funcionalidade da C2 morta) |
| A20 | `crescimento_rcl_pct` é *no-op* nas simulações de Pessoal e Dívida — os dois indicadores com teto mais severo do produto. O gestor muda a premissa de RCL e o resultado simulado não muda. | Previsões | `forecast/service.py:672-785` (ramo `PCT_RCL` não usa `crescimento_rcl_pct`) | **Crítica** |
| A21 | Retificação que corrige um indicador para dentro do limite **não fecha o alerta antigo** — mesma família de A14/A15 (*versão nova chega, vigência não se propaga*), agora do lado dos alertas: `_alertas_limite` só grava quando a faixa continua não-nula, nunca limpa a que ficou órfã. | Alertas | `engine.py:313-358` | Alta |

Recomendo A21 **junto da A5** (mesma causa raiz, mesma correção de "eleger o vigente e
reavaliar"), não como sprint separada — ver ficha em §12.3.

### 12.2 Novos achados de clareza e legenda (continuando U1–U18)

| # | Achado | Página | Evidência |
|---|---|---|---|
| U19 | Receita anuncia hierarquia de 5 níveis (Categoria→Origem→Espécie→Rubrica→Alínea); a árvore só deriva 3. Despesa mostra 4 níveis no cabeçalho fixo, mas o próprio backend já expõe o rótulo certo (2 níveis) numa Memória que o cabeçalho não usa. | Receita, Despesa | `ReceitaPage.tsx:193`, `natureza.py:6-13`; `DespesaPage.tsx:168`, `classificacao.py:223-226` |
| U20 | "Receita arrecadada" não diz bruto × líquido de deduções; a medida `deducoes` é materializada e nunca chega à tela. | Receita | `natureza.py:29`, `ReceitaPage.tsx:206-220` |
| U21/U22 | Transferências correntes/capital fundidas numa barra só; `SeloCobertura` existe em Receita e falta em Despesa (páginas irmãs, tratamento inconsistente). | Receita, Despesa | `ReceitaPage.tsx:150-169`; ausência confirmada em `DespesaPage.tsx` |
| U23 | Cabeçalho/série presos ao eixo função mesmo com a árvore navegando em natureza, sem aviso do descompasso. | Despesa | `DespesaPage.tsx:52` |
| U25 | Cabeçalho de Pessoal não diz se a cadência é quadrimestral ou semestral (porte < 50 mil hab.) — o cálculo já trata isso certo, só o rótulo não. | Pessoal | `PessoalPage.tsx:78` |
| U26 | O card CAPAG já resolveu o achado-semente do ano-base (B1: "classificação de {ano} · dados de {ano-1}") — mas "Metodologia" ainda mistura **três grandezas diferentes** sob um rótulo único: ano-base real da planilha (quando o layout traz `Ano_Base`), ICF (índice de qualidade da informação, layout oficial) e versão de metodologia (layout estadual). Nenhuma validação cruza o `Ano_Base` real contra o `ano_ref-1` calculado. | Dívida | `DividaPage.tsx:316-318`, `capag.py:268-269,303,369-375` |
| U27 | O resultado primário/nominal ainda não diz "(com RPPS)"/"(sem RPPS)" no número principal — a `NotaRpps` da B1 existe, mas fica num card de Reconciliação recolhido por padrão, longe do `MetricHeader` que ela explica. A memória de cálculo também não verbaliza o regime. | Resultado | `ResultadoPage.tsx:90-115,154,458-460` |
| U28 | `meta_nominal`/`realizado_nominal` existem no schema e nunca aparecem — se o ente publica só meta nominal, a tela mostra "Meta de resultado primário —", parecendo ausência total quando há meta cadastrada. | Resultado | `result/schemas.py:56-65`, `ResultadoPage.tsx:240-244` |
| U29/U30 | `Art42Panel` calcula o quadrimestre avaliado e não o exibe; FUNDEB não repete a nota de expurgo de RPNP sem lastro que a árvore MDE/ASPS já traz. | Caixa, Saúde&Educação | `CaixaPage.tsx:397-444`; `SaudeEducacaoPage.tsx:664-699` |
| U33 | "✓ Conciliado" (Patrimônio) aparece para entes **sem MSC nenhuma** — só 1 de 3 checks roda, mas o rótulo e a `observacao` fixa descrevem os 3 como se todos tivessem rodado. | Patrimônio | `PatrimonioPage.tsx:224-230`, `accounting/service.py:913-918` |
| U31/U34 | Sem `SeloCobertura`/`SeloQualidadePagina` em Limites e Benchmarking, apesar de `INDICADORES_POR_PAGINA` já registrar as duas — justamente a página mais sensível a "faltou apurar" (Limites) e a de coorte mais rala (Benchmarking, Nordeste ~10%). | Limites, Benchmarking | `coverage/service.py:50-60` |
| U32 | Barra de progresso de Limites não inverte a direção visual para pisos — só o texto distingue "piso" de "teto". | Limites | `LimitesPage.tsx:106` |

*(Achados menores — cadência de teste sem esfera estadual, célula ambígua na conferência da
Linha Bruta, casamento de número por string exata no Assistente, seletor de período inerte,
etc. — viraram tarefas dentro das fichas abaixo, sem número próprio.)*

### 12.3 Fichas de sprint

#### Sprint A5 — Eleger a versão vigente (A14 + A15 + A21): a mesma família, fechada de uma vez

**Objetivo:** impedir que uma versão de entrega superada continue sendo somada/lida junto
da vigente, nas três frentes já provadas: transferências (A14), RGF republicado (A15) e
alertas não reavaliados (A21).

**Problema:** `silver.tesouro_fpm`, `silver.fnde_fundeb_repasse` e
`silver.transferencia_generica` guardam `versao_entrega` sem coluna de vigência —
`forecast/series.py::_fpm_periodo` e 3 leitores de `revenue/repository.py` somam todas as
versões (Fortaleza: R$ 3.095,00 mi de FPM 2024 lido onde o real é R$ 1.547,50 mi; 185/185
entes com versão duplicada em 2025). O RGF republica quadrimestres anteriores a cada nova
entrega — é assim que a retificação chega — e a materialização usa o primeiro valor, nunca
revisita (63 quadrimestres divergentes >2%, 5 com o dobro ou mais; o caso extremo produziu
324,49% da RCL em pessoal). O motor de alertas nunca reavalia um alerta de limite quando a
entrega que o originou é superada por uma retificação que resolve.

**Justificativa:** são números que alimentam a previsão, a conciliação de receita e a fila
de alertas — o núcleo da promessa de "não pode haver erro". Já era a próxima sprint do
plano (§9) antes desta rodada.

**Páginas afetadas:** Previsões (exógena FPM), Receita (conciliação), Pessoal/Limites (RCL
como denominador), Alertas (fila).

**Tarefas:**
- Coluna de vigência (ou eleição por `(fonte, ente, período)` mais recente via
  `bronze.raw_payload_tesouro_fpm.ingerido_em`) nas três tabelas de transferência; migration
  sem apagar histórico.
- Filtrar por vigência em `_fpm_periodo` e nos 3 leitores de `revenue/repository.py`.
- RGF: ao materializar um período, reconsultar as entregas subsequentes que o republicam e
  usar o valor mais recente publicado para aquele quadrimestre.
- Alertas: em `_alertas_limite`, se a faixa do indicador voltou a `adequado`, fechar/expirar
  o alerta órfão.
- Reprocessar o mart afetado (RCL, pessoal) para os 63 quadrimestres da A15.

**Riscos:** reprocessamento pode mudar números já publicados/exportados — avisar quem já
exportou; script idempotente (padrão de `materialize_endividamento.py`, B2).

**Critérios de aceite:** Fortaleza FPM 2024 = R$ 1.547,50 mi em previsão e conciliação; as
63 divergências de RGF passam a usar o valor republicado; alerta de indicador hoje
`adequado` aparece fechado/expirado.

**Testes:** completude por fonte/ano (formaliza o que revelou a A14); republicação de RGF
(entrega N+1 corrige N); reavaliação de alerta (retificação que resolve fecha o alerta).

**Evidências:** consulta antes/depois em `gold.mart_indicador`/`silver.tesouro_fpm` para
Fortaleza 2024 e os 5 entes da tabela de A15; captura da fila de alertas antes/depois.

**Prompt Claude Code:**
```
Implemente a Sprint A5 de backend_plataforma_fiscal/docs/evolucao_plataforma.md — família
A14+A15+A21 ("versão que existe, vigência que não se declara"), a próxima sprint crítica
do plano (§9).

1) Transferências (A14): silver.tesouro_fpm, silver.fnde_fundeb_repasse,
silver.transferencia_generica não têm coluna de vigência. Adicione (migration reversível) e
eleja a vigente por (fonte, ente, período) usando bronze.raw_payload_tesouro_fpm.ingerido_em
como critério de recência — nunca apague histórico. Filtre por vigência em
forecast/series.py::_fpm_periodo e nos 3 leitores de revenue/repository.py que hoje somam
todas as versões. Prove com Fortaleza FPM 2024: hoje lê R$ 3.095,00 mi, deve ler
R$ 1.547,50 mi.

2) RGF republicado (A15): o RGF republica os quadrimestres anteriores a cada entrega nova —
é a forma como a retificação chega, e a materialização usa só o primeiro valor. Ao
materializar RCL/pessoal, use o valor mais recente publicado para cada quadrimestre entre as
entregas disponíveis. Reprocesse os 63 quadrimestres já identificados como divergentes
(>2%) — 5 deles com o dobro ou mais do valor correto. **Correção registrada durante a
implementação (ver §11, entrada da A5):** o sentido abaixo estava invertido nesta ficha — o
valor da 1ª entrega é o preso/desatualizado, e o republicado é o vigente (regra do CLAUDE.md:
"retificação supera a versão anterior"). O caso 2307650/2023 deve passar de **R$ 152,1 mi**
(1ª entrega, presa) **para R$ 1.031,3 mi / R$ 1.022,4 mi** (republicado, vigente) de RCL
Ajustada no 1º quadrimestre — não o contrário.

3) Alertas órfãos (A21, achado desta rodada): engine.py::_alertas_limite nunca fecha um
alerta cuja faixa voltou a "adequado" após retificação. Adicione a reavaliação: se o
indicador que originou o alerta não está mais em faixa não-nula, feche/expire o registro em
vez de deixá-lo ativo indefinidamente.

Testes obrigatórios: completude por fonte/ano (formalizar o que revelou a A14);
republicação de RGF (entrega N+1 corrige N, materialização usa o corrigido, com caso de
regressão para 2307650/2023); reavaliação de alerta (retificação que resolve fecha o
registro). Script de reprocessamento idempotente, no padrão de materialize_endividamento.py
(B2) — reexecutar não deve duplicar nem falhar em silêncio. make lint && make test.
```

---

#### Sprint A6 — Regressões de leitura crítica no Cockpit e em Limites (A16, A17, A18)

**Objetivo:** corrigir três casos em que a tela afirma o oposto (ou uma ordem de grandeza
errada) do que o dado diz — a mesma classe de defeito que a U1 já corrigiu uma vez, reaberta
em três lugares novos.

**Problema:** (A16) `RadialMeter` recebe `max=teto*1.1` como parâmetro `piso` de
`classifyFloor`, que multiplica de novo internamente — o limiar efetivo vira 110% do mínimo
legal, não 100%; um ente exatamente adequado aparece "Abaixo do mínimo". (A17)
`build_limites` lista todo `mart_indicador` do período sem filtrar por `dim_limite_legal` —
indicadores gerenciais herdam formatação de limite legal e de moeda absoluta (÷1e6),
produzindo um valor 1.000.000× menor. (A18) o explicador de pessoal do cockpit sempre usa o
RGF mais recente do ente, não o correspondente ao período RREO selecionado.

**Justificativa:** são exatamente os números que um secretário lê primeiro (o velocímetro
do cockpit, a lista de limites) — errar a leitura de conformidade é o pior tipo de erro que
a plataforma pode cometer.

**Páginas afetadas:** Cockpit, Limites.

**Tarefas:**
- `classifyFloor` recebe o piso real (`teto`, não `max` pré-multiplicado), sem remultiplicar
  internamente; teste de regressão para "exatamente no mínimo".
- `build_limites`: filtrar para indicadores com `dim_limite_legal`; gerenciais saem desta
  lista ou ganham formatação `brl_per_capita` própria.
- `cockpit_service.py`: mapear o RGF do mesmo ciclo do período RREO selecionado (reusar o
  mapeamento B→Q de `estadual_service.py:231`).

**Riscos:** mudar `classifyFloor` afeta outros consumidores (Saúde&Educação, Benchmarking)
— checar todos antes de alterar a assinatura.

**Critérios de aceite:** ente com saúde exatamente em 15,00% aparece "adequado" no cockpit,
igual à `/saude-educacao`; `rcl_per_capita` sai da lista de Limites (ou aparece em R$/hab
sem "teto 0%"); explicador de pessoal em `2024-B2` mostra o RGF de `2024-Q2`.

**Testes:** `piso-vs-teto.test.tsx` ganha o caso "exatamente no piso"; teste de
`build_limites` sem indicador sem `dim_limite_legal`; teste do explicador com período
histórico.

**Evidências:** captura do cockpit antes/depois para o mesmo ente/indicador; diff de
`GET /limites` (contagem de itens) antes/depois.

**Prompt Claude Code:**
```
Implemente a Sprint A6 de backend_plataforma_fiscal/docs/evolucao_plataforma.md — três
regressões de leitura crítica achadas na segunda rodada de auditoria (§12.1, A16/A17/A18).

A16 — Cockpit/RadialMeter.tsx:59 recebe max=teto*1.1 (CockpitPage.tsx:298) como parâmetro
"piso" de classifyFloor, que multiplica de novo por 1.05/1.10 em theme.ts:119-124. Um ente
com saúde exatamente em 15,00% (que o backend classifica "adequado") aparece "Abaixo do
mínimo" no cockpit. Corrija para que classifyFloor receba o piso real sem remultiplicação;
adicione o caso "exatamente no mínimo" ao teste piso-vs-teto.test.tsx. Confira todos os
consumidores de classifyFloor antes de mudar a assinatura.

A17 — limits/service.py::build_limites (linhas 139-167) devolve todo gold.mart_indicador do
período sem filtrar por dim_limite_legal. Indicadores gerenciais como rcl_per_capita (R$/hab,
sem teto legal) herdam formatação de limite (LimitesPage.tsx:104,140): "teto 0%" e valor
dividido por 1e6 (R$ 4.870,66/hab vira "R$ 0,0 M"). Filtre build_limites para indicadores com
dim_limite_legal associado; para os gerenciais que devam continuar na tela, aplique a
formatação per-capita que o Benchmarking já usa corretamente (formatBenchmarkValue).

A18 — cockpit_service.py:640-645 usa sempre periodo_util.mais_recente(entregas_rgf) para o
explicador de pessoal, ignorando o período RREO selecionado. Reaproveite o mapeamento
bimestre→quadrimestre já correto em estadual_service.py:231 para buscar o RGF do mesmo ciclo
do período RREO selecionado.

Testes: regressão de piso no cockpit; build_limites sem indicador gerencial (ou formatado
certo); explicador de pessoal com período histórico retornando o RGF do ciclo correto.
make lint && make test; npm run test.
```

---

#### Sprint F1 — `as_of` e memória de cálculo visíveis em toda tela

**Objetivo:** toda página fiscal deve poder mostrar "como era" (`as_of`) e a memória de
cálculo do número que exibe — hoje várias já aceitam `as_of` no backend e nunca o
devolvem/consomem no frontend.

**Problema:** Receita, Despesa, Linha Bruta, Pessoal, Resultado (5 endpoints), Patrimônio
(Explorador/Matriz), Limites (`GET /limites`) e Cockpit (as 7 camadas) não carregam `as_of`
no schema de resposta e/ou não enviam o parâmetro no fetcher, mesmo o router aceitando — ao
contrário de Dívida, que já tem o padrão certo (schemas com `as_of`, sub-cards "pinados" no
mesmo `as_of` do cabeçalho via `DividaPage.tsx:140-161`). Caixa tem o padrão certo em 2 de 3
cards, mas não no herói.

**Justificativa:** é a regra R3 do CLAUDE.md ("o frontend exibe a proveniência") — hoje só
Dívida a cumpre de ponta a ponta.

**Páginas afetadas:** Receita, Despesa, Linha Bruta, Pessoal, Resultado, Caixa, Patrimônio,
Limites, Cockpit.

**Tarefas:**
- Adicionar `as_of: datetime | None` aos schemas que não têm; ecoar o parâmetro de query já
  aceito.
- Nos fetchers (`backend.ts`), enviar `as_of` e propagá-lo aos sub-cards de cada página
  (padrão "pin" de Dívida).
- Reusar o seletor "ver como era" (de Dívida/Pessoal) nas páginas que ainda não o têm.
- `GET /limites`: aceitar `as_of` (hoje só o detalhe aceita).

**Riscos:** mudança de contrato em ~8 schemas — manter o campo opcional, checar consumidores
TS.

**Critérios de aceite:** toda página fiscal lista tem `FonteChip`/`AuditLine` mostrando
`as_of` quando presente; abrir uma retificação passada reproduz "como era" em cada página,
com sub-cards pinados na mesma versão.

**Testes:** contrato por endpoint (`as_of` no schema); reprodução histórica por página
(estender o padrão já usado em Dívida/Pessoal); teste de "pin" (sub-cards não divergem de
versão).

**Evidências:** lista de endpoints antes/depois com presença de `as_of`; capturas do
seletor "ver como era" em 3 páginas novas.

**Prompt Claude Code:**
```
Implemente a Sprint F1 de backend_plataforma_fiscal/docs/evolucao_plataforma.md — fechar a
regra R3 (bitemporalidade visível) nas páginas que ainda não a cumprem, achado consolidado
da segunda rodada (§12).

Backend: adicione as_of: datetime | None aos schemas que não têm (revenue:
ReceitaDetalhe/similares; expense: DespesaDetalhe/EstagiosOut; personnel:
PessoalDetalhe/MemoriaPessoal/PorPoderOut; result: ResultadoDetalhe/CascataOut/
ReconciliacaoOut/MetaOut/MemoriaResultado; accounting: nós de drill/matriz; limits:
GET /limites, que hoje só aceita as_of no detalhe). Todos os routers já aceitam o parâmetro
de query — só falta ecoar no schema de resposta.

Frontend: em cada fetcher afetado (services/backend.ts), envie as_of quando presente. Nas
páginas com múltiplos sub-cards (Pessoal: PorPoderCard/ArvoreDrill/MemoriaPessoalDialog;
Patrimônio: Explorador/Matriz; Cockpit: as 7 camadas), replique o padrão de "pin" já usado em
DividaPage.tsx:140-161 — todos os sub-cards fixados no as_of resolvido pelo cabeçalho, para
que uma retificação no meio do carregamento não misture versões na mesma tela. Adicione/reuse
o seletor "ver como era" (mesmo componente de Dívida/Pessoal) nas páginas que ainda não o
têm. Em Caixa, o FonteChip do herói da matriz de suficiência também precisa receber asOf (os
outros 2 cards da mesma página já o fazem — CaixaPage.tsx:320,356).

Testes: contrato (as_of presente em cada schema listado); reprodução histórica por página
(adaptar o padrão já usado em test_debt.py/test_personnel.py); teste de "pin" garantindo que
sub-cards da mesma página não divergem de versão. make lint && make test; npm run test.
```

---

#### Sprint F2 — Legendas: fechar o que a B1 não terminou

**Objetivo:** fechar os residuais de clareza que a B1 não cobriu — RPPS no ponto de leitura,
ano-base×ICF da CAPAG, semântica de piso em Limites, e um conjunto de rótulos que ainda
divergem do dado.

**Problema:** achados U19–U34 (§12.2) — resumidamente: hierarquia de Receita/Despesa
anunciada não bate com a entregue; deduções e transferências de capital escondidas; RPPS
ainda só explicado numa nota recolhida, não no número principal; meta nominal descartada
quando existe; CAPAG mistura 3 grandezas num rótulo "Metodologia"; "conciliado" aparece para
entes sem MSC nenhuma; barra de piso/teto usa a mesma direção visual; `SeloCobertura`
ausente em Despesa/Limites/Benchmarking.

**Justificativa:** é a classe de achado que a B1 já provou valer a pena (dez rótulos
corrigidos sem mudar nenhum número) — cobre os dois exemplos que o dono do produto deu nesta
rodada (RPPS no Resultado, ano-base da CAPAG).

**Páginas afetadas:** Receita, Despesa, Resultado, Dívida, Patrimônio, Limites,
Benchmarking, Pessoal, Caixa, Saúde&Educação.

**Tarefas** (uma por achado de §12.2): corrigir texto de hierarquia (Receita/Despesa);
expor bruto×deduções na Receita; desdobrar transferência corrente×capital; mover
"(com RPPS)"/"(sem RPPS)" para o `MetricHeader` do Resultado e para as fórmulas da memória;
exibir `meta_nominal`/`realizado_nominal` quando presentes; separar `ano_base_fonte` de
`metodologia_versao`/ICF na CAPAG com validação cruzada; rótulo condicional em Patrimônio
quando `tem_msc=false`; inverter a barra de progresso para pisos em Limites; adicionar
`SeloCobertura`/`SeloQualidadePagina` em Despesa/Limites/Benchmarking; rotular cadência RGF
em Pessoal; exibir o quadrimestre avaliado no `Art42Panel`; replicar nota de expurgo de RPNP
no card FUNDEB.

**Riscos:** nenhum — mudança de rótulo/campo exibido, sem alterar cálculo (disciplina da
B1: "não trocou nenhum número").

**Critérios de aceite:** cada achado de §12.2 tem teste de regressão de UI correspondente;
nenhum teste de cálculo muda de valor esperado.

**Testes:** estender `clareza-conceitual.test.tsx` com um caso por achado; snapshot dos
rótulos antes/depois.

**Evidências:** tabela antes/depois igual à da B1 (o que o gestor lia × o que o dado dizia
× correção).

**Prompt Claude Code:**
```
Implemente a Sprint F2 de backend_plataforma_fiscal/docs/evolucao_plataforma.md —
continuação direta da B1 ("a plataforma é honesta sobre ausência de dado; ainda não é
honesta sobre significado"), fechando os achados U19-U34 da segunda rodada de auditoria
(§12.2). Mesma disciplina da B1: NENHUM número muda, só rótulo, denominador exibido ou campo
novo para permitir dizer a verdade completa.

- Receita/Despesa: corrija o texto de hierarquia (ReceitaPage.tsx:193, DespesaPage.tsx:168)
  para os níveis realmente derivados (natureza.py, classificacao.py::hierarquia_label);
  exponha bruto×deduções na Receita; desdobre a barra "própria×transferida" em
  corrente×capital.
- Resultado: mova "(com RPPS)"/"(sem RPPS)" para o MetricHeader do primário/nominal (não só
  na NotaMetodologica recolhida); inclua o regime em formula_primario/formula_nominal; exiba
  meta_nominal/realizado_nominal quando result/schemas.py::MetaResumo os trouxer.
- Dívida/CAPAG: separe ano_base_fonte (quando o layout traz Ano_Base) de
  metodologia_versao/ICF — três campos, três rótulos, sem misturar; valide ano_base_fonte
  contra ano_ref-1 quando ambos existirem.
- Patrimônio: rótulo condicional em build_conciliacao quando tem_msc=false ("Balanço fecha"
  em vez de "Conciliação MSC↔DCA"), com observacao descrevendo só os checks que rodaram.
- Limites: inverta a direção visual da barra de progresso para indicadores de piso;
  adicione SeloCobertura/SeloQualidadePagina (já registrado em coverage/service.py, só falta
  consumir).
- Benchmarking, Despesa: mesmo SeloCobertura/SeloQualidadePagina onde falta.
- Pessoal: rotule a cadência RGF (quadrimestral/semestral) no PageHeader.
- Caixa: Art42Panel exibe o quadrimestre avaliado, não só o booleano dentro/fora da janela.
- Saúde&Educação: CardFundeb replica a nota de expurgo de RPNP sem lastro que a árvore
  MDE/ASPS já tem.

Testes: estenda clareza-conceitual.test.tsx com um caso de regressão por achado acima.
Nenhum teste de cálculo deve mudar de valor esperado — se mudar, o achado era de cálculo,
não de rótulo, e pertence a outra sprint. npm run test; make test.
```

---

#### Sprint G1 — Cenários "E se?" em padrão de memorando técnico governamental

**Objetivo:** o pedido explícito desta rodada — tornar o simulador de cenários robusto o
bastante para sustentar uma decisão real de governo, com parâmetros de significado fiscal
completo e CRUD de cenários salvos sem lacunas.

**Problema:** (A19) a capacidade RBAC `"editar"` não existe no enum/constraint — renomear e
arquivar cenário retornam 403 para todo mundo, sempre, desde o deploy da C2. (A20)
`crescimento_rcl_pct` é *no-op* nas simulações de Pessoal e Dívida — exatamente os dois
indicadores com teto mais severo. Além disso: os parâmetros aceitos são só 3 macro
(IPCA/Selic/FPM) + 2 choques genéricos — não há FUNDEB separado de FPM, tributo específico,
novo contrato de dívida estruturado (principal/prazo/carência) ou reajuste de folha;
duplicar cenário e excluir definitivamente não existem; a comparação não exporta/imprime;
`criado_por` é gravado e nunca exposto; o modelo de projeção nunca é escolhido pelo usuário;
o aviso legal calculado (`memoria.observacao_minimos`) é descartado pela tela; o alerta
preditivo não leva ao indicador de origem.

**Justificativa:** é o pedido central desta rodada de auditoria — o simulador precisa sair
de "protótipo" (diagnóstico da própria C1) para algo que resiste a ser citado num memorando
técnico.

**Páginas afetadas:** Previsões.

**Tarefas:**
- RBAC: adicionar a capacidade que falta (ou reusar `"exportar"`) ao enum +
  `CheckConstraint`, com migration; corrigir `require_capability` de renomear/arquivar.
- Propagar `crescimento_rcl_pct` também ao ramo `PCT_RCL` (Pessoal/Dívida) de
  `_impacto_cenario`.
- Novos parâmetros: FUNDEB separado de FPM; simulador estruturado de novo contrato de
  dívida (principal, prazo, carência, taxa) com impacto no teto de 120%/200% RCL; variação
  de folha/admissões distinta do choque genérico de pessoal.
- CRUD: duplicar cenário; exclusão definitiva (distinta de arquivar) com confirmação;
  exportar/imprimir a comparação; expor `criado_por`; seletor de modelo na simulação.
- Renderizar `memoria.observacao_minimos` no `ScenarioPanel`; alerta preditivo passa o
  indicador de origem via query param.

**Riscos:** mudança de enum RBAC é sensível (constraint de banco) — testar em cópia antes;
simulador de dívida estruturado começa com escopo mínimo (impacto no teto, sem persistir
contrato hipotético).

**Critérios de aceite:** renomear/arquivar cenário funciona para papel com a capacidade
certa; simular Pessoal/Dívida com `crescimento_rcl_pct` diferente de zero produz resultado
diferente; duplicar e excluir definitivamente funcionam; comparação exporta; `criado_por`
visível; modelo escolhível; alerta preditivo abre no indicador certo.

**Testes:** RBAC (papel sem a capacidade → 403, com ela → sucesso); ramo `PCT_RCL` de
`_impacto_cenario` com `crescimento_rcl_pct != 0`; CRUD completo em `test_forecast.py`; E2E
simular→salvar→reabrir→comparar→exportar.

**Evidências:** antes/depois do resultado de uma simulação de Pessoal com RCL -10%; captura
da comparação exportada; captura do 403 antes da correção.

**Prompt Claude Code:**
```
Implemente a Sprint G1 de backend_plataforma_fiscal/docs/evolucao_plataforma.md —
robustecer os "Controles de cenário · e se?" de Previsões para padrão de memorando técnico
governamental. É o pedido central da segunda rodada de auditoria (§12), com dois defeitos
críticos (A19, A20) e um conjunto de lacunas de robustez a fechar juntos, porque são a mesma
tela.

A19 — a capacidade RBAC "editar", exigida por PATCH/DELETE /cenarios/{id}
(forecast/router.py:135,148), não existe no enum de tenancy/models.py:37-44 nem passaria o
CheckConstraint (:112) — renomear/arquivar cenário retorna 403 para todo mundo, sempre,
desde o deploy da C2. Adicione a capacidade ao enum (ou reutilize "exportar", que já é
concedida onde faz sentido) com migration, e corrija os dois endpoints.

A20 — crescimento_rcl_pct é no-op nas simulações de Pessoal/Dívida:
forecast/service.py::_impacto_cenario (linhas 768-785) só usa essa premissa no ramo
unidade==BRL; o ramo PCT_RCL (Pessoal, Dívida — os dois indicadores com teto mais severo)
ignora silenciosamente o slider de RCL. Propague o choque também a esse ramo, com teste que
prova resultado diferente para crescimento_rcl_pct != 0.

Robustez do simulador: adicione parâmetro de FUNDEB separado do choque de FPM; um simulador
de novo contrato de dívida (principal, prazo, carência, taxa) com impacto explícito no teto
de 120%/200% RCL, escopo mínimo (calcula o impacto, não precisa persistir o contrato
hipotético); parâmetro de reajuste de folha distinto do choque genérico de pessoal.

CRUD de cenários salvos: duplicar (endpoint + UI); excluir definitivamente, distinto de
arquivar, com confirmação; exportar/imprimir a comparação de cenários (reuse ExportButton já
usado na comparação de modelos); exponha criado_por (já gravado em cenarios.py, nunca
projetado em schema) no VersaoCenario/CenarioDetalhe e na tela; adicione seletor de modelo
na simulação (hoje sempre "o melhor disponível").

Renderize memoria.observacao_minimos no ScenarioPanel (calculado e descartado hoje). O link
de alerta preditivo (engine.py:503, sempre "/previsoes" sem indicador) deve levar
?indicador=X; PrevisoesPage.tsx lê useSearchParams para abrir já no indicador certo.

Testes: RBAC (papel sem a capacidade → 403, com ela → sucesso); ramo PCT_RCL de
_impacto_cenario com crescimento_rcl_pct != 0; CRUD completo (criar/duplicar/editar/
excluir/arquivar) em test_forecast.py; E2E simular→salvar→reabrir→comparar→exportar.
make lint && make test; npm run test && npx playwright test.
```

---

#### Sprint D1 — Drill-down profundo (ficha detalhada, conforme prometido em §9)

**Objetivo:** entregar a ficha que o plano já previa para D1 ("drill-down por órgão, fonte
de recurso, programa/ação"), ampliada com o inventário concreto desta rodada — o maior
número de "candidatas a página/drill nova" de toda a auditoria.

**Problema:** o backend já tem drill/memória/série/simulador prontos e testados para
Limites (`GET /limites/{indicador}`, `POST /limites/{indicador}/simular`) sem nenhum
consumidor no frontend. A MSC desce até a conta PCASP folha, mas 90%+ das folhas mostram só
código sem nome (a MSC do SICONFI não publica descrição por conta). Garantias e Operações de
Crédito não têm cartão de posição vigente — só aparecem dentro do simulador, com base
digitada manualmente. A Central de Dados tem lineage arquitetural (por tipo de nó), não por
valor específico, e a aba Lineage não lê `?no=` da URL. O Cockpit não linka nenhum card
(exceto Riscos) para a página de detalhe. A Carteira perde o indicador selecionado e não tem
"voltar ao ranking" ao abrir um ente.

**Justificativa:** em quase todo módulo, o backend já sustenta o próximo nível de
profundidade — o gargalo é consumo no frontend, não cálculo novo. É o maior ganho por
esforço da auditoria.

**Páginas afetadas:** Limites, Patrimônio, Dívida, Central de Dados, Cockpit, Carteira,
Relatórios, Alertas.

**Tarefas:**
- Painel expansível por linha em `/limites` consumindo `GET /limites/{indicador}` e
  `POST /limites/{indicador}/simular`.
- Glossário PCASP estático (~500 contas nível 6-7, portaria STN pública) como drawer no
  Explorador MSC.
- Cartão de posição vigente de Garantias e Operações de Crédito na Dívida.
- Deep-link universal `?painel=lineage&no=` e `?painel=qualidade&ente=&periodo=` na Central
  de Dados, consumido a partir dos cards de indicador/`FonteChip` e do
  `SeloQualidade`.
- Lineage por valor específico (`ente+período+indicador → job → bronze → versão →
  checks`), acionável a partir do `FatoRow` do Assistente.
- Crosslinks do Cockpit (Críticos, Tendências, Explicadores) para as páginas de detalhe.
- Carteira: "voltar ao ranking de {indicador} — UF {sigla}" e preservação do indicador
  selecionado ao trocar de ente.
- Ligar as páginas fiscais como emissoras do deep-link `?modelo=` que `RelatoriosPage` já lê.
- Candidatas a página nova (se o volume justificar sprint própria): Transferências (lista
  completa, série mensal); execução por função ao longo do tempo; CDP como linha do tempo.

**Riscos:** glossário PCASP estático precisa de manutenção quando a portaria mudar —
documentar fonte e data da versão usada.

**Critérios de aceite:** `/limites` abre memória/série/simulador sem sair da página; MSC
mostra nome oficial nas folhas; Dívida mostra % vigente de garantias/operações de crédito
sem exigir simulação; um clique a partir de qualquer selo/indicador leva à Central de Dados
já filtrada; Cockpit e Carteira navegam sem perder contexto.

**Testes:** contrato do painel expansível de Limites; snapshot do glossário PCASP; E2E de
navegação Cockpit→detalhe→volta e Carteira→ente→volta preservando indicador.

**Evidências:** antes/depois de 3 telas (Limites expandido, MSC com nomes, Dívida com
cartão de garantias); vídeo do fluxo de navegação sem perda de contexto.

**Prompt Claude Code:**
```
Implemente a Sprint D1 de backend_plataforma_fiscal/docs/evolucao_plataforma.md — ficha
detalhada do drill-down profundo já previsto no plano (§9), ampliada com o inventário da
segunda rodada de auditoria (§12) — o maior "ganho por esforço" da auditoria, porque a maior
parte já existe no backend.

1) /limites: crie o painel expansível por linha consumindo GET /limites/{indicador} e POST
   /limites/{indicador}/simular — já testados no backend, sem consumidor no frontend hoje
   (backend.ts não tem fetchLimiteDetail/fetchSimularLimite). Memória, série histórica,
   providências (base legal por faixa) e simulador, tudo sem sair da página.
2) Explorador MSC (Patrimônio): dicionário estático de nomes PCASP (portaria STN, ~500
   contas nível 6-7) como drawer, substituindo "código · Subitem" — hoje 90%+ das folhas de
   nível 6-7 não têm nome (a MSC do SICONFI não publica descrição por conta).
3) Dívida: cartão de posição vigente de Garantias e Operações de Crédito (hoje só aparecem
   dentro do simulador, com base digitada manualmente pelo usuário) — busque e exiba o %
   atual sobre a RCL Ajustada.
4) Central de Dados: deep-link universal ?painel=lineage&no= e
   ?painel=qualidade&ente=&periodo= (a aba Lineage hoje começa sempre no mesmo nó fixo,
   CentralDadosPage.tsx:1138, e nada linka para lá com parâmetro). Consuma esse deep-link a
   partir dos cards de indicador/FonteChip das páginas fiscais e do link "ver a conta que
   não fechou" do SeloQualidade.tsx. Se o tempo permitir, avance lineage por instância
   (ente+período+indicador → job → bronze → versão → checks), não só por tipo de nó.
5) Cockpit: crosslink de Críticos/Tendências/Explicadores para /limites?indicador=,
   /receita, /despesa, /pessoal, /previsoes (hoje só Riscos linka).
6) Carteira: "voltar ao ranking de {indicador} — UF {sigla}" e preservação do indicador
   selecionado ao abrir um ente a partir do ranking/mapa.
7) Ligue RelatoriosPage.tsx (que já lê ?modelo=, RelatoriosPage.tsx:63-64) como destino de
   um botão "exportar esta análise" nas páginas fiscais — hoje nenhuma emite esse link.

Testes: contrato do painel de Limites; snapshot do glossário PCASP contra uma amostra
conhecida; E2E de navegação Cockpit→detalhe→volta e Carteira→ente→volta sem perder o
indicador selecionado. make lint && make test; npm run test && npx playwright test.
```

---

#### Sprint H1 — Governança: billing, control plane e licença visível

**Objetivo:** consertar a cadeia de billing (hoje sempre R$ 0,00), dar ao control plane sua
própria auditoria, e tornar a licença visível para quem a usa.

**Problema:** `emitir_fatura` sempre calcula `preco=Decimal("0")` porque o único endpoint
que grava `op.assinatura` está com 403 hardcoded e o control plane não tem equivalente —
toda fatura emitida vale zero. Criar usuário, criar papel e alterar a matriz RBAC não gravam
`op.audit_log`. A trilha de auditoria não mostra quem agiu e a UI não expõe os filtros de
usuário/período que o backend já aceita. O superusuário nunca tem `org_id` de sessão e
portanto nunca pode consultar `/admin/auditoria` — e não existe `/platform/auditoria` —
logo nenhuma ação de licenciamento é auditável por ninguém. Badge de licença expirada mostra
"ATIVA". O tenant nunca vê sua própria licença — só descobre por erro ao tentar adicionar um
ente. Cobrança por população conta ente sem população como zero, silenciosamente.

**Justificativa:** achados de governança e integridade comercial — fora do escopo fiscal,
mas dentro do "não pode haver erro" quando o erro é uma fatura errada ou uma ação
administrativa sem rastro.

**Páginas afetadas:** Admin, Plataforma, Perfil.

**Tarefas:** criar `POST/PATCH /platform/orgs/{id}/assinatura` e religar "Emitir fatura" a
um preço real; `insert_audit_log` em `create_user`/`create_papel`/`update_papel_capacidades`;
expor nome/e-mail do ator na trilha (join com `op.usuario`) e os filtros já suportados;
criar `/platform/auditoria` com sessão bypass de RLS; corrigir o badge de vigência; expor
`GET /me/licencas` e um badge de vigência na aba Organização/Perfil; expor o flag
`sem_populacao` na UI da fatura; formulário de provisionamento passa a coletar
`metrica_cobranca` e preço.

**Riscos:** nenhum cálculo fiscal é tocado; risco é só de regressão em RBAC/billing —
estender a suíte de isolamento RLS entre organizações da Sprint 28.

**Critérios de aceite:** fatura com organização/assinatura configurada tem
`valor_total > 0`; ação de RBAC aparece em `op.audit_log` com o nome do ator; superusuário
consulta suas próprias ações; badge mostra "expirada" quando vencida; tenant vê sua licença
sem precisar de um erro para descobrir.

**Testes:** fatura com preço configurado; auditoria de RBAC (3 fluxos); `/platform/auditoria`
isolado por sessão; badge com licença vencida.

**Evidências:** fatura de teste com valor não-zero; entrada de auditoria mostrando quem
alterou um papel; captura do badge "expirada".

**Prompt Claude Code:**
```
Implemente a Sprint H1 de backend_plataforma_fiscal/docs/evolucao_plataforma.md —
governança de billing, auditoria e licenciamento, achados da segunda rodada de auditoria
(§12) nas páginas Admin/Plataforma/Perfil.

1) Billing zerado: emitir_fatura (tenancy/service.py:377-384) sempre usa
   preco=Decimal("0") porque POST /billing/assinatura está com 403 hardcoded
   (admin_router.py:53-64) e modules/platform não tem endpoint equivalente. Crie
   POST/PATCH /platform/orgs/{id}/assinatura no control plane (metrica_cobranca +
   preco_unitario) e religue o botão "Emitir fatura" do AdminPage a um preço real.
2) Auditoria de RBAC ausente: create_user, create_papel e update_papel_capacidades
   (tenancy/service.py) não chamam insert_audit_log, diferente de
   TROCAR_ORGANIZACAO/ALTERAR_SENHA. Adicione, com antes/depois das capacidades no payload.
3) Trilha de auditoria sem autor: AuditoriaItem só carrega usuario_id; junte com
   op.usuario para expor nome/e-mail. Exponha na UI os filtros usuario_id/de/ate que
   admin_router.py já aceita e o AdminPage ainda não usa.
4) Control plane sem auditoria própria: superusuário nunca tem org_id de sessão, então
   nunca pode chamar /admin/auditoria (que filtra por org_id) — e não existe
   /platform/auditoria. Crie o endpoint com sessão bypass de RLS (igual a
   superuser_session), cobrindo inclusive ações com org_id=None (ex.: definir_brasao).
5) Badge de licença: PainelLicencas (PlataformaPage.tsx:300-303) mostra "ATIVA" mesmo
   vencida, porque não há transição automática de status. Corrija o label:
   vigente ? 'vigente' : (hoje > vigencia_fim ? 'expirada' : status).
6) Licença invisível ao tenant: crie GET /me/licencas (ou equivalente) e um badge de
   vigência na aba Organização (Admin) ou Perfil — hoje só se descobre por erro ao adicionar
   ente à carteira.
7) Formulário de provisionamento de organização (PlataformaPage.tsx:397-507) passa a
   coletar metrica_cobranca e preco — hoje toda org nasce com billing indefinido. Exponha
   também o flag sem_populacao (já calculado, tenancy/service.py:400) na tabela de faturas.

Testes: fatura com preço configurado (valor_total > 0); auditoria dos 3 fluxos de RBAC;
/platform/auditoria isolado por sessão; badge com licença vencida. Estenda a suíte de
isolamento RLS entre organizações da Sprint 28 para cobrir os novos endpoints.
make lint && make test.
```

---

#### Sprint B3 — Funcionalidades construídas e nunca ligadas

**Objetivo:** fechar os itens já listados como "Planejado (B3)" na tabela de UX (U12-U17) e
somar os achados equivalentes do Assistente encontrados nesta rodada — em todos os casos, a
peça já existe no código e simplesmente não é chamada.

**Problema:** infraestrutura de impressão completa (`@page`, `.no-print`) com zero
gatilhos; 6 de 13 links "relatório completo" passam um modelo inexistente e caem
silenciosamente em "Resumo Executivo"; `AccessibleChart` (205 linhas, com alternativa
tabular) nunca é importado; escalas de gráfico inconsistentes entre `SerieChart` (ancora em
zero) e `TendenciaChart` (trunca o eixo sem avisar); seletor de período inerte em 7 rotas. No
Assistente: `dados_incompletos` é calculado e nunca lido pela tela; o número ancorado só vira
link de fonte quando o texto do Gemini repete o valor formatado **exatamente**; quando o
Gemini está indisponível, a resposta degrada sem aviso de "modo offline".

**Justificativa:** é trabalho já pago (o componente existe) e não entregue ao gestor — o
menor custo por item corrigido de toda a auditoria.

**Páginas afetadas:** todas (impressão/gráficos), Relatórios, Assistente.

**Tarefas:** botão/gatilho de impressão nas páginas fiscais principais; corrigir os 6 links
de "relatório completo"; adotar `AccessibleChart` nos gráficos que ainda não o usam;
padronizar a âncora de escala entre `SerieChart`/`TendenciaChart` (ou sinalizar o
truncamento); ligar/remover o seletor de período inerte; Assistente: renderizar
`dados_incompletos`; casar número por regex numérica tolerante; rótulo "modo offline (sem
Gemini)".

**Riscos:** nenhum — é ligar UI a funcionalidade já existente, sem tocar cálculo.

**Critérios de aceite:** impressão produz página legível nas 5 páginas fiscais mais usadas;
13/13 links de relatório abrem o modelo certo; gráficos com alternativa tabular acessível;
Assistente sinaliza dado incompleto e modo offline na tela.

**Testes:** teste de impressão (CSS aplicado); teste dos 13 links de relatório; axe-core nos
gráficos convertidos; teste do Assistente com resposta parafraseada e com `GEMINI_API_KEY`
ausente.

**Evidências:** PDF/print preview de 2 páginas; captura do Assistente em modo offline com o
aviso visível.

**Prompt Claude Code:**
```
Implemente a Sprint B3 de backend_plataforma_fiscal/docs/evolucao_plataforma.md — itens já
listados como "Planejado (B3)" na tabela de UX (§5.2, U12-U17), mais os achados equivalentes
do Assistente encontrados na segunda rodada de auditoria (§12). Em todos os casos a peça já
existe no código; falta ligá-la.

- Impressão: @page/.no-print já existem em global.css:342-405; window.print() nunca é
  chamado (grep = 0). Adicione o gatilho nas páginas fiscais principais.
- Relatórios: 6 de 13 links "relatório completo" passam um modelo inexistente e caem em
  "Resumo Executivo" (reports/models.py:26-32) — corrija os 6 para o modelo certo.
- AccessibleChart.tsx (205 linhas, com figure/figcaption e alternativa tabular) tem zero
  importações — adote-o nos gráficos SVG artesanais que ainda não o usam.
- Escalas: SerieChart ancora em zero, TendenciaChart/PrevisoesPage truncam o eixo sem avisar
  em série monetária (TendenciaChart.tsx:60-72) — padronize ou sinalize explicitamente o
  truncamento.
- Seletor de período do AppShell (AppShell.tsx:257) é inerte em 7 rotas — ligue-o ou
  oculte-o nessas rotas.
- Assistente: dados_incompletos é calculado (assistant/service.py:173-194,236) e nunca lido
  em AssistentePage.tsx — replique o bloco que RelatoriosPage.tsx:349-352 já usa para o campo
  irmão. O casamento de "número ancorado" (RespostaMarkdown.tsx:29-38) exige igualdade de
  string exata com fato.valor_formatado — troque por regex numérica tolerante para não
  perder o link em paráfrases. Quando use_gemini() degrada para LocalGroundedProvider
  (llm.py:44-50), mostre "modo offline (sem Gemini)" na tela quando
  uso.modelo === 'local-grounded'.

Testes: impressão (CSS aplicado); 13/13 links de relatório corretos; axe-core nos gráficos
convertidos; Assistente com resposta parafraseada (link de fonte preservado) e com chave
ausente (aviso visível). npm run test && npx playwright test; make test.
```

---

#### Sprint E1 — Segurança, isolamento entre organizações e desempenho

**Objetivo:** transformar em regra exigida por teste o que hoje só está certo por hábito
(o 404 que não vaza existência), fechar a única rota por ente sem gate de escopo e tirar do
caminho quente o N+1 que o próprio gate introduz.

**Origem:** todos os itens abaixo são **achados confirmados no código** pela frente P4 da
A0R (§5.1.2), com `arquivo:linha`. O que ainda é hipótese — o alcance da A24, o número de
entes afetados pela A23 — **não entra nesta ficha**: é fiscal e depende de medição prévia.

**Problema:**
1. **A22** — `GET /ingestao/data?fonte=&ente=&periodo=` exige a capacidade `administrar` e
   não chama `assert_ente_in_scope` (`ingestion/router.py:185-197`, `service.py:377-407`).
   O dado é público, então não há vazamento entre tenants; o que é furado é o **gate de
   licença**, que só existe dentro daquele `assert`. Uma organização licenciada para um
   município lê o silver de qualquer um dos 5.598.
2. **404 × 403 sem régua** — a convenção certa (recurso de outra organização não existe
   para quem pergunta) está implementada em cenários, alertas e relatórios, mas o teste que
   deveria protegê-la aceita os dois códigos (`test_sprint28_seguranca.py:248`). Uma
   regressão que passe a responder 403 — vazando a existência do recurso alheio — não
   quebraria nada.
3. **A27** — `shared/scope.py::_estado_prefixes` (linhas 126-142) percorre a carteira e
   consulta `dim_ente` **ente a ente**, sem cache de sessão, dentro de
   `assert_ente_in_scope` e de `carteira_scope_ibges`. Para conta estadual com 184
   municípios, é até 184 consultas **por requisição**, em toda rota fiscal. O padrão certo
   está dois blocos acima, na `cobertura_licenca` (`:96-117`), que memoriza em
   `session.info`.
4. **Materialização síncrona no request** — `POST /carteira/refresh` percorre o escopo
   inteiro chamando `refresh_mart_carteira` ente a ente dentro da requisição
   (`carteira_service.py:178-183`, `carteira_router.py:72-79`). Para uma licença global são
   5.598 iterações num handler HTTP.
5. **A26** — `GET /reconciliacao/rcl_rgf` e `GET /qualidade` devolvem número fiscal sem
   `source_ref`, e `gold.data_quality_check` não guarda a `versao_entrega` conferida
   (`quality/models.py:38-48`): depois de uma retificação, ninguém sabe sobre qual versão o
   check passou ou falhou.
6. **A25** — a conversão bimestre→quadrimestre existe seis vezes, em duas semânticas
   (ímpar → `None` × ímpar → teto), e nenhuma cobre o RGF semestral (LRF art. 63).

**Justificativa:** os itens 1 e 2 são de conformidade e de contrato comercial — a licença é
o que separa "cliente de um município" de "acesso ao país inteiro". Os itens 3 e 4 são o
custo por requisição da rota mais quente do produto, e crescem com o tamanho do cliente:
pioram exatamente no cliente que paga mais. O 5 e o 6 são dívidas de coerência que já
produziram defeito em outra família (A14/A15: *versão que existe, vigência que não se
declara*).

**Páginas afetadas:** todas as rotas por ente (gate de escopo), Central de Dados
(`/ingestao/data`, painel de qualidade), Carteira (refresh), Reconciliação.

**Tarefas:**
- `assert_ente_in_scope` em `GET /ingestao/data` (roteador **e** serviço, para que o
  caminho programático não fique aberto).
- Memorizar `_estado_prefixes` e `_is_estado` em `session.info`, no mesmo padrão de
  `cobertura_licenca`, com invalidação junto de `invalidar_cobertura`.
- `POST /carteira/refresh` passa a enfileirar job (padrão `POST /carteira/lote/{acao}`, que
  já devolve 202) ou a recusar acima de um teto declarado de entes.
- Endurecer a asserção de isolamento: **404** (não 403) para identificador de outra
  organização, cobrindo relatório, cenário, alerta, agendamento e job de ingestão.
- `source_ref` + `versao_entrega` em `ReconciliacaoResultado`/`DivergenciaItem` e em
  `CheckOut`; coluna `versao_entrega` em `gold.data_quality_check` (migration aditiva,
  reversível; sem apagar histórico).
- Consolidar a conversão bimestre→quadrimestre em `shared/periodo.py`, com a semântica
  decidida explicitamente (e o RGF semestral), substituindo as seis cópias.

**Riscos:**
- Mudar 403→404 numa rota que hoje devolve 403 **altera contrato** para o frontend: checar
  cada consumidor em `services/backend.ts` antes (o `AsyncState` trata os dois, mas a
  mensagem muda).
- Fechar a A22 pode quebrar rotina operacional que hoje lê silver de ente fora da carteira
  (uso interno, conferência) — se houver, a saída é a sessão de superusuário do control
  plane, não afrouxar o gate.
- Consolidar a regra de período **muda números** onde as duas semânticas divergiam
  (bimestre ímpar): medir antes, com a mesma disciplina de dry-run da A5, e tratar como
  correção fiscal, não refatoração.
- Cache de escopo em `session.info` erra se a carteira mudar dentro da mesma requisição:
  invalidar no mesmo ponto em que a licença já invalida.

**Critérios de aceite (objetivos):**
1. `GET /ingestao/data?ente=X` com `X` fora da carteira devolve **403**; com `X` na carteira
   e fora da licença, **403 `ente-nao-licenciado`**; dentro dos dois, **200**.
2. Para relatório, cenário, alerta, agendamento e job de ingestão de outra organização, o
   status é **exatamente 404** (`assert == 404`, não `in {403, 404}`) e o corpo não contém o
   `cod_ibge` nem o nome do outro tenant.
3. Requisição a `/entes/{ibge}/dashboard` com conta estadual de 184 municípios na carteira
   emite **≤ 5 consultas** no gate de escopo (hoje: 1 + até 184), medido por contador de
   eventos do SQLAlchemy no teste — e o `x-performance-p95-ms` da rota permanece dentro do
   orçamento de 500 ms já declarado pelo middleware da Sprint 27.
4. `POST /carteira/refresh` responde **202 com job** (ou 422 declarando o teto) e não
   percorre o escopo dentro do request.
5. `GET /reconciliacao/rcl_rgf` e `GET /qualidade` devolvem `source_ref` com
   `versao_entrega` nos dois lados comparados; `gold.data_quality_check` grava a versão, e
   reexecutar o check sobre uma retificação **cria linha nova** em vez de sobrescrever a
   anterior sem rastro.
6. `pytest tests/test_auditoria_a0r.py` continua verde com o conjunto de rotas sem gate
   **reduzido** (a catraca aceita a redução; `ingestion` sai da lista de conhecidos).
7. `make lint && make test` verdes, sem exceção.

**Testes:**
- `test_sprint28_seguranca.py`: asserção estrita de 404 por recurso, um caso por família.
- Novo: `/ingestao/data` nos três estados (fora da carteira, sem licença, ok).
- Novo: contador de consultas do gate de escopo para conta estadual (evento
  `before_cursor_execute`), com o número máximo declarado no próprio teste.
- Novo: `/carteira/refresh` devolve job e não bloqueia; job materializa o mesmo total.
- Contrato: `source_ref` presente nos dois contratos que hoje não o têm.
- Regressão de período: para cada bimestre 1–6 e para RGF semestral, a função única
  devolve o que a decisão registrada mandar — e as seis cópias deixam de existir.

**Evidências:** captura do 403 antes/depois em `/ingestao/data` para um ente fora da
licença; contagem de consultas por requisição antes/depois (log do contador); tempo de
resposta de `POST /carteira/refresh` antes/depois; diff do contrato de reconciliação com o
`source_ref` novo.

**Prompt Claude Code:**
```
Implemente a Sprint E1 de backend_plataforma_fiscal/docs/evolucao_plataforma.md —
segurança, isolamento entre organizações e desempenho. Todos os itens são achados
CONFIRMADOS NO CÓDIGO pela frente P4 da auditoria A0R (§5.1.2); nenhum depende de medição
prévia. Não amplie o escopo para os achados fiscais (A23/A24), que são de outra sprint.

1) A22 — GET /ingestao/data (ingestion/router.py:185-197, service.py:377-407) exige só a
capacidade "administrar" e nunca chama assert_ente_in_scope: a licença, que vive dentro
desse gate, não é conferida. Adicione o gate no roteador e no serviço. Teste os três
estados: ente fora da carteira (403 de escopo), ente na carteira e fora da licença (403
ente-nao-licenciado), ente ok (200).

2) 404 × 403 — tests/test_sprint28_seguranca.py:248 aceita "403 ou 404" para identificador
de outra organização. Endureça para == 404 e estenda a cobertura a relatório, cenário,
alerta, agendamento e job de ingestão: existência alheia não pode vazar nem pelo status.
Se alguma rota hoje devolver 403, corrija a rota, não o teste.

3) A27 — shared/scope.py::_estado_prefixes (126-142) consulta dim_ente ente a ente dentro
do gate, sem cache, em toda requisição de conta estadual. Memorize em session.info no mesmo
padrão de cobertura_licenca (:96-117), invalidando junto com invalidar_cobertura. Prove com
um contador de consultas (before_cursor_execute): ≤ 5 consultas no gate para uma carteira
de 184 municípios.

4) POST /carteira/refresh (carteira_router.py:72-79 → carteira_service.py:178-183) percorre
o escopo inteiro dentro do request. Passe a enfileirar job (o padrão de /carteira/lote já
devolve 202) ou recuse acima de um teto declarado.

5) A26 — reconciliation/schemas.py e quality/schemas.py::CheckOut devolvem número fiscal
sem source_ref, e gold.data_quality_check não guarda versao_entrega (models.py:38-48).
Adicione o source_ref nos dois contratos e a coluna na tabela (migration aditiva e
reversível), de modo que reexecutar um check depois de uma retificação não sobrescreva o
resultado da versão anterior sem rastro.

6) A25 — a conversão bimestre→quadrimestre existe seis vezes (quality/service.py:83,
cash_rap/service.py:96, result/service.py:95, benchmark/service.py:817,
dashboard/estadual_service.py:227, reports/service.py:391) em DUAS semânticas: bimestre
ímpar vira None em três e vira quadrimestre por teto nas outras três; nenhuma conhece o RGF
semestral (LRF art. 63). Consolide em shared/periodo.py, DECIDINDO a semântica de forma
explícita e registrando a decisão no §10 do documento. Atenção: isto MUDA número onde as
duas divergiam — meça antes com dry-run, no padrão da A5, e trate como correção fiscal.

A catraca tests/test_auditoria_a0r.py deve continuar verde: ela aceita que o conjunto de
rotas sem gate e o de cópias da regra de período ENCOLHAM. make lint && make test.
```

---

#### Sprint A4_MSC/A4_SIOPS — dry-run e prova de escala das ingestões adiadas na A4

**Objetivo:** fechar a lacuna que a A4 deixou aberta por volume, não por defeito: `MSC`,
`SIOPS` e `SIOPE` têm conector completo e testado, mas cobertura real de **1 ente cada**
(confirmado no banco de dev: `gold.dim_entrega` tem 1 `cod_ibge` só para os três
`relatorio`; `gold.fato_msc_saldo` tem 8.285 linhas, todas do mesmo ente). Sem um jeito de
medir o custo antes de pagar o custo, a decisão de escalar continua sendo chute.

**Problema:** o motor de backfill resiliente já existe (`app/workers/backfill.py` —
checkpoint, commit por unidade, guarda de disco) e já foi usado para a âncora CE (Sprint
21, `scripts/backfill_sprint21.py`), mas **não tem modo `--dry-run`** em lugar nenhum — nem
no motor, nem no script. Para MSC especificamente, cada unidade (ente × mês) custa **12
chamadas** (`classe_conta` 1..4 × `id_tv` 3 tipos — `MscConnector.extract`,
`connectors/siconfi.py:321-329`), o que faz 184 municípios × 12 meses × 12 chamadas ≈ **26
mil requisições** por ano de histórico. Disparar isso ao vivo sem antes saber o número e o
tempo estimado é a mesma classe de erro que a A5/A15 já ensinaram a não repetir: mudar
volume grande sem medir antes.

**Justificativa:** Patrimônio (MSC) e Saúde/Educação (SIOPS/SIOPE) são páginas do produto
com cobertura de fato zero fora do ente-âncora — o selo de cobertura (A1) já declara isso
honestamente, mas "declarar a lacuna" não é o mesmo que "ter o caminho para fechá-la".

**Páginas afetadas:** Patrimônio (MSC), Saúde & Educação (SIOPS/SIOPE), Central de Dados
(cobertura por fonte).

**Tarefas:**
- `--dry-run` no motor (`app/workers/backfill.py`) e/ou na camada de script: monta o plano
  de `BackfillUnit` inteiro, soma o número de unidades e o número de chamadas HTTP
  esperadas por unidade (1 para SIOPS/SIOPE, 12 para MSC), estima o tempo pelo rate-limit
  já herdado do cliente SICONFI (~6 req/s) — **sem** chamar `extract`/`to_bronze`/`to_silver`
  e **sem** tocar no checkpoint.
- Novo script (ou extensão de `backfill_sprint21.py`) que monta o plano nacional (todos os
  entes, não só CE) para `siconfi_msc`, `siops_saude`, `siope_educacao`, reusando
  `_escopo_entes`/checkpoint/disk-guard do motor existente — sem duplicar a lógica de
  retomada.
- Registrar no documento o número real do dry-run (unidades, chamadas, tempo estimado) para
  cada uma das três fontes, nacional e por ano de histórico disponível.
- **Fora de escopo desta sprint:** disparar a carga nacional completa ao vivo. Os ~26 mil
  chamadas de MSC sozinho são volume real contra uma API de terceiro — decisão de quando e
  em que lote executar fica para quem acompanha a cota/rate-limit, não para a sprint.

**Riscos:** rate-limit/bloqueio da API do Tesouro/MS/FNDE se a carga real for disparada sem
throttle — mitigado por manter o `--dry-run` como produto desta sprint, não a carga em si.
Custo de armazenamento do backfill completo de MSC (maior tabela do sistema, Sprint 12) —
por isso o dry-run soma tempo **e** volume estimado de linhas antes de qualquer decisão.

**Critérios de aceite:** `--dry-run` roda para as três fontes sem gravar nada (banco de dev
antes/depois idêntico); relata unidades, chamadas HTTP e tempo estimado; o número de
chamadas do MSC bate com `184 × meses × 12` (±ente-estado); documento atualizado com os
números reais do dry-run rodado nesta sprint.

**Testes:** `--dry-run` não altera `gold.dim_entrega`/`fato_msc_saldo`/checkpoint (asserção
antes/depois); a contagem de chamadas estimadas para MSC bate com a fórmula documentada no
conector; o plano nacional inclui o ente estadual e não duplica os já cobertos (mesma
guarda de idempotência do checkpoint).

**Evidências:** saída do `--dry-run` para as três fontes, com contagem de unidades/chamadas/
tempo estimado, registrada no documento.

---

### Sprint E1 — execução: o que foi feito, com evidência antes/depois

> **Ressalva de método — atualizada após execução real.** O ambiente em que a E1 foi
> *implementada* não tinha shell utilizável (`bwrap: No permissions to create new
> namespace`, e a saída sem sandbox bloqueada por política): nem uma linha de
> `make lint`/`make mypy`/`make test` rodou ali, e o rascunho ficou várias tentativas sem
> conseguir avançar por causa disso — não por defeito no código escrito. A execução real
> aconteceu depois, fora daquele ambiente: `ruff check` e `mypy` limpos, suíte completa do
> backend verde (`pytest`, banco de desenvolvimento real) e do frontend verde (`eslint`,
> `tsc --noEmit`, `vitest` 228/228). Dois defeitos reais só apareceram nessa execução —
> exatamente o tipo de coisa que leitura de código não pega:
> 1. **ID da revision da migration com 34 caracteres** (`0041_sprinte1_isolamento_qualidade`)
>    contra o limite de 32 de `alembic_version.version_num` — todo teste que aplicava
>    migration quebrava em cascata (`StringDataRightTruncation`), disfarçado de dezenas de
>    falhas não relacionadas. Renomeado para `0041_sprinte1_isolamento_qual` (29
>    caracteres); nenhuma outra referência ao ID antigo existia no repositório.
> 2. Um `SIM117` de estilo (`ruff`) em `test_sprint_e1_isolamento.py` — dois `with`
>    aninhados que cabiam num só. Corrigido.
>
> O item 7 dos critérios de aceite (`make lint && make test` verdes) está **cumprido**, não
> mais pendente. Nada disto era executável no ambiente de autoria — a disciplina de separar
> "confirmado no código" de "confirmado contra o dado", herdada da A0R, foi o que permitiu
> confiar no rascunho o suficiente para verificá-lo em vez de descartá-lo.

#### 1. A22 — `GET /admin/ingestion/data` passou a conferir escopo e licença

| | Comportamento para um ente **fora** da carteira |
|---|---|
| **Antes** | `200` com as linhas do silver daquele ente (`ingestion/router.py:185-197` exigia só `administrar`; `service.read_data` não conferia nada) |
| **Depois** | `403 urn:…:scope-forbidden`; ente na carteira e sem licença, `403 urn:…:ente-nao-licenciado`; ente ok, `200` |

O `assert` está nos **dois** níveis, e isso é deliberado: o roteador protege o navegador, o
serviço protege o caminho programático (worker, script, outro módulo), que é justamente
por onde uma rotina interna leria o silver alheio sem passar pela borda. Teste do caminho
programático incluído (`test_o_gate_de_ingestao_data_vive_tambem_no_servico`).

Efeito colateral pretendido: `ingestion` saiu de `_SEM_GATE_CONHECIDOS` na catraca da A0R.
Restou **um** módulo na lista, `platform`, que é exceção legítima (control plane).

#### 2. 404 × 403 — a régua deixou de ser opinião

| | `GET /relatorios/{id}` de outra organização |
|---|---|
| **Antes** | `assert resposta.status_code in {403, 404}` (`test_sprint28_seguranca.py:248`) — uma regressão que passasse a responder 403, **vazando a existência**, não quebrava nada |
| **Depois** | `assert resposta.status_code == 404`, e a matriz completa em `tests/test_sprint_e1_isolamento.py` |

A matriz cobre **cinco famílias** (relatório, agendamento, cenário, alerta e job de
ingestão) em **leitura e mutação**, 13 rotas ao todo, e vai além do status:

* o corpo do erro não pode conter o `cod_ibge` nem o `org_id` do outro tenant;
* **o dado do dono tem de continuar como estava** depois de todas as tentativas de mutação
  — sem isso, um handler que apagasse a linha e *depois* devolvesse 404 passaria no teste;
* o dono continua enxergando e alterando o que é dele — um 404 universal passaria na
  matriz inteira e quebraria o produto.

O intruso recebe **todas** as capacidades RBAC na própria organização, de propósito: assim
um 403 só poderia vir da fronteira entre tenants, nunca de permissão faltando.

Nenhuma rota precisou ser corrigida: a convenção já estava certa no código (o repositório
filtra por `org_id`, o serviço devolve 404). O que a E1 fez foi torná-la **exigível**.

#### 3. A27 — o N+1 que o próprio gate introduzia

| | Consultas no gate, conta estadual com 184 municípios |
|---|---|
| **Antes** | `1` (carteira do ente) `+ 1` (`get_org`) `+ 1` (`list_carteira`) `+ 184` (`dim_ente`, uma por ente, via `session.get`) `+ 1` (licença) = **até 188**, repetidas a cada chamada do gate na mesma requisição |
| **Depois** | **5** na primeira chamada; **1** em cada chamada seguinte da mesma sessão |

Três mudanças: `dim_ente` em **uma** consulta (`list_dim_entes`, `IN (...)`); memorização
de `_estado_prefixes`/`_is_estado` em `session.info`, no mesmo padrão que
`cobertura_licenca` já usava; e a leitura da carteira unificada em `_carteira_ibges` — o
contador expôs que o gate e a visão agregada liam **a mesma carteira** duas vezes na mesma
requisição, o que a leitura de código não tinha mostrado.

O teste mede o caminho da **ampliação estadual** (município da UF que não está listado na
carteira), e não o município listado: quando o ente está na carteira, a primeira condição
do `or` responde antes e nada disto roda. Medir o caminho fácil não mediria defeito nenhum. A invalidação foi ao mesmo ponto — `invalidar_cobertura` —,
mais `invalidar_escopo_carteira`, chamada nas duas mutações de carteira
(`add_carteira_ente`, `carteira_lote`), porque a carteira **pode** mudar dentro da mesma
requisição. Esse risco estava listado na ficha e tem teste próprio.

O limiar é **contagem de consultas**, não milissegundos: a suíte divide o banco com o
resto, e um limiar de tempo mediria a carga da máquina, não o código. Registro completo
(consulta, volume, ambiente, limiar) em `docs/baseline_desempenho_e1.md`.

#### 4. `POST /carteira/refresh` — 202 com job, nada de escopo no request

| | `POST /carteira/refresh` |
|---|---|
| **Antes** | `200 {"linhas_materializadas": N}`, com **N iterações** de `refresh_mart_carteira` dentro do handler — 5.598 para uma licença global |
| **Depois** | `202` com o job (`op.carteira_lote_job`, `acao='refresh'`); o request grava **uma** linha. Escopo vazio ⇒ `422` |

Quem executa é `app/workers/carteira_tasks.py`, com relógio próprio ao lado dos de
relatórios e qualidade (e retomada do que ficou pendente antes de um restart — o banco é a
fila). **Sem teto artificial de entes**: um teto que nunca dispara é decoração, e nada no
request cresce mais com o escopo. O teste prende os três lados: a resposta é 202, nada foi
materializado quando ela volta, e o job materializa **o mesmo total** de antes — sem esse
terceiro, "melhorar o desempenho" seria só perder a funcionalidade.

#### 5. A26 — `source_ref` nos dois contratos que não tinham, e a versão conferida

**Reconciliação.** `DivergenciaItem` ganhou `source_ref_plataforma` e `source_ref_oficial`
— os **dois** lados, com `versao_entrega` em cada um —, e `ReconciliacaoResultado` ganhou
o `source_ref` agregado. O lado oficial carrega também o **período de origem**, porque a
correção do RGF chega por republicação num quadrimestre posterior (A15): sem isso, não se
distingue divergência real de comparação entre versões diferentes.

**Checks de qualidade.** `CheckOut` ganhou `versao_entrega` e `source_ref`; a coluna entrou
em `gold.data_quality_check` **e na chave única** (migration `0041`, aditiva e reversível).

| | Reexecutar um check depois de uma retificação |
|---|---|
| **Antes** | Sobrescrevia a linha. O painel dizia "ok" e ninguém sabia se aquele "ok" era do número novo ou do velho |
| **Depois** | Cria **linha nova**. As duas versões coexistem, e a leitura (painel e selo) elege o veredito **mais recente por chave** |

Três decisões de implementação que valem registro:

* `versao_entrega` é `NOT NULL DEFAULT '-'`, não `NULL`. Em PostgreSQL, `NULL` é distinto
  de `NULL` numa `UNIQUE`: com `NULL`, o *upsert* nunca conflitaria e o check de
  atualidade empilharia uma linha por execução. A sentinela não vaza para o contrato.
* A leitura precisou de um filtro de vigência. Sem ele, uma falha de entrega **já
  retificada** continuaria selando a página — o histórico seria ganho e a tela, prejuízo.
* `executado_em` passou a ser gravado pelo relógio da aplicação também na inserção: `now()`
  do PostgreSQL é o instante da **transação**, e dois vereditos no mesmo commit ficariam
  com o mesmo carimbo, deixando a eleição do vigente dependente de um desempate aleatório.

O índice `ix_data_quality_check_chave` acompanha, e **não é especulativo**: ele sustenta a
subconsulta correlacionada que esta mesma sprint introduziu. Nenhum outro índice foi
criado — não há medição que justifique.

#### 6. A25 — consolidada **depois** de a caracterização provar equivalência

A ficha avisava que consolidar "muda número onde as duas semânticas divergiam". Mudaria, se
a consolidação escolhesse uma das duas. Ela não escolheu: as duas respondem perguntas
diferentes e as duas têm uso legítimo — o painel de qualidade não deve conferir a DCL
contra um quadrimestre que ainda não fechou, e o benchmarking não deve perder a linhagem do
numerador de pessoal porque o usuário abriu um bimestre ímpar. **O defeito era a escolha
ser implícita**, não haver duas.

`shared/periodo.py::em_periodo_rgf` passou a expor as duas com nome — `CICLO_FECHADO`
(bimestre ímpar ⇒ `None`) e `CICLO_CORRENTE` (teto) — e cada chamador declara a sua, na
que já tinha. A cadência **semestral** do art. 63, II, entrou junto (`2024-B3` → `2024-S1`),
que era ponto cego das seis cópias.

`tests/test_sprint_e1_regra_periodo.py` reproduz as **seis implementações antigas
literalmente** e compara caso a caso, em todo o domínio que os chamadores produzem
(bimestres, anual, mensal, entrada vazia): **zero divergência**. Três diferenças
intencionais estão isoladas em testes próprios, com o motivo:

1. período já em forma de RGF volta como está (`2024-Q2` → `2024-Q2`) em vez de `None` —
   idempotência; os três que devolviam `None` faziam-no por acidente de `split`, e nenhum
   é alcançável por esse caminho;
2. bimestre fora da faixa (`2024-B7`) devolve `None` em vez de fabricar `2024-Q4`;
3. ano malformado (`20XX-B2`) devolve `None` em vez de `20XX-Q1`.

As três são correções, não regressões — e nenhuma é alcançável pelos chamadores reais, que
só passam período bimestral do RREO. Um quarto teste prova que as seis cópias **deixaram de
existir**: a catraca da A0R aceita a redução, este teste mostra que ela aconteceu.

Além da caracterização, a decisão do §10 está escrita como **tabela** — os seis bimestres,
nas duas semânticas, um caso de teste por bimestre (`_TABELA_QUADRIMESTRAL`), mais a
cadência semestral completa. É o que a ficha pedia ("para cada bimestre 1–6 e para o RGF
semestral"): quem mudar a semântica quebra no bimestre exato, e não numa página fiscal
três sprints depois. Um teste à parte prende o **padrão** do parâmetro: quem esquecer de
declarar a semântica recebe o ciclo fechado, que erra para o lado da ausência (aparece como
"sem dado") em vez de apontar um quadrimestre que ainda não fechou (apareceria como número).

#### Arquivos da sprint

* Migration: `alembic/versions/0041_sprinte1_isolamento_qualidade.py` (aditiva, reversível).
* Novos: `app/workers/carteira_tasks.py`, `docs/baseline_desempenho_e1.md`,
  `tests/test_sprint_e1_isolamento.py`, `tests/test_sprint_e1_desempenho.py`,
  `tests/test_sprint_e1_regra_periodo.py`, `tests/test_sprint_e1_rastreabilidade.py`.
* Contrato alterado (o frontend precisa saber): `POST /carteira/refresh` passou de
  `200 {linhas_materializadas}` para `202 {job}`. Nenhum fetcher em `services/backend.ts`
  consome essa rota hoje — conferido antes de mudar, como o risco da ficha pedia. Os
  campos novos de `CheckOut` são aditivos e opcionais.

---

### Sprint A4_MSC/A4_SIOPS — execução: os números reais do dry-run, medidos contra o cadastro nacional

Não há "antes/depois" de valor fiscal nesta sprint — nenhum dado publicado foi tocado. A
evidência é **"estimado pela ficha" × "medido pelo dry-run real"**, contra o cadastro
nacional de verdade (`silver.siconfi_entes`, banco de desenvolvimento), com o banco
conferido idêntico antes e depois (hash do checkpoint e contagens de linhas).

#### 1. `--dry-run` no motor + `estimate_backfill`

`app/workers/backfill.py` ganhou `estimate_backfill(units)`: monta o mesmo plano de
`BackfillUnit` que `run_backfill` executaria, chama o `discover()` **real** de cada
conector (puro — nenhum dos três toca rede ou sessão ali) e soma as chamadas HTTP por job
com uma fórmula derivada do próprio conector, não um literal solto:

| Fonte | Chamadas por job | De onde vem |
|---|---|---|
| MSC | `len(MscConnector.classes) * len(MscConnector.tipos_valor)` = 4×3 = **12** | os mesmos atributos que `MscConnector.extract` percorre (`connectors/siconfi.py:321-329`) |
| SIOPS/SIOPE | `len(job.params["entes"])` | o próprio job — varia com o tamanho do lote, não é fixo |
| demais fontes | `1` (piso; RGF subestimado — fora do escopo desta sprint) | `SiconfiConnectorBase.extract` |

O cliente e o *sink* injetados no conector são sentinelas (`_DryRunNetworkGuard`,
`_DryRunWriteGuard`): qualquer tentativa de `extract`/`upsert_bronze`/`register_entrega`
levanta `RuntimeError` na hora, em vez de sair à rede ou gravar em silêncio. O tempo
estimado usa a mesma constante nomeada que os três clientes já usam de verdade
(`DEFAULT_MAX_PER_SECOND = 6.0`, promovida de literal solto para constante em
`shared/ingestion/client.py`) — é um **piso**: soma só o intervalo mínimo entre chamadas,
não a latência de resposta nem o backoff de erro, então a execução real leva mais tempo
que isto, nunca menos.

#### 2. A fórmula bate com o critério de aceite, ao pé da letra

| | Estimado pela ficha | Medido pelo dry-run real |
|---|---|---|
| MSC, escopo do Ceará (184 municípios + 1 estado), 1 ano | "184 × 12 meses × 12 chamadas ≈ 26 mil" | `--dry-run --ufs CE --fontes siconfi_msc --anos 2023` → **185 unidades, 2.220 jobs, 26.640 chamadas** = 184×12×12 + **1×12×12** (o "± ente-estado" da ficha, exato) |
| CE + PI, 2 anos (2022–2023), as 3 fontes | (sem número prévio — só o plano nacional total) | **828 unidades, 9.888 jobs, 127.920 chamadas**; MSC/ano = 410 unidades = 185 (CE) + 225 (PI, 224 municípios + 1 estado) — bate com a contagem real de municípios das duas UFs |

Reproduzido com o script: `python -m scripts.backfill_msc_siops_siope --dry-run --ufs CE --fontes siconfi_msc --anos 2023`.

#### 3. Escala nacional, medida (não chutada)

Cadastro nacional hoje (`silver.siconfi_entes`, consulta real desta sprint): **5.568**
municípios (`esfera='M'`) + **26** estados (`esfera='E'`) + **1** Distrito Federal
(`esfera='D'`, `cod_ibge='53'` — código de 2 dígitos, tratado como "estado" pelas três
APIs) = **5.595** entes subnacionais em escopo (fora a União, `esfera='U'`, sem sentido
fiscal subnacional). É consistente com o total oficial do SICONFI registrado em §20/P6
(5.598 = 5.570 municípios + 27 estaduais + 1 federal); a diferença de 2 municípios é a
mesma lacuna de cadastro já rastreada ali, não uma descoberta nova desta sprint.

`python -m scripts.backfill_msc_siops_siope --dry-run --anos 2021-2026` (nacional, as 3
fontes, 6 exercícios — rodado de verdade nesta sprint, ~4s, puro cômputo local):

| Fonte | Unidades | Jobs | Chamadas HTTP | Tempo estimado (piso, 6 req/s) |
|---|---:|---:|---:|---:|
| **Total** | 33.894 | 404.784 | **5.236.920** | 242,4 h (~10,1 dias corridos ininterruptos) |
| `siconfi_msc` | 33.570 | 402.840 | 4.834.080 | 223,8 h (~9,3 dias) |
| `siops_saude` | 162 | 972 | 201.420 | 9,3 h |
| `siope_educacao` | 162 | 972 | 201.420 | 9,3 h |

Por ano — **idêntico nos 6 exercícios** (2021 a 2026, incluindo o ano corrente): nenhum dos
três conectores descarta período ainda não decorrido (`tipo_periodo` não é setado em
`MscConnector`, e `SiopsConnector`/`SiopeConnector` não herdam esse filtro) — o plano pede
os 12 meses/6 bimestres completos mesmo para 2026 em andamento. É uma leitura honesta do
que o plano **tentaria**, não do que existe publicado; a diferença é o tipo de coisa que só
aparece medindo, e é exatamente o que esta sprint existia para expor antes de gastar a cota:

| Fonte | Unidades/ano | Jobs/ano | Chamadas/ano | Tempo/ano |
|---|---:|---:|---:|---:|
| `siconfi_msc` | 5.595 | 67.140 | 805.680 | 37,3 h |
| `siops_saude` | 27 | 162 | 33.570 | 1,6 h |
| `siope_educacao` | 27 | 162 | 33.570 | 1,6 h |

MSC domina o custo por si só (maior tabela do sistema, Sprint 12): **~92%** das chamadas
totais do plano nacional 2021–2026, e é a fonte cujo volume de linhas (não só de chamadas)
merece decisão de janela antes de qualquer disparo — o risco que a ficha já apontava.

#### 4. Achado de implementação: SIOPS/SIOPE precisam de unidade agrupada por UF, não por ente

Os conectores de SIOPS/SIOPE não são "um ente por chamada" como o MSC. `discover()` gera
**1 job por (ano, bimestre)**, e esse job carrega **vários entes dentro do mesmo
`params["entes"]`**; `extract` faz 1 chamada por ente, mas o bronze e a entrega são
gravados sob a chave sentinela `cod_ibge="BR"` (`connectors/siops.py`/`siope.py`).
`repository.upsert_bronze` usa `ON CONFLICT DO NOTHING` em
`(fonte, cod_ibge, período, versão)` — então **duas unidades do mesmo `(ano, bimestre)`
executadas no mesmo dia** (mesma `versao` = data de captura) colidiriam na mesma chave, e
só a primeira gravaria o silver; as demais seriam puladas em silêncio. É exatamente o
padrão que o campo `entrega_agregada` documenta em `connectors/registry.py`, e que o outro
orquestrador da plataforma (`app/workers/ingest_jobs.py::_all_units`) já trata de
propósito — mas `FONTE_META` **não** marca `siops_saude`/`siope_educacao` com
`entrega_agregada=True`.

Por isso `scripts/backfill_msc_siops_siope.py` monta **uma unidade por (UF, ano)** para
estas duas fontes — o estado/DF e todos os seus municípios num único `RunRequest` — em vez
de uma por ente (o desenho do MSC). O total de chamadas HTTP não muda (seria o mesmo de
qualquer forma: 1 por ente por bimestre), mas a forma de agrupar evita a colisão de chave
E mantém o raio de uma falha real pequeno (uma UF, não o país inteiro, no rollback de uma
unidade que falhar no meio).

**Não corrigido nesta sprint, de propósito:** marcar `entrega_agregada=True` em
`FONTE_META` para `siops_saude`/`siope_educacao` resolveria a mesma armadilha no *outro*
orquestrador (`ingest_jobs.py`, usado pela tela "Central de Dados"), mas está fora do
escopo explícito desta ficha (conector/registry central, não o motor de backfill nem o
script). Fica registrado aqui como achado relacionado para decisão humana — é uma mudança
de 1 linha, aditiva, sem migration, no mesmo padrão já usado por `tesouro_fpm`/
`fnde_fundeb_repasse`/`transferencia_generica`.

#### 5. Prova de que o dry-run não grava nada

Hash MD5 de `var/backfill/checkpoint.json` idêntico antes e depois de rodar o dry-run
nacional completo (`9f0e166c504ccacee7c2bd4860b8232b`, nas duas medições) — o arquivo nem
foi aberto. Contagem de `gold.dim_entrega`/`silver.siconfi_msc`/`silver.siops_saude`/
`silver.siope_educacao`, filtrada pelos mesmos entes usados no plano, idêntica antes e
depois. `tests/test_a4_msc_siops_dry_run.py::test_dry_run_nao_grava_nada_no_banco` torna
isso um teste de regressão, não só uma observação pontual: cria um ente que só existe
dentro do teste, roda o `--dry-run` referenciando-o, e confere zero linhas em bronze,
entrega e nos três silvers tipados.

#### 6. Critérios de aceite

* `--dry-run` roda para as três fontes sem gravar nada — ✅ provado por hash de checkpoint
  + contagem de linhas antes/depois, e coberto por teste de regressão.
* Relata unidades, chamadas HTTP e tempo estimado — ✅ `format_estimate_report`, total e
  por fonte × ano (tabelas acima).
* O número de chamadas do MSC bate com `184 × meses × 12` (± ente-estado) — ✅ 26.640 =
  184×12×12 + 1×12×12, medido de verdade (§2), e coberto por teste que reproduz a fórmula
  a partir dos atributos do conector, não de um número solto.
* Documento atualizado com os números reais do dry-run — ✅ esta seção.
* Plano nacional inclui o ente estadual e não duplica os já cobertos — ✅ o cadastro por
  UF sempre inclui o código do estado/DF (§3); o checkpoint **padrão** do script novo é o
  **mesmo arquivo** do Sprint 21 (`var/backfill/checkpoint.json`), então uma unidade já
  concluída pela âncora CE (ou por uma corrida anterior deste script) é pulada, não
  reexecutada — testado em
  `test_plano_nacional_respeita_checkpoint_ja_concluido`.

`ruff check src tests` e `mypy` limpos; suíte completa **758 passed, 34 skipped, 2
xfailed** (banco de desenvolvimento real, sem processo concorrente). 21 testes novos em
`tests/test_a4_msc_siops_dry_run.py`.

#### Arquivos da sprint

* Sem migration — nenhuma tabela nova (o motor de backfill e o script não persistem
  estado próprio além do checkpoint em arquivo, que já existia).
* Alterados: `app/workers/backfill.py` (`estimate_backfill`, `BackfillEstimate`,
  `UnitEstimate`, `format_estimate_report`, sentinelas de rede/gravação),
  `app/shared/ingestion/client.py` (`DEFAULT_MAX_PER_SECOND`, antes literal `6.0` solto no
  default do construtor).
* Novos: `scripts/backfill_msc_siops_siope.py`, `tests/test_a4_msc_siops_dry_run.py`.
* `scripts/backfill_sprint21.py` **não foi alterado** — o script novo é independente
  (import de `app.workers.backfill`, sem tocar na âncora CE), e os dois só se encontram no
  mesmo arquivo de checkpoint, de propósito (§6).
* Fora de escopo desta sprint, como planejado: nenhuma chamada real de backfill em escala
  contra a API do Tesouro/MS/FNDE foi disparada. A janela/throttle da carga nacional
  completa (~5,2 milhões de chamadas nas 3 fontes, 2021–2026) é decisão humana separada —
  os números acima existem para informar essa decisão, não para substituí-la.

---

## 10. Decisões técnicas e metodológicas registradas

| Data | Decisão | Motivo |
|---|---|---|
| 2026-08-06 | **SIOPS/SIOPE no backfill nacional agrupam entes por UF, uma unidade por `(UF, ano)`** — não uma unidade por ente, como o MSC | Os dois conectores gravam bronze/entrega sob a chave sentinela `cod_ibge="BR"` (`connectors/siops.py`/`siope.py`); `upsert_bronze` faz `ON CONFLICT DO NOTHING`, então duas unidades do mesmo `(ano, bimestre)` no mesmo dia colidiriam e só a primeira gravaria o silver — o mesmo padrão que o campo `entrega_agregada` documenta em `connectors/registry.py`, mas que `FONTE_META` não marca para estas duas fontes. Agrupar por UF evita a colisão sem tocar no conector nem no registry (fora do escopo da A4_MSC/A4_SIOPS); ver a sprint completa abaixo |
| 2026-08-06 | **Bimestre → RGF tem duas semânticas, e as duas ficam — com nome.** `CICLO_FECHADO` (bimestre ímpar ⇒ sem RGF correspondente) e `CICLO_CORRENTE` (teto: B3 cai no Q2 em curso), em `shared/periodo.py::em_periodo_rgf`; cada chamador declara a sua | A A25 pedia "decidir a semântica". Decidir por **uma** teria mudado número em metade da plataforma sem que ninguém tivesse pedido: o painel de qualidade não deve conferir a DCL contra um quadrimestre que ainda não fechou, e o benchmarking não deve perder a linhagem do numerador de pessoal porque o usuário abriu um bimestre ímpar. As perguntas são diferentes; o defeito era a escolha ser **implícita**, e a correção é obrigá-la a aparecer no ponto da chamada. A cadência **semestral** do art. 63, II, entrou junto — era ponto cego das seis cópias |
| 2026-08-06 | **Limiar de desempenho em número de consultas, não em milissegundos** | A suíte divide o banco de desenvolvimento com o resto (decisão de 2026-08-04, abaixo): um limiar de tempo passaria numa máquina ociosa e falharia numa ocupada, e a reação natural a um teste que falha por motivo alheio é afrouxá-lo até parar de incomodar — é assim que um guarda de desempenho morre. Contagem de consultas é determinística e mede exatamente a classe de defeito da A27: trabalho que **cresce com o tamanho do cliente**. O orçamento de latência por rota continua onde estava (`x-performance-p95-ms`, Sprint 27) |
| 2026-08-06 | **`versao_entrega` entra na chave do check de qualidade com sentinela `'-'`, não `NULL`** | Em PostgreSQL, `NULL` é distinto de `NULL` numa `UNIQUE`: com `NULL`, o *upsert* nunca conflitaria e o check de atualidade — que não se ancora em entrega nenhuma — empilharia uma linha por execução. A sentinela é de chave e não vaza para o contrato (`versao_entrega: null`, `source_ref: null`) |
| 2026-08-04 | Suíte do backend roda contra o **banco de desenvolvimento real**: nada de carga nem de segunda suíte enquanto ela roda | Aconteceu duas vezes na mesma sessão — primeiro a materialização da B2 derrubou `test_reports`, depois um `pytest` paralelo derrubou `test_sprint21_backfill`. Nenhuma das duas era regressão. A suíte é boa o bastante para notar a mudança embaixo dela; a disciplina de operação é que precisa acompanhar |
| 2026-08-04 | Nota metodológica e glossário **inline**, nunca em página separada | Quem sai do número para entender o número raramente volta ao mesmo ponto |
| 2026-08-04 | Participação **omitida** (não zerada) quando há valor negativo no nível | Com clamp, as participações somavam >100% e cada fonte superavitária aparecia inflada — 21% exibido como 34% |
| 2026-08-04 | Modelo de projeção rotulado pela **premissa**, não pelo método | O gestor decide se a projeção serve pela premissa dela, não pelo sobrenome de quem publicou o método |
| 2026-07-31 | Vínculo nó↔linha bruta gravado na materialização (`fato_despesa.linha_origem`) | 31 de 105 nós não fechavam por descrição; drill que acerta dois terços é pior que nenhum |
| 2026-07-31 | Residual "Restante a pagar" entra no total sem entrar na série anual | Não é um ano; virar barra falsa seria pior que descartar. Descartá-lo subestimava a dívida em 7,8%–16,6% |
| 2026-07-31 | CDP tratado como base nacional (`cod_ibge='BR'`) | `res-cdp` ignora `id_ente`; 117 mil linhas nacionais estavam atribuídas a um ente |
| 2026-07-31 | Regra de calendário fiscal canônica em `shared.periodo.em_bimestre` | Estava triplicada e as três cópias ignoravam o RGF semestral |
| 2026-07-30 | Procedência declarada como dado, conferida contra os conectores por teste | Cópia de endereços envelhece; página de auditoria desatualizada é pior que nenhuma |
| 2026-07-29 | Ausência com saída (extensão RFC 7807) | "Tentar de novo" contra ausência de publicação é conselho inútil |

---

## 20. Bloqueios e decisões pendentes

| # | Item | Situação | O que falta | Como desbloquear |
|---|---|---|---|---|
| P1 | **A8 — resíduo de teste em produção** | Aguardando decisão | 3 organizações `Org <hex>` e 6 entes sintéticos em `op.organizacao`/`op.carteira_ente`. Apagar é destrutivo e irreversível sem backup | Decisão do responsável. Recomendo: verificar se alguma tem usuário real associado, fazer dump só do schema `op`, e então remover |
| P2 | **Frente fiscal/contábil** | ✅ **Relatório concluído** (A0R, §5.1.2) — execução contra o dado pendente | RCL × RCL Ajustada, exclusões do art. 19 §1º e acima × abaixo da linha auditados e **corretos**; a leitura da RCL Ajustada tem defeito de consulta (**A24**). Falta rodar `scripts/validacao_fiscal.py` e as consultas do §5.1.2 contra o banco | Uma sessão com banco de desenvolvimento. Comandos já escritos |
| P3 | **Frente de dados/rastreabilidade** | ✅ **Relatório concluído** (A0R, §5.1.2) — nove checks **não executados** | Inventário de `source_ref` fechado (160 rotas, 22 contratos, 2 lacunas reais = **A26**); varredura de valores fixos **limpa** (nenhum teto da LRF hardcoded); os nove checks auditados por código, com um defeito achado (**A23**), e ainda **não executados** | Mesma sessão com banco. `pytest tests/test_auditoria_a0r.py` já roda sem dado |
| P4 | **Frente de arquitetura/segurança** | ✅ **Relatório concluído, corrigido e verificado na E1** | Todos os itens foram fechados: asserção estrita de **404** com matriz de leitura e mutação em cinco famílias de recurso; **A22** (gate em `/ingestao/data`, roteador e serviço); **A25** (regra de período consolidada com as duas semânticas nomeadas, após caracterização); **A26** (`source_ref` + `versao_entrega`); **A27** (N+1 do gate). Evidência antes/depois em §12.3. `make lint && make mypy && make test` (backend) e `eslint && tsc && vitest` (frontend) executados de verdade e verdes, fora do ambiente de autoria (que não tinha shell) | — |
| P5 | **Divergência do DTP** | 🔎 **Rediagnosticada — permanece aberta, com causa provável e correção especificada** | O enunciado antigo ("`DTP (VI) = (IIIa + IIIb)` tratado como composição em vez de `bruta − exclusões`") **não descreve o código de hoje**: a DTP publicada *é* a líquida repartida por estágio no MDF, e é assim que `pessoal.apurar` a usa (`pessoal.py:164-178`) — tratá-la como valor oficial está certo. A causa provável das divergências residuais é o **denominador**, não o numerador: **A24** (leitura da RCL Ajustada sem filtro de coluna e com `limit(1)` sem ordenação) explica um conjunto pequeno e específico de entes, que é a forma dos 6/2024 e 18/2025 registrados. **Decisão:** permanece aberta nesta sprint (diagnóstico) porque a confirmação exige o banco; a correção é de baixo risco e está especificada — filtrar `coluna = 'Valor'` e ordenar deterministicamente, sem migration e sem reprocessar. Motivo de não corrigir agora: mexer no denominador do limite de pessoal sem antes medir quantas linhas mudam repetiria o erro que a B2-c e a A5 já custaram | Rodar a 1ª e a 4ª consulta do §5.1.2. Se a 1ª voltar linhas, a correção da A24 entra na próxima sprint fiscal com reprocessamento medido antes |
| P6 | ~~Contagem de `dim_ente` não explicada~~ | ✅ **Resolvido** | A investigação levou a A9/A10: o catálogo estava incompleto em 8 municípios e o silver de entes em 1. Após reingestão e conformação, o catálogo bate **exatamente** com a fonte: 5.570 municipais + 27 estaduais (26 + DF) + 1 federal = **5.598**, o total publicado pelo SICONFI | — |

---

## 11. Histórico de atualizações

| Data | Alteração |
|---|---|
| 2026-08-12 | **Sprint B3 em produção — deploy verificado.** Backend não tocado. Frontend em `0227fa2` (build → rsync), bundle `index--zw6F-Az.js` — os três lados batem (build = publicado = servido); `/api/health` 200. |
| 2026-08-12 | **Sprint B3 implementada — impressão, `AccessibleChart` em mais gráficos, seletor de período, Assistente.** Todo o trabalho já existia no código e nunca era chamado. `PrintButton.tsx` (novo) aciona `window.print()` nas 10 páginas fiscais principais — o CSS de impressão (`@page`/`.no-print`) já existia sem gatilho nenhum. `SerieChart` adota `AccessibleChart` (figure/figcaption + alternativa tabular), com a implementação manual antiga (`TabelaSerie`, estado/IDs de toggle) removida por completo. `TendenciaChart` e o `ProjectionChart` embutido em `PrevisoesPage` passam a sinalizar explicitamente quando o eixo não ancora em zero, em vez de truncar em silêncio. Seletor de período do `AppShell` ocultado nas 7 rotas que nunca o consomem (`admin`, `central-dados`, `perfil`, `plataforma`, `divida/operacao`, `alertas`, `previsoes`) — antes ficava visível e mudo. Assistente: `dados_incompletos` (já calculado no backend) passa a ser exibido; casamento do número ancorado ganha regex numérica tolerante além da igualdade exata de string, para não perder o link de fonte quando o Gemini parafraseia (menos casas decimais, sem separador de milhar); aviso "modo offline (sem Gemini)" quando degrada para o provedor local. **Uma primeira tentativa bateu limite de sessão da conta no meio da conversão do `SerieChart`, deixando JSX não fechado** (chave/parêntese) e código morto (`TabelaSerie`/`th`/`td`, estado `tabela`/`panelId`/`descriptionId` não mais usados) — corrigido diretamente (não por agente) antes de uma segunda tentativa continuar do ponto certo, sem refazer o que já estava pronto. O item "6 de 13 links de relatório completo" da ficha original **já tinha sido corrigido incidentalmente pela Sprint D1** (7 usos achados e corrigidos, com teste de regressão próprio) — não repetido aqui. Verificado: `eslint` 0 erros (10 avisos pré-existentes), `tsc --noEmit` limpo, `vitest` 270/270 (21 novos). Backend não tocado — os campos que o Assistente passou a exibir já eram expostos pelo `service.py`. |
| 2026-08-12 | **Sprint H1 em produção — deploy verificado.** Backend em `9e0e346` (build → restart, sem migration nova — `op.assinatura` já existia, só faltava quem escrevesse nela de verdade). `/health` 200 local e via nginx. Frontend em `f131e8d`, bundle `index-CZqZySGs.js` — os três lados batem (build = publicado = servido). |
| 2026-08-12 | **Sprint H1 implementada — billing real, auditoria de RBAC e do control plane, licença visível.** As 7 tarefas da ficha, nenhum cálculo fiscal tocado. `POST/PATCH /platform/orgs/{id}/assinatura` religa `emitir_fatura` a um preço real (antes sempre `Decimal("0")`, porque o único endpoint que gravava `op.assinatura` tinha 403 hardcoded de propósito — um tenant não fixa o próprio preço). `insert_audit_log` entrou em `create_user`/`create_papel`/`update_papel_capacidades` (antes só dois fluxos registravam). A trilha ganhou autor (join com `op.usuario`) e os filtros que o backend já aceitava. `GET /platform/auditoria` resolve o achado de que o superusuário nunca tem `org_id` de sessão e por isso nunca podia consultar `/admin/auditoria` — mesma sessão bypass de RLS (`superuser_session`) e mesmo gate (`require_superuser`) que os outros endpoints do control plane já usam, sem inventar uma segunda forma de bypass; cobre inclusive ações sem organização-alvo. Badge de licença parou de mostrar "ATIVA" vencida; `GET /me/licencas` + badge de vigência no Perfil tiram o tenant de descobrir a própria licença por erro ao adicionar ente. Formulário de provisionamento passa a coletar `metrica_cobranca`/preço. Isolamento testado explicitamente (extensão de `test_sprint28_seguranca.py`): `/me/licencas` não vaza entre organizações; `/platform/auditoria` nega admin de tenant com 403 e isola por `org_id` no filtro. No caminho, um processo pytest órfão deixado pela própria implementação (rodando havia ~20 minutos sem lock nem progresso real de CPU) precisou ser encerrado manualmente antes da verificação independente poder rodar sem risco de concorrência no banco de dev. Verificado: `ruff`/`mypy` limpos, suíte completa do backend verde; `eslint` 0 erros (10 avisos pré-existentes), `tsc --noEmit` limpo, `vitest` 249/249 (frontend). |
| 2026-08-12 | **Sprint D1 em produção — deploy verificado.** Backend em `968e67a` (build → restart, sem migration nova — D1 não altera schema). `/health` 200 local e via nginx. Frontend em `ca6cde4`, bundle `index-DO9sdk5Y.js` — os três lados batem (build = publicado = servido). No caminho, o `git push` também subiu ao GitHub dois commits mais antigos (`c3568b6`, `b208005` — "ingerir malha do IBGE por UF") que já estavam aplicados em produção (HEAD local e da produção já os tinham; `alembic current` confirmou `0042_ibge_malha_job` já como head antes deste deploy) mas nunca tinham sido enviados ao remoto — identidade e escopo conferidos antes de aceitar como legítimos, sem qualquer ação adicional necessária. |
| 2026-08-07 | **Sprint D1 implementada — drill-down profundo, as 7 tarefas da ficha.** Backend concentrado no glossário PCASP (a única tarefa que exigia código novo de verdade — as outras 6 já tinham backend pronto, faltava só consumo no frontend): reaproveitado um rascunho preservado de tentativa anterior (3.555 contas PCASP níveis 6-7, portaria STN, nunca commitado), verificado contra o código real antes de aceitar, e integrado em `accounting/service.py` com prioridade DCA-autoritativa > glossário > fallback genérico — a MSC não publica nome de conta, e ~90% das folhas nível 6-7 só existiam como "código · Subitem". Backfill idempotente aplicado ao dev (0 linhas precisaram correção no momento). Frontend: painel expansível de Limites (memória/série/providências/simulador sem sair da página, deep-link `?indicador=`); cartão de posição vigente de garantias/operações de crédito na Dívida; deep-link universal `?painel=lineage&no=`/`?painel=qualidade&ente=&periodo=` na Central de Dados; crosslinks do Cockpit (Críticos/Tendências/Explicadores); Carteira preservando o indicador selecionado com "voltar ao ranking"; e um achado incidental — 7 usos de `ExportButton` passavam `modeloRelatorio` inexistente em `reports/models.py::MODELOS`, caindo em silêncio no "Resumo Executivo", corrigidos com teste de regressão estático. Fora de escopo, por decisão explícita da própria ficha: lineage por instância e as três "candidatas a página nova". Um teste (`test_carteira.py::test_estado_ve_municipios_da_uf_ranking_e_drill`) apareceu como falho no relato da implementação mas não reproduziu na verificação independente (suíte completa, `pytest`, exit 0) — tratado como falha de isolamento pré-existente, não causada pela D1 (nenhum arquivo de carteira do backend foi tocado nesta sprint). Verificado: `ruff`/`mypy` limpos (backend), suíte completa 784 passed; `eslint` 0 erros (10 avisos pré-existentes), `tsc --noEmit` limpo, `vitest` 245/245 (frontend). |
|---|---|
| 2026-08-06 | **A4_MSC/A4_SIOPS em produção — deploy verificado.** Backend em `0764e1a` (pull → build → restart, sem migration nova). `/health` 200 local e via nginx. Prova real dentro do contêiner de produção: `docker compose exec api python -m scripts.backfill_msc_siops_siope --dry-run --ufs CE --fontes siconfi_msc --anos 2023` devolveu **185 unidades, 2.220 jobs, 26.640 chamadas HTTP** — idêntico ao medido no dev. Nenhuma carga real foi disparada contra a API do Tesouro; o `--dry-run` continua sendo o único modo executado. |
| 2026-08-06 | **Sprint A4_MSC/A4_SIOPS implementada — `--dry-run` no motor de backfill, plano nacional para MSC/SIOPS/SIOPE.** Os três conectores já existiam e funcionavam (testados 1 ente cada); a lacuna real era medir o custo antes de decidir escalar. `app/workers/backfill.py` ganhou `estimate_backfill()`: monta o mesmo plano que a execução real usaria, chama o `discover()` real de cada conector (puro) e soma chamadas HTTP por uma fórmula derivada do próprio conector — `len(classes) × len(tipos_valor)` = 12 para MSC, `len(entes do job)` para SIOPS/SIOPE — nunca um número solto. Sentinelas (`_DryRunNetworkGuard`/`_DryRunWriteGuard`) fazem qualquer `extract`/`upsert_bronze`/`register_entrega` acidental falhar alto em vez de tocar rede ou banco. `scripts/backfill_msc_siops_siope.py` (novo) monta o plano **nacional** — não só CE — reusando checkpoint/disk-guard do motor do Sprint 21 (mesmo arquivo, sem duplicar retomada). **Achado real:** SIOPS/SIOPE gravam sob a chave sentinela `cod_ibge="BR"`; como `upsert_bronze` usa `ON CONFLICT DO NOTHING`, uma unidade por ente colidiria e perderia dado em silêncio — o mesmo padrão que `entrega_agregada` existe para evitar em `registry.py`, que não marca essas duas fontes. Contornado agrupando por UF no script novo; marcar `entrega_agregada=True` no registro central fica fora do escopo (afeta o outro orquestrador, `ingest_jobs.py`) e ficou registrado como achado relacionado para decisão humana. **Números reais**, medidos contra o cadastro nacional (5.595 entes): plano 2021–2026 nas três fontes = 33.894 unidades, 404.784 jobs, **5.236.920 chamadas HTTP**, ~242h estimado — MSC sozinho é ~92% do custo. Disparar essa carga ao vivo continua fora do escopo desta sprint, por decisão explícita. 21 testes novos, `ruff`/`mypy` limpos, suíte completa 758 passed. |
| 2026-08-06 | **A0R e E1 em produção — deploy real, verificado de ponta a ponta.** Backend: `git pull` (commit `7d7fa08`, inclui A0R `43f7aa7` e E1) → build → migration `0041` isolada (exit 0, aplicou exatamente `0040→0041`, mesmo texto de revisão gerado pela correção do ID de 34 caracteres) → restart `api`/`ingest-worker`/`scheduler` com `-f docker-compose.prod.yml -f docker-compose.ec2.yml` (os dois arquivos, confirmados explicitamente desta vez — o container já rodava com os dois, mas o comando deixou de depender de um comportamento implícito do Compose). `/health` responde `200` local e via nginx; `alembic current` confirma `0041_sprinte1_isolamento_qual`. Frontend: `git pull` (commit `1f6f328`) → build com `VITE_API_BASE_URL=/api` → `rsync` para `/var/www/plataforma`. Bundle `index-ByIkPdEf.js` — **idêntico** ao publicado na F2, esperado: a única mudança do frontend na E1 são dois campos TypeScript opcionais, apagados na compilação, sem efeito no JS emitido. Os três lados batem (build = publicado = servido pelo nginx). O orquestrador externo que produzira o rascunho da E1 foi cancelado nesta mesma sessão após corrupção de armazenamento do Docker Desktop; a sprint foi verificada e implantada diretamente, sem ele. |
| 2026-08-06 | **Sprint E1 implementada — segurança, isolamento entre organizações e desempenho.** Todos os seis itens da ficha (§12.3) foram fechados, e nenhum deles saiu de suposição: são os achados que a frente P4 da A0R confirmou no código, com `arquivo:linha`. **A22** — `GET /admin/ingestion/data?ente=` exigia só a capacidade `administrar` e nunca chamava `assert_ente_in_scope`; como o gate de **licença** vive dentro desse `assert`, uma conta licenciada para um município lia o silver de qualquer um dos 5.598. O gate entrou no roteador **e** no serviço (o caminho programático — worker, script — não passa pela borda), com os três estados cobertos por teste: fora da carteira `403 scope-forbidden`, na carteira e sem licença `403 ente-nao-licenciado`, ok `200`. O módulo saiu de `_SEM_GATE_CONHECIDOS` na catraca da A0R, que restou com **um** nome (`platform`, exceção legítima). **404 × 403** — a convenção certa já estava no código; o que faltava era ser exigida, porque `test_sprint28_seguranca.py:248` aceitava `403 ou 404` e uma regressão que passasse a vazar a existência do recurso alheio não quebrava nada. Agora é `== 404`, e a matriz nova (`test_sprint_e1_isolamento.py`) cobre **cinco famílias** — relatório, agendamento, cenário, alerta e job de ingestão — em **leitura e mutação**, 13 rotas, com o intruso recebendo *todas* as capacidades RBAC na própria organização de propósito (assim um 403 só poderia vir da fronteira entre tenants, nunca de permissão faltando). Três asserções além do status: o corpo do erro não pode conter o `cod_ibge` nem o `org_id` alheio; **o dado do dono continua como estava** depois de todas as tentativas de mutação (sem isso, um handler que apagasse a linha e *depois* devolvesse 404 passaria); e o dono continua enxergando e alterando o que é dele (um 404 universal passaria na matriz inteira e quebraria o produto). Nenhuma rota precisou ser corrigida — o repositório já filtrava por `org_id` e o serviço já devolvia 404. **A27** — o gate fazia N+1 em `dim_ente` (`session.get` por ente da carteira, sem cache): 1 + até 184 + 1 consultas por requisição numa conta estadual, repetidas a cada chamada do gate, num custo que **cresce com o tamanho do cliente**. Virou uma consulta em lote mais memorização em `session.info`, no mesmo padrão que `cobertura_licenca` já usava; a invalidação foi ao mesmo ponto, mais `invalidar_escopo_carteira` nas duas mutações de carteira, porque a carteira pode mudar dentro da mesma requisição (risco listado na ficha, com teste próprio). O limiar declarado é **≤ 5 consultas**, preso por contador de eventos `before_cursor_execute` — e há um teste que impede o próprio limiar de voltar a crescer com a carteira. **Decisão de método registrada no §10:** o guarda é em número de consultas, não em milissegundos, porque a suíte divide o banco de desenvolvimento com o resto e um limiar de tempo mediria a carga da máquina; o orçamento de latência por rota continua onde estava (`x-performance-p95-ms`, Sprint 27). **`POST /carteira/refresh`** deixou de percorrer o escopo dentro do handler (5.598 iterações para uma licença global) e passou a **202 com job durável** em `op.carteira_lote_job`, executado por `app/workers/carteira_tasks.py`, com relógio próprio ao lado dos de relatórios e qualidade. Sem teto artificial de entes — um teto que nunca dispara é decoração, e nada no request cresce mais com o escopo; escopo vazio responde 422, porque 202 para um lote sem trabalho esconderia erro de cadastro atrás de um "aceito". O teste prende os três lados: a resposta é 202, **nada** foi materializado quando ela volta, e o job materializa **o mesmo total** de antes — sem esse terceiro, "melhorar o desempenho" seria só perder a funcionalidade. **A26** — a reconciliação e os checks de qualidade eram as duas únicas lacunas reais do inventário de `source_ref` da frente P3, e as duas devolvem número fiscal. `DivergenciaItem` ganhou a procedência dos **dois** lados comparados (com `versao_entrega` em cada um, e o período de origem do lado oficial, porque a correção do RGF chega por republicação num quadrimestre posterior — A15); `CheckOut` ganhou `versao_entrega` e `source_ref`; e a coluna entrou em `gold.data_quality_check` **e na chave única** (migration `0041`, aditiva e reversível), de modo que reexecutar um check depois de uma retificação **cria linha nova** em vez de sobrescrever o veredito da versão anterior sem rastro. Três decisões de implementação que valem registro: a sentinela `'-'` em vez de `NULL` (em PostgreSQL `NULL` é distinto de `NULL` numa `UNIQUE`, e o check de atualidade empilharia uma linha por execução); um filtro de vigência na leitura, sem o qual uma falha de entrega **já retificada** continuaria selando a página (o histórico seria ganho e a tela, prejuízo); e `executado_em` gravado pelo relógio da aplicação também na inserção, porque `now()` do PostgreSQL é o instante da **transação** e dois vereditos no mesmo commit ficariam com carimbos iguais, deixando a eleição do vigente dependente de desempate aleatório. O índice `ix_data_quality_check_chave` acompanha e **não é especulativo**: sustenta a subconsulta correlacionada que esta mesma sprint introduziu; nenhum outro índice foi criado, porque não há medição que justifique. **A25** — a ficha avisava que consolidar "muda número onde as duas semânticas divergiam". Mudaria, se a consolidação escolhesse uma delas; ela não escolheu. As duas respondem perguntas diferentes e as duas têm uso legítimo — o painel de qualidade não deve conferir a DCL contra um quadrimestre que ainda não fechou, e o benchmarking não deve perder a linhagem do numerador de pessoal porque o usuário abriu um bimestre ímpar. O defeito era a escolha ser **implícita**. `shared/periodo.py::em_periodo_rgf` passou a expor as duas com nome (`CICLO_FECHADO` × `CICLO_CORRENTE`), cada chamador declara a que já tinha, e a cadência **semestral** do art. 63, II (`2024-B3` → `2024-S1`) entrou junto — ponto cego das seis cópias. A troca só foi feita **depois** do teste de caracterização: `test_sprint_e1_regra_periodo.py` reproduz as seis implementações antigas **literalmente** e compara caso a caso em todo o domínio que os chamadores produzem, com **zero divergência**; as três diferenças intencionais (idempotência para período já em forma de RGF, bimestre fora da faixa e ano malformado deixando de fabricar `2024-Q4`/`20XX-Q1`) estão isoladas em testes próprios, com o motivo, e nenhuma é alcançável pelos chamadores reais. Um quarto teste prova que as seis cópias **deixaram de existir** — a catraca da A0R aceita a redução, este mostra que ela aconteceu. **Ressalva de método, declarada também na ficha e no baseline:** o ambiente de autoria **não tinha shell** (`bwrap: No permissions to create new namespace`), então `make lint`, `make mypy` e `make test` não rodaram lá, nem nenhuma consulta ao banco — o rascunho ficou pronto, mas não verificado. A execução real aconteceu depois, fora daquele ambiente: `ruff`/`mypy` limpos, suíte completa do backend verde contra o banco de desenvolvimento, `eslint`/`tsc --noEmit`/`vitest` (228/228) verdes no frontend. Dois defeitos reais só apareceram nessa execução, nenhum dos dois no raciocínio da sprint: o ID da revision da migration tinha 34 caracteres contra o limite de 32 de `alembic_version.version_num` (renomeado para `0041_sprinte1_isolamento_qual`, 29 caracteres — sem isso, toda fixture que aplicava migration quebrava em cascata, disfarçado de dezenas de falhas não relacionadas), e um `SIM117` de estilo em `test_sprint_e1_isolamento.py` (dois `with` aninhados que cabiam num só). O critério 7 da ficha (`make lint && make test` verdes) está **cumprido**. Baseline de desempenho com consulta, volume, ambiente e limiar em `docs/baseline_desempenho_e1.md`. Nesta mesma sessão, o orquestrador externo (`prumo-sprint-orchestrator`) que produziu este rascunho foi cancelado após uma corrupção de armazenamento do Docker Desktop (I/O error nos snapshots do containerd, `wsl --shutdown` não resolveu por completo) — a sprint foi retomada e verificada diretamente, sem o orquestrador. Um `package.json`/`package-lock.json`/`node_modules` e uma leva de `.env*`/lockfiles vazios, criados por engano na raiz do projeto (fora dos dois repositórios) na primeira configuração do orquestrador, quebravam a resolução de config do Vite/esbuild e foram removidos — `.claude/` na raiz foi preservado por conter estado real do próprio Claude Code. |
| 2026-08-05 | **Sprint A0R concluída — as três frentes interrompidas voltaram com relatório (§5.1.2).** P2, P3 e P4 têm agora status individual, evidência `arquivo:linha` e a consulta/comando de confirmação para cada item. **Ressalva de método declarada no topo da seção, e ela vale para tudo que a A0R afirma:** esta rodada correu **sem shell e sem banco** — nenhuma consulta SQL, nenhum `pytest` e nenhum comando foram executados aqui; "confirmado" significa *confirmado no código*, e o que depende de dado está marcado **hipótese** com a consulta que a decide. **P2 (fiscal):** RCL × RCL Ajustada corretas (o denominador do art. 20 é a Ajustada publicada, com a RCL cheia só como queda declarada por `rcl_ajustada = NULL`), exclusões do art. 19 §1º completas e condicionais ao RPPS, acima × abaixo da linha com identidade verificada e tolerância explícita. **P3 (dados):** inventário de `source_ref` fechado — 160 rotas, 22 contratos com `source_ref`, duas lacunas reais (reconciliação e checks de qualidade) e uma dispensa justificada (cobertura mede o produto, não é número de demonstrativo); varredura de valores fixos **limpa** — nenhum teto da LRF hardcoded no backend, as faixas 90/95% derivadas num lugar só com override do banco, e a única exceção é o `0.70` do FUNDEB dentro do conector de PDF, registrado como tal; os nove checks foram **inventariados e auditados**, e **não executados**. **P4 (arquitetura):** a convenção 404 × 403 está certa no código (repositório filtra por `org_id`, serviço devolve 404 — existência alheia não vaza) e **frouxa no teste** (`test_sprint28_seguranca.py:248` aceita os dois); das 19 rotas que recebem ente, 17 validam escopo, 1 é exceção legítima (control plane) e **1 não valida** (A22). **Seis achados novos:** **A22** (`GET /ingestao/data?ente=` sem gate — fura a licença, não o dado, que é público), **A23** (o check `mart_vs_detalhe_pessoal` compara o mart, apurado sobre a RCL Ajustada, com um recálculo pela RCL cheia — falha estrutural falsa que vira alerta na fila do cliente), **A24** (a leitura da RCL Ajustada do Anexo 01 não filtra a coluna e usa `limit(1)` sem `order by` — mesma família do B2-b), **A25** (a conversão bimestre→quadrimestre existe **seis vezes em duas semânticas**: para o bimestre ímpar, metade da plataforma diz "não há RGF" e a outra metade aponta um; nenhuma cobre o RGF semestral), **A26** (reconciliação e checks devolvem número fiscal sem `source_ref`, e o check não guarda a `versao_entrega` conferida) e **A27** (o gate de escopo faz N+1 por requisição em conta estadual, sem o cache de sessão que a licença ao lado já usa). **P5/DTP rediagnosticada:** o enunciado antigo não descreve o código atual — no MDF a `DTP (VI) = (IIIa + IIIb)` **é** a líquida repartida por estágio, e usá-la como valor oficial está certo; a causa provável das divergências residuais é o denominador (A24), não o numerador. **Decisão registrada: permanece aberta**, com a correção especificada (filtrar `coluna = 'Valor'` e ordenar) e o motivo de não aplicá-la agora — mexer no denominador do limite de pessoal sem medir antes quantas linhas mudam repetiria o custo da B2-c e da A5. **Sete falsos positivos** foram verificados e descartados com o motivo de cada um (relatório em lote, refresh estadual, painel de qualidade e a própria condicionalidade de RPPS na DTP), para que a próxima rodada não gaste a mesma hora. A ficha da **E1** foi escrita a partir dos achados confirmados, com critérios objetivos e mensuráveis (403 nos três estados de `/ingestao/data`; `== 404` no lugar de `in {403,404}`; ≤ 5 consultas no gate de escopo para carteira de 184 municípios; `/carteira/refresh` em 202 com job). **Testes:** `tests/test_auditoria_a0r.py` — 9 casos, nenhum dependente de dado: regra pura do Anexo 01 (DTP publicada manda; exclusão de inativos condicional ao RPPS; coluna de percentual nunca entra no numerador; contas do layout oficial reconhecidas) e três **catracas** de inventário (contrato que tem `source_ref` não pode perdê-lo; rota por ente sem gate não pode crescer; a regra de período não pode ganhar uma sétima cópia). As catracas são deliberadamente unilaterais: aceitam a melhora e falham na piora — uma que travasse o defeito no lugar deixaria a suíte vermelha para quem corrigisse. Os 9 casos foram **escritos e não executados** nesta rodada (mesmo motivo: sem shell); rodar `pytest tests/test_auditoria_a0r.py` é o primeiro item da retomada, antes de qualquer conclusão sobre eles. **Nenhuma correção foi aplicada e nenhuma ação de produção foi executada**, por desenho da sprint. |
| 2026-08-05 | **Sprint F2 em produção — deploy verificado.** Backend em `8364c7b`, migration sem pendência nova (F2 não adiciona nenhuma), `/api/health` respondendo `200`. Frontend em `afb7811`, bundle `index-ByIkPdEf.js` — hash servido = publicado = build, os três conferidos, diferente do anterior (`index-z3fGw81Y.js`). |
| 2026-08-05 | **Sprint F2 concluída — 15 de 16 achados de clareza fechados (U19–U34), continuação direta da B1.** Mesma disciplina: nenhum número mudou, só rótulo, denominador exibido ou campo novo. Três exemplos concretos (o que o gestor lia × o que o dado dizia × a correção), no formato da tabela da B1 (§5.1.5): **(1) Receita/Despesa (U19)** — lia "Categoria → Origem → Espécie → Rubrica → Alínea" (5 níveis) sobre a árvore de Receita e "Categoria → Grupo → Modalidade → Elemento" (4 níveis) sobre a de Despesa/natureza; o dado só deriva 3 e 2 níveis respectivamente (`natureza.construir_arvore`, `classificacao.NATUREZA_PARENT` — o SICONFI não expõe código numérico pontuado, logo não há Rubrica/Alínea/Modalidade/Elemento a mostrar); corrigido para "Categoria → Origem → Espécie" e "Categoria Econômica → Grupo de Natureza" nos dois lugares onde o texto estava hardcoded (`ReceitaPage.tsx`, `DespesaPage.tsx`, e o `hierarquia` da memória de Receita em `revenue/service.py`, que tinha o mesmo erro). **(2) Limites — barra de progresso para pisos (U32)** — um ente em 27% de aplicação em saúde (piso 15%, cumprindo com folga) desenhava uma barra **quase cheia**, a mesma leitura visual que um ente prestes a estourar um teto; a razão valor/piso satura em 100% assim que o piso é superado, então "cumprir com folga" e "estar exatamente no limite" ficavam indistinguíveis pelo preenchimento. Corrigido invertendo o preenchimento visual só para `sentido=piso` (`ratioVisual = 100 − ratio`): agora a barra fica **vazia** para quem cumpre com folga e **cheia** para quem está bem abaixo do mínimo — a mesma convenção "mais cheia = mais perto de violar" que já valia para teto. O valor real na árvore de acessibilidade (`aria-valuenow`) não mudou — só o pixel. **(3) CAPAG — três grandezas sob um rótulo (U26)** — o card de Dívida mostrava "Metodologia 2022" para um ente cujo layout de origem (municipal histórico) publica a coluna `Ano_Base`, não uma metodologia — 2022 era o ano-base real da planilha, não uma versão de método; e um município (layout oficial, ICF) e um estado (layout estadual, texto de metodologia) apareciam com o mesmo rótulo "Metodologia" apesar de serem publicações e conectores diferentes do Tesouro. Corrigido com dois campos novos, sem migration e sem reprocessar CAPAG: `ano_base_fonte` (`debt/service.py::_parse_ano_base_fonte`, extraído só quando o valor bruto é um ano plausível de 4 dígitos) exibido como "Ano-base da fonte", com validação cruzada contra `ano_ref − 1` sinalizada (nunca corrigida em silêncio) quando diverge; e `metodologia_rotulo` ("ICF" para município, "Metodologia" para estado — resolvido pelo mesmo `len(cod_ibge)` que já escolhe entre as entregas `CAPAG`/`CAPAG-EST`, sem precisar de coluna nova para saber a origem). `metodologia_versao` deixou de repetir o mesmo número sob o rótulo errado quando era na verdade um ano-base — as três grandezas agora têm três rótulos, sem se misturar. Demais achados fechados: **Resultado (U27/U28)** — "(com RPPS)"/"(sem RPPS)" movidos para o `MetricHeader` do primário/nominal (antes só na `NotaRpps` recolhida) e para as fórmulas da memória de cálculo; `meta_nominal`/`realizado_nominal` passam a aparecer quando o ente publica só a meta nominal (antes lia "Meta de resultado primário —", indistinguível de não ter meta nenhuma). **Patrimônio (U33)** — "✓ Conciliado — Conciliação MSC ↔ DCA" aparecia para entes sem MSC nenhuma (só 1 dos 3 checks roda); `build_conciliacao` ganhou um `titulo` condicional ("Balanço fecha" sem MSC) e a `observacao` passou a descrever só os checks que de fato rodaram. **Receita (U20/U21/U22)** — `deducoes` (materializada, nunca exibida) exposta como bruto×deduções quando a fonte publica a coluna (achado à parte: nenhum RREO Anexo 01 real no acervo tem essa coluna hoje — confirmado por consulta direta ao banco; o campo aparece pronto para quando/se existir); a barra "própria×transferida" desdobrada em corrente×capital (`DependenciaResumo` ganhou `transferida_corrente`/`transferida_capital`, calculados pela raiz categórica do nó — sem mudar a soma). **Despesa (U21/U22/U23)** — `SeloCobertura` que só existia em Receita agora também aparece aqui (já registrada em `registry.py::FONTE_META`, só faltava consumir); aviso novo quando o eixo natureza está selecionado avisando que cabeçalho/série continuam no eixo função (Anexo 02), evitando a leitura de bug. **Pessoal (U25)** — cabeçalho passa a dizer a cadência do RGF do ente (quadrimestral/semestral, LRF art. 63, II), reusando `alerts/rules.py::cadencia_rgf` (campo novo `PessoalDetalhe.cadencia_rgf`). **Caixa (U29)** — `Art42Panel` mostra o quadrimestre avaliado (ex.: "2024-Q3"), não só o booleano dentro/fora da janela. **Limites/Benchmarking (U31/U34)** — `SeloCobertura`/`SeloQualidadePagina` adicionados nas duas páginas (indicadores já registrados em `coverage/service.py::INDICADORES_POR_PAGINA`, só faltava consumir). **Achado revisado na investigação, não fechado como a ficha propunha (U29/U30, parte do FUNDEB):** a ficha pedia replicar no card FUNDEB a nota de expurgo de RPNP sem lastro que a árvore MDE/ASPS já tem — mas a investigação (`health_edu/service.py::_apurar_educacao`) mostrou que o indicador `fundeb_profissionais` **não é expurgado** de RPNP sem lastro (só o total combinado de MDE é: `aplicada = despesa_bruta − deducoes − rpnp`; o `FUNDEB_PROFISSIONAIS` do Anexo 8 entra bruto, sem a subtração). Replicar a nota como paridade teria sido uma **falsa clareza** — exatamente o defeito que esta sprint existe para consertar, só que ao contrário. Em vez de replicar, `CardFundeb` ganhou uma nota que diz a assimetria real ("não expurga… diferente do ASPS/MDE acima"), sem tocar em nenhum cálculo; se o expurgo do FUNDEB for de fato exigido, é um achado de **cálculo**, não de rótulo, e pertence a outra sprint. **Testes:** backend — extensões em `test_debt.py` (+5, incluindo o caso estadual "Metodologia" × municipal "ICF"), `test_accounting.py` (+2 asserções), `test_revenue.py` (+2), `test_personnel.py` (+2) e `test_result.py` (+2) sobre a suíte já existente, tudo verde (`ruff`, `mypy` 235 arquivos, `pytest` completo, sem falha — inclusive reexecutado depois de corrigir uma colisão de `versao_entrega` fixo entre corridas no teste estadual, mesma lição de higiene de dado de teste já registrada em sprints anteriores). Frontend — arquivo novo `divida-capag.test.tsx` (4 testes) + extensões em `receita-despesa.test.tsx`, `sprint25b.test.tsx`, `sprint25c.test.tsx`, `sprint25d.test.tsx`, `sprint25e.test.tsx` e um índice de rastreabilidade em `clareza-conceitual.test.tsx`: suíte de 207 para **228 testes**, `tsc --noEmit` limpo, `eslint` só com os 10 avisos pré-existentes. |
| 2026-08-05 | **A5, A6, G1 e F1 em produção — deploy real, verificado de ponta a ponta, não apenas relatado.** Backend: `git pull` (commit `2908326`) → `docker compose build` → migration `0040` isolada (exit 0, aplicou exatamente `0039→0040`) → restart `api`/`ingest-worker`/`scheduler`. Frontend: `git pull` (commit `9b7d301`) → build com `node:20-alpine` → `rsync` para `/var/www/plataforma`. **Dois achados de infraestrutura, reais, corrigidos no processo — nenhum dos dois é bug de código das sprints, são lacunas de processo de deploy:** (1) `docker compose -f docker-compose.prod.yml up -d api ...` sozinho **não publica a porta 8000** — o compose versionado deixa em `expose` de propósito; existe um `docker-compose.ec2.yml` **não versionado**, específico desta instância, que adiciona `ports: 127.0.0.1:8000:8000`. Sem ele, a API sobe saudável mas o nginx (que roda no host, fora do Docker) não a alcança — 502 silencioso. Comando correto: `docker compose -f docker-compose.prod.yml -f docker-compose.ec2.yml up -d api ingest-worker scheduler`. Verificado que os dados reais (5.598 entes, tabelas com milhões de linhas) nunca saíram do lugar — só o nome/identidade do container mudou (volume nomeado `backend_pgdata`, persistente, independente do nome do container). (2) Não existe `.env` real na instância (só `.env.example`) — `src/services/api.ts` cai em `http://localhost:8000` fixo se `VITE_API_BASE_URL` não for passado no build, repetindo o incidente que este runbook já tinha documentado uma vez. O bundle publicado antes deste deploy já usava `/api` (caminho relativo, batendo com `location /api/ { proxy_pass http://127.0.0.1:8000/; }` do nginx) — usado o mesmo valor no build novo (`-e VITE_API_BASE_URL=/api`). **Verificação real, não relato**: `/api/health` responde `200 {"status":"ok",...}`; hash do bundle servido = publicado = build novo (`index-z3fGw81Y.js`, diferente do anterior `index-DBhkueEj.js`) — os três lados do teste que o runbook §3.1 pede. **Reprocessamento de PRODUÇÃO da A15 aplicado** (autorizado pelo usuário explicitamente, mesmo padrão do dev): dry-run mostrou 70 linhas afetadas (2074 entregas vigentes, mais que o dev por cobrir mais exercícios/entes), `materialize_endividamento.py` aplicado — 3.898 indicadores, zero erro — reverificação limpa em **0 divergência residual**, amostra conferida direto no banco de produção: `2307650/2023-B2` com `base_valor = R$ 1.022.418.338,43`, idêntico ao valor corrigido já validado no dev. |
| 2026-08-05 | **Sprint F1 concluída — `as_of` propagado às páginas que faltavam.** `as_of: datetime \| None` (ou `str \| null` no TS) adicionado aos schemas/interfaces de Receita, Despesa, Linha Bruta, Pessoal, Resultado, Patrimônio e Limites (`GET /limites`, que antes só aceitava `as_of` no detalhe); fetchers do frontend passam a enviá-lo; sub-cards de Pessoal/Patrimônio/Cockpit replicam o padrão de "pin" já usado em Dívida, para que uma retificação no meio do carregamento não misture versões na mesma tela. **Interrompida uma vez por limite de sessão da conta** (não erro de código) bem no meio dos edits de tipo do frontend — retomada do ponto exato onde parou, sem redigitar nada (o trabalho de backend, já commitável, sobreviveu no disco). Ao reabrir depois da retomada, o agente encerrou de novo sem relatório final; em vez de aceitar isso, os testes foram rodados **diretamente pelo orquestrador**, não por relato de agente: backend saiu limpo de cara (`ruff`, `mypy` em 235 arquivos, `pytest` completo, tudo verde); o frontend tinha `vitest` verde (207/207) mas `tsc --noEmit` acusava 9 erros reais — `as_of` virou campo obrigatório nas interfaces e 9 fixtures de teste em `linha-bruta.test.tsx` e `receita-despesa.test.tsx` (`LinhaBruta`, `ReceitaConciliacao/Realizacao/Dependencia/Memoria`, `DespesaEstagios/Execucao/Rigidez/Memoria`) não tinham sido atualizadas — `vitest` não faz checagem de tipo completa (usa esbuild), por isso os testes passavam e o `tsc` não. Corrigido diretamente (`as_of: null` nas 9 fixtures) por ser mecânico e bem localizado — sem abrir nova sprint/agente para isso. Reverificado: `tsc --noEmit` limpo, e os dois arquivos tocados rodados de novo isoladamente (22/22 testes, sem regressão). Suíte completa do frontend: 229 testes (207 + 22), `eslint` só com os 10 avisos pré-existentes (`react-refresh`/`exhaustive-deps`, nenhum novo). |
| 2026-08-05 | **Sprint G1 concluída — RBAC morto (A19) e `crescimento_rcl_pct` no-op (A20) corrigidos; simulador de cenários robustecido.** **A19**: a capacidade `"editar"`, exigida por `PATCH`/`DELETE /cenarios/{id}` desde a Sprint C2, nunca existiu no enum (`tenancy/models.CAPACIDADES`) nem no `CheckConstraint` do banco — o código dos dois endpoints sempre esteve certo, mas **nenhum papel, de nenhuma organização, jamais conseguiu receber essa capacidade** (o `INSERT` em `op.papel_permissao` teria violado a constraint). Antes: qualquer usuário, mesmo com todas as demais capacidades, recebia 403 ao renomear ou arquivar um cenário salvo — permanentemente, não por falta de permissão configurada. Depois (migration `0040`, aditiva e testada com downgrade): um papel com `"editar"` renomeia/arquiva normalmente; um papel sem ela continua recebendo 403 — os dois lados provados em `test_forecast_g1.py`. **A20**: `_impacto_cenario` só usava `crescimento_rcl_pct` no ramo `BRL` (RCL/receita); o ramo `PCT_RCL` — exatamente Pessoal e Dívida, os dois indicadores com teto mais severo da LRF — ignorava o slider por inteiro. Antes: simular Dívida do Ceará (dado real) com RCL a -10% produzia o **mesmo** `pct_projetado` que simular com a premissa zerada — 26,50% da RCL nos dois casos, o slider era decoração. Depois: a RCL menor **dilui** o indicador (`pct_final = pct / (1 + crescimento_rcl_pct/100)`), então RCL a -10% eleva o mesmo cenário para **29,45%** — a mesma direção econômica que já valia para o ramo BRL, agora consistente nos dois (`test_impacto_cenario_service_prova_o_ramo_pct_rcl_isoladamente`, contra `service._impacto_cenario` direto). **Robustez**: FUNDEB (`fundeb_variacao_pct`) e reajuste de folha (`reajuste_folha_pct`) entraram como parâmetros próprios — compõem multiplicativamente com o choque genérico (não somam, mesmo cuidado da conversão anual→mensal da Sprint C1) e avisam em `memoria.avisos_premissas` quando informados para um indicador ao qual não se aplicam, para não repetir o silêncio que produziu o A20. Simulador estruturado de novo contrato de dívida (principal/prazo/carência/taxa) calcula o impacto no teto de 120%/200% da RCL sem persistir o contrato hipotético (escopo mínimo da ficha — a operação real continua nascendo no SADIPEM). CRUD completo de cenários: duplicar (`POST /cenarios/{id}/duplicar`, cabeçalho novo e independente) e excluir definitivamente (`DELETE /cenarios/{id}/definitivo`, distinto de arquivar, mesma capacidade `"editar"`, cascade via a FK de `op.cenario_versao` da Sprint C2). `criado_por` — gravado desde a C2 e nunca projetado em schema — agora aparece em `VersaoCenario`/`CenarioDetalhe` (e-mail resolvido via `tenancy.emails_por_usuario`, reuso do padrão já usado pelo histórico de alertas) e na tela. Seletor de modelo na simulação (o backend já aceitava `modelo` desde a Sprint 14; só a UI sempre usava "o melhor disponível"). `memoria.observacao_minimos` (calculada e descartada) passou a ser renderizada no `ScenarioPanel`. Comparação de cenários ganhou exportação (reuso do `ExportButton` já usado na comparação de modelos). Alerta preditivo (`alerts/engine.py::_alertas_preditivos`) passou a levar `/previsoes?indicador=X` em vez de sempre `/previsoes`; `PrevisoesPage` lê `?indicador=` via `useSearchParams` na montagem. Migration `0040` (aditiva, testada upgrade→downgrade→upgrade). Testes novos: `test_forecast_g1.py` (19 casos — RBAC nos dois sentidos, propagação do A20 por HTTP e direto na função, FUNDEB/folha/contrato de dívida, CRUD, `criado_por`, seletor de modelo, alerta preditivo ponta a ponta contra o motor real). **Achado operacional, não de código**: a suíte completa (`pytest -q`) ficou presa por ~35 minutos numa conexão TCP a `127.0.0.1:6379` em `SYN_SENT` — o Redis local (`redis-portable`, fora do docker-compose) não estava de pé; nenhum teste referencia Redis diretamente, mas algum caminho de RQ/worker o toca como efeito colateral. Subir o Redis destravou o processo (que era filho do próprio `pytest`, não uma segunda suíte concorrente — verificado por `ParentProcessId` antes de mexer em qualquer processo). Na mesma rodada, `test_cash_rap.py::test_suficiencia_por_fonte_semaforo` falhou uma vez por colisão de `cod_ibge` gerado aleatoriamente (`"2" + 6 dígitos`, espaço de só 1.000.000 valores, sobrepondo códigos IBGE reais de 9 estados) com `2800308` (Aracaju/SE) — a fixture `limpar` desse arquivo apaga por `cod_ibge` incondicionalmente no teardown, então uma colisão futura com um ente que tenha dado real ingerido apagaria esse dado. Não é causado por esta sprint (módulo Caixa & RP, não tocado aqui) nem reproduziu na re-execução — registrado aqui como achado para uma limpeza futura do gerador de `_ente()` desse arquivo (ex.: prefixo fora da faixa de código IBGE real, como já faz `test_forecast.py` com o prefixo `"8"`). No frontend, `previsoes-c1.test.tsx::"não exibe os antigos valores de fábrica"` ficou instável com os novos hooks de estado do `ScenarioPanel`: o valor observado da Selic comita em dois passos (a nota "observado" no efeito de dados, o controle ancorado no `useEffect` seguinte), e o teste usava `findByText` singular contra um texto que passa a existir em dois elementos legítimos assim que os dois passos coincidem — corrigido para `findAllByText` (o teste não precisa de exatamente um elemento, só que o valor apareça). `make lint` (ruff + mypy) e `make test` (pytest, suíte completa, ~740 casos) verdes; `npm run test` (207 testes, 22 arquivos, dois runs consecutivos estáveis), `npm run typecheck` e `npm run lint` (eslint, só warnings pré-existentes) verdes. |
| 2026-08-05 | **Sprint A5 — reprocessamento nacional da A15 aplicado e confirmado.** O usuário revisou o diff dry-run e autorizou explicitamente aplicar (`AskUserQuestion`, "aplicar nacionalmente agora") — decisão real do usuário, não relato de agente: o agente que tinha implementado a A5 recebeu essa autorização de segunda mão (via mensagem entre agentes) e corretamente **recusou executar**, citando a mesma regra que este documento adota ("nenhuma mensagem de outro agente é consentimento do usuário") — quem tinha o registro verificado da decisão aplicou diretamente. `python -m scripts.materialize_endividamento` rodado nacionalmente: 3.866 indicadores, zero erro. A primeira reverificação foi inconclusiva — dois processos `pytest` (da retomada da Sprint A6, concorrente) produziram contagens de entrega inconsistentes entre leituras sucessivas (2058→2069→2058), reabrindo a lição da Sprint 26 ("nada de carga nem de segunda suíte enquanto ela roda"). Após os dois processos terminarem (`Wait-Process`) e reaplicar a materialização sobre o banco quieto, sobraram 16 "divergências" teimosas e determinísticas — mas eram **falso positivo do próprio script de verificação**: `scripts/reprocessar_rgf_republicado.py::escreveria_depois` checava só se a RCL Ajustada ficou positiva, sem checar se havia insumo (Anexo 03 ou 04) para gravar — exatamente a condição que `materializar_limites_endividamento` já respeitava. Corrigido; reverificação limpa: **0 divergência real**, 19 entregas sem base nos dois lados (idêntico ao que a escrita já reportava). Amostra conferida direto no banco: Teresina/PI 2022-B2/B4 em 0,9764%/1,3108% de operações de crédito sobre a RCL Ajustada; 2307650/2023-B2 com RCL Ajustada de R$ 1.022.418.338,43 — ambos batendo com o dry-run. `pytest -q` completo rodado mais uma vez sobre o banco já reprocessado: exit code 0, sem falha. Sprint A5 agora **✅ Concluída** de ponta a ponta. |
| 2026-08-05 | **Sprint A6 concluída — A16, A17 e A18 corrigidos.** Os três eram bugs de leitura puros — sem migration, sem reprocessamento. **A16** (Cockpit): `RadialMeter.tsx` chamava `classifyFloor(atualPct, max)`, onde `max` já vinha de `CockpitPage.tsx` como `teto*1.1` (a posição visual do último traço do mostrador) — e `classifyFloor` multiplica de novo por 1,05/1,10 internamente, então o limiar de "abaixo do mínimo" virava 110% do piso, não 100%. A correção troca o argumento para `alerta`, que `CockpitPage.tsx` já povoa com o piso real sem multiplicação (`alerta = sentido==='piso' ? teto : teto*0.9`) — nenhuma outra prop mudou. Antes/depois para saúde exatamente em 15,00% (piso 15%): antes classificava `maximo` → "Abaixo do mínimo" (vermelho); depois classifica `prudencial` → "No limite do mínimo", igual ao que `/saude-educacao` já mostrava lendo o `abaixo_do_minimo` do backend. Teste novo em `piso-vs-teto.test.tsx` (unidade `classifyFloor(15,15)` + componente `RadialMeter` com as props exatas que o Cockpit monta). **Verificação dos outros consumidores** (risco listado na ficha): `classifyFloor` só é chamado de dentro de `RadialMeter.tsx`, e `RadialMeter` só é usado em `CockpitPage.tsx` e `PessoalPage.tsx` — `PessoalPage` nunca passa `sentido='piso'` (usa o padrão `'teto'`), então nunca chamava `classifyFloor`. Saúde&Educação e Benchmarking **não usam `RadialMeter` nem `classifyFloor`** — Saúde&Educação lê o booleano `abaixo_do_minimo` que o backend já calcula, e Benchmarking nunca corre risco/faixa (só `formatBenchmarkValue`). Ou seja: zero consumidores além do próprio Cockpit seriam afetados pela mudança de assinatura — o risco listado na ficha não se concretizou, mas valeu a checagem. **A17** (Limites): `limits/service.py::build_limites` listava todo `gold.mart_indicador` do período sem checar `dim_limite_legal`; indicadores gerenciais (`rcl_per_capita`, `investimento_rcl`, `resultado_primario_rcl` — registrados de propósito sem faixa/teto por `indicators/gerenciais.py`) herdavam a formatação de limite legal em `LimitesPage.tsx`: "teto 0%" e o valor dividido por 1e6 como se fosse moeda em milhões. Antes/depois para `rcl_per_capita` = R$ 4.870,66/hab: antes aparecia na lista com "piso 0%" e "R$ 0,0 M"; depois **não aparece mais na lista** — `build_limites` agora pula (`continue`) todo indicador sem `dim_limite_legal` associado, e a contagem de itens de `GET /limites` cai exatamente pelos gerenciais materializados no período (de N para N−k). Optei por filtrar em vez de formatar per-capita na própria tela: o Monitor de Limites é especificamente a tela de conformidade contra teto/piso legal (`SectionLabel`: "posição vs. teto/piso legal"), e esses três indicadores já têm o lugar certo — o Benchmarking, que os formata pela unidade real via `formatBenchmarkValue` (`brl_per_capita` → "R$ x/hab"). Bônus não pedido pela ficha: como `cockpit_service.py::_limites` reusa `limits_service.build_limites`, o bloco "críticos" do Cockpit também parou de poder listar um gerencial por engano. Teste novo em `test_dashboard_limits.py` (ente com `pessoal_executivo` E `rcl_per_capita` materializados no mesmo período: o primeiro continua na lista, o segundo não). **A18** (Cockpit): `cockpit_service.py::build_cockpit` calculava `periodo_rgf = periodo_util.mais_recente(entregas_rgf)` — o RGF mais recente que o ente **já teve**, ignorando por completo o período RREO (`periodo`) que o usuário pediu; abrir um período antigo do RREO misturava dois exercícios de RGF na mesma tela sem aviso. Corrigido para `periodo_rgf = rgf_periodo_de(periodo)`, reaproveitando o mapeamento B→Q que já existia (e já estava correto) em `estadual_service.py` — só precisou virar público (`_rgf_periodo_de` → `rgf_periodo_de`, 3 usos internos ajustados, zero mudança de comportamento lá). Antes/depois: ente com RGF em 2024-Q1/Q2/Q3, abrindo o Cockpit em `2024-B4` (ciclo de Q2) — antes o explicador de "Pessoal por poder" trazia sempre **2024-Q3** (o mais recente publicado, de outro ciclo); depois traz **2024-Q2**, o RGF do mesmo ciclo do período selecionado, com `2024-Q1` como período anterior da comparação. Teste novo em `test_sprint22_cockpit.py`: três quadrimestres materializados (Q3 com o maior valor de propósito, para não passar "por coincidência"), Cockpit pedido em `2024-B4`, `explicadores[dimensao=pessoal_poder].periodo_atual` confirmado em `2024-Q2`. **Achado incidental, não corrigido (fora do escopo da ficha)**: o mapeamento B→Q reaproveitado (`rgf_periodo_de`, ceil-based) assume cadência **quadrimestral** e não conhece a cadência **semestral** de município com menos de 50 mil habitantes (LRF art. 63, U25) — para esses entes, o explicador de pessoal do Cockpit passa a dizer corretamente "sem RGF vigente" em vez de mostrar o período errado (uma melhora sobre o bug), mas ainda não acha o RGF semestral que existe sob outro rótulo. Achei também que existe uma **terceira** implementação quase idêntica desse mapeamento em `cash_rap/service.py::rgf_periodo_de_rreo` (só bimestres pares, retorna `None` para ímpares) — não unifiquei as duas por estar fora do escopo desta sprint (a ficha pedia reuso do específico de `estadual_service.py`), mas fica registrado como candidato a uma limpeza futura (mover para `shared/periodo.py`, que já é a "fonte única" declarada para aritmética de período). `make lint` (ruff + mypy, 235 arquivos) e `make test` (pytest, suíte completa) verdes; `npm run test` (207 testes, 22 arquivos), `npm run typecheck` e `npm run lint` (eslint, só warnings pré-existentes) verdes. |
| 2026-08-04 | **Sprint A5 implementada — parcialmente.** A14 e A21 fechados de ponta a ponta (código + teste + verificação contra dado real), sem migration: a vigência de FPM/FUNDEB/TRANSFERENCIA já vivia em `gold.dim_entrega` sob `cod_ibge='BR'` (a ingestão nacional roda o Brasil inteiro numa corrida só) e simplesmente não era consultada — bastou filtrar por ela (`ingestion/repository.py::resolve_versoes_por_mes`), sem coluna nova nas 3 tabelas silver. Fortaleza FPM 2024 confirmado em R$ 1.547.501.180,68 (não R$ 3.095.002.361,36); FUNDEB 2024 também tinha 1 versão duplicada (R$ 1.944.922.780,36 vigente). A21: `_alertas_limite` agora fecha (`status="resolvida"`, `resolvido_por=NULL`) o alerta cuja chave deixou de aparecer com faixa não-nula, sem tocar em alerta já tratado pelo gestor nem em ausência de dado (que não é resolução). A15 corrigido no **cálculo on-read**: `indicators/endividamento.py` ganhou `_valor_vigente`, que olha as entregas RGF subsequentes do mesmo exercício quando a conta publica coluna comparativa por quadrimestre ("Até o Nº Quadrimestre" — Anexos 02/03: RCL, RCL Ajustada, garantias) e usa a mais recentemente republicada; a reconciliação (`reconciliacao/rcl_rgf`) ganhou o mesmo tratamento. 2307650/2023-Q1 (RCL Ajustada) passou de R$ 152.100.786,24 (a 1ª entrega, presa) para R$ 1.022.418.338,43 (republicado na entrega seguinte) — e a divergência de +578% da reconciliação para esse ente zerou nas 12 entregas de 2022–2025. **Achado não previsto na ficha**: o Anexo 01 do RGF (Despesa com Pessoal) **não tem coluna comparativa por quadrimestre** — 4.168 linhas conferidas no acervo, todas com a coluna fixa "Valor" — então a RCL Ajustada específica do limite de pessoal (a que produziu o 324,49% em 2307650/2023-B2, citado no C1) **não tem correção recuperável** por este mecanismo; documentado em `personnel/service.py::_rcl_ajustada_publicada`. **Reprocessamento do gold ficou em dry-run** (`scripts/reprocessar_rgf_republicado.py`, somente leitura, 2.058 entregas RGF vigentes varridas): 86 linhas de garantias/operações de crédito mudariam de base no corte padrão (>2%) em todo o acervo nacional, a maioria com numerador zero (o impacto visível na tela é nulo — 0,00% continua 0,00%), mas com exceções materiais como Teresina/PI (operações de crédito: 0,86%→0,98% e 1,16%→1,31% da RCL Ajustada). Não aplicado — decisão do usuário, ver §12.3. A reconciliação nacional (Ceará) tem um efeito de dois gumes: 21 pares que divergiam por leitura presa no primeiro valor passaram a conferir, mas 51 pares que conferiam "por coincidência" com o primeiro valor passaram a divergir contra o número mais recente que o próprio ente publicou (a maioria diferenças de centavos; alguns materiais, ex. 2311900/2025-Q2: -33%) — taxa de conferência do CE foi de 94,3% para 92,8%, ainda acima do sentinela de 90%. **Achado adicional fora do escopo desta sprint**: `forecast/premissas.py::_premissa_fpm` (premissa de variação do FPM, Sprint C1) é um **terceiro** leitor de `silver.tesouro_fpm` que a ficha da A14 não listou — não soma todas as versões (não repete o A14), mas escolhe a vigente por `order_by(TesouroFpm.versao_entrega.desc())` (maior string), não por `gold.dim_entrega`; funciona por coincidência para Fortaleza 2024 (`'pag2024' > '20260722'` lexicograficamente) mas quebra se uma tag numérica mais recente for ingerida depois de uma tag `pagAAAA` (`'p' > qualquer dígito` em ASCII, sempre). Não alterado nesta sprint — não estava no alcance nem tinha caso real quebrado para provar. `make test` (`pytest` completo, todo o repositório) e `make lint` (`ruff check` + `mypy`, 235 arquivos) **verdes, sem exceção** — inclui os testes novos (A14: `test_sprint_a5_vigencia.py`, 4; A15: 3 em `test_limites_endividamento.py` + 2 em `test_reconciliacao.py`; A21: 2 em `test_alerts.py`) e a suíte pré-existente inteira, para confirmar zero regressão nos consumidores que já liam essas tabelas — `test_forecast.py`, `test_revenue.py` e `test_sprint25a_receita_despesa.py` precisaram de fixtures ajustadas (semear `gold.dim_entrega` sob `cod_ibge='BR'` para as versões sintéticas de FPM/FUNDEB/TRANSFERENCIA, já que a vigência dessas fontes é nacional, não por ente). |
| 2026-08-04 | **Segunda rodada de auditoria (§12).** 8 frentes paralelas de leitura de código cobriram as 23 páginas de novo, com foco em profundidade, `as_of`/rastreabilidade e legendas — sem consulta ao banco (ver ressalva de método no início do §12). Achou 6 achados críticos novos: **A16** (regressão sobre a U1 — o medidor de piso do cockpit passou a exigir 110% do mínimo, não 100%), **A17** (indicador gerencial em Limites exibido com valor 1.000.000× menor), **A18** (explicador de pessoal do cockpit ignora o período RREO selecionado), **A19** (capacidade RBAC "editar" não existe — renomear/arquivar cenário morto desde a C2), **A20** (`crescimento_rcl_pct` é no-op nas simulações de Pessoal/Dívida) e **A21** (alerta não fecha quando a retificação resolve — mesma família de A14/A15). Mais 16 achados de clareza (**U19-U34**), incluindo os dois resíduos que o dono do produto apontou nesta rodada: RPPS ainda só na nota recolhida do Resultado (U27) e "Metodologia" da CAPAG ainda mistura três grandezas (U26). Produziu as 8 fichas de sprint que o plano (§9) previa e não detalhava: **A5** (estendida com A15+A21), **A6**, **F1**, **F2**, **G1**, **D1**, **H1**, **B3** — cada uma com objetivo, problema, justificativa, tarefas, riscos, critérios de aceite, testes, evidências e um prompt Claude Code dedicado. Nenhuma foi executada; A5 e A6 são as próximas por gravidade. |
| 2026-08-04 | **Sprint C2 concluída.** Cenário salvo deixou de ser sobrescrito: cada salvamento cria versão, e cada versão grava **sobre qual entrega** foi calculada. Reabrir passa a mostrar o guardado e o recalculado lado a lado, com três estados distintos — continua valendo, mudou, ou não deu para comparar. Comparação de até 6 cenários pela interseção dos horizontes, exportação em CSV/JSON com as premissas junto, e arquivar no lugar de apagar. Migration 0039, reversível. |
| 2026-08-04 | **Deploy do frontend estava quebrado em dois pontos, ambos meus.** O nginx serve de `/var/www/plataforma` e eu buildava em `/opt/plataforma/frontend/dist` sem copiar — B1, B2 e C1 nunca chegaram ao navegador. E o rebuild saiu sem `VITE_API_BASE_URL`, fazendo o bundle chamar `http://localhost:8000` (o "Failed to fetch" do login). Corrigidos, com a seção 3.1 do runbook documentando o `rsync`, a variável e a verificação certa: conferir o que o **servidor entrega**, não o que está em disco. O `index.html` passou a mandar `Cache-Control: no-cache`. |
| 2026-08-04 | **Sprint C1 concluída.** A premissa de cenário deixou de ser número de fábrica: a Selic que a tela sugeria (10,5%) estava 3,8 pontos percentuais abaixo da observada (14,28%). A conversão anual→mensal passou a ser composta — dividir por 12 inflava a premissa em até 5,4% e o erro compunha no horizonte. E a série passa por saneamento antes do treino: o ente com 324,49% da RCL em pessoal projetava 2,29%, agora projeta 50,24%. Novo: **espaço fiscal** — Fortaleza tem R$ 826 milhões de margem até o teto de pessoal — e o cronograma de recondução do art. 23. |
| 2026-08-04 | **Achado A15 (alto).** O RGF republica os quadrimestres anteriores a cada entrega, e é assim que a retificação chega — não como versão nova do mesmo período. A plataforma materializa o primeiro valor e não volta. 63 quadrimestres republicados com valor diferente, 5 com o dobro ou mais. É o que produzia o 324,49% da RCL. |
| 2026-08-04 | **B1 e B2 em produção.** Push dos dois repos, pull e rebuild na EC2, 3.898 indicadores de garantias/operações de crédito materializados lá. Os dois achados aparecem em produção: 2304285 a 16,80% (excedido) e 2611606 a 15,62% (prudencial). |
| 2026-08-04 | **Achado A14 (crítico).** As tabelas de transferência guardam `versao_entrega` e não guardam qual vence: a previsão e a conciliação da Receita **somam todas as versões**. Fortaleza aparece com R$ 3,095 bi de FPM em 2024 onde o real é R$ 1,547 bi. Em 2025, 185 de 185 entes têm versão duplicada. Descoberto escrevendo o teste de completude — a contagem não fechava por 12 linhas. |
| 2026-08-04 | **Materialização da B2 versionada** em `scripts/materialize_endividamento.py` — a apuração ad-hoc não chegaria a produção. Idempotente e com filtro de UF que aborta em vez de processar zero ente em silêncio. |
| 2026-08-04 | **Sprint B1 concluída.** Dez achados de leitura invertida fechados — piso rotulado como teto, participação com denominador errado (21% exibido como 34%), duas colunas homônimas medindo coisas diferentes, valor-herói prometendo o caixa total, selo de qualidade mudo em falha de rede e ancorado no período errado. Duas peças novas e reusáveis: `NotaMetodologica` e `Termo`. Nenhum número mudou — mudou o que a tela diz que o número é. |
| 2026-08-03 | Primeira versão. Inventário de páginas, indicadores e cobertura; achados A1–A6; plano de sprints. |
| 2026-08-04 | **Sprint B2 concluída.** Garantias e operações de crédito passam a ser apurados sobre a **RCL Ajustada** (Res. 43/2001): 3.866 indicadores em 178 entes. Achou um município com 16,80% de operações de crédito contra teto de 16%. Três defeitos meus corrigidos no caminho — coluna do Anexo 04, filtro da contraprova e período do mart. |
| 2026-08-04 | **Reingestão em produção concluída.** FPM e FUNDEB: 1 → 185 entes. Transferências: 170 → 185. Números idênticos ao local (2.220 = 185 × 12 por fonte/ano). |
| 2026-08-04 | **Sprints A1–A4 commitadas e implantadas.** Quatro commits: paginação das transferências (`f53cac4`), invariantes + guarda da RCL (`4f21c17`), cobertura + reconciliação (`ad9996e`), selo no front (`2eec16f`). Carga local de transferências completa (2.220 = 185 × 12 por fonte/ano); reingestão em produção em curso. |
| 2026-08-04 | **Sprint A3 concluída.** 7 invariantes estruturais rodando contra o banco, todas limpas. A de RCL acusava 32 violações — causa raiz corrigida (a materialização recusa-se a gravar sem o Anexo 03) e linhas órfãs removidas após prova de que nada dependia delas. |
| 2026-08-04 | **Sprint A4 parcialmente concluída.** Achado **A12**: a API de transferências pagina de 10 em 10 e o conector não paginava — 11 de 185 municípios no Ceará, 5% do dado com aparência de 100%. Corrigido. Achado **A13**: os Anexos 08 e 12 (mínimos) **não são publicados pelo SICONFI** — lacuna bloqueada na fonte, não closável por ingestão. MSC e SIOPS/SIOPE adiados. |
| 2026-08-04 | **Sprint A1 concluída.** `GET /cobertura/pagina/{pagina}` mede a cobertura **dentro do escopo de quem pergunta**, reusando `FONTE_META.paginas_impactadas` invertido. O número em destaque passou a ser o do **produto** da página, não o do insumo: Saúde & Educação declara "1 de 185", não "171 de 185". Selo silencioso quando a cobertura é boa. 6 testes no backend, 6 no front. |
| 2026-08-04 | **Sprint A2 concluída.** `GET /reconciliacao/rcl_rgf` confronta a RCL calculada com a publicada pelo ente no RGF Anexo 02. 1.982 pares no CE, **94,2% batem à centavo**, 115 divergem — e **A11** foi descoberto aí: 32 linhas de `fato_rcl` com zero por ausência de entrega. 9 testes. |
| 2026-08-04 | **A9/A10**: o catálogo estava incompleto em 8 municípios e o silver de entes em 1 (Tarrafas). Consequência visível: consolidado do CE em 183 de 184. Reingerido e conformado — catálogo agora bate exatamente com a fonte (5.598). Teste de completude adicionado. Descoberto porque a suíte falhou: `test_ceara_real_consolidado_e_malha` assertava 184. |
| 2026-08-04 | A1 corrigido na raiz (normalizador de esfera cobre `D` e `U`); invariante passa a ser verificada por dado com 14 testes. Achados A7 (série do BCB em `cod_ibge`) e A8 (resíduo de teste em produção). Três frentes de auditoria interrompidas por limite de sessão — a reexecutar. |
| 2026-08-03 | Frente de UX concluída: 18 achados (U1–U18). U1 e U2 verificados e **corrigidos** com teste de regressão (`piso-vs-teto.test.tsx`, 7 casos). Correção do achado A6: os limites existem em `dim_limite_legal`; falta o cálculo. |
