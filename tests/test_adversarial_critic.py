# -*- coding: utf-8 -*-
"""
Suíte de Testes Abrangente — Agente 7 (Adversarial Critic / Duplo Crivo)
Milestone 2 do Projeto SentencIA.

Cobre 100% dos requisitos especificados nas Features:
- F07: Matriz de Corroboração Oral (depoimentos vs laudos)
- F08: Extrator Determinístico de Teses Defensivas (14 teses canônicas)
- F09: Adversarial Judicial Critic LLM & Ladder de Resiliência JSON
- F10: Schemas Pydantic ItemCritica e RelatorioCriticaAdversarial
- F11: Trava Mandatória de Omissão de Teses Defensivas (Art. 381, III, CPP)
- F12: Standard Probatório e Protocolo de Gênero (Resolução CNJ nº 492/2023)
- Dosimetria (Art. 59, Súmulas 231 e 444 STJ, Vedação ao Bis in Idem)
- Dispositivo (Comando condenatório, Art. 21 LMP)
- Fallback Determinístico de <5ms (Zero Stalling)
- Integração de Gating com SectionalPipelineEngine (M1 ↔ M2)
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from app.generation.adversarial_critic import (
    AdversarialJudicialCritic,
    GravidadeCritica,
    ItemCritica,
    RelatorioCriticaAdversarial,
    TipoCritica,
    criar_gating_validator_adversarial,
    criticar_secao,
    extrair_teses_defensivas,
)
from app.generation.sectional_pipeline import (
    DossieRestricaoSecao,
    ResultadoGating,
    SecaoRascunho,
    SecaoTipo,
    SectionalPipelineEngine,
)
from app.ingestion.laya_classifier import PecaClassificada, TeseDefensivaItem
from app.transcription.oral_proof_analyzer import DepoenteAnaliseOral, MatrizCorroboracaoOral

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


# ==============================================================================
# 1. TestPydanticModelsAndValidation (F10)
# ==============================================================================


class TestPydanticModelsAndValidation:
    """Testa contratos Pydantic, normalizadores de campo, enums e conversões."""

    def test_item_critica_valid_creation(self):
        item = ItemCritica(
            tipo="omissao_tese",
            descricao="Tese defensiva MER-01 omitida na fundamentação.",
            paragrafo_afetado="Parágrafo 2",
            gravidade="critica",
            feedback_cirurgico="Enfrente expressamente a tese de in dubio pro reo.",
        )
        assert item.tipo == TipoCritica.OMISSAO_TESE.value
        assert item.gravidade == GravidadeCritica.CRITICA.value
        assert item.paragrafo_afetado == "Parágrafo 2"

    def test_item_critica_normalizacao_aliases_tipo(self):
        # Testa normalização de aliases para tipo
        aliases = [
            ("omissao_tese_defesa", "omissao_tese"),
            ("omissao", "omissao_tese"),
            ("coerencia", "dialetica"),
            ("probatario", "standard_probatorio"),
            ("genero", "genero_cnj492"),
            ("cnj492", "genero_cnj492"),
            ("incoerencia", "contradicao"),
        ]
        for alias, esperado in aliases:
            item = ItemCritica(
                tipo=alias,
                descricao="Teste de normalização",
                gravidade="moderada",
                feedback_cirurgico="Corrija o ponto.",
            )
            assert item.tipo == esperado

    def test_item_critica_normalizacao_aliases_gravidade(self):
        # Testa normalização de aliases para gravidade
        aliases = [
            ("grave", "critica"),
            ("bloqueio", "critica"),
            ("alta", "critica"),
            ("media", "moderada"),
            ("leve", "sugestao"),
            ("recomendacao", "sugestao"),
        ]
        for alias, esperado in aliases:
            item = ItemCritica(
                tipo="dialetica",
                descricao="Teste",
                gravidade=alias,
                feedback_cirurgico="Ajuste",
            )
            assert item.gravidade == esperado

    def test_item_critica_invalid_tipo_raises_error(self):
        with pytest.raises(ValidationError):
            ItemCritica(
                tipo="tipo_totalmente_invalido_xyz",
                descricao="Teste",
                gravidade="critica",
                feedback_cirurgico="Ajuste",
            )

    def test_relatorio_critica_bounds_and_methods(self):
        # Score deve estar entre 0.0 e 100.0
        with pytest.raises(ValidationError):
            RelatorioCriticaAdversarial(
                aprovado=False,
                score_dialetico=120.0,
                itens=[],
                parecer_sintetico="Score inválido",
            )

        with pytest.raises(ValidationError):
            RelatorioCriticaAdversarial(
                aprovado=False,
                score_dialetico=-10.0,
                itens=[],
                parecer_sintetico="Score negativo",
            )

        item_critico = ItemCritica(
            tipo="omissao_tese",
            descricao="Falha grave",
            gravidade="critica",
            feedback_cirurgico="Reescrever parágrafo 1",
        )
        relatorio = RelatorioCriticaAdversarial(
            aprovado=False,
            score_dialetico=70.0,
            itens=[item_critico],
            parecer_sintetico="Minuta rejeitada",
        )
        assert relatorio.tem_falha_critica() is True
        assert relatorio.extrair_feedbacks_cirurgicos() == ["Reescrever parágrafo 1"]

    def test_to_resultado_gating_conversion(self):
        item_critico = ItemCritica(
            tipo="omissao_tese",
            descricao="Tese omitida",
            gravidade="critica",
            feedback_cirurgico="Enfrentar PREL-01",
        )
        relatorio = RelatorioCriticaAdversarial(
            aprovado=False,
            score_dialetico=70.0,
            itens=[item_critico],
            parecer_sintetico="Reprovado",
        )
        gating = relatorio.to_resultado_gating(SecaoTipo.PRELIMINARES)
        assert isinstance(gating, ResultadoGating)
        assert gating.secao == SecaoTipo.PRELIMINARES
        assert gating.aprovado is False
        assert gating.score == 70.0
        assert len(gating.erros) == 1
        assert "[OMISSAO_TESE] Tese omitida" in gating.erros[0]
        assert gating.feedbacks_cirurgicos == ["Enfrentar PREL-01"]


# ==============================================================================
# 2. TestF08ExtratorTesesDefensivas (F08)
# ==============================================================================


class TestF08ExtratorTesesDefensivas:
    """Testa a extração determinística das 14 teses canônicas a partir de peças defensivas."""

    def test_extracao_preliminares_canônicas(self):
        conteudo_peca = """
        ALEGAÇÕES FINAIS DA DEFESA TÉCNICA
        1. PRELIMINARMENTE:
        a) Da inépcia da denúncia e falta de justa causa (art. 41 e art. 395, I e III do CPP);
        b) Da nulidade da prova material ante a ausência de laudo pericial válido nos termos do art. 158 do CPP;
        c) Da extinção da punibilidade pela decadência do direito de representação (art. 38 do CPP e art. 107, IV do CP);
        d) Da incompetência do juízo da violência doméstica por ausência de motivação de gênero (art. 5º da Lei 11.340/06).
        """
        pecas = [
            PecaClassificada(
                doc_id="ID-DEF-01",
                folha="fls. 40-50",
                titulo="Memoriais da Defesa",
                data_peca="10/05/2024",
                fase="INSTRUCAO",
                categoria="ALEGACOES_FINAIS_DEFESA",
                laya_confidence=0.98,
                resumo_estruturado="Memoriais defensivos com preliminares",
                conteudo=conteudo_peca,
            )
        ]
        teses = extrair_teses_defensivas(pecas)
        codigos = [t.codigo for t in teses]
        assert "PREL-01" in codigos
        assert "PREL-02" in codigos
        assert "PREL-03" in codigos
        assert "PREL-04" in codigos

    def test_extracao_merito_e_subsidiarias(self):
        conteudo_peca = """
        RESPOSTA À ACUSAÇÃO (Art. 396-A CPP)
        No mérito, pugna pela absolvição por:
        - fragilidade probatória e in dubio pro reo (art. 386, VII, CPP);
        - negativa de autoria, comprovando que o réu não praticou o delito;
        - legítima defesa perante agressões recíprocas iniciadas pela ofendida (art. 25 CP);
        - atipicidade da ameaça proferida em estado de ânimo exaltado e cólera passageira;
        - ausência de dolo quanto ao descumprimento de MPU, haja vista o chamado pela própria vítima;
        - desclassificação para a contravenção de vias de fato (art. 21 LCP);
        - atipicidade da perseguição por falta de habitualidade e atos reiterados;
        - inexigibilidade de conduta diversa decorrente de urgência médica do filho menor.
        Subsidiariamente:
        - fixação da pena-base no mínimo legal e incidência da confissão espontânea;
        - substituição por restritiva de direitos e sursis da pena (art. 77 CP), além do afastamento do dano moral.
        """
        pecas = [{"categoria": "RESPOSTA_ACUSACAO", "conteudo": conteudo_peca}]
        teses = extrair_teses_defensivas(pecas)
        codigos = [t.codigo for t in teses]

        assert "MER-01" in codigos
        assert "MER-02" in codigos
        assert "MER-03" in codigos
        assert "MER-04" in codigos
        assert "MER-05" in codigos
        assert "MER-06" in codigos
        assert "MER-07" in codigos
        assert "MER-08" in codigos
        assert "SUB-01" in codigos
        assert "SUB-02" in codigos

    def test_extracao_peca_generica_nao_injeta_mer01(self):
        # TASK-041: peça defensiva sem tese reconhecida NÃO injeta MER-01 artificialmente.
        conteudo_generico = "A Defesa vem manifestar-se nos termos da lei e requerer o que for de direito."
        pecas = [{"categoria": "ALEGACOES_FINAIS_DEFESA", "conteudo": conteudo_generico, "doc_id": "ID-DEF-GEN"}]
        teses = extrair_teses_defensivas(pecas)
        assert teses == []

    def test_extracao_vazia(self):
        assert extrair_teses_defensivas([]) == []

    def test_extracao_teses_todos_cinco_casos_fixtures(self):
        """Valida que extrair_teses_defensivas opera com sucesso em todos os 5 arquivos de cenários."""
        cenarios = [
            FIXTURES_DIR / "case_scenario_1_ameaca_lesao.json",
            FIXTURES_DIR / "case_scenario_2_descumprimento_mpu.json",
            FIXTURES_DIR / "case_scenario_3_lesao_palavra_vitima.json",
            FIXTURES_DIR / "case_scenario_4_multirreincidente_sumula444.json",
            FIXTURES_DIR / "case_scenario_5_autocorrection_fallback.json",
        ]
        for cenario_path in cenarios:
            with open(cenario_path, "r", encoding="utf-8") as f:
                bundle = json.load(f)
            teses = extrair_teses_defensivas(bundle)
            assert isinstance(teses, list)
            assert len(teses) >= 1
            for t in teses:
                assert isinstance(t, TeseDefensivaItem)
                assert t.codigo
                assert t.tipo in ("PRELIMINAR", "MERITO", "SUBSIDIARIA")


# ==============================================================================
# 3. TestF07MatrizCorroboracaoOral (F07)
# ==============================================================================


class TestF07MatrizCorroboracaoOral:
    """Testa o confronto entre depoimentos orais e laudos médicos/periciais sob a Lei 11.340/06."""

    @pytest.fixture
    def matriz_caso_3_laudo_inconclusivo(self):
        # Simula o Caso 3: Laudo IML tardio inconclusivo, mas UPA e fotos atestam escoriações
        depoente = DepoenteAnaliseOral(
            media_id="ID-MIDIA-30",
            arquivo="audiencia_renata.mp4",
            depoente="Renata Silveira",
            papel="OFENDIDA",
            duracao="12:00",
            segmentos_literais=[],
            discurso_indireto_sentenca="Declarou ter sido agredida com socos e puxões de cabelo.",
            relato_fase_policial="Declarou agressões físicas",
            coerencia_delegacia_juizo="ALTA_COERENCIA",
            corroboracao_pericial_documental="Laudo pericial IML inconclusivo por decurso de prazo (realizado 16 dias após o fato). Prontuário de atendimento médico de emergência na UPA e fotografias (ID-2207803) atestam hematomas e escoriações.",
        )
        return MatrizCorroboracaoOral(
            perspectiva_genero_aplicada=True,
            protocolo_cnj="Resolução CNJ nº 492/2023",
            depoentes=[depoente],
            fundamentacao_sintese="Palavra da vítima harmônica com prontuário UPA e fotografias.",
        )

    def test_deteccao_contradicao_laudo_direto_positivo_falso(self, matriz_caso_3_laudo_inconclusivo):
        critic = AdversarialJudicialCritic(use_ollama=False)
        rascunho_com_contradicao = SecaoRascunho(
            secao=SecaoTipo.MERITO,
            texto_redigido=(
                "A materialidade do crime de lesão corporal restou cabalmente demonstrada, "
                "pois o laudo pericial direto comprovou as equimoses e lesões corporais suportadas pela vítima. "
                "Rejeito a tese de in dubio pro reo e condeno o réu."
            ),
        )
        dossie = DossieRestricaoSecao(
            secao=SecaoTipo.MERITO,
            folhas_permitidas=["ID-2207803"],
            fatos_delimitados=["Agressão em 02/03/2024"],
            teses_defesa=["MER-01: In dubio pro reo"],
            diretrizes_jurisprudenciais=[],
        )
        relatorio = critic.criticar_secao(rascunho_com_contradicao, dossie, matriz_oral=matriz_caso_3_laudo_inconclusivo)
        assert relatorio.aprovado is False
        assert relatorio.tem_falha_critica() is True
        tipos = [item.tipo for item in relatorio.itens]
        assert "contradicao" in tipos

    def test_deteccao_erro_standard_absolvicao_por_falta_laudo_direto(self, matriz_caso_3_laudo_inconclusivo):
        critic = AdversarialJudicialCritic(use_ollama=False)
        rascunho_absolv_erronea = SecaoRascunho(
            secao=SecaoTipo.MERITO,
            texto_redigido=(
                "Em que pese o relato da ofendida, absolvo o réu por falta de laudo direto conclusivo. "
                "A ausência de exame de corpo de delito direto enseja a absolvição do denunciado."
            ),
        )
        dossie = DossieRestricaoSecao(
            secao=SecaoTipo.MERITO,
            folhas_permitidas=["ID-2207803"],
            fatos_delimitados=["Agressão"],
            teses_defesa=[],
            diretrizes_jurisprudenciais=[],
        )
        relatorio = critic.criticar_secao(rascunho_absolv_erronea, dossie, matriz_oral=matriz_caso_3_laudo_inconclusivo)
        assert relatorio.aprovado is False
        tipos = [item.tipo for item in relatorio.itens]
        assert "standard_probatorio" in tipos

    def test_aprovacao_fundamentacao_correta_prova_indireta(self, matriz_caso_3_laudo_inconclusivo):
        critic = AdversarialJudicialCritic(use_ollama=False)
        rascunho_correto = SecaoRascunho(
            secao=SecaoTipo.MERITO,
            texto_redigido=(
                "No tocante à materialidade, conquanto o laudo pericial direto tenha restado inconclusivo "
                "pelo decurso de 16 dias entre o fato e a perícia, a materialidade delitiva erige-se induvidosa "
                "pela prova indireta, consubstanciada no prontuário médico de atendimento de urgência da UPA e "
                "nas fotografias datadas (ID-2207803), plenamente válidos nos termos do art. 167 do CPP c/c "
                "art. 12, § 3º, da Lei nº 11.340/2006. Rejeito as teses de in dubio pro reo e insuficiência probatória, "
                "condenando o acusado ante a harmonia da palavra da ofendida com os elementos periciais indiretos."
            ),
        )
        dossie = DossieRestricaoSecao(
            secao=SecaoTipo.MERITO,
            folhas_permitidas=["ID-2207803"],
            fatos_delimitados=["Agressão"],
            teses_defesa=["MER-01: In dubio pro reo e insuficiência probatória"],
            diretrizes_jurisprudenciais=[],
        )
        relatorio = critic.criticar_secao(rascunho_correto, dossie, matriz_oral=matriz_caso_3_laudo_inconclusivo)
        assert relatorio.aprovado is True
        assert relatorio.score_dialetico >= 80.0
        assert not relatorio.tem_falha_critica()


# ==============================================================================
# 4. TestF11TravaOmissaoTeses (F11)
# ==============================================================================


class TestF11TravaOmissaoTeses:
    """Testa o cumprimento estrito do art. 381, III, do CPP quanto à trava de omissão."""

    def test_omissao_tese_preliminar_bloqueia_minuta(self):
        critic = AdversarialJudicialCritic(use_ollama=False)
        rascunho = SecaoRascunho(
            secao=SecaoTipo.PRELIMINARES,
            texto_redigido=(
                "Passo à análise das preliminares. Rejeito a preliminar de inépcia da denúncia "
                "(PREL-01), pois a exordial descreve suficientemente a conduta típica imputada ao réu."
            ),
        )
        # Dossiê exige PREL-01 e PREL-02
        dossie = DossieRestricaoSecao(
            secao=SecaoTipo.PRELIMINARES,
            folhas_permitidas=[],
            fatos_delimitados=[],
            teses_defesa=[
                "PREL-01: Inépcia da denúncia",
                "PREL-02: Nulidade da prova pericial por ausência de laudo do art. 158 do CPP",
            ],
            diretrizes_jurisprudenciais=[],
        )
        relatorio = critic.criticar_secao(rascunho, dossie)
        assert relatorio.aprovado is False
        assert relatorio.tem_falha_critica() is True
        # Verifica se apontou omissão de PREL-02
        erros_omissao = [i for i in relatorio.itens if i.tipo == "omissao_tese"]
        assert len(erros_omissao) >= 1
        assert "PREL-02" in erros_omissao[0].descricao or "laudo" in erros_omissao[0].descricao.lower()
        assert "PREL-02" in relatorio.extrair_feedbacks_cirurgicos()[0]

    def test_omissao_tese_merito_bloqueia_minuta(self):
        critic = AdversarialJudicialCritic(use_ollama=False)
        rascunho = SecaoRascunho(
            secao=SecaoTipo.MERITO,
            texto_redigido=(
                "Quanto ao mérito, a autoria e a materialidade restaram comprovadas. "
                "Afasto a tese de insuficiência probatória (MER-01), restando certa a prática delitiva."
            ),
        )
        dossie = DossieRestricaoSecao(
            secao=SecaoTipo.MERITO,
            folhas_permitidas=[],
            fatos_delimitados=[],
            teses_defesa=[
                "MER-01: Insuficiência probatória / in dubio pro reo",
                "MER-08: Inexigibilidade de conduta diversa decorrente de urgência médica",
            ],
            diretrizes_jurisprudenciais=[],
        )
        relatorio = critic.criticar_secao(rascunho, dossie)
        assert relatorio.aprovado is False
        erros_omissao = [i for i in relatorio.itens if i.tipo == "omissao_tese"]
        assert len(erros_omissao) >= 1
        assert "MER-08" in erros_omissao[0].descricao

    def test_todas_teses_enfrentadas_aprova(self):
        critic = AdversarialJudicialCritic(use_ollama=False)
        rascunho = SecaoRascunho(
            secao=SecaoTipo.MERITO,
            texto_redigido=(
                "No mérito, rejeito a tese de insuficiência probatória (MER-01), porquanto as provas "
                "orais e documentais atestam de forma indene de dúvidas a autoria do réu. "
                "Do mesmo modo, inacolhível a tese de inexigibilidade de conduta diversa por urgência médica (MER-08), "
                "haja vista que a entrega de medicamentos ao filho poderia ter sido realizada por intermédio de terceiros, "
                "não justificando a violação da medida protetiva."
            ),
        )
        dossie = DossieRestricaoSecao(
            secao=SecaoTipo.MERITO,
            folhas_permitidas=[],
            fatos_delimitados=[],
            teses_defesa=[
                "MER-01: Insuficiência probatória / in dubio pro reo",
                "MER-08: Inexigibilidade de conduta diversa",
            ],
            diretrizes_jurisprudenciais=[],
        )
        relatorio = critic.criticar_secao(rascunho, dossie)
        assert relatorio.aprovado is True
        assert not relatorio.tem_falha_critica()
        assert relatorio.score_dialetico == 100.0

    def test_mencionou_tese_mas_nao_julgou_gera_moderada(self):
        critic = AdversarialJudicialCritic(use_ollama=False)
        rascunho = SecaoRascunho(
            secao=SecaoTipo.PRELIMINARES,
            texto_redigido=(
                "Quanto às preliminares, a Defesa arguiu inépcia da denúncia alegando narrativa genérica dos fatos. "
                "Os autos vieram conclusos para sentença após audiência de instrução."
            ),
        )
        dossie = DossieRestricaoSecao(
            secao=SecaoTipo.PRELIMINARES,
            folhas_permitidas=[],
            fatos_delimitados=[],
            teses_defesa=["PREL-01: Inépcia da denúncia"],
            diretrizes_jurisprudenciais=[],
        )
        relatorio = critic.criticar_secao(rascunho, dossie)
        # Como mencionou sem rejeitar nem acolher, deve gerar crítica moderada
        itens_dialetica = [i for i in relatorio.itens if i.gravidade == "moderada"]
        assert len(itens_dialetica) >= 1
        assert "carece de julgamento" in itens_dialetica[0].descricao


# ==============================================================================
# 5. TestF12StandardProbatorioECNJ492 (F12)
# ==============================================================================


class TestF12StandardProbatorioECNJ492:
    """Testa a aplicação do Protocolo de Julgamento com Perspectiva de Gênero (CNJ 492) e garantias constitucionais."""

    def test_estereotipo_genero_culpabilizacao_vitima_reprova(self):
        critic = AdversarialJudicialCritic(use_ollama=False)
        rascunho = SecaoRascunho(
            secao=SecaoTipo.MERITO,
            texto_redigido=(
                "Constata-se que o comportamento da vítima contribuiu decisivamente para a briga, "
                "pois a mulher histérica provocou a agressão física ao desferir xingamentos. "
                "Rejeito a tese de legítima defesa e condeno o réu."
            ),
        )
        dossie = DossieRestricaoSecao(
            secao=SecaoTipo.MERITO,
            folhas_permitidas=[],
            fatos_delimitados=[],
            teses_defesa=["MER-03: Legítima defesa"],
            diretrizes_jurisprudenciais=[],
        )
        relatorio = critic.criticar_secao(rascunho, dossie)
        assert relatorio.aprovado is False
        assert relatorio.tem_falha_critica() is True
        itens_genero = [i for i in relatorio.itens if i.tipo == "genero_cnj492"]
        assert len(itens_genero) >= 1

    def test_legitima_defesa_da_honra_sem_repudio_reprova(self):
        critic = AdversarialJudicialCritic(use_ollama=False)
        rascunho = SecaoRascunho(
            secao=SecaoTipo.MERITO,
            texto_redigido=(
                "O réu alegou ter agido em legítima defesa da honra ferida diante das ofensas da companheira. "
                "Todavia, a autoria restou comprovada pelas testemunhas."
            ),
        )
        dossie = DossieRestricaoSecao(
            secao=SecaoTipo.MERITO,
            folhas_permitidas=[],
            fatos_delimitados=[],
            teses_defesa=[],
            diretrizes_jurisprudenciais=[],
        )
        relatorio = critic.criticar_secao(rascunho, dossie)
        assert relatorio.aprovado is False
        itens_honra = [i for i in relatorio.itens if "honra" in i.descricao.lower()]
        assert len(itens_honra) >= 1

    def test_legitima_defesa_da_honra_expressamente_repudiada_aprova(self):
        critic = AdversarialJudicialCritic(use_ollama=False)
        rascunho = SecaoRascunho(
            secao=SecaoTipo.MERITO,
            texto_redigido=(
                "Afasto a tese de legítima defesa da honra, expressamente declarada inconstitucional pelo STF "
                "no julgamento da ADPF 779, sendo inadmissível no Estado Democrático de Direito. "
                "Rejeito o in dubio pro reo e condeno o acusado."
            ),
        )
        dossie = DossieRestricaoSecao(
            secao=SecaoTipo.MERITO,
            folhas_permitidas=[],
            fatos_delimitados=[],
            teses_defesa=["MER-01: In dubio pro reo"],
            diretrizes_jurisprudenciais=[],
        )
        relatorio = critic.criticar_secao(rascunho, dossie)
        assert not any(i.tipo == "genero_cnj492" for i in relatorio.itens)

    def test_violacao_direito_ao_silencio_e_revelia_reprova(self):
        critic = AdversarialJudicialCritic(use_ollama=False)
        # Presunção de culpa a partir do silêncio
        rascunho_silencio = SecaoRascunho(
            secao=SecaoTipo.MERITO,
            texto_redigido=(
                "A materialidade é induvidosa. Ademais, o acusado permaneceu em silêncio em seu interrogatório, "
                "fato que autoriza a presunção de culpa em seu desfavor, corroborando a autoria delitiva. "
                "Rejeito a tese de falta de provas."
            ),
        )
        dossie = DossieRestricaoSecao(
            secao=SecaoTipo.MERITO,
            folhas_permitidas=[],
            fatos_delimitados=[],
            teses_defesa=["MER-01: Falta de provas"],
            diretrizes_jurisprudenciais=[],
        )
        relatorio = critic.criticar_secao(rascunho_silencio, dossie)
        assert relatorio.aprovado is False
        itens_silencio = [i for i in relatorio.itens if "silêncio" in i.descricao.lower() or "silencio" in i.descricao.lower()]
        assert len(itens_silencio) >= 1
        assert itens_silencio[0].gravidade == "critica"

    def test_desqualificacao_palavra_vitima_sem_testemunha_presencial(self):
        """Testa o especial relevo probatório da ofendida em violência doméstica sob CNJ 492."""
        critic = AdversarialJudicialCritic(use_ollama=False)
        rascunho_desqualifica = SecaoRascunho(
            secao=SecaoTipo.MERITO,
            texto_redigido=(
                "A palavra da vítima isolada sem testemunhas presenciais não autoriza a condenação do acusado, "
                "impondo-se a absolvição por ausência de testemunha presencial."
            ),
        )
        dossie = DossieRestricaoSecao(
            secao=SecaoTipo.MERITO,
            folhas_permitidas=[],
            fatos_delimitados=[],
            teses_defesa=[],
            diretrizes_jurisprudenciais=[],
        )
        relatorio = critic.criticar_secao(rascunho_desqualifica, dossie)
        assert relatorio.aprovado is False
        assert relatorio.tem_falha_critica() is True
        itens_standard = [i for i in relatorio.itens if i.tipo == "standard_probatorio"]
        assert len(itens_standard) >= 1
        assert "especial relevo" in itens_standard[0].descricao.lower()


# ==============================================================================
# 6. TestScoreDialeticoAndSeverityEscalation
# ==============================================================================


class TestScoreDialeticoAndSeverityEscalation:
    """Testa a aritmética da nota dialética e as regras de severidade."""

    def test_zero_falhas_score_100_aprovado(self):
        critic = AdversarialJudicialCritic()
        score, aprovado = critic._calcular_score_e_status([])
        assert score == 100.0
        assert aprovado is True

    def test_uma_critica_reprova_automaticamente(self):
        critic = AdversarialJudicialCritic()
        itens = [
            ItemCritica(
                tipo="omissao_tese",
                descricao="Omissão PREL-01",
                gravidade="critica",
                feedback_cirurgico="Enfrentar PREL-01",
            )
        ]
        score, aprovado = critic._calcular_score_e_status(itens)
        assert score == 70.0
        assert aprovado is False

    def test_uma_moderada_deduz_15_pontos_aprova(self):
        critic = AdversarialJudicialCritic()
        itens = [
            ItemCritica(
                tipo="dialetica",
                descricao="Fundamentação sucinta",
                gravidade="moderada",
                feedback_cirurgico="Elaborar mais",
            )
        ]
        score, aprovado = critic._calcular_score_e_status(itens)
        assert score == 85.0
        assert aprovado is True

    def test_duas_moderadas_deduzem_30_pontos_reprova(self):
        critic = AdversarialJudicialCritic()
        itens = [
            ItemCritica(tipo="dialetica", descricao="Vício 1", gravidade="moderada", feedback_cirurgico="A"),
            ItemCritica(tipo="dialetica", descricao="Vício 2", gravidade="moderada", feedback_cirurgico="B"),
        ]
        score, aprovado = critic._calcular_score_e_status(itens)
        assert score == 70.0
        assert aprovado is False

    def test_tres_sugestoes_deduzem_15_pontos_aprova(self):
        critic = AdversarialJudicialCritic()
        itens = [
            ItemCritica(tipo="dialetica", descricao="Estilo 1", gravidade="sugestao", feedback_cirurgico="A"),
            ItemCritica(tipo="dialetica", descricao="Estilo 2", gravidade="sugestao", feedback_cirurgico="B"),
            ItemCritica(tipo="dialetica", descricao="Estilo 3", gravidade="sugestao", feedback_cirurgico="C"),
        ]
        score, aprovado = critic._calcular_score_e_status(itens)
        assert score == 85.0
        assert aprovado is True

    def test_score_bounded_at_zero(self):
        critic = AdversarialJudicialCritic()
        itens = [
            ItemCritica(tipo="omissao_tese", descricao=f"Crítica {i}", gravidade="critica", feedback_cirurgico="X")
            for i in range(5)
        ]
        score, aprovado = critic._calcular_score_e_status(itens)
        assert score == 0.0
        assert aprovado is False


# ==============================================================================
# 7. TestMockedLLMOllamaAndJSONRobustness (F09)
# ==============================================================================


class TestMockedLLMOllamaAndJSONRobustness:
    """Testa o Agente 7 LLM, ladder de resiliência e fallback sob falhas de rede."""

    @patch("httpx.Client.post")
    def test_ollama_retorno_json_limpo(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        json_output = {
            "aprovado": True,
            "score_dialetico": 100.0,
            "itens": [],
            "parecer_sintetico": "Minuta aprovada com louvor.",
        }
        mock_response.json.return_value = {"response": json.dumps(json_output)}
        mock_post.return_value = mock_response

        critic = AdversarialJudicialCritic(use_ollama=True)
        rascunho = SecaoRascunho(secao=SecaoTipo.RELATORIO, texto_redigido="Relatório completo...")
        dossie = DossieRestricaoSecao(secao=SecaoTipo.RELATORIO, folhas_permitidas=[], fatos_delimitados=[], teses_defesa=[], diretrizes_jurisprudenciais=[])

        relatorio = critic.criticar_secao(rascunho, dossie)
        assert relatorio.aprovado is True
        assert relatorio.score_dialetico == 100.0

    @patch("httpx.Client.post")
    def test_ollama_retorno_markdown_fences(self, mock_post):
        # Simula LLM que envolve o JSON em ```json ... ```
        mock_response = MagicMock()
        mock_response.status_code = 200
        raw_content = """```json
        {
            "aprovado": false,
            "score_dialetico": 70.0,
            "itens": [
                {
                    "tipo": "omissao_tese",
                    "descricao": "Falta MER-02",
                    "paragrafo_afetado": "Parágrafo 2",
                    "gravidade": "critica",
                    "feedback_cirurgico": "Enfrente MER-02"
                }
            ],
            "parecer_sintetico": "Reprovado por omissão."
        }
        ```"""
        mock_response.json.return_value = {"response": raw_content}
        mock_post.return_value = mock_response

        critic = AdversarialJudicialCritic(use_ollama=True)
        rascunho = SecaoRascunho(secao=SecaoTipo.MERITO, texto_redigido="Texto...")
        dossie = DossieRestricaoSecao(secao=SecaoTipo.MERITO, folhas_permitidas=[], fatos_delimitados=[], teses_defesa=[], diretrizes_jurisprudenciais=[])

        relatorio = critic.criticar_secao(rascunho, dossie)
        assert relatorio.aprovado is False
        assert relatorio.tem_falha_critica() is True
        assert relatorio.itens[0].tipo == "omissao_tese"

    @patch("httpx.Client.post")
    def test_ollama_preambulo_conversacional(self, mock_post):
        # Simula LLM conversacional que adiciona texto antes do JSON
        mock_response = MagicMock()
        mock_response.status_code = 200
        raw_content = """Certamente! Como Juiz Corregedor, analisei a minuta e constatei:
        {
            "aprovado": true,
            "score_dialetico": 100.0,
            "itens": [],
            "parecer_sintetico": "Sem objeções."
        }
        Espero ter ajudado."""
        mock_response.json.return_value = {"response": raw_content}
        mock_post.return_value = mock_response

        critic = AdversarialJudicialCritic(use_ollama=True)
        rascunho = SecaoRascunho(secao=SecaoTipo.MERITO, texto_redigido="Texto...")
        dossie = DossieRestricaoSecao(secao=SecaoTipo.MERITO, folhas_permitidas=[], fatos_delimitados=[], teses_defesa=[], diretrizes_jurisprudenciais=[])

        relatorio = critic.criticar_secao(rascunho, dossie)
        assert relatorio.aprovado is True
        assert relatorio.score_dialetico == 100.0

    @patch("httpx.Client.post", side_effect=httpx.ConnectError("Ollama offline"))
    def test_ollama_connect_error_aciona_fallback_deterministico(self, mock_post):
        critic = AdversarialJudicialCritic(use_ollama=True)
        rascunho = SecaoRascunho(
            secao=SecaoTipo.MERITO,
            texto_redigido="Texto de fundamentação substancial com rejeição do in dubio pro reo e condenação.",
        )
        dossie = DossieRestricaoSecao(
            secao=SecaoTipo.MERITO,
            folhas_permitidas=[],
            fatos_delimitados=[],
            teses_defesa=["MER-01: In dubio pro reo"],
            diretrizes_jurisprudenciais=[],
        )
        # Não deve levantar exceção! Deve acionar Tier 2 sem quebras.
        relatorio = critic.criticar_secao(rascunho, dossie)
        assert isinstance(relatorio, RelatorioCriticaAdversarial)
        assert relatorio.aprovado is True

    @patch("httpx.Client.post", side_effect=httpx.TimeoutException("Timeout"))
    def test_ollama_timeout_aciona_fallback_deterministico(self, mock_post):
        critic = AdversarialJudicialCritic(use_ollama=True)
        rascunho = SecaoRascunho(secao=SecaoTipo.MERITO, texto_redigido="Texto substancial com condenação e rejeição de tese.")
        dossie = DossieRestricaoSecao(secao=SecaoTipo.MERITO, folhas_permitidas=[], fatos_delimitados=[], teses_defesa=[], diretrizes_jurisprudenciais=[])
        relatorio = critic.criticar_secao(rascunho, dossie)
        assert isinstance(relatorio, RelatorioCriticaAdversarial)


# ==============================================================================
# 8. TestDeterministicFallbackEvaluation
# ==============================================================================


class TestDeterministicFallbackEvaluation:
    """Testa as regras de dosimetria, dispositivo e sanidade do Tier 2 Determinístico."""

    def test_dosimetria_omissao_vetores_art59(self):
        critic = AdversarialJudicialCritic(use_ollama=False)
        # Omitindo antecedentes, motivos e conduta social
        texto_incompleto = (
            "Na primeira fase do art. 59 do Código Penal, analiso a culpabilidade, "
            "as circunstâncias, a personalidade, as consequências do crime e o comportamento da vítima."
        )
        rascunho = SecaoRascunho(secao=SecaoTipo.DOSIMETRIA, texto_redigido=texto_incompleto)
        dossie = DossieRestricaoSecao(secao=SecaoTipo.DOSIMETRIA, folhas_permitidas=[], fatos_delimitados=[], teses_defesa=[], diretrizes_jurisprudenciais=[])
        relatorio = critic.criticar_secao(rascunho, dossie)
        itens_vetores = [i for i in relatorio.itens if "art. 59" in i.descricao]
        assert len(itens_vetores) >= 1
        assert "antecedentes" in itens_vetores[0].descricao
        assert "motivos" in itens_vetores[0].descricao

    def test_dosimetria_bis_in_idem_art61_em_tipo_domestico(self):
        critic = AdversarialJudicialCritic(use_ollama=False)
        texto_com_bis_in_idem = (
            "Culpabilidade, antecedentes, conduta social, personalidade, motivos, circunstâncias, "
            "consequências e comportamento da vítima neutros. "
            "Na segunda fase, aplico a agravante das relacoes domesticas do art. 61, II, 'f' do CP exasperando a pena."
        )
        rascunho = SecaoRascunho(secao=SecaoTipo.DOSIMETRIA, delito_especifico="art. 129, § 9º, do CP", texto_redigido=texto_com_bis_in_idem)
        dossie = DossieRestricaoSecao(secao=SecaoTipo.DOSIMETRIA, delito_especifico="art. 129, § 9º, do CP", folhas_permitidas=[], fatos_delimitados=[], teses_defesa=[], diretrizes_jurisprudenciais=[])
        relatorio = critic.criticar_secao(rascunho, dossie)
        assert relatorio.aprovado is False
        itens_bis = [i for i in relatorio.itens if "bis in idem" in i.descricao.lower()]
        assert len(itens_bis) >= 1

    def test_dispositivo_omissao_comando_condenatorio(self):
        critic = AdversarialJudicialCritic(use_ollama=False)
        texto_sem_comando = "Ante o exposto, julgo procedente a denúncia. Intime-se a ofendida nos termos do art. 21 da Lei nº 11.340/2006."
        rascunho = SecaoRascunho(secao=SecaoTipo.DISPOSITIVO, texto_redigido=texto_sem_comando)
        dossie = DossieRestricaoSecao(secao=SecaoTipo.DISPOSITIVO, folhas_permitidas=[], fatos_delimitados=[], teses_defesa=[], diretrizes_jurisprudenciais=[])
        relatorio = critic.criticar_secao(rascunho, dossie)
        assert relatorio.aprovado is False
        itens_comando = [i for i in relatorio.itens if "CONDENAR ou ABSOLVER" in i.descricao]
        assert len(itens_comando) >= 1

    def test_dispositivo_omissao_art21_intimacao_vitima(self):
        critic = AdversarialJudicialCritic(use_ollama=False)
        texto_sem_art21 = "Ante o exposto, julgo procedente a ação para CONDENAR o acusado às penas da lei. Custas pelo réu."
        rascunho = SecaoRascunho(secao=SecaoTipo.DISPOSITIVO, texto_redigido=texto_sem_art21)
        dossie = DossieRestricaoSecao(secao=SecaoTipo.DISPOSITIVO, folhas_permitidas=[], fatos_delimitados=[], teses_defesa=[], diretrizes_jurisprudenciais=[])
        relatorio = critic.criticar_secao(rascunho, dossie)
        itens_art21 = [i for i in relatorio.itens if "Art. 21 da Lei nº 11.340/2006" in i.descricao]
        assert len(itens_art21) >= 1
        assert itens_art21[0].gravidade == "moderada"

    def test_minuta_vazia_ou_muito_curta_retorna_zero(self):
        critic = AdversarialJudicialCritic(use_ollama=False)
        rascunho_curto = SecaoRascunho(secao=SecaoTipo.MERITO, texto_redigido="Curto.")
        dossie = DossieRestricaoSecao(secao=SecaoTipo.MERITO, folhas_permitidas=[], fatos_delimitados=[], teses_defesa=[], diretrizes_jurisprudenciais=[])
        relatorio = critic.criticar_secao(rascunho_curto, dossie)
        assert relatorio.aprovado is False
        assert relatorio.score_dialetico == 0.0
        assert relatorio.tem_falha_critica() is True

    def test_dosimetria_sumula_231_pena_aquem_minimo(self):
        critic = AdversarialJudicialCritic(use_ollama=False)
        texto_sumula231 = (
            "Culpabilidade, antecedentes, conduta social, personalidade, motivos, circunstâncias, consequências "
            "e comportamento da vítima favoráveis. Na segunda fase, em razão da menoridade relativa, fixo a pena "
            "aquém do mínimo legal cominado ao delito."
        )
        rascunho = SecaoRascunho(secao=SecaoTipo.DOSIMETRIA, texto_redigido=texto_sumula231)
        dossie = DossieRestricaoSecao(secao=SecaoTipo.DOSIMETRIA, folhas_permitidas=[], fatos_delimitados=[], teses_defesa=[], diretrizes_jurisprudenciais=[])
        relatorio = critic.criticar_secao(rascunho, dossie)
        assert relatorio.aprovado is False
        assert any("Súmula 231" in i.descricao or "Sumula 231" in i.descricao for i in relatorio.itens)

    def test_dosimetria_sumula_444_inqueritos_maus_antecedentes(self):
        critic = AdversarialJudicialCritic(use_ollama=False)
        texto_sumula444 = (
            "Na primeira fase, verifico a existência de inquéritos policiais em curso que justificam a exasperação "
            "da pena-base a título de maus antecedentes. Culpabilidade, conduta social, personalidade, motivos, "
            "circunstâncias, consequências e comportamento da vítima neutros."
        )
        rascunho = SecaoRascunho(secao=SecaoTipo.DOSIMETRIA, texto_redigido=texto_sumula444)
        dossie = DossieRestricaoSecao(secao=SecaoTipo.DOSIMETRIA, folhas_permitidas=[], fatos_delimitados=[], teses_defesa=[], diretrizes_jurisprudenciais=[])
        relatorio = critic.criticar_secao(rascunho, dossie)
        assert relatorio.aprovado is False
        assert any("Súmula 444" in i.descricao or "Sumula 444" in i.descricao for i in relatorio.itens)


# ==============================================================================
# 9. TestIntegrationSectionalPipeline (M1 ↔ M2 Gating)
# ==============================================================================


class TestIntegrationSectionalPipeline:
    """Testa a integração via criar_gating_validator_adversarial e auto-correção do Caso 5."""

    def test_gating_validator_adapter_bloqueia_e_aprova(self):
        critic = AdversarialJudicialCritic(use_ollama=False)
        gating_fn = criar_gating_validator_adversarial(critic, combinar_com_default=True)

        dossie = DossieRestricaoSecao(
            secao=SecaoTipo.PRELIMINARES,
            folhas_permitidas=["ID-100", "ID-200"],
            fatos_delimitados=["Fato 1"],
            teses_defesa=["PREL-01: Inépcia da denúncia"],
            diretrizes_jurisprudenciais=[],
        )

        # Minuta omitindo PREL-01
        rascunho_defeito = SecaoRascunho(
            secao=SecaoTipo.PRELIMINARES,
            texto_redigido="Não há questões prévias pendentes nos autos constantes de ID-100.",
        )
        resultado_defeito = gating_fn(SecaoTipo.PRELIMINARES, rascunho_defeito, dossie)
        assert resultado_defeito.aprovado is False
        assert len(resultado_defeito.feedbacks_cirurgicos) >= 1

        # Minuta corrigida enfrentando PREL-01
        rascunho_correto = SecaoRascunho(
            secao=SecaoTipo.PRELIMINARES,
            texto_redigido="Rejeito a preliminar de inépcia da denúncia (PREL-01) suscitada pela Defesa Técnica no ID-100.",
        )
        resultado_correto = gating_fn(SecaoTipo.PRELIMINARES, rascunho_correto, dossie)
        assert resultado_correto.aprovado is True
        assert resultado_correto.score >= 80.0

    def test_simulacao_autocorrecao_cenario_5(self):
        """Reproduz a dinâmica empírica exata do fixture case_scenario_5_autocorrection_fallback.json."""
        with open(FIXTURES_DIR / "case_scenario_5_autocorrection_fallback.json", "r", encoding="utf-8") as f:
            caso_5 = json.load(f)

        fluxo = caso_5["fluxo_teste_autocorrecao"]
        r1_defeito = fluxo["rascunho_1_defeito"]["texto_paragrafo_2"]
        r2_corrigido = fluxo["retentativa_cirurgica_rodada_2"]["texto_corrigido_paragrafo_2"]

        critic = AdversarialJudicialCritic(use_ollama=False)
        dossie_merito = DossieRestricaoSecao(
            secao=SecaoTipo.MERITO,
            folhas_permitidas=["ID-2401125", "ID-MIDIA-40"],
            fatos_delimitados=["Descumprimento de MPU"],
            teses_defesa=["MER-02: Inexigibilidade de conduta diversa decorrente de urgência médica do filho menor (fls. 33-37 / ID-2401125)"],
            diretrizes_jurisprudenciais=[],
        )

        # Rodada 1: Minuta com defeito (omite MER-02)
        rascunho_r1 = SecaoRascunho(secao=SecaoTipo.MERITO, texto_redigido=r1_defeito)
        relatorio_r1 = critic.criticar_secao(rascunho_r1, dossie_merito)
        assert relatorio_r1.aprovado is False
        assert relatorio_r1.tem_falha_critica() is True
        feedbacks = relatorio_r1.extrair_feedbacks_cirurgicos()
        assert len(feedbacks) >= 1
        assert "MER-02" in feedbacks[0] or "inexigibilidade" in feedbacks[0].lower()

        # Rodada 2: Minuta corrigida refutando MER-02
        rascunho_r2 = SecaoRascunho(secao=SecaoTipo.MERITO, texto_redigido=r2_corrigido)
        relatorio_r2 = critic.criticar_secao(rascunho_r2, dossie_merito)
        assert relatorio_r2.aprovado is True
        assert not relatorio_r2.tem_falha_critica()
        assert relatorio_r2.score_dialetico == 100.0

    def test_pipeline_engine_executa_com_gating_adversarial(self):
        """Conecta o gating adversarial ao SectionalPipelineEngine executando sobre um caso canônico."""
        critic = AdversarialJudicialCritic(use_ollama=False)
        gating_hook = criar_gating_validator_adversarial(critic, combinar_com_default=True)

        engine = SectionalPipelineEngine(
            use_ollama=False,
            max_retries=2,
            gating_validator=gating_hook,
        )

        with open(FIXTURES_DIR / "case_scenario_1_ameaca_lesao.json", "r", encoding="utf-8") as f:
            bundle_caso_1 = json.load(f)

        # v2: caso real exige veredicto explícito do juízo por crime.
        from app.core.case_context import obter_contexto
        from app.core.errors import DecisaoJudicialPendenteError

        with pytest.raises(DecisaoJudicialPendenteError):
            engine.executar_pipeline(bundle_caso_1)
        crimes = obter_contexto(bundle_caso_1).meta.crimes_imputados
        override = {"crimes_config": [{"codigo": c, **({"enquadramento_ameaca": "caput"} if c == "art_147_cp" else {}), "veredicto": "condenar"} for c in crimes]}

        sentenca = engine.executar_pipeline(bundle_caso_1, custom_dosimetria_override=override)
        assert sentenca is not None
        assert sentenca.texto_completo
        assert sentenca.secao_relatorio
        assert sentenca.secao_preliminares
        assert sentenca.secao_merito
        assert sentenca.secao_dosimetria
        assert sentenca.secao_dispositivo

    def test_avaliar_minuta_completa_e_funcao_conveniencia(self):
        """Testa o método de auditoria global e a função criticar_secao de conveniência."""
        texto_global = (
            "RELATÓRIO: Autos de processo crime contra réu. "
            "PRELIMINARES: Sem preliminares. "
            "MÉRITO: Rejeito a tese de insuficiência probatória (MER-01), restando certa a autoria delitiva. "
            "DOSIMETRIA: Culpabilidade, antecedentes, conduta social, personalidade, motivos, circunstâncias, "
            "consequências e comportamento da vítima neutros. "
            "DISPOSITIVO: CONDENO o réu. Intime-se a ofendida nos termos do art. 21 da Lei nº 11.340/2006."
        )
        critic = AdversarialJudicialCritic(use_ollama=False)
        relatorio_global = critic.avaliar_minuta_completa(texto_global)
        assert isinstance(relatorio_global, RelatorioCriticaAdversarial)
        assert relatorio_global.aprovado is True

        # Testa função utilitária top-level criticar_secao
        rascunho = SecaoRascunho(secao=SecaoTipo.RELATORIO, texto_redigido=texto_global[:100])
        dossie = DossieRestricaoSecao(secao=SecaoTipo.RELATORIO, folhas_permitidas=[], fatos_delimitados=[], teses_defesa=[], diretrizes_jurisprudenciais=[])
        relatorio_util = criticar_secao(rascunho, dossie, use_ollama=False)
        assert isinstance(relatorio_util, RelatorioCriticaAdversarial)


@pytest.mark.parametrize(
    ("codigo", "tipo", "alegacao", "contexto"),
    [
        ("MER-11", "MERITO", "Sustenta a embriaguez fortuita como causa de isenção.",
         "A testemunha descreveu sinais de embriaguez ao sair da festa."),
        ("MER-12", "MERITO", "Pleiteia a semi-imputabilidade penal em razão do laudo.",
         "A testemunha foi incapaz de entender a pergunta sobre o horário."),
        ("MER-13", "MERITO", "Requer a desclassificação do art. 129, § 13, para o § 9º.",
         "A denúncia transcreveu o § 13 e o § 9º do art. 129."),
        ("MER-14", "MERITO", "Requer a desclassificação de lesão grave para lesão corporal leve.",
         "O laudo explica a diferença entre lesão corporal grave e leve."),
        ("MER-15", "MERITO", "Pede a extinção da punibilidade em razão do desinteresse da vítima.",
         "A reconciliação posterior foi mencionada no histórico da relação."),
        ("MER-16", "MERITO", "Invoca o princípio da insignificância para afastar a tipicidade.",
         "A testemunha considerava a discussão insignificante."),
        ("MER-17", "MERITO", "Sustenta o consentimento da vítima no descumprimento da medida protetiva.",
         "A ofendida consentiu no ingresso da equipe de perícia."),
        ("PREL-05", "PRELIMINAR", "Requer o reconhecimento da prescrição da pretensão punitiva.",
         "Consta registro de interrupção do prazo para prescrição em outro feito."),
        ("SUB-03", "SUBSIDIARIA", "Alega bis in idem na dosimetria pela dupla valoração da circunstância judicial.",
         "Alega omissão de intimação para o cálculo posterior da pena."),
    ],
)
def test_task044_catalogo_reconhece_alegacoes_atribuidas_a_defesa(codigo, tipo, alegacao, contexto):
    defesa = {"categoria": "ALEGACOES_FINAIS_DEFESA", "doc_id": "ID-DEF-TESTE", "folha": "fl. 4"}
    teses = extrair_teses_defensivas([{**defesa, "conteudo": alegacao}])
    tese = next(t for t in teses if t.codigo == codigo)
    assert tese.tipo == tipo
    assert (tese.fonte_id, tese.folha, tese.trecho) == ("ID-DEF-TESTE", "fl. 4", alegacao)
    if codigo in ("MER-13", "MER-14"):
        assert all(t.codigo != "MER-06" for t in teses)

    assert codigo not in {t.codigo for t in extrair_teses_defensivas([{**defesa, "conteudo": contexto}])}
    mp = {"categoria": "ALEGACOES_FINAIS_MP", "conteudo": f"O MP comenta os memoriais da defesa. {alegacao}"}
    assert not extrair_teses_defensivas([mp])


@pytest.mark.parametrize(
    "alegacao",
    [
        "O réu reagiu em defesa da sua honra após descobrir a traição.",
        "A defesa sustenta que o ciúme justificou a agressão.",
        "A suposta infidelidade torna a reação compreensível.",
        "Pede que se atenue a pena diante da provocação moral da vítima.",
        "A defesa invoca legítima defesa da honra como fundamento de absolvição.",
    ],
)
def test_task044_tese_vedada_explicita_ou_dissimulada(alegacao):
    teses = extrair_teses_defensivas([{
        "categoria": "ALEGACOES_FINAIS_DEFESA", "conteudo": alegacao, "doc_id": "ID-DEF-TESTE",
    }])
    tese = next(t for t in teses if t.codigo == "VED-01")
    assert tese.tipo == "VEDADA"
    assert tese.trecho == alegacao
    assert all(t.codigo != "MER-03" for t in teses)


@pytest.mark.parametrize(
    ("categoria", "texto", "codigos_esperados"),
    [
        ("DENUNCIA", "O réu, por ciúmes, ameaçou a vítima.", set()),
        ("ALEGACOES_FINAIS_DEFESA", "Os depoentes mencionaram que o réu agiu por ciúmes.", set()),
        ("ALEGACOES_FINAIS_DEFESA", "A defesa não invoca a legítima defesa da honra.", set()),
        ("RESPOSTA_ACUSACAO", "Alega legítima defesa para repelir agressão injusta atual.", {"MER-03"}),
        ("RESPOSTA_ACUSACAO", "Alega legítima defesa da honra. Sustenta também agressões recíprocas.", {"MER-03", "VED-01"}),
    ],
)
def test_task044_honra_nao_se_confunde_com_narrativa_ou_legitima_defesa_comum(categoria, texto, codigos_esperados):
    teses = extrair_teses_defensivas([{"categoria": categoria, "conteudo": texto}])
    assert {t.codigo for t in teses} & {"MER-03", "VED-01"} == codigos_esperados


@pytest.mark.parametrize(
    ("texto", "repudio"),
    [
        ("A materialidade está demonstrada e a autoria foi atribuída ao acusado.", False),
        ("A ADPF 779/STF consta do acervo. A defesa pediu absolvição.", False),
        ("O Ministério Público rejeitou a legítima defesa da honra nos memoriais.", False),
        ("A legítima defesa da honra não é manifestamente inconstitucional.", False),
        ("A defesa alegou legítima defesa da honra, que rejeito à luz da ADPF 779/STF.", True),
        ("Acolho a legítima defesa da honra por força da ADPF 779/STF.", False),
        ("Não rejeito a legítima defesa da honra, invocada expressamente nos memoriais.", False),
        ("Rejeito a legítima defesa da honra. Ainda assim, acolho a legítima defesa da honra.", False),
        ("Rejeito a legítima defesa da honra. Ainda assim, a traição justificou a agressão.", False),
        ("Repudio expressamente a legítima defesa da honra nos termos da ADPF 779/STF.", True),
        ("A defesa invocou legítima defesa da honra. Essa tese é inadmissível conforme a ADPF 779/STF.", True),
        ("A traição jamais justifica a agressão. Rejeito o argumento defensivo.", True),
    ],
)
def test_task044_crivos_exigem_repudio_da_ved_01(texto, repudio):
    from app.generation.hallucination_auditor import auditar_secao_merito, auditar_sentenca
    from app.generation.sectional_pipeline import default_in_flight_gating_validator

    teses = extrair_teses_defensivas([{
        "categoria": "ALEGACOES_FINAIS_DEFESA",
        "conteudo": "O réu reagiu em defesa da sua honra após descobrir a traição.",
    }])
    assert [t.codigo for t in teses] == ["VED-01"]
    rascunho = SecaoRascunho(secao=SecaoTipo.MERITO, texto_redigido=texto)
    dossie = DossieRestricaoSecao(
        secao=SecaoTipo.MERITO, folhas_permitidas=[], fatos_delimitados=[],
        teses_defesa=[f"{t.codigo}: {t.titulo}" for t in teses], diretrizes_jurisprudenciais=[],
    )
    critico = AdversarialJudicialCritic(use_ollama=False)
    critica = critico.criticar_secao(rascunho, dossie)
    assert (not any("VED-01" in item.descricao for item in critica.itens)) == repudio
    secao = auditar_secao_merito(texto, [], teses)
    assert (not any("repúdio" in erro.lower() for erro in secao.erros)) == repudio
    integral = auditar_sentenca(texto, [], teses)
    assert (not integral.teses_omitidas and integral.conformidade_estilo_cnj_amb) == repudio
    gating = default_in_flight_gating_validator(SecaoTipo.MERITO, rascunho, dossie)
    assert (not any("VED-01" in erro for erro in gating.erros)) == repudio


def test_task044_omissao_de_repudio_nao_e_aprovada_pelo_llm(monkeypatch):
    critico = AdversarialJudicialCritic(use_ollama=False)
    critico.use_ollama = True
    llm = MagicMock()
    monkeypatch.setattr(critico, "_avaliar_llm", llm)
    rascunho = SecaoRascunho(
        secao=SecaoTipo.MERITO,
        texto_redigido="A materialidade foi demonstrada e o juízo passa ao exame da autoria.",
    )
    dossie = DossieRestricaoSecao(
        secao=SecaoTipo.MERITO, folhas_permitidas=[], fatos_delimitados=[],
        teses_defesa=["VED-01: Legítima Defesa da Honra — Tese Vedada"], diretrizes_jurisprudenciais=[],
    )
    assert critico.criticar_secao(rascunho, dossie).tem_falha_critica()
    llm.assert_not_called()


def test_task044_classificador_preserva_ved_01_nos_metadados_e_pedidos():
    from app.ingestion.laya_classifier import extrair_metadados_processo

    texto = "A defesa sustenta que o acusado reagiu em defesa da sua honra após descobrir a traição."
    peca = PecaClassificada(
        doc_id="ID-DEF-TESTE",
        folha="fl. 4",
        titulo="Memoriais da Defesa",
        data_peca="",
        fase="INSTRUCAO",
        categoria="ALEGACOES_FINAIS_DEFESA",
        laya_confidence=0.98,
        resumo_estruturado="Memoriais com pedido defensivo",
        conteudo=texto,
    )
    meta = extrair_metadados_processo([peca])
    tese = next(t for t in meta.teses_defesa if t.codigo == "VED-01")
    pedido = next(p for p in meta.pedidos_defesa if p.tipo == "VED-01")
    assert tese.tipo == "VEDADA"
    assert pedido.fonte_id == peca.doc_id
    assert pedido.folha == peca.folha
    assert pedido.trecho == texto


def test_task044_relatorio_pode_atribuir_o_argumento_a_defesa_sem_acolhe_lo():
    from app.generation.hallucination_auditor import auditar_secao_relatorio

    mencao = "A defesa, em seus memoriais, alegou legítima defesa da honra como fundamento do pedido."
    teses = ["VED-01: Legítima Defesa da Honra — Tese Vedada"]
    auditoria = auditar_secao_relatorio(mencao, [], teses)
    assert auditoria.conformidade_estilo_cnj_amb

    acolhimento = "Acolho a legítima defesa da honra invocada pela defesa como causa de justificação."
    auditoria = auditar_secao_relatorio(acolhimento, [], teses)
    assert not auditoria.conformidade_estilo_cnj_amb
