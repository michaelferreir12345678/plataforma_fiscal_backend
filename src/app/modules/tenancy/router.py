"""Endpoints do módulo tenancy: auth, me, orgs, users, papeis, carteira (§ Sprint 0)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_current_principal, get_db, require_capability
from app.core.errors import AppError
from app.modules.tenancy import service
from app.modules.tenancy.schemas import (
    CarteiraEnteCreate,
    CarteiraEnteOut,
    MeResponse,
    OrgCreate,
    OrgOut,
    PapelCreate,
    PapelOut,
    TokenResponse,
    UserCreate,
    UserOut,
)
from app.shared.scope import require_ente_scope

router = APIRouter()


# --- Autenticação ---
@router.post("/auth/login", response_model=TokenResponse, tags=["auth"])
def login(form: OAuth2PasswordRequestForm = Depends()) -> TokenResponse:
    """OAuth2 password grant: ``username`` = e-mail. Retorna o JWT de acesso."""
    return service.authenticate(form.username, form.password)


@router.get("/me", response_model=MeResponse, tags=["auth"])
def me(principal: Principal = Depends(get_current_principal)) -> MeResponse:
    return service.build_me(principal.usuario_id, principal.org_id)


# --- Organizações (plano de controle; requer 'administrar') ---
@router.post("/orgs", response_model=OrgOut, status_code=201, tags=["orgs"])
def create_org(
    data: OrgCreate, _: Principal = Depends(require_capability("administrar"))
) -> OrgOut:
    return service.create_org(data)


@router.get("/orgs", response_model=list[OrgOut], tags=["orgs"])
def list_orgs(_: Principal = Depends(require_capability("administrar"))) -> list[OrgOut]:
    return service.list_orgs()


# --- Usuários (plano de controle; requer 'administrar') ---
@router.post("/users", response_model=UserOut, status_code=201, tags=["users"])
def create_user(
    data: UserCreate, _: Principal = Depends(require_capability("administrar"))
) -> UserOut:
    return service.create_user(data)


@router.get("/users", response_model=list[UserOut], tags=["users"])
def list_users(_: Principal = Depends(require_capability("administrar"))) -> list[UserOut]:
    return service.list_users()


# --- Papéis / RBAC (plano de dados; isolado por RLS na org do principal) ---
@router.post("/papeis", response_model=PapelOut, status_code=201, tags=["papeis"])
def create_papel(
    data: PapelCreate,
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> PapelOut:
    if principal.org_id is None:
        raise AppError(status=400, title="Sem organização", detail="Principal sem org ativa.")
    return service.create_papel(session, principal.org_id, data)


@router.get("/papeis", response_model=list[PapelOut], tags=["papeis"])
def list_papeis(
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> list[PapelOut]:
    if principal.org_id is None:
        return []
    return service.list_papeis(session, principal.org_id)


# --- Carteira (plano de dados; isolado por RLS na org do principal) ---
@router.post("/carteira", response_model=CarteiraEnteOut, status_code=201, tags=["carteira"])
def add_carteira_ente(
    data: CarteiraEnteCreate,
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> CarteiraEnteOut:
    if principal.org_id is None:
        raise AppError(status=400, title="Sem organização", detail="Principal sem org ativa.")
    return service.add_carteira_ente(session, principal.org_id, data)


@router.get("/carteira", response_model=list[CarteiraEnteOut], tags=["carteira"])
def list_carteira(
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> list[CarteiraEnteOut]:
    if principal.org_id is None:
        return []
    return service.list_carteira(session, principal.org_id)


@router.get("/carteira/consulta", response_model=CarteiraEnteOut, tags=["carteira"])
def consultar_ente(
    ente: str = Depends(require_ente_scope),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> CarteiraEnteOut:
    """Consulta um ente da carteira. Aplica o middleware de escopo (§6.4): 403 se fora."""
    assert principal.org_id is not None
    ente_row = service.list_carteira(session, principal.org_id)
    match = next((e for e in ente_row if e.cod_ibge == ente), None)
    if match is None:  # pragma: no cover — require_ente_scope já garante presença
        raise AppError(status=404, title="Não encontrado", detail="Ente não está na carteira.")
    return match
