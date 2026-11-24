"""Rota para confirmação humana do papel processual por segmento de áudio (TASK-065)."""

from __future__ import annotations

import copy
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.jobs import INGESTAO_LOCK
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
    manifesto_id: Optional[str] = None


@router.post("/api/cases/transcricao/papel-segmento")
def api_confirmar_papel_segmento(req: ConfirmarPapelSegmentoRequest) -> Dict[str, Any]:
    """Confirma ou ajusta o papel processual de um segmento de transcrição no caso ativo."""
    if INGESTAO_LOCK.locked():
        raise HTTPException(
            status_code=409,
            detail="Há um carregamento de autos em andamento. Aguarde a conclusão para confirmar o papel do segmento.",
        )
    with STATE_LOCK:
        bundle_atual = ACTIVE_CASE_STATE.get("bundle")
        bundle = copy.deepcopy(bundle_atual or {})
        explicit_demo = bool(ACTIVE_CASE_STATE.get("explicit_demo"))
    if not bundle:
        raise HTTPException(status_code=404, detail="Nenhum caso ativo carregado.")

    manifesto_atual = (bundle.get("manifesto_caso") or {}).get("id")
    if req.manifesto_id is not None and req.manifesto_id != manifesto_atual:
        raise HTTPException(
            status_code=409,
            detail="O caso ativo mudou. Recarregue a análise da prova oral antes de confirmar o papel.",
        )

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
    segs_lit = dep_atualizado.get("segmentos_literais") or []
    pendentes = int(dep_atualizado.get("segmentos_pendentes_confirmacao") or 0)
    papeis_confirmados = {
        str(s.get("papel_segmento") or "").strip().upper()
        for s in segs_lit
        if isinstance(s, dict) and s.get("papel_confirmado")
    }
    substituido = False
    for i, dep in enumerate(depoentes):
        if isinstance(dep, dict) and str(dep.get("media_id") or "") == req.media_id:
            dep_novo = dict(dep)
            dep_novo["segmentos_literais"] = segs_lit
            dep_novo["segmentos_pendentes_confirmacao"] = pendentes
            dep_novo["papel_confirmado"] = dep_atualizado["papel_confirmado"]
            # Pendência 065 (TASK-095): só promove o papel do depoente INDETERMINADO quando
            # TODOS os trechos estiverem confirmados com o mesmo papel.
            if (
                req.confirmar
                and dep_novo.get("papel") == "INDETERMINADO"
                and pendentes == 0
                and len(segs_lit) > 0
                and len(papeis_confirmados) == 1
            ):
                papel_unico = next(iter(papeis_confirmados))
                if papel_unico and papel_unico != "INDETERMINADO":
                    dep_novo["papel"] = papel_unico
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

    with STATE_LOCK:
        if ACTIVE_CASE_STATE.get("bundle") is not bundle_atual or INGESTAO_LOCK.locked():
            raise HTTPException(
                status_code=409,
                detail="Os autos mudaram durante a confirmação do papel. Tente novamente.",
            )
        set_active_bundle(bundle, explicit_demo=explicit_demo, limpar_producao=False)
    return {
        "status": "ok",
        "media_id": req.media_id,
        "indice": req.indice,
        "segmento": seg_atualizado,
        "matriz_oral": bundle.get("matriz_oral"),
    }
