"""Peças comuns às ferramentas do catálogo (Sprint IA-1b).

Três coisas que toda ferramenta de leitura repete e que não podem divergir entre elas:

- **qual é o período quando ninguém disse qual** — a última entrega registrada da âncora,
  resolvida com o mesmo ``as_of`` da pergunta (nunca ``date.today()``, que responderia por
  um período que o ente ainda não publicou);
- **como se declara ausência** — o par ``disponivel=false`` + ``observacao``, que é o que
  permite ao assistente *sinalizar a lacuna* (§9) em vez de ficar sem resposta;
- **de onde vem a fonte de uma linha do mart** — ver ``shared.source_ref.fonte_gravada``.

O módulo existe para que ``catalogo``, ``ferramentas`` e ``consultas`` compartilhem isso
sem que nenhum precise importar o outro.
"""

from __future__ import annotations

from datetime import datetime

from app.modules.ingestion import repository as ingestion_repo
from app.shared.tooling.base import ToolContext

#: Relatório que define o calendário fiscal do ente. É o RREO porque é o de cadência mais
#: curta (bimestral) e o único que praticamente todo ente publica — usar o RGF como âncora
#: faria o "período mais recente" saltar de quatro em quatro meses.
RELATORIO_ANCORA = "RREO"


def periodo_efetivo(
    ctx: ToolContext,
    *,
    ente: str,
    periodo: str | None,
    as_of: datetime | None,
    relatorio: str = RELATORIO_ANCORA,
) -> str | None:
    """Período pedido, ou o da última entrega registrada. ``None`` ⇒ não há entrega.

    O ``as_of`` entra aqui também, e é sutil: numa consulta histórica o "período mais
    recente" é o mais recente **naquele instante**, não o de hoje. Sem isso, uma pergunta
    reproduzindo um relatório de março responderia com o bimestre de agosto.
    """
    if periodo:
        return periodo
    ultima = ingestion_repo.resolve_latest_entrega(
        ctx.session, cod_ibge=ente, relatorio=relatorio, as_of=as_of
    )
    return ultima.periodo if ultima else None


def sem_entrega(ente: str, *, relatorio: str = RELATORIO_ANCORA) -> str:
    """Frase única para "não há o que apurar porque não há entrega"."""
    return (
        f"Não há entrega de {relatorio} registrada para o ente {ente}; sem entrega não há "
        f"período a apurar. A ausência pode ser do ente (não publicou) ou da nossa carga — "
        f"a ferramenta cobertura_do_ente distingue as duas."
    )


def sem_vigente(ente: str, periodo: str, as_of: datetime | None, *, relatorio: str) -> str:
    """Frase única para "existe o período, mas não há versão vigente dele"."""
    return (
        f"Sem {relatorio} vigente para {ente} em {periodo}"
        + (f" no as_of {as_of.isoformat()}" if as_of else "")
        + "."
    )


def texto(valor: object) -> str | None:
    """Converte um valor de memória em texto — nunca em número solto.

    Memória de cálculo viaja como texto de propósito. Um número dentro de um dicionário
    de memória não tem ``source_ref`` próprio e, se saísse como número, a guarda de
    rastreabilidade teria de escolher entre reprovar a resposta inteira ou abrir uma
    exceção larga demais. Como texto ele continua legível para o modelo e para o gestor,
    e deixa de se passar por um valor apurado.
    """
    return None if valor is None else str(valor)
