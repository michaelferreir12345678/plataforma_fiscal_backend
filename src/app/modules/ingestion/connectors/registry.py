"""Registro central de conectores (fonte → classe) e mapa fonte → relatório.

Agrega os conectores de todas as fontes (SICONFI + complementares). O ``service`` usa
este registry para instanciar o conector correto por ``fonte``.
"""

from __future__ import annotations

from app.modules.ingestion.connectors import (
    bcb,
    capag,
    ibge,
    sadipem,
    siconfi,
    siconfi_rreo_minimos_pdf,
    siope,
    siops,
    transferencias,
)
from app.shared.ingestion.base import BaseConnector

CONNECTOR_REGISTRY: dict[str, type[BaseConnector]] = {
    **siconfi.CONNECTORS,
    **siconfi_rreo_minimos_pdf.CONNECTORS,
    **sadipem.CONNECTORS,
    **bcb.CONNECTORS,
    **ibge.CONNECTORS,
    **transferencias.CONNECTORS,
    **capag.CONNECTORS,
    **siops.CONNECTORS,
    **siope.CONNECTORS,
}

# Mapa fonte → relatório (para status e resolução de versão as_of).
FONTE_RELATORIO: dict[str, str] = {
    fonte: cls.relatorio for fonte, cls in CONNECTOR_REGISTRY.items()
}


# Metadado por fonte (DADO do catálogo, não código): família, cadência, órgão, escopo.
# Cadência ∈ {mensal, bimestral, quadrimestral, anual, diaria, continua, eventual};
# a cadência ancora o cálculo de defasagem da cobertura (Sprint 21/20).
class FonteMeta:
    __slots__ = (
        "familia",
        "cadencia",
        "orgao",
        "url_origem",
        "escopo",
        "descricao",
        "parser_versao",
        "paginas_impactadas",
        "dependencias",
        "requer_configuracao",
        "consulta_por_ente",
        "entrega_agregada",
    )

    def __init__(
        self,
        *,
        familia: str,
        cadencia: str,
        orgao: str,
        url_origem: str | None = None,
        escopo: str = "por_ente",
        descricao: str | None = None,
        parser_versao: str = "1",
        paginas_impactadas: tuple[str, ...] = (),
        dependencias: tuple[str, ...] = (),
        requer_configuracao: str | None = None,
        consulta_por_ente: bool | None = None,
        entrega_agregada: bool = False,
    ) -> None:
        self.familia = familia
        self.cadencia = cadencia
        self.orgao = orgao
        self.url_origem = url_origem
        self.escopo = escopo
        self.descricao = descricao
        self.parser_versao = parser_versao
        # Telas que deixam de ter dado se esta fonte falhar; e fontes das quais depende.
        self.paginas_impactadas = paginas_impactadas
        self.dependencias = dependencias
        #: Quando preenchido, a fonte **não pode funcionar** sem configuração adicional e
        #: fica fora do disparo "todas as fontes". Falha previsível a cada execução não é
        #: informação: ensina o operador a ignorar erro, e aí o erro que importa passa.
        self.requer_configuracao = requer_configuracao
        #: A **consulta** precisa da lista de entes? É diferente de a **entrega** ser
        #: nacional: o cadastro de entes e o FPM registram uma entrega ``BR`` única, mas
        #: perguntam à API um ente por vez. Tratar os dois como a mesma coisa fazia o
        #: disparo "todas as fontes" mandá-las sem ente nenhum — e elas falhavam com
        #: "nenhuma unidade encontrada".
        self.consulta_por_ente = (
            escopo != "nacional" if consulta_por_ente is None else consulta_por_ente
        )
        #: Uma entrega por período cobre **todos** os entes de uma vez (as transferências
        #: chegam por UF, não por município). O job **não pode** ser dividido por ente:
        #: todas as unidades gravariam a mesma chave de bronze ``(fonte, BR, período,
        #: versão)`` e só a primeira entraria — as demais seriam puladas como "já
        #: ingerido", e o dado dos outros entes sumiria em silêncio.
        self.entrega_agregada = entrega_agregada


_STN = "Tesouro Nacional (STN)"
_SICONFI_URL = "https://apidatalake.tesouro.gov.br/ords/siconfi/"

FONTE_META: dict[str, FonteMeta] = {
    "siconfi_rreo": FonteMeta(
        familia="siconfi",
        cadencia="bimestral",
        orgao=_STN,
        url_origem=_SICONFI_URL,
        descricao="Relatório Resumido da Execução Orçamentária",
        paginas_impactadas=(
            "dashboard",
            "limites",
            "receita",
            "despesa",
            "resultado",
            "caixa",
            "saude-educacao",
            "carteira",
            "benchmarking",
            "previsoes",
        ),
    ),
    "siconfi_rgf": FonteMeta(
        familia="siconfi",
        cadencia="quadrimestral",
        orgao=_STN,
        url_origem=_SICONFI_URL,
        descricao="Relatório de Gestão Fiscal (semestral p/ municípios < 50 mil hab.)",
        paginas_impactadas=("dashboard", "limites", "divida", "caixa", "carteira"),
        dependencias=("siconfi_rreo",),  # o % da RCL vem do RREO Anexo 03
    ),
    "siconfi_dca": FonteMeta(
        familia="siconfi",
        cadencia="anual",
        orgao=_STN,
        url_origem=_SICONFI_URL,
        descricao="Declaração de Contas Anuais (balanços)",
        paginas_impactadas=("patrimonio",),
    ),
    "siconfi_msc": FonteMeta(
        familia="siconfi",
        cadencia="mensal",
        orgao=_STN,
        url_origem=_SICONFI_URL,
        descricao="Matriz de Saldos Contábeis (conta PCASP)",
        paginas_impactadas=("patrimonio",),
    ),
    "siconfi_extratos": FonteMeta(
        familia="siconfi",
        cadencia="continua",
        orgao=_STN,
        url_origem=_SICONFI_URL,
        escopo="por_ente",
        descricao="Extrato de entregas (gatilho de retificação)",
        paginas_impactadas=("admin",),
    ),
    "siconfi_entes": FonteMeta(
        consulta_por_ente=True,
        familia="siconfi",
        cadencia="eventual",
        orgao=_STN,
        url_origem=_SICONFI_URL,
        escopo="nacional",
        descricao="Cadastro de entes",
        paginas_impactadas=("carteira", "benchmarking"),
    ),
    "siconfi_rreo_minimos_pdf": FonteMeta(
        requer_configuracao=(
            "O template de página é o do portal de Fortaleza. Cada ente publica os "
            "Anexos 8 e 12 no seu próprio portal — informe params.page_url_template."
        ),
        familia="siconfi",
        cadencia="bimestral",
        orgao="Portais municipais",
        descricao="Fallback PDF dos mínimos (Anexos 8/12) do RREO",
        paginas_impactadas=("saude-educacao",),
        dependencias=("siconfi_rreo",),
    ),
    "sadipem_pvl": FonteMeta(
        familia="sadipem",
        cadencia="diaria",
        orgao=_STN,
        descricao="Pedidos de verificação de limites (PVL)",
        paginas_impactadas=("divida",),
    ),
    "sadipem_op_contratada": FonteMeta(
        familia="sadipem",
        cadencia="diaria",
        orgao=_STN,
        descricao="Operações de crédito contratadas",
        paginas_impactadas=("divida",),
    ),
    "sadipem_cronograma_pgto": FonteMeta(
        familia="sadipem",
        cadencia="diaria",
        orgao=_STN,
        descricao="Cronograma de pagamento (serviço da dívida)",
        paginas_impactadas=("divida",),
        dependencias=("sadipem_op_contratada",),
    ),
    "sadipem_cdp": FonteMeta(
        familia="sadipem",
        cadencia="diaria",
        orgao=_STN,
        descricao="Situação no Cadastro da Dívida Pública",
        paginas_impactadas=("divida",),
    ),
    "bcb": FonteMeta(
        familia="bcb",
        cadencia="mensal",
        orgao="Banco Central (SGS)",
        url_origem="https://api.bcb.gov.br/dados/serie/",
        escopo="nacional",
        descricao="Índices econômicos (IPCA, Selic, IGP-M)",
        paginas_impactadas=("previsoes", "receita"),
    ),
    "ibge_populacao": FonteMeta(
        familia="ibge",
        cadencia="anual",
        orgao="IBGE",
        url_origem="https://servicodados.ibge.gov.br/api/",
        descricao="Estimativas de população",
        paginas_impactadas=("carteira", "benchmarking"),
    ),
    "ibge_pib": FonteMeta(
        familia="ibge",
        cadencia="anual",
        orgao="IBGE",
        url_origem="https://servicodados.ibge.gov.br/api/",
        descricao="PIB municipal",
        paginas_impactadas=("benchmarking",),
    ),
    "tesouro_fpm": FonteMeta(
        entrega_agregada=True,
        consulta_por_ente=True,
        familia="tesouro",
        cadencia="mensal",
        orgao=_STN,
        escopo="nacional",
        descricao="Repasses de FPM/FPE",
        paginas_impactadas=("receita", "previsoes"),
    ),
    "fnde_fundeb_repasse": FonteMeta(
        entrega_agregada=True,
        consulta_por_ente=True,
        familia="fnde",
        cadencia="mensal",
        orgao="FNDE",
        escopo="nacional",
        descricao="Repasses do FUNDEB",
        paginas_impactadas=("receita", "saude-educacao"),
    ),
    "transferencia_generica": FonteMeta(
        entrega_agregada=True,
        familia="transferencia",
        cadencia="mensal",
        orgao="Sefaz/STN",
        descricao="Demais transferências (ICMS cota-parte, royalties)",
        paginas_impactadas=("receita",),
        dependencias=("siconfi_rreo",),
    ),
    "tesouro_capag": FonteMeta(
        familia="tesouro",
        cadencia="anual",
        orgao=_STN,
        escopo="nacional",
        descricao="Capacidade de Pagamento (nota A–D)",
        paginas_impactadas=("divida",),
    ),
    "siops_saude": FonteMeta(
        familia="siops",
        cadencia="bimestral",
        orgao="Ministério da Saúde",
        descricao="Indicadores de saúde (ASPS)",
        paginas_impactadas=("saude-educacao",),
    ),
    "siope_educacao": FonteMeta(
        familia="siope",
        cadencia="bimestral",
        orgao="FNDE",
        descricao="Indicadores de educação (MDE)",
        paginas_impactadas=("saude-educacao",),
    ),
}
