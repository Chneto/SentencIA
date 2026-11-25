"""TASK-103. Mascotes do tema Matilha 2.0 (cães vetoriais): tela inteira, bolinha, arrastar, reações.

Escolhas do usuário: seguem o mouse pela tela; sentam no canto enquanto se digita; bebem, comem, fazem cocô (clique
limpa) e dormem juntinhos; brincam de bolinha (duplo clique em área vazia); podem ser arrastados (clique direto no
cão); perseguem um ao outro; reagem ao programa; sempre animados (mesmo com "reduzir movimento").
"""

from __future__ import annotations

from tests.e2e.test_layout_transbordo import _LocalAppServer, _launch_chromium

MODULO = "/static/js/modulos/matilha-mascotes.js?v=2.5.0"
VW, VH = 1366, 768


def _abrir_matilha(browser, base_url, **contexto):
    page = browser.new_context(viewport={"width": VW, "height": VH}, **contexto).new_page()
    page.goto(base_url, wait_until="networkidle")
    page.evaluate(
        """async () => {
            localStorage.removeItem('sentencia_matilha_mascotes');
            const { applyDesign } = await import('/static/js/modulos/temas.js?v=2.5.0');
            applyDesign('matilha', { persist: false });
        }"""
    )
    page.wait_for_selector("#matilhaPets [data-cao='pug'] .mcao", state="attached", timeout=10000)
    return page


def _estado(page):
    return {c["cao"]: c for c in page.evaluate(f"async () => (await import('{MODULO}')).estadoMascotes()")}


def _ate(page, condicao, timeout: float = 15.0) -> dict:
    """Espera `condicao(estado)` (estado por cão, vindo de estadoMascotes). wait_for_function com função async
    retorna na hora (a Promise conta como verdadeira), por isso a espera é feita aqui."""
    import time

    fim = time.monotonic() + timeout
    while True:
        estado = _estado(page)
        if condicao(estado):
            return estado
        if time.monotonic() > fim:
            raise AssertionError(f"condição não atingida: {estado}")
        page.wait_for_timeout(150)


def _acao(page, cao, acao):
    page.evaluate(f"async () => (await import('{MODULO}')).acaoMascote('{cao}', '{acao}')")


def _esperar_caes(page, condicao: str, timeout: int = 8000) -> None:
    """Espera ``condicao`` sobre ``c`` (lista dos elementos dos cães, com data-estado, data-direcao...)."""
    page.wait_for_function(
        f"() => {{ const c = [...document.querySelectorAll('#matilhaPets [data-cao]')]; return c.length === 2 && ({condicao}); }}",
        timeout=timeout,
    )


def _centro(page, cao):
    caixa = page.locator(f"#matilhaPets [data-cao='{cao}']").bounding_box()
    return caixa["x"] + caixa["width"] / 2, caixa["y"] + caixa["height"] * 0.6


def test_estrutura_cliques_atravessam_fora_do_desenho(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = _abrir_matilha(browser, _live_server.base_url)
            info = page.evaluate(
                """() => {
                    const raiz = document.getElementById('matilhaPets');
                    const pug = raiz.querySelector("[data-cao='pug']");
                    const r = pug.getBoundingClientRect();
                    const noCorpo = document.elementFromPoint(r.x + r.width * 0.48, r.y + r.height * 0.62);
                    const longe = document.elementFromPoint(window.innerWidth / 2, 200);
                    return {
                        caes: [...raiz.querySelectorAll('[data-cao] svg.mcao')].length,
                        potes: [...raiz.querySelectorAll('[data-pote]')].map((p) => p.dataset.pote).sort(),
                        peRaiz: getComputedStyle(raiz).pointerEvents,
                        corpoEhCao: !!(noCorpo && noCorpo.closest("[data-cao='pug']")),
                        longeEhPets: !!(longe && longe.closest('#matilhaPets')),
                    };
                }"""
            )
            assert info["caes"] == 2 and info["potes"] == ["agua", "racao"]
            assert info["peRaiz"] == "none"
            assert info["corpoEhCao"], "o desenho do cão recebe o clique (carinho e arrastar)"
            assert not info["longeEhPets"], "fora dos cães o clique atravessa a camada"
        finally:
            browser.close()


def test_seguem_o_mouse_pela_tela_e_sentam_no_canto_ao_digitar(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = _abrir_matilha(browser, _live_server.base_url)
            page.mouse.move(900, 330)
            estado = _ate(page, lambda e: all(abs(c["x"] - 900) < 170 and abs(c["y"] - 376) < 90 for c in e.values()))
            assert estado["pug"]["x"] < estado["rott"]["x"]
            page.mouse.move(250, 300)
            _esperar_caes(page, "c.every((e) => e.dataset.direcao === 'esquerda')")

            page.evaluate(
                """() => {
                    const campo = document.createElement('input');
                    campo.id = 'campoTesteMascotes';
                    campo.style.cssText = 'position:fixed;top:140px;left:400px;z-index:99999';
                    document.body.appendChild(campo);
                    campo.focus();
                }"""
            )
            _esperar_caes(page, "c.every((e) => e.dataset.estado === 'sentado')", timeout=15000)
            estado = _estado(page)
            assert all(c["x"] < 360 and c["y"] > VH - 40 for c in estado.values()), "sentam no canto inferior esquerdo"
            page.evaluate("() => document.getElementById('campoTesteMascotes').blur()")
            page.mouse.move(1000, 400)
            _esperar_caes(page, "c.every((e) => e.dataset.estado !== 'sentado')")
        finally:
            browser.close()


def test_bolinha_buscam_trazem_e_duplo_clique_em_botao_nao_joga(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = _abrir_matilha(browser, _live_server.base_url)
            botao = page.locator(".station-btn").first
            botao.dblclick()
            assert page.locator("#matilhaPets [data-bolinha]").count() == 0

            page.mouse.move(400, 500)
            page.evaluate(f"async () => (await import('{MODULO}')).jogarBolinha(1100, 300)")
            assert page.locator("#matilhaPets [data-bolinha]").count() == 1
            _esperar_caes(page, "c.some((e) => e.dataset.comBolinha === '1')", timeout=15000)
            _esperar_caes(page, "c.some((e) => e.dataset.acaoConcluida === 'bolinha')", timeout=20000)
            page.wait_for_function("() => !document.querySelector('#matilhaPets .matilha-bolinha:not(.matilha-bolinha-solta)')", timeout=5000)
        finally:
            browser.close()


def test_arrastar_e_carinho(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = _abrir_matilha(browser, _live_server.base_url)
            x, y = _centro(page, "pug")
            page.mouse.move(x, y)
            # Com o cursor em cima, o cão fica parado olhando (não foge do clique) e passa a receber o clique.
            page.wait_for_selector("#matilhaPets [data-cao='pug'].mcao-interativo", timeout=3000)
            x, y = _centro(page, "pug")
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(650, 320, steps=8)
            assert page.locator("#matilhaPets [data-cao='pug']").get_attribute("data-estado") == "carregado"
            page.mouse.up()
            estado = _estado(page)["pug"]
            assert abs(estado["x"] - 650) < 40 and abs(estado["y"] - (320 + 80 * 0.82)) < 40

            rx, ry = _centro(page, "rott")
            page.mouse.move(rx, ry)
            page.wait_for_selector("#matilhaPets [data-cao='rott'].mcao-interativo", timeout=3000)
            rx, ry = _centro(page, "rott")
            page.mouse.click(rx, ry)
            _esperar_caes(page, "c.find((e) => e.dataset.cao === 'rott').dataset.estado === 'feliz'", timeout=3000)
            assert "♥" in page.locator("#matilhaPets [data-cao='rott'] .matilha-pet-balao").inner_text()
        finally:
            browser.close()


def test_beber_comer_coco_e_limpeza(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = _abrir_matilha(browser, _live_server.base_url)
            _acao(page, "pug", "beber")
            _acao(page, "rott", "comer")
            _esperar_caes(
                page,
                "c.find((e) => e.dataset.cao === 'pug').dataset.acaoConcluida === 'beber' && "
                "c.find((e) => e.dataset.cao === 'rott').dataset.acaoConcluida === 'comer'",
                timeout=25000,
            )
            _acao(page, "rott", "coco")
            coco = page.locator("#matilhaPets [data-coco]")
            coco.first.wait_for(timeout=25000)
            assert coco.first.bounding_box()["x"] > VW * 0.7
            coco.first.click()
            page.wait_for_function("() => !document.querySelector('#matilhaPets [data-coco]')", timeout=3000)
        finally:
            browser.close()


def test_dormem_juntinhos_perseguem_e_reagem_ao_programa(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = _abrir_matilha(browser, _live_server.base_url)
            page.mouse.move(700, 400)
            _acao(page, "pug", "dormir")
            _esperar_caes(page, "c.every((e) => e.dataset.estado === 'dormindo')", timeout=15000)
            estado = _estado(page)
            assert abs(estado["pug"]["x"] - estado["rott"]["x"]) < 120, "dormem lado a lado"
            page.mouse.move(600, 380)
            _esperar_caes(page, "c.every((e) => e.dataset.estado !== 'dormindo')")

            _acao(page, "pug", "perseguir")
            _esperar_caes(page, "c.every((e) => e.dataset.estado === 'correndo')", timeout=3000)

            page.evaluate(
                "async () => (await import('/static/js/modulos/dom.js?v=2.5.0')).showToast('Falha fictícia de teste.', 'error')"
            )
            _esperar_caes(page, "c.find((e) => e.dataset.cao === 'rott').dataset.estado === 'latindo'", timeout=3000)
            page.evaluate(f"async () => (await import('{MODULO}')).reagirMascotes('auditoria_aprovada')")
            _esperar_caes(page, "c.every((e) => e.dataset.estado === 'pulando')", timeout=3000)
        finally:
            browser.close()


def test_animados_mesmo_com_reduzir_movimento_e_opcao_desliga(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = _abrir_matilha(browser, _live_server.base_url, reduced_motion="reduce")
            antes = _estado(page)["pug"]["x"]
            page.mouse.move(1150, 400)
            _ate(page, lambda e: abs(e["pug"]["x"] - antes) > 120)
            animados = page.evaluate(
                """() => [...document.querySelectorAll('#matilhaPets .mcao, #matilhaPets .mcao *')]
                    .filter((el) => (getComputedStyle(el).animationName || 'none') !== 'none').length"""
            )
            assert animados > 0, "com 'reduzir movimento' os cães continuam animados (escolha do usuário)"

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
                    const { applyDesign } = await import('/static/js/modulos/temas.js?v=2.5.0');
                    applyDesign('original', { persist: false });
                }"""
            )
            page.wait_for_function("() => !document.getElementById('matilhaPets')", timeout=3000)
        finally:
            browser.close()


_INTERATIVOS_JS = (
    "input,textarea,select,button,a,label,summary,[contenteditable],[tabindex],[role='button'],[role='tab'],"
    "[role='menuitem'],[role='menuitemradio'],[role='option'],[role='switch'],.sentence-paper-wrapper,"
    ".modal-backdrop,.matilha-pet,.toast-item,#appMainMenu"
)


def test_duplo_clique_real_em_area_vazia_joga_a_bolinha(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = _abrir_matilha(browser, _live_server.base_url)
            ponto = page.evaluate(
                """(sel) => {
                    for (let y = 160; y < innerHeight - 120; y += 23) {
                        for (let x = 40; x < innerWidth - 40; x += 29) {
                            const el = document.elementFromPoint(x, y);
                            if (el && !el.closest(sel) && !el.closest('#matilhaPets')) return { x, y };
                        }
                    }
                    return null;
                }""",
                _INTERATIVOS_JS,
            )
            assert ponto, "há algum ponto vazio na tela inicial"
            page.mouse.dblclick(ponto["x"], ponto["y"])
            page.wait_for_selector("#matilhaPets [data-bolinha]", state="attached", timeout=3000)
        finally:
            browser.close()


def test_bolinha_cai_quando_quem_carrega_e_interrompido_e_volta_a_funcionar(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = _abrir_matilha(browser, _live_server.base_url)
            page.mouse.move(400, 500)
            page.evaluate(f"async () => (await import('{MODULO}')).jogarBolinha(1100, 300)")
            estado = _ate(page, lambda e: any(c["comBolinha"] for c in e.values()), timeout=20)
            dono = next(n for n, c in estado.items() if c["comBolinha"])
            outro = "rott" if dono == "pug" else "pug"
            _acao(page, dono, "beber")
            # Quem carregava larga a bolinha (ela fica no chão, sem dono) e o outro cão vai buscá-la.
            _ate(page, lambda e: not e[dono]["comBolinha"], timeout=5)
            _ate(page, lambda e: e[outro]["comBolinha"], timeout=20)
            page.wait_for_function(
                "() => !document.querySelector('#matilhaPets .matilha-bolinha:not(.matilha-bolinha-solta)')",
                timeout=25000,
            )
            page.evaluate(f"async () => (await import('{MODULO}')).jogarBolinha(300, 300)")
            assert page.locator("#matilhaPets .matilha-bolinha:not(.matilha-bolinha-solta)").count() == 1
        finally:
            browser.close()


def test_arraste_termina_se_a_captura_do_ponteiro_se_perde(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = _abrir_matilha(browser, _live_server.base_url)
            x, y = _centro(page, "pug")
            page.mouse.move(x, y)
            page.wait_for_selector("#matilhaPets [data-cao='pug'].mcao-interativo", timeout=3000)
            x, y = _centro(page, "pug")
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + 120, y - 60, steps=6)
            assert page.locator("#matilhaPets [data-cao='pug']").get_attribute("data-estado") == "carregado"
            page.evaluate(
                """() => document.querySelector("#matilhaPets [data-cao='pug']")
                    .dispatchEvent(new PointerEvent('lostpointercapture', { bubbles: true }))"""
            )
            _esperar_caes(page, "c.find((e) => e.dataset.cao === 'pug').dataset.estado !== 'carregado'", timeout=3000)
            page.mouse.up()
        finally:
            browser.close()


def test_auditoria_aprovada_avisada_pela_estacao_faz_os_caes_comemorarem(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = _abrir_matilha(browser, _live_server.base_url)
            page.evaluate(
                """async () => {
                    const { state } = await import('/static/js/modulos/estado.js?v=2.5.0');
                    state.lastAudit = { score_confiabilidade: 100, aprovado: true };
                    document.dispatchEvent(new CustomEvent('sentencia:auditoria'));
                }"""
            )
            _esperar_caes(page, "c.every((e) => e.dataset.estado === 'pulando')", timeout=3000)
            assert page.locator("#matilhaAuditBadge .mcao").count() == 1
        finally:
            browser.close()