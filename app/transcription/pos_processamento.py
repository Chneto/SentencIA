# -*- coding: utf-8 -*-
"""Pós-processamento da transcrição: ajusta nomes próprios e o número do processo.

O Whisper erra nomes e números ("Carlos Eduarto", "0804, 590, 12, 2025…"). Aqui o texto é aproximado dos
nomes realmente conhecidos nos autos, **sem inventar nada**: só se troca um trecho quando ele é muito
parecido com um nome conhecido, e o texto original do Whisper é sempre preservado em ``texto_whisper``
(o ajuste fica visível e reversível).
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Sequence, Tuple

_PARTICULAS = {"da", "de", "do", "das", "dos", "e"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _tokens_nome(nome: str) -> List[str]:
    nome = re.sub(r"\s*\(Parte\s+\d+\)\s*$", "", nome or "", flags=re.IGNORECASE)
    return [t for t in re.findall(r"[A-Za-zÀ-ÿ]+", nome) if t]


def _titulo(token: str) -> str:
    return token.lower() if token.lower() in _PARTICULAS else token.capitalize()


def nomes_validos(nomes: Iterable[str]) -> List[List[str]]:
    """Lista de nomes (como listas de tokens), sem vazios, genéricos ou duplicados."""
    vistos, saida = set(), []
    for n in nomes:
        toks = _tokens_nome(n)
        sig = tuple(_norm(t) for t in toks)
        if len(toks) < 2 or sig in vistos:
            continue
        if _norm(" ".join(toks)) in {"nao identificado", "depoente", "reu", "ofendida"}:
            continue
        vistos.add(sig)
        saida.append(toks)
    return saida


def _ajustar_nomes(texto: str, nomes: Sequence[List[str]]) -> Tuple[str, List[Tuple[str, str]]]:
    """Troca por nomes conhecidos os trechos muito parecidos. Devolve (texto, [(antes, depois)])."""
    trocas: List[Tuple[str, str]] = []
    palavras = list(re.finditer(r"[A-Za-zÀ-ÿ]+", texto))
    if not palavras:
        return texto, trocas
    # substituições de trás para frente, para não deslocar os índices
    candidatos: List[Tuple[int, int, str, str]] = []
    usados = [False] * len(palavras)
    for toks in nomes:
        alvo_sig = [_norm(t) for t in toks if _norm(t) not in _PARTICULAS]
        if not alvo_sig:
            continue
        # 1) sequência de 2+ palavras do nome (ex.: "Carlos Eduarto Ferreira" ~ "Carlos Eduardo Ferreira")
        for n in range(min(len(toks), 4), 1, -1):
            for ini in range(0, len(toks) - n + 1):
                sub = toks[ini : ini + n]
                alvo = " ".join(_norm(t) for t in sub)
                for i in range(0, len(palavras) - n + 1):
                    if any(usados[i : i + n]):
                        continue
                    trecho = texto[palavras[i].start() : palavras[i + n - 1].end()]
                    if any(c not in " " and not c.isalpha() for c in trecho):
                        continue
                    if len(re.findall(r"[A-Za-zÀ-ÿ]+", trecho)) != n:
                        continue
                    cand = _norm(trecho)
                    if cand == alvo:
                        for k in range(i, i + n):
                            usados[k] = True
                        if trecho != " ".join(_titulo(t) if t.lower() in _PARTICULAS else _titulo(t) for t in sub):
                            candidatos.append(
                                (palavras[i].start(), palavras[i + n - 1].end(), trecho, " ".join(_titulo(t) for t in sub))
                            )
                        continue
                    if difflib.SequenceMatcher(None, cand, alvo).ratio() >= 0.82 and cand != alvo:
                        for k in range(i, i + n):
                            usados[k] = True
                        candidatos.append(
                            (palavras[i].start(), palavras[i + n - 1].end(), trecho, " ".join(_titulo(t) for t in sub))
                        )
        # 2) sobrenome/prenome isolado, só se distintivo (>= 6 letras) e muito parecido
        for tok in toks:
            if len(tok) < 6 or _norm(tok) in _PARTICULAS:
                continue
            alvo = _norm(tok)
            for i, m in enumerate(palavras):
                if usados[i]:
                    continue
                cand = _norm(m.group())
                if cand == alvo or abs(len(cand) - len(alvo)) > 2:
                    continue
                if difflib.SequenceMatcher(None, cand, alvo).ratio() >= 0.86:
                    usados[i] = True
                    candidatos.append((m.start(), m.end(), m.group(), _titulo(tok)))
    candidatos.sort(key=lambda c: c[0], reverse=True)
    saida = texto
    for ini, fim, antes, depois in candidatos:
        if antes == depois:
            continue
        saida = saida[:ini] + depois + saida[fim:]
        trocas.append((antes, depois))
    trocas.reverse()
    return saida, trocas


_RE_SEQ_NUM = re.compile(r"(?<![\w])\d[\d\s,.\-/]{14,}\d(?![\w])")


def _ajustar_numero_processo(texto: str, numero: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Reescreve números de processo ditados ("0800, 802, 40, 2025…") se casam com o número dos autos."""
    alvo = re.sub(r"\D", "", numero or "")
    if len(alvo) != 20:
        return texto, []
    trocas: List[Tuple[str, str]] = []

    def _sub(m: re.Match[str]) -> str:
        digs = re.sub(r"\D", "", m.group())
        if digs == alvo or difflib.SequenceMatcher(None, digs, alvo).ratio() >= 0.8:
            if m.group() != numero:
                trocas.append((m.group(), numero))
            return numero
        return m.group()

    return _RE_SEQ_NUM.sub(_sub, texto), trocas


def aprimorar_segmentos(
    segmentos: List[Dict[str, Any]],
    *,
    nomes: Iterable[str] = (),
    numero_processo: str = "",
) -> List[Dict[str, Any]]:
    """Devolve os segmentos com nomes e número do processo ajustados.

    Cada segmento alterado ganha ``texto_whisper`` (original) e ``ajustes`` (texto "antes → depois; ...").
    Idempotente: segmentos já ajustados não são alterados de novo.
    """
    lista_nomes = nomes_validos(nomes)
    saida: List[Dict[str, Any]] = []
    for seg in segmentos or []:
        novo = dict(seg)
        if "texto_whisper" in novo:
            saida.append(novo)
            continue
        original = str(novo.get("texto_literal") or "")
        texto, ajustes = _ajustar_numero_processo(original, numero_processo)
        texto, aj_nomes = _ajustar_nomes(texto, lista_nomes)
        ajustes += aj_nomes
        if ajustes and texto != original:
            novo["texto_whisper"] = original
            novo["texto_literal"] = texto
            novo["ajustes"] = "; ".join(f"{a} → {b}" for a, b in ajustes)
        saida.append(novo)
    return saida
