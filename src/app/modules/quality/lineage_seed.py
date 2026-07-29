"""Seed do grafo de linhagem (Sprint 26): fonte → bronze → silver → gold → endpoint → página.

O caminho do dado existia só implícito no código. Sem ele, ninguém responde às duas
perguntas que aparecem quando algo quebra:

- **"o que para de funcionar se `silver.siconfi_rgf` falhar?"** (para frente)
- **"de onde vem o número que estou vendo em /pessoal?"** (para trás)

O grafo é mantido **aqui**, em código, e aplicado por seed idempotente. A alternativa —
cadastro manual — viraria documentação desatualizada com aparência de verdade. Quando uma
sprint acrescenta um endpoint, a aresta entra junto com ele, e o teste de cobertura
reprova qualquer página do produto que não esteja no mapa.

A matriz §5 da auditoria foi o ponto de partida; o que está aqui é a realidade **depois**
das Sprints 22–25 (carteira real, /pessoal, mínimos no mart, benchmark multi-indicador).
"""

from __future__ import annotations

from dataclasses import dataclass

TIPO_FONTE_BRONZE = "fonte_bronze"
TIPO_BRONZE_SILVER = "bronze_silver"
TIPO_SILVER_GOLD = "silver_gold"
TIPO_GOLD_ENDPOINT = "gold_endpoint"
TIPO_ENDPOINT_PAGINA = "endpoint_pagina"


@dataclass(frozen=True)
class Aresta:
    origem: str
    destino: str
    tipo: str
    detalhe: dict[str, str] | None = None


# --- fonte externa → bronze ------------------------------------------------- #
_FONTE_BRONZE: tuple[tuple[str, str, str], ...] = (
    ("SICONFI/tt-rreo", "bronze.siconfi_rreo", "RREO bimestral (API de dados abertos)"),
    ("SICONFI/tt-rgf", "bronze.siconfi_rgf", "RGF quadrimestral/semestral"),
    ("SICONFI/tt-dca", "bronze.siconfi_dca", "DCA anual (balanços)"),
    ("SICONFI/tt-msc", "bronze.siconfi_msc", "MSC mensal por conta PCASP"),
    ("SICONFI/entes", "bronze.siconfi_entes", "cadastro de entes e população"),
    (
        "Portal municipal (PDF)", "bronze.siconfi_rreo_minimos_pdf",
        "RREO Anexos 8/12 — a API do SICONFI não os publica",
    ),
    ("SADIPEM", "bronze.sadipem_pvl", "pedidos de verificação de limites"),
    ("BCB/SGS", "bronze.bcb_indice", "IPCA (433) e Selic (4390)"),
    ("IBGE/agregados", "bronze.ibge_populacao", "estimativas populacionais"),
    ("IBGE/agregados", "bronze.ibge_pib", "PIB municipal"),
    ("IBGE/malhas", "bronze.ibge_malha", "geometria para o coroplético"),
    ("Tesouro/FPM", "bronze.tesouro_fpm", "transferências FPM"),
    ("Tesouro/CAPAG", "bronze.tesouro_capag", "capacidade de pagamento"),
    ("FNDE/FUNDEB", "bronze.fnde_fundeb_repasse", "repasses do fundo"),
    ("SIOPS", "bronze.siops_saude", "enriquecimento de saúde"),
    ("SIOPE", "bronze.siope_educacao", "enriquecimento de educação"),
)

# --- bronze → silver (mesma família, normalizada) --------------------------- #
_BRONZE_SILVER: tuple[str, ...] = (
    "siconfi_rreo", "siconfi_rgf", "siconfi_dca", "siconfi_msc", "siconfi_entes",
    "sadipem_pvl", "bcb_indice", "ibge_populacao", "ibge_pib", "ibge_malha",
    "tesouro_fpm", "tesouro_capag", "fnde_fundeb_repasse", "siops_saude", "siope_educacao",
)

# --- silver → gold ---------------------------------------------------------- #
_SILVER_GOLD: tuple[tuple[str, str, str], ...] = (
    ("silver.siconfi_rreo", "gold.fato_rcl", "Anexo 03 — RCL 12 meses"),
    ("silver.siconfi_rreo", "gold.fato_receita", "Anexo 01 — receita por origem"),
    ("silver.siconfi_rreo", "gold.fato_despesa", "Anexo 02 (função) e Anexo 01 (natureza)"),
    ("silver.siconfi_rreo", "gold.fato_resultado", "Anexo 06 — primário e nominal"),
    ("silver.siconfi_rreo", "gold.fato_rap", "Anexo 07 — restos a pagar"),
    ("silver.siconfi_rreo", "gold.fato_saude", "Anexo 12 — ASPS"),
    ("silver.siconfi_rreo", "gold.fato_educacao", "Anexo 08 — MDE e FUNDEB"),
    ("silver.siconfi_rreo", "gold.mart_indicador", "indicadores classificados por limite"),
    ("silver.siconfi_rgf", "gold.fato_pessoal", "Anexo 01 — despesa com pessoal"),
    ("silver.siconfi_rgf", "gold.fato_divida", "Anexo 02 — dívida consolidada"),
    ("silver.siconfi_rgf", "gold.fato_disponibilidade", "Anexo 05 — disponibilidade de caixa"),
    ("silver.siconfi_rgf", "gold.mart_indicador", "pessoal e DCL sobre a RCL"),
    ("silver.siconfi_dca", "gold.fato_balanco", "balanços patrimoniais e orçamentários"),
    ("silver.siconfi_msc", "gold.fato_msc_saldo", "saldos mensais por conta PCASP"),
    ("silver.siconfi_msc", "gold.mart_msc_rollup", "rollup pai = Σ filhos"),
    ("silver.siconfi_entes", "gold.dim_ente", "esfera, UF, população, região"),
    ("silver.sadipem_pvl", "gold.fato_vencimento", "cronograma e operações em tramitação"),
    ("silver.bcb_indice", "gold.fato_projecao", "exógenas das projeções"),
    ("silver.ibge_populacao", "gold.dim_ente", "população conformada"),
    ("silver.ibge_pib", "gold.dim_coorte", "faixa de PIB das coortes"),
    ("silver.ibge_malha", "gold.dim_malha", "geometria por UF"),
    ("silver.tesouro_fpm", "gold.fato_receita", "conciliação de transferências"),
    ("silver.tesouro_capag", "gold.fato_capag", "nota A–D"),
    ("silver.fnde_fundeb_repasse", "gold.fato_educacao", "enriquecimento FUNDEB"),
    ("silver.siops_saude", "gold.fato_saude", "enriquecimento SIOPS (não substitui o RREO)"),
    ("silver.siope_educacao", "gold.fato_educacao", "enriquecimento SIOPE (não substitui o RREO)"),
    ("gold.mart_indicador", "gold.mart_carteira", "snapshot de conformidade por ente"),
    ("gold.mart_indicador", "gold.mart_benchmark", "posição na coorte"),
    ("gold.mart_indicador", "gold.mart_consolidado_uf", "Σnum/Σden por UF"),
)

# --- gold → endpoint -------------------------------------------------------- #
_GOLD_ENDPOINT: tuple[tuple[str, str], ...] = (
    ("gold.mart_indicador", "GET /entes/{ibge}/cockpit"),
    ("gold.mart_indicador", "GET /entes/{ibge}/dashboard"),
    ("gold.mart_indicador", "GET /entes/{ibge}/limites"),
    ("gold.mart_indicador", "GET /benchmark"),
    ("gold.mart_indicador", "GET /alertas"),
    ("gold.fato_rcl", "GET /entes/{ibge}/rcl"),
    ("gold.fato_rcl", "GET /entes/{ibge}/cockpit"),
    ("gold.fato_receita", "GET /entes/{ibge}/receita"),
    ("gold.fato_despesa", "GET /entes/{ibge}/despesa"),
    ("gold.fato_pessoal", "GET /entes/{ibge}/pessoal"),
    ("gold.fato_divida", "GET /entes/{ibge}/divida"),
    ("gold.fato_resultado", "GET /entes/{ibge}/resultado"),
    ("gold.fato_disponibilidade", "GET /entes/{ibge}/caixa"),
    ("gold.fato_rap", "GET /entes/{ibge}/caixa"),
    ("gold.fato_saude", "GET /entes/{ibge}/saude"),
    ("gold.fato_educacao", "GET /entes/{ibge}/educacao"),
    ("gold.fato_balanco", "GET /entes/{ibge}/balancos"),
    ("gold.fato_msc_saldo", "GET /entes/{ibge}/msc/arvore"),
    ("gold.mart_msc_rollup", "GET /entes/{ibge}/msc/arvore"),
    ("gold.fato_balanco", "GET /entes/{ibge}/patrimonio"),
    ("gold.fato_capag", "GET /entes/{ibge}/divida/capag"),
    ("gold.fato_vencimento", "GET /entes/{ibge}/divida/cronograma"),
    ("gold.fato_projecao", "GET /entes/{ibge}/projecao"),
    ("gold.mart_benchmark", "GET /benchmark"),
    ("gold.mart_carteira", "GET /carteira/resumo"),
    ("gold.mart_consolidado_uf", "GET /uf/{uf}/consolidado"),
    ("gold.dim_ente", "GET /entes"),
    ("gold.dim_malha", "GET /uf/{uf}/malha"),
    ("gold.calendario_obrigacao", "GET /entes/{ibge}/calendario"),
    ("gold.norma_chunk", "POST /assistant/perguntar"),
    ("gold.data_quality_check", "GET /admin/qualidade"),
    ("gold.lineage_edge", "GET /admin/lineage"),
    ("gold.mart_cobertura_fonte", "GET /admin/cobertura"),
)

# --- endpoint → página ------------------------------------------------------ #
_ENDPOINT_PAGINA: tuple[tuple[str, str], ...] = (
    ("GET /entes/{ibge}/cockpit", "/dashboard"),
    ("GET /entes/{ibge}/dashboard", "/dashboard"),
    ("GET /entes/{ibge}/limites", "/limites"),
    ("GET /entes/{ibge}/receita", "/receita"),
    ("GET /entes/{ibge}/rcl", "/receita"),
    ("GET /entes/{ibge}/despesa", "/despesa"),
    ("GET /entes/{ibge}/pessoal", "/pessoal"),
    ("GET /entes/{ibge}/divida", "/divida"),
    ("GET /entes/{ibge}/divida/capag", "/divida"),
    ("GET /entes/{ibge}/divida/cronograma", "/divida"),
    ("GET /entes/{ibge}/resultado", "/resultado"),
    ("GET /entes/{ibge}/caixa", "/caixa"),
    ("GET /entes/{ibge}/saude", "/saude-educacao"),
    ("GET /entes/{ibge}/educacao", "/saude-educacao"),
    ("GET /entes/{ibge}/patrimonio", "/patrimonio"),
    ("GET /entes/{ibge}/balancos", "/patrimonio"),
    ("GET /entes/{ibge}/msc/arvore", "/patrimonio"),
    ("GET /entes/{ibge}/projecao", "/previsoes"),
    ("GET /benchmark", "/benchmarking"),
    ("GET /alertas", "/alertas"),
    ("GET /entes/{ibge}/calendario", "/alertas"),
    ("GET /carteira/resumo", "/carteira"),
    ("GET /uf/{uf}/consolidado", "/carteira"),
    ("GET /uf/{uf}/malha", "/carteira"),
    ("POST /assistant/perguntar", "/assistente"),
    ("GET /admin/qualidade", "/central-dados"),
    ("GET /admin/lineage", "/central-dados"),
    ("GET /admin/cobertura", "/central-dados"),
    ("GET /entes", "/dashboard"),
)

# Páginas fiscais do produto — o teste de cobertura exige todas no grafo.
PAGINAS_DO_PRODUTO: frozenset[str] = frozenset(
    {
        "/dashboard", "/carteira", "/limites", "/receita", "/despesa", "/pessoal",
        "/divida", "/resultado", "/caixa", "/saude-educacao", "/patrimonio",
        "/previsoes", "/benchmarking", "/alertas", "/assistente", "/central-dados",
    }
)


def arestas() -> list[Aresta]:
    """Todas as arestas do grafo, prontas para o seed idempotente."""
    resultado: list[Aresta] = []
    for fonte, bronze, nota in _FONTE_BRONZE:
        resultado.append(Aresta(fonte, bronze, TIPO_FONTE_BRONZE, {"nota": nota}))
    for nome in _BRONZE_SILVER:
        resultado.append(
            Aresta(f"bronze.{nome}", f"silver.{nome}", TIPO_BRONZE_SILVER,
                   {"nota": "normalização e tipagem; idempotente por versão de entrega"})
        )
    for origem, destino, nota in _SILVER_GOLD:
        resultado.append(Aresta(origem, destino, TIPO_SILVER_GOLD, {"nota": nota}))
    for origem, destino in _GOLD_ENDPOINT:
        resultado.append(Aresta(origem, destino, TIPO_GOLD_ENDPOINT))
    for origem, destino in _ENDPOINT_PAGINA:
        resultado.append(Aresta(origem, destino, TIPO_ENDPOINT_PAGINA))
    return resultado
