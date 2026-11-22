# -*- coding: utf-8 -*-
"""Testes da descoberta/seleção de modelos Ollama e aviso de modelo ausente (TASK-021)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from app.api.security import TOKEN_HEADER, get_session_token
from app.core import config as cfg
from app.core import ollama_client as oc
from app.core import vram_manager as vm
from app.demo.demo_case import get_demo_case_bundle
from app.generation.sectional_pipeline import SectionalPipelineEngine
from app.main import app


class _FakeResp:
    def __init__(self, status_code: int, payload: Dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, *, tags_resp=None, generate_fn=None, raise_on_get: Exception | None = None) -> None:
        self._tags_resp = tags_resp
        self._generate_fn = generate_fn
        self._raise_on_get = raise_on_get

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url: str):
        if self._raise_on_get:
            raise self._raise_on_get
        return self._tags_resp

    def post(self, url: str, json: Dict[str, Any]):
        if self._generate_fn:
            return self._generate_fn(url, json)
        return _FakeResp(200, {"response": "ok"})


def _headers_auth() -> dict:
    return {TOKEN_HEADER: get_session_token()}


def test_padrao_ollama_model_eh_auto():
    assert cfg.UserSettings().ollama_model == "auto"


def test_listar_modelos_configurado_instalado_e_details_ausentes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "user_settings.json")
    cfg.save_settings(cfg.UserSettings(ollama_model="gemma4:e2b"))

    tags_payload = {
        "models": [
            {
                "name": "gemma4:e2b",
                "size": int(3.2 * (1024**3)),
                "details": {"parameter_size": "2.3B", "quantization_level": "Q4_K_M"},
            },
            {
                "name": "modelo-sem-detalhes:latest",
                # sem size e sem details -> campos ausentes devem virar None (null)
            },
        ]
    }
    monkeypatch.setattr(oc, "criar_cliente", lambda timeout: _FakeClient(tags_resp=_FakeResp(200, tags_payload)))

    client = TestClient(app)
    resp = client.get("/api/ollama/modelos", headers=_headers_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["online"] is True
    assert body["configurado"] == "gemma4:e2b"
    assert body["configurado_instalado"] is True
    assert body["recomendado"] == "gemma4:e2b"
    assert len(body["instalados"]) == 2
    assert body["instalados"][0] == {
        "nome": "gemma4:e2b",
        "tamanho_gb": 3.2,
        "parametros": "2.3B",
        "quantizacao": "Q4_K_M",
    }
    assert body["instalados"][1] == {
        "nome": "modelo-sem-detalhes:latest",
        "tamanho_gb": None,
        "parametros": None,
        "quantizacao": None,
    }


def test_recomendado_por_vram_prefere_instruct_e_maior_que_cabe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "user_settings.json")
    cfg.save_settings(cfg.UserSettings(ollama_model="modelo-inexistente:latest"))

    # VRAM livre = 6000 MB -> cabe 4.0 GB (4.0*1024*1.15 = 4710 MB), não cabe 9.0 GB
    monkeypatch.setattr(
        vm,
        "get_gpu_vram_status",
        lambda force_refresh=False: {"gpu_available": True, "vram_free_mb": 6000, "vram_total_mb": 6144},
    )
    tags_payload = {
        "models": [
            {"name": "tiny-base:1b", "size": int(1.0 * (1024**3)), "details": {}},
            {"name": "llama3:8b", "size": int(4.5 * (1024**3)), "details": {}},
            {"name": "qwen2.5:7b-instruct", "size": int(4.2 * (1024**3)), "details": {}},
            {"name": "giant:14b-instruct", "size": int(9.0 * (1024**3)), "details": {}},
        ]
    }
    monkeypatch.setattr(oc, "criar_cliente", lambda timeout: _FakeClient(tags_resp=_FakeResp(200, tags_payload)))

    res = oc.listar_modelos_ollama()
    assert res["online"] is True
    assert res["configurado_instalado"] is False
    # qwen2.5:7b-instruct cabe na VRAM medida e tem 'instruct' (preferido sobre llama3:8b base)
    assert res["recomendado"] == "qwen2.5:7b-instruct"


def test_recomendado_sem_medicao_vram_prefere_menor_instruct_ou_primeiro(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "user_settings.json")
    cfg.save_settings(cfg.UserSettings(ollama_model="auto"))
    monkeypatch.setattr(
        vm,
        "get_gpu_vram_status",
        lambda force_refresh=False: {"gpu_available": False, "vram_free_mb": None, "vram_total_mb": None},
    )
    tags_com_instruct = {
        "models": [
            {"name": "grande:14b-instruct", "size": int(9.0 * (1024**3)), "details": {}},
            {"name": "base:latest", "size": int(2.0 * (1024**3)), "details": {}},
            {"name": "pequeno:3b-instruct", "size": int(2.2 * (1024**3)), "details": {}},
        ]
    }
    monkeypatch.setattr(oc, "criar_cliente", lambda timeout: _FakeClient(tags_resp=_FakeResp(200, tags_com_instruct)))
    res = oc.listar_modelos_ollama()
    assert res["recomendado"] == "pequeno:3b-instruct"

    # Sem nenhum modelo instruct/it -> primeiro instalado
    tags_sem_instruct = {
        "models": [
            {"name": "primeiro:latest", "size": int(2.0 * (1024**3)), "details": {}},
            {"name": "segundo:latest", "size": int(4.0 * (1024**3)), "details": {}},
        ]
    }
    monkeypatch.setattr(oc, "criar_cliente", lambda timeout: _FakeClient(tags_resp=_FakeResp(200, tags_sem_instruct)))
    res2 = oc.listar_modelos_ollama()
    assert res2["recomendado"] == "primeiro:latest"


def test_listar_modelos_nenhum_instalado_e_offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "user_settings.json")
    cfg.save_settings(cfg.UserSettings(ollama_model="auto"))

    # Online sem modelos
    monkeypatch.setattr(oc, "criar_cliente", lambda timeout: _FakeClient(tags_resp=_FakeResp(200, {"models": []})))
    res_vazio = oc.listar_modelos_ollama()
    assert res_vazio["online"] is True
    assert res_vazio["instalados"] == []
    assert res_vazio["recomendado"] is None

    # Offline (erro de conexão)
    monkeypatch.setattr(
        oc, "criar_cliente", lambda timeout: _FakeClient(raise_on_get=ConnectionError("refused"))
    )
    res_off = oc.listar_modelos_ollama()
    assert res_off["online"] is False
    assert res_off["instalados"] == []
    assert res_off["recomendado"] is None


def test_geracao_com_modelo_ausente_usa_recomendado_e_registra_troca(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "user_settings.json")
    cfg.save_settings(cfg.UserSettings(ollama_model="gemma4:e2b"))
    monkeypatch.setattr(
        vm,
        "get_gpu_vram_status",
        lambda force_refresh=False: {"gpu_available": False, "vram_free_mb": None, "vram_total_mb": None},
    )

    modelos_chamados: List[str] = []

    def _fake_generate(url: str, payload: Dict[str, Any]):
        modelos_chamados.append(payload["model"])
        if payload["model"] == "gemma4:e2b":
            return _FakeResp(404, {"error": "model 'gemma4:e2b' not found"})
        return _FakeResp(200, {"response": "Texto redigido pelo modelo substituto."})

    tags_payload = {"models": [{"name": "qwen2.5:7b-instruct", "size": int(4.1 * (1024**3)), "details": {}}]}
    monkeypatch.setattr(
        oc,
        "criar_cliente",
        lambda timeout: _FakeClient(tags_resp=_FakeResp(200, tags_payload), generate_fn=_fake_generate),
    )

    texto = oc.gerar_texto("Teste de prompt")
    assert texto == "Texto redigido pelo modelo substituto."
    assert modelos_chamados == ["gemma4:e2b", "qwen2.5:7b-instruct"]

    ultima = vm._ULTIMA_DECISAO_OLLAMA
    assert ultima is not None
    assert ultima["modelo"] == "qwen2.5:7b-instruct"
    assert ultima["modelo_configurado"] == "gemma4:e2b"
    assert ultima["modelo_trocado"] is True


def test_pipeline_seccional_avisa_quando_ollama_sem_modelo_instalado(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "user_settings.json")
    cfg.save_settings(cfg.UserSettings(ollama_model="auto"))
    monkeypatch.setattr(oc, "criar_cliente", lambda timeout: _FakeClient(tags_resp=_FakeResp(200, {"models": []})))

    pipe = SectionalPipelineEngine(use_ollama=True)
    sentenca = pipe.executar_pipeline(get_demo_case_bundle(), use_ollama=True)
    tel = pipe.snapshot_telemetria()
    assert any("Ollama sem modelo instalado: redação pelo motor determinístico" in a for a in sentenca.alertas)
    assert tel.get("aviso_ollama") == "Ollama sem modelo instalado: redação pelo motor determinístico"


def test_modo_auto_estavel_quando_vram_livre_cai_entre_chamadas(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "user_settings.json")
    cfg.save_settings(cfg.UserSettings(ollama_model="auto"))
    oc.invalidar_cache_modelo_auto()

    estado_vram = {"vram_free_mb": 5800, "vram_total_mb": 6144}
    monkeypatch.setattr(
        vm,
        "get_gpu_vram_status",
        lambda force_refresh=False: {"gpu_available": True, **estado_vram},
    )

    tags_payload = {
        "models": [
            {"name": "tiny:1b-instruct", "size": int(1.0 * (1024**3)), "details": {}},
            {"name": "qwen2.5:7b-instruct", "size": int(4.2 * (1024**3)), "details": {}},
            {"name": "giant:14b-instruct", "size": int(9.0 * (1024**3)), "details": {}},
        ]
    }
    modelos_enviados: List[str] = []
    chamadas_tags = {"n": 0}

    class _CountingClient(_FakeClient):
        def get(self, url: str):
            chamadas_tags["n"] += 1
            return super().get(url)

    def _fake_generate(url: str, payload: Dict[str, Any]):
        modelos_enviados.append(payload["model"])
        # Simula que o modelo carregado consumiu quase toda a VRAM livre
        estado_vram["vram_free_mb"] = 900
        return _FakeResp(200, {"response": "Resposta gerada."})

    monkeypatch.setattr(
        oc,
        "criar_cliente",
        lambda timeout: _CountingClient(tags_resp=_FakeResp(200, tags_payload), generate_fn=_fake_generate),
    )

    r1 = oc.gerar_texto("Seção 1")
    r2 = oc.gerar_texto("Seção 2")
    assert r1 == "Resposta gerada."
    assert r2 == "Resposta gerada."
    assert modelos_enviados == ["qwen2.5:7b-instruct", "qwen2.5:7b-instruct"]
    # Cache curto evita reconsultar /api/tags na 2ª chamada
    assert chamadas_tags["n"] == 1

    # Mesmo invalidando o cache, _escolher_recomendado usa VRAM total e não expulsa o modelo em uso
    oc.invalidar_cache_modelo_auto()
    res_lista = oc.listar_modelos_ollama()
    assert res_lista["recomendado"] == "qwen2.5:7b-instruct"


def test_pipeline_seccional_rotulo_igual_ao_modelo_enviado_em_generate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from app.generation.sectional_pipeline import ResultadoGating

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "user_settings.json")
    cfg.save_settings(cfg.UserSettings(ollama_model="auto"))
    oc.invalidar_cache_modelo_auto()

    estado_vram = {"vram_free_mb": 5800, "vram_total_mb": 6144}
    monkeypatch.setattr(
        vm,
        "get_gpu_vram_status",
        lambda force_refresh=False: {"gpu_available": True, **estado_vram},
    )

    tags_payload = {
        "models": [
            {"name": "tiny:1b-instruct", "size": int(1.0 * (1024**3)), "details": {}},
            {"name": "qwen2.5:7b-instruct", "size": int(4.2 * (1024**3)), "details": {}},
        ]
    }
    modelos_enviados: List[str] = []

    def _fake_generate(url: str, payload: Dict[str, Any]):
        modelos_enviados.append(payload["model"])
        estado_vram["vram_free_mb"] = 500
        return _FakeResp(200, {"response": "Texto aprovado pelo gating simulado."})

    monkeypatch.setattr(
        oc,
        "criar_cliente",
        lambda timeout: _FakeClient(tags_resp=_FakeResp(200, tags_payload), generate_fn=_fake_generate),
    )

    pipe = SectionalPipelineEngine(
        use_ollama=True,
        gating_validator=lambda secao, rascunho, dossie: ResultadoGating(
            secao=secao, aprovado=True, score=100.0, erros=[]
        ),
    )
    sentenca = pipe.executar_pipeline(
        get_demo_case_bundle(),
        use_ollama=True,
        secoes_habilitadas=["relatorio", "merito"],
    )
    tel = pipe.snapshot_telemetria()

    assert modelos_enviados == ["qwen2.5:7b-instruct", "qwen2.5:7b-instruct"]
    assert tel["modelo_ollama_usado"] == "qwen2.5:7b-instruct"
    assert "Ollama (qwen2.5:7b-instruct)" in sentenca.motor_utilizado

