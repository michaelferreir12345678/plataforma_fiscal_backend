# Plataforma de Inteligência Fiscal — Backend

API + modelo de dados (SICONFI) para o gestor público. Este repositório é o **backend**
(separado do frontend). Contexto e regras em [CLAUDE.md](../CLAUDE.md); roadmap em
[backend-sprints.md](../backend-sprints.md).

**Stack:** Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · Alembic · PostgreSQL 16
(ltree + RLS) · pytest · ruff · mypy.

## Sprint 0 — Fundação & multi-tenant (implementada)

- Scaffold FastAPI (§5) + `docker-compose` (api, postgres, redis) + Alembic.
- Módulo **tenancy** (schema `op`): organizações, usuários, papéis/capacidades (RBAC),
  memberships, escopo de carteira, auditoria.
- **RLS por `org_id`** no PostgreSQL (plano de dados isolado) com contexto injetado por
  transação (`app.current_org_id` / `app.current_user_id` / `app.is_admin`).
- **Auth** OAuth2 password + JWT; **RBAC** por capacidade; **escopo** multi-tenant (§6.4).
- Padrões reutilizáveis (§6): `hierarchy` (ltree + envelope de drill), `envelope`,
  `pagination`, `source_ref`, dependência de escopo e **middleware de auditoria**.
- `make seed`: 1 município (prefeitura) + 1 estado (Sefaz).

### Critérios de aceite — status
| Critério | Status |
|---|---|
| RLS por `org_id` ativa | ✅ (`test_rls.py`, validado como role não-superuser) |
| Login JWT | ✅ (`test_auth.py`) |
| Usuário fora de escopo → 403 | ✅ (`test_scope.py`) |
| `shared/hierarchy.py`, `envelope.py`, `source_ref.py` testados | ✅ (`test_hierarchy_envelope.py`) |
| `make seed` cria 1 município + 1 estado | ✅ (`scripts/seed.py`) |

## Sprint 1 — Ingestão SICONFI & medallion bitemporal (implementada)

- **Framework de ingestão reusável** (§6.7) em [shared/ingestion/base.py](src/app/shared/ingestion/base.py):
  `BaseConnector` com `discover → extract → to_bronze → to_silver → mark_done`,
  idempotência por `(fonte, ente, periodo, versao)`, *rate-limit* (~6 req/s) e *backoff*
  (`tenacity`). Persistência via porta `MedallionSink` (a Sprint 1B reusa sem alterar).
- **Conectores SICONFI**: RREO, RGF, DCA, MSC, extratos, entes
  ([connectors/siconfi.py](src/app/modules/ingestion/connectors/siconfi.py)), com cliente
  HTTP injetável ([client.py](src/app/shared/ingestion/client.py)).
- **Medallion**: `bronze.raw_payload` (JSONB, particionada por `LIST(fonte)`, imutável),
  `silver.siconfi_*` tipado (com `valid_time` + `versao_entrega`), `gold.dim_entrega`
  (controle de versões/retificação: nova versão vigente **sem apagar** o histórico).
- **Bitemporal / as_of** (§6.5): `gold.dim_entrega` resolve a versão vigente ou a que
  estava vigente em um instante passado.
- **Endpoints admin** (capacidade `administrar`): `GET /admin/ingestion/status`,
  `POST /admin/ingestion/run` (backfill), `POST /admin/ingestion/replay` (reprocessa do
  bronze, sem rede), `GET /admin/ingestion/data?...&as_of=` (leitura histórica).
- Orquestração RQ em [workers/ingestion_tasks.py](src/app/workers/ingestion_tasks.py)
  (executa síncrono no MVP; enfileirável quando houver Redis).

### Critérios de aceite — status
| Critério | Status |
|---|---|
| Rodar 2x não duplica | ✅ (`test_ingestion.py::test_rodar_2x_nao_duplica`) |
| Retificação supera versão e mantém histórico | ✅ (`test_retificacao_supera_versao_e_mantem_historico`) |
| `dim_entrega.vigente` correto | ✅ (mesmo teste) |
| `as_of` retorna versão histórica | ✅ (`test_as_of_retorna_versao_historica`) |
| MSC de 1 ente-ano ingere rápido (< 5 min) | ✅ (`test_msc_um_ente_ano_rapido`, 2000 linhas) |

> Migration `0002` reversível (validado down→up); dado do lago (bronze/silver/gold) é
> público/compartilhado — **sem RLS** (não é dado de tenant).

## Sprint 1B — Conectores complementares (implementada)

Reusa o `BaseConnector` da Sprint 1; **um conector isolado por dataset**, com cadência e
adaptador próprios. Fontes técnicas distintas resolvidas por `ClientResolver`
([client.py](src/app/shared/ingestion/client.py)): ORDS paginado (Tesouro), array simples
(BCB/SGS) e agregados aninhados (IBGE, com *flatten*).

- **SADIPEM** ([connectors/sadipem.py](src/app/modules/ingestion/connectors/sadipem.py)):
  PVL, operações contratadas, cronograma de pagamentos, CDP →
  `silver.sadipem_{pvl,op_contratada,cronograma_pgto,cdp}`. Consome: Sprint 8 (Dívida).
- **BCB/SGS** ([connectors/bcb.py](src/app/modules/ingestion/connectors/bcb.py)):
  IPCA=433, Selic=11/4390/4189, IGP-M=189; **sempre** `dataInicial`/`dataFinal`; delta pela
  última data por série (lida do silver) → `silver.bcb_indice` (long format).
  Consome: Sprint 14 (exógenas), Sprint 5 (deflação).
- **IBGE** ([connectors/ibge.py](src/app/modules/ingestion/connectors/ibge.py)):
  população (agregado 6579), PIB nominal (5938, variável 37 em mil reais) e PIB per
  capita oficial ([Pesquisa 38, indicador leaf 47001](https://servicodados.ibge.gov.br/api/v1/pesquisas/38/periodos/2023/indicadores/47001/resultados/2304400), em reais por habitante) →
  `silver.ibge_populacao`, `silver.ibge_pib`. PIB per capita é ingerido diretamente da
  API de Pesquisas v1, sem derivação por população; a variável 513 do agregado 5938 é
  VAB agropecuário.
  Consome: Sprint 2 (`dim_ente`), Sprint 13 (coortes).

Regra bitemporal uniforme: fontes sem versão própria usam a **data de captura** como
`versao_entrega` (`shared/ingestion/base.py::capture_versao`). Migration `0003` reversível.

**Fontes baseadas em arquivo** (planilhas — hook `prepare()` no `BaseConnector`: baixa o
arquivo, usa o **checksum como `versao_entrega`** e faz o parse antes do bronze):
- **FPM/FPE, FUNDEB, transferências genéricas**
  ([connectors/transferencias.py](src/app/modules/ingestion/connectors/transferencias.py)) →
  `silver.tesouro_fpm`, `silver.fnde_fundeb_repasse`, `silver.transferencia_generica`.
  Um arquivo cobre muitos municípios; o `to_silver` explode em linhas por ente.
- **CAPAG** ([connectors/capag.py](src/app/modules/ingestion/connectors/capag.py)) →
  `silver.tesouro_capag`. Parser **falha explicitamente** (`SpreadsheetLayoutError` → 422)
  se o layout mudar — nunca adivinha.
- **SIOPS** ([connectors/siops.py](src/app/modules/ingestion/connectors/siops.py)) →
  `silver.siops_saude` (long format, *wide→long*). Sem API REST; download do arquivo de
  estrutura bimestral. Defasagem esperada (Sprint 15 alerta).

Parsing via openpyxl/CSV em [_spreadsheet.py](src/app/modules/ingestion/connectors/_spreadsheet.py).
Registro central [connectors/registry.py](src/app/modules/ingestion/connectors/registry.py):
**18 fontes**, 18 silver, 19 partições de bronze. Migrations `0003`/`0004` reversíveis
(validadas down→up).

## Sprint 2 — Gold, dimensões conformadas & indicadores base (implementada)

Módulos **catalog** (dimensões) e **indicators** (cálculos — fonte única de verdade, §7).

- **dim_ente** ([catalog](src/app/modules/catalog/)) conformada de `silver.siconfi_entes` +
  enriquecida por `silver.ibge_populacao`/`ibge_pib` (ano de referência mais recente;
  atualiza quando novo ano do IBGE chega). **dim_periodo** hierárquica (ano →
  bimestre/quadrimestre → mês) com `ltree`; **dim_limite_legal** como **DADO** versionado
  (tetos/pisos por esfera da §2 — `make seed`).
- **RCL** ([indicators/rcl.py](src/app/modules/indicators/rcl.py)): 12 meses móveis,
  consolidada, deduções RPPS/compensação/FUNDEB, a partir do RREO Anexo 03 →
  `gold.fato_rcl` (recálculo **incremental** por período, com memória de cálculo e `as_of`).
- **Limites** ([indicators/limites.py](src/app/modules/indicators/limites.py)): cruza
  fato × `dim_limite_legal` por esfera/poder → `gold.mart_indicador`, classificando a
  **faixa** (alerta 90% / prudencial 95% / máximo 100% do teto; pisos invertidos).
- Endpoints: `GET /entes/{ibge}`, `GET /entes/{ibge}/rcl?periodo=&as_of=` (memória +
  drill DOWN das deduções), `GET /periodos` (drill temporal §6.1). Escopo §6.4 + `source_ref`.

### Critérios de aceite — status
| Critério | Status |
|---|---|
| RCL bate com caso conhecido (Anexo 03) | ✅ (`test_indicators.py::test_rcl_caso_conhecido`) |
| Muda entre esfera municipal/estadual | ✅ (`test_limite_pessoal_varia_por_esfera`) |
| Memória de cálculo rastreável + `as_of` | ✅ (`test_rcl_endpoint_memoria_e_drill`, `test_rcl_as_of_...`) |
| Recálculo incremental só do período | ✅ (`calcular_rcl` por `(ente, periodo, versão)`) |
| `dim_ente` atualiza com novo ano IBGE | ✅ (`test_catalog.py::test_dim_ente_atualiza_com_novo_ano_ibge`) |

> Migration `0005` reversível (validada down→up). Cálculos ficam só em `indicators/`
> (fonte única de verdade); outros módulos consomem `gold.fato_rcl`/`mart_indicador`.

## Sprint 3 — Dashboard Executivo & Monitor de Limites (implementada)

Módulos **dashboard** (Módulo 1) e **limits** (Módulo 3), consumindo `mart_indicador`/`fato_rcl`.
Cria `gold.dim_providencia_legal` (o que fazer em cada faixa — DADO; semeado).

- `GET /entes/{ibge}/dashboard?periodo=` — semáforo (pessoal/dívida/saúde/educação →
  verde/amarelo/laranja/vermelho/cinza), KPIs (RCL/receita/deduções; despesa/resultado
  marcados indisponíveis até as Sprints 5/8), status de conformidade e destaques automáticos.
- `GET /entes/{ibge}/limites?periodo=` — todos os limites com faixa e **distância ao
  teto/alerta**. Drill DOWN: `GET /entes/{ibge}/limites/{indicador}` — memória de cálculo,
  **providências da faixa corrente** e série histórica com breadcrumb temporal (drill UP §6.1).
- `POST /entes/{ibge}/limites/{indicador}/simular` — recalcula a faixa para
  `novo_valor_rs`/`delta_rs` **sem persistir**.

### Critérios de aceite — status
| Critério | Status |
|---|---|
| Semáforo coerente com `mart_indicador` | ✅ (`test_dashboard_semaforo_coerente`) |
| Simulador recalcula faixa sem persistir | ✅ (`test_simular_nao_persiste`) |
| Providências só na faixa correspondente | ✅ (`test_limite_detail_providencias_da_faixa`) |
| Drill período (ano→bimestre) funcional | ✅ (breadcrump `[2024]` + série; `/periodos`) |

> Migration `0006` reversível (validada down→up). `dim_providencia_legal` apenas **aponta o
> dispositivo legal** por faixa (§9): não decide nem emite parecer.

## Sprint 13 — Benchmarking (Módulo 12)

Benchmark auditável entre entes da mesma esfera, usando somente valores reais já
materializados em `gold.mart_indicador`. As migrations `0017`–`0019` criam
`gold.dim_coorte`, seu histórico bitemporal e `gold.mart_benchmark`.

- Coortes explícitas e editáveis por porte, região ou faixa de PIB. Alterações ficam
  versionadas e uma consulta com `as_of` recupera a definição vigente naquele instante.
- Percentil SQL-standard: `100 × (RANK(valor ASC) - 1) / (N - 1)`, com empate por
  `RANK` e `N=1 → 0`. Quantis usam interpolação linear Type 7.
- `GET /benchmark?indicador=&ente=&coorte=&periodo=&as_of=` retorna distribuição,
  cobertura da amostra e posição do ente destacado.
- `GET /benchmark/ranking?...&ordenar=&ordem=&pagina=&por_pagina=` retorna ranking
  ordenável/paginado e mantém o ente consultado em `ente_ancora`.
- Cada ponto traz `source_ref`, `as_of` e memória de cálculo. Pessoal combina, por chave
  imutável, as entregas exatas de RGF (numerador) e RREO (RCL); população e PIB trazem
  também suas referências IBGE/SICONFI. O `snapshot_hash` permite repetição idempotente.

Fluxo operacional, depois de executar os conectores reais pelo
`POST /admin/ingestion/run` (`siconfi_entes`, `siconfi_rreo`, `siconfi_rgf`,
`ibge_populacao` e `ibge_pib`):

```bash
python -m scripts.conform_all_entes
python -m scripts.materialize_benchmark --ente 2304400 \
  --indicador pessoal_executivo --periodo 2024-B6 --coorte regiao:NE
```

A página `/benchmarking` do frontend consome esses dois endpoints diretamente, com
estados assíncronos, troca de coorte, distribuição, cobertura, ranking e drills
coorte → ente → posição; não há fallback para mock em produção.

## Como rodar (Postgres local — sem Docker)

Pré-requisitos: Python 3.12 e um PostgreSQL acessível em `localhost:5432`
(superuser `postgres`/`postgres`; ajuste em `.env` se necessário).

```bash
python -m venv .venv
./.venv/Scripts/python -m pip install -e ".[dev]"   # Windows
cp .env.example .env

python -m scripts.bootstrap_db   # cria role 'plataforma_app' + banco 'plataforma_fiscal'
python -m alembic upgrade head   # cria schema op, extensão ltree, tabelas e RLS
python -m scripts.seed           # 1 município + 1 estado
python -m uvicorn app.main:app --reload --app-dir src
```

Docs interativas em `http://localhost:8000/docs`.

### Logins de exemplo (seed, senha `senha1234`)
- `admin@municipio.gov.br` — admin do município (carteira completa)
- `admin@sefaz.gov.br` — admin do estado (carteira completa)
- `gestor@sefaz.gov.br` — estado, **escopo restrito** a `2304400` (demonstra 403)

## Como rodar (Docker)

```bash
docker compose up -d --build   # sobe postgres 16 + redis + api (bootstrap+migrate no start)
```

## Comandos (Makefile)

`make install | bootstrap | migrate | seed | run | test | lint | fmt | up | down`.
No Windows sem GNU make, use o interpretador do venv diretamente, ex.:
`./.venv/Scripts/python -m pytest`.

## Arquitetura (resumo)

- **CQRS de schemas:** `op` (operacional/transacional, isolado por tenant via RLS) ×
  `gold` (analítico/leitura — Sprints seguintes). Dado fiscal do SICONFI é público e
  **compartilhado**; a carteira referencia o ente por **código IBGE**.
- **Plano de controle × plano de dados:** `organizacao`/`usuario` são globais
  (protegidos pela capacidade `administrar`); `papel`, `membership`, `carteira_ente` e
  `audit_log` têm RLS por `org_id`. O contexto administrativo (login, seed, gestão) usa
  `app.is_admin=on`, controlado apenas no servidor.
- Camadas `router → service → repository` (§7); Pydantic nunca expõe ORM.

## Layout

```
src/app/
  core/     config, db (RLS), security (JWT/hash), deps, errors
  shared/   hierarchy, envelope, pagination, source_ref, scope, audit
  modules/
    tenancy/        models, schemas, repository, service, router
    hierarchy_demo/ endpoint que demonstra o envelope de drill (§6.1)
alembic/versions/   0001_sprint0_tenancy.py
scripts/            bootstrap_db.py, seed.py
tests/              auth, scope, rls, hierarchy_envelope, audit
```
