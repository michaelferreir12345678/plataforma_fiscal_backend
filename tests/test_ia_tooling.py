"""Sprint IA-1a — camada de ferramentas: envelope, escopo, fonte, auditoria e as_of.

Os testes de envelope chamam ``invoke`` **direto**, sem passar por HTTP. É deliberado: o
princípio de desenho da sprint é que a garantia mora dentro da ferramenta, não na borda
que a chama (achado A22 da Sprint E1). Um teste que só exercitasse a rota HTTP provaria a
borda, que é exatamente o que não se quer provar.

Tudo roda offline: o provedor de chat é o local determinístico (``ASSISTANT_PROVIDER=local``
no ``conftest``) ou um falso injetado por ``dependency_overrides``.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.db import admin_session, tenant_session
from app.core.deps import Principal
from app.core.errors import AppError, ScopeForbiddenError
from app.main import app
from app.modules.assistant import retriever
from app.modules.assistant.llm import get_llm_provider, schema_para_provedor
from app.modules.assistant.models import Conversa, IaToolCall
from app.modules.catalog.models import DimEnte
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import DimEntrega
from app.shared import tooling
from app.shared.scope import EnteNaoLicenciadoError
from app.shared.source_ref import SourceRef
from app.shared.tooling.base import EnteToolInput, Tool, ToolContext, ToolInput, ToolOutput
from app.shared.tooling.errors import (
    EntradaInvalidaError,
    FerramentaDesconhecidaError,
    FonteAusenteError,
    RegistroInvalidoError,
)
from tests.conftest import auth_header, login
from tests.test_assistant import FakeProvider

PERIODO = "2090-B4"
VERSAO_ANTIGA = "ia1a-v1"
VERSAO_VIGENTE = "ia1a-v2"
#: Homologações: a retificação chega depois, e é isso que o ``as_of`` tem de respeitar.
HOMOLOGADA_ANTIGA = datetime(2024, 1, 20, tzinfo=UTC)
HOMOLOGADA_VIGENTE = datetime(2024, 6, 10, tzinfo=UTC)
AS_OF_RETROATIVO = datetime(2024, 3, 1, tzinfo=UTC)

PCT_ANTIGO = Decimal("10.50")
PCT_VIGENTE = Decimal("21.40")

#: Argumentos mínimos por ferramenta que recebe ente. Uma ferramenta nova sem entrada
#: aqui **reprova** o teste da matriz — é assim que a IA-1b entra na matriz sozinha.
ARGS_MINIMOS: dict[str, dict[str, object]] = {
    "indicador_do_ente": {"indicador": "garantias"},
}


def _cod_ente() -> str:
    return "9" + "".join(random.choices("0123456789", k=6))


@dataclass(frozen=True)
class Cenario:
    ente: str


@pytest.fixture
def cenario() -> Iterator[Cenario]:
    """Ente sintético com RREO retificado e ``garantias`` materializado nas duas versões."""
    cod = _cod_ente()
    with admin_session() as s:
        s.add(
            DimEnte(
                cod_ibge=cod,
                nome="Município Ferramenta",
                esfera="municipal",
                uf="CE",
                regiao="Nordeste",
                populacao=90_000,
                pib=Decimal("900000000"),
                rpps=False,
                possui_tcm=False,
            )
        )
        for versao, homologada, vigente in (
            (VERSAO_ANTIGA, HOMOLOGADA_ANTIGA, False),
            (VERSAO_VIGENTE, HOMOLOGADA_VIGENTE, True),
        ):
            s.add(
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
            s.add(
                FatoRcl(
                    cod_ibge=cod,
                    periodo_ref=PERIODO,
                    rcl_12m=Decimal("400000000"),
                    receita_corrente=Decimal("450000000"),
                    deducoes=Decimal("50000000"),
                    versao_entrega=versao,
                    memoria={"formula": "receita_corrente - deducoes"},
                )
            )
        # `garantias`: um dos códigos que existem no mart e nunca chegavam ao assistente.
        for versao, pct in ((VERSAO_ANTIGA, PCT_ANTIGO), (VERSAO_VIGENTE, PCT_VIGENTE)):
            s.add(
                MartIndicador(
                    cod_ibge=cod,
                    periodo=PERIODO,
                    indicador="garantias",
                    valor_rs=(pct / Decimal(100)) * Decimal("400000000"),
                    valor_pct_rcl=pct,
                    faixa="normal" if pct < Decimal(20) else "alerta",
                    teto_pct=Decimal("22"),
                    denominador="rcl",
                    base_valor=Decimal("400000000"),
                    versao_entrega=versao,
                    source_ref={
                        "relatorio": "RGF",
                        "anexo": "Anexo 03",
                        "periodo": PERIODO,
                        "versao_entrega": versao,
                        "indicador": "garantias",
                        "esfera": "municipal",
                    },
                )
            )
    yield Cenario(ente=cod)
    with admin_session() as s:
        s.execute(delete(MartIndicador).where(MartIndicador.cod_ibge == cod))
        s.execute(delete(FatoRcl).where(FatoRcl.cod_ibge == cod))
        s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
        s.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))


def _principal(org, *, capacidades: list[str] | None = None) -> Principal:
    return Principal(
        usuario_id=org.usuario_id,
        org_id=org.org_id,
        papel="Papel",
        capacidades=frozenset(capacidades if capacidades is not None else org.capacidades),
        escopo_ibges=None if org.escopo is None else frozenset(org.escopo),
    )


def _chamadas(org_id: uuid.UUID) -> list[IaToolCall]:
    with admin_session() as s:
        return list(
            s.scalars(
                select(IaToolCall)
                .where(IaToolCall.org_id == org_id)
                .order_by(IaToolCall.criado_em)
            )
        )


def _invocar(org, nome: str, argumentos: dict, *, capacidades: list[str] | None = None):
    principal = _principal(org, capacidades=capacidades)
    with tenant_session(org.org_id, user_id=org.usuario_id) as session:
        ctx = ToolContext(session=session, principal=principal, origem="teste")
        return tooling.invoke(ctx, tooling.registro(), nome, argumentos)


# --------------------------------------------------------------------------- #
# 1. Registro: declaração incompleta falha na CARGA, não em runtime
# --------------------------------------------------------------------------- #
class _EntradaComEnte(EnteToolInput):
    pass


class _EntradaSemEnte(ToolInput):
    algo: str = "x"


class _SaidaSimples(ToolOutput):
    texto: str = "ok"


class _SaidaComNumero(ToolOutput):
    valor_rs: Decimal = Decimal("1")
    source_ref: SourceRef | None = None


def _tool(**kwargs) -> Tool:
    base = {
        "nome": "ferramenta_teste",
        "descricao": "Ferramenta de teste.",
        "entrada": _EntradaSemEnte,
        "saida": _SaidaSimples,
        "handler": lambda ctx, entrada: _SaidaSimples(),
        "capacidade": "ver",
        "recebe_ente": False,
        "saida_tem_numero_fiscal": False,
    }
    base.update(kwargs)
    return Tool(**base)  # type: ignore[arg-type]


def test_ferramenta_sem_declarar_capacidade_nao_compila() -> None:
    """Esquecer uma declaração é TypeError no import — não um 200 silencioso depois."""
    with pytest.raises(TypeError):
        Tool(  # type: ignore[call-arg]
            nome="x_incompleta",
            descricao="d",
            entrada=_EntradaSemEnte,
            saida=_SaidaSimples,
            handler=lambda ctx, e: _SaidaSimples(),
        )


@pytest.mark.parametrize(
    ("kwargs", "trecho"),
    [
        ({"capacidade": "inventada"}, "não existe no RBAC"),
        ({"recebe_ente": True}, "EnteToolInput"),
        ({"entrada": _EntradaComEnte}, "sem declarar recebe_ente"),
        ({"saida_tem_numero_fiscal": True}, "source_ref"),
        ({"nome": "X-Maiuscula"}, "inválido"),
        ({"descricao": "  "}, "sem descrição"),
    ],
)
def test_registro_recusa_declaracao_incoerente(kwargs: dict, trecho: str) -> None:
    registro = tooling.ToolRegistry()
    with pytest.raises(RegistroInvalidoError) as exc:
        registro.register(_tool(**kwargs))
    assert trecho in str(exc.value)
    assert len(registro) == 0


def test_registro_da_plataforma_declara_tudo() -> None:
    registro = tooling.construir_registro()
    assert set(registro.nomes()) == {"indicador_do_ente", "linhagem_do_indicador"}
    for tool in registro.todas():
        assert tool.capacidade  # RBAC declarado
        assert tool.schema_entrada()["type"] == "object"
        assert tool.schema_saida()["type"] == "object"
        if tool.recebe_ente:
            assert tool.nome in ARGS_MINIMOS, (
                f"{tool.nome} recebe ente e não está na matriz de escopo — acrescente-a."
            )


# --------------------------------------------------------------------------- #
# 2. Matriz de escopo — parametrizada sobre o registro
# --------------------------------------------------------------------------- #
def _ferramentas_com_ente() -> list[str]:
    return [t.nome for t in tooling.registro().todas() if t.recebe_ente]


@pytest.mark.parametrize("ferramenta", _ferramentas_com_ente())
def test_ente_na_carteira_e_licenciado_responde(client, make_org, cenario, ferramenta) -> None:
    org = make_org(entes=[cenario.ente])
    resultado = _invocar(org, ferramenta, {"ente": cenario.ente, **ARGS_MINIMOS[ferramenta]})
    assert resultado.ferramenta == ferramenta
    assert resultado.ente == cenario.ente


@pytest.mark.parametrize("ferramenta", _ferramentas_com_ente())
def test_ente_fora_da_carteira_403_de_escopo(client, make_org, cenario, ferramenta) -> None:
    """403 de escopo — e é o envelope que o levanta, sem nenhuma borda HTTP no caminho."""
    org = make_org(entes=[])
    with pytest.raises(ScopeForbiddenError) as exc:
        _invocar(org, ferramenta, {"ente": cenario.ente, **ARGS_MINIMOS[ferramenta]})
    assert exc.value.status == 403
    assert exc.value.type.endswith("scope-forbidden")


@pytest.mark.parametrize("ferramenta", _ferramentas_com_ente())
def test_ente_na_carteira_sem_licenca_403_de_licenca(client, make_org, cenario, ferramenta) -> None:
    """A distinção de causa é o produto: cadastro e comercial pedem ações opostas (E1)."""
    org = make_org(entes=[cenario.ente], licenciar=False)
    with pytest.raises(EnteNaoLicenciadoError) as exc:
        _invocar(org, ferramenta, {"ente": cenario.ente, **ARGS_MINIMOS[ferramenta]})
    assert exc.value.status == 403
    assert exc.value.type.endswith("ente-nao-licenciado")


def test_os_dois_403_sao_distinguiveis_na_auditoria(client, make_org, cenario) -> None:
    fora = make_org(entes=[])
    sem_licenca = make_org(entes=[cenario.ente], licenciar=False)
    for org in (fora, sem_licenca):
        with pytest.raises(AppError):
            _invocar(org, "indicador_do_ente", {"ente": cenario.ente, "indicador": "garantias"})
    tipos = {
        _chamadas(fora.org_id)[0].erro_tipo,
        _chamadas(sem_licenca.org_id)[0].erro_tipo,
    }
    assert len(tipos) == 2, f"as duas causas colapsaram num tipo só: {tipos}"


def test_sem_capacidade_rbac_403_sem_tocar_no_dado(client, make_org, cenario) -> None:
    org = make_org(entes=[cenario.ente])
    with pytest.raises(AppError) as exc:
        _invocar(
            org,
            "indicador_do_ente",
            {"ente": cenario.ente, "indicador": "garantias"},
            capacidades=["usar_ia"],  # tem IA, não tem 'ver'
        )
    assert exc.value.status == 403
    assert exc.value.type.endswith("missing-capability")


def test_escopo_restrito_do_membership_vale_na_ferramenta(client, make_org, cenario) -> None:
    """Usuário restrito a um subconjunto da carteira não alcança o resto por ferramenta."""
    outro = _cod_ente()
    org = make_org(entes=[cenario.ente, outro], escopo=[outro])
    with pytest.raises(ScopeForbiddenError):
        _invocar(org, "indicador_do_ente", {"ente": cenario.ente, "indicador": "garantias"})


# --------------------------------------------------------------------------- #
# 3. Entrada e saída: o contrato é do envelope
# --------------------------------------------------------------------------- #
def test_argumento_desconhecido_e_recusado(client, make_org, cenario) -> None:
    """``extra='forbid'``: um argumento inventado pelo modelo não pode ser ignorado."""
    org = make_org(entes=[cenario.ente])
    with pytest.raises(EntradaInvalidaError) as exc:
        _invocar(
            org,
            "indicador_do_ente",
            {"ente": cenario.ente, "indicador": "garantias", "exercicio": 2023},
        )
    assert exc.value.status == 422


def test_ferramenta_inexistente_404_com_catalogo(client, make_org) -> None:
    org = make_org(entes=[])
    with pytest.raises(FerramentaDesconhecidaError) as exc:
        _invocar(org, "indicador_inventado", {})
    assert "indicador_do_ente" in exc.value.extras["ferramentas"]


def test_numero_sem_source_ref_e_rejeitado_pelo_envelope(client, make_org) -> None:
    """G4: a rastreabilidade deixa de ser convenção e passa a ser condição de entrega."""
    org = make_org(entes=[])
    registro = tooling.ToolRegistry()
    registro.register(
        _tool(
            nome="ferramenta_sem_fonte",
            saida=_SaidaComNumero,
            handler=lambda ctx, entrada: _SaidaComNumero(valor_rs=Decimal("123.45")),
            saida_tem_numero_fiscal=True,
        )
    )
    principal = _principal(org)
    with tenant_session(org.org_id, user_id=org.usuario_id) as session:
        ctx = ToolContext(session=session, principal=principal, origem="teste")
        with pytest.raises(FonteAusenteError) as exc:
            tooling.invoke(ctx, registro, "ferramenta_sem_fonte", {})
    assert "valor_rs" in exc.value.extras["campos"][0]
    assert exc.value.status == 500

    # A mesma ferramenta, agora com fonte, passa — a guarda mira o vazio, não o número.
    registro_ok = tooling.ToolRegistry()
    registro_ok.register(
        _tool(
            nome="ferramenta_com_fonte",
            saida=_SaidaComNumero,
            handler=lambda ctx, entrada: _SaidaComNumero(
                valor_rs=Decimal("123.45"),
                source_ref=SourceRef(relatorio="RREO", periodo=PERIODO, versao_entrega="v1"),
            ),
            saida_tem_numero_fiscal=True,
        )
    )
    with tenant_session(org.org_id, user_id=org.usuario_id) as session:
        ctx = ToolContext(session=session, principal=principal, origem="teste")
        resultado = tooling.invoke(ctx, registro_ok, "ferramenta_com_fonte", {})
    assert resultado.payload["source_ref"]["relatorio"] == "RREO"


def test_guarda_de_fonte_ignora_contadores_e_cobre_por_ancestral() -> None:
    """A guarda é estrutural: número é dado, contador é envelope."""
    assert tooling.numeros_sem_fonte({"total": 3, "pagina": 1}) == []
    assert tooling.numeros_sem_fonte({"valor_rs": 10}) == ["$.valor_rs"]
    coberto = {
        "source_ref": {"relatorio": "RREO"},
        "itens": [{"valor_rs": 10}, {"valor_pct": 2.5}],
    }
    assert tooling.numeros_sem_fonte(coberto) == []
    parcial = {"itens": [{"valor_rs": 10, "source_ref": {"relatorio": "RGF"}}, {"valor_rs": 1}]}
    assert tooling.numeros_sem_fonte(parcial) == ["$.itens[1].valor_rs"]


# --------------------------------------------------------------------------- #
# 4. Auditoria (G7) — inclusive da chamada que falhou
# --------------------------------------------------------------------------- #
def test_auditoria_registra_chamada_bem_sucedida(client, make_org, cenario) -> None:
    org = make_org(entes=[cenario.ente])
    _invocar(org, "indicador_do_ente", {"ente": cenario.ente, "indicador": "garantias"})
    linhas = _chamadas(org.org_id)
    assert len(linhas) == 1
    linha = linhas[0]
    assert linha.ferramenta == "indicador_do_ente"
    assert linha.status == "ok"
    assert linha.usuario_id == org.usuario_id  # principal
    assert linha.cod_ibge == cenario.ente
    assert linha.argumentos["indicador"] == "garantias"  # argumentos
    assert linha.duracao_ms >= 0  # duração
    assert linha.origem == "teste"
    assert linha.linhas == 1
    assert linha.source_refs and linha.source_refs[0]["relatorio"] == "RGF"


def test_auditoria_registra_a_chamada_que_falhou(client, make_org, cenario) -> None:
    """O rollback da requisição recusada não pode apagar o registro da recusa."""
    org = make_org(entes=[])
    with pytest.raises(ScopeForbiddenError):
        _invocar(org, "indicador_do_ente", {"ente": cenario.ente, "indicador": "garantias"})
    linhas = _chamadas(org.org_id)
    assert len(linhas) == 1
    assert linhas[0].status == "erro"
    assert linhas[0].http_status == 403
    assert linhas[0].erro_tipo.endswith("scope-forbidden")
    assert linhas[0].cod_ibge == cenario.ente
    assert linhas[0].linhas == 0


def test_auditoria_registra_ferramenta_inexistente(client, make_org) -> None:
    """Nome inventado por um modelo é informação de operação, não ruído."""
    org = make_org(entes=[])
    with pytest.raises(FerramentaDesconhecidaError):
        _invocar(org, "consulta_livre_no_banco", {})
    linhas = _chamadas(org.org_id)
    assert len(linhas) == 1
    assert linhas[0].ferramenta == "consulta_livre_no_banco"
    assert linhas[0].status == "erro"
    assert linhas[0].http_status == 404


def test_auditoria_deixa_trilha_geral(client, make_org, cenario) -> None:
    from app.modules.tenancy.models import AuditLog

    org = make_org(entes=[cenario.ente])
    _invocar(org, "indicador_do_ente", {"ente": cenario.ente, "indicador": "garantias"})
    with admin_session() as s:
        acoes = list(
            s.scalars(select(AuditLog.recurso).where(AuditLog.org_id == org.org_id))
        )
    assert any("tool:indicador_do_ente" in r for r in acoes)


# --------------------------------------------------------------------------- #
# 5. Bitemporalidade (G5) — as_of retroativo devolve a versão de então
# --------------------------------------------------------------------------- #
def test_as_of_retroativo_devolve_a_versao_de_entao(client, make_org, cenario) -> None:
    org = make_org(entes=[cenario.ente])
    vigente = _invocar(
        org, "indicador_do_ente", {"ente": cenario.ente, "indicador": "garantias"}
    )
    assert vigente.payload["versao_entrega"] == VERSAO_VIGENTE
    assert Decimal(str(vigente.payload["valor_pct"])) == PCT_VIGENTE

    historico = _invocar(
        org,
        "indicador_do_ente",
        {
            "ente": cenario.ente,
            "indicador": "garantias",
            "as_of": AS_OF_RETROATIVO.isoformat(),
        },
    )
    assert historico.payload["versao_entrega"] == VERSAO_ANTIGA
    assert Decimal(str(historico.payload["valor_pct"])) == PCT_ANTIGO
    assert historico.payload["source_ref"]["versao_entrega"] == VERSAO_ANTIGA
    # A auditoria guarda o as_of pedido: reproduzir a consulta depois é possível.
    linhas = _chamadas(org.org_id)
    assert linhas[-1].as_of == AS_OF_RETROATIVO


def test_indicador_nao_materializado_e_ausencia_declarada(client, make_org, cenario) -> None:
    """Ausência é resposta com explicação — nunca zero, nunca estimativa."""
    org = make_org(entes=[cenario.ente])
    resultado = _invocar(
        org, "indicador_do_ente", {"ente": cenario.ente, "indicador": "operacoes_credito"}
    )
    assert resultado.payload["disponivel"] is False
    assert resultado.payload["valor_rs"] is None
    assert resultado.payload["source_ref"] is None
    assert "não está materializado" in resultado.payload["observacao"]
    assert resultado.linhas == 0


# --------------------------------------------------------------------------- #
# 6. linhagem_do_indicador — metadado, sem ente e sem número
# --------------------------------------------------------------------------- #
def test_linhagem_explica_procedencia_e_alcance(client, make_org) -> None:
    org = make_org(entes=[])
    resultado = _invocar(org, "linhagem_do_indicador", {"indicador": "garantias"})
    payload = resultado.payload
    assert payload["tabela_gold"] == "gold.mart_indicador"
    assert any("SICONFI" in fonte for fonte in payload["fontes_de_origem"])
    assert "/limites" in payload["paginas_afetadas"]
    assert payload["total_arestas"] > 0
    assert resultado.linhas == payload["total_arestas"]


def test_linhagem_nao_aceita_ente(client, make_org, cenario) -> None:
    """Ferramenta sem escopo não pode receber ente nem por engano (nem por malícia)."""
    org = make_org(entes=[cenario.ente])
    with pytest.raises(EntradaInvalidaError):
        _invocar(org, "linhagem_do_indicador", {"indicador": "garantias", "ente": cenario.ente})


# --------------------------------------------------------------------------- #
# 7. Primeiro consumo: o assistente alcança `garantias`
# --------------------------------------------------------------------------- #
def test_indicadores_nomeados_alcanca_o_que_o_dicionario_nao_alcancava() -> None:
    assert "garantias" in retriever.indicadores_nomeados("e as garantias concedidas?")
    assert "operacoes_credito" in retriever.indicadores_nomeados(
        "qual o total de operações de crédito?"
    )
    assert "rcl_per_capita" in retriever.indicadores_nomeados("qual a RCL per capita?")
    # "crédito" sozinho não sequestra a pergunta.
    assert "operacoes_credito" not in retriever.indicadores_nomeados(
        "como está o crédito tributário?"
    )


def test_pergunta_sobre_garantias_tem_resposta_fundamentada(client, make_org, cenario) -> None:
    """O critério de aceite da ficha: `garantias` era inalcançável (§1) e passa a responder."""
    org = make_org(entes=[cenario.ente])
    headers = auth_header(login(client, org.email, org.senha))
    resposta = client.post(
        "/assistant/perguntar",
        headers=headers,
        json={
            "ente": cenario.ente,
            "periodo": PERIODO,
            "pergunta": "Qual a situação das garantias concedidas pelo município?",
        },
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    fato = next((f for f in corpo["fatos"] if f["codigo"] == "garantias"), None)
    assert fato is not None, "o assistente continua sem enxergar garantias"
    assert fato["disponivel"] is True
    assert fato["source_ref"]["relatorio"] == "RGF"
    assert fato["source_ref"]["versao_entrega"] == VERSAO_VIGENTE
    assert fato["faixa"] == "alerta"
    assert "21,40%" in fato["valor_formatado"]
    # O número aparece na resposta com fonte, e o chip de fonte acompanha.
    assert "21,40%" in corpo["resposta"]
    assert any(c["rotulo"] == "Garantias e contragarantias" for c in corpo["fontes"])
    # A chamada de ferramenta ficou auditada, ligada à origem 'assistente'.
    linhas = [c for c in _chamadas(org.org_id) if c.ferramenta == "indicador_do_ente"]
    assert linhas and linhas[0].origem == "assistente"
    assert linhas[0].status == "ok"


def test_recusa_honesta_continua_sem_chamar_o_modelo(client, make_org, monkeypatch) -> None:
    """G3: sem dado e sem norma, a recusa da Sprint 17 continua intacta no caminho novo."""
    provider = FakeProvider()
    app.dependency_overrides[get_llm_provider] = lambda: provider
    try:
        monkeypatch.setattr(retriever, "retrieve_normas", lambda *a, **k: [])
        ente = _cod_ente()
        org = make_org(entes=[ente])
        headers = auth_header(login(client, org.email, org.senha))
        resposta = client.post(
            "/assistant/perguntar",
            headers=headers,
            json={"ente": ente, "pergunta": "E as garantias concedidas?"},
        )
        assert resposta.status_code == 200, resposta.text
        corpo = resposta.json()
        assert corpo["recusa"] is True
        assert corpo["dado_disponivel"] is False
        assert provider.calls == [], "o modelo foi chamado sem fundamento"
        with admin_session() as s:
            conversas = list(s.scalars(select(Conversa).where(Conversa.org_id == org.org_id)))
        assert len(conversas) == 1 and conversas[0].recusa is True
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)


# --------------------------------------------------------------------------- #
# 8. Porta de function calling — tradução do esquema (o laço do SDK exige rede)
# --------------------------------------------------------------------------- #
def test_schema_para_provedor_achata_opcionais_e_resolve_refs() -> None:
    bruto = tooling.registro().get("indicador_do_ente").schema_entrada()
    assert "$defs" in bruto or "anyOf" in str(bruto)  # o Pydantic emite isto
    limpo = schema_para_provedor(bruto)
    assert "$defs" not in limpo
    assert "anyOf" not in str(limpo)
    assert limpo["type"] == "object"
    assert set(limpo["required"]) == {"ente", "indicador"}
    assert limpo["properties"]["periodo"]["type"] == "string"
    assert limpo["properties"]["as_of"]["type"] == "string"


def test_especificacoes_expostas_ao_provedor_cobrem_o_registro() -> None:
    from app.modules.assistant.service import especificacoes_de_ferramenta

    specs = especificacoes_de_ferramenta()
    assert {s.nome for s in specs} == set(tooling.registro().nomes())
    assert all(s.descricao and s.parametros["type"] == "object" for s in specs)
