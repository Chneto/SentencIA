# -*- coding: utf-8 -*-
"""Empacotamento e plataforma (v2.0.0): pyproject, iniciadores, pacote de distribuição e conftest."""

from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

RAIZ = Path(__file__).resolve().parent.parent


def _carregar_modulo(nome: str, caminho: Path):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    spec.loader.exec_module(modulo)
    return modulo


build_release = _carregar_modulo("jvdfm_build_release", RAIZ / "scripts" / "build_release.py")
launcher = _carregar_modulo("jvdfm_launcher", RAIZ / "launcher.py")
conftest_raiz = _carregar_modulo("jvdfm_conftest_raiz", RAIZ / "tests" / "conftest.py")


# ----------------------------------------------------------------------------------------------
# pyproject / lock
# ----------------------------------------------------------------------------------------------
@pytest.mark.skipif(tomllib is None, reason="tomllib requer Python 3.11+")
def test_pyproject_versao_extras_e_grupos():
    dados = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
    projeto = dados["project"]
    assert projeto["version"] == "2.1.0"
    assert projeto["requires-python"] == ">=3.11,<3.15"
    assert projeto["readme"] == "README.md"
    extras = projeto["optional-dependencies"]
    assert any(d.startswith("faster-whisper") for d in extras["transcricao"])
    assert extras["ocr-windows"] and all("sys_platform == 'win32'" in d for d in extras["ocr-windows"])
    assert extras["ocr-macos"] and all("sys_platform == 'darwin'" in d for d in extras["ocr-macos"])
    assert any(d.startswith("google-genai") for d in extras["nuvem"])
    assert any(d.lower().startswith("speechrecognition") for d in extras["nuvem"])
    assert any(d.startswith("keyring") for d in extras["seguranca"])
    # Núcleo mínimo: recursos pesados/nuvem/dev ficam fora das dependências base;
    # OCR nativo (com marcador de plataforma) e cofre keyring ficam nas principais (TASK-027).
    base = " ".join(projeto["dependencies"]).lower()
    for pesado in ("faster-whisper", "google-genai", "speechrecognition", "pytest"):
        assert pesado not in base
    assert any(d.startswith("winocr") and "sys_platform == 'win32'" in d for d in projeto["dependencies"])
    assert any(d.startswith("ocrmac") and "sys_platform == 'darwin'" in d for d in projeto["dependencies"])
    assert any(d.startswith("keyring") for d in projeto["dependencies"])
    assert any(d.startswith("pytest") for d in dados["dependency-groups"]["dev"])
    markers = dados["tool"]["pytest"]["ini_options"]["markers"]
    assert any(m.startswith("dados_reais") for m in markers)


@pytest.mark.skipif(tomllib is None, reason="tomllib requer Python 3.11+")
def test_uv_lock_cobre_windows_e_macos():
    lock = tomllib.loads((RAIZ / "uv.lock").read_text(encoding="utf-8"))
    nomes = {p["name"] for p in lock["package"]}
    for pacote in ("sentenca-jvdfm", "faster-whisper", "winocr", "ocrmac", "keyring", "google-genai"):
        assert pacote in nomes
    projeto = next(p for p in lock["package"] if p["name"] == "sentenca-jvdfm")
    assert projeto["version"] == "2.1.0"


# ----------------------------------------------------------------------------------------------
# Iniciadores: terminações de linha, BOM e sintaxe
# ----------------------------------------------------------------------------------------------
SCRIPTS_LF = [
    "Iniciar SentencIA.command",
    "instalador/bootstrap.sh",
    "SentencIA.app/Contents/MacOS/sentencia",
]
SCRIPTS_CRLF = ["Iniciar SentencIA.bat", "Iniciar_SentencIA.vbs", "instalador/bootstrap.ps1"]


@pytest.mark.parametrize("rel", SCRIPTS_LF)
def test_scripts_unix_com_lf_e_shebang(rel):
    dados = (RAIZ / rel).read_bytes()
    assert b"\r\n" not in dados, f"{rel} tem CRLF: quebraria no macOS"
    assert not dados.startswith(b"\xef\xbb\xbf")
    assert dados.startswith(b"#!"), f"{rel} sem shebang"


@pytest.mark.parametrize("rel", SCRIPTS_CRLF)
def test_scripts_windows_com_crlf(rel):
    dados = (RAIZ / rel).read_bytes()
    sem_crlf = dados.replace(b"\r\n", b"")
    assert b"\n" not in sem_crlf, f"{rel} tem LF solto (esperado CRLF)"
    if rel.endswith(".ps1"):
        assert dados.startswith(b"\xef\xbb\xbf"), "bootstrap.ps1 precisa de BOM UTF-8 (Windows PowerShell 5.1)"
    else:
        dados.decode("ascii")  # .bat/.vbs só ASCII: o cmd/wscript não leem UTF-8 sem BOM


def test_iniciadores_apontam_para_o_bootstrap():
    bat = (RAIZ / "Iniciar SentencIA.bat").read_text(encoding="ascii")
    assert "-ExecutionPolicy Bypass" in bat and "-NoProfile" in bat and "-File" in bat
    assert 'instalador\\bootstrap.ps1"' in bat and "%~dp0" in bat
    command = (RAIZ / "Iniciar SentencIA.command").read_text(encoding="utf-8")
    assert "set -euo pipefail" in command and 'cd "$(dirname "$0")"' in command
    assert "instalador/bootstrap.sh" in command
    app_exec = (RAIZ / "SentencIA.app/Contents/MacOS/sentencia").read_text(encoding="utf-8")
    assert "open -a Terminal" in app_exec
    shim = (RAIZ / "launcher.pyw").read_text(encoding="utf-8").strip().splitlines()
    assert len(shim) <= 3 and any("from launcher import main" in l for l in shim)


def test_info_plist_do_app():
    info = plistlib.loads((RAIZ / "SentencIA.app/Contents/Info.plist").read_bytes())
    assert info["CFBundlePackageType"] == "APPL"
    assert info["CFBundleShortVersionString"] == "2.1.0"
    assert info["CFBundleVersion"] == "2.1.0"
    assert (RAIZ / "SentencIA.app/Contents/MacOS" / info["CFBundleExecutable"]).is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="parser do Windows PowerShell")
def test_bootstrap_ps1_sem_erros_de_sintaxe():
    ps = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not ps.exists():
        pytest.skip("Windows PowerShell indisponível")
    env = dict(os.environ, JVDFM_PS1_ALVO=str(RAIZ / "instalador" / "bootstrap.ps1"))
    comando = (
        "$t=$null; $e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
        "$env:JVDFM_PS1_ALVO, [ref]$t, [ref]$e); $e.Count"
    )
    r = subprocess.run([str(ps), "-NoProfile", "-NonInteractive", "-Command", comando], env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == "0", r.stdout


def _bash() -> str | None:
    if sys.platform != "win32":
        return shutil.which("bash")
    git = shutil.which("git")
    if not git:
        return None
    base = Path(git).resolve().parent.parent
    for candidato in (base / "usr/bin/bash.exe", base / "bin/bash.exe", base / "usr/bin/sh.exe"):
        if candidato.exists():
            r = subprocess.run([str(candidato), "--version"], capture_output=True, text=True)
            if "GNU bash" in r.stdout:
                return str(candidato)
    return None


@pytest.mark.parametrize("rel", SCRIPTS_LF)
def test_scripts_bash_sem_erros_de_sintaxe(rel):
    bash = _bash()
    if not bash:
        pytest.skip("bash indisponível")
    r = subprocess.run([bash, "-n", str(RAIZ / rel).replace("\\", "/")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def test_bootstrap_idempotente_e_sem_admin():
    ps1 = (RAIZ / "instalador/bootstrap.ps1").read_text(encoding="utf-8-sig")
    sh = (RAIZ / "instalador/bootstrap.sh").read_text(encoding="utf-8")
    for texto in (ps1, sh):
        assert ".instalado" in texto and "bootstrap.log" in texto
        assert "astral.sh/uv/install" in texto
        assert "launcher.py" in texto
    assert "Start-Process -Verb RunAs" not in ps1 and "sudo " not in sh
    assert "winget" in ps1 and "Ollama.Ollama" in ps1
    assert "brew" in sh and "arm64" in sh and "AppTranslocation" in sh


# ----------------------------------------------------------------------------------------------
# build_release
# ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "rel",
    [
        "data/cases/autos.pdf",
        "data/cases/_arquivados/20260101_000000.zip",
        "data/user_settings.json",
        "data/secrets/secrets.json",
        "data/logs/app.log",
        "data/exports/Sentenca.docx",
        "data/jobs/x.json",
        "data/templates_gabinete/amostras/MOD-01.txt",
        "Modelos de Sentenças/sentenca.docx",
        "docs/design/screenshots/tela.png",
        ".venv/Scripts/python.exe",
        ".git/config",
        "app/__pycache__/main.cpython-312.pyc",
        "tests/test_x.py",
        "dist/SentencIA-v2.1.0.zip",
        "outra/autos.pdf.pje_cache_v2.json",
        "outra/audiencia.mp4.transcript_v2.json",
        "data/.instalado",
    ],
)
def test_build_release_exclui_conteudo_sigiloso(rel):
    assert build_release.arquivo_excluido(rel)


@pytest.mark.parametrize(
    "rel",
    ["app/main.py", "uv.lock", "pyproject.toml", "data/knowledge_base/acervo_completo.json",
     "data/templates_gabinete/timbre_tjrn.docx", "instalador/bootstrap.sh", "SentencIA.app/Contents/Info.plist"],
)
def test_build_release_inclui_programa(rel):
    assert not build_release.arquivo_excluido(rel)


def test_build_release_testes_so_com_opcao():
    assert build_release.arquivo_excluido("tests/test_x.py")
    assert not build_release.arquivo_excluido("tests/test_x.py", incluir_testes=True)


def _projeto_sintetico(raiz: Path) -> None:
    arquivos = {
        "pyproject.toml": '[project]\nname = "x"\nversion = "9.9.9"\n',
        "uv.lock": "version = 1\n",
        "README.md": "leia\n",
        "launcher.py": "print('x')\n",
        "app/main.py": "app = None\n",
        "app/static/index.html": "<html></html>\n",
        "Iniciar SentencIA.bat": "@echo off\necho oi\n",
        "Iniciar SentencIA.command": "#!/bin/bash\r\necho oi\r\n",
        "instalador/bootstrap.ps1": "Write-Host 'Sentença'\n",
        "instalador/bootstrap.sh": "#!/usr/bin/env bash\r\necho oi\r\n",
        "SentencIA.app/Contents/Info.plist": "<plist/>\n",
        "SentencIA.app/Contents/MacOS/sentencia": "#!/bin/bash\necho oi\n",
        "data/knowledge_base/acervo_completo.json": "{}",
        "data/cases/autos.pdf": "%PDF sigiloso",
        "data/cases/autos.pdf.pje_cache_v2.json": "{}",
        "data/user_settings.json": "{}",
        "data/secrets/secrets.json": "{}",
        "Modelos de Sentenças/real.txt": "nome real",
        "docs/design/screenshots/a.png": "png",
        "docs/context.md": "Instale em " + "C:\\Users\\" + "fulano\\Sistema\n",
        "tests/test_a.py": "def test(): pass\n",
    }
    for rel, conteudo in arquivos.items():
        destino = raiz / rel
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(conteudo.encode("utf-8"))


def test_build_release_coleta_e_gera_zip_com_permissoes(tmp_path):
    projeto = tmp_path / "Projeto Ação"
    _projeto_sintetico(projeto)
    plano = build_release.coletar(projeto)
    incluidos = {i.relativo for i in plano.itens}
    assert plano.versao == "9.9.9"
    for proibido in ("data/cases/autos.pdf", "data/user_settings.json", "data/secrets/secrets.json",
                     "Modelos de Sentenças/real.txt", "docs/design/screenshots/a.png", "tests/test_a.py",
                     "data/cases/autos.pdf.pje_cache_v2.json"):
        assert proibido not in incluidos
    assert {"app/main.py", "uv.lock", "data/knowledge_base/acervo_completo.json"} <= incluidos
    assert not [a for a in plano.alertas if a.startswith(("PROIBIDO", "ESSENCIAL"))]
    assert any(a.startswith("CAMINHO PESSOAL em docs/context.md") for a in plano.alertas)

    destino, soma = build_release.gerar_zip(plano, tmp_path / "dist" / "pacote.zip")
    assert len(soma) == 64
    with zipfile.ZipFile(destino) as zf:
        nomes = zf.namelist()
        assert all(n.startswith("SentencIA-v9.9.9/") for n in nomes)

        def info(rel):
            return zf.getinfo(f"SentencIA-v9.9.9/{rel}")

        for rel in ("Iniciar SentencIA.command", "instalador/bootstrap.sh",
                    "SentencIA.app/Contents/MacOS/sentencia"):
            i = info(rel)
            assert i.create_system == 3
            assert (i.external_attr >> 16) & 0o777 == 0o755, rel
            assert b"\r\n" not in zf.read(i)
        assert (info("app/main.py").external_attr >> 16) & 0o777 == 0o644
        assert zf.read(info("Iniciar SentencIA.bat")) == b"@echo off\r\necho oi\r\n"
        ps1 = zf.read(info("instalador/bootstrap.ps1"))
        assert ps1.startswith(b"\xef\xbb\xbf") and ps1.endswith(b"\r\n")
        somas = zf.read("SentencIA-v9.9.9/SHA256SUMS.txt").decode("utf-8")
        assert "  app/main.py" in somas and "data/cases" not in somas


def test_build_release_dry_run_do_repositorio_nao_tem_proibidos():
    plano = build_release.coletar(RAIZ)
    relativos = [i.relativo for i in plano.itens]
    assert build_release.verificar_lista(relativos) == []
    assert build_release.verificar_essenciais(relativos) == []
    assert [a for a in plano.alertas if a.startswith("CAMINHO PESSOAL")] == []
    for rel in relativos:
        assert not rel.startswith(("data/cases", "data/secrets", "data/logs", "data/exports", "tests/",
                                   "Modelos de Sentenças", "docs/design", ".venv", ".git/", ".agent/", ".ai-collab/"))
        assert rel not in ("data/user_settings.json", ".git", "CLAUDE.md")
    executaveis = {i.relativo for i in plano.itens if i.executavel}
    assert executaveis == {
        "Iniciar SentencIA.command",
        "instalador/bootstrap.sh",
        "instalador/instalar-mac.sh",
        "instalador/desinstalar-mac.sh",
        "SentencIA.app/Contents/MacOS/sentencia",
    }


# ----------------------------------------------------------------------------------------------
# launcher.py
# ----------------------------------------------------------------------------------------------
def test_launcher_data_dir_respeita_variavel(monkeypatch, tmp_path):
    monkeypatch.setenv("JVDFM_DATA_DIR", str(tmp_path / "dados"))
    assert launcher.data_dir() == tmp_path / "dados"
    assert launcher.logs_dir() == tmp_path / "dados" / "logs"
    monkeypatch.delenv("JVDFM_DATA_DIR")
    assert launcher.data_dir() == RAIZ / "data"


def test_launcher_porta_preferida(monkeypatch):
    monkeypatch.delenv("JVDFM_PORT", raising=False)
    assert launcher.porta_preferida(None) == 8000
    monkeypatch.setenv("JVDFM_PORT", "8123")
    assert launcher.porta_preferida(None) == 8123
    assert launcher.porta_preferida(9001) == 9001
    monkeypatch.setenv("JVDFM_PORT", "abc")
    assert launcher.porta_preferida(None) == 8000


def test_launcher_escolhe_outra_porta_quando_ocupada():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as ocupado:
        ocupado.bind(("127.0.0.1", 0))
        ocupado.listen(1)
        porta = ocupado.getsockname()[1]
        assert not launcher.porta_livre(porta)
        escolhida = launcher.escolher_porta(porta)
        assert escolhida != porta and launcher.porta_livre(escolhida)


def test_launcher_consulta_health(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            corpo = json.dumps({"status": "ok", "versao": "2.1.0"}).encode()
            self.send_response(200 if self.path == "/api/health" else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, *args):
            pass

    servidor = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=servidor.serve_forever, daemon=True)
    t.start()
    try:
        porta = servidor.server_address[1]
        assert launcher.consultar_health(porta) == {"status": "ok", "versao": "2.1.0"}
        assert launcher.aguardar_health(porta, None, timeout=5)
    finally:
        servidor.shutdown()
        servidor.server_close()
    assert launcher.consultar_health(porta, timeout=0.5) is None


def test_launcher_aguardar_health_detecta_servidor_que_morreu():
    proc = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
    proc.wait(timeout=30)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    assert launcher.aguardar_health(porta, proc, timeout=10) is False


def test_launcher_usa_python_do_venv():
    python = Path(launcher.python_do_venv())
    assert python.exists()
    venv = RAIZ / ".venv"
    if venv.exists():
        assert venv in python.parents


def test_launcher_modo_console(monkeypatch):
    monkeypatch.setenv("JVDFM_LAUNCHER_CONSOLE", "1")
    assert launcher._tem_console() is True
    monkeypatch.setenv("JVDFM_LAUNCHER_CONSOLE", "0")
    assert launcher._modo_console() is False


# ----------------------------------------------------------------------------------------------
# conftest (marcador dados_reais)
# ----------------------------------------------------------------------------------------------
def test_marcador_dados_reais_registrado(pytestconfig):
    assert any(str(m).startswith("dados_reais") for m in pytestconfig.getini("markers"))


def test_localizar_autos_reais_pula_sem_variavel(monkeypatch, tmp_path):
    monkeypatch.delenv("JVDFM_REAL_CASES_DIR", raising=False)
    assert conftest_raiz.real_cases_dir() is None
    with pytest.raises(pytest.skip.Exception):
        conftest_raiz.localizar_autos_reais("autos.pdf")
    monkeypatch.setenv("JVDFM_REAL_CASES_DIR", str(tmp_path))
    (tmp_path / "autos.pdf").write_bytes(b"%PDF")
    assert conftest_raiz.real_cases_dir() == tmp_path
    assert conftest_raiz.localizar_autos_reais("faltando.pdf", "autos.pdf") == tmp_path / "autos.pdf"
    with pytest.raises(pytest.skip.Exception):
        conftest_raiz.localizar_autos_reais("faltando.pdf")


def test_ambiente_de_teste_isolado_do_cofre():
    assert os.environ.get("JVDFM_SECRETS_BACKEND") == "arquivo"


# ----------------------------------------------------------------------------------------------
# Atalho
# ----------------------------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="atalho .lnk só no Windows")
def test_criar_atalho_windows_em_pasta_com_acentos(tmp_path):
    atalho = _carregar_modulo("jvdfm_criar_atalho", RAIZ / "scripts" / "criar_atalho_desktop.py")
    destino = tmp_path / "Área de Trabalho"
    destino.mkdir()
    criados = atalho.criar_atalho_windows(destino)
    assert criados and criados[0].exists() and criados[0].suffix == ".lnk"
    assert criados[0].parent == destino


def test_criar_atalho_sem_caminhos_fixos_de_usuario():
    texto = (RAIZ / "scripts" / "criar_atalho_desktop.py").read_text(encoding="utf-8")
    assert "C:\\\\Users" not in texto and "C:\\Users" not in texto and "/Users/" not in texto


# ----------------------------------------------------------------------------------------------
# Correções da revisão adversarial (pacote)
# ----------------------------------------------------------------------------------------------
def test_build_release_exclui_perfil_de_estilo_e_precedentes_do_gabinete():
    for rel in ("data/templates_gabinete/style_profile.json", "data/knowledge_base/custom_precedentes.json"):
        assert build_release.arquivo_excluido(rel)
        assert build_release.verificar_lista([rel]), rel
    plano = build_release.coletar(RAIZ)
    relativos = {i.relativo for i in plano.itens}
    assert "data/templates_gabinete/style_profile.json" not in relativos
    assert "data/knowledge_base/custom_precedentes.json" not in relativos


def test_build_release_normaliza_nfd_e_caixa():
    import unicodedata

    nfd = unicodedata.normalize("NFD", "Modelos de Sentenças/Sentenca_real.docx")
    assert nfd != "Modelos de Sentenças/Sentenca_real.docx"
    assert build_release.arquivo_excluido(nfd)
    assert build_release.verificar_lista([nfd])
    assert build_release.arquivo_excluido("DATA/Cases/x.txt")


@pytest.mark.parametrize(
    "rel",
    ["autos_processo.pdf", "data/cases_backup/autos.pdf", "extra/audiencia.mp4", "x/a.m4a", "x/a.WAV", "x/a.mkv",
     "outra/Sentenca.docx"],
)
def test_build_release_barra_autos_e_midias_fora_da_lista_branca(rel):
    assert build_release.arquivo_excluido(rel)
    assert build_release.verificar_lista([rel])


def test_build_release_timbre_continua_permitido():
    rel = "data/templates_gabinete/timbre_tjrn.docx"
    assert not build_release.arquivo_excluido(rel)
    assert build_release.verificar_lista([rel]) == []


def _ler_ps1() -> str:
    return (RAIZ / "instalador/bootstrap.ps1").read_text(encoding="utf-8-sig")


def _ler_sh() -> str:
    return (RAIZ / "instalador/bootstrap.sh").read_text(encoding="utf-8")


def test_bootstrap_remove_quarentena_no_macos():
    sh = _ler_sh()
    assert "xattr -dr com.apple.quarantine" in sh
    assert "xattr" in (RAIZ / "scripts/criar_atalho_desktop.py").read_text(encoding="utf-8")


def test_bootstrap_revisita_etapas_opcionais_e_valida_venv():
    ps1, sh = _ler_ps1(), _ler_sh()
    # etapas opcionais pendentes (Ollama ausente / atalho) no ramo "já instalado"
    assert "Configurar-Atalho $marca" in ps1 and "'ausente'" in ps1
    assert "configurar_atalho" in sh and '"ausente"' in sh
    # venv validado por import, não só pela existência do python.exe
    assert "import fastapi, uvicorn" in ps1 and "import fastapi, uvicorn" in sh
    # extras pendentes + repetição da mesma tentativa antes de reduzir
    for texto in (ps1, sh):
        assert "extras_pendentes" in texto and "tentativas_extras" in texto
    assert "-le 2" in ps1 and "-le 2" in sh


def test_bootstrap_arm64_tls_e_versao_do_uv():
    ps1, sh = _ler_ps1(), _ler_sh()
    assert "cpython-3.12-windows-x86_64-none" in ps1 and "windows-arm64-x64emu" in ps1
    # TLS 1.2 ajustado no PowerShell filho que baixa o uv
    assert "-bor 3072; irm https://astral.sh/uv/install.ps1" in ps1
    assert "UvVersaoMinima" in ps1 and "UV_VERSAO_MINIMA" in sh and "'self', 'update'" in ps1 and "self update" in sh
    pyproject = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    assert "required-version" in pyproject


def test_bat_nao_fecha_sem_mostrar_falha_do_powershell():
    bat = (RAIZ / "Iniciar SentencIA.bat").read_text(encoding="ascii")
    assert "pause" in bat and "-SemPerguntas" in bat  # o modo oculto do VBS não pode travar


def test_gitattributes_e_modos_executaveis():
    attrs = (RAIZ / ".gitattributes").read_text(encoding="utf-8")
    for regra in ("*.sh text eol=lf", "*.command text eol=lf", "*.bat text eol=crlf", "*.ps1 text eol=crlf",
                  "*.vbs text eol=crlf"):
        assert regra in attrs
    if shutil.which("git") and (RAIZ / ".git").exists():
        r = subprocess.run(["git", "ls-files", "-s", "--", "Iniciar SentencIA.command", "instalador/bootstrap.sh",
                            "SentencIA.app/Contents/MacOS/sentencia"],
                           cwd=RAIZ, capture_output=True, text=True)
        linhas = [l for l in r.stdout.splitlines() if l.strip()]
        assert len(linhas) == 3 and all(l.startswith("100755") for l in linhas), r.stdout


def test_build_release_exclui_git_worktree_arquivo_e_arquivos_orquestracao(tmp_path: Path):
    """Em git worktree, .git é um arquivo na raiz; .git, .agent/, .ai-collab/ e CLAUDE.md nunca entram no pacote."""
    projeto = tmp_path / "worktree_sentencia"
    _projeto_sintetico(projeto)
    (projeto / ".git").write_text("gitdir: C:/SentencIA/.git/worktrees/review\n", encoding="utf-8")
    (projeto / ".agent" / "rules").mkdir(parents=True, exist_ok=True)
    (projeto / ".agent" / "rules" / "executor.md").write_text("regras", encoding="utf-8")
    (projeto / ".ai-collab").mkdir(parents=True, exist_ok=True)
    (projeto / ".ai-collab" / "BOARD.md").write_text("board", encoding="utf-8")
    (projeto / "CLAUDE.md").write_text("claude", encoding="utf-8")

    plano = build_release.coletar(projeto)
    incluidos = {i.relativo for i in plano.itens}
    assert ".git" not in incluidos
    assert "CLAUDE.md" not in incluidos
    assert not any(r.startswith((".agent/", ".ai-collab/")) for r in incluidos)
    assert build_release.verificar_lista(list(incluidos)) == []
    assert build_release.verificar_lista([".git", "CLAUDE.md", ".agent/a.md", ".ai-collab/BOARD.md"])


def test_versao_unica_consistente_no_repositorio():
    """Versão em pyproject.toml bate com backend, launcher, instaladores, frontend e pacote."""
    from app.api import security

    versao = build_release.ler_versao(RAIZ)
    assert versao == "2.1.0"
    assert security.VERSAO_APP == versao
    assert security.ler_versao_app(RAIZ) == versao
    assert launcher.VERSAO_ESPERADA == versao

    ps1 = (RAIZ / "instalador/bootstrap.ps1").read_text(encoding="utf-8-sig")
    sh = (RAIZ / "instalador/bootstrap.sh").read_text(encoding="utf-8")
    assert f"$VersaoApp = '{versao}'" in ps1
    assert f'VERSAO_APP="{versao}"' in sh

    estado_js = (RAIZ / "app/static/js/modulos/estado.js").read_text(encoding="utf-8")
    assert f'APP_VERSION = "{versao}"' in estado_js

    html = (RAIZ / "app/static/index.html").read_text(encoding="utf-8")
    assert f'id="sidebarAuthorVersion">v{versao}<' in html
    assert f'id="aboutSentenciaVersion">v{versao}<' in html
    assert 'id="telemetryAppVersion"' in html and f"SentencIA v{versao}" in html
    assert f"/static/css/design-tokens.css?v={versao}" in html
    assert f"/static/js/app.js?v={versao}" in html

    plano = build_release.coletar(RAIZ)
    assert plano.versao == versao
    incluidos = {i.relativo for i in plano.itens}
    assert "docs/GUIA_DO_USUARIO.md" in incluidos
    assert not [a for a in plano.alertas if a.startswith(("PROIBIDO", "ESSENCIAL"))]
