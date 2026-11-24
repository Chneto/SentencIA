# -*- coding: utf-8 -*-
"""
Suíte de testes de integração (100% dados fictícios):
1. Pipeline completo de ingestão, prova oral, dosimetria (com emendatio do art. 150) e sentença;
2. Divisão do upload em duas estações (POST /api/cases/upload-pdfs e POST /api/cases/upload-medias)
   com rastreamento de status, 'uploaded_at' e 'processed_at';
3. Separação dos 5 Motores Python por função e geração do Script Estruturado do Caso;
4. Integração Dupla Via com o modelo Gemini (API Direta + Copiar Script / Colar Sentença Web)
   com Pseudonimização Reversível em 1 Clique ([OFENDIDA] / [ACUSADO]);
5. Modelos cadastrados no perfil de estilo (MOD-01, MOD-02, MOD-03) e verbetes na Base Jurídica Fechada.
"""

from __future__ import annotations

from pathlib import Path
import sys
import types
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pymupdf

from app.export.document_exporter import _is_post_sentence_order, _is_section_heading
from app.generation.case_script_engine import pseudonimizar_texto
from app.generation.hallucination_auditor import auditar_sentenca
from app.generation.sentence_generator import gerar_sentenca_estruturada
from app.ingestion.laya_classifier import classificar_pecas_autos, extrair_metadados_processo
from app.knowledge.knowledge_base import get_all_knowledge_base
from app.knowledge.style_cloner import get_style_profile
from app.main import app
from app.transcription.oral_proof_analyzer import gerar_matriz_corroboracao_oral
from tests.test_regressions_review import _synthetic_multicrime_bundle


def test_new_docx_models_and_knowledge_base_entries():
    """Verifica o cadastro dos modelos no perfil de estilo e dos precedentes/súmulas na Base Fechada."""
    profile = get_style_profile()
    ids_modelos = {m["id"] for m in profile.get("modelos_cadastrados", [])}
    assert {"MOD-01", "MOD-02", "MOD-03"}.issubset(ids_modelos)

    kb = get_all_knowledge_base()
    refs = {item["id"] for item in kb["sumulas_e_temas"]}
    assert "SUMULA-54-STJ" in refs
    assert "SUMULA-362-STJ" in refs
    assert "TEMA-585-STJ" in refs
    assert "STJ-INFO-846-CONSUNCAO" in refs
    assert "STJ-CE-AMEACA-COLERA" in refs
    assert "ADPF-779-STF" in refs

    dout_ids = {d["id"] for d in kb["doutrina"]}
    assert {"DOUT-01", "DOUT-03", "DOUT-04", "DOUT-08"}.issubset(dout_ids)


CRIMES_IMPUTADOS_SINTETICO = ["art_129_p13_cp", "art_150_p1_cp", "art_147_cp"]

CRIMES_CONFIG_SINTETICO = [
    {"codigo": "art_129_p13_cp", "veredicto": "condenar"},
    {
        "codigo": "art_150_p1_cp",
        "veredicto": "desclassificar",
        "crime_desclassificado": "art_150_cp",
        "fundamentacao": "Emendatio libelli (art. 383 do CPP): os fatos narrados amoldam-se ao caput do art. 150 do CP.",
    },
    {"codigo": "art_147_cp", "veredicto": "condenar", "enquadramento_ameaca": "caput"},
]


def _assert_auditoria_honesta(audit, marcadores_sentenca) -> None:
    """Critério v2: forma 100, zero alucinação, penas conferidas; só os marcadores
    ``[COMPLETAR PELO JUÍZO: ...]`` podem impedir a aprovação (score 100 ⇔ nenhum marcador)."""
    dados = audit if isinstance(audit, dict) else audit.model_dump()
    marcadores = list(marcadores_sentenca or [])
    assert dados["score_forma"] == 100.0, dados["detalhes_camadas"]
    assert dados["ids_inexistentes"] == []
    assert dados["citacoes_nao_autorizadas"] == []
    assert dados["teses_omitidas"] == []
    assert dados["penas_divergentes"] == []
    assert dados["aprovada_sem_alucinacao"] is (not marcadores)
    assert (dados["score_confiabilidade"] == 100.0) is (not marcadores)


def _instalar_google_genai_falso(monkeypatch, texto_resposta: str, chamadas: dict) -> None:
    """Registra um pacote ``google.genai`` falso (o real é opcional e pode não estar instalado)."""

    class _FakeModels:
        def generate_content(self, model, contents, config=None):
            chamadas["model"] = model
            chamadas["contents"] = contents
            return SimpleNamespace(text=texto_resposta)

    class _FakeClient:
        def __init__(self, api_key):
            chamadas["api_key"] = api_key
            self.models = _FakeModels()

    fake_google = types.ModuleType("google")
    fake_genai = types.ModuleType("google.genai")
    fake_types = types.ModuleType("google.genai.types")
    fake_types.GenerateContentConfig = lambda **kw: kw
    fake_genai.Client = _FakeClient
    fake_genai.types = fake_types
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)


def _criar_pdf_multicrime_sintetico(destino: Path) -> Path:
    bundle = _synthetic_multicrime_bundle()
    doc = pymupdf.open()
    toc = []
    for idx, peca in enumerate(bundle["raw_documents"], start=1):
        num = peca["doc_id"].replace("ID-", "")
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(40, 40, 550, 780), peca["conteudo"], fontsize=10)
        toc.append([1, f"{peca['titulo']} | NUM: {num} | 20/02/2025", idx])
    doc.set_toc(toc)
    doc.save(str(destino))
    doc.close()
    return destino


def test_synthetic_multicrime_full_ingestion_oral_dosimetry_and_sentence():
    """
    Testa de ponta a ponta o caso sintético 0807111-11.2025.8.20.0001 (Roberto / Camila):
    - Classificação das peças e laudos UN-9101-2025 e UN-9102-2025;
    - Extração dos 3 crimes (art_129_p13_cp, art_150_p1_cp com alerta de emendatio e art_147_cp);
    - Matriz de corroboração oral e geração da sentença condenatória completa com auditoria honesta.
    """
    bundle = _synthetic_multicrime_bundle()
    raw_docs = bundle["raw_documents"]
    pecas = classificar_pecas_autos(raw_docs)
    doc_ids = {p.doc_id for p in pecas}

    assert {"ID-7100101", "ID-7100102", "ID-7100103", "ID-7100104", "ID-7100108", "ID-7100109"}.issubset(doc_ids)

    meta = extrair_metadados_processo(pecas)
    assert meta.numero_processo == "0807111-11.2025.8.20.0001"
    assert meta.nome_ofendida == "CAMILA ROCHA AZEVEDO"
    assert meta.nome_reu == "ROBERTO GOMES SILVEIRA"
    assert meta.crimes_imputados == CRIMES_IMPUTADOS_SINTETICO
    assert any("150" in a for a in meta.alertas_extracao), meta.alertas_extracao

    transcriptions = bundle["transcriptions"]
    matriz_oral = gerar_matriz_corroboracao_oral(pecas, transcriptions)
    assert len(matriz_oral.depoentes) == 2
    media_ids = [d.media_id for d in matriz_oral.depoentes]
    assert len(set(media_ids)) == 2

    sent = gerar_sentenca_estruturada(
        bundle,
        custom_dosimetria_override={"crimes_config": CRIMES_CONFIG_SINTETICO},
        use_ollama_if_available=False,
    )
    txt = sent.texto_completo
    assert "0807111-11.2025.8.20.0001" in txt
    assert "CAMILA ROCHA AZEVEDO" in txt.upper()
    assert "ROBERTO GOMES SILVEIRA" in txt.upper()
    assert "ID-7100103" in txt
    assert "Goiânia/GO" not in txt

    all_media_ids = sorted({t["media_id"] for t in transcriptions} | set(media_ids))
    audit = auditar_sentenca(
        txt, pecas, meta.teses_defesa, all_media_ids, dosimetria_dados=sent.dosimetria_dados
    )
    _assert_auditoria_honesta(audit, sent.marcadores_pendentes)


def test_split_upload_endpoints_and_gemini_dual_path_with_pseudonymization(tmp_path: Path, monkeypatch):
    """
    Verifica:
    1. Upload dividido em duas áreas (/api/cases/upload-pdfs e /api/cases/upload-medias)
       com validação de extensão e status ('uploaded_at' e 'processed_at');
    2. Geração do Script Estruturado dos 5 Motores Python (/api/gemini/build-script)
       com Pseudonimização Reversível ([OFENDIDA], [ACUSADO]);
    3. Geração via /api/gemini/generate e importação/despseudonimização via /api/gemini/import-sentence.
    """
    import app.main as main_module
    from app.transcription.audio_transcriber import _infer_speaker_and_role

    pdf_path = _criar_pdf_multicrime_sintetico(tmp_path / "0807111-11.2025.8.20.0001.pdf")
    mp4_path = tmp_path / "Audiencias-20260323-Alegações Finais do MP.mp4"
    mp4_path.write_bytes(b"fake_mp4_alegacoes_mp")

    def _fake_transcrever(m_path, depoente="OFENDIDA / TESTEMUNHA", papel="OFENDIDA", known_ofendida="", known_reu=""):
        media_id, nome_dep, papel_inf = _infer_speaker_and_role(
            m_path.name, depoente, papel, known_ofendida=known_ofendida, known_reu=known_reu
        )
        return {
            "media_id": media_id,
            "arquivo": m_path.name,
            "depoente": nome_dep,
            "papel": papel_inf,
            "duracao": "01:45",
            "segmentos": [
                {"inicio": "00:05", "fim": "01:15", "texto": "O Ministério Público requer a condenação do réu nos termos da denúncia."}
            ],
            "sintese": "O Ministério Público ratificou o pedido condenatório.",
        }

    cases_tmp = tmp_path / "cases"
    cases_tmp.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main_module, "CASES_DIR", cases_tmp)
    monkeypatch.setattr(main_module, "transcrever_midia_audiencia", _fake_transcrever)
    client = TestClient(app)

    # 1. Rejeita arquivo trocado entre as duas áreas de upload
    with open(mp4_path, "rb") as f_m:
        r_bad_pdf = client.post(
            "/api/cases/upload-pdfs",
            files=[("files", (mp4_path.name, f_m, "video/mp4"))],
        )
    assert r_bad_pdf.status_code == 422

    with open(pdf_path, "rb") as f_p:
        r_bad_media = client.post(
            "/api/cases/upload-medias",
            files=[("files", (pdf_path.name, f_p, "application/pdf"))],
        )
    assert r_bad_media.status_code == 422

    # 2. Upload Parte 1: PDF do Processo
    with open(pdf_path, "rb") as f_p:
        r_pdf = client.post(
            "/api/cases/upload-pdfs",
            files=[("files", (pdf_path.name, f_p, "application/pdf"))],
        )
    assert r_pdf.status_code == 200
    d_pdf = r_pdf.json()
    assert d_pdf["numero_processo"] == "0807111-11.2025.8.20.0001"
    assert len(d_pdf["upload_status"]["pdfs_processo"]) == 1
    pdf_st = d_pdf["upload_status"]["pdfs_processo"][0]
    assert pdf_st["filename"] == pdf_path.name
    assert pdf_st["status"] == "PROCESSADO"
    assert pdf_st["uploaded_at"]
    assert pdf_st["processed_at"]

    # 3. Upload Parte 2: Mídia de Audiência
    with open(mp4_path, "rb") as f_m:
        r_med = client.post(
            "/api/cases/upload-medias",
            files=[("files", (mp4_path.name, f_m, "video/mp4"))],
            data={"depoente_padrao": "OFENDIDA / TESTEMUNHA"},
        )
    assert r_med.status_code == 200
    d_med = r_med.json()
    assert len(d_med["upload_status"]["pdfs_processo"]) == 1
    assert len(d_med["upload_status"]["midias_audiencia"]) == 1
    med_st = d_med["upload_status"]["midias_audiencia"][0]
    assert med_st["filename"] == mp4_path.name
    assert med_st["status"] == "PROCESSADO"
    assert med_st["uploaded_at"]
    assert med_st["processed_at"]

    # 4. Consulta de status dos arquivos e dos 5 motores Python
    r_st = client.get("/api/cases/upload-status")
    assert r_st.status_code == 200
    st_json = r_st.json()
    assert len(st_json["motores_py"]) == 5

    # 5. Geração do Script Estruturado com Pseudonimização Reversível Ativa
    override = {"crimes_config": CRIMES_CONFIG_SINTETICO}
    r_scr = client.post(
        "/api/gemini/build-script", json={"pseudonimizar": True, "custom_dosimetria_override": override}
    )
    assert r_scr.status_code == 200
    scr = r_scr.json()["script"]
    assert scr["pseudonimizado"] is True
    assert "[OFENDIDA]" in scr["script_markdown"]
    assert "[ACUSADO]" in scr["script_markdown"]
    assert "CAMILA ROCHA AZEVEDO" not in scr["script_markdown"]
    assert "ROBERTO GOMES SILVEIRA" not in scr["script_markdown"]
    assert "0807111-11.2025.8.20.0001" not in scr["script_markdown"]
    assert {"mapa_hash", "mapa_reversao"}.isdisjoint(scr)
    assert len(scr["motores_status"]) == 5

    # 6. Geração via /api/gemini/generate (sem chave: motor local) e importação via /api/gemini/import-sentence
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr("app.generation.gemini_integration.resolver_chave_gemini", lambda api_key="": (api_key or "").strip())
    r_gen = client.post(
        "/api/gemini/generate", json={"pseudonimizar": True, "custom_dosimetria_override": override}
    )
    assert r_gen.status_code == 200
    gen_data = r_gen.json()
    assert gen_data["telemetria"]["gemini_utilizado"] is False
    assert {"mapa_hash", "mapa_reversao"}.isdisjoint(gen_data["script"])
    _assert_auditoria_honesta(gen_data["auditoria"], gen_data["marcadores_pendentes"])
    assert "CAMILA ROCHA AZEVEDO" in gen_data["sentenca"]["texto_completo"]
    assert "ROBERTO GOMES SILVEIRA" in gen_data["sentenca"]["texto_completo"]

    from app.main import ACTIVE_CASE_STATE, _mapa_guardado

    mapa, _ = _mapa_guardado(ACTIVE_CASE_STATE["bundle"])
    assert mapa
    texto_pseudonimizado = pseudonimizar_texto(gen_data["sentenca"]["texto_completo"], mapa)
    assert "[OFENDIDA]" in texto_pseudonimizado
    assert "[ACUSADO]" in texto_pseudonimizado

    r_imp = client.post(
        "/api/gemini/import-sentence",
        json={
            "texto_sentenca": texto_pseudonimizado,
            "auto_blindar_auditoria": True,
            "custom_dosimetria_override": override,
        },
    )
    assert r_imp.status_code == 200
    imp_data = r_imp.json()
    _assert_auditoria_honesta(imp_data["auditoria"], imp_data["marcadores_pendentes"])
    assert "[OFENDIDA]" not in imp_data["sentenca"]["texto_completo"]
    assert "[ACUSADO]" not in imp_data["sentenca"]["texto_completo"]
    assert "CAMILA ROCHA AZEVEDO" in imp_data["sentenca"]["texto_completo"]
    assert "ROBERTO GOMES SILVEIRA" in imp_data["sentenca"]["texto_completo"]

    # 7. String vazia em /api/gemini/import-sentence (422)
    r_empty_imp = client.post("/api/gemini/import-sentence", json={"texto_sentenca": "   "})
    assert r_empty_imp.status_code == 422

    # 8. Caminho de API Direta do Gemini (google-genai falso) com autorização de envio externo
    import app.generation.gemini_integration as gi

    chamadas: dict = {}
    _instalar_google_genai_falso(monkeypatch, texto_pseudonimizado, chamadas)
    settings_permite = gi.load_settings().model_copy(update={"permitir_envio_externo": True})
    monkeypatch.setattr(gi, "load_settings", lambda: settings_permite)

    chave_teste = "AIza" + "SyTestKey123456"
    r_api_direct = client.post(
        "/api/gemini/generate",
        json={
            "api_key": chave_teste,
            "model": "gemini-2.5-pro",
            "pseudonimizar": True,
            "consentimento_envio_externo": True,
            "custom_dosimetria_override": override,
        },
    )
    assert r_api_direct.status_code == 200
    api_data = r_api_direct.json()
    assert chamadas["api_key"] == chave_teste
    assert "CAMILA ROCHA AZEVEDO" not in chamadas["contents"]
    assert api_data["telemetria"]["modo_gemini"] == "API_DIRETA_GOOGLE_GENAI"
    _assert_auditoria_honesta(api_data["auditoria"], api_data["marcadores_pendentes"])
    assert "[OFENDIDA]" not in api_data["sentenca"]["texto_completo"]
    assert "[ACUSADO]" not in api_data["sentenca"]["texto_completo"]
    assert "CAMILA ROCHA AZEVEDO" in api_data["sentenca"]["texto_completo"]
    assert "ROBERTO GOMES SILVEIRA" in api_data["sentenca"]["texto_completo"]

    # 9. Pipeline seccional e exportação (.docx, .pdf)
    r_sec = client.post(
        "/api/sentence/generate-sectional",
        json={"use_ollama": False, "crimes_config": CRIMES_CONFIG_SINTETICO},
    )
    assert r_sec.status_code == 200
    sec_data = r_sec.json()
    _assert_auditoria_honesta(sec_data["auditoria"], sec_data["marcadores_pendentes"])
    assert sec_data["score_auditoria"] == sec_data["auditoria"]["score_confiabilidade"]
    assert "CAMILA ROCHA AZEVEDO" in sec_data["sentenca"]["texto_completo"]

    liberada = sec_data["exportacao_liberada"]
    sufixo = "" if liberada else "?rascunho=true"
    if not liberada:
        bloqueado = client.get("/api/export/docx")
        assert bloqueado.status_code == 409
        assert bloqueado.json()["erro"] == "auditoria_nao_aprovada"

    r_docx = client.get(f"/api/export/docx{sufixo}")
    assert r_docx.status_code == 200
    assert len(r_docx.content) > 5000
    assert r_docx.headers["X-SentencIA-Marca-Dagua"] == ("0" if liberada else "1")

    r_pdf_exp = client.get(f"/api/export/pdf{sufixo}")
    assert r_pdf_exp.status_code == 200
    assert len(r_pdf_exp.content) > 5000

    client.post("/api/cases/load-demo")


def test_formatting_post_sentence_orders_and_headings():
    """Valida classificação de comandos pós-sentença e cabeçalhos no exportador DOCX."""
    assert _is_post_sentence_order("a) Comunique-se ao TRE;")
    assert _is_post_sentence_order("1. Suspendam-se os direitos políticos;")
    assert not _is_section_heading("1. Suspendam-se os direitos políticos;")
    assert _is_section_heading("2.6. Da Dosimetria da Pena")
