"""TASK-066. Estados de processamento e validação na folha da sentença."""

from tests.e2e.test_layout_transbordo import _LocalAppServer, _launch_chromium


def test_tentativa_substituicao_validacao_e_revisao_sao_estados_distintos(_live_server: _LocalAppServer):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")
            page.evaluate(
                """async () => {
                    const { state } = await import('/static/js/modulos/estado.js?v=2.1.0');
                    const { renderAuditDrawer, renderPendenciasPanel, renderSectionalTelemetry } =
                        await import('/static/js/modulos/estacao-sentenca.js?v=2.1.0');
                    state.lastSentence = { texto_completo: "Minuta fictícia para verificação da interface." };
                    state.lastAudit = { aprovada_sem_alucinacao: true, score_confiabilidade: 100 };
                    state.lastPayload = { exportacao_liberada: true };
                    renderPendenciasPanel();
                    renderAuditDrawer();
                    renderSectionalTelemetry({
                        secoes: {
                            relatorio: { status: "gerando", tentativas: 1 },
                            preliminares: { status: "fallback_acionado", fallback: true, aprovada: true, selada: true, tentativas: 3 },
                            merito: { status: "aprovado", aprovada: true, selada: true, tentativas: 1, score: 100 },
                            dosimetria: { status: "pendente" },
                        },
                    });
                }"""
            )
            tentativa = page.locator('[data-secao-telemetria="relatorio"]')
            substituida = page.locator('[data-secao-telemetria="preliminares"]')
            validada = page.locator('[data-secao-telemetria="merito"]')
            pendente = page.locator('[data-secao-telemetria="dosimetria"]')

            expect(tentativa.locator('[data-estado-secao="tentativa"]')).to_have_text("Tentativa em andamento")
            expect(substituida.locator('[data-estado-secao="substituida"]')).to_have_text("Seção substituída")
            expect(substituida.locator('[data-estado-secao="validada"]')).to_have_count(0)
            expect(validada.locator('[data-estado-secao="validada"]')).to_have_text("Seção validada automaticamente")
            expect(pendente).to_contain_text("Tentativas —")
            expect(page.locator("#auditDrawerScore")).to_have_text("Checagens automáticas concluídas")
            expect(page.locator("[data-revisao-judicial]")).to_have_text("Revisão judicial necessária")

            page.route("**/api/ollama/destino", lambda rota: rota.fulfill(json={"remoto": False}))
            status = page.locator("#sentenceGenStatus")
            anterior = status.inner_text()
            tentativas = []

            def falhar_regeneracao(rota):
                tentativas.append(status.inner_text())
                rota.fulfill(status=422, json={"detail": "Falha fictícia na geração."})

            page.route("**/api/sentence/regenerate-section", falhar_regeneracao)
            page.evaluate(
                """async () => {
                    const { regenerarSecao } = await import('/static/js/modulos/estacao-sentenca.js?v=2.1.0');
                    await regenerarSecao();
                }"""
            )
            assert tentativas == ["Tentativa de redação em andamento"]
            expect(status).to_have_text(anterior)
        finally:
            browser.close()
