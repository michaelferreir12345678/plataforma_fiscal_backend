"""Schemas Pydantic do módulo de ingestão (admin)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.shared.source_ref import SourceRef


class RunRequest(BaseModel):
    """Backfill controlado de uma fonte.

    SICONFI usa ``entes``/``anos``/``periodos``; complementares usam o que faz sentido
    (BCB usa ``series`` + janela ``data_inicial``/``data_final``; IBGE/SADIPEM usam
    ``entes``/``anos``). Fontes sem versão própria usam a data de captura como versão.
    """

    fonte: str = Field(description="Ex.: siconfi_rreo, sadipem_pvl, bcb, ibge_populacao, ...")
    entes: list[str] = Field(default_factory=list, description="Códigos IBGE.")
    anos: list[int] = Field(default_factory=list)
    periodos: list[int] | None = Field(
        default=None, description="Nº do período (bimestre/quadrimestre/mês). Default = todos."
    )
    versao: str | None = Field(
        default=None, description="Versão da entrega; se ausente, usa a data de captura."
    )
    homologada_em: datetime | None = Field(
        default=None, description="Data de homologação da entrega (retificação usa data maior)."
    )
    # Fontes complementares
    series: list[int] | None = Field(default=None, description="Códigos de série do SGS/BCB.")
    data_inicial: date | None = Field(default=None, description="Janela inicial (BCB/SADIPEM).")
    data_final: date | None = Field(default=None, description="Janela final (BCB/SADIPEM).")
    # Fontes de arquivo (planilhas): url do arquivo, escopo, formato, etc.
    params: dict[str, Any] = Field(
        default_factory=dict, description="Parâmetros extras (ex.: url, escopo, formato)."
    )
    force: bool = Field(default=False, description="Reprocessa o silver mesmo sem novo bronze.")
    confirmar: bool = Field(
        default=False,
        description="Confirma explicitamente uma execução cuja estimativa excede o limiar.",
    )


class RunResult(BaseModel):
    fonte: str
    total_jobs: int
    ingeridos: int
    pulados: int
    silver_rows: int
    versoes_vigentes: list[str]
    pausado: bool = Field(
        default=False, description="Integração desligada (op.integracao): conector não rodou."
    )
    observacao: str | None = None


# --- Integrações (op.integracao; Sprint 18) ---
class IntegracaoOut(BaseModel):
    id: str
    codigo: str
    nome: str
    descricao: str | None = None
    categoria: str
    ativo: bool
    fontes: list[str]
    atualizado_em: datetime


class IntegracaoPatch(BaseModel):
    ativo: bool = Field(description="Liga/desliga a orquestração dos conectores da família.")


class EntregaStatus(BaseModel):
    cod_ibge: str
    relatorio: str
    periodo: str
    versao_entrega: str
    vigente: bool
    homologada_em: datetime


class DataResponse(BaseModel):
    """Leitura silver 'as of' (§6.5): versão vigente ou histórica conforme ``as_of``."""

    fonte: str
    cod_ibge: str
    periodo: str
    versao_entrega: str | None
    as_of: datetime | None
    total: int
    rows: list[dict[str, Any]]
    source_ref: SourceRef | None = None


# --- Catálogo de fontes e cobertura (Sprint 21 / instrumento da Sprint 20) ---
class FonteCatalogo(BaseModel):
    """Uma fonte do catálogo + observabilidade da última execução."""

    fonte: str
    familia: str
    relatorio: str
    descricao: str | None = None
    cadencia: str
    orgao: str | None = None
    url_origem: str | None = None
    escopo: str | None = None
    parser_versao: str | None = None
    paginas_impactadas: list[str] = Field(default_factory=list)
    dependencias: list[str] = Field(default_factory=list)
    ativo: bool = True
    ultima_execucao: datetime | None = None
    ultima_execucao_ok: datetime | None = None
    periodo_mais_recente: str | None = None
    defasagem_periodos: int | None = None
    entes_cobertos: int = 0
    registros_cobertos: int = Field(
        default=0,
        description="Soma de n_registros da cobertura materializada para a fonte.",
    )
    tipo_acesso: str | None = Field(
        default=None,
        description="Como se chega ao dado (api_rest, api_odata, catalogo_ckan, arquivo, "
        "raspagem_pdf). Na listagem é só o rótulo; a origem completa vem em /procedencia.",
    )


class ParametroOut(BaseModel):
    """Um parâmetro da chamada, explicado — nome cru não permite auditar."""

    nome: str
    exemplo: str
    significado: str


class EndpointOut(BaseModel):
    """Uma chamada concreta que a ingestão faz à fonte."""

    metodo: str
    url: str
    formato: str
    o_que_traz: str
    parametros: list[ParametroOut] = Field(default_factory=list)
    exemplo: str | None = Field(
        default=None,
        description="URL real e clicável que devolve o mesmo dado ingerido — a prova "
        "oferecida ao usuário, para conferir sem depender da nossa palavra.",
    )
    observacao: str | None = None


class ProcedenciaOut(BaseModel):
    """Origem completa de uma fonte: de onde o dado sai antes de virar número na tela."""

    fonte: str
    descricao: str | None = None
    orgao: str | None = None
    familia: str
    cadencia: str
    acesso: str
    acesso_rotulo: str
    portal: str
    documentacao: str | None = None
    licenca: str
    autenticacao: str
    como_funciona: str
    endpoints: list[EndpointOut] = Field(default_factory=list)
    paginas_impactadas: list[str] = Field(default_factory=list)
    dependencias: list[str] = Field(default_factory=list)
    requer_configuracao: str | None = None


class CoberturaItem(BaseModel):
    fonte: str
    cod_ibge: str
    uf: str | None = None
    ano: int
    periodo: str
    n_registros: int
    versao_entrega_vigente: str | None = None
    ingerido_em: datetime | None = None
    defasagem_periodos: int | None = None


class CoberturaResumo(BaseModel):
    """Agregado para leitura rápida da matriz (a UI mostra sem paginar tudo)."""

    total_linhas: int
    entes: int
    periodos: int
    fontes: list[str]


class CoberturaResponse(BaseModel):
    data: list[CoberturaItem]
    page: int
    page_size: int = Field(
        description=(
            "Número de grupos fonte×cod_ibge×ano selecionados por página; data pode "
            "conter mais linhas porque inclui todos os períodos dos grupos."
        )
    )
    total: int = Field(
        description="Total de grupos fonte×cod_ibge×ano após os filtros."
    )
    resumo: CoberturaResumo
