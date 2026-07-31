"""Cockpit Executivo Fiscal (Sprint 22) — composição, não duplicação.

O cockpit **não tem mart próprio**: ele compõe em service o que os módulos já calculam
(limites, previsão, alertas, receita/despesa/pessoal, cobertura). Assim a verdade continua
tendo um dono só — se a regra do limite muda em ``indicators``, o cockpit acompanha.

Regras de honestidade aplicadas em todas as camadas:
- comparação sem base ⇒ ``disponivel=false`` + motivo; **nunca** zero;
- severidade vem da faixa legal, nunca de adjetivo;
- todo bloco carrega ``source_ref``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError
from app.modules.alerts import service as alerts_service
from app.modules.catalog import service as catalog_service
from app.modules.dashboard.cockpit_schemas import (
    ChecagemAberta,
    CockpitQualidade,
    CockpitResponse,
    CockpitResumo,
    ComparacaoItem,
    ComponenteVariacao,
    CriticoItem,
    ExplicadorItem,
    MudancaRelevante,
    PontoProjetado,
    PontoSerie,
    QualidadeFonte,
    RiscoItem,
    TendenciaItem,
)
from app.modules.dashboard.service import COR_POR_FAIXA
from app.modules.expense import repository as expense_repo
from app.modules.expense.classificacao import SENTINELA
from app.modules.expense.models import DimFuncao
from app.modules.forecast import service as forecast_service
from app.modules.indicators import repository as indicators_repo
from app.modules.indicators import rotulos
from app.modules.ingestion import repository as ingestion_repo
from app.modules.ingestion.connectors.registry import FONTE_META
from app.modules.limits import repository as limits_repo
from app.modules.limits import service as limits_service
from app.modules.personnel import repository as personnel_repo
from app.modules.personnel.models import DimPoderOrgao
from app.modules.quality import service as quality_service
from app.modules.revenue import repository as revenue_repo
from app.modules.revenue.models import DimOrigemReceita
from app.shared import periodo as periodo_util
from app.shared.source_ref import SourceRef

# Rótulos curtos do cockpit (a tela é densa); tudo que não estiver aqui cai no registro
# canônico, que nunca devolve o código cru — era assim que "investimento_rcl" chegava à tela.
_ROTULO = {
    "pessoal_executivo": "Pessoal (Executivo)",
    "divida_consolidada_liquida": "Dívida Consolidada Líquida",
    "saude_minimo": "Saúde (mínimo)",
    "educacao_mde": "Educação (MDE)",
}


def _rotulo_indicador(codigo: str, padrao: str | None = None) -> str:
    return _ROTULO.get(codigo) or rotulos.rotulo(codigo, padrao=padrao)


_SEVERIDADE = {"verde": 0, "cinza": 0, "amarelo": 1, "laranja": 2, "vermelho": 3}
_FAROL_POR_SEVERIDADE = {0: "conforme", 1: "alerta", 2: "prudencial", 3: "critico"}
# Fontes que servem o cockpit — a camada de qualidade responde "posso confiar nisto?".
_FONTES_DO_COCKPIT = ("siconfi_rreo", "siconfi_rgf")
_TOP_COMPONENTES = 3
_ZERO = Decimal(0)


def _pct_delta(atual: Decimal | None, anterior: Decimal | None) -> Decimal | None:
    if atual is None or anterior is None or anterior == 0:
        return None
    return (atual - anterior) / abs(anterior) * Decimal(100)


def _source_rreo(periodo: str, versao: str | None) -> SourceRef:
    return SourceRef(relatorio="RREO", periodo=periodo, versao_entrega=versao)


# --- 1/2. Resumo e críticos (a partir do monitor de limites) ----------------
def _limites(session: Session, cod_ibge: str, periodo: str) -> Any:
    return limits_service.build_limites(session, cod_ibge, periodo)


def _criticos(limites: Any) -> list[CriticoItem]:
    itens: list[CriticoItem] = []
    for it in limites.itens:
        cor = COR_POR_FAIXA.get(it.faixa, "cinza")
        itens.append(
            CriticoItem(
                indicador=it.indicador,
                rotulo=_rotulo_indicador(it.indicador),
                sentido=it.sentido,
                valor_pct=it.valor_pct_rcl,
                valor_rs=it.valor_rs,
                limite_pct=it.teto_pct,
                faixa=it.faixa,
                cor=cor,
                distancia_pp=it.distancia_teto,
                denominador=getattr(it, "denominador", "rcl"),
                source_ref=limites.source_ref,
            )
        )
    # Mais severo primeiro: o gestor lê de cima para baixo.
    return sorted(itens, key=lambda i: _SEVERIDADE.get(i.cor, 0), reverse=True)


def _mudancas(
    session: Session, cod_ibge: str, periodo: str, criticos: list[CriticoItem]
) -> list[MudancaRelevante]:
    """Δ de cada indicador contra o período anterior — o "o que mudou" do resumo."""
    anterior = periodo_util.anterior(periodo)
    if anterior is None:
        return []
    versao_ant = indicators_repo.resolve_versao_rreo(
        session, cod_ibge=cod_ibge, periodo=anterior
    )
    if versao_ant is None:
        return []
    mudancas: list[MudancaRelevante] = []
    for c in criticos:
        mart_ant = indicators_repo.get_mart_indicador(
            session,
            cod_ibge=cod_ibge,
            periodo=anterior,
            indicador=c.indicador,
            versao_entrega=versao_ant,
        )
        if mart_ant is None:
            continue
        delta = (
            c.valor_pct - mart_ant.valor_pct_rcl
            if c.valor_pct is not None and mart_ant.valor_pct_rcl is not None
            else None
        )
        mudancas.append(
            MudancaRelevante(
                indicador=c.indicador,
                rotulo=c.rotulo,
                valor_atual=c.valor_pct,
                valor_anterior=mart_ant.valor_pct_rcl,
                delta_pp=delta,
                faixa_atual=c.faixa,
                faixa_anterior=mart_ant.faixa,
                mudou_de_faixa=bool(c.faixa and mart_ant.faixa and c.faixa != mart_ant.faixa),
                periodo_anterior=anterior,
            )
        )
    # Prioriza mudança de faixa; depois a maior variação absoluta.
    return sorted(
        mudancas,
        key=lambda m: (m.mudou_de_faixa, abs(m.delta_pp or _ZERO)),
        reverse=True,
    )


def _resumo(
    criticos: list[CriticoItem],
    mudancas: list[MudancaRelevante],
    n_alertas: int,
    n_criticos: int,
    source: SourceRef,
) -> CockpitResumo:
    severidades = [_SEVERIDADE.get(c.cor, 0) for c in criticos if c.faixa is not None]
    farol = _FAROL_POR_SEVERIDADE[max(severidades)] if severidades else "sem_dados"
    cor = COR_POR_FAIXA.get(
        {"conforme": "normal", "alerta": "alerta", "prudencial": "prudencial",
         "critico": "excedido"}.get(farol),
        "cinza",
    )
    return CockpitResumo(
        farol=farol,
        cor=cor,
        indicadores_avaliados=len([c for c in criticos if c.faixa is not None]),
        n_alertas=n_alertas,
        n_alertas_criticos=n_criticos,
        mudancas_relevantes=mudancas[:3],
        source_ref=source,
    )


# --- 3. Tendências (reusa a Sprint 14) -------------------------------------
# O catálogo de previsão tem chaves próprias ('pessoal'/'divida'), distintas das chaves
# de limite legal ('pessoal_executivo'/'divida_consolidada_liquida'). Sem esse mapa o
# cockpit pediria um indicador inexistente e mostraria "indisponível" para tudo.
_INDICADORES_TENDENCIA = ("pessoal", "divida", "rcl")


def _tendencias(
    session: Session, cod_ibge: str, *, as_of: datetime | None
) -> list[TendenciaItem]:
    itens: list[TendenciaItem] = []
    for indicador in _INDICADORES_TENDENCIA:
        try:
            proj = forecast_service.build_projecao(
                session, cod_ibge, indicador, horizonte=4, as_of=as_of, persistir=False
            )
        except AppError as exc:
            itens.append(
                TendenciaItem(
                    indicador=indicador,
                    rotulo=_rotulo_indicador(indicador),
                    unidade="",
                    disponivel=False,
                    motivo_indisponivel=exc.detail or exc.title,
                )
            )
            continue
        itens.append(
            TendenciaItem(
                indicador=indicador,
                rotulo=_rotulo_indicador(indicador, proj.descricao),
                unidade=proj.unidade,
                modelo=proj.modelo,
                historico=[PontoSerie(periodo=p.periodo, valor=p.valor) for p in proj.historico],
                projecao=[
                    PontoProjetado(
                        periodo=p.periodo_alvo,
                        previsto=p.valor_previsto,
                        ic_inferior=p.ic_inferior,
                        ic_superior=p.ic_superior,
                    )
                    for p in proj.projecao
                ],
                limite_pct=proj.cruzamento.teto_pct,
                cruzamento_periodo=proj.cruzamento.periodo_cruzamento,
                source_ref=proj.historico[-1].source_ref if proj.historico else None,
            )
        )
    return itens


# --- 4. Explicadores (Δ real por componente) -------------------------------
def _rotulos(session: Session, modelo: Any, codigos: Iterable[str]) -> dict[str, str]:
    """``{código: descrição}`` das dimensões, para o explicador falar em português.

    O cockpit vinha exibindo ``10``, ``12`` e ``ENTE.EXEC`` porque montava o componente
    com o código nos dois campos. O código é chave, não rótulo: quem lê a tela quer
    "Saúde" e "Educação", e o nome já está na dimensão — faltava perguntar.
    """
    alvo = [c for c in dict.fromkeys(codigos) if c]
    if not alvo:
        return {}
    linhas = session.execute(
        select(modelo.codigo, modelo.descricao).where(modelo.codigo.in_(alvo))
    ).all()
    return {str(codigo): str(descricao) for codigo, descricao in linhas if descricao}


def _com_rotulo(
    mapa: dict[str, tuple[str, Decimal]], rotulos: Mapping[str, str]
) -> dict[str, tuple[str, Decimal]]:
    """Troca o rótulo pelo nome da dimensão; sem nome, o código continua valendo —
    é feio, mas é honesto, e melhor do que apagar a linha."""
    return {cod: (rotulos.get(cod, desc), valor) for cod, (desc, valor) in mapa.items()}


def _top_variacoes(
    atual: Mapping[str, tuple[str, Decimal | None]],
    anterior: Mapping[str, tuple[str, Decimal | None]],
) -> list[ComponenteVariacao]:
    """Top componentes por |Δ| absoluto, com atual/anterior/Δabs/Δ%."""
    comps: list[ComponenteVariacao] = []
    vazio: tuple[str, Decimal | None] = ("", None)
    for codigo in set(atual) | set(anterior):
        desc_a, val_a = atual.get(codigo, vazio)
        desc_b, val_b = anterior.get(codigo, vazio)
        if val_a is None and val_b is None:
            continue
        delta = (val_a or _ZERO) - (val_b or _ZERO)
        comps.append(
            ComponenteVariacao(
                codigo=codigo,
                descricao=desc_a or desc_b,
                atual=val_a,
                anterior=val_b,
                delta_abs=delta,
                delta_pct=_pct_delta(val_a, val_b),
            )
        )
    return sorted(comps, key=lambda c: abs(c.delta_abs or _ZERO), reverse=True)[:_TOP_COMPONENTES]


def _indisponivel(
    dimensao: str, rotulo: str, medida: str, periodo: str, motivo: str
) -> ExplicadorItem:
    return ExplicadorItem(
        dimensao=dimensao, rotulo=rotulo, medida=medida, periodo_atual=periodo,
        disponivel=False, motivo_indisponivel=motivo,
    )


def _explicador_receita(
    session: Session, cod_ibge: str, periodo: str, anterior: str | None
) -> ExplicadorItem:
    rotulo, medida, dim = "Receita por origem", "arrecadado_acum", "receita_origem"
    if anterior is None:
        return _indisponivel(dim, rotulo, medida, periodo, "Sem período anterior comparável.")
    v_atual = indicators_repo.resolve_versao_rreo(session, cod_ibge=cod_ibge, periodo=periodo)
    v_ant = indicators_repo.resolve_versao_rreo(session, cod_ibge=cod_ibge, periodo=anterior)
    if v_atual is None or v_ant is None:
        return _indisponivel(dim, rotulo, medida, periodo, f"Sem RREO vigente em {anterior}.")

    def mapa(per: str, versao: str) -> dict[str, tuple[str, Decimal]]:
        out: dict[str, tuple[str, Decimal]] = {}
        for f in revenue_repo.list_fatos(
            session, cod_ibge=cod_ibge, periodo=per, versao_entrega=versao
        ):
            if f.arrecadado_acum is not None:
                out[f.origem_codigo] = (f.origem_codigo, Decimal(f.arrecadado_acum))
        return out

    atual, ant = mapa(periodo, v_atual), mapa(anterior, v_ant)
    if not atual or not ant:
        return _indisponivel(
            dim, rotulo, medida, periodo, "Receita não materializada nos dois períodos."
        )
    rotulos = _rotulos(session, DimOrigemReceita, [*atual, *ant])
    atual, ant = _com_rotulo(atual, rotulos), _com_rotulo(ant, rotulos)
    return ExplicadorItem(
        dimensao=dim, rotulo=rotulo, medida=medida,
        periodo_atual=periodo, periodo_anterior=anterior,
        componentes=_top_variacoes(atual, ant),
        source_ref=SourceRef(
            relatorio="RREO", anexo="Anexo 01", periodo=periodo, versao_entrega=v_atual
        ),
    )


def _explicador_despesa(
    session: Session, cod_ibge: str, periodo: str, anterior: str | None
) -> ExplicadorItem:
    rotulo, medida, dim = "Despesa por função", "empenhado", "despesa_funcao"
    if anterior is None:
        return _indisponivel(dim, rotulo, medida, periodo, "Sem período anterior comparável.")
    v_atual = indicators_repo.resolve_versao_rreo(session, cod_ibge=cod_ibge, periodo=periodo)
    v_ant = indicators_repo.resolve_versao_rreo(session, cod_ibge=cod_ibge, periodo=anterior)
    if v_atual is None or v_ant is None:
        return _indisponivel(dim, rotulo, medida, periodo, f"Sem RREO vigente em {anterior}.")

    def mapa(per: str, versao: str) -> dict[str, tuple[str, Decimal]]:
        out: dict[str, tuple[str, Decimal]] = {}
        for f in expense_repo.list_fatos(
            session, cod_ibge=cod_ibge, periodo=per, versao_entrega=versao
        ):
            # A tabela guarda os dois eixos; o de função é o que traz a sentinela '*'
            # na natureza. Sem esse filtro somaríamos a mesma despesa duas vezes.
            if f.natureza_codigo != SENTINELA or f.empenhado is None:
                continue
            out[f.funcao_codigo] = (f.funcao_codigo, Decimal(f.empenhado))
        return out

    atual, ant = mapa(periodo, v_atual), mapa(anterior, v_ant)
    if not atual or not ant:
        return _indisponivel(
            dim, rotulo, medida, periodo, "Despesa não materializada nos dois períodos."
        )
    rotulos = _rotulos(session, DimFuncao, [*atual, *ant])
    atual, ant = _com_rotulo(atual, rotulos), _com_rotulo(ant, rotulos)
    return ExplicadorItem(
        dimensao=dim, rotulo=rotulo, medida=medida,
        periodo_atual=periodo, periodo_anterior=anterior,
        componentes=_top_variacoes(atual, ant),
        source_ref=SourceRef(
            relatorio="RREO", anexo="Anexo 02", periodo=periodo, versao_entrega=v_atual
        ),
    )


def _explicador_pessoal(session: Session, cod_ibge: str, periodo_rgf: str | None) -> ExplicadorItem:
    rotulo, medida, dim = "Pessoal por poder", "despesa_liquida", "pessoal_poder"
    base = periodo_rgf or ""
    if periodo_rgf is None:
        return _indisponivel(dim, rotulo, medida, base, "Ente sem RGF vigente.")
    anterior = periodo_util.anterior(periodo_rgf)
    v_atual = ingestion_repo.resolve_versao(
        session, cod_ibge=cod_ibge, relatorio="RGF", periodo=periodo_rgf
    )
    v_ant = (
        ingestion_repo.resolve_versao(
            session, cod_ibge=cod_ibge, relatorio="RGF", periodo=anterior
        )
        if anterior
        else None
    )
    if v_atual is None or v_ant is None or anterior is None:
        return _indisponivel(dim, rotulo, medida, base, "Sem RGF anterior para comparar.")

    def mapa(per: str, versao: str) -> dict[str, tuple[str, Decimal]]:
        return {
            f.poder_codigo: (f.poder_codigo, Decimal(f.despesa_liquida))
            for f in personnel_repo.list_fatos(
                session, cod_ibge=cod_ibge, periodo=per, versao_entrega=versao
            )
            if f.despesa_liquida is not None
        }

    atual, ant = mapa(periodo_rgf, v_atual), mapa(anterior, v_ant)
    if not atual or not ant:
        return _indisponivel(
            dim, rotulo, medida, base, "Pessoal não materializado nos dois períodos."
        )
    rotulos = _rotulos(session, DimPoderOrgao, [*atual, *ant])
    atual, ant = _com_rotulo(atual, rotulos), _com_rotulo(ant, rotulos)
    return ExplicadorItem(
        dimensao=dim, rotulo=rotulo, medida=medida,
        periodo_atual=periodo_rgf, periodo_anterior=anterior,
        componentes=_top_variacoes(atual, ant),
        source_ref=SourceRef(
            relatorio="RGF", anexo="Anexo 01", periodo=periodo_rgf, versao_entrega=v_atual
        ),
    )


# --- 5. Comparações --------------------------------------------------------
def _comparacao_mart(
    session: Session, cod_ibge: str, indicador: str, atual: Decimal | None,
    periodo_base: str | None, base: str, rotulo: str,
) -> ComparacaoItem:
    """Compara o indicador com o mesmo indicador num período-base do próprio ente."""
    if periodo_base is None:
        return ComparacaoItem(
            base=base, rotulo=rotulo, indicador=indicador, disponivel=False,
            motivo_indisponivel="Período-base indefinido para este período.",
        )
    versao = indicators_repo.resolve_versao_rreo(
        session, cod_ibge=cod_ibge, periodo=periodo_base
    )
    mart = (
        indicators_repo.get_mart_indicador(
            session, cod_ibge=cod_ibge, periodo=periodo_base,
            indicador=indicador, versao_entrega=versao,
        )
        if versao
        else None
    )
    if mart is None or mart.valor_pct_rcl is None:
        return ComparacaoItem(
            base=base, rotulo=rotulo, indicador=indicador, disponivel=False,
            motivo_indisponivel=(
                f"Sem base de comparação: {indicador} não apurado em {periodo_base}."
            ),
            referencia=periodo_base,
        )
    delta = atual - mart.valor_pct_rcl if atual is not None else None
    return ComparacaoItem(
        base=base, rotulo=rotulo, indicador=indicador, disponivel=True,
        valor_atual=atual, valor_base=mart.valor_pct_rcl,
        delta_abs=delta, delta_pct=_pct_delta(atual, mart.valor_pct_rcl),
        referencia=periodo_base,
        source_ref=_source_rreo(periodo_base, versao),
    )


def _comparacao_coorte(
    session: Session, cod_ibge: str, indicador: str, periodo: str, atual: Decimal | None
) -> ComparacaoItem:
    """Mediana da coorte no mesmo período (comparação externa)."""
    rotulo = "Mediana da coorte"
    valores = limits_repo.valores_indicador_periodo(session, indicador=indicador, periodo=periodo)
    pares = [v for v in valores if v is not None]
    if len(pares) < 3:
        return ComparacaoItem(
            base="coorte_mediana", rotulo=rotulo, indicador=indicador, disponivel=False,
            motivo_indisponivel="Sem base de comparação: menos de 3 entes apurados no período.",
            referencia=periodo,
        )
    ordenados = sorted(pares)
    meio = len(ordenados) // 2
    mediana = (
        ordenados[meio]
        if len(ordenados) % 2
        else (ordenados[meio - 1] + ordenados[meio]) / Decimal(2)
    )
    return ComparacaoItem(
        base="coorte_mediana", rotulo=rotulo, indicador=indicador, disponivel=True,
        valor_atual=atual, valor_base=mediana,
        delta_abs=(atual - mediana) if atual is not None else None,
        delta_pct=_pct_delta(atual, mediana),
        referencia=f"{len(ordenados)} entes em {periodo}",
        source_ref=SourceRef(relatorio="MART_INDICADOR", periodo=periodo),
    )


def _comparacoes(
    session: Session, cod_ibge: str, periodo: str, criticos: list[CriticoItem]
) -> list[ComparacaoItem]:
    principal_ind = next((c for c in criticos if c.faixa is not None), None)
    if principal_ind is None:
        return []
    ind, atual = principal_ind.indicador, principal_ind.valor_pct
    return [
        _comparacao_mart(
            session, cod_ibge, ind, atual, periodo_util.anterior(periodo),
            "periodo_anterior", "Período anterior",
        ),
        _comparacao_mart(
            session, cod_ibge, ind, atual,
            periodo_util.mesmo_periodo_exercicio_anterior(periodo),
            "exercicio_anterior", "Mesmo período do exercício anterior",
        ),
        _comparacao_coorte(session, cod_ibge, ind, periodo, atual),
        ComparacaoItem(
            base="orcamento", rotulo="Orçamento (LDO/meta)", indicador=ind, disponivel=False,
            motivo_indisponivel=(
                "Sem base de comparação: metas da LDO não cadastradas "
                "(op.meta_fiscal — decisão pendente do produto)."
            ),
        ),
    ]


# --- 6. Riscos -------------------------------------------------------------
def _riscos(
    session: Session, principal: Principal, cod_ibge: str
) -> tuple[list[RiscoItem], int, int]:
    try:
        fila = alerts_service.listar_fila(session, principal, escopo="ente", cod_ibge=cod_ibge)
    except AppError:
        return [], 0, 0
    riscos = [
        RiscoItem(
            id=a.id, severidade=a.severidade, categoria=a.categoria, titulo=a.titulo,
            motivo_legal=a.motivo_legal, acao_sugerida=a.acao_sugerida, prazo=a.prazo,
            link=a.link, indicador=a.indicador, periodo=a.periodo, source_ref=a.source_ref,
        )
        for a in fila.alertas
    ]
    return riscos, fila.contadores.total, fila.contadores.critico


# --- 7. Qualidade ----------------------------------------------------------
def _qualidade(session: Session, cod_ibge: str) -> CockpitQualidade:
    fontes: list[QualidadeFonte] = []
    defasagens: list[int] = []
    for fonte in _FONTES_DO_COCKPIT:
        meta = FONTE_META.get(fonte)
        linhas = limits_repo.cobertura_do_ente(session, fonte=fonte, cod_ibge=cod_ibge)
        if not linhas:
            continue
        recente = max(linhas, key=lambda x: periodo_util.ordenar_chave(x.periodo))
        retificacoes = ingestion_repo.contar_entregas(
            session,
            cod_ibge=cod_ibge,
            relatorio=(meta and FONTE_RELATORIO_LOCAL.get(fonte)) or "RREO",
            periodo=recente.periodo,
        )
        if recente.defasagem_periodos is not None:
            defasagens.append(recente.defasagem_periodos)
        fontes.append(
            QualidadeFonte(
                fonte=fonte,
                relatorio=FONTE_RELATORIO_LOCAL.get(fonte, fonte),
                cadencia=meta.cadencia if meta else "?",
                periodo_mais_recente=recente.periodo,
                defasagem_periodos=recente.defasagem_periodos,
                ultima_carga=recente.ingerido_em,
                n_registros=recente.n_registros,
                versao_entrega_vigente=recente.versao_entrega_vigente,
                retificacoes=max(retificacoes - 1, 0),
            )
        )
    maxima = max(defasagens) if defasagens else None
    abertos = quality_service.selo_do_ente(session, cod_ibge)
    falhas = [c for c in abertos if c.status == "falha"]
    avisos = [c for c in abertos if c.status == "aviso"]
    observacao = None
    if not fontes:
        observacao = "Sem cobertura materializada para este ente — rode o refresh da cobertura."
    elif falhas:
        observacao = (
            f"{len(falhas)} verificação(ões) de qualidade em falha sobre estes números — "
            "veja o painel na Central de Dados antes de usar o dado em decisão."
        )
    return CockpitQualidade(
        fontes=fontes,
        defasagem_maxima=maxima,
        # Um check em falha derruba a confiabilidade mesmo com a fonte em dia: dado
        # atual e errado é pior que dado velho e correto.
        confiavel=bool(fontes) and (maxima is None or maxima <= 2) and not falhas,
        observacao=observacao,
        checks_abertos=[
            ChecagemAberta(
                check_codigo=c.check_codigo,
                rotulo=c.rotulo,
                status=c.status,
                fonte=c.fonte,
                periodo=c.periodo,
                diferenca=c.diferenca,
                tolerancia=c.tolerancia,
                motivo=(c.detalhe or {}).get("motivo"),
            )
            for c in abertos
        ],
        n_checks_falha=len(falhas),
        n_checks_aviso=len(avisos),
    )


FONTE_RELATORIO_LOCAL = {"siconfi_rreo": "RREO", "siconfi_rgf": "RGF"}


# --- Composição ------------------------------------------------------------
def build_cockpit(
    session: Session,
    principal: Principal,
    cod_ibge: str,
    periodo: str,
    *,
    as_of: datetime | None = None,
) -> CockpitResponse:
    """Monta as 7 camadas do cockpit para o ente/período."""
    ente = catalog_service.refresh_dim_ente(session, cod_ibge)
    versao = indicators_repo.resolve_versao_rreo(
        session, cod_ibge=cod_ibge, periodo=periodo, as_of=as_of
    )
    source = _source_rreo(periodo, versao)

    limites = _limites(session, cod_ibge, periodo)
    criticos = _criticos(limites)
    mudancas = _mudancas(session, cod_ibge, periodo, criticos)
    riscos, n_alertas, n_criticos = _riscos(session, principal, cod_ibge)

    # O RGF correspondente ancora os explicadores de pessoal.
    entregas_rgf = [
        e.periodo
        for e in catalog_service.repository.periodos_com_dado(
            session, cod_ibge=cod_ibge, relatorio="RGF"
        )
    ]
    periodo_rgf = periodo_util.mais_recente(entregas_rgf)
    anterior = periodo_util.anterior(periodo)

    return CockpitResponse(
        cod_ibge=cod_ibge,
        nome=ente.nome if ente else None,
        esfera=ente.esfera if ente else None,
        periodo=periodo,
        as_of=as_of,
        resumo=_resumo(criticos, mudancas, n_alertas, n_criticos, source),
        criticos=criticos,
        tendencias=_tendencias(session, cod_ibge, as_of=as_of),
        explicadores=[
            _explicador_receita(session, cod_ibge, periodo, anterior),
            _explicador_despesa(session, cod_ibge, periodo, anterior),
            _explicador_pessoal(session, cod_ibge, periodo_rgf),
        ],
        comparacoes=_comparacoes(session, cod_ibge, periodo, criticos),
        riscos=riscos,
        qualidade=_qualidade(session, cod_ibge),
        source_ref=source,
    )
