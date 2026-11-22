"""Contexto do caso calculado UMA vez por conteúdo.

``obter_contexto(bundle)`` classifica as peças (Laya), extrai os metadados e monta a matriz de
corroboração oral apenas quando o conteúdo muda. O resultado fica num cache LRU thread-safe
(8 entradas) indexado pelo ``fingerprint`` = sha256 do JSON canônico de ``raw_documents`` +
``transcriptions`` + ``metadados_override`` (+ flag demo e campos suplementares do bundle).

Mesmo conteúdo → o MESMO objeto ``CaseContext``. Por isso, consumidores NÃO devem mutar
``ctx.meta``/``ctx.pecas``/``ctx.bundle``: para alterar, use ``ctx.copia_meta()``
(= ``meta.model_copy(deep=True)``) ou ``ctx.copia_pecas()``.

Overrides do juiz (``bundle["metadados_override"]``: ``data_fato``,
``data_recebimento_denuncia``, ``crimes_imputados``, ``nome_ofendida``, ``nome_reu`` e demais campos
de ``MetadadosProcesso``) são aplicados sobre ``meta`` aqui dentro.
"""

from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import inspect
import json
import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Tuple

from app.core.case_flags import is_demo_bundle

if TYPE_CHECKING:  # pragma: no cover - apenas para tipagem
    from app.ingestion.laya_classifier import MetadadosProcesso, PecaClassificada
    from app.transcription.oral_proof_analyzer import MatrizCorroboracaoOral

logger = logging.getLogger(__name__)

__all__ = ["CaseContext", "obter_contexto", "limpar_cache_contexto", "calcular_fingerprint"]

_CACHE_MAX = 8
_CACHE: "OrderedDict[str, CaseContext]" = OrderedDict()
_CACHE_LOCK = threading.Lock()

# Campos do bundle que suprem metadados quando a extração não os encontra.
_SUPLEMENTOS_BUNDLE: Tuple[Tuple[str, str], ...] = (
    ("numero_processo", "numero_processo"),
    ("nome_ofendida", "ofendida"),
    ("nome_reu", "reu"),
    # Datas informadas no topo do bundle (fixtures, demo, integrações) completam a extração vazia.
    # Nunca são inventadas: só valem quando presentes e válidas (ISO ou dd/mm/aaaa).
    ("data_fato", "data_fato"),
    ("data_recebimento_denuncia", "data_recebimento_denuncia"),
)


@dataclass(frozen=True)
class CaseContext:
    fingerprint: str
    bundle: dict
    pecas: "list[PecaClassificada]"
    meta: "MetadadosProcesso"
    matriz_oral: "MatrizCorroboracaoOral"
    is_demo: bool
    ids_midias: Tuple[str, ...]
    transcriptions: "list[dict]"

    def copia_meta(self) -> "MetadadosProcesso":
        """Cópia profunda e mutável dos metadados (não afeta o cache)."""
        return self.meta.model_copy(deep=True)

    def copia_pecas(self) -> "list[PecaClassificada]":
        """Cópias mutáveis das peças classificadas (não afetam o cache)."""
        return [p.model_copy(deep=True) for p in self.pecas]


# ----------------------------------------------------------------------------------------------
# Fingerprint
# ----------------------------------------------------------------------------------------------
def _json_default(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=str)
    return str(obj)


def _como_dict(item: Any) -> Optional[dict]:
    if isinstance(item, Mapping):
        return dict(item)
    if hasattr(item, "model_dump"):
        try:
            return item.model_dump()
        except Exception:
            return None
    return None


def _overrides(bundle: Mapping[str, Any]) -> Dict[str, Any]:
    ov = bundle.get("metadados_override")
    return dict(ov) if isinstance(ov, Mapping) else {}


def calcular_fingerprint(bundle: Mapping[str, Any]) -> str:
    """sha256 do JSON canônico do conteúdo que determina o contexto."""
    canon = {
        "raw_documents": bundle.get("raw_documents") or [],
        "transcriptions": bundle.get("transcriptions") or [],
        "metadados_override": _overrides(bundle),
        "is_demo": is_demo_bundle(bundle),
        "suplementos": {chave: bundle.get(chave) for _, chave in _SUPLEMENTOS_BUNDLE},
    }
    texto = json.dumps(canon, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------------------------
# Overrides de metadados
# ----------------------------------------------------------------------------------------------
_DATA_BR_RE = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\s*$")


def _normalizar_data(valor: Any) -> Optional[str]:
    if isinstance(valor, _dt.datetime):
        return valor.date().isoformat()
    if isinstance(valor, _dt.date):
        return valor.isoformat()
    texto = str(valor or "").strip()
    if not texto:
        return None
    try:
        return _dt.date.fromisoformat(texto[:10]).isoformat()
    except ValueError:
        pass
    m = _DATA_BR_RE.match(texto)
    if m:
        try:
            return _dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return None
    return None


def _normalizar_override(campo: str, valor: Any) -> Tuple[bool, Any]:
    """(aceito, valor_normalizado). Valores vazios são ignorados (não apagam o extraído)."""
    if valor is None:
        return False, None
    if isinstance(valor, str) and not valor.strip():
        return False, None
    if campo.startswith("data_") and not isinstance(valor, bool):
        data = _normalizar_data(valor)
        if data is None:
            logger.warning("Override de metadado ignorado: data inválida em '%s'.", campo)
            return False, None
        return True, data
    if campo == "crimes_imputados":
        if isinstance(valor, str):
            itens = [c.strip() for c in re.split(r"[,;\n]", valor) if c.strip()]
        elif isinstance(valor, (list, tuple)):
            itens = [str(c).strip() for c in valor if str(c).strip()]
        else:
            return False, None
        return (True, itens) if itens else (False, None)
    if isinstance(valor, str):
        return True, valor.strip()
    return True, valor


#: API pública: normaliza um override de metadado -> (aceito, valor_normalizado).
normalizar_override_metadado = _normalizar_override
#: API pública: data em ISO ou dd/mm/aaaa (ou date/datetime) -> 'AAAA-MM-DD' | None.
normalizar_data = _normalizar_data


def _aplicar_overrides(meta: "MetadadosProcesso", overrides: Mapping[str, Any]) -> "MetadadosProcesso":
    if not overrides:
        return meta
    classe = type(meta)
    campos = classe.model_fields
    atual = meta.model_dump()
    for campo, bruto in overrides.items():
        if campo not in campos:
            logger.warning("Override de metadado ignorado: campo desconhecido '%s'.", campo)
            continue
        aceito, valor = _normalizar_override(campo, bruto)
        if not aceito:
            continue
        candidato = {**atual, campo: valor}
        try:
            classe.model_validate(candidato)
        except Exception:
            logger.warning("Override de metadado ignorado: valor inválido para '%s'.", campo)
            continue
        atual = candidato
    return classe.model_validate(atual)


def _aplicar_suplementos(meta: "MetadadosProcesso", bundle: Mapping[str, Any]) -> "MetadadosProcesso":
    atualizacoes: Dict[str, Any] = {}
    for campo_meta, chave_bundle in _SUPLEMENTOS_BUNDLE:
        if campo_meta not in type(meta).model_fields:
            continue
        valor_bundle = bundle.get(chave_bundle)
        if not getattr(meta, campo_meta, None) and isinstance(valor_bundle, str) and valor_bundle.strip():
            if campo_meta.startswith("data_"):
                data = _normalizar_data(valor_bundle)
                if data is None:
                    logger.warning("Suplemento de metadado ignorado: data inválida em '%s'.", chave_bundle)
                    continue
                atualizacoes[campo_meta] = data
            else:
                atualizacoes[campo_meta] = valor_bundle.strip()
    return meta.model_copy(update=atualizacoes) if atualizacoes else meta


# ----------------------------------------------------------------------------------------------
# Cálculo
# ----------------------------------------------------------------------------------------------
def _chamar_com_is_demo(func: Callable[..., Any], *args: Any, is_demo: bool) -> Any:
    """Passa ``is_demo`` apenas se a função o aceitar (migração gradual dos módulos)."""
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        params = {}
    aceita = "is_demo" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    if aceita:
        return func(*args, is_demo=is_demo)
    return func(*args)


def _coletar_ids_midias(transcriptions: List[dict], matriz_oral: Any) -> Tuple[str, ...]:
    ids: set[str] = set()
    for t in transcriptions:
        for chave in ("media_id", "alias"):
            v = t.get(chave)
            if isinstance(v, str) and v.strip():
                ids.add(v.strip())
        aliases = t.get("aliases")
        if isinstance(aliases, (list, tuple)):
            ids.update(a.strip() for a in aliases if isinstance(a, str) and a.strip())
    for dep in getattr(matriz_oral, "depoentes", None) or []:
        mid = getattr(dep, "media_id", None)
        if isinstance(mid, str) and mid.strip():
            ids.add(mid.strip())
    return tuple(sorted(ids))


def _construir(bundle: Mapping[str, Any], fingerprint: str) -> CaseContext:
    # Imports tardios: evitam import circular (laya_classifier/oral_proof_analyzer → generation → core).
    from app.ingestion.laya_classifier import classificar_pecas_autos, extrair_metadados_processo
    from app.transcription.oral_proof_analyzer import gerar_matriz_corroboracao_oral

    is_demo = is_demo_bundle(bundle)
    bundle_copia = copy.deepcopy(dict(bundle))
    raw_docs = [d for d in (_como_dict(x) for x in (bundle_copia.get("raw_documents") or [])) if d is not None]
    transcriptions = [
        t for t in (_como_dict(x) for x in (bundle_copia.get("transcriptions") or [])) if t is not None
    ]

    pecas = _chamar_com_is_demo(classificar_pecas_autos, raw_docs, is_demo=is_demo)
    meta = _chamar_com_is_demo(extrair_metadados_processo, pecas, is_demo=is_demo)
    meta = _aplicar_suplementos(meta, bundle_copia)
    meta = _aplicar_overrides(meta, _overrides(bundle_copia))
    matriz_oral = _chamar_com_is_demo(gerar_matriz_corroboracao_oral, pecas, transcriptions, is_demo=is_demo)

    return CaseContext(
        fingerprint=fingerprint,
        bundle=bundle_copia,
        pecas=list(pecas),
        meta=meta,
        matriz_oral=matriz_oral,
        is_demo=is_demo,
        ids_midias=_coletar_ids_midias(transcriptions, matriz_oral),
        transcriptions=transcriptions,
    )


def obter_contexto(bundle: dict) -> CaseContext:
    """Contexto do caso (cacheado por conteúdo). Levanta ``ValueError`` se ``bundle`` não for dict."""
    if not isinstance(bundle, Mapping):
        raise ValueError("O case_bundle fornecido deve ser um dicionário.")
    fingerprint = calcular_fingerprint(bundle)
    with _CACHE_LOCK:
        ctx = _CACHE.get(fingerprint)
        if ctx is not None:
            _CACHE.move_to_end(fingerprint)
            return ctx
    novo = _construir(bundle, fingerprint)  # fora do lock: classificação pode demorar
    with _CACHE_LOCK:
        existente = _CACHE.get(fingerprint)
        if existente is not None:  # outra thread calculou primeiro: devolve o mesmo objeto
            _CACHE.move_to_end(fingerprint)
            return existente
        _CACHE[fingerprint] = novo
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
        return novo


def limpar_cache_contexto() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
