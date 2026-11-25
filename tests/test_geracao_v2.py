# -*- coding: utf-8 -*-
"""Geração v2 (Agente B): sentence_generator, sectional_pipeline, duplo_crivo e demo único.

Cobre os princípios de docs/v2/PLANO_V2.md: nada de dados inventados, juiz decide (veredicto por
crime), demo só por flag explícita, marcadores [COMPLETAR PELO JUÍZO: ...], prescrição por crime,
cálculo determinístico único por geração, Ollama com num_ctx e contexto resumido, e telemetria
honesta (rótulo do motor e seções de fallback).
"""

from __future__ import annotations

import copy
import datetime
import inspect
import json
import re
from pathlib import Path
from unittest import mock

import pytest

import app.generation.sectional_pipeline as sp_mod
from app.core.case_context import obter_contexto
from app.core.errors import DadosInsuficientesError, DecisaoJudicialPendenteError
from app.demo.demo_case import get_demo_case_bundle
from app.generation.duplo_crivo import criar_gating_validator_duplo_crivo, executar_crivo_duplo
from app.generation.sectional_pipeline import (
    SecaoRascunho,
    SecaoTipo,
    SectionalPipelineEngine,
    construir_dossie_secao,
    resumir_contexto_anterior,
)
from app.generation.sentence_generator import (
    calcular_secoes_deterministicas,
    gerar_sentenca_estruturada,
    inferir_confissao_por_crime,
    inferir_detracao_cautelar,
    listar_marcadores_pendentes,
    resolver_dosimetria_e_decisoes,
)
from app.ingestion.laya_classifier import PecaClassificada

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MARCADOR_PRISAO = "[COMPLETAR PELO JUÍZO: situação prisional do réu]"


def _cenario(n: int = 1) -> dict:
    nome = sorted(FIXTURES.glob(f"case_scenario_{n}_*.json"))[0]
    return json.loads(nome.read_text(encoding="utf-8"))


def _veredictos(bundle: dict, veredicto: str = "condenar", **extra) -> dict:
    crimes = obter_contexto(bundle).meta.crimes_imputados
    return {"crimes_config": [{"codigo": c, **({"enquadramento_ameaca": "caput"} if c == "art_147_cp" else {}), "veredicto": veredicto, **extra} for c in crimes]}


def _peca(doc_id: str, categoria: str, titulo: str, conteudo: str, data: str = "") -> PecaClassificada:
    return PecaClassificada(
        doc_id=doc_id,
        folha="fls. 1",
        titulo=titulo,
        data_peca=data,
        fase="JUDICIAL",
        categoria=categoria,
        laya_confidence=0.9,
        resumo_estruturado=titulo,
        conteudo=conteudo,
    )


# ==============================================================================
# Juiz decide / nada inventado
# ==============================================================================


def test_caso_real_sem_veredicto_levanta_decisao_pendente_com_crimes():
    bundle = _cenario(1)
    with pytest.raises(DecisaoJudicialPendenteError) as exc:
        gerar_sentenca_estruturada(bundle, use_ollama_if_available=False)
    assert set(exc.value.crimes_pendentes) == {"art_129_p13_cp", "art_147_cp"}
    assert exc.value.to_dict()["erro"] == "decisao_judicial_pendente"


def test_simulacao_presume_condenacao_e_sinaliza():
    bundle = _cenario(1)
    res = calcular_secoes_deterministicas(bundle, exigir_veredicto=False)
    assert res.simulacao_presume_condenacao is True
    assert all(d["presumido"] for d in res.decisoes_crimes)
    assert any("PRESUMIDA" in a for a in res.dosimetria_global.alertas_globais)


def test_desclassificacao_sem_tipo_destino_exige_complementacao():
    bundle = _cenario(1)
    ov = {
        "crimes_config": [
            {"codigo": "art_129_p13_cp", "veredicto": "desclassificar"},
            {"codigo": "art_147_cp", "veredicto": "condenar"},
        ]
    }
    with pytest.raises(DadosInsuficientesError) as exc:
        gerar_sentenca_estruturada(bundle, ov, use_ollama_if_available=False)
    assert "crimes_config.art_129_p13_cp.crime_desclassificado" in exc.value.campos


def test_sem_crimes_imputados_nao_ha_crime_padrao():
    bundle = {
        "numero_processo": "0800001-00.2025.8.20.0001",
        "raw_documents": [{"doc_id": "ID-1", "conteudo": "Despacho de mero expediente.", "tipo": "DESPACHO"}],
        "metadados_override": {"data_fato": "01/02/2025", "data_recebimento_denuncia": "10/03/2025"},
    }
    with pytest.raises(DadosInsuficientesError) as exc:
        gerar_sentenca_estruturada(bundle, use_ollama_if_available=False)
    assert "crimes_imputados" in exc.value.campos


def test_datas_ausentes_nao_sao_inventadas_e_overrides_br_sao_aceitos():
    bundle = {
        "numero_processo": "0800001-00.2025.8.20.0001",
        "raw_documents": [
            {"doc_id": "ID-1", "conteudo": "VEM OFERECER DENÚNCIA em face de Fulano pelo art. 147 do CP.", "tipo": "DENUNCIA"}
        ],
    }
    with pytest.raises(DadosInsuficientesError) as exc:
        calcular_secoes_deterministicas(bundle, exigir_veredicto=False)
    assert {"data_fato", "data_recebimento_denuncia"} <= set(exc.value.campos)

    completo = dict(bundle, metadados_override={"data_fato": "05/01/2025", "data_recebimento_denuncia": "2025-02-20", "crimes_imputados": "art_147_cp"})
    res = calcular_secoes_deterministicas(completo, exigir_veredicto=False)
    assert res.dt_fato == datetime.date(2025, 1, 5)
    assert res.dt_rec == datetime.date(2025, 2, 20)


def test_caso_real_sem_valoracao_negativa_por_padrao():
    bundle = _cenario(1)
    sent = gerar_sentenca_estruturada(bundle, _veredictos(bundle), use_ollama_if_available=False)
    for crime in sent.dosimetria_dados["crimes_calculados"]:
        assert crime["vetores_negativos_efetivos"] == []
    assert "art_147a" not in json.dumps(sent.dosimetria_dados)


def test_vetor_negativo_sem_fundamento_vira_marcador():
    bundle = _cenario(1)
    ov = _veredictos(bundle)
    ov["crimes_config"][0]["vetores_art59"] = {"culpabilidade": {"desfavoravel": True}}
    sent = gerar_sentenca_estruturada(bundle, ov, use_ollama_if_available=False)
    assert any("valoração negativa de culpabilidade" in m for m in sent.marcadores_pendentes)


# ==============================================================================
# Demo só por flag explícita; texto do demo em um só lugar
# ==============================================================================


def test_demo_gera_sem_veredicto_e_sem_marcadores():
    sent = gerar_sentenca_estruturada(get_demo_case_bundle(), use_ollama_if_available=False)
    assert sent.simulacao_presume_condenacao is True
    assert sent.marcadores_pendentes == []
    assert "Cb. PM Roberto Silva" in sent.secao_relatorio


def test_prefixo_id4100_sem_flag_nao_e_demo():
    bundle = get_demo_case_bundle()
    bundle.pop("is_demo")
    ctx = obter_contexto(bundle)
    assert ctx.is_demo is False
    with pytest.raises(DecisaoJudicialPendenteError):
        gerar_sentenca_estruturada(bundle, use_ollama_if_available=False)
    dossie = construir_dossie_secao(SecaoTipo.RELATORIO, bundle)
    assert "ID-MIDIA-001" not in dossie.folhas_permitidas


def test_texto_demo_vive_apenas_em_demo_sentence():
    fontes = {
        "sectional_pipeline": inspect.getsource(sp_mod),
        "sentence_generator": inspect.getsource(__import__("app.generation.sentence_generator", fromlist=["x"])),
    }
    for nome, src in fontes.items():
        assert "Roberto Silva" not in src, nome
        assert "ID-4100" not in src, nome
        assert "Goiânia" not in src, nome
        assert "3715" not in src, nome


def test_pipeline_e_gerador_usam_o_mesmo_texto_demo():
    demo = get_demo_case_bundle()
    sent = gerar_sentenca_estruturada(demo, use_ollama_if_available=False)
    engine = SectionalPipelineEngine(use_ollama=False)
    sent_sec = engine.executar_pipeline(demo)
    for campo in ("secao_relatorio", "secao_preliminares", "secao_merito", "secao_dosimetria", "secao_dispositivo"):
        assert getattr(sent, campo) == getattr(sent_sec, campo), campo


# ==============================================================================
# Confissão, detração e liberdade
# ==============================================================================


def test_confissao_respeita_negacao_e_pedidos_defensivos():
    def _conf(texto: str) -> bool:
        pecas = [_peca("ID-T", "TERMO_AUDIENCIA", "Termo de Audiência", texto)]
        return inferir_confissao_por_crime(pecas, [], ["art_129_p13_cp"])["art_129_p13_cp"]

    assert _conf("Em seu interrogatório o réu confessou a agressão à ofendida.") is True
    assert _conf("Em seu interrogatório o réu não confessou a agressão.") is False
    assert _conf("O réu admitiu a agressão, mas negou o soco desferido.") is False
    assert _conf("A Defesa requer o reconhecimento da atenuante da confissão quanto à agressão.") is False


def test_detracao_conta_da_prisao_em_flagrante_ate_a_soltura():
    pecas = [
        _peca("ID-APF", "BO", "Auto de Prisão em Flagrante", "Foi preso em flagrante em 20/01/2025.", "2025-01-20"),
        _peca("ID-ALV", "OUTROS", "Certidão de cumprimento do alvará de soltura", "Cumprido o alvará em 22/01/2025, referente à custódia do ID-APF.", "2025-01-22"),
    ]
    dias, periodo = inferir_detracao_cautelar(pecas, data_fato_iso="2024-12-01")
    assert dias == 3
    assert "20/01/2025" in periodo and "22/01/2025" in periodo
    # Sem documento de soltura não há detração presumida.
    assert inferir_detracao_cautelar(pecas[:1]) == (0, "")


def test_recorrer_em_liberdade_exige_decisao_registrada_pelo_juizo():
    bundle = _cenario(1)
    sent = gerar_sentenca_estruturada(bundle, _veredictos(bundle), use_ollama_if_available=False)
    assert MARCADOR_PRISAO in sent.marcadores_pendentes
    assert "respondeu solto" not in sent.texto_completo.lower()

    com_alvara = copy.deepcopy(bundle)
    com_alvara["raw_documents"].append(
        {
            "doc_id": "ID-2010199",
            "tipo": "OUTROS",
            "titulo": "Alvará de Soltura",
            "conteudo": "Concedo a liberdade provisória ao acusado. Expeça-se alvará de soltura.",
            "data_peca": "2025-01-21",
            "folha": "fls. 40",
        }
    )
    sent2 = gerar_sentenca_estruturada(com_alvara, _veredictos(com_alvara), use_ollama_if_available=False)
    assert MARCADOR_PRISAO in sent2.marcadores_pendentes
    assert "CONCEDO ao réu o direito de recorrer em liberdade" not in sent2.secao_dispositivo

    opcoes = _veredictos(com_alvara)
    opcoes["decisao_cautelar"] = {
        "decisao": "conceder",
        "fundamento": "Fundamento cautelar fictício, registrado após exame da situação atual.",
        "referencias": [{"fonte_id": "ID-2010199", "trecho": "Expeça-se alvará de soltura."}],
    }
    sent3 = gerar_sentenca_estruturada(com_alvara, opcoes, use_ollama_if_available=False)
    assert MARCADOR_PRISAO not in sent3.marcadores_pendentes
    assert "CONCEDO ao réu o direito de recorrer em liberdade" in sent3.secao_dispositivo
    assert "ID-2010199" in sent3.secao_dispositivo
    assert "não sobrevieram fundamentos" not in sent3.secao_dispositivo


# ==============================================================================
# Marcadores, absolvição por inciso, dano moral e prescrição por crime
# ==============================================================================


def test_preliminar_sem_resposta_especifica_recebe_marcador():
    bundle = _cenario(1)
    sent = gerar_sentenca_estruturada(bundle, _veredictos(bundle), use_ollama_if_available=False)
    assert any("PREL-02" in m for m in sent.marcadores_pendentes)
    assert listar_marcadores_pendentes(sent.texto_completo) == sent.marcadores_pendentes
    assert any("aguardam complementação pelo juízo" in a for a in sent.alertas)


def test_absolvicao_texto_por_inciso_e_inciso_pendente_no_caso_real():
    bundle = _cenario(1)

    def _merito(cfg_147: dict) -> str:
        ov = {"crimes_config": [{"codigo": "art_129_p13_cp", "veredicto": "condenar"}, {"codigo": "art_147_cp", **cfg_147}]}
        return gerar_sentenca_estruturada(bundle, ov, use_ollama_if_available=False).secao_merito

    m_ii = _merito({"veredicto": "absolver", "fundamento_absolvicao_art386": "II"})
    m_vii = _merito({"veredicto": "absolver", "fundamento_absolvicao_art386": "VII"})
    assert "art. 386, inciso II" in m_ii and "não haver prova da existência do fato" in m_ii
    assert "art. 386, inciso VII" in m_vii and "não existir prova suficiente para a condenação" in m_vii
    m_sem = _merito({"veredicto": "absolver"})
    assert "[COMPLETAR PELO JUÍZO: inciso do art. 386 do CPP aplicável]" in m_sem


def test_dano_moral_pedido_sem_valor_nao_diz_ausencia_de_pedido():
    bundle = _cenario(3)
    sent = gerar_sentenca_estruturada(bundle, _veredictos(bundle), use_ollama_if_available=False)
    assert sent.dosimetria_dados["dano_moral_arbitramento_pendente"] is True
    assert "[COMPLETAR PELO JUÍZO: decisão sobre a reparação mínima pedida pelo Ministério Público]" in sent.secao_dispositivo
    assert "ausência de pedido" not in sent.secao_dispositivo
    assert "ausência de pedido" not in sent.secao_dosimetria


@pytest.mark.parametrize("decidido", [None, 0, 1250])
def test_pedido_quantificado_nao_se_confunde_com_decisao(decidido):
    """CPP, art. 387, IV, e Tema 983/STJ.
    https://www.planalto.gov.br/ccivil_03/decreto-lei/del3689compilado.htm#art387
    https://processo.stj.jus.br/repetitivos/temas_repetitivos/pesquisa.jsp?cod_tema_inicial=983&cod_tema_final=983
    """
    bundle = _cenario(3)
    bundle["metadados_override"] = {
        "pedido_dano_moral_expresso_mp": True,
        "valor_dano_moral_pedido_mp": 2500.0,
    }
    ov = {**_veredictos(bundle), "valor_dano_moral": decidido}
    sent = gerar_sentenca_estruturada(bundle, ov, use_ollama_if_available=False)
    assert sent.dosimetria_dados["valor_dano_moral_pedido"] == 2500
    assert sent.dosimetria_dados["valor_dano_moral_sugerido"] == 2500
    assert sent.dosimetria_dados["valor_dano_moral_decidido"] == decidido
    assert sent.dosimetria_dados["dano_moral_arbitramento_pendente"] == (decidido is None)
    assert "2.500" not in sent.secao_dispositivo
    if decidido == 0:
        assert "DEIXO DE FIXAR" in sent.secao_dispositivo
        assert "ausência de pedido" not in sent.secao_dispositivo
    if decidido == 1250:
        assert "1.250" in sent.secao_dispositivo


def test_dosimetria_cita_prescricao_por_crime():
    """TASK-046 (ordem do usuário): sem prescrição, a sentença não abre tópico de prescrição; o cálculo por crime
    continua disponível nos dados (``prescricao_dados``) para quando houver prescrição."""
    bundle = _cenario(1)
    sent = gerar_sentenca_estruturada(bundle, _veredictos(bundle), use_ollama_if_available=False)
    por_crime = sent.prescricao_dados["por_crime"]
    assert {p["crime_code"] for p in por_crime} == {"art_129_p13_cp", "art_147_cp"}
    houve = any(p.get("prescricao_reconhecida") or not p.get("verificacao_completa", True) for p in por_crime)
    assert ("Da Prescrição pela Pena Aplicada a Cada Crime" in sent.secao_dosimetria) == houve
    if not houve:
        assert "Da Ausência de Prescrição" not in sent.secao_preliminares
        assert "Da Prescrição" not in sent.secao_preliminares


def test_topico_de_prescricao_so_quando_ha_prescricao_ou_verificacao_incompleta():
    from types import SimpleNamespace

    from app.generation.sentence_generator import prescricao_exige_topico, texto_prescricao_preliminares

    def _p(reconhecida, completa):
        return SimpleNamespace(
            prescricao_reconhecida=reconhecida, verificacao_completa=completa, prescricao_abstrata_reconhecida=reconhecida,
            crime_nome="Ameaça", artigo_formatado="art. 147 do CP",
        )

    sem = SimpleNamespace(por_crime=[_p(False, True)], fundamentacao="")
    assert not prescricao_exige_topico(sem)
    assert texto_prescricao_preliminares(sem) == ("", "")
    incompleta = SimpleNamespace(por_crime=[_p(False, False)], fundamentacao="")
    titulo, texto = texto_prescricao_preliminares(incompleta)
    assert titulo == "Da Prescrição" and "COMPLETAR" in texto.upper()
    com = SimpleNamespace(por_crime=[_p(True, True)], fundamentacao="")
    assert texto_prescricao_preliminares(com)[0] == "Da Prescrição da Pretensão Punitiva"


def test_resolver_dosimetria_exige_veredicto_quando_pedido():
    bundle = _cenario(1)
    ctx = obter_contexto(bundle)
    with pytest.raises(DecisaoJudicialPendenteError):
        resolver_dosimetria_e_decisoes(
            ctx.pecas, ctx.copia_meta(), [], False, datetime.date(2025, 1, 20), datetime.date(2025, 2, 15), {}, exigir_veredicto=True
        )


def test_gerador_integral_rotula_motor_sem_llm():
    sent = gerar_sentenca_estruturada(get_demo_case_bundle(), use_ollama_if_available=True)
    assert "Ollama" not in sent.motor_utilizado
    assert sent.secoes_fallback == []
    assert any("nenhum trecho foi redigido por LLM" in a for a in sent.alertas)


# ==============================================================================
# Pipeline seccional: cálculo único, Ollama com num_ctx, contexto resumido, telemetria
# ==============================================================================


def test_pipeline_calcula_secoes_deterministicas_uma_unica_vez():
    demo = get_demo_case_bundle()
    original = sp_mod.calcular_secoes_deterministicas
    with mock.patch.object(sp_mod, "calcular_secoes_deterministicas", side_effect=original) as espiao:
        engine = SectionalPipelineEngine(use_ollama=False)
        engine.executar_pipeline(demo)
        assert espiao.call_count == 1
        engine.regenerar_secao_atomica(SecaoTipo.MERITO)
        engine.montar_sentenca()
        assert espiao.call_count == 1


def test_pipeline_ollama_usa_num_ctx_e_contexto_resumido():
    demo = get_demo_case_bundle()
    chamadas = []

    def falso_gerar_texto(prompt, **kwargs):
        chamadas.append((prompt, kwargs))
        return None  # Ollama indisponível: força fallback

    with mock.patch.object(sp_mod, "gerar_texto", side_effect=falso_gerar_texto):
        engine = SectionalPipelineEngine(use_ollama=True)
        sent = engine.executar_pipeline(demo)

    assert len(chamadas) == 5
    for _prompt, kwargs in chamadas:
        assert kwargs.get("num_ctx") == engine.settings.ollama_num_ctx
    # O prompt do dispositivo leva só o RESUMO das seções anteriores, não o texto integral.
    prompt_disp = chamadas[-1][0]
    assert "SELADO — RESUMO" in prompt_disp
    assert engine.estado.secoes_seladas[SecaoTipo.MERITO] not in prompt_disp
    assert sent.secoes_fallback == [s.value for s in SecaoTipo]
    assert "Fallback Determinístico" in sent.motor_utilizado
    assert engine.snapshot_telemetria()["secoes_fallback"] == sent.secoes_fallback


def test_pipeline_llm_aprovado_rotula_ollama_e_sem_fallback():
    demo = get_demo_case_bundle()
    textos = {}
    base_motivada = calcular_secoes_deterministicas(demo)
    dispositivo_completo = base_motivada.secao("dispositivo")

    def falso_gerar_texto(prompt, **kwargs):
        secao = re.search(r"a seção: (\w+)", prompt).group(1).lower()
        textos[secao] = True
        return {
            "relatorio": "I – RELATÓRIO\n\nTexto redigido pelo LLM com base na denúncia (ID-4100201) e nos autos, sem fatos novos.",
            "preliminares": "II – FUNDAMENTAÇÃO\n\nRejeito a preliminar PREL-01, pois a denúncia (ID-4100201) atende ao art. 41 do CPP.",
            "merito": base_motivada.secao("merito"),
            "dosimetria": base_motivada.secao("dosimetria"),
            "dispositivo": dispositivo_completo,
        }[secao]

    with mock.patch.object(sp_mod, "gerar_texto", side_effect=falso_gerar_texto):
        engine = SectionalPipelineEngine(use_ollama=True)
        sent = engine.executar_pipeline(demo)

    assert sent.secoes_fallback == []
    assert "Ollama" in sent.motor_utilizado
    assert engine.snapshot_telemetria()["modo_geracao"] == "LLM_OLLAMA"
    assert all(engine.snapshot_telemetria()["secoes"][s.value]["fonte"] == "llm" for s in SecaoTipo)
    assert any("redigidas pelo LLM" in a for a in sent.alertas)


def test_pipeline_deterministico_rotulo_honesto():
    sent = SectionalPipelineEngine(use_ollama=False).executar_pipeline(get_demo_case_bundle())
    assert "Ollama" not in sent.motor_utilizado
    assert "sem LLM" in sent.motor_utilizado
    assert sent.secoes_fallback == []


def test_pipeline_caso_real_sem_veredicto_e_bloqueado():
    with pytest.raises(DecisaoJudicialPendenteError):
        SectionalPipelineEngine(use_ollama=False).executar_pipeline(_cenario(2))


def test_cabecalho_sem_apenso_nao_imprime_none():
    bundle = _cenario(1)
    bundle.pop("autos_mpu_apenso", None)
    sent = SectionalPipelineEngine(use_ollama=False).executar_pipeline(bundle, custom_dosimetria_override=_veredictos(bundle))
    assert "None" not in sent.secao_cabecalho


def test_resumo_contexto_anterior_limita_tamanho():
    secoes = {SecaoTipo.RELATORIO: "I – RELATÓRIO\n" + ("Narrativa longa. " * 2000), SecaoTipo.MERITO: "2.1. Item\nRejeito a tese MER-01."}
    resumo = resumir_contexto_anterior(secoes, excluir=SecaoTipo.DISPOSITIVO, limite_total=500)
    assert len(resumo) <= 600
    assert "I – RELATÓRIO" in resumo
    assert "Narrativa longa. Narrativa longa." not in resumo


def test_dossies_reais_sem_fatos_inventados():
    bundle = _cenario(1)
    res = calcular_secoes_deterministicas(bundle, _veredictos(bundle))
    mer = construir_dossie_secao(SecaoTipo.MERITO, bundle, resultado=res)
    txt = " ".join(mer.fatos_delimitados)
    assert "Auto de Prisão em Flagrante" not in txt  # não há APF neste processo
    assert "CONDENAÇÃO" in txt
    dos = construir_dossie_secao(SecaoTipo.DOSIMETRIA, bundle, resultado=res)
    assert "prescricao" in dos.parametros_dosimetria
    # Regra do usuário (TASK-046/071): sem prescrição, o dossiê manda não abrir o tópico; com prescrição, cita o art. 119.
    assert any("art. 119" in f or "NÃO abra tópico de prescrição" in f for f in dos.fatos_delimitados)
    # Sem certidão de antecedentes nos autos, a minuta não afirma o conteúdo de uma certidão inexistente.
    texto_dos = res.secao(SecaoTipo.DOSIMETRIA)
    assert "revelam que o acusado detém primariedade" not in texto_dos
    assert "Não consta dos autos certidão de antecedentes criminais" in texto_dos
    disp = construir_dossie_secao(SecaoTipo.DISPOSITIVO, bundle, resultado=res)
    assert any(MARCADOR_PRISAO in f for f in disp.fatos_delimitados)


# ==============================================================================
# Duplo crivo: marcadores não pressionam o redator a inventar
# ==============================================================================


def test_duplo_crivo_marcador_nao_bloqueia_gating_mas_e_reportado():
    bundle = _cenario(1)
    res = calcular_secoes_deterministicas(bundle, _veredictos(bundle))
    texto = res.secao(SecaoTipo.DISPOSITIVO)
    assert MARCADOR_PRISAO in texto
    dossie = construir_dossie_secao(SecaoTipo.DISPOSITIVO, bundle, resultado=res)
    rascunho = SecaoRascunho(secao=SecaoTipo.DISPOSITIVO, texto_redigido=texto)

    tolerante = executar_crivo_duplo(SecaoTipo.DISPOSITIVO, rascunho, dossie, bundle=bundle)
    assert tolerante.aprovado is True
    assert MARCADOR_PRISAO in tolerante.marcadores_pendentes
    assert not any("marcador" in e.lower() for e in tolerante.erros_auditor)

    estrito = executar_crivo_duplo(SecaoTipo.DISPOSITIVO, rascunho, dossie, bundle=bundle, marcadores_bloqueiam=True)
    assert estrito.aprovado is False

    gating = criar_gating_validator_duplo_crivo(use_ollama=False)(SecaoTipo.DISPOSITIVO, rascunho, dossie)
    assert gating.aprovado is True


def test_duplo_crivo_continua_reprovando_id_inventado_mesmo_com_marcador():
    bundle = _cenario(1)
    res = calcular_secoes_deterministicas(bundle, _veredictos(bundle))
    dossie = construir_dossie_secao(SecaoTipo.DISPOSITIVO, bundle, resultado=res)
    texto = res.secao(SecaoTipo.DISPOSITIVO) + "\nConforme ID-99999999."
    r = executar_crivo_duplo(
        SecaoTipo.DISPOSITIVO, SecaoRascunho(secao=SecaoTipo.DISPOSITIVO, texto_redigido=texto), dossie
    )
    assert r.aprovado is False
    assert any("ID-99999999" in e for e in r.erros_auditor)


def test_prescricao_mista_mantem_marcador_do_item_inconclusivo():
    """Revisão cruzada do CODEX (TASK-046): um crime prescrito e outro com marco ausente."""
    from types import SimpleNamespace

    from app.generation.sentence_generator import texto_prescricao_preliminares

    def _p(nome, reconhecida, completa):
        return SimpleNamespace(
            prescricao_reconhecida=reconhecida, verificacao_completa=completa, prescricao_abstrata_reconhecida=reconhecida,
            crime_nome=nome, artigo_formatado="art. 147 do CP",
        )

    misto = SimpleNamespace(por_crime=[_p("Ameaça", True, True), _p("Dano", False, False)], fundamentacao="")
    titulo, texto = texto_prescricao_preliminares(misto)
    assert titulo == "Da Prescrição da Pretensão Punitiva"
    assert "Dano" in texto and "COMPLETAR" in texto.upper()


def test_script_e_seccional_nao_mandam_citar_prescricao_inexistente():
    from types import SimpleNamespace

    from app.generation.sentence_generator import prescricao_exige_topico

    assert not prescricao_exige_topico(SimpleNamespace(prescricao_reconhecida=False, por_crime=[]))
    assert prescricao_exige_topico(SimpleNamespace(prescricao_reconhecida=True, por_crime=[]))
    assert not prescricao_exige_topico(None)

def test_script_externo_completo_da_demo_sem_prescricao_nao_manda_abrir_topico():
    """Revisão cruzada do CODEX (TASK-071): exercita a saída completa do script, não só o predicado."""
    from app.demo.demo_case import get_demo_case_bundle
    from app.generation.case_script_engine import gerar_script_estruturado_caso
    from app.generation.sentence_generator import prescricao_exige_topico

    script = gerar_script_estruturado_caso(get_demo_case_bundle())
    md = script.script_markdown
    presc = script.prescricao_calculada if hasattr(script, "prescricao_calculada") else None
    if presc is None or not prescricao_exige_topico(presc):
        assert "NÃO abra tópico nem mencione a prescrição" in md
        assert "Análise de Prescrição (Arts. 109 e 117, I, CP)" not in md