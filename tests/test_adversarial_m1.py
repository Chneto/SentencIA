"""
Suíte de Testes Adversariais e Estresse Empírico — Milestone 1 (F01 a F06).
Challenger 1 (Critic & Specialist): Verificação exaustiva de invariantes de máquina de estados,
imunidade a manipulação de buffer, exaustão de retentativas e conformidade estrita da auditoria.
"""

import copy
from typing import Any, Dict, List
import unittest.mock as mock
import pytest
import httpx

from app.demo.demo_case import get_demo_case_bundle
from app.generation.hallucination_auditor import auditar_sentenca
from app.ingestion.laya_classifier import classificar_pecas_autos, extrair_metadados_processo
from app.generation.sectional_pipeline import (
    DossieRestricaoSecao,
    ResultadoGating,
    SecaoRascunho,
    SecaoTipo,
    SectionalPipelineEngine,
    StatusSecao,
    default_in_flight_gating_validator,
)


# ==============================================================================
# FIXTURES
# ==============================================================================

@pytest.fixture
def demo_bundle() -> Dict[str, Any]:
    return get_demo_case_bundle()


# ==============================================================================
# 1. INVARIANTE: PREVENÇÃO DE EXECUÇÃO FORA DE ORDEM / STATE JUMPING
# ==============================================================================

class TestInvarianteOrdemExecucao:
    """Verifica se a máquina de estados impede salto de estados ou execução do Dispositivo sem seções prévias."""

    def test_impedir_regeneracao_antes_de_executar_pipeline(self):
        """Caller não pode invocar regeneração de qualquer seção (incluindo Dispositivo) sem pipeline executado."""
        engine = SectionalPipelineEngine(use_ollama=False)
        with pytest.raises(ValueError, match="Nenhum bundle processual ativo"):
            engine.regenerar_secao_atomica(SecaoTipo.DISPOSITIVO)

        with pytest.raises(ValueError, match="Nenhum bundle processual ativo"):
            engine.regenerar_secao_atomica(SecaoTipo.RELATORIO)

    def test_ordem_estrita_execucao_seccional(self, demo_bundle):
        """Garante que o pipeline percorre estritamente a ordem canônica: RELATORIO -> PRELIMINARES -> MERITO -> DOSIMETRIA -> DISPOSITIVO."""
        ordem_observada: List[SecaoTipo] = []

        def tracking_validator(secao: SecaoTipo, rascunho: SecaoRascunho, dossie: DossieRestricaoSecao) -> ResultadoGating:
            ordem_observada.append(secao)
            return default_in_flight_gating_validator(secao, rascunho, dossie)

        engine = SectionalPipelineEngine(gating_validator=tracking_validator, use_ollama=False)
        sentenca = engine.executar_pipeline(demo_bundle)

        ordem_esperada = [
            SecaoTipo.RELATORIO,
            SecaoTipo.PRELIMINARES,
            SecaoTipo.MERITO,
            SecaoTipo.DOSIMETRIA,
            SecaoTipo.DISPOSITIVO,
        ]
        assert ordem_observada == ordem_esperada, f"Ordem observada violou a sequência canônica: {ordem_observada}"
        assert sentenca is not None

    def test_dispositivo_sempre_recebe_contexto_das_quatro_secoes_anteriores(self, demo_bundle):
        """Garante que a última seção (Dispositivo) tem acesso às 4 seções prévias seladas."""
        secoes_no_contexto_dispositivo: List[str] = []

        def inspector_validator(secao: SecaoTipo, rascunho: SecaoRascunho, dossie: DossieRestricaoSecao) -> ResultadoGating:
            if secao == SecaoTipo.DISPOSITIVO:
                secoes_seladas_atuais = list(engine.estado.secoes_seladas.keys())
                secoes_no_contexto_dispositivo.extend([s.value for s in secoes_seladas_atuais])
            return default_in_flight_gating_validator(secao, rascunho, dossie)

        engine = SectionalPipelineEngine(gating_validator=inspector_validator, use_ollama=False)
        engine.executar_pipeline(demo_bundle)

        assert "relatorio" in secoes_no_contexto_dispositivo
        assert "preliminares" in secoes_no_contexto_dispositivo
        assert "merito" in secoes_no_contexto_dispositivo
        assert "dosimetria" in secoes_no_contexto_dispositivo
        assert "dispositivo" not in secoes_no_contexto_dispositivo

    def test_bloqueio_salto_para_dispositivo_se_secoes_previas_ausentes(self, demo_bundle):
        """Adversarial: simula crash mid-stream (no Mérito) e tenta gerar Dispositivo sem Mérito e Dosimetria."""
        def crash_on_merito(secao, rascunho, dossie):
            if secao == SecaoTipo.MERITO:
                raise RuntimeError("Crash intencional em Mérito")
            return ResultadoGating(secao=secao, aprovado=True, score=100.0)

        engine = SectionalPipelineEngine(gating_validator=crash_on_merito, use_ollama=False)
        with pytest.raises(RuntimeError):
            engine.executar_pipeline(demo_bundle)

        # O engine deveria proibir a regeneração do Dispositivo enquanto o pipeline estiver incompleto
        with pytest.raises(ValueError, match="Pipeline incompleto ou seções prévias ausentes"):
            engine.regenerar_secao_atomica(SecaoTipo.DISPOSITIVO)


# ==============================================================================
# 2. INVARIANTE: IMUTABILIDADE DO BUFFER DE MEMÓRIA SELADA
# ==============================================================================

class TestInvarianteImutabilidadeBuffer:
    """Verifica se o buffer de seções seladas permanece inviolável e imutável contra corrupção externa ou downstream."""

    def test_secoes_seladas_nao_sao_sobrescritas_durante_execucao(self, demo_bundle):
        """Garante que o texto selado de uma seção anterior não é alterado durante o processamento das seguintes."""
        captura_relatorio: List[str] = []

        def spy_validator(secao: SecaoTipo, rascunho: SecaoRascunho, dossie: DossieRestricaoSecao) -> ResultadoGating:
            if SecaoTipo.RELATORIO in engine.estado.secoes_seladas:
                captura_relatorio.append(engine.estado.secoes_seladas[SecaoTipo.RELATORIO])
            return default_in_flight_gating_validator(secao, rascunho, dossie)

        engine = SectionalPipelineEngine(gating_validator=spy_validator, use_ollama=False)
        engine.executar_pipeline(demo_bundle)

        assert len(captura_relatorio) >= 4
        for snapshot in captura_relatorio[1:]:
            assert snapshot == captura_relatorio[0], "O buffer selado de Relatório sofreu mutação in-flight!"

    def test_regeneracao_atomica_nao_altera_outras_secoes_seladas(self, demo_bundle):
        """A regeneração de uma seção específica não pode alterar nenhuma outra seção já selada."""
        engine = SectionalPipelineEngine(use_ollama=False)
        sentenca_original = engine.executar_pipeline(demo_bundle)

        snapshot_antes = copy.deepcopy(engine.estado.secoes_seladas)
        assert sentenca_original.secao_relatorio == snapshot_antes[SecaoTipo.RELATORIO]

        rascunho_novo = engine.regenerar_secao_atomica(SecaoTipo.PRELIMINARES, feedback="Reforçar ausência de prescrição")

        assert rascunho_novo.secao == SecaoTipo.PRELIMINARES
        assert engine.estado.secoes_seladas[SecaoTipo.RELATORIO] == snapshot_antes[SecaoTipo.RELATORIO]
        assert engine.estado.secoes_seladas[SecaoTipo.MERITO] == snapshot_antes[SecaoTipo.MERITO]
        assert engine.estado.secoes_seladas[SecaoTipo.DOSIMETRIA] == snapshot_antes[SecaoTipo.DOSIMETRIA]
        assert engine.estado.secoes_seladas[SecaoTipo.DISPOSITIVO] == snapshot_antes[SecaoTipo.DISPOSITIVO]

    def test_manipulacao_externa_do_bundle_nao_afeta_estado_ja_gerado(self, demo_bundle):
        """Mutar o bundle original após a execução não pode retroagir no estado do engine."""
        bundle_copia = copy.deepcopy(demo_bundle)
        engine = SectionalPipelineEngine(use_ollama=False)
        sentenca = engine.executar_pipeline(bundle_copia)

        bundle_copia["raw_documents"] = []
        bundle_copia["numero_processo"] = "PROCESSO-HACKEADO"

        assert engine.estado.secoes_seladas[SecaoTipo.RELATORIO] == sentenca.secao_relatorio
        assert "PROCESSO-HACKEADO" not in sentenca.texto_completo


# ==============================================================================
# 3. INVARIANTE: EXAUSTÃO DE RETRIES E FALLBACK DETERMINÍSTICO
# ==============================================================================

class TestInvarianteExaustaoRetriesEFallback:
    """Verifica se 3 rejeições consecutivas acionam o fallback determinístico sem crashar."""

    def test_tres_rejeicoes_consecutivas_acionam_fallback_determinístico_sem_crash(self, demo_bundle):
        """Rejeição nas tentativas 1, 2 e 3 (max_retries=2) deve ativar fallback determinístico para aquela seção."""
        contagem_tentativas: Dict[SecaoTipo, int] = {s: 0 for s in SecaoTipo}

        def reject_merito_validator(secao: SecaoTipo, rascunho: SecaoRascunho, dossie: DossieRestricaoSecao) -> ResultadoGating:
            contagem_tentativas[secao] += 1
            if secao == SecaoTipo.MERITO:
                return ResultadoGating(
                    secao=secao,
                    aprovado=False,
                    score=20.0,
                    erros=["Alucinação artificial induzida para teste de estresse"],
                    feedbacks_cirurgicos=["Reescreva observando as folhas permitidas"],
                    acionar_fallback=False,
                )
            return default_in_flight_gating_validator(secao, rascunho, dossie)

        engine = SectionalPipelineEngine(gating_validator=reject_merito_validator, use_ollama=False, max_retries=2)
        sentenca = engine.executar_pipeline(demo_bundle)

        assert contagem_tentativas[SecaoTipo.MERITO] == 3
        assert engine.estado.status_secoes[SecaoTipo.MERITO] == StatusSecao.FALLBACK_ACIONADO
        assert len(engine.estado.historico_tentativas[SecaoTipo.MERITO]) == 3

        texto_merito = engine.estado.secoes_seladas[SecaoTipo.MERITO]
        assert "Materialidade, Autoria" in texto_merito
        assert "Lei nº 11.340/2006" in texto_merito
        assert sentenca is not None

    def test_rejeicao_total_em_todas_as_cinco_secoes_converte_100_porcento_para_fallback(self, demo_bundle):
        """Se todas as 5 seções falharem em todas as tentativas, o engine deve concluir 100% via fallback sem crash."""
        def reject_all_validator(secao: SecaoTipo, rascunho: SecaoRascunho, dossie: DossieRestricaoSecao) -> ResultadoGating:
            return ResultadoGating(
                secao=secao,
                aprovado=False,
                score=0.0,
                erros=["Falha simulada universal"],
                feedbacks_cirurgicos=["Tente novamente"],
                acionar_fallback=False,
            )

        engine = SectionalPipelineEngine(gating_validator=reject_all_validator, use_ollama=False, max_retries=2)
        sentenca = engine.executar_pipeline(demo_bundle)

        assert sentenca is not None
        for secao in SecaoTipo:
            assert engine.estado.status_secoes[secao] == StatusSecao.FALLBACK_ACIONADO
            assert len(engine.estado.historico_tentativas[secao]) == 3
            assert len(engine.estado.secoes_seladas[secao]) > 100

    def test_acionamento_imediato_de_fallback_por_bandeira_curto_circuito(self, demo_bundle):
        """Se o validador sinalizar acionar_fallback=True na tentativa 1, não deve rodar tentativas 2 e 3."""
        def short_circuit_validator(secao: SecaoTipo, rascunho: SecaoRascunho, dossie: DossieRestricaoSecao) -> ResultadoGating:
            if secao == SecaoTipo.DOSIMETRIA:
                return ResultadoGating(
                    secao=secao,
                    aprovado=False,
                    score=0.0,
                    erros=["Violação gravíssima detectada: acionamento imediato de contingência"],
                    feedbacks_cirurgicos=[],
                    acionar_fallback=True,
                )
            return default_in_flight_gating_validator(secao, rascunho, dossie)

        engine = SectionalPipelineEngine(gating_validator=short_circuit_validator, use_ollama=False, max_retries=2)
        sentenca = engine.executar_pipeline(demo_bundle)

        assert len(engine.estado.historico_tentativas[SecaoTipo.DOSIMETRIA]) == 1
        assert engine.estado.status_secoes[SecaoTipo.DOSIMETRIA] == StatusSecao.FALLBACK_ACIONADO
        assert sentenca.secao_dosimetria is not None

    def test_regenerar_secao_atomica_deve_executar_gating_validator(self, demo_bundle):
        """Adversarial: regenerar_secao_atomica com Ollama deve passar pelo gating_validator antes de selar."""
        validator_executado = False

        def strict_audit(secao, rascunho, dossie):
            nonlocal validator_executado
            validator_executado = True
            return ResultadoGating(secao=secao, aprovado=False, score=0.0, erros=["Texto alucinado rejeitado"])

        engine = SectionalPipelineEngine(gating_validator=strict_audit, use_ollama=True)
        engine.executar_pipeline(demo_bundle, use_ollama=False)

        with mock.patch("httpx.Client.post") as mock_post:
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"response": "Texto alucinado com ID-99999 e Súmula 9999"}
            mock_post.return_value = mock_resp

            validator_executado = False
            engine.regenerar_secao_atomica(SecaoTipo.RELATORIO, use_ollama=True)

            assert validator_executado is True, "gating_validator foi completamente burlado em regenerar_secao_atomica!"
            assert "ID-99999" not in engine.estado.secoes_seladas[SecaoTipo.RELATORIO]

    def test_regenerar_secao_atomica_atualiza_status_secoes(self, demo_bundle):
        """Adversarial: regenerar_secao_atomica deve atualizar engine.estado.status_secoes[secao]."""
        def reject_relatorio(secao, rascunho, dossie):
            if secao == SecaoTipo.RELATORIO:
                return ResultadoGating(secao=secao, aprovado=False, score=0.0, erros=["Falha"])
            return default_in_flight_gating_validator(secao, rascunho, dossie)

        engine = SectionalPipelineEngine(gating_validator=reject_relatorio, use_ollama=False)
        engine.executar_pipeline(demo_bundle)

        assert engine.estado.status_secoes[SecaoTipo.RELATORIO] == StatusSecao.FALLBACK_ACIONADO

        # Agora regenera com sucesso determinístico
        engine.regenerar_secao_atomica(SecaoTipo.RELATORIO, use_ollama=False)

        # O status em status_secoes deve ter sido sincronizado para APROVADO
        assert engine.estado.status_secoes[SecaoTipo.RELATORIO] == StatusSecao.APROVADO


# ==============================================================================
# 4. INVARIANTE: CONFORMIDADE DA SENTENÇA DE FALLBACK COM O AUDITOR ANTI-ALUCINAÇÃO
# ==============================================================================

class TestInvarianteConformidadeAuditorFallback:
    """Verifica se a sentença gerada exclusivamente por Fallback obtém aprovação integral no auditor."""

    def test_sentenca_100_porcento_fallback_obtem_score_100_no_auditor(self, demo_bundle):
        """A sentença montada exclusivamente a partir dos fallbacks determinísticos deve passar com 100% no auditor."""
        def reject_all_validator(secao: SecaoTipo, rascunho: SecaoRascunho, dossie: DossieRestricaoSecao) -> ResultadoGating:
            return ResultadoGating(
                secao=secao,
                aprovado=False,
                score=0.0,
                erros=["Forçar fallback total"],
                feedbacks_cirurgicos=[],
                acionar_fallback=False,
            )

        engine = SectionalPipelineEngine(gating_validator=reject_all_validator, use_ollama=False, max_retries=2)
        sentenca = engine.executar_pipeline(demo_bundle)

        pecas = classificar_pecas_autos(demo_bundle["raw_documents"], is_demo=True)
        meta = extrair_metadados_processo(pecas, is_demo=True)

        relatorio_audit = auditar_sentenca(
            texto_sentenca=sentenca.texto_completo,
            pecas_autos=pecas,
            teses_defesa=meta.teses_defesa,
        )

        assert relatorio_audit.aprovada_sem_alucinacao is True, (
            f"Sentença de fallback reprovou no auditor! Detalhes: {relatorio_audit.detalhes_camadas}"
        )
        assert relatorio_audit.score_confiabilidade == 100.0
        assert len(relatorio_audit.ids_inexistentes) == 0
        assert len(relatorio_audit.citacoes_nao_autorizadas) == 0
        assert len(relatorio_audit.teses_omitidas) == 0
        assert relatorio_audit.conformidade_art381_cpp is True
        assert relatorio_audit.conformidade_art21_lmp is True
        assert relatorio_audit.conformidade_lc95_98 is True
        assert relatorio_audit.conformidade_datas_padrao_br is True
        assert relatorio_audit.conformidade_abnt_citacoes is True
        assert relatorio_audit.conformidade_estilo_cnj_amb is True
        assert relatorio_audit.conformidade_oito_circunstancias_art59 is True

    def test_fallback_com_custom_dosimetria_override_preserva_score_100(self, demo_bundle):
        """Verifica se parâmetros customizados na dosimetria preservam nota 100.0 no auditor."""
        override = {
            "criterio_primeira_fase": "um_oitavo_intervalo",
            "culpabilidade_lesao": True,
            "motivos_lesao": True,
            "reincidente": False,
            "valor_dano_moral": 15000.0,
        }
        engine = SectionalPipelineEngine(use_ollama=False)
        sentenca = engine.executar_pipeline(demo_bundle, custom_dosimetria_override=override)

        pecas = classificar_pecas_autos(demo_bundle["raw_documents"], is_demo=True)
        meta = extrair_metadados_processo(pecas, is_demo=True)

        relatorio_audit = auditar_sentenca(
            texto_sentenca=sentenca.texto_completo,
            pecas_autos=pecas,
            teses_defesa=meta.teses_defesa,
        )

        assert relatorio_audit.aprovada_sem_alucinacao is True
        assert relatorio_audit.score_confiabilidade == 100.0


# ==============================================================================
# 5. ESTRESSE: RESILIÊNCIA A EXCEÇÕES E ENTRADAS CORROMPIDAS
# ==============================================================================

class TestEstresseResilienciaExcecoes:
    """Verifica robustez sob condições adversas de entrada e exceções de rede/infra."""

    def test_bundle_com_campos_ausentes_ou_tipo_incorreto(self):
        """Entradas que não sejam dict devem lançar ValueError imediatamente."""
        engine = SectionalPipelineEngine(use_ollama=False)
        with pytest.raises(ValueError, match="deve ser um dicionário"):
            engine.executar_pipeline(None)  # type: ignore

        with pytest.raises(ValueError, match="deve ser um dicionário"):
            engine.executar_pipeline("string_invalida")  # type: ignore

    def test_bundle_com_raw_documents_vazio_ou_nulo(self):
        """Bundle sem raw_documents ou com lista vazia deve lançar erro explícito."""
        engine = SectionalPipelineEngine(use_ollama=False)
        with pytest.raises(KeyError, match="raw_documents"):
            engine.executar_pipeline({"case_id": "SEM_RAW"})

        with pytest.raises(ValueError, match="está vazia"):
            engine.executar_pipeline({"case_id": "VAZIO", "raw_documents": []})

    def test_ollama_com_excecao_de_rede_nao_interrompe_pipeline(self, demo_bundle):
        """Se o Ollama lançar falha de rede ou timeout (httpx.TimeoutException), o pipeline recupera via fallback."""
        def mock_post_raise(*args, **kwargs):
            raise httpx.ConnectTimeout("Timeout de 90s excedido na chamada ao Ollama")

        engine = SectionalPipelineEngine(use_ollama=True)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(httpx.Client, "post", mock_post_raise)
            sentenca = engine.executar_pipeline(demo_bundle, use_ollama=True)
            assert sentenca is not None
            assert len(sentenca.texto_completo) > 1000
