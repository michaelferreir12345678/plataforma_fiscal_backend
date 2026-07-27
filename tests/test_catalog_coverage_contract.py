"""Contrato HTTP do catálogo e da paginação da cobertura."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select

from app.core.db import SessionLocal
from app.modules.ingestion.models import CatalogoFonte, MartCoberturaFonte
from tests.conftest import auth_header, login


def _cod_ibge_unico() -> str:
    return f"9{uuid.uuid4().int % 1_000_000:06d}"


def test_catalogo_expoe_soma_de_registros_cobertos(client, make_org) -> None:
    fx = make_org()
    token = login(client, fx.email, fx.senha)
    codigos = [_cod_ibge_unico(), _cod_ibge_unico()]
    fonte = "siconfi_rreo"
    try:
        with SessionLocal() as session:
            session.add_all(
                [
                    MartCoberturaFonte(
                        fonte=fonte,
                        cod_ibge=codigos[0],
                        uf="CE",
                        ano=2098,
                        periodo="2098-B1",
                        n_registros=7,
                    ),
                    MartCoberturaFonte(
                        fonte=fonte,
                        cod_ibge=codigos[1],
                        uf="CE",
                        ano=2098,
                        periodo="2098-B1",
                        n_registros=11,
                    ),
                ]
            )
            session.commit()
            esperado = int(
                session.scalar(
                    select(func.sum(MartCoberturaFonte.n_registros)).where(
                        MartCoberturaFonte.fonte == fonte
                    )
                )
                or 0
            )

        response = client.get("/admin/ingestion/fontes", headers=auth_header(token))
        assert response.status_code == 200, response.text
        item = next(row for row in response.json() if row["fonte"] == fonte)
        assert item["registros_cobertos"] == esperado
    finally:
        with SessionLocal() as session:
            session.execute(
                delete(MartCoberturaFonte).where(
                    MartCoberturaFonte.fonte == fonte,
                    MartCoberturaFonte.cod_ibge.in_(codigos),
                )
            )
            session.commit()


def test_catalogo_lista_metadado_persistido_fora_do_registry(client, make_org) -> None:
    fx = make_org()
    token = login(client, fx.email, fx.senha)
    fonte = f"fonte_persistida_{uuid.uuid4().hex}"
    try:
        with SessionLocal() as session:
            session.add(
                CatalogoFonte(
                    fonte=fonte,
                    familia="parceiro",
                    relatorio="RELATORIO PARCEIRO",
                    descricao="Fonte cadastrada diretamente no catálogo.",
                    cadencia="anual",
                    orgao="Órgão parceiro",
                    url_origem="https://example.invalid/dados",
                    escopo="nacional",
                    parser_versao="v1",
                    paginas_impactadas=["dashboard"],
                    dependencias=[],
                )
            )
            session.commit()

        response = client.get("/admin/ingestion/fontes", headers=auth_header(token))
        assert response.status_code == 200, response.text
        item = next(row for row in response.json() if row["fonte"] == fonte)
        assert item["familia"] == "parceiro"
        assert item["relatorio"] == "RELATORIO PARCEIRO"
        assert item["paginas_impactadas"] == ["dashboard"]
        assert item["registros_cobertos"] == 0
        assert item["ativo"] is True
    finally:
        with SessionLocal() as session:
            session.execute(delete(CatalogoFonte).where(CatalogoFonte.fonte == fonte))
            session.commit()


def test_cobertura_pagina_grupos_sem_cortar_periodos(client, make_org) -> None:
    fx = make_org()
    token = login(client, fx.email, fx.senha)
    fonte = f"teste_cobertura_{uuid.uuid4().hex}"
    codigos = sorted([_cod_ibge_unico(), _cod_ibge_unico()])
    periodos_por_codigo = {
        codigos[0]: ["2098-B1", "2098-B2", "2098-B3"],
        codigos[1]: ["2098-B1", "2098-B2"],
    }
    try:
        with SessionLocal() as session:
            session.add_all(
                [
                    MartCoberturaFonte(
                        fonte=fonte,
                        cod_ibge=cod_ibge,
                        uf="CE",
                        ano=2098,
                        periodo=periodo,
                        n_registros=1,
                    )
                    for cod_ibge, periodos in periodos_por_codigo.items()
                    for periodo in periodos
                ]
            )
            session.commit()

        page_1 = client.get(
            f"/admin/ingestion/cobertura?fonte={fonte}&page=1&page_size=1",
            headers=auth_header(token),
        )
        assert page_1.status_code == 200, page_1.text
        body_1 = page_1.json()
        assert body_1["total"] == 2
        assert body_1["page_size"] == 1
        assert body_1["resumo"]["total_linhas"] == 5
        assert {row["cod_ibge"] for row in body_1["data"]} == {codigos[0]}
        assert {row["periodo"] for row in body_1["data"]} == set(periodos_por_codigo[codigos[0]])

        page_2 = client.get(
            f"/admin/ingestion/cobertura?fonte={fonte}&page=2&page_size=1",
            headers=auth_header(token),
        )
        assert page_2.status_code == 200, page_2.text
        body_2 = page_2.json()
        assert body_2["total"] == 2
        assert {row["cod_ibge"] for row in body_2["data"]} == {codigos[1]}
        assert {row["periodo"] for row in body_2["data"]} == set(periodos_por_codigo[codigos[1]])
    finally:
        with SessionLocal() as session:
            session.execute(delete(MartCoberturaFonte).where(MartCoberturaFonte.fonte == fonte))
            session.commit()
