"""Demonstração do Padrão de Recurso Hierárquico (§6.1).

Expõe ``GET /hierarchy/demo`` sobre uma árvore de receita em memória, para exercitar
o contrato do ``DrillEnvelope`` (node/breadcrumb/children/measures/period/source_ref).
Sprints futuras substituem a árvore por dimensões reais da ``gold`` reusando
``shared/hierarchy.py`` sem alterar o contrato.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.shared.envelope import DrillEnvelope
from app.shared.hierarchy import HierarchyNode, build_drill_envelope, make_path
from app.shared.source_ref import SourceRef

router = APIRouter(tags=["hierarchy"])


def _sample_receita() -> list[HierarchyNode]:
    """Árvore-exemplo de receita (RREO Anexo 1), com medidas fictícias."""
    n1 = HierarchyNode("1", "Receitas Correntes", None, 1, make_path(None, "1"), {"valor": 1000})
    n11 = HierarchyNode(
        "1.1", "Impostos, Taxas e Contribuições", "1", 2, make_path(n1.path, "1.1"), {"valor": 700}
    )
    n111 = HierarchyNode(
        "1.1.1", "Impostos", "1.1", 3, make_path(n11.path, "1.1.1"), {"valor": 500}
    )
    n12 = HierarchyNode(
        "1.2", "Transferências Correntes", "1", 2, make_path(n1.path, "1.2"), {"valor": 300}
    )
    n2 = HierarchyNode("2", "Receitas de Capital", None, 1, make_path(None, "2"), {"valor": 200})
    return [n1, n11, n111, n12, n2]


_SOURCE_REF = SourceRef(relatorio="RREO", anexo="Anexo 1", periodo="2024-B6", versao_entrega="1")


@router.get("/hierarchy/demo", response_model=DrillEnvelope)
def hierarchy_demo(
    node: str | None = Query(None, description="Código do nó; vazio ⇒ raízes."),
    periodo: str = Query("2024-B6", description="Período fiscal canônico."),
) -> DrillEnvelope:
    return build_drill_envelope(
        _sample_receita(), node, period=periodo, source_ref=_SOURCE_REF
    )
