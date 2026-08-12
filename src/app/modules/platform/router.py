"""Endpoints do control plane (Sprint 19) — exclusivos do operador da plataforma.

Todas as rotas exigem ``is_superuser``. A capacidade ``administrar`` do tenant **não**
abre nenhuma delas: quem administra a própria organização não licencia a si mesmo.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.deps import Principal, require_superuser, superuser_session
from app.modules.platform import service
from app.modules.platform.schemas import (
    AssinaturaPatch,
    AuditoriaPlataformaPage,
    IdentidadeVisualOut,
    LicencaCreate,
    LicencaOut,
    LicencaPatch,
    OrgProvisionar,
    OrgUsoOut,
    ProvisionamentoOut,
)
from app.modules.tenancy.schemas import AssinaturaInput, AssinaturaOut

router = APIRouter(prefix="/platform", tags=["platform"])


@router.post("/orgs", response_model=ProvisionamentoOut, status_code=201)
def provisionar_org(
    data: OrgProvisionar,
    principal: Principal = Depends(require_superuser),
    session: Session = Depends(superuser_session),
) -> ProvisionamentoOut:
    """Cria organização + papel de administrador + usuário inicial + licenças."""
    return service.provisionar_org(session, principal, data)


@router.get("/orgs", response_model=list[OrgUsoOut])
def listar_orgs(
    principal: Principal = Depends(require_superuser),
    session: Session = Depends(superuser_session),
) -> list[OrgUsoOut]:
    """Organizações com uso e licenças — a visão comercial da plataforma."""
    return service.uso_por_org(session)


@router.get("/uso", response_model=list[OrgUsoOut])
def uso(
    org_id: uuid.UUID | None = None,
    principal: Principal = Depends(require_superuser),
    session: Session = Depends(superuser_session),
) -> list[OrgUsoOut]:
    """Consumo por organização: entes, usuários, relatórios e consultas de IA."""
    return service.uso_por_org(session, org_id)


@router.get("/orgs/{org_id}/licencas", response_model=list[LicencaOut])
def listar_licencas(
    org_id: uuid.UUID,
    principal: Principal = Depends(require_superuser),
    session: Session = Depends(superuser_session),
) -> list[LicencaOut]:
    """Histórico completo — inclusive suspensas e expiradas."""
    return service.listar_licencas(session, org_id)


@router.post("/orgs/{org_id}/licencas", response_model=LicencaOut, status_code=201)
def conceder_licenca(
    org_id: uuid.UUID,
    data: LicencaCreate,
    principal: Principal = Depends(require_superuser),
    session: Session = Depends(superuser_session),
) -> LicencaOut:
    return service.conceder_licenca(session, principal, org_id, data)


@router.patch("/licencas/{licenca_id}", response_model=LicencaOut)
def alterar_licenca(
    licenca_id: uuid.UUID,
    data: LicencaPatch,
    principal: Principal = Depends(require_superuser),
    session: Session = Depends(superuser_session),
) -> LicencaOut:
    """Suspender, reativar ou prorrogar. Efeito imediato no escopo."""
    return service.alterar_licenca(session, principal, licenca_id, data)


@router.post("/orgs/{org_id}/assinatura", response_model=AssinaturaOut, status_code=201)
def definir_assinatura(
    org_id: uuid.UUID,
    data: AssinaturaInput,
    principal: Principal = Depends(require_superuser),
    session: Session = Depends(superuser_session),
) -> AssinaturaOut:
    """Cria/substitui a assinatura (métrica + preço) da organização — só o control plane."""
    return service.definir_assinatura(session, principal, org_id, data)


@router.patch("/orgs/{org_id}/assinatura", response_model=AssinaturaOut)
def alterar_assinatura(
    org_id: uuid.UUID,
    data: AssinaturaPatch,
    principal: Principal = Depends(require_superuser),
    session: Session = Depends(superuser_session),
) -> AssinaturaOut:
    """Altera campos pontuais da assinatura (ex.: só reajustar o preço)."""
    return service.alterar_assinatura(session, principal, org_id, data)


@router.get("/auditoria", response_model=AuditoriaPlataformaPage)
def auditoria(
    org_id: uuid.UUID | None = Query(None, description="Filtra por organização-alvo."),
    acao: str | None = Query(None, description="Filtra por ação (substring)."),
    recurso: str | None = Query(None, description="Filtra por recurso (substring)."),
    usuario_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, description="Busca livre em ação/recurso."),
    de: datetime | None = Query(None, description="Início do intervalo (ts >=)."),
    ate: datetime | None = Query(None, description="Fim do intervalo (ts <)."),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_superuser),
    session: Session = Depends(superuser_session),
) -> AuditoriaPlataformaPage:
    """Trilha de auditoria do próprio control plane — o superusuário nunca tem
    org_id de sessão e por isso nunca poderia chamar GET /admin/auditoria (que filtra
    pelo org_id do principal). Cobre inclusive ações sem organização (definir_brasao)."""
    return service.auditoria(
        session,
        org_id=org_id,
        acao=acao,
        recurso=recurso,
        usuario_id=usuario_id,
        texto=q,
        de=de,
        ate=ate,
        limit=limit,
        offset=offset,
    )


@router.put("/orgs/{org_id}/logo", response_model=IdentidadeVisualOut)
async def definir_logo(
    org_id: uuid.UUID,
    arquivo: UploadFile = File(...),
    principal: Principal = Depends(require_superuser),
    session: Session = Depends(superuser_session),
) -> IdentidadeVisualOut:
    conteudo = await arquivo.read()
    return service.definir_logo(
        session, principal, org_id, conteudo=conteudo, filename=arquivo.filename
    )


@router.put("/entes/{cod_ibge}/brasao", response_model=IdentidadeVisualOut)
async def definir_brasao(
    cod_ibge: str,
    arquivo: UploadFile = File(...),
    principal: Principal = Depends(require_superuser),
    session: Session = Depends(superuser_session),
) -> IdentidadeVisualOut:
    conteudo = await arquivo.read()
    return service.definir_brasao(
        session, principal, cod_ibge, conteudo=conteudo, filename=arquivo.filename
    )
