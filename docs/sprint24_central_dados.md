# Sprint 24 — Runbook da fila de ingestão

Este runbook cobre a operação da Central de Dados com PostgreSQL, Redis e RQ. O fluxo
normal é:

```text
API -> op.ingest_job + fila "ingest" no Redis -> worker RQ -> progresso/resultado no Postgres
```

O PostgreSQL é a fonte de verdade do estado exibido pela interface. O Redis é o transporte
durável da fila; no Compose, AOF (`appendfsync everysec`) e o volume `redisdata` preservam
jobs em reinícios. A API não deve consumir a fila dentro do processo HTTP quando
`INGEST_WORKER_IN_PROCESS=false`.

## Variáveis mínimas

Copie `.env.example` para `.env` e substitua todos os segredos antes de usar fora de
desenvolvimento:

```dotenv
POSTGRES_USER=postgres
POSTGRES_PASSWORD=***
POSTGRES_DB=plataforma_fiscal
APP_DB_ROLE=plataforma_app
APP_DB_PASSWORD=***
DATABASE_URL=postgresql+psycopg2://plataforma_app:***@localhost:5432/plataforma_fiscal
DATABASE_ADMIN_URL=postgresql+psycopg2://postgres:***@localhost:5432/plataforma_fiscal
REDIS_URL=redis://localhost:6379/0
INGEST_WORKER_IN_PROCESS=false
APP_ENV=production
JWT_SECRET=***
```

`POSTGRES_PASSWORD`, `APP_DB_PASSWORD` e `JWT_SECRET` são obrigatórios no Compose; ele
não possui senha conhecida como fallback. Nunca exponha as portas 5432 ou 6379 à
Internet. O Compose publica PostgreSQL e Redis apenas no loopback; se estiverem em outro
host, use rede privada, autenticação e TLS.

## Docker Compose (Windows, Linux ou macOS)

Pré-requisitos: Docker com o plugin Compose. Os comandos abaixo não apagam volumes:

```bash
docker compose config
docker compose up -d --build
docker compose ps
make smoke-infra
```

Sem GNU Make:

```bash
docker compose exec -T redis redis-cli ping
docker compose exec -T api alembic current
docker compose exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read().decode())"
docker compose exec -T worker rq info --url redis://redis:6379/0
```

Resultados esperados: `PONG`, Alembic em `0029_sprint24_job_integrity (head)`, `/health`
com `status=ok`, fila `ingest` e ao menos um worker RQ registrado.

Para subir apenas a infraestrutura da fila ou recriar o worker:

```bash
make redis
make worker
docker compose up -d --no-deps --force-recreate worker
```

O worker depende diretamente de PostgreSQL e Redis, não da API. Seu bootstrap aguarda o
schema necessário, reconcilia execuções abandonadas e reentrega jobs persistidos antes de
começar a consumir. Assim, reiniciar ou indisponibilizar o processo HTTP não interrompe o
consumidor.

## Desenvolvimento nativo no Windows

O caminho recomendado é manter PostgreSQL/Redis no Docker e executar API/worker no host.
Depois de criar o venv e instalar `.[dev]`, use terminais PowerShell separados.

Terminal 1:

```powershell
.\.venv\Scripts\python.exe -m scripts.bootstrap_db
.\.venv\Scripts\python.exe -m alembic upgrade head
$env:INGEST_WORKER_IN_PROCESS = "false"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --app-dir src
```

Terminal 2:

```powershell
$env:REDIS_URL = "redis://localhost:6379/0"
.\.venv\Scripts\python.exe -m app.workers.ingest_worker
```

Smoke local:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
.\.venv\Scripts\rq.exe info --url $env:REDIS_URL
```

Se Docker não estiver disponível, use um Redis compatível executado no WSL ou como
serviço local e confirme `PONG` antes de iniciar o worker. O pacote Python `redis` sozinho
não instala o servidor.

## Ubuntu

### Opção recomendada: Compose

Instale Docker/Compose pelo procedimento oficial da distribuição, mantenha `.env` com
permissão restrita e execute:

```bash
docker compose config
docker compose up -d --build
docker compose ps
make smoke-infra
```

Mantenha as portas 5432, 6379 e 8000 restritas ao loopback. Publique a API por um proxy reverso
com TLS. Para atualizar sem remover os volumes:

```bash
docker compose build api worker
docker compose up -d api
docker compose up -d worker
```

Não execute `docker compose down -v`: essa opção remove `pgdata` e `redisdata`.

### Worker nativo com systemd

Quando API/Postgres/Redis forem serviços externos ao Compose, crie uma unidade semelhante
a esta, ajustando usuário, caminhos e `EnvironmentFile`:

```ini
[Unit]
Description=Plataforma Fiscal - worker RQ de ingestao
After=network-online.target redis-server.service
Wants=network-online.target

[Service]
Type=simple
User=plataforma
Group=plataforma
WorkingDirectory=/opt/plataforma/backend_plataforma_fiscal
EnvironmentFile=/etc/plataforma-fiscal/backend.env
Environment=PYTHONPATH=/opt/plataforma/backend_plataforma_fiscal/src
ExecStart=/opt/plataforma/backend_plataforma_fiscal/.venv/bin/python -m app.workers.ingest_worker
Restart=always
RestartSec=5
TimeoutStopSec=3900

[Install]
WantedBy=multi-user.target
```

Depois:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now plataforma-ingest-worker
sudo systemctl status plataforma-ingest-worker
journalctl -u plataforma-ingest-worker -f
```

Execute migrations antes de liberar tráfego para a API/worker.

## Monitoramento

Estado dos containers e logs:

```bash
docker compose ps
docker compose logs --tail=200 api
docker compose logs --tail=200 worker
docker compose logs -f --since=10m worker
```

Fila e workers RQ:

```bash
docker compose exec -T worker rq info --url redis://redis:6379/0
docker compose exec -T redis redis-cli LLEN rq:queue:ingest
docker compose exec -T redis redis-cli INFO persistence
docker compose exec -T redis redis-cli INFO memory
```

Estado persistido dos jobs:

```bash
docker compose exec -T postgres psql -U postgres -d plataforma_fiscal -c "select status, count(*) from op.ingest_job group by status order by status"
docker compose exec -T postgres psql -U postgres -d plataforma_fiscal -c "select id, fonte, status, progresso_pct, itens_ok, itens_erro, criado_em, iniciado_em, terminado_em from op.ingest_job where status in ('na_fila','executando','falhou') order by criado_em"
```

Alertas operacionais mínimos:

- worker ausente no `rq info`;
- fila crescendo continuamente;
- jobs em `executando` sem mudança de progresso;
- `aof_last_write_status` diferente de `ok`;
- reinícios repetidos do container `worker`;
- espaço em disco insuficiente para `pgdata` ou `redisdata`.

## Recovery

1. Registre o ID do job e preserve os logs antes de agir.
2. Se Redis estiver saudável e o worker parado, suba/reinicie somente o worker:

   ```bash
   docker compose up -d worker
   docker compose restart worker
   docker compose logs --tail=200 worker
   docker compose exec -T worker rq info --url redis://redis:6379/0
   ```

3. Depois de um restart normal do Redis, confirme o AOF e a fila:

   ```bash
   docker compose exec -T redis redis-cli INFO persistence
   docker compose exec -T redis redis-cli LLEN rq:queue:ingest
   ```

4. Para job com status `falhou`, use **Retry** na Central de Dados. O retry da aplicação
   preserva auditoria e reexecuta somente itens com erro; não reenvie manualmente a função
   Python pelo CLI.
5. Jobs `na_fila` ausentes no Redis são reentregues automaticamente pelo bootstrap e pela
   manutenção periódica do worker. O id RQ por tentativa usa deduplicação atômica
   (`unique=True`), então várias instâncias podem executar a reconciliação.
6. Um job `executando` cujo `iniciado_em` ultrapasse `JOB_TIMEOUT` mais a tolerância do
   monitor é devolvido automaticamente à fila. Checkpoints concluídos são preservados e a
   tentativa seguinte processa somente unidades pendentes. Antes desse cutoff, não duplique
   o job manualmente.

Em caso de corrupção ou perda do volume, restaure o backup antes de iniciar produtores.
AOF reduz perda em restart, mas não substitui backup do volume nem reconciliação com o
estado persistido no PostgreSQL.
