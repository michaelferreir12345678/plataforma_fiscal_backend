"""O último degrau do drill: as linhas do relatório como o ente as entregou.

O que um drill de auditoria precisa garantir, e o que este arquivo fixa:

1. **Reconciliar.** A soma das colunas que alimentam uma medida tem de ser exatamente a
   medida do mart. Sem isso o drill não é prova — é uma segunda opinião.
2. **Acrescentar.** Se as linhas repetissem o agregado, o clique seria cerimônia. A
   entrega publica colunas que o modelo descarta (percentuais, saldo, valor do bimestre).
3. **Achar a linha certa.** Na despesa o vínculo nó↔linha não é dedutível do código: a
   função é derivada do texto já limpo e a mesma descrição se repete sob funções
   diferentes. 31 dos 105 nós de Fortaleza não fechavam por descrição — por isso o
   vínculo é gravado na materialização (``fato_despesa.linha_origem``).
"""

from __future__ import annotations

import pytest

from app.core.db import admin_session
from app.core.errors import AppError
from app.modules.expense import service as despesa
from app.modules.revenue import service as receita

ENTE = "2304400"
PERIODO = "2025-B6"


def test_receita_reconcilia_com_o_mart() -> None:
    """Cada medida do mart é a soma exata das colunas que a alimentaram."""
    with admin_session() as s:
        r = receita.build_linha_bruta(s, ENTE, PERIODO, "ReceitasCorrentes")
    assert r.linhas, "sem linhas o drill não prova nada"
    for medida, somado in r.conferencia.items():
        assert r.medidas[medida] == somado, f"{medida}: mart {r.medidas[medida]} × entrega {somado}"


def test_receita_mostra_o_que_o_mart_descarta() -> None:
    """Se só repetisse o agregado, o clique seria cerimônia."""
    with admin_session() as s:
        r = receita.build_linha_bruta(s, ENTE, PERIODO, "ReceitasCorrentes")
    sem_medida = [linha for linha in r.linhas if linha.medida is None]
    assert sem_medida, "a entrega publica colunas que o modelo não guarda; elas têm de aparecer"
    colunas = {linha.coluna for linha in sem_medida}
    assert any("SALDO" in (c or "").upper() for c in colunas)


def test_despesa_acha_a_linha_pelo_vinculo_gravado_e_nao_pela_descricao() -> None:
    """Função 10 (Saúde): o código é derivado, e só o vínculo gravado leva à linha certa."""
    with admin_session() as s:
        d = despesa.build_linha_bruta(s, ENTE, PERIODO, "funcao", "10")
    assert d.descricao == "Saúde"
    assert d.linhas, "o nó tem de alcançar suas linhas de origem"
    for medida, somado in d.conferencia.items():
        assert d.medidas[medida] == somado, f"{medida}: mart {d.medidas[medida]} × entrega {somado}"


def test_despesa_expoe_a_secao_intra_do_eixo_funcao() -> None:
    """O total da função soma a seção principal **e** a intra-orçamentária.

    Não é erro: o RREO publica as duas e o consolidado é I + III. Mas o gestor precisa ver
    a separação, senão encontra a mesma coluna duas vezes e conclui que há duplicidade.
    ``cod_conta`` é o que as distingue.
    """
    with admin_session() as s:
        d = despesa.build_linha_bruta(s, ENTE, PERIODO, "funcao", "10")
    contas = {linha.cod_conta for linha in d.linhas}
    assert any(c and "Intra" in c for c in contas), "a seção intra tem de estar visível"
    assert any(c and "Intra" not in c for c in contas), "e a principal também"


def test_natureza_tambem_reconcilia() -> None:
    """O outro eixo da despesa vem do Anexo 01 e liga pelo slug do STN."""
    with admin_session() as s:
        d = despesa.build_linha_bruta(s, ENTE, PERIODO, "natureza", "PessoalEEncargosSociais")
    assert d.linhas
    for medida, somado in d.conferencia.items():
        assert d.medidas[medida] == somado


def test_eixo_invalido_e_recusado_com_422_e_nao_404() -> None:
    """Eixo inexistente é erro de quem chamou, não ausência de dado — e a distinção importa
    para o front: 404 leva a "sem dado para o período", que mandaria procurar no lugar errado."""
    with admin_session() as s, pytest.raises(AppError) as exc:
        despesa.build_linha_bruta(s, ENTE, PERIODO, "orgao", "10")
    assert exc.value.status == 422


def test_no_inexistente_diz_que_pode_estar_em_outro_periodo() -> None:
    with admin_session() as s, pytest.raises(AppError) as exc:
        receita.build_linha_bruta(s, ENTE, PERIODO, "OrigemQueNaoExiste")
    assert exc.value.status == 404
    assert "outro período" in str(exc.value.detail)


def test_source_ref_aponta_o_anexo_certo_de_cada_eixo() -> None:
    """Função vem do Anexo 02, natureza do 01. Rastrear ao anexo errado invalidaria a prova."""
    with admin_session() as s:
        f = despesa.build_linha_bruta(s, ENTE, PERIODO, "funcao", "10")
        n = despesa.build_linha_bruta(s, ENTE, PERIODO, "natureza", "PessoalEEncargosSociais")
    assert f.source_ref.anexo != n.source_ref.anexo
    assert f.source_ref.relatorio == "RREO"
