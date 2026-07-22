"""Caixa & Restos a Pagar — funções puras de apuração (Sprint 10).

Base legal: LRF (LC 101/2000) art. 8º § único (vinculação de recursos), art. 42 (vedação de
fim de mandato) e art. 55 (RGF Anexo 5 — Disponibilidade de Caixa e Restos a Pagar); RREO
Anexo 7 (Demonstrativo dos Restos a Pagar). O SICONFI **não** traz código de fonte no Anexo
5: a descrição é a chave. Aqui mapeamos as colunas (cujos rótulos-letra ``(a)…(i)`` variam
entre layouts do STN) aos componentes, sempre pelo **prefixo descritivo**, e classificamos
cada fonte na hierarquia (Total → Não Vinculados / Vinculados / RPPS → fonte).

Regra invariante (§2.5 do CLAUDE.md): a suficiência é **fonte a fonte** — nunca se compensa
o superávit de uma fonte com o déficit de outra. O RPNP inscrito sem disponibilidade de
caixa (``rpnp_sem_lastro``) não conta na apuração dos mínimos (Sprint 11 consome).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

from app.shared.hierarchy import ltree_label, make_path

# --- hierarquia da fonte de recurso ---
ROOT_CODIGO = "TOTAL_RECURSOS"
ROOT_DESCRICAO = "Total dos recursos"
GRUPO_NAO_VINCULADOS = "NAO_VINCULADOS"
GRUPO_VINCULADOS = "VINCULADOS"
GRUPO_VINCULADOS_RPPS = "VINCULADOS_RPPS"

_GRUPOS: dict[str, tuple[str, bool]] = {
    # codigo → (descrição, vinculada)
    GRUPO_NAO_VINCULADOS: ("Recursos não vinculados", False),
    GRUPO_VINCULADOS: ("Recursos vinculados (exceto RPPS)", True),
    GRUPO_VINCULADOS_RPPS: ("Recursos vinculados ao RPPS", True),
}

# Medidas do fato de disponibilidade, na ordem canônica.
MEDIDAS_DISP = (
    "disp_bruta",
    "obrigacoes",
    "disp_liquida_antes",
    "rpnp_exercicio",
    "disp_liquida_apos",
    "rpnp_sem_lastro",
)

# Medidas do fato de restos a pagar (RREO Anexo 7).
MEDIDAS_RAP = (
    "rpp_inscritos",
    "rpp_pagos",
    "rpp_cancelados",
    "rpp_a_pagar",
    "rpnp_inscritos",
    "rpnp_liquidados",
    "rpnp_pagos",
    "rpnp_cancelados",
    "rpnp_a_pagar",
    "saldo_total",
)

# Semáforo (3 níveis) da suficiência por fonte.
STATUS_SUFICIENTE = "suficiente"  # disp_liquida_apos ≥ 0
STATUS_INSUF_RPNP = "insuficiente_rpnp"  # cobre obrigações mas não todo o RPNP inscrito
STATUS_DEFICIT = "deficit"  # não cobre nem as obrigações correntes
_SEMAFORO = {
    STATUS_SUFICIENTE: "verde",
    STATUS_INSUF_RPNP: "amarelo",
    STATUS_DEFICIT: "vermelho",
}

_ROMANO_RE = re.compile(r"\((?:I|II|III|IV|V)\)")


def normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(sem_acento.upper().split())


def slug_fonte(descricao: str) -> str:
    """Código estável (label ltree) a partir da descrição normalizada da fonte."""
    return ltree_label(normalizar(descricao).replace(" ", "_"))[:200] or "FONTE"


def e_totalizador(descricao: str | None) -> bool:
    """Linhas de subtotal/total do Anexo 5 (não são fontes-folha)."""
    norm = normalizar(descricao)
    return "TOTAL" in norm or bool(_ROMANO_RE.search(norm))


def grupo_descricao(codigo: str) -> str:
    """Descrição do grupo de vinculação (ou o próprio código, se desconhecido)."""
    return _GRUPOS.get(codigo, (codigo, False))[0]


def classificar_fonte(descricao: str) -> tuple[str, bool]:
    """Classifica a fonte em (grupo, vinculada) pela descrição (heurística auditável).

    - contém "RPPS" ⇒ vinculada ao RPPS (grupo III);
    - "NÃO VINCULAD" ou extraorçamentário ⇒ não vinculada (grupo I);
    - caso contrário ⇒ vinculada (grupo II).
    """
    norm = normalizar(descricao)
    if "RPPS" in norm:
        return GRUPO_VINCULADOS_RPPS, True
    if "NAO VINCULAD" in norm or "EXTRAORCAMENTARI" in norm:
        return GRUPO_NAO_VINCULADOS, False
    return GRUPO_VINCULADOS, True


# --- RGF Anexo 5: parsing das colunas (por prefixo descritivo, letra-agnóstico) ---
@dataclass(frozen=True)
class LinhaAnexo5:
    """Linha do RGF Anexo 5 já lida do silver (fonte, coluna, valor)."""

    conta: str | None  # descrição da fonte
    coluna: str | None
    valor: Decimal | None


def _papel_coluna(coluna: str | None) -> str | None:
    """Mapeia a coluna do Anexo 5 ao componente (rótulos-letra variam entre layouts)."""
    c = normalizar(coluna)
    if not c:
        return None
    if "DISPONIBILIDADE DE CAIXA BRUTA" in c:
        return "disp_bruta"
    if "DISPONIBILIDADE DE CAIXA LIQUIDA" in c and "ANTES DA INSCRICAO" in c:
        return "disp_liquida_antes"
    if "DISPONIBILIDADE DE CAIXA LIQUIDA" in c and "APOS A INSCRICAO" in c:
        return "disp_liquida_apos"
    # RP empenhados e não liquidados DO EXERCÍCIO = inscrição em RPNP do período (coluna g/h).
    if "RESTOS A PAGAR EMPENHADOS E NAO LIQUIDADOS DO EXERCICIO" in c:
        return "rpnp_exercicio"
    return None


@dataclass
class Disponibilidade:
    """Suficiência de uma fonte (medidas + status/semáforo derivados)."""

    fonte_descricao: str
    disp_bruta: Decimal | None = None
    disp_liquida_antes: Decimal | None = None
    rpnp_exercicio: Decimal | None = None
    disp_liquida_apos_reportada: Decimal | None = None

    @property
    def obrigacoes(self) -> Decimal | None:
        if self.disp_bruta is None or self.disp_liquida_antes is None:
            return None
        return self.disp_bruta - self.disp_liquida_antes

    @property
    def disp_liquida_apos(self) -> Decimal | None:
        if self.disp_liquida_antes is not None and self.rpnp_exercicio is not None:
            return self.disp_liquida_antes - self.rpnp_exercicio
        return self.disp_liquida_apos_reportada

    @property
    def rpnp_sem_lastro(self) -> Decimal | None:
        """RPNP inscrito sem lastro = max(0, RPNP − caixa disponível para RPNP)."""
        if self.rpnp_exercicio is None or self.disp_liquida_antes is None:
            return None
        caixa = self.disp_liquida_antes if self.disp_liquida_antes > 0 else Decimal(0)
        sem_lastro = self.rpnp_exercicio - caixa
        return sem_lastro if sem_lastro > 0 else Decimal(0)

    @property
    def status(self) -> str:
        apos, antes = self.disp_liquida_apos, self.disp_liquida_antes
        if antes is not None and antes < 0:
            return STATUS_DEFICIT
        if apos is not None and apos < 0:
            return STATUS_INSUF_RPNP
        return STATUS_SUFICIENTE

    @property
    def semaforo(self) -> str:
        return _SEMAFORO[self.status]

    @property
    def suficiente(self) -> bool:
        return self.status == STATUS_SUFICIENTE

    def medidas(self) -> dict[str, Decimal]:
        vals = {
            "disp_bruta": self.disp_bruta,
            "obrigacoes": self.obrigacoes,
            "disp_liquida_antes": self.disp_liquida_antes,
            "rpnp_exercicio": self.rpnp_exercicio,
            "disp_liquida_apos": self.disp_liquida_apos,
            "rpnp_sem_lastro": self.rpnp_sem_lastro,
        }
        return {k: v for k, v in vals.items() if v is not None}


def apurar_disponibilidades(linhas: list[LinhaAnexo5]) -> list[Disponibilidade]:
    """Agrega o Anexo 5 por fonte (somando poderes) e monta a suficiência de cada uma."""
    acc: dict[str, dict[str, Decimal]] = {}
    descricoes: dict[str, str] = {}
    for linha in linhas:
        if not linha.conta or linha.valor is None or e_totalizador(linha.conta):
            continue
        papel = _papel_coluna(linha.coluna)
        if papel is None:
            continue
        chave = slug_fonte(linha.conta)
        descricoes.setdefault(chave, linha.conta.strip())
        comp = acc.setdefault(chave, {})
        comp[papel] = comp.get(papel, Decimal(0)) + linha.valor

    disponibilidades = [
        Disponibilidade(
            fonte_descricao=descricoes[chave],
            disp_bruta=comp.get("disp_bruta"),
            disp_liquida_antes=comp.get("disp_liquida_antes"),
            rpnp_exercicio=comp.get("rpnp_exercicio"),
            disp_liquida_apos_reportada=comp.get("disp_liquida_apos"),
        )
        for chave, comp in acc.items()
    ]
    return sorted(disponibilidades, key=lambda d: d.fonte_descricao)


# --- RREO Anexo 7: parsing dos restos a pagar por poder/órgão ---
# slug do cod_conta (sem sufixo "Intra") → medida do fato_rap.
_RAP_ROLE: dict[str, str] = {
    "RestosAPagarProcessadosENaoProcessadosLiquidadosInscritosEmExerciciosAnteriores": "rpp_inscritos",  # noqa: E501
    "RestosAPagarProcessadosENaoProcessadosLiquidadosInscritosEmExercicioAnterior": "rpp_inscritos",
    "RestosAPagarProcessadosENaoProcessadosLiquidadosPagos": "rpp_pagos",
    "RestosAPagarProcessadosENaoProcessadosLiquidadosCancelados": "rpp_cancelados",
    "RestosAPagarProcessadosENaoProcessadosLiquidadosAPagar": "rpp_a_pagar",
    "RestosAPagarNaoProcessadosInscritosEmExerciciosAnteriores": "rpnp_inscritos",
    "RestosAPagarNaoProcessadosInscritosEmExercicioAnterior": "rpnp_inscritos",
    "RestosAPagarNaoProcessadosLiquidados": "rpnp_liquidados",
    "RestosAPagarNaoProcessadosPagos": "rpnp_pagos",
    "RestosAPagarNaoProcessadosCancelados": "rpnp_cancelados",
    "RestosAPagarNaoProcessadosAPagar": "rpnp_a_pagar",
    "SaldoTotal": "saldo_total",
}


@dataclass(frozen=True)
class LinhaAnexo7:
    conta: str | None  # poder/órgão
    cod_conta: str | None  # slug estável do STN
    valor: Decimal | None


def _rap_role(cod_conta: str | None) -> str | None:
    if not cod_conta:
        return None
    slug = cod_conta[:-5] if cod_conta.endswith("Intra") else cod_conta
    return _RAP_ROLE.get(slug)


@dataclass
class RapOrgao:
    orgao: str
    medidas: dict[str, Decimal] = field(default_factory=dict)


def apurar_rap(linhas: list[LinhaAnexo7]) -> list[RapOrgao]:
    """Agrega o Anexo 7 por poder (soma intra + não-intra); ignora totais e órgãos-folha."""
    acc: dict[str, dict[str, Decimal]] = {}
    for linha in linhas:
        if not linha.conta or linha.valor is None:
            continue
        # Mantém só as linhas de poder (evita duplicar com totais e órgãos aninhados).
        if not normalizar(linha.conta).startswith("PODER "):
            continue
        papel = _rap_role(linha.cod_conta)
        if papel is None:
            continue
        comp = acc.setdefault(linha.conta.strip(), {})
        comp[papel] = comp.get(papel, Decimal(0)) + linha.valor
    return sorted(
        (RapOrgao(orgao=orgao, medidas=comp) for orgao, comp in acc.items()),
        key=lambda r: r.orgao,
    )


def consolidar_rap(orgaos: list[RapOrgao]) -> dict[str, Decimal]:
    total: dict[str, Decimal] = {}
    for r in orgaos:
        for m, v in r.medidas.items():
            total[m] = total.get(m, Decimal(0)) + v
    return total


# --- art. 42 LRF (fim de mandato) ---
def fim_de_mandato(ano: int, esfera: str | None) -> bool:
    """Último ano do mandato: municipal ⇒ ano % 4 == 0 (2024, 2028…); estadual/federal ⇒
    ano % 4 == 2 (2022, 2026…)."""
    if esfera == "estadual":
        return ano % 4 == 2
    return ano % 4 == 0  # municipal (default)


def janela_art42(quadrimestre: int) -> bool:
    """Vedação alcança os **dois últimos quadrimestres** do exercício (Q2 e Q3)."""
    return quadrimestre in (2, 3)


# --- hierarquia: nós + agregação (folha = dado; pai = soma dos filhos) ---
@dataclass(frozen=True)
class FonteNode:
    codigo: str
    descricao: str
    parent_codigo: str | None
    nivel: int
    path: str
    vinculada: bool


def montar_hierarquia(disponibilidades: list[Disponibilidade]) -> list[FonteNode]:
    """Constrói dim_fonte_recurso (root → grupos → fontes) a partir das fontes apuradas."""
    root_path = ltree_label(ROOT_CODIGO)
    nodes: list[FonteNode] = [
        FonteNode(ROOT_CODIGO, ROOT_DESCRICAO, None, 1, root_path, False)
    ]
    grupos_usados: dict[str, tuple[str, bool]] = {}
    leaf_nodes: list[FonteNode] = []
    for disp in disponibilidades:
        grupo, vinculada = classificar_fonte(disp.fonte_descricao)
        grupos_usados[grupo] = _GRUPOS[grupo]
        grupo_path = make_path(root_path, grupo)
        codigo = slug_fonte(disp.fonte_descricao)
        leaf_nodes.append(
            FonteNode(
                codigo=codigo,
                descricao=disp.fonte_descricao,
                parent_codigo=grupo,
                nivel=3,
                path=make_path(grupo_path, codigo),
                vinculada=vinculada,
            )
        )
    for grupo, (descricao, vinculada) in grupos_usados.items():
        nodes.append(
            FonteNode(grupo, descricao, ROOT_CODIGO, 2, make_path(root_path, grupo), vinculada)
        )
    nodes.extend(leaf_nodes)
    return nodes


def agregar_medidas(
    nodes: list[FonteNode], proprias: dict[str, dict[str, Decimal]]
) -> dict[str, dict[str, Decimal]]:
    """Medidas efetivas: nó com dado próprio (folha) = dado; nó pai = soma dos filhos.

    A soma de subtotais/root é **informativa** (a suficiência que vale é fonte a fonte).
    """
    filhos: dict[str | None, list[str]] = {}
    for n in nodes:
        filhos.setdefault(n.parent_codigo, []).append(n.codigo)
    efetivas: dict[str, dict[str, Decimal]] = {}

    def resolver(codigo: str) -> dict[str, Decimal]:
        if codigo in efetivas:
            return efetivas[codigo]
        if codigo in proprias:
            efetivas[codigo] = dict(proprias[codigo])
            return efetivas[codigo]
        soma: dict[str, Decimal] = {}
        for filho in filhos.get(codigo, []):
            for m, v in resolver(filho).items():
                soma[m] = soma.get(m, Decimal(0)) + v
        efetivas[codigo] = soma
        return soma

    for n in nodes:
        resolver(n.codigo)
    return efetivas
