"""Cofre de segredos local (ex.: ``gemini_api_key``).

Backend preferencial: ``keyring`` (Windows Credential Manager / macOS Keychain), quando instalado
(extra ``seguranca``) e com backend funcional. Fallback: ``<DATA_DIR>/secrets/secrets.json`` com
permissão ``0o600`` em POSIX, gravado de forma atômica.

``SENTENCIA_SECRETS_BACKEND=arquivo`` (ou ``file``, aceitando também o legado
``JVDFM_SECRETS_BACKEND``) força o backend de arquivo (usado nos testes).
Os valores dos segredos nunca são registrados em log.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

__all__ = ["get_secret", "set_secret", "delete_secret", "has_secret", "backend_ativo", "SegredoInseguroError"]


class SegredoInseguroError(RuntimeError):
    """O cofre do sistema esta indisponivel e a pasta de dados e sincronizada com a nuvem."""

SERVICE_NAME = "SentencIA"
SERVICE_NAME_LEGADO = "SentencaJVDFM"
_ENV_BACKEND = "SENTENCIA_SECRETS_BACKEND"
_LOCK = threading.RLock()


# ----------------------------------------------------------------------------------------------
# Backend keyring (opcional)
# ----------------------------------------------------------------------------------------------
def _keyring() -> Optional[Any]:
    """Retorna o módulo ``keyring`` se utilizável; ``None`` para usar o arquivo."""
    from app.core.env import ler_env

    if (ler_env("SECRETS_BACKEND", "") or "").strip().lower() in {"arquivo", "file"}:
        return None
    try:
        import keyring  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        backend = keyring.get_keyring()
        nome = f"{type(backend).__module__}.{type(backend).__name__}".lower()
        if "fail" in nome or "null" in nome:
            return None
        if "chainer" in nome and not getattr(backend, "backends", None):
            return None
        prioridade = getattr(backend, "priority", 1)
        if isinstance(prioridade, (int, float)) and prioridade <= 0:
            return None
    except Exception:
        return None
    return keyring


def backend_ativo() -> str:
    """``"keyring"`` ou ``"arquivo"`` (informativo; nunca revela valores)."""
    return "keyring" if _keyring() is not None else "arquivo"


# ----------------------------------------------------------------------------------------------
# Backend arquivo (fallback)
# ----------------------------------------------------------------------------------------------
def _arquivo_segredos() -> Path:
    from app.core import config

    return Path(config.DATA_DIR) / "secrets" / "secrets.json"


def _ler_arquivo() -> Dict[str, str]:
    caminho = _arquivo_segredos()
    if not caminho.exists():
        return {}
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        logger.warning("Arquivo de segredos ilegível (%s); ignorando.", type(err).__name__)
        return {}
    if not isinstance(dados, dict):
        return {}
    return {str(k): str(v) for k, v in dados.items() if isinstance(v, str)}


def _restringir_acl_windows(pasta: Path) -> None:
    """Remove a heranca e concede acesso so ao usuario atual (icacls). Melhor esforco."""
    usuario = os.environ.get("USERNAME", "").strip()
    if not usuario:
        return
    try:
        import subprocess

        subprocess.run(
            ["icacls", str(pasta), "/inheritance:r", "/grant:r", f"{usuario}:(OI)(CI)F"],
            capture_output=True, timeout=15, check=False,
        )
    except Exception as err:  # pragma: no cover - icacls ausente
        logger.warning("Nao foi possivel restringir a ACL da pasta de segredos (%s).", type(err).__name__)


def _gravar_arquivo(dados: Dict[str, str]) -> None:
    caminho = _arquivo_segredos()
    if dados:
        from app.core import config

        if config.pasta_sincronizada(caminho.parent):
            raise SegredoInseguroError(
                "O cofre do sistema (keyring) esta indisponivel e a pasta de dados fica numa pasta "
                "sincronizada com a nuvem (OneDrive/iCloud): a chave nao sera gravada em arquivo. "
                "Instale o extra de seguranca (keyring) ou defina SENTENCIA_DATA_DIR para uma pasta local."
            )
    novo_dir = not caminho.parent.exists()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    if novo_dir and sys.platform == "win32":
        _restringir_acl_windows(caminho.parent)
    if sys.platform != "win32":
        try:
            os.chmod(caminho.parent, 0o700)
        except OSError:
            pass
    if not dados:
        if caminho.exists():
            caminho.unlink()
        return
    fd, tmp_name = tempfile.mkstemp(prefix=".secrets.", suffix=".tmp", dir=str(caminho.parent))
    tmp = Path(tmp_name)
    try:
        if sys.platform != "win32":
            os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(dados, fh, ensure_ascii=False, indent=2)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp, caminho)
        if sys.platform != "win32":
            os.chmod(caminho, 0o600)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _validar_nome(nome: str) -> str:
    if not isinstance(nome, str) or not nome.strip():
        raise ValueError("Nome de segredo inválido.")
    return nome.strip()


# ----------------------------------------------------------------------------------------------
# API pública
# ----------------------------------------------------------------------------------------------
def get_secret(nome: str) -> str:
    """Valor do segredo, ou ``""`` se ausente. Nunca levanta exceção por falha de backend."""
    nome = _validar_nome(nome)
    with _LOCK:
        kr = _keyring()
        if kr is not None:
            try:
                valor = kr.get_password(SERVICE_NAME, nome)
                if valor:
                    return valor
                valor_legado = kr.get_password(SERVICE_NAME_LEGADO, nome)
                if valor_legado:
                    try:
                        kr.set_password(SERVICE_NAME, nome, valor_legado)
                    except Exception as err:
                        logger.warning(
                            "Falha ao migrar segredo do cofre legado '%s' para '%s' (%s); mantendo legado.",
                            SERVICE_NAME_LEGADO,
                            SERVICE_NAME,
                            type(err).__name__,
                        )
                    else:
                        try:
                            kr.delete_password(SERVICE_NAME_LEGADO, nome)
                        except Exception as err:
                            logger.warning(
                                "Segredo copiado para '%s', mas falhou ao remover do legado '%s' (%s).",
                                SERVICE_NAME,
                                SERVICE_NAME_LEGADO,
                                type(err).__name__,
                            )
                        else:
                            logger.info(
                                "Segredo migrado do cofre legado '%s' para '%s'.",
                                SERVICE_NAME_LEGADO,
                                SERVICE_NAME,
                            )
                    return valor_legado
            except Exception as err:
                logger.warning("Falha ao ler segredo do keyring (%s); tentando arquivo.", type(err).__name__)
        return _ler_arquivo().get(nome, "")


def set_secret(nome: str, valor: str) -> None:
    """Grava o segredo. Valor vazio equivale a ``delete_secret``."""
    nome = _validar_nome(nome)
    valor = (valor or "").strip()
    if not valor:
        delete_secret(nome)
        return
    with _LOCK:
        kr = _keyring()
        if kr is not None:
            try:
                kr.set_password(SERVICE_NAME, nome, valor)
                try:
                    kr.delete_password(SERVICE_NAME_LEGADO, nome)
                except Exception:
                    pass
                # Remove eventual cópia antiga no arquivo de fallback.
                dados = _ler_arquivo()
                if nome in dados:
                    dados.pop(nome)
                    _gravar_arquivo(dados)
                return
            except Exception as err:
                logger.warning("Falha ao gravar no keyring (%s); usando arquivo local.", type(err).__name__)
        dados = _ler_arquivo()
        dados[nome] = valor
        _gravar_arquivo(dados)


def delete_secret(nome: str) -> None:
    """Remove o segredo de todos os backends (silencioso se ausente)."""
    nome = _validar_nome(nome)
    with _LOCK:
        kr = _keyring()
        if kr is not None:
            for servico in (SERVICE_NAME, SERVICE_NAME_LEGADO):
                try:
                    kr.delete_password(servico, nome)
                except Exception:
                    pass
        dados = _ler_arquivo()
        if nome in dados:
            dados.pop(nome)
            _gravar_arquivo(dados)


def has_secret(nome: str) -> bool:
    return bool(get_secret(nome))
