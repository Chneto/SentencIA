# -*- coding: utf-8 -*-
"""Testes de progresso ao vivo do OCR, estados explícitos, cancelamento e Refazer OCR (TASK-027)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Dict, List

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.api.jobs import CANCELAR_INGESTAO, INGESTAO_LOCK, PROGRESSO
from app.api.security import TOKEN_HEADER, get_session_token
from app.ingestion import pdf_ingestor
from app.main import ACTIVE_CASE_STATE, STATE_LOCK, _status_pdf, app, set_active_bundle


def _headers_auth() -> Dict[str, str]:
    return {TOKEN_HEADER: get_session_token()}


def _png_sintetico(texto: str) -> bytes:
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=600, height=180)
        page.insert_text((30, 100), texto, fontsize=28, color=(0, 0, 0))
        return page.get_pixmap(dpi=150).tobytes("png")
    finally:
        doc.close()


def _pdf_digitalizado(destino: Path, n_paginas: int = 3, *, primeira_nativa: bool = False) -> Path:
    png = _png_sintetico("Documento sintético de teste")
    doc = pymupdf.open()
    try:
        for i in range(n_paginas):
            page = doc.new_page(width=595, height=842)
            if primeira_nativa and i == 0:
                page.insert_text(
                    (50, 100),
                    "Página nativa com texto suficiente para dispensar o OCR no ingestor de autos processuais fictícios.",
                    fontsize=12,
                )
            else:
                page.insert_image(pymupdf.Rect(40, 80, 555, 220), stream=png)
        doc.save(str(destino))
    finally:
        doc.close()
    return destino


def test_progresso_ocr_pagina_a_pagina_com_timestamps_e_sigilo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pdf_path = _pdf_digitalizado(tmp_path / "autos_scan_3p.pdf", n_paginas=3)
    monkeypatch.setattr(pdf_ingestor, "_motor_ocr", lambda: "winocr")
    monkeypatch.setattr(
        pdf_ingestor,
        "_ocr_imagem_png",
        lambda png, motor: "CONTEUDO_SIGILOSO_NAO_PODE_VAZAR_NA_TELEMETRIA",
    )

    eventos: List[tuple[str, int, int]] = []
    PROGRESSO.reiniciar()
    PROGRESSO.atualizar(active=True, phase="ocr", current_file="autos_scan_3p.pdf", total_files=1)

    def _cb(etapa: str, atual: int, total: int) -> None:
        eventos.append((etapa, atual, total))
        PROGRESSO.atualizar(
            etapa=etapa,
            phase="ocr" if etapa == "ocr" else "segmentando_pdf_ocr",
            pagina_atual=atual,
            paginas_total=total,
        )

    docs = pdf_ingestor.segmentar_autos_pdf_pje(pdf_path, usar_cache=False, ao_progresso=_cb)
    assert len(docs) == 1
    assert docs[0]["ocr_aplicado"] is True
    assert docs[0].get("ocr_estado") == "OCR_CONCLUIDO"
    assert docs[0].get("ocr_paginas_reconhecidas") == 3

    ocr_ev = [e for e in eventos if e[0] == "ocr"]
    assert ocr_ev[0] == ("ocr", 0, 3)
    assert ocr_ev[-1] == ("ocr", 3, 3)

    snap = PROGRESSO.obter()
    assert snap["etapa"] == "ocr"
    assert snap["pagina_atual"] == 3
    assert snap["paginas_total"] == 3
    assert snap["iniciado_em"]
    assert snap["ultimo_avanco_em"]
    # Sigilo: nenhum texto reconhecido pode vazar em PROGRESSO
    assert "CONTEUDO_SIGILOSO" not in str(snap)


def test_sem_motor_marca_ocr_indisponivel_sem_pendente(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pdf_path = _pdf_digitalizado(tmp_path / "autos_sem_motor.pdf", n_paginas=2)
    monkeypatch.setattr(pdf_ingestor, "_motor_ocr", lambda: None)

    docs = pdf_ingestor.segmentar_autos_pdf_pje(pdf_path, usar_cache=False)
    assert len(docs) == 1
    assert docs[0].get("ocr_estado") == "OCR_INDISPONIVEL"
    assert docs[0].get("ocr_indisponivel") == 2

    status = _status_pdf(pdf_path, docs, uploaded_at="21/11/2026 16:30:00")
    assert status["ocr_estado"] == "OCR_INDISPONIVEL"
    assert status["ocr_indisponivel"] == 2
    assert "Motor de OCR não instalado" in status["detalhes"]
    assert "Iniciar SentencIA" in status["detalhes"]
    assert "pendente" not in status["detalhes"].lower()


def test_falha_em_uma_pagina_segue_demais_e_registra_apenas_tipo_excecao(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    pdf_path = _pdf_digitalizado(tmp_path / "autos_falha_p2.pdf", n_paginas=3)
    monkeypatch.setattr(pdf_ingestor, "_motor_ocr", lambda: "winocr")

    chamada = {"n": 0}
    lock = threading.Lock()

    def _ocr_com_falha_na_segunda(png: bytes, motor: str) -> str:
        with lock:
            chamada["n"] += 1
            atual = chamada["n"]
        if atual == 2:
            raise RuntimeError("DADO_CONFIDENCIAL_DA_PARTE_NAO_LOGAR")
        return f"Texto reconhecido #{atual}"

    monkeypatch.setattr(pdf_ingestor, "_ocr_imagem_png", _ocr_com_falha_na_segunda)

    with caplog.at_level(logging.WARNING):
        docs = pdf_ingestor.segmentar_autos_pdf_pje(pdf_path, usar_cache=False)

    assert len(docs) == 1
    doc0 = docs[0]
    assert doc0["ocr_aplicado"] is True
    assert doc0.get("ocr_falhou") == 1
    assert len(doc0.get("ocr_paginas_falha") or []) == 1
    assert doc0.get("ocr_estado") == "OCR_FALHOU"

    status = _status_pdf(pdf_path, docs, uploaded_at="21/11/2026 16:30:00")
    assert status["ocr_estado"] == "OCR_FALHOU"
    assert status["ocr_falhou"] == 1
    assert len(status["ocr_paginas_falha"]) == 1

    # Log registra apenas o tipo da exceção, nunca o conteúdo da mensagem/PDF
    logs = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "RuntimeError" in logs
    assert "DADO_CONFIDENCIAL" not in logs


def test_refazer_ocr_reprocessa_so_paginas_sem_texto_e_respeita_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = _pdf_digitalizado(cases_dir / "processo_hibrido.pdf", n_paginas=2, primeira_nativa=True)

    monkeypatch.setattr("app.main._cases_dir", lambda: cases_dir)
    monkeypatch.setattr(pdf_ingestor, "_motor_ocr", lambda: None)
    docs_iniciais = pdf_ingestor.segmentar_autos_pdf_pje(pdf_path, usar_cache=False)
    status_inicial = _status_pdf(pdf_path, docs_iniciais, uploaded_at="21/11/2026 16:30:00")
    assert status_inicial["ocr_estado"] == "OCR_INDISPONIVEL"

    with STATE_LOCK:
        set_active_bundle(
            {
                "case_id": "REAL-TEST-OCR",
                "is_demo": False,
                "raw_documents": docs_iniciais,
                "transcriptions": [],
                "upload_status": {"pdfs_processo": [status_inicial], "midias_audiencia": []},
            },
            explicit_demo=False,
        )

    client = TestClient(app)

    # 1) Sem motor instalado: deve recusar com erro explícito (503)
    resp_sem_motor = client.post(
        "/api/cases/refazer-ocr",
        json={"filename": "processo_hibrido.pdf"},
        headers=_headers_auth(),
    )
    assert resp_sem_motor.status_code == 503

    # 2) Com INGESTAO_LOCK ocupado: deve devolver 409
    monkeypatch.setattr(pdf_ingestor, "_motor_ocr", lambda: "winocr")
    monkeypatch.setattr("app.main.motor_ocr_disponivel", lambda: "winocr")
    assert INGESTAO_LOCK.acquire(blocking=False)
    try:
        resp_409 = client.post(
            "/api/cases/refazer-ocr",
            json={"filename": "processo_hibrido.pdf"},
            headers=_headers_auth(),
        )
        assert resp_409.status_code == 409
    finally:
        INGESTAO_LOCK.release()

    # 3) Com motor disponível: reprocessa SOMENTE a página 2 (digitalizada), sem refazer a página 1 nativa
    chamadas_ocr = {"n": 0}

    def _ocr_contado(png: bytes, motor: str) -> str:
        chamadas_ocr["n"] += 1
        return "Texto recuperado da página digitalizada 2"

    monkeypatch.setattr(pdf_ingestor, "_ocr_imagem_png", _ocr_contado)
    resp_ok = client.post(
        "/api/cases/refazer-ocr",
        json={"filename": "processo_hibrido.pdf"},
        headers=_headers_auth(),
    )
    assert resp_ok.status_code == 200, resp_ok.text
    body = resp_ok.json()
    assert chamadas_ocr["n"] == 1
    pdfs_status = body["upload_status"]["pdfs_processo"]
    assert pdfs_status[0]["ocr_estado"] == "OCR_CONCLUIDO"
    assert pdfs_status[0]["ocr_indisponivel"] == 0


def test_cancelar_ingestao_interrompe_ocr_entre_paginas(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pdf_path = _pdf_digitalizado(tmp_path / "autos_cancelar.pdf", n_paginas=6)
    monkeypatch.setattr(pdf_ingestor, "_motor_ocr", lambda: "winocr")

    processadas = {"n": 0}
    lock = threading.Lock()
    CANCELAR_INGESTAO.clear()

    def _ocr_e_cancela(png: bytes, motor: str) -> str:
        with lock:
            processadas["n"] += 1
        CANCELAR_INGESTAO.set()
        return "Texto parcial"

    monkeypatch.setattr(pdf_ingestor, "_ocr_imagem_png", _ocr_e_cancela)
    try:
        with pymupdf.open(str(pdf_path)) as doc:
            pdf_ingestor._ocr_paginas(doc, [0, 1, 2, 3, 4, 5], cancelar=CANCELAR_INGESTAO)
        assert processadas["n"] < 6
    finally:
        CANCELAR_INGESTAO.clear()


def test_refazer_ocr_dois_pdfs_e_transcricao_reconstroi_bundle_e_preserva_outro_pdf(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Refazer OCR de 1 PDF num caso com 2 PDFs + 1 transcrição preserva tudo e reclassifica (Laya)."""
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    pdf_a = _pdf_digitalizado(cases_dir / "autos_a_nativo.pdf", n_paginas=1, primeira_nativa=True)
    pdf_b = _pdf_digitalizado(cases_dir / "autos_b_scan.pdf", n_paginas=1, primeira_nativa=False)

    monkeypatch.setattr("app.main._cases_dir", lambda: cases_dir)
    monkeypatch.setattr(pdf_ingestor, "_motor_ocr", lambda: None)
    docs_a = pdf_ingestor.segmentar_autos_pdf_pje(pdf_a, usar_cache=True)
    docs_b = pdf_ingestor.segmentar_autos_pdf_pje(pdf_b, usar_cache=True)
    st_a = _status_pdf(pdf_a, docs_a, uploaded_at="21/11/2026 16:30:00")
    st_b = _status_pdf(pdf_b, docs_b, uploaded_at="21/11/2026 16:31:00")

    transcricao_fake = {
        "media_id": "ID-MIDIA-001",
        "arquivo": "audiencia_ficticia.mp4",
        "depoente": "MARIA FICTICIA",
        "papel": "OFENDIDA",
        "status": "CONCLUIDO",
        "segmentos": [{"inicio": "00:00", "fim": "00:05", "texto": "Relato fictício em audiência."}],
    }
    overrides_fake = {"nome_reu": "JOSE FICTICIO DA SILVA"}

    from app.main import _construir_bundle_real

    bundle_ini = _construir_bundle_real(docs_a + docs_b, [transcricao_fake], overrides_fake)
    set_active_bundle(
        bundle_ini,
        explicit_demo=False,
        upload_status={"pdfs_processo": [st_a, st_b], "midias_audiencia": []},
    )

    monkeypatch.setattr(pdf_ingestor, "_motor_ocr", lambda: "winocr")
    monkeypatch.setattr("app.main.motor_ocr_disponivel", lambda: "winocr")
    monkeypatch.setattr(
        pdf_ingestor,
        "_ocr_imagem_png",
        lambda png, motor: "DENÚNCIA DO MINISTÉRIO PÚBLICO — Processo nº 0800123-45.2025.8.20.5001. Art. 147 do CP.",
    )

    client = TestClient(app)
    resp = client.post(
        "/api/cases/refazer-ocr",
        json={"filename": "autos_b_scan.pdf"},
        headers=_headers_auth(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    with STATE_LOCK:
        bundle_final = ACTIVE_CASE_STATE.get("bundle") or {}

    raw_docs = bundle_final.get("raw_documents") or []
    titulos_raw = " ".join(str(d.get("titulo", "")) for d in raw_docs)
    conteudos_raw = " ".join(str(d.get("conteudo", "")) for d in raw_docs)

    # Peças do PDF A (não refeito) e do PDF B (refeito com OCR) devem ambas estar presentes
    assert "autos_a_nativo.pdf" in titulos_raw
    assert "autos_b_scan.pdf" in titulos_raw
    assert "0800123-45.2025.8.20.5001" in conteudos_raw
    # Transcrição e overrides preservados
    assert len(bundle_final.get("transcriptions") or []) == 1
    assert bundle_final["transcriptions"][0]["media_id"] == "ID-MIDIA-001"
    assert bundle_final.get("metadados_override") == overrides_fake
    # Reconstrução completa (Laya/metadados): número do processo extraído do texto novo do OCR
    assert body.get("numero_processo") == "0800123-45.2025.8.20.5001"


def test_refazer_ocr_em_excecao_encerra_progresso_com_erro(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Se _segmentar_pdf_seguro falhar no meio de /api/cases/refazer-ocr, PROGRESSO não fica preso em active=True."""
    from fastapi import HTTPException

    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    _pdf_digitalizado(cases_dir / "corrompido.pdf", n_paginas=1)

    monkeypatch.setattr("app.main._cases_dir", lambda: cases_dir)
    monkeypatch.setattr(pdf_ingestor, "_motor_ocr", lambda: "winocr")
    monkeypatch.setattr("app.main.motor_ocr_disponivel", lambda: "winocr")

    def _explodir(*args, **kwargs):
        raise HTTPException(status_code=422, detail="PDF corrompido simulado")

    monkeypatch.setattr("app.main._segmentar_pdf_seguro", _explodir)

    client = TestClient(app)
    resp = client.post(
        "/api/cases/refazer-ocr",
        json={"filename": "corrompido.pdf"},
        headers=_headers_auth(),
    )
    assert resp.status_code == 422

    snap = PROGRESSO.obter()
    assert snap["active"] is False
    assert snap["status"] == "error"
    assert "PDF corrompido simulado" in str(snap.get("erro") or "")

