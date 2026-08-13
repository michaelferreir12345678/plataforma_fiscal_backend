"""Schemas do monitor de limites (lista, detalhe com drill, simulador)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.shared.envelope import DrillNodeRef
from app.shared.source_ref import SourceRef


class LimiteItem(BaseModel):
    indicador: str
    esfera: str
    sentido: str  # teto | piso
    valor_rs: Decimal | None = None
    valor_pct_rcl: Decimal | None = None
    faixa: str | None = None
    #: ``None`` quando o indicador **não tem limite legal** (os gerenciais: investimento
    #: sobre a RCL, resultado primário, RCL por habitante). Antes vinha ``0``, e "limite
    #: 0%" numa tela de conformidade lê-se como "o teto é zero" — o oposto de "não há teto".
    teto_pct: Decimal | None = None
    alerta_pct: Decimal | None = None
    prudencial_pct: Decimal | None = None
    distancia_teto: Decimal | None = None  # teto − valor (teto) / valor − piso (piso)
    distancia_alerta: Decimal | None = None
    # Sprint 25C: com os mínimos no mart, a lista deixou de ser só percentuais da RCL.
    # ``denominador`` é o que a tela usa para rotular a coluna sem mentir a base.
    denominador: str = "rcl"
    base_valor: Decimal | None = None


class LimitesResponse(BaseModel):
    cod_ibge: str
    periodo: str
    as_of: datetime | None = None
    versao_entrega: str
    itens: list[LimiteItem]
    source_ref: SourceRef


class ProvidenciaOut(BaseModel):
    faixa: str
    texto: str
    base_legal: str | None = None


class SerieItem(BaseModel):
    periodo: str
    valor_pct_rcl: Decimal | None = None
    faixa: str | None = None
    valor_rs: Decimal | None = None
    #: A entrega **daquele período** — não a do período mais recente (Sprint IA-1b).
    #: Cada ponto da série é lido de uma vigência própria; sem declarar qual, dois pontos
    #: vizinhos podem vir de versões diferentes sem que nada na resposta o diga, que é a
    #: forma silenciosa da família A14/A15.
    versao_entrega: str | None = None
    source_ref: SourceRef | None = None


class LimiteDetail(BaseModel):
    cod_ibge: str
    periodo: str
    as_of: datetime | None = None
    indicador: str
    esfera: str
    faixa: str | None = None
    valor_rs: Decimal | None = None
    valor_pct_rcl: Decimal | None = None
    #: ``None`` quando o indicador **não tem limite legal** (os gerenciais: investimento
    #: sobre a RCL, resultado primário, RCL por habitante). Antes vinha ``0``, e "limite
    #: 0%" numa tela de conformidade lê-se como "o teto é zero" — o oposto de "não há teto".
    teto_pct: Decimal | None = None
    memoria: dict | None = None
    providencias: list[ProvidenciaOut]
    serie_historica: list[SerieItem]
    periodo_breadcrumb: list[DrillNodeRef]  # drill UP (ano → …)
    source_ref: SourceRef


class SimularRequest(BaseModel):
    novo_valor_rs: Decimal | None = None
    delta_rs: Decimal | None = None


class SimularResponse(BaseModel):
    indicador: str
    valor_rs_atual: Decimal | None = None
    valor_rs_simulado: Decimal
    valor_pct_atual: Decimal | None = None
    valor_pct_simulado: Decimal
    faixa_atual: str | None = None
    faixa_simulada: str
    #: ``None`` quando o indicador **não tem limite legal** (os gerenciais: investimento
    #: sobre a RCL, resultado primário, RCL por habitante). Antes vinha ``0``, e "limite
    #: 0%" numa tela de conformidade lê-se como "o teto é zero" — o oposto de "não há teto".
    teto_pct: Decimal | None = None
    persistido: bool = False
