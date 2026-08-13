"""Regras da credencial MCP: emitir, listar, revogar e **autenticar** (Sprint IA-3).

O que este módulo decide: se a credencial apresentada é válida e **quem** ela é. O que ele
deliberadamente não decide: o que essa identidade pode ler. Escopo, licença e capacidade
continuam sendo resolvidos dentro da ferramenta (``shared/tooling/envelope.py`` →
``shared/scope.py``), exatamente como para um usuário de navegador.

Essa divisão é o ponto inteiro da Sprint: se autenticar e autorizar morassem os dois aqui,
haveria duas implementações da regra de escopo na plataforma — a do HTTP e a do MCP — e
elas divergiriam no primeiro mês (achado A22 da Sprint E1).
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.db import admin_session
from app.core.deps import Principal
from app.core.errors import AppError
from app.core.security import hash_password, verify_password
from app.modules.mcp import repository
from app.modules.mcp.models import McpCredencial
from app.modules.tenancy import repository as tenancy_repo

#: Prefixo humano do token — identifica o que vazou num log de proxy sem precisar decodificar.
PREFIXO_TOKEN = "mcp"
TIPO_CREDENCIAL_INVALIDA = "urn:plataforma-fiscal:error:mcp-credential-invalid"


class CredencialInvalidaError(AppError):
    """401 do servidor MCP — deliberadamente **sem** distinguir a causa.

    Ao contrário dos dois 403 de escopo/licença, cujas causas diferentes pedem ações
    diferentes de quem já está dentro, aqui quem pergunta ainda não provou ser ninguém.
    Dizer "esta credencial existe mas expirou" confirma a existência do prefixo a quem o
    adivinhou — e transforma a mensagem de erro num oráculo de enumeração.
    """

    def __init__(self, detalhe: str = "Credencial MCP ausente, inválida ou revogada.") -> None:
        super().__init__(
            status=401,
            title="Credencial MCP inválida",
            detail=detalhe,
            type_=TIPO_CREDENCIAL_INVALIDA,
        )


@dataclass(frozen=True)
class CredencialEmitida:
    """O que a emissão devolve. ``token`` aparece **uma vez** e não é recuperável."""

    credencial: McpCredencial
    token: str


@dataclass(frozen=True)
class Autenticacao:
    """Identidade resolvida de um cliente MCP — nada além de identidade."""

    principal: Principal
    credencial_id: uuid.UUID
    org_id: uuid.UUID
    nome: str


def _gerar_token() -> tuple[str, str, str]:
    """(token, prefixo, segredo). O prefixo é público; o segredo nunca é persistido."""
    prefixo = secrets.token_hex(6)
    segredo = secrets.token_urlsafe(32)
    return f"{PREFIXO_TOKEN}_{prefixo}_{segredo}", prefixo, segredo


def _partes(token: str) -> tuple[str, str] | None:
    """Divide ``mcp_<prefixo>_<segredo>``. O segredo pode conter ``_`` (base64url)."""
    pedacos = (token or "").strip().split("_", 2)
    if len(pedacos) != 3 or pedacos[0] != PREFIXO_TOKEN:
        return None
    prefixo, segredo = pedacos[1], pedacos[2]
    if not prefixo or not segredo:
        return None
    return prefixo, segredo


def emitir(
    session: Session,
    principal: Principal,
    *,
    nome: str,
    papel_id: uuid.UUID,
    escopo_ibges: list[str] | None = None,
    expira_em: datetime | None = None,
) -> CredencialEmitida:
    """Emite uma credencial para um papel **da própria organização**.

    O papel é validado contra a organização do principal, e não apenas lido: sem isso, um
    administrador poderia apontar a credencial para o papel de outro tenant e herdar as
    capacidades de lá. É a única verificação de fronteira deste módulo, e ela existe
    porque ``papel_id`` é entrada do cliente.
    """
    org_id = _org(principal)
    papel = tenancy_repo.get_papel(session, papel_id)
    if papel is None or papel.org_id != org_id:
        raise AppError(
            status=404,
            title="Papel não encontrado",
            detail="O papel informado não existe nesta organização.",
        )
    if principal.usuario_id is None:  # pragma: no cover - principal autenticado sempre tem
        raise CredencialInvalidaError()

    token, prefixo, segredo = _gerar_token()
    credencial = repository.insert_credencial(
        session,
        McpCredencial(
            org_id=org_id,
            nome=nome.strip(),
            prefixo=prefixo,
            segredo_hash=hash_password(segredo),
            papel_id=papel_id,
            criado_por=principal.usuario_id,
            escopo_ibges=list(escopo_ibges) if escopo_ibges else None,
            expira_em=expira_em,
        ),
    )
    tenancy_repo.insert_audit_log(
        session,
        org_id=org_id,
        usuario_id=principal.usuario_id,
        acao="MCP_CREDENCIAL_EMITIDA",
        recurso=f"mcp_credencial:{credencial.id};papel={papel_id}",
    )
    return CredencialEmitida(credencial=credencial, token=token)


def listar(session: Session, principal: Principal) -> list[McpCredencial]:
    return repository.list_credenciais(session, org_id=_org(principal))


def revogar(session: Session, principal: Principal, credencial_id: uuid.UUID) -> McpCredencial:
    """Revoga uma credencial da própria organização. Alheia ⇒ 404, nunca 403 (padrão E1)."""
    org_id = _org(principal)
    credencial = repository.get_credencial(
        session, org_id=org_id, credencial_id=credencial_id
    )
    if credencial is None:
        raise AppError(
            status=404,
            title="Credencial não encontrada",
            detail="Não existe credencial MCP com este identificador nesta organização.",
        )
    if credencial.revogada_em is None:
        repository.revogar(session, credencial, momento=datetime.now(UTC))
        tenancy_repo.insert_audit_log(
            session,
            org_id=org_id,
            usuario_id=principal.usuario_id,
            acao="MCP_CREDENCIAL_REVOGADA",
            recurso=f"mcp_credencial:{credencial.id}",
        )
    return credencial


def autenticar(token: str | None) -> Autenticacao:
    """Resolve a credencial num :class:`Principal`. Qualquer falha ⇒ 401 indistinto.

    Roda no **plano de controle** (``admin_session``), como o login: é preciso encontrar a
    linha antes de saber a organização — e é este passo que fixa a organização que a RLS
    vai impor no resto da requisição. A sessão de controle termina aqui; nada do trabalho
    subsequente acontece dentro dela.
    """
    if not token:
        raise CredencialInvalidaError()
    partes = _partes(token)
    if partes is None:
        raise CredencialInvalidaError()
    prefixo, segredo = partes
    agora = datetime.now(UTC)

    with admin_session() as sessao:
        credencial = repository.get_by_prefixo(sessao, prefixo)
        if credencial is None or not verify_password(segredo, credencial.segredo_hash):
            raise CredencialInvalidaError()
        if not credencial.utilizavel_em(agora):
            raise CredencialInvalidaError()
        if credencial.criado_por is None:
            # ``ON DELETE SET NULL`` no emissor: a credencial perdeu a quem atribuir as
            # chamadas. Sem autor não há G7 — e auditoria que não sabe de quem foi a
            # chamada não é auditoria. Apagar o usuário revoga o que ele emitiu.
            raise CredencialInvalidaError(
                "Credencial sem emissor válido; emita uma nova credencial."
            )
        capacidades = frozenset(
            tenancy_repo.capacidades_do_papel(sessao, credencial.papel_id)
        )
        papel = tenancy_repo.get_papel(sessao, credencial.papel_id)
        escopo = (
            frozenset(str(c) for c in credencial.escopo_ibges)
            if credencial.escopo_ibges
            else None
        )
        dados = Autenticacao(
            principal=Principal(
                usuario_id=credencial.criado_por,
                org_id=credencial.org_id,
                papel=papel.nome if papel else None,
                capacidades=capacidades,
                escopo_ibges=escopo,
                # Uma credencial de máquina jamais é operador da plataforma. O control
                # plane (Sprint 19) não tem porta para fora, e esta não a abre.
                is_superuser=False,
            ),
            credencial_id=credencial.id,
            org_id=credencial.org_id,
            nome=credencial.nome,
        )
        repository.marcar_uso(sessao, credencial_id=credencial.id, momento=agora)
    return dados


def _org(principal: Principal) -> uuid.UUID:
    if principal.org_id is None:
        raise AppError(
            status=403, title="Sem organização", detail="Requer uma organização ativa."
        )
    return principal.org_id
