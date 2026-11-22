# -*- coding: utf-8 -*-
"""Testes do Modo GPU moderado: fila única, 70% VRAM, num_ctx, Whisper e regressão do normal (TASK-029)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from app.api.security import TOKEN_HEADER, get_session_token
from app.core import config as cfg
from app.core import ollama_client as oc
from app.core import vram_manager as vm
from app.main import app


class _FakeResp:
    def __init__(self, status_code: int, payload: Dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, *, tags_resp=None, generate_fn=None) -> None:
        self._tags_resp = tags_resp
        self._generate_fn = generate_fn

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url: str):
        return self._tags_resp

    def post(self, url: str, json: Dict[str, Any]):
        if self._generate_fn:
            return self._generate_fn(url, json)
        return _FakeResp(200, {"response": "ok"})


def _headers_auth() -> Dict[str, str]:
    return {TOKEN_HEADER: get_session_token()}


def test_padrao_modo_gpu_eh_normal_e_aceita_moderado(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    assert cfg.UserSettings().modo_gpu == "normal"
    assert cfg.UserSettings(modo_gpu="moderado").modo_gpu == "moderado"

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "user_settings.json")
    client = TestClient(app)
    resp = client.post("/api/settings", json={"modo_gpu": "moderado"}, headers=_headers_auth())
    assert resp.status_code == 200
    assert resp.json()["settings"]["modo_gpu"] == "moderado"

    resp_inv = client.post("/api/settings", json={"modo_gpu": "turbo_invalido"}, headers=_headers_auth())
    assert resp_inv.status_code == 422


def test_modo_moderado_serializa_tarefas_gpu_com_threads_e_reporta_fila(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "user_settings.json")
    cfg.save_settings(cfg.UserSettings(modo_gpu="moderado"))

    ativas = {"atual": 0, "max": 0}
    lock_cont = threading.Lock()
    t1_entrou = threading.Event()
    t2_aguardou_visto = {"ok": False, "msg": None}
    liberar_t1 = threading.Event()

    def _tarefa1():
        with vm.reservar_gpu("ollama", modo_gpu="moderado"):
            with lock_cont:
                ativas["atual"] += 1
                ativas["max"] = max(ativas["max"], ativas["atual"])
            t1_entrou.set()
            liberar_t1.wait(timeout=5)
            with lock_cont:
                ativas["atual"] -= 1

    def _tarefa2():
        t1_entrou.wait(timeout=5)
        with vm.reservar_gpu("whisper", modo_gpu="moderado"):
            with lock_cont:
                ativas["atual"] += 1
                ativas["max"] = max(ativas["max"], ativas["atual"])
            time.sleep(0.03)
            with lock_cont:
                ativas["atual"] -= 1

    th1 = threading.Thread(target=_tarefa1)
    th2 = threading.Thread(target=_tarefa2)
    th1.start()
    th2.start()

    # Aguarda até que a thread 2 esteja na fila esperando a placa de vídeo
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        fila = vm.obter_estado_fila_gpu()
        if fila["aguardando"] >= 1:
            t2_aguardou_visto["ok"] = True
            t2_aguardou_visto["msg"] = fila["mensagem"]
            break
        time.sleep(0.01)

    # Verifica também na telemetria /api/status enquanto há fila
    client = TestClient(app)
    st = client.get("/api/status", headers=_headers_auth()).json()
    fila_api = st["politica_vram"]["fila_gpu"]
    assert fila_api["aguardando"] >= 1
    assert fila_api["mensagem"] == "aguardando a placa de vídeo"

    liberar_t1.set()
    th1.join(timeout=5)
    th2.join(timeout=5)

    assert t2_aguardou_visto["ok"] is True
    assert t2_aguardou_visto["msg"] == "aguardando a placa de vídeo"
    assert ativas["max"] == 1, f"Nunca pode haver duas tarefas GPU simultâneas: {ativas}"
    assert vm.obter_estado_fila_gpu()["aguardando"] == 0


def test_modo_moderado_limita_num_ctx_e_num_gpu_em_70_porcento_vram_6144():
    hw_rtx2060 = {
        "gpu_available": True,
        "backend": "cuda",
        "vram_total_mb": 6144,
        "vram_free_mb": 5900,
    }
    # Modelo 8b (~5200 * 1.15 = 5980 MiB): no normal quase cabe (5900 < 5980 -> 31 camadas);
    # no moderado, o teto é 70% de 6144 = 4300 MiB -> camadas <= int((4300/5980)*32) = 23 e num_ctx <= 4096.
    pol_mod = vm.calcular_opcoes_ollama(
        gpu_status=hw_rtx2060,
        modelo="llama3:8b",
        num_ctx_padrao=8192,
        modo_gpu="moderado",
    )
    assert pol_mod["num_ctx"] <= 4096
    assert pol_mod["num_gpu"] is not None and 4 <= pol_mod["num_gpu"] <= 24
    assert pol_mod["keep_alive"] == "2m"
    assert pol_mod["modo_gpu"] == "moderado"

    # Regressão: no modo normal, modelo que cabe (e2b: 2760 MiB <= 5900 MiB) mantém num_ctx=8192, num_gpu=99 e sem keep_alive
    pol_norm = vm.calcular_opcoes_ollama(
        gpu_status=hw_rtx2060,
        modelo="gemma4:e2b",
        num_ctx_padrao=8192,
        modo_gpu="normal",
    )
    assert pol_norm["num_ctx"] == 8192
    assert pol_norm["num_gpu"] == 99
    assert pol_norm.get("keep_alive") is None


def test_modo_moderado_selecao_auto_usa_70_porcento_vram(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "user_settings.json")
    monkeypatch.setattr(
        vm,
        "get_gpu_vram_status",
        lambda force_refresh=False, forcar_medicao=False: {
            "gpu_available": True,
            "vram_free_mb": 5800,
            "vram_total_mb": 6144,
        },
    )
    tags_payload = {
        "models": [
            {"name": "qwen2.5:3b-instruct", "size": int(2.5 * (1024**3)), "details": {}},  # ~2944 MB (cabe em 70% = 4300)
            {"name": "qwen2.5:7b-instruct", "size": int(4.2 * (1024**3)), "details": {}},  # ~4946 MB (cabe em 100%, NÃO em 70%)
        ]
    }
    monkeypatch.setattr(oc, "criar_cliente", lambda timeout: _FakeClient(tags_resp=_FakeResp(200, tags_payload)))

    cfg.save_settings(cfg.UserSettings(ollama_model="auto", modo_gpu="normal"))
    assert oc.listar_modelos_ollama()["recomendado"] == "qwen2.5:7b-instruct"

    cfg.save_settings(cfg.UserSettings(ollama_model="auto", modo_gpu="moderado"))
    assert oc.listar_modelos_ollama()["recomendado"] == "qwen2.5:3b-instruct"


def test_modo_moderado_whisper_usa_int8_float16_e_beam_size_menor(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vm, "IS_MACOS", False)
    hw = {"gpu_available": True, "backend": "cuda", "vram_total_mb": 6144, "vram_free_mb": 5500}
    dec_norm = vm.avaliar_whisper_vram("auto", "small", gpu_status=hw, modo_gpu="normal")
    assert dec_norm["device"] == "cuda"
    assert dec_norm["compute_type"] == "float16"
    assert dec_norm.get("beam_size") is None

    dec_mod = vm.avaliar_whisper_vram("auto", "small", gpu_status=hw, modo_gpu="moderado")
    assert dec_mod["device"] == "cuda"
    assert dec_mod["compute_type"] == "int8_float16"
    assert dec_mod.get("beam_size") == 2


def test_modo_normal_vs_moderado_payload_ollama(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "user_settings.json")
    monkeypatch.setattr(
        vm,
        "get_gpu_vram_status",
        lambda force_refresh=False, forcar_medicao=False: {
            "gpu_available": True,
            "backend": "cuda",
            "vram_free_mb": 5800,
            "vram_total_mb": 6144,
        },
    )
    payloads: List[Dict[str, Any]] = []

    def _cap(url: str, payload: Dict[str, Any]):
        payloads.append(payload)
        return _FakeResp(200, {"response": "Texto ok."})

    monkeypatch.setattr(oc, "criar_cliente", lambda timeout: _FakeClient(generate_fn=_cap))

    # Modo normal: sem keep_alive e num_ctx=8192 (idêntico ao comportamento atual)
    cfg.save_settings(cfg.UserSettings(ollama_model="gemma4:e2b", ollama_num_ctx=8192, modo_gpu="normal"))
    assert oc.gerar_texto("Prompt normal") == "Texto ok."
    assert "keep_alive" not in payloads[-1]
    assert payloads[-1]["options"]["num_ctx"] == 8192

    # Modo moderado: keep_alive="2m" e num_ctx <= 4096
    cfg.save_settings(cfg.UserSettings(ollama_model="gemma4:e2b", ollama_num_ctx=8192, modo_gpu="moderado"))
    assert oc.gerar_texto("Prompt moderado") == "Texto ok."
    assert payloads[-1].get("keep_alive") == "2m"
    assert payloads[-1]["options"]["num_ctx"] <= 4096
