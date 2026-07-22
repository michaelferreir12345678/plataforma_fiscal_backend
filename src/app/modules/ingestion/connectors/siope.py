"""Conector da API OData oficial SIOPE/FNDE (educacao).

Usa ``Indicadores_Siope`` em long format. ``COD_INDI`` e a chave canonica do
indicador; ``VAL_INDI`` e convertido diretamente para Decimal. O recurso e apenas
enriquecimento — os minimos MDE/FUNDEB continuam calculados do RREO Anexo 8.

Nota para evolucao da arvore por subfuncao: em ``Despesas_Funcao_Educacao_Siope``
ha subtotais (por exemplo, NUM_ORDE 13 = ordens 11 + 12 para subfuncao 365). Esse
recurso nao e ingerido aqui para evitar dupla contagem acidental.
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.modules.ingestion import repository
from app.modules.ingestion.connectors._parsing import first, num
from app.modules.ingestion.models import FONTE_SIOPE, SiopeEducacao
from app.shared.ingestion.base import BaseConnector, IngestionJob, capture_versao

UF_POR_PREFIXO_IBGE = {
    "11": "RO",
    "12": "AC",
    "13": "AM",
    "14": "RR",
    "15": "PA",
    "16": "AP",
    "17": "TO",
    "21": "MA",
    "22": "PI",
    "23": "CE",
    "24": "RN",
    "25": "PB",
    "26": "PE",
    "27": "AL",
    "28": "SE",
    "29": "BA",
    "31": "MG",
    "32": "ES",
    "33": "RJ",
    "35": "SP",
    "41": "PR",
    "42": "SC",
    "43": "RS",
    "50": "MS",
    "51": "MT",
    "52": "GO",
    "53": "DF",
}

_SELECT = (
    "TIPO,NUM_ANO,NUM_PERI,COD_UF,SIG_UF,COD_MUNI,NOM_MUNI,"
    "COD_INDI,COD_EXIB,NOM_INDI,COD_GRUP,NOM_GRUP_INDI,VAL_INDI"
)


def _fim_bimestre(ano: int, bimestre: int) -> date:
    mes = bimestre * 2
    return date(ano, mes, calendar.monthrange(ano, mes)[1])


def _uf(cod_ibge: str) -> str:
    try:
        return UF_POR_PREFIXO_IBGE[cod_ibge[:2]]
    except KeyError as exc:
        raise ValueError(f"Prefixo IBGE desconhecido para o SIOPE: {cod_ibge!r}") from exc


class SiopeConnector(BaseConnector):
    fonte = FONTE_SIOPE
    relatorio = "SIOPE"
    cadencia = "bimestral"

    def discover(self, state: dict[str, Any]) -> list[IngestionJob]:
        entes = list(dict.fromkeys(str(cod).strip() for cod in state.get("entes") or []))
        if not entes:
            return []
        anos: list[int] = state.get("anos") or [date.today().year]
        bimestres: list[int] = state.get("periodos") or list(range(1, 7))
        invalidos = sorted(set(bimestres) - set(range(1, 7)))
        if invalidos:
            raise ValueError(f"Periodos SIOPE invalidos: {invalidos}; use valores de 1 a 6")
        versao = state.get("versao") or capture_versao()

        return [
            IngestionJob(
                fonte=self.fonte,
                relatorio=self.relatorio,
                cod_ibge="BR",
                ano=ano,
                periodo=f"{ano}-B{bimestre}",
                versao=versao,
                homologada_em=state.get("homologada_em"),
                valid_time=_fim_bimestre(ano, bimestre),
                params={"num": bimestre, "entes": entes},
            )
            for ano in anos
            for bimestre in bimestres
        ]

    def extract(self, job: IngestionJob) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        bimestre = int(job.params["num"])
        for cod_ibge in job.params["entes"]:
            sigla = _uf(cod_ibge)
            if len(cod_ibge) == 2:
                filtro = "TIPO eq 'Estadual'"
            elif len(cod_ibge) == 7 and cod_ibge.isdigit():
                # O SIOPE publica COD_MUNI com seis digitos, sem o digito verificador.
                filtro = f"COD_MUNI eq {int(cod_ibge[:6])}"
            else:
                raise ValueError(
                    f"Codigo IBGE SIOPE invalido: {cod_ibge!r}; "
                    "use UF com 2 ou municipio com 7 digitos"
                )
            path = (
                f"Indicadores_Siope(Ano_Consulta={job.ano},Num_Peri={bimestre},"
                f"Sig_UF='{sigla}')"
            )
            params = {
                "$filter": filtro,
                "$select": _SELECT,
                "$format": "json",
            }
            for item in self.client.get_records(path, params):
                records.append({**item, "_cod_ibge": cod_ibge})
        return records

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        bimestre = int(job.params["num"])
        rows = []
        for item in payload:
            codigo = first(item, "COD_INDI", "cod_indi", "indicador_codigo")
            cod_ibge = item.get("_cod_ibge")
            if codigo in (None, "") or not cod_ibge:
                continue
            descricao = first(item, "NOM_INDI", "nom_indi", "indicador_descricao")
            rows.append(
                {
                    "cod_ibge": str(cod_ibge),
                    "ano": job.ano,
                    "bimestre": bimestre,
                    "indicador_codigo": str(codigo).strip(),
                    "indicador_descricao": descricao,
                    "unidade": (
                        "percentual" if "PERCENTUAL" in str(descricao or "").upper() else None
                    ),
                    "valor": num(first(item, "VAL_INDI", "val_indi", "valor")),
                    "valid_time": job.valid_time,
                    "versao_entrega": versao_entrega,
                }
            )
        total = 0
        for cod_ibge in job.params["entes"]:
            rows_ente = [row for row in rows if row["cod_ibge"] == cod_ibge]
            total += repository.replace_silver_rows(
                session,
                SiopeEducacao,
                keys={
                    "cod_ibge": cod_ibge,
                    "ano": job.ano,
                    "bimestre": bimestre,
                    "versao_entrega": versao_entrega,
                },
                rows=rows_ente,
            )
        return total


CONNECTORS: dict[str, type[BaseConnector]] = {FONTE_SIOPE: SiopeConnector}
