# -*- coding: utf-8 -*-
"""Testes E2E do painel de jurisprudência do gabinete (TASK-019)."""

from __future__ import annotations

import json
from pathlib import Path


from tests.e2e.test_layout_transbordo import (
    DESIGNS,
    OVERFLOW_JS,
    THEMES,
    _LocalAppServer,
    _launch_chromium,
    _set_theme,
)


def test_painel_jurisprudencia_fluxo_completo(_live_server: _LocalAppServer, tmp_path: Path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768}, accept_downloads=True)
            page = context.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")

            # 1) Abre o modal do Acervo / Jurisprudência
            page.click("#btnAppMenu")
            page.click('#appMainMenu [data-menu-action="acervo"]')
            page.wait_for_selector("#modalAcervo", state="visible", timeout=5000)
            page.wait_for_selector('#precedentesContainer .juris-card[data-origem="oficial"]', timeout=5000)

            # Itens oficiais têm selo "Acervo oficial" e não têm botões de editar/excluir
            card_oficial = page.locator('#precedentesContainer .juris-card[data-item-id="SUMULA-231-STJ"]')
            assert card_oficial.count() == 1
            assert "Acervo oficial" in (card_oficial.text_content() or "")
            assert card_oficial.locator(".btn-juris-editar").count() == 0
            assert card_oficial.locator(".btn-juris-excluir").count() == 0

            # 2) Busca e filtros (origem, tribunal, texto)
            page.select_option("#jurisFiltroTribunal", "STJ")
            page.fill("#jurisBusca", "231")
            assert page.locator('#precedentesContainer .juris-card[data-item-id="SUMULA-231-STJ"]').count() == 1
            page.select_option("#jurisFiltroOrigem", "gabinete")
            assert "Nenhum precedente encontrado" in (page.text_content("#precedentesContainer") or "")
            page.select_option("#jurisFiltroOrigem", "TODOS")
            page.select_option("#jurisFiltroTribunal", "TODOS")
            page.fill("#jurisBusca", "")

            # 3) Validação ao vivo: normalização + duplicidade com link para o item existente
            page.select_option("#precTribunal", "STJ")
            page.fill("#precReferencia", "sumula n. 231 do stj")
            page.wait_for_selector("#precLiveNorm", state="visible", timeout=3000)
            assert "Súmula 231/STJ" in (page.text_content("#precLiveNorm") or "")
            page.wait_for_selector("#precLiveFeedback", state="visible", timeout=3000)
            fb_txt = page.text_content("#precLiveFeedback") or ""
            assert "Duplicidade" in fb_txt
            assert "SUMULA-231-STJ" in fb_txt

            # Clicar em "Ver item existente" destaca o card oficial
            page.click("#precLiveFeedback .btn-juris-ir-existente")
            page.wait_for_selector('#precedentesContainer .juris-card[data-item-id="SUMULA-231-STJ"].is-highlighted', timeout=3000)

            # 4) Adicionar novo precedente do gabinete
            page.select_option("#precTribunal", "STJ")
            page.select_option("#precTipo", "SUMULA_STJ")
            page.fill("#precReferencia", "sumula 995 do stj")
            page.fill("#precTitulo", "Súmula Teste do Gabinete 995")
            page.fill("#precTexto", "Texto literal suficientemente longo cadastrado pelo gabinete para teste E2E.")
            page.click("#btnPrecSubmit")

            page.wait_for_selector('#precedentesContainer .juris-card[data-origem="gabinete"]', timeout=5000)
            card_gab = page.locator('#precedentesContainer .juris-card[data-origem="gabinete"]').first
            assert "Súmula 995/STJ" in (card_gab.text_content() or "")
            assert "Gabinete" in (card_gab.text_content() or "")
            gab_id = card_gab.get_attribute("data-item-id")
            assert gab_id

            # 5) Editar precedente do gabinete
            card_gab.locator(".btn-juris-editar").click()
            assert "Salvar alterações" in (page.text_content("#btnPrecSubmit") or "")
            page.fill("#precTitulo", "Súmula Teste 995 Editada")
            page.click("#btnPrecSubmit")
            page.wait_for_function(
                "() => (document.getElementById('precedentesContainer')?.textContent || '').includes('Súmula Teste 995 Editada')",
                timeout=5000,
            )

            # 6) Exportar JSON do gabinete
            with page.expect_download(timeout=5000) as dl_info:
                page.click("#btnJurisExportar")
            dl = dl_info.value
            exp_path = tmp_path / "exportado.json"
            dl.save_as(str(exp_path))
            exportado = json.loads(exp_path.read_text(encoding="utf-8"))
            assert exportado["formato_versao"] == 1
            assert any(it["referencia"] == "Súmula 995/STJ" for it in exportado["itens"])

            # 7) Importar JSON com prévia (1 novo + 2 duplicados ignorados) e confirmar
            pacote = {
                "formato_versao": 1,
                "itens": [
                    exportado["itens"][0],
                    {
                        "referencia": "Súmula 231/STJ",
                        "titulo": "Duplicado Oficial",
                        "texto_literal": "Texto suficientemente longo que colide com o acervo oficial.",
                        "tribunal": "STJ",
                    },
                    {
                        "referencia": "Tema 9991/STJ",
                        "titulo": "Tema Importado via UI",
                        "texto_literal": "Enunciado importado via painel com prévia e confirmação explícita.",
                        "tipo": "TEMA_REPETITIVO_STJ",
                        "tribunal": "STJ",
                    },
                ],
            }
            arq_imp = tmp_path / "importar_gabinete.json"
            arq_imp.write_text(json.dumps(pacote, ensure_ascii=False), encoding="utf-8")

            page.set_input_files("#jurisImportFile", str(arq_imp))
            page.wait_for_selector("#jurisImportPreviewBox", state="visible", timeout=5000)
            prev_txt = page.text_content("#jurisImportPreviewBody") or ""
            assert "1 novo(s) entram" in prev_txt
            assert "2 duplicado(s) ignorado(s)" in prev_txt
            assert "Tema 9991/STJ" in prev_txt
            assert not page.is_disabled("#btnJurisImportConfirmar")

            page.click("#btnJurisImportConfirmar")
            page.wait_for_selector("#jurisImportPreviewBox", state="hidden", timeout=5000)
            page.wait_for_function(
                "() => (document.getElementById('precedentesContainer')?.textContent || '').includes('Tema 9991/STJ')",
                timeout=5000,
            )

            # 8) Excluir precedente do gabinete com confirmação
            card_imp = page.locator('#precedentesContainer .juris-card[data-origem="gabinete"]', has_text="Tema 9991/STJ").first
            card_imp.locator(".btn-juris-excluir").click()
            page.wait_for_selector("#jurisDeleteConfirmBox", state="visible", timeout=3000)
            page.click("#btnJurisConfirmarExclusao")
            page.wait_for_selector("#jurisDeleteConfirmBox", state="hidden", timeout=5000)
            page.wait_for_function(
                "() => !(document.getElementById('precedentesContainer')?.textContent || '').includes('Tema 9991/STJ')",
                timeout=5000,
            )

            context.close()
        finally:
            browser.close()


def test_modal_jurisprudencia_sem_transbordo_nos_4_temas(_live_server: _LocalAppServer):
    """TASK-019: painel de jurisprudência sem transbordo nos 4 desenhos × claro/escuro."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            page = context.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")

            page.click("#btnAppMenu")
            page.click('#appMainMenu [data-menu-action="acervo"]')
            page.wait_for_selector("#modalAcervo", state="visible", timeout=5000)
            page.wait_for_selector("#precedentesContainer .juris-card", timeout=5000)

            falhas: list[str] = []
            for design in DESIGNS:
                for theme in THEMES:
                    _set_theme(page, design, theme)
                    page.wait_for_timeout(120)
                    offenders = page.evaluate(OVERFLOW_JS)
                    modal_offenders = [o for o in offenders if o.get("tag") != "BODY"]
                    if modal_offenders:
                        falhas.append(f"{design}/{theme}: {modal_offenders[:3]}")

            assert not falhas, "Transbordo detectado com modalAcervo aberto:\n" + "\n".join(falhas)
            context.close()
        finally:
            browser.close()
