"""Regras de negócio do módulo tenancy (§7: regra só no service)."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import admin_session
from app.core.deps import Principal
from app.core.errors import AppError
from app.core.security import create_access_token, hash_password, verify_password
from app.modules.tenancy import repository
from app.modules.tenancy.repository import MembershipView
from app.modules.tenancy.schemas import (
    AssinaturaInput,
    AssinaturaOut,
    AuditoriaItem,
    AuditoriaPage,
    BillingOverview,
    CarteiraEnteCreate,
    CarteiraEnteOut,
    CarteiraLoteInput,
    CarteiraLoteResult,
    EntePadrao,
    FaturaEmitInput,
    FaturaOut,
    FaturaPreview,
    MembershipInfo,
    MeResponse,
    MinhaLicencaOut,
    OrgOut,
    PapelCreate,
    PapelOut,
    TokenResponse,
    UserCreate,
    UserOut,
)
from app.shared.scope import (
    EnteNaoLicenciadoError,
    cobertura_licenca,
    invalidar_escopo_carteira,
)

_COMPETENCIA_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _org_id(principal: Principal) -> uuid.UUID:
    if principal.org_id is None:
        raise AppError(status=400, title="Sem organização", detail="Principal sem org ativa.")
    return principal.org_id


def _to_membership_info(view: MembershipView) -> MembershipInfo:
    return MembershipInfo(
        org_id=view.org_id,
        org_nome=view.org_nome,
        tipo_conta=view.tipo_conta,
        papel=view.papel_nome,
        capacidades=sorted(view.capacidades),
        escopo_ibges=sorted(view.escopo_ibges) if view.escopo_ibges is not None else None,
    )


# --- Autenticação ---
def authenticate(email: str, senha: str) -> TokenResponse:
    """Valida credenciais e emite o JWT com a organização ativa e suas capacidades."""
    with admin_session() as session:
        usuario = repository.get_usuario_by_email(session, email)
        if usuario is None or not verify_password(senha, usuario.senha_hash):
            raise AppError(status=401, title="Não autenticado", detail="E-mail ou senha inválidos.")
        views = repository.membership_views_for_user(session, usuario.id)
        active = views[0] if views else None
        token = create_access_token(
            usuario_id=usuario.id,
            org_id=active.org_id if active else None,
            capacidades=sorted(active.capacidades) if active else [],
        )
    return TokenResponse(access_token=token)


def _ente_padrao(session: Session, org: MembershipInfo | None) -> EntePadrao | None:
    """Ente com que a sessão abre, derivado do **tipo da conta**.

    Conta estadual abre no próprio Governo do Estado (código IBGE de 2 dígitos); as demais,
    no primeiro ente da carteira. A escolha é do backend porque só ele sabe o tipo da conta
    — no frontend isso virava uma variável de ambiente única, e o usuário da Sefaz abria em
    Fortaleza toda vez.
    """
    from app.modules.catalog.models import DimEnte
    from app.modules.tenancy.models import CarteiraEnte

    if org is None:
        return None
    codigos = list(
        session.scalars(
            select(CarteiraEnte.cod_ibge).where(CarteiraEnte.org_id == org.org_id)
        )
    )
    if org.escopo_ibges is not None:
        # Membro restrito a um subconjunto: abrir fora dele daria 403 na primeira tela.
        permitidos = set(org.escopo_ibges)
        codigos = [c for c in codigos if c in permitidos] or sorted(permitidos)
    if not codigos:
        return None
    estaduais = sorted(c for c in codigos if len(c) < 7)
    escolhido = (
        estaduais[0] if org.tipo_conta == "estado" and estaduais else sorted(codigos)[0]
    )
    ente = session.get(DimEnte, escolhido)
    return EntePadrao(cod_ibge=escolhido, nome=(ente.nome if ente else escolhido))


def build_me(usuario_id: uuid.UUID, org_ativa_id: uuid.UUID | None) -> MeResponse:
    """Monta a resposta de ``GET /me`` (usuário + vínculos + org ativa + ente padrão)."""
    with admin_session() as session:
        usuario = repository.get_usuario(session, usuario_id)
        if usuario is None:
            raise AppError(status=404, title="Não encontrado", detail="Usuário inexistente.")
        views = repository.membership_views_for_user(session, usuario_id)
        infos = [_to_membership_info(v) for v in views]
        org_ativa = next((i for i in infos if i.org_id == org_ativa_id), None)
        return MeResponse(
            usuario_id=usuario.id,
            email=usuario.email,
            nome=usuario.nome,
            org_ativa=org_ativa,
            memberships=infos,
            is_superuser=usuario.is_superuser,
            ente_padrao=_ente_padrao(session, org_ativa),
        )


# --- Organização (plano de controle) ---
def list_orgs(session: Session, principal: Principal) -> list[OrgOut]:
    """Retorna somente a organização ativa do administrador autenticado."""
    org = repository.get_org(session, _org_id(principal))
    return [OrgOut.model_validate(org)] if org is not None else []


# --- Usuário (escopo explícito da organização ativa) ---
def _papel_novo_usuario(
    session: Session, *, org_id: uuid.UUID, papel_id: uuid.UUID | None
) -> uuid.UUID:
    if papel_id is not None:
        papel = repository.get_papel(session, papel_id)
        if papel is None or papel.org_id != org_id:
            raise AppError(status=404, title="Papel inexistente", detail=str(papel_id))
        return papel.id

    candidatos: list[tuple[bool, int, str, uuid.UUID]] = []
    for papel in repository.list_papeis(session, org_id):
        capacidades = repository.capacidades_do_papel(session, papel.id)
        candidatos.append(
            (
                "administrar" in capacidades,
                len(capacidades),
                papel.nome.casefold(),
                papel.id,
            )
        )
    if not candidatos:
        raise AppError(
            status=409,
            title="Organização sem papel",
            detail="Cadastre ao menos um papel antes de criar usuários.",
        )
    return min(candidatos)[-1]


def create_user(session: Session, principal: Principal, data: UserCreate) -> UserOut:
    org_id = _org_id(principal)
    if repository.get_usuario_by_email(session, data.email) is not None:
        raise AppError(status=409, title="Conflito", detail="E-mail já cadastrado.")
    papel_id = _papel_novo_usuario(session, org_id=org_id, papel_id=data.papel_id)
    usuario = repository.create_usuario(
        session,
        email=data.email,
        nome=data.nome,
        senha_hash=hash_password(data.senha),
        mfa_ativo=data.mfa_ativo,
    )
    repository.create_membership(
        session,
        org_id=org_id,
        usuario_id=usuario.id,
        papel_id=papel_id,
    )
    papel = repository.get_papel(session, papel_id)
    repository.insert_audit_log(
        session,
        org_id=org_id,
        usuario_id=principal.usuario_id,
        acao="CRIAR_USUARIO",
        recurso=(
            f"usuario:{usuario.id}:{usuario.email};"
            f"papel={papel.nome if papel is not None else papel_id}"
        ),
    )
    return UserOut(
        id=usuario.id,
        email=usuario.email,
        nome=usuario.nome,
        mfa_ativo=usuario.mfa_ativo,
        papel_id=papel_id,
        papel_nome=papel.nome if papel is not None else None,
    )


def list_users(session: Session, principal: Principal) -> list[UserOut]:
    org_id = _org_id(principal)
    return [
        UserOut(
            id=usuario.id,
            email=usuario.email,
            nome=usuario.nome,
            mfa_ativo=usuario.mfa_ativo,
            papel_id=papel_id,
            papel_nome=papel_nome,
        )
        for usuario, papel_id, papel_nome in repository.list_usuarios_org(session, org_id)
    ]


# --- Papel / RBAC (plano de dados, org do principal) ---
def create_papel(
    session: Session, org_id: uuid.UUID, data: PapelCreate, *, actor_user_id: uuid.UUID
) -> PapelOut:
    papel = repository.create_papel(session, org_id=org_id, nome=data.nome)
    repository.set_papel_capacidades(session, papel_id=papel.id, capacidades=list(data.capacidades))
    repository.insert_audit_log(
        session,
        org_id=org_id,
        usuario_id=actor_user_id,
        acao="CRIAR_PAPEL",
        recurso=f"papel:{papel.id}:{papel.nome};capacidades={','.join(sorted(data.capacidades))}",
    )
    return PapelOut(
        id=papel.id,
        org_id=papel.org_id,
        nome=papel.nome,
        capacidades=sorted(data.capacidades),
    )


def list_papeis(session: Session, org_id: uuid.UUID) -> list[PapelOut]:
    result: list[PapelOut] = []
    for papel in repository.list_papeis(session, org_id):
        caps = repository.capacidades_do_papel(session, papel.id)
        result.append(
            PapelOut(
                id=papel.id,
                org_id=papel.org_id,
                nome=papel.nome,
                capacidades=caps,
            )
        )
    return result


def update_papel_capacidades(
    session: Session,
    org_id: uuid.UUID,
    papel_id: uuid.UUID,
    capacidades: list[str],
    *,
    actor_user_id: uuid.UUID,
) -> PapelOut:
    """Substitui as capacidades de um papel da org (aba Permissões). 404 se não for da org."""
    # Todas as decisões "ainda existe um admin?" da organização ficam sob o
    # mesmo lock. Sem isso, duas alterações concorrentes em papéis diferentes
    # poderiam aprovar-se mutuamente e deixar o tenant sem administrador.
    if not repository.lock_org(session, org_id):
        raise AppError(status=404, title="Organização inexistente", detail=str(org_id))
    papel = repository.get_papel(session, papel_id)
    if papel is None or papel.org_id != org_id:
        raise AppError(status=404, title="Papel inexistente", detail=str(papel_id))
    atuais = set(repository.capacidades_do_papel(session, papel_id))
    removendo_admin = "administrar" in atuais and "administrar" not in capacidades
    actor_membership = repository.get_membership(
        session,
        org_id=org_id,
        usuario_id=actor_user_id,
    )
    if removendo_admin and (
        (actor_membership is not None and actor_membership.papel_id == papel_id)
        or repository.count_admin_members_excluding_papel(
            session,
            org_id=org_id,
            papel_id=papel_id,
        )
        == 0
    ):
        raise AppError(
            status=409,
            title="Administrador protegido",
            detail=(
                "Não é possível remover 'administrar' do próprio papel ou do último "
                "papel administrativo da organização."
            ),
            type_="urn:plataforma-fiscal:error:last-admin-protected",
        )
    repository.set_papel_capacidades(session, papel_id=papel_id, capacidades=capacidades)
    repository.insert_audit_log(
        session,
        org_id=org_id,
        usuario_id=actor_user_id,
        acao="ALTERAR_PAPEL_CAPACIDADES",
        recurso=(
            f"papel:{papel_id}:{papel.nome};"
            f"antes={','.join(sorted(atuais))};depois={','.join(sorted(set(capacidades)))}"
        ),
    )
    return PapelOut(
        id=papel.id, org_id=papel.org_id, nome=papel.nome, capacidades=sorted(set(capacidades))
    )


# --- Carteira (plano de dados, org do principal) ---
def _exigir_licenca(session: Session, org_id: uuid.UUID, cod_ibge: str) -> None:
    """A carteira não pode conter o que a licença não cobre (Sprint 19).

    Aceitar o cadastro e depois negar a leitura seria pior: o cliente veria o ente na
    própria lista e levaria um 403 ao abri-lo, sem entender por quê.
    """
    if not cobertura_licenca(session, org_id).cobre(cod_ibge):
        raise EnteNaoLicenciadoError(cod_ibge)


def add_carteira_ente(
    session: Session, org_id: uuid.UUID, data: CarteiraEnteCreate
) -> CarteiraEnteOut:
    _exigir_licenca(session, org_id, data.cod_ibge)
    ente = repository.add_carteira_ente(
        session, org_id=org_id, cod_ibge=data.cod_ibge, grupo=data.grupo, tag=data.tag
    )
    # O escopo memorizado (A27) foi calculado sobre a carteira anterior a esta linha.
    invalidar_escopo_carteira(session, org_id)
    return CarteiraEnteOut.model_validate(ente)


def list_carteira(session: Session, org_id: uuid.UUID) -> list[CarteiraEnteOut]:
    return [CarteiraEnteOut.model_validate(e) for e in repository.list_carteira(session, org_id)]


# --- Carteira em lote (Sprint 18) ---
def carteira_lote(
    session: Session, principal: Principal, data: CarteiraLoteInput
) -> CarteiraLoteResult:
    """Adiciona/remove entes da carteira em lote (idempotente). Registra na auditoria."""
    org_id = _org_id(principal)
    existentes = {e.cod_ibge for e in repository.list_carteira(session, org_id)}
    adicionados: list[str] = []
    ignorados: list[str] = []
    cobertura = cobertura_licenca(session, org_id)
    nao_licenciados: list[str] = []
    for item in data.adicionar:
        if item.cod_ibge in existentes:
            ignorados.append(item.cod_ibge)
            continue
        # Em lote, um ente fora da licença não derruba a operação inteira: entra na
        # lista de recusados, e o administrador vê o que passou e o que não passou.
        if not cobertura.cobre(item.cod_ibge):
            nao_licenciados.append(item.cod_ibge)
            continue
        repository.add_carteira_ente(
            session, org_id=org_id, cod_ibge=item.cod_ibge, grupo=item.grupo, tag=item.tag
        )
        existentes.add(item.cod_ibge)
        adicionados.append(item.cod_ibge)
    removidos: list[str] = []
    for cod in data.remover:
        if repository.remove_carteira_ente(session, org_id=org_id, cod_ibge=cod) > 0:
            existentes.discard(cod)
            removidos.append(cod)
        else:
            ignorados.append(cod)
    if adicionados or removidos:
        # O escopo memorizado (A27) reflete a carteira de antes do lote.
        invalidar_escopo_carteira(session, org_id)
    repository.insert_audit_log(
        session,
        org_id=org_id,
        usuario_id=principal.usuario_id,
        acao="CARTEIRA_LOTE",
        recurso=f"carteira;+{len(adicionados)};-{len(removidos)}",
    )
    return CarteiraLoteResult(
        adicionados=adicionados,
        removidos=removidos,
        ignorados=ignorados,
        nao_licenciados=nao_licenciados,
        total_carteira=len(existentes),
    )


# --- Billing (Sprint 18) ---
def _competencia_atual() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _competencia_bounds(competencia: str) -> tuple[datetime, datetime]:
    ano, mes = int(competencia[:4]), int(competencia[5:7])
    inicio = datetime(ano, mes, 1, tzinfo=UTC)
    fim = (
        datetime(ano + 1, 1, 1, tzinfo=UTC) if mes == 12 else datetime(ano, mes + 1, 1, tzinfo=UTC)
    )
    return inicio, fim


def _metrica_efetiva(session: Session, org_id: uuid.UUID) -> tuple[str, Decimal, uuid.UUID | None]:
    """Métrica/preço vigentes: assinatura tem prioridade; senão cai na org; default 'fixo'."""
    assinatura = repository.get_assinatura(session, org_id=org_id)
    if assinatura is not None:
        return assinatura.metrica_cobranca, Decimal(assinatura.preco_unitario), assinatura.id
    org = repository.get_org(session, org_id)
    metrica = org.metrica_cobranca if org and org.metrica_cobranca else "fixo"
    return metrica, Decimal("0"), None


def _computar_base(
    session: Session, org_id: uuid.UUID, competencia: str, metrica: str, preco: Decimal
) -> dict[str, Any]:
    """Quantidade cobrável + memória + source_refs conforme a métrica (auditável)."""
    inicio, fim = _competencia_bounds(competencia)
    memoria: dict[str, Any] = {"metrica": metrica, "competencia": competencia}
    source_refs: list[dict[str, Any]] = []

    if metrica == "por_populacao":
        linhas = repository.carteira_populacao(session, org_id=org_id)
        quantidade = Decimal("0")
        detalhe = []
        for cod, nome, pop, ano_ref, sref in linhas:
            pop_val = int(pop) if pop is not None else 0
            quantidade += Decimal(pop_val)
            detalhe.append(
                {
                    "cod_ibge": cod,
                    "nome": nome,
                    "populacao": pop_val,
                    "ano_ref": ano_ref,
                    "sem_populacao": pop is None,
                }
            )
            if sref:
                source_refs.append(dict(sref))
        memoria.update(
            {
                "base": "gold.dim_ente.populacao (IBGE)",
                "n_entes": len(linhas),
                "entes": detalhe,
                "formula": "quantidade = Σ população dos entes da carteira",
            }
        )
    elif metrica == "por_consulta_ia":
        quantidade = Decimal(
            repository.count_conversa_uso_competencia(
                session, org_id=org_id, inicio=inicio, fim=fim
            )
        )
        memoria.update(
            {
                "base": "op.conversa_uso",
                "formula": "quantidade = nº de consultas de IA na competência",
            }
        )
        source_refs.append({"relatorio": "op.conversa_uso", "periodo": competencia})
    elif metrica == "fixo":
        quantidade = Decimal("1")
        memoria.update({"base": "op.assinatura", "formula": "cobrança fixa por ciclo"})
        source_refs.append({"relatorio": "op.assinatura", "periodo": competencia})
    else:  # por_ente (default)
        cods = [e.cod_ibge for e in repository.list_carteira(session, org_id)]
        quantidade = Decimal(len(cods))
        memoria.update(
            {
                "base": "op.carteira_ente",
                "entes": cods,
                "formula": "quantidade = nº de entes na carteira",
            }
        )
        source_refs.append({"relatorio": "op.carteira_ente", "periodo": competencia})

    valor_total = (quantidade * preco).quantize(Decimal("0.01"))
    memoria["quantidade"] = str(quantidade)
    memoria["preco_unitario"] = str(preco)
    memoria["valor_total"] = str(valor_total)
    return {
        "quantidade": quantidade,
        "preco_unitario": preco,
        "valor_total": valor_total,
        "memoria": memoria,
        "source_refs": source_refs,
    }


def set_assinatura(session: Session, principal: Principal, data: AssinaturaInput) -> AssinaturaOut:
    org_id = _org_id(principal)
    row = repository.upsert_assinatura(
        session,
        org_id=org_id,
        plano=data.plano,
        metrica_cobranca=data.metrica_cobranca,
        preco_unitario=data.preco_unitario,
        moeda=data.moeda,
        ciclo=data.ciclo,
        status=data.status,
        inicio_vigencia=data.inicio_vigencia,
        fim_vigencia=data.fim_vigencia,
    )
    repository.insert_audit_log(
        session,
        org_id=org_id,
        usuario_id=principal.usuario_id,
        acao="CONFIG_ASSINATURA",
        recurso=f"assinatura;metrica={data.metrica_cobranca};preco={data.preco_unitario}",
    )
    return AssinaturaOut.model_validate(row)


def billing_overview(session: Session, principal: Principal) -> BillingOverview:
    org_id = _org_id(principal)
    metrica, preco, _ = _metrica_efetiva(session, org_id)
    competencia = _competencia_atual()
    base = _computar_base(session, org_id, competencia, metrica, preco)
    assinatura = repository.get_assinatura(session, org_id=org_id)
    preview = FaturaPreview(
        competencia=competencia,
        metrica_cobranca=metrica,
        quantidade=base["quantidade"],
        preco_unitario=base["preco_unitario"],
        valor_total=base["valor_total"],
        moeda=assinatura.moeda if assinatura else "BRL",
        ja_emitida=repository.get_fatura_by_competencia(
            session, org_id=org_id, competencia=competencia
        )
        is not None,
        memoria=base["memoria"],
        source_refs=base["source_refs"],
    )
    return BillingOverview(
        org_id=org_id,
        metrica_cobranca=metrica,
        assinatura=AssinaturaOut.model_validate(assinatura) if assinatura else None,
        preview=preview,
        faturas=[
            FaturaOut.model_validate(f) for f in repository.list_faturas(session, org_id=org_id)
        ],
    )


def emitir_fatura(session: Session, principal: Principal, data: FaturaEmitInput) -> FaturaOut:
    org_id = _org_id(principal)
    competencia = data.competencia or _competencia_atual()
    if not _COMPETENCIA_RE.match(competencia):
        raise AppError(status=422, title="Competência inválida", detail="Use o formato YYYY-MM.")
    if repository.get_fatura_by_competencia(session, org_id=org_id, competencia=competencia):
        raise AppError(
            status=409,
            title="Fatura já emitida",
            detail=f"Já existe fatura para {competencia}.",
        )
    metrica, preco, assinatura_id = _metrica_efetiva(session, org_id)
    base = _computar_base(session, org_id, competencia, metrica, preco)
    row = repository.insert_fatura(
        session,
        org_id=org_id,
        assinatura_id=assinatura_id,
        competencia=competencia,
        metrica_cobranca=metrica,
        quantidade=base["quantidade"],
        preco_unitario=base["preco_unitario"],
        valor_total=base["valor_total"],
        moeda="BRL",
        status=data.status,
        empenho_ref=data.empenho_ref,
        contrato_ref=data.contrato_ref,
        vencimento=data.vencimento,
        memoria=base["memoria"],
        source_refs=base["source_refs"],
    )
    repository.insert_audit_log(
        session,
        org_id=org_id,
        usuario_id=principal.usuario_id,
        acao="EMITIR_FATURA",
        recurso=(
            f"fatura:{row.id};competencia={competencia};metrica={metrica};"
            f"valor={base['valor_total']};empenho={data.empenho_ref or ''};"
            f"contrato={data.contrato_ref or ''}"
        ),
    )
    return FaturaOut.model_validate(row)


# --- Auditoria filtrável (Sprint 18) ---
def auditoria(
    session: Session,
    principal: Principal,
    *,
    acao: str | None = None,
    recurso: str | None = None,
    usuario_id: uuid.UUID | None = None,
    texto: str | None = None,
    de: datetime | None = None,
    ate: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> AuditoriaPage:
    org_id = _org_id(principal)
    rows, total = repository.query_auditoria(
        session,
        org_id=org_id,
        acao=acao,
        recurso=recurso,
        usuario_id=usuario_id,
        texto=texto,
        de=de,
        ate=ate,
        limit=limit,
        offset=offset,
    )
    return AuditoriaPage(
        itens=[
            AuditoriaItem(
                id=log.id,
                usuario_id=log.usuario_id,
                usuario_nome=nome,
                usuario_email=email,
                acao=log.acao,
                recurso=log.recurso,
                ts=log.ts,
            )
            for log, nome, email in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


# --- Licença, do ponto de vista do tenant (Sprint H1) ---
def minhas_licencas(session: Session, principal: Principal) -> list[MinhaLicencaOut]:
    """A licença da própria organização — sem precisar de um 403 para descobrir o estado dela."""
    org_id = _org_id(principal)
    hoje = date.today()
    return [
        MinhaLicencaOut(
            id=lic.id,
            tipo=lic.tipo,
            cod_ibge=lic.cod_ibge,
            uf=lic.uf,
            vigencia_inicio=lic.vigencia_inicio,
            vigencia_fim=lic.vigencia_fim,
            status=lic.status,
            vigente=lic.vigente_em(hoje),
            observacao=lic.observacao,
        )
        for lic in repository.list_licencas(session, org_id)
    ]


def trocar_organizacao(
    principal: Principal, org_id: uuid.UUID, *, senha: str
) -> TokenResponse:
    """Emite um novo token com outra organização **da qual o usuário já é membro**.

    A troca reautentica: mudar de organização muda tudo o que a sessão enxerga, então
    pedir a senha de novo é o mesmo crivo que se aplica a operações sensíveis — e impede
    que um token esquecido num terminal aberto passeie entre clientes.

    O que esta função **não** faz, de propósito: colocar o usuário numa organização de
    que ele não participa. Vínculo é concessão, e quem concede é o operador da
    plataforma (``POST /platform/orgs/{id}/...``, Sprint 19). Se um administrador de
    tenant pudesse se vincular a outro tenant, o isolamento entre clientes deixaria de
    existir — e nenhuma "comprovação extra" na tela conserta isso, porque a verificação
    aconteceria do lado de quem quer entrar.
    """
    with admin_session() as session:
        usuario = repository.get_usuario(session, principal.usuario_id)
        if usuario is None or not verify_password(senha, usuario.senha_hash):
            raise AppError(
                status=401, title="Não autenticado", detail="Senha incorreta."
            )
        views = repository.membership_views_for_user(session, principal.usuario_id)
        alvo = next((v for v in views if v.org_id == org_id), None)
        if alvo is None:
            raise AppError(
                status=403,
                title="Sem vínculo",
                detail=(
                    "Você não participa desta organização. O vínculo é concedido pelo "
                    "operador da plataforma."
                ),
                type_="urn:plataforma-fiscal:error:sem-vinculo",
            )
        token = create_access_token(
            usuario_id=principal.usuario_id,
            org_id=alvo.org_id,
            capacidades=sorted(alvo.capacidades),
        )
        repository.insert_audit_log(
            session,
            org_id=alvo.org_id,
            usuario_id=principal.usuario_id,
            acao="TROCAR_ORGANIZACAO",
            recurso=f"organizacao:{alvo.org_id}:{alvo.org_nome}",
        )
    return TokenResponse(access_token=token)


def alterar_senha(principal: Principal, *, senha_atual: str, senha_nova: str) -> None:
    """Troca a própria senha. Exige a atual — sessão aberta não é prova de identidade."""
    if len(senha_nova) < 8:
        raise AppError(
            status=422, title="Senha fraca", detail="Use ao menos 8 caracteres."
        )
    with admin_session() as session:
        usuario = repository.get_usuario(session, principal.usuario_id)
        if usuario is None or not verify_password(senha_atual, usuario.senha_hash):
            raise AppError(status=401, title="Não autenticado", detail="Senha atual incorreta.")
        usuario.senha_hash = hash_password(senha_nova)
        repository.insert_audit_log(
            session,
            org_id=principal.org_id,
            usuario_id=principal.usuario_id,
            acao="ALTERAR_SENHA",
            recurso=f"usuario:{principal.usuario_id}",
        )


def atualizar_perfil(principal: Principal, *, nome: str) -> MeResponse:
    """Atualiza o próprio nome — o que o usuário pode mudar sozinho, sem crivo extra."""
    limpo = nome.strip()
    if not limpo:
        raise AppError(status=422, title="Nome vazio", detail="Informe o nome.")
    with admin_session() as session:
        usuario = repository.get_usuario(session, principal.usuario_id)
        if usuario is None:
            raise AppError(status=404, title="Não encontrado", detail="Usuário inexistente.")
        usuario.nome = limpo
    return build_me(principal.usuario_id, principal.org_id)
