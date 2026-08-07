"""Famílias de integração (Sprint 18) e gate de orquestração dos conectores.

Cada **família** de `op.integracao` (SICONFI, SADIPEM, BCB, IBGE, FPM, FUNDEB, CAPAG,
SIOPS, SIOPE) governa um conjunto de conectores das Sprints 1/1B. Desligar ``ativo`` pausa
efetivamente todos os conectores da família na orquestração (`ingestion.service.run`).

Gate **default-on**: uma fonte sem família governante — ou sem linha semeada — é tratada
como ativa, de modo que a ingestão nunca é bloqueada por engano antes da seed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.modules.ingestion import repository
from app.modules.ingestion.models import (
    FONTE_BCB,
    FONTE_CAPAG,
    FONTE_DCA,
    FONTE_ENTES,
    FONTE_EXTRATOS,
    FONTE_FPM,
    FONTE_FUNDEB,
    FONTE_IBGE_MALHA,
    FONTE_IBGE_PIB,
    FONTE_IBGE_POPULACAO,
    FONTE_MSC,
    FONTE_RGF,
    FONTE_RREO,
    FONTE_SADIPEM_CDP,
    FONTE_SADIPEM_CRONOGRAMA,
    FONTE_SADIPEM_OP,
    FONTE_SADIPEM_PVL,
    FONTE_SIOPE,
    FONTE_SIOPS,
    FONTE_TRANSFERENCIA,
)

FONTE_RREO_MINIMOS_PDF = "siconfi_rreo_minimos_pdf"

# Uma família por fonte das Sprints 1 e 1B. ``fontes`` lista as chaves de conector.
FAMILIES: list[dict[str, Any]] = [
    {
        "codigo": "SICONFI",
        "nome": "SICONFI — Tesouro Nacional",
        "categoria": "nacional",
        "descricao": "RREO, RGF, DCA, MSC, Entes e Extratos (dados abertos do SICONFI).",
        "fontes": [
            FONTE_RREO,
            FONTE_RGF,
            FONTE_DCA,
            FONTE_MSC,
            FONTE_ENTES,
            FONTE_EXTRATOS,
            FONTE_RREO_MINIMOS_PDF,
        ],
    },
    {
        "codigo": "SADIPEM",
        "nome": "SADIPEM — Dívida e operações de crédito",
        "categoria": "nacional",
        "descricao": "PVL, operações contratadas, cronograma de pagamento e CDP.",
        "fontes": [
            FONTE_SADIPEM_PVL,
            FONTE_SADIPEM_OP,
            FONTE_SADIPEM_CRONOGRAMA,
            FONTE_SADIPEM_CDP,
        ],
    },
    {
        "codigo": "BCB",
        "nome": "Banco Central — SGS",
        "categoria": "complementar",
        "descricao": "Séries econômicas (Selic, IPCA) do SGS/BCB para exógenas de previsão.",
        "fontes": [FONTE_BCB],
    },
    {
        "codigo": "IBGE",
        "nome": "IBGE — População, PIB e malhas",
        "categoria": "complementar",
        "descricao": "População, PIB e malha geográfica municipal por UF.",
        "fontes": [FONTE_IBGE_POPULACAO, FONTE_IBGE_PIB, FONTE_IBGE_MALHA],
    },
    {
        "codigo": "FPM",
        "nome": "Tesouro — Transferências (FPM/FPE)",
        "categoria": "complementar",
        "descricao": "FPM/FPE e demais transferências constitucionais.",
        "fontes": [FONTE_FPM, FONTE_TRANSFERENCIA],
    },
    {
        "codigo": "FUNDEB",
        "nome": "FNDE — FUNDEB",
        "categoria": "complementar",
        "descricao": "Repasses do FUNDEB por município (FNDE).",
        "fontes": [FONTE_FUNDEB],
    },
    {
        "codigo": "CAPAG",
        "nome": "Tesouro — CAPAG",
        "categoria": "complementar",
        "descricao": "Capacidade de pagamento (nota A–D) e subindicadores.",
        "fontes": [FONTE_CAPAG],
    },
    {
        "codigo": "SIOPS",
        "nome": "SIOPS — Saúde",
        "categoria": "complementar",
        "descricao": "Indicadores de saúde (Ministério da Saúde) para conferência dos mínimos.",
        "fontes": [FONTE_SIOPS],
    },
    {
        "codigo": "SIOPE",
        "nome": "SIOPE — Educação",
        "categoria": "complementar",
        "descricao": "Indicadores de educação (FNDE) para conferência dos mínimos.",
        "fontes": [FONTE_SIOPE],
    },
]

FAMILY_CODES: tuple[str, ...] = tuple(f["codigo"] for f in FAMILIES)
FAMILY_BY_FONTE: dict[str, str] = {
    fonte: fam["codigo"] for fam in FAMILIES for fonte in fam["fontes"]
}


def seed_integracoes(session: Session) -> int:
    """Semeia/atualiza as 9 famílias (idempotente; preserva ``ativo`` já definido)."""
    gravados = 0
    for fam in FAMILIES:
        row = repository.get_integracao(session, codigo=fam["codigo"])
        if row is None:
            repository.insert_integracao(
                session,
                codigo=fam["codigo"],
                nome=fam["nome"],
                descricao=fam["descricao"],
                categoria=fam["categoria"],
                fontes=list(fam["fontes"]),
            )
            gravados += 1
        else:
            # Mantém o toggle do gestor; só atualiza os metadados descritivos.
            row.nome = fam["nome"]
            row.descricao = fam["descricao"]
            row.categoria = fam["categoria"]
            row.fontes = list(fam["fontes"])
    session.flush()
    return gravados


def integracao_ativa(session: Session, fonte: str) -> bool:
    """Gate default-on: fonte sem família ou sem linha semeada ⇒ ativa."""
    codigo = FAMILY_BY_FONTE.get(fonte)
    if codigo is None:
        return True
    row = repository.get_integracao(session, codigo=codigo)
    return True if row is None else bool(row.ativo)
