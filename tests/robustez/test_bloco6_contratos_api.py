# -*- coding: utf-8 -*-
"""BLOCO 6 — Contratos da API (particionamento em classes de equivalência + varredura de rotas).

Métodos: (1) smoke/varredura: toda rota GET sem parâmetro responde sem erro 5xx num caso vazio e com o caso demo;
(2) particionamento de equivalência: cada classe de entrada INVÁLIDA precisa ser recusada com 422 (nunca aceita em
silêncio, nunca 500); a classe VÁLIDA "corpo ausente" da simulação usa o padrão e responde 200.
Ambiente isolado (pasta de dados temporária e token desativado pelo conftest).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

ROTAS_EXTERNAS = ("ollama", "gemini", "assistente", "abrir-pasta", "atualizacao", "update")


def _exigir_422(r, payload):
    assert r.status_code == 422, (payload, r.status_code, r.text[:300])
    assert r.json().get("detail"), "422 sem detalhe do campo"


def _rotas_get_sem_parametro():
    return sorted({
        r.path for r in app.routes
        if hasattr(r, "methods") and "GET" in r.methods and r.path.startswith("/api")
        and "{" not in r.path and not any(x in r.path for x in ROTAS_EXTERNAS)
    })


@pytest.fixture(scope="module")
def cliente():
    return TestClient(app)


@pytest.mark.parametrize("com_caso", [False, True], ids=["sem_caso", "caso_demo"])
def test_nenhuma_rota_get_devolve_erro_500(cliente, com_caso):
    if com_caso:
        assert cliente.post("/api/cases/load-demo").status_code == 200
    else:
        cliente.post("/api/cases/reset")
    falhas = []
    for rota in _rotas_get_sem_parametro():
        r = cliente.get(rota)
        if r.status_code >= 500:
            falhas.append(f"{rota} → {r.status_code}")
    assert not falhas, falhas


def test_simulacao_com_corpo_ausente_usa_o_padrao(cliente):
    """Classe válida: o corpo é opcional; sem corpo, a simulação usa os parâmetros padrão."""
    cliente.post("/api/cases/load-demo")
    assert cliente.post("/api/dosimetria/simulate", json=None).status_code == 200


SIMULACAO_INVALIDA = [
    pytest.param("texto solto", id="corpo_texto"),
    pytest.param([], id="corpo_lista"),
    pytest.param({"criterio_primeira_fase": 123}, id="criterio_numero"),
    pytest.param({"crimes_config": "deveria ser lista"}, id="crimes_config_texto"),
    pytest.param({"valor_dano_moral": "mil reais"}, id="dano_texto"),
    pytest.param({"valor_dano_moral": -500}, id="dano_negativo"),
    pytest.param({"dias_prisao_cautelar": "dez"}, id="detracao_texto"),
    # Booleano reconhecido pelo modelo com texto sem sentido (classe isolada).
    pytest.param({"reincidente": "talvez"}, id="booleano_sem_sentido"),
    # Brechas reais (classes isoladas, código penal existente no demo):
    pytest.param({"crimes_config": [{"codigo": "art_147_cp", "veredicto": 7}]}, id="veredicto_numerico"),
    pytest.param({"campo_inexistente": 1}, id="campo_desconhecido"),
]


@pytest.mark.parametrize("payload", SIMULACAO_INVALIDA)
def test_simulacao_recusa_entradas_invalidas_com_422(cliente, payload):
    cliente.post("/api/cases/load-demo")
    _exigir_422(cliente.post("/api/dosimetria/simulate", json=payload), payload)


@pytest.mark.parametrize("payload", [{"reincidente": "true"}, {"reincidente": False}], ids=["booleano_textual_padrao", "booleano"])
def test_simulacao_aceita_booleanos_validos(cliente, payload):
    """Classe válida: 'true'/'false' em texto é a conversão padrão do modelo e é aceita."""
    cliente.post("/api/cases/load-demo")
    assert cliente.post("/api/dosimetria/simulate", json=payload).status_code == 200


OVERRIDE_INVALIDO = [
    pytest.param({"metadados": {"data_fato": "31/02/2025"}}, id="data_impossivel"),
    pytest.param({"metadados": {"campo_inexistente": 1}}, id="campo_desconhecido"),
    pytest.param({"metadados": "texto"}, id="metadados_texto"),
    pytest.param({"metadados": []}, id="metadados_lista"),
    pytest.param(None, id="corpo_nulo"),
    pytest.param([], id="corpo_lista"),
    pytest.param({"data_fato": "31/02/2025"}, id="fora_do_envelope"),
]


@pytest.mark.parametrize("payload", OVERRIDE_INVALIDO)
def test_override_de_metadados_recusa_entradas_invalidas_com_422(cliente, payload):
    cliente.post("/api/cases/load-demo")
    _exigir_422(cliente.post("/api/cases/metadata-override", json=payload), payload)


@pytest.mark.parametrize("rota", [
    "/api/dosimetria/simulate",
    "/api/sentence/generate",
    "/api/sentence/generate-sectional",
])
@pytest.mark.parametrize(("campo", "valor"), [
    ("tentativa_fracao", "2/1"),
    ("reincidente", "não"),
    ("veredicto", 7),
    ("dias_multa", True),
    ("valor_unitario_multa", "1/60"),
])
def test_decisao_invalida_indica_o_caminho_no_envelope(cliente, rota, campo, valor):
    crime = {"codigo": "art_147_cp", campo: valor}
    payload = {"custom_dosimetria_override": {"crimes_config": [crime]}}
    r = cliente.post(rota, json=payload)
    _exigir_422(r, payload)
    assert ["body", "custom_dosimetria_override", "crimes_config", 0, campo] in [
        detalhe["loc"] for detalhe in r.json()["detail"]
    ]


def test_tentativa_de_cumular_multa_alternativa_indica_o_campo(cliente):
    """A multa do art. 147 do CP é alternativa à detenção.
    https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm#art147
    """
    cliente.post("/api/cases/load-demo")
    payload = {"crimes_config": [{
        "codigo": "art_147_cp", "veredicto": "condenar", "enquadramento_ameaca": "caput",
        "dias_multa": 20, "valor_unitario_multa": "1/15",
    }]}
    r = cliente.post("/api/dosimetria/simulate", json=payload)
    _exigir_422(r, payload)
    assert r.json()["campos"][0].endswith(".dias_multa")
