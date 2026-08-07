"""Testes da Sprint A4_MSC/A4_SIOPS — ``--dry-run`` do backfill nacional de MSC/SIOPS/SIOPE.

A4 deixou MSC/SIOPS/SIOPE com conector completo mas cobertura real de 1 único ente cada
(o histórico nunca foi carregado por volume, não por defeito — ver
``docs/evolucao_plataforma.md``). Esta sprint não mexe em conector nenhum: adiciona
``estimate_backfill`` (o motor de ``app/workers/backfill.py``) para medir o plano antes de
pagar o custo, e ``scripts/backfill_msc_siops_siope.py`` para montar esse plano em escopo
nacional.

Cobertura destes testes:
- a fórmula de chamadas HTTP do MSC deriva dos atributos do conector (não é um literal
  solto) e bate com ``184 × meses × 12`` (o critério de aceite da ficha);
- SIOPS/SIOPE fazem 1 chamada por ente por bimestre, e isso vem do próprio job (o job pode
  agrupar vários entes — ver a razão na docstring do script);
- ``--dry-run`` não grava nada: nem bronze, nem entrega, nem silver, nem checkpoint (prova
  direta no banco, antes/depois, com um ente que só existe dentro do teste);
- as sentinelas de rede/gravação bloqueiam qualquer uso indevido;
- o plano nacional inclui o ente estadual, não duplica chaves, e reusa o MESMO arquivo de
  checkpoint do Sprint 21 (a guarda de idempotência é a mesma, não uma nova).
"""

from __future__ import annotations

import random
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.modules.ingestion.connectors.siconfi import MscConnector
from app.modules.ingestion.models import (
    FONTE_MSC,
    FONTE_SIOPE,
    FONTE_SIOPS,
    DimEntrega,
    RawPayload,
    SilverMsc,
    SiopeEducacao,
    SiopsSaude,
)
from app.modules.ingestion.schemas import RunRequest
from app.workers import backfill
from scripts import backfill_msc_siops_siope as script
from scripts import backfill_sprint21


def _ente7() -> str:
    return "8" + "".join(random.choices("0123456789", k=6))


class FakeClient:
    """Cliente + resolver falso: se for chamado, o teste que o injetou está errado."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, fonte: str) -> FakeClient:
        return self

    def get_records(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((path, dict(params)))
        return []

    def close(self) -> None:
        return None


# =================== a fórmula do MSC deriva do conector, não é valor solto ===================


def test_formula_msc_deriva_de_classes_x_tipos_valor() -> None:
    """12 = 4 classes × 3 tipos_valor — os MESMOS atributos que MscConnector.extract percorre
    (connectors/siconfi.py:321-329). Se o conector mudasse esses atributos, a estimativa
    mudaria junto — não há um "12" hardcoded em paralelo que pudesse divergir."""
    assert tuple(MscConnector.classes) == (1, 2, 3, 4)
    assert len(MscConnector.tipos_valor) == 3
    esperado = len(MscConnector.classes) * len(MscConnector.tipos_valor)
    assert esperado == 12

    conn = MscConnector(backfill._DryRunNetworkGuard(), backfill._DryRunWriteGuard())
    job = conn.build_job("2304400", 2023, 1, "1", None)
    assert backfill._http_calls_per_job(FONTE_MSC, conn, job) == esperado


def test_estimate_msc_bate_formula_184_x_meses_x_12() -> None:
    """Reproduz o critério de aceite ao pé da letra: 184 municípios (como o Ceará) + 1
    ente estadual, 1 ano (12 meses, o MSC não filtra período ainda não decorrido) ->
    184×12×12 + 1×12×12 chamadas — o "(± ente-estado)" da ficha é exatamente este +144.
    """
    municipios_fake = [f"99{n:05d}" for n in range(184)]
    entes = [*municipios_fake, "99"]  # 184 municípios fake + 1 "estado" fake
    units = [
        backfill.BackfillUnit(
            key=f"{FONTE_MSC}:{ente}:2023",
            req=RunRequest(fonte=FONTE_MSC, entes=[ente], anos=[2023]),
        )
        for ente in entes
    ]
    est = backfill.estimate_backfill(units)
    assert est.total_unidades == 185
    assert est.total_jobs == 185 * 12
    assert est.total_chamadas_http == 184 * 12 * 12 + 1 * 12 * 12
    assert est.total_chamadas_http == 26_640


def test_estimate_msc_tempo_estimado_usa_o_rate_limit_informado() -> None:
    unit = backfill.BackfillUnit(
        key=f"{FONTE_MSC}:2304400:2023",
        req=RunRequest(fonte=FONTE_MSC, entes=["2304400"], anos=[2023]),
    )
    est = backfill.estimate_backfill([unit], max_per_second=6.0)
    assert est.total_chamadas_http == 144
    assert est.tempo_estimado_segundos == pytest.approx(144 / 6.0)


# =================== SIOPS/SIOPE: 1 chamada por ente por bimestre, vem do job ===================


@pytest.mark.parametrize("fonte", [FONTE_SIOPS, FONTE_SIOPE])
def test_estimate_siops_siope_uma_chamada_por_ente_por_bimestre(fonte: str) -> None:
    entes = [f"99{n:05d}" for n in range(50)] + ["99"]  # 50 municípios fake + o "estado"
    unit = backfill.BackfillUnit(
        key=f"{fonte}:99:2023", req=RunRequest(fonte=fonte, entes=entes, anos=[2023])
    )
    est = backfill.estimate_backfill([unit])
    assert est.total_jobs == 6  # 6 bimestres (discover não filtra período elapsed)
    assert est.total_chamadas_http == 6 * len(entes)  # 1 chamada por ente, por bimestre


def test_estimate_siops_respeita_periodos_explicitos() -> None:
    """Períodos explícitos restringem os bimestres — e portanto as chamadas contadas."""
    unit = backfill.BackfillUnit(
        key="x",
        req=RunRequest(
            fonte=FONTE_SIOPS, entes=["99", "9900001"], anos=[2023], periodos=[1, 2]
        ),
    )
    est = backfill.estimate_backfill([unit])
    assert est.total_jobs == 2
    assert est.total_chamadas_http == 2 * 2


def test_estimate_siops_e_siope_divergem_quando_lotes_diferem() -> None:
    """Confirma que a estimativa é por-job (não uma constante global): lotes de tamanhos
    diferentes para a MESMA fonte produzem chamadas HTTP diferentes."""
    pequeno = backfill.BackfillUnit(
        key="a", req=RunRequest(fonte=FONTE_SIOPS, entes=["99"], anos=[2023])
    )
    grande = backfill.BackfillUnit(
        key="b",
        req=RunRequest(fonte=FONTE_SIOPS, entes=["99", "9900001", "9900002"], anos=[2023]),
    )
    est = backfill.estimate_backfill([pequeno, grande])
    por_unidade = {u.key: u.chamadas_http for u in est.unidades}
    assert por_unidade["a"] == 6
    assert por_unidade["b"] == 18


# =================== sentinelas: nada de rede, nada de gravação ===================


def test_sentinela_de_rede_bloqueia_get_records() -> None:
    with pytest.raises(RuntimeError, match="dry-run"):
        backfill._DryRunNetworkGuard().get_records("tt/msc_patrimonial", {})


def test_sentinela_de_rede_bloqueia_fetch() -> None:
    with pytest.raises(RuntimeError, match="dry-run"):
        backfill._DryRunNetworkGuard().fetch({"url": "https://example.org/x.xlsx"})


def test_sentinela_de_gravacao_bloqueia_upsert_bronze() -> None:
    with pytest.raises(RuntimeError, match="dry-run"):
        backfill._DryRunWriteGuard().upsert_bronze(None, None, None, "hash")  # type: ignore[arg-type]


def test_sentinela_de_gravacao_bloqueia_register_entrega() -> None:
    with pytest.raises(RuntimeError, match="dry-run"):
        backfill._DryRunWriteGuard().register_entrega(None, None, "hash")  # type: ignore[arg-type]


def test_estimate_backfill_fonte_desconhecida_falha_claro() -> None:
    unit = backfill.BackfillUnit(
        key="x", req=RunRequest(fonte="fonte_que_nao_existe", entes=["99"], anos=[2023])
    )
    with pytest.raises(ValueError, match="Fonte desconhecida"):
        backfill.estimate_backfill([unit])


# =================== --dry-run não grava nada no banco real (antes/depois) ===================


def test_dry_run_nao_grava_nada_no_banco(tmp_path: Path) -> None:
    """Um ente que só existe dentro deste teste: se QUALQUER linha aparecer para ele em
    bronze/entrega/silver depois do --dry-run, o dry-run deixou de ser dry."""
    ente = _ente7()
    checkpoint_intocado = tmp_path / "nao_deveria_existir.json"
    units = [
        backfill.BackfillUnit(
            key=f"{FONTE_MSC}:{ente}:2023",
            req=RunRequest(fonte=FONTE_MSC, entes=[ente], anos=[2023]),
        ),
        backfill.BackfillUnit(
            key=f"{FONTE_SIOPS}:{ente}:2023",
            req=RunRequest(fonte=FONTE_SIOPS, entes=[ente], anos=[2023]),
        ),
        backfill.BackfillUnit(
            key=f"{FONTE_SIOPE}:{ente}:2023",
            req=RunRequest(fonte=FONTE_SIOPE, entes=[ente], anos=[2023]),
        ),
    ]

    # estimate_backfill nem recebe um checkpoint_path — a assinatura não tem esse parâmetro,
    # o que já é uma garantia estrutural. A verificação abaixo é ainda assim explícita.
    est = backfill.estimate_backfill(units)
    assert est.total_chamadas_http > 0  # o plano tem conteúdo real, não é um no-op trivial

    assert not checkpoint_intocado.exists()

    with SessionLocal() as s:
        for model in (RawPayload, DimEntrega, SilverMsc, SiopsSaude, SiopeEducacao):
            n = s.scalar(
                select(func.count()).select_from(model).where(model.cod_ibge == ente)
            )
            assert n == 0, f"{model.__name__} tem {n} linha(s) para {ente} após --dry-run"


def test_dry_run_do_script_nao_grava_nada_no_banco() -> None:
    """Mesma prova acima, mas passando pelo caminho real do script (--ufs de um único
    município fake não existe no cadastro, então usamos o filtro por UF real CE só para
    garantir que o script monta e mede o plano sem escrever nada)."""
    with SessionLocal() as s:
        antes_msc = s.scalar(select(func.count()).select_from(SilverMsc))
        antes_entrega = s.scalar(select(func.count()).select_from(DimEntrega))

    args = SimpleNamespace(anos="2023", fontes=FONTE_MSC, ufs="CE")
    with SessionLocal() as s:
        units = script._plano(s, args)
    estimativa = backfill.estimate_backfill(units)
    assert estimativa.total_unidades == len(units) > 0

    with SessionLocal() as s:
        depois_msc = s.scalar(select(func.count()).select_from(SilverMsc))
        depois_entrega = s.scalar(select(func.count()).select_from(DimEntrega))
    assert depois_msc == antes_msc
    assert depois_entrega == antes_entrega


# =================== plano nacional: inclui o estado, não duplica, mesma guarda ===================


def test_grupos_por_uf_agrupa_estado_e_municipios_pelo_prefixo_ibge() -> None:
    with SessionLocal() as s:
        grupos = script._grupos_por_uf(s)
    assert len(grupos) == 27  # 26 estados + DF
    assert "23" in grupos["23"]  # o próprio código do estado do Ceará está no grupo
    assert "2304400" in grupos["23"]  # Fortaleza também
    assert "53" in grupos["53"]  # DF (esfera 'D', não 'E') não fica de fora


def test_filtrar_ufs_aceita_sigla_e_prefixo_numerico() -> None:
    grupos = {"23": ["23", "2304400"], "22": ["22", "2200053"], "11": ["11"]}
    por_sigla = script._filtrar_ufs(grupos, ["CE", "PI"])
    por_numero = script._filtrar_ufs(grupos, ["23", "22"])
    assert por_sigla == por_numero == {"23": grupos["23"], "22": grupos["22"]}
    assert script._filtrar_ufs(grupos, None) == grupos


def test_plano_msc_ce_bate_com_numero_real_e_inclui_o_estado() -> None:
    args = SimpleNamespace(anos="2023", fontes=FONTE_MSC, ufs="CE")
    with SessionLocal() as s:
        units = script._plano(s, args)
    chaves = [u.key for u in units]
    assert len(chaves) == len(set(chaves)), "unidades duplicadas no plano"
    assert len(units) == 185  # 184 municípios + 1 estado, confirmado na inspeção real do CE
    assert any(u.req.entes == ["23"] for u in units)  # o ente estadual está no plano


def test_plano_siops_ce_agrupa_estado_e_municipios_numa_so_unidade() -> None:
    args = SimpleNamespace(anos="2023", fontes=FONTE_SIOPS, ufs="CE")
    with SessionLocal() as s:
        units = script._plano(s, args)
    assert len(units) == 1  # uma unidade por (UF, ano) — não uma por ente
    (unit,) = units
    assert "23" in unit.req.entes
    assert "2304400" in unit.req.entes
    assert len(unit.req.entes) == 185


def test_plano_nacional_sem_ufs_cobre_as_27_ufs_para_cada_fonte() -> None:
    args = SimpleNamespace(anos="2023", fontes=f"{FONTE_MSC},{FONTE_SIOPS}", ufs="")
    with SessionLocal() as s:
        units = script._plano(s, args)
    chaves = [u.key for u in units]
    assert len(chaves) == len(set(chaves))
    siops_units = [u for u in units if u.req.fonte == FONTE_SIOPS]
    assert len(siops_units) == 27  # uma unidade por UF (nacional)


def test_checkpoint_padrao_e_o_mesmo_arquivo_do_sprint21() -> None:
    """A guarda de idempotência do checkpoint é a MESMA do Sprint 21 (âncora CE) — não uma
    nova. Reusar o arquivo é o que garante que o plano nacional não repita unidades que a
    âncora (ou uma corrida anterior deste script) já concluiu."""
    default_sprint21 = Path(backfill_sprint21.VAR_DIR) / "checkpoint.json"
    assert default_sprint21 == script.CHECKPOINT_PADRAO


def test_plano_nacional_respeita_checkpoint_ja_concluido(tmp_path: Path) -> None:
    """Uma unidade já marcada concluída (por exemplo, pela âncora CE do Sprint 21, ou por
    uma corrida anterior deste script) é pulada — nunca reexecutada — mesmo quando o plano
    é reconstruído do zero a partir do cadastro nacional. Usa o MESMO formato de chave que
    ``scripts.backfill_msc_siops_siope`` produz para o MSC."""
    ente = _ente7()
    chave = f"{FONTE_MSC}:{ente}:2023"
    ckpt_path = tmp_path / "ck.json"
    ck = backfill.Checkpoint(path=ckpt_path)
    ck.add(chave)
    ck.save()

    unit = script._unit(FONTE_MSC, chave=ente, entes=[ente], ano=2023)
    assert unit.key == chave  # mesmo formato de chave que o checkpoint já conhece

    fake = FakeClient()
    res = backfill.run_backfill([unit], checkpoint_path=ckpt_path, resolver=fake)
    assert res.pulados == 1
    assert res.executados == 0
    assert fake.calls == []  # nem chegou a tentar: a unidade nunca saiu do "pulado"
