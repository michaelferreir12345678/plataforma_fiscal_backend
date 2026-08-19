"""Sprint Q1 — o fluxo de resolução de uma verificação em falha.

O que se prova aqui não é que os botões existem: é que **a ação oferecida corresponde a
quem é o dono do número**. Oferecer "reprocessar" numa divergência da fonte gasta o tempo
do gestor, não muda o resultado e ensina a desconfiar do botão — e um botão em que ninguém
confia é pior que botão nenhum.

Por isso quase todo teste aqui vem em par: um mostra o que o fluxo permite, o outro mostra
o que ele **recusa**.
"""

# ruff: noqa: F811 — fixtures importadas reaparecem como argumentos dos testes
from __future__ import annotations

import inspect
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import delete

from app.core.db import admin_session
from app.core.deps import Principal
from app.core.errors import AppError
from app.modules.catalog.models import DimEnte
from app.modules.dashboard import cockpit_service
from app.modules.indicators.models import MartIndicador
from app.modules.quality import causa as causa_mod
from app.modules.quality import checks as checks_mod
from app.modules.quality import resolucao
from app.modules.quality import service as quality_service
from app.modules.quality.causa import ACOES_POR_CLASSE, causa_do_check
from app.modules.quality.checks import SLAS
from app.modules.quality.models import DataQualityCheck, QualidadeTratativa
from tests.test_ia_tooling import PERIODO as PERIODO_CENARIO
from tests.test_ia_tooling import cenario  # noqa: F401 — ente sintético reusado
from tests.test_sprint26_qualidade_lineage import PERIODO


# --------------------------------------------------------------------------- #
# 1. A classificação: de quem é o número que não fechou
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("check_codigo", "classe"),
    [
        # Dois lados nossos ⇒ defeito nosso, há o que corrigir.
        ("mart_vs_detalhe_pessoal", "plataforma"),
        ("minimo_saude_recalculado", "plataforma"),
        ("minimo_educacao_recalculado", "plataforma"),
        # Dois lados do ente ⇒ a publicação é que está inconsistente.
        ("dcl_a6_vs_rgf", "fonte"),
        ("receita_soma_filhos", "fonte"),
        ("despesa_estagios_monotonicos", "fonte"),
        ("msc_vs_dca", "fonte"),
        # Um de cada ⇒ descarta-se a hipótese nossa primeiro.
        ("rcl_calculada_vs_publicada", "misto"),
        # Defasagem não diz de quem é a falta.
        ("freshness_rreo", "cobertura"),
        ("freshness_rgf", "cobertura"),
        ("freshness_dca", "cobertura"),
        ("freshness_msc", "cobertura"),
    ],
)
def test_classe_sai_do_que_o_check_compara(check_codigo: str, classe: str) -> None:
    assert causa_do_check(check_codigo).classe == classe


def test_todo_check_do_catalogo_esta_classificado() -> None:
    """Um check sem classe cairia no default e nunca ofereceria a ação certa.

    O teste amarra o catálogo real (``SLAS`` + ``CAUSA_POR_CHECK``) à classificação: um
    check novo que entre no produto sem passar por aqui é pego na suíte, não em produção.
    """
    do_catalogo = set(causa_mod.CAUSA_POR_CHECK) | {
        f"freshness_{s.relatorio.lower()}" for s in SLAS
    }
    for codigo in do_catalogo:
        assert causa_do_check(codigo).classe in {"plataforma", "fonte", "misto", "cobertura"}


def test_check_desconhecido_nao_acusa_a_fonte() -> None:
    """Default conservador: sem classificação, a hipótese nossa se descarta primeiro.

    O contrário seria pior — um check novo começaria dizendo ao gestor que o ente publicou
    errado, com base em nada.
    """
    causa = causa_do_check("check_que_ainda_nao_existe")
    assert causa.classe == "misto"
    assert "rematerializar" in ACOES_POR_CLASSE[causa.classe]


# --------------------------------------------------------------------------- #
# 2. A ação cabível — e, sobretudo, a que NÃO cabe
# --------------------------------------------------------------------------- #
def test_falha_da_fonte_nao_oferece_reprocessamento() -> None:
    """O ponto central da sprint.

    ``dcl_a6_vs_rgf`` compara dois demonstrativos que o **ente** publicou. Rematerializar
    lê os mesmos dois números e chega ao mesmo resultado: a ação não existe para essa
    classe, e oferecê-la seria uma promessa que a plataforma não pode cumprir.
    """
    acoes = ACOES_POR_CLASSE[causa_do_check("dcl_a6_vs_rgf").classe]
    assert "rematerializar" not in acoes
    assert acoes == ("aceitar_como_fato",)


def test_falha_da_plataforma_oferece_reprocessamento_e_nao_aceite() -> None:
    """Controle negativo do teste acima: onde o defeito é nosso, aceitar não é opção.

    Se ``aceitar_como_fato`` aparecesse aqui, o fluxo permitiria arquivar como "fato da
    fonte" uma divergência entre dois números que a própria plataforma produziu — que é
    exatamente o modo de esconder um defeito nosso com aparência de processo.
    """
    acoes = ACOES_POR_CLASSE[causa_do_check("mart_vs_detalhe_pessoal").classe]
    assert acoes == ("rematerializar",)
    assert "aceitar_como_fato" not in acoes


def test_defasagem_nao_promete_reingestao_antes_de_olhar_a_fonte() -> None:
    """A classe ``cobertura`` não tem ação fixa: ela depende do diagnóstico.

    "O RGF está com 80 dias" pode ser o ente que não publicou ou nós que não ingerimos.
    Oferecer "reingerir" antes de saber seria prometer que existe o que ingerir.
    """
    assert ACOES_POR_CLASSE[causa_do_check("freshness_rgf").classe] == ()


# --------------------------------------------------------------------------- #
# 3. A régua não pode carregar o vício que existe para achar
#
# Achado por uso, e é o pior tipo de defeito num sistema de verificação: o check
# `mart_vs_detalhe_pessoal` dividia pela RCL **cheia**, enquanto o indicador (corrigido na
# Sprint 28, migration 0035) divide pela RCL **Ajustada**. A régua acusava de errado
# exatamente o valor correto — 8 falhas em produção, todas falso positivo, e o painel de
# resolução mandando reprocessar dado que já estava certo.
#
# Medido no ente 23 em 2026-B2:
#   R$ 16.679.957.857,72 ÷ RCL Ajustada R$ 40.690.096.057,23 = 40,9927%  (o mart)
#   R$ 16.679.957.857,72 ÷ RCL cheia    R$ 40.899.706.794,11 = 40,7826%  (o check errado)
# --------------------------------------------------------------------------- #
def test_check_de_pessoal_usa_a_rcl_ajustada_e_nao_a_cheia() -> None:
    """O denominador do check tem de ser o mesmo do indicador — fonte única.

    O teste lê o código: chamar ``endividamento.rcl_ajustada`` é o que garante que uma
    mudança futura na regra do denominador chegue aos dois lados juntos. Foi a divergência
    entre eles que produziu o falso positivo.
    """
    fonte = inspect.getsource(checks_mod.mart_vs_detalhe_pessoal)
    # Sem a docstring: ela cita `rcl_12m` de propósito, ao contar por que o denominador
    # errado esteve ali. Documentar o defeito não pode reprovar a correção.
    doc = checks_mod.mart_vs_detalhe_pessoal.__doc__ or ""
    corpo = fonte.replace(doc, "")
    assert "rcl_ajustada" in corpo, "o check tem de usar a RCL Ajustada"
    assert "rcl_12m" not in corpo, (
        "a RCL cheia é o denominador errado destes limites (CLAUDE.md §2, Sprint 28)"
    )


def test_reconciliacao_de_pessoal_bate_quando_o_denominador_e_o_mesmo() -> None:
    """Com o denominador certo, os números reais de produção fecham dentro da tolerância."""
    despesa = Decimal("16679957857.72")
    rcl_ajustada = Decimal("40690096057.23")
    rcl_cheia = Decimal("40899706794.11")
    mart_gravado = Decimal("40.99267260087047587826243186")

    com_ajustada = despesa / rcl_ajustada * Decimal(100)
    com_cheia = despesa / rcl_cheia * Decimal(100)

    assert abs(com_ajustada - mart_gravado) < Decimal("0.01")
    # E o controle negativo: com a RCL cheia a diferença estoura a tolerância — que é
    # exatamente a falha que o gestor via na tela.
    assert abs(com_cheia - mart_gravado) > Decimal("0.01")


def test_lados_do_check_de_pessoal_estao_rotulados_na_ordem_certa() -> None:
    """O rótulo tem de dizer de onde veio cada número.

    Estavam trocados: o painel mostrava "semáforo: 40,78 / detalhe: 40,99" quando é o
    inverso. Atribuir o número à origem errada é pior que não mostrar origem nenhuma —
    manda o gestor investigar o lado que não tem problema.
    """
    causa = causa_do_check("mart_vs_detalhe_pessoal")
    # O check passa `esquerda=recalculado` e `direita=mart` (ver checks.py).
    assert "detalhe" in causa.esquerda
    assert "mart_indicador" in causa.direita


# --------------------------------------------------------------------------- #
# 4. Consulta à fonte: o passo que transforma dúvida em fato
#
# Antes, `verificar_na_fonte` não consultava nada — na tela era um botão que não
# respondia. Agora ele pergunta ao SICONFI pelo período que já deveria estar publicado.
#
# O risco de errar em silêncio aqui é específico e grave: tratar **falha de rede** como
# "o ente não publicou". Seria o mesmo vício que a `cobertura_do_ente` existe para
# evitar — culpar o ente por uma lacuna que pode ser nossa. Por isso o par de testes.
# --------------------------------------------------------------------------- #
def test_proximo_periodo_avanca_dentro_do_ano_e_vira_o_ano() -> None:
    sla_rreo = next(s for s in SLAS if s.relatorio == "RREO")  # 6 bimestres
    assert resolucao._proximo_periodo_esperado(sla_rreo, "2025-B3") == (2025, 4)
    # Último período do ano ⇒ o próximo é o primeiro do ano seguinte.
    assert resolucao._proximo_periodo_esperado(sla_rreo, "2025-B6") == (2026, 1)

    sla_dca = next(s for s in SLAS if s.relatorio == "DCA")  # anual
    assert resolucao._proximo_periodo_esperado(sla_dca, "2024") == (2025, 1)


def test_sem_entrega_nenhuma_nao_ha_proximo_periodo() -> None:
    """Ente sem nada ingerido é caso de carga inicial, não de defasagem.

    Chutar "o próximo é o do ano corrente" mandaria consultar um período que talvez nunca
    devesse existir para aquele ente.
    """
    sla = next(s for s in SLAS if s.relatorio == "RREO")
    assert resolucao._proximo_periodo_esperado(sla, None) is None


def test_falha_de_rede_nao_vira_acusacao_ao_ente(monkeypatch: pytest.MonkeyPatch) -> None:
    """Indisponibilidade nossa é `indeterminado`, nunca `fonte_nao_tem`.

    Este é o controle que importa: se um timeout virasse "o ente não publicou", a
    plataforma passaria a atribuir ao gestor uma falta que é da nossa consulta — e ele
    aceitaria como fato uma conclusão que nunca foi apurada.
    """
    class ConectorQueFalha:
        def __init__(self, *_a: object, **_k: object) -> None: ...
        def build_job(self, *_a: object, **_k: object) -> object:
            return object()
        def extract(self, _job: object) -> object:
            raise TimeoutError("a fonte não respondeu")

    monkeypatch.setattr(
        "app.modules.ingestion.connectors.registry.CONNECTOR_REGISTRY",
        {"siconfi_rreo": ConectorQueFalha},
    )
    monkeypatch.setattr(
        "app.shared.ingestion.client.RealClientResolver",
        lambda: SimpleNamespace(get=lambda _f: object()),
    )

    check = SimpleNamespace(
        check_codigo="freshness_rreo", cod_ibge="2304400", periodo="2025-B6",
        esquerda=None, tolerancia=None, detalhe={},
    )
    sessao = SimpleNamespace(
        scalar=lambda _stmt: SimpleNamespace(periodo="2025-B6", versao_entrega="1")
    )

    resultado = resolucao._verificar_na_fonte(sessao, check)  # type: ignore[arg-type]
    assert resultado["consultado"] is False
    assert resultado["resultado"] == "indeterminado"
    assert "não completou" in resultado["motivo"]


# --------------------------------------------------------------------------- #
# 5. Escalonamento: a ação que não resolveu não volta como botão
# --------------------------------------------------------------------------- #
def test_reprocessamento_que_falhou_e_reconhecido() -> None:
    tratativa = SimpleNamespace(
        tentativas=[{"acao": "rematerializar", "status_apos": "falha"}]
    )
    assert resolucao._reprocessamento_ja_falhou(tratativa) is True  # type: ignore[arg-type]


def test_reprocessamento_bem_sucedido_nao_escala() -> None:
    """Controle negativo: reprocessar e voltar a `ok` não é caso de escalonamento."""
    tratativa = SimpleNamespace(
        tentativas=[{"acao": "rematerializar", "status_apos": "ok"}]
    )
    assert resolucao._reprocessamento_ja_falhou(tratativa) is False  # type: ignore[arg-type]

    # E outra ação qualquer também não: só o reprocessamento descarta a hipótese de
    # materialização vencida.
    outra = SimpleNamespace(tentativas=[{"acao": "verificar_na_fonte", "resultado": "x"}])
    assert resolucao._reprocessamento_ja_falhou(outra) is False  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 6. Reingerir: só depois de a fonte confirmar
#
# É a perna que fecha o fluxo — a que de fato faz a falha sumir, ingerindo o que falta.
# E é onde um atalho custaria caro: enfileirar sem confirmação manda o worker buscar o que
# talvez não exista e depois registrar como "ausente" um dado que ninguém deveria ter.
# --------------------------------------------------------------------------- #
def test_reingerir_exige_confirmacao_previa_da_fonte() -> None:
    """Sem `fonte_tem` no diagnóstico, a ação é recusada com instrução do que fazer."""
    tratativa = SimpleNamespace(diagnostico={"origem_indeterminada": True}, tentativas=[])
    check = SimpleNamespace(check_codigo="freshness_rreo", cod_ibge="2304400", periodo="2025-B6")

    with pytest.raises(AppError) as exc:
        resolucao._reingerir(
            SimpleNamespace(),  # type: ignore[arg-type]
            SimpleNamespace(capacidades=frozenset({"administrar"})),  # type: ignore[arg-type]
            check,  # type: ignore[arg-type]
            tratativa,  # type: ignore[arg-type]
        )
    assert exc.value.status == 422
    assert "Verificar na fonte" in (exc.value.detail or "")


def test_reingerir_usa_o_periodo_que_a_fonte_confirmou(monkeypatch: pytest.MonkeyPatch) -> None:
    """O período vem do diagnóstico, não de um palpite sobre o calendário."""
    capturado: dict[str, object] = {}

    class JobFalso:
        id = uuid.UUID("11111111-2222-3333-4444-555555555555")

    def criar_job_falso(_s: object, _p: object, create: object) -> object:
        capturado["fonte"] = create.fonte  # type: ignore[attr-defined]
        capturado["entes"] = create.entes  # type: ignore[attr-defined]
        capturado["anos"] = create.anos  # type: ignore[attr-defined]
        capturado["parametros"] = create.parametros  # type: ignore[attr-defined]
        return SimpleNamespace(job=JobFalso(), estimativa_itens=1, limiar=100)

    monkeypatch.setattr(
        "app.modules.ingestion.jobs_service.criar_job", criar_job_falso
    )

    tratativa = SimpleNamespace(
        diagnostico={"resultado": "fonte_tem", "periodo_conferido": "2026-1"},
        tentativas=[],
    )
    check = SimpleNamespace(check_codigo="freshness_rreo", cod_ibge="2304400", periodo="2025-B6")

    resultado = resolucao._reingerir(
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(capacidades=frozenset({"administrar"})),  # type: ignore[arg-type]
        check,  # type: ignore[arg-type]
        tratativa,  # type: ignore[arg-type]
    )

    assert capturado["fonte"] == "siconfi_rreo"
    assert capturado["entes"] == ["2304400"]
    assert capturado["anos"] == [2026]
    assert capturado["parametros"] == {"periodos": [1]}
    # Assíncrono: a resposta não promete que resolveu, só que enfileirou.
    assert resultado["assincrono"] is True
    assert resultado["job_id"] == "11111111-2222-3333-4444-555555555555"


def test_reingerir_nao_registra_enfileiramento_que_nao_aconteceu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Se o serviço pedir confirmação e não criar o job, a ação falha — não mente.

    ``IngestJobCreateResult`` devolve ``job=None`` nesse caso. Registrar "enfileirado" sem
    id deixaria a tratativa com um rastro que não existe, e o gestor esperando por uma
    carga que ninguém agendou.
    """
    monkeypatch.setattr(
        "app.modules.ingestion.jobs_service.criar_job",
        lambda *_a, **_k: SimpleNamespace(job=None, estimativa_itens=9000, limiar=100),
    )
    tratativa = SimpleNamespace(
        diagnostico={"resultado": "fonte_tem", "periodo_conferido": "2026-1"},
        tentativas=[],
    )
    check = SimpleNamespace(check_codigo="freshness_rreo", cod_ibge="2304400", periodo="2025-B6")

    with pytest.raises(AppError) as exc:
        resolucao._reingerir(
            SimpleNamespace(),  # type: ignore[arg-type]
            SimpleNamespace(capacidades=frozenset({"administrar"})),  # type: ignore[arg-type]
            check,  # type: ignore[arg-type]
            tratativa,  # type: ignore[arg-type]
        )
    assert exc.value.status == 422
    assert "9000" in (exc.value.detail or "")


# --------------------------------------------------------------------------- #
# 7. Resolver no painel tem de apagar o selo na página
#
# Relatado em uso, e a queixa era exata: o gestor tratou todas as ocorrências no painel
# ("Nenhuma verificação em falha em aberto"), voltou às páginas e o selo continuava
# dizendo "7 verificações em falha".
#
# Os dois liam fontes diferentes — o painel considerava a tratativa, o selo lia o veredito
# cru. Um fluxo de resolução que não apaga o aviso que ele resolveu não é um fluxo de
# resolução, e ensina a ignorar os dois.
# --------------------------------------------------------------------------- #
def test_ocorrencia_encerrada_nao_sela_mais_o_numero(client, make_org, cenario) -> None:
    org = make_org(entes=[cenario.ente])
    with admin_session() as s:
        s.add(
            DataQualityCheck(
                fonte="siconfi_rgf", cod_ibge=cenario.ente, periodo=PERIODO,
                versao_entrega="-", check_codigo="dcl_a6_vs_rgf", status="falha",
                esquerda=Decimal(1), direita=Decimal(2), diferenca=Decimal(-1),
                tolerancia=Decimal("0.01"),
            )
        )

    with admin_session() as s:
        antes = quality_service.selo_do_ente(s, cenario.ente, org_id=org.org_id)
    assert any(c.check_codigo == "dcl_a6_vs_rgf" for c in antes), "o selo deve começar aceso"

    # A organização aceita a divergência como fato da fonte, com justificativa.
    with admin_session() as s:
        s.add(
            QualidadeTratativa(
                org_id=org.org_id, check_codigo="dcl_a6_vs_rgf", cod_ibge=cenario.ente,
                periodo=PERIODO, status="aceita_como_fato",
                justificativa="Divergência publicada pelo ente; retificação solicitada.",
                tentativas=[],
            )
        )

    with admin_session() as s:
        depois = quality_service.selo_do_ente(s, cenario.ente, org_id=org.org_id)
    assert not any(c.check_codigo == "dcl_a6_vs_rgf" for c in depois)


def test_tratativa_de_uma_organizacao_nao_apaga_o_selo_de_outra(
    client, make_org, cenario
) -> None:
    """Controle negativo: a decisão é privada da organização.

    Duas consultorias acompanham o mesmo município. Se o aceite de uma silenciasse o selo
    da outra, uma organização estaria decidindo o que a outra vê sobre dado público — que
    é exatamente a fronteira que a plataforma inteira protege.
    """
    org_a = make_org(entes=[cenario.ente])
    org_b = make_org(entes=[cenario.ente])
    with admin_session() as s:
        s.add(
            DataQualityCheck(
                fonte="siconfi_rgf", cod_ibge=cenario.ente, periodo=PERIODO,
                versao_entrega="-", check_codigo="msc_vs_dca", status="falha",
                esquerda=Decimal(1), direita=Decimal(2), diferenca=Decimal(-1),
                tolerancia=Decimal("0.01"),
            )
        )
        s.add(
            QualidadeTratativa(
                org_id=org_a.org_id, check_codigo="msc_vs_dca", cod_ibge=cenario.ente,
                periodo=PERIODO, status="aceita_como_fato",
                justificativa="Analisado pela organização A e aceito como fato.",
                tentativas=[],
            )
        )

    with admin_session() as s:
        selo_a = quality_service.selo_do_ente(s, cenario.ente, org_id=org_a.org_id)
        selo_b = quality_service.selo_do_ente(s, cenario.ente, org_id=org_b.org_id)

    assert not any(c.check_codigo == "msc_vs_dca" for c in selo_a)
    assert any(c.check_codigo == "msc_vs_dca" for c in selo_b), (
        "a organização B não tratou nada e tem de continuar vendo a divergência"
    )


# --------------------------------------------------------------------------- #
# 8. O Resumo comparava com um período onde o indicador não pode existir
#
# Relatado em uso: "Sem base de comparação com o período anterior" no Ceará em 2025-B6.
# Não era falta de dado — 2025-B5 existe e tem entrega vigente.
#
# É estrutural: pessoal e dívida vêm do RGF, que é quadrimestral, e só existem nos
# bimestres que fecham com um quadrimestre (B2↔Q1, B4↔Q2, B6↔Q3). Medido no ente 23:
# B6 e B2 têm 7 indicadores; B5 e B1 têm 3. Comparar B6 com B5 estava condenado a vir
# vazio por construção.
# --------------------------------------------------------------------------- #
def test_comparacao_usa_a_apuracao_anterior_do_proprio_indicador(cenario) -> None:
    """Pula o bimestre que não tem o indicador, em vez de desistir nele."""
    with admin_session() as s:
        # `garantias` do cenário existe em PERIODO. Cria uma apuração dois bimestres antes,
        # deixando o bimestre do meio SEM o indicador — exatamente o padrão RGF.
        # Dois bimestres antes do PERIODO do cenario (2090-B4), deixando B3 sem o indicador.
        anterior_real = "2090-B2"
        s.add(
            MartIndicador(
                cod_ibge=cenario.ente, periodo=anterior_real, indicador="garantias",
                valor_rs=Decimal(1), valor_pct_rcl=Decimal("10"), faixa="normal",
                teto_pct=Decimal("22"), denominador="rcl", base_valor=Decimal(10),
                versao_entrega="1", source_ref={"relatorio": "RGF"},
            )
        )

    with admin_session() as s:
        achado = cockpit_service._apuracao_anterior(
            s, cod_ibge=cenario.ente, periodo=PERIODO_CENARIO, indicador="garantias"
        )
    assert achado == anterior_real, (
        "deve achar a apuração anterior do indicador, ainda que não seja o bimestre "
        "imediatamente anterior"
    )


def test_sem_apuracao_anterior_devolve_none(cenario) -> None:
    """Controle negativo: não inventa base onde não há.

    Se devolvesse o período mais antigo qualquer, o Resumo compararia com um valor sem
    relação temporal e apresentaria como "mudança" algo que é só a distância no tempo.
    """
    with admin_session() as s:
        achado = cockpit_service._apuracao_anterior(
            s, cod_ibge=cenario.ente, periodo="1900-B1", indicador="garantias"
        )
    assert achado is None


# --------------------------------------------------------------------------- #
# 9. Carga já pedida não aceita segunda
#
# Relatado em uso: dois jobs de DCA idênticos para o mesmo caso. O gestor clicou duas
# vezes porque o histórico mostrava "reingerir —" sem resultado — mas o remédio não é só
# mostrar melhor: ingestão é assíncrona, e nada impedia a segunda carga.
# --------------------------------------------------------------------------- #
def test_segunda_reingestao_do_mesmo_caso_e_recusada() -> None:
    """Recusa apontando o job anterior, em vez de duplicar trabalho do worker."""
    tratativa = SimpleNamespace(
        status="acao_aplicada",
        classe="cobertura",
        diagnostico={"resultado": "fonte_tem", "periodo_conferido": "2025-1"},
        tentativas=[
            {"acao": "reingerir", "job_id": "9fd7dd36-f03c-47d0-8b5d-18fde15d5aac",
             "periodo_solicitado": "2025-1"}
        ],
    )
    ja = resolucao._reingestao_ja_pedida(tratativa)  # type: ignore[arg-type]
    assert ja is not None
    assert str(ja.get("job_id")).startswith("9fd7dd36")


def test_reingestao_e_liberada_de_novo_quando_o_check_voltou_a_ok() -> None:
    """Controle negativo: bloquear para sempre impediria tratar uma defasagem futura.

    O bloqueio existe contra o clique repetido enquanto a carga não foi avaliada — não
    contra pedir carga de novo num período novo, meses depois.
    """
    tratativa = SimpleNamespace(
        tentativas=[{"acao": "verificar_na_fonte", "resultado": "fonte_tem"}]
    )
    assert resolucao._reingestao_ja_pedida(tratativa) is None  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 10. Carga de um estado pode incluir os municípios dele
#
# Pedido de uso: baixar a fonte de um estado e, junto, dos municípios daquela UF — hoje
# seriam 184 códigos digitados à mão para o Ceará.
#
# A expansão fica no backend porque é aqui que o escopo é conferido. E é **interseção**,
# não união: quem tem parte da UF na carteira recebe a parte, com a contagem do que ficou
# de fora. Expandir para todos daria 403 no lote inteiro; expandir em silêncio esconderia
# que a maioria não veio.
# --------------------------------------------------------------------------- #
def test_expansao_por_uf_respeita_o_escopo_e_conta_o_que_ficou_de_fora(make_org) -> None:
    from app.modules.ingestion import jobs_service

    # UF sintética 92: 90 e 94–99 já são usadas por outras suítes, e sortear dentro dessa
    # faixa faria a limpeza abaixo apagar dado alheio. A limpeza é por código exato (não
    # por UF) e roda antes e depois, para o teste não depender de ter terminado limpo da
    # última vez — um resíduo de execução interrompida já quebrou a suíte inteira aqui.
    uf, dentro, fora = "92", "9200001", "9200002"
    with admin_session() as s:
        s.execute(delete(DimEnte).where(DimEnte.cod_ibge.in_([uf, dentro, fora])))
        # `uf="BR"` no ente estadual espelha a produção: só o município carrega a sigla
        # da UF. A fixture anterior dava a mesma `uf` aos dois e, com isso, aprovava uma
        # expansão que em produção devolveria zero município — o teste validava a
        # suposição do código em vez do dado real.
        s.add(DimEnte(cod_ibge=uf, nome="Estado Teste", esfera="estadual", uf="BR"))
        for cod in (dentro, fora):
            s.add(DimEnte(cod_ibge=cod, nome=f"Municipio {cod}", esfera="municipal", uf="ZZ"))

    # Carteira com apenas UM dos dois municípios — o escopo sai da carteira real, não de
    # uma lista informada no principal.
    org = make_org(entes=[uf, dentro])
    principal = Principal(
        usuario_id=org.usuario_id, org_id=org.org_id, papel="Papel",
        capacidades=frozenset({"administrar"}), escopo_ibges=None,
    )
    with admin_session() as s:
        incluidos, de_fora = jobs_service._municipios_da_uf(s, principal, [uf])

    with admin_session() as s:
        s.execute(delete(DimEnte).where(DimEnte.cod_ibge.in_([uf, dentro, fora])))

    assert incluidos == [dentro], "só entra o município que está no escopo"
    assert de_fora == 1, "o que ficou de fora tem de ser contado, não silenciado"


def test_expansao_ignora_ente_que_nao_e_estadual(make_org) -> None:
    """Controle negativo: pedir a expansão com um município na lista não traz a UF inteira.

    Sem isto, digitar um código municipal e marcar a caixa dispararia a carga do estado
    todo — uma ação muito maior do que a pedida, a partir de um clique ambíguo.
    """
    from app.modules.ingestion import jobs_service

    org = make_org(entes=["2304400"])
    principal = Principal(
        usuario_id=org.usuario_id, org_id=org.org_id, papel="Papel",
        capacidades=frozenset({"administrar"}), escopo_ibges=None,
    )
    with admin_session() as s:
        incluidos, de_fora = jobs_service._municipios_da_uf(s, principal, ["2304400"])

    assert incluidos == []
    assert de_fora == 0
