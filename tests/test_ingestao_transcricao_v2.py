"""Testes da v2 — Ingestão (PDF/Laya) e Transcrição/Prova Oral (Agente D).

Cobrem: cache por sha256, data_peca vazia, fechamento do PDF em exceção, OCR por plataforma com pool,
níveis do TOC, Laya sem dados fictícios e com ``is_demo`` explícito, transcrição local primeiro,
consentimento para motores externos, falha honesta (status FALHOU), IDs de mídia estáveis por
conteúdo, papel por tokens inteiros e matriz oral que ignora transcrições vazias.
"""

from __future__ import annotations

import json
import os
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pymupdf
import pytest

import app.ingestion.pdf_ingestor as pi
import app.transcription.audio_transcriber as at
from app.demo.demo_case import get_demo_case_bundle
from app.ingestion.laya_classifier import (
    PecaClassificada,
    _extrair_crimes_dinamicamente,
    classificar_pecas_autos,
    extrair_metadados_processo,
)
from app.transcription.oral_proof_analyzer import (
    consolidar_prova_oral_por_interlocutor,
    gerar_matriz_corroboracao_oral,
)


# ==============================================================================================
# Utilidades
# ==============================================================================================
TEXTO_DENUNCIA = (
    "O MINISTERIO PUBLICO VEM OFERECER DENUNCIA contra FULANO DE TAL pela pratica de crime em "
    "contexto de violencia domestica e familiar contra a mulher, conforme narrado a seguir."
)
TEXTO_CONTINUACAO = (
    "Continuacao da peca acusatoria com a descricao pormenorizada dos fatos e o rol de testemunhas "
    "arroladas pela acusacao para a audiencia de instrucao."
)
TEXTO_DECISAO = (
    "DECISÃO. Presentes os requisitos legais, RECEBO A DENÚNCIA oferecida pelo Ministério Público. "
    "Cite-se o acusado para responder à acusação no prazo legal de dez dias."
)


def _criar_pdf(caminho: Path, paginas: list[str], toc: list[list] | None = None) -> Path:
    doc = pymupdf.open()
    for texto in paginas:
        page = doc.new_page()
        if texto:
            page.insert_textbox(pymupdf.Rect(40, 40, 560, 800), texto, fontsize=10)
    if toc:
        doc.set_toc(toc)
    doc.save(str(caminho))
    doc.close()
    return caminho


def _peca(doc_id: str, categoria: str, conteudo: str, titulo: str = "", data_peca: str = "", fase: str = "JUDICIAL") -> PecaClassificada:
    return PecaClassificada(
        doc_id=doc_id,
        folha="fl. 1",
        titulo=titulo or categoria,
        data_peca=data_peca,
        fase=fase,
        categoria=categoria,
        laya_confidence=0.9,
        resumo_estruturado="",
        conteudo=conteudo,
    )


@pytest.fixture
def sem_ocr(monkeypatch):
    monkeypatch.setattr(pi, "_motor_ocr", lambda: None)


# ==============================================================================================
# PDF
# ==============================================================================================
class TestPdfIngestor:
    def test_toc_respeita_niveis_e_data_vazia_quando_ausente(self, tmp_path, sem_ocr):
        pdf = _criar_pdf(
            tmp_path / "autos.pdf",
            [
                "CAPA DO PROCESSO PJE - Processo 0801234-56.2024.8.20.0001 - partes e assuntos cadastrados no sistema.",
                TEXTO_DENUNCIA,
                "ANEXO",
                TEXTO_CONTINUACAO,
                TEXTO_DECISAO,
            ],
            toc=[
                [1, "CABEÇALHO", 1],
                [1, "Denúncia | NUM: 111 | 04/06/2024", 2],
                [2, "Anexo | NUM: 112", 3],
                [1, "Decisão | NUM: 113", 5],
            ],
        )
        pecas = pi.segmentar_autos_pdf_pje(pdf)
        por_id = {p["doc_id"]: p for p in pecas}

        den = por_id["ID-111"]
        # O filho (nível 2) não trunca o pai: a denúncia vai até a página anterior ao próximo nível 1.
        assert den["folha"] == "fls. 02/04"
        assert "Continuacao da peca acusatoria" in den["conteudo"]
        assert den["data_peca"] == "2024-06-04"

        dec = por_id["ID-113"]
        assert dec["data_peca"] == ""  # sem data no marcador: vazio, nunca a data de hoje
        assert "ID-112" not in por_id  # anexo de 1 linha é irrelevante

    def test_cache_validado_por_sha256_e_esquema(self, tmp_path, sem_ocr):
        pdf = _criar_pdf(
            tmp_path / "autos.pdf",
            ["CAPA " * 20, TEXTO_DENUNCIA, TEXTO_DECISAO],
            toc=[[1, "CABEÇALHO", 1], [1, "Denúncia | NUM: 111 | 04/06/2024", 2], [1, "Decisão | NUM: 113", 3]],
        )
        pecas1 = pi.segmentar_autos_pdf_pje(pdf)
        cache_file = pdf.with_name(pdf.name + ".pje_cache_v2.json")
        dados = json.loads(cache_file.read_text(encoding="utf-8"))
        assert dados["schema_version"] == pi.PDF_CACHE_SCHEMA
        assert dados["sha256"] == pi.sha256_arquivo(pdf)

        # Cache válido: não reabre o PDF.
        with mock.patch.object(pi.pymupdf, "open", side_effect=AssertionError("não deveria abrir")):
            assert pi.segmentar_autos_pdf_pje(pdf) == pecas1

        # Mesmo tamanho, conteúdo diferente -> cache invalidado (antes era validado só pelo tamanho).
        dados["pecas"] = [{"doc_id": "ID-ADULTERADO"}]
        dados["sha256"] = "0" * 64
        cache_file.write_text(json.dumps(dados), encoding="utf-8")
        assert all(p["doc_id"] != "ID-ADULTERADO" for p in pi.segmentar_autos_pdf_pje(pdf))

    def test_cache_de_esquema_antigo_por_tamanho_e_ignorado(self, tmp_path, sem_ocr):
        pdf = _criar_pdf(
            tmp_path / "autos.pdf",
            ["CAPA " * 20, TEXTO_DENUNCIA, TEXTO_DECISAO],
            toc=[[1, "CABEÇALHO", 1], [1, "Denúncia | NUM: 111", 2], [1, "Decisão | NUM: 113", 3]],
        )
        legado = {"schema_version": 5, "size": pdf.stat().st_size, "pecas": [{"doc_id": "ID-LEGADO", "data_peca": "2099-01-01"}]}
        pdf.with_name(pdf.name + ".pje_cache_v2.json").write_text(json.dumps(legado), encoding="utf-8")
        pecas = pi.segmentar_autos_pdf_pje(pdf)
        assert all(p["doc_id"] != "ID-LEGADO" for p in pecas)

    def test_documento_fechado_mesmo_em_excecao(self, tmp_path):
        arq = tmp_path / "quebrado.pdf"
        arq.write_bytes(b"%PDF-1.4 conteudo")
        doc = mock.MagicMock()
        doc.get_toc.side_effect = RuntimeError("TOC corrompido")
        with mock.patch.object(pi.pymupdf, "open", return_value=doc):
            with pytest.raises(RuntimeError):
                pi.segmentar_autos_pdf_pje(arq)
        doc.close.assert_called_once()

    def test_ocr_paralelo_por_plataforma_preenche_paginas_digitalizadas(self, tmp_path, monkeypatch):
        pdf = _criar_pdf(
            tmp_path / "scan.pdf",
            ["CAPA " * 20, "", "", TEXTO_DECISAO],
            toc=[[1, "CABEÇALHO", 1], [1, "Petição Inicial | NUM: 200 | 01/02/2024", 2], [1, "Decisão | NUM: 201", 4]],
        )
        chamadas = []

        def fake_ocr(png: bytes, motor: str) -> str:
            chamadas.append(motor)
            return "AUTO DE PRISÃO EM FLAGRANTE lavrado pela autoridade policial com termos de declarações."

        monkeypatch.setattr(pi, "_motor_ocr", lambda: "motor-teste")
        monkeypatch.setattr(pi, "_ocr_imagem_png", fake_ocr)
        pecas = pi.segmentar_autos_pdf_pje(pdf, usar_cache=False)
        apf = next(p for p in pecas if p["doc_id"] == "ID-200")
        assert apf["ocr_aplicado"] is True
        assert "[Pág. 2 - OCR]" in apf["conteudo"] and "[Pág. 3 - OCR]" in apf["conteudo"]
        assert chamadas == ["motor-teste", "motor-teste"]
        # O pool é reutilizado entre chamadas.
        assert pi._obter_pool_ocr() is pi._obter_pool_ocr()

    def test_timeout_do_ocr_descarta_pool_e_aborta_apos_timeouts_seguidos(self, tmp_path, monkeypatch):
        pdf = _criar_pdf(tmp_path / "scan_lento.pdf", [""] * 12)
        monkeypatch.setattr(pi, "_motor_ocr", lambda: "motor-teste")
        monkeypatch.setattr(pi, "_OCR_TIMEOUT_S", 0.3)
        liberar = __import__("threading").Event()
        chamadas = []

        def travado(png: bytes, motor: str) -> str:
            chamadas.append(1)
            liberar.wait(5)
            return ""

        monkeypatch.setattr(pi, "_ocr_imagem_png", travado)
        pool_antigo = pi._obter_pool_ocr()
        doc = pymupdf.open(str(pdf))
        try:
            inicio = time.monotonic()
            res = pi._ocr_paginas(doc, list(range(12)))
            assert time.monotonic() - inicio < 6.0
            assert res == {}
            assert len(chamadas) < 12  # abortou: não tentou todas as páginas
            assert pi._OCR_POOL is not pool_antigo  # pool preso foi descartado
        finally:
            liberar.set()
            doc.close()

        # Motor saudável depois: o novo pool processa normalmente.
        monkeypatch.setattr(pi, "_ocr_imagem_png", lambda png, motor: "texto ok")
        doc = pymupdf.open(str(pdf))
        try:
            assert pi._ocr_paginas(doc, [0, 1]) == {0: "texto ok", 1: "texto ok"}
        finally:
            doc.close()

    def test_sem_motor_ocr_marca_pendente_sem_inventar_texto(self, tmp_path, sem_ocr):
        pdf = _criar_pdf(tmp_path / "scan.pdf", ["", ""])
        paginas = pi.extrair_texto_pdf_hibrido(pdf)
        assert all(p["ocr_aplicado"] is False for p in paginas)
        assert all(p["ocr_pendente"] is True for p in paginas)
        assert all("[OCR" not in p["conteudo"] for p in paginas)

        pecas = pi.segmentar_autos_pdf_pje(pdf)  # sem TOC -> peça única
        assert len(pecas) == 1
        assert pecas[0]["data_peca"] == ""
        assert pecas[0]["conteudo"] == ""
        assert pecas[0]["alertas"]

    def test_motor_ocr_por_plataforma(self, monkeypatch):
        pi._detectar_motor_ocr.cache_clear()
        try:
            monkeypatch.setattr(pi, "IS_WINDOWS", False)
            monkeypatch.setattr(pi, "IS_MACOS", False)
            assert pi._detectar_motor_ocr() is None
            pi._detectar_motor_ocr.cache_clear()
            monkeypatch.setattr(pi, "IS_MACOS", True)
            fake_ocrmac = types.ModuleType("ocrmac")
            fake_ocrmac.ocrmac = types.ModuleType("ocrmac.ocrmac")
            monkeypatch.setitem(sys.modules, "ocrmac", fake_ocrmac)
            monkeypatch.setitem(sys.modules, "ocrmac.ocrmac", fake_ocrmac.ocrmac)
            assert pi._detectar_motor_ocr() == "ocrmac"
            monkeypatch.setenv("JVDFM_OCR", "desligado")
            assert pi._motor_ocr() is None
        finally:
            pi._detectar_motor_ocr.cache_clear()


# ==============================================================================================
# Laya
# ==============================================================================================
class TestLayaSemDadosFicticios:
    def test_dados_nao_extraidos_ficam_vazios_com_alertas(self):
        pecas = classificar_pecas_autos(
            [
                {"doc_id": "ID-1", "titulo": "Petição", "conteudo": "Manifestação genérica sem dados processuais relevantes."},
            ]
        )
        meta = extrair_metadados_processo(pecas)
        assert meta.numero_processo == ""
        assert meta.nome_ofendida == ""
        assert meta.nome_reu == ""
        assert meta.data_fato == ""
        assert meta.data_recebimento_denuncia == ""
        assert meta.data_intimacao_mpu == ""
        assert meta.crimes_imputados == []
        assert "art_147a_cp" not in meta.crimes_imputados
        assert meta.alertas_extracao
        assert any("Data do fato" in a for a in meta.alertas_extracao)
        assert any("recebimento da denúncia" in a for a in meta.alertas_extracao)
        assert any("tipo penal" in a for a in meta.alertas_extracao)

    def test_crimes_vazios_quando_nada_identificado(self):
        p = _peca("ID-D", "DENUNCIA", "O Ministério Público oferece denúncia narrando os fatos.")
        cods, _, _ = _extrair_crimes_dinamicamente([p], p, None, None)
        assert cods == []

    def test_mes_ano_sem_dia_nao_inventa_o_dia(self):
        den = _peca("ID-D", "DENUNCIA", "Os fatos ocorreram desde junho de 2022, de forma reiterada.")
        meta = extrair_metadados_processo([den])
        assert meta.data_fato == ""
        assert any("junho de 2022" in a for a in meta.alertas_extracao)

    def test_pedido_dano_moral_sem_valor_fica_zero_com_alerta(self):
        den = _peca(
            "ID-D",
            "DENUNCIA",
            "Requer a condenação e a fixação de valor mínimo para reparação dos danos morais (art. 387, IV, do CPP).",
        )
        meta = extrair_metadados_processo([den])
        assert meta.pedido_dano_moral_expresso_mp is True
        assert meta.valor_dano_moral_pedido_mp == 0.0
        assert any("sem valor" in a for a in meta.alertas_extracao)

    def test_recebimento_nao_herda_data_do_fato(self):
        bo = _peca("ID-BO", "BO", "Data/Hora do Fato Início: 10/03/2024 22:00. Histórico da ocorrência.", data_peca="2024-03-10")
        meta = extrair_metadados_processo([bo])
        assert meta.data_fato == "2024-03-10"
        assert meta.data_recebimento_denuncia == ""

    def test_id_real_iniciado_em_4100_nao_vira_demo(self):
        raw = [
            {
                "doc_id": "ID-41001234",
                "titulo": "Denúncia Ministerial",
                "conteudo": "O MP oferece denúncia contra JOSE DA SILVA, incurso nas sanções do art. 147 do CP. Vítima: ANA PEREIRA.",
                "data_peca": "2024-05-02",
            }
        ]
        pecas = classificar_pecas_autos(raw)
        assert not pecas[0].resumo_estruturado.startswith("Síntese Laya S1: Imputação tríplice")
        assert not any(e["valor"] == "MARIANA ALVES FERREIRA" for e in pecas[0].entidades_extraidas)
        meta = extrair_metadados_processo(pecas)
        assert meta.nome_ofendida != "MARIANA ALVES FERREIRA"
        assert meta.nome_reu != "CARLOS EDUARDO ROCHA MENDES"
        assert meta.crimes_imputados == ["art_147_cp"]

    def test_emendatio_apenas_sinalizada_sem_alterar_imputacao(self):
        den = _peca(
            "ID-D",
            "DENUNCIA",
            "O MP oferece denúncia contra JOSE DA SILVA, incurso nas sanções do art. 150, § 1º, do CP.",
        )
        bo = _peca("ID-BO", "BO", "Fato ocorrido por volta das 10h30 na residência da vítima.")
        meta = extrair_metadados_processo([den, bo])
        assert "art_150_p1_cp" in meta.crimes_imputados
        assert "art_150_cp" not in meta.crimes_imputados
        assert not any("emendatio" in t.titulo.lower() for t in meta.teses_defesa)
        assert any("emendatio" in a.lower() for a in meta.alertas_extracao)

    def test_sem_teses_nao_fabrica_tese_defensiva(self):
        den = _peca("ID-D", "DENUNCIA", "O MP oferece denúncia contra JOSE DA SILVA, incurso nas sanções do art. 147 do CP.")
        meta = extrair_metadados_processo([den])
        assert all("Insuficiência Probatória e Princípio do In Dubio Pro Reo (Art. 386, VII" not in t.titulo for t in meta.teses_defesa)

    def test_case_context_repassa_is_demo(self):
        from app.core.case_context import limpar_cache_contexto, obter_contexto

        limpar_cache_contexto()
        ctx = obter_contexto(get_demo_case_bundle())
        assert ctx.is_demo is True
        assert ctx.meta.nome_ofendida == "MARIANA ALVES FERREIRA"
        vitima = next(d for d in ctx.matriz_oral.depoentes if d.papel == "OFENDIDA")
        assert vitima.coerencia_delegacia_juizo == "ALTA_COERENCIA"
        limpar_cache_contexto()


# ==============================================================================================
# Transcrição
# ==============================================================================================
def _settings(**kw):
    base = dict(whisper_model="small", whisper_device="cpu", permitir_envio_externo=False)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def ambiente_transcricao(monkeypatch):
    """Isola a transcrição: settings controladas, sem Ollama, sem ffprobe e cache de modelo limpo."""
    estado = {"settings": _settings(), "gemini": 0, "sr": 0}
    monkeypatch.setattr("app.core.config.load_settings", lambda: estado["settings"])
    monkeypatch.setattr(at, "release_ollama_vram_for_whisper", lambda: True)
    monkeypatch.setattr(at, "_duracao_midia_segundos", lambda p: None)
    monkeypatch.setattr(at, "_CUDA_FALHOU", False)
    monkeypatch.setattr(at, "_cuda_disponivel", lambda: False)
    monkeypatch.setattr(at, "_ISOLAR_CUDA", False)  # o Whisper falso roda no próprio processo
    monkeypatch.setattr(at, "_aguardar_vram_livre", lambda *a, **k: True)

    def fake_gemini(path):
        estado["gemini"] += 1
        return {"segmentos": [{"timestamp": "00:00 - 00:10", "texto_literal": "Trecho externo."}], "engine": "Gemini Multimodal Audio PT-BR"}

    def fake_sr(path, dur):
        estado["sr"] += 1
        return {"erro": "sr indisponível"}

    monkeypatch.setattr(at, "_transcrever_gemini", fake_gemini)
    monkeypatch.setattr(at, "_transcrever_speech_recognition", fake_sr)
    at.descarregar_modelo_whisper()
    yield estado
    at.descarregar_modelo_whisper()


def _instalar_fake_whisper(monkeypatch, falhar_em: tuple[str, ...] = (), segmentos=None):
    instancias: list[tuple] = []
    segs = segmentos if segmentos is not None else [
        SimpleNamespace(start=0, end=12.4, text=" Ele me ameaçou de morte. "),
        SimpleNamespace(start=12.4, end=75.2, text="Fiquei com muito medo."),
    ]

    class FakeWhisperModel:
        def __init__(self, modelo, device="auto", compute_type="default"):
            instancias.append((modelo, device, compute_type))
            if device in falhar_em:
                raise RuntimeError(f"falha simulada em {device}")

        def transcribe(self, path, language=None, vad_filter=None, initial_prompt="", **_extra):
            return iter(list(segs)), SimpleNamespace(duration=75.2)

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    return instancias


class TestTranscricao:
    def test_sem_motor_local_e_sem_consentimento_falha_honestamente(self, tmp_path, monkeypatch, ambiente_transcricao):
        monkeypatch.setitem(sys.modules, "faster_whisper", None)  # import falha
        ambiente_transcricao["settings"] = _settings(permitir_envio_externo=True)
        midia = tmp_path / "Depoimento da Vítima Maria_001.mp4"
        midia.write_bytes(b"video-falso")

        tr = at.transcrever_midia_audiencia(midia, papel="OFENDIDA")
        assert tr["status"] == "FALHOU"
        assert tr["segmentos"] == []
        assert "consentimento" in tr["erro"]
        assert tr["engine"] == "nenhum"
        assert tr["duracao"] == ""  # nada de "03:15" fictício
        assert ambiente_transcricao["gemini"] == 0
        for chave in ("media_id", "arquivo", "depoente", "papel", "duracao", "segmentos", "engine", "status", "erro", "sha256"):
            assert chave in tr
        # Falha por ausência de motor não é cacheada (nova tentativa depois da instalação).
        assert not midia.with_name(midia.name + ".transcript_v2.json").exists()

    def test_externo_exige_settings_e_consentimento(self, tmp_path, monkeypatch, ambiente_transcricao):
        monkeypatch.setitem(sys.modules, "faster_whisper", None)
        midia = tmp_path / "audiencia.mp4"
        midia.write_bytes(b"video-falso-2")

        ambiente_transcricao["settings"] = _settings(permitir_envio_externo=False)
        tr = at.transcrever_midia_audiencia(midia, consentimento_envio_externo=True)
        assert tr["status"] == "FALHOU" and "permitir_envio_externo" in tr["erro"]
        assert ambiente_transcricao["gemini"] == 0

        ambiente_transcricao["settings"] = _settings(permitir_envio_externo=True)
        tr = at.transcrever_midia_audiencia(midia, consentimento_envio_externo=True)
        assert ambiente_transcricao["gemini"] == 1
        assert tr["status"] == "OK"
        assert tr["engine"] == "Gemini Multimodal Audio PT-BR"

    def test_whisper_local_primeiro_mesmo_com_consentimento(self, tmp_path, monkeypatch, ambiente_transcricao):
        instancias = _instalar_fake_whisper(monkeypatch)
        ambiente_transcricao["settings"] = _settings(permitir_envio_externo=True, whisper_model="medium", whisper_device="cpu")
        midia = tmp_path / "Depoimento da Vítima Maria.mp4"
        midia.write_bytes(b"conteudo-1")

        tr = at.transcrever_midia_audiencia(midia, papel="OFENDIDA", consentimento_envio_externo=True)
        assert ambiente_transcricao["gemini"] == 0
        assert tr["status"] == "OK"
        assert tr["engine"].startswith("faster-whisper medium (cpu/int8)")
        assert instancias == [("medium", "cpu", "int8")]
        assert tr["segmentos"][0] == {"timestamp": "00:00 - 00:12", "texto_literal": "Ele me ameaçou de morte."}
        assert tr["duracao"] == "01:15"
        assert tr["sha256"] == at.sha256_arquivo(midia)

        cache = json.loads(midia.with_name(midia.name + ".transcript_v2.json").read_text(encoding="utf-8"))
        assert cache["schema_version"] == at.TRANSCRICAO_SCHEMA and cache["sha256"] == tr["sha256"]

        # Segunda mídia: o modelo em cache é reutilizado (não recarrega por arquivo).
        midia2 = tmp_path / "Depoimento da Testemunha PM Tercio.mp4"
        midia2.write_bytes(b"conteudo-2")
        tr2 = at.transcrever_midia_audiencia(midia2)
        assert tr2["papel"] == "TESTEMUNHA_POLICIAL"
        assert instancias == [("medium", "cpu", "int8")]

        # Cache por sha256: nova chamada não transcreve de novo.
        with mock.patch.object(at, "_transcrever_whisper_local", side_effect=AssertionError("não deveria transcrever")):
            assert at.transcrever_midia_audiencia(midia, papel="OFENDIDA")["segmentos"] == tr["segmentos"]

    def test_macos_usa_cpu_int8_e_cuda_com_falha_cai_para_cpu(self, tmp_path, monkeypatch, ambiente_transcricao):
        from app.core import vram_manager as vm

        monkeypatch.setattr(at, "IS_MACOS", True)
        monkeypatch.setattr(vm, "IS_MACOS", True)
        assert at._resolver_dispositivo_whisper("cuda") == ("cpu", "int8")
        monkeypatch.setattr(at, "IS_MACOS", False)
        monkeypatch.setattr(vm, "IS_MACOS", False)
        assert at._resolver_dispositivo_whisper("cuda") == ("cuda", "int8_float16")
        assert at._resolver_dispositivo_whisper("auto") == ("cpu", "int8")  # _cuda_disponivel -> False

        instancias = _instalar_fake_whisper(monkeypatch, falhar_em=("cuda",))
        ambiente_transcricao["settings"] = _settings(whisper_device="cuda")
        midia = tmp_path / "Interrogatório do Acusado.mp4"
        midia.write_bytes(b"conteudo-3")
        tr = at.transcrever_midia_audiencia(midia)
        assert [i[1] for i in instancias] == ["cuda", "cpu"]
        assert tr["status"] == "OK" and "(cpu/int8)" in tr["engine"]
        assert tr["papel"] == "INTERROGATORIO_REU"

    def test_whisper_sem_fala_falha_definitiva_cacheada(self, tmp_path, monkeypatch, ambiente_transcricao):
        _instalar_fake_whisper(monkeypatch, segmentos=[])
        midia = tmp_path / "silencio.mp4"
        midia.write_bytes(b"silencio")
        tr = at.transcrever_midia_audiencia(midia)
        assert tr["status"] == "FALHOU" and tr["segmentos"] == []
        assert tr["falha_definitiva"] is True
        with mock.patch.object(at, "_transcrever_whisper_local", side_effect=AssertionError("cacheado")):
            assert at.transcrever_midia_audiencia(midia)["status"] == "FALHOU"

    def test_falha_sem_consentimento_nao_bloqueia_reprocesso_com_consentimento(self, tmp_path, monkeypatch, ambiente_transcricao):
        _instalar_fake_whisper(monkeypatch, segmentos=[])
        ambiente_transcricao["settings"] = _settings(permitir_envio_externo=True)
        midia = tmp_path / "audiencia_muda.mp4"
        midia.write_bytes(b"sem-fala")
        tr1 = at.transcrever_midia_audiencia(midia)
        assert tr1["status"] == "FALHOU" and not tr1.get("falha_definitiva")
        assert ambiente_transcricao["gemini"] == 0
        tr2 = at.transcrever_midia_audiencia(midia, consentimento_envio_externo=True)
        assert ambiente_transcricao["gemini"] == 1
        assert tr2["status"] == "OK"

    def test_cache_antigo_falhou_definitivo_sem_gemini_tentado_e_reprocessado(self, tmp_path, monkeypatch, ambiente_transcricao):
        _instalar_fake_whisper(monkeypatch, segmentos=[])
        ambiente_transcricao["settings"] = _settings(permitir_envio_externo=True)
        midia = tmp_path / "antigo.mp4"
        midia.write_bytes(b"cache-antigo")
        sha = at.sha256_arquivo(midia)
        midia.with_name(midia.name + ".transcript_v2.json").write_text(
            json.dumps({"schema_version": at.TRANSCRICAO_SCHEMA, "sha256": sha, "status": "FALHOU",
                        "falha_definitiva": True, "segmentos": [], "motores_tentados": ["faster-whisper"]}),
            encoding="utf-8",
        )
        tr = at.transcrever_midia_audiencia(midia, consentimento_envio_externo=True)
        assert ambiente_transcricao["gemini"] == 1 and tr["status"] == "OK"

    def test_speech_recognition_nao_e_encadeado_apos_falha_do_gemini(self, tmp_path, monkeypatch, ambiente_transcricao):
        monkeypatch.setitem(sys.modules, "faster_whisper", None)
        ambiente_transcricao["settings"] = _settings(permitir_envio_externo=True)
        monkeypatch.setattr(at, "_transcrever_gemini", lambda p: {"erro": "Gemini: chave de API não configurada"})
        midia = tmp_path / "depoimento.mp4"
        midia.write_bytes(b"sr-sem-consentimento")
        tr = at.transcrever_midia_audiencia(midia, consentimento_envio_externo=True)
        assert tr["status"] == "FALHOU"
        assert ambiente_transcricao["sr"] == 0
        assert "speech_recognition" not in tr["motores_tentados"]
        # Com consentimento próprio e explícito, passa a ser tentado.
        tr = at.transcrever_midia_audiencia(
            midia, consentimento_envio_externo=True, consentimento_speech_recognition=True, usar_cache=False
        )
        assert ambiente_transcricao["sr"] == 1 and "speech_recognition" in tr["motores_tentados"]

    def test_log_de_falha_nao_contem_nome_da_midia(self, tmp_path, monkeypatch, ambiente_transcricao, caplog):
        monkeypatch.setitem(sys.modules, "faster_whisper", None)
        midia = tmp_path / "Depoimento Vitima Fernanda Lima Souza.mp4"
        midia.write_bytes(b"log-privacidade")
        with caplog.at_level("WARNING"):
            at.transcrever_midia_audiencia(midia)
        assert "Fernanda" not in caplog.text and "Souza" not in caplog.text
        assert at.rotulo_log_arquivo(midia.name) in caplog.text

    def test_lote_descarrega_modelo_whisper(self, monkeypatch, tmp_path):
        import app.main as main_module

        chamadas = []
        monkeypatch.setattr(main_module, "descarregar_modelo_whisper", lambda: chamadas.append(1))

        def boom(*a, **k):
            raise RuntimeError("falha")

        monkeypatch.setattr(main_module, "_transcrever", boom)
        monkeypatch.setattr(main_module, "_nomes_conhecidos", lambda docs: ([], "", "", ""))
        from app.core.case_manifest import descrever_arquivo_caso, montar_manifesto_caso

        monkeypatch.setattr(main_module, "CASES_DIR", tmp_path)
        pdf, midia = tmp_path / "autos.pdf", tmp_path / "x.mp4"
        pdf.write_bytes(b"autos ficticios")
        midia.write_bytes(b"audio ficticio")
        manifesto = montar_manifesto_caso([descrever_arquivo_caso(tmp_path, pdf, "PDF")], [])
        bundle = {"raw_documents": [{"doc_id": "ID-FICTICIO"}], "manifesto_caso": manifesto}
        with pytest.raises(RuntimeError):
            main_module._processar_arquivos_upload_sync([], [midia], {}, "D", bundle, False, 1)
        assert chamadas == [1]  # finally executa mesmo na falha

    def test_cache_de_transcricao_preserva_contexto_do_manifesto(self, tmp_path, monkeypatch, ambiente_transcricao):
        from app.transcription import pos_processamento

        midia = tmp_path / "gravacao.mp4"
        midia.write_bytes(b"audiencia ficticia")
        chamadas = []

        def aprimorar(segmentos, *, nomes, numero_processo):
            chamadas.append(numero_processo)
            return segmentos

        monkeypatch.setattr(pos_processamento, "aprimorar_segmentos", aprimorar)
        monkeypatch.setattr(at, "_numero_cnj_vizinho", lambda _: pytest.fail("O caso já foi identificado"))
        _instalar_fake_whisper(monkeypatch)
        primeiro = at.transcrever_midia_audiencia(midia, identidade_caso="caso-a", numero_processo="FICTICIO-A")
        novamente = at.transcrever_midia_audiencia(midia, identidade_caso="caso-a", numero_processo="FICTICIO-A")
        segundo = at.transcrever_midia_audiencia(midia, identidade_caso="caso-b", numero_processo="FICTICIO-B")

        assert primeiro["status"] == segundo["status"] == "OK"
        assert novamente == primeiro
        assert chamadas == ["FICTICIO-A", "FICTICIO-B"]

    def test_ids_estaveis_por_conteudo_independentes_de_vizinhos(self, tmp_path, monkeypatch, ambiente_transcricao):
        _instalar_fake_whisper(monkeypatch)
        d1, d2 = tmp_path / "a", tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        m1 = d1 / "Vitima_Maria_001.mp4"
        m1.write_bytes(b"mesmo-conteudo")
        m2 = d2 / "Vitima_Maria_001.mp4"
        m2.write_bytes(b"mesmo-conteudo")
        (d2 / "Interrogatorio_Reu_001.mp4").write_bytes(b"vizinho")  # vizinho não altera o ID
        m3 = d1 / "Vitima_Ana_001.mp4"
        m3.write_bytes(b"outro-conteudo")

        id1 = at.transcrever_midia_audiencia(m1, papel="OFENDIDA")["media_id"]
        id2 = at.transcrever_midia_audiencia(m2, papel="OFENDIDA")["media_id"]
        id3 = at.transcrever_midia_audiencia(m3, papel="OFENDIDA")["media_id"]
        esperado = f"ID-MIDIA-{at.sha256_arquivo(m1)[:8].upper()}-001"
        assert id1 == id2 == esperado
        assert id3 != id1 and id3.endswith("-001")

    def test_papel_por_tokens_inteiros(self):
        casos = {
            "Depoimento da Testemunha Kempman.mp4": "TESTEMUNHA_ACUSACAO",  # 'PM' dentro de palavra
            "Depoimento da Testemunha Andreus Pereira.mp4": "TESTEMUNHA_ACUSACAO",  # 'REU' dentro de palavra
            "Depoimento da Testemunha PM Tercio.mp4": "TESTEMUNHA_POLICIAL",
            "Testemunha_Cb._PM_Roberto.mp4": "TESTEMUNHA_POLICIAL",
            "Interrogatório do Réu Carlos.mp4": "INTERROGATORIO_REU",
            "Alegações Finais do MP.mp4": "ALEGACOES_FINAIS_MP",
            "Depoimento da Vítima Ana_002.mp4": "OFENDIDA",
            "Testemunha de Defesa Joao.mp4": "TESTEMUNHA_DEFESA",
        }
        for nome, esperado in casos.items():
            _, _, papel = at._infer_speaker_and_role(nome, "OFENDIDA / TESTEMUNHA", "OFENDIDA")
            assert papel == esperado, nome

    def test_cache_legado_migrado_somente_quando_seguro(self, tmp_path, monkeypatch, ambiente_transcricao):
        monkeypatch.setitem(sys.modules, "faster_whisper", None)
        midia = tmp_path / "Vitima_001.mp4"
        midia.write_bytes(b"legado")
        cache = midia.with_name(midia.name + ".transcript_v2.json")
        legado = {
            "size": midia.stat().st_size,
            "media_id": "ID-MIDIA-001",
            "segmentos": [{"timestamp": "00:00 - 00:30", "texto_literal": "Relato real."}],
            "engine": "faster-whisper (CUDA RTX 2060)",
        }
        cache.write_text(json.dumps(legado), encoding="utf-8")
        tr = at.transcrever_midia_audiencia(midia, papel="OFENDIDA")
        assert tr["status"] == "OK" and tr["segmentos"] == legado["segmentos"]
        assert tr["media_id"] == f"ID-MIDIA-{at.sha256_arquivo(midia)[:8].upper()}-001"
        gravado = json.loads(cache.read_text(encoding="utf-8"))
        assert gravado["schema_version"] == at.TRANSCRICAO_SCHEMA and gravado["sha256"] == tr["sha256"]

        # Transcrição fictícia antiga ("Local Audio Pipeline") nunca é reaproveitada.
        midia2 = tmp_path / "Vitima_002.mp4"
        midia2.write_bytes(b"legado-2")
        cache2 = midia2.with_name(midia2.name + ".transcript_v2.json")
        cache2.write_text(json.dumps({**legado, "size": midia2.stat().st_size, "engine": "Local Audio Pipeline"}), encoding="utf-8")
        assert at.transcrever_midia_audiencia(midia2)["status"] == "FALHOU"

        # Mídia modificada depois do cache legado: ignorado.
        midia3 = tmp_path / "Vitima_003.mp4"
        midia3.write_bytes(b"legado-3")
        cache3 = midia3.with_name(midia3.name + ".transcript_v2.json")
        cache3.write_text(json.dumps({**legado, "size": midia3.stat().st_size}), encoding="utf-8")
        futuro = time.time() + 120
        os.utime(midia3, (futuro, futuro))
        assert at.transcrever_midia_audiencia(midia3)["status"] == "FALHOU"

    def test_find_binary_delegado_retorna_none(self, monkeypatch):
        monkeypatch.setattr(at, "find_binary", lambda name: None)
        assert at._find_binary("ffmpeg") is None


# ==============================================================================================
# Prova oral
# ==============================================================================================
class TestProvaOral:
    def test_transcricoes_falhas_ou_vazias_sao_ignoradas(self):
        trs = [
            {"media_id": "ID-MIDIA-AAAA0001", "arquivo": "a.mp4", "depoente": "ANA", "papel": "OFENDIDA",
             "duracao": "", "segmentos": [], "status": "FALHOU", "erro": "faster-whisper não instalado"},
            {"media_id": "ID-MIDIA-AAAA0002", "arquivo": "b.mp4", "depoente": "BIA", "papel": "OFENDIDA",
             "duracao": "01:00", "segmentos": [{"timestamp": "00:00 - 00:05", "texto_literal": "   "}]},
            {"media_id": "ID-MIDIA-AAAA0003", "arquivo": "c.mp4", "depoente": "CAIO PM", "papel": "TESTEMUNHA_POLICIAL",
             "duracao": "02:00", "status": "OK",
             "segmentos": [{"timestamp": "00:10 - 00:40", "texto_literal": "Fomos acionados para atender a ocorrência."}]},
        ]
        matriz = gerar_matriz_corroboracao_oral([], trs)
        assert [d.media_id for d in matriz.depoentes] == ["ID-MIDIA-AAAA0003"]
        assert {i["media_id"] for i in matriz.midias_sem_transcricao} == {"ID-MIDIA-AAAA0001", "ID-MIDIA-AAAA0002"}
        assert "faster-whisper" in matriz.midias_sem_transcricao[0]["motivo"]

    def test_sintese_nao_inventa_fatos_nem_converte_negativa_em_confissao(self):
        pecas = [_peca("ID-500", "DENUNCIA", "Denúncia.")]
        trs = [
            {"media_id": "ID-MIDIA-0000000A", "arquivo": "v.mp4", "depoente": "ANA SOUZA", "papel": "OFENDIDA", "duracao": "03:00",
             "segmentos": [
                 {"timestamp": "00:05 - 00:30", "texto_literal": "Ele entrou e ficou gritando comigo."},
                 {"timestamp": "00:31 - 01:10", "texto_literal": "Ele me deu um soco no braço."},
             ]},
            {"media_id": "ID-MIDIA-0000000B", "arquivo": "r.mp4", "depoente": "JOSE LIMA", "papel": "INTERROGATORIO_REU", "duracao": "02:00",
             "segmentos": [{"timestamp": "00:10 - 00:50", "texto_literal": "Eu não dei um tapa nela, nunca agredi ninguém. Mudei para Goiânia."}]},
        ]
        matriz = gerar_matriz_corroboracao_oral(pecas, trs)
        vit = next(d for d in matriz.depoentes if d.papel == "OFENDIDA")
        texto_vit = vit.discurso_indireto_sentenca
        assert "muro" not in texto_vit and "dormia" not in texto_vit and "vizinhos" not in texto_vit
        assert "agressão física" in texto_vit and "ID-MIDIA-0000000A, 00:31 - 01:10" in texto_vit
        assert vit.coerencia_delegacia_juizo == "PENDENTE_AVALIACAO_JUIZO"

        reu = next(d for d in matriz.depoentes if d.papel == "INTERROGATORIO_REU")
        assert "admitiu" not in reu.discurso_indireto_sentenca
        assert "negou" in reu.discurso_indireto_sentenca
        assert "Goiás" not in reu.discurso_indireto_sentenca

    def test_referencias_documentais_nao_apontam_peca_aleatoria(self):
        pecas = [_peca("ID-999", "CERTIDAO_INTIMACAO_MPU", "Certidão qualquer.")]
        trs = [{"media_id": "ID-MIDIA-0000000C", "arquivo": "t.mp4", "depoente": "LUIZ", "papel": "TESTEMUNHA_ACUSACAO",
                "duracao": "01:00", "segmentos": [{"timestamp": "00:00 - 00:20", "texto_literal": "Não presenciei os fatos."}]}]
        dep = gerar_matriz_corroboracao_oral(pecas, trs).depoentes[0]
        assert "ID-999" not in dep.discurso_indireto_sentenca
        assert "ID-999" not in dep.corroboracao_pericial_documental
        assert "não ter presenciado" in dep.discurso_indireto_sentenca

    def test_consolidacao_multiparte_ancora_cada_trecho_na_sua_midia(self):
        trs = [
            {"media_id": "ID-MIDIA-ABCDEF01-001", "arquivo": "v_001.mp4", "depoente": "ANA (Parte 01)", "papel": "OFENDIDA",
             "duracao": "01:00", "segmentos": [{"timestamp": "00:00 - 00:30", "texto_literal": "Tenho medo dele."}]},
            {"media_id": "ID-MIDIA-ABCDEF02-002", "arquivo": "v_002.mp4", "depoente": "ANA (Parte 02)", "papel": "OFENDIDA",
             "duracao": "00:45", "segmentos": [{"timestamp": "00:05 - 00:20", "texto_literal": "Ele me deu um tapa."}]},
        ]
        matriz = gerar_matriz_corroboracao_oral([], trs)
        grupos = consolidar_prova_oral_por_interlocutor(matriz.depoentes, pecas=[])
        assert len(grupos) == 1
        sintese = grupos[0]["sintese_consolidada"][0]
        assert grupos[0]["duracao_total"] == "01:45"
        assert "(Parte 01)" not in sintese
        assert "ID-MIDIA-ABCDEF02-002, 00:05 - 00:20" in sintese
        assert "ID-MIDIA-ABCDEF01-001, 00:00 - 00:30" in sintese
