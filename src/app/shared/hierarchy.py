"""Padrão de Recurso Hierárquico — drill-down universal (§6.1).

Implementado **uma vez** e reusado por toda dimensão hierárquica (receita, despesa,
conta PCASP, poder/órgão, tempo, geografia). Toda hierarquia é uma tabela com a forma:

    codigo        text PK
    descricao     text
    parent_codigo text NULL   -- auto-referência (drill UP)
    nivel         int         -- 1 = raiz
    path          ltree       -- caminho materializado (ex.: '1.1.1')

O contrato de endpoint e o envelope de resposta são idênticos para todas as hierarquias
(ver ``DrillEnvelope`` em ``shared/envelope.py``):

    GET /<recurso>?ente={ibge}&periodo={p}&node={codigo|null}&depth={1}

- ``node = null``  ⇒ raízes da hierarquia.
- Drill DOWN: chamar de novo com ``node = codigo do filho``.
- Drill UP: seguir o ``breadcrumb`` (ancestrais) ou ``node = parent_codigo``.
- ``has_children`` evita round-trips.

O ``breadcrumb`` é resolvido pela cadeia ``parent_codigo`` (robusto a códigos com
pontos); ``path`` (ltree) é mantido para consultas de subárvore (``@>`` / ``<@``) e
``depth > 1``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from app.shared.envelope import DrillChild, DrillEnvelope, DrillNodeRef, Measures
from app.shared.source_ref import SourceRef

_LABEL_UNSAFE = re.compile(r"[^A-Za-z0-9_]")


def ltree_label(codigo: str) -> str:
    """Converte um código em um *label* válido de ltree (``[A-Za-z0-9_]``)."""
    return _LABEL_UNSAFE.sub("_", codigo)


def make_path(parent_path: str | None, codigo: str) -> str:
    """Constrói o ``path`` (ltree) do nó a partir do path do pai e do código."""
    label = ltree_label(codigo)
    return f"{parent_path}.{label}" if parent_path else label


@dataclass(frozen=True)
class HierarchyNode:
    """Nó genérico de qualquer dimensão hierárquica."""

    codigo: str
    descricao: str
    parent_codigo: str | None = None
    nivel: int = 1
    path: str = ""
    measures: Measures = field(default_factory=dict)

    def as_ref(self) -> DrillNodeRef:
        return DrillNodeRef(codigo=self.codigo, descricao=self.descricao, nivel=self.nivel)


def _index(nodes: Iterable[HierarchyNode]) -> dict[str, HierarchyNode]:
    return {n.codigo: n for n in nodes}


def roots(nodes: Iterable[HierarchyNode]) -> list[HierarchyNode]:
    """Nós raiz (``parent_codigo is None``), ordenados por código."""
    return sorted((n for n in nodes if n.parent_codigo is None), key=lambda n: n.codigo)


def children_of(nodes: Iterable[HierarchyNode], node_codigo: str | None) -> list[HierarchyNode]:
    """Filhos diretos de ``node_codigo`` (ou raízes se ``None``)."""
    if node_codigo is None:
        return roots(nodes)
    return sorted(
        (n for n in nodes if n.parent_codigo == node_codigo),
        key=lambda n: n.codigo,
    )


def breadcrumb_of(nodes: Iterable[HierarchyNode], node_codigo: str) -> list[HierarchyNode]:
    """Ancestrais de ``node_codigo`` na ordem raiz → … → pai (exclui o próprio nó)."""
    idx = _index(nodes)
    trail: list[HierarchyNode] = []
    current = idx.get(node_codigo)
    seen: set[str] = set()
    while current is not None and current.parent_codigo is not None:
        parent = idx.get(current.parent_codigo)
        if parent is None or parent.codigo in seen:
            break
        seen.add(parent.codigo)
        trail.append(parent)
        current = parent
    trail.reverse()
    return trail


def has_children(nodes: Iterable[HierarchyNode], node_codigo: str) -> bool:
    """Se ``node_codigo`` possui ao menos um filho."""
    return any(n.parent_codigo == node_codigo for n in nodes)


def build_drill_envelope(
    nodes: Iterable[HierarchyNode],
    node_codigo: str | None,
    *,
    period: str | None = None,
    source_ref: SourceRef | None = None,
    node_measures: Mapping[str, Measures] | None = None,
    as_of: datetime | None = None,
) -> DrillEnvelope:
    """Monta o :class:`DrillEnvelope` para ``node_codigo`` (``None`` ⇒ raízes).

    ``node_measures`` permite sobrepor as medidas por código (ex.: agregados calculados
    fora do nó). Se ausente, usa ``HierarchyNode.measures``.
    """
    nodes = list(nodes)
    measures_map = node_measures or {}

    def measures_for(codigo: str) -> Measures:
        node = _index(nodes).get(codigo)
        base = dict(node.measures) if node else {}
        base.update(measures_map.get(codigo, {}))
        return base

    children = [
        DrillChild(
            codigo=child.codigo,
            descricao=child.descricao,
            nivel=child.nivel,
            measures=measures_for(child.codigo),
            has_children=has_children(nodes, child.codigo),
        )
        for child in children_of(nodes, node_codigo)
    ]

    if node_codigo is None:
        return DrillEnvelope(
            node=None,
            breadcrumb=[],
            children=children,
            measures={},
            period=period,
            as_of=as_of,
            source_ref=source_ref,
        )

    node = _index(nodes).get(node_codigo)
    if node is None:
        raise KeyError(f"nó '{node_codigo}' inexistente na hierarquia")

    return DrillEnvelope(
        node=node.as_ref(),
        breadcrumb=[a.as_ref() for a in breadcrumb_of(nodes, node_codigo)],
        children=children,
        measures=measures_for(node_codigo),
        period=period,
        as_of=as_of,
        source_ref=source_ref,
    )
