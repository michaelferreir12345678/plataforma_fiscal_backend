"""Processo único do relógio da plataforma (Sprint 28).

Dois agendadores precisam existir e **exatamente uma vez**:

* ``report_tasks`` — emite os relatórios agendados;
* ``quality_tasks`` — roda as verificações de qualidade fora do ciclo de carga, porque
  a fonte que **parou de chegar** não aparece em carga nenhuma (Sprint 26).

Em desenvolvimento eles sobem junto com a API, o que é conveniente com uma réplica. Em
produção seria errado: com quatro réplicas de uvicorn, o mesmo relatório sairia quatro
vezes e o mesmo check rodaria quatro vezes por hora. Por isso o relógio ganha processo
próprio, e o ``main`` só o inicia fora de produção.

Encerramento: ``SIGTERM`` para os agendadores e sai. Nenhum trabalho é abandonado no
meio — o que estava em curso termina, e o que estava agendado é reencontrado no próximo
ciclo, porque agendamento é estado no banco, não na memória.
"""

from __future__ import annotations

import logging
import signal
import threading
from types import FrameType

# Processo isolado: registra os mapeamentos antes da primeira query (ver models_registry).
from app.core import models_registry as _models_registry  # noqa: F401
from app.workers import carteira_tasks, quality_tasks, report_tasks

logger = logging.getLogger(__name__)

_parar = threading.Event()


def _encerrar(signum: int, _frame: FrameType | None) -> None:
    logger.info("scheduler_worker: sinal %s recebido, encerrando", signum)
    _parar.set()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    signal.signal(signal.SIGTERM, _encerrar)
    signal.signal(signal.SIGINT, _encerrar)

    # Retoma o que ficou pendente de um encerramento anterior antes de armar o relógio:
    # subir o agendador com fila suja faria o próximo tique competir com o resgate.
    report_tasks.recover_pending()
    report_tasks.executar_agendamentos()

    # Refresh de carteira enfileirado antes de um restart também é trabalho pendente.
    carteira_tasks.executar_pendentes()

    report_tasks.start_scheduler()
    quality_tasks.start_scheduler()
    carteira_tasks.start_scheduler()
    logger.info("scheduler_worker: relatórios, qualidade e carteira agendados")

    try:
        _parar.wait()
    finally:
        report_tasks.stop_scheduler()
        quality_tasks.stop_scheduler()
        carteira_tasks.stop_scheduler()
        logger.info("scheduler_worker: encerrado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
