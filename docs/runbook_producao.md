# Runbook de produção

> Para quem vai colocar no ar, e para quem vai ser acordado às 3h. Cada procedimento
> aqui foi **executado** durante a Sprint 28; onde não foi, está dito.

---

## 1. Antes do primeiro deploy

### 1.1 Segredos

Nada de segredo no repositório. `.env` está no `.gitignore` e o histórico foi
conferido: `git log --all -p | grep AIza` retorna **0 ocorrências** — a chave do Gemini
nunca foi versionada.

Em nuvem, injete pelo gerenciador do provedor (Secrets Manager, Parameter Store, Vault).
O `.env.prod` local existe apenas como **contrato de quais variáveis existem**, com
placeholders — nunca com valores.

| Variável | Como gerar |
|---|---|
| `JWT_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `POSTGRES_PASSWORD`, `APP_DB_PASSWORD` | senhas longas e distintas entre si |
| `GEMINI_API_KEY` | console do Google AI Studio |
| `CORS_ORIGINS` | domínio exato do frontend, sem curinga |

**Rotação da chave do Gemini.** Gere a nova no console, publique no gerenciador de
segredos, reinicie API e workers, e só então revogue a antiga — nessa ordem, para não
haver janela sem chave válida. O assistente degrada de forma segura: sem chave, cai no
provedor local determinístico e **nunca inventa número**.

### 1.2 A conta que roda a aplicação

A role do runtime **não pode** ser dona das tabelas nem ter `BYPASSRLS` — dono de tabela
ignora *policy* por padrão, e aí o isolamento entre clientes vira decoração. Isso é
verificado automaticamente em
`tests/test_sprint28_seguranca.py::test_a_role_da_aplicacao_nao_pode_desligar_a_rls`.

### 1.3 Dimensionamento do pool

```
(db_pool_size + db_max_overflow) × réplicas_da_api  +  workers  <  max_connections
```

O padrão do SQLAlchemy (5 + 10) **não serve**: foi o primeiro gargalo que o teste de
carga encontrou. Com `max_connections = 100` e 4 réplicas, `20 + 20` por réplica não
cabe — use `8 + 8` por réplica, ou eleve `max_connections`.

---

## 2. Subir do zero

```bash
cp .env.example .env.prod          # preencha; nunca versione
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
docker compose -f docker-compose.prod.yml logs -f migrate   # tem de sair com código 0
```

A ordem é imposta pelo compose: `postgres` saudável → `migrate` (one-shot) conclui →
`api`, `ingest-worker` e `scheduler` sobem. **A API não roda migration**: com N réplicas
subindo juntas, todas disputariam o mesmo `alembic_version` e o deploy entraria em laço.

Carga inicial mínima, na ordem:

```bash
docker compose -f docker-compose.prod.yml exec api python -m scripts.seed_dimensoes
docker compose -f docker-compose.prod.yml exec api python -m scripts.backfill --uf 23 --anos 2023 2024
docker compose -f docker-compose.prod.yml exec api python -m scripts.materializar --uf 23
```

**O seed de demonstração (`scripts/seed.py`) não vai para produção.** Ele cria
organizações de exemplo e o operador `operador@erario.com.br` com senha conhecida. Em
produção, a primeira organização nasce pelo control plane:
`POST /platform/orgs` — e o primeiro superuser é criado uma única vez, à mão, com senha
gerada na hora.

---

## 3. Deploy de uma nova versão

```bash
git pull && docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d migrate      # migra antes
docker compose -f docker-compose.prod.yml up -d api ingest-worker scheduler
```

Antes de considerar concluído:

```bash
curl -fsS https://<host>/health
docker compose -f docker-compose.prod.yml exec api python -m scripts.validacao_fiscal
```

A validação fiscal sai com **código 1** se algum número da plataforma divergir do
demonstrativo oficial sem causa conhecida. Um deploy que a quebra não vai ao ar.

---

## 4. Backup e restore

### 4.1 Backup

```bash
docker compose -f docker-compose.prod.yml exec postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /backup/fiscal-$(date +%F).dump
```

Formato *custom* (`-Fc`) porque permite restore seletivo e paralelo. O volume
`./var/backup` já está montado no contêiner.

Retenção sugerida: diário por 14 dias, semanal por 3 meses. **Guarde fora da máquina** —
backup no mesmo disco não sobrevive ao incidente que o justifica.

### 4.2 Restore — testado em 28/07/2026

Nunca restaure por cima do banco vivo. Restaure ao lado e compare:

```bash
psql -U postgres -d postgres -c "CREATE DATABASE fiscal_restore"
pg_restore -U postgres -d fiscal_restore --no-owner --no-privileges -j 4 backup.dump
psql -U postgres -d fiscal_restore -c "
  select (select count(*) from gold.dim_ente)      as entes,
         (select count(*) from gold.mart_indicador) as indicadores,
         (select version_num from alembic_version)  as migration"
```

**Resultado do teste:** dump de 387,5 MB restaurado em banco limpo; 5.594 entes,
3.163 linhas de RCL, 14.629 de `mart_indicador`, 9 licenças e a versão de migration
preservadas.

---

## 5. Rollback

### 5.1 Só a aplicação (sem mudança de schema)

```bash
docker compose -f docker-compose.prod.yml up -d --no-deps api=<imagem-anterior>
```

### 5.2 Com mudança de schema — testado em 28/07/2026

O caminho de volta foi exercitado **na cópia restaurada**, nunca no banco vivo:

```bash
alembic downgrade 0024_sprint18_admin_billing   # desfaz as migrations 0025+
alembic upgrade head                            # e volta
```

**Resultado:** o ciclo completo funciona. No `upgrade` de volta, a migration `0034`
**reconstituiu as 9 licenças a partir da carteira** — nenhuma organização perde acesso
num rollback.

Regra de ouro: **backup antes de qualquer `downgrade`**. Migration reversível devolve o
schema, não o dado que a coluna removida guardava.

---

## 6. Operação diária

| Sintoma | Onde olhar | Ação |
|---|---|---|
| Números velhos na tela | `GET /admin/qualidade` (freshness) | conferir se a ingestão rodou; ver `ingest-worker` |
| Alerta de qualidade | `/central-dados?painel=qualidade` | a página mostra **os dois lados** da conta que não fechou |
| Página lenta | header `X-Performance-P95-Ms` na resposta | ver §7 |
| Login recusando muito | log `429` no proxy | freio de tentativas; confirmar se é ataque ou senha errada em série |
| Relatório não sai | `docker compose logs scheduler` | o relógio é **um** processo; se ele caiu, ninguém agenda |

Um monitor que morre calado é pior que nenhum: o próprio ciclo de qualidade se
monitora, e ciclo perdido vira check em falha (`execucao_agendada`).

---

## 7. Desempenho — o que se sabe hoje

Medido em 28/07/2026, em máquina de desenvolvimento (8 núcleos lógicos) hospedando
**ao mesmo tempo** a API e o gerador de carga. Os números abaixo são conservadores por
construção; o ensaio precisa ser repetido no ambiente alvo.

Com 10 usuários simultâneos, 4 workers:

| Rota | P95 |
|---|---:|
| `/entes/{ibge}/despesa/arvore` | 872 ms |
| `/entes/{ibge}/receita/arvore` | 924 ms |
| `/uf/{uf}/ranking` | 995 ms |
| `/entes/{ibge}/limites` | 1.408 ms |
| `/alertas` | 2.457 ms |
| `/entes/{ibge}/receita` | 2.892 ms |
| `/entes/{ibge}/despesa` | 5.328 ms |
| **`/entes/{ibge}/cockpit`** | **8.068 ms** |

**O critério de 50 usuários com P95 < 800 ms não foi atingido, e não pôde ser medido com
justiça nesta bancada** — o gerador disputa CPU com a API na mesma máquina. O que a
medição estabeleceu com clareza:

1. **O pool padrão era um defeito real.** 5 + 10 conexões com espera de 30 s produziam
   timeout total sob concorrência. Corrigido e configurável.
2. **O motor de alertas na leitura era um defeito real.** Avaliar é *escrever*: 50
   leitores viravam 50 escritores concorrentes na mesma tabela. Com a janela de 30 s
   entre avaliações do mesmo ente, `/alertas` caiu de 30 s (timeout) para 2,5 s.
3. **O cockpit continua sendo o caminho mais caro do produto**, e a dívida é a mesma
   que a Sprint 27 registrou: a Sprint 22 o desenhou sem mart próprio, então ele avalia
   alertas e roda modelos de previsão a cada carga.

**Próximo passo para fechar o critério** (não feito nesta sprint): tirar a avaliação de
alertas e a previsão do caminho de leitura — alertas passam a ser produzidos no job de
carga e no `scheduler`, e a tendência lê `gold.fato_projecao` em vez de recalcular. É
mudança de arquitetura, não ajuste, e por isso não entrou junto com uma sprint de
validação.

---

## 8. O que ainda não foi exercitado

Dito com todas as letras, porque runbook que promete o que não testou é pior que
runbook nenhum:

- **Deploy real em nuvem.** O compose de produção está escrito e revisado, mas não foi
  executado num host limpo com TLS e proxy reverso.
- **Carga no ambiente alvo.** Ver §7.
- **Restore a partir de backup fora da máquina.** O restore foi testado com o dump
  local; a cópia remota (S3/bucket) não foi exercitada.
- **Failover de Postgres.** Não há réplica configurada.
