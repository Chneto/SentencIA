"""Rotas de persistência do rascunho de decisões por caso (TASK-068)."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.jobs import INGESTAO_LOCK
from app.core.session_state import (
    ACTIVE_CASE_STATE,
    STATE_LOCK,
    is_demo_or_test_bundle,
    salvar_ultimo_caso_real,
)

router = APIRouter()


class RascunhoDecisoesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_key: str = Field(..., min_length=1)
    crime_configs: List[Dict[str, Any]] = Field(default_factory=list)
    extras: Dict[str, Any] = Field(default_factory=dict)
    manifesto_id: Optional[str] = None


@router.get("/api/cases/rascunho-decisoes")
def api_obter_rascunho_decisoes() -> Dict[str, Any]:
    """Devolve o rascunho de decisões persistido no caso ativo (ou ``None``)."""
    with STATE_LOCK:
        bundle = ACTIVE_CASE_STATE.get("bundle") or {}
        rascunho = copy.deepcopy(bundle.get("rascunho_decisoes"))
    return {"rascunho": rascunho}


@router.post("/api/cases/rascunho-decisoes")
def api_salvar_rascunho_decisoes(req: RascunhoDecisoesRequest) -> Dict[str, Any]:
    """Persiste o rascunho das decisões (incluindo registro probatório) vinculado ao caso ativo."""
    if INGESTAO_LOCK.locked():
        raise HTTPException(
            status_code=409,
            detail="Há um carregamento de autos em andamento. Aguarde a conclusão para salvar o rascunho.",
        )
    with STATE_LOCK:
        if INGESTAO_LOCK.locked():
            raise HTTPException(
                status_code=409,
                detail="Há um carregamento de autos em andamento. Aguarde a conclusão para salvar o rascunho.",
            )
        bundle = ACTIVE_CASE_STATE.get("bundle")
        explicit_demo = bool(ACTIVE_CASE_STATE.get("explicit_demo"))
        upload_status = dict(ACTIVE_CASE_STATE.get("upload_status") or {})
        if not isinstance(bundle, dict):
            raise HTTPException(status_code=404, detail="Nenhum caso ativo carregado.")
        manifesto_atual = (bundle.get("manifesto_caso") or {}).get("id")
        if req.manifesto_id is not None and req.manifesto_id != manifesto_atual:
            raise HTTPException(
                status_code=409,
                detail="O caso ativo mudou. Recarregue a página antes de salvar as decisões.",
            )
        rascunho = {
            "case_key": req.case_key,
            "manifesto_id": manifesto_atual,
            "crime_configs": req.crime_configs,
            "extras": req.extras,
            "atualizado_em": datetime.now(timezone.utc).isoformat(),
        }
        bundle["rascunho_decisoes"] = rascunho
        persistir = not explicit_demo and not is_demo_or_test_bundle(bundle)
        copia_para_disco = copy.deepcopy(bundle) if persistir else None
    if copia_para_disco is not None:
        salvar_ultimo_caso_real(copia_para_disco, upload_status)
    return {"status": "ok", "rascunho": rascunho}


@router.delete("/api/cases/rascunho-decisoes")
def api_limpar_rascunho_decisoes(manifesto_id: Optional[str] = None) -> Dict[str, Any]:
    """Remove o rascunho de decisões persistido do caso ativo."""
    if INGESTAO_LOCK.locked():
        raise HTTPException(
            status_code=409,
            detail="Há um carregamento de autos em andamento. Aguarde a conclusão para limpar o rascunho.",
        )
    with STATE_LOCK:
        if INGESTAO_LOCK.locked():
            raise HTTPException(
                status_code=409,
                detail="Há um carregamento de autos em andamento. Aguarde a conclusão para limpar o rascunho.",
            )
        bundle = ACTIVE_CASE_STATE.get("bundle")
        explicit_demo = bool(ACTIVE_CASE_STATE.get("explicit_demo"))
        upload_status = dict(ACTIVE_CASE_STATE.get("upload_status") or {})
        if not isinstance(bundle, dict):
            return {"status": "ok", "rascunho": None}
        manifesto_atual = (bundle.get("manifesto_caso") or {}).get("id")
        if manifesto_id is not None and manifesto_id != manifesto_atual:
            raise HTTPException(
                status_code=409,
                detail="O caso ativo mudou. Recarregue a página antes de limpar as decisões.",
            )
        bundle.pop("rascunho_decisoes", None)
        persistir = not explicit_demo and not is_demo_or_test_bundle(bundle)
        copia_para_disco = copy.deepcopy(bundle) if persistir else None
    if copia_para_disco is not None:
        salvar_ultimo_caso_real(copia_para_disco, upload_status)
    return {"status": "ok", "rascunho": None}
