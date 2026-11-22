# -*- coding: utf-8 -*-
"""Estabilização v2.0.0: integrações que ficaram entre áreas.

- CaseContext completa data_fato / data_recebimento_denuncia a partir do topo do bundle (nunca inventa);
- art. 115 do CP alimentado por ``data_nascimento_reu`` (metadados_override) ou pelas flags do juízo;
- antecedentes neutros sem afirmar o conteúdo de certidão inexistente;
- Súmulas 497/STF e 659/STJ (citadas pelo motor de dosimetria) presentes na base fechada;
- helpers públicos usados pela API.
"""

from __future__ import annotations

import datetime

import pytest

from app.core.case_context import (
    limpar_cache_contexto,
    normalizar_data,
    normalizar_override_metadado,
    obter_contexto,
)
from app.generation.sentence_generator import flags_idade_art115, resolver_data
from app.knowledge.knowledge_base import SUMULAS_E_TEMAS_VERIFICADOS
from app.legal_engine.dosimetria_calculator import obter_fundamentacao_padrao_vetor


def _bundle_real(**extra):
    bundle = {
        "case_id": "REAL-TESTE",
        "numero_processo": "0000001-11.2025.8.20.0001",
        "ofendida": "OFENDIDA TESTE",
        "reu": "REU TESTE",
        "raw_documents": [
            {
                "doc_id": "ID-900001",
                "folha": "fls. 1",
                "titulo": "Denúncia",
                "data_peca": "",
                "conteudo": "DENÚNCIA. O Ministério Público oferece denúncia em face do acusado pela prática do crime de ameaça (art. 147 do CP).",
            }
        ],
        "transcriptions": [],
    }
    bundle.update(extra)
    return bundle


@pytest.fixture(autouse=True)
def _cache_limpo():
    limpar_cache_contexto()
    yield
    limpar_cache_contexto()


def test_contexto_completa_datas_do_topo_do_bundle_normalizadas():
    ctx = obter_contexto(_bundle_real(data_fato="10/02/2025", data_recebimento_denuncia="2025-03-01"))
    assert ctx.meta.data_fato == "2025-02-10"
    assert ctx.meta.data_recebimento_denuncia == "2025-03-01"


def test_contexto_ignora_data_invalida_do_bundle_sem_inventar():
    ctx = obter_contexto(_bundle_real(data_fato="31/02/2025"))
    assert ctx.meta.data_fato == ""
    assert ctx.meta.data_recebimento_denuncia == ""


def test_override_do_juizo_prevalece_sobre_o_topo_do_bundle():
    ctx = obter_contexto(
        _bundle_real(data_fato="2025-02-10", metadados_override={"data_fato": "05/01/2025"})
    )
    assert ctx.meta.data_fato == "2025-01-05"


def test_data_nascimento_reu_aceita_no_override():
    ctx = obter_contexto(_bundle_real(metadados_override={"data_nascimento_reu": "20/06/2005"}))
    assert ctx.meta.data_nascimento_reu == "2005-06-20"


def test_flags_art115_calculadas_pela_data_de_nascimento():
    fato = datetime.date(2025, 2, 10)
    sentenca = datetime.date(2026, 11, 18)
    assert flags_idade_art115("2005-06-20", fato, sentenca) == (True, False)  # 19 anos no fato
    assert flags_idade_art115("2004-02-10", fato, sentenca) == (False, False)  # 21 completos no fato
    assert flags_idade_art115("1956-11-18", fato, sentenca) == (False, True)  # 70 completos na sentença
    assert flags_idade_art115("1956-11-19", fato, sentenca) == (False, False)  # 69 anos na sentença
    assert flags_idade_art115("", fato, sentenca) == (False, False)  # nada é presumido


def test_flags_art115_override_explicito_prevalece():
    fato = datetime.date(2025, 2, 10)
    sentenca = datetime.date(2026, 11, 18)
    assert flags_idade_art115("2005-06-20", fato, sentenca, False, None) == (False, False)
    assert flags_idade_art115("", fato, sentenca, None, True) == (False, True)


def test_prescricao_usa_metade_do_prazo_com_reu_menor_de_21():
    from app.ingestion.laya_classifier import MetadadosProcesso
    from app.generation.sentence_generator import resolver_dosimetria_e_decisoes

    meta = MetadadosProcesso(
        numero_processo="0000001-11.2025.8.20.0001",
        nome_ofendida="OFENDIDA",
        nome_reu="REU",
        data_fato="2025-02-10",
        data_recebimento_denuncia="2025-03-01",
        data_intimacao_mpu="",
        intimidado_pessoalmente_mpu=False,
        reu_primario_sumula444=True,
        pedido_dano_moral_expresso_mp=False,
        valor_dano_moral_pedido_mp=0.0,
        crimes_imputados=["art_147_cp"],
        teses_defesa=[],
        data_nascimento_reu="2005-06-20",
    )
    override = {"crimes_config": [{"codigo": "art_147_cp", "veredicto": "condenar"}], "data_sentenca": "2026-11-18"}
    _dos, prescricao, *_ = resolver_dosimetria_e_decisoes(
        [], meta, [], False, datetime.date(2025, 2, 10), datetime.date(2025, 3, 1), override,
        exigir_veredicto=True, data_sentenca=datetime.date(2026, 11, 18),
    )
    assert prescricao.por_crime and all(p.reduzido_metade_art115 for p in prescricao.por_crime)


def test_antecedentes_neutros_sem_certidao_nao_afirmam_conteudo_de_certidao():
    texto, status = obter_fundamentacao_padrao_vetor("antecedentes", desfavoravel=False)
    assert status == "favoravel"
    assert "certid" not in texto.lower()
    assert "presunção de primariedade" in texto
    com_cac, _ = obter_fundamentacao_padrao_vetor("antecedentes", desfavoravel=False, id_cac="ID-123456 (fls. 9)")
    assert "ID-123456 (fls. 9)" in com_cac and "certidão de antecedentes" in com_cac


def test_sumulas_citadas_pela_dosimetria_estao_na_base_fechada():
    refs = {item["referencia"] for item in SUMULAS_E_TEMAS_VERIFICADOS}
    assert {"Súmula 497/STF", "Súmula 659/STJ", "Súmula 269/STJ", "Tema Repetitivo 585/STJ"} <= refs
    ids = [item["id"] for item in SUMULAS_E_TEMAS_VERIFICADOS]
    assert len(ids) == len(set(ids))


def test_auditor_nao_bloqueia_sumula_497_como_citacao_externa():
    from app.generation.hallucination_auditor import auditar_sentenca

    texto = (
        "SENTENÇA\n\nI – RELATÓRIO\n\nRelatório.\n\nII – FUNDAMENTAÇÃO\n\n"
        "Nos termos do art. 119 do CP e da Súmula 497/STF, a prescrição regula-se pela pena de cada crime.\n\n"
        "III – DISPOSITIVO\n\nAnte o exposto, decido."
    )
    rel = auditar_sentenca(texto, [], [], [])
    assert not any("497" in str(c) for c in rel.citacoes_nao_autorizadas)


def test_helpers_publicos_da_api():
    assert normalizar_data("10/02/2025") == "2025-02-10"
    assert normalizar_override_metadado("data_fato", "10/02/2025") == (True, "2025-02-10")
    assert normalizar_override_metadado("crimes_imputados", "art_147_cp; art_129_p13_cp") == (
        True,
        ["art_147_cp", "art_129_p13_cp"],
    )
    assert resolver_data(None, "", "2025-03-01") == datetime.date(2025, 3, 1)
