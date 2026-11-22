"""Limpeza segura de artefatos de demonstração e teste (TASK-031).

Identifica e remove **somente** artefatos explicitamente marcados como demo/teste:
- ``data/cases/.ultimo_caso_real.json`` quando contiver bundle de demo/teste;
- caches ``*.pje_cache_v2.json`` e ``*.transcript_v2.json`` em ``data/cases/`` com marcador explícito
  de demo/teste (ou nome prefixado por ``demo_`` / ``test_demo_`` / ``e2e_test_``);
- lotes em ``data/lote/`` cujo manifesto tenha marcador explícito de demo/teste.

**Nunca** toca em autos reais (PDF/mídias/DOCX), exportações (``data/exports``), cofre
(``data/secrets``), configurações (``data/user_settings.json``), acervo jurídico
(``data/knowledge_base``), templates do gabinete ou arquivo morto (``_arquivados``).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List

from app.core.case_flags import is_demo_or_test_bundle

ULTIMO_CASO_FILENAME = ".ultimo_caso_real.json"
_CACHE_SUFIXOS = (".pje_cache_v2.json", ".transcript_v2.json")
_PREFIXOS_NOME_DEMO_TESTE = ("demo_", "test_demo_", "e2e_test_")


def _json_seguro(caminho: Path) -> Any:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _relativo_seguro(alvo: Path, base: Path) -> str:
    try:
        return str(alvo.resolve().relative_to(base.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return alvo.name


def _cache_eh_demo_ou_teste(caminho: Path) -> str | None:
    nome_lower = caminho.name.lower()
    if any(nome_lower.startswith(pref) for pref in _PREFIXOS_NOME_DEMO_TESTE):
        return "cache de demonstração/teste (nome)"
    dados = _json_seguro(caminho)
    if isinstance(dados, dict):
        if is_demo_or_test_bundle(dados):
            return "cache com marcador demo/teste"
        bundle = dados.get("bundle")
        if isinstance(bundle, dict) and is_demo_or_test_bundle(bundle):
            return "cache com bundle demo/teste"
    return None


def listar_artefatos_demo(data_dir: Path) -> List[Dict[str, str]]:
    """Retorna lista de artefatos de demo/teste identificáveis dentro de ``data_dir``."""
    base = Path(data_dir).resolve()
    itens: List[Dict[str, str]] = []
    if not base.is_dir():
        return itens

    # Verifica .ultimo_caso_real.json na raiz de data/ (e também em data/cases/ por retrocompatibilidade)
    for ultimo in (base / ULTIMO_CASO_FILENAME, base / "cases" / ULTIMO_CASO_FILENAME):
        if ultimo.is_file():
            dados = _json_seguro(ultimo)
            bundle = dados.get("bundle") if isinstance(dados, dict) else None
            if isinstance(bundle, dict) and is_demo_or_test_bundle(bundle):
                itens.append(
                    {
                        "caminho": _relativo_seguro(ultimo, base),
                        "absoluto": str(ultimo.resolve()),
                        "tipo": "arquivo",
                        "motivo": "estado persistido de demonstração/teste",
                    }
                )

    cases_dir = base / "cases"
    if cases_dir.is_dir():
        try:
            filhos = sorted(cases_dir.iterdir())
        except OSError:
            filhos = []
        for arq in filhos:
            if not arq.is_file():
                continue
            if not arq.name.lower().endswith(_CACHE_SUFIXOS):
                continue
            motivo = _cache_eh_demo_ou_teste(arq)
            if motivo:
                itens.append(
                    {
                        "caminho": _relativo_seguro(arq, base),
                        "absoluto": str(arq.resolve()),
                        "tipo": "arquivo",
                        "motivo": motivo,
                    }
                )

    lote_dir = base / "lote"
    if lote_dir.is_dir():
        try:
            subpastas = sorted(lote_dir.iterdir())
        except OSError:
            subpastas = []
        for entry in subpastas:
            if not entry.is_dir():
                continue
            for nome_manifesto in ("manifest.json", "lote.json", "estado.json"):
                manifesto = entry / nome_manifesto
                if manifesto.is_file():
                    dados = _json_seguro(manifesto)
                    if isinstance(dados, dict) and is_demo_or_test_bundle(dados):
                        itens.append(
                            {
                                "caminho": _relativo_seguro(entry, base),
                                "absoluto": str(entry.resolve()),
                                "tipo": "pasta",
                                "motivo": "lote com marcador demo/teste",
                            }
                        )
                    break

    return itens


def limpar_artefatos_demo(
    data_dir: Path,
    *,
    simular: bool = True,
    confirmar: bool = False,
) -> Dict[str, Any]:
    """Simula ou remove os artefatos de demonstração/teste identificados em ``data_dir``.

    Exige ``confirmar=True`` quando ``simular=False``. Nunca remove arquivos fora de ``data_dir``.
    """
    if not simular and confirmar is not True:
        raise ValueError("A remoção de dados de demonstração exige simular=False e confirmar=True.")

    base = Path(data_dir).resolve()
    itens = listar_artefatos_demo(base)
    if simular:
        return {
            "simulado": True,
            "total": len(itens),
            "itens": [{"caminho": i["caminho"], "motivo": i["motivo"], "tipo": i["tipo"]} for i in itens],
            "removidos": [],
        }

    removidos: List[str] = []
    for item in itens:
        alvo = Path(item["absoluto"]).resolve()
        try:
            alvo.relative_to(base)
        except ValueError:
            continue
        try:
            if item["tipo"] == "pasta" and alvo.is_dir():
                shutil.rmtree(alvo)
                removidos.append(item["caminho"])
            elif alvo.is_file():
                alvo.unlink()
                removidos.append(item["caminho"])
        except OSError:
            continue

    return {
        "simulado": False,
        "total": len(removidos),
        "itens": [{"caminho": i["caminho"], "motivo": i["motivo"], "tipo": i["tipo"]} for i in itens],
        "removidos": removidos,
    }
