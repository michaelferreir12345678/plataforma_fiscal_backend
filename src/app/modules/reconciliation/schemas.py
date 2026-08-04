"""Contrato da reconciliação com valor oficial."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class ReconciliacaoResumo(BaseModel):
    pares: int
    conferem: int
    divergem: int
    #: Um lado sem valor não é divergência — é ausência, e contá-la como acerto ou como
    #: erro distorceria a taxa nos dois sentidos.
    sem_par: int

    @property
    def taxa_conferencia(self) -> float:
        comparaveis = self.conferem + self.divergem
        return self.conferem / comparaveis if comparaveis else 0.0


class DivergenciaItem(BaseModel):
    cod_ibge: str
    periodo: str
    valor_plataforma: Decimal
    valor_oficial: Decimal
    diferenca: Decimal
    #: ``None`` quando o valor oficial é zero: dividir por ele daria infinito, e devolver
    #: zero se leria como "sem diferença".
    diferenca_pct: Decimal | None = None
    causa_provavel: str


class ReconciliacaoResultado(BaseModel):
    codigo: str
    titulo: str
    fonte_oficial: str
    metodologia: str
    resumo: ReconciliacaoResumo
    divergencias: list[DivergenciaItem] = Field(default_factory=list)
    truncado: bool = False
