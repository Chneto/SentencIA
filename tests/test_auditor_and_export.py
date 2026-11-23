from pathlib import Path
from fastapi.testclient import TestClient

from app.demo.demo_case import get_demo_case_bundle
from app.ingestion.laya_classifier import classificar_pecas_autos, extrair_metadados_processo
from app.generation.sentence_generator import gerar_sentenca_estruturada
from app.generation.hallucination_auditor import auditar_sentenca
from app.export.document_exporter import exportar_docx, exportar_html_pje, exportar_pdf
from app.main import app


def test_auditor_anti_alucinacao_4_camadas():
    """Verifica que o Auditor valida IDs existentes, Súmulas da base fechada, completude das teses defensivas, LC 95/98 e art. 21 da Lei 11.340/06."""
    demo = get_demo_case_bundle()
    pecas = classificar_pecas_autos(demo["raw_documents"])
    meta = extrair_metadados_processo(pecas)

    sentenca = gerar_sentenca_estruturada(
        case_bundle=demo,
        use_ollama_if_available=False,  # Determinístico para teste unitário rápido
    )
    relatorio_auditoria = auditar_sentenca(
        texto_sentenca=sentenca.texto_completo,
        pecas_autos=pecas,
        teses_defesa=meta.teses_defesa,
    )

    assert relatorio_auditoria.aprovada_sem_alucinacao is True
    assert relatorio_auditoria.ids_inexistentes == []
    assert relatorio_auditoria.citacoes_nao_autorizadas == []
    assert relatorio_auditoria.teses_omitidas == []
    assert relatorio_auditoria.conformidade_art21_lmp is True
    assert relatorio_auditoria.conformidade_lc95_98 is True
    assert relatorio_auditoria.conformidade_datas_padrao_br is True
    assert relatorio_auditoria.conformidade_abnt_citacoes is True
    assert relatorio_auditoria.conformidade_estilo_cnj_amb is True

    # Se injetarmos um ID falso e um REsp inventado, o auditor deve detectar e reprovar!
    texto_adulterado = sentenca.texto_completo + "\nConforme documento de ID-99999999 e REsp 9.876.543/XY."
    auditoria_falha = auditar_sentenca(
        texto_sentenca=texto_adulterado,
        pecas_autos=pecas,
        teses_defesa=meta.teses_defesa,
    )
    assert auditoria_falha.aprovada_sem_alucinacao is False
    assert "ID-99999999" in auditoria_falha.ids_inexistentes
    assert any("9.876.543" in c for c in auditoria_falha.citacoes_nao_autorizadas)
    # v2.0.0: score coerente com a aprovação (toda reprovação desconta) e forma separada do conteúdo.
    assert relatorio_auditoria.score_confiabilidade == 100.0
    assert auditoria_falha.score_confiabilidade == 50.0
    assert auditoria_falha.score_forma == 100.0


def test_auditor_camada_4_detecta_omissao_das_oito_circunstancias_art59():
    """Garante que a Camada 4 da auditoria detecta a omissão de qualquer uma das 8 circunstâncias do art. 59 do CP."""
    demo = get_demo_case_bundle()
    pecas = classificar_pecas_autos(demo["raw_documents"])
    meta = extrair_metadados_processo(pecas)

    sentenca = gerar_sentenca_estruturada(case_bundle=demo, use_ollama_if_available=False)
    auditoria_valida = auditar_sentenca(sentenca.texto_completo, pecas, meta.teses_defesa)
    assert auditoria_valida.conformidade_oito_circunstancias_art59 is True

    # Suprime referências a Conduta Social e Personalidade
    texto_mutilado = sentenca.texto_completo.replace("CONDUTA SOCIAL:", "COMPORTAMENTO GERAL:")
    texto_mutilado = texto_mutilado.replace("conduta social", "comportamento geral")
    texto_mutilado = texto_mutilado.replace("PERSONALIDADE DO AGENTE:", "PERFIL PSICOLÓGICO:")
    texto_mutilado = texto_mutilado.replace("personalidade", "perfil psíquico")

    auditoria_incompleta = auditar_sentenca(texto_mutilado, pecas, meta.teses_defesa)
    assert auditoria_incompleta.conformidade_oito_circunstancias_art59 is False
    assert auditoria_incompleta.aprovada_sem_alucinacao is False
    assert any("Conduta Social" in d["descricao"] for d in auditoria_incompleta.detalhes_camadas if "Art. 59" in d["descricao"] or "4." in d["camada"])


def test_auditor_camada_4_detecta_falta_de_valoracao_art59():
    """Garante que a Camada 4 da auditoria detecta quando um vetor do art. 59 é apenas citado sem valoração judicial expressa."""
    demo = get_demo_case_bundle()
    pecas = classificar_pecas_autos(demo["raw_documents"])
    meta = extrair_metadados_processo(pecas)

    sentenca = gerar_sentenca_estruturada(case_bundle=demo, use_ollama_if_available=False)
    # Altera 'PERSONALIDADE DO AGENTE' para citar o vetor sem qualquer termo de valoração
    texto_sem_val = sentenca.texto_completo.replace(
        "d) PERSONALIDADE DO AGENTE: Não há nos autos laudos periciais, relatórios psicossociais ou elementos científicos seguros e idôneos que permitam aquilatar desfavoravelmente o perfil psíquico ou a personalidade do sentenciado. Segundo pacífica jurisprudência do Superior Tribunal de Justiça e as diretrizes da Escola Nacional de Formação e Aperfeiçoamento de Magistrados (ENFAM), é defeso ao julgador emitir juízo subjetivo sobre a personalidade sem subsídios técnicos formais. Circunstância judicial neutra.",
        "d) PERSONALIDADE DO AGENTE: Observação fática genérica sobre o comportamento.",
    )

    auditoria = auditar_sentenca(texto_sem_val, pecas, meta.teses_defesa)
    assert auditoria.conformidade_oito_circunstancias_art59 is False
    assert auditoria.aprovada_sem_alucinacao is False
    assert any("valoração expressa" in d["descricao"] for d in auditoria.detalhes_camadas if "4." in d["camada"])


def test_exportacao_tripla_docx_html_pdf(tmp_path: Path):
    """Verifica a exportação nos 3 formatos (.docx forense, HTML limpo para PJe/eproc e .pdf)."""
    demo = get_demo_case_bundle()
    sentenca = gerar_sentenca_estruturada(case_bundle=demo, use_ollama_if_available=False)

    docx_path = exportar_docx(sentenca, tmp_path / "sentenca_teste.docx")
    assert docx_path.exists()
    assert docx_path.stat().st_size > 5000

    html_pje = exportar_html_pje(sentenca)
    assert "SENTENÇA" in html_pje
    assert "text-align: justify" in html_pje
    assert "text-indent: 1.25cm" in html_pje
    assert "margin-left: 4cm" in html_pje
    assert "art. 21 da Lei nº 11.340/2006" in html_pje

    pdf_path = exportar_pdf(sentenca, tmp_path / "sentenca_teste.pdf")
    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 2000


def test_exportacao_com_marca_dagua_minuta_nao_auditada(tmp_path: Path):
    """v2.0.0: com marca d'água, .docx/.html/.pdf exibem 'MINUTA NÃO AUDITADA'; sem ela, nada muda."""
    import docx as _docx
    import pymupdf as _pymupdf
    from app.export.document_exporter import MARCA_DAGUA_NAO_AUDITADA

    demo = get_demo_case_bundle()
    sentenca = gerar_sentenca_estruturada(case_bundle=demo, use_ollama_if_available=False)

    docx_marcado = exportar_docx(sentenca, tmp_path / "minuta.docx", marca_dagua=MARCA_DAGUA_NAO_AUDITADA)
    doc = _docx.Document(str(docx_marcado))
    cabecalhos = " ".join(p.text for s in doc.sections for p in s.header.paragraphs)
    assert MARCA_DAGUA_NAO_AUDITADA in cabecalhos
    assert MARCA_DAGUA_NAO_AUDITADA in doc.paragraphs[0].text

    docx_limpo = exportar_docx(sentenca, tmp_path / "final.docx")
    doc_limpo = _docx.Document(str(docx_limpo))
    assert MARCA_DAGUA_NAO_AUDITADA not in " ".join(p.text for p in doc_limpo.paragraphs)
    assert MARCA_DAGUA_NAO_AUDITADA not in " ".join(p.text for s in doc_limpo.sections for p in s.header.paragraphs)

    html_marcado = exportar_html_pje(sentenca, marca_dagua=MARCA_DAGUA_NAO_AUDITADA)
    assert "MINUTA NÃO AUDITADA" in html_marcado
    assert "MINUTA NÃO AUDITADA" not in exportar_html_pje(sentenca)

    pdf_marcado = exportar_pdf(sentenca, tmp_path / "minuta.pdf", marca_dagua=MARCA_DAGUA_NAO_AUDITADA)
    with _pymupdf.open(str(pdf_marcado)) as pdf:
        assert pdf.page_count >= 1
        for pagina in pdf:
            assert "MINUTA" in pagina.get_text()
    pdf_limpo = exportar_pdf(sentenca, tmp_path / "final.pdf")
    with _pymupdf.open(str(pdf_limpo)) as pdf:
        assert all("MINUTA NÃO AUDITADA" not in pagina.get_text() for pagina in pdf)


def test_fastapi_endpoints():
    """Verifica as rotas principais da API FastAPI local."""
    client = TestClient(app)

    r_health = client.get("/api/health")
    assert r_health.status_code == 200
    assert r_health.json()["status"] == "ok"

    r_demo = client.post("/api/cases/load-demo")
    assert r_demo.status_code == 200
    data = r_demo.json()
    assert "case_id" in data
    assert len(data["pecas"]) >= 9

    r_gen = client.post("/api/sentence/generate", json={"use_ollama": False})
    assert r_gen.status_code == 200
    gen_data = r_gen.json()
    assert gen_data["auditoria"]["aprovada_sem_alucinacao"] is True
    assert "DISPOSITIVO" in gen_data["sentenca"]["texto_completo"]


def test_v2_studio_editorial_liquid_glass_ui_contract():
    """Garante o contrato da interface v3.0 'Gabinete Editorial Limpo': sem emojis, tema claro padrão, 4 estações diretas, folha ~85% editável e remoção da poluição visual."""
    import re

    client = TestClient(app)
    r_index = client.get("/")
    assert r_index.status_code == 200
    html = r_index.text

    emoji_re = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
    assert emoji_re.findall(html) == []
    assert 'data-theme="light"' in html

    # Remoção da navegação concorrente e velocímetros/anéis SVG gigantes
    assert "studio-pill-dock" not in html
    assert "svgVramGaugeArc" not in html
    assert "dosimetrySvgRing" not in html
    assert "magneticDropzone" not in html

    # 4 Estações Diretas + upload bipartido limpo + barra de progresso
    for st in ('data-station="autos"', 'data-station="analise"', 'data-station="dosimetria"', 'data-station="sentenca"'):
        assert st in html
    assert "formUploadPdfs" in html
    assert "formUploadMedias" in html
    assert "uploadProgressContainer" in html

    # Dosimetria por delito + Sentença Toolbar 1 linha + Folha editável + Gavetas
    assert "dosimetryCrimeTabs" in html
    assert "selTipoConcurso" in html
    assert "sentence-toolbar" in html
    assert "selScriptDensidade" in html
    assert "sentence-paper-wrapper" in html
    assert "btnReauditEdited" in html
    assert "auditTraceabilityTableBody" in html
    assert "liquidIdPopover" in html


def test_dosimetria_override_synchronization_in_sentence_generation():
    """Garante que os vetores extras da calculadora (motivos, circunstâncias, consequências, reincidência) sincronizam com a sentença gerada."""
    client = TestClient(app)
    r_demo = client.post("/api/cases/load-demo")
    assert r_demo.status_code == 200
    payload = {
        "use_ollama": False,
        "criterio_primeira_fase": "um_oitavo_intervalo",
        "culpabilidade_lesao": True,
        "motivos_lesao": True,
        "circunstancias_lesao": True,
        "consequencias_lesao": False,
        "confissao_art24a": True,
        "reincidente": True,
        "agravante_art61f": True,
        "valor_dano_moral": 5000.0,
    }
    r_sim = client.post("/api/dosimetria/simulate", json=payload)
    assert r_sim.status_code == 200
    sim_data = r_sim.json()

    r_gen = client.post("/api/sentence/generate", json=payload)
    assert r_gen.status_code == 200
    gen_data = r_gen.json()

    assert (
        gen_data["sentenca"]["dosimetria_dados"]["pena_total_unificada"]
        == sim_data["dosimetria"]["pena_total_unificada"]
    )
    assert gen_data["sentenca"]["dosimetria_dados"]["pena_total_unificada"]["anos"] >= 5
    assert "5,000.00" in gen_data["sentenca"]["secao_dispositivo"] or "5.000,00" in gen_data["sentenca"]["secao_dispositivo"]


def test_laya_executive_summary_and_ner_entities():
    """Garante que o Laya S1 extrai entidades estruturadas (NER) e síntese executiva sem duplicar os primeiros 240 caracteres da peça."""
    demo = get_demo_case_bundle()
    pecas = classificar_pecas_autos(demo["raw_documents"])
    for p in pecas:
        assert p.resumo_estruturado.startswith("Síntese Laya S1:")
        assert p.resumo_estruturado != p.conteudo[:240].strip() + "..."
        assert len(p.entidades_extraidas) >= 2
        assert any(e["tipo"] == "ID" for e in p.entidades_extraidas)


