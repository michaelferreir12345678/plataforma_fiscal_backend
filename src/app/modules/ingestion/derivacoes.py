"""Derivações de séries a partir de fontes já ingeridas (Sprint 21).

**ICMS cota-parte (`transferencia_generica`).** A decisão de produto (auditoria §8, B12)
oferece duas fontes: um feed dedicado da Sefaz-CE ou a **derivação documentada do RREO
Anexo 01**. Como não há, hoje, credencial/endpoint Sefaz-CE conectado, derivamos do RREO
Anexo 01 a linha **"Transferências correntes dos Estados e do DF e de suas entidades"**
(``cod_conta`` = ``TransferenciasCorrentesDosEstados...``), coluna **"No Bimestre (b)"** —
o valor **realizado no bimestre**.

Ressalva registrada no dado: essa linha é o **agregado de transferências estaduais**
(predominantemente a cota-parte do ICMS — CF art. 158, IV — somada à cota-parte do IPVA),
não o ICMS isolado. Por isso o ``tipo`` é ``cota_parte_estados`` e a ``fonte`` é
``derivado_rreo_a1`` — a proveniência fica explícita e a série **não** é apresentada como
contraprova independente do próprio RREO. Quando o feed Sefaz-CE entrar, o conector
``transferencia_generica`` substitui essa derivação sem mudar o schema.
"""

from __future__ import annotations

import unicodedata
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.ingestion import repository
from app.modules.ingestion.connectors._parsing import num
from app.modules.ingestion.models import SilverRreo, TransferenciaGenerica

TIPO_ICMS = "cota_parte_estados"
FONTE_DERIVADA = "derivado_rreo_a1"
_COD_CONTA_ALVO = "TRANSFERENCIASCORRENTESDOSESTADOS"


def _norm(texto: str | None) -> str:
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper()


def _is_no_bimestre(coluna: str | None) -> bool:
    c = _norm(coluna)
    return "NO BIMESTRE" in c or c.startswith("NO BIMESTRE")


def derivar_icms_cota_parte(
    session: Session, *, cod_ibge: str, periodo: str, versao_rreo: str
) -> int:
    """Deriva a cota-parte de transferências estaduais do RREO A1 de um período bimestral.

    Grava uma linha em ``silver.transferencia_generica`` no fechamento do bimestre
    (``mes = 2 × bimestre``) com o valor realizado no bimestre. Idempotente por
    (cod_ibge, ano, mes, versao_entrega=``derivado_rreo_a1``).
    """
    if "-B" not in periodo:
        return 0
    ano = int(periodo[:4])
    bimestre = int(periodo.split("-B", 1)[1])
    mes = 2 * bimestre

    valor = None
    for row in repository.read_silver(
        session, SilverRreo, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao_rreo
    ):
        if (
            row.anexo
            and "01" in row.anexo
            and _COD_CONTA_ALVO in _norm(row.cod_conta)
            and _is_no_bimestre(row.coluna)
        ):
            valor = num(row.valor)
            break
    if valor is None:
        return 0

    return repository.replace_silver_rows(
        session,
        TransferenciaGenerica,
        keys={
            "cod_ibge": cod_ibge,
            "ano": ano,
            "mes": mes,
            "versao_entrega": FONTE_DERIVADA,
        },
        rows=[
            {
                "cod_ibge": cod_ibge,
                "tipo": TIPO_ICMS,
                "ano": ano,
                "mes": mes,
                "valor": valor,
                "fonte": FONTE_DERIVADA,
                "valid_time": date(ano, min(mes, 12), 1),
                "versao_entrega": FONTE_DERIVADA,
            }
        ],
    )


def derivar_icms_ente(session: Session, *, cod_ibge: str) -> int:
    """Deriva a cota-parte de todos os períodos RREO bimestrais vigentes do ente."""
    total = 0
    periodos = sorted(
        session.scalars(
            select(SilverRreo.periodo)
            .where(SilverRreo.cod_ibge == cod_ibge, SilverRreo.periodo.like("%-B%"))
            .distinct()
        )
    )
    for periodo in periodos:
        versao = repository.resolve_versao(
            session, cod_ibge=cod_ibge, relatorio="RREO", periodo=periodo
        )
        if versao is not None:
            total += derivar_icms_cota_parte(
                session, cod_ibge=cod_ibge, periodo=periodo, versao_rreo=versao
            )
    return total
