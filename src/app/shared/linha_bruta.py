"""O fundo do drill: as linhas do relatório **como o ente as entregou**.

Todo número da plataforma nasce de linhas de um relatório do SICONFI — anexo × conta ×
coluna × valor. Os marts agregam essas linhas em nós navegáveis; este módulo faz o
caminho de volta, e é o último degrau: abaixo dele só existe o JSON bruto no bronze, que
a página de procedência já mostra como chegar.

Serve receita e despesa com o mesmo contrato porque a pergunta é a mesma — *de quais
linhas este número saiu?* —, e responder de duas formas diferentes obrigaria a tela a
saber de qual módulo veio o dado.

## Por que a linha crua acrescenta informação

Não é repetição do agregado. O mart guarda as medidas que o domínio usa; a entrega traz
colunas que ele descarta. Na receita de Fortaleza, o nó ``ReceitasCorrentes`` tem três
medidas no ``fato_receita`` e **sete** colunas no RREO: além de previsão inicial,
atualizada e arrecadado acumulado, a entrega publica "No Bimestre (b)", "% (b/a)",
"% (c/a)" e "SALDO (a-c)". O drill mostra o que o modelo não guardou.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.shared.source_ref import SourceRef


class LinhaRelatorio(BaseModel):
    """Uma linha do relatório, exatamente como veio na entrega."""

    anexo: str | None = None
    #: Descrição publicada pelo ente (o texto que aparece no demonstrativo).
    conta: str | None = None
    #: Identificador estável do STN. É por ele que a receita liga nó↔linha.
    cod_conta: str | None = None
    coluna: str | None = None
    valor: Decimal | None = None
    #: Ordem no payload. Preserva a sequência do demonstrativo, que carrega a hierarquia:
    #: no Anexo 02 a subfunção vem logo depois da função a que pertence.
    linha_seq: int | None = None
    #: A medida do mart que esta coluna alimentou, quando alimentou alguma. ``None``
    #: significa que a entrega publica a coluna e o modelo não a guarda — é justamente
    #: o que o drill acrescenta.
    medida: str | None = None


class LinhaBrutaResponse(BaseModel):
    """Fundo do drill de um nó: suas linhas de origem e como elas viram o agregado."""

    cod_ibge: str
    periodo: str
    as_of: datetime | None = None
    #: Código do nó no mart (origem da receita, função/subfunção ou natureza).
    codigo: str
    descricao: str | None = None
    #: Medidas do nó no mart — o número que a tela mostrava antes do drill.
    medidas: dict[str, Decimal | None] = Field(default_factory=dict)
    linhas: list[LinhaRelatorio] = Field(default_factory=list)
    #: Quando o nó agrega mais de uma linha, o total conferido. Serve de contraprova:
    #: se não bater com ``medidas``, há divergência entre mart e entrega.
    conferencia: dict[str, Decimal] = Field(default_factory=dict)
    observacao: str | None = None
    source_ref: SourceRef
