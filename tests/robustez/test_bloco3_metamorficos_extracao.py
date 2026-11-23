# -*- coding: utf-8 -*-
"""BLOCO 3 — Testes metamórficos da extração (nomes e imputações).

Método: metamorphic testing. Sem saber o "resultado certo" de um PDF arbitrário, verifica-se que TRANSFORMAÇÕES que
não mudam o sentido dos autos não mudam a extração: quebras de linha e espaços extras no meio do nome, caixa do
rótulo, peças irrelevantes acrescentadas, ordem das peças secundárias. Casos gerados com nomes fictícios.
"""

from __future__ import annotations

import random

from app.ingestion.laya_classifier import classificar_pecas_autos, extrair_metadados_processo
from tests.robustez._gerador import casos, falha, nome_ficticio


def _den(nome_reu: str, nome_vit: str, extra: str = "") -> dict:
    return {
        "doc_id": "ID-DEN",
        "titulo": "Denúncia Ministerial",
        "conteudo": (
            f"O MINISTÉRIO PÚBLICO oferece DENÚNCIA em desfavor de {nome_reu}, brasileiro, pela prática do crime do "
            f"art. 147 do Código Penal. Vítima: {nome_vit}, brasileira. Fato ocorrido em 10/01/2026.{extra}"
        ),
    }


def _meta(docs):
    return extrair_metadados_processo(classificar_pecas_autos(docs))


def _base(rng: random.Random):
    return nome_ficticio(rng, 3), nome_ficticio(rng, 3)


def test_quebra_de_linha_e_espacos_no_meio_do_nome_nao_mudam_a_extracao():
    for i, (reu, vit) in casos(_base, n=30):
        original = _meta([_den(reu, vit)])
        partes = reu.split(" ")
        quebrado = partes[0] + "\n" + "   ".join(partes[1:])
        transformado = _meta([_den(quebrado, vit)])
        assert transformado.nome_reu == original.nome_reu == reu, falha(i, f"{reu!r} → {transformado.nome_reu!r}")


def test_peca_irrelevante_acrescentada_nao_muda_partes_nem_crimes():
    juntada = {"doc_id": "ID-JUNT", "titulo": "Certidão de Juntada", "conteudo": "Certifico a juntada de documentos."}
    for i, (reu, vit) in casos(_base, n=30):
        a = _meta([_den(reu, vit)])
        b = _meta([_den(reu, vit), juntada])
        assert (a.nome_reu, a.nome_ofendida, a.crimes_imputados) == (b.nome_reu, b.nome_ofendida, b.crimes_imputados), falha(i, "peça irrelevante mudou a extração")


def test_ordem_das_pecas_nao_muda_o_nome_quando_a_denuncia_existe():
    for i, (reu, vit) in casos(_base, n=30):
        capa = {"doc_id": "ID-CAPA", "titulo": "Capa", "conteudo": f"Polo Passivo: {reu.split()[0]}\n"}
        a = _meta([_den(reu, vit), capa])
        b = _meta([capa, _den(reu, vit)])
        assert a.nome_reu == b.nome_reu == reu, falha(i, f"ordem mudou o nome: {a.nome_reu!r} × {b.nome_reu!r}")


def test_rotulo_em_caixa_alta_equivale_ao_rotulo_normal():
    """ACUSADO:/VÍTIMA: (comum em cabeçalhos do PJe) deve render o mesmo que Acusado:/Vítima:."""
    rotulos = [("Acusado", "Vítima"), ("ACUSADO", "VÍTIMA"), ("DENUNCIADO", "OFENDIDA"), ("RÉU", "VÍTIMA"), ("Réu", "Ofendida"),
               ("Polo Passivo", "Vítima"), ("POLO PASSIVO", "OFENDIDA")]
    for i, (reu, vit) in casos(_base, n=20):
        for rot_reu, rot_vit in rotulos:
            meta = _meta([{"doc_id": "ID-ATA", "titulo": "Termo de Audiência", "conteudo": f"{rot_reu}: {reu}.\n{rot_vit}: {vit}."}])
            # Valor esperado fixo (revisão cruzada do CODEX): dois resultados vazios não podem "empatar".
            assert (meta.nome_reu, meta.nome_ofendida) == (reu, vit), falha(i, f"rótulos {rot_reu}/{rot_vit}: {meta.nome_reu!r}, {meta.nome_ofendida!r}")


def test_duplicar_a_mesma_denuncia_nao_duplica_imputacoes():
    for i, (reu, vit) in casos(_base, n=15):
        a = _meta([_den(reu, vit)])
        b = _meta([_den(reu, vit), {**_den(reu, vit), "doc_id": "ID-DEN-COPIA"}])
        assert a.crimes_imputados == b.crimes_imputados, falha(i, "cópia da denúncia duplicou crimes")
