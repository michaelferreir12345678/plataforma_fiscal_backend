"""Contrato da cobertura por página."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CoberturaFonteItem(BaseModel):
    """Uma fonte que alimenta a página, e o quanto ela alcança do escopo."""

    fonte: str
    descricao: str | None = None
    orgao: str | None = None
    entes_com_dado: int
    periodo_mais_recente: str | None = None


class CoberturaIndicadorItem(BaseModel):
    """Um indicador apresentado pela página.

    Separado das fontes de propósito: a página de saúde/educação depende do RREO, que
    quase todo ente tem, **e** dos mínimos apurados, que quase nenhum tem. Declarar só a
    fonte esconderia justamente a lacuna.
    """

    indicador: str
    entes_com_dado: int
    periodo_mais_recente: str | None = None


class EnteCobertura(BaseModel):
    cod_ibge: str
    tem_dado: bool
    periodo_mais_recente: str | None = None
    periodo_solicitado: str | None = None


class EscopoCobertura(BaseModel):
    """Denominador que o gestor reconhece: a carteira dele, não o país."""

    entes_no_escopo: int
    entes_com_dado: int

    @property
    def fracao(self) -> float:
        return self.entes_com_dado / self.entes_no_escopo if self.entes_no_escopo else 0.0


class CoberturaPagina(BaseModel):
    """Para quantos entes e períodos esta página de fato responde."""

    pagina: str
    ente: EnteCobertura
    escopo: EscopoCobertura
    fontes: list[CoberturaFonteItem] = Field(default_factory=list)
    indicadores: list[CoberturaIndicadorItem] = Field(default_factory=list)
    #: Indicadores cuja cobertura é residual dentro do escopo. É o que a tela precisa
    #: dizer em voz alta: a ausência é da nossa carga, não da entrega do ente.
    lacunas: list[str] = Field(default_factory=list)
    observacao: str | None = None
