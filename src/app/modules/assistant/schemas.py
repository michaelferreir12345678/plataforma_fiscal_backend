"""Schemas Pydantic (entrada/saída) do assistente de IA."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.shared.source_ref import SourceRef


# ------------------------------- Entrada -------------------------------- #
class PerguntaRequest(BaseModel):
    """Uma pergunta ao assistente — isolada, ou a continuação de uma conversa (IA-7).

    ``ente`` deixou de ser obrigatório **apenas** quando há ``conversa_id``: numa pergunta
    de acompanhamento ("e por que isso aconteceu?"), exigir o ente de novo obrigaria o
    cliente a repetir o que o servidor já sabe — e era esse contrato que fazia cada
    pergunta ser isolada por construção. Sem ``conversa_id``, nada mudou: o ente continua
    obrigatório, e a validação falha na borda em vez de virar consulta sem alvo.
    """

    ente: str | None = Field(
        default=None,
        description=(
            "Código IBGE do ente (7 dígitos, ou 2 para UF). Opcional quando há "
            "'conversa_id': o ente é herdado do turno anterior."
        ),
    )
    pergunta: str = Field(min_length=3, max_length=2000)
    conversa_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Turno anterior desta conversa (o 'conversa_id' devolvido pela resposta "
            "anterior). Recupera o histórico e herda ente/período. O escopo é revalidado "
            "a cada turno — conversa não é passe livre."
        ),
    )
    periodo: str | None = Field(
        default=None, description="Período RREO (ex.: 2024-B6). Default: última entrega vigente."
    )
    as_of: datetime | None = Field(
        default=None, description="Reprodução bitemporal; default = versão vigente."
    )
    pagina: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Rota da tela de onde veio a pergunta (ex.: '/pessoal'). Usada só quando a "
            "pergunta não nomeia indicador — 'pergunte sobre esta tela' (Sprint 25E)."
        ),
    )

    @model_validator(mode="after")
    def _exige_ente_ou_conversa(self) -> PerguntaRequest:
        if not self.ente and self.conversa_id is None:
            raise ValueError("Informe 'ente' ou 'conversa_id' para continuar uma conversa.")
        return self


class ResumoExecutivoRequest(BaseModel):
    ente: str = Field(description="Código IBGE do ente.")
    periodo: str | None = Field(default=None, description="Período RREO. Default: última entrega.")
    as_of: datetime | None = None
    foco: str | None = Field(
        default=None, description="Instrução opcional de foco (ex.: 'riscos de pessoal')."
    )


# ------------------------------- Saída ---------------------------------- #
class FatoResposta(BaseModel):
    """Um indicador calculado usado na resposta, com rastreabilidade total."""

    codigo: str
    rotulo: str
    valor_formatado: str
    valor: str | None = None
    unidade: str
    status: str
    faixa: str | None = None
    #: Os limiares que definem a faixa, já formatados em pt-BR. Estão aqui por duas razões
    #: e as duas importam: é deste objeto que o guardrail G6 tira o lastro (ele confere os
    #: fatos e os payloads das ferramentas, não o texto do prompt), e é ele que vai para
    #: ``op.conversa.fatos`` — quem auditar a conversa depois vê contra qual faixa aquele
    #: número foi lido, não só que "estava normal".
    teto_formatado: str | None = None
    alerta_formatado: str | None = None
    prudencial_formatado: str | None = None
    disponivel: bool
    periodo: str
    as_of: datetime | None = None
    source_ref: SourceRef | None = None
    memoria: dict = Field(default_factory=dict)


class NormaResposta(BaseModel):
    fonte: str
    dispositivo: str
    titulo: str | None = None
    trecho: str
    score: float


class FonteChip(BaseModel):
    """Chip de fonte exibido junto à resposta (calculado × norma)."""

    tipo: str  # "indicador" | "norma"
    rotulo: str
    detalhe: str | None = None
    source_ref: SourceRef | None = None


class DadoIncompleto(BaseModel):
    tipo: str
    codigo: str
    mensagem: str
    periodo_esperado: str | None = None
    periodo_encontrado: str | None = None


class UsoInfo(BaseModel):
    modelo: str
    tokens_entrada: int
    tokens_saida: int
    latencia_ms: int
    modelo_solicitado: str | None = None
    model_version: str | None = None
    model_versions: list[str] = Field(default_factory=list)
    finish_reasons: list[str] = Field(default_factory=list)
    truncada: bool = False
    requests_provedor: int = 0
    max_tokens_entrada_por_request: int = 0


class VerificacaoOut(BaseModel):
    """Laudo do guardrail G6 — todo número da prosa foi casado com o que a plataforma deu.

    Estruturado **e** visível: o aviso correspondente também é anexado ao texto da
    resposta. Um campo que a tela pode ignorar seria exatamente "publicar em silêncio".
    """

    status: str = Field(description="'ok' | 'sinalizado'.")
    total_citados: int = Field(description="Números fiscais encontrados na prosa.")
    com_lastro: int = Field(description="Quantos casaram com valor devolvido pela plataforma.")
    sem_lastro: list[str] = Field(
        default_factory=list,
        description="Os que não casaram — sinalizados, nunca publicados em silêncio.",
    )


class RespostaOut(BaseModel):
    """Resposta fundamentada do assistente (perguntar ou resumo-executivo)."""

    conversa_id: uuid.UUID
    thread_id: uuid.UUID | None = Field(
        default=None, description="Fio ao qual o turno persistido pertence."
    )
    parent_id: uuid.UUID | None = Field(
        default=None, description="Turno causal usado como âncora desta continuação."
    )
    tipo: str
    ente: str
    ente_nome: str | None = None
    periodo: str | None = None
    as_of: datetime | None = None
    titulo: str | None = None
    pergunta: str
    resposta: str
    recusa: bool
    dado_disponivel: bool
    fatos: list[FatoResposta] = Field(default_factory=list)
    normas: list[NormaResposta] = Field(default_factory=list)
    fontes: list[FonteChip] = Field(default_factory=list)
    dados_incompletos: list[DadoIncompleto] = Field(default_factory=list)
    uso: UsoInfo
    source_refs: list[SourceRef] = Field(default_factory=list)
    verificacao: VerificacaoOut | None = Field(
        default=None,
        description=("Verificação de saída (G6). Ausente na recusa honesta, que não cita número."),
    )
    turnos_no_contexto: int = Field(
        default=0,
        description=(
            "Turnos anteriores que entraram no contexto desta resposta (Sprint IA-7). "
            "Zero = pergunta isolada. Turno de ente fora do escopo atual é descartado, e "
            "por isso este número pode ser menor que o tamanho do fio."
        ),
    )
    turnos_descartados: int = Field(
        default=0,
        description=(
            "Ancestrais inspecionados mas removidos porque o ente deixou o escopo ou a "
            "licença vigente. Nunca entram no prompt nem no lastro."
        ),
    )
    gerado_em: datetime


class ConversaResumo(BaseModel):
    id: uuid.UUID
    thread_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    tipo: str
    cod_ibge: str | None = None
    periodo: str | None = None
    pergunta: str
    resposta: str
    recusa: bool
    modelo: str | None = None
    as_of: datetime | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)
    verificacao: VerificacaoOut | None = None
    criado_em: datetime


class ConversasOut(BaseModel):
    itens: list[ConversaResumo] = Field(default_factory=list)


class UsoResumoOut(BaseModel):
    """Consumo de IA da organização no mês (alimenta a cota do plano)."""

    mes: str
    consultas: int
    tokens_entrada: int
    tokens_saida: int
    gerado_em: datetime
