# Sprint 21 — Backfill âncora Ceará: decisões e operação

Documento operacional da Sprint 21 (completude e backfill dos dados reais). Registra as
decisões que o prompt da sprint mandou documentar e como rodar cada peça.

---

## 1. ICMS cota-parte — decisão de fonte (auditoria §8, B12)

O prompt oferecia duas opções: **feed dedicado da Sefaz-CE** ou **derivação documentada do
RREO Anexo 01**. Não há credencial/endpoint da Sefaz-CE conectado ao projeto, então a
implementação usa a **derivação do RREO A1**, em `app/modules/ingestion/derivacoes.py`.

**O que é extraído.** A linha do Anexo 01 cujo `cod_conta` é
`TransferenciasCorrentesDosEstadosEDoDistritoFederalEDeSuasEntidades`, na coluna
**"No Bimestre (b)"** (valor realizado no bimestre). Grava-se uma linha por bimestre em
`silver.transferencia_generica` no mês de fechamento (`mes = 2 × bimestre`), com
`tipo = 'cota_parte_estados'` e `fonte = 'derivado_rreo_a1'`.

**Ressalva registrada no próprio dado.** Essa linha é o **agregado das transferências
estaduais** — predominantemente a cota-parte do ICMS (CF art. 158, IV) somada à cota-parte
do IPVA —, **não o ICMS isolado**. O RREO consolidado do SICONFI não abre a cota-parte do
ICMS em linha própria (verificado em Fortaleza/2024: o Anexo 01 só traz
"Transferências dos Estados e do DF e de suas Entidades").

**Consequência para a conciliação.** Como a série é derivada do próprio RREO, ela **não é
contraprova independente** — o `tipo` e a `fonte` deixam a proveniência explícita para que
a tela de transferências não a apresente como validação externa. Quando o feed Sefaz-CE
entrar, o conector `transferencia_generica` substitui a derivação **sem mudar o schema**.

Exemplo real (Fortaleza, 2024, 6 bimestres derivados):

| mês | valor (R$) | fonte |
|---|---|---|
| 2 | 323.774.774,46 | derivado_rreo_a1 |
| 12 | 227.410.659,57 | derivado_rreo_a1 |

---

## 2. Volume: tablespace dedicado (ops local, fora das migrations)

O backfill CE multiplica bronze/silver por ~200×. Onde o disco padrão do Postgres está
saturado, `scripts/ensure_tablespace.py` move as tabelas volumosas para outro disco e
define o `default_tablespace` do banco, para que as linhas novas nasçam lá.

```
python -m scripts.ensure_tablespace --tablespace fiscal_dados --location D:/pg_tablespace_fiscal
```

**Não é migration**: a localização é específica do ambiente e não deve ser versionada — as
migrations continuam portáveis (sem referência a tablespace).

---

## 3. Como rodar

```bash
# 1) Backfill (worker com checkpoint; NÃO usa o POST /run síncrono)
python -m scripts.backfill_sprint21 --anos 2021-2024 \
    --fontes siconfi_rreo,siconfi_rgf,siconfi_dca,siconfi_extratos --entes-limit 0
python -m scripts.backfill_sprint21 --so-estado --anos 2021-2024   # só o ente estadual 23
python -m scripts.backfill_sprint21 --bcb                          # séries BCB 2019→hoje

# 2) Materialização da gold (lazy vira fallback)
python -m scripts.materialize_sprint21 --uf 23 --benchmark

# 3) Agendamento contínuo (uma passagem; cadência por fonte)
python -m scripts.scheduler --once      # registrado no Agendador de Tarefas do Windows
```

O backfill é **idempotente em escala**: o checkpoint (`var/backfill/checkpoint.json`) evita
reexecutar unidades concluídas e o medallion evita duplicar dados mesmo sem checkpoint.
A **guarda de disco** interrompe com elegância abaixo do limite livre, preservando o
checkpoint.

**Isolamento de falha.** Uma unidade que a fonte recuse (4xx definitivo, layout mudado)
**não derruba o lote**: é contada, registrada em `gold.ingestion_log` com `status='erro'` e
**fica fora do checkpoint**, de modo que a próxima corrida a retenta. Rodar o mesmo comando
de novo é sempre seguro e é o modo normal de retomar.

---

## 3.1 Acompanhar o progresso

```
python -m scripts.status_sprint21          # progresso, cobertura, falhas, próximos passos
python -m scripts.status_sprint21 --uf 23 --meta 95
```

Mostra, sem depender do stdout do worker (que pode ter rolado ou sido perdido):

- **checkpoints** — unidades concluídas e linhas silver por job;
- **cobertura por fonte/ano** com o percentual sobre os 184 municípios e marcação da meta;
- **frescor** — quando a cobertura foi materializada e um aviso se o silver já avançou além
  dela (a cobertura é materializada por job, não é uma *view*);
- **falhas** de `ingestion_log` e o lembrete de que basta reexecutar;
- **próximos passos** já com os comandos prontos.

Checagem rápida se o worker ainda está vivo: `Get-Process python`.

---

## 3.2 Quando o backfill terminar

1. `python -m scripts.backfill_sprint21 --anos 2021-2024 --fontes siconfi_dca,siconfi_extratos`
   — fecha o histórico anual e os extratos (que alimentam a varredura de retificações).
2. `python -m scripts.materialize_sprint21 --uf 23 --benchmark`
   — materializa fatos/marts de **todos** os períodos, deriva a cota-parte, atualiza a CAPAG
   e **refaz a cobertura**.
3. `python -m scripts.status_sprint21` — confirmar RREO/RGF ≥ 95% para 2022→atual.
4. Nada mais é manual: a tarefa diária `PlataformaFiscal-Ingestao` mantém tudo atualizado
   pela cadência de cada fonte e registra cada passagem em `gold.ingestion_log`.

---

## 3.3 Resultado dos backfills (23/07/2026)

| job | unidades | falhas | resultado |
|---|---|---|---|
| RREO + RGF (CE 2022-2024) | 1.734 | 0 | 5,7M linhas; 180 entes RREO / 178 RGF |
| DCA + extratos (CE 2021-2024) | 1.544 | 0 | 866k linhas; DCA 194 entes / extratos 193 |
| IBGE população + PIB | 1.536 | 0 | 192 entes; `dim_ente` com PIB no CE 1 → 184 |
| BCB (IPCA/Selic/IGP-M) | 4 séries | 0 | 2019 → 2026 |

**Os extratos revelaram 5.928 retificações reais** no escopo (MSC 3.475, RGF 2.242, RREO 211) —
massa concreta para o mecanismo bitemporal, antes exercitado em um único caso.

## 4. Retificação real comprovada (`as_of`)

Caso real usado como prova — **Fortaleza (2304400), RGF 2023-Q3**. O extrato de entregas do
SICONFI registra duas entregas homologadas para o mesmo período:

| versão (`data_status`) | homologada em | vigente |
|---|---|---|
| `2024-01-26T15:30:27Z` | 26/01/2024 12:30 | não |
| `2024-01-29T16:10:52Z` | 29/01/2024 13:10 | **sim** |

- `resolve_versao(as_of=None)` → `2024-01-29T16:10:52Z` (retificação vigente)
- `resolve_versao(as_of=27/01/2024)` → `2024-01-26T15:30:27Z` (entrega original)

A retificação **supera** a versão anterior sem apagá-la (§6.5). Ressalva honesta: a API
aberta do SICONFI serve apenas os números correntes, então as duas versões armazenadas
carregam os mesmos valores — o que a prova demonstra é a **resolução bitemporal** (o
`as_of` seleciona a entrega vigente naquele instante), que é o mecanismo exigido pela
auditoria.

---

## 5. Guarda de payload vazio

Uma fonte que ainda não publicou o período devolve lista vazia. O framework passou a
**não** registrar entrega nesse caso (`status = "vazio"` no `ingestion_log`): uma entrega
vigente sem linha alguma mascararia a lacuna da fonte como "dado zero". Assim, município
sem entrega no SICONFI simplesmente **não tem linha** em `mart_cobertura_fonte` — a lacuna
fica explícita como falha da fonte, não da plataforma.

---

## 6. RGF esfera-aware

O `co_esfera` passou a ser derivado do próprio código do ente (UF de 2 dígitos ⇒ `E`) e os
poderes consultados acompanham a esfera:

- **Municipal**: Executivo, Legislativo.
- **Estadual**: Executivo, Legislativo, Judiciário, Ministério Público, Defensoria.

O RGF também aceita `periodicidade="S"` (semestral), obrigatória para municípios com menos
de 50 mil habitantes (LRF art. 63) — `dim_periodo` ganhou os nós `AAAA-S1`/`AAAA-S2`.
