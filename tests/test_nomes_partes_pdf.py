# -*- coding: utf-8 -*-
"""TASK-046: nome completo do réu e da ofendida mesmo com quebra de linha no meio do nome (PDF do PJe).

Todos os nomes abaixo são fictícios.
"""

from __future__ import annotations

from app.ingestion.laya_classifier import (
    _completar_nome_pela_qualificacao,
    _limpar_nome_pessoa,
    classificar_pecas_autos,
    extrair_metadados_processo,
)


def _doc(doc_id: str, titulo: str, conteudo: str) -> dict:
    return {"doc_id": doc_id, "titulo": titulo, "conteudo": conteudo}


def test_limpar_nome_corta_na_qualificacao_e_em_rotulos():
    assert _limpar_nome_pessoa("TICIO FICTICIO DA SILVA, brasileiro, solteiro") == "TICIO FICTICIO DA SILVA"
    assert _limpar_nome_pessoa("TICIO\nFICTICIO DA SILVA brasileiro") == "TICIO FICTICIO DA SILVA"
    assert _limpar_nome_pessoa("TICIO FICTICIO\nVÍTIMA: MEVIA") == "TICIO FICTICIO"
    assert _limpar_nome_pessoa("Ticio Ficticio dos Santos, portador do RG") == "TICIO FICTICIO DOS SANTOS"
    assert _limpar_nome_pessoa("TICIO DE") == "TICIO"


def test_reu_com_nome_quebrado_em_duas_linhas_no_pdf():
    pecas = classificar_pecas_autos([
        _doc(
            "ID-DEN",
            "Denúncia",
            "O MINISTÉRIO PÚBLICO oferece DENÚNCIA em desfavor de TICIO\nFICTICIO DA SILVA, brasileiro, solteiro, "
            "pela prática do crime do art. 147 do Código Penal. Vítima: MEVIA\nEXEMPLAR SOUZA, brasileira.",
        ),
    ])
    meta = extrair_metadados_processo(pecas)
    assert meta.nome_reu == "TICIO FICTICIO DA SILVA"
    assert meta.nome_ofendida == "MEVIA EXEMPLAR SOUZA"


def test_prefere_o_nome_mais_completo_entre_os_rotulos():
    pecas = classificar_pecas_autos([
        _doc("ID-CAPA", "Capa", "Polo Passivo: TICIO\n"),
        _doc("ID-DEN", "Denúncia", "Denunciado: TICIO FICTICIO DA SILVA, brasileiro."),
    ])
    assert extrair_metadados_processo(pecas).nome_reu == "TICIO FICTICIO DA SILVA"


def test_so_prenome_e_completado_pela_qualificacao_com_alerta():
    texto = "Acusado: TICIO.\n... qualificação: TICIO FICTICIO DA SILVA, brasileiro, nascido em 01/01/1990."
    assert _completar_nome_pela_qualificacao("TICIO", texto) == "TICIO FICTICIO DA SILVA"
    pecas = classificar_pecas_autos([_doc("ID-ATA", "Termo de Audiência", texto)])
    meta = extrair_metadados_processo(pecas)
    assert meta.nome_reu == "TICIO FICTICIO DA SILVA"
    assert any("completado" in a for a in meta.alertas_extracao)


def test_so_prenome_sem_qualificacao_gera_alerta_de_nome_incompleto():
    pecas = classificar_pecas_autos([_doc("ID-ATA", "Termo de Audiência", "Acusado: TICIO.\nNada mais.")])
    meta = extrair_metadados_processo(pecas)
    assert meta.nome_reu == "TICIO"
    assert any("incompleto" in a for a in meta.alertas_extracao)


def test_nomes_divergentes_nao_sao_escolhidos_pelo_tamanho():
    """Revisão cruzada do CODEX (TASK-046): nome diferente na capa, mesmo mais longo, não substitui o da denúncia."""
    pecas = classificar_pecas_autos([
        _doc("ID-DEN", "Denúncia", "Denunciado: TICIO FICTICIO, brasileiro."),
        _doc("ID-CAPA", "Capa", "Polo Passivo: CAIO EXEMPLAR DE SOUZA NETO\n"),
    ])
    meta = extrair_metadados_processo(pecas)
    assert meta.nome_reu == "TICIO FICTICIO"
    assert any("diverge" in a for a in meta.alertas_extracao)

def test_mesmo_rotulo_em_pecas_diferentes_gera_alerta_e_prioriza_a_denuncia():
    """Revisão cruzada do CODEX (TASK-071): mesmo rótulo na denúncia e em outra peça, com pessoas diferentes."""
    pecas = classificar_pecas_autos([
        _doc("ID-CAPA", "Capa", "Acusado: CAIO EXEMPLAR DE SOUZA NETO\nOfendida: TERCIA DIVERSA LIMA\n"),
        _doc("ID-DEN", "Denúncia Ministerial", "O MINISTÉRIO PÚBLICO oferece DENÚNCIA. Acusado: TICIO FICTICIO, brasileiro.\nOfendida: MEVIA EXEMPLAR, brasileira."),
    ])
    meta = extrair_metadados_processo(pecas)
    assert meta.nome_reu == "TICIO FICTICIO"
    assert meta.nome_ofendida == "MEVIA EXEMPLAR"
    assert any("acusado diverge" in a for a in meta.alertas_extracao)
    assert any("ofendida diverge" in a for a in meta.alertas_extracao)


def test_rotulo_diferente_na_capa_nao_vence_a_denuncia():
    pecas = classificar_pecas_autos([
        _doc("ID-CAPA", "Capa", "Denunciado: CAIO EXEMPLAR DE SOUZA NETO\n"),
        _doc("ID-DEN", "Denúncia Ministerial", "O MINISTÉRIO PÚBLICO oferece DENÚNCIA. Acusado: TICIO FICTICIO, brasileiro."),
    ])
    meta = extrair_metadados_processo(pecas)
    assert meta.nome_reu == "TICIO FICTICIO"
    assert any("acusado diverge" in a for a in meta.alertas_extracao)