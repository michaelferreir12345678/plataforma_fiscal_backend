"""Endpoints de Previsões & Cenários (Módulo 13)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.forecast import cenarios, service
from app.modules.forecast.schemas import (
    CenarioAbertoResponse,
    CenarioDetalhe,
    CenarioRenomearRequest,
    CenarioSimularRequest,
    CenarioSimularResponse,
    ComparacaoCenariosRequest,
    ComparacaoCenariosResponse,
    ComparacaoModelosResponse,
    PremissasResponse,
    ProjecaoResponse,
)
from app.shared.scope import assert_ente_in_scope

router = APIRouter(tags=["forecast"])

_INDICADOR_Q = Query("rcl", description="Indicador: rcl | receita | pessoal | divida.")
_AS_OF_Q = Query(None, description="Consulta bitemporal 'as of'.")


@router.get("/entes/{cod_ibge}/projecao", response_model=ProjecaoResponse)
def projecao(
    cod_ibge: str,
    indicador: str = _INDICADOR_Q,
    horizonte: int = Query(4, ge=1, le=24, description="Períodos à frente."),
    modelo: str | None = Query(None, description="fechamento | holt_winters | regressao_exogenas."),
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> ProjecaoResponse:
    """Histórico + projeção + IC + marcador de cruzamento de limite (materializa a gold)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_projecao(
        session, cod_ibge, indicador, horizonte=horizonte, modelo=modelo, as_of=as_of
    )


@router.get(
    "/entes/{cod_ibge}/projecao/comparacao", response_model=ComparacaoModelosResponse
)
def comparacao_modelos(
    cod_ibge: str,
    indicador: str = _INDICADOR_Q,
    horizonte: int = Query(4, ge=1, le=24, description="Períodos à frente."),
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> ComparacaoModelosResponse:
    """As três camadas lado a lado, com incerteza e o critério da escolha."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.comparar_modelos(
        session, cod_ibge, indicador, horizonte=horizonte, as_of=as_of
    )


@router.get("/entes/{cod_ibge}/cenario/premissas", response_model=PremissasResponse)
def premissas_cenario(
    cod_ibge: str,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> PremissasResponse:
    """As premissas macro **observadas**, com data e fonte, para o cenário abrir ancorado.

    Sem isto a tela sugeria valores de fábrica indistinguíveis de valores medidos — e um
    cenário construído sobre premissa inventada é uma conclusão inventada.
    """
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.premissas_observadas(session, cod_ibge)


@router.post("/entes/{cod_ibge}/cenario/simular", response_model=CenarioSimularResponse)
def simular_cenario(
    cod_ibge: str,
    req: CenarioSimularRequest,
    indicador: str = _INDICADOR_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> CenarioSimularResponse:
    """Deltas de gestor → recalcula projeção + impacto em limites/mínimos. Só grava se salvar."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.simular_cenario(session, principal, cod_ibge, indicador, req)


@router.get("/entes/{cod_ibge}/cenarios", response_model=list[CenarioDetalhe])
def listar_cenarios(
    cod_ibge: str,
    incluir_arquivados: bool = Query(False, description="Traz também os arquivados."),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> list[CenarioDetalhe]:
    """Cenários da organização para o ente, cada um com seu histórico de versões."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return cenarios.listar(
        session, principal, cod_ibge=cod_ibge, incluir_arquivados=incluir_arquivados
    )


@router.get("/cenarios/{cenario_id}", response_model=CenarioAbertoResponse)
def obter_cenario(
    cenario_id: uuid.UUID,
    versao: int | None = Query(None, description="Versão específica; ausente usa a última."),
    recalcular: bool = Query(
        True, description="Roda de novo com o dado de hoje, para comparar com o guardado."
    ),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> CenarioAbertoResponse:
    """Reabre o cenário mostrando **os dois lados**: o guardado e o de hoje.

    Escolher um deles pelo servidor seria decidir por quem pergunta qual das duas perguntas
    ele está fazendo — "com o que eu decidi" e "isso ainda vale?" são ambas legítimas, e a
    segunda só existe se as duas estiverem à vista.
    """
    return cenarios.abrir(
        session, principal, cenario_id=cenario_id, versao=versao, recalcular=recalcular
    )


@router.patch("/cenarios/{cenario_id}", response_model=CenarioDetalhe)
def renomear_cenario(
    cenario_id: uuid.UUID,
    req: CenarioRenomearRequest,
    principal: Principal = Depends(require_capability("editar")),
    session: Session = Depends(get_db),
) -> CenarioDetalhe:
    """Renomeia o cabeçalho. As versões guardam o nome que tinham quando foram salvas."""
    detalhe = cenarios.renomear(session, principal, cenario_id=cenario_id, nome=req.nome)
    session.commit()
    return detalhe


@router.delete("/cenarios/{cenario_id}", response_model=CenarioDetalhe)
def arquivar_cenario(
    cenario_id: uuid.UUID,
    desarquivar: bool = Query(False, description="Traz o cenário de volta à lista."),
    principal: Principal = Depends(require_capability("editar")),
    session: Session = Depends(get_db),
) -> CenarioDetalhe:
    """**Arquiva, não apaga.** Um cenário que embasou decisão não some porque a lista
    ficou grande; sai da tela e continua auditável."""
    detalhe = cenarios.arquivar(
        session, principal, cenario_id=cenario_id, desarquivar=desarquivar
    )
    session.commit()
    return detalhe


@router.post("/entes/{cod_ibge}/cenarios/comparar", response_model=ComparacaoCenariosResponse)
def comparar_cenarios(
    cod_ibge: str,
    req: ComparacaoCenariosRequest,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> ComparacaoCenariosResponse:
    """Cenários lado a lado no mesmo eixo — a interseção dos horizontes, não a união."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return cenarios.comparar(
        session, principal, cod_ibge=cod_ibge, cenario_ids=req.cenario_ids
    )


@router.get("/cenarios/{cenario_id}/exportar")
def exportar_cenario(
    cenario_id: uuid.UUID,
    versao: int | None = Query(None, description="Versão a exportar; ausente usa a última."),
    formato: str = Query("csv", pattern="^(csv|json)$"),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> Response:
    """Exporta a versão com **as premissas e a procedência juntas**.

    Exportar só a curva produziria um arquivo que ninguém consegue auditar seis meses
    depois: sem saber sob quais premissas e sobre qual entrega ela foi feita, a série é um
    desenho. O cabeçalho do arquivo carrega as duas coisas.
    """
    conteudo, tipo, nome = cenarios.exportar(
        session, principal, cenario_id=cenario_id, versao=versao, formato=formato
    )
    return Response(
        content=conteudo,
        media_type=tipo,
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )
