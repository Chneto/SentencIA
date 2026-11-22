# -*- coding: utf-8 -*-
"""Testes E2E da remoção dos atalhos duplicados da sidebar e acesso pelo menu do logo (TASK-026)."""

from __future__ import annotations

from tests.e2e.test_layout_transbordo import DESIGNS, THEMES, _LocalAppServer, _launch_chromium, _set_theme


def test_atalhos_duplicados_removidos_em_todos_temas_e_menu_logo_abre_modais(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            page = context.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")

            # 1) Em todos os 8 modos (4 temas × claro/escuro), nenhum dos 3 botões nem .sidebar-utilities existe
            for design in DESIGNS:
                for theme in THEMES:
                    _set_theme(page, theme, design)
                    page.wait_for_timeout(60)
                    for sel in (
                        "#btnOpenAcervoModal",
                        "#btnOpenSettingsModal",
                        "#btnOpenTelemetrySidebar",
                        ".sidebar-utilities",
                    ):
                        assert page.locator(sel).count() == 0, f"{sel} ainda existe em {design}/{theme}"
                    sidebar_txt = page.locator(".sidebar-nav").inner_text()
                    assert "Gabinete & Acervo" not in sidebar_txt

            # 2) Assinatura discreta continua presente e sincronizada com a versão do app
            _set_theme(page, "dark", "original")
            sig = page.locator(".sidebar-author-signature")
            assert sig.count() == 1
            assert "FChNeto" in sig.inner_text()
            app_ver = page.evaluate("() => document.documentElement.getAttribute('data-app-version')")
            assert app_ver and app_ver in sig.inner_text()

            # 3) Menu do logo exibe o contador do acervo e abre Acervo, Configurações e Telemetria
            page.click("#btnAppMenu")
            page.wait_for_selector("#appMainMenu:not([hidden])", timeout=3000)
            acervo_item = page.locator('#appMainMenu [data-menu-action="acervo"]')
            badge_txt = acervo_item.locator("#badgeAcervoCount").inner_text().strip()
            assert badge_txt.isdigit() and int(badge_txt) > 0, f"Contador do acervo inválido: {badge_txt!r}"

            # Abre Acervo
            acervo_item.click()
            page.wait_for_selector("#modalAcervo:not([hidden])", timeout=5000)
            page.click('#modalAcervo [data-close-modal="modalAcervo"]')

            # Abre Configurações
            page.click("#btnAppMenu")
            page.click('#appMainMenu [data-menu-action="settings"]')
            page.wait_for_selector("#modalSettings:not([hidden])", timeout=5000)
            page.click('#modalSettings [data-close-modal="modalSettings"]')

            # Abre Telemetria
            page.click("#btnAppMenu")
            page.click('#appMainMenu [data-menu-action="telemetry"]')
            page.wait_for_selector("#modalTelemetry:not([hidden])", timeout=5000)
            page.click('#modalTelemetry [data-close-modal="modalTelemetry"]')

            context.close()
        finally:
            browser.close()
