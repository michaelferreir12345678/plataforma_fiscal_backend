"""As invariantes do domínio valem **no dado**, não só no código.

O CLAUDE.md declara cinco regras que valem em todo o sistema, e o código as cumpre — há
422, há tipo, há `assert`. O dado as violou duas vezes nesta auditoria, em silêncio:

* a esfera era exigida e estava nula para União e Distrito Federal;
* a RCL é "o denominador" e 32 linhas guardavam **zero**, vindas de bimestre sem Anexo 03
  entregue. O RGF do mesmo ente publicava R$ 57.301.035,70 no mesmo fecho.

Este arquivo é a rede que faltava: a pergunta feita ao banco, não ao código.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.db import admin_session
from app.core.errors import AppError
from app.modules.quality import invariantes


def test_o_acervo_nao_viola_nenhuma_invariante() -> None:
    """O teste de regressão do domínio inteiro.

    Falhar aqui não pede investigação de um ente: pede correção do sistema. A mensagem
    traz a regra e a consequência para que quem receber a falha entenda o que quebrou sem
    ter de reabrir o histórico.
    """
    with admin_session() as s:
        violacoes = invariantes.verificar(s)
    if violacoes:
        detalhe = "\n".join(
            f"  · {v.codigo} ({v.quantidade}): {v.regra}\n    consequência: {v.consequencia}\n"
            f"    exemplo: {v.exemplos[0] if v.exemplos else '—'}"
            for v in violacoes
        )
        pytest.fail(f"{len(violacoes)} invariante(s) do domínio violada(s):\n{detalhe}")


@pytest.mark.parametrize("inv", invariantes.INVARIANTES, ids=lambda i: i.codigo)
def test_toda_invariante_declara_regra_consequencia_e_fundamento(
    inv: invariantes.Invariante,
) -> None:
    """Invariante sem consequência declarada não ensina nada a quem recebe a falha."""
    assert inv.regra and inv.consequencia and inv.fundamento
    assert "select" in inv.sql.lower()


def test_a_rcl_recusa_se_a_materializar_sem_o_anexo_03() -> None:
    """A causa raiz do zero: entrega existente, Anexo 03 ausente.

    Antes, `_calcular_rcl_puro([])` devolvia zeros e a materialização os gravava. Uma RCL
    zero não existe na realidade fiscal — recusar é a única saída honesta.
    """
    from app.modules.indicators import service as indicators

    with admin_session() as s:
        # Período em que o ente tem entrega de RREO mas nenhuma linha do Anexo 03.
        alvo = s.execute(
            text(
                """
                select e.cod_ibge, e.periodo
                from gold.dim_entrega e
                where e.relatorio = 'RREO' and e.vigente is true
                  and not exists (
                    select 1 from silver.siconfi_rreo r
                    where r.cod_ibge = e.cod_ibge and r.periodo = e.periodo
                      and r.versao_entrega = e.versao_entrega and r.anexo like '%03%'
                  )
                limit 1
                """
            )
        ).first()
    if alvo is None:
        pytest.skip("acervo sem entrega de RREO desprovida de Anexo 03")
    with admin_session() as s, pytest.raises(AppError) as exc:
        indicators.calcular_rcl(s, alvo[0], alvo[1])
    assert exc.value.status == 404
    assert "Anexo 03" in str(exc.value.title)
    assert "zero" in str(exc.value.detail), "o erro precisa dizer por que não devolve zero"


def test_a_verificacao_devolve_apenas_o_que_falhou() -> None:
    """Listar as respeitadas encheria o relatório de verde e esconderia a violação."""
    with admin_session() as s:
        violacoes = invariantes.verificar(s)
    assert all(v.quantidade > 0 for v in violacoes)
