"""Seed de exemplo (§8 `make seed`): 1 município (prefeitura) + 1 estado (Sefaz).

Idempotente: se o usuário admin de exemplo já existe, não recria nada.

Uso:  python -m scripts.seed
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.core.db import admin_session  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.modules.assistant import norma_seed  # noqa: E402
from app.modules.assistant.embeddings import get_embedder  # noqa: E402
from app.modules.catalog import service as catalog_service  # noqa: E402
from app.modules.ingestion import integracoes  # noqa: E402
from app.modules.limits import service as limits_service  # noqa: E402
from app.modules.tenancy import repository  # noqa: E402
from app.modules.tenancy.models import CAPACIDADES  # noqa: E402

ADMIN_MUNICIPIO_EMAIL = "admin@municipio.gov.br"
ADMIN_ESTADO_EMAIL = "admin@sefaz.gov.br"
GESTOR_ESTADO_EMAIL = "gestor@sefaz.gov.br"
SENHA_PADRAO = "senha1234"  # noqa: S105 — apenas dados de exemplo local


def run() -> None:
    # Dimensões de referência da gold (idempotente): sempre garantidas.
    with admin_session() as s:
        catalog_service.seed_dimensoes(s)
        limits_service.seed_providencias(s)
        gravados = norma_seed.seed_norma_corpus(s, get_embedder())
        integracoes.seed_integracoes(s)
        print("[seed] dimensões (limites + períodos + providências) garantidas")
        print(f"[seed] corpo normativo do assistente indexado ({gravados} dispositivos)")
        print(f"[seed] integrações semeadas ({len(integracoes.FAMILIES)} fontes)")

    with admin_session() as s:
        if repository.get_usuario_by_email(s, ADMIN_MUNICIPIO_EMAIL) is not None:
            print("[seed] tenancy já aplicado — nada a fazer")
            return

        # --- Ente municipal: 1 prefeitura olhando para si (Fortaleza/CE 2304400) ---
        muni = repository.create_org(
            s,
            nome="Prefeitura de Fortaleza (exemplo)",
            tipo_conta="prefeitura",
            metrica_cobranca="por_ente",
        )
        papel_muni = repository.create_papel(s, org_id=muni.id, nome="Administrador")
        repository.set_papel_capacidades(
            s, papel_id=papel_muni.id, capacidades=list(CAPACIDADES)
        )
        repository.add_carteira_ente(
            s, org_id=muni.id, cod_ibge="2304400", grupo="capital", tag="exemplo"
        )
        # São Paulo (3550308) publica MSC no SICONFI (Fortaleza não) — habilita o
        # Explorador MSC / Patrimônio (Sprint 12) com dado mensal real na carteira.
        repository.add_carteira_ente(
            s, org_id=muni.id, cod_ibge="3550308", grupo="capital", tag="exemplo-msc"
        )
        admin_muni = repository.create_usuario(
            s,
            email=ADMIN_MUNICIPIO_EMAIL,
            nome="Administrador do Município",
            senha_hash=hash_password(SENHA_PADRAO),
            mfa_ativo=False,
        )
        repository.create_membership(
            s, org_id=muni.id, usuario_id=admin_muni.id, papel_id=papel_muni.id
        )

        # --- Ente estadual: Sefaz monitorando municípios do território ---
        estado = repository.create_org(
            s,
            nome="Sefaz — Governo do Estado (exemplo)",
            tipo_conta="estado",
            metrica_cobranca="por_populacao",
        )
        papel_estado = repository.create_papel(s, org_id=estado.id, nome="Administrador")
        repository.set_papel_capacidades(
            s, papel_id=papel_estado.id, capacidades=list(CAPACIDADES)
        )
        for cod, grupo in [("23", "uf"), ("2304400", "municipio"), ("2307650", "municipio")]:
            repository.add_carteira_ente(
                s, org_id=estado.id, cod_ibge=cod, grupo=grupo, tag="exemplo"
            )
        admin_estado = repository.create_usuario(
            s,
            email=ADMIN_ESTADO_EMAIL,
            nome="Administrador da Sefaz",
            senha_hash=hash_password(SENHA_PADRAO),
            mfa_ativo=False,
        )
        repository.create_membership(
            s, org_id=estado.id, usuario_id=admin_estado.id, papel_id=papel_estado.id
        )

        # Usuário com escopo restrito (demonstra §6.4): só enxerga Fortaleza.
        papel_gestor = repository.create_papel(s, org_id=estado.id, nome="Gestor Regional")
        repository.set_papel_capacidades(
            s, papel_id=papel_gestor.id, capacidades=["ver", "exportar"]
        )
        gestor = repository.create_usuario(
            s,
            email=GESTOR_ESTADO_EMAIL,
            nome="Gestor Regional",
            senha_hash=hash_password(SENHA_PADRAO),
            mfa_ativo=False,
        )
        m_gestor = repository.create_membership(
            s, org_id=estado.id, usuario_id=gestor.id, papel_id=papel_gestor.id
        )
        repository.set_membership_escopo(
            s, membership_id=m_gestor.id, cods_ibge=["2304400"]
        )

    print("[seed] criado: 1 município (prefeitura) + 1 estado (Sefaz)")
    print(f"[seed] logins (senha '{SENHA_PADRAO}'):")
    print(f"        - {ADMIN_MUNICIPIO_EMAIL}  (admin município, carteira completa)")
    print(f"        - {ADMIN_ESTADO_EMAIL}     (admin estado, carteira completa)")
    print(f"        - {GESTOR_ESTADO_EMAIL}    (estado, escopo restrito a 2304400)")


def main() -> None:
    run()


if __name__ == "__main__":
    main()
