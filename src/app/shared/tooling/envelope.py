"""Envelope de execução: o único caminho por onde uma ferramenta roda (Sprint IA-1a).

Toda chamada — venha do assistente, de um cliente MCP ou de uma tela — passa por
:func:`invoke`, que em ordem:

1. resolve a ferramenta no registro (nome desconhecido ⇒ 404, e **auditado**: nome
   inventado por um modelo é informação de operação);
2. exige a **capacidade RBAC** declarada;
3. valida a entrada contra o esquema (``extra='forbid'``, ver ``base.ToolInput``);
4. quando a ferramenta recebe ente, chama ``assert_ente_in_scope`` — **a função de
   ``shared/scope.py``**, nunca uma cópia da regra. É ela que distingue os dois 403
   (fora da carteira × sem licença) e é dentro dela que o gate de licença vive;
5. aplica o ``as_of`` (§6.5) e cronometra;
6. executa;
7. valida a saída contra o esquema **e** exige ``source_ref`` onde há número fiscal (G4);
8. grava a auditoria (G7) — inclusive quando qualquer etapa acima falhou.

**Por que a auditoria vai em transação própria.** O caminho de falha é o que mais importa
auditar, e é exatamente o que se perderia: a exceção sobe até o handler HTTP, ``get_db``
faz *rollback* e uma linha gravada na sessão da requisição desapareceria junto. A
auditoria abre a sua própria sessão de tenant (RLS por ``org_id``) e comita ali — o
registro de que a chamada aconteceu sobrevive à recusa da chamada.

Ordem deliberada: capacidade **antes** de escopo, escopo **antes** de executar. Quem não
tem a capacidade não descobre, pelo código do erro, se o ente existe na carteira de
alguém.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.core.db import tenant_session
from app.core.errors import AppError
from app.modules.assistant import repository as assistant_repo
from app.modules.tenancy import repository as tenancy_repo
from app.shared.scope import assert_ente_in_scope
from app.shared.tooling.base import EnteToolInput, Tool, ToolContext, ToolOutput, ToolRegistry
from app.shared.tooling.errors import (
    EntradaInvalidaError,
    FerramentaDesconhecidaError,
    FonteAusenteError,
    SaidaInvalidaError,
)
from app.shared.tooling.fonte import numeros_sem_fonte

logger = logging.getLogger(__name__)

ACAO_AUDITORIA = "FERRAMENTA_IA"
STATUS_OK = "ok"
STATUS_ERRO = "erro"


@dataclass(frozen=True)
class ToolCallResult:
    """O que o chamador recebe: a saída tipada + o payload pronto para o modelo."""

    ferramenta: str
    saida: ToolOutput
    #: Mesma saída serializada (``mode='json'``) — é isto que vai ao LLM/cliente MCP.
    payload: dict[str, Any]
    duracao_ms: int
    linhas: int
    as_of: datetime | None = None
    ente: str | None = None
    call_id: uuid.UUID | None = None


@dataclass
class _Registro:
    """Estado acumulado da chamada para a auditoria (existe mesmo quando tudo falha)."""

    ferramenta: str
    origem: str
    argumentos: dict[str, Any]
    cod_ibge: str | None = None
    as_of: datetime | None = None
    status: str = STATUS_ERRO
    erro_tipo: str | None = None
    erro_detalhe: str | None = None
    http_status: int | None = None
    linhas: int = 0
    duracao_ms: int = 0
    source_refs: list[dict[str, Any]] = field(default_factory=list)


def invoke(
    ctx: ToolContext,
    registry: ToolRegistry,
    nome: str,
    argumentos: dict[str, Any] | None = None,
) -> ToolCallResult:
    """Executa ``nome`` com ``argumentos`` sob todas as garantias da camada."""
    args = dict(argumentos or {})
    registro = _Registro(ferramenta=nome, origem=ctx.origem, argumentos=_sanear(args))
    inicio = time.perf_counter()
    try:
        resultado = _executar(ctx, registry, nome, args, registro)
    except Exception as exc:
        registro.duracao_ms = int((time.perf_counter() - inicio) * 1000)
        _preencher_erro(registro, exc)
        # A auditoria não pode mascarar o erro que ela está auditando.
        _auditar(ctx, registro, tolerar_falha=True)
        raise
    registro.duracao_ms = int((time.perf_counter() - inicio) * 1000)
    registro.status = STATUS_OK
    registro.linhas = resultado.linhas
    registro.source_refs = fontes_do_payload(resultado.payload)
    # No caminho de sucesso a auditoria é condição de entrega: um número fundamentado que
    # ninguém consegue rastrear depois vale menos que um erro honesto.
    call_id = _auditar(ctx, registro, tolerar_falha=False)
    return ToolCallResult(
        ferramenta=resultado.ferramenta,
        saida=resultado.saida,
        payload=resultado.payload,
        duracao_ms=registro.duracao_ms,
        linhas=resultado.linhas,
        as_of=registro.as_of,
        ente=registro.cod_ibge,
        call_id=call_id,
    )


def _executar(
    ctx: ToolContext,
    registry: ToolRegistry,
    nome: str,
    args: dict[str, Any],
    registro: _Registro,
) -> ToolCallResult:
    if ctx.principal.org_id is None:
        raise AppError(
            status=403,
            title="Sem organização",
            detail="Chamada de ferramenta exige uma organização ativa.",
        )
    tool = registry.get(nome)
    if tool is None:
        raise FerramentaDesconhecidaError(nome, registry.nomes())
    if not ctx.principal.has_cap(tool.capacidade):
        raise AppError(
            status=403,
            title="Sem permissão",
            detail=f"A ferramenta '{nome}' requer a capacidade '{tool.capacidade}'.",
            type_="urn:plataforma-fiscal:error:missing-capability",
        )

    try:
        entrada = tool.entrada.model_validate(args)
    except ValidationError as exc:
        raise EntradaInvalidaError(
            nome, [dict(erro) for erro in exc.errors(include_url=False)]
        ) from exc

    if isinstance(entrada, EnteToolInput):
        registro.cod_ibge = entrada.ente
        registro.as_of = entrada.as_of
        # A garantia mora aqui, e não na borda que chamou (A22/E1). ScopeForbiddenError e
        # EnteNaoLicenciadoError sobem com `type` distinto — a distinção é o produto.
        assert_ente_in_scope(ctx.session, ctx.principal, entrada.ente)
    elif tool.recebe_ente:  # pragma: no cover - o registro impede este estado
        raise SaidaInvalidaError(nome, "declara receber ente sem contrato de escopo")

    saida = tool.handler(ctx, entrada)
    return _validar_saida(tool, saida)


def _validar_saida(tool: Tool, saida: ToolOutput) -> ToolCallResult:
    """Revalida a saída contra o contrato e aplica a guarda de rastreabilidade (G4)."""
    try:
        validada = tool.saida.model_validate(saida, from_attributes=True)
    except ValidationError as exc:
        raise SaidaInvalidaError(tool.nome, str(exc)) from exc

    # ``mode='python'`` de propósito: em JSON o Pydantic serializa Decimal como string, e
    # a guarda deixaria de enxergar justamente os números fiscais que existe para vigiar.
    faltantes = numeros_sem_fonte(validada.model_dump())
    if faltantes:
        raise FonteAusenteError(tool.nome, faltantes)

    return ToolCallResult(
        ferramenta=tool.nome,
        saida=validada,
        payload=validada.model_dump(mode="json"),
        duracao_ms=0,
        linhas=validada.linhas(),
    )


def _preencher_erro(registro: _Registro, exc: Exception) -> None:
    if isinstance(exc, AppError):
        registro.erro_tipo = exc.type
        registro.erro_detalhe = exc.detail or exc.title
        registro.http_status = exc.status
    else:
        registro.erro_tipo = f"python:{exc.__class__.__name__}"
        registro.erro_detalhe = str(exc)[:500]
        registro.http_status = 500


def _sanear(args: dict[str, Any]) -> dict[str, Any]:
    """Argumentos prontos para JSONB — o que veio do modelo pode não ser serializável."""
    saneado: dict[str, Any] = {}
    for chave, valor in args.items():
        if isinstance(valor, str | int | float | bool) or valor is None:
            saneado[str(chave)] = valor
        else:
            saneado[str(chave)] = str(valor)
    return saneado


def fontes_do_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrai os ``source_ref`` do payload — o lastro do que a ferramenta devolveu.

    Pública desde a Sprint IA-5: as telas precisam exibir a procedência de cada número que
    a resposta usou, e reimplementar a varredura na camada de apresentação faria a tela
    mostrar um conjunto de fontes diferente do que a auditoria gravou. Uma varredura só.
    """
    encontrados: list[dict[str, Any]] = []

    def _andar(valor: Any) -> None:
        if isinstance(valor, dict):
            ref = valor.get("source_ref")
            if isinstance(ref, dict) and ref.get("relatorio"):
                encontrados.append(ref)
            for chave, filho in valor.items():
                if chave != "source_ref":
                    _andar(filho)
        elif isinstance(valor, list):
            for item in valor:
                _andar(item)

    _andar(payload)
    # Sem duplicatas, preservando a ordem de aparição.
    vistos: list[dict[str, Any]] = []
    chaves: set[tuple] = set()
    for ref in encontrados:
        chave = tuple(sorted((k, str(v)) for k, v in ref.items()))
        if chave not in chaves:
            chaves.add(chave)
            vistos.append(ref)
    return vistos


def _auditar(ctx: ToolContext, registro: _Registro, *, tolerar_falha: bool) -> uuid.UUID | None:
    """Grava ``op.ia_tool_call`` + trilha geral, em transação própria (ver docstring)."""
    org_id = ctx.principal.org_id
    if org_id is None:
        return None
    try:
        with tenant_session(org_id, user_id=ctx.principal.usuario_id) as sessao:
            linha = assistant_repo.insert_ia_tool_call(
                sessao,
                org_id=org_id,
                usuario_id=ctx.principal.usuario_id,
                conversa_id=ctx.conversa_id,
                ferramenta=registro.ferramenta,
                origem=registro.origem,
                cod_ibge=registro.cod_ibge,
                argumentos=registro.argumentos,
                as_of=registro.as_of,
                status=registro.status,
                erro_tipo=registro.erro_tipo,
                erro_detalhe=registro.erro_detalhe,
                http_status=registro.http_status,
                linhas=registro.linhas,
                duracao_ms=registro.duracao_ms,
                source_refs=registro.source_refs,
            )
            tenancy_repo.insert_audit_log(
                sessao,
                org_id=org_id,
                usuario_id=ctx.principal.usuario_id,
                acao=ACAO_AUDITORIA,
                recurso=(
                    f"tool:{registro.ferramenta};origem={registro.origem};"
                    + (f"por={ctx.origem_ref};" if ctx.origem_ref else "")
                    + f"ente={registro.cod_ibge or '-'};status={registro.status}"
                ),
            )
            return linha.id
    except Exception:
        if not tolerar_falha:
            raise
        logger.exception(
            "Falha ao auditar a chamada de ferramenta %s (status=%s)",
            registro.ferramenta,
            registro.status,
        )
        return None
