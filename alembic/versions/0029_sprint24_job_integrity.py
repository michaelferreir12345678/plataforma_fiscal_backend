"""Sprint 24 — integridade e lineage dos jobs em bancos já migrados.

A primeira versão da 0028 foi aplicada durante o desenvolvimento antes de receber
as constraints finais. Esta revisão incremental garante o mesmo esquema em bancos
existentes e em instalações novas, sem depender de reexecutar uma migration aplicada.

Revision ID: 0029_sprint24_job_integrity
Revises: 0028_sprint24_ingest_jobs
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0029_sprint24_job_integrity"
down_revision: str | None = "0028_sprint24_ingest_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # O ``IF NOT EXISTS`` também cobre ambientes efêmeros que tenham aplicado uma
    # versão intermediária da 0028 durante o desenvolvimento.
    # Logs antigos podem conter IDs de jobs de fixtures já removidas; preservamos a
    # observabilidade e apenas limpamos a referência inválida antes de criar o FK.
    op.execute(
        """
        UPDATE gold.ingestion_log AS log
        SET job_id = NULL
        WHERE log.job_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM op.ingest_job AS job WHERE job.id = log.job_id
          );
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'fk_ingest_job_criado_por'
              AND conrelid = 'op.ingest_job'::regclass
          ) THEN
            ALTER TABLE op.ingest_job
              ADD CONSTRAINT fk_ingest_job_criado_por
              FOREIGN KEY (criado_por) REFERENCES op.usuario(id) ON DELETE SET NULL;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'ck_ingest_job_progresso_pct'
              AND conrelid = 'op.ingest_job'::regclass
          ) THEN
            ALTER TABLE op.ingest_job
              ADD CONSTRAINT ck_ingest_job_progresso_pct
              CHECK (progresso_pct BETWEEN 0 AND 100);
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'ck_ingest_job_contadores'
              AND conrelid = 'op.ingest_job'::regclass
          ) THEN
            ALTER TABLE op.ingest_job
              ADD CONSTRAINT ck_ingest_job_contadores
              CHECK (
                itens_total >= 0 AND itens_ok >= 0
                AND itens_erro >= 0 AND tentativas >= 0
              );
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'ck_ingest_job_itens_consistentes'
              AND conrelid = 'op.ingest_job'::regclass
          ) THEN
            ALTER TABLE op.ingest_job
              ADD CONSTRAINT ck_ingest_job_itens_consistentes
              CHECK (itens_ok + itens_erro <= itens_total);
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'fk_ingestion_log_job'
              AND conrelid = 'gold.ingestion_log'::regclass
          ) THEN
            ALTER TABLE gold.ingestion_log
              ADD CONSTRAINT fk_ingestion_log_job
              FOREIGN KEY (job_id) REFERENCES op.ingest_job(id) ON DELETE SET NULL;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE gold.ingestion_log
          DROP CONSTRAINT IF EXISTS fk_ingestion_log_job;
        ALTER TABLE op.ingest_job
          DROP CONSTRAINT IF EXISTS ck_ingest_job_itens_consistentes;
        ALTER TABLE op.ingest_job
          DROP CONSTRAINT IF EXISTS ck_ingest_job_contadores;
        ALTER TABLE op.ingest_job
          DROP CONSTRAINT IF EXISTS ck_ingest_job_progresso_pct;
        ALTER TABLE op.ingest_job
          DROP CONSTRAINT IF EXISTS fk_ingest_job_criado_por;
        """
    )
