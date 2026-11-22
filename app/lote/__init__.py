# -*- coding: utf-8 -*-
"""Pacote de pré-análise de processos em lote (Fase 5 / TASK-016)."""

from app.lote.manager import (
    LOTE_MANAGER,
    arquivar_lotes_no_reset,
    diretorio_lotes,
)

__all__ = ["LOTE_MANAGER", "arquivar_lotes_no_reset", "diretorio_lotes"]
