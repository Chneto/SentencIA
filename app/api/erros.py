"""Mapeamento dos erros de domínio para respostas HTTP.

- ``DecisaoJudicialPendenteError`` → 422 ``{detail, erro: "decisao_judicial_pendente", campos, crimes_pendentes}``
- ``DadosInsuficientesError``     → 422 ``{detail, erro: "dados_insuficientes", campos, crimes_pendentes: []}``
- ``EnvioExternoNaoAutorizadoError`` → 403 ``{detail, erro: "envio_externo_nao_autorizado"}``
- ``GeminiIndisponivelError``     → 503 ``{detail, erro: "gemini_indisponivel", causa}``
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.secrets import SegredoInseguroError
from app.core.errors import DadosInsuficientesError, DecisaoJudicialPendenteError, EnvioExternoNaoAutorizadoError

logger = logging.getLogger(__name__)

try:  # o módulo Gemini é opcional em tempo de import (google-genai é extra)
    from app.generation.gemini_integration import GeminiIndisponivelError
except Exception:  # pragma: no cover
    class GeminiIndisponivelError(RuntimeError):  # type: ignore[no-redef]
        mensagem = ""
        causa = None

        def to_dict(self) -> Dict[str, Any]:
            return {"erro": "gemini_indisponivel", "mensagem": str(self), "causa": None}


def corpo_dados_insuficientes(exc: DadosInsuficientesError) -> Dict[str, Any]:
    base = exc.to_dict()
    return {
        "detail": exc.mensagem,
        "erro": base.get("erro", "dados_insuficientes"),
        "campos": list(getattr(exc, "campos", []) or []),
        "crimes_pendentes": list(getattr(exc, "crimes_pendentes", []) or []),
    }


def descrever_erro(exc: BaseException) -> Tuple[int, Dict[str, Any]]:
    """(status HTTP, corpo JSON) para qualquer exceção (usado nos handlers e nos jobs)."""
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            corpo = dict(detail)
            corpo.setdefault("detail", corpo.get("mensagem") or "Erro")
            return exc.status_code, corpo
        return exc.status_code, {"detail": detail}
    if isinstance(exc, DadosInsuficientesError):  # inclui DecisaoJudicialPendenteError
        return 422, corpo_dados_insuficientes(exc)
    if isinstance(exc, EnvioExternoNaoAutorizadoError):
        return 403, {"detail": exc.mensagem, "erro": "envio_externo_nao_autorizado"}
    if isinstance(exc, SegredoInseguroError):
        return 409, {"detail": str(exc), "erro": "cofre_inseguro"}
    if isinstance(exc, GeminiIndisponivelError):
        corpo = exc.to_dict()
        return 503, {"detail": corpo.get("mensagem") or str(exc), **corpo}
    logger.exception("Erro inesperado na API: %s", type(exc).__name__, exc_info=exc)
    return 500, {
        "detail": f"Falha inesperada no processamento ({type(exc).__name__}). Consulte data/logs/app.log.",
        "erro": "erro_interno",
    }


def registrar_handlers(app: FastAPI) -> None:
    async def _handler_dados(_request: Request, exc: DadosInsuficientesError) -> JSONResponse:
        status, corpo = descrever_erro(exc)
        return JSONResponse(status_code=status, content=corpo)

    async def _handler_envio(_request: Request, exc: EnvioExternoNaoAutorizadoError) -> JSONResponse:
        status, corpo = descrever_erro(exc)
        return JSONResponse(status_code=status, content=corpo)

    async def _handler_gemini(_request: Request, exc: Exception) -> JSONResponse:
        status, corpo = descrever_erro(exc)
        return JSONResponse(status_code=status, content=corpo)

    async def _handler_inesperado(_request: Request, exc: Exception) -> JSONResponse:
        # Resposta JSON legível (o erro completo vai para data/logs/app.log).
        status, corpo = descrever_erro(exc)
        return JSONResponse(status_code=status, content=corpo)

    app.add_exception_handler(DecisaoJudicialPendenteError, _handler_dados)
    app.add_exception_handler(DadosInsuficientesError, _handler_dados)
    app.add_exception_handler(EnvioExternoNaoAutorizadoError, _handler_envio)
    app.add_exception_handler(GeminiIndisponivelError, _handler_gemini)
    app.add_exception_handler(SegredoInseguroError, _handler_gemini)
    app.add_exception_handler(Exception, _handler_inesperado)


def eh_erro_de_dominio(exc: BaseException) -> bool:
    return isinstance(exc, (DadosInsuficientesError, EnvioExternoNaoAutorizadoError, GeminiIndisponivelError, SegredoInseguroError))
