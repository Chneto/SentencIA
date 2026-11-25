# -*- coding: utf-8 -*-
"""Testes da TASK-067: qualidade do OCR por página, invalidação seletiva do cache e segmentação sem sumário."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pymupdf
import pytest

from app.ingestion import pdf_ingestor as pi


def _png_sintetico(texto: str) -> bytes:
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=600, height=180)
        page.insert_text((30, 100), texto, fontsize=24, color=(0, 0, 0))
        return page.get_pixmap(dpi=150).tobytes("png")
    finally:
        doc.close()


def _pdf_digitalizado(destino: Path, n_paginas: int) -> Path:
    png = _png_sintetico("Documento sintético de teste")
    doc = pymupdf.open()
    try:
        for _ in range(n_paginas):
            page = doc.new_page(width=595, height=842)
            page.insert_image(pymupdf.Rect(40, 80, 555, 220), stream=png)
        doc.save(str(destino))
    finally:
        doc.close()
    return destino


def _pdf_texto(destino: Path, paginas: list[str]) -> Path:
    doc = pymupdf.open()
    try:
        for txt in paginas:
            page = doc.new_page(width=595, height=842)
            if txt:
                page.insert_text((40, 60), txt, fontsize=11)
        doc.save(str(destino))
    finally:
        doc.close()
    return destino


class TestQualidadeECacheSeletivoOCR:
    def test_avaliar_qualidade_ocr_pagina(self):
        assert pi.avaliar_qualidade_ocr_pagina("")["qualidade"] == "vazia"
        assert pi.avaliar_qualidade_ocr_pagina("   \n\t ")["qualidade"] == "vazia"
        assert pi.avaliar_qualidade_ocr_pagina("\ufffd\ufffd ||| ___ ...")["qualidade"] == "baixa"
        assert pi.avaliar_qualidade_ocr_pagina(".. ; : ! @ # $ % ^ & *")["qualidade"] == "baixa"
        assert pi.avaliar_qualidade_ocr_pagina("texto ok")["qualidade"] == "boa"
        assert (
            pi.avaliar_qualidade_ocr_pagina(
                "TERMO DE DECLARAÇÕES da ofendida prestado perante a autoridade policial."
            )["qualidade"]
            == "boa"
        )

    def test_invalidacao_seletiva_reaproveita_paginas_boas_e_refaz_so_falha_e_baixa(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        pdf = _pdf_digitalizado(tmp_path / "autos_misto.pdf", n_paginas=3)
        monkeypatch.setattr(pi, "_motor_ocr", lambda: "winocr")

        chamadas_por_pagina: list[int] = []
        rodada = {"n": 1}

        def fake_ocr(png: bytes, motor: str) -> str:
            # Identifica a ordem da chamada na rodada para simular comportamento por página
            idx = len(chamadas_por_pagina)
            chamadas_por_pagina.append(idx)
            if rodada["n"] == 1:
                if idx == 0:
                    return "DENÚNCIA oferecida pelo Ministério Público contra o réu fictício."
                if idx == 1:
                    raise RuntimeError("falha simulada na pagina 2")
                return "\ufffd\ufffd ||| ..."
            # Rodada 2: só páginas 2 e 3 devem ser reprocessadas
            return "Peça processual legível recuperada na segunda tentativa de OCR."

        monkeypatch.setattr(pi, "_ocr_imagem_png", fake_ocr)

        pecas1 = pi.segmentar_autos_pdf_pje(pdf, usar_cache=True)
        assert len(pecas1) == 1
        assert pecas1[0]["ocr_falhou"] == 1
        assert pecas1[0]["ocr_paginas_falha"] == [2]
        assert pecas1[0]["ocr_baixa_qualidade"] == 1
        assert pecas1[0]["ocr_paginas_baixa_qualidade"] == [3]
        assert len(chamadas_por_pagina) == 3

        cache_dados = json.loads(pi._caminho_cache(pdf).read_text(encoding="utf-8"))
        assert cache_dados["ocr_paginas"]["1"]["qualidade"] == "boa"
        assert cache_dados["ocr_paginas"]["2"]["estado"] == "OCR_FALHOU"
        assert cache_dados["ocr_paginas"]["3"]["qualidade"] == "baixa"

        # Rodada 2 com usar_cache=True: só refaz páginas 2 e 3 (página 1 vem do cache)
        rodada["n"] = 2
        chamadas_por_pagina.clear()
        pecas2 = pi.segmentar_autos_pdf_pje(pdf, usar_cache=True)
        assert len(chamadas_por_pagina) == 2
        assert pecas2[0].get("ocr_falhou", 0) == 0
        assert pecas2[0].get("ocr_baixa_qualidade", 0) == 0
        assert pecas2[0]["ocr_paginas_reconhecidas"] == 3
        assert "DENÚNCIA oferecida pelo Ministério Público" in pecas2[0]["conteudo"]

        # Rodada 3: todas as páginas boas -> não reabre nem o PDF
        with mock.patch.object(pi.pymupdf, "open", side_effect=AssertionError("deveria usar cache")):
            pecas3 = pi.segmentar_autos_pdf_pje(pdf, usar_cache=True)
        assert pecas3 == pecas2

    def test_cache_indisponivel_e_reprocessado_quando_motor_passa_a_existir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        pdf = _pdf_digitalizado(tmp_path / "autos_sem_motor.pdf", n_paginas=2)
        monkeypatch.setattr(pi, "_motor_ocr", lambda: None)

        pecas1 = pi.segmentar_autos_pdf_pje(pdf, usar_cache=True)
        assert pecas1[0]["ocr_estado"] == "OCR_INDISPONIVEL"

        # Motor instalado depois -> usar_cache=True detecta pendência e aplica OCR
        monkeypatch.setattr(pi, "_motor_ocr", lambda: "winocr")
        monkeypatch.setattr(
            pi,
            "_ocr_imagem_png",
            lambda png, motor: "Certidão judicial legível extraída após instalação do OCR.",
        )
        pecas2 = pi.segmentar_autos_pdf_pje(pdf, usar_cache=True)
        assert pecas2[0]["ocr_estado"] == "OCR_CONCLUIDO"
        assert pecas2[0]["ocr_paginas_reconhecidas"] == 2

    def test_invalidar_cache_ocr_paginas_seletivo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        pdf = _pdf_digitalizado(tmp_path / "autos_invalida_p2.pdf", n_paginas=3)
        monkeypatch.setattr(pi, "_motor_ocr", lambda: "winocr")
        chamadas = {"n": 0}

        def fake_ocr(png: bytes, motor: str) -> str:
            chamadas["n"] += 1
            return "Conteúdo processual legível reconhecido pelo motor de OCR."

        monkeypatch.setattr(pi, "_ocr_imagem_png", fake_ocr)
        pi.segmentar_autos_pdf_pje(pdf, usar_cache=True)
        assert chamadas["n"] == 3

        removidas = pi.invalidar_cache_ocr_paginas(pdf, paginas=[2])
        assert removidas == 1

        chamadas["n"] = 0
        pi.segmentar_autos_pdf_pje(pdf, usar_cache=True)
        assert chamadas["n"] == 1


class TestSegmentacaoSemSumario:
    def test_segmenta_por_marcadores_pje_sem_toc(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(pi, "_motor_ocr", lambda: None)
        pdf = _pdf_texto(
            tmp_path / "pje_sem_toc.pdf",
            [
                "MINISTÉRIO PÚBLICO\nDENÚNCIA\nO Ministério Público oferece denúncia contra Fulano.\nNum. 4100101 - Pág. 1",
                "Ante o exposto, requer o recebimento da denúncia.\nNum. 4100101 - Pág. 2",
                "PODER JUDICIÁRIO\nDECISÃO\nRecebo a denúncia em face do acusado.\nNum. 4100102 - Pág. 1",
                "ALEGAÇÕES FINAIS\nA Defesa requer a absolvição sumária.\nNum. 4100103 - Pág. 1",
                "Pede deferimento.\nNum. 4100103 - Pág. 2",
            ],
        )

        pecas = pi.segmentar_autos_pdf_pje(pdf, usar_cache=False)
        ids = [p["doc_id"] for p in pecas]
        assert ids == ["ID-4100101", "ID-4100102", "ID-4100103"]
        assert pecas[0]["folha"] == "fls. 01/02"
        assert pecas[1]["folha"] == "fls. 03/03"
        assert pecas[2]["folha"] == "fls. 04/05"
        assert "Denúncia" in pecas[0]["titulo"]
        assert "Decisão" in pecas[1]["titulo"]
        assert "Alegações Finais" in pecas[2]["titulo"]

    def test_segmenta_por_cabecalhos_judiciais_sem_toc_e_sem_num_pje(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(pi, "_motor_ocr", lambda: None)
        pdf = _pdf_texto(
            tmp_path / "escaneado_sem_toc.pdf",
            [
                "DENÚNCIA\nO Ministério Público Estadual denuncia Beltrano pela prática do fato.",
                "Segue o rol de testemunhas arroladas pela acusação.",
                "LAUDO DE PERÍCIA CRIMINAL\nExame de corpo de delito realizado na vítima.",
                "ALEGAÇÕES FINAIS\nMemoriais apresentados pela defesa técnica do acusado.",
            ],
        )

        pecas = pi.segmentar_autos_pdf_pje(pdf, usar_cache=False)
        assert [p["doc_id"] for p in pecas] == ["ID-SEMTOC-001", "ID-SEMTOC-003", "ID-SEMTOC-004"]
        assert pecas[0]["folha"] == "fls. 01/02"
        assert pecas[1]["folha"] == "fls. 03/03"
        assert pecas[2]["folha"] == "fls. 04/04"

    def test_mantem_peca_unica_quando_nao_ha_fronteiras_claras(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(pi, "_motor_ocr", lambda: None)
        pdf = _pdf_texto(
            tmp_path / "sem_fronteiras.pdf",
            [
                "Texto corrido genérico da página um dos autos processuais fictícios sem título de peça.",
                "Continuação do mesmo texto corrido genérico da página dois sem novo cabeçalho.",
            ],
        )

        pecas = pi.segmentar_autos_pdf_pje(pdf, usar_cache=False)
        assert len(pecas) == 1
        assert pecas[0]["folha"] == "fls. 01/02"

    @pytest.mark.parametrize(
        "texto_pagina",
        [
            "Decisão proferida às fls. 30 manteve a medida.",
            "Denúncia oferecida pelo MP foi recebida em audiência.",
            "Sentença de pronúncia mencionada no relatório.",
            "DECISÃO PROFERIDA ÀS FLS. 30,",
        ],
    )
    def test_frase_comum_nao_abre_peca_sem_toc(self, texto_pagina: str):
        assert pi._detectar_fronteira_sem_toc(texto_pagina) is None

    def test_cabecalho_caixa_alta_e_carimbo_pje_abrem_peca_sem_toc(self):
        det_cab = pi._detectar_fronteira_sem_toc("DECISÃO\nVistos.")
        assert det_cab is not None and det_cab["nova_peca"] is True

        det_pje = pi._detectar_fronteira_sem_toc("Decisão proferida às fls. 30.\nNum. 4100199 - Pág. 1")
        assert det_pje is not None and det_pje["nova_peca"] is True and det_pje["doc_id"] == "ID-4100199"


@pytest.mark.parametrize("com_sumario", [True, False])
def test_memoriais_sem_rotulo_no_corpo_chegam_ao_laya(tmp_path, monkeypatch, com_sumario):
    from app.ingestion.laya_classifier import classificar_pecas_autos, extrair_metadados_processo

    monkeypatch.setattr(pi, "_motor_ocr", lambda: None)
    paginas = [
        "DENÚNCIA\nO Ministério Público oferece denúncia pela ameaça de art. 147 do Código Penal.",
        "DECISÃO\nRecebo a denúncia em face do acusado. Cite-se a defesa para resposta.",
        "Ao Juízo. Defensoria Pública. A defesa requer a absolvição por insuficiência probatória.",
        "Certidão do fim do caderno de teste. Documento sem sinais para definir a espécie.",
    ]
    titulos = ["Denúncia", "Decisão", "Memoriais da defesa", "Peça avulsa"]
    pdf = tmp_path / "autos-ficticios.pdf"
    with pymupdf.open() as doc:
        for indice, corpo in enumerate(paginas, 1):
            pg = doc.new_page()
            pg.insert_text((35, 55), corpo + f"\nNum. {9900200 + indice} - Pág. 1")
        if com_sumario:
            doc.set_toc([[1, f"{titulo} | NUM: {9900200 + i}", i] for i, titulo in enumerate(titulos, 1)])
        doc.save(pdf)

    brutas = pi.segmentar_autos_pdf_pje(pdf, usar_cache=False)
    ids = {p["doc_id"]: p for p in brutas}
    assert ids["ID-9900203"]["folha"] == "fls. 03/03"
    assert ids["ID-9900203"]["alertas"]
    assert "insuficiência probatória" in ids["ID-9900203"]["conteudo"]
    pecas = classificar_pecas_autos(brutas)
    defesa = next(p for p in pecas if p.doc_id == "ID-9900203")
    if not com_sumario:
        assert defesa.categoria == "NAO_IDENTIFICADA"
        ids[defesa.doc_id]["categoria_confirmada"] = "ALEGACOES_FINAIS_DEFESA"
        pecas = classificar_pecas_autos(brutas)
        defesa = next(p for p in pecas if p.doc_id == "ID-9900203")
    assert defesa.categoria == "ALEGACOES_FINAIS_DEFESA"
    meta = extrair_metadados_processo(pecas)
    assert any(t.fonte_id == defesa.doc_id for t in meta.teses_defesa)


def test_duplicata_identica_tem_trilha_e_despacho_com_texto_e_preservado(tmp_path, monkeypatch):
    from app.main import _status_pdf

    monkeypatch.setattr(pi, "_motor_ocr", lambda: None)
    pdf = tmp_path / "duplicata-ficticia.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((30, 40), "Certifico a prática de ato fictício com texto de teste.")
        doc.new_page().insert_text((30, 40), "À defesa para manifestação sobre os documentos novos.")
        doc.new_page().insert_text((30, 40), "Documento avulso fictício com texto para classificação posterior.")
        doc.set_toc([
            [1, "Certidão | NUM: 9900291", 1],
            [1, "Certidão | NUM: 9900291", 1],
            [1, "Despacho", 2],
            [1, "Peça avulsa", 3],
        ])
        doc.save(pdf)

    brutas = pi.segmentar_autos_pdf_pje(pdf, usar_cache=False)
    assert len(brutas) == 3
    assert "documentos novos" in brutas[1]["conteudo"]
    status = _status_pdf(pdf, brutas, "hora fictícia")
    assert len(status["exclusoes_segmentacao"]) == 1
    assert status["exclusoes_segmentacao"][0]["doc_id"] == "ID-9900291"
    assert "fls. 01/01" in status["detalhes"]
