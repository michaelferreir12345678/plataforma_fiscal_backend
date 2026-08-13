"""Regras do dicionário semântico: seed idempotente, catraca e busca (Sprint IA-2).

Três coisas moram aqui:

1. **Seed idempotente** — o dicionário é dado, mas mantido em código (``verbetes``,
   ``campos``, ``juncoes``) e aplicado por *upsert*, como o grafo de linhagem da Sprint 26.
2. **Catraca de completude** — a defesa contra o modo de falha que o §3 do plano nomeia:
   um dicionário que envelhece em silêncio e passa a mentir. A catraca aceita a melhora
   (verbete novo, coluna nova descrita) e falha na piora (indicador no mart sem verbete,
   coluna sem descrição, verbete apontando para coluna que não existe mais).
3. **Busca** — resolver o vocabulário de negócio ("gasto com pessoal") para o código, e
   selecionar os verbetes relevantes a uma pergunta para entrarem no contexto do agente.

Nada aqui calcula valor fiscal: o dicionário descreve o cálculo, quem calcula é
``indicators/`` (§7 do CLAUDE.md).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

# Reuso deliberado do tokenizador do RAG: uma segunda normalização de texto divergiria da
# primeira no primeiro acento, e "gasto com pessoal" deixaria de casar num dos caminhos.
from app.modules.assistant import vectors
from app.modules.catalog import repository as catalog_repo
from app.modules.dictionary import campos as campos_seed
from app.modules.dictionary import juncoes as juncoes_seed
from app.modules.dictionary import repository
from app.modules.dictionary import verbetes as verbetes_seed
from app.modules.dictionary.models import DicionarioCampo, DicionarioIndicador, DicionarioJuncao

#: Teto de verbetes por pergunta. O dicionário inteiro em toda pergunta é o erro que a
#: §2.3 descreve ao contrário: recurso barato vira contexto caro se ninguém o recortar.
MAX_VERBETES_POR_PERGUNTA = 3


# --------------------------------------------------------------------------- #
# Seed
# --------------------------------------------------------------------------- #
def seed_dicionario(session: Session) -> dict[str, int]:
    """Aplica o dicionário do código no banco (idempotente). Retorna os totais."""
    for verbete in verbetes_seed.VERBETES:
        repository.upsert_verbete(
            session,
            {
                "codigo": verbete.codigo,
                "rotulo": verbete.rotulo,
                "definicao": verbete.definicao,
                "formula": verbete.formula,
                "denominador": verbete.denominador,
                "denominador_fallback": verbete.denominador_fallback,
                "denominador_definicao": verbete.denominador_definicao,
                "unidade": verbete.unidade,
                "sentido": verbete.sentido,
                "base_legal": verbete.base_legal,
                "tabela_origem": verbete.tabela_origem,
                "coluna_valor": verbete.coluna_valor,
                "coluna_base": verbete.coluna_base,
                "sinonimos": list(verbete.sinonimos),
                "armadilha": verbete.armadilha,
                "fonte_definicao": verbete.fonte_definicao,
                "atualizado_em": verbete.atualizado_em,
            },
        )
    for campo in campos_seed.campos():
        repository.upsert_campo(
            session,
            {
                "schema_nome": campo.schema_nome,
                "tabela": campo.tabela,
                "coluna": campo.coluna,
                "descricao": campo.descricao,
                "unidade": campo.unidade,
                "chave": campo.chave,
                "consultavel": campo.consultavel,
                "armadilha": campo.armadilha,
                "fonte_definicao": campo.fonte_definicao,
                "atualizado_em": campo.atualizado_em,
            },
        )
    for juncao in juncoes_seed.JUNCOES:
        repository.upsert_juncao(
            session,
            {
                "origem_tabela": juncao.origem_tabela,
                "origem_colunas": list(juncao.origem_colunas),
                "destino_tabela": juncao.destino_tabela,
                "destino_colunas": list(juncao.destino_colunas),
                "cardinalidade": juncao.cardinalidade,
                "condicao": juncao.condicao,
                "nota": juncao.nota,
                "fonte_definicao": juncao.fonte_definicao,
                "atualizado_em": juncao.atualizado_em,
            },
        )
    session.flush()
    return {
        "indicadores": repository.contar_verbetes(session),
        "campos": repository.contar_campos(session),
        "juncoes": repository.contar_juncoes(session),
    }


def garantir_seed(session: Session) -> None:
    """Semeia o dicionário na primeira leitura de um banco que ainda não o tem."""
    if repository.contar_verbetes(session) == 0:
        seed_dicionario(session)


# --------------------------------------------------------------------------- #
# Catraca de completude
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Auditoria:
    """Resultado da catraca. Vazia em todas as listas ⇒ dicionário íntegro."""

    indicadores_sem_verbete: list[str] = field(default_factory=list)
    verbetes_incompletos: list[str] = field(default_factory=list)
    colunas_sem_descricao: list[str] = field(default_factory=list)
    descricoes_orfas: list[str] = field(default_factory=list)
    referencias_quebradas: list[str] = field(default_factory=list)
    tabelas_op_consultaveis: list[str] = field(default_factory=list)
    denominadores_nao_declarados: list[str] = field(default_factory=list)
    sentidos_divergentes: list[str] = field(default_factory=list)
    juncoes_invalidas: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problemas()

    def problemas(self) -> list[str]:
        """Todos os achados, prefixados pela categoria — a saída da catraca."""
        rotulos = {
            "indicadores_sem_verbete": "indicador materializado sem verbete",
            "verbetes_incompletos": "verbete sem fórmula ou base legal",
            "colunas_sem_descricao": "coluna de tabela consultável sem descrição",
            "descricoes_orfas": "descrição de coluna que não existe no banco",
            "referencias_quebradas": "verbete apontando para tabela/coluna inexistente",
            "tabelas_op_consultaveis": "tabela de 'op' marcada como consultável",
            "denominadores_nao_declarados": "denominador materializado que o verbete não prevê",
            "sentidos_divergentes": "sentido do verbete divergente de dim_limite_legal",
            "juncoes_invalidas": "junção sancionada inválida",
        }
        achados: list[str] = []
        for atributo, rotulo in rotulos.items():
            for item in getattr(self, atributo):
                achados.append(f"{rotulo}: {item}")
        return achados


def lacunas_de_indicador(
    codigos_materializados: set[str], verbetes: Mapping[str, object]
) -> list[str]:
    """Códigos presentes no mart que não têm verbete. Função pura (testável sem banco)."""
    return sorted(codigos_materializados - set(verbetes))


def lacunas_de_campo(
    colunas_reais: dict[str, set[str]], descritas: dict[str, set[str]]
) -> tuple[list[str], list[str]]:
    """``(colunas sem descrição, descrições órfãs)``. Função pura.

    A segunda metade é o que impede o dicionário de mentir por omissão do outro lado: uma
    coluna renomeada por migration deixaria a descrição antiga apontando para o nada, e a
    consulta gerada a partir dela falharia — ou, pior, casaria com outra coisa.
    """
    faltando: list[str] = []
    orfas: list[str] = []
    for tabela in sorted(set(colunas_reais) | set(descritas)):
        reais = colunas_reais.get(tabela, set())
        descrita = descritas.get(tabela, set())
        faltando.extend(f"{tabela}.{c}" for c in sorted(reais - descrita))
        orfas.extend(f"{tabela}.{c}" for c in sorted(descrita - reais))
    return faltando, orfas


def auditar_completude(session: Session) -> Auditoria:
    """Roda a catraca inteira contra o banco. É o teste que reprova a suíte."""
    garantir_seed(session)
    verbetes = {v.codigo: v for v in repository.listar_verbetes(session)}
    campos_por_tabela: dict[str, set[str]] = {}
    tabelas_op: list[str] = []
    for campo in repository.listar_campos(session):
        chave = f"{campo.schema_nome}.{campo.tabela}"
        campos_por_tabela.setdefault(chave, set()).add(campo.coluna)
        if campo.schema_nome == "op" and campo.consultavel:
            tabelas_op.append(chave)

    tabelas = sorted(set(campos_por_tabela) | set(campos_seed.TABELAS_CONSULTAVEIS))
    reais = repository.colunas_reais(session, tabelas)
    faltando, orfas = lacunas_de_campo(reais, campos_por_tabela)

    # Todo indicador do mart precisa de verbete — e a lista estática garante que a catraca
    # continue significando alguma coisa num banco recém-migrado (sem linhas no mart).
    materializados = repository.indicadores_no_mart(session) | set(
        verbetes_seed.CODIGOS_MATERIALIZADOS
    )
    incompletos = sorted(
        codigo
        for codigo, v in verbetes.items()
        if not v.formula.strip() or not v.base_legal.strip() or not v.definicao.strip()
    )

    referencias = _referencias_quebradas(session, list(verbetes.values()))
    denominadores = _denominadores_nao_declarados(session, verbetes)
    sentidos = _sentidos_divergentes(session, verbetes)
    juncoes = _juncoes_invalidas(repository.listar_juncoes(session), reais)

    return Auditoria(
        indicadores_sem_verbete=lacunas_de_indicador(materializados, verbetes),
        verbetes_incompletos=incompletos,
        colunas_sem_descricao=faltando,
        descricoes_orfas=orfas,
        referencias_quebradas=referencias,
        tabelas_op_consultaveis=sorted(set(tabelas_op)),
        denominadores_nao_declarados=denominadores,
        sentidos_divergentes=sentidos,
        juncoes_invalidas=juncoes,
    )


def _referencias_quebradas(
    session: Session, verbetes: Sequence[DicionarioIndicador]
) -> list[str]:
    """Verbete que aponta para tabela/coluna que o ``information_schema`` não conhece."""
    tabelas = sorted({v.tabela_origem for v in verbetes})
    reais = repository.colunas_reais(session, tabelas)
    achados: list[str] = []
    for verbete in verbetes:
        colunas = reais.get(verbete.tabela_origem, set())
        if not colunas:
            achados.append(f"{verbete.codigo} → tabela {verbete.tabela_origem} não existe")
            continue
        for atributo in ("coluna_valor", "coluna_base"):
            coluna = getattr(verbete, atributo)
            if coluna and coluna not in colunas:
                achados.append(
                    f"{verbete.codigo} → {verbete.tabela_origem}.{coluna} ({atributo}) "
                    "não existe"
                )
    return achados


def _denominadores_nao_declarados(
    session: Session, verbetes: dict[str, DicionarioIndicador]
) -> list[str]:
    """Denominador que o mart usa de verdade e o verbete não prevê.

    É a checagem que teria pego a Sprint 28 antes do gestor: o dicionário afirma qual é o
    100% do percentual, e o mart é quem sabe qual foi usado. Divergir aqui significa que a
    definição descolou da apuração.
    """
    achados: list[str] = []
    for codigo, observados in sorted(repository.denominadores_no_mart(session).items()):
        verbete = verbetes.get(codigo)
        if verbete is None:
            continue  # já contabilizado como indicador sem verbete
        declarados = {verbete.denominador, verbete.denominador_fallback or ""} - {""}
        for denominador in sorted(observados - declarados):
            achados.append(f"{codigo} usa '{denominador}' no mart, não declarado no verbete")
    return achados


def _sentidos_divergentes(
    session: Session, verbetes: dict[str, DicionarioIndicador]
) -> list[str]:
    """Sentido do verbete × ``gold.dim_limite_legal`` — os tetos/pisos já são dado (§2)."""
    achados: list[str] = []
    for codigo, verbete in sorted(verbetes.items()):
        for esfera in ("municipal", "estadual"):
            poder = "Executivo" if codigo == "pessoal_executivo" else ""
            limite = catalog_repo.get_limite(
                session, indicador=codigo, esfera=esfera, poder=poder
            )
            if limite is None:
                continue
            if verbete.sentido == "gerencial":
                achados.append(
                    f"{codigo} é 'gerencial' no verbete mas tem limite em dim_limite_legal "
                    f"({esfera})"
                )
            elif limite.sentido != verbete.sentido:
                achados.append(
                    f"{codigo}/{esfera}: verbete diz '{verbete.sentido}', dim_limite_legal "
                    f"diz '{limite.sentido}'"
                )
    return achados


def _juncoes_invalidas(
    juncoes: Sequence[DicionarioJuncao], reais: dict[str, set[str]]
) -> list[str]:
    """Junção que referencia tabela não consultável, coluna inexistente ou aridade errada."""
    consultaveis = set(campos_seed.TABELAS_CONSULTAVEIS)
    achados: list[str] = []
    for juncao in juncoes:
        rotulo = f"{juncao.origem_tabela} → {juncao.destino_tabela}"
        for lado, tabela, colunas in (
            ("origem", juncao.origem_tabela, juncao.origem_colunas),
            ("destino", juncao.destino_tabela, juncao.destino_colunas),
        ):
            if tabela not in consultaveis:
                achados.append(f"{rotulo}: {lado} '{tabela}' não é tabela consultável")
                continue
            existentes = reais.get(tabela, set())
            for coluna in colunas:
                if coluna not in existentes:
                    achados.append(f"{rotulo}: {tabela}.{coluna} não existe")
        if len(juncao.origem_colunas) != len(juncao.destino_colunas):
            achados.append(f"{rotulo}: aridade diferente entre os dois lados")
    return achados


# --------------------------------------------------------------------------- #
# Busca — vocabulário de negócio → esquema
# --------------------------------------------------------------------------- #
def _termos(texto: str) -> set[str]:
    return set(vectors.tokenize(texto))


def resolver_codigo(session: Session, termo: str) -> str | None:
    """Resolve um termo de negócio para o código canônico ("gasto com pessoal" ⇒ código).

    Exige que **todos** os termos do sinônimo estejam presentes, como em
    ``retriever.indicadores_nomeados``: casar por palavra solta faria "crédito" sequestrar
    uma pergunta sobre crédito tributário.
    """
    ranking = _ranquear(session, termo, codigos_extra=set())
    return ranking[0].codigo if ranking else None


def verbetes_para_pergunta(
    session: Session,
    pergunta: str,
    *,
    codigos: set[str] | None = None,
    limite: int = MAX_VERBETES_POR_PERGUNTA,
) -> list[DicionarioIndicador]:
    """Verbetes relevantes à pergunta (e aos indicadores já no contexto), em ordem estável."""
    garantir_seed(session)
    return _ranquear(session, pergunta, codigos_extra=codigos or set())[:limite]


def _ranquear(
    session: Session, pergunta: str, *, codigos_extra: set[str]
) -> list[DicionarioIndicador]:
    garantir_seed(session)
    tokens = _termos(pergunta)
    pontuados: list[tuple[int, str, DicionarioIndicador]] = []
    for verbete in repository.listar_verbetes(session):
        score = 0
        codigo_tokens = _termos(verbete.codigo.replace("_", " "))
        if codigo_tokens and codigo_tokens <= tokens:
            score += 3
        if any(
            (termos := _termos(sinonimo)) and termos <= tokens
            for sinonimo in (verbete.sinonimos or [])
        ):
            score += 3
        rotulo_tokens = _termos(verbete.rotulo)
        if rotulo_tokens and rotulo_tokens <= tokens:
            score += 2
        # O denominador é caminho de entrada legítimo: "o que é RCL Ajustada?" é uma
        # pergunta sobre o denominador do limite de pessoal, e é lá que a resposta mora.
        denominador_tokens = _termos((verbete.denominador or "").replace("_", " "))
        if denominador_tokens and denominador_tokens <= tokens:
            score += 2
        if verbete.codigo in codigos_extra:
            score += 1
        if score:
            pontuados.append((score, verbete.codigo, verbete))
    pontuados.sort(key=lambda item: (-item[0], item[1]))
    return [verbete for _, _, verbete in pontuados]


# --------------------------------------------------------------------------- #
# Renderização (o conteúdo dos recursos)
# --------------------------------------------------------------------------- #
def _limites_do_indicador(session: Session, codigo: str) -> str:
    """Tetos/pisos vigentes lidos de ``gold.dim_limite_legal`` — nunca copiados no verbete."""
    partes: list[str] = []
    for esfera in ("municipal", "estadual"):
        poder = "Executivo" if codigo == "pessoal_executivo" else ""
        limite = catalog_repo.get_limite(session, indicador=codigo, esfera=esfera, poder=poder)
        if limite is not None:
            partes.append(f"{esfera} {limite.teto_pct:g}% ({limite.sentido})")
    return "; ".join(partes) if partes else "sem limite legal (indicador gerencial)"


def render_indicadores(session: Session) -> str:
    """Recurso ``dicionario://indicadores`` — um verbete por indicador, em Markdown."""
    garantir_seed(session)
    linhas = [
        "# Dicionário de indicadores da plataforma",
        "",
        "Definições oficiais do que cada indicador mede. Use estas definições em vez de "
        "conhecimento geral: o denominador correto de um limite não é adivinhável.",
        "",
    ]
    for verbete in repository.listar_verbetes(session):
        linhas.extend(
            [
                f"## {verbete.rotulo} (`{verbete.codigo}`)",
                f"- **Definição:** {verbete.definicao}",
                f"- **Fórmula:** {verbete.formula}",
                f"- **Denominador:** `{verbete.denominador or '—'}`"
                + (
                    f" (reserva: `{verbete.denominador_fallback}`)"
                    if verbete.denominador_fallback
                    else ""
                )
                + f" — {verbete.denominador_definicao}",
                f"- **Unidade:** {verbete.unidade} | **Sentido:** {verbete.sentido}",
                f"- **Limite legal:** {_limites_do_indicador(session, verbete.codigo)}",
                f"- **Base legal:** {verbete.base_legal}",
                f"- **Origem:** {verbete.tabela_origem}.{verbete.coluna_valor}"
                + (f" (base em {verbete.coluna_base})" if verbete.coluna_base else ""),
                f"- **Também chamado de:** {', '.join(verbete.sinonimos or []) or '—'}",
            ]
        )
        if verbete.armadilha:
            linhas.append(f"- **Atenção:** {verbete.armadilha}")
        linhas.append(
            f"- _Definição de {verbete.fonte_definicao}; revisada em "
            f"{verbete.atualizado_em.isoformat()}._"
        )
        linhas.append("")
    return "\n".join(linhas)


def render_campos(session: Session) -> str:
    """Recurso ``dicionario://campos`` — colunas das tabelas consultáveis."""
    garantir_seed(session)
    por_tabela: dict[str, list[DicionarioCampo]] = {}
    for campo in repository.listar_campos(session):
        por_tabela.setdefault(f"{campo.schema_nome}.{campo.tabela}", []).append(campo)
    linhas = [
        "# Dicionário de campos (tabelas consultáveis)",
        "",
        "Só as tabelas listadas aqui podem entrar em consulta analítica. Nenhuma tabela do "
        "schema `op` é consultável: dado da organização não entra em consulta livre.",
        "",
    ]
    for tabela, campos_da_tabela in sorted(por_tabela.items()):
        linhas.append(f"## {tabela}")
        for campo in campos_da_tabela:
            marca = " *(chave)*" if campo.chave else ""
            if not campo.consultavel:
                marca += " *(não consultável)*"
            unidade = f" [{campo.unidade}]" if campo.unidade else ""
            linha = f"- `{campo.coluna}`{unidade}{marca}: {campo.descricao}"
            if campo.armadilha:
                linha += f" **Atenção:** {campo.armadilha}"
            linhas.append(linha)
        linhas.append("")
    return "\n".join(linhas)


def render_juncoes(session: Session) -> str:
    """Recurso ``dicionario://juncoes`` — caminhos de junção sancionados."""
    garantir_seed(session)
    linhas = [
        "# Caminhos de junção sancionados",
        "",
        "Junção fora desta lista não é sancionada. O risco não é o JOIN que falha: é o que "
        "funciona e multiplica linha.",
        "",
    ]
    for juncao in repository.listar_juncoes(session):
        origem = ", ".join(juncao.origem_colunas)
        destino = ", ".join(juncao.destino_colunas)
        linhas.append(
            f"## {juncao.origem_tabela} → {juncao.destino_tabela} ({juncao.cardinalidade})"
        )
        linhas.append(f"- **ON:** ({origem}) = ({destino})")
        if juncao.condicao:
            linhas.append(f"- **Condição obrigatória:** {juncao.condicao}")
        linhas.append(f"- **Nota:** {juncao.nota}")
        linhas.append("")
    return "\n".join(linhas)
