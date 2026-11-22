"""
Banco de trechos do gabinete: fundamentações e fórmulas que o magistrado repete em várias sentenças.

Cada trecho tem título, categoria, texto e um *atalho* (``/atalho`` + Tab na folha insere o texto).
Ficam em ``templates_gabinete/trechos_gabinete.json`` (o reset da sessão não toca nessa pasta).
Os trechos de exemplo são fórmulas neutras com marcadores ``[COMPLETAR PELO JUÍZO: ...]``: o conteúdo
jurídico de mérito é sempre do juízo.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core import config as core_config

_ARQUIVO = "trechos_gabinete.json"
_LOCK = threading.Lock()
_ATALHO_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,23}$")
_TEXTO_MAX = 12000
_MAX_TRECHOS = 500

#: Exemplos iniciais (somente leitura; ``padrao=True``).
TRECHOS_PADRAO: List[Dict[str, str]] = [
    {
        "id": "padrao-vistos", "atalho": "vistos", "categoria": "Relatório", "titulo": "Abertura (Vistos etc.)",
        "texto": "Vistos etc.",
    },
    {
        "id": "padrao-fecho", "atalho": "fecho", "categoria": "Dispositivo", "titulo": "Fórmula de encerramento",
        "texto": "Publique-se. Registre-se. Intimem-se.",
    },
    {
        "id": "padrao-exec", "atalho": "execucao", "categoria": "Dispositivo", "titulo": "Ressalva para a execução penal",
        "texto": (
            "Ressalva-se que, em sede executória, perante o Juízo da Execução Penal, deverão ser observados "
            "[COMPLETAR PELO JUÍZO: parâmetros de progressão de regime e demais benefícios aplicáveis ao caso]."
        ),
    },
    {
        "id": "padrao-prelim", "atalho": "prelim", "categoria": "Preliminares",
        "titulo": "Modelo de enfrentamento de preliminar",
        "texto": (
            "[COMPLETAR PELO JUÍZO: identificar a preliminar arguida]. Não merece acolhimento / Merece acolhimento a tese "
            "defensiva, porque [COMPLETAR PELO JUÍZO: fundamentos e referência às peças (ID-...)]."
        ),
    },
]


def _arquivo() -> Path:
    return Path(core_config.TEMPLATES_DIR) / _ARQUIVO


def normalizar_trecho(bruto: Dict[str, Any]) -> Dict[str, str]:
    titulo = re.sub(r"\s+", " ", str(bruto.get("titulo", ""))).strip()[:80]
    categoria = re.sub(r"\s+", " ", str(bruto.get("categoria", ""))).strip()[:40] or "Geral"
    atalho = str(bruto.get("atalho", "")).strip().lstrip("/").lower()
    texto = str(bruto.get("texto", "")).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not titulo:
        raise ValueError("Informe um título para o trecho.")
    if not texto:
        raise ValueError("O texto do trecho não pode ficar vazio.")
    if len(texto) > _TEXTO_MAX:
        raise ValueError(f"O trecho excede {_TEXTO_MAX} caracteres.")
    if atalho and not _ATALHO_RE.match(atalho):
        raise ValueError("Atalho inválido: use 2 a 24 letras minúsculas, números, '-' ou '_' (sem espaços nem acentos).")
    return {
        "id": str(bruto.get("id") or "").strip()[:40] or uuid.uuid4().hex[:12],
        "atalho": atalho,
        "categoria": categoria,
        "titulo": titulo,
        "texto": texto,
    }


def _ler_usuario() -> List[Dict[str, str]]:
    try:
        bruto = json.loads(_arquivo().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    saida: List[Dict[str, str]] = []
    for item in bruto if isinstance(bruto, list) else []:
        try:
            saida.append(normalizar_trecho(item))
        except (ValueError, AttributeError):
            continue
    return saida


def listar_trechos() -> List[Dict[str, Any]]:
    """Trechos de exemplo (``padrao=True``) e os do usuário, ordenados por categoria e título."""
    with _LOCK:
        todos = [dict(t, padrao=True) for t in TRECHOS_PADRAO] + [dict(t, padrao=False) for t in _ler_usuario()]
    return sorted(todos, key=lambda t: (t["categoria"].casefold(), t["titulo"].casefold()))


def salvar_trecho(bruto: Dict[str, Any]) -> Dict[str, str]:
    """Cria ou atualiza (mesmo ``id``) um trecho do usuário. Atalho não pode repetir o de outro trecho."""
    trecho = normalizar_trecho(bruto)
    if trecho["id"].startswith("padrao-"):
        raise ValueError("Os trechos de exemplo não podem ser alterados; salve uma cópia com outro título.")
    with _LOCK:
        atuais = _ler_usuario()
        if trecho["atalho"]:
            outros = [t for t in atuais if t["id"] != trecho["id"]] + TRECHOS_PADRAO
            if any(t["atalho"] == trecho["atalho"] for t in outros):
                raise ValueError(f"O atalho /{trecho['atalho']} já está em uso por outro trecho.")
        restantes = [t for t in atuais if t["id"] != trecho["id"]]
        if len(restantes) >= _MAX_TRECHOS:
            raise ValueError(f"Limite de {_MAX_TRECHOS} trechos atingido.")
        restantes.append(trecho)
        core_config._gravar_json_atomico(_arquivo(), json.dumps(restantes, ensure_ascii=False, indent=2))
    return trecho


def excluir_trecho(trecho_id: str) -> bool:
    with _LOCK:
        atuais = _ler_usuario()
        restantes = [t for t in atuais if t["id"] != str(trecho_id).strip()]
        if len(restantes) == len(atuais):
            return False
        core_config._gravar_json_atomico(_arquivo(), json.dumps(restantes, ensure_ascii=False, indent=2))
    return True


def trecho_por_atalho(atalho: str) -> Optional[Dict[str, Any]]:
    a = str(atalho or "").strip().lstrip("/").lower()
    return next((t for t in listar_trechos() if a and t["atalho"] == a), None)
