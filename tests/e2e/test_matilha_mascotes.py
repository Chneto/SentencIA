"""TASK-100. Mascotes do tema Matilha: seguem o mouse, bebem, comem, fazem cocô no canto e dormem.

Regras: nunca bloqueiam cliques (pointer-events: none, exceto o cocô, que se limpa com um clique); desligáveis nas
Configurações; "reduzir movimento" os mantém parados; somem ao sair do tema.
"""

from __future__ import annotations

from tests.e2e.test_layout_transbordo import _LocalAppServer, _launch_chromium

MODULO = "/static/js/modulos/matilha-mascotes.js?v=2.4.0"


def _abrir_matilha(browser, base_url, **contexto):
    page = browser.new_context(viewport={"width": 1366, "height": 768}, **contexto).new_page()
    page.goto(base_url, wait_until="networkidle")
    page.evaluate(
        """async () => {
            localStorage.removeItem('sentencia_matilha_mascotes');
            const { applyDesign } = await import('/static/js/modulos/temas.js?v=2.4.0');
            applyDesign('matilha', { persist: false });
        }"""
    )
    page.wait_for_selector("#matilhaPets [data-cao='pug']", state="attached", timeout=10000)
    return page


def _estado(page):
    return page.evaluate(f"async () => (await import('{MODULO}')).estadoMascotes()")


def test_mascotes_aparecem_com_potes_e_nao_bloqueiam_cliques(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = _abrir_matilha(browser, _live_server.base_url)
            info = page.evaluate(
                """() => {
                    const raiz = document.getElementById('matilhaPets');
                    const caes = [...raiz.querySelectorAll('[data-cao]')];
                    const pe = (el) => getComputedStyle(el).pointerEvents;
                    const r = caes[0].getBoundingClientRect();
                    const sob = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
                    return {
                        caes: caes.map((c) => c.dataset.cao).sort(),
                        agua: !!raiz.querySelector('[data-pote="agua"]'),
                        racao: !!raiz.querySelector('[data-pote="racao"]'),
                        peRaiz: pe(raiz), peCao: pe(caes[0]),
                        sobEhCao: !!(sob && sob.closest('#matilhaPets')),
                        ariaHidden: raiz.getAttribute('aria-hidden'),
                    };
                }"""
            )
            assert info["caes"] == ["pug", "rott"]
            assert info["agua"] and info["racao"]
            assert info["peRaiz"] == "none" and info["peCao"] == "none"
            assert info["sobEhCao"] is False, "o clique deve atravessar o cão"
            assert info["ariaHidden"] == "true"
        finally:
            browser.close()


def test_mascotes_seguem_o_mouse_e_viram_para_o_lado_certo(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = _abrir_matilha(browser, _live_server.base_url)
            page.mouse.move(1100, 420)
            page.wait_for_function(
                f"""async () => {{
                    const s = (await import('{MODULO}')).estadoMascotes();
                    return s.every((c) => Math.abs(c.x - 1100) < 160);
                }}""",
                timeout=15000,
            )
            estado = {c["cao"]: c for c in _estado(page)}
            assert estado["pug"]["x"] < estado["rott"]["x"], "o pug fica à esquerda do cursor e o rottweiler à direita"
            page.mouse.move(150, 420)
            page.wait_for_timeout(600)
            assert all(c["direcao"] == "esquerda" for c in _estado(page))
        finally:
            browser.close()


def test_beber_comer_coco_e_limpeza(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = _abrir_matilha(browser, _live_server.base_url)
            for cao, acao in (("pug", "beber"), ("rott", "comer")):
                page.evaluate(f"async () => (await import('{MODULO}')).acaoMascote('{cao}', '{acao}')")
            page.wait_for_function(
                """() => {
                    const pug = document.querySelector("#matilhaPets [data-cao='pug']");
                    const rott = document.querySelector("#matilhaPets [data-cao='rott']");
                    return pug.dataset.acaoConcluida === 'beber' && rott.dataset.acaoConcluida === 'comer';
                }""",
                timeout=25000,
            )
            page.evaluate(f"async () => (await import('{MODULO}')).acaoMascote('rott', 'coco')")
            coco = page.locator("#matilhaPets [data-coco]")
            coco.first.wait_for(timeout=25000)
            assert coco.count() == 1
            caixa = coco.first.bounding_box()
            assert caixa["x"] > 1366 * 0.7, "o cocô fica no canto direito"
            assert page.evaluate("() => getComputedStyle(document.querySelector('[data-coco]')).pointerEvents") == "auto"
            coco.first.click()
            page.wait_for_function("() => !document.querySelector('#matilhaPets [data-coco]')", timeout=3000)
        finally:
            browser.close()


def test_mascotes_dormem_acordam_com_o_mouse_e_param_com_reduzir_movimento(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = _abrir_matilha(browser, _live_server.base_url)
            # O modo escuro não adormece os mascotes (seguem o mouse normalmente).
            page.evaluate("() => document.documentElement.setAttribute('data-theme', 'dark')")
            page.mouse.move(900, 420)
            page.wait_for_timeout(800)
            assert any(c["estado"] != "dormindo" for c in _estado(page))
            for cao in ("pug", "rott"):
                page.evaluate(f"async () => (await import('{MODULO}')).acaoMascote('{cao}', 'dormir')")
            page.wait_for_timeout(300)
            assert all(c["estado"] == "dormindo" for c in _estado(page))
            assert page.evaluate("() => document.querySelector(\"#matilhaPets [data-cao='pug'] .matilha-pet-balao\").textContent") == "Zzz"
            page.mouse.move(600, 420)
            page.wait_for_timeout(300)
            assert all(c["estado"] != "dormindo" for c in _estado(page))

            parado = _abrir_matilha(browser, _live_server.base_url, reduced_motion="reduce")
            antes = {c["cao"]: c["x"] for c in _estado(parado)}
            parado.mouse.move(1200, 420)
            parado.wait_for_timeout(1500)
            depois = {c["cao"]: c["x"] for c in _estado(parado)}
            assert depois == antes
        finally:
            browser.close()


def test_opcao_desliga_mascotes_e_sair_do_tema_remove(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = _abrir_matilha(browser, _live_server.base_url)
            page.evaluate(
                """() => {
                    const chk = document.getElementById('chkMatilhaMascotes');
                    chk.checked = false;
                    chk.dispatchEvent(new Event('change', { bubbles: true }));
                }"""
            )
            page.wait_for_function("() => !document.getElementById('matilhaPets')", timeout=3000)
            assert page.evaluate("() => localStorage.getItem('sentencia_matilha_mascotes')") == "0"
            page.evaluate(
                """() => {
                    const chk = document.getElementById('chkMatilhaMascotes');
                    chk.checked = true;
                    chk.dispatchEvent(new Event('change', { bubbles: true }));
                }"""
            )
            page.wait_for_selector("#matilhaPets", state="attached", timeout=3000)
            page.evaluate(
                """async () => {
                    const { applyDesign } = await import('/static/js/modulos/temas.js?v=2.4.0');
                    applyDesign('original', { persist: false });
                }"""
            )
            page.wait_for_function("() => !document.getElementById('matilhaPets')", timeout=3000)
        finally:
            browser.close()
