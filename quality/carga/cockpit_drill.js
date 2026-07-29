/**
 * Teste de carga — jornada real de leitura (Sprint 28).
 *
 * 50 usuários simultâneos fazendo o que um gestor faz de verdade: abrir o cockpit,
 * descer no drill de receita e despesa, olhar limites e comparar na carteira/UF. Não é
 * um martelo em um endpoint só — carga sintética contra a rota mais barata dá um número
 * bonito e mente sobre o produto.
 *
 * Critério de aceite da sprint: **P95 < 800 ms** nas rotas de página.
 *
 * Uso:
 *   k6 run -e BASE_URL=http://127.0.0.1:8000 \
 *          -e EMAIL=... -e SENHA=... \
 *          -e ENTE=2304400 -e PERIODO=2024-B6 -e UF=23 \
 *          quality/carga/cockpit_drill.js
 *
 * As credenciais vêm do ambiente; nada de segredo versionado.
 */

import http from 'k6/http';
import { check, group, sleep } from 'k6';
import { Trend, Rate } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'http://127.0.0.1:8000';
const EMAIL = __ENV.EMAIL;
const SENHA = __ENV.SENHA;
const ENTE = __ENV.ENTE || '2304400';
const PERIODO = __ENV.PERIODO || '2024-B6';
const UF = __ENV.UF || '23';

/** Latência das rotas de **página**, que é o que o critério mede. */
const paginaDur = new Trend('pagina_duracao', true);
/** Drill é interação: o usuário espera menos por ela do que pela carga inicial. */
const drillDur = new Trend('drill_duracao', true);
const erros = new Rate('erros_funcionais');

export const options = {
  scenarios: {
    leitura: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 50 }, // sobe até os 50 usuários do critério
        { duration: '2m', target: 50 },  // patamar: é aqui que o P95 vale
        { duration: '20s', target: 0 },
      ],
      gracefulRampDown: '20s',
    },
  },
  thresholds: {
    // O critério da sprint, expresso como condição de falha do próprio teste.
    'pagina_duracao': ['p(95)<800'],
    'drill_duracao': ['p(95)<800'],
    'erros_funcionais': ['rate<0.01'],
    'http_req_failed': ['rate<0.01'],
  },
};

export function setup() {
  if (!EMAIL || !SENHA) {
    throw new Error('Defina EMAIL e SENHA no ambiente — credencial não se versiona.');
  }
  const r = http.post(
    `${BASE}/auth/login`,
    { username: EMAIL, password: SENHA },
    { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } },
  );
  if (r.status !== 200) {
    throw new Error(`Login falhou (${r.status}). Sem token não há teste de carga.`);
  }
  return { token: r.json('access_token') };
}

function medir(resposta, trend, rotulo) {
  trend.add(resposta.timings.duration);
  const ok = check(resposta, {
    [`${rotulo}: 200`]: (r) => r.status === 200,
    [`${rotulo}: corpo não vazio`]: (r) => r.body && r.body.length > 2,
  });
  erros.add(!ok);
  return ok;
}

export default function (dados) {
  const params = {
    headers: { Authorization: `Bearer ${dados.token}` },
    // Sem tags por ente/período: agrupar por template mantém a métrica legível e não
    // joga identificador de município no relatório.
    tags: { jornada: 'cockpit_drill' },
  };

  group('1. cockpit', () => {
    medir(http.get(`${BASE}/entes/${ENTE}/cockpit?periodo=${PERIODO}`, params), paginaDur, 'cockpit');
    medir(http.get(`${BASE}/alertas?escopo=ente&ente=${ENTE}`, params), paginaDur, 'alertas');
  });
  sleep(1);

  group('2. drill de receita', () => {
    medir(
      http.get(`${BASE}/entes/${ENTE}/receita?periodo=${PERIODO}`, params),
      paginaDur, 'receita',
    );
    medir(
      http.get(`${BASE}/entes/${ENTE}/receita/arvore?periodo=${PERIODO}`, params),
      drillDur, 'receita/arvore',
    );
  });
  sleep(1);

  group('3. drill de despesa', () => {
    medir(
      http.get(`${BASE}/entes/${ENTE}/despesa?periodo=${PERIODO}`, params),
      paginaDur, 'despesa',
    );
    medir(
      http.get(`${BASE}/entes/${ENTE}/despesa/arvore?periodo=${PERIODO}&eixo=funcao`, params),
      drillDur, 'despesa/arvore',
    );
  });
  sleep(1);

  group('4. limites e comparação', () => {
    medir(
      http.get(`${BASE}/entes/${ENTE}/limites?periodo=${PERIODO}`, params),
      paginaDur, 'limites',
    );
    medir(
      http.get(`${BASE}/uf/${UF}/ranking?indicador=rcl&periodo=${PERIODO}`, params),
      paginaDur, 'ranking uf',
    );
  });
  sleep(2);
}
