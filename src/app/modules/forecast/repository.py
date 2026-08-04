"""Acesso a dados da Sprint 14: gold.fato_projecao (upsert) e op.cenario (CRUD)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.forecast.models import Cenario, CenarioVersao, FatoProjecao

_PROJECAO_UPDATE = (
    "horizonte",
    "valor_previsto",
    "ic_inferior",
    "ic_superior",
    "nivel_confianca",
    "unidade",
    "teto_pct",
    "faixa",
    "cruza_limite",
    "gerado_em",
    "source_ref",
    "memoria",
)


def upsert_projecao(session: Session, valores: dict[str, Any]) -> None:
    """Materializa uma projeção (idempotente por cod_ibge/indicador/modelo/periodo_alvo)."""
    stmt = pg_insert(FatoProjecao).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cod_ibge", "indicador", "modelo", "periodo_alvo"],
        set_={k: valores[k] for k in _PROJECAO_UPDATE if k in valores},
    )
    session.execute(stmt)


def list_projecoes(
    session: Session, *, cod_ibge: str, indicador: str, modelo: str
) -> list[FatoProjecao]:
    return list(
        session.scalars(
            select(FatoProjecao).where(
                FatoProjecao.cod_ibge == cod_ibge,
                FatoProjecao.indicador == indicador,
                FatoProjecao.modelo == modelo,
            )
        )
    )


# --- op.cenario ---
def criar_cenario(session: Session, valores: dict[str, Any]) -> Cenario:
    cenario = Cenario(**valores)
    session.add(cenario)
    session.flush()
    return cenario


def get_cenario(session: Session, *, org_id: uuid.UUID, cenario_id: uuid.UUID) -> Cenario | None:
    return session.scalar(
        select(Cenario).where(Cenario.id == cenario_id, Cenario.org_id == org_id)
    )


def list_cenarios(
    session: Session,
    *,
    org_id: uuid.UUID,
    ente: str | None = None,
    incluir_arquivados: bool = False,
) -> list[Cenario]:
    stmt = select(Cenario).where(Cenario.org_id == org_id)
    if ente is not None:
        stmt = stmt.where(Cenario.ente == ente)
    if not incluir_arquivados:
        stmt = stmt.where(Cenario.arquivado_em.is_(None))
    # Ordena pela última alteração: quem tem muitos cenários procura o que mexeu por
    # último, não o que criou primeiro.
    return list(
        session.scalars(
            stmt.order_by(
                func.coalesce(Cenario.atualizado_em, Cenario.criado_em).desc()
            )
        )
    )


# --- op.cenario_versao ---------------------------------------------------------------- #
def proxima_versao(session: Session, *, cenario_id: uuid.UUID) -> int:
    """Próximo número de versão do cenário.

    Calculado no banco, sob a mesma transação do insert, porque a unicidade
    ``(cenario_id, versao)`` é garantida por constraint: dois salvamentos simultâneos
    disputam o número, e quem perder recebe erro em vez de sobrescrever silenciosamente.
    """
    atual = session.scalar(
        select(func.max(CenarioVersao.versao)).where(CenarioVersao.cenario_id == cenario_id)
    )
    return int(atual or 0) + 1


def criar_versao(session: Session, valores: dict[str, Any]) -> CenarioVersao:
    versao = CenarioVersao(**valores)
    session.add(versao)
    session.flush()
    return versao


def list_versoes(
    session: Session, *, org_id: uuid.UUID, cenario_id: uuid.UUID
) -> list[CenarioVersao]:
    """Histórico do cenário, da mais recente para a mais antiga."""
    return list(
        session.scalars(
            select(CenarioVersao)
            .where(
                CenarioVersao.cenario_id == cenario_id,
                CenarioVersao.org_id == org_id,
            )
            .order_by(CenarioVersao.versao.desc())
        )
    )


def get_versao(
    session: Session, *, org_id: uuid.UUID, cenario_id: uuid.UUID, versao: int | None = None
) -> CenarioVersao | None:
    """Uma versão específica; sem ``versao``, a mais recente."""
    stmt = select(CenarioVersao).where(
        CenarioVersao.cenario_id == cenario_id, CenarioVersao.org_id == org_id
    )
    if versao is not None:
        stmt = stmt.where(CenarioVersao.versao == versao)
    return session.scalar(stmt.order_by(CenarioVersao.versao.desc()).limit(1))


def get_versoes_por_ids(
    session: Session, *, org_id: uuid.UUID, cenario_ids: list[uuid.UUID]
) -> dict[uuid.UUID, CenarioVersao]:
    """Última versão de cada cenário pedido — para a comparação lado a lado.

    Devolve dicionário, não lista: quem compara precisa saber **qual** cenário faltou, e
    uma lista com menos itens que a entrada não diz qual.
    """
    if not cenario_ids:
        return {}
    linhas = session.scalars(
        select(CenarioVersao)
        .where(
            CenarioVersao.cenario_id.in_(cenario_ids),
            CenarioVersao.org_id == org_id,
        )
        .order_by(CenarioVersao.cenario_id, CenarioVersao.versao.desc())
    ).all()
    ultimas: dict[uuid.UUID, CenarioVersao] = {}
    for linha in linhas:
        ultimas.setdefault(linha.cenario_id, linha)
    return ultimas
