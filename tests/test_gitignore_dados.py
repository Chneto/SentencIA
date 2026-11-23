"""TASK-079: guarda de que todo arquivo sigiloso gravado em ``data/`` é ignorado pelo git.

Só usa caminhos fictícios e ``git ls-files``; nunca lê o conteúdo de ``data/``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]

# Caminhos fictícios dentro de cada pasta/arquivo que o app grava em data/
# (app/core/config.py, app/core/secrets.py, app/core/session_state.py, jobs/manager.py,
# migracao_dados.py, limpeza_demo.py, scripts/build_release.py e scripts/verificar_sigilo.py).
CAMINHOS_SIGILOSOS = [
    "data/cases/x.pdf",
    "data/cases/caso_ficticio/bundle.json",
    "data/cases/.ultimo_caso_real.json",  # local antigo (retrocompatibilidade)
    "data/lote/x.pdf",
    "data/secrets/secrets.json",
    "data/logs/app.log",
    "data/jobs/x.json",
    "data/exports/x.docx",
    "data/templates_gabinete/amostras/x.docx",
    "data/user_settings.json",
    "data/.ultimo_caso_real.json",
    "data/.servidor.json",
    "data/.launcher.lock",
    "data/.instalado",
    "data/templates_gabinete/style_profile.json",
    "data/knowledge_base/custom_precedentes.json",
]

# Únicos arquivos versionados em data/ (dados públicos do produto). O perfil de estilo e os precedentes do
# gabinete saíram do índice no commit f6bcd93 e não podem voltar.
VERSIONADOS_PERMITIDOS = {
    "data/knowledge_base/acervo_completo.json",
    "data/templates_gabinete/timbre_tjrn.docx",
}


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", check=False
    )


@pytest.fixture(scope="module", autouse=True)
def _exige_git() -> None:
    if shutil.which("git") is None:
        pytest.skip("git não disponível (pacote extraído)")
    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        pytest.skip("fora de um repositório git (pacote extraído)")


@pytest.mark.parametrize("caminho", CAMINHOS_SIGILOSOS)
def test_caminho_sigiloso_ignorado(caminho: str) -> None:
    resultado = _git("check-ignore", "-q", "--no-index", caminho)
    assert resultado.returncode == 0, f"{caminho} NÃO está coberto pelo .gitignore"


def test_sem_arquivo_versionado_inesperado_em_data() -> None:
    resultado = _git("ls-files", "data")
    assert resultado.returncode == 0, resultado.stderr
    versionados = {linha.strip() for linha in resultado.stdout.splitlines() if linha.strip()}
    inesperados = sorted(versionados - VERSIONADOS_PERMITIDOS)
    assert not inesperados, f"arquivos versionados em data/ fora da lista de exceções: {inesperados}"
