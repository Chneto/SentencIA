"""TASK-088. Tela das decisões acessórias: prova de 4 a 6 anos no art. 77, § 2º, e exceção do art. 44, § 3º."""

from tests.e2e.test_dosimetria_por_imputacao import _autos_ficticios
from tests.e2e.test_layout_transbordo import _LocalAppServer, _launch_chromium


def test_sursis_p2_e_excecao_44_p3_na_tela(_live_server: _LocalAppServer, tmp_path):
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
            page.click('.station-btn[data-station="sentenca"]')
            page.get_by_text("Decisões acessórias", exact=True).click()
            subst = page.locator('[data-acessorio="substituicao"]')
            subst.locator("[data-decisao-acessoria]").select_option("negar")
            subst.locator("[data-fundamento-acessorio] textarea").fill("Negativa fictícia.")
            sursis = page.locator('[data-acessorio="sursis"]')
            sursis.locator("[data-decisao-acessoria]").select_option("conceder")
            sursis.locator("[data-fundamento-acessorio] textarea").fill("Fundamento fictício.")
            sursis.locator("[data-hipotese-art77-p2]").select_option("maior_70")
            sursis.locator("[data-condicoes-acessorias]").fill("Condição fictícia.")
            validar = """async () => {
                const m = await import('/static/js/modulos/registro-decisoes-acessorias.js?v=2.4.0');
                return {res: m.validarDecisoesAcessorias(), dados: m.dadosAcessorias()};
            }"""
            sursis.locator("[data-prazo-acessorio]").fill("3")
            resultado = page.evaluate(validar)
            assert resultado["res"]["ok"] is False and "entre 4 e 6 anos" in resultado["res"]["mensagem"]
            sursis.locator("[data-prazo-acessorio]").fill("5")
            resultado = page.evaluate(validar)
            assert resultado["res"]["ok"] is True
            registro = resultado["dados"]["decisoes_acessorias"]["sursis"]
            assert registro["prazo_anos"] == 5 and registro["hipotese_art77_p2"] == "maior_70"

            subst.locator("[data-decisao-acessoria]").select_option("conceder")
            subst.locator("[data-modalidade]").first.check()
            subst.locator("[data-socialmente-recomendavel]").check()
            subst.locator("[data-reincidencia-nao-especifica]").check()
            dados = page.evaluate(validar)["dados"]["decisoes_acessorias"]["substituicao"]
            assert dados["excecao_art44_p3"] == {"socialmente_recomendavel": True, "reincidencia_nao_especifica": True}
        finally:
            browser.close()
