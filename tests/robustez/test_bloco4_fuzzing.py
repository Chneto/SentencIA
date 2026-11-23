# -*- coding: utf-8 -*-
"""BLOCO 4 — Fuzzing de entradas (robustez a dados malformados).

Método: fuzz testing determinístico. Texto hostil (unicode raro, caracteres de controle, BOM, RTL, HTML, injeção,
cadeias enormes) alimenta as funções que recebem texto dos autos. Propriedade: nenhuma exceção inesperada (só os
erros de domínio previstos) e resposta dentro de um orçamento de tempo (OCR ruim não pode travar o app).
"""

from __future__ import annotations

import random
import time

from app.export.rich_text import sanitizar_html, texto_do_html
from app.generation.adversarial_critic import extrair_teses_defensivas
from app.ingestion.laya_classifier import _limpar_nome_pessoa, classificar_pecas_autos, extrair_metadados_processo
from tests.robustez._gerador import casos, falha, texto_hostil

ERROS_DE_DOMINIO = {"DadosInsuficientesError", "TipoPenalInexistenteNaDataError"}


def _sem_excecao_inesperada(i, func, *args):
    inicio = time.perf_counter()
    try:
        func(*args)
    except Exception as exc:
        assert type(exc).__name__ in ERROS_DE_DOMINIO, falha(i, f"{func.__name__} lançou {type(exc).__name__}: {exc}")
    return time.perf_counter() - inicio


def _docs(rng: random.Random):
    return [{"doc_id": f"ID-{k}", "titulo": texto_hostil(rng, 40), "conteudo": texto_hostil(rng)} for k in range(rng.randint(0, 4))]


def test_classificacao_e_metadados_aguentam_autos_hostis():
    for i, docs in casos(_docs, n=40):
        def _rodar(d):
            extrair_metadados_processo(classificar_pecas_autos(d))
        assert _sem_excecao_inesperada(i, _rodar, docs) < 5.0, falha(i, "extração lenta demais")


def test_limpeza_de_nome_aguenta_qualquer_texto():
    for i, txt in casos(lambda r: texto_hostil(r), n=150):
        assert _sem_excecao_inesperada(i, _limpar_nome_pessoa, txt) < 1.0
        assert isinstance(_limpar_nome_pessoa(txt), str)


def test_sanitizacao_e_texto_do_html_aguentam_html_quebrado():
    for i, txt in casos(lambda r: texto_hostil(r) + "<p><b><i>" * r.randint(0, 50), n=150):
        assert _sem_excecao_inesperada(i, sanitizar_html, txt) < 1.0
        assert _sem_excecao_inesperada(i, texto_do_html, txt) < 1.0


def test_extrator_de_teses_aguenta_peca_hostil():
    for i, txt in casos(lambda r: texto_hostil(r, 5000), n=40):
        assert _sem_excecao_inesperada(i, extrair_teses_defensivas, [{"categoria": "ALEGACOES_FINAIS_DEFESA", "conteudo": txt}]) < 3.0


def test_entradas_vazias_e_nulas():
    for i, entrada in enumerate(["", " ", "\n\n", "\x00", "﻿"]):
        _sem_excecao_inesperada(i, _limpar_nome_pessoa, entrada)
        _sem_excecao_inesperada(i, sanitizar_html, entrada)
        _sem_excecao_inesperada(i, lambda e: extrair_metadados_processo(classificar_pecas_autos([{"doc_id": "X", "titulo": e, "conteudo": e}])), entrada)
    _sem_excecao_inesperada(0, lambda: extrair_metadados_processo(classificar_pecas_autos([])))
