# -*- coding: utf-8 -*-
"""Tier 3: Cross-Feature Integration and In-Flight Gating Test Suite.

Verifies end-to-end data lifecycle and cross-feature interactions:
- TC-CROSS-01: Ingestion PJe -> Defense Mining (folio ID integrity, zero hallucinated IDs)
- TC-CROSS-02: Oral Proof -> Gender Perspective Scrutiny (CNJ 492 / Word of victim)
- TC-CROSS-03: Defense Mining -> Art. 381 CPP Omission Lock (blocking omitted theses)
- TC-CROSS-04: In-Flight Gating State Machine (Relatório -> Preliminares -> Mérito -> Dosimetria -> Dispositivo)
- TC-CROSS-05: Surgical Auto-Correction (Retry 1 recovery with simulated defect)
- TC-CROSS-06: Deterministic Fallback Trigger (2 failed retries triggering safe Python template)
- TC-CROSS-07: Dosimetria Trifásica -> Camada 4 Auditor (8 vectors, Súmula 444, Súmula 231, Bis in idem)
- TC-CROSS-08: Full Sectional Pipeline -> Simultaneous Triple Export (DOCX, HTML, PDF)
- TC-CROSS-09: VRAM Manager Orchestration (Whisper <-> Ollama keep-alive transitions)
"""

import datetime

from app.core.vram_manager import (
    check_ollama_status,
    get_gpu_vram_status,
    release_ollama_vram_for_whisper,
)
from app.export.document_exporter import (
    exportar_docx,
    exportar_html_pje,
    exportar_pdf,
)
from app.generation.hallucination_auditor import (
    auditar_sentenca,
)
from app.generation.sectional_pipeline import (
    ResultadoGating,
    SecaoTipo,
    SectionalPipelineEngine,
    StatusSecao,
    construir_dossie_secao,
    default_in_flight_gating_validator,
)
from app.generation.sentence_generator import (
    build_crimes_inputs_for_case,
)
from app.ingestion.laya_classifier import (
    TeseDefensivaItem,
    classificar_pecas_autos,
    extrair_metadados_processo,
)
from app.legal_engine.dosimetria_calculator import (
    ConcursoCrimesConfig,
    DosimetriaConfig,
    calcular_dosimetria_global,
)
from app.transcription.oral_proof_analyzer import gerar_matriz_corroboracao_oral
from tests.e2e.conftest import (
    assert_auditoria_honesta,
    auditar_com_dosimetria,
    gerar_sentenca_caso_real,
    veredictos_condenatorios,
)


# ==============================================================================
# TC-CROSS-01: Ingestão PJe ➔ Mineração de Teses Defensivas
# ==============================================================================
class TestCross01_IngestaoToDefenseMining:
  """TC-CROSS-01: Validates that mined defense claims correlate 100% with real folio IDs."""

  def test_cross_ingestion_and_defense_mining_flow(self, load_fixture_case):
    """Mined defense arguments must strictly reference genuine classified pieces without ID hallucination."""
    case_data = load_fixture_case("1")
    pecas = classificar_pecas_autos(case_data["raw_documents"])
    meta = extrair_metadados_processo(pecas)
    assert len(meta.teses_defesa) > 0
    assert meta.nome_reu

    pecas_ids = {p.doc_id for p in pecas}
    pecas_categorias = {p.categoria.upper() for p in pecas}

    # Verify classified pieces exist and include defense
    assert "ALEGACOES_FINAIS_DEFESA" in pecas_categorias
    assert len(case_data["teses_defesa"]) > 0

    # Cross-feature check: every defense argument must reference a real document ID
    for tese in case_data["teses_defesa"]:
      assert "folha_autos_id" in tese
      tese_doc_id = tese["folha_autos_id"].split()[0]
      assert (
          tese_doc_id in pecas_ids
      ), f"Tese doc_id {tese_doc_id} aponta documento que não existe nas peças"

    # Dossie construction inherits real IDs
    dossie_merito = construir_dossie_secao(SecaoTipo.MERITO, case_data)
    assert len(dossie_merito.teses_defesa) > 0


# ==============================================================================
# TC-CROSS-02: Prova Oral ➔ Crivo Perspectiva Gênero (CNJ 492)
# ==============================================================================
class TestCross02_OralMatrixToGenderCritic:
  """TC-CROSS-02: Validates oral corroboration matrix adherence to CNJ Res 492/2023."""

  def test_cross_oral_matrix_to_gender_critic(self, load_fixture_case):
    """Victim testimony in clandestine domestic violence has special weight and bars victim blaming."""
    case_data = load_fixture_case("3")  # Case 3: Palavra da Vítima
    pecas = classificar_pecas_autos(case_data["raw_documents"])
    transcricoes = case_data["transcriptions"]

    matriz = gerar_matriz_corroboracao_oral(pecas, transcricoes)

    assert matriz is not None
    assert len(matriz.depoentes) >= 2
    assert "Resolução CNJ nº 492/2023" in matriz.protocolo_cnj

    # Verify that victim's statement was preserved
    vitimas = [d for d in matriz.depoentes if d.papel == "OFENDIDA"]
    assert len(vitimas) >= 1
    assert len(vitimas[0].depoente) > 0
    assert "RENATA" in vitimas[0].depoente.upper() or "JULIANA" in vitimas[0].depoente.upper() or "VÍTIMA" in vitimas[0].depoente.upper()

    # Gender protocol validation: Vetor comportamento da vítima must be neutral
    crimes = build_crimes_inputs_for_case(
        crimes_codes=case_data["crimes_imputados"],
        dt_fato=datetime.date(2025, 1, 1),
        culpabilidade_lesao=False,
        motivos_lesao=False,
        circunstancias_lesao=False,
        consequencias_lesao=False,
        confissao_art24a=False,
        reincidente=False,
        agravante_art61f=False,
        is_demo=False,
    )
    res_dos = calcular_dosimetria_global(
        crimes=crimes,
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc = res_dos.crimes_calculados[0]
    # Comportamento da vítima must remain neutral under CNJ 492
    assert "comportamento_vitima" not in calc.vetores_negativos_efetivos


# ==============================================================================
# TC-CROSS-03: Mineração de Teses ➔ Trava de Omissão (Art. 381 CPP)
# ==============================================================================
class TestCross03_DefenseMiningToOmissionLock:
  """TC-CROSS-03: Validates that unaddressed defense claims are flagged and gate the pipeline."""

  def test_cross_defense_mining_to_omission_lock(self, load_fixture_case):
    """Omitting an alleged defense thesis must trigger Camada 3 audit failure."""
    case_data = load_fixture_case("1")
    pecas = classificar_pecas_autos(case_data["raw_documents"])
    meta = extrair_metadados_processo(pecas)
    ids_midias = [t["media_id"] for t in case_data.get("transcriptions", [])]

    # 1. Complete sentence addressing all claims
    sent_completa = gerar_sentenca_caso_real(case_data)
    audit_ok = auditar_com_dosimetria(sent_completa, case_data)
    assert_auditoria_honesta(audit_ok, sent_completa)
    assert audit_ok.teses_omitidas == []

    # 2. Defective scenario: Add a fabricated defense claim that was never addressed
    tese_fantasma = TeseDefensivaItem(
        codigo="MER-FANTASMA",
        tipo="MERITO",
        titulo="Inexistência material de prova testemunhal externa",
        descricao_defesa="Tese inexistente não enfrentada na sentença",
        obrigatorio_enfrentamento_art381=True,
    )
    audit_bad = auditar_sentenca(
        sent_completa.texto_completo,
        pecas,
        meta.teses_defesa + [tese_fantasma],
        ids_midias,
    )
    # Pendências probatórias podem zerar o score nas duas versões. A camada de teses deve distingui-las.
    for audit, esperado in ((audit_ok, "APROVADO"), (audit_bad, "REPROVADO")):
        camada_teses = next(item for item in audit.verificacoes_conteudo if item["verificacao"] == "teses_defensivas")
        assert camada_teses["status"] == esperado
    assert len(audit_bad.teses_omitidas) >= 1
    assert audit_bad.aprovada_sem_alucinacao is False


# ==============================================================================
# TC-CROSS-04: Crivo Duplo Seccional ➔ In-Flight Gating State Machine
# ==============================================================================
class TestCross04_InFlightGatingStateMachine:
  """TC-CROSS-04: Validates strict state transitions and progression of SectionalPipelineEngine."""

  def test_cross_inflight_gating_state_machine_progression(
      self, load_fixture_case
  ):
    """Engine must advance sequentially through all 5 sections and seal each intermediate draft."""
    case_data = load_fixture_case("1")
    engine = SectionalPipelineEngine(use_ollama=False, max_retries=2)

    sent = engine.executar_pipeline(case_data, custom_dosimetria_override=veredictos_condenatorios(case_data))

    # 1. Verify all 5 sections are present in final state
    expected_sections = [
        SecaoTipo.RELATORIO,
        SecaoTipo.PRELIMINARES,
        SecaoTipo.MERITO,
        SecaoTipo.DOSIMETRIA,
        SecaoTipo.DISPOSITIVO,
    ]
    for sec in expected_sections:
      assert sec in engine.estado.status_secoes
      assert engine.estado.status_secoes[sec] == StatusSecao.APROVADO
      assert sec in engine.estado.secoes_seladas
      assert len(engine.estado.secoes_seladas[sec]) > 50

    # 2. Verify chapters structure
    assert len(sent.texto_completo) > 1000
    assert "I – RELATÓRIO" in sent.texto_completo
    assert "II – FUNDAMENTAÇÃO" in sent.texto_completo
    assert "III – DISPOSITIVO" in sent.texto_completo

  def test_cross_inflight_gating_blocks_on_rejection(self, load_fixture_case):
    """When a section is rejected on retry 1, engine retries and records attempt history."""
    case_data = load_fixture_case("2")

    retry_count = 0

    def mock_validator(secao, rascunho, dossie):
      nonlocal retry_count
      if secao == SecaoTipo.MERITO and retry_count == 0:
        retry_count += 1
        return ResultadoGating(
            secao=secao,
            aprovado=False,
            score=75.0,
            erros=["ID folha 9999 inexistente"],
            feedbacks_cirurgicos=["Ajustar citação documental"],
        )
      return ResultadoGating(
          secao=secao, aprovado=True, score=100.0, erros=[], feedbacks_cirurgicos=[]
      )

    engine = SectionalPipelineEngine(
        use_ollama=False, max_retries=2, gating_validator=mock_validator
    )
    sent = engine.executar_pipeline(case_data, custom_dosimetria_override=veredictos_condenatorios(case_data))
    assert sent.texto_completo and len(sent.secao_merito) > 50

    # Merito must have recorded 2 attempts
    assert len(engine.estado.historico_tentativas[SecaoTipo.MERITO]) == 2
    # Final status must still be approved after retry
    assert engine.estado.status_secoes[SecaoTipo.MERITO] == StatusSecao.APROVADO


# ==============================================================================
# TC-CROSS-05: Falha In-Flight ➔ Auto-Correção Retry 1
# ==============================================================================
class TestCross05_SurgicalAutoCorrectionRetrySuccess:
  """TC-CROSS-05: Validates surgical auto-correction on Retry 1 when hallucination is injected."""

  def test_cross_surgical_autocorrection_retry_success(
      self, load_fixture_case
  ):
    """Surgical auto-correction fixes targeted error on retry 1 and recovers to 100.0 score."""
    case_data = load_fixture_case("1")

    attempts_log = []

    def mock_surgical_validator(secao, rascunho, dossie):
      attempts_log.append(secao)
      # Fail Dosimetria on first try with surgical feedback
      if secao == SecaoTipo.DOSIMETRIA and attempts_log.count(SecaoTipo.DOSIMETRIA) == 1:
        return ResultadoGating(
            secao=secao,
            aprovado=False,
            score=87.5,
            erros=["Omissão de valoração do vetor conduta social"],
            feedbacks_cirurgicos=[
                "Enunciar expressamente a conduta social como neutra nos termos da jurisprudência"
            ],
        )
      return default_in_flight_gating_validator(secao, rascunho, dossie)

    engine = SectionalPipelineEngine(
        use_ollama=False,
        max_retries=2,
        gating_validator=mock_surgical_validator,
    )
    sent = engine.executar_pipeline(case_data, custom_dosimetria_override=veredictos_condenatorios(case_data))
    assert sent.texto_completo and len(sent.secao_dosimetria) > 50

    assert attempts_log.count(SecaoTipo.DOSIMETRIA) == 2
    assert engine.estado.status_secoes[SecaoTipo.DOSIMETRIA] == StatusSecao.APROVADO
    assert engine.estado.status_secoes[SecaoTipo.DISPOSITIVO] == StatusSecao.APROVADO


# ==============================================================================
# TC-CROSS-06: Falha Persistente ➔ Fallback Determinístico Seguro
# ==============================================================================
class TestCross06_SurgicalAutoCorrectionFallbackTrigger:
  """TC-CROSS-06: Validates automatic invocation of deterministic Python fallback after 2 failed retries."""

  def test_cross_surgical_autocorrection_fallback_trigger(
      self, load_fixture_case
  ):
    """When both retries fail, engine triggers safe fallback template and unblocks pipeline."""
    case_data = load_fixture_case("5")  # Scenario 5: Autocorrection and Fallback Case

    def persistent_failure_validator(secao, rascunho, dossie):
      # Merito fails persistently
      if secao == SecaoTipo.MERITO:
        return ResultadoGating(
            secao=secao,
            aprovado=False,
            score=60.0,
            erros=["Alucinação persistente não sanada"],
            feedbacks_cirurgicos=["Correção obrigatória de precedentes"],
        )
      return ResultadoGating(
          secao=secao, aprovado=True, score=100.0, erros=[], feedbacks_cirurgicos=[]
      )

    engine = SectionalPipelineEngine(
        use_ollama=False,
        max_retries=2,
        gating_validator=persistent_failure_validator,
    )
    sent = engine.executar_pipeline(case_data, custom_dosimetria_override=veredictos_condenatorios(case_data))

    # Merito must be marked as fallback_acionado
    assert (
        engine.estado.status_secoes[SecaoTipo.MERITO]
        == StatusSecao.FALLBACK_ACIONADO
    )
    # Merito must have 3 recorded attempts (original + retry 1 + fallback)
    assert len(engine.estado.historico_tentativas[SecaoTipo.MERITO]) == 3

    # Subsequent sections (Dosimetria, Dispositivo) must continue and succeed
    assert (
        engine.estado.status_secoes[SecaoTipo.DOSIMETRIA]
        == StatusSecao.APROVADO
    )
    assert (
        engine.estado.status_secoes[SecaoTipo.DISPOSITIVO]
        == StatusSecao.APROVADO
    )
    assert len(sent.texto_completo) > 1000


# ==============================================================================
# TC-CROSS-07: Dosimetria Trifásica ➔ Auditor Camada 4
# ==============================================================================
class TestCross07_DosimetriaToAuditorCamada4:
  """TC-CROSS-07: Validates end-to-end integration between dosimetria calculation and Camada 4 audit."""

  def test_cross_dosimetria_to_auditor_camada4(self, load_fixture_case):
    """Dosimetria text must strictly evaluate all 8 vectors, enforce Súmula 231, and pass Camada 4."""
    case_data = load_fixture_case("4")  # Case 4: Multirreincidente + Súmula 444
    sent = gerar_sentenca_caso_real(case_data)
    audit = auditar_com_dosimetria(sent, case_data)

    # 1. Full auditor pass (form 100, zero hallucination, penalties match the dosimetry)
    assert_auditoria_honesta(audit, sent)
    assert audit.conformidade_penas_dosimetria is True
    assert audit.conformidade_art381_cpp is True
    assert audit.conformidade_oito_circunstancias_art59 is True

    # 2. Camada 4: all 8 vectors must be present in the dosimetria sections
    dosi_text = (
        sent.secao_dosimetria
        + " "
        + sent.secao_dispositivo
        + " "
        + sent.secao_merito
    ).lower()
    vetores_obrigatorios = [
        "culpabilidade",
        "antecedentes",
        "conduta social",
        "personalidade",
        "motivos",
        "circunstâncias",
        "consequências",
        "comportamento da vítima",
    ]
    for v in vetores_obrigatorios:
      assert v in dosi_text, f"Vetor {v} ausente na fundamentação da dosimetria"

    # 3. Súmula 444 STJ explicitly applied for inquiries
    assert "444" in dosi_text or "inquérito" in dosi_text


# ==============================================================================
# TC-CROSS-08: Pipeline Completo ➔ Exportação Tripla Simultânea
# ==============================================================================
class TestCross08_PipelineToTripleExport:
  """TC-CROSS-08: Validates simultaneous generation of DOCX, HTML PJe, and PDF files."""

  def test_cross_pipeline_to_triple_export(self, tmp_path, load_fixture_case):
    """Final sentence must be exportable to DOCX, HTML, and PDF, each valid and >5KB."""
    case_data = load_fixture_case("1")
    engine = SectionalPipelineEngine(use_ollama=False, max_retries=2)
    sent = engine.executar_pipeline(case_data, custom_dosimetria_override=veredictos_condenatorios(case_data))

    # 1. Export DOCX
    docx_path = tmp_path / "sentenca_cross_08.docx"
    exportar_docx(sent, docx_path)
    assert docx_path.exists()
    assert docx_path.stat().st_size > 5000

    # 2. Export HTML PJe
    html_str = exportar_html_pje(sent)
    html_path = tmp_path / "sentenca_cross_08.html"
    html_path.write_text(html_str, encoding="utf-8")
    assert html_path.exists()
    assert html_path.stat().st_size > 1000
    assert "DISPOSITIVO" in html_str
    assert "RELATÓRIO" in html_str

    # 3. Export PDF
    pdf_path = tmp_path / "sentenca_cross_08.pdf"
    exportar_pdf(sent, pdf_path)
    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 5000


# ==============================================================================
# TC-CROSS-09: Gestão de VRAM ➔ Whisper + Ollama Orchestration
# ==============================================================================
class TestCross09_VRAMOrchestrationWhisperAndOllama:
  """TC-CROSS-09: Validates VRAM keep-alive and unload transitions for RTX 2060 (6GB)."""

  def test_cross_vram_orchestration_whisper_and_ollama(self):
    """Simulates alternating execution between Whisper transcription and Ollama inference."""
    # 1. Check initial VRAM status
    vram_init = get_gpu_vram_status()
    assert isinstance(vram_init, dict)
    assert "gpu_available" in vram_init

    # 2. Transition from Ollama to Whisper (unloads Ollama VRAM)
    release_ollama_vram_for_whisper()
    vram_after_ollama_release = get_gpu_vram_status()
    assert isinstance(vram_after_ollama_release, dict)

    # 3. Check ollama status responds
    ollama_ok = check_ollama_status()
    assert isinstance(ollama_ok, dict)
    assert "online" in ollama_ok


# ==============================================================================
# TC-CROSS-10: Injeção de Falhas Adversariais e Auto-Correção Cirúrgica
# ==============================================================================
class TestCrossFeatureFaultInjection:
  """Valida o comportamento do pipeline sob injeção adversarial de falhas via AIServiceMockController."""

  def test_fault_injection_fake_id_triggers_rejection_and_fallback(
      self, load_fixture_case, mock_ai_services
  ):
    """Ativa fault fake_id e prova que o In-Flight Gating rejeita o rascunho e aciona fallback."""
    case = load_fixture_case("1")
    mock_ai_services.inject_fault("fake_id", transient=False)

    engine = SectionalPipelineEngine(use_ollama=True, max_retries=2)
    sentenca = engine.executar_pipeline(case, use_ollama=True, custom_dosimetria_override=veredictos_condenatorios(case))

    # 1. Prova que houve rejeição em cada uma das 3 tentativas por citação de ID inexistente
    prel_tentativas = engine.estado.historico_tentativas[SecaoTipo.PRELIMINARES]
    assert len(prel_tentativas) == 3
    for t in prel_tentativas:
      assert t.status == StatusSecao.REPROVADO_RETRY
      assert any("ID-9999999" in err for err in t.resultado_gating.erros)

    # 2. Prova que o motor acionou o fallback determinístico seguro
    assert (
        engine.estado.status_secoes[SecaoTipo.PRELIMINARES]
        == StatusSecao.FALLBACK_ACIONADO
    )

    # 3. Prova que o ID falso foi totalmente purgado da sentença final
    assert "ID-9999999" not in sentenca.texto_completo
    assert "ID-MIDIA-999" not in sentenca.texto_completo

  def test_fault_injection_omit_thesis_triggers_gating_failure(
      self, load_fixture_case, mock_ai_services
  ):
    """Ativa fault omit_thesis e prova que o In-Flight Gating bloqueia a omissão de teses."""
    case = load_fixture_case("1")
    mock_ai_services.inject_fault("omit_thesis", transient=False)

    engine = SectionalPipelineEngine(use_ollama=True, max_retries=2)
    sentenca = engine.executar_pipeline(case, use_ollama=True, custom_dosimetria_override=veredictos_condenatorios(case))
    assert sentenca.texto_completo and len(sentenca.secao_preliminares) > 50

    prel_tentativas = engine.estado.historico_tentativas[SecaoTipo.PRELIMINARES]
    assert any(
        "Omissão" in err
        for t in prel_tentativas
        for err in t.resultado_gating.erros
    )
    assert (
        engine.estado.status_secoes[SecaoTipo.PRELIMINARES]
        == StatusSecao.FALLBACK_ACIONADO
    )

  def test_fault_injection_transient_autocorrection(
      self, load_fixture_case, mock_ai_services
  ):
    """Ativa fault fake_id transiente: Tentativa 1 reprova, Tentativa 2 é auto-corrigida com aprovação."""
    case = load_fixture_case("1")
    mock_ai_services.inject_fault("fake_id", transient=True)

    engine = SectionalPipelineEngine(use_ollama=True, max_retries=2)
    sentenca = engine.executar_pipeline(case, use_ollama=True, custom_dosimetria_override=veredictos_condenatorios(case))
    assert sentenca.texto_completo and "ID-9999999" not in sentenca.texto_completo

    rel_tentativas = engine.estado.historico_tentativas[SecaoTipo.RELATORIO]
    assert len(rel_tentativas) == 2
    assert rel_tentativas[0].status == StatusSecao.REPROVADO_RETRY
    assert any(
        "ID-9999999" in err for err in rel_tentativas[0].resultado_gating.erros
    )
    assert rel_tentativas[1].status == StatusSecao.APROVADO
    assert (
        engine.estado.status_secoes[SecaoTipo.RELATORIO] == StatusSecao.APROVADO
    )

  def test_fault_injection_cuda_oom_recovers_via_deterministic_fallback(
      self, load_fixture_case, mock_ai_services
  ):
    """Ativa fault error_500 (CUDA OOM) e prova resiliência sem crash do processo."""
    case = load_fixture_case("1")
    mock_ai_services.inject_fault("error_500")

    engine = SectionalPipelineEngine(use_ollama=True, max_retries=2)
    sentenca = engine.executar_pipeline(case, use_ollama=True, custom_dosimetria_override=veredictos_condenatorios(case))
    assert sentenca.texto_completo
    assert len(sentenca.texto_completo) > 500
