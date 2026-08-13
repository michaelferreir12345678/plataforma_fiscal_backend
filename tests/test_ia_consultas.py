"""Sprint IA-1b — ampliação do catálogo e consulta guiada (§6.1 do plano de MCP).

O teste central deste arquivo é o da **vigência**. A consulta guiada existe porque quem
escreve o SQL sabe do A14/A15 ("versão que existe, vigência que não se declara"); se a
consulta não resolvesse a vigência, ela seria só um SQL livre com nome bonito. Por isso o
cenário é montado com uma **retificação que muda a conclusão**: na versão superada o ente
estourou o limite de pessoal, na vigente não estourou. Uma leitura ingênua devolve o ente
como estourado — e é exatamente essa a falha que precisa reprovar aqui.

A matriz de escopo é parametrizada sobre ``consultas.CATALOGO``: uma consulta nova entra
nela sozinha, e sem argumentos declarados o teste reprova.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.core.db import admin_session, tenant_session
from app.core.deps import Principal
from app.core.errors import ScopeForbiddenError
from app.modules.catalog.models import DimEnte
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import DimEntrega
from app.shared import tooling
from app.shared.scope import EnteNaoLicenciadoError
from app.shared.tooling import consultas
from app.shared.tooling.base import ToolContext

INDICADOR = "pessoal_executivo"
PERIODO = "2091-B4"
#: Período sem nenhuma entrega — é sobre ele que "quem não entregou" tem resposta útil.
PERIODO_SEM_ENTREGA = "2091-B6"

VERSAO_SUPERADA = "ia1b-v1"
VERSAO_VIGENTE = "ia1b-v2"
HOMOLOGADA_SUPERADA = datetime(2024, 1, 20, tzinfo=UTC)
HOMOLOGADA_VIGENTE = datetime(2024, 6, 10, tzinfo=UTC)
AS_OF_RETROATIVO = datetime(2024, 3, 1, tzinfo=UTC)

#: A retificação **muda a conclusão**: estourado na versão superada, normal na vigente.
PCT_SUPERADO = Decimal("56.20")
PCT_VIGENTE = Decimal("48.10")
PCT_VIZINHO = Decimal("57.90")

RCL = Decimal("400000000")

#: Argumentos por consulta guiada. Consulta nova sem entrada aqui **reprova** a matriz.
ARGS_CONSULTA: dict[str, dict[str, object]] = {
    "entes_que_ultrapassaram_faixa": {
        "indicador": INDICADOR,
        "faixas": ["normal", "alerta", "prudencial", "estourado"],
    },
    "ranking_indicador_na_coorte": {"indicador": INDICADOR, "periodo": PERIODO},
    "serie_do_indicador_por_ente": {"indicador": INDICADOR, "entes": []},  # preenchido no teste
    "entes_sem_entrega_da_fonte": {"relatorio": "RREO", "periodo": PERIODO_SEM_ENTREGA},
}


def _cod_ente() -> str:
    return "8" + "".join(random.choices("0123456789", k=6))


@dataclass(frozen=True)
class Cenario:
    """Três entes: o retificado, o vizinho que de fato estourou, e o alheio."""

    retificado: str
    vizinho: str
    alheio: str


def _ente(session, cod: str, nome: str, *, populacao: int) -> None:
    session.add(
        DimEnte(
            cod_ibge=cod,
            nome=nome,
            esfera="municipal",
            uf="CE",
            regiao="Nordeste",
            populacao=populacao,
            pib=Decimal("900000000"),
            rpps=False,
            possui_tcm=False,
        )
    )


def _entrega(session, cod: str, versao: str, homologada: datetime, *, vigente: bool) -> None:
    session.add(
        DimEntrega(
            cod_ibge=cod,
            relatorio="RREO",
            periodo=PERIODO,
            versao_entrega=versao,
            homologada_em=homologada,
            vigente=vigente,
            hash_payload=f"hash-{cod}-{versao}",
        )
    )
    session.add(
        FatoRcl(
            cod_ibge=cod,
            periodo_ref=PERIODO,
            rcl_12m=RCL,
            receita_corrente=RCL,
            deducoes=Decimal("0"),
            versao_entrega=versao,
            memoria={"formula": "cenario ia-1b"},
        )
    )


def _mart(session, cod: str, versao: str, pct: Decimal, faixa: str) -> None:
    session.add(
        MartIndicador(
            cod_ibge=cod,
            periodo=PERIODO,
            indicador=INDICADOR,
            valor_rs=(pct / Decimal(100)) * RCL,
            valor_pct_rcl=pct,
            faixa=faixa,
            teto_pct=Decimal("54"),
            denominador="rcl",
            base_valor=RCL,
            versao_entrega=versao,
            source_ref={
                "relatorio": "RGF",
                "anexo": "Anexo 01",
                "periodo": PERIODO,
                "versao_entrega": versao,
                "indicador": INDICADOR,
                "esfera": "municipal",
            },
        )
    )


@pytest.fixture
def cenario() -> Iterator[Cenario]:
    retificado, vizinho, alheio = _cod_ente(), _cod_ente(), _cod_ente()
    with admin_session() as s:
        _ente(s, retificado, "Município Retificado", populacao=90_000)
        _ente(s, vizinho, "Município Vizinho", populacao=120_000)
        _ente(s, alheio, "Município Alheio", populacao=70_000)

        # O retificado publicou duas vezes: a primeira estourou, a segunda corrigiu.
        _entrega(s, retificado, VERSAO_SUPERADA, HOMOLOGADA_SUPERADA, vigente=False)
        _entrega(s, retificado, VERSAO_VIGENTE, HOMOLOGADA_VIGENTE, vigente=True)
        _mart(s, retificado, VERSAO_SUPERADA, PCT_SUPERADO, "estourado")
        _mart(s, retificado, VERSAO_VIGENTE, PCT_VIGENTE, "normal")

        # O vizinho publicou uma vez e estourou de verdade.
        _entrega(s, vizinho, VERSAO_SUPERADA, HOMOLOGADA_SUPERADA, vigente=True)
        _mart(s, vizinho, VERSAO_SUPERADA, PCT_VIZINHO, "estourado")

        # O alheio também estourou — e não pode aparecer para quem não o licenciou.
        _entrega(s, alheio, VERSAO_SUPERADA, HOMOLOGADA_SUPERADA, vigente=True)
        _mart(s, alheio, VERSAO_SUPERADA, PCT_VIZINHO, "estourado")
    yield Cenario(retificado=retificado, vizinho=vizinho, alheio=alheio)
    cods = [retificado, vizinho, alheio]
    with admin_session() as s:
        s.execute(delete(MartIndicador).where(MartIndicador.cod_ibge.in_(cods)))
        s.execute(delete(FatoRcl).where(FatoRcl.cod_ibge.in_(cods)))
        s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge.in_(cods)))
        s.execute(delete(DimEnte).where(DimEnte.cod_ibge.in_(cods)))


def _principal(org) -> Principal:
    return Principal(
        usuario_id=org.usuario_id,
        org_id=org.org_id,
        papel="Papel",
        capacidades=frozenset(org.capacidades),
        escopo_ibges=None if org.escopo is None else frozenset(org.escopo),
    )


def _invocar(org, nome: str, argumentos: dict):
    with tenant_session(org.org_id, user_id=org.usuario_id) as session:
        ctx = ToolContext(session=session, principal=_principal(org), origem="teste")
        return tooling.invoke(ctx, tooling.registro(), nome, argumentos)


def _codigos(payload: dict) -> set[str]:
    return {linha["cod_ibge"] for linha in payload.get("resultados", [])}


# --------------------------------------------------------------------------- #
# 1. Vigência — o critério de aceite que a consulta guiada existe para cumprir
# --------------------------------------------------------------------------- #
def test_consulta_nao_conta_versao_superada(client, make_org, cenario) -> None:
    """A11/A14: o ente que estourou e **retificou** não pode voltar como estourado.

    Este é o teste que separa consulta guiada de SQL livre. Na versão superada o
    retificado tinha 56,20% (estourado); na vigente tem 48,10% (normal). Uma leitura que
    não resolve vigência devolve os dois entes; a correta devolve só o vizinho.
    """
    org = make_org(entes=[cenario.retificado, cenario.vizinho])
    resultado = _invocar(
        org,
        "entes_que_ultrapassaram_faixa",
        {"indicador": INDICADOR, "faixas": ["estourado"]},
    )
    codigos = _codigos(resultado.payload)
    assert cenario.vizinho in codigos, "o ente que de fato estourou sumiu"
    assert cenario.retificado not in codigos, (
        "a versão superada voltou: a consulta somou/leu uma entrega já retificada (A14)"
    )
    assert resultado.payload["total"] == 1


def test_consulta_nao_duplica_o_ente_retificado(client, make_org, cenario) -> None:
    """Uma linha por (ente, período) — duas seria a retificação contada em dobro."""
    org = make_org(entes=[cenario.retificado])
    resultado = _invocar(
        org,
        "serie_do_indicador_por_ente",
        {"indicador": INDICADOR, "entes": [cenario.retificado]},
    )
    linhas = resultado.payload["resultados"]
    assert len(linhas) == 1, f"esperava uma linha vigente, veio {len(linhas)}"
    assert linhas[0]["versao_entrega"] == VERSAO_VIGENTE
    assert Decimal(str(linhas[0]["valor_pct"])) == PCT_VIGENTE
    # E a procedência acompanha a linha, com a versão que a originou (§6.3/G4).
    assert linhas[0]["source_ref"]["versao_entrega"] == VERSAO_VIGENTE
    assert linhas[0]["source_ref"]["relatorio"] == "RGF"


def test_as_of_retroativo_devolve_a_versao_de_entao(client, make_org, cenario) -> None:
    """G5: reproduzir o relatório de março vê o estouro que existia em março."""
    org = make_org(entes=[cenario.retificado, cenario.vizinho])
    historico = _invocar(
        org,
        "entes_que_ultrapassaram_faixa",
        {
            "indicador": INDICADOR,
            "faixas": ["estourado"],
            "as_of": AS_OF_RETROATIVO.isoformat(),
        },
    )
    codigos = _codigos(historico.payload)
    assert cenario.retificado in codigos, "o as_of não voltou à versão vigente de então"
    linha = next(
        item
        for item in historico.payload["resultados"]
        if item["cod_ibge"] == cenario.retificado
    )
    assert linha["versao_entrega"] == VERSAO_SUPERADA
    assert Decimal(str(linha["valor_pct"])) == PCT_SUPERADO


def test_serie_por_ente_respeita_as_of(client, make_org, cenario) -> None:
    org = make_org(entes=[cenario.retificado])
    resultado = _invocar(
        org,
        "serie_do_indicador_por_ente",
        {
            "indicador": INDICADOR,
            "entes": [cenario.retificado],
            "as_of": AS_OF_RETROATIVO.isoformat(),
        },
    )
    linhas = resultado.payload["resultados"]
    assert len(linhas) == 1
    assert linhas[0]["versao_entrega"] == VERSAO_SUPERADA


def test_toda_consulta_declara_como_resolve_vigencia() -> None:
    """Catraca de revisão: o campo existe para ser lido por quem audita, não por enfeite."""
    for consulta in consultas.CATALOGO:
        assert consulta.vigencia.strip(), f"{consulta.nome} não declara a vigência"
        assert "vigente" in consulta.vigencia.lower()


# --------------------------------------------------------------------------- #
# 2. Matriz de escopo — parametrizada sobre o catálogo
# --------------------------------------------------------------------------- #
def _args(consulta: consultas.ConsultaGuiada, cenario: Cenario) -> dict:
    args = dict(ARGS_CONSULTA[consulta.nome])
    if consulta.nominal:
        args["entes"] = [cenario.retificado, cenario.vizinho]
    return args


def test_toda_consulta_do_catalogo_esta_na_matriz() -> None:
    faltando = [c.nome for c in consultas.CATALOGO if c.nome not in ARGS_CONSULTA]
    assert not faltando, f"consultas fora da matriz de escopo: {faltando}"


def test_catalogo_inteiro_esta_registrado_como_ferramenta() -> None:
    registro = tooling.registro()
    for consulta in consultas.CATALOGO:
        assert consulta.nome in registro, f"{consulta.nome} não foi registrada"


@pytest.mark.parametrize("consulta", consultas.CATALOGO, ids=lambda c: c.nome)
def test_consulta_nunca_devolve_ente_fora_do_escopo(
    client, make_org, cenario, consulta
) -> None:
    """O ente alheio estourou o limite e **não** pode aparecer para quem não o licenciou."""
    org = make_org(entes=[cenario.retificado, cenario.vizinho])
    resultado = _invocar(org, consulta.nome, _args(consulta, cenario))
    texto = json.dumps(resultado.payload)
    assert cenario.alheio not in texto, (
        f"{consulta.nome} vazou um ente fora da carteira — o escopo não foi aplicado"
    )


@pytest.mark.parametrize(
    "consulta", [c for c in consultas.CATALOGO if c.nominal], ids=lambda c: c.nome
)
def test_consulta_nominal_recusa_ente_fora_da_carteira(
    client, make_org, cenario, consulta
) -> None:
    """Nomear um ente alheio é recusa explícita, não omissão silenciosa."""
    org = make_org(entes=[cenario.retificado])
    args = dict(ARGS_CONSULTA[consulta.nome])
    args["entes"] = [cenario.retificado, cenario.alheio]
    with pytest.raises(ScopeForbiddenError) as exc:
        _invocar(org, consulta.nome, args)
    assert exc.value.status == 403
    assert exc.value.type.endswith("scope-forbidden")


@pytest.mark.parametrize(
    "consulta", [c for c in consultas.CATALOGO if c.nominal], ids=lambda c: c.nome
)
def test_consulta_nominal_distingue_licenca_de_carteira(
    client, make_org, cenario, consulta
) -> None:
    """Os dois 403 continuam distinguíveis (E1) — cadastro e comercial pedem ações opostas."""
    org = make_org(entes=[cenario.retificado], licenciar=False)
    args = dict(ARGS_CONSULTA[consulta.nome])
    args["entes"] = [cenario.retificado]
    with pytest.raises(EnteNaoLicenciadoError) as exc:
        _invocar(org, consulta.nome, args)
    assert exc.value.type.endswith("ente-nao-licenciado")


@pytest.mark.parametrize(
    "consulta", [c for c in consultas.CATALOGO if not c.nominal], ids=lambda c: c.nome
)
def test_consulta_agregada_sem_licenca_nao_devolve_linha(
    client, make_org, cenario, consulta
) -> None:
    """Licença suspensa muda o resultado da mesma consulta — como no SQL da IA-4."""
    licenciada = make_org(entes=[cenario.retificado, cenario.vizinho])
    sem_licenca = make_org(entes=[cenario.retificado, cenario.vizinho], licenciar=False)
    com = _invocar(licenciada, consulta.nome, _args(consulta, cenario))
    sem = _invocar(sem_licenca, consulta.nome, _args(consulta, cenario))
    assert com.payload["total"] > 0, f"{consulta.nome} não devolveu linha nem licenciada"
    assert sem.payload["total"] == 0
    assert "escopo" in (sem.payload["observacao"] or "").lower()


def test_escopo_restrito_do_usuario_vale_na_consulta(client, make_org, cenario) -> None:
    """``membership_escopo`` recorta a carteira também na consulta agregada."""
    org = make_org(
        entes=[cenario.retificado, cenario.vizinho], escopo=[cenario.retificado]
    )
    resultado = _invocar(
        org,
        "entes_que_ultrapassaram_faixa",
        {"indicador": INDICADOR, "faixas": ["normal", "estourado"]},
    )
    assert _codigos(resultado.payload) == {cenario.retificado}


# --------------------------------------------------------------------------- #
# 3. Contenção e ausência com saída
# --------------------------------------------------------------------------- #
def test_limite_de_linhas_e_declarado_quando_trunca(client, make_org, cenario) -> None:
    org = make_org(entes=[cenario.retificado, cenario.vizinho])
    resultado = _invocar(
        org,
        "ranking_indicador_na_coorte",
        {"indicador": INDICADOR, "periodo": PERIODO, "limite": 1},
    )
    assert resultado.payload["truncado"] is True
    assert len(resultado.payload["resultados"]) == 1
    assert "truncada" in resultado.payload["observacao"]


def test_limite_acima_do_teto_e_recusado(client, make_org, cenario) -> None:
    """Contenção é do sistema: o modelo não negocia o teto de linhas."""
    org = make_org(entes=[cenario.retificado])
    with pytest.raises(tooling.EntradaInvalidaError):
        _invocar(
            org,
            "ranking_indicador_na_coorte",
            {"indicador": INDICADOR, "limite": consultas.LIMITE_MAXIMO + 1},
        )


def test_quem_nao_entregou_usa_ausencia_de_versao_vigente(client, make_org, cenario) -> None:
    org = make_org(entes=[cenario.retificado, cenario.vizinho])
    resultado = _invocar(
        org,
        "entes_sem_entrega_da_fonte",
        {"relatorio": "RREO", "periodo": PERIODO_SEM_ENTREGA},
    )
    codigos = _codigos(resultado.payload)
    assert {cenario.retificado, cenario.vizinho} <= codigos
    assert resultado.payload["entes_com_dado"] == 0
    # O período em que os dois entregaram devolve o complemento.
    entregue = _invocar(
        org, "entes_sem_entrega_da_fonte", {"relatorio": "RREO", "periodo": PERIODO}
    )
    assert cenario.retificado not in _codigos(entregue.payload)


def test_ranking_sem_apuracao_explica_em_vez_de_estourar(client, make_org, cenario) -> None:
    org = make_org(entes=[cenario.retificado])
    resultado = _invocar(
        org, "ranking_indicador_na_coorte", {"indicador": "indicador_que_nao_existe"}
    )
    assert resultado.payload["resultados"] == []
    assert "não" in (resultado.payload["observacao"] or "")


def test_faixa_desconhecida_e_recusada_com_as_validas(client, make_org, cenario) -> None:
    org = make_org(entes=[cenario.retificado])
    with pytest.raises(tooling.EntradaInvalidaError) as exc:
        _invocar(
            org,
            "entes_que_ultrapassaram_faixa",
            {"indicador": INDICADOR, "faixas": ["estourado"], "faixa": "estourado"},
        )
    assert exc.value.status == 422


# --------------------------------------------------------------------------- #
# 4. Auditoria da consulta guiada (G7)
# --------------------------------------------------------------------------- #
def test_consulta_guiada_fica_auditada(client, make_org, cenario) -> None:
    """É por ``op.ia_tool_call`` que se mede quanto da cauda longa cai fora do catálogo."""
    from sqlalchemy import select

    from app.modules.assistant.models import IaToolCall

    org = make_org(entes=[cenario.retificado, cenario.vizinho])
    _invocar(
        org,
        "entes_que_ultrapassaram_faixa",
        {"indicador": INDICADOR, "faixas": ["estourado"]},
    )
    with admin_session() as s:
        linhas = list(
            s.scalars(select(IaToolCall).where(IaToolCall.org_id == org.org_id))
        )
    assert len(linhas) == 1
    assert linhas[0].ferramenta == "entes_que_ultrapassaram_faixa"
    assert linhas[0].status == "ok"
    assert linhas[0].argumentos["indicador"] == INDICADOR
    # Consulta que atravessa entes não carimba um ente só na auditoria.
    assert linhas[0].cod_ibge is None
    assert linhas[0].source_refs
