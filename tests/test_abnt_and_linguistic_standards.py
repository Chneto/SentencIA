import datetime
import re
from pathlib import Path
import docx
from docx.shared import Cm, Pt
import pymupdf
import pytest

from app.generation.date_formatter import (
    converter_mencoes_textuais_para_extenso,
    formatar_data_br,
    formatar_moeda_br,
    formatar_moeda_com_extenso,
    mascarar_e_padronizar_datas_texto,
    parse_qualquer_data,
    valor_por_extenso,
)
from app.demo.demo_case import get_demo_case_bundle
from app.ingestion.laya_classifier import classificar_pecas_autos, extrair_metadados_processo
from app.knowledge.knowledge_base import DOUTRINA_VERIFICADA, SUMULAS_E_TEMAS_VERIFICADOS
from app.generation.sentence_generator import gerar_sentenca_estruturada
from app.generation.hallucination_auditor import auditar_sentenca
from app.export.document_exporter import exportar_docx, exportar_html_pje, exportar_pdf


def test_date_formatter_padrao_brasileiro():
    """Verifica formatação estrita de datas no padrão brasileiro (DD/MM/AAAA e extenso formal judiciário)."""
    # 1. ISO para DD/MM/AAAA técnico
    assert formatar_data_br("2025-02-14") == "14/02/2025"
    assert formatar_data_br("2024-06-07") == "07/06/2024"
    assert formatar_data_br("2026-11-11") == "11/11/2026"

    # 2. Extenso formal forense para menções narrativas textuais
    assert formatar_data_br("2025-02-14", por_extenso=True) == "14 de fevereiro de 2025"
    assert formatar_data_br("2024-03-01", por_extenso=True) == "1º de março de 2024"
    assert formatar_data_br(datetime.date(2026, 11, 11), por_extenso=True) == "11 de novembro de 2026"

    # 3. Preservação de padrão BR já formatado
    assert formatar_data_br("14/02/2025") == "14/02/2025"
    assert formatar_data_br("14/02/2025", por_extenso=True) == "14 de fevereiro de 2025"

    # 4. Parse de datas em múltiplos formatos comumente encontrados nos autos
    assert parse_qualquer_data("2025-10-10") == datetime.date(2025, 10, 10)
    assert parse_qualquer_data("05/03/2026") == datetime.date(2026, 3, 5)
    assert parse_qualquer_data("14 de fevereiro de 2025") == datetime.date(2025, 2, 14)
    assert parse_qualquer_data("1º de março de 2024") == datetime.date(2024, 3, 1)
    assert parse_qualquer_data("1 de janeiro de 2026") == datetime.date(2026, 1, 1)
    assert parse_qualquer_data("14.02.2025") == datetime.date(2025, 2, 14)
    assert parse_qualquer_data("14-02-2025") == datetime.date(2025, 2, 14)
    assert parse_qualquer_data("2025/02/14") == datetime.date(2025, 2, 14)
    assert parse_qualquer_data(None) is None


def test_mascarar_e_padronizar_datas_no_texto():
    """Verifica que o conversor varre o texto e substitui datas residuais ISO pelo padrão oficial DD/MM/AAAA."""
    texto_com_iso = (
        "O fato ocorreu em 2025-02-14 e a denúncia foi recebida em 2025-02-28. "
        "A certidão policial data de 14-02-2025 nos autos 0804590-12.2025.8.00.0001 sob a Lei 11.340/2006."
    )
    limpo = mascarar_e_padronizar_datas_texto(texto_com_iso)
    assert "2025-02-14" not in limpo
    assert "2025-02-28" not in limpo
    assert "14-02-2025" not in limpo
    assert "14/02/2025" in limpo
    assert "28/02/2025" in limpo
    # O número do processo e número da lei devem ser preservados
    assert "0804590-12.2025.8.00.0001" in limpo
    assert "11.340/2006" in limpo


def test_converter_mencoes_textuais_para_extenso():
    """Garante que menções narrativas textuais após preposições forenses são grafadas por extenso."""
    texto_narrativo = (
        "Narra a exordial que, no dia 14/02/2025, o réu foi intimado em 22/01/2025. "
        "A denúncia foi recebida em 28/02/2025 e a audiência realizada em 18/06/2025. "
        "Porém, no documento ID-4100101 (fls. 02/05, 14/02/2025), o formato técnico permanece."
    )
    formatado = converter_mencoes_textuais_para_extenso(texto_narrativo)
    assert "no dia 14 de fevereiro de 2025" in formatado
    assert "intimado em 22 de janeiro de 2025" in formatado
    assert "recebida em 28 de fevereiro de 2025" in formatado
    assert "realizada em 18 de junho de 2025" in formatado
    # A referência técnica entre parênteses permanece intacta
    assert "14/02/2025)" in formatado


def test_formatacao_moeda_padrao_brasileiro_e_extenso():
    """Verifica formatação monetária oficial brasileira (R$ X.XXX,XX) e representação por extenso."""
    assert formatar_moeda_br(3000.0) == "R$ 3.000,00"
    assert formatar_moeda_br(5000.5) == "R$ 5.000,50"
    assert formatar_moeda_br(12500.75) == "R$ 12.500,75"

    assert valor_por_extenso(0.0) == "zero real"
    assert valor_por_extenso(1.0) == "um real"
    assert valor_por_extenso(3000.0) == "três mil reais"
    assert valor_por_extenso(5000.0) == "cinco mil reais"
    assert valor_por_extenso(10000.0) == "dez mil reais"
    assert valor_por_extenso(12500.75) == "doze mil e quinhentos reais e setenta e cinco centavos"
    assert valor_por_extenso(1000000.0) == "um milhão de reais"

    assert formatar_moeda_com_extenso(3000.0) == "R$ 3.000,00 (três mil reais)"
    assert formatar_moeda_com_extenso(5000.0) == "R$ 5.000,00 (cinco mil reais)"


def test_sentenca_gerada_sem_vazamento_de_datas_iso_e_mencoes_extenso():
    """Garante que a sentença penal gerada não contém datas ISO e apresenta datas por extenso na narrativa."""
    demo = get_demo_case_bundle()
    sentenca = gerar_sentenca_estruturada(case_bundle=demo, use_ollama_if_available=False)

    # 1. Ausência total de datas ISO
    datas_iso = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", sentenca.texto_completo)
    assert datas_iso == [], f"Datas ISO detectadas: {datas_iso}"

    # 2. Data de prolação no padrão técnico DD/MM/AAAA
    assert re.match(r"^\d{2}/\d{2}/\d{4}$", sentenca.data_prolatacao)

    # 3. Menções narrativas em português culto por extenso
    assert "14 de fevereiro de 2025" in sentenca.secao_relatorio
    assert "22 de janeiro de 2025" in sentenca.secao_relatorio
    assert "28 de fevereiro de 2025" in sentenca.secao_relatorio
    assert "18 de junho de 2025" in sentenca.secao_relatorio

    # 4. Fechamento formal com datação por extenso
    assert re.search(r"\d{1,2} de [a-zç]+ de \d{4}", sentenca.secao_dispositivo)


def test_dinamismo_quantum_dano_moral_com_extenso_no_dispositivo():
    """Garante que o valor arbitrado de danos morais é dinâmico e acompanhado do extenso correto."""
    demo = get_demo_case_bundle()

    # Caso 1: R$ 3.000,00
    s_3000 = gerar_sentenca_estruturada(demo, custom_dosimetria_override={"valor_dano_moral": 3000.0}, use_ollama_if_available=False)
    assert "R$ 3.000,00 (três mil reais)" in s_3000.secao_dispositivo

    # Caso 2: R$ 5.000,00 (não pode ter resíduo estático de três mil reais)
    s_5000 = gerar_sentenca_estruturada(demo, custom_dosimetria_override={"valor_dano_moral": 5000.0}, use_ollama_if_available=False)
    assert "R$ 5.000,00 (cinco mil reais)" in s_5000.secao_dispositivo
    assert "três mil reais" not in s_5000.secao_dispositivo


def test_abnt_nbr_10520_citacoes_diretas_longas_e_curtas():
    """
    Verifica conformidade estrita com a ABNT NBR 10520:
    - Citações diretas curtas (até 3 linhas, como súmulas e falas): incorporadas entre aspas duplas no parágrafo.
    - Citações diretas longas (> 3 linhas, como doutrinas): destacadas com recuo de 4 espaços/4 cm e SEM ASPAS EXTERNAS.
    - Citações indiretas (paráfrases): redação impessoal forense em 3ª pessoa do singular.
    """
    demo = get_demo_case_bundle()
    sentenca = gerar_sentenca_estruturada(case_bundle=demo, use_ollama_if_available=False)
    texto = sentenca.texto_completo

    # 1. Citações curtas devem conter aspas duplas (e não aspas simples)
    assert 'Súmula 600/STJ: "' in texto
    assert 'Súmula 589/STJ' in texto and f'"{SUMULAS_E_TEMAS_VERIFICADOS[3]["texto_literal"]}"' in texto
    assert 'Tema Repetitivo 983/STJ' in texto and f'"{SUMULAS_E_TEMAS_VERIFICADOS[8]["texto_literal"]}"' in texto
    assert '"acabar com a sua vida"' in texto

    # 2. Citações diretas longas (precedentes literais verificados) iniciam com recuo de 4 espaços e NÃO possuem aspas externas
    # Doutrinas entram como síntese em parágrafo corrido (sem aspas — TASK-024)
    blocos = texto.split("\n\n")
    citacoes_longas = [b for b in blocos if b.startswith("    ")]
    assert len(citacoes_longas) >= 1, f"Deve haver ao menos 1 citação direta longa verificada: {len(citacoes_longas)}"

    for c in citacoes_longas:
        conteudo = c.strip()
        assert not conteudo.startswith(('"', "'", "“", "‘")), f"Citação longa violou ABNT ao iniciar com aspas: {conteudo[:40]}"
        assert not conteudo.endswith(('"', "'", "”", "’")), f"Citação longa violou ABNT ao terminar com aspas: {conteudo[-40:]}"
        assert len(conteudo) > 100, "Citação longa deve possuir corpo substancial de texto"


def test_doutrina_verificada_referencias_abnt_nbr_6023():
    """Garante que todas as doutrinas da Base Fechada contêm referências completas segundo a ABNT NBR 6023."""
    for d in DOUTRINA_VERIFICADA:
        assert "referencia_abnt" in d, f"Doutrina {d['id']} sem campo referencia_abnt"
        ref = d["referencia_abnt"]
        # Verifica elementos essenciais da NBR 6023: Autor em versalete/caixa alta, título, edição/cidade e editora
        assert re.match(r"^[A-ZÀ-Ú]+,\s+[A-Za-zÀ-ú\s]+", ref), f"Autor fora do padrão ABNT na referência: {ref}"
        assert any(ed in ref.lower() for ed in ("ed.", "editora", "juspodivm", "atlas", "saraiva", "forense"))


def test_abnt_estilos_exportacao_docx_html_e_pdf(tmp_path: Path):
    """
    Verifica que o exportador Word (.docx), HTML PJe e PDF aplicam as regras ABNT:
    - Margens: Sup/Esq 3 cm; Inf/Dir 2 cm (NBR 14724).
    - Recuo de primeira linha do corpo: 1,25 cm; entrelinhas 1,5.
    - Citação longa: recuo esquerdo 4,0 cm; sem recuo de 1ª linha; entrelinhas simples; fonte 10pt.
    - Fórmula de encerramento, linha de data e assinatura sem recuo de primeira linha de 1,25 cm.
    - Margens do PDF exatas sem dupla expansão.
    """
    demo = get_demo_case_bundle()
    sentenca = gerar_sentenca_estruturada(case_bundle=demo, use_ollama_if_available=False)

    # 1. Exportação Word (.docx)
    docx_file = exportar_docx(sentenca, tmp_path / "sentenca_abnt.docx")
    assert docx_file.exists()

    doc = docx.Document(str(docx_file))
    sec = doc.sections[0]
    assert round(sec.top_margin.cm, 1) == 3.0
    assert round(sec.left_margin.cm, 1) == 3.0
    assert round(sec.bottom_margin.cm, 1) == 2.0
    assert round(sec.right_margin.cm, 1) == 2.0

    corpo_encontrado = False
    citacao_encontrada = False
    closing_sem_recuo = False

    for p in doc.paragraphs:
        # Parágrafo normal de corpo do texto
        if p.paragraph_format.first_line_indent and round(p.paragraph_format.first_line_indent.cm, 2) == 1.25:
            corpo_encontrado = True
            assert p.paragraph_format.line_spacing == 1.5
        # Parágrafo de citação longa recuada
        if p.paragraph_format.left_indent and round(p.paragraph_format.left_indent.cm, 1) == 4.0:
            citacao_encontrada = True
            assert p.paragraph_format.line_spacing == 1.0
            assert p.paragraph_format.first_line_indent == Cm(0)
            for r in p.runs:
                if r.font.size:
                    assert r.font.size == Pt(10)
        # Parágrafo de encerramento ou assinatura
        if "Publique-se" in p.text or "Juiz(a) de Direito" in p.text:
            closing_sem_recuo = True
            assert p.paragraph_format.first_line_indent == Cm(0)

    assert corpo_encontrado, "Deve haver parágrafos de corpo com recuo de 1,25 cm e entrelinhas 1,5"
    assert citacao_encontrada, "Deve haver parágrafos de citação longa com recuo de 4,0 cm e fonte 10pt"
    assert closing_sem_recuo, "Fórmula de encerramento e assinatura não devem conter recuo de 1,25 cm"

    # 2. Exportação HTML PJe
    html_pje = exportar_html_pje(sentenca)
    assert "text-indent: 1.25cm" in html_pje
    assert "margin-left: 4cm" in html_pje
    assert "font-size: 10pt" in html_pje
    assert "line-height: 1.5" in html_pje
    assert "line-height: 1.0" in html_pje

    # 3. Exportação PDF com margens ABNT exatas
    pdf_file = exportar_pdf(sentenca, tmp_path / "sentenca_abnt.pdf")
    assert pdf_file.exists()
    pdf_doc = pymupdf.open(str(pdf_file))
    first_page = pdf_doc[0]
    blocks = first_page.get_text("blocks")
    # Primeiro bloco (epígrafe) desenhado dentro do retângulo com recuo esquerdo exato de ~85pt (3cm)
    assert len(blocks) > 0
    x0_min = min(b[0] for b in blocks if len(b[4].strip()) > 5)
    assert round(x0_min, 0) >= 85.0, f"Margem esquerda do PDF deve ser no mínimo 85pt (3cm): atual {x0_min}"


def test_auditor_camada_4_reprova_violacao_datas_e_abnt():
    """Garante que a Camada 4 do Auditor detecta e reprova datas fora do padrão BR, solecismos ou citações longas com aspas."""
    demo = get_demo_case_bundle()
    pecas = classificar_pecas_autos(demo["raw_documents"])
    meta = extrair_metadados_processo(pecas)
    sentenca = gerar_sentenca_estruturada(case_bundle=demo, use_ollama_if_available=False)

    # Sentença oficial deve ser 100% aprovada
    auditoria_ok = auditar_sentenca(sentenca.texto_completo, pecas, meta.teses_defesa)
    assert auditoria_ok.aprovada_sem_alucinacao is True
    assert auditoria_ok.conformidade_datas_padrao_br is True
    assert auditoria_ok.conformidade_abnt_citacoes is True
    assert auditoria_ok.conformidade_estilo_cnj_amb is True
    assert auditoria_ok.score_confiabilidade == 100.0

    # 1. Violação de data ISO: se injetarmos '2025-02-14'
    texto_data_errada = sentenca.texto_completo + "\nDecisão liminar concedida na data de 2025-02-14."
    auditoria_data = auditar_sentenca(texto_data_errada, pecas, meta.teses_defesa)
    assert auditoria_data.conformidade_datas_padrao_br is False
    assert auditoria_data.aprovada_sem_alucinacao is False

    # 2. Violação de data com traço: se injetarmos '14-02-2025'
    texto_data_traco = sentenca.texto_completo + "\nCertidão lavrada em 14-02-2025."
    auditoria_traco = auditar_sentenca(texto_data_traco, pecas, meta.teses_defesa)
    assert auditoria_traco.conformidade_datas_padrao_br is False

    # 3. Violação ABNT NBR 10520: se injetarmos bloco recuado com aspas externas
    texto_abnt_errada = sentenca.texto_completo + '\n\n    "Citação longa com recuo indevidamente entre aspas duplas violando NBR 10520."'
    auditoria_abnt = auditar_sentenca(texto_abnt_errada, pecas, meta.teses_defesa)
    assert auditoria_abnt.conformidade_abnt_citacoes is False
    assert auditoria_abnt.aprovada_sem_alucinacao is False

    # 4. Violação das Diretrizes da Magistratura (CNJ/AMB): tese inconstitucional de legítima defesa da honra acolhida
    texto_ldh_errada = sentenca.texto_completo + "\nAcolhe-se a legítima defesa da honra alegada pelo denunciado."
    auditoria_ldh = auditar_sentenca(texto_ldh_errada, pecas, meta.teses_defesa)
    assert auditoria_ldh.conformidade_estilo_cnj_amb is False
    assert auditoria_ldh.aprovada_sem_alucinacao is False

    # 5. Solecismo gramatical clássico: 'ao meu ver'
    texto_solecismo = sentenca.texto_completo + "\nAo meu ver, restou demonstrada a tipicidade."
    auditoria_sol = auditar_sentenca(texto_solecismo, pecas, meta.teses_defesa)
    assert auditoria_sol.conformidade_estilo_cnj_amb is False


def test_edge_cases_dates_and_currencies():
    """Testa casos de borda: ano bissexto, primeiro dia do mês, virada de ano, valores zerados e entradas inválidas."""
    # Ano bissexto
    assert formatar_data_br("2024-02-29") == "29/02/2024"
    assert formatar_data_br("2024-02-29", por_extenso=True) == "29 de fevereiro de 2024"

    # Primeiro dia do mês (numeral ordinal formal '1º')
    assert formatar_data_br("2025-01-01", por_extenso=True) == "1º de janeiro de 2025"
    assert formatar_data_br("2025-05-01", por_extenso=True) == "1º de maio de 2025"

    # Virada de ano
    assert formatar_data_br("2025-12-31") == "31/12/2025"
    assert formatar_data_br("2025-12-31", por_extenso=True) == "31 de dezembro de 2025"

    # Entradas inválidas ou vazias não quebram
    assert formatar_data_br("") == ""
    assert formatar_data_br(None) == ""
    assert formatar_data_br("data-invalida") == "data-invalida"

    # Moeda zerada ou centavos
    assert formatar_moeda_br(0.0) == "R$ 0,00"
    assert formatar_moeda_br(0.5) == "R$ 0,50"
    assert formatar_moeda_br(1000000.0) == "R$ 1.000.000,00"


@pytest.mark.dados_reais
def test_real_case_sentence_abnt_and_dates_conformity():
    """Garante que nos autos externos fornecidos via ``JVDFM_REAL_CASES_DIR`` as datas são brasileiras e as citações seguem a ABNT.

    Depende dos autos reais sigilosos: só roda com ``JVDFM_REAL_CASES_DIR`` (ver ``tests/conftest.py``).
    """
    from fastapi.testclient import TestClient
    from app.api.security import token_headers
    from app.main import app
    from tests.conftest import localizar_autos_reais

    client = TestClient(app, headers=token_headers())
    pasta_autos = localizar_autos_reais()
    pdfs = sorted(pasta_autos.glob("*.pdf"))
    mp4s = sorted(pasta_autos.glob("*.mp4"))
    if not pdfs or len(mp4s) < 2:
        pytest.skip("autos reais (PDF e ao menos 2 MP4) ausentes em JVDFM_REAL_CASES_DIR")
    pdf_path = pdfs[0]
    mp4_1, mp4_2 = mp4s[0], mp4s[1]

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

    # v2.0.0: o juiz decide — a geração de caso real exige veredicto explícito por crime imputado.
    crimes = r_up.json()["metadados"]["crimes_imputados"]
    assert crimes, "a extração deveria identificar ao menos um crime imputado nos autos reais"
    crimes_config = [{"codigo": c, "veredicto": "condenar"} for c in crimes]
    r_sent = client.post(
        "/api/sentence/generate", json={"use_ollama": False, "crimes_config": crimes_config}
    )
    assert r_sent.status_code == 200, r_sent.text
    sent_data = r_sent.json()
    texto_real = sent_data["sentenca"]["texto_completo"]

    # 1. Nenhuma data ISO residual
    datas_iso = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", texto_real)
    assert datas_iso == [], f"Datas ISO detectadas no processo real: {datas_iso}"

    # 2. Citações longas sem aspas externas
    blocos = texto_real.split("\n\n")
    citacoes_longas = [b for b in blocos if b.startswith("    ")]
    for c in citacoes_longas:
        assert not c.strip().startswith(('"', "'", "“", "‘"))
        assert not c.strip().endswith(('"', "'", "”", "’"))

    # 3. Auditoria (critério v2.0.0): forma 100, sem alucinação, datas BR e ABNT; só os marcadores
    #    [COMPLETAR PELO JUÍZO: ...] — lacunas que apenas o juízo preenche — podem impedir a aprovação.
    auditoria = sent_data["auditoria"]
    marcadores = sent_data.get("marcadores_pendentes") or []
    assert auditoria["score_forma"] == 100.0, auditoria["detalhes_camadas"]
    assert auditoria["ids_inexistentes"] == []
    assert auditoria["citacoes_nao_autorizadas"] == []
    assert auditoria["penas_divergentes"] == []
    assert auditoria["aprovada_sem_alucinacao"] is (not marcadores)
    assert auditoria["conformidade_datas_padrao_br"] is True
    assert auditoria["conformidade_abnt_citacoes"] is True

    # Restaura o demo case
    client.post("/api/cases/load-demo")
