# -*- coding: utf-8 -*-
"""Testes da política de VRAM entre Whisper e Ollama (TASK-014 / Fase 4)."""

from __future__ import annotations

import json
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

from fastapi.testclient import TestClient

from app.core import ollama_client, vram_manager
from app.core.vram_manager import (
    MARGEM_SEGURANCA_VRAM,
    avaliar_whisper_vram,
    decidir_dispositivo_whisper,
    requisitos_whisper_mb,
    sugerir_quantizacao_ollama,
)


def _gpu_mock(
    *,
    available: bool = True,
    total_mb: int | None = 6144,
    free_mb: int | None = 5800,
    backend: str = "cuda",
) -> Dict[str, Any]:
    used = (total_mb - free_mb) if (total_mb is not None and free_mb is not None) else None
    return {
        "gpu_available": available,
        "backend": backend,
        "gpu_name": "NVIDIA GeForce RTX 2060" if available else None,
        "vram_used_mb": used,
        "vram_total_mb": total_mb if available else None,
        "vram_free_mb": free_mb if available else None,
        "vram_percent": round((used / total_mb) * 100, 1) if (used is not None and total_mb) else None,
        "gpu_utilization_percent": 0 if available else None,
        "fonte": "nvidia-smi" if available else None,
        "detalhe": "Medido via nvidia-smi." if available else "GPU indisponível.",
    }


def test_decidir_dispositivo_whisper_faixas_de_vram_e_margem_15():
    # 6 GB livres -> float16 inclusive para large-v3 (4500 * 1.15 = 5175 <= 6000)
    assert decidir_dispositivo_whisper(6000, "small") == ("cuda", "float16")
    assert decidir_dispositivo_whisper(6000, "medium") == ("cuda", "float16")
    assert decidir_dispositivo_whisper(6000, "large-v3") == ("cuda", "float16")

    # 2 GB livres -> small cabe em float16 (1725), mas medium/large caem para CPU
    assert decidir_dispositivo_whisper(2000, "small") == ("cuda", "float16")
    assert decidir_dispositivo_whisper(2000, "medium") == ("cpu", "int8")
    assert decidir_dispositivo_whisper(2000, "large-v3") == ("cpu", "int8")

    # Entre req int8_float16 e req float16 -> int8_float16
    _, req_fp16, req_int8 = requisitos_whisper_mb("small")
    assert req_fp16 == int(-(-1500 * MARGEM_SEGURANCA_VRAM // 1))
    assert req_int8 == int(-(-1000 * MARGEM_SEGURANCA_VRAM // 1))
    assert decidir_dispositivo_whisper(req_int8, "small") == ("cuda", "int8_float16")
    assert decidir_dispositivo_whisper(req_int8 - 1, "small") == ("cpu", "int8")

    # Sem medição ou zero -> CPU int8
    assert decidir_dispositivo_whisper(None, "small") == ("cpu", "int8")
    assert decidir_dispositivo_whisper(0, "tiny") == ("cpu", "int8")


def test_avaliar_whisper_vram_cuda_forcado_sem_vram_gera_alerta(caplog, monkeypatch):
    monkeypatch.setattr(vram_manager, "IS_MACOS", False)
    hw_pouca = _gpu_mock(available=True, total_mb=6144, free_mb=800)
    with caplog.at_level(logging.WARNING):
        res = avaliar_whisper_vram("cuda", "large-v3", gpu_status=hw_pouca)
    assert res["device"] == "cuda"
    assert res["compute_type"] == "int8_float16"
    assert res["alerta"] is not None and "insuficiente" in res["alerta"].lower()
    assert any("insuficiente" in rec.message.lower() for rec in caplog.records)


def test_avaliar_whisper_vram_cpu_explicito_e_medicao_indisponivel():
    res_cpu = avaliar_whisper_vram("cpu", "large-v3", gpu_status=_gpu_mock(available=True, free_mb=6000))
    assert res_cpu["device"] == "cpu"
    assert res_cpu["compute_type"] == "int8"
    assert res_cpu["alerta"] is None

    hw_sem_gpu = _gpu_mock(available=False, total_mb=None, free_mb=None, backend="cpu")
    res_auto = avaliar_whisper_vram("auto", "small", gpu_status=hw_sem_gpu)
    assert res_auto["device"] == "cpu"
    assert res_auto["compute_type"] == "int8"
    assert res_auto["vram_medida"] is False


def test_sequencia_descarregar_medir_decidir_e_cpu_intocado(tmp_path: Path, monkeypatch):
    from app.transcription import audio_transcriber as at

    monkeypatch.setitem(sys.modules, "faster_whisper", types.ModuleType("faster_whisper"))
    monkeypatch.setattr(at, "IS_MACOS", False)
    monkeypatch.setattr(vram_manager, "IS_MACOS", False)
    monkeypatch.setattr(at, "_CUDA_FALHOU", False)
    monkeypatch.setattr(at, "_ISOLAR_CUDA", True)
    monkeypatch.setattr(at, "_cuda_disponivel", lambda: True)

    eventos: List[str] = []

    def fake_release() -> bool:
        eventos.append("release_ollama")
        return True

    def fake_gpu() -> Dict[str, Any]:
        eventos.append("medir_vram")
        return _gpu_mock(available=True, total_mb=6144, free_mb=5500)

    def fake_isolado(media_path, modelo, dev, ctype, prompt):
        eventos.append(f"worker:{dev}:{ctype}")
        return [{"timestamp": "00:01", "texto_literal": "fala teste"}], 2.0

    monkeypatch.setattr(at, "release_ollama_vram_for_whisper", fake_release)
    monkeypatch.setattr(at, "get_gpu_vram_status", fake_gpu)
    monkeypatch.setattr(at, "_executar_whisper_isolado", fake_isolado)

    midia = tmp_path / "audiencia.wav"
    midia.write_bytes(b"RIFF0000")
    settings_auto = SimpleNamespace(whisper_model="small", whisper_device="auto")
    res = at._transcrever_whisper_local(midia, settings_auto, "prompt")
    assert res["engine"] == "faster-whisper small (cuda/float16)"
    assert eventos == ["release_ollama", "medir_vram", "worker:cuda:float16"]

    # Quando whisper_device="cpu": não chama release_ollama nem medir_vram
    eventos.clear()
    monkeypatch.setattr(
        at,
        "_executar_whisper",
        lambda mp, m, dev, ctype, pr: ([{"timestamp": "00:01", "texto_literal": "cpu"}], 1.0),
    )
    settings_cpu = SimpleNamespace(whisper_model="small", whisper_device="cpu")
    res_cpu = at._transcrever_whisper_local(midia, settings_cpu, "prompt")
    assert res_cpu["engine"] == "faster-whisper small (cpu/int8)"
    assert eventos == []


def test_whisper_worker_encerra_e_libera_cache(tmp_path: Path, monkeypatch):
    from app.transcription import audio_transcriber as at
    from app.transcription import whisper_worker

    descarregou = {"chamado": False}

    monkeypatch.setattr(
        at,
        "_executar_whisper",
        lambda mp, m, dev, ctype, pr: ([{"timestamp": "00:02", "texto_literal": "ok"}], 3.5),
    )
    monkeypatch.setattr(at, "descarregar_modelo_whisper", lambda: descarregou.__setitem__("chamado", True))

    entrada = tmp_path / "in.json"
    saida = tmp_path / "out.json"
    entrada.write_text(
        json.dumps(
            {
                "media": str(tmp_path / "audio.wav"),
                "modelo": "small",
                "device": "cuda",
                "compute_type": "float16",
                "prompt": "",
            }
        ),
        encoding="utf-8",
    )
    rc = whisper_worker.main(["whisper_worker", str(entrada), str(saida)])
    assert rc == 0
    assert descarregou["chamado"] is True
    dados = json.loads(saida.read_text(encoding="utf-8"))
    assert dados["duracao"] == 3.5 and len(dados["segmentos"]) == 1


def test_ollama_gerar_texto_passa_num_gpu_e_num_ctx_pela_vram(monkeypatch):
    capturado: Dict[str, Any] = {}

    class _FakeResp:
        status_code = 200

        def json(self):
            return {"response": "texto gerado"}

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None):
            capturado["payload"] = json
            return _FakeResp()

    fake_settings = SimpleNamespace(
        ollama_url="http://127.0.0.1:11434",
        ollama_model="gemma4:e2b",
        ollama_num_ctx=8192,
        permitir_envio_externo=False,
    )
    monkeypatch.setattr(ollama_client, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(ollama_client, "criar_cliente", lambda timeout: _FakeClient())

    # 1) 6 GB livres -> todas as camadas na GPU (99) e num_ctx cheio (8192)
    monkeypatch.setattr(vram_manager, "get_gpu_vram_status", lambda: _gpu_mock(available=True, total_mb=6144, free_mb=5800))
    assert ollama_client.gerar_texto("oi") == "texto gerado"
    assert capturado["payload"]["options"]["num_gpu"] == 99
    assert capturado["payload"]["options"]["num_ctx"] == 8192

    # 2) 2 GB livres -> offloading parcial e num_ctx conservador (2048)
    monkeypatch.setattr(vram_manager, "get_gpu_vram_status", lambda: _gpu_mock(available=True, total_mb=6144, free_mb=2000))
    assert ollama_client.gerar_texto("oi") == "texto gerado"
    assert 8 <= capturado["payload"]["options"]["num_gpu"] <= 32
    assert capturado["payload"]["options"]["num_ctx"] == 2048

    # 3) VRAM medida muito baixa (< 1200 MiB) -> num_gpu=0 (CPU)
    monkeypatch.setattr(vram_manager, "get_gpu_vram_status", lambda: _gpu_mock(available=True, total_mb=6144, free_mb=600))
    assert ollama_client.gerar_texto("oi") == "texto gerado"
    assert capturado["payload"]["options"]["num_gpu"] == 0
    assert capturado["payload"]["options"]["num_ctx"] == 2048

    # 4) Sem medição disponível (sem nvidia-smi / AMD / CPU) -> NÃO envia num_gpu (alocação automática do Ollama)
    monkeypatch.setattr(vram_manager, "get_gpu_vram_status", lambda: _gpu_mock(available=False, total_mb=None, free_mb=None, backend="cpu"))
    assert ollama_client.gerar_texto("oi") == "texto gerado"
    assert "num_gpu" not in capturado["payload"]["options"]


def test_cache_medicao_gpu_10s(monkeypatch):
    vram_manager.limpar_cache_vram()
    chamadas = {"n": 0}

    def fake_get_gpu():
        chamadas["n"] += 1
        return _gpu_mock(available=True, total_mb=6144, free_mb=5000)

    monkeypatch.setattr(vram_manager, "get_gpu_status", fake_get_gpu)
    r1 = vram_manager.get_gpu_vram_status()
    r2 = vram_manager.get_gpu_vram_status()
    assert r1["vram_free_mb"] == 5000 and r2["vram_free_mb"] == 5000
    assert chamadas["n"] == 1
    vram_manager.get_gpu_vram_status(forcar_medicao=True)
    assert chamadas["n"] == 2
    vram_manager.limpar_cache_vram()


def test_sugestao_quantizacao_q4_k_m_e_telemetria_api_status(monkeypatch):
    # <= 6 GB sem q4/q5 -> sugere Q4_K_M
    sug = sugerir_quantizacao_ollama(6144, "gemma4:e2b")
    assert sug is not None and "Q4_K_M" in sug
    # Já quantizado -> sem sugestão
    assert sugerir_quantizacao_ollama(6144, "llama3:8b-q4_K_M") is None
    assert sugerir_quantizacao_ollama(6144, "qwen2.5:7b-q5_K_M") is None
    # GPU > 6 GB -> sem sugestão
    assert sugerir_quantizacao_ollama(8192, "gemma4:e2b") is None
    # Medição indisponível -> sem sugestão
    assert sugerir_quantizacao_ollama(None, "gemma4:e2b") is None

    # /api/status inclui politica_vram completa
    from app import main as main_mod

    monkeypatch.setattr(main_mod, "get_gpu_status", lambda: _gpu_mock(available=True, total_mb=6144, free_mb=5200))
    monkeypatch.setattr(main_mod, "ollama_disponivel", lambda: {"online": False, "installed_models": []})
    client = TestClient(main_mod.app)
    resp = client.get("/api/status", headers={main_mod.TOKEN_HEADER: main_mod.get_session_token()})
    assert resp.status_code == 200
    body = resp.json()
    pol = body["politica_vram"]
    assert pol["vram_medida"] is True
    assert pol["vram_livre_mb"] == 5200
    assert pol["whisper"]["device"] in ("cuda", "cpu")
    assert pol["whisper"]["compute_type"] in ("float16", "int8_float16", "int8")
    assert pol["whisper"]["motivo"]
    assert pol["ollama"]["num_gpu"] == 99
    assert pol["ollama"]["motivo"]
