"""Checks de qualidade do dado fiscal (Sprint 26).

As invariantes do domínio existiam só dentro da suíte de testes: se um dado chegasse
corrompido em produção, nada perguntaria "os filhos somam o pai?" ou "a RCL que calculei
bate com a que o ente publicou?". Aqui elas viram **verificação executável sobre o dado
real**, com os dois lados guardados para que o resultado seja auditável — e discutível.

Três decisões que valem por todo o módulo:

- **Ausência não é falha.** Ente que não publicou o Anexo 12 não "falhou no check": o
  check não se aplica (``aviso`` com motivo, ou nem é emitido). Confundir os dois
  transformaria lacuna de ingestão em acusação de erro contábil.
- **Tolerância é do domínio, não do float.** Somas de centenas de linhas em Decimal
  acumulam centavos; a tolerância é declarada por check e viaja no resultado.
- **Aviso é o degrau entre ok e falha**: divergência que cabe em arredondamento amplo
  (até 10× a tolerância) merece olhar humano, não alarme.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.accounting import service as accounting_service
from app.modules.debt.models import FatoDivida
from app.modules.expense.models import FatoDespesa
from app.modules.health_edu.models import FatoEducacao, FatoSaude
from app.modules.indicators import endividamento
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import DimEntrega, SilverRreo
from app.modules.personnel.models import FatoPessoal
from app.modules.result.models import FatoResultado
from app.modules.revenue.models import DimOrigemReceita, FatoReceita

Status = Literal["ok", "aviso", "falha"]

# Um real de folga absorve o arredondamento de somas com centenas de parcelas.
TOL_REAIS = Decimal("1.00")
# Um centésimo de ponto percentual: abaixo disso, dois cálculos do mesmo número.
TOL_PONTOS = Decimal("0.01")
# Divergência até este múltiplo da tolerância vira aviso; acima, falha.
FATOR_AVISO = Decimal(10)


@dataclass(frozen=True)
class ResultadoCheck:
    """O veredito de uma verificação, com a conta à vista."""

    check_codigo: str
    fonte: str
    status: Status
    cod_ibge: str | None = None
    periodo: str | None = None
    #: Entrega sobre a qual o check foi conferido (A26/E1). Sem ela, depois de uma
    #: retificação ninguém sabe se o "ok" guardado se refere ao número novo ou ao velho —
    #: e o resultado era sobrescrito sem rastro. ``None`` = check que não se ancora numa
    #: entrega (freshness, execução agendada).
    versao_entrega: str | None = None
    esquerda: Decimal | None = None
    direita: Decimal | None = None
    diferenca: Decimal | None = None
    tolerancia: Decimal | None = None
    detalhe: dict[str, Any] = field(default_factory=dict)

    @property
    def critico(self) -> bool:
        return self.status == "falha"


def _classificar(
    diferenca: Decimal, tolerancia: Decimal
) -> Status:
    if abs(diferenca) <= tolerancia:
        return "ok"
    if abs(diferenca) <= tolerancia * FATOR_AVISO:
        return "aviso"
    return "falha"


def _comparacao(
    *,
    codigo: str,
    fonte: str,
    cod_ibge: str,
    periodo: str,
    esquerda: Decimal,
    direita: Decimal,
    tolerancia: Decimal,
    detalhe: dict[str, Any],
) -> ResultadoCheck:
    diferenca = esquerda - direita
    return ResultadoCheck(
        check_codigo=codigo,
        fonte=fonte,
        status=_classificar(diferenca, tolerancia),
        cod_ibge=cod_ibge,
        periodo=periodo,
        esquerda=esquerda,
        direita=direita,
        diferenca=diferenca,
        tolerancia=tolerancia,
        detalhe=detalhe,
    )


def _nao_aplicavel(
    codigo: str, fonte: str, cod_ibge: str, periodo: str, motivo: str
) -> ResultadoCheck:
    """Sem insumo para verificar. É lacuna de cobertura, não erro do ente."""
    return ResultadoCheck(
        check_codigo=codigo,
        fonte=fonte,
        status="aviso",
        cod_ibge=cod_ibge,
        periodo=periodo,
        detalhe={"nao_aplicavel": True, "motivo": motivo},
    )


# --------------------------------------------------------------------------- #
# 1) Soma dos filhos = pai (receita)
# --------------------------------------------------------------------------- #
# Linhas de **memória** do RREO Anexo 01: aparecem sob um bloco de receita mas não o
# compõem. "Saldo de exercícios anteriores utilizado para créditos adicionais" é
# superávit financeiro de exercício passado — fonte de financiamento, não receita
# arrecadada no período. Somá-la ao pai daria receita a mais.
# Evidência que motivou a exclusão: entre os 179 entes com Anexo 01 em 2024-B6, todos os
# pais fecham exatamente, **exceto** 'ReceitasDeCapital' nos 51 entes que reportam essa
# linha — e neles a diferença é exatamente o valor dela.
LINHAS_MEMORIA_RECEITA: frozenset[str] = frozenset(
    {"SaldoDeExerciciosAnterioresUtilizadosParaCreditosAdicionais"}
)


def receita_soma_filhos(
    session: Session, cod_ibge: str, periodo: str, versao: str
) -> ResultadoCheck:
    """Cada origem-pai tem de ser a soma exata dos seus filhos diretos.

    Percorre **todos** os pais presentes no fato (não só a raiz) e reporta a pior
    divergência; as demais viajam no detalhe, para que uma falha seja diagnosticável sem
    abrir o banco.
    """
    codigo = "receita_soma_filhos"
    linhas = list(
        session.execute(
            select(
                FatoReceita.origem_codigo,
                FatoReceita.arrecadado_acum,
                DimOrigemReceita.parent_codigo,
            )
            .join(
                DimOrigemReceita,
                DimOrigemReceita.codigo == FatoReceita.origem_codigo,
                isouter=True,
            )
            .where(
                FatoReceita.cod_ibge == cod_ibge,
                FatoReceita.periodo == periodo,
                FatoReceita.versao_entrega == versao,
            )
        )
    )
    if not linhas:
        return _nao_aplicavel(codigo, "siconfi_rreo", cod_ibge, periodo, "sem gold.fato_receita")

    valores: dict[str, Decimal] = {}
    filhos_de: dict[str, list[str]] = {}
    for origem, valor, parent in linhas:
        if valor is None:
            continue
        codigo_origem = str(origem)
        valores[codigo_origem] = Decimal(valor)
        if parent:
            filhos_de.setdefault(str(parent), []).append(codigo_origem)

    divergencias: list[dict[str, Any]] = []
    pior: tuple[str, Decimal, Decimal] | None = None
    for pai, filhos in filhos_de.items():
        if pai not in valores:
            continue
        considerados = [f for f in filhos if f not in LINHAS_MEMORIA_RECEITA]
        if not considerados:
            continue
        soma = sum((valores[f] for f in considerados), Decimal(0))
        diferenca = valores[pai] - soma
        if abs(diferenca) > TOL_REAIS:
            divergencias.append(
                {"pai": pai, "pai_valor": str(valores[pai]), "soma_filhos": str(soma),
                 "diferenca": str(diferenca), "n_filhos": len(considerados)}
            )
        if pior is None or abs(diferenca) > abs(pior[1] - pior[2]):
            pior = (pai, valores[pai], soma)

    if pior is None:
        return _nao_aplicavel(
            codigo, "siconfi_rreo", cod_ibge, periodo,
            "nenhuma origem-pai com filhos materializados neste período",
        )
    return _comparacao(
        codigo=codigo,
        fonte="siconfi_rreo",
        cod_ibge=cod_ibge,
        periodo=periodo,
        esquerda=pior[1],
        direita=pior[2],
        tolerancia=TOL_REAIS,
        detalhe={
            "pai_avaliado": pior[0],
            "pais_verificados": len(filhos_de),
            "divergencias": divergencias,
            "linhas_de_memoria_excluidas": sorted(LINHAS_MEMORIA_RECEITA),
            "regra": "pai = Σ filhos diretos (exceto linhas de memória do Anexo 01)",
        },
    )


# --------------------------------------------------------------------------- #
# 2) Estágios da despesa: empenhado ≥ liquidado ≥ pago
# --------------------------------------------------------------------------- #
def despesa_estagios(
    session: Session, cod_ibge: str, periodo: str, versao: str
) -> ResultadoCheck:
    """Ordem legal da execução. Estágio **não publicado** é ignorado, não reprovado.

    O RREO Anexo 02 (eixo função) não publica "pago" para todos os entes — tratar zero
    como violação transformaria uma escolha do demonstrativo em erro do ente.
    """
    codigo = "despesa_estagios_monotonicos"
    linha = session.execute(
        select(
            func.sum(FatoDespesa.empenhado),
            func.sum(FatoDespesa.liquidado),
            func.sum(FatoDespesa.pago),
        ).where(
            FatoDespesa.cod_ibge == cod_ibge,
            FatoDespesa.periodo == periodo,
            FatoDespesa.versao_entrega == versao,
            FatoDespesa.natureza_codigo == "*",  # eixo função, evita dupla contagem
        )
    ).first()
    if linha is None or linha[0] is None:
        return _nao_aplicavel(codigo, "siconfi_rreo", cod_ibge, periodo, "sem gold.fato_despesa")
    empenhado, liquidado, pago = (Decimal(v or 0) for v in linha)
    violacoes: list[str] = []
    if liquidado > 0 and liquidado - empenhado > TOL_REAIS:
        violacoes.append("liquidado > empenhado")
    if pago > 0 and pago - liquidado > TOL_REAIS:
        violacoes.append("pago > liquidado")
    ausentes = [
        nome for nome, valor in (("liquidado", liquidado), ("pago", pago)) if valor == 0
    ]
    pior = max(
        (liquidado - empenhado if liquidado > 0 else Decimal(0)),
        (pago - liquidado if pago > 0 else Decimal(0)),
    )
    return ResultadoCheck(
        check_codigo=codigo,
        fonte="siconfi_rreo",
        status="falha" if violacoes else "ok",
        cod_ibge=cod_ibge,
        periodo=periodo,
        esquerda=empenhado,
        direita=liquidado,
        diferenca=pior,
        tolerancia=TOL_REAIS,
        detalhe={
            "empenhado": str(empenhado),
            "liquidado": str(liquidado),
            "pago": str(pago),
            "violacoes": violacoes,
            "estagios_nao_publicados": ausentes,
            "regra": "empenhado ≥ liquidado ≥ pago (estágio ausente não é violação)",
        },
    )


# --------------------------------------------------------------------------- #
# 3) RCL calculada × RCL publicada (Anexo 03)
# --------------------------------------------------------------------------- #
_CONTA_RCL_PUBLICADA = "RREO3ReceitaCorrenteLiquida"
_COLUNA_12M = "TOTAL (ÚLTIMOS 12 MESES)"


def rcl_calculada_vs_publicada(
    session: Session, cod_ibge: str, periodo: str, versao: str
) -> ResultadoCheck:
    """O número que o produto calcula tem de ser o que o ente publicou no A3."""
    codigo = "rcl_calculada_vs_publicada"
    calculada = session.scalar(
        select(FatoRcl.rcl_12m).where(
            FatoRcl.cod_ibge == cod_ibge,
            FatoRcl.periodo_ref == periodo,
            FatoRcl.versao_entrega == versao,
        )
    )
    publicada = session.scalar(
        select(SilverRreo.valor).where(
            SilverRreo.cod_ibge == cod_ibge,
            SilverRreo.periodo == periodo,
            SilverRreo.versao_entrega == versao,
            SilverRreo.cod_conta == _CONTA_RCL_PUBLICADA,
            SilverRreo.coluna == _COLUNA_12M,
        )
    )
    if calculada is None:
        return _nao_aplicavel(codigo, "siconfi_rreo", cod_ibge, periodo, "sem gold.fato_rcl")
    if publicada is None:
        return _nao_aplicavel(
            codigo, "siconfi_rreo", cod_ibge, periodo,
            "Anexo 03 sem a linha de RCL publicada nesta entrega",
        )
    return _comparacao(
        codigo=codigo,
        fonte="siconfi_rreo",
        cod_ibge=cod_ibge,
        periodo=periodo,
        esquerda=Decimal(calculada),
        direita=Decimal(publicada),
        tolerancia=TOL_REAIS,
        detalhe={
            "conta_publicada": _CONTA_RCL_PUBLICADA,
            "coluna": _COLUNA_12M,
            "regra": "RCL 12m recalculada = RCL publicada no RREO Anexo 03",
        },
    )


# --------------------------------------------------------------------------- #
# 4) DCL: RREO Anexo 6 × RGF Anexo 2
# --------------------------------------------------------------------------- #
def dcl_a6_vs_rgf(
    session: Session, cod_ibge: str, periodo_rreo: str, periodo_rgf: str
) -> ResultadoCheck:
    """A dívida do fim do período no A6 tem de bater com a apurada no RGF."""
    codigo = "dcl_a6_vs_rgf"
    a6 = session.scalar(
        select(FatoResultado.dcl_fim).where(
            FatoResultado.cod_ibge == cod_ibge, FatoResultado.periodo == periodo_rreo
        )
    )
    rgf = session.scalar(
        select(FatoDivida.dcl).where(
            FatoDivida.cod_ibge == cod_ibge, FatoDivida.periodo == periodo_rgf
        )
    )
    if a6 is None or rgf is None:
        faltando = "RREO Anexo 6" if a6 is None else "RGF Anexo 2"
        return _nao_aplicavel(codigo, "siconfi_rgf", cod_ibge, periodo_rreo, f"sem {faltando}")
    return _comparacao(
        codigo=codigo,
        fonte="siconfi_rgf",
        cod_ibge=cod_ibge,
        periodo=periodo_rreo,
        esquerda=Decimal(a6),
        direita=Decimal(rgf),
        tolerancia=TOL_REAIS,
        detalhe={
            "periodo_rgf": periodo_rgf,
            "regra": "DCL do fim do período (RREO A6) = DCL apurada (RGF A2)",
        },
    )


# --------------------------------------------------------------------------- #
# 5) MSC ↔ DCA (reusa a conciliação da Sprint 12)
# --------------------------------------------------------------------------- #
def msc_vs_dca(session: Session, cod_ibge: str, ano: int) -> ResultadoCheck:
    """Persiste a conciliação patrimonial que antes só existia on-read."""
    codigo = "msc_vs_dca"
    try:
        conc = accounting_service.build_conciliacao(session, cod_ibge, ano)
    except AppError as exc:
        return _nao_aplicavel(
            codigo, "siconfi_dca", cod_ibge, str(ano), exc.detail or "sem DCA/MSC"
        )
    divergentes = [c for c in conc.checks if c.divergente and c.aplicavel]
    pior = max(
        (abs(Decimal(str(c.diferenca))) for c in divergentes if c.diferenca is not None),
        default=Decimal(0),
    )
    return ResultadoCheck(
        check_codigo=codigo,
        fonte="siconfi_dca",
        status="falha" if divergentes else "ok",
        cod_ibge=cod_ibge,
        periodo=str(ano),
        diferenca=pior,
        tolerancia=TOL_REAIS,
        detalhe={
            "n_checks": conc.n_checks,
            "n_divergencias": conc.n_divergencias,
            "divergentes": [c.chave for c in divergentes],
            "regra": "identidade do encerramento: MSC rolled-up = DCA (Sprint 12)",
        },
    )


# --------------------------------------------------------------------------- #
# 6) Mínimos: recalculado × materializado
# --------------------------------------------------------------------------- #
def minimo_recalculado_vs_materializado(
    session: Session, cod_ibge: str, periodo: str, area: Literal["saude", "educacao"]
) -> ResultadoCheck:
    """O percentual materializado tem de sair da própria base e despesa gravadas.

    Não é redundância: se o fato foi gravado por uma versão anterior da regra (ou por uma
    migração parcial), o percentual e seus componentes deixam de casar — e é exatamente
    esse desalinhamento silencioso que o produto não pode ter.
    """
    codigo = f"minimo_{area}_recalculado"
    fato: FatoSaude | FatoEducacao | None
    if area == "saude":
        fato = session.scalar(
            select(FatoSaude)
            .where(FatoSaude.cod_ibge == cod_ibge, FatoSaude.periodo == periodo)
            .order_by(FatoSaude.versao_rreo.desc())
            .limit(1)
        )
    else:
        fato = session.scalar(
            select(FatoEducacao)
            .where(FatoEducacao.cod_ibge == cod_ibge, FatoEducacao.periodo == periodo)
            .order_by(FatoEducacao.versao_rreo.desc())
            .limit(1)
        )
    if fato is None:
        return _nao_aplicavel(
            codigo, "siconfi_rreo", cod_ibge, periodo, f"sem gold.fato_{area}"
        )
    base = Decimal(fato.base_impostos_transferencias or 0)
    if base <= 0:
        return _nao_aplicavel(
            codigo, "siconfi_rreo", cod_ibge, periodo, "base de impostos não positiva"
        )
    recalculado = Decimal(fato.despesa_aplicada) / base * Decimal(100)
    return _comparacao(
        codigo=codigo,
        fonte="siconfi_rreo",
        cod_ibge=cod_ibge,
        periodo=periodo,
        esquerda=recalculado,
        direita=Decimal(fato.pct_aplicado),
        tolerancia=TOL_PONTOS,
        detalhe={
            "base": str(base),
            "aplicado": str(fato.despesa_aplicada),
            "regra": "pct = aplicado ÷ base × 100 (recalculado sobre o próprio fato)",
        },
    )


# --------------------------------------------------------------------------- #
# 7) Reconciliação mart_indicador × detalhe
# --------------------------------------------------------------------------- #
def mart_vs_detalhe_pessoal(
    session: Session, cod_ibge: str, periodo_rreo: str, periodo_rgf: str
) -> ResultadoCheck:
    """O semáforo (mart) e a página de detalhe têm de contar a mesma história.

    Este é o risco estrutural que a auditoria apontou: o mart alimenta dashboard, carteira
    e alertas; o detalhe alimenta a página. Se divergirem, o gestor vê dois números para o
    mesmo fato — e nada no sistema avisa.

    **O denominador é a RCL Ajustada, não a RCL cheia.** Esta verificação nasceu na Sprint
    26 dividindo pela ``fato_rcl.rcl_12m`` e não acompanhou a Sprint 28, que corrigiu o
    denominador dos limites de pessoal e dívida (migration 0035). O resultado foi o pior
    defeito possível numa régua: ela carregava o vício que existe para achar, e passou a
    acusar de errado exatamente o valor correto — 8 falhas em produção, todas falso
    positivo, com o painel de resolução mandando reprocessar dado que já estava certo.

    Medido no ente 23 em 2026-B2: R$ 16.679.957.857,72 sobre a RCL Ajustada de
    R$ 40.690.096.057,23 dá 40,9927% (o que o mart grava); sobre a RCL cheia de
    R$ 40.899.706.794,11 daria 40,7826% — a divergência de 0,21 p.p. que o check acusava.
    """
    codigo = "mart_vs_detalhe_pessoal"
    mart = session.scalar(
        select(MartIndicador.valor_pct_rcl)
        .where(
            MartIndicador.cod_ibge == cod_ibge,
            MartIndicador.periodo == periodo_rreo,
            MartIndicador.indicador == "pessoal_executivo",
            MartIndicador.valor_pct_rcl.is_not(None),
        )
        .order_by(MartIndicador.versao_entrega)
        .limit(1)
    )
    fato = session.scalar(
        select(FatoPessoal)
        .where(
            FatoPessoal.cod_ibge == cod_ibge,
            FatoPessoal.periodo == periodo_rgf,
            FatoPessoal.poder_codigo == "ENTE.EXEC",
        )
        .limit(1)
    )
    # A RCL Ajustada vem da mesma função que o cálculo do indicador usa — fonte única.
    # Reproduzi-la aqui faria a régua e o medido divergirem na primeira mudança de regra,
    # que é como este defeito nasceu.
    versao_rgf = session.scalar(
        select(DimEntrega.versao_entrega).where(
            DimEntrega.cod_ibge == cod_ibge,
            DimEntrega.relatorio == "RGF",
            DimEntrega.periodo == periodo_rgf,
            DimEntrega.vigente.is_(True),
        )
    )
    rcl_valor = (
        endividamento.rcl_ajustada(
            session, cod_ibge=cod_ibge, periodo=periodo_rgf, versao=versao_rgf
        )
        if versao_rgf
        else None
    )
    liquida = (
        Decimal(str(fato.despesa_liquida))
        if fato is not None and fato.despesa_liquida is not None
        else None
    )
    if mart is None or liquida is None or rcl_valor is None or rcl_valor == 0:
        return _nao_aplicavel(
            codigo, "siconfi_rgf", cod_ibge, periodo_rreo,
            "sem mart, despesa líquida de pessoal ou RCL Ajustada para reconciliar",
        )
    recalculado = liquida / rcl_valor * Decimal(100)
    return _comparacao(
        codigo=codigo,
        fonte="siconfi_rgf",
        cod_ibge=cod_ibge,
        periodo=periodo_rreo,
        esquerda=recalculado,
        direita=Decimal(mart),
        tolerancia=TOL_PONTOS,
        detalhe={
            "periodo_rgf": periodo_rgf,
            "regra": (
                "detalhe (fato_pessoal ÷ RCL Ajustada) = mart_indicador.pessoal_executivo"
            ),
            "denominador": "rcl_ajustada",
            "rcl_ajustada": str(rcl_valor),
        },
    )


# --------------------------------------------------------------------------- #
# 8) Freshness por cadência (SLA)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SlaFonte:
    """Cadência esperada de uma fonte e a folga aceitável depois do prazo legal."""

    relatorio: str
    rotulo: str
    periodos_por_ano: int
    dias_apos_periodo: int  # prazo legal de publicação
    tolerancia_dias: int  # folga antes de virar falha


SLAS: tuple[SlaFonte, ...] = (
    # LRF art. 52: RREO em até 30 dias após o bimestre. Damos mais 30 de folga antes de
    # chamar de falha — atraso de dias é rotina; de meses, é problema.
    SlaFonte("RREO", "RREO (bimestral)", 6, 30, 30),
    SlaFonte("RGF", "RGF (quadrimestral)", 3, 30, 45),
    # DCA: 30 de abril do exercício seguinte (Portaria STN).
    SlaFonte("DCA", "DCA (anual)", 1, 120, 60),
    SlaFonte("MSC", "MSC (mensal)", 12, 30, 30),
)


def freshness(
    session: Session, sla: SlaFonte, *, cod_ibge: str, hoje: date | None = None
) -> ResultadoCheck:
    """Compara a entrega mais recente com o que já deveria ter sido publicado.

    O atraso é medido em **dias além do prazo legal** da entrega esperada, não em
    "períodos de diferença": um ente pontual em 15 de janeiro não está atrasado só porque
    o 6º bimestre ainda não fechou.
    """
    codigo = f"freshness_{sla.relatorio.lower()}"
    referencia = hoje or datetime.now(UTC).date()
    entrega = session.scalar(
        select(DimEntrega)
        .where(
            DimEntrega.cod_ibge == cod_ibge,
            DimEntrega.relatorio == sla.relatorio,
            DimEntrega.vigente.is_(True),
        )
        .order_by(DimEntrega.periodo.desc())
        .limit(1)
    )
    if entrega is None:
        return ResultadoCheck(
            check_codigo=codigo,
            fonte=f"siconfi_{sla.relatorio.lower()}",
            status="aviso",
            cod_ibge=cod_ibge,
            detalhe={
                "nao_aplicavel": True,
                "motivo": f"nenhuma entrega de {sla.rotulo} ingerida para este ente",
            },
        )
    atraso = _dias_de_atraso(entrega.periodo, sla, referencia)
    if atraso <= 0:
        status: Status = "ok"
    elif atraso <= sla.tolerancia_dias:
        status = "aviso"
    else:
        status = "falha"
    return ResultadoCheck(
        check_codigo=codigo,
        fonte=f"siconfi_{sla.relatorio.lower()}",
        status=status,
        cod_ibge=cod_ibge,
        periodo=entrega.periodo,
        esquerda=Decimal(atraso),
        direita=Decimal(sla.tolerancia_dias),
        diferenca=Decimal(atraso),
        tolerancia=Decimal(sla.tolerancia_dias),
        detalhe={
            "ultimo_periodo_entregue": entrega.periodo,
            "homologada_em": entrega.homologada_em.isoformat() if entrega.homologada_em else None,
            "prazo_legal_dias_apos_periodo": sla.dias_apos_periodo,
            "regra": f"{sla.rotulo}: atraso além do prazo legal + {sla.tolerancia_dias} dias",
        },
    )


def _dias_de_atraso(periodo_entregue: str, sla: SlaFonte, referencia: date) -> int:
    """Dias entre hoje e o prazo do **próximo** período que já deveria estar publicado."""
    from app.modules.forecast.periodos import parse_periodo

    try:
        atual = parse_periodo(periodo_entregue)
    except ValueError:
        return 0
    indice = atual.indice + 1
    ano = atual.ano
    if indice > sla.periodos_por_ano:
        indice, ano = 1, ano + 1
    fim_meses = 12 // sla.periodos_por_ano
    mes_fim = min(12, indice * fim_meses)
    # Fim do período seguinte + prazo legal de publicação.
    fim = date(ano, 12, 31) if mes_fim == 12 else date(ano, mes_fim + 1, 1) - _UM_DIA
    prazo = fim + _dias(sla.dias_apos_periodo)
    return max(0, (referencia - prazo).days)


def _dias(n: int) -> timedelta:
    return timedelta(days=n)


_UM_DIA = _dias(1)
