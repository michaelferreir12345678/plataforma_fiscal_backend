# Sprint 26 — Qualidade, atualização e observabilidade dos dados

> Auditoria §§4, 7.3, 9. As invariantes fiscais existiam **só dentro dos testes**: se um
> dado chegasse corrompido em produção, nada perguntaria "os filhos somam o pai?". E o
> caminho fonte→página vivia implícito no código — ninguém respondia "o que quebra se o
> RGF falhar?".

---

## 1. O que foi construído

**Migration 0033** — duas tabelas na gold (qualidade e linhagem descrevem o dado público
compartilhado, não o operacional de uma organização):

- `gold.data_quality_check` — resultado de cada verificação com **os dois lados** da
  comparação, diferença e tolerância. Guardar só um booleano tornaria o veredito
  indiscutível; guardando a conta, quem discorda refaz.
- `gold.lineage_edge` — o grafo fonte→bronze→silver→gold→endpoint→página, mantido por
  **código** (seed idempotente, 122 arestas).

### Os nove checks

| Check | Compara | Tolerância |
|---|---|---|
| `receita_soma_filhos` | cada origem-pai × Σ filhos diretos | R$ 1,00 |
| `despesa_estagios_monotonicos` | empenhado ≥ liquidado ≥ pago | R$ 1,00 |
| `rcl_calculada_vs_publicada` | `fato_rcl` × RREO Anexo 03 publicado | R$ 1,00 |
| `dcl_a6_vs_rgf` | DCL do Anexo 6 × DCL do RGF Anexo 2 | R$ 1,00 |
| `msc_vs_dca` | conciliação patrimonial da Sprint 12 (agora persistida) | R$ 1,00 |
| `minimo_saude/educacao_recalculado` | pct gravado × recalculado dos componentes | 0,01 p.p. |
| `mart_vs_detalhe_pessoal` | `mart_indicador` × `fato_pessoal ÷ RCL` | 0,01 p.p. |
| `freshness_{rreo,rgf,dca,msc}` | atraso além do prazo legal, por cadência | SLA por fonte |
| `contrato_layout` | parser que recusou o arquivo | — |

### Três decisões que valem por todo o módulo

1. **Ausência não é falha.** Ente que não publicou o Anexo 12 não "falhou no check" — ele
   vira `aviso` com motivo. Confundir os dois transformaria lacuna de ingestão em
   acusação de erro contábil, e o painel diz a diferença em texto.
2. **Tolerância é do domínio.** Somas de centenas de linhas em `Decimal` acumulam
   centavos; a tolerância é declarada por check e viaja no resultado.
3. **Aviso é o degrau entre ok e falha.** Divergência até 10× a tolerância pede olhar
   humano, não alarme.

---

## 2. Uma regra fiscal que o dado real ensinou

O check de soma pai=filhos falhava em `ReceitasDeCapital`. Antes de "ajustar a
tolerância", fui ver: entre os **179 entes** com Anexo 01 em 2024-B6, **todos** os pais
fecham exatamente — exceto `ReceitasDeCapital` nos **51 entes** que reportam
`SaldoDeExerciciosAnterioresUtilizadosParaCreditosAdicionais`, e neles a diferença é
exatamente o valor dessa linha.

Não é erro do ente: é **linha de memória** do demonstrativo — superávit financeiro de
exercício anterior usado para créditos adicionais, que é fonte de financiamento, não
receita arrecadada no período. O check a exclui, declara a exclusão no `detalhe` e o
código registra a evidência. Depois disso, os 11 pais de Fortaleza fecham em R$ 0,00.

---

## 3. Quando os checks rodam

- **Ao fim de cada carga** (`_recalcular` do job da Sprint 24): nenhum dado entra em
  produção sem passar pelas invariantes. O resultado vai no `resultado` do job e a lista
  `indicadores_recalculados` passa a incluir `gold.data_quality_check`.
- **Agendado** (`workers/quality_tasks.py`): a fonte que **parou de chegar** não aparece
  em nenhuma carga — se ninguém carrega, nenhum job roda, e é aí que o dado envelhece sem
  que ninguém veja.
- **O próprio agendamento é monitorado**: ciclo perdido vira check em falha
  (`execucao_agendada`). Um monitor que morre calado é pior que nenhum monitor.

Toda falha vira alerta na fila da organização, com link para `/central-dados?painel=qualidade`.
Categorias novas: `qualidade_dado` (crítico) e `falha_ingestao` (atenção, para freshness).
Na ordem de prioridade, **qualidade vem antes de prazo**: um número errado é pior que um
número atrasado, porque o gestor pode estar decidindo com ele agora.

---

## 4. Na tela

- **Central de Dados** ganhou as abas **Qualidade** (falhas primeiro, com os dois lados
  da conta e filtros por status/fonte) e **Lineage** (as duas perguntas lado a lado). O
  alerta linka com `?painel=qualidade` e a página abre na aba certa.
- **Selo de qualidade** nas páginas fiscais e no cockpit: quando há check em falha sobre
  os números da tela, o dado continua aparecendo **com a ressalva** e o caminho para a
  conta que não fechou. Esconder seria pior; apresentar como conferido, também.
  O selo é **silencioso** quando há só aviso ou está tudo certo — selo verde em toda tela
  vira ruído e ensina a ignorar o vermelho.
- **Cockpit**: `confiavel` passa a cair com check em falha mesmo com a fonte em dia —
  dado atual e errado é pior que dado velho e correto.

### Com dado real (Fortaleza, 2024-B6)
8 checks `ok`, 2 `aviso` (MSC não ingerida), 3 `falha` — todas de **freshness**, e
verdadeiras: o banco de desenvolvimento tem carga até 2024 e a data de hoje é jul/2026.
O lineage responde: `silver.siconfi_rreo` alimenta **10 páginas**; `/saude-educacao` vem
de SICONFI/tt-rreo + SIOPS + SIOPE + FNDE/FUNDEB.

---

## 5. As três correções herdadas da conversa

1. **Seletor de entes no construtor de relatórios.** `lote`/`estadual` pegavam o escopo
   inteiro sem seleção — para a Sefaz, um clique enfileirava **185 relatórios**. O
   backend sempre aceitou `entes: [...]`; faltava a tela deixar escolher. Nada
   selecionado continua valendo "todos", agora como escolha declarada. Entes sem dado
   ingerido aparecem marcados.
2. **Truncamento declarado no seletor de ente.** A lista mostra 30 de 185 e agora diz
   isso ("mostrando 30 de 185 — digite para filtrar · escopo total: 185"). Foi o que
   levou a crer que a conta da Sefaz não podia escolher municípios.
3. **Ente estadual no topo do seletor**, separado dos municípios, com a nota de que o
   Governo do Estado **não** é a soma dos municípios.

### Sobre "ver o estado como um todo" — a parte que não se deve fazer
São duas coisas distintas, e a plataforma já as separa desde a Sprint 23:

- **Governo do Estado (ente `23`)** — as contas do próprio estado. **Existe e tem dado**:
  RCL, 5 indicadores no mart, receita, despesa, pessoal, dívida e balanços 2021–2024. A
  conta `admin@sefaz.gov.br` já o enxerga (escopo de 185 entes); o que faltava era achá-lo
  na lista alfabética — corrigido no item 3.
- **"O estado inteiro" = estado + 184 municípios** — **não se soma**. A cota-parte do
  ICMS/IPVA sai da receita do estado e entra na dos municípios: somar conta o mesmo real
  duas vezes. E os limites da LRF são **por ente**: não existe "RCL do Ceará inteiro" com
  significado legal. Por isso o consolidado da UF é só de municípios, com `observacao`
  explicando, e o ente estadual fica em aba própria.

---

## 6. Testes

- `tests/test_sprint26_qualidade_lineage.py` (18): cada check com **ok / falha / aviso**;
  linha de memória não quebra a soma; estágio não publicado não reprova; SLA por cadência
  em dia/aviso/falha; contrato de layout persistido; execução agendada perdida; **E2E**
  (corrompe a RCL ⇒ check falha ⇒ alerta com link ⇒ aparece no painel com os dois lados);
  aviso não vira alerta; painel respeita escopo; cockpit sela e derruba `confiavel`;
  lineage nos dois sentidos, idempotente, com 404 para nó inexistente e **cobertura de
  100% das páginas do produto**.
- `src/test/sprint26.test.tsx` (12): painel abre pela URL do alerta, mostra os dois lados,
  distingue "não deu para verificar", explica o vazio; lineage nos dois sentidos; selo
  presente na falha e **calado** em aviso/tudo-certo; e as três correções herdadas.
- `tests/test_ingest_worker_resilience.py` atualizado: o contrato pós-job mudou de
  propósito (passa a incluir `gold.data_quality_check` e o resumo `qualidade`).
- `src/test/central-dados.test.tsx` passou a montar a página dentro de um `MemoryRouter`
  — ela lê `?painel=` e, no app, sempre vive dentro do Router.

`ruff` + `mypy` (208 arquivos) + `pytest` **352 testes** verdes; `tsc` + `vitest`
**98 testes** + `build` verdes.
