# -*- coding: utf-8 -*-
"""Testes dos pontos decisivos da sentença e ligação com o cálculo após confirmação (TASK-042)."""

from __future__ import annotations

import datetime

from app.core.case_context import obter_contexto
from app.demo.demo_case import get_demo_case_bundle
from app.generation.sentence_generator import gerar_sentenca_estruturada, resolver_dosimetria_e_decisoes
from app.ingestion.laya_classifier import classificar_pecas_autos, extrair_metadados_processo


def _doc(doc_id: str, titulo: str, conteudo: str, data_peca: str = "2026-02-10", folha: str = "fls. 10/11") -> dict:
    return {
        "doc_id": doc_id,
        "titulo": titulo,
        "data_peca": data_peca,
        "folha": folha,
        "conteudo": conteudo,
    }


def _ponto(meta, chave: str):
    return next(p for p in meta.pontos_decisivos if p.chave == chave)


def test_data_nascimento_da_vitima_nao_e_atribuida_ao_reu():
    """Critério 1: data de nascimento da vítima no mesmo texto NÃO é atribuída ao réu."""
    raw = [
        _doc(
            "ID-DEN-01",
            "Denúncia Ministerial",
            (
                "O Ministério Público denuncia Marcos Oliveira pela prática do art. 129, § 13, do CP "
                "contra a vítima Ana Paula Souza, nascida em 15/03/2006, brasileira, solteira. "
                "O acusado Marcos Oliveira agrediu a ofendida em 10/01/2026."
            ),
            folha="fls. 01/03",
        ),
    ]
    pecas = classificar_pecas_autos(raw)
    meta = extrair_metadados_processo(pecas)
    pt = _ponto(meta, "data_nascimento_reu")
    assert pt.estado == "ausente"
    assert pt.valor is None
    assert meta.data_nascimento_reu == ""


def test_reu_menor_21_so_entra_no_calculo_apos_confirmacao_do_juiz():
    """Critério 2: réu com 20 anos no fato → sem confirmar nada muda; após confirmar, atenuante art. 65, I e prescrição pela metade."""
    raw = [
        _doc(
            "ID-DEN-02",
            "Denúncia Ministerial",
            (
                "Processo nº 0807111-11.2025.8.20.0001. "
                "O Ministério Público oferece denúncia em face do acusado Lucas Ferreira, nascido em 10/06/2005, "
                "pela prática do crime de lesão corporal contra a mulher (art. 129, § 13, do CP) "
                "contra a vítima Carla Mendes, nascida em 20/08/1990, ocorrido em 15/01/2026."
            ),
            folha="fls. 01/04",
        ),
        _doc(
            "ID-REC-02",
            "Decisão de Recebimento da Denúncia",
            "Recebo a denúncia em 01/02/2026.",
            data_peca="2026-02-01",
            folha="fls. 15",
        ),
    ]
    bundle_sem_conf = {
        "raw_documents": raw,
        "transcriptions": [],
        "data_fato": "2026-01-15",
        "data_recebimento_denuncia": "2026-02-01",
        "crimes_imputados": ["art_129_p13_cp"],
    }
    ctx_sem = obter_contexto(bundle_sem_conf)
    pt_nasc = _ponto(ctx_sem.meta, "data_nascimento_reu")
    assert pt_nasc.estado == "a_confirmar"
    assert pt_nasc.valor == "2005-06-10"
    assert pt_nasc.fonte_id == "ID-DEN-02"
    assert ctx_sem.meta.data_nascimento_reu == ""  # NÃO entra antes da confirmação!

    ov = {"crimes_config": [{"codigo": "art_129_p13_cp", "veredicto": "condenar"}]}
    dos_sem, presc_sem, _, _, _ = resolver_dosimetria_e_decisoes(
        pecas=ctx_sem.pecas,
        meta=ctx_sem.copia_meta(),
        transcriptions=[],
        is_demo=False,
        dt_fato=datetime.date(2026, 1, 15),
        dt_rec=datetime.date(2026, 2, 1),
        custom_dosimetria_override=ov,
        exigir_veredicto=True,
        data_sentenca=datetime.date(2026, 6, 1),
    )
    assert presc_sem.por_crime[0].reduzido_metade_art115 is False
    assert not any("65, i" in a.lower() or "menor de 21" in a.lower() for a in dos_sem.crimes_calculados[0].atenuantes_ativas)

    # Agora o juiz confirma o ponto decisivo via metadados_override
    bundle_com_conf = {
        **bundle_sem_conf,
        "metadados_override": {"pontos_decisivos_confirmados": {"data_nascimento_reu": True}},
    }
    ctx_com = obter_contexto(bundle_com_conf)
    assert ctx_com.meta.data_nascimento_reu == "2005-06-10"
    assert _ponto(ctx_com.meta, "data_nascimento_reu").confirmado is True

    dos_com, presc_com, _, _, _ = resolver_dosimetria_e_decisoes(
        pecas=ctx_com.pecas,
        meta=ctx_com.copia_meta(),
        transcriptions=[],
        is_demo=False,
        dt_fato=datetime.date(2026, 1, 15),
        dt_rec=datetime.date(2026, 2, 1),
        custom_dosimetria_override=ov,
        exigir_veredicto=True,
        data_sentenca=datetime.date(2026, 6, 1),
    )
    assert presc_com.por_crime[0].reduzido_metade_art115 is True
    assert "art. 115" in presc_com.por_crime[0].fundamentacao.lower()
    assert any("65" in a or "menor de 21" in a.lower() for a in dos_com.crimes_calculados[0].atenuantes_ativas)


def test_antecedentes_reincidencia_maus_antecedentes_e_em_curso():
    """Critério 3: trânsito há 3 anos → reincidência; há 7 anos → maus antecedentes; em curso → neutro."""
    # 1. Trânsito em julgado há 3 anos (dentro dos 5 anos do art. 64, I, CP)
    pecas_reinc = classificar_pecas_autos([
        _doc("ID-DEN", "Denúncia Ministerial", "Fato ocorrido em 10/01/2026."),
        _doc(
            "ID-CAC-3A",
            "Certidão de Antecedentes Criminais",
            "CERTIDÃO DE ANTECEDENTES CRIMINAIS. Consta condenação definitiva com trânsito em julgado em 10/01/2023.",
            folha="fls. 40",
        ),
    ])
    meta_reinc = extrair_metadados_processo(pecas_reinc)
    pt_r = _ponto(meta_reinc, "antecedentes")
    assert pt_r.estado == "a_confirmar"
    assert pt_r.valor["classificacao"] == "reincidencia"
    assert pt_r.fonte_id == "ID-CAC-3A"

    # 2. Trânsito em julgado há 7 anos: depende da data de cumprimento/extinção (art. 64, I) → conferir
    pecas_maus = classificar_pecas_autos([
        _doc("ID-DEN", "Denúncia Ministerial", "Fato ocorrido em 10/01/2026."),
        _doc(
            "ID-CAC-7A",
            "Certidão de Antecedentes Criminais",
            "CERTIDÃO DE ANTECEDENTES CRIMINAIS. Consta condenação transitada em julgado em 10/01/2019.",
            folha="fls. 41",
        ),
    ])
    meta_maus = extrair_metadados_processo(pecas_maus)
    pt_m = _ponto(meta_maus, "antecedentes")
    assert pt_m.estado == "a_confirmar"
    assert pt_m.valor["classificacao"] == "verificar_periodo_depurador"
    assert "cumprimento ou a extinção" in pt_m.efeito

    # 2b. Trânsito em julgado POSTERIOR ao fato → nunca reincidência (art. 63 do CP)
    pecas_post = classificar_pecas_autos([
        _doc("ID-DEN", "Denúncia Ministerial", "Fato ocorrido em 10/01/2026."),
        _doc(
            "ID-CAC-POST",
            "Certidão de Antecedentes Criminais",
            "CERTIDÃO DE ANTECEDENTES CRIMINAIS. Consta condenação transitada em julgado em 15/06/2026.",
            folha="fls. 43",
        ),
    ])
    pt_p = _ponto(extrair_metadados_processo(pecas_post), "antecedentes")
    assert pt_p.valor["classificacao"] == "maus_antecedentes"
    assert "63" in pt_p.efeito

    # 3. Ação penal em curso (Súmula 444/STJ e Tema 129/STF → neutro)
    pecas_curso = classificar_pecas_autos([
        _doc("ID-DEN", "Denúncia Ministerial", "Fato ocorrido em 10/01/2026."),
        _doc(
            "ID-CAC-CURSO",
            "Certidão de Antecedentes Criminais",
            "CERTIDÃO DE ANTECEDENTES CRIMINAIS. Consta apenas ação penal em curso, sem trânsito em julgado.",
            folha="fls. 42",
        ),
    ])
    meta_curso = extrair_metadados_processo(pecas_curso)
    pt_c = _ponto(meta_curso, "antecedentes")
    assert pt_c.estado == "extraido"
    assert pt_c.valor["classificacao"] == "neutro_em_curso"
    assert "444" in pt_c.efeito and "129" in pt_c.efeito


def test_confissao_parcial_no_interrogatorio_vira_a_confirmar_com_trecho_e_minuto():
    """Critério 4: confissão parcial no interrogatório vira a_confirmar com trecho e minuto."""
    pecas = classificar_pecas_autos([_doc("ID-DEN", "Denúncia Ministerial", "Denúncia pelo art. 129, § 13 e art. 147 do CP.")])
    transcricoes = [
        {
            "media_id": "MIDIA-INT-01",
            "papel": "INTERROGATORIO_REU",
            "segmentos": [
                {
                    "timestamp": "01:25",
                    "texto_literal": "O réu admitiu parcialmente que empurrou a vítima durante a discussão, mas negou a ameaça.",
                }
            ],
        }
    ]
    meta = extrair_metadados_processo(pecas, transcricoes=transcricoes)
    pt = _ponto(meta, "confissao")
    assert pt.estado == "a_confirmar"
    assert pt.valor["tipo"] == "parcial"
    assert pt.valor["minuto"] == "01:25"
    assert pt.fonte_id == "MIDIA-INT-01"
    assert "empurrou" in pt.trecho.lower()


def test_demais_pontos_decisivos_prisao_representacao_vitima_revelia_e_demo():
    """Valida prisao_provisoria, representacao, vitima_condicao, revelia, reu_preso e o caso demo."""
    raw = [
        _doc(
            "ID-BO-01",
            "Auto de Prisão em Flagrante",
            (
                "O autuado foi preso em flagrante em 10/01/2026 após agredir a vítima gestante na presença dos filhos menores. "
                "Termo de representação lavrado em 10/01/2026: a vítima manifestou que deseja representar criminalmente."
            ),
            folha="fls. 02/05",
        ),
        _doc(
            "ID-SOL-01",
            "Alvará de Soltura",
            "Expedido alvará de soltura em 20/01/2026 concedendo liberdade provisória ao acusado.",
            folha="fls. 18",
        ),
        _doc(
            "ID-ATA-01",
            "Termo de Audiência",
            "Decretada a revelia do acusado nos termos do art. 367 do CPP.",
            folha="fls. 50",
        ),
    ]
    meta = extrair_metadados_processo(classificar_pecas_autos(raw))

    pt_pr = _ponto(meta, "prisao_provisoria")
    assert pt_pr.estado == "a_confirmar" and pt_pr.valor["dias"] == 10

    pt_rep = _ponto(meta, "representacao")
    assert pt_rep.estado == "extraido" and pt_rep.valor["presente"] is True and pt_rep.valor["data"] == "2026-01-10"

    pt_vc = _ponto(meta, "vitima_condicao")
    assert pt_vc.estado == "a_confirmar" and "gestante" in pt_vc.valor and "presenca_descendente" in pt_vc.valor

    pt_rev = _ponto(meta, "revelia")
    assert pt_rev.estado == "a_confirmar" and pt_rev.valor is True

    pt_rp = _ponto(meta, "reu_preso")
    assert pt_rp.estado == "a_confirmar" and pt_rp.valor == "solto"

    # Critério 5: caso demo gera sentença completa com os 10 pontos decisivos presentes
    demo = get_demo_case_bundle()
    ctx_demo = obter_contexto(demo)
    chaves_demo = {p.chave for p in ctx_demo.meta.pontos_decisivos}
    assert {
        "data_nascimento_reu",
        "antecedentes",
        "confissao",
        "prisao_provisoria",
        "representacao",
        "intimacao_mpu",
        "pedido_dano_moral",
        "vitima_condicao",
        "revelia",
        "reu_preso",
    }.issubset(chaves_demo)
    sent = gerar_sentenca_estruturada(case_bundle=demo, use_ollama_if_available=False)
    assert len(sent.texto_completo) > 10000


def test_antecedentes_sem_data_do_fato_nao_conclui_reincidencia():
    """Revisão do Claude: sem data do fato, condenação definitiva não vira reincidência nem maus antecedentes."""
    pecas = classificar_pecas_autos([
        _doc("ID-DEN", "Denúncia Ministerial", "O denunciado ameaçou a vítima."),
        _doc(
            "ID-CAC-SD",
            "Certidão de Antecedentes Criminais",
            "CERTIDÃO DE ANTECEDENTES CRIMINAIS. Consta condenação transitada em julgado em 10/01/2025.",
            folha="fls. 44",
        ),
    ])
    pt = _ponto(extrair_metadados_processo(pecas), "antecedentes")
    assert pt.estado == "a_confirmar"
    assert pt.valor["classificacao"] == "verificar_data_do_fato"

def test_antecedentes_negacao_distante_e_nada_consta():
    """Revisão do Claude (TASK-043): 'sem denúncia ou sentença transitada' e 'NADA CONSTA' não são condenação."""
    pecas_curso = classificar_pecas_autos([
        _doc("ID-DEN", "Denúncia Ministerial", "Fato ocorrido em 10/01/2026."),
        _doc(
            "ID-CAC-NEG",
            "Certidão de Antecedentes Criminais",
            "CERTIDÃO DE ANTECEDENTES CRIMINAIS. CONSTA apenas 01 inquérito policial em andamento sem denúncia ou sentença transitada em julgado.",
            folha="fls. 45",
        ),
    ])
    pt_c = _ponto(extrair_metadados_processo(pecas_curso), "antecedentes")
    assert pt_c.valor["classificacao"] == "neutro_em_curso"

    pecas_nada = classificar_pecas_autos([
        _doc("ID-DEN", "Denúncia Ministerial", "Fato ocorrido em 10/01/2026."),
        _doc(
            "ID-CAC-NADA",
            "Certidão de Antecedentes Criminais",
            "CERTIDÃO DE ANTECEDENTES CRIMINAIS. Certifico que NADA CONSTA em nome do réu, inclusive condenação transitada em julgado.",
            folha="fls. 46",
        ),
    ])
    pt_n = _ponto(extrair_metadados_processo(pecas_nada), "antecedentes")
    assert pt_n.valor["classificacao"] == "primario"