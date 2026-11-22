"""Testes da Fundação v2.0.0 (app/core): utilitários, flags, contexto, segredos, plataforma, Ollama."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import httpx
import pytest

from app.core import case_context as cc
from app.core import config as core_config
from app.core import ollama_client
from app.core import platform_utils as pu
from app.core import secrets as core_secrets
from app.core import vram_manager
from app.core.case_flags import is_demo_bundle
from app.core.errors import (
    DadosInsuficientesError,
    DecisaoJudicialPendenteError,
    EnvioExternoNaoAutorizadoError,
)
from app.core.text_utils import normalizar, remover_acentos
from app.demo.demo_case import get_demo_case_bundle


# ----------------------------------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------------------------------
@pytest.fixture
def sandbox_data(tmp_path, monkeypatch):
    """Isola DATA_DIR/SETTINGS_FILE e força o cofre em arquivo (nunca toca o cofre real)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(core_config, "DATA_DIR", data_dir)
    monkeypatch.setattr(core_config, "SETTINGS_FILE", data_dir / "user_settings.json")
    monkeypatch.setenv("JVDFM_SECRETS_BACKEND", "arquivo")
    return data_dir


@pytest.fixture
def contexto_limpo():
    cc.limpar_cache_contexto()
    yield
    cc.limpar_cache_contexto()


def _bundle_real_minimo(doc_id: str = "ID-41001234") -> dict:
    return {
        "numero_processo": "0001234-56.2024.8.09.0001",
        "raw_documents": [
            {
                "doc_id": doc_id,
                "titulo": "Denúncia",
                "data_peca": "2024-03-10",
                "conteudo": (
                    "O MINISTÉRIO PÚBLICO oferece DENÚNCIA em desfavor de FULANO DE TAL pela prática do "
                    "crime previsto no art. 129, § 13, do Código Penal, no âmbito da Lei 11.340/2006."
                ),
            }
        ],
        "transcriptions": [],
    }


# ----------------------------------------------------------------------------------------------
# text_utils
# ----------------------------------------------------------------------------------------------
def test_remover_acentos_preserva_caixa_e_espacos():
    assert remover_acentos("Ação Penal — LESÃO  Corporal") == "Acao Penal — LESAO  Corporal"
    assert remover_acentos("") == ""
    assert remover_acentos(None) == ""  # type: ignore[arg-type]


def test_normalizar_sem_acento_minusculo_espacos_colapsados():
    assert normalizar("  Ameaça\t\nCONTRA   a  Mulher ") == "ameaca contra a mulher"
    assert normalizar("Violência Doméstica") == normalizar("violencia   domestica")
    assert normalizar("") == ""
    assert normalizar(None) == ""  # type: ignore[arg-type]


# ----------------------------------------------------------------------------------------------
# errors
# ----------------------------------------------------------------------------------------------
def test_dados_insuficientes_error_atributos():
    err = DadosInsuficientesError(["data_fato", "data_recebimento_denuncia"], "Faltam datas.")
    assert isinstance(err, ValueError)
    assert err.campos == ["data_fato", "data_recebimento_denuncia"]
    assert err.mensagem == "Faltam datas."
    assert str(err) == "Faltam datas."
    # Ordem invertida tolerada.
    inv = DadosInsuficientesError("Falta a data do fato.", ["data_fato"])
    assert inv.campos == ["data_fato"] and inv.mensagem == "Falta a data do fato."
    # Mensagem padrão menciona os campos.
    padrao = DadosInsuficientesError(campos=["crimes_imputados"])
    assert "crimes_imputados" in padrao.mensagem
    assert padrao.to_dict()["campos"] == ["crimes_imputados"]


def test_decisao_judicial_pendente_error():
    err = DecisaoJudicialPendenteError(["art_147_cp", "art_24a_lmp"])
    assert isinstance(err, DadosInsuficientesError)
    assert isinstance(err, ValueError)
    assert err.crimes_pendentes == ["art_147_cp", "art_24a_lmp"]
    assert err.campos == ["veredicto.art_147_cp", "veredicto.art_24a_lmp"]
    assert "art_147_cp" in err.mensagem
    assert err.to_dict()["crimes_pendentes"] == ["art_147_cp", "art_24a_lmp"]


def test_envio_externo_nao_autorizado_error():
    err = EnvioExternoNaoAutorizadoError()
    assert isinstance(err, PermissionError)
    assert "permitir_envio_externo" in str(err)


# ----------------------------------------------------------------------------------------------
# case_flags / demo
# ----------------------------------------------------------------------------------------------
def test_demo_bundle_tem_flag_explicita():
    bundle = get_demo_case_bundle()
    assert bundle["is_demo"] is True
    assert is_demo_bundle(bundle) is True


def test_bundle_real_com_id_4100_nao_e_demo():
    bundle = _bundle_real_minimo("ID-41001234")
    assert is_demo_bundle(bundle) is False
    # Mesmo com todos os doc_ids do demo, sem a flag explícita não é demo.
    demo_sem_flag = get_demo_case_bundle()
    demo_sem_flag.pop("is_demo")
    assert is_demo_bundle(demo_sem_flag) is False


@pytest.mark.parametrize("valor", ["true", 1, "True", None, False])
def test_is_demo_exige_booleano_true(valor):
    assert is_demo_bundle({"is_demo": valor}) is False


def test_is_demo_bundle_entradas_invalidas():
    assert is_demo_bundle(None) is False
    assert is_demo_bundle([]) is False  # type: ignore[arg-type]
    assert is_demo_bundle({}) is False


# ----------------------------------------------------------------------------------------------
# CaseContext
# ----------------------------------------------------------------------------------------------
def test_contexto_mesmo_conteudo_mesmo_objeto(contexto_limpo):
    b1 = get_demo_case_bundle()
    b2 = copy.deepcopy(b1)
    ctx1 = cc.obter_contexto(b1)
    ctx2 = cc.obter_contexto(b2)
    assert ctx1 is ctx2
    assert len(ctx1.fingerprint) == 64 and all(c in "0123456789abcdef" for c in ctx1.fingerprint)
    assert ctx1.is_demo is True
    assert ctx1.pecas and ctx1.meta.numero_processo
    assert "ID-4100301" in ctx1.ids_midias
    assert list(ctx1.ids_midias) == sorted(ctx1.ids_midias)
    assert len(ctx1.transcriptions) == len(b1["transcriptions"])
    assert ctx1.matriz_oral.depoentes


def test_contexto_conteudo_diferente_fingerprint_diferente(contexto_limpo):
    base = get_demo_case_bundle()
    alterado = copy.deepcopy(base)
    alterado["raw_documents"][0]["conteudo"] += " Acréscimo."
    assert cc.calcular_fingerprint(base) != cc.calcular_fingerprint(alterado)
    assert cc.obter_contexto(base) is not cc.obter_contexto(alterado)


def test_contexto_bundle_real_id_4100_nao_e_demo(contexto_limpo):
    ctx = cc.obter_contexto(_bundle_real_minimo("ID-41001234"))
    assert ctx.is_demo is False
    assert ctx.pecas[0].doc_id == "ID-41001234"


def test_contexto_aplica_overrides_de_metadados(contexto_limpo):
    base = get_demo_case_bundle()
    ctx_base = cc.obter_contexto(base)

    com_override = copy.deepcopy(base)
    com_override["metadados_override"] = {
        "data_fato": "15/03/2025",
        "data_recebimento_denuncia": "2025-04-01",
        "crimes_imputados": "art_129_p13_cp; art_147_cp",
        "nome_reu": "  RÉU INFORMADO PELO JUIZ ",
        "nome_ofendida": "",  # vazio é ignorado (não apaga o extraído)
        "campo_inexistente": "x",  # ignorado
    }
    ctx = cc.obter_contexto(com_override)
    assert ctx.fingerprint != ctx_base.fingerprint
    assert ctx.meta.data_fato == "2025-03-15"
    assert ctx.meta.data_recebimento_denuncia == "2025-04-01"
    assert ctx.meta.crimes_imputados == ["art_129_p13_cp", "art_147_cp"]
    assert ctx.meta.nome_reu == "RÉU INFORMADO PELO JUIZ"
    assert ctx.meta.nome_ofendida == ctx_base.meta.nome_ofendida
    # O contexto sem override segue intacto.
    assert ctx_base.meta.nome_reu != "RÉU INFORMADO PELO JUIZ"


def test_contexto_override_data_invalida_e_ignorado(contexto_limpo):
    base = get_demo_case_bundle()
    ctx_base = cc.obter_contexto(base)
    ruim = copy.deepcopy(base)
    ruim["metadados_override"] = {"data_fato": "31/02/2025"}
    ctx = cc.obter_contexto(ruim)
    assert ctx.meta.data_fato == ctx_base.meta.data_fato


def test_contexto_copias_defensivas_nao_contaminam_cache(contexto_limpo):
    bundle = get_demo_case_bundle()
    ctx = cc.obter_contexto(bundle)
    nome_original = ctx.meta.nome_reu

    meta_mut = ctx.copia_meta()
    meta_mut.nome_reu = "ALTERADO"
    pecas_mut = ctx.copia_pecas()
    pecas_mut[0].conteudo = "ALTERADO"

    # Mutar o bundle de entrada também não afeta o contexto (cópia profunda).
    bundle["raw_documents"][0]["conteudo"] = "MUTADO"

    assert ctx.meta.nome_reu == nome_original
    assert ctx.pecas[0].conteudo != "ALTERADO"
    assert ctx.bundle["raw_documents"][0]["conteudo"] != "MUTADO"
    with pytest.raises(Exception):
        ctx.is_demo = False  # type: ignore[misc]  # dataclass frozen


def test_contexto_cache_lru_tamanho_8_e_thread_safe(contexto_limpo, monkeypatch):
    construcoes: list[str] = []

    def _fake_construir(bundle, fingerprint):
        construcoes.append(fingerprint)
        return cc.CaseContext(
            fingerprint=fingerprint,
            bundle=dict(bundle),
            pecas=[],
            meta=None,  # type: ignore[arg-type]
            matriz_oral=None,  # type: ignore[arg-type]
            is_demo=False,
            ids_midias=(),
            transcriptions=[],
        )

    monkeypatch.setattr(cc, "_construir", _fake_construir)
    bundles = [{"raw_documents": [{"doc_id": f"ID-{i}", "conteudo": str(i)}]} for i in range(9)]
    primeiro = cc.obter_contexto(bundles[0])
    for b in bundles[1:8]:
        cc.obter_contexto(b)
    assert cc.obter_contexto(bundles[0]) is primeiro  # ainda no cache (e passa a ser o mais recente)
    cc.obter_contexto(bundles[8])  # expulsa o menos recente (bundles[1])
    assert len(cc._CACHE) == 8
    n = len(construcoes)
    cc.obter_contexto(bundles[1])
    assert len(construcoes) == n + 1  # recalculado após expulsão
    assert cc.obter_contexto(bundles[0]) is primeiro

    # Concorrência: todas as threads recebem o mesmo objeto.
    cc.limpar_cache_contexto()
    resultados = []
    alvo = {"raw_documents": [{"doc_id": "ID-X", "conteudo": "concorrente"}]}
    threads = [threading.Thread(target=lambda: resultados.append(cc.obter_contexto(alvo))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(resultados) == 8
    assert all(r is resultados[0] for r in resultados)


def test_obter_contexto_rejeita_nao_dict():
    with pytest.raises(ValueError):
        cc.obter_contexto(None)  # type: ignore[arg-type]


# ----------------------------------------------------------------------------------------------
# secrets
# ----------------------------------------------------------------------------------------------
def test_secrets_fallback_em_arquivo(sandbox_data):
    assert core_secrets.backend_ativo() == "arquivo"
    assert core_secrets.get_secret("gemini_api_key") == ""
    assert core_secrets.has_secret("gemini_api_key") is False

    core_secrets.set_secret("gemini_api_key", "  chave-teste-123  ")
    arquivo = sandbox_data / "secrets" / "secrets.json"
    assert arquivo.exists()
    assert json.loads(arquivo.read_text(encoding="utf-8")) == {"gemini_api_key": "chave-teste-123"}
    assert core_secrets.get_secret("gemini_api_key") == "chave-teste-123"
    assert core_secrets.has_secret("gemini_api_key") is True
    if sys.platform != "win32":
        assert (arquivo.stat().st_mode & 0o777) == 0o600

    core_secrets.set_secret("outro", "v2")
    core_secrets.delete_secret("gemini_api_key")
    assert core_secrets.has_secret("gemini_api_key") is False
    assert core_secrets.get_secret("outro") == "v2"

    core_secrets.set_secret("outro", "")  # vazio = remoção
    assert core_secrets.has_secret("outro") is False
    assert not list((sandbox_data / "secrets").glob("*.tmp"))


def test_secrets_nao_usa_keyring_quando_forcado_arquivo(sandbox_data, monkeypatch):
    class _KeyringProibido:
        def __getattr__(self, nome):
            raise AssertionError("keyring não deveria ser usado com backend de arquivo forçado")

    monkeypatch.setitem(sys.modules, "keyring", _KeyringProibido())
    core_secrets.set_secret("gemini_api_key", "abc")
    assert core_secrets.get_secret("gemini_api_key") == "abc"
    core_secrets.delete_secret("gemini_api_key")


def test_secrets_nome_invalido(sandbox_data):
    with pytest.raises(ValueError):
        core_secrets.get_secret("  ")


# ----------------------------------------------------------------------------------------------
# config
# ----------------------------------------------------------------------------------------------
def test_settings_novos_campos_e_sem_gemini_api_key():
    s = core_config.UserSettings()
    assert "gemini_api_key" not in core_config.UserSettings.model_fields
    assert s.permitir_envio_externo is False
    assert s.ollama_num_ctx == 8192
    assert s.whisper_model == "small"
    assert s.whisper_device == "auto"
    assert s.max_upload_mb_pdf == 600
    assert s.max_upload_mb_midia == 4096
    assert core_config.EXPORTS_DIR == core_config.DATA_DIR / "exports"
    assert core_config.LOGS_DIR == core_config.DATA_DIR / "logs"
    assert core_config.ARCHIVE_DIR == core_config.CASES_DIR / "_arquivados"


def test_migracao_gemini_api_key_para_cofre(sandbox_data):
    settings_file = core_config.SETTINGS_FILE
    settings_file.write_text(
        json.dumps(
            {
                "gemini_api_key": "chave-legada-xyz",
                "ollama_model": "qwen2.5:7b",
                "tema_interface": "valor-invalido",
                "chave_desconhecida": 1,
            }
        ),
        encoding="utf-8",
    )
    s = core_config.load_settings()
    assert s.ollama_model == "qwen2.5:7b"  # preserva o válido
    assert s.tema_interface == "dark"  # descarta só o inválido
    assert not hasattr(s, "gemini_api_key")
    persistido = json.loads(settings_file.read_text(encoding="utf-8"))
    assert "gemini_api_key" not in persistido
    assert persistido["ollama_model"] == "qwen2.5:7b"
    assert core_secrets.get_secret("gemini_api_key") == "chave-legada-xyz"

    # Segunda carga: nada a migrar, cofre intacto.
    core_config.load_settings()
    assert core_secrets.get_secret("gemini_api_key") == "chave-legada-xyz"


def test_migracao_remove_chave_vazia_sem_criar_segredo(sandbox_data):
    core_config.SETTINGS_FILE.write_text(json.dumps({"gemini_api_key": ""}), encoding="utf-8")
    core_config.load_settings()
    assert "gemini_api_key" not in json.loads(core_config.SETTINGS_FILE.read_text(encoding="utf-8"))
    assert core_secrets.has_secret("gemini_api_key") is False


def test_save_settings_atomico(sandbox_data, monkeypatch):
    s = core_config.UserSettings(ollama_model="modelo-a")
    core_config.save_settings(s)
    assert json.loads(core_config.SETTINGS_FILE.read_text(encoding="utf-8"))["ollama_model"] == "modelo-a"
    assert core_config.load_settings().ollama_model == "modelo-a"

    original = core_config.SETTINGS_FILE.read_text(encoding="utf-8")

    def _replace_falha(*_a, **_k):
        raise OSError("disco cheio simulado")

    with monkeypatch.context() as m:
        m.setattr(core_config.os, "replace", _replace_falha)
        with pytest.raises(OSError):
            core_config.save_settings(core_config.UserSettings(ollama_model="modelo-b"))
    # Arquivo original intacto e nenhum temporário deixado para trás.
    assert core_config.SETTINGS_FILE.read_text(encoding="utf-8") == original
    assert not [p for p in sandbox_data.iterdir() if p.suffix == ".tmp"]


def test_settings_corrompido_e_preservado(sandbox_data):
    core_config.SETTINGS_FILE.write_text("{ isto não é json", encoding="utf-8")
    s = core_config.load_settings()
    assert s == core_config.UserSettings()
    preservados = list(sandbox_data.glob("user_settings.corrompido-*.json"))
    assert len(preservados) == 1
    assert preservados[0].read_text(encoding="utf-8") == "{ isto não é json"


def test_jvdfm_data_dir_resolucao(tmp_path):
    destino = tmp_path / "dados relocados"
    assert core_config._resolver_data_dir(str(destino)) == destino.resolve()
    assert core_config._resolver_data_dir("") == core_config.BASE_DIR / "data"
    assert core_config._resolver_data_dir("   ") == core_config.BASE_DIR / "data"


def test_jvdfm_data_dir_relocaliza_no_import(tmp_path):
    destino = tmp_path / "SentencaJVDFM"
    env = {**os.environ, "JVDFM_DATA_DIR": str(destino)}
    codigo = (
        "import app.core.config as c, json; "
        "print(json.dumps([str(c.DATA_DIR), str(c.SETTINGS_FILE), str(c.CASES_DIR), str(c.LOGS_DIR)]))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", codigo],
        cwd=str(core_config.BASE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    data_dir, settings_file, cases_dir, logs_dir = json.loads(proc.stdout.strip().splitlines()[-1])
    assert Path(data_dir) == destino.resolve()
    assert Path(settings_file) == destino.resolve() / "user_settings.json"
    assert Path(cases_dir).is_dir() and Path(logs_dir).is_dir()
    assert (destino / "exports").is_dir()


# ----------------------------------------------------------------------------------------------
# platform_utils
# ----------------------------------------------------------------------------------------------
def test_flags_de_plataforma_coerentes():
    assert [pu.IS_WINDOWS, pu.IS_MACOS, pu.IS_LINUX].count(True) <= 1
    kwargs = pu.subprocess_no_window_kwargs()
    if pu.IS_WINDOWS:
        assert kwargs.get("creationflags")
    else:
        assert kwargs == {}


def test_find_binary_sem_excecao(monkeypatch):
    assert pu.find_binary("binario-inexistente-jvdfm-xyz") is None
    assert pu.find_binary("") is None
    assert pu.find_binary(None) is None  # type: ignore[arg-type]

    def _which_explode(*_a, **_k):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(pu.shutil, "which", _which_explode)
    assert pu.find_binary("ffmpeg") is None
    pu.limpar_cache_binarios()


def test_find_binary_encontra_em_diretorio_conhecido(tmp_path, monkeypatch):
    nome = "ferramenta-jvdfm"
    arquivo = tmp_path / (f"{nome}.exe" if pu.IS_WINDOWS else nome)
    arquivo.write_text("#!/bin/sh\n", encoding="utf-8")
    if not pu.IS_WINDOWS:
        arquivo.chmod(0o755)
    monkeypatch.setattr(pu.shutil, "which", lambda *_a, **_k: None)
    monkeypatch.setattr(pu, "_diretorios_conhecidos", lambda: [tmp_path / "vazio", tmp_path])
    assert pu.find_binary(nome) == str(arquivo)


def test_find_chrome_sem_excecao(monkeypatch):
    resultado = pu.find_chrome()
    assert resultado is None or Path(resultado).is_file()

    monkeypatch.setattr(pu, "_candidatos_chrome", lambda: iter(()))
    monkeypatch.setattr(pu.shutil, "which", lambda *_a, **_k: None)
    assert pu.find_chrome() is None


_CHAVES_NUMERICAS = ("vram_used_mb", "vram_total_mb", "vram_free_mb", "vram_percent", "gpu_utilization_percent")


def test_gpu_status_sem_nvidia_smi_nao_inventa_valores(monkeypatch):
    monkeypatch.setattr(pu, "IS_MACOS", False)
    monkeypatch.setattr(pu, "find_binary", lambda _n: None)
    status = pu.get_gpu_status()
    assert status["gpu_available"] is False
    assert status["backend"] == "cpu"
    assert status["gpu_name"] is None
    for chave in _CHAVES_NUMERICAS:
        assert status[chave] is None
    assert "RTX" not in json.dumps(status)

    # A fachada legada delega sem inventar números.
    vram_manager.limpar_cache_vram()
    monkeypatch.setattr(vram_manager, "get_gpu_status", pu.get_gpu_status)
    legado = vram_manager.get_gpu_vram_status()
    assert legado["gpu_available"] is False and legado["vram_total_mb"] is None


def test_gpu_status_macos_apple_silicon(monkeypatch):
    monkeypatch.setattr(pu, "IS_MACOS", True)
    monkeypatch.setattr(pu.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(pu, "_nome_chip_macos", lambda: None)
    status = pu.get_gpu_status()
    assert status["gpu_available"] is False
    assert status["backend"] == "apple_silicon"
    for chave in _CHAVES_NUMERICAS:
        assert status[chave] is None


def test_gpu_status_com_nvidia_smi_medido(monkeypatch):
    monkeypatch.setattr(pu, "IS_MACOS", False)
    monkeypatch.setattr(pu, "find_binary", lambda _n: "nvidia-smi")

    class _Proc:
        returncode = 0
        stdout = "NVIDIA GeForce GTX 1650, 1000, 4096, 7\n"

    monkeypatch.setattr(pu.subprocess, "run", lambda *a, **k: _Proc())
    status = pu.get_gpu_status()
    assert status["gpu_available"] is True
    assert status["backend"] == "cuda"
    assert status["gpu_name"] == "NVIDIA GeForce GTX 1650"
    assert status["vram_used_mb"] == 1000
    assert status["vram_total_mb"] == 4096
    assert status["vram_free_mb"] == 3096
    assert status["gpu_utilization_percent"] == 7
    assert status["fonte"] == "nvidia-smi"


def test_gpu_status_nvidia_smi_falhando(monkeypatch):
    monkeypatch.setattr(pu, "IS_MACOS", False)
    monkeypatch.setattr(pu, "find_binary", lambda _n: "nvidia-smi")

    class _Proc:
        returncode = 9
        stdout = ""

    monkeypatch.setattr(pu.subprocess, "run", lambda *a, **k: _Proc())
    status = pu.get_gpu_status()
    assert status["gpu_available"] is False
    for chave in _CHAVES_NUMERICAS:
        assert status[chave] is None


# ----------------------------------------------------------------------------------------------
# ollama_client
# ----------------------------------------------------------------------------------------------
def test_ollama_disponivel_offline_lista_vazia(sandbox_data, monkeypatch):
    def _get_falha(self, url, *a, **k):
        raise httpx.ConnectError("conexão recusada", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", _get_falha)
    status = ollama_client.ollama_disponivel()
    assert status["online"] is False
    assert status["installed_models"] == []
    assert status["active_model"] is None
    assert status["url"] == core_config.UserSettings().ollama_url
    assert status["erro"]

    legado = vram_manager.check_ollama_status()
    assert legado["online"] is False and legado["installed_models"] == []


def test_ollama_disponivel_online_reporta_somente_medido(sandbox_data, monkeypatch):
    core_config.save_settings(core_config.UserSettings(ollama_model="llama3"))

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"models": [{"name": "llama3:latest"}, {"name": "qwen2.5:7b"}]}

    monkeypatch.setattr(httpx.Client, "get", lambda self, url, *a, **k: _Resp())
    status = ollama_client.ollama_disponivel()
    assert status["online"] is True
    assert status["installed_models"] == ["llama3:latest", "qwen2.5:7b"]
    assert status["active_model"] == "llama3:latest"
    assert status["model_installed"] is True

    core_config.save_settings(core_config.UserSettings(ollama_model="modelo-ausente"))
    status = ollama_client.ollama_disponivel()
    assert status["online"] is True
    assert status["active_model"] is None  # não finge que outro modelo está ativo
    assert status["model_installed"] is False


def test_gerar_texto_envia_num_ctx_e_retorna_texto(sandbox_data, monkeypatch):
    core_config.save_settings(core_config.UserSettings(ollama_num_ctx=4096, ollama_model="m1"))
    capturado = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"response": "  texto gerado  "}

    def _post(self, url, *a, json=None, **k):
        capturado["url"] = url
        capturado["payload"] = json
        return _Resp()

    monkeypatch.setattr(httpx.Client, "post", _post)
    assert ollama_client.gerar_texto("prompt", num_predict=50) == "texto gerado"
    assert capturado["url"].endswith("/api/generate")
    assert capturado["payload"]["model"] == "m1"
    assert capturado["payload"]["stream"] is False
    assert capturado["payload"]["options"]["num_ctx"] == 4096
    assert capturado["payload"]["options"]["num_predict"] == 50

    ollama_client.gerar_texto("prompt", num_ctx=16384)
    assert capturado["payload"]["options"]["num_ctx"] == 16384


def test_gerar_texto_falha_retorna_none_sem_excecao(sandbox_data, monkeypatch, caplog):
    def _post_falha(self, url, *a, **k):
        raise httpx.ConnectError("offline", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", _post_falha)
    with caplog.at_level("WARNING"):
        assert ollama_client.gerar_texto("prompt") is None
    assert any("Ollama" in r.getMessage() for r in caplog.records)

    class _Resp500:
        status_code = 500

        @staticmethod
        def json():
            return {}

    monkeypatch.setattr(httpx.Client, "post", lambda self, url, *a, **k: _Resp500())
    assert ollama_client.gerar_texto("prompt") is None


def test_release_vram_retorna_bool_offline(sandbox_data, monkeypatch):
    def _post_falha(self, url, *a, **k):
        raise httpx.ConnectError("offline", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", _post_falha)
    assert vram_manager.release_ollama_vram_for_whisper() is False
