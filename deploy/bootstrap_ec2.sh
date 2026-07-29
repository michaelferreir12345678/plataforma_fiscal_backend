#!/usr/bin/env bash
# Bootstrap da Plataforma de Inteligência Fiscal numa EC2 Ubuntu.
#
# Idempotente: pode rodar de novo sem duplicar nada. Não recebe segredo por parâmetro —
# as senhas são geradas AQUI, na própria instância, e nunca trafegam.
#
# Desenho: só a porta 80 é pública. O nginx serve o frontend e faz proxy de /api para a
# API, que fica em 127.0.0.1:8000 (nunca exposta à internet). Mesma origem ⇒ sem CORS, e
# o IP não fica cravado no bundle: trocar o IP não exige rebuild.
set -euo pipefail

RAIZ=/opt/plataforma
# Os repositórios são privados: o clone vai por SSH com **chave de deploy** — uma por
# repositório, somente leitura, revogável de dentro do próprio repositório. A chave privada
# nasce e morre nesta instância; nada é digitado nem colado.
# Repositório público? Basta exportar as URLs https antes de rodar.
REPO_BACK=${REPO_BACK:-git@github.com:michaelferreir12345678/plataforma_fiscal_backend.git}
REPO_FRONT=${REPO_FRONT:-git@github.com:michaelferreir12345678/plataforma_fiscal_frontend.git}

log() { echo -e "\n\033[1;36m==> $*\033[0m"; }

# --------------------------------------------------------------------------- #
log "1/9  Identidade da instância"
TOKEN=$(curl -sS -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 300" || true)
IP_PUBLICO=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo "")
[ -z "$IP_PUBLICO" ] && IP_PUBLICO=$(hostname -I | awk '{print $1}')
echo "    IP público: $IP_PUBLICO"
. /etc/os-release; echo "    SO: $PRETTY_NAME (codename: ${VERSION_CODENAME:-?})"

# --------------------------------------------------------------------------- #
log "2/9  Docker"
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq ca-certificates curl gnupg git nginx openssl
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>/dev/null || true
  sudo chmod a+r /etc/apt/keyrings/docker.gpg 2>/dev/null || true
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  # O repositório oficial pode ainda não publicar para uma release muito nova (26.04 é
  # de abril). Se não houver canal, cai para o pacote do Ubuntu em vez de falhar.
  if sudo apt-get update -qq 2>/dev/null && \
     sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin 2>/dev/null; then
    echo "    docker-ce (repositório oficial)"
  else
    echo "    repositório oficial indisponível para ${VERSION_CODENAME}; usando o do Ubuntu"
    sudo rm -f /etc/apt/sources.list.d/docker.list
    sudo apt-get update -qq
    sudo apt-get install -y -qq docker.io docker-compose-v2
  fi
  sudo systemctl enable --now docker
  sudo usermod -aG docker "$USER" || true
else
  echo "    já instalado: $(docker --version)"
fi
command -v nginx >/dev/null || sudo apt-get install -y -qq nginx
DC="sudo docker compose"; $DC version >/dev/null 2>&1 || DC="sudo docker-compose"

# --------------------------------------------------------------------------- #
log "3/9  Repositórios"
sudo mkdir -p "$RAIZ" && sudo chown "$USER:$USER" "$RAIZ"

if [[ "$REPO_BACK" == git@* ]]; then
  CHAVE="$HOME/.ssh/plataforma_deploy"
  mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
  ssh-keygen -F github.com >/dev/null 2>&1 || \
    ssh-keyscan -t ed25519 github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null
  if [ ! -f "$CHAVE" ]; then
    ssh-keygen -t ed25519 -N "" -C "deploy-plataforma@ec2" -f "$CHAVE" >/dev/null
  fi
  export GIT_SSH_COMMAND="ssh -i $CHAVE -o IdentitiesOnly=yes"
  if ! ssh -i "$CHAVE" -o IdentitiesOnly=yes -o BatchMode=yes -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
    cat <<AVISO

  A chave de deploy ainda não foi autorizada no GitHub. Copie a linha abaixo e
  cadastre-a em CADA repositório (backend e frontend):

      Settings > Deploy keys > Add deploy key
      (deixe "Allow write access" DESMARCADO — o deploy só precisa ler)

  ------------------------------------------------------------------
$(cat "$CHAVE.pub")
  ------------------------------------------------------------------

  Feito isso, rode este script de novo. Ele continua de onde parou.

AVISO
    exit 2
  fi
  echo "    chave de deploy autorizada"
fi

for par in "backend:$REPO_BACK" "frontend:$REPO_FRONT"; do
  dir="${par%%:*}"; url="${par#*:}"
  if [ -d "$RAIZ/$dir/.git" ]; then
    git -C "$RAIZ/$dir" pull --ff-only
  else
    git clone --depth 1 "$url" "$RAIZ/$dir"
  fi
done

# --------------------------------------------------------------------------- #
log "4/9  Segredos (gerados aqui; nunca trafegam)"
ENV="$RAIZ/backend/.env"
if [ ! -f "$ENV" ]; then
  umask 077
  cat > "$ENV" <<EOF
POSTGRES_USER=postgres
POSTGRES_PASSWORD=$(openssl rand -base64 33 | tr -d '/+=' | head -c 40)
POSTGRES_DB=plataforma_fiscal
APP_DB_ROLE=plataforma_app
APP_DB_PASSWORD=$(openssl rand -base64 33 | tr -d '/+=' | head -c 40)
JWT_SECRET=$(openssl rand -base64 48 | tr -d '\n')
CORS_ORIGINS=http://$IP_PUBLICO
API_WORKERS=4
GEMINI_API_KEY=
EOF
  chmod 600 "$ENV"
  echo "    .env criado (chmod 600). A chave do Gemini fica vazia — preencha depois."
else
  echo "    .env já existe; preservado (as senhas do banco não podem mudar sozinhas)."
  # O IP muda a cada stop/start sem Elastic IP; o CORS acompanha.
  sed -i "s|^CORS_ORIGINS=.*|CORS_ORIGINS=http://$IP_PUBLICO|" "$ENV"
fi

# --------------------------------------------------------------------------- #
log "5/9  Publicação local da API (só 127.0.0.1)"
cat > "$RAIZ/backend/docker-compose.ec2.yml" <<'EOF'
# Override de deploy: o compose de produção deixa a API em `expose` porque quem publica
# é o proxy. Aqui o proxy é o nginx do host, então a porta abre apenas no loopback.
services:
  api:
    ports:
      - "127.0.0.1:8000:8000"
EOF

log "6/9  Subindo os serviços (migrate roda antes da API)"
cd "$RAIZ/backend"
$DC -f docker-compose.prod.yml -f docker-compose.ec2.yml up -d --build
echo "    aguardando a API responder…"
for i in $(seq 1 60); do
  curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 && { echo "    API no ar"; break; }
  [ "$i" = 60 ] && { echo "    !! API não respondeu; veja: $DC -f docker-compose.prod.yml logs api"; exit 1; }
  sleep 5
done

# --------------------------------------------------------------------------- #
log "7/9  Frontend (build em container: sem Node no host)"
cd "$RAIZ/frontend"
sudo docker run --rm -v "$PWD":/app -w /app node:22-alpine sh -lc '
  npm ci --silent && VITE_API_BASE_URL=/api npm run build
'
sudo mkdir -p /var/www/plataforma
sudo rsync -a --delete dist/ /var/www/plataforma/

sudo tee /etc/nginx/sites-available/plataforma >/dev/null <<'EOF'
server {
    listen 80 default_server;
    server_name _;
    root /var/www/plataforma;
    index index.html;

    # SPA: qualquer rota desconhecida devolve o index (o router é do cliente).
    location / { try_files $uri $uri/ /index.html; }

    # Mesma origem: o browser não faz preflight e não há CORS para configurar errado.
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # A ingestão de um exercício leva dezenas de minutos; o padrão de 60s cortaria.
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }
    client_max_body_size 32m;
}
EOF
sudo ln -sf /etc/nginx/sites-available/plataforma /etc/nginx/sites-enabled/plataforma
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

# --------------------------------------------------------------------------- #
log "8/9  Seed (organizações e usuários de exemplo)"
cd "$RAIZ/backend"
if $DC -f docker-compose.prod.yml -f docker-compose.ec2.yml run --rm api python -m scripts.seed; then
  echo "    seed aplicado"
else
  echo "    seed já aplicado ou falhou — verifique acima (não é fatal para o serviço)"
fi

# --------------------------------------------------------------------------- #
log "9/9  Conformação de dim_ente (cadastro dos entes da carteira)"
# Sem esta etapa a instalação nasce navegável mas **inerte**: os dados fiscais entram,
# e o cockpit responde 422 "dim_ente sem esfera". Faz sentido — sem esfera não há limite
# legal aplicável, porque município e estado têm tetos diferentes —, mas o operador não
# tem como adivinhar que falta o cadastro. Então o bootstrap o resolve.
$DC -f docker-compose.prod.yml -f docker-compose.ec2.yml run --rm api python - <<'PYEOF' || echo "    (conformação pulada; rode depois pela Central de Dados)"
from app.core.db import admin_session
from app.modules.ingestion import service
from app.modules.ingestion.schemas import RunRequest
from app.shared.ingestion.client import RealClientResolver
from sqlalchemy import text

with admin_session() as s:
    entes = [r[0] for r in s.execute(text("SELECT DISTINCT cod_ibge FROM op.carteira_ente")).all()]
print(f"entes da carteira: {entes or 'nenhum'}")
if entes:
    resolver = RealClientResolver()
    try:
        with admin_session() as s:
            r = service.run(s, resolver, RunRequest(fonte="siconfi_entes", entes=entes, anos=[]))
            print(f"cadastro ingerido: {r.ingeridos}/{r.total_jobs}")
    finally:
        resolver.close()
PYEOF

log "PRONTO"
echo "  Frontend : http://$IP_PUBLICO"
echo "  API      : http://$IP_PUBLICO/api/health"
echo "  Login    : admin@sefaz.gov.br / senha1234  (TROQUE ANTES DE QUALQUER USO REAL)"
echo ""
echo "  Sem HTTPS o JWT trafega em claro. Não use com dado de cliente antes do TLS."
