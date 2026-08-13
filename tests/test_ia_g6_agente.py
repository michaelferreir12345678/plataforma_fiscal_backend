"""Sprint IA-3 — G6 (verificação de saída) e o laço de agente com teto.

Dois guardrails que só valem se forem **verificação**, não instrução de prompt:

- **G6**: todo número citado na prosa é casado com o que as ferramentas e o contexto
  daquela conversa devolveram. Número sem lastro é sinalizado — o teste central forja um
  valor plausível na resposta de um provedor falso e exige que ele apareça sinalizado.
- **Laço de agente**: teto de passos e orçamento de tokens. O estouro degrada para
  resposta **parcial declarada**, composta só do que as ferramentas já devolveram, e nunca
  para resposta inventada nem para um erro que jogue fora o trabalho já pago.

O provedor falso é o que torna isso testável sem rede: o adaptador Gemini fornece apenas o
motor de um turno (``llm._MotorGemini``), e a política de parada — que é o que precisa ser
verificado — mora em ``assistant/agente.py``, código de domínio.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.assistant import agente
from app.modules.assistant.agente import (
    ChamadaPedida,
    OrcamentoAgente,
    ResultadoLaco,
    Turno,
    executar_laco,
)
from app.modules.assistant.llm import LLMProviderError
from app.shared.tooling import verificacao

FORTALEZA = "2304400"


# --------------------------------------------------------------------------- #
# 1. G6 — a régua de números
# --------------------------------------------------------------------------- #
def test_reconhece_numero_fiscal_e_ignora_ano_e_artigo() -> None:
    """A regra da B3: inteiro solto não é número fiscal.

    Sem essa exclusão, "art. 20" e "2024" seriam citações órfãs em toda resposta, e o
    sinal viraria ruído até ninguém mais olhar para ele.
    """
    citados = verificacao.numeros_citados(
        "Conforme o art. 20 da LRF, em 2024 o gasto foi de 54,32% da RCL, "
        "equivalente a R$ 1.234.567,89."
    )
    tokens = [c.texto for c in citados]
    assert "54,32%" in tokens
    assert "1.234.567,89" in tokens
    assert "20" not in tokens and "2024" not in tokens


def test_casamento_tolerante_aceita_arredondamento_do_modelo() -> None:
    """O modelo parafraseia: escreve 54,3 onde o mart tem 54,32. Isso tem lastro."""
    laudo = verificacao.verificar("O índice ficou em 54,3% da RCL.", {"valor_pct": "54.32"})
    assert laudo.ok, laudo.tokens_sem_lastro()


def test_casamento_tolerante_recusa_numero_de_magnitude_parecida() -> None:
    """Tolerância é meio dígito na última casa escrita — não "parecido o suficiente"."""
    laudo = verificacao.verificar("O índice ficou em 54,9% da RCL.", {"valor_pct": "54.32"})
    assert not laudo.ok
    assert laudo.tokens_sem_lastro() == ["54,9%"]


def test_separador_de_milhar_suprimido_continua_casando() -> None:
    laudo = verificacao.verificar(
        "A RCL foi de R$ 1.234.567,89.", {"valor_rs": Decimal("1234567.89")}
    )
    assert laudo.ok


def test_numero_forjado_na_prosa_e_sinalizado() -> None:
    """O caso que a ficha pede: valor que nenhuma ferramenta devolveu."""
    payload = {
        "indicador": "garantias",
        "valor_pct": "1.20",
        "source_ref": {"relatorio": "RGF", "anexo": "Anexo 3", "periodo": "2024-Q3"},
    }
    laudo = verificacao.verificar(
        "As garantias somam 1,20% da RCL e a dívida consolidada chega a 87,45%.", payload
    )
    assert not laudo.ok
    assert laudo.tokens_sem_lastro() == ["87,45%"]
    assert laudo.com_lastro == 1
    assert laudo.total_citados == 2
    assert laudo.status == "sinalizado"


def test_o_texto_da_norma_e_lastro_legitimo() -> None:
    """O teto de 54% vem do texto do dispositivo, não de um payload de ferramenta.

    Restringir lastro a payload reprovaria toda citação de limite legal — o guardrail
    existe para pegar número inventado, não para proibir repetir a norma recebida.
    """
    laudo = verificacao.verificar(
        "O limite do Executivo municipal é de 54% da RCL.",
        {},
        ["Art. 20. A despesa total com pessoal não poderá exceder 54% para o Executivo."],
    )
    assert laudo.ok


def test_aviso_declara_os_numeros_sem_lastro() -> None:
    """"Sinalizado, não publicado em silêncio": o aviso nomeia os valores."""
    laudo = verificacao.verificar("O resultado foi de 99,99%.", {})
    aviso = laudo.aviso()
    assert aviso is not None
    assert "99,99%" in aviso
    assert "G6" in aviso
    texto = verificacao.anexar_aviso("O resultado foi de 99,99%.", laudo)
    assert texto.startswith("O resultado foi de 99,99%.")
    assert "99,99%" in texto


def test_resposta_sem_numero_passa_trivialmente() -> None:
    """Recusa honesta e explicação normativa não citam valor apurado."""
    laudo = verificacao.verificar("Não localizei indicadores materializados.", {})
    assert laudo.ok
    assert laudo.total_citados == 0
    assert laudo.aviso() is None
    assert verificacao.anexar_aviso("texto", laudo) == "texto"


def test_o_lastro_nao_vem_do_proprio_texto_do_modelo() -> None:
    """Conferir o modelo contra ele mesmo aprovaria qualquer coisa."""
    texto = "O gasto com pessoal é de 77,77% da RCL."
    assert not verificacao.verificar(texto, {}).ok
    # O texto passado como fonte é o que a plataforma entregou — nunca a saída do modelo.
    assert verificacao.verificar(texto, [texto]).ok


# --------------------------------------------------------------------------- #
# 2. Laço de agente
# --------------------------------------------------------------------------- #
class MotorFalso:
    """Motor roteirizado: devolve os turnos na ordem, sem SDK e sem rede."""

    def __init__(self, turnos: list[Turno], modelo: str = "modelo-falso") -> None:
        self.modelo = modelo
        self._turnos = list(turnos)
        self.recebidos: list[object] = []

    def gerar(self, respostas):  # type: ignore[no-untyped-def]
        self.recebidos.append(respostas)
        if not self._turnos:
            return Turno(texto="fim")
        return self._turnos.pop(0)


PAYLOAD_GARANTIAS = {
    "indicador": "garantias",
    "rotulo": "Garantias concedidas",
    "valor_formatado": "1,20%",
    "periodo": "2024-B6",
    "disponivel": True,
    "source_ref": {
        "relatorio": "RGF", "anexo": "Anexo 3", "periodo": "2024-Q3", "versao_entrega": "v1"
    },
}


def executor_fixo(nome: str, argumentos: dict) -> dict:
    return dict(PAYLOAD_GARANTIAS)


def test_laco_devolve_o_texto_quando_o_modelo_redige() -> None:
    motor = MotorFalso(
        [
            Turno(chamadas=(ChamadaPedida("indicador_do_ente", {"ente": FORTALEZA}),),
                  tokens_entrada=100, tokens_saida=20),
            Turno(texto="As garantias somam 1,20% da RCL.", tokens_entrada=150,
                  tokens_saida=30),
        ]
    )
    resultado = executar_laco(motor, executor_fixo)
    assert resultado.texto == "As garantias somam 1,20% da RCL."
    assert resultado.parcial is False
    assert resultado.passos == 2
    assert resultado.tokens_entrada == 250 and resultado.tokens_saida == 50
    assert resultado.payloads == [PAYLOAD_GARANTIAS]
    # A segunda volta recebeu o resultado da ferramenta pedida na primeira.
    assert motor.recebidos[0] is None
    assert motor.recebidos[1] == [("indicador_do_ente", PAYLOAD_GARANTIAS)]


def test_teto_de_passos_degrada_para_parcial_declarada() -> None:
    """Um modelo que só pede ferramenta não derruba a resposta — ele a encurta.

    Antes da IA-3 isto era ``LLMProviderError``: o gestor recebia erro depois de a
    plataforma ter consultado (e pago) indicadores. Agora o trabalho feito é entregue,
    declarado como parcial.
    """
    pedido = Turno(chamadas=(ChamadaPedida("indicador_do_ente", {"ente": FORTALEZA}),))
    motor = MotorFalso([pedido, pedido, pedido, pedido, pedido])
    resultado = executar_laco(motor, executor_fixo, orcamento=OrcamentoAgente(max_passos=3))
    assert resultado.parcial is True
    assert resultado.motivo_parcial == agente.MOTIVO_PASSOS
    assert resultado.passos == 3
    assert "Resposta parcial" in resultado.texto
    assert "3 passos" in resultado.texto


def test_a_parcial_so_contem_numero_que_veio_de_ferramenta() -> None:
    """A composição é extrativa: o G6 aprova a parcial por construção."""
    pedido = Turno(chamadas=(ChamadaPedida("indicador_do_ente", {"ente": FORTALEZA}),))
    motor = MotorFalso([pedido, pedido])
    resultado = executar_laco(motor, executor_fixo, orcamento=OrcamentoAgente(max_passos=2))
    assert "1,20%" in resultado.texto
    assert "RGF" in resultado.texto, "a parcial tem de citar a fonte de cada número"
    laudo = verificacao.verificar(resultado.texto, resultado.payloads)
    assert laudo.ok, laudo.tokens_sem_lastro()


def test_orcamento_de_tokens_tambem_interrompe() -> None:
    """Teto de passos não limita custo: uma volta com dez séries custa mais que quatro curtas."""
    pedido = Turno(
        chamadas=(ChamadaPedida("indicador_do_ente", {"ente": FORTALEZA}),),
        tokens_entrada=900,
        tokens_saida=200,
    )
    motor = MotorFalso([pedido, pedido, pedido, pedido])
    resultado = executar_laco(
        motor, executor_fixo, orcamento=OrcamentoAgente(max_passos=10, max_tokens=1000)
    )
    assert resultado.parcial is True
    assert resultado.motivo_parcial == agente.MOTIVO_TOKENS
    assert resultado.passos == 1, "parou na primeira volta, ao estourar o orçamento"
    assert "1000 tokens" in resultado.texto


def test_parcial_sem_nenhum_dado_nao_inventa_numero() -> None:
    """Ferramenta que só devolveu recusa ⇒ parcial sem número, e dizendo isso."""
    pedido = Turno(chamadas=(ChamadaPedida("indicador_do_ente", {"ente": FORTALEZA}),))
    motor = MotorFalso([pedido, pedido])

    def executor_recusa(nome: str, argumentos: dict) -> dict:
        return {"erro": "scope-forbidden", "status": 403, "titulo": "Ente fora do escopo"}

    resultado = executar_laco(
        motor, executor_recusa, orcamento=OrcamentoAgente(max_passos=2)
    )
    assert resultado.parcial is True
    assert verificacao.verificar(resultado.texto, resultado.payloads).ok


def test_indicador_indisponivel_aparece_como_ausencia_na_parcial() -> None:
    """Ausência declarada continua sendo ausência — não vira zero nem some."""
    pedido = Turno(chamadas=(ChamadaPedida("indicador_do_ente", {"ente": FORTALEZA}),))
    motor = MotorFalso([pedido, pedido])

    def executor_ausente(nome: str, argumentos: dict) -> dict:
        return {
            "indicador": "garantias",
            "rotulo": "Garantias concedidas",
            "disponivel": False,
            "observacao": "não está materializado para 2024-B6",
        }

    resultado = executar_laco(
        motor, executor_ausente, orcamento=OrcamentoAgente(max_passos=2)
    )
    assert "dado não apurado" in resultado.texto
    assert "não está materializado" in resultado.texto


def test_resposta_vazia_do_provedor_continua_sendo_erro_de_provedor() -> None:
    """Degradação é para estouro de limite. Provedor mudo continua sendo §9: erro claro."""
    motor = MotorFalso([Turno(texto=None, motivo_vazio="O modelo X retornou vazio.")])
    with pytest.raises(LLMProviderError) as exc:
        executar_laco(motor, executor_fixo)
    assert "retornou vazio" in str(exc.value.detail)


def test_orcamento_exige_ao_menos_um_passo() -> None:
    with pytest.raises(ValueError):
        OrcamentoAgente(max_passos=0)


def test_resultado_do_laco_vira_llm_result_preservando_telemetria() -> None:
    """``op.conversa_uso`` continua recebendo tokens reais — a cota depende disso."""
    resultado = ResultadoLaco(
        texto="ok", modelo="m", tokens_entrada=11, tokens_saida=22
    )
    convertido = resultado.para_llm_result()
    assert convertido.tokens_entrada == 11 and convertido.tokens_saida == 22
    assert convertido.modelo == "m"
