"""Identidade e conferência dos arquivos que compõem um caso ativo."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def sha256_arquivo(caminho: Path, bloco: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for parte in iter(lambda: arquivo.read(bloco), b""):
            digest.update(parte)
    return digest.hexdigest()


def descrever_arquivo_caso(raiz: Path, caminho: Path, tipo: str) -> dict[str, str]:
    resolvido = caminho.resolve()
    if caminho.is_symlink() or resolvido.parent != raiz.resolve() or not resolvido.is_file():
        raise ValueError("O arquivo do manifesto precisa estar na pasta de autos.")
    digest = sha256_arquivo(resolvido)
    return {"arquivo": caminho.name, "id": f"{tipo}-{digest}", "sha256": digest}


def montar_manifesto_caso(
    documentos: list[dict[str, str]], midias: list[dict[str, str]]
) -> dict[str, Any]:
    if not documentos:
        raise ValueError("O manifesto precisa conter um PDF de autos.")
    identidade = ":".join(sorted({item["sha256"] for item in documentos}))
    return {
        "versao": 1,
        "id": hashlib.sha256(identidade.encode("ascii")).hexdigest(),
        "documentos": documentos,
        "midias": midias,
    }


def validar_manifesto_caso(raiz: Path, manifesto: Any) -> None:
    if not isinstance(manifesto, dict) or manifesto.get("versao") != 1:
        raise ValueError("Versão de manifesto inválida.")
    conferidos: dict[str, list[dict[str, str]]] = {}
    for chave, tipo in (("documentos", "PDF"), ("midias", "MIDIA")):
        entradas = manifesto.get(chave)
        if not isinstance(entradas, list):
            raise ValueError("Lista de arquivos do manifesto inválida.")
        arquivos = []
        for item in entradas:
            nome = item.get("arquivo") if isinstance(item, dict) else None
            if not isinstance(nome, str) or not nome or Path(nome).name != nome:
                raise ValueError("Nome de arquivo do manifesto inválido.")
            arquivos.append(descrever_arquivo_caso(raiz, raiz / nome, tipo))
        conferidos[chave] = arquivos
    if montar_manifesto_caso(conferidos["documentos"], conferidos["midias"]) != manifesto:
        raise ValueError("A identidade ou o conteúdo dos arquivos do manifesto mudou.")
