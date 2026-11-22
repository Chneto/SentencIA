# -*- coding: utf-8 -*-
"""Política de retenção do arquivo morto (``data/cases/_arquivados/``).

Rotina segura, auditável e opcional (Fase 5):
- decide quais zips ``AAAAMMDD_HHMMSS[_N].zip`` expiram a partir do timestamp no nome do arquivo
  (nunca pelo ``mtime``);
- ``simular=True`` (padrão) apenas lista o que seria removido, sem tocar no disco;
- na remoção real: apaga cada zip elegível (tratando ``PermissionError`` por arquivo no Windows),
  retira do ``indice_hashes.json`` as entradas que apontam para zips removidos (gravação atômica)
  e acrescenta uma linha em ``_arquivados/retencao_log.jsonl`` contendo apenas nomes de zip
  (nunca nomes de arquivos internos);
- executa sob ``STATE_LOCK`` para não concorrer com o reset completo.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.session_state import (
    ARCHIVE_INDEX_NAME,
    ARCHIVE_TIMESTAMP_FORMAT,
    STATE_LOCK,
    _archive_root,
    _gravar_indice,
    _resolve_cases_dir,
    ler_indice_arquivo_morto,
)

logger = logging.getLogger(__name__)

RETENCAO_LOG_NAME = "retencao_log.jsonl"
_PADRAO_ZIP_ARQUIVO = re.compile(r"^(\d{8}_\d{6})(?:_(\d+))?\.zip$")


@dataclass(frozen=True)
class _ZipCandidato:
    caminho: Path
    nome: str
    dt: datetime.datetime
    sufixo: int


def _validar_criterios(manter_dias: Optional[int], manter_ultimos: Optional[int]) -> None:
    if manter_dias is None and manter_ultimos is None:
        raise ValueError("Informe ao menos um critério de retenção (manter_dias ou manter_ultimos).")
    if manter_dias is not None and (isinstance(manter_dias, bool) or not isinstance(manter_dias, int) or manter_dias <= 0):
        raise ValueError("manter_dias deve ser um número inteiro maior que zero.")
    if manter_ultimos is not None and (isinstance(manter_ultimos, bool) or not isinstance(manter_ultimos, int) or manter_ultimos <= 0):
        raise ValueError("manter_ultimos deve ser um número inteiro maior que zero.")


def _listar_zips_validos(root: Path) -> List[_ZipCandidato]:
    """Lista apenas zips diretos dentro de ``root`` que casam com ``AAAAMMDD_HHMMSS[_N].zip``."""
    resolved_root = root.resolve()
    encontrados: List[_ZipCandidato] = []
    for item in root.iterdir():
        try:
            if item.is_symlink():
                continue
            if not item.is_file():
                continue
            if item.resolve().parent != resolved_root:
                continue
        except (OSError, RuntimeError):
            continue
        m = _PADRAO_ZIP_ARQUIVO.match(item.name)
        if not m:
            continue
        try:
            dt = datetime.datetime.strptime(m.group(1), ARCHIVE_TIMESTAMP_FORMAT)
        except ValueError:
            continue
        sufixo = int(m.group(2)) if m.group(2) else 1
        encontrados.append(_ZipCandidato(caminho=item, nome=item.name, dt=dt, sufixo=sufixo))

    # Mais recentes primeiro
    encontrados.sort(key=lambda c: (c.dt, c.sufixo, c.nome), reverse=True)
    return encontrados


def _registrar_log_retencao(
    root: Path,
    *,
    executado_em: str,
    manter_dias: Optional[int],
    manter_ultimos: Optional[int],
    zips_removidos: List[str],
    falhas: List[Dict[str, str]],
    bytes_liberados: int,
    indice_entradas_removidas: int,
) -> None:
    """Grava registro auditável em JSONL contendo apenas nomes de zip (nunca nomes internos)."""
    log_path = root / RETENCAO_LOG_NAME
    if log_path.resolve().parent != root.resolve():
        return
    registro = {
        "executado_em": executado_em,
        "criterios": {
            "manter_dias": manter_dias,
            "manter_ultimos": manter_ultimos,
        },
        "zips_removidos": list(zips_removidos),
        "falhas": [{"zip": f["zip"], "erro": f["erro"]} for f in falhas],
        "bytes_liberados": int(bytes_liberados),
        "indice_entradas_removidas": int(indice_entradas_removidas),
    }
    linha = json.dumps(registro, ensure_ascii=False) + "\n"
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(linha)


def aplicar_retencao(
    *,
    manter_dias: Optional[int],
    manter_ultimos: Optional[int],
    simular: bool = True,
    cases_dir: Optional[Path] = None,
    agora: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """Aplica (ou simula) a política de retenção sobre ``data/cases/_arquivados/``.

    Um zip é mantido se atender a **qualquer** critério informado:
    - ``manter_dias``: timestamp do nome do arquivo dentro da janela ``agora - manter_dias``;
    - ``manter_ultimos``: entre os ``N`` zips mais recentes.
    """
    _validar_criterios(manter_dias, manter_ultimos)
    ref_agora = agora or datetime.datetime.now()
    limite_data = (
        ref_agora - datetime.timedelta(days=manter_dias) if manter_dias is not None else None
    )

    with STATE_LOCK:
        alvo_cases = cases_dir or _resolve_cases_dir()
        root = _archive_root(alvo_cases)
        resultado: Dict[str, Any] = {
            "simular": bool(simular),
            "criterios": {"manter_dias": manter_dias, "manter_ultimos": manter_ultimos},
            "mantidos": [],
            "elegiveis_remocao": [],
            "removidos": [],
            "falhas": [],
            "bytes_liberados": 0,
            "indice_entradas_removidas": 0,
        }
        if root is None or not root.exists() or not root.is_dir():
            return resultado

        candidatos = _listar_zips_validos(root)
        mantidos: List[_ZipCandidato] = []
        expirados: List[_ZipCandidato] = []

        for idx, cand in enumerate(candidatos):
            por_dias = limite_data is not None and cand.dt >= limite_data
            por_ultimos = manter_ultimos is not None and idx < manter_ultimos
            if por_dias or por_ultimos:
                mantidos.append(cand)
            else:
                expirados.append(cand)

        resultado["mantidos"] = [c.nome for c in mantidos]
        resultado["elegiveis_remocao"] = [c.nome for c in expirados]

        if simular or not expirados:
            return resultado

        resolved_root = root.resolve()
        removidos: List[str] = []
        falhas: List[Dict[str, str]] = []
        bytes_liberados = 0

        for cand in expirados:
            try:
                if cand.caminho.is_symlink() or not cand.caminho.is_file():
                    continue
                if cand.caminho.resolve().parent != resolved_root:
                    continue
                tamanho = int(cand.caminho.stat().st_size)
                cand.caminho.unlink()
                removidos.append(cand.nome)
                bytes_liberados += tamanho
            except PermissionError as exc:
                nome_erro = type(exc).__name__
                logger.warning("Arquivo morto em uso, não foi possível remover %s (%s)", cand.nome, nome_erro)
                falhas.append({"zip": cand.nome, "erro": nome_erro})
            except OSError as exc:
                nome_erro = type(exc).__name__
                logger.warning("Erro ao remover zip do arquivo morto %s (%s)", cand.nome, nome_erro)
                falhas.append({"zip": cand.nome, "erro": nome_erro})

        entradas_removidas = 0
        if removidos and (root / ARCHIVE_INDEX_NAME).exists():
            indice = ler_indice_arquivo_morto(alvo_cases)
            arquivos = dict(indice.get("arquivos") or {})
            removidos_set = set(removidos)
            chaves_remover = [h for h, reg in arquivos.items() if isinstance(reg, dict) and reg.get("zip") in removidos_set]
            for h in chaves_remover:
                arquivos.pop(h, None)
            entradas_removidas = len(chaves_remover)
            indice["arquivos"] = arquivos
            _gravar_indice(root, indice)

        try:
            _registrar_log_retencao(
                root,
                executado_em=ref_agora.isoformat(timespec="seconds"),
                manter_dias=manter_dias,
                manter_ultimos=manter_ultimos,
                zips_removidos=removidos,
                falhas=falhas,
                bytes_liberados=bytes_liberados,
                indice_entradas_removidas=entradas_removidas,
            )
        except OSError as exc:
            nome_erro = type(exc).__name__
            logger.warning("Falha ao gravar log de retenção (%s)", nome_erro)
            resultado["aviso_log"] = f"Não foi possível gravar o log de retenção ({nome_erro})."

        resultado["removidos"] = removidos
        resultado["falhas"] = falhas
        resultado["bytes_liberados"] = bytes_liberados
        resultado["indice_entradas_removidas"] = entradas_removidas
        return resultado
