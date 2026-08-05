"""Cenários salvos: versão, procedência, reabertura e comparação.

Um cenário salvo guardava as premissas e o resultado do momento. Faltavam três coisas, e as
três falhavam em silêncio.

## Editar destruía

Ajustar uma premissa sobrescrevia o registro. Mas cenário é peça de decisão: *"o que eu
levei à reunião de agosto"* precisa sobreviver ao ajuste de outubro, ou não há como
reconstruir por que a decisão foi aquela. Aqui, salvar sobre um cenário existente **cria
versão** — a anterior permanece, íntegra e consultável.

## Reabrir mentia por omissão

O resultado congelado era exibido com a mesma cara de um cálculo corrente. Se o ente
entregou RGF novo no intervalo, as mesmas premissas produzem outro número hoje, e a tela
não tinha como dizer qual dos dois estava mostrando.

:func:`abrir` devolve os dois lados: o **guardado** (o que embasou a decisão) e o
**recalculado** (o que o dado diz agora), mais a divergência entre eles e quais entregas
novas apareceram desde então. Não escolhe por ninguém — quem decide se quer reproduzir ou
atualizar é o gestor, e essa escolha só existe se os dois números estiverem à vista.

## A procedência inclui a premissa observada

A coluna menos óbvia de ``op.cenario_versao`` é ``premissas_observadas``. *"Aceitei o IPCA
observado"* muda de significado quando o observado muda: o cenário de agosto rodou com IPCA
de 4,54%, e o mesmo botão em dezembro significa outro número. Sem gravar o valor vigente à
época, a premissa registrada não reproduz coisa alguma.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError
from app.modules.forecast import premissas as premissas_mod
from app.modules.forecast import repository
from app.modules.forecast.models import Cenario, CenarioVersao
from app.modules.forecast.schemas import (
    CenarioAbertoResponse,
    CenarioComparadoItem,
    CenarioDetalhe,
    CenarioSimularRequest,
    ComparacaoCenariosResponse,
    DivergenciaCenario,
    ProcedenciaCenario,
    VersaoCenario,
)
from app.modules.tenancy import repository as tenancy_repo

#: Diferença a partir da qual o recálculo deixa de ser ruído de arredondamento e passa a
#: ser mudança de fato. Abaixo disso, dois números que arredondam igual na tela.
TOLERANCIA_DIVERGENCIA = Decimal("0.005")


def _org(principal: Principal) -> uuid.UUID:
    if principal.org_id is None:
        raise AppError(
            status=403,
            title="Sem organização",
            detail="Operar cenários exige organização ativa.",
        )
    return principal.org_id


def _procedencia(versao: CenarioVersao) -> ProcedenciaCenario:
    return ProcedenciaCenario(
        as_of=versao.as_of,
        versoes_entrega=versao.versoes_entrega or {},
        premissas_observadas=versao.premissas_observadas or {},
        # Versão sem `as_of` veio da migração do formato antigo. Declarar isso é o que
        # impede a tela de prometer uma reprodutibilidade que esses registros não têm.
        registrada=versao.as_of is not None,
    )


def _versao_out(versao: CenarioVersao, emails: dict[uuid.UUID, str]) -> VersaoCenario:
    return VersaoCenario(
        versao=versao.versao,
        nome=versao.nome,
        parametros=versao.parametros,
        modelo=versao.modelo,
        horizonte=versao.horizonte,
        nota=versao.nota,
        procedencia=_procedencia(versao),
        criado_em=versao.criado_em,
        criado_por=emails.get(versao.criado_por) if versao.criado_por else None,
    )


def _emails_do_cenario(
    session: Session, cenario: Cenario, versoes: list[CenarioVersao]
) -> dict[uuid.UUID, str]:
    """E-mails de quem criou o cabeçalho e cada versão, resolvidos numa só ida ao banco."""
    ids = [cenario.criado_por, *(v.criado_por for v in versoes)]
    return tenancy_repo.emails_por_usuario(session, ids=[i for i in ids if i is not None])


def _detalhe(
    cenario: Cenario, versoes: list[CenarioVersao], emails: dict[uuid.UUID, str]
) -> CenarioDetalhe:
    return CenarioDetalhe(
        id=str(cenario.id),
        ente=cenario.ente,
        indicador=cenario.indicador,
        nome=cenario.nome,
        versao_atual=max((v.versao for v in versoes), default=0),
        arquivado=cenario.arquivado_em is not None,
        criado_em=cenario.criado_em,
        atualizado_em=cenario.atualizado_em,
        criado_por=emails.get(cenario.criado_por) if cenario.criado_por else None,
        versoes=[_versao_out(v, emails) for v in versoes],
    )


def _entregas_da_serie(session: Session, cod_ibge: str, indicador: str) -> dict[str, str]:
    """Quais entregas alimentaram a série — a impressão digital do dado usado.

    É o que permite, na reabertura, dizer *o que* mudou: não "o número está diferente", e
    sim "o ente entregou o RGF de 2026-Q1 depois que você salvou".
    """
    from app.modules.forecast.series import carregar_serie

    try:
        serie = carregar_serie(session, indicador, cod_ibge)
    except (KeyError, AppError):
        return {}
    return {p.periodo: p.versao_entrega for p in serie.pontos}


def _premissas_vigentes(session: Session, cod_ibge: str) -> dict[str, Any]:
    return {
        p.chave: {
            "observado": str(p.observado) if p.observado is not None else None,
            "referencia": p.referencia,
            "fonte": p.fonte,
        }
        for p in premissas_mod.observadas(session, cod_ibge)
    }


def salvar(
    session: Session,
    principal: Principal,
    *,
    cod_ibge: str,
    indicador: str,
    req: CenarioSimularRequest,
    resultado: dict | None,
    modelo: str | None,
    cenario_id: uuid.UUID | None = None,
) -> tuple[Cenario, CenarioVersao]:
    """Grava o cenário. Com ``cenario_id``, adiciona versão; sem ele, cria o cenário.

    A procedência é capturada **no momento do salvamento**, não do cálculo: são o mesmo
    instante na prática, e amarrá-la aqui garante que nenhum caminho de gravação escape
    sem registrar sobre qual dado o cenário foi construído.
    """
    org_id = _org(principal)
    agora = datetime.now(UTC)

    if cenario_id is not None:
        cenario = repository.get_cenario(session, org_id=org_id, cenario_id=cenario_id)
        if cenario is None:
            raise AppError(
                status=404, title="Cenário não encontrado", detail=str(cenario_id)
            )
        cenario.nome = req.nome or cenario.nome
        cenario.atualizado_em = agora
    else:
        cenario = repository.criar_cenario(
            session,
            {
                "org_id": org_id,
                "ente": cod_ibge,
                "indicador": indicador,
                "nome": req.nome,
                "parametros": req.model_dump(exclude_none=True),
                "resultado": resultado,
                "criado_por": principal.usuario_id,
                "atualizado_em": agora,
            },
        )

    versao = repository.criar_versao(
        session,
        {
            "org_id": org_id,
            "cenario_id": cenario.id,
            "versao": repository.proxima_versao(session, cenario_id=cenario.id),
            "nome": req.nome,
            "parametros": req.model_dump(exclude_none=True),
            "resultado": resultado,
            "as_of": agora,
            "versoes_entrega": _entregas_da_serie(session, cod_ibge, indicador),
            "premissas_observadas": _premissas_vigentes(session, cod_ibge),
            "modelo": modelo,
            "horizonte": req.horizonte,
            "criado_por": principal.usuario_id,
        },
    )
    return cenario, versao


def _valor_final(resultado: dict | None) -> Decimal | None:
    """Último ponto projetado do resultado guardado, seja qual for o formato gravado."""
    if not isinstance(resultado, dict):
        return None
    pontos = resultado.get("projecao")
    if not isinstance(pontos, list) or not pontos:
        return None
    ultimo = pontos[-1]
    if not isinstance(ultimo, dict):
        return None
    bruto = ultimo.get("valor_previsto")
    if bruto is None:
        return None
    try:
        return Decimal(str(bruto))
    except (ArithmeticError, ValueError):
        return None


def _motivo_divergencia(entregas_novas: list[str], delta: Decimal) -> str | None:
    """Por que o recálculo difere — distinguindo a causa esperada da que pede investigação.

    Entrega nova explica a diferença e é rotina. Diferença **sem** entrega nova não é: ou o
    modelo mudou, ou algo se moveu na gold sem passar por entrega. Dizer "o dado mudou"
    nesse caso mandaria o gestor procurar onde não está.
    """
    if entregas_novas:
        return "O dado mudou desde o salvamento."
    if abs(delta) > TOLERANCIA_DIVERGENCIA:
        return "A projeção mudou sem entrega nova — verifique o modelo."
    return None


def _divergencia(
    versao: CenarioVersao, recalculado_final: Decimal | None, entregas_hoje: dict[str, str]
) -> DivergenciaCenario:
    """Compara o guardado com o recálculo, e diz **o que** mudou no dado.

    Distingue três estados que um booleano só colapsaria: não deu para comparar, comparou e
    bate, comparou e diverge. O primeiro é o mais perigoso se disfarçado de terceiro — uma
    versão migrada sem procedência apareceria como "sem divergência".
    """
    guardado = _valor_final(versao.resultado)
    antigas = versao.versoes_entrega or {}
    novas = sorted(
        p for p, v in entregas_hoje.items() if p not in antigas or antigas[p] != v
    )

    if versao.as_of is None:
        return DivergenciaCenario(
            comparavel=False,
            motivo=(
                "Versão salva antes de a procedência ser registrada: não há como saber "
                "sobre qual entrega o resultado foi calculado."
            ),
            valor_guardado=guardado,
            valor_recalculado=recalculado_final,
            entregas_novas=novas,
        )
    if guardado is None or recalculado_final is None:
        return DivergenciaCenario(
            comparavel=False,
            motivo="Sem resultado guardado ou sem recálculo possível para comparar.",
            valor_guardado=guardado,
            valor_recalculado=recalculado_final,
            entregas_novas=novas,
        )

    delta = recalculado_final - guardado
    return DivergenciaCenario(
        comparavel=True,
        diverge=abs(delta) > TOLERANCIA_DIVERGENCIA,
        valor_guardado=guardado,
        valor_recalculado=recalculado_final,
        delta=delta,
        entregas_novas=novas,
        motivo=_motivo_divergencia(novas, delta),
    )


def abrir(
    session: Session,
    principal: Principal,
    *,
    cenario_id: uuid.UUID,
    versao: int | None = None,
    recalcular: bool = True,
) -> CenarioAbertoResponse:
    """Reabre o cenário mostrando **os dois lados**: o que foi decidido e o que o dado diz.

    Escolher um dos dois pelo gestor seria decidir por ele qual pergunta ele está fazendo —
    e as duas são legítimas: "com o que eu decidi" e "isso ainda vale?".
    """
    from app.modules.forecast import service

    org_id = _org(principal)
    cenario = repository.get_cenario(session, org_id=org_id, cenario_id=cenario_id)
    if cenario is None:
        raise AppError(status=404, title="Cenário não encontrado", detail=str(cenario_id))

    versoes = repository.list_versoes(session, org_id=org_id, cenario_id=cenario_id)
    if not versoes:
        raise AppError(
            status=404,
            title="Cenário sem versões",
            detail="O cenário existe mas nenhuma versão foi gravada.",
        )
    alvo = next((v for v in versoes if v.versao == versao), None) if versao else versoes[0]
    if alvo is None:
        raise AppError(
            status=404,
            title="Versão não encontrada",
            detail=f"O cenário não tem versão {versao}.",
        )

    recalculado = None
    final_hoje: Decimal | None = None
    if recalcular:
        try:
            req = CenarioSimularRequest(**{**alvo.parametros, "salvar": False})
            recalculado = service.simular_cenario(
                session, principal, cenario.ente, cenario.indicador, req
            )
            if recalculado.cenario.projecao:
                final_hoje = recalculado.cenario.projecao[-1].valor_previsto
        except AppError:
            # Recálculo impossível (série encolheu, indicador saiu do catálogo) **não**
            # invalida a reabertura: o guardado continua valendo como registro da decisão.
            recalculado = None

    emails = _emails_do_cenario(session, cenario, versoes)
    return CenarioAbertoResponse(
        cenario=_detalhe(cenario, versoes, emails),
        versao=_versao_out(alvo, emails),
        guardado=alvo.resultado,
        recalculado=recalculado,
        divergencia=_divergencia(
            alvo, final_hoje, _entregas_da_serie(session, cenario.ente, cenario.indicador)
        ),
    )


def listar(
    session: Session, principal: Principal, *, cod_ibge: str | None, incluir_arquivados: bool
) -> list[CenarioDetalhe]:
    org_id = _org(principal)
    cenarios = repository.list_cenarios(
        session, org_id=org_id, ente=cod_ibge, incluir_arquivados=incluir_arquivados
    )
    resultado: list[CenarioDetalhe] = []
    for c in cenarios:
        versoes = repository.list_versoes(session, org_id=org_id, cenario_id=c.id)
        resultado.append(_detalhe(c, versoes, _emails_do_cenario(session, c, versoes)))
    return resultado


def renomear(
    session: Session, principal: Principal, *, cenario_id: uuid.UUID, nome: str
) -> CenarioDetalhe:
    """Renomeia o cabeçalho **sem** tocar nas versões.

    O nome de cada versão é o que ela tinha quando foi salva — reescrevê-lo apagaria o
    rastro de que o cenário se chamava outra coisa quando embasou a decisão.
    """
    org_id = _org(principal)
    cenario = repository.get_cenario(session, org_id=org_id, cenario_id=cenario_id)
    if cenario is None:
        raise AppError(status=404, title="Cenário não encontrado", detail=str(cenario_id))
    cenario.nome = nome
    cenario.atualizado_em = datetime.now(UTC)
    session.flush()
    versoes = repository.list_versoes(session, org_id=org_id, cenario_id=cenario_id)
    return _detalhe(cenario, versoes, _emails_do_cenario(session, cenario, versoes))


def arquivar(
    session: Session, principal: Principal, *, cenario_id: uuid.UUID, desarquivar: bool = False
) -> CenarioDetalhe:
    """Tira o cenário da lista sem apagá-lo.

    Apagar seria remover a evidência de uma decisão porque alguém quis limpar a tela. O
    arquivamento é reversível; a exclusão definitiva não seria.
    """
    org_id = _org(principal)
    cenario = repository.get_cenario(session, org_id=org_id, cenario_id=cenario_id)
    if cenario is None:
        raise AppError(status=404, title="Cenário não encontrado", detail=str(cenario_id))
    cenario.arquivado_em = None if desarquivar else datetime.now(UTC)
    cenario.atualizado_em = datetime.now(UTC)
    session.flush()
    versoes = repository.list_versoes(session, org_id=org_id, cenario_id=cenario_id)
    return _detalhe(cenario, versoes, _emails_do_cenario(session, cenario, versoes))


def duplicar(
    session: Session, principal: Principal, *, cenario_id: uuid.UUID, nome: str | None = None
) -> CenarioDetalhe:
    """Copia o cenário para um cabeçalho novo e independente (Sprint G1).

    "Testar uma variação sem perder o original" não é o que o versionamento resolve —
    salvar uma nova versão *substitui* qual delas a lista mostra por padrão. Duplicar cria
    um segundo cenário, com a última versão do original como ponto de partida, para que os
    dois possam ser comparados lado a lado depois (o mesmo painel que já existe para
    cenários distintos).
    """
    org_id = _org(principal)
    original = repository.get_cenario(session, org_id=org_id, cenario_id=cenario_id)
    if original is None:
        raise AppError(status=404, title="Cenário não encontrado", detail=str(cenario_id))
    ultima = repository.get_versao(session, org_id=org_id, cenario_id=cenario_id)
    if ultima is None:
        raise AppError(
            status=404,
            title="Cenário sem versões",
            detail="Nada para duplicar — o cenário não tem nenhuma versão gravada.",
        )

    novo_nome = nome or f"Cópia de {original.nome}"
    agora = datetime.now(UTC)
    clone = repository.criar_cenario(
        session,
        {
            "org_id": org_id,
            "ente": original.ente,
            "indicador": original.indicador,
            "nome": novo_nome,
            "parametros": ultima.parametros,
            "resultado": ultima.resultado,
            "criado_por": principal.usuario_id,
            "atualizado_em": agora,
        },
    )
    versao = repository.criar_versao(
        session,
        {
            "org_id": org_id,
            "cenario_id": clone.id,
            "versao": 1,
            "nome": novo_nome,
            "parametros": ultima.parametros,
            "resultado": ultima.resultado,
            # A procedência do clone é a mesma da versão copiada — duplicar não recalcula,
            # então "sobre qual dado" continua sendo a mesma resposta de antes.
            "as_of": ultima.as_of,
            "versoes_entrega": ultima.versoes_entrega,
            "premissas_observadas": ultima.premissas_observadas,
            "modelo": ultima.modelo,
            "horizonte": ultima.horizonte,
            "nota": f"Duplicado de '{original.nome}' (v{ultima.versao}).",
            "criado_por": principal.usuario_id,
        },
    )
    session.flush()
    return _detalhe(clone, [versao], _emails_do_cenario(session, clone, [versao]))


def excluir_definitivo(session: Session, principal: Principal, *, cenario_id: uuid.UUID) -> None:
    """Apaga o cenário e todas as suas versões — irreversível, distinto de arquivar.

    Arquivar é sempre a resposta certa **quando o cenário embasou uma decisão**: o registro
    precisa sobreviver mesmo fora da lista. Um rascunho criado por engano não carrega esse
    peso, e forçar o arquivamento nesse caso só ensina o gestor a ignorar a lista de
    arquivados. A UI exige confirmação antes de chamar isto — aqui não há mais pergunta.
    """
    org_id = _org(principal)
    cenario = repository.get_cenario(session, org_id=org_id, cenario_id=cenario_id)
    if cenario is None:
        raise AppError(status=404, title="Cenário não encontrado", detail=str(cenario_id))
    repository.excluir_cenario(session, cenario)


def _pontos_do_resultado(resultado: dict | None) -> list[dict]:
    if not isinstance(resultado, dict):
        return []
    pontos = resultado.get("projecao")
    return [p for p in pontos if isinstance(p, dict)] if isinstance(pontos, list) else []


def comparar(
    session: Session,
    principal: Principal,
    *,
    cod_ibge: str,
    cenario_ids: list[uuid.UUID],
) -> ComparacaoCenariosResponse:
    """Cenários lado a lado no mesmo eixo temporal.

    O eixo é a **interseção** dos horizontes, não a união: comparar um cenário de 4
    períodos com um de 12 num eixo de 12 deixaria o primeiro terminando no meio do gráfico,
    e a leitura natural — *"este cenário despenca no fim"* — seria falsa.

    Cenário pedido e não encontrado entra na resposta com ``encontrado=False``. Sumir da
    lista faria o gestor comparar três curvas achando que são as quatro que escolheu.
    """
    from app.modules.forecast.schemas import PontoProjecao

    org_id = _org(principal)
    encontrados = repository.get_versoes_por_ids(
        session, org_id=org_id, cenario_ids=cenario_ids
    )
    cabecalhos = {
        c.id: c
        for c in repository.list_cenarios(
            session, org_id=org_id, ente=None, incluir_arquivados=True
        )
    }

    itens: list[CenarioComparadoItem] = []
    conjuntos: list[set[str]] = []
    for cid in cenario_ids:
        versao = encontrados.get(cid)
        cabecalho = cabecalhos.get(cid)
        if versao is None or cabecalho is None:
            itens.append(
                CenarioComparadoItem(
                    cenario_id=str(cid),
                    nome="(não encontrado)",
                    versao=0,
                    encontrado=False,
                    motivo_ausencia="Cenário inexistente ou de outra organização.",
                )
            )
            continue
        pontos_brutos = _pontos_do_resultado(versao.resultado)
        pontos = [PontoProjecao(**p) for p in pontos_brutos]
        conjuntos.append({p.periodo_alvo for p in pontos})
        itens.append(
            CenarioComparadoItem(
                cenario_id=str(cid),
                nome=cabecalho.nome,
                versao=versao.versao,
                indicador=cabecalho.indicador,
                modelo=versao.modelo,
                parametros=versao.parametros,
                projecao=pontos,
                valor_final=_valor_final(versao.resultado),
                criado_em=versao.criado_em,
            )
        )

    comuns = sorted(set.intersection(*conjuntos)) if conjuntos else []
    for item in itens:
        if item.encontrado:
            item.projecao = [p for p in item.projecao if p.periodo_alvo in comuns]

    indicadores = {i.indicador for i in itens if i.encontrado and i.indicador}
    aviso = None
    if len(indicadores) > 1:
        # Sobrepor pessoal (% RCL) e RCL (R$) no mesmo eixo produz um gráfico bonito e sem
        # significado. A comparação é devolvida — quem pediu pode ter razão —, mas dita.
        aviso = (
            "Os cenários comparados são de indicadores diferentes "
            f"({', '.join(sorted(indicadores))}); as escalas não são a mesma."
        )
    elif conjuntos and not comuns:
        aviso = "Os cenários não têm nenhum período em comum para comparar."

    return ComparacaoCenariosResponse(
        cod_ibge=cod_ibge,
        indicador=next(iter(indicadores)) if len(indicadores) == 1 else None,
        periodos=comuns,
        itens=itens,
        aviso=aviso,
    )


def exportar(
    session: Session,
    principal: Principal,
    *,
    cenario_id: uuid.UUID,
    versao: int | None,
    formato: str,
) -> tuple[bytes, str, str]:
    """Exporta a versão com **as premissas e a procedência junto da curva**.

    Exportar só a série produziria um arquivo que ninguém consegue auditar seis meses
    depois: sem saber sob quais premissas e sobre qual entrega ela foi construída, a curva
    é um desenho bonito. O cabeçalho carrega as duas coisas, e é por isso que o CSV começa
    com linhas de metadado em vez de ir direto ao cabeçalho da tabela — um CSV que só o
    Excel entende é pior que um que a auditoria entende.
    """
    import csv
    import io
    import json

    org_id = _org(principal)
    cenario = repository.get_cenario(session, org_id=org_id, cenario_id=cenario_id)
    if cenario is None:
        raise AppError(status=404, title="Cenário não encontrado", detail=str(cenario_id))
    alvo = repository.get_versao(
        session, org_id=org_id, cenario_id=cenario_id, versao=versao
    )
    if alvo is None:
        raise AppError(
            status=404,
            title="Versão não encontrada",
            detail=f"O cenário não tem versão {versao}." if versao else "Cenário sem versões.",
        )

    base = f"cenario-{cenario.ente}-{cenario.indicador}-v{alvo.versao}"
    pontos = _pontos_do_resultado(alvo.resultado)

    if formato == "json":
        corpo = {
            "cenario": {
                "id": str(cenario.id),
                "nome": cenario.nome,
                "ente": cenario.ente,
                "indicador": cenario.indicador,
            },
            "versao": alvo.versao,
            "premissas": alvo.parametros,
            "procedencia": _procedencia(alvo).model_dump(mode="json"),
            "modelo": alvo.modelo,
            "projecao": pontos,
        }
        return (
            json.dumps(corpo, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
            "application/json; charset=utf-8",
            f"{base}.json",
        )

    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=";")
    escritor.writerow(["# cenário", cenario.nome])
    escritor.writerow(["# ente", cenario.ente])
    escritor.writerow(["# indicador", cenario.indicador])
    escritor.writerow(["# versão", alvo.versao])
    escritor.writerow(["# modelo", alvo.modelo or "—"])
    proc = _procedencia(alvo)
    escritor.writerow(
        ["# calculado em", proc.as_of.isoformat() if proc.as_of else "não registrado"]
    )
    entregas = "; ".join(f"{k}=v{v}" for k, v in sorted(proc.versoes_entrega.items()))
    escritor.writerow(["# entregas usadas", entregas or "não registradas"])
    for chave, valor in sorted(alvo.parametros.items()):
        if chave not in ("salvar", "nome", "cenario_id"):
            escritor.writerow([f"# premissa {chave}", valor])
    escritor.writerow([])
    escritor.writerow(["periodo", "valor_previsto", "ic_inferior", "ic_superior", "cruza_limite"])
    for ponto in pontos:
        escritor.writerow(
            [
                ponto.get("periodo_alvo", ""),
                ponto.get("valor_previsto", ""),
                ponto.get("ic_inferior", ""),
                ponto.get("ic_superior", ""),
                "sim" if ponto.get("cruza_limite") else "nao",
            ]
        )
    # BOM para o Excel em pt-BR abrir com acento correto sem passar pelo assistente de
    # importação — o arquivo é para o gestor, não para um pipeline.
    return (
        buffer.getvalue().encode("utf-8-sig"),
        "text/csv; charset=utf-8",
        f"{base}.csv",
    )
