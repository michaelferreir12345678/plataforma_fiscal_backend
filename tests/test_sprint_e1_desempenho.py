"""Sprint E1 — guardas de desempenho mensuráveis no gate de escopo (A27).

O achado, confirmado no código pela frente P4 da A0R: ``shared/scope.py::_estado_prefixes``
percorria a carteira e consultava ``gold.dim_ente`` **ente a ente**, sem cache de sessão,
dentro de ``assert_ente_in_scope`` e de ``carteira_scope_ibges``. Para uma conta estadual
com 184 municípios na carteira, isso era até 184 consultas **por requisição**, em toda
rota fiscal — e o custo cresce com o tamanho do cliente, ou seja, piora exatamente no
cliente que paga mais. O padrão certo já existia dois blocos acima, na
``cobertura_licenca``, que memoriza em ``session.info``.

Este arquivo mede, não estima. O contador é o evento ``before_cursor_execute`` do
SQLAlchemy, e o **limiar está declarado aqui dentro** — não num relatório à parte que
envelhece sem ninguém notar. O registro do baseline (consulta, volume, ambiente, limiar e
o número medido antes/depois) está em ``docs/baseline_desempenho_e1.md``.

Nota de método: o teste é **relativo à carteira**, não ao relógio. Contar consultas é
determinístico e roda igual em qualquer máquina; cronometrar milissegundos numa suíte que
divide o banco com o resto mediria a carga da máquina, não o código.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy import delete, event

from app.core.db import SessionLocal, admin_session, apply_context
from app.core.deps import Principal
from app.modules.catalog.models import ESFERA_ESTADUAL, ESFERA_MUNICIPAL, DimEnte
from app.modules.tenancy import repository as tenancy_repo
from app.modules.tenancy.models import Licenca, Organizacao
from app.shared import scope

#: Carteira do cenário medido. É o tamanho de um estado real de porte médio, e era o
#: número de consultas que o gate emitia antes da correção.
MUNICIPIOS_NA_CARTEIRA = 184

#: Prefixo fora da faixa de código IBGE real (11–53), para não colidir com ente de verdade
#: — a mesma higiene que ``test_forecast`` já adota com o prefixo "8".
PREFIXO = "97"

#: Orçamento de consultas do gate de escopo para **uma** requisição, declarado.
#: Antes da E1: 1 (carteira) + até 184 (``dim_ente`` ente a ente) = até 185.
#: Depois: organização + carteira + ``dim_ente`` em lote + licença — todas memorizadas na
#: sessão. A folga até 5 cobre uma consulta a mais de infraestrutura sem virar teste frágil.
ORCAMENTO_CONSULTAS_GATE = 5


class ContadorDeConsultas:
    """Conta instruções SQL emitidas por uma conexão (``before_cursor_execute``)."""

    def __init__(self) -> None:
        self.instrucoes: list[str] = []

    @property
    def total(self) -> int:
        return len(self.instrucoes)

    def __call__(  # noqa: ANN001, PLR0913
        self, conn, cursor, statement, parameters, context, executemany
    ) -> None:
        del conn, cursor, parameters, context, executemany
        self.instrucoes.append(str(statement))


@pytest.fixture
def carteira_estadual() -> Iterator[tuple[uuid.UUID, list[str]]]:
    """Conta estadual com o ente da UF + 184 municípios na carteira (dado real no banco)."""
    municipios = [f"{PREFIXO}{i:05d}" for i in range(1, MUNICIPIOS_NA_CARTEIRA + 1)]
    with admin_session() as s:
        org = tenancy_repo.create_org(
            s,
            nome=f"Estado E1 {uuid.uuid4().hex[:8]}",
            tipo_conta="estado",
            metrica_cobranca=None,
        )
        org_id = org.id
        s.add(DimEnte(cod_ibge=PREFIXO, nome="UF de teste", uf="ZZ", esfera=ESFERA_ESTADUAL))
        for cod in municipios:
            s.add(
                DimEnte(cod_ibge=cod, nome=f"Município {cod}", uf="ZZ", esfera=ESFERA_MUNICIPAL)
            )
        # A carteira guarda os 184 municípios (7 dígitos), e não só o código da UF: é o
        # formato que produzia o N+1, porque cada código de 7 dígitos disparava um
        # ``session.get(DimEnte, ...)`` próprio dentro do gate.
        for cod in municipios:
            tenancy_repo.add_carteira_ente(s, org_id=org_id, cod_ibge=cod, grupo=None, tag=None)
        tenancy_repo.add_carteira_ente(s, org_id=org_id, cod_ibge=PREFIXO, grupo=None, tag=None)
        tenancy_repo.add_licenca(
            s,
            Licenca(
                org_id=org_id, tipo="uf", uf=PREFIXO,
                vigencia_inicio=date.today(), status="ativa",
            ),
        )

    yield org_id, municipios

    with admin_session() as s:
        s.execute(delete(Organizacao).where(Organizacao.id == org_id))
        s.execute(delete(DimEnte).where(DimEnte.cod_ibge.in_([PREFIXO, *municipios])))


def _principal(org_id: uuid.UUID) -> Principal:
    return Principal(
        usuario_id=uuid.uuid4(),
        org_id=org_id,
        papel="Papel",
        capacidades=frozenset({"ver", "administrar"}),
        escopo_ibges=None,
    )


def _sessao_de_requisicao(org_id: uuid.UUID):
    """Sessão equivalente à de uma requisição HTTP: RLS fixada na organização."""
    session = SessionLocal()
    apply_context(session, org_id=org_id, user_id=None, is_admin=False)
    return session


def test_gate_de_escopo_de_conta_estadual_cabe_no_orcamento_de_consultas(
    carteira_estadual,
) -> None:
    """A27: era 1 + até 184 consultas por requisição; agora é um punhado, com cache.

    O ente pedido é de propósito **um município da UF que não está na carteira**: é o
    caminho da ampliação estadual (Módulo 2, Sprint 4), e é o único em que o gate precisa
    resolver os prefixos de UF — quando o ente está listado na carteira, a primeira
    condição do ``or`` já responde e nada disto roda. Medir o caminho fácil não mediria
    defeito nenhum.

    A sessão é a unidade: uma sessão SQLAlchemy = uma requisição HTTP no runtime da API
    (``get_db``). Repetir o ``assert`` na mesma sessão simula a rota fiscal que confere o
    escopo mais de uma vez — e é aí que o cache tem de aparecer.
    """
    org_id, municipios = carteira_estadual
    fora_da_carteira = f"{PREFIXO}90001"  # mesma UF, ausente da carteira
    assert fora_da_carteira not in municipios
    contador = ContadorDeConsultas()
    session = _sessao_de_requisicao(org_id)
    bind = session.get_bind()
    event.listen(bind, "before_cursor_execute", contador)
    try:
        principal = _principal(org_id)
        scope.assert_ente_in_scope(session, principal, fora_da_carteira)
        consultas_primeira = contador.total
        for sufixo in range(2, 7):
            scope.assert_ente_in_scope(session, principal, f"{PREFIXO}9000{sufixo}")
        consultas_totais = contador.total
    finally:
        event.remove(bind, "before_cursor_execute", contador)
        session.close()

    assert consultas_primeira <= ORCAMENTO_CONSULTAS_GATE, (
        f"o gate emitiu {consultas_primeira} consultas para uma carteira de "
        f"{len(municipios)} municípios (orçamento declarado: {ORCAMENTO_CONSULTAS_GATE}); "
        "antes da E1 eram até 186 — ver docs/baseline_desempenho_e1.md\n"
        + "\n".join(sql[:120] for sql in contador.instrucoes[:12])
    )
    # Depois da primeira, o escopo está memorizado: cada verificação seguinte custa a
    # consulta de carteira do próprio ente, não a varredura de ``dim_ente`` de novo.
    seguintes = consultas_totais - consultas_primeira
    assert seguintes <= 5 + 1, (
        f"{seguintes} consultas nas 5 verificações seguintes — o cache de sessão não "
        "está sendo aproveitado"
    )


def test_escopo_agregado_nao_consulta_dim_ente_ente_a_ente(carteira_estadual) -> None:
    """``carteira_scope_ibges`` compartilha o mesmo gate — e compartilhava o defeito."""
    org_id, municipios = carteira_estadual
    contador = ContadorDeConsultas()
    session = _sessao_de_requisicao(org_id)
    bind = session.get_bind()
    event.listen(bind, "before_cursor_execute", contador)
    try:
        cods = scope.carteira_scope_ibges(session, _principal(org_id))
    finally:
        event.remove(bind, "before_cursor_execute", contador)
        session.close()

    assert set(municipios).issubset(cods), "a expansão estadual deixou de funcionar"
    assert contador.total <= ORCAMENTO_CONSULTAS_GATE + 1, (
        f"{contador.total} consultas para resolver o escopo agregado de "
        f"{len(municipios)} municípios (orçamento: {ORCAMENTO_CONSULTAS_GATE + 1})"
    )


def test_o_cache_de_escopo_morre_quando_a_carteira_muda_na_mesma_sessao(
    carteira_estadual,
) -> None:
    """Cache que sobrevive à mudança é pior que cache nenhum.

    Risco listado na própria ficha da E1: a carteira **pode** mudar dentro da mesma
    requisição (cadastro em lote seguido de leitura do escopo). A invalidação vive no
    mesmo ponto em que a licença já invalidava.

    A asserção é sobre ``_estado_prefixes`` porque é ele que a carteira determina: uma UF
    nova monitorada só aparece se o valor memorizado for descartado.
    """
    org_id, _municipios = carteira_estadual
    outra_uf = "96"
    with admin_session() as s:
        s.add(DimEnte(cod_ibge=outra_uf, nome="Outra UF", uf="YY", esfera=ESFERA_ESTADUAL))
    try:
        session = _sessao_de_requisicao(org_id)
        try:
            antes = scope._estado_prefixes(session, org_id)
            tenancy_repo.add_carteira_ente(
                session, org_id=org_id, cod_ibge=outra_uf, grupo=None, tag=None
            )
            ainda_memorizado = scope._estado_prefixes(session, org_id)
            scope.invalidar_escopo_carteira(session, org_id)
            depois = scope._estado_prefixes(session, org_id)
            session.rollback()
        finally:
            session.close()

        assert antes == {PREFIXO}
        # Sem invalidar, o valor da requisição continua valendo — é o cache funcionando,
        # e é exatamente por isso que a invalidação precisa existir no ponto da mutação.
        assert ainda_memorizado == {PREFIXO}
        assert depois == {PREFIXO, outra_uf}, "o escopo memorizado ignorou a carteira nova"
    finally:
        with admin_session() as s:
            s.execute(delete(DimEnte).where(DimEnte.cod_ibge == outra_uf))


def test_orcamento_declarado_e_independente_do_tamanho_da_carteira() -> None:
    """A catraca do baseline: o orçamento não pode voltar a crescer com a carteira.

    Se alguém "ajustar" o limiar para caber uma regressão, este teste denuncia — o valor
    só faz sentido enquanto for **independente** do número de entes monitorados.
    """
    assert ORCAMENTO_CONSULTAS_GATE < MUNICIPIOS_NA_CARTEIRA / 10
