"""Endpoints da Despesa (Módulo 5) — Padrão de Detalhe + drill por dois eixos (§6.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.expense import service
from app.modules.expense.schemas import (
    DespesaDetalhe,
    EstagiosOut,
    ExecucaoOut,
    MemoriaDespesa,
    RigidezOut,
)
from app.shared.envelope import DrillEnvelope
from app.shared.scope import assert_ente_in_scope

router = APIRouter(tags=["expense"])

Eixo = Literal["funcao", "natureza"]

_PERIODO_Q = Query(..., description="Período RREO (ex.: 2024-B6).")
_AS_OF_Q = Query(None, description="Consulta bitemporal 'as of' (§6.5).")
_EIXO_Q = Query(
    "funcao", description="Eixo de drill: 'funcao' (Anexo 02) ou 'natureza' (Anexo 01)."
)


@router.get("/entes/{cod_ibge}/despesa", response_model=DespesaDetalhe)
def detalhe_despesa(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    eixo: Eixo = _EIXO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> DespesaDetalhe:
    """Cabeçalho + composição (raiz do eixo) + série + comparação com o exercício anterior."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_detalhe(session, cod_ibge, periodo, eixo=eixo, as_of=as_of)


@router.get("/entes/{cod_ibge}/despesa/arvore", response_model=DrillEnvelope)
def arvore_despesa(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    node: str | None = Query(None, description="Código do nó; vazio ⇒ raízes do eixo."),
    eixo: Eixo = _EIXO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> DrillEnvelope:
    """Drill DOWN/UP por função (Função→Subfunção) ou natureza (Categoria→…→Elemento)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_arvore(session, cod_ibge, periodo, node, eixo=eixo, as_of=as_of)


@router.get("/entes/{cod_ibge}/despesa/memoria", response_model=MemoriaDespesa)
def memoria_despesa(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> MemoriaDespesa:
    """Memória de cálculo: estágios, verificação de agregação, invariantes e conciliação."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_memoria(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/despesa/estagios", response_model=EstagiosOut)
def estagios_despesa(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    eixo: Eixo = _EIXO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> EstagiosOut:
    """Cascata dotação→empenhado→liquidado→pago e lacunas (potencial RAP = empenhado−pago)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_estagios(session, cod_ibge, periodo, eixo=eixo, as_of=as_of)


@router.get("/entes/{cod_ibge}/despesa/execucao", response_model=ExecucaoOut)
def execucao_despesa(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> ExecucaoOut:
    """Ritmo da execução (empenho/liquidação/pagamento ÷ dotação) × esperado no período."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_execucao(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/despesa/rigidez", response_model=RigidezOut)
def rigidez_despesa(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> RigidezOut:
    """Peso da despesa rígida (pessoal/juros/amortização) sobre a despesa empenhada."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_rigidez(session, cod_ibge, periodo, as_of=as_of)
