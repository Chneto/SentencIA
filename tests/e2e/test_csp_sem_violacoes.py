"""Teste E2E (Playwright/Chromium) verificando ausência de violações de CSP na UI (TASK-094)."""

from __future__ import annotations

import pytest

from tests.e2e.conftest import _LocalAppServer
from tests.e2e.test_layout_transbordo import _launch_chromium


def _playwright_ou_skip():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("Playwright não instalado neste ambiente.")
    return sync_playwright


def test_ui_sem_violacoes_de_csp_no_navegador() -> None:
    sync_playwright = _playwright_ou_skip()

    with _LocalAppServer() as server:
        with sync_playwright() as p:
            browser = _launch_chromium(p)
            try:
                context = browser.new_context()
                page = context.new_page()

                console_csp_errors = []
                page.on(
                    "console",
                    lambda msg: console_csp_errors.append(msg.text)
                    if msg.type == "error" and "Content Security Policy" in msg.text
                    else None,
                )

                page.add_init_script(
                    """
                    window.__cspViolations = [];
                    document.addEventListener("securitypolicyviolation", (ev) => {
                        window.__cspViolations.push({
                            directive: ev.violatedDirective,
                            blocked: ev.blockedURI,
                        });
                    });
                    """
                )

                resp = page.goto(server.base_url, wait_until="networkidle")
                assert resp is not None
                assert "script-src 'self'" in (resp.headers.get("content-security-policy") or "")

                # Carrega o caso demo via API autenticada na página e navega pelas estações
                btn_demo = page.locator("#btnLoadDemo, [data-action='load-demo']").first
                if btn_demo.count() > 0 and btn_demo.is_visible():
                    btn_demo.click()
                    page.wait_for_timeout(400)

                violacoes = page.evaluate("() => window.__cspViolations")
                assert violacoes == [], f"Violações de CSP detectadas: {violacoes}"
                assert console_csp_errors == [], f"Erros de CSP no console: {console_csp_errors}"
            finally:
                browser.close()
