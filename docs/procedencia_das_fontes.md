# Procedência das fontes — rastreabilidade da origem

## O que faltava

O catálogo de fontes dizia **família, cadência e órgão**. Nada disso permite conferir. Um
gestor que desconfia de um número via "fonte: Tesouro Nacional" e não tinha o que fazer com
isso a não ser acreditar. A plataforma já dava rastreabilidade *para dentro* — todo valor
carrega `source_ref` com relatório, anexo, período e versão da entrega (§6.3) —, mas não
*para fora*: de qual **endereço** aquela entrega saiu, com quais parâmetros, em que formato.

## O que existe agora

Cada uma das 20 fontes declara, em `connectors/procedencia.py`:

- **tipo de acesso** — API REST, API OData, catálogo CKAN → arquivo, arquivo, PDF de portal;
- **portal público** onde a mesma informação se consulta sem API, e a **documentação**;
- **licença** e **autenticação**;
- **como funciona** — em prosa: por que são N chamadas, por que aquele parâmetro, o que a
  fonte não entrega;
- **cada chamada**, com método, URL, formato, o que traz, **cada parâmetro explicado** e um
  **exemplo real e clicável**.

Exposto em `GET /admin/ingestion/fontes/{fonte}/procedencia`, e na tela em
`/central-dados/fontes/{fonte}`.

## Decisões de apresentação

**Página, não modal.** Isto é material de auditoria. Quem confere uma origem manda o
endereço a um colega, anexa a um processo, imprime. Modal não tem URL própria e some no
primeiro clique fora. O conteúdo também é longo por natureza — uma tabela de parâmetros por
endpoint, URLs de mais de cem caracteres —, e espremer isso numa caixa flutuante obriga a
rolar dentro de um retângulo dentro de uma página que também rola.

**Não virou coluna da tabela.** O catálogo já tem quinze colunas e rola na horizontal.
Endpoint não é um valor: é uma lista de 1 a 3 chamadas, cada uma com método, formato,
parâmetros e observações. Forçar em célula significaria mostrar só a primeira — escondendo
justamente as fontes de origem composta (CAPAG, cronograma do SADIPEM, PIB do IBGE), que são
as que mais precisam de explicação.

**A tabela ganhou um selo de origem** (`API`, `OData`, `catálogo → arquivo`, `PDF do
portal`), porque a forma de obtenção muda o que pode falhar e é útil ao varrer a lista. E o
nome da fonte virou o link para a página.

**O exemplo é a prova.** Cada endpoint traz uma URL com valores reais que abre no navegador
e devolve o mesmo dado que ingerimos. Sem isso a página seria uma declaração sobre a qual
ainda restaria confiar; com isso, o usuário confere sozinho.

## Como isto não vira mentira

Reescrever endereços num segundo lugar cria o risco de a cópia envelhecer enquanto o
conector muda — e uma página de auditoria desatualizada é pior do que nenhuma, porque dá a
sensação de ter conferido. Duas defesas:

`tests/test_procedencia.py` (suíte padrão) reconcilia declaração e conector:

- toda fonte registrada tem procedência — **fonte nova sem origem quebra a suíte**;
- o `path` estático do conector aparece na URL declarada;
- o host declarado é o de alguma base do cliente HTTP;
- todo parâmetro tem significado e todo endpoint diz o que traz.

`tests/test_procedencia_rede.py` (opt-in, bate nas fontes reais):

```
PROCEDENCIA_REDE=1 pytest tests/test_procedencia_rede.py -q
```

Verifica que **cada exemplo devolve dado** — 200 e sem lista vazia. Fica fora da suíte
padrão porque depende de servidores de terceiros, e falha ali não é regressão nossa.

## Dois defeitos que a verificação achou

Ambos teriam ido para a tela com aparência de rigor:

1. **`id_tv=period` não existe.** Os valores reais da MSC são `beginning_balance`,
   `period_change` e `ending_balance`. O exemplo respondia 200 com lista vazia.
2. **`p_estado=23` é o Rio Grande do Sul, não o Ceará.** O código de estado da API de
   transferências não é o do IBGE: Ceará é 23 no IBGE e **6** no Tesouro. Quatro exemplos
   estavam apontando para a UF errada — devolvendo dado plausível, do estado errado, sem
   nenhum erro aparente. A explicação do parâmetro agora diz isso com todas as letras.
