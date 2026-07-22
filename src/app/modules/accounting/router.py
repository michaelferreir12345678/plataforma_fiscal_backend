"""Endpoints de Patrimônio (DCA) & Explorador MSC (Módulo 11) — Sprint 12."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.accounting import service
from app.modules.accounting.schemas import (
    BalancosIndex,
    ConciliacaoOut,
    MatrizMensalOut,
    PatrimonioDetalhe,
)
from app.shared.envelope import DrillEnvelope
from app.shared.scope import assert_ente_in_scope

router = APIRouter(tags=["accounting"])

_ANO_Q = Query(None, description="Exercício (ex.: 2022). Vazio = ano mais recente disponível.")
_AS_OF_Q = Query(None, description="Consulta bitemporal 'as of' (§6.5).")
_NODE_Q = Query(None, description="Conta PCASP (drill); vazio = raízes (classes).")
_PERIODO_Q = Query(
    None, description="Mês da MSC (AAAA-Mnn) ou ano; vazio = último mês disponível."
)


@router.get("/entes/{cod_ibge}/patrimonio", response_model=PatrimonioDetalhe)
def detalhe_patrimonio(
    cod_ibge: str,
    ano: int | None = _ANO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> PatrimonioDetalhe:
    """Cabeçalho patrimonial: Ativo/Passivo/PL, VPD/VPA e estado das fontes (Padrão de Detalhe)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_detalhe(session, cod_ibge, ano, as_of=as_of)


@router.get("/entes/{cod_ibge}/msc/arvore", response_model=DrillEnvelope)
def arvore_msc(
    cod_ibge: str,
    node: str | None = _NODE_Q,
    periodo: str | None = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> DrillEnvelope:
    """Explorador MSC: filhos do nó com saldo *rolled-up*, drill **lazy** (§6.1)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_arvore(session, cod_ibge, node, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/msc/conta/{codigo}/saldos", response_model=MatrizMensalOut)
def saldos_conta_msc(
    cod_ibge: str,
    codigo: str,
    ano: int | None = _ANO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> MatrizMensalOut:
    """Matriz mensal (12 meses) dos saldos de uma conta PCASP ao longo do exercício."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_matriz(session, cod_ibge, codigo, ano, as_of=as_of)


@router.get("/entes/{cod_ibge}/balancos", response_model=None)
def balancos(
    cod_ibge: str,
    ano: int | None = _ANO_Q,
    tipo: str | None = Query(
        None,
        description=(
            "Balanço específico (patrimonial/variacoes/orcamentario_receita/"
            "orcamentario_despesa). Vazio = índice dos balanços disponíveis."
        ),
    ),
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> Any:
    """Balanços da DCA. Sem ``tipo`` retorna o índice; com ``tipo`` retorna o demonstrativo."""
    assert_ente_in_scope(session, principal, cod_ibge)
    if tipo:
        return service.build_balanco(session, cod_ibge, ano, tipo, as_of=as_of)
    return service.build_balancos_index(session, cod_ibge, ano, as_of=as_of)


@router.get("/entes/{cod_ibge}/msc/conciliacao", response_model=ConciliacaoOut)
def conciliacao_msc(
    cod_ibge: str,
    ano: int | None = _ANO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> ConciliacaoOut:
    """Conciliação: rollup (pai = Σ filhos), identidade do Balanço e cruzamento MSC↔DCA."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_conciliacao(session, cod_ibge, ano, as_of=as_of)


# Compat: também expõe o índice de tipos disponíveis (útil para a navegação da tela).
@router.get("/entes/{cod_ibge}/balancos/tipos", response_model=BalancosIndex)
def balancos_tipos(
    cod_ibge: str,
    ano: int | None = _ANO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> BalancosIndex:
    """Índice dos balanços disponíveis (patrimonial/variações/orçamentário) para o ente/ano."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_balancos_index(session, cod_ibge, ano, as_of=as_of)
