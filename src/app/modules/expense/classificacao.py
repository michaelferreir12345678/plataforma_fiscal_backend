"""Classificação da despesa — derivação a partir do RREO **real** (Sprint 6).

Base legal: Lei 4.320/1964; Portaria MOG 42/1999 (classificação **funcional**: Função →
Subfunção); Portaria Interministerial STN/SOF 163/2001 (**natureza da despesa**: Categoria
Econômica → Grupo/GND).

O SICONFI não traz código numérico:

- **Função** (Anexo 02): ``cod_conta`` é inútil (``RREO2TotalDespesas``); a função é
  reconhecida pela **descrição** (nome da Portaria 42) e a subfunção é a linha seguinte,
  na **ordem** (``linha_seq``), até a próxima função. O ``codigo`` do nó é o código da
  função (ex.: ``10``) e, na subfunção, ``10.<slug>``.
- **Natureza** (Anexo 01, lado despesa): o ``cod_conta`` é um slug estável
  (``PessoalEEncargosSociais``, ``Investimentos``…). O ``codigo`` do nó é o próprio slug;
  a hierarquia Categoria→Grupo vem de um mapa curado (layout fixo do STN).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.shared.hierarchy import ltree_label

# Sentinela do eixo não-aplicável no fato (linha de um eixo só; ver models/migration).
SENTINELA = "*"

EIXO_FUNCAO = "funcao"
EIXO_NATUREZA = "natureza"
EIXOS = (EIXO_FUNCAO, EIXO_NATUREZA)

# Medidas (estágios) do fato_despesa, na ordem canônica da cascata.
MEDIDAS = (
    "dotacao_inicial",
    "dotacao_atualizada",
    "empenhado",
    "liquidado",
    "pago",
    "inscrito_rap",
)

# Estágios encadeados que respeitam a invariante empenhado ≥ liquidado ≥ pago.
ESTAGIOS_EXECUCAO = ("empenhado", "liquidado", "pago")

# Classificação de rigidez de um grupo (GND).
RIGIDA = "rigida"
DISCRICIONARIA = "discricionaria"
SEMIVARIAVEL = "semivariavel"

# Funções de governo (Portaria MOG 42/1999), código de 2 dígitos → nome.
FUNCOES_CANONICAS = {
    "01": "Legislativa",
    "02": "Judiciária",
    "03": "Essencial à Justiça",
    "04": "Administração",
    "05": "Defesa Nacional",
    "06": "Segurança Pública",
    "07": "Relações Exteriores",
    "08": "Assistência Social",
    "09": "Previdência Social",
    "10": "Saúde",
    "11": "Trabalho",
    "12": "Educação",
    "13": "Cultura",
    "14": "Direitos da Cidadania",
    "15": "Urbanismo",
    "16": "Habitação",
    "17": "Saneamento",
    "18": "Gestão Ambiental",
    "19": "Ciência e Tecnologia",
    "20": "Agricultura",
    "21": "Organização Agrária",
    "22": "Indústria",
    "23": "Comércio e Serviços",
    "24": "Comunicações",
    "25": "Energia",
    "26": "Transporte",
    "27": "Desporto e Lazer",
    "28": "Encargos Especiais",
    "99": "Reserva de Contingência",
}


def normalizar_texto(texto: str | None) -> str:
    """Maiúsculas, sem acentos, espaços colapsados — para comparação robusta."""
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(sem_acento.upper().split())


# Nome da função (normalizado) → código. Para reconhecer as linhas de função no Anexo 02.
_FUNCAO_POR_NOME = {normalizar_texto(nome): cod for cod, nome in FUNCOES_CANONICAS.items()}
_SUBFUNCAO_PREFIXO = re.compile(r"^FU\d+\s*-\s*", re.IGNORECASE)

# --- natureza da despesa (Anexo 01): slug estável → pai / rigidez (layout STN fixo) ---
NATUREZA_PARENT: dict[str, str | None] = {
    "DespesasCorrentes": None,
    "DespesasDeCapital": None,
    "ReservaDeContingencia": None,
    "PessoalEEncargosSociais": "DespesasCorrentes",
    "JurosEEncargosDaDivida": "DespesasCorrentes",
    "OutrasDespesasCorrentes": "DespesasCorrentes",
    "Investimentos": "DespesasDeCapital",
    "InversoesFinanceiras": "DespesasDeCapital",
    "AmortizacaoDaDivida": "DespesasDeCapital",
}
# GND rígidos (obrigatórios): pessoal, juros e amortização da dívida.
NATUREZA_RIGIDEZ: dict[str, str] = {
    "PessoalEEncargosSociais": RIGIDA,
    "JurosEEncargosDaDivida": RIGIDA,
    "AmortizacaoDaDivida": RIGIDA,
    "Investimentos": DISCRICIONARIA,
    "InversoesFinanceiras": DISCRICIONARIA,
    "OutrasDespesasCorrentes": SEMIVARIAVEL,
}


@dataclass(frozen=True)
class NoDespesa:
    """Nó da hierarquia derivada."""

    codigo: str
    descricao: str
    parent_codigo: str | None
    nivel: int


def path_de(codigo: str) -> str:
    """Path ltree (rótulo único sanitizado). A sentinela ``'*'`` vira ``'_'``."""
    return "_" if codigo == SENTINELA else ltree_label(codigo)


def _e_total_despesa(descricao: str) -> bool:
    d = normalizar_texto(descricao)
    return (
        not d
        or "EXCETO INTRA" in d
        or "INTRA-ORCAMENTARIAS" in d
        or "INTRAORCAMENTARIAS" in d
        or d.startswith("TOTAL")
        or "SUBTOTAL" in d
    )


def classificar_coluna(coluna: str | None) -> str | None:
    """Mapeia a coluna de despesa do RREO (Anexos 01/02) para a medida (ou ``None``).

    Só o **acumulado até o bimestre** dos estágios é capturado (ignora "no bimestre",
    saldos, percentuais e colunas de receita).
    """
    c = normalizar_texto(coluna)
    if not c:
        return None
    if "%" in c or "PERCENTUAL" in c or "SALDO" in c:
        return None
    if "DOTACAO INICIAL" in c:
        return "dotacao_inicial"
    if "DOTACAO ATUALIZADA" in c:
        return "dotacao_atualizada"
    if "RESTOS A PAGAR" in c:  # inscritas em restos a pagar não processados
        return "inscrito_rap"
    if "NO BIMESTRE" in c and "ATE O BIMESTRE" not in c:
        return None  # queremos o acumulado (até o bimestre), não o valor do bimestre
    if "EMPENHAD" in c:
        return "empenhado"
    if "LIQUIDAD" in c:
        return "liquidado"
    if "PAGA" in c or "PAGO" in c:
        return "pago"
    return None


def derivar_funcao(linhas: list[tuple[int, str | None, str]]) -> list[tuple[str, NoDespesa]]:
    """Reconstrói Função→Subfunção do Anexo 02 a partir de ``(linha_seq, cod_conta, desc)``.

    Retorna ``(descricao_bruta, nó)`` — a descrição bruta serve para mapear as medidas de
    volta ao nó. Ignora a seção intra-orçamentária e as linhas de total.
    """
    nos: list[tuple[str, NoDespesa]] = []
    vistos: set[str] = set()
    func: str | None = None
    for _seq, cod_conta, desc in sorted(linhas, key=lambda t: t[0]):
        if cod_conta and "Intra" in cod_conta:
            continue  # seção intra-orçamentária (secundária)
        if _e_total_despesa(desc):
            continue
        code = _FUNCAO_POR_NOME.get(normalizar_texto(desc))
        if code is not None:
            func = code
            if code not in vistos:
                vistos.add(code)
                nos.append((desc, NoDespesa(code, desc, None, 1)))
        elif func is not None:
            limpo = _SUBFUNCAO_PREFIXO.sub("", desc).strip() or desc
            codigo = f"{func}.{ltree_label(normalizar_texto(limpo))}"
            if codigo not in vistos:
                vistos.add(codigo)
                nos.append((desc, NoDespesa(codigo, limpo, func, 2)))
    return nos


def derivar_natureza(linhas: list[tuple[str | None, str]]) -> list[NoDespesa]:
    """Categoria→Grupo do Anexo 01 (lado despesa), pelo slug estável ``cod_conta``."""
    nos: list[NoDespesa] = []
    vistos: set[str] = set()
    for cod_conta, desc in linhas:
        if not cod_conta or cod_conta not in NATUREZA_PARENT or cod_conta in vistos:
            continue
        vistos.add(cod_conta)
        parent = NATUREZA_PARENT[cod_conta]
        nos.append(NoDespesa(cod_conta, desc, parent, 1 if parent is None else 2))
    return nos


def classificar_rigidez(codigo: str) -> str | None:
    """Classifica um grupo de natureza (slug) em rígida/discricionária/semivariável."""
    return NATUREZA_RIGIDEZ.get(codigo)


def hierarquia_label(eixo: str) -> str:
    if eixo == EIXO_FUNCAO:
        return "Função → Subfunção (classificação funcional)"
    return "Categoria Econômica → Grupo de Natureza (natureza da despesa)"
