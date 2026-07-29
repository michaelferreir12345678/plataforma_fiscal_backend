# Sprint 25D — Patrimônio/MSC & Benchmarking

> Auditoria §2.10 e §2.12. Patrimônio tinha um **seletor de entes-demo** que trocava o
> ente global silenciosamente (risco de leitura errada, prioridade alta); Benchmarking
> comparava **dois** indicadores — ambos de conformidade — num único período.

**Aceite:** nenhum ente é trocado silenciosamente; benchmark cobre ≥6 indicadores em ≥4
períodos. **Resultado:** 7 indicadores; 5 deles com 171–176 entes na coorte e até 18
períodos materializados.

---

## 1. Patrimônio: o ente é o do contexto

O seletor-demo saiu. A página passou a usar `ente` do `AppContext` como todas as outras,
e o backend deixou de responder **404** para ente sem fonte patrimonial: `/patrimonio`
devolve 200 com `cobertura`, que diz em português o que existe e o que falta.

```json
"cobertura": {
  "tem_dca": true, "tem_msc": false,
  "anos_dca": [2021, 2022, 2023, 2024], "meses_msc": [],
  "fontes_ausentes": ["MSC"],
  "mensagem": "DCA disponível em 2021…2024. Este ente **não** publica a MSC mensal…"
}
```

Esse 404 era a causa raiz do seletor: sem um jeito de dizer "não tem dado", a versão
anterior preferiu trocar o ente por um que tivesse. Com a cobertura declarada, a tela
mostra o aviso (e o link para a Central de Dados **para quem tem `administrar`**), e o
`EmptyState` compartilhado cobre o caso de nenhuma fonte.

Descoberta ao ligar no dado real: a auditoria dizia "DCA só SP+Fortaleza 2021–2023", mas
o backfill da Sprint 21 ampliou para **193 municípios × 2021–2024**. O seletor-demo já
não era só arriscado — era desnecessário. A MSC, sim, continua só São Paulo.

### Comparação anual dos balanços (novo)
`GET /entes/{ibge}/balancos/comparacao?tipo=&anos=4` — a mesma conta em vários
exercícios, com variação absoluta e percentual. Um balanço isolado diz a posição; a série
diz a direção, que é o que se justifica ao tribunal de contas. Exercício em que a conta
não aparece fica **nulo**, nunca zero — num balanço são coisas diferentes. A variação só
é calculada quando existem os dois extremos, e o percentual usa o **módulo** do primeiro
valor (contas de PL podem ser negativas).

Fortaleza, Ativo: 5,46 bi (2021) → 6,22 → 7,32 → 7,07 bi (2024), **+29,4%**, 214 contas.

---

## 2. Benchmarking: de 2 para 7 indicadores

Três indicadores **gerenciais** entraram no mart (`indicators/gerenciais.py`), calculados
a partir de fatos que já existiam — nada é recalculado aqui:

| Indicador | Cálculo | Unidade | Coorte real (NE) |
|---|---|---|---|
| `rcl_per_capita` | `fato_rcl.rcl_12m ÷ população` | `brl_per_capita` | 176 entes, 18 períodos |
| `investimento_rcl` | `fato_despesa(Investimentos).empenhado ÷ RCL` | `percentual_rcl` | 176 entes, 18 períodos |
| `resultado_primario_rcl` | `fato_resultado.resultado_primario ÷ RCL` | `percentual_rcl` | 175 entes, 12 períodos |

Somados a `pessoal_executivo`, `divida_consolidada_liquida` e aos mínimos da 25C
(`saude_minimo`, `educacao_mde`, `fundeb_profissionais`), são **7 indicadores**.

**Eles não têm limite legal** — e é por isso que precisam de um caminho próprio,
`registrar_indicador_gerencial`: entram no mart com `faixa` e `teto_pct` **nulos**. Assim
o semáforo e o motor de alertas, que só falam quando há faixa, continuam mudos: um
"normal" inventado afirmaria conformidade onde a lei nada exige. `IndicadorOut.faixa` e
`teto_pct` passaram a ser opcionais por isso.

Insumo ausente ⇒ o indicador **não é gravado**. Uma linha zerada seria lida como "não
investe" em vez de "não publicou".

### Per capita
`BenchmarkValue` ganhou `valor_per_capita`, `populacao` e `pop_ano_ref` — preenchidos
**só** quando a métrica está em R$ (`unidade == "brl"`). Percentual não se divide por
população: uma razão per capita não é informação fiscal. `rcl_per_capita` já nasce
dividido e por isso declara `denominador='populacao'` e unidade `brl_per_capita`, que o
eixo do gráfico mostra como "R$/hab".

### Multi-período
`GET /benchmark/evolucao?ente=&indicador=&coorte=&periodos=6` — a posição do ente na
**mesma** coorte, período a período. A coorte é fixada no primeiro ponto e repetida: se
mudasse a cada período, a variação de posição misturaria movimento do ente com troca de
régua. Período sem valor comparável não vira ponto e é devolvido em
`periodos_sem_comparacao` — interpolar posição em ranking não significa nada.

Fortaleza, RCL por habitante em 2024: **60º → 29º** entre 176 municípios do Nordeste
(R$ 4.087 → R$ 4.443 por habitante).

---

## 3. Efeitos colaterais tratados

- **`gold.mart_benchmark.unidade`** já havia virado `text` na 0031 (25C); os rótulos
  novos (`brl_per_capita`) cabem.
- **`test_health_edu.py`** passou a limpar `MartIndicador`: desde a 25C a apuração dos
  mínimos escreve no mart, e o banco de desenvolvimento vinha acumulando entes
  sintéticos (60 linhas órfãs foram removidas).
- **`formatBenchmarkValue`** no frontend deixou de rotular tudo como "% da RCL": cada
  unidade tem seu sufixo (`da RCL`, `dos impostos e transf.`, `do FUNDEB`, `R$/hab`).

---

## 4. Cobertura honesta na tela

A coorte "Região Nordeste" tem **1.791 entes elegíveis** e 176 com valor (9,8%) — o
backfill da Sprint 21 cobriu o Ceará, não a região inteira. A tela mostra os dois
números em cada ponto da evolução; comparar com base incompleta é comparar outra coisa,
e o gestor precisa saber disso antes de citar o percentil num ofício.

---

## 5. Testes

- `tests/test_sprint25d_patrimonio_benchmark.py` (11): gerenciais no mart sem faixa nem
  teto; indicador sem insumo não vira linha zerada; unidade `brl_per_capita`; sentido
  neutro para investimento; per capita só em métricas em R$; evolução com coorte fixa em
  4 períodos; período não reproduzível no `as_of` declarado em vez de interpolado;
  patrimônio de ente sem fonte responde cobertura (não erro) e devolve **o ente
  consultado**; comparação anual com variação e lacuna nula.
- `src/test/sprint25d.test.tsx` (11): sem seletor-demo e sem menção a São Paulo; aviso de
  cobertura; CTA só para quem administra; estado vazio com CTA; comparação com lacuna
  vazia; indicadores gerenciais na tela; evolução com mediana por período; recusa de
  trajetória com um só ponto; export; unidade rotulada.

`ruff` + `mypy` (199 arquivos) + `pytest` **324 testes** verdes; `tsc` + `vitest`
**71 testes** + `build` verdes.
