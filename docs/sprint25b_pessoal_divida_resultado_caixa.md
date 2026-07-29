# Sprint 25B — Pessoal, Dívida, Resultado e Caixa

Walkthrough sobre **Fortaleza (2304400)** — RGF 2024-Q3 e RREO 2024-B6, dado real do SICONFI
já no banco, verificado em execução.

## 1. `/pessoal` — a página que não existia

O indicador-assinatura da LRF (art. 20) tinha quatro endpoints prontos desde a Sprint 7 e
**nenhuma tela** (auditoria §2.19). As 10 perguntas do domínio e onde são respondidas:

| # | Pergunta | Bloco | Endpoint |
|---|---|---|---|
| 1 | Estou dentro do limite? | Medidor do Executivo, faixa esfera-aware | `/pessoal` |
| 2 | Quanto falta para o teto? | Folga em p.p. **e em R$** (p.p. × RCL) | `/pessoal` |
| 3 | Quem gasta o quê? | Por poder, cada um com a sua faixa | `/pessoal/por-poder` |
| 4 | Qual órgão puxa a folha? | Árvore poder → órgão | `/pessoal/arvore` |
| 5 | O que entra e sai da conta? | Memória com exclusões do art. 19, §1º | `/pessoal/memoria` |
| 6 | Qual é o denominador? | RCL 12m com deduções | `/entes/{ibge}/rcl` |
| 7 | Como a folha vem andando? | Série RGF nominal × real × per capita | `/pessoal` |
| 8 | Para onde vai? | Projeção com IC e cruzamento do teto | `/projecao?indicador=pessoal` |
| 9 | E se eu reajustar a folha? | Simulador de impacto | `/limites/pessoal_executivo/simular` |
| 10 | Posso confiar? | `FonteChip` + estado vazio nomeado | — |

Números reais observados: Executivo **47,21%** da RCL (teto 54%, faixa normal, folga 6,79 p.p.);
Legislativo 1,62% (sem teto próprio — a tela diz isso em vez de inventar faixa); exclusões com
`inativos_pensionistas_rpps` **não aplicada** (Fortaleza não tem RPPS); simulador com +R$ 500 mi
na folha leva de `normal` (47,21%) para **`prudencial` (51,58%)**.

### Defeito encontrado com dado real (e corrigido)

O `mart_indicador` e a RCL são apurados no **período RREO** (`2024-B6`), enquanto a página de
Pessoal navega em período **RGF** (`2024-Q3`). Pedir `/rcl` ou simular o limite com o período RGF
devolvia `404 — Sem RREO vigente`. Em vez de duplicar a regra Q1→B2 / Q2→B4 / Q3→B6 no
frontend, o backend passou a **declarar** o par: `PessoalDetalhe.periodo_rreo` (mesmo padrão que
`CaixaDetalhe.periodo_rreo` usa desde a Sprint 10). A tela pede RCL e simulação nesse período.

## 2. Meta fiscal da LDO (decisão §11.5 da auditoria)

O Anexo 6 existe para 176 entes do CE, mas **a linha de meta não vem preenchida** para Fortaleza
em nenhum exercício. Sem cadastro manual, o bloco de meta nasceria vazio. Decisão adotada:

- **`op.meta_fiscal`** (migration 0030, reversível): meta declarada pela organização, com RLS por
  `org_id`, `fonte_declarada` obrigatória e chave `(org_id, cod_ibge, exercicio, indicador)`.
- **A meta oficial do A6 sempre vence.** A manual só entra quando o ente não publicou.
- **A manual não sai da tela do ente.** `/resultado/meta` (tela) considera o cadastro;
  `/resultado` (detalhe, que alimenta relatório institucional e agregados) **só vê meta oficial**.
  O guardrail é o parâmetro `org_id` do `build_meta`: quem não passa org não enxerga cadastro.
- Cadastro exige capacidade **administrar** e vai para `op.audit_log` (`meta_fiscal.salvar` /
  `meta_fiscal.excluir`) com ente, exercício, valor e fonte declarada.
- A resposta carrega `origem` (`a6` | `manual` | `ausente`) e `restrita_ao_ente`; a tela rotula
  "Cadastro da organização · não sai desta tela".

## 3. Séries comparáveis nos quatro módulos

`indicators/serie_ajuste` (Sprint 25A) passou a alimentar Pessoal, Dívida, Resultado e Caixa: cada
série traz o valor **real** (a preços do período consultado, IPCA série 433) e **per capita**, com
a fonte declarada e sem inventar valor quando falta deflator ou população. Exemplos reais:

| Módulo | Série | Observação |
|---|---|---|
| Pessoal | 9 períodos RGF (2022-Q1…2024-Q3) | folha 2024-Q3 R$ 5,59 bi · R$ 2.169/hab |
| Dívida | 9 quadrimestres | DCL 2024-Q3 R$ 3,97 bi · R$ 1.544/hab |
| Resultado | 18 bimestres | primário 2024-B6 R$ 220,1 mi |
| Caixa | 3 exercícios (Q3) | RPNP sem lastro 2023-Q3 R$ 97,9 mi → real R$ 102,7 mi |

## 4. Fim dos endpoints ociosos nos quatro módulos

| Endpoint | Onde passou a ser consumido |
|---|---|
| `/pessoal`, `/pessoal/arvore`, `/pessoal/memoria`, `/pessoal/por-poder` | página `/pessoal` |
| `/entes/{ibge}/rcl` | card do denominador em `/pessoal` |
| `/limites/{indicador}/simular` | simulador de impacto em `/pessoal` |
| `/resultado/memoria` | diálogo de memória em `/resultado` |
| `/caixa/memoria`, `/caixa/arvore`, `/caixa/rpnp-sem-lastro` | `/caixa` (memória, árvore por fonte, vista dedicada) |

**Backend novo:** `GET /entes/{ibge}/divida/pvl` (PVL/CDP do SADIPEM). A silver está vazia para
Fortaleza: a resposta diz *"a fonte SADIPEM ainda não foi ingerida para ele — ausência de
ingestão não significa ausência de pedidos"*, em vez de sugerir "nenhum pedido".

## 5. Outras decisões

- **Dívida × coorte:** o `mart_indicador` tem `divida_consolidada_liquida` para 178 entes, então a
  comparação com pares usa o benchmark da Sprint 13 (mediana/p10/p90 + cobertura da coorte),
  jamais média de percentuais.
- **`useResource` ganhou `pular`:** as páginas seguravam uma requisição com período vazio antes de
  o contexto resolver o período do ente (a chamada nascia inválida). O flag evita a ida ao
  servidor sem mudar o estado exibido (segue em carregamento).
- **Consolidado do ente × Executivo:** a tabela por poder mostra o consolidado (teto 60%/50%) e o
  Executivo (54%/49%) lado a lado, com a nota de que somar percentuais de poderes não produz
  limite algum.
