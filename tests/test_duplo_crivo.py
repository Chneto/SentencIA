# -*- coding: utf-8 -*-
"""
Suíte de Testes do Marco M3 — Auditoria Seccional Granular + Duplo Crivo + Fallback.

Cobre:
- Contrato Pydantic ``RelatorioAuditoriaSecao`` e wrappers seccionais (5 seções);
- Camada 1: IDs alucinados/forjados por seção e universo fechado de mídias;
- Camada 2: citações apócrifas (Súmula 999/STJ) e base fechada autorizada;
- Camada 3: omissão de teses PRELIMINARES/MÉRITO, fórmulas evasivas e art. 381, III, CPP;
- Camada 4: datas ISO (2025-02-14), ABNT NBR 10520, estilo CNJ/AMB, art. 59 do CP
  (somente na dosimetria), art. 21 da LMP (somente no dispositivo) e art. 381 I-III;
- Retrocompatibilidade integral de ``auditar_sentenca`` (score 100.0 nos 5 cenários reais
  de ``tests/fixtures/case_scenario_*.json`` e detecção de alucinações injetadas);
- Duplo Crivo (auditor Python + Agente 7): composição AND, score = mínimo, agregação de
  erros/feedbacks, saneamento de citações literais e sinal de fallback após retries;
- Integração real com ``SectionalPipelineEngine``: caso limpo → 5 seções APROVADO; caso
  sujo com mock de Ollama → retry cirúrgico e fallback determinístico seguro;
- Determinismo (duas execuções idênticas) e desempenho (< 0,5s por seção).
"""

import json
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.demo.demo_case import get_demo_case_bundle
from app.generation.adversarial_critic import (
    RelatorioCriticaAdversarial,
)
from app.generation.duplo_crivo import (
    LIMIAR_SCORE_PADRAO,
    MAX_RETRIES_CRIVO_PADRAO,
    ResultadoCrivoDuplo,
    criar_gating_validator_duplo_crivo,
    executar_crivo_duplo,
)
from app.generation.hallucination_auditor import (
    RelatorioAuditoriaSecao,
    RelatorioAuditoriaSentenca,
    SECOES_VALIDAS,
    auditar_secao,
    auditar_secao_dispositivo,
    auditar_secao_dosimetria,
    auditar_secao_merito,
    auditar_secao_preliminares,
    auditar_secao_relatorio,
    auditar_sentenca,
)
from app.generation.sectional_pipeline import (
    DossieRestricaoSecao,
    ResultadoGating,
    SecaoRascunho,
    SecaoTipo,
    SectionalPipelineEngine,
    StatusSecao,
    construir_dossie_secao,
    gerar_fallback_deterministico_secao,
)
from app.generation.sentence_generator import gerar_sentenca_estruturada
from app.ingestion.laya_classifier import classificar_pecas_autos, extrair_metadados_processo

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
CENARIOS_FIXTURES = sorted(FIXTURES_DIR.glob("case_scenario_*.json"))


# ==============================================================================
# FIXTURES E HELPERS
# ==============================================================================


@pytest.fixture(scope="module")
def demo_bundle() -> dict:
    """Case bundle canônico de demonstração (3 crimes: 24-A, 129 §13, 147)."""
    return get_demo_case_bundle()


@pytest.fixture(scope="module")
def textos_fallback(demo_bundle) -> dict:
    """Textos determinísticos de fallback das 5 seções (score 100.0 no auditor)."""
    return {secao: gerar_fallback_deterministico_secao(secao, demo_bundle) for secao in SecaoTipo}


@pytest.fixture(scope="module")
def dossies_demo(demo_bundle) -> dict:
    """Dossiês de restrição seccional do caso de demonstração."""
    return {secao: construir_dossie_secao(secao, demo_bundle) for secao in SecaoTipo}


def _dossie_secao(
    secao: SecaoTipo,
    *,
    folhas=("ID-100",),
    teses=(),
    delito: str | None = None,
) -> DossieRestricaoSecao:
    """Constrói um dossiê seccional mínimo e controlado para os testes unitários."""
    return DossieRestricaoSecao(
        secao=secao,
        delito_especifico=delito,
        folhas_permitidas=list(folhas),
        fatos_delimitados=["Fato delimitado para teste."],
        teses_defesa=list(teses),
        diretrizes_jurisprudenciais=[],
    )


def _auditar(texto: str, secao: SecaoTipo, dossie: DossieRestricaoSecao, **kwargs):
    """Atalho: audita uma seção usando o universo fechado do próprio dossiê."""
    return auditar_secao(
        secao,
        texto,
        list(dossie.folhas_permitidas),
        dossie.teses_defesa,
        **kwargs,
    )


def _carregar_cenario(caminho: Path) -> dict:
    with open(caminho, encoding="utf-8") as arquivo:
        return json.load(arquivo)


def _veredictos_condenatorios(bundle: dict) -> dict:
    """Override com o veredicto do juízo (``condenar``) para cada crime imputado no cenário real.

    Na v2 a sentença de caso real exige veredicto explícito por crime (o motor nunca condena por
    padrão); os cenários de fixture simulam a decisão do magistrado.
    """
    from app.core.case_context import obter_contexto

    crimes = obter_contexto(bundle).meta.crimes_imputados
    return {"crimes_config": [{"codigo": c, **({"enquadramento_ameaca": "caput"} if c == "art_147_cp" else {}), "veredicto": "condenar"} for c in crimes]}


def _ids_midias_do_bundle(bundle: dict) -> list:
    """Coleta os IDs de mídias transcritas declarados no case bundle (PJe Mídias)."""
    ids = []
    for transcricao in bundle.get("transcriptions") or []:
        if isinstance(transcricao, dict) and transcricao.get("media_id"):
            ids.append(str(transcricao["media_id"]))
    return ids


# ==============================================================================
# 1. CONTRATOS PYDANTIC E NORMALIZAÇÃO
# ==============================================================================


class TestContratosModelos:
    def test_relatorio_auditoria_secao_tem_campos_do_contrato_m3(self):
        """RelatorioAuditoriaSecao deve expor os campos obrigatórios do contrato PROJECT.md §M3."""
        campos_obrigatorios = {
            "secao",
            "aprovado",
            "score",
            "erros",
            "feedbacks_cirurgicos",
            "ids_inexistentes",
            "citacoes_nao_autorizadas",
            "teses_omitidas",
            "conformidade_datas_padrao_br",
            "conformidade_abnt_citacoes",
            "conformidade_estilo_cnj_amb",
            "conformidade_oito_circunstancias_art59",
            "conformidade_art21_lmp",
        }
        assert campos_obrigatorios <= set(RelatorioAuditoriaSecao.model_fields)

    def test_resultado_crivo_duplo_tem_campos_do_contrato_m3(self):
        """ResultadoCrivoDuplo deve espelhar exatamente os campos do contrato M3."""
        campos_obrigatorios = {
            "secao",
            "aprovado",
            "score_auditor_python",
            "score_critico_llm",
            "auditor_python_aprovado",
            "critico_llm_aprovado",
            "erros_auditor",
            "feedbacks_cirurgicos",
            "acionar_fallback_deterministico",
            "texto_final_secao",
        }
        assert campos_obrigatorios <= set(ResultadoCrivoDuplo.model_fields)

    def test_resultado_crivo_duplo_rejeita_score_fora_da_faixa(self):
        """O contrato Pydantic deve barrar scores fora de [0, 100]."""
        with pytest.raises(ValidationError):
            ResultadoCrivoDuplo(
                secao=SecaoTipo.MERITO,
                aprovado=True,
                score_auditor_python=120.0,
                score_critico_llm=100.0,
                auditor_python_aprovado=True,
                critico_llm_aprovado=True,
                texto_final_secao="texto",
            )

    def test_secoes_canonicas_do_auditor(self):
        """O auditor declara exatamente as 5 seções canônicas do pipeline M1."""
        assert SECOES_VALIDAS == (
            "relatorio",
            "preliminares",
            "merito",
            "dosimetria",
            "dispositivo",
        )

    def test_auditar_secao_rejeita_secao_invalida(self):
        """Seção fora do contrato deve levantar ValueError descritivo."""
        with pytest.raises(ValueError):
            auditar_secao("apelacao", "Texto suficientemente longo para a auditoria.", [], [])

    def test_auditar_secao_texto_curto_reprova_com_score_zero(self):
        """Seção vazia/curta deve reprovar imediatamente com score 0.0."""
        relatorio = auditar_secao(SecaoTipo.MERITO, "Curto.", [], [])
        assert relatorio.aprovado is False
        assert relatorio.score == 0.0
        assert relatorio.erros

    def test_art381_e_considerado_nao_aplicavel_sem_texto_integral(self):
        """Sem texto integral fornecido, a checagem do art. 381 I-III não penaliza a seção."""
        relatorio = auditar_secao_relatorio(
            "I – RELATÓRIO\n\nO Ministério Público ofereceu denúncia em face do acusado, conforme ID-100.",
            ["ID-100"],
            [],
        )
        assert relatorio.conformidade_art381_cpp is True
        assert relatorio.aprovado is True


# ==============================================================================
# 2. APROVAÇÃO DAS CINCO SEÇÕES LIMPAS (CASO DEMONSTRATIVO DETERMINÍSTICO)
# ==============================================================================


class TestAuditoriaSeccionalAprovacaoLimpa:
    def test_aprovacao_100_das_cinco_secoes_limpas(self, demo_bundle, textos_fallback, dossies_demo):
        """As 5 seções do fallback determinístico devem ser aprovadas com score 100.0."""
        for secao in SecaoTipo:
            relatorio = _auditar(textos_fallback[secao], secao, dossies_demo[secao])
            assert relatorio.aprovado is True, f"Seção {secao.value} reprovada: {relatorio.erros}"
            assert relatorio.score == 100.0, f"Seção {secao.value} com score {relatorio.score}"
            assert relatorio.erros == []
            assert relatorio.feedbacks_cirurgicos == []

    @pytest.mark.parametrize(
        "nome_wrapper,secao,nome_canonico",
        [
            ("relatorio", SecaoTipo.RELATORIO, "relatorio"),
            ("preliminares", SecaoTipo.PRELIMINARES, "preliminares"),
            ("merito", SecaoTipo.MERITO, "merito"),
            ("dosimetria", SecaoTipo.DOSIMETRIA, "dosimetria"),
            ("dispositivo", SecaoTipo.DISPOSITIVO, "dispositivo"),
        ],
    )
    def test_wrappers_granulares_aprovam_secao_limpa(
        self, nome_wrapper, secao, nome_canonico, textos_fallback, dossies_demo
    ):
        """Cada wrapper seccional explícito deve auditar a sua própria seção com 100.0."""
        wrappers = {
            "relatorio": auditar_secao_relatorio,
            "preliminares": auditar_secao_preliminares,
            "merito": auditar_secao_merito,
            "dosimetria": auditar_secao_dosimetria,
            "dispositivo": auditar_secao_dispositivo,
        }
        dossie = dossies_demo[secao]
        relatorio = wrappers[nome_wrapper](
            textos_fallback[secao],
            list(dossie.folhas_permitidas),
            dossie.teses_defesa,
        )
        assert isinstance(relatorio, RelatorioAuditoriaSecao)
        assert relatorio.secao == nome_canonico
        assert relatorio.aprovado is True
        assert relatorio.score == 100.0


# ==============================================================================
# 3. CAMADA 1 — ANCORAGEM PROBATÓRIA DE IDs (POR SEÇÃO)
# ==============================================================================


class TestCamadaUmIds:
    @pytest.mark.parametrize("secao", list(SecaoTipo))
    def test_id_alucinado_reprova_cada_uma_das_cinco_secoes(
        self, secao, textos_fallback, dossies_demo
    ):
        """ID inexistente injetado em QUALQUER seção deve reprovar a Camada 1."""
        dossie = dossies_demo[secao]
        texto = textos_fallback[secao] + "\n\nConforme o documento ID-9999999, resta comprovada a autoria."
        relatorio = _auditar(texto, secao, dossie)
        assert relatorio.aprovado is False
        assert "ID-9999999" in relatorio.ids_inexistentes
        assert any("ID-9999999" in erro for erro in relatorio.erros)
        assert relatorio.score < 100.0

    def test_ids_forjados_com_prefixos_arbitrarios_sao_detectados(self, textos_fallback, dossies_demo):
        """Prefixos forjados (ID-DOC, ID-FAKE, ID-PROVA) devem cair no regex amplo seccional."""
        dossie = dossies_demo[SecaoTipo.RELATORIO]
        texto = (
            textos_fallback[SecaoTipo.RELATORIO]
            + "\n\nVer ID-DOC-9999, ID-FAKE-8888, ID-PROVA-001 e ID-LAUDO-77."
        )
        relatorio = _auditar(texto, SecaoTipo.RELATORIO, dossie)
        assert relatorio.aprovado is False
        for forjado in ("ID-DOC-9999", "ID-FAKE-8888", "ID-PROVA-001", "ID-LAUDO-77"):
            assert forjado in relatorio.ids_inexistentes

    def test_id_valido_do_universo_fechado_nao_reprova(self):
        """ID constante do universo fechado da seção deve ser aceito."""
        dossie = _dossie_secao(SecaoTipo.RELATORIO, folhas=("ID-4100101",))
        texto = "Conforme a denúncia de ID-4100101, o fato ocorreu em contexto de violência doméstica."
        relatorio = _auditar(texto, SecaoTipo.RELATORIO, dossie)
        assert relatorio.aprovado is True
        assert "ID-4100101" in relatorio.ids_citados_verificados

    def test_id_de_midia_autorizado_via_ids_midias(self):
        """Mídia audiovisual informada em ``ids_midias`` deve integrar o universo fechado."""
        texto = (
            "A testemunha confirmou o fato na mídia ID-MIDIA-TESTE, cuja transcrição "
            "integral foi juntada aos autos e valorada sob o contraditório."
        )
        relatorio = auditar_secao(
            SecaoTipo.MERITO,
            texto,
            [],
            [],
            ids_midias=["ID-MIDIA-TESTE"],
        )
        assert relatorio.aprovado is True
        assert "ID-MIDIA-TESTE" in relatorio.ids_citados_verificados

    def test_id_de_midia_nao_autorizado_reprova(self):
        """Mídia não listada no universo fechado deve reprovar (vedação de mídia fictícia)."""
        texto = "A testemunha confirmou o fato na mídia ID-MIDIA-INVENTADA, conforme transcrição dos autos."
        relatorio = auditar_secao(
            SecaoTipo.MERITO,
            texto,
            [],
            [],
            ids_midias=["ID-MIDIA-TESTE"],
        )
        assert relatorio.aprovado is False
        assert "ID-MIDIA-INVENTADA" in relatorio.ids_inexistentes

    def test_ids_inexistentes_geram_feedback_cirurgico(self, textos_fallback, dossies_demo):
        """A reprovação de ID deve vir acompanhada de comando corretivo injetável no retry."""
        dossie = dossies_demo[SecaoTipo.MERITO]
        texto = textos_fallback[SecaoTipo.MERITO] + " Ver ID-9876543."
        relatorio = _auditar(texto, SecaoTipo.MERITO, dossie)
        assert any("ID-9876543" in feedback for feedback in relatorio.feedbacks_cirurgicos)
        assert "ID-9876543" in relatorio.ids_inexistentes


# ==============================================================================
# 4. CAMADA 2 — BASE FECHADA DE JURISPRUDÊNCIA
# ==============================================================================


class TestCamadaDoisBaseFechada:
    def test_sumula_apocrifa_999_reprova_secao(self, textos_fallback, dossies_demo):
        """Súmula 999/STJ inexistente na base fechada deve reprovar a Camada 2."""
        dossie = dossies_demo[SecaoTipo.DISPOSITIVO]
        texto = textos_fallback[SecaoTipo.DISPOSITIVO] + " Aplica-se ao caso a Súmula 999/STJ."
        relatorio = _auditar(texto, SecaoTipo.DISPOSITIVO, dossie)
        assert relatorio.aprovado is False
        assert any("999" in citacao for citacao in relatorio.citacoes_nao_autorizadas)
        assert any("999" in erro for erro in relatorio.erros)
        assert relatorio.score < 100.0

    def test_citacoes_autorizadas_sao_verificadas(self, textos_fallback, dossies_demo):
        """Súmulas constantes do acervo verificado devem ser conferidas e aceitas."""
        dossie = dossies_demo[SecaoTipo.DOSIMETRIA]
        relatorio = _auditar(textos_fallback[SecaoTipo.DOSIMETRIA], SecaoTipo.DOSIMETRIA, dossie)
        assert relatorio.aprovado is True
        assert any("231" in citacao for citacao in relatorio.citacoes_juridicas_verificadas)

    def test_tema_repetitivo_apocrifo_reprova(self, textos_fallback, dossies_demo):
        """Tema Repetitivo inexistente deve ser bloqueado pela base fechada."""
        dossie = dossies_demo[SecaoTipo.MERITO]
        texto = textos_fallback[SecaoTipo.MERITO] + " Incide o Tema Repetitivo 8888 do STJ."
        relatorio = _auditar(texto, SecaoTipo.MERITO, dossie)
        assert relatorio.aprovado is False
        assert any("8888" in citacao for citacao in relatorio.citacoes_nao_autorizadas)

    def test_citacao_apocrifa_nao_derruba_auditoria_monolitica_de_caso_limpo(self, demo_bundle):
        """A auditoria monolítica do caso limpo segue 100.0 (não regressão da Camada 2)."""
        sentenca = gerar_sentenca_estruturada(case_bundle=demo_bundle, use_ollama_if_available=False)
        pecas = classificar_pecas_autos(demo_bundle["raw_documents"])
        meta = extrair_metadados_processo(pecas)
        auditoria = auditar_sentenca(sentenca.texto_completo, pecas, meta.teses_defesa)
        assert auditoria.aprovada_sem_alucinacao is True
        assert auditoria.score_confiabilidade == 100.0


# ==============================================================================
# 5. CAMADA 3 — ENFRENTAMENTO DE TESES (ART. 381, III, CPP) E EVASIVAS
# ==============================================================================


class TestCamadaTresTeses:
    def test_omissao_de_tese_preliminar_reprova(self):
        dossie = _dossie_secao(
            SecaoTipo.PRELIMINARES,
            teses=("PREL-01: Inépcia da denúncia por ausência de justa causa",),
        )
        texto = (
            "Rejeito as alegações genéricas da Defesa Técnica e passo à análise do mérito da causa, "
            "porquanto a instrução probatória foi regularmente concluída."
        )
        relatorio = _auditar(texto, SecaoTipo.PRELIMINARES, dossie)
        assert relatorio.aprovado is False
        assert any("PREL-01" in omissao for omissao in relatorio.teses_omitidas)
        assert any("381, III, CPP" in erro for erro in relatorio.erros)

    def test_omissao_de_tese_de_merito_reprova(self):
        dossie = _dossie_secao(
            SecaoTipo.MERITO,
            teses=("MER-01: Tese de insuficiência probatória (in dubio pro reo)",),
        )
        texto = (
            "A materialidade e a autoria restaram suficientemente demonstradas pelo conjunto "
            "probatório colhido nos autos, inclusive pela prova oral e documental."
        )
        relatorio = _auditar(texto, SecaoTipo.MERITO, dossie)
        assert relatorio.aprovado is False
        assert any("MER-01" in omissao for omissao in relatorio.teses_omitidas)

    def test_tese_enfrentada_por_codigo_nao_reprova(self):
        dossie = _dossie_secao(
            SecaoTipo.PRELIMINARES,
            teses=("PREL-01: Inépcia da denúncia por ausência de justa causa",),
        )
        texto = (
            "Rejeito a preliminar de inépcia da denúncia (PREL-01), pois a peça acusatória "
            "descreve os fatos com justa causa e permite o pleno exercício da defesa."
        )
        relatorio = _auditar(texto, SecaoTipo.PRELIMINARES, dossie)
        assert relatorio.aprovado is True
        assert relatorio.teses_omitidas == []
        assert relatorio.teses_defesa_enfrentadas

    def test_formula_evasiva_de_nao_enfrentamento_reprova(self):
        """A fórmula "deixo de analisar" deve ser detectada como violação do art. 381, III, CPP."""
        dossie = _dossie_secao(
            SecaoTipo.PRELIMINARES,
            teses=("PREL-01: Inépcia da denúncia",),
        )
        texto = "Deixo de analisar a tese de inépcia da denúncia por ausência de prévia provocação defensiva."
        relatorio = _auditar(texto, SecaoTipo.PRELIMINARES, dossie)
        assert relatorio.aprovado is False
        assert relatorio.conformidade_fundamentacao_evasiva is False
        assert any("evasiva" in erro.lower() for erro in relatorio.erros)

    def test_tese_sem_enfrentamento_no_merito_nao_penaliza_preliminares(self):
        """Teses de mérito não são exigíveis na seção de preliminares (aplicabilidade seccional)."""
        dossie = _dossie_secao(
            SecaoTipo.PRELIMINARES,
            teses=(
                "PREL-01: Inépcia da denúncia",
                "MER-01: Insuficiência probatória",
            ),
        )
        texto = (
            "Rejeito a preliminar de inépcia da denúncia (PREL-01), visto que os requisitos "
            "do art. 41 do CPP estão integralmente preenchidos na peça acusatória."
        )
        relatorio = _auditar(texto, SecaoTipo.PRELIMINARES, dossie)
        assert relatorio.aprovado is True
        assert relatorio.teses_omitidas == []


# ==============================================================================
# 6. CAMADA 4 — CONFORMIDADE FORMAL E NORMATIVA
# ==============================================================================


class TestCamadaQuatroConformidade:
    def test_data_iso_estrangeira_reprova(self, textos_fallback, dossies_demo):
        """Data em padrão ISO (2025-02-14) é vedada na narrativa forense."""
        dossie = dossies_demo[SecaoTipo.RELATORIO]
        texto = textos_fallback[SecaoTipo.RELATORIO] + "\n\nOs fatos ocorreram em 2025-02-14, segundo a denúncia."
        relatorio = _auditar(texto, SecaoTipo.RELATORIO, dossie)
        assert relatorio.aprovado is False
        assert relatorio.conformidade_datas_padrao_br is False
        assert any("2025-02-14" in erro for erro in relatorio.erros)

    def test_data_brasileira_por_extenso_e_aprovada(self, textos_fallback, dossies_demo):
        """O texto limpo do relatório usa datas por extenso e deve manter conformidade."""
        dossie = dossies_demo[SecaoTipo.RELATORIO]
        relatorio = _auditar(textos_fallback[SecaoTipo.RELATORIO], SecaoTipo.RELATORIO, dossie)
        assert relatorio.conformidade_datas_padrao_br is True

    def test_citacao_longa_abnt_com_aspas_externas_reprova(self, textos_fallback, dossies_demo):
        """ABNT NBR 10520: bloco recuado de citação longa não pode estar entre aspas."""
        dossie = dossies_demo[SecaoTipo.MERITO]
        bloco_invalido = (
            '\n\n    "A violência doméstica constitui fenômeno estrutural que exige julgamento '
            'com perspectiva de gênero, nos termos da Resolução CNJ nº 492/2023."'
        )
        relatorio = _auditar(
            textos_fallback[SecaoTipo.MERITO] + bloco_invalido,
            SecaoTipo.MERITO,
            dossie,
        )
        assert relatorio.aprovado is False
        assert relatorio.conformidade_abnt_citacoes is False

    def test_legitima_defesa_da_honra_sem_repudio_reprova(self, textos_fallback, dossies_demo):
        """Tese de legítima defesa da honra sem repúdio viola CNJ/AMB (ADPF 779/STF)."""
        dossie = dossies_demo[SecaoTipo.RELATORIO]
        texto = (
            textos_fallback[SecaoTipo.RELATORIO]
            + "\n\nA legítima defesa da honra autoriza a absolvição do acusado."
        )
        relatorio = _auditar(texto, SecaoTipo.RELATORIO, dossie)
        assert relatorio.aprovado is False
        assert relatorio.conformidade_estilo_cnj_amb is False

    def test_legitima_defesa_da_honra_com_repudio_expresso_e_aprovada(self, textos_fallback, dossies_demo):
        """O repúdio expresso à legítima defesa da honra (ADPF 779) mantém a conformidade."""
        dossie = dossies_demo[SecaoTipo.RELATORIO]
        texto = (
            textos_fallback[SecaoTipo.RELATORIO]
            + "\n\nAfasto a tese de legítima defesa da honra, inconstitucional conforme a ADPF 779/STF."
        )
        relatorio = _auditar(texto, SecaoTipo.RELATORIO, dossie)
        assert relatorio.conformidade_estilo_cnj_amb is True

    def test_grafia_de_artigo_em_desacordo_com_lc95_reprova(self, textos_fallback, dossies_demo):
        """LC 95/98: 'art. 129º' é grafia incorreta e deve reprovar a Camada 4."""
        dossie = dossies_demo[SecaoTipo.RELATORIO]
        texto = textos_fallback[SecaoTipo.RELATORIO] + "\n\nConforme art. 129º do Código Penal."
        relatorio = _auditar(texto, SecaoTipo.RELATORIO, dossie)
        assert relatorio.aprovado is False
        assert relatorio.conformidade_lc95_98 is False

    def test_art59_so_e_exigido_na_dosimetria(self):
        """Os 8 vetores do art. 59 não são exigíveis fora da dosimetria (n/a = True)."""
        dossie = _dossie_secao(SecaoTipo.MERITO)
        texto = (
            "A materialidade restou demonstrada, passo à análise da autoria e da tipicidade "
            "da conduta imputada ao acusado."
        )
        relatorio = _auditar(texto, SecaoTipo.MERITO, dossie)
        assert relatorio.conformidade_oito_circunstancias_art59 is True
        assert relatorio.aprovado is True

    def test_dosimetria_sem_oito_vetores_do_art59_reprova(self):
        """Na dosimetria, a omissão dos vetores do art. 59 deve reprovar a Camada 4."""
        texto = (
            "Passo à dosimetria da pena. Fixo a pena-base em 2 (dois) anos de reclusão, "
            "sem individualizar as circunstâncias judiciais."
        )
        relatorio = auditar_secao_dosimetria(texto, ["ID-100"], [])
        assert relatorio.aprovado is False
        assert relatorio.conformidade_oito_circunstancias_art59 is False
        assert any("art. 59" in erro.lower() for erro in relatorio.erros)

    def test_dosimetria_com_vetores_desacentuados_e_aprovada(self, demo_bundle, dossies_demo):
        """A dosimetria aceita grafia sem acento dos vetores (tolerância redacional do M1)."""
        import unicodedata

        texto = gerar_fallback_deterministico_secao(SecaoTipo.DOSIMETRIA, demo_bundle)
        texto_sem_acentos = "".join(
            c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
        )
        dossie = dossies_demo[SecaoTipo.DOSIMETRIA]
        relatorio = _auditar(texto_sem_acentos, SecaoTipo.DOSIMETRIA, dossie)
        assert relatorio.conformidade_oito_circunstancias_art59 is True
        assert relatorio.aprovado is True

    def test_art21_exigido_somente_no_dispositivo(self, textos_fallback, dossies_demo):
        """A determinação do art. 21 da LMP é exigível exclusivamente no dispositivo."""
        dossie_dispositivo = dossies_demo[SecaoTipo.DISPOSITIVO]
        texto_sem_art21 = textos_fallback[SecaoTipo.DISPOSITIVO].replace(
            "art. 21 da Lei nº 11.340/2006",
            "a intimação da ofendida",
        )
        relatorio_dispositivo = _auditar(
            texto_sem_art21, SecaoTipo.DISPOSITIVO, dossie_dispositivo
        )
        assert relatorio_dispositivo.conformidade_art21_lmp is False
        assert relatorio_dispositivo.aprovado is False

        # O mesmo texto auditado como RELATÓRIO não sofre a exigência do art. 21 da LMP.
        relatorio_relatorio = auditar_secao_relatorio(
            texto_sem_art21,
            list(dossie_dispositivo.folhas_permitidas),
            [],
        )
        assert relatorio_relatorio.conformidade_art21_lmp is True

    def test_estrutura_art381_integral_verificada_quando_texto_completo_fornecido(
        self, textos_fallback, dossies_demo
    ):
        """Com o texto integral, a estrutura canônica I/II/III do art. 381 do CPP é exigida."""
        texto_integral = "\n\n".join(textos_fallback[secao] for secao in SecaoTipo)
        dossie = dossies_demo[SecaoTipo.DISPOSITIVO]
        relatorio_ok = auditar_secao_dispositivo(
            textos_fallback[SecaoTipo.DISPOSITIVO],
            list(dossie.folhas_permitidas),
            dossie.teses_defesa,
            texto_sentenca_completo=texto_integral,
        )
        assert relatorio_ok.conformidade_art381_cpp is True

        texto_mutilado = texto_integral.replace("III – DISPOSITIVO", "III – DECISÃO FINAL")
        relatorio_mutilado = auditar_secao_dispositivo(
            textos_fallback[SecaoTipo.DISPOSITIVO],
            list(dossie.folhas_permitidas),
            dossie.teses_defesa,
            texto_sentenca_completo=texto_mutilado,
        )
        assert relatorio_mutilado.conformidade_art381_cpp is False
        assert relatorio_mutilado.aprovado is False


# ==============================================================================
# 7. RETROCOMPATIBILIDADE DA AUDITORIA MONOLÍTICA (5 CENÁRIOS REAIS)
# ==============================================================================


class TestRetrocompatibilidadeAuditoriaMonolitica:
    @pytest.mark.parametrize("cenario", CENARIOS_FIXTURES, ids=lambda p: p.stem)
    def test_auditar_sentenca_score_100_nos_cinco_cenarios_reais(self, cenario):
        """Nos 5 cenários reais: forma 100.0, zero alucinação e penas conferidas; as lacunas que só o
        juízo pode preencher aparecem como marcadores pendentes (e só elas impedem a aprovação)."""
        from app.core.errors import DecisaoJudicialPendenteError

        bundle = _carregar_cenario(cenario)
        with pytest.raises(DecisaoJudicialPendenteError):
            gerar_sentenca_estruturada(case_bundle=bundle, use_ollama_if_available=False)

        sentenca = gerar_sentenca_estruturada(
            case_bundle=bundle,
            custom_dosimetria_override=_veredictos_condenatorios(bundle),
            use_ollama_if_available=False,
        )
        pecas = classificar_pecas_autos(bundle["raw_documents"])
        meta = extrair_metadados_processo(pecas)

        auditoria = auditar_sentenca(
            sentenca.texto_completo,
            pecas,
            meta.teses_defesa,
            ids_midias=_ids_midias_do_bundle(bundle),
            dosimetria_dados=sentenca.dosimetria_dados,
        )

        assert isinstance(auditoria, RelatorioAuditoriaSentenca)
        assert auditoria.score_forma == 100.0
        assert auditoria.ids_inexistentes == []
        assert auditoria.citacoes_nao_autorizadas == []
        assert auditoria.teses_omitidas == []
        assert auditoria.penas_divergentes == []
        assert set(auditoria.marcadores_pendentes) == set(sentenca.marcadores_pendentes)
        # Nenhum caso real traz alvará/liberdade provisória: a situação prisional fica para o juízo.
        assert "[COMPLETAR PELO JUÍZO: situação prisional do réu]" in sentenca.marcadores_pendentes
        assert "respondeu solto" not in sentenca.texto_completo.lower()
        assert auditoria.aprovada_sem_alucinacao is (not sentenca.marcadores_pendentes)

    def test_auditar_sentenca_ainda_detecta_id_e_precedente_alucinados(self, demo_bundle):
        """A refatoração não pode cegar a detecção de ID falso e REsp inventado."""
        sentenca = gerar_sentenca_estruturada(case_bundle=demo_bundle, use_ollama_if_available=False)
        pecas = classificar_pecas_autos(demo_bundle["raw_documents"])
        meta = extrair_metadados_processo(pecas)
        texto_adulterado = (
            sentenca.texto_completo
            + "\nConforme documento de ID-99999999 e REsp 9.876.543/XY."
        )
        auditoria = auditar_sentenca(texto_adulterado, pecas, meta.teses_defesa)
        assert auditoria.aprovada_sem_alucinacao is False
        assert "ID-99999999" in auditoria.ids_inexistentes
        assert any("9.876.543" in citacao for citacao in auditoria.citacoes_nao_autorizadas)

    def test_auditar_sentenca_camada_4_art59_continua_operante(self, demo_bundle):
        """A checagem dos 8 vetores do art. 59 na auditoria monolítica segue detectando omissões."""
        sentenca = gerar_sentenca_estruturada(case_bundle=demo_bundle, use_ollama_if_available=False)
        pecas = classificar_pecas_autos(demo_bundle["raw_documents"])
        meta = extrair_metadados_processo(pecas)
        texto_mutilado = sentenca.texto_completo.replace("conduta social", "comportamento geral")
        texto_mutilado = texto_mutilado.replace("CONDUTA SOCIAL:", "COMPORTAMENTO GERAL:")
        auditoria = auditar_sentenca(texto_mutilado, pecas, meta.teses_defesa)
        assert auditoria.conformidade_oito_circunstancias_art59 is False
        assert auditoria.aprovada_sem_alucinacao is False


# ==============================================================================
# 8. DUPLO CRIVO — COMPOSIÇÃO DOS DOIS CRIVOS
# ==============================================================================


class TestDuploCrivoComposicao:
    @pytest.mark.parametrize("secao", list(SecaoTipo))
    def test_aprovacao_dupla_de_caso_limpo_por_secao(
        self, secao, demo_bundle, textos_fallback, dossies_demo
    ):
        """Caso limpo: auditor Python e Agente 7 aprovam com score 100.0 e sem fallback."""
        resultado = executar_crivo_duplo(
            secao,
            SecaoRascunho(
                secao=secao,
                texto_redigido=textos_fallback[secao],
                versao_tentativa=1,
            ),
            dossies_demo[secao],
            bundle=demo_bundle,
        )
        assert isinstance(resultado, ResultadoCrivoDuplo)
        assert resultado.secao == secao
        assert resultado.aprovado is True
        assert resultado.auditor_python_aprovado is True
        assert resultado.critico_llm_aprovado is True
        assert resultado.score_auditor_python == 100.0
        assert resultado.score_critico_llm == 100.0
        assert resultado.acionar_fallback_deterministico is False
        assert resultado.texto_final_secao == textos_fallback[secao]

    def test_id_alucinado_reprova_auditor_mas_nao_critico(self, demo_bundle, textos_fallback, dossies_demo):
        """A composição AND: ID falso reprova pelo auditor mesmo se o crítico aprovar."""
        texto = textos_fallback[SecaoTipo.MERITO] + " Ver ID-9999999."
        resultado = executar_crivo_duplo(
            SecaoTipo.MERITO,
            SecaoRascunho(secao=SecaoTipo.MERITO, texto_redigido=texto),
            dossies_demo[SecaoTipo.MERITO],
            bundle=demo_bundle,
        )
        assert resultado.auditor_python_aprovado is False
        assert resultado.critico_llm_aprovado is True
        assert resultado.aprovado is False
        assert any("ID-9999999" in erro for erro in resultado.erros_auditor)

    def test_sumula_apocrifa_reprova_auditor_mas_nao_critico(self, demo_bundle, textos_fallback, dossies_demo):
        """Súmula 999/STJ é bloqueada pela base fechada do auditor; o crítico não a valida."""
        texto = textos_fallback[SecaoTipo.DISPOSITIVO] + " Aplica-se a Súmula 999/STJ."
        resultado = executar_crivo_duplo(
            SecaoTipo.DISPOSITIVO,
            SecaoRascunho(secao=SecaoTipo.DISPOSITIVO, texto_redigido=texto),
            dossies_demo[SecaoTipo.DISPOSITIVO],
            bundle=demo_bundle,
        )
        assert resultado.auditor_python_aprovado is False
        assert resultado.critico_llm_aprovado is True
        assert resultado.aprovado is False

    def test_texto_evasivo_reprova_duplo_crivo(self, demo_bundle):
        """Texto evasivo (não enfrentamento) deve ser reprovado pelos dois crivos."""
        dossie = _dossie_secao(
            SecaoTipo.PRELIMINARES,
            teses=("PREL-01: Inépcia da denúncia",),
        )
        texto = (
            "Deixo de analisar a preliminar de inépcia por absoluta desnecessidade, "
            "passando diretamente ao exame do mérito da pretensão punitiva."
        )
        resultado = executar_crivo_duplo(
            SecaoTipo.PRELIMINARES,
            SecaoRascunho(secao=SecaoTipo.PRELIMINARES, texto_redigido=texto),
            dossie,
            bundle=demo_bundle,
        )
        assert resultado.aprovado is False
        assert resultado.auditor_python_aprovado is False
        assert resultado.feedbacks_cirurgicos

    def test_score_do_gating_e_o_minimo_dos_dois_crivos(self, demo_bundle, textos_fallback, dossies_demo):
        """ResultadoGating.score deve ser ``min`` dos dois crivos (critério mais severo)."""
        gating = criar_gating_validator_duplo_crivo(use_ollama=False)
        dossie = dossies_demo[SecaoTipo.DISPOSITIVO]
        texto = textos_fallback[SecaoTipo.DISPOSITIVO] + " Aplica-se a Súmula 999/STJ."
        resultado = gating(
            SecaoTipo.DISPOSITIVO,
            SecaoRascunho(secao=SecaoTipo.DISPOSITIVO, texto_redigido=texto),
            dossie,
        )
        resultado_duplo = executar_crivo_duplo(
            SecaoTipo.DISPOSITIVO,
            SecaoRascunho(secao=SecaoTipo.DISPOSITIVO, texto_redigido=texto),
            dossie,
            bundle=demo_bundle,
        )
        assert isinstance(resultado, ResultadoGating)
        assert resultado.aprovado is False
        assert resultado.score == min(
            resultado_duplo.score_auditor_python,
            resultado_duplo.score_critico_llm,
        )
        assert resultado.erros
        assert resultado.feedbacks_cirurgicos

    def test_gating_aceita_caso_limpo_e_reprova_caso_sujo(self, demo_bundle, textos_fallback, dossies_demo):
        """O adapter do engine deve aprovar o limpo e reprovar o sujo (ID forjado)."""
        gating = criar_gating_validator_duplo_crivo(use_ollama=False)
        dossie = dossies_demo[SecaoTipo.RELATORIO]
        limpo = gating(
            SecaoTipo.RELATORIO,
            SecaoRascunho(secao=SecaoTipo.RELATORIO, texto_redigido=textos_fallback[SecaoTipo.RELATORIO]),
            dossie,
        )
        assert limpo.aprovado is True
        assert limpo.score == 100.0

        sujo = gating(
            SecaoTipo.RELATORIO,
            SecaoRascunho(
                secao=SecaoTipo.RELATORIO,
                texto_redigido=textos_fallback[SecaoTipo.RELATORIO] + " Ver ID-1111111.",
            ),
            dossie,
        )
        assert sujo.aprovado is False
        assert any("ID-1111111" in erro for erro in sujo.erros)

    def test_acionar_fallback_somente_apos_retentativas(self, demo_bundle):
        """O sinal de fallback só pode surgir na 3ª tentativa (2 retries esgotados)."""
        dossie = _dossie_secao(
            SecaoTipo.MERITO,
            teses=("MER-01: Insuficiência probatória",),
        )
        texto_sujo = (
            "Conforme ID-9999999 e Súmula 999/STJ, condeno o réu sem enfrentar as teses defensivas."
        )
        sinalizacoes = []
        for tentativa in (1, 2, 3):
            resultado = executar_crivo_duplo(
                SecaoTipo.MERITO,
                SecaoRascunho(
                    secao=SecaoTipo.MERITO,
                    texto_redigido=texto_sujo,
                    versao_tentativa=tentativa,
                ),
                dossie,
                bundle=demo_bundle,
            )
            sinalizacoes.append(resultado.acionar_fallback_deterministico)

        assert sinalizacoes == [False, False, True]
        assert MAX_RETRIES_CRIVO_PADRAO == 2
        assert LIMIAR_SCORE_PADRAO == 80.0

    def test_erros_e_feedbacks_agregam_os_dois_crivos(self, demo_bundle):
        """Erros e feedbacks do veredito devem agregar auditor Python e Agente 7."""
        dossie = _dossie_secao(
            SecaoTipo.MERITO,
            teses=("MER-02: Atipicidade do crime de ameaça por ânimo exaltado",),
        )
        texto_sujo = "Conforme ID-9999999, a conduta não se amolda ao tipo penal, sem enfrentar a tese."
        resultado = executar_crivo_duplo(
            SecaoTipo.MERITO,
            SecaoRascunho(secao=SecaoTipo.MERITO, texto_redigido=texto_sujo),
            dossie,
            bundle=demo_bundle,
        )
        assert resultado.aprovado is False
        assert any("ID-9999999" in erro for erro in resultado.erros_auditor)
        assert any("[AGENTE 7" in erro for erro in resultado.erros_auditor)
        assert any("Remova ou substitua os IDs" in feedback for feedback in resultado.feedbacks_cirurgicos)
        assert len(resultado.feedbacks_cirurgicos) >= 2

    def test_critic_injetado_e_reutilizado(self, demo_bundle, textos_fallback, dossies_demo):
        """A instância de crítico injetada deve ser efetivamente utilizada (sem recriação)."""
        from unittest import mock

        critico_mock = mock.MagicMock()
        critico_mock.criticar_secao.return_value = RelatorioCriticaAdversarial(
            aprovado=True,
            score_dialetico=100.0,
            itens=[],
            parecer_sintetico="Parecer mockado aprovado.",
        )
        resultado = executar_crivo_duplo(
            SecaoTipo.RELATORIO,
            SecaoRascunho(
                secao=SecaoTipo.RELATORIO,
                texto_redigido=textos_fallback[SecaoTipo.RELATORIO],
            ),
            dossies_demo[SecaoTipo.RELATORIO],
            bundle=demo_bundle,
            critic=critico_mock,
        )
        assert resultado.aprovado is True
        assert critico_mock.criticar_secao.call_count == 1

    def test_falha_inesperada_do_critico_bloqueia_sem_excecao(self, demo_bundle, textos_fallback, dossies_demo):
        """Falha catastrófica do Agente 7 não pode lançar exceção: deve bloquear e reportar."""

        class CriticoQueFalha:
            def criticar_secao(self, *args, **kwargs):
                raise RuntimeError("falha simulada do Ollama")

        resultado = executar_crivo_duplo(
            SecaoTipo.RELATORIO,
            SecaoRascunho(
                secao=SecaoTipo.RELATORIO,
                texto_redigido=textos_fallback[SecaoTipo.RELATORIO],
            ),
            dossies_demo[SecaoTipo.RELATORIO],
            bundle=demo_bundle,
            critic=CriticoQueFalha(),
        )
        assert resultado.aprovado is False
        assert resultado.critico_llm_aprovado is False
        assert any("indisponível" in erro for erro in resultado.erros_auditor)


# ==============================================================================
# 9. DUPLO CRIVO — SANEAMENTO DE CITAÇÕES LITERAIS (FALSO POSITIVO SÚMULA 231)
# ==============================================================================


class TestSaneamentoCitacoesLiterais:
    def test_dosimetria_limpa_com_sumula_231_citada_literalmente_e_aprovada(
        self, demo_bundle, textos_fallback, dossies_demo
    ):
        """
        A dosimetria determinística transcreve a Súmula 231 entre aspas.

        Sem o saneamento, o Tier 2 do Agente 7 confunde a transcrição com violação;
        com o saneamento, o Duplo Crivo aprova a seção limpa (regressão do caso demo).
        """
        texto = textos_fallback[SecaoTipo.DOSIMETRIA]
        assert "abaixo do mínimo legal" in texto  # citação literal presente no texto limpo
        resultado = executar_crivo_duplo(
            SecaoTipo.DOSIMETRIA,
            SecaoRascunho(secao=SecaoTipo.DOSIMETRIA, texto_redigido=texto),
            dossies_demo[SecaoTipo.DOSIMETRIA],
            bundle=demo_bundle,
        )
        assert resultado.aprovado is True
        assert resultado.score_critico_llm == 100.0

    def test_saneamento_desativado_expoe_o_falso_positivo(self, demo_bundle, textos_fallback, dossies_demo):
        """Com ``sanear_citacoes_literais=False``, o falso positivo do Agente 7 reaparece."""
        resultado = executar_crivo_duplo(
            SecaoTipo.DOSIMETRIA,
            SecaoRascunho(
                secao=SecaoTipo.DOSIMETRIA,
                texto_redigido=textos_fallback[SecaoTipo.DOSIMETRIA],
            ),
            dossies_demo[SecaoTipo.DOSIMETRIA],
            bundle=demo_bundle,
            sanear_citacoes_literais=False,
        )
        assert resultado.auditor_python_aprovado is True
        assert resultado.critico_llm_aprovado is False
        assert resultado.aprovado is False

    def test_violacao_real_da_sumula_231_nao_e_saneada(self, demo_bundle, textos_fallback, dossies_demo):
        """Violação efetiva (sem aspas) da Súmula 231 deve continuar sendo reprovada."""
        texto = (
            textos_fallback[SecaoTipo.DOSIMETRIA]
            + "\n\nFixo a pena abaixo do mínimo legal em razão da atenuante da confissão espontânea."
        )
        resultado = executar_crivo_duplo(
            SecaoTipo.DOSIMETRIA,
            SecaoRascunho(secao=SecaoTipo.DOSIMETRIA, texto_redigido=texto),
            dossies_demo[SecaoTipo.DOSIMETRIA],
            bundle=demo_bundle,
        )
        assert resultado.critico_llm_aprovado is False
        assert resultado.aprovado is False
        assert any("231" in erro for erro in resultado.erros_auditor)


# ==============================================================================
# 10. INTEGRAÇÃO REAL COM O SECTIONAL PIPELINE ENGINE (M1 ↔ M3)
# ==============================================================================


class TestIntegracaoSectionalPipeline:
    def test_caso_limpo_cenario_real_cinco_secoes_aprovadas(self):
        """Caso limpo real: as 5 seções devem ser seladas com status APROVADO."""
        bundle = _carregar_cenario(CENARIOS_FIXTURES[0])
        engine = SectionalPipelineEngine(
            use_ollama=False,
            gating_validator=criar_gating_validator_duplo_crivo(use_ollama=False),
        )
        sentenca = engine.executar_pipeline(bundle, custom_dosimetria_override=_veredictos_condenatorios(bundle))

        assert sentenca is not None
        assert len(engine.estado.secoes_seladas) == 5
        for secao in SecaoTipo:
            assert engine.estado.status_secoes[secao] == StatusSecao.APROVADO, (
                f"Seção {secao.value} não aprovada no caso limpo."
            )
            assert len(engine.estado.historico_tentativas[secao]) == 1
        assert sentenca.texto_completo
        assert sentenca.secoes_fallback == []
        # Marcadores do juízo não reprovam o gating, mas seguem visíveis na minuta.
        assert sentenca.marcadores_pendentes

    def test_caso_limpo_demo_cinco_secoes_aprovadas(self, demo_bundle):
        """Caso limpo demo: as 5 seções devem ser seladas com status APROVADO (sem fallback)."""
        engine = SectionalPipelineEngine(
            use_ollama=False,
            gating_validator=criar_gating_validator_duplo_crivo(use_ollama=False),
        )
        sentenca = engine.executar_pipeline(demo_bundle)

        assert sentenca is not None
        assert len(engine.estado.secoes_seladas) == 5
        for secao in SecaoTipo:
            assert engine.estado.status_secoes[secao] == StatusSecao.APROVADO
        assert sentenca.secao_dosimetria

    def test_sentenca_limpa_do_pipeline_audita_100(self, demo_bundle):
        """A sentença montada pelo pipeline com Duplo Crivo deve manter score 100.0 no Gate 4."""
        engine = SectionalPipelineEngine(
            use_ollama=False,
            gating_validator=criar_gating_validator_duplo_crivo(use_ollama=False),
        )
        sentenca = engine.executar_pipeline(demo_bundle)
        pecas = classificar_pecas_autos(demo_bundle["raw_documents"])
        meta = extrair_metadados_processo(pecas)
        ids_midias = [
            str(t.get("media_id", ""))
            for t in demo_bundle.get("transcriptions", [])
            if t.get("media_id")
        ]
        auditoria = auditar_sentenca(
            sentenca.texto_completo,
            pecas,
            meta.teses_defesa,
            ids_midias=ids_midias,
        )
        assert auditoria.aprovada_sem_alucinacao is True
        assert auditoria.score_confiabilidade == 100.0

    def test_caso_sujo_mock_ollama_dispara_retry_e_fallback(self, demo_bundle):
        """Mock de Ollama alucinado: o engine deve esgotar 2 retries e selar o fallback seguro."""

        class RespostaOllamaAlucinada:
            status_code = 200

            def json(self):
                return {
                    "response": (
                        "Conforme ID-9999999 e Súmula 999/STJ, condeno o réu sem enfrentar as teses. "
                        "Os fatos ocorreram em 2025-02-14, conforme ID-FAKE-8888."
                    )
                }

        from unittest import mock

        with mock.patch("httpx.Client") as cliente_mock:
            cliente_mock.return_value.__enter__.return_value.post.return_value = (
                RespostaOllamaAlucinada()
            )
            engine = SectionalPipelineEngine(
                use_ollama=True,
                max_retries=2,
                gating_validator=criar_gating_validator_duplo_crivo(use_ollama=False),
            )
            sentenca = engine.executar_pipeline(demo_bundle)

        assert sentenca is not None
        for secao in SecaoTipo:
            assert engine.estado.status_secoes[secao] == StatusSecao.FALLBACK_ACIONADO
            assert len(engine.estado.historico_tentativas[secao]) == 3
        assert "ID-9999999" not in sentenca.texto_completo
        assert "ID-FAKE-8888" not in sentenca.texto_completo
        # O fallback seguro é o texto determinístico auditado do M1 (nunca o texto do LLM).
        for secao in SecaoTipo:
            assert engine.estado.secoes_seladas[secao] == gerar_fallback_deterministico_secao(
                secao, demo_bundle
            )

    def test_feedback_cirurgico_do_duplo_crivo_chega_ao_historico_do_engine(self, demo_bundle):
        """O feedback cirúrgico agregado deve constar da telemetria de retry do engine."""
        from unittest import mock

        class RespostaOllamaAlucinada:
            status_code = 200

            def json(self):
                return {"response": "Texto alucinado com ID-9999999 e Súmula 999/STJ sem qualquer fundamentação."}

        with mock.patch("httpx.Client") as cliente_mock:
            cliente_mock.return_value.__enter__.return_value.post.return_value = (
                RespostaOllamaAlucinada()
            )
            engine = SectionalPipelineEngine(
                use_ollama=True,
                max_retries=0,  # uma única tentativa para inspeção rápida da telemetria
                gating_validator=criar_gating_validator_duplo_crivo(use_ollama=False),
            )
            engine.executar_pipeline(demo_bundle)

        tentativas = engine.estado.historico_tentativas[SecaoTipo.RELATORIO]
        assert tentativas
        registro = tentativas[0]
        assert registro.resultado_gating is not None
        assert registro.resultado_gating.aprovado is False
        assert registro.resultado_gating.feedbacks_cirurgicos
        assert any("ID-9999999" in feedback for feedback in registro.resultado_gating.feedbacks_cirurgicos)


# ==============================================================================
# 11. DETERMINISMO E DESEMPENHO (CRITÉRIO M3: < 0,5s POR SEÇÃO)
# ==============================================================================


class TestDeterminismoEPerformance:
    def test_duas_execucoes_produzem_resultado_identico(self, demo_bundle, textos_fallback, dossies_demo):
        """O crivo duplo deve ser absolutamente determinístico (use_ollama=False)."""
        def executar():
            return executar_crivo_duplo(
                SecaoTipo.MERITO,
                SecaoRascunho(
                    secao=SecaoTipo.MERITO,
                    texto_redigido=textos_fallback[SecaoTipo.MERITO],
                ),
                dossies_demo[SecaoTipo.MERITO],
                bundle=demo_bundle,
            )

        primeiro = executar().model_dump(mode="json")
        segundo = executar().model_dump(mode="json")
        assert primeiro == segundo

    def test_duas_execucoes_de_caso_sujo_produzem_resultado_identico(self, demo_bundle):
        """A reprovação e os feedbacks também devem ser determinísticos."""
        dossie = _dossie_secao(
            SecaoTipo.PRELIMINARES,
            teses=("PREL-01: Inépcia da denúncia",),
        )
        texto_sujo = "Deixo de analisar a tese PREL-01 e não há qualquer enfrentamento das preliminares."
        resultado_a = executar_crivo_duplo(
            SecaoTipo.PRELIMINARES,
            SecaoRascunho(secao=SecaoTipo.PRELIMINARES, texto_redigido=texto_sujo),
            dossie,
            bundle=demo_bundle,
        )
        resultado_b = executar_crivo_duplo(
            SecaoTipo.PRELIMINARES,
            SecaoRascunho(secao=SecaoTipo.PRELIMINARES, texto_redigido=texto_sujo),
            dossie,
            bundle=demo_bundle,
        )
        assert resultado_a.model_dump(mode="json") == resultado_b.model_dump(mode="json")

    def test_desempenho_abaixo_de_meio_segundo_por_secao(
        self, demo_bundle, textos_fallback, dossies_demo
    ):
        """Cada auditoria + crivo duplo determinístico deve consumir menos de 0,5s por seção."""
        tempos = {}
        for secao in SecaoTipo:
            inicio = time.perf_counter()
            resultado = executar_crivo_duplo(
                secao,
                SecaoRascunho(
                    secao=secao,
                    texto_redigido=textos_fallback[secao],
                ),
                dossies_demo[secao],
                bundle=demo_bundle,
            )
            tempos[secao.value] = time.perf_counter() - inicio
            assert resultado.aprovado is True

        for nome_secao, tempo in tempos.items():
            assert tempo < 0.5, f"Seção {nome_secao} excedeu 0,5s: {tempo:.3f}s"
        assert sum(tempos.values()) < 2.0
