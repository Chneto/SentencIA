"""
Comparação entre duas versões da sentença (parágrafo a parágrafo e, nos alterados, palavra a palavra).

Serve ao painel *Versões*: mostra o que mudou entre a minuta gerada/auditada e a edição do magistrado,
sem depender de ferramentas externas.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List

_TOKEN_RE = re.compile(r"\s+|[^\s]+")
#: Abaixo desta semelhança, um par de parágrafos é tratado como "removido + adicionado" inteiros.
_SEMELHANCA_MINIMA = 0.35
#: Limite de parágrafos comparados em cada texto (proteção contra entrada gigante).
_MAX_PARAGRAFOS = 3000


def _paragrafos(texto: str) -> List[str]:
    # Qualquer quebra de linha separa parágrafos: o texto do motor une "título\ncorpo" e as ordens em
    # linhas simples, enquanto a folha os guarda como parágrafos próprios; o mesmo conteúdo não é diferença.
    return [re.sub(r"[ \t]+", " ", p).strip() for p in re.split(r"\n+", str(texto or "")) if p.strip()][:_MAX_PARAGRAFOS]


def _palavras(a: str, b: str) -> List[Dict[str, str]]:
    """Segmentos ``{"op": "=" | "-" | "+", "t": texto}`` da diferença entre ``a`` e ``b``."""
    ta, tb = _TOKEN_RE.findall(a), _TOKEN_RE.findall(b)
    segmentos: List[Dict[str, str]] = []

    def _add(op: str, tokens: List[str]) -> None:
        if not tokens:
            return
        texto = "".join(tokens)
        if segmentos and segmentos[-1]["op"] == op:
            segmentos[-1]["t"] += texto
        else:
            segmentos.append({"op": op, "t": texto})

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if tag == "equal":
            _add("=", ta[i1:i2])
        else:
            _add("-", ta[i1:i2])
            _add("+", tb[j1:j2])
    return segmentos


def _contar(segmentos: List[Dict[str, str]], op: str) -> int:
    return sum(len(s["t"].split()) for s in segmentos if s["op"] == op)


def comparar(texto_a: str, texto_b: str) -> Dict[str, Any]:
    """Compara ``texto_a`` (base) com ``texto_b`` (novo).

    Retorna ``{"blocos": [...], "resumo": {...}}``; cada bloco é
    ``{"tipo": "igual" | "alterado" | "removido" | "adicionado", "segmentos": [{"op", "t"}]}``.
    """
    pa, pb = _paragrafos(texto_a), _paragrafos(texto_b)
    blocos: List[Dict[str, Any]] = []
    palavras_add = palavras_rem = alterados = 0

    def _inteiro(tipo: str, texto: str) -> None:
        nonlocal palavras_add, palavras_rem
        op = "-" if tipo == "removido" else "+"
        n = len(texto.split())
        if tipo == "removido":
            palavras_rem += n
        else:
            palavras_add += n
        blocos.append({"tipo": tipo, "segmentos": [{"op": op, "t": texto}]})

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, pa, pb, autojunk=False).get_opcodes():
        if tag == "equal":
            for p in pa[i1:i2]:
                blocos.append({"tipo": "igual", "segmentos": [{"op": "=", "t": p}]})
            continue
        trecho_a, trecho_b = pa[i1:i2], pb[j1:j2]
        usados_b: set[int] = set()
        grande = len(trecho_a) * len(trecho_b) > 40000  # evita custo quadrático: vira removido + adicionado
        for a in trecho_a:
            # melhor parágrafo ainda livre do outro lado, mantendo a ordem relativa
            melhor, melhor_r = None, 0.0
            for idx, b in enumerate([] if grande else trecho_b):
                if idx in usados_b:
                    continue
                r = difflib.SequenceMatcher(None, a, b, autojunk=False).quick_ratio()
                if r > melhor_r:
                    melhor, melhor_r = idx, r
            if melhor is not None and melhor_r >= _SEMELHANCA_MINIMA:
                # emite antes os adicionados que precedem o par
                for idx in range(melhor):
                    if idx not in usados_b:
                        usados_b.add(idx)
                        _inteiro("adicionado", trecho_b[idx])
                usados_b.add(melhor)
                segs = _palavras(a, trecho_b[melhor])
                palavras_add += _contar(segs, "+")
                palavras_rem += _contar(segs, "-")
                alterados += 1
                blocos.append({"tipo": "alterado", "segmentos": segs})
            else:
                _inteiro("removido", a)
        for idx, b in enumerate(trecho_b):
            if idx not in usados_b:
                _inteiro("adicionado", b)

    houve = any(b["tipo"] != "igual" for b in blocos)
    return {
        "blocos": blocos,
        "resumo": {
            "paragrafos_alterados": alterados,
            "paragrafos_removidos": sum(1 for b in blocos if b["tipo"] == "removido"),
            "paragrafos_adicionados": sum(1 for b in blocos if b["tipo"] == "adicionado"),
            "palavras_adicionadas": palavras_add,
            "palavras_removidas": palavras_rem,
            "identicos": not houve,
        },
    }
