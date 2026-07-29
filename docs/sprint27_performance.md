# Sprint 27 — HTTP cache, compressão e orçamento de performance

Evidência coletada em **2026-07-28**. Nenhum endpoint foi criado nesta sprint.
Este documento separa o contrato HTTP, os caches internos, a medição autenticada
dos endpoints, a latência SQL e os planos `EXPLAIN ANALYZE`.

> **Correção de 28/07 — os dois endpoints mais lentos estavam fora da medição.**
> A tabela de dez caminhos abaixo não incluía `GET /entes/{ibge}/cockpit` nem
> `GET /alertas`, que eram justamente os mais caros do produto: 7,7–8,4 s e 6,4 s.
> O orçamento fora declarado cumprido sobre uma amostra que não continha os piores
> casos. A causa e a correção estão na seção **Cockpit e fila de alertas**; a
> conclusão original ("os dez P95 HTTP ficaram abaixo de 500 ms") vale para os dez
> caminhos medidos, não para o produto inteiro.

## Contrato HTTP

`GoldHttpOptimizationMiddleware` atua somente nos `GET` analíticos existentes:

- `/entes`, `/periodos` e `/entes/{cod_ibge}/...`;
- `/benchmark...`, `/carteira/{resumo,entes,mapa}`;
- `/uf/{uf}/...` e `/geo/...`;
- `/admin/ingestion/{status,fontes,cobertura,data,retificacoes}`;
- `/admin/{qualidade,lineage}`.

Recursos operacionais privados — usuários, papéis, jobs, arquivos, conversas e
cenários salvos — não recebem ETag. `FileResponse` e respostas de streaming não
são consumidos nem transformados.

Para uma resposta JSON `200` elegível:

- `ETag: W/"..."` cobre método, caminho, query, corpo e um salt do principal;
- `Cache-Control: private, no-cache, max-age=0, must-revalidate`;
- `Vary: Authorization, Cookie, Accept-Encoding`;
- `If-None-Match` usa comparação fraca e devolve `304` sem corpo;
- o `304` preserva os validadores, CORS e `Server-Timing`.

O salt impede que o ETag de um principal gere `304` para outro tenant. O cache
HTTP é privado e sempre revalidado. `GZipMiddleware` é global, com limiar padrão
de 500 bytes e nível 6. O ETag é fraco porque cobre o JSON anterior ao gzip.

## Caches internos de páginas pesadas

Quatro leituras de alta cardinalidade usam cache em processo com TTL de 30
segundos e LRU limitado. A consulta ao cache ocorre **depois** de autenticação e
resolução de escopo:

- contexto MSC: ente, ano e versões DCA/MSC vigentes;
- ranking estadual: UF, filtros, códigos autorizados e versão/hash das entregas;
- carteira: organização, escopo, dimensão, grupo/tag e
  `count/max(atualizado_em)` do mart;
- cobertura: filtros, página e `count/max(atualizado_em)` do mart.

Retificação, atualização ou mudança de escopo produz outra chave. Ranking,
carteira e cobertura só armazenam conjuntos grandes (100 entes/códigos/linhas).
Os testes automatizados demonstram hit e miss quando a identidade muda.

## Instrumentação P95

Cada GET analítico recebe:

- `Server-Timing: app;dur=<ms>, app_p95;dur=<ms>;desc="rolling <n>"`;
- `X-Performance-Budget-Ms: 500`;
- `X-Performance-P95-Ms` e `X-Performance-Samples`.

A janela é limitada (256 amostras por padrão) e agregada por **template de
rota**, nunca pelo IBGE/UF concreto. O P95 usa nearest rank e registra
`performance_budget_exceeded` quando `p95 >= 500 ms`. A medição de aplicação
inclui endpoint, dependências, serialização e ETag; o gzip externo ocorre depois.

## Ambiente e volume

- PostgreSQL local 15.7, porta 5432;
- Alembic em `0033_sprint26_qualidade_lineage`;
- perfil SQL em transação read-only, `statement_timeout = 30s`;
- DSN, credenciais, tokens e parâmetros concretos omitidos.

| Relação | Linhas estimadas |
|---|---:|
| `gold.mart_cobertura_fonte` | 15.097 |
| `gold.mart_indicador` | 14.629 |
| `gold.mart_msc_rollup` | 13.006 |
| `gold.dim_entrega` | 7.398 |
| `gold.fato_disponibilidade` | 6.192 |
| `gold.dim_ente` | 5.595 |
| `gold.mart_carteira` | 3.105 |
| `gold.mart_benchmark` | 2.675 |
| `gold.dim_conta_pcasp` | 1.977 |

## Latência SQL

Após duas execuções de aquecimento, cada consulta foi executada 20 vezes na
mesma conexão read-only. O tempo inclui ida/volta SQLAlchemy e materialização
das linhas.

| Consulta | P50 ms | P95 ms | Máx. ms | Linhas |
|---|---:|---:|---:|---:|
| `01_msc_arvore` | 9,758 | 18,919 | 18,972 | 4 |
| `02_msc_matriz_mensal` | 6,629 | 16,329 | 18,099 | 12 |
| `03_ranking_uf` | 38,901 | 49,038 | 55,037 | 170 |
| `04_carteira_entes` | 15,472 | 21,347 | 31,786 | 169 |
| `05_benchmark_ranking` | 63,009 | 202,108 | 271,648 | 176 |
| `06_cobertura_total` | 7,087 | 10,310 | 10,436 | 1 |
| `07_cobertura_pagina` | 45,549 | 88,607 | 92,686 | 595 |
| `08_dashboard_indicadores` | 6,042 | 8,592 | 20,619 | 14 |
| `09_receita_arvore` | 11,599 | 16,743 | 22,972 | 46 |
| `10_despesa_arvore` | 22,385 | 36,517 | 49,642 | 162 |

Todos os P95 SQL ficaram abaixo de 500 ms.

## Latência HTTP autenticada

Uma instância limpa de um worker foi exercitada via loopback com autenticação,
escopo, todas as consultas do serviço, Pydantic, JSON, ETag e transporte HTTP.
Foram duas chamadas de aquecimento e 20 amostras medidas por endpoint, em
sequência.

| Endpoint | Runs | HTTP P50 ms | HTTP P95 ms | App P50 ms | App P95 ms | Máx. HTTP ms | Bytes |
|---|---:|---:|---:|---:|---:|---:|---:|
| `01_msc_arvore` | 20 | 85,620 | 95,742 | 75,060 | 85,610 | 489,215 | 1.177 |
| `02_msc_matriz_mensal` | 20 | 115,607 | 181,255 | 104,440 | 166,890 | 184,806 | 2.529 |
| `03_ranking_uf` | 20 | 283,568 | 499,237 | 263,510 | 464,020 | 3.449,444 | 50.492 |
| `04_carteira_entes` | 20 | 236,641 | 313,295 | 215,250 | 282,060 | 374,803 | 85.714 |
| `05_benchmark_ranking` | 20 | 165,387 | 251,008 | 140,860 | 215,830 | 266,920 | 80.679 |
| `06_cobertura_total` | 20 | 91,452 | 109,974 | 80,480 | 97,780 | 127,554 | 1.344 |
| `07_cobertura_pagina` | 20 | 125,784 | 174,559 | 106,040 | 151,510 | 180,600 | 123.758 |
| `08_dashboard_indicadores` | 20 | 152,927 | 178,124 | 139,990 | 166,030 | 191,449 | 1.455 |
| `09_receita_arvore` | 20 | 118,783 | 136,989 | 107,390 | 126,180 | 171,388 | 670 |
| `10_despesa_arvore` | 20 | 155,359 | 208,300 | 143,110 | 194,930 | 275,006 | 5.874 |

Os dez P95 HTTP ficaram abaixo de 500 ms. O ranking ficou próximo do limite e
teve um outlier de 3.449,444 ms, preservado na tabela. A validação final inclui
uma amostra específica 2+100 do ranking, em nova instância limpa:

| Endpoint | Warmup | Runs | HTTP P50 ms | HTTP P95 ms | App P50 ms | App P95 ms | Máx. HTTP ms | Bytes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `03_ranking_uf` | 2 | 100 | 275,962 | 422,734 | 257,510 | 389,280 | 838,685 | 50.492 |

A amostra ampliada confirmou margem de aproximadamente 77 ms no P95 e não
repetiu o outlier de 3,4 s. O ensaio local serial não substitui teste
concorrente no deploy.

## Materialização da cobertura

O perfil da suíte identificou duas fontes de custo em
`refresh_cobertura`: uma consulta de `RawPayload` por linha e cerca de 15 mil
UPSERTs individuais. A implementação final:

- faz um prefetch agrupado por `(fonte, cod_ibge, periodo, versao)` e preserva
  o fallback nacional ao escolher o menor `ingerido_em` entre ente e `BR`;
- envia UPSERT multi-values em chunks de 500 linhas;
- conta silver por subconsulta correlacionada, dirigindo o plano aos índices
  compostos existentes `(cod_ibge, periodo,versao_entrega)` mesmo quando as
  estatísticas/autovacuum estão atrasados após backfills.

O teste dominante da Sprint 21 caiu de **268,32 s** para **79,56 s** na suíte
completa (redução de 70,3%); uma execução isolada marcou 73,56 s. A suíte
Sprint 21 passou 17/17, e o teste de unidade da Sprint 27 prova o fallback
ente/`BR` e que 1.001 linhas geram três batches. Não foi criada migration.

## EXPLAIN ANALYZE final

A captura final integral e não contraditória dos dez caminhos está em
[`sprint27_explain_analyze.txt`](sprint27_explain_analyze.txt). Resumo:

| # | Endpoint/consulta | Execução | Principal acesso |
|---|---|---:|---|
| 1 | MSC árvore | 14,993 ms | `ix_mart_msc_rollup_children` |
| 2 | MSC matriz mensal | 0,872 ms | `ix_mart_msc_rollup_conta` |
| 3 | ranking UF | 22,722 ms | `gold.fato_rcl`, PKs de ente/entrega |
| 4 | carteira | 4,450 ms | `ix_mart_carteira_periodo_indicador` |
| 5 | benchmark | 2,099 ms | `ix_mart_benchmark_consulta` |
| 6 | cobertura — total | 4,458 ms | `ix_mart_cobertura_fonte_filtro` |
| 7 | cobertura — página | 24,761 ms | PK de cobertura após filtro externo |
| 8 | dashboard | 0,739 ms | `ix_mart_indicador_ente_periodo` |
| 9 | receita | 2,746 ms | `uq_fato_receita_chave` |
| 10 | despesa | 8,214 ms | `ix_fato_despesa_ente_periodo` |

O `Seq Scan` do ranking percorreu apenas 3.179 linhas de `fato_rcl`; os scans
sequenciais das dimensões de receita/despesa percorrem somente 78/239 linhas.
Forçar índices nesses três casos seria mais caro. O filtro externo redundante da
cobertura evita o scan completo do mart. Não foi necessária migration de índice.

## Reprodução

```powershell
.\.venv\Scripts\python.exe -u scripts\profile_sprint27.py --latency-runs 20

$env:SPRINT27_HTTP_STATE_EMAIL = "<conta-estadual>"
$env:SPRINT27_HTTP_STATE_PASSWORD = "<segredo>"
$env:SPRINT27_HTTP_MSC_EMAIL = "<conta-municipal>"
$env:SPRINT27_HTTP_MSC_PASSWORD = "<segredo>"
.\.venv\Scripts\python.exe -u scripts\profile_sprint27_http.py `
  --base-url http://127.0.0.1:8011 --warmup 2 --runs 20 --timeout 10
```

Os scripts descobrem parâmetros de alta cardinalidade no banco, não imprimem
segredos, limitam cada operação e escrevem Markdown em stdout.

## Cockpit e fila de alertas — o caminho que faltava medir

A auditoria de acessibilidade denunciou o problema por outro caminho: quatro rotas
não terminavam de carregar em 30 s. Três eram lentidão do servidor de
desenvolvimento; a `/dashboard` era lentidão de verdade.

Perfilando `build_cockpit` camada a camada (sessão quente, segunda chamada):

| Camada | Antes | Depois |
|---|---:|---:|
| `_riscos` (motor de alertas) | 4.822,5 ms | 374,2 ms |
| `_tendencias` (previsão on-read) | 1.047,9 ms | 384,8 ms |
| demais nove camadas somadas | ~453 ms | ~138 ms |
| **`build_cockpit` inteiro** | **7.268 ms** | **1.008 ms** |

`cProfile` isolou a causa: `_alertas_minimos` respondia por **10,6 s dos 12,3 s** do
motor porque chamava `health_edu.build_projecao`, que reapura saúde e educação e
monta a série de cinco exercícios — **622 idas ao banco por requisição**. A regra
usa apenas o veredito "a trajetória fura o piso?" de dois domínios; a série era
computada e descartada. Pior: no **6º bimestre** a regra descarta tudo (quem fala no
fechamento é o alerta de limite) — e descartava **depois** de pagar os 10,6 s, que é
exatamente o período vigente de Fortaleza.

Duas correções, ambas na origem:

1. `_alertas_minimos` sai **antes de projetar** quando `bimestre >= 6`.
2. Novo `health_edu.projetar_minimos` devolve só as duas projeções, sem a série.
   `build_projecao` (usado pela tela, que precisa da série) segue intacto.

Efeito no HTTP autenticado, mesmo host:

| Endpoint | Antes | Depois |
|---|---:|---:|
| `GET /entes/{ibge}/cockpit` | 7,7–8,4 s | 1,4–2,4 s |
| `GET /alertas?escopo=ente` | 6,4–6,5 s | 0,65–0,90 s |

**O que continua fora do orçamento, dito com todas as letras:** o cockpit ficou em
~1,4 s, acima dos 500 ms. O que sobra não é desperdício — é cálculo real de uma
página que a Sprint 22 desenhou **sem mart próprio**: o motor de alertas avalia (e
grava) na leitura, e `_tendencias` roda os modelos de previsão a cada carga. Fechar
o orçamento exige decisão de arquitetura, não ajuste: mover a avaliação de alertas
para o job de carga (como a Sprint 26 fez com os checks de qualidade), deixando a
leitura só lendo `op.alerta`, e consumir `gold.fato_projecao` em vez de recalcular.
Fica registrado como dívida com causa conhecida, não como critério atingido.

## Acessibilidade — o que a auditoria encontrou de fato

A suíte axe rodava contra o **dev server**, e nele quatro rotas não terminavam de
carregar em 30 s: o teste abortava antes de auditar. Trocado o alvo para o **bundle de
produção** (`build` + `vite preview`) — que é o artefato que o gestor recebe —, a suíte
caiu de 14,8 min para 4,7 min e passou a auditar as 19 rotas.

Aí apareceram **24 violações reais** em 6 rotas, todas de quatro causas:

| Causa | Onde | Correção |
|---|---|---|
| `opacity: 0.7` sobre texto ⇒ 3,02:1 | Benchmarking (rótulos das coortes) | hierarquia por família/peso; transparência derruba qualquer token |
| `faint` sobre `accentSoft` ⇒ 4,34:1 | linha selecionada das tabelas | token → `#626E64` (4,60:1) |
| `muted` sobre `yellowSoft` ⇒ 4,02:1 | caixa de aviso | token → `#536156` (4,65:1) |
| `serieA` como **cor de texto** ⇒ 3,93:1 | Receita, coluna de conciliação | novo `serieAInk` (5,40:1) |

Mais duas, fora de contraste: região rolável sem foco de teclado (Patrimônio) e botão
de enviar sem nome acessível (Assistente).

O par `serieA`/`serieAInk` merece registro: cor de série é validada como **marca
gráfica** (3:1, SC 1.4.11) e reprova como **texto** (4,5:1, SC 1.4.3). São critérios
distintos, e o mesmo hex não serve para os dois papéis.

O teste unitário de contraste existia e passava — porque aferia os tokens só contra
`bg` e `surface`. Foi por essa fresta que as 24 violações passaram. Agora ele afere
também `accentSoft` e `yellowSoft`, e separa cor de traço de tinta de texto.

**Resultado:** 19/19 rotas sem violação axe (WCAG 2.0/2.1/2.2, níveis A e AA).

Também corrigido no caminho um defeito de produto: `useResource({pular})` mantinha
`loading` ligado **sem chamada nenhuma para resolvê-lo**. Um ente sem período de RGF
via um esqueleto girar para sempre na Central de Caixa — pior que dizer "não há",
porque promete um dado que não vem. O recurso ganhou estado próprio (`indisponivel`) e
a página declara a ausência com o motivo.

## Lighthouse — medido, não estimado

Executado em 28/07 nas cinco páginas mais pesadas (preset desktop, bundle de produção,
sessão autenticada real). Era o critério que estava configurado e nunca rodado.

| rota | acessibilidade | performance | LCP | TBT |
|---|---:|---:|---:|---:|
| `/benchmarking` | 99 | 75 | 0,88 s | 0,01 s |
| `/carteira` | 99 | 73 | 1,08 s | 0,06 s |
| `/patrimonio` | 99 | 59 | 2,86 s | 0,00 s |
| `/central-dados` | 99 | 46 | 2,82 s | 0,00 s |
| `/dashboard` | 99 | 32 | 3,31 s | 0,28 s |

**Acessibilidade ≥ 95: atingido** (99 nas cinco).
**Performance ≥ 80: não atingido em nenhuma.**

O diagnóstico contraria a conclusão fácil. O aviso do build sobre o *chunk* de 570 kB
convida a culpar o bundle — mas o **TBT é praticamente zero** em todas as páginas: o
JavaScript quase não bloqueia a thread principal. O que derruba a nota é o **LCP**: a
página não pinta o conteúdo principal enquanto espera a API.

A correlação fecha: `/benchmarking` e `/carteira`, cujos endpoints respondem em ~0,3 s,
são as que mais se aproximam de passar; `/dashboard`, com o pior LCP, é a do cockpit
que ainda mede 1,4–2,4 s no backend. **Fazer code-splitting aqui melhoraria pouco** —
o caminho é o mesmo já registrado acima: tirar avaliação de alertas e previsão do
caminho de leitura. A dívida é uma só, e aparece nas duas medições.

## Critério de deploy

O banco e o ensaio HTTP serial local atendem:

```text
p95(GET de página) < 500 ms
```

A promoção deve repetir a medição com volume e concorrência do ambiente alvo.
Violações aparecem no log por template de rota, sem IBGE, query ou credenciais.
