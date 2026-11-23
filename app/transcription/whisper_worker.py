# -*- coding: utf-8 -*-
"""Processo auxiliar que roda o Whisper (GPU) isolado do servidor.

O CUDA pode encerrar o processo de forma abrupta (``0xC0000409``) sem lançar exceção Python. Rodando aqui,
a queda só derruba este processo; o servidor lê o código de saída, tenta de novo ou usa a CPU.
Ao finalizar, libera o cache do modelo e força ``gc.collect()`` antes de encerrar o processo para
devolver a VRAM ao sistema imediatamente.

Uso interno: ``python -m app.transcription.whisper_worker <entrada.json> <saida.json>``.
"""

from __future__ import annotations

import gc
import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    entrada = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    from app.transcription import audio_transcriber as at

    try:
        opcoes = {chave: entrada[chave] for chave in ("beam_size", "hotwords", "batch_size") if entrada.get(chave) is not None}
        segmentos, duracao = at._executar_whisper(
            Path(entrada["media"]),
            entrada["modelo"],
            entrada["device"],
            entrada["compute_type"],
            entrada["prompt"],
            **opcoes,
        )
        Path(argv[2]).write_text(
            json.dumps({"segmentos": segmentos, "duracao": duracao}, ensure_ascii=False), encoding="utf-8"
        )
        return 0
    finally:
        at.descarregar_modelo_whisper()
        gc.collect()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
