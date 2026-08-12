"""Regras do control plane (Sprint 19).

Duas coisas moram aqui e em lugar nenhum: **provisionar** uma organização e **licenciar**
o que ela enxerga. O admin do tenant gerencia usuários e carteira dentro do que a licença
permite; quem define a licença é o operador da plataforma.

Toda ação é auditada com o marcador ``plataforma`` — o control plane escreve no
``op.audit_log`` como qualquer outra ação, porque "quem liberou este ente, e quando?" é
pergunta que precisa de resposta anos depois.
"""

from __future__ import annotations

import mimetypes
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal
from app.core.errors import AppError
from app.core.security import hash_password
from app.modules.assistant.models import ConversaUso
from app.modules.catalog.models import DimEnte
from app.modules.platform.schemas import (
    AssinaturaPatch,
    AuditoriaPlataformaItem,
    AuditoriaPlataformaPage,
    IdentidadeVisualOut,
    LicencaCreate,
    LicencaOut,
    LicencaPatch,
    OrgProvisionar,
    OrgUsoOut,
    ProvisionamentoOut,
)
from app.modules.reports.models import Relatorio
from app.modules.tenancy import repository
from app.modules.tenancy.models import (
    LICENCA_ATIVA,
    Assinatura,
    AuditLog,
    CarteiraEnte,
    Licenca,
    Membership,
    Organizacao,
)
from app.modules.tenancy.schemas import AssinaturaInput, AssinaturaOut
from app.shared.scope import invalidar_cobertura

MARCADOR = "plataforma"

# Papel criado junto com a organização. Nasce com todas as capacidades porque, no
# instante do provisionamento, não existe mais ninguém para conceder as que faltassem.
_PAPEL_ADMIN = "Administrador"
_CAPACIDADES_ADMIN = (
    "ver",
    "exportar",
    "config_alerta",
    "gerar_relatorio",
    "usar_ia",
    "administrar",
)

_EXTENSOES_IMAGEM = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
_TAMANHO_MAXIMO_BYTES = 2 * 1024 * 1024


def _auditar(
    session: Session, principal: Principal, *, acao: str, recurso: str, org_id: uuid.UUID | None
) -> None:
    session.add(
        AuditLog(
            org_id=org_id,
            usuario_id=principal.usuario_id,
            acao=f"{MARCADOR}:{acao}",
            recurso=recurso,
        )
    )


def _to_out(lic: Licenca, *, hoje: date | None = None) -> LicencaOut:
    return LicencaOut(
        id=lic.id,
        org_id=lic.org_id,
        tipo=lic.tipo,
        cod_ibge=lic.cod_ibge,
        uf=lic.uf,
        vigencia_inicio=lic.vigencia_inicio,
        vigencia_fim=lic.vigencia_fim,
        status=lic.status,
        vigente=lic.vigente_em(hoje or date.today()),
        observacao=lic.observacao,
        criada_em=lic.criada_em,
    )


def _criar_licenca(
    session: Session, *, org_id: uuid.UUID, data: LicencaCreate, criada_por: uuid.UUID
) -> Licenca:
    lic = Licenca(
        org_id=org_id,
        tipo=data.tipo,
        cod_ibge=data.cod_ibge,
        uf=data.uf,
        vigencia_inicio=data.vigencia_inicio or date.today(),
        vigencia_fim=data.vigencia_fim,
        status=LICENCA_ATIVA,
        observacao=data.observacao,
        criada_por=criada_por,
    )
    return repository.add_licenca(session, lic)


# --- Provisionamento --------------------------------------------------------


def provisionar_org(
    session: Session, principal: Principal, data: OrgProvisionar
) -> ProvisionamentoOut:
    """Cria organização, papel de administrador, usuário inicial e licenças, atomicamente.

    Provisionar pela metade é pior que não provisionar: uma org sem papel não consegue
    criar usuário, e um usuário sem licença entra e não vê nada. Ou sai tudo, ou nada.
    """
    if repository.get_usuario_by_email(session, data.admin_email) is not None:
        raise AppError(
            status=409,
            title="Conflito",
            detail=f"O e-mail {data.admin_email} já pertence a um usuário.",
        )

    org = repository.create_org(
        session,
        nome=data.nome,
        tipo_conta=data.tipo_conta,
        metrica_cobranca=data.metrica_cobranca,
    )
    papel = repository.create_papel(session, org_id=org.id, nome=_PAPEL_ADMIN)
    repository.set_papel_capacidades(
        session, papel_id=papel.id, capacidades=list(_CAPACIDADES_ADMIN)
    )
    usuario = repository.create_usuario(
        session,
        email=str(data.admin_email),
        nome=data.admin_nome,
        senha_hash=hash_password(data.admin_senha),
        mfa_ativo=False,
    )
    repository.create_membership(
        session, org_id=org.id, usuario_id=usuario.id, papel_id=papel.id
    )

    licencas = [
        _criar_licenca(session, org_id=org.id, data=item, criada_por=principal.usuario_id)
        for item in data.licencas
    ]

    # Sem métrica/preço aqui, a organização nasce com billing indefinido e a primeira
    # fatura sai em zero — o achado central da Sprint H1. O formulário de
    # provisionamento agora coleta os dois; quando vêm, a assinatura nasce junto.
    assinatura_row: Assinatura | None = None
    if data.metrica_cobranca is not None:
        assinatura_row = _gravar_assinatura(
            session,
            org_id=org.id,
            plano="padrao",
            metrica_cobranca=data.metrica_cobranca,
            preco_unitario=data.preco_unitario or Decimal("0"),
            moeda="BRL",
            ciclo="mensal",
            status="ativa",
            inicio_vigencia=None,
            fim_vigencia=None,
        )

    _auditar(
        session,
        principal,
        acao="provisionar_org",
        recurso=(
            f"organizacao:{org.id}:{org.nome}"
            + (
                f";metrica={data.metrica_cobranca};preco={data.preco_unitario or Decimal('0')}"
                if assinatura_row is not None
                else ""
            )
        ),
        org_id=org.id,
    )
    session.flush()
    return ProvisionamentoOut(
        org_id=org.id,
        nome=org.nome,
        admin_usuario_id=usuario.id,
        admin_email=usuario.email,
        papel_admin_id=papel.id,
        licencas=[_to_out(lic) for lic in licencas],
        assinatura=AssinaturaOut.model_validate(assinatura_row) if assinatura_row else None,
    )


# --- Assinatura / billing (Sprint H1) ----------------------------------------
# emitir_fatura (tenancy/service.py) sempre calculava preco=Decimal("0") porque o
# único endpoint que grava op.assinatura (POST /billing/assinatura) está com 403
# hardcoded de propósito — um tenant não pode fixar o próprio preço. Faltava o
# equivalente do control plane; é o que existe daqui para baixo.


def _gravar_assinatura(
    session: Session,
    *,
    org_id: uuid.UUID,
    plano: str,
    metrica_cobranca: str,
    preco_unitario: Decimal,
    moeda: str,
    ciclo: str,
    status: str,
    inicio_vigencia: date | None,
    fim_vigencia: date | None,
) -> Assinatura:
    return repository.upsert_assinatura(
        session,
        org_id=org_id,
        plano=plano,
        metrica_cobranca=metrica_cobranca,
        preco_unitario=preco_unitario,
        moeda=moeda,
        ciclo=ciclo,
        status=status,
        inicio_vigencia=inicio_vigencia,
        fim_vigencia=fim_vigencia,
    )


def definir_assinatura(
    session: Session, principal: Principal, org_id: uuid.UUID, data: AssinaturaInput
) -> AssinaturaOut:
    """POST — cria ou substitui integralmente a assinatura (métrica + preço) da organização."""
    if repository.get_org(session, org_id) is None:
        raise AppError(status=404, title="Organização inexistente", detail=str(org_id))
    row = _gravar_assinatura(
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
    _auditar(
        session,
        principal,
        acao="definir_assinatura",
        recurso=f"assinatura:{row.id};metrica={data.metrica_cobranca};preco={data.preco_unitario}",
        org_id=org_id,
    )
    session.flush()
    return AssinaturaOut.model_validate(row)


def alterar_assinatura(
    session: Session, principal: Principal, org_id: uuid.UUID, data: AssinaturaPatch
) -> AssinaturaOut:
    """PATCH — altera só os campos informados (ex.: reajustar o preço sem reenviar tudo)."""
    if repository.get_org(session, org_id) is None:
        raise AppError(status=404, title="Organização inexistente", detail=str(org_id))
    atual = repository.get_assinatura(session, org_id=org_id)
    base: dict[str, object] = {
        "plano": atual.plano if atual else "padrao",
        "metrica_cobranca": atual.metrica_cobranca if atual else "fixo",
        "preco_unitario": atual.preco_unitario if atual else Decimal("0"),
        "moeda": atual.moeda if atual else "BRL",
        "ciclo": atual.ciclo if atual else "mensal",
        "status": atual.status if atual else "ativa",
        "inicio_vigencia": atual.inicio_vigencia if atual else None,
        "fim_vigencia": atual.fim_vigencia if atual else None,
    }
    for campo, valor in data.model_dump(exclude_unset=True).items():
        if valor is not None:
            base[campo] = valor
    row = _gravar_assinatura(session, org_id=org_id, **base)  # type: ignore[arg-type]
    antes = f"{atual.metrica_cobranca}:{atual.preco_unitario}" if atual else "inexistente"
    depois = f"{row.metrica_cobranca}:{row.preco_unitario}"
    _auditar(
        session,
        principal,
        acao="alterar_assinatura",
        recurso=f"assinatura:{row.id};antes={antes};depois={depois}",
        org_id=org_id,
    )
    session.flush()
    return AssinaturaOut.model_validate(row)


# --- Licenças ---------------------------------------------------------------


def conceder_licenca(
    session: Session, principal: Principal, org_id: uuid.UUID, data: LicencaCreate
) -> LicencaOut:
    if repository.get_org(session, org_id) is None:
        raise AppError(status=404, title="Organização inexistente", detail=str(org_id))
    lic = _criar_licenca(session, org_id=org_id, data=data, criada_por=principal.usuario_id)
    alvo = lic.cod_ibge or lic.uf or "global"
    _auditar(
        session,
        principal,
        acao="conceder_licenca",
        recurso=f"licenca:{lic.id}:{lic.tipo}:{alvo}",
        org_id=org_id,
    )
    invalidar_cobertura(session, org_id)
    session.flush()
    return _to_out(lic)


def alterar_licenca(
    session: Session, principal: Principal, licenca_id: uuid.UUID, data: LicencaPatch
) -> LicencaOut:
    """Suspende, reativa ou prorroga — preservando o registro.

    Não existe exclusão: o histórico de acesso é evidência, e apagá-lo impediria de
    responder "esta organização podia ver este ente em março?".
    """
    lic = repository.get_licenca(session, licenca_id)
    if lic is None:
        raise AppError(status=404, title="Licença inexistente", detail=str(licenca_id))

    antes = lic.status
    if data.status is not None:
        lic.status = data.status
    if data.vigencia_fim is not None:
        if data.vigencia_fim < lic.vigencia_inicio:
            raise AppError(
                status=422,
                title="Vigência inválida",
                detail="vigencia_fim não pode ser anterior a vigencia_inicio.",
            )
        lic.vigencia_fim = data.vigencia_fim
    if data.observacao is not None:
        lic.observacao = data.observacao
    lic.atualizada_em = datetime.now(UTC)

    _auditar(
        session,
        principal,
        acao="alterar_licenca",
        recurso=f"licenca:{lic.id}:{antes}->{lic.status}",
        org_id=lic.org_id,
    )
    invalidar_cobertura(session, lic.org_id)
    session.flush()
    return _to_out(lic)


def listar_licencas(session: Session, org_id: uuid.UUID) -> list[LicencaOut]:
    hoje = date.today()
    return [_to_out(lic, hoje=hoje) for lic in repository.list_licencas(session, org_id)]


# --- Uso --------------------------------------------------------------------


def _entes_licenciados(session: Session, licencas: list[Licenca]) -> int | None:
    """Quantos entes a licença alcança. ``None`` sob licença global — não é enumerável."""
    hoje = date.today()
    vigentes = [lic for lic in licencas if lic.vigente_em(hoje)]
    if any(lic.tipo == "global" for lic in vigentes):
        return None
    entes = {lic.cod_ibge for lic in vigentes if lic.tipo == "ente" and lic.cod_ibge}
    ufs = [lic.uf for lic in vigentes if lic.tipo == "uf" and lic.uf]
    if ufs:
        for uf in ufs:
            entes |= set(
                session.scalars(select(DimEnte.cod_ibge).where(DimEnte.cod_ibge.startswith(uf)))
            )
    return len(entes)


def uso_por_org(session: Session, org_id: uuid.UUID | None = None) -> list[OrgUsoOut]:
    """Consumo por organização. Requer sessão de control plane (lê entre orgs)."""
    conds = [Organizacao.id == org_id] if org_id is not None else []
    orgs = list(session.scalars(select(Organizacao).where(*conds).order_by(Organizacao.nome)))

    saida: list[OrgUsoOut] = []
    for org in orgs:
        licencas = repository.list_licencas(session, org.id)
        ativas = [lic for lic in licencas if lic.status == LICENCA_ATIVA]
        carteira = int(
            session.scalar(
                select(func.count()).select_from(CarteiraEnte).where(CarteiraEnte.org_id == org.id)
            )
            or 0
        )
        usuarios = int(
            session.scalar(
                select(func.count()).select_from(Membership).where(Membership.org_id == org.id)
            )
            or 0
        )
        relatorios = int(
            session.scalar(
                select(func.count()).select_from(Relatorio).where(Relatorio.org_id == org.id)
            )
            or 0
        )
        ia = int(
            session.scalar(
                select(func.count()).select_from(ConversaUso).where(ConversaUso.org_id == org.id)
            )
            or 0
        )
        licenciados = _entes_licenciados(session, licencas)
        saida.append(
            OrgUsoOut(
                org_id=org.id,
                nome=org.nome,
                tipo_conta=org.tipo_conta,
                logo_url=org.logo_url,
                criada_em=org.criada_em,
                licencas_ativas=len(ativas),
                entes_licenciados=licenciados,
                entes_na_carteira=carteira,
                usuarios=usuarios,
                relatorios_gerados=relatorios,
                consultas_ia=ia,
                entes_com_dado=carteira,
            )
        )
    return saida


# --- Identidade visual ------------------------------------------------------


def _guardar_imagem(nome_base: str, conteudo: bytes, filename: str | None) -> str:
    """Grava no storage local (MVP) e devolve a URL relativa.

    O storage é configurável justamente para trocar disco por S3 sem tocar no domínio.
    """
    sufixo = Path(filename or "").suffix.lower()
    if sufixo not in _EXTENSOES_IMAGEM:
        raise AppError(
            status=422,
            title="Formato não suportado",
            detail=f"Use um destes formatos: {', '.join(sorted(_EXTENSOES_IMAGEM))}.",
        )
    if len(conteudo) > _TAMANHO_MAXIMO_BYTES:
        raise AppError(
            status=413,
            title="Arquivo grande demais",
            detail="Limite de 2 MB para identidade visual.",
        )
    raiz = (Path(settings.reports_storage_dir).expanduser().resolve().parent / "identidade")
    raiz.mkdir(parents=True, exist_ok=True)
    destino = (raiz / f"{nome_base}{sufixo}").resolve()
    if raiz not in destino.parents:
        raise RuntimeError("Caminho de identidade visual fora do storage configurado.")
    destino.write_bytes(conteudo)
    return f"/identidade/{destino.name}"


def definir_logo(
    session: Session,
    principal: Principal,
    org_id: uuid.UUID,
    *,
    conteudo: bytes,
    filename: str | None,
) -> IdentidadeVisualOut:
    org = repository.get_org(session, org_id)
    if org is None:
        raise AppError(status=404, title="Organização inexistente", detail=str(org_id))
    org.logo_url = _guardar_imagem(f"org-{org_id}", conteudo, filename)
    _auditar(
        session, principal, acao="definir_logo", recurso=f"organizacao:{org_id}", org_id=org_id
    )
    session.flush()
    return IdentidadeVisualOut(alvo=f"organizacao:{org_id}", url=org.logo_url)


def definir_brasao(
    session: Session,
    principal: Principal,
    cod_ibge: str,
    *,
    conteudo: bytes,
    filename: str | None,
) -> IdentidadeVisualOut:
    ente = session.get(DimEnte, cod_ibge)
    if ente is None:
        raise AppError(status=404, title="Ente inexistente", detail=cod_ibge)
    ente.brasao_url = _guardar_imagem(f"ente-{cod_ibge}", conteudo, filename)
    _auditar(session, principal, acao="definir_brasao", recurso=f"ente:{cod_ibge}", org_id=None)
    session.flush()
    return IdentidadeVisualOut(alvo=f"ente:{cod_ibge}", url=ente.brasao_url)


def tipo_conteudo(nome: str) -> str:
    return mimetypes.guess_type(nome)[0] or "application/octet-stream"


# --- Auditoria própria do control plane (Sprint H1) --------------------------
# O superusuário nunca tem org_id de sessão (existe fora de qualquer organização),
# então nunca poderia chamar GET /admin/auditoria — que filtra por org_id do
# principal. Este endpoint espelha o mesmo padrão (filtros, paginação, autor
# via join) mas sobre a sessão bypass de RLS (`superuser_session`), cobrindo
# inclusive ações sem organização-alvo (`definir_brasao`, org_id is None).


def auditoria(
    session: Session,
    *,
    org_id: uuid.UUID | None = None,
    acao: str | None = None,
    recurso: str | None = None,
    usuario_id: uuid.UUID | None = None,
    texto: str | None = None,
    de: datetime | None = None,
    ate: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> AuditoriaPlataformaPage:
    rows, total = repository.query_auditoria_plataforma(
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
    return AuditoriaPlataformaPage(
        itens=[
            AuditoriaPlataformaItem(
                id=log.id,
                org_id=log.org_id,
                org_nome=org_nome,
                usuario_id=log.usuario_id,
                usuario_nome=usuario_nome,
                usuario_email=usuario_email,
                acao=log.acao,
                recurso=log.recurso,
                ts=log.ts,
            )
            for log, usuario_nome, usuario_email, org_nome in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
