"""Confirmação manual da espécie de uma peça fictícia na estação de autos."""

import base64

import pymupdf

from tests.e2e.test_layout_transbordo import _LocalAppServer, _launch_chromium


def test_indice_confirma_categoria_e_reabre_a_peca(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with pymupdf.open() as pdf:
        blocos = [
            ("Capa e Autuação", "9900197", "Processo nº 0001234-56.2024.8.09.0001."),
            ("Denúncia Ministerial", "9900198", "O Ministério Público oferece denúncia."),
            ("Memoriais", "9900199", "A defesa requer a absolvição. O Promotor de Justiça requer condenação."),
        ]
        toc = []
        for numero, (titulo, id_, conteudo) in enumerate(blocos, 1):
            folha = pdf.new_page()
            folha.insert_textbox(pymupdf.Rect(40, 40, 550, 780), conteudo, fontsize=10)
            toc.append([1, f"{titulo} | NUM: {id_} | 12/04/2024", numero])
        pdf.set_toc(toc)
        arquivo = base64.b64encode(pdf.tobytes()).decode("ascii")

    with sync_playwright() as pw:
        navegador = _launch_chromium(pw)
        try:
            pagina = navegador.new_page()
            pagina.goto(_live_server.base_url, wait_until="networkidle")
            indice = pagina.evaluate(
                """async (pdf) => {
                    const { apiJson } = await import('/static/js/modulos/api.js?v=2.1.0');
                    const dados = Uint8Array.from(atob(pdf), (c) => c.charCodeAt(0));
                    const form = new FormData();
                    form.append("files", new Blob([dados], {type: "application/pdf"}), "memoriais-ficticios.pdf");
                    const caso = await apiJson('/api/cases/upload-pdfs', {method: "POST", body: form});
                    return caso.pecas.map(({doc_id, titulo, categoria}) => ({doc_id, titulo, categoria}));
                }""",
                arquivo,
            )
            assert any(
                p["doc_id"].endswith("9900198") and p["categoria"] == "DENUNCIA"
                for p in indice
            ), indice
            pagina.reload(wait_until="networkidle")
            pagina.locator('[data-subtab="sub-autos-indice"]').click()
            linha = pagina.locator("#pecasListContainer tbody tr").filter(has_text="9900198")
            linha.get_by_text("DENUNCIA", exact=True).first.wait_for()
            linha.get_by_text("Alterar categoria").click()
            linha.locator("select").select_option("CERTIDAO")
            with pagina.expect_response(lambda r: r.url.endswith("/api/cases/document-category")) as evento:
                linha.get_by_role("button", name="Confirmar", exact=True).click()
            assert evento.value.ok
            pagina.reload(wait_until="networkidle")
            pagina.locator('[data-subtab="sub-autos-indice"]').click()
            assert linha.get_by_text("Confirmada", exact=True).is_visible()
            linha.get_by_text("Alterar categoria").click()
            assert linha.locator("select").input_value() == "CERTIDAO"
        finally:
            navegador.close()
