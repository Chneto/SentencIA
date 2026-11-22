# -*- coding: utf-8 -*-
"""Testes E2E do Modo GPU moderado nas Configurações e Telemetria (TASK-029)."""

from __future__ import annotations

import json

from tests.e2e.test_layout_transbordo import _LocalAppServer, _launch_chromium


def test_modo_gpu_configuracoes_e_telemetria_fila(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            page = context.new_page()

            simular_fila = {"ativo": False}

            def _intercept_status(route):
                resp = route.fetch()
                data = resp.json()
                if simular_fila["ativo"]:
                    pol = data.setdefault("politica_vram", {})
                    pol["modo_gpu"] = "moderado"
                    pol["fila_gpu"] = {
                        "ativa": "ollama",
                        "aguardando": 1,
                        "mensagem": "aguardando a placa de vídeo",
                    }
                    ol = pol.setdefault("ollama", {})
                    ol["modelo"] = "qwen2.5:3b-instruct"
                    ol["num_gpu"] = 22
                    ol["num_ctx"] = 4096
                route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

            page.route("**/api/status", _intercept_status)
            page.goto(_live_server.base_url, wait_until="networkidle")

            # 1) Configurações: campo #setModoGpu com texto explicativo, altera para 'moderado' e persiste
            page.click("#btnAppMenu")
            page.click('#appMainMenu [data-menu-action="settings"]')
            page.wait_for_selector("#modalSettings:not([hidden])", timeout=5000)

            modal_txt = page.locator("#modalSettings").inner_text()
            assert "Reduz picos de uso da placa de vídeo" in modal_txt
            assert page.locator("#setModoGpu").input_value() == "normal"

            page.select_option("#setModoGpu", "moderado")
            page.click('#formSettings button[type="submit"]')
            page.wait_for_selector("#modalSettings[hidden]", state="attached", timeout=5000)

            # Reabre Configurações e confirma que 'moderado' persistiu
            page.click("#btnAppMenu")
            page.click('#appMainMenu [data-menu-action="settings"]')
            page.wait_for_selector("#modalSettings:not([hidden])", timeout=5000)
            assert page.locator("#setModoGpu").input_value() == "moderado"
            page.click('#modalSettings [data-close-modal="modalSettings"]')

            # 2) Telemetria exibe Modo GPU, camadas, contexto e 'aguardando a placa de vídeo' quando há fila
            simular_fila["ativo"] = True
            page.click("#btnAppMenu")
            page.click('#appMainMenu [data-menu-action="telemetry"]')
            page.wait_for_selector("#modalTelemetry:not([hidden])", timeout=5000)
            page.wait_for_function(
                "() => (document.getElementById('telemetryGpuStatus')?.textContent || '').includes('aguardando a placa de vídeo')",
                timeout=5000,
            )
            gpu_txt = page.locator("#telemetryGpuStatus").inner_text()
            assert "Moderado" in gpu_txt
            assert "22" in gpu_txt
            assert "4096" in gpu_txt
            assert "aguardando a placa de vídeo" in gpu_txt

            context.close()
        finally:
            browser.close()
