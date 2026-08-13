"""Erros da camada de ferramentas (Sprint IA-1a), todos RFC 7807 (§6.2).

A separação por classe não é decoração: cada uma responde a uma pergunta diferente de
quem opera a plataforma.

- :class:`FerramentaDesconhecidaError` — o chamador (modelo, cliente MCP) pediu algo que
  não existe. É sinal de alucinação de nome, não de defeito do backend.
- :class:`EntradaInvalidaError` — o argumento não bate com o contrato declarado. Culpa do
  chamador; a ferramenta **não** roda.
- :class:`SaidaInvalidaError` / :class:`FonteAusenteError` — a ferramenta rodou e devolveu
  algo que viola o contrato. Culpa **nossa**, e por isso é 5xx: um número fiscal sem
  ``source_ref`` chegando ao gestor é pior que um erro.

Os dois 403 (escopo e licença) **não** aparecem aqui de propósito: eles vêm de
``shared/scope.py``, que é a fonte única da regra. Reimplementá-los aqui recriaria o
achado A22 da Sprint E1 num lugar novo.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import AppError

TIPO_DESCONHECIDA = "urn:plataforma-fiscal:error:tool-not-found"
TIPO_ENTRADA = "urn:plataforma-fiscal:error:tool-input-invalid"
TIPO_SAIDA = "urn:plataforma-fiscal:error:tool-output-invalid"
TIPO_SEM_FONTE = "urn:plataforma-fiscal:error:tool-source-ref-missing"


class RegistroInvalidoError(RuntimeError):
    """Ferramenta mal declarada — falha **na carga**, não em runtime.

    É deliberadamente uma exceção de programação (e não um ``AppError``): uma ferramenta
    sem capacidade RBAC declarada, ou que recebe ente sem herdar o contrato de escopo,
    não pode existir num processo em execução esperando a primeira requisição para
    revelar o problema.
    """


class FerramentaDesconhecidaError(AppError):
    def __init__(self, nome: str, disponiveis: list[str]) -> None:
        super().__init__(
            status=404,
            title="Ferramenta desconhecida",
            detail=f"Não existe a ferramenta '{nome}'.",
            type_=TIPO_DESCONHECIDA,
            # A lista viaja no problema para que um agente que errou o nome possa se
            # corrigir sem uma segunda viagem ao catálogo.
            extras={"ferramentas": disponiveis},
        )


class EntradaInvalidaError(AppError):
    def __init__(self, nome: str, erros: list[dict[str, Any]]) -> None:
        super().__init__(
            status=422,
            title="Argumentos inválidos",
            detail=f"Os argumentos de '{nome}' não satisfazem o contrato de entrada.",
            type_=TIPO_ENTRADA,
            extras={"ferramenta": nome, "errors": erros},
        )


class SaidaInvalidaError(AppError):
    def __init__(self, nome: str, detalhe: str) -> None:
        super().__init__(
            status=500,
            title="Saída da ferramenta inválida",
            detail=f"A ferramenta '{nome}' devolveu saída fora do contrato: {detalhe}",
            type_=TIPO_SAIDA,
        )


class FonteAusenteError(AppError):
    """G4 — número fiscal sem ``source_ref``. Rejeitado pelo envelope, não por convenção."""

    def __init__(self, nome: str, caminhos: list[str]) -> None:
        super().__init__(
            status=500,
            title="Número fiscal sem fonte",
            detail=(
                f"A ferramenta '{nome}' devolveu número fiscal sem 'source_ref' em: "
                f"{', '.join(caminhos)}. Rastreabilidade (§6.3) é requisito de produto."
            ),
            type_=TIPO_SEM_FONTE,
            extras={"ferramenta": nome, "campos": caminhos},
        )
