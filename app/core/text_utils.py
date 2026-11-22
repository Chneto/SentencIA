"""Utilitários de texto compartilhados (fonte única de normalização).

Substituem as cópias locais ``_remover_acentos`` (adversarial_critic, hallucination_auditor,
sectional_pipeline), ``_normalize_match_text`` (main) e ``_norm_sem_acento``
(case_script_engine).
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["remover_acentos", "normalizar"]

_ESPACOS_RE = re.compile(r"\s+")


def remover_acentos(texto: str) -> str:
    """Remove marcas diacríticas (decomposição NFKD), preservando caixa e espaçamento.

    ``None`` ou string vazia retornam ``""``.
    """
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(texto))
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def normalizar(texto: str) -> str:
    """Forma canônica para comparações: sem acento, minúscula, espaços colapsados e ``strip()``."""
    if not texto:
        return ""
    return _ESPACOS_RE.sub(" ", remover_acentos(texto)).strip().lower()
