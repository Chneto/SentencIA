# -*- coding: utf-8 -*-
"""Testes E2E do seletor de modelos Ollama nas Configurações (TASK-021)."""

from __future__ import annotations

import json


from tests.e2e.test_layout_transbordo import _LocalAppServer, _launch_chromium


def test_ollama_ui_seletor_lista_automatico_outro_e_offline(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            page = context.new_page()

            estado_mock = {"online": True}

            def _mock_modelos(route):
                if estado_mock["online"]:
                    payload = {
                        "online": True,
                        "instalados": [
                            {"nome": "qwen2.5:7b-instruct", "tamanho_gb": 4.2, "parametros": "7B", "quantizacao": "Q4_K_M"},
                            {"nome": "gemma4:e2b", "tamanho_gb": 3.1, "parametros": "2.3B", "quantizacao": "Q4_K_M"},
                        ],
                        "configurado": "auto",
                        "configurado_instalado": False,
                        "recomendado": "qwen2.5:7b-instruct",
                    }
                else:
                    payload = {
                        "online": False,
                        "instalados": [],
                        "configurado": "auto",
                        "configurado_instalado": False,
                        "recomendado": None,
                    }
                route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

            page.route("**/api/ollama/modelos", _mock_modelos)
            page.goto(_live_server.base_url, wait_until="networkidle")

            # 1) Online: seletor habilitado, lista com tamanho + quantização e opção Automático (recomendado)
            page.click("#btnAppMenu")
            page.click('#appMainMenu [data-menu-action="settings"]')
            page.wait_for_selector("#modalSettings", state="visible", timeout=5000)
            page.wait_for_function(
                "() => (document.getElementById('setOllamaModelSelect')?.options.length || 0) >= 4",
                timeout=5000,
            )
            assert not page.is_disabled("#setOllamaModelSelect")
            opcoes = page.eval_on_selector_all(
                "#setOllamaModelSelect option",
                "els => els.map(o => ({ value: o.value, text: o.textContent }))",
            )
            assert opcoes[0]["value"] == "auto"
            assert "Automático (recomendado: qwen2.5:7b-instruct)" in opcoes[0]["text"]
            assert any("qwen2.5:7b-instruct" in o["text"] and "4,2 GB" in o["text"] and "Q4_K_M" in o["text"] for o in opcoes)
            assert opcoes[-1]["value"] == "__outro__"

            # 2) Seleciona "Outro…" -> exibe input de texto livre e salva modelo personalizado
            assert page.is_hidden("#setOllamaModel")
            page.select_option("#setOllamaModelSelect", "__outro__")
            page.wait_for_selector("#setOllamaModel", state="visible", timeout=3000)
            page.fill("#setOllamaModel", "meu-modelo-custom:latest")
            page.click('#formSettings button[type="submit"]')
            page.wait_for_selector("#modalSettings", state="hidden", timeout=5000)

            # 3) Offline: seletor desabilitado, mensagem "Ollama não está em execução" e link para telemetria
            estado_mock["online"] = False
            page.click("#btnAppMenu")
            page.click('#appMainMenu [data-menu-action="settings"]')
            page.wait_for_selector("#modalSettings", state="visible", timeout=5000)
            page.wait_for_function("() => document.getElementById('setOllamaModelSelect')?.disabled === true", timeout=5000)
            hint_txt = page.text_content("#ollamaModelHint") or ""
            assert "Ollama não está em execução" in hint_txt
            page.click("#btnOllamaLinkTelemetria")
            page.wait_for_selector("#modalTelemetry", state="visible", timeout=5000)

            context.close()
        finally:
            browser.close()
