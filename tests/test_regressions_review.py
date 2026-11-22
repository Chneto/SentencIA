# -*- coding: utf-8 -*-
"""
Testes de regressão da revisão técnica (SentencIA):
- Sub-segmentação de peças compostas (APF/IP -> Laudos UN-9101-2025 / UN-9102-2025) e distinção Custódia vs AIJ
- Ausência de vazamento de rodapé PJe, discurso direto bruto de ASR, falsa reincidência e Dispositivo duplicado
- Consolidação de mídias multi-parte em discurso indireto forense único por interlocutor
- Telemetria honesta de fallback quando Ollama está offline e propagação de secoes_habilitadas
- Blindagem cirúrgica por seção na importação externa do Gemini (sem sobrescrever seções válidas)
"""

import os
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.demo.demo_case import get_demo_case_bundle
from app.generation.case_script_engine import gerar_script_estruturado_caso
from app.generation.gemini_integration import importar_e_auditar_sentenca_externa
from app.generation.sectional_pipeline import (
    SecaoTipo,
    SectionalPipelineEngine,
    StatusSecao,
)
from app.generation.sentence_generator import gerar_sentenca_estruturada
from app.ingestion.laya_classifier import classificar_pecas_autos, extrair_metadados_processo
from app.main import app
from app.transcription.oral_proof_analyzer import (
    consolidar_prova_oral_por_interlocutor,
    gerar_matriz_corroboracao_oral,
)


def _synthetic_multicrime_bundle() -> dict:
    """Caso sintético com laudos periciais, custódia separada de AIJ e 3 crimes imputados."""
    raw_docs = [
        {
            "doc_id": "ID-7100101",
            "titulo": "Denúncia Ministerial — Ministério Público Estadual (ID-7100101)",
            "data_peca": "2025-02-20",
            "folha": "fls. 1/4",
            "conteudo": (
                "MINISTÉRIO PÚBLICO DO ESTADO DO RIO GRANDE DO NORTE. "
                "Processo nº 0807111-11.2025.8.20.0001. "
                "Vítima: CAMILA ROCHA AZEVEDO. "
                "Denunciado: ROBERTO GOMES SILVEIRA. "
                "O Ministério Público VEM OFERECER DENÚNCIA contra ROBERTO GOMES SILVEIRA, "
                "incurso nas penas dos artigos 129, § 13, 150, § 1º e 147, caput, todos do Código Penal, "
                "em prejuízo de sua ex-companheira, CAMILA ROCHA AZEVEDO, "
                "por fato ocorrido em 10/02/2025 na residência da ofendida."
            ),
        },
        {
            "doc_id": "ID-7100102",
            "titulo": "Decisão de Recebimento da Denúncia (ID-7100102)",
            "data_peca": "2025-02-25",
            "folha": "fls. 5/6",
            "conteudo": (
                "RECEBIMENTO DE DENÚNCIA. DECISÃO. Recebo a denúncia em 25/02/2025 em desfavor de "
                "ROBERTO GOMES SILVEIRA quanto às imputações dos artigos 129, § 13, 150, § 1º e 147, caput, do Código Penal."
            ),
        },
        {
            "doc_id": "ID-7100103",
            "titulo": "Laudo de Exame de Corpo de Delito UN-9101-2025 (ID-7100103)",
            "data_peca": "2025-02-11",
            "folha": "fls. 12/14",
            "conteudo": (
                "LAUDO PERICIAL Nº UN-9101-2025. Exame de lesão corporal de fato ocorrido por volta das 14h30. "
                "Pericianda: CAMILA ROCHA AZEVEDO. "
                "Conclusão: há ofensa à integridade corporal compatível com instrumento contundente."
            ),
        },
        {
            "doc_id": "ID-7100104",
            "titulo": "Laudo Pericial Complementar UN-9102-2025 (ID-7100104)",
            "data_peca": "2025-02-12",
            "folha": "fls. 15/16",
            "conteudo": (
                "LAUDO PERICIAL Nº UN-9102-2025. Exame de lesão corporal complementar e constatação de danos no imóvel."
            ),
        },
        {
            "doc_id": "ID-7100105",
            "titulo": "Termo de Audiência de Custódia e Decisão de Liberdade Provisória (ID-7100105)",
            "data_peca": "2025-02-12",
            "folha": "fls. 18/20",
            "conteudo": (
                "TERMO DE AUDIÊNCIA DE CUSTÓDIA. Em 12/02/2025, homologo o flagrante de 10/02/2025 "
                "e concedo liberdade provisória com medidas protetivas em favor de CAMILA ROCHA AZEVEDO."
            ),
        },
        {
            "doc_id": "ID-7100106",
            "titulo": "Certidão de Antecedentes Criminais (ID-7100106)",
            "data_peca": "2025-03-01",
            "folha": "fls. 25",
            "conteudo": "CERTIDÃO DE ANTECEDENTES CRIMINAIS. Certifico que ROBERTO GOMES SILVEIRA é tecnicamente primário.",
        },
        {
            "doc_id": "ID-7100108",
            "titulo": "Termo de Audiência de Instrução e Julgamento (ID-7100108)",
            "data_peca": "2026-03-23",
            "folha": "fls. 40/42",
            "conteudo": (
                "TERMO DE AUDIÊNCIA DE INSTRUÇÃO E JULGAMENTO. Realizada em 23/03/2026, "
                "presentes a vítima CAMILA ROCHA AZEVEDO, o interrogatório do acusado ROBERTO GOMES SILVEIRA "
                "e as testemunhas policiais SGT LUCAS MENDES e CB RAFAEL COSTA."
            ),
        },
        {
            "doc_id": "ID-7100109",
            "titulo": "Alegações Finais por Memoriais da Defesa Técnica (ID-7100109)",
            "data_peca": "2026-03-28",
            "folha": "fls. 45/48",
            "conteudo": (
                "DEFENSORIA PÚBLICA DO ESTADO DO RIO GRANDE DO NORTE. "
                "ALEGAÇÕES FINAIS DA DEFESA. A Defensoria Pública vem apresentar suas alegações finais "
                "pugnando pela absolvição por insuficiência de provas (art. 386, VII, do CPP) e, "
                "subsidiariamente, a fixação da pena no mínimo legal."
            ),
        },
    ]
    transcriptions = [
        {
            "media_id": "ID-MIDIA-7101-001",
            "arquivo": "Audiencias-20260323-Depoimento da Vítima Camila_001.mp4",
            "depoente": "Camila Rocha Azevedo (Ofendida)",
            "papel": "OFENDIDA",
            "duracao": "03:20",
            "segmentos": [
                {"inicio": "00:05", "fim": "01:10", "texto": "Relatou que o ex-companheiro invadiu a casa, desferiu socos e proferiu ameaças de morte."}
            ],
            "sintese": "A ofendida confirmou a invasão de domicílio, as agressões físicas e as ameaças.",
        },
        {
            "media_id": "ID-MIDIA-7102-001",
            "arquivo": "Audiencias-20260323-Interrogatório do Acusado Roberto_001.mp4",
            "depoente": "Roberto Gomes Silveira (Acusado)",
            "papel": "INTERROGATORIO_REU",
            "duracao": "02:40",
            "segmentos": [
                {"inicio": "00:10", "fim": "01:00", "texto": "Negou as agressões e afirmou que apenas foi buscar pertences pessoais."}
            ],
            "sintese": "O interrogado negou as agressões físicas e as ameaças.",
        },
    ]
    return {
        "case_id": "0807111-11.2025.8.20.0001",
        "numero_processo": "0807111-11.2025.8.20.0001",
        "ofendida": "CAMILA ROCHA AZEVEDO",
        "reu": "ROBERTO GOMES SILVEIRA",
        "vinculo_domestico": "ex-companheiro (art. 5º, III, da Lei nº 11.340/2006)",
        "data_fato": "2025-02-10",
        "data_recebimento_denuncia": "2025-02-25",
        "raw_documents": raw_docs,
        "transcriptions": transcriptions,
    }


def test_subsegmentacao_laudos_aij_e_sentenca_limpa():
    bundle = _synthetic_multicrime_bundle()
    pecas = classificar_pecas_autos(bundle["raw_documents"])
    meta = extrair_metadados_processo(pecas)

    laudos = [p for p in pecas if p.categoria == "LAUDO_PERICIAL"]
    assert any("UN-9101-2025" in p.titulo or "UN-9101-2025" in p.conteudo for p in laudos)
    assert any("UN-9102-2025" in p.titulo or "UN-9102-2025" in p.conteudo for p in laudos)

    # Parênteses do vínculo doméstico balanceados
    assert meta.vinculo_domestico.count("(") == meta.vinculo_domestico.count(")")

    # Custódia (ID-7100105) não pode ser confundida com AIJ (ID-7100108)
    custodia_cert = next((p for p in pecas if p.doc_id == "ID-7100105"), None)
    assert custodia_cert is not None and custodia_cert.categoria != "TERMO_AUDIENCIA"
    aij = next((p for p in pecas if p.doc_id == "ID-7100108"), None)
    assert aij is not None and aij.categoria == "TERMO_AUDIENCIA"

    override = {
        "crimes_config": [
            {"codigo": "art_129_p13_cp", "veredicto": "condenar"},
            {"codigo": "art_150_p1_cp", "veredicto": "desclassificar", "crime_desclassificado": "art_150_cp"},
            {"codigo": "art_147_cp", "veredicto": "condenar"},
        ]
    }
    sent = gerar_sentenca_estruturada(bundle, custom_dosimetria_override=override, use_ollama_if_available=False)
    txt = sent.texto_completo

    # Sem rodapés PJe, sem ASR bruto de abertura, sem falsa reincidência e sem Dispositivo duplicado
    assert "Pág. Total" not in txt
    assert "Assinado eletronicamente" not in txt
    assert "este é o processo" not in txt.lower()
    assert "agravante da reincidência" not in txt.lower()
    assert "ID-CAPA-PJE" not in sent.secao_dosimetria
    assert txt.count("Ante o exposto") == 1
    assert txt.count("III – DISPOSITIVO") == 1

    # Script para Gemini inclui laudos sub-segmentados
    script = gerar_script_estruturado_caso(bundle, pseudonimizar=True, custom_dosimetria_override=override)
    assert "UN-9101-2025" in script.script_markdown
    assert "UN-9102-2025" in script.script_markdown
    assert "LAUDO_PERICIAL" in script.script_markdown


def test_consolidacao_midias_multiparte_em_discurso_indireto():
    raw_docs = [
        {
            "doc_id": "ID-7200101",
            "titulo": "Denúncia (ID-7200101)",
            "data_peca": "2025-01-20",
            "folha": "fls. 1/3",
            "conteudo": (
                "O Ministério Público oferece denúncia contra EDUARDO NUNES TEIXEIRA pela prática do crime "
                "do art. 147-A do Código Penal contra HELENA MARTINS PRADO."
            ),
        }
    ]
    pecas = classificar_pecas_autos(raw_docs)
    trs = [
        {
            "media_id": "ID-MIDIA-7201-001",
            "arquivo": "div_Audiencias-20260305_100511-Depoimento da Vítima Helena_001.mp4",
            "depoente": "Helena Martins Prado (Parte 01)",
            "papel": "OFENDIDA",
            "duracao": "02:30",
            "segmentos": [
                {"inicio": "00:00", "fim": "00:10", "texto": "Este é o processo em audiência."},
                {"inicio": "00:11", "fim": "01:20", "texto": "Declarou que o acusado passou a segui-la diariamente no trajeto do trabalho e enviar mensagens insistentes."},
            ],
            "sintese": "Relatou perseguição reiterada no trajeto do trabalho.",
        },
        {
            "media_id": "ID-MIDIA-7201-002",
            "arquivo": "div_Audiencias-20260305_100511-Depoimento da Vítima Helena_002.mp4",
            "depoente": "Helena Martins Prado (Parte 02)",
            "papel": "OFENDIDA",
            "duracao": "02:10",
            "segmentos": [
                {"inicio": "00:00", "fim": "01:15", "texto": "Acrescentou que precisou alterar sua rotina e solicitar medidas protetivas de urgência."}
            ],
            "sintese": "Acrescentou alteração de rotina e pedido de medidas protetivas.",
        },
    ]
    matriz = gerar_matriz_corroboracao_oral(pecas, trs)
    interlocutores = consolidar_prova_oral_por_interlocutor(matriz.depoentes, pecas=pecas)

    assert len(interlocutores) == 1
    assert len(interlocutores[0]["media_ids"]) == 2
    sintese = " ".join(interlocutores[0]["sintese_consolidada"])
    assert "(Parte 01)" not in sintese
    assert "este é o processo" not in sintese.lower()


def test_sectional_pipeline_offline_ollama_marca_fallback_acionado():
    demo = get_demo_case_bundle()
    engine = SectionalPipelineEngine(use_ollama=True)

    with patch("app.generation.sectional_pipeline.httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.return_value.post.side_effect = httpx.ConnectError("offline")
        sent = engine.executar_pipeline(demo, use_ollama=True)

    telemetria = engine.snapshot_telemetria()
    assert telemetria["modo_geracao"] == "FALLBACK_DETERMINISTICO"
    assert telemetria["totais"]["fallback_acionado"] == 5
    for sec in SecaoTipo:
        assert engine.estado.status_secoes[sec] == StatusSecao.FALLBACK_ACIONADO
    assert "Fallback Determinístico" in sent.motor_utilizado


def test_api_generate_sectional_propaga_secoes_habilitadas():
    with TestClient(app) as client:
        client.post("/api/cases/load-demo")
        resp = client.post(
            "/api/sentence/generate-sectional",
            json={
                "use_ollama": False,
                "usar_duplo_crivo": False,
                "secoes_habilitadas": ["relatorio", "dispositivo"],
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        tel = data["telemetria"]
        assert tel["secoes"]["relatorio"]["selada"] is True
        assert tel["secoes"]["dispositivo"]["selada"] is True
        assert tel["secoes"]["merito"]["status"] == "pendente"
        assert tel["secoes"]["merito"]["selada"] is False
        client.post("/api/cases/load-demo")


def test_gemini_import_reparo_cirurgico_preserva_secoes_validas():
    demo = get_demo_case_bundle()
    sent_ref = gerar_sentenca_estruturada(demo, use_ollama_if_available=False)

    # Injeta frase válida de estilo Gemini no RELATÓRIO e no MÉRITO, e falha intencional na DOSIMETRIA (sem art. 59)
    marcador_rel = "O Parquet ofereceu denúncia detalhada (ID-4100101) narrando a sequência fática apurada."
    marcador_mer = "As provas orais confirmam de modo coeso a agressão narrada pela vítima."
    rel_custom = sent_ref.secao_relatorio + f"\n\n{marcador_rel}"
    mer_custom = sent_ref.secao_merito + f"\n\n{marcador_mer}"
    dos_quebrada = "DOSIMETRIA DA PENA\n\nPena fixada diretamente sem análise dos vetores judiciais."

    texto_externo = "\n\n".join([
        sent_ref.secao_cabecalho,
        rel_custom,
        sent_ref.secao_preliminares,
        mer_custom,
        dos_quebrada,
        sent_ref.secao_dispositivo,
    ])

    sentenca_imp, auditoria_imp, _tel = importar_e_auditar_sentenca_externa(
        case_bundle=demo,
        texto_sentenca_externa=texto_externo,
        auto_blindar_auditoria=True,
    )
    assert auditoria_imp.aprovada_sem_alucinacao is True
    assert auditoria_imp.score_confiabilidade == 100.0
    # Seções válidas do Gemini foram preservadas!
    assert marcador_rel in sentenca_imp.secao_relatorio
    assert marcador_mer in sentenca_imp.secao_merito
    # Somente a dosimetria quebrada foi reparada com os 8 vetores do art. 59
    assert "CULPABILIDADE" in sentenca_imp.secao_dosimetria.upper()
    assert "sem análise dos vetores judiciais" not in sentenca_imp.secao_dosimetria


def test_load_real_case_from_disk_isolates_coexisting_cases_and_medias(tmp_path, monkeypatch):
    """
    Regressão: quando dois processos coexistem em data/cases/, o autoload de disco
    deve carregar exclusivamente as mídias vinculadas ao processo ativo (por data da AIJ
    e nomes das partes), sem contaminação cruzada nem nomes hardcoded.
    """
    import app.main as main_module
    from app.transcription.audio_transcriber import _infer_speaker_and_role

    nome_pdf_a = "0807222-22.2025.8.20.0001.pdf"
    nome_pdf_b = "0807111-11.2025.8.20.0001.pdf"

    docs_by_name = {
        nome_pdf_a: [
            {
                "doc_id": "ID-7200101",
                "titulo": "Denúncia (ID-7200101)",
                "data_peca": "2025-01-20",
                "folha": "fls. 1/3",
                "conteudo": (
                    "Processo nº 0807222-22.2025.8.20.0001. "
                    "O MINISTÉRIO PÚBLICO oferece DENÚNCIA contra EDUARDO NUNES TEIXEIRA "
                    "pelo crime do art. 147-A do CP contra a vítima HELENA MARTINS PRADO."
                ),
            },
            {
                "doc_id": "ID-7200105",
                "titulo": "Termo de Audiência de Instrução e Julgamento (ID-7200105)",
                "data_peca": "2026-03-05",
                "folha": "fls. 20/21",
                "conteudo": (
                    "TERMO DE AUDIÊNCIA DE INSTRUÇÃO E JULGAMENTO. Realizada em 05/03/2026, "
                    "presente a ofendida HELENA MARTINS PRADO e o acusado EDUARDO NUNES TEIXEIRA."
                ),
            },
        ],
        nome_pdf_b: _synthetic_multicrime_bundle()["raw_documents"],
    }

    pdf_a = tmp_path / nome_pdf_a
    pdf_b = tmp_path / nome_pdf_b
    pdf_a.write_bytes(b"%PDF-1.4\n")
    pdf_b.write_bytes(b"%PDF-1.4\n")

    nomes_midias = [
        "div_Audiências do 3JVD-20260305_100511-Depoimento da Vítima Helena_001.mp4",
        "div_Audiências do 3JVD-20260305_100511-Depoimento da Vítima Helena_002.mp4",
        "Audiências do 3JVD-20260323_110634-Depoimento da Testemunha PM Lucas.mp4",
        "Audiências do 3JVD-20260323_111349-Depoimento da Testemunha PM Rafael.mp4",
        "Audiências do 3JVD-20260323_113321-Alegações Finais do MP.mp4",
        "div_Audiências do 3JVD-20260323_105000-Depoimento da Vítima Camila_001.mp4",
        "div_Audiências do 3JVD-20260323_105000-Depoimento da Vítima Camila_002.mp4",
        "div_Audiências do 3JVD-20260323_105000-Depoimento da Vítima Camila_003.mp4",
        "div_Audiências do 3JVD-20260323_111830-Interrogatório do Acusado Roberto_001.mp4",
        "div_Audiências do 3JVD-20260323_111830-Interrogatório do Acusado Roberto_002.mp4",
        "div_Audiências do 3JVD-20260323_111830-Interrogatório do Acusado Roberto_003.mp4",
    ]
    for nome in nomes_midias:
        (tmp_path / nome).write_bytes(b"\x00" * 64)

    def _fake_segmentar(pdf_path, remover_em_falha=True):
        return docs_by_name[pdf_path.name]

    def _fake_transcrever(m_path, depoente="OFENDIDA / TESTEMUNHA", papel="OFENDIDA", known_ofendida="", known_reu=""):
        media_id, nome_dep, papel_inf = _infer_speaker_and_role(
            m_path.name, depoente, papel, known_ofendida=known_ofendida, known_reu=known_reu
        )
        return {
            "media_id": media_id,
            "arquivo": m_path.name,
            "depoente": nome_dep,
            "papel": papel_inf,
            "duracao": "01:00",
            "segmentos": [{"inicio": "00:00", "fim": "00:10", "texto": "Registro de audiência."}],
            "sintese": "Registro de audiência.",
        }

    monkeypatch.setattr(main_module, "CASES_DIR", tmp_path)
    monkeypatch.setattr(main_module, "_segmentar_pdf_seguro", _fake_segmentar)
    monkeypatch.setattr(main_module, "transcrever_midia_audiencia", _fake_transcrever)

    # 1. Caso ativo = 0807222-22 (Helena, AIJ 05/03/2026) mais recente
    os.utime(pdf_b, (1_700_000_100, 1_700_000_100))
    os.utime(pdf_a, (1_700_000_200, 1_700_000_200))
    bundle_a = main_module._load_real_case_from_disk_if_present()
    assert bundle_a is not None
    assert bundle_a["numero_processo"] == "0807222-22.2025.8.20.0001"
    arqs_a = [t["arquivo"] for t in bundle_a["transcriptions"]]
    assert len(arqs_a) == 2, f"Esperadas 2 mídias de Helena, obtidas {len(arqs_a)}: {arqs_a}"
    assert all("Helena" in a and "20260305" in a for a in arqs_a)
    assert not any(
        any(x in a for x in ("Camila", "Roberto", "Lucas", "Rafael", "20260323"))
        for a in arqs_a
    )

    # 2. Caso ativo = 0807111-11 (Camila / Roberto, AIJ 23/03/2026) mais recente
    os.utime(pdf_a, (1_700_000_100, 1_700_000_100))
    os.utime(pdf_b, (1_700_000_300, 1_700_000_300))
    bundle_b = main_module._load_real_case_from_disk_if_present()
    assert bundle_b is not None
    assert bundle_b["numero_processo"] == "0807111-11.2025.8.20.0001"
    arqs_b = [t["arquivo"] for t in bundle_b["transcriptions"]]
    assert len(arqs_b) == 9, f"Esperadas 9 mídias de 0807111-11, obtidas {len(arqs_b)}: {arqs_b}"
    assert all("20260323" in a for a in arqs_b)
    assert not any("Helena" in a or "20260305" in a for a in arqs_b)


def test_filter_media_files_for_case_dynamic_edge_cases(tmp_path):
    """Testa filtragem dinâmica por data da AIJ e nomes (sem hardcode) em processo sintético."""
    import app.main as main_module
    from app.ingestion.laya_classifier import PecaClassificada

    pecas = [
        PecaClassificada(
            doc_id="ID-9001",
            folha="fls. 10/12",
            titulo="Termo de Audiência de Instrução e Julgamento (ID-9001)",
            data_peca="2026-04-10",
            fase="INSTRUCAO",
            categoria="TERMO_AUDIENCIA",
            laya_confidence=0.95,
            resumo_estruturado="AIJ realizada em 10/04/2026.",
            conteudo=(
                "TERMO DE AUDIÊNCIA. Em 10/04/2026 às 14:00, presente a vítima MARIA CLARA DE OLIVEIRA, "
                "o acusado CARLOS ALBERTO DA SILVA e a testemunha SGT PEDRO HENRIQUE."
            ),
        )
    ]
    candidatos = [
        tmp_path / "Audiencias-20260410_140500-Depoimento da Vítima Maria Clara_001.mp4",
        tmp_path / "Audiencias-20260410_142000-Interrogatório do Acusado Carlos.mp4",
        tmp_path / "Audiencias-20260410_143500-Depoimento da Testemunha PM Pedro.mp4",
        tmp_path / "Audiencias-20260410_145000-Alegações Finais do MP.mp4",
        tmp_path / "audiencia_generica.mp4",
        # Contaminantes:
        tmp_path / "Audiencias-20260512_090000-Alegações Finais do MP.mp4",
        tmp_path / "Audiencias-20260410_100000-Depoimento da Vítima Joana.mp4",
        tmp_path / "Audiencias-20260410_103000-Interrogatório do Acusado Roberto.mp4",
        tmp_path / "Audiencias-20260410_110000-Depoimento da Testemunha PM Marcos.mp4",
    ]
    for p in candidatos:
        p.write_bytes(b"\x00")

    filtradas = [
        p.name
        for p in main_module._filter_media_files_for_case(
            media_files=candidatos,
            pecas=pecas,
            known_ofendida="MARIA CLARA DE OLIVEIRA",
            known_reu="CARLOS ALBERTO DA SILVA",
        )
    ]
    assert filtradas == [
        "Audiencias-20260410_140500-Depoimento da Vítima Maria Clara_001.mp4",
        "Audiencias-20260410_142000-Interrogatório do Acusado Carlos.mp4",
        "Audiencias-20260410_143500-Depoimento da Testemunha PM Pedro.mp4",
        "Audiencias-20260410_145000-Alegações Finais do MP.mp4",
        "audiencia_generica.mp4",
    ]


def test_filter_media_rejects_custodia_date_and_first_name_collisions(tmp_path):
    """
    Regressão:
    1) Termo de Audiência de Custódia (DECISAO_MPU) não pode poluir _extract_aij_dates_from_pecas;
    2) Homônimos de primeiro nome (ex.: Maria Joana vs Maria Clara, Carlos Eduardo vs Carlos Alberto)
       não podem vazar quando outro token do nome diverge;
    3) 'Interrogatório de <Nome>' e sufixos como '(Complemento)'/'(Sala 02)' funcionam dinamicamente.
    """
    import app.main as main_module
    from app.ingestion.laya_classifier import PecaClassificada

    pecas = [
        PecaClassificada(
            doc_id="ID-1001",
            folha="fls. 10/12",
            titulo="Termo de Audiência de Custódia e Decisão de Medidas Protetivas (ID-1001)",
            data_peca="2024-02-17",
            fase="INQUERITO",
            categoria="DECISAO_MPU",
            laya_confidence=0.95,
            resumo_estruturado="Custódia.",
            conteudo="Audiência de custódia realizada em 17/02/2024 às 11:00.",
        ),
        PecaClassificada(
            doc_id="ID-2001",
            folha="fls. 50/52",
            titulo="Termo de Audiência de Instrução e Julgamento (ID-2001)",
            data_peca="2026-04-10",
            fase="INSTRUCAO",
            categoria="TERMO_AUDIENCIA",
            laya_confidence=0.98,
            resumo_estruturado="AIJ realizada em 10/04/2026.",
            conteudo=(
                "TERMO DE AUDIÊNCIA. Em 10/04/2026 às 14:00 na sala de audiências, "
                "fatos ocorridos em 16/02/2024 às 22:00. "
                "Vítima: MARIA CLARA DE OLIVEIRA. Acusado: CARLOS ALBERTO DA SILVA. "
                "Testemunha: SGT PEDRO HENRIQUE."
            ),
        ),
    ]

    aij_dates = main_module._extract_aij_dates_from_pecas(pecas)
    assert aij_dates == {"20260410"}, f"Datas indevidas em aij_dates: {aij_dates}"

    candidatos = [
        tmp_path / "Audiencias-20260410-Depoimento da Vítima Maria Clara (Complemento).mp4",
        tmp_path / "Audiencias-20260410-Interrogatório de Carlos Alberto.mp4",
        # Contaminantes na mesma data / primeiro nome coincidente / data de custódia:
        tmp_path / "Audiencias-20260410-Depoimento da Vítima Maria Joana.mp4",
        tmp_path / "Audiencias-20260410-Interrogatório de Carlos Eduardo.mp4",
        tmp_path / "Audiencias-20260410-Depoimento da Testemunha Pedro Souza.mp4",
        tmp_path / "Audiencias-20240217-Alegações Finais do MP.mp4",
    ]
    for p in candidatos:
        p.write_bytes(b"\x00")

    filtradas = [
        p.name
        for p in main_module._filter_media_files_for_case(
            media_files=candidatos,
            pecas=pecas,
            known_ofendida="MARIA CLARA DE OLIVEIRA",
            known_reu="CARLOS ALBERTO DA SILVA",
        )
    ]
    assert filtradas == [
        "Audiencias-20260410-Depoimento da Vítima Maria Clara (Complemento).mp4",
        "Audiencias-20260410-Interrogatório de Carlos Alberto.mp4",
    ]
