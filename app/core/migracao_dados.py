"""Migração segura de dados legados (``<programa>/data`` -> pasta de dados fora do programa).

Regras invioláveis (TASK-033):
- **Copia** (nunca move nem apaga) os arquivos da pasta antiga para a nova;
- **Confere o SHA-256** de cada arquivo copiado antes de registrar a conclusão;
- **Nunca sobrescreve** um arquivo já existente no destino com conteúdo diferente;
- O original permanece intacto na pasta antiga até o usuário decidir apagar.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List

MARCA_MIGRACAO = ".migracao_concluida.json"
_PASTAS_IGNORADAS = frozenset({"logs", "__pycache__"})
_ARQUIVOS_IGNORADOS = frozenset({".instalado", ".servidor.json", ".launcher.lock", MARCA_MIGRACAO})
_SUFIXOS_IGNORADOS = (".tmp", ".parcial", ".lock")


def _sha256_arquivo(caminho: Path, bloco: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as fh:
        for parte in iter(lambda: fh.read(bloco), b""):
            h.update(parte)
    return h.hexdigest()


def _arquivos_elegiveis(origem: Path) -> List[Path]:
    resultado: List[Path] = []
    if not origem.is_dir():
        return resultado
    for raiz, subpastas, arquivos in os.walk(origem):
        subpastas[:] = sorted(sp for sp in subpastas if sp not in _PASTAS_IGNORADAS)
        base = Path(raiz)
        for nome in sorted(arquivos):
            if nome in _ARQUIVOS_IGNORADOS or nome.lower().endswith(_SUFIXOS_IGNORADOS):
                continue
            arq = base / nome
            if arq.is_file() and not arq.is_symlink():
                resultado.append(arq)
    return resultado


def migrar_dados_legados(origem: Path, destino: Path) -> Dict[str, Any]:
    """Copia e verifica por SHA-256 os dados de ``origem`` para ``destino`` sem apagar ``origem``."""
    orig = Path(origem).resolve()
    dest = Path(destino).resolve()
    if not orig.is_dir() or orig == dest:
        return {"migrado": False, "motivo": "sem_origem", "copiados": 0, "verificados": 0, "preservados_no_destino": 0}

    arquivos = _arquivos_elegiveis(orig)
    if not arquivos:
        return {"migrado": False, "motivo": "origem_vazia", "copiados": 0, "verificados": 0, "preservados_no_destino": 0}

    dest.mkdir(parents=True, exist_ok=True)
    copiados = 0
    verificados = 0
    preservados_no_destino = 0

    for src in arquivos:
        rel = src.relative_to(orig)
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        hash_src = _sha256_arquivo(src)

        if dst.exists():
            if dst.is_file() and _sha256_arquivo(dst) == hash_src:
                verificados += 1
            else:
                preservados_no_destino += 1
            continue

        shutil.copy2(src, dst)
        hash_dst = _sha256_arquivo(dst)
        if hash_dst != hash_src:
            try:
                dst.unlink()
            except OSError:
                pass
            raise OSError(f"Falha na conferência SHA-256 ao migrar {rel.as_posix()}.")
        copiados += 1
        verificados += 1

    marca = dest / MARCA_MIGRACAO
    payload = {
        "versao": 1,
        "concluida_em": datetime.datetime.now().isoformat(timespec="seconds"),
        "copiados": copiados,
        "verificados": verificados,
        "preservados_no_destino": preservados_no_destino,
    }
    fd, tmp_name = tempfile.mkstemp(prefix=".migracao_", suffix=".tmp", dir=str(dest))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, marca)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    return {
        "migrado": True,
        "motivo": "ok",
        "copiados": copiados,
        "verificados": verificados,
        "preservados_no_destino": preservados_no_destino,
    }
