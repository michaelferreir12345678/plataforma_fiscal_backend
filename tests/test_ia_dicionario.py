"""Sprint IA-2 — dicionário semântico: catraca de completude, recursos e a prova do RAG.

Dois grupos:

1. **Catraca** — a defesa contra o risco que a própria ficha nomeia: um dicionário que
   envelhece em silêncio e passa a mentir. Como na Sprint A0R, a catraca aceita a melhora
   (verbete novo, coluna nova descrita) e falha na piora. Parte das asserções compara o
   dicionário com o ``information_schema``: descrever uma coluna que não existe é tão
   grave quanto não descrever uma que existe.

2. **Fundamentação** — a pergunta "o que é RCL Ajustada e por que ela é o denominador do
   limite de pessoal?" respondida **sem** conhecimento próprio do modelo, com provedor
   determinístico. O teste fecha o argumento provando o contrapositivo: o corpo normativo
   indexado não contém a definição, então tudo que aparecer na resposta veio do dicionário.

Tudo roda offline (``ASSISTANT_PROVIDER=local`` no ``conftest``).
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text

from app.core.db import admin_session
from app.main import app
from app.modules.assistant import retriever
from app.modules.assistant.llm import LocalGroundedProvider, get_llm_provider, render_grounding
from app.modules.assistant.models import NormaChunk
from app.modules.catalog.models import DimEnte
from app.modules.dictionary import campos as campos_seed
from app.modules.dictionary import repository as dic_repo
from app.modules.dictionary import service as dictionary
from app.modules.dictionary import verbetes as verbetes_seed
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import DimEntrega
from app.modules.personnel.models import FatoPessoal
from app.shared import tooling
from app.shared.tooling.errors import RegistroInvalidoError
from app.shared.tooling.recursos import URI_CAMPOS, URI_INDICADORES, URI_JUNCOES, Recurso
from tests.conftest import auth_header, login
from tests.test_assistant import FakeProvider

PERIODO = "2092-B4"
#: O RGF do ciclo corrente do bimestre B4 é o Q2 — a tradução canônica da §6.6.
PERIODO_RGF = "2092-Q2"
VERSAO = "ia2-v1"
HOMOLOGADA = datetime(2024, 2, 10, tzinfo=UTC)
RCL_CHEIA = Decimal("500000000")
#: RCL Ajustada menor que a cheia — é a dedução das emendas que a EC 105/2019 manda fazer.
RCL_AJUSTADA = Decimal("480000000")
DESPESA_PESSOAL = Decimal("230400000")  # 48,00% da ajustada; 46,08% da cheia.
PCT_AJUSTADA = Decimal("48.00")

PERGUNTA_RCL_AJUSTADA = (
    "o que é RCL Ajustada e por que ela é o denominador do limite de pessoal?"
)


def _cod_ente() -> str:
    return "9" + "".join(random.choices("0123456789", k=6))


@dataclass(frozen=True)
class Cenario:
    ente: str


@pytest.fixture
def cenario() -> Iterator[Cenario]:
    """Ente sintético com RREO vigente e ``pessoal_executivo`` sobre a RCL **Ajustada**."""
    cod = _cod_ente()
    with admin_session() as s:
        s.add(
            DimEnte(
                cod_ibge=cod,
                nome="Município Dicionário",
                esfera="municipal",
                uf="CE",
                regiao="Nordeste",
                populacao=110_000,
                pib=Decimal("1200000000"),
                rpps=False,
                possui_tcm=False,
            )
        )
        s.add(
            DimEntrega(
                cod_ibge=cod,
                relatorio="RREO",
                periodo=PERIODO,
                versao_entrega=VERSAO,
                homologada_em=HOMOLOGADA,
                vigente=True,
                hash_payload=f"hash-{cod}",
            )
        )
        s.add(
            FatoRcl(
                cod_ibge=cod,
                periodo_ref=PERIODO,
                rcl_12m=RCL_CHEIA,
                receita_corrente=Decimal("560000000"),
                deducoes=Decimal("60000000"),
                versao_entrega=VERSAO,
                memoria={"formula": "receita_corrente - deducoes"},
            )
        )
        # O limite de pessoal cruza duas entregas: o numerador é do RGF, o período do
        # mart é o bimestre do RREO. Sem a entrega do RGF o relatório executivo declara
        # ausência — e é o cenário completo que prova que o número continua vindo do dado.
        s.add(
            DimEntrega(
                cod_ibge=cod,
                relatorio="RGF",
                periodo=PERIODO_RGF,
                versao_entrega=VERSAO,
                homologada_em=HOMOLOGADA,
                vigente=True,
                hash_payload=f"hash-rgf-{cod}",
            )
        )
        s.add(
            FatoPessoal(
                cod_ibge=cod,
                periodo=PERIODO_RGF,
                poder_codigo="ENTE.EXEC",
                despesa_bruta=Decimal("250000000"),
                exclusoes=Decimal("19600000"),
                despesa_liquida=DESPESA_PESSOAL,
                pct_rcl=PCT_AJUSTADA,
                rcl_ajustada=RCL_AJUSTADA,
                versao_entrega=VERSAO,
            )
        )
        s.add(
            MartIndicador(
                cod_ibge=cod,
                periodo=PERIODO,
                indicador="pessoal_executivo",
                valor_rs=DESPESA_PESSOAL,
                valor_pct_rcl=PCT_AJUSTADA,
                faixa="normal",
                teto_pct=Decimal("54"),
                denominador="rcl_ajustada",
                base_valor=RCL_AJUSTADA,
                versao_entrega=VERSAO,
                source_ref={
                    "relatorio": "RGF/RREO",
                    "anexo": "RGF Anexo 01 / RREO Anexo 03",
                    "periodo": PERIODO,
                    "versao_entrega": VERSAO,
                    "indicador": "pessoal_executivo",
                    "esfera": "municipal",
                },
            )
        )
    yield Cenario(ente=cod)
    with admin_session() as s:
        s.execute(delete(MartIndicador).where(MartIndicador.cod_ibge == cod))
        s.execute(delete(FatoPessoal).where(FatoPessoal.cod_ibge == cod))
        s.execute(delete(FatoRcl).where(FatoRcl.cod_ibge == cod))
        s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
        s.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))


# --------------------------------------------------------------------------- #
# 1. Catraca de completude
# --------------------------------------------------------------------------- #
def test_catraca_de_completude_esta_verde() -> None:
    """A catraca inteira: se algo faltar, a mensagem diz exatamente o quê."""
    with admin_session() as s:
        auditoria = dictionary.auditar_completude(s)
        s.commit()
    assert auditoria.ok, "\n".join(auditoria.problemas())


def test_todo_indicador_do_mart_tem_verbete_com_formula_e_base_legal() -> None:
    """Critério de aceite 1 — medido contra o banco **e** contra a lista dos 10 códigos.

    A lista estática existe porque um banco recém-migrado tem ``mart_indicador`` vazio, e
    uma catraca que passa por falta de dado não é catraca.
    """
    with admin_session() as s:
        dictionary.garantir_seed(s)
        s.commit()
        verbetes = {v.codigo: v for v in dic_repo.listar_verbetes(s)}
        no_mart = dic_repo.indicadores_no_mart(s)

    alvos = no_mart | set(verbetes_seed.CODIGOS_MATERIALIZADOS)
    assert alvos >= verbetes_seed.CODIGOS_MATERIALIZADOS
    faltando = sorted(alvos - set(verbetes))
    assert not faltando, f"indicadores sem verbete: {faltando}"
    for codigo in sorted(alvos):
        verbete = verbetes[codigo]
        assert verbete.formula.strip(), f"{codigo} sem fórmula"
        assert verbete.base_legal.strip(), f"{codigo} sem base legal"
        assert verbete.definicao.strip(), f"{codigo} sem definição"
        assert verbete.fonte_definicao.strip(), f"{codigo} sem fonte da definição"
        assert verbete.atualizado_em is not None, f"{codigo} sem data de revisão"


def test_verbete_de_teto_e_verbete_de_piso_declaram_denominadores_diferentes() -> None:
    """Evidência da ficha: um verbete completo de teto e um de piso, lado a lado."""
    with admin_session() as s:
        dictionary.garantir_seed(s)
        s.commit()
        teto = dic_repo.get_verbete(s, "pessoal_executivo")
        piso = dic_repo.get_verbete(s, "saude_minimo")

    assert teto is not None and piso is not None
    assert teto.sentido == "teto" and piso.sentido == "piso"
    # O ponto inteiro da sprint: o denominador **não** é o mesmo, e nem sempre é a RCL.
    assert teto.denominador == "rcl_ajustada"
    assert piso.denominador == "impostos_transferencias"
    assert "emenda" in teto.denominador_definicao.lower()
    assert "não é a rcl" in piso.denominador_definicao.lower()
    assert "art. 20" in teto.base_legal
    assert "art. 198" in piso.base_legal


def test_toda_coluna_de_tabela_consultavel_tem_descricao() -> None:
    """Critério de aceite 2 (primeira metade), medido contra o ``information_schema``."""
    with admin_session() as s:
        dictionary.garantir_seed(s)
        s.commit()
        reais = dic_repo.colunas_reais(s, campos_seed.TABELAS_CONSULTAVEIS)
        descritas: dict[str, set[str]] = {}
        for campo in dic_repo.listar_campos(s):
            descritas.setdefault(f"{campo.schema_nome}.{campo.tabela}", set()).add(campo.coluna)

    for tabela in campos_seed.TABELAS_CONSULTAVEIS:
        assert reais[tabela], f"{tabela} não existe no banco"
    faltando, orfas = dictionary.lacunas_de_campo(reais, descritas)
    assert not faltando, f"colunas sem descrição: {faltando}"
    assert not orfas, f"descrições apontando para colunas inexistentes: {orfas}"


def test_catraca_de_campo_falha_quando_uma_coluna_nova_nao_e_descrita() -> None:
    """A catraca precisa reprovar de verdade — o 'depois' do antes/depois da evidência."""
    reais = {"gold.mart_indicador": {"cod_ibge", "periodo", "coluna_nova"}}
    descritas = {"gold.mart_indicador": {"cod_ibge", "periodo"}}
    faltando, orfas = dictionary.lacunas_de_campo(reais, descritas)
    assert faltando == ["gold.mart_indicador.coluna_nova"]
    assert orfas == []
    # E na direção contrária: coluna removida deixa a descrição órfã.
    faltando, orfas = dictionary.lacunas_de_campo(
        {"gold.mart_indicador": {"cod_ibge"}}, {"gold.mart_indicador": {"cod_ibge", "sumiu"}}
    )
    assert faltando == [] and orfas == ["gold.mart_indicador.sumiu"]


def test_catraca_de_indicador_falha_quando_um_codigo_novo_chega_sem_verbete() -> None:
    lacunas = dictionary.lacunas_de_indicador(
        {"pessoal_executivo", "indicador_novo"}, {"pessoal_executivo": object()}
    )
    assert lacunas == ["indicador_novo"]


def test_nenhuma_tabela_de_op_e_consultavel() -> None:
    """Critério de aceite 2 (segunda metade) — e a proibição é do banco, não da seed."""
    with admin_session() as s:
        dictionary.garantir_seed(s)
        s.commit()
        campos = dic_repo.listar_campos(s)
    assert campos, "dicionário de campos vazio"
    assert all(c.schema_nome in ("gold", "silver") for c in campos)
    assert not [c for c in campos if c.schema_nome == "op"]
    assert all(not t.startswith("op.") for t in campos_seed.TABELAS_CONSULTAVEIS)

    # A garantia estrutural: mesmo uma seed distraída não consegue declarar 'op'.
    with admin_session() as s:
        with pytest.raises(Exception) as exc:
            s.execute(
                text(
                    "INSERT INTO gold.dicionario_campo (schema_nome, tabela, coluna, "
                    "descricao, chave, consultavel, fonte_definicao, atualizado_em) "
                    "VALUES ('op', 'alerta', 'id', 'x', false, true, 'teste', CURRENT_DATE)"
                )
            )
            s.flush()
        assert "ck_dicionario_campo" in str(exc.value)
        s.rollback()


def test_nenhum_verbete_aponta_para_tabela_ou_coluna_inexistente() -> None:
    """Critério de aceite 4 — validado contra o ``information_schema``, não contra o ORM."""
    with admin_session() as s:
        dictionary.garantir_seed(s)
        s.commit()
        verbetes = dic_repo.listar_verbetes(s)
        tabelas = sorted({v.tabela_origem for v in verbetes})
        reais = dic_repo.colunas_reais(s, tabelas)

    for verbete in verbetes:
        colunas = reais[verbete.tabela_origem]
        assert colunas, f"{verbete.codigo} aponta para {verbete.tabela_origem}, que não existe"
        assert verbete.coluna_valor in colunas, f"{verbete.codigo}.coluna_valor inexistente"
        if verbete.coluna_base:
            assert verbete.coluna_base in colunas, f"{verbete.codigo}.coluna_base inexistente"


def test_juncao_sancionada_so_liga_tabelas_consultaveis_com_colunas_reais() -> None:
    with admin_session() as s:
        dictionary.garantir_seed(s)
        s.commit()
        juncoes = dic_repo.listar_juncoes(s)
        reais = dic_repo.colunas_reais(s, campos_seed.TABELAS_CONSULTAVEIS)

    assert juncoes, "nenhuma junção sancionada"
    consultaveis = set(campos_seed.TABELAS_CONSULTAVEIS)
    for juncao in juncoes:
        assert juncao.origem_tabela in consultaveis
        assert juncao.destino_tabela in consultaveis
        assert len(juncao.origem_colunas) == len(juncao.destino_colunas)
        for coluna in juncao.origem_colunas:
            assert coluna in reais[juncao.origem_tabela]
        for coluna in juncao.destino_colunas:
            assert coluna in reais[juncao.destino_tabela]


def test_juncao_que_resolve_vigencia_exige_a_versao_na_chave() -> None:
    """A lição da IA-1b virou dado: a vigência se resolve no JOIN, não num filtro no fim."""
    with admin_session() as s:
        dictionary.garantir_seed(s)
        s.commit()
        juncoes = {(j.origem_tabela, j.destino_tabela): j for j in dic_repo.listar_juncoes(s)}

    mart_entrega = juncoes[("gold.mart_indicador", "gold.dim_entrega")]
    assert "versao_entrega" in mart_entrega.origem_colunas
    assert "versao_entrega" in mart_entrega.destino_colunas
    assert mart_entrega.condicao and "vigente" in mart_entrega.condicao
    assert "multiplica" in mart_entrega.nota or "dobra" in mart_entrega.nota

    # A única 1:n da lista precisa avisar que agregar por ela duplica o consolidado.
    uf_ente = juncoes[("gold.mart_consolidado_uf", "gold.dim_ente")]
    assert uf_ente.cardinalidade == "1:n"
    assert "nunca para somar" in uf_ente.nota.lower()


def test_denominador_declarado_cobre_o_que_o_mart_realmente_usa() -> None:
    """O verbete não pode descolar da apuração — é a checagem que a Sprint 28 não tinha."""
    with admin_session() as s:
        dictionary.garantir_seed(s)
        s.commit()
        observados = dic_repo.denominadores_no_mart(s)
        verbetes = {v.codigo: v for v in dic_repo.listar_verbetes(s)}

    for codigo, denominadores in sorted(observados.items()):
        verbete = verbetes.get(codigo)
        assert verbete is not None, f"{codigo} está no mart e não tem verbete"
        declarados = {verbete.denominador, verbete.denominador_fallback or ""} - {""}
        assert denominadores <= declarados, (
            f"{codigo} usa {sorted(denominadores - declarados)} no mart, "
            f"não declarado no verbete"
        )


def test_sentido_do_verbete_bate_com_dim_limite_legal() -> None:
    """Teto × piso é dado em ``dim_limite_legal``; o verbete não pode contradizê-lo."""
    with admin_session() as s:
        auditoria = dictionary.auditar_completude(s)
        s.commit()
    assert auditoria.sentidos_divergentes == []


def test_seed_e_idempotente() -> None:
    with admin_session() as s:
        primeiro = dictionary.seed_dicionario(s)
        s.commit()
    with admin_session() as s:
        segundo = dictionary.seed_dicionario(s)
        s.commit()
    assert primeiro == segundo
    assert segundo["indicadores"] >= len(verbetes_seed.CODIGOS_MATERIALIZADOS)


# --------------------------------------------------------------------------- #
# 2. Vocabulário de negócio → esquema
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("termo", "esperado"),
    [
        ("gasto com pessoal", "pessoal_executivo"),
        ("qual é a folha de pagamento?", "pessoal_executivo"),
        ("endividamento do município", "divida_consolidada_liquida"),
        ("aplicação em saúde", "saude_minimo"),
        ("70% do fundeb", "fundeb_profissionais"),
        ("receita por habitante", "rcl_per_capita"),
    ],
)
def test_sinonimo_de_negocio_resolve_para_o_codigo_certo(termo: str, esperado: str) -> None:
    with admin_session() as s:
        dictionary.garantir_seed(s)
        s.commit()
        assert dictionary.resolver_codigo(s, termo) == esperado


def test_termo_sem_correspondencia_nao_inventa_indicador() -> None:
    with admin_session() as s:
        dictionary.garantir_seed(s)
        s.commit()
        assert dictionary.resolver_codigo(s, "quantos habitantes tem o bairro") is None


# --------------------------------------------------------------------------- #
# 3. Recurso, não ferramenta (§2.3)
# --------------------------------------------------------------------------- #
def test_dicionario_e_recurso_e_nao_ferramenta() -> None:
    """Transformar dicionário em ferramenta gastaria uma chamada para saber o que já cabia
    no contexto — o erro que a §2.3 descreve nominalmente."""
    nomes = tooling.registro().nomes()
    assert not [n for n in nomes if "dicionario" in n]
    uris = tooling.registro_de_recursos().uris()
    assert uris == sorted([URI_INDICADORES, URI_CAMPOS, URI_JUNCOES])


def test_recurso_nao_pode_ser_parametrizado_por_ente() -> None:
    """Recurso não passa pelo gate de escopo — então não pode carregar dado de ente."""
    registro = tooling.RecursoRegistry()
    with pytest.raises(RegistroInvalidoError) as exc:
        registro.register(
            Recurso(
                uri="dicionario://ente/{ente}",
                nome="x",
                descricao="d",
                mime_type="text/markdown",
                carregar=lambda s: "",
            )
        )
    assert "sem gate de escopo" in str(exc.value)


def test_recurso_sem_descricao_falha_na_carga() -> None:
    registro = tooling.RecursoRegistry()
    with pytest.raises(RegistroInvalidoError):
        registro.register(
            Recurso(
                uri="dicionario://vazio",
                nome="x",
                descricao="   ",
                mime_type="text/markdown",
                carregar=lambda s: "",
            )
        )


def test_recursos_rendem_o_dicionario_inteiro() -> None:
    with admin_session() as s:
        dictionary.garantir_seed(s)
        s.commit()
        indicadores = tooling.ler_recurso(s, URI_INDICADORES)
        campos = tooling.ler_recurso(s, URI_CAMPOS)
        juncoes = tooling.ler_recurso(s, URI_JUNCOES)
        s.commit()

    for codigo in sorted(verbetes_seed.CODIGOS_MATERIALIZADOS):
        assert f"`{codigo}`" in indicadores
    # Os limites vêm de dim_limite_legal, não copiados no verbete (§2 do CLAUDE.md).
    assert "municipal 54% (teto)" in indicadores
    assert "sem limite legal (indicador gerencial)" in indicadores
    for tabela in campos_seed.TABELAS_CONSULTAVEIS:
        assert f"## {tabela}" in campos
    assert "Nenhuma tabela do schema `op` é consultável" in campos
    assert "gold.mart_indicador → gold.dim_entrega (n:1)" in juncoes
    assert "Condição obrigatória:" in juncoes


def test_uri_desconhecida_falha_em_vez_de_devolver_vazio() -> None:
    with admin_session() as s, pytest.raises(RegistroInvalidoError):
        tooling.ler_recurso(s, "dicionario://inexistente")


# --------------------------------------------------------------------------- #
# 4. A prova: a resposta vem do dicionário, não do modelo
# --------------------------------------------------------------------------- #
def test_corpo_normativo_nao_define_rcl_ajustada() -> None:
    """Controle negativo, e é ele que dá força ao teste seguinte.

    Se a definição estivesse no corpo normativo indexado, a resposta correta não provaria
    nada sobre o dicionário. Ela não está: o corpus da Sprint 17 fala da RCL do art. 2º,
    IV — a distinção da ajustada é justamente a que a plataforma aprendeu na Sprint 28.
    """
    with admin_session() as s:
        textos = list(s.scalars(select(NormaChunk.texto)))
    assert textos, "corpo normativo vazio"
    assert not [t for t in textos if "ajustada" in t.lower()]


def test_verbetes_relevantes_entram_no_contexto_da_pergunta(cenario: Cenario) -> None:
    """A recuperação escolhe o verbete certo pelo denominador, não por sorte."""
    with admin_session() as s:
        dictionary.garantir_seed(s)
        s.commit()
        verbetes = retriever.retrieve_verbetes(
            s, pergunta=PERGUNTA_RCL_AJUSTADA, codigos=set()
        )
        s.commit()

    codigos = [v.codigo for v in verbetes]
    assert "pessoal_executivo" in codigos
    assert len(codigos) <= dictionary.MAX_VERBETES_POR_PERGUNTA
    pessoal = next(v for v in verbetes if v.codigo == "pessoal_executivo")
    assert pessoal.denominador == "rcl_ajustada"
    assert "166-A" in pessoal.denominador_definicao


def test_pergunta_sobre_rcl_ajustada_e_respondida_pelo_dicionario(
    client, make_org, cenario: Cenario
) -> None:
    """Critério de aceite 3, com o provedor determinístico local.

    ``LocalGroundedProvider`` é extrativo: não tem conhecimento nenhum, só costura o que
    recebeu. Se a resposta explica a RCL Ajustada e o porquê de ela ser o denominador do
    art. 20, a explicação só pode ter vindo do contexto — e o teste anterior mostra que
    não veio do corpo normativo.
    """
    org = make_org(entes=[cenario.ente])
    headers = auth_header(login(client, org.email, org.senha))
    app.dependency_overrides[get_llm_provider] = LocalGroundedProvider
    try:
        resposta = client.post(
            "/assistant/perguntar",
            headers=headers,
            json={"ente": cenario.ente, "pergunta": PERGUNTA_RCL_AJUSTADA},
        )
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["recusa"] is False
    texto = corpo["resposta"]
    baixo = texto.lower()
    assert "rcl ajustada" in baixo, texto
    assert "emenda" in baixo, "a dedução que define a ajustada não foi explicada"
    assert "166-a" in baixo or "105/2019" in baixo, "faltou o fundamento constitucional"
    assert "art. 20" in baixo, "faltou o dispositivo do limite de pessoal"
    # E o número continua vindo do dado, com fonte — o dicionário não trouxe valor nenhum.
    assert "48,00%" in texto
    assert any("pessoal" in chip["rotulo"].lower() for chip in corpo["fontes"]), corpo["fontes"]


def test_o_contexto_enviado_ao_modelo_carrega_a_definicao(
    client, make_org, cenario: Cenario
) -> None:
    """O mesmo, olhando o outro lado: o que o provedor recebe já contém a definição."""
    org = make_org(entes=[cenario.ente])
    headers = auth_header(login(client, org.email, org.senha))
    provider = FakeProvider()
    app.dependency_overrides[get_llm_provider] = lambda: provider
    try:
        resposta = client.post(
            "/assistant/perguntar",
            headers=headers,
            json={"ente": cenario.ente, "pergunta": PERGUNTA_RCL_AJUSTADA},
        )
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)

    assert resposta.status_code == 200, resposta.text
    assert provider.calls, "o modelo não foi chamado"
    request = provider.calls[-1]
    codigos = {v.codigo for v in request.verbetes}
    assert "pessoal_executivo" in codigos
    bloco = render_grounding(request)
    assert "DICIONÁRIO DA PLATAFORMA" in bloco
    assert "prevalecem sobre conhecimento geral" in bloco
    assert "Denominador (rcl_ajustada)" in bloco
    assert "revisada em" in bloco, "a data da definição precisa viajar com ela"


def test_dicionario_nao_conta_como_fundamento_para_responder(
    client, make_org, monkeypatch, cenario: Cenario
) -> None:
    """G3 preservado: verbete explica, não mede — sem dado e sem norma, a recusa fica.

    É a regressão que o dicionário poderia causar sem ninguém notar: como ele está sempre
    disponível, tratá-lo como contexto fundamentado esvaziaria a recusa honesta da Sprint
    17 — o assistente passaria a "responder" toda pergunta, sem nenhum número atrás.
    """
    provider = FakeProvider()
    app.dependency_overrides[get_llm_provider] = lambda: provider
    try:
        monkeypatch.setattr(retriever, "retrieve_normas", lambda *a, **k: [])
        ente_vazio = _cod_ente()
        org = make_org(entes=[ente_vazio])
        headers = auth_header(login(client, org.email, org.senha))
        resposta = client.post(
            "/assistant/perguntar",
            headers=headers,
            json={"ente": ente_vazio, "pergunta": PERGUNTA_RCL_AJUSTADA},
        )
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["recusa"] is True
    assert corpo["dado_disponivel"] is False
    assert provider.calls == [], "o modelo foi chamado só com definições, sem fundamento"
