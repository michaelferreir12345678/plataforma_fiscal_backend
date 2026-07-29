"""Registro único dos modelos ORM — importar este módulo popula ``Base.metadata``.

Existe porque um mapeamento incompleto do SQLAlchemy só falha **em runtime**: a API importa
todos os modelos de carona nos routers, mas um processo isolado (worker de ingestão,
scheduler, script) importa apenas o que usa. Aí uma FK entre schemas estoura
``NoReferencedTableError`` na primeira query — longe do import que faltou, e só na máquina
que rodou aquele processo.

A descoberta é automática de propósito: a lista manual que vivia no ``alembic/env.py``
cobria 12 dos 20 módulos com modelos e nada denunciava a defasagem.
"""

from __future__ import annotations

import importlib
import pkgutil

from sqlalchemy.orm import configure_mappers

import app.modules
from app.core.db import Base

__all__ = ["Base", "importar_modelos", "modulos_de_modelo"]


def modulos_de_modelo() -> list[str]:
    """Nomes de todo módulo de modelo sob ``app.modules`` (``models`` e ``*_models``)."""
    nomes: list[str] = []
    for modulo in pkgutil.iter_modules(app.modules.__path__):
        if not modulo.ispkg:
            continue
        pacote = f"app.modules.{modulo.name}"
        sub = importlib.import_module(pacote)
        caminho = getattr(sub, "__path__", None)
        if caminho is None:
            continue
        nomes.extend(
            f"{pacote}.{filho.name}"
            for filho in pkgutil.iter_modules(caminho)
            if filho.name == "models" or filho.name.endswith("_models")
        )
    return sorted(nomes)


def importar_modelos() -> list[str]:
    """Importa todos os módulos de modelo e resolve os mapeamentos de uma vez."""
    nomes = modulos_de_modelo()
    for nome in nomes:
        importlib.import_module(nome)
    configure_mappers()
    return nomes


importar_modelos()
