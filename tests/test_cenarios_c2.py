"""Sprint C2 — cenário salvo que sobrevive à edição e sabe sobre qual dado foi calculado.

Um cenário guardava as premissas e o resultado do momento. Duas coisas falhavam, e as duas
em silêncio:

* **editar destruía.** Ajustar uma premissa sobrescrevia o registro. Cenário é peça de
  decisão: "o que eu levei à reunião de agosto" precisa sobreviver ao ajuste de outubro;
* **reabrir mentia por omissão.** O resultado congelado aparecia com a mesma cara de um
  cálculo corrente. Se o ente entregou RGF novo no intervalo, as mesmas premissas dão outro
  número hoje — e a tela não tinha como dizer qual dos dois estava mostrando.

Os testes abaixo usam o banco real e criam a própria organização, porque a RLS de
``op.cenario`` é por ``org_id`` e testar isolamento com dados emprestados de outro teste
não prova isolamento nenhum.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.db import admin_session
from app.core.deps import Principal
from app.core.errors import AppError
from app.modules.forecast import cenarios, repository
from app.modules.forecast.schemas import CenarioSimularRequest

ENTE = "2304400"
INDICADOR = "pessoal"


@pytest.fixture
def org() -> uuid.UUID:
    """Organização e usuário descartáveis.

    A RLS de ``op.cenario`` é por ``org_id``, e ``criado_por`` tem chave estrangeira para
    ``op.usuario``: testar com identidades emprestadas de outro teste não prova isolamento
    e ainda acopla a suíte ao estado do banco.
    """
    org_id = uuid.uuid4()
    usuario_id = uuid.uuid4()
    with admin_session() as session:
        session.execute(
            text(
                "insert into op.organizacao (id, nome, tipo_conta, criada_em) "
                "values (:i, :n, 'prefeitura', now())"
            ),
            {"i": org_id, "n": f"Teste C2 {org_id.hex[:8]}"},
        )
        session.execute(
            text(
                "insert into op.usuario (id, email, nome, senha_hash, mfa_ativo, is_superuser) "
                "values (:i, :e, :n, 'x', false, false)"
            ),
            {"i": usuario_id, "e": f"c2-{usuario_id.hex[:10]}@teste.invalid", "n": "Teste C2"},
        )
        session.commit()
    yield org_id, usuario_id
    with admin_session() as session:
        session.execute(text("delete from op.organizacao where id = :i"), {"i": org_id})
        session.execute(text("delete from op.usuario where id = :i"), {"i": usuario_id})
        session.commit()


@pytest.fixture
def principal(org: tuple[uuid.UUID, uuid.UUID]) -> Principal:
    org_id, usuario_id = org
    return Principal(
        usuario_id=usuario_id,
        org_id=org_id,
        papel="admin",
        capacidades=frozenset({"ver", "editar"}),
        escopo_ibges=None,
    )


def _req(nome: str = "Cenário de teste", **extra) -> CenarioSimularRequest:
    return CenarioSimularRequest(nome=nome, horizonte=4, salvar=True, **extra)


def _resultado(valor_final: float) -> dict:
    return {
        "projecao": [
            {
                "periodo_alvo": "2026-B2",
                "passo": 1,
                "valor_previsto": str(valor_final),
                "ic_inferior": str(valor_final - 2),
                "ic_superior": str(valor_final + 2),
                "cruza_limite": False,
            }
        ],
        "cruzamento": {"aplicavel": True, "cruza": False},
    }


# --------------------------------------------------------------------------------------
# Versionamento: editar cria versão, não destrói
# --------------------------------------------------------------------------------------
def test_salvar_duas_vezes_cria_duas_versoes(principal: Principal) -> None:
    """A versão 1 continua consultável depois da 2 — é o registro do que foi decidido."""
    with admin_session() as session:
        cenario, v1 = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req(ipca_aa_pct=4.5), resultado=_resultado(47.0), modelo="holt_winters",
        )
        _, v2 = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req(ipca_aa_pct=9.0, cenario_id=str(cenario.id)),
            resultado=_resultado(51.0), modelo="holt_winters", cenario_id=cenario.id,
        )
        session.commit()
        versoes = repository.list_versoes(
            session, org_id=principal.org_id, cenario_id=cenario.id
        )

    assert (v1.versao, v2.versao) == (1, 2)
    assert len(versoes) == 2
    # A premissa original sobreviveu à edição.
    antiga = next(v for v in versoes if v.versao == 1)
    assert antiga.parametros["ipca_aa_pct"] == 4.5


def test_versao_grava_a_procedencia_do_dado(principal: Principal) -> None:
    """Sem saber sobre qual entrega o cálculo rodou, reproduzir e recalcular viram a mesma
    tela — e a diferença entre as duas é o ponto inteiro desta sprint."""
    with admin_session() as session:
        _, versao = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req(), resultado=_resultado(47.0), modelo="fechamento",
        )
        session.commit()
        assert versao.as_of is not None, "sem as_of a versão não é reproduzível"
        assert versao.versoes_entrega, "a impressão digital do dado tem de ser gravada"
        # A premissa observada à época: "aceitei o IPCA observado" muda de significado
        # quando o observado muda.
        assert "ipca_aa_pct" in (versao.premissas_observadas or {})


def test_renomear_nao_reescreve_o_nome_das_versoes(principal: Principal) -> None:
    """O nome de cada versão é o que ela tinha quando foi salva.

    Reescrevê-lo apagaria o rastro de que o cenário se chamava outra coisa quando embasou
    a decisão — que é justamente o que um histórico existe para preservar.
    """
    with admin_session() as session:
        cenario, _ = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req("Nome original"), resultado=_resultado(47.0), modelo="fechamento",
        )
        session.commit()
        detalhe = cenarios.renomear(
            session, principal, cenario_id=cenario.id, nome="Nome novo"
        )
        session.commit()

    assert detalhe.nome == "Nome novo"
    assert detalhe.versoes[0].nome == "Nome original"


# --------------------------------------------------------------------------------------
# Arquivar em vez de apagar
# --------------------------------------------------------------------------------------
def test_arquivar_tira_da_lista_sem_apagar(principal: Principal) -> None:
    """Apagar removeria a evidência de uma decisão porque alguém quis limpar a tela."""
    with admin_session() as session:
        cenario, _ = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req(), resultado=_resultado(47.0), modelo="fechamento",
        )
        session.commit()
        cenarios.arquivar(session, principal, cenario_id=cenario.id)
        session.commit()

        visiveis = cenarios.listar(
            session, principal, cod_ibge=ENTE, incluir_arquivados=False
        )
        todos = cenarios.listar(session, principal, cod_ibge=ENTE, incluir_arquivados=True)

    assert not any(c.id == str(cenario.id) for c in visiveis)
    assert any(c.id == str(cenario.id) and c.arquivado for c in todos)


def test_desarquivar_traz_de_volta(principal: Principal) -> None:
    with admin_session() as session:
        cenario, _ = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req(), resultado=_resultado(47.0), modelo="fechamento",
        )
        session.commit()
        cenarios.arquivar(session, principal, cenario_id=cenario.id)
        detalhe = cenarios.arquivar(
            session, principal, cenario_id=cenario.id, desarquivar=True
        )
        session.commit()
    assert not detalhe.arquivado


# --------------------------------------------------------------------------------------
# Reabrir: o guardado e o de hoje, lado a lado
# --------------------------------------------------------------------------------------
def test_reabrir_traz_guardado_e_recalculado(principal: Principal) -> None:
    with admin_session() as session:
        cenario, _ = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req(), resultado=_resultado(47.0), modelo="fechamento",
        )
        session.commit()
        aberto = cenarios.abrir(session, principal, cenario_id=cenario.id)

    assert aberto.guardado is not None, "o que embasou a decisão continua à vista"
    assert aberto.recalculado is not None, "e o que o dado diz hoje, ao lado"
    assert aberto.divergencia.comparavel


def test_divergencia_distingue_incomparavel_de_igual(principal: Principal) -> None:
    """``diverge=False`` com ``comparavel=False`` não é "está tudo igual": é "não deu para
    comparar". Colapsar os dois num booleano só faria uma versão sem procedência aparecer
    como conferida."""
    with admin_session() as session:
        cenario, versao = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req(), resultado=_resultado(47.0), modelo="fechamento",
        )
        # Simula uma versão migrada do formato antigo: sem `as_of`.
        session.execute(
            text("update op.cenario_versao set as_of = null where id = :i"),
            {"i": versao.id},
        )
        session.commit()

    # **Sessão nova de propósito.** `SessionLocal` usa `expire_on_commit=False`, então a
    # sessão original devolveria o objeto do identity map com o valor de antes do update —
    # e o teste passaria por acidente, verificando o que estava em memória em vez do que
    # está no banco. Uma requisição HTTP real abre a própria sessão.
    with admin_session() as session:
        aberto = cenarios.abrir(session, principal, cenario_id=cenario.id)

    assert not aberto.divergencia.comparavel
    assert not aberto.divergencia.diverge
    assert aberto.divergencia.motivo and "procedência" in aberto.divergencia.motivo
    assert not aberto.versao.procedencia.registrada


def test_entrega_nova_aparece_como_causa_da_divergencia(principal: Principal) -> None:
    """Dizer *o que* mudou é o que transforma "o número está diferente" em informação."""
    with admin_session() as session:
        cenario, versao = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req(), resultado=_resultado(47.0), modelo="fechamento",
        )
        # Finge que o cenário foi salvo quando a série tinha um período a menos.
        entregas = dict(versao.versoes_entrega or {})
        if entregas:
            entregas.pop(max(entregas))
        session.execute(
            text("update op.cenario_versao set versoes_entrega = cast(:v as jsonb) where id = :i"),
            {"v": json.dumps(entregas), "i": versao.id},
        )
        session.commit()

    with admin_session() as session:  # sessão nova: ver a nota do teste anterior
        aberto = cenarios.abrir(session, principal, cenario_id=cenario.id)

    assert aberto.divergencia.entregas_novas, "a entrega que apareceu depois tem de ser dita"


def test_reabrir_sem_recalcular_ainda_devolve_o_guardado(principal: Principal) -> None:
    """O registro da decisão vale por si; o recálculo é opcional."""
    with admin_session() as session:
        cenario, _ = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req(), resultado=_resultado(47.0), modelo="fechamento",
        )
        session.commit()
        aberto = cenarios.abrir(
            session, principal, cenario_id=cenario.id, recalcular=False
        )
    assert aberto.guardado is not None
    assert aberto.recalculado is None


def test_versao_inexistente_e_404_e_nao_a_ultima(principal: Principal) -> None:
    """Devolver a última no lugar da pedida faria o gestor auditar a versão errada."""
    with admin_session() as session:
        cenario, _ = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req(), resultado=_resultado(47.0), modelo="fechamento",
        )
        session.commit()
        with pytest.raises(AppError) as erro:
            cenarios.abrir(session, principal, cenario_id=cenario.id, versao=99)
    assert erro.value.status == 404


# --------------------------------------------------------------------------------------
# Comparação
# --------------------------------------------------------------------------------------
def test_comparar_usa_a_intersecao_dos_horizontes(principal: Principal) -> None:
    """Comparar um cenário de 1 período com um de 2 num eixo de 2 deixaria o primeiro
    terminando no meio do gráfico — e "este cenário despenca no fim" seria falso."""
    curto = _resultado(47.0)
    longo = {
        "projecao": [
            *curto["projecao"],
            {
                "periodo_alvo": "2026-B4",
                "passo": 2,
                "valor_previsto": "48.0",
                "ic_inferior": "46.0",
                "ic_superior": "50.0",
                "cruza_limite": False,
            },
        ],
        "cruzamento": {"aplicavel": True, "cruza": False},
    }
    with admin_session() as session:
        a, _ = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req("Curto"), resultado=curto, modelo="fechamento",
        )
        b, _ = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req("Longo"), resultado=longo, modelo="fechamento",
        )
        session.commit()
        comp = cenarios.comparar(
            session, principal, cod_ibge=ENTE, cenario_ids=[a.id, b.id]
        )

    assert comp.periodos == ["2026-B2"]
    assert all(len(i.projecao) == 1 for i in comp.itens if i.encontrado)


def test_cenario_pedido_e_nao_encontrado_entra_na_resposta(principal: Principal) -> None:
    """Sumir da lista faria o gestor comparar duas curvas achando que são as três que
    escolheu."""
    with admin_session() as session:
        a, _ = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req("A"), resultado=_resultado(47.0), modelo="fechamento",
        )
        b, _ = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req("B"), resultado=_resultado(49.0), modelo="fechamento",
        )
        session.commit()
        fantasma = uuid.uuid4()
        comp = cenarios.comparar(
            session, principal, cod_ibge=ENTE, cenario_ids=[a.id, b.id, fantasma]
        )

    assert len(comp.itens) == 3
    ausente = next(i for i in comp.itens if i.cenario_id == str(fantasma))
    assert not ausente.encontrado and ausente.motivo_ausencia


def test_comparar_indicadores_diferentes_avisa(principal: Principal) -> None:
    """Sobrepor % da RCL e reais no mesmo eixo dá um gráfico bonito e sem significado."""
    with admin_session() as session:
        a, _ = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador="pessoal",
            req=_req("Pessoal"), resultado=_resultado(47.0), modelo="fechamento",
        )
        b, _ = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador="rcl",
            req=_req("RCL"), resultado=_resultado(47.0), modelo="fechamento",
        )
        session.commit()
        comp = cenarios.comparar(
            session, principal, cod_ibge=ENTE, cenario_ids=[a.id, b.id]
        )
    assert comp.aviso and "escalas" in comp.aviso
    assert comp.indicador is None


# --------------------------------------------------------------------------------------
# Isolamento entre organizações
# --------------------------------------------------------------------------------------
def test_cenario_de_outra_organizacao_nao_e_visivel(principal: Principal) -> None:
    """O caso que a RLS existe para impedir, testado por dado e não por confiança."""
    with admin_session() as session:
        cenario, _ = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req(), resultado=_resultado(47.0), modelo="fechamento",
        )
        session.commit()

    intruso = Principal(
        usuario_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        papel="admin",
        capacidades=frozenset({"ver"}),
        escopo_ibges=None,
    )
    with admin_session() as session, pytest.raises(AppError) as erro:
        cenarios.abrir(session, intruso, cenario_id=cenario.id)
    assert erro.value.status == 404, "existência de cenário alheio não deve vazar por 403"


def test_sem_organizacao_nao_opera_cenario() -> None:
    anonimo = Principal(
        usuario_id=uuid.uuid4(),
        org_id=None,
        papel=None,
        capacidades=frozenset({"ver"}),
        escopo_ibges=None,
    )
    with admin_session() as session, pytest.raises(AppError) as erro:
        cenarios.listar(session, anonimo, cod_ibge=ENTE, incluir_arquivados=False)
    assert erro.value.status == 403


# --------------------------------------------------------------------------------------
# Exportação
# --------------------------------------------------------------------------------------
def test_export_csv_leva_premissas_e_procedencia(principal: Principal) -> None:
    """Exportar só a curva produz um arquivo que ninguém audita seis meses depois."""
    with admin_session() as session:
        cenario, _ = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req("Reajuste 6%", ipca_aa_pct=4.54), resultado=_resultado(47.0),
            modelo="holt_winters",
        )
        session.commit()
        conteudo, tipo, nome = cenarios.exportar(
            session, principal, cenario_id=cenario.id, versao=None, formato="csv"
        )

    texto = conteudo.decode("utf-8-sig")
    assert "text/csv" in tipo and nome.endswith(".csv")
    assert "# premissa ipca_aa_pct" in texto
    assert "# entregas usadas" in texto
    assert "# calculado em" in texto
    assert "periodo;valor_previsto" in texto
    assert "2026-B2" in texto


def test_export_json_carrega_a_procedencia_estruturada(principal: Principal) -> None:
    import json

    with admin_session() as session:
        cenario, _ = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req(), resultado=_resultado(47.0), modelo="fechamento",
        )
        session.commit()
        conteudo, tipo, nome = cenarios.exportar(
            session, principal, cenario_id=cenario.id, versao=None, formato="json"
        )

    corpo = json.loads(conteudo)
    assert "application/json" in tipo and nome.endswith(".json")
    assert corpo["procedencia"]["registrada"] is True
    assert corpo["premissas"] and corpo["projecao"]


def test_export_de_versao_antiga_traz_a_versao_antiga(principal: Principal) -> None:
    """Exportar sempre a última tornaria o histórico decorativo."""
    with admin_session() as session:
        cenario, _ = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req(ipca_aa_pct=4.0), resultado=_resultado(47.0), modelo="fechamento",
        )
        cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req(ipca_aa_pct=9.0), resultado=_resultado(52.0), modelo="fechamento",
            cenario_id=cenario.id,
        )
        session.commit()
        conteudo, _, nome = cenarios.exportar(
            session, principal, cenario_id=cenario.id, versao=1, formato="csv"
        )

    texto = conteudo.decode("utf-8-sig")
    assert "v1" in nome
    assert "# premissa ipca_aa_pct;4.0" in texto


# --------------------------------------------------------------------------------------
# Tolerância da divergência
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("delta", "esperado"),
    [(Decimal("0.001"), False), (Decimal("0.01"), True), (Decimal("-0.5"), True)],
)
def test_tolerancia_separa_ruido_de_mudanca(delta: Decimal, esperado: bool) -> None:
    """Dois números que arredondam igual na tela não são uma divergência."""
    assert (abs(delta) > cenarios.TOLERANCIA_DIVERGENCIA) is esperado


def test_data_de_atualizacao_ordena_a_lista(principal: Principal) -> None:
    """Quem tem muitos cenários procura o que mexeu por último, não o que criou primeiro."""
    with admin_session() as session:
        antigo, _ = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req("Antigo"), resultado=_resultado(47.0), modelo="fechamento",
        )
        novo, _ = cenarios.salvar(
            session, principal, cod_ibge=ENTE, indicador=INDICADOR,
            req=_req("Novo"), resultado=_resultado(48.0), modelo="fechamento",
        )
        session.execute(
            text("update op.cenario set atualizado_em = :t where id = :i"),
            {"t": datetime.now(UTC) + timedelta(hours=1), "i": antigo.id},
        )
        session.commit()
        lista = cenarios.listar(
            session, principal, cod_ibge=ENTE, incluir_arquivados=False
        )
    ids = [c.id for c in lista]
    assert ids.index(str(antigo.id)) < ids.index(str(novo.id))
