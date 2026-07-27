"""Acesso a dados do medallion. Implementa o ``MedallionSink`` e as leituras bitemporais."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.ingestion.models import (
    CatalogoFonte,
    DimEntrega,
    IngestionLog,
    Integracao,
    MartCoberturaFonte,
    RawPayload,
    SilverExtrato,
)
from app.shared.ingestion.base import EntregaInfo, IngestionJob


class MedallionRepository:
    """Implementação concreta do ``MedallionSink`` (bronze + gold.dim_entrega + log)."""

    def upsert_bronze(
        self, session: Session, job: IngestionJob, payload: Any, hash_: str
    ) -> bool:
        """Grava o payload cru (idempotente por chave). Retorna ``True`` se inseriu."""
        stmt = (
            pg_insert(RawPayload)
            .values(
                fonte=job.fonte,
                cod_ibge=job.cod_ibge,
                periodo=job.periodo,
                versao=job.versao,
                ano=job.ano,
                hash_payload=hash_,
                payload=payload,
            )
            .on_conflict_do_nothing(index_elements=["fonte", "cod_ibge", "periodo", "versao"])
        )
        result = session.execute(stmt)
        return bool(result.rowcount == 1)

    def register_entrega(
        self, session: Session, job: IngestionJob, hash_: str
    ) -> EntregaInfo:
        """Registra a entrega em gold.dim_entrega. Retificação mais recente supera a vigente."""
        # Duas entregas da mesma chave podem ser processadas por workers diferentes.
        # Serializamos a decisão bitemporal durante toda a transação; sem essa trava,
        # ambos poderiam observar o mesmo máximo e gravar ``vigente=true``.
        lock_key = (
            f"gold.dim_entrega:{job.cod_ibge}\x1f{job.relatorio}\x1f{job.periodo}"
        )
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )
        chave = (
            DimEntrega.cod_ibge == job.cod_ibge,
            DimEntrega.relatorio == job.relatorio,
            DimEntrega.periodo == job.periodo,
        )
        existing = session.scalar(
            select(DimEntrega).where(*chave, DimEntrega.versao_entrega == job.versao)
        )
        if existing is not None:
            if existing.hash_payload is None:
                existing.hash_payload = hash_
            return EntregaInfo(existing.versao_entrega, existing.vigente)

        homologada = job.homologada_em or datetime.now(UTC)
        max_homologada = session.scalar(select(func.max(DimEntrega.homologada_em)).where(*chave))
        # Timezone-safe: compara em UTC.
        becomes_vigente = max_homologada is None or homologada >= max_homologada
        if becomes_vigente:
            session.execute(update(DimEntrega).where(*chave).values(vigente=False))

        entrega = DimEntrega(
            cod_ibge=job.cod_ibge,
            relatorio=job.relatorio,
            periodo=job.periodo,
            versao_entrega=job.versao,
            homologada_em=homologada,
            vigente=becomes_vigente,
            hash_payload=hash_,
        )
        session.add(entrega)
        session.flush()
        return EntregaInfo(job.versao, becomes_vigente)

    def log_ingestion(
        self, session: Session, job: IngestionJob, status: str, mensagem: str | None = None
    ) -> None:
        execution_job_id = session.info.get("ingest_job_id")
        session.add(
            IngestionLog(
                job_id=execution_job_id,
                fonte=job.fonte,
                cod_ibge=job.cod_ibge,
                periodo=job.periodo,
                versao=job.versao,
                status=status,
                mensagem=mensagem,
            )
        )
        session.flush()


# --- Leituras bitemporais (as_of) e materialização silver ---
def resolve_versao(
    session: Session,
    *,
    cod_ibge: str,
    relatorio: str,
    periodo: str,
    as_of: datetime | None = None,
) -> str | None:
    """Resolve a ``versao_entrega`` efetiva (§6.5).

    Sem ``as_of``, retorna a versão **vigente**; com ``as_of``, a que estava vigente
    naquele instante (maior ``homologada_em`` ≤ ``as_of``).
    """
    chave = (
        DimEntrega.cod_ibge == cod_ibge,
        DimEntrega.relatorio == relatorio,
        DimEntrega.periodo == periodo,
    )
    if as_of is None:
        return session.scalar(
            select(DimEntrega.versao_entrega).where(*chave, DimEntrega.vigente.is_(True))
        )
    return session.scalar(
        select(DimEntrega.versao_entrega)
        .where(*chave, DimEntrega.homologada_em <= as_of)
        .order_by(DimEntrega.homologada_em.desc())
        .limit(1)
    )


def entrega_homologada_em(
    session: Session,
    *,
    cod_ibge: str,
    relatorio: str,
    periodo: str,
    versao_entrega: str,
) -> datetime | None:
    """Instante efetivo da entrega usada — ancora o ``as_of`` quando a query o omite."""
    return session.scalar(
        select(DimEntrega.homologada_em).where(
            DimEntrega.cod_ibge == cod_ibge,
            DimEntrega.relatorio == relatorio,
            DimEntrega.periodo == periodo,
            DimEntrega.versao_entrega == versao_entrega,
        )
    )


def resolve_latest_entrega(
    session: Session,
    *,
    cod_ibge: str,
    relatorio: str,
    as_of: datetime | None = None,
) -> DimEntrega | None:
    """Entrega mais recente de uma fonte de fotografia (ex.: SADIPEM)."""
    stmt = select(DimEntrega).where(
        DimEntrega.cod_ibge == cod_ibge,
        DimEntrega.relatorio == relatorio,
    )
    if as_of is None:
        stmt = stmt.where(DimEntrega.vigente.is_(True))
    else:
        stmt = stmt.where(DimEntrega.homologada_em <= as_of)
    return session.scalar(
        stmt.order_by(DimEntrega.periodo.desc(), DimEntrega.homologada_em.desc()).limit(1)
    )


def replace_silver_rows(
    session: Session,
    model: Any,
    *,
    keys: dict[str, Any],
    rows: list[dict[str, Any]],
) -> int:
    """Substitui as linhas silver que casam com ``keys`` (idempotente por reprocesso).

    ``keys`` são as colunas que identificam a versão a substituir (ex.:
    ``{"cod_ibge": ..., "periodo": ..., "versao_entrega": ...}`` para SICONFI, ou
    ``{"codigo_serie": ..., "versao_entrega": ...}`` para o BCB). Retorna a contagem.
    """
    session.execute(
        delete(model).where(
            *[getattr(model, col) == value for col, value in keys.items()]
        )
    )
    if rows:
        has_id = "id" in model.__table__.columns
        payload = [
            {"id": uuid.uuid4(), **row} if has_id and "id" not in row else row for row in rows
        ]
        session.execute(insert(model), payload)
    return len(rows)


def read_silver(
    session: Session,
    model: Any,
    *,
    cod_ibge: str,
    periodo: str,
    versao_entrega: str,
) -> list[Any]:
    return list(
        session.scalars(
            select(model).where(
                model.cod_ibge == cod_ibge,
                model.periodo == periodo,
                model.versao_entrega == versao_entrega,
            )
        )
    )


def list_bronze(
    session: Session, *, fonte: str, cod_ibge: str, periodo: str
) -> list[RawPayload]:
    """Payloads crus de (fonte, ente, período) — todas as versões (para replay)."""
    return list(
        session.scalars(
            select(RawPayload)
            .where(
                RawPayload.fonte == fonte,
                RawPayload.cod_ibge == cod_ibge,
                RawPayload.periodo == periodo,
            )
            .order_by(RawPayload.versao)
        )
    )


def entrega_existe(
    session: Session, *, cod_ibge: str, relatorio: str, periodo: str, versao_entrega: str
) -> bool:
    """Já há entrega registrada para esta versão exata? (evita reprocessar o mesmo evento)."""
    return (
        session.scalar(
            select(DimEntrega.id).where(
                DimEntrega.cod_ibge == cod_ibge,
                DimEntrega.relatorio == relatorio,
                DimEntrega.periodo == periodo,
                DimEntrega.versao_entrega == versao_entrega,
            )
        )
        is not None
    )


def contar_entregas(
    session: Session, *, cod_ibge: str, relatorio: str, periodo: str
) -> int:
    """Nº de entregas registradas para (ente, relatório, período).

    Mais de uma significa **retificação**: o cockpit reporta ``n - 1`` retificações na
    camada de qualidade, para que o gestor saiba que aquele número já foi revisado.
    """
    return int(
        session.scalar(
            select(func.count())
            .select_from(DimEntrega)
            .where(
                DimEntrega.cod_ibge == cod_ibge,
                DimEntrega.relatorio == relatorio,
                DimEntrega.periodo == periodo,
            )
        )
        or 0
    )


def list_extratos(session: Session, *, cod_ibge: str) -> list[SilverExtrato]:
    """Eventos de entrega do ente (silver.siconfi_extratos), do mais antigo ao mais novo."""
    return list(
        session.scalars(
            select(SilverExtrato)
            .where(SilverExtrato.cod_ibge == cod_ibge)
            .order_by(SilverExtrato.homologada_em)
        )
    )


def list_entregas(
    session: Session, *, relatorio: str | None = None
) -> list[DimEntrega]:
    stmt = select(DimEntrega)
    if relatorio is not None:
        stmt = stmt.where(DimEntrega.relatorio == relatorio)
    stmt = stmt.order_by(
        DimEntrega.cod_ibge, DimEntrega.relatorio, DimEntrega.periodo, DimEntrega.homologada_em
    )
    return list(session.scalars(stmt))


# --- op.integracao (config global das fontes; Sprint 18) ---
def list_integracoes(session: Session) -> list[Integracao]:
    return list(
        session.scalars(select(Integracao).order_by(Integracao.categoria, Integracao.codigo))
    )


def get_integracao(session: Session, *, codigo: str) -> Integracao | None:
    return session.scalar(select(Integracao).where(Integracao.codigo == codigo))


def count_integracoes(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(Integracao)) or 0)


def insert_integracao(
    session: Session,
    *,
    codigo: str,
    nome: str,
    descricao: str | None,
    categoria: str,
    fontes: list[str],
) -> Integracao:
    row = Integracao(
        codigo=codigo, nome=nome, descricao=descricao, categoria=categoria, fontes=fontes
    )
    session.add(row)
    session.flush()
    return row


def set_integracao_ativo(
    session: Session,
    *,
    codigo: str,
    ativo: bool,
    atualizado_por: uuid.UUID | None,
) -> Integracao | None:
    row = get_integracao(session, codigo=codigo)
    if row is None:
        return None
    row.ativo = ativo
    row.atualizado_por = atualizado_por
    row.atualizado_em = datetime.now(UTC)
    session.flush()
    return row


# --- Catálogo, cobertura e observabilidade por fonte ---
def list_catalogo_fontes(session: Session) -> list[CatalogoFonte]:
    """Metadados persistidos que alimentam o catálogo público de fontes."""
    return list(session.scalars(select(CatalogoFonte).order_by(CatalogoFonte.fonte)))


def query_cobertura(
    session: Session,
    *,
    fonte: str | None = None,
    uf: str | None = None,
    ano: int | None = None,
    page: int = 1,
    page_size: int = 100,
) -> tuple[list[MartCoberturaFonte], int]:
    """Página grupos fonte×ente×ano e devolve todos os períodos de cada grupo.

    ``page_size`` limita grupos, não linhas de período. Isso evita que uma página
    corte o histórico anual de um ente e a camada de apresentação conclua,
    incorretamente, que os períodos que ficaram na página seguinte estão ausentes.
    """
    filtros = []
    if fonte:
        filtros.append(MartCoberturaFonte.fonte == fonte)
    if uf:
        filtros.append(MartCoberturaFonte.uf == uf)
    if ano:
        filtros.append(MartCoberturaFonte.ano == ano)

    grupos = select(
        MartCoberturaFonte.fonte.label("fonte"),
        MartCoberturaFonte.cod_ibge.label("cod_ibge"),
        MartCoberturaFonte.ano.label("ano"),
    )
    if filtros:
        grupos = grupos.where(*filtros)
    grupos = grupos.group_by(
        MartCoberturaFonte.fonte,
        MartCoberturaFonte.cod_ibge,
        MartCoberturaFonte.ano,
    )
    total = int(
        session.scalar(select(func.count()).select_from(grupos.order_by(None).subquery())) or 0
    )
    pagina_grupos = (
        grupos.order_by(
            MartCoberturaFonte.fonte,
            MartCoberturaFonte.cod_ibge,
            MartCoberturaFonte.ano,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .subquery()
    )
    rows = list(
        session.scalars(
            select(MartCoberturaFonte)
            .join(
                pagina_grupos,
                and_(
                    MartCoberturaFonte.fonte == pagina_grupos.c.fonte,
                    MartCoberturaFonte.cod_ibge == pagina_grupos.c.cod_ibge,
                    MartCoberturaFonte.ano == pagina_grupos.c.ano,
                ),
            )
            .order_by(
                MartCoberturaFonte.fonte,
                MartCoberturaFonte.cod_ibge,
                MartCoberturaFonte.ano,
                MartCoberturaFonte.periodo,
            )
        )
    )
    return rows, total


def cobertura_resumo(
    session: Session, *, fonte: str | None = None, uf: str | None = None, ano: int | None = None
) -> tuple[int, int, int, list[str]]:
    """(total_linhas, entes distintos, períodos distintos, fontes distintas) do filtro."""
    filtros = []
    if fonte:
        filtros.append(MartCoberturaFonte.fonte == fonte)
    if uf:
        filtros.append(MartCoberturaFonte.uf == uf)
    if ano:
        filtros.append(MartCoberturaFonte.ano == ano)
    stmt = select(
        func.count(),
        func.count(func.distinct(MartCoberturaFonte.cod_ibge)),
        func.count(func.distinct(MartCoberturaFonte.periodo)),
    )
    if filtros:
        stmt = stmt.where(*filtros)
    total, entes, periodos = session.execute(stmt).one()
    fontes_stmt = select(func.distinct(MartCoberturaFonte.fonte))
    if filtros:
        fontes_stmt = fontes_stmt.where(*filtros)
    fontes = sorted(str(f) for f in session.scalars(fontes_stmt))
    return int(total or 0), int(entes or 0), int(periodos or 0), fontes


def last_run_por_fonte(session: Session) -> dict[str, dict[str, Any]]:
    """Última execução e última execução OK por fonte (de gold.ingestion_log)."""
    ult = session.execute(
        select(IngestionLog.fonte, func.max(IngestionLog.ts)).group_by(IngestionLog.fonte)
    ).all()
    ok = session.execute(
        select(IngestionLog.fonte, func.max(IngestionLog.ts))
        .where(IngestionLog.status == "ingested")
        .group_by(IngestionLog.fonte)
    ).all()
    ok_map: dict[str, Any] = {row[0]: row[1] for row in ok}
    return {row[0]: {"ultima": row[1], "ultima_ok": ok_map.get(row[0])} for row in ult}


def cobertura_por_fonte(session: Session) -> dict[str, dict[str, Any]]:
    """Período, defasagem, entes e registros materializados por fonte."""
    rows = session.execute(
        select(
            MartCoberturaFonte.fonte,
            func.max(MartCoberturaFonte.periodo),
            func.min(MartCoberturaFonte.defasagem_periodos),
            func.count(func.distinct(MartCoberturaFonte.cod_ibge)),
            func.sum(MartCoberturaFonte.n_registros),
        ).group_by(MartCoberturaFonte.fonte)
    ).all()
    return {
        f: {
            "periodo_recente": p,
            "defasagem": d,
            "entes": int(n_entes or 0),
            "registros": int(n_registros or 0),
        }
        for f, p, d, n_entes, n_registros in rows
    }
