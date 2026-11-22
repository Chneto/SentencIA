"""
Pasta de exportação escolhida pelo usuário.

A exportação continua sendo gravada em ``data/exports`` (e baixada pelo navegador). Quando o usuário
define uma pasta própria, o sistema também deposita ali uma CÓPIA, opcionalmente numa subpasta por
processo (``Proc. {nº}``). Regras de segurança:

* a pasta não pode estar dentro dos dados do programa nem do código (o reset da sessão apaga arquivos
  ``Sentença - ...`` das pastas de dados; uma pasta do usuário nunca é tocada pelo reset);
* nunca sobrescreve um arquivo existente com conteúdo diferente (o usuário pode tê-lo editado no Word):
  grava ``nome (2).ext``; se o conteúdo for idêntico, reaproveita o existente;
* falha na cópia nunca derruba a exportação.
"""

from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable, List, Tuple

from app.export.nomeacao import limpar_nome

_NUVEM = ("onedrive", "dropbox", "icloud", "google drive", "googledrive", "box sync", "mega", "pcloud", "nextcloud")


def _dentro_de(pasta: Path, base: Path) -> bool:
    try:
        pasta.relative_to(base)
        return True
    except ValueError:
        return False


def validar_pasta_exportacao(texto: str, bloqueadas: Iterable[Path]) -> Tuple[Path, List[str]]:
    """Valida e cria a pasta. Devolve ``(caminho_absoluto, avisos)``; levanta ``ValueError`` com a causa."""
    bruto = os.path.expandvars(os.path.expanduser(str(texto or "").strip().strip('"')))
    if not bruto:
        raise ValueError("Informe o caminho da pasta.")
    pasta = Path(bruto)
    if not pasta.is_absolute():
        raise ValueError("Use o caminho completo da pasta (ex.: C:\\Sentenças).")
    try:
        pasta = pasta.resolve()
    except OSError as exc:
        raise ValueError(f"Caminho inválido: {exc.strerror or exc}") from exc
    if pasta == Path(pasta.anchor):
        raise ValueError("Escolha uma pasta, não a raiz do disco.")
    for base in bloqueadas:
        try:
            b = Path(base).resolve()
        except OSError:
            continue
        if pasta == b or _dentro_de(pasta, b):
            raise ValueError("Essa pasta está dentro dos dados/código do programa; escolha outra, fora da pasta do sistema.")
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=pasta, prefix=".jvdfm_teste_", delete=True):
            pass
    except OSError as exc:
        raise ValueError(f"Não consigo gravar nessa pasta ({exc.strerror or type(exc).__name__}).") from exc
    avisos: List[str] = []
    if any(n in str(pasta).lower() for n in _NUVEM):
        avisos.append(
            "Essa pasta parece sincronizada com a nuvem (OneDrive, Dropbox, iCloud...): as sentenças exportadas "
            "serão enviadas ao provedor. Em processos sigilosos, prefira uma pasta apenas local."
        )
    return pasta, avisos


def _subpasta(numero_processo: str) -> str:
    return "Proc. " + (limpar_nome(numero_processo, 40) or "sem número")


_DOCX_METADADOS_TEMPORAIS = frozenset({"docProps/core.xml"})


def _arquivos_equivalentes(origem: Path, destino: Path) -> bool:
    """True se ``origem`` e ``destino`` têm o mesmo conteúdo (para ``.docx``, ignora timestamps do pacote ZIP e ``docProps/core.xml``)."""
    if filecmp.cmp(origem, destino, shallow=False):
        return True
    if origem.suffix.lower() != ".docx" or destino.suffix.lower() != ".docx":
        return False
    try:
        with zipfile.ZipFile(origem, "r") as za, zipfile.ZipFile(destino, "r") as zb:
            nomes_a = {n for n in za.namelist() if n not in _DOCX_METADADOS_TEMPORAIS}
            nomes_b = {n for n in zb.namelist() if n not in _DOCX_METADADOS_TEMPORAIS}
            if nomes_a != nomes_b:
                return False
            return all(za.read(n) == zb.read(n) for n in nomes_a)
    except (zipfile.BadZipFile, OSError):
        return False


def copiar_exportacao(origem: Path, pasta: str, numero_processo: str, por_processo: bool = True) -> Path:
    """Copia ``origem`` para a pasta do usuário sem sobrescrever conteúdo diferente. Levanta ``OSError``."""
    destino_dir = Path(pasta)
    if por_processo:
        destino_dir = destino_dir / _subpasta(numero_processo)
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / origem.name
    n = 2
    while destino.exists():
        if _arquivos_equivalentes(origem, destino):
            return destino  # já está lá, idêntico (mesmo se exportado em outro segundo)
        destino = destino_dir / f"{origem.stem} ({n}){origem.suffix}"
        n += 1
    fd, tmp = tempfile.mkstemp(prefix=".jvdfm_", suffix=origem.suffix, dir=destino_dir)
    os.close(fd)
    try:
        shutil.copyfile(origem, tmp)
        os.replace(tmp, destino)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return destino


def abrir_pasta(pasta: Path) -> None:
    """Abre a pasta no gerenciador de arquivos do sistema (somente caminhos já validados)."""
    if sys.platform == "win32":
        os.startfile(str(pasta))  # noqa: S606 - caminho validado pelo chamador
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(pasta)])  # noqa: S603,S607
    else:
        subprocess.Popen(["xdg-open", str(pasta)])  # noqa: S603,S607
