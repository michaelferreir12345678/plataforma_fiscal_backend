# Sprint 25A — Receita & Despesa: enriquecimento gerencial

Walkthrough sobre **Fortaleza (2304400), 2024-B6** — dado real do SICONFI já no banco,
verificado em execução (não há número de exemplo neste documento).

## 1. Perguntas gerenciais × onde a tela responde

Nenhum componente novo entrou sem uma pergunta da auditoria (§2.4 receita, §2.5 despesa).

### Receita (`/receita`)

| # | Pergunta | Componente | Endpoint |
|---|---|---|---|
| 1 | Quanto entrou e quanto era previsto? | Cabeçalho + Realização | `/receita`, `/receita/realizacao` |
| 2 | Quanto da minha receita é minha? | Barra própria × transferida + maiores transferências | `/receita`, `/receita/dependencia` |
| 3 | De onde vem cada real? | `<ArvoreDrill>` Categoria→Origem→Espécie→Rubrica→Alínea | `/receita/arvore` |
| 4 | O previsto foi revisado ou requentado? | Colunas previsto inicial × atualizado × arrecadado na árvore | `/receita/arvore` |
| 5 | Cresceu de verdade? | `<SerieChart>` nominal × real (IPCA) × per capita | `/receita` (`serie`, `serie_ajuste`) |
| 6 | O RREO bate com o que a União/FNDE repassou? | Conciliação de transferências | `/receita/transferencias/conciliacao` |
| 7 | Como este número foi apurado? | `<MemoriaDialog>` | `/receita/memoria` |
| 8 | Como levo isto para a reunião? | `<ExportButton>` (CSV + relatório institucional) | Sprint 16 |
| 9 | Este dado ainda vale? | `<FonteChip>` (relatório/anexo/período/versão + selo de defasagem) | todos |
| 10 | E se não houver dado? | `EmptyState` nomeado, com CTA para a Central de Dados | — |

### Despesa (`/despesa`)

| # | Pergunta | Componente | Endpoint |
|---|---|---|---|
| 1 | Quanto já comprometi do orçamento? | Cabeçalho empenhado × dotação × % RCL | `/despesa` |
| 2 | Onde a execução trava? | Cascata dotação→empenhado→liquidado→**pago** | `/despesa/estagios?eixo=natureza` |
| 3 | Quanto vira restos a pagar? | Lacunas + inscrito em RAP | `/despesa/estagios` |
| 4 | Estou no ritmo do calendário? | Ritmo × esperado linear do bimestre | `/despesa/execucao?eixo=natureza` |
| 5 | Quanto consigo mexer? | Rigidez por GND (rígida/semivariável/discricionária) | `/despesa/rigidez` |
| 6 | Em que se gasta? | `<ArvoreDrill>` com eixo função **e** natureza | `/despesa/arvore` |
| 7 | Cresceu de verdade? | `<SerieChart>` nominal × real × per capita | `/despesa` |
| 8 | Os dois eixos fecham? | Memória com reconciliação função × natureza | `/despesa/memoria` |
| 9–10 | Como levo adiante / dá para confiar? | `<ExportButton>`, `<FonteChip>`, `EmptyState` | — |

Com isso a auditoria §6 zera para os dois módulos: `/receita/memoria`, `/receita/dependencia`,
`/receita/realizacao`, `/despesa/arvore`, `/despesa/memoria`, `/despesa/estagios`,
`/despesa/execucao` e `/despesa/rigidez` passam a ter consumidor.

## 2. Decisões que não se deduzem do código

### 2.1 Deflação e per capita (`indicators/serie_ajuste.py`)

- O ajuste é **calculado no backend**, não na tela: o IPCA (série 433 do SGS/BCB, ingerida na
  Sprint 1B) e a população (IBGE) são dado, e o fator vai na resposta com a fonte.
- Base = **o período consultado** (`serie_ajuste.base_periodo`), cujo fator é sempre 1.
- **Mês faltante não vira inflação zero:** sem algum mês do intervalo, o fator daquele ponto é
  `null`, o modo "real" fica desabilitado e a `observacao` diz quais períodos ficaram de fora.
- **Per capita usa a população do ano do ponto**, com `pop_ano_ref` na resposta. Sem estimativa
  do ano, cai para o ano anterior mais próximo e a tela mostra qual ano foi usado — em Fortaleza
  os pontos de 2022/2023 usam a estimativa de 2021, os de 2024 usam a de 2024.
- Fortaleza 2024-B6: 18 pontos de série (2022-B1…2024-B6); a inflação acumulada de 2022-B1 até a
  base dá fator 1,1424 — a série nominal cresce bem mais do que a real.

### 2.2 Conciliação de transferências: equivalência × contenção

O RREO Anexo 01 **não obriga** o ente a abrir FPM/FUNDEB em linha própria. Fortaleza publica só
até a espécie ("Transferências da União e de suas Entidades"), então a comparação 1:1 não existe.
A conciliação passou a ter duas bases, declaradas em `base_comparacao`:

| base | Como compara | Status possíveis |
|---|---|---|
| `linha_especifica` | igualdade dentro da tolerância de 1% | `conciliado`, `divergente` |
| `agregado` | **contenção**: a parte tem de caber no todo | `contido`, `excede_agregado` |
| `ausente` | não compara | `sem_par_rreo` |

- `divergencia_pct` só existe na base específica; no agregado devolvemos
  `participacao_no_agregado_pct` (percentual da transferência dentro do agregado). Percentual de
  "divergência" contra um agregado seria uma mentira aritmética.
- **FUNDEB não tem agregado inequívoco** (a distribuição mistura cotas da União, do estado e do
  próprio município): sem linha própria fica `sem_par_rreo`, em vez de comparar contra o agregado
  errado e inventar um alarme.
- Séries **derivadas do próprio RREO** (ICMS/IPVA cota-parte, Sprint 21) vêm com
  `independente=false` — batem por construção e não valem como contraprova.
- Cada item carrega os **dois lados**: `tabela_externa` + `periodo_externo` (janela agregada) e
  `nos_rreo` (códigos casados no Anexo 01).

Resultado real em Fortaleza 2024-B6: FPM externo R$ 1,55 bi **contido** nos R$ 3,96 bi de
transferências da União informadas (39,1% do agregado); cota-parte dos estados bate exatamente
(marcada como não independente); FUNDEB fica `sem_par_rreo`.

### 2.3 O eixo importa: o Anexo 02 não publica "pago"

A cascata e o ritmo de execução usam o **eixo natureza (Anexo 01)** porque o Anexo 02 (função) não
traz a coluna de despesas pagas — no eixo função, `pago` e o potencial de RAP simplesmente não
existem. Duas consequências:

- `/despesa/estagios` devolve `estagios_ausentes[]` + `observacao` apontando o anexo que publica o
  estágio faltante, em vez de uma cascata que para em "liquidado" sem explicação.
- `/despesa/execucao` ganhou o parâmetro `eixo` (default `funcao`, compatível): numerador e base
  saem sempre do mesmo anexo. O frontend pede `natureza`.

Fortaleza 2024-B6, eixo natureza: dotação inicial 12,39 bi → atualizada 13,96 bi → empenhado
13,39 bi → liquidado 13,08 bi → **pago 13,04 bi**; inscrito em RAP 313,5 mi; potencial de RAP
(empenhado − pago) 350,4 mi.

**Achado honesto:** os dois eixos **não fecham** para Fortaleza (função 14,27 bi × natureza
13,39 bi; diferença 875,6 mi). Isso já é sinalizado por `/despesa/memoria`
(`reconciliacao_eixos_ok=false`) e agora aparece na tela — é qualidade de dado da fonte, e nenhum
dos dois valores é "corrigido".

### 2.4 Exportação: CSV no cliente, XLSX/PDF no servidor

`<ExportButton>` gera **CSV da tabela visível** (separador `;`, BOM UTF-8 — o destino é o Excel em
pt-BR) com cabeçalho de proveniência (ente, período, fonte, gerado_em). Não geramos `.xlsx` no
navegador: arquivo que o Excel abre com aviso não é entrega. O documento institucional
(PDF/XLSX) continua sendo o da Sprint 16, e o botão leva ao formulário já pré-preenchido.

### 2.5 Cores de série fora da paleta de risco

Gráfico ganhou `colors.serieA`/`serieB` próprios: verde, amarelo, laranja e vermelho estão
reservados às faixas da LRF e não podem significar "linha 2". O par foi validado para daltonismo
sobre fundo branco (ΔE deutan 9,5 · tritan 13,2 · normal 18,9) e a distinção não depende só de
cor — traço sólido × tracejado, legenda e rótulo direto.

## 3. Limites conhecidos

- A **série mostra o que está na gold**: períodos entregues mas nunca materializados não aparecem
  (a lacuna é assunto da cobertura/Central de Dados, não vira zero na tela).
- A série de despesa é apurada no eixo função (Anexo 02) — é o eixo com histórico completo;
  cascata e ritmo, que precisam de "pago", usam natureza.
- `/receita/memoria` acusa 2 inconsistências de agregação em Fortaleza 2024-B6 (nó × soma dos
  filhos). São reportadas como qualidade de dado; o valor oficial do RREO permanece.
