import datetime
import json
from pathlib import Path
from types import SimpleNamespace

import docx
from fastapi.testclient import TestClient

from app.core.config import TEMPLATES_DIR
from app.export.document_exporter import exportar_docx
from app.generation.case_script_engine import (
    construir_mapa_pseudonimizacao,
    deduplicar_pecas_equivalentes,
    despseudonimizar_texto,
    gerar_script_estruturado_caso,
    pseudonimizar_texto,
)
from app.generation.sentence_generator import gerar_sentenca_estruturada
from app.ingestion.laya_classifier import PecaClassificada, _extrair_crimes_dinamicamente
from app.legal_engine.dosimetria_calculator import (
    ConcursoCrimesConfig,
    CrimeDosimetriaInput,
    DosimetriaConfig,
    calcular_dosimetria_global,
)
from app.legal_engine.penal_types_catalog import get_penal_type_for_date, list_all_penal_types
from app.main import app
from app.transcription.audio_transcriber import transcrever_midia_audiencia


def test_penal_catalog_expansion_and_laya_extraction():
    codigos = {item["code"] for item in list_all_penal_types()}
    novos = {
        "art_21_lcp",
        "art_140_cp",
        "art_140_p2_cp",
        "art_138_cp",
        "art_163_cp",
        "art_163_pu_cp",
        "art_148_p1_cp",
        "art_146_cp",
        "art_158_cp",
        "art_215a_cp",
        "art_218c_cp",
        "art_330_cp",
        "art_331_cp",
    }
    assert novos.issubset(codigos)

    spec_218c = get_penal_type_for_date("art_218c_cp", datetime.date(2025, 2, 1))
    assert spec_218c.especie_pena == "reclusao"
    assert spec_218c.min_pena.anos == 1

    texto_den = (
        "O MINISTÉRIO PÚBLICO denuncia o acusado como incurso nas penas dos arts. "
        "21 da LCP (vias de fato), art. 140, § 2º, do CP (injúria real), "
        "art. 148, § 1º, do CP (cárcere privado qualificado), art. 215-A do CP "
        "(importunação sexual), art. 218-C do CP (divulgação de cena íntima) e "
        "art. 330 do CP (desobediência), na forma da Lei 11.340/2006."
    )
    pecas_fake = [
        PecaClassificada(
            doc_id="ID-DEN-01",
            folha="fls. 01/03",
            titulo="Denúncia Oferecida pelo Ministério Público",
            data_peca="2025-02-10",
            fase="JUDICIAL",
            categoria="DENUNCIA",
            laya_confidence=0.95,
            resumo_estruturado="Denúncia",
            conteudo=texto_den,
        )
    ]
    extraidos, _, _ = _extrair_crimes_dinamicamente(pecas_fake, pecas_fake[0], None, None)
    for esp in ("art_21_lcp", "art_140_p2_cp", "art_148_p1_cp", "art_215a_cp", "art_218c_cp", "art_330_cp"):
        assert esp in extraidos


def test_deduplicacao_apf_ip_e_densidade_script():
    import copy
    from app.demo.demo_case import get_demo_case_bundle

    corpo_repetido = (
        "TERMO DE DECLARAÇÕES DA VÍTIMA. A vítima Marina Lopes Ferreira declarou perante a autoridade "
        "policial que em 14/02/2025 foi agredida fisicamente e ameaçada pelo acusado Carlos Eduardo Ferreira "
        "no interior da residência familiar. "
    ) * 18
    bundle = copy.deepcopy(get_demo_case_bundle())
    bundle["raw_documents"].extend([
        {
            "doc_id": "ID-4100102-SUB-01",
            "parent_doc_id": "ID-4100102",
            "titulo": "Termo de Declarações da Vítima (ID-4100102, fls. 10/11)",
            "data_peca": "2025-02-14",
            "folha": "fls. 10/11",
            "conteudo": corpo_repetido,
        },
        {
            "doc_id": "ID-4100202-SUB-01",
            "parent_doc_id": "ID-4100202",
            "titulo": "Termo de Declarações da Vítima (ID-4100202, fls. 45/46)",
            "data_peca": "2025-02-14",
            "folha": "fls. 45/46",
            "conteudo": corpo_repetido,
        },
    ])
    sc = gerar_script_estruturado_caso(bundle, pseudonimizar=True, densidade="compacto")
    si = gerar_script_estruturado_caso(bundle, pseudonimizar=True, densidade="integral")
    bytes_c = len(sc.script_markdown.encode("utf-8"))
    bytes_i = len(si.script_markdown.encode("utf-8"))
    assert bytes_c < bytes_i
    assert bytes_c <= 65000
    assert sc.densidade == "compacto"
    assert si.densidade == "integral"
    assert sc.pecas_deduplicadas_removidas >= 1
    assert " / " in sc.script_markdown

    # Teste unitário sintético de unificação [ID-A / ID-B]
    p1 = PecaClassificada(
        doc_id="ID-4100102-SUB-05",
        parent_doc_id="ID-4100102",
        folha="fls. 10/11",
        titulo="Termo de Declarações da Vítima (ID-4100102, fls. 10/11)",
        data_peca="2024-06-04",
        fase="INQUISITORIAL",
        categoria="BO",
        laya_confidence=0.95,
        resumo_estruturado="Relato na delegacia.",
        conteudo="A vítima declarou que foi agredida pelo ex-companheiro em sua residência na madrugada.",
    )
    p2 = PecaClassificada(
        doc_id="ID-4100202-SUB-05",
        parent_doc_id="ID-4100202",
        folha="fls. 45/46",
        titulo="Termo de Declarações da Vítima (ID-4100202, fls. 45/46)",
        data_peca="2024-06-04",
        fase="INQUISITORIAL",
        categoria="BO",
        laya_confidence=0.95,
        resumo_estruturado="Relato no IP.",
        conteudo="A vítima declarou que foi agredida pelo ex-companheiro em sua residência na madrugada.",
    )
    grupos, removidas = deduplicar_pecas_equivalentes([p1, p2])
    assert removidas == 1
    assert len(grupos) == 1
    assert "ID-4100102-SUB-05 / ID-4100202-SUB-05" == grupos[0]["unified_id"]


def test_pseudonimizacao_reversivel_ampliada():
    meta = SimpleNamespace(nome_ofendida="Maria da Silva Oliveira", nome_reu="Carlos Eduardo Souza")
    cpf_ficticio = ".".join(("123", "456", "789")) + "-00"
    tel_ficticio = "(" + "84) " + "99988-7766"
    texto = (
        f"Ofendida: Maria da Silva Oliveira, RG 1.234.567-8, CPF {cpf_ficticio}, "
        f"telefone {tel_ficticio}, residente na Rua das Flores, nº 142, Bairro Centro.\n"
        "Testemunha de Acusação: Joana Pereira Lima\n"
        "Filha: Beatriz Oliveira Souza\n"
        "Acusado: Carlos Eduardo Souza."
    )
    mapa = construir_mapa_pseudonimizacao(meta, texto)
    tokens_str = " ".join(mapa.keys())
    assert "[OFENDIDA]" in tokens_str
    assert "[ACUSADO]" in tokens_str
    assert "[CPF_01]" in tokens_str
    assert "[RG_01]" in tokens_str
    assert "[TELEFONE_01]" in tokens_str
    assert "[ENDERECO_01]" in tokens_str
    assert "[TESTEMUNHA_01]" in tokens_str
    assert "[DESCENDENTE_01]" in tokens_str

    pseudo = pseudonimizar_texto(texto, mapa)
    assert "Maria da Silva Oliveira" not in pseudo
    assert "Carlos Eduardo Souza" not in pseudo
    assert cpf_ficticio not in pseudo
    assert "99988-7766" not in pseudo
    assert "Rua das Flores" not in pseudo
    assert "Joana Pereira Lima" not in pseudo
    assert "Beatriz Oliveira Souza" not in pseudo

    restaurado = despseudonimizar_texto(pseudo, mapa)
    assert "Maria da Silva Oliveira" in restaurado
    assert "Carlos Eduardo Souza" in restaurado
    assert cpf_ficticio in restaurado
    assert "99988-7766" in restaurado
    assert "Rua das Flores" in restaurado
    assert "Joana Pereira Lima" in restaurado
    assert "Beatriz Oliveira Souza" in restaurado


def test_veredictos_por_crime_procedencia_parcial_absolvicao_e_concurso():
    client = TestClient(app)
    r_demo = client.post("/api/cases/load-demo")
    assert r_demo.status_code == 200
    assert client.post("/api/cases/demo").status_code == 200

    # 1. Procedência Parcial: condena lesão, absolve ameaça (Art. 386, VII), extingue art. 24-A (Art. 107)
    payload_parcial = {
        "use_ollama": False,
        "tipo_concurso": "material",
        "valor_dano_moral": 2500.0,
        "crimes_config": [
            {"codigo": "art_129_p13_cp", "veredicto": "condenar", "confissao": True},
            {
                "codigo": "art_147_cp",
                "veredicto": "absolver",
                "fundamento_absolvicao_art386": "art. 386, VII, do CPP (insuficiência de provas para a condenação)",
            },
            {"codigo": "art_24a_lmp", "veredicto": "extinguir"},
        ],
    }
    r_sim = client.post("/api/dosimetria/simulate", json=payload_parcial)
    assert r_sim.status_code == 200
    d_sim = r_sim.json()["dosimetria"]
    assert len(d_sim["crimes_calculados"]) == 1
    assert len(d_sim["crimes_nao_condenados"]) == 2
    assert d_sim["sem_condenacao"] is False

    r_gen = client.post("/api/sentence/generate", json=payload_parcial)
    assert r_gen.status_code == 200
    gen_data = r_gen.json()
    assert "PARCIALMENTE PROCEDENTE" in gen_data["sentenca"]["secao_dispositivo"]
    assert "ABSOLVER" in gen_data["sentenca"]["secao_dispositivo"]
    assert "EXTINGUIR A PUNIBILIDADE" in gen_data["sentenca"]["secao_dispositivo"]
    # v2: sem alucinação; o que o motor não sabe (marcos da extinção sem fundamentação do juízo e a
    # situação prisional sem alvará nos autos) vira marcador pendente — e só isso impede a aprovação.
    aud = gen_data["auditoria"]
    assert aud["ids_inexistentes"] == []
    assert aud["citacoes_nao_autorizadas"] == []
    assert aud["teses_omitidas"] == []
    assert aud["score_forma"] == 100.0
    assert "[COMPLETAR PELO JUÍZO: situação prisional do réu]" in aud["marcadores_pendentes"]
    assert any("prescrição, decadência ou perempção" in m for m in aud["marcadores_pendentes"])
    assert aud["aprovada_sem_alucinacao"] is False
    assert "respondeu solto" not in gen_data["sentenca"]["texto_completo"].lower()

    # 2. Absolvição Integral (sem condenação): Auditor Camada 4 aprova sem falso positivo das 8 vetoriais
    payload_abs = {
        "use_ollama": False,
        "crimes_config": [
            {"codigo": "art_129_p13_cp", "veredicto": "absolver"},
            {"codigo": "art_147_cp", "veredicto": "absolver"},
            {"codigo": "art_24a_lmp", "veredicto": "absolver"},
        ],
    }
    r_abs = client.post("/api/sentence/generate", json=payload_abs)
    assert r_abs.status_code == 200
    abs_data = r_abs.json()
    assert abs_data["sentenca"]["dosimetria_dados"]["sem_condenacao"] is True
    assert "IMPROCEDENTE" in abs_data["sentenca"]["secao_dispositivo"]
    assert "previsto(s) no ," not in abs_data["sentenca"]["secao_relatorio"]
    assert "art. 129, § 13" in abs_data["sentenca"]["secao_relatorio"]
    assert abs_data["auditoria"]["aprovada_sem_alucinacao"] is True

    # 2b. Condenação apenas em delito sem violência ou grave ameaça (Art. 24-A LMP) + tentativa_fracao="1/3"
    payload_nv = {
        "use_ollama": False,
        "crimes_config": [
            {"codigo": "art_24a_lmp", "veredicto": "condenar", "tentativa_fracao": "1/3"},
            {"codigo": "art_129_p13_cp", "veredicto": "absolver"},
            {"codigo": "art_147_cp", "veredicto": "absolver"},
        ],
    }
    r_nv = client.post("/api/sentence/generate", json=payload_nv)
    assert r_nv.status_code == 200
    nv_data = r_nv.json()
    assert nv_data["sentenca"]["dosimetria_dados"]["substituicao_art44_cabivel"] is True
    assert "vedada a substituição por restritivas de direitos (Súmula 588/STJ)" not in nv_data["sentenca"]["secao_dispositivo"]
    assert nv_data["auditoria"]["ids_inexistentes"] == []
    assert nv_data["auditoria"]["citacoes_nao_autorizadas"] == []
    assert nv_data["auditoria"]["marcadores_pendentes"] == ["[COMPLETAR PELO JUÍZO: situação prisional do réu]"]

    # 3. Desclassificação Art. 383 CPP (Lesão -> Vias de Fato) + Concurso Material Benéfico automático (Art. 70, § único)
    payload_desc = {
        "use_ollama": False,
        "tipo_concurso": "continuado",
        "concurso_fracao": "1/3",
        "crimes_config": [
            {
                "codigo": "art_129_p13_cp",
                "veredicto": "desclassificar",
                "crime_desclassificado": "art_21_lcp",
            },
            {"codigo": "art_147_cp", "veredicto": "condenar"},
        ],
    }
    r_desc = client.post("/api/dosimetria/simulate", json=payload_desc)
    assert r_desc.status_code == 200
    cods = [c["crime_spec"]["code"] for c in r_desc.json()["dosimetria"]["crimes_calculados"]]
    assert "art_21_lcp" in cods
    assert r_desc.json()["dosimetria"]["tipo_concurso"] == "material"
    assert any("benéfico" in a.lower() for a in r_desc.json()["dosimetria"]["alertas_globais"])

    # 3b. Crime Continuado efetivo quando exasperação é menor que o cúmulo material (ex.: art_129_p13_cp + art_24a_lmp)
    payload_cont = {
        "use_ollama": False,
        "tipo_concurso": "continuado",
        "concurso_fracao": "1/3",
        "crimes_config": [
            {"codigo": "art_129_p13_cp", "veredicto": "condenar"},
            {"codigo": "art_24a_lmp", "veredicto": "condenar"},
        ],
    }
    r_cont = client.post("/api/dosimetria/simulate", json=payload_cont)
    assert r_cont.status_code == 200
    assert r_cont.json()["dosimetria"]["tipo_concurso"] == "continuado"

    # 4. Regra benéfica do Art. 70 § único (Concurso Formal nunca excede o Concurso Material)
    dt = datetime.date(2025, 2, 14)
    c2 = CrimeDosimetriaInput(crime_code="art_147_cp", data_fato=dt)
    c_big = CrimeDosimetriaInput(crime_code="art_129_p13_cp", data_fato=dt)
    m_diff = calcular_dosimetria_global(
        [c_big, c2], DosimetriaConfig(), ConcursoCrimesConfig(tipo_concurso="material")
    )
    f_diff = calcular_dosimetria_global(
        [c_big, c2], DosimetriaConfig(), ConcursoCrimesConfig(tipo_concurso="formal", fracao_exasperacao=(2, 3))
    )
    assert f_diff.pena_total_unificada.to_total_days() <= m_diff.pena_total_unificada.to_total_days()


def test_update_and_audit_progress_timbre_docx_e_desambiguacao_ofendida(tmp_path: Path, monkeypatch):
    client = TestClient(app)
    assert client.post("/api/cases/load-demo").status_code == 200

    # Progresso de upload (JSON + SSE stream)
    r_prog = client.get("/api/cases/upload-progress")
    assert r_prog.status_code == 200
    assert "percent" in r_prog.json()

    r_sse = client.get("/api/cases/upload-progress?stream=1")
    assert r_sse.status_code == 200
    assert "text/event-stream" in r_sse.headers.get("content-type", "")
    assert "data:" in r_sse.text

    # Build script compacto via API
    r_sc = client.post("/api/gemini/build-script", json={"pseudonimizar": True, "densidade": "compacto"})
    assert r_sc.status_code == 200
    assert r_sc.json()["script"]["densidade"] == "compacto"

    # Edição direta na folha + Re-Auditoria
    r_gen = client.post("/api/sentence/generate", json={"use_ollama": False})
    assert r_gen.status_code == 200
    gen_id_1 = r_gen.json()["generation_id"]
    texto_editado = r_gen.json()["sentenca"]["texto_completo"].replace(
        "Passo a decidir e fundamentar",
        "Passo a decidir e fundamentar, com análise pormenorizada das provas colhidas",
    )

    r_empty = client.post("/api/sentence/update-and-audit", json={"texto_completo": "   "})
    assert r_empty.status_code == 422

    r_upd = client.post("/api/sentence/update-and-audit", json={"texto_completo": texto_editado})
    assert r_upd.status_code == 200
    upd_data = r_upd.json()
    assert upd_data["status"] == "updated"
    assert upd_data["generation_id"] == gen_id_1 + 1
    assert "análise pormenorizada das provas colhidas" in upd_data["sentenca"]["texto_completo"]
    assert upd_data["auditoria"]["aprovada_sem_alucinacao"] is True

    # Timbre TJRN no .docx mantendo margens ABNT (cria timbre sintético em tmp_path se data/ não tiver o arquivo)
    timbre = TEMPLATES_DIR / "timbre_tjrn.docx"
    if not timbre.exists():
        timbre = tmp_path / "timbre_tjrn.docx"
        doc_timbre = docx.Document()
        doc_timbre.sections[0].header.paragraphs[0].text = (
            "PODER JUDICIÁRIO DO ESTADO DO RIO GRANDE DO NORTE"
        )
        doc_timbre.save(str(timbre))
        monkeypatch.setattr("app.export.document_exporter.TEMPLATES_DIR", tmp_path)
    assert timbre.exists()
    out_docx = tmp_path / "sentenca_timbre.docx"
    sent_obj = gerar_sentenca_estruturada(
        client.get("/api/cases/active").json() if False else __import__("app.demo.demo_case", fromlist=["get_demo_case_bundle"]).get_demo_case_bundle(),
        use_ollama_if_available=False,
    )
    exportar_docx(sent_obj, out_docx)
    doc = docx.Document(str(out_docx))
    assert round(doc.sections[0].top_margin.cm, 1) == 3.0
    assert round(doc.sections[0].left_margin.cm, 1) == 3.0
    assert round(doc.sections[0].bottom_margin.cm, 1) == 2.0
    assert round(doc.sections[0].right_margin.cm, 1) == 2.0
    header_txt = "\n".join(p.text for p in doc.sections[0].header.paragraphs)
    assert "RIO GRANDE DO NORTE" in header_txt

    # Desambiguação de duas mídias OFENDIDA com sufixo _001 no mesmo diretório
    m1 = tmp_path / "Vitima_Maria_001.mp4"
    m2 = tmp_path / "Vitima_Ana_001.mp4"
    m1.write_bytes(b"fake_mp4_1")
    m2.write_bytes(b"fake_mp4_2")
    for mp, dep in ((m1, "Maria"), (m2, "Ana")):
        cache = mp.with_suffix(mp.suffix + ".transcript_v2.json")
        cache.write_text(
            json.dumps(
                {
                    "size": mp.stat().st_size,
                    "media_id": "ID-MIDIA-001",
                    "arquivo": mp.name,
                    "depoente": dep,
                    "papel": "OFENDIDA",
                    "duracao": "01:00",
                    "segmentos": [{"timestamp": "00:00 - 00:30", "texto_literal": f"Depoimento de {dep}."}],
                    "engine": "Mock",
                }
            ),
            encoding="utf-8",
        )
    tr1 = transcrever_midia_audiencia(m1, papel="OFENDIDA")
    tr2 = transcrever_midia_audiencia(m2, papel="OFENDIDA")
    assert tr1["media_id"] != tr2["media_id"]
    assert tr1["media_id"].endswith("-001")
    assert tr2["media_id"].endswith("-001")


def test_laya_extraction_realistic_clauses_and_witness_pseudonymization():
    # Regressão: frases com "incurso nas sanções do art. ..." não podem truncar no ponto de "art."
    frases = [
        ("incurso nas sanções do art. 138 do CP", "art_138_cp"),
        ("incurso nas sanções do art. 21 do Decreto-Lei nº 3.688/1941", "art_21_lcp"),
        ("incurso nas sanções do art. 21 da Lei das Contravenções Penais", "art_21_lcp"),
        ("incurso nas sanções do art. 163, p.ú., do CP", "art_163_pu_cp"),
        ("incurso nas sanções do art. 146, caput, do CP", "art_146_cp"),
        ("incurso nas sanções do art. 158, caput, do Código Penal", "art_158_cp"),
        ("incurso nas sanções do art. 331 do CP", "art_331_cp"),
        ("incurso nas sanções do art. 140, caput, do CP", "art_140_cp"),
    ]
    for frase, esperado in frases:
        p = PecaClassificada(
            doc_id="ID-DEN-TEST",
            folha="fl. 01",
            titulo="Denúncia",
            data_peca="2025-02-01",
            fase="JUDICIAL",
            categoria="DENUNCIA",
            laya_confidence=0.95,
            resumo_estruturado="Denúncia",
            conteudo=f"O MP denuncia o réu como {frase}.",
        )
        cods, _, _ = _extrair_crimes_dinamicamente([p], p, None, None)
        assert esperado in cods, f"Falhou para '{frase}': obteve {cods}"

    # Regressão: testemunhas case-insensitive e policiais + demo oral
    from app.demo.demo_case import get_demo_case_bundle

    sc_demo = gerar_script_estruturado_caso(get_demo_case_bundle(), pseudonimizar=True, densidade="compacto")
    assert "Roberto Silva" not in sc_demo.script_markdown
    assert "João Batista" not in sc_demo.script_markdown

    # Regressão: frontend JS (app.js + modulos/) deve apontar para /api/cases/load-demo e ler entidades_extraidas
    js_dir = Path(__file__).resolve().parent.parent / "app" / "static" / "js"
    app_js = "\n".join(
        p.read_text(encoding="utf-8")
        for p in [js_dir / "app.js", *sorted((js_dir / "modulos").glob("*.js"))]
    )
    assert "/api/cases/load-demo" in app_js
    assert "entidades_extraidas" in app_js
    assert "entidades_ner" not in app_js


def test_audio_transcriber_gemini_files_api_large_audio_and_whisper_prompt(tmp_path: Path, monkeypatch):
    import sys
    import types
    import subprocess
    import app.transcription.audio_transcriber as at

    media = tmp_path / "Oitiva_Vitima_001.mp4"
    media.write_bytes(b"fake_video")

    # 1. Simula Gemini Files API para MP3 > 18 MB
    calls = {"uploaded": False, "deleted": False, "prompt_seen": ""}

    def fake_subprocess_run(cmd, *args, **kwargs):
        out_path = Path(cmd[-1])
        if out_path.suffix == ".mp3":
            out_path.write_bytes(b"0" * (19 * 1024 * 1024))
        return SimpleNamespace(returncode=0, stdout="60.0", stderr="")

    class FakeFilesAPI:
        def upload(self, file):
            calls["uploaded"] = True
            return SimpleNamespace(name="files/audio-123")

        def delete(self, name):
            calls["deleted"] = (name == "files/audio-123")

    class FakeModelsAPI:
        def generate_content(self, model, contents):
            return SimpleNamespace(text='[{"timestamp":"00:00 - 00:30","texto_literal":"Relato completo."}]')

    class FakeClient:
        def __init__(self, api_key):
            self.files = FakeFilesAPI()
            self.models = FakeModelsAPI()

    fake_google = types.ModuleType("google")
    fake_genai = types.ModuleType("google.genai")
    fake_genai.Client = FakeClient
    fake_types = types.ModuleType("google.genai.types")
    fake_types.Part = SimpleNamespace(from_bytes=lambda **kw: kw)
    fake_google.genai = fake_genai

    monkeypatch.setattr(at, "release_ollama_vram_for_whisper", lambda: None)
    monkeypatch.setattr(at, "find_binary", lambda nome: nome)
    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr("app.core.secrets.get_secret", lambda nome: "")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)
    # v2: Whisper local é o primeiro motor; aqui ele está ausente para exercitar o motor externo.
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    monkeypatch.setattr(
        "app.core.config.load_settings",
        lambda: SimpleNamespace(permitir_envio_externo=True, whisper_model="small", whisper_device="cpu"),
    )

    # 1a. Sem consentimento explícito na requisição: nada sai da máquina (LGPD / segredo de justiça).
    res_sem = at.transcrever_midia_audiencia(media, papel="OFENDIDA", known_ofendida="Maria Silva", usar_cache=False)
    assert calls["uploaded"] is False
    assert res_sem["status"] == "FALHOU"
    assert res_sem["segmentos"] == []
    assert "consentimento" in res_sem["erro"]

    # 1b. Com permissão nas configurações + consentimento: Gemini Files API para MP3 > 18 MB.
    res_gem = at.transcrever_midia_audiencia(
        media, papel="OFENDIDA", known_ofendida="Maria Silva", consentimento_envio_externo=True, usar_cache=False
    )
    assert calls["uploaded"] is True
    assert calls["deleted"] is True
    assert res_gem["engine"] == "Gemini Multimodal Audio PT-BR"

    # 2. Simula faster-whisper com initial_prompt forense
    media2 = tmp_path / "Oitiva_Testemunha_001.mp4"
    media2.write_bytes(b"fake_video_2")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.core.config.load_settings",
        lambda: SimpleNamespace(permitir_envio_externo=False, whisper_model="small", whisper_device="cpu"),
    )

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, path, language=None, vad_filter=None, initial_prompt="", **_extra):
            calls["prompt_seen"] = initial_prompt
            return [SimpleNamespace(start=0, end=12, text="Confirmou os fatos.")], {}

    fake_fw = types.ModuleType("faster_whisper")
    fake_fw.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)

    res_fw = at.transcrever_midia_audiencia(
        media2,
        depoente="João PM",
        papel="TESTEMUNHA_POLICIAL",
        known_ofendida="Maria Silva",
        known_reu="Carlos Souza",
        usar_cache=False,
    )
    assert "Lei Maria da Penha" in calls["prompt_seen"]
    assert "Maria Silva" in calls["prompt_seen"]
    assert res_fw["engine"].startswith("faster-whisper")
