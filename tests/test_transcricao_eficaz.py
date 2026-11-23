"""TASK-077 — opções da transcrição local exercitadas sem download nem áudio real."""

from types import SimpleNamespace

from app.transcription import audio_transcriber as at


def test_hotwords_priorizam_nomes_e_limite_cabe_no_prompt():
    texto = at._hotwords_forenses(["Tício Fictício", "Mévia Exemplar", "Tício Fictício", "á" * 120])
    assert texto.startswith("Tício Fictício, Mévia Exemplar")
    assert texto.count("Tício Fictício") == 1
    assert "medida protetiva" in texto
    assert len((texto + at._prompt_forense()).encode("utf-8")) <= 220
    assert "á" * 120 not in texto


def test_transcricao_sequencial_entrega_hotwords_ao_modelo(tmp_path, monkeypatch):
    chamadas = []

    def transcribe(arquivo, **kwargs):
        chamadas.append((arquivo, kwargs))
        return [], SimpleNamespace(duration=2.0)

    monkeypatch.setattr(at, "_obter_modelo_whisper", lambda *_: SimpleNamespace(transcribe=transcribe))
    at._executar_whisper(tmp_path / "fala.wav", "small", "cpu", "int8", at._prompt_forense(),
                         hotwords="Tício Fictício, medida protetiva")
    opcoes = chamadas[0][1]
    assert opcoes["hotwords"] == "Tício Fictício, medida protetiva"
    assert opcoes["condition_on_previous_text"] is False
    assert opcoes["word_timestamps"] is True


def test_modelo_automatico_depende_da_vram_e_respeita_preferencia(monkeypatch):
    from app.core import vram_manager as vram

    gpu = {"gpu_available": True, "vram_free_mb": 4800}
    fraca = {"gpu_available": True, "vram_free_mb": 2000}
    ausente = {"gpu_available": False}
    escolher = vram.avaliar_whisper_vram
    assert escolher("auto", "auto", gpu)["modelo"] == "turbo"
    assert escolher("auto", "auto", gpu)["compute_type"] == "int8_float16"
    ampla = {"gpu_available": True, "vram_free_mb": 6700}
    assert escolher("auto", "auto", ampla)["compute_type"] == "float16"
    assert escolher("auto", "auto", {"gpu_available": True, "vram_free_mb": 3200})["compute_type"] == "int8_float16"
    assert escolher("auto", "auto", fraca)["modelo"] == "small"
    assert escolher("auto", "auto", ausente)["device"] == "cpu"
    assert escolher("auto", "medium", gpu)["modelo"] == "medium"
    assert escolher("cpu", "auto", gpu)["modelo"] == "small"
    monkeypatch.setattr(vram, "IS_MACOS", True)
    assert escolher("auto", "auto", gpu)["modelo"] == "small"


def test_modelo_resolvido_invalida_cache_da_configuracao_automatica(monkeypatch):
    monkeypatch.setattr(at, "_cuda_disponivel", lambda: True)
    monkeypatch.setattr(at, "avaliar_whisper_vram", lambda *_a, **_kw: {"modelo": "turbo"})
    config = SimpleNamespace(whisper_model="auto", whisper_device="auto")
    assert at._modelo_mudou({"engine": "faster-whisper small (cpu/int8)"}, config)
    assert not at._modelo_mudou({"engine": "faster-whisper turbo (cuda/float16)"}, config)
    assert not at._modelo_mudou({"engine": "faster-whisper large-v3-turbo (cuda/float16)"}, config)


def test_turbo_informa_download_antes_de_pedir_arquivo_ao_hub(monkeypatch, tmp_path):
    import pytest

    utils = pytest.importorskip("faster_whisper.utils")
    hub = pytest.importorskip("huggingface_hub")
    from huggingface_hub.errors import LocalEntryNotFoundError
    from app.api.jobs import PROGRESSO

    def ausente(*_a, **_kw):
        raise LocalEntryNotFoundError("cache fictício vazio")

    def download(repositorio, **opcoes):
        assert "large-v3-turbo" in repositorio
        assert opcoes["token"] is False
        assert PROGRESSO.obter()["phase"] == "baixando_modelo_whisper"
        return str(tmp_path)

    monkeypatch.setattr(utils, "download_model", ausente)
    monkeypatch.setattr(hub, "snapshot_download", download)
    anterior = PROGRESSO.obter()["phase"]
    assert at._preparar_modelo_turbo() == str(tmp_path)
    assert PROGRESSO.obter()["phase"] == anterior


def test_lote_so_em_cuda_preserva_timestamps_e_opcoes(tmp_path, monkeypatch):
    import pytest

    whisper = pytest.importorskip("faster_whisper")
    chamadas = []

    def transcribe(arquivo, **kwargs):
        chamadas.append(kwargs.copy())
        return [], SimpleNamespace(duration=2.0)

    modelo = SimpleNamespace(transcribe=transcribe)

    def em_lote(model):
        assert model is modelo
        return SimpleNamespace(transcribe=transcribe)

    monkeypatch.setattr(at, "_obter_modelo_whisper", lambda *_: modelo)
    monkeypatch.setattr(whisper, "BatchedInferencePipeline", em_lote)
    arquivo = tmp_path / "fala.wav"
    at._executar_whisper(arquivo, "turbo", "cuda", "int8_float16", "prompt", batch_size=4, hotwords="Mévia Exemplar")
    at._executar_whisper(arquivo, "small", "cpu", "int8", "prompt", batch_size=4)
    assert chamadas[0]["batch_size"] == 4
    assert chamadas[0]["hotwords"] == "Mévia Exemplar"
    assert chamadas[0]["word_timestamps"] and chamadas[1]["word_timestamps"]
    assert "batch_size" not in chamadas[1]


def test_lote_respeita_folga_e_modelo_snapshot(monkeypatch):
    livre = 4800
    monkeypatch.setattr(at, "get_gpu_vram_status", lambda: {"vram_free_mb": livre})
    modelo = "modelos/models--publico--faster-whisper-large-v3-turbo/snapshots/abcd"
    assert at._lote_cuda(modelo, "float16", False) == 4
    assert at._lote_cuda(modelo, "int8_float16", True) == 4
    livre = 6100
    assert at._lote_cuda(modelo, "int8_float16", False) == 8
    assert at._lote_cuda(modelo, "int8_float16", True) == 4
    livre = 4300
    assert at._lote_cuda(modelo, "float16", False) == 1


def test_cuda_falhando_recua_para_cpu_sem_perder_hotwords(tmp_path, monkeypatch):
    import pytest

    pytest.importorskip("faster_whisper")
    chamadas = []
    monkeypatch.setattr(at, "_CUDA_FALHOU", False)
    monkeypatch.setattr(at, "IS_MACOS", False)
    monkeypatch.setattr(at, "_ISOLAR_CUDA", True)
    monkeypatch.setattr(at, "_cuda_disponivel", lambda: True)
    monkeypatch.setattr(at, "release_ollama_vram_for_whisper", lambda: True)
    monkeypatch.setattr(at, "get_gpu_vram_status", lambda: {"gpu_available": True, "vram_free_mb": 5100})
    monkeypatch.setattr(at, "_preparar_modelo_turbo", lambda: "turbo-ficticio")

    def executar(arquivo, modelo, dispositivo, precisao, prompt, **opcoes):
        chamadas.append((modelo, dispositivo, opcoes))
        if dispositivo == "cuda":
            raise RuntimeError("erro fictício de CUDA")
        return [{"timestamp": "00:00 - 00:01", "texto_literal": "fala fictícia"}], 1.0

    monkeypatch.setattr(at, "_executar_whisper", executar)
    monkeypatch.setattr(at, "_executar_whisper_isolado", executar)
    config = SimpleNamespace(whisper_device="auto", whisper_model="auto")
    resultado = at._transcrever_whisper_local(tmp_path / "fala.wav", config, "prompt", hotwords="Mévia Exemplar")
    assert at._CUDA_FALHOU
    assert chamadas == [
        ("turbo-ficticio", "cuda", {"hotwords": "Mévia Exemplar"}),
        ("small", "cpu", {"hotwords": "Mévia Exemplar"}),
    ]
    assert resultado["engine"] == "faster-whisper small (cpu/int8)"


def test_filtro_usa_copia_mono_16k_e_limpa_em_falha_do_decoder(tmp_path, monkeypatch):
    import pytest

    midia = tmp_path / "original.wav"
    midia.write_bytes(b"original ficticio")
    monkeypatch.setattr(at, "find_binary", lambda _: "ffmpeg-ficticio")

    def executar(comando, **kwargs):
        from pathlib import Path

        assert ["-ac", "1", "-ar", "16000"] == comando[comando.index("-ac"):comando.index("-ac") + 4]
        assert "highpass=f=80,dynaudnorm=f=250:g=15:p=0.85" in comando
        assert kwargs["capture_output"]
        Path(comando[-1]).write_bytes(b"audio processado ficticio" * 4)

    monkeypatch.setattr(at.subprocess, "run", executar)
    with pytest.raises(RuntimeError, match="decoder fictício"):
        with at._audio_para_whisper(midia, True) as copia:
            assert copia != midia and copia.is_file()
            raise RuntimeError("decoder fictício")
    assert not copia.exists()
    assert midia.read_bytes() == b"original ficticio"


def test_sem_ffmpeg_usa_original_e_log_sem_nome_do_arquivo(tmp_path, monkeypatch, caplog):
    import logging

    midia = tmp_path / "audiencia-ficticia.wav"
    monkeypatch.setattr(at, "find_binary", lambda _: None)
    with caplog.at_level(logging.INFO):
        with at._audio_para_whisper(midia, True) as audio:
            assert audio == midia
    assert "ffmpeg ausente" in caplog.text
    assert midia.name not in caplog.text

    def proibido(_):
        raise AssertionError("Configuração desativou o filtro")

    monkeypatch.setattr(at, "find_binary", proibido)
    with at._audio_para_whisper(midia, False) as audio:
        assert audio == midia


def test_ffmpeg_com_erro_remove_saida_parcial(tmp_path, monkeypatch):
    from pathlib import Path

    original = tmp_path / "original.wav"
    monkeypatch.setattr(at, "find_binary", lambda _: "ffmpeg-ficticio")
    caminhos = []

    def executar(comando, **_kwargs):
        parcial = Path(comando[-1])
        caminhos.append(parcial)
        parcial.write_bytes(b"parcial")
        raise at.subprocess.CalledProcessError(1, comando)

    monkeypatch.setattr(at.subprocess, "run", executar)
    with at._audio_para_whisper(original, True) as audio:
        assert audio == original
    assert len(caminhos) == 1 and not caminhos[0].exists()


def test_download_turbo_incompleto_nao_marca_cuda_com_falha(tmp_path, monkeypatch):
    import pytest

    pytest.importorskip("faster_whisper")
    monkeypatch.setattr(at, "_CUDA_FALHOU", False)
    monkeypatch.setattr(at, "IS_MACOS", False)
    monkeypatch.setattr(at, "_cuda_disponivel", lambda: True)
    monkeypatch.setattr(at, "release_ollama_vram_for_whisper", lambda: True)
    monkeypatch.setattr(at, "get_gpu_vram_status", lambda: {"gpu_available": True, "vram_free_mb": 5100})

    def falha_modelo():
        raise OSError("falha de cache simulada")

    monkeypatch.setattr(at, "_preparar_modelo_turbo", falha_modelo)
    monkeypatch.setattr(
        at, "_executar_whisper",
        lambda audio, modelo, dispositivo, *_a, **_kw: ([{"modelo": modelo, "dispositivo": dispositivo}], 1.0),
    )
    config = SimpleNamespace(whisper_device="auto", whisper_model="auto")
    resposta = at._transcrever_whisper_local(tmp_path / "fala.wav", config, "prompt")
    assert not at._CUDA_FALHOU
    assert resposta["segmentos"] == [{"modelo": "small", "dispositivo": "cpu"}]
