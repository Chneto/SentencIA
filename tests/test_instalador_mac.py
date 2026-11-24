# -*- coding: utf-8 -*-
"""Testes do instalador macOS (TASK-034): LF/sem BOM, sintaxe bash, contrato e empacotamento."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import build_release
from tests.test_empacotamento_v2 import _bash

RAIZ = Path(__file__).resolve().parent.parent
SCRIPTS_MAC = [
    "instalador/instalar-mac.sh",
    "instalador/desinstalar-mac.sh",
    "Iniciar SentencIA.command",
    "SentencIA.app/Contents/MacOS/sentencia",
]


@pytest.mark.parametrize("rel", SCRIPTS_MAC)
def test_scripts_mac_lf_sem_bom_e_shebang(rel: str):
    dados = (RAIZ / rel).read_bytes()
    assert b"\r\n" not in dados, f"{rel} contém CRLF (quebraria no macOS)"
    assert not dados.startswith(b"\xef\xbb\xbf"), f"{rel} contém BOM UTF-8"
    assert dados.startswith(b"#!"), f"{rel} não começa com shebang"


@pytest.mark.parametrize("rel", ["instalador/instalar-mac.sh", "instalador/desinstalar-mac.sh"])
def test_scripts_mac_sintaxe_bash(rel: str):
    bash = _bash()
    if not bash:
        pytest.skip("bash indisponível neste ambiente")
    alvo = str(RAIZ / rel).replace("\\", "/")
    r = subprocess.run([bash, "-n", alvo], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"Erro de sintaxe em {rel}: {r.stderr}"


@pytest.mark.parametrize("rel", ["instalador/instalar-mac.sh", "instalador/desinstalar-mac.sh"])
def test_scripts_mac_shellcheck_quando_disponivel(rel: str):
    sc = shutil.which("shellcheck")
    if not sc:
        pytest.skip("shellcheck indisponível localmente (validado no CI)")
    r = subprocess.run([sc, "-s", "bash", str(RAIZ / rel)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"shellcheck apontou problemas em {rel}:\n{r.stdout}\n{r.stderr}"


def test_instalar_mac_cumpre_contrato_task_034():
    txt = (RAIZ / "instalador/instalar-mac.sh").read_text(encoding="utf-8")
    # Sem sudo
    assert "sudo " not in txt
    # Convenção fixa de pastas no Mac
    assert "$HOME/Applications/SentencIA" in txt
    assert "$HOME/Applications/SentencIA.app" in txt
    assert "$HOME/Library/Application Support/SentencIA" in txt
    assert "SENTENCIA_DATA_DIR" in txt
    # Conferência de SHA-256
    assert "shasum -a 256" in txt
    assert "SHA256SUMS.txt" in txt
    # Montagem local do .app com ícone
    assert "AppIcon.icns" in txt
    assert "Contents/Info.plist" in txt
    assert "Contents/MacOS/sentencia" in txt
    # Chama bootstrap.sh com suporte a Apple Silicon/Intel e extras
    assert "bootstrap.sh" in txt
    assert "sysctl.proc_translated" in txt
    assert "xcode-select" in txt
    # Instala o desinstalador
    assert "desinstalar.command" in txt


def test_desinstalar_mac_padrao_mantem_dados():
    txt = (RAIZ / "instalador/desinstalar-mac.sh").read_text(encoding="utf-8")
    assert "$HOME/Library/Application Support/SentencIA" in txt
    assert "launcher.py" in txt and "--parar" in txt
    assert "Manter seus dados do gabinete" in txt
    assert "--manter-dados" in txt
    assert "--apagar-dados" in txt


def test_lancadores_mac_passam_SENTENCIA_data_dir():
    cmd = (RAIZ / "Iniciar SentencIA.command").read_text(encoding="utf-8")
    app_bin = (RAIZ / "SentencIA.app/Contents/MacOS/sentencia").read_text(encoding="utf-8")
    esperado = "$HOME/Library/Application Support/SentencIA"
    assert "SENTENCIA_DATA_DIR" in cmd and esperado in cmd
    assert "SENTENCIA_DATA_DIR" in app_bin and esperado in app_bin


def test_desinstalar_mac_execucao_mantem_dados_por_padrao(tmp_path: Path):
    bash = _bash()
    if not bash:
        pytest.skip("bash indisponível neste ambiente")

    destino = tmp_path / "Applications" / "SentencIA"
    app_bundle = tmp_path / "Applications" / "SentencIA.app"
    dados = tmp_path / "Library" / "Application Support" / "SentencIA"
    (destino / "instalador").mkdir(parents=True, exist_ok=True)
    (app_bundle / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
    (dados / "cases").mkdir(parents=True, exist_ok=True)
    autos = dados / "cases" / "processo_ficticio.pdf"
    autos.write_bytes(b"%PDF-1.4")

    script = str(RAIZ / "instalador/desinstalar-mac.sh").replace("\\", "/")
    env = dict(os.environ)
    env["PATH"] = str(Path(bash).parent) + os.pathsep + env.get("PATH", "")
    r = subprocess.run(
        [
            bash,
            script,
            "--destino",
            destino.as_posix(),
            "--app-destino",
            app_bundle.as_posix(),
            "--dados",
            dados.as_posix(),
            "--manter-dados",
            "--sim",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert not destino.exists()
    assert not app_bundle.exists()
    assert autos.exists(), "Os dados do gabinete devem ser mantidos por padrão"


def test_build_release_inclui_instalador_mac_como_executavel_e_guia():
    plano = build_release.coletar(RAIZ)
    mapa = {i.relativo: i for i in plano.itens}
    assert "instalador/instalar-mac.sh" in mapa
    assert mapa["instalador/instalar-mac.sh"].executavel is True
    assert "instalador/desinstalar-mac.sh" in mapa
    assert mapa["instalador/desinstalar-mac.sh"].executavel is True
    assert "docs/INSTALACAO_MAC.md" in mapa
    assert not [a for a in plano.alertas if a.startswith(("PROIBIDO", "ESSENCIAL", "CAMINHO PESSOAL"))]
