"""Dicionário semântico da plataforma (Sprint IA-2).

Descreve **o significado** do dado — fórmula, denominador correto, sentido, base legal, o
que cada coluna guarda e quais junções são sancionadas. É o guardrail que age *antes* da
falha: sem ele, uma consulta gerada por modelo escolhe a coluna plausível e errada com
sintaxe perfeita.

O dicionário é **dado** (mora em ``gold``, com seed idempotente e data/fonte da definição),
e é exposto como **recurso** — não como ferramenta (§2.3 do plano de MCP): recurso entra no
contexto sem gastar uma chamada; ferramenta carrega escopo e auditoria, que definição não
tem por que carregar.
"""

from __future__ import annotations
