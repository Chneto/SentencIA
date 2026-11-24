# -*- coding: utf-8 -*-
"""Testes E2E do menu do aplicativo no logo, infraestrutura de temas e tema Gabinete Clássico (TASK-007)."""

from __future__ import annotations

from typing import Any, List


from tests.e2e.test_layout_transbordo import (
    OVERFLOW_JS,
    _LocalAppServer,
    _launch_chromium,
)


def test_menu_logo_temas_teclado_paleta_e_persistencia(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    external_urls: List[str] = []

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            page = context.new_page()

            def _route(route: Any) -> None:
                url = route.request.url
                if not (url.startswith(_live_server.base_url) or url.startswith("data:") or url.startswith("blob:")):
                    external_urls.append(url)
                    route.abort()
                    return
                route.continue_()

            page.route("**/*", _route)
            page.goto(_live_server.base_url, wait_until="networkidle")

            # 1) Logo acessível com role/aria e abertura por clique
            assert page.get_attribute("#btnAppMenu", "aria-haspopup") == "menu"
            assert page.get_attribute("#btnAppMenu", "aria-expanded") == "false"
            page.click("#btnAppMenu")
            assert page.get_attribute("#btnAppMenu", "aria-expanded") == "true"
            assert page.is_visible("#appMainMenu")
            assert page.get_attribute("#appMainMenu", "role") == "menu"

            # Submenu Temas com os 4 desenhos + Claro/Escuro/Sistema
            page.click("#menuItemTemas")
            assert page.is_visible("#appThemesSubmenu")
            assert not page.is_disabled('[data-set-design="moderno"]')
            assert not page.is_disabled('[data-set-design="cockpit"]')
            assert page.get_attribute('[data-set-design="original"]', "aria-checked") == "true"
            assert page.get_attribute('[data-set-design="classico"]', "role") == "menuitemradio"

            # Zero transbordo com menu e submenu abertos
            assert page.evaluate(OVERFLOW_JS) == []

            # Carrega demo e edita texto na folha antes de trocar tema (não pode perder edição)
            page.click('[data-menu-action="demo"]')
            page.wait_for_function(
                "() => !document.getElementById('heroCaseNumber').textContent.includes('Sem autos')",
                timeout=15000,
            )
            page.click('.station-btn[data-station="sentenca"]')
            page.wait_for_timeout(150)
            page.evaluate(
                "() => { const b = document.getElementById('sentenceRenderBody'); if (b) b.dataset.editSentinel = 'preservado'; }"
            )

            # 2) Troca para Gabinete Clássico + Escuro pelo menu (aplica na hora, sem recarregar, persiste)
            page.click("#btnAppMenu")
            page.click("#menuItemTemas")
            page.click('[data-set-design="classico"]')
            assert page.get_attribute("html", "data-design") == "classico"
            assert page.evaluate("() => document.getElementById('sentenceRenderBody').dataset.editSentinel") == "preservado"

            page.click("#btnAppMenu")
            page.click("#menuItemTemas")
            # Espera a gravação real em /api/settings (no CI lento, um atraso fixo não bastava).
            with page.expect_response(
                lambda r: "/api/settings" in r.url and r.request.method != "GET", timeout=15000
            ) as gravacao:
                page.click('[data-set-mode="dark"]')
            assert gravacao.value.ok
            assert page.get_attribute("html", "data-theme") == "dark"

            page.reload(wait_until="networkidle")
            assert page.get_attribute("html", "data-design") == "classico"
            assert page.get_attribute("html", "data-theme") == "dark"

            # 3) Navegação completa por teclado (Enter -> ArrowRight -> ArrowLeft -> Escape retorna foco ao logo)
            page.focus("#btnAppMenu")
            page.keyboard.press("Enter")
            assert page.is_visible("#appMainMenu")
            page.keyboard.press("ArrowRight")
            assert page.is_visible("#appThemesSubmenu")
            page.keyboard.press("ArrowLeft")
            assert not page.is_visible("#appThemesSubmenu")
            page.keyboard.press("Escape")
            assert not page.is_visible("#appMainMenu")
            assert page.evaluate("() => document.activeElement && document.activeElement.id") == "btnAppMenu"

            # Clicar fora fecha o menu
            page.click("#btnAppMenu")
            assert page.is_visible("#appMainMenu")
            page.mouse.click(900, 300)
            assert not page.is_visible("#appMainMenu")

            # 4) Demais opções do menu funcionam (Configurações, Acervo, Telemetria, Reset, Sobre)
            for action, modal_id in [
                ("settings", "modalSettings"),
                ("acervo", "modalAcervo"),
                ("telemetry", "modalTelemetry"),
                ("reset", "modalResetProduction"),
                ("about", "modalAboutSentencia"),
            ]:
                page.click("#btnAppMenu")
                page.click(f'[data-menu-action="{action}"]')
                page.wait_for_selector(f"#{modal_id}", state="visible", timeout=5000)
                page.keyboard.press("Escape")
                page.wait_for_timeout(80)

            # Botões originais do topo continuam funcionando
            page.click("#btnToggleTheme")
            assert page.get_attribute("html", "data-theme") == "light"

            # 5) Paleta de comandos (Ctrl+K) lista temas e aplica Original
            page.keyboard.press("Control+k")
            assert page.is_visible("#modalCommandPalette")
            page.fill("#cmdPaletteInput", "Original")
            page.keyboard.press("Enter")
            assert not page.is_visible("#modalCommandPalette")
            assert page.get_attribute("html", "data-design") == "original"

            assert not external_urls, f"Requisições externas proibidas: {external_urls}"
            context.close()
        finally:
            browser.close()



def test_rotulo_botao_tema_tres_modos_menu_e_paleta(_live_server: _LocalAppServer):
    """TASK-012: #btnToggleTheme mostra sempre a ação disponível nos 3 modos, menu e Paleta."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768}, color_scheme="light")
            page = context.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")

            # Garante estado inicial claro independente de testes anteriores no mesmo servidor
            page.click("#btnAppMenu")
            page.click("#menuItemTemas")
            page.click('[data-set-mode="light"]')
            assert page.get_attribute("html", "data-theme") == "light"
            assert page.text_content("#btnToggleThemeLabel").strip() == "Tema Escuro"

            # Alterna pelo próprio botão -> escuro -> oferece "Tema Claro"
            page.click("#btnToggleTheme")
            assert page.get_attribute("html", "data-theme") == "dark"
            assert page.text_content("#btnToggleThemeLabel").strip() == "Tema Claro"

            # Alterna para Claro pelo menu do logo -> oferece "Tema Escuro"
            page.click("#btnAppMenu")
            page.click("#menuItemTemas")
            page.click('[data-set-mode="light"]')
            assert page.get_attribute("html", "data-theme") == "light"
            assert page.text_content("#btnToggleThemeLabel").strip() == "Tema Escuro"

            # Alterna para Escuro pela Paleta de comandos (Ctrl+K) -> oferece "Tema Claro"
            page.keyboard.press("Control+k")
            page.fill("#cmdPaletteInput", "Aparência: Escuro")
            page.keyboard.press("Enter")
            assert page.get_attribute("html", "data-theme") == "dark"
            assert page.text_content("#btnToggleThemeLabel").strip() == "Tema Claro"

            # Modo "Seguir o sistema" com prefers-color-scheme: dark -> efetivo dark -> "Tema Claro"
            page.emulate_media(color_scheme="dark")
            page.click("#btnAppMenu")
            page.click("#menuItemTemas")
            page.click('[data-set-mode="system"]')
            assert page.get_attribute("html", "data-theme") == "dark"
            assert page.text_content("#btnToggleThemeLabel").strip() == "Tema Claro"

            # Mudança dinâmica do SO para light enquanto em "system" -> efetivo light -> "Tema Escuro"
            page.emulate_media(color_scheme="light")
            page.wait_for_function(
                "() => document.documentElement.getAttribute('data-theme') === 'light'",
                timeout=3000,
            )
            assert page.text_content("#btnToggleThemeLabel").strip() == "Tema Escuro"

            context.close()
        finally:
            browser.close()


def test_toasts_autoclose_pausa_persistencia_e_limite_3(_live_server: _LocalAppServer):
    """TASK-012: toast informativo fecha sozinho (pausa em hover/foco), simulação/warn persistem, máx 3."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            page = context.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")

            # TASK-022 (TASK-012 r1): sem ?teste=1, window.__sentenciaShowToast não fica exposto
            assert page.evaluate("() => typeof window.__sentenciaShowToast === 'undefined'")

            # Abre com ?teste=1 para habilitar o helper de teste
            page.goto(f"{_live_server.base_url}/?teste=1", wait_until="networkidle")
            assert page.evaluate("() => typeof window.__sentenciaShowToast === 'function'")

            # 1) Toast informativo fecha sozinho e pausa no hover
            page.evaluate("() => window.__sentenciaShowToast('Aviso informativo temporário', 'info', 400)")
            toast = page.locator(".toast-item").first
            assert toast.is_visible()
            toast.hover()
            page.wait_for_timeout(550)
            # Ainda visível porque o hover pausou a contagem
            assert toast.is_visible()
            # Move o mouse para fora -> retoma e fecha sozinho
            page.mouse.move(10, 10)
            page.wait_for_function("() => document.querySelectorAll('.toast-item').length === 0", timeout=2000)

            # 2) Toast de foco pausa a contagem e retoma ao sair o foco
            page.evaluate("() => window.__sentenciaShowToast('Sucesso com foco', 'success', 350)")
            ok_btn = page.locator(".toast-item button").first
            ok_btn.focus()
            page.wait_for_timeout(500)
            assert page.locator(".toast-item").count() == 1
            page.evaluate("() => document.activeElement && document.activeElement.blur()")
            page.wait_for_function("() => document.querySelectorAll('.toast-item').length === 0", timeout=2000)

            # 3) Toast de SIMULAÇÃO e warn/error ficam até o usuário fechar + limite de 3 empilhados
            page.evaluate(
                """() => {
                    window.__sentenciaShowToast('Minuta de SIMULAÇÃO (condenação presumida para crime sem veredicto). Não utilize como decisão.', 'warn');
                    window.__sentenciaShowToast('Info 1 antiga', 'info', 6000);
                    window.__sentenciaShowToast('Info 2 intermediária', 'info', 6000);
                    window.__sentenciaShowToast('Info 3 recente', 'success', 6000);
                }"""
            )
            texts = page.eval_on_selector_all(".toast-item span", "els => els.map(e => e.textContent)")
            assert len(texts) == 3, f"Esperado limite de 3 toasts, obtido {len(texts)}: {texts}"
            assert any("Minuta de SIMULAÇÃO" in t for t in texts), f"Toast de simulação foi removido: {texts}"
            assert "Info 1 antiga" not in texts, f"Toast informativo mais antigo deveria ter saído primeiro: {texts}"
            assert "Info 2 intermediária" in texts and "Info 3 recente" in texts

            # Após 6,4 s, os dois informativos somem sozinhos e o de SIMULAÇÃO permanece aberto até clicar OK
            page.wait_for_function(
                "() => document.querySelectorAll('.toast-item').length === 1",
                timeout=8000,
            )
            remaining = page.eval_on_selector_all(".toast-item span", "els => els.map(e => e.textContent)")
            assert len(remaining) == 1 and "Minuta de SIMULAÇÃO" in remaining[0]
            page.locator(".toast-item button").first.click()
            assert page.locator(".toast-item").count() == 0

            # 4) TASK-022 (TASK-012 r2): mesmo com 3 persistentes (warn/error), aviso de SIMULAÇÃO só sai por último
            page.evaluate(
                """() => {
                    window.__sentenciaShowToast('Minuta de SIMULAÇÃO (condenação presumida). Não utilize.', 'warn');
                    window.__sentenciaShowToast('Aviso persistente 1', 'warn');
                    window.__sentenciaShowToast('Erro persistente 2', 'error');
                    window.__sentenciaShowToast('Erro persistente 3', 'error');
                }"""
            )
            persist_texts = page.eval_on_selector_all(".toast-item span", "els => els.map(e => e.textContent)")
            assert len(persist_texts) == 3
            assert any("Minuta de SIMULAÇÃO" in t for t in persist_texts), f"Simulação saiu antes de outro persistente: {persist_texts}"
            assert "Aviso persistente 1" not in persist_texts
            while page.locator(".toast-item button").count() > 0:
                page.locator(".toast-item button").first.click()

            context.close()
        finally:
            browser.close()
