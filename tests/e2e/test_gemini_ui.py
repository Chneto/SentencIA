# -*- coding: utf-8 -*-
"""Testes E2E da interface do Gemini (TASK-006).

Cobre com backend simulado via interceptação de rede do Playwright:
1. Modal Gemini: lista dinâmica de modelos via ``GET /api/gemini/modelos`` + indicadores de estado.
2. Sem chave: seletor desabilitado e link "Cadastrar chave" levando às Configurações.
3. Configurações: botão "Testar chave" em caso de sucesso e em caso de erro traduzido.
4. Geração com troca automática de modelo exibindo "Modelo X indisponível; usado Y".
5. Zero transbordo nos novos elementos do modal Gemini e de Configurações.
"""

from __future__ import annotations

import json
from typing import Any


from tests.e2e.test_layout_transbordo import (
    OVERFLOW_JS,
    _LocalAppServer,
    _launch_chromium,
)


def test_gemini_ui_modelos_sem_chave_testar_chave_e_troca_modelo(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            page = context.new_page()

            estado = {
                "modelos_resp": {
                    "modelos": [
                        {"name": "gemini-3.8-flash", "display_name": "Gemini 3.8 Flash"},
                        {"name": "gemini-3.7-flash", "display_name": "Gemini 3.7 Flash"},
                    ],
                    "recomendado": "gemini-3.8-flash",
                    "chave_configurada": True,
                },
                "secret_resp": {"configurada": True, "backend": "Windows Credential Manager"},
            }

            def _route_handler(route: Any) -> None:
                url = route.request.url
                if url.endswith("/api/settings") and route.request.method == "GET":
                    resp = route.fetch()
                    data = resp.json()
                    data["permitir_envio_externo"] = True
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(data),
                    )
                    return
                if url.endswith("/api/gemini/modelos"):
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(estado["modelos_resp"]),
                    )
                    return
                if url.endswith("/api/secrets/gemini") and route.request.method == "GET":
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(estado["secret_resp"]),
                    )
                    return
                if url.endswith("/api/gemini/generate") and route.request.method == "POST":
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(
                            {
                                "sentenca": {
                                    "texto_completo": "I - RELATÓRIO\n\nCaso demo.\n\nII - FUNDAMENTAÇÃO\n\nOk.\n\nIII - DISPOSITIVO\n\nCondeno.",
                                    "motor_utilizado": "Gemini API (gemini-3.8-flash)",
                                },
                                "auditoria": {
                                    "aprovada": True,
                                    "score_confiabilidade": 100.0,
                                    "alertas": [],
                                },
                                "telemetria": {
                                    "gemini_utilizado": True,
                                    "modelo_solicitado": "gemini-2.5-pro",
                                    "modelo_utilizado": "gemini-3.8-flash",
                                    "modelo_gemini": "gemini-3.8-flash",
                                    "motivo_troca": "Modelo 'gemini-2.5-pro' indisponível; usado 'gemini-3.8-flash'.",
                                },
                            }
                        ),
                    )
                    return
                route.continue_()

            page.route("**/*", _route_handler)
            page.goto(_live_server.base_url, wait_until="networkidle")

            # Carrega caso demo e vai para a estação Sentença
            page.click("#btnLoadDemo")
            page.wait_for_function(
                "() => !document.getElementById('heroCaseNumber').textContent.includes('Sem autos')",
                timeout=15000,
            )
            page.click('.station-btn[data-station="sentenca"]')
            page.wait_for_timeout(150)

            # 1) Modal Gemini COM chave -> lista preenchida
            page.click("#btnOpenGeminiApiModal")
            page.wait_for_function(
                "() => document.querySelectorAll('#selGeminiModel option').length >= 3",
                timeout=10000,
            )
            opcoes = page.eval_on_selector_all("#selGeminiModel option", "els => els.map(e => e.textContent)")
            assert "Automático (recomendado: gemini-3.8-flash)" in opcoes[0]
            assert any("gemini-3.8-flash" in o for o in opcoes[1:])
            assert not page.is_disabled("#selGeminiModel")
            assert "✓ Chave configurada" in (page.text_content("#geminiStatusChave") or "")
            assert "gemini-3.8-flash" in (page.text_content("#geminiStatusModelo") or "")

            # Zero transbordo no modal Gemini
            overflow_modal = page.evaluate(OVERFLOW_JS)
            assert overflow_modal == [], f"Transbordo no modal Gemini: {overflow_modal}"

            # 2) Simula geração com troca automática de modelo -> toast mostra "Modelo X indisponível; usado Y"
            page.evaluate(
                """() => {
                  const chkEnvio = document.getElementById('setPermitirEnvioExterno');
                  if (chkEnvio) chkEnvio.checked = true;
                  const chkCons = document.getElementById('chkConsentimentoGemini');
                  if (chkCons) chkCons.checked = true;
                }"""
            )
            page.click("#btnRunGeminiApi")
            page.wait_for_function(
                "() => (document.getElementById('toastContainer')?.textContent || '').includes('gemini-3.8-flash')",
                timeout=10000,
            )
            toast_txt = page.text_content("#toastContainer") or ""
            assert "Modelo gemini-2.5-pro indisponível; usado gemini-3.8-flash" in toast_txt

            # 3) Modal Gemini SEM chave -> seletor desabilitado + link "Cadastrar chave" levando às Configurações
            estado["secret_resp"] = {"configurada": False, "backend": "Windows Credential Manager"}
            estado["modelos_resp"] = {"modelos": [], "recomendado": None, "chave_configurada": False}
            page.click("#btnOpenGeminiApiModal")
            page.wait_for_function(
                "() => document.getElementById('selGeminiModel').disabled === true",
                timeout=10000,
            )
            assert page.is_visible("#btnGeminiGoSettingsKey")
            page.click("#btnGeminiGoSettingsKey")
            page.wait_for_function(
                "() => document.getElementById('modalSettings').hidden === false",
                timeout=10000,
            )

            # 4) Botão "Testar chave" em Configurações: sucesso e erro
            estado["modelos_resp"] = {
                "modelos": [
                    {"name": "gemini-3.8-flash", "display_name": "Gemini 3.8 Flash"},
                    {"name": "gemini-3.7-flash", "display_name": "Gemini 3.7 Flash"},
                ],
                "recomendado": "gemini-3.8-flash",
                "chave_configurada": True,
            }
            page.click("#btnTestGeminiKey")
            page.wait_for_function(
                "() => (document.getElementById('geminiKeyTestResult')?.textContent || '').includes('✓')",
                timeout=10000,
            )
            res_ok = page.text_content("#geminiKeyTestResult") or ""
            assert "2 modelo(s)" in res_ok

            estado["modelos_resp"] = {
                "modelos": [],
                "recomendado": None,
                "chave_configurada": True,
                "erro": "Chave da API Gemini inválida ou sem permissão (401/403).",
            }
            page.click("#btnTestGeminiKey")
            page.wait_for_function(
                "() => (document.getElementById('geminiKeyTestResult')?.textContent || '').includes('✗')",
                timeout=10000,
            )
            res_err = page.text_content("#geminiKeyTestResult") or ""
            assert "401/403" in res_err

            # Zero transbordo em Configurações com resultado do teste visível
            overflow_cfg = page.evaluate(OVERFLOW_JS)
            assert overflow_cfg == [], f"Transbordo em Configurações: {overflow_cfg}"

            context.close()
        finally:
            browser.close()
