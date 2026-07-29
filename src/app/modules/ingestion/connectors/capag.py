"""Conector CAPAG (Tesouro) — nota A–D e subindicadores, por município e por estado.

Cadência anual. ``versao_entrega`` = checksum do arquivo. O parser **falha explicitamente**
(``SpreadsheetLayoutError`` → HTTP 422) se o layout mudar — nunca adivinha, pois um CAPAG
errado silencioso contaminaria a Sprint 8 (Dívida / fato_capag).

**Descoberta pelo catálogo, não por URL fixa.** O Tesouro publica a CAPAG no CKAN do Tesouro
Transparente, em dois conjuntos (``capag-municipios`` e ``capag-estados``), e o identificador
do recurso muda a cada publicação — em 2024 foram quatro; em 2025, três. Uma URL escrita à
mão quebraria na revisão seguinte e, pior, continuaria baixando a versão velha sem avisar.
Por isso o ``discover`` consulta o catálogo, escolhe o recurso do ano e usa a publicação mais
recente como vigente; as anteriores continuam alcançáveis pela bitemporalidade (§6.5).

**Escala dos indicadores.** Município e estado publicam a mesma grandeza em formatos
diferentes: a planilha municipal traz fração (``0,3890``) e o CSV estadual traz texto com
símbolo (``38,90%``). Converter "por parecer grande" seria um erro de 100× num indicador de
endividamento, então a normalização é explícita por layout — nunca inferida do valor.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.modules.ingestion import repository
from app.modules.ingestion.connectors._file_base import FileConnectorBase
from app.modules.ingestion.connectors._parsing import first, num
from app.modules.ingestion.connectors._spreadsheet import (
    SpreadsheetLayoutError,
    read_table,
    read_xlsx,
    require_columns,
)
from app.modules.ingestion.models import FONTE_CAPAG, TesouroCapag
from app.shared.ingestion.base import BaseConnector, IngestionJob
from app.shared.uf import codigo_da_sigla

# Colunas mínimas exigidas (metodologia MF/STN vigente).
COLUNAS_NORMALIZADAS = (
    "cod_ibge",
    "nota_final",
    "ind_endividamento",
    "ind_poupanca",
    "ind_liquidez",
)

# Layout publicado pelo Tesouro (municípios): aba fixa, duas linhas de título, cabeçalho na 3.
ABA_OFICIAL = "Prévia da CAPAG"
LINHA_CABECALHO_OFICIAL = 3
COLUNAS_OFICIAIS = (
    "Código Município Completo",
    "CAPAG",
    "Indicador 1",
    "Indicador 2",
    "Indicador 3",
)

# Layout municipal histórico (publicações de 2022 e 2023): aba única, cabeçalho na linha 1,
# nomes com underscore e código em ``Cod.IBGE``. A coluna da nota muda de nome a cada ano
# (``CAPAG_2022``, ``CAPAG_Oficial``), por isso é localizada por prefixo — mas as colunas
# que carregam **número** são exigidas pelo nome exato: adivinhar indicador é inaceitável.
COLUNAS_MUNICIPAIS_HISTORICAS = (
    "Cod.IBGE",
    "Indicador_1",
    "Indicador_2",
    "Indicador_3",
)
_PREFIXO_NOTA_HISTORICA = "CAPAG"

# Layout publicado pelo Tesouro (estados): CSV ';' com sigla da UF e percentual textual.
COLUNAS_ESTADUAIS = (
    "UF",
    "Indicador 1",
    "Indicador 2",
    "Indicador 3",
    "Classificação da CAPAG",
)

# --- Catálogo público (CKAN do Tesouro Transparente) ---
CKAN_BASE = "https://www.tesourotransparente.gov.br/ckan/api/3/action"
PACOTES = {"municipios": "capag-municipios", "estados": "capag-estados"}

#: Relatório por escopo. Precisa ser **distinto**: a entrega é chaveada por
#: ``(cod_ibge, relatorio, periodo)`` e as duas publicações do mesmo ano viajam ambas como
#: entrega nacional ``BR``. Com o mesmo rótulo, o arquivo dos estados marcava o dos
#: municípios como retificado — 5.568 municípios sumiriam da visão vigente por serem
#: "superados" por 27 estados. São universos distintos, e o Tesouro os publica como dois
#: conjuntos, com metodologias documentadas em separado.
RELATORIO_POR_ESCOPO = {"municipios": "CAPAG", "estados": "CAPAG-EST"}
_ANO_NO_NOME = re.compile(r"20\d{2}")


def _tem_colunas(registros: list[dict[str, Any]], colunas: tuple[str, ...]) -> bool:
    return bool(registros) and set(colunas).issubset(registros[0])


def _codigo_ibge(value: Any) -> str:
    """Normaliza o código lido do Excel, que pode chegar como inteiro ou ``2304400.0``."""
    if value in (None, ""):
        return ""
    texto = str(value).strip()
    if texto.endswith(".0") and texto[:-2].isdigit():
        texto = texto[:-2]
    return texto


def fracao_percentual(value: Any) -> Decimal | None:
    """``'38,90%'`` → ``Decimal('0.3890')``. Só aceita o formato percentual textual.

    Usada **apenas** no layout estadual, onde o Tesouro publica com símbolo. Um valor sem
    ``%`` aqui é sinal de que o layout mudou, e devolver ``None`` é melhor que devolver um
    número cem vezes maior: a linha fica sem indicador e a ausência aparece como ausência.
    """
    if value is None:
        return None
    texto = str(value).strip()
    if not texto:
        return None
    if not texto.endswith("%"):
        return None
    texto = texto[:-1].strip().replace(".", "").replace(",", ".")
    try:
        return Decimal(texto) / Decimal(100)
    except (InvalidOperation, ValueError):
        return None


def _ano_do_recurso(recurso: dict[str, Any]) -> int | None:
    achados = _ANO_NO_NOME.findall(str(recurso.get("name") or ""))
    return int(achados[0]) if achados else None


_DATA_NO_NOME = re.compile(r"(\d{2})/(\d{2})/(20\d{2})")


def _data_no_nome(recurso: dict[str, Any]) -> str:
    """``'CAPAG Municípios 2024 - 15/10/2024'`` → ``'2024-10-15'`` (ordenável)."""
    achado = _DATA_NO_NOME.search(str(recurso.get("name") or ""))
    if achado is None:
        return ""
    dia, mes, ano = achado.groups()
    return f"{ano}-{mes}-{dia}"


def _ordem_publicacao(recurso: dict[str, Any]) -> tuple[str, str, str]:
    """Chave de recência da publicação — a data do **nome** manda.

    O ``last_modified`` do CKAN é manutenção de catálogo, não apuração: as quatro
    publicações municipais de 2024 foram todas tocadas dentro do mesmo minuto de
    11/06/2025, em ordem que inverte a cronologia real. Ordenar por ele elegia a foto de
    17/06/2024 no lugar da de 15/10/2024 — servir a posição vencida de um ente que mudou de
    nota entre as duas seria um erro grave num painel de risco.

    Onde o Tesouro não grava data no nome (o CSV dos estados), cai-se no carimbo do catálogo
    e, por fim, no nome — que também é o que faz "Revisão" superar a versão original.
    """
    return (
        _data_no_nome(recurso),
        str(recurso.get("last_modified") or recurso.get("created") or ""),
        str(recurso.get("name") or ""),
    )


def rotulo_publicacao(recurso: dict[str, Any]) -> str:
    """Data da publicação para auditoria: a do nome, senão a do catálogo."""
    return _data_no_nome(recurso) or str(
        recurso.get("last_modified") or recurso.get("created") or ""
    )[:10]


def recursos_do_ano(pacote: dict[str, Any], ano: int) -> list[dict[str, Any]]:
    """Recursos de dados daquele ano, da publicação mais recente para a mais antiga."""
    candidatos = [
        r
        for r in pacote.get("resources", [])
        if str(r.get("format", "")).upper() in {"XLSX", "CSV"}
        and _ano_do_recurso(r) == ano
        and r.get("url")
    ]
    return sorted(candidatos, key=_ordem_publicacao, reverse=True)


class CapagConnector(FileConnectorBase):
    fonte = FONTE_CAPAG
    relatorio = "CAPAG"
    cadencia = "anual"

    # --- descoberta pelo catálogo ---
    def _catalogo(self, escopo: str) -> dict[str, Any]:
        """Lê o pacote CKAN. Falha explícita: sem catálogo não há de onde inventar URL."""
        import httpx

        url = f"{CKAN_BASE}/package_show?id={PACOTES[escopo]}"
        resp = httpx.get(url, timeout=60, follow_redirects=True)
        resp.raise_for_status()
        corpo = resp.json()
        if not corpo.get("success"):
            raise SpreadsheetLayoutError(f"Catálogo CAPAG indisponível para '{escopo}'.")
        return dict(corpo["result"])

    def discover(self, state: dict[str, Any]) -> list[IngestionJob]:
        """Um job por (ano × escopo), descoberto no catálogo.

        Só consulta o catálogo quando o cliente de fato precisa de uma URL. Um cliente que
        já entrega o conteúdo (teste, cache local) segue pelo caminho manual — a descoberta
        é sobre *onde* está o arquivo, e não deve mudar o contrato de quem já o tem.
        """
        if state.get("url") or not getattr(self.client, "requer_url", False):
            return super().discover(state)

        from datetime import date

        anos: list[int] = state.get("anos") or [date.today().year]
        escopos: list[str] = list(state.get("escopos") or PACOTES)
        jobs: list[IngestionJob] = []
        for escopo in escopos:
            pacote = self._catalogo(escopo)
            for ano in anos:
                recursos = recursos_do_ano(pacote, ano)
                if not recursos:
                    # Ano sem publicação não é erro: a CAPAG do exercício ainda não saiu.
                    continue
                recurso = recursos[0]
                jobs.append(
                    IngestionJob(
                        fonte=self.fonte,
                        relatorio=RELATORIO_POR_ESCOPO[escopo],
                        cod_ibge="BR",
                        ano=ano,
                        periodo=str(ano),
                        versao=state.get("versao") or "",
                        homologada_em=state.get("homologada_em"),
                        valid_time=date(ano, 12, 31),
                        params={
                            "url": str(recurso["url"]),
                            "num": 0,
                            "escopo_capag": escopo,
                            "recurso_id": recurso.get("id"),
                            "recurso_nome": recurso.get("name"),
                            "publicado_em": rotulo_publicacao(recurso),
                            "formato": str(recurso.get("format", "")).lower(),
                            "delimiter": ";",
                        },
                    )
                )
        return jobs

    # --- parse ---
    def _parse_estadual(self, registros: list[dict[str, Any]]) -> list[dict[str, Any]]:
        require_columns(registros[0].keys(), COLUNAS_ESTADUAIS)
        mapeados: list[dict[str, Any]] = []
        for row in registros:
            sigla = str(first(row, "UF") or "").strip()
            cod = codigo_da_sigla(sigla)
            if cod is None:
                # Linha de totais/rodapé, ou sigla desconhecida: não se inventa ente.
                continue
            mapeados.append(
                {
                    "cod_ibge": cod,
                    "nota_final": (str(first(row, "Classificação da CAPAG") or "").strip() or None),
                    "ind_endividamento": fracao_percentual(first(row, "Indicador 1")),
                    "ind_poupanca": fracao_percentual(first(row, "Indicador 2")),
                    "ind_liquidez": fracao_percentual(first(row, "Indicador 3")),
                    "metodologia_versao": first(
                        row, "Qualidade da informação contábil e fiscal", "Metodologia"
                    ),
                }
            )
        return mapeados

    def _parse_municipal_historico(
        self, registros: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        coluna_nota = next(
            (
                k
                for k in registros[0]
                if str(k).upper().startswith(_PREFIXO_NOTA_HISTORICA)
            ),
            None,
        )
        if coluna_nota is None:
            raise SpreadsheetLayoutError(
                "Layout municipal histórico sem coluna de nota (esperado prefixo "
                f"'{_PREFIXO_NOTA_HISTORICA}'). Presentes: {sorted(registros[0])}"
            )
        mapeados: list[dict[str, Any]] = []
        for row in registros:
            cod = _codigo_ibge(first(row, "Cod.IBGE"))
            if not cod:
                continue
            mapeados.append(
                {
                    "cod_ibge": cod,
                    "nota_final": (str(row.get(coluna_nota) or "").strip() or None),
                    "ind_endividamento": num(first(row, "Indicador_1")),
                    "ind_poupanca": num(first(row, "Indicador_2")),
                    "ind_liquidez": num(first(row, "Indicador_3")),
                    "metodologia_versao": first(row, "Ano_Base", "Metodologia"),
                }
            )
        return mapeados

    def parse(self, raw: bytes, job: IngestionJob) -> list[dict[str, Any]]:
        if raw[:4] == b"PK\x03\x04":
            # Prioriza o contrato oficial: o arquivo público tem ~24 MB e não deve ser
            # carregado uma vez pela aba ativa e outra vez pela aba correta.
            try:
                registros_oficiais = read_xlsx(
                    raw,
                    sheet=ABA_OFICIAL,
                    header_row=LINHA_CABECALHO_OFICIAL,
                )
            except SpreadsheetLayoutError:
                registros_oficiais = None

            if registros_oficiais is not None:
                require_columns(
                    registros_oficiais[0].keys() if registros_oficiais else (),
                    COLUNAS_OFICIAIS,
                )
                registros = registros_oficiais
                layout = "oficial"
            else:
                # Sem a aba oficial: ou é uma publicação municipal antiga (aba única,
                # cabeçalho na linha 1), ou o layout normalizado de cargas legadas/testes.
                registros = read_table(raw, job.params)
                if not registros:
                    return []
                if _tem_colunas(registros, COLUNAS_MUNICIPAIS_HISTORICAS):
                    return self._parse_municipal_historico(registros)
                require_columns(registros[0].keys(), COLUNAS_NORMALIZADAS)
                layout = "normalizado"
        else:
            registros = read_table(raw, job.params)
            if not registros:
                return []
            if _tem_colunas(registros, COLUNAS_NORMALIZADAS):
                layout = "normalizado"
            elif _tem_colunas(registros, COLUNAS_ESTADUAIS):
                return self._parse_estadual(registros)
            elif _tem_colunas(registros, COLUNAS_MUNICIPAIS_HISTORICAS):
                return self._parse_municipal_historico(registros)
            elif _tem_colunas(registros, COLUNAS_OFICIAIS):
                layout = "oficial"
            else:
                # CSVs oficiais têm cabeçalho na primeira linha; não há segunda posição a tentar.
                presentes = registros[0].keys()
                raise SpreadsheetLayoutError(
                    "Layout CAPAG não reconhecido. "
                    f"Esperadas colunas normalizadas {list(COLUNAS_NORMALIZADAS)}, "
                    f"municipais {list(COLUNAS_OFICIAIS)}, "
                    f"municipais históricas {list(COLUNAS_MUNICIPAIS_HISTORICAS)} ou "
                    f"estaduais {list(COLUNAS_ESTADUAIS)}. Presentes: {sorted(presentes)}"
                )

        mapeados = []
        for row in registros:
            if layout == "oficial":
                cod = _codigo_ibge(first(row, "Código Município Completo"))
                nota_final = first(row, "CAPAG")
                endividamento = first(row, "Indicador 1")
                poupanca = first(row, "Indicador 2")
                liquidez = first(row, "Indicador 3")
                metodologia = first(
                    row,
                    "ICF",
                    "Metodologia",
                    "Metodologia Versão",
                    "Versão da metodologia",
                )
            else:
                cod = _codigo_ibge(first(row, "cod_ibge"))
                nota_final = first(row, "nota_final")
                endividamento = first(row, "ind_endividamento")
                poupanca = first(row, "ind_poupanca")
                liquidez = first(row, "ind_liquidez")
                metodologia = first(row, "metodologia_versao", "metodologia")
            if not cod:
                continue
            mapeados.append(
                {
                    "cod_ibge": cod,
                    "nota_final": (str(nota_final or "").strip() or None),
                    # Indicador 1 da metodologia CAPAG = DC bruta / RCL, em fração.
                    "ind_endividamento": num(endividamento),
                    "ind_poupanca": num(poupanca),
                    "ind_liquidez": num(liquidez),
                    "metodologia_versao": metodologia,
                }
            )
        return mapeados

    def to_silver(
        self, session: Session, job: IngestionJob, payload: Any, versao_entrega: str
    ) -> int:
        rows = [
            {**r, "ano_ref": job.ano, "valid_time": job.valid_time,
             "versao_entrega": versao_entrega}
            for r in payload
        ]
        return repository.replace_silver_rows(
            session, TesouroCapag,
            keys={"ano_ref": job.ano, "versao_entrega": versao_entrega}, rows=rows,
        )


CONNECTORS: dict[str, type[BaseConnector]] = {FONTE_CAPAG: CapagConnector}
