# -*- coding: utf-8 -*-
"""Tier 4: Authentic Real-World Judicial Scenarios Test Suite.

End-to-end execution of the 5 authentic judicial cases from the Juizado de Violência
Doméstica e Familiar contra a Mulher (JVDFM) of Natal/RN:
- Case 1: 0801245-78.2025.8.20.5001 (Ameaça + Lesão Corporal, Decadência e Legítima Defesa)
- Case 2: 0803456-12.2025.8.20.5001 (Descumprimento de MPU via WhatsApp + Súmula 231)
- Case 3: 0804567-89.2024.8.20.5001 (Lesão Corporal, Laudo Pericial, Palavra da Vítima sob CNJ 492)
- Case 4: 0805678-90.2025.8.20.5001 (Multirreincidente, Súmula 444 STJ, Concurso Material Heterogêneo)
- Case 5: 0806789-01.2025.8.20.5001 (Auto-Correção Cirúrgica, Contingência Determinística e Blindagem Final)

Quality Gates Enforced on Final Sentences (v2.0.0):
- The judge's verdict per crime is mandatory for real cases (no default conviction)
- Form score == 100.0; overall score == 100.0 and `aprovada_sem_alucinacao is True` exactly when
  no `[COMPLETAR PELO JUÍZO: ...]` gap remains (gaps only the court can fill are never invented)
- Penalties in the DISPOSITIVO match the computed dosimetry (`penas_divergentes == []`)
- Zero folio hallucination (`ids_inexistentes == []`)
- Zero unauthorized jurisprudence (`citacoes_nao_autorizadas == []`)
- Complete defense confrontation (`teses_omitidas == []`)
- Full statutory compliance: Art. 381 CPP, Art. 21 LMP, LC 95/98, ABNT NBR 10520
- Simultaneous triple export generation: DOCX (>5KB), HTML PJe (>1KB), PDF (>5KB)
"""

import pytest

from app.core.errors import DecisaoJudicialPendenteError
from app.export.document_exporter import (
    exportar_docx,
    exportar_html_pje,
    exportar_pdf,
)
from app.generation.sectional_pipeline import (
    SectionalPipelineEngine,
)
from app.generation.sentence_generator import (
    gerar_sentenca_estruturada,
)
from app.ingestion.laya_classifier import (
    classificar_pecas_autos,
    extrair_metadados_processo,
)
from tests.e2e.conftest import (
    assert_all_teses_answered,
    assert_auditoria_honesta,
    auditar_com_dosimetria,
    gerar_sentenca_caso_real,
    veredictos_condenatorios,
)


# ==============================================================================
# Scenario 1: Ameaça e Lesão Corporal em Concurso Material
# Processo nº 0801245-78.2025.8.20.5001
# ==============================================================================
class TestScenario1AmeacaLesaoDecadenciaLegitimaDefesa:
  """Case 1: Multiple crimes (Art. 129 § 13 and Art. 147 CP) in material concurso."""

  def test_case_1_full_pipeline_audit_and_export(
      self, load_fixture_case, tmp_path
  ):
    """Executes Case 1 end-to-end, asserting 100.0 score, full thesis rejection, and triple export."""
    case_data = load_fixture_case("1")
    pecas = classificar_pecas_autos(case_data["raw_documents"])
    meta = extrair_metadados_processo(pecas)
    ids_midias = [t["media_id"] for t in case_data.get("transcriptions", [])]

    # 1. Generate complete judicial sentence
    sent = gerar_sentenca_caso_real(case_data)
    assert sent is not None
    assert len(sent.texto_completo) > 2000
    assert "0801245-78.2025.8.20.5001" in sent.numero_processo

    # 2. Strict 4-layer audit verification
    audit = auditar_com_dosimetria(sent, case_data)
    assert_auditoria_honesta(audit, sent)

    # 3. Defense claims confrontation verification (Art. 381, III, CPP)
    assert_all_teses_answered(sent.texto_completo, case_data["teses_defesa"])
    texto_lower = sent.texto_completo.lower()
    assert "decadência" in texto_lower or "decadencia" in texto_lower
    assert "legítima defesa" in texto_lower or "legitima defesa" in texto_lower

    # 4. Statutories and Súmulas verification
    assert "588" in sent.texto_completo  # Súmula 588 STJ blocks PRD substitution
    assert "983" in sent.texto_completo  # Tema 983 STJ damages
    assert "art. 21" in texto_lower  # Art. 21 LMP victim notice

    # 5. Triple Export verification
    docx_p = tmp_path / "case_01.docx"
    exportar_docx(sent, docx_p)
    assert docx_p.exists() and docx_p.stat().st_size > 5000

    html_str = exportar_html_pje(sent)
    html_p = tmp_path / "case_01.html"
    html_p.write_text(html_str, encoding="utf-8")
    assert html_p.exists() and html_p.stat().st_size > 1000
    assert "DISPOSITIVO" in html_str and "RELATÓRIO" in html_str

    pdf_p = tmp_path / "case_01.pdf"
    exportar_pdf(sent, pdf_p)
    assert pdf_p.exists() and pdf_p.stat().st_size > 5000


# ==============================================================================
# Scenario 2: Descumprimento de Medida Protetiva de Urgência (MPU)
# Processo nº 0803456-12.2025.8.20.5001
# ==============================================================================
class TestScenario2DescumprimentoMPUWhatsappConfissao:
  """Case 2: Art. 24-A LMP + Art. 147 CP with WhatsApp messages and confession."""

  def test_case_2_full_pipeline_audit_and_export(
      self, load_fixture_case, tmp_path
  ):
    """Executes Case 2 end-to-end, asserting Súmula 231 floor, zero hallucination, and triple export."""
    case_data = load_fixture_case("2")
    pecas = classificar_pecas_autos(case_data["raw_documents"])
    meta = extrair_metadados_processo(pecas)
    ids_midias = [t["media_id"] for t in case_data.get("transcriptions", [])]

    sent = gerar_sentenca_caso_real(case_data)
    assert "0803456-12.2025.8.20.5001" in sent.numero_processo

    audit = auditar_com_dosimetria(sent, case_data)
    assert_auditoria_honesta(audit, sent)

    # Confrontation of defense claims
    assert_all_teses_answered(sent.texto_completo, case_data["teses_defesa"])

    # Súmula 231 STJ must be respected
    dosi_text = (sent.secao_dosimetria + " " + sent.secao_dispositivo).lower()
    assert "231" in dosi_text or "súmula" in dosi_text

    # Triple Export verification
    docx_p = tmp_path / "case_02.docx"
    exportar_docx(sent, docx_p)
    assert docx_p.exists() and docx_p.stat().st_size > 5000

    html_str = exportar_html_pje(sent)
    html_p = tmp_path / "case_02.html"
    html_p.write_text(html_str, encoding="utf-8")
    assert html_p.exists() and html_p.stat().st_size > 1000

    pdf_p = tmp_path / "case_02.pdf"
    exportar_pdf(sent, pdf_p)
    assert pdf_p.exists() and pdf_p.stat().st_size > 5000


# ==============================================================================
# Scenario 3: Lesão Corporal com Especial Relevo da Palavra da Vítima
# Processo nº 0804567-89.2024.8.20.5001
# ==============================================================================
class TestScenario3LesaoPalavraVitimaClandestinidade:
  """Case 3: Domestic violence in clandestine setting under CNJ Res. 492/2023."""

  def test_case_3_full_pipeline_audit_and_export(
      self, load_fixture_case, tmp_path
  ):
    """Executes Case 3 end-to-end, asserting gender protocol compliance, 100.0 score, and triple export."""
    case_data = load_fixture_case("3")
    pecas = classificar_pecas_autos(case_data["raw_documents"])
    meta = extrair_metadados_processo(pecas)
    ids_midias = [t["media_id"] for t in case_data.get("transcriptions", [])]

    sent = gerar_sentenca_caso_real(case_data)
    assert "0804567-89.2024.8.20.5001" in sent.numero_processo

    audit = auditar_com_dosimetria(sent, case_data)
    assert_auditoria_honesta(audit, sent)

    # Gender protocol Res. CNJ 492/2023 must be present in fundamentação
    assert "492" in sent.texto_completo
    assert_all_teses_answered(sent.texto_completo, case_data["teses_defesa"])

    # Triple Export verification
    docx_p = tmp_path / "case_03.docx"
    exportar_docx(sent, docx_p)
    assert docx_p.exists() and docx_p.stat().st_size > 5000

    html_str = exportar_html_pje(sent)
    html_p = tmp_path / "case_03.html"
    html_p.write_text(html_str, encoding="utf-8")
    assert html_p.exists() and html_p.stat().st_size > 1000

    pdf_p = tmp_path / "case_03.pdf"
    exportar_pdf(sent, pdf_p)
    assert pdf_p.exists() and pdf_p.stat().st_size > 5000


# ==============================================================================
# Scenario 4: Réu Multirreincidente e Blindagem da Súmula 444 STJ
# Processo nº 0805678-90.2025.8.20.5001
# ==============================================================================
class TestScenario4MultirreincidenteSumula444:
  """Case 4: Multirecidivist defendant with active inquiries and Súmula 444 STJ protection."""

  def test_case_4_full_pipeline_audit_and_export(
      self, load_fixture_case, tmp_path
  ):
    """Executes Case 4 end-to-end, asserting Súmula 444 neutral antecedents, 100.0 score, and triple export."""
    case_data = load_fixture_case("4")
    pecas = classificar_pecas_autos(case_data["raw_documents"])
    meta = extrair_metadados_processo(pecas)
    ids_midias = [t["media_id"] for t in case_data.get("transcriptions", [])]

    sent = gerar_sentenca_caso_real(case_data)
    assert "0805678-90.2025.8.20.5001" in sent.numero_processo

    audit = auditar_com_dosimetria(sent, case_data)
    assert_auditoria_honesta(audit, sent)

    # Súmula 444 STJ must be cited
    assert "444" in sent.texto_completo
    assert_all_teses_answered(sent.texto_completo, case_data["teses_defesa"])

    # Triple Export verification
    docx_p = tmp_path / "case_04.docx"
    exportar_docx(sent, docx_p)
    assert docx_p.exists() and docx_p.stat().st_size > 5000

    html_str = exportar_html_pje(sent)
    html_p = tmp_path / "case_04.html"
    html_p.write_text(html_str, encoding="utf-8")
    assert html_p.exists() and html_p.stat().st_size > 1000

    pdf_p = tmp_path / "case_04.pdf"
    exportar_pdf(sent, pdf_p)
    assert pdf_p.exists() and pdf_p.stat().st_size > 5000


# ==============================================================================
# Scenario 5: Auto-Correção Cirúrgica e Fallback Determinístico
# Processo nº 0806789-01.2025.8.20.5001
# ==============================================================================
class TestScenario5AutocorrectionFallbackRecovery:
  """Case 5: Sectional state machine with surgical auto-correction / fallback recovery."""

  def test_case_5_full_pipeline_audit_and_export(
      self, load_fixture_case, tmp_path
  ):
    """Executes Case 5 through SectionalPipelineEngine, ensuring 100.0 score and triple export."""
    case_data = load_fixture_case("5")
    pecas = classificar_pecas_autos(case_data["raw_documents"])
    meta = extrair_metadados_processo(pecas)
    ids_midias = [t["media_id"] for t in case_data.get("transcriptions", [])]

    # Sectional pipeline execution
    engine = SectionalPipelineEngine(use_ollama=False, max_retries=2)
    sent = engine.executar_pipeline(case_data, custom_dosimetria_override=veredictos_condenatorios(case_data))
    assert "0806789-01.2025.8.20.5001" in sent.numero_processo

    audit = auditar_com_dosimetria(sent, case_data)
    assert_auditoria_honesta(audit, sent)

    assert_all_teses_answered(sent.texto_completo, case_data["teses_defesa"])

    # Triple Export verification
    docx_p = tmp_path / "case_05.docx"
    exportar_docx(sent, docx_p)
    assert docx_p.exists() and docx_p.stat().st_size > 5000

    html_str = exportar_html_pje(sent)
    html_p = tmp_path / "case_05.html"
    html_p.write_text(html_str, encoding="utf-8")
    assert html_p.exists() and html_p.stat().st_size > 1000

    pdf_p = tmp_path / "case_05.pdf"
    exportar_pdf(sent, pdf_p)
    assert pdf_p.exists() and pdf_p.stat().st_size > 5000


# ==============================================================================
# Cross-Scenario Standardization & Format Compliance
# ==============================================================================
class TestAllFiveCasesFormatCompliance:
  """Verifies uniform format compliance across all 5 authentic cases."""

  @pytest.mark.parametrize("case_id", ["1", "2", "3", "4", "5"])
  def test_pje_clean_html_across_all_cases(self, case_id, load_fixture_case):
    """Every case produces valid PJe-clean HTML with mandatory chapters and no prohibited tags."""
    case_data = load_fixture_case(case_id)
    sent = gerar_sentenca_caso_real(case_data)
    html_str = exportar_html_pje(sent)

    assert len(html_str) > 1000
    assert "I – RELATÓRIO" in html_str
    assert "II – FUNDAMENTAÇÃO" in html_str
    assert "III – DISPOSITIVO" in html_str

    # Prohibited tags in PJe editor
    assert "<script" not in html_str.lower()
    assert "<iframe" not in html_str.lower()
    assert "<link" not in html_str.lower()

  @pytest.mark.parametrize("case_id", ["1", "2", "3", "4", "5"])
  def test_auditor_reliability_score_100_across_all_cases(
      self, case_id, load_fixture_case
  ):
    """Every case: form score 100.0, zero hallucination and penalties matching the dosimetry;
    the overall score reaches 100.0 exactly when no [COMPLETAR PELO JUÍZO] gap remains. Without
    the judge's verdict the real-case sentence is refused (DecisaoJudicialPendenteError)."""
    case_data = load_fixture_case(case_id)

    with pytest.raises(DecisaoJudicialPendenteError):
      gerar_sentenca_estruturada(case_data, use_ollama_if_available=False)

    sent = gerar_sentenca_caso_real(case_data)
    audit = auditar_com_dosimetria(sent, case_data)

    assert_auditoria_honesta(audit, sent)
    assert audit.score_forma == 100.0
    assert audit.ids_inexistentes == []
    assert audit.citacoes_nao_autorizadas == []
    assert audit.teses_omitidas == []
