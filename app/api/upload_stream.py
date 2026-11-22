"""Recepcao de ``multipart/form-data`` em streaming REAL, com limite por arquivo.

O Starlette executa ``request.form()`` antes da rota e grava cada parte de arquivo inteira num
``SpooledTemporaryFile`` (em ``%TEMP%``) sem limite: o 413 so saia depois de o corpo inteiro ter
sido recebido e gravado (2x o espaco em disco). Aqui o corpo e lido de ``request.stream()`` e cada
arquivo e gravado direto no ``.part`` final; ao exceder o limite a leitura e abortada na hora.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from fastapi import HTTPException, Request

try:  # python-multipart >= 0.0.13
    from python_multipart.multipart import MultipartParser, parse_options_header
except ImportError:  # pragma: no cover - nome antigo do pacote
    from multipart.multipart import MultipartParser, parse_options_header  # type: ignore[no-redef]

_MAX_BYTES_CAMPO = 64 * 1024
_MAX_CAMPOS = 64


@dataclass
class ArquivoRecebido:
    filename_original: str
    ext: str
    tmp: Path
    total: int = 0


@dataclass
class _Estado:
    campos: Dict[str, str] = field(default_factory=dict)
    cabecalho_campo: bytearray = field(default_factory=bytearray)
    cabecalho_valor: bytearray = field(default_factory=bytearray)
    cabecalhos: Dict[str, bytes] = field(default_factory=dict)
    nome: str = ""
    arquivo: Optional[ArquivoRecebido] = None
    fh: Optional[object] = None
    buffer_campo: bytearray = field(default_factory=bytearray)
    limite: int = 0
    ignorar: bool = False


def _fechar(estado: _Estado) -> None:
    if estado.fh is not None:
        try:
            estado.fh.close()  # type: ignore[attr-defined]
        except OSError:
            pass
        estado.fh = None


async def receber_multipart(
    request: Request,
    tmp_dir: Path,
    *,
    validar_arquivo: Callable[[str, str], int],
    arquivos: List[ArquivoRecebido],
) -> Dict[str, str]:
    """Le o corpo multipart. Devolve os campos de texto; os arquivos entram em ``arquivos``.

    ``validar_arquivo(filename, ext)`` roda assim que o cabecalho da parte chega (antes de qualquer
    byte do arquivo): levanta ``HTTPException`` para recusar e devolve o limite em bytes.
    Excedido o limite, levanta 413 sem ler o restante. O chamador remove os ``.part`` em caso de erro.
    """
    tipo, opcoes = parse_options_header(request.headers.get("content-type", ""))
    if tipo.lower() != b"multipart/form-data" or b"boundary" not in opcoes:
        raise HTTPException(status_code=422, detail="Envie os arquivos como multipart/form-data.")

    estado = _Estado()

    def on_part_begin() -> None:
        estado.cabecalhos = {}
        estado.cabecalho_campo = bytearray()
        estado.cabecalho_valor = bytearray()
        estado.nome = ""
        estado.arquivo = None
        estado.buffer_campo = bytearray()
        estado.ignorar = False
        _fechar(estado)

    def on_header_field(data: bytes, start: int, end: int) -> None:
        estado.cabecalho_campo += data[start:end]

    def on_header_value(data: bytes, start: int, end: int) -> None:
        estado.cabecalho_valor += data[start:end]

    def on_header_end() -> None:
        estado.cabecalhos[bytes(estado.cabecalho_campo).decode("latin-1").lower()] = bytes(estado.cabecalho_valor)
        estado.cabecalho_campo = bytearray()
        estado.cabecalho_valor = bytearray()

    def on_headers_finished() -> None:
        _, params = parse_options_header(estado.cabecalhos.get("content-disposition", b""))
        estado.nome = params.get(b"name", b"").decode("utf-8", "replace")
        bruto = params.get(b"filename")
        if bruto is None:
            if len(estado.campos) >= _MAX_CAMPOS:
                raise HTTPException(status_code=413, detail="Campos de formulario demais.")
            return
        filename = bruto.decode("utf-8", "replace")
        if not filename:  # navegador sem arquivo selecionado
            estado.ignorar = True
            return
        ext = Path(filename).suffix.lower()
        estado.limite = int(validar_arquivo(filename, ext))
        tmp = tmp_dir / f"{uuid.uuid4().hex}{ext}.part"
        recebido = ArquivoRecebido(filename_original=filename, ext=ext, tmp=tmp)
        arquivos.append(recebido)  # registrado ANTES de abrir: o chamador limpa em caso de erro
        estado.arquivo = recebido
        estado.fh = open(tmp, "wb")

    def on_part_data(data: bytes, start: int, end: int) -> None:
        if estado.ignorar:
            return
        pedaco = data[start:end]
        if estado.arquivo is not None and estado.fh is not None:
            estado.arquivo.total += len(pedaco)
            if estado.arquivo.total > estado.limite:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"O arquivo '{estado.arquivo.filename_original}' excede o limite de "
                        f"{estado.limite // (1024 * 1024)} MB "
                        "(ajuste max_upload_mb_pdf/max_upload_mb_midia nas configurações)."
                    ),
                )
            estado.fh.write(pedaco)  # type: ignore[attr-defined]
        else:
            estado.buffer_campo += pedaco
            if len(estado.buffer_campo) > _MAX_BYTES_CAMPO:
                raise HTTPException(status_code=413, detail="Campo de formulario grande demais.")

    def on_part_end() -> None:
        if estado.arquivo is None and not estado.ignorar and estado.nome:
            estado.campos[estado.nome] = bytes(estado.buffer_campo).decode("utf-8", "replace")
        _fechar(estado)

    parser = MultipartParser(
        opcoes[b"boundary"],
        callbacks={
            "on_part_begin": on_part_begin,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_headers_finished": on_headers_finished,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
        },
    )
    try:
        async for chunk in request.stream():
            parser.write(chunk)
        parser.finalize()
    except HTTPException:
        raise
    except Exception as exc:
        if isinstance(exc, OSError):
            raise
        raise HTTPException(status_code=422, detail=f"Corpo multipart inválido: {exc}") from exc
    finally:
        _fechar(estado)
    return estado.campos
