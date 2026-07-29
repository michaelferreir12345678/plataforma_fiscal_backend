# Sprint 25E — Alertas, Previsões, Relatórios & Assistente

> Auditoria §§2.11, 2.13, 2.14, 2.15. Fecha a Sprint 25.

**Aceite:** toda página fiscal tem export próprio + caminho de 1 clique para o relatório
institucional. **Resultado:** 14 das 18 páginas têm `ExportButton` (as 4 restantes —
Admin, Central de Dados, Assistente e a própria Relatórios — não são páginas de análise
fiscal), e **zero endpoints ociosos** nos quatro módulos.

---

## 1. Alertas: quem tratou, quando, em quanto tempo

`op.alerta` guardava só o `status` corrente: dava para saber que um alerta estava
resolvido, não **quando** nem **por quem**. **Migration 0032** acrescenta `resolvido_em`
e `resolvido_por` (FK para `op.usuario`, `ON DELETE SET NULL`).

Duas decisões:

- **Reabrir apaga a assinatura.** Manter o nome de quem fechou num alerta que voltou à
  fila atribuiria a essa pessoa um estado que ela não escolheu.
- **O histórico não reavalia.** `/alertas` dispara o motor (materialize-on-read); o
  histórico é registro do passado, e reavaliar ali poderia ressuscitar na tela um alerta
  que o gestor acabou de fechar.

`GET /alertas/historico?escopo=&ente=&categoria=&limite=` devolve os tratados, o tempo
médio de tratamento e a contagem por categoria. Tratar um alerta passou a gravar
`op.audit_log` (`alerta.resolvida` / `alerta.descartada`) — decisão de gestão fica na
trilha. Alertas fechados antes desta sprint não têm instante de resolução, e a resposta
diz isso em vez de calcular um tempo médio sobre nada.

Na tela: histórico ao lado do calendário, com "reabrir", export da fila **e** da trilha.

## 2. Previsões: horizonte e as três camadas lado a lado

- **Horizonte configurável** (+2/+4/+6/+8/+12) — era fixo em 4. O simulador de cenário
  usa o mesmo horizonte da tela, senão a comparação seria com outra coisa.
- **`GET /entes/{ibge}/projecao/comparacao`**: as três camadas juntas, cada uma com valor
  final, IC, **amplitude média do IC** (a medida honesta de incerteza), R² quando existe,
  nº de observações e se cruza o limite. O modelo indisponível aparece **com o motivo**,
  em vez de sumir.
- **Não há ranking por acurácia.** Com séries de poucos períodos, separar treino e teste
  mediria ruído; o critério é a ordem de preferência (regressão → Holt → fechamento) e a
  viabilidade, e a resposta declara isso no campo `criterio_escolha`.

Com dado real de Fortaleza (dívida, horizonte 6): regressão com exógenas IC médio 28,05
p.p., Holt 36,22, fechamento 30,45 — três respostas diferentes para a mesma pergunta, que
é exatamente o que o gestor precisa ver antes de citar um número.

## 3. Relatórios: agendamento deixou de ser via de mão única

Dava para **criar** uma recorrência e nunca mais vê-la. Agora:
`GET /relatorios/agendamentos`, `PATCH /relatorios/agendamentos/{id}` (periodicidade,
formato, período, próxima execução, `ativo`) e `DELETE`.

**Desativar preserva o registro** — o histórico de que a regra existiu e por quanto tempo
rodou faz parte da trilha; excluir é o caminho explícito para remover de vez. A UI lista
com estado, próxima e última execução.

## 4. Assistente: contexto da tela e histórico

- **"Pergunte sobre esta tela"**: o atalho vive no shell (uma edição, vale para todas as
  páginas fiscais) e leva a `/assistente?de=<rota>`. O `PerguntaRequest` ganhou `pagina`.
- **A pergunta manda.** A página só entra quando a pergunta não nomeia indicador ("e isto
  aqui, está bom?") — quem está na tela de dívida e pergunta de educação quer educação.
  Rota desconhecida não inventa contexto.
- **`/assistant/conversas`** existia desde a Sprint 17 sem consumidor: agora abre sob
  demanda no rodapé do chat, marcando as **recusas fundamentadas** (o registro de quando
  o assistente se negou a responder por falta de fonte).

## 5. Export por página

`ExportButton` (Sprint 25A) já embute o link `relatórios?modelo=…`, então export próprio
e caminho de 1 clique vêm juntos. Faltavam Limites, Cockpit e Carteira — incluídos aqui,
além de Alertas e Previsões.

---

## 6. Testes

- `tests/test_sprint25e_alertas_previsoes_relatorios.py` (10): alerta tratado sai da fila
  com assinatura e tempo; reabrir apaga a assinatura; trilha de auditoria; histórico não
  ressuscita alerta; comparação com as três camadas, incerteza e critério; horizonte muda
  o tamanho da projeção; CRUD de agendamento incluindo desativar-sem-perder e RLS entre
  organizações; periodicidade inválida recusada; regra pura do contexto de página.
- `src/test/sprint25e.test.tsx` (15): histórico com autor/dias e estado vazio explicado;
  export da fila; horizonte; três camadas (com a indisponível e seu motivo); critério na
  tela; lista/desativa/vazio dos agendamentos; contexto da tela anunciado, enviado e não
  inventado; histórico de conversas sob demanda com recusa marcada; aceite do export +
  link institucional em página fiscal.

`ruff` + `mypy` (199 arquivos) + `pytest` **334 testes** verdes; `tsc` + `vitest`
**86 testes** + `build` verdes.

> Nota de ambiente: `src/test/setup.ts` passou a fazer *polyfill* de `Element.scrollTo` —
> jsdom não a implementa e o chat do assistente rola sozinho; era falha do ambiente de
> teste, não do produto.
