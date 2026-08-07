"""Sprint E1 — caracterização da conversão bimestre → RGF antes de consolidá-la (A25).

A A25 achou a mesma tradução reimplementada **seis vezes, em duas semânticas**: para o
bimestre ímpar, três módulos diziam "não há RGF correspondente" (``None``) e os outros três
apontavam um (quadrimestre por teto). Nenhuma das seis conhecia o RGF **semestral** do
município com menos de 50 mil habitantes (LRF, art. 63, II).

A tentação seria escolher uma das duas e passar por cima da outra. Seria errado: as duas
respondem perguntas diferentes, e as duas têm uso legítimo — o painel de qualidade não
deve conferir a DCL contra um quadrimestre que ainda não fechou, e o benchmarking não deve
perder a linhagem do numerador de pessoal só porque o usuário abriu um bimestre ímpar. O
que estava errado era a escolha ser **implícita**.

Por isso a consolidação preserva o comportamento de cada chamador, com a semântica
declarada no ponto da chamada (``CICLO_FECHADO`` × ``CICLO_CORRENTE``). Este arquivo é a
prova: as seis implementações antigas estão reproduzidas literalmente abaixo e comparadas,
caso a caso, com o que os módulos devolvem depois da consolidação.

As **únicas** diferenças intencionais estão isoladas nos dois últimos testes, com o motivo
de cada uma. Nenhuma delas é alcançável pelos chamadores reais, que só passam período
bimestral do RREO.
"""

from __future__ import annotations

import math
import re

import pytest

from app.modules.benchmark import service as benchmark_service
from app.modules.cash_rap import service as cash_rap_service
from app.modules.dashboard import estadual_service
from app.modules.quality import service as quality_service
from app.modules.reports import service as reports_service
from app.modules.result import service as result_service
from app.shared import periodo as periodo_util

_ANOS = ("2023", "2024", "2025")

#: Domínio que os chamadores reais produzem: o período RREO bimestral, mais o anual e o
#: mensal, que aparecem quando o usuário escolhe outro recorte na tela.
DOMINIO_REAL: tuple[str, ...] = tuple(
    [f"{ano}-B{b}" for ano in _ANOS for b in range(1, 7)]
    + [f"{ano}" for ano in _ANOS]
    + [f"{ano}-M{m:02d}" for ano in _ANOS for m in (1, 7, 12)]
    + ["", "lixo"]
)


# --------------------------------------------------------------------------- #
# As seis implementações originais, copiadas verbatim do código anterior à E1
# --------------------------------------------------------------------------- #
def _antigo_quality(periodo_rreo: str) -> str | None:
    try:
        ano, bim = periodo_rreo.split("-B", 1)
        numero = int(bim)
    except (ValueError, TypeError):
        return None
    return f"{ano}-Q{(numero + 1) // 2}" if numero % 2 == 0 else None


def _antigo_cash_rap(periodo_rreo: str) -> str | None:
    m = re.match(r"^(\d{4})-B([1-6])$", periodo_rreo)
    if m is None:
        return None
    b = int(m.group(2))
    return f"{m.group(1)}-Q{b // 2}" if b % 2 == 0 else None


_PERIODO_BIMESTRAL_RE = re.compile(r"^(\d{4})-B([1-6])$")


def _antigo_result(periodo: str) -> str | None:
    m = _PERIODO_BIMESTRAL_RE.match(periodo)
    if m is None:
        return None
    b = int(m.group(2))
    return f"{m.group(1)}-Q{b // 2}" if b % 2 == 0 else None


def _antigo_benchmark(periodo_rreo: str) -> str | None:
    if "-Q" in periodo_rreo:
        return periodo_rreo
    try:
        ano, bimestre = periodo_rreo.split("-B", 1)
        numero = int(bimestre)
    except (ValueError, TypeError):
        return None
    return f"{ano}-Q{(numero + 1) // 2}"


def _antigo_estadual(periodo_rreo: str) -> str | None:
    try:
        ano_s, bim_s = periodo_rreo.split("-B")
        return f"{ano_s}-Q{math.ceil(int(bim_s) / 2)}"
    except (ValueError, AttributeError):
        return None


def _antigo_reports(periodo: str) -> str:
    match = re.fullmatch(r"(\d{4})-B([1-6])", periodo)
    if match:
        ano, bimestre = match.groups()
        return f"{ano}-Q{(int(bimestre) + 1) // 2}"
    if re.fullmatch(r"\d{4}-Q[1-3]", periodo):
        return periodo
    return f"{periodo[:4]}-Q3"


_CARACTERIZACAO = (
    ("quality", _antigo_quality, quality_service._rgf_de_rreo),
    ("cash_rap", _antigo_cash_rap, cash_rap_service.rgf_periodo_de_rreo),
    ("result", _antigo_result, result_service._periodo_rgf),
    ("benchmark", _antigo_benchmark, benchmark_service._rgf_periodo),
    ("estadual", _antigo_estadual, estadual_service.rgf_periodo_de),
    ("reports", _antigo_reports, reports_service._rgf_period),
)


@pytest.mark.parametrize(
    ("modulo", "antigo", "novo"),
    _CARACTERIZACAO,
    ids=[caso[0] for caso in _CARACTERIZACAO],
)
def test_a_consolidacao_preserva_o_resultado_de_cada_chamador(modulo, antigo, novo) -> None:
    """Nenhum número muda no domínio que os chamadores realmente produzem.

    Isto é o que autoriza a consolidação: a A5 e a B2-c já custaram caro por mexer numa
    regra fiscal antes de medir o que ela mudava. Aqui a medição vem primeiro.
    """
    divergencias = [
        (p, antigo(p), novo(p)) for p in DOMINIO_REAL if antigo(p) != novo(p)
    ]
    assert not divergencias, (
        f"{modulo}: a regra consolidada mudou o resultado em {divergencias}"
    )


#: A decisão registrada no §10 do documento, em forma de tabela: **os seis bimestres**,
#: nas duas semânticas, para a cadência quadrimestral (a regra geral do RGF).
#:
#: A coluna do ciclo fechado é onde as três cópias conservadoras respondiam ``None``; a do
#: ciclo corrente, onde as três de teto respondiam um quadrimestre. Nos bimestres **pares**
#: as duas coincidem — é exatamente por isso que a divergência sobreviveu seis vezes sem
#: ninguém notar: o caso mais comum na tela não a expõe.
_TABELA_QUADRIMESTRAL: tuple[tuple[str, str | None, str], ...] = (
    ("2024-B1", None, "2024-Q1"),
    ("2024-B2", "2024-Q1", "2024-Q1"),
    ("2024-B3", None, "2024-Q2"),
    ("2024-B4", "2024-Q2", "2024-Q2"),
    ("2024-B5", None, "2024-Q3"),
    ("2024-B6", "2024-Q3", "2024-Q3"),
)


@pytest.mark.parametrize(
    ("bimestre", "fechado", "corrente"),
    _TABELA_QUADRIMESTRAL,
    ids=[caso[0] for caso in _TABELA_QUADRIMESTRAL],
)
def test_cada_bimestre_devolve_o_que_a_decisao_registrada_manda(
    bimestre: str, fechado: str | None, corrente: str
) -> None:
    """O bimestre ímpar é onde as seis cópias divergiam entre si.

    Consolidar não podia significar apagar a diferença: significa **nomeá-la**, para que a
    escolha apareça no ponto da chamada em vez de depender de qual módulo se está lendo.
    A tabela acima é a decisão do §10 escrita como teste — se alguém mudar a semântica,
    quebra aqui, no bimestre exato, e não numa página fiscal três sprints depois.
    """
    assert periodo_util.em_periodo_rgf(bimestre, quando=periodo_util.CICLO_FECHADO) == fechado
    assert periodo_util.em_periodo_rgf(bimestre, quando=periodo_util.CICLO_CORRENTE) == corrente


def test_o_ciclo_fechado_e_o_padrao_conservador() -> None:
    """Quem não declara a semântica recebe a que não inventa RGF onde não há.

    O padrão importa: um chamador novo que esqueça o argumento erra para o lado de
    "não há RGF correspondente", que aparece como ausência — nunca para o lado de apontar
    um quadrimestre que ainda não fechou, que aparece como número.
    """
    assert periodo_util.em_periodo_rgf("2024-B3") is None
    assert periodo_util.em_periodo_rgf("2024-B4") == "2024-Q2"


def test_a_cadencia_semestral_do_art_63_deixou_de_ser_um_ponto_cego() -> None:
    """LRF, art. 63, II: município com menos de 50 mil habitantes publica RGF semestral.

    Nenhuma das seis cópias conhecia essa cadência. Para esses entes, a conversão
    simplesmente não achava o RGF — e o sintoma na tela era "sem dado", que se lê como
    lacuna de ingestão quando na verdade era a tradução que faltava.
    """
    semestral = periodo_util.CADENCIA_SEMESTRAL
    assert periodo_util.em_periodo_rgf("2024-B3", cadencia=semestral) == "2024-S1"
    assert periodo_util.em_periodo_rgf("2024-B6", cadencia=semestral) == "2024-S2"
    # B1/B2/B4/B5 não fecham semestre: no ciclo fechado, não há RGF correspondente.
    for bimestre in ("2024-B1", "2024-B2", "2024-B4", "2024-B5"):
        assert periodo_util.em_periodo_rgf(bimestre, cadencia=semestral) is None
    # No ciclo corrente, cada bimestre cai dentro de um semestre.
    esperado = {
        "2024-B1": "2024-S1", "2024-B2": "2024-S1", "2024-B3": "2024-S1",
        "2024-B4": "2024-S2", "2024-B5": "2024-S2", "2024-B6": "2024-S2",
    }
    for bimestre, semestre in esperado.items():
        assert (
            periodo_util.em_periodo_rgf(
                bimestre, cadencia=semestral, quando=periodo_util.CICLO_CORRENTE
            )
            == semestre
        )


def test_a_conversao_e_a_inversa_de_em_bimestre() -> None:
    """As duas direções da mesma regra de calendário moram no mesmo módulo, e fecham."""
    for quadrimestre in ("2024-Q1", "2024-Q2", "2024-Q3"):
        bimestre = periodo_util.em_bimestre(quadrimestre)
        assert bimestre is not None
        assert periodo_util.em_periodo_rgf(bimestre) == quadrimestre
    for semestre in ("2024-S1", "2024-S2"):
        bimestre = periodo_util.em_bimestre(semestre)
        assert bimestre is not None
        assert (
            periodo_util.em_periodo_rgf(bimestre, cadencia=periodo_util.CADENCIA_SEMESTRAL)
            == semestre
        )


def test_diferenca_intencional_1_periodo_ja_em_forma_de_rgf_volta_como_esta() -> None:
    """Idempotência: pedir "o RGF de 2024-Q2" responde 2024-Q2, não ``None``.

    ``quality``, ``result`` e ``cash_rap`` devolviam ``None`` aqui, por acidente de
    implementação (o ``split('-B')`` falhava). ``benchmark`` e ``reports`` já devolviam o
    próprio período. Nenhum dos três primeiros é alcançável por esse caminho — todos
    recebem período RREO —, e ``None`` seria a resposta errada se fosse.
    """
    assert quality_service._rgf_de_rreo("2024-Q2") == "2024-Q2"
    assert result_service._periodo_rgf("2024-Q2") == "2024-Q2"
    assert cash_rap_service.rgf_periodo_de_rreo("2024-Q2") == "2024-Q2"
    assert benchmark_service._rgf_periodo("2024-Q2") == "2024-Q2"
    assert estadual_service.rgf_periodo_de("2024-Q2") == "2024-Q2"


def test_diferenca_intencional_2_bimestre_fora_da_faixa_nao_inventa_quadrimestre() -> None:
    """``2024-B7`` não existe, e ``2024-Q4`` existe menos ainda.

    O ``benchmark`` antigo fazia ``(7+1)//2`` e devolvia um quadrimestre impossível; o
    ``estadual`` fazia ``ceil(7/2)`` e chegava ao mesmo lugar. A regra canônica recusa a
    entrada em vez de produzir um período que o calendário fiscal não tem.
    """
    for fora_da_faixa in ("2024-B0", "2024-B7", "2024-B9"):
        assert benchmark_service._rgf_periodo(fora_da_faixa) is None
        assert estadual_service.rgf_periodo_de(fora_da_faixa) is None
        assert periodo_util.em_periodo_rgf(fora_da_faixa) is None


def test_diferenca_intencional_3_ano_malformado_nao_vira_periodo_fabricado() -> None:
    """``20XX-B2`` virava ``20XX-Q1`` em três dos seis, e ninguém percebia.

    Um ano que não é um ano produzia um período aparentemente válido, que seguia adiante
    como chave de consulta. A regra canônica exige o formato canônico (§6.6) e devolve
    ``None`` — a ausência aparece como ausência, que é a regra de produto.
    """
    for modulo, novo in (
        ("quality", quality_service._rgf_de_rreo),
        ("benchmark", benchmark_service._rgf_periodo),
        ("estadual", estadual_service.rgf_periodo_de),
    ):
        assert novo("20XX-B2") is None, modulo
    # ``reports`` mantém o fallback local declarado e continua devolvendo um período.
    assert reports_service._rgf_period("20XX-B2") == "20XX-Q3"


def test_cadencia_ou_semantica_desconhecida_falha_alto() -> None:
    """Erro de programação não vira ``None`` silencioso — vira exceção."""
    with pytest.raises(ValueError):
        periodo_util.em_periodo_rgf("2024-B2", cadencia="trimestral")
    with pytest.raises(ValueError):
        periodo_util.em_periodo_rgf("2024-B2", quando="talvez")


def test_as_seis_copias_deixaram_de_existir() -> None:
    """A ficha da E1 pede a consolidação, não a coexistência.

    A catraca de ``test_auditoria_a0r.py`` é unilateral (aceita a redução); esta asserção
    é o outro lado: prova que a redução de fato aconteceu, e não que apenas foi permitida.
    """
    from pathlib import Path

    modulos = Path(__file__).resolve().parents[1] / "src" / "app" / "modules"
    assinatura = re.compile(r"-Q\{[^}]*(?://\s*2|/\s*2|ceil)")
    sobreviventes = sorted(
        str(caminho.relative_to(modulos)).replace("\\", "/")
        for caminho in modulos.glob("*/*.py")
        if assinatura.search(caminho.read_text(encoding="utf-8"))
    )
    assert not sobreviventes, (
        f"ainda há cópia da conversão bimestre→quadrimestre em {sobreviventes} — "
        "a fonte única é app/shared/periodo.py::em_periodo_rgf (§6.6)"
    )
