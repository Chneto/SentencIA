"""TASK-084: OCR não substitui texto nativo legível e curto; a camada escolhida fica registrada. Dados fictícios."""

from pathlib import Path

import pytest

from app.ingestion import pdf_ingestor as pi
from tests.test_ingestao_transcricao_v2 import _criar_pdf

NATIVO = "Cumprido o alvará em 11/02/2025."
OCR_TROCADO = "Cumprido o alvará em 11/02/2023."
OCR_LONGO = (
    "CERTIDÃO. Certifico que, nesta data, foi cumprido o alvará de soltura expedido nos autos fictícios, "
    "tendo sido o custodiado posto em liberdade às 14h, conforme termo assinado pelo oficial de justiça. "
    "Nada mais havendo, lavrei a presente certidão."
)


@pytest.mark.parametrize(("nativo", "ocr", "camada"), [
    (NATIVO, OCR_TROCADO, "nativa"),
    (NATIVO, "@@ ## ~~ %% ¨¨ 11/0", "nativa"),
    ("", OCR_TROCADO, "ocr"),
    ("fl. 3", OCR_TROCADO, "ocr"),
    ("Assinado digitalmente.", OCR_LONGO, "ocr"),
    (NATIVO, "", "nativa"),
])
def test_escolha_da_camada_por_qualidade_das_duas(nativo, ocr, camada):
    assert pi.escolher_camada_texto(nativo, ocr) == camada


def _ocr_simulado(monkeypatch, texto):
    monkeypatch.setattr(pi, "_motor_ocr", lambda: "motor-teste")
    monkeypatch.setattr(pi, "_ocr_imagem_png", lambda png, motor: texto)


def test_segmentacao_mantem_linha_nativa_e_registra_procedencia(tmp_path: Path, monkeypatch):
    pdf = _criar_pdf(
        tmp_path / "autos.pdf",
        ["CAPA " * 20, NATIVO],
        toc=[[1, "CABEÇALHO", 1], [1, "Certidão | NUM: 300 | 11/02/2025", 2]],
    )
    _ocr_simulado(monkeypatch, OCR_TROCADO)
    paginas_ocr: dict = {}
    doc = pi.pymupdf.open(str(pdf))
    try:
        pecas = pi._segmentar_por_toc(doc, doc.get_toc(), ocr_paginas_out=paginas_ocr)
    finally:
        doc.close()
    certidao = next(p for p in pecas if p["doc_id"] == "ID-300")
    assert "11/02/2025" in certidao["conteudo"] and "11/02/2023" not in certidao["conteudo"]
    assert certidao["ocr_aplicado"] is False
    assert paginas_ocr["2"]["camada"] == "nativa"


def test_extracao_hibrida_mantem_linha_nativa(tmp_path: Path, monkeypatch):
    pdf = _criar_pdf(tmp_path / "solto.pdf", [NATIVO])
    _ocr_simulado(monkeypatch, OCR_TROCADO)
    paginas_ocr: dict = {}
    pagina = pi.extrair_texto_pdf_hibrido(pdf, ocr_paginas_out=paginas_ocr)[0]
    assert "11/02/2025" in pagina["conteudo"] and "11/02/2023" not in pagina["conteudo"]
    assert pagina["ocr_aplicado"] is False
    assert paginas_ocr["1"]["camada"] == "nativa"


def test_pagina_digitalizada_com_carimbo_nativo_usa_ocr(tmp_path: Path, monkeypatch):
    pdf = _criar_pdf(tmp_path / "scan.pdf", ["Assinado digitalmente."])
    _ocr_simulado(monkeypatch, OCR_LONGO)
    paginas_ocr: dict = {}
    pagina = pi.extrair_texto_pdf_hibrido(pdf, ocr_paginas_out=paginas_ocr)[0]
    assert pagina["ocr_aplicado"] is True and "posto em liberdade" in pagina["conteudo"]
    assert paginas_ocr["1"]["camada"] == "ocr"
