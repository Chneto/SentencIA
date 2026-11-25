"""TASK-101: subida de versão só nos pontos conhecidos.

Na 2.4.0 a troca em massa de "2.3.1" alterou a dependência ``iniconfig`` no ``uv.lock`` (lock inválido) e comentários
que citam a versão em que uma falha ocorreu. O script troca padrões conhecidos e apenas relata o restante.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def _modulo():
    spec = importlib.util.spec_from_file_location("sentencia_subir_versao", RAIZ / "scripts" / "subir_versao.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # @dataclass precisa do módulo registrado
    spec.loader.exec_module(mod)
    return mod


def _escrever(raiz: Path, rel: str, texto: str, bom: bool = False) -> None:
    destino = raiz / rel
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes((b"\xef\xbb\xbf" if bom else b"") + texto.encode("utf-8"))


def _arvore(raiz: Path, v: str) -> None:
    _escrever(raiz, "pyproject.toml", f'[project]\nname = "sentencia"\nversion = "{v}"\n')
    _escrever(
        raiz, "uv.lock",
        f'[[package]]\nname = "iniconfig"\nversion = "{v}"\nsdist = {{ url = "https://x/iniconfig-{v}.tar.gz" }}\n\n'
        f'[[package]]\nname = "sentencia"\nversion = "{v}"\nsource = {{ editable = "." }}\n',
    )
    _escrever(raiz, "instalador/bootstrap.ps1", f"$VersaoApp = '{v}'   # conferido\nWrite-Host 'Instalação'\n", bom=True)
    _escrever(raiz, "instalador/bootstrap.sh", f'VERSAO_APP="{v}"\n')
    _escrever(raiz, "instalador/instalar.ps1", f"function V {{\n    return '{v}'\n}}\n")
    _escrever(raiz, "instalador/instalar-mac.sh", f'  --versao <tag|latest>    versão (ex.: v{v} ou latest)\n    printf \'%s\' "${{v:-{v}}}"\n')
    _escrever(raiz, "launcher.py", f'def versao():\n    return "{v}"\n')
    _escrever(raiz, "instalador/windows/SentencIA.iss", f'; Compilar com: iscc /DMyAppVersion={v} (no CI)\n#define MyAppVersion "{v}"\n')
    _escrever(raiz, "SentencIA.app/Contents/Info.plist", f"<key>CFBundleShortVersionString</key>\n\t<string>{v}</string>\n\t<key>CFBundleVersion</key>\n\t<string>{v}</string>\n")
    _escrever(raiz, "app/static/js/modulos/estado.js", f'export const APP_VERSION = "{v}";\nimport x from "./dom.js?v={v}";\n')
    _escrever(raiz, "app/static/index.html", f'<script src="/static/js/app.js?v={v}"></script>\n<span id="sidebarAuthorVersion">v{v}</span>\n<span>SentencIA v{v}</span>\n')
    _escrever(raiz, "tests/e2e/test_algo.py", f'MOD = "/static/js/modulos/api.js?v={v}"\n# Regressão encontrada na CI da {v}: histórico\n')
    _escrever(raiz, "README.md", f"# SentencIA {v}\n\nBaixe `SentencIA-Setup-{v}.exe` ou `SentencIA-v{v}.zip`; `~/Documents/SentencIA-v{v}`.\n")
    _escrever(raiz, "docs/INSTALACAO.md", f"# Guia de Instalação — SentencIA {v} (Windows e macOS)\n`SentencIA-v{v}.zip`\n")
    _escrever(raiz, "docs/context.md", f"## Sistema Especialista de Sentenças Penais — JVDFM ({v})\n")


# Versões fictícias: com números reais (ex.: 2.4.0), a própria subida de versão do projeto trocava o ?v= deste teste.
def test_sobe_so_os_pontos_conhecidos_e_relata_o_resto(tmp_path):
    mod = _modulo()
    _arvore(tmp_path, "9.8.7")
    resultado = mod.subir_versao(tmp_path, "9.9.0")

    lock = (tmp_path / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "iniconfig"\nversion = "9.8.7"' in lock and "iniconfig-9.8.7.tar.gz" in lock
    assert 'name = "sentencia"\nversion = "9.9.0"' in lock
    assert 'version = "9.9.0"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    ps1 = (tmp_path / "instalador/bootstrap.ps1").read_bytes()
    assert ps1.startswith(b"\xef\xbb\xbf") and b"'9.9.0'" in ps1
    for rel in ("instalador/bootstrap.sh", "instalador/instalar.ps1", "instalador/instalar-mac.sh", "launcher.py",
                "instalador/windows/SentencIA.iss", "SentencIA.app/Contents/Info.plist", "app/static/index.html",
                "app/static/js/modulos/estado.js", "README.md", "docs/INSTALACAO.md", "docs/context.md"):
        assert "9.8.7" not in (tmp_path / rel).read_text(encoding="utf-8-sig"), rel
    teste = (tmp_path / "tests/e2e/test_algo.py").read_text(encoding="utf-8")
    assert "?v=9.9.0" in teste and "Regressão encontrada na CI da 9.8.7" in teste
    assert any("test_algo.py" in p for p in resultado.pendencias), "o histórico fica para revisão humana"
    assert not any("uv.lock" in p for p in resultado.pendencias), "dependências no lock não são pendência"


def test_simulacao_nao_grava(tmp_path):
    mod = _modulo()
    _arvore(tmp_path, "9.8.7")
    copia = tmp_path.parent / (tmp_path.name + "-orig")
    shutil.copytree(tmp_path, copia)
    resultado = mod.subir_versao(tmp_path, "9.9.0", gravar=False)
    assert resultado.alterados
    for arquivo in copia.rglob("*"):
        if arquivo.is_file():
            assert (tmp_path / arquivo.relative_to(copia)).read_bytes() == arquivo.read_bytes()


def test_recusa_versao_invalida_ou_igual(tmp_path):
    mod = _modulo()
    _arvore(tmp_path, "9.8.7")
    for nova in ("9.8", "v9.9.0", "9.8.7"):
        try:
            mod.subir_versao(tmp_path, nova)
        except ValueError:
            continue
        raise AssertionError(f"aceitou {nova!r}")


def test_repositorio_real_coerente_em_simulacao():
    mod = _modulo()
    atual = mod.versao_atual(RAIZ)
    resultado = mod.subir_versao(RAIZ, "99.0.0", gravar=False)
    assert "pyproject.toml" in resultado.alterados and "uv.lock" in resultado.alterados
    assert atual != "99.0.0"
