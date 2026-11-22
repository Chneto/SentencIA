"""Utilitários multiplataforma (Windows + macOS + Linux).

Nada aqui levanta exceção por ausência de binário/GPU: as funções devolvem ``None`` ou um status
explícito de indisponibilidade — nunca números fictícios.
"""

from __future__ import annotations

import functools
import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "IS_WINDOWS",
    "IS_MACOS",
    "IS_LINUX",
    "subprocess_no_window_kwargs",
    "find_binary",
    "find_chrome",
    "get_gpu_status",
    "limpar_cache_binarios",
]

IS_WINDOWS: bool = sys.platform == "win32"
IS_MACOS: bool = sys.platform == "darwin"
IS_LINUX: bool = sys.platform.startswith("linux")

_CREATE_NO_WINDOW = 0x08000000


def subprocess_no_window_kwargs() -> Dict[str, Any]:
    """kwargs para ``subprocess`` que evitam abrir janela de console no Windows (vazio nos demais)."""
    if IS_WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", _CREATE_NO_WINDOW)}
    return {}


# ----------------------------------------------------------------------------------------------
# Localização de binários
# ----------------------------------------------------------------------------------------------
def _eh_executavel(caminho: Path) -> bool:
    try:
        if not caminho.is_file():
            return False
        return IS_WINDOWS or os.access(str(caminho), os.X_OK)
    except OSError:
        return False


def _nomes_candidatos(name: str) -> List[str]:
    if IS_WINDOWS and not name.lower().endswith(".exe"):
        return [f"{name}.exe", name]
    return [name]


def _diretorios_conhecidos() -> List[Path]:
    dirs: List[Path] = []
    home = Path.home()
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA", "")
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        if local:
            dirs += [
                Path(local) / "Microsoft" / "WinGet" / "Links",
                Path(local) / "Programs" / "Ollama",
            ]
        dirs += [
            Path(pf) / "NVIDIA Corporation" / "NVSMI",
            Path(pf) / "ffmpeg" / "bin",
            Path(pf86) / "ffmpeg" / "bin",
            Path(pf) / "Ollama",
            home / "scoop" / "shims",
            Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "chocolatey" / "bin",
        ]
    else:
        dirs += [
            Path("/opt/homebrew/bin"),
            Path("/usr/local/bin"),
            Path("/usr/bin"),
            Path("/opt/local/bin"),
            home / ".local" / "bin",
        ]
        if IS_MACOS:
            dirs.append(Path("/Applications/Ollama.app/Contents/Resources"))
        else:
            dirs.append(Path("/snap/bin"))
    return dirs


@functools.lru_cache(maxsize=64)
def _buscar_winget(nome_arquivo: str) -> Optional[str]:
    """Busca (memorizada) em ``%LOCALAPPDATA%\\Microsoft\\WinGet\\Packages`` — a varredura é lenta."""
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return None
    raiz = Path(local) / "Microsoft" / "WinGet" / "Packages"
    try:
        if not raiz.is_dir():
            return None
        for achado in raiz.rglob(nome_arquivo):
            if _eh_executavel(achado):
                return str(achado)
    except OSError:
        return None
    return None


def _ffmpeg_via_imageio() -> Optional[str]:
    try:
        import imageio_ffmpeg  # type: ignore[import-not-found]

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        return exe if exe and Path(exe).is_file() else None
    except Exception:
        return None


def find_binary(name: str) -> Optional[str]:
    """Caminho absoluto de um executável, ou ``None`` se não encontrado.

    Ordem: ``shutil.which`` → diretórios conhecidos (WinGet Links/Packages, Homebrew,
    ``/usr/local/bin``...) → ``imageio_ffmpeg`` (apenas para ``ffmpeg``, se instalado).
    """
    try:
        if not name or not isinstance(name, str):
            return None
        name = name.strip()
        if not name:
            return None
        achado = shutil.which(name)
        if achado:
            return achado
        candidatos = _nomes_candidatos(name)
        for d in _diretorios_conhecidos():
            for nome_arq in candidatos:
                caminho = d / nome_arq
                if _eh_executavel(caminho):
                    return str(caminho)
        if IS_WINDOWS:
            achado = _buscar_winget(candidatos[0])
            if achado:
                return achado
        if Path(name).stem.lower() == "ffmpeg":
            return _ffmpeg_via_imageio()
    except Exception as err:  # nunca propagar
        logger.debug("find_binary(%r) falhou: %s", name, err)
    return None


def limpar_cache_binarios() -> None:
    """Esquece buscas memorizadas (ex.: após instalar um programa com o sistema aberto)."""
    _buscar_winget.cache_clear()


def _candidatos_chrome() -> Iterable[Path]:
    if IS_WINDOWS:
        bases = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        rel = [
            Path("Google") / "Chrome" / "Application" / "chrome.exe",
            Path("Microsoft") / "Edge" / "Application" / "msedge.exe",
            Path("BraveSoftware") / "Brave-Browser" / "Application" / "brave.exe",
        ]
        for r in rel:
            for b in bases:
                if b:
                    yield Path(b) / r
    elif IS_MACOS:
        for app_dir in (Path("/Applications"), Path.home() / "Applications"):
            yield app_dir / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
            yield app_dir / "Microsoft Edge.app" / "Contents" / "MacOS" / "Microsoft Edge"
            yield app_dir / "Brave Browser.app" / "Contents" / "MacOS" / "Brave Browser"
            yield app_dir / "Chromium.app" / "Contents" / "MacOS" / "Chromium"


def find_chrome() -> Optional[str]:
    """Navegador baseado em Chromium (Chrome, Edge, Brave, Chromium) para modo app; ``None`` se ausente."""
    try:
        for caminho in _candidatos_chrome():
            if _eh_executavel(caminho):
                return str(caminho)
        nomes = (
            ["chrome", "msedge"]
            if IS_WINDOWS
            else [
                "google-chrome",
                "google-chrome-stable",
                "chromium",
                "chromium-browser",
                "microsoft-edge",
                "brave-browser",
            ]
        )
        for nome in nomes:
            achado = shutil.which(nome)
            if achado:
                return achado
    except Exception as err:
        logger.debug("find_chrome falhou: %s", err)
    return None


# ----------------------------------------------------------------------------------------------
# GPU
# ----------------------------------------------------------------------------------------------
def _status_base(backend: str, detalhe: str) -> Dict[str, Any]:
    return {
        "gpu_available": False,
        "backend": backend,
        "gpu_name": None,
        "vram_used_mb": None,
        "vram_total_mb": None,
        "vram_free_mb": None,
        "vram_percent": None,
        "gpu_utilization_percent": None,
        "fonte": None,
        "detalhe": detalhe,
    }


def _int_ou_none(valor: str) -> Optional[int]:
    try:
        return int(float(valor.strip()))
    except (ValueError, AttributeError):
        return None


def _consultar_nvidia_smi(exe: str) -> Optional[Dict[str, Any]]:
    try:
        proc = subprocess.run(
            [
                exe,
                "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=4,
            **subprocess_no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as err:
        logger.debug("nvidia-smi falhou: %s", err)
        return None
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None
    parts = [p.strip() for p in proc.stdout.strip().splitlines()[0].split(",")]
    if len(parts) < 4:
        return None
    used, total, util = _int_ou_none(parts[1]), _int_ou_none(parts[2]), _int_ou_none(parts[3])
    status = _status_base("cuda", "Medido via nvidia-smi.")
    status.update(
        {
            "gpu_available": True,
            "gpu_name": parts[0] or None,
            "vram_used_mb": used,
            "vram_total_mb": total,
            "vram_free_mb": max(0, total - used) if used is not None and total is not None else None,
            "vram_percent": round((used / total) * 100, 1) if used is not None and total else None,
            "gpu_utilization_percent": util,
            "fonte": "nvidia-smi",
        }
    )
    return status


def _nome_chip_macos() -> Optional[str]:
    try:
        proc = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=2
        )
        nome = (proc.stdout or "").strip()
        return nome or None
    except (OSError, subprocess.SubprocessError):
        return None


def get_gpu_status() -> Dict[str, Any]:
    """Status real da GPU. Sem medição disponível → ``gpu_available: False`` e números ``None``.

    Chaves sempre presentes: ``gpu_available``, ``backend`` (``cuda``/``apple_silicon``/``cpu``),
    ``gpu_name``, ``vram_used_mb``, ``vram_total_mb``, ``vram_free_mb``, ``vram_percent``,
    ``gpu_utilization_percent``, ``fonte``, ``detalhe``.
    """
    try:
        exe = None if IS_MACOS else find_binary("nvidia-smi")
        if exe:
            medido = _consultar_nvidia_smi(exe)
            if medido is not None:
                return medido
            return _status_base("cpu", "nvidia-smi presente, mas não retornou dados; GPU indisponível.")
        if IS_MACOS:
            if platform.machine().lower() in {"arm64", "aarch64"}:
                status = _status_base(
                    "apple_silicon",
                    "Apple Silicon (memória unificada); VRAM dedicada não se aplica e não é medida.",
                )
                status["gpu_name"] = _nome_chip_macos()
                status["fonte"] = "sysctl" if status["gpu_name"] else None
                return status
            return _status_base("cpu", "Mac Intel: aceleração por GPU indisponível; processamento em CPU.")
        return _status_base("cpu", "Nenhuma GPU NVIDIA detectada (nvidia-smi ausente); processamento em CPU.")
    except Exception as err:  # nunca propagar
        logger.debug("get_gpu_status falhou: %s", err)
        return _status_base("cpu", "Status da GPU indisponível.")
