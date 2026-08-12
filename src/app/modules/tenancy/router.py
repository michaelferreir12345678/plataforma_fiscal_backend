"""Endpoints do módulo tenancy: auth, me, orgs, users, papeis, carteira (§ Sprint 0)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_current_principal, get_db, require_capability
from app.core.errors import AppError
from app.modules.tenancy import service
from app.modules.tenancy.schemas import (
    AlterarSenhaInput,
    CarteiraEnteCreate,
    CarteiraEnteOut,
    MeResponse,
    MinhaLicencaOut,
    OrgCreate,
    OrgOut,
    PapelCreate,
    PapelOut,
    PapelUpdate,
    PerfilInput,
    TokenResponse,
    TrocarOrgInput,
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


@router.post("/me/organizacao", response_model=TokenResponse, tags=["auth"])
def trocar_organizacao(
    data: TrocarOrgInput, principal: Principal = Depends(get_current_principal)
) -> TokenResponse:
    """Troca a organização ativa entre os vínculos do próprio usuário (reautentica).

    Não cria vínculo: entrar numa organização de que não se participa é concessão do
    operador da plataforma, não decisão de quem quer entrar.
    """
    return service.trocar_organizacao(principal, data.org_id, senha=data.senha)


@router.patch("/me", response_model=MeResponse, tags=["auth"])
def atualizar_perfil(
    data: PerfilInput, principal: Principal = Depends(get_current_principal)
) -> MeResponse:
    return service.atualizar_perfil(principal, nome=data.nome)


@router.post("/me/senha", status_code=204, response_class=Response, tags=["auth"])
def alterar_senha(
    data: AlterarSenhaInput, principal: Principal = Depends(get_current_principal)
) -> Response:
    """Troca a própria senha. 204 sem corpo: não há o que devolver, e devolver o
    usuário aqui só ampliaria a superfície de uma rota que lida com credencial."""
    service.alterar_senha(
        principal, senha_atual=data.senha_atual, senha_nova=data.senha_nova
    )
    return Response(status_code=204)


@router.get("/me/licencas", response_model=list[MinhaLicencaOut], tags=["auth"])
def minhas_licencas(
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> list[MinhaLicencaOut]:
    """A licença da própria organização (Sprint H1) — hoje só se descobre por um 403
    ao tentar adicionar um ente fora dela à carteira."""
    return service.minhas_licencas(session, principal)


# --- Organizações (plano de controle; requer 'administrar') ---
@router.post("/orgs", response_model=OrgOut, status_code=201, tags=["orgs"])
def create_org(
    data: OrgCreate, _: Principal = Depends(require_capability("administrar"))
) -> OrgOut:
    # ``administrar`` é uma capacidade do tenant, não uma credencial de operador da
    # plataforma. Provisionar outro tenant com uma sessão admin global criaria uma
    # organização órfã e permitiria expansão de escopo indevida.
    raise AppError(
        status=403,
        title="Provisionamento restrito",
        detail="A criação de organizações exige o plano de controle da plataforma.",
        type_="urn:plataforma-fiscal:error:platform-admin-required",
    )


@router.get("/orgs", response_model=list[OrgOut], tags=["orgs"])
def list_orgs(
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> list[OrgOut]:
    return service.list_orgs(session, principal)


# --- Usuários (plano de controle; requer 'administrar') ---
@router.post("/users", response_model=UserOut, status_code=201, tags=["users"])
def create_user(
    data: UserCreate,
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> UserOut:
    return service.create_user(session, principal, data)


@router.get("/users", response_model=list[UserOut], tags=["users"])
def list_users(
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> list[UserOut]:
    return service.list_users(session, principal)


# --- Papéis / RBAC (plano de dados; isolado por RLS na org do principal) ---
@router.post("/papeis", response_model=PapelOut, status_code=201, tags=["papeis"])
def create_papel(
    data: PapelCreate,
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> PapelOut:
    if principal.org_id is None:
        raise AppError(status=400, title="Sem organização", detail="Principal sem org ativa.")
    return service.create_papel(
        session, principal.org_id, data, actor_user_id=principal.usuario_id
    )


@router.get("/papeis", response_model=list[PapelOut], tags=["papeis"])
def list_papeis(
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> list[PapelOut]:
    if principal.org_id is None:
        return []
    return service.list_papeis(session, principal.org_id)


@router.patch("/papeis/{papel_id}", response_model=PapelOut, tags=["papeis"])
def update_papel(
    papel_id: uuid.UUID,
    data: PapelUpdate,
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> PapelOut:
    """Atualiza as capacidades de um papel da org (aba Permissões — fim do mock)."""
    if principal.org_id is None:
        raise AppError(status=400, title="Sem organização", detail="Principal sem org ativa.")
    return service.update_papel_capacidades(
        session,
        principal.org_id,
        papel_id,
        list(data.capacidades),
        actor_user_id=principal.usuario_id,
    )


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
