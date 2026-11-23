"""Fluxo dos pontos decisivos com o navegador e a API local."""

from __future__ import annotations

from tests.e2e.test_layout_transbordo import OVERFLOW_JS, _LocalAppServer, _launch_chromium


def test_task043_demo_pedidos_pontos_e_trecho(_live_server: _LocalAppServer):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            page.goto(_live_server.base_url, wait_until="networkidle")
            with page.expect_response("**/api/cases/load-demo") as demo:
                page.click("#btnLoadDemo")
            assert demo.value.ok
            meta = demo.value.json()["metadados"]
            page.click('.station-btn[data-station="analise"]')

            painel = page.locator("#sub-analise-pontos")
            expect(painel).to_be_visible()
            expect(painel.get_by_role("columnheader", name="MP pede")).to_be_visible()
            expect(painel.get_by_role("columnheader", name="Defesa pede")).to_be_visible()
            expect(painel.locator(".pd-cartao .pd-fonte").filter(has_text="Fonte:").first).to_be_visible()
            assert page.evaluate(OVERFLOW_JS) == []

            pedido = next(p for p in meta["pedidos_mp"] if p["trecho"] and p["fonte_id"])
            linha = painel.locator(".pd-pedidos li").filter(has_text=pedido["descricao"]).first
            botao = linha.get_by_role("button", name="Ver trecho")
            botao.focus()
            botao.press("Enter")

            gaveta = page.locator("#modalEvidence")
            expect(gaveta).to_be_visible()
            expect(gaveta.locator("#evidenceDrawerTitle")).to_contain_text(pedido["fonte_id"])
            if pedido["folha"]:
                expect(gaveta.locator("#evidenceDrawerMeta")).to_contain_text(pedido["folha"])
            marca = gaveta.locator(".pd-trecho").first
            expect(marca).to_be_visible()
            assert " ".join(pedido["trecho"].split()) in " ".join(marca.inner_text().split())
            page.keyboard.press("Escape")
            expect(gaveta).to_be_hidden()

            modo = page.locator("html").get_attribute("data-theme")
            page.click("#btnToggleTheme")
            expect(page.locator("html")).to_have_attribute("data-theme", "dark" if modo == "light" else "light")
            expect(painel).to_be_visible()
            assert page.evaluate(OVERFLOW_JS) == []
            page.click('.station-btn[data-station="sentenca"]')
            expect(page.locator("#pdAvisoGeracao")).to_contain_text("alterar a pena")
        finally:
            browser.close()


def test_task043_confirmar_e_corrigir_nascimento_grava_override(_live_server: _LocalAppServer):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            page.goto(_live_server.base_url, wait_until="networkidle")

            # A demo não tem certidão de nascimento. Simula uma extração a confirmar
            # na resposta inicial e usa a API real para confirmar e corrigir o dado.
            def com_nascimento(route):
                resposta = route.fetch()
                corpo = resposta.json()
                ponto = next(p for p in corpo["metadados"]["pontos_decisivos"] if p["chave"] == "data_nascimento_reu")
                ponto.update(valor="2005-01-10", estado="a_confirmar")
                antecedentes = next(p for p in corpo["metadados"]["pontos_decisivos"] if p["chave"] == "antecedentes")
                antecedentes.update(valor={"classificacao": "verificar_periodo_depurador"}, estado="a_confirmar")
                confissao = next(p for p in corpo["metadados"]["pontos_decisivos"] if p["chave"] == "confissao")
                confissao.update(valor={"tipo": "total"}, estado="a_confirmar")
                route.fulfill(response=resposta, json=corpo)

            page.route("**/api/cases/load-demo", com_nascimento)
            page.click("#btnLoadDemo")
            page.click('.station-btn[data-station="analise"]')
            cartao = page.locator('[data-ponto="data_nascimento_reu"]')
            expect(cartao).to_have_attribute("data-estado", "pendente")
            antecedentes = page.locator('[data-ponto="antecedentes"]')
            expect(antecedentes).to_contain_text("Verificar período depurador")
            expect(antecedentes.get_by_role("button", name="Confirmar Antecedentes")).to_have_count(0)
            antecedentes.get_by_role("button", name="Corrigir Antecedentes").click()
            expect(page.locator('#pdCorrecao option[value="verificar_data_do_fato"]')).to_have_text("Verificar data do fato")
            page.get_by_role("button", name="Cancelar", exact=True).click()
            expect(page.locator('[data-ponto="confissao"]')).to_contain_text(
                "Confissão aplicada automaticamente: confirme ou corrija."
            )
            with page.expect_response("**/api/cases/metadata-override") as confirmacao:
                cartao.get_by_role("button", name="Confirmar Nascimento do réu").click()
            assert confirmacao.value.ok
            ativos = confirmacao.value.json()["active_case"]
            assert ativos["metadados_override"]["data_nascimento_reu"] == "2005-01-10"
            assert ativos["metadados_override"]["pontos_decisivos_confirmados"]["data_nascimento_reu"] == "2005-01-10"
            expect(cartao).to_have_attribute("data-estado", "confirmado")
            expect(cartao).to_contain_text("10/01/2005")
            expect(cartao).to_contain_text("20 anos na data do fato → atenuante")

            cartao.get_by_role("button", name="Corrigir Nascimento do réu").click()
            dialogo = page.get_by_role("dialog", name="Corrigir nascimento do réu")
            dialogo.get_by_label("Data de nascimento").fill("2004-02-15")
            with page.expect_response("**/api/cases/metadata-override") as correcao:
                dialogo.get_by_role("button", name="Salvar correção").click()
            assert correcao.value.ok
            assert correcao.value.json()["metadados_override"]["data_nascimento_reu"] == "2004-02-15"
            expect(cartao).to_have_attribute("data-estado", "confirmado")
            expect(cartao).to_contain_text("15/02/2004")
        finally:
            browser.close()
