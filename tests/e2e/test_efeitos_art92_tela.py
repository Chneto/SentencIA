"""TASK-087. Efeitos do art. 92, § 2º, registrados na tela chegam à minuta (autos fictícios)."""

from tests.e2e.test_dosimetria_por_imputacao import _autos_ficticios
from tests.e2e.test_layout_transbordo import _LocalAppServer, _launch_chromium


def test_efeitos_art92_da_tela_chegam_ao_dispositivo(_live_server: _LocalAppServer, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")
            page.evaluate(
                """async (pdf) => {
                    const { apiJson } = await import('/static/js/modulos/api.js?v=2.3.1');
                    const dados = Uint8Array.from(atob(pdf), (c) => c.charCodeAt(0));
                    const form = new FormData();
                    form.append('files', new Blob([dados], {type: 'application/pdf'}), 'autos-ficticios.pdf');
                    await apiJson('/api/cases/upload-pdfs', {method: 'POST', body: form});
                }""",
                _autos_ficticios(tmp_path),
            )
            page.reload(wait_until="networkidle")
            page.click('.station-btn[data-station="dosimetria"]')
            abas = page.locator("#dosimetryCrimeTabs [data-crime-tab]")
            abas.first.wait_for()
            for indice in range(abas.count()):
                abas.nth(indice).click()
                page.select_option("#selCrimeVeredicto", "condenar")
            page.click('.station-btn[data-station="sentenca"]')
            page.get_by_text("Decisões acessórias", exact=True).click()
            bloco = page.locator('[data-acessorio="efeitos_art92"]')
            bloco.locator("[data-decisao-acessoria]").select_option("violencia_domestica")
            bloco.locator("[data-cargo-art92]").select_option("sim")
            bloco.locator("[data-poder-familiar-art92]").select_option("nao")

            validacao = page.evaluate(
                """async () => {
                    const m = await import('/static/js/modulos/registro-decisoes-acessorias.js?v=2.3.1');
                    return m.validarDecisoesAcessorias();
                }"""
            )
            assert validacao["ok"] is False and "identifique o cargo" in validacao["mensagem"]

            bloco.locator("[data-descricao-cargo-art92]").fill("servidor municipal fictício")
            registro = page.evaluate(
                """async () => {
                    const m = await import('/static/js/modulos/registro-decisoes-acessorias.js?v=2.3.1');
                    return {ok: m.validarDecisoesAcessorias().ok, dados: m.dadosAcessorias()};
                }"""
            )
            assert registro["ok"] is True
            assert registro["dados"]["decisoes_acessorias"]["efeitos_art92"] == {
                "enquadramento": "violencia_domestica", "fundamento": None, "cargo_publico": "sim",
                "poder_familiar": "nao", "descricao_cargo": "servidor municipal fictício",
            }
        finally:
            browser.close()
