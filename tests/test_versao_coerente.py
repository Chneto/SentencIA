"""TASK-096 — a versão do programa tem uma fonte única (``pyproject.toml``) e todos os pontos que a repetem
(instaladores, lançador, Inno Setup) precisam coincidir; o workflow de release passa a versão ao Inno Setup e
confere a tag. Sem isso, um release novo saía com o instalador do Windows marcado com a versão antiga e a
marca de instalação dos bootstraps ficava com a versão errada."""

from __future__ import annotations

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def _versao() -> str:
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', (RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
    assert m, "versão ausente no pyproject.toml"
    return m.group(1)


LITERAIS = [
    ("instalador/bootstrap.ps1", r"\$VersaoApp\s*=\s*'([^']+)'"),
    ("instalador/bootstrap.sh", r'VERSAO_APP="([^"]+)"'),
    ("instalador/instalar.ps1", r"return\s+'(\d+\.\d+\.\d+)'"),
    ("instalador/instalar-mac.sh", r'\$\{v:-(\d+\.\d+\.\d+)\}'),
    ("launcher.py", r'return\s+"(\d+\.\d+\.\d+)"'),
    ("instalador/windows/SentencIA.iss", r'#define\s+MyAppVersion\s+"([^"]+)"'),
]


def test_todas_as_versoes_literais_coincidem_com_o_pyproject():
    versao = _versao()
    divergentes = []
    for arquivo, padrao in LITERAIS:
        texto = (RAIZ / arquivo).read_text(encoding="utf-8-sig")
        achados = re.findall(padrao, texto)
        assert achados, f"padrão de versão não encontrado em {arquivo}"
        divergentes += [f"{arquivo}: {v}" for v in achados if v != versao]
    assert not divergentes, f"versões diferentes de {versao}: {divergentes}"


def test_release_passa_a_versao_ao_inno_setup_e_confere_a_tag():
    wf = (RAIZ / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "/DMyAppVersion=${{ steps.versao.outputs.versao }}" in wf
    assert 'GITHUB_REF_NAME" -ne "v$versao"' in wf


def test_lock_tem_o_projeto_com_a_mesma_versao():
    lock = (RAIZ / "uv.lock").read_text(encoding="utf-8")
    assert re.search(r'name = "sentencia"\nversion = "' + re.escape(_versao()) + '"', lock)
