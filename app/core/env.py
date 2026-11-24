"""Leitura centralizada de variáveis de ambiente ``SENTENCIA_*`` com compatibilidade legada ``JVDFM_*``."""

from __future__ import annotations

import logging
import os
import threading
from typing import Mapping, Optional

logger = logging.getLogger("sentencia.env")

PREFIXO_ATUAL = "SENTENCIA_"
PREFIXO_LEGADO = "JVDFM_"

_AVISADOS_LOCK = threading.Lock()
_AVISADOS: set[str] = set()


def ler_env(
    sufixo: str,
    padrao: Optional[str] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """Lê ``SENTENCIA_<sufixo>``; se ausente, aceita ``JVDFM_<sufixo>`` (legado) e avisa uma vez."""
    mapa = os.environ if env is None else env
    chave_nova = f"{PREFIXO_ATUAL}{sufixo}"
    val_novo = mapa.get(chave_nova)
    if val_novo is not None and str(val_novo).strip() != "":
        return str(val_novo)

    chave_legada = f"{PREFIXO_LEGADO}{sufixo}"
    val_legado = mapa.get(chave_legada)
    if val_legado is not None and str(val_legado).strip() != "":
        with _AVISADOS_LOCK:
            if chave_legada not in _AVISADOS:
                _AVISADOS.add(chave_legada)
                logger.warning(
                    "Variável de ambiente legada %s em uso; prefira %s.",
                    chave_legada,
                    chave_nova,
                )
        return str(val_legado)
    return padrao


def reset_avisos_env_legado() -> None:
    """Limpa o registro de avisos já emitidos (uso em testes)."""
    with _AVISADOS_LOCK:
        _AVISADOS.clear()
