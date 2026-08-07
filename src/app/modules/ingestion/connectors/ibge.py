"""Conectores IBGE (população e PIB por UF/município). Cadência anual.

Fontes oficiais:
- https://servicodados.ibge.gov.br/api/v3/agregados/{agregado}/...
- https://servicodados.ibge.gov.br/api/v1/pesquisas/38/periodos/{ano}/indicadores/47001/resultados/{cod_ibge}

- População: agregado 6579 (estimativas), variável 9324.
- PIB municipal: agregado 5938, variável 37 (PIB a preços correntes, em mil reais).
- Malha: API de malhas v3, GeoJSON municipal consolidado por UF.
- PIB per capita: API de Pesquisas v1, pesquisa 38, indicador leaf 47001 (reais por
  habitante). O valor é consumido diretamente da fonte; não se combinam anos de PIB
  e população. A variável 513 do agregado 5938 é VAB agropecuário, não PIB per capita.
Consome: Sprint 2 (dim_ente: população/PIB por ano) e Sprint 13 (coortes por porte/PIB).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast

import httpx
from sqlalchemy.orm import Session

from app.modules.dashboard import estadual_repository
from app.modules.ingestion import repository
from app.modules.ingestion.connectors._parsing import num
from app.modules.ingestion.models import (
    FONTE_IBGE_MALHA,
    FONTE_IBGE_PIB,
    FONTE_IBGE_POPULACAO,
    IbgePib,
    IbgePopulacao,
    RawPayload,
)
from app.shared.ingestion.base import BaseConnector, IngestionJob, capture_versao

MALHA_ANO_REFERENCIA = 2022
MALHA_QUALIDADE_PADRAO = "minima"


def _capture_versao_malha() -> str:
    """Uma captura distinta por execução; a API de malhas não publica versão."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _uf_do_codigo(cod_ibge: str) -> str:
    """Converte ente estadual ou municipal na UF que possui uma única malha."""
    codigo = str(cod_ibge).strip()
    if codigo.isdigit() and len(codigo) == 2:
        return codigo
    if codigo.isdigit() and len(codigo) == 7:
        return codigo[:2]
    raise ValueError(
        f"Código IBGE inválido para malha territorial: {cod_ibge!r}. "
        "Use 2 dígitos para UF ou 7 para município."
    )


def _features_malha(payload: Any, uf: str) -> list[dict[str, Any]]:
    """Valida o contrato mínimo necessário para ligar polígonos aos municípios."""
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise ValueError(f"Malha da UF {uf} não é um GeoJSON FeatureCollection.")
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise ValueError(f"Malha da UF {uf} não contém polígonos municipais.")
    codigos: set[str] = set()
    for feature in features:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        codarea = str(properties.get("codarea") or "") if isinstance(properties, dict) else ""
        if len(codarea) != 7 or not codarea.isdigit() or not codarea.startswith(uf):
            raise ValueError(f"Malha da UF {uf} contém código municipal incompatível: {codarea!r}.")
        if codarea in codigos:
            raise ValueError(f"Malha da UF {uf} repete o município {codarea}.")
        codigos.add(codarea)
        geometry = feature.get("geometry") if isinstance(feature, dict) else None
        geometry_type = geometry.get("type") if isinstance(geometry, dict) else None
        coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
        if (
            geometry_type not in {"Polygon", "MultiPolygon"}
            or not isinstance(coordinates, list)
            or not coordinates
        ):
            raise ValueError(
                f"Malha da UF {uf} contém geometria municipal inválida para {codarea!r}."
            )
    return features


def _conteudo_malha(
    payload: Any,
    uf: str,
    *,
    qualidade_fallback: str = MALHA_QUALIDADE_PADRAO,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """Lê o envelope bronze novo e continua aceitando GeoJSON legado em replay."""
    if isinstance(payload, dict) and "geojson" in payload:
        geojson = payload.get("geojson")
        qualidade = str(payload.get("qualidade") or qualidade_fallback).strip().lower()
    else:
        geojson = payload
        qualidade = qualidade_fallback
    if qualidade not in {"minima", "intermediaria"}:
        raise ValueError(f"Qualidade inválida no bronze da malha da UF {uf}: {qualidade!r}.")
    features = _features_malha(geojson, uf)
    return cast(dict[str, Any], geojson), features, qualidade


def _nivel_territorial(cod_ibge: str) -> str:
    """Traduz o código canônico da plataforma para o nível da API de Agregados."""
    codigo = str(cod_ibge).strip()
    if codigo.isdigit() and len(codigo) == 2:
        return "N3"  # Unidade da Federação
    if codigo.isdigit() and len(codigo) == 7:
        return "N6"  # Município
    raise ValueError(
        f"Código IBGE inválido para agregação territorial: {cod_ibge!r}. "
        "Use 2 dígitos para UF ou 7 para município."
    )


class IbgeConnectorBase(BaseConnector):
    """Base IBGE: um job por (ente, ano); extract nos agregados v3."""

    agregado: int
    variaveis: str

    def discover(self, state: dict[str, Any]) -> list[IngestionJob]:
        entes: list[str] = state.get("entes") or []
        anos: list[int] = state.get("anos") or [date.today().year - 1]
        versao = state.get("versao") or capture_versao()
        jobs: list[IngestionJob] = []
        for cod_ibge in entes:
            for ano in anos:
                jobs.append(
                    IngestionJob(
                        fonte=self.fonte,
                        relatorio=self.relatorio,
                        cod_ibge=cod_ibge,
                        ano=ano,
                        periodo=f"{ano}",
                        versao=versao,
                        homologada_em=state.get("homologada_em"),
                        valid_time=date(ano, 12, 31),
                        params={"ano": ano, "cod_ibge": cod_ibge},
                    )
                )
        return jobs

    def extract(self, job: IngestionJob) -> Any:
        ano = job.params["ano"]
        cod_ibge = job.params["cod_ibge"]
        nivel = _nivel_territorial(cod_ibge)
        path = f"v3/agregados/{self.agregado}/periodos/{ano}/variaveis/{self.variaveis}"
        return self.client.get_records(path, {"localidades": f"{nivel}[{cod_ibge}]"})


class IbgePopulacaoConnector(IbgeConnectorBase):
    fonte = FONTE_IBGE_POPULACAO
    relatorio = "IBGE-POP"
    agregado = 6579
    variaveis = "9324"

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        # payload já vem achatado: [{variavel, cod_ibge, ano, valor}]
        valores = (num(r.get("valor")) for r in payload if r.get("valor") not in (None, ""))
        valor = next(valores, None)
        if valor is None:
            return repository.replace_silver_rows(
                session,
                IbgePopulacao,
                keys={
                    "cod_ibge": job.cod_ibge,
                    "ano_ref": job.ano,
                    "versao_entrega": versao_entrega,
                },
                rows=[],
            )
        rows = [
            {
                "cod_ibge": job.cod_ibge,
                "ano_ref": job.ano,
                "populacao": int(valor) if valor is not None else None,
                "fonte": "estimativa",
                "valid_time": job.valid_time,
                "versao_entrega": versao_entrega,
            }
        ]
        return repository.replace_silver_rows(
            session,
            IbgePopulacao,
            keys={"cod_ibge": job.cod_ibge, "ano_ref": job.ano, "versao_entrega": versao_entrega},
            rows=rows,
        )


class IbgeMalhaConnector(BaseConnector):
    """Uma entrega GeoJSON por UF; anos e municípios repetidos não multiplicam chamadas."""

    fonte = FONTE_IBGE_MALHA
    relatorio = "IBGE-MALHA"

    def discover(self, state: dict[str, Any]) -> list[IngestionJob]:
        ufs = list(dict.fromkeys(_uf_do_codigo(cod) for cod in (state.get("entes") or [])))
        versao = state.get("versao") or _capture_versao_malha()
        qualidade = str(state.get("qualidade") or MALHA_QUALIDADE_PADRAO).strip().lower()
        if qualidade not in {"minima", "intermediaria"}:
            raise ValueError("Qualidade da malha deve ser 'minima' ou 'intermediaria'.")
        return [
            IngestionJob(
                fonte=self.fonte,
                relatorio=self.relatorio,
                cod_ibge=uf,
                ano=MALHA_ANO_REFERENCIA,
                periodo=str(MALHA_ANO_REFERENCIA),
                versao=versao,
                homologada_em=state.get("homologada_em"),
                valid_time=date(MALHA_ANO_REFERENCIA, 12, 31),
                params={
                    "uf": uf,
                    "qualidade": qualidade,
                },
            )
            for uf in ufs
        ]

    def extract(self, job: IngestionJob) -> Any:
        uf = job.params["uf"]
        qualidade = job.params["qualidade"]
        get_document = getattr(self.client, "get_document", None)
        if not callable(get_document):
            raise TypeError("Cliente IBGE da malha precisa preservar o documento GeoJSON.")
        payload = get_document(
            f"v3/malhas/estados/{uf}",
            {
                "periodo": str(MALHA_ANO_REFERENCIA),
                "intrarregiao": "municipio",
                "formato": "application/vnd.geo+json",
                "qualidade": qualidade,
            },
        )
        _features_malha(payload, uf)
        return {"geojson": payload, "qualidade": qualidade}

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        # ``force`` significa reprocessar o bronze imutável, não promover uma resposta
        # nova da rede sob a mesma versão. Assim gold e replay nunca divergem do raw.
        bronze = session.get(RawPayload, job.chave)
        if bronze is not None:
            payload = bronze.payload
        qualidade_fallback = str(job.params.get("qualidade") or MALHA_QUALIDADE_PADRAO)
        geojson, features, qualidade = _conteudo_malha(
            payload,
            job.cod_ibge,
            qualidade_fallback=qualidade_fallback,
        )
        # Replay histórico materializa o payload antigo, mas nunca pode rebaixar o mapa
        # servido pela aplicação. Só a entrega vigente atualiza a projeção gold.
        vigente = repository.resolve_versao(
            session,
            cod_ibge=job.cod_ibge,
            relatorio=self.relatorio,
            periodo=job.periodo,
        )
        if vigente != versao_entrega:
            return 0
        # A completude é comparada ao cadastro atual apenas para a projeção vigente.
        # Versões históricas podem legitimamente anteceder a criação de um município.
        codigos_recebidos = {str(feature["properties"]["codarea"]) for feature in features}
        codigos_conhecidos = set(estadual_repository.list_ibges_por_prefixo(session, job.cod_ibge))
        faltantes = sorted(codigos_conhecidos - codigos_recebidos)
        if faltantes:
            amostra = ", ".join(faltantes[:5])
            raise ValueError(
                f"Malha da UF {job.cod_ibge} está incompleta: faltam "
                f"{len(faltantes)} município(s) conhecidos (ex.: {amostra})."
            )
        estadual_repository.upsert_malha(
            session,
            {
                "uf": job.cod_ibge,
                "formato": "geojson",
                "malha": geojson,
                "simplificacao": qualidade,
                "fonte": "IBGE — API de malhas v3",
                "ano": MALHA_ANO_REFERENCIA,
                "n_areas": len(features),
            },
        )
        return 1


class IbgePibConnector(IbgeConnectorBase):
    fonte = FONTE_IBGE_PIB
    relatorio = "IBGE-PIB"
    agregado = 5938
    variaveis = "37"  # PIB a preços correntes; unidade oficial do agregado = mil reais
    pesquisa_pib = 38
    indicador_pib_per_capita = 47001

    def extract(self, job: IngestionJob) -> Any:
        """Extrai PIB nominal e per capita oficiais para o mesmo ente/ano.

        O agregado 5938 e a Pesquisa 38 têm contratos JSON diferentes, mas o cliente
        IBGE normaliza ambos para registros planos. Os dois payloads permanecem juntos
        no bronze, tornando a origem de cada coluna reproduzível na mesma entrega.

        O PIB municipal tem defasagem de anos: para um exercício que a pesquisa ainda
        **não publicou**, a API responde 4xx. Isso é lacuna da fonte, não erro de
        requisição — o per capita fica vazio e o PIB nominal (se houver) é preservado.
        Erros que não sejam "período inexistente" continuam subindo.
        """
        pib_nominal = super().extract(job)
        ano = job.params["ano"]
        cod_ibge = job.params["cod_ibge"]
        # O indicador 47001 da Pesquisa 38 é municipal. Para UF, o agregado 5938
        # fornece o PIB nominal oficial e o per capita permanece ausente, sem derivá-lo.
        if _nivel_territorial(cod_ibge) == "N3":
            return {
                "pib_nominal_agregado_5938_variavel_37": pib_nominal,
                "pib_per_capita_pesquisa_38_indicador_47001": [],
            }
        per_capita_path = (
            f"v1/pesquisas/{self.pesquisa_pib}/periodos/{ano}/indicadores/"
            f"{self.indicador_pib_per_capita}/resultados/{cod_ibge}"
        )
        try:
            pib_per_capita = self.client.get_records(per_capita_path, {})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (400, 404):
                raise
            pib_per_capita = []
        return {
            "pib_nominal_agregado_5938_variavel_37": pib_nominal,
            "pib_per_capita_pesquisa_38_indicador_47001": pib_per_capita,
        }

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        # Compatibilidade de replay: bronzes anteriores continham apenas a lista do
        # agregado 5938. Eles continuam legíveis, mas não fabricam PIB per capita.
        if isinstance(payload, dict):
            nominal_records = payload.get("pib_nominal_agregado_5938_variavel_37", [])
            per_capita_records = payload.get(
                "pib_per_capita_pesquisa_38_indicador_47001", []
            )
        else:
            nominal_records = payload if isinstance(payload, list) else []
            per_capita_records = []

        pib_nominal = _valor_ibge(nominal_records, codigo="37", ano=job.ano)
        pib_per_capita = _valor_ibge(
            per_capita_records,
            codigo=str(self.indicador_pib_per_capita),
            ano=job.ano,
        )
        if pib_nominal is None and pib_per_capita is None:
            return repository.replace_silver_rows(
                session,
                IbgePib,
                keys={
                    "cod_ibge": job.cod_ibge,
                    "ano_ref": job.ano,
                    "versao_entrega": versao_entrega,
                },
                rows=[],
            )
        rows = [
            {
                "cod_ibge": job.cod_ibge,
                "ano_ref": job.ano,
                "pib_nominal": pib_nominal,
                "pib_per_capita": pib_per_capita,
                "valid_time": job.valid_time,
                "versao_entrega": versao_entrega,
            }
        ]
        return repository.replace_silver_rows(
            session,
            IbgePib,
            keys={"cod_ibge": job.cod_ibge, "ano_ref": job.ano, "versao_entrega": versao_entrega},
            rows=rows,
        )


def _valor_ibge(records: Any, *, codigo: str, ano: int) -> Decimal | None:
    """Seleciona um valor do indicador e ano pedidos sem cruzar exercícios."""
    if not isinstance(records, list):
        return None
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("variavel")) != codigo or str(record.get("ano")) != str(ano):
            continue
        valor = num(record.get("valor"))
        if valor is not None:
            return valor
    return None


CONNECTORS: dict[str, type[BaseConnector]] = {
    FONTE_IBGE_POPULACAO: IbgePopulacaoConnector,
    FONTE_IBGE_PIB: IbgePibConnector,
    FONTE_IBGE_MALHA: IbgeMalhaConnector,
}
