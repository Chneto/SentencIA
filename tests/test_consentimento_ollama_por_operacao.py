"""Clientes simulados para garantir que conteúdo do caso depende de consentimento por operação."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.core import ollama_client, vram_manager
from app.core.config import UserSettings
from app.core.envio_externo import permissao_ollama_na_operacao
from app.core.errors import EnvioExternoNaoAutorizadoError
from app.generation.adversarial_critic import AdversarialJudicialCritic

URL = "https://ollama.example.invalid"
TEXTO = "Excerto referente à PESSOA DE EXEMPLO."
MAPA = {"[ACUSADO]": "PESSOA DE EXEMPLO"}


class ClienteSimulado:
    def __init__(self, enviados):
        self.enviados = enviados

    def post(self, url, *, json):
        self.enviados.append((url, json))
        return SimpleNamespace(status_code=200, json=lambda: {"response": "Resposta sobre [ACUSADO]."})


@pytest.fixture
def ambiente(monkeypatch):
    settings = UserSettings(ollama_url=URL, ollama_model="modelo-teste", permitir_envio_externo=True)
    enviados = []
    monkeypatch.setattr(ollama_client, "load_settings", lambda: settings)
    monkeypatch.setattr("app.core.config.load_settings", lambda: settings)
    monkeypatch.setattr(ollama_client, "criar_cliente", lambda *_: nullcontext(ClienteSimulado(enviados)))
    monkeypatch.setattr("app.generation.adversarial_critic.httpx.Client", lambda **_: nullcontext(ClienteSimulado(enviados)))
    monkeypatch.setattr(vram_manager, "reservar_gpu", lambda *_, **__: nullcontext())
    monkeypatch.setattr(vram_manager, "calcular_opcoes_ollama", lambda *_, **__: {
        "num_ctx": 128, "num_gpu": None, "keep_alive": None,
    })
    return settings, enviados


def test_ollama_remoto_minimiza_payload_e_esquece_consentimento_apos_resposta(ambiente):
    settings, enviados = ambiente
    assert ollama_client.gerar_texto(TEXTO) is None
    assert enviados == []

    with permissao_ollama_na_operacao(settings, True, URL, lambda: MAPA):
        assert ollama_client.gerar_texto(TEXTO) == "Resposta sobre PESSOA DE EXEMPLO."
    assert len(enviados) == 1
    _, payload = enviados[0]
    assert payload["prompt"] == "Excerto referente à [ACUSADO]."
    assert "mapa_reversao" not in payload

    assert ollama_client.gerar_texto(TEXTO) is None
    assert len(enviados) == 1


def test_critic_remoto_usa_a_mesma_politica(ambiente, monkeypatch):
    settings, enviados = ambiente
    critic = AdversarialJudicialCritic(use_ollama=True, ollama_url=URL, ollama_model="modelo-teste")
    monkeypatch.setattr(critic, "_construir_prompt_critica", lambda *_: TEXTO)
    monkeypatch.setattr(critic, "_reparar_e_parsear_json", lambda resposta: resposta)
    assert critic._avaliar_llm(None, None, None) is None
    assert enviados == []

    with permissao_ollama_na_operacao(settings, True, URL, lambda: MAPA):
        assert critic._avaliar_llm(None, None, None) == "Resposta sobre PESSOA DE EXEMPLO."
    assert len(enviados) == 1
    assert enviados[0][1]["prompt"] == "Excerto referente à [ACUSADO]."


def test_configuracao_e_destino_devem_corresponder_ao_consentimento(ambiente):
    settings, enviados = ambiente
    with pytest.raises(EnvioExternoNaoAutorizadoError, match="destino"):
        with permissao_ollama_na_operacao(settings, True, "https://outro.example.invalid", lambda: MAPA):
            pass
    settings.permitir_envio_externo = False
    with pytest.raises(EnvioExternoNaoAutorizadoError, match="desabilitado"):
        with permissao_ollama_na_operacao(settings, True, URL, lambda: MAPA):
            pass
    assert enviados == []


def test_analise_oral_pede_anuencia_e_limita_ao_job(ambiente, monkeypatch, tmp_path):
    import json
    import time

    from app import main
    from app.api.jobs import RegistroJobs
    from app.api.models import ConsentimentoOllamaRequest

    settings, enviados = ambiente
    registro = RegistroJobs()
    monkeypatch.setattr(main, "JOBS", registro)
    monkeypatch.setattr(main, "PROGRESSO", SimpleNamespace(atualizar=lambda **_: None))
    monkeypatch.setattr(main, "load_settings", lambda: settings)
    monkeypatch.setattr(main, "_cases_dir", lambda: tmp_path)
    monkeypatch.setattr(main, "_liberar_whisper", lambda: None)
    monkeypatch.setattr(main, "_analises_orais_em_disco", lambda: {})
    monkeypatch.setattr(main, "obter_contexto", lambda bundle: bundle)
    monkeypatch.setattr(main, "construir_mapa_do_contexto", lambda _: MAPA)
    monkeypatch.setattr("app.transcription.falas.analisar_midia", lambda *_, **__: ollama_client.gerar_texto(TEXTO))
    monkeypatch.setitem(main.ACTIVE_CASE_STATE, "bundle", {
        "transcriptions": [{"arquivo": "audiencia-ficticia.wav", "media_id": "ID-MIDIA-FICTICIA", "segmentos": [{"texto_literal": TEXTO}]}],
    })

    with pytest.raises(EnvioExternoNaoAutorizadoError, match="consentimento"):
        main.api_analisar_oral(refazer=False, req=None)
    assert enviados == []

    resp = main.api_analisar_oral(refazer=False, req=ConsentimentoOllamaRequest(
        consentimento_envio_externo=True, destino_ollama_confirmado=URL,
    ))
    job_id = json.loads(resp.body)["job_id"]
    ate = time.monotonic() + 2
    while main.INGESTAO_LOCK.locked() and time.monotonic() < ate:
        time.sleep(0.01)
    assert registro.obter(job_id)["status"] == "concluido"
    assert len(enviados) == 1 and enviados[0][1]["prompt"] == "Excerto referente à [ACUSADO]."
    assert ollama_client.gerar_texto(TEXTO) is None
    assert len(enviados) == 1
