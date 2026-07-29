"""Unidades da Federação: código IBGE (2 dígitos) ↔ sigla ↔ nome.

Dado de referência estável, usado tanto pela Visão Estadual quanto pela ingestão (a CAPAG
dos estados, por exemplo, é publicada por **sigla**, e o ente estadual é identificado pelo
código IBGE de 2 dígitos). Fica em ``shared`` para que a ingestão não precise importar um
módulo de dashboard só para traduzir "CE" em "23".
"""

from __future__ import annotations

UFS: tuple[tuple[str, str, str], ...] = (
    ("11", "RO", "Rondônia"),
    ("12", "AC", "Acre"),
    ("13", "AM", "Amazonas"),
    ("14", "RR", "Roraima"),
    ("15", "PA", "Pará"),
    ("16", "AP", "Amapá"),
    ("17", "TO", "Tocantins"),
    ("21", "MA", "Maranhão"),
    ("22", "PI", "Piauí"),
    ("23", "CE", "Ceará"),
    ("24", "RN", "Rio Grande do Norte"),
    ("25", "PB", "Paraíba"),
    ("26", "PE", "Pernambuco"),
    ("27", "AL", "Alagoas"),
    ("28", "SE", "Sergipe"),
    ("29", "BA", "Bahia"),
    ("31", "MG", "Minas Gerais"),
    ("32", "ES", "Espírito Santo"),
    ("33", "RJ", "Rio de Janeiro"),
    ("35", "SP", "São Paulo"),
    ("41", "PR", "Paraná"),
    ("42", "SC", "Santa Catarina"),
    ("43", "RS", "Rio Grande do Sul"),
    ("50", "MS", "Mato Grosso do Sul"),
    ("51", "MT", "Mato Grosso"),
    ("52", "GO", "Goiás"),
    ("53", "DF", "Distrito Federal"),
)

SIGLA_PARA_COD: dict[str, str] = {sigla: cod for cod, sigla, _ in UFS}
COD_PARA_SIGLA: dict[str, str] = {cod: sigla for cod, sigla, _ in UFS}
COD_PARA_NOME: dict[str, str] = {cod: nome for cod, _, nome in UFS}


def codigo_da_sigla(sigla: str) -> str | None:
    """``'CE'`` → ``'23'``. ``None`` para sigla desconhecida — nunca inventa código."""
    return SIGLA_PARA_COD.get((sigla or "").strip().upper())
