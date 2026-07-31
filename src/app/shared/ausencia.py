"""Ausência de dado com **saída** — o 404 que ensina em vez de só recusar.

Um relatório fiscal ausente quase nunca é falha da plataforma: é cadência de publicação.
O RREO sai a cada bimestre, ~60 dias depois do fecho; o RGF a cada quadrimestre (semestre
se o município tem menos de 50 mil habitantes); a DCA e a CAPAG, uma vez por ano. Pedir
o 6º bimestre em março é pedir algo que ninguém publicou ainda.

Dizer só "sem dado" transfere ao gestor o trabalho de descobrir isso — e a tela, sem mais
informação, oferece "tentar de novo", que é o pior conselho possível: repetir a consulta
não faz o Tesouro publicar. Este módulo monta o 404 com dois acréscimos:

* **explicação** — por que o dado não está lá, na cadência do relatório;
* **campos de extensão** (RFC 7807 §3.2) — ``periodo_sugerido``/``rotulo_sugerido``, o
  último período que **tem** o relatório, para a tela virar um botão que navega até lá.

Os campos são de extensão, e não texto embutido na frase, para que o front aja sobre o
erro sem interpretar redação — uma vírgula a mais na mensagem não pode quebrar o botão.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.ingestion.models import DimEntrega
from app.shared import periodo as periodo_util

# Cadência de cada relatório, para explicar a ausência sem inventar prazo.
CADENCIA: dict[str, str] = {
    "RREO": (
        "O RREO é bimestral e o ente tem até 30 dias após o fim do bimestre para publicá-lo "
        "(LRF, art. 52) — o bimestre em curso ou recém-encerrado normalmente ainda não existe."
    ),
    "RGF": (
        "O RGF é quadrimestral (semestral para municípios com menos de 50 mil habitantes, "
        "LRF, art. 63) e é publicado após o fim do período — o quadrimestre em curso ainda não "
        "foi entregue."
    ),
    "DCA": (
        "A DCA é anual e entregue até 30 de abril do exercício seguinte (Portaria STN) — o "
        "exercício em curso só aparece no ano que vem."
    ),
    "MSC": (
        "A MSC é mensal e depende de o ente transmitir a matriz do mês; meses recentes podem "
        "ainda não ter sido enviados."
    ),
    "CAPAG": (
        "A CAPAG é apurada uma vez por ano pelo Tesouro, sobre as contas do exercício "
        "encerrado — não há nota parcial de exercício em curso."
    ),
    "SADIPEM": (
        "O SADIPEM reflete operações de crédito registradas; sem registro no período, não há "
        "o que exibir."
    ),
}

_ROTULO_PERIODO = {"B": "bimestre", "Q": "quadrimestre", "S": "semestre"}


def _explicacao(relatorio: str) -> str:
    """Frase que explica a cadência do relatório (vazia quando não a conhecemos)."""
    return CADENCIA.get(relatorio.upper().split("-")[0], "")


def ultimo_periodo_com_dado(
    session: Session,
    *,
    cod_ibge: str,
    relatorio: str,
    ate: str | None = None,
) -> str | None:
    """Último período **vigente** do relatório para o ente (o destino do "ir para").

    A ordenação é a cronológica de ``shared.periodo`` e não a lexicográfica do SQL: um
    município que cruzou os 50 mil habitantes publica RGF em quadrimestre e em semestre,
    e ``2024-S1`` > ``2024-Q3`` como texto mas é anterior no calendário. É também a mesma
    ordenação que decide o ``default`` do seletor de período, então o botão leva exatamente
    ao período que o seletor escolheria — não a um vizinho dele.

    ``ate`` limita a alternativa a períodos até o pedido, para o caso em que faz sentido
    "voltar" em vez de "ir ao mais recente".
    """
    periodos = list(
        session.scalars(
            select(DimEntrega.periodo).where(
                DimEntrega.cod_ibge == cod_ibge,
                DimEntrega.relatorio == relatorio,
                DimEntrega.vigente.is_(True),
            )
        )
    )
    if ate is not None:
        limite = periodo_util.ordenar_chave(ate)
        periodos = [p for p in periodos if periodo_util.ordenar_chave(p) <= limite]
    encontrado = periodo_util.mais_recente(periodos)
    # Igual ao pedido não é alternativa: mandaria a tela de volta ao período que falhou.
    return None if encontrado == ate else encontrado


def extras_com_saida(
    session: Session,
    *,
    cod_ibge: str,
    relatorio: str,
    periodo: str | None = None,
) -> dict[str, object]:
    """Campos de extensão do Problem Details: a explicação e o período navegável.

    Devolve dicionário vazio quando não há nada de útil a dizer — não há período com dado
    e o relatório é desconhecido. Melhor omitir a chave do que preencher com ruído: o
    front decide mostrar o botão pela **presença** de ``periodo_sugerido``.
    """
    extras: dict[str, object] = {}
    explicacao = _explicacao(relatorio)
    if explicacao:
        extras["explicacao"] = explicacao

    sugerido = ultimo_periodo_com_dado(session, cod_ibge=cod_ibge, relatorio=relatorio)
    if sugerido is not None and sugerido != periodo:
        extras["periodo_sugerido"] = sugerido
        extras["rotulo_sugerido"] = f"Ir para {rotulo_humano(sugerido)}"
    return extras


def rotulo_humano(periodo: str) -> str:
    """``2025-Q3`` → ``3º quadrimestre de 2025``; ``2024`` → ``2024``.

    O rótulo do botão fala a língua do gestor. ``2025-Q3`` é o identificador canônico e
    serve à navegação, mas quem lê a tela pensa em "3º quadrimestre".
    """
    if "-" not in periodo:
        return periodo
    ano, resto = periodo.split("-", 1)
    nome = _ROTULO_PERIODO.get(resto[:1].upper())
    numero = resto[1:]
    if nome is None or not numero.isdigit():
        return periodo
    return f"{int(numero)}º {nome} de {ano}"


def ausencia_de_entrega(
    session: Session,
    *,
    cod_ibge: str,
    relatorio: str,
    periodo: str,
    title: str,
    detail: str,
) -> AppError:
    """Monta o 404 de relatório ausente já com explicação e saída.

    Devolve o erro em vez de levantá-lo para que o ``raise`` fique no chamador — quem lê
    o serviço vê onde o fluxo termina.
    """
    return AppError(
        status=404,
        title=title,
        detail=detail,
        extras=extras_com_saida(session, cod_ibge=cod_ibge, relatorio=relatorio, periodo=periodo),
    )
