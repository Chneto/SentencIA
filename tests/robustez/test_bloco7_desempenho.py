# -*- coding: utf-8 -*-
"""BLOCO 7 — Desempenho e complexidade (orçamento de tempo e retrocesso catastrófico de regex).

Método: testes de orçamento (performance budget) com entradas adversariais desenhadas para explorar
"catastrophic backtracking" em expressões regulares (repetições longas sem ponto final, quase-casamentos) e
autos grandes. O limite é folgado (CI lento), mas pega crescimento exponencial, que travaria o app numa peça real.
"""

from __future__ import annotations

import time

from app.generation.adversarial_critic import extrair_teses_defensivas, violacao_vedacao_honra
from app.ingestion.laya_classifier import _limpar_nome_pessoa, classificar_pecas_autos, extrair_metadados_processo


def _cronometrar(func, *args):
    inicio = time.perf_counter()
    func(*args)
    return time.perf_counter() - inicio


QUASE_CASAMENTOS = [
    "a defesa alega que " + "o réu agiu " * 4000,
    "desclassificação " + "do § 13 " * 3000 + "sem destino",
    "embriaguez " * 5000,
    "traição ciúme provocação " * 3000,
    "Acusado: " + "TICIO " * 5000,
    "art. 147 " * 8000,
]


def test_extrator_de_teses_sem_retrocesso_catastrofico():
    for texto in QUASE_CASAMENTOS:
        assert _cronometrar(extrair_teses_defensivas, [{"categoria": "ALEGACOES_FINAIS_DEFESA", "conteudo": texto}]) < 5.0, texto[:40]


def test_crivo_da_defesa_da_honra_sem_retrocesso_catastrofico():
    for texto in QUASE_CASAMENTOS:
        assert _cronometrar(violacao_vedacao_honra, texto) < 5.0, texto[:40]


def test_limpeza_de_nome_linear_no_tamanho():
    assert _cronometrar(_limpar_nome_pessoa, "TICIO " * 20000) < 2.0


def test_autos_grandes_processados_em_tempo_util():
    pecas = [{"doc_id": f"ID-{k}", "titulo": "Termo de Declarações",
              "conteudo": ("A vítima declarou que foi ameaçada em 10/01/2026. " * 400)} for k in range(40)]
    pecas.insert(0, {"doc_id": "ID-DEN", "titulo": "Denúncia Ministerial",
                     "conteudo": "O MINISTÉRIO PÚBLICO oferece DENÚNCIA em desfavor de TICIO FICTICIO, brasileiro, art. 147 do CP."})
    inicio = time.perf_counter()
    meta = extrair_metadados_processo(classificar_pecas_autos(pecas))
    assert time.perf_counter() - inicio < 20.0
    assert meta.nome_reu == "TICIO FICTICIO"
