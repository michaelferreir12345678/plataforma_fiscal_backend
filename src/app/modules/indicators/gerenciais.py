"""Indicadores **gerenciais** do mart (Sprint 25D).

A auditoria (§2.12) registrou o benchmarking preso a dois indicadores de conformidade
— pessoal do Executivo e dívida consolidada. Só com eles a comparação com os pares
responde "quem está mais perto de estourar um limite", nunca "quem investe mais",
"quem arrecada mais por habitante" ou "quem fecha o exercício no azul".

Estes três não têm limite legal, e é justamente por isso que precisam de um caminho
próprio (``registrar_indicador_gerencial``): entram no mart **sem faixa e sem teto**,
para que o semáforo e o motor de alertas — que só falam quando há faixa — continuem
mudos, e o benchmarking passe a ter o que comparar.

Nada é recalculado aqui: cada valor vem de um fato já materializado pela sprint dona do
assunto (RCL da 2, despesa da 6, resultado da 9). Este módulo só divide, rotula a base e
grava a linha do mart com a linhagem do RREO que a originou.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.expense.models import FatoDespesa
from app.modules.indicators import repository
from app.modules.indicators import service as indicators_service
from app.modules.indicators.models import FatoRcl
from app.modules.result.models import FatoResultado
from app.shared.source_ref import SourceRef

CEM = Decimal(100)

# Investimentos no eixo de natureza do RREO Anexo 02 (o mesmo rótulo que o SICONFI
# publica; a Sprint 6 já o materializa em gold.fato_despesa).
_NATUREZA_INVESTIMENTOS = "Investimentos"

INDICADORES_GERENCIAIS: tuple[str, ...] = (
    "rcl_per_capita",
    "investimento_rcl",
    "resultado_primario_rcl",
)


@dataclass(frozen=True)
class _Insumos:
    versao_rreo: str
    rcl: Decimal | None
    investimento: Decimal | None
    resultado_primario: Decimal | None
    populacao: int | None
    pop_ano_ref: int | None


def _populacao(session: Session, cod_ibge: str, ano: int) -> tuple[int | None, int | None]:
    """População do exercício do ponto; sem estimativa daquele ano, cai para a conformada."""
    por_ano = repository.populacao_por_ano(session, cod_ibge=cod_ibge)
    if ano in por_ano:
        return por_ano[ano], ano
    conformada = repository.populacao_dim_ente(session, cod_ibge=cod_ibge)
    if conformada is None:
        return None, None
    return conformada[0], conformada[1]


def _coletar(
    session: Session, cod_ibge: str, periodo: str, versao_rreo: str
) -> _Insumos:
    rcl_row = session.scalar(
        select(FatoRcl.rcl_12m).where(
            FatoRcl.cod_ibge == cod_ibge,
            FatoRcl.periodo_ref == periodo,
            FatoRcl.versao_entrega == versao_rreo,
        )
    )
    investimento = session.scalar(
        select(FatoDespesa.empenhado).where(
            FatoDespesa.cod_ibge == cod_ibge,
            FatoDespesa.periodo == periodo,
            FatoDespesa.natureza_codigo == _NATUREZA_INVESTIMENTOS,
            FatoDespesa.versao_entrega == versao_rreo,
        )
    )
    resultado = session.scalar(
        select(FatoResultado.resultado_primario).where(
            FatoResultado.cod_ibge == cod_ibge,
            FatoResultado.periodo == periodo,
            FatoResultado.versao_entrega == versao_rreo,
        )
    )
    ano = int(periodo[:4])
    populacao, pop_ano = _populacao(session, cod_ibge, ano)
    return _Insumos(
        versao_rreo=versao_rreo,
        rcl=Decimal(rcl_row) if rcl_row is not None else None,
        investimento=Decimal(investimento) if investimento is not None else None,
        resultado_primario=Decimal(resultado) if resultado is not None else None,
        populacao=populacao,
        pop_ano_ref=pop_ano,
    )


def _source(periodo: str, versao: str, anexo: str) -> SourceRef:
    return SourceRef(relatorio="RREO", anexo=anexo, periodo=periodo, versao_entrega=versao)


def materializar_gerenciais(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> list[str]:
    """Grava os indicadores gerenciais do ente/período. Retorna os que foram materializados.

    Insumo ausente ⇒ o indicador **não é gravado**. Uma linha de mart com valor zero
    seria lida como "não investe" em vez de "não publicou".
    """
    versao = repository.resolve_versao_rreo(
        session, cod_ibge=cod_ibge, periodo=periodo, as_of=as_of
    )
    if versao is None:
        return []
    dados = _coletar(session, cod_ibge, periodo, versao)
    gravados: list[str] = []

    if dados.rcl is not None and dados.rcl > 0 and dados.populacao:
        indicators_service.registrar_indicador_gerencial(
            session, cod_ibge, periodo, "rcl_per_capita",
            valor_rs=dados.rcl / Decimal(dados.populacao),
            valor_pct=None,
            denominador="populacao",
            base_valor=Decimal(dados.populacao),
            versao_entrega=versao,
            as_of=as_of,
            source_ref=_source(periodo, versao, "Anexo 03 — RCL"),
        )
        gravados.append("rcl_per_capita")

    if dados.rcl is not None and dados.rcl > 0 and dados.investimento is not None:
        indicators_service.registrar_indicador_gerencial(
            session, cod_ibge, periodo, "investimento_rcl",
            valor_rs=dados.investimento,
            valor_pct=dados.investimento / dados.rcl * CEM,
            denominador="rcl",
            base_valor=dados.rcl,
            versao_entrega=versao,
            as_of=as_of,
            # Numerador e denominador saem da **mesma entrega** RREO: não há versão
            # composta a registrar (isso é caso do pessoal, que cruza RGF com RREO).
            source_ref=_source(periodo, versao, "Anexo 02 — natureza × Anexo 03 — RCL"),
        )
        gravados.append("investimento_rcl")

    if dados.rcl is not None and dados.rcl > 0 and dados.resultado_primario is not None:
        indicators_service.registrar_indicador_gerencial(
            session, cod_ibge, periodo, "resultado_primario_rcl",
            valor_rs=dados.resultado_primario,
            valor_pct=dados.resultado_primario / dados.rcl * CEM,
            denominador="rcl",
            base_valor=dados.rcl,
            versao_entrega=versao,
            as_of=as_of,
            source_ref=_source(periodo, versao, "Anexo 06 — primário × Anexo 03 — RCL"),
        )
        gravados.append("resultado_primario_rcl")

    return gravados
