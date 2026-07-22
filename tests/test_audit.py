"""Teste do middleware de auditoria (§7): requisições autenticadas gravam em op.audit_log."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import admin_session
from app.modules.tenancy.models import AuditLog
from tests.conftest import auth_header, login


def test_requisicao_autenticada_gera_audit_log(client: TestClient, make_org) -> None:
    fx = make_org(capacidades=["ver", "administrar"], entes=["2304400"])
    token = login(client, fx.email, fx.senha)

    resp = client.get("/carteira", headers=auth_header(token))
    assert resp.status_code == 200

    with admin_session() as s:
        rows = list(
            s.scalars(
                select(AuditLog).where(
                    AuditLog.org_id == fx.org_id, AuditLog.usuario_id == fx.usuario_id
                )
            )
        )
    assert any(r.recurso == "/carteira" and r.acao == "GET /carteira" for r in rows)
