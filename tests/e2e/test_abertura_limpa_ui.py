# -*- coding: utf-8 -*-
"""E2E da TASK-031: abertura limpa com demo persistida e reabertura automática de caso real."""

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

from app.core.limpeza_demo import ULTIMO_CASO_FILENAME
from app.demo.demo_case import get_demo_case_bundle
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


def _subir_servidor_isolado(data_dir: Path) -> tuple[subprocess.Popen, str]:
    porta = _porta_livre()
    env = os.environ.copy()
    env["JVDFM_DATA_DIR"] = str(data_dir)
    env["JVDFM_PORT"] = str(porta)
    env["JVDFM_SECRETS_BACKEND"] = "arquivo"
    env.pop("JVDFM_DISABLE_TOKEN", None)
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(porta),
        "--log-level",
        "warning",
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


def test_e2e_abertura_limpa_com_demo_persistida_e_reabertura_caso_real(tmp_path: Path):
    """Critério de aceite: demo persistida -> abre limpo; caso real persistido -> reabre esse caso."""
    # Cenário A: demo persistida em .ultimo_caso_real.json -> DEVE abrir limpo
    data_demo = tmp_path / "data_demo"
    (data_demo / "cases").mkdir(parents=True, exist_ok=True)
    (data_demo / ULTIMO_CASO_FILENAME).write_text(
        json.dumps({"versao": 1, "bundle": get_demo_case_bundle()}),
        encoding="utf-8",
    )

    proc_a, url_a = _subir_servidor_isolado(data_demo)
    try:
        with sync_playwright() as p:
            browser = _launch_chromium(p)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(url_a, wait_until="networkidle")
            page.wait_for_function(
                "() => document.getElementById('heroCaseNumber')?.textContent?.trim() === 'Sem autos carregados'",
                timeout=10000,
            )
            hero_txt = page.locator("#heroCaseNumber").inner_text()
            assert hero_txt.strip() == "Sem autos carregados"
            assert "demonstração" not in hero_txt.lower()
            assert "simulação" not in page.locator("#heroTotalPenalty").inner_text().lower()
            assert page.locator("#toastContainer .toast").count() == 0
            browser.close()
    finally:
        proc_a.terminate()
        try:
            proc_a.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc_a.kill()

    # Cenário B: caso REAL (fictício) persistido em .ultimo_caso_real.json -> DEVE reabrir esse caso
    data_real = tmp_path / "data_real"
    (data_real / "cases").mkdir(parents=True, exist_ok=True)
    bundle_real = {
        "case_id": "REAL-0807111-11.2025.8.20.0001",
        "is_demo": False,
        "numero_processo": "0807111-11.2025.8.20.0001",
        "ofendida": "ANA TESTE E2E",
        "reu": "CARLOS TESTE E2E",
        "raw_documents": [
            {
                "id": "ID-700001",
                "title": "Denúncia",
                "text": "Denúncia fictícia contra CARLOS TESTE E2E por ameaça (art. 147 do CP).",
                "date": "12/04/2025",
            }
        ],
        "transcriptions": [],
    }
    (data_real / ULTIMO_CASO_FILENAME).write_text(
        json.dumps({"versao": 1, "bundle": bundle_real}),
        encoding="utf-8",
    )

    proc_b, url_b = _subir_servidor_isolado(data_real)
    try:
        with sync_playwright() as p:
            browser = _launch_chromium(p)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(url_b, wait_until="networkidle")
            page.wait_for_function(
                "() => (document.getElementById('heroCaseNumber')?.textContent || '').includes('0807111-11.2025.8.20.0001')",
                timeout=10000,
            )
            hero_txt = page.locator("#heroCaseNumber").inner_text()
            assert "0807111-11.2025.8.20.0001" in hero_txt
            assert "demonstração" not in hero_txt.lower()
            browser.close()
    finally:
        proc_b.terminate()
        try:
            proc_b.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc_b.kill()
