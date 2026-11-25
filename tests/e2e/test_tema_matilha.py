"""Testes E2E e estruturais do Tema Matilha (TASK-099 Fase B)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

import pytest

from tests.e2e.conftest import _LocalAppServer
from tests.e2e.test_layout_transbordo import _launch_chromium

RAIZ = Path(__file__).resolve().parent.parent.parent


def _playwright_ou_skip():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("Playwright não instalado neste ambiente.")
    return sync_playwright


def test_assets_matilha_respeitam_orcamento_e_licenca() -> None:
    pasta = RAIZ / "app" / "static" / "img" / "matilha"
    assert pasta.is_dir()
    arquivos = list(pasta.glob("*.webp"))
    nomes = {p.name for p in arquivos}
    # Matilha 2.0 (TASK-103): os cães são desenho vetorial em código; só os fundos continuam como imagem.
    assert nomes == {"pattern-matilha-claro.webp", "pattern-matilha-escuro.webp"}
    assert (RAIZ / "app" / "static" / "js" / "modulos" / "matilha-caes-svg.js").is_file()
    assert (RAIZ / "app" / "static" / "css" / "temas" / "matilha-caes.css").is_file()
    total = sum(p.stat().st_size for p in arquivos)
    assert total <= 4_000_000, f"Orçamento excedido: {total} bytes (> 4 MB)"
    assert (RAIZ / "docs" / "identidade" / "licenca_matilha.md").is_file()


def test_tema_matilha_easter_eggs_fluxo_exportacao_identica_e_reduced_motion() -> None:
    sync_playwright = _playwright_ou_skip()

    with _LocalAppServer() as server:
        with sync_playwright() as p:
            browser = _launch_chromium(p)
            try:
                context = browser.new_context(viewport={"width": 1366, "height": 768})
                page = context.new_page()
                # O selo do cabeçalho muda com a hora (pug depois das 18h, rottweiler de dia): relógio fixo
                # torna o teste independente do fuso e do horário em que a CI roda.
                page.clock.set_fixed_time(datetime(2026, 3, 10, 20, 0))

                csp_violations = []
                external_urls = []
                page.route(
                    "**/*",
                    lambda route: (
                        external_urls.append(route.request.url) or route.abort()
                        if not (
                            route.request.url.startswith(server.base_url)
                            or route.request.url.startswith("data:")
                            or route.request.url.startswith("blob:")
                        )
                        else route.continue_()
                    ),
                )
                page.add_init_script(
                    """
                    window.__matilhaCsp = [];
                    document.addEventListener("securitypolicyviolation", (ev) => {
                        window.__matilhaCsp.push(ev.violatedDirective);
                    });
                    """
                )

                page.goto(server.base_url, wait_until="networkidle")

                # 1) Gera e exporta sentença demo no tema ORIGINAL para baseline de comparação
                baseline = page.evaluate(
                    """async () => {
                        const { postJson, apiJson, apiFetch } = await import("/static/js/modulos/api.js?v=2.5.0");
                        const { hooks } = await import("/static/js/modulos/estado.js?v=2.5.0");
                        const demo = await postJson("/api/cases/load-demo", {});
                        if (hooks.applyActiveCase) await hooks.applyActiveCase(demo, { clearSentence: true });
                        const gen = await postJson("/api/sentence/generate", { use_ollama: false });
                        const rev = gen.sentenca?.revisao_documento || "";
                        const pje = await apiJson(`/api/export/pje?revisao_esperada=${encodeURIComponent(rev)}`);
                        const htmlResp = await apiFetch("/api/export/html");
                        const docxResp = await apiFetch("/api/export/docx");
                        return {
                            texto: gen.sentenca?.texto_completo || "",
                            pje_plain: pje.texto_plano || pje.plain_text || "",
                            html: await htmlResp.text(),
                            docx_ok: docxResp.ok,
                        };
                    }"""
                )
                assert baseline["texto"] and baseline["docx_ok"]

                # 2) Ativa o tema Matilha pelo menu/API de tema e verifica sons desligados por padrão
                page.evaluate(
                    """async () => {
                        const { applyDesign } = await import("/static/js/modulos/temas.js?v=2.5.0");
                        applyDesign("matilha", { persist: true });
                    }"""
                )
                page.wait_for_selector("#matilhaRibbonBadge", state="attached", timeout=5000)
                assert page.get_attribute("html", "data-design") == "matilha"
                assert page.evaluate('() => document.getElementById("chkMatilhaSons")?.checked') is False

                # 3) Easter egg #1: 3 cliques na logo -> pug inclina cabeça; +4 cliques (7 total) -> zoomies com sprite sheet
                for _ in range(3):
                    page.click("#btnAppMenu")
                page.wait_for_selector('[data-matilha-egg="logo-3"]', timeout=3000)
                page.keyboard.press("Escape")
                for _ in range(4):
                    page.click("#btnAppMenu")
                page.wait_for_selector('[data-matilha-egg="logo-7"]', timeout=3000)
                corredores = page.locator(".matilha-zoomies-runner .mcao")
                assert corredores.count() == 2
                assert corredores.first.get_attribute("data-pose") == "correndo"
                page.keyboard.press("Escape")
                assert page.locator("#matilhaFxOverlay").count() == 0

                # 3b) Fase B2: fundo pastel temático só no canvas (body), nunca na folha da sentença
                page.evaluate('() => document.documentElement.setAttribute("data-theme", "light")')
                bg_claro = page.evaluate(
                    """() => ({
                        body: getComputedStyle(document.body).backgroundImage,
                        sheet: getComputedStyle(document.querySelector(".sentence-paper-wrapper")).backgroundImage,
                        renderBody: getComputedStyle(document.getElementById("sentenceRenderBody")).backgroundImage,
                    })"""
                )
                assert "pattern-matilha-claro.webp" in bg_claro["body"]
                assert bg_claro["sheet"] == "none"
                assert bg_claro["renderBody"] == "none"

                # Modo escuro usa o padrão escuro, mantendo folha da sentença limpa
                page.evaluate('() => document.documentElement.setAttribute("data-theme", "dark")')
                bg_escuro = page.evaluate(
                    """() => ({
                        body: getComputedStyle(document.body).backgroundImage,
                        sheet: getComputedStyle(document.querySelector(".sentence-paper-wrapper")).backgroundImage,
                        renderBody: getComputedStyle(document.getElementById("sentenceRenderBody")).backgroundImage,
                    })"""
                )
                assert "pattern-matilha-escuro.webp" in bg_escuro["body"]
                assert bg_escuro["sheet"] == "none"
                assert bg_escuro["renderBody"] == "none"
                page.evaluate('() => document.documentElement.setAttribute("data-theme", "light")')

                # 3c) Fase B2: seleção de variantes (Pug Branco e Bege + Rottweiler Atlético Feliz) persiste após reload
                page.evaluate(
                    """() => {
                        const selPug = document.getElementById("selMatilhaPug");
                        const selRott = document.getElementById("selMatilhaRott");
                        selPug.value = "branco_bege";
                        selPug.dispatchEvent(new Event("change", { bubbles: true }));
                        selRott.value = "atletico_feliz";
                        selRott.dispatchEvent(new Event("change", { bubbles: true }));
                    }"""
                )
                page.wait_for_function(
                    '() => !!document.querySelector("#matilhaRibbonBadge .mcao[data-variante=\'branco_bege\'], #matilhaRibbonBadge .mcao[data-variante=\'atletico_feliz\']")',
                    timeout=5000,
                )
                variante_selo = page.evaluate(
                    '() => document.querySelector("#matilhaRibbonBadge .mcao")?.getAttribute("data-variante") || ""'
                )
                assert variante_selo in ("branco_bege", "atletico_feliz")

                # A gravação das variantes é agrupada (~150 ms): espera o servidor tê-las antes de recarregar.
                gravado = {}
                for _ in range(25):
                    gravado = page.evaluate(
                        """async () => {
                            const { apiJson } = await import("/static/js/modulos/api.js?v=2.5.0");
                            const s = await apiJson("/api/settings");
                            return { pug: s.matilha_pug, rott: s.matilha_rott };
                        }"""
                    )
                    if gravado == {"pug": "branco_bege", "rott": "atletico_feliz"}:
                        break
                    page.wait_for_timeout(200)
                assert gravado == {"pug": "branco_bege", "rott": "atletico_feliz"}

                page.reload(wait_until="networkidle")
                page.wait_for_selector("#matilhaRibbonBadge", state="attached", timeout=5000)
                persisted = page.evaluate(
                    """async () => {
                        const { apiJson } = await import("/static/js/modulos/api.js?v=2.5.0");
                        const s = await apiJson("/api/settings");
                        return {
                            selPug: document.getElementById("selMatilhaPug")?.value,
                            selRott: document.getElementById("selMatilhaRott")?.value,
                            lsPug: localStorage.getItem("sentencia_matilha_pug"),
                            lsRott: localStorage.getItem("sentencia_matilha_rott"),
                            apiPug: s.matilha_pug,
                            apiRott: s.matilha_rott,
                            badgeVariante: document.querySelector("#matilhaRibbonBadge .mcao")?.getAttribute("data-variante") || "",
                        };
                    }"""
                )
                assert persisted["selPug"] == "branco_bege"
                assert persisted["selRott"] == "atletico_feliz"
                assert persisted["lsPug"] == "branco_bege"
                assert persisted["lsRott"] == "atletico_feliz"
                assert persisted["apiPug"] == "branco_bege"
                assert persisted["apiRott"] == "atletico_feliz"
                assert persisted["badgeVariante"] in ("branco_bege", "atletico_feliz")

                # 4) Easter egg #2: Código Konami (↑↑↓↓←→←→BA)
                page.evaluate("() => document.activeElement?.blur?.()")
                for tecla in (
                    "ArrowUp", "ArrowUp", "ArrowDown", "ArrowDown",
                    "ArrowLeft", "ArrowRight", "ArrowLeft", "ArrowRight",
                    "b", "a",
                ):
                    page.keyboard.press(tecla)
                page.wait_for_selector('[data-matilha-egg="konami"]', timeout=3000)
                page.keyboard.press("Escape")

                # 5) Easter egg #8: Paleta de comandos ("au au", "petisco", "quem é um bom garoto?")
                for termo, esperado in (
                    ("au au", "paleta-au_au"),
                    ("petisco", "paleta-petisco"),
                    ("quem é um bom garoto?", "paleta-bom_garoto"),
                ):
                    page.evaluate(
                        """async () => {
                            const { openCommandPalette } = await import("/static/js/modulos/temas.js?v=2.5.0");
                            openCommandPalette();
                        }"""
                    )
                    page.fill("#cmdPaletteInput", termo)
                    page.locator("#cmdPaletteList .cmd-palette-item").first.click()
                    page.wait_for_selector(f'[data-matilha-egg="{esperado}"]', timeout=3000)
                    page.keyboard.press("Escape")

                # 6) Fluxo principal completo com tema Matilha ligado (análise -> dosimetria -> sentença -> exportar)
                for estacao in ("analise", "dosimetria", "sentenca"):
                    page.click(f'.station-btn[data-station="{estacao}"]')
                matilha_out = page.evaluate(
                    """async () => {
                        const { postJson, apiJson, apiFetch } = await import("/static/js/modulos/api.js?v=2.5.0");
                        const gen = await postJson("/api/sentence/generate", { use_ollama: false });
                        const rev = gen.sentenca?.revisao_documento || "";
                        const pje = await apiJson(`/api/export/pje?revisao_esperada=${encodeURIComponent(rev)}`);
                        const htmlResp = await apiFetch("/api/export/html");
                        const docxResp = await apiFetch("/api/export/docx");
                        return {
                            texto: gen.sentenca?.texto_completo || "",
                            pje_plain: pje.texto_plano || pje.plain_text || "",
                            html: await htmlResp.text(),
                            docx_ok: docxResp.ok,
                        };
                    }"""
                )
                assert matilha_out["docx_ok"]
                assert matilha_out["texto"] == baseline["texto"]
                assert matilha_out["pje_plain"] == baseline["pje_plain"]
                assert matilha_out["html"] == baseline["html"]
                for proibido in (r"\bpug\b", r"\brottweiler\b", r"\bmatilha\b", r"\bzoomies\b", r"\bpetisco\b"):
                    assert not re.search(proibido, matilha_out["texto"], re.IGNORECASE)
                    assert not re.search(proibido, matilha_out["html"], re.IGNORECASE)

                # 7) Com "reduzir movimento", os cães continuam animados (escolha do usuário na TASK-103)
                page.emulate_media(reduced_motion="reduce")
                page.evaluate("() => document.activeElement?.blur?.()")
                for tecla in (
                    "ArrowUp", "ArrowUp", "ArrowDown", "ArrowDown",
                    "ArrowLeft", "ArrowRight", "ArrowLeft", "ArrowRight",
                    "b", "a",
                ):
                    page.keyboard.press(tecla)
                page.wait_for_selector('[data-matilha-egg="konami"]', timeout=3000)
                assert page.locator(".matilha-zoomies-runner .mcao").count() == 2
                page.keyboard.press("Escape")

                # 8) Zero violações de CSP e zero requisições externas
                csp_violations = page.evaluate("() => window.__matilhaCsp")
                assert csp_violations == []
                assert external_urls == []
            finally:
                browser.close()
