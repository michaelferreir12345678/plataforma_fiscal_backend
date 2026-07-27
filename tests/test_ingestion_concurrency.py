"""Concorrência do controle bitemporal de entregas."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

from sqlalchemy import delete, select

from app.core.db import admin_session
from app.modules.ingestion.models import DimEntrega
from app.modules.ingestion.repository import MedallionRepository
from app.shared.ingestion.base import IngestionJob


def test_retificacoes_concorrentes_mantem_uma_unica_versao_vigente() -> None:
    sufixo = str(int(uuid.uuid4().hex[:5], 16)).zfill(7)[-7:]
    cod_ibge = sufixo
    periodo = f"2099-B{int(uuid.uuid4().hex[0], 16) % 6 + 1}"
    relatorio = "RREO-CONCORRENCIA"
    barrier = Barrier(2)
    repository = MedallionRepository()
    jobs = [
        IngestionJob(
            fonte="siconfi_rreo",
            relatorio=relatorio,
            cod_ibge=cod_ibge,
            ano=2099,
            periodo=periodo,
            versao="v1",
            homologada_em=datetime(2099, 1, 1, tzinfo=UTC),
        ),
        IngestionJob(
            fonte="siconfi_rreo",
            relatorio=relatorio,
            cod_ibge=cod_ibge,
            ano=2099,
            periodo=periodo,
            versao="v2",
            homologada_em=datetime(2099, 2, 1, tzinfo=UTC),
        ),
    ]

    def registrar(job: IngestionJob) -> None:
        with admin_session() as session:
            barrier.wait(timeout=10)
            repository.register_entrega(session, job, f"hash-{job.versao}")

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(registrar, jobs))

        with admin_session() as session:
            entregas = list(
                session.scalars(
                    select(DimEntrega)
                    .where(
                        DimEntrega.cod_ibge == cod_ibge,
                        DimEntrega.relatorio == relatorio,
                        DimEntrega.periodo == periodo,
                    )
                    .order_by(DimEntrega.versao_entrega)
                )
            )

        assert [(row.versao_entrega, row.vigente) for row in entregas] == [
            ("v1", False),
            ("v2", True),
        ]
    finally:
        with admin_session() as session:
            session.execute(
                delete(DimEntrega).where(
                    DimEntrega.cod_ibge == cod_ibge,
                    DimEntrega.relatorio == relatorio,
                    DimEntrega.periodo == periodo,
                )
            )
