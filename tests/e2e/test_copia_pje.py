# -*- coding: utf-8 -*-
"""Testes E2E da cópia para o PJe: ClipboardItem, fallback, Id. x ID- e pendências (TASK-020)."""

from __future__ import annotations


from tests.e2e.test_layout_transbordo import _LocalAppServer, _launch_chromium


def _gerar_sentenca_demo(page) -> None:
    """Garante que o caso demo e uma sentença determinística estão carregados na Estação 4."""
    page.evaluate(
        """async () => {
            const { postJson } = await import('/static/js/modulos/api.js?v=2.1.0');
            const { hooks } = await import('/static/js/modulos/estado.js?v=2.1.0');
            const { renderSentenceOnSheet } = await import('/static/js/modulos/estacao-sentenca.js?v=2.1.0');
            const demo = await postJson('/api/cases/load-demo', {});
            if (hooks.applyActiveCase) await hooks.applyActiveCase(demo, { clearSentence: true });
            const res = await postJson('/api/sentence/generate', { use_ollama: false });
            renderSentenceOnSheet(res);
        }"""
    )
    page.click('.station-btn[data-station="sentenca"]')
    page.wait_for_function(
        "() => (document.getElementById('sentenceRenderBody')?.textContent || '').includes('SENTENÇA')",
        timeout=10000,
    )


def test_copia_pje_clipboard_item_fallback_id_ponto_e_pendencias(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            page = context.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")

            _gerar_sentenca_demo(page)

            # 1) Cópia com ClipboardItem: captura text/html e text/plain
            page.evaluate(
                """() => {
                    window.__pjeCapturado = { writeCalls: [], writeTextCalls: [] };
                    class FakeClipboardItem {
                        constructor(data) { this.data = data; }
                    }
                    window.ClipboardItem = FakeClipboardItem;
                    Object.defineProperty(navigator, 'clipboard', {
                        configurable: true,
                        value: {
                            write: async (items) => {
                                const out = [];
                                for (const item of items) {
                                    const entry = {};
                                    for (const [k, blob] of Object.entries(item.data || {})) {
                                        entry[k] = await blob.text();
                                    }
                                    out.push(entry);
                                }
                                window.__pjeCapturado.writeCalls.push(out);
                            },
                            writeText: async (txt) => {
                                window.__pjeCapturado.writeTextCalls.push(txt);
                            },
                        },
                    });
                }"""
            )

            page.click("#btnCopyPje")
            page.wait_for_function("() => window.__pjeCapturado.writeCalls.length === 1", timeout=5000)

            capturado = page.evaluate("() => window.__pjeCapturado.writeCalls[0][0]")
            assert "text/html" in capturado and "text/plain" in capturado
            assert "<p " in capturado["text/html"]
            assert "data-marca-dagua" not in capturado["text/html"]
            assert "ID-4100201" in capturado["text/html"]
            assert "<" not in capturado["text/plain"]
            assert "\n\n" in capturado["text/plain"]
            assert "SENTENÇA" in capturado["text/plain"]

            # 2) Opção Id. 4100201 nas Configurações
            page.click("#btnAppMenu")
            page.click('#appMainMenu [data-menu-action="settings"]')
            page.wait_for_selector("#modalSettings", state="visible", timeout=5000)
            assert page.input_value("#setFormatoReferenciaPje") == "id_hifen"
            page.select_option("#setFormatoReferenciaPje", "id_ponto")
            page.click('#formSettings button[type="submit"]')
            page.wait_for_selector("#modalSettings", state="hidden", timeout=5000)
            # Aguarda o recarregamento do html_pje com o novo formato
            page.wait_for_timeout(400)

            page.click("#btnCopyPje")
            page.wait_for_function("() => window.__pjeCapturado.writeCalls.length === 2", timeout=5000)
            capturado_id_ponto = page.evaluate("() => window.__pjeCapturado.writeCalls[1][0]")
            assert "Id. 4100201" in capturado_id_ponto["text/html"]
            assert "Id. 4100201" in capturado_id_ponto["text/plain"]
            assert "ID-4100201" not in capturado_id_ponto["text/html"]
            # A folha (texto auditado) continua com ID-4100201
            folha_txt = page.text_content("#sentenceRenderBody") or ""
            assert "ID-4100201" in folha_txt

            # Restaura padrão id_hifen
            page.click("#btnAppMenu")
            page.click('#appMainMenu [data-menu-action="settings"]')
            page.wait_for_selector("#modalSettings", state="visible", timeout=5000)
            page.select_option("#setFormatoReferenciaPje", "id_hifen")
            page.click('#formSettings button[type="submit"]')
            page.wait_for_selector("#modalSettings", state="hidden", timeout=5000)
            page.wait_for_timeout(400)

            # 3) Fallback sem ClipboardItem: copia texto simples (NUNCA HTML bruto com '<') e avisa
            page.evaluate("() => { window.ClipboardItem = undefined; }")
            page.click("#btnCopyPje")
            page.wait_for_function("() => window.__pjeCapturado.writeTextCalls.length === 1", timeout=5000)

            texto_fallback = page.evaluate("() => window.__pjeCapturado.writeTextCalls[0]")
            assert "<" not in texto_fallback
            assert "\n\n" in texto_fallback
            assert "SENTENÇA" in texto_fallback
            toast_txt = page.text_content("#toastContainer") or ""
            assert "Seu navegador copiou só o texto; a formatação não foi incluída." in toast_txt

            # 4) Bloqueio de pendências [COMPLETAR PELO JUÍZO: ...]: exige confirmação
            page.evaluate(
                """() => {
                    const body = document.getElementById('sentenceRenderBody');
                    const p = document.createElement('p');
                    p.textContent = 'Observação pendente: [COMPLETAR PELO JUÍZO: verificar data fictícia].';
                    body.appendChild(p);
                    body.dispatchEvent(new Event('input', { bubbles: true }));
                }"""
            )

            dialogos = []

            def _cancelar(dialog):
                dialogos.append(dialog.message)
                dialog.dismiss()

            page.once("dialog", _cancelar)
            page.click("#btnCopyPje")
            page.wait_for_timeout(500)
            assert len(dialogos) == 1
            assert "1 trecho a completar. Copiar assim mesmo?" in dialogos[0]
            # Cancelado -> não chamou writeText novamente
            assert page.evaluate("() => window.__pjeCapturado.writeTextCalls.length") == 1

            def _confirmar(dialog):
                dialogos.append(dialog.message)
                dialog.accept()

            page.once("dialog", _confirmar)
            page.click("#btnCopyPje")
            page.wait_for_function("() => window.__pjeCapturado.writeTextCalls.length === 2", timeout=5000)
            assert len(dialogos) == 2

            context.close()
        finally:
            browser.close()

def test_duas_abas_copia_pje_rejeita_revisao_anterior(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            primeira = context.new_page()
            primeira.goto(_live_server.base_url, wait_until="networkidle")
            _gerar_sentenca_demo(primeira)
            segunda = context.new_page()
            segunda.goto(_live_server.base_url, wait_until="networkidle")
            revisao_antiga = segunda.evaluate("""async () => {
                const {apiJson} = await import('/static/js/modulos/api.js?v=2.1.0');
                return (await apiJson('/api/sentence/last')).sentenca.revisao_documento;
            }""")
            salta = primeira.evaluate("""async () => {
                const {sincronizarFolha} = await import('/static/js/modulos/folha-editor.js?v=2.1.0');
                const paragrafo = document.querySelector('#sentenceRenderBody p');
                paragrafo.style.textAlign = 'right';
                return await sincronizarFolha();
            }""")
            assert salta
            resultados = segunda.evaluate("""async (anterior) => {
                const {apiFetch, apiJson} = await import('/static/js/modulos/api.js?v=2.1.0');
                const atual = (await apiJson('/api/sentence/last')).sentenca.revisao_documento;
                const antiga = await apiFetch('/api/export/pje?revisao_esperada=' + anterior);
                const nova = await apiFetch('/api/export/pje?revisao_esperada=' + atual);
                return [atual !== anterior, antiga.status, nova.status];
            }""", revisao_antiga)
            assert resultados == [True, 409, 200]
        finally:
            browser.close()
