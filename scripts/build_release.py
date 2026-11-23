# -*- coding: utf-8 -*-
"""Gera o pacote de distribuição do SentencIA (Windows + macOS).

Uso::

    uv run python scripts/build_release.py --dry-run      # só lista o que entraria no zip
    uv run python scripts/build_release.py                # gera dist/SentencIA-v<versão>.zip

Saída: ``dist/SentencIA-v<versão>.zip`` (pasta raiz ``SentencIA-v<versão>/`` dentro do zip)
e ``dist/SHA256SUMS.txt``. O zip também leva um ``SHA256SUMS.txt`` interno com o hash de cada arquivo.

Garantias:

- **Nada sigiloso entra no pacote**: autos e caches (``data/cases``), preferências
  (``data/user_settings.json``), cofre (``data/secrets``), logs, exportações, jobs, as amostras de
  sentenças do gabinete (``data/templates_gabinete/amostras``), ``Modelos de Sentenças`` (sentenças
  reais com nomes), capturas de tela (``docs/design``), testes e notas internas de desenvolvimento
  (citam partes de processos reais). Depois de montar a lista, uma verificação independente
  (:func:`verificar_lista`) recusa o pacote se algum desses caminhos aparecer.
- **Permissões Unix preservadas no macOS**: ``.command``, ``.sh`` e o executável do bundle ``.app``
  recebem modo ``0o755`` no ``external_attr`` (com ``create_system = 3``, Unix), os demais ``0o644``.
- **Terminações de linha corretas** independentemente do checkout: scripts de shell com LF; ``.bat``,
  ``.cmd``, ``.vbs`` e ``.ps1`` com CRLF; ``.ps1`` com BOM UTF-8 (o Windows PowerShell 5.1 lê arquivos
  sem BOM como ANSI e quebra com caracteres acentuados).
- **Sem caminhos pessoais**: textos com caminhos absolutos de perfil (``C:\\Users\\<nome>``,
  ``/Users/<nome>``) geram alerta; no modo normal o pacote é recusado (``--ignorar-alertas`` força).
"""

from __future__ import annotations

__author__ = "FChNeto"

import argparse
import fnmatch
import hashlib
import os
import re
import stat
import sys
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable, List, Optional, Sequence, Tuple

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - requires-python >= 3.11
    tomllib = None  # type: ignore[assignment]

PROJECT_DIR = Path(__file__).resolve().parent.parent
NOME_BASE = "SentencIA"

# Pastas excluídas pelo caminho relativo (POSIX) a partir da raiz do projeto.
PASTAS_EXCLUIDAS: Tuple[str, ...] = (
    ".venv",
    "python",  # interpretador Python local copiado para a instalação (não vai no pacote)
    ".git",
    ".github",
    ".idea",
    ".vscode",
    ".claude",
    ".agent",
    ".ai-collab",
    "dist",
    "build",
    "data/cases",
    "data/lote",
    "data/secrets",
    "data/logs",
    "data/exports",
    "data/jobs",
    "data/templates_gabinete/amostras",
    "Modelos de Sentenças",
    "docs/design",  # inclui docs/design/screenshots (podem conter dados de processos)
    "docs/v2",  # especificação interna de refatoração
)
# Pastas de testes: fora do pacote do usuário final (citam partes de casos reais); --incluir-testes.
PASTAS_TESTES: Tuple[str, ...] = ("tests",)
# Nomes de pasta excluídos em qualquer nível.
NOMES_PASTA_EXCLUIDOS: Tuple[str, ...] = (
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".upload_tmp",
    "node_modules",
    ".venv",
    "_arquivados",
)
# Arquivos excluídos pelo caminho relativo.
ARQUIVOS_EXCLUIDOS: Tuple[str, ...] = (
    ".git",  # em git worktree, .git é um arquivo na raiz (não pasta)
    ".pasta_dados",  # gravado pelo instalador na máquina do usuário
    "CLAUDE.md",
    "data/user_settings.json",
    "data/.instalado",
    "data/.servidor.json",
    "data/.ultimo_caso_real.json",
    "docs/memory.md",  # memória interna de desenvolvimento (cita processos reais)
    "docs/TEST_READY.md",
    # perfil de estilo e precedentes cadastrados pelo gabinete (citam cabeçalhos/partes de sentenças reais);
    # o app regenera o perfil padrão quando ausentes.
    "data/templates_gabinete/style_profile.json",
    "data/knowledge_base/custom_precedentes.json",
)
# Padrões de nome de arquivo excluídos em qualquer pasta.
PADROES_ARQUIVO_EXCLUIDOS: Tuple[str, ...] = (
    "*.pyc",
    "*.pyo",
    "*.zip",
    "*.lnk",
    "*.log",
    "*.log.*",
    "*.tmp",
    "*.parcial",
    ".launcher.lock",  # (não usar "*.lock": o uv.lock é essencial)
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "~$*",
    "*.pje_cache_v2.json",  # caches de segmentação dos autos (texto integral do processo)
    "*.transcript_v2.json",  # caches de transcrição das audiências
    "user_settings*.json",
    "secrets.json",
    ".teste_gravacao*",
    ".env",
    ".env.*",
)

# Prefixos que NUNCA podem aparecer no pacote (verificação independente da lista acima).
PROIBIDOS: Tuple[str, ...] = (
    ".venv/",
    ".git/",
    ".agent/",
    ".ai-collab/",
    "CLAUDE.md",
    "data/cases/",
    "data/lote/",
    "data/secrets/",
    "data/logs/",
    "data/exports/",
    "data/jobs/",
    "data/user_settings.json",
    "data/templates_gabinete/amostras/",
    "data/templates_gabinete/style_profile.json",
    "data/knowledge_base/custom_precedentes.json",
    "Modelos de Sentenças/",
    "docs/design/screenshots/",
    "dist/",
)

# Autos/mídias/documentos: só entram se estiverem na lista branca (qualquer outro vira exclusão/erro).
EXTENSOES_SIGILOSAS: Tuple[str, ...] = (".pdf", ".mp4", ".mp3", ".m4a", ".wav", ".ogg", ".mkv", ".docx")
ARQUIVOS_SIGILOSOS_PERMITIDOS: Tuple[str, ...] = (
    "data/templates_gabinete/timbre_tjrn.docx",
    "docs/INSTALACAO.pdf",
)

EXTENSOES_LF = {".sh", ".command"}
EXTENSOES_CRLF = {".bat", ".cmd", ".vbs", ".ps1"}
EXTENSOES_TEXTO = {
    ".py", ".pyw", ".md", ".txt", ".toml", ".json", ".html", ".css", ".js", ".sh", ".command",
    ".bat", ".cmd", ".vbs", ".ps1", ".plist", ".cfg", ".ini", ".yml", ".yaml", ".lock", ".gitignore",
}
BOM_UTF8 = b"\xef\xbb\xbf"

# Caminhos absolutos de perfil de usuário (vazamento de nome/estrutura da máquina do autor).
_RE_CAMINHO_PESSOAL = re.compile(
    r"(?i)(?:[a-z]:\\{1,2}users\\{1,2}(?!public\b|default\b|<)[^\\/\s\"'`<>]+|/Users/(?!Shared\b|<|\$)[A-Za-z0-9._-]+/)"
)


@dataclass
class ItemPacote:
    relativo: str  # caminho POSIX relativo à raiz do projeto
    origem: Path
    executavel: bool
    tamanho: int


@dataclass
class Plano:
    versao: str
    itens: List[ItemPacote] = field(default_factory=list)
    excluidos: List[str] = field(default_factory=list)
    alertas: List[str] = field(default_factory=list)

    @property
    def tamanho_total(self) -> int:
        return sum(i.tamanho for i in self.itens)


# ----------------------------------------------------------------------------------------------
# Regras
# ----------------------------------------------------------------------------------------------
def ler_versao(raiz: Path = PROJECT_DIR) -> str:
    texto = (raiz / "pyproject.toml").read_text(encoding="utf-8")
    if tomllib is not None:
        return str(tomllib.loads(texto)["project"]["version"])
    m = re.search(r'^version\s*=\s*"([^"]+)"', texto, re.M)  # pragma: no cover
    if not m:  # pragma: no cover
        raise SystemExit("versão não encontrada em pyproject.toml")
    return m.group(1)  # pragma: no cover


def _n(texto: str) -> str:
    """Normaliza para comparação: NFC (macOS/iCloud/unzip podem entregar NFD) e sem diferença de caixa."""
    return unicodedata.normalize("NFC", texto).casefold()


def _sob(rel: str, prefixo: str) -> bool:
    rel, prefixo = _n(rel), _n(prefixo)
    return rel == prefixo or rel.startswith(prefixo + "/")


def _sigiloso_nao_permitido(rel: str) -> bool:
    if _n(rel) in {_n(p) for p in ARQUIVOS_SIGILOSOS_PERMITIDOS}:
        return False
    return PurePosixPath(_n(rel)).suffix in EXTENSOES_SIGILOSAS


def pasta_excluida(rel: str, incluir_testes: bool = False) -> bool:
    nome = PurePosixPath(rel).name
    if _n(nome) in {_n(x) for x in NOMES_PASTA_EXCLUIDOS}:
        return True
    pastas = PASTAS_EXCLUIDAS if incluir_testes else PASTAS_EXCLUIDAS + PASTAS_TESTES
    return any(_sob(rel, p) for p in pastas)


def arquivo_excluido(rel: str, incluir_testes: bool = False) -> bool:
    partes = PurePosixPath(rel).parts
    for i in range(1, len(partes)):
        if pasta_excluida("/".join(partes[:i]), incluir_testes):
            return True
    if _n(rel) in {_n(a) for a in ARQUIVOS_EXCLUIDOS}:
        return True
    if _sigiloso_nao_permitido(rel):
        return True
    nome = _n(partes[-1])
    return any(fnmatch.fnmatchcase(nome, _n(p)) for p in PADROES_ARQUIVO_EXCLUIDOS)


def eh_executavel(rel: str) -> bool:
    caminho = PurePosixPath(rel)
    if caminho.suffix in EXTENSOES_LF:
        return True
    partes = caminho.parts
    # Executável de bundle macOS: <Nome>.app/Contents/MacOS/<arquivo>
    for i, parte in enumerate(partes[:-1]):
        if parte.endswith(".app") and partes[i + 1 : i + 3] == ("Contents", "MacOS") and len(partes) == i + 4:
            return True
    return False


def coletar(raiz: Path = PROJECT_DIR, incluir_testes: bool = False) -> Plano:
    plano = Plano(versao=ler_versao(raiz))
    for pasta_atual, subpastas, arquivos in os.walk(raiz):
        base = Path(pasta_atual)
        rel_base = base.relative_to(raiz).as_posix()
        rel_base = "" if rel_base == "." else rel_base
        mantidas = []
        for sp in sorted(subpastas):
            rel = f"{rel_base}/{sp}" if rel_base else sp
            if pasta_excluida(rel, incluir_testes) or (base / sp).is_symlink():
                plano.excluidos.append(rel + "/")
            else:
                mantidas.append(sp)
        subpastas[:] = mantidas
        for nome in sorted(arquivos):
            rel = f"{rel_base}/{nome}" if rel_base else nome
            origem = base / nome
            if arquivo_excluido(rel, incluir_testes) or origem.is_symlink():
                plano.excluidos.append(rel)
                continue
            plano.itens.append(ItemPacote(rel, origem, eh_executavel(rel), origem.stat().st_size))
    plano.itens.sort(key=lambda i: i.relativo)
    plano.alertas.extend(verificar_lista([i.relativo for i in plano.itens]))
    plano.alertas.extend(verificar_caminhos_pessoais(plano.itens))
    plano.alertas.extend(verificar_essenciais([i.relativo for i in plano.itens]))
    return plano


def verificar_lista(relativos: Iterable[str]) -> List[str]:
    """Checagem independente: nenhum caminho proibido pode estar na lista."""
    erros = []
    for rel in relativos:
        rel_n = _n(rel)
        for proibido in PROIBIDOS:
            p_n = _n(proibido)
            if rel_n == p_n.rstrip("/") or rel_n.startswith(p_n):
                erros.append(f"PROIBIDO no pacote: {rel}")
        if _sigiloso_nao_permitido(rel):
            erros.append(f"PROIBIDO no pacote (autos/mídia/documento fora da lista branca): {rel}")
        nome = PurePosixPath(rel_n).name
        if nome.endswith((".pje_cache_v2.json", ".transcript_v2.json")) or nome == "secrets.json":
            erros.append(f"PROIBIDO no pacote (cache/segredo): {rel}")
    return erros


ESSENCIAIS: Tuple[str, ...] = (
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "launcher.py",
    "Iniciar SentencIA.bat",
    "Iniciar SentencIA.command",
    "instalador/bootstrap.ps1",
    "instalador/bootstrap.sh",
    "SentencIA.app/Contents/Info.plist",
    "SentencIA.app/Contents/MacOS/sentencia",
    "app/main.py",
    "app/static/index.html",
)


def verificar_essenciais(relativos: Sequence[str]) -> List[str]:
    presentes = set(relativos)
    return [f"ESSENCIAL ausente: {e}" for e in ESSENCIAIS if e not in presentes]


def verificar_caminhos_pessoais(itens: Iterable[ItemPacote]) -> List[str]:
    alertas = []
    for item in itens:
        sufixo = PurePosixPath(item.relativo).suffix.lower()
        if sufixo not in EXTENSOES_TEXTO or item.tamanho > 5 * 1024 * 1024:
            continue
        try:
            texto = item.origem.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for n, linha in enumerate(texto.splitlines(), 1):
            if _RE_CAMINHO_PESSOAL.search(linha):
                alertas.append(f"CAMINHO PESSOAL em {item.relativo}:{n}")
                break
    return alertas


# ----------------------------------------------------------------------------------------------
# Conteúdo normalizado (terminações de linha / BOM)
# ----------------------------------------------------------------------------------------------
def conteudo_normalizado(item: ItemPacote) -> bytes:
    dados = item.origem.read_bytes()
    sufixo = PurePosixPath(item.relativo).suffix.lower()
    if sufixo in EXTENSOES_LF or item.executavel:
        return dados.replace(b"\r\n", b"\n")
    if sufixo in EXTENSOES_CRLF:
        dados = dados.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        if sufixo == ".ps1" and not dados.startswith(BOM_UTF8):
            dados = BOM_UTF8 + dados
        return dados
    return dados


def sha256_bytes(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def _zipinfo(nome: str, mtime: float, modo: int, diretorio: bool = False) -> zipfile.ZipInfo:
    data = time.localtime(max(mtime, 315532800))[:6]  # ZIP não aceita datas antes de 1980
    info = zipfile.ZipInfo(nome, date_time=data)
    info.create_system = 3  # Unix: o macOS/unzip passam a respeitar os bits de permissão
    if diretorio:
        info.external_attr = ((stat.S_IFDIR | modo) << 16) | 0x10
        info.compress_type = zipfile.ZIP_STORED
    else:
        info.external_attr = (stat.S_IFREG | modo) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
    return info


def gerar_zip(plano: Plano, destino_zip: Path, pasta_raiz: Optional[str] = None) -> Tuple[Path, str]:
    """Escreve o zip (de forma atômica) e devolve (caminho, sha256 do zip)."""
    pasta_raiz = pasta_raiz or f"{NOME_BASE}-v{plano.versao}"
    destino_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino_zip.with_name(destino_zip.name + ".parcial")
    somas: List[str] = []
    pastas: set = set()
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(_zipinfo(pasta_raiz + "/", time.time(), 0o755, diretorio=True), b"")
        for item in plano.itens:
            partes = PurePosixPath(item.relativo).parts
            for i in range(1, len(partes)):
                pasta = "/".join(partes[:i])
                if pasta not in pastas:
                    pastas.add(pasta)
                    zf.writestr(_zipinfo(f"{pasta_raiz}/{pasta}/", time.time(), 0o755, diretorio=True), b"")
            dados = conteudo_normalizado(item)
            modo = 0o755 if item.executavel else 0o644
            zf.writestr(_zipinfo(f"{pasta_raiz}/{item.relativo}", item.origem.stat().st_mtime, modo), dados)
            somas.append(f"{sha256_bytes(dados)}  {item.relativo}")
        indice = ("\n".join(somas) + "\n").encode("utf-8")
        zf.writestr(_zipinfo(f"{pasta_raiz}/SHA256SUMS.txt", time.time(), 0o644), indice)
    os.replace(tmp, destino_zip)
    h = hashlib.sha256()
    with open(destino_zip, "rb") as fh:
        for bloco in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(bloco)
    return destino_zip, h.hexdigest()


# ----------------------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------------------
def _fmt_tamanho(n: int) -> str:
    for unidade in ("B", "KB", "MB", "GB"):
        if n < 1024 or unidade == "GB":
            return f"{n:.0f} {unidade}" if unidade == "B" else f"{n:.1f} {unidade}"
        n /= 1024  # type: ignore[assignment]
    return f"{n} B"  # pragma: no cover


def main(argv: Optional[Sequence[str]] = None) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError, ValueError):
            # Fluxos redirecionados ou substituídos em testes podem não suportar reconfigure().
            continue
    parser = argparse.ArgumentParser(description="Gera o pacote de distribuição do SentencIA.")
    parser.add_argument("--dry-run", action="store_true", help="apenas lista o que entraria no zip")
    parser.add_argument("--saida", type=Path, default=PROJECT_DIR / "dist", help="pasta de saída (padrão: dist)")
    parser.add_argument("--incluir-testes", action="store_true", help="inclui tests/ (pacote de desenvolvimento)")
    parser.add_argument("--ignorar-alertas", action="store_true", help="gera mesmo com alertas de caminho pessoal")
    parser.add_argument("--resumo", action="store_true", help="no dry-run, não lista arquivo por arquivo")
    args = parser.parse_args(argv)

    plano = coletar(PROJECT_DIR, incluir_testes=args.incluir_testes)
    nome_zip = f"{NOME_BASE}-v{plano.versao}.zip"
    bloqueantes = [a for a in plano.alertas if a.startswith(("PROIBIDO", "ESSENCIAL"))]
    pessoais = [a for a in plano.alertas if a.startswith("CAMINHO PESSOAL")]

    if args.dry_run:
        print(f"Pacote: {nome_zip} (pasta raiz {NOME_BASE}-v{plano.versao}/)")
        if not args.resumo:
            print("\nEntraria no zip:")
            for item in plano.itens:
                marca = " [0755]" if item.executavel else ""
                print(f"  {item.relativo}{marca}  ({_fmt_tamanho(item.tamanho)})")
        print("\nExcluído (pastas e arquivos de topo da exclusão):")
        for rel in plano.excluidos:
            if rel.count("/") <= 2 or rel.endswith("/"):
                print(f"  - {rel}")
        print(f"\nTotal: {len(plano.itens)} arquivos, {_fmt_tamanho(plano.tamanho_total)} (antes da compressão)")
        for a in plano.alertas:
            print(f"ALERTA: {a}")
        if not plano.alertas:
            print("Verificação: nenhum arquivo proibido (autos, preferências, segredos, modelos, capturas) no pacote.")
        return 1 if bloqueantes else 0

    if bloqueantes or (pessoais and not args.ignorar_alertas):
        for a in plano.alertas:
            print(f"ERRO: {a}", file=sys.stderr)
        print("Pacote NÃO gerado. Corrija os itens acima (ou use --ignorar-alertas para caminhos pessoais).", file=sys.stderr)
        return 1

    destino, soma = gerar_zip(plano, args.saida / nome_zip)
    (args.saida / "SHA256SUMS.txt").write_text(f"{soma}  {nome_zip}\n", encoding="utf-8", newline="\n")
    print(f"Gerado: {destino} ({_fmt_tamanho(destino.stat().st_size)}, {len(plano.itens)} arquivos)")
    print(f"SHA-256: {soma}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
