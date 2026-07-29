"""Orquestração da qualidade do dado (Sprint 26).

Roda os checks aplicáveis a um recorte, **persiste o estado corrente** e emite alerta
quando algo crítico quebra. Três invariantes deste módulo:

1. **Nada aqui recalcula regra fiscal.** Os checks leem o que os módulos donos do assunto
   materializaram; qualidade compara, não decide número.
2. **Um check que não pôde rodar não é um check que passou.** Insumo ausente vira
   ``aviso`` com motivo — e o painel mostra a diferença entre "verificado e correto" e
   "não deu para verificar".
3. **Falha nunca é silenciosa.** Todo check em falha vira alerta na fila da organização,
   com link para a Central de Dados.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError
from app.modules.ingestion.models import DimEntrega
from app.modules.quality import checks as checks_mod
from app.modules.quality import lineage_seed, repository
from app.modules.quality.checks import ResultadoCheck
from app.modules.quality.models import DataQualityCheck
from app.modules.quality.schemas import (
    CheckOut,
    ExecucaoChecksOut,
    LineageAresta,
    LineageNo,
    LineageResponse,
    QualidadeResponse,
    ResumoQualidade,
)
from app.shared.scope import carteira_scope_ibges

ROTULOS: dict[str, str] = {
    "receita_soma_filhos": "Receita: soma das origens = total",
    "despesa_estagios_monotonicos": "Despesa: empenhado ≥ liquidado ≥ pago",
    "rcl_calculada_vs_publicada": "RCL calculada = RCL publicada (A3)",
    "dcl_a6_vs_rgf": "DCL: RREO Anexo 6 × RGF Anexo 2",
    "msc_vs_dca": "Patrimônio: MSC ↔ DCA",
    "minimo_saude_recalculado": "Mínimo de saúde recalculado",
    "minimo_educacao_recalculado": "Mínimo de educação recalculado",
    "mart_vs_detalhe_pessoal": "Reconciliação mart × detalhe (pessoal)",
    "freshness_rreo": "Atualidade do RREO",
    "freshness_rgf": "Atualidade do RGF",
    "freshness_dca": "Atualidade da DCA",
    "freshness_msc": "Atualidade da MSC",
    "contrato_layout": "Contrato de layout da fonte",
    "execucao_agendada": "Execução agendada do backfill",
}

CATEGORIA_QUALIDADE = "qualidade_dado"
CATEGORIA_FALHA_INGESTAO = "falha_ingestao"
_LINK_CENTRAL = "/central-dados?painel=qualidade"


def rotulo(codigo: str) -> str:
    return ROTULOS.get(codigo, codigo.replace("_", " ").capitalize())


# --------------------------------------------------------------------------- #
# Execução
# --------------------------------------------------------------------------- #
def _versao_vigente(session: Session, cod_ibge: str, relatorio: str, periodo: str) -> str | None:
    return session.scalar(
        select(DimEntrega.versao_entrega).where(
            DimEntrega.cod_ibge == cod_ibge,
            DimEntrega.relatorio == relatorio,
            DimEntrega.periodo == periodo,
            DimEntrega.vigente.is_(True),
        )
    )


def _rgf_de_rreo(periodo_rreo: str) -> str | None:
    """RREO bimestral → RGF quadrimestral correspondente (B2→Q1, B4→Q2, B6→Q3)."""
    try:
        ano, bim = periodo_rreo.split("-B", 1)
        numero = int(bim)
    except (ValueError, TypeError):
        return None
    return f"{ano}-Q{(numero + 1) // 2}" if numero % 2 == 0 else None


def _exercicio(periodo: str) -> int | None:
    try:
        return int(periodo[:4])
    except ValueError:
        return None


def executar_para_ente(
    session: Session,
    cod_ibge: str,
    periodo_rreo: str,
    *,
    job_id: uuid.UUID | None = None,
    hoje: date | None = None,
) -> list[ResultadoCheck]:
    """Roda todos os checks aplicáveis ao ente/período. Não levanta: coleta resultados."""
    resultados: list[ResultadoCheck] = []
    versao = _versao_vigente(session, cod_ibge, "RREO", periodo_rreo)
    if versao is not None:
        resultados.append(checks_mod.receita_soma_filhos(session, cod_ibge, periodo_rreo, versao))
        resultados.append(checks_mod.despesa_estagios(session, cod_ibge, periodo_rreo, versao))
        resultados.append(
            checks_mod.rcl_calculada_vs_publicada(session, cod_ibge, periodo_rreo, versao)
        )
        for area in ("saude", "educacao"):
            resultados.append(
                checks_mod.minimo_recalculado_vs_materializado(
                    session, cod_ibge, periodo_rreo, area
                )
            )
    periodo_rgf = _rgf_de_rreo(periodo_rreo)
    if periodo_rgf:
        resultados.append(
            checks_mod.dcl_a6_vs_rgf(session, cod_ibge, periodo_rreo, periodo_rgf)
        )
        resultados.append(
            checks_mod.mart_vs_detalhe_pessoal(session, cod_ibge, periodo_rreo, periodo_rgf)
        )
    ano = _exercicio(periodo_rreo)
    if ano is not None:
        resultados.append(checks_mod.msc_vs_dca(session, cod_ibge, ano))
    for sla in checks_mod.SLAS:
        resultados.append(checks_mod.freshness(session, sla, cod_ibge=cod_ibge, hoje=hoje))
    return resultados


def persistir(
    session: Session, resultados: list[ResultadoCheck], *, job_id: uuid.UUID | None = None
) -> None:
    for r in resultados:
        repository.upsert_check(
            session,
            {
                "job_id": job_id,
                "fonte": r.fonte,
                "cod_ibge": r.cod_ibge,
                "periodo": r.periodo,
                "check_codigo": r.check_codigo,
                "status": r.status,
                "esquerda": r.esquerda,
                "direita": r.direita,
                "diferenca": r.diferenca,
                "tolerancia": r.tolerancia,
                "detalhe": r.detalhe or None,
            },
        )


def registrar_contrato_layout(
    session: Session,
    *,
    fonte: str,
    cod_ibge: str | None,
    periodo: str | None,
    erro: str,
    job_id: uuid.UUID | None = None,
) -> ResultadoCheck:
    """O parser recusou o layout: vira check, não só log.

    O conector já falha alto (Sprint 1B); o que faltava era esse "não" virar estado
    consultável — sem isso, uma fonte que mudou de formato some da tela sem explicação.
    """
    resultado = ResultadoCheck(
        check_codigo="contrato_layout",
        fonte=fonte,
        status="falha",
        cod_ibge=cod_ibge,
        periodo=periodo,
        detalhe={"erro": erro[:2000], "regra": "layout declarado pelo conector"},
    )
    persistir(session, [resultado], job_id=job_id)
    return resultado


def registrar_execucao_perdida(
    session: Session, *, fonte: str, esperado_em: datetime, detalhe: str
) -> ResultadoCheck:
    """Agendamento que deveria ter rodado e não rodou (Sprint 21 monitorada)."""
    resultado = ResultadoCheck(
        check_codigo="execucao_agendada",
        fonte=fonte,
        status="falha",
        detalhe={
            "esperado_em": esperado_em.isoformat(),
            "motivo": detalhe,
            "regra": "execução agendada perdida também é falha de dado",
        },
    )
    persistir(session, [resultado])
    return resultado


def executar_e_alertar(
    session: Session,
    cod_ibge: str,
    periodo_rreo: str,
    *,
    job_id: uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
    hoje: date | None = None,
) -> ExecucaoChecksOut:
    """Roda, persiste e — havendo organização — emite os alertas das falhas."""
    resultados = executar_para_ente(
        session, cod_ibge, periodo_rreo, job_id=job_id, hoje=hoje
    )
    persistir(session, resultados, job_id=job_id)
    falhas = [r for r in resultados if r.status == "falha"]
    emitidos = 0
    if org_id is not None:
        emitidos = emitir_alertas(session, org_id, falhas)
    return ExecucaoChecksOut(
        cod_ibge=cod_ibge,
        periodo=periodo_rreo,
        executados=len(resultados),
        ok=sum(1 for r in resultados if r.status == "ok"),
        aviso=sum(1 for r in resultados if r.status == "aviso"),
        falha=len(falhas),
        alertas_emitidos=emitidos,
        codigos_falha=[r.check_codigo for r in falhas],
    )


def emitir_alertas(
    session: Session, org_id: uuid.UUID, falhas: list[ResultadoCheck]
) -> int:
    """Cada falha vira um alerta com link para a Central — nunca fica só no banco."""
    from app.modules.alerts import repository as alerts_repo
    from app.modules.alerts import rules

    agora = datetime.now(UTC)
    for r in falhas:
        freshness = r.check_codigo.startswith("freshness_")
        categoria = CATEGORIA_FALHA_INGESTAO if freshness else CATEGORIA_QUALIDADE
        severidade = rules.SEV_ATENCAO if freshness else rules.SEV_CRITICO
        alerts_repo.upsert_alerta(
            session,
            {
                "id": uuid.uuid4(),
                "org_id": org_id,
                "cod_ibge": r.cod_ibge or "BR",
                "status": "nova",
                "criado_em": agora,
                "atualizado_em": agora,
                "chave": f"qualidade:{r.check_codigo}:{r.cod_ibge or 'BR'}:{r.periodo or '-'}",
                "categoria": categoria,
                "severidade": severidade,
                "prioridade": rules.prioridade(severidade, categoria),
                "titulo": _titulo_alerta(r),
                "motivo_legal": (
                    "Integridade do dado publicado: o número exibido tem de reproduzir a "
                    "fonte oficial (LRF art. 48 — transparência e confiabilidade)."
                ),
                "acao_sugerida": _acao_alerta(r),
                "prazo": None,
                "link": _LINK_CENTRAL,
                "indicador": r.check_codigo,
                "periodo": r.periodo,
                "source_ref": None,
                "memoria": {
                    "esquerda": str(r.esquerda) if r.esquerda is not None else None,
                    "direita": str(r.direita) if r.direita is not None else None,
                    "diferenca": str(r.diferenca) if r.diferenca is not None else None,
                    "tolerancia": str(r.tolerancia) if r.tolerancia is not None else None,
                    **(r.detalhe or {}),
                },
            },
        )
    return len(falhas)


def _titulo_alerta(r: ResultadoCheck) -> str:
    if r.check_codigo.startswith("freshness_"):
        atraso = int(r.esquerda or 0)
        return f"{rotulo(r.check_codigo)} — {atraso} dia(s) além do prazo"
    return f"{rotulo(r.check_codigo)} — divergência detectada"


def _acao_alerta(r: ResultadoCheck) -> str:
    if r.check_codigo.startswith("freshness_"):
        return (
            "Verificar a publicação da fonte no SICONFI e reprocessar a carga na Central "
            "de Dados. Enquanto a fonte não atualiza, as telas mostram o período anterior."
        )
    if r.check_codigo == "contrato_layout":
        return (
            "O layout da fonte mudou: o conector recusou o arquivo em vez de adivinhar. "
            "Ajustar o parser antes de reprocessar."
        )
    return (
        "Conferir a memória de cálculo do indicador e a entrega de origem na Central de "
        "Dados. Enquanto o check estiver em falha, o número exibido carrega o selo."
    )


# --------------------------------------------------------------------------- #
# Leitura (painel)
# --------------------------------------------------------------------------- #
def _to_out(row: DataQualityCheck) -> CheckOut:
    return CheckOut(
        id=row.id,
        job_id=row.job_id,
        fonte=row.fonte,
        cod_ibge=row.cod_ibge,
        periodo=row.periodo,
        check_codigo=row.check_codigo,
        rotulo=rotulo(row.check_codigo),
        status=row.status,
        esquerda=row.esquerda,
        direita=row.direita,
        diferenca=row.diferenca,
        tolerancia=row.tolerancia,
        detalhe=row.detalhe or {},
        executado_em=row.executado_em,
    )


def painel(
    session: Session,
    principal: Principal,
    *,
    fonte: str | None = None,
    status: str | None = None,
    cod_ibge: str | None = None,
    pagina: int = 1,
    por_pagina: int = 50,
) -> QualidadeResponse:
    """Painel de qualidade restrito ao escopo do usuário (§6.4)."""
    escopo = carteira_scope_ibges(session, principal)
    linhas, total = repository.listar_checks(
        session,
        fonte=fonte,
        status=status,
        cod_ibge=cod_ibge,
        cods_escopo=escopo,
        limite=por_pagina,
        offset=(pagina - 1) * por_pagina,
    )
    contagem = repository.contar_por_status(session, cods_escopo=escopo)
    falhas, _ = repository.listar_checks(
        session, status="falha", cods_escopo=escopo, limite=500
    )
    resumo = ResumoQualidade(
        total=sum(contagem.values()),
        ok=contagem.get("ok", 0),
        aviso=contagem.get("aviso", 0),
        falha=contagem.get("falha", 0),
        fontes_com_falha=sorted({f.fonte for f in falhas}),
        checks_com_falha=sorted({f.check_codigo for f in falhas}),
    )
    observacao = None
    if resumo.total == 0:
        observacao = (
            "Nenhum check executado ainda no seu escopo. Eles rodam ao fim de cada carga "
            "(Central de Dados) e na verificação agendada."
        )
    return QualidadeResponse(
        gerado_em=datetime.now(UTC),
        resumo=resumo,
        itens=[_to_out(row) for row in linhas],
        total=total,
        pagina=pagina,
        por_pagina=por_pagina,
        observacao=observacao,
    )


def selo_do_ente(
    session: Session, cod_ibge: str, periodo: str | None = None
) -> list[CheckOut]:
    """Checks abertos que a página fiscal precisa selar sobre o número exibido."""
    abertos = repository.checks_abertos(session, cod_ibge=cod_ibge, periodo=periodo)
    return [_to_out(row) for row in abertos]


# --------------------------------------------------------------------------- #
# Lineage
# --------------------------------------------------------------------------- #
def seed_lineage(session: Session) -> int:
    """Aplica o grafo do código no banco (idempotente). Retorna o total de arestas."""
    for aresta in lineage_seed.arestas():
        repository.upsert_aresta(
            session,
            origem=aresta.origem,
            destino=aresta.destino,
            tipo=aresta.tipo,
            detalhe=aresta.detalhe,
        )
    return repository.contar_arestas(session)


def _camada(no: str) -> str:
    if no.startswith("bronze."):
        return "bronze"
    if no.startswith("silver."):
        return "silver"
    if no.startswith("gold."):
        return "gold"
    if no.startswith(("GET ", "POST ", "PATCH ", "DELETE ")):
        return "endpoint"
    if no.startswith("/"):
        return "pagina"
    return "fonte"


def lineage(session: Session, *, no: str | None = None) -> LineageResponse:
    """Grafo completo, ou a vizinhança navegável de um nó nos dois sentidos."""
    if repository.contar_arestas(session) == 0:
        seed_lineage(session)
    arestas = [
        LineageAresta(origem=a.origem, destino=a.destino, tipo=a.tipo, detalhe=a.detalhe or {})
        for a in repository.listar_arestas(session)
    ]
    nos = sorted({a.origem for a in arestas} | {a.destino for a in arestas})
    catalogo = [LineageNo(id=n, camada=_camada(n)) for n in nos]
    if no is None:
        return LineageResponse(
            nos=catalogo, arestas=arestas, total_arestas=len(arestas)
        )
    if no not in nos:
        raise AppError(
            status=404,
            title="Nó inexistente no lineage",
            detail=f"'{no}' não está no grafo; consulte /admin/lineage sem filtro.",
        )
    adjacencia: dict[str, list[LineageAresta]] = {}
    reversa: dict[str, list[LineageAresta]] = {}
    for a in arestas:
        adjacencia.setdefault(a.origem, []).append(a)
        reversa.setdefault(a.destino, []).append(a)

    jusante = _percorrer(adjacencia, no, para_frente=True)
    montante = _percorrer(reversa, no, para_frente=False)
    return LineageResponse(
        no=no,
        camada=_camada(no),
        montante=montante,
        jusante=jusante,
        paginas_afetadas=sorted({a.destino for a in jusante if _camada(a.destino) == "pagina"}),
        fontes_de_origem=sorted({a.origem for a in montante if _camada(a.origem) == "fonte"}),
        nos=catalogo,
        arestas=arestas,
        total_arestas=len(arestas),
    )


def _percorrer(
    indice: dict[str, list[LineageAresta]], inicio: str, *, para_frente: bool
) -> list[LineageAresta]:
    """Busca em largura; o grafo é um DAG pequeno, mas o visitados evita ciclo acidental."""
    vistos: set[str] = {inicio}
    fila = [inicio]
    resultado: list[LineageAresta] = []
    while fila:
        atual = fila.pop(0)
        for aresta in indice.get(atual, []):
            proximo = aresta.destino if para_frente else aresta.origem
            resultado.append(aresta)
            if proximo not in vistos:
                vistos.add(proximo)
                fila.append(proximo)
    return resultado


def dicionario_checks() -> dict[str, Any]:
    """Catálogo dos checks, para a Central explicar o que cada um verifica."""
    return {
        codigo: {"rotulo": texto, "critico": not codigo.startswith("freshness_")}
        for codigo, texto in ROTULOS.items()
    }


def tolerancia_padrao() -> dict[str, str]:
    return {
        "reais": str(checks_mod.TOL_REAIS),
        "pontos_percentuais": str(checks_mod.TOL_PONTOS),
        "fator_aviso": str(Decimal(checks_mod.FATOR_AVISO)),
    }
