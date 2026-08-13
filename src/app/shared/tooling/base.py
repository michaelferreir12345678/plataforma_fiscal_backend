"""Contrato de uma ferramenta e o registro único (Sprint IA-1a).

Uma **ferramenta** é uma capacidade da plataforma exposta de forma tipada: nome, descrição,
esquema de entrada e de saída (JSON Schema), capacidade RBAC exigida e se recebe ente. É a
mesma implementação que o servidor MCP, o *function calling* do assistente e um endpoint
HTTP vão chamar — as exposições são adaptadores burros, e a garantia mora aqui dentro
(§2.2 do plano de MCP; lição A22 da Sprint E1).

**O que é declarado é obrigatório.** ``capacidade``, ``recebe_ente`` e
``saida_tem_numero_fiscal`` não têm valor padrão: esquecer um deles é ``TypeError`` no
import, não um 200 silencioso em produção. O registro ainda confere, na carga:

- que a capacidade existe no RBAC (``CAPACIDADES``) — um nome digitado errado nunca
  concederia acesso, mas também nunca seria concedido a ninguém;
- que quem recebe ente herda :class:`EnteToolInput` — que é o que garante que existe um
  campo ``ente`` para o envelope levar a ``assert_ente_in_scope``, e um ``as_of`` para a
  bitemporalidade (G5);
- que quem **não** declara receber ente não tem campo ``ente`` — sem isso, uma ferramenta
  poderia consultar um ente driblando o gate de escopo por omissão;
- que quem devolve número fiscal declara ``source_ref`` na saída (G4).
"""

from __future__ import annotations

import re
import typing
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.modules.tenancy.models import CAPACIDADES
from app.shared.tooling.errors import RegistroInvalidoError

#: Nome de ferramenta aceito por clientes MCP e pelo *function calling* do Gemini.
_NOME_VALIDO = re.compile(r"^[a-z][a-z0-9_]{2,63}$")

#: Nomes que **declaram** procedência numa saída (ver ``fonte.CHAVES_DE_FONTE``).
_CAMPOS_DE_FONTE = ("source_ref", "source_refs")


def _submodelos(anotacao: Any) -> list[type[BaseModel]]:
    """Modelos Pydantic alcançáveis a partir de uma anotação (``list[X]``, ``X | None``…)."""
    if isinstance(anotacao, type) and issubclass(anotacao, BaseModel):
        return [anotacao]
    achados: list[type[BaseModel]] = []
    for arg in typing.get_args(anotacao):
        achados.extend(_submodelos(arg))
    return achados


def declara_fonte(modelo: type[BaseModel], *, profundidade: int = 1) -> bool:
    """Se o contrato de saída declara ``source_ref`` na raiz **ou em cada item**.

    A IA-1a exigia a fonte na raiz, que é a forma certa quando a resposta inteira sai de
    uma entrega só. A IA-1b encontrou o caso contrário: uma **série histórica** tem um
    período por entrega, e uma lista de limites mistura indicadores apurados do RREO com
    outros apurados do RGF. Nesses contratos a fonte tem de ser por item, e um carimbo
    único na raiz seria uma procedência uniforme e **errada** — pior que nenhuma, porque
    erra com aparência de rigor.

    Por isso a exigência é "declara procedência de forma estruturalmente alcançável", não
    "tem um campo na raiz". A guarda de runtime (``fonte.numeros_sem_fonte``) continua
    conferindo, número a número, que a declaração foi de fato preenchida.
    """
    campos = modelo.model_fields
    if any(nome in campos for nome in _CAMPOS_DE_FONTE):
        return True
    if profundidade <= 0:
        return False
    return any(
        declara_fonte(sub, profundidade=profundidade - 1)
        for campo in campos.values()
        for sub in _submodelos(campo.annotation)
    )


class ToolInput(BaseModel):
    """Entrada de ferramenta. ``extra='forbid'`` é guardrail, não rigor estético.

    Um argumento não previsto tem de estourar. O modo de falha silencioso — ignorar o
    campo desconhecido — é exatamente o que produziria uma resposta sobre o período
    errado quando o modelo inventa ``exercicio=2023`` numa ferramenta que só entende
    ``periodo``: números certos respondendo a uma pergunta que ninguém fez.
    """

    model_config = ConfigDict(extra="forbid")


class EnteToolInput(ToolInput):
    """Entrada de toda ferramenta ligada a um ente — o contrato que o escopo exige.

    ``as_of`` é de primeira classe (§6.5/G5): sem ele, versão vigente; com ele, a que
    estava vigente naquele instante. A resposta declara qual usou.
    """

    ente: str = Field(
        min_length=2,
        max_length=7,
        pattern=r"^\d{2}$|^\d{7}$",
        description="Código IBGE do ente (7 dígitos; 2 para o ente estadual).",
    )
    as_of: datetime | None = Field(
        default=None,
        description=(
            "Instante bitemporal. Ausente ⇒ versão vigente; presente ⇒ a versão que "
            "estava vigente naquele instante (reprodução de relatório histórico)."
        ),
    )


class ToolOutput(BaseModel):
    """Saída de ferramenta. ``linhas()`` alimenta a auditoria (G7)."""

    def linhas(self) -> int:
        """Quantas linhas de dado a chamada devolveu (auditoria/observabilidade)."""
        return 1


#: Assinatura do executor concreto. Recebe o contexto já validado pelo envelope.
Handler = Callable[["ToolContext", Any], ToolOutput]


@dataclass(frozen=True)
class ToolContext:
    """Quem chama, por onde, e com qual sessão de banco.

    ``origem`` distingue assistente, servidor MCP e tela — é o que permite responder
    "quanto do consumo veio de cliente externo?" sem instrumentar cada exposição.
    """

    session: Session
    principal: Principal
    origem: str = "api"
    conversa_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Tool:
    """Uma ferramenta registrada. Imutável — o catálogo é montado na carga do processo."""

    nome: str
    descricao: str
    entrada: type[ToolInput]
    saida: type[ToolOutput]
    handler: Handler
    #: Capacidade RBAC exigida (``op.papel_permissao.capacidade``).
    capacidade: str
    #: Recebe ente? Se sim, o envelope chama ``assert_ente_in_scope`` — sempre.
    recebe_ente: bool
    #: A saída carrega número fiscal? Se sim, ``source_ref`` é obrigatório nela.
    saida_tem_numero_fiscal: bool

    def schema_entrada(self) -> dict[str, Any]:
        """JSON Schema da entrada — o que MCP e *function calling* declaram ao cliente."""
        return self.entrada.model_json_schema()

    def schema_saida(self) -> dict[str, Any]:
        return self.saida.model_json_schema()


class ToolRegistry:
    """Registro único das ferramentas. Uma implementação, várias exposições."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        """Valida a declaração e registra. Erro aqui é erro de carga, não de runtime."""
        if not _NOME_VALIDO.match(tool.nome):
            raise RegistroInvalidoError(
                f"Nome de ferramenta inválido: '{tool.nome}' (use snake_case, 3-64 chars)."
            )
        if tool.nome in self._tools:
            raise RegistroInvalidoError(f"Ferramenta '{tool.nome}' já registrada.")
        if not tool.descricao.strip():
            raise RegistroInvalidoError(
                f"'{tool.nome}' sem descrição: é ela que o modelo lê para decidir se chama."
            )
        if tool.capacidade not in CAPACIDADES:
            raise RegistroInvalidoError(
                f"'{tool.nome}' exige a capacidade '{tool.capacidade}', que não existe no "
                f"RBAC ({', '.join(CAPACIDADES)})."
            )
        if tool.recebe_ente and not issubclass(tool.entrada, EnteToolInput):
            raise RegistroInvalidoError(
                f"'{tool.nome}' declara receber ente, então a entrada tem de herdar "
                f"EnteToolInput (é o contrato que o gate de escopo/licença consome)."
            )
        if not tool.recebe_ente and "ente" in tool.entrada.model_fields:
            raise RegistroInvalidoError(
                f"'{tool.nome}' aceita 'ente' sem declarar recebe_ente=True — seria um "
                f"caminho para consultar um ente sem passar pelo gate de escopo (A22/E1)."
            )
        if tool.saida_tem_numero_fiscal and not declara_fonte(tool.saida):
            raise RegistroInvalidoError(
                f"'{tool.nome}' devolve número fiscal, então a saída tem de declarar "
                f"'source_ref' — na raiz ou em cada item da lista (§6.3)."
            )
        self._tools[tool.nome] = tool
        return tool

    def get(self, nome: str) -> Tool | None:
        return self._tools.get(nome)

    def nomes(self) -> list[str]:
        return sorted(self._tools)

    def todas(self) -> list[Tool]:
        return [self._tools[n] for n in self.nomes()]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, nome: object) -> bool:
        return nome in self._tools
