# -*- coding: utf-8 -*-
"""E2E Playwright do Assistente de Primeira Execução (TASK-033): Ollama e OCR simulados (presente/ausente)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright

from tests.e2e.test_layout_transbordo import _launch_chromium


def _porta_livre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _aguardar_health(url: str, timeout: float = 25.0) -> None:
    fim = time.monotonic() + timeout
    while time.monotonic() < fim:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=1.5) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"Servidor E2E não respondeu em {url}")


def _subir_servidor_limpo(data_dir: Path) -> tuple[subprocess.Popen, str]:
    porta = _porta_livre()
    env = os.environ.copy()
    env["SENTENCIA_DATA_DIR"] = str(data_dir)
    env["SENTENCIA_PORT"] = str(porta)
    env["SENTENCIA_SECRETS_BACKEND"] = "arquivo"
    env.pop("SENTENCIA_DISABLE_TOKEN", None)
    cmd = [
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", str(porta), "--log-level", "warning",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(Path(__file__).resolve().parent.parent.parent),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{porta}"
    try:
        _aguardar_health(url)
    except Exception:
        proc.terminate()
        proc.wait(timeout=10)
        raise
    return proc, url


def test_e2e_assistente_ollama_e_ocr_simulados_e_conclusao(tmp_path: Path):
    """Critério de aceite: Assistente abre na 1ª execução, trata Ollama/OCR ausentes e presentes, conclui e reabre pelo menu."""
    data_dir = tmp_path / "perfil_limpo"
    data_dir.mkdir(parents=True, exist_ok=True)
    proc, url = _subir_servidor_limpo(data_dir)

    estado_mock = {"modo": "ausente"}

    def _interceptar_diag(route):
        if estado_mock["modo"] == "ausente":
            payload = {
                "assistente_concluido": False,
                "plataforma": "win32",
                "gpu": {"gpu_available": False, "gpu_name": None, "vram_total_mb": None},
                "ollama": {
                    "instalado": False,
                    "online": False,
                    "link_download": "https://ollama.com/download/windows",
                    "modelo_sugerido": "gemma4:e2b",
                    "motivo_sugestao": "Modelo compacto para CPU.",
                    "modelos_instalados": [],
                    "modelo_sugerido_instalado": False,
                },
                "ocr": {
                    "plataforma": "windows",
                    "motor_instalado": True,
                    "idioma_pt_br": False,
                    "disponivel": False,
                    "comando_admin": 'Add-WindowsCapability -Online -Name "Language.OCR~~~pt-BR~0.0.1.0"',
                    "passos": ["Execute no PowerShell como Administrador."],
                },
                "pull": {"ativo": False, "status": "idle", "modelo": "", "percent": 0, "mensagem": "", "erro": None},
            }
        else:
            payload = {
                "assistente_concluido": False,
                "plataforma": "win32",
                "gpu": {"gpu_available": True, "gpu_name": "GPU 6GB", "vram_total_mb": 6144},
                "ollama": {
                    "instalado": True,
                    "online": True,
                    "link_download": "https://ollama.com/download/windows",
                    "modelo_sugerido": "gemma3:4b",
                    "motivo_sugestao": "GPU com 6 GB de VRAM.",
                    "modelos_instalados": [],
                    "modelo_sugerido_instalado": False,
                },
                "ocr": {
                    "plataforma": "windows",
                    "motor_instalado": True,
                    "idioma_pt_br": True,
                    "disponivel": True,
                    "comando_admin": "",
                    "passos": [],
                },
                "pull": {"ativo": False, "status": "idle", "modelo": "", "percent": 0, "mensagem": "", "erro": None},
            }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    try:
        with sync_playwright() as p:
            browser = _launch_chromium(p)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.route("**/api/assistente/diagnostico", _interceptar_diag)

            # 1) Primeira abertura: modal abre sozinho com Ollama e OCR ausentes
            page.goto(url, wait_until="networkidle")
            page.wait_for_selector("#modalAssistente", state="visible", timeout=10000)

            ollama_txt = page.locator("#assistenteOllamaBox").inner_text()
            assert "Ollama não detectado" in ollama_txt
            assert "motor determinístico" in ollama_txt
            assert page.locator("#btnAssistentePullOllama").is_hidden()

            ocr_txt = page.locator("#assistenteOcrBox").inner_text()
            assert "Language.OCR~~~pt-BR~0.0.1.0" in ocr_txt
            assert page.locator("#linkAssistenteGuiaUsuario").get_attribute("href") == "/guia-usuario"

            # 2) Simula Ollama online (6 GB -> gemma3:4b) e OCR pt-BR pronto
            estado_mock["modo"] = "presente"
            page.click("#btnAssistenteRefresh")
            page.wait_for_function(
                "() => (document.getElementById('assistenteOllamaBox')?.textContent || '').includes('gemma3:4b')",
                timeout=5000,
            )
            assert page.locator("#btnAssistentePullOllama").is_visible()
            assert "Baixar gemma3:4b" in page.locator("#btnAssistentePullOllama").inner_text()
            assert "PRONTO" in page.locator("#assistenteOcrBox").inner_text()

            # 3) Concluir assistente -> recarregar NÃO abre sozinho -> menu do logo reabre
            page.click("#btnAssistenteConcluir")
            page.wait_for_selector("#modalAssistente", state="hidden", timeout=5000)

            page.reload(wait_until="networkidle")
            page.wait_for_timeout(400)
            assert page.locator("#modalAssistente").is_hidden()

            page.click("#btnAppMenu")
            page.click("#btnMenuAssistente")
            page.wait_for_selector("#modalAssistente", state="visible", timeout=5000)
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
