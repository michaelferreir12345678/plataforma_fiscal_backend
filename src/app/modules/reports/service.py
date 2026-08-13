"""Regras de geração, lote, rastreabilidade e agendamento de relatórios."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal
from app.core.errors import AppError, ScopeForbiddenError
from app.modules.ingestion import repository as ingestion_repo
from app.modules.reports import renderers, repository
from app.modules.reports.models import ESCOPOS, FORMATOS, MODELOS, PERIODICIDADES, Relatorio
from app.modules.reports.schemas import (
    AgendamentoCreate,
    AgendamentoOut,
    AgendamentoPatch,
    DadoIncompletoOut,
    ModeloRelatorioOut,
    ModelosResponse,
    RelatorioCreate,
    RelatorioDetalheOut,
    RelatorioListaOut,
    RelatorioOut,
    RelatorioSolicitacaoOut,
)
from app.modules.tenancy import repository as tenancy_repo
from app.shared import periodo as periodo_util
from app.shared.scope import assert_ente_in_scope, carteira_scope_ibges

MODEL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "executivo": {
        "nome": "Resumo Executivo",
        "publico": "Prefeito · Gabinete",
        "descricao": "Síntese dos principais números fiscais, riscos e mínimos constitucionais.",
        "secoes": ["rcl", "pessoal", "divida", "resultado", "saude", "educacao"],
        "formalidade": "Sintético",
    },
    "limites": {
        "nome": "Relatório de Limites Legais",
        "publico": "Controladoria · TCE/TCM",
        "descricao": "Limites e mínimos com componentes e memória de cálculo rastreável.",
        "secoes": ["pessoal", "divida", "saude", "educacao"],
        "formalidade": "Formal",
    },
    "comparativo": {
        "nome": "Comparativo / Benchmark",
        "publico": "Consultoria · Gestão",
        "descricao": "Posição, percentil e ranking nas coortes materializadas da Sprint 13.",
        "secoes": ["benchmark"],
        "formalidade": "Analítico",
    },
    "conformidade": {
        "nome": "Relatório de Conformidade",
        "publico": "Sefaz · Controle",
        "descricao": "Entregas SICONFI, prazos, pendências e situação dos limites.",
        "secoes": ["pessoal", "divida", "saude", "educacao", "calendario"],
        "formalidade": "Formal",
    },
    "boletim": {
        "nome": "Boletim Periódico",
        "publico": "Carteira · Gestão recorrente",
        "descricao": "Boletim recorrente com indicadores, comparação e conformidade.",
        "secoes": [
            "rcl",
            "pessoal",
            "divida",
            "resultado",
            "saude",
            "educacao",
            "benchmark",
            "calendario",
        ],
        "formalidade": "Sintético",
    },
}


def modelos() -> ModelosResponse:
    return ModelosResponse(
        modelos=[
            ModeloRelatorioOut(
                codigo=codigo,
                nome=definition["nome"],
                publico=definition["publico"],
                descricao=definition["descricao"],
                secoes=definition["secoes"],
                formatos=list(FORMATOS),
                formalidade=definition["formalidade"],
            )
            for codigo, definition in MODEL_DEFINITIONS.items()
        ],
        gerado_em=datetime.now(UTC),
    )


def _validate_options(modelo: str, formato: str, escopo: str) -> None:
    if modelo not in MODELOS:
        raise AppError(
            status=422,
            title="Modelo inválido",
            detail=f"Use um dos modelos: {', '.join(MODELOS)}.",
        )
    if formato not in FORMATOS:
        raise AppError(
            status=422,
            title="Formato inválido",
            detail=f"Use um dos formatos: {', '.join(FORMATOS)}.",
        )
    if escopo not in ESCOPOS:
        raise AppError(
            status=422,
            title="Escopo inválido",
            detail=f"Use um dos escopos: {', '.join(ESCOPOS)}.",
        )


def _resolve_entes(
    session: Session,
    principal: Principal,
    *,
    escopo: str,
    ente: str | None,
    entes: list[str] | None,
) -> list[str]:
    if principal.org_id is None:
        raise AppError(
            status=400, title="Sem organização", detail="Selecione uma organização ativa."
        )
    if escopo == "ente":
        if not ente:
            raise AppError(
                status=422,
                title="Ente obrigatório",
                detail="Informe ente para o escopo individual.",
            )
        assert_ente_in_scope(session, principal, ente)
        return [ente]

    scope = carteira_scope_ibges(session, principal)
    selecionados = list(dict.fromkeys(entes or sorted(scope)))
    if not selecionados:
        raise AppError(
            status=422,
            title="Escopo vazio",
            detail="Não há entes disponíveis para geração em lote.",
        )
    for cod in selecionados:
        if cod not in scope:
            raise ScopeForbiddenError(cod)
    if len(selecionados) > 2000:
        raise AppError(
            status=422,
            title="Lote excede o limite",
            detail="Divida a solicitação em lotes de até 2.000 entes.",
        )
    return selecionados


def solicitar(
    session: Session, principal: Principal, body: RelatorioCreate
) -> RelatorioSolicitacaoOut:
    _validate_options(body.modelo, body.formato, body.escopo)
    entes = _resolve_entes(
        session,
        principal,
        escopo=body.escopo,
        ente=body.ente,
        entes=body.entes,
    )
    assert principal.org_id is not None
    lote_id = uuid.uuid4()
    as_of = body.as_of or datetime.now(UTC)
    parametros = _jsonable(
        {
            **body.parametros,
            "secoes": body.secoes or MODEL_DEFINITIONS[body.modelo]["secoes"],
            "modelo_versao": "v1",
            "snapshot_as_of": as_of,
        }
    )
    rows = repository.insert_relatorios(
        session,
        org_id=principal.org_id,
        lote_id=lote_id,
        modelo=body.modelo,
        formato=body.formato,
        escopo=body.escopo,
        entes=entes,
        periodo=body.periodo,
        as_of=as_of,
        parametros=parametros,
        criado_por=principal.usuario_id,
    )
    # O worker usa outra sessão/thread: publica o job antes de enfileirá-lo.
    session.commit()
    return RelatorioSolicitacaoOut(
        lote_id=lote_id,
        total_entes=len(rows),
        status="enfileirado",
        relatorios=[to_out(row) for row in rows],
    )


def obter(session: Session, principal: Principal, relatorio_id: uuid.UUID) -> RelatorioDetalheOut:
    row = repository.get_relatorio(session, relatorio_id)
    if row is None or principal.org_id is None:
        raise AppError(status=404, title="Relatório não encontrado")
    lote = repository.list_lote(session, org_id=principal.org_id, lote_id=row.lote_id)
    base = to_out(row).model_dump()
    return RelatorioDetalheOut(**base, lote_itens=[to_out(item) for item in lote])


def listar(session: Session, principal: Principal, *, limit: int = 50) -> RelatorioListaOut:
    if principal.org_id is None:
        raise AppError(status=400, title="Sem organização")
    rows = repository.list_relatorios(session, org_id=principal.org_id, limit=min(limit, 200))
    return RelatorioListaOut(
        itens=[to_out(row) for row in rows],
        total=repository.count_relatorios(session, org_id=principal.org_id),
        gerado_em=datetime.now(UTC),
    )


def agendar(session: Session, principal: Principal, body: AgendamentoCreate) -> AgendamentoOut:
    _validate_options(body.modelo, body.formato, body.escopo)
    if body.periodicidade not in PERIODICIDADES:
        raise AppError(
            status=422,
            title="Periodicidade inválida",
            detail=f"Use: {', '.join(PERIODICIDADES)}.",
        )
    entes = _resolve_entes(
        session,
        principal,
        escopo=body.escopo,
        ente=body.ente,
        entes=body.entes,
    )
    assert principal.org_id is not None
    parametros = _jsonable(
        {
            **body.parametros,
            "secoes": body.secoes or MODEL_DEFINITIONS[body.modelo]["secoes"],
            "modelo_versao": "v1",
        }
    )
    row = repository.insert_agendamento(
        session,
        org_id=principal.org_id,
        modelo=body.modelo,
        formato=body.formato,
        escopo=body.escopo,
        entes=entes,
        periodo=body.periodo,
        periodicidade=body.periodicidade,
        parametros=parametros,
        proxima_execucao=body.proxima_execucao,
        criado_por=principal.usuario_id,
    )
    return AgendamentoOut.model_validate(row, from_attributes=True)


def listar_agendamentos(session: Session, principal: Principal) -> list[AgendamentoOut]:
    """Agendamentos da organização — a UI da Sprint 25E lista, edita e desativa."""
    assert principal.org_id is not None
    linhas = repository.list_agendamentos(session, org_id=principal.org_id)
    return [AgendamentoOut.model_validate(row, from_attributes=True) for row in linhas]


def atualizar_agendamento(
    session: Session,
    principal: Principal,
    agendamento_id: uuid.UUID,
    body: AgendamentoPatch,
) -> AgendamentoOut:
    """Edita a recorrência. **Desativar preserva o registro** — é regra de negócio com
    histórico, não lixo: o gestor precisa saber que existiu um agendamento e por quanto
    tempo ele rodou."""
    assert principal.org_id is not None
    row = repository.get_agendamento(
        session, org_id=principal.org_id, agendamento_id=agendamento_id
    )
    if row is None:
        raise AppError(
            status=404, title="Agendamento não encontrado", detail=str(agendamento_id)
        )
    if body.periodicidade is not None:
        if body.periodicidade not in PERIODICIDADES:
            raise AppError(
                status=422,
                title="Periodicidade inválida",
                detail=f"Use: {', '.join(PERIODICIDADES)}.",
            )
        row.periodicidade = body.periodicidade
    if body.formato is not None:
        if body.formato not in FORMATOS:
            raise AppError(
                status=422, title="Formato inválido", detail=f"Use: {', '.join(FORMATOS)}."
            )
        row.formato = body.formato
    if body.periodo is not None:
        row.periodo = body.periodo
    if body.proxima_execucao is not None:
        row.proxima_execucao = body.proxima_execucao
    if body.ativo is not None:
        row.ativo = body.ativo
    row.atualizado_em = datetime.now(UTC)
    session.flush()
    session.refresh(row)
    return AgendamentoOut.model_validate(row, from_attributes=True)


def excluir_agendamento(
    session: Session, principal: Principal, agendamento_id: uuid.UUID
) -> None:
    assert principal.org_id is not None
    removidos = repository.delete_agendamento(
        session, org_id=principal.org_id, agendamento_id=agendamento_id
    )
    if removidos == 0:
        raise AppError(
            status=404, title="Agendamento não encontrado", detail=str(agendamento_id)
        )


def to_out(row: Relatorio) -> RelatorioOut:
    source_refs = [_source_ref(ref) for ref in (row.source_refs or [])]
    return RelatorioOut(
        id=row.id,
        lote_id=row.lote_id,
        modelo=row.modelo,
        modelo_versao=row.modelo_versao,
        formato=row.formato,
        escopo=row.escopo,
        cod_ibge=row.cod_ibge,
        periodo=row.periodo,
        as_of=row.as_of,
        status=row.status,
        progresso=row.progresso,
        cabecalho=row.cabecalho or {},
        source_refs=source_refs,
        memoria=row.memoria or {},
        dados_incompletos=[DadoIncompletoOut(**issue) for issue in row.dados_incompletos or []],
        arquivo_nome=row.arquivo_nome,
        arquivo_url=f"/relatorios/{row.id}/arquivo" if row.arquivo_path else None,
        mime_type=row.mime_type,
        tamanho_bytes=row.tamanho_bytes,
        conteudo_hash=row.conteudo_hash,
        gerado_em=row.gerado_em,
        erro=row.erro,
        criado_em=row.criado_em,
        atualizado_em=row.atualizado_em,
    )


def _source_ref(ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "relatorio": ref.get("relatorio") or "FONTE-NÃO-INFORMADA",
        "anexo": ref.get("anexo"),
        "periodo": ref.get("periodo"),
        "versao_entrega": ref.get("versao_entrega"),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(item) for item in value]
    return value


def _rgf_period(periodo: str) -> str:
    """RGF do **ciclo corrente**; período que a regra canônica não interpreta cai no Q3.

    Delega à regra canônica (§6.6) — era uma das seis cópias da A25. O fallback para o
    último quadrimestre do exercício é local e continua aqui de propósito: um relatório
    precisa de um período concreto para renderizar, e a regra de calendário não deve
    inventar um para quem pediu um exercício inteiro.
    """
    return periodo_util.em_periodo_rgf(periodo, quando=periodo_util.CICLO_CORRENTE) or (
        f"{periodo[:4]}-Q3"
    )


def _delivery(
    session: Session,
    *,
    cod_ibge: str,
    relatorio: str,
    periodo: str,
    as_of: datetime,
) -> tuple[str | None, datetime | None]:
    versao = ingestion_repo.resolve_versao(
        session,
        cod_ibge=cod_ibge,
        relatorio=relatorio,
        periodo=periodo,
        as_of=as_of,
    )
    if versao is None:
        return None, None
    efetivo = ingestion_repo.entrega_homologada_em(
        session,
        cod_ibge=cod_ibge,
        relatorio=relatorio,
        periodo=periodo,
        versao_entrega=versao,
    )
    return versao, efetivo


def formatar_valor(value: Decimal | None, unidade: str) -> str:
    """Valor formatado para leitura humana — fonte única (relatório, assistente, ferramenta).

    Público desde a Sprint IA-1a: a camada de ferramentas devolve ao modelo o mesmo texto
    que o relatório imprime. Duas formatações do mesmo número em canais diferentes é um
    convite a discussão sobre qual está certa.
    """
    if value is None:
        return "Dado não disponível"
    number = float(value)
    if unidade == "BRL":
        return f"R$ {number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if unidade == "PERCENTUAL":
        return f"{number:.2f}%".replace(".", ",")
    if unidade == "POSICAO":
        return f"{int(number)}º"
    return f"{number:,.2f}"


def _metric(
    *,
    codigo: str,
    rotulo: str,
    valor: Decimal | None,
    unidade: str,
    status: str,
    source_refs: list[dict[str, Any]],
    as_of: datetime | None,
    memoria: dict[str, Any],
    faixa: str | None = None,
) -> dict[str, Any]:
    return _jsonable(
        {
            "codigo": codigo,
            "rotulo": rotulo,
            "valor": valor,
            "valor_formatado": formatar_valor(valor, unidade),
            "unidade": unidade,
            "status": status,
            "faixa": faixa,
            "source_refs": [_source_ref(ref) for ref in source_refs],
            "as_of": as_of,
            "memoria": memoria,
        }
    )


def _missing_metric(
    issues: list[dict[str, Any]],
    *,
    codigo: str,
    rotulo: str,
    unidade: str,
    relatorio: str,
    anexo: str,
    periodo: str,
    formula: str,
) -> dict[str, Any]:
    message = f"{rotulo} não está materializado para {periodo}; o item foi mantido no relatório."
    issues.append(
        {
            "tipo": "ausente",
            "codigo": codigo,
            "mensagem": message,
            "periodo_esperado": periodo,
            "periodo_encontrado": None,
        }
    )
    return _metric(
        codigo=codigo,
        rotulo=rotulo,
        valor=None,
        unidade=unidade,
        status="dado_incompleto",
        source_refs=[
            {
                "relatorio": relatorio,
                "anexo": anexo,
                "periodo": periodo,
                "versao_entrega": None,
            }
        ],
        as_of=None,
        memoria={"formula": formula, "componentes": {}, "checagem": "fonte ausente"},
    )


def build_document(session: Session, row: Relatorio, gerado_em: datetime) -> dict[str, Any]:
    org = repository.get_org(session, row.org_id)
    ente = repository.get_ente(session, row.cod_ibge)
    usuario = repository.get_usuario(session, row.criado_por)
    if org is None:
        raise RuntimeError("Organização do relatório não existe.")

    cabecalho = {
        "organizacao": org.nome,
        "ente": ente.nome if ente and ente.nome else row.cod_ibge,
        "cod_ibge": row.cod_ibge,
        "uf": ente.uf if ente else None,
        "esfera": ente.esfera if ente else None,
        "responsavel": usuario.nome if usuario else "Geração agendada",
        "modelo": MODEL_DEFINITIONS[row.modelo]["nome"],
    }
    requested_sections = set(
        row.parametros.get("secoes") or MODEL_DEFINITIONS[row.modelo]["secoes"]
    )
    issues: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    rreo_version, rreo_as_of = _delivery(
        session,
        cod_ibge=row.cod_ibge,
        relatorio="RREO",
        periodo=row.periodo,
        as_of=row.as_of,
    )
    rgf_period = _rgf_period(row.periodo)
    rgf_version, rgf_as_of = _delivery(
        session,
        cod_ibge=row.cod_ibge,
        relatorio="RGF",
        periodo=rgf_period,
        as_of=row.as_of,
    )
    for source_name, selected_period in (("RREO", row.periodo), ("RGF", rgf_period)):
        latest = ingestion_repo.resolve_latest_entrega(
            session, cod_ibge=row.cod_ibge, relatorio=source_name, as_of=row.as_of
        )
        if latest is not None and latest.periodo > selected_period:
            issues.append(
                {
                    "tipo": "defasado",
                    "codigo": f"fonte_{source_name.lower()}",
                    "mensagem": (
                        f"O snapshot usa {source_name} {selected_period}, mas já existe "
                        f"entrega {latest.periodo} disponível no as_of informado."
                    ),
                    "periodo_esperado": latest.periodo,
                    "periodo_encontrado": selected_period,
                }
            )

    if "rcl" in requested_sections:
        rcl = (
            repository.get_rcl(
                session, cod_ibge=row.cod_ibge, periodo=row.periodo, versao=rreo_version
            )
            if rreo_version
            else None
        )
        if rcl is None:
            metrics.append(
                _missing_metric(
                    issues,
                    codigo="rcl",
                    rotulo="Receita Corrente Líquida (12 meses)",
                    unidade="BRL",
                    relatorio="RREO",
                    anexo="Anexo 03",
                    periodo=row.periodo,
                    formula="receita_corrente - deducoes",
                )
            )
        else:
            metrics.append(
                _metric(
                    codigo="rcl",
                    rotulo="Receita Corrente Líquida (12 meses)",
                    valor=rcl.rcl_12m,
                    unidade="BRL",
                    status="materializado",
                    source_refs=[
                        {
                            "relatorio": "RREO",
                            "anexo": "Anexo 03",
                            "periodo": row.periodo,
                            "versao_entrega": rreo_version,
                        }
                    ],
                    as_of=rreo_as_of,
                    memoria={
                        "formula": "RCL = receitas correntes - deduções legais (12 meses móveis)",
                        "componentes": rcl.memoria
                        or {
                            "receita_corrente": rcl.receita_corrente,
                            "deducoes": rcl.deducoes,
                        },
                        "tabela_origem": "gold.fato_rcl",
                    },
                )
            )

    if "pessoal" in requested_sections:
        pessoal = (
            repository.get_pessoal(
                session, cod_ibge=row.cod_ibge, periodo=rgf_period, versao=rgf_version
            )
            if rgf_version
            else None
        )
        mart_rows = repository.list_mart_indicadores(
            session, cod_ibge=row.cod_ibge, periodo=row.periodo
        )
        mart = next((item for item in mart_rows if item.indicador == "pessoal_executivo"), None)
        if pessoal is None:
            metrics.append(
                _missing_metric(
                    issues,
                    codigo="pessoal_executivo",
                    rotulo="Despesa com pessoal · Executivo",
                    unidade="PERCENTUAL",
                    relatorio="RGF",
                    anexo="Anexo 01",
                    periodo=rgf_period,
                    formula="(despesa_bruta - exclusoes) / RCL * 100",
                )
            )
        else:
            source_times = [time for time in (rgf_as_of, rreo_as_of) if time is not None]
            metrics.append(
                _metric(
                    codigo="pessoal_executivo",
                    rotulo="Despesa com pessoal · Executivo",
                    valor=(
                        mart.valor_pct_rcl
                        if mart and mart.valor_pct_rcl is not None
                        else pessoal.pct_rcl
                    ),
                    unidade="PERCENTUAL",
                    status=mart.faixa or "sem_classificacao" if mart else "sem_classificacao",
                    faixa=mart.faixa if mart else None,
                    source_refs=[
                        {
                            "relatorio": "RGF",
                            "anexo": "Anexo 01",
                            "periodo": rgf_period,
                            "versao_entrega": rgf_version,
                        },
                        {
                            "relatorio": "RREO",
                            "anexo": "Anexo 03",
                            "periodo": row.periodo,
                            "versao_entrega": rreo_version,
                        },
                    ],
                    as_of=max(source_times) if source_times else None,
                    memoria={
                        "formula": (
                            "despesa_liquida = despesa_bruta - exclusoes; "
                            "percentual = despesa_liquida / RCL × 100"
                        ),
                        "componentes": {
                            "despesa_bruta": pessoal.despesa_bruta,
                            "exclusoes": pessoal.exclusoes,
                            "despesa_liquida": pessoal.despesa_liquida,
                            "percentual_rcl_materializado": pessoal.pct_rcl,
                            "teto_pct": mart.teto_pct if mart else None,
                        },
                        "tabelas_origem": ["gold.fato_pessoal", "gold.mart_indicador"],
                    },
                )
            )

    if "divida" in requested_sections:
        divida = (
            repository.get_divida(
                session, cod_ibge=row.cod_ibge, periodo=rgf_period, versao=rgf_version
            )
            if rgf_version
            else None
        )
        if divida is None:
            metrics.append(
                _missing_metric(
                    issues,
                    codigo="divida_consolidada_liquida",
                    rotulo="Dívida Consolidada Líquida",
                    unidade="PERCENTUAL",
                    relatorio="RGF",
                    anexo="Anexo 02",
                    periodo=rgf_period,
                    formula="(divida_consolidada - disponibilidades - haveres) / RCL * 100",
                )
            )
        else:
            metrics.append(
                _metric(
                    codigo="divida_consolidada_liquida",
                    rotulo="Dívida Consolidada Líquida",
                    valor=divida.pct_rcl,
                    unidade="PERCENTUAL",
                    status="materializado",
                    source_refs=[
                        {
                            "relatorio": "RGF",
                            "anexo": "Anexo 02",
                            "periodo": rgf_period,
                            "versao_entrega": rgf_version,
                        }
                    ],
                    as_of=rgf_as_of,
                    memoria={
                        "formula": (
                            "DCL = dívida consolidada bruta - disponibilidades - haveres; "
                            "percentual = DCL / RCL ajustada × 100"
                        ),
                        "componentes": {
                            "dc_bruta": divida.dc_bruta,
                            "disponibilidades": divida.disponibilidades,
                            "haveres": divida.haveres,
                            "dcl_materializada": divida.dcl,
                            "rcl_ajustada": divida.rcl_ajustada,
                        },
                        "tabela_origem": "gold.fato_divida",
                    },
                )
            )

    if "resultado" in requested_sections:
        resultado = (
            repository.get_resultado(
                session, cod_ibge=row.cod_ibge, periodo=row.periodo, versao=rreo_version
            )
            if rreo_version
            else None
        )
        if resultado is None or resultado.resultado_primario is None:
            metrics.append(
                _missing_metric(
                    issues,
                    codigo="resultado_primario",
                    rotulo="Resultado primário",
                    unidade="BRL",
                    relatorio="RREO",
                    anexo="Anexo 06",
                    periodo=row.periodo,
                    formula="receita_primaria - despesa_primaria",
                )
            )
        else:
            metrics.append(
                _metric(
                    codigo="resultado_primario",
                    rotulo="Resultado primário",
                    valor=resultado.resultado_primario,
                    unidade="BRL",
                    status="materializado",
                    source_refs=[
                        {
                            "relatorio": "RREO",
                            "anexo": "Anexo 06",
                            "periodo": row.periodo,
                            "versao_entrega": rreo_version,
                        }
                    ],
                    as_of=rreo_as_of,
                    memoria={
                        "formula": "resultado_primario = receita_primaria - despesa_primaria",
                        "componentes": {
                            "receita_primaria": resultado.receita_primaria,
                            "despesa_primaria": resultado.despesa_primaria,
                            "resultado_nominal": resultado.resultado_nominal,
                        },
                        "tabela_origem": "gold.fato_resultado",
                    },
                )
            )

    if "saude" in requested_sections:
        saude = (
            repository.get_saude(
                session, cod_ibge=row.cod_ibge, periodo=row.periodo, versao_rreo=rreo_version
            )
            if rreo_version
            else None
        )
        if saude is None:
            metrics.append(
                _missing_metric(
                    issues,
                    codigo="saude_asps",
                    rotulo="Aplicação em Saúde · ASPS",
                    unidade="PERCENTUAL",
                    relatorio="RREO",
                    anexo="Anexo 12",
                    periodo=row.periodo,
                    formula="despesa_aplicada / base_impostos_transferencias * 100",
                )
            )
        else:
            metrics.append(
                _metric(
                    codigo="saude_asps",
                    rotulo="Aplicação em Saúde · ASPS",
                    valor=saude.pct_aplicado,
                    unidade="PERCENTUAL",
                    status="abaixo_minimo" if saude.abaixo_do_minimo else "conforme",
                    source_refs=[
                        {
                            "relatorio": "RREO",
                            "anexo": "Anexo 12",
                            "periodo": row.periodo,
                            "versao_entrega": saude.versao_rreo,
                        }
                    ],
                    as_of=rreo_as_of,
                    memoria={
                        "formula": (
                            "despesa_aplicada = despesa_bruta - deducoes - RP sem lastro; "
                            "percentual = despesa_aplicada / base × 100"
                        ),
                        "componentes": {
                            "base": saude.base_impostos_transferencias,
                            "despesa_bruta": saude.despesa_bruta,
                            "deducoes": saude.deducoes_outras,
                            "rpnp_sem_lastro": saude.rpnp_sem_lastro,
                            "despesa_aplicada": saude.despesa_aplicada,
                            "minimo_pct": saude.minimo_pct,
                        },
                        "tabela_origem": "gold.fato_saude",
                    },
                )
            )

    if "educacao" in requested_sections:
        educacao = (
            repository.get_educacao(
                session, cod_ibge=row.cod_ibge, periodo=row.periodo, versao_rreo=rreo_version
            )
            if rreo_version
            else None
        )
        if educacao is None:
            metrics.append(
                _missing_metric(
                    issues,
                    codigo="educacao_mde",
                    rotulo="Aplicação em Educação · MDE",
                    unidade="PERCENTUAL",
                    relatorio="RREO",
                    anexo="Anexo 08",
                    periodo=row.periodo,
                    formula="despesa_aplicada / base_impostos_transferencias * 100",
                )
            )
        else:
            metrics.append(
                _metric(
                    codigo="educacao_mde",
                    rotulo="Aplicação em Educação · MDE",
                    valor=educacao.pct_aplicado,
                    unidade="PERCENTUAL",
                    status="abaixo_minimo" if educacao.abaixo_do_minimo else "conforme",
                    source_refs=[
                        {
                            "relatorio": "RREO",
                            "anexo": "Anexo 08",
                            "periodo": row.periodo,
                            "versao_entrega": educacao.versao_rreo,
                        }
                    ],
                    as_of=rreo_as_of,
                    memoria={
                        "formula": (
                            "despesa_aplicada = impostos + FUNDEB - deducoes - "
                            "RP sem lastro; percentual = despesa_aplicada / base × 100"
                        ),
                        "componentes": {
                            "base": educacao.base_impostos_transferencias,
                            "despesa_impostos": educacao.despesa_impostos,
                            "despesa_fundeb": educacao.despesa_fundeb,
                            "deducoes": educacao.deducoes_outras,
                            "rpnp_sem_lastro": educacao.rpnp_sem_lastro,
                            "despesa_aplicada": educacao.despesa_aplicada,
                            "minimo_pct": educacao.minimo_pct,
                        },
                        "tabela_origem": "gold.fato_educacao",
                    },
                )
            )

    if "benchmark" in requested_sections:
        benchmarks = repository.list_benchmarks(
            session, cod_ibge=row.cod_ibge, periodo=row.periodo, as_of=row.as_of
        )
        if not benchmarks:
            metrics.append(
                _missing_metric(
                    issues,
                    codigo="benchmark",
                    rotulo="Posição na coorte",
                    unidade="PERCENTUAL",
                    relatorio="MART-BENCHMARK",
                    anexo="Coorte porte/região/PIB",
                    periodo=row.periodo,
                    formula="percentil materializado na coorte",
                )
            )
        for benchmark, coorte in benchmarks:
            source = dict(benchmark.source_ref or {})
            metrics.append(
                _metric(
                    codigo=f"benchmark_{coorte.codigo}",
                    rotulo=f"Percentil · {coorte.rotulo}",
                    valor=benchmark.percentil,
                    unidade="PERCENTUAL",
                    status=benchmark.faixa or "materializado",
                    source_refs=[source],
                    as_of=benchmark.as_of,
                    memoria={
                        "formula": (
                            "percentil = posição relativa do valor na distribuição "
                            "materializada da coorte"
                        ),
                        "componentes": {
                            "indicador": benchmark.indicador,
                            "valor": benchmark.valor,
                            "posicao": benchmark.posicao,
                            "percentil": benchmark.percentil,
                            "coorte": coorte.codigo,
                            "criterio": coorte.criterio,
                        },
                        "memoria_mart": benchmark.memoria,
                        "tabela_origem": "gold.mart_benchmark",
                    },
                )
            )

    compliance: list[dict[str, Any]] = []
    if "calendario" in requested_sections:
        items = repository.list_calendario(session, cod_ibge=row.cod_ibge, ano=row.periodo[:4])
        if not items:
            issues.append(
                {
                    "tipo": "ausente",
                    "codigo": "calendario_obrigacoes",
                    "mensagem": (
                        "Calendário de obrigações não materializado; " "a seção foi mantida vazia."
                    ),
                    "periodo_esperado": row.periodo[:4],
                    "periodo_encontrado": None,
                }
            )
        compliance = [
            _jsonable(
                {
                    "relatorio": item.relatorio,
                    "periodo": item.periodo,
                    "prazo": item.prazo,
                    "status": item.status,
                    "versao_entrega": item.versao_entrega,
                    "source_ref": item.source_ref,
                    "as_of": item.entregue_em or item.atualizado_em,
                }
            )
            for item in items
        ]

    source_refs = _dedupe_sources(source for metric in metrics for source in metric["source_refs"])
    document = {
        "titulo": MODEL_DEFINITIONS[row.modelo]["nome"],
        "modelo": row.modelo,
        "modelo_versao": row.modelo_versao,
        "cabecalho": cabecalho,
        "periodo": row.periodo,
        "as_of": row.as_of.isoformat(),
        "gerado_em": gerado_em.isoformat(),
        "metricas": metrics,
        "conformidade": compliance,
        "source_refs": source_refs,
        "dados_incompletos": issues,
        "criterio_incompletude": (
            "Itens esperados pelo modelo nunca são omitidos: "
            "ausência/defasagem gera linha explícita."
        ),
    }
    return _jsonable(document)


def _dedupe_sources(sources: Any) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for source in sources:
        normalized = _source_ref(source)
        key = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
        unique[key] = normalized
    return list(unique.values())


def persist_artifact(row: Relatorio, document: dict[str, Any]) -> dict[str, Any]:
    content = renderers.render(document, row.formato)
    digest = hashlib.sha256(content).hexdigest()
    storage_root = Path(settings.reports_storage_dir).expanduser().resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    safe_period = re.sub(r"[^A-Za-z0-9_-]+", "-", row.periodo)
    file_name = f"{row.modelo}-{row.cod_ibge}-{safe_period}-{str(row.id)[:8]}.{row.formato}"
    target = (storage_root / file_name).resolve()
    if storage_root not in target.parents:
        raise RuntimeError("Caminho de relatório fora do storage configurado.")
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(target)
    return {
        "arquivo_nome": file_name,
        "arquivo_path": str(target),
        "mime_type": renderers.MIME_TYPES[row.formato],
        "tamanho_bytes": len(content),
        "conteudo_hash": digest,
    }


def resolve_artifact(row: Relatorio) -> Path:
    if not row.arquivo_path:
        raise AppError(status=409, title="Arquivo ainda não disponível")
    storage_root = Path(settings.reports_storage_dir).expanduser().resolve()
    path = Path(row.arquivo_path).resolve()
    if storage_root not in path.parents or not path.is_file():
        raise AppError(status=404, title="Arquivo não encontrado")
    return path


def report_memory(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "modelo": document["modelo"],
        "modelo_versao": document["modelo_versao"],
        "snapshot_as_of": document["as_of"],
        "metricas": document["metricas"],
        "conformidade": document["conformidade"],
        "criterio_incompletude": document["criterio_incompletude"],
    }


def audit_export(
    session: Session,
    row: Relatorio,
    *,
    acao: str = "EXPORTAR_RELATORIO",
    usuario_id: uuid.UUID | None = None,
) -> None:
    tenancy_repo.insert_audit_log(
        session,
        org_id=row.org_id,
        usuario_id=usuario_id if usuario_id is not None else row.criado_por,
        acao=acao,
        recurso=(
            f"relatorio:{row.id};modelo={row.modelo};formato={row.formato};"
            f"ente={row.cod_ibge};periodo={row.periodo};hash={row.conteudo_hash or ''}"
        ),
    )
