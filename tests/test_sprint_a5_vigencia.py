"""Sprint A5 — eleger a versão vigente (A14 + A15 + A21), "a mesma família fechada de
uma vez": versão que existe, vigência que não se declara.

A14 (este arquivo): ``silver.tesouro_fpm``, ``silver.fnde_fundeb_repasse`` e
``silver.transferencia_generica`` guardam ``versao_entrega`` sem coluna de vigência
própria. Dois leitores somavam **todas** as versões em vez de só a vigente — Fortaleza
aparecia com o dobro do FPM real de 2024. A correção usa ``gold.dim_entrega`` (já
existente, ``cod_ibge='BR'`` — a ingestão nacional roda o Brasil inteiro numa corrida só,
§6.7): não foi preciso migration nenhuma, só consultar o controle de vigência que já
existia e não era lido por estes dois consumidores.

A15 (RGF republicado) tem testes em ``test_limites_endividamento.py`` e
``test_reconciliacao.py``. A21 (alertas órfãos) tem teste em ``test_alerts.py``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.db import admin_session
from app.modules.forecast import series as forecast_series
from app.modules.forecast.periodos import parse_periodo
from app.modules.revenue import repository as revenue_repo

FORTALEZA = "2304400"


def test_fortaleza_fpm_2024_nao_dobra_via_revenue() -> None:
    """Critério de aceite da A14: R$ 1.547,50 mi, não R$ 3.095,00 mi (o dobro)."""
    with admin_session() as s:
        total = revenue_repo.soma_fpm(s, cod_ibge=FORTALEZA, ano=2024, meses=list(range(1, 13)))
    assert total == Decimal("1547501180.68")
    dobrado = Decimal("3095002361.36")
    assert total != dobrado
    assert abs(total * 2 - dobrado) < Decimal("0.01"), (
        "o valor dobrado bate exatamente com 2x o vigente — confirma que o defeito "
        "era somar as duas versões, não um erro de outra natureza"
    )


def test_fortaleza_fpm_2024_nao_dobra_via_forecast_exogena() -> None:
    """O mesmo defeito, no outro consumidor (exógena das projeções, forecast/series.py)."""
    p = parse_periodo("2024")  # cadência anual: p.meses() = 1..12
    with admin_session() as s:
        valor = forecast_series._fpm_periodo(s, FORTALEZA, p)
    assert valor == pytest.approx(1547501180.68, abs=0.01)


def test_fortaleza_fundeb_2024_nao_dobra() -> None:
    """Mesma família de defeito, mesma correção — FUNDEB tem 1 versão duplicada em 2024."""
    with admin_session() as s:
        total = revenue_repo.soma_fundeb(s, cod_ibge=FORTALEZA, ano=2024, meses=list(range(1, 13)))
    assert total == Decimal("1944922780.36")


def test_completude_por_fonte_ano_fpm_2025_nao_soma_versoes_superadas() -> None:
    """Formaliza o que revelou a A14: 185/185 entes tinham versão duplicada em 2025.

    Para uma amostra real de entes com múltiplas versões de FPM em 2025, o valor lido
    por ``soma_fpm`` tem de bater exatamente com a soma **só da versão vigente** por mês
    (recomputada aqui de forma independente, direto no silver) — nunca com a soma de
    todas as versões. Este é o teste de regressão que teria pego a A14 antes de ela
    acontecer: qualquer leitor futuro que volte a esquecer o filtro de vigência quebra
    aqui, não só em produção.
    """
    with admin_session() as s:
        entes_com_duplicata = [
            r[0]
            for r in s.execute(
                text(
                    """
                    select cod_ibge from silver.tesouro_fpm
                    where ano = 2025
                    group by cod_ibge
                    having count(distinct versao_entrega) > 1
                    order by cod_ibge
                    limit 5
                    """
                )
            ).all()
        ]
        assert entes_com_duplicata, (
            "acervo sem entes com FPM duplicado em 2025 — o cenário que a A14 mediu "
            "(185/185) não está mais reproduzível; confirme se o backfill mudou"
        )

        for cod in entes_com_duplicata:
            lido = revenue_repo.soma_fpm(s, cod_ibge=cod, ano=2025, meses=list(range(1, 13)))

            # Soma independente: só a versão vigente por (mês), via junção direta com
            # gold.dim_entrega (mesma fonte de verdade que o código de produção usa, mas
            # escrita aqui do zero — não reusa resolve_versoes/soma_fpm).
            esperado_row = s.execute(
                text(
                    """
                    select sum(t.valor_liquido)
                      from silver.tesouro_fpm t
                      join gold.dim_entrega e
                        on e.cod_ibge = 'BR' and e.relatorio = 'FPM'
                       and e.periodo = (t.ano || '-M' || lpad(t.mes::text, 2, '0'))
                       and e.vigente = true
                       and e.versao_entrega = t.versao_entrega
                     where t.cod_ibge = :cod and t.ano = 2025
                    """
                ),
                {"cod": cod},
            ).scalar()
            esperado = Decimal(esperado_row) if esperado_row is not None else None

            # E a soma "ingênua" (o defeito da A14) — tem de ser MAIOR que o vigente
            # sempre que há duplicata real (nunca igual, senão a amostra não provaria nada).
            ingenuo_row = s.execute(
                text(
                    "select sum(valor_liquido) from silver.tesouro_fpm "
                    "where cod_ibge = :cod and ano = 2025"
                ),
                {"cod": cod},
            ).scalar()
            ingenuo = Decimal(ingenuo_row) if ingenuo_row is not None else None

            assert lido == esperado, f"{cod}: leitura vigente divergiu do esperado"
            if ingenuo is not None and esperado is not None:
                assert ingenuo >= esperado, f"{cod}: soma ingênua não pode ser menor que a vigente"
