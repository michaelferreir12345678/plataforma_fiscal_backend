"""PCASP: normalização de códigos e hierarquia **posicional** (Sprint 12).

O Plano de Contas Aplicado ao Setor Público (PCASP) é uma hierarquia posicional: o
nível de uma conta é determinado pela posição dos dígitos significativos, e não por uma
coluna ``nivel``/``parent`` da fonte. Máscara canônica ``C.G.SG.T.ST.II.SS`` — 9 dígitos:
5 níveis de 1 dígito (classe→subtítulo) + 2 níveis de 2 dígitos (item, subitem).

Duas fontes do SICONFI expõem PCASP em formatos distintos, e este módulo as concilia num
único ``codigo`` canônico (a máscara pontuada, sem o ``P``):

- **MSC** (``silver.siconfi_msc``): ``conta_contabil`` = 9 dígitos crus (``111110100``),
  sempre no nível de folha (subitem).
- **DCA** (``silver.siconfi_dca`` Anexos I-AB Balanço Patrimonial / I-HI Variações):
  ``cod_conta`` = ``P`` + máscara pontuada (``P1.1.1.0.0.00.00``), em qualquer nível.

O rollup (pai = Σ filhos) roda na convenção **saldo devedor líquido** (débito ``+``,
crédito ``−``): nela a identidade contábil vale exatamente — o saldo devedor líquido da
classe 1 é o Ativo do Balanço Patrimonial, e o de cada nó-pai é a soma dos filhos. Para
exibição, converte-se de volta à convenção natural da natureza da classe (Ativo e Passivo
ambos positivos), ver :func:`saldo_natural`.

Base legal: PCASP (Portaria STN/MDF vigente); Balanço Patrimonial e Variações Patrimoniais
da DCA (Anexos I-AB e I-HI).
"""

from __future__ import annotations

import re
from decimal import Decimal

# Nome de cada classe PCASP (nível 1). Natureza devedora (D) nas ímpares, credora (C) nas
# pares — convenção fixa do PCASP, usada quando a fonte não traz descrição/natureza.
NOMES_CLASSE: dict[int, str] = {
    1: "Ativo",
    2: "Passivo e Patrimônio Líquido",
    3: "Variação Patrimonial Diminutiva (VPD)",
    4: "Variação Patrimonial Aumentativa (VPA)",
    5: "Controles da Aprovação do Planejamento e Orçamento",
    6: "Controles da Execução do Planejamento e Orçamento",
    7: "Controles Devedores",
    8: "Controles Credores",
}
NOMES_NIVEL: dict[int, str] = {
    1: "Classe",
    2: "Grupo",
    3: "Subgrupo",
    4: "Título",
    5: "Subtítulo",
    6: "Item",
    7: "Subitem",
}

# Largura de cada segmento da máscara (posição → nº de dígitos).
_SEG_WIDTHS = (1, 1, 1, 1, 1, 2, 2)  # soma = 9
_ONLY_DIGITS = re.compile(r"\D")
_PADRAO_RE = re.compile(r"^[0-9.]+$")


def is_conta_padrao(raw: str | None) -> bool:
    """Se ``raw`` é uma conta PCASP canônica ("Padrão"), não um quadro suplementar.

    O Balanço Patrimonial da DCA (Anexo I-AB) traz, além da conta PCASP (``P1.1.…``), os
    quadros suplementares da Lei 4.320 — Ativo **Financeiro**/**Permanente** — com o mesmo
    código + sufixo ``F``/``P`` (``P1.1.0.0.0.00.00F``). Sem esse filtro, ``digits9``
    normalizaria os três ao mesmo código, duplicando/contando em dobro. Só o "Padrão"
    (código = ``P`` + apenas dígitos e pontos) é a linha do Balanço PCASP.
    """
    if raw is None:
        return False
    s = str(raw).strip()
    if s[:1] in ("P", "p"):
        s = s[1:]
    return bool(s) and bool(_PADRAO_RE.match(s))


def digits9(raw: str | None) -> str | None:
    """Normaliza um código cru (MSC 9-dígitos ou DCA ``P1.1.…``) para 9 dígitos.

    Remove o prefixo ``P`` e a pontuação, mantém só dígitos e completa/trunca para 9.
    Retorna ``None`` se não sobrar dígito algum (linha não-PCASP — ex.: slugs da DCA
    orçamentária ``ReceitasExcetoIntraOrcamentarias``).
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if s[:1] in ("P", "p"):
        s = s[1:]
    s = _ONLY_DIGITS.sub("", s)
    if not s:
        return None
    return (s + "000000000")[:9]


def _segments(d9: str) -> list[str]:
    segs: list[str] = []
    pos = 0
    for w in _SEG_WIDTHS:
        segs.append(d9[pos : pos + w])
        pos += w
    return segs


def mask(d9: str) -> str:
    """Máscara pontuada canônica ``C.G.SG.T.ST.II.SS`` a partir dos 9 dígitos."""
    return ".".join(_segments(d9))


def nivel(d9: str) -> int:
    """Nível significativo (1..7): o índice do segmento não-zero mais profundo."""
    segs = _segments(d9)
    lvl = 1
    for i, seg in enumerate(segs):
        if int(seg) != 0:
            lvl = i + 1
    return lvl


def _truncate(d9: str, level: int) -> str:
    """Zera os segmentos abaixo de ``level`` (mantém os ``level`` primeiros)."""
    segs = _segments(d9)
    for i in range(level, len(segs)):
        segs[i] = "0" * _SEG_WIDTHS[i]
    return "".join(segs)


def classe(d9: str) -> int:
    """Classe PCASP (1..8) = primeiro dígito."""
    return int(d9[0])


def natureza_classe(classe_num: int) -> str:
    """Natureza da classe: ``D`` (devedora) nas ímpares, ``C`` (credora) nas pares."""
    return "D" if classe_num % 2 == 1 else "C"


def signed(valor: Decimal | None, natureza: str | None) -> Decimal:
    """Saldo na convenção **devedor líquido**: ``+valor`` se devedora, ``−valor`` se credora.

    Nessa convenção a identidade contábil é exata (Σ filhos = pai), pois somam-se débitos
    e subtraem-se créditos independentemente da classe do nó.
    """
    if valor is None:
        return Decimal(0)
    return valor if (natureza or "D").upper().startswith("D") else -valor


def saldo_natural(saldo_devedor: Decimal | None, classe_num: int) -> Decimal | None:
    """Converte o saldo devedor líquido à convenção natural da classe (para exibição).

    Classes devedoras (Ativo, VPD) mantêm o sinal; credoras (Passivo, VPA) invertem — de
    modo que Ativo e Passivo apareçam ambos positivos, como no Balanço Patrimonial.
    """
    if saldo_devedor is None:
        return None
    return saldo_devedor if natureza_classe(classe_num) == "D" else -saldo_devedor


class ContaPcasp:
    """Nó normalizado do PCASP (código canônico, nível, pai, ancestrais)."""

    __slots__ = ("d9", "codigo", "nivel", "classe")

    def __init__(self, d9: str) -> None:
        self.d9 = d9
        self.codigo = mask(d9)
        self.nivel = nivel(d9)
        self.classe = classe(d9)

    @property
    def natureza(self) -> str:
        return natureza_classe(self.classe)

    def parent_codigo(self) -> str | None:
        """Código canônico do pai (nível imediatamente acima), ou ``None`` na classe."""
        if self.nivel <= 1:
            return None
        return mask(_truncate(self.d9, self.nivel - 1))

    def ancestral_d9s(self) -> list[str]:
        """9-dígitos de todos os ancestrais + o próprio nó, da raiz (classe) até ele."""
        return [_truncate(self.d9, lvl) for lvl in range(1, self.nivel + 1)]


def parse(raw: str | None) -> ContaPcasp | None:
    """Converte um código cru (MSC/DCA) num :class:`ContaPcasp`, ou ``None`` se não-PCASP."""
    d9 = digits9(raw)
    return ContaPcasp(d9) if d9 is not None else None


def require(raw: str) -> ContaPcasp:
    """Como :func:`parse`, mas para códigos já sabidamente PCASP (erro se inválido)."""
    conta = parse(raw)
    if conta is None:
        raise ValueError(f"código PCASP inválido: {raw!r}")
    return conta


def nome_no(codigo: str) -> str:
    """Nome de fallback quando a fonte não traz descrição (usa nome de classe + código)."""
    d9 = digits9(codigo)
    if d9 is None:
        return codigo
    conta = ContaPcasp(d9)
    if conta.nivel == 1:
        return NOMES_CLASSE.get(conta.classe, f"Classe {conta.classe}")
    return f"{codigo} · {NOMES_NIVEL.get(conta.nivel, 'Conta')}"
