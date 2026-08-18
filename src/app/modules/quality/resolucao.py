"""Sprint Q1 — o que o gestor **faz** com uma verificação em falha.

A Sprint 26 entregou a metade que detecta: 9 checks, os dois lados da conta guardados, o
selo sobre o número. Faltava a outra metade. Um aviso permanente que ninguém consegue
encerrar é um aviso que todos aprendem a ignorar — e aí a detecção, que custou uma sprint,
deixa de proteger qualquer coisa.

O fluxo inteiro se apoia numa pergunta só, a mesma que ``cobertura_do_ente`` faz sobre o
dado: **o número que não fechou é nosso ou é do ente?** A resposta determina a ação, e
oferecer a ação errada é pior que não oferecer nenhuma — "reprocessar" numa divergência da
fonte gasta o tempo do gestor, não muda o resultado e ensina a desconfiar do botão.

Ver ``causa.py`` para a classificação e ``docs/sprint_q1_resolucao_qualidade.md`` para o
desenho.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError
from app.modules.ingestion.models import DimEntrega
from app.modules.quality import repository as quality_repo
from app.modules.quality import service as quality_service
from app.modules.quality.causa import ACOES_POR_CLASSE, Acao, causa_do_check
from app.modules.quality.checks import SLAS, SlaFonte
from app.modules.quality.models import DataQualityCheck, QualidadeTratativa

#: Capacidade exigida para as ações que tocam a **gold**, que é compartilhada entre todas
#: as organizações: um clique de uma consultoria muda o que todas as outras leem. Já
#: aceitar como fato escreve só na tratativa privada da própria organização.
CAP_ACAO_COMPARTILHADA = "administrar"
CAP_ACAO_PRIVADA = "editar"

_MIN_JUSTIFICATIVA = 10


@dataclass(frozen=True)
class Ocorrencia:
    """Uma falha com o que se sabe dela e o que se pode fazer a respeito."""

    check_codigo: str
    cod_ibge: str | None
    periodo: str | None
    fonte: str
    status_check: str
    esquerda: Any
    direita: Any
    diferenca: Any
    tolerancia: Any
    classe: str
    lado_esquerdo: str
    lado_direito: str
    porque: str
    diagnostico: dict[str, Any]
    acoes: tuple[Acao, ...]
    tratativa: QualidadeTratativa | None


def _chave(check: DataQualityCheck) -> tuple[str, str | None, str | None]:
    return (check.check_codigo, check.cod_ibge, check.periodo)


def _sla_do_check(check_codigo: str) -> SlaFonte | None:
    alvo = check_codigo.removeprefix("freshness_").upper()
    return next((s for s in SLAS if s.relatorio == alvo), None)


def _diagnostico_cobertura(
    session: Session, check: DataQualityCheck
) -> tuple[dict[str, Any], tuple[Acao, ...]]:
    """Defasagem: mede o buraco, e **não** afirma de quem ele é.

    Aqui está o limite honesto do que se sabe sem perguntar à fonte. Temos a nossa entrega
    mais recente e o prazo legal; com isso dá para dizer *quantos períodos faltam*. O que
    não dá é saber se faltam porque o ente não publicou ou porque não ingerimos — e as
    duas coisas pedem ações opostas.

    Por isso a única ação oferecida é ``verificar_na_fonte``: uma consulta deliberada, para
    aquele ente e período, que transforma a dúvida em fato antes de qualquer reprocessamento.
    Oferecer "reingerir" agora seria prometer que existe o que ingerir.
    """
    detalhe = check.detalhe or {}
    sla = _sla_do_check(check.check_codigo)
    ultima = session.scalar(
        select(DimEntrega)
        .where(
            DimEntrega.cod_ibge == check.cod_ibge,
            DimEntrega.relatorio == (sla.relatorio if sla else ""),
            DimEntrega.vigente.is_(True),
        )
        .order_by(DimEntrega.periodo.desc())
        .limit(1)
    )
    diagnostico: dict[str, Any] = {
        "relatorio": sla.relatorio if sla else None,
        "rotulo": sla.rotulo if sla else None,
        "ultimo_periodo_que_temos": ultima.periodo if ultima else None,
        "ultima_versao_que_temos": ultima.versao_entrega if ultima else None,
        "atraso_dias": float(check.esquerda) if check.esquerda is not None else None,
        "tolerancia_dias": float(check.tolerancia) if check.tolerancia is not None else None,
        "prazo_legal_dias_apos_periodo": detalhe.get("prazo_legal_dias_apos_periodo"),
        "origem_indeterminada": True,
        "explicacao": (
            "A defasagem está medida, mas não diz de quem é a falta: ou o ente não "
            "publicou, ou publicou e não ingerimos. Consulte a fonte antes de reprocessar."
        ),
    }
    return diagnostico, ("verificar_na_fonte",)


def _diagnostico_estrutural(check: DataQualityCheck) -> dict[str, Any]:
    """Para as classes que a origem dos lados já decide, a evidência é a própria conta."""
    return {
        "esquerda": float(check.esquerda) if check.esquerda is not None else None,
        "direita": float(check.direita) if check.direita is not None else None,
        "diferenca": float(check.diferenca) if check.diferenca is not None else None,
        "tolerancia": float(check.tolerancia) if check.tolerancia is not None else None,
        "versao_entrega": check.versao_entrega,
        "origem_indeterminada": False,
    }


def montar_ocorrencia(
    session: Session, check: DataQualityCheck, tratativa: QualidadeTratativa | None
) -> Ocorrencia:
    """Junta veredito + classificação + evidência + ações cabíveis."""
    causa = causa_do_check(check.check_codigo)
    if causa.classe == "cobertura":
        diagnostico, acoes = _diagnostico_cobertura(session, check)
    else:
        diagnostico = _diagnostico_estrutural(check)
        acoes = ACOES_POR_CLASSE[causa.classe]
    # Caso já encerrado não volta a oferecer ação: reabrir é decisão explícita, e uma
    # tratativa que se reabre sozinha é a mesma triagem infinita que este fluxo evita.
    if tratativa is not None and tratativa.status in {"resolvida", "aceita_como_fato"}:
        acoes = ()
    return Ocorrencia(
        check_codigo=check.check_codigo,
        cod_ibge=check.cod_ibge,
        periodo=check.periodo,
        fonte=check.fonte,
        status_check=check.status,
        esquerda=check.esquerda,
        direita=check.direita,
        diferenca=check.diferenca,
        tolerancia=check.tolerancia,
        classe=causa.classe,
        lado_esquerdo=causa.esquerda,
        lado_direito=causa.direita,
        porque=causa.porque,
        diagnostico=diagnostico,
        acoes=acoes,
        tratativa=tratativa,
    )


def listar_ocorrencias(
    session: Session,
    principal: Principal,
    *,
    cods_escopo: set[str] | None,
    cod_ibge: str | None = None,
    incluir_encerradas: bool = False,
    limite: int = 100,
) -> list[Ocorrencia]:
    """As falhas do escopo, cada uma com classe, evidência e o que se pode fazer."""
    checks, _ = quality_repo.listar_checks(
        session,
        status="falha",
        cod_ibge=cod_ibge,
        cods_escopo=cods_escopo,
        limite=limite,
    )
    tratativas = {
        (t.check_codigo, t.cod_ibge, t.periodo): t
        for t in session.scalars(
            select(QualidadeTratativa).where(QualidadeTratativa.org_id == principal.org_id)
        )
    }
    ocorrencias = [montar_ocorrencia(session, c, tratativas.get(_chave(c))) for c in checks]
    if incluir_encerradas:
        return ocorrencias
    return [
        o
        for o in ocorrencias
        if o.tratativa is None or o.tratativa.status not in {"resolvida", "aceita_como_fato"}
    ]


def _obter_ou_criar(
    session: Session,
    principal: Principal,
    *,
    check_codigo: str,
    cod_ibge: str | None,
    periodo: str | None,
) -> QualidadeTratativa:
    atual = session.scalar(
        select(QualidadeTratativa).where(
            QualidadeTratativa.org_id == principal.org_id,
            QualidadeTratativa.check_codigo == check_codigo,
            QualidadeTratativa.cod_ibge == cod_ibge,
            QualidadeTratativa.periodo == periodo,
        )
    )
    if atual is not None:
        return atual
    nova = QualidadeTratativa(
        id=uuid.uuid4(),
        org_id=principal.org_id,
        check_codigo=check_codigo,
        cod_ibge=cod_ibge,
        periodo=periodo,
        status="aberta",
        tentativas=[],
        usuario_id=principal.usuario_id,
    )
    session.add(nova)
    session.flush()
    return nova


def _exigir_capacidade(principal: Principal, capacidade: str, acao: str) -> None:
    if capacidade not in principal.capacidades:
        raise AppError(
            status=403,
            title="Sem permissão para esta ação",
            detail=(
                f"A ação '{acao}' exige a capacidade '{capacidade}'. Ações que reprocessam "
                "dado alteram o schema `gold`, que é compartilhado por todas as "
                "organizações — o efeito não se limita à sua carteira."
            ),
        )


def _registrar_tentativa(
    tratativa: QualidadeTratativa, acao: str, resultado: dict[str, Any]
) -> None:
    # Lista nova (e não `append`): o JSONB do SQLAlchemy não detecta mutação in-place, e
    # a tentativa se perderia sem erro nenhum — o pior tipo de perda.
    tratativa.tentativas = [
        *(tratativa.tentativas or []),
        {"acao": acao, "em": datetime.now(UTC).isoformat(), **resultado},
    ]
    tratativa.atualizado_em = datetime.now(UTC)


def _reexecutar_check(
    session: Session, *, cod_ibge: str, periodo: str | None, check_codigo: str
) -> dict[str, Any]:
    """Roda os checks do ente e devolve o veredito **daquele** check.

    Toda ação passa por aqui, sem exceção: uma ação que não é reavaliada é uma ação que
    ninguém sabe se funcionou — e o gestor ficaria clicando num botão que talvez não faça
    nada.
    """
    saida = quality_service.executar_e_alertar(session, cod_ibge, periodo or "")
    novo = session.scalar(
        select(DataQualityCheck)
        .where(
            DataQualityCheck.check_codigo == check_codigo,
            DataQualityCheck.cod_ibge == cod_ibge,
        )
        .order_by(DataQualityCheck.seq.desc())
        .limit(1)
    )
    return {
        "status_apos": novo.status if novo else None,
        "checks_executados": saida.executados,
        "falhas_no_ente": saida.falha,
    }


def aplicar_acao(
    session: Session,
    principal: Principal,
    *,
    check_codigo: str,
    cod_ibge: str,
    periodo: str | None,
    acao: Acao | str,
    justificativa: str | None = None,
) -> Ocorrencia:
    """Executa a ação, **reexecuta o check** e registra o desfecho.

    A ação oferecida vem de ``montar_ocorrencia``; aqui ela é conferida outra vez contra a
    classe. A guarda mora dentro da ferramenta, não na borda que a chama (lição A22/E1):
    um cliente que montasse a requisição à mão não pode obter uma ação que a classe não
    autoriza.
    """
    checks, _ = quality_repo.listar_checks(
        session, check_codigo=check_codigo, cod_ibge=cod_ibge, limite=5
    )
    check = next((c for c in checks if c.periodo == periodo), checks[0] if checks else None)
    if check is None:
        raise AppError(
            status=404,
            title="Verificação não encontrada",
            detail=(
                f"Não há veredito vigente de '{check_codigo}' para {cod_ibge}"
                + (f" em {periodo}" if periodo else "")
                + "."
            ),
        )

    tratativa = _obter_ou_criar(
        session,
        principal,
        check_codigo=check_codigo,
        cod_ibge=check.cod_ibge,
        periodo=check.periodo,
    )
    ocorrencia = montar_ocorrencia(session, check, tratativa)
    if acao not in ocorrencia.acoes:
        raise AppError(
            status=422,
            title="Ação não cabível",
            detail=(
                f"'{acao}' não se aplica a uma falha da classe '{ocorrencia.classe}'. "
                f"{ocorrencia.porque} Ações cabíveis: "
                + (", ".join(ocorrencia.acoes) if ocorrencia.acoes else "nenhuma")
                + "."
            ),
        )

    tratativa.classe = ocorrencia.classe
    tratativa.diagnostico = ocorrencia.diagnostico

    if acao == "aceitar_como_fato":
        _exigir_capacidade(principal, CAP_ACAO_PRIVADA, acao)
        texto = (justificativa or "").strip()
        if len(texto) < _MIN_JUSTIFICATIVA:
            raise AppError(
                status=422,
                title="Justificativa obrigatória",
                detail=(
                    "Aceitar uma divergência como fato da fonte exige dizer por quê — "
                    "sem isso, o aceite silencia o número com um clique. O selo continua "
                    "aparecendo, agora com este motivo e com quem o assinou."
                ),
            )
        tratativa.status = "aceita_como_fato"
        tratativa.justificativa = texto
        tratativa.usuario_id = principal.usuario_id
        _registrar_tentativa(tratativa, acao, {"justificativa": texto})
        session.flush()
        return montar_ocorrencia(session, check, tratativa)

    if acao == "verificar_na_fonte":
        _exigir_capacidade(principal, CAP_ACAO_COMPARTILHADA, acao)
        resultado = _verificar_na_fonte(session, check)
        tratativa.status = "diagnosticada"
        tratativa.diagnostico = {**(tratativa.diagnostico or {}), **resultado}
        _registrar_tentativa(tratativa, acao, resultado)
        session.flush()
        return montar_ocorrencia(session, check, tratativa)

    if acao == "rematerializar":
        _exigir_capacidade(principal, CAP_ACAO_COMPARTILHADA, acao)
        from app.workers import materialize

        materializado = materialize.materialize_ente(session, cod_ibge)
        veredito = _reexecutar_check(
            session, cod_ibge=cod_ibge, periodo=check.periodo, check_codigo=check_codigo
        )
        _registrar_tentativa(
            tratativa, acao, {"materializado": materializado, **veredito}
        )
        # Só o veredito novo encerra o caso. "Apliquei a ação" não é "resolvi".
        tratativa.status = "resolvida" if veredito["status_apos"] == "ok" else "acao_aplicada"
        session.flush()
        atualizado, _ = quality_repo.listar_checks(
            session, check_codigo=check_codigo, cod_ibge=cod_ibge, limite=5
        )
        alvo = next((c for c in atualizado if c.periodo == check.periodo), check)
        return montar_ocorrencia(session, alvo, tratativa)

    raise AppError(
        status=422,
        title="Ação desconhecida",
        detail=f"'{acao}' não é uma ação deste fluxo.",
    )


def _verificar_na_fonte(session: Session, check: DataQualityCheck) -> dict[str, Any]:
    """Pergunta à fonte se existe entrega mais recente do que a nossa.

    É o passo que transforma "está defasado" em "de quem é a falta" — e o único do fluxo
    que sai da nossa base. Deliberadamente pontual (um ente, um relatório): varrer a
    carteira inteira para responder uma ocorrência seria caro e desnecessário.

    Ainda **não** consulta o SICONFI ao vivo: a descoberta de períodos publicados exige o
    conector com rede, e enfileirá-lo aqui tornaria a resposta assíncrona. O que este
    passo faz hoje é fechar o que dá para fechar com o que já temos — o período que o
    prazo legal já exigia contra o último que ingerimos — e declarar explicitamente o que
    falta apurar. Declarar o limite é o que impede a tela de parecer conclusiva.
    """
    sla = _sla_do_check(check.check_codigo)
    ultima = session.scalar(
        select(DimEntrega)
        .where(
            DimEntrega.cod_ibge == check.cod_ibge,
            DimEntrega.relatorio == (sla.relatorio if sla else ""),
            DimEntrega.vigente.is_(True),
        )
        .order_by(DimEntrega.periodo.desc())
        .limit(1)
    )
    return {
        "consultado_em": datetime.now(UTC).isoformat(),
        "ultimo_periodo_ingerido": ultima.periodo if ultima else None,
        "fonte_consultada": False,
        "pendente": (
            "A verificação ao vivo no SICONFI ainda não está ligada a este fluxo. Confira "
            "o período no portal do Tesouro: havendo entrega mais nova que a nossa, o caso "
            "é de reingestão; não havendo, o ente é que não publicou e o caso se aceita "
            "como fato."
        ),
    }
