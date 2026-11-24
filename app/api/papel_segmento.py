"""Rota para confirmação humana do papel processual por segmento de áudio (TASK-065)."""

from __future__ import annotations

import copy
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.core.session_state import ACTIVE_CASE_STATE, STATE_LOCK, set_active_bundle
from app.transcription.oral_proof_analyzer import (
    _build_real_case_oral_analysis,
    confirmar_papel_segmento,
)

router = APIRouter()


class ConfirmarPapelSegmentoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_id: str = Field(..., min_length=1)
    indice: int = Field(..., ge=0)
    papel: str = Field(..., min_length=1)
    confirmar: bool = True


@router.post("/api/cases/transcricao/papel-segmento")
def api_confirmar_papel_segmento(req: ConfirmarPapelSegmentoRequest) -> Dict[str, Any]:
    """Confirma ou ajusta o papel processual de um segmento de transcrição no caso ativo."""
    with STATE_LOCK:
        bundle = copy.deepcopy(ACTIVE_CASE_STATE.get("bundle") or {})
        explicit_demo = bool(ACTIVE_CASE_STATE.get("explicit_demo"))
    if not bundle:
        raise HTTPException(status_code=404, detail="Nenhum caso ativo carregado.")

    transcriptions = list(bundle.get("transcriptions") or [])
    alvo_tr = next(
        (tr for tr in transcriptions if isinstance(tr, dict) and str(tr.get("media_id") or "") == req.media_id),
        None,
    )
    if alvo_tr is None:
        raise HTTPException(status_code=404, detail=f"Mídia '{req.media_id}' não encontrada no caso ativo.")

    try:
        seg_atualizado = confirmar_papel_segmento(alvo_tr, req.indice, req.papel, confirmar=req.confirmar)
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Atualiza o resumo correspondente em matriz_oral sem perder os demais depoentes.
    matriz = dict(bundle.get("matriz_oral") or {})
    depoentes = list(matriz.get("depoentes") or [])
    dep_atualizado = _build_real_case_oral_analysis(alvo_tr, []).model_dump()
    substituido = False
    for i, dep in enumerate(depoentes):
        if isinstance(dep, dict) and str(dep.get("media_id") or "") == req.media_id:
            dep_novo = dict(dep)
            dep_novo["segmentos_literais"] = dep_atualizado["segmentos_literais"]
            dep_novo["segmentos_pendentes_confirmacao"] = dep_atualizado["segmentos_pendentes_confirmacao"]
            dep_novo["papel_confirmado"] = dep_atualizado["papel_confirmado"]
            if req.confirmar and dep_novo.get("papel") == "INDETERMINADO":
                dep_novo["papel"] = seg_atualizado["papel_segmento"]
                dep_novo["discurso_indireto_sentenca"] = dep_atualizado["discurso_indireto_sentenca"]
            depoentes[i] = dep_novo
            substituido = True
            break
    if not substituido and depoentes:
        depoentes.append(dep_atualizado)
    if depoentes:
        matriz["depoentes"] = depoentes
        matriz["segmentos_pendentes_confirmacao"] = sum(
            int(d.get("segmentos_pendentes_confirmacao") or 0) for d in depoentes if isinstance(d, dict)
        )
        bundle["matriz_oral"] = matriz
    bundle["transcriptions"] = transcriptions

    set_active_bundle(bundle, explicit_demo=explicit_demo, limpar_producao=False)
    return {
        "status": "ok",
        "media_id": req.media_id,
        "indice": req.indice,
        "segmento": seg_atualizado,
        "matriz_oral": bundle.get("matriz_oral"),
    }
