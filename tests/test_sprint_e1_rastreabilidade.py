"""Sprint E1 — A26: os dois contratos que devolviam número fiscal sem ``source_ref``.

O inventário de rastreabilidade da frente P3 (A0R, §5.1.2) fechou em 160 rotas e 22
contratos com ``source_ref``, e achou **duas** lacunas reais: a reconciliação e os checks
de qualidade. As duas devolvem número fiscal — a reconciliação devolve dois, um de cada
lado da comparação — e nenhuma dizia de qual entrega o número saiu. §6.3 é requisito de
produto, não opcional.

Pior que a lacuna de contrato: ``gold.data_quality_check`` não guardava a
``versao_entrega`` conferida, então reexecutar o check depois de uma retificação
**sobrescrevia** o veredito da versão anterior. Ninguém sabia se o "ok" na tela dizia
respeito ao número novo ou ao velho — a mesma família do A14/A15, agora na verificação.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select, text, update

from app.core.db import admin_session
from app.modules.quality import repository as quality_repo
from app.modules.quality import service as quality_service
from app.modules.quality.models import DataQualityCheck
from app.modules.reconciliation import service as reconciliation_service
from app.modules.reconciliation.schemas import DivergenciaItem, ReconciliacaoResultado

PERIODO = "2087-B6"
CHECK = "rcl_calculada_vs_publicada"


@pytest.fixture
def ente_de_teste() -> Iterator[str]:
    cod = str(9_500_000 + uuid.uuid4().int % 90_000)
    yield cod
    with admin_session() as s:
        s.execute(delete(DataQualityCheck).where(DataQualityCheck.cod_ibge == cod))


def _gravar_check(cod: str, *, versao: str, status: str) -> None:
    with admin_session() as s:
        quality_repo.upsert_check(
            s,
            {
                "job_id": None,
                "fonte": "siconfi_rreo",
                "cod_ibge": cod,
                "periodo": PERIODO,
                "versao_entrega": versao,
                "check_codigo": CHECK,
                "status": status,
                "esquerda": None,
                "direita": None,
                "diferenca": None,
                "tolerancia": None,
                "detalhe": None,
            },
        )


def _linhas(cod: str) -> int:
    with admin_session() as s:
        return int(
            s.scalar(
                select(func.count())
                .select_from(DataQualityCheck)
                .where(DataQualityCheck.cod_ibge == cod)
            )
            or 0
        )


# --------------------------------------------------------------------------- #
# Checks de qualidade
# --------------------------------------------------------------------------- #
def test_o_check_devolve_source_ref_com_a_versao_conferida(ente_de_teste) -> None:
    """O selo sobre o número passa a dizer **sobre qual entrega** o veredito foi dado."""
    _gravar_check(ente_de_teste, versao="v1", status="falha")
    with admin_session() as s:
        itens = quality_service.selo_do_ente(s, ente_de_teste, PERIODO)
    assert len(itens) == 1, itens
    item = itens[0]
    assert item.versao_entrega == "v1"
    assert item.source_ref is not None
    assert item.source_ref.relatorio == "RREO"
    assert item.source_ref.periodo == PERIODO
    assert item.source_ref.versao_entrega == "v1"


def test_reexecutar_sobre_a_mesma_versao_atualiza_a_mesma_linha(ente_de_teste) -> None:
    """Idempotência preservada: sem retificação, o estado corrente não vira série."""
    _gravar_check(ente_de_teste, versao="v1", status="falha")
    _gravar_check(ente_de_teste, versao="v1", status="ok")
    assert _linhas(ente_de_teste) == 1
    with admin_session() as s:
        assert quality_service.selo_do_ente(s, ente_de_teste, PERIODO) == []


def test_retificacao_cria_linha_nova_e_nao_apaga_o_veredito_anterior(
    ente_de_teste,
) -> None:
    """O ganho da A26: o histórico de vereditos acompanha o histórico do dado.

    Antes, a segunda execução sobrescrevia a primeira e o rastro sumia — não dava para
    responder "esse check falhou antes ou depois da retificação?".
    """
    _gravar_check(ente_de_teste, versao="v1", status="falha")
    _gravar_check(ente_de_teste, versao="v2", status="ok")
    assert _linhas(ente_de_teste) == 2, "a retificação sobrescreveu o veredito anterior"

    with admin_session() as s:
        versoes = set(
            s.scalars(
                select(DataQualityCheck.versao_entrega).where(
                    DataQualityCheck.cod_ibge == ente_de_teste
                )
            )
        )
        assert versoes == {"v1", "v2"}
        # O selo mostra **estado**, não série: só o veredito da entrega vigente conta.
        assert quality_service.selo_do_ente(s, ente_de_teste, PERIODO) == []


def test_empate_de_carimbo_nao_elege_veredito_por_sorteio(ente_de_teste) -> None:
    """Regressão da migration 0044 — o desempate é ordem de escrita, não ``uuid4()``.

    A E1 elegia o veredito vigente por ``executado_em`` e desempatava por ``id``, apostando
    que o relógio da aplicação sempre distinguiria duas execuções. A aposta é falsa onde o
    relógio é grosso: no Windows, 200 chamadas consecutivas de ``datetime.now(UTC)`` foram
    medidas com **o mesmo valor**, e aí quem vencia era um UUID — moeda. No acervo real
    havia 31 de 193 linhas empatadas por chave (nenhuma com veredito divergente, então o
    sorteio nunca chegou a mudar o que o gestor via — mas elegia por sorte).

    Aqui o empate é **forçado**, não esperado do acaso: os dois vereditos recebem o mesmo
    carimbo, e a retificação (``v2``, que passou a estar ok) tem de vencer sempre.
    """
    _gravar_check(ente_de_teste, versao="v1", status="falha")
    _gravar_check(ente_de_teste, versao="v2", status="ok")

    with admin_session() as s:
        # Empata o carimbo das duas linhas: o que sobra para decidir é só o desempate.
        s.execute(
            update(DataQualityCheck)
            .where(DataQualityCheck.cod_ibge == ente_de_teste)
            .values(executado_em=datetime(2087, 12, 31, 12, 0, tzinfo=UTC))
        )
        s.flush()
        carimbos = set(
            s.scalars(
                select(DataQualityCheck.executado_em).where(
                    DataQualityCheck.cod_ibge == ente_de_teste
                )
            )
        )
        assert len(carimbos) == 1, "o teste precisa do empate para valer alguma coisa"
        assert quality_service.selo_do_ente(s, ente_de_teste, PERIODO) == [], (
            "com carimbos empatados, venceu o veredito da entrega superada — o desempate "
            "voltou a ser sorteio"
        )


def test_a_falha_da_entrega_vigente_continua_selando_a_pagina(ente_de_teste) -> None:
    """O outro sentido: se a retificação **piorou**, é o veredito novo que vale."""
    _gravar_check(ente_de_teste, versao="v1", status="ok")
    _gravar_check(ente_de_teste, versao="v2", status="falha")
    with admin_session() as s:
        itens = quality_service.selo_do_ente(s, ente_de_teste, PERIODO)
    assert [i.versao_entrega for i in itens] == ["v2"]
    assert itens[0].status == "falha"


def test_selo_as_of_elege_o_ultimo_veredito_ate_o_corte(ente_de_teste) -> None:
    """Uma correção posterior não reescreve o selo que existia na fotografia antiga."""
    _gravar_check(ente_de_teste, versao="v1", status="falha")
    _gravar_check(ente_de_teste, versao="v2", status="ok")
    instante_v1 = datetime(2087, 1, 10, 10, 0, tzinfo=UTC)
    corte = datetime(2087, 2, 1, 0, 0, tzinfo=UTC)
    instante_v2 = datetime(2087, 3, 10, 10, 0, tzinfo=UTC)
    with admin_session() as s:
        s.execute(
            update(DataQualityCheck)
            .where(
                DataQualityCheck.cod_ibge == ente_de_teste,
                DataQualityCheck.versao_entrega == "v1",
            )
            .values(executado_em=instante_v1)
        )
        s.execute(
            update(DataQualityCheck)
            .where(
                DataQualityCheck.cod_ibge == ente_de_teste,
                DataQualityCheck.versao_entrega == "v2",
            )
            .values(executado_em=instante_v2)
        )

    with admin_session() as s:
        no_corte = quality_service.selo_do_ente(
            s, ente_de_teste, PERIODO, as_of=corte
        )
        corrente = quality_service.selo_do_ente(s, ente_de_teste, PERIODO)

    assert [(item.versao_entrega, item.status) for item in no_corte] == [("v1", "falha")]
    assert corrente == [], "o veredito v2 corrigiu a falha apenas depois da fotografia"


def test_check_sem_entrega_nao_inventa_source_ref(ente_de_teste) -> None:
    """Atualidade mede a **ausência** da entrega; carimbar uma fonte ali seria mentira."""
    _gravar_check(ente_de_teste, versao=quality_service.SEM_VERSAO, status="aviso")
    with admin_session() as s:
        itens = quality_service.selo_do_ente(s, ente_de_teste, PERIODO)
    assert len(itens) == 1
    assert itens[0].versao_entrega is None, "a sentinela de chave vazou para o contrato"
    assert itens[0].source_ref is None


def test_o_painel_conta_uma_vez_o_check_com_duas_versoes(ente_de_teste) -> None:
    """Contar as duas versões inflaria o total e o resumo por status do painel."""
    _gravar_check(ente_de_teste, versao="v1", status="falha")
    _gravar_check(ente_de_teste, versao="v2", status="ok")
    with admin_session() as s:
        linhas, total = quality_repo.listar_checks(
            s, cod_ibge=ente_de_teste, check_codigo=CHECK, limite=50
        )
    assert total == 1, f"o painel listou {total} linhas para um único check vigente"
    assert [linha.versao_entrega for linha in linhas] == ["v2"]


# --------------------------------------------------------------------------- #
# Reconciliação
# --------------------------------------------------------------------------- #
def test_o_contrato_da_reconciliacao_declara_a_procedencia_dos_dois_lados() -> None:
    """Contrato antes do dado: os campos existem mesmo num ambiente sem acervo."""
    campos_item = set(DivergenciaItem.model_fields)
    assert {"source_ref_plataforma", "source_ref_oficial"} <= campos_item
    assert "source_ref" in ReconciliacaoResultado.model_fields


def test_reconciliacao_com_escopo_vazio_ja_declara_a_fonte_oficial() -> None:
    with admin_session() as s:
        resultado = reconciliation_service.build_reconciliacao(
            s, codigo="rcl_rgf", entes=set()
        )
    assert resultado.source_ref is not None
    assert resultado.source_ref.relatorio == "RGF"
    assert resultado.source_ref.anexo == "Anexo 02"


def test_cada_divergencia_carrega_a_versao_dos_dois_lados_comparados() -> None:
    """A26, o caso que motiva tudo: divergência sem versão não é auditável.

    Sem saber de qual entrega saiu cada lado, o analista não distingue divergência real de
    comparação entre versões diferentes — que é exatamente a família do A15 (o RGF
    republica os quadrimestres anteriores, e a correção chega por aí).
    """
    with admin_session() as s:
        # O escopo real do acervo de desenvolvimento: os municípios do Ceará, o mesmo
        # recorte que ``test_reconciliacao.py`` já usa.
        entes = {
            str(r[0])
            for r in s.execute(
                text("select cod_ibge from gold.dim_ente where uf='CE' and esfera='municipal'")
            )
        }
        resultado = reconciliation_service.build_reconciliacao(
            s, codigo="rcl_rgf", entes=entes, limite=20
        )

    if not resultado.divergencias:
        pytest.skip("sem divergências no acervo deste ambiente para inspecionar")

    for item in resultado.divergencias:
        assert item.source_ref_plataforma is not None
        assert item.source_ref_plataforma.relatorio == "RREO"
        assert item.source_ref_plataforma.versao_entrega, item
        assert item.source_ref_oficial is not None
        assert item.source_ref_oficial.relatorio == "RGF"
        assert item.source_ref_oficial.versao_entrega, item
        # A republicação (A15) faz o lado oficial vir de uma entrega **posterior** ao
        # período comparado; declarar o período de origem é o que torna isso visível.
        assert item.source_ref_oficial.periodo
