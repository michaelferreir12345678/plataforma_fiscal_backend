"""Camada de ferramentas governadas (Sprint IA-1a).

Uma implementação, várias exposições: o assistente (via *function calling*), o futuro
servidor MCP e as telas chamam **as mesmas** ferramentas, e toda garantia — escopo,
licença, ``as_of``, ``source_ref`` e auditoria — mora dentro do envelope, não na borda
que chama (lição A22 da Sprint E1).

Uso típico::

    from app.shared import tooling

    ctx = tooling.ToolContext(session=session, principal=principal, origem="assistente")
    resultado = tooling.invoke(ctx, tooling.registro(), "indicador_do_ente",
                               {"ente": "2304400", "indicador": "garantias"})
"""

from __future__ import annotations

from app.shared.tooling.base import (
    EnteToolInput,
    Tool,
    ToolContext,
    ToolInput,
    ToolOutput,
    ToolRegistry,
)
from app.shared.tooling.catalogo import construir_registro, erro_para_payload, registro
from app.shared.tooling.envelope import ToolCallResult, fontes_do_payload, invoke
from app.shared.tooling.errors import (
    EntradaInvalidaError,
    FerramentaDesconhecidaError,
    FonteAusenteError,
    RegistroInvalidoError,
    SaidaInvalidaError,
)
from app.shared.tooling.fonte import numeros_sem_fonte
from app.shared.tooling.recursos import Recurso, RecursoRegistry
from app.shared.tooling.recursos import ler as ler_recurso
from app.shared.tooling.recursos import registro as registro_de_recursos

__all__ = [
    "EnteToolInput",
    "EntradaInvalidaError",
    "FerramentaDesconhecidaError",
    "FonteAusenteError",
    "Recurso",
    "RecursoRegistry",
    "RegistroInvalidoError",
    "SaidaInvalidaError",
    "Tool",
    "ToolCallResult",
    "ToolContext",
    "ToolInput",
    "ToolOutput",
    "ToolRegistry",
    "construir_registro",
    "erro_para_payload",
    "fontes_do_payload",
    "invoke",
    "ler_recurso",
    "numeros_sem_fonte",
    "registro",
    "registro_de_recursos",
]
