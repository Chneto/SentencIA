# -*- coding: utf-8 -*-
"""Testes E2E da interface de retenção do arquivo morto nas Configurações (TASK-013)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path


from tests.e2e.test_layout_transbordo import (
    DESIGNS,
    OVERFLOW_JS,
    THEMES,
    _LocalAppServer,
    _launch_chromium,
    _set_theme,
)


def _criar_zip_ficticio(pasta: Path, nome: str, conteudo_interno: str = "ficticio") -> Path:
    destino = pasta / nome
    with zipfile.ZipFile(destino, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("arquivo_interno_sigiloso.txt", conteudo_interno)
    return destino


def test_retencao_arquivo_morto_simulacao_confirmacao_remocao_e_erros(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    archive_dir = Path(_live_server._tmp.name) / "cases" / "_arquivados"
    archive_dir.mkdir(parents=True, exist_ok=True)
    for item in archive_dir.iterdir():
        if item.is_file():
            item.unlink()

    zips_nomes = [
        "20261120_100000.zip",
        "20261119_100000.zip",
        "20240101_080000.zip",
        "20230601_080000.zip",
    ]
    for nome in zips_nomes:
        _criar_zip_ficticio(archive_dir, nome, f"conteudo sintetico {nome}")

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            page = context.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")

            # Abre modal de Configurações
            page.click("#btnAppMenu")
            page.click('[data-menu-action="settings"]')
            page.wait_for_selector("#modalSettings", state="visible", timeout=5000)

            # 1) "Aplicar remoção…" começa desabilitado sem simulação
            assert page.is_disabled("#btnRetencaoAplicar")

            # 2) Critérios vazios -> erro 422 exibido com mensagem clara
            page.fill("#retManterDias", "")
            page.fill("#retManterUltimos", "")
            page.click("#btnRetencaoSimular")
            page.wait_for_selector("#retencaoResultado", state="visible", timeout=5000)
            txt_422 = page.text_content("#retencaoResultado") or ""
            assert "critério" in txt_422.lower() or "manter_" in txt_422.lower()
            assert page.is_disabled("#btnRetencaoAplicar")

            # 3) Simular mantendo os 2 mais recentes -> mostra apenas nomes dos zips, nunca conteúdo interno
            page.fill("#retManterUltimos", "2")
            page.click("#btnRetencaoSimular")
            page.wait_for_function(
                "() => (document.getElementById('retencaoResultado')?.textContent || '').includes('Simulação concluída')",
                timeout=5000,
            )
            txt_sim = page.text_content("#retencaoResultado") or ""
            assert "2 mantido(s), 2 elegível(is) para remoção" in txt_sim
            assert "20240101_080000.zip" in txt_sim
            assert "20230601_080000.zip" in txt_sim
            assert "20261120_100000.zip" in txt_sim
            assert "20261119_100000.zip" in txt_sim
            assert "arquivo_interno_sigiloso.txt" not in txt_sim
            assert not page.is_disabled("#btnRetencaoAplicar")

            # 4) Alterar critério após simular desabilita "Aplicar remoção…" novamente
            page.fill("#retManterUltimos", "1")
            assert page.is_disabled("#btnRetencaoAplicar")

            # Volta para 2, simula de novo e abre confirmação destrutiva
            page.fill("#retManterUltimos", "2")
            page.click("#btnRetencaoSimular")
            page.wait_for_function(
                "() => !document.getElementById('btnRetencaoAplicar').disabled",
                timeout=5000,
            )
            page.click("#btnRetencaoAplicar")
            page.wait_for_selector("#retencaoConfirmBox", state="visible", timeout=3000)

            # Botão de execução exige checkbox marcado
            assert page.is_disabled("#btnRetencaoConfirmarExec")
            page.check("#chkRetencaoConfirmar")
            assert not page.is_disabled("#btnRetencaoConfirmarExec")

            # 5) Aplica remoção e valida disco + relatório
            page.click("#btnRetencaoConfirmarExec")
            page.wait_for_function(
                "() => (document.getElementById('retencaoResultado')?.textContent || '').includes('Remoção concluída')",
                timeout=5000,
            )
            txt_exec = page.text_content("#retencaoResultado") or ""
            assert "2 removido(s), 2 mantido(s)" in txt_exec
            assert page.is_disabled("#btnRetencaoAplicar")

            restantes = sorted(p.name for p in archive_dir.glob("*.zip"))
            assert restantes == ["20261119_100000.zip", "20261120_100000.zip"]

            # 6) Erro 409 (carregamento de autos em andamento) exibido com mensagem clara
            page.route(
                "**/api/cases/arquivo-morto/retencao",
                lambda route: route.fulfill(
                    status=409,
                    content_type="application/json",
                    body=json.dumps(
                        {"detail": "Há um carregamento de autos em andamento. Aguarde antes de executar a retenção."}
                    ),
                ),
            )
            page.fill("#retManterUltimos", "1")
            page.click("#btnRetencaoSimular")
            page.wait_for_function(
                "() => (document.getElementById('retencaoResultado')?.textContent || '').includes('carregamento de autos')",
                timeout=5000,
            )
            page.unroute("**/api/cases/arquivo-morto/retencao")

            context.close()
        finally:
            browser.close()


def test_modal_configuracoes_sem_transbordo_nos_4_temas(_live_server: _LocalAppServer):
    """TASK-013: modal de Configurações (com seção Arquivo morto) sem transbordo nos 4 desenhos × claro/escuro."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            page = context.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")

            page.click("#btnAppMenu")
            page.click('[data-menu-action="settings"]')
            page.wait_for_selector("#modalSettings", state="visible", timeout=5000)

            for design in DESIGNS:
                for theme in THEMES:
                    _set_theme(page, theme, design=design)
                    issues = page.evaluate(OVERFLOW_JS)
                    assert issues == [], f"Transbordo no modal Configurações ({design}/{theme}): {issues}"

            context.close()
        finally:
            browser.close()
