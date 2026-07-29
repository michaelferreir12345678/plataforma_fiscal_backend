"""Engine, sessão e contexto multi-tenant (RLS via GUCs de sessão).

O isolamento por organização é imposto no banco (PostgreSQL Row-Level Security).
A cada transação a aplicação injeta o contexto do principal autenticado em três
parâmetros de configuração locais (``SET LOCAL`` via ``set_config(..., is_local=true)``):

- ``app.current_org_id`` — organização ativa; as policies filtram ``org_id`` por ele.
- ``app.current_user_id`` — usuário autenticado (usado por policies "self").
- ``app.is_admin``       — ``on`` habilita o plano de controle (login/seed/admin),
  que enxerga através dos tenants; ``off`` é o plano de dados (isolado).

Como ``is_local=true``, os valores valem só até o commit/rollback da transação —
não vazam entre conexões do pool.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import MetaData, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

# Chave em ``session.info`` onde guardamos o contexto RLS, para reaplicá-lo a cada
# transação (os GUCs são ``SET LOCAL`` — valem só até o commit/rollback).
_RLS_CTX_KEY = "rls_ctx"


def _json_serializer(obj: Any) -> str:
    """Serializa JSONB tolerando Decimal/date/datetime (bronze guarda payloads variados)."""
    return json.dumps(obj, ensure_ascii=False, default=str)


# Convenção de nomes de constraints — migrations determinísticas/reversíveis.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base declarativa. Tabelas operacionais moram no schema ``op``."""

    metadata = MetaData(schema="op", naming_convention=NAMING_CONVENTION)


# O pool precisa ser dimensionado, não herdado. O padrão do SQLAlchemy — 5 conexões,
# 10 de overflow, 30 s de espera — foi o gargalo que o teste de carga da Sprint 28
# encontrou: 50 usuários simultâneos disputando 15 conexões, cada requisição segurando
# a sua por mais de um segundo, formam fila até estourar os 30 s. O sintoma enganava,
# porque parecia lentidão de consulta e era espera por conexão.
#
# O teto é o `max_connections` do Postgres (100 por padrão): a conta é
# ``(pool_size + max_overflow) × réplicas da API + workers``, e ela tem de caber com
# folga — deixar o banco recusar conexão é pior do que a API enfileirar.
#
# A espera curta é deliberada: pool saturado deve **falhar rápido e visível**, não
# pendurar o usuário por meio minuto num carregamento que ele já abandonou.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout_s,
    pool_recycle=settings.db_pool_recycle_s,
    future=True,
    json_serializer=_json_serializer,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

_mapeamentos_prontos = False


def garantir_mapeamentos() -> None:
    """Registra **todos** os modelos antes da primeira sessão.

    Um processo que importa só o módulo de que precisa deixa FKs entre schemas sem tabela
    referenciada, e o erro (``NoReferencedTableError``) só aparece na primeira query — longe
    do import que faltou. Garantir aqui cobre worker, scheduler, script e teste de uma vez,
    em vez de exigir que cada entrypoint se lembre.

    O import é tardio de propósito: ``models_registry`` depende de ``Base``, definida acima.
    """
    global _mapeamentos_prontos
    if _mapeamentos_prontos:
        return
    _mapeamentos_prontos = True  # antes do import: modelos importam este módulo de volta
    from app.core import models_registry

    models_registry.importar_modelos()


def apply_context(
    session: Session,
    *,
    org_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    is_admin: bool = False,
) -> None:
    """Injeta o contexto multi-tenant na transação corrente da ``session``.

    Guarda o contexto em ``session.info`` para que seja **reaplicado a cada nova
    transação** (workers que commitam por unidade — progresso incremental — não perdem o
    isolamento). Deve ser chamada com uma transação ativa (o SQLAlchemy autoinicia).
    """
    ctx = {
        "org": str(org_id) if org_id else "",
        "user": str(user_id) if user_id else "",
        "admin": "on" if is_admin else "off",
    }
    session.info[_RLS_CTX_KEY] = ctx
    _emit_context(session, ctx)


def _emit_context(bind: Any, ctx: dict[str, str]) -> None:
    bind.execute(text("select set_config('app.current_org_id', :v, true)"), {"v": ctx["org"]})
    bind.execute(text("select set_config('app.current_user_id', :v, true)"), {"v": ctx["user"]})
    bind.execute(text("select set_config('app.is_admin', :v, true)"), {"v": ctx["admin"]})


@event.listens_for(Session, "after_begin")
def _reapply_rls_context(session: Session, transaction: Any, connection: Any) -> None:
    """Reaplica o contexto RLS em cada nova transação (os GUCs são ``SET LOCAL``)."""
    ctx = session.info.get(_RLS_CTX_KEY)
    if ctx is not None:
        _emit_context(connection, ctx)


@contextmanager
def admin_session() -> Iterator[Session]:
    """Sessão do plano de controle (``is_admin=on``): login, seed, gestão de orgs/users."""
    garantir_mapeamentos()
    session = SessionLocal()
    try:
        apply_context(session, is_admin=True)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def tenant_session(
    org_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
) -> Iterator[Session]:
    """Sessão do plano de dados (isolada por ``org_id`` via RLS)."""
    garantir_mapeamentos()
    session = SessionLocal()
    try:
        apply_context(session, org_id=org_id, user_id=user_id, is_admin=False)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
