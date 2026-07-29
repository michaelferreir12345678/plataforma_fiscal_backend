# Sprint 28 — Validação integrada com dados reais e preparação para produção

> A última do ciclo. O objetivo não era construir: era **descobrir o que estava errado**
> antes que um gestor descobrisse por nós. Descobriu-se mais do que se esperava.

---

## O achado que justifica a sprint inteira

A validação em lote comparou o número da plataforma com o **demonstrativo que o próprio
ente publicou**, em 7 entes × 2 exercícios × 7 verificações. Dezenove células não
bateram — todas de **pessoal** e **dívida**, todas no mesmo sentido: **para menos**.

A causa não estava no numerador. A despesa líquida e a DCL batiam com o publicado. O
erro era o **denominador**: o teto do art. 20 da LRF (e o da Resolução 40/2001) incide
sobre a *Receita Corrente Líquida Ajustada* — que a EC 105/2019 manda calcular deduzindo
as transferências recebidas por emenda individual —, e a plataforma dividia pela RCL
cheia. O demonstrativo publica as duas receitas, lado a lado, e nós usávamos a errada.

| Ente | Exerc. | Plataforma | Publicado | Δ |
|---|---:|---:|---:|---:|
| Maracanaú | 2024 | 46,91% | 48,20% | **1,29 p.p.** |
| Caucaia | 2024 | 50,72% | 51,45% | 0,73 p.p. |
| Fortaleza | 2024 | 47,21% | 47,87% | 0,66 p.p. |

Num monitor de limites, errar **para menos** é o pior sentido possível: a faixa
prudencial começa em 51,3%, e um município a 51,5% oficial apareceria a 50,2% —
confortável. O produto existe para mostrar esse risco e estava escondendo-o.

Eram **dois defeitos distintos**, achados pela mesma medição:

* **Dívida** — o `fato_divida` já estava certo (34,84%); quem errava era a
  materialização do mart, que jogava fora o `pct_rcl` correto e redividia o valor
  absoluto pela RCL cheia. Duas telas do mesmo produto discordariam sobre o mesmo limite.
* **Pessoal** — errava na origem: o serviço nunca leu a RCL ajustada.

**Correção** (`migration 0035`): `gold.fato_pessoal` ganhou `rcl_ajustada`, guardada ao
lado da apuração — sem ela o percentual não é reproduzível quando a regra de dedução
mudar de novo. Ambos os caminhos passam a usar o denominador publicado, com *fallback*
declarado para a RCL onde o ente não publica a linha.

**Depois da correção e da rematerialização: 65 conferências `ok`, zero divergências.**

---

## 1. Validação fiscal em lote

`scripts/validacao_fiscal.py` — reproduzível, versionado, e **sai com código 1** quando
há divergência sem causa conhecida. É o que trava um deploy.

Amostra estratificada, porque erro de cálculo raramente é uniforme:

| Estrato | Ente |
|---|---|
| capital do território | Fortaleza |
| município médio | Caucaia, Maracanaú, Sobral |
| município pequeno (< 50 mil hab.) | São Benedito |
| ente estadual | Governo do Ceará |
| capital de outra UF | Recife |

**Sobre o estrato "pequeno com RGF semestral":** a LRF (art. 63) faculta a periodicidade
semestral a municípios com menos de 50 mil habitantes. Conferindo o dado real: dos
**131 municípios do CE nessa faixa, todos os 131 publicam quadrimestralmente**. Nenhum
exerce a faculdade. O estrato foi preenchido com um município pequeno de fato (São
Benedito, 49.829 hab.), e fica registrado que a cadência semestral **não ocorre no
território** — o script não assume `Q3`, pergunta ao dado qual foi o último RGF entregue.

Cada célula sai classificada, e a distinção que mais importa é entre **dado** e
**cálculo**: linha não publicada pelo ente não é defeito nosso; percentual que não fecha,
é. Das 98 conferências: 65 `ok`, 7 `sem_publicacao`, 26 `nao_aplicavel` — estas últimas
são os Anexos 8/12, que **a API do SICONFI não expõe** (vêm do PDF do portal, por
conector próprio, hoje carregado para um ente).

Duas comparações erradas minhas, corrigidas durante o trabalho e registradas porque
ensinam a ler o demonstrativo:

* **Caixa** — somar todas as linhas do Anexo 05 conta o mesmo real duas vezes: o anexo
  traz subtotais no meio das fontes. Comparando **folha contra folha**, a diferença é
  **R$ 0,00**. E não serve comparar com o `TOTAL (IV)`: ele não inclui os recursos
  extraorçamentários, que a gold guarda como fonte.
* **Resultado primário** — a gold guarda a linha **COM RPPS**; eu comparei com a SEM
  RPPS. Fica o registro de uma assimetria do modelo: `resultado_primario` é COM RPPS e
  `resultado_primario_abaixo` é SEM RPPS, de modo que a reconciliação acima×abaixo
  compara universos diferentes.

---

## 2. Trava anti-mock

`quality/anti-mock.mjs`, no CI do frontend. Duas verificações:

1. **Nenhum array fiscal estático** em `pages`, `services`, `components`, `layouts`. O
   corte é três ou mais objetos com vocabulário fiscal e números — abaixo disso é
   configuração de UI, acima é tabela, e tabela de número fiscal vem da API.
2. **Os 13 itens da auditoria §4**, um a um, por identidade: arquivo que não pode voltar
   a existir, padrão que não pode reaparecer, garantia que não pode sumir (o `AppShell`
   tem de continuar chamando `/me`; o `LoginGate`, de condicionar a credencial de
   demonstração a `APP_ENV=local`).

A allowlist é explícita e **cada entrada tem o motivo escrito** — allowlist sem
justificativa vira depósito.

**A trava foi testada nos dois sentidos**: reintroduzindo um array fiscal na
`LimitesPage` (pegou, com arquivo, linha e os termos que a caracterizam) e recriando
`carteiraData.ts` (pegou, citando o item 1 da auditoria). Gate que nunca dispara não
vale nada.

---

## 3. Segurança

**Cabeçalhos** (`shared/seguranca.py`), em toda resposta, inclusive erro e 429:
CSP `default-src 'none'` (a API responde JSON, nunca HTML com script), `X-Frame-Options`,
`nosniff`, `Referrer-Policy: no-referrer`, COOP/CORP, `Permissions-Policy`. HSTS **só sob
HTTPS** — anunciá-lo em HTTP puro é fingir proteção que o navegador ignora.

**Freio no autenticador.** Por **origem + identidade tentada**, não só por IP: atrás de
um NAT institucional, limitar por IP puniria a prefeitura inteira por causa de um. E
conta apenas **tentativa falha** — acertar a senha devolve a cota, então quem erra duas
vezes e acerta na terceira não fica mais perto do bloqueio.

**Segredos.** `.env` está no `.gitignore` e o histórico foi conferido: a chave do Gemini
**nunca foi commitada** (`git log --all -p | grep AIza` → 0). A auditoria a tratava como
exposta; ela está apenas em máquina de desenvolvimento. A rotação continua recomendada
como higiene, e o procedimento está no runbook §1.1.

**`pip-audit --strict`** no CI, sem `|| true`: aviso que não quebra nada não é auditoria.

### Pen-test de RLS

Testar só pela API responde metade da pergunta. A outra metade é **atacar o banco com o
contexto do cliente A e tentar ler as linhas de B** — porque é isso que sobra se uma
rota nova esquecer o filtro. Os testes rodam consultas **sem `where org_id`** e exigem
isolamento por construção; verificam ainda que sessão sem contexto lê **zero** (*default
deny*) e que a role do runtime não é dona das tabelas nem tem `BYPASSRLS` — dono de
tabela ignora *policy* por padrão, e aí o isolamento seria decorativo.

13 testes, nenhum pulado.

---

## 4. Carga

`quality/carga/cockpit_drill.js` (k6) versionado para o pipeline, e
`scripts/carga_local.py` para medir onde o k6 não está instalado — porque **evidência
não se promete**.

**O critério (50 usuários, P95 < 800 ms) não foi atingido, e não pôde ser medido com
justiça** nesta bancada: 8 núcleos hospedando ao mesmo tempo a API e um gerador de 50
threads. Dito isso, a medição encontrou **dois defeitos reais**, ambos corrigidos:

1. **Pool de conexões herdado do padrão.** 5 + 10 conexões com espera de 30 s: 50
   usuários disputando 15 conexões, cada requisição segurando a sua por mais de um
   segundo, formavam fila até estourar. O sintoma enganava — parecia consulta lenta e
   era espera por conexão. Agora dimensionado e configurável, com espera curta de
   propósito: pool cheio deve falhar visível, não pendurar meio minuto.
2. **Motor de alertas na leitura.** Avaliar é **escrever**: 50 leitores viravam 50
   escritores concorrentes nas mesmas linhas de `op.alerta`. Com uma janela de 30 s
   entre avaliações do mesmo ente, `/alertas` caiu de **30 s (timeout) para 2,5 s**.

Perfil com 10 usuários e 4 workers, tudo respondendo 200: drill ~900 ms,
`/uf/ranking` 995 ms, `/limites` 1,4 s, `/alertas` 2,5 s, `/receita` 2,9 s,
`/despesa` 5,3 s, **`/cockpit` 8,1 s**.

O cockpit segue sendo o caminho mais caro, e a dívida é a mesma que a Sprint 27
registrou: a Sprint 22 o desenhou **sem mart próprio**, então ele avalia alertas e roda
modelos de previsão a cada carga. Fechar o critério exige tirar as duas coisas do
caminho de leitura — mudança de arquitetura, não ajuste, e por isso não entrou numa
sprint de validação.

---

## 5. Produção

`docker-compose.prod.yml` — três diferenças em relação ao de desenvolvimento, cada uma
por um incidente que evita:

1. **Migration não roda no boot da API**, e sim num serviço *one-shot*: com N réplicas
   subindo juntas, todas disputariam o `alembic_version` e o deploy entraria em laço.
2. **Banco e fila não publicam porta.**
3. **Workers separados por responsabilidade** — e o `scheduler_worker` (novo) é o
   **único** processo com relógio: rodar o agendador em cada réplica emitiria o mesmo
   relatório N vezes. Isso também fechou a pendência que a Sprint 26 deixou aberta, de
   `quality_tasks` nunca ter sido registrado no startup.

`docs/runbook_producao.md` cobre segredos, subida do zero, deploy, backup/restore,
rollback e operação diária — com o que foi **executado** marcado como tal, e uma seção
final dizendo o que **não** foi.

### Testado em 28/07

* **Backup** — dump *custom* de 387,5 MB.
* **Restore** em banco limpo — 5.594 entes, 3.163 linhas de RCL, 14.629 de
  `mart_indicador`, 9 licenças, versão de migration preservada.
* **Downgrade das migrations 0025+** (0035 → 0024) e **upgrade de volta**, na cópia
  restaurada, nunca no banco vivo. No caminho de volta, a `0034` **reconstituiu as
  licenças a partir da carteira** — nenhuma organização perde acesso num rollback.

---

## 6. Evidências

| Item | Resultado |
|---|---|
| Validação fiscal em lote | **65 `ok`, 0 divergências** (eram 19 antes da correção) |
| Trava anti-mock | passa; **testada nos dois sentidos** |
| Segurança + pen-test RLS | 13 testes, 0 pulados |
| `ruff` + `mypy` | limpos (215 arquivos) |
| Suíte backend | ver §7 |
| Suíte frontend | 113 testes |
| Backup / restore / downgrade | executados, com números acima |
| Carga | executada; critério **não atingido** — §4 |

## 7. O que fica em aberto

Declarado, não escondido:

1. **P95 < 800 ms com 50 usuários** — depende de tirar alerta e previsão do caminho de
   leitura do cockpit. É a dívida que a Sprint 27 já havia registrado, agora com
   evidência de carga além da de latência.
2. **Deploy real em nuvem** — o compose está escrito e revisado, não executado num host
   com TLS e proxy.
3. **Cobertura dos Anexos 8/12** — hoje um ente. A API do SICONFI não os expõe; ampliar
   depende de ingerir PDF por ente.
4. **Assimetria COM/SEM RPPS** no resultado primário — registrada em §1, não corrigida:
   mexer nisso muda a reconciliação acima×abaixo da Sprint 9 e pede sprint própria.
