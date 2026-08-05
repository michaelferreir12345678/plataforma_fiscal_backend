"""Motor de alertas (Sprint 15) — avalia um ente e materializa alertas + calendário.

Consome, sem recalcular, os módulos existentes:
- **Limites** (Módulo 3) — faixa classificada por indicador.
- **Previsões** (Sprint 14) — cruzamento projetado de teto (alerta PREDITIVO).
- **dim_entrega** (Sprint 1) — publicações homologadas (alerta de nova publicação)
  e defasagem de fontes complementares (SIOPS/SIOPE/CAPAG vs âncora RREO).

Idempotente: dedup por ``(org_id, chave)``; reavaliar não duplica e preserva o
``status`` já tratado pelo gestor.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.alerts import repository, rules
from app.modules.catalog import service as catalog_service
from app.modules.forecast import service as forecast_service
from app.modules.forecast.periodos import parse_periodo
from app.modules.health_edu import service as health_edu_service
from app.modules.indicators import rotulos
from app.modules.ingestion.models import DimEntrega
from app.modules.limits import repository as limits_repo
from app.modules.limits import service as limits_service
from app.shared.source_ref import SourceRef

# Fonte nacional (não por ente) de algumas entregas.
_COD_NACIONAL = "BR"
_PREDITIVOS = ("divida", "pessoal")


@dataclass(frozen=True)
class _Entregas:
    """Fotografia das entregas vigentes de um ente (por relatório)."""

    vigentes: dict[tuple[str, str], DimEntrega]  # (relatorio, periodo) -> entrega
    latest: dict[str, DimEntrega]  # relatorio -> entrega mais recente

    def entregue(self, relatorio: str, periodo: str) -> DimEntrega | None:
        return self.vigentes.get((relatorio, periodo))


def _rotulo_periodo(codigo: str) -> str:
    p = parse_periodo(codigo)
    if p.cadencia == "B":
        return f"{p.indice}º bimestre/{p.ano}"
    if p.cadencia == "Q":
        return f"{p.indice}º quadrimestre/{p.ano}"
    if p.cadencia == "M":
        return f"{p.indice:02d}/{p.ano}"
    return f"{p.ano}"


def _carregar_entregas(session: Session, cod_ibge: str) -> _Entregas:
    rows = list(
        session.scalars(
            select(DimEntrega).where(
                DimEntrega.cod_ibge.in_([cod_ibge, _COD_NACIONAL]),
                DimEntrega.vigente.is_(True),
            )
        )
    )
    vigentes: dict[tuple[str, str], DimEntrega] = {}
    latest: dict[str, DimEntrega] = {}
    for r in rows:
        if r.cod_ibge == cod_ibge:
            vigentes[(r.relatorio, r.periodo)] = r
        atual = latest.get(r.relatorio)
        # Guarda a entrega nacional só se não houver uma do próprio ente.
        prefer_ente = r.cod_ibge == cod_ibge
        if atual is None:
            latest[r.relatorio] = r
        else:
            try:
                mais_nova = parse_periodo(r.periodo).ordinal >= parse_periodo(atual.periodo).ordinal
            except ValueError:
                mais_nova = False
            atual_ente = atual.cod_ibge == cod_ibge
            if (prefer_ente and not atual_ente) or (prefer_ente == atual_ente and mais_nova):
                latest[r.relatorio] = r
    return _Entregas(vigentes=vigentes, latest=latest)


def _latest_periodo(entregas: _Entregas, relatorio: str) -> str | None:
    e = entregas.latest.get(relatorio)
    return e.periodo if e is not None else None


def _ente_alerta_base(cod_ibge: str, org_id: uuid.UUID, agora: datetime) -> dict:
    return {
        "id": uuid.uuid4(),
        "org_id": org_id,
        "cod_ibge": cod_ibge,
        "status": "nova",
        "criado_em": agora,
        "atualizado_em": agora,
    }


def avaliar_ente(
    session: Session,
    org_id: uuid.UUID,
    cod_ibge: str,
    *,
    incluir_preditivo: bool = True,
) -> int:
    """Avalia um ente e materializa alertas + calendário. Retorna nº de alertas escritos."""
    ente = catalog_service.refresh_dim_ente(session, cod_ibge)
    if ente is None or ente.esfera is None:
        return 0
    entregas = _carregar_entregas(session, cod_ibge)
    anchor = _latest_periodo(entregas, rules.REL_RREO)
    if anchor is None:
        return 0  # sem RREO não há âncora fiscal — nada a avaliar com honestidade

    agora = datetime.now(UTC)
    n = 0
    n += _calendario(session, cod_ibge, ente.esfera, ente.populacao, entregas, anchor, agora)
    n += _alertas_publicacao(session, org_id, cod_ibge, entregas, agora)
    n += _alertas_defasagem(session, org_id, cod_ibge, entregas, anchor, agora)
    n += _alertas_limite(session, org_id, cod_ibge, anchor, agora)
    n += _alertas_minimos(session, org_id, cod_ibge, anchor, agora)
    if incluir_preditivo:
        n += _alertas_preditivos(session, org_id, cod_ibge, anchor, agora)
    return n


# --------------------------------------------------------------------------- #
# Calendário de obrigações (status entregue/pendente/atrasado)
# --------------------------------------------------------------------------- #
def _calendario(
    session: Session,
    cod_ibge: str,
    esfera: str,
    populacao: int | None,
    entregas: _Entregas,
    anchor: str,
    agora: datetime,
) -> int:
    # O calendário reflete o exercício vigente (ano da âncora RREO). Anos sem RREO no
    # banco são lacuna de ingestão, não descumprimento — não são inventados aqui.
    anos = [parse_periodo(anchor).ano]
    # Índice do último período entregue por relatório (para marcar "atrasado").
    ultimo_ordinal: dict[str, int] = {}
    for (relatorio, periodo) in entregas.vigentes:
        try:
            ordinal = parse_periodo(periodo).ordinal
        except ValueError:
            continue
        ultimo_ordinal[relatorio] = max(ultimo_ordinal.get(relatorio, -1), ordinal)

    total = 0
    for ano in anos:
        for ob in rules.obrigacoes_do_ano(ano, esfera, populacao):
            entrega = entregas.entregue(ob.relatorio, ob.periodo)
            if entrega is not None:
                status = "entregue"
                entregue_em = entrega.homologada_em
                versao = entrega.versao_entrega
            else:
                try:
                    ordinal = parse_periodo(ob.periodo).ordinal
                except ValueError:
                    ordinal = -1
                atrasado = ordinal < ultimo_ordinal.get(ob.relatorio, -1)
                status, entregue_em, versao = ("atrasado" if atrasado else "pendente"), None, None
            repository.upsert_calendario(
                session,
                {
                    "id": uuid.uuid4(),
                    "cod_ibge": cod_ibge,
                    "relatorio": ob.relatorio,
                    "periodo": ob.periodo,
                    "periodicidade": ob.periodicidade,
                    "prazo": ob.prazo,
                    "status": status,
                    "entregue_em": entregue_em,
                    "versao_entrega": versao,
                    "source_ref": SourceRef(
                        relatorio=ob.relatorio, periodo=ob.periodo, versao_entrega=versao
                    ).model_dump(mode="json"),
                    "atualizado_em": agora,
                },
            )
            total += 1
    return total


# --------------------------------------------------------------------------- #
# Alertas de nova publicação (dim_entrega)
# --------------------------------------------------------------------------- #
def _alertas_publicacao(
    session: Session,
    org_id: uuid.UUID,
    cod_ibge: str,
    entregas: _Entregas,
    agora: datetime,
) -> int:
    n = 0
    for relatorio in (rules.REL_RREO, rules.REL_RGF, rules.REL_DCA, rules.REL_MSC):
        entrega = entregas.latest.get(relatorio)
        if entrega is None or entrega.cod_ibge != cod_ibge:
            continue
        rotulo = _rotulo_periodo(entrega.periodo)
        base = _ente_alerta_base(cod_ibge, org_id, agora)
        titulo = f"{relatorio} {rotulo} publicado — indicadores recalculados"
        acao = "Revise os indicadores do período; a leitura reflete a entrega mais recente."
        repository.upsert_alerta(
            session,
            {
                **base,
                "chave": f"publicacao:{relatorio}:{entrega.periodo}:{entrega.versao_entrega}",
                "categoria": "publicacao",
                "severidade": rules.SEV_INFORMATIVO,
                "prioridade": rules.prioridade(rules.SEV_INFORMATIVO, "publicacao"),
                "titulo": titulo,
                "motivo_legal": "Extrato de entregas SICONFI — nova entrega homologada (Sprint 1).",
                "acao_sugerida": acao,
                "prazo": None,
                "link": _link_relatorio(relatorio),
                "indicador": None,
                "periodo": entrega.periodo,
                "source_ref": SourceRef(
                    relatorio=relatorio,
                    periodo=entrega.periodo,
                    versao_entrega=entrega.versao_entrega,
                ).model_dump(mode="json"),
                "memoria": {"homologada_em": entrega.homologada_em.isoformat()},
            },
        )
        n += 1
    return n


def _link_relatorio(relatorio: str) -> str:
    return {
        rules.REL_RREO: "/dashboard",
        rules.REL_RGF: "/limites",
        rules.REL_DCA: "/patrimonio",
        rules.REL_MSC: "/patrimonio",
    }.get(relatorio, "/alertas")


# --------------------------------------------------------------------------- #
# Alertas de defasagem de fonte (SIOPS/SIOPE/CAPAG vs âncora RREO)
# --------------------------------------------------------------------------- #
def _alertas_defasagem(
    session: Session,
    org_id: uuid.UUID,
    cod_ibge: str,
    entregas: _Entregas,
    anchor: str,
    agora: datetime,
) -> int:
    ancora_anual = str(parse_periodo(anchor).ano)
    n = 0
    for fonte in rules.FONTES_COMPLEMENTARES:
        periodo_fonte = _latest_periodo(entregas, fonte.relatorio)
        ancora = ancora_anual if fonte.cadencia == "anual" else anchor
        defas = rules.avaliar_defasagem(fonte, periodo_fonte, ancora)
        if defas is None:
            continue  # em dia
        if defas.disponivel:
            titulo = f"{fonte.rotulo} está {defas.atraso} período(s) atrás do RREO"
            acao = (
                f"A leitura pode não refletir o dado mais recente; confira a publicação "
                f"da fonte ({fonte.rotulo})."
            )
        else:
            titulo = f"{fonte.rotulo} indisponível para o ente"
            acao = f"Sem entrega da fonte {fonte.rotulo}; a tela pode estar defasada."
        base = _ente_alerta_base(cod_ibge, org_id, agora)
        repository.upsert_alerta(
            session,
            {
                **base,
                "chave": f"defasagem:{fonte.relatorio}",
                "categoria": "defasagem_fonte",
                "severidade": defas.severidade,
                "prioridade": rules.prioridade(defas.severidade, "defasagem_fonte"),
                "titulo": titulo,
                "motivo_legal": fonte.base_legal,
                "acao_sugerida": acao,
                "prazo": None,
                "link": fonte.link,
                "indicador": None,
                "periodo": periodo_fonte,
                "source_ref": SourceRef(
                    relatorio=fonte.relatorio, periodo=periodo_fonte
                ).model_dump(mode="json"),
                "memoria": {
                    "periodo_fonte": periodo_fonte,
                    "periodo_ancora": ancora,
                    "atraso": defas.atraso,
                    "disponivel": defas.disponivel,
                },
            },
        )
        n += 1
    return n


# --------------------------------------------------------------------------- #
# Alertas de limite (Módulo 3)
# --------------------------------------------------------------------------- #
def _alertas_limite(
    session: Session,
    org_id: uuid.UUID,
    cod_ibge: str,
    anchor: str,
    agora: datetime,
) -> int:
    try:
        limites = limits_service.build_limites(session, cod_ibge, anchor)
    except AppError:
        return 0
    n = 0
    for item in limites.itens:
        severidade = rules.severidade_faixa(item.faixa)
        if severidade is None:
            # A21 (Sprint A5): a faixa não pede alerta — "adequado"/"normal" (dado existe,
            # ente em conformidade) **ou** ausência (item.faixa is None, indicador não
            # apurado neste período). Só a primeira é retificação resolvendo: fechar por
            # ausência de dado seria tratar "não sei" como "está bom", o inverso do que o
            # produto promete. Sem isto, um indicador que voltava para dentro do limite
            # numa retificação (ex.: RCL corrigida via A15) deixava o alerta antigo ativo
            # para sempre — a materialização nunca "revisitava".
            if item.faixa is not None and repository.fechar_alerta_orfao(
                session, org_id=org_id, chave=f"limite:{item.indicador}:{anchor}", agora=agora
            ):
                n += 1
            continue
        provs = limits_repo.providencias(session, indicador=item.indicador, faixa=item.faixa or "")
        prov = provs[0] if provs else None
        motivo = prov.base_legal if prov else "Limite da LRF (LC 101/2000)."
        acao = prov.texto if prov else "Acompanhar a trajetória e adotar as providências da LRF."
        base = _ente_alerta_base(cod_ibge, org_id, agora)
        repository.upsert_alerta(
            session,
            {
                **base,
                "chave": f"limite:{item.indicador}:{anchor}",
                "categoria": "limite",
                "severidade": severidade,
                "prioridade": rules.prioridade(severidade, "limite"),
                "titulo": _titulo_limite(item.indicador, item.faixa),
                "motivo_legal": motivo,
                "acao_sugerida": acao,
                "prazo": None,
                "link": "/limites",
                "indicador": item.indicador,
                "periodo": anchor,
                "source_ref": limites.source_ref.model_dump(mode="json"),
                "memoria": {
                    "valor_pct_rcl": str(item.valor_pct_rcl),
                    "teto_pct": str(item.teto_pct),
                    "faixa": item.faixa,
                },
            },
        )
        n += 1
    return n


def _titulo_limite(indicador: str, faixa: str | None) -> str:
    # O título do alerta é mais explícito que o rótulo de gráfico ("Aplicação **mínima** em
    # saúde"), por isso o mapa local; o registro canônico cobre o que faltar.
    locais = {
        "pessoal_executivo": "Despesa com pessoal (Executivo)",
        "saude_minimo": "Aplicação mínima em saúde",
        "educacao_mde": "Aplicação mínima em educação (MDE)",
        "fundeb_profissionais": "FUNDEB em profissionais da educação",
        "garantias": "Garantias concedidas",
        "aro": "Antecipação de Receita Orçamentária",
    }
    return f"{locais.get(indicador) or rotulos.rotulo(indicador)} — faixa {faixa}"


# --------------------------------------------------------------------------- #
# Risco de descumprimento dos mínimos (Sprint 25C: projeção da Sprint 11 → motor 15)
# --------------------------------------------------------------------------- #
_MINIMOS: dict[str, tuple[str, str]] = {
    # indicador da projeção -> (indicador do limite legal, rótulo)
    "saude": ("saude_minimo", "Aplicação mínima em saúde (ASPS)"),
    "educacao": ("educacao_mde", "Aplicação mínima em educação (MDE)"),
}


def _alertas_minimos(
    session: Session,
    org_id: uuid.UUID,
    cod_ibge: str,
    anchor: str,
    agora: datetime,
) -> int:
    """Sinaliza **risco** de não atingir o piso constitucional antes do fechamento.

    O mínimo é apurado no encerramento do exercício: no 6º bimestre, ficar abaixo do
    piso é descumprimento (o alerta de limite já o classifica como ``insuficiente``);
    nos bimestres anteriores, é *trajetória* — e é aí que ainda dá para corrigir. Esta
    regra consome a projeção da Sprint 11 (razão acumulada anualizada) sem recalcular
    nada, e diz explicitamente que projeção não é apuração.
    """
    bimestre = parse_periodo(anchor).indice
    if bimestre >= 6:
        # No fechamento quem fala é o alerta de limite (faixa insuficiente). Sair aqui,
        # antes de projetar: apurar saúde e educação para depois descartar custava mais
        # do que todo o resto do motor somado.
        return 0
    try:
        saude, educacao = health_edu_service.projetar_minimos(session, cod_ibge, anchor)
    except AppError:
        return 0
    projecao = {"saude": saude, "educacao": educacao}
    n = 0
    for dominio, (indicador, rotulo) in _MINIMOS.items():
        item = projecao[dominio]
        if not item.abaixo_do_minimo_projetado:
            continue
        provs = limits_repo.providencias(session, indicador=indicador, faixa="insuficiente")
        prov = provs[0] if provs else None
        base = _ente_alerta_base(cod_ibge, org_id, agora)
        repository.upsert_alerta(
            session,
            {
                **base,
                "chave": f"minimo_risco:{indicador}:{anchor}",
                "categoria": "preditivo",
                "severidade": rules.SEV_ATENCAO,
                "prioridade": rules.prioridade(rules.SEV_ATENCAO, "preditivo"),
                "titulo": (
                    f"{rotulo} — trajetória projeta {item.pct_projetado:.2f}% "
                    f"contra piso de {item.minimo_pct:.0f}%"
                ),
                "motivo_legal": (
                    prov.base_legal if prov
                    else "Piso constitucional de aplicação mínima."
                ),
                "acao_sugerida": (
                    "Projeção do fechamento a partir do acumulado até "
                    f"{_rotulo_periodo(anchor)} — não é apuração. Reforçar a execução "
                    "vinculada nos bimestres restantes; a apuração definitiva é no 6º."
                ),
                "prazo": None,
                "link": "/saude-educacao",
                "indicador": indicador,
                "periodo": anchor,
                "source_ref": item.source_ref.model_dump(mode="json"),
                "memoria": {
                    "pct_acumulado": str(item.pct_atual),
                    "pct_projetado": str(item.pct_projetado),
                    "minimo_pct": str(item.minimo_pct),
                    "valor_aplicado_projetado": str(item.valor_aplicado_projetado),
                    "valor_minimo_projetado": str(item.valor_minimo_projetado),
                    "metodo": item.metodo,
                    "apuracao_definitiva": "6º bimestre (encerramento do exercício)",
                },
            },
        )
        n += 1
    return n


# --------------------------------------------------------------------------- #
# Alertas PREDITIVOS (Sprint 14)
# --------------------------------------------------------------------------- #
def _alertas_preditivos(
    session: Session,
    org_id: uuid.UUID,
    cod_ibge: str,
    anchor: str,
    agora: datetime,
) -> int:
    n = 0
    for indicador in _PREDITIVOS:
        try:
            proj = forecast_service.build_projecao(
                session, cod_ibge, indicador, horizonte=4, persistir=False
            )
        except AppError:
            continue
        c = proj.cruzamento
        if not (c.aplicavel and c.cruza):
            continue
        base = _ente_alerta_base(cod_ibge, org_id, agora)
        repository.upsert_alerta(
            session,
            {
                **base,
                "chave": f"preditivo:{indicador}:{c.periodo_cruzamento}",
                "categoria": "preditivo",
                "severidade": rules.SEV_ATENCAO,
                "prioridade": rules.prioridade(rules.SEV_ATENCAO, "preditivo"),
                "titulo": (
                    f"{proj.descricao} deve cruzar o teto em {c.periodo_cruzamento} "
                    f"({proj.modelo})"
                ),
                "motivo_legal": (
                    "Projeção (Sprint 14) sobre série gold — trajetória cruza o teto legal "
                    f"de {c.teto_pct}% da RCL."
                ),
                "acao_sugerida": (
                    "Antecipar medidas de contenção; a projeção estatística indica risco "
                    "de descumprimento (não é número fechado)."
                ),
                "prazo": None,
                # Sprint G1: antes sempre "/previsoes", sem indicador — quem abria o alerta
                # caía numa tela de RCL genérica e tinha que reencontrar Pessoal/Dívida por
                # conta própria. PrevisoesPage lê `?indicador=` via useSearchParams.
                "link": f"/previsoes?indicador={indicador}",
                "indicador": indicador,
                "periodo": c.periodo_cruzamento,
                "source_ref": proj.source_ref.model_dump(mode="json"),
                "memoria": {
                    "modelo": proj.modelo,
                    "valor_no_cruzamento": str(c.valor_no_cruzamento),
                    "teto_pct": str(c.teto_pct),
                    "nivel_confianca": str(proj.nivel_confianca),
                },
            },
        )
        n += 1
    return n
