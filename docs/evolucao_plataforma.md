# Evolução da Plataforma Prumo — auditoria técnica, funcional e conceitual

> Documento vivo. Fonte central de acompanhamento da evolução da plataforma.
> **Vive em `backend_plataforma_fiscal/docs/`** e é versionado com o código. Ficou
> fora do git de 2026-08-03 a 2026-08-04, na raiz do projeto, onde nenhum dos dois
> repositórios o rastreava.
> **Iniciado em:** 2026-08-03 · **Última atualização:** 2026-08-04
> **Estado:** auditoria em andamento. Sprints concluídas: **B0, A1, A2, A3, A3a, A3b, B1,
> B2, C1, C2**; **A4 parcial** (mínimos bloqueados na fonte). Aberto e crítico: **A14** e
> **A15** — a mesma família, *versão que existe, vigência que não se declara*. Três frentes
> de diagnóstico interrompidas por limite de sessão, a reexecutar (§20, P2–P4).

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

### 5.1.2 Frentes de auditoria interrompidas

As frentes **fiscal/contábil**, **dados/rastreabilidade** e **arquitetura/segurança**
foram interrompidas por limite de sessão antes de produzirem relatório. O que cada uma
alcançou antes de parar está registrado como pendência em §20, com o ponto exato em que
estavam:

- fiscal: ia validar os achados invocando os serviços de ponta a ponta;
- dados: ainda na leitura de contexto e docs;
- arquitetura: ia testar empiricamente exposição de recurso entre organizações (404 × 403).

**Nenhuma conclusão dessas três frentes foi incorporada** — relatório não produzido não é
relatório parcial, é ausência. Serão reexecutadas.

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
| **A0** | Diagnóstico completo e este documento | fiscal, dados, UX, arquitetura | — | ⏳ **Em andamento** — frentes de UX e de dados concluídas; fiscal, rastreabilidade e arquitetura interrompidas por limite de sessão (§20, P2–P4) |
| **B0** | Correções de sentido já verificadas (U1, U2) | UX | A0 | ✅ **Concluída** |
| **A3a** | Invariante da esfera verificada por dado (A1) | fiscal, testes | A0 | ✅ **Concluída** |
| **A3b** | Completude do catálogo de entes (A9, A10) | dados, testes | A3a | ✅ **Concluída** |
| **A1** | Cobertura honesta: cada página declara para quantos entes/períodos responde | dados, UX | A0 | ✅ **Concluída** |
| **A2** | Reconciliação com fonte oficial: painel de divergências | fiscal, dados | A0 | ✅ **Concluída** (motor + endpoint; triagem persistida fica para A2b) |
| **A3** | Invariantes do domínio verificadas por dado (esfera, denominador, faixa) | fiscal, testes | A0 | ✅ **Concluída** — 7 invariantes, 0 violadas |
| **A4** | Fechar as lacunas de ingestão (mínimos, MSC, SIOPS/SIOPE, FPM) | dados | A1 | ⚠️ **Parcialmente concluída** — FPM/transferências corrigidos (11→185); mínimos **bloqueados na fonte**; MSC e SIOPS adiados |
| **A5** | **A14** — eleger a versão vigente das transferências e impedir a dupla contagem | dados, fiscal | A4 | 🔴 **Próxima** — identificada e quantificada, correção não iniciada |
| **B1** | Clareza conceitual: notas metodológicas, glossário, ano-base × publicação | UX, fiscal | A0 | ✅ **Concluída** — 10 achados de leitura invertida fechados; `NotaMetodologica` e `Termo` como peças reusáveis |
| **B2** | Limites ausentes: garantias e operações de crédito | fiscal | A3 | ✅ **Concluída** — 3.866 indicadores, 178 entes; achou 1 ente com o teto de operações de crédito **excedido** |
| **C1** | Previsões e cenários "E se?" em nível governamental | previsão, fiscal, UX | B1 | ✅ **Concluída** — premissas ancoradas no observado (a Selic de fábrica estava 3,8 p.p. fora), conversão composta, saneamento da série e **espaço fiscal em reais**; achou o **A15** |
| **C2** | Cenários salvos: persistência, versão, comparação, exportação | previsão, arquitetura | C1 | ✅ **Concluída** — versão em vez de sobrescrita, procedência do dado gravada, reabertura que compara guardado × hoje, comparação por interseção e exportação com premissas junto (migration 0039) |
| **D1** | Drill-down por órgão, fonte de recurso, programa/ação | dados, UX | A4 | Planejada |
| **E1** | Segurança, isolamento entre organizações e desempenho | arquitetura, segurança | A0 | Planejada |

*(Cada sprint recebe ficha detalhada — objetivo, problema, justificativa, páginas afetadas,
tarefas, riscos, critérios de aceite, testes, evidências — quando entra em execução.)*

---

## 10. Decisões técnicas e metodológicas registradas

| Data | Decisão | Motivo |
|---|---|---|
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
| P2 | **Frente fiscal/contábil** | Interrompida (limite de sessão) | Auditoria de fórmulas: RCL × RCL Ajustada, exclusões do art. 19 §1º, DTP como composição × subtração, resultado acima × abaixo da linha | Reexecutar quando o limite renovar |
| P3 | **Frente de dados/rastreabilidade** | Interrompida | Levantamento de `source_ref` endpoint a endpoint; varredura de valores fixos; execução dos 9 checks de qualidade | Reexecutar |
| P4 | **Frente de arquitetura/segurança** | Interrompida | Teste empírico de exposição entre organizações (404 × 403); regra fiscal duplicada; endpoints sem `assert_ente_in_scope` | Reexecutar |
| P5 | **Divergência conhecida do DTP** | Pendente desde antes desta auditoria | `DTP (VI) = (IIIa + IIIb)` tratado como composição em vez de `bruta − exclusões`; 6 divergências em 2024 e 18 em 2025 | Depende de P2 |
| P6 | ~~Contagem de `dim_ente` não explicada~~ | ✅ **Resolvido** | A investigação levou a A9/A10: o catálogo estava incompleto em 8 municípios e o silver de entes em 1. Após reingestão e conformação, o catálogo bate **exatamente** com a fonte: 5.570 municipais + 27 estaduais (26 + DF) + 1 federal = **5.598**, o total publicado pelo SICONFI | — |

---

## 11. Histórico de atualizações

| Data | Alteração |
|---|---|
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
