"""Regras da Despesa (Módulo 5): materialização gold, drill por dois eixos e estágios.

A despesa é materializada em **dois eixos** a partir do RREO — por função/subfunção
(Anexo 02) e por natureza da despesa (Anexo 01) — cada linha do fato pertencendo a um
só eixo (o outro recebe a sentinela ``'*'``). Como na receita, o nó com linha própria
guarda o **valor oficial** do anexo; nós sem linha própria são agregados dos filhos na
leitura. Divergências (nó × soma dos filhos, e entre eixos) são **sinalizadas como
qualidade de dado**, nunca corrigidas.

Invariante do domínio (LRF/Lei 4.320): ``empenhado ≥ liquidado ≥ pago``; a lacuna
``empenhado − pago`` é o **potencial de restos a pagar** do exercício.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.catalog import service as catalog_service
from app.modules.expense import classificacao as cls
from app.modules.expense import repository
from app.modules.expense.models import FatoDespesa
from app.modules.expense.repository import DIM_MODEL
from app.modules.expense.schemas import (
    ComparacaoDespesa,
    DespesaDetalhe,
    DespesaTotais,
    EstagioItem,
    EstagioPasso,
    EstagiosOut,
    ExecucaoEstagio,
    ExecucaoOut,
    InconsistenciaAgregacao,
    LacunaEstagio,
    MemoriaDespesa,
    RigidezComponente,
    RigidezOut,
    SerieDespesaItem,
    ViolacaoEstagio,
)
from app.modules.indicators import repository as indicators_repo
from app.modules.indicators import serie_ajuste
from app.modules.indicators import service as indicators_service
from app.modules.indicators.schemas import SerieAjuste
from app.shared.ausencia import ausencia_de_entrega
from app.shared.envelope import DrillEnvelope, Measures
from app.shared.hierarchy import HierarchyNode, build_drill_envelope
from app.shared.source_ref import SourceRef

_ANEXO_FUNCAO = "Anexo 02"
_ANEXO_NATUREZA = "Anexo 01"
_ANEXO_AMBOS = "Anexos 01 e 02"
_MARCA_ANEXO = {cls.EIXO_FUNCAO: "02", cls.EIXO_NATUREZA: "01"}

_TOLERANCIA_AGREGACAO = Decimal("0.01")  # centavos, nó × soma dos filhos
_TOLERANCIA_RECONC = Decimal("0.01")  # conciliação entre eixos
_TOL_RITMO_PP = Decimal("5")  # pontos percentuais de folga do "no ritmo"
_PERIODO_BIMESTRAL_RE = re.compile(r"^(\d{4})-B([1-6])$")

Medidas = dict[str, Decimal]


# --- helpers de fonte/versão ---
def _source_ref(periodo: str, versao: str, anexo: str) -> SourceRef:
    return SourceRef(relatorio="RREO", anexo=anexo, periodo=periodo, versao_entrega=versao)


def _anexo_do_eixo(eixo: str) -> str:
    return _ANEXO_FUNCAO if eixo == cls.EIXO_FUNCAO else _ANEXO_NATUREZA


def _validar_eixo(eixo: str) -> None:
    if eixo not in cls.EIXOS:
        raise AppError(
            status=422, title="Eixo inválido",
            detail=f"Eixo de drill deve ser 'funcao' ou 'natureza'; recebido '{eixo}'.",
        )


def _resolve_versao(
    session: Session, cod_ibge: str, periodo: str, as_of: datetime | None
) -> str:
    versao = indicators_repo.resolve_versao_rreo(
        session, cod_ibge=cod_ibge, periodo=periodo, as_of=as_of
    )
    if versao is None:
        raise ausencia_de_entrega(
            session,
            cod_ibge=cod_ibge,
            relatorio="RREO",
            periodo=periodo,
            title="RREO ausente",
            detail=f"Sem RREO vigente para {cod_ibge} em {periodo}.",
        )
    return versao


def _pct(parte: Decimal | None, todo: Decimal | None) -> Decimal | None:
    if parte is None or not todo:
        return None
    return parte / todo * Decimal(100)


def _lacuna(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None or b is None:
        return None
    return a - b


def _chave_eixo(eixo: str, codigo: str) -> dict[str, str]:
    if eixo == cls.EIXO_FUNCAO:
        return {"funcao_codigo": codigo, "natureza_codigo": cls.SENTINELA}
    return {"funcao_codigo": cls.SENTINELA, "natureza_codigo": codigo}


def _fato_codigo(fato: FatoDespesa, eixo: str) -> str | None:
    """Código do fato no eixo pedido (``None`` se a linha é do outro eixo)."""
    if eixo == cls.EIXO_FUNCAO:
        return fato.funcao_codigo if fato.natureza_codigo == cls.SENTINELA else None
    return fato.natureza_codigo if fato.funcao_codigo == cls.SENTINELA else None


def _identificar_eixo(fato: FatoDespesa) -> tuple[str, str]:
    """Eixo e código de uma linha do fato (uma linha = um eixo)."""
    if fato.natureza_codigo == cls.SENTINELA:
        return cls.EIXO_FUNCAO, fato.funcao_codigo
    return cls.EIXO_NATUREZA, fato.natureza_codigo


# --- materialização silver → gold ---
def _ensure_sentinela(session: Session) -> None:
    """Garante a sentinela ``'*'`` em ambas as dimensões (FK das linhas de eixo único)."""
    for eixo, model in DIM_MODEL.items():
        rotulo = "funções" if eixo == cls.EIXO_FUNCAO else "naturezas"
        repository.upsert_dim(
            session, model,
            {
                "codigo": cls.SENTINELA, "descricao": f"Total (todas as {rotulo})",
                "parent_codigo": None, "nivel": 0, "path": cls.path_de(cls.SENTINELA),
            },
            sobrescrever_descricao=False,
        )


def _upsert_no(session: Session, model: type, no: cls.NoDespesa) -> None:
    repository.upsert_dim(
        session, model,
        {
            "codigo": no.codigo, "descricao": no.descricao,
            "parent_codigo": no.parent_codigo, "nivel": no.nivel, "path": cls.path_de(no.codigo),
        },
        sobrescrever_descricao=True,
    )


def _upsert_fato_eixo(
    session: Session, cod_ibge: str, periodo: str, versao: str,
    eixo: str, codigo: str, medidas: Medidas,
) -> None:
    repository.upsert_fato(
        session,
        {
            "cod_ibge": cod_ibge, "periodo": periodo, "versao_entrega": versao,
            **_chave_eixo(eixo, codigo),
            **{m: medidas.get(m) for m in cls.MEDIDAS},
        },
    )


def _materializar_funcao(session: Session, cod_ibge: str, periodo: str, versao: str) -> None:
    """RREO Anexo 02 → dim_funcao + fato_despesa (Função→Subfunção pela ordem)."""
    rows = repository.read_anexo(
        session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao,
        marca=_MARCA_ANEXO[cls.EIXO_FUNCAO],
    )
    walk: dict[str, tuple[int, str | None]] = {}
    for r in rows:
        if not r.conta or (r.cod_conta and "Intra" in r.cod_conta):
            continue
        seq = r.linha_seq if r.linha_seq is not None else 10**9
        if r.conta not in walk or seq < walk[r.conta][0]:
            walk[r.conta] = (seq, r.cod_conta)
    pares = cls.derivar_funcao([(s, cc, desc) for desc, (s, cc) in walk.items()])
    no_por_desc = dict(pares)
    model = DIM_MODEL[cls.EIXO_FUNCAO]
    for _desc, no in pares:  # ordem de leitura: função antes da subfunção (FK)
        _upsert_no(session, model, no)

    agregado: dict[str, Medidas] = {}
    for r in rows:
        alvo = no_por_desc.get(r.conta or "")
        medida = cls.classificar_coluna(r.coluna)
        if alvo is None or medida is None:
            continue
        m = agregado.setdefault(alvo.codigo, {})
        m[medida] = m.get(medida, Decimal(0)) + Decimal(r.valor or 0)
    for codigo, medidas in agregado.items():
        _upsert_fato_eixo(session, cod_ibge, periodo, versao, cls.EIXO_FUNCAO, codigo, medidas)


def _materializar_natureza(session: Session, cod_ibge: str, periodo: str, versao: str) -> None:
    """RREO Anexo 01 (lado despesa) → dim_natureza + fato_despesa (Categoria→Grupo por slug)."""
    rows = repository.read_anexo(
        session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao,
        marca=_MARCA_ANEXO[cls.EIXO_NATUREZA],
    )
    descr: dict[str, str] = {}
    for r in rows:
        if r.cod_conta and r.conta and r.cod_conta not in descr:
            descr[r.cod_conta] = r.conta
    nos = cls.derivar_natureza(list(descr.items()))
    no_por_cod = {no.codigo: no for no in nos}
    model = DIM_MODEL[cls.EIXO_NATUREZA]
    for no in sorted(nos, key=lambda n: n.nivel):  # categoria antes do grupo (FK)
        _upsert_no(session, model, no)

    agregado: dict[str, Medidas] = {}
    for r in rows:
        cc = r.cod_conta
        medida = cls.classificar_coluna(r.coluna)
        if not cc or cc not in no_por_cod or medida is None:
            continue
        m = agregado.setdefault(cc, {})
        m[medida] = m.get(medida, Decimal(0)) + Decimal(r.valor or 0)
    for codigo, medidas in agregado.items():
        _upsert_fato_eixo(session, cod_ibge, periodo, versao, cls.EIXO_NATUREZA, codigo, medidas)


def materializar_despesa(
    session: Session, cod_ibge: str, periodo: str, versao: str
) -> list[FatoDespesa]:
    """Materializa os dois eixos (função e natureza) do RREO em ``fato_despesa``."""
    _ensure_sentinela(session)
    _materializar_funcao(session, cod_ibge, periodo, versao)
    _materializar_natureza(session, cod_ibge, periodo, versao)
    return repository.list_fatos(
        session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
    )


def _ensure_gold(
    session: Session, cod_ibge: str, periodo: str, versao: str
) -> list[FatoDespesa]:
    fatos = repository.list_fatos(
        session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
    )
    return fatos if fatos else materializar_despesa(session, cod_ibge, periodo, versao)


# --- agregação (nó com linha própria = valor oficial; sem linha = soma dos filhos) ---
def _medidas_efetivas(
    dims: list[HierarchyNode], proprias: dict[str, Medidas]
) -> dict[str, Medidas]:
    filhos: dict[str | None, list[str]] = {}
    for d in dims:
        filhos.setdefault(d.parent_codigo, []).append(d.codigo)
    efetivas: dict[str, Medidas] = {}

    def resolver(codigo: str) -> Medidas:
        if codigo in efetivas:
            return efetivas[codigo]
        if codigo in proprias:
            efetivas[codigo] = dict(proprias[codigo])
            return efetivas[codigo]
        soma: Medidas = {}
        for filho in filhos.get(codigo, []):
            for m, v in resolver(filho).items():
                soma[m] = soma.get(m, Decimal(0)) + v
        efetivas[codigo] = soma
        return soma

    for d in dims:
        resolver(d.codigo)
    return efetivas


def _com_ancestrais(session: Session, model: type, codigos: set[str]) -> list[Any]:
    """Carrega os nós de ``codigos`` + a cadeia de ancestrais (via ``parent_codigo``)."""
    carregados: dict[str, Any] = {}
    pendentes = set(codigos)
    while pendentes:
        novos = [
            o for o in repository.list_dim(session, model, pendentes) if o.codigo not in carregados
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
    session: Session, cod_ibge: str, periodo: str, versao: str, eixo: str
) -> tuple[list[HierarchyNode], dict[str, Medidas], dict[str, Medidas]]:
    """Nós do eixo (restritos ao reportado + ancestrais), medidas efetivas e próprias."""
    fatos = _ensure_gold(session, cod_ibge, periodo, versao)
    model = DIM_MODEL[eixo]
    proprias: dict[str, Medidas] = {}
    for f in fatos:
        codigo = _fato_codigo(f, eixo)
        if codigo is None:
            continue
        proprias[codigo] = {
            m: Decimal(v) for m in cls.MEDIDAS if (v := getattr(f, m)) is not None
        }
    dim_rows = _com_ancestrais(session, model, set(proprias))
    nodes = [
        HierarchyNode(
            codigo=d.codigo, descricao=d.descricao, parent_codigo=d.parent_codigo,
            nivel=d.nivel, path=d.path,
        )
        for d in dim_rows
    ]
    medidas = _medidas_efetivas(nodes, proprias)
    return nodes, medidas, proprias


def _totais(nodes: list[HierarchyNode], medidas: dict[str, Medidas]) -> DespesaTotais:
    """Totais do ente = soma das raízes do eixo (funções ou categorias econômicas)."""
    soma: Medidas = {}
    for node in nodes:
        if node.parent_codigo is not None:
            continue
        for m, v in medidas.get(node.codigo, {}).items():
            soma[m] = soma.get(m, Decimal(0)) + v
    return DespesaTotais(**{m: soma.get(m) for m in cls.MEDIDAS})


def _measures_map(medidas: dict[str, Medidas]) -> dict[str, Measures]:
    return {c: dict(m) for c, m in medidas.items()}


def _rcl_12m(
    session: Session, cod_ibge: str, periodo: str, as_of: datetime | None
) -> Decimal | None:
    """RCL da Sprint 2, reusada como contexto — ausência não bloqueia a tela de despesa."""
    try:
        return indicators_service.obter_fato_rcl(
            session, cod_ibge, periodo, as_of=as_of
        ).rcl_12m
    except AppError:
        return None


# --- série e comparação temporal ---
def _serie(
    session: Session, cod_ibge: str, base_periodo: str
) -> tuple[list[SerieDespesaItem], SerieAjuste]:
    """Série multi-exercício em nominal + real (IPCA) + per capita, a preços de ``base``."""
    serie: list[SerieDespesaItem] = []
    for periodo in repository.distinct_periodos_fato(session, cod_ibge=cod_ibge):
        versao = indicators_repo.resolve_versao_rreo(
            session, cod_ibge=cod_ibge, periodo=periodo
        )
        if versao is None:
            continue
        nodes, medidas, _ = _carregar_arvore(
            session, cod_ibge, periodo, versao, cls.EIXO_FUNCAO
        )
        serie.append(
            SerieDespesaItem(periodo=periodo, empenhado=_totais(nodes, medidas).empenhado)
        )
    ajuste = serie_ajuste.calcular(
        session, cod_ibge, [s.periodo for s in serie], base_periodo
    )
    por_periodo = serie_ajuste.indexar(ajuste)
    for item in serie:
        a = por_periodo.get(item.periodo)
        item.empenhado_real = serie_ajuste.real(item.empenhado, a)
        item.empenhado_per_capita = serie_ajuste.per_capita(item.empenhado, a)
        item.populacao = a.populacao if a else None
    return serie, ajuste


def _comparacao(
    serie: list[SerieDespesaItem], periodo: str, atual: Decimal | None
) -> ComparacaoDespesa | None:
    m = _PERIODO_BIMESTRAL_RE.match(periodo)
    if m is None:
        return None
    anterior_cod = f"{int(m.group(1)) - 1}-B{m.group(2)}"
    anterior = next((s for s in serie if s.periodo == anterior_cod), None)
    if anterior is None:
        return None
    delta = None
    if atual is not None and anterior.empenhado:
        delta = (atual - anterior.empenhado) / anterior.empenhado * Decimal(100)
    return ComparacaoDespesa(
        periodo_anterior=anterior_cod,
        empenhado_anterior=anterior.empenhado,
        delta_pct=delta,
    )


# --- endpoints ---
def build_arvore(
    session: Session,
    cod_ibge: str,
    periodo: str,
    node: str | None,
    *,
    eixo: str = cls.EIXO_FUNCAO,
    as_of: datetime | None = None,
) -> DrillEnvelope:
    """Drill DOWN/UP por função (Anexo 02) ou natureza (Anexo 01), conforme ``eixo`` (§6.1)."""
    _validar_eixo(eixo)
    versao = _resolve_versao(session, cod_ibge, periodo, as_of)
    nodes, medidas, _ = _carregar_arvore(session, cod_ibge, periodo, versao, eixo)
    if node is not None and not any(n.codigo == node for n in nodes):
        raise AppError(
            status=404, title="Nó inexistente",
            detail=f"Despesa por {eixo}: nó '{node}' sem dado para {cod_ibge} em {periodo}.",
        )
    return build_drill_envelope(
        nodes, node, period=periodo,
        source_ref=_source_ref(periodo, versao, _anexo_do_eixo(eixo)),
        node_measures=_measures_map(medidas),
    )


def build_detalhe(
    session: Session,
    cod_ibge: str,
    periodo: str,
    *,
    eixo: str = cls.EIXO_FUNCAO,
    as_of: datetime | None = None,
) -> DespesaDetalhe:
    """Cabeçalho + composição (raiz do eixo) + série + comparação (Padrão de Detalhe)."""
    _validar_eixo(eixo)
    versao = _resolve_versao(session, cod_ibge, periodo, as_of)
    nodes, medidas, _ = _carregar_arvore(session, cod_ibge, periodo, versao, eixo)
    totais = _totais(nodes, medidas)
    raiz = build_drill_envelope(nodes, None, period=periodo, node_measures=_measures_map(medidas))
    serie, ajuste = _serie(session, cod_ibge, periodo)
    rcl = _rcl_12m(session, cod_ibge, periodo, as_of)
    return DespesaDetalhe(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        eixo=eixo,
        totais=totais,
        potencial_rap=_lacuna(totais.empenhado, totais.pago),
        empenhado_pct_rcl=_pct(totais.empenhado, rcl),
        rcl_12m=rcl,
        composicao=raiz.children,
        serie=serie,
        serie_ajuste=ajuste,
        comparacao=_comparacao(serie, periodo, totais.empenhado),
        periodo_breadcrumb=catalog_service.periodo_breadcrumb(session, periodo),
        source_ref=_source_ref(periodo, versao, _ANEXO_AMBOS),
    )


def _inconsistencias(
    eixo: str,
    nodes: list[HierarchyNode],
    proprias: dict[str, Medidas],
    medidas: dict[str, Medidas],
) -> list[InconsistenciaAgregacao]:
    """Nós com linha própria cujo valor difere da soma (efetiva) dos filhos."""
    filhos: dict[str, list[str]] = {}
    for n in nodes:
        if n.parent_codigo is not None:
            filhos.setdefault(n.parent_codigo, []).append(n.codigo)
    achados: list[InconsistenciaAgregacao] = []
    for codigo, med_propria in proprias.items():
        if not filhos.get(codigo):
            continue
        for medida in cls.MEDIDAS:
            valor_no = med_propria.get(medida)
            if valor_no is None:
                continue
            presentes = [
                medidas.get(c, {}) for c in filhos[codigo] if medida in medidas.get(c, {})
            ]
            if not presentes:
                continue
            soma = sum((m[medida] for m in presentes), Decimal(0))
            if abs(valor_no - soma) > _TOLERANCIA_AGREGACAO:
                achados.append(
                    InconsistenciaAgregacao(
                        eixo=eixo, codigo=codigo, medida=medida,
                        valor_no=valor_no, soma_filhos=soma,
                    )
                )
    return achados


def _violacoes_estagio(fatos: list[FatoDespesa]) -> list[ViolacaoEstagio]:
    """Linhas que violam empenhado ≥ liquidado ≥ pago (invariante do domínio)."""
    violacoes: list[ViolacaoEstagio] = []
    for f in fatos:
        eixo, codigo = _identificar_eixo(f)
        emp, liq, pag = f.empenhado, f.liquidado, f.pago
        problemas: list[str] = []
        if emp is not None and liq is not None and liq > emp:
            problemas.append("liquidado > empenhado")
        if liq is not None and pag is not None and pag > liq:
            problemas.append("pago > liquidado")
        if emp is not None and pag is not None and pag > emp:
            problemas.append("pago > empenhado")
        if problemas:
            violacoes.append(
                ViolacaoEstagio(
                    eixo=eixo, codigo=codigo, empenhado=emp, liquidado=liq, pago=pag,
                    problema="; ".join(problemas),
                )
            )
    return violacoes


def build_memoria(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> MemoriaDespesa:
    """Memória rastreável: estágios, verificação de agregação, invariantes e eixos."""
    versao = _resolve_versao(session, cod_ibge, periodo, as_of)
    fatos = _ensure_gold(session, cod_ibge, periodo, versao)
    nodes_f, med_f, prop_f = _carregar_arvore(
        session, cod_ibge, periodo, versao, cls.EIXO_FUNCAO
    )
    nodes_n, med_n, prop_n = _carregar_arvore(
        session, cod_ibge, periodo, versao, cls.EIXO_NATUREZA
    )
    tot_f = _totais(nodes_f, med_f)
    tot_n = _totais(nodes_n, med_n)

    diferenca: Decimal | None = None
    reconc_ok = True
    if tot_f.empenhado is not None and tot_n.empenhado is not None:
        diferenca = tot_f.empenhado - tot_n.empenhado
        reconc_ok = abs(diferenca) <= _TOLERANCIA_RECONC

    inconsistencias = _inconsistencias(cls.EIXO_FUNCAO, nodes_f, prop_f, med_f) + _inconsistencias(
        cls.EIXO_NATUREZA, nodes_n, prop_n, med_n
    )
    return MemoriaDespesa(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        medidas=list(cls.MEDIDAS),
        totais_funcao=tot_f,
        totais_natureza=tot_n,
        reconciliacao_eixos_ok=reconc_ok,
        diferenca_eixos=diferenca,
        formula_potencial_rap="potencial_rap = empenhado − pago",
        hierarquia_funcao=cls.hierarquia_label(cls.EIXO_FUNCAO),
        hierarquia_natureza=cls.hierarquia_label(cls.EIXO_NATUREZA),
        inconsistencias=inconsistencias,
        violacoes_estagio=_violacoes_estagio(fatos),
        detalhes={
            "linhas_fato": len(fatos),
            "nos_funcao": len(nodes_f),
            "nos_natureza": len(nodes_n),
            "invariante": "empenhado ≥ liquidado ≥ pago",
            "regra_agregacao": "nó com linha própria = valor oficial do RREO; "
            "sem linha própria = soma dos filhos",
        },
        source_ref=_source_ref(periodo, versao, _ANEXO_AMBOS),
    )


# --- estágios (cascata) ---
_CASCATA = ("dotacao_inicial", "dotacao_atualizada", "empenhado", "liquidado", "pago")


def _cascata(totais: DespesaTotais) -> list[EstagioPasso]:
    passos: list[EstagioPasso] = []
    anterior: Decimal | None = None
    for estagio in _CASCATA:
        valor = getattr(totais, estagio)
        delta = _lacuna(valor, anterior) if anterior is not None else None
        passos.append(EstagioPasso(estagio=estagio, valor=valor, delta_anterior=delta))
        if valor is not None:
            anterior = valor
    passos.append(
        EstagioPasso(estagio="inscrito_rap", valor=totais.inscrito_rap, delta_anterior=None)
    )
    return passos


def _estagios_ausentes(totais: DespesaTotais) -> list[str]:
    """Estágios que o anexo consultado não publicou (lacuna da fonte, não zero)."""
    return [e for e in (*_CASCATA, "inscrito_rap") if getattr(totais, e) is None]


def _observacao_estagios(eixo: str, totais: DespesaTotais) -> str | None:
    """Explica a cascata incompleta apontando o anexo que publica o estágio faltante.

    O RREO **Anexo 02** (função) não traz a coluna de despesas *pagas* — só o Anexo 01
    (natureza) traz. Sem isso a cascata pararia em "liquidado" sem dizer por quê, e o
    potencial de restos a pagar (empenhado − pago) sumiria da tela silenciosamente.
    """
    if totais.pago is None and eixo == cls.EIXO_FUNCAO:
        return (
            "O RREO Anexo 02 (função) não publica o estágio 'pago': a cascata completa "
            "e o potencial de restos a pagar vêm do eixo natureza (Anexo 01)."
        )
    ausentes = _estagios_ausentes(totais)
    if ausentes:
        return f"Estágios não publicados nesta entrega: {', '.join(ausentes)}."
    return None


def _lacunas(totais: DespesaTotais) -> list[LacunaEstagio]:
    candidatas = [
        ("credito_a_empenhar", _lacuna(totais.dotacao_atualizada, totais.empenhado),
         "dotação atualizada − empenhado"),
        ("a_liquidar", _lacuna(totais.empenhado, totais.liquidado), "empenhado − liquidado"),
        ("a_pagar", _lacuna(totais.liquidado, totais.pago), "liquidado − pago"),
        ("potencial_rap", _lacuna(totais.empenhado, totais.pago), "empenhado − pago"),
    ]
    return [
        LacunaEstagio(nome=nome, valor=valor, formula=formula)
        for nome, valor, formula in candidatas
        if valor is not None
    ]


def _estagio_item(node: HierarchyNode, medidas: Medidas) -> EstagioItem:
    return EstagioItem(
        codigo=node.codigo,
        descricao=node.descricao,
        dotacao_atualizada=medidas.get("dotacao_atualizada"),
        empenhado=medidas.get("empenhado"),
        liquidado=medidas.get("liquidado"),
        pago=medidas.get("pago"),
        inscrito_rap=medidas.get("inscrito_rap"),
        potencial_rap=_lacuna(medidas.get("empenhado"), medidas.get("pago")),
    )


def build_estagios(
    session: Session,
    cod_ibge: str,
    periodo: str,
    *,
    eixo: str = cls.EIXO_FUNCAO,
    as_of: datetime | None = None,
) -> EstagiosOut:
    """Cascata dotação→empenhado→liquidado→pago + lacunas (potencial RAP = empenhado−pago)."""
    _validar_eixo(eixo)
    versao = _resolve_versao(session, cod_ibge, periodo, as_of)
    nodes, medidas, _ = _carregar_arvore(session, cod_ibge, periodo, versao, eixo)
    totais = _totais(nodes, medidas)
    por_eixo = [
        _estagio_item(n, medidas.get(n.codigo, {}))
        for n in sorted(nodes, key=lambda n: n.codigo)
        if n.parent_codigo is None
    ]
    return EstagiosOut(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        eixo=eixo,
        totais=totais,
        cascata=_cascata(totais),
        lacunas=_lacunas(totais),
        por_eixo=por_eixo,
        estagios_ausentes=_estagios_ausentes(totais),
        observacao=_observacao_estagios(eixo, totais),
        source_ref=_source_ref(periodo, versao, _anexo_do_eixo(eixo)),
    )


# --- execução (ritmo × calendário) ---
def _bimestre(periodo: str) -> int:
    m = _PERIODO_BIMESTRAL_RE.match(periodo)
    if m is None:
        raise AppError(
            status=422, title="Período inválido",
            detail=f"Execução exige período bimestral 'AAAA-Bn'; recebido '{periodo}'.",
        )
    return int(m.group(2))


def _execucao_estagio(
    estagio: str, executado: Decimal | None, base: Decimal | None, esperado: Decimal
) -> ExecucaoEstagio:
    pct = _pct(executado, base)
    if pct is None:
        return ExecucaoEstagio(
            estagio=estagio, executado=executado, base_dotacao=base,
            executado_pct=None, esperado_pct=esperado, ritmo_pp=None, status="sem_base",
        )
    ritmo = pct - esperado
    if ritmo > _TOL_RITMO_PP:
        status = "adiantado"
    elif ritmo < -_TOL_RITMO_PP:
        status = "atrasado"
    else:
        status = "no_ritmo"
    return ExecucaoEstagio(
        estagio=estagio, executado=executado, base_dotacao=base,
        executado_pct=pct, esperado_pct=esperado, ritmo_pp=ritmo, status=status,
    )


def build_execucao(
    session: Session,
    cod_ibge: str,
    periodo: str,
    *,
    eixo: str = cls.EIXO_FUNCAO,
    as_of: datetime | None = None,
) -> ExecucaoOut:
    """Ritmo de execução (empenho/liquidação/pagamento ÷ dotação) × esperado linear.

    O ``eixo`` importa: o Anexo 02 (função) não publica despesas **pagas**, então o
    ritmo de pagamento só existe no eixo natureza (Anexo 01). Base e numerador vêm
    sempre do mesmo anexo — misturar eixos daria um percentual sem significado.
    """
    _validar_eixo(eixo)
    versao = _resolve_versao(session, cod_ibge, periodo, as_of)
    bimestre = _bimestre(periodo)
    nodes, medidas, _ = _carregar_arvore(session, cod_ibge, periodo, versao, eixo)
    totais = _totais(nodes, medidas)
    base = totais.dotacao_atualizada
    esperado = Decimal(bimestre) / Decimal(6) * Decimal(100)
    estagios = [
        _execucao_estagio(nome, getattr(totais, nome), base, esperado)
        for nome in cls.ESTAGIOS_EXECUCAO
    ]
    return ExecucaoOut(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        eixo=eixo,
        bimestre=bimestre,
        esperado_pct=esperado,
        estagios=estagios,
        source_ref=_source_ref(periodo, versao, _anexo_do_eixo(eixo)),
    )


# --- rigidez orçamentária (eixo natureza) ---
def build_rigidez(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> RigidezOut:
    """Peso da despesa rígida (pessoal/juros/amortização) sobre a despesa empenhada."""
    versao = _resolve_versao(session, cod_ibge, periodo, as_of)
    nodes, medidas, _ = _carregar_arvore(
        session, cod_ibge, periodo, versao, cls.EIXO_NATUREZA
    )
    total = _totais(nodes, medidas).empenhado or Decimal(0)

    rigida = discricionaria = semivariavel = Decimal(0)
    componentes: list[RigidezComponente] = []
    # Grupos (GND) reconhecidos pelo slug; particionam a despesa por natureza sem dupla contagem.
    grupos = [(n, cls.classificar_rigidez(n.codigo)) for n in nodes]
    for node, tipo in sorted(
        ((n, t) for n, t in grupos if t is not None), key=lambda nt: nt[0].codigo
    ):
        empenhado = medidas.get(node.codigo, {}).get("empenhado")
        if empenhado is None:
            continue
        if tipo == cls.RIGIDA:
            rigida += empenhado
        elif tipo == cls.DISCRICIONARIA:
            discricionaria += empenhado
        else:
            semivariavel += empenhado
        componentes.append(
            RigidezComponente(
                grupo=node.codigo,
                descricao=node.descricao,
                tipo=tipo,
                empenhado=empenhado,
                pct_despesa=_pct(empenhado, total),
            )
        )
    return RigidezOut(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        despesa_total=total,
        rigida=rigida,
        discricionaria=discricionaria,
        semivariavel=semivariavel,
        rigidez_pct=_pct(rigida, total),
        discricionaria_pct=_pct(discricionaria, total),
        componentes=componentes,
        source_ref=_source_ref(periodo, versao, _ANEXO_NATUREZA),
    )
