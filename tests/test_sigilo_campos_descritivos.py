# -*- coding: utf-8 -*-
"""TASK-046: campos descritivos do caso (pontos decisivos, pedidos) não viram "nomes" no dicionário de sigilo."""

from __future__ import annotations

from tests.test_publicacao_e_sigilo import verificar_sigilo as vs


def test_parece_nome_de_pessoa():
    assert vs._parece_nome_de_pessoa("TICIO FICTICIO DA SILVA")
    assert vs._parece_nome_de_pessoa("Mevia Exemplar dos Santos")
    assert not vs._parece_nome_de_pessoa("gestante")
    assert not vs._parece_nome_de_pessoa("Agravante da reincidência (art. 61, I, CP) com reflexos no regime")
    assert not vs._parece_nome_de_pessoa("reu_preso")


def test_campos_descritivos_nao_alimentam_tokens_de_nomes():
    dic = vs.DicionarioSigilo()
    caso = {
        "nome_reu": "TICIO FICTICIO DA SILVA",
        "pontos_decisivos": [
            {"chave": "vitima_condicao", "valor": "contexto domestico com descendentes presentes"},
            {"chave": "reu_preso", "efeito": "Direito de recorrer em liberdade e detracao penal"},
        ],
        "vitima_condicao": "contexto domestico com descendentes presentes",
    }
    vs._extrair_json_em_memoria(caso, dic)
    assert dic.nomes_completos_reais  # o nome de verdade (fictício aqui) continua entrando
    for comum in ("contexto", "domestico", "descendentes", "presentes", "liberdade", "detracao"):
        assert comum not in dic.tokens_nomes_reais
