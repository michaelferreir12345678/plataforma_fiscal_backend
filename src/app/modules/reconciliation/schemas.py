"""Contrato da reconciliação com valor oficial."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.shared.source_ref import SourceRef


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
    """Um par divergente, com a procedência **dos dois lados** (§6.3).

    A26 (E1): a reconciliação devolvia dois números fiscais e nenhum ``source_ref``. Era a
    lacuna mais incômoda da rastreabilidade, porque aqui o número existe **para ser
    contestado**: sem saber de qual entrega saiu cada lado, o analista não consegue
    distinguir divergência real de comparação entre versões diferentes — que é
    exatamente a família de defeito do A15.
    """

    cod_ibge: str
    periodo: str
    valor_plataforma: Decimal
    valor_oficial: Decimal
    diferenca: Decimal
    #: ``None`` quando o valor oficial é zero: dividir por ele daria infinito, e devolver
    #: zero se leria como "sem diferença".
    diferenca_pct: Decimal | None = None
    causa_provavel: str
    #: Entrega do lado calculado pela plataforma (``gold.fato_rcl``).
    source_ref_plataforma: SourceRef | None = None
    #: Entrega do lado publicado pelo ente — inclusive quando a correção chegou por
    #: **republicação** num quadrimestre posterior (A15), caso em que a versão aqui é a da
    #: entrega que republicou, não a do período comparado.
    source_ref_oficial: SourceRef | None = None


class ReconciliacaoResultado(BaseModel):
    codigo: str
    titulo: str
    fonte_oficial: str
    metodologia: str
    resumo: ReconciliacaoResumo
    divergencias: list[DivergenciaItem] = Field(default_factory=list)
    truncado: bool = False
    #: Procedência agregada da comparação: qual relatório/anexo é o lado oficial. A
    #: versão por par vive em cada :class:`DivergenciaItem` — uma só não caberia, porque
    #: cada ente/período tem a sua.
    source_ref: SourceRef | None = None
