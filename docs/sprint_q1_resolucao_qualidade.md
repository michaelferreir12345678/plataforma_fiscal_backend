# Sprint Q1 — O fluxo de resolução das verificações de qualidade

> Origem: uso real (18/08/2026). *"9 verificações de qualidade em falha sobre estes
> números… tem que haver uma forma de resolução disso. Temos que implementar o fluxo de
> resolução de cada ponto."*

**A queixa é legítima e o diagnóstico dela é preciso.** A Sprint 26 entregou a metade que
*detecta*: `gold.data_quality_check` roda 9 verificações, guarda os dois lados da conta e a
plataforma passa a exibir o selo sobre o número. O que nunca existiu foi a outra metade — o
que o gestor **faz** com a falha. Hoje o selo diz "não conferido" e para aí, o que com o
tempo produz o pior resultado possível: um aviso permanente que todos aprendem a ignorar.

---

## 1. A pergunta que decide tudo: o número é nosso ou é deles?

Esta plataforma já organiza o mundo por essa distinção — a ferramenta `cobertura_do_ente`
existe para separar *"o ente não entregou"* de *"a plataforma não carregou"*. A resolução de
uma falha de qualidade é a mesma pergunta aplicada à verificação, e ela **determina a ação**:

- se os dois lados são **nossos**, a divergência é defeito nosso e há o que corrigir;
- se os dois lados são **do ente**, o dado publicado é que está inconsistente — não há o que
  "consertar" aqui, há o que **comunicar**;
- se um lado é nosso e o outro é publicado, primeiro é preciso descartar a hipótese nossa.

Oferecer "reprocessar" para uma falha que é da fonte é pior que não oferecer nada: gasta o
tempo do gestor, não muda o resultado e ensina a desconfiar do botão.

### Classificação dos 9 checks (derivada do que cada um compara)

| Check | Esquerda × Direita | Classe | Ação cabível |
|---|---|---|---|
| `minimo_saude_recalculado` | recalculado × materializado | **plataforma** | rematerializar |
| `minimo_educacao_recalculado` | recalculado × materializado | **plataforma** | rematerializar |
| `mart_vs_detalhe_pessoal` | mart × detalhe | **plataforma** | rematerializar |
| `receita_soma_filhos` | pai × Σ filhos (publicados) | **fonte** | achado para o ente |
| `despesa_estagios_monotonicos` | empenhado × liquidado/pago | **fonte** | achado para o ente |
| `dcl_a6_vs_rgf` | RREO A6 × RGF A2 | **fonte** | achado para o ente |
| `msc_vs_dca` | MSC × DCA | **fonte** | achado para o ente |
| `rcl_calculada_vs_publicada` | nossa RCL × A3 publicado | **misto** | rematerializar e reavaliar |
| `freshness_{rreo,rgf,dca,msc}` | dias desde a entrega × SLA | **cobertura** | **depende de diagnóstico** |

**A classe `cobertura` é a única que não se resolve por classificação estática.** "O RGF está
com 80 dias contra um SLA de 45" pode significar duas coisas opostas — o ente não publicou,
ou publicou e nós não ingerimos — e só a fonte responde. Por isso ela ganha um passo de
diagnóstico próprio, e é o único que consulta a origem.

---

## 2. Como está hoje, medido em produção (18/08/2026)

| Check | Status | Ocorrências | Exemplo |
|---|---|---|---|
| `freshness_msc` | aviso | 1.528 | — |
| `minimo_educacao_recalculado` | aviso | 11 | ente 21, 2024-B6 |
| `minimo_saude_recalculado` | aviso | 11 | ente 21, 2024-B6 |
| `mart_vs_detalhe_pessoal` | **falha** | 8 | 45,57% × 46,54% (tol. 0,01) |
| `freshness_rreo` | **falha** | 6 | Δ141 dias, SLA 30 |
| `freshness_rgf` | **falha** | 6 | Δ80 dias, SLA 45 |
| `freshness_dca` | **falha** | 3 | Δ110 dias, SLA 60 |
| `dcl_a6_vs_rgf` | **falha** | 2 | R$ 309.020.505,01 × R$ 309.026.595,01 (Δ 6.090) |
| `receita_soma_filhos` | **falha** | 1 | ente 23, 2025-B6 |

Duas leituras que já saem daqui:

1. **`mart_vs_detalhe_pessoal` é nosso, sem ambiguidade.** Dois números que a própria
   plataforma produz discordam em ~1 ponto percentual — e é um limite de pessoal, onde
   0,9 p.p. muda a faixa. Não é ruído de tolerância: a tolerância é 0,01.
2. **`dcl_a6_vs_rgf` diverge em R$ 6.090,00 sobre R$ 309 milhões.** Dois demonstrativos que
   o mesmo ente publicou não fecham entre si. Nada que rematerializemos muda isso.

---

## 3. O fluxo, passo a passo

Cada ocorrência em falha percorre quatro estados, e nenhum deles é "sumir da tela":

```
        ┌──────────┐   diagnóstico    ┌────────────┐   ação aplicada   ┌───────────┐
        │  aberta  │ ───────────────▶ │ diagnosti- │ ────────────────▶ │ reavaliada│
        │          │                  │   cada     │                   │           │
        └──────────┘                  └────────────┘                   └───────────┘
                                            │                                │
                                            │ classe = fonte                 │ voltou ok
                                            ▼                                ▼
                                     ┌──────────────┐                 ┌────────────┐
                                     │ aceita como  │                 │ resolvida  │
                                     │ fato da fonte│                 │            │
                                     └──────────────┘                 └────────────┘
```

**1. Diagnóstico** — responde "de quem é o número" com evidência, não com opinião. Para as
classes `plataforma`, `fonte` e `misto` a resposta é estrutural (sai da tabela do §1). Para
`cobertura`, compara a nossa entrega mais recente com o que a fonte publica e o que o
calendário de obrigações já exigia.

**2. Ação cabível** — só é oferecida a que existe para aquela classe:

| Classe | Ação oferecida | O que ela faz |
|---|---|---|
| `plataforma` | *Rematerializar e reavaliar* | recalcula o indicador e roda o check de novo |
| `cobertura` (fonte tem entrega mais nova) | *Reingerir a entrega* | enfileira a ingestão daquele ente/período |
| `cobertura` (fonte não publicou) | *Aceitar como fato* | nada a reprocessar: o ente é que não entregou |
| `fonte` | *Aceitar como fato* + registrar achado | com justificativa obrigatória |
| `misto` | *Rematerializar e reavaliar* → se persistir, vira `fonte` | descarta a hipótese nossa primeiro |

**3. Reavaliação** — a ação **sempre** reexecuta o check e grava o novo veredito. Uma ação
que não é reavaliada é uma ação que ninguém sabe se funcionou.

**4. Desfecho** — `resolvida` (o check voltou a `ok`) ou `aceita_como_fato` (a falha é real e
da fonte). **"Aceita" não apaga o selo**: ele passa a dizer *por que* o número diverge, com a
justificativa e quem a assinou. Esconder uma divergência conhecida seria pior que exibi-la.

---

## 4. O que este fluxo NÃO faz (e por que)

- **Não edita dado fiscal.** Nenhuma tela desta plataforma escreve valor sobre o que o ente
  publicou. Divergência da fonte se resolve com retificação no SICONFI, feita pelo ente —
  não com correção manual no nosso banco, que produziria um número sem procedência.
- **Não fecha ocorrência por decurso de prazo.** Falha que some sozinha é falha que ninguém
  tratou.
- **Não promete que "reingerir" resolve.** A ação é oferecida quando o diagnóstico mostra
  entrega mais nova na fonte; nos demais casos ela não aparece.

---

## 5. Critérios de aceite

- Toda ocorrência em falha exibe **classe da causa e a evidência** que a sustenta.
- A ação oferecida corresponde à classe — e **nenhuma ação inexequível é oferecida**.
- Toda ação aplicada **reexecuta o check** e grava o veredito novo.
- `aceita_como_fato` exige justificativa e **mantém o selo**, agora com o motivo.
- A tratativa é privada da organização (RLS) e auditável: quem, quando, o quê, com que
  desfecho.
- Reexecutar um check que continua falhando **não** reabre a tratativa do zero: o histórico
  de tentativas fica visível, senão o mesmo caso é triado indefinidamente.
