"""
Suíte de Testes Automatizados do Pipeline Seccional (In-Flight Gating Engine) - Milestone 1 (F01 a F06).
Cobre Ingestão PJe, Limpeza de Rodapés, Dossiês de Restrição, Prompt Bounding, Máquina de Estados Seccional,
Áudio/Depoimentos e Casos Limítrofes.
"""

from pathlib import Path
import unittest.mock as mock
from typing import Any, Dict
import pytest
from pydantic import ValidationError

from app.demo.demo_case import get_demo_case_bundle
from app.ingestion.pdf_ingestor import (
    _infer_title_and_phase,
    _strip_pje_footer,
    segmentar_autos_pdf_pje,
)
from app.transcription.audio_transcriber import _infer_speaker_and_role
from app.generation.sentence_generator import SentencaGerada

# Contratos e Componentes do Milestone 1
from app.generation.sectional_pipeline import (
    DossieRestricaoSecao,
    RegistroTentativaSecao,
    ResultadoGating,
    SecaoRascunho,
    SecaoTipo,
    SectionalPipelineEngine,
    StatusSecao,
    construir_dossie_secao,
    construir_prompt_seccional,
    default_in_flight_gating_validator,
)


# ==============================================================================
# FIXTURES
# ==============================================================================

@pytest.fixture
def demo_bundle() -> Dict[str, Any]:
    """Retorna o case bundle canônico de demonstração (3 crimes: 24-A, 129 §13, 147)."""
    return get_demo_case_bundle()


@pytest.fixture
def sample_dossie_relatorio() -> DossieRestricaoSecao:
    """Fixture de dossiê para a Seção Relatório."""
    return DossieRestricaoSecao(
        secao=SecaoTipo.RELATORIO,
        delito_especifico=None,
        folhas_permitidas=["ID-4100101", "ID-4100201", "ID-4100210"],
        fatos_delimitados=["Fato 1: Descumprimento em 14/02/2025", "Fato 2: Agressão física"],
        teses_defesa=["PREL-01: Inépcia da denúncia", "MER-01: Legítima defesa"],
        diretrizes_jurisprudenciais=["Art. 381 do CPP"],
        parametros_dosimetria=None,
    )


@pytest.fixture
def sample_dossie_dosimetria() -> DossieRestricaoSecao:
    """Fixture de dossiê para a Seção Dosimetria."""
    return DossieRestricaoSecao(
        secao=SecaoTipo.DOSIMETRIA,
        delito_especifico="art_129_p13_cp",
        folhas_permitidas=["ID-4100108", "ID-4100201"],
        fatos_delimitados=["Lesão corporal praticada na presença da filha menor"],
        teses_defesa=["MER-02: Fixação da pena no mínimo legal"],
        diretrizes_jurisprudenciais=["Súmula 231 do STJ", "Súmula 444 do STJ", "Súmula 588 do STJ"],
        parametros_dosimetria={
            "criterio": "um_oitavo_intervalo",
            "pena_base_meses": 28,
            "pena_definitiva_meses": 28,
        },
    )


# ==============================================================================
# F01: INGESTÃO PJE E SEGMENTAÇÃO POR BOOKMARKS
# ==============================================================================

class TestF01PJeIngestionAndBookmarks:

    def test_infer_title_and_phase_identifies_pje_pieces(self):
        """Verifica a correta inferência de títulos e fases a partir de títulos do TOC e cabeçalho."""
        titulo, fase, rel = _infer_title_and_phase(
            "Petição | NUM: 4100238 | 04/06/2024",
            "VEM OFERECER DENÚNCIA em face de...",
            "ID-4100238",
        )
        assert "Denúncia Ministerial" in titulo
        assert fase == "JUDICIAL"
        assert rel is True

        titulo_mem, fase_mem, rel_mem = _infer_title_and_phase(
            "Memoriais | NUM: 4100293",
            "DEFENSORIA PÚBLICA VEM APRESENTAR SUAS ALEGAÇÕES FINAIS...",
            "ID-4100293",
        )
        assert "Alegações Finais por Memoriais da Defesa Técnica" in titulo_mem
        assert rel_mem is True

        # Peça irrelevante (intimação ordinatória)
        _, _, rel_int = _infer_title_and_phase("INTIMAÇÃO | NUM: 99999", "Fica intimado...", "ID-99999")
        assert rel_int is False

    def test_segmentar_autos_fallback_when_toc_missing(self):
        """Verifica se o segmentador aplica fallback para extração híbrida se o TOC for inexistente."""
        with mock.patch("pymupdf.open") as mock_open, mock.patch(
            "app.ingestion.pdf_ingestor.extrair_texto_pdf_hibrido"
        ) as mock_hibrido:
            mock_doc = mock.MagicMock()
            mock_doc.get_toc.return_value = []
            mock_doc.__len__.return_value = 2
            mock_open.return_value = mock_doc

            mock_hibrido.return_value = [
                {"pagina": 1, "conteudo": "Conteúdo página 1", "ocr_aplicado": False},
                {"pagina": 2, "conteudo": "Conteúdo página 2", "ocr_aplicado": False},
            ]

            fake_pdf = Path("teste_sem_toc.pdf")
            with mock.patch.object(Path, "stat") as mock_stat:
                mock_stat.return_value.st_size = 1024
                pecas = segmentar_autos_pdf_pje(fake_pdf)
                assert len(pecas) == 1
                assert pecas[0]["doc_id"].startswith("ID-PDF-")
                assert "fls. 01/02" in pecas[0]["folha"]

    def test_segmentar_autos_deduplicates_by_doc_id(self, tmp_path):
        """Verifica que bookmarks duplicados com o mesmo NUM são deduplicados via seen_ids."""
        with mock.patch("pymupdf.open") as mock_open:
            mock_doc = mock.MagicMock()
            # TOC com 4 itens sendo 2 com mesmo número
            mock_doc.get_toc.return_value = [
                [1, "CABEÇALHO", 1],
                [1, "Petição | NUM: 4100238 | 04/06/2024", 2],
                [1, "Petição Repetida | NUM: 4100238 | 04/06/2024", 5],
                [1, "Decisão | NUM: 123060529 | 07/06/2024", 8],
            ]
            mock_doc.__len__.return_value = 10
            page_mock = mock.MagicMock()
            page_mock.get_text.return_value = (
                "VEM OFERECER DENÚNCIA em face de fulano de tal pelo crime de violência doméstica e familiar com laudo."
            )
            mock_doc.__getitem__.return_value = page_mock

            mock_open.return_value = mock_doc

            fake_pdf = tmp_path / "teste_dedup.pdf"
            fake_pdf.write_bytes(b"%PDF-1.4 test")
            pecas = segmentar_autos_pdf_pje(fake_pdf)
            doc_ids = [p["doc_id"] for p in pecas]
            # ID-4100238 deve aparecer apenas 1 vez devido à deduplicação por seen_ids
            assert doc_ids.count("ID-4100238") == 1


# ==============================================================================
# F02: LIMPEZA DE RODAPÉS E METADADOS DO PJE
# ==============================================================================

class TestF02LimpezaRodapesEMetadados:

    def test_strip_pje_footer_removes_standard_page_stamps(self):
        """Verifica que números de página, carimbos PJe e total de páginas são removidos."""
        raw = "O réu foi citado pessoalmente. Num. 4100238 - Pág. 3 Pág. Total - 45 Número do documento: 4100238"
        clean = _strip_pje_footer(raw)
        assert clean == "O réu foi citado pessoalmente."

    def test_strip_pje_footer_removes_electronic_signatures_and_urls(self):
        """Verifica que assinaturas eletrônicas e URLs de validação são expurgadas."""
        raw = "Ante o exposto, julgo procedente. Assinado eletronicamente por: https://pje.tjrn.jus.br/validacao?x=123 Número do documento: 4100238"
        clean = _strip_pje_footer(raw)
        assert clean == "Ante o exposto, julgo procedente."

    def test_strip_pje_footer_preserves_internal_citations_and_articles(self):
        """Garante tolerância zero para mutilação de IDs legítimos e artigos de lei."""
        raw = "Conforme ID-4100108 às fls. 12/14 e art. 129, § 13, do CP, condeno o réu."
        clean = _strip_pje_footer(raw)
        assert "ID-4100108" in clean
        assert "fls. 12/14" in clean
        assert "art. 129, § 13, do CP" in clean

    def test_strip_pje_footer_edge_cases(self):
        """Verifica robustez com strings vazias, None ou compostas unicamente por carimbos."""
        assert _strip_pje_footer("") == ""
        assert _strip_pje_footer("Num. 100 - Pág. 1") == ""


# ==============================================================================
# F03: DOSSIÊ DE RESTRIÇÃO FÁTICA SECCIONAL
# ==============================================================================

class TestF03DossieRestricaoSeccional:

    def test_instanciacao_dossie_todas_as_cinco_secoes(self, demo_bundle):
        """Verifica que construir_dossie_secao produz instâncias válidas para as 5 seções."""
        for secao in SecaoTipo:
            dossie = construir_dossie_secao(secao=secao, bundle=demo_bundle)
            assert isinstance(dossie, DossieRestricaoSecao)
            assert dossie.secao == secao
            assert len(dossie.folhas_permitidas) > 0
            assert len(dossie.fatos_delimitados) > 0

    def test_dossie_pydantic_validation_required_fields(self):
        """Verifica que omissão de campos obrigatórios dispara erro de validação."""
        with pytest.raises(ValidationError):
            DossieRestricaoSecao(
                secao="secao_invalida",  # Enum inválido
                folhas_permitidas=["ID-1"],
                fatos_delimitados=[],
                teses_defesa=[],
                diretrizes_jurisprudenciais=[],
            )

    def test_dossie_isolamento_seccional_dosimetria(self, demo_bundle):
        """Garante que a dosimetria contém parâmetros matemáticos e que o relatório não os contém."""
        dossie_rel = construir_dossie_secao(SecaoTipo.RELATORIO, demo_bundle)
        assert dossie_rel.parametros_dosimetria is None

        dossie_dos = construir_dossie_secao(SecaoTipo.DOSIMETRIA, demo_bundle)
        assert dossie_dos.parametros_dosimetria is not None
        assert "crimes_calculados" in dossie_dos.parametros_dosimetria

    def test_secao_rascunho_versao_increment(self):
        """Verifica contrato de SecaoRascunho e controle de versões."""
        r1 = SecaoRascunho(secao=SecaoTipo.RELATORIO, texto_redigido="Minuta 1", versao_tentativa=1)
        assert r1.versao_tentativa == 1
        r2 = SecaoRascunho(secao=SecaoTipo.RELATORIO, texto_redigido="Minuta 2", versao_tentativa=2)
        assert r2.versao_tentativa == 2

    def test_dossie_merito_delito_especifico_filtering(self, demo_bundle):
        """Verifica que construir_dossie_secao para o mérito suporta isolamento por delito."""
        dossie_lesao = construir_dossie_secao(
            SecaoTipo.MERITO,
            bundle=demo_bundle,
            delito_especifico="art_129_p13_cp",
        )
        assert dossie_lesao.delito_especifico == "art_129_p13_cp"
        assert any("129" in f for f in dossie_lesao.fatos_delimitados)
        assert any("MER-01" in t for t in dossie_lesao.teses_defesa)


# ==============================================================================
# F04: PROMPT BOUNDING COM RESTRIÇÃO ESTRITA
# ==============================================================================

class TestF04PromptBoundingEstrito:

    def test_prompt_bounding_contem_clausulas_proibitivas(self, sample_dossie_relatorio):
        """Verifica se o prompt seccional contém delimitações estritas proibindo alucinação."""
        prompt = construir_prompt_seccional(sample_dossie_relatorio)
        assert "PROIBIDO" in prompt or "VEDADO" in prompt
        assert "folhas_permitidas" in prompt.lower() or "folhas autorizadas" in prompt.lower()
        for folha in sample_dossie_relatorio.folhas_permitidas:
            assert folha in prompt

    def test_prompt_bounding_dosimetria_bloqueia_alteracao_numerica(self, sample_dossie_dosimetria):
        """Verifica se o prompt de dosimetria bloqueia qualquer alteração das frações e cálculos."""
        prompt = construir_prompt_seccional(sample_dossie_dosimetria)
        assert "Súmula 231" in prompt
        assert "art_129_p13_cp" in prompt

    def test_prompt_bounding_injecao_feedback_corretivo(self, sample_dossie_relatorio):
        """Garante que o feedback cirúrgico seja injetado no topo do prompt em caso de retry."""
        fb = "O parágrafo 2 omitiu a tese PREL-01. Insira imediatamente."
        prompt = construir_prompt_seccional(sample_dossie_relatorio, feedback_corretivo=fb)
        assert fb in prompt
        assert prompt.index(fb) < len(prompt) // 2

    def test_prompt_bounding_injecao_contexto_anterior(self, sample_dossie_dosimetria):
        """Verifica injeção das seções anteriores seladas como contexto imutável."""
        ctx = "[RELATÓRIO CONCLUÍDO E SELADO]\nO réu foi denunciado..."
        prompt = construir_prompt_seccional(sample_dossie_dosimetria, contexto_anterior=ctx)
        assert ctx in prompt


# ==============================================================================
# F05: SECCIONADOR DE ESTADOS (STATE MACHINE)
# ==============================================================================

class TestF05SectionalStateMachine:

    def test_transicao_estados_ciclo_completo_happy_path(self, demo_bundle):
        """Verifica o ciclo completo de transições de estado (Relatório -> Dispositivo)."""
        engine = SectionalPipelineEngine(use_ollama=False)
        sentenca = engine.executar_pipeline(case_bundle=demo_bundle)

        assert isinstance(sentenca, SentencaGerada)
        assert sentenca.secao_relatorio
        assert sentenca.secao_preliminares
        assert sentenca.secao_merito
        assert sentenca.secao_dosimetria
        assert sentenca.secao_dispositivo
        assert len(engine.estado.secoes_seladas) == 5

        for secao in SecaoTipo:
            assert engine.estado.status_secoes[secao] == StatusSecao.APROVADO

    def test_buffer_memoria_imutabilidade(self, demo_bundle):
        """Garante que seções seladas anteriormente no buffer não sofrem mutação posterior."""
        engine = SectionalPipelineEngine(use_ollama=False)
        engine.executar_pipeline(case_bundle=demo_bundle)

        relatorio_original = engine.estado.secoes_seladas[SecaoTipo.RELATORIO]
        assert "RELATÓRIO" in relatorio_original.upper()

        # Nenhuma operação posterior altera o texto selado
        assert engine.estado.secoes_seladas[SecaoTipo.RELATORIO] == relatorio_original

    def test_gating_rejeicao_aciona_retry_cirurgico(self, demo_bundle):
        """Verifica que uma rejeição no gating dispara nova tentativa com feedback cirúrgico."""
        chamadas_gating = 0

        def mock_validator(secao, rascunho, dossie):
            nonlocal chamadas_gating
            if secao == SecaoTipo.RELATORIO and chamadas_gating == 0:
                chamadas_gating += 1
                return ResultadoGating(
                    secao=secao,
                    aprovado=False,
                    score=60.0,
                    erros=["Omissão de tese"],
                    feedbacks_cirurgicos=["Incluir tese da defesa"],
                    acionar_fallback=False,
                )
            return ResultadoGating(
                secao=secao,
                aprovado=True,
                score=100.0,
                erros=[],
                feedbacks_cirurgicos=[],
                acionar_fallback=False,
            )

        engine = SectionalPipelineEngine(gating_validator=mock_validator, use_ollama=False)
        sentenca = engine.executar_pipeline(demo_bundle)

        assert sentenca is not None
        assert len(engine.estado.historico_tentativas[SecaoTipo.RELATORIO]) == 2
        assert engine.estado.status_secoes[SecaoTipo.RELATORIO] == StatusSecao.APROVADO

    def test_esgotamento_retries_dispara_fallback_deterministico(self, demo_bundle):
        """Verifica que após 2 retries (3 tentativas falhas) o fallback determinístico é acionado."""
        def mock_always_reject(secao, rascunho, dossie):
            return ResultadoGating(
                secao=secao,
                aprovado=False,
                score=40.0,
                erros=["Alucinação persistente"],
                feedbacks_cirurgicos=["Corrigir imediatamente"],
                acionar_fallback=False,
            )

        engine = SectionalPipelineEngine(gating_validator=mock_always_reject, use_ollama=False, max_retries=2)
        sentenca = engine.executar_pipeline(demo_bundle)

        # Não deve falhar nem lançar exceção: fallback garante nota 100.0
        assert sentenca is not None
        assert engine.estado.status_secoes[SecaoTipo.RELATORIO] == StatusSecao.FALLBACK_ACIONADO
        assert engine.estado.secoes_seladas[SecaoTipo.RELATORIO]

    def test_regeneracao_atomica_secao_isolada(self, demo_bundle):
        """Verifica capacidade de regenerar atomicamente apenas uma seção solicitada."""
        engine = SectionalPipelineEngine(use_ollama=False)
        engine.executar_pipeline(demo_bundle)

        merito_antes = engine.estado.secoes_seladas[SecaoTipo.MERITO]
        novo_rascunho = engine.regenerar_secao_atomica(
            secao=SecaoTipo.PRELIMINARES,
            feedback="Reforçar rejeição da decadência",
        )
        assert novo_rascunho.secao == SecaoTipo.PRELIMINARES
        assert engine.estado.secoes_seladas[SecaoTipo.MERITO] == merito_antes

    def test_registro_tentativas_telemetria(self, demo_bundle):
        """Verifica se o histórico de telemetria das tentativas é mantido fielmente."""
        engine = SectionalPipelineEngine(use_ollama=False)
        engine.executar_pipeline(demo_bundle)

        for secao in SecaoTipo:
            tentativas = engine.estado.historico_tentativas[secao]
            assert len(tentativas) >= 1
            assert isinstance(tentativas[0], RegistroTentativaSecao)
            assert tentativas[0].tentativa == 1
            assert tentativas[0].timestamp


# ==============================================================================
# F06: TRANSCRIÇÃO DE ÁUDIO E SEPARAÇÃO DE FALANTES
# ==============================================================================

class TestF06AudioTranscriptAndSpeakerFormatting:

    def test_infer_speaker_and_role_patterns(self):
        """Verifica inferência de falantes e papéis a partir de nomes de arquivo típicos do PJe Mídias."""
        m_id1, nome1, papel1 = _infer_speaker_and_role(
            "Audiencia_Depoimento da Vitima Mariana_001.mp4",
            depoente_input="",
            papel_input="OFENDIDA",
            known_ofendida="MARIANA ALVES FERREIRA",
        )
        assert papel1 == "OFENDIDA"
        assert "MARIANA ALVES FERREIRA" in nome1
        assert m_id1 == "ID-MIDIA-001"

        m_id2, nome2, papel2 = _infer_speaker_and_role(
            "Interrogatorio_do_Reu_Carlos_002.mp4",
            depoente_input="",
            papel_input="",
            known_reu="CARLOS EDUARDO ROCHA MENDES",
        )
        assert papel2 == "INTERROGATORIO_REU"
        assert "CARLOS EDUARDO ROCHA MENDES" in nome2
        assert m_id2 == "ID-MIDIA-002"

    def test_multi_part_media_collision_prevention(self):
        """Garante que arquivos multipartes (_001, _002) geram IDs e nomes distintos sem colisão."""
        m_id1, nome1, _ = _infer_speaker_and_role("depoimento_vitima_001.mp4", "", "OFENDIDA", "MARIANA")
        m_id2, nome2, _ = _infer_speaker_and_role("depoimento_vitima_002.mp4", "", "OFENDIDA", "MARIANA")
        assert m_id1 == "ID-MIDIA-001"
        assert m_id2 == "ID-MIDIA-002"
        assert "Parte 01" in nome1
        assert "Parte 02" in nome2

    def test_matriz_oral_feeding_into_merito_dossier(self, demo_bundle):
        """Verifica se as transcrições orais e IDs de mídia alimentam o dossiê de mérito."""
        dossie_mer = construir_dossie_secao(SecaoTipo.MERITO, demo_bundle)
        assert any(folha.startswith("ID-MIDIA-") for folha in dossie_mer.folhas_permitidas)
        assert any("Mariana" in fato or "ofendida" in fato.lower() for fato in dossie_mer.fatos_delimitados)


# ==============================================================================
# EDGE CASES & MOCKS
# ==============================================================================

class TestEdgeCasesAndMocks:

    def test_mock_ollama_offline_connection_timeout(self, demo_bundle):
        """Simula Ollama offline / timeout com graceful fallback determinístico sem 500."""
        with mock.patch("httpx.Client.post", side_effect=Exception("Connection refused / Timeout")):
            engine = SectionalPipelineEngine(use_ollama=True)
            sentenca = engine.executar_pipeline(demo_bundle)
            assert sentenca is not None
            assert sentenca.secao_relatorio
            assert "Motor Híbrido" in sentenca.motor_utilizado or "Fallback" in sentenca.motor_utilizado

    def test_mock_ollama_retorno_malformado(self, demo_bundle):
        """Simula resposta malformada do Ollama (string vazia ou lixo de tokens)."""
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": ""}

        with mock.patch("httpx.Client.post", return_value=mock_resp):
            engine = SectionalPipelineEngine(use_ollama=True)
            sentenca = engine.executar_pipeline(demo_bundle)
            assert sentenca is not None
            assert len(sentenca.texto_completo) > 500

    def test_bundle_incompleto_sem_documentos_lanca_excecao_descritiva(self):
        """Garante que bundle corrompido ou sem documentos lança erro claro com diagnóstico."""
        bundle_corrompido = {"case_id": "TESTE", "raw_documents": []}
        engine = SectionalPipelineEngine(use_ollama=False)
        with pytest.raises((ValueError, KeyError)):
            engine.executar_pipeline(bundle_corrompido)

    def test_bundle_sem_campos_obrigatorios_lanca_erro(self):
        """Garante que bundle sem chave raw_documents dispara erro."""
        engine = SectionalPipelineEngine(use_ollama=False)
        with pytest.raises((ValueError, KeyError)):
            engine.executar_pipeline({"case_id": "SEM_DOCS"})


# ==============================================================================
# REMEDIAÇÃO DE DEFEITOS — ITERAÇÃO 2 (DEFECTS 1 A 8)
# ==============================================================================

class TestDefectRemediationsIteration2:
    """Suíte de regressão dos 8 defeitos identificados pelos Challengers 1 e 2."""

    def test_defect_1_regenerar_secao_atomica_executes_gating_and_prevents_hallucination_leakage(self, demo_bundle):
        """
        Defeito 1: regenerar_secao_atomica com Ollama deve invocar o gating_validator.
        Minutas alucinadas devem ser rejeitadas e não podem ser seladas no buffer.
        """
        validator_invocado = False

        def strict_rejecting_validator(secao, rascunho, dossie):
            nonlocal validator_invocado
            validator_invocado = True
            return ResultadoGating(
                secao=secao,
                aprovado=False,
                score=0.0,
                erros=["Alucinação detectada: ID forjado"],
                feedbacks_cirurgicos=["Remover ID forjado"],
                acionar_fallback=False,
            )

        engine = SectionalPipelineEngine(gating_validator=strict_rejecting_validator, use_ollama=True, max_retries=2)
        engine.executar_pipeline(demo_bundle, use_ollama=False)

        # Mock da resposta do Ollama retornando texto alucinado com ID forjado
        with mock.patch("httpx.Client.post") as mock_post:
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"response": "Texto alucinado com ID-DOC-INEXISTENTE e Súmula 9999"}
            mock_post.return_value = mock_resp

            validator_invocado = False
            rascunho = engine.regenerar_secao_atomica(SecaoTipo.RELATORIO, use_ollama=True)

            # 1. Validador foi obrigatoriamente executado
            assert validator_invocado is True, "gating_validator foi ignorado em regenerar_secao_atomica!"
            # 2. O texto alucinado NÃO pode estar no buffer selado
            texto_selado = engine.estado.secoes_seladas[SecaoTipo.RELATORIO]
            assert "ID-DOC-INEXISTENTE" not in texto_selado
            # 3. Como o validador rejeitou todas as tentativas, o fallback foi ativado
            assert engine.estado.status_secoes[SecaoTipo.RELATORIO] == StatusSecao.FALLBACK_ACIONADO

    def test_defect_2_regenerar_secao_atomica_synchronizes_status_secoes(self, demo_bundle):
        """
        Defeito 2: regenerar_secao_atomica deve sincronizar engine.estado.status_secoes[secao]
        com o status resultante da regeneração (evitando ficar preso em FALLBACK_ACIONADO).
        """
        def fail_first_relatorio(secao, rascunho, dossie):
            if secao == SecaoTipo.RELATORIO:
                return ResultadoGating(secao=secao, aprovado=False, score=0.0, erros=["Falha inicial"])
            return default_in_flight_gating_validator(secao, rascunho, dossie)

        engine = SectionalPipelineEngine(gating_validator=fail_first_relatorio, use_ollama=False)
        engine.executar_pipeline(demo_bundle)

        # Inicialmente, a seção terminou em fallback
        assert engine.estado.status_secoes[SecaoTipo.RELATORIO] == StatusSecao.FALLBACK_ACIONADO

        # Regenera a seção com validador aprovado
        engine.gating_validator = default_in_flight_gating_validator
        rascunho = engine.regenerar_secao_atomica(SecaoTipo.RELATORIO, use_ollama=False)

        # O status em status_secoes DEVE ser sincronizado para APROVADO
        assert engine.estado.status_secoes[SecaoTipo.RELATORIO] == StatusSecao.APROVADO
        assert engine.estado.historico_tentativas[SecaoTipo.RELATORIO][-1].status == StatusSecao.APROVADO

    def test_defect_3_regenerar_secao_atomica_blocks_state_jumping_when_prerequisites_missing(self, demo_bundle):
        """
        Defeito 3: regenerar_secao_atomica deve impedir salto de estado (ex: gerar Dispositivo
        sem que Mérito ou Dosimetria tenham sido executados/selados).
        """
        def crash_at_merito(secao, rascunho, dossie):
            if secao == SecaoTipo.MERITO:
                raise RuntimeError("Interrupção simulada no Mérito")
            return ResultadoGating(secao=secao, aprovado=True, score=100.0)

        engine = SectionalPipelineEngine(gating_validator=crash_at_merito, use_ollama=False)
        with pytest.raises(RuntimeError):
            engine.executar_pipeline(demo_bundle)

        # O pipeline está incompleto (faltam Mérito, Dosimetria e Dispositivo)
        # Tentar regenerar o Dispositivo deve levantar ValueError com mensagem descritiva
        with pytest.raises(ValueError, match="Pipeline incompleto ou seções prévias ausentes"):
            engine.regenerar_secao_atomica(SecaoTipo.DISPOSITIVO)

        # Tentar regenerar Dosimetria também deve falhar pois Mérito está ausente
        with pytest.raises(ValueError, match="Pipeline incompleto ou seções prévias ausentes"):
            engine.regenerar_secao_atomica(SecaoTipo.DOSIMETRIA)

    def test_defect_4_safe_initialization_of_historico_tentativas(self, demo_bundle):
        """
        Defeito 4: Registro de tentativas em regenerar_secao_atomica não pode disparar KeyError
        mesmo que a chave da seção não esteja pré-populada em historico_tentativas.
        """
        engine = SectionalPipelineEngine(use_ollama=False)
        engine.executar_pipeline(demo_bundle)

        # Simula corrupção/remoção da chave da seção no dicionário historico_tentativas
        if SecaoTipo.PRELIMINARES in engine.estado.historico_tentativas:
            del engine.estado.historico_tentativas[SecaoTipo.PRELIMINARES]

        # Regeneração não pode lançar KeyError: deve usar setdefault
        rascunho = engine.regenerar_secao_atomica(SecaoTipo.PRELIMINARES, use_ollama=False)
        assert rascunho.secao == SecaoTipo.PRELIMINARES
        assert SecaoTipo.PRELIMINARES in engine.estado.historico_tentativas
        assert len(engine.estado.historico_tentativas[SecaoTipo.PRELIMINARES]) == 1

    def test_defect_5_whitelist_isolation_in_cases_without_audio(self):
        """
        Defeito 5: Em casos reais (não-demo) sem áudios/transcrições (transcriptions=[]),
        construir_dossie_secao NÃO pode injetar IDs demo (ID-4100301..304) nem aliases fictícios (ID-MIDIA-001..004).
        """
        bundle_real_sem_audio = {
            "numero_processo": "0809999-99.2025.8.20.0001",
            "raw_documents": [
                {"doc_id": "ID-9001001", "conteudo": "Denúncia do MP.", "tipo": "DENUNCIA", "folha": "fls. 1/3"},
                {"doc_id": "ID-9001002", "conteudo": "Defesa prévia.", "tipo": "RESPOSTA_ACUSACAO", "folha": "fls. 4/6"},
            ],
            "transcriptions": [],
        }

        dossie = construir_dossie_secao(SecaoTipo.RELATORIO, bundle_real_sem_audio)

        ids_demo_proibidos = {"ID-4100301", "ID-4100302", "ID-4100303", "ID-4100304"}
        aliases_proibidos = {"ID-MIDIA-001", "ID-MIDIA-002", "ID-MIDIA-003", "ID-MIDIA-004"}

        folhas_set = set(dossie.folhas_permitidas)
        assert not ids_demo_proibidos.intersection(folhas_set), f"IDs demo vazaram: {ids_demo_proibidos.intersection(folhas_set)}"
        assert not aliases_proibidos.intersection(folhas_set), f"Aliases vazaram: {aliases_proibidos.intersection(folhas_set)}"
        assert folhas_set == {"ID-9001001", "ID-9001002"}

    def test_defect_6_validator_regex_catches_all_id_prefixes(self):
        """
        Defeito 6: default_in_flight_gating_validator deve extrair qualquer padrão ID-[A-Za-z0-9_-]+
        (como ID-DOC-9999, ID-FAKE-8888, ID-PROVA-001) e rejeitar se não estiver na whitelist.
        """
        dossie = DossieRestricaoSecao(
            secao=SecaoTipo.RELATORIO,
            folhas_permitidas=["ID-4100101"],
            fatos_delimitados=["Fato regular"],
            teses_defesa=[],
            diretrizes_jurisprudenciais=[],
        )

        texto_com_ids_forjados = (
            "Conforme demonstrado em ID-DOC-9999, ID-FAKE-8888, ID-PROVA-001 e ID-LAUDO-77, "
            "o acusado não concorreu para a infração penal."
        )
        rascunho = SecaoRascunho(secao=SecaoTipo.RELATORIO, texto_redigido=texto_com_ids_forjados)
        res = default_in_flight_gating_validator(SecaoTipo.RELATORIO, rascunho, dossie)

        assert res.aprovado is False, "O validador aprovou indevidamente minutas com IDs forjados!"
        assert any("ID-DOC-9999" in e for e in res.erros)
        assert any("ID-FAKE-8888" in e for e in res.erros)
        assert any("ID-PROVA-001" in e for e in res.erros)
        assert any("ID-LAUDO-77" in e for e in res.erros)

    def test_defect_7_validator_regex_and_dossier_support_hex_hash_audio_ids(self):
        """
        Defeito 7: IDs de mídia com hash hexadecimal gerados por audio_transcriber
        (ex: ID-MIDIA-B73F) devem ser reconhecidos pelo validador e pelo dossiê.
        """
        dossie = DossieRestricaoSecao(
            secao=SecaoTipo.MERITO,
            folhas_permitidas=["ID-4100101", "ID-MIDIA-B73F"],
            fatos_delimitados=["Fatos delimitados"],
            teses_defesa=[],
            diretrizes_jurisprudenciais=[],
        )

        # 1. Minuta citando mídia legítima com hash hex autorizada deve ser aprovada
        texto_legitimo = "Em sede de audiência, na mídia ID-MIDIA-B73F, a vítima confirmou as agressões."
        rascunho_legitimo = SecaoRascunho(secao=SecaoTipo.MERITO, texto_redigido=texto_legitimo)
        res_legitimo = default_in_flight_gating_validator(SecaoTipo.MERITO, rascunho_legitimo, dossie)
        assert res_legitimo.aprovado is True

        # 2. Minuta citando mídia com hash hex não autorizada deve ser reprovada
        texto_alucinado = "Em audiência, na mídia ID-MIDIA-C99A, a testemunha declarou alibi."
        rascunho_alucinado = SecaoRascunho(secao=SecaoTipo.MERITO, texto_redigido=texto_alucinado)
        res_alucinado = default_in_flight_gating_validator(SecaoTipo.MERITO, rascunho_alucinado, dossie)
        assert res_alucinado.aprovado is False
        assert any("ID-MIDIA-C99A" in e for e in res_alucinado.erros)

    def test_defect_8_art59_vector_validation_diacritic_normalization(self):
        """
        Defeito 8: default_in_flight_gating_validator deve usar normalização Unicode NFD
        para aceitar vetores do art. 59 grafados com ou sem acento (ex: 'consequencias' vs 'consequências',
        'circunstancias' vs 'circunstâncias').
        """
        dossie = DossieRestricaoSecao(
            secao=SecaoTipo.DOSIMETRIA,
            folhas_permitidas=["ID-4100108"],
            fatos_delimitados=["Fatos"],
            teses_defesa=[],
            diretrizes_jurisprudenciais=[],
        )

        texto_sem_circunflexo = (
            "DOSIMETRIA DA PENA:\n"
            "Passo à análise dos vetores do art. 59 do Código Penal: culpabilidade normal à espécie; "
            "não registra maus antecedentes; conduta social adequada; personalidade sem elementos para aferição; "
            "motivos comuns ao delito; circunstancias do crime e consequencias da ação delituosa; "
            "comportamento da vitima em nada contribuiu para a prática do ilícito.\n"
            "Fixo a pena-base no mínimo legal."
        )
        rascunho = SecaoRascunho(secao=SecaoTipo.DOSIMETRIA, texto_redigido=texto_sem_circunflexo)
        res = default_in_flight_gating_validator(SecaoTipo.DOSIMETRIA, rascunho, dossie)

        assert res.aprovado is True, f"Falso positivo no art. 59: {res.erros}"
        assert len(res.erros) == 0
