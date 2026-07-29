"""Regras da Receita (Módulo 4): materialização gold, drill §6.1 e indicadores derivados.

O fato registra **exatamente** o que o RREO Anexo 01 informou por natureza (inclusive
subtotais); nós sem linha própria são agregados dos filhos na leitura. Divergências
entre agregações — e entre RREO e transferências externas (1B) — são **sinalizadas
como qualidade de dado**, nunca corrigidas: o valor oficial do RREO permanece.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.catalog import service as catalog_service
from app.modules.indicators import repository as indicators_repo
from app.modules.indicators import serie_ajuste
from app.modules.indicators import service as indicators_service
from app.modules.indicators.schemas import SerieAjuste
from app.modules.revenue import natureza, repository
from app.modules.revenue.models import DimOrigemReceita, FatoReceita
from app.modules.revenue.schemas import (
    ComparacaoOut,
    ConciliacaoItem,
    ConciliacaoOut,
    DependenciaOut,
    DependenciaResumo,
    InconsistenciaAgregacao,
    MemoriaReceita,
    RealizacaoItem,
    RealizacaoOut,
    ReceitaDetalhe,
    ReceitaTotais,
    SerieReceitaItem,
    TransferenciaTop,
)
from app.shared.envelope import DrillEnvelope, Measures
from app.shared.hierarchy import HierarchyNode, build_drill_envelope, make_path
from app.shared.source_ref import SourceRef

_ANEXO = "Anexo 01"
_TOLERANCIA_PCT = Decimal("1")  # divergência de conciliação tolerada (qualidade de dado)
_TOLERANCIA_AGREGACAO = Decimal("0.01")  # centavos, na verificação nó × soma dos filhos
_PERIODO_BIMESTRAL_RE = re.compile(r"^(\d{4})-B([1-6])$")

_OBSERVACAO_CONCILIACAO = (
    "Divergência sinalizada como qualidade de dado: o valor oficial do RREO "
    "permanece inalterado; a fonte externa serve de contraprova."
)

# Termos de casamento do lado RREO. O ente pode abrir a transferência em linha própria
# ou parar na espécie; nesse caso comparamos com o agregado que **necessariamente** a
# contém (contenção, não equivalência).
_TERMOS_FPM = ("FPM", "FUNDO DE PARTICIPACAO DOS MUNICIPIOS")
_AGREGADO_UNIAO = ("TRANSFERENCIAS DA UNIAO",)
_AGREGADO_ESTADOS = ("TRANSFERENCIAS DOS ESTADOS",)

Medidas = dict[str, Decimal]


def _source_ref(periodo: str, versao: str) -> SourceRef:
    return SourceRef(relatorio="RREO", anexo=_ANEXO, periodo=periodo, versao_entrega=versao)


def _resolve_versao(
    session: Session, cod_ibge: str, periodo: str, as_of: datetime | None
) -> str:
    versao = indicators_repo.resolve_versao_rreo(
        session, cod_ibge=cod_ibge, periodo=periodo, as_of=as_of
    )
    if versao is None:
        raise AppError(
            status=404, title="RREO ausente",
            detail=f"Sem RREO vigente para {cod_ibge} em {periodo}.",
        )
    return versao


# --- materialização silver → gold ---
def materializar_receita(
    session: Session, cod_ibge: str, periodo: str, versao: str
) -> list[FatoReceita]:
    """ETL do RREO Anexo 01 → ``dim_origem_receita`` + ``fato_receita`` (idempotente).

    A hierarquia é derivada da **ordem** (``linha_seq``) + caixa do texto (§ natureza);
    o ``codigo`` do nó é o ``cod_conta`` (slug estável do STN). Só linhas de **receita**
    (colunas de previsão/arrecadação) entram; despesa/totais/intra ficam de fora.
    """
    agregado: dict[str, Medidas] = {}
    linhas_arvore: dict[str, tuple[int, str]] = {}
    for linha in repository.read_anexo01(
        session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
    ):
        cod = linha.cod_conta
        medida = natureza.classificar_coluna(linha.coluna)
        if not cod or medida is None:
            continue
        medidas = agregado.setdefault(cod, {})
        medidas[medida] = medidas.get(medida, Decimal(0)) + Decimal(linha.valor or 0)
        seq = linha.linha_seq if linha.linha_seq is not None else 10**9
        if cod not in linhas_arvore or seq < linhas_arvore[cod][0]:
            linhas_arvore[cod] = (seq, linha.conta or cod)

    nos = natureza.construir_arvore([(s, c, d) for c, (s, d) in linhas_arvore.items()])
    nos_por_cod = {n.codigo: n for n in nos}

    # Dimensão: pais antes dos filhos (construir_arvore já retorna nessa ordem) → FK/path ok.
    path_por: dict[str, str] = {}
    for n in nos:
        parent_path = path_por.get(n.parent_codigo) if n.parent_codigo else None
        path = make_path(parent_path, n.codigo)
        path_por[n.codigo] = path
        repository.upsert_origem(
            session,
            {
                "codigo": n.codigo,
                "descricao": n.descricao,
                "parent_codigo": n.parent_codigo,
                "nivel": n.nivel,
                "path": path,
            },
            sobrescrever_descricao=True,
        )

    # Fato: só nós da árvore (totais/intra são descartados; valor oficial, sem invenção).
    for codigo, medidas in agregado.items():
        if codigo not in nos_por_cod:
            continue
        repository.upsert_fato(
            session,
            {
                "cod_ibge": cod_ibge,
                "periodo": periodo,
                "origem_codigo": codigo,
                "versao_entrega": versao,
                **{m: medidas.get(m) for m in natureza.MEDIDAS},
            },
        )
    return repository.list_fatos(
        session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
    )


def _ensure_gold(
    session: Session, cod_ibge: str, periodo: str, versao: str
) -> list[FatoReceita]:
    fatos = repository.list_fatos(
        session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
    )
    return fatos if fatos else materializar_receita(session, cod_ibge, periodo, versao)


# --- agregação (nó com linha própria = valor oficial; sem linha = soma dos filhos) ---
def _medidas_efetivas(
    origens: list[DimOrigemReceita], fatos: list[FatoReceita]
) -> dict[str, Medidas]:
    proprias: dict[str, Medidas] = {
        f.origem_codigo: {
            m: Decimal(v)
            for m in natureza.MEDIDAS
            if (v := getattr(f, m)) is not None
        }
        for f in fatos
    }
    filhos: dict[str | None, list[str]] = {}
    for o in origens:
        filhos.setdefault(o.parent_codigo, []).append(o.codigo)

    efetivas: dict[str, Medidas] = {}

    def resolver(codigo: str) -> Medidas:
        if codigo in efetivas:
            return efetivas[codigo]
        if codigo in proprias:
            efetivas[codigo] = proprias[codigo]
            return efetivas[codigo]
        soma: Medidas = {}
        for filho in filhos.get(codigo, []):
            for m, v in resolver(filho).items():
                soma[m] = soma.get(m, Decimal(0)) + v
        efetivas[codigo] = soma
        return soma

    for o in origens:
        resolver(o.codigo)
    return efetivas


def _com_ancestrais(
    session: Session, codigos: set[str]
) -> list[DimOrigemReceita]:
    """Carrega os nós de ``codigos`` + toda a cadeia de ancestrais (via ``parent_codigo``)."""
    carregados: dict[str, DimOrigemReceita] = {}
    pendentes = set(codigos)
    while pendentes:
        novos = [
            o for o in repository.list_origens(session, pendentes) if o.codigo not in carregados
        ]
        if not novos:
            break
        for o in novos:
            carregados[o.codigo] = o
        pendentes = {
            o.parent_codigo for o in novos if o.parent_codigo and o.parent_codigo not in carregados
        }
    return list(carregados.values())


def _carregar_arvore(
    session: Session, cod_ibge: str, periodo: str, versao: str
) -> tuple[list[HierarchyNode], dict[str, Medidas]]:
    """Nós (restritos ao que o ente reportou + ancestrais) e medidas efetivas."""
    fatos = _ensure_gold(session, cod_ibge, periodo, versao)
    origens = _com_ancestrais(session, {f.origem_codigo for f in fatos})
    medidas = _medidas_efetivas(origens, fatos)
    nodes = [
        HierarchyNode(
            codigo=o.codigo, descricao=o.descricao, parent_codigo=o.parent_codigo,
            nivel=o.nivel, path=o.path,
        )
        for o in origens
    ]
    return nodes, medidas


def _totais(nodes: list[HierarchyNode], medidas: dict[str, Medidas]) -> ReceitaTotais:
    """Totais do ente = soma das categorias econômicas (raízes da hierarquia)."""
    soma: Medidas = {}
    for node in nodes:
        if node.parent_codigo is not None:
            continue
        for m, v in medidas.get(node.codigo, {}).items():
            soma[m] = soma.get(m, Decimal(0)) + v
    return ReceitaTotais(**{m: soma.get(m) for m in natureza.MEDIDAS})


def _pct(parte: Decimal | None, todo: Decimal | None) -> Decimal | None:
    if parte is None or not todo:
        return None
    return parte / todo * Decimal(100)


def _dependencia(nodes: list[HierarchyNode], medidas: dict[str, Medidas]) -> DependenciaResumo:
    """Própria × transferida sobre o arrecadado acumulado, sem dupla contagem.

    Percorre só as **raízes das origens transferidas** (``1.7``/``2.4``); o restante
    do total é receita própria.
    """
    total = _totais(nodes, medidas).arrecadado_acum or Decimal(0)
    transferida = Decimal(0)
    for node in nodes:
        if natureza.is_transferida_origem(node.codigo) and (
            node.parent_codigo is None or not natureza.is_transferida_origem(node.parent_codigo)
        ):
            transferida += medidas.get(node.codigo, {}).get("arrecadado_acum", Decimal(0))
    propria = total - transferida
    return DependenciaResumo(
        propria=propria,
        transferida=transferida,
        total=total,
        pct_propria=_pct(propria, total),
        pct_transferida=_pct(transferida, total),
    )


def _rcl_12m(
    session: Session, cod_ibge: str, periodo: str, as_of: datetime | None
) -> Decimal | None:
    """RCL da Sprint 2, reusada como contexto — ausência não bloqueia a tela de receita."""
    try:
        return indicators_service.obter_fato_rcl(session, cod_ibge, periodo, as_of=as_of).rcl_12m
    except AppError:
        return None


# --- endpoints ---
def build_arvore(
    session: Session,
    cod_ibge: str,
    periodo: str,
    node: str | None,
    *,
    as_of: datetime | None = None,
) -> DrillEnvelope:
    """Drill DOWN/UP da natureza da receita (§6.1)."""
    versao = _resolve_versao(session, cod_ibge, periodo, as_of)
    nodes, medidas = _carregar_arvore(session, cod_ibge, periodo, versao)
    if node is not None and not any(n.codigo == node for n in nodes):
        raise AppError(
            status=404, title="Origem inexistente",
            detail=f"Origem de receita '{node}' sem dado para {cod_ibge} em {periodo}.",
        )
    measures_map: dict[str, Measures] = {c: dict(m) for c, m in medidas.items()}
    return build_drill_envelope(
        nodes, node, period=periodo, source_ref=_source_ref(periodo, versao),
        node_measures=measures_map,
    )


def _serie(
    session: Session, cod_ibge: str, base_periodo: str
) -> tuple[list[SerieReceitaItem], SerieAjuste]:
    """Série multi-exercício em nominal + real (IPCA) + per capita, a preços de ``base``."""
    serie: list[SerieReceitaItem] = []
    for periodo in repository.distinct_periodos_fato(session, cod_ibge=cod_ibge):
        versao = indicators_repo.resolve_versao_rreo(
            session, cod_ibge=cod_ibge, periodo=periodo
        )
        if versao is None:
            continue
        nodes, medidas = _carregar_arvore(session, cod_ibge, periodo, versao)
        serie.append(
            SerieReceitaItem(
                periodo=periodo, arrecadado_acum=_totais(nodes, medidas).arrecadado_acum
            )
        )
    ajuste = serie_ajuste.calcular(
        session, cod_ibge, [s.periodo for s in serie], base_periodo
    )
    por_periodo = serie_ajuste.indexar(ajuste)
    for item in serie:
        a = por_periodo.get(item.periodo)
        item.arrecadado_real = serie_ajuste.real(item.arrecadado_acum, a)
        item.arrecadado_per_capita = serie_ajuste.per_capita(item.arrecadado_acum, a)
        item.populacao = a.populacao if a else None
    return serie, ajuste


def _comparacao(
    serie: list[SerieReceitaItem], periodo: str, atual: Decimal | None
) -> ComparacaoOut | None:
    """Mesmo bimestre do exercício anterior, quando existente na série."""
    m = _PERIODO_BIMESTRAL_RE.match(periodo)
    if m is None:
        return None
    anterior_cod = f"{int(m.group(1)) - 1}-B{m.group(2)}"
    anterior = next((s for s in serie if s.periodo == anterior_cod), None)
    if anterior is None:
        return None
    delta = None
    if atual is not None and anterior.arrecadado_acum:
        delta = (atual - anterior.arrecadado_acum) / anterior.arrecadado_acum * Decimal(100)
    return ComparacaoOut(
        periodo_anterior=anterior_cod,
        arrecadado_acum_anterior=anterior.arrecadado_acum,
        delta_pct=delta,
    )


def build_detalhe(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> ReceitaDetalhe:
    """Cabeçalho + composição (raiz) + série + comparação (Padrão de Detalhe de Indicador)."""
    versao = _resolve_versao(session, cod_ibge, periodo, as_of)
    nodes, medidas = _carregar_arvore(session, cod_ibge, periodo, versao)
    totais = _totais(nodes, medidas)
    raiz = build_drill_envelope(
        nodes, None, period=periodo,
        node_measures={c: dict(m) for c, m in medidas.items()},
    )
    serie, ajuste = _serie(session, cod_ibge, periodo)
    return ReceitaDetalhe(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        totais=totais,
        realizacao_pct=_pct(totais.arrecadado_acum, totais.previsto_atualizado),
        rcl_12m=_rcl_12m(session, cod_ibge, periodo, as_of),
        dependencia=_dependencia(nodes, medidas),
        composicao=raiz.children,
        serie=serie,
        serie_ajuste=ajuste,
        comparacao=_comparacao(serie, periodo, totais.arrecadado_acum),
        periodo_breadcrumb=catalog_service.periodo_breadcrumb(session, periodo),
        source_ref=_source_ref(periodo, versao),
    )


def _inconsistencias(
    nodes: list[HierarchyNode],
    fatos: list[FatoReceita],
    medidas: dict[str, Medidas],
) -> list[InconsistenciaAgregacao]:
    """Nós com linha própria cujo valor difere da soma (efetiva) dos filhos."""
    proprios = {f.origem_codigo: f for f in fatos}
    filhos: dict[str, list[str]] = {}
    for n in nodes:
        if n.parent_codigo is not None:
            filhos.setdefault(n.parent_codigo, []).append(n.codigo)
    achados: list[InconsistenciaAgregacao] = []
    for codigo, fato in proprios.items():
        if not filhos.get(codigo):
            continue
        for medida in ("previsto_inicial", "previsto_atualizado",
                       "arrecadado_bimestre", "arrecadado_acum"):
            valor_no = getattr(fato, medida)
            if valor_no is None:
                continue
            presentes = [
                medidas.get(c, {}) for c in filhos[codigo] if medida in medidas.get(c, {})
            ]
            if not presentes:
                continue
            soma = sum((m[medida] for m in presentes), Decimal(0))
            if abs(Decimal(valor_no) - soma) > _TOLERANCIA_AGREGACAO:
                achados.append(
                    InconsistenciaAgregacao(
                        codigo=codigo, medida=medida,
                        valor_no=Decimal(valor_no), soma_filhos=soma,
                    )
                )
    return achados


def build_memoria(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> MemoriaReceita:
    """Memória rastreável: medidas mapeadas, totais e verificação de agregação por nível."""
    versao = _resolve_versao(session, cod_ibge, periodo, as_of)
    nodes, medidas = _carregar_arvore(session, cod_ibge, periodo, versao)
    fatos = repository.list_fatos(
        session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
    )
    return MemoriaReceita(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        medidas=list(natureza.MEDIDAS),
        totais=_totais(nodes, medidas),
        formula_realizacao="realizacao_pct = arrecadado_acum / previsto_atualizado × 100",
        hierarquia="Categoria → Origem → Espécie → Rubrica → Alínea (natureza da receita)",
        inconsistencias=_inconsistencias(nodes, fatos, medidas),
        detalhes={
            "linhas_fato": len(fatos),
            "nos_hierarquia": len(nodes),
            "regra_agregacao": "nó com linha própria = valor oficial do RREO; "
            "sem linha própria = soma dos filhos",
        },
        source_ref=_source_ref(periodo, versao),
    )


def build_dependencia(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> DependenciaOut:
    """Receita própria × transferida + maiores transferências (drill para a origem)."""
    versao = _resolve_versao(session, cod_ibge, periodo, as_of)
    nodes, medidas = _carregar_arvore(session, cod_ibge, periodo, versao)
    resumo = _dependencia(nodes, medidas)
    detalhes = [
        (n, medidas.get(n.codigo, {}).get("arrecadado_acum"))
        for n in nodes
        if n.parent_codigo is not None and natureza.is_transferida_origem(n.parent_codigo)
    ]
    maiores = [
        TransferenciaTop(
            codigo=n.codigo,
            descricao=n.descricao,
            arrecadado_acum=valor,
            pct_das_transferencias=_pct(valor, resumo.transferida),
        )
        for n, valor in sorted(
            ((n, v) for n, v in detalhes if v is not None),
            key=lambda item: item[1], reverse=True,
        )[:5]
    ]
    return DependenciaOut(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        resumo=resumo,
        maiores_transferencias=maiores,
        rcl_12m=_rcl_12m(session, cod_ibge, periodo, as_of),
        source_ref=_source_ref(periodo, versao),
    )


def build_realizacao(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> RealizacaoOut:
    """Arrecadado ÷ previsto (atualizado), no total e por categoria econômica."""
    versao = _resolve_versao(session, cod_ibge, periodo, as_of)
    nodes, medidas = _carregar_arvore(session, cod_ibge, periodo, versao)
    totais = _totais(nodes, medidas)

    def item(codigo: str, descricao: str, m: Medidas) -> RealizacaoItem:
        previsto = m.get("previsto_atualizado")
        arrecadado = m.get("arrecadado_acum")
        return RealizacaoItem(
            codigo=codigo, descricao=descricao,
            previsto_atualizado=previsto, arrecadado_acum=arrecadado,
            realizacao_pct=_pct(arrecadado, previsto),
        )

    por_categoria = [
        item(n.codigo, n.descricao, medidas.get(n.codigo, {}))
        for n in sorted(nodes, key=lambda n: n.codigo)
        if n.parent_codigo is None
    ]
    return RealizacaoOut(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        total=item(
            "*", "Total da receita",
            {
                m: v
                for m, v in (
                    ("previsto_atualizado", totais.previsto_atualizado),
                    ("arrecadado_acum", totais.arrecadado_acum),
                )
                if v is not None
            },
        ),
        por_categoria=por_categoria,
        source_ref=_source_ref(periodo, versao),
    )


# --- conciliação RREO × fontes externas (1B) ---
def _parse_periodo_bimestral(periodo: str) -> tuple[int, list[int]]:
    """``2024-B6`` → (2024, meses 1..12 acumulados até o fim do bimestre)."""
    m = _PERIODO_BIMESTRAL_RE.match(periodo)
    if m is None:
        raise AppError(
            status=422, title="Período inválido",
            detail=f"Conciliação exige período bimestral 'AAAA-Bn'; recebido '{periodo}'.",
        )
    ano, bimestre = int(m.group(1)), int(m.group(2))
    return ano, list(range(1, 2 * bimestre + 1))


def _rreo_por_descricao(
    nodes: list[HierarchyNode], medidas: dict[str, Medidas], termos: tuple[str, ...]
) -> tuple[Decimal | None, list[str]]:
    """Soma do ``arrecadado_acum`` dos nós cuja descrição contém os termos, + os códigos.

    Considera apenas as **correspondências mais profundas** (descarta um nó quando um
    descendente também corresponde), evitando dupla contagem de subtotais. Devolver os
    códigos casados é o que torna o lado RREO da conciliação auditável na tela.
    """
    correspondentes = [
        n for n in nodes
        if any(t in natureza.normalizar_texto(n.descricao) for t in termos)
    ]
    if not correspondentes:
        return None, []
    parent_de = {n.codigo: n.parent_codigo for n in nodes}

    def descende_de(codigo: str, ancestral: str) -> bool:
        atual = parent_de.get(codigo)
        while atual is not None:
            if atual == ancestral:
                return True
            atual = parent_de.get(atual)
        return False

    codigos = {n.codigo for n in correspondentes}
    folhas = [
        n for n in correspondentes
        if not any(c != n.codigo and descende_de(c, n.codigo) for c in codigos)
    ]
    total = sum(
        (medidas.get(n.codigo, {}).get("arrecadado_acum", Decimal(0)) for n in folhas),
        Decimal(0),
    )
    return total, sorted(n.codigo for n in folhas)


def _janela_externa(ano: int, meses: list[int]) -> str:
    return f"{ano} · meses {meses[0]}–{meses[-1]}" if meses else str(ano)


def _agregado_de(termo: str) -> tuple[str, ...]:
    """Agregado continente de uma transferência genérica (ICMS/IPVA ⇒ cota dos estados)."""
    if any(t in termo for t in ("ICMS", "IPVA", "ESTADO")):
        return _AGREGADO_ESTADOS
    if "UNIAO" in termo or "FPM" in termo:
        return _AGREGADO_UNIAO
    return ()


def _lado_rreo(
    nodes: list[HierarchyNode],
    medidas: dict[str, Medidas],
    especificos: tuple[str, ...],
    agregados: tuple[str, ...],
) -> tuple[Decimal | None, list[str], str]:
    """Lado RREO da conciliação: linha específica ou, na falta dela, o agregado que a contém.

    O RREO Anexo 01 **não obriga** o ente a abrir FPM/FUNDEB em linha própria — Fortaleza,
    por exemplo, publica só até a espécie ("Transferências da União e de suas Entidades").
    Comparar contra o agregado não é equivalência: é **contenção** (a parte tem de caber no
    todo), e o status devolvido diz qual das duas comparações foi feita.
    """
    valor, nos = _rreo_por_descricao(nodes, medidas, especificos)
    if valor is not None:
        return valor, nos, "linha_especifica"
    if agregados:
        valor, nos = _rreo_por_descricao(nodes, medidas, agregados)
        if valor is not None:
            return valor, nos, "agregado"
    return None, [], "ausente"


def _item_conciliacao(
    transferencia: str,
    fonte: str,
    rreo: tuple[Decimal | None, list[str], str],
    externo: Decimal | None,
    *,
    tabela_externa: str,
    periodo_externo: str,
    independente: bool = True,
) -> ConciliacaoItem:
    rreo_valor, nos, base = rreo
    divergencia: Decimal | None = None
    divergencia_rs: Decimal | None = None
    participacao: Decimal | None = None
    if externo is None:
        status = "sem_dado_externo"
    elif rreo_valor is None:
        status = "sem_par_rreo"
    elif base == "agregado":
        # Contenção: a transferência é parte do agregado. Exceder o todo é erro de dado.
        if rreo_valor != 0:
            participacao = externo / rreo_valor * Decimal(100)
        status = "contido" if externo <= rreo_valor else "excede_agregado"
    else:
        divergencia_rs = externo - rreo_valor
        if rreo_valor != 0:
            divergencia = divergencia_rs / rreo_valor * Decimal(100)
        elif externo != 0:
            divergencia = Decimal(100)
        status = (
            "conciliado"
            if divergencia is not None and abs(divergencia) <= _TOLERANCIA_PCT
            else "divergente"
        )
    return ConciliacaoItem(
        transferencia=transferencia, fonte_externa=fonte,
        rreo_acum=rreo_valor, externo_acum=externo,
        divergencia_pct=divergencia, divergencia_rs=divergencia_rs, status=status,
        tabela_externa=tabela_externa, periodo_externo=periodo_externo,
        nos_rreo=nos, independente=independente,
        base_comparacao=base, participacao_no_agregado_pct=participacao,
    )


def build_conciliacao(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> ConciliacaoOut:
    """RREO × FPM/FUNDEB (e transferências genéricas) — contraprova, não correção."""
    versao = _resolve_versao(session, cod_ibge, periodo, as_of)
    ano, meses = _parse_periodo_bimestral(periodo)
    nodes, medidas = _carregar_arvore(session, cod_ibge, periodo, versao)

    janela = _janela_externa(ano, meses)
    itens = [
        _item_conciliacao(
            "FPM", "tesouro_fpm",
            _lado_rreo(nodes, medidas, _TERMOS_FPM, _AGREGADO_UNIAO),
            repository.soma_fpm(session, cod_ibge=cod_ibge, ano=ano, meses=meses),
            tabela_externa="silver.tesouro_fpm", periodo_externo=janela,
        ),
        # FUNDEB não tem agregado que o contenha sem ambiguidade (a distribuição mistura
        # cotas da União, do estado e do próprio município): sem linha própria, fica
        # explicitamente "sem par no RREO" em vez de comparar contra o agregado errado.
        _item_conciliacao(
            "FUNDEB", "fnde_fundeb_repasse",
            _lado_rreo(nodes, medidas, ("FUNDEB",), ()),
            repository.soma_fundeb(session, cod_ibge=cod_ibge, ano=ano, meses=meses),
            tabela_externa="silver.fnde_fundeb_repasse", periodo_externo=janela,
        ),
    ]
    for tipo, soma, fonte in repository.somas_transferencia_generica(
        session, cod_ibge=cod_ibge, ano=ano, meses=meses
    ):
        termo = natureza.normalizar_texto(tipo)
        itens.append(
            _item_conciliacao(
                tipo, fonte or "transferencia_generica",
                _lado_rreo(nodes, medidas, (termo,), _agregado_de(termo)),
                soma,
                tabela_externa="silver.transferencia_generica", periodo_externo=janela,
                # Derivado do próprio RREO (ICMS/IPVA cota-parte): contraprova só de
                # consistência interna, jamais fonte independente — ver Sprint 21.
                independente=not (fonte or "").startswith("derivado_rreo"),
            )
        )
    return ConciliacaoOut(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        itens=itens,
        tolerancia_pct=_TOLERANCIA_PCT,
        observacao=_OBSERVACAO_CONCILIACAO,
        source_ref=_source_ref(periodo, versao),
    )
