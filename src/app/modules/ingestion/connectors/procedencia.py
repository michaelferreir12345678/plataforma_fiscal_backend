"""Procedência de cada fonte: **de onde exatamente** o dado sai antes de virar número na tela.

O catálogo de fontes dizia família, cadência e órgão — nada disso permite conferir. Um
gestor que desconfia de um valor precisa poder chegar ao endereço que consultamos, abrir
com os mesmos parâmetros e ver o mesmo número. Sem isso, "fonte: Tesouro Nacional" é uma
afirmação sobre a qual só resta confiar.

Aqui cada fonte declara o acesso (API REST, OData, catálogo CKAN, arquivo, PDF), o portal
humano equivalente, a documentação, a licença e **cada chamada** que fazemos — com método,
URL, parâmetros explicados um a um e um exemplo real e clicável.

## Por que isto é dado e não docstring

Precisa chegar à tela. Docstring não serializa.

## Por que isto não vira mentira com o tempo

O risco óbvio de reescrever endereços num segundo lugar é a cópia envelhecer enquanto o
conector muda — e aí a página de auditoria passa a mentir com aparência de rigor, o que é
pior do que não existir. `tests/test_procedencia.py` reconcilia esta declaração com os
conectores: o ``path`` estático tem de bater com o do conector, a base tem de bater com a do
cliente HTTP, e **toda** fonte registrada tem de ter procedência. Fonte nova sem procedência
quebra a suíte.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- vocabulário de acesso ---------------------------------------------------------
#: Como se chega ao dado. Muda o que o usuário pode fazer para conferir: uma API REST se
#: abre no navegador; um arquivo se baixa; um PDF exige leitura humana.
ACESSO_ROTULO = {
    "api_rest": "API REST (JSON)",
    "api_odata": "API OData (JSON)",
    "catalogo_ckan": "Catálogo CKAN → arquivo",
    "arquivo": "Arquivo (planilha/CSV)",
    "raspagem_pdf": "PDF publicado no portal do ente",
}

LICENCA_ABERTA = "Dados abertos — Lei de Acesso à Informação (Lei 12.527/2011)"


@dataclass(frozen=True)
class Parametro:
    """Um parâmetro da chamada, explicado. O nome cru não diz nada a quem audita."""

    nome: str
    exemplo: str
    significado: str


@dataclass(frozen=True)
class Endpoint:
    """Uma chamada concreta que a plataforma faz."""

    metodo: str
    url: str
    formato: str
    o_que_traz: str
    parametros: tuple[Parametro, ...] = ()
    #: URL real, com valores reais, que abre no navegador e devolve o mesmo dado que
    #: ingerimos. É a prova: o usuário confere sem depender da nossa palavra.
    exemplo: str | None = None
    observacao: str | None = None


@dataclass(frozen=True)
class Procedencia:
    """Origem completa de uma fonte."""

    acesso: str
    #: Página humana onde a mesma informação pode ser consultada sem API.
    portal: str
    licenca: str = LICENCA_ABERTA
    autenticacao: str = "Não requer — dado público, sem chave nem cadastro."
    documentacao: str | None = None
    #: Como a ingestão funciona nesta fonte, em prosa. Responde "por que são N chamadas",
    #: "por que este parâmetro", "o que a fonte não entrega".
    como_funciona: str = ""
    endpoints: tuple[Endpoint, ...] = field(default_factory=tuple)


# --- bases (as mesmas constantes do cliente HTTP; o teste reconcilia) ----------------
SICONFI = "https://apidatalake.tesouro.gov.br/ords/siconfi/"
SADIPEM = "https://apidatalake.tesouro.gov.br/ords/cdwhprd/sadipem/tt/"
BCB = "https://api.bcb.gov.br/"
IBGE = "https://servicodados.ibge.gov.br/api/"
TRANSFERENCIAS = "https://apiapex.tesouro.gov.br/aria/v1/transferencias_constitucionais/custom/"
SIOPS = "https://siops-consulta-publica-api.saude.gov.br/v1/"
SIOPE = "https://www.fnde.gov.br/olinda-ide/servico/DADOS_ABERTOS_SIOPE/versao/v1/odata/"
CKAN = "https://www.tesourotransparente.gov.br/ckan/api/3/action/"

_PORTAL_SICONFI = "https://siconfi.tesouro.gov.br/siconfi/pages/public/consulta_finbra/finbra_list.jsf"
_DOC_ORDS = "https://apidatalake.tesouro.gov.br/docs/siconfi/"

# Parâmetros que se repetem entre os endpoints do SICONFI.
_P_ENTE = Parametro(
    nome="id_ente",
    exemplo="2304400",
    significado="Código IBGE do ente (7 dígitos para município, 2 para estado).",
)
_P_EXERCICIO = Parametro(
    nome="an_exercicio", exemplo="2025", significado="Exercício financeiro (ano)."
)

_ORDS = (
    "A API do SICONFI é ORDS (Oracle REST Data Services): devolve `{items: [...], hasMore}` "
    "e pagina por `offset`/`limit`. A plataforma percorre todas as páginas — o que a tela "
    "mostra é o conjunto completo, não a primeira página."
)


PROCEDENCIA: dict[str, Procedencia] = {
    # ============================== SICONFI ==============================
    "siconfi_rreo": Procedencia(
        acesso="api_rest",
        portal=_PORTAL_SICONFI,
        documentacao=_DOC_ORDS,
        como_funciona=(
            "Uma chamada por (ente, exercício, bimestre). O RREO vem em linhas de anexo × "
            "conta × coluna, exatamente como o ente entregou ao Tesouro — não há cálculo "
            "nosso nesta etapa: o que entra no bronze é o JSON bruto, com hash, e só depois "
            "é normalizado. " + _ORDS
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{SICONFI}tt/rreo",
                formato="JSON (ORDS paginado)",
                o_que_traz=(
                    "Todas as linhas do RREO do bimestre: receita realizada (Anexo 01), "
                    "despesa empenhada/liquidada/paga (Anexo 02), RCL (Anexo 03), resultados "
                    "(Anexo 06), restos a pagar (Anexo 07) e os mínimos de saúde/educação."
                ),
                parametros=(
                    _P_EXERCICIO,
                    Parametro("nr_periodo", "6", "Bimestre, de 1 a 6."),
                    Parametro(
                        "co_tipo_demonstrativo", "RREO", "Fixo: seleciona o demonstrativo."
                    ),
                    _P_ENTE,
                ),
                exemplo=(
                    f"{SICONFI}tt/rreo?an_exercicio=2025&nr_periodo=6"
                    "&co_tipo_demonstrativo=RREO&id_ente=2304400"
                ),
                observacao=(
                    "Este exemplo é o RREO do 6º bimestre de 2025 de Fortaleza — a mesma "
                    "chamada que originou os números das páginas de receita, despesa e "
                    "resultado. Abrir no navegador devolve o JSON tal como o recebemos."
                ),
            ),
        ),
    ),
    "siconfi_rgf": Procedencia(
        acesso="api_rest",
        portal=_PORTAL_SICONFI,
        documentacao=_DOC_ORDS,
        como_funciona=(
            "O RGF é separado por **poder**, e a API exige `co_poder` em cada chamada: um "
            "ente municipal rende 2 chamadas por período (Executivo e Legislativo) e um "
            "estadual rende 5 (soma-se Judiciário, Ministério Público e Defensoria). "
            "A periodicidade acompanha o porte: quadrimestral em regra, semestral para "
            "municípios com menos de 50 mil habitantes (LRF, art. 63). " + _ORDS
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{SICONFI}tt/rgf",
                formato="JSON (ORDS paginado)",
                o_que_traz=(
                    "Anexos do RGF: despesa com pessoal (Anexo 01), dívida consolidada "
                    "(Anexo 02), garantias (03), operações de crédito (04) e disponibilidade "
                    "de caixa (05)."
                ),
                parametros=(
                    _P_EXERCICIO,
                    Parametro("nr_periodo", "3", "Quadrimestre (1–3) ou semestre (1–2)."),
                    Parametro(
                        "in_periodicidade", "Q", "`Q` quadrimestral, `S` semestral."
                    ),
                    Parametro("co_tipo_demonstrativo", "RGF", "Fixo."),
                    Parametro(
                        "co_poder",
                        "E",
                        "Poder: E=Executivo, L=Legislativo, J=Judiciário, "
                        "M=Ministério Público, D=Defensoria.",
                    ),
                    _P_ENTE,
                ),
                exemplo=(
                    f"{SICONFI}tt/rgf?an_exercicio=2025&nr_periodo=3&in_periodicidade=Q"
                    "&co_tipo_demonstrativo=RGF&co_poder=E&id_ente=2304400"
                ),
                observacao=(
                    "É de onde sai o percentual de pessoal do Executivo e a dívida "
                    "consolidada líquida. Trocando `co_poder=L` aparece o Legislativo."
                ),
            ),
        ),
    ),
    "siconfi_dca": Procedencia(
        acesso="api_rest",
        portal=_PORTAL_SICONFI,
        documentacao=_DOC_ORDS,
        como_funciona=(
            "Uma chamada por (ente, exercício). A DCA é anual e traz os balanços fechados — "
            "é a fonte dos balanços patrimonial, orçamentário e financeiro. " + _ORDS
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{SICONFI}tt/dca",
                formato="JSON (ORDS paginado)",
                o_que_traz="Anexos da Declaração de Contas Anuais: balanços e demonstrativos.",
                parametros=(_P_EXERCICIO, _P_ENTE),
                exemplo=f"{SICONFI}tt/dca?an_exercicio=2024&id_ente=2304400",
            ),
        ),
    ),
    "siconfi_msc": Procedencia(
        acesso="api_rest",
        portal=_PORTAL_SICONFI,
        documentacao=_DOC_ORDS,
        como_funciona=(
            "A MSC é a maior tabela do sistema e a API **não** aceita busca ampla: exige "
            "classe da conta e tipo de valor em cada chamada. Um mês de um ente rende "
            "`classes × tipos_de_valor` requisições, cujos resultados são concatenados. "
            "O `id_tv` não volta no corpo da resposta — é injetado por nós em cada linha, "
            "senão a origem do saldo se perderia. " + _ORDS
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{SICONFI}tt/msc_patrimonial",
                formato="JSON (ORDS paginado)",
                o_que_traz="Saldos por conta PCASP do mês, na matriz consolidada.",
                parametros=(
                    Parametro("an_referencia", "2024", "Exercício de referência."),
                    Parametro("me_referencia", "12", "Mês de referência (1–12)."),
                    Parametro(
                        "co_tipo_matriz", "MSCC", "Fixo: matriz consolidada (MSCC)."
                    ),
                    Parametro(
                        "classe_conta", "1", "Classe PCASP (1 a 4 = patrimonial)."
                    ),
                    Parametro(
                        "id_tv",
                        "ending_balance",
                        "Tipo de valor: `beginning_balance` (saldo inicial), "
                        "`period_change` (movimento do período) ou `ending_balance` "
                        "(saldo final).",
                    ),
                    _P_ENTE,
                ),
                exemplo=(
                    f"{SICONFI}tt/msc_patrimonial?an_referencia=2024&me_referencia=12"
                    "&co_tipo_matriz=MSCC&classe_conta=1&id_tv=ending_balance&id_ente=3550308"
                ),
                observacao=(
                    "O exemplo usa São Paulo porque nem todo ente transmite MSC — Fortaleza "
                    "não transmite. Ausência de MSC não é falha da plataforma."
                ),
            ),
        ),
    ),
    "siconfi_extratos": Procedencia(
        acesso="api_rest",
        portal=_PORTAL_SICONFI,
        documentacao=_DOC_ORDS,
        como_funciona=(
            "É o que permite detectar **retificação**. O extrato lista as entregas do ente "
            "com suas datas de homologação; quando uma entrega já ingerida reaparece com "
            "homologação posterior, abrimos uma nova versão e a anterior deixa de ser "
            "vigente — sem apagar nada, para que um relatório antigo continue reproduzível."
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{SICONFI}tt/extrato_entregas",
                formato="JSON (ORDS paginado)",
                o_que_traz="Entregas declaradas pelo ente no exercício, com data de homologação.",
                parametros=(
                    Parametro("an_referencia", "2025", "Exercício de referência."),
                    _P_ENTE,
                ),
                exemplo=f"{SICONFI}tt/extrato_entregas?an_referencia=2025&id_ente=2304400",
            ),
        ),
    ),
    "siconfi_entes": Procedencia(
        acesso="api_rest",
        portal=_PORTAL_SICONFI,
        documentacao=_DOC_ORDS,
        como_funciona=(
            "Cadastro oficial: nome, UF, esfera, população e CNPJ. É daqui que sai a "
            "**esfera** do ente — e a esfera decide o teto de cada limite da LRF (pessoal "
            "54% para município, 49% para estado). Aplicar o limite errado seria consequência "
            "direta de errar esta fonte."
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{SICONFI}tt/entes",
                formato="JSON (ORDS paginado)",
                o_que_traz="Cadastro do ente: nome, UF, esfera, população, CNPJ.",
                parametros=(_P_ENTE,),
                exemplo=f"{SICONFI}tt/entes?id_ente=2304400",
            ),
        ),
    ),
    "siconfi_rreo_minimos_pdf": Procedencia(
        acesso="raspagem_pdf",
        portal="Portal da transparência do próprio ente",
        documentacao=None,
        como_funciona=(
            "**Último recurso, e por isso desligada por padrão.** Os Anexos 8 (educação) e "
            "12 (saúde) do RREO não vêm pela API do SICONFI para todos os entes; quando "
            "faltam, o número oficial só existe no PDF que o ente publica no próprio portal. "
            "Cada prefeitura tem um endereço e um layout — não há padrão nacional a seguir, "
            "então o template da página precisa ser informado por ente em "
            "`params.page_url_template`. Sem isso a fonte falharia em toda execução, e falha "
            "previsível ensina o operador a ignorar erro."
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url="{page_url_template}",
                formato="HTML → PDF",
                o_que_traz=(
                    "A página do portal do ente que lista os anexos do RREO; dela se extrai "
                    "o link do PDF do anexo, e do PDF as linhas do demonstrativo."
                ),
                parametros=(
                    Parametro(
                        "page_url_template",
                        "https://transparencia.fortaleza.ce.gov.br/.../rreo/{ano}/{bimestre}",
                        "Endereço da página de publicação do ente, com marcadores de ano e "
                        "bimestre. Obrigatório: não há valor padrão que sirva a outro ente.",
                    ),
                ),
                exemplo=None,
                observacao=(
                    "Sem `page_url_template` a fonte não é sequer oferecida no disparo em "
                    "lote — é a única fonte do catálogo nessa condição."
                ),
            ),
        ),
    ),
    # ============================== SADIPEM ==============================
    "sadipem_pvl": Procedencia(
        acesso="api_rest",
        portal="https://www.tesourotransparente.gov.br/temas/estados-e-municipios/sadipem",
        documentacao="https://apidatalake.tesouro.gov.br/docs/sadipem/",
        como_funciona=(
            "Pedidos de Verificação de Limites: cada operação de crédito que o ente quer "
            "contratar passa por análise do Tesouro. Traz o estágio de cada pleito — é o "
            "que antecipa endividamento que ainda não apareceu no RGF."
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{SADIPEM}pvl",
                formato="JSON (ORDS paginado)",
                o_que_traz="Pleitos do ente com status, valor, credor e finalidade.",
                parametros=(_P_ENTE,),
                exemplo=f"{SADIPEM}pvl?id_ente=2304400",
                observacao=(
                    "O SADIPEM não filtra por período: a API devolve a base inteira do ente "
                    "e a data vira a fotografia (entrega) no medallion."
                ),
            ),
        ),
    ),
    "sadipem_op_contratada": Procedencia(
        acesso="api_rest",
        portal="https://www.tesourotransparente.gov.br/temas/estados-e-municipios/sadipem",
        documentacao="https://apidatalake.tesouro.gov.br/docs/sadipem/",
        como_funciona=(
            "Usa o **mesmo** endpoint de PVL e filtra os pleitos efetivamente contratados. "
            "O endpoint legado dedicado a operações não trazia a marcação de contratação, o "
            "que impedia distinguir pedido de contrato — daí a escolha de derivar do PVL."
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{SADIPEM}pvl",
                formato="JSON (ORDS paginado)",
                o_que_traz="Os mesmos pleitos, restritos aos que viraram contrato.",
                parametros=(_P_ENTE,),
                exemplo=f"{SADIPEM}pvl?id_ente=2304400",
            ),
        ),
    ),
    "sadipem_cronograma_pgto": Procedencia(
        acesso="api_rest",
        portal="https://www.tesourotransparente.gov.br/temas/estados-e-municipios/sadipem",
        documentacao="https://apidatalake.tesouro.gov.br/docs/sadipem/",
        como_funciona=(
            "Duas etapas: primeiro os pleitos do ente (`pvl`), depois o cronograma de **cada** "
            "pleito pelo seu identificador. O número de chamadas acompanha a quantidade de "
            "operações do ente — não há endpoint que traga todos de uma vez."
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{SADIPEM}pvl",
                formato="JSON (ORDS paginado)",
                o_que_traz="Os pleitos, para obter os `id_pleito` do passo seguinte.",
                parametros=(_P_ENTE,),
                exemplo=f"{SADIPEM}pvl?id_ente=2304400",
            ),
            Endpoint(
                metodo="GET",
                url=f"{SADIPEM}opc-cronograma-pagamentos",
                formato="JSON (ORDS paginado)",
                o_que_traz="Vencimentos futuros de amortização e juros da operação.",
                parametros=(
                    Parametro(
                        "id_pleito",
                        "64171",
                        "Identificador do pleito, obtido na chamada anterior.",
                    ),
                ),
                exemplo=f"{SADIPEM}opc-cronograma-pagamentos?id_pleito=64171",
                observacao=(
                    "O pleito 64171 é uma operação real de Fortaleza, escolhida para que o "
                    "exemplo devolva cronograma de verdade. Cada ente tem os seus."
                ),
            ),
        ),
    ),
    "sadipem_cdp": Procedencia(
        acesso="api_rest",
        portal="https://www.tesourotransparente.gov.br/temas/estados-e-municipios/sadipem",
        documentacao="https://apidatalake.tesouro.gov.br/docs/sadipem/",
        como_funciona=(
            "Situação do ente no Cadastro da Dívida Pública — a regularidade que condiciona "
            "novas operações de crédito."
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{SADIPEM}res-cdp",
                formato="JSON (ORDS paginado)",
                o_que_traz="Situação cadastral do ente na dívida pública.",
                parametros=(_P_ENTE,),
                exemplo=f"{SADIPEM}res-cdp?id_ente=2304400",
            ),
        ),
    ),
    # ============================== BCB ==============================
    "bcb": Procedencia(
        acesso="api_rest",
        portal="https://www3.bcb.gov.br/sgspub/",
        documentacao="https://dadosabertos.bcb.gov.br/dataset/sgs",
        como_funciona=(
            "Sistema Gerenciador de Séries Temporais do Banco Central. Cada série tem um "
            "código e é buscada separadamente: IPCA (433), Selic diária (11), Selic mensal "
            "(4390), Selic anualizada (4189) e IGP-M (189). O IPCA é o deflator das séries "
            "reais; a Selic entra como variável exógena nas projeções. Nenhuma delas é "
            "dado do ente — são índices nacionais."
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{BCB}dados/serie/bcdata.sgs.{{codigo}}/dados",
                formato="JSON",
                o_que_traz="Série histórica de um índice, ponto a ponto, com data e valor.",
                parametros=(
                    Parametro(
                        "codigo",
                        "433",
                        "Código da série no SGS (vai no caminho, não na query): "
                        "433=IPCA, 11=Selic diária, 4390=Selic mensal, "
                        "4189=Selic anualizada, 189=IGP-M.",
                    ),
                    Parametro("formato", "json", "Fixo: formato da resposta."),
                    Parametro("dataInicial", "01/01/2015", "Início, em dd/mm/aaaa."),
                    Parametro("dataFinal", "31/12/2025", "Fim, em dd/mm/aaaa."),
                ),
                exemplo=(
                    f"{BCB}dados/serie/bcdata.sgs.433/dados"
                    "?formato=json&dataInicial=01/01/2024&dataFinal=31/12/2025"
                ),
                observacao="O exemplo traz o IPCA mensal de 2024–2025.",
            ),
        ),
    ),
    # ============================== IBGE ==============================
    "ibge_populacao": Procedencia(
        acesso="api_rest",
        portal="https://www.ibge.gov.br/estatisticas/sociais/populacao.html",
        documentacao="https://servicodados.ibge.gov.br/api/docs/agregados",
        como_funciona=(
            "Estimativas anuais de população, agregado 6579, variável 9324. É o denominador "
            "de todo indicador **per capita** da plataforma — errar aqui distorce a "
            "comparação entre entes sem distorcer nenhum valor absoluto, o que a torna "
            "difícil de perceber."
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{IBGE}v3/agregados/6579/periodos/{{ano}}/variaveis/9324",
                formato="JSON",
                o_que_traz="População estimada do município no ano.",
                parametros=(
                    Parametro("ano", "2024", "Ano da estimativa (vai no caminho)."),
                    Parametro(
                        "localidades",
                        "N6[2304400]",
                        "Nível N6 = município, entre colchetes o código IBGE.",
                    ),
                ),
                exemplo=(
                    f"{IBGE}v3/agregados/6579/periodos/2024/variaveis/9324"
                    "?localidades=N6[2304400]"
                ),
            ),
        ),
    ),
    "ibge_pib": Procedencia(
        acesso="api_rest",
        portal="https://www.ibge.gov.br/estatisticas/economicas/contas-nacionais.html",
        documentacao="https://servicodados.ibge.gov.br/api/docs/agregados",
        como_funciona=(
            "Duas chamadas com contratos diferentes: o agregado 5938 (variável 37) traz o "
            "PIB a preços correntes **em mil reais**, e a Pesquisa 38 (indicador 47001) traz "
            "o PIB per capita já calculado pelo IBGE. Preferimos o per capita oficial a "
            "dividir PIB por população: a razão de duas fontes com arredondamentos "
            "diferentes produz um terceiro número que não é de ninguém."
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{IBGE}v3/agregados/5938/periodos/{{ano}}/variaveis/37",
                formato="JSON",
                o_que_traz="PIB municipal a preços correntes (unidade oficial: mil reais).",
                parametros=(
                    Parametro("ano", "2021", "Ano de referência do PIB."),
                    Parametro("localidades", "N6[2304400]", "Município, por código IBGE."),
                ),
                exemplo=(
                    f"{IBGE}v3/agregados/5938/periodos/2021/variaveis/37"
                    "?localidades=N6[2304400]"
                ),
            ),
            Endpoint(
                metodo="GET",
                url=f"{IBGE}v1/pesquisas/38/periodos/{{ano}}/indicadores/47001/resultados/{{cod_ibge}}",
                formato="JSON",
                o_que_traz="PIB per capita municipal, calculado pelo próprio IBGE.",
                parametros=(
                    Parametro("ano", "2021", "Ano de referência."),
                    Parametro("cod_ibge", "2304400", "Código IBGE do município."),
                ),
                exemplo=f"{IBGE}v1/pesquisas/38/periodos/2021/indicadores/47001/resultados/2304400",
            ),
        ),
    ),
    "ibge_malha": Procedencia(
        acesso="api_rest",
        portal="https://www.ibge.gov.br/geociencias/organizacao-do-territorio/malhas-territoriais.html",
        documentacao="https://servicodados.ibge.gov.br/api/docs/malhas",
        como_funciona=(
            "Uma chamada por UF devolve uma FeatureCollection GeoJSON com os polígonos "
            "de todos os seus municípios. Estado e códigos municipais são normalizados "
            "para o mesmo prefixo de dois dígitos antes do job, por isso vários anos ou "
            "municípios da mesma UF nunca repetem o download."
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{IBGE}v3/malhas/estados/{{uf}}",
                formato="GeoJSON (FeatureCollection)",
                o_que_traz="Polígonos municipais usados no mapa coroplético da visão estadual.",
                parametros=(
                    Parametro("uf", "21", "Código IBGE da Unidade da Federação."),
                    Parametro(
                        "periodo",
                        "2022",
                        "Fixo: edição territorial persistida e servida pelo mapa.",
                    ),
                    Parametro(
                        "intrarregiao",
                        "municipio",
                        "Fixo: divide a malha estadual em polígonos municipais.",
                    ),
                    Parametro(
                        "formato",
                        "application/vnd.geo+json",
                        "Solicita GeoJSON diretamente à API.",
                    ),
                    Parametro(
                        "qualidade",
                        "minima",
                        "Simplificação adequada à renderização web do mapa.",
                    ),
                ),
                exemplo=(
                    f"{IBGE}v3/malhas/estados/21?periodo=2022&intrarregiao=municipio"
                    "&formato=application%2Fvnd.geo%2Bjson&qualidade=minima"
                ),
            ),
        ),
    ),
    # ====================== Transferências constitucionais ======================
    "tesouro_fpm": Procedencia(
        acesso="api_rest",
        portal="https://www.tesourotransparente.gov.br/temas/estados-e-municipios/transferencias-constitucionais",
        documentacao="https://apiapex.tesouro.gov.br/",
        como_funciona=(
            "API de transferências constitucionais do Tesouro. O parâmetro "
            "`p_transferencia` seleciona os tipos por código, separados por `:` — FPM usa "
            "`3:7:18` (FPM, FPE e o 1% de julho/dezembro). A consulta é **por estado**, não "
            "por município: uma chamada devolve todos os municípios da UF, e por isso a "
            "entrega é registrada como nacional em vez de uma por ente."
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{TRANSFERENCIAS}por_estado_municipio",
                formato="JSON",
                o_que_traz="Repasse do mês para cada município da UF, por tipo de transferência.",
                parametros=(
                    Parametro("p_ano", "2025", "Exercício."),
                    Parametro("p_mes", "6", "Mês (1–12)."),
                    Parametro(
                        "p_transferencia",
                        "3:7:18",
                        "Códigos do catálogo de transferências, separados por `:`. "
                        "3=FPM, 7=FPE, 18=FPM 1%.",
                    ),
                    Parametro(
                        "p_estado",
                        "6",
                        "Código do estado **no padrão do Tesouro**, que não coincide com o "
                        "do IBGE: o Ceará é 23 no IBGE e 6 aqui — e o 23 do Tesouro é o Rio "
                        "Grande do Sul. Confundir os dois devolve dado do estado errado sem "
                        "nenhum erro aparente.",
                    ),
                ),
                exemplo=(
                    f"{TRANSFERENCIAS}por_estado_municipio"
                    "?p_ano=2025&p_mes=6&p_transferencia=3:7:18&p_estado=6"
                ),
            ),
            Endpoint(
                metodo="GET",
                url=f"{TRANSFERENCIAS}por_estados",
                formato="JSON",
                o_que_traz="Repasse do mês para o próprio estado (FPE), quando o ente é uma UF.",
                parametros=(
                    Parametro("p_ano", "2025", "Exercício."),
                    Parametro("p_mes", "6", "Mês (1–12)."),
                    Parametro("p_transferencia", "3:7:18", "Mesmos códigos."),
                    Parametro("p_estado", "6", "Código do Tesouro para o Ceará (no IBGE é 23)."),
                ),
                exemplo=(
                    f"{TRANSFERENCIAS}por_estados"
                    "?p_ano=2025&p_mes=6&p_transferencia=3:7:18&p_estado=6"
                ),
            ),
        ),
    ),
    "fnde_fundeb_repasse": Procedencia(
        acesso="api_rest",
        portal="https://www.tesourotransparente.gov.br/temas/estados-e-municipios/transferencias-constitucionais",
        documentacao="https://apiapex.tesouro.gov.br/",
        como_funciona=(
            "Mesma API das demais transferências, com `p_transferencia=10:14` (FUNDEB e "
            "complementação da União). Os códigos não se sobrepõem aos do FPM nem aos das "
            "demais transferências — um teste garante isso, porque sobreposição faria o "
            "mesmo repasse ser contado duas vezes na receita."
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{TRANSFERENCIAS}por_estado_municipio",
                formato="JSON",
                o_que_traz="Repasse mensal do FUNDEB por município da UF.",
                parametros=(
                    Parametro("p_ano", "2025", "Exercício."),
                    Parametro("p_mes", "6", "Mês (1–12)."),
                    Parametro("p_transferencia", "10:14", "10=FUNDEB, 14=complementação."),
                    Parametro("p_estado", "6", "Código do Tesouro para o Ceará (no IBGE é 23)."),
                ),
                exemplo=(
                    f"{TRANSFERENCIAS}por_estado_municipio"
                    "?p_ano=2025&p_mes=6&p_transferencia=10:14&p_estado=6"
                ),
            ),
        ),
    ),
    "transferencia_generica": Procedencia(
        acesso="api_rest",
        portal="https://www.tesourotransparente.gov.br/temas/estados-e-municipios/transferencias-constitucionais",
        documentacao="https://apiapex.tesouro.gov.br/",
        como_funciona=(
            "Os treze tipos restantes do catálogo (ITR, IOF-ouro, royalties, CIDE, IPI-"
            "exportação, cota-parte do ICMS e outros): `p_transferencia=1:2:4:5:6:8:9:11:"
            "12:13:15:16:17`. Serve de **contraprova** da receita declarada no RREO — quando "
            "os dois divergem, a divergência é mostrada, não escondida."
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{TRANSFERENCIAS}por_estado_municipio",
                formato="JSON",
                o_que_traz="Demais transferências constitucionais do mês, por município.",
                parametros=(
                    Parametro("p_ano", "2025", "Exercício."),
                    Parametro("p_mes", "6", "Mês (1–12)."),
                    Parametro(
                        "p_transferencia",
                        "1:2:4:5:6:8:9:11:12:13:15:16:17",
                        "Todos os tipos que não são FPM/FPE nem FUNDEB.",
                    ),
                    Parametro("p_estado", "6", "Código do Tesouro para o Ceará (no IBGE é 23)."),
                ),
                exemplo=(
                    f"{TRANSFERENCIAS}por_estado_municipio?p_ano=2025&p_mes=6"
                    "&p_transferencia=1:2:4:5:6:8:9:11:12:13:15:16:17&p_estado=6"
                ),
            ),
        ),
    ),
    # ============================== CAPAG ==============================
    "tesouro_capag": Procedencia(
        acesso="catalogo_ckan",
        portal="https://www.tesourotransparente.gov.br/temas/estados-e-municipios/capacidade-de-pagamento-capag",
        documentacao="https://docs.ckan.org/en/latest/api/",
        como_funciona=(
            "**Não há URL fixa de arquivo.** O Tesouro republica a CAPAG a cada apuração e o "
            "endereço muda; fixar um link significaria congelar numa publicação antiga sem "
            "perceber. Então consultamos o catálogo CKAN, lemos a lista de recursos do "
            "pacote e escolhemos a publicação mais recente pela **data no nome do recurso** "
            "— e não pelo `last_modified`, que é manutenção de catálogo: quatro publicações "
            "de 2024 foram tocadas no mesmo minuto e fora de ordem cronológica.\n\n"
            "Municípios e estados são dois pacotes distintos, com formatos distintos "
            "(planilha e CSV) e layouts que mudaram ao longo dos anos — o leitor reconhece "
            "três variações. Tratá-los como uma coisa só fazia 27 estados marcarem 5.568 "
            "municípios como superados."
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{CKAN}package_show?id=capag-municipios",
                formato="JSON (CKAN)",
                o_que_traz=(
                    "A ficha do conjunto de dados, com a lista de recursos publicados e a "
                    "URL de download de cada um."
                ),
                parametros=(
                    Parametro(
                        "id",
                        "capag-municipios",
                        "Identificador do pacote: `capag-municipios` ou `capag-estados`.",
                    ),
                ),
                exemplo=f"{CKAN}package_show?id=capag-municipios",
                observacao=(
                    "Abrindo este endereço aparecem os mesmos recursos que o conector "
                    "percorre — inclusive o que ele escolheu como mais recente."
                ),
            ),
            Endpoint(
                metodo="GET",
                url=f"{CKAN}package_show?id=capag-estados",
                formato="JSON (CKAN)",
                o_que_traz="A ficha do conjunto dos estados (publicação e formato próprios).",
                parametros=(
                    Parametro("id", "capag-estados", "Pacote dos estados."),
                ),
                exemplo=f"{CKAN}package_show?id=capag-estados",
            ),
            Endpoint(
                metodo="GET",
                url="{url_do_recurso_no_ckan}",
                formato="XLSX (municípios) · CSV (estados)",
                o_que_traz=(
                    "A planilha ou o CSV com a nota de cada ente e os subindicadores "
                    "(endividamento, poupança corrente, liquidez)."
                ),
                parametros=(),
                exemplo=None,
                observacao=(
                    "O endereço sai da resposta do catálogo acima — por isso não é fixo aqui. "
                    "É o único caso do sistema em que o endereço final é descoberto, e não "
                    "declarado."
                ),
            ),
        ),
    ),
    # ============================== SIOPS / SIOPE ==============================
    "siops_saude": Procedencia(
        acesso="api_rest",
        portal="https://siops.datasus.gov.br/",
        documentacao="https://siops-consulta-publica-api.saude.gov.br/",
        como_funciona=(
            "Indicadores de aplicação em Ações e Serviços Públicos de Saúde, direto do "
            "Ministério da Saúde. O caminho muda com a esfera do ente (municipal, estadual "
            "ou Distrito Federal) e o bimestre é traduzido para um código próprio do SIOPS "
            "— 1→12, 2→14, 3→1, 4→18, 5→20, 6→2 —, que não segue nenhuma ordem óbvia. "
            "Município usa o código IBGE de **6 dígitos** (sem o dígito verificador)."
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=f"{SIOPS}indicador/municipal/{{cod_ibge6}}/{{ano}}/{{codigo_periodo}}",
                formato="JSON",
                o_que_traz="Indicadores de saúde do município no bimestre, incluindo o % ASPS.",
                parametros=(
                    Parametro("cod_ibge6", "230440", "Código IBGE sem o dígito verificador."),
                    Parametro("ano", "2024", "Exercício."),
                    Parametro(
                        "codigo_periodo",
                        "2",
                        "Código interno do SIOPS para o bimestre (6º bimestre = 2).",
                    ),
                ),
                exemplo=f"{SIOPS}indicador/municipal/230440/2024/2",
            ),
            Endpoint(
                metodo="GET",
                url=f"{SIOPS}indicador/estadual/{{uf}}/{{ano}}/{{codigo_periodo}}",
                formato="JSON",
                o_que_traz="Os mesmos indicadores para um ente estadual.",
                parametros=(
                    Parametro("uf", "23", "Código IBGE da UF (2 dígitos)."),
                    Parametro("ano", "2024", "Exercício."),
                    Parametro("codigo_periodo", "2", "Código interno do bimestre."),
                ),
                exemplo=f"{SIOPS}indicador/estadual/23/2024/2",
            ),
        ),
    ),
    "siope_educacao": Procedencia(
        acesso="api_odata",
        portal="https://www.fnde.gov.br/siope/",
        documentacao="https://www.fnde.gov.br/olinda-ide/servico/DADOS_ABERTOS_SIOPE/versao/v1/odata/$metadata",
        como_funciona=(
            "É a única fonte em **OData**, o que muda a forma da consulta: os parâmetros de "
            "recorte vão no caminho (entre parênteses) e o filtro em `$filter`, no dialeto "
            "OData. A consulta é por UF e o ente é filtrado dentro dela — pedir um município "
            "isolado não é uma opção que a API ofereça."
        ),
        endpoints=(
            Endpoint(
                metodo="GET",
                url=(
                    f"{SIOPE}Indicadores_Siope"
                    "(Ano_Consulta={ano},Num_Peri={bimestre},Sig_UF='{uf}')"
                ),
                formato="JSON (OData)",
                o_que_traz=(
                    "Indicadores de aplicação em Manutenção e Desenvolvimento do Ensino "
                    "(MDE) e do FUNDEB, por ente da UF."
                ),
                parametros=(
                    Parametro("ano", "2024", "Exercício (no caminho)."),
                    Parametro("bimestre", "6", "Bimestre, 1 a 6 (no caminho)."),
                    Parametro("uf", "CE", "Sigla da UF (no caminho, entre aspas simples)."),
                    Parametro(
                        "$filter",
                        "Cod_Municipio eq '2304400'",
                        "Filtro OData que restringe ao ente desejado.",
                    ),
                    Parametro("$format", "json", "Fixo: formato da resposta."),
                ),
                exemplo=(
                    f"{SIOPE}Indicadores_Siope"
                    "(Ano_Consulta=2024,Num_Peri=6,Sig_UF='CE')?$format=json"
                ),
                observacao=(
                    "O exemplo traz a UF inteira, sem `$filter`, para que o conteúdo seja "
                    "visível de imediato no navegador."
                ),
            ),
        ),
    ),
}
