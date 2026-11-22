# -*- coding: utf-8 -*-
"""Testes do hotfix de OCR no Windows (winocr awaitable + falha explícita — TASK-028)."""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
import pytest

from app.ingestion import pdf_ingestor
from app.main import _status_pdf


def _motivo_skip_winocr_real() -> str | None:
    if sys.platform != "win32":
        return "Teste real do winocr aplicável apenas no Windows."
    try:
        import winocr  # noqa: F401
    except Exception:
        return "Pacote opcional 'winocr' não instalado neste ambiente."
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine

        if not OcrEngine.is_language_supported(Language("pt")):
            return "Idioma OCR 'pt' não disponível no Windows desta máquina."
    except Exception:
        # Se a API winrt não responder à sondagem, deixa o teste rodar e validar o motor diretamente.
        pass
    return None


_SKIP_WINOCR_REAL = _motivo_skip_winocr_real()


def _gerar_png_sintetico_com_texto(texto: str) -> bytes:
    """Renderiza uma página sintética com fonte grande e nítida em PNG (sem dados reais)."""
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=800, height=240)
        page.insert_text((40, 120), texto, fontsize=36, color=(0, 0, 0))
        return page.get_pixmap(dpi=200).tobytes("png")
    finally:
        doc.close()


def _criar_pdf_digitalizado_sintetico(destino: Path, n_paginas: int = 2) -> Path:
    """Cria um PDF apenas com imagens (sem camada de texto), exigindo OCR em todas as páginas."""
    png = _gerar_png_sintetico_com_texto("Sentença de teste fictícia nº 123")
    doc = pymupdf.open()
    try:
        for _ in range(n_paginas):
            page = doc.new_page(width=595, height=842)
            page.insert_image(pymupdf.Rect(40, 80, 555, 240), stream=png)
        doc.save(str(destino))
    finally:
        doc.close()
    return destino


@pytest.mark.skipif(_SKIP_WINOCR_REAL is not None, reason=_SKIP_WINOCR_REAL or "")
def test_winocr_real_reconhece_imagem_sintetica_pt():
    """Reproduz o bug de winocr==0.0.15 + winrt-runtime 4.x (IAsyncOperation awaitable)."""
    png = _gerar_png_sintetico_com_texto("Sentença de teste fictícia nº 123")
    texto = pdf_ingestor._ocr_imagem_png(png, "winocr")
    assert "teste" in texto.lower(), f"Texto reconhecido inesperado: {texto!r}"


def test_falha_motor_ocr_em_todas_as_paginas_marca_ocr_falhou_e_detalhes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Quando o motor de OCR falha em todas as páginas digitalizadas, não pode silenciar."""
    pdf_path = _criar_pdf_digitalizado_sintetico(tmp_path / "autos_scan_ficticio.pdf", n_paginas=2)

    monkeypatch.setattr(pdf_ingestor, "_motor_ocr", lambda: "winocr")

    def _falhar_sempre(png_bytes: bytes, motor: str) -> str:
        raise ValueError("a coroutine was expected (simulado)")

    monkeypatch.setattr(pdf_ingestor, "_ocr_imagem_png", _falhar_sempre)

    docs = pdf_ingestor.segmentar_autos_pdf_pje(pdf_path, usar_cache=False)
    assert len(docs) == 1
    doc0 = docs[0]
    assert doc0.get("ocr_falhou") == 2
    assert not doc0.get("ocr_pendente")

    status = _status_pdf(pdf_path, docs, uploaded_at="2026-11-21 16:00:00")
    assert status.get("ocr_falhou") == 2
    assert "2 página(s) com falha no OCR" in status["detalhes"]
