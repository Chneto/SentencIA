"""TASK-056. Preenchimento probatório por imputação na interface."""

from tests.e2e.test_dosimetria_por_imputacao import _autos_ficticios
from tests.e2e.test_layout_transbordo import _LocalAppServer, _launch_chromium


def test_editor_vincula_trechos_e_decisao_cautelar(_live_server: _LocalAppServer, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")
            fonte = page.evaluate(
                """async (pdf) => {
                    const { apiJson } = await import('/static/js/modulos/api.js?v=2.2.0');
                    const form = new FormData();
                    const dados = Uint8Array.from(atob(pdf), (c) => c.charCodeAt(0));
                    form.append('files', new Blob([dados], {type: 'application/pdf'}), 'autos-ficticios.pdf');
                    await apiJson('/api/cases/upload-pdfs', {method: 'POST', body: form});
                    const caso = await apiJson('/api/cases/active');
                    return caso.metadados.imputacoes[0].fonte_id;
                }""",
                _autos_ficticios(tmp_path),
            )
            page.reload(wait_until="networkidle")
            page.click('.station-btn[data-station="dosimetria"]')
            abas = page.locator("#dosimetryCrimeTabs [data-crime-tab]")
            abas.nth(1).wait_for()
            abas.nth(0).click()
            page.select_option("#selCrimeVeredicto", "condenar")
            page.get_by_text("Conclusões sobre a prova desta imputação", exact=True).click()
            elemento = page.locator('[data-elemento-probatorio="promessa_mal_grave"]')
            elemento.locator("[data-conclusao]").select_option("provado")
            elemento.locator("[data-fundamento]").fill("Fundamento fictício do primeiro fato.")
            elemento.locator('[data-fonte="favor"]').fill(fonte)
            elemento.locator('[data-trecho="favor"]').fill("o acusado teria praticado ameaça")

            abas.nth(1).click()
            page.select_option("#selCrimeVeredicto", "absolver")
            page.select_option("#selFundArt386", "VII")
            page.get_by_text("Conclusões sobre a prova desta imputação", exact=True).click()
            assert elemento.locator("[data-conclusao]").input_value() == ""
            page.get_by_text("Decisão cautelar da sentença", exact=True).click()
            cautelar = page.locator("#registroCautelar")
            cautelar.locator("[data-conclusao]").select_option("conceder")
            cautelar.locator("[data-fundamento]").fill("Fundamento cautelar fictício.")

            resposta = page.evaluate(
                """async () => {
                    const { postJson } = await import('/static/js/modulos/api.js?v=2.2.0');
                    const { buildDosimetryPayload } = await import('/static/js/modulos/estacao-dosimetria.js?v=2.2.0');
                    const payload = {...buildDosimetryPayload(), data_sentenca: '2024-05-30', use_ollama: false};
                    return {payload, geracao: await postJson('/api/sentence/generate', payload)};
                }"""
            )
        finally:
            browser.close()

    primeiro, segundo = resposta["payload"]["crimes_config"]
    assert primeiro["id_imputacao"] != segundo["id_imputacao"]
    assert primeiro["elementos_provados"][0]["provas_favoraveis"][0]["fonte_id"] == fonte
    assert "elementos_provados" not in segundo
    sentenca = resposta["geracao"]["sentenca"]
    assert sentenca["secao_merito"].count("Fundamento fictício do primeiro fato.") == 1
    assert "“o acusado teria praticado ameaça”" in sentenca["secao_merito"]
    assert "Fundamento cautelar fictício." in sentenca["secao_dispositivo"]
