# -*- coding: utf-8 -*-
"""Configuração global da suíte (v2.0.0).

- Marcador ``dados_reais``: testes que exigem os autos reais (PDF/MP4 sigilosos, segredo de justiça).
  Eles só rodam quando a variável de ambiente ``SENTENCIA_REAL_CASES_DIR`` aponta para uma pasta
  existente com esses arquivos; caso contrário são PULADOS (nunca "passam" em silêncio).
  Exemplo (PowerShell): ``$env:SENTENCIA_REAL_CASES_DIR = "D:\\autos-sigilosos"; uv run pytest -m dados_reais``.
- Helpers para localizar os autos reais: :func:`real_cases_dir`, :func:`localizar_autos_reais` e a
  fixture ``autos_reais_dir``.
- Ambiente isolado para todos os testes: ``SENTENCIA_DISABLE_TOKEN=1`` (só tem efeito sob pytest; os
  testes de segurança removem a variável por conta própria) e ``SENTENCIA_SECRETS_BACKEND=arquivo``
  (a suíte nunca lê nem grava no cofre do sistema operacional — Keychain/Credential Manager).
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_DATA_DIR = (REPO_ROOT / "data").resolve()
_SESSION_TMP_DATA_DIR: Optional[Path] = None

REAL_CASES_ENV = "SENTENCIA_REAL_CASES_DIR"
MARCADOR_DADOS_REAIS = "dados_reais"
_DESCRICAO_MARCADOR = (
    f"{MARCADOR_DADOS_REAIS}: exige os autos reais (PDF/MP4 sigilosos) na pasta indicada por "
    f"{REAL_CASES_ENV}; pulado quando a variável não está definida"
)


def real_cases_dir() -> Optional[Path]:
    """Pasta dos autos reais (``SENTENCIA_REAL_CASES_DIR``) ou ``None`` se não definida/inexistente."""
    bruto = os.environ.get(REAL_CASES_ENV, "").strip()
    if not bruto:
        return None
    pasta = Path(os.path.expandvars(bruto)).expanduser()
    return pasta if pasta.is_dir() else None


def _motivo_sem_autos() -> str:
    bruto = os.environ.get(REAL_CASES_ENV, "").strip()
    if not bruto:
        return f"autos reais indisponíveis (defina {REAL_CASES_ENV} com a pasta dos PDFs/MP4 sigilosos)"
    return f"{REAL_CASES_ENV} aponta para uma pasta inexistente: {bruto}"


def localizar_autos_reais(*nomes: str) -> Path:
    """Caminho de um arquivo dos autos reais; PULA o teste se a pasta ou o arquivo não existir.

    Sem argumentos devolve a própria pasta. Com vários nomes, devolve o primeiro que existir.
    """
    pasta = real_cases_dir()
    if pasta is None:
        pytest.skip(_motivo_sem_autos())
    if not nomes:
        return pasta
    for nome in nomes:
        candidato = pasta / nome
        if candidato.exists():
            return candidato
    pytest.skip(f"arquivo(s) dos autos reais ausente(s) em {REAL_CASES_ENV}: {', '.join(nomes)}")
    raise AssertionError("inalcançável")  # pragma: no cover


def verificar_data_dir_isolado(data_dir: Path, repo_root: Path = REPO_ROOT) -> None:
    """Falha se ``data_dir`` apontar para a pasta ``data/`` real do repositório (TASK-031)."""
    alvo = Path(data_dir).resolve()
    real = (Path(repo_root) / "data").resolve()
    if alvo == real or real in alvo.parents:
        raise AssertionError(
            f"Isolamento violado: testes NÃO podem usar a pasta de dados real ({real}); "
            f"recebido SENTENCIA_DATA_DIR={alvo}."
        )


def _garantir_env_data_dir_isolado() -> Path:
    """Garante ``SENTENCIA_DATA_DIR`` isolado antes que qualquer módulo ``app.*`` seja importado."""
    global _SESSION_TMP_DATA_DIR
    atual = os.environ.get("SENTENCIA_DATA_DIR", "").strip()
    if atual:
        try:
            verificar_data_dir_isolado(Path(atual))
            return Path(atual)
        except AssertionError:
            pass
    pasta = Path(tempfile.mkdtemp(prefix="sentencia_pytest_data_"))
    _SESSION_TMP_DATA_DIR = pasta
    # Copia apenas o timbre institucional estático (não sigiloso) se existir.
    timbre_src = REAL_DATA_DIR / "templates_gabinete" / "timbre_tjrn.docx"
    if timbre_src.is_file():
        timbre_dst = pasta / "templates_gabinete" / "timbre_tjrn.docx"
        timbre_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(timbre_src, timbre_dst)
    os.environ["SENTENCIA_DATA_DIR"] = str(pasta)
    return pasta


def _limpar_session_tmp_data_dir() -> None:
    global _SESSION_TMP_DATA_DIR
    if _SESSION_TMP_DATA_DIR is not None:
        shutil.rmtree(_SESSION_TMP_DATA_DIR, ignore_errors=True)
        _SESSION_TMP_DATA_DIR = None


# Executa no carregamento do conftest (antes da importação de qualquer módulo de teste).
_garantir_env_data_dir_isolado()
atexit.register(_limpar_session_tmp_data_dir)


def pytest_configure(config: pytest.Config) -> None:
    _garantir_env_data_dir_isolado()
    config.addinivalue_line("markers", _DESCRICAO_MARCADOR)


def pytest_unconfigure(config: pytest.Config) -> None:
    _limpar_session_tmp_data_dir()


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    if real_cases_dir() is not None:
        return
    pular = pytest.mark.skip(reason=_motivo_sem_autos())
    for item in items:
        if MARCADOR_DADOS_REAIS in item.keywords:
            item.add_marker(pular)


@pytest.fixture
def autos_reais_dir() -> Path:
    """Pasta dos autos reais; pula o teste quando indisponível."""
    return localizar_autos_reais()


@pytest.fixture(scope="session", autouse=True)
def _salvaguarda_data_dir_isolado() -> None:
    """Falha toda a suíte se ``app.core.config.DATA_DIR`` apontar para ``<repo>/data``."""
    from app.core import config as core_config

    verificar_data_dir_isolado(core_config.DATA_DIR)
    verificar_data_dir_isolado(core_config.CASES_DIR)


@pytest.fixture(autouse=True)
def _ambiente_isolado_de_teste(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTENCIA_DISABLE_TOKEN", "1")
    monkeypatch.setenv("SENTENCIA_SECRETS_BACKEND", "arquivo")


@pytest.fixture(autouse=True)
def _limpar_cache_vram_entre_testes():
    """Limpa o cache de 10 s de medição GPU/VRAM antes e depois de cada teste (TASK-037)."""
    from app.core import vram_manager

    vram_manager.limpar_cache_vram()
    yield
    vram_manager.limpar_cache_vram()
