# -*- coding: utf-8 -*-
"""Testes da TASK-033: migração segura, instalador Windows (.ps1/.iss/.wsb) e Assistente de 1ª execução."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.security import token_headers
from app.core import assistente, config as core_config
from app.core.migracao_dados import MARCA_MIGRACAO, migrar_dados_legados
from app.main import app

RAIZ = Path(__file__).resolve().parent.parent


def _powershell_exe() -> Path | None:
    if sys.platform != "win32":
        return None
    ps = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    return ps if ps.is_file() else None


def test_instalar_ps1_ascii_sem_bom_para_irm_iex():
    """``irm .../instalar.ps1 | iex``: o PowerShell 5.1 decodifica o asset baixado sem honrar o BOM.

    Os bytes EF BB BF viravam "ï»¿" colados ao ``<#`` e o bloco de comentário era lido como código
    (falha relatada na 2.3.1). O instalador da linha única precisa ser ASCII puro, sem BOM.
    """
    dados = (RAIZ / "instalador/instalar.ps1").read_bytes()
    assert not dados.startswith(b"\xef\xbb\xbf"), "instalar.ps1 não pode ter BOM (quebra irm | iex)"
    dados.decode("ascii")  # mesma leitura com qualquer codificação que o irm escolha
    assert b"\n" not in dados.replace(b"\r\n", b""), "instalar.ps1 precisa de CRLF"


def test_instalar_ps1_analisa_sem_erros_como_texto_baixado():
    """Simula o ``iex``: analisa o texto (decodificado como o irm faz com application/octet-stream)."""
    ps = _powershell_exe()
    if ps is None:
        pytest.skip("Windows PowerShell indisponível")
    env = dict(os.environ, SENTENCIA_PS1_ALVO=str(RAIZ / "instalador/instalar.ps1"))
    cmd = (
        "$s=[Text.Encoding]::GetEncoding(28591).GetString([IO.File]::ReadAllBytes($env:SENTENCIA_PS1_ALVO));"
        "$t=$null; $e=$null; [void][System.Management.Automation.Language.Parser]::ParseInput($s, [ref]$t, [ref]$e);"
        "$e.Count"
    )
    r = subprocess.run([str(ps), "-NoProfile", "-NonInteractive", "-Command", cmd], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == "0", f"instalar.ps1 não analisa como texto baixado: {r.stdout}"


@pytest.mark.parametrize("rel", ["instalador/instalar.ps1", "instalador/desinstalar.ps1"])
def test_scripts_ps1_bom_crlf_e_sintaxe(rel: str):
    dados = (RAIZ / rel).read_bytes()
    if rel.endswith("desinstalar.ps1"):
        assert dados.startswith(b"\xef\xbb\xbf"), f"{rel} precisa de BOM UTF-8 (PowerShell 5.1)"
    assert b"\n" not in dados.replace(b"\r\n", b""), f"{rel} precisa de CRLF"

    ps = _powershell_exe()
    if ps is None:
        pytest.skip("Windows PowerShell indisponível")
    env = dict(os.environ, SENTENCIA_PS1_ALVO=str(RAIZ / rel))
    cmd = (
        "$t=$null; $e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
        "$env:SENTENCIA_PS1_ALVO, [ref]$t, [ref]$e); $e.Count"
    )
    r = subprocess.run([str(ps), "-NoProfile", "-NonInteractive", "-Command", cmd], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == "0", f"Erros de sintaxe em {rel}: {r.stdout}"


def test_inno_setup_iss_e_windows_sandbox_wsb():
    iss = (RAIZ / "instalador/windows/SentencIA.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in iss
    assert r"{localappdata}\Programs\SentencIA" in iss
    assert r"{localappdata}\SentencIA\data" in iss
    assert "SentencIA-Setup-" in iss
    assert "MB_DEFBUTTON1" in iss  # manter dados como opção padrão

    wsb_path = RAIZ / "instalador/testar-sandbox.wsb"
    root = ET.fromstring(wsb_path.read_text(encoding="utf-8"))
    assert root.tag == "Configuration"
    assert root.findtext(".//ReadOnly") == "true"
    assert "instalar.ps1" in (root.findtext(".//LogonCommand/Command") or "")


def test_migracao_segura_copia_verifica_sha256_e_preserva_original(tmp_path: Path):
    origem = tmp_path / "legado" / "data"
    destino = tmp_path / "localappdata" / "SentencIA" / "data"
    (origem / "cases").mkdir(parents=True, exist_ok=True)
    (origem / "logs").mkdir(parents=True, exist_ok=True)

    f1 = origem / "user_settings.json"
    f1.write_text('{"comarca_nome": "Comarca Ficticia"}', encoding="utf-8")
    f2 = origem / "cases" / "nota.txt"
    f2.write_text("conteudo ficticio de migracao", encoding="utf-8")
    (origem / "logs" / "app.log").write_text("log ignorado", encoding="utf-8")

    res = migrar_dados_legados(origem, destino)
    assert res["migrado"] is True
    assert res["copiados"] == 2
    assert res["verificados"] == 2
    assert (destino / MARCA_MIGRACAO).is_file()
    assert (destino / "user_settings.json").read_text(encoding="utf-8") == f1.read_text(encoding="utf-8")
    assert (destino / "cases" / "nota.txt").read_text(encoding="utf-8") == f2.read_text(encoding="utf-8")
    assert not (destino / "logs" / "app.log").exists()
    # Original NUNCA e apagado nem movido
    assert f1.is_file() and f2.is_file()

    # Segunda execucao nao sobrescreve arquivo ja alterado no destino
    (destino / "user_settings.json").write_text('{"comarca_nome": "Nova Comarca"}', encoding="utf-8")
    res2 = migrar_dados_legados(origem, destino)
    assert res2["copiados"] == 0
    assert res2["preservados_no_destino"] == 1
    assert "Nova Comarca" in (destino / "user_settings.json").read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="Teste do instalador PowerShell no Windows")
def test_caminho_a_instala_atualiza_preserva_dados_e_desinstala(tmp_path: Path):
    """Critérios de aceite 1 e 2: instala em pasta limpa, atualiza preservando dados e desinstala mantendo dados."""
    ps = _powershell_exe()
    if ps is None:
        pytest.skip("Windows PowerShell indisponível")

    # Monta pacote zip sintético com bootstrap mínimo (para validar o fluxo real do instalar.ps1)
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    zip_path = pkg_dir / "SentencIA-v2.1.0.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SentencIA-v2.1.0/pyproject.toml", '[project]\nname = "sentenca-jvdfm"\nversion = "2.1.0"\n')
        zf.writestr("SentencIA-v2.1.0/launcher.py", "print('ok')\n")
        zf.writestr(
            "SentencIA-v2.1.0/instalador/bootstrap.ps1",
            "param([switch]$SemIniciar, [switch]$SemPerguntas, [switch]$Nuvem)\n"
            "if (-not $env:SENTENCIA_DATA_DIR) { throw 'SENTENCIA_DATA_DIR ausente' }\n"
            "New-Item -ItemType Directory -Path $env:SENTENCIA_DATA_DIR -Force | Out-Null\n"
            "Set-Content -Path (Join-Path $env:SENTENCIA_DATA_DIR '.bootstrap_ok') -Value 'ok'\n",
        )
        zf.write(RAIZ / "instalador/desinstalar.ps1", "SentencIA-v2.1.0/instalador/desinstalar.ps1")

    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    sha_path = pkg_dir / "SHA256SUMS.txt"
    sha_path.write_text(f"{sha}  SentencIA-v2.1.0.zip\n", encoding="utf-8")

    destino = tmp_path / "Programs" / "SentencIA"
    dados = tmp_path / "LocalAppData" / "SentencIA" / "data"

    # 1) Instalação limpa
    r1 = subprocess.run(
        [
            str(ps), "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(RAIZ / "instalador/instalar.ps1"),
            "-PacoteLocal", str(zip_path), "-Sha256Local", str(sha_path),
            "-Destino", str(destino), "-Dados", str(dados),
            "-SemPerguntas", "-SemIniciar", "-SemAtalhos", "-SemRegistro",
        ],
        capture_output=True, text=True, timeout=90,
    )
    assert r1.returncode == 0, f"STDOUT:\n{r1.stdout}\nSTDERR:\n{r1.stderr}"
    assert (destino / "pyproject.toml").is_file()
    assert (dados / ".bootstrap_ok").is_file()
    assert (destino / ".pasta_dados").is_file(), "instalar.ps1 deve persistir .pasta_dados"
    assert Path((destino / ".pasta_dados").read_text(encoding="utf-8").strip()).resolve() == dados.resolve()

    # Segunda abertura SEM SENTENCIA_DATA_DIR resolve para a mesma pasta via .pasta_dados
    assert core_config._resolver_data_dir("", base_dir=destino, ambiente={}) == dados.resolve()

    # 2) Usuário grava dados -> reinstalar/atualizar PRESERVA os dados
    (dados / "user_settings.json").write_text('{"comarca_nome": "Preservada"}', encoding="utf-8")
    (dados / "cases").mkdir(parents=True, exist_ok=True)
    autos_user = dados / "cases" / "ficticio.txt"
    autos_user.write_text("nao apagar", encoding="utf-8")

    r2 = subprocess.run(
        [
            str(ps), "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(RAIZ / "instalador/instalar.ps1"),
            "-PacoteLocal", str(zip_path), "-Sha256Local", str(sha_path),
            "-Destino", str(destino), "-Dados", str(dados),
            "-SemPerguntas", "-SemIniciar", "-SemAtalhos", "-SemRegistro",
        ],
        capture_output=True, text=True, timeout=90,
    )
    assert r2.returncode == 0, r2.stderr
    assert "Preservada" in (dados / "user_settings.json").read_text(encoding="utf-8")
    assert autos_user.read_text(encoding="utf-8") == "nao apagar"

    # 3) SHA-256 adulterado deve falhar
    sha_ruim = pkg_dir / "SHA256_RUIM.txt"
    sha_ruim.write_text(f"{'0' * 64}  SentencIA-v2.1.0.zip\n", encoding="utf-8")
    r_bad = subprocess.run(
        [
            str(ps), "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(RAIZ / "instalador/instalar.ps1"),
            "-PacoteLocal", str(zip_path), "-Sha256Local", str(sha_ruim),
            "-Destino", str(destino), "-Dados", str(dados),
            "-SemPerguntas", "-SemIniciar", "-SemAtalhos", "-SemRegistro",
        ],
        capture_output=True, text=True, timeout=90,
    )
    assert r_bad.returncode != 0

    # 4) Desinstalação padrão MANTÉM os dados e NUNCA remove atalhos de outra instalação (ex.: o atalho real do usuário)
    import os as _os

    mesa_real = Path(_os.path.expanduser("~")) / "Desktop"
    try:
        out = subprocess.run(
            # Pasta da Área de Trabalho pode ter acento (pt-BR): força saída e leitura em UTF-8.
            [str(ps), "-NoProfile", "-Command",
             "[Console]::OutputEncoding = [Text.Encoding]::UTF8; [Environment]::GetFolderPath('Desktop')"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        ).stdout.strip()
        if out:
            mesa_real = Path(out)
    except (OSError, subprocess.SubprocessError):
        pass
    atalho_real = mesa_real / "SentencIA.lnk"
    atalho_real_existia = atalho_real.is_file()
    r_un = subprocess.run(
        [
            str(ps), "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(RAIZ / "instalador/desinstalar.ps1"),
            "-Destino", str(destino), "-Dados", str(dados), "-Sim",
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert r_un.returncode == 0, r_un.stderr
    assert not destino.exists()
    assert autos_user.is_file(), "Desinstalação padrão deve manter os dados do gabinete"
    if atalho_real_existia:
        assert atalho_real.is_file(), "O desinstalador não pode apagar o atalho de outra instalação do SentencIA"


def test_assistente_sugestao_modelo_e_rotas_api(monkeypatch: pytest.MonkeyPatch):
    assert assistente.sugerir_modelo_ollama(6144, plataforma="win32")["modelo"] == "gemma3:4b"
    assert assistente.sugerir_modelo_ollama(12288, plataforma="win32")["modelo"] == "gemma3:12b"
    assert assistente.sugerir_modelo_ollama(None, plataforma="win32")["modelo"] == "gemma4:e2b"
    assert assistente.sugerir_modelo_ollama(None, plataforma="darwin")["modelo"] == "gemma4:e2b"

    monkeypatch.setattr(assistente, "get_gpu_vram_status", lambda: {"gpu_available": True, "gpu_name": "GPU Teste", "vram_total_mb": 6144})
    monkeypatch.setattr(assistente, "ollama_disponivel", lambda _u: False)
    monkeypatch.setattr(assistente, "find_binary", lambda _n: None)
    monkeypatch.setattr(assistente, "detectar_ocr_pt_br", lambda plataforma="win32": {
        "plataforma": "windows", "motor_instalado": True, "idioma_pt_br": False, "disponivel": False,
        "comando_admin": assistente.COMANDO_OCR_WINDOWS_PT_BR, "passos": ["Passo 1"],
    })

    client = TestClient(app, headers=token_headers())
    r = client.get("/api/assistente/diagnostico")
    assert r.status_code == 200
    body = r.json()
    assert body["ollama"]["instalado"] is False
    assert body["ollama"]["modelo_sugerido"] == "gemma3:4b"
    assert body["ocr"]["disponivel"] is False
    assert "Language.OCR~~~pt-BR~0.0.1.0" in body["ocr"]["comando_admin"]

    # Nome de modelo inválido -> 422
    r_inv = client.post("/api/assistente/ollama/pull", json={"modelo": "modelo; rm -rf /"})
    assert r_inv.status_code == 422

    # Guia do usuário acessível em HTML
    r_guia = client.get("/guia-usuario")
    assert r_guia.status_code == 200
    assert "Guia do Usuário" in r_guia.text


def test_pasta_dados_persistida_e_precedencia_SENTENCIA_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ajuste v2 (bloqueante): .pasta_dados persiste fora do programa; SENTENCIA_DATA_DIR tem precedência."""
    import launcher
    from scripts import build_release

    prog = tmp_path / "Programs" / "SentencIA"
    dados_cfg = tmp_path / "SentencIA" / "data"
    dados_env = tmp_path / "OverrideEnv" / "data"
    prog.mkdir(parents=True, exist_ok=True)
    (prog / ".pasta_dados").write_text(f"{dados_cfg.resolve()}\n", encoding="utf-8")

    # (a) Sem SENTENCIA_DATA_DIR -> _resolver_data_dir e launcher.data_dir usam .pasta_dados
    assert core_config._resolver_data_dir("", base_dir=prog, ambiente={}) == dados_cfg.resolve()
    monkeypatch.delenv("SENTENCIA_DATA_DIR", raising=False)
    monkeypatch.setattr(launcher, "PROJECT_DIR", prog)
    assert launcher.data_dir().resolve() == dados_cfg.resolve()

    # (c) SENTENCIA_DATA_DIR continua com precedência sobre .pasta_dados
    assert core_config._resolver_data_dir(str(dados_env), base_dir=prog, ambiente={}) == dados_env.resolve()
    monkeypatch.setenv("SENTENCIA_DATA_DIR", str(dados_env))
    assert launcher.data_dir().resolve() == dados_env.resolve()

    # .pasta_dados nunca entra no pacote de distribuição
    assert build_release.arquivo_excluido(".pasta_dados") is True
