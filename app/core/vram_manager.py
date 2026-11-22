"""Política de VRAM e telemetria de hardware para Whisper + Ollama (Fase 4 / TASK-014).

Princípios:
- Honestidade operacional: sem medição real (sem ``nvidia-smi``, Apple Silicon, CPU),
  usa padrões conservadores e registra ``vram_medida: False`` — nunca inventa números.
- Margem de segurança de 15% sobre os requisitos mínimos de VRAM por modelo Whisper.
- Sequência segura para GPU: descarregar Ollama → medir VRAM novamente → decidir.
- Sugestão de quantização Q4_K_M apenas em texto quando VRAM total <= 6 GB (nunca troca
  nem baixa modelos automaticamente).
"""

from __future__ import annotations

import contextlib
import gc
import logging
import math
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

from app.core.config import load_settings
from app.core.platform_utils import IS_MACOS, get_gpu_status

logger = logging.getLogger(__name__)

MARGEM_SEGURANCA_VRAM = 1.15

# Requisitos mínimos estimados (MiB) antes da margem de segurança de 15%.
WHISPER_VRAM_MIN_MB: Dict[str, Dict[str, int]] = {
    "tiny": {"float16": 600, "int8_float16": 400},
    "base": {"float16": 900, "int8_float16": 600},
    "small": {"float16": 1500, "int8_float16": 1000},
    "medium": {"float16": 3000, "int8_float16": 2100},
    "turbo": {"float16": 3500, "int8_float16": 2400},
    "large": {"float16": 4500, "int8_float16": 3200},
}

_OLLAMA_VRAM_ESTIMADA_MB: Dict[str, int] = {
    "1b": 1400,
    "2b": 2200,
    "e2b": 2400,
    "3b": 2600,
    "4b": 3400,
    "e4b": 3600,
    "7b": 4800,
    "8b": 5200,
    "9b": 5800,
    "12b": 7800,
    "14b": 9000,
}

_ULTIMA_DECISAO_OLLAMA: Optional[Dict[str, Any]] = None
_CACHE_GPU_TTL_SEG = 10.0
_CACHE_GPU_LOCK = threading.Lock()
_CACHE_GPU_TS: float = 0.0
_CACHE_GPU_VAL: Optional[Dict[str, Any]] = None

#: Lock global de GPU para o Modo GPU moderado (serializa Whisper, Ollama e lote; sempre interno ao INGESTAO_LOCK).
_GPU_LOCK = threading.Lock()
_GPU_FILA_LOCK = threading.Lock()
_GPU_TAREFA_ATIVA: Optional[str] = None
_GPU_AGUARDANDO: int = 0

MODO_GPU_MODERADO_CTX_MAX = 4096
MODO_GPU_MODERADO_VRAM_FATOR = 0.70
MODO_GPU_MODERADO_KEEP_ALIVE = "2m"
MODO_GPU_MODERADO_WHISPER_BEAM = 2


def _normalizar_modo_gpu(modo_gpu: Optional[str] = None) -> str:
    if modo_gpu is None:
        try:
            modo_gpu = getattr(load_settings(), "modo_gpu", "normal")
        except Exception:
            modo_gpu = "normal"
    return "moderado" if str(modo_gpu or "").strip().lower() == "moderado" else "normal"


def obter_estado_fila_gpu() -> Dict[str, Any]:
    """Snapshot do uso e fila da GPU no Modo GPU moderado."""
    with _GPU_FILA_LOCK:
        ativa = _GPU_TAREFA_ATIVA
        aguardando = _GPU_AGUARDANDO
    return {
        "ativa": ativa,
        "aguardando": aguardando,
        "mensagem": "aguardando a placa de vídeo" if aguardando > 0 else None,
    }


@contextlib.contextmanager
def reservar_gpu(tarefa: str, *, modo_gpu: Optional[str] = None) -> Iterator[None]:
    """Serializa tarefas de GPU quando ``modo_gpu == 'moderado'``; no-op no modo ``'normal'``."""
    global _GPU_TAREFA_ATIVA, _GPU_AGUARDANDO
    if _normalizar_modo_gpu(modo_gpu) != "moderado":
        yield
        return

    adquirido_ja = _GPU_LOCK.acquire(blocking=False)
    if not adquirido_ja:
        with _GPU_FILA_LOCK:
            _GPU_AGUARDANDO += 1
        try:
            _GPU_LOCK.acquire()
        finally:
            with _GPU_FILA_LOCK:
                _GPU_AGUARDANDO = max(0, _GPU_AGUARDANDO - 1)
    with _GPU_FILA_LOCK:
        _GPU_TAREFA_ATIVA = tarefa
    try:
        yield
    finally:
        with _GPU_FILA_LOCK:
            _GPU_TAREFA_ATIVA = None
        _GPU_LOCK.release()


def limpar_cache_vram() -> None:
    """Invalida o cache de 10 s da medição de GPU (útil após descarregar Ollama ou em testes)."""
    global _CACHE_GPU_TS, _CACHE_GPU_VAL
    with _CACHE_GPU_LOCK:
        _CACHE_GPU_TS = 0.0
        _CACHE_GPU_VAL = None


def _familia_modelo_whisper(modelo: str) -> str:
    nome = (Path(str(modelo or "small")).name or str(modelo or "small")).strip().lower()
    if "turbo" in nome:
        return "turbo"
    for fam in ("large", "medium", "small", "base", "tiny"):
        if fam in nome:
            return fam
    return "small"


def requisitos_whisper_mb(modelo: str) -> Tuple[str, int, int]:
    """Retorna ``(familia, req_float16_mb, req_int8_float16_mb)`` já com margem de 15%."""
    fam = _familia_modelo_whisper(modelo)
    base = WHISPER_VRAM_MIN_MB[fam]
    req_fp16 = int(math.ceil(base["float16"] * MARGEM_SEGURANCA_VRAM))
    req_int8 = int(math.ceil(base["int8_float16"] * MARGEM_SEGURANCA_VRAM))
    return fam, req_fp16, req_int8


def decidir_dispositivo_whisper(vram_livre_mb: Optional[int], modelo: str) -> Tuple[str, str]:
    """Decide ``("cuda", "float16" | "int8_float16")`` ou ``("cpu", "int8")`` pela VRAM livre.

    Aplica margem de segurança de 15% sobre a tabela ``WHISPER_VRAM_MIN_MB``.
    Quando ``vram_livre_mb`` é ``None`` ou ``<= 0``, retorna ``("cpu", "int8")``.
    """
    if vram_livre_mb is None or vram_livre_mb <= 0:
        return "cpu", "int8"
    _, req_fp16, req_int8 = requisitos_whisper_mb(modelo)
    if vram_livre_mb >= req_fp16:
        return "cuda", "float16"
    if vram_livre_mb >= req_int8:
        return "cuda", "int8_float16"
    return "cpu", "int8"


def avaliar_whisper_vram(
    preferencia: str,
    modelo: str,
    gpu_status: Optional[Dict[str, Any]] = None,
    *,
    modo_gpu: Optional[str] = None,
) -> Dict[str, Any]:
    """Avalia a decisão completa do Whisper respeitando ``preferencia`` (auto/cuda/cpu) e ``modo_gpu``."""
    pref = (preferencia or "auto").strip().lower()
    modo = _normalizar_modo_gpu(modo_gpu)
    beam_size = MODO_GPU_MODERADO_WHISPER_BEAM if modo == "moderado" else None
    fam, req_fp16, req_int8 = requisitos_whisper_mb(modelo)

    if IS_MACOS or pref == "cpu":
        motivo = (
            "macOS utiliza CPU int8 para faster-whisper."
            if IS_MACOS
            else "Configurado explicitamente para CPU (whisper_device='cpu')."
        )
        return {
            "device": "cpu",
            "compute_type": "int8",
            "beam_size": beam_size,
            "modo_gpu": modo,
            "modelo": modelo,
            "modelo_familia": fam,
            "preferencia": pref,
            "vram_medida": False,
            "vram_livre_mb": None,
            "requisito_float16_mb": req_fp16,
            "requisito_int8_mb": req_int8,
            "motivo": motivo,
            "alerta": None,
        }

    hw = gpu_status if gpu_status is not None else get_gpu_vram_status()
    gpu_ok = bool(hw.get("gpu_available"))
    vram_livre = hw.get("vram_free_mb")
    vram_medida = gpu_ok and isinstance(vram_livre, (int, float))
    vram_livre_int = int(vram_livre) if vram_medida else None

    if pref == "cuda":
        alerta: Optional[str] = None
        if not gpu_ok:
            compute_type = "int8_float16"
            alerta = (
                f"whisper_device='cuda' forçado, mas nenhuma GPU CUDA foi detectada "
                f"({hw.get('detalhe') or 'medição indisponível'})."
            )
            motivo = "CUDA forçado pelo usuário sem GPU detectada (alerta emitido)."
        elif not vram_medida:
            compute_type = "int8_float16"
            alerta = "whisper_device='cuda' forçado sem medição de VRAM disponível; usando int8_float16."
            motivo = "CUDA forçado pelo usuário com medição de VRAM indisponível."
        elif vram_livre_int is not None and vram_livre_int < req_int8:
            compute_type = "int8_float16"
            alerta = (
                f"whisper_device='cuda' forçado com VRAM livre insuficiente para '{fam}': "
                f"{vram_livre_int} MiB livres < {req_int8} MiB necessários (com margem de 15%)."
            )
            motivo = f"CUDA forçado pelo usuário abaixo do mínimo recomendado ({vram_livre_int} MiB < {req_int8} MiB)."
        else:
            _, compute_type = decidir_dispositivo_whisper(vram_livre_int, modelo)
            if modo == "moderado":
                compute_type = "int8_float16"
            motivo = f"CUDA explícito com {vram_livre_int} MiB livres ({compute_type})."

        if alerta:
            logger.warning("Política VRAM Whisper: %s", alerta)
        return {
            "device": "cuda",
            "compute_type": compute_type,
            "beam_size": beam_size,
            "modo_gpu": modo,
            "modelo": modelo,
            "modelo_familia": fam,
            "preferencia": pref,
            "vram_medida": vram_medida,
            "vram_livre_mb": vram_livre_int,
            "requisito_float16_mb": req_fp16,
            "requisito_int8_mb": req_int8,
            "motivo": motivo,
            "alerta": alerta,
        }

    # Modo "auto"
    if not gpu_ok or not vram_medida:
        motivo = (
            "Medição de VRAM indisponível; padrão conservador em CPU (int8)."
            if not vram_medida
            else "Nenhuma GPU CUDA disponível; usando CPU (int8)."
        )
        return {
            "device": "cpu",
            "compute_type": "int8",
            "beam_size": beam_size,
            "modo_gpu": modo,
            "modelo": modelo,
            "modelo_familia": fam,
            "preferencia": pref,
            "vram_medida": False,
            "vram_livre_mb": None,
            "requisito_float16_mb": req_fp16,
            "requisito_int8_mb": req_int8,
            "motivo": motivo,
            "alerta": None,
        }

    device, compute_type = decidir_dispositivo_whisper(vram_livre_int, modelo)
    if device == "cuda" and modo == "moderado":
        compute_type = "int8_float16"
    if device == "cuda":
        motivo = (
            f"VRAM livre medida ({vram_livre_int} MiB) atende '{fam}' em {compute_type} "
            f"(mínimo com 15%: {req_fp16 if compute_type == 'float16' else req_int8} MiB)."
        )
    else:
        motivo = (
            f"VRAM livre medida ({vram_livre_int} MiB) insuficiente para '{fam}' "
            f"(mínimo com 15%: {req_int8} MiB); direcionado para CPU (int8)."
        )
    return {
        "device": device,
        "compute_type": compute_type,
        "beam_size": beam_size,
        "modo_gpu": modo,
        "modelo": modelo,
        "modelo_familia": fam,
        "preferencia": pref,
        "vram_medida": True,
        "vram_livre_mb": vram_livre_int,
        "requisito_float16_mb": req_fp16,
        "requisito_int8_mb": req_int8,
        "motivo": motivo,
        "alerta": None,
    }


def _estimar_vram_ollama_mb(modelo: str) -> int:
    nome = (modelo or "").strip().lower()
    m = re.search(r"(e?\d+)b\b", nome)
    if m and m.group(0) in _OLLAMA_VRAM_ESTIMADA_MB:
        return _OLLAMA_VRAM_ESTIMADA_MB[m.group(0)]
    return 4000


def sugerir_quantizacao_ollama(vram_total_mb: Optional[int], modelo: str) -> Optional[str]:
    """Sugere variante Q4_K_M (somente texto) quando VRAM total <= 6 GB e modelo não é q4/q5."""
    if vram_total_mb is None or vram_total_mb > 6144:
        return None
    nome = (modelo or "").strip()
    if not nome:
        return None
    low = nome.lower()
    if "q4" in low or "q5" in low:
        return None
    return (
        f"GPU com {int(vram_total_mb)} MiB (<= 6 GB) e modelo '{nome}' sem quantização Q4/Q5 no nome: "
        f"considere selecionar manualmente uma variante Q4_K_M nas Configurações para reduzir o consumo de VRAM."
    )


def calcular_opcoes_ollama(
    gpu_status: Optional[Dict[str, Any]] = None,
    modelo: Optional[str] = None,
    num_ctx_padrao: Optional[int] = None,
    *,
    modo_gpu: Optional[str] = None,
) -> Dict[str, Any]:
    """Calcula ``num_gpu``, ``num_ctx`` e ``keep_alive`` para o Ollama com base na VRAM e ``modo_gpu``."""
    if modelo is None or num_ctx_padrao is None or modo_gpu is None:
        settings = load_settings()
        modelo = modelo if modelo is not None else settings.ollama_model
        num_ctx_padrao = num_ctx_padrao if num_ctx_padrao is not None else settings.ollama_num_ctx
        modo_gpu = modo_gpu if modo_gpu is not None else getattr(settings, "modo_gpu", "normal")
    modo = _normalizar_modo_gpu(modo_gpu)
    ctx_base = max(512, int(num_ctx_padrao or 4096))
    keep_alive: Optional[str] = MODO_GPU_MODERADO_KEEP_ALIVE if modo == "moderado" else None
    if modo == "moderado":
        ctx_base = min(ctx_base, MODO_GPU_MODERADO_CTX_MAX)

    hw = gpu_status if gpu_status is not None else get_gpu_vram_status()
    gpu_ok = bool(hw.get("gpu_available"))
    backend = str(hw.get("backend") or "cpu")
    vram_livre = hw.get("vram_free_mb")
    vram_total = hw.get("vram_total_mb")
    vram_medida = gpu_ok and isinstance(vram_livre, (int, float))
    vram_livre_int = int(vram_livre) if vram_medida else None
    vram_total_int = int(vram_total) if isinstance(vram_total, (int, float)) else None

    if not vram_medida:
        if backend == "apple_silicon":
            return {
                "modelo": modelo,
                "modo_gpu": modo,
                "num_gpu": None,
                "num_ctx": min(ctx_base, 4096),
                "keep_alive": keep_alive,
                "vram_medida": False,
                "vram_livre_mb": None,
                "vram_total_mb": None,
                "motivo": "Apple Silicon (memória unificada não medida): alocação automática do Ollama (num_gpu omitido).",
            }
        return {
            "modelo": modelo,
            "modo_gpu": modo,
            "num_gpu": None,
            "num_ctx": min(ctx_base, 4096),
            "keep_alive": keep_alive,
            "vram_medida": False,
            "vram_livre_mb": None,
            "vram_total_mb": vram_total_int,
            "motivo": "Medição de VRAM indisponível: alocação automática do Ollama (num_gpu omitido).",
        }

    necessidade = int(math.ceil(_estimar_vram_ollama_mb(modelo) * MARGEM_SEGURANCA_VRAM))
    assert vram_livre_int is not None

    if modo == "moderado":
        base_total = vram_total_int if (vram_total_int and vram_total_int > 0) else vram_livre_int
        orcamento_mb = int(base_total * MODO_GPU_MODERADO_VRAM_FATOR)
        if orcamento_mb >= necessidade:
            return {
                "modelo": modelo,
                "modo_gpu": modo,
                "num_gpu": 99,
                "num_ctx": ctx_base,
                "keep_alive": keep_alive,
                "vram_medida": True,
                "vram_livre_mb": vram_livre_int,
                "vram_total_mb": vram_total_int,
                "motivo": (
                    f"Modo GPU moderado (70% de {base_total} MiB = {orcamento_mb} MiB >= {necessidade} MiB): "
                    f"num_ctx={ctx_base}, keep_alive={keep_alive}."
                ),
            }
        if orcamento_mb >= 1000:
            camadas = max(4, min(32, int((orcamento_mb / necessidade) * 32)))
            return {
                "modelo": modelo,
                "modo_gpu": modo,
                "num_gpu": camadas,
                "num_ctx": ctx_base,
                "keep_alive": keep_alive,
                "vram_medida": True,
                "vram_livre_mb": vram_livre_int,
                "vram_total_mb": vram_total_int,
                "motivo": (
                    f"Modo GPU moderado (limite 70% = {orcamento_mb} MiB < {necessidade} MiB): "
                    f"{camadas} camadas na GPU, num_ctx={ctx_base}, keep_alive={keep_alive}."
                ),
            }
        return {
            "modelo": modelo,
            "modo_gpu": modo,
            "num_gpu": 0,
            "num_ctx": min(ctx_base, 2048),
            "keep_alive": keep_alive,
            "vram_medida": True,
            "vram_livre_mb": vram_livre_int,
            "vram_total_mb": vram_total_int,
            "motivo": f"Modo GPU moderado (limite 70% = {orcamento_mb} MiB): execução em CPU (num_gpu=0).",
        }

    if vram_livre_int >= necessidade:
        return {
            "modelo": modelo,
            "modo_gpu": modo,
            "num_gpu": 99,
            "num_ctx": ctx_base,
            "keep_alive": None,
            "vram_medida": True,
            "vram_livre_mb": vram_livre_int,
            "vram_total_mb": vram_total_int,
            "motivo": f"VRAM livre medida ({vram_livre_int} MiB >= {necessidade} MiB): todas as camadas na GPU (num_gpu=99).",
        }
    if vram_livre_int >= 1200:
        camadas = max(8, min(32, int((vram_livre_int / necessidade) * 32)))
        ctx_seguro = min(ctx_base, 4096 if vram_livre_int >= 2500 else 2048)
        return {
            "modelo": modelo,
            "modo_gpu": modo,
            "num_gpu": camadas,
            "num_ctx": ctx_seguro,
            "keep_alive": None,
            "vram_medida": True,
            "vram_livre_mb": vram_livre_int,
            "vram_total_mb": vram_total_int,
            "motivo": (
                f"VRAM livre medida ({vram_livre_int} MiB < {necessidade} MiB): "
                f"offloading parcial ({camadas} camadas na GPU, num_ctx={ctx_seguro})."
            ),
        }
    return {
        "modelo": modelo,
        "modo_gpu": modo,
        "num_gpu": 0,
        "num_ctx": min(ctx_base, 2048),
        "keep_alive": None,
        "vram_medida": True,
        "vram_livre_mb": vram_livre_int,
        "vram_total_mb": vram_total_int,
        "motivo": f"VRAM livre medida ({vram_livre_int} MiB < 1200 MiB): execução em CPU (num_gpu=0, num_ctx={min(ctx_base, 2048)}).",
    }


def registrar_ultima_chamada_ollama(decisao: Dict[str, Any]) -> None:
    global _ULTIMA_DECISAO_OLLAMA
    _ULTIMA_DECISAO_OLLAMA = dict(decisao)


def obter_politica_vram(
    settings: Any = None,
    gpu_status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Snapshot completo da política de VRAM para a telemetria ``/api/status``."""
    cfg = settings if settings is not None else load_settings()
    hw = gpu_status if gpu_status is not None else get_gpu_vram_status()
    modo = _normalizar_modo_gpu(getattr(cfg, "modo_gpu", "normal"))
    whisper_pol = avaliar_whisper_vram(
        str(getattr(cfg, "whisper_device", "auto") or "auto"),
        str(getattr(cfg, "whisper_model", "small") or "small"),
        gpu_status=hw,
        modo_gpu=modo,
    )
    ollama_pol = calcular_opcoes_ollama(
        gpu_status=hw,
        modelo=str(getattr(cfg, "ollama_model", "") or ""),
        num_ctx_padrao=int(getattr(cfg, "ollama_num_ctx", 4096) or 4096),
        modo_gpu=modo,
    )
    vram_total = hw.get("vram_total_mb")
    vram_total_int = int(vram_total) if isinstance(vram_total, (int, float)) else None
    vram_livre = hw.get("vram_free_mb")
    vram_medida = bool(hw.get("gpu_available")) and isinstance(vram_livre, (int, float))
    vram_livre_int = int(vram_livre) if vram_medida else None

    sugestao = sugerir_quantizacao_ollama(vram_total_int, str(getattr(cfg, "ollama_model", "") or ""))
    return {
        "modo_gpu": modo,
        "fila_gpu": obter_estado_fila_gpu(),
        "vram_medida": vram_medida,
        "vram_livre_mb": vram_livre_int,
        "vram_total_mb": vram_total_int,
        "whisper": whisper_pol,
        "ollama": {
            "modelo": ollama_pol["modelo"],
            "num_gpu": ollama_pol["num_gpu"],
            "num_ctx": ollama_pol["num_ctx"],
            "keep_alive": ollama_pol.get("keep_alive"),
            "motivo": ollama_pol["motivo"],
            "ultima_chamada": dict(_ULTIMA_DECISAO_OLLAMA) if _ULTIMA_DECISAO_OLLAMA else None,
        },
        "sugestao_modelo": sugestao,
    }


def get_gpu_vram_status(*, forcar_medicao: bool = False) -> Dict[str, Any]:
    """Status real da GPU local (cacheado por 10 s); ``gpu_available: False`` e valores ``None`` quando não medível."""
    global _CACHE_GPU_TS, _CACHE_GPU_VAL
    agora = time.monotonic()
    with _CACHE_GPU_LOCK:
        if not forcar_medicao and _CACHE_GPU_VAL is not None and (agora - _CACHE_GPU_TS) < _CACHE_GPU_TTL_SEG:
            return dict(_CACHE_GPU_VAL)
    medido = get_gpu_status()
    with _CACHE_GPU_LOCK:
        _CACHE_GPU_VAL = dict(medido) if isinstance(medido, dict) else medido
        _CACHE_GPU_TS = time.monotonic()
    return dict(medido) if isinstance(medido, dict) else medido


def check_ollama_status() -> Dict[str, Any]:
    """Status medido do servidor Ollama local."""
    from app.core.ollama_client import ollama_disponivel

    return ollama_disponivel()


def release_ollama_vram_for_whisper() -> bool:
    """Envia ``keep_alive=0`` ao Ollama para liberar memória antes de carregar o Whisper."""
    from app.core.ollama_client import descarregar_modelo

    try:
        return descarregar_modelo()
    finally:
        limpar_cache_vram()
        gc.collect()
