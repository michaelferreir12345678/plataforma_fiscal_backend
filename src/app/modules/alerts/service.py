"""Regras da Sprint 15: fila priorizada, calendário, agregação de carteira, patch.

As leituras **disparam a avaliação** do escopo (materialize-on-read, como os demais
módulos), de modo que o frontend sempre vê alertas coerentes com o dado real sem
depender de um worker externo. A avaliação é idempotente (dedup por chave).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError
from app.modules.alerts import engine, repository, rules
from app.modules.alerts.models import Alerta
from app.modules.alerts.schemas import (
    AlertaOut,
    CalendarioItem,
    CalendarioResponse,
    CarteiraAlertasResponse,
    CarteiraCategoriaAgg,
    CarteiraEnteAlertas,
    Contadores,
    FilaAlertasResponse,
)
from app.modules.catalog import repository as catalog_repo
from app.modules.catalog import service as catalog_service
from app.shared.scope import assert_ente_in_scope, carteira_scope_ibges
from app.shared.source_ref import SourceRef

_STATUS_ATIVOS = ("nova", "reconhecida")
_STATUS_VALIDOS = ("nova", "reconhecida", "resolvida", "descartada")
_SEV_ORDEM = {rules.SEV_CRITICO: 0, rules.SEV_ATENCAO: 1, rules.SEV_INFORMATIVO: 2}
# Acima deste tamanho de escopo, a avaliação de carteira omite o preditivo (custo).
_LIMITE_PREDITIVO_CARTEIRA = 8


def _source_ref(valor: dict | None) -> SourceRef | None:
    return SourceRef(**valor) if valor else None


def _to_out(a: Alerta) -> AlertaOut:
    return AlertaOut(
        id=str(a.id),
        cod_ibge=a.cod_ibge,
        categoria=a.categoria,
        severidade=a.severidade,
        prioridade=a.prioridade,
        titulo=a.titulo,
        motivo_legal=a.motivo_legal,
        acao_sugerida=a.acao_sugerida,
        prazo=a.prazo,
        link=a.link,
        status=a.status,
        indicador=a.indicador,
        periodo=a.periodo,
        source_ref=_source_ref(a.source_ref),
        memoria=a.memoria,
        criado_em=a.criado_em,
        atualizado_em=a.atualizado_em,
    )


def _contadores(alertas: list[Alerta]) -> Contadores:
    c = Contadores()
    for a in alertas:
        if a.status == "descartada":
            continue
        if a.severidade == rules.SEV_CRITICO:
            c.critico += 1
        elif a.severidade == rules.SEV_ATENCAO:
            c.atencao += 1
        else:
            c.informativo += 1
        c.total += 1
    return c


def _org(principal: Principal) -> uuid.UUID:
    if principal.org_id is None:
        raise AppError(status=403, title="Sem organização", detail="Requer organização ativa.")
    return principal.org_id


def listar_fila(
    session: Session,
    principal: Principal,
    *,
    escopo: str,
    cod_ibge: str | None,
) -> FilaAlertasResponse:
    """Fila priorizada (crítico → atenção → informativo) do ente ou da carteira."""
    org_id = _org(principal)
    if escopo == "ente":
        if cod_ibge is None:
            raise AppError(status=422, title="Ente ausente", detail="escopo=ente exige ?ente=.")
        assert_ente_in_scope(session, principal, cod_ibge)
        engine.avaliar_ente(session, org_id, cod_ibge, incluir_preditivo=True)
        cods = [cod_ibge]
    else:
        cods = _avaliar_carteira(session, principal, org_id)
    alertas = repository.list_alertas(
        session, org_id=org_id, cods_ibge=cods, incluir_status=_STATUS_ATIVOS
    )
    return FilaAlertasResponse(
        escopo=escopo,
        cod_ibge=cod_ibge if escopo == "ente" else None,
        gerado_em=datetime.now(UTC),
        contadores=_contadores(alertas),
        alertas=[_to_out(a) for a in alertas],
    )


def _avaliar_carteira(session: Session, principal: Principal, org_id: uuid.UUID) -> list[str]:
    cods = sorted(carteira_scope_ibges(session, principal))
    incluir_preditivo = len(cods) <= _LIMITE_PREDITIVO_CARTEIRA
    for cod in cods:
        engine.avaliar_ente(session, org_id, cod, incluir_preditivo=incluir_preditivo)
    return cods


def calendario(session: Session, principal: Principal, cod_ibge: str) -> CalendarioResponse:
    """Calendário de obrigações do ente (periodicidade sensível ao porte)."""
    _org(principal)
    assert_ente_in_scope(session, principal, cod_ibge)
    org_id = _org(principal)
    engine.avaliar_ente(session, org_id, cod_ibge, incluir_preditivo=False)
    ente = catalog_service.refresh_dim_ente(session, cod_ibge)
    esfera = ente.esfera if ente else None
    populacao = ente.populacao if ente else None
    itens = [
        CalendarioItem(
            relatorio=o.relatorio,
            periodo=o.periodo,
            periodicidade=o.periodicidade,
            prazo=o.prazo,
            status=o.status,
            entregue_em=o.entregue_em,
            versao_entrega=o.versao_entrega,
            base_legal=rules.base_legal_calendario(o.relatorio),
            source_ref=_source_ref(o.source_ref),
        )
        for o in repository.list_calendario(session, cod_ibge=cod_ibge)
    ]
    return CalendarioResponse(
        cod_ibge=cod_ibge,
        esfera=esfera,
        populacao=populacao,
        periodicidade_rgf=rules.cadencia_rgf(esfera, populacao),
        gerado_em=datetime.now(UTC),
        itens=itens,
    )


def carteira_alertas(session: Session, principal: Principal) -> CarteiraAlertasResponse:
    """Agregados de alertas no nível carteira (drill UP)."""
    org_id = _org(principal)
    cods = _avaliar_carteira(session, principal, org_id)
    alertas = repository.list_alertas(
        session, org_id=org_id, cods_ibge=cods, incluir_status=_STATUS_ATIVOS
    )
    por_categoria: dict[str, int] = {}
    por_ente: dict[str, list[Alerta]] = {}
    for a in alertas:
        por_categoria[a.categoria] = por_categoria.get(a.categoria, 0) + 1
        por_ente.setdefault(a.cod_ibge, []).append(a)

    nomes = {
        e.cod_ibge: e.nome
        for e in catalog_repo.list_dim_entes(session, list(por_ente.keys()))
    }
    def _pior(lst: list[Alerta]) -> str | None:
        melhor: str | None = None
        melhor_peso = 99
        for a in lst:
            peso = _SEV_ORDEM.get(a.severidade, 9)
            if peso < melhor_peso:
                melhor_peso, melhor = peso, a.severidade
        return melhor

    ente_rows = [
        CarteiraEnteAlertas(
            cod_ibge=cod,
            nome=nomes.get(cod),
            contadores=_contadores(lst),
            pior_severidade=_pior(lst),
        )
        for cod, lst in por_ente.items()
    ]
    ente_rows.sort(key=lambda r: (_SEV_ORDEM.get(r.pior_severidade or "", 9), -r.contadores.total))

    top = sorted(alertas, key=lambda a: (a.prioridade, a.prazo or datetime.max.date()))[:10]
    return CarteiraAlertasResponse(
        n_entes=len(cods),
        gerado_em=datetime.now(UTC),
        contadores=_contadores(alertas),
        por_categoria=[
            CarteiraCategoriaAgg(categoria=k, total=v)
            for k, v in sorted(por_categoria.items(), key=lambda kv: -kv[1])
        ],
        por_ente=ente_rows,
        top_alertas=[_to_out(a) for a in top],
    )


def atualizar_status(
    session: Session, principal: Principal, alerta_id: uuid.UUID, status: str
) -> AlertaOut:
    """PATCH do status do alerta (reconhecer/resolver/descartar). RLS por org."""
    org_id = _org(principal)
    if status not in _STATUS_VALIDOS:
        raise AppError(
            status=422, title="Status inválido", detail=f"Use um de {_STATUS_VALIDOS}."
        )
    afetados = repository.set_status(
        session, org_id=org_id, alerta_id=alerta_id, status=status, agora=datetime.now(UTC)
    )
    if afetados == 0:
        raise AppError(status=404, title="Alerta não encontrado", detail=str(alerta_id))
    alerta = repository.get_alerta(session, org_id=org_id, alerta_id=alerta_id)
    assert alerta is not None
    return _to_out(alerta)
