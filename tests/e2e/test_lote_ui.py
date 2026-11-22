# -*- coding: utf-8 -*-
"""Testes E2E da interface de pré-análise em lote (TASK-018)."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from tests.e2e.test_layout_transbordo import (
    DESIGNS,
    OVERFLOW_JS,
    THEMES,
    _LocalAppServer,
    _launch_chromium,
    _set_theme,
)


def _criar_pdf_sintetico(destino: Path, texto: str) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), texto, fontsize=11)
    doc.save(str(destino))
    doc.close()
    return destino


def test_lote_envio_progresso_status_e_abrir_no_estudio(_live_server: _LocalAppServer, tmp_path: Path):
    from playwright.sync_api import sync_playwright

    pdf_a = _criar_pdf_sintetico(
        tmp_path / "proc_a_sintetico.pdf",
        "PODER JUDICIARIO\nProcesso nº 0803111-11.2025.8.20.0001\n"
        "DENUNCIA\nO Ministerio Publico denuncia JOAO FICTICIO por ameaca art. 147 do CP "
        "contra ANA FICTICIA em 10/01/2025. Recebimento da denuncia em 15/02/2025.",
    )
    pdf_b = _criar_pdf_sintetico(
        tmp_path / "proc_b_sintetico.pdf",
        "PODER JUDICIARIO\nProcesso nº 0803222-22.2025.8.20.0001\n"
        "BOLETIM DE OCORRENCIA\nFato relatado sem denuncia completa.",
    )
    pdf_corrompido = tmp_path / "proc_c_corrompido.pdf"
    pdf_corrompido.write_bytes(b"%PDF-1.4 lixo-corrompido-sem-estrutura-valida")

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            page = context.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")

            # 1) Verifica presença na Paleta de Comandos (Ctrl+K)
            page.keyboard.press("Control+k")
            page.wait_for_selector("#modalCommandPalette", state="visible", timeout=5000)
            page.fill("#cmdPaletteInput", "lote")
            palette_txt = page.text_content("#cmdPaletteList") or ""
            assert "Pré-análise em lote" in palette_txt
            page.click('[data-close-modal="modalCommandPalette"]')

            # 2) Abre pelo menu do logo ("Pré-análise em lote…")
            page.click("#btnAppMenu")
            page.click('[data-menu-action="lote"]')
            page.wait_for_selector("#modalLote", state="visible", timeout=5000)

            # Aviso fixo obrigatório
            aviso = (page.text_content("#loteAvisoFixo") or "").strip()
            assert aviso == "O lote só organiza e pré-analisa; a decisão e a sentença são feitas processo a processo."

            # 3) Seleciona 3 PDFs (2 válidos + 1 corrompido) e inicia pré-análise
            page.set_input_files("#loteFileInput", [str(pdf_a), str(pdf_b), str(pdf_corrompido)])
            assert not page.is_disabled("#btnLoteEnviar")
            page.click("#btnLoteEnviar")

            # Aguarda conclusão do lote (3 de 3)
            page.wait_for_function(
                "() => (document.getElementById('loteProgressText')?.textContent || '').includes('Concluído') "
                "&& (document.getElementById('loteProgressText')?.textContent || '').includes('3 de 3')",
                timeout=15000,
            )

            tabela_txt = page.text_content("#loteTableBody") or ""
            assert "0803111-11.2025.8.20.0001" in tabela_txt
            assert "0803222-22.2025.8.20.0001" in tabela_txt
            assert "proc_c_corrompido.pdf" in tabela_txt
            assert "Falhou" in tabela_txt

            # Botões "Abrir no estúdio": habilitados nos válidos, desabilitado no corrompido
            botoes_habilitados = page.locator("#loteTableBody .btn-lote-abrir:not([disabled])")
            botoes_desabilitados = page.locator("#loteTableBody .btn-lote-abrir[disabled]")
            assert botoes_habilitados.count() == 2
            assert botoes_desabilitados.count() == 1

            # 4) Abre o primeiro processo no estúdio (se houver caso/produção ativa, confirma via caixa de confirmação)
            botoes_habilitados.first.click()
            page.wait_for_function(
                "() => document.getElementById('modalLote')?.hidden === true "
                "|| document.getElementById('loteConfirmTrocaBox')?.hidden === false",
                timeout=8000,
            )
            if not page.is_hidden("#loteConfirmTrocaBox"):
                assert page.is_disabled("#btnLoteConfirmarTroca")
                page.check("#chkLoteConfirmarTroca")
                assert not page.is_disabled("#btnLoteConfirmarTroca")
                page.click("#btnLoteConfirmarTroca")
            page.wait_for_selector("#modalLote", state="hidden", timeout=8000)
            page.wait_for_function(
                "() => (document.getElementById('heroCaseNumber')?.textContent || '').includes('0803111-11.2025.8.20.0001')",
                timeout=8000,
            )

            # 5) Reabre o modal e tenta abrir o segundo processo com caso real já ativo -> exige confirmação (409)
            page.click("#btnAppMenu")
            page.click('[data-menu-action="lote"]')
            page.wait_for_selector("#modalLote", state="visible", timeout=5000)
            page.locator("#loteTableBody .btn-lote-abrir:not([disabled])").nth(1).click()

            page.wait_for_selector("#loteConfirmTrocaBox", state="visible", timeout=5000)
            assert page.is_disabled("#btnLoteConfirmarTroca")
            page.check("#chkLoteConfirmarTroca")
            assert not page.is_disabled("#btnLoteConfirmarTroca")
            page.click("#btnLoteConfirmarTroca")

            page.wait_for_selector("#modalLote", state="hidden", timeout=8000)
            page.wait_for_function(
                "() => (document.getElementById('heroCaseNumber')?.textContent || '').includes('0803222-22.2025.8.20.0001')",
                timeout=8000,
            )

            context.close()
        finally:
            browser.close()


def test_modal_lote_sem_transbordo_nos_4_temas(_live_server: _LocalAppServer):
    """TASK-018: modal de Pré-análise em lote sem transbordo nos 4 desenhos × claro/escuro."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            page = context.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")

            page.click("#btnAppMenu")
            page.click('[data-menu-action="lote"]')
            page.wait_for_selector("#modalLote", state="visible", timeout=5000)

            falhas: list[str] = []
            for design in DESIGNS:
                for theme in THEMES:
                    _set_theme(page, design, theme)
                    page.wait_for_timeout(120)
                    offenders = page.evaluate(OVERFLOW_JS)
                    modal_offenders = [o for o in offenders if o.get("tag") != "BODY"]
                    if modal_offenders:
                        falhas.append(f"{design}/{theme}: {modal_offenders[:3]}")

            assert not falhas, "Transbordo detectado com modalLote aberto:\n" + "\n".join(falhas)
            context.close()
        finally:
            browser.close()
