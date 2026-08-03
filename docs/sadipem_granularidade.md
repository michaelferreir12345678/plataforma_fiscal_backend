# SADIPEM: por que a Central mostrava "0 registros" e o que mais estava errado

Investigação a partir de uma pergunta simples — *"as fontes do SADIPEM estão com zero
registros, mas a página de dívida mostra dívida; a página está correta?"*. A resposta tinha
três camadas, e a terceira era a que ninguém tinha olhado.

## 1. O "0 registros" não era falta de dado

O dado estava ingerido: 606 PVL, 80 operações contratadas, 182 linhas de cronograma,
117.054 de CDP. O que faltava era **cobertura**: nenhuma das quatro fontes `sadipem_*`
constava nos mapas que materializam `gold.mart_cobertura_fonte`, e a Central de Dados lê a
cobertura. Treze outras fontes constavam.

O modo de falha é o pior tipo: silencioso e enganoso na direção errada. A ingestão
funcionava, a tela dizia zero, e a leitura natural — a que o usuário fez — é "a ingestão
falhou".

## 2. A página de dívida estava certa, mas por sorte

Os números de dívida (DCL, limite, faixa) vêm do **RGF Anexo 02**, não do SADIPEM. São
independentes da falha de cobertura.

Mas `build_cronograma` **somava entre operações** no mesmo ano. O
`/opc-cronograma-pagamentos` não devolve a parcela de uma operação: devolve o cronograma
**consolidado do ente** como estava na análise daquele pleito. Duas análises produzem duas
fotografias do mesmo estoque — somá-las dobra a dívida.

Fortaleza tinha duas entregas para 2026:

| entrega | operações | linhas | vigente |
|---|---|---|---|
| `20260721` | **8** | 105 | não |
| `20260721-r2` | 1 | 11 | **sim** |

A tela resolvia a vigente. O número estava certo porque a bitemporalidade o salvou, não
porque o código estivesse correto. E as fotografias eram reconhecivelmente do mesmo estoque:
duas operações traziam R$ 99.926.330,89 e R$ 99.661.808,16 para o mesmo ano.

Agora há guarda explícita (`_fotografia_unica`): fica a fotografia com mais anos cobertos e,
no empate, a análise mais recente. Preferir a de maior valor seria escolher justamente a que
mais infla; preferir só a mais recente descartaria a fotografia completa quando a última
análise cobre menos anos.

## 3. Três colunas que nunca poderiam se preencher

| coluna | preenchidas | campo procurado | o que a API publica |
|---|---|---|---|
| `cronograma.juros` | 0 de 182 | `juros`/`vl_juros`/`total_juros` | `total_encargos` |
| `cronograma.mes` | 0 de 182 | `mes` | nada — o cronograma é **anual** |
| `pvl.decisao` | 0 de 606 | `decisao`/`resultado` | `status` |

A pior era `juros`: chegava à tela como **"R$ 0,00"**, que se lê como *não há juros* — quando
o fato é que o SADIPEM não separa juros de encargos. Coluna que nunca se preenche não é dado
ausente; é promessa de granularidade que a fonte não tem.

## 4. Metade do que a fonte publica ia para o lixo

| fonte | API | guardávamos | recuperado |
|---|---|---|---|
| PVL | 18 campos | 7 | `num_pvl`, `num_processo`, `finalidade`, `credor`, `tipo_credor`, `moeda`, `data_protocolo` |
| operação contratada | 18 | 7 | idem + `status` |
| cronograma | 12 | 5 | separação **dívida consolidada × operações contratadas**, `num_pvl`, indicador de moeda estrangeira |
| CDP | 7 | 3 | `num_pvl`, `num_processo`, `id_pleito` |

`num_pvl`/`num_processo` são a âncora documental: sem eles, uma operação na tela não é
rastreável até o processo no Tesouro. E a separação DC×OC é o corte analítico de operações
de crédito — responde *"quanto do serviço da dívida vem do que eu acabei de contratar"*, que
o total sozinho não responde.

Depois da reingestão real, Fortaleza: serviço total **R$ 6,60 bi**, sendo **R$ 6,37 bi de
estoque** e **R$ 230 mi de contratado novo**. `DC + OC = total` **à centavo** — a própria
fonte reconcilia, o que valida o mapeamento.

## 5. O CDP estava atribuído ao ente errado

`res-cdp` **ignora `id_ente`**. Verificado: Fortaleza, São Paulo e sem filtro devolvem
exatamente os mesmos registros — a base do país. As 117 mil linhas nacionais estavam
gravadas sob o código do ente consultado.

Nenhuma tela lia `SadipemCdp`, então era mina e não mentira. Passa a ser tabela nacional
(`cod_ibge = 'BR'`, como FPM e CAPAG), com `num_pvl`/`id_pleito` fazendo a ponte para o ente
via `sadipem_pvl`. `_replace` passou a apagar pela mesma chave sob a qual grava — senão cada
execução empilharia outra cópia da base inteira.

## Migration

`0036_sadipem_granularidade` apaga a carga existente antes de alterar. As linhas antigas não
têm como ser completadas sem reconsultar a fonte, e o CDP antigo está atribuído ao ente
errado. Reingerir é barato (o SADIPEM é fotografia, não histórico a reconstruir) e é a única
forma de o dado ficar correto — manter o que estava seria preservar a atribuição errada.
