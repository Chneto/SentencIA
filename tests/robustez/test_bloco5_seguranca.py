# -*- coding: utf-8 -*-
"""BLOCO 5 — Segurança e privacidade (testes adversariais).

Método: casos de ataque conhecidos (OWASP: XSS por tag, atributo de evento, URL ``javascript:``, SVG/iframe; path
traversal em nome de arquivo) e propriedades de privacidade da pseudonimização: ida e volta sem perda
(``despseudonimizar(pseudonimizar(x)) == x``) e nenhum nome real remanescente no texto pseudonimizado.
Nomes, números e textos são fictícios.
"""

from __future__ import annotations

import random
import re
from types import SimpleNamespace

import pytest

from app.export.nomeacao import limpar_nome
from app.export.rich_text import sanitizar_html
from app.generation.case_script_engine import (
    construir_mapa_pseudonimizacao,
    despseudonimizar_texto,
    detectar_vazamento_pseudonimizacao,
    pseudonimizar_texto,
)
from tests.robustez._gerador import casos, falha, nome_ficticio

ATAQUES_XSS = [
    "<script>alert(1)</script><p>texto</p>",
    "<p onclick=\"alert(1)\">texto</p>",
    "<img src=x onerror=alert(1)>",
    "<a href=\"javascript:alert(1)\">x</a>",
    "<a href=\"JaVaScRiPt:alert(1)\">x</a>",
    "<iframe src=\"https://exemplo.invalid\"></iframe>",
    "<svg onload=alert(1)><p>x</p></svg>",
    "<p style=\"background:url(javascript:alert(1))\">x</p>",
    "<object data=\"x\"></object><embed src=\"x\">",
    "<scr<script>ipt>alert(1)</script>",
]


@pytest.mark.parametrize("ataque", ATAQUES_XSS)
def test_sanitizacao_remove_vetores_de_xss(ataque):
    limpo = sanitizar_html(ataque).lower()
    assert "<script" not in limpo
    assert not re.search(r"\bon[a-z]+\s*=", limpo), limpo
    assert "javascript:" not in limpo
    for tag in ("<iframe", "<object", "<embed", "<svg"):
        assert tag not in limpo, (tag, limpo)


@pytest.mark.parametrize("entrada", ["../../Windows/System32/x", "..\\..\\x", "a/b\\c:d*e?f\"g<h>i|j", "CON", "nome.." , "\x00nulo"])
def test_nome_de_arquivo_sem_travessia_de_pasta_nem_caracteres_ilegais(entrada):
    nome = limpar_nome(entrada)
    assert "/" not in nome and "\\" not in nome and ".." not in nome
    assert not re.search(r'[<>:"|?*\x00-\x1f]', nome), nome


def _cenario(rng: random.Random):
    return SimpleNamespace(
        nome_ofendida=nome_ficticio(rng, 3),
        nome_reu=nome_ficticio(rng, 3),
        numero_processo=f"0{rng.randint(100000, 999999)}-{rng.randint(10, 99)}.2025.8.00.0001",
        autos_mpu_apenso="Sem apenso registrado",
    )


def test_pseudonimizacao_ida_e_volta_sem_perda_e_sem_vazamento():
    for i, meta in casos(_cenario, n=40):
        texto = (
            f"Processo {meta.numero_processo}. A vítima {meta.nome_ofendida} relatou que {meta.nome_reu} a ameaçou. "
            f"{meta.nome_reu.title()} negou. Aplica-se a Lei Maria da Penha."
        )
        mapa = construir_mapa_pseudonimizacao(meta, texto)
        pseudo = pseudonimizar_texto(texto, mapa)
        assert meta.nome_reu not in pseudo.upper() and meta.nome_ofendida not in pseudo.upper(), falha(i, "nome real no texto pseudonimizado")
        assert meta.numero_processo not in pseudo, falha(i, "número do processo vazou")
        assert not detectar_vazamento_pseudonimizacao(pseudo, mapa), falha(i, "detector acusou vazamento")
        # A restauração devolve os nomes na forma canônica (MAIÚSCULAS, padrão forense); o conteúdo não se perde.
        assert despseudonimizar_texto(pseudo, mapa).upper() == texto.upper(), falha(i, "ida e volta perdeu informação")
        assert "Lei Maria da Penha" in pseudo


def test_detector_acusa_vazamento_quando_nome_real_permanece():
    meta = SimpleNamespace(nome_ofendida="MEVIA EXEMPLAR SOUZA", nome_reu="TICIO FICTICIO DA SILVA",
                           numero_processo="0804590-12.2025.8.00.0001", autos_mpu_apenso="")
    mapa = construir_mapa_pseudonimizacao(meta, "")
    assert detectar_vazamento_pseudonimizacao("O réu TICIO FICTICIO DA SILVA ameaçou.", mapa)
