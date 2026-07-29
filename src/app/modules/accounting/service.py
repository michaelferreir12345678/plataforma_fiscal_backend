"""Regras de Patrimônio & MSC (Módulo 11): materialização silver→gold + endpoints.

Fontes: ``silver.siconfi_msc`` (Matriz de Saldos Contábeis — mensal, por conta PCASP) e
``silver.siconfi_dca`` (Declaração de Contas Anuais — balanços). Todo número é lido do dado
real do SICONFI, materializado na gold e servido com ``source_ref`` + memória + ``as_of``.

Convenção de saldo: **devedor líquido** (débito ``+``, crédito ``−``) na materialização e no
rollup — nela a identidade contábil vale exatamente (Σ filhos = pai; classe 1 = Ativo do
Balanço). Para exibição converte-se à convenção natural da classe (Ativo e Passivo positivos),
ver :func:`app.modules.accounting.pcasp.saldo_natural`.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.accounting import pcasp, repository
from app.modules.accounting.models import MartMscRollup
from app.modules.accounting.schemas import (
    BalancoComparacaoOut,
    BalancoLinha,
    BalancoOut,
    BalancosIndex,
    BalancoTipoDisponivel,
    CoberturaPatrimonio,
    ComparacaoLinha,
    ConciliacaoCheck,
    ConciliacaoOut,
    MatrizMensalOut,
    MesSaldo,
    PatrimonioDetalhe,
)
from app.modules.catalog import repository as catalog_repository
from app.modules.ingestion import repository as ingestion_repo
from app.shared.envelope import DrillChild, DrillEnvelope, DrillNodeRef
from app.shared.hierarchy import make_path
from app.shared.source_ref import SourceRef

_REL_MSC = "MSC"
_REL_DCA = "DCA"
_ZERO = Decimal(0)
_TOL = Decimal("1.00")  # tolerância de conciliação (R$ 1,00; arredondamentos de centavos)
_MSC_CONTEXT_CACHE_TTL_SECONDS = 30.0
_MSC_CONTEXT_CACHE_MAX_ENTRIES = 64
_MSC_CONTEXT_CACHE_MIN_MONTHS = 6

# Balanços da DCA que materializamos (anexo → tipo canônico).
_ANEXO_TIPO: dict[str, str] = {
    "I-AB": "patrimonial",
    "I-HI": "variacoes",
    "I-C": "orcamentario_receita",
    "I-D": "orcamentario_despesa",
}
_TIPO_ROTULO: dict[str, str] = {
    "patrimonial": "Balanço Patrimonial",
    "variacoes": "Demonstração das Variações Patrimoniais",
    "orcamentario_receita": "Balanço Orçamentário · Receita",
    "orcamentario_despesa": "Balanço Orçamentário · Despesa",
}
_TIPO_ANEXO = {v: k for k, v in _ANEXO_TIPO.items()}

# Prefixo IBGE (2 dígitos) → UF (fallback quando dim_ente ainda não tem uf conformada).
_UF_POR_PREFIXO: dict[str, str] = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO",
    "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL",
    "28": "SE", "29": "BA", "31": "MG", "32": "ES", "33": "RJ", "35": "SP", "41": "PR",
    "42": "SC", "43": "RS", "50": "MS", "51": "MT", "52": "GO", "53": "DF",
}


# --- contexto / helpers ---
@dataclass(frozen=True)
class MscMes:
    periodo: str
    mes: int
    versao: str


@dataclass
class Contexto:
    cod_ibge: str
    uf: str
    ano: int
    esfera: str | None
    dca_versao: str | None
    msc_meses: list[MscMes] = field(default_factory=list)
    as_of: datetime | None = None

    @property
    def tem_msc(self) -> bool:
        return bool(self.msc_meses)

    @property
    def tem_dca(self) -> bool:
        return self.dca_versao is not None

    @property
    def mes_referencia(self) -> MscMes | None:
        """Último mês MSC disponível (fechamento do exercício ou o mais recente)."""
        return self.msc_meses[-1] if self.msc_meses else None


@dataclass(frozen=True)
class _CachedContexto:
    stored_at: float
    contexto: Contexto


_ContextIdentity = tuple[tuple[str, str, str], ...]
_ContextCacheKey = tuple[str, int, _ContextIdentity]
_contexto_cache: OrderedDict[_ContextCacheKey, _CachedContexto] = OrderedDict()
_contexto_cache_lock = threading.Lock()


def _contexto_cache_get(
    cod_ibge: str,
    ano: int,
    identity: _ContextIdentity,
) -> Contexto | None:
    key = (cod_ibge, ano, identity)
    now = time.monotonic()
    with _contexto_cache_lock:
        cached = _contexto_cache.get(key)
        if cached is None:
            return None
        if now - cached.stored_at > _MSC_CONTEXT_CACHE_TTL_SECONDS:
            del _contexto_cache[key]
            return None
        _contexto_cache.move_to_end(key)
        return cached.contexto


def _contexto_cache_put(
    contexto: Contexto,
    identity: _ContextIdentity,
) -> None:
    key = (contexto.cod_ibge, contexto.ano, identity)
    with _contexto_cache_lock:
        _contexto_cache[key] = _CachedContexto(
            stored_at=time.monotonic(),
            contexto=contexto,
        )
        _contexto_cache.move_to_end(key)
        while len(_contexto_cache) > _MSC_CONTEXT_CACHE_MAX_ENTRIES:
            _contexto_cache.popitem(last=False)


def _ente_contexto(session: Session, cod_ibge: str) -> tuple[str, str | None]:
    """UF/esfera já conformadas na gold, lidas uma vez por materialização."""

    ente = catalog_repository.get_dim_ente(session, cod_ibge)
    uf = ente.uf if ente is not None and ente.uf else _UF_POR_PREFIXO.get(cod_ibge[:2], "ZZ")
    return uf, ente.esfera if ente is not None else None


def _source_dca(ano: int, versao: str | None, anexo: str | None = None) -> SourceRef:
    return SourceRef(relatorio=_REL_DCA, anexo=anexo, periodo=str(ano), versao_entrega=versao)


def _source_msc(periodo: str, versao: str) -> SourceRef:
    return SourceRef(
        relatorio=_REL_MSC, anexo="MSC Patrimonial", periodo=periodo, versao_entrega=versao
    )


# --- materialização silver → gold (idempotente por versão) ---
def _materializar_msc_mes(
    session: Session, cod_ibge: str, uf: str, periodo: str, mes: int, versao: str
) -> None:
    """MSC de um mês → fato_msc_saldo (folhas) + mart_msc_rollup (todos os nós, rolled-up)."""
    linhas = repository.read_msc_month(
        session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
    )
    ano = int(periodo[:4])
    # Folhas (contas de 9 dígitos da MSC). O silver já traz o saldo na convenção **devedor
    # líquido** (débito +, crédito −) por conta — o gold só agrega e rola para cima.
    folhas: dict[str, dict[str, Decimal]] = {}
    fato_rows: list[dict] = []
    for r in linhas:
        conta = pcasp.parse(r.cod_conta_pcasp)
        if conta is None:
            continue
        si = r.saldo_inicial or _ZERO
        sf = r.saldo_final or _ZERO
        agg = folhas.setdefault(conta.codigo, {"si": _ZERO, "sf": _ZERO})
        agg["si"] += si
        agg["sf"] += sf
        fato_rows.append(
            {
                "uf": uf, "ano": ano, "cod_ibge": cod_ibge, "periodo": periodo, "mes": mes,
                "cod_conta": conta.codigo, "natureza": r.natureza,
                "saldo_inicial": si, "saldo_final": sf,
                "mov_devedor": r.mov_devedor, "mov_credor": r.mov_credor,
                "versao_entrega": versao,
            }
        )
    repository.replace_msc_saldo(
        session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao, rows=fato_rows
    )

    # Rollup: acumula cada folha em todos os seus ancestrais (pai = Σ filhos, exato).
    nos: dict[str, dict[str, Decimal]] = {}
    for codigo, saldos in folhas.items():
        for d9 in pcasp.require(codigo).ancestral_d9s():
            cod = pcasp.mask(d9)
            n = nos.setdefault(cod, {"si": _ZERO, "sf": _ZERO})
            n["si"] += saldos["si"]
            n["sf"] += saldos["sf"]

    # Descrições da DCA (patrimonial/variações) quando já materializadas — enriquece nós MSC.
    descricoes = {
        c.codigo: c.descricao for c in repository.list_contas(session, list(nos))
    }
    _registrar_contas(session, {cod: descricoes.get(cod) for cod in nos}, fonte="msc")
    pais = {c: pcasp.require(c).parent_codigo() for c in nos}
    filhos_por_pai: dict[str, int] = {}
    for pai in pais.values():
        if pai is not None:
            filhos_por_pai[pai] = filhos_por_pai.get(pai, 0) + 1

    rollup_rows: list[dict] = []
    for cod, saldos in nos.items():
        conta = pcasp.require(cod)
        natureza = pcasp.natureza_classe(conta.classe)
        rollup_rows.append(
            {
                "cod_ibge": cod_ibge, "uf": uf, "ano": ano, "periodo": periodo, "mes": mes,
                "cod_conta": cod, "parent_conta": pais[cod], "nivel": conta.nivel,
                "classe": conta.classe, "natureza": natureza,
                "descricao": descricoes.get(cod) or pcasp.nome_no(cod),
                "saldo_inicial": saldos["si"], "saldo_final": saldos["sf"],
                "has_children": filhos_por_pai.get(cod, 0) > 0,
                "versao_entrega": versao,
            }
        )
    repository.replace_rollup_month(
        session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao, rows=rollup_rows
    )


def _upsert_conta(
    session: Session, conta: pcasp.ContaPcasp, descricao: str | None, *, fonte: str
) -> None:
    parent = conta.parent_codigo()
    parent_path = None
    if parent is not None:
        # path do pai construído recursivamente pelos códigos canônicos dos ancestrais.
        labels = [pcasp.mask(d9) for d9 in conta.ancestral_d9s()[:-1]]
        parent_path = ""
        for lbl in labels:
            parent_path = make_path(parent_path or None, lbl)
    path = make_path(parent_path, conta.codigo)
    repository.upsert_conta(
        session,
        {
            "codigo": conta.codigo,
            "descricao": descricao or pcasp.nome_no(conta.codigo),
            "descricao_autoritativa": bool(descricao),
            "parent_codigo": parent,
            "nivel": conta.nivel,
            "path": path,
            "classe": conta.classe,
            "natureza": pcasp.natureza_classe(conta.classe),
            "fonte_dado": fonte,
        },
    )


def _registrar_contas(session: Session, nomes: dict[str, str | None], *, fonte: str) -> None:
    """Materializa em dim_conta_pcasp todos os nós + **ancestrais**, em ordem de nível.

    A FK auto-referente (``parent_codigo``) exige o pai antes do filho; ordenar por nível
    garante isso. ``nomes`` traz descrições autoritativas por código (ancestrais sintéticos
    ausentes ganham nome de fallback). Expande cada código a toda a sua linhagem.
    """
    autoritativas = {c: n for c, n in nomes.items() if n}
    todos: set[str] = set()
    for codigo in nomes:
        conta = pcasp.parse(codigo)
        if conta is None:
            continue
        for d9 in conta.ancestral_d9s():
            todos.add(pcasp.mask(d9))
    for cod in sorted(todos, key=lambda c: (pcasp.require(c).nivel, c)):
        conta = pcasp.require(cod)
        _upsert_conta(session, conta, autoritativas.get(cod), fonte=fonte)


def _nome_conta_dca(conta_desc: str | None, codigo: str) -> str | None:
    """Extrai o nome da conta da descrição da DCA ("1.1.0.0.0.00.00 - Ativo Circulante")."""
    if not conta_desc:
        return None
    if " - " in conta_desc:
        return conta_desc.split(" - ", 1)[1].strip()
    return conta_desc.strip()


def _materializar_dca(
    session: Session, cod_ibge: str, uf: str, ano: int, versao: str
) -> None:
    """DCA do exercício → fato_balanco (patrimonial/variações/orçamentário) + dim_conta_pcasp."""
    linhas = repository.read_dca_year(
        session, cod_ibge=cod_ibge, periodo=str(ano), versao_entrega=versao
    )
    rows: list[dict] = []
    nomes_pcasp: dict[str, str | None] = {}
    for r in linhas:
        tipo = _tipo_de_anexo(r.anexo)
        if tipo is None:
            continue
        if tipo in ("patrimonial", "variacoes"):
            # Pula os quadros suplementares da Lei 4.320 (Financeiro/Permanente, sufixo F/P):
            # não são a conta PCASP do Balanço e duplicariam o código canônico.
            if not pcasp.is_conta_padrao(r.cod_conta):
                continue
            conta = pcasp.parse(r.cod_conta)
        else:
            conta = None
        if conta is not None:
            nome = _nome_conta_dca(r.conta, conta.codigo)
            nomes_pcasp[conta.codigo] = nome
            rows.append(
                {
                    "cod_ibge": cod_ibge, "uf": uf, "ano": ano, "periodo": str(ano),
                    "tipo": tipo, "anexo": r.anexo, "cod_conta": conta.codigo,
                    "parent_conta": conta.parent_codigo(), "nivel": conta.nivel,
                    "conta_descricao": nome, "coluna": r.coluna, "valor": r.valor,
                    "versao_entrega": versao,
                }
            )
        else:
            # Orçamentário (slugs): sem hierarquia PCASP.
            cod = (r.cod_conta or r.conta or "").strip() or "?"
            rows.append(
                {
                    "cod_ibge": cod_ibge, "uf": uf, "ano": ano, "periodo": str(ano),
                    "tipo": tipo, "anexo": r.anexo, "cod_conta": cod,
                    "parent_conta": None, "nivel": None,
                    "conta_descricao": (r.conta or "").strip() or None,
                    "coluna": r.coluna, "valor": r.valor, "versao_entrega": versao,
                }
            )
    # Registra os nós PCASP (+ ancestrais) em ordem de nível antes de gravar os fatos.
    _registrar_contas(session, nomes_pcasp, fonte="dca")
    repository.replace_balanco_year(
        session, cod_ibge=cod_ibge, ano=ano, versao_entrega=versao, rows=rows
    )


def _tipo_de_anexo(anexo: str | None) -> str | None:
    if not anexo:
        return None
    token = anexo.split("Anexo", 1)[-1].strip() if "Anexo" in anexo else anexo.strip()
    token = token.upper().replace(" ", "")
    return _ANEXO_TIPO.get(token)


def ensure_materializado(
    session: Session, cod_ibge: str, *, ano: int, as_of: datetime | None
) -> Contexto:
    """Materializa (idempotente) DCA + MSC do ano e devolve o contexto (versões resolvidas)."""
    current_identity: _ContextIdentity | None = None
    if as_of is None:
        current_identity = ingestion_repo.version_identity_for_year(
            session,
            cod_ibge=cod_ibge,
            ano=ano,
        )
        cached = _contexto_cache_get(cod_ibge, ano, current_identity)
        if cached is not None:
            return cached

    uf, esfera = _ente_contexto(session, cod_ibge)

    # DCA (anual).
    dca_versao = (
        next(
            (
                versao
                for relatorio, periodo, versao in current_identity
                if relatorio == _REL_DCA and periodo == str(ano)
            ),
            None,
        )
        if current_identity is not None
        else ingestion_repo.resolve_versao(
            session,
            cod_ibge=cod_ibge,
            relatorio=_REL_DCA,
            periodo=str(ano),
            as_of=as_of,
        )
    )
    if dca_versao is not None and not repository.balanco_present(
        session, cod_ibge=cod_ibge, ano=ano, versao_entrega=dca_versao
    ):
        _materializar_dca(session, cod_ibge, uf, ano, dca_versao)

    # MSC (mensal): resolve a versão vigente/as_of de cada mês presente no silver.
    periodos = repository.distinct_msc_periodos(session, cod_ibge=cod_ibge, ano=ano)
    versoes = (
        {
            periodo: versao
            for relatorio, periodo, versao in current_identity
            if relatorio == _REL_MSC
        }
        if current_identity is not None
        else ingestion_repo.resolve_versoes(
            session,
            cod_ibge=cod_ibge,
            relatorio=_REL_MSC,
            periodos=periodos,
            as_of=as_of,
        )
    )
    pares = [(periodo, versoes[periodo]) for periodo in periodos if periodo in versoes]
    materializados = repository.rollup_present_pairs(
        session,
        cod_ibge=cod_ibge,
        periodo_versao=pares,
    )
    meses: list[MscMes] = []
    for periodo, versao in pares:
        if versao is None:
            continue
        mes = int(periodo.split("-M")[1])
        if (periodo, versao) not in materializados:
            _materializar_msc_mes(session, cod_ibge, uf, periodo, mes, versao)
        meses.append(MscMes(periodo=periodo, mes=mes, versao=versao))
    meses.sort(key=lambda m: m.mes)
    contexto = Contexto(
        cod_ibge=cod_ibge, uf=uf, ano=ano, esfera=esfera,
        dca_versao=dca_versao, msc_meses=meses, as_of=as_of,
    )
    if current_identity is not None and len(meses) >= _MSC_CONTEXT_CACHE_MIN_MONTHS:
        _contexto_cache_put(contexto, current_identity)
    return contexto


def _anos_disponiveis(session: Session, cod_ibge: str) -> list[int]:
    anos = set(repository.anos_com_dca(session, cod_ibge=cod_ibge))
    anos |= set(repository.anos_com_msc(session, cod_ibge=cod_ibge))
    return sorted(anos)


def _resolver_ano(session: Session, cod_ibge: str, ano: int | None) -> int:
    if ano is not None:
        return ano
    anos = _anos_disponiveis(session, cod_ibge)
    if not anos:
        raise AppError(
            status=404, title="Sem patrimônio",
            detail=f"Ente {cod_ibge} não tem DCA nem MSC ingeridas.",
        )
    return anos[-1]


# --- apresentação ---
def _measures_rollup(row: MartMscRollup) -> dict:
    return {
        "saldo": pcasp.saldo_natural(row.saldo_final, row.classe),
        "saldo_inicial": pcasp.saldo_natural(row.saldo_inicial, row.classe),
        "saldo_devedor_liquido": row.saldo_final,
        "classe": row.classe,
        "natureza": row.natureza,
        "nivel": row.nivel,
    }


def _breadcrumb_pcasp(session: Session, codigo: str) -> list[DrillNodeRef]:
    """Ancestrais (raiz→pai) de ``codigo`` com descrição de dim_conta_pcasp."""
    conta = pcasp.parse(codigo)
    if conta is None:
        return []
    anc_cods = [pcasp.mask(d9) for d9 in conta.ancestral_d9s()[:-1]]
    descricoes = repository.descricoes_por_codigo(session, anc_cods)
    out: list[DrillNodeRef] = []
    for cod in anc_cods:
        c = descricoes.get(cod)
        out.append(
            DrillNodeRef(
                codigo=cod,
                descricao=c.descricao if c else pcasp.nome_no(cod),
                nivel=pcasp.require(cod).nivel,
            )
        )
    return out


def build_arvore(
    session: Session, cod_ibge: str, node: str | None, periodo: str | None,
    *, as_of: datetime | None = None,
) -> DrillEnvelope:
    """Explorador MSC: filhos de ``node`` com saldo *rolled-up* — drill *lazy* (§6.1)."""
    ano = _resolver_ano_de_periodo(session, cod_ibge, periodo)
    ctx = ensure_materializado(session, cod_ibge, ano=ano, as_of=as_of)
    mes = _mes_alvo(ctx, periodo)
    if mes is None:
        # Sem MSC (ex.: Fortaleza não publica MSC): árvore vazia, mas honesta.
        return DrillEnvelope(
            node=None, breadcrumb=[], children=[], measures={},
            period=periodo, source_ref=_source_dca(ano, ctx.dca_versao),
        )
    node_codigo: str | None = None
    if node:
        conta_node = pcasp.parse(node)
        if conta_node is None:
            raise AppError(
                status=422, title="Conta inválida",
                detail=f"'{node}' não é um código PCASP válido.",
            )
        node_codigo = conta_node.codigo
    children_rows = repository.rollup_children(
        session, cod_ibge=cod_ibge, periodo=mes.periodo, versao_entrega=mes.versao,
        parent=node_codigo,
    )
    children = [
        DrillChild(
            codigo=r.cod_conta, descricao=r.descricao, nivel=r.nivel,
            measures=_measures_rollup(r), has_children=r.has_children,
        )
        for r in children_rows
    ]
    node_ref = None
    node_measures: dict = {}
    breadcrumb: list[DrillNodeRef] = []
    if node_codigo is not None:
        nrow = repository.rollup_node(
            session, cod_ibge=cod_ibge, periodo=mes.periodo, versao_entrega=mes.versao,
            cod_conta=node_codigo,
        )
        if nrow is None:
            raise AppError(
                status=404, title="Conta inexistente",
                detail=f"Conta '{node}' sem saldo na MSC de {cod_ibge} em {mes.periodo}.",
            )
        node_ref = DrillNodeRef(codigo=nrow.cod_conta, descricao=nrow.descricao, nivel=nrow.nivel)
        node_measures = _measures_rollup(nrow)
        breadcrumb = _breadcrumb_pcasp(session, node_codigo)
    return DrillEnvelope(
        node=node_ref, breadcrumb=breadcrumb, children=children, measures=node_measures,
        period=mes.periodo, source_ref=_source_msc(mes.periodo, mes.versao),
    )


def _resolver_ano_de_periodo(session: Session, cod_ibge: str, periodo: str | None) -> int:
    if periodo and periodo[:4].isdigit():
        return int(periodo[:4])
    return _resolver_ano(session, cod_ibge, None)


def _mes_alvo(ctx: Contexto, periodo: str | None) -> MscMes | None:
    """Mês MSC alvo: o solicitado (AAAA-Mnn), ou o último disponível do ano."""
    if not ctx.msc_meses:
        return None
    if periodo and "-M" in periodo:
        alvo = next((m for m in ctx.msc_meses if m.periodo == periodo), None)
        if alvo is not None:
            return alvo
    return ctx.mes_referencia


def build_matriz(
    session: Session, cod_ibge: str, codigo: str, ano: int | None,
    *, as_of: datetime | None = None,
) -> MatrizMensalOut:
    """Matriz mensal de uma conta PCASP (12 meses de saldos)."""
    conta = pcasp.parse(codigo)
    if conta is None:
        raise AppError(
            status=422, title="Código inválido",
            detail=f"'{codigo}' não é um código PCASP válido.",
        )
    ano_r = _resolver_ano(session, cod_ibge, ano)
    ctx = ensure_materializado(session, cod_ibge, ano=ano_r, as_of=as_of)
    if not ctx.tem_msc:
        raise AppError(
            status=404, title="Sem MSC",
            detail=(
                f"Ente {cod_ibge} não tem MSC publicada para {ano_r}. "
                "Use os balanços da DCA (/balancos) para a posição patrimonial anual."
            ),
        )
    pares = [(m.periodo, m.versao) for m in ctx.msc_meses]
    rows = repository.rollup_matriz(
        session, cod_ibge=cod_ibge, cod_conta=conta.codigo, periodo_versao=pares
    )
    if not rows:
        raise AppError(
            status=404, title="Conta sem saldo",
            detail=f"Conta '{conta.codigo}' não aparece na MSC de {cod_ibge} em {ano_r}.",
        )
    por_periodo = {r.periodo: r for r in rows}
    classe = rows[0].classe
    meses: list[MesSaldo] = []
    for m in ctx.msc_meses:
        r = por_periodo.get(m.periodo)
        sf = pcasp.saldo_natural(r.saldo_final, classe) if r else None
        si = pcasp.saldo_natural(r.saldo_inicial, classe) if r else None
        mov = (sf - si) if (sf is not None and si is not None) else None
        meses.append(
            MesSaldo(mes=m.mes, periodo=m.periodo, saldo_inicial=si, saldo_final=sf, movimento=mov)
        )
    abertura = next((x.saldo_inicial for x in meses if x.saldo_inicial is not None), None)
    encerr = next((x.saldo_final for x in reversed(meses) if x.saldo_final is not None), None)
    variacao = (encerr - abertura) if (encerr is not None and abertura is not None) else None
    ref = rows[-1]
    return MatrizMensalOut(
        cod_ibge=cod_ibge, ano=ano_r, as_of=as_of.isoformat() if as_of else None,
        versao_entrega=ctx.msc_meses[-1].versao, cod_conta=ref.cod_conta,
        descricao=ref.descricao, nivel=ref.nivel, classe=classe, natureza=ref.natureza,
        breadcrumb=_breadcrumb_pcasp(session, ref.cod_conta), meses=meses,
        saldo_abertura=abertura, saldo_encerramento=encerr, variacao_exercicio=variacao,
        memoria={
            "convencao": "saldos exibidos na convenção natural da classe (D→+, C→+ invertido)",
            "origem": "MSC Patrimonial (saldo final ending_balance por mês), agregado por conta",
            "meses_com_dado": [m.periodo for m in ctx.msc_meses],
        },
        source_ref=_source_msc(ctx.msc_meses[-1].periodo, ctx.msc_meses[-1].versao),
    )


# --- balanços (DCA) ---
def build_balancos_index(
    session: Session, cod_ibge: str, ano: int | None, *, as_of: datetime | None = None
) -> BalancosIndex:
    ano_r = _resolver_ano(session, cod_ibge, ano)
    ctx = ensure_materializado(session, cod_ibge, ano=ano_r, as_of=as_of)
    presentes = (
        set(
            repository.distinct_balanco_tipos(
                session, cod_ibge=cod_ibge, ano=ano_r, versao_entrega=ctx.dca_versao
            )
        )
        if ctx.dca_versao
        else set()
    )
    tipos = [
        BalancoTipoDisponivel(
            tipo=t, rotulo=_TIPO_ROTULO[t], anexo=f"DCA-Anexo {_TIPO_ANEXO[t]}",
            disponivel=t in presentes,
        )
        for t in _ANEXO_TIPO.values()
    ]
    return BalancosIndex(
        cod_ibge=cod_ibge, ano=ano_r, versao_entrega=ctx.dca_versao, tipos=tipos,
        source_ref=_source_dca(ano_r, ctx.dca_versao),
    )


def build_balanco(
    session: Session, cod_ibge: str, ano: int | None, tipo: str,
    *, as_of: datetime | None = None,
) -> BalancoOut:
    if tipo not in _ANEXO_TIPO.values():
        raise AppError(
            status=422, title="Tipo inválido",
            detail=f"tipo deve ser um de {sorted(_ANEXO_TIPO.values())}.",
        )
    ano_r = _resolver_ano(session, cod_ibge, ano)
    ctx = ensure_materializado(session, cod_ibge, ano=ano_r, as_of=as_of)
    if ctx.dca_versao is None:
        raise AppError(
            status=404, title="DCA ausente",
            detail=f"Sem DCA vigente para {cod_ibge} em {ano_r}.",
        )
    linhas_db = repository.read_balanco(
        session, cod_ibge=cod_ibge, ano=ano_r, tipo=tipo, versao_entrega=ctx.dca_versao
    )
    if not linhas_db:
        raise AppError(
            status=404, title="Balanço ausente",
            detail=f"DCA de {cod_ibge}/{ano_r} não traz o {_TIPO_ROTULO[tipo]}.",
        )
    linhas = [
        BalancoLinha(
            cod_conta=r.cod_conta, descricao=r.conta_descricao, coluna=r.coluna,
            valor=r.valor, nivel=r.nivel, parent_conta=r.parent_conta,
            natureza=pcasp.natureza_classe(pcasp.require(r.cod_conta).classe)
            if r.nivel is not None
            else None,
        )
        for r in linhas_db
    ]
    return BalancoOut(
        cod_ibge=cod_ibge, ano=ano_r, tipo=tipo,
        anexo=f"DCA-Anexo {_TIPO_ANEXO[tipo]}",
        as_of=as_of.isoformat() if as_of else None, versao_entrega=ctx.dca_versao,
        destaques=_destaques_balanco(session, cod_ibge, ano_r, tipo, ctx.dca_versao),
        linhas=linhas,
        memoria={
            "origem": f"DCA Anexo {_TIPO_ANEXO[tipo]} ({_TIPO_ROTULO[tipo]})",
            "convencao": "valores na convenção natural reportada pela DCA (não recalculados)",
        },
        source_ref=_source_dca(ano_r, ctx.dca_versao, anexo=f"DCA-Anexo {_TIPO_ANEXO[tipo]}"),
    )


def _destaques_balanco(
    session: Session, cod_ibge: str, ano: int, tipo: str, versao: str
) -> dict[str, Decimal | None]:
    def v(cod: str) -> Decimal | None:
        return repository.balanco_valor(
            session, cod_ibge=cod_ibge, ano=ano, tipo=tipo, versao_entrega=versao, cod_conta=cod
        )

    if tipo == "patrimonial":
        return {
            "ativo": v("1.0.0.0.0.00.00"),
            "passivo_pl": v("2.0.0.0.0.00.00"),
            "patrimonio_liquido": v("2.3.0.0.0.00.00"),
        }
    if tipo == "variacoes":
        vpd = v("3.0.0.0.0.00.00")
        vpa = v("4.0.0.0.0.00.00")
        return {
            "vpd": vpd, "vpa": vpa,
            "resultado_patrimonial": (vpa - vpd) if (vpa is not None and vpd is not None) else None,
        }
    return {}


# --- conciliação ---
def _ativo_passivo_pl_dca(
    session: Session, cod_ibge: str, ano: int, versao: str
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    def v(cod: str) -> Decimal | None:
        return repository.balanco_valor(
            session, cod_ibge=cod_ibge, ano=ano, tipo="patrimonial",
            versao_entrega=versao, cod_conta=cod,
        )

    return v("1.0.0.0.0.00.00"), v("2.0.0.0.0.00.00"), v("2.3.0.0.0.00.00")


def _saldos_classe_msc(
    session: Session, cod_ibge: str, mes: MscMes
) -> dict[int, Decimal | None]:
    """Saldos naturais das classes 1..4 no mês de referência (Ativo/Passivo+PL/VPD/VPA)."""
    out: dict[int, Decimal | None] = {}
    for classe in (1, 2, 3, 4):
        nrow = repository.rollup_node(
            session, cod_ibge=cod_ibge, periodo=mes.periodo, versao_entrega=mes.versao,
            cod_conta=f"{classe}.0.0.0.0.00.00",
        )
        out[classe] = pcasp.saldo_natural(nrow.saldo_final, classe) if nrow else None
    return out


def _rollup_check(
    session: Session, ctx: Contexto
) -> tuple[int, Decimal, str | None]:
    """Verifica pai = Σ filhos no mês de referência. Retorna (nº divergências, pior, exemplo)."""
    mes = ctx.mes_referencia
    if mes is None:
        return 0, _ZERO, None
    # Carrega todos os nós do mês de referência e agrupa por pai.
    nodes = list(
        session.scalars(
            select(MartMscRollup).where(
                MartMscRollup.cod_ibge == ctx.cod_ibge,
                MartMscRollup.periodo == mes.periodo,
                MartMscRollup.versao_entrega == mes.versao,
            )
        )
    )
    por_pai: dict[str, Decimal] = {}
    saldo_por_cod: dict[str, Decimal] = {}
    for n in nodes:
        saldo_por_cod[n.cod_conta] = n.saldo_final or _ZERO
        if n.parent_conta is not None:
            por_pai[n.parent_conta] = por_pai.get(n.parent_conta, _ZERO) + (n.saldo_final or _ZERO)
    divergencias = 0
    pior = _ZERO
    exemplo = None
    for cod, soma_filhos in por_pai.items():
        proprio = saldo_por_cod.get(cod, _ZERO)
        dif = abs(proprio - soma_filhos)
        if dif > _TOL:
            divergencias += 1
            if dif > pior:
                pior = dif
                exemplo = cod
    return divergencias, pior, exemplo


def build_conciliacao(
    session: Session, cod_ibge: str, ano: int | None, *, as_of: datetime | None = None
) -> ConciliacaoOut:
    """Conciliação: rollup (pai = Σ filhos) + identidade do Balanço + cruzamento MSC↔DCA."""
    ano_r = _resolver_ano(session, cod_ibge, ano)
    ctx = ensure_materializado(session, cod_ibge, ano=ano_r, as_of=as_of)
    checks: list[ConciliacaoCheck] = []
    # Locais estreitados (mypy não estreita através das properties tem_msc/tem_dca).
    mes = ctx.mes_referencia
    dca_versao = ctx.dca_versao

    # 1) Rollup interno da MSC: cada nó-pai = soma dos filhos.
    if mes is not None:
        ndiv, pior, exemplo = _rollup_check(session, ctx)
        checks.append(
            ConciliacaoCheck(
                chave="rollup_msc",
                titulo="Rollup da MSC (pai = Σ filhos)",
                descricao=(
                    "Cada conta-pai da Matriz de Saldos deve igualar a soma dos filhos "
                    f"(mês de referência {mes.periodo})."
                ),
                esquerda_rotulo="nós conferidos", esquerda=None,
                direita_rotulo="divergências", direita=Decimal(ndiv),
                diferenca=pior, tolerancia=_TOL, divergente=ndiv > 0,
                detalhe=(f"pior divergência na conta {exemplo}: R$ {pior}" if exemplo else
                         "todos os nós fecham (Σ filhos = pai)"),
                source_refs=[_source_msc(mes.periodo, mes.versao)],
            )
        )

    # 2) Identidade do Balanço Patrimonial (DCA): Ativo = Passivo + PL.
    if dca_versao is not None:
        ativo, passivo_pl, _pl = _ativo_passivo_pl_dca(session, cod_ibge, ano_r, dca_versao)
        dif = _dif(ativo, passivo_pl)
        checks.append(
            ConciliacaoCheck(
                chave="balanco_fecha",
                titulo="Balanço Patrimonial fecha (Ativo = Passivo + PL)",
                descricao="Equação fundamental do Balanço Patrimonial (DCA Anexo I-AB).",
                esquerda_rotulo="Ativo (classe 1)", esquerda=ativo,
                direita_rotulo="Passivo + PL (classe 2)", direita=passivo_pl,
                diferenca=dif, tolerancia=_TOL, divergente=_divergente(dif),
                aplicavel=ativo is not None and passivo_pl is not None,
                source_refs=[_source_dca(ano_r, dca_versao, anexo="DCA-Anexo I-AB")],
            )
        )

    # 3) Cruzamento MSC ↔ DCA no fechamento do exercício.
    if mes is not None and dca_versao is not None:
        ativo_dca, passivo_dca, _ = _ativo_passivo_pl_dca(session, cod_ibge, ano_r, dca_versao)
        msc = _saldos_classe_msc(session, cod_ibge, mes)

        # 3a) Ativo é invariante ao encerramento → deve bater exatamente.
        dif_ativo = _dif(msc.get(1), ativo_dca)
        checks.append(
            ConciliacaoCheck(
                chave="msc_dca_ativo",
                titulo="MSC × DCA — Ativo",
                descricao=(
                    f"O Ativo de fechamento da MSC ({mes.periodo}) deve igualar o Ativo do "
                    "Balanço Patrimonial anual da DCA (o Ativo não muda no encerramento)."
                ),
                esquerda_rotulo=f"MSC Ativo ({mes.periodo})", esquerda=msc.get(1),
                direita_rotulo=f"DCA Ativo ({ano_r})", direita=ativo_dca,
                diferenca=dif_ativo, tolerancia=_TOL, divergente=_divergente(dif_ativo),
                aplicavel=msc.get(1) is not None and ativo_dca is not None,
                source_refs=[
                    _source_msc(mes.periodo, mes.versao),
                    _source_dca(ano_r, dca_versao, anexo="DCA-Anexo I-AB"),
                ],
            )
        )

        # 3b) Identidade do encerramento: a MSC mensal é PRÉ-encerramento (o resultado ainda
        # não foi transferido ao PL). Logo, DCA Passivo+PL = MSC Passivo+PL + Resultado (VPA−VPD).
        msc_passivo = msc.get(2)
        vpa_msc, vpd_msc = msc.get(4), msc.get(3)
        resultado = (
            (vpa_msc - vpd_msc) if (vpa_msc is not None and vpd_msc is not None) else None
        )
        msc_fechado = (
            (msc_passivo + resultado)
            if (msc_passivo is not None and resultado is not None)
            else None
        )
        dif_enc = _dif(msc_fechado, passivo_dca)
        checks.append(
            ConciliacaoCheck(
                chave="msc_dca_encerramento",
                titulo="MSC × DCA — Passivo + PL (identidade do encerramento)",
                descricao=(
                    "A MSC mensal é pré-encerramento (o resultado do exercício ainda não foi "
                    "transferido ao Patrimônio Líquido). Assim: DCA (Passivo + PL) = MSC "
                    "(Passivo + PL) + Resultado Patrimonial (VPA − VPD)."
                ),
                esquerda_rotulo="MSC (Passivo+PL) + (VPA−VPD)", esquerda=msc_fechado,
                direita_rotulo=f"DCA Passivo + PL ({ano_r})", direita=passivo_dca,
                diferenca=dif_enc, tolerancia=_TOL, divergente=_divergente(dif_enc),
                aplicavel=msc_fechado is not None and passivo_dca is not None,
                detalhe=(
                    f"MSC Passivo+PL={msc_passivo} + Resultado(VPA−VPD)={resultado}"
                    if resultado is not None else None
                ),
                source_refs=[
                    _source_msc(mes.periodo, mes.versao),
                    _source_dca(ano_r, dca_versao, anexo="DCA-Anexo I-AB"),
                ],
            )
        )

    n_div = sum(1 for c in checks if c.divergente and c.aplicavel)
    return ConciliacaoOut(
        cod_ibge=cod_ibge, ano=ano_r, as_of=as_of.isoformat() if as_of else None,
        tem_msc=ctx.tem_msc, tem_dca=ctx.tem_dca,
        n_checks=len(checks), n_divergencias=n_div, conciliado=n_div == 0,
        checks=checks,
        observacao=(
            "Conciliação do patrimônio: (1) rollup da MSC (pai = Σ filhos, convenção devedor "
            "líquido, exata); (2) identidade do Balanço (Ativo = Passivo + PL); (3) cruzamento "
            "MSC↔DCA no fechamento do exercício. Tolerância de R$ 1,00 (arredondamento). "
            "Divergência acima da tolerância é sinalizada — nunca silenciada."
        ),
        memoria={
            "tolerancia_reais": str(_TOL),
            "convencao_rollup": "devedor líquido (D +, C −); exibição em convenção natural",
            "fontes": "MSC Patrimonial (mensal) + DCA Anexos I-AB/I-HI (anual)",
        },
        source_refs=[
            *([_source_dca(ano_r, dca_versao)] if dca_versao is not None else []),
            *([_source_msc(mes.periodo, mes.versao)] if mes is not None else []),
        ],
    )


def _dif(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None or b is None:
        return None
    return a - b


def _divergente(dif: Decimal | None) -> bool:
    return dif is not None and abs(dif) > _TOL


# --- detalhe (Padrão de Detalhe / cabeçalho da página) ---
def _cobertura(
    session: Session, cod_ibge: str, *, tem_dca: bool, tem_msc: bool, meses: list[str]
) -> CoberturaPatrimonio:
    """Diz, em português, o que este ente publicou — e o que falta ingerir."""
    anos_dca = sorted(set(repository.anos_com_dca(session, cod_ibge=cod_ibge)))
    ausentes = [
        fonte
        for fonte, presente in (("DCA", tem_dca), ("MSC", tem_msc))
        if not presente
    ]
    if tem_dca and tem_msc:
        mensagem = (
            f"DCA em {len(anos_dca)} exercício(s) e MSC mensal em {len(meses)} mês(es): "
            "balanços e explorador de contas disponíveis."
        )
    elif tem_dca:
        mensagem = (
            f"DCA disponível em {', '.join(str(a) for a in anos_dca)}. Este ente **não** "
            "publica a MSC mensal no SICONFI — sem ela não há explorador de contas PCASP "
            "nem conciliação MSC↔DCA; os balanços anuais seguem completos."
        )
    elif tem_msc:
        mensagem = (
            "MSC mensal disponível, mas sem DCA ingerida: os balanços anuais dependem "
            "da Declaração de Contas Anuais."
        )
    else:
        mensagem = (
            "Nenhuma fonte patrimonial ingerida para este ente: sem DCA (balanços "
            "anuais) e sem MSC (saldos mensais por conta PCASP)."
        )
    return CoberturaPatrimonio(
        tem_dca=tem_dca, tem_msc=tem_msc, anos_dca=anos_dca, meses_msc=meses,
        fontes_ausentes=ausentes, mensagem=mensagem,
    )


def build_detalhe(
    session: Session, cod_ibge: str, ano: int | None, *, as_of: datetime | None = None
) -> PatrimonioDetalhe:
    anos = _anos_disponiveis(session, cod_ibge)
    if not anos and ano is None:
        # Ente sem nenhuma fonte patrimonial. Antes isto era 404 e a tela caía no bloco
        # de erro — o que empurrou a versão anterior a trocar o ente por um "de demo".
        # Falta de dado não é falha: é cobertura, e a tela precisa poder dizê-lo.
        return PatrimonioDetalhe(
            cod_ibge=cod_ibge,
            ano=datetime.now(UTC).year - 1,
            as_of=as_of.isoformat() if as_of else None,
            tem_msc=False,
            tem_dca=False,
            cobertura=_cobertura(
                session, cod_ibge, tem_dca=False, tem_msc=False, meses=[]
            ),
        )
    ano_r = _resolver_ano(session, cod_ibge, ano)
    ctx = ensure_materializado(session, cod_ibge, ano=ano_r, as_of=as_of)
    ativo = passivo_pl = pl = vpd = vpa = None
    balanco_fechado = None
    dca_versao = ctx.dca_versao
    mes = ctx.mes_referencia
    if dca_versao is not None:
        ativo, passivo_pl, pl = _ativo_passivo_pl_dca(session, cod_ibge, ano_r, dca_versao)
        dif = _dif(ativo, passivo_pl)
        balanco_fechado = dif is not None and abs(dif) <= _TOL
        vpd = repository.balanco_valor(
            session, cod_ibge=cod_ibge, ano=ano_r, tipo="variacoes",
            versao_entrega=dca_versao, cod_conta="3.0.0.0.0.00.00",
        )
        vpa = repository.balanco_valor(
            session, cod_ibge=cod_ibge, ano=ano_r, tipo="variacoes",
            versao_entrega=dca_versao, cod_conta="4.0.0.0.0.00.00",
        )
    conc = build_conciliacao(session, cod_ibge, ano_r, as_of=as_of)
    resultado = (vpa - vpd) if (vpa is not None and vpd is not None) else None
    source_ref = None
    if dca_versao is not None:
        source_ref = _source_dca(ano_r, dca_versao)
    elif mes is not None:
        source_ref = _source_msc(mes.periodo, mes.versao)
    return PatrimonioDetalhe(
        cod_ibge=cod_ibge, ano=ano_r, as_of=as_of.isoformat() if as_of else None,
        esfera=ctx.esfera, uf=ctx.uf, tem_msc=ctx.tem_msc, tem_dca=ctx.tem_dca,
        ativo=ativo, passivo_pl=passivo_pl, patrimonio_liquido=pl,
        vpd=vpd, vpa=vpa, resultado_patrimonial=resultado, balanco_fechado=balanco_fechado,
        meses_msc=[m.periodo for m in ctx.msc_meses],
        anos_disponiveis=anos or [ano_r],
        conciliado=conc.conciliado, n_divergencias=conc.n_divergencias,
        cobertura=_cobertura(
            session, cod_ibge, tem_dca=ctx.tem_dca, tem_msc=ctx.tem_msc,
            meses=[m.periodo for m in ctx.msc_meses],
        ),
        source_ref=source_ref,
    )


def build_balanco_comparacao(
    session: Session,
    cod_ibge: str,
    tipo: str,
    *,
    anos: int = 4,
    as_of: datetime | None = None,
) -> BalancoComparacaoOut:
    """Compara o mesmo balanço em vários exercícios, conta a conta (Sprint 25D).

    Um balanço isolado responde "quanto tenho"; a comparação responde "para onde isto
    está indo" — que é a pergunta de quem precisa justificar a variação do patrimônio ao
    tribunal de contas. Exercícios em que a conta não aparece ficam **nulos**: zero e
    "não publicado" são coisas diferentes num balanço.
    """
    if tipo not in _ANEXO_TIPO.values():
        raise AppError(
            status=422, title="Tipo inválido",
            detail=f"tipo deve ser um de {sorted(_ANEXO_TIPO.values())}.",
        )
    disponiveis = sorted(repository.anos_com_dca(session, cod_ibge=cod_ibge))
    if not disponiveis:
        raise AppError(
            status=404, title="DCA ausente",
            detail=f"Ente {cod_ibge} não tem DCA ingerida — nada a comparar.",
        )
    janela = disponiveis[-anos:]
    linhas: dict[str, ComparacaoLinha] = {}
    anos_lidos: list[int] = []
    anos_sem: list[int] = []
    fontes: list[SourceRef] = []
    for ano in janela:
        ctx = ensure_materializado(session, cod_ibge, ano=ano, as_of=as_of)
        if ctx.dca_versao is None:
            anos_sem.append(ano)
            continue
        registros = repository.read_balanco(
            session, cod_ibge=cod_ibge, ano=ano, tipo=tipo, versao_entrega=ctx.dca_versao
        )
        if not registros:
            anos_sem.append(ano)
            continue
        anos_lidos.append(ano)
        fontes.append(
            _source_dca(ano, ctx.dca_versao, anexo=f"DCA-Anexo {_TIPO_ANEXO[tipo]}")
        )
        for registro in registros:
            linha = linhas.setdefault(
                registro.cod_conta,
                ComparacaoLinha(
                    cod_conta=registro.cod_conta,
                    descricao=registro.conta_descricao,
                    nivel=registro.nivel,
                ),
            )
            # Um balanço pode trazer a mesma conta em mais de uma coluna; a comparação
            # anual usa a coluna principal do exercício (a primeira lida).
            if str(ano) not in linha.valores:
                linha.valores[str(ano)] = registro.valor
                if registro.valor is not None:
                    linha.anos_com_valor.append(ano)

    for linha in linhas.values():
        presentes = [a for a in anos_lidos if linha.valores.get(str(a)) is not None]
        if len(presentes) >= 2:
            primeiro = linha.valores[str(presentes[0])]
            ultimo = linha.valores[str(presentes[-1])]
            assert primeiro is not None and ultimo is not None
            linha.variacao_abs = ultimo - primeiro
            if primeiro != 0:
                linha.variacao_pct = (ultimo - primeiro) / abs(primeiro) * Decimal(100)

    observacao = None
    if anos_sem:
        observacao = (
            f"{len(anos_lidos)} de {len(janela)} exercícios têm o "
            f"{_TIPO_ROTULO[tipo]}; sem dado em {', '.join(str(a) for a in anos_sem)}."
        )
    return BalancoComparacaoOut(
        cod_ibge=cod_ibge,
        tipo=tipo,
        anexo=f"DCA-Anexo {_TIPO_ANEXO[tipo]}",
        anos=anos_lidos,
        anos_sem_balanco=anos_sem,
        observacao=observacao,
        linhas=sorted(linhas.values(), key=lambda item: item.cod_conta),
        memoria={
            "variacao": (
                "último exercício com valor − primeiro exercício com valor; percentual "
                "sobre o módulo do primeiro (contas de PL podem ser negativas)"
            ),
            "ausencia": "exercício sem a conta fica nulo — não é zero",
            "origem": f"DCA Anexo {_TIPO_ANEXO[tipo]} ({_TIPO_ROTULO[tipo]})",
        },
        source_refs=fontes,
    )
