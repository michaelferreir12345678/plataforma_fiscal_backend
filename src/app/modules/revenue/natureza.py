"""Natureza da receita — derivação da hierarquia a partir do RREO **real** (Sprint 5).

Base legal: Lei 4.320/1964, art. 11 e Ementário da Natureza de Receita (STN/MTO). O
SICONFI **não expõe** o código numérico pontuado (ex.: ``1.7.1``); cada linha do RREO
Anexo 01 traz uma **descrição** (``conta``) e um **slug estável** (``cod_conta``, ex.:
``ReceitasCorrentes``). A hierarquia **Categoria → Origem → Espécie** é reconstruída pela
**ordem** das linhas (``linha_seq``) combinada à caixa do texto:

- **Categoria** (nível 1): slugs ``ReceitasCorrentes`` / ``ReceitasDeCapital``.
- **Origem** (nível 2): linhas em CAIXA ALTA (ex.: ``IMPOSTOS, TAXAS…``, ``TRANSFERÊNCIAS
  CORRENTES``).
- **Espécie** (nível 3): linhas em caixa mista (ex.: ``Impostos``, ``Contribuições Sociais``).

Totais/subtotais e a seção **intra-orçamentária** ficam fora da árvore. O ``codigo`` do nó
é o próprio ``cod_conta`` (identificador estável do STN), servindo de rótulo ltree válido.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

# Medidas do fato_receita, na ordem canônica.
MEDIDAS = (
    "previsto_inicial",
    "previsto_atualizado",
    "arrecadado_bimestre",
    "arrecadado_acum",
    "deducoes",
)

# Slugs das categorias econômicas de receita (nível 1). Intra-orçamentárias ficam de fora.
CATEGORIA_SLUGS = {"ReceitasCorrentes", "ReceitasDeCapital"}

# Origens de receita **transferida** (base do indicador de dependência): slug com este prefixo.
TRANSFERIDA_PREFIXO = "Transferencias"

# Slugs de totalizadores conhecidos (fora da árvore).
_TOTAL_SLUGS = {
    "ReceitasExcetoIntraOrcamentarias",
    "SubtotalDasReceitas",
    "TotalReceitas",
    "TotalReceitasComDeficit",
    "ReceitasIntraOrcamentarias",
    "ReceitasIntraOrcamentariasTotal",
}


def normalizar_texto(texto: str | None) -> str:
    """Maiúsculas, sem acentos, espaços colapsados — para comparação robusta."""
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(sem_acento.upper().split())


def is_transferida_origem(cod_conta: str | None) -> bool:
    """Se o slug é uma origem (ou descendente) de receita transferida."""
    return bool(cod_conta) and str(cod_conta).startswith(TRANSFERIDA_PREFIXO)


def _e_total(cod_conta: str | None, descricao: str) -> bool:
    """Linha de total/subtotal ou da seção intra-orçamentária (fora da árvore)."""
    if not cod_conta or "Intra" in cod_conta or cod_conta in _TOTAL_SLUGS:
        return True
    d = normalizar_texto(descricao)
    return "SUBTOTAL" in d or d.startswith("TOTAL") or "DEFICIT" in d or "SUPERAVIT" in d


def _e_cabecalho(descricao: str) -> bool:
    """Caixa alta ⇒ nível de agregação (origem)."""
    letras = [c for c in descricao if c.isalpha()]
    return bool(letras) and all(c.isupper() for c in letras)


@dataclass(frozen=True)
class NoReceita:
    """Nó da hierarquia derivada (código = ``cod_conta``)."""

    codigo: str
    descricao: str
    parent_codigo: str | None
    nivel: int


# Vocabulário do bloco de **despesa** do Anexo 01. O Anexo 01 é o *Balanço Orçamentário*:
# traz a receita E a despesa. As colunas de despesa também dizem "ATÉ O BIMESTRE" / "NO
# BIMESTRE" (ex.: "DESPESAS EMPENHADAS ATÉ O BIMESTRE (f)"), então precisam ser rejeitadas
# **antes** das regras de arrecadação — caso contrário a despesa entra no fato_receita e,
# por tabela, vira nó da hierarquia de origens de receita.
_MARCAS_DESPESA = (
    "DESPESA",
    "DOTACAO",
    "EMPENHAD",
    "LIQUIDAD",
    "PAGAS",
    "INSCRITAS EM RESTOS",
)


def e_coluna_de_despesa(coluna: str | None) -> bool:
    """A coluna pertence ao bloco de despesa do Balanço Orçamentário?"""
    c = normalizar_texto(coluna)
    return bool(c) and any(marca in c for marca in _MARCAS_DESPESA)


def classificar_coluna(coluna: str | None) -> str | None:
    """Mapeia a coluna do Anexo 01 para a medida do ``fato_receita`` (ou ``None``).

    Colunas percentuais/saldo não entram — o percentual de realização é derivado.
    Colunas do bloco de despesa também não: elas pertencem ao Módulo 5 (Despesa).
    """
    c = normalizar_texto(coluna)
    if not c:
        return None
    if e_coluna_de_despesa(coluna):
        return None
    if "%" in c or "PERCENTUAL" in c or "SALDO" in c:
        return None
    if "DEDUC" in c:
        return "deducoes"
    if "PREVISAO INICIAL" in c:
        return "previsto_inicial"
    if "PREVISAO ATUALIZADA" in c:
        return "previsto_atualizado"
    if "ATE O BIMESTRE" in c or "ACUMULAD" in c:
        return "arrecadado_acum"
    if "NO BIMESTRE" in c:
        return "arrecadado_bimestre"
    return None


def construir_arvore(linhas: list[tuple[int, str, str]]) -> list[NoReceita]:
    """Reconstrói a árvore da receita a partir de ``(linha_seq, cod_conta, descricao)``.

    ``linhas`` deve conter uma entrada por ``cod_conta`` (a primeira ocorrência),
    **ordenada por ``linha_seq``**. Retorna os nós na ordem de leitura (pais antes
    dos filhos), com ``parent_codigo`` e ``nivel`` resolvidos pela ordem + caixa.
    """
    nos: list[NoReceita] = []
    categoria: str | None = None
    origem: str | None = None
    for _seq, cod, desc in sorted(linhas, key=lambda t: t[0]):
        if _e_total(cod, desc):
            continue
        if cod in CATEGORIA_SLUGS:
            categoria, origem = cod, None
            nos.append(NoReceita(cod, desc, None, 1))
        elif _e_cabecalho(desc):
            origem = cod
            nos.append(NoReceita(cod, desc, categoria, 2))
        else:
            nos.append(NoReceita(cod, desc, origem or categoria, 3))
    return nos
