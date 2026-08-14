"""Schemas da IA nas telas (Sprint IA-5).

Um envelope só para as quatro capacidades. Deliberado: a tela que já sabe exibir
"Explique este número" sabe exibir a explicação da fila de alertas sem código novo, e um
critério de aceite ("``source_ref`` visível", "ausência declarada") vale para as quatro de
uma vez, em vez de ser reimplementado quatro vezes com quatro larguras diferentes.

Os tipos de fato, chip de fonte, uso e verificação são **os do Assistente**
(``assistant.schemas``): a mesma resposta renderizada pelo mesmo componente de tela, com o
mesmo casamento tolerante de número da Sprint B3. Duplicá-los aqui criaria dois contratos
para a mesma coisa — e o segundo é o que fica para trás.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.modules.assistant.schemas import (
    DadoIncompleto,
    FatoResposta,
    FonteChip,
    UsoInfo,
    VerificacaoOut,
)
from app.shared.source_ref import SourceRef

#: As quatro capacidades da ficha, na ordem em que a §4 do plano as prioriza.
CAPACIDADES: tuple[str, ...] = (
    "explicar_numero",
    "explicar_alertas",
    "narrar_relatorio",
    "central_dados",
)


# ------------------------------- Entrada -------------------------------- #
class ExplicarNumeroRequest(BaseModel):
    """"Explique este número" — o indicador que está na tela, com a fonte que a tela mostra."""

    ente: str = Field(description="Código IBGE do ente (7 dígitos, ou 2 para UF).")
    indicador: str = Field(
        min_length=2,
        max_length=64,
        description="Código do indicador em gold.mart_indicador (ex.: pessoal_executivo).",
    )
    periodo: str | None = Field(
        default=None, description="Período canônico (ex.: 2024-B6). Ausente ⇒ o mais recente."
    )
    as_of: datetime | None = Field(
        default=None, description="Reprodução bitemporal; ausente ⇒ versão vigente (§6.5)."
    )


class ExplicarAlertasRequest(BaseModel):
    """Explicação da fila — a ordem continua sendo de ``alerts/rules.py``."""

    ente: str = Field(description="Código IBGE do ente.")
    as_of: datetime | None = None


class NarrarRelatorioRequest(BaseModel):
    """Narrativa do documento que ``reports.build_document`` monta — sem número novo."""

    ente: str = Field(description="Código IBGE do ente.")
    periodo: str | None = Field(default=None, description="Período canônico do relatório.")
    modelo: str = Field(
        default="executivo",
        max_length=24,
        description="Modelo: executivo, limites, comparativo, conformidade ou boletim.",
    )
    as_of: datetime | None = None


class CentralDadosRequest(BaseModel):
    """Busca em linguagem natural na Central de Dados ("por que Saúde está vazia?")."""

    ente: str = Field(description="Código IBGE do ente.")
    pergunta: str = Field(min_length=3, max_length=2000)
    pagina: str | None = Field(
        default=None,
        max_length=48,
        description=(
            "Página cuja ausência se quer explicar. Ausente ⇒ deduzida da pergunta; "
            "sem dedução possível, a resposta declara quais páginas sabe responder."
        ),
    )
    periodo: str | None = None
    as_of: datetime | None = None


# ------------------------------- Saída ---------------------------------- #
class NotaInsight(BaseModel):
    """Bloco textual **apurado por ferramenta** que fundamenta a prosa (não é número).

    Linhagem, providência legal, cobertura e prazo entram por aqui. Vão para o modelo como
    contexto e voltam para a tela como estão: quem lê a resposta consegue conferir de onde
    saiu cada afirmação sem depender de o modelo ter repetido a nota corretamente.
    """

    titulo: str
    linhas: list[str] = Field(default_factory=list)
    origem: str | None = Field(
        default=None, description="Ferramenta ou tabela de onde a nota saiu."
    )


class InsightOut(BaseModel):
    """Resposta de qualquer uma das quatro capacidades da IA nas telas."""

    capacidade: str = Field(
        description=(
            "explicar_numero | explicar_alertas | narrar_relatorio | central_dados"
        )
    )
    titulo: str
    ente: str
    ente_nome: str | None = None
    periodo: str | None = None
    as_of: datetime | None = None
    #: A pergunta que esta superfície declara responder (§ riscos da ficha: "IA vira
    #: enfeite"). É a mesma frase enviada ao provedor.
    pergunta: str
    resposta: str
    #: Houve dado a explicar? ``False`` ⇒ ``ausencia`` diz o quê e por quê, e o modelo
    #: **não** foi chamado (G3 — a recusa honesta da Sprint 17 é herdada, não reescrita).
    disponivel: bool
    ausencia: str | None = None
    fatos: list[FatoResposta] = Field(default_factory=list)
    notas: list[NotaInsight] = Field(default_factory=list)
    fontes: list[FonteChip] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    dados_incompletos: list[DadoIncompleto] = Field(default_factory=list)
    #: Ferramentas efetivamente chamadas — a cadeia que a auditoria (``op.ia_tool_call``)
    #: registrou para esta resposta.
    ferramentas: list[str] = Field(default_factory=list)
    uso: UsoInfo
    verificacao: VerificacaoOut | None = None
    gerado_em: datetime
