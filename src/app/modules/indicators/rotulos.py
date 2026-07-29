"""Nome legível de cada indicador — fonte única.

Existiam quatro mapas independentes (cockpit, dashboard, benchmark, alertas), cada um com
um recorte diferente dos mesmos códigos. Um indicador fora do mapa daquele módulo aparecia
na tela com o código cru (``investimento_rcl``), e o mesmo indicador tinha nomes distintos
em telas distintas.

Regra: **nenhuma tela mostra o código**. Quando o código é desconhecido, a formatação de
fallback ainda produz algo lido por gente, e o código fica só no `source_ref`/export.
"""

from __future__ import annotations

#: Códigos canônicos → nome de exibição. A unidade não entra no nome: quem a exibe é a
#: própria célula (%, R$, R$/hab), e repetir "sobre a RCL" no rótulo de um gráfico já
#: rotulado em % da RCL polui a leitura.
ROTULOS: dict[str, str] = {
    # Limites legais (LRF)
    "pessoal_executivo": "Pessoal do Executivo",
    "pessoal_consolidado": "Pessoal consolidado",
    "divida_consolidada_liquida": "Dívida consolidada líquida",
    "operacoes_credito": "Operações de crédito",
    "garantias": "Garantias e contragarantias",
    "aro": "Antecipação de receita orçamentária",
    # Mínimos constitucionais
    "saude_minimo": "Aplicação em saúde (ASPS)",
    "educacao_mde": "Aplicação em educação (MDE)",
    "fundeb_profissionais": "FUNDEB — profissionais da educação",
    # Gerenciais (sem faixa legal)
    "rcl_per_capita": "RCL por habitante",
    "investimento_rcl": "Investimento sobre a RCL",
    "resultado_primario_rcl": "Resultado primário sobre a RCL",
    # Agregados usados nas visões consolidadas
    "rcl": "Receita Corrente Líquida",
    "disponibilidade": "Disponibilidade de caixa (líquida)",
}


def rotulo(codigo: str, *, padrao: str | None = None) -> str:
    """Nome de exibição do indicador; nunca devolve o código cru.

    ``padrao`` cobre o caso em que o chamador tem um nome melhor vindo do dado (a descrição
    publicada, por exemplo). Sem ele, um código desconhecido vira texto legível em vez de
    vazar ``snake_case`` para a tela.
    """
    if codigo in ROTULOS:
        return ROTULOS[codigo]
    if padrao:
        return padrao
    return humanizar(codigo)


def humanizar(codigo: str) -> str:
    """``investimento_rcl`` → ``Investimento RCL``. Último recurso, nunca a via normal."""
    palavras = [p for p in codigo.replace("-", "_").split("_") if p]
    if not palavras:
        return codigo
    # Siglas do domínio ficam em caixa alta; o resto vira sentença.
    siglas = {"rcl", "rgf", "rreo", "dca", "msc", "mde", "asps", "aro", "pib", "fundeb"}
    saida = [p.upper() if p.lower() in siglas else p for p in palavras]
    primeira = saida[0]
    if primeira.lower() not in siglas:
        primeira = primeira.capitalize()
    return " ".join([primeira, *saida[1:]])
