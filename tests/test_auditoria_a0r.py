"""Sprint A0R — regressões da retomada da auditoria (P2, P3, P4 do §20).

Estes testes são a contraparte executável do relatório em ``docs/evolucao_plataforma.md``
§5.1.2. Dois grupos, e **nenhuma asserção depende do banco** (a fixture de schema da
suíte continua valendo para o pacote inteiro; o que não existe aqui é consulta):

1. **Regra fiscal em função pura** — a apuração do RGF Anexo 01 (DTP, exclusões do
   art. 19, §1º, colunas de valor) vive em ``personnel/pessoal.py`` sem dependência de
   sessão. É onde a P2 e a P5 se decidem, e é testável sem infraestrutura.
2. **Inventário estático do repositório** — rastreabilidade (``source_ref``), gate de
   escopo por ente e a tradução bimestre→quadrimestre. Todas as asserções são
   **catracas**: aceitam que o inventário melhore (um módulo ganhe ``source_ref``, uma
   rota ganhe gate, uma cópia da regra de período desapareça) e falham quando ele piora.
   Uma catraca que travasse o defeito no lugar seria pior que nenhuma — quem corrigisse
   veria a suíte vermelha por ter acertado.

O que **não** está aqui: a execução dos nove checks de qualidade e as consultas que medem
o alcance dos achados A23 e A24 — as duas exigem o banco, e a sessão que produziu este
arquivo não tinha nenhum. Os comandos estão registrados no §5.1.2.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from app.modules.personnel import pessoal

_RAIZ = Path(__file__).resolve().parents[1]
_APP = _RAIZ / "src" / "app"
_MODULOS = _APP / "modules"


# --------------------------------------------------------------------------- #
# P2 — RGF Anexo 01: DTP, exclusões do art. 19, §1º e colunas de valor
# --------------------------------------------------------------------------- #
def test_dtp_publicada_pelo_ente_manda_no_numero_do_limite() -> None:
    """Havendo DTP (VI) publicada, ela é a líquida e as exclusões viram derivadas.

    É a decisão de domínio registrada em ``pessoal.apurar``: o limite do art. 20 é medido
    sobre o número que o ente publicou, não sobre uma reconstrução nossa.
    """
    ap = pessoal.apurar(
        {
            pessoal.BRUTA: Decimal("1000"),
            pessoal.DTP: Decimal("900"),
            pessoal.EXCL_INDENIZACAO: Decimal("40"),
            pessoal.EXCL_INATIVOS_RPPS: Decimal("60"),
        },
        rpps=True,
    )
    assert ap.despesa_liquida == Decimal("900")
    assert ap.exclusoes == Decimal("100"), "exclusões = bruta − DTP publicada"


def test_dtp_publicada_prevalece_sobre_a_liquida_reportada() -> None:
    """Os dois existem no anexo (III e VI); a DTP é a que o limite usa."""
    ap = pessoal.apurar(
        {
            pessoal.BRUTA: Decimal("1000"),
            pessoal.LIQUIDA_REPORTADA: Decimal("950"),
            pessoal.DTP: Decimal("900"),
        },
        rpps=False,
    )
    assert ap.despesa_liquida == Decimal("900")


def test_sem_valor_publicado_a_exclusao_de_inativos_depende_do_rpps() -> None:
    """Art. 19, §1º, IV: sem regime próprio não há fundo vinculado que a justifique."""
    componentes = {
        pessoal.BRUTA: Decimal("1000"),
        pessoal.EXCL_INDENIZACAO: Decimal("40"),
        pessoal.EXCL_INATIVOS_RPPS: Decimal("60"),
    }
    com_rpps = pessoal.apurar(componentes, rpps=True)
    sem_rpps = pessoal.apurar(componentes, rpps=False)

    assert com_rpps.exclusoes == Decimal("100")
    assert com_rpps.despesa_liquida == Decimal("900")
    assert sem_rpps.exclusoes == Decimal("40")
    assert sem_rpps.despesa_liquida == Decimal("960")
    # A parcela continua **visível** nos dois casos: excluída ou não, ela foi reportada.
    assert sem_rpps.exclusao_rpps_disponivel == Decimal("60")
    assert sem_rpps.exclusao_rpps_aplicada == Decimal("0")


def test_com_dtp_publicada_a_condicionalidade_de_rpps_nao_altera_o_numero() -> None:
    """Caracterização, não defeito — e a distinção importa para não "corrigir" o certo.

    Quando o ente publica a DTP, ela já vem com as exclusões que **ele** aplicou. Refazer
    a conta pelo lado dos componentes trocaria o número oficial por um recálculo nosso; o
    preço é que a invariante "exclusões dependem do RPPS" deixa de ser verificável por
    este caminho — ela passa a ser responsabilidade do próprio demonstrativo.
    """
    componentes = {
        pessoal.BRUTA: Decimal("1000"),
        pessoal.DTP: Decimal("900"),
        pessoal.EXCL_INATIVOS_RPPS: Decimal("60"),
    }
    assert pessoal.apurar(componentes, rpps=True).despesa_liquida == Decimal("900")
    assert pessoal.apurar(componentes, rpps=False).despesa_liquida == Decimal("900")


def test_coluna_de_percentual_nunca_entra_na_apuracao() -> None:
    """O Anexo 01 publica ``% sobre a RCL Ajustada`` ao lado do valor.

    Somá-la ao valor produziria um numerador com "R$ + pontos percentuais" dentro. É a
    mesma classe do defeito B2-b (contraprova que não filtrava a coluna).
    """
    assert pessoal.classificar_valor_coluna("% sobre a RCL Ajustada") is False
    assert pessoal.classificar_valor_coluna("PERCENTUAL SOBRE A RCL") is False
    assert pessoal.classificar_valor_coluna("SALDO DO EXERCÍCIO ANTERIOR") is False
    assert pessoal.classificar_valor_coluna("Valor") is True
    assert pessoal.classificar_valor_coluna("TOTAL (ÚLTIMOS 12 MESES)") is True


def test_as_contas_do_layout_oficial_do_anexo_01_sao_reconhecidas() -> None:
    """Os rótulos exatos que o SICONFI publica, com a numeração romana do MDF."""
    assert pessoal.classificar_conta("DESPESA BRUTA COM PESSOAL (I)") == pessoal.BRUTA
    assert (
        pessoal.classificar_conta("DESPESA TOTAL COM PESSOAL - DTP (VI) = (IIIa + IIIb)")
        == pessoal.DTP
    )
    assert (
        pessoal.classificar_conta("DESPESA LÍQUIDA COM PESSOAL (III) = (I - II)")
        == pessoal.LIQUIDA_REPORTADA
    )
    assert (
        pessoal.classificar_conta("DESPESAS NÃO COMPUTADAS (§ 1º do art. 19 da LRF) (II)")
        == pessoal.EXCL_TOTAL_REPORTADO
    )
    assert (
        pessoal.classificar_conta("Inativos e Pensionistas com Recursos Vinculados")
        == pessoal.EXCL_INATIVOS_RPPS
    )
    assert pessoal.classificar_conta("LIMITE MÁXIMO (VIII) (incisos I, II e III, art. 20)") is None


# --------------------------------------------------------------------------- #
# P3 — rastreabilidade: inventário de ``source_ref`` (catraca)
# --------------------------------------------------------------------------- #
#: Módulos cujo contrato HTTP declara ``source_ref`` hoje (auditoria A0R, 2026-08-05).
#: Perder um deles é regressão da regra §6.3; ganhar um novo é melhoria e não quebra.
_SCHEMAS_COM_SOURCE_REF: tuple[str, ...] = (
    "accounting/schemas.py",
    "alerts/schemas.py",
    "assistant/schemas.py",
    "benchmark/schemas.py",
    "cash_rap/schemas.py",
    "catalog/schemas.py",
    "dashboard/carteira_schemas.py",
    "dashboard/cockpit_schemas.py",
    "dashboard/estadual_schemas.py",
    "dashboard/schemas.py",
    "debt/schemas.py",
    "expense/schemas.py",
    "forecast/schemas.py",
    "health_edu/schemas.py",
    "indicators/schemas.py",
    "ingestion/schemas.py",
    "limits/schemas.py",
    "personnel/schemas.py",
    "reports/schemas.py",
    "result/schemas.py",
    "revenue/schemas.py",
    "tenancy/schemas.py",
)


def test_nenhum_contrato_perde_o_source_ref_que_ja_tinha() -> None:
    sem_source_ref = [
        rel for rel in _SCHEMAS_COM_SOURCE_REF
        if "source_ref" not in (_MODULOS / rel).read_text(encoding="utf-8")
    ]
    assert not sem_source_ref, (
        "estes contratos declaravam source_ref na auditoria A0R e deixaram de declarar: "
        f"{sem_source_ref} — §6.3 é requisito de produto, não opcional"
    )


# --------------------------------------------------------------------------- #
# P4 — escopo multi-tenant: gate por ente (catraca)
# --------------------------------------------------------------------------- #
_SIMBOLOS_DE_ESCOPO = (
    "assert_ente_in_scope",
    "require_ente_scope",
    "carteira_scope_ibges",
    "assert_uf_in_scope",
)

#: Rota que recebe ente na URL ou na query — é o gatilho da regra §6.4.
_GATILHO_DE_ENTE = re.compile(r"\{cod_ibge\}|\bentes?\s*:\s*(?:str|list\[str\])[^\n]*Query")

#: Módulos sem gate por ente no roteador nem no serviço, com o motivo de cada um:
#: * ``platform`` — control plane, exclusivo do operador da plataforma; não há carteira
#:   a respeitar, e é a única sessão que legitimamente enxerga todos os entes.
#:
#: ``ingestion`` esteve aqui como o **achado A22**, não como exceção legítima. A Sprint E1
#: fechou o gate em ``GET /ingestao/data`` (roteador **e** serviço) e o módulo saiu da
#: lista — a catraca é unilateral, então a redução do conjunto é exatamente o que ela
#: aceita.
_SEM_GATE_CONHECIDOS = {"platform"}


def _texto(caminho: Path) -> str:
    return caminho.read_text(encoding="utf-8") if caminho.exists() else ""


def test_rota_de_ente_sem_gate_de_escopo_nao_pode_crescer() -> None:
    sem_gate: set[str] = set()
    roteadores = sorted(p for p in _MODULOS.glob("*/*router*.py"))
    assert roteadores, "nenhum roteador encontrado — o varredor está olhando o lugar errado"

    for roteador in roteadores:
        texto_router = _texto(roteador)
        if not _GATILHO_DE_ENTE.search(texto_router):
            continue
        modulo = roteador.parent.name
        texto_service = _texto(roteador.parent / "service.py")
        protegido = any(
            simbolo in texto_router or simbolo in texto_service
            for simbolo in _SIMBOLOS_DE_ESCOPO
        )
        if not protegido:
            sem_gate.add(modulo)

    novos = sem_gate - _SEM_GATE_CONHECIDOS
    assert not novos, (
        f"módulos com rota por ente e sem gate de escopo (§6.4): {sorted(novos)} — "
        "toda rota ligada a um ente valida carteira + licença, ou o 403 vira decoração"
    )


# --------------------------------------------------------------------------- #
# P4 — regra fiscal duplicada: bimestre RREO → quadrimestre RGF (catraca)
# --------------------------------------------------------------------------- #
#: A conversão estava reimplementada nestes seis lugares, em **duas** semânticas: bimestre
#: ímpar virava ``None`` em três deles e quadrimestre por teto nos outros três. Nenhum
#: conhecia o RGF **semestral** do município com menos de 50 mil habitantes (LRF art. 63).
#: A Sprint E1 consolidou tudo em ``app/shared/periodo.py::em_periodo_rgf``, com as duas
#: semânticas **nomeadas** (``CICLO_FECHADO`` × ``CICLO_CORRENTE``) e cada chamador
#: declarando a sua; o conjunto encontrado hoje é vazio, e a catraca aceita a redução.
_CONVERSAO_B_PARA_Q: frozenset[str] = frozenset(
    {
        "benchmark/service.py",
        "cash_rap/service.py",
        "dashboard/estadual_service.py",
        "quality/service.py",
        "reports/service.py",
        "result/service.py",
    }
)

#: Constrói um código ``AAAA-Qn`` dividindo/arredondando um bimestre — a assinatura da
#: conversão, e não de qualquer f-string que mencione quadrimestre.
_ASSINATURA_CONVERSAO = re.compile(r"-Q\{[^}]*(?://\s*2|/\s*2|ceil)")


def test_a_conversao_bimestre_quadrimestre_nao_ganha_uma_setima_copia() -> None:
    encontrados = {
        str(caminho.relative_to(_MODULOS)).replace("\\", "/")
        for caminho in _MODULOS.glob("*/*.py")
        if _ASSINATURA_CONVERSAO.search(_texto(caminho))
    }
    novos = encontrados - _CONVERSAO_B_PARA_Q
    assert not novos, (
        f"nova cópia da regra de calendário fiscal em {sorted(novos)} — a fonte única é "
        "app/shared/periodo.py (§6.6); seis cópias já divergem entre si no bimestre ímpar"
    )
