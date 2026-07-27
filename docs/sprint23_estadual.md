# Sprint 23 — Visão Estadual & Consolidação Territorial da UF

Separa e entrega os **dois conceitos estaduais** que a auditoria (§§2.2, 6) apontou como
confundidos: o **ente estadual** (o Governo do Estado) e o **consolidado territorial** (o
agregado dos municípios da UF). Genérico para qualquer UF; o Ceará é o caso 1 (dados reais).

---

## 1. A regra invariante: consolidado = Σnumerador / Σdenominador (nunca média de %)

O `gold.mart_consolidado_uf` guarda, por `(uf, período, indicador)`, o **numerador** e o
**denominador** somados dos municípios que têm o indicador. O percentual é
`Σnum/Σden` — a média ponderada pela RCL —, **nunca** a média aritmética dos percentuais
municipais.

Prova com o Ceará real, 2024-B6, pessoal do Executivo:

| medida | valor |
|---|---|
| Σ despesa de pessoal (168 municípios) | R$ 19,40 bi |
| Σ RCL (dos mesmos 168) | R$ 42,81 bi |
| **consolidado = Σ/Σ** | **45,32 %** |
| média simples dos % municipais | 45,61 % |
| diferença | 0,29 p.p. |

A diferença não é ruído: municípios pequenos com % alto pesam menos no consolidado do que
na média simples. O teste `test_consolidado_nao_e_media_de_percentuais` fixa isso com uma UF
sintética (M1: RCL 1.000/pessoal 500 = 50 %; M2: RCL 3.000/pessoal 600 = 20 %) onde
consolidado = 1.100/4.000 = **27,5 %** e a média seria **35 %**.

**Indicadores v1** (aditivos seguros): `rcl` e `disponibilidade` (absolutos, Σ em R$),
`pessoal_executivo` e `divida_consolidada_liquida` (razão sobre a RCL). O teto/faixa dos
indicadores-razão vem do `dim_limite_legal` **municipal** (o consolidado é de municípios).

---

## 2. Ente estadual × consolidado: nunca se misturam

O consolidado é **só de municípios** (código IBGE de 7 dígitos). O ente estadual (2 dígitos,
ex.: `23` = Ceará) fica **estruturalmente de fora** — `list_municipios_uf` filtra por
`length(cod_ibge) = 7`. A resposta traz o ente estadual apenas **referenciado**
(`ente_estadual`), com endpoints distintos (`/entes/23/...`), e nunca somado.

**Dupla contagem de transferências intra-governamentais.** A cota-parte estado→município
(ICMS/IPVA, derivada do RREO A1 na Sprint 21) entra na RCL dos municípios. Como o consolidado
**exclui o ente estadual**, essa transferência nunca é contada duas vezes. A regra fica
documentada em `versao_calculo = 'v1'` e na `observacao` da resposta. Um eventual v2 que
produza um total estado+municípios teria de **líquidar** a transferência marcada — hoje
desnecessário porque os dois conceitos são servidos separados. O teste
`test_estado_nunca_entra_no_consolidado` prova que a RCL consolidada de M1+M2 é 4.000, não
94.000 (o estado tem RCL 90.000).

---

## 3. Cobertura honesta (n/184, ausentes, períodos mistos)

Cada indicador do consolidado carrega:

- `n_entes_total` — municípios da UF (184 no CE);
- `n_entes_com_dado` — quantos têm o indicador no período exato;
- `entes_ausentes` — a lista nominal dos que faltam (não some, não vira zero);
- `cobertura_pct`;
- `periodos_mistos` — **true** quando, no ano do período consolidado, os municípios da UF
  têm dado em mais de um período distinto (ex.: uns em B6, outros só em B4; ou RGF
  quadrimestral vs semestral). É o aviso de que a cobertura é limitada pela cadência.

CE 2024-B6 real: RCL 170/184, pessoal 168/184, DCL 163/184, disponibilidade 165/184 — todos
com `periodos_mistos` marcado exceto disponibilidade (só há Q3). Nunca há "100 % fabricado".

---

## 4. Disponibilidade: mapeamento de cadência RREO→RGF

O consolidado é chaveado pelo período **RREO** (bimestral). A disponibilidade líquida vem do
**RGF** (quadrimestral): mapeia-se `B{n}` → `Q{teto(n/2)}` (B5/B6 → Q3). No CE real, o
`fato_disponibilidade` só tem **Q3**, então a disponibilidade consolidada só aparece nos
períodos que mapeiam para Q3 — coberto honestamente pela `cobertura_pct`. A suficiência
**por fonte** (módulo 9) continua **não** consolidável; aqui soma-se apenas a
`disp_liquida_apos` (aditiva em R$) como total de liquidez territorial.

---

## 5. Regiões: divisão oficial do IBGE (decisão de fonte)

O prompt pedia "14 Regiões de Planejamento do IPECE". Não há feed do IPECE conectado ao
projeto, e semear 184 atribuições municipais de memória seria fabricar dado — o que a regra
cardinal do projeto proíbe. `gold.dim_regiao_uf` é populada da **API oficial de localidades
do IBGE** (nível *região geográfica imediata*): real, determinística, completa e **genérica
para qualquer UF**. Para o CE isso dá **18 regiões** (não 14).

Quando o feed do IPECE entrar, basta repovoar `dim_regiao_uf` — schema e endpoints não mudam
(mesmo padrão de substituição honesta usado para a cota-parte do ICMS na Sprint 21). O drill
UF→região→município funciona igual com qualquer divisão.

---

## 6. Malha: GeoJSON real do IBGE (não TopoJSON)

`gold.geo_malha_uf` guarda a malha municipal **GeoJSON** do IBGE (qualidade *mínima*, ~103 KB
para o CE, 184 polígonos com `properties.codarea` = código IBGE). Optou-se por GeoJSON em vez
de TopoJSON porque renderiza **direto no navegador** (o coroplético é uma projeção SVG simples,
sem dependência de `topojson-client`) — menos superfície, mesmo resultado. Servida por
`GET /geo/malha/{uf}`. O teste real confirma `n_areas = 184` e 184 features.

---

## 7. Escopo: agregado público × valor nominal por ente

A linha de corte (§6.4):

- **Agregados** (consolidado, distribuição) são **territoriais e públicos** para quem tem a UF
  no escopo — a distribuição é anônima (só estatística). Acesso via `assert_uf_in_scope`
  (403 se o usuário não tem nenhum ente da UF no escopo).
- **Valores por ente nomeados** (ranking, mapa) respeitam a carteira: a conta **estadual** vê
  todos os municípios da UF; a **consultoria** vê só os da sua carteira (os demais aparecem no
  mapa em cinza, `no_escopo`); a **prefeitura** de outra UF recebe **403**.

Testes: `test_escopo_estadual_ve_todos_os_municipios`, `test_escopo_consultoria_ve_so_carteira_no_ranking`,
`test_escopo_outra_uf_403`.

---

## 8. Endpoints (drill §6.1)

```
GET /uf/{uf}/consolidado?periodo=            # Σnum/Σden + cobertura + ente estadual referenciado
GET /uf/{uf}/ranking?indicador=&periodo=&regiao=&porte=&ordenar=
GET /uf/{uf}/distribuicao?indicador=&periodo=   # histograma + percentis + concentração (top-N)
GET /uf/{uf}/mapa?indicador=&periodo=           # valor por município + malha_ref
GET /uf/{uf}/arvore?indicador=&periodo=&agrupar=regiao|porte&node=   # drill; folha linka /entes/{ibge}/cockpit
GET /geo/malha/{uf}                             # GeoJSON real
POST /uf/{uf}/consolidado/refresh?periodo=      # materializa (cap administrar)
```

O `uf` aceita sigla (`CE`) ou código (`23`); normaliza para o prefixo IBGE de 2 dígitos.
`build_consolidado` lê o mart materializado e, na primeira leitura de um período, materializa
**lazy**. Semeadura em massa: `python -m scripts.seed_estadual --uf 23`.

---

## 9. Frontend

`CarteiraPage` reescrita em **4 abas** que nunca confundem os conceitos: **Consolidado UF**
(cartões Σnum/Σden com cobertura n/184 + selo "períodos mistos"; **mapa coroplético real** da
malha do IBGE; distribuição/concentração; ranking clicável) · **Ente estadual** (abre o
cockpit do Governo do Estado, rotulado como distinto) · **Minha carteira** (grade + ações em
lote reais `POST /carteira/lote/{acao}`) · **Grupos** (carteira agrupada).

Clicar num município (ranking ou mapa) **troca o ente do contexto** e abre o cockpit — o drill
território→ente. O **seletor de visão** do shell (`SeletorVisao`) ganhou município · ente
estadual · consolidado UF · carteira · grupos · comparação, navegando para a tela/aba certa
(`/carteira?aba=…`, `/benchmarking`). O `carteiraData.ts` já não existia (removido antes); o
**Onboarding foi removido definitivamente** (era um wizard com dados fabricados; o contexto
agora vive no shell + nestas abas). Testes RTL em `src/test/carteira.test.tsx` (5).

---

## 10. Migração e validação

Migration `0027_sprint23_estadual` (reversível, down→up validado): `mart_consolidado_uf`,
`dim_regiao_uf`, `geo_malha_uf`. `make lint && make test` verdes (ruff + mypy 191 arquivos;
239 testes, 9 novos). Frontend: `npm test` (12), `tsc --noEmit` e `vite build` verdes.
