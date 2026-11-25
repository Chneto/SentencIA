#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Exportador da árvore de publicação saneada do SentencIA (`scripts/publicacao.py`).

Monta a árvore limpa para o repositório GitHub com histórico novo a partir de uma lista
branca estrita de arquivos verificados:
  - `--listar`: lista os caminhos relativos (POSIX) que entram na árvore pública.
  - `--exportar <pasta>`: copia a lista branca para `<pasta>` (ex.: `C:\\SentencIA\\publicacao\\SentencIA`),
    gera `data/knowledge_base/acervo_completo.json` a partir do código (`ACERVO_OFICIAL`) e
    normaliza fins de linha (LF em `.sh`/`.command`/executável macOS; UTF-8 c/ BOM + CRLF em `.ps1`;
    CRLF em `.bat`/`.cmd`/`.vbs`).

Exclui sempre:
  - `data/` (exceto `data/knowledge_base/acervo_completo.json` gerado a partir do código)
  - `Modelos de Sentenças/`
  - `python/`, `.venv/`, `dist/`, `.ai-collab/`, `.agent/`, `.claude/`, `.git/`
  - `CLAUDE.md`, `*.lnk`, `*.zip`, `*.log`, `*.pje_cache_v2.json`, `*.transcript_v2.json`
  - `docs/memory.md`, `docs/TEST_READY.md`, `docs/agents.md`, `docs/v2/`, `docs/design/`
  - qualquer `.pdf`, `.docx` ou mídia audiovisual (`.mp4`, `.mp3`, `.m4a`, `.wav`, `.ogg`, `.mkv`)
"""

from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path
import shutil
import stat
import sys
from typing import List, Optional, Sequence, Set, Tuple
import unicodedata

RAIZ_PADRAO = Path(__file__).resolve().parent.parent

ARQUIVOS_RAIZ_PERMITIDOS: Set[str] = {
    ".gitattributes",
    ".gitignore",
    "CHANGELOG.md",
    "Iniciar SentencIA.bat",
    "Iniciar SentencIA.command",
    "Iniciar SentencIA.vbs",
    "Iniciar_SentencIA.vbs",
    "LICENSE",
    "README.md",
    "launcher.py",
    "launcher.pyw",
    "pyproject.toml",
    "uv.lock",
}

PASTAS_PERMITIDAS: Tuple[str, ...] = (
    "app",
    "instalador",
    "scripts",
    "SentencIA.app",
    ".github",
    "tests",
)

DOCS_PUBLICOS_PERMITIDOS: Tuple[str, ...] = (
    "docs/DESIGN.md",
    "docs/GUIA_DO_USUARIO.md",
    "docs/INSTALACAO.md",
    "docs/INSTALACAO.pdf",
    "docs/INSTALACAO_MAC.md",
    "docs/context.md",
    "docs/roadmap.md",
    "docs/pje/",
    "docs/identidade/",
)

ARQUIVOS_BINARIOS_PUBLICOS_PERMITIDOS: Set[str] = {
    "docs/INSTALACAO.pdf",
}

DOCS_EXCLUIDOS: Tuple[str, ...] = (
    "docs/memory.md",
    "docs/TEST_READY.md",
    "docs/agents.md",
    "docs/v2/",
    "docs/design/",
)

NOMES_PASTA_EXCLUIDOS: Set[str] = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".upload_tmp",
    "node_modules",
    ".venv",
    "venv",
    "ENV",
    ".git",
    ".agent",
    ".ai-collab",
    ".claude",
    "python",
    "dist",
    "build",
    "_arquivados",
    "Modelos de Sentenças",
}

PADROES_ARQUIVO_EXCLUIDOS: Tuple[str, ...] = (
    "*.pyc",
    "*.pyo",
    "*.zip",
    "*.lnk",
    "*.log",
    "*.log.*",
    "*.tmp",
    "*.parcial",
    ".launcher.lock",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "~$*",
    "*.pje_cache_v2.json",
    "*.transcript_v2.json",
    "user_settings*.json",
    "secrets.json",
    ".teste_gravacao*",
    ".env",
    ".env.*",
    "*.pdf",
    "*.docx",
    "*.mp4",
    "*.mp3",
    "*.m4a",
    "*.wav",
    "*.ogg",
    "*.mkv",
)

EXECUTAVEIS_UNIX: Set[str] = {
    "Iniciar SentencIA.command",
    "instalador/bootstrap.sh",
    "instalador/instalar-mac.sh",
    "instalador/desinstalar-mac.sh",
    "SentencIA.app/Contents/MacOS/sentencia",
}

EXTENSOES_LF: Set[str] = {".sh", ".command"}
EXTENSOES_CRLF: Set[str] = {".bat", ".cmd", ".vbs", ".ps1"}
BOM_UTF8 = b"\xef\xbb\xbf"
#: .ps1 executados por ``irm ... | iex``: o PowerShell 5.1 não honra o BOM no texto baixado e o leria como
#: caracteres soltos antes do ``<#``. Ficam sem BOM (são ASCII puro).
PS1_SEM_BOM: Set[str] = {"instalador/instalar.ps1"}
ACERVO_REL = "data/knowledge_base/acervo_completo.json"


def _normalizar_rel(rel: str) -> str:
    limpo = unicodedata.normalize("NFC", rel.replace("\\", "/")).lstrip("/")
    while "//" in limpo:
        limpo = limpo.replace("//", "/")
    return limpo


def arquivo_na_lista_branca(rel: str) -> bool:
    """Indica se um caminho relativo (POSIX) pertence à lista branca de publicação."""
    rel_norm = _normalizar_rel(rel)
    if not rel_norm:
        return False

    partes = rel_norm.split("/")
    nomes_excluidos_lower = {unicodedata.normalize("NFC", p).lower() for p in NOMES_PASTA_EXCLUIDOS}
    if any(p.lower() in nomes_excluidos_lower for p in partes[:-1]):
        return False

    nome = partes[-1]
    nome_lower = nome.lower()
    if nome_lower in nomes_excluidos_lower:
        return False
    if rel_norm not in ARQUIVOS_BINARIOS_PUBLICOS_PERMITIDOS:
        for padrao in PADROES_ARQUIVO_EXCLUIDOS:
            if fnmatch.fnmatchcase(nome_lower, padrao.lower()):
                return False

    # 1. Acervo oficial gerado a partir do código
    if rel_norm == ACERVO_REL:
        return True

    # Qualquer outro arquivo em data/ é sempre excluído
    if partes[0].lower() == "data":
        return False

    # 2. Arquivos na raiz
    if len(partes) == 1:
        return rel_norm in ARQUIVOS_RAIZ_PERMITIDOS

    # 3. Documentação pública em docs/
    if partes[0] == "docs":
        for exc in DOCS_EXCLUIDOS:
            exc_norm = exc.rstrip("/")
            if rel_norm == exc_norm or rel_norm.startswith(exc_norm + "/"):
                return False
        for perm in DOCS_PUBLICOS_PERMITIDOS:
            if perm.endswith("/"):
                if rel_norm.startswith(perm):
                    return True
            elif rel_norm == perm:
                return True
        return False

    # 4. Pastas permitidas da lista branca
    if partes[0] in PASTAS_PERMITIDAS:
        return True

    return False


def listar_arquivos_publicacao(raiz: Path = RAIZ_PADRAO) -> List[str]:
    """Devolve a lista ordenada de caminhos relativos (POSIX) que entram na publicação."""
    raiz = Path(raiz).resolve()
    selecionados: Set[str] = set()

    for caminho in raiz.rglob("*"):
        if caminho.is_dir():
            continue
        rel_partes = caminho.relative_to(raiz).parts
        if any(p in NOMES_PASTA_EXCLUIDOS for p in rel_partes[:-1]):
            continue
        rel_posix = _normalizar_rel(caminho.relative_to(raiz).as_posix())
        if arquivo_na_lista_branca(rel_posix):
            selecionados.add(rel_posix)

    # O acervo oficial é sempre gerado a partir do código na exportação
    selecionados.add(ACERVO_REL)
    return sorted(selecionados)


def _normalizar_conteudo(caminho_origem: Path, rel_posix: str) -> bytes:
    dados = caminho_origem.read_bytes()
    ext = caminho_origem.suffix.lower()
    eh_exec_unix = rel_posix in EXECUTAVEIS_UNIX
    if ext not in EXTENSOES_LF and ext not in EXTENSOES_CRLF and not eh_exec_unix:
        return dados

    sem_bom = dados[len(BOM_UTF8) :] if dados.startswith(BOM_UTF8) else dados
    try:
        texto = sem_bom.decode("utf-8")
    except UnicodeDecodeError:
        return dados

    texto_lf = texto.replace("\r\n", "\n").replace("\r", "\n")
    if ext in EXTENSOES_LF or eh_exec_unix:
        return texto_lf.encode("utf-8")
    texto_crlf = texto_lf.replace("\n", "\r\n")
    if ext == ".ps1" and rel_posix not in PS1_SEM_BOM:
        return BOM_UTF8 + texto_crlf.encode("utf-8")
    return texto_crlf.encode("utf-8")


def _gerar_acervo_oficial_json(raiz: Path) -> bytes:
    """Gera o conteúdo de `data/knowledge_base/acervo_completo.json` direto de `ACERVO_OFICIAL`."""
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))
    from app.knowledge.knowledge_base import ACERVO_OFICIAL

    conteudo = json.dumps(ACERVO_OFICIAL, ensure_ascii=False, indent=2) + "\n"
    return conteudo.encode("utf-8")


def _limpar_destino_exportacao(destino: Path) -> None:
    """Remove arquivos e pastas antigos do destino (preservando `.venv` se existir para evitar reinstalação)."""
    if not destino.exists():
        return
    for item in list(destino.iterdir()):
        if item.name == ".venv":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


def exportar_arvore_publicacao(
    destino: Path,
    *,
    raiz: Path = RAIZ_PADRAO,
    limpar_destino: bool = True,
) -> List[str]:
    """Exporta a árvore de publicação para ``destino`` e devolve a lista de arquivos exportados."""
    raiz = Path(raiz).resolve()
    destino = Path(destino).resolve()
    if destino == raiz or raiz in destino.parents:
        raise ValueError(f"A pasta de exportação ({destino}) deve ficar fora do repositório ({raiz}).")

    destino.mkdir(parents=True, exist_ok=True)
    if limpar_destino:
        _limpar_destino_exportacao(destino)

    arquivos = listar_arquivos_publicacao(raiz)
    for rel_posix in arquivos:
        alvo = destino / Path(rel_posix)
        alvo.parent.mkdir(parents=True, exist_ok=True)
        if rel_posix == ACERVO_REL:
            try:
                alvo.write_bytes(_gerar_acervo_oficial_json(raiz))
            except Exception:
                origem_acervo = raiz / Path(rel_posix)
                if origem_acervo.is_file():
                    alvo.write_bytes(origem_acervo.read_bytes())
                else:
                    raise
        else:
            origem = raiz / Path(rel_posix)
            alvo.write_bytes(_normalizar_conteudo(origem, rel_posix))

        if rel_posix in EXECUTAVEIS_UNIX or alvo.suffix.lower() in EXTENSOES_LF:
            try:
                modo = alvo.stat().st_mode
                alvo.chmod(modo | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except OSError:
                pass

    return arquivos


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lista ou exporta a árvore de publicação saneada do SentencIA."
    )
    parser.add_argument(
        "--raiz",
        default=str(RAIZ_PADRAO),
        help="Raiz do repositório de origem (padrão: raiz onde o script está).",
    )
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument(
        "--listar",
        action="store_true",
        help="Lista os arquivos da lista branca que entram no repositório público.",
    )
    grupo.add_argument(
        "--exportar",
        metavar="PASTA",
        help="Exporta a árvore saneada para a pasta indicada (fora do repositório).",
    )
    args = parser.parse_args(argv)

    raiz = Path(args.raiz).resolve()
    if not raiz.is_dir():
        print(f"ERRO: raiz inexistente: {raiz}", file=sys.stderr)
        return 2

    if args.listar:
        arquivos = listar_arquivos_publicacao(raiz)
        for rel in arquivos:
            print(rel)
        return 0

    destino = Path(args.exportar).resolve()
    try:
        arquivos = exportar_arvore_publicacao(destino, raiz=raiz)
    except ValueError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

    print(f"Árvore de publicação exportada para {destino} ({len(arquivos)} arquivos).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
