"""Avaliação e verificação contínua da IA (Sprint IA-6).

Transforma "a IA parece boa" em medição repetível. O módulo não acrescenta nenhuma
regra fiscal e não calcula indicador: ele **executa** o assistente pelo caminho real
(``assistant.service``, com escopo, ferramentas, ``source_ref`` e G6) e confere o que
saiu contra o que o banco tem.

Entradas do pacote::

    from app.modules.evaluation import runner
    resultado = runner.avaliar()          # conjunto dourado + bateria adversária
    print(resultado.metricas.alucinacao_numerica)

O comando único é ``python -m scripts.avaliar_ia``.
"""

from __future__ import annotations

from app.modules.evaluation.cenario import Cenario, cenario_de_avaliacao
from app.modules.evaluation.conjunto import (
    Conjunto,
    PerguntaAdversaria,
    PerguntaDourada,
    carregar_conjunto,
)
from app.modules.evaluation.metricas import Metricas
from app.modules.evaluation.runner import ResultadoAvaliacao, avaliar

__all__ = [
    "Cenario",
    "Conjunto",
    "Metricas",
    "PerguntaAdversaria",
    "PerguntaDourada",
    "ResultadoAvaliacao",
    "avaliar",
    "carregar_conjunto",
    "cenario_de_avaliacao",
]
