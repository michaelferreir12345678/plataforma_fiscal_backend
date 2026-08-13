"""Catálogo de ferramentas da Sprint IA-1a — duas, e nenhum cálculo fiscal novo.

As duas ferramentas desta fatia vertical existem para provar o contrato, não para cobrir
o produto (a ampliação é a IA-1b):

- ``indicador_do_ente`` — devolve um indicador **já materializado** em ``gold``, com faixa,
  limite legal, memória de cálculo, providência e ``source_ref``. É o que faz `garantias`,
  `operacoes_credito` e os demais códigos do mart deixarem de ser inalcançáveis para a IA:
  em vez de um pacote de fatos escolhido *antes* da pergunta por um dicionário de 17
  palavras (``assistant/retriever.py``), o indicador passa a ser **pedido pelo nome**.
- ``linhagem_do_indicador`` — responde "de onde vem este número e o que ele afeta",
  reusando o grafo de ``gold.lineage_edge`` (Sprint 26).

**Zero cálculo aqui dentro.** Os valores saem de ``indicators``/``limits``, que são a fonte
única (§7 do CLAUDE.md); esta camada lê, tipa e rotula. Se uma ferramenta futura precisar
somar ou dividir algo fiscal, o lugar é ``indicators/`` — e o número tem de ficar
materializado antes de chegar aqui.

**Ausência é resposta, não exceção.** Quando o indicador não está materializado, a
ferramenta devolve ``disponivel=false`` com uma observação explicando o quê e o porquê, em
vez de estourar: é assim que o assistente consegue *sinalizar a lacuna* (§9) em vez de
ficar sem resposta — e sem nenhum número no payload, a guarda de ``source_ref`` continua
satisfeita por construção.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, ValidationError

from app.core.errors import AppError
from app.modules.benchmark.service import unidade_da_metrica
from app.modules.catalog import service as catalog_service
from app.modules.indicators import repository as indicators_repo
from app.modules.indicators import rotulos
from app.modules.ingestion import repository as ingestion_repo
from app.modules.limits import service as limits_service
from app.modules.quality import service as quality_service
from app.modules.reports.service import formatar_valor
from app.shared.source_ref import SourceRef
from app.shared.tooling.base import (
    EnteToolInput,
    Tool,
    ToolContext,
    ToolInput,
    ToolOutput,
    ToolRegistry,
)

RELATORIO_ANCORA = "RREO"

#: Unidade do mart → unidade de formatação do relatório (fonte única de formatação).
_UNIDADE_FORMATO = {
    "brl": "BRL",
    "brl_per_capita": "BRL",
}

#: Tabela ``gold`` onde cada indicador é materializado (o resto vive no mart).
_TABELA_DO_INDICADOR = {
    "rcl": "gold.fato_rcl",
    "disponibilidade": "gold.fato_disponibilidade",
}
_TABELA_PADRAO = "gold.mart_indicador"


# --------------------------------------------------------------------------- #
# indicador_do_ente
# --------------------------------------------------------------------------- #
class IndicadorDoEnteIn(EnteToolInput):
    """Entrada de ``indicador_do_ente``."""

    indicador: str = Field(
        min_length=2,
        max_length=64,
        description=(
            "Código do indicador em gold.mart_indicador: pessoal_executivo, "
            "divida_consolidada_liquida, operacoes_credito, garantias, saude_minimo, "
            "educacao_mde, fundeb_profissionais, investimento_rcl, rcl_per_capita, "
            "resultado_primario_rcl."
        ),
    )
    periodo: str | None = Field(
        default=None,
        max_length=16,
        description="Período fiscal canônico (ex.: '2024-B6'). Ausente ⇒ o mais recente.",
    )


class ProvidenciaFerramenta(ToolOutput):
    faixa: str
    texto: str
    base_legal: str | None = None


class IndicadorDoEnteOut(ToolOutput):
    """Saída de ``indicador_do_ente`` — número materializado + o que o qualifica."""

    ente: str
    ente_nome: str | None = None
    esfera: str | None = None
    indicador: str
    rotulo: str
    periodo: str | None = None
    disponivel: bool
    valor_rs: Decimal | None = None
    #: Percentual sobre ``denominador`` (RCL nos limites da LRF; base própria nos mínimos).
    valor_pct: Decimal | None = None
    denominador: str | None = None
    base_valor: Decimal | None = None
    valor_formatado: str = "Dado não disponível"
    unidade: str | None = None
    faixa: str | None = None
    #: ``None`` quando o indicador não tem limite legal (os gerenciais).
    teto_pct: Decimal | None = None
    versao_entrega: str | None = None
    as_of: datetime | None = None
    memoria: dict[str, Any] = Field(default_factory=dict)
    providencias: list[ProvidenciaFerramenta] = Field(default_factory=list)
    #: Por que não há número, quando não há. Preenchido só no caso de ausência.
    observacao: str | None = None
    source_ref: SourceRef | None = None

    def linhas(self) -> int:
        return 1 if self.disponivel else 0


def _indisponivel(
    entrada: IndicadorDoEnteIn, *, periodo: str | None, motivo: str
) -> IndicadorDoEnteOut:
    return IndicadorDoEnteOut(
        ente=entrada.ente,
        indicador=entrada.indicador,
        rotulo=rotulos.rotulo(entrada.indicador),
        periodo=periodo,
        disponivel=False,
        as_of=entrada.as_of,
        observacao=motivo,
    )


def _fonte_do_indicador(bruto: dict | None, alternativa: SourceRef) -> SourceRef:
    """A fonte gravada na linha do mart manda; o detalhe do Monitor é a reserva.

    ``limits.build_limite_detail`` compõe o ``source_ref`` como RREO/Anexo 03 para
    qualquer indicador, porque é dali que sai a RCL do denominador. Está certo para
    pessoal e DCL sobre a RCL, mas ``garantias`` e ``operacoes_credito`` são apurados do
    **RGF** — e atribuir ao RREO um número que o ente publicou no RGF é errar a
    procedência com aparência de rigor. A linha do mart carrega o relatório/anexo que a
    materializou; é ela que responde "de onde veio".
    """
    if bruto and bruto.get("relatorio"):
        try:
            return SourceRef.model_validate(bruto)
        except ValidationError:
            pass
    return alternativa


def _periodo_efetivo(ctx: ToolContext, entrada: IndicadorDoEnteIn) -> str | None:
    if entrada.periodo:
        return entrada.periodo
    ultima = ingestion_repo.resolve_latest_entrega(
        ctx.session, cod_ibge=entrada.ente, relatorio=RELATORIO_ANCORA, as_of=entrada.as_of
    )
    return ultima.periodo if ultima else None


def executar_indicador_do_ente(ctx: ToolContext, entrada: IndicadorDoEnteIn) -> IndicadorDoEnteOut:
    """Lê um indicador materializado do ente — sem recalcular nada (§7).

    O ``as_of`` percorre todo o caminho: resolve a versão da entrega vigente **naquele
    instante** e lê a linha do mart daquela versão. Uma retificação posterior não muda a
    resposta de uma consulta histórica — que é o requisito de auditoria da §6.5.
    """
    periodo = _periodo_efetivo(ctx, entrada)
    if periodo is None:
        return _indisponivel(
            entrada,
            periodo=None,
            motivo=(
                f"Não há entrega de {RELATORIO_ANCORA} registrada para o ente "
                f"{entrada.ente}; sem entrega não há período a apurar."
            ),
        )

    versao = indicators_repo.resolve_versao_rreo(
        ctx.session, cod_ibge=entrada.ente, periodo=periodo, as_of=entrada.as_of
    )
    if versao is None:
        return _indisponivel(
            entrada,
            periodo=periodo,
            motivo=(
                f"Sem {RELATORIO_ANCORA} vigente para {entrada.ente} em {periodo}"
                + (f" no as_of {entrada.as_of.isoformat()}" if entrada.as_of else "")
                + "."
            ),
        )

    mart = indicators_repo.get_mart_indicador(
        ctx.session,
        cod_ibge=entrada.ente,
        periodo=periodo,
        indicador=entrada.indicador,
        versao_entrega=versao,
    )
    if mart is None:
        return _indisponivel(
            entrada,
            periodo=periodo,
            motivo=(
                f"O indicador '{entrada.indicador}' não está materializado para "
                f"{entrada.ente} em {periodo} (entrega {versao}). A plataforma não estima "
                f"o que não apurou."
            ),
        )

    # Reuso do detalhe do Monitor de Limites: memória de cálculo, providência legal da
    # faixa e ``as_of`` efetivo já resolvidos lá — nada disso é recalculado aqui.
    detalhe = limits_service.build_limite_detail(
        ctx.session, entrada.ente, periodo, entrada.indicador, as_of=entrada.as_of
    )
    ente_dim = catalog_service.refresh_dim_ente(ctx.session, entrada.ente)
    unidade = unidade_da_metrica(mart)
    principal_valor = mart.valor_pct_rcl if mart.valor_pct_rcl is not None else mart.valor_rs
    formato = "PERCENTUAL" if mart.valor_pct_rcl is not None else _UNIDADE_FORMATO.get(unidade, "")

    return IndicadorDoEnteOut(
        ente=entrada.ente,
        ente_nome=ente_dim.nome if ente_dim else None,
        esfera=detalhe.esfera,
        indicador=entrada.indicador,
        rotulo=rotulos.rotulo(entrada.indicador),
        periodo=periodo,
        disponivel=True,
        valor_rs=mart.valor_rs,
        valor_pct=mart.valor_pct_rcl,
        denominador=mart.denominador,
        base_valor=mart.base_valor,
        valor_formatado=formatar_valor(principal_valor, formato),
        unidade=unidade,
        faixa=mart.faixa,
        # ``build_limite_detail`` devolve 0 onde não há limite legal; "teto de 0%" lido por
        # um modelo vira "o limite é zero", que é o oposto de "não há limite".
        teto_pct=detalhe.teto_pct or None,
        versao_entrega=versao,
        as_of=detalhe.as_of,
        memoria=detalhe.memoria or {},
        providencias=[
            ProvidenciaFerramenta(
                faixa=p.faixa, texto=p.texto, base_legal=p.base_legal
            )
            for p in detalhe.providencias
        ],
        source_ref=_fonte_do_indicador(mart.source_ref, detalhe.source_ref),
    )


# --------------------------------------------------------------------------- #
# linhagem_do_indicador
# --------------------------------------------------------------------------- #
class LinhagemDoIndicadorIn(ToolInput):
    """Entrada de ``linhagem_do_indicador`` — não recebe ente: o grafo é do sistema.

    O caminho do dado (fonte → bronze → silver → gold → endpoint → página) é o mesmo para
    todos os entes; ele descreve a plataforma, não o fisco de ninguém. Por isso a
    ferramenta **não** declara ``recebe_ente`` — e, por construção do registro, também não
    consegue aceitar um ``ente`` por baixo do pano.
    """

    indicador: str = Field(
        min_length=2,
        max_length=64,
        description="Código do indicador cuja procedência se quer explicar.",
    )


class ArestaLinhagem(ToolOutput):
    origem: str
    destino: str
    tipo: str
    nota: str | None = None


class LinhagemDoIndicadorOut(ToolOutput):
    """Procedência e alcance de um indicador. Metadado — não carrega número fiscal."""

    indicador: str
    rotulo: str
    tabela_gold: str
    fontes_de_origem: list[str] = Field(default_factory=list)
    tabelas_montante: list[str] = Field(default_factory=list)
    endpoints: list[str] = Field(default_factory=list)
    paginas_afetadas: list[str] = Field(default_factory=list)
    caminho: list[ArestaLinhagem] = Field(default_factory=list)
    total_arestas: int = 0
    observacao: str | None = None

    def linhas(self) -> int:
        return len(self.caminho)


def executar_linhagem_do_indicador(
    ctx: ToolContext, entrada: LinhagemDoIndicadorIn
) -> LinhagemDoIndicadorOut:
    """Explica de onde vem o indicador, reusando ``gold.lineage_edge`` (Sprint 26)."""
    tabela = _TABELA_DO_INDICADOR.get(entrada.indicador, _TABELA_PADRAO)
    observacao = None
    if entrada.indicador not in rotulos.ROTULOS:
        observacao = (
            f"'{entrada.indicador}' não é um código canônico de indicador; a linhagem "
            f"devolvida é a da tabela {tabela}, onde os indicadores são materializados."
        )
    grafo = quality_service.lineage(ctx.session, no=tabela)
    montante = [
        ArestaLinhagem(
            origem=a.origem, destino=a.destino, tipo=a.tipo, nota=(a.detalhe or {}).get("nota")
        )
        for a in grafo.montante
    ]
    jusante = [
        ArestaLinhagem(
            origem=a.origem, destino=a.destino, tipo=a.tipo, nota=(a.detalhe or {}).get("nota")
        )
        for a in grafo.jusante
    ]
    tabelas = sorted(
        {a.origem for a in grafo.montante if a.origem.split(".")[0] in ("bronze", "silver", "gold")}
    )
    endpoints = sorted({a.destino for a in grafo.jusante if a.tipo == "gold_endpoint"})
    return LinhagemDoIndicadorOut(
        indicador=entrada.indicador,
        rotulo=rotulos.rotulo(entrada.indicador),
        tabela_gold=tabela,
        fontes_de_origem=list(grafo.fontes_de_origem),
        tabelas_montante=tabelas,
        endpoints=endpoints,
        paginas_afetadas=list(grafo.paginas_afetadas),
        caminho=montante + jusante,
        total_arestas=len(montante) + len(jusante),
        observacao=observacao,
    )


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #
def construir_registro() -> ToolRegistry:
    """Monta o registro da plataforma. Uma implementação, várias exposições (§2.2)."""
    registro = ToolRegistry()
    registro.register(
        Tool(
            nome="indicador_do_ente",
            descricao=(
                "Devolve um indicador fiscal já calculado e materializado de um ente "
                "(valor em R$, percentual sobre a base, faixa em relação ao limite legal, "
                "memória de cálculo, providência legal aplicável e fonte). Use para "
                "responder 'quanto é X do ente Y'. Nunca estima: quando o indicador não "
                "foi apurado, devolve disponivel=false com a explicação."
            ),
            entrada=IndicadorDoEnteIn,
            saida=IndicadorDoEnteOut,
            handler=_handler_indicador,
            capacidade="ver",
            recebe_ente=True,
            saida_tem_numero_fiscal=True,
        )
    )
    registro.register(
        Tool(
            nome="linhagem_do_indicador",
            descricao=(
                "Explica a procedência de um indicador: de qual fonte externa ele vem, por "
                "quais tabelas passa e quais páginas/endpoints dependem dele. Use para "
                "responder 'de onde vem este número' e 'o que quebra se a fonte falhar'. "
                "Devolve metadado de linhagem, não valores."
            ),
            entrada=LinhagemDoIndicadorIn,
            saida=LinhagemDoIndicadorOut,
            handler=_handler_linhagem,
            capacidade="ver",
            recebe_ente=False,
            saida_tem_numero_fiscal=False,
        )
    )
    return registro


def _handler_indicador(ctx: ToolContext, entrada: Any) -> ToolOutput:
    return executar_indicador_do_ente(ctx, entrada)


def _handler_linhagem(ctx: ToolContext, entrada: Any) -> ToolOutput:
    return executar_linhagem_do_indicador(ctx, entrada)


_REGISTRO: ToolRegistry | None = None


def registro() -> ToolRegistry:
    """Registro único do processo (carregado na primeira chamada)."""
    global _REGISTRO
    if _REGISTRO is None:
        _REGISTRO = construir_registro()
    return _REGISTRO


def erro_para_payload(exc: AppError) -> dict[str, Any]:
    """Converte a recusa de uma ferramenta no que o modelo pode ler e respeitar.

    Um 403 de escopo **não** vira uma exceção que derruba a conversa: vira um fato — "não
    posso acessar este ente" —, que é o que o assistente precisa dizer ao gestor. A
    garantia já foi aplicada; o que sobra é comunicá-la sem número nenhum junto.
    """
    return {
        "erro": exc.type,
        "titulo": exc.title,
        "detalhe": exc.detail,
        "status": exc.status,
    }
