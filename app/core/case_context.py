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
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from app.core.case_flags import is_demo_bundle

if TYPE_CHECKING:  # pragma: no cover - apenas para tipagem
    from app.ingestion.laya_classifier import MetadadosProcesso, PecaClassificada
    from app.transcription.oral_proof_analyzer import MatrizCorroboracaoOral

logger = logging.getLogger(__name__)

__all__ = ["CaseContext", "obter_contexto", "limpar_cache_contexto", "calcular_fingerprint"]

_CACHE_MAX = 8
_CACHE: "OrderedDict[str, CaseContext]" = OrderedDict()
_CACHE_GERACAO: "OrderedDict[Tuple[Any, ...], Dict[str, Any]]" = OrderedDict()
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


def _aplicar_confirmacoes_pontos(atual: Dict[str, Any], confirmacoes: Mapping[str, Any]) -> Dict[str, Any]:
    """Aplica confirmações explícitas do juiz sobre ``pontos_decisivos`` aos campos de cálculo."""
    if not isinstance(confirmacoes, Mapping):
        return atual
    pontos = [dict(p) if isinstance(p, Mapping) else p for p in (atual.get("pontos_decisivos") or [])]
    por_chave = {p.get("chave"): p for p in pontos if isinstance(p, dict) and p.get("chave")}
    conf_efetivos = dict(atual.get("pontos_decisivos_confirmados") or {})

    for chave, bruto in confirmacoes.items():
        if bruto is None or chave == "antecedentes":
            continue
        ponto = por_chave.get(chave)
        if bruto is False:
            if ponto is not None:
                ponto["confirmado"] = False
            conf_efetivos[chave] = False
            continue

        val_final = ponto.get("valor") if (bruto is True and ponto is not None) else bruto
        if chave == "data_nascimento_reu":
            dt_norm = _normalizar_data(val_final)
            if dt_norm is None:
                continue
            val_final = dt_norm
            atual["data_nascimento_reu"] = dt_norm

        conf_efetivos[chave] = val_final
        if ponto is not None:
            ponto["valor"] = val_final
            ponto["confirmado"] = True
            ponto["estado"] = "extraido"

    atual["pontos_decisivos"] = pontos
    atual["pontos_decisivos_confirmados"] = conf_efetivos
    return atual


def _aplicar_overrides(meta: "MetadadosProcesso", overrides: Mapping[str, Any]) -> "MetadadosProcesso":
    if not overrides:
        return meta
    classe = type(meta)
    campos = classe.model_fields
    atual = meta.model_dump()
    for campo, bruto in overrides.items():
        if campo in {
            "registros_antecedentes", "reu_primario_sumula444", "confissoes_sugeridas",
            "intervalos_custodia", "alertas_custodia",
        }:
            continue
        if campo == "pontos_decisivos_confirmados" and isinstance(bruto, Mapping):
            candidato = _aplicar_confirmacoes_pontos(dict(atual), bruto)
            try:
                classe.model_validate(candidato)
                atual = candidato
            except Exception:
                logger.warning("Override de metadado ignorado: valor inválido para '%s'.", campo)
            continue
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
    meta = extrair_metadados_processo(pecas, is_demo=is_demo, transcricoes=transcriptions)
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


def _extrair_vinculos_decisoes(obj: Any, ids_out: set[str], trechos_out: Dict[str, List[str]]) -> None:
    if isinstance(obj, Mapping):
        fid = obj.get("fonte_id") or obj.get("fonte_inicio") or obj.get("fonte_fim")
        trecho = obj.get("trecho") or obj.get("trecho_literal")
        if isinstance(fid, str) and fid.strip():
            fid_limpo = fid.strip()
            ids_out.add(fid_limpo)
            if isinstance(trecho, str) and trecho.strip():
                trechos_out.setdefault(fid_limpo, []).append(trecho.strip())
        for v in obj.values():
            if isinstance(v, (Mapping, list, tuple)):
                _extrair_vinculos_decisoes(v, ids_out, trechos_out)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            if isinstance(item, (Mapping, list, tuple)):
                _extrair_vinculos_decisoes(item, ids_out, trechos_out)


def _recortar_janela_trecho(conteudo: str, citacoes: Sequence[str], max_chars: int) -> Tuple[str, str]:
    texto = (conteudo or "").strip()
    if not texto:
        return "", "inicio"
    texto_low = texto.lower()
    for cit in citacoes:
        cit_limpa = (cit or "").strip()
        if not cit_limpa:
            continue
        pos = texto_low.find(cit_limpa.lower())
        if pos >= 0:
            if len(texto) <= max_chars:
                return texto, "trecho_vinculado"
            margem = max(0, (max_chars - len(cit_limpa)) // 2)
            ini = max(0, pos - margem)
            fim = min(len(texto), ini + max_chars)
            if fim == len(texto):
                ini = max(0, fim - max_chars)
            return texto[ini:fim], "trecho_vinculado"
    return texto[:max_chars], "inicio"


def recorte_trechos_vinculados(
    ctx: CaseContext,
    *,
    decisoes: Any = None,
    ids_vinculados: Optional[Sequence[str]] = None,
    trechos_citados: Optional[Mapping[str, Sequence[str]]] = None,
    max_chars_por_peca: int = 1200,
    max_total_chars: int = 8000,
    oral_literal: bool = False,
) -> Dict[str, Any]:
    """Recorta apenas os trechos das peças e mídias vinculadas às decisões/pontos do caso.

    Quando houver trecho citado para a peça, recorta uma janela centrada no trecho
    (``recorte="trecho_vinculado"``); sem trecho citado localizado, usa o início (``recorte="inicio"``).
    """
    vinculados: set[str] = {str(x).strip() for x in (ids_vinculados or []) if str(x).strip()}
    citacoes_por_id: Dict[str, List[str]] = {}
    for fid, lista in (trechos_citados or {}).items():
        fid_limpo = str(fid).strip()
        if fid_limpo:
            vinculados.add(fid_limpo)
            citacoes_por_id.setdefault(fid_limpo, []).extend(str(c).strip() for c in lista if str(c).strip())

    if decisoes is not None:
        _extrair_vinculos_decisoes(decisoes, vinculados, citacoes_por_id)

    ids_citados = set(citacoes_por_id)
    for ponto in getattr(ctx.meta, "pontos_decisivos", None) or []:
        fid = getattr(ponto, "fonte_id", None) if not isinstance(ponto, Mapping) else ponto.get("fonte_id")
        tr = getattr(ponto, "trecho", None) if not isinstance(ponto, Mapping) else ponto.get("trecho")
        if fid:
            fid_s = str(fid).strip()
            vinculados.add(fid_s)
            if tr and str(tr).strip():
                citacoes_por_id.setdefault(fid_s, []).append(str(tr).strip())
    for reg in getattr(ctx.meta, "registros_antecedentes", None) or []:
        fid = getattr(reg, "fonte_id", None) if not isinstance(reg, Mapping) else reg.get("fonte_id")
        if fid:
            vinculados.add(str(fid).strip())
    for imp in getattr(ctx.meta, "imputacoes", None) or []:
        eventos = getattr(imp, "eventos", None) if not isinstance(imp, Mapping) else imp.get("eventos")
        for ev in eventos or []:
            fid = getattr(ev, "fonte_id", None) if not isinstance(ev, Mapping) else ev.get("fonte_id")
            tr = getattr(ev, "trecho", None) if not isinstance(ev, Mapping) else ev.get("trecho")
            if fid:
                fid_s = str(fid).strip()
                vinculados.add(fid_s)
                if tr and str(tr).strip():
                    citacoes_por_id.setdefault(fid_s, []).append(str(tr).strip())

    trechos_pecas: List[Dict[str, str]] = []
    total_chars = 0
    for peca in sorted(ctx.pecas, key=lambda p: p.doc_id not in ids_citados):
        if peca.doc_id not in vinculados:
            continue
        if total_chars >= max_total_chars:
            break
        restante = min(max_chars_por_peca, max_total_chars - total_chars)
        trecho, modo_recorte = _recortar_janela_trecho(
            peca.conteudo or "", citacoes_por_id.get(peca.doc_id, ()), restante
        )
        total_chars += len(trecho)
        trechos_pecas.append(
            {
                "doc_id": peca.doc_id,
                "titulo": peca.titulo,
                "folha": peca.folha,
                "trecho": trecho,
                "recorte": modo_recorte,
            }
        )

    trechos_orais: List[Dict[str, str]] = []
    for dep in getattr(ctx.matriz_oral, "depoentes", None) or []:
        if dep.media_id not in vinculados and vinculados:
            continue
        if total_chars >= max_total_chars:
            break
        restante = min(max_chars_por_peca, max_total_chars - total_chars)
        conteudo = dep.discurso_indireto_sentenca or ""
        if oral_literal:
            conteudo = "\n".join(
                s.get("texto_literal") or s.get("texto") or "" for s in dep.segmentos_literais
            )
        trecho, modo_recorte = _recortar_janela_trecho(conteudo, citacoes_por_id.get(dep.media_id, ()), restante)
        total_chars += len(trecho)
        trechos_orais.append(
            {
                "media_id": dep.media_id,
                "depoente": dep.depoente,
                "papel": dep.papel,
                "trecho": trecho,
                "recorte": modo_recorte,
            }
        )

    return {
        "fingerprint": ctx.fingerprint,
        "ids_vinculados": sorted(vinculados),
        "trechos_pecas": trechos_pecas,
        "trechos_orais": trechos_orais,
        "total_chars": total_chars,
    }


def obter_contexto_geracao(
    bundle: dict,
    *,
    decisoes: Any = None,
    ids_vinculados: Optional[Sequence[str]] = None,
    trechos_citados: Optional[Mapping[str, Sequence[str]]] = None,
    max_chars_por_peca: int = 1200,
    max_total_chars: int = 8000,
    contexto: CaseContext | None = None,
    exigir_papel_confirmado: bool = False,
) -> Dict[str, Any]:
    """Reaproveita o contexto de geração e a vista de prova oral por caso e decisões."""
    ctx = contexto or obter_contexto(bundle)
    filtrar_oral = exigir_papel_confirmado and not ctx.is_demo
    assinatura_decisoes = (
        hashlib.sha256(
            json.dumps(decisoes, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=_json_default).encode(
                "utf-8"
            )
        ).hexdigest()
        if decisoes is not None
        else ""
    )
    citacoes_chave = tuple(
        (str(k).strip(), tuple(str(x).strip() for x in v if str(x).strip()))
        for k, v in sorted((trechos_citados or {}).items())
    )
    chave = (
        ctx.fingerprint,
        assinatura_decisoes,
        tuple(sorted({str(x).strip() for x in (ids_vinculados or []) if str(x).strip()})),
        citacoes_chave,
        int(max_chars_por_peca),
        int(max_total_chars),
        filtrar_oral,
    )
    with _CACHE_LOCK:
        existente = _CACHE_GERACAO.get(chave)
        if existente is not None:
            _CACHE_GERACAO.move_to_end(chave)
            return existente
    prova_oral = None
    if filtrar_oral:
        from app.generation.prova_oral_confirmada import separar_prova_oral
        from app.transcription.oral_proof_analyzer import gerar_matriz_corroboracao_oral

        prova_oral = separar_prova_oral(ctx.transcriptions)
        ctx = replace(
            ctx, transcriptions=prova_oral.transcricoes,
            matriz_oral=gerar_matriz_corroboracao_oral(ctx.pecas, prova_oral.transcricoes),
        )
    novo = recorte_trechos_vinculados(
        ctx,
        decisoes=decisoes,
        ids_vinculados=ids_vinculados,
        trechos_citados=trechos_citados,
        max_chars_por_peca=max_chars_por_peca,
        max_total_chars=max_total_chars,
        oral_literal=filtrar_oral,
    )
    novo["assinatura_decisoes"] = assinatura_decisoes
    if filtrar_oral:
        novo["contexto"] = ctx
        novo["prova_oral"] = prova_oral
    with _CACHE_LOCK:
        existente = _CACHE_GERACAO.get(chave)
        if existente is not None:
            _CACHE_GERACAO.move_to_end(chave)
            return existente
        _CACHE_GERACAO[chave] = novo
        while len(_CACHE_GERACAO) > _CACHE_MAX * 2:
            _CACHE_GERACAO.popitem(last=False)
        return novo


def limpar_cache_contexto() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE_GERACAO.clear()
