"""TASK-063. Decisões do juízo preenchidas em autos fictícios e consumidas pela geração."""

from tests.e2e.test_dosimetria_por_imputacao import _autos_ficticios
from tests.e2e.test_layout_transbordo import _LocalAppServer, _launch_chromium


def test_sentenca_consumida_com_decisao_acessoria_da_tela(_live_server: _LocalAppServer, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")
            fonte = page.evaluate(
                """async (pdf) => {
                    const { apiJson } = await import('/static/js/modulos/api.js?v=2.3.1');
                    const dados = Uint8Array.from(atob(pdf), (c) => c.charCodeAt(0));
                    const form = new FormData();
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
            page.select_option("#selCrimeVeredicto", "condenar")
            abas.nth(1).click()
            page.select_option("#selCrimeVeredicto", "absolver")
            page.select_option("#selFundArt386", "VII")
            page.click('.station-btn[data-station="sentenca"]')
            page.get_by_text("Decisões acessórias", exact=True).click()
            mpu = page.locator('[data-acessorio="medidas_protetivas"]')
            mpu.locator("[data-decisao-acessoria]").select_option("manter")
            mpu.locator("[data-fundamento-acessorio] textarea").fill("O juízo fictício registrou a persistência do risco.")
            mpu.get_by_text("Peças dos autos", exact=True).click()
            mpu.locator(f'[data-fonte-acessoria][value="{fonte}"]').check()
            envio = page.evaluate(
                """async () => {
                    const { postJson } = await import('/static/js/modulos/api.js?v=2.3.1');
                    const { buildDosimetryPayload } = await import('/static/js/modulos/estacao-dosimetria.js?v=2.3.1');
                    const payload = {...buildDosimetryPayload(), data_sentenca: '2024-05-30', use_ollama: false};
                    return {payload, geracao: await postJson('/api/sentence/generate', payload)};
                }"""
            )
            registro = envio["payload"]["decisoes_acessorias"]["medidas_protetivas"]
            assert registro["fontes"] == [fonte]
            sentenca = envio["geracao"]["sentenca"]
            assert registro["fundamento"] in sentenca["secao_dosimetria"]
            assert "MANTENHO as medidas protetivas" in sentenca["secao_dispositivo"]
            assert not envio["geracao"]["auditoria"]["penas_divergentes"]
        finally:
            browser.close()
