"""Diagnóstico e ações do Assistente de Primeira Execução (TASK-033).

- Detecta Ollama (instalado/online), sugere o modelo pela VRAM disponível e permite baixar
  **somente com o clique explícito do usuário** (streaming de progresso via API local do Ollama).
- Confere o idioma ``pt-BR`` do OCR do Windows (``Windows.Media.Ocr``) e exibe o comando
  administrativo apenas como instrução (o aplicativo **nunca** executa comandos administrativos).
"""

from __future__ import annotations

import json
import re
import sys
import threading
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import UserSettings, ollama_url_loopback
from app.core.ollama_client import listar_modelos_ollama, ollama_disponivel
from app.core.platform_utils import find_binary
from app.core.vram_manager import get_gpu_vram_status

COMANDO_OCR_WINDOWS_PT_BR = 'Add-WindowsCapability -Online -Name "Language.OCR~~~pt-BR~0.0.1.0"'
LINK_OLLAMA_WINDOWS = "https://ollama.com/download/windows"
LINK_OLLAMA_MAC = "https://ollama.com/download/mac"
LINK_OLLAMA_PADRAO = "https://ollama.com/download"

_RE_MODELO_VALIDO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,120}$")

_PULL_LOCK = threading.Lock()
_PULL_ESTADO: Dict[str, Any] = {
    "ativo": False,
    "status": "idle",
    "modelo": "",
    "percent": 0,
    "mensagem": "",
    "erro": None,
}


def obter_progresso_pull() -> Dict[str, Any]:
    with _PULL_LOCK:
        return dict(_PULL_ESTADO)


def _atualizar_pull(**campos: Any) -> None:
    with _PULL_LOCK:
        _PULL_ESTADO.update(campos)


def sugerir_modelo_ollama(vram_total_mb: Optional[int], plataforma: str = sys.platform) -> Dict[str, str]:
    """Sugere o modelo Ollama adequado ao hardware (ex.: ``gemma3:4b`` em 6 GB de VRAM)."""
    if vram_total_mb is not None and vram_total_mb >= 12000:
        return {
            "modelo": "gemma3:12b",
            "motivo": f"GPU com {round(vram_total_mb / 1024)} GB de VRAM — suporta modelo de alta qualidade.",
        }
    if vram_total_mb is not None and vram_total_mb >= 5500:
        return {
            "modelo": "gemma3:4b",
            "motivo": f"GPU com {round(vram_total_mb / 1024)} GB de VRAM — ótimo equilíbrio entre velocidade e qualidade.",
        }
    if plataforma == "darwin":
        return {
            "modelo": "gemma4:e2b",
            "motivo": "Modelo leve recomendado para memória unificada no macOS.",
        }
    return {
        "modelo": "gemma4:e2b",
        "motivo": "Modelo compacto recomendado para placas com até 4 GB de VRAM ou execução em CPU.",
    }


def _consultar_idiomas_ocr_windows() -> Optional[List[str]]:
    """Consulta idiomas instalados no ``Windows.Media.Ocr`` sem privilégios administrativos."""
    try:
        import winocr  # noqa: F401
        from winsdk.windows.media.ocr import OcrEngine  # type: ignore[import-not-found]

        langs = OcrEngine.available_recognizer_languages
        return [str(getattr(lang, "language_tag", "") or "") for lang in langs]
    except Exception:
        pass
    try:
        from winrt.windows.media.ocr import OcrEngine  # type: ignore[import-not-found]

        langs = OcrEngine.available_recognizer_languages
        return [str(getattr(lang, "language_tag", "") or "") for lang in langs]
    except Exception:
        return None


def detectar_ocr_pt_br(plataforma: str = sys.platform) -> Dict[str, Any]:
    """Verifica se o OCR nativo e o idioma Português (pt-BR) estão disponíveis."""
    if plataforma == "win32":
        winocr_ok = False
        try:
            import winocr  # noqa: F401

            winocr_ok = True
        except Exception:
            winocr_ok = False

        idiomas = _consultar_idiomas_ocr_windows() if winocr_ok else None
        pt_br = False
        if idiomas is not None:
            pt_br = any(tag.lower().startswith("pt") for tag in idiomas)
        return {
            "plataforma": "windows",
            "motor_instalado": winocr_ok,
            "idioma_pt_br": pt_br,
            "disponivel": bool(winocr_ok and pt_br),
            "idiomas_detectados": idiomas or [],
            "comando_admin": COMANDO_OCR_WINDOWS_PT_BR,
            "passos": [
                "Abra o menu Iniciar, digite PowerShell, clique com o botão direito e escolha 'Executar como administrador'.",
                f"Cole e execute o comando: {COMANDO_OCR_WINDOWS_PT_BR}",
                "Aguarde a conclusão e clique em 'Verificar novamente' aqui no SentencIA.",
            ],
        }

    if plataforma == "darwin":
        ocrmac_ok = False
        try:
            import ocrmac  # noqa: F401

            ocrmac_ok = True
        except Exception:
            ocrmac_ok = False
        return {
            "plataforma": "macos",
            "motor_instalado": ocrmac_ok,
            "idioma_pt_br": ocrmac_ok,
            "disponivel": ocrmac_ok,
            "idiomas_detectados": ["pt-BR"] if ocrmac_ok else [],
            "comando_admin": "",
            "passos": [] if ocrmac_ok else ["Reexecute o instalador do macOS para instalar o suporte de OCR (ocrmac)."],
        }

    return {
        "plataforma": plataforma,
        "motor_instalado": False,
        "idioma_pt_br": False,
        "disponivel": False,
        "idiomas_detectados": [],
        "comando_admin": "",
        "passos": ["OCR nativo disponível no Windows e no macOS."],
    }


def diagnosticar_assistente(settings: UserSettings, *, plataforma: str = sys.platform) -> Dict[str, Any]:
    """Monta o diagnóstico completo para o Assistente de Primeira Execução."""
    hw = get_gpu_vram_status()
    vram_mb = hw.get("vram_total_mb")
    sugestao = sugerir_modelo_ollama(vram_mb, plataforma=plataforma)

    ollama_url = str(settings.ollama_url or "http://127.0.0.1:11434")
    online = ollama_disponivel(ollama_url) if ollama_url_loopback(ollama_url) else False
    binario = find_binary("ollama")
    instalado = bool(online or binario)
    modelos_instalados = listar_modelos_ollama(ollama_url) if online else []
    modelo_sugerido = sugestao["modelo"]
    ja_instalado = any(
        m == modelo_sugerido or m.startswith(modelo_sugerido + ":")
        for m in modelos_instalados
    )

    link_ollama = (
        LINK_OLLAMA_WINDOWS
        if plataforma == "win32"
        else LINK_OLLAMA_MAC
        if plataforma == "darwin"
        else LINK_OLLAMA_PADRAO
    )

    return {
        "assistente_concluido": bool(getattr(settings, "assistente_concluido", False)),
        "plataforma": plataforma,
        "gpu": {
            "gpu_available": bool(hw.get("gpu_available")),
            "gpu_name": hw.get("gpu_name"),
            "vram_total_mb": vram_mb,
        },
        "ollama": {
            "instalado": instalado,
            "online": online,
            "link_download": link_ollama,
            "modelo_sugerido": modelo_sugerido,
            "motivo_sugestao": sugestao["motivo"],
            "modelos_instalados": modelos_instalados,
            "modelo_sugerido_instalado": ja_instalado,
        },
        "ocr": detectar_ocr_pt_br(plataforma=plataforma),
        "pull": obter_progresso_pull(),
    }


def iniciar_download_modelo_ollama(modelo: str, ollama_url: str) -> Dict[str, Any]:
    """Dispara ``POST /api/pull`` no Ollama local em thread de fundo com progresso."""
    nome = (modelo or "").strip()
    if not _RE_MODELO_VALIDO.match(nome):
        raise ValueError(f"Nome de modelo Ollama inválido: {nome!r}")
    if not ollama_url_loopback(ollama_url):
        raise ValueError("A URL do Ollama precisa apontar para o computador local (loopback).")

    with _PULL_LOCK:
        if _PULL_ESTADO["ativo"]:
            return dict(_PULL_ESTADO)
        _PULL_ESTADO.update(
            {
                "ativo": True,
                "status": "starting",
                "modelo": nome,
                "percent": 0,
                "mensagem": f"Iniciando download de {nome}...",
                "erro": None,
            }
        )

    def _worker() -> None:
        url = ollama_url.rstrip("/") + "/api/pull"
        try:
            with httpx.Client(timeout=httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)) as client:
                with client.stream("POST", url, json={"name": nome, "stream": True}) as resp:
                    if resp.status_code >= 400:
                        _atualizar_pull(
                            ativo=False,
                            status="error",
                            mensagem=f"Ollama respondeu HTTP {resp.status_code}.",
                            erro=f"HTTP {resp.status_code}",
                        )
                        return
                    for linha in resp.iter_lines():
                        if not linha:
                            continue
                        try:
                            ev = json.loads(linha)
                        except ValueError:
                            continue
                        if ev.get("error"):
                            _atualizar_pull(
                                ativo=False,
                                status="error",
                                mensagem=str(ev["error"]),
                                erro=str(ev["error"]),
                            )
                            return
                        status_txt = str(ev.get("status") or "baixando")
                        total = int(ev.get("total") or 0)
                        completed = int(ev.get("completed") or 0)
                        pct = int(min(99, round(100 * completed / total))) if total > 0 else _PULL_ESTADO["percent"]
                        _atualizar_pull(status="downloading", percent=pct, mensagem=status_txt)
            _atualizar_pull(
                ativo=False,
                status="completed",
                percent=100,
                mensagem=f"Modelo {nome} pronto para uso!",
                erro=None,
            )
        except Exception as exc:
            _atualizar_pull(
                ativo=False,
                status="error",
                mensagem=f"Não foi possível baixar o modelo ({type(exc).__name__}).",
                erro=str(exc),
            )

    threading.Thread(target=_worker, name="sentencia-ollama-pull", daemon=True).start()
    return obter_progresso_pull()
