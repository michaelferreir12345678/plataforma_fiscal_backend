"""Modelos do dicionário semântico (Sprint IA-2) — migration ``0045``.

As três tabelas moram em ``gold`` porque significado descreve o dado **público
compartilhado**: a fórmula do limite de pessoal é a mesma para toda organização que olha
o mesmo município. Nada aqui é de tenant, e por isso nenhuma tem RLS.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, CheckConstraint, Date, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

#: Sentidos possíveis de um indicador. ``gerencial`` é o que **não** tem limite legal —
#: e precisa ser dito, porque "sem faixa" lido como "dentro do limite" é falso conforto.
SENTIDO_TETO = "teto"
SENTIDO_PISO = "piso"
SENTIDO_GERENCIAL = "gerencial"


class DicionarioIndicador(Base):
    """gold.dicionario_indicador — o verbete de um indicador da gold.

    Guarda o que ``gold.mart_indicador`` não consegue dizer sobre si mesmo: o que o número
    significa, como se chega nele, **qual é o denominador correto** e qual dispositivo o
    fundamenta. Não guarda percentuais de limite: esses já são dado em
    ``gold.dim_limite_legal`` (§2 do CLAUDE.md), e duplicá-los aqui criaria uma segunda
    régua para o mesmo teto.
    """

    __tablename__ = "dicionario_indicador"
    __table_args__ = (
        CheckConstraint(
            "sentido IN ('teto', 'piso', 'gerencial')", name="ck_dicionario_indicador_sentido"
        ),
        CheckConstraint("tabela_origem NOT LIKE 'op.%'", name="ck_dicionario_indicador_sem_op"),
        {"schema": "gold"},
    )

    codigo: Mapped[str] = mapped_column(Text, primary_key=True)
    rotulo: Mapped[str] = mapped_column(Text, nullable=False)
    definicao: Mapped[str] = mapped_column(Text, nullable=False)
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    #: Código do denominador como ele é **gravado** em ``mart_indicador.denominador``.
    denominador: Mapped[str] = mapped_column(Text, nullable=False)
    #: Denominador de reserva. Existe porque a plataforma tem esse caso de verdade: sem a
    #: RCL Ajustada publicada, pessoal e DCL caem para a RCL cheia — e a linha declara qual
    #: usou. Um consumidor que assuma o canônico erra silenciosamente nesses entes.
    denominador_fallback: Mapped[str | None] = mapped_column(Text, nullable=True)
    denominador_definicao: Mapped[str] = mapped_column(Text, nullable=False)
    unidade: Mapped[str] = mapped_column(Text, nullable=False)
    sentido: Mapped[str] = mapped_column(String(10), nullable=False)
    base_legal: Mapped[str] = mapped_column(Text, nullable=False)
    tabela_origem: Mapped[str] = mapped_column(Text, nullable=False)
    coluna_valor: Mapped[str] = mapped_column(Text, nullable=False)
    coluna_base: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Vocabulário de negócio → esquema ("gasto com pessoal" ⇒ ``pessoal_executivo``).
    sinonimos: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    armadilha: Mapped[str | None] = mapped_column(Text, nullable=True)
    fonte_definicao: Mapped[str] = mapped_column(Text, nullable=False)
    atualizado_em: Mapped[date] = mapped_column(Date, nullable=False)


class DicionarioCampo(Base):
    """gold.dicionario_campo — o que uma coluna guarda, e o que ela **não** é.

    ``consultavel`` marca a coluna liberada para consulta analítica governada. A restrição
    de schema é do banco (``CHECK``), não de convenção: o §4.1 do plano de MCP proíbe
    consulta livre sobre ``op`` — dado da organização — e essa proibição não pode depender
    de alguém lembrar dela na hora de escrever a seed.
    """

    __tablename__ = "dicionario_campo"
    __table_args__ = (
        CheckConstraint("schema_nome <> 'op'", name="ck_dicionario_campo_sem_op"),
        CheckConstraint("schema_nome IN ('gold', 'silver')", name="ck_dicionario_campo_camada"),
        {"schema": "gold"},
    )

    schema_nome: Mapped[str] = mapped_column(Text, primary_key=True)
    tabela: Mapped[str] = mapped_column(Text, primary_key=True)
    coluna: Mapped[str] = mapped_column(Text, primary_key=True)
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    unidade: Mapped[str | None] = mapped_column(Text, nullable=True)
    chave: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consultavel: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    armadilha: Mapped[str | None] = mapped_column(Text, nullable=True)
    fonte_definicao: Mapped[str] = mapped_column(Text, nullable=False)
    atualizado_em: Mapped[date] = mapped_column(Date, nullable=False)

    @property
    def qualificado(self) -> str:
        return f"{self.schema_nome}.{self.tabela}.{self.coluna}"


class DicionarioJuncao(Base):
    """gold.dicionario_juncao — um caminho de junção **sancionado** entre tabelas gold.

    O risco que esta tabela existe para conter não é o ``JOIN`` que falha: é o que
    funciona e multiplica linha. Juntar ``mart_indicador`` a ``dim_entrega`` sem
    ``versao_entrega`` na chave faz cada retificação duplicar o ente, e o ``COUNT`` dobra
    sem que nada estoure. Por isso ``condicao`` e ``nota`` são parte do caminho, não
    comentário sobre ele.
    """

    __tablename__ = "dicionario_juncao"
    __table_args__ = (
        CheckConstraint(
            "cardinalidade IN ('1:1', 'n:1', '1:n')", name="ck_dicionario_juncao_cardinalidade"
        ),
        CheckConstraint(
            "origem_tabela NOT LIKE 'op.%' AND destino_tabela NOT LIKE 'op.%'",
            name="ck_dicionario_juncao_sem_op",
        ),
        CheckConstraint(
            "array_length(origem_colunas, 1) = array_length(destino_colunas, 1)",
            name="ck_dicionario_juncao_arity",
        ),
        {"schema": "gold"},
    )

    origem_tabela: Mapped[str] = mapped_column(Text, primary_key=True)
    destino_tabela: Mapped[str] = mapped_column(Text, primary_key=True)
    origem_colunas: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    destino_colunas: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    cardinalidade: Mapped[str] = mapped_column(String(8), nullable=False)
    condicao: Mapped[str | None] = mapped_column(Text, nullable=True)
    nota: Mapped[str] = mapped_column(Text, nullable=False)
    fonte_definicao: Mapped[str] = mapped_column(Text, nullable=False)
    atualizado_em: Mapped[date] = mapped_column(Date, nullable=False)
