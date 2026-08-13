"""Administração das credenciais MCP — montada na **API**, não no servidor MCP.

Quem emite e revoga credencial é um administrador da organização, pelo navegador, com JWT
e a capacidade ``administrar``. O servidor MCP não expõe nenhuma destas rotas: uma
credencial que pudesse emitir outra credencial transformaria um vazamento em acesso
permanente e auto-renovável.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.mcp import service
from app.modules.mcp.models import McpCredencial
from app.modules.mcp.schemas import (
    CredenciaisOut,
    CredencialCreate,
    CredencialCriadaOut,
    CredencialOut,
)

router = APIRouter(prefix="/admin/mcp", tags=["mcp-admin"])


def _to_out(credencial: McpCredencial) -> CredencialOut:
    return CredencialOut(
        id=credencial.id,
        nome=credencial.nome,
        prefixo=credencial.prefixo,
        papel_id=credencial.papel_id,
        escopo_ibges=list(credencial.escopo_ibges) if credencial.escopo_ibges else None,
        expira_em=credencial.expira_em,
        revogada_em=credencial.revogada_em,
        ultimo_uso_em=credencial.ultimo_uso_em,
        criado_em=credencial.criado_em,
        ativa=credencial.utilizavel_em(datetime.now(UTC)),
    )


@router.post("/credenciais", response_model=CredencialCriadaOut, status_code=201)
def emitir_credencial(
    body: CredencialCreate,
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> CredencialCriadaOut:
    """Emite uma credencial de organização para um cliente MCP externo."""
    emitida = service.emitir(
        session,
        principal,
        nome=body.nome,
        papel_id=body.papel_id,
        escopo_ibges=body.escopo_ibges,
        expira_em=body.expira_em,
    )
    base = _to_out(emitida.credencial)
    return CredencialCriadaOut(**base.model_dump(), token=emitida.token)


@router.get("/credenciais", response_model=CredenciaisOut)
def listar_credenciais(
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> CredenciaisOut:
    """Credenciais da própria organização (sem segredo, com último uso)."""
    return CredenciaisOut(
        itens=[_to_out(c) for c in service.listar(session, principal)]
    )


@router.delete("/credenciais/{credencial_id}", response_model=CredencialOut)
def revogar_credencial(
    credencial_id: uuid.UUID,
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> CredencialOut:
    """Revoga a credencial. Identificador de outra organização ⇒ 404 (padrão E1)."""
    return _to_out(service.revogar(session, principal, credencial_id))
