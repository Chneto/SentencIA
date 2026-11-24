"""Testes E2E (Playwright/Chromium) da TASK-098: validação de decisões acessórias, 422 em PT-BR e deduplicação de toasts."""

from __future__ import annotations

import pytest

from tests.e2e.conftest import _LocalAppServer
from tests.e2e.test_layout_transbordo import _launch_chromium


def _playwright_ou_skip():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("Playwright não instalado neste ambiente.")
    return sync_playwright


def test_acessorias_incompletas_bloqueiam_envio_sem_empilhar_e_traduzem_422() -> None:
    sync_playwright = _playwright_ou_skip()

    with _LocalAppServer() as server:
        with sync_playwright() as p:
            browser = _launch_chromium(p)
            try:
                context = browser.new_context(viewport={"width": 1366, "height": 768})
                page = context.new_page()

                requisicoes_bloqueaveis = []
                page.on(
                    "request",
                    lambda req: requisicoes_bloqueaveis.append(req.url)
                    if any(
                        rota in req.url
                        for rota in (
                            "/api/sentence/generate",
                            "/api/sentence/generate-sectional",
                            "/api/dosimetria/simulate",
                        )
                    )
                    else None,
                )

                page.goto(server.base_url, wait_until="networkidle")

                # Carrega o caso demo para habilitar o editor de Decisões acessórias na Estação 4
                page.evaluate(
                    """async () => {
                        const { postJson } = await import("/static/js/modulos/api.js?v=2.2.1");
                        const { hooks } = await import("/static/js/modulos/estado.js?v=2.2.1");
                        const demo = await postJson("/api/cases/load-demo", {});
                        if (hooks.applyActiveCase) await hooks.applyActiveCase(demo, { clearSentence: true });
                    }"""
                )
                page.click('.station-btn[data-station="sentenca"]')
                page.wait_for_selector("#editorDecisoesAcessorias", state="visible")

                # Limpa requisições anteriores ao teste e toasts existentes
                requisicoes_bloqueaveis.clear()
                page.evaluate('() => { document.getElementById("toastContainer").innerHTML = ""; }')

                # 1) Escolhe "Manter" em Medidas protetivas com Fundamento vazio
                page.evaluate(
                    """() => {
                        const editor = document.getElementById("editorDecisoesAcessorias");
                        editor.open = true;
                        const sel = editor.querySelector('[data-acessorio="medidas_protetivas"] [data-decisao-acessoria]');
                        sel.value = "manter";
                        sel.dispatchEvent(new Event("change", { bubbles: true }));
                    }"""
                )

                # Dispara geração 3x seguidas: NÃO pode fazer requisição e só pode aparecer 1 toast
                for _ in range(3):
                    page.click("#btnGenerateSentence")
                # Também tenta simular dosimetria manualmente
                page.evaluate(
                    """async () => {
                        const { simulateDosimetry } = await import("/static/js/modulos/estacao-dosimetria.js?v=2.2.1");
                        await simulateDosimetry(false);
                    }"""
                )
                page.wait_for_timeout(150)

                assert requisicoes_bloqueaveis == [], f"Não deveria enviar requisição com decisão incompleta: {requisicoes_bloqueaveis}"

                toasts = page.evaluate(
                    '() => [...document.querySelectorAll("#toastContainer .toast-item span")].map((s) => s.textContent)'
                )
                assert toasts.count("Medidas protetivas: informe o fundamento da decisão.") == 1
                assert not any("String should have" in t or "422" in t for t in toasts)

                # Campo de fundamento deve estar destacado com aria-invalid="true"
                invalido = page.evaluate(
                    '() => document.querySelector(\'[data-acessorio="medidas_protetivas"] [data-fundamento-acessorio] textarea\')?.getAttribute("aria-invalid")'
                )
                assert invalido == "true"

                # 2) Preenche fundamento mas sem selecionar peça -> 1 aviso sobre peça
                page.evaluate(
                    """() => {
                        document.getElementById("toastContainer").innerHTML = "";
                        const txt = document.querySelector('[data-acessorio="medidas_protetivas"] [data-fundamento-acessorio] textarea');
                        txt.value = "Persiste a situação de risco à integridade da ofendida.";
                        txt.dispatchEvent(new Event("input", { bubbles: true }));
                    }"""
                )
                page.click("#btnGenerateSentence")
                toasts_peca = page.evaluate(
                    '() => [...document.querySelectorAll("#toastContainer .toast-item span")].map((s) => s.textContent)'
                )
                assert toasts_peca.count("Medidas protetivas: selecione ao menos uma peça dos autos.") == 1
                assert requisicoes_bloqueaveis == []

                # 3) Voltar para "Pendente" não envia nada em dadosAcessorias() e valida ok
                res_pendente = page.evaluate(
                    """async () => {
                        const mod = await import("/static/js/modulos/registro-decisoes-acessorias.js?v=2.2.1");
                        const sel = document.querySelector('[data-acessorio="medidas_protetivas"] [data-decisao-acessoria]');
                        sel.value = "";
                        sel.dispatchEvent(new Event("change", { bubbles: true }));
                        return { validacao: mod.validarDecisoesAcessorias(), dados: mod.dadosAcessorias() };
                    }"""
                )
                assert res_pendente["validacao"]["ok"] is True
                assert res_pendente["dados"] == {}

                # 4) Deduplicação de showToast: 3 chamadas idênticas -> 1 único toast visível
                qtd_toasts = page.evaluate(
                    """async () => {
                        const { showToast } = await import("/static/js/modulos/dom.js?v=2.2.1");
                        document.getElementById("toastContainer").innerHTML = "";
                        showToast("Aviso repetido para deduplicação.", "warn");
                        showToast("Aviso repetido para deduplicação.", "warn");
                        showToast("Aviso repetido para deduplicação.", "warn");
                        return document.querySelectorAll("#toastContainer .toast-item").length;
                    }"""
                )
                assert qtd_toasts == 1

                # 5) Tradução de erro 422 do Pydantic para português claro (sem cortar e sem inglês)
                msg_422 = page.evaluate(
                    """async () => {
                        const { ApiError, formatarErroValidacao422 } = await import("/static/js/modulos/api.js?v=2.2.1");
                        const detail = [
                            {
                                type: "string_too_short",
                                loc: ["body", "custom_dosimetria_override", "decisoes_acessorias", "medidas_protetivas", "fundamento"],
                                msg: "String should have at least 1 character",
                                ctx: { min_length: 1 },
                            },
                            {
                                type: "too_short",
                                loc: ["body", "custom_dosimetria_override", "decisoes_acessorias", "medidas_protetivas", "fontes"],
                                msg: "List should have at least 1 item after validation, not 0",
                                ctx: { min_length: 1 },
                            },
                            {
                                type: "greater_than_equal",
                                loc: ["body", "decisoes_acessorias", "sursis", "prazo_anos"],
                                msg: "Input should be greater than or equal to 2",
                                ctx: { ge: 2 },
                            },
                            {
                                type: "extra_forbidden",
                                loc: ["body", "campo_desconhecido"],
                                msg: "Extra inputs are not permitted",
                            },
                        ];
                        return {
                            direto: formatarErroValidacao422(detail),
                            viaError: new ApiError(422, { detail }).message,
                        };
                    }"""
                )
                assert msg_422["direto"] == msg_422["viaError"]
                texto = msg_422["direto"]
                assert "Decisões acessórias › Medidas protetivas › Fundamento" in texto
                assert "Decisões acessórias › Medidas protetivas › Peças dos autos" in texto
                assert "Decisões acessórias › Suspensão condicional da pena › Prazo em anos" in texto
                assert "valor mínimo permitido: 2" in texto
                assert "campo não reconhecido" in texto
                assert "String should have" not in texto
                assert "List should have" not in texto
                assert "Extra inputs" not in texto
            finally:
                browser.close()
