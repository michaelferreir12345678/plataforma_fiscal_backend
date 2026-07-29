"""Verificação de qualidade agendada (Sprint 26).

Os checks já rodam ao fim de cada carga (Sprint 24). Mas dois problemas não aparecem numa
carga:

1. **A fonte que parou de chegar.** Se ninguém carrega nada, nenhum job roda — e é
   exatamente aí que o dado envelhece sem que ninguém veja. A verificação agendada existe
   para que o silêncio da ingestão vire alerta.
2. **A divergência que nasce depois.** ``mart_indicador`` alimenta o semáforo e a
   carteira; o detalhe alimenta a página. Uma delas pode ser rematerializada sozinha e as
   duas passarem a contar histórias diferentes. A reconciliação agendada é o que impede
   isso de ficar silencioso.

Também monitora o **próprio agendamento**: se o ciclo anterior deveria ter rodado e não
rodou, isso vira check em falha — um monitor que morre calado é pior que nenhum monitor.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import admin_session
from app.modules.ingestion.models import DimEntrega
from app.modules.quality import service as quality_service
from app.modules.tenancy.models import CarteiraEnte, Organizacao

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_SCHEDULER_THREAD: threading.Thread | None = None
_SCHEDULER_STOP = threading.Event()

# Intervalo esperado entre execuções; passar de 2× isto conta como execução perdida.
INTERVALO_PADRAO_SEGUNDOS = 3600.0
_ULTIMA_EXECUCAO: datetime | None = None


def _entes_por_org(session: Session) -> dict[uuid.UUID, list[str]]:
    """Carteira de cada organização — o alerta é da org que acompanha o ente."""
    linhas = session.execute(
        select(CarteiraEnte.org_id, CarteiraEnte.cod_ibge).join(
            Organizacao, Organizacao.id == CarteiraEnte.org_id
        )
    ).all()
    resultado: dict[uuid.UUID, list[str]] = {}
    for org_id, cod in linhas:
        resultado.setdefault(org_id, []).append(str(cod))
    return resultado


def _ultimo_periodo_rreo(session: Session, cod_ibge: str) -> str | None:
    return session.scalar(
        select(DimEntrega.periodo)
        .where(
            DimEntrega.cod_ibge == cod_ibge,
            DimEntrega.relatorio == "RREO",
            DimEntrega.vigente.is_(True),
        )
        .order_by(DimEntrega.periodo.desc())
        .limit(1)
    )


def executar_verificacao(*, agora: datetime | None = None) -> dict[str, int]:
    """Roda os checks de todos os entes acompanhados e emite os alertas devidos."""
    global _ULTIMA_EXECUCAO
    instante = agora or datetime.now(UTC)
    resumo = {"orgs": 0, "entes": 0, "falha": 0, "aviso": 0, "alertas": 0}
    with admin_session() as session:
        _verificar_execucao_perdida(session, instante)
        por_org = _entes_por_org(session)
        for org_id, entes in por_org.items():
            resumo["orgs"] += 1
            for cod in dict.fromkeys(entes):
                periodo = _ultimo_periodo_rreo(session, cod)
                if periodo is None:
                    continue
                try:
                    saida = quality_service.executar_e_alertar(
                        session, cod, periodo, org_id=org_id
                    )
                except Exception:  # noqa: BLE001 — um ente não derruba a varredura
                    logger.exception("Falha ao verificar qualidade de %s", cod)
                    continue
                resumo["entes"] += 1
                resumo["falha"] += saida.falha
                resumo["aviso"] += saida.aviso
                resumo["alertas"] += saida.alertas_emitidos
        session.commit()
    _ULTIMA_EXECUCAO = instante
    return resumo


def _verificar_execucao_perdida(session: Session, instante: datetime) -> None:
    """Registra check em falha quando o ciclo anterior não aconteceu."""
    if _ULTIMA_EXECUCAO is None:
        return
    limite = timedelta(seconds=INTERVALO_PADRAO_SEGUNDOS * 2)
    if instante - _ULTIMA_EXECUCAO <= limite:
        return
    quality_service.registrar_execucao_perdida(
        session,
        fonte="verificacao_agendada",
        esperado_em=_ULTIMA_EXECUCAO + timedelta(seconds=INTERVALO_PADRAO_SEGUNDOS),
        detalhe=(
            f"Última verificação em {_ULTIMA_EXECUCAO.isoformat()}; o ciclo previsto não "
            "ocorreu no intervalo esperado."
        ),
    )


def start_scheduler(interval_seconds: float = INTERVALO_PADRAO_SEGUNDOS) -> None:
    """Relógio da verificação; uma thread por processo, como o de relatórios."""
    global _SCHEDULER_THREAD
    with _LOCK:
        if _SCHEDULER_THREAD is not None and _SCHEDULER_THREAD.is_alive():
            return
        _SCHEDULER_STOP.clear()

        def _loop() -> None:
            while not _SCHEDULER_STOP.wait(interval_seconds):
                try:
                    executar_verificacao()
                except Exception:
                    logger.exception("Falha no ciclo de verificação de qualidade")
                    continue

        _SCHEDULER_THREAD = threading.Thread(
            target=_loop, name="quality-scheduler", daemon=True
        )
        _SCHEDULER_THREAD.start()


def stop_scheduler() -> None:
    global _SCHEDULER_THREAD
    with _LOCK:
        _SCHEDULER_STOP.set()
        if _SCHEDULER_THREAD is not None:
            _SCHEDULER_THREAD.join(timeout=2)
            _SCHEDULER_THREAD = None


def marcar_execucao(instante: datetime | None = None) -> None:
    """Usado pelos testes para simular o relógio da execução anterior."""
    global _ULTIMA_EXECUCAO
    _ULTIMA_EXECUCAO = instante
