# Ausência com saída — o 404 que orienta

## O problema

A tela pedia o quadrimestre em curso e recebia:

```
⚠ Sem RGF vigente para 2304400 em 2026-Q2.        [ Tentar de novo ]
```

Duas coisas erradas de uma vez. A frase é verdadeira e inútil: não diz que o **prazo do RGF
ainda não venceu** (LRF, art. 63), então o gestor lê "a plataforma não tem o dado" quando o
fato é "ninguém publicou ainda". E o botão convida a insistir contra uma parede — repetir a
consulta não faz o ente entregar o relatório.

O mesmo vale para a CAPAG de um exercício em curso: o Tesouro apura uma vez por ano, sobre
contas já encerradas. Não existe nota parcial, e nenhuma quantidade de recarregar produz uma.

## A solução

Dois campos de **extensão** do Problem Details (RFC 7807, §3.2) em todo 404 de relatório
ausente, montados em `app/shared/ausencia.py`:

| Campo | Para quê |
|---|---|
| `explicacao` | a cadência de publicação do relatório — por que o dado não está lá |
| `periodo_sugerido` | o último período que **tem** o relatório, para navegar |
| `rotulo_sugerido` | o mesmo período em língua de gestor (`3º quadrimestre de 2025`) |

Resultado na tela (`ErroComSaida`, em `components/AsyncState.tsx`):

```
ⓘ Sem RGF vigente para 2304400 em 2026-Q2.     [ Ir para 3º quadrimestre de 2025 ]
  O RGF é quadrimestral (semestral para municípios com menos de 50 mil
  habitantes, LRF, art. 63) e é publicado após o fim do período.
```

Amarelo, não vermelho — `role="status"`, não `role="alert"`. Ausência legítima da fonte não
é falha da plataforma, e o leitor de tela não deve interromper por ela.

## Por que campo de extensão, e não texto na frase

O front precisa **agir** sobre o erro, não só exibi-lo. Extrair `2025-Q3` de dentro de uma
mensagem exigiria interpretar redação: o primeiro ajuste de vírgula quebraria o botão sem
quebrar teste nenhum. Com campo próprio, a redação é livre e o contrato é estável.

## Decisões que não são óbvias

**A ordenação é cronológica, não lexicográfica.** Um município que cruza os 50 mil
habitantes muda a cadência do RGF e passa a ter quadrimestre **e** semestre no mesmo
exercício. Como texto, `2024-S1` > `2024-Q3`; no calendário, é o contrário. `ORDER BY
periodo DESC` mandaria o gestor de volta para junho achando que ia para dezembro. Usamos
`shared.periodo.mais_recente`, que é também o que decide o `default` do seletor — então o
botão leva exatamente ao período que o seletor escolheria, nunca a um vizinho dele.

**Nunca sugerir o período que acabou de falhar.** Mandaria a tela navegar para si mesma e
falhar de novo, agora com aparência de defeito nosso. Vale para a CAPAG também: a sugestão
tem de ser um exercício **anterior** ao pedido — a versão inicial de `_capag_ausente`
oferecia "Ir para 2026" quando 2026 era justamente o que não abriu.

**Rótulo sem destino não é emitido.** Se o exercício sugerido não tem RGF, o botão
prometeria um lugar que não existe; a explicação vai sozinha.

**Sem alternativa, "tentar de novo" volta a valer.** A entrega pode ter saído entre esta
consulta e a próxima. O que muda nesse caso é só o tom — ausência da fonte, não falha nossa.

## Onde está ligado

`shared/ausencia.py` é a fonte única; os serviços chamam `ausencia_de_entrega(...)` em vez
de montar `AppError` à mão. Cobre as telas presas ao seletor global de período:

| Módulo | Relatório |
|---|---|
| `revenue`, `expense`, `result`, `health_edu`, `indicators`, `limits` | RREO |
| `personnel`, `debt`, `cash_rap` | RGF |
| `debt` (CAPAG) | publicação anual do Tesouro |

**Onde deliberadamente não está:** `accounting` (Patrimônio) tem seletor de exercício
próprio alimentado por `anos_disponiveis` e já abre no ano mais recente com dado — não há
para onde sugerir. E os casos de `EmptyState` são outra coisa: ali a entrega **existe** e
veio sem linhas. Mandar o gestor a outro período esconderia um problema de qualidade do
dado em vez de mostrá-lo.
