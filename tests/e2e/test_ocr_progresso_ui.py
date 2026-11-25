# -*- coding: utf-8 -*-
"""Testes E2E da visibilidade do OCR: progresso página X de Y, sem avanço >60s, motor ausente e Refazer OCR (TASK-027)."""

from __future__ import annotations

import json

from tests.e2e.test_layout_transbordo import _LocalAppServer, _launch_chromium


def test_ocr_ui_progresso_travamento_motor_ausente_e_quatro_estados(_live_server: _LocalAppServer):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            page = context.new_page()

            estado_status = {"motor": None}

            def _intercept_status(route):
                resp = route.fetch()
                data = resp.json()
                data["ocr"] = {"motor": estado_status["motor"]}
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(data),
                )

            page.route("**/api/status", _intercept_status)
            page.goto(_live_server.base_url, wait_until="networkidle")

            # 1) Motor ausente -> aviso global persistente visível na Estação 1
            page.wait_for_selector("#ocrUnavailableBanner:not([hidden])", timeout=5000)
            banner_txt = page.locator("#ocrUnavailableBanner").inner_text()
            assert "OCR desativado" in banner_txt
            assert "páginas digitalizadas não terão texto" in banner_txt

            # 2) Os 4 estados em badgeStatusArquivo + ausência total de "OCR PENDENTE" + Refazer OCR desabilitado sem motor
            badges = page.evaluate(
                """async () => {
                  const { badgeStatusArquivo, renderUploadStatusTables } = await import('/static/js/modulos/estacao-autos.js?v=2.3.0');
                  const rows = [
                    { filename: 'andamento.pdf', status: 'PROCESSADO', ocr_estado: 'OCR_EM_ANDAMENTO' },
                    { filename: 'concluido.pdf', status: 'PROCESSADO', ocr_estado: 'OCR_CONCLUIDO', ocr_paginas_reconhecidas: 5 },
                    { filename: 'sem_motor.pdf', status: 'PROCESSADO', ocr_estado: 'OCR_INDISPONIVEL', ocr_indisponivel: 3, detalhes: 'Motor de OCR não instalado.' },
                    { filename: 'falha_p3.pdf', status: 'PROCESSADO', ocr_estado: 'OCR_FALHOU', ocr_falhou: 1, ocr_paginas_falha: [3] },
                  ];
                  renderUploadStatusTables({ pdfs_processo: rows, midias_audiencia: [] });
                  return rows.map(r => badgeStatusArquivo(r));
                }"""
            )
            assert "OCR EM ANDAMENTO" in badges[0]
            assert "OCR CONCLUÍDO" in badges[1]
            assert "OCR INDISPONÍVEL" in badges[2] and "badge-danger" in badges[2]
            assert "OCR FALHOU NA PÁGINA 3" in badges[3] and "badge-danger" in badges[3]

            tabela_txt = page.locator("#uploadPdfStatusList").inner_text()
            assert "OCR PENDENTE" not in tabela_txt.upper()

            btn_refazer = page.locator('button[data-refazer-ocr="sem_motor.pdf"]')
            assert btn_refazer.count() == 1
            assert btn_refazer.is_disabled() is True

            # 3) Quando o motor está presente, o aviso some e "Refazer OCR" fica habilitado
            estado_status["motor"] = "winocr"
            page.evaluate(
                """async () => {
                  const { refreshStatus } = await import('/static/js/modulos/telemetria.js?v=2.3.0');
                  const { state } = await import('/static/js/modulos/estado.js?v=2.3.0');
                  state.activeCase = {
                    upload_status: {
                      pdfs_processo: [
                        { filename: 'sem_motor.pdf', status: 'PROCESSADO', ocr_estado: 'OCR_INDISPONIVEL', ocr_indisponivel: 3 }
                      ],
                      midias_audiencia: [],
                    },
                  };
                  await refreshStatus();
                }"""
            )
            assert page.locator("#ocrUnavailableBanner").is_hidden() is True
            assert page.locator('button[data-refazer-ocr="sem_motor.pdf"]').is_disabled() is False

            # 4) Barra de progresso ao vivo com página X de Y e tempo decorrido + aviso de sem avanço (> 60s com relógio simulado)
            progresso_res = page.evaluate(
                """async () => {
                  const { mostrarProgresso, atualizarBarraProgresso } = await import('/static/js/modulos/api.js?v=2.3.0');
                  mostrarProgresso('Iniciando OCR...');
                  const agora = Date.now();
                  // 72s decorridos (1 min 12 s), último avanço há 5s -> sem aviso de travamento
                  atualizarBarraProgresso({
                    active: true,
                    phase: 'ocr',
                    etapa: 'ocr',
                    current_file: 'autos_ficticios.pdf',
                    pagina_atual: 37,
                    paginas_total: 120,
                    percent: 31,
                    iniciado_em: new Date(agora - 72000).toISOString(),
                    ultimo_avanco_em: new Date(agora - 5000).toISOString(),
                  });
                  const faseNormal = document.getElementById('uploadProgressPhase').textContent;
                  const stallNormalOculto = document.getElementById('uploadProgressStall').hidden;

                  // Relógio simulado: último avanço há 75s (> 60s) -> exibe mensagem de sem avanço e botão Cancelar
                  atualizarBarraProgresso({
                    active: true,
                    phase: 'ocr',
                    etapa: 'ocr',
                    current_file: 'autos_ficticios.pdf',
                    pagina_atual: 37,
                    paginas_total: 120,
                    percent: 31,
                    iniciado_em: new Date(agora - 150000).toISOString(),
                    ultimo_avanco_em: new Date(agora - 75000).toISOString(),
                  });
                  const stallVisivel = !document.getElementById('uploadProgressStall').hidden;
                  const stallTexto = document.getElementById('uploadProgressStallText').textContent;
                  const temCancelar = !!document.getElementById('btnCancelarIngestao');
                  return { faseNormal, stallNormalOculto, stallVisivel, stallTexto, temCancelar };
                }"""
            )
            assert "OCR: página 37 de 120" in progresso_res["faseNormal"]
            assert "1 min 12 s" in progresso_res["faseNormal"]
            assert "autos_ficticios.pdf" in progresso_res["faseNormal"]
            assert progresso_res["stallNormalOculto"] is True
            assert progresso_res["stallVisivel"] is True
            assert "Sem avanço há" in progresso_res["stallTexto"]
            assert "cancele e tente de novo" in progresso_res["stallTexto"]
            assert progresso_res["temCancelar"] is True

            context.close()
        finally:
            browser.close()
