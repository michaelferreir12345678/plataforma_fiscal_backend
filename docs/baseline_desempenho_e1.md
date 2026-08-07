# Baseline de desempenho — Sprint E1

> **O que este documento é.** O registro dos caminhos quentes que a E1 tratou, com
> **consulta, volume, ambiente e limiar** declarados para cada um, e o valor antes/depois.
> O limiar não vive só aqui: ele está codificado em `tests/test_sprint_e1_desempenho.py`,
> de modo que uma regressão quebra a suíte em vez de envelhecer neste arquivo.
>
> **O que este documento não é.** Não é um relatório de latência. A unidade escolhida é
> **número de consultas por requisição**, não milissegundos — ver "Por que contar consultas
> e não cronometrar" abaixo.

---

## 1. Caminho crítico A27 — gate de escopo (`shared/scope.py`)

### A consulta

O gate roda em **toda** rota ligada a um ente (§6.4). Para uma conta do tipo `estado`, ele
precisa saber quais prefixos de UF a organização monitora:

```python
# app/shared/scope.py — antes da E1
for c in repository.list_carteira(session, org_id):      # 1 consulta
    if len(c.cod_ibge) == 2:
        prefixes.add(c.cod_ibge)
    else:
        ente = catalog_repo.get_dim_ente(session, c.cod_ibge)   # 1 consulta POR ENTE
        ...
```

`get_dim_ente` é `session.get(DimEnte, cod_ibge)` — uma ida ao banco por ente da carteira,
sem cache de sessão. `_is_estado` somava mais uma consulta (`get_org`) a cada chamada.

Depois da E1, a mesma resolução é:

```sql
SELECT ... FROM op.carteira_ente WHERE org_id = :org
SELECT ... FROM gold.dim_ente WHERE cod_ibge IN (:c1, :c2, ..., :cN)
```

e o resultado fica memorizado em `session.info` — uma sessão é uma requisição —, no mesmo
padrão que `cobertura_licenca` já usava dois blocos acima.

O contador expôs, de quebra, uma consulta duplicada que a leitura de código não tinha
mostrado: `_estado_prefixes` e `carteira_scope_ibges` liam **a mesma carteira** em duas
consultas separadas dentro da mesma requisição. A leitura passou por `_carteira_ibges`,
memorizada junto — é a diferença entre "os dois caminhos usam o mesmo conjunto" por
construção e por coincidência.

> **Onde o N+1 realmente acontecia**, e por isso o teste mede este caminho e não outro: o
> gate só resolve prefixos de UF quando o ente **não** está listado na carteira (a
> primeira condição do `or` responde antes, nos demais casos). É o caminho da ampliação
> estadual — o município da UF monitorada que a conta não cadastrou uma a um — e o de
> `carteira_scope_ibges`, que resolve os prefixos sempre que a conta é estadual.

### O volume

| Grandeza | Valor |
|---|---|
| Entes na carteira do cenário medido | **184 municípios + 1 código de UF** |
| Por que 184 | É o tamanho de um estado real de porte médio (o Ceará tem 184 municípios) e é o cenário citado na ficha da E1 |
| Universo máximo possível | 5.598 entes (licença global) |
| Chamadas ao gate por requisição | ≥ 1; rotas que resolvem ente **e** carteira chamam mais de uma vez |

### O ambiente

| Item | Valor |
|---|---|
| Banco | PostgreSQL 16, instância local de desenvolvimento (a mesma que a suíte usa) |
| Aplicação | Python 3.12, SQLAlchemy 2.0, sessão por requisição (`core/deps.get_db`) |
| Medição | evento `before_cursor_execute` do SQLAlchemy, contando instruções emitidas na conexão da sessão |
| Dado | 185 linhas em `op.carteira_ente` e 185 em `gold.dim_ente`, semeadas pelo próprio teste com prefixo `97` (fora da faixa de código IBGE real, 11–53) |
| Reprodução | `pytest tests/test_sprint_e1_desempenho.py -q` |

### O limiar

**≤ 5 consultas** no gate de escopo para a primeira verificação de uma requisição
(`ORCAMENTO_CONSULTAS_GATE`, declarado no próprio teste).

A folga entre o número esperado (organização + carteira + `dim_ente` em lote + licença) e
o limiar de 5 existe para não transformar uma consulta a mais de infraestrutura num teste
frágil. O que o limiar **garante** é o que importa: ele é uma constante, não uma função do
tamanho da carteira. Há um teste específico para isso
(`test_orcamento_declarado_e_independente_do_tamanho_da_carteira`), para que ninguém
"ajuste" o número para caber uma regressão.

### Antes × depois

| | Consultas no gate, carteira de 184 municípios |
|---|---|
| **Antes** (código removido nesta sprint) | `1` (carteira do ente) `+ 1` (`get_org`) `+ 1` (`list_carteira`) `+ 184` (`dim_ente`, uma por ente) `+ 1` (licença) = **até 188**, e **repetidas** a cada chamada do gate na mesma requisição |
| **Depois** | **5** na primeira chamada (carteira do ente, `get_org`, `list_carteira`, `dim_ente` em lote, licença); **1** em cada chamada seguinte da mesma sessão |
| **Escopo agregado** (`carteira_scope_ibges`) | **5**, contra `1 + 1 + 1 + 184 + 1 + 1` antes |

O "antes" é **analítico**, derivado do código que esta sprint substituiu: o laço fazia
exatamente uma consulta por ente da carteira, por construção, e não havia memorização. O
"depois" é **medido a cada execução da suíte** pelo contador de eventos — é o número que a
catraca prende.

### Limitação declarada desta edição

O ambiente em que esta sprint foi escrita **não tinha shell disponível** (`bwrap: No
permissions to create new namespace`), então `make lint`, `make mypy` e `make test` não
foram executados aqui. Os limiares e o instrumento de medição estão no código e rodam na
próxima execução da suíte; enquanto isso não acontecer, o número "depois" deve ser lido
como **esperado e verificável**, não como observado. Essa distinção é a mesma disciplina
que a A0R adotou ao separar "confirmado no código" de "confirmado contra o dado".

---

## 2. Caminho crítico — `POST /carteira/refresh`

### A consulta

Não é uma consulta: é um **laço** que estava dentro do handler HTTP.

```python
# app/modules/dashboard/carteira_service.py — antes da E1
for cod in sorted(scope.carteira_scope_ibges(session, principal)):
    total += refresh_mart_carteira(session, cod, periodo)   # resolve versão + N upserts
```

Cada iteração resolve a versão vigente do RREO do ente e faz um `upsert` por indicador
materializado. O trabalho por ente é pequeno; o problema é o número de iterações.

### O volume

| Escopo do cliente | Iterações dentro do request |
|---|---|
| Município isolado | 1 |
| Conta estadual (CE) | 184 |
| Licença global | **5.598** |

### O limiar

Não há limiar de tempo, porque não há mais trabalho proporcional ao escopo dentro do
request: `POST /carteira/refresh` passou a **enfileirar um job durável**
(`op.carteira_lote_job`, `acao='refresh'`) e responder **202**. O que o request faz agora é
resolver o conjunto de entes (uma consulta de conjunto, a mesma que a leitura da carteira
já fazia) e gravar uma linha.

O teste que prende isto é comportamental, não cronométrico
(`tests/test_carteira.py::test_refresh_enfileira_job_e_o_job_materializa_o_escopo`):

1. a resposta é **202** com o job, e
2. **nada** foi materializado quando ela volta, e
3. o job, executado pelo worker, materializa **o mesmo total** que o laço síncrono
   materializava.

O item 3 é o que impede a "melhora de desempenho" de ser só perda de funcionalidade.

### Antes × depois

| | `POST /carteira/refresh` |
|---|---|
| **Antes** | 200, com `N` iterações de materialização no handler (N = tamanho do escopo) |
| **Depois** | 202, com 1 inserção; escopo vazio responde 422 em vez de aceitar um lote sem trabalho |

---

## 3. Índice novo, e por que ele **não** é especulativo

A regra da sprint é "corrigir N+1/índices apenas com evidência". O único índice criado
(`ix_data_quality_check_chave`, migration `0041`) não vem de uma suspeita de lentidão: ele
vem de uma **consulta nova** que esta mesma sprint introduziu.

Com a `versao_entrega` na chave de `gold.data_quality_check` (A26), a leitura do painel e
do selo passou a filtrar "o veredito mais recente por chave" com uma subconsulta
correlacionada em `(check_codigo, fonte, cod_ibge, periodo)`. Sem índice, essa subconsulta
varreria a tabela uma vez por linha candidata — um N² introduzido pela própria correção. O
índice cobre exatamente as colunas do predicado, mais `executado_em`, que é o critério de
ordenação.

Nenhum outro índice foi adicionado: não há medição que justifique.

---

## 4. Por que contar consultas e não cronometrar

A suíte roda contra o banco de desenvolvimento real, compartilhado (decisão registrada em
§10 do `evolucao_plataforma.md`, 2026-08-04). Um limiar em milissegundos ali mediria a
carga da máquina, não o código: passaria numa máquina ociosa e falharia numa ocupada, e a
reação natural a um teste que falha por motivo alheio é afrouxá-lo até parar de incomodar
— que é como um guarda de desempenho morre.

Contagem de consultas é determinística, independe de hardware e mede exatamente a classe
de defeito que a A27 descreve: trabalho que **cresce com o tamanho do cliente**. O
orçamento de latência por rota continua existindo, e continua onde já estava: o
`x-performance-p95-ms` do middleware da Sprint 27, com orçamento de 500 ms declarado em
`http_performance_budget_ms`.
