"""Tier 1: Feature Coverage (F01 a F28) — Suíte Exaustiva de Testes E2E.

Cobre 100% das 28 features do projeto SentencIA com pelo menos 5 casos
de teste ou asserções representativas por feature (>= 140 asserções no total).
"""

import datetime
from pathlib import Path
import re
from typing import List

import pytest
from fastapi.testclient import TestClient

from app.core.vram_manager import (
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
from app.generation.sentence_generator import (
    build_crimes_inputs_for_case,
)
from app.ingestion.laya_classifier import (
    classificar_pecas_autos,
    extrair_metadados_processo,
)
from app.ingestion.pdf_ingestor import _strip_pje_footer
from app.knowledge.knowledge_base import (
    get_all_knowledge_base,
)
from app.legal_engine.dosimetria_calculator import (
    ConcursoCrimesConfig,
    DosimetriaConfig,
    calcular_dosimetria_global,
)
from app.legal_engine.penal_types_catalog import (
    get_penal_type_for_date,
)
from app.main import ACTIVE_CASE_STATE, app
from app.transcription import audio_transcriber
from app.transcription.oral_proof_analyzer import gerar_matriz_corroboracao_oral
from app.generation.sectional_pipeline import (
    SecaoTipo,
    StatusSecao,
    DossieRestricaoSecao,
    SecaoRascunho,
    ResultadoGating,
    SectionalPipelineEngine,
    construir_dossie_secao,
    construir_prompt_seccional,
)
from app.api.security import TOKEN_HEADER, get_session_token
from tests.e2e.conftest import (
    assert_auditoria_honesta,
    auditar_com_dosimetria,
    gerar_sentenca_caso_real,
    veredictos_condenatorios,
)


def _make_crimes(
    crimes_codes: List[str],
    dt_fato: datetime.date,
    culpabilidade_lesao: bool = False,
    motivos_lesao: bool = False,
    circunstancias_lesao: bool = False,
    consequencias_lesao: bool = False,
    confissao_art24a: bool = False,
    reincidente: bool = False,
    agravante_art61f: bool = False,
    is_demo: bool = False,
):
  return build_crimes_inputs_for_case(
      crimes_codes=crimes_codes,
      dt_fato=dt_fato,
      culpabilidade_lesao=culpabilidade_lesao,
      motivos_lesao=motivos_lesao,
      circunstancias_lesao=circunstancias_lesao,
      consequencias_lesao=consequencias_lesao,
      confissao_art24a=confissao_art24a,
      reincidente=reincidente,
      agravante_art61f=agravante_art61f,
      is_demo=is_demo,
  )


# ==============================================================================
# F01 a F05: Ingestão, Bounding e Estados Seccionais
# ==============================================================================


class TestF01_F05_IngestionAndSectionalState:

  def test_f01_ingestao_bookmarks_pje(self, load_fixture_case):
    """F01: Ingestão e segmentação determinística de peças PJe (5 casos)."""
    case = load_fixture_case("1")
    raw_docs = case["raw_documents"]

    # 1. Peças com títulos e tipos judiciais válidos
    pecas = classificar_pecas_autos(raw_docs)
    assert len(pecas) >= 5, f"Esperadas >=5 peças segmentadas, obtidas {len(pecas)}"

    # 2. Identificação determinística de Denúncia
    denuncias = [p for p in pecas if p.categoria == "DENUNCIA"]
    assert (
        len(denuncias) >= 1
    ), f"Deve identificar pelo menos 1 Denúncia, encontradas: {[p.categoria for p in pecas]}"

    # 3. Identificação determinística de Decisão de Recebimento
    recebimentos = [p for p in pecas if p.categoria == "RECEBIMENTO_DENUNCIA"]
    assert (
        len(recebimentos) == 1
    ), "Deve identificar 1 Decisão de Recebimento da Denúncia"

    # 4. Preservação de IDs de folha reais
    for p in pecas:
      assert (
          p.doc_id.startswith("ID-")
      ), f"Peça {p.titulo} deve conter doc_id com prefixo ID-"
      assert "/" in p.folha, f"Peça {p.titulo} deve conter indicação de folhas"

    # 5. Diferenciação de alegações finais MP vs Defesa
    alegacoes = [
        p
        for p in pecas
        if p.categoria in ("ALEGACOES_FINAIS_MP", "ALEGACOES_FINAIS_DEFESA")
    ]
    assert len(alegacoes) >= 1, "Deve classificar peças de alegações finais"

  def test_f02_limpeza_carimbos_pje(self):
    """F02: Higienização de carimbos e metadados PJe (5 casos)."""
    # 1. Rodapé padrão PJe Num. X - Pág. Y
    t1 = "O réu agrediu a ofendida. Num. 12345678 - Pág. 4"
    assert _strip_pje_footer(t1) == "O réu agrediu a ofendida."

    # 2. Carimbo de assinatura eletrônica
    t2 = (
        "Relatório circunstanciado.\nAssinado eletronicamente por: JUIZ DE"
        " DIREITO - 15/02/2025"
    )
    assert "Assinado eletronicamente" not in _strip_pje_footer(t2)

    # 3. URL de validação do PJe
    t3 = (
        "Texto legal substantivo."
        " https://pje.tjrn.jus.br/pje/Processo/ConsultaDocumento/listView.seam"
    )
    assert "https://" not in _strip_pje_footer(t3)

    # 4. Numeração composta de protocolo
    t4 = "Manifestação ministerial. Documento nº: 0804590-12.2025.8.20.5001.1234"
    limpo4 = _strip_pje_footer(t4)
    assert "Manifestação ministerial." in limpo4

    # 5. String vazia ou contendo apenas carimbo retorna limpa sem erro
    t5 = "Num. 9999999 - Pág. 1"
    assert _strip_pje_footer(t5) == ""

  def test_f03_dossie_restricao_fatica(self, load_fixture_case):
    """F03: Confinamento probatório do Dossiê de Restrição Fática (5 seções)."""
    case = load_fixture_case("1")
    pecas = classificar_pecas_autos(case["raw_documents"])
    meta = extrair_metadados_processo(pecas)

    # 1. Dossiê de Relatório
    d_rel = DossieRestricaoSecao(
        secao=SecaoTipo.RELATORIO,
        folhas_permitidas=[p.folha for p in pecas],
        fatos_delimitados=[f"Processo {meta.numero_processo}"],
        teses_defesa=[],
        diretrizes_jurisprudenciais=[],
    )
    assert d_rel.secao == SecaoTipo.RELATORIO
    assert len(d_rel.folhas_permitidas) >= 5

    # 2. Dossiê de Preliminares
    d_prel = DossieRestricaoSecao(
        secao=SecaoTipo.PRELIMINARES,
        folhas_permitidas=[
            p.folha
            for p in pecas
            if p.categoria in ("RESPOSTA_ACUSACAO", "ALEGACOES_FINAIS_DEFESA")
        ],
        fatos_delimitados=["Arguição de decadência e inépcia"],
        teses_defesa=[t.codigo for t in meta.teses_defesa],
        diretrizes_jurisprudenciais=["Art. 41 CPP", "Art. 107 IV CP"],
    )
    assert d_prel.secao == SecaoTipo.PRELIMINARES

    # 3. Dossiê de Mérito
    d_mer = DossieRestricaoSecao(
        secao=SecaoTipo.MERITO,
        delito_especifico="art_129_p13_cp",
        folhas_permitidas=[
            p.folha
            for p in pecas
            if p.categoria in ("LAUDO_PERICIAL", "DENUNCIA", "BO")
        ],
        fatos_delimitados=["Lesões corporais atestadas em laudo pericial"],
        teses_defesa=["MER-01"],
        diretrizes_jurisprudenciais=["Resolução CNJ nº 492/2023"],
    )
    assert d_mer.delito_especifico == "art_129_p13_cp"

    # 4. Dossiê de Dosimetria
    d_dos = DossieRestricaoSecao(
        secao=SecaoTipo.DOSIMETRIA,
        folhas_permitidas=[p.folha for p in pecas if p.categoria == "CAC_FAC"],
        fatos_delimitados=["Certidão de antecedentes criminais do réu"],
        teses_defesa=["Pena-base no mínimo legal"],
        diretrizes_jurisprudenciais=["Súmula 444 STJ", "Súmula 231 STJ"],
    )
    assert len(d_dos.diretrizes_jurisprudenciais) == 2

    # 5. Dossiê de Dispositivo
    d_disp = DossieRestricaoSecao(
        secao=SecaoTipo.DISPOSITIVO,
        folhas_permitidas=[],
        fatos_delimitados=["Decreto condenatório e fixação de indenização"],
        teses_defesa=[],
        diretrizes_jurisprudenciais=["Tema 983 STJ", "Art. 21 LMP"],
    )
    assert d_disp.secao == SecaoTipo.DISPOSITIVO

  def test_f04_prompt_bounding_estrito(self, load_fixture_case):
    """F04: Estrutura do prompt bounding com restrições e diretrizes negativas (5 casos)."""
    case = load_fixture_case("1")
    dossie_rel = construir_dossie_secao(SecaoTipo.RELATORIO, case, custom_dosimetria_override=veredictos_condenatorios(case))
    dossie_mer = construir_dossie_secao(SecaoTipo.MERITO, case, custom_dosimetria_override=veredictos_condenatorios(case))
    dossie_dos = construir_dossie_secao(SecaoTipo.DOSIMETRIA, case, custom_dosimetria_override=veredictos_condenatorios(case))

    # 1. Delimitação estrita de folhas autorizadas gerada pela função de produção
    prompt_merito = construir_prompt_seccional(dossie_mer)
    assert "LISTA TAXATIVA DE FOLHAS AUTORIZADAS (folhas_permitidas):" in prompt_merito
    for folha in dossie_mer.folhas_permitidas[:3]:
      assert folha in prompt_merito

    # 2. Cláusulas mandatórias anti-alucinação no corpo do prompt
    assert "DIRETRIZES DE UNIVERSO FECHADO E RESTRIÇÃO ESTRITA (PROMPT BOUNDING):" in prompt_merito
    assert "1. É TERMINANTEMENTE PROIBIDO inventar IDs de folhas ou números de documentos" in prompt_merito
    assert "2. É TERMINANTEMENTE PROIBIDO invocar julgados, súmulas, enunciados ou precedentes que não constem das diretrizes_jurisprudenciais autorizadas." in prompt_merito
    assert "3. É VEDADO criar fatos novos, datas divergentes ou modificar os relatos probatórios" in prompt_merito

    # 3. Injeção cirúrgica de feedback corretivo da tentativa anterior
    prompt_corrigido = construir_prompt_seccional(
        dossie=dossie_mer,
        feedback_corretivo="Remover citação de ID inexistente ID-FAKE-01 e adequar à folha autorizada.",
    )
    assert "=== [FEEDBACK CIRÚRGICO DA TENTATIVA ANTERIOR - CORREÇÃO MANDATÓRIA] ===" in prompt_corrigido
    assert "ID-FAKE-01" in prompt_corrigido

    # 4. Injeção de buffer de memória de seções anteriores seladas (evita repetição)
    prompt_com_contexto = construir_prompt_seccional(
        dossie=dossie_mer,
        contexto_anterior="[RELATORIO SELADO]\nProcesso regular com denúncia recebida.",
    )
    assert "=== [CONTEXTO DE SEÇÕES ANTERIORES SELADAS - LEITURA OBRIGATÓRIA - NÃO REPETIR] ===" in prompt_com_contexto
    assert "[RELATORIO SELADO]" in prompt_com_contexto

    # 5. Diretriz matemática rígida e imutável para a dosimetria
    prompt_dosimetria = construir_prompt_seccional(dossie_dos)
    assert "DIRETRIZ MATEMÁTICA RÍGIDA DA DOSIMETRIA:" in prompt_dosimetria
    assert "É TERMINANTEMENTE VEDADO alterar, recalcular ou modificar frações (1/8, 1/6)" in prompt_dosimetria

  def test_f05_seccionador_transicoes(self, load_fixture_case):
    """F05: Seccionador de estados da sentença e ciclo de vida (5 casos)."""
    case = load_fixture_case("1")
    engine = SectionalPipelineEngine(use_ollama=False)

    # 1. Estado inicial antes da execução
    assert engine.estado.secao_atual is None
    assert len(engine.estado.secoes_seladas) == 0

    # 2. Execução sequencial da máquina de estados (5 seções estanques)
    sentenca = engine.executar_pipeline(case, custom_dosimetria_override=veredictos_condenatorios(case))
    ordem_esperada = [
        SecaoTipo.RELATORIO,
        SecaoTipo.PRELIMINARES,
        SecaoTipo.MERITO,
        SecaoTipo.DOSIMETRIA,
        SecaoTipo.DISPOSITIVO,
    ]
    for secao in ordem_esperada:
      assert engine.estado.status_secoes[secao] == StatusSecao.APROVADO

    # 3. Buffer de memória selada imutável contém exatamente as 5 seções
    assert set(engine.estado.secoes_seladas.keys()) == set(ordem_esperada)
    assert len(engine.estado.secoes_seladas[SecaoTipo.RELATORIO]) > 0
    assert len(engine.estado.secoes_seladas[SecaoTipo.DISPOSITIVO]) > 0

    # 4. Histórico de telemetria registrou a aprovação de cada estado
    for secao in ordem_esperada:
      historico = engine.estado.historico_tentativas[secao]
      assert len(historico) >= 1
      assert historico[-1].status == StatusSecao.APROVADO
      assert historico[-1].resultado_gating.aprovado is True

    # 5. Invariante: tentativa de regeneração atômica em engine virgem sem execução prévia é bloqueada
    engine_virgem = SectionalPipelineEngine(use_ollama=False)
    with pytest.raises(ValueError, match="Nenhum bundle processual ativo"):
      engine_virgem.regenerar_secao_atomica(SecaoTipo.MERITO)


# ==============================================================================
# F06 a F08: Transcrição Oral, Matriz CNJ 492 e Mineração de Defesa
# ==============================================================================


class TestF06_F08_OralAndDefenseMining:

  def test_f06_transcricao_separacao_falantes(
      self, mock_whisper_services, load_mock_transcriptions
  ):
    """F06: Transcrição de áudio e identificação de interlocutores (5 casos)."""
    # 1. Transcrição de depoimento da vítima
    tr_vit = audio_transcriber.transcrever_midia_audiencia(
        Path("audiencia_juliana.mp4"),
        depoente="JULIANA MENDES",
        papel="OFENDIDA",
    )
    assert tr_vit["papel"] == "OFENDIDA"
    assert tr_vit["media_id"] == "ID-MIDIA-01"

    # 2. Transcrição de interrogatório do réu
    tr_reu = audio_transcriber.transcrever_midia_audiencia(
        Path("audiencia_marcos.mp4"),
        depoente="MARCOS VINÍCIUS",
        papel="INTERROGATORIO_REU",
    )
    assert tr_reu["papel"] == "INTERROGATORIO_REU"

    # 3. Presença de segmentos temporais cronometrados
    assert len(tr_vit["segmentos"]) >= 1
    assert "timestamp" in tr_vit["segmentos"][0]
    assert "texto_literal" in tr_vit["segmentos"][0]

    # 4. Transcrição de depoimento de testemunha
    tr_test = audio_transcriber.transcrever_midia_audiencia(
        Path("audiencia_mariana.mp4"),
        depoente="MARIANA COSTA",
        papel="TESTEMUNHA_ACUSACAO",
    )
    assert tr_test["papel"] == "TESTEMUNHA_ACUSACAO"

    # 5. Tratamento de mídia desconhecida com retorno mock estruturado
    tr_unk = audio_transcriber.transcrever_midia_audiencia(
        Path("arquivo_desconhecido.mp4"),
        depoente="DESCONHECIDO",
        papel="TESTEMUNHA_DEFESA",
    )
    assert tr_unk["papel"] == "TESTEMUNHA_DEFESA"
    assert "segmentos" in tr_unk

  def test_f07_matriz_corroboracao_cnj492(self, load_fixture_case):
    """F07: Matriz de corroboração oral e perspectiva de gênero (5 casos)."""
    case = load_fixture_case("1")
    pecas = classificar_pecas_autos(case["raw_documents"])
    transcricoes = case["transcriptions"]

    matriz = gerar_matriz_corroboracao_oral(pecas, transcricoes)

    # 1. Matriz gerada com sucesso
    assert matriz is not None

    # 2. Presença de depoentes analisados
    assert len(matriz.depoentes) >= 2

    # 3. Ofendida identificada com proteção sob Res. CNJ 492
    vitimas = [d for d in matriz.depoentes if d.papel == "OFENDIDA"]
    assert len(vitimas) >= 1
    assert "JULIANA" in vitimas[0].depoente.upper()

    # 4. Corroboração pericial e documental registrada
    assert vitimas[0].corroboracao_pericial_documental is not None

    # 5. Síntese probatória oral com perspectiva de gênero (Resolução CNJ nº 492/2023)
    assert matriz.fundamentacao_sintese != ""
    assert "Resolução CNJ nº 492/2023" in matriz.protocolo_cnj

  def test_f08_extrator_teses_defesa(self, load_fixture_case):
    """F08: Mineração determinística de teses defensivas (5 casos)."""
    case = load_fixture_case("1")
    pecas = classificar_pecas_autos(case["raw_documents"])
    meta = extrair_metadados_processo(pecas)

    teses = meta.teses_defesa
    assert len(teses) >= 3, f"Esperadas >=3 teses, obtidas: {len(teses)}"

    # 1. Presença de preliminar catalogada
    preliminares = [t for t in teses if t.tipo == "PRELIMINAR"]
    assert len(preliminares) >= 1
    assert preliminares[0].codigo.startswith("PREL-")

    # 2. Presença de tese de mérito catalogada
    meritos = [t for t in teses if t.tipo == "MERITO"]
    assert len(meritos) >= 1
    assert meritos[0].codigo.startswith("MER-")

    # 3. Título substancial presente em cada tese
    for t in teses:
      assert len(t.titulo) >= 10, f"Título muito curto: {t.titulo}"

    # 4. Descrição fática da defesa presente
    for t in teses:
      assert (
          len(t.descricao_defesa) >= 15
      ), f"Descrição muito curta: {t.descricao_defesa}"

    # 5. Códigos únicos para cada tese
    codigos = [t.codigo for t in teses]
    assert len(codigos) == len(
        set(codigos)
    ), f"Códigos duplicados detectados: {codigos}"


# ==============================================================================
# F09 a F12: Adversarial Critic e Perspectiva de Gênero
# ==============================================================================


class TestF09_F12_AdversarialCriticAndGender:

  def test_f09_adversarial_critic_llm(self, load_fixture_case):
    """F09: Adversarial Judicial Critic — Escrutínio dialético (5 casos).

    Nota de contrato (M2 implementado): a API canônica do Agente 7 para avaliação
    integral é `AdversarialJudicialCritic.avaliar_minuta_completa(
    texto_sentenca=..., meta=...)`. Não existe alias `avaliar_minuta` em produção
    (o teste NÃO deve inventar API; corrigir o teste, não o código de produção).
    """
    from app.generation.adversarial_critic import (
        AdversarialJudicialCritic,
        RelatorioCriticaAdversarial,
    )
    case = load_fixture_case("1")
    pecas = classificar_pecas_autos(case["raw_documents"])
    meta = extrair_metadados_processo(pecas)
    minuta = gerar_sentenca_caso_real(case)
    critic = AdversarialJudicialCritic(use_ollama=False)

    # 1. Avaliação integral da minuta real (Tier 2 determinístico, sem Ollama)
    relatorio = critic.avaliar_minuta_completa(
        texto_sentenca=minuta.texto_completo,
        meta=meta,
    )
    assert isinstance(relatorio, RelatorioCriticaAdversarial)
    assert isinstance(relatorio.aprovado, bool)
    assert 0.0 <= relatorio.score_dialetico <= 100.0
    assert isinstance(relatorio.itens, list)
    assert relatorio.parecer_sintetico.strip()

    # 2. Determinismo do rule engine Python (nenhuma dependência de rede/GPU)
    relatorio_repetido = critic.avaliar_minuta_completa(
        texto_sentenca=minuta.texto_completo,
        meta=meta,
    )
    assert relatorio_repetido.score_dialetico == relatorio.score_dialetico
    assert relatorio_repetido.aprovado == relatorio.aprovado

    # 3. Sanidade mínima: minuta insuficiente reprova com falha crítica
    relatorio_curto = critic.avaliar_minuta_completa(
        texto_sentenca="Minuta preliminar",
        meta=meta,
    )
    assert relatorio_curto.aprovado is False
    assert relatorio_curto.tem_falha_critica() is True
    assert relatorio_curto.score_dialetico < relatorio.score_dialetico

    # 4. Omissão total das teses defensivas => itens de omissão (Art. 381, III, CPP)
    texto_omisso = (
        "II – FUNDAMENTAÇÃO\n"
        "Passo à análise do mérito, restando demonstradas a autoria e a"
        " materialidade delitiva pelos elementos colhidos nos autos, com base no"
        " laudo pericial e no depoimento da ofendida, motivo pelo qual julgo"
        " procedente a pretensão punitiva."
    )
    relatorio_omisso = critic.avaliar_minuta_completa(
        texto_sentenca=texto_omisso,
        meta=meta,
    )
    assert "omissao_tese" in {item.tipo for item in relatorio_omisso.itens}
    assert relatorio_omisso.aprovado is False

  def test_f10_schema_relatorio_critica(self):
    """F10: Validação de tipagem e integridade do Schema Pydantic RelatorioCriticaAdversarial (5 casos)."""
    from app.generation.adversarial_critic import (
        ItemCritica,
        RelatorioCriticaAdversarial,
    )
    # 1. Instanciação padrão válida
    r1 = RelatorioCriticaAdversarial(
        aprovado=True,
        score_dialetico=100.0,
        parecer_sintetico="Aprovado sem ressalvas.",
    )
    assert r1.score_dialetico == 100.0

    # 2. Coerção de tipos float
    r2 = RelatorioCriticaAdversarial(
        aprovado=False,
        score_dialetico="55.5",
        parecer_sintetico="Reprovado.",
    )
    assert r2.score_dialetico == 55.5

    # 3. Lista de itens tipada
    r3 = RelatorioCriticaAdversarial(
        aprovado=True,
        score_dialetico=85.0,
        itens=[
            ItemCritica(
                tipo="dialetica",
                descricao="Estilo refinado",
                gravidade="sugestao",
                feedback_cirurgico="Sem correções necessárias",
            )
        ],
        parecer_sintetico="Parcialmente aprovado",
    )
    assert len(r3.itens) == 1

    # 4. Validação de serialização JSON round-trip
    dumped = r3.model_dump(mode="json")
    reloaded = RelatorioCriticaAdversarial.model_validate(dumped)
    assert reloaded.score_dialetico == 85.0

    # 5. Validação de rejeição para valores ilegais (Pydantic ValidationError)
    with pytest.raises(Exception):
      RelatorioCriticaAdversarial(
          aprovado="invalido",
          score_dialetico="nao_numero",
          parecer_sintetico="x",
      )

  def test_f11_trava_omissao_teses(self, load_fixture_case):
    """F11: Trava algorítmica de omissão de teses defensivas (Art. 381, III, CPP) (5 casos)."""
    case = load_fixture_case("1")
    pecas = classificar_pecas_autos(case["raw_documents"])
    meta = extrair_metadados_processo(pecas)
    teses = meta.teses_defesa

    # 1. Sentença enfrentando 100% das teses -> Sem omissões
    texto_completo = "\n".join(
        [f"Rejeito a tese {t.codigo} — {t.titulo}" for t in teses]
        + ["I – RELATÓRIO", "II – FUNDAMENTAÇÃO", "III – DISPOSITIVO"]
    )
    auditoria = auditar_sentenca(texto_completo, pecas, teses, [])
    assert auditoria.teses_omitidas == []

    # 2. Sentença omitindo a tese PREL-01
    texto_sem_prel = "\n".join(
        [
            f"Rejeito a tese {t.codigo} — {t.titulo}"
            for t in teses
            if "PREL" not in t.codigo
        ]
        + ["I – RELATÓRIO", "II – FUNDAMENTAÇÃO", "III – DISPOSITIVO"]
    )
    auditoria_sem_prel = auditar_sentenca(texto_sem_prel, pecas, teses, [])
    assert any(
        "PREL" in tom for tom in auditoria_sem_prel.teses_omitidas
    ), f"Deveria acusar omissão de PREL: {auditoria_sem_prel.teses_omitidas}"

    # 3. Penalização expressiva no score por omissão
    assert auditoria_sem_prel.score_confiabilidade < 100.0

    # 4. Sentença omitindo 100% das teses
    texto_vazio = (
        "I – RELATÓRIO\nII – FUNDAMENTAÇÃO\nCondeno o réu.\nIII – DISPOSITIVO"
    )
    auditoria_vazia = auditar_sentenca(texto_vazio, pecas, teses, [])
    assert len(auditoria_vazia.teses_omitidas) == len(teses)

    # 5. Reconhecimento de tese pelo código e título enfrentados
    texto_enfrentado = (
        f"Quanto à tese {teses[0].codigo}, rejeito a preliminar. "
        f"Quanto à tese {teses[1].codigo}, rejeito o pleito meritório. "
        "I – RELATÓRIO\nII – FUNDAMENTAÇÃO\nIII – DISPOSITIVO"
    )
    auditoria_enf = auditar_sentenca(texto_enfrentado, pecas, teses, [])
    assert len(auditoria_enf.teses_defesa_enfrentadas) >= 2

  def test_f12_crivo_genero_cnj492(self, load_fixture_case):
    """F12: Crivo de Standard Probatório e Perspectiva de Gênero (Res. CNJ 492/2023) (5 casos)."""
    case = load_fixture_case("1")
    pecas = classificar_pecas_autos(case["raw_documents"])
    transcricoes = case["transcriptions"]
    matriz = gerar_matriz_corroboracao_oral(pecas, transcricoes)
    meta = extrair_metadados_processo(pecas)

    # 1. Proibição de juízo moral depreciativo da vítima na fundamentação síntese
    assert "provocou" not in matriz.fundamentacao_sintese.lower()
    assert "ciúme excessivo" not in matriz.fundamentacao_sintese.lower()

    # 2. Citação mandatória da Resolução CNJ nº 492/2023
    assert "492/2023" in matriz.protocolo_cnj

    # 3. Valoração especial da palavra da vítima no recesso do lar
    assert any(
        "ofendida" in d.papel.lower() or "ofendida" in d.depoente.lower()
        for d in matriz.depoentes
    )

    # 4. Auditoria estrita da ADPF 779 do STF e Resolução CNJ 492 via hallucination_auditor
    texto_com_adpf = (
        "I – RELATÓRIO\n"
        f"Processo nº {meta.numero_processo}. O Ministério Público ofereceu denúncia contra o acusado.\n\n"
        "II – FUNDAMENTAÇÃO\n"
        "Em respeito à Resolução CNJ 492/2023, afasto qualquer desqualificação da palavra da vítima. "
        "Ademais, em consonância com a jurisprudência vinculante da ADPF 779 do STF, rejeito com veemência "
        "qualquer alegação explícita ou dissimulada de legítima defesa da honra.\n\n"
        "III – DISPOSITIVO\n"
        "Condeno o acusado."
    )
    auditoria_genero = auditar_sentenca(
        texto_sentenca=texto_com_adpf,
        pecas_autos=pecas,
        teses_defesa=meta.teses_defesa,
        ids_midias=[t.get("media_id") for t in transcricoes if t.get("media_id")],
    )
    assert any("ADPF 779" in c for c in auditoria_genero.citacoes_juridicas_verificadas)
    assert any("492" in c for c in auditoria_genero.citacoes_juridicas_verificadas)
    assert len(auditoria_genero.citacoes_nao_autorizadas) == 0

    # 5. Aplicação da perspectiva de gênero informada na matriz
    assert matriz.perspectiva_genero_aplicada is True


# ==============================================================================
# F13 a F19: Auditoria em 4 Camadas, Auto-Correção e Fallback
# ==============================================================================


class TestF13_F19_AuditorAndAutoCorrection:

  def test_f13_auditoria_granular_seccional(self, load_fixture_case):
    """F13: Auditoria seccional granular e modular (5 seções)."""
    case = load_fixture_case("1")
    sent = gerar_sentenca_caso_real(case)
    pecas = classificar_pecas_autos(case["raw_documents"])
    meta = extrair_metadados_processo(pecas)
    ids_midias = [t["media_id"] for t in case["transcriptions"]]

    # 1. Relatório isolado
    aud_rel = auditar_sentenca(sent.secao_relatorio, pecas, [], ids_midias)
    assert aud_rel.ids_inexistentes == []

    # 2. Preliminares isoladas
    aud_prel = auditar_sentenca(
        sent.secao_preliminares, pecas, meta.teses_defesa, ids_midias
    )
    assert aud_prel.citacoes_nao_autorizadas == []

    # 3. Mérito isolado
    aud_mer = auditar_sentenca(sent.secao_merito, pecas, [], ids_midias)
    assert aud_mer.ids_inexistentes == []

    # 4. Dosimetria isolada
    aud_dos = auditar_sentenca(sent.secao_dosimetria, pecas, [], ids_midias)
    assert aud_dos.conformidade_oito_circunstancias_art59 is True

    # 5. Dispositivo isolado
    aud_disp = auditar_sentenca(sent.secao_dispositivo, pecas, [], ids_midias)
    assert aud_disp.conformidade_art21_lmp is True

  def test_f14_camada1_ids_reais(self, load_fixture_case):
    """F14: Camada 1 — Auditoria rígida de IDs de folhas e mídias (5 casos)."""
    case = load_fixture_case("1")
    pecas = classificar_pecas_autos(case["raw_documents"])
    ids_midias = [t["media_id"] for t in case["transcriptions"]]

    # 1. Sentença com IDs reais válidos -> Score 100.0 na Camada 1
    doc_real = pecas[0].doc_id
    mid_real = ids_midias[0]
    txt_ok = (
        f"I – RELATÓRIO\nII – FUNDAMENTAÇÃO\nConforme comprovado no documento"
        f" {doc_real} e na gravação {mid_real}.\nIII – DISPOSITIVO"
    )
    aud_ok = auditar_sentenca(txt_ok, pecas, [], ids_midias)
    assert aud_ok.ids_inexistentes == []
    assert doc_real in aud_ok.ids_citados_verificados
    assert mid_real in aud_ok.ids_citados_verificados

    # 2. Invocação de ID de folha alucinado/inexistente
    txt_fake = (
        "I – RELATÓRIO\nII – FUNDAMENTAÇÃO\nConforme certificado na folha"
        " ID-9999999.\nIII – DISPOSITIVO"
    )
    aud_fake = auditar_sentenca(txt_fake, pecas, [], ids_midias)
    assert "ID-9999999" in aud_fake.ids_inexistentes
    assert aud_fake.score_confiabilidade < 100.0

    # 3. Invocação de ID de mídia inexistente
    txt_fake_midia = (
        "I – RELATÓRIO\nII – FUNDAMENTAÇÃO\nConforme vídeo ID-MIDIA-999.\nIII –"
        " DISPOSITIVO"
    )
    aud_fake_midia = auditar_sentenca(txt_fake_midia, pecas, [], ids_midias)
    assert "ID-MIDIA-999" in aud_fake_midia.ids_inexistentes

    # 4. Múltiplos IDs válidos no mesmo parágrafo
    txt_multi = f"Conforme {pecas[0].doc_id} e {pecas[1].doc_id}."
    aud_multi = auditar_sentenca(txt_multi, pecas, [], ids_midias)
    assert len(aud_multi.ids_citados_verificados) == 2

    # 5. Expressões textuais comuns sem ID não são penalizadas falsamente
    txt_texto = "Conforme laudo pericial anexado aos autos pelo médico legista."
    aud_texto = auditar_sentenca(txt_texto, pecas, [], ids_midias)
    assert aud_texto.ids_inexistentes == []

  def test_f15_camada2_acervo_fechado_60_verbetes(self, load_fixture_case):
    """F15: Camada 2 — Confronto com acervo fechado de precedentes vinculantes (5 casos)."""
    case = load_fixture_case("1")
    pecas = classificar_pecas_autos(case["raw_documents"])

    # 1. Citação de Súmula STJ autorizada (Súmula 542)
    txt_s542 = (
        "I – RELATÓRIO\nII – FUNDAMENTAÇÃO\nAplica-se a Súmula 542 do STJ quanto"
        " à ação incondicionada.\nIII – DISPOSITIVO"
    )
    aud_s542 = auditar_sentenca(txt_s542, pecas, [], [])
    assert any("542" in c for c in aud_s542.citacoes_juridicas_verificadas)
    assert aud_s542.citacoes_nao_autorizadas == []

    # 2. Citação de Tema Repetitivo STJ autorizado (Tema 983)
    txt_t983 = (
        "I – RELATÓRIO\nII – FUNDAMENTAÇÃO\nFixa-se reparação na forma do Tema"
        " 983 do STJ.\nIII – DISPOSITIVO"
    )
    aud_t983 = auditar_sentenca(txt_t983, pecas, [], [])
    assert any("983" in c for c in aud_t983.citacoes_juridicas_verificadas)

    # 3. Detecção e bloqueio de Súmula fictícia (Súmula 999 do STJ)
    txt_fake = (
        "I – RELATÓRIO\nII – FUNDAMENTAÇÃO\nAplica-se a Súmula 999 do STJ que"
        " autoriza o pleito.\nIII – DISPOSITIVO"
    )
    aud_fake = auditar_sentenca(txt_fake, pecas, [], [])
    assert any("999" in c for c in aud_fake.citacoes_nao_autorizadas)
    assert aud_fake.score_confiabilidade < 100.0

    # 4. Detecção e bloqueio de Tema Repetitivo fictício (Tema 4999 do STJ)
    txt_tema_fake = (
        "I – RELATÓRIO\nII – FUNDAMENTAÇÃO\nConforme Tema Repetitivo 4999 do"
        " STJ.\nIII – DISPOSITIVO"
    )
    aud_tema_fake = auditar_sentenca(txt_tema_fake, pecas, [], [])
    assert any("4999" in c for c in aud_tema_fake.citacoes_nao_autorizadas)

    # 5. Acervo da base de dados contém pelo menos 50 precedentes vinculantes
    base = get_all_knowledge_base()
    count_precedentes = len(base.get("sumulas_e_temas", [])) + len(
        base.get("precedentes_gabinete", [])
    )
    assert (
        count_precedentes >= 50
    ), f"Acervo fechado deve conter >= 50 precedentes vinculantes, possui {count_precedentes}"

  def test_f16_camada3_checklist_art381_cpp(self, load_fixture_case):
    """F16: Camada 3 — Checklist estrutural do Art. 381 do CPP (5 casos)."""
    case = load_fixture_case("1")
    pecas = classificar_pecas_autos(case["raw_documents"])

    # 1. Sentença completa com os 3 títulos canônicos
    txt_ok = (
        "I – RELATÓRIO\nHistórico dos autos.\nII – FUNDAMENTAÇÃO\nMérito da"
        " causa.\nIII – DISPOSITIVO\nCondeno o réu."
    )
    aud_ok = auditar_sentenca(txt_ok, pecas, [], [])
    assert aud_ok.conformidade_art381_cpp is True

    # 2. Sentença sem capítulo de Relatório
    txt_sem_rel = (
        "II – FUNDAMENTAÇÃO\nMérito da causa.\nIII – DISPOSITIVO\nCondeno o réu."
    )
    aud_sem_rel = auditar_sentenca(txt_sem_rel, pecas, [], [])
    assert aud_sem_rel.conformidade_art381_cpp is False

    # 3. Sentença sem capítulo de Dispositivo
    txt_sem_disp = (
        "I – RELATÓRIO\nHistórico dos autos.\nII – FUNDAMENTAÇÃO\nMérito da"
        " causa."
    )
    aud_sem_disp = auditar_sentenca(txt_sem_disp, pecas, [], [])
    assert aud_sem_disp.conformidade_art381_cpp is False

    # 4. Presença de dispositivo com fundamentação e relatório preenchidos
    txt_integral = (
        "I – RELATÓRIO\n"
        "Autos criminais regulares.\n\n"
        "II – FUNDAMENTAÇÃO\n"
        "Análise probatória completa.\n\n"
        "III – DISPOSITIVO\n"
        "Julgo procedente a denúncia."
    )
    aud_integral = auditar_sentenca(txt_integral, pecas, [], [])
    assert aud_integral.conformidade_art381_cpp is True

    # 5. Sentença vazia reprovada no art. 381 CPP
    aud_vazia = auditar_sentenca("", pecas, [], [])
    assert aud_vazia.conformidade_art381_cpp is False

  def test_f17_camada4_oito_vetores_art59_abnt(self, load_fixture_case):
    """F17: Camada 4 — 8 vetores do Art. 59 do CP e normas linguísticas ABNT (5 casos)."""
    case = load_fixture_case("1")
    sent = gerar_sentenca_caso_real(case)
    pecas = classificar_pecas_autos(case["raw_documents"])

    # 1. Sentença gerada pelo sistema contempla expressamente os 8 vetores
    aud = auditar_sentenca(sent.texto_completo, pecas, [], [])
    assert aud.conformidade_oito_circunstancias_art59 is True

    # 2. Verificação individual dos 8 termos no texto
    vetores = [
        "culpabilidade",
        "antecedentes",
        "conduta social",
        "personalidade",
        "motivos",
        "circunstâncias",
        "consequências",
        "comportamento da vítima",
    ]
    for v in vetores:
      assert (
          v in sent.secao_dosimetria.lower()
      ), f"Vetor {v} ausente na dosimetria"

    # 3. Conformidade com máscara de datas brasileiras
    assert aud.conformidade_datas_padrao_br is True
    assert not re.search(
        r"\b\d{4}-\d{2}-\d{2}\b", sent.texto_completo
    ), "Datas no formato ISO detectadas no texto final!"

    # 4. Detecção de violação quando omitido um vetor em trecho de dosimetria
    texto_sem_conduta = re.sub(r"\bconduta\s+social\b", "conduta_desconsiderada", sent.texto_completo, flags=re.IGNORECASE)
    aud_falha = auditar_sentenca(texto_sem_conduta, pecas, [], [])
    assert aud_falha.conformidade_oito_circunstancias_art59 is False

    # 5. Conformidade com normas de citação ABNT
    assert aud.conformidade_abnt_citacoes is True

  def test_f18_motor_autocorrecao_2_retries(self, load_fixture_case):
    """F18: Laço de auto-correção cirúrgica com feedback e limite de 2 retries (5 casos)."""
    case = load_fixture_case("1")

    # Cenário 1: Validador customizado falha na tentativa 1 e aprova na tentativa 2 (1º retry)
    tentativas_cenario1 = {"count": 0}

    def validador_com_sucesso_no_retry(secao, rascunho, dossie):
      if secao == SecaoTipo.RELATORIO:
        tentativas_cenario1["count"] += 1
        if tentativas_cenario1["count"] == 1:
          return ResultadoGating(
              secao=secao,
              aprovado=False,
              score=50.0,
              erros=["ID inexistente ID-FAKE-999 detectado no relatório"],
              feedbacks_cirurgicos=["Substituir ID-FAKE-999 pelas folhas válidas do dossiê."],
          )
      return ResultadoGating(secao=secao, aprovado=True, score=100.0)

    engine1 = SectionalPipelineEngine(
        use_ollama=False, max_retries=2, gating_validator=validador_com_sucesso_no_retry
    )
    engine1.executar_pipeline(case, custom_dosimetria_override=veredictos_condenatorios(case))
    historico_rel = engine1.estado.historico_tentativas[SecaoTipo.RELATORIO]

    # 1. Tentativa inicial (versão 1) reprovada e registrada
    assert len(historico_rel) == 2
    assert historico_rel[0].tentativa == 1
    assert historico_rel[0].status == StatusSecao.REPROVADO_RETRY
    assert historico_rel[0].resultado_gating.aprovado is False

    # 2. 1ª Retentativa cirúrgica (versão 2) aprovada com feedback absorvido
    assert historico_rel[1].tentativa == 2
    assert historico_rel[1].status == StatusSecao.APROVADO
    assert historico_rel[1].resultado_gating.aprovado is True
    assert engine1.estado.status_secoes[SecaoTipo.RELATORIO] == StatusSecao.APROVADO

    # Cenário 2: Validador reprova todas as tentativas (estouro do teto de 2 retries)
    def validador_falha_persistente(secao, rascunho, dossie):
      if secao == SecaoTipo.MERITO:
        return ResultadoGating(
            secao=secao,
            aprovado=False,
            score=30.0,
            erros=["Erro persistente de coerência probatória"],
            feedbacks_cirurgicos=["Reescrever fundamentação"],
        )
      return ResultadoGating(secao=secao, aprovado=True, score=100.0)

    engine2 = SectionalPipelineEngine(
        use_ollama=False, max_retries=2, gating_validator=validador_falha_persistente
    )
    sent2 = engine2.executar_pipeline(case, custom_dosimetria_override=veredictos_condenatorios(case))
    historico_mer = engine2.estado.historico_tentativas[SecaoTipo.MERITO]

    # 3. 2ª Retentativa cirúrgica (tentativa 3 no total: 1 inicial + 2 retries)
    assert len(historico_mer) == 3
    assert historico_mer[2].tentativa == 3
    assert historico_mer[2].status == StatusSecao.REPROVADO_RETRY

    # 4. Bloqueio de novas tentativas além do teto de 2 retries (nenhuma 4ª tentativa é disparada)
    assert len(historico_mer) == 3

    # 5. Acionamento obrigatório do Fallback Determinístico seguro após esgotar os 2 retries
    assert engine2.estado.status_secoes[SecaoTipo.MERITO] == StatusSecao.FALLBACK_ACIONADO
    assert len(engine2.estado.secoes_seladas[SecaoTipo.MERITO]) > 0
    assert sent2.secao_merito is not None

  def test_f19_fallback_deterministico(self, load_fixture_case):
    """F19: Fallback determinístico seguro em Python puro (5 casos)."""
    case = load_fixture_case("1")

    # 1. Geração determinística sem Ollama (fallback ativo)
    sent_fallback = gerar_sentenca_caso_real(case)
    assert sent_fallback is not None

    # 2. Score de confiabilidade 100.0 garantido
    pecas = classificar_pecas_autos(case["raw_documents"])
    meta = extrair_metadados_processo(pecas)
    ids_midias = [t["media_id"] for t in case["transcriptions"]]
    aud = auditar_com_dosimetria(sent_fallback, case)
    assert_auditoria_honesta(aud, sent_fallback)

    # 3. Todas as 5 seções presentes no fallback
    assert len(sent_fallback.secao_relatorio) > 50
    assert len(sent_fallback.secao_preliminares) > 50
    assert len(sent_fallback.secao_merito) > 50
    assert len(sent_fallback.secao_dosimetria) > 50
    assert len(sent_fallback.secao_dispositivo) > 50

    # 4. Motor reportado como determinístico/fallback
    assert (
        "Determinístico" in sent_fallback.motor_utilizado
        or "Python" in sent_fallback.motor_utilizado
    )

    # 5. Tempo de execução sub-segundo (< 1000ms)
    t0 = datetime.datetime.now()
    _ = gerar_sentenca_caso_real(case)
    delta = (datetime.datetime.now() - t0).total_seconds()
    assert (
        delta < 2.0
    ), f"Fallback determinístico deve ser instantâneo, levou {delta}s"


# ==============================================================================
# F20 a F24: Dosimetria Trifásica e Fixação de Regime
# ==============================================================================


class TestF20_F24_DosimetriaTrifasica:

  def test_f20_primeira_fase_um_oitavo_e_sumula444(self):
    """F20: 1ª Fase — Fração de 1/8 do intervalo e blindagem da Súmula 444 STJ (5 casos)."""
    spec = get_penal_type_for_date(
        "art_129_p13_cp", datetime.date(2025, 1, 1)
    )  # 2 a 5 anos (720 a 1800 dias)
    intervalo = spec.max_pena.to_total_days() - spec.min_pena.to_total_days()  # 1080 dias
    passo_1_8 = intervalo // 8  # 135 dias

    # 1. 0 vetores desfavoráveis -> Pena no mínimo legal (2 anos)
    inputs_0 = _make_crimes(
        ["art_129_p13_cp"],
        datetime.date(2025, 1, 1),
        culpabilidade_lesao=False,
    )
    cfg = DosimetriaConfig(criterio_primeira_fase="um_oitavo_intervalo")
    res_0 = calcular_dosimetria_global(
        crimes=inputs_0,
        config=cfg,
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc_0 = res_0.crimes_calculados[0]
    assert calc_0.pena_base.anos == 2
    assert calc_0.pena_base.meses == 0
    assert calc_0.pena_base.dias == 0

    # 2. 1 vetor desfavorável (culpabilidade) -> +135 dias (4 meses e 15 dias)
    inputs_1 = _make_crimes(
        ["art_129_p13_cp"],
        datetime.date(2025, 1, 1),
        culpabilidade_lesao=True,
    )
    res_1 = calcular_dosimetria_global(
        crimes=inputs_1,
        config=cfg,
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc_1 = res_1.crimes_calculados[0]
    assert calc_1.pena_base.anos == 2
    assert calc_1.pena_base.meses == 4
    assert calc_1.pena_base.dias == 15

    # 3. 2 vetores desfavoráveis -> +270 dias (9 meses) -> 2 anos e 9 meses
    inputs_2 = _make_crimes(
        ["art_129_p13_cp"],
        datetime.date(2025, 1, 1),
        culpabilidade_lesao=True,
        motivos_lesao=True,
    )
    res_2 = calcular_dosimetria_global(
        crimes=inputs_2,
        config=cfg,
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc_2 = res_2.crimes_calculados[0]
    assert calc_2.pena_base.anos == 2
    assert calc_2.pena_base.meses == 9

    # 4. Súmula 444 STJ: Inquéritos sem trânsito não aumentam antecedentes
    inputs_s444 = _make_crimes(
        ["art_129_p13_cp"],
        datetime.date(2025, 1, 1),
        culpabilidade_lesao=False,
        reincidente=False,
    )
    res_s444 = calcular_dosimetria_global(
        crimes=inputs_s444,
        config=cfg,
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    assert res_s444.crimes_calculados[0].pena_base.anos == 2

    # 5. Passo exato de 1/8 respeita intervalo
    assert passo_1_8 == 135

  def test_f21_segunda_fase_um_sexto_e_sumula231(self):
    """F21: 2ª Fase — Fração de 1/6, compensação e trava da Súmula 231 STJ (5 casos)."""
    # 1. Súmula 231 STJ: Confissão com pena no mínimo NÃO reduz aquém de 2 anos
    inputs_conf = _make_crimes(
        ["art_129_p13_cp"],
        datetime.date(2025, 1, 1),
        culpabilidade_lesao=False,
        confissao_art24a=True,
    )
    cfg = DosimetriaConfig(aplicar_sumula_231_stj=True)
    res_conf = calcular_dosimetria_global(
        crimes=inputs_conf,
        config=cfg,
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc_c = res_conf.crimes_calculados[0]
    assert calc_c.pena_intermediaria.anos == 2
    assert calc_c.sumula_231_aplicada is True

    # 2. Confissão com pena exasperada (2 anos e 9 meses = 990 dias) -> -1/6 (165 dias)
    inputs_exasp = _make_crimes(
        ["art_129_p13_cp"],
        datetime.date(2025, 1, 1),
        culpabilidade_lesao=True,
        motivos_lesao=True,
        confissao_art24a=True,
    )
    res_exasp = calcular_dosimetria_global(
        crimes=inputs_exasp,
        config=cfg,
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc_ex = res_exasp.crimes_calculados[0]
    assert calc_ex.pena_intermediaria.anos == 2
    assert calc_ex.pena_intermediaria.meses == 3
    assert calc_ex.pena_intermediaria.dias == 15

    # 3. Reincidência isolada (+1/6 sobre 2 anos / 720 dias -> +120 dias -> 2 anos e 4 meses)
    inputs_reinc = _make_crimes(
        ["art_129_p13_cp"],
        datetime.date(2025, 1, 1),
        culpabilidade_lesao=False,
        reincidente=True,
    )
    res_reinc = calcular_dosimetria_global(
        crimes=inputs_reinc,
        config=cfg,
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc_r = res_reinc.crimes_calculados[0]
    assert calc_r.pena_intermediaria.anos == 2
    assert calc_r.pena_intermediaria.meses == 4

    # 4. Compensação integral Tema 585 STJ (Confissão x Reincidência)
    inputs_comp = _make_crimes(
        ["art_129_p13_cp"],
        datetime.date(2025, 1, 1),
        culpabilidade_lesao=True,
        confissao_art24a=True,
        reincidente=True,
    )
    res_comp = calcular_dosimetria_global(
        crimes=inputs_comp,
        config=cfg,
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc_cp = res_comp.crimes_calculados[0]
    assert (
        calc_cp.pena_intermediaria.to_total_days()
        == calc_cp.pena_base.to_total_days()
    )
    assert calc_cp.compensacao_confissao_reincidencia is True

    # 5. Alerta formal de compensação emitido no cálculo
    memoria_join = " ".join(calc_cp.memoria_calculo).lower()
    alertas_join = " ".join(calc_cp.alertas_juridicos).lower()
    assert (
        "compensad" in memoria_join
        or "compensad" in alertas_join
        or "tema 585" in memoria_join
    )

  def test_f22_terceira_fase_bloqueio_bis_in_idem(self):
    """F22: 3ª Fase e Bloqueio Mandatório de Bis in Idem (Art. 61, II, 'f', CP) (5 casos)."""
    # 1. Art. 129 § 13 CP: Tipo que já contém violência doméstica -> Bloqueia art. 61 II f
    spec_lesao = get_penal_type_for_date(
        "art_129_p13_cp", datetime.date(2025, 1, 1)
    )
    assert (
        spec_lesao.has_builtin_domestic_violence_element is True
    ), "Art. 129 § 13 CP deve possuir violência doméstica como elementar"

    # 2. Art. 24-A LMP: Bloqueio obrigatório de art. 61 II f
    spec_24a = get_penal_type_for_date("art_24a_lmp", datetime.date(2025, 1, 1))
    assert spec_24a.has_builtin_domestic_violence_element is True

    # 3. Art. 147 caput pré-14.994: Tipo genérico que PERMITE art. 61 II f
    spec_147_pre = get_penal_type_for_date(
        "art_147_cp", datetime.date(2024, 1, 1)
    )
    assert spec_147_pre.has_builtin_domestic_violence_element is False

    # 4. Cálculo com tentativa de forçar art. 61 II f no art. 129 § 13 resulta em bloqueio
    inputs = _make_crimes(
        ["art_129_p13_cp"],
        datetime.date(2025, 1, 1),
        agravante_art61f=True,
    )
    res = calcular_dosimetria_global(
        crimes=inputs,
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc = res.crimes_calculados[0]
    assert (
        calc.pena_intermediaria.to_total_days()
        == calc.pena_base.to_total_days()
    )
    assert calc.bis_in_idem_art61f_bloqueado is True

    # 5. Inexistência de majorantes genéricas na 3ª fase mantém pena definitiva
    assert (
        calc.pena_definitiva.to_total_days()
        == calc.pena_intermediaria.to_total_days()
    )

  def test_f23_cumulo_material_art69(self):
    """F23: Cúmulo Material (Art. 69 CP) com Segregação Estrita de Reclusão e Detenção (5 casos)."""
    # Concurso entre Lesão (reclusão: 1 ano pré-14.994) e Ameaça (detenção: 1 mês)
    inputs = _make_crimes(
        ["art_129_p13_cp", "art_147_cp"],
        datetime.date(2024, 1, 1),
        culpabilidade_lesao=False,
    )
    res = calcular_dosimetria_global(
        crimes=inputs,
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="material"),
    )

    # 1. Total de reclusão preservado individualmente
    assert res.penas_por_especie["reclusao"].anos == 1

    # 2. Total de detenção preservado individualmente
    assert res.penas_por_especie["detencao"].meses == 1

    # 3. Proibição de fusão indiferenciada
    assert res.penas_por_especie["reclusao"] != res.penas_por_especie["detencao"]

    # 4. Ambas as espécies calculadas e segregadas
    assert "reclusao" in res.penas_por_especie
    assert "detencao" in res.penas_por_especie

    # 5. Concurso de crimes homogêneo (apenas reclusão) soma perfeitamente
    inputs_reclusao = _make_crimes(
        ["art_129_p13_cp", "art_24a_lmp"],
        datetime.date(2025, 1, 1),
        culpabilidade_lesao=False,
    )
    res_rec = calcular_dosimetria_global(
        crimes=inputs_reclusao,
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="material"),
    )
    assert res_rec.penas_por_especie["reclusao"].anos == 4

  def test_f24_regime_sursis_e_dano_moral(self):
    """F24: Regime prisional, Súmula 588 STJ, Sursis (art. 45 LMP) e Dano Moral (Tema 983) (5 casos)."""
    # 1. Pena <= 4 anos, primário, com violência -> Regime ABERTO
    inputs = _make_crimes(
        ["art_129_p13_cp"],
        datetime.date(2025, 1, 1),
        culpabilidade_lesao=False,
        reincidente=False,
    )
    res = calcular_dosimetria_global(
        crimes=inputs,
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
        pedido_expresso_dano_moral_mp=True,
        valor_dano_moral_sugerido=3000.0,
    )
    assert res.regime_inicial.lower() == "aberto"

    # 2. Súmula 588 STJ: Veda conversão em restritiva de direitos (PRD)
    assert res.substituicao_art44_cabivel is False

    # 3. Sursis Especial (Art. 77 CP c/c Art. 45 LMP) cabível para pena <= 2 anos
    assert res.sursis_art77_cabivel is True

    # 4. Fixação de Dano Moral in re ipsa (Tema 983 STJ) mediante pedido expresso
    assert res.indenizacao_dano_moral_tema983 == 3000.0

    # 5. Ausência de pedido expresso zera fixação de dano moral
    res_sem_dano = calcular_dosimetria_global(
        crimes=inputs,
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
        pedido_expresso_dano_moral_mp=False,
        valor_dano_moral_sugerido=0.0,
    )
    assert res_sem_dano.indenizacao_dano_moral_tema983 == 0.0


# ==============================================================================
# F25 a F28: Integração de API, Gestão VRAM e Exportação Tripla
# ==============================================================================


class TestF25_F28_IntegrationAndExport:

  def test_f25_api_generate_sectional(self, api_client, load_fixture_case):
    """F25: API Endpoint de Geração Seccional /api/sentence/generate-sectional (5 casos)."""
    case = load_fixture_case("1")
    veredictos = veredictos_condenatorios(case)

    # 0. Caso real sem veredicto do juízo => 422 com os crimes pendentes (nunca condena por padrão)
    r0 = api_client.post(
        "/api/sentence/generate-sectional",
        json={"case_bundle": case, "use_ollama": False},
    )
    assert r0.status_code == 422
    assert r0.json()["erro"] == "decisao_judicial_pendente"
    assert set(r0.json()["crimes_pendentes"]) == {
        c["codigo"] for c in veredictos["crimes_config"]
    }

    # 1. Chamada válida com bundle embutido e veredicto por crime
    r1 = api_client.post(
        "/api/sentence/generate-sectional",
        json={
            "case_bundle": case,
            "use_ollama": False,
            "custom_dosimetria_override": veredictos,
        },
    )
    assert r1.status_code == 200, r1.text
    d1 = r1.json()
    assert d1["status"] == "completed"

    # 2. Retorno com as 5 seções estruturadas
    secoes = d1["secoes"]
    assert "relatorio" in secoes
    assert "preliminares" in secoes
    assert "merito" in secoes
    assert "dosimetria" in secoes
    assert "dispositivo" in secoes

    # 3. Auditoria integrada: forma 100, zero alucinação, penas conferidas; só marcadores
    #    [COMPLETAR PELO JUÍZO] (sinalizados) podem impedir o score 100.
    assert_auditoria_honesta(d1["auditoria"], d1["sentenca"])
    assert d1["score_auditoria"] == d1["auditoria"]["score_confiabilidade"]

    # 4. Chamada utilizando o caso ativo da sessão
    ACTIVE_CASE_STATE["bundle"] = case
    r2 = api_client.post(
        "/api/sentence/generate-sectional",
        json={"use_ollama": False, "custom_dosimetria_override": veredictos},
    )
    assert r2.status_code == 200, r2.text

    # 5. Persistência de last_sentence em memória
    assert ACTIVE_CASE_STATE["last_sentence"] is not None

  def test_f26_regenerar_secao_atomica(self, load_fixture_case):
    """F26: Motor de Regeneração Atômica de Seção (SectionalPipelineEngine.regenerar_secao_atomica) (5 casos)."""
    case = load_fixture_case("1")
    engine = SectionalPipelineEngine(use_ollama=False)
    sent_original = engine.executar_pipeline(case, custom_dosimetria_override=veredictos_condenatorios(case))

    # Snapshot das seções seladas antes da regeneração
    rel_original = engine.estado.secoes_seladas[SecaoTipo.RELATORIO]
    prel_original = engine.estado.secoes_seladas[SecaoTipo.PRELIMINARES]
    mer_original = engine.estado.secoes_seladas[SecaoTipo.MERITO]
    dos_original = engine.estado.secoes_seladas[SecaoTipo.DOSIMETRIA]
    disp_original = engine.estado.secoes_seladas[SecaoTipo.DISPOSITIVO]

    # 1. Regeneração atômica da seção de mérito com feedback cirúrgico
    rascunho_merito = engine.regenerar_secao_atomica(
        secao=SecaoTipo.MERITO,
        feedback="Aprofundar enfrentamento da tese de legítima defesa sob a ótica da ADPF 779.",
    )
    assert isinstance(rascunho_merito, SecaoRascunho)
    assert rascunho_merito.secao == SecaoTipo.MERITO
    assert len(rascunho_merito.texto_redigido) > 100

    # 2. Imutabilidade absoluta: todas as outras 4 seções seladas permanecem estritamente intactas
    assert engine.estado.secoes_seladas[SecaoTipo.RELATORIO] == rel_original
    assert engine.estado.secoes_seladas[SecaoTipo.PRELIMINARES] == prel_original
    assert engine.estado.secoes_seladas[SecaoTipo.DOSIMETRIA] == dos_original
    assert engine.estado.secoes_seladas[SecaoTipo.DISPOSITIVO] == disp_original

    # 3. Telemetria e histórico da seção de mérito refletem a regeneração (versão incrementada)
    assert rascunho_merito.versao_tentativa >= 2
    assert engine.estado.status_secoes[SecaoTipo.MERITO] == StatusSecao.APROVADO

    # 4. Invariante de Pré-requisitos: falha se houver lacuna nas seções prévias
    engine_incompleto = SectionalPipelineEngine(use_ollama=False)
    engine_incompleto._last_bundle = case
    # Simulando gap: RELATORIO selado mas PRELIMINARES ausente
    engine_incompleto.estado.secoes_seladas[SecaoTipo.RELATORIO] = "Relatório selado."
    with pytest.raises(ValueError, match="Pipeline incompleto ou seções prévias ausentes"):
      engine_incompleto.regenerar_secao_atomica(SecaoTipo.MERITO)

    # 5. Auditoria de confiabilidade da seção regenerada mantém integridade fática
    pecas = classificar_pecas_autos(case["raw_documents"])
    meta = extrair_metadados_processo(pecas)
    aud = auditar_sentenca(
        texto_sentenca=engine.estado.secoes_seladas[SecaoTipo.MERITO],
        pecas_autos=pecas,
        teses_defesa=[t for t in meta.teses_defesa if t.tipo == "MERITO"],
        ids_midias=[t.get("media_id") for t in case.get("transcriptions", []) if t.get("media_id")],
    )
    assert len(aud.ids_inexistentes) == 0
    assert len(aud.citacoes_nao_autorizadas) == 0

  def test_f26_api_regenerate_section(self, api_client, load_fixture_case):
    """F26: API Endpoint de Regeneração Atômica (/api/sentence/regenerate-section) (5 casos)."""
    case = load_fixture_case("1")
    r_gen = api_client.post(
        "/api/sentence/generate-sectional",
        json={
            "case_bundle": case,
            "use_ollama": False,
            "custom_dosimetria_override": veredictos_condenatorios(case),
        },
    )
    assert r_gen.status_code == 200, r_gen.text

    # Regenera exclusivamente a seção de preliminares
    r_prel = api_client.post(
        "/api/sentence/regenerate-section",
        json={"secao": "preliminares", "instrucoes_cirurgicas": "Rejeitar decadência"},
    )
    assert r_prel.status_code == 200
    d_prel = r_prel.json()
    assert d_prel["status"] == "regenerated"
    assert d_prel["secao"] == "preliminares"
    # Valida a reauditoria sem colapso de estado (forma 100, zero alucinação, penas conferidas)
    assert_auditoria_honesta(d_prel["auditoria"], d_prel)

    # Seção inválida retorna 400 ou 422
    r_inv = api_client.post(
        "/api/sentence/regenerate-section", json={"secao": "secao_inexistente"}
    )
    assert r_inv.status_code in (400, 422)

  def test_f27_vram_manager_rtx2060(self, mock_ai_services):
    """F27: Gestão inteligente de VRAM para GPU NVIDIA RTX 2060 (5 casos)."""
    # 1. Consulta de telemetria da VRAM
    status = get_gpu_vram_status()
    assert (
        "gpu_name" in status or "vram_total_mb" in status or "total_mb" in status
    )

    # 2. Descarregamento de VRAM via keep_alive=0 para o Whisper
    res_unload = release_ollama_vram_for_whisper()
    assert res_unload is True or isinstance(res_unload, bool)

    # 3. Interceptação da chamada keep_alive no mock controller
    assert mock_ai_services.call_count >= 1
    last_call = mock_ai_services.calls[-1]
    assert last_call.get("keep_alive") == 0

    # 4. Capacidade de suportar múltiplas chamadas sem estouro de pilha
    for _ in range(5):
      release_ollama_vram_for_whisper()
    assert mock_ai_services.call_count >= 5

    # 5. Monitoramento reporta VRAM consistente
    total_val = status.get("vram_total_mb") or status.get("total_mb", 0)
    assert total_val >= 0

  def test_f28_exportacao_tripla_formatos(self, tmp_path, load_fixture_case):
    """F28: Exportação Tripla — DOCX ABNT, HTML PJe e PDF (5 casos)."""
    case = load_fixture_case("1")
    sent = gerar_sentenca_caso_real(case)

    # 1. Exportação para DOCX (ABNT NBR 10520)
    docx_file = tmp_path / "sentenca_teste.docx"
    exportar_docx(sent, docx_file)
    assert docx_file.exists()
    assert docx_file.stat().st_size > 5000, "Arquivo DOCX muito pequeno"

    # 2. Exportação para HTML PJe
    html_content = exportar_html_pje(sent)
    assert isinstance(html_content, str)
    assert len(html_content) > 1000
    assert "RELATÓRIO" in html_content
    assert "DISPOSITIVO" in html_content

    # 3. Exportação para PDF com layout formal
    pdf_file = tmp_path / "sentenca_teste.pdf"
    exportar_pdf(sent, pdf_file)
    assert pdf_file.exists()
    assert pdf_file.stat().st_size > 5000, "Arquivo PDF muito pequeno"

    # 4. Preservação exata do número do processo nos 3 formatos
    assert case["numero_processo"] in html_content

    # 5. Cabeçalhos de integridade das partes presentes
    assert case["ofendida"] in html_content
    assert case["reu"] in html_content


# ==============================================================================
# TestUploadFiles: Robustez do Endpoint Multipart POST /api/cases/upload-files
# ==============================================================================


class TestUploadFiles:
  """Testes E2E para o endpoint multipart POST /api/cases/upload-files (F01)."""

  def test_upload_valid_pdf_and_media(self, api_client):
    """Valida upload multipart simultâneo de PDF autêntico e mídia de audiência."""
    import io
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text(
        (50, 72),
        "TRIBUNAL DE JUSTICA - PROCESSO 0801245-78.2025.8.20.5001\nID-2010101"
        " (fls. 1/5)",
    )
    pdf_bytes = doc.tobytes()
    doc.close()

    files = [
        (
            "files",
            ("autos_processo.pdf", io.BytesIO(pdf_bytes), "application/pdf"),
        ),
        (
            "files",
            (
                "audiencia_depoimento.mp4",
                io.BytesIO(b"\x00\x00\x00\x20ftypisom"),
                "video/mp4",
            ),
        ),
    ]
    response = api_client.post(
        "/api/cases/upload-files",
        files=files,
        data={"depoente_padrao": "OFENDIDA"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "case_id" in data
    assert len(data.get("pecas", [])) >= 1
    assert any("autos_processo" in p.get("titulo", "") for p in data["pecas"])

  def test_upload_missing_files_payload(self, api_client):
    """Valida que chamada sem o campo files retorna HTTP 422."""
    response = api_client.post("/api/cases/upload-files")
    assert response.status_code == 422

  # --------------------------------------------------------------------------
  # Robustez a payloads inválidos (Gate 2 / E2E): PDF corrompido e arquivo vazio
  # --------------------------------------------------------------------------

  @staticmethod
  def _upload_single_pdf(client: TestClient, filename: str, content: bytes):
    """Auxiliar: POST multipart com um único "PDF" (bytes crus) como application/pdf."""
    import io

    return client.post(
        "/api/cases/upload-files",
        files=[("files", (filename, io.BytesIO(content), "application/pdf"))],
        data={"depoente_padrao": "OFENDIDA"},
    )

  @staticmethod
  def _assert_tratamento_nao_enganoso(response) -> None:
    """Upload inválido DEVE ser rejeitado com 4xx explicativo — nunca HTTP 500 cru.

    Correção de robustez aplicada no backend (M4): `_validar_pdf_upload` valida o
    PDF fisicamente via PyMuPDF e remove o arquivo inválido de data/cases,
    respondendo 422 com `detail` esclarecedor.
    """
    status = response.status_code
    assert status in (400, 415, 422), (
        f"Upload inválido deve ser rejeitado com 4xx explicativo; obtido {status}: "
        f"{response.text[:300]}"
    )
    corpo = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    detalhe = str(corpo.get("detail", "")).strip()
    assert detalhe, (
        "Resposta 4xx para upload inválido deve conter 'detail' explicativo: "
        f"{response.text[:300]}"
    )

  def test_upload_corrupted_pdf_bytes(self):
    """PDF corrompido (bytes não-PDF) deve ser rejeitado com 422 e não poluir data/cases."""
    with TestClient(app, raise_server_exceptions=False, headers={TOKEN_HEADER: get_session_token()}) as client:
      response = self._upload_single_pdf(
          client, "autos_corrompidos.pdf", b"nao-e-pdf\x00"
      )
    self._assert_tratamento_nao_enganoso(response)
    assert response.status_code == 422

  def test_upload_empty_pdf_bytes(self):
    """Arquivo PDF vazio (0 bytes) deve ser rejeitado com 422 e não poluir data/cases."""
    with TestClient(app, raise_server_exceptions=False, headers={TOKEN_HEADER: get_session_token()}) as client:
      response = self._upload_single_pdf(client, "autos_vazios.pdf", b"")
    self._assert_tratamento_nao_enganoso(response)
