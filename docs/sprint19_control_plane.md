# Sprint 19 — Control plane: superuser, licenças e identidade visual

> A sprint que faltava. O produto tinha dois níveis — a organização e o usuário dentro
> dela — e nenhum acima. Entrar com um cliente novo era rodar SQL na mão, e nada impedia
> uma organização de cadastrar na carteira um ente que ela não contratou.

---

## 1. O conceito: dois planos, não um papel a mais

O erro fácil aqui seria criar uma capacidade `superadministrar` no RBAC. Não serve:
capacidade é atributo de papel, papel pertence a uma organização, e o operador da
plataforma **não pertence a nenhuma**. Por isso `is_superuser` mora em `op.usuario`, e
`/platform` exige a flag — nunca a capacidade `administrar`, que é o topo do RBAC
*dentro* de um tenant. Quem administra a própria conta não pode licenciar a si mesmo
mais entes; se pudesse, licenciamento não significaria nada.

A flag é lida **do banco a cada requisição**, não do JWT: revogar um superuser não pode
depender de o token dele expirar.

---

## 2. `op.licenca` — o que a organização pode ver

| tipo | alvo | alcança |
|---|---|---|
| `ente` | `cod_ibge` | aquele ente |
| `uf` | `uf` (2 dígitos) | todos os municípios do território **e** o ente estadual |
| `global` | — | tudo (uso interno/demonstração) |

**Histórico preservado.** Suspender troca o `status`, não apaga a linha: "esta
organização podia ver este ente em março?" é pergunta de auditoria e de cobrança, e
some se a suspensão for um `DELETE`.

**Vigência por data, não por job.** `Licenca.vigente_em(dia)` exige status `ativa` **e**
data dentro da janela. Uma licença cujo prazo venceu ontem já não vale hoje, mesmo que
nenhuma rotina tenha passado para marcá-la como `expirada`. Deixar isso a cargo de um
job faria o acesso depender de o relógio ter passado por lá.

**Alvo coerente com o tipo** é `CHECK` no banco *e* validação no schema: licença de ente
sem IBGE não libera nada, e ninguém descobriria até o 403.

---

## 3. A mudança transversal: escopo ∩ licença

`shared/scope.py` ganhou o quarto termo, e é o mais forte:

```
escopo efetivo = (carteira ∪ expansão estadual) ∩ escopo do membership ∩ licença vigente
```

Carteira é o que a organização **quer** olhar; licença é o que ela **pode**. São 100
linhas de módulo, mas **46 módulos** dependem delas — foi por isso que a Sprint 28
declara a 19 como pré-requisito: fazer a 19 depois produziria evidência de RLS, carga e
validação fiscal sobre um caminho de autorização que ainda ia mudar.

Três decisões dentro disso:

1. **403 com causa própria.** `ente-nao-licenciado` é distinto de `scope-forbidden`.
   "Não está na sua carteira" e "sua licença não cobre" pedem ações opostas — uma é
   cadastro do próprio cliente, a outra é comercial. Unificar os dois faria o suporte
   perder tempo em toda ocorrência.
2. **A visão agregada filtra igual ao gate.** `carteira_scope_ibges` aplica a mesma
   cobertura; senão a carteira mostraria um total que o drill devolve como 403.
3. **A carteira não aceita o que a licença não cobre.** No unitário, 403; **em lote**,
   os recusados voltam em `nao_licenciados` — um ente fora da licença não derruba a
   importação inteira, e o administrador vê o que passou e o que não passou.

**Custo.** O gate roda em toda rota com `?ente=`. A cobertura é resolvida **uma vez por
requisição** (memória em `session.info`, e uma sessão é uma requisição), e invalidada
explicitamente quando uma licença muda — suspensão tem de valer na hora.

---

## 4. A migration não pode tirar o acesso de quem já tinha

Este é o ponto perigoso da sprint. A partir da `0034`, "sem licença" significa "sem
acesso" — e subir isso com a tabela vazia derrubaria **todas** as organizações
existentes no instante do deploy.

Por isso a migration **transcreve o escopo vigente** como licença: conta `estado` vira
licença de `uf` (era assim que a expansão territorial da Sprint 4 funcionava); as demais
recebem uma licença `ente` por item da carteira. Nada é concedido além do que a
organização já enxergava. Depois disso, a ausência de licença passa a significar de fato
ausência de acesso.

---

## 5. Identidade visual

`op.organizacao.logo_url` e `gold.dim_ente.brasao_url`. O brasão fica na **gold** porque
é atributo do ente — público e compartilhado —, não da organização que o monitora: duas
consultorias que acompanham Fortaleza veem o mesmo brasão.

Upload por `PUT /platform/orgs/{id}/logo` e `PUT /platform/entes/{ibge}/brasao`, em
storage configurável (disco no MVP, S3 depois), com limite de 2 MB e extensão validada.

---

## 6. Endpoints

| método | rota | o que faz |
|---|---|---|
| `POST` | `/platform/orgs` | provisiona org + papel admin + usuário inicial + licenças, **atomicamente** |
| `GET` | `/platform/orgs` | organizações com uso e licenças |
| `GET` | `/platform/uso` | consumo: entes, usuários, relatórios, consultas de IA |
| `GET` | `/platform/orgs/{id}/licencas` | histórico completo, inclusive suspensas |
| `POST` | `/platform/orgs/{id}/licencas` | concede |
| `PATCH` | `/platform/licencas/{id}` | suspende, reativa, prorroga |
| `PUT` | `/platform/orgs/{id}/logo` · `/platform/entes/{ibge}/brasao` | identidade visual |

Provisionar pela metade é pior que não provisionar: org sem papel não cria usuário, e
usuário sem licença entra e não vê nada. Por isso é tudo numa transação.

O bypass de RLS vive em `superuser_session`, ao lado da dependência que exige
`is_superuser`, e **não é exportado para rota de tenant nenhuma**. Ler uso agregado
exige enxergar entre organizações — que é exatamente o que a RLS proíbe no plano do
tenant.

Toda ação de superuser entra no `op.audit_log` com o marcador `plataforma`.

---

## 7. Na tela

`/plataforma`, atrás de `RequireSuperuser` — que, como o backend, **não** aceita
`administrar` e explica a diferença em vez de só negar: administrar a própria conta
continua em **Administração**. A entrada no menu aparece só para quem tem a flag.

A tela mostra o uso por organização e as licenças com o **status efetivo**: uma licença
`ativa` com prazo vencido aparece como expirada, porque é isso que ela é. Licença
global exibe "todos" em vez de um número — o alcance não é enumerável, e inventar uma
contagem ali seria número sem lastro. Organização sem licença recebe o aviso explícito
de que a carteira não basta.

**Testes:** 16 no backend (`tests/test_sprint19_control_plane.py`) e 6 no frontend
(`src/test/sprint19.test.tsx`).

**Uma lacuna declarada:** a varredura axe do e2e roda autenticada como administrador de
tenant, que por definição **não** enxerga `/plataforma`. A página ficou fora das 19
rotas auditadas automaticamente. Ela é construída sobre as primitivas já auditadas
(`PageHeader`, `Card`, `Async`, `StatusBadge`, tabela com `scope="col"`, rótulos via
`useId`), mas isso é argumento, não medição — auditá-la exige uma segunda sessão
autenticada na suíte.
