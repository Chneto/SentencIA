"""Compara o Whisper em CUDA usando exclusivamente uma audiência gerada por TTS local.

Execute no ambiente da worktree. Os dois caminhos de modelo devem apontar para
snapshots locais de small e turbo. A comparação usa a mesma voz, áudio e GPU,
em processos separados. Informe a precisão real de cada política. O tempo inclui carga dos pesos e decodificação.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import importlib.util
import json
import multiprocessing as mp
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave

RAIZ = Path(__file__).resolve().parents[1]
NOMES = ("Tício Fictício", "Mévia Exemplar", "Caio Modelo")
SEM_JANELA = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _roteiro() -> str:
    abertura = (
        "Iniciamos agora uma simulação de audiência, criada apenas para verificar a transcrição. "
        "Primeiro descrevemos os procedimentos de teste, a posição do microfone e o ruído de fundo. "
        "A voz é sintética e o conteúdo não se refere a qualquer pessoa ou processo existente. "
    )
    dialogo = []
    for indice in range(1, 5):
        dialogo.extend((
            f"No trecho {indice}, Tício Fictício descreve o horário aproximado da chegada e a sequência do relato. "
            "A pessoa que faz a pergunta solicita esclarecimento sobre a duração da conversa.",
            "A depoente Mévia Exemplar explica que ouviu a primeira parte da conversa, mas não ouviu o encerramento. "
            "A pergunta seguinte aborda a distância do local onde as palavras foram proferidas.",
            "A testemunha Caio Modelo relata uma percepção diferente do volume e compara o momento com a passagem de um veículo. "
            "O registro desta resposta é encerrado antes do próximo trecho.",
        ))
    return "\n\n".join([abertura] * 4 + dialogo)


def _gerar_audio(pasta: Path) -> tuple[Path, float, Counter[str]]:
    if os.name != "nt":
        raise RuntimeError("O gerador desta aferição precisa de uma voz pt-BR em System.Speech no Windows.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("O ffmpeg precisa estar no PATH para gerar a amostra ruidosa.")
    roteiro = _roteiro()
    texto, voz, amostra = pasta / "roteiro.txt", pasta / "voz.wav", pasta / "amostra.wav"
    texto.write_text(roteiro, encoding="utf-8")
    comando = """
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$voz = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $pt = @($voz.GetInstalledVoices() | Where-Object {$_.VoiceInfo.Culture.Name -eq 'pt-BR' -and $_.Enabled})
    if (-not $pt) { throw 'Voz offline pt-BR não instalada' }
    $voz.SelectVoice($pt[0].VoiceInfo.Name)
    $voz.SetOutputToWaveFile($env:SIA_TTS_WAV)
    $voz.Speak([IO.File]::ReadAllText($env:SIA_TTS_TEXTO,[Text.Encoding]::UTF8))
} finally {
    $voz.Dispose()
}
"""
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    env = {
        **os.environ,
        "SIA_TTS_WAV": str(voz),
        "SIA_TTS_TEXTO": str(texto),
        "PSModulePath": str(powershell.parent / "Modules"),
    }
    codificado = base64.b64encode(comando.encode("utf-16-le")).decode("ascii")
    subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", codificado],
        env=env, check=True, timeout=120, capture_output=True, **SEM_JANELA,
    )
    subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(voz), "-f", "lavfi",
         "-i", "anoisesrc=color=pink:amplitude=0.01:seed=77", "-filter_complex",
         "[0:a][1:a]amix=inputs=2:duration=first:normalize=0", "-ac", "1", "-ar", "16000", str(amostra)],
        check=True, timeout=120, capture_output=True, **SEM_JANELA,
    )
    with wave.open(str(amostra), "rb") as arq:
        duracao = arq.getnframes() / arq.getframerate()
    return amostra, duracao, Counter({nome: roteiro.count(nome) for nome in NOMES})


def _vram() -> int:
    r = subprocess.run(
        ["nvidia-smi", "--id=0", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        check=True, timeout=3, capture_output=True, text=True, **SEM_JANELA,
    )
    return int(r.stdout.strip())


def _transcrever(fila, audio, modelo, precisao, base, duracao):
    if str(RAIZ) not in sys.path:
        sys.path.insert(0, str(RAIZ))
    try:
        from app.transcription import audio_transcriber as at

        motor = at
        if base is not None:
            spec = importlib.util.spec_from_file_location("benchmark_whisper_base", base)
            motor = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(motor)

        inicial = _vram()
        pico = [inicial]
        encerrar = threading.Event()

        def amostrar():
            while not encerrar.wait(0.2):
                try:
                    pico.append(_vram())
                except (OSError, ValueError, subprocess.SubprocessError):
                    break

        medidor = threading.Thread(target=amostrar, daemon=True)
        medidor.start()
        inicio = time.perf_counter()
        try:
            if base is not None:
                partes, _ = motor._executar_whisper(audio, modelo, "cuda", precisao, motor._prompt_forense(list(NOMES)))
                lote = 1
            else:
                lote = at._lote_cuda(modelo, precisao, False)
                with at._audio_para_whisper(audio, True) as filtrado:
                    if filtrado == audio:
                        raise RuntimeError("O FFmpeg não preparou a amostra do benchmark.")
                    partes, _ = at._executar_whisper(
                        filtrado, modelo, "cuda", precisao, at._prompt_forense(),
                        hotwords=at._hotwords_forenses(list(NOMES)), batch_size=lote,
                    )
        finally:
            encerrar.set()
            medidor.join()
        segundos = time.perf_counter() - inicio
        if not partes:
            raise RuntimeError("A versão medida não gerou segmentos.")
        apos_30 = " ".join(
            p["texto_literal"] for p in partes
            if sum(int(n) * 60 ** i for i, n in enumerate(reversed(p["timestamp"].split(" - ")[0].split(":")))) >= 30
        )
        texto = " ".join(apos_30.casefold().split())
        fila.put({
            "tempo_s": round(segundos, 2), "rtf": round(segundos / duracao, 4),
            "vram_pico_mib": max(pico), "vram_acima_do_inicio_mib": max(pico) - inicial,
            "lote": lote,
            "acerto_apos_30s": {
                nome: len(re.findall(rf"(?<!\w){re.escape(nome.casefold())}(?!\w)", texto)) for nome in NOMES
            },
        })
    except Exception as exc:
        fila.put({"erro": f"{type(exc).__name__}: {exc}"})


def _medir(audio, modelo, precisao, base, duracao):
    contexto = mp.get_context("spawn")
    fila = contexto.Queue()
    processo = contexto.Process(target=_transcrever, args=(fila, audio, modelo, precisao, base, duracao))
    processo.start()
    processo.join(600)
    if processo.is_alive():
        processo.terminate()
        processo.join()
        raise TimeoutError("A medição CUDA ultrapassou dez minutos.")
    if processo.exitcode:
        raise RuntimeError(f"O processo CUDA encerrou com código {processo.exitcode}.")
    resultado = fila.get(timeout=5)
    if "erro" in resultado:
        raise RuntimeError(resultado["erro"])
    return resultado


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Commit antes da TASK-077")
    parser.add_argument("--small", type=Path, required=True)
    parser.add_argument("--turbo", type=Path, required=True)
    parser.add_argument("--precisao", choices=("float16", "int8_float16"), default="float16")
    parser.add_argument("--precisao-turbo", choices=("float16", "int8_float16"))
    parser.add_argument("--pasta", type=Path, required=True, help="Pasta temporária de uma worktree da tarefa")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{7,40}", args.base):
        parser.error("Informe o hash do commit-base.")
    pasta = args.pasta.resolve()
    pasta.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=pasta, prefix="afericao-") as temp:
        raiz = Path(temp)
        os.environ["SENTENCIA_DATA_DIR"] = str(raiz / "dados_sinteticos")
        os.environ["TEMP"] = os.environ["TMP"] = str(raiz)
        base = raiz / "audio_transcriber_base.py"
        base.write_bytes(subprocess.check_output(
            ["git", "show", f"{args.base}:app/transcription/audio_transcriber.py"], cwd=RAIZ,
        ))
        audio, duracao, esperados = _gerar_audio(raiz)
        if not 180 <= duracao <= 300:
            raise RuntimeError(f"A duração sintética é {duracao:.1f} s; são exigidos 180–300 s.")
        print(f"Amostra sintética pronta ({duracao:.1f} s). Medindo small e turbo em CUDA.", file=sys.stderr, flush=True)
        antes = _medir(audio, str(args.small.resolve()), args.precisao, base, duracao)
        precisao_turbo = args.precisao_turbo or args.precisao
        depois = _medir(audio, str(args.turbo.resolve()), precisao_turbo, None, duracao)
        print(json.dumps({
            "duracao_audio_s": round(duracao, 2),
            "precisao_antes": args.precisao, "precisao_depois": precisao_turbo,
            "nomes_esperados_apos_30s": esperados, "antes": antes, "depois": depois,
        }, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
