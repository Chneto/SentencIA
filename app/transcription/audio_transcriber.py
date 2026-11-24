"""Transcrição de mídias de audiência (vídeo/áudio) — local primeiro, honesta sempre.

Ordem dos motores:
1. **faster-whisper local** (modelo e dispositivo de ``settings.whisper_model``/``whisper_device``;
   CPU ``int8`` no macOS e quando não há CUDA). O modelo fica em cache (singleton por
   modelo/dispositivo) e não é recarregado a cada arquivo.
2. **Motores externos** (Gemini multimodal, depois Google Web Speech via ``speech_recognition``)
   SOMENTE com ``settings.permitir_envio_externo == True`` **e** ``consentimento_envio_externo=True``
   na chamada (segredo de justiça / LGPD).

Nenhuma transcrição é inventada: se nenhum motor funcionar, o retorno tem ``status="FALHOU"``,
``segmentos=[]`` e ``erro`` explicando o motivo.

IDs de mídia estáveis: ``ID-MIDIA-`` + 8 hex do sha256 do CONTEÚDO (+ ``-NNN`` quando o arquivo
é parte de uma gravação dividida). Não dependem de arquivos vizinhos nem do diretório.

Cache: ``<arquivo>.transcript_v2.json`` (nome mantido por compatibilidade com o reset), validado
pelo sha256 e pela versão do esquema. Caches antigos (validados só por tamanho) são migrados apenas
quando o tamanho confere, o cache é posterior à última modificação da mídia e contém segmentos reais;
caso contrário, são ignorados.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import gc
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.platform_utils import IS_MACOS, find_binary, subprocess_no_window_kwargs
from app.core.text_utils import remover_acentos
from app.core.vram_manager import (
    avaliar_whisper_vram,
    get_gpu_vram_status,
    release_ollama_vram_for_whisper,
)

logger = logging.getLogger(__name__)

TRANSCRICAO_SCHEMA = 3
TRANSCRICAO_CACHE_SUFFIX = ".transcript_v2.json"
STATUS_OK = "OK"
STATUS_FALHOU = "FALHOU"

PAPEIS_VALIDOS = (
    "OFENDIDA",
    "TESTEMUNHA_POLICIAL",
    "TESTEMUNHA_ACUSACAO",
    "TESTEMUNHA_DEFESA",
    "INTERROGATORIO_REU",
    "ALEGACOES_FINAIS_MP",
    "ALEGACOES_FINAIS_DEFESA",
    "INDETERMINADO",
)

_ENGINES_FICTICIOS_LEGADOS = {"Local Audio Pipeline"}
_GEMINI_MODELO_TRANSCRICAO = "auto"


# ----------------------------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------------------------
def _find_binary(name: str) -> Optional[str]:
    """Compatibilidade: delega a ``platform_utils.find_binary`` (``None`` quando ausente)."""
    return find_binary(name)


def sha256_arquivo(caminho: Path, bloco: int = 1024 * 1024) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(caminho, "rb") as fh:
            for parte in iter(lambda: fh.read(bloco), b""):
                h.update(parte)
        return h.hexdigest()
    except OSError:
        return None


def rotulo_log_arquivo(nome: str) -> str:
    """Rótulo anônimo (hash curto) para logs: nomes de mídia/PDF podem conter o nome do depoente."""
    return hashlib.sha256(str(nome).encode("utf-8", "replace")).hexdigest()[:8]


def _fmt_mmss(segundos: float) -> str:
    total = max(0, int(segundos or 0))
    m, s = divmod(total, 60)
    return f"{m:02d}:{s:02d}"


def _gravar_json_atomico(destino: Path, dados: Any) -> bool:
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{destino.name}.", suffix=".tmp", dir=str(destino.parent))
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(dados, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, destino)
        tmp_name = None
        return True
    except Exception as err:
        logger.warning("Falha ao gravar cache de transcrição %s: %s", destino.name, type(err).__name__)
        return False
    finally:
        if tmp_name and os.path.exists(tmp_name):
            with contextlib.suppress(OSError):
                os.remove(tmp_name)


# ----------------------------------------------------------------------------------------------
# Identificação do depoente, do papel e do ID da mídia
# ----------------------------------------------------------------------------------------------
_RE_PARTE = re.compile(r"[_-](\d{3})$")


def _texto_tokens(texto: str) -> Tuple[str, set]:
    """(texto normalizado com espaços simples, conjunto de tokens) — sem acento, MAIÚSCULO."""
    norm = re.sub(r"[^A-Z0-9]+", " ", remover_acentos(texto or "").upper()).strip()
    return norm, set(norm.split())


def _tem_frase(norm: str, frase: str) -> bool:
    return f" {frase} " in f" {norm} "


def _inferir_papel(stem: str, depoente_input: str, papel_input: str) -> str:
    norm, tokens = _texto_tokens(stem)
    if (
        _tem_frase(norm, "ALEGACOES FINAIS")
        or _tem_frase(norm, "MEMORIAIS DO MP")
        or tokens & {"PROMOTOR", "PROMOTORA", "PROMOTORIA"}
    ):
        return "ALEGACOES_FINAIS_MP"
    if tokens & {"VITIMA", "OFENDIDA", "DECLARANTE"}:
        return "OFENDIDA"
    if tokens & {"POLICIAL", "PM", "PMS", "CB", "SGT", "SD", "AGENTE", "SARGENTO", "CABO", "SOLDADO", "GCM"}:
        return "TESTEMUNHA_POLICIAL"
    if "DEFESA" in tokens or any(t.startswith("ABONAT") for t in tokens):
        return "TESTEMUNHA_DEFESA"
    if tokens & {"INTERROGATORIO", "REU", "ACUSADO", "DENUNCIADO"}:
        return "INTERROGATORIO_REU"
    papel = (papel_input or "").strip().upper()
    dep_norm, dep_tokens = _texto_tokens(depoente_input)
    if "OFENDIDA" in dep_tokens and "TESTEMUNHA" not in dep_tokens:
        return "OFENDIDA"
    if "TESTEMUNHA" in tokens and papel in ("", "OFENDIDA", "DEPOENTE"):
        # Arquivo nomeado como testemunha sem indicação de polícia/defesa: não é a ofendida.
        return "TESTEMUNHA_ACUSACAO"
    if papel in PAPEIS_VALIDOS:
        return papel
    return "INDETERMINADO"


def _gerar_media_id(filename: str, sha256_conteudo: Optional[str], part_num: str) -> str:
    if sha256_conteudo:
        base = f"ID-MIDIA-{sha256_conteudo[:8].upper()}"
        return f"{base}-{part_num}" if part_num else base
    # Sem acesso ao conteúdo (chamadas utilitárias/testes): ID determinístico pelo nome.
    if part_num:
        return f"ID-MIDIA-{part_num}"
    return f"ID-MIDIA-{hashlib.sha256(filename.encode('utf-8')).hexdigest()[:8].upper()}"


def _infer_speaker_and_role(
    filename: str,
    depoente_input: str,
    papel_input: str,
    known_ofendida: str = "",
    known_reu: str = "",
    sha256_conteudo: Optional[str] = None,
) -> tuple[str, str, str]:
    """
    Infere (media_id, nome_depoente, papel) a partir do nome do arquivo PJe Mídias e dos metadados
    do processo ativo. O papel é inferido por TOKENS inteiros (``PM`` não casa dentro de palavras).
    Com ``sha256_conteudo`` o ID é ``ID-MIDIA-<8 hex do sha256>`` (+ ``-NNN`` da parte).
    """
    stem = Path(filename).stem
    m_part = _RE_PARTE.search(stem)
    part_num = m_part.group(1) if m_part else ""
    media_id = _gerar_media_id(Path(filename).name, sha256_conteudo, part_num)

    papel = _inferir_papel(stem, depoente_input, papel_input)

    depoente_clean = (depoente_input or "").strip()
    is_generic_input = depoente_clean.upper() in ("", "DEPOENTE", "OFENDIDA / TESTEMUNHA", "OFENDIDA", "TESTEMUNHA")

    if not is_generic_input:
        nome_final = depoente_clean
    elif papel == "ALEGACOES_FINAIS_MP":
        nome_final = "MINISTÉRIO PÚBLICO ESTADUAL (Alegações Finais Orais)"
    elif papel == "OFENDIDA" and known_ofendida:
        nome_final = known_ofendida
    elif papel == "INTERROGATORIO_REU" and known_reu:
        nome_final = known_reu
    else:
        m_name = re.search(
            r"(?:Depoimento\s+(?:da\s+V[íi]tima|da\s+Ofendida|da\s+Testemunha|de)|Interrogat[óo]rio(?:[_\s]+do[_\s]+R[ée]u|[_\s]+do[_\s]+Acusado|[_\s]+de)?|V[íi]tima|Ofendida|Testemunha)[_\s]+([A-Za-zÀ-ÿ0-9º°\.\s]+?)(?:[_-]\d{2,3}|$)",
            stem,
            flags=re.IGNORECASE,
        )
        if m_name:
            extracted = re.sub(
                r"^(?:DA\s+TESTEMUNHA|DA\s+V[ÍI]TIMA|DO\s+ACUSADO|DO\s+R[ÉE]U)\s+",
                "",
                m_name.group(1).strip(),
                flags=re.IGNORECASE,
            ).upper()
            if known_ofendida and extracted and extracted in known_ofendida.upper():
                nome_final = known_ofendida
            elif known_reu and extracted and extracted in known_reu.upper():
                nome_final = known_reu
            else:
                nome_final = extracted or stem
        else:
            nome_final = known_ofendida if papel == "OFENDIDA" and known_ofendida else stem

    if part_num:
        part_label = f" (Parte {int(part_num):02d})"
        if part_label.upper() not in nome_final.upper():
            nome_final = f"{nome_final}{part_label}"

    return media_id, nome_final, papel


# ----------------------------------------------------------------------------------------------
# Duração
# ----------------------------------------------------------------------------------------------
def _duracao_midia_segundos(media_path: Path) -> Optional[float]:
    """Duração real (PyAV, depois ffprobe). ``None`` quando não for possível medir."""
    try:
        import av  # type: ignore[import-not-found]

        with av.open(str(media_path)) as cont:
            if cont.duration:
                return float(cont.duration) / float(getattr(av, "time_base", 1_000_000) or 1_000_000)
    except Exception as err:
        logger.debug("PyAV indisponível ou falhou ao medir duração de %s: %s", media_path.name, err)
    ffprobe = find_binary("ffprobe")
    if not ffprobe:
        return None
    try:
        r = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(media_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            **subprocess_no_window_kwargs(),
        )
        valor = float(str(r.stdout).strip())
        return valor if valor > 0 else None
    except Exception:
        return None


# ----------------------------------------------------------------------------------------------
# Motor 1: faster-whisper local
# ----------------------------------------------------------------------------------------------
_WHISPER_CACHE: Dict[tuple, Any] = {}
_WHISPER_CACHE_LOCK = threading.Lock()
_WHISPER_USO_LOCK = threading.Lock()


_CUDA_FALHOU = False  # uma falha de CUDA nesta execução desativa a GPU até reiniciar o programa


def _registrar_dlls_cuda_pip() -> None:
    """Registra as DLLs do CUDA instaladas por pip (pacotes ``nvidia-cublas-cu12``/``nvidia-cudnn-cu12``)."""
    if os.name != "nt":
        return
    try:
        import importlib.util

        spec = importlib.util.find_spec("nvidia")
        raizes = list(getattr(spec, "submodule_search_locations", []) or []) if spec else []
        for raiz in raizes:
            for bin_dir in Path(raiz).glob("*/bin"):
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        logger.debug("não foi possível registrar as DLLs CUDA do pip", exc_info=True)


def _bibliotecas_cuda_carregam() -> bool:
    """No Windows, confere se o cuBLAS do CUDA 12 carrega (sem ele o faster-whisper falha ou trava)."""
    if os.name != "nt":
        return True
    _registrar_dlls_cuda_pip()
    try:
        import ctypes

        ctypes.WinDLL("cublas64_12.dll")
        return True
    except OSError:
        return False


def _cuda_disponivel() -> bool:
    if IS_MACOS or _CUDA_FALHOU:
        return False
    try:
        import ctranslate2  # type: ignore[import-not-found]

        return int(ctranslate2.get_cuda_device_count()) > 0 and _bibliotecas_cuda_carregam()
    except Exception:
        return False


def _resolver_dispositivo_whisper(preferencia: str) -> Tuple[str, str]:
    """(device, compute_type) conforme a configuração e o hardware."""
    pref = (preferencia or "auto").strip().lower()
    if IS_MACOS or pref == "cpu" or _CUDA_FALHOU:
        return "cpu", "int8"
    if pref == "cuda":
        return "cuda", "int8_float16"
    return ("cuda", "int8_float16") if _cuda_disponivel() else ("cpu", "int8")


def _preparar_modelo_turbo() -> str:
    """Localiza o turbo em cache ou publica o aviso e o progresso do download."""
    from faster_whisper.utils import download_model
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError
    from tqdm.auto import tqdm

    try:
        local = download_model("turbo", local_files_only=True)
    except LocalEntryNotFoundError:
        local = ""
    if local and all((Path(local) / nome).is_file() for nome in ("model.bin", "tokenizer.json", "config.json")):
        return local

    from app.api.jobs import PROGRESSO

    estado = PROGRESSO.obter()
    logger.warning("Whisper turbo: iniciando download público do modelo (aprox. 1,6 GB).")
    PROGRESSO.atualizar(phase="baixando_modelo_whisper", etapa="Baixando Whisper turbo (aprox. 1,6 GB)")

    class ProgressoTurbo(tqdm):
        def update(self, quantidade=1):
            resultado = super().update(quantidade)
            if self.total and estado.get("active"):
                PROGRESSO.atualizar(etapa=f"Baixando Whisper turbo ({int(self.n)}/{int(self.total)} arquivos)")
            return resultado

    try:
        return snapshot_download(
            "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
            allow_patterns=["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"],
            token=False,
            tqdm_class=ProgressoTurbo,
        )
    finally:
        PROGRESSO.atualizar(phase=estado.get("phase"), etapa=estado.get("etapa"))


def _obter_modelo_whisper(modelo: str, device: str, compute_type: str) -> Any:
    """Modelo faster-whisper em cache (um por vez, para não acumular memória)."""
    import faster_whisper  # type: ignore[import-not-found]

    if device == "cuda":
        _registrar_dlls_cuda_pip()
    chave = (id(faster_whisper.WhisperModel), modelo, device, compute_type)
    with _WHISPER_CACHE_LOCK:
        existente = _WHISPER_CACHE.get(chave)
        if existente is not None:
            return existente
        _WHISPER_CACHE.clear()
        gc.collect()
        novo = faster_whisper.WhisperModel(modelo, device=device, compute_type=compute_type)
        _WHISPER_CACHE[chave] = novo
        return novo


def descarregar_modelo_whisper() -> None:
    """Libera o modelo Whisper em cache (ex.: antes de gerar a sentença com o Ollama)."""
    with _WHISPER_CACHE_LOCK:
        _WHISPER_CACHE.clear()
    gc.collect()


def _prompt_forense() -> str:
    return "Audiência judicial em português."


def _hotwords_forenses(nomes: List[str]) -> str:
    """Prioriza nomes inteiros e limita o contexto por bytes antes da tokenização."""
    palavras = list(dict.fromkeys(" ".join(nome.split()) for nome in nomes if nome))
    palavras.extend(("medida protetiva", "ameaça", "lesão corporal", "Ministério Público", "Defensoria Pública"))
    cabem: List[str] = []
    for palavra in palavras:
        if not palavra or palavra.casefold() == "não identificado":
            continue
        if len(", ".join([*cabem, palavra]).encode("utf-8")) <= 174:
            cabem.append(palavra)
    return ", ".join(cabem)


def _compatibilizar_pyav() -> None:
    """Contorna a incompatibilidade faster-whisper x PyAV 19.

    O faster-whisper 1.2.x abre a mídia com ``av.open(..., metadata_errors="ignore")``, argumento que o
    PyAV 19 removeu (``TypeError: open() got an unexpected keyword argument 'metadata_errors'``).
    Se o ``av.open`` instalado não aceitar o argumento, ele é descartado — o comportamento padrão do
    PyAV já tolera metadados inválidos. Idempotente e inofensivo com versões antigas do PyAV.
    """
    try:
        import av  # type: ignore[import-not-found]
    except Exception:
        return
    original = getattr(av, "open", None)
    if original is None or getattr(original, "_sentencia_compat", False):
        return
    try:
        original_open = original

        def _open_compativel(*args: Any, **kwargs: Any) -> Any:
            try:
                return original_open(*args, **kwargs)
            except TypeError as err:
                if "metadata_errors" in kwargs and "metadata_errors" in str(err):
                    kwargs.pop("metadata_errors", None)
                    return original_open(*args, **kwargs)
                raise

        _open_compativel._sentencia_compat = True  # type: ignore[attr-defined]
        av.open = _open_compativel
    except Exception:  # nunca impede a transcrição
        logger.debug("não foi possível compatibilizar o PyAV", exc_info=True)


_ISOLAR_CUDA = True  # roda o Whisper em GPU num processo à parte (testes desligam)


def _executar_whisper_isolado(
    media_path: Path,
    modelo: str,
    device: str,
    compute_type: str,
    prompt: str,
    tentativas: int = 5,
    *,
    beam_size: Optional[int] = None,
    hotwords: Optional[str] = None,
    batch_size: Optional[int] = None,
) -> Tuple[List[Dict[str, str]], Optional[float]]:
    """Whisper em subprocesso: uma queda nativa do CUDA não derruba o servidor; tenta de novo."""
    import json
    import subprocess
    import sys
    import time

    ultimo = ""
    for n in range(1, tentativas + 1):
        with tempfile.TemporaryDirectory(prefix="sia_whisper_") as tmp:
            entrada, saida = Path(tmp) / "in.json", Path(tmp) / "out.json"
            payload_in: Dict[str, Any] = {
                "media": str(media_path),
                "modelo": modelo,
                "device": device,
                "compute_type": compute_type,
                "prompt": prompt,
            }
            if batch_size is not None:
                payload_in["batch_size"] = batch_size
            if hotwords:
                payload_in["hotwords"] = hotwords
            if beam_size is not None:
                payload_in["beam_size"] = int(beam_size)
            entrada.write_text(json.dumps(payload_in), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "app.transcription.whisper_worker", str(entrada), str(saida)],
                cwd=str(Path(__file__).resolve().parent.parent.parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3 * 3600,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.returncode == 0 and saida.exists():
                dados = json.loads(saida.read_text(encoding="utf-8"))
                return dados.get("segmentos") or [], dados.get("duracao")
            ultimo = f"código {proc.returncode}: {(proc.stderr or '').strip()[-300:]}"
            logger.warning("Whisper (GPU) encerrou de forma anormal na tentativa %s/%s: %s", n, tentativas, ultimo)
            time.sleep(8.0)  # dá tempo de a GPU liberar o contexto do processo que caiu
    raise RuntimeError(f"Whisper em GPU falhou {tentativas} vezes ({ultimo})")


class _Trecho:
    """Trecho (frase) com texto e tempos, no formato lido por ``_executar_whisper``."""

    def __init__(self, text: str, start: float, end: float) -> None:
        self.text, self.start, self.end = text, start, end


def _dividir_em_frases(segments: Any, max_seg: float = 18.0) -> Any:
    """Quebra cada segmento do Whisper em frases usando o tempo de cada palavra.

    Sem ``words`` (outros motores, testes) o segmento passa inalterado.
    """
    for s in segments:
        palavras = list(getattr(s, "words", None) or [])
        if not palavras:
            yield s
            continue
        grupo: List[Any] = []
        for w in palavras:
            grupo.append(w)
            txt = str(getattr(w, "word", "") or "").strip()
            dur = float(getattr(w, "end", 0) or 0) - float(getattr(grupo[0], "start", 0) or 0)
            if (txt.endswith((".", "?", "!")) and len(grupo) >= 3) or dur >= max_seg:
                yield _Trecho("".join(str(getattr(x, "word", "")) for x in grupo).strip(), grupo[0].start, grupo[-1].end)
                grupo = []
        if grupo:
            yield _Trecho("".join(str(getattr(x, "word", "")) for x in grupo).strip(), grupo[0].start, grupo[-1].end)


def _executar_whisper(
    media_path: Path,
    modelo: str,
    device: str,
    compute_type: str,
    prompt: str,
    *,
    beam_size: Optional[int] = None,
    hotwords: Optional[str] = None,
    batch_size: Optional[int] = None,
) -> Tuple[List[Dict[str, str]], Optional[float]]:
    _compatibilizar_pyav()
    if device == "cuda" and batch_size is None:
        batch_size = _lote_cuda(modelo, compute_type, beam_size is not None)
    model = _obter_modelo_whisper(modelo, device, compute_type)
    transcribe_kwargs: Dict[str, Any] = {
        "language": "pt",
        "vad_filter": True,
        "initial_prompt": prompt,
        "word_timestamps": True,  # permite citar por frase (modelos grandes devolvem trechos de ~30 s)
        "condition_on_previous_text": False,  # evita laços de repetição ("E foi quando aconteceu...")
    }
    if hotwords:
        transcribe_kwargs["hotwords"] = hotwords
    if beam_size is not None:
        transcribe_kwargs["beam_size"] = int(beam_size)
    if device == "cuda" and batch_size is not None and batch_size > 1:
        from faster_whisper import BatchedInferencePipeline

        pipeline = BatchedInferencePipeline(model=model)
        segments, info = pipeline.transcribe(str(media_path), batch_size=batch_size, **transcribe_kwargs)
    else:
        segments, info = model.transcribe(str(media_path), **transcribe_kwargs)
    seg_list: List[Dict[str, str]] = []
    ultimo_norm = ""
    for s in _dividir_em_frases(segments):  # o gerador decodifica sob demanda: erros de CUDA surgem aqui
        texto = str(getattr(s, "text", "") or "").strip()
        if not texto:
            continue
        norm = re.sub(r"\W+", " ", texto.lower()).strip()
        if norm and norm == ultimo_norm:
            continue  # repetição idêntica seguida (alucinação típica do Whisper)
        ultimo_norm = norm
        seg_list.append(
            {
                "timestamp": f"{_fmt_mmss(getattr(s, 'start', 0))} - {_fmt_mmss(getattr(s, 'end', 0))}",
                "texto_literal": texto,
            }
        )
    duracao = getattr(info, "duration", None)
    try:
        duracao = float(duracao) if duracao else None
    except (TypeError, ValueError):
        duracao = None
    return seg_list, duracao


def _lote_cuda(modelo: str, compute_type: str, moderado: bool) -> int:
    """Mede a folga para o lote antes de carregar os pesos no processo CUDA."""
    from app.core.vram_manager import requisitos_whisper_mb

    livre = get_gpu_vram_status().get("vram_free_mb")
    if not isinstance(livre, (int, float)):
        return 1
    # O modelo público pode estar em um snapshot de cache cujo último componente é um hash.
    referencia = next((parte for parte in Path(modelo).parts if parte.startswith("models--")), modelo)
    _, req_fp16, req_int8 = requisitos_whisper_mb(referencia)
    folga = livre - (req_fp16 if compute_type == "float16" else req_int8)
    if folga >= 2048 and not moderado:
        return 8
    return 4 if folga >= 768 else 1


@contextlib.contextmanager
def _audio_para_whisper(media_path: Path, ativado: bool):
    """Prepara uma cópia transitória; a original permanece disponível para recuo."""
    if not ativado:
        yield media_path
        return
    ffmpeg = find_binary("ffmpeg")
    if not ffmpeg:
        logger.info("Whisper [%s]: ffmpeg ausente; usando o áudio original.", rotulo_log_arquivo(media_path.name))
        yield media_path
        return
    with tempfile.TemporaryDirectory(prefix="sia_audio_") as tmp:
        destino = Path(tmp) / "audio.wav"
        try:
            subprocess.run(
                [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(media_path),
                 "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
                 "-af", "highpass=f=80,dynaudnorm=f=250:g=15:p=0.85", "-c:a", "pcm_s16le", str(destino)],
                check=True, capture_output=True, timeout=1800, **subprocess_no_window_kwargs(),
            )
            if not destino.is_file() or destino.stat().st_size <= 44:
                raise OSError("o filtro não gerou áudio")
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Whisper [%s]: filtro indisponível (%s); usando áudio original.",
                           rotulo_log_arquivo(media_path.name), type(exc).__name__)
            yield media_path
        else:
            yield destino


def _transcrever_whisper_local(
    media_path: Path, settings: Any, prompt: str, *, hotwords: Optional[str] = None,
) -> Dict[str, Any]:
    """Resultado parcial de transcrição local, preservando a mídia original."""
    try:
        import faster_whisper  # type: ignore[import-not-found]  # noqa: F401
    except Exception:
        return {"erro": "faster-whisper não instalado (extra 'transcricao')"}

    preprocessar = bool(getattr(settings, "whisper_preprocessar", False))
    with _audio_para_whisper(media_path, preprocessar) as audio:
        resultado = _transcrever_whisper_no_dispositivo(audio, settings, prompt, hotwords=hotwords)
    return {**resultado, "whisper_preprocessado": audio != media_path}


def _transcrever_whisper_no_dispositivo(
    media_path: Path, settings: Any, prompt: str, *, hotwords: Optional[str] = None,
) -> Dict[str, Any]:
    global _CUDA_FALHOU
    from app.core.vram_manager import reservar_gpu

    pedido = str(getattr(settings, "whisper_model", "auto") or "auto").strip() or "auto"
    automatico = pedido.lower() == "auto"
    modelo = "small" if automatico else pedido
    preferencia = str(getattr(settings, "whisper_device", "auto") or "auto").strip().lower()
    modo_gpu = str(getattr(settings, "modo_gpu", "normal") or "normal").strip().lower()
    beam_size: Optional[int] = None

    with reservar_gpu("whisper", modo_gpu=modo_gpu):
        if IS_MACOS or preferencia == "cpu" or _CUDA_FALHOU:
            device, compute_type = "cpu", "int8"
            if modo_gpu == "moderado":
                beam_size = 2
        else:
            # Sequência segura (TASK-014/029): descarregar Ollama → medir VRAM novamente → decidir.
            release_ollama_vram_for_whisper()
            gpu_atual = get_gpu_vram_status()
            decisao = avaliar_whisper_vram(preferencia, pedido, gpu_status=gpu_atual, modo_gpu=modo_gpu)
            device, compute_type = decisao["device"], decisao["compute_type"]
            modelo = decisao["modelo"]
            beam_size = decisao.get("beam_size")
            if device == "cuda" and not _cuda_disponivel() and (automatico or preferencia == "auto"):
                device, compute_type = "cpu", "int8"
                if automatico:
                    modelo = "small"

        tentativas = [(device, compute_type)]
        if device == "cuda":
            tentativas.append(("cpu", "int8"))

        ultimo_erro = ""
        caminho_turbo: Optional[str] = None
        for dev, ctype in tentativas:
            modelo_tentativa = "small" if automatico and dev == "cpu" else modelo
            rotulo_modelo = "turbo" if modelo_tentativa == "large-v3-turbo" else Path(modelo_tentativa).name
            if modelo_tentativa in ("turbo", "large-v3-turbo"):
                try:
                    caminho_turbo = caminho_turbo or _preparar_modelo_turbo()
                except Exception as exc:
                    ultimo_erro = f"preparação do modelo turbo ({type(exc).__name__})"
                    logger.warning("Whisper turbo indisponível na preparação (%s).", type(exc).__name__)
                    if automatico:
                        continue
                    return {"erro": f"faster-whisper falhou ({ultimo_erro})"}
                modelo_tentativa = caminho_turbo
            try:
                with _WHISPER_USO_LOCK:
                    executar = _executar_whisper_isolado if dev == "cuda" and _ISOLAR_CUDA else _executar_whisper
                    opcoes: Dict[str, Any] = {}
                    if hotwords:
                        opcoes["hotwords"] = hotwords
                    if beam_size is not None:
                        opcoes["beam_size"] = beam_size
                    segs, duracao = executar(media_path, modelo_tentativa, dev, ctype, prompt, **opcoes)
                return {"segmentos": segs, "engine": f"faster-whisper {rotulo_modelo} ({dev}/{ctype})", "duracao_seg": duracao}
            except Exception as err:
                ultimo_erro = f"{type(err).__name__}: {err}"
                logger.warning("faster-whisper falhou em %s/%s (%s).", dev, ctype, type(err).__name__)
                if dev == "cuda":
                    _CUDA_FALHOU = True  # não insiste na GPU nas próximas mídias (podia travar o motor)
                    descarregar_modelo_whisper()
        return {"erro": f"faster-whisper falhou ({ultimo_erro})"}


# ----------------------------------------------------------------------------------------------
# Motores externos (somente com consentimento)
# ----------------------------------------------------------------------------------------------
def _envio_externo_autorizado(settings: Any, consentimento: bool) -> Tuple[bool, str]:
    from app.core.envio_externo import motivo_bloqueio_envio_externo

    motivo = motivo_bloqueio_envio_externo(settings, consentimento)
    return not motivo, motivo


def _chave_gemini() -> str:
    try:
        from app.core.secrets import get_secret

        chave = (get_secret("gemini_api_key") or "").strip()
    except Exception:
        chave = ""
    return chave or os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()


def _converter_para_mp3(ffmpeg_bin: str, media_path: Path) -> Optional[str]:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3:
        mp3_path = tmp_mp3.name
    try:
        subprocess.run(
            [ffmpeg_bin, "-y", "-i", str(media_path), "-vn", "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "48k", mp3_path],
            capture_output=True,
            timeout=600,
            **subprocess_no_window_kwargs(),
        )
        if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 1000:
            return mp3_path
    except Exception as err:
        logger.warning("Conversão para MP3 falhou: %s", type(err).__name__)
    with contextlib.suppress(OSError):
        os.remove(mp3_path)
    return None


def _transcrever_gemini(media_path: Path) -> Dict[str, Any]:
    chave = _chave_gemini()
    if not chave:
        return {"erro": "Gemini: chave de API não configurada"}
    ffmpeg_bin = find_binary("ffmpeg")
    if not ffmpeg_bin:
        return {"erro": "Gemini: ffmpeg ausente para extrair o áudio"}
    try:
        from google import genai  # type: ignore[import-not-found]
        from google.genai import types  # type: ignore[import-not-found]
    except Exception:
        return {"erro": "Gemini: pacote opcional google-genai não instalado (extra 'nuvem')"}

    mp3_path = _converter_para_mp3(ffmpeg_bin, media_path)
    if not mp3_path:
        return {"erro": "Gemini: não foi possível extrair o áudio da mídia"}
    try:
        from app.generation.gemini_integration import (
            _chave_cache_hash,
            resolver_modelo_gemini,
        )

        client = genai.Client(api_key=chave)
        resolucao = resolver_modelo_gemini(
            client,
            _GEMINI_MODELO_TRANSCRICAO,
            preferencia_familia="flash",
            chave_cache=_chave_cache_hash(chave),
        )
        modelo_tr = resolucao["modelo_utilizado"]
        audio_bytes = Path(mp3_path).read_bytes()
        prompt_tr = (
            "Transcreva integralmente este áudio de audiência judicial brasileira em português, fiel ao falado, "
            "identificando os interlocutores (Juiz, Ministério Público, Defesa, Vítima/Testemunha/Réu) e retornando APENAS JSON válido "
            "no formato: [{\"timestamp\": \"MM:SS - MM:SS\", \"texto_literal\": \"...\"}]. Não resuma e não invente trechos."
        )
        if len(audio_bytes) <= 18 * 1024 * 1024:
            resp = client.models.generate_content(
                model=modelo_tr,
                contents=[types.Part.from_bytes(data=audio_bytes, mime_type="audio/mp3"), prompt_tr],
            )
        else:
            uploaded_audio = client.files.upload(file=mp3_path)
            try:
                resp = client.models.generate_content(model=modelo_tr, contents=[uploaded_audio, prompt_tr])
            finally:
                with contextlib.suppress(Exception):
                    if getattr(uploaded_audio, "name", None):
                        client.files.delete(name=uploaded_audio.name)
        raw_json = (getattr(resp, "text", "") or "").strip()
        raw_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_json, flags=re.IGNORECASE | re.MULTILINE).strip()
        parsed = json.loads(raw_json)
        if not isinstance(parsed, list):
            return {"erro": "Gemini: resposta fora do formato esperado"}
        segs: List[Dict[str, str]] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            texto = str(item.get("texto_literal", "")).strip()
            ts = str(item.get("timestamp", "")).strip()
            if texto:
                segs.append({"timestamp": ts, "texto_literal": texto})
        if not segs:
            return {"erro": "Gemini: nenhum segmento transcrito"}
        return {"segmentos": segs, "engine": "Gemini Multimodal Audio PT-BR"}
    except Exception as err:
        return {"erro": f"Gemini falhou ({type(err).__name__})"}
    finally:
        with contextlib.suppress(OSError):
            os.remove(mp3_path)


def _transcribe_chunk_sr(ffmpeg_bin: str, media_path: Path, t_start: float, t_end: float) -> Dict[str, str] | None:
    import speech_recognition as sr  # type: ignore[import-not-found]

    ts_label = f"{_fmt_mmss(t_start)} - {_fmt_mmss(t_end)}"
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    try:
        subprocess.run(
            [
                ffmpeg_bin, "-y", "-ss", str(t_start), "-t", str(max(1.0, t_end - t_start)), "-i", str(media_path),
                "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", wav_path,
            ],
            capture_output=True,
            timeout=60,
            **subprocess_no_window_kwargs(),
        )
        rec = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio = rec.record(source)
        txt = rec.recognize_google(audio, language="pt-BR").strip()
        return {"timestamp": ts_label, "texto_literal": txt} if txt else None
    except Exception:
        return None
    finally:
        with contextlib.suppress(OSError):
            os.remove(wav_path)


def _transcrever_speech_recognition(media_path: Path, duracao_seg: Optional[float]) -> Dict[str, Any]:
    try:
        import speech_recognition  # type: ignore[import-not-found]  # noqa: F401
    except Exception:
        return {"erro": "SpeechRecognition não instalado (extra 'nuvem')"}
    ffmpeg_bin = find_binary("ffmpeg")
    if not ffmpeg_bin:
        return {"erro": "SpeechRecognition: ffmpeg ausente"}
    if not duracao_seg:
        return {"erro": "SpeechRecognition: duração da mídia desconhecida"}
    step = 45.0
    intervals: List[Tuple[float, float]] = []
    t = 0.0
    while t < duracao_seg:
        intervals.append((t, min(duracao_seg, t + step)))
        t += step
    segs: List[Dict[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_transcribe_chunk_sr, ffmpeg_bin, media_path, a, b) for a, b in intervals]
        for fut in futures:
            res = fut.result()
            if res:
                segs.append(res)
    if not segs:
        return {"erro": "SpeechRecognition: nenhum trecho reconhecido"}
    return {"segmentos": segs, "engine": "FFmpeg + SpeechRecognition (Google Web Speech) PT-BR"}


# ----------------------------------------------------------------------------------------------
# Cache
# ----------------------------------------------------------------------------------------------
def _caminho_cache(media_path: Path) -> Path:
    return media_path.with_name(media_path.name + TRANSCRICAO_CACHE_SUFFIX)


def _ler_cache(cache_file: Path, media_path: Path, sha: str) -> Optional[Dict[str, Any]]:
    try:
        if not cache_file.exists():
            return None
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(cached, dict):
        return None
    if cached.get("schema_version") == TRANSCRICAO_SCHEMA:
        if cached.get("sha256") != sha:
            return None
        if cached.get("status") == STATUS_OK and cached.get("segmentos"):
            return cached
        if cached.get("status") == STATUS_FALHOU and cached.get("falha_definitiva"):
            return cached
        return None
    return _migrar_cache_legado(cached, cache_file, media_path, sha)


def _migrar_cache_legado(cached: Dict[str, Any], cache_file: Path, media_path: Path, sha: str) -> Optional[Dict[str, Any]]:
    """Aceita cache antigo (por tamanho) só quando é seguro: tamanho igual, cache posterior à mídia,
    segmentos reais e motor não fictício."""
    try:
        st_media = media_path.stat()
        st_cache = cache_file.stat()
    except OSError:
        return None
    segs = cached.get("segmentos")
    if (
        cached.get("schema_version") is None
        and cached.get("size") == st_media.st_size
        and st_cache.st_mtime >= st_media.st_mtime
        and isinstance(segs, list)
        and any(isinstance(s, dict) and str(s.get("texto_literal", "")).strip() for s in segs)
        and str(cached.get("engine", "")) not in _ENGINES_FICTICIOS_LEGADOS
    ):
        migrado = dict(cached)
        migrado.update({"schema_version": TRANSCRICAO_SCHEMA, "sha256": sha, "status": STATUS_OK, "erro": ""})
        migrado["migrado_de_cache_legado"] = True
        return migrado
    return None


# ----------------------------------------------------------------------------------------------
# Orquestração
# ----------------------------------------------------------------------------------------------
_RE_CNJ = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")


def _numero_cnj_vizinho(media_path: Path) -> str:
    """Número do processo (padrão CNJ) pelo nome do PDF dos autos que está na mesma pasta da mídia."""
    try:
        for pdf in sorted(Path(media_path).parent.glob("*.pdf")):
            m = _RE_CNJ.search(pdf.name)
            if m:
                return m.group()
    except OSError:
        pass
    return ""


def _modelo_preferido_cache(settings: Any) -> str:
    pedido = str(getattr(settings, "whisper_model", "auto") or "auto").strip() or "auto"
    if pedido == "large-v3-turbo":
        return "turbo"
    if pedido.lower() != "auto":
        return Path(pedido).name or pedido
    preferencia = str(getattr(settings, "whisper_device", "auto") or "auto").strip().lower()
    if preferencia == "cpu" or IS_MACOS or not _cuda_disponivel():
        return "small"
    politica = avaliar_whisper_vram(preferencia, "auto", modo_gpu=getattr(settings, "modo_gpu", None))
    return politica["modelo"]


def _modelo_mudou(cached: Dict[str, Any], settings: Any) -> bool:
    """True se a transcrição em cache foi feita pelo faster-whisper com outro modelo que o configurado."""
    engine = str(cached.get("engine") or "")
    m = re.match(r"faster-whisper\s+([^\s(]\S*)\s+\(", engine)
    if not m:
        return False  # motor externo/legado: não refaz
    modelo_cache = "turbo" if m.group(1) == "large-v3-turbo" else m.group(1)
    return modelo_cache != _modelo_preferido_cache(settings)


def _aguardar_vram_livre(minimo_mb: int, espera_s: float = 25.0) -> bool:
    """Espera a VRAM livre atingir ``minimo_mb`` (o Ollama leva alguns segundos para devolvê-la).

    Se a medição não for possível, devolve True (não bloqueia quem não tem nvidia-smi).
    """
    import time

    from app.core.platform_utils import get_gpu_status

    limite = time.monotonic() + espera_s
    while True:
        try:
            livre = get_gpu_status().get("vram_free_mb")
        except Exception:
            return True
        if livre is None or float(livre) >= minimo_mb:
            return True
        if time.monotonic() >= limite:
            return False
        time.sleep(1.5)


def transcrever_midia_audiencia(
    media_path: Path,
    depoente: str = "DEPOENTE",
    papel: str = "TESTEMUNHA_ACUSACAO",
    known_ofendida: str = "",
    known_reu: str = "",
    *,
    consentimento_envio_externo: bool = False,
    consentimento_speech_recognition: bool = False,
    usar_cache: bool = True,
    numero_processo: Optional[str] = None,
    identidade_caso: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Transcreve uma mídia de audiência (.mp4, .mp3, .wav, .ogg...).

    Retorno (chaves estáveis): ``media_id``, ``arquivo``, ``depoente``, ``papel``, ``duracao``
    (``MM:SS`` ou ``""`` se desconhecida), ``segmentos`` [{``timestamp``, ``texto_literal``}],
    ``engine``, ``status`` (``OK``/``FALHOU``), ``erro``, ``sha256``, ``schema_version``.

    O Google Web Speech (``speech_recognition``) trafega o áudio sem TLS: nunca é encadeado
    automaticamente após falha do Gemini; só roda com ``consentimento_speech_recognition=True``
    (consentimento próprio, além do envio externo).
    """
    media_path = Path(media_path)
    sha = sha256_arquivo(media_path)
    media_id, nome_final, papel_final = _infer_speaker_and_role(
        media_path.name, depoente, papel, known_ofendida=known_ofendida, known_reu=known_reu, sha256_conteudo=sha
    )
    identidade = {"media_id": media_id, "arquivo": media_path.name, "depoente": nome_final, "papel": papel_final}
    cache_file = _caminho_cache(media_path)

    from app.core.config import load_settings

    try:
        settings = load_settings()
    except Exception:
        settings = None

    if usar_cache and sha:
        cached = _ler_cache(cache_file, media_path, sha)
        if cached is not None and cached.get("identidade_caso") != identidade_caso:
            cached = None
        if cached is not None and (str(cached.get("engine") or "").startswith("faster-whisper")
                                   or cached.get("status") == STATUS_FALHOU):
            if bool(cached.get("whisper_preprocessar")) != bool(getattr(settings, "whisper_preprocessar", False)):
                cached = None
        if cached is not None and cached.get("status") == STATUS_FALHOU:
            # FALHOU cacheado só vale se esta chamada não autoriza motores ainda não tentados
            # nem muda o modelo Whisper (consentimento posterior deve poder reprocessar).
            autorizado_agora, _ = _envio_externo_autorizado(settings, consentimento_envio_externo)
            tentados = set(cached.get("motores_tentados") or [])
            modelo_atual = _modelo_preferido_cache(settings)
            if (autorizado_agora and "gemini" not in tentados) or cached.get("whisper_model", modelo_atual) != modelo_atual:
                cached = None
        if cached is not None and cached.get("status") != STATUS_FALHOU and _modelo_mudou(cached, settings):
            cached = None  # o usuário trocou o modelo Whisper: refaz a transcrição com o modelo escolhido
        if cached is not None:
            atualizado = {**cached, **identidade}
            if atualizado != cached or cached.get("migrado_de_cache_legado"):
                atualizado.pop("migrado_de_cache_legado", None)
                _gravar_json_atomico(cache_file, atualizado)
            return atualizado

    erros: List[str] = []
    motores: List[str] = []
    if sha is None:
        erros.append("arquivo de mídia inacessível")
        resultado: Dict[str, Any] = {}
    else:
        hotwords = _hotwords_forenses([known_ofendida, known_reu, nome_final])
        motores.append("faster-whisper")
        resultado = _transcrever_whisper_local(media_path, settings, _prompt_forense(), hotwords=hotwords)
        if resultado.get("erro"):
            erros.append(resultado["erro"])
        elif not resultado.get("segmentos"):
            erros.append("faster-whisper não reconheceu fala na mídia")

        if not resultado.get("segmentos"):
            autorizado, motivo = _envio_externo_autorizado(settings, consentimento_envio_externo)
            if autorizado:
                motores.append("gemini")
                externo = _transcrever_gemini(media_path)
                if not externo.get("segmentos"):
                    erros.append(externo.get("erro", "Gemini falhou"))
                    if consentimento_speech_recognition is True:
                        motores.append("speech_recognition")
                        externo = _transcrever_speech_recognition(media_path, _duracao_midia_segundos(media_path))
                        if not externo.get("segmentos"):
                            erros.append(externo.get("erro", "SpeechRecognition falhou"))
                    else:
                        erros.append("SpeechRecognition (Google, sem TLS) não utilizado: sem consentimento próprio")
                if externo.get("segmentos"):
                    resultado = externo
            else:
                erros.append(f"motores externos não utilizados: {motivo}")

    segmentos = resultado.get("segmentos") or []
    if segmentos:
        try:
            from app.transcription.pos_processamento import aprimorar_segmentos

            segmentos = aprimorar_segmentos(
                segmentos,
                nomes=[known_ofendida, known_reu, nome_final],
                numero_processo=_numero_cnj_vizinho(media_path) if numero_processo is None else numero_processo,
            )
        except Exception:  # o ajuste é um complemento: nunca derruba a transcrição
            logger.warning("Ajuste de nomes na transcrição falhou; mantido o texto do Whisper.", exc_info=True)
    duracao_seg = resultado.get("duracao_seg") or (_duracao_midia_segundos(media_path) if sha else None)
    if not duracao_seg and segmentos:
        ultimo_ts = str(segmentos[-1].get("timestamp", "")).split("-")[-1].strip()
        duracao_txt = ultimo_ts if re.fullmatch(r"\d{2,}:\d{2}", ultimo_ts) else ""
    else:
        duracao_txt = _fmt_mmss(duracao_seg) if duracao_seg else ""

    saida: Dict[str, Any] = {
        "schema_version": TRANSCRICAO_SCHEMA,
        "sha256": sha,
        "size": media_path.stat().st_size if sha else None,
        **identidade,
        "duracao": duracao_txt,
        "segmentos": segmentos,
        "engine": resultado.get("engine", "nenhum") if segmentos else "nenhum",
        "status": STATUS_OK if segmentos else STATUS_FALHOU,
        "erro": "" if segmentos else "; ".join(e for e in erros if e) or "transcrição indisponível",
        "motores_tentados": motores,
    }
    if "whisper_preprocessado" in resultado:
        saida["whisper_preprocessado"] = resultado["whisper_preprocessado"]
        saida["whisper_preprocessar"] = bool(getattr(settings, "whisper_preprocessar", False))
    if identidade_caso is not None:
        saida["identidade_caso"] = identidade_caso
    if not segmentos:
        # Falha definitiva (o Whisper rodou e não há fala) pode ser cacheada; ausência de motor não.
        # Só é definitiva se os motores externos elegíveis foram de fato tentados.
        externo_tentado = "gemini" in motores
        saida["falha_definitiva"] = bool(resultado.get("engine")) and not resultado.get("erro") and (
            externo_tentado or not _envio_externo_autorizado(settings, True)[0]
        )
        saida["whisper_model"] = _modelo_preferido_cache(settings)
        logger.warning("Transcrição FALHOU para mídia [%s]: %s", rotulo_log_arquivo(media_path.name), saida["erro"])
    if sha and (segmentos or saida.get("falha_definitiva")):
        _gravar_json_atomico(cache_file, saida)
    return saida
