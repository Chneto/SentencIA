"""Testes E2E (Playwright) de migração de localStorage e sessionStorage (TASK-082)."""

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


def test_migracao_tema_e_rascunho_no_navegador() -> None:
    sync_playwright = _playwright_ou_skip()

    with _LocalAppServer() as server:
        with sync_playwright() as p:
            browser = _launch_chromium(p)
            try:
                context = browser.new_context()
                page = context.new_page()

                # Prepara chaves legadas antes do carregamento da página inicial
                page.add_init_script(
                    """
                    if (!window.__sentenciaMigracaoSeeded) {
                        window.__sentenciaMigracaoSeeded = true;
                        localStorage.removeItem("sentencia_theme");
                        localStorage.setItem("jvdfm_theme", "dark");
                        sessionStorage.removeItem("sentencia.decisoes.v1");
                        sessionStorage.setItem("jvdfm.decisoes.v1", JSON.stringify({
                            caseKey: "ficticio-082",
                            crimeConfigs: [{ veredicto: "condenar" }]
                        }));
                    }
                    """
                )
                page.goto(server.base_url, wait_until="domcontentloaded")

                # 1. localStorage: jvdfm_theme -> sentencia_theme e remoção da chave antiga
                estado_tema = page.evaluate(
                    """() => ({
                        novo: localStorage.getItem("sentencia_theme"),
                        legado: localStorage.getItem("jvdfm_theme"),
                        attr: document.documentElement.getAttribute("data-theme"),
                    })"""
                )
                assert estado_tema == {"novo": "dark", "legado": None, "attr": "dark"}

                # 2. sessionStorage: jvdfm.decisoes.v1 -> sentencia.decisoes.v1 via módulo real
                estado_rascunho = page.evaluate(
                    """async () => {
                        const mod = await import("/static/js/modulos/registro-rascunho.js");
                        const lido = mod.lerRascunhoDecisoes("ficticio-082");
                        return {
                            lido,
                            novo: sessionStorage.getItem("sentencia.decisoes.v1"),
                            legado: sessionStorage.getItem("jvdfm.decisoes.v1"),
                        };
                    }"""
                )
                assert estado_rascunho["lido"] is not None
                assert estado_rascunho["lido"]["caseKey"] == "ficticio-082"
                assert estado_rascunho["novo"] is not None
                assert estado_rascunho["legado"] is None
            finally:
                browser.close()
