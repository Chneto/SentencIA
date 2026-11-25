"""TASK-092. Decisões acessórias no rascunho persistido do caso (autos fictícios)."""

from tests.e2e.test_dosimetria_por_imputacao import _autos_ficticios
from tests.e2e.test_layout_transbordo import _LocalAppServer, _launch_chromium

FUNDAMENTO = "Registro fictício ainda incompleto do juízo."


def _abrir_editor(page):
    page.click('.station-btn[data-station="sentenca"]')
    editor = page.locator("#editorDecisoesAcessorias")
    editor.wait_for(state="visible")
    if not editor.evaluate("el => el.open"):
        page.get_by_text("Decisões acessórias", exact=True).click()
    return page.locator('[data-acessorio="medidas_protetivas"]')


def test_rascunho_restaura_so_o_caso_certo_e_marca_minuta_desatualizada(_live_server: _LocalAppServer, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")
            page.evaluate(
                """async (pdf) => {
                    const { apiJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                    const dados = Uint8Array.from(atob(pdf), (c) => c.charCodeAt(0));
                    const form = new FormData();
                    form.append('files', new Blob([dados], {type: 'application/pdf'}), 'autos-ficticios.pdf');
                    await apiJson('/api/cases/upload-pdfs', {method: 'POST', body: form});
                }""",
                _autos_ficticios(tmp_path),
            )
            page.reload(wait_until="networkidle")
            page.click('.station-btn[data-station="dosimetria"]')
            page.locator("#dosimetryCrimeTabs [data-crime-tab]").first.wait_for()
            mpu = _abrir_editor(page)
            mpu.locator("[data-decisao-acessoria]").select_option("manter")
            mpu.locator("[data-fundamento-acessorio] textarea").fill(FUNDAMENTO)

            # A gravação é agrupada (~1,5 s): espera o servidor ter o rascunho com o fundamento digitado.
            salvo = []
            for _ in range(30):
                salvo = page.evaluate(
                    """async () => {
                        const { apiJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                        const r = (await apiJson('/api/cases/rascunho-decisoes')).rascunho;
                        return r?.extras?.acessorias?.medidas_protetivas || [];
                    }"""
                )
                if FUNDAMENTO in salvo:
                    break
                page.wait_for_timeout(500)
            assert FUNDAMENTO in salvo

            page.reload(wait_until="networkidle")
            mpu = _abrir_editor(page)
            assert mpu.locator("[data-decisao-acessoria]").input_value() == "manter"
            assert mpu.locator("[data-fundamento-acessorio] textarea").input_value() == FUNDAMENTO
            assert mpu.locator("[data-fundamento-acessorio]").is_visible()

            desatualizada = page.evaluate(
                """async () => {
                    const { state } = await import('/static/js/modulos/estado.js?v=2.4.0');
                    const m = await import('/static/js/modulos/registro-decisoes-acessorias.js?v=2.4.0');
                    state.lastSentence = { texto_completo: 'Minuta fictícia.' };
                    m.marcarMinutaGerada();
                    const antes = !!document.querySelector('[data-minuta-desatualizada]');
                    const campo = document.querySelector('[data-acessorio="medidas_protetivas"] textarea');
                    campo.value = campo.value + ' Complemento.';
                    campo.dispatchEvent(new Event('input', { bubbles: true }));
                    return { antes, depois: !!document.querySelector('[data-minuta-desatualizada]') };
                }"""
            )
            assert desatualizada == {"antes": False, "depois": True}

            page.evaluate(
                """async () => {
                    const { postJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                    const { hooks } = await import('/static/js/modulos/estado.js?v=2.4.0');
                    const demo = await postJson('/api/cases/load-demo', {});
                    await hooks.applyActiveCase(demo, { clearSentence: true });
                }"""
            )
            mpu = _abrir_editor(page)
            assert mpu.locator("[data-decisao-acessoria]").input_value() == ""
            assert mpu.locator("[data-fundamento-acessorio] textarea").input_value() == ""
        finally:
            browser.close()
