"""Sobe a versão do SentencIA só nos pontos conhecidos (TASK-101).

Uso: ``uv run python scripts/subir_versao.py 2.4.1 [--simular]``

Por que não trocar em massa: na 2.4.0, substituir "2.3.1" em todos os arquivos alterou a dependência ``iniconfig``
no ``uv.lock`` (lock inválido) e comentários que registram em que versão uma falha ocorreu. Aqui cada ponto tem um
padrão próprio; ocorrências restantes da versão antiga são só relatadas para revisão. O BOM dos arquivos é mantido.
Depois: regenerar ``docs/INSTALACAO.pdf`` (``scripts/gerar_pdf_instalacao.py``) e escrever o CHANGELOG.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

RAIZ_PADRAO = Path(__file__).resolve().parent.parent
BOM = b"\xef\xbb\xbf"
VERSAO_RE = re.compile(r"^\d+\.\d+\.\d+$")

Regra = Callable[[str, str, str], str]


def _sub(padrao: str) -> Regra:
    """Troca a versão antiga só dentro das ocorrências de ``padrao`` (``{v}`` marca o lugar da versão)."""

    def aplicar(texto: str, antiga: str, nova: str) -> str:
        regex = re.compile(padrao.replace("{v}", re.escape(antiga)))
        return regex.sub(lambda m: m.group(0).replace(antiga, nova), texto)

    return aplicar


# Pontos conhecidos: arquivo (ou glob) -> padrões. Os literais espelham tests/test_versao_coerente.py.
PONTOS: dict[str, list[Regra]] = {
    "pyproject.toml": [_sub(r'(?m)^version = "{v}"')],
    "uv.lock": [_sub(r'name = "sentencia"\r?\nversion = "{v}"')],
    "instalador/bootstrap.ps1": [_sub(r"\$VersaoApp\s*=\s*'{v}'")],
    "instalador/bootstrap.sh": [_sub(r'VERSAO_APP="{v}"')],
    "instalador/instalar.ps1": [_sub(r"return\s+'{v}'")],
    "instalador/instalar-mac.sh": [_sub(r"\$\{v:-{v}\}"), _sub(r"ex\.: v{v} ")],
    "launcher.py": [_sub(r'return\s+"{v}"')],
    "instalador/windows/SentencIA.iss": [_sub(r'#define\s+MyAppVersion\s+"{v}"'), _sub(r"/DMyAppVersion={v} ")],
    "SentencIA.app/Contents/Info.plist": [_sub(r"<string>{v}</string>")],
    "app/static/js/modulos/estado.js": [_sub(r'APP_VERSION = "{v}"')],
    "app/static/index.html": [_sub(r">v{v}<"), _sub(r"SentencIA v{v}\b")],
    "README.md": [_sub(r"(?m)^# SentencIA {v}$"), _sub(r"SentencIA-Setup-{v}\.exe"), _sub(r"SentencIA-v{v}\b")],
    "docs/INSTALACAO.md": [_sub(r"SentencIA {v}\b"), _sub(r"SentencIA-Setup-{v}\.exe"), _sub(r"SentencIA-v{v}\b")],
    "docs/INSTALACAO_MAC.md": [_sub(r"SentencIA-v{v}\b")],
    "docs/GUIA_DO_USUARIO.md": [_sub(r"SentencIA {v}\b")],
    "docs/context.md": [_sub(r"JVDFM \({v}\)")],
}
# Cache-bust dos módulos e folhas de estilo (inclusive nos testes que os importam).
CACHE_BUST_GLOBS = ["app/static/**/*.js", "app/static/**/*.html", "app/static/**/*.css", "tests/**/*.py"]
CACHE_BUST = _sub(r"\?v={v}\b")
# Onde procurar sobras da versão antiga para revisão humana.
RELATORIO_GLOBS = ["app/**/*", "tests/**/*", "instalador/**/*", "scripts/**/*", "docs/**/*.md", "*.md", "*.py",
                   ".github/**/*"]
IGNORAR_RELATORIO = {"CHANGELOG.md"}


@dataclass
class Resultado:
    alterados: list[str] = field(default_factory=list)
    pendencias: list[str] = field(default_factory=list)


def versao_atual(raiz: Path) -> str:
    m = re.search(r'(?m)^version = "([^"]+)"', (raiz / "pyproject.toml").read_text(encoding="utf-8"))
    if not m:
        raise ValueError("versão ausente no pyproject.toml")
    return m.group(1)


def _ler(caminho: Path) -> tuple[bool, str]:
    dados = caminho.read_bytes()
    bom = dados.startswith(BOM)
    return bom, dados[len(BOM):].decode("utf-8") if bom else dados.decode("utf-8")


def _aplicar(caminho: Path, regras: list[Regra], antiga: str, nova: str, gravar: bool) -> bool:
    bom, texto = _ler(caminho)
    novo = texto
    for regra in regras:
        novo = regra(novo, antiga, nova)
    if novo == texto:
        return False
    if gravar:
        caminho.write_bytes((BOM if bom else b"") + novo.encode("utf-8"))
    return True


def subir_versao(raiz: Path, nova: str, *, gravar: bool = True) -> Resultado:
    """Troca a versão nos pontos conhecidos; com ``gravar=False`` só informa o que mudaria."""
    if not VERSAO_RE.match(nova):
        raise ValueError(f"versão inválida: {nova!r} (use X.Y.Z, sem 'v')")
    antiga = versao_atual(raiz)
    if nova == antiga:
        raise ValueError(f"a versão já é {antiga}")
    resultado = Resultado()
    for rel, regras in PONTOS.items():
        caminho = raiz / rel
        if caminho.is_file() and _aplicar(caminho, regras, antiga, nova, gravar):
            resultado.alterados.append(rel)
    vistos = set(resultado.alterados)
    for padrao in CACHE_BUST_GLOBS:
        for caminho in sorted(raiz.glob(padrao)):
            rel = caminho.relative_to(raiz).as_posix()
            if caminho.is_file() and _aplicar(caminho, [CACHE_BUST], antiga, nova, gravar) and rel not in vistos:
                vistos.add(rel)
                resultado.alterados.append(rel)
    if gravar:
        resultado.pendencias = _sobras(raiz, antiga)
    return resultado


def _sobras(raiz: Path, antiga: str) -> list[str]:
    alvo = re.compile(rf"(?<![\d.]){re.escape(antiga)}(?![\d])")
    sobras = []
    for padrao in RELATORIO_GLOBS:
        for caminho in sorted(raiz.glob(padrao)):
            rel = caminho.relative_to(raiz).as_posix()
            if not caminho.is_file() or rel in IGNORAR_RELATORIO or ".venv" in caminho.parts:
                continue
            try:
                _, texto = _ler(caminho)
            except (UnicodeDecodeError, OSError):
                continue
            for numero, linha in enumerate(texto.splitlines(), 1):
                if alvo.search(linha):
                    sobras.append(f"{rel}:{numero}: {linha.strip()[:120]}")
    return sorted(set(sobras))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("versao", help="nova versão X.Y.Z")
    parser.add_argument("--simular", action="store_true", help="mostra o que mudaria sem gravar")
    parser.add_argument("--raiz", default=str(RAIZ_PADRAO))
    args = parser.parse_args(argv)
    raiz = Path(args.raiz).resolve()
    try:
        antiga = versao_atual(raiz)
        resultado = subir_versao(raiz, args.versao, gravar=not args.simular)
    except ValueError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    acao = "mudaria" if args.simular else "alterados"
    print(f"{antiga} -> {args.versao}: {len(resultado.alterados)} arquivo(s) {acao}.")
    for rel in resultado.alterados:
        print(f"  {rel}")
    if resultado.pendencias:
        print("\nOcorrências restantes da versão antiga (revise; não foram trocadas):")
        for linha in resultado.pendencias:
            print(f"  {linha}")
    if not args.simular:
        print("\nPróximos passos: scripts/gerar_pdf_instalacao.py, CHANGELOG e suíte completa.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
