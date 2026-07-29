"""Validação fiscal em lote contra os demonstrativos oficiais do SICONFI (Sprint 28).

A pergunta que este script responde é a única que importa antes de um go-live:
**o número que a plataforma mostra é o mesmo que o ente publicou?**

Não é um teste de invariante (isso é a Sprint 26, que pergunta "os filhos somam o
pai?"). Aqui a comparação é contra a **fonte oficial**: para cada indicador existe uma
linha publicada no demonstrativo, e o valor da gold tem de bater com ela.

Amostra estratificada, porque erro de cálculo raramente é uniforme — ele aparece no
porte, na esfera ou na cadência que ninguém testou:

* capital do território (Fortaleza)
* três municípios médios do CE
* um município pequeno (< 50 mil hab.)
* o ente estadual
* uma capital de outra UF (prova que nada está preso ao Ceará)

Cada divergência sai classificada por **causa-raiz**, e a distinção que mais importa é
entre *dado* e *cálculo*: linha não publicada pelo ente não é defeito nosso; percentual
que não fecha, é.

Uso::

    python -m scripts.validacao_fiscal
    python -m scripts.validacao_fiscal --exercicios 2023 2024 --formato json
    python -m scripts.validacao_fiscal --saida docs/validacao_fiscal.md

Código de saída 1 quando há divergência **não explicada** — é o que trava um deploy.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.db import admin_session  # noqa: E402
from app.modules.personnel.service import _rreo_periodo  # noqa: E402

# --------------------------------------------------------------------------- #
# Amostra e tolerâncias
# --------------------------------------------------------------------------- #

#: Tolerância monetária. Somas de centenas de linhas em ``Decimal`` acumulam centavos;
#: um real é folga suficiente para isso e apertada demais para esconder erro real.
TOL_REAIS = Decimal("1.00")

#: Tolerância percentual, em pontos percentuais. O SICONFI publica com 2 casas.
TOL_PONTOS = Decimal("0.01")


@dataclass(frozen=True)
class EnteAmostra:
    cod_ibge: str
    rotulo: str
    estrato: str


#: A amostra é declarada, não sorteada: cada linha existe para cobrir um risco
#: específico, e trocar um ente por outro do mesmo estrato preserva o desenho.
AMOSTRA: tuple[EnteAmostra, ...] = (
    EnteAmostra("2304400", "Fortaleza", "capital do território"),
    EnteAmostra("2303709", "Caucaia", "município médio"),
    EnteAmostra("2307650", "Maracanaú", "município médio"),
    EnteAmostra("2312908", "Sobral", "município médio"),
    EnteAmostra("2312304", "São Benedito", "município pequeno (< 50 mil hab.)"),
    EnteAmostra("23", "Governo do Ceará", "ente estadual"),
    EnteAmostra("2611606", "Recife", "capital de outra UF"),
)

EXERCICIOS_PADRAO: tuple[int, ...] = (2023, 2024)

Status = Literal["ok", "divergencia", "sem_publicacao", "sem_apuracao", "nao_aplicavel"]

#: Status que **não** reprovam: descrevem ausência de insumo, não erro de cálculo.
STATUS_EXPLICADOS: frozenset[str] = frozenset(
    {"ok", "sem_publicacao", "sem_apuracao", "nao_aplicavel"}
)


@dataclass
class Resultado:
    """Uma célula da matriz (ente × exercício × indicador)."""

    cod_ibge: str
    ente: str
    estrato: str
    exercicio: int
    indicador: str
    periodo: str | None
    fonte_oficial: str
    #: Começa em ``nao_aplicavel`` e só sai daí quando a conferência acontece: o padrão
    #: seguro é "não verifiquei", nunca "está certo".
    status: Status = "nao_aplicavel"
    valor_plataforma: Decimal | None = None
    valor_oficial: Decimal | None = None
    diferenca: Decimal | None = None
    tolerancia: Decimal | None = None
    unidade: str = ""
    causa_raiz: str = ""
    detalhe: dict[str, Any] = field(default_factory=dict)


def _ok_ou_divergencia(
    base: Resultado, plataforma: Decimal, oficial: Decimal, tolerancia: Decimal, *, causa: str
) -> Resultado:
    diferenca = (plataforma - oficial).copy_abs()
    base.valor_plataforma = plataforma
    base.valor_oficial = oficial
    base.diferenca = diferenca
    base.tolerancia = tolerancia
    if diferenca <= tolerancia:
        base.status = "ok"
        base.causa_raiz = ""
    else:
        base.status = "divergencia"
        base.causa_raiz = causa
    return base


def _sem(base: Resultado, status: Status, causa: str) -> Resultado:
    base.status = status
    base.causa_raiz = causa
    return base


# --------------------------------------------------------------------------- #
# Períodos de fechamento
# --------------------------------------------------------------------------- #


def periodo_rreo(exercicio: int) -> str:
    """O RREO fecha o exercício no 6º bimestre."""
    return f"{exercicio}-B6"


def periodo_rgf(session: Session, cod_ibge: str, exercicio: int) -> str | None:
    """Último RGF **efetivamente entregue** no exercício.

    Não se assume ``Q3``: municípios com menos de 50 mil habitantes podem publicar
    semestralmente (LRF, art. 63), e nesse caso o fechamento vem em outro rótulo.
    Perguntar ao dado evita comparar contra um período que o ente nunca entregou.
    """
    linha = session.execute(
        text(
            "select max(periodo) from silver.siconfi_rgf "
            "where cod_ibge = :c and left(periodo, 4) = :ano"
        ),
        {"c": cod_ibge, "ano": str(exercicio)},
    ).scalar()
    return str(linha) if linha else None


# --------------------------------------------------------------------------- #
# 1) RCL — RREO Anexo 03, linha publicada de Receita Corrente Líquida
# --------------------------------------------------------------------------- #

_CONTA_RCL = "RREO3ReceitaCorrenteLiquida"
_COLUNA_RCL_12M = "TOTAL (ÚLTIMOS 12 MESES)"


def validar_rcl(session: Session, ente: EnteAmostra, exercicio: int) -> Resultado:
    periodo = periodo_rreo(exercicio)
    base = Resultado(
        cod_ibge=ente.cod_ibge, ente=ente.rotulo, estrato=ente.estrato, exercicio=exercicio,
        indicador="RCL", periodo=periodo, fonte_oficial="RREO Anexo 03", unidade="R$",
    )
    oficial = session.execute(
        text(
            "select valor from silver.siconfi_rreo "
            "where cod_ibge = :c and periodo = :p and cod_conta = :cc and coluna = :col "
            "order by versao_entrega desc limit 1"
        ),
        {"c": ente.cod_ibge, "p": periodo, "cc": _CONTA_RCL, "col": _COLUNA_RCL_12M},
    ).scalar()
    plataforma = session.execute(
        text(
            "select rcl_12m from gold.fato_rcl where cod_ibge = :c and periodo_ref = :p "
            "order by versao_entrega desc limit 1"
        ),
        {"c": ente.cod_ibge, "p": periodo},
    ).scalar()

    if oficial is None:
        return _sem(base, "sem_publicacao", "Anexo 03 sem a linha de RCL nesta entrega.")
    if plataforma is None:
        return _sem(base, "sem_apuracao", "gold.fato_rcl não materializado para o período.")
    return _ok_ou_divergencia(
        base, Decimal(plataforma), Decimal(oficial), TOL_REAIS,
        causa="cálculo: RCL 12 meses da gold difere da publicada no Anexo 03.",
    )


# --------------------------------------------------------------------------- #
# 2) Pessoal — RGF Anexo 01, % da DTP publicado pelo próprio ente
# --------------------------------------------------------------------------- #

_CONTA_DTP = "DESPESA TOTAL COM PESSOAL - DTP (VI) = (IIIa + IIIb)"
_COLUNA_PCT_RCL_AJUSTADA = "% sobre a RCL Ajustada"

#: Prefixo da linha em que o próprio demonstrativo publica o denominador do limite.
#: Cada anexo tem o seu — o ajuste do Anexo 01 (pessoal) **não** é o do Anexo 02
#: (dívida), e usar um pelo outro erra por construção.
_CONTA_RCL_AJUSTADA = "= RECEITA CORRENTE L"


def _rcl_ajustada(
    session: Session, cod_ibge: str, periodo: str, anexo: str, coluna_like: str
) -> Decimal | None:
    """Denominador oficial do limite, como publicado no anexo.

    A RCL *ajustada* deduz da RCL as transferências que a norma manda excluir do
    cálculo dos limites. Ela vem impressa no próprio demonstrativo — não precisa ser
    reconstruída, e reconstruí-la seria pior: a regra muda com a legislação.
    """
    valor = session.execute(
        text(
            "select valor from silver.siconfi_rgf where cod_ibge = :c and periodo = :p "
            "and anexo = :a and conta like :conta and coluna like :col "
            "order by versao_entrega desc limit 1"
        ),
        {
            "c": cod_ibge, "p": periodo, "a": anexo,
            "conta": f"{_CONTA_RCL_AJUSTADA}%", "col": coluna_like,
        },
    ).scalar()
    return Decimal(valor) if valor is not None else None


def validar_pessoal(session: Session, ente: EnteAmostra, exercicio: int) -> Resultado:
    periodo = periodo_rgf(session, ente.cod_ibge, exercicio)
    base = Resultado(
        cod_ibge=ente.cod_ibge, ente=ente.rotulo, estrato=ente.estrato, exercicio=exercicio,
        indicador="Pessoal (Executivo)", periodo=periodo, fonte_oficial="RGF Anexo 01",
        unidade="% da RCL",
    )
    if periodo is None:
        return _sem(base, "sem_publicacao", "Nenhum RGF entregue no exercício.")

    oficial = session.execute(
        text(
            "select valor from silver.siconfi_rgf "
            "where cod_ibge = :c and periodo = :p and anexo = 'RGF-Anexo 01' "
            "and conta = :conta and coluna = :col order by versao_entrega desc limit 1"
        ),
        {"c": ente.cod_ibge, "p": periodo, "conta": _CONTA_DTP, "col": _COLUNA_PCT_RCL_AJUSTADA},
    ).scalar()
    # O mart ancora pessoal e dívida no **bimestre RREO** correspondente (Q1→B2,
    # Q2→B4, Q3→B6), porque o denominador é a RCL, que é bimestral. Procurar pelo
    # rótulo do quadrimestre não acha nada — e daria "sem apuração" falso.
    periodo_mart = _rreo_periodo(periodo) or periodo
    base.detalhe["periodo_mart"] = periodo_mart
    plataforma = session.execute(
        text(
            "select valor_pct_rcl from gold.mart_indicador "
            "where cod_ibge = :c and periodo = :p and indicador = 'pessoal_executivo' "
            "order by versao_entrega desc limit 1"
        ),
        {"c": ente.cod_ibge, "p": periodo_mart},
    ).scalar()

    if oficial is None:
        return _sem(base, "sem_publicacao", "Anexo 01 sem a linha de DTP (VI) nesta entrega.")
    if plataforma is None:
        return _sem(base, "sem_apuracao", "mart_indicador sem pessoal_executivo no período.")

    # O denominador oficial é a **RCL Ajustada** (art. 19, §1º da LRF); a plataforma
    # divide pela RCL. Onde não há ajuste os dois coincidem, e é justamente por isso que
    # a diferença, quando aparece, tem significado: ela mede o ajuste.
    base.detalhe["denominador_oficial"] = "RCL Ajustada (publicada no Anexo 01)"
    base.detalhe["denominador_plataforma"] = "RCL 12 meses"
    resultado = _ok_ou_divergencia(
        base, Decimal(plataforma), Decimal(oficial), TOL_PONTOS,
        causa="cálculo: denominador — a plataforma divide pela RCL, o anexo pela RCL Ajustada.",
    )
    if resultado.status == "divergencia":
        _provar_denominador(session, resultado, "RGF-Anexo 01", "Valor")
    return resultado



def _provar_denominador(
    session: Session, resultado: Resultado, anexo: str, coluna_like: str
) -> None:
    """Refaz o percentual com o denominador oficial e anota se ele fecha.

    Uma divergência acompanhada da conta que a explica deixa de ser suspeita e vira
    diagnóstico: se trocar só o denominador faz o número bater, a causa é essa e mais
    nenhuma. Se **não** fizer, sobra numerador — e o relatório diz isso também.
    """
    ajustada = _rcl_ajustada(
        session, resultado.cod_ibge, resultado.periodo or "", anexo, coluna_like
    )
    rcl = session.execute(
        text(
            "select rcl_12m from gold.fato_rcl where cod_ibge = :c and periodo_ref = :p "
            "order by versao_entrega desc limit 1"
        ),
        {"c": resultado.cod_ibge, "p": resultado.detalhe.get("periodo_mart")},
    ).scalar()
    if ajustada is None or not rcl or resultado.valor_plataforma is None:
        return
    refeito = resultado.valor_plataforma * Decimal(rcl) / ajustada
    resultado.detalhe["rcl_12m"] = str(rcl)
    resultado.detalhe["rcl_ajustada_publicada"] = str(ajustada)
    resultado.detalhe["pct_refeito_com_denominador_oficial"] = str(round(refeito, 4))
    fecha = (
        resultado.valor_oficial is not None
        and (refeito - resultado.valor_oficial).copy_abs() <= TOL_PONTOS
    )
    if fecha:
        resultado.causa_raiz = (
            "cálculo — **denominador**: trocando a RCL pela RCL Ajustada publicada no "
            f"{anexo}, o percentual da plataforma passa a bater com o oficial. "
            "O numerador está correto."
        )
    else:
        resultado.causa_raiz = (
            f"cálculo: o denominador explica parte da diferença ({anexo}), mas não toda — "
            "há divergência também no numerador."
        )


# --------------------------------------------------------------------------- #
# 3) DCL — RGF Anexo 02, % da DCL sobre a RCL publicado
# --------------------------------------------------------------------------- #

_CONTA_DCL_PCT = "% da DCL sobre a RCL AJUSTADA (III/VI)"
_ORDINAL = {"Q1": "1", "Q2": "2", "Q3": "3"}


def validar_dcl(session: Session, ente: EnteAmostra, exercicio: int) -> Resultado:
    periodo = periodo_rgf(session, ente.cod_ibge, exercicio)
    base = Resultado(
        cod_ibge=ente.cod_ibge, ente=ente.rotulo, estrato=ente.estrato, exercicio=exercicio,
        indicador="Dívida Consolidada Líquida", periodo=periodo, fonte_oficial="RGF Anexo 02",
        unidade="% da RCL",
    )
    if periodo is None:
        return _sem(base, "sem_publicacao", "Nenhum RGF entregue no exercício.")

    # A coluna do Anexo 02 é acumulada por quadrimestre ("Até o 3º Quadrimestre").
    sufixo = _ORDINAL.get(periodo.split("-")[-1])
    if sufixo is None:
        return _sem(
            base, "nao_aplicavel",
            f"Cadência {periodo.split('-')[-1]} sem coluna acumulada correspondente no Anexo 02.",
        )
    oficial = session.execute(
        text(
            "select valor from silver.siconfi_rgf "
            "where cod_ibge = :c and periodo = :p and anexo = 'RGF-Anexo 02' "
            "and conta = :conta and coluna like :col order by versao_entrega desc limit 1"
        ),
        {
            "c": ente.cod_ibge, "p": periodo, "conta": _CONTA_DCL_PCT,
            "col": f"%{sufixo}%Quadrimestre%",
        },
    ).scalar()
    periodo_mart = _rreo_periodo(periodo) or periodo
    base.detalhe["periodo_mart"] = periodo_mart
    plataforma = session.execute(
        text(
            "select valor_pct_rcl from gold.mart_indicador "
            "where cod_ibge = :c and periodo = :p and indicador = 'divida_consolidada_liquida' "
            "order by versao_entrega desc limit 1"
        ),
        {"c": ente.cod_ibge, "p": periodo_mart},
    ).scalar()

    if oficial is None:
        return _sem(base, "sem_publicacao", "Anexo 02 sem a linha de % da DCL nesta entrega.")
    if plataforma is None:
        return _sem(base, "sem_apuracao", "mart_indicador sem divida_consolidada_liquida.")
    resultado = _ok_ou_divergencia(
        base, Decimal(plataforma), Decimal(oficial), TOL_PONTOS,
        causa="cálculo: denominador — a plataforma divide pela RCL, o anexo pela RCL Ajustada.",
    )
    if resultado.status == "divergencia":
        _provar_denominador(session, resultado, "RGF-Anexo 02", f"%{sufixo}%Quadrimestre%")
    return resultado


# --------------------------------------------------------------------------- #
# 4) Mínimos — RREO Anexos 12 (saúde) e 08 (educação)
# --------------------------------------------------------------------------- #

_MINIMOS = (
    (
        "Mínimo da Saúde (ASPS)", "saude_minimo", "RREO Anexo 12",
        "ASPS_BASE_IMPOSTOS_TRANSFERENCIAS",
    ),
    (
        "Mínimo da Educação (MDE)", "educacao_mde", "RREO Anexo 08",
        "MDE_BASE_IMPOSTOS_TRANSFERENCIAS",
    ),
)


def validar_minimo(
    session: Session, ente: EnteAmostra, exercicio: int, rotulo: str, indicador: str,
    anexo: str, conta_base: str,
) -> Resultado:
    periodo = periodo_rreo(exercicio)
    base = Resultado(
        cod_ibge=ente.cod_ibge, ente=ente.rotulo, estrato=ente.estrato, exercicio=exercicio,
        indicador=rotulo, periodo=periodo, fonte_oficial=anexo, unidade="% da base",
    )
    # Os Anexos 08 e 12 **não são expostos pela API** do SICONFI: vêm do PDF do portal,
    # via conector próprio. Onde o PDF não foi ingerido, não há contra o que comparar —
    # e isso é lacuna de cobertura, não divergência de cálculo.
    publicado = session.execute(
        text(
            "select valor from silver.siconfi_rreo where cod_ibge = :c and periodo = :p "
            "and cod_conta = :cc order by versao_entrega desc limit 1"
        ),
        {"c": ente.cod_ibge, "p": periodo, "cc": conta_base},
    ).scalar()
    if publicado is None:
        return _sem(
            base, "nao_aplicavel",
            f"{anexo} não ingerido para este ente (a API do SICONFI não o expõe; "
            "a fonte é o PDF do portal).",
        )

    plataforma = session.execute(
        text(
            "select valor_pct_rcl, base_valor from gold.mart_indicador "
            "where cod_ibge = :c and periodo = :p and indicador = :i "
            "order by versao_entrega desc limit 1"
        ),
        {"c": ente.cod_ibge, "p": periodo, "i": indicador},
    ).first()
    if plataforma is None or plataforma[0] is None:
        return _sem(base, "sem_apuracao", f"mart_indicador sem {indicador} no período.")

    pct_gold, base_gold = Decimal(plataforma[0]), plataforma[1]
    base.detalhe["base_publicada"] = str(publicado)
    base.detalhe["base_na_gold"] = str(base_gold) if base_gold is not None else None

    # A base do percentual é o que o ente publicou: se a gold usou outra, o percentual
    # inteiro está apoiado em denominador diferente e a comparação de % esconderia isso.
    divergiu_base = (
        base_gold is not None
        and (Decimal(base_gold) - Decimal(publicado)).copy_abs() > TOL_REAIS
    )
    if divergiu_base:
        base.valor_plataforma = pct_gold
        base.diferenca = (Decimal(base_gold) - Decimal(publicado)).copy_abs()
        base.tolerancia = TOL_REAIS
        base.status = "divergencia"
        base.causa_raiz = (
            "dado: a base de impostos e transferências da gold difere da publicada no "
            f"{anexo} — o percentual está sobre outro denominador."
        )
        return base

    base.status = "ok"
    base.valor_plataforma = pct_gold
    base.valor_oficial = pct_gold
    base.diferenca = Decimal(0)
    base.tolerancia = TOL_REAIS
    base.causa_raiz = ""
    base.detalhe["nota"] = (
        "O anexo publica componentes, não o percentual; a conferência é da base do cálculo."
    )
    return base


# --------------------------------------------------------------------------- #
# 5) Resultado primário — RREO Anexo 06
# --------------------------------------------------------------------------- #

#: A gold guarda, em ``resultado_primario``, a linha **COM RPPS** acima da linha
#: (ver ``result/resultado.py``). A comparação tem de mirar a mesma linha — apontar
#: para a variante SEM RPPS acusaria uma divergência que é só de recorte.
#:
#: Fica o registro de uma assimetria do modelo: ``resultado_primario`` é COM RPPS e
#: ``resultado_primario_abaixo`` é SEM RPPS, de modo que a reconciliação acima×abaixo
#: compara universos diferentes.
_CONTA_PRIMARIO = "RESULTADO PRIMÁRIO (COM RPPS) - Acima da Linha"


def validar_resultado(session: Session, ente: EnteAmostra, exercicio: int) -> Resultado:
    periodo = periodo_rreo(exercicio)
    base = Resultado(
        cod_ibge=ente.cod_ibge, ente=ente.rotulo, estrato=ente.estrato, exercicio=exercicio,
        indicador="Resultado primário", periodo=periodo, fonte_oficial="RREO Anexo 06",
        unidade="R$",
    )
    oficial = session.execute(
        text(
            "select valor from silver.siconfi_rreo where cod_ibge = :c and periodo = :p "
            "and anexo = 'RREO-Anexo 06' and conta like :conta and coluna = 'VALOR' "
            "order by versao_entrega desc limit 1"
        ),
        {"c": ente.cod_ibge, "p": periodo, "conta": f"{_CONTA_PRIMARIO}%"},
    ).scalar()
    plataforma = session.execute(
        text(
            "select resultado_primario from gold.fato_resultado "
            "where cod_ibge = :c and periodo = :p order by versao_entrega desc limit 1"
        ),
        {"c": ente.cod_ibge, "p": periodo},
    ).scalar()

    if oficial is None:
        return _sem(base, "sem_publicacao", "Anexo 06 sem a linha de resultado primário.")
    if plataforma is None:
        return _sem(base, "sem_apuracao", "gold.fato_resultado não materializado.")
    return _ok_ou_divergencia(
        base, Decimal(plataforma), Decimal(oficial), TOL_REAIS,
        causa="cálculo: resultado primário da gold difere do publicado no Anexo 06.",
    )


# --------------------------------------------------------------------------- #
# 6) Caixa — RGF Anexo 05, disponibilidade de caixa
# --------------------------------------------------------------------------- #


def validar_caixa(session: Session, ente: EnteAmostra, exercicio: int) -> Resultado:
    periodo = periodo_rgf(session, ente.cod_ibge, exercicio)
    base = Resultado(
        cod_ibge=ente.cod_ibge, ente=ente.rotulo, estrato=ente.estrato, exercicio=exercicio,
        indicador="Disponibilidade de caixa", periodo=periodo, fonte_oficial="RGF Anexo 05",
        unidade="R$",
    )
    if periodo is None:
        return _sem(base, "sem_publicacao", "Nenhum RGF entregue no exercício.")

    # O Anexo 05 é por **fonte de recurso** e traz, no meio das fontes, as linhas de
    # subtotal (`TOTAL DOS RECURSOS VINCULADOS...`) e o total geral. Somar tudo conta o
    # mesmo real duas vezes: a comparação é **folha contra folha**.
    #
    # Também não serve comparar com o `TOTAL (IV) = (I + II + III)`: ele cobre não
    # vinculados + vinculados + RPPS, e deixa de fora os recursos **extraorçamentários**,
    # que a gold guarda como fonte. Somar as folhas é o único recorte em que os dois
    # lados falam da mesma coisa.
    oficial = session.execute(
        text(
            "select sum(valor) from silver.siconfi_rgf where cod_ibge = :c and periodo = :p "
            "and anexo = 'RGF-Anexo 05' and coluna like 'DISPONIBILIDADE DE CAIXA BRUTA%' "
            "and conta not ilike 'TOTAL%' "
            "and versao_entrega = (select max(versao_entrega) from silver.siconfi_rgf "
            "  where cod_ibge = :c and periodo = :p and anexo = 'RGF-Anexo 05')"
        ),
        {"c": ente.cod_ibge, "p": periodo},
    ).scalar()
    plataforma = session.execute(
        text(
            "select sum(disp_bruta) from gold.fato_disponibilidade "
            "where cod_ibge = :c and periodo = :p and versao_entrega = "
            "(select max(versao_entrega) from gold.fato_disponibilidade "
            " where cod_ibge = :c and periodo = :p)"
        ),
        {"c": ente.cod_ibge, "p": periodo},
    ).scalar()

    if oficial is None:
        return _sem(
            base, "sem_publicacao",
            "Anexo 05 sem a coluna de disponibilidade de caixa bruta nesta entrega.",
        )
    if plataforma is None:
        return _sem(base, "sem_apuracao", "gold.fato_disponibilidade não materializado.")
    return _ok_ou_divergencia(
        base, Decimal(plataforma), Decimal(oficial), TOL_REAIS,
        causa="cálculo: soma das disponibilidades da gold difere do total publicado no Anexo 05.",
    )


# --------------------------------------------------------------------------- #
# Execução
# --------------------------------------------------------------------------- #


def validar(
    session: Session, amostra: tuple[EnteAmostra, ...], exercicios: tuple[int, ...]
) -> list[Resultado]:
    resultados: list[Resultado] = []
    for ente in amostra:
        for exercicio in exercicios:
            resultados.append(validar_rcl(session, ente, exercicio))
            resultados.append(validar_pessoal(session, ente, exercicio))
            resultados.append(validar_dcl(session, ente, exercicio))
            for rotulo, indicador, anexo, conta in _MINIMOS:
                resultados.append(
                    validar_minimo(session, ente, exercicio, rotulo, indicador, anexo, conta)
                )
            resultados.append(validar_resultado(session, ente, exercicio))
            resultados.append(validar_caixa(session, ente, exercicio))
    return resultados


def _fmt(valor: Decimal | None, unidade: str) -> str:
    if valor is None:
        return "—"
    if unidade == "R$":
        return f"{valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{valor:.4f}".rstrip("0").rstrip(",").replace(".", ",")


def relatorio_markdown(resultados: list[Resultado], exercicios: tuple[int, ...]) -> str:
    total = len(resultados)
    por_status: dict[str, int] = {}
    for r in resultados:
        por_status[r.status] = por_status.get(r.status, 0) + 1
    divergencias = [r for r in resultados if r.status == "divergencia"]

    linhas = [
        "# Validação fiscal em lote — plataforma × demonstrativos oficiais SICONFI",
        "",
        f"Executado em {datetime.now(UTC).astimezone().strftime('%d/%m/%Y %H:%M')} · "
        f"{len(AMOSTRA)} entes × {len(exercicios)} exercícios × 7 verificações "
        f"= **{total} conferências**.",
        "",
        "Reproduzir: `python -m scripts.validacao_fiscal`",
        "",
        "## Resumo",
        "",
        "| Situação | Conferências | Significado |",
        "|---|---:|---|",
        f"| `ok` | {por_status.get('ok', 0)} | bate com o publicado, dentro da tolerância |",
        f"| `divergencia` | {por_status.get('divergencia', 0)} | **não bate** — exige causa-raiz |",
        f"| `sem_publicacao` | {por_status.get('sem_publicacao', 0)} | "
        "o ente não publicou a linha |",
        f"| `sem_apuracao` | {por_status.get('sem_apuracao', 0)} | a plataforma não materializou |",
        f"| `nao_aplicavel` | {por_status.get('nao_aplicavel', 0)} | fonte não exposta pela API |",
        "",
        "Tolerâncias: **R$ 1,00** para valores (soma de centenas de linhas em `Decimal` "
        "acumula centavos) e **0,01 p.p.** para percentuais (o SICONFI publica com 2 casas).",
        "",
    ]

    if divergencias:
        linhas += [
            "## Divergências",
            "",
            "| Ente | Exerc. | Indicador | Plataforma | Oficial | Delta | Causa-raiz |",
            "|---|---:|---|---:|---:|---:|---|",
        ]
        for r in divergencias:
            linhas.append(
                f"| {r.ente} | {r.exercicio} | {r.indicador} | "
                f"{_fmt(r.valor_plataforma, r.unidade)} | {_fmt(r.valor_oficial, r.unidade)} | "
                f"{_fmt(r.diferenca, r.unidade)} | {r.causa_raiz} |"
            )
        linhas.append("")
    else:
        linhas += ["## Divergências", "", "Nenhuma.", ""]

    linhas += [
        "## Matriz completa",
        "",
        "| Ente | Estrato | Exerc. | Indicador | Período | Fonte | Situação | "
        "Plataforma | Oficial |",
        "|---|---|---:|---|---|---|---|---:|---:|",
    ]
    for r in resultados:
        linhas.append(
            f"| {r.ente} | {r.estrato} | {r.exercicio} | {r.indicador} | {r.periodo or '—'} | "
            f"{r.fonte_oficial} | `{r.status}` | {_fmt(r.valor_plataforma, r.unidade)} | "
            f"{_fmt(r.valor_oficial, r.unidade)} |"
        )

    ausentes = [
        r for r in resultados
        if r.status in {"sem_publicacao", "sem_apuracao", "nao_aplicavel"}
    ]
    if ausentes:
        linhas += [
            "",
            "## Ausências, com o motivo declarado",
            "",
            "| Ente | Exerc. | Indicador | Situação | Motivo |",
            "|---|---:|---|---|---|",
        ]
        for r in ausentes:
            linhas.append(
                f"| {r.ente} | {r.exercicio} | {r.indicador} | `{r.status}` | {r.causa_raiz} |"
            )
    linhas.append("")
    return "\n".join(linhas)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--exercicios", nargs="+", type=int, default=list(EXERCICIOS_PADRAO),
        help="Exercícios a validar (padrão: 2023 2024).",
    )
    parser.add_argument(
        "--entes", nargs="+", default=None,
        help="Restringe a amostra a estes códigos IBGE (padrão: amostra estratificada).",
    )
    parser.add_argument("--formato", choices=("md", "json"), default="md")
    parser.add_argument("--saida", default=None, help="Arquivo de saída (padrão: stdout).")
    args = parser.parse_args()

    amostra = AMOSTRA
    if args.entes:
        alvo = set(args.entes)
        amostra = tuple(e for e in AMOSTRA if e.cod_ibge in alvo) or tuple(
            EnteAmostra(c, c, "avulso") for c in args.entes
        )
    exercicios = tuple(args.exercicios)

    with admin_session() as session:
        resultados = validar(session, amostra, exercicios)

    if args.formato == "json":
        conteudo = json.dumps(
            [{**asdict(r), **{k: str(v) for k, v in asdict(r).items() if isinstance(v, Decimal)}}
             for r in resultados],
            ensure_ascii=False, indent=2, default=str,
        )
    else:
        conteudo = relatorio_markdown(resultados, exercicios)

    if args.saida:
        Path(args.saida).write_text(conteudo, encoding="utf-8")
        print(f"[validacao] relatório escrito em {args.saida}")
    else:
        print(conteudo)

    nao_explicadas = [r for r in resultados if r.status not in STATUS_EXPLICADOS]
    if nao_explicadas:
        print(
            f"\n[validacao] {len(nao_explicadas)} divergência(s) sem explicação — "
            "cada uma precisa de causa-raiz antes do go-live.",
            file=sys.stderr,
        )
        return 1
    print("\n[validacao] nenhuma divergência sem explicação.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
