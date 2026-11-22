"""
Modelos de formatação do gabinete.

Um modelo reúne o que o magistrado costuma repetir em toda sentença: fonte, tamanho, entrelinhas do
corpo, recuo de primeira linha, margens e numeração de páginas. Os modelos de fábrica ficam no código;
os do usuário em ``templates_gabinete/formatos_gabinete.json`` (pasta que o reset da sessão não toca).
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core import config as core_config
from app.export.rich_text import MARGENS_ABNT, normalizar_formatacao

_ARQUIVO = "formatos_gabinete.json"
_LOCK = threading.Lock()
_NOME_MAX = 60

#: Modelos de fábrica (somente leitura).
MODELOS_PADRAO: List[Dict[str, Any]] = [
    {
        "nome": "ABNT padrão — Times 12, entrelinhas 1,5",
        "fonte": "Times New Roman", "tamanho_pt": 12.0, "entrelinhas": 1.5, "recuo_primeira_cm": 1.25,
        "margens_cm": dict(MARGENS_ABNT), "numerar_paginas": True,
    },
    {
        "nome": "Compacto — Arial 11, entrelinhas 1,15",
        "fonte": "Arial", "tamanho_pt": 11.0, "entrelinhas": 1.15, "recuo_primeira_cm": 1.25,
        "margens_cm": {"superior": 2.5, "esquerda": 2.5, "inferior": 2.5, "direita": 2.5}, "numerar_paginas": True,
    },
    {
        "nome": "Leitura ampliada — Times 14, entrelinhas 1,5",
        "fonte": "Times New Roman", "tamanho_pt": 14.0, "entrelinhas": 1.5, "recuo_primeira_cm": 1.25,
        "margens_cm": dict(MARGENS_ABNT), "numerar_paginas": True,
    },
]


def _arquivo() -> Path:
    return Path(core_config.TEMPLATES_DIR) / _ARQUIVO


def normalizar_modelo(bruto: Dict[str, Any], nome: Optional[str] = None) -> Dict[str, Any]:
    """Modelo validado: nome limpo e formatação normalizada (limites de margem, fonte e tamanho)."""
    nome_limpo = re.sub(r"\s+", " ", str(nome if nome is not None else bruto.get("nome", ""))).strip()[:_NOME_MAX]
    if not nome_limpo:
        raise ValueError("Informe um nome para o modelo.")
    fmt = normalizar_formatacao(bruto)
    try:
        entrelinhas = round(min(max(float(bruto.get("entrelinhas", 1.5)), 1.0), 3.0), 2)
    except (TypeError, ValueError):
        entrelinhas = 1.5
    try:
        recuo = round(min(max(float(bruto.get("recuo_primeira_cm", 1.25)), 0.0), 4.0), 2)
    except (TypeError, ValueError):
        recuo = 1.25
    return {
        "nome": nome_limpo,
        "fonte": fmt.get("fonte", "Times New Roman"),
        "tamanho_pt": fmt.get("tamanho_pt", 12.0),
        "entrelinhas": entrelinhas,
        "recuo_primeira_cm": recuo,
        "margens_cm": fmt["margens_cm"],
        "numerar_paginas": fmt["numerar_paginas"],
    }


def _ler_usuario() -> List[Dict[str, Any]]:
    try:
        bruto = json.loads(_arquivo().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    saida: List[Dict[str, Any]] = []
    for item in bruto if isinstance(bruto, list) else []:
        try:
            saida.append(normalizar_modelo(item))
        except (ValueError, AttributeError):
            continue
    return saida


def listar_modelos() -> List[Dict[str, Any]]:
    """Modelos de fábrica (``padrao=True``) seguidos dos do usuário."""
    fabrica = [dict(normalizar_modelo(m), padrao=True) for m in MODELOS_PADRAO]
    with _LOCK:
        return fabrica + [dict(m, padrao=False) for m in _ler_usuario()]


def salvar_modelo(bruto: Dict[str, Any]) -> Dict[str, Any]:
    """Cria ou substitui (mesmo nome) um modelo do usuário. Nome de modelo de fábrica é recusado."""
    modelo = normalizar_modelo(bruto)
    if modelo["nome"].casefold() in {m["nome"].casefold() for m in MODELOS_PADRAO}:
        raise ValueError("Esse nome é de um modelo de fábrica; escolha outro nome.")
    with _LOCK:
        atuais = [m for m in _ler_usuario() if m["nome"].casefold() != modelo["nome"].casefold()]
        atuais.append(modelo)
        core_config._gravar_json_atomico(_arquivo(), json.dumps(atuais, ensure_ascii=False, indent=2))
    return modelo


def excluir_modelo(nome: str) -> bool:
    with _LOCK:
        atuais = _ler_usuario()
        restantes = [m for m in atuais if m["nome"].casefold() != str(nome).strip().casefold()]
        if len(restantes) == len(atuais):
            return False
        core_config._gravar_json_atomico(_arquivo(), json.dumps(restantes, ensure_ascii=False, indent=2))
    return True
