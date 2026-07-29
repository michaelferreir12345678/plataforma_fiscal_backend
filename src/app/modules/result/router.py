"""Endpoints do Resultado Fiscal (Módulo 8) — Padrão de Detalhe + cascata/reconciliação/meta."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.result import service
from app.modules.result.schemas import (
    CascataOut,
    MemoriaResultado,
    MetaCadastro,
    MetaFiscalUpsert,
    MetaOut,
    ReconciliacaoOut,
    ResultadoDetalhe,
)
from app.shared.scope import assert_ente_in_scope

router = APIRouter(tags=["result"])

_PERIODO_Q = Query(..., description="Período RREO (ex.: 2024-B6).")
_AS_OF_Q = Query(None, description="Consulta bitemporal 'as of' (§6.5).")


@router.get("/entes/{cod_ibge}/resultado", response_model=ResultadoDetalhe)
def detalhe_resultado(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> ResultadoDetalhe:
    """Resultado primário e nominal × meta + componentes primários (drill receita/despesa)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_detalhe(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/resultado/cascata", response_model=CascataOut)
def cascata_resultado(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> CascataOut:
    """Waterfall: receita→−despesa→primário→−juros→nominal (acima) e DCL início→fim (abaixo)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_cascata(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/resultado/reconciliacao", response_model=ReconciliacaoOut)
def reconciliacao_resultado(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> ReconciliacaoOut:
    """Acima × abaixo da linha, ajustes metodológicos e cruzamento da DCL com a Sprint 8."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_reconciliacao(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/resultado/meta", response_model=MetaOut)
def meta_resultado(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> MetaOut:
    """Realizado × meta fiscal (LDO) + projeção de fechamento e esforço necessário.

    Tela do ente: considera a meta cadastrada pela organização quando o Anexo 6 não a
    publica. Agregados e relatórios usam ``/resultado`` (só meta oficial).
    """
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_meta(
        session, cod_ibge, periodo, as_of=as_of, org_id=principal.org_id
    )


@router.get("/entes/{cod_ibge}/meta-fiscal", response_model=list[MetaCadastro])
def listar_meta_fiscal(
    cod_ibge: str,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> list[MetaCadastro]:
    """Metas da LDO cadastradas por esta organização para o ente (dado privado do tenant)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.listar_metas_fiscais(session, principal, cod_ibge)


@router.put("/entes/{cod_ibge}/meta-fiscal", response_model=MetaCadastro, status_code=200)
def salvar_meta_fiscal(
    cod_ibge: str,
    body: MetaFiscalUpsert,
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> MetaCadastro:
    """Cadastra/atualiza a meta da LDO do exercício (auditado; exige administrar)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.salvar_meta_fiscal(session, principal, cod_ibge, body)


@router.delete("/entes/{cod_ibge}/meta-fiscal/{meta_id}", status_code=204, response_model=None)
def excluir_meta_fiscal(
    cod_ibge: str,
    meta_id: uuid.UUID,
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> Response:
    """Remove a meta cadastrada (auditado; exige administrar)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    service.excluir_meta_fiscal(session, principal, cod_ibge, meta_id)
    return Response(status_code=204)


@router.get("/entes/{cod_ibge}/resultado/memoria", response_model=MemoriaResultado)
def memoria_resultado(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> MemoriaResultado:
    """Memória de cálculo: componentes, fórmulas, identidades e origem de cada número."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_memoria(session, cod_ibge, periodo, as_of=as_of)
