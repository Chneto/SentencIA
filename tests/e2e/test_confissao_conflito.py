"""TASK-086. Negativa registrada na Análise e "Sim" na Dosimetria: conflito visível e cálculo bloqueado."""

from tests.e2e.test_dosimetria_por_imputacao import _autos_ficticios
from tests.e2e.test_layout_transbordo import _LocalAppServer, _launch_chromium


def test_conflito_de_confissao_aparece_e_bloqueia_simulacao(_live_server: _LocalAppServer, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")
            page.evaluate(
                """async (pdf) => {
                    const { apiJson, postJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                    const dados = Uint8Array.from(atob(pdf), (c) => c.charCodeAt(0));
                    const form = new FormData();
                    form.append('files', new Blob([dados], {type: 'application/pdf'}), 'autos-ficticios.pdf');
                    await apiJson('/api/cases/upload-pdfs', {method: 'POST', body: form});
                    const caso = await apiJson('/api/cases/active');
                    const alvo = caso.metadados.imputacoes[0];
                    await postJson('/api/cases/metadata-override', {
                        manifesto_id: caso.manifesto_caso?.id,
                        metadados: {confissoes_confirmadas: {[alvo.id_imputacao || alvo.codigo]: {tipo: 'negativa'}}},
                    });
                }""",
                _autos_ficticios(tmp_path),
            )
            page.reload(wait_until="networkidle")
            page.click('.station-btn[data-station="dosimetria"]')
            page.locator("#dosimetryCrimeTabs [data-crime-tab]").first.wait_for()
            page.select_option("#selCrimeVeredicto", "condenar")
            assert "negativa do fato" in page.locator("#selCrimeConfissao option[value='auto']").inner_text()
            assert page.locator("[data-conflito-confissao]").count() == 0

            page.select_option("#selCrimeConfissao", "total")
            aviso = page.locator("[data-conflito-confissao]")
            aviso.wait_for()
            assert "negativa do fato" in aviso.inner_text()

            simular = """async () => {
                const { postJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                const { buildDosimetryPayload } = await import('/static/js/modulos/estacao-dosimetria.js?v=2.4.0');
                try {
                    await postJson('/api/dosimetria/simulate', {...buildDosimetryPayload(), data_sentenca: '2024-05-30'});
                    return 'ok';
                } catch (erro) { return String(erro.message || erro); }
            }"""
            assert "Confissão em conflito" in page.evaluate(simular)

            page.select_option("#selCrimeConfissao", "auto")
            assert page.locator("[data-conflito-confissao]").count() == 0
            assert page.evaluate(simular) == "ok"
        finally:
            browser.close()
