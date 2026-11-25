"""Tema Matilha: o "rabo abanando" no hover de botão primário não muda o layout.

Regressão encontrada na CI da 2.3.0: os 12 px do pseudo-elemento quebravam a linha do cabeçalho em 1366 px; o botão
saía de baixo do ponteiro, o hover acabava e o cabeçalho oscilava a cada quadro (menu "instável").
"""

from tests.e2e.test_layout_transbordo import _LocalAppServer, _launch_chromium


def test_hover_em_botao_primario_nao_altera_largura_nem_cabecalho(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            page.goto(_live_server.base_url, wait_until="networkidle")
            page.evaluate(
                """async () => {
                    const { applyDesign } = await import('/static/js/modulos/temas.js?v=2.4.0');
                    applyDesign('matilha', { persist: false });
                }"""
            )
            page.wait_for_selector("html[data-design='matilha']", state="attached")
            botao = page.locator(".btn-primary:visible").first
            botao.wait_for()
            # Caixa de layout (offset*), que ignora o transform do próprio hover (leve elevação animada);
            # a posição do menu é arredondada para não depender de subpixel.
            medir = """(el) => {
                const menu = document.getElementById('btnAppMenu').getBoundingClientRect();
                return { largura: el.offsetWidth, altura: el.offsetHeight, menuY: Math.round(menu.y) };
            }"""
            antes = botao.evaluate(medir)
            botao.hover()
            page.wait_for_timeout(150)
            depois = botao.evaluate(medir)
            assert depois == antes
            depois_tail = page.evaluate(
                """() => {
                    const el = document.querySelector('.btn-primary:hover');
                    return el ? getComputedStyle(el, '::after').position : 'sem-hover';
                }"""
            )
            assert depois_tail == "absolute"
        finally:
            browser.close()
