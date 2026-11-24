"""Preservação de ocorrências e apreciação motivada em peças fictícias."""

import pytest

from app.generation.hallucination_auditor import auditar_secao_merito, auditar_sentenca
from app.ingestion.laya_classifier import (
    classificar_pecas_autos, extrair_metadados_processo, extrair_pedidos_defesa_texto, extrair_pedidos_mp_texto,
)
from tests.test_pontos_decisivos import _doc


def test_repeticoes_de_pedidos_e_teses_de_dois_fatos_conservam_individualidade():
    docs = [
        _doc("ID-DEN", "Denúncia Ministerial",
             "Fato 1: Em 10/03/2024, o acusado praticou ameaça do art. 147 do CP.\n"
             "Fato 2: Em 21/03/2024, o acusado praticou ameaça do art. 147 do CP."),
        _doc("ID-MP", "Alegações Finais do MP",
             "Fato 1: O MP requer a condenação do réu pelo art. 147 do CP; "
             "Fato 2: O MP requer a condenação do réu pelo art. 147 do CP."),
        _doc("ID-DEF", "Alegações Finais da Defesa",
             "Fato 1: A defesa sustenta insuficiência probatória.\n"
             "Fato 2: A defesa sustenta insuficiência probatória.\n"
             "Subsidiariamente, requer a fixação da pena-base no mínimo legal."),
    ]
    meta = extrair_metadados_processo(classificar_pecas_autos(docs))
    ids = {i.numero_fato: i.id_imputacao for i in meta.imputacoes}
    assert set(ids) == {"1", "2"}
    condenacoes = [p for p in meta.pedidos_mp if p.fonte_id == "ID-MP" and p.tipo.startswith("condenacao")]
    assert [p.id_imputacao for p in condenacoes] == [ids["1"], ids["2"]]
    assert [p.ordem for p in condenacoes] == sorted(p.ordem for p in condenacoes)
    assert all(p.parte == "mp" and p.natureza == "principal" for p in condenacoes)
    insuficiencia = [t for t in meta.teses_defesa if t.codigo == "MER-01"]
    assert [t.id_imputacao for t in insuficiencia] == [ids["1"], ids["2"]]
    assert all(t.fonte_id == "ID-DEF" and t.parte == "defesa" for t in insuficiencia)
    assert any(p.natureza == "subsidiario" for p in meta.pedidos_defesa)


TESE = {"codigo": "MER-01", "titulo": "Insuficiência probatória", "tipo": "MERITO", "numero_fato": "1"}


@pytest.mark.parametrize("trecho", [
    "Fato 1. A defesa sustenta insuficiência probatória (MER-01).",
    "Fato 1. Rejeito a tese de insuficiência probatória (MER-01).",
    "Fato 1. A defesa sustenta insuficiência probatória (MER-01). Rejeito a nulidade da citação, pois houve intimação pessoal.",
    "Fato 2. Rejeito a tese de insuficiência probatória (MER-01), pois o laudo registrou a lesão.",
])
def test_mencao_ou_conclusao_sem_motivo_da_mesma_tese_fica_omitida(trecho):
    """CPP, art. 381, III, em vigor desde 01/01/1942 (arts. 381 e 810).
    https://planalto.gov.br/ccivil_03/decreto-lei/del3689compilado.htm
    """
    resultado = auditar_secao_merito(trecho, [], [TESE])
    assert not resultado.teses_defesa_enfrentadas
    assert len(resultado.teses_omitidas) == 1


def test_conclusao_motivada_na_secao_correta_e_pendencia_explicita():
    fundamentacao = "Fato 1. Rejeito a tese de insuficiência probatória (MER-01), pois o laudo registrou a lesão."
    errado = auditar_sentenca(
        "I – RELATÓRIO\n\n" + fundamentacao + "\n\nII – FUNDAMENTAÇÃO\n\n2. Do Mérito\n\nSem apreciação.",
        [], [TESE],
    )
    assert errado.teses_omitidas and not errado.teses_defesa_enfrentadas
    certo = auditar_sentenca(
        "I – RELATÓRIO\n\nRelato.\n\nII – FUNDAMENTAÇÃO\n\n2. Do Mérito\n\n" + fundamentacao,
        [], [TESE],
    )
    assert certo.teses_defesa_enfrentadas and not certo.teses_omitidas
    pendente = auditar_secao_merito(
        "Fato 1. [COMPLETAR PELO JUÍZO: apreciação da tese MER-01 — Insuficiência probatória]", [], [TESE],
    )
    assert pendente.teses_pendentes and not pendente.teses_defesa_enfrentadas and not pendente.teses_omitidas


def test_pedidos_expressos_nao_se_confundem_com_citacoes_ou_negativas():
    mp = extrair_pedidos_mp_texto(
        "O dano moral está previsto no art. 387, IV, do CPP.", fonte_id="ID-MP-FICTICIO", origem="denuncia",
    )
    assert not any(pedido.tipo == "dano_moral" for pedido in mp)
    defesa = extrair_pedidos_defesa_texto(
        "A defesa não requer a extinção da punibilidade. "
        "Requer a absolvição no fato 1. Subsidiariamente, pede a desclassificação no fato 2.",
        fonte_id="ID-DEF-FICTICIO",
    )
    assert [(p.tipo, p.natureza) for p in defesa] == [
        ("absolvicao", "principal"), ("desclassificacao", "subsidiario"),
    ]
