# Caminho de migração da MSC para um OLAP colunar (Sprint 12)

> **Escopo.** `gold.fato_msc_saldo` é, por desenho, a maior tabela do sistema (Matriz de
> Saldos Contábeis — mensal, por conta PCASP, por ente). No MVP ela convive com o resto no
> mesmo PostgreSQL (CLAUDE.md §3), **particionada por `(uf, ano)`** e desacoplada da API por
> um mart de leitura (`gold.mart_msc_rollup`). Este documento descreve como ela migra para um
> armazém colunar (ClickHouse / Redshift / BigQuery / DuckDB-Parquet) quando o volume exigir,
> **sem alterar o contrato dos endpoints** do Módulo 11.

## 1. Por que a MSC é o candidato natural a OLAP

| Fator | MSC | Demais fatos |
|---|---|---|
| Granularidade | conta PCASP × mês × ente × fonte | linha do relatório × período |
| Volume/ano (1 ente) | ~10³–10⁴ contas × 12 meses | dezenas–centenas de linhas |
| Volume/ano (Brasil) | ~5.570 municípios × 27 UF × 12 meses × ~10³ contas ⇒ **bilhões de linhas** | milhões |
| Padrão de acesso | agregação por prefixo de conta, filtro por (ente, período), série temporal | leitura pontual |
| Escrita | *append* mensal imutável (bitemporal por `versao_entrega`) | idem |

Isso é exatamente o perfil onde um *columnar store* ganha: varreduras agregadas em poucas
colunas numéricas, compressão alta por coluna, particionamento/ordenação por `(uf, ano, mês)`.

## 2. O que já está pronto para a migração (desenho atual)

1. **Particionamento composto `LIST(uf) → LIST(ano)`** com partições DEFAULT em ambos os
   níveis (migração `0016`). Cada partição folha `fato_msc_saldo_<uf>_<ano>` é um alvo natural
   de *export* independente (um arquivo Parquet por partição, um `PARTITION` no ClickHouse).
2. **Convenção de saldo estável — devedor líquido** (débito `+`, crédito `−`): as identidades
   contábeis (Σ filhos = pai; classe 1 = Ativo) valem sem pós-processamento, então o OLAP
   pode materializar os rollups com um simples `sum(saldo_final)` agrupado por prefixo.
2. **Fronteira de leitura já isolada:** a API **nunca** lê `fato_msc_saldo` diretamente no
   caminho quente — lê o `mart_msc_rollup` (nós pré-agregados, com `has_children`). O
   `repository.py` do módulo é a **única** costura de acesso. Trocar o *backend* de `fato_*`
   e do *job* de rollup não toca `service.py`/`router.py`.
3. **Bitemporalidade preservada:** `versao_entrega` participa da chave. No OLAP vira coluna de
   ordenação/partição lógica; retificações continuam *append-only* (nunca `UPDATE`).
4. **Idempotência por versão:** a materialização (`accounting.service.ensure_materializado`)
   só (re)processa `(ente, mês, versão)` ausentes — compatível com *loads* incrementais.

## 3. Passos da migração (quando `fato_msc_saldo` crescer além do PostgreSQL)

### Fase A — *Offload* frio, sem mudar a API
- **Export por partição** para Parquet no *object storage* (`fato_msc_saldo_<uf>_<ano>` →
  `s3://.../msc/uf=<uf>/ano=<ano>/*.parquet`), particionado por `uf`/`ano`/`mes`.
- Manter no PostgreSQL apenas as partições **quentes** (anos correntes); anexar as frias como
  *foreign tables* (`postgres_fdw`/`parquet_fdw`) ou movê-las para o OLAP.
- `mart_msc_rollup` permanece no PostgreSQL como cache de leitura → **endpoints inalterados**.

### Fase B — Rollup no motor colunar
- Recriar `fato_msc_saldo` no OLAP:
  - **ClickHouse:** `MergeTree ORDER BY (uf, ano, cod_ibge, cod_conta, mes)`,
    `PARTITION BY (uf, ano)`; rollups via *materialized view* `SummingMergeTree` agregando
    `saldo_final` por prefixo de `cod_conta`.
  - **Redshift/BigQuery:** tabela colunar `DISTKEY(cod_ibge)` / `SORTKEY(uf, ano, mes, cod_conta)`;
    rollup por *scheduled query* que reescreve `mart_msc_rollup`.
  - **DuckDB + Parquet:** consulta direta aos Parquets particionados; rollup com
    `SELECT ... GROUP BY substr(cod_conta, ...)` explorando a hierarquia posicional PCASP.
- O *job* de rollup (hoje em `service._materializar_msc_mes`) é reescrito como uma consulta de
  agregação **no motor colunar**, escrevendo o mesmo `mart_msc_rollup` (mesmo esquema).

### Fase C — Troca do adaptador de repositório
- Introduzir uma **porta** `MscSaldoStore` (Protocol) com as operações que hoje vivem em
  `accounting/repository.py`: `replace_msc_saldo`, `rollup_children`, `rollup_node`,
  `rollup_matriz`, `msc_saldo_present`. Implementações: `PostgresMscSaldoStore` (atual) e
  `ClickHouseMscSaldoStore` (novo). `service.py` depende só da porta.
- A hierarquia PCASP (`dim_conta_pcasp`, `accounting/pcasp.py`) e o `mart_msc_rollup`
  permanecem no PostgreSQL — o *drill lazy* e a matriz mensal continuam sub-300 ms.

## 4. Invariantes que a migração deve preservar

- **Rollup exato:** `saldo(pai) == Σ saldo(filhos)` na convenção devedor líquido.
- **Conciliação:** MSC (fechamento) × DCA (Balanço Patrimonial) e a identidade do encerramento
  `DCA(Passivo+PL) = MSC(Passivo+PL) + (VPA − VPD)` continuam valendo (são consultas sobre o
  mart e o `fato_balanco`, independentes do *store* físico da MSC).
- **`source_ref` + `as_of`:** toda leitura resolve a `versao_entrega` vigente/histórica via
  `gold.dim_entrega` antes de consultar o *store* — bitemporalidade preservada no OLAP.
- **Contrato dos endpoints** (`/msc/arvore`, `/msc/conta/{codigo}/saldos`, `/msc/conciliacao`,
  `/balancos`) **não muda** — só o adaptador por trás do `repository`.

## 5. Gatilhos operacionais para iniciar a migração

- `gold.fato_msc_saldo` acima de ~10⁸ linhas ou ~50 GB, **ou**
- `p95` da materialização mensal (por ente-ano) acima do SLA de ingestão, **ou**
- necessidade de análises *cross-ente* nacionais (Sefaz/estadual) varrendo muitas UFs/anos.

Até lá, o particionamento `(uf, ano)` + índices por `path`/partição + o `mart_msc_rollup`
entregam o aceite (**consulta de conta < 300 ms no seed**) sem OLAP dedicado.
