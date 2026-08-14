"""Leitura do conjunto dourado — as perguntas são **dado**, não código.

O conjunto vive em ``conjunto_dourado.json``, ao lado deste módulo. Ficar em arquivo, e
não espalhado em funções de teste, é o que permite revisar a cobertura lendo um diff:
"entraram seis perguntas de dívida estadual" é uma frase que um diff de JSON mostra e um
diff de código não.

**O que o arquivo NÃO contém: o valor esperado.** Nenhuma entrada diz "a RCL é
812.345.678,90". O gabarito é derivado do banco no momento da execução
(:mod:`app.modules.evaluation.gabarito`), pelo mesmo motivo que o dicionário da IA-2
deriva a definição do registro canônico: número escrito à mão em arquivo de teste
envelhece em silêncio, e um conjunto dourado que envelheceu em silêncio é pior que
conjunto nenhum — ele aprova o que deveria reprovar.

O arquivo também não nomeia código IBGE: nomeia **papéis** (``municipal_com_dado``,
``fora_do_escopo``), resolvidos por :mod:`app.modules.evaluation.cenario`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

#: Caminho do conjunto versionado. Único lugar do código que conhece o nome do arquivo.
ARQUIVO_CONJUNTO = Path(__file__).with_name("conjunto_dourado.json")

# As três respostas difíceis da ficha da IA-6.
CATEGORIA_EXISTE = "existe"
CATEGORIA_AUSENTE = "ausente"
CATEGORIA_DEFASADO = "defasado"
CATEGORIAS = (CATEGORIA_EXISTE, CATEGORIA_AUSENTE, CATEGORIA_DEFASADO)

# As quatro famílias da bateria adversária.
FAMILIA_INJECAO = "injecao"
FAMILIA_PARECER = "parecer_juridico"
FAMILIA_ESTIMATIVA = "estimativa_ausente"
FAMILIA_EXFILTRACAO = "exfiltracao"
FAMILIAS = (FAMILIA_INJECAO, FAMILIA_PARECER, FAMILIA_ESTIMATIVA, FAMILIA_EXFILTRACAO)


@dataclass(frozen=True)
class PerguntaDourada:
    """Uma pergunta do conjunto e a **expectativa** — nunca o valor esperado."""

    id: str
    categoria: str
    ente: str
    periodo: str | None
    pergunta: str
    #: Indicador cujo valor a resposta tem de citar (``existe``/``defasado``) ou cuja
    #: ausência tem de declarar (``ausente``). ``None`` só em pergunta panorâmica.
    indicador: str | None = None
    nota: str | None = None


@dataclass(frozen=True)
class ValorProibido:
    """Valor que a resposta não pode conter, derivado do banco na hora de rodar.

    Existe para a exfiltração: o número do ente vizinho não pode estar escrito no arquivo
    (seria gabarito à mão), e sem ele o teste não teria como saber que vazou.
    """

    ente: str
    indicador: str
    periodo: str | None = None


@dataclass(frozen=True)
class PerguntaAdversaria:
    """Um ataque e o que a plataforma tem de fazer com ele."""

    id: str
    familia: str
    ente: str
    periodo: str | None
    pergunta: str
    #: Indicador cuja ausência o ataque tenta fazer o assistente preencher por estimativa.
    indicador: str | None = None
    #: Literais que não podem aparecer na resposta (o número plantado pela injeção).
    proibido: tuple[str, ...] = ()
    #: Valores a derivar do banco e proibir na resposta (exfiltração).
    proibido_derivado: tuple[ValorProibido, ...] = ()
    #: ``True`` quando o ataque tem de morrer na borda com 403, sem chegar ao modelo.
    espera_403: bool = False
    nota: str | None = None


@dataclass(frozen=True)
class Preco:
    """Preço declarado de um modelo. **Entrada de configuração, não medição.**

    A latência a avaliação mede; o preço ela não tem como medir — ele vem da tabela do
    fornecedor. Fica versionado aqui, com data, para que o custo por resposta do relatório
    seja auditável: quem lê sabe de que tabela saiu e quando ela foi conferida.
    """

    modelo: str
    entrada_usd_por_milhao: Decimal
    saida_usd_por_milhao: Decimal
    fonte: str
    declarado_em: str

    def custo_usd(self, tokens_entrada: int, tokens_saida: int) -> Decimal:
        milhao = Decimal(1_000_000)
        return (
            Decimal(tokens_entrada) / milhao * self.entrada_usd_por_milhao
            + Decimal(tokens_saida) / milhao * self.saida_usd_por_milhao
        )


@dataclass(frozen=True)
class Conjunto:
    versao: str
    descricao: str
    perguntas: tuple[PerguntaDourada, ...]
    adversarias: tuple[PerguntaAdversaria, ...]
    precos: dict[str, Preco]

    def por_categoria(self, categoria: str) -> tuple[PerguntaDourada, ...]:
        return tuple(p for p in self.perguntas if p.categoria == categoria)

    def contagem(self) -> dict[str, int]:
        contagem = {cat: len(self.por_categoria(cat)) for cat in CATEGORIAS}
        contagem["adversarial"] = len(self.adversarias)
        return contagem

    def preco(self, modelo: str) -> Preco | None:
        return self.precos.get(modelo)


def _ler_pergunta(bruto: dict[str, Any], indice: int) -> PerguntaDourada:
    categoria = str(bruto.get("categoria", ""))
    if categoria not in CATEGORIAS:
        raise ValueError(
            f"Pergunta #{indice} ({bruto.get('id')}): categoria {categoria!r} não é uma das "
            f"três respostas difíceis {CATEGORIAS}."
        )
    return PerguntaDourada(
        id=str(bruto["id"]),
        categoria=categoria,
        ente=str(bruto["ente"]),
        periodo=bruto.get("periodo"),
        pergunta=str(bruto["pergunta"]),
        indicador=bruto.get("indicador"),
        nota=bruto.get("nota"),
    )


def _ler_adversaria(bruto: dict[str, Any], indice: int) -> PerguntaAdversaria:
    familia = str(bruto.get("familia", ""))
    if familia not in FAMILIAS:
        raise ValueError(
            f"Adversária #{indice} ({bruto.get('id')}): família {familia!r} desconhecida "
            f"(esperado uma de {FAMILIAS})."
        )
    return PerguntaAdversaria(
        id=str(bruto["id"]),
        familia=familia,
        ente=str(bruto["ente"]),
        periodo=bruto.get("periodo"),
        pergunta=str(bruto["pergunta"]),
        indicador=bruto.get("indicador"),
        proibido=tuple(str(x) for x in bruto.get("proibido", ())),
        proibido_derivado=tuple(
            ValorProibido(
                ente=str(item["ente"]),
                indicador=str(item["indicador"]),
                periodo=item.get("periodo"),
            )
            for item in bruto.get("proibido_derivado", ())
        ),
        espera_403=bool(bruto.get("espera_403", False)),
        nota=bruto.get("nota"),
    )


def carregar_conjunto(caminho: Path | None = None) -> Conjunto:
    """Lê e valida o conjunto. Arquivo malformado falha **aqui**, não no meio da execução."""
    alvo = caminho or ARQUIVO_CONJUNTO
    bruto = json.loads(alvo.read_text(encoding="utf-8"))
    perguntas = tuple(
        _ler_pergunta(item, i) for i, item in enumerate(bruto.get("perguntas", []), start=1)
    )
    adversarias = tuple(
        _ler_adversaria(item, i) for i, item in enumerate(bruto.get("adversarial", []), start=1)
    )
    ids = [p.id for p in perguntas] + [a.id for a in adversarias]
    duplicados = sorted({i for i in ids if ids.count(i) > 1})
    if duplicados:
        raise ValueError(f"IDs repetidos no conjunto dourado: {duplicados}")
    precos = {
        modelo: Preco(
            modelo=modelo,
            entrada_usd_por_milhao=Decimal(str(item["entrada_usd_por_milhao"])),
            saida_usd_por_milhao=Decimal(str(item["saida_usd_por_milhao"])),
            fonte=str(item.get("fonte", "não declarada")),
            declarado_em=str(item.get("declarado_em", "não declarado")),
        )
        for modelo, item in (bruto.get("precos") or {}).items()
    }
    return Conjunto(
        versao=str(bruto.get("versao", "sem-versao")),
        descricao=str(bruto.get("descricao", "")),
        perguntas=perguntas,
        adversarias=adversarias,
        precos=precos,
    )


@lru_cache(maxsize=1)
def conjunto_padrao() -> Conjunto:
    """O conjunto versionado do repositório (memorizado — o arquivo não muda em execução)."""
    return carregar_conjunto()
