# Sprint 25C — Saúde & Educação

> Auditoria §2.9. Faltavam: série plurianual visível, comparação com pares, exportação,
> memória em diálogo e o alerta de risco de descumprimento. Os endpoints `/saude/serie`,
> `/educacao/serie`, `/saude/memoria` e `/educacao/memoria` existiam desde a Sprint 11 e
> nenhuma tela os consumia.

**Aceite:** o gestor vê em **uma tela** — % atual, piso, projeção, série de exercícios,
posição na coorte e SIOPS/SIOPE com selo.

---

## 1. A decisão estrutural: o mart passou a dizer qual é o 100%

Para comparar ASPS/MDE com a coorte, os mínimos precisavam existir em
`gold.mart_indicador` — que até aqui só guardava percentuais **da RCL** (a coluna se
chama `valor_pct_rcl`). Os mínimos constitucionais têm outra base:

| Indicador | Base (100%) | Piso | Norma |
|---|---|---|---|
| `saude_minimo` | receita de impostos e transferências | 15% mun. / 12% est. | CF art. 198; LC 141/2012 |
| `educacao_mde` | receita de impostos e transferências | 25% | CF art. 212 |
| `fundeb_profissionais` | receitas principais do FUNDEB | 70% | Lei 14.113/2020 art. 26 |

Gravar 25,3% de impostos numa coluna que o produto inteiro lê como "% da RCL" seria
publicar um número certo com rótulo errado. **Migration 0031** acrescenta:

- `gold.mart_indicador.denominador` (`NOT NULL DEFAULT 'rcl'`) — o que é 100% na linha.
  O default preserva o significado de todas as 6.210 linhas já gravadas, sem backfill.
- `gold.mart_indicador.base_valor` — o denominador em R$: o percentual fica reconferível
  na própria linha (`pct = valor_rs ÷ base_valor × 100`), sem voltar ao fato de origem.
- `gold.mart_benchmark.unidade` vira `text` — `percentual_impostos_transferencias` não
  cabia no `varchar(20)` (defeito encontrado ao rodar contra o dado real).

`valor_pct_rcl` manteve o nome físico: dez módulos a leem, e renomear seria um refactor
sem valor para o gestor. Quem **apresenta** o número passou a ler `denominador`.

### Quem foi corrigido junto (senão passariam a mentir)
- `/limites` — cada item devolve `denominador`/`base_valor`; a tela escreve "de impostos
  + transferências" sob o valor. Antes da 25C a lista só tinha indicadores de RCL.
- Semáforo do dashboard — a mensagem era `"({pct} da RCL)"` fixa; agora usa a base do
  indicador. Com Fortaleza real, "Educação (MDE) em faixa 'insuficiente' (22,6% **dos
  impostos e transferências**)".
- Benchmark — `unidade` derivada da linha e **exclusão de pares com base divergente**:
  comparar 27% de impostos com 47% de RCL produziria um ranking sem sentido. O número
  de excluídos vai na memória (`entes_excluidos_por_base_divergente`).

`classificar_sobre_base()` (em `indicators/service.py`) é o caminho novo; a classificação
de faixa continua vindo de `indicators/limites.py` — fonte única de verdade (§7).

---

## 2. Série plurianual: dois gráficos porque são duas perguntas

`GET /entes/{ibge}/{saude|educacao}/serie?periodo=&anos=5` devolve:

- **`data` — um ponto por exercício**, no período mais avançado que aquele ano publicou
  (B6 quando fechado). É a série comparável: 25% acumulados no 2º bimestre e 25% no 6º
  não medem a mesma coisa, então bimestres de anos diferentes nunca entram lado a lado.
  Cada ponto declara `exercicio`, `parcial` e `estagio_legal` (liquidado × empenhado).
- **`trajetoria_exercicio`** — o acumulado bimestre a bimestre **do exercício
  consultado**. Responde "estamos no caminho do piso?", que é a pergunta operacional.

E a **cobertura viaja junto**: `exercicios_com_dado`, `exercicios_sem_dado`,
`cobertura_completa` e uma `observacao` em português. Dizer "série de 5 anos" e devolver
um ponto sem explicar por quê é esconder a lacuna de ingestão.

### Cobertura real (limite conhecido, não defeito de código)
Fortaleza tem os Anexos 8/12 **só de 2024**. Motivo apurado nesta sprint:

- A **API do SICONFI não publica os Anexos 8 e 12**. Verificado ao vivo: `tt/rreo` para
  2304400/2023-B6 devolve 11 anexos (01, 02, 03, 04, 06, 07, 09, 10, 11, 13, 14) e o
  filtro `no_anexo=RREO-Anexo 08|12` volta vazio. É por isso que existe o conector
  `siconfi_rreo_minimos_pdf`, que raspa o PDF oficial do portal do município.
- O portal de Fortaleza **tem** os PDFs de 2021, 2022 e 2023 (6 bimestres cada), mas o
  parser recusa os dois layouts anteriores — e recusa alto, como foi desenhado:
  - **2022**: numeração de linhas diferente (transição do Novo FUNDEB) — a linha 20 é
    "PERCENTUAL DE 50% DA COMPLEMENTAÇÃO DA UNIÃO (VAAT)", não "TOTAL DAS DESPESAS COM
    AÇÕES TÍPICAS DE MDE".
  - **2023**: o PDF tem camada de texto com as **colunas separadas dos rótulos** — as
    linhas numéricas saem sem a conta a que pertencem.

  Ampliar o parser é trabalho de ingestão com risco alto de leitura desalinhada (um
  deslocamento de coluna produziria um percentual plausível e errado). Fica registrado
  para a **Sprint 26** (qualidade/contratos de dados). Enquanto isso a tela declara
  "1 de 5 exercícios apurados; sem dado em 2020, 2021, 2022, 2023".

O mesmo vale para a coorte: só Fortaleza tem o indicador entre os 15 municípios do porte
"1 milhão ou mais". A tela **recusa** montar ranking com menos de 3 entes — percentil e
posição com N=1 não significam nada — e explica a causa.

---

## 3. Alerta de risco de descumprimento (Sprint 11 → motor da 15)

Regra nova `_alertas_minimos` em `alerts/engine.py`, consumindo `build_projecao` sem
recalcular nada. A distinção que ela faz é jurídica, não estatística:

- **Bimestres 1–5**: acumulado abaixo do piso é **risco** (`categoria=preditivo`,
  `severidade=atencao`) — ainda dá para corrigir, e a ação sugerida diz explicitamente
  que "projeção não é apuração".
- **6º bimestre**: a apuração é definitiva. A regra se cala e quem fala é o alerta de
  limite, com faixa `insuficiente` e severidade crítica. Dois alertas para o mesmo fato
  seriam ruído.

Com dado real de Fortaleza: em **2024-B5** o MDE acumulava **24,05%** contra piso de 25%
→ um alerta de risco; a saúde, com 25,79% contra 15%, não gera nada. Em **2024-B6** o MDE
fechou em **25,28%** — cumpre, e nenhum alerta é emitido.

---

## 4. A tela

`SaudeEducacaoPage` foi reescrita em torno das sete perguntas (documentadas no topo do
arquivo). Reusa o padrão transversal da Sprint 25: `FonteChip`, `MemoriaDialog`,
`SerieChart`, `ExportButton`, `ArvoreDrill`, `Async/Skeleton`.

`SerieChart` ganhou `formato="pct"` e `limiar` (linha de referência legal):
- percentual não se deflaciona nem se divide por população — os modos real/per capita
  somem em vez de aparecerem quebrados;
- a linha do piso é tracejada **e rotulada** (cor sozinha não informa), a escala sempre
  contém o limiar, e pontos abaixo do piso ficam vermelhos com o motivo no tooltip e na
  tabela equivalente.

Memória de cálculo saiu do corpo da página para diálogo, carregada sob demanda pelos
endpoints `/memoria` — com a fonte de **cada componente** (a linha do expurgo aponta o
RGF Anexo 5, não o Anexo 12).

---

## 5. Números reais (Fortaleza, 2304400)

| Período | ASPS | MDE | FUNDEB |
|---|---|---|---|
| 2024-B1 | 18,95% | 12,90% | 77,44% |
| 2024-B3 | 24,17% | 22,64% | 95,72% |
| 2024-B5 | 25,79% | 24,05% ⚠ | 97,72% |
| 2024-B6 | **27,14%** | **25,28%** | **98,19%** |

Pisos: ASPS 15%, MDE 25%, FUNDEB 70%. A trajetória do MDE — cinco bimestres abaixo do
piso e fechamento acima — é exatamente o caso que a distinção risco × descumprimento
existe para tratar.

---

## 6. Testes

- `tests/test_sprint25c_saude_educacao.py` (10): mart com base própria e faixa por piso;
  FUNDEB sobre a base do fundo; `/limites` e semáforo com o denominador certo; série com
  um ponto por exercício e `parcial`; exercícios sem dado declarados; trajetória separada
  da série; benchmark posicionando na coorte com a unidade correta; exclusão de par com
  base divergente; alerta de risco no bimestre intermediário e silêncio no fechamento.
- `src/test/sprint25c.test.tsx` (13): as sete perguntas na tela, mais as três recusas
  honestas (coorte pequena, série de um exercício, enriquecimento que não altera o piso).

`ruff` + `mypy` (198 arquivos) + `pytest` verdes; `tsc` + `vitest` + `build` verdes.
