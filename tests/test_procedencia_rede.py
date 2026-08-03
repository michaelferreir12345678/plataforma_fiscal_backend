"""Os exemplos prometidos ao usuário abrem e devolvem dado? (teste de rede, opt-in)

A página de procedência oferece uma URL clicável por endpoint e diz que ela devolve o
**mesmo** dado que ingerimos. Se um desses endereços 404, ou devolve lista vazia, a
promessa vira armadilha: o gestor clica para conferir, não vê nada e conclui que a
plataforma inventou o número — o oposto exato do que a página existe para fazer.

Sai da suíte padrão porque depende da internet e de servidores de terceiros: falha aqui
não é regressão nossa e não pode bloquear um merge. Roda sob demanda, que é quando
importa — ao mexer na procedência, e periodicamente para detectar API que mudou de
endereço:

    PROCEDENCIA_REDE=1 pytest tests/test_procedencia_rede.py -q
"""

from __future__ import annotations

import os

import httpx
import pytest

from app.modules.ingestion.connectors.procedencia import PROCEDENCIA

pytestmark = pytest.mark.skipif(
    not os.environ.get("PROCEDENCIA_REDE"),
    reason="teste de rede: defina PROCEDENCIA_REDE=1 para rodar",
)

#: Marcas de resposta bem-sucedida **e vazia**: o endereço existe, mas o recorte escolhido
#: para o exemplo não seleciona registro nenhum — e é justamente o exemplo que precisa ter.
#: Checar tamanho não serve: o PIB per capita de um município cabe em 94 bytes e é dado
#: legítimo, enquanto uma lista vazia do ORDS ocupa ~500.
_VAZIOS = ('"items":[]', '"registros":[]', '"value":[]', '"res":[]')

CASOS = [
    (fonte, indice, endpoint.exemplo)
    for fonte, p in sorted(PROCEDENCIA.items())
    for indice, endpoint in enumerate(p.endpoints)
    if endpoint.exemplo
]


@pytest.mark.parametrize(("fonte", "indice", "url"), CASOS, ids=[f"{f}[{i}]" for f, i, _ in CASOS])
def test_exemplo_devolve_dado(fonte: str, indice: int, url: str) -> None:
    resposta = httpx.get(url, timeout=60, follow_redirects=True)
    assert resposta.status_code == 200, f"{fonte}[{indice}]: {resposta.status_code} em {url}"
    corpo = resposta.text.replace(" ", "")
    assert corpo.strip(), f"{fonte}[{indice}]: corpo vazio"
    vazio = next((m for m in _VAZIOS if m in corpo), None)
    assert vazio is None, (
        f"{fonte}[{indice}]: endpoint respondeu 200 com {vazio} — o exemplo não seleciona "
        f"nenhum registro e não serve de prova para o usuário. URL: {url}"
    )
