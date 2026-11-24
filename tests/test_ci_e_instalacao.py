# -*- coding: utf-8 -*-
"""
Testes automatizados dos workflows GitHub Actions (`.github/workflows/ci.yml` e `release.yml`),
do guia de instalação (`docs/INSTALACAO.md` e `docs/INSTALACAO.pdf`) e do `README.md` público (TASK-035).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pymupdf

RAIZ = Path(__file__).resolve().parent.parent


def _carregar_modulo(nome: str, caminho: Path):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)
    return mod


publicacao = _carregar_modulo("sentencia_publicacao_035", RAIZ / "scripts" / "publicacao.py")
build_release = _carregar_modulo("sentencia_build_release_035", RAIZ / "scripts" / "build_release.py")


def test_workflow_ci_estrutura_e_matriz():
    ci_path = RAIZ / ".github" / "workflows" / "ci.yml"
    assert ci_path.is_file()
    texto = ci_path.read_text(encoding="utf-8")

    for obrigatorio in (
        "push:",
        "pull_request:",
        "windows-latest",
        "macos-15",
        "macos-15-intel",
        "python-version: \"3.12\"",
        "playwright install chromium",
        "scripts/verificar_sigilo.py --apenas-padroes",
        "scripts/build_release.py --dry-run",
        "uv sync --frozen",
        "uv run --frozen --extra nuvem pytest",
        "aceitacao_juridica",
        "FORCE_JAVASCRIPT_ACTIONS_TO_NODE24",
        "actions/checkout@v5",
        "shellcheck",
        "instalador/instalar-mac.sh",
        "import ocrmac",
        "/api/health",
    ):
        assert obrigatorio in texto, f"Trecho ausente em ci.yml: {obrigatorio}"

    assert publicacao.arquivo_na_lista_branca(".github/workflows/ci.yml")


def test_workflow_release_artefatos_testes_limpos_e_publicacao():
    rel_path = RAIZ / ".github" / "workflows" / "release.yml"
    assert rel_path.is_file()
    texto = rel_path.read_text(encoding="utf-8")

    for obrigatorio in (
        "tags:",
        "- \"v*\"",
        "scripts/verificar_sigilo.py --apenas-padroes",
        "scripts/build_release.py",
        "choco install innosetup",
        "SentencIA.iss",
        "instalar.ps1",
        "instalar-mac.sh",
        "SHA256SUMS.txt",
        "CHANGELOG.md",
        "teste-instalacao-windows",
        "teste-instalacao-macos",
        "macos-15",
        "macos-15-intel",
        "softprops/action-gh-release@v2",
    ):
        assert obrigatorio in texto, f"Trecho ausente em release.yml: {obrigatorio}"

    assert publicacao.arquivo_na_lista_branca(".github/workflows/release.yml")


def test_guia_instalacao_markdown_e_pdf_completos():
    md_path = RAIZ / "docs" / "INSTALACAO.md"
    pdf_path = RAIZ / "docs" / "INSTALACAO.pdf"
    assert md_path.is_file()
    assert pdf_path.is_file()

    md = md_path.read_text(encoding="utf-8")
    for topico in (
        "instalar.ps1",
        "SentencIA-Setup-",
        "SmartScreen",
        "Mais informações",
        "Executar assim mesmo",
        "instalar-mac.sh",
        "Terminal",
        "Ollama",
        "OCR",
        "winocr",
        "ocrmac",
        "LOCALAPPDATA",
        "Library/Application Support/SentencIA",
        "desinstalar",
        "Privacidade",
        "127.0.0.1",
    ):
        assert topico in md, f"Tópico obrigatório ausente em docs/INSTALACAO.md: {topico}"

    with pymupdf.open(str(pdf_path)) as doc:
        assert len(doc) >= 2
        texto_pdf = "\n".join(pg.get_text("text") for pg in doc)
    assert "Guia de Instalação" in texto_pdf
    assert "SmartScreen" in texto_pdf
    assert "instalar-mac.sh" in texto_pdf

    assert publicacao.arquivo_na_lista_branca("docs/INSTALACAO.md")
    assert publicacao.arquivo_na_lista_branca("docs/INSTALACAO.pdf")
    assert not build_release.arquivo_excluido("docs/INSTALACAO.md")
    assert not build_release.arquivo_excluido("docs/INSTALACAO.pdf")


def test_readme_publico_com_captura_demo_instalacao_2_linhas_licenca_e_aviso():
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    captura = RAIZ / "docs" / "identidade" / "captura_caso_demo.png"
    assert captura.is_file() and captura.stat().st_size > 50_000

    for item in (
        "docs/identidade/captura_caso_demo.png",
        "instalar.ps1 | iex",
        "instalar-mac.sh | bash",
        "docs/INSTALACAO.md",
        "docs/INSTALACAO.pdf",
        "Licença MIT",
        "LICENSE",
        "minuta",
        "magistrado",
    ):
        assert item in readme, f"Item obrigatório ausente no README.md: {item}"


def test_verificador_falha_fechado_com_pdf_publico_ilegivel(tmp_path):
    """Revisão da TASK-035: um PDF público que não pode ser lido não pode passar como verificado."""
    verificador = _carregar_modulo("sentencia_verificar_sigilo_035", RAIZ / "scripts" / "verificar_sigilo.py")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "INSTALACAO.pdf").write_bytes(b"%PDF-1.7\nisto nao e um pdf valido\n")
    achados, _verificados, _termos = verificador.verificar_arvore(tmp_path, apenas_padroes=True)
    tipos = {(a.arquivo if hasattr(a, "arquivo") else a[0], a.tipo if hasattr(a, "tipo") else a[2]) for a in achados}
    assert any(tipo == "PDF_NAO_VERIFICAVEL" for _arq, tipo in tipos), tipos
