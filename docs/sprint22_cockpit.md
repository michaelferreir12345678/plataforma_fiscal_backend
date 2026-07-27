# Sprint 22 — Cockpit Executivo Fiscal: walkthrough e decisões

## 1. As perguntas-guia e onde o cockpit as responde

Walkthrough sobre **Fortaleza (2304400), 2024-B6** — dado real, verificado em execução.

| # | Pergunta | Camada | O que aparece |
|---|---|---|---|
| 1 | Estou bem? | 1 · Resumo | Farol derivado da **pior faixa legal** apurada, nº de indicadores e de alertas |
| 2 | O que mudou? | 1 · Resumo | Δ contra o período anterior, em p.p., com destaque quando **muda de faixa** |
| 3 | O que está apertado? | 2 · Críticos | Valor, limite, faixa e **distância em p.p.** até o teto (ou acima do piso) |
| 4 | Para onde vai? | 3 · Tendências | Série + projeção com **IC** e o período em que cruza o limite |
| 5 | Por que mudou? | 4 · Explicadores | Top-3 componentes por \|Δ\|: receita por origem, despesa por função, pessoal por poder |
| 6 | Comparado a quê? | 5 · Comparações | Período anterior, mesmo período do exercício anterior, mediana da coorte, orçamento |
| 7 | Sou exceção ou regra? | 5 · Comparações | Mediana da coorte no mesmo período (nº de entes informado) |
| 8 | O que fazer? | 6 · Riscos | Alerta + **fundamento legal** + ação sugerida + prazo + link |
| 9 | Qual o prazo? | 6 · Riscos | `prazo` do alerta (quando a norma o fixa) |
| 10 | Posso confiar no dado? | 7 · Qualidade | Por fonte: período mais recente, **defasagem**, retificações, última carga |

Saída real observada em 2024-B6 (Fortaleza): farol `conforme`; pessoal 47,21% (teto 54%,
folga 6,79 p.p.); DCL 34,75% (teto 120%); a projeção do pessoal **cruza o teto em 2025-B3**;
o explicador de receita aponta `ReceitasCorrentes` +R$ 2,14 bi e `TransferenciasCorrentes`
+R$ 1,24 bi entre B5 e B6; a comparação com o exercício anterior mostra DCL 24,58% → 34,75%.

## 2. Decisões de arquitetura

**Sem `gold.mart_cockpit`.** O cockpit **compõe** em service o que os módulos já calculam.
Duplicar a verdade num mart próprio significaria que uma mudança na regra de limite
(em `indicators`) passaria a exigir dois lugares para corrigir.

**Comparação sem base nunca vira zero.** Todo item traz `disponivel` + `motivo_indisponivel`.
Ex.: a DCL não é apurada em bimestres ímpares (o RGF é quadrimestral), então "período
anterior" para 2024-B6 responde *"não apurado em 2024-B5"* em vez de exibir 0.

**Severidade vem da faixa legal.** A UI não escolhe adjetivo: `cor`/`faixa` chegam do
domínio (`dim_limite_legal` → `indicators`). A tela apenas traduz para rótulo.

**Período não vem mais do ambiente.** O contexto pergunta a
`/entes/{ibge}/periodos` quais períodos o ente **tem** e adota o mais recente. Trocar de
ente invalida o período — o app não pode ficar preso num período que o novo ente não possui.

**RREO × RGF por rota, declarado num lugar só.** `ROTAS_RGF` no `AppShell` substitui o
antigo `periodoRgf` condicional espalhado.

## 3. Defeito encontrado — e corrigido na origem

Ao construir o explicador de receita, o dado real revelou que **`gold.dim_origem_receita`
continha a seção de despesa** do RREO Anexo 01 (que é o *Balanço Orçamentário*: receita **e**
despesa).

**Causa raiz:** `natureza.classificar_coluna` mapeava a coluna pela substring
`"ATÉ O BIMESTRE"` / `"NO BIMESTRE"` — que também aparece nas colunas de **despesa**
(`"DESPESAS EMPENHADAS ATÉ O BIMESTRE (f)"`). Cinco colunas de despesa viravam medidas de
arrecadação; as linhas entravam no `fato_receita` e, por consequência, o construtor de
árvore as transformava em nós pendurados sob `ReceitasDeCapital`.

**Correção (na origem):** `classificar_coluna` rejeita o vocabulário de despesa
(`DESPESA`, `DOTACAO`, `EMPENHAD`, `LIQUIDAD`, `PAGAS`, `INSCRITAS EM RESTOS`) **antes**
das regras de arrecadação. Sem medida válida, a linha de despesa nunca chega ao construtor
de árvore. Regressões em `tests/test_correcoes_origem.py`.

**Expurgo:** `scripts/corrigir_receita_despesa.py` removeu **13 nós** de `dim_origem_receita`
e **19.299 linhas** de `fato_receita`. O critério exige que o nó tenha linhas de despesa e
**nenhuma** de receita — um nó sem linha alguma é órfão de carga antiga (ex.:
`OperacoesDeCredito`, que é receita de capital legítima) e é preservado.

A mitigação temporária do cockpit (`_e_despesa`) foi **removida**: a correção agora é da
materialização, não do consumidor.

## 4. DDCL: haveres são opcionais

`fato_divida` cobria 79 de 178 entes. A causa **não** era ausência de Anexo 02 (todos os 178
têm), e sim `Demais Haveres Financeiros` ser exigido como componente obrigatório —
e o RGF só publica essa linha quando o ente possui tais haveres (**90 dos 178 não possuem**).

`calcular_dcl` passou a tratar `haveres` como **opcional, com valor zero**, mantendo
`dc_bruta` e `disponibilidades` obrigatórios. A suposição fica **rastreável**: um
`ComponenteDdcl` com a conta `"(linha ausente no DDCL — assumido zero)"` entra na memória de
cálculo. O zero é a leitura conservadora — sem a dedução, a DCL apurada é maior, nunca menor.

**Resultado final: 178/178 entes com Anexo 02 têm `fato_divida` (100%).** Os "8 entes com
DDCL quebrado" que eu havia reportado eram um **erro meu de diagnóstico**: são as 8 capitais
NE fora do Ceará (prefixos IBGE 21–29), com DDCL íntegro, apenas excluídas do
`materialize_sprint21 --uf 23`. Materializá-las (dado já no silver, sem rede) fechou a lacuna.
Foi o **terceiro** diagnóstico equivocado de dívida na sessão — lição registrada: verificar a
hipótese contra o dado antes de rotular algo como "quebrado na fonte".
