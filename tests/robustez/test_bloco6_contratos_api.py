# -*- coding: utf-8 -*-
"""BLOCO 6 — Contratos da API (particionamento em classes de equivalência + varredura de rotas).

Métodos: (1) smoke/varredura: toda rota GET sem parâmetro responde sem erro 5xx num caso vazio e com o caso demo;
(2) particionamento de equivalência: cada classe de entrada INVÁLIDA precisa ser recusada com 422 (nunca aceita em
silêncio, nunca 500); a classe VÁLIDA "corpo ausente" da simulação usa o padrão e responde 200.
Brechas ainda abertas ficam como ``xfail(strict=True)`` apontando a tarefa que as corrige: quando a correção entrar,
o teste passa a falhar como XPASS e obriga a promover o caso a exigência (revisão cruzada do CODEX na TASK-072).
Ambiente isolado (pasta de dados temporária e token desativado pelo conftest).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

ROTAS_EXTERNAS = ("ollama", "gemini", "assistente", "abrir-pasta", "atualizacao", "update")


class AceitacaoIndevida(AssertionError):
    """A rota aceitou (HTTP 200) uma entrada que o contrato manda recusar. É a ÚNICA falha esperada nos xfail."""


# xfail restrito ao tipo conhecido (revisão cruzada do CODEX): um 500 ou outra exceção falha normalmente, e o 422
# esperado após a TASK-061 vira XPASS (strict) e obriga a promover o caso.
PENDENTE_061 = pytest.mark.xfail(raises=AceitacaoIndevida, strict=True,
                                 reason="TASK-061: validação estrita das entradas (P1-09)")


def _exigir_422(r, payload):
    if r.status_code == 200:
        raise AceitacaoIndevida(f"aceitou {payload!r}")
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
    pytest.param({"crimes_config": [{"codigo": "art_147_cp", "veredicto": 7}]}, id="veredicto_numerico", marks=PENDENTE_061),
    pytest.param({"campo_inexistente": 1}, id="campo_desconhecido", marks=PENDENTE_061),
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
    pytest.param({"data_fato": "31/02/2025"}, id="fora_do_envelope", marks=PENDENTE_061),
]


@pytest.mark.parametrize("payload", OVERRIDE_INVALIDO)
def test_override_de_metadados_recusa_entradas_invalidas_com_422(cliente, payload):
    cliente.post("/api/cases/load-demo")
    _exigir_422(cliente.post("/api/cases/metadata-override", json=payload), payload)
