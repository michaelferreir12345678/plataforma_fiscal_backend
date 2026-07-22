"""Testes do envelope de drill (§6.1) — contrato node/breadcrumb/children/measures."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.shared.hierarchy import HierarchyNode, build_drill_envelope, make_path
from app.shared.source_ref import SourceRef


def _tree() -> list[HierarchyNode]:
    a = HierarchyNode("1", "Raiz", None, 1, make_path(None, "1"), {"valor": 10})
    b = HierarchyNode("1.1", "Filho", "1", 2, make_path(a.path, "1.1"), {"valor": 6})
    c = HierarchyNode("1.1.1", "Neto", "1.1", 3, make_path(b.path, "1.1.1"), {"valor": 4})
    d = HierarchyNode("1.2", "Filho 2", "1", 2, make_path(a.path, "1.2"), {"valor": 4})
    return [a, b, c, d]


def test_raizes_quando_node_none() -> None:
    env = build_drill_envelope(_tree(), None, period="2024")
    assert env.node is None
    assert env.breadcrumb == []
    assert [c.codigo for c in env.children] == ["1"]
    assert env.children[0].has_children is True
    assert env.children[0].measures == {"valor": 10}


def test_drill_down_node_intermediario() -> None:
    src = SourceRef(relatorio="RREO", anexo="Anexo 1", periodo="2024", versao_entrega="1")
    env = build_drill_envelope(_tree(), "1.1", period="2024", source_ref=src)

    assert env.node is not None
    assert env.node.codigo == "1.1"
    assert env.node.nivel == 2
    # breadcrumb = ancestrais raiz -> pai
    assert [b.codigo for b in env.breadcrumb] == ["1"]
    # children = filhos diretos
    assert [c.codigo for c in env.children] == ["1.1.1"]
    assert env.children[0].has_children is False
    assert env.measures == {"valor": 6}
    assert env.period == "2024"
    assert env.source_ref is not None
    assert env.source_ref.relatorio == "RREO"


def test_endpoint_demo_respeita_contrato(client: TestClient) -> None:
    # Sem node ⇒ raízes.
    root = client.get("/hierarchy/demo").json()
    for key in ("node", "breadcrumb", "children", "measures", "period", "source_ref"):
        assert key in root
    assert root["node"] is None
    codigos = {c["codigo"] for c in root["children"]}
    assert {"1", "2"} <= codigos

    # Drill em '1.1' ⇒ node preenchido, breadcrumb com a raiz, filho '1.1.1'.
    node = client.get("/hierarchy/demo", params={"node": "1.1"}).json()
    assert node["node"]["codigo"] == "1.1"
    assert [b["codigo"] for b in node["breadcrumb"]] == ["1"]
    assert [c["codigo"] for c in node["children"]] == ["1.1.1"]
    assert node["source_ref"]["relatorio"] == "RREO"
