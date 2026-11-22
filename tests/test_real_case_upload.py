# -*- coding: utf-8 -*-
"""
Teste de integração do upload de autos e mídias substituindo o caso de demonstração,
com dados 100% fictícios (processo sintético 0807777-11.2025.8.20.0001).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pymupdf

from app.main import app


def _criar_pdf_pje_sintetico(destino: Path) -> Path:
    """Cria um PDF sintético com sumário (TOC) no formato do PJe e 3 peças oficiais."""
    doc = pymupdf.open()
    pecas = [
        (
            "Petição | NUM: 7700101 | 20/01/2025",
            (
                "PODER JUDICIÁRIO DO ESTADO DO RIO GRANDE DO NORTE\n"
                "Processo nº 0807777-11.2025.8.20.0001\n"
                "MINISTÉRIO PÚBLICO DO ESTADO DO RIO GRANDE DO NORTE\n"
                "Vítima: HELENA MARTINS PRADO.\n"
                "Denunciado: EDUARDO NUNES TEIXEIRA.\n"
                "O Ministério Público VEM OFERECER DENÚNCIA contra EDUARDO NUNES TEIXEIRA, brasileiro, "
                "incurso nas penas do art. 147-A do Código Penal (perseguição), "
                "em contexto de violência doméstica e familiar contra a mulher (Lei nº 11.340/2006), "
                "em prejuízo de sua ex-companheira, HELENA MARTINS PRADO, "
                "por fato ocorrido em 10/01/2025."
            ),
        ),
        (
            "Decisão | NUM: 7700102 | 25/01/2025",
            (
                "RECEBIMENTO DE DENÚNCIA\n"
                "Processo nº 0807777-11.2025.8.20.0001\n"
                "DECISÃO. Recebo a denúncia em 25/01/2025 oferecida pelo Ministério Público em desfavor de "
                "EDUARDO NUNES TEIXEIRA pela prática do delito do art. 147-A do Código Penal "
                "contra a ofendida HELENA MARTINS PRADO. Cite-se o acusado."
            ),
        ),
        (
            "Alegações Finais | NUM: 7700202 | 10/03/2026",
            (
                "MINISTÉRIO PÚBLICO DO ESTADO DO RIO GRANDE DO NORTE\n"
                "ALEGAÇÕES FINAIS DO MINISTÉRIO PÚBLICO\n"
                "Processo nº 0807777-11.2025.8.20.0001\n"
                "O Ministério Público vem apresentar suas alegações finais e requer a procedência "
                "da ação penal para condenar EDUARDO NUNES TEIXEIRA nas penas do art. 147-A do Código Penal."
            ),
        ),
        (
            "Memoriais | NUM: 7700303 | 15/03/2026",
            (
                "DEFENSORIA PÚBLICA DO ESTADO DO RIO GRANDE DO NORTE\n"
                "ALEGAÇÕES FINAIS POR MEMORIAIS DA DEFESA\n"
                "Processo nº 0807777-11.2025.8.20.0001\n"
                "A Defensoria Pública vem apresentar suas alegações finais em favor de EDUARDO NUNES TEIXEIRA, "
                "pugnando pela absolvição por insuficiência de provas (art. 386, VII, do CPP) e, "
                "subsidiariamente, a fixação da pena no mínimo legal."
            ),
        ),
    ]
    toc = []
    for idx, (titulo_toc, texto) in enumerate(pecas, start=1):
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(40, 40, 550, 780), texto, fontsize=10)
        toc.append([1, titulo_toc, idx])
    doc.set_toc(toc)
    doc.save(str(destino))
    doc.close()
    return destino


def test_real_case_upload_replaces_demo_and_transcribes_media(tmp_path: Path, monkeypatch):
    """
    Ao anexar um processo (PDF PJe 0807777-11.2025.8.20.0001.pdf) e mídias (.mp4), o sistema deve:
    1. Remover 100% dos documentos e mídias da versão demo anterior (ID-4100...);
    2. Segmentar o PDF do PJe pelos seus documentos oficiais (ID-7700101, ID-7700102, ID-7700202, ID-7700303);
    3. Extrair os metadados do processo (0807777-11.2025.8.20.0001, vítima Helena Martins Prado,
       réu Eduardo Nunes Teixeira, delito art_147a_cp);
    4. Transcrever e analisar as mídias (.mp4) da vítima Helena sem textos fixos da demo;
    5. Gerar a sentença penal completa e auditada referente ao processo anexado.
    """
    import app.main as main_module
    from app.transcription.audio_transcriber import _infer_speaker_and_role

    pdf_path = _criar_pdf_pje_sintetico(tmp_path / "0807777-11.2025.8.20.0001.pdf")
    mp4_1 = tmp_path / "div_Audiencias-20260305_100511-Depoimento da Vítima Helena_001.mp4"
    mp4_2 = tmp_path / "div_Audiencias-20260305_100511-Depoimento da Vítima Helena_002.mp4"
    mp4_1.write_bytes(b"fake_mp4_helena_1")
    mp4_2.write_bytes(b"fake_mp4_helena_2")

    def _fake_transcrever(m_path, depoente="OFENDIDA / TESTEMUNHA", papel="OFENDIDA", known_ofendida="", known_reu=""):
        media_id, nome_dep, papel_inf = _infer_speaker_and_role(
            m_path.name, depoente, papel, known_ofendida=known_ofendida, known_reu=known_reu
        )
        return {
            "media_id": media_id,
            "arquivo": m_path.name,
            "depoente": nome_dep,
            "papel": papel_inf,
            "duracao": "02:30",
            "segmentos": [
                {
                    "inicio": "00:05",
                    "fim": "01:10",
                    "texto": "Relatou que o ex-companheiro Eduardo Nunes Teixeira passou a persegui-la diariamente no trajeto do trabalho.",
                },
                {
                    "inicio": "01:11",
                    "fim": "02:20",
                    "texto": "Confirmou que precisou alterar sua rotina e solicitar medidas protetivas de urgência.",
                },
            ],
            "sintese": "A ofendida Helena Martins Prado confirmou a perseguição reiterada praticada pelo acusado.",
        }

    cases_tmp = tmp_path / "cases"
    cases_tmp.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main_module, "CASES_DIR", cases_tmp)
    monkeypatch.setattr(main_module, "transcrever_midia_audiencia", _fake_transcrever)

    client = TestClient(app)
    client.post("/api/cases/load-demo")

    with open(pdf_path, "rb") as f_pdf, open(mp4_1, "rb") as f_m1, open(mp4_2, "rb") as f_m2:
        r_up = client.post(
            "/api/cases/upload-files",
            files=[
                ("files", (pdf_path.name, f_pdf, "application/pdf")),
                ("files", (mp4_1.name, f_m1, "video/mp4")),
                ("files", (mp4_2.name, f_m2, "video/mp4")),
            ],
            data={"depoente_padrao": "OFENDIDA / TESTEMUNHA"},
        )

    assert r_up.status_code == 200, r_up.text
    up_data = r_up.json()

    # 1. Nenhuma peça da versão demo pode permanecer ativa
    doc_ids = [p["doc_id"] for p in up_data["pecas"]]
    assert not any(d.startswith("ID-4100") for d in doc_ids), f"Peças da demo não foram removidas: {doc_ids}"
    assert "ID-7700101" in doc_ids
    assert "ID-7700202" in doc_ids
    assert "ID-7700303" in doc_ids

    # 2. Metadados de topo e metadados estruturados atualizados para o processo anexado
    assert up_data["numero_processo"] == "0807777-11.2025.8.20.0001"
    assert "HELENA MARTINS PRADO" in up_data["ofendida"].upper()
    assert "EDUARDO NUNES TEIXEIRA" in up_data["reu"].upper()
    assert "art_147a_cp" in up_data["metadados"]["crimes_imputados"]

    # 3. Mídias (.mp4) transcritas e analisadas sem texto estático da demo
    depoentes = up_data["matriz_oral"]["depoentes"]
    assert len(depoentes) == 2
    for dep in depoentes:
        assert dep["papel"] == "OFENDIDA"
        assert "HELENA" in dep["depoente"].upper()
        assert len(dep["segmentos_literais"]) >= 2
        assert dep["media_id"].startswith("ID-MIDIA-") and dep["media_id"].endswith(("-001", "-002"))

    # 4. Avanço para Dosimetria e Geração da Sentença
    r_sim = client.post("/api/dosimetria/simulate", json={})
    assert r_sim.status_code == 200
    sim_data = r_sim.json()
    crimes_calc = [c["crime_spec"]["code"] for c in sim_data["dosimetria"]["crimes_calculados"]]
    assert crimes_calc == ["art_147a_cp"]

    # Sem veredicto do juízo a geração de caso real devolve 422 (nunca condena por padrão)
    r_pend = client.post("/api/sentence/generate", json={"use_ollama": False})
    assert r_pend.status_code == 422
    assert r_pend.json()["erro"] == "decisao_judicial_pendente"

    r_sent = client.post(
        "/api/sentence/generate",
        json={"use_ollama": False, "crimes_config": [{"codigo": "art_147a_cp", "veredicto": "condenar"}]},
    )
    assert r_sent.status_code == 200, r_sent.text
    sent_data = r_sent.json()
    texto_sentenca = sent_data["sentenca"]["texto_completo"]

    assert "0807777-11.2025.8.20.0001" in texto_sentenca
    assert "HELENA MARTINS PRADO" in texto_sentenca.upper()
    assert "EDUARDO NUNES TEIXEIRA" in texto_sentenca.upper()
    assert "ID-7700101" in texto_sentenca
    assert "ID-7700303" in texto_sentenca

    auditoria = sent_data["auditoria"]
    marcadores = sent_data.get("marcadores_pendentes") or []
    assert auditoria["score_forma"] == 100.0, auditoria["detalhes_camadas"]
    assert auditoria["ids_inexistentes"] == []
    assert auditoria["citacoes_nao_autorizadas"] == []
    assert auditoria["penas_divergentes"] == []
    assert auditoria["aprovada_sem_alucinacao"] is (not marcadores)
    assert (auditoria["score_confiabilidade"] == 100.0) is (not marcadores)

    # Restaura o caso demo ao final
    client.post("/api/cases/load-demo")
