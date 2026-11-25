# -*- coding: utf-8 -*-
"""Cria o atalho do SentencIA na Área de Trabalho (Windows, macOS e Linux).

- **Windows:** ``SentencIA.lnk`` na Área de Trabalho real do usuário (inclusive quando ela é
  redirecionada para o OneDrive), apontando para ``wscript.exe "Iniciar_SentencIA.vbs"``
  (inicia sem janela quando já instalado; na primeira vez abre o instalador). Ícone gerado a partir
  de ``app/static/assets/logo_sentencia.png``.
- **macOS:** apelido (alias do Finder) do ``SentencIA.app`` na Mesa; se o Finder não puder ser
  automatizado, um link simbólico.
- **Linux:** arquivo ``.desktop`` que executa ``instalador/bootstrap.sh`` em um terminal.

Uso: ``uv run python scripts/criar_atalho_desktop.py`` (o instalador oferece criar o atalho na
primeira instalação). Não requer privilégios de administrador.
"""

from __future__ import annotations

__author__ = "FChNeto"

import base64
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_DIR / "app" / "static" / "assets"
NOME_ATALHO = "SentencIA"
DESCRICAO = "SentencIA — apoio à prolação de sentenças penais (Lei 11.340/2006)"


# ----------------------------------------------------------------------------------------------
# Ícone
# ----------------------------------------------------------------------------------------------
def garantir_icone_ico() -> Optional[Path]:
    """``app_icon.ico`` (gera a partir do PNG se faltar). ``None`` se não houver como gerar."""
    ico = ASSETS_DIR / "app_icon.ico"
    if ico.exists():
        return ico
    png = ASSETS_DIR / "logo_sentencia.png"
    if not png.exists():
        return None
    try:
        from PIL import Image

        Image.open(png).convert("RGBA").save(
            ico, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        )
        return ico
    except Exception as err:  # o atalho funciona sem ícone próprio
        print(f"Aviso: não foi possível gerar o ícone ({err}).")
        return None


# ----------------------------------------------------------------------------------------------
# Windows
# ----------------------------------------------------------------------------------------------
_PS_ATALHO = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$desktop = $env:SENTENCIA_ATALHO_DESTINO
if (-not $desktop) { $desktop = [Environment]::GetFolderPath('Desktop') }
if (-not $desktop) { throw 'Area de Trabalho nao encontrada.' }
$lnk = Join-Path $desktop ($env:SENTENCIA_ATALHO_NOME + '.lnk')
$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = (Join-Path $env:SystemRoot 'System32\wscript.exe')
$s.Arguments = '"' + $env:SENTENCIA_ATALHO_VBS + '"'
$s.WorkingDirectory = $env:SENTENCIA_ATALHO_PASTA
if ($env:SENTENCIA_ATALHO_ICONE) { $s.IconLocation = $env:SENTENCIA_ATALHO_ICONE + ',0' }
$s.Description = $env:SENTENCIA_ATALHO_DESCRICAO
$s.Save()
Write-Output $lnk
"""


def criar_atalho_windows(destino: Optional[Path] = None) -> List[Path]:
    """Cria o ``.lnk`` na Área de Trabalho (ou em ``destino``, usado nos testes)."""
    vbs = PROJECT_DIR / "Iniciar_SentencIA.vbs"
    if not vbs.exists():
        raise FileNotFoundError(f"Iniciador não encontrado: {vbs}")
    ico = garantir_icone_ico()
    env = dict(os.environ)
    # Caminhos vão por variável de ambiente: espaços, acentos e apóstrofos não quebram o script.
    env.update(
        {
            "SENTENCIA_ATALHO_NOME": NOME_ATALHO,
            "SENTENCIA_ATALHO_VBS": str(vbs),
            "SENTENCIA_ATALHO_PASTA": str(PROJECT_DIR),
            "SENTENCIA_ATALHO_ICONE": str(ico) if ico else "",
            "SENTENCIA_ATALHO_DESCRICAO": DESCRICAO,
            "SENTENCIA_ATALHO_DESTINO": str(destino) if destino else "",
        }
    )
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    # -EncodedCommand (UTF-16LE em base64) evita qualquer problema de aspas na linha de comando.
    codificado = base64.b64encode(_PS_ATALHO.encode("utf-16-le")).decode("ascii")
    resultado = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", codificado],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if resultado.returncode != 0:
        raise RuntimeError(f"Falha ao criar o atalho: {resultado.stderr.strip() or resultado.stdout.strip()}")
    linhas = [linha.strip() for linha in resultado.stdout.splitlines() if linha.strip()]
    return [Path(linhas[-1])] if linhas else []


# ----------------------------------------------------------------------------------------------
# macOS
# ----------------------------------------------------------------------------------------------
def _escapar_applescript(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace('"', '\\"')


def criar_atalho_macos() -> List[Path]:
    app = PROJECT_DIR / "SentencIA.app"
    if not app.exists():
        raise FileNotFoundError(f"Aplicativo não encontrado: {app}")
    # Um .app em quarentena (zip baixado) é executado via App Translocation e se recusa a abrir:
    # remove a quarentena do programa antes de criar o apelido (melhor esforço).
    try:
        subprocess.run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(PROJECT_DIR)],
                       capture_output=True, check=False, timeout=120)
    except (OSError, subprocess.SubprocessError):
        pass
    mesa = Path.home() / "Desktop"
    destino = mesa / NOME_ATALHO
    script = (
        'tell application "Finder"\n'
        f'  set alvo to POSIX file "{_escapar_applescript(str(app))}" as alias\n'
        f'  set atalho to make new alias file at desktop to alvo\n'
        f'  set name of atalho to "{_escapar_applescript(NOME_ATALHO)}"\n'
        "end tell"
    )
    if not destino.exists():
        r = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True, check=False)
        if r.returncode == 0:
            return [destino]
        print(f"Aviso: o Finder não criou o apelido ({r.stderr.strip()}); usando link simbólico.")
        mesa.mkdir(parents=True, exist_ok=True)
        os.symlink(app, destino)
    return [destino]


# ----------------------------------------------------------------------------------------------
# Linux
# ----------------------------------------------------------------------------------------------
def criar_atalho_linux() -> List[Path]:
    mesa = Path(os.environ.get("XDG_DESKTOP_DIR", "") or (Path.home() / "Desktop"))
    mesa.mkdir(parents=True, exist_ok=True)
    destino = mesa / "sentencia.desktop"
    bootstrap = PROJECT_DIR / "instalador" / "bootstrap.sh"
    icone = ASSETS_DIR / "logo_sentencia.png"
    destino.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={NOME_ATALHO}\n"
        f"Comment={DESCRICAO}\n"
        f'Exec=/bin/bash "{bootstrap}"\n'
        f"Path={PROJECT_DIR}\n"
        f"Icon={icone}\n"
        "Terminal=true\n",
        encoding="utf-8",
    )
    destino.chmod(0o755)
    return [destino]


def main() -> int:
    try:
        if sys.platform == "win32":
            criados = criar_atalho_windows()
        elif sys.platform == "darwin":
            criados = criar_atalho_macos()
        else:
            criados = criar_atalho_linux()
    except Exception as err:
        print(f"Não foi possível criar o atalho: {err}")
        return 1
    for c in criados:
        print(f"Atalho criado: {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
