# -*- coding: utf-8 -*-
"""TASK-046: ao terminar o download do modelo Ollama, o aviso "baixado com sucesso" aparece UMA vez.

Antes, cada recarga do diagnóstico devolvia o mesmo status "completed" e gerava outro aviso, sem fim.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright

from tests.e2e.test_assistente_ui import _subir_servidor_limpo
from tests.e2e.test_layout_transbordo import _launch_chromium

_PULL_CONCLUIDO = {"ativo": False, "status": "completed", "modelo": "gemma3:4b", "percent": 100, "mensagem": "Concluído", "erro": None}


def _diag(pull: dict) -> dict:
    return {
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
        "ocr": {"plataforma": "windows", "motor_instalado": True, "idioma_pt_br": True, "disponivel": True, "comando_admin": "", "passos": []},
        "pull": pull,
    }


def test_task046_aviso_de_download_concluido_aparece_uma_unica_vez(tmp_path: Path):
    data_dir = tmp_path / "perfil_limpo"
    data_dir.mkdir(parents=True, exist_ok=True)
    proc, url = _subir_servidor_limpo(data_dir)
    estado = {"pull": {"ativo": False, "status": "idle", "modelo": "", "percent": 0, "mensagem": "", "erro": None}}

    def _diag_route(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(_diag(estado["pull"])))

    def _pull_route(route):
        estado["pull"] = dict(_PULL_CONCLUIDO)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(estado["pull"]))

    def _status_route(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(estado["pull"]))

    try:
        with sync_playwright() as p:
            browser = _launch_chromium(p)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.route("**/api/assistente/diagnostico", _diag_route)
            page.route("**/api/assistente/ollama/pull", _pull_route)
            page.route("**/api/assistente/ollama/pull-status", _status_route)
            page.goto(url, wait_until="networkidle")
            page.wait_for_selector("#btnAssistentePullOllama", state="visible", timeout=10000)

            page.click("#btnAssistentePullOllama")
            page.wait_for_timeout(3000)
            avisos = page.locator(".toast-item", has_text="baixado com sucesso")
            assert avisos.count() == 1

            # Recarregar o diagnóstico (mesmo status "completed" no servidor) não repete o aviso.
            page.click("#btnAssistenteRefresh")
            page.wait_for_timeout(2000)
            assert page.locator(".toast-item", has_text="baixado com sucesso").count() <= 1
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
