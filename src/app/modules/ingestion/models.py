"""Modelos ORM do medallion (bronze/silver/gold) — Sprint 1.

O dado fiscal do SICONFI é público → lago compartilhado, **sem RLS** (não é dado de
tenant). Bitemporalidade: silver carrega ``valid_time`` (período fiscal) e
``versao_entrega``; ``gold.dim_entrega`` controla qual versão é vigente.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# Identificadores de fonte (uma partição de bronze por fonte). Espelha FONTES na migration.
FONTE_RREO = "siconfi_rreo"
FONTE_RGF = "siconfi_rgf"
FONTE_DCA = "siconfi_dca"
FONTE_MSC = "siconfi_msc"
FONTE_EXTRATOS = "siconfi_extratos"
FONTE_ENTES = "siconfi_entes"

# Fontes complementares (Sprint 1B).
FONTE_SADIPEM_PVL = "sadipem_pvl"
FONTE_SADIPEM_OP = "sadipem_op_contratada"
FONTE_SADIPEM_CRONOGRAMA = "sadipem_cronograma_pgto"
FONTE_SADIPEM_CDP = "sadipem_cdp"
FONTE_BCB = "bcb"
FONTE_IBGE_POPULACAO = "ibge_populacao"
FONTE_IBGE_PIB = "ibge_pib"

# Fontes baseadas em arquivo (planilhas) — Sprint 1B.
FONTE_FPM = "tesouro_fpm"
FONTE_FUNDEB = "fnde_fundeb_repasse"
FONTE_TRANSFERENCIA = "transferencia_generica"
FONTE_CAPAG = "tesouro_capag"
FONTE_SIOPS = "siops_saude"
FONTE_SIOPE = "siope_educacao"


class RawPayload(Base):
    """bronze.raw_payload — payload cru imutável, particionado por LIST(fonte)."""

    __tablename__ = "raw_payload"
    __table_args__ = {"schema": "bronze"}

    fonte: Mapped[str] = mapped_column(Text, primary_key=True)
    cod_ibge: Mapped[str] = mapped_column(Text, primary_key=True)
    periodo: Mapped[str] = mapped_column(Text, primary_key=True)
    versao: Mapped[str] = mapped_column(Text, primary_key=True)
    ano: Mapped[int] = mapped_column(Integer, nullable=False)
    ingerido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    hash_payload: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)


class DimEntrega(Base):
    """gold.dim_entrega — controle de versões/retificação (bitemporal)."""

    __tablename__ = "dim_entrega"
    __table_args__ = (
        UniqueConstraint(
            "cod_ibge", "relatorio", "periodo", "versao_entrega", name="uq_dim_entrega_chave"
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    relatorio: Mapped[str] = mapped_column(Text, nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)
    homologada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    vigente: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    hash_payload: Mapped[str | None] = mapped_column(Text, nullable=True)


class _SilverRelatorioMixin:
    """Colunas comuns dos silver tipados por relatório (RREO/RGF/DCA)."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    anexo: Mapped[str | None] = mapped_column(Text, nullable=True)
    conta: Mapped[str | None] = mapped_column(Text, nullable=True)  # descrição da linha
    cod_conta: Mapped[str | None] = mapped_column(Text, nullable=True)  # slug estável do STN
    coluna: Mapped[str | None] = mapped_column(Text, nullable=True)
    poder: Mapped[str | None] = mapped_column(Text, nullable=True)  # co_poder (E/L/J/M/D) — RGF
    linha_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)  # ordem no payload
    valor: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    valid_time: Mapped[date | None] = mapped_column(Date, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class SilverRreo(Base, _SilverRelatorioMixin):
    __tablename__ = "siconfi_rreo"
    __table_args__ = {"schema": "silver"}


class SilverRgf(Base, _SilverRelatorioMixin):
    __tablename__ = "siconfi_rgf"
    __table_args__ = {"schema": "silver"}


class SilverDca(Base, _SilverRelatorioMixin):
    __tablename__ = "siconfi_dca"
    __table_args__ = {"schema": "silver"}


class SilverMsc(Base):
    """silver.siconfi_msc — granularidade máxima (conta PCASP)."""

    __tablename__ = "siconfi_msc"
    __table_args__ = {"schema": "silver"}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    cod_conta_pcasp: Mapped[str] = mapped_column(Text, nullable=False)
    natureza: Mapped[str | None] = mapped_column(String(1), nullable=True)  # D/C (natureza_conta)
    saldo_inicial: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    mov_devedor: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    mov_credor: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    saldo_final: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    valid_time: Mapped[date | None] = mapped_column(Date, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class SilverEnte(Base):
    """silver.siconfi_entes — cadastro dos entes."""

    __tablename__ = "siconfi_entes"
    __table_args__ = {"schema": "silver"}

    cod_ibge: Mapped[str] = mapped_column(String(7), primary_key=True)
    nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True)
    esfera: Mapped[str | None] = mapped_column(String(1), nullable=True)
    populacao: Mapped[int | None] = mapped_column(Integer, nullable=True)
    regiao: Mapped[str | None] = mapped_column(Text, nullable=True)
    capital: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    versao_entrega: Mapped[str | None] = mapped_column(Text, nullable=True)
    carregado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SilverExtrato(Base):
    """silver.siconfi_extratos — entregas homologadas; dispara reprocessamento."""

    __tablename__ = "siconfi_extratos"
    __table_args__ = (
        UniqueConstraint(
            "cod_ibge", "relatorio", "periodo", "versao", name="uq_siconfi_extratos_chave"
        ),
        {"schema": "silver"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    relatorio: Mapped[str] = mapped_column(Text, nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    homologada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    versao: Mapped[str] = mapped_column(Text, nullable=False)


class SadipemPvl(Base):
    """silver.sadipem_pvl — pedidos de verificação de limites (dívida/operações)."""

    __tablename__ = "sadipem_pvl"
    __table_args__ = {"schema": "silver"}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    id_pvl: Mapped[str | None] = mapped_column(Text, nullable=True)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    tipo_operacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    decisao: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_analise: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_time: Mapped[date | None] = mapped_column(Date, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class SadipemOpContratada(Base):
    """silver.sadipem_op_contratada — operações de crédito contratadas."""

    __tablename__ = "sadipem_op_contratada"
    __table_args__ = {"schema": "silver"}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    id_operacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    tipo_operacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    credor: Mapped[str | None] = mapped_column(Text, nullable=True)
    moeda: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor_contratado: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    data_contratacao: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_time: Mapped[date | None] = mapped_column(Date, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class SadipemCronogramaPgto(Base):
    """silver.sadipem_cronograma_pgto — cronograma de pagamento (serviço da dívida)."""

    __tablename__ = "sadipem_cronograma_pgto"
    __table_args__ = {"schema": "silver"}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    id_operacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    ano: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    principal: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    juros: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    encargos: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    valid_time: Mapped[date | None] = mapped_column(Date, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class SadipemCdp(Base):
    """silver.sadipem_cdp — situação no Cadastro da Dívida Pública."""

    __tablename__ = "sadipem_cdp"
    __table_args__ = {"schema": "silver"}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    data_ref: Mapped[date | None] = mapped_column(Date, nullable=True)
    situacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_time: Mapped[date | None] = mapped_column(Date, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class BcbIndice(Base):
    """silver.bcb_indice — séries do SGS/BCB em long format (data×série)."""

    __tablename__ = "bcb_indice"
    __table_args__ = {"schema": "silver"}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    codigo_serie: Mapped[int] = mapped_column(Integer, nullable=False)
    data_ref: Mapped[date] = mapped_column(Date, nullable=False)
    valor: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    valid_time: Mapped[date | None] = mapped_column(Date, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class IbgePopulacao(Base):
    """silver.ibge_populacao — população por ano de referência."""

    __tablename__ = "ibge_populacao"
    __table_args__ = {"schema": "silver"}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    ano_ref: Mapped[int] = mapped_column(Integer, nullable=False)
    populacao: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fonte: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_time: Mapped[date | None] = mapped_column(Date, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class IbgePib(Base):
    """silver.ibge_pib — PIB municipal por ano de referência."""

    __tablename__ = "ibge_pib"
    __table_args__ = {"schema": "silver"}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    ano_ref: Mapped[int] = mapped_column(Integer, nullable=False)
    pib_nominal: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    pib_per_capita: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    valid_time: Mapped[date | None] = mapped_column(Date, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class TesouroFpm(Base):
    """silver.tesouro_fpm — repasses de FPM/FPE (planilha Tesouro Transparente)."""

    __tablename__ = "tesouro_fpm"
    __table_args__ = {"schema": "silver"}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    ano: Mapped[int] = mapped_column(Integer, nullable=False)
    mes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decendio: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valor_bruto: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    deducoes: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    valor_liquido: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    valid_time: Mapped[date | None] = mapped_column(Date, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class FndeFundebRepasse(Base):
    """silver.fnde_fundeb_repasse — repasses mensais do FUNDEB por município (FNDE)."""

    __tablename__ = "fnde_fundeb_repasse"
    __table_args__ = {"schema": "silver"}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    ano: Mapped[int] = mapped_column(Integer, nullable=False)
    mes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valor_repassado: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    complementacao_uniao: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    valid_time: Mapped[date | None] = mapped_column(Date, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class TransferenciaGenerica(Base):
    """silver.transferencia_generica — demais transferências (ICMS, royalties, etc.)."""

    __tablename__ = "transferencia_generica"
    __table_args__ = {"schema": "silver"}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    ano: Mapped[int] = mapped_column(Integer, nullable=False)
    mes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valor: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    fonte: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_time: Mapped[date | None] = mapped_column(Date, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class TesouroCapag(Base):
    """silver.tesouro_capag — nota CAPAG (A–D) + subindicadores (planilha anual)."""

    __tablename__ = "tesouro_capag"
    __table_args__ = {"schema": "silver"}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    ano_ref: Mapped[int] = mapped_column(Integer, nullable=False)
    nota_final: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ind_endividamento: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    ind_poupanca: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    ind_liquidez: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    metodologia_versao: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_time: Mapped[date | None] = mapped_column(Date, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class SiopsSaude(Base):
    """silver.siops_saude — indicadores SIOPS (saúde) em long format."""

    __tablename__ = "siops_saude"
    __table_args__ = {"schema": "silver"}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    ano: Mapped[int] = mapped_column(Integer, nullable=False)
    bimestre: Mapped[int | None] = mapped_column(Integer, nullable=True)
    indicador_codigo: Mapped[str] = mapped_column(Text, nullable=False)
    indicador_descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    unidade: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    valid_time: Mapped[date | None] = mapped_column(Date, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class SiopeEducacao(Base):
    """silver.siope_educacao — indicadores SIOPE (educacao) em long format."""

    __tablename__ = "siope_educacao"
    __table_args__ = {"schema": "silver"}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    ano: Mapped[int] = mapped_column(Integer, nullable=False)
    bimestre: Mapped[int | None] = mapped_column(Integer, nullable=True)
    indicador_codigo: Mapped[str] = mapped_column(Text, nullable=False)
    indicador_descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    unidade: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    valid_time: Mapped[date | None] = mapped_column(Date, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class IngestionLog(Base):
    """gold.ingestion_log — observabilidade dos jobs de ingestão."""

    __tablename__ = "ingestion_log"
    __table_args__ = {"schema": "gold"}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    fonte: Mapped[str] = mapped_column(Text, nullable=False)
    cod_ibge: Mapped[str | None] = mapped_column(String(7), nullable=True)
    periodo: Mapped[str | None] = mapped_column(Text, nullable=True)
    versao: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    mensagem: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
