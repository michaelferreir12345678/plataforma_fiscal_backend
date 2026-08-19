# Arquitetura de dados e ingestão

> Como os dados públicos entram na plataforma, atravessam as camadas e viram número na
> tela. Números medidos no banco de produção em 19/08/2026.

---

## 1. O que este documento cobre — e o que deliberadamente não cobre

São **162 tabelas** em quatro schemas. Descrever cada uma daria dezenas de páginas que
ninguém lê, e um documento não lido é pior que nenhum: cria a ilusão de que a arquitetura
está documentada.

Este documento faz o contrário. Explica **os padrões**, que são poucos e se repetem, e
detalha as tabelas que sustentam tudo. Uma tabela nova que siga o padrão fica compreendida
sem estar listada aqui.

| Schema | Tabelas | Papel |
|---|---:|---|
| `bronze` | 22 | payload cru como a fonte devolveu |
| `silver` | 19 | payload normalizado, uma linha por registro |
| `gold` | 96 | fatos, dimensões e marts prontos para leitura |
| `op` | 25 | operacional privado da organização |

O inventário completo, com colunas e tipos, sai do próprio banco:

```sql
select table_schema, table_name, column_name, data_type
from information_schema.columns
where table_schema in ('bronze','silver','gold','op')
order by 1, 2, ordinal_position;
```

Gerá-lo é melhor que escrevê-lo: texto escrito à mão envelhece na primeira migration.

---

## 2. A ideia central: dado público é compartilhado, decisão é privada

Antes de qualquer diagrama, a fronteira que organiza o resto:

**O dado fiscal do SICONFI é público.** O RREO de Fortaleza é o mesmo para a Sefaz, para
uma consultoria e para a própria prefeitura. Por isso ele vive num **acervo único**
(`bronze`/`silver`/`gold`), sem cópia por cliente. Uma carteira referencia o ente por
código IBGE; nunca duplica o número dele.

**O que é privado é o operacional:** quais municípios cada organização acompanha, os
usuários, os alertas configurados, os relatórios gerados, a trilha de auditoria, as
decisões de qualidade. Isso vive em `op`, com *Row Level Security* — **20 políticas** em
20 tabelas, impostas pelo PostgreSQL. Mesmo que uma consulta da aplicação esqueça o filtro
da organização, o banco não devolve a linha alheia.

Essa separação é o que permite ter 96 tabelas de dado fiscal sem multiplicá-las por
cliente, e é o motivo de o banco ter 2,7 GB em vez de dezenas.

---

## 3. O caminho de um número, de ponta a ponta

```
   API pública                                                      Tela
  (SICONFI, SADIPEM,                                                  ▲
   BCB, IBGE, ...)                                                    │
        │                                                             │
        │  ┌──────────┐   ┌──────────┐   ┌────────────────┐   ┌──────────────┐
        └─▶│  BRONZE  │──▶│  SILVER  │──▶│      GOLD      │──▶│   API REST   │
           │          │   │          │   │                │   │              │
           │ payload  │   │ 1 linha  │   │ fato + dim +   │   │ + source_ref │
           │ cru +    │   │ por      │   │ mart calculado │   │ + escopo     │
           │ hash     │   │ registro │   │                │   │              │
           └──────────┘   └──────────┘   └────────────────┘   └──────────────┘
                │              │                  │
                └──────────────┴──────────────────┘
                               │
                       gold.dim_entrega
                  (qual versão, vigente ou não)
```

Cada seta é uma transformação com contrato próprio. A regra que atravessa todas: **nenhuma
camada apaga o que a anterior guardou.** Uma retificação cria versão nova; a antiga
continua consultável.

---

## 4. As camadas, uma a uma

### 4.1 BRONZE — o que a fonte devolveu, sem interpretação

**Garantia:** se amanhã descobrirmos que interpretamos um campo errado, o payload original
está aqui para reprocessar. Sem bronze, um erro de parsing é irreversível — só se corrige
baixando tudo de novo, e a fonte pode ter mudado no meio.

**Tabela única, particionada por fonte:**

```
bronze.raw_payload  ──  PARTITION BY LIST (fonte)
   ├── raw_payload_siconfi_rreo
   ├── raw_payload_siconfi_rgf
   ├── raw_payload_siconfi_msc
   ├── raw_payload_sadipem_pvl
   ├── ...  (19 partições nomeadas)
   └── raw_payload_default        ← fonte nova cai aqui sem quebrar
```

| Coluna | Para quê |
|---|---|
| `fonte`, `cod_ibge`, `periodo`, `versao` | a chave de negócio — idempotência |
| `payload` (JSONB) | o corpo cru, como veio |
| `hash_payload` | **detecta retificação**: mesmo hash ⇒ nada mudou, pula o silver |
| `ingerido_em` | quando entrou |

O `hash_payload` é o que torna a ingestão barata de repetir: rodar a mesma carga duas vezes
não reprocessa nada, porque o hash bate. E quando **não** bate, isso é a definição
operacional de "o ente retificou".

**Uma partição `DEFAULT` existe de propósito.** Uma fonte nova ingerida antes de alguém
criar a partição dela não falha — cai no default, e a migração para partição própria é
posterior. O contrário faria a plataforma recusar dado por falta de arrumação interna.

### 4.2 SILVER — uma linha por registro, tipada

**Garantia:** o payload vira tabela relacional consultável, sem ainda decidir nada sobre
significado fiscal.

Uma tabela por fonte, 19 no total. Exemplo do RREO:

| Coluna | Exemplo |
|---|---|
| `cod_ibge`, `periodo`, `versao_entrega` | `2304400`, `2025-B6`, `1` |
| `anexo` | `RREO-Anexo 03` |
| `conta`, `cod_conta` | `Receita Corrente Líquida`, `RREO3ReceitaCorrenteLiquida` |
| `coluna` | `TOTAL (ÚLTIMOS 12 MESES)` |
| `valor` | `40899706794.11` |
| `linha_seq` | ordem no demonstrativo |

**O `cod_conta` do SICONFI não é numérico.** É um *slug* textual, e a ordem das linhas
importa para reconstruir a hierarquia — daí o `linha_seq`. Quem espera um plano de contas
numerado erra aqui.

Silver **não calcula**: não deduz RCL, não aplica limite, não decide faixa. Ele só
normaliza. Isso mantém a fronteira: erro de leitura da fonte é problema do silver; erro de
regra fiscal é problema do gold.

### 4.3 GOLD — o dado com significado fiscal

96 tabelas, três famílias:

**`dim_*` — dimensões conformadas.** Descrevem o mundo, mudam pouco.

| Tabela | Guarda |
|---|---|
| `dim_ente` | 5.598 entes: IBGE, nome, esfera, UF, população, PIB, RPPS |
| `dim_entrega` | 9.569 entregas: qual versão de qual relatório, e **qual está vigente** |
| `dim_limite_legal` | os tetos/pisos por esfera e poder (54% município × 49% estado) |
| `dim_periodo` | hierarquia temporal ano → bimestre/quadrimestre → mês |
| `dim_conta_pcasp` | plano de contas, com `ltree` para a hierarquia |

**`fato_*` — os números apurados.** Uma linha por ente × período × recorte × versão.

| Tabela | Linhas | Guarda |
|---|---:|---|
| `fato_balanco` | 393.126 | balanços da DCA |
| `fato_despesa` | 326.449 | despesa por função e por natureza |
| `fato_receita` | 117.760 | receita por origem |
| `fato_msc_saldo` | 79.888 | saldo mensal por conta PCASP |
| `fato_capag` | 27.732 | nota de capacidade de pagamento |
| `fato_rcl` | — | a RCL de 12 meses, **denominador de quase tudo** |
| `fato_pessoal`, `fato_divida`, `fato_caixa_rap`, `fato_resultado` | — | os do RGF e do A6 |

**`mart_*` — pré-cálculo para leitura.** A API quase nunca calcula em request.

| Tabela | Linhas | Guarda |
|---|---:|---|
| `mart_indicador` | 23.756 | o semáforo: valor, faixa, teto, denominador, base |
| `mart_msc_rollup` | 145.664 | saldos agregados por nó da árvore PCASP |
| `mart_cobertura_fonte` | 20.546 | quem entregou o quê (retrato **corrente**) |
| `mart_carteira` | 15.661 | visão consolidada por carteira |
| `mart_consolidado_uf` | — | soma dos municípios de uma UF |
| `data_quality_check` | — | veredito das 9 verificações |

**`mart_indicador` merece detalhe** porque é a tabela que a tela mais lê:

| Coluna | Por que existe |
|---|---|
| `valor_rs`, `valor_pct_rcl` | o número em reais e em % |
| `base_valor` | **o denominador que foi usado** — sem isso a conta não se refaz |
| `denominador` | `rcl` ou `rcl_ajustada`: qual regra valeu |
| `teto_pct`, `faixa` | contra o quê foi comparado, e o resultado |
| `versao_entrega` | de qual entrega saiu |
| `source_ref` (JSONB) | relatório, anexo, período, versão — a procedência |

Guardar o denominador junto do resultado é o que permite auditar sem recalcular. Foi o que
permitiu descobrir, esta semana, que uma verificação de qualidade dividia pela RCL cheia
quando o indicador usa a Ajustada.

### 4.4 OP — o operacional, privado e com RLS

25 tabelas. `organizacao`, `usuario`, `papel`, `membership`, `carteira_ente`, `licenca`,
`alerta`, `relatorio`, `conversa`, `ingest_job`, `audit_log` (11.565 linhas),
`qualidade_tratativa`.

Toda tabela com `org_id` tem política de RLS **forçada** — nem o dono da tabela escapa:

```sql
CREATE POLICY x_tenant_isolation ON op.tabela
USING (current_setting('app.is_admin', true) = 'on'
       OR org_id = current_setting('app.current_org_id', true)::uuid);
```

---

## 5. O motor de extração

### 5.1 O ciclo, idêntico para as 21 fontes

`shared/ingestion/base.py::BaseConnector` define o ciclo. **Nenhuma fonte reinventa
framework de ingestão** — é o que impede 21 comportamentos diferentes para o mesmo
problema.

```
discover ──▶ extract ──▶ [payload vazio?] ──▶ hash ──▶ upsert_bronze ──▶ register_entrega
                              │                            │
                              │ sim                        │ hash igual ⇒ is_new=False
                              ▼                            ▼
                      log "vazio", NÃO cria         silver pulado (skipped)
                      entrega                              │
                                                           │ hash novo
                                                           ▼
                                                      to_silver ──▶ mark_done
```

Três decisões desse ciclo valem ser entendidas:

**Payload vazio não vira entrega.** Se a fonte ainda não publicou o período, registrar uma
entrega vigente sem linha alguma faria a lacuna da fonte parecer "dado zero". A ausência
tem de continuar sendo ausência.

**Hash igual pula o silver.** Rodar a mesma carga duas vezes custa uma requisição e nada
mais. É o que torna a ingestão segura de repetir.

**Versão nova não apaga a anterior.** A retificação cria entrega nova e passa a vigente; a
antiga continua consultável por `as_of`.

### 5.2 As 21 fontes

| Família | Conectores |
|---|---|
| SICONFI | `rreo`, `rgf`, `dca`, `msc`, `entes`, `extratos`, `rreo_minimos_pdf` |
| SADIPEM | `pvl`, `cdp`, `op_contratada`, `cronograma_pgto` |
| Tesouro | `capag`, `fpm` |
| IBGE | `populacao`, `pib`, `malha` |
| Outros | `bcb`, `siops_saude`, `siope_educacao`, `fnde_fundeb_repasse`, `transferencia_generica` |

O cliente HTTP (`shared/ingestion/client.py`) tem *rate limit* por fonte, paginação ORDS
(`items`/`hasMore`/`offset`) e retentativa exponencial — mas **só para 5xx e 429**. Um 404
falha de imediato: repetir cinco vezes um "não existe" é desperdício com aparência de
robustez.

---

## 6. O `ingest-worker`

Container próprio, consumindo uma fila **RQ** sobre Redis.

```
API  ──cria op.ingest_job──▶  Redis (fila "ingest")  ──▶  ingest-worker
                                                              │
                                                    run_job_async(job_id)
                                                              │
                                          ┌───────────────────┴──────────────────┐
                                          │  claim condicional (só 1 worker pega)│
                                          │  para cada ente × período:           │
                                          │     conector.run(...)                │
                                          │  materialize_scope(entes)            │
                                          │  refresh_cobertura()                 │
                                          │  executar_checks_qualidade()         │
                                          └──────────────────────────────────────┘
```

**Por que fila e não request:** uma carga de 5.570 municípios × 6 bimestres leva horas. O
gestor recebe `202` com o id do job e acompanha o progresso.

**O claim é condicional** — um `UPDATE ... WHERE status = 'pendente'` que só um worker
ganha. Sem isso, dois workers processariam o mesmo job e duplicariam requisições à fonte.

**Todo job termina rodando as verificações de qualidade.** Não é job separado: garante que
nenhum dado entra em produção sem passar pelas invariantes, e o resultado fica em
`gold.data_quality_check`. É o que permite o veredito mudar sozinho depois de uma carga.

**O refresh de cobertura é serializado** por `pg_advisory_xact_lock`: dois jobs concorrentes
produziriam contagem corrompida.

---

## 7. Bitemporalidade — a coluna que atravessa tudo

Não é detalhe de implementação; é o que permite reproduzir um relatório "como ele era".

Todo dado fiscal carrega **duas linhas do tempo**:

- **`periodo`** — a que exercício o número se refere (tempo fiscal)
- **`versao_entrega`** — de qual entrega ele saiu (tempo de transação)

`gold.dim_entrega` guarda as duas e marca qual é a **vigente**:

```
cod_ibge  relatorio  periodo   versao  vigente  homologada_em
2304400   RREO       2025-B6   1       false    2026-01-20
2304400   RREO       2025-B6   2       true     2026-03-15   ← retificação
```

Uma consulta sem `as_of` lê a vigente. Com `as_of`, lê a que era vigente **naquele
instante** — e o relatório de janeiro se reproduz igual, mesmo depois da retificação de
março.

**O erro clássico que isso previne** (e que já custou dois defeitos graves, família
A14/A15): resolver a vigência **uma vez** e repeti-la em todos os períodos de uma série.
Isso mistura, na mesma linha de gráfico, versões superadas com vigentes. Por isso a série
resolve a vigência **período a período**, e cada ponto declara a entrega de que saiu.

---

## 8. DER — o núcleo das relações

```
                          ┌──────────────┐
                          │   dim_ente   │  5.598 entes
                          │  cod_ibge PK │  esfera, uf, populacao, rpps
                          └──────┬───────┘
                                 │ cod_ibge
        ┌────────────────────────┼────────────────────────┐
        │                        │                        │
┌───────▼────────┐      ┌────────▼────────┐      ┌────────▼────────┐
│  dim_entrega   │      │    fato_rcl     │      │  mart_indicador │
│ relatorio      │◀─────│  rcl_12m        │◀─────│  valor_pct_rcl  │
│ periodo        │ ver  │  periodo_ref    │ base │  base_valor     │
│ versao_entrega │      │  versao_entrega │      │  denominador    │
│ vigente        │      └─────────────────┘      │  faixa, teto    │
└───────┬────────┘                               └────────┬────────┘
        │ versao_entrega                                  │ indicador
        │                                        ┌────────▼────────┐
┌───────▼────────┐                               │ dim_limite_legal│
│ silver.siconfi │                               │ esfera, poder   │
│ _rreo / _rgf   │                               │ teto/alerta/prud│
│ _dca / _msc    │                               └─────────────────┘
└───────┬────────┘
        │ hash_payload
┌───────▼────────┐
│ bronze.raw_    │
│ payload        │  particionada por fonte
└────────────────┘
```

**A leitura do diagrama:** `dim_ente` é o centro; `dim_entrega` é o que versiona;
`fato_rcl` é o denominador que quase todo indicador usa; `dim_limite_legal` é o teto contra
o qual se compara — e ele **muda com a esfera**, por isso não pode ser constante no código.

Do lado operacional, isolado:

```
op.organizacao ──┬── op.usuario (via membership)
                 ├── op.carteira_ente ──▶ (cod_ibge) ──▶ gold.dim_ente
                 ├── op.licenca
                 ├── op.alerta
                 └── op.qualidade_tratativa
```

`carteira_ente` **referencia** o ente por código; nunca copia o dado dele. É o que mantém
um acervo só.

---

## 9. Armazenamento e volume

**Particionamento existe onde o volume exige, não por princípio.**

`bronze.raw_payload` — por `LIST (fonte)`. Uma fonte nunca varre a partição de outra, e
descartar o histórico de uma fonte é `DROP` de partição, não `DELETE` de milhões de linhas.

`gold.fato_msc_saldo` — por `LIST (uf)` e ano. A MSC é mensal e por conta contábil: hoje
são 79.888 linhas com poucos entes, mas o histórico nacional projeta centenas de milhões.
As partições atuais mostram o desenho funcionando:

```
fato_msc_saldo_uf_default_default   71.603 linhas
fato_msc_saldo_sp_2022               8.285 linhas
```

**A migração planejada:** quando o volume exigir, `fato_msc_saldo` vai para armazenamento
colunar (S3 + Athena, ou Redshift) — dado histórico, consultado por varredura, sem
transação. O resto continua no PostgreSQL, que é onde há transação, RLS e integridade
referencial. Ver `docs/msc_olap_migration.md`.

**Índices:** 445 no total. Não são decorativos — as telas dependem de leitura sub-segundo
(o cockpit foi otimizado de 7,2 s para ~1 s na Sprint 27).

---

## 10. Como se percebe quando algo quebra

A plataforma não confia que a ingestão deu certo: ela verifica.

**9 verificações** rodam ao fim de cada carga e gravam os **dois lados** da conta em
`gold.data_quality_check` — não só o veredito, porque um gestor que discorda precisa poder
refazer a conta.

| Verificação | Compara |
|---|---|
| `receita_soma_filhos` | pai × Σ filhos publicados |
| `despesa_estagios_monotonicos` | empenhado ≥ liquidado ≥ pago |
| `rcl_calculada_vs_publicada` | nossa RCL × A3 publicado |
| `dcl_a6_vs_rgf` | RREO Anexo 6 × RGF Anexo 2 |
| `msc_vs_dca` | Matriz de Saldos × Declaração Anual |
| `minimo_{saude,educacao}_recalculado` | recalculado × materializado |
| `mart_vs_detalhe_pessoal` | semáforo × página de detalhe |
| `freshness_{rreo,rgf,dca,msc}` | dias desde a entrega × prazo legal |

Elas se classificam por **de quem é o número** — e isso decide a ação (Sprint Q1):

- **plataforma** (dois lados nossos) ⇒ rematerializar
- **fonte** (dois lados do ente) ⇒ nada a reprocessar; é achado para o ente
- **cobertura** (defasagem) ⇒ consultar a fonte para saber de quem é a falta

`gold.lineage_edge` complementa: o grafo fonte → bronze → silver → gold → endpoint →
página, mantido **por código** e não por cadastro — grafo mantido à mão vira documentação
desatualizada com aparência de verdade.

---

## 11. Os três erros que esta arquitetura existe para evitar

Vale fechar por aqui, porque cada decisão acima responde a um deles.

**1. Confundir "o ente não entregou" com "nós não carregamos".** São causas opostas com
ações opostas, e atribuí-las errado faz a plataforma culpar o gestor por uma falha nossa.
`mart_cobertura_fonte` e a classe `cobertura` das verificações existem só para separá-las.

**2. Apresentar ausência como zero.** Payload vazio não vira entrega; indicador sem
apuração não vira `0`; tela sem dado mostra ausência, não um número. Zero é uma afirmação
fiscal — dizer que a despesa foi zero é diferente de dizer que não sabemos.

**3. Perder a procedência.** Todo número que sai da plataforma carrega `source_ref`:
relatório, anexo, período e versão da entrega. Sem isso, um número correto e um número
inventado são indistinguíveis para quem lê — e numa plataforma de dado fiscal público, essa
diferença é a única que importa.
