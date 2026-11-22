# -*- coding: utf-8 -*-
"""Testes da resolução dinâmica de modelos Gemini e robustez da chamada (TASK-005).

Todos os testes desta suíte usam ``genai.Client`` simulado (sem acesso à rede).
"""

from __future__ import annotations

import logging
import sys
import types
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

import app.generation.gemini_integration as gi
from app.api.security import TOKEN_HEADER, get_session_token
from app.core.config import UserSettings
from app.demo.demo_case import get_demo_case_bundle
from app.generation.sentence_generator import gerar_sentenca_estruturada
from app.main import app


def _modelo(
    name: str,
    *,
    actions: Optional[List[str]] = None,
    output_token_limit: Optional[int] = 65536,
    display_name: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name if name.startswith("models/") else f"models/{name}",
        display_name=display_name or name,
        supported_actions=actions if actions is not None else ["generateContent"],
        output_token_limit=output_token_limit,
        input_token_limit=1048576,
    )


def _instalar_sdk_falso(
    monkeypatch: pytest.MonkeyPatch,
    *,
    modelos: List[SimpleNamespace],
    generate_fn: Optional[Callable[[str, Any, Any], Any]] = None,
    list_fn: Optional[Callable[[], List[SimpleNamespace]]] = None,
) -> Dict[str, Any]:
    chamadas: Dict[str, Any] = {
        "list_calls": 0,
        "generate_calls": [],
        "http_options": None,
        "api_keys": [],
    }

    class _Models:
        def list(self):
            chamadas["list_calls"] += 1
            if list_fn is not None:
                return list_fn()
            return list(modelos)

        def generate_content(self, model: str, contents: Any, config: Any = None):
            chamadas["generate_calls"].append({"model": model, "config": config})
            if generate_fn is not None:
                return generate_fn(model, contents, config)
            return SimpleNamespace(
                text="Sentença simulada OK",
                candidates=[SimpleNamespace(finish_reason="STOP")],
            )

    class _Client:
        def __init__(self, api_key: str, http_options: Any = None):
            chamadas["api_keys"].append(api_key)
            chamadas["http_options"] = http_options
            self.models = _Models()

    fake_google = types.ModuleType("google")
    fake_genai = types.ModuleType("google.genai")
    fake_types = types.ModuleType("google.genai.types")
    fake_types.GenerateContentConfig = lambda **kw: SimpleNamespace(**kw)
    fake_types.HttpOptions = lambda **kw: SimpleNamespace(**kw)
    fake_genai.Client = _Client
    fake_genai.types = fake_types
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)
    gi.limpar_cache_modelos_gemini()
    return chamadas


@pytest.fixture(autouse=True)
def _limpar_cache():
    gi.limpar_cache_modelos_gemini()
    yield
    gi.limpar_cache_modelos_gemini()


def test_config_padrao_gemini_model_e_auto():
    cfg = UserSettings()
    assert cfg.gemini_model == "auto"
    # Settings antigos com gemini-2.5-* continuam válidos
    legado = UserSettings(gemini_model="gemini-2.5-pro")
    assert legado.gemini_model == "gemini-2.5-pro"


def test_resolver_modelo_preferido_existe(monkeypatch):
    modelos = [
        _modelo("gemini-3.0-pro", output_token_limit=65536),
        _modelo("gemini-3.0-flash", output_token_limit=65536),
    ]
    chamadas = _instalar_sdk_falso(monkeypatch, modelos=modelos)
    from google import genai  # type: ignore

    client = genai.Client(api_key="AQ." + "chave-secreta-teste-1234567890")
    res = gi.resolver_modelo_gemini(client, "gemini-3.0-flash", chave_cache="k1")
    assert res["modelo_solicitado"] == "gemini-3.0-flash"
    assert res["modelo_utilizado"] == "gemini-3.0-flash"
    assert res["motivo_troca"] is None
    assert chamadas["list_calls"] == 1
    # Segunda chamada usa cache (1 hora)
    res2 = gi.resolver_modelo_gemini(client, "gemini-3.0-flash", chave_cache="k1")
    assert res2["modelo_utilizado"] == "gemini-3.0-flash"
    assert chamadas["list_calls"] == 1


def test_resolver_modelo_preferido_ausente_faz_troca_e_ignora_preview_e_tts(monkeypatch):
    modelos = [
        _modelo("gemini-4.0-pro-preview", output_token_limit=65536),
        _modelo("gemini-3.5-flash-tts", output_token_limit=8192),
        _modelo("gemini-3.5-pro-image", output_token_limit=8192),
        _modelo("gemini-3.5-pro-exp", output_token_limit=65536),
        _modelo("gemini-3.0-flash", output_token_limit=65536),
        _modelo("gemini-3.0-pro", output_token_limit=65536),
        _modelo("text-embedding-004", actions=["embedContent"]),
    ]
    _instalar_sdk_falso(monkeypatch, modelos=modelos)
    from google import genai  # type: ignore

    client = genai.Client(api_key="AQ." + "chave-secreta-teste")
    res = gi.resolver_modelo_gemini(client, "gemini-2.5-pro", preferencia_familia="pro")
    assert res["modelo_solicitado"] == "gemini-2.5-pro"
    assert res["modelo_utilizado"] == "gemini-3.0-pro"
    assert res["motivo_troca"] is not None
    assert "gemini-2.5-pro" in res["motivo_troca"]
    assert "gemini-3.0-pro" in res["motivo_troca"]

    # Preferência flash (usada na transcrição) escolhe o flash estável mais recente
    res_flash = gi.resolver_modelo_gemini(client, "auto", preferencia_familia="flash")
    assert res_flash["modelo_utilizado"] == "gemini-3.0-flash"


def test_resolver_modelo_geracao_mais_nova_vence_pro_antigo(monkeypatch):
    modelos = [
        _modelo("gemini-2.5-pro", output_token_limit=65536),
        _modelo("gemini-3.8-flash", output_token_limit=65536),
    ]
    _instalar_sdk_falso(monkeypatch, modelos=modelos)
    from google import genai  # type: ignore

    client = genai.Client(api_key="AQ." + "chave-secreta-teste")
    res = gi.resolver_modelo_gemini(client, "auto", preferencia_familia="pro")
    assert res["modelo_utilizado"] == "gemini-3.8-flash"


def test_resolver_modelo_lista_vazia_levanta_erro_claro(monkeypatch):
    _instalar_sdk_falso(monkeypatch, modelos=[_modelo("text-embedding-004", actions=["embedContent"])])
    from google import genai  # type: ignore

    client = genai.Client(api_key="AQ." + "chave-secreta-teste")
    with pytest.raises(gi.GeminiIndisponivelError) as exc_info:
        gi.resolver_modelo_gemini(client, "auto")
    assert "Nenhum modelo Gemini" in exc_info.value.mensagem


def test_chamar_gemini_max_tokens_e_safety_levantam_erro(monkeypatch):
    modelos = [_modelo("gemini-3.0-pro", output_token_limit=65536)]

    # 1) MAX_TOKENS
    _instalar_sdk_falso(
        monkeypatch,
        modelos=modelos,
        generate_fn=lambda m, c, cfg: SimpleNamespace(
            text="Texto cortado no meio...",
            candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")],
        ),
    )
    with pytest.raises(gi.GeminiIndisponivelError) as exc_mt:
        gi._chamar_gemini("prompt", "AQ." + "chave-teste-sigilosa", "auto")
    assert "cortada" in exc_mt.value.mensagem.lower() or "MAX_TOKENS" in exc_mt.value.mensagem

    # 2) SAFETY
    _instalar_sdk_falso(
        monkeypatch,
        modelos=modelos,
        generate_fn=lambda m, c, cfg: SimpleNamespace(
            text="",
            candidates=[SimpleNamespace(finish_reason="SAFETY")],
        ),
    )
    with pytest.raises(gi.GeminiIndisponivelError) as exc_sf:
        gi._chamar_gemini("prompt", "AQ." + "chave-teste-sigilosa", "auto")
    assert "SAFETY" in exc_sf.value.mensagem or "segurança" in exc_sf.value.mensagem.lower()


def test_chamar_gemini_429_faz_retry_e_sucesso(monkeypatch):
    modelos = [_modelo("gemini-3.0-pro", output_token_limit=16384)]
    tentativas = {"n": 0}
    esperas: List[float] = []

    def _gen(model, contents, config):
        tentativas["n"] += 1
        if tentativas["n"] < 3:
            raise RuntimeError("429 RESOURCE_EXHAUSTED: rate limit exceeded")
        return SimpleNamespace(
            text="Sentença completa gerada após retry",
            candidates=[SimpleNamespace(finish_reason="STOP")],
        )

    chamadas = _instalar_sdk_falso(monkeypatch, modelos=modelos, generate_fn=_gen)
    monkeypatch.setattr(gi, "_dormir", lambda s: esperas.append(s))

    texto, meta = gi._chamar_gemini_detalhado("prompt", "AQ." + "chave-teste", "auto")
    assert texto == "Sentença completa gerada após retry"
    assert tentativas["n"] == 3
    assert len(esperas) == 2
    assert esperas[1] > esperas[0]
    # output_token_limit (16384) < 32768 → respeita o teto do modelo
    assert chamadas["generate_calls"][-1]["config"].max_output_tokens == 16384
    assert chamadas["http_options"].timeout == 180_000
    assert meta["modelo_utilizado"] == "gemini-3.0-pro"


def test_chamar_gemini_404_faz_unica_nova_tentativa_com_recomendado(monkeypatch):
    modelos = [
        _modelo("gemini-2.5-pro", output_token_limit=65536),
        _modelo("gemini-3.0-pro", output_token_limit=65536),
    ]
    usados: List[str] = []

    def _gen(model, contents, config):
        usados.append(model)
        if model == "gemini-2.5-pro":
            raise RuntimeError("404 NOT_FOUND: models/gemini-2.5-pro is no longer available")
        return SimpleNamespace(
            text="Gerado pelo recomendado",
            candidates=[SimpleNamespace(finish_reason="STOP")],
        )

    _instalar_sdk_falso(monkeypatch, modelos=modelos, generate_fn=_gen)
    texto, meta = gi._chamar_gemini_detalhado("prompt", "AQ." + "chave-teste", "gemini-2.5-pro")
    assert texto == "Gerado pelo recomendado"
    assert usados == ["gemini-2.5-pro", "gemini-3.0-pro"]
    assert meta["modelo_solicitado"] == "gemini-2.5-pro"
    assert meta["modelo_utilizado"] == "gemini-3.0-pro"
    assert meta["motivo_troca"] is not None


def test_chave_nunca_vaza_em_erros_ou_logs(monkeypatch, caplog):
    chave_real = "AQ." + "Ab8RN6L_segredo_absoluto_nao_vazar_9999999999"
    modelos = [_modelo("gemini-3.0-pro")]

    def _gen(model, contents, config):
        raise RuntimeError(f"403 PERMISSION_DENIED for key={chave_real}")

    _instalar_sdk_falso(monkeypatch, modelos=modelos, generate_fn=_gen)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(gi.GeminiIndisponivelError) as exc_info:
            gi._chamar_gemini("prompt", chave_real, "auto")

    assert chave_real not in exc_info.value.mensagem
    assert chave_real not in (exc_info.value.causa or "")
    assert chave_real not in caplog.text
    assert "401/403" in exc_info.value.mensagem or "chave" in exc_info.value.mensagem.lower()


def test_gerar_sentenca_telemetria_e_motor_mostram_modelo_real(monkeypatch):
    demo = get_demo_case_bundle()
    sentenca_ref = gerar_sentenca_estruturada(demo, use_ollama_if_available=False)
    modelos = [_modelo("gemini-3.0-pro", output_token_limit=65536)]
    _instalar_sdk_falso(
        monkeypatch,
        modelos=modelos,
        generate_fn=lambda m, c, cfg: SimpleNamespace(
            text=sentenca_ref.texto_completo,
            candidates=[SimpleNamespace(finish_reason="STOP")],
        ),
    )
    settings = gi.load_settings().model_copy(update={"permitir_envio_externo": True, "gemini_model": "gemini-2.5-pro"})
    monkeypatch.setattr(gi, "load_settings", lambda: settings)

    sent, _aud, _script, tel = gi.gerar_sentenca_via_gemini_api(
        demo,
        api_key="AQ." + "chave-demo-teste",
        model_name="gemini-2.5-pro",
        pseudonimizar=False,
        consentimento_envio_externo=True,
    )
    assert tel["modelo_solicitado"] == "gemini-2.5-pro"
    assert tel["modelo_utilizado"] == "gemini-3.0-pro"
    assert tel["modelo_gemini"] == "gemini-3.0-pro"
    assert tel["motivo_troca"] is not None
    assert "gemini-3.0-pro" in sent.motor_utilizado


def test_rota_api_gemini_modelos_sem_chave_e_com_chave(monkeypatch):
    client_http = TestClient(app)
    headers = {TOKEN_HEADER: get_session_token()}

    # Sem chave
    monkeypatch.setattr(gi, "resolver_chave_gemini", lambda api_key="": "")
    resp_sem = client_http.get("/api/gemini/modelos", headers=headers)
    assert resp_sem.status_code == 200
    body_sem = resp_sem.json()
    assert body_sem["chave_configurada"] is False
    assert body_sem["modelos"] == []
    assert body_sem["recomendado"] is None

    # Com chave
    chave_falsa = "AQ." + "chave-secreta-rota-1234567890"
    monkeypatch.setattr(gi, "resolver_chave_gemini", lambda api_key="": chave_falsa)
    _instalar_sdk_falso(
        monkeypatch,
        modelos=[
            _modelo("gemini-3.0-pro", output_token_limit=65536, display_name="Gemini 3.0 Pro"),
            _modelo("gemini-3.0-flash", output_token_limit=65536, display_name="Gemini 3.0 Flash"),
        ],
    )
    resp_com = client_http.get("/api/gemini/modelos", headers=headers)
    assert resp_com.status_code == 200
    body_com = resp_com.json()
    assert body_com["chave_configurada"] is True
    assert body_com["recomendado"] == "gemini-3.0-pro"
    nomes = [m["name"] for m in body_com["modelos"]]
    assert "gemini-3.0-pro" in nomes
    assert "gemini-3.0-flash" in nomes
    assert chave_falsa not in resp_com.text


def test_chamar_gemini_504_ou_timeout_troca_para_recomendado_e_marca_apos_duas_falhas(monkeypatch):
    modelos = [
        _modelo("gemini-2.5-pro", output_token_limit=65536),
        _modelo("gemini-3.8-flash", output_token_limit=65536),
    ]
    usados: List[str] = []

    def _gen(model, contents, config):
        usados.append(model)
        if model == "gemini-2.5-pro":
            raise RuntimeError("504 DEADLINE_EXCEEDED: Deadline expired")
        return SimpleNamespace(
            text="Gerado apos fallback de 504",
            candidates=[SimpleNamespace(finish_reason="STOP")],
        )

    chamadas = _instalar_sdk_falso(monkeypatch, modelos=modelos, generate_fn=_gen)

    # 1º 504: troca para o recomendado com timeout reduzido (90s), mas NÃO marca indisponível por 1h ainda
    texto1, meta1 = gi._chamar_gemini_detalhado("prompt", "AQ." + "chave-teste", "gemini-2.5-pro")
    assert texto1 == "Gerado apos fallback de 504"
    assert usados == ["gemini-2.5-pro", "gemini-3.8-flash"]
    assert meta1["modelo_utilizado"] == "gemini-3.8-flash"
    assert "504" in (meta1["motivo_troca"] or "")
    assert chamadas["http_options"].timeout == 90_000
    assert not gi._modelo_esta_indisponivel("gemini-2.5-pro")

    # 2º 504 seguido: agora sim marca indisponível por 1h e troca novamente
    usados.clear()
    texto2, meta2 = gi._chamar_gemini_detalhado("prompt", "AQ." + "chave-teste", "gemini-2.5-pro")
    assert texto2 == "Gerado apos fallback de 504"
    assert usados == ["gemini-2.5-pro", "gemini-3.8-flash"]
    assert gi._modelo_esta_indisponivel("gemini-2.5-pro")

    # 3ª chamada: pula direto para o recomendado sem tentar o modelo marcado como indisponível
    usados.clear()
    texto3, meta3 = gi._chamar_gemini_detalhado("prompt", "AQ." + "chave-teste", "gemini-2.5-pro")
    assert texto3 == "Gerado apos fallback de 504"
    assert usados == ["gemini-3.8-flash"]
    assert meta3["modelo_utilizado"] == "gemini-3.8-flash"
    assert meta3["motivo_troca"] is not None


def test_chamar_gemini_auto_com_504_no_recomendado_troca_para_segundo_melhor(monkeypatch):
    modelos = [
        _modelo("gemini-3.8-flash", output_token_limit=65536),
        _modelo("gemini-3.7-flash", output_token_limit=65536),
    ]
    usados: List[str] = []

    def _gen(model, contents, config):
        usados.append(model)
        if model == "gemini-3.8-flash":
            raise RuntimeError("504 DEADLINE_EXCEEDED: Deadline expired")
        return SimpleNamespace(
            text="Gerado pelo segundo melhor",
            candidates=[SimpleNamespace(finish_reason="STOP")],
        )

    chamadas = _instalar_sdk_falso(monkeypatch, modelos=modelos, generate_fn=_gen)
    texto, meta = gi._chamar_gemini_detalhado("prompt", "AQ." + "chave-teste", "auto")
    assert texto == "Gerado pelo segundo melhor"
    assert usados == ["gemini-3.8-flash", "gemini-3.7-flash"]
    assert meta["modelo_solicitado"] == "auto"
    assert meta["modelo_utilizado"] == "gemini-3.7-flash"
    assert "504" in (meta["motivo_troca"] or "")
    assert "90s" in (meta["motivo_troca"] or "")
    assert chamadas["http_options"].timeout == 90_000
    # Um único 504 não bane o modelo recomendado por 1h
    assert not gi._modelo_esta_indisponivel("gemini-3.8-flash")


def test_settings_legado_gemini_2_5_tratado_como_auto(monkeypatch):
    demo = get_demo_case_bundle()
    sentenca_ref = gerar_sentenca_estruturada(demo, use_ollama_if_available=False)
    modelos = [
        _modelo("gemini-2.5-pro", output_token_limit=65536),
        _modelo("gemini-3.8-flash", output_token_limit=65536),
    ]
    usados: List[str] = []

    def _gen(model, contents, config):
        usados.append(model)
        return SimpleNamespace(
            text=sentenca_ref.texto_completo,
            candidates=[SimpleNamespace(finish_reason="STOP")],
        )

    _instalar_sdk_falso(monkeypatch, modelos=modelos, generate_fn=_gen)
    settings = gi.load_settings().model_copy(
        update={"permitir_envio_externo": True, "gemini_model": "gemini-2.5-pro"}
    )
    monkeypatch.setattr(gi, "load_settings", lambda: settings)

    _sent, _aud, _script, tel = gi.gerar_sentenca_via_gemini_api(
        demo,
        api_key="AQ." + "chave-legado-teste",
        model_name="",
        pseudonimizar=False,
        consentimento_envio_externo=True,
    )
    assert usados == ["gemini-3.8-flash"]
    assert tel["modelo_solicitado"] == "auto"
    assert tel["modelo_utilizado"] == "gemini-3.8-flash"
