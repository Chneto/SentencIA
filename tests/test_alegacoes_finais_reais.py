# -*- coding: utf-8 -*-
"""Testes da extração real de alegações finais do MP e da Defesa (TASK-041)."""

from __future__ import annotations

from app.demo.demo_case import get_demo_case_bundle
from app.generation.adversarial_critic import extrair_teses_defensivas
from app.generation.sentence_generator import gerar_sentenca_estruturada
from app.ingestion.laya_classifier import (
    classificar_pecas_autos,
    extrair_alegacoes_orais_ata,
    extrair_metadados_processo,
    extrair_pedidos_mp_texto,
)


def _doc(doc_id: str, titulo: str, conteudo: str, folhas: str = "fls. 10/12") -> dict:
    return {
        "doc_id": doc_id,
        "titulo": titulo,
        "data_peca": "2026-03-10",
        "folha": folhas,
        "conteudo": conteudo,
    }


def test_memoriais_mp_pedindo_absolvicao_nao_diz_condenacao_no_resumo():
    """Critério 1: memoriais do MP pedindo absolvição geram 'absolvicao' e resumo não diz 'condenação'."""
    raw = [
        _doc(
            "ID-DEN-01",
            "Denúncia",
            "O Ministério Público oferece denúncia em face de Carlos Silva pelo crime do art. 147 do CP.",
            folhas="fls. 01/03",
        ),
        _doc(
            "ID-MP-01",
            "Alegações Finais do Ministério Público (Memoriais)",
            (
                "ALEGAÇÕES FINAIS DO MINISTÉRIO PÚBLICO. "
                "Finda a instrução, não há prova suficiente da autoria delitiva. "
                "Diante do exposto, o Ministério Público requer a absolvição do acusado nos termos do art. 386, VII, do CPP."
            ),
            folhas="fls. 80/82",
        ),
    ]
    pecas = classificar_pecas_autos(raw)
    peca_mp = next(p for p in pecas if p.doc_id == "ID-MP-01")
    assert peca_mp.categoria == "ALEGACOES_FINAIS_MP"
    assert "condena" not in peca_mp.resumo_estruturado.lower()
    assert "absolvi" in peca_mp.resumo_estruturado.lower()

    meta = extrair_metadados_processo(pecas)
    tipos_mp = [p.tipo for p in meta.pedidos_mp]
    assert "absolvicao" in tipos_mp
    assert "condenacao_total" not in tipos_mp
    ped_abs = next(p for p in meta.pedidos_mp if p.tipo == "absolvicao")
    assert "386" in ped_abs.descricao and "VII" in ped_abs.descricao
    assert ped_abs.fonte_id == "ID-MP-01"
    assert ped_abs.folha == "fls. 80/82"
    assert ped_abs.origem == "memoriais"
    assert 0 < len(ped_abs.trecho) <= 300


def test_memoriais_mp_sem_pedido_gera_aviso_no_resumo_e_alerta():
    """Requisito 2: memoriais do MP sem pedido identificável geram resumo de conferência e alerta."""
    raw = [
        _doc(
            "ID-MP-VAZIO",
            "Alegações Finais do Ministério Público",
            "ALEGAÇÕES FINAIS DO MINISTÉRIO PÚBLICO. Junta-se aos autos o relatório fático da instrução para ciência do Juízo.",
            folhas="fls. 90",
        ),
    ]
    pecas = classificar_pecas_autos(raw)
    peca_mp = next(p for p in pecas if p.doc_id == "ID-MP-VAZIO")
    assert "pedidos não identificados automaticamente: conferir a peça" in peca_mp.resumo_estruturado.lower()

    meta = extrair_metadados_processo(pecas)
    assert any("pedidos não identificados automaticamente" in a.lower() for a in meta.alertas_extracao)


def test_memoriais_mp_dano_moral_sincronizado_com_pedidos_mp():
    """Requisito 6: pedido_dano_moral_expresso_mp e valor vêm de pedidos_mp."""
    raw = [
        _doc(
            "ID-MP-DM",
            "Alegações Finais do Ministério Público",
            (
                "ALEGAÇÕES FINAIS DO MINISTÉRIO PÚBLICO. "
                "Pugna pela condenação do acusado nas penas do art. 129, § 13, do CP, "
                "com reconhecimento da agravante do art. 61, II, f, do CP, fixação de regime inicial semiaberto "
                "e fixação de valor mínimo para reparação pelos danos morais em R$ 2.500,00 (art. 387, IV, CPP)."
            ),
            folhas="fls. 70/73",
        ),
    ]
    pecas = classificar_pecas_autos(raw)
    meta = extrair_metadados_processo(pecas)

    tipos = {p.tipo for p in meta.pedidos_mp}
    assert {"condenacao_total", "dano_moral", "agravante_qualificadora", "regime"}.issubset(tipos)
    assert meta.pedido_dano_moral_expresso_mp is True
    assert meta.valor_dano_moral_pedido_mp == 2500.0
    for p in meta.pedidos_mp:
        assert p.fonte_id == "ID-MP-DM"
        assert 0 < len(p.trecho) <= 300


def test_defesa_sem_tese_reconhecida_nao_gera_mer01_e_gera_alerta():
    """Critério 2: defesa sem tese reconhecida não injeta MER-01 e registra alerta."""
    raw = [
        _doc(
            "ID-DEF-SEM",
            "Alegações Finais da Defesa",
            "ALEGAÇÕES FINAIS DA DEFESA. A Defesa reporta-se aos autos e aguarda a apreciação judicial.",
            folhas="fls. 95",
        ),
    ]
    pecas = classificar_pecas_autos(raw)
    meta = extrair_metadados_processo(pecas)

    assert meta.teses_defesa == []
    assert not any(t.codigo == "MER-01" for t in meta.teses_defesa)
    assert any("teses da defesa não identificadas: conferir" in a.lower() for a in meta.alertas_extracao)


def test_teses_defesa_e_subsidiarios_guardam_trecho_real_e_fonte():
    """Requisito 4 e Critério 4: cada tese e pedido subsidiário tem fonte_id e trecho <= 300 chars."""
    raw = [
        _doc(
            "ID-DEF-01",
            "Alegações Finais da Defesa (Memoriais)",
            (
                "ALEGAÇÕES FINAIS DA DEFESA TÉCNICA. "
                "Requer a absolvição por insuficiência probatória e in dubio pro reo (art. 386, VII, CPP). "
                "Subsidiariamente, pleiteia pena-base no mínimo legal, reconhecimento da confissão espontânea (Súmula 545/STJ), "
                "regime inicial aberto, sursis penal (art. 77 do CP), justiça gratuita, exclusão do dano moral e direito de recorrer em liberdade."
            ),
            folhas="fls. 100/104",
        ),
    ]
    pecas = classificar_pecas_autos(raw)
    meta = extrair_metadados_processo(pecas)

    assert len(meta.teses_defesa) >= 1
    for t in meta.teses_defesa:
        assert t.fonte_id == "ID-DEF-01"
        assert t.folha == "fls. 100/104"
        assert 0 < len(t.trecho) <= 300
        assert t.origem == "memoriais"

    tipos_def = {p.tipo for p in meta.pedidos_defesa}
    assert {"MER-01", "pena_minima", "atenuante", "regime", "sursis", "gratuidade", "dano_moral", "recorrer_liberdade"}.issubset(tipos_def)
    for p in meta.pedidos_defesa:
        assert p.fonte_id == "ID-DEF-01"
        assert 0 < len(p.trecho) <= 300


def test_alegacoes_orais_na_ata_atribuidas_a_parte_certa():
    """Critério 3a: alegações finais orais na ata são atribuídas corretamente ao MP e à Defesa."""
    raw = [
        _doc(
            "ID-ATA-01",
            "Termo de Audiência de Instrução e Julgamento",
            (
                "TERMO DE AUDIÊNCIA DE INSTRUÇÃO. Ouvida a ofendida e interrogado o réu. "
                "Em alegações finais orais, o Ministério Público requereu a condenação do acusado nos termos da denúncia "
                "e a fixação de danos morais em R$ 1.500,00. "
                "Em seguida, a Defesa Técnica requereu a absolvição por insuficiência probatória (art. 386, VII, CPP) "
                "e, subsidiariamente, a concessão de sursis penal e justiça gratuita."
            ),
            folhas="fls. 60/61",
        ),
    ]
    pecas = classificar_pecas_autos(raw)
    meta = extrair_metadados_processo(pecas)

    assert any(p.tipo == "condenacao_total" and p.origem == "oral" and p.fonte_id == "ID-ATA-01" for p in meta.pedidos_mp)
    assert any(p.tipo == "dano_moral" and p.origem == "oral" and p.valor == 1500.0 for p in meta.pedidos_mp)
    assert meta.pedido_dano_moral_expresso_mp is True
    assert meta.valor_dano_moral_pedido_mp == 1500.0

    assert any(t.codigo == "MER-01" and t.origem == "oral" and t.fonte_id == "ID-ATA-01" for t in meta.teses_defesa)
    assert any(p.tipo == "sursis" and p.origem == "oral" and p.fonte_id == "ID-ATA-01" for p in meta.pedidos_defesa)
    assert any(p.tipo == "gratuidade" and p.origem == "oral" and p.fonte_id == "ID-ATA-01" for p in meta.pedidos_defesa)
    assert not any("ambígua" in a.lower() for a in meta.alertas_extracao)


def test_alegacoes_orais_ambiguas_na_ata_geram_alerta():
    """Critério 3b: alegações finais orais sem identificação da parte geram alerta e não inventam autoria."""
    raw = [
        _doc(
            "ID-ATA-AMB",
            "Termo de Audiência",
            (
                "TERMO DE AUDIÊNCIA. Colhida a prova oral. "
                "Em alegações finais orais, requereu-se a absolvição por falta de provas e a fixação de pena mínima."
            ),
            folhas="fls. 62",
        ),
    ]
    pecas = classificar_pecas_autos(raw)
    meta = extrair_metadados_processo(pecas)

    assert meta.pedidos_mp == []
    assert any("ambígua" in a.lower() and "ID-ATA-AMB" in a for a in meta.alertas_extracao)


def test_alegacoes_orais_em_transcricoes_gravadas():
    """Requisito 5: transcrições com papel ALEGACOES_FINAIS_MP / ALEGACOES_FINAIS_DEFESA alimentam pedidos com origem='oral'."""
    pecas = classificar_pecas_autos([_doc("ID-DEN-02", "Denúncia", "Denúncia pelo art. 147 do CP.")])
    transcricoes = [
        {
            "media_id": "MIDIA-MP-01",
            "papel": "ALEGACOES_FINAIS_MP",
            "texto": "O Ministério Público pugna pela condenação do réu e reparação mínima por danos morais de R$ 2.000,00.",
        },
        {
            "media_id": "MIDIA-DEF-01",
            "papel": "ALEGACOES_FINAIS_DEFESA",
            "texto": "A Defesa pleiteia a absolvição por insuficiência probatória e subsidiariamente pena-base no mínimo legal.",
        },
    ]
    meta = extrair_metadados_processo(pecas, transcricoes=transcricoes)
    assert any(p.tipo == "condenacao_total" and p.origem == "oral" and p.fonte_id == "MIDIA-MP-01" for p in meta.pedidos_mp)
    assert any(p.tipo == "dano_moral" and p.origem == "oral" and p.valor == 2000.0 for p in meta.pedidos_mp)
    assert any(p.tipo == "MER-01" and p.origem == "oral" and p.fonte_id == "MIDIA-DEF-01" for p in meta.pedidos_defesa)
    assert any(p.tipo == "pena_minima" and p.origem == "oral" and p.fonte_id == "MIDIA-DEF-01" for p in meta.pedidos_defesa)


def test_caso_demo_mantem_sentenca_completa_e_pedidos_com_fonte_e_trecho():
    """Critérios 4 e 5: caso demo continua gerando sentença completa e todos os pedidos têm fonte_id e trecho <= 300."""
    demo = get_demo_case_bundle()
    pecas = classificar_pecas_autos(demo["raw_documents"], is_demo=True)
    meta = extrair_metadados_processo(pecas, is_demo=True)

    assert len(meta.pedidos_mp) >= 1
    assert len(meta.pedidos_defesa) >= 1
    for p in meta.pedidos_mp + meta.pedidos_defesa:
        assert p.fonte_id, f"Pedido sem fonte_id: {p}"
        assert p.trecho and len(p.trecho) <= 300, f"Trecho inválido: {p}"

    sent = gerar_sentenca_estruturada(case_bundle=demo, use_ollama_if_available=False)
    assert len(sent.texto_completo) > 10000
    assert "None" not in sent.texto_completo
