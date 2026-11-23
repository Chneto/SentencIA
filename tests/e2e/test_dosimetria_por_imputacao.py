"""TASK-051. Decisões independentes para dois fatos fictícios no mesmo tipo penal."""

import base64

import pymupdf

from app.generation.hallucination_auditor import auditar_sentenca
from tests.e2e.test_layout_transbordo import _LocalAppServer, _launch_chromium


def _autos_ficticios(tmp_path):
    destino = tmp_path / "duas-imputacoes-ficticias.pdf"
    pecas = [
        ("Capa e Autuação", "9900101", "Processo nº 0001234-56.2024.8.09.0001."),
        (
            "Denúncia Ministerial", "9900102",
            "Processo nº 0001234-56.2024.8.09.0001. O Ministério Público oferece denúncia.\n"
            "Acusado: TICIO FICTICIO, brasileiro. Ofendida: MEVIA FICTICIA, brasileira.\n"
            "Fato 1: No dia 10/03/2024, o acusado teria praticado ameaça, art. 147 do Código Penal.\n"
            "Fato 2: No dia 21/03/2024, o acusado teria praticado outra ameaça, art. 147 do Código Penal.",
        ),
        (
            "Decisão de Recebimento da Denúncia", "9900103",
            "Decisão judicial. Recebo a denúncia quanto aos fatos 1 e 2. Cite-se a defesa. Cumpra-se.",
        ),
    ]
    with pymupdf.open() as pdf:
        toc = []
        for numero, (titulo, id_, conteudo) in enumerate(pecas, 1):
            pagina = pdf.new_page()
            pagina.insert_textbox(pymupdf.Rect(40, 40, 550, 780), conteudo, fontsize=10)
            toc.append([1, f"{titulo} | NUM: {id_} | 12/04/2024", numero])
        pdf.set_toc(toc)
        pdf.save(str(destino))
    return base64.b64encode(destino.read_bytes()).decode("ascii")


def test_dois_fatos_do_mesmo_artigo_da_tela_ate_a_minuta(_live_server: _LocalAppServer, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")
            ids = page.evaluate(
                """async (pdf) => {
                    const { apiJson } = await import('/static/js/modulos/api.js?v=2.1.0');
                    const { chaveCaso, CHAVE_DECISOES_SESSAO } = await import('/static/js/modulos/estacao-dosimetria.js?v=2.1.0');
                    const bytes = Uint8Array.from(atob(pdf), (c) => c.charCodeAt(0));
                    const form = new FormData();
                    form.append("files", new Blob([bytes], {type: "application/pdf"}), "autos-ficticios.pdf");
                    await apiJson('/api/cases/upload-pdfs', {method: "POST", body: form});
                    const caso = await apiJson('/api/cases/active');
                    sessionStorage.setItem(CHAVE_DECISOES_SESSAO, JSON.stringify({
                      caseKey: chaveCaso(caso),
                      crimeConfigs: [{ codigo: "art_147_cp", veredicto: "condenar" }],
                    }));
                    return caso.metadados.imputacoes.map((i) => i.id_imputacao);
                }""",
                _autos_ficticios(tmp_path),
            )
            assert len(ids) == 2 and ids[0] != ids[1]
            page.reload(wait_until="networkidle")
            page.click('.station-btn[data-station="dosimetria"]')
            abas = page.locator("#dosimetryCrimeTabs [data-crime-tab]")
            abas.nth(1).wait_for()
            assert abas.count() == 2
            assert "Fato 1" in abas.nth(0).inner_text()
            assert "Fato 2" in abas.nth(1).inner_text()
            assert "decisões antigas" in page.locator("#dosimetryVeredictoBannerText").inner_text()
            assert page.input_value("#selCrimeVeredicto") == ""

            page.select_option("#selCrimeVeredicto", "condenar")
            abas.nth(1).click()
            assert page.input_value("#selCrimeVeredicto") == ""
            page.select_option("#selCrimeVeredicto", "absolver")
            page.select_option("#selFundArt386", "VII")

            resultado = page.evaluate(
                """async () => {
                    const { apiFetch, postJson } = await import('/static/js/modulos/api.js?v=2.1.0');
                    const { buildDosimetryPayload } = await import('/static/js/modulos/estacao-dosimetria.js?v=2.1.0');
                    const legado = await apiFetch('/api/dosimetria/simulate', {
                      method: 'POST', headers: {'Content-Type': 'application/json'},
                      body: JSON.stringify({crimes_config: [{codigo: "art_147_cp", veredicto: "condenar"}]})
                    });
                    const payload = {...buildDosimetryPayload(), data_sentenca: "2024-05-30", use_ollama: false};
                    const simulacao = await postJson('/api/dosimetria/simulate', payload);
                    const geracao = await postJson('/api/sentence/generate', payload);
                    return {payload, simulacao, geracao, legado: await legado.json()};
                }"""
            )
        finally:
            browser.close()

    assert resultado["legado"]["erro"] == "decisao_judicial_pendente"
    assert resultado["legado"]["crimes_pendentes"] == ids
    assert [i["id_imputacao"] for i in resultado["payload"]["crimes_config"]] == ids
    calculo = resultado["simulacao"]["dosimetria"]["crimes_calculados"]
    assert len(calculo) == 1 and calculo[0]["id_imputacao"] == ids[0]
    prescricao = resultado["simulacao"]["prescricao"]["por_crime"]
    assert prescricao[0]["id_imputacao"] == ids[0]
    assert prescricao[0]["data_fato"] == "2024-03-10"
    assert prescricao[0]["data_recebimento_denuncia"] == "2024-04-12"
    sentenca = resultado["geracao"]["sentenca"]
    assert [(d["id_imputacao"], d["veredicto"]) for d in sentenca["decisoes_crimes"]] == [
        (ids[0], "condenar"), (ids[1], "absolver"),
    ]
    dispositivo = sentenca["secao_dispositivo"].split("\n\n")
    assert any("CONDENAR o réu" in p and "Fato 1" in p for p in dispositivo)
    assert any("ABSOLVER o réu" in p and "Fato 2" in p for p in dispositivo)

    trocado = sentenca["secao_dispositivo"].replace("Fato 1", "<reserva>").replace("Fato 2", "Fato 1").replace("<reserva>", "Fato 2")
    laudo = auditar_sentenca(trocado, [], [], dosimetria_dados=sentenca["dosimetria_dados"])
    assert any("incompatível com a imputação" in d for d in laudo.penas_divergentes)
