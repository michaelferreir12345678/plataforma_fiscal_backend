"""Endpoints de Relatórios & Exportação (Módulo 16)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.core.errors import AppError
from app.modules.reports import repository, service
from app.modules.reports.schemas import (
    AgendamentoCreate,
    AgendamentoOut,
    ModelosResponse,
    RelatorioCreate,
    RelatorioDetalheOut,
    RelatorioListaOut,
    RelatorioSolicitacaoOut,
)
from app.workers import report_tasks

router = APIRouter(prefix="/relatorios", tags=["reports"])


@router.get("/modelos", response_model=ModelosResponse)
def modelos(
    _: Principal = Depends(require_capability("ver")),
) -> ModelosResponse:
    """Modelos versionados por público, sem conteúdo fiscal fictício."""
    return service.modelos()


@router.get("", response_model=RelatorioListaOut)
def listar(
    limit: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> RelatorioListaOut:
    """Histórico real do tenant para a tela de relatórios."""
    return service.listar(session, principal, limit=limit)


@router.post("", response_model=RelatorioSolicitacaoOut, status_code=202)
def criar(
    body: RelatorioCreate,
    principal: Principal = Depends(require_capability("gerar_relatorio")),
    session: Session = Depends(get_db),
) -> RelatorioSolicitacaoOut:
    """Persiste e enfileira um artefato por ente, inclusive no modo lote/estadual."""
    response = service.solicitar(session, principal, body)
    report_tasks.enqueue_reports([item.id for item in response.relatorios])
    return response


@router.post("/agendamentos", response_model=AgendamentoOut, status_code=201)
def agendar(
    body: AgendamentoCreate,
    principal: Principal = Depends(require_capability("gerar_relatorio")),
    session: Session = Depends(get_db),
) -> AgendamentoOut:
    """Registra recorrência; o scheduler chama ``executar_agendamentos`` quando vencer."""
    return service.agendar(session, principal, body)


@router.get("/{relatorio_id}", response_model=RelatorioDetalheOut)
def obter(
    relatorio_id: uuid.UUID,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> RelatorioDetalheOut:
    """Status, lote, arquivo, fontes, memória e incompletudes de um relatório."""
    return service.obter(session, principal, relatorio_id)


@router.get("/{relatorio_id}/arquivo", response_class=FileResponse)
def arquivo(
    relatorio_id: uuid.UUID,
    principal: Principal = Depends(require_capability("exportar")),
    session: Session = Depends(get_db),
) -> FileResponse:
    """Download autenticado do PDF/XLSX/PPTX; a requisição também entra no audit_log."""
    row = repository.get_relatorio(session, relatorio_id)
    if row is None:
        # Mantém 404 sem revelar existência em outro tenant (RLS).
        raise AppError(status=404, title="Relatório não encontrado")
    path = service.resolve_artifact(row)
    service.audit_export(
        session,
        row,
        acao="BAIXAR_RELATORIO",
        usuario_id=principal.usuario_id,
    )
    return FileResponse(
        path=path,
        media_type=row.mime_type or "application/octet-stream",
        filename=row.arquivo_nome or path.name,
    )
