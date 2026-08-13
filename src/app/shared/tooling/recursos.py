"""Recursos — o que entra no contexto sem gastar uma chamada (Sprint IA-2).

MCP separa *tools* (ações que o modelo invoca) de *resources* (conteúdo que o cliente
carrega no contexto), e a §2.3 do plano é explícita sobre por que a separação importa
**aqui**: transformar dicionário em ferramenta faz o modelo gastar um passo do agente para
descobrir o que já podia estar no contexto; transformar consulta em recurso perde escopo e
auditoria.

Por isso este módulo é deliberadamente mais pobre que ``envelope.py``: um recurso **não**
recebe ente, **não** consulta escopo/licença e **não** grava ``op.ia_tool_call``. Ele
descreve a plataforma, não o fisco de ninguém — a mesma razão pela qual
``linhagem_do_indicador`` não declara ``recebe_ente``.

O que o registro garante na carga (erro de import, não 500 em produção):

- a URI segue ``dicionario://<nome>`` e **não** tem parâmetro (``{ente}``): um recurso
  parametrizado por ente seria dado de ente entrando por um caminho sem gate de escopo;
- não há dois recursos com a mesma URI;
- todo recurso tem descrição — é o que o cliente MCP lê para decidir carregá-lo.

A Sprint IA-3 mapeia este registro 1:1 na lista de *resources* do servidor MCP. Nada aqui
depende de protocolo.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.modules.dictionary import service as dictionary_service
from app.shared.tooling.errors import RegistroInvalidoError

#: ``dicionario://indicadores`` — esquema + nome, sem parâmetros.
_URI_VALIDA = re.compile(r"^[a-z][a-z0-9_]{2,31}://[a-z][a-z0-9_]{2,63}$")


@dataclass(frozen=True)
class Recurso:
    """Um bloco de conteúdo estável, carregável no contexto sem chamada de ferramenta."""

    uri: str
    nome: str
    descricao: str
    mime_type: str
    #: Recebe só a sessão: recurso não tem principal porque não tem escopo a verificar.
    carregar: Callable[[Session], str]


class RecursoRegistry:
    """Registro único dos recursos. Uma implementação, várias exposições (§2.2)."""

    def __init__(self) -> None:
        self._recursos: dict[str, Recurso] = {}

    def register(self, recurso: Recurso) -> Recurso:
        if not _URI_VALIDA.match(recurso.uri):
            raise RegistroInvalidoError(
                f"URI de recurso inválida: '{recurso.uri}' (use 'esquema://nome', sem "
                f"parâmetros — recurso parametrizado por ente seria dado de ente sem gate "
                f"de escopo)."
            )
        if recurso.uri in self._recursos:
            raise RegistroInvalidoError(f"Recurso '{recurso.uri}' já registrado.")
        if not recurso.descricao.strip():
            raise RegistroInvalidoError(
                f"'{recurso.uri}' sem descrição: é ela que o cliente lê para decidir "
                f"carregar o recurso."
            )
        self._recursos[recurso.uri] = recurso
        return recurso

    def get(self, uri: str) -> Recurso | None:
        return self._recursos.get(uri)

    def uris(self) -> list[str]:
        return sorted(self._recursos)

    def todos(self) -> list[Recurso]:
        return [self._recursos[u] for u in self.uris()]

    def __len__(self) -> int:
        return len(self._recursos)

    def __contains__(self, uri: object) -> bool:
        return uri in self._recursos


URI_INDICADORES = "dicionario://indicadores"
URI_CAMPOS = "dicionario://campos"
URI_JUNCOES = "dicionario://juncoes"


def construir_registro() -> RecursoRegistry:
    """Monta o registro de recursos da plataforma (dicionário semântico da IA-2)."""
    registro = RecursoRegistry()
    registro.register(
        Recurso(
            uri=URI_INDICADORES,
            nome="Dicionário de indicadores",
            descricao=(
                "Definição oficial de cada indicador fiscal da plataforma: o que mede, "
                "fórmula legível, denominador correto (RCL × RCL Ajustada × base dos "
                "mínimos), unidade, sentido (teto/piso/gerencial), base legal, sinônimos "
                "de negócio e armadilhas conhecidas. Carregue antes de interpretar "
                "qualquer número ou escrever qualquer consulta."
            ),
            mime_type="text/markdown",
            carregar=dictionary_service.render_indicadores,
        )
    )
    registro.register(
        Recurso(
            uri=URI_CAMPOS,
            nome="Dicionário de campos",
            descricao=(
                "O que cada coluna das tabelas consultáveis guarda e o que ela não é "
                "(sinal, base, cadência de outro relatório). Declara também quais tabelas "
                "podem entrar em consulta analítica — nenhuma do schema 'op'."
            ),
            mime_type="text/markdown",
            carregar=dictionary_service.render_campos,
        )
    )
    registro.register(
        Recurso(
            uri=URI_JUNCOES,
            nome="Caminhos de junção sancionados",
            descricao=(
                "As junções permitidas entre as tabelas consultáveis, com a condição que "
                "precisa viajar junto (vigência da entrega!) e o que acontece se ela for "
                "esquecida — tipicamente, multiplicação silenciosa de linhas."
            ),
            mime_type="text/markdown",
            carregar=dictionary_service.render_juncoes,
        )
    )
    return registro


_REGISTRO: RecursoRegistry | None = None


def registro() -> RecursoRegistry:
    """Registro único do processo (carregado na primeira chamada)."""
    global _REGISTRO
    if _REGISTRO is None:
        _REGISTRO = construir_registro()
    return _REGISTRO


def ler(session: Session, uri: str) -> str:
    """Conteúdo de um recurso. URI desconhecida ⇒ erro de registro, não conteúdo vazio."""
    recurso = registro().get(uri)
    if recurso is None:
        raise RegistroInvalidoError(
            f"Recurso '{uri}' não existe. Disponíveis: {', '.join(registro().uris())}."
        )
    return recurso.carregar(session)
