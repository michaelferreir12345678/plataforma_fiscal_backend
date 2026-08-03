"""A procedência declarada tem de ser a que o conector realmente usa.

Uma página de auditoria que exibe endereços desatualizados é pior do que não existir: dá
ao gestor a sensação de ter conferido a origem quando ele conferiu uma ficção. Como os
endereços aparecem em dois lugares — o conector, que chama, e `procedencia.py`, que conta —
a única defesa contra a divergência é reconciliar os dois automaticamente.

O que se prova aqui:

* toda fonte registrada tem procedência (fonte nova sem origem quebra a suíte);
* o ``path`` estático do conector aparece na URL declarada, **caractere a caractere**;
* a base declarada é a mesma constante que o cliente HTTP usa;
* os exemplos são endereços absolutos e completos — o usuário vai clicar neles.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from app.modules.ingestion.connectors import procedencia as proc
from app.modules.ingestion.connectors.registry import CONNECTOR_REGISTRY, FONTE_META
from app.shared.ingestion import client as http_client

TODAS = sorted(CONNECTOR_REGISTRY)


def test_toda_fonte_registrada_tem_procedencia() -> None:
    """A guarda que importa: conector novo sem origem declarada não passa."""
    faltando = [f for f in TODAS if f not in proc.PROCEDENCIA]
    assert not faltando, f"fontes sem procedência declarada: {faltando}"


def test_procedencia_nao_descreve_fonte_inexistente() -> None:
    sobrando = [f for f in proc.PROCEDENCIA if f not in CONNECTOR_REGISTRY]
    assert not sobrando, f"procedência de fonte que não existe mais: {sobrando}"


@pytest.mark.parametrize("fonte", TODAS)
def test_path_estatico_do_conector_aparece_na_url_declarada(fonte: str) -> None:
    """Quando o conector tem ``path`` fixo, a URL declarada tem de terminar nele.

    É o caso do SICONFI e do SADIPEM. Conectores que montam o caminho em tempo de
    execução (BCB, IBGE, SIOPS, SIOPE, transferências) não têm ``path`` de classe e são
    cobertos pela conferência de base, abaixo.
    """
    path = getattr(CONNECTOR_REGISTRY[fonte], "path", None)
    if not isinstance(path, str) or not path:
        pytest.skip("conector monta o caminho dinamicamente")
    urls = [e.url for e in proc.PROCEDENCIA[fonte].endpoints]
    assert any(u.rstrip("/").endswith(path) for u in urls), (
        f"{fonte}: conector usa path {path!r}, mas a procedência declara {urls}"
    )


@pytest.mark.parametrize("fonte", TODAS)
def test_base_declarada_bate_com_a_do_cliente(fonte: str) -> None:
    """O host da URL declarada tem de ser o de alguma base configurada no cliente HTTP.

    Impede o erro mais provável: alguém troca o domínio da API no cliente e a página de
    auditoria continua apontando para o antigo.
    """
    bases = {
        http_client.SICONFI_BASE_URL,
        http_client.SADIPEM_BASE_URL,
        http_client.BCB_BASE_URL,
        http_client.IBGE_BASE_URL,
        http_client.SIOPS_BASE_URL,
        http_client.SIOPE_BASE_URL,
        http_client.TESOURO_TRANSFERENCIAS_BASE_URL,
        proc.CKAN,  # o CKAN não passa pelo cliente: é lido direto no conector da CAPAG
    }
    hosts_validos = {urlsplit(b).netloc for b in bases}
    for endpoint in proc.PROCEDENCIA[fonte].endpoints:
        host = urlsplit(endpoint.url).netloc
        if not host:
            continue  # URL-modelo (ex.: o recurso do CKAN, descoberto em execução)
        assert host in hosts_validos, (
            f"{fonte}: endpoint aponta para {host}, fora das bases do cliente {hosts_validos}"
        )


@pytest.mark.parametrize("fonte", TODAS)
def test_exemplos_sao_clicaveis(fonte: str) -> None:
    """Exemplo é a prova oferecida ao usuário; meia URL não prova nada."""
    for endpoint in proc.PROCEDENCIA[fonte].endpoints:
        if endpoint.exemplo is None:
            continue
        partes = urlsplit(endpoint.exemplo)
        assert partes.scheme in {"http", "https"}, f"{fonte}: exemplo sem esquema"
        assert partes.netloc, f"{fonte}: exemplo sem host"
        assert "{" not in endpoint.exemplo, (
            f"{fonte}: exemplo com marcador não substituído — não abre no navegador"
        )


@pytest.mark.parametrize("fonte", TODAS)
def test_procedencia_e_explicada_e_nao_so_listada(fonte: str) -> None:
    """O pedido era explicar, não só apontar. Endpoint sem explicação não cumpre isso."""
    p = proc.PROCEDENCIA[fonte]
    assert p.acesso in proc.ACESSO_ROTULO, f"{fonte}: tipo de acesso desconhecido"
    assert len(p.como_funciona) >= 80, f"{fonte}: explicação vazia ou raquítica"
    assert p.endpoints, f"{fonte}: nenhuma chamada declarada"
    for endpoint in p.endpoints:
        assert endpoint.o_que_traz, f"{fonte}: endpoint sem dizer o que traz"
        assert endpoint.formato, f"{fonte}: endpoint sem formato"
        for parametro in endpoint.parametros:
            assert parametro.significado, (
                f"{fonte}: parâmetro {parametro.nome} sem significado — nome cru não audita"
            )


def test_fonte_que_exige_configuracao_diz_isso_na_procedencia() -> None:
    """Quem lê a origem precisa saber que aquela fonte não roda sem parâmetro do operador."""
    for fonte, meta in FONTE_META.items():
        if meta.requer_configuracao and fonte in proc.PROCEDENCIA:
            p = proc.PROCEDENCIA[fonte]
            texto = p.como_funciona + " ".join(
                (e.observacao or "") + " ".join(x.significado for x in e.parametros)
                for e in p.endpoints
            )
            assert "params." in texto or "informado" in texto or "Obrigatório" in texto, (
                f"{fonte} exige configuração, mas a procedência não avisa"
            )
