"""Conectores SICONFI (fonte primária): RREO, RGF, DCA, MSC, extratos, entes.

Cada conector implementa o contrato ``BaseConnector`` (§6.7). O ``extract`` usa o
``RecordsClient`` injetado (real = SICONFI; nos testes, um cliente falso). O ``discover``
enumera jobs a partir de um estado (entes/anos/períodos/versão). O ``to_silver`` faz o
parsing tipado e substitui as linhas da versão.

Base legal/fonte: https://apidatalake.tesouro.gov.br/ords/siconfi/ (JSON público).
"""

from __future__ import annotations

import calendar
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.ingestion import repository
from app.modules.ingestion.connectors._parsing import boolean as _boolean
from app.modules.ingestion.connectors._parsing import first as _first
from app.modules.ingestion.connectors._parsing import num as _num
from app.modules.ingestion.models import (
    FONTE_DCA,
    FONTE_ENTES,
    FONTE_EXTRATOS,
    FONTE_MSC,
    FONTE_RGF,
    FONTE_RREO,
    SilverDca,
    SilverEnte,
    SilverExtrato,
    SilverMsc,
    SilverRgf,
    SilverRreo,
)
from app.shared import periodo as periodo_util
from app.shared.ingestion.base import BaseConnector, IngestionJob


def _period_end(ano: int, month: int) -> date:
    last_day = calendar.monthrange(ano, month)[1]
    return date(ano, month, last_day)


def valid_time_from_periodo(periodo: str) -> date | None:
    """Deriva a data-fim do período canônico ("2024", "2024-B6", "2024-Q2", "2024-M07")."""
    try:
        ano = int(periodo[:4])
    except (ValueError, IndexError):
        return None
    if "-" not in periodo:
        return _period_end(ano, 12)
    marca = periodo.split("-", 1)[1]
    tipo, num = marca[0], marca[1:]
    try:
        n = int(num)
    except ValueError:
        return _period_end(ano, 12)
    if tipo == "B":  # bimestre
        return _period_end(ano, min(n * 2, 12))
    if tipo == "Q":  # quadrimestre
        return _period_end(ano, min(n * 4, 12))
    if tipo == "S":  # semestre (RGF facultativo p/ municípios < 50 mil hab. — LRF art. 63)
        return _period_end(ano, min(n * 6, 12))
    if tipo == "M":  # mensal
        return _period_end(ano, min(max(n, 1), 12))
    return _period_end(ano, 12)


def esfera_siconfi(cod_ibge: str) -> str:
    """Esfera do ente no vocabulário da API (``co_esfera``): UF de 2 dígitos ⇒ ``E``."""
    cod = str(cod_ibge).strip()
    if cod == "1":  # União
        return "U"
    return "E" if len(cod) == 2 else "M"


class SiconfiConnectorBase(BaseConnector):
    """Base dos conectores SICONFI: extract paginado + discover por ente/ano/período."""

    path: str
    default_periodos: tuple[int, ...] = (1,)
    #: Tipo do período na notação canônica (§6.6). ``None`` = o conector não é
    #: periódico dentro do exercício (DCA é anual) e não sofre o corte de calendário.
    tipo_periodo: str | None = None

    def extract(self, job: IngestionJob) -> Any:
        return self.client.get_records(self.path, job.params)

    # Cada conector especializa a construção do job (período/params/valid_time).
    def build_job(
        self,
        cod_ibge: str,
        ano: int,
        periodo_num: int,
        versao: str,
        homologada_em: Any,
    ) -> IngestionJob:
        raise NotImplementedError

    def periodos_do_ano(self, ano: int, explicitos: list[int] | None) -> list[int]:
        """Períodos a buscar em ``ano``.

        Período explícito é respeitado sem discussão — quem pede ``2026-B5`` sabe o que
        quer (retificação, teste, verificação). Sem ele, o exercício **em andamento** só
        rende os períodos que já terminaram: pedir o 6º bimestre de 2026 em julho de 2026
        não é otimismo, é gerar trabalho garantido a vazio e registrar como "ausente" um
        dado que ninguém deveria ter publicado.
        """
        if explicitos:
            return list(explicitos)
        padrao = list(self.default_periodos)
        if self.tipo_periodo is None:
            return padrao
        possiveis = set(periodo_util.periodos_possiveis(ano, self.tipo_periodo, date.today()))
        return [p for p in padrao if p in possiveis]

    def discover(self, state: dict[str, Any]) -> list[IngestionJob]:
        entes: list[str] = state.get("entes") or []
        anos: list[int] = state.get("anos") or []
        explicitos: list[int] | None = state.get("periodos") or None
        versao: str = state.get("versao") or "1"
        homologada_em = state.get("homologada_em")
        jobs: list[IngestionJob] = []
        for cod_ibge in entes:
            for ano in anos:
                for periodo_num in self.periodos_do_ano(ano, explicitos):
                    jobs.append(
                        self.build_job(cod_ibge, ano, periodo_num, versao, homologada_em)
                    )
        return jobs


class _RelatorioMixin:
    """to_silver comum para RREO/RGF/DCA (mesma forma silver)."""

    silver_model: type

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        rows = [
            {
                "cod_ibge": job.cod_ibge,
                "periodo": job.periodo,
                "anexo": _first(item, "no_anexo", "anexo", "co_anexo"),
                "conta": _first(item, "conta", "cod_conta", "co_conta"),
                "cod_conta": _first(item, "cod_conta", "co_conta"),
                "coluna": _first(item, "coluna", "no_coluna"),
                "poder": _first(item, "co_poder", "poder"),
                "linha_seq": seq,
                "valor": _num(_first(item, "valor", "vl_valor")),
                "valid_time": job.valid_time,
                "versao_entrega": versao_entrega,
            }
            for seq, item in enumerate(payload)
        ]
        return repository.replace_silver_rows(
            session,
            self.silver_model,
            keys={
                "cod_ibge": job.cod_ibge,
                "periodo": job.periodo,
                "versao_entrega": versao_entrega,
            },
            rows=rows,
        )


class RreoConnector(_RelatorioMixin, SiconfiConnectorBase):
    fonte = FONTE_RREO
    relatorio = "RREO"
    path = "tt/rreo"
    default_periodos = (1, 2, 3, 4, 5, 6)  # bimestres
    tipo_periodo = "B"
    silver_model = SilverRreo

    def build_job(self, cod_ibge, ano, periodo_num, versao, homologada_em) -> IngestionJob:  # type: ignore[no-untyped-def]
        return IngestionJob(
            fonte=self.fonte,
            relatorio=self.relatorio,
            cod_ibge=cod_ibge,
            ano=ano,
            periodo=f"{ano}-B{periodo_num}",
            versao=versao,
            homologada_em=homologada_em,
            valid_time=_period_end(ano, periodo_num * 2),
            params={
                "an_exercicio": ano,
                "nr_periodo": periodo_num,
                "co_tipo_demonstrativo": "RREO",
                "id_ente": cod_ibge,
            },
        )


class RgfConnector(_RelatorioMixin, SiconfiConnectorBase):
    """RGF quadrimestral (padrão) ou semestral (municípios < 50 mil hab., LRF art. 63).

    O ``co_esfera`` sai do próprio código do ente (UF de 2 dígitos ⇒ estadual) e os
    poderes consultados acompanham a esfera: municípios têm Executivo/Legislativo;
    estados têm também Judiciário, Ministério Público e Defensoria.
    """

    fonte = FONTE_RGF
    relatorio = "RGF"
    path = "tt/rgf"
    default_periodos = (1, 2, 3)  # quadrimestres (semestral usa periodicidade="S" e 1..2)
    tipo_periodo = "Q"
    silver_model = SilverRgf
    poderes_municipais = ("E", "L")
    poderes_estaduais = ("E", "L", "J", "M", "D")

    def extract(self, job: IngestionJob) -> Any:
        """O RGF exige ``co_poder``; busca cada poder e concatena (co_poder vem em cada linha)."""
        esfera = job.params.get("co_esfera", "M")
        poderes = self.poderes_estaduais if esfera == "E" else self.poderes_municipais
        registros: list[Any] = []
        for poder in poderes:
            registros.extend(self.client.get_records(self.path, {**job.params, "co_poder": poder}))
        return registros

    def discover(self, state: dict[str, Any]) -> list[IngestionJob]:
        periodicidade = str(state.get("periodicidade") or "Q").upper()
        if periodicidade == "S" and not state.get("periodos"):
            state = {**state, "periodos": [1, 2]}
        self._periodicidade = periodicidade
        try:
            return super().discover(state)
        finally:
            self._periodicidade = "Q"

    _periodicidade: str = "Q"

    def build_job(self, cod_ibge, ano, periodo_num, versao, homologada_em) -> IngestionJob:  # type: ignore[no-untyped-def]
        periodicidade = self._periodicidade
        meses_por_periodo = 6 if periodicidade == "S" else 4
        return IngestionJob(
            fonte=self.fonte,
            relatorio=self.relatorio,
            cod_ibge=cod_ibge,
            ano=ano,
            periodo=f"{ano}-{periodicidade}{periodo_num}",
            versao=versao,
            homologada_em=homologada_em,
            valid_time=_period_end(ano, periodo_num * meses_por_periodo),
            params={
                "an_exercicio": ano,
                "nr_periodo": periodo_num,
                "in_periodicidade": periodicidade,
                "co_tipo_demonstrativo": "RGF",
                "co_esfera": esfera_siconfi(cod_ibge),
                "id_ente": cod_ibge,
            },
        )


class DcaConnector(_RelatorioMixin, SiconfiConnectorBase):
    fonte = FONTE_DCA
    relatorio = "DCA"
    path = "tt/dca"
    default_periodos = (0,)  # anual
    silver_model = SilverDca

    def build_job(self, cod_ibge, ano, periodo_num, versao, homologada_em) -> IngestionJob:  # type: ignore[no-untyped-def]
        return IngestionJob(
            fonte=self.fonte,
            relatorio=self.relatorio,
            cod_ibge=cod_ibge,
            ano=ano,
            periodo=f"{ano}",
            versao=versao,
            homologada_em=homologada_em,
            valid_time=_period_end(ano, 12),
            params={"an_exercicio": ano, "id_ente": cod_ibge},
        )


class MscConnector(SiconfiConnectorBase):
    """MSC Patrimonial (mensal, por conta PCASP) — a maior tabela do SICONFI.

    A API ``tt/msc_patrimonial`` só devolve dados quando **todos** os filtros são fixados:
    ``an_referencia``/``me_referencia`` (não ``an_exercicio``/``nr_periodo``), mais
    ``co_tipo_matriz=MSCC`` (consolidada), ``classe_conta`` (1..4 = patrimonial) e ``id_tv``
    (tipo de valor). Cada linha vem quebrada por fonte de recurso / poder-órgão e sem
    ``id_tv`` no corpo — por isso o ``extract`` percorre o produto
    ``classe × id_tv`` e **etiqueta** cada linha com o ``id_tv`` da consulta, e o
    ``to_silver`` **agrega por conta contábil** (soma as quebras), guardando a natureza.
    """

    fonte = FONTE_MSC
    relatorio = "MSC"
    path = "tt/msc_patrimonial"
    default_periodos = tuple(range(1, 13))  # mensal
    classes = (1, 2, 3, 4)  # patrimonial: Ativo, Passivo/PL, VPD, VPA
    # tipo de valor → coluna silver alvo.
    tipos_valor = ("beginning_balance", "ending_balance", "period_change")

    def build_job(self, cod_ibge, ano, periodo_num, versao, homologada_em) -> IngestionJob:  # type: ignore[no-untyped-def]
        return IngestionJob(
            fonte=self.fonte,
            relatorio=self.relatorio,
            cod_ibge=cod_ibge,
            ano=ano,
            periodo=f"{ano}-M{periodo_num:02d}",
            versao=versao,
            homologada_em=homologada_em,
            valid_time=_period_end(ano, periodo_num),
            params={
                "an_referencia": ano,
                "me_referencia": periodo_num,
                "co_tipo_matriz": "MSCC",
                "id_ente": cod_ibge,
            },
        )

    def extract(self, job: IngestionJob) -> Any:
        """Percorre ``classe × id_tv``; injeta ``id_tv`` em cada linha (o corpo não o traz)."""
        registros: list[Any] = []
        for classe in self.classes:
            for tv in self.tipos_valor:
                params = {**job.params, "classe_conta": classe, "id_tv": tv}
                for item in self.client.get_records(self.path, params):
                    registros.append({**item, "id_tv": tv})
        return registros

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        # Agrega por conta contábil na convenção **devedor líquido** (débito +, crédito −),
        # somando cada quebra (fonte de recurso, poder/órgão) COM seu sinal. Contas com
        # linhas de naturezas mistas (ex.: redutoras) são corretamente netadas — o saldo do
        # nó vira Σ(débitos) − Σ(créditos), no qual a identidade contábil é exata.
        contas: dict[str, dict[str, Any]] = {}
        for item in payload:
            conta = _first(item, "conta_contabil", "cod_conta", "cod_conta_contabil")
            if not conta:
                continue
            conta = str(conta).strip()
            valor = _num(_first(item, "valor", "vl_saldo")) or Decimal(0)
            natureza = _first(item, "natureza_conta", "natureza")
            nat = str(natureza).strip().upper()[:1] if natureza else "D"
            sinal = valor if nat != "C" else -valor  # devedor líquido
            tv = item.get("id_tv")
            agg = contas.setdefault(
                conta,
                {
                    "cod_conta_pcasp": conta,
                    "saldo_inicial": Decimal(0), "saldo_final": Decimal(0),
                    "mov_devedor": Decimal(0), "mov_credor": Decimal(0),
                },
            )
            if tv == "beginning_balance":
                agg["saldo_inicial"] += sinal
            elif tv == "ending_balance":
                agg["saldo_final"] += sinal
            elif tv == "period_change":
                if nat == "C":
                    agg["mov_credor"] += valor
                else:
                    agg["mov_devedor"] += valor
        rows = [
            {
                "cod_ibge": job.cod_ibge,
                "periodo": job.periodo,
                "cod_conta_pcasp": agg["cod_conta_pcasp"],
                # Natureza resultante do saldo líquido (informativa; exibição usa a da classe).
                "natureza": "C" if agg["saldo_final"] < 0 else "D",
                "saldo_inicial": agg["saldo_inicial"],  # devedor líquido (com sinal)
                "mov_devedor": agg["mov_devedor"],
                "mov_credor": agg["mov_credor"],
                "saldo_final": agg["saldo_final"],  # devedor líquido (com sinal)
                "valid_time": job.valid_time,
                "versao_entrega": versao_entrega,
            }
            for agg in contas.values()
        ]
        return repository.replace_silver_rows(
            session,
            SilverMsc,
            keys={
                "cod_ibge": job.cod_ibge,
                "periodo": job.periodo,
                "versao_entrega": versao_entrega,
            },
            rows=rows,
        )


def relatorio_do_entregavel(
    entregavel: str | None, tipo_relatorio: str | None = None
) -> str | None:
    """Normaliza o ``entregavel`` do extrato para o relatório canônico da plataforma.

    O extrato usa nomes por extenso ("Relatório Resumido de Execução Orçamentária",
    "Relatório de Gestão Fiscal", "Balanço Anual (DCA)", "MSC Agregada/Encerramento"),
    não as siglas — por isso o casamento é feito sobre o texto sem acento.
    """
    bruto = f"{entregavel or ''} {tipo_relatorio or ''}"
    nfkd = unicodedata.normalize("NFKD", bruto)
    texto = "".join(c for c in nfkd if not unicodedata.combining(c)).upper()
    if "MSC" in texto or "MATRIZ DE SALDOS" in texto:
        return "MSC"
    if "RREO" in texto or "RESUMIDO DE EXECUCAO ORCAMENTARIA" in texto:
        return "RREO"
    if "RGF" in texto or "GESTAO FISCAL" in texto:
        return "RGF"
    if "DCA" in texto or "BALANCO ANUAL" in texto or "CONTAS ANUAIS" in texto:
        return "DCA"
    return None


def periodo_canonico(ano: int, periodicidade: str | None, periodo_num: Any) -> str:
    """Período canônico da plataforma a partir de (periodicidade, nº) do extrato."""
    tipo = (periodicidade or "A").strip().upper()[:1]
    try:
        n = int(periodo_num)
    except (TypeError, ValueError):
        n = 0
    if tipo == "A" or n <= 0:
        return str(ano)
    if tipo == "M":
        return f"{ano}-M{n:02d}"
    return f"{ano}-{tipo}{n}"


class ExtratosConnector(SiconfiConnectorBase):
    """Extrato de entregas: cada evento homologado da fonte (dispara retificações).

    O extrato não traz número de versão; a **data de status** identifica o evento e é
    preservada como ``versao``/``homologada_em``. O período é canonizado
    (periodicidade + nº ⇒ ``2024-B6``/``2024-Q2``/``2024-M07``/...), casando com a
    chave usada por ``gold.dim_entrega``.
    """

    fonte = FONTE_EXTRATOS
    relatorio = "EXTRATOS"
    path = "tt/extrato_entregas"
    default_periodos = (0,)

    def build_job(self, cod_ibge, ano, periodo_num, versao, homologada_em) -> IngestionJob:  # type: ignore[no-untyped-def]
        return IngestionJob(
            fonte=self.fonte,
            relatorio=self.relatorio,
            cod_ibge=cod_ibge,
            ano=ano,
            periodo=f"{ano}",
            versao=versao,
            homologada_em=homologada_em,
            valid_time=_period_end(ano, 12),
            params={"an_referencia": ano, "id_ente": cod_ibge},
        )

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        count = 0
        for item in payload:
            relatorio = relatorio_do_entregavel(
                _first(item, "entregavel", "relatorio"), _first(item, "tipo_relatorio")
            )
            if relatorio is None:
                continue
            ano = int(_first(item, "exercicio") or job.ano)
            data_status = _first(item, "data_status", "homologada_em")
            stmt = (
                pg_insert(SilverExtrato)
                .values(
                    cod_ibge=job.cod_ibge,
                    relatorio=relatorio,
                    periodo=periodo_canonico(
                        ano, _first(item, "periodicidade"), _first(item, "periodo")
                    ),
                    homologada_em=data_status,
                    versao=str(data_status or "1"),
                )
                .on_conflict_do_nothing(
                    index_elements=["cod_ibge", "relatorio", "periodo", "versao"]
                )
            )
            session.execute(stmt)
            count += 1
        return count


class EntesConnector(SiconfiConnectorBase):
    fonte = FONTE_ENTES
    relatorio = "ENTES"
    path = "tt/entes"
    default_periodos = (0,)

    def build_job(self, cod_ibge, ano, periodo_num, versao, homologada_em) -> IngestionJob:  # type: ignore[no-untyped-def]
        return IngestionJob(
            fonte=self.fonte,
            relatorio=self.relatorio,
            cod_ibge=cod_ibge,
            ano=ano,
            periodo=f"{ano}",
            versao=versao,
            homologada_em=homologada_em,
            valid_time=None,
            params={"id_ente": cod_ibge},
        )

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        count = 0
        for item in payload:
            cod = str(_first(item, "cod_ibge", "id_ente") or job.cod_ibge)
            populacao = _first(item, "populacao")
            stmt = (
                pg_insert(SilverEnte)
                .values(
                    cod_ibge=cod,
                    nome=_first(item, "ente", "nome", "no_ente"),
                    uf=_first(item, "uf", "sigla_uf"),
                    esfera=_first(item, "esfera", "co_esfera"),
                    populacao=int(populacao) if populacao not in (None, "") else None,
                    regiao=_first(item, "regiao"),
                    capital=_boolean(_first(item, "capital")),
                    versao_entrega=versao_entrega,
                )
                .on_conflict_do_update(
                    index_elements=["cod_ibge"],
                    set_={
                        "nome": _first(item, "ente", "nome", "no_ente"),
                        "populacao": int(populacao) if populacao not in (None, "") else None,
                        "versao_entrega": versao_entrega,
                    },
                )
            )
            session.execute(stmt)
            count += 1
        return count


# Conectores desta fonte (agregados pelo registry central).
CONNECTORS: dict[str, type[BaseConnector]] = {
    FONTE_RREO: RreoConnector,
    FONTE_RGF: RgfConnector,
    FONTE_DCA: DcaConnector,
    FONTE_MSC: MscConnector,
    FONTE_EXTRATOS: ExtratosConnector,
    FONTE_ENTES: EntesConnector,
}
