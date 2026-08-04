"""Regras dos indicadores: cálculo incremental da RCL e classificação de limites."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.catalog import repository as catalog_repo
from app.modules.catalog import service as catalog_service
from app.modules.indicators import repository
from app.modules.indicators.limites import (
    LimiteLegal,
    calcular_indicador_sobre_rcl,
    classificar_faixa,
)
from app.modules.indicators.models import FatoRcl
from app.modules.indicators.rcl import RclLinha
from app.modules.indicators.rcl import calcular_rcl as _calcular_rcl_puro
from app.modules.indicators.schemas import ComponenteOut, IndicadorOut, RclResponse
from app.shared.ausencia import ausencia_de_entrega
from app.shared.source_ref import SourceRef, composite_version_key

_ANEXO_RCL = "Anexo 03"


def _source_ref(periodo: str, versao: str) -> SourceRef:
    return SourceRef(relatorio="RREO", anexo=_ANEXO_RCL, periodo=periodo, versao_entrega=versao)


def calcular_rcl(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> FatoRcl:
    """Calcula a RCL (12m) do período e materializa em ``fato_rcl`` (recálculo incremental)."""
    versao = repository.resolve_versao_rreo(
        session, cod_ibge=cod_ibge, periodo=periodo, as_of=as_of
    )
    if versao is None:
        raise ausencia_de_entrega(
            session,
            cod_ibge=cod_ibge,
            relatorio="RREO",
            periodo=periodo,
            title="RREO ausente",
            detail=f"Sem RREO vigente para {cod_ibge} em {periodo}.",
        )
    linhas = [
        RclLinha(conta=r.conta or "", coluna=r.coluna, valor=Decimal(r.valor or 0))
        for r in repository.read_anexo03(
            session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
        )
    ]
    if not linhas:
        # **A entrega existe, o Anexo 03 não.** Sem esta guarda, `_calcular_rcl_puro([])`
        # devolvia zeros e a materialização gravava `rcl_12m = 0` — ausência virando
        # número, no denominador de quase todo limite da LRF. Eram 32 linhas em 8 entes,
        # e a reconciliação com o RGF as expôs: o ente publicava R$ 57 milhões onde a
        # plataforma dizia zero.
        #
        # Recusar é a única saída honesta: uma RCL zero não existe na realidade fiscal.
        raise AppError(
            status=404,
            title="RREO sem o Anexo 03",
            detail=(
                f"A entrega {versao} do RREO de {cod_ibge} em {periodo} não traz o Anexo 03 "
                f"(Receita Corrente Líquida). Sem ele não há RCL a apurar — e apurar zero "
                f"seria afirmar que o ente não arrecadou."
            ),
        )
    resultado = _calcular_rcl_puro(linhas)
    memoria: dict[str, Any] = {
        "receita_corrente": str(resultado.receita_corrente),
        "deducoes_total": str(resultado.deducoes_total),
        "componentes": [{"conta": c.conta, "valor": str(c.valor)} for c in resultado.componentes],
        "deducoes": [{"conta": d.conta, "valor": str(d.valor)} for d in resultado.deducoes],
        "source_ref": {"relatorio": "RREO", "anexo": _ANEXO_RCL, "periodo": periodo,
                       "versao_entrega": versao},
    }
    repository.upsert_fato_rcl(
        session,
        {
            "cod_ibge": cod_ibge,
            "periodo_ref": periodo,
            "rcl_12m": resultado.rcl_12m,
            "deducoes": resultado.deducoes_total,
            "receita_corrente": resultado.receita_corrente,
            "versao_entrega": versao,
            "memoria": memoria,
        },
    )
    fato = repository.get_fato_rcl(
        session, cod_ibge=cod_ibge, periodo_ref=periodo, versao_entrega=versao
    )
    assert fato is not None
    return fato


def _ensure_fato_rcl(
    session: Session, cod_ibge: str, periodo: str, versao: str, as_of: datetime | None
) -> FatoRcl:
    fato = repository.get_fato_rcl(
        session, cod_ibge=cod_ibge, periodo_ref=periodo, versao_entrega=versao
    )
    return fato if fato is not None else calcular_rcl(session, cod_ibge, periodo, as_of=as_of)


def obter_fato_rcl(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> FatoRcl:
    """Resolve a versão do RREO e garante ``fato_rcl`` (calcula se ausente); reusável."""
    versao = repository.resolve_versao_rreo(
        session, cod_ibge=cod_ibge, periodo=periodo, as_of=as_of
    )
    if versao is None:
        raise ausencia_de_entrega(
            session,
            cod_ibge=cod_ibge,
            relatorio="RREO",
            periodo=periodo,
            title="RREO ausente",
            detail=f"Sem RREO vigente para {cod_ibge} em {periodo}.",
        )
    return _ensure_fato_rcl(session, cod_ibge, periodo, versao, as_of)


def build_rcl_response(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> RclResponse:
    """Monta a resposta de RCL com memória de cálculo e drill DOWN."""
    versao = repository.resolve_versao_rreo(
        session, cod_ibge=cod_ibge, periodo=periodo, as_of=as_of
    )
    if versao is None:
        raise ausencia_de_entrega(
            session,
            cod_ibge=cod_ibge,
            relatorio="RREO",
            periodo=periodo,
            title="RREO ausente",
            detail=f"Sem RREO vigente para {cod_ibge} em {periodo}.",
        )
    fato = _ensure_fato_rcl(session, cod_ibge, periodo, versao, as_of)
    memoria = fato.memoria or {}
    componentes = [
        ComponenteOut(conta=c["conta"], valor=Decimal(c["valor"]))
        for c in memoria.get("componentes", [])
    ]
    deducoes = [
        ComponenteOut(conta=d["conta"], valor=Decimal(d["valor"]))
        for d in memoria.get("deducoes", [])
    ]
    return RclResponse(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao,
        as_of=as_of,
        rcl_12m=fato.rcl_12m,
        receita_corrente=fato.receita_corrente or Decimal(0),
        deducoes_total=fato.deducoes or Decimal(0),
        componentes=componentes,
        deducoes=deducoes,
        source_ref=_source_ref(periodo, versao),
    )


def classificar_limite(
    session: Session,
    cod_ibge: str,
    periodo: str,
    indicador: str,
    valor_rs: Decimal,
    *,
    poder: str = "",
    as_of: datetime | None = None,
    source_ref: SourceRef | None = None,
    source_components: Sequence[SourceRef] | None = None,
) -> IndicadorOut:
    """Cruza valor, RCL e limite, preservando a linhagem bitemporal da apuração.

    Indicadores derivados de mais de uma entrega recebem uma chave histórica
    ``cmp:v1:<sha256>``. Para manter compatibilidade com consumidores que resolvem
    a versão vigente pelo RREO, uma projeção pela versão simples do RREO é gravada
    somente quando ``as_of`` não foi informado. Uma consulta histórica nunca
    retrocede essa projeção vigente.
    """
    ente = catalog_service.refresh_dim_ente(session, cod_ibge)
    if ente is None or ente.esfera is None:
        raise AppError(
            status=422, title="Esfera desconhecida",
            detail=f"dim_ente sem esfera para {cod_ibge} (ingerir siconfi_entes).",
        )
    limite_row = catalog_repo.get_limite(
        session, indicador=indicador, esfera=ente.esfera, poder=poder
    )
    if limite_row is None:
        raise AppError(
            status=404, title="Limite não cadastrado",
            detail=f"Sem limite para {indicador}/{ente.esfera}/{poder or '-'}.",
        )
    versao = repository.resolve_versao_rreo(
        session, cod_ibge=cod_ibge, periodo=periodo, as_of=as_of
    )
    if versao is None:
        raise AppError(status=404, title="RREO ausente", detail="Sem RREO para calcular a RCL.")
    fato = _ensure_fato_rcl(session, cod_ibge, periodo, versao, as_of)

    limite = LimiteLegal(
        indicador=indicador,
        esfera=ente.esfera,
        poder=poder,
        sentido=limite_row.sentido,
        teto_pct=limite_row.teto_pct,
        alerta_pct=limite_row.alerta_pct,
        prudencial_pct=limite_row.prudencial_pct,
    )
    calc = calcular_indicador_sobre_rcl(valor_rs, fato.rcl_12m, limite)
    return _persistir_indicador(
        session,
        cod_ibge=cod_ibge,
        periodo=periodo,
        indicador=indicador,
        esfera=ente.esfera,
        valor_rs=calc.valor_rs,
        valor_pct=calc.valor_pct_rcl,
        faixa=calc.faixa,
        teto_pct=calc.teto_pct,
        denominador="rcl",
        base_valor=fato.rcl_12m,
        versao=versao,
        as_of=as_of,
        source_ref=source_ref or _source_ref(periodo, versao),
        source_components=source_components,
    )


def _persistir_indicador(
    session: Session,
    *,
    cod_ibge: str,
    periodo: str,
    indicador: str,
    esfera: str,
    valor_rs: Decimal,
    valor_pct: Decimal | None,
    faixa: str | None,
    teto_pct: Decimal | None,
    denominador: str,
    base_valor: Decimal | None,
    versao: str,
    as_of: datetime | None,
    source_ref: SourceRef,
    source_components: Sequence[SourceRef] | None,
) -> IndicadorOut:
    """Grava a linha do mart e devolve o contrato — comum a todos os denominadores."""
    persisted_source = {
        **source_ref.model_dump(mode="json"),
        "indicador": indicador,
        "esfera": esfera,
    }
    valores = {
        "cod_ibge": cod_ibge,
        "periodo": periodo,
        "indicador": indicador,
        "valor_rs": valor_rs,
        "valor_pct_rcl": valor_pct,
        "faixa": faixa,
        "teto_pct": teto_pct,
        "denominador": denominador,
        "base_valor": base_valor,
    }
    persisted_version = versao
    componentes = tuple(source_components or ())
    if componentes:
        persisted_version = composite_version_key(componentes)
        lineage = {
            "source_refs_componentes": [
                ref.model_dump(mode="json", exclude_none=True) for ref in componentes
            ],
            "chave_versao_composta": persisted_version,
        }
        repository.upsert_mart_indicador(
            session,
            {
                **valores,
                "source_ref": {
                    **persisted_source,
                    **lineage,
                    "tipo_registro": "historico_composto",
                },
                "versao_entrega": persisted_version,
            },
        )
        if as_of is None:
            repository.upsert_mart_indicador(
                session,
                {
                    **valores,
                    "source_ref": {
                        **persisted_source,
                        **lineage,
                        "tipo_registro": "projecao_vigente",
                    },
                    "versao_entrega": versao,
                },
            )
    else:
        repository.upsert_mart_indicador(
            session,
            {
                **valores,
                "source_ref": persisted_source,
                "versao_entrega": versao,
            },
        )
    return IndicadorOut(
        cod_ibge=cod_ibge,
        periodo=periodo,
        indicador=indicador,
        esfera=esfera,
        valor_rs=valor_rs,
        valor_pct_rcl=valor_pct,
        faixa=faixa,
        teto_pct=teto_pct,
        versao_entrega=persisted_version,
        source_ref=source_ref,
        denominador=denominador,
        base_valor=base_valor,
    )


def registrar_indicador_gerencial(
    session: Session,
    cod_ibge: str,
    periodo: str,
    indicador: str,
    *,
    valor_rs: Decimal,
    valor_pct: Decimal | None,
    denominador: str,
    base_valor: Decimal | None,
    versao_entrega: str,
    as_of: datetime | None = None,
    source_ref: SourceRef,
    source_components: Sequence[SourceRef] | None = None,
) -> IndicadorOut:
    """Materializa um indicador **sem limite legal** (Sprint 25D).

    Investimento sobre a RCL, RCL por habitante e resultado primário sobre a RCL medem
    gestão, não conformidade: a lei não fixa teto nem piso para eles. A linha vai ao mart
    com ``faixa`` e ``teto_pct`` nulos — o semáforo e o motor de alertas, que só falam
    quando há faixa, continuam em silêncio, e o benchmarking passa a ter o que comparar.
    """
    ente = catalog_service.refresh_dim_ente(session, cod_ibge)
    if ente is None or ente.esfera is None:
        raise AppError(
            status=422, title="Esfera desconhecida",
            detail=f"dim_ente sem esfera para {cod_ibge} (ingerir siconfi_entes).",
        )
    return _persistir_indicador(
        session,
        cod_ibge=cod_ibge,
        periodo=periodo,
        indicador=indicador,
        esfera=ente.esfera,
        valor_rs=valor_rs,
        valor_pct=valor_pct,
        faixa=None,
        teto_pct=None,
        denominador=denominador,
        base_valor=base_valor,
        versao=versao_entrega,
        as_of=as_of,
        source_ref=source_ref,
        source_components=source_components,
    )


def classificar_sobre_base(
    session: Session,
    cod_ibge: str,
    periodo: str,
    indicador: str,
    valor_rs: Decimal,
    base_valor: Decimal,
    *,
    denominador: str,
    versao_entrega: str,
    poder: str = "",
    as_of: datetime | None = None,
    source_ref: SourceRef,
    source_components: Sequence[SourceRef] | None = None,
) -> IndicadorOut:
    """Classifica um indicador cuja base **não é a RCL** e o materializa no mart.

    É o caminho dos mínimos constitucionais (CF art. 198 e 212; LC 141/2012; Lei
    14.113/2020): o denominador é a receita de impostos e transferências — ou, no
    FUNDEB, as receitas principais do fundo. O cálculo do percentual e a faixa
    continuam vindo de ``indicators.limites`` (fonte única de verdade, §7); o que muda
    é de onde vem o 100%, e isso viaja gravado na linha (``denominador``/``base_valor``)
    para que nenhum consumidor rotule ASPS como percentual da RCL.
    """
    ente = catalog_service.refresh_dim_ente(session, cod_ibge)
    if ente is None or ente.esfera is None:
        raise AppError(
            status=422, title="Esfera desconhecida",
            detail=f"dim_ente sem esfera para {cod_ibge} (ingerir siconfi_entes).",
        )
    limite_row = catalog_repo.get_limite(
        session, indicador=indicador, esfera=ente.esfera, poder=poder
    )
    if limite_row is None:
        raise AppError(
            status=404, title="Limite não cadastrado",
            detail=f"Sem limite para {indicador}/{ente.esfera}/{poder or '-'}.",
        )
    if base_valor <= 0:
        raise AppError(
            status=422,
            title="Base inválida",
            detail=f"O denominador '{denominador}' de {indicador} deve ser positivo.",
        )
    limite = LimiteLegal(
        indicador=indicador,
        esfera=ente.esfera,
        poder=poder,
        sentido=limite_row.sentido,
        teto_pct=limite_row.teto_pct,
        alerta_pct=limite_row.alerta_pct,
        prudencial_pct=limite_row.prudencial_pct,
    )
    pct = valor_rs / base_valor * Decimal(100)
    return _persistir_indicador(
        session,
        cod_ibge=cod_ibge,
        periodo=periodo,
        indicador=indicador,
        esfera=ente.esfera,
        valor_rs=valor_rs,
        valor_pct=pct,
        faixa=classificar_faixa(pct, limite),
        teto_pct=limite.teto_pct,
        denominador=denominador,
        base_valor=base_valor,
        versao=versao_entrega,
        as_of=as_of,
        source_ref=source_ref,
        source_components=source_components,
    )
