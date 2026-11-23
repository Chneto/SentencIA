# -*- coding: utf-8 -*-
"""Gerador determinístico de casos para os testes de robustez (TASK-072).

Testes baseados em propriedades sem dependência externa: cada teste roda N casos aleatórios a partir de uma semente
fixa (``ROBUSTEZ_SEED``, padrão 20261122). Uma falha imprime a semente e o índice do caso, e o caso é reproduzido
exatamente rodando de novo com a mesma semente. ``ROBUSTEZ_N`` aumenta a quantidade de casos em execuções longas.
Todos os dados são fictícios.
"""

from __future__ import annotations

import os
import random
import string
from typing import Callable, Iterator, TypeVar

SEMENTE = int(os.environ.get("ROBUSTEZ_SEED", "20261122"))
N_PADRAO = int(os.environ.get("ROBUSTEZ_N", "60"))

T = TypeVar("T")

PRENOMES = ["TICIO", "CAIO", "MEVIA", "TERCIA", "SEMPRONIO", "QUINTINA", "AULO", "LIVIA", "NUMERIO", "OTACILIA"]
SOBRENOMES = ["FICTICIO", "EXEMPLAR", "DIVERSO", "MODELAR", "HIPOTETICO", "AMOSTRAL", "TESTEIRO", "SIMULADO"]


def casos(gerar: Callable[[random.Random], T], n: int | None = None, semente: int | None = None) -> Iterator[tuple[int, T]]:
    """Itera ``(indice, caso)`` com RNG próprio por caso (o índice basta para reproduzir)."""
    base = SEMENTE if semente is None else semente
    for i in range(n or N_PADRAO):
        yield i, gerar(random.Random(base * 1_000_003 + i))


def nome_ficticio(rng: random.Random, partes: int | None = None) -> str:
    qtd = partes or rng.randint(2, 4)
    nomes = [rng.choice(PRENOMES)] + [rng.choice(SOBRENOMES) for _ in range(qtd - 1)]
    if qtd >= 3 and rng.random() < 0.4:
        nomes.insert(rng.randint(1, qtd - 1), rng.choice(["DA", "DE", "DOS"]))
    return " ".join(nomes)


def texto_hostil(rng: random.Random, tamanho: int | None = None) -> str:
    """Texto com unicode variado, controle, HTML, aspas, quebras e trechos jurídicos soltos (fuzzing)."""
    pedacos = [
        string.ascii_letters, string.digits, string.punctuation, " \n\t\r", "áéíóúâêôãõçÁÉÍÓÚÇ",
        "​ ﻿", "§º°ª", "<script>alert(1)</script>", "<img src=x onerror=alert(1)>",
        "art. 147, § 1º, do CP", "Acusado:", "Vítima:", "DENÚNCIA em desfavor de", "\x00\x07\x1b",
        "😀🧪", "ǅǆ", "‮", "\\", "{{}}%s%n", "../../..", "'; DROP TABLE x;--",
    ]
    n = tamanho if tamanho is not None else rng.randint(0, 2000)
    out = []
    while sum(len(p) for p in out) < n:
        fonte = rng.choice(pedacos)
        out.append("".join(rng.choice(fonte) for _ in range(rng.randint(1, 12))) if rng.random() < 0.6 else fonte)
    return "".join(out)[:n]


def falha(semente_caso: int, descricao: str) -> str:
    return f"[ROBUSTEZ_SEED={SEMENTE} caso={semente_caso}] {descricao}"
