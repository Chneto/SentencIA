"""Testes da API v2.0.0 (Agente E — API e Estado).

Cobre: token de sessão, Host/Origin/CORS, porta e logging; mapeamento dos erros de domínio
(422/403/503); complementação de metadados; simulação sem datas fictícias; upload em streaming
(limite, sem sobrescrever, deduplicação, lock de exclusividade, job assíncrono, falha inesperada);
cache dos motores no ``upload-status``; exportação condicionada à auditoria (rascunho com marca
d'água em ``data/exports``); cofre de segredos; demo reconhecido apenas pela flag ``is_demo``.

Isolamento total: todos os diretórios de dados apontam para ``tmp_path`` e o cofre usa o backend
de arquivo do sandbox. O cliente envia o token REAL obtido do app.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

import app.core.config as core_config
import app.core.session_state as session_state
import app.main as main_module
from app.export.nomeacao import eh_arquivo_exportacao
from app.api import security
from app.api.jobs import INGESTAO_LOCK, PROGRESSO
from app.api.security import TOKEN_HEADER, TOKEN_QUERY_PARAM, get_session_token
from app.core.case_context import obter_contexto
from app.demo.demo_case import get_demo_case_bundle
from app.main import ACTIVE_CASE_STATE, app

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

STATE_KEYS = [
    "bundle",
    "last_sentence",
    "last_audit",
    "explicit_demo",
    "last_dosimetria",
    "pipeline_engine",
    "pipeline_snapshot",
    "generation_id",
    "explicit_reset",
    "last_reset",
    "upload_status",
    "motores_cache",
    "gemini_script_mapa",
    "last_generation_origin",
]


def _carregar(nome: str) -> Dict[str, Any]:
    with open(FIXTURES_DIR / nome, encoding="utf-8") as f:
        return json.load(f)


def _veredictos(bundle: Dict[str, Any], veredicto: str = "condenar") -> Dict[str, Any]:
    crimes = list(obter_contexto(bundle).meta.crimes_imputados) or list(bundle.get("crimes_imputados") or [])
    return {"crimes_config": [{"codigo": c, **({"enquadramento_ameaca": "caput"} if c == "art_147_cp" else {}), "veredicto": veredicto} for c in crimes]}


def _pdf(texto: str = "ID-0000000 - Autos de teste") -> bytes:
    import pymupdf

    doc = pymupdf.open()
    try:
        doc.new_page().insert_text((50, 72), texto)
        return doc.tobytes()
    finally:
        doc.close()


def _bundle_sintetico_sem_datas() -> Dict[str, Any]:
    """Caso real cuja denúncia não traz número, datas nem nomes (nada pode ser inventado)."""
    return {
        "is_demo": False,
        "raw_documents": [
            {
                "doc_id": "ID-555001",
                "titulo": "Denúncia",
                "tipo": "DENUNCIA",
                "pagina_inicial": 1,
                "pagina_final": 2,
                "conteudo_resumido": "",
                "data_peca": "",
                "conteudo": (
                    "O MINISTÉRIO PÚBLICO oferece DENÚNCIA contra o acusado pela prática do crime "
                    "previsto no art. 147 do Código Penal (ameaça) no âmbito doméstico."
                ),
                "folha": "fls. 1/2",
            }
        ],
        "transcriptions": [],
    }


@pytest.fixture(autouse=True)
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dict[str, Path]:
    data_dir = tmp_path / "data"
    dirs = {
        "data_dir": data_dir,
        "cases_dir": data_dir / "cases",
        "exports_dir": data_dir / "exports",
        "logs_dir": data_dir / "logs",
        "knowledge_dir": data_dir / "knowledge_base",
        "templates_dir": data_dir / "templates_gabinete",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    settings_file = data_dir / "user_settings.json"
    settings_file.write_text(json.dumps({"comarca_nome": "COMARCA SANDBOX"}, ensure_ascii=False), encoding="utf-8")
    dirs["settings_file"] = settings_file

    monkeypatch.setattr(core_config, "DATA_DIR", data_dir)
    monkeypatch.setattr(core_config, "CASES_DIR", dirs["cases_dir"])
    monkeypatch.setattr(core_config, "EXPORTS_DIR", dirs["exports_dir"])
    monkeypatch.setattr(core_config, "LOGS_DIR", dirs["logs_dir"])
    monkeypatch.setattr(core_config, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(main_module, "CASES_DIR", dirs["cases_dir"])
    monkeypatch.setattr(main_module, "EXPORTS_DIR", dirs["exports_dir"])
    monkeypatch.setattr(session_state, "CASES_DIR", dirs["cases_dir"])
    monkeypatch.setattr(session_state, "EXPORTS_DIR", dirs["exports_dir"])
    monkeypatch.setenv("JVDFM_SECRETS_BACKEND", "arquivo")
    monkeypatch.delenv("JVDFM_DISABLE_TOKEN", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    with session_state.STATE_LOCK:
        salvo = {k: copy.deepcopy(ACTIVE_CASE_STATE.get(k)) for k in STATE_KEYS if k not in ("pipeline_engine",)}
        salvo["pipeline_engine"] = ACTIVE_CASE_STATE.get("pipeline_engine")
        ACTIVE_CASE_STATE.update(
            {
                "bundle": get_demo_case_bundle(),
                "last_sentence": None,
                "last_audit": None,
                "explicit_demo": False,
                "last_dosimetria": None,
                "pipeline_engine": None,
                "pipeline_snapshot": None,
                "generation_id": 0,
                "explicit_reset": False,
                "last_reset": None,
                "upload_status": {"pdfs_processo": [], "midias_audiencia": []},
                "motores_cache": None,
                "gemini_script_mapa": None,
                "last_generation_origin": None,
            }
        )
    PROGRESSO.reiniciar()
    try:
        yield dirs
    finally:
        with session_state.STATE_LOCK:
            ACTIVE_CASE_STATE.update(salvo)
        PROGRESSO.reiniciar()


@pytest.fixture
def client(sandbox) -> TestClient:
    with TestClient(app, headers={TOKEN_HEADER: get_session_token()}) as c:
        yield c


@pytest.fixture
def client_sem_token(sandbox) -> TestClient:
    with TestClient(app) as c:
        yield c


def _ativar(bundle: Dict[str, Any]) -> None:
    with session_state.STATE_LOCK:
        ACTIVE_CASE_STATE["bundle"] = bundle
        ACTIVE_CASE_STATE["explicit_demo"] = False


def _gravar_settings(sandbox: Dict[str, Path], **campos: Any) -> None:
    atual = json.loads(sandbox["settings_file"].read_text(encoding="utf-8"))
    atual.update(campos)
    sandbox["settings_file"].write_text(json.dumps(atual, ensure_ascii=False), encoding="utf-8")


# ==============================================================================
# Token de sessão, Host, Origin, CORS
# ==============================================================================


class TestSegurancaToken:
    def test_api_sem_token_401_e_com_token_200(self, client_sem_token: TestClient):
        for metodo, rota in (("get", "/api/cases/active"), ("get", "/api/status"), ("get", "/api/settings"), ("post", "/api/cases/load-demo")):
            resp = getattr(client_sem_token, metodo)(rota)
            assert resp.status_code == 401, rota
            assert resp.json()["erro"] == "token_invalido"
        resp = client_sem_token.get("/api/cases/active", headers={TOKEN_HEADER: get_session_token()})
        assert resp.status_code == 200

    def test_token_errado_401(self, client_sem_token: TestClient):
        assert client_sem_token.get("/api/status", headers={TOKEN_HEADER: "token-falso"}).status_code == 401

    def test_health_publico_e_minimo(self, client_sem_token: TestClient):
        resp = client_sem_token.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "versao": "2.1.0"}

    def test_index_injeta_meta_com_token(self, client_sem_token: TestClient):
        resp = client_sem_token.get("/")
        assert resp.status_code == 200
        assert f'<meta name="jvdfm-token" content="{get_session_token()}">' in resp.text
        assert resp.headers["cache-control"].startswith("no-store")

    def test_injetar_token_substitui_placeholder(self):
        html = '<html><head><meta name="jvdfm-token" content=""><title>x</title></head></html>'
        saida = security.injetar_token_html(html, "abc")
        assert saida.count('name="jvdfm-token"') == 1
        assert 'content="abc"' in saida
        assert security.injetar_token_html("<p>sem head</p>", "abc").startswith('<meta name="jvdfm-token" content="abc">')

    def test_token_em_query_so_para_get_de_download_e_progresso(self, client_sem_token: TestClient):
        q = {TOKEN_QUERY_PARAM: get_session_token()}
        assert client_sem_token.get("/api/cases/upload-progress", params=q).status_code == 200
        assert client_sem_token.get("/api/cases/active", params=q).status_code == 401
        assert client_sem_token.post("/api/cases/load-demo", params=q).status_code == 401

    def test_desativacao_por_env_somente_sob_pytest(self, client_sem_token: TestClient, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("JVDFM_DISABLE_TOKEN", "1")
        assert security.exigencia_token_desativada() is True
        assert client_sem_token.get("/api/cases/active").status_code == 200
        monkeypatch.delitem(sys.modules, "pytest")
        assert security.exigencia_token_desativada() is False


class TestSegurancaHostOrigemCors:
    def test_host_externo_recusado(self, sandbox):
        with TestClient(app, base_url="http://evil.example") as c:
            assert c.get("/api/health").status_code == 400
            assert c.get("/").status_code == 400
            assert c.get("/api/status", headers={TOKEN_HEADER: get_session_token()}).status_code == 400

    @pytest.mark.parametrize("base", ["http://localhost:8000", "http://127.0.0.1:8123", "http://[::1]:8000"])
    def test_hosts_de_loopback_aceitos(self, sandbox, base: str):
        with TestClient(app, base_url=base) as c:
            assert c.get("/api/health").status_code == 200

    def test_extrair_hostname(self):
        assert security.extrair_hostname("[::1]:8000") == "::1"
        assert security.extrair_hostname("LOCALHOST:8000") == "localhost"
        assert security.extrair_hostname("127.0.0.1") == "127.0.0.1"
        assert security.host_permitido("evil.example:8000") is False

    def test_origin_externa_403(self, client: TestClient):
        assert client.get("/api/status", headers={"Origin": "https://evil.example"}).status_code == 403
        # Mesma origem (Host do TestClient) passa; outra porta de loopback e cross-origin.
        assert client.get("/api/status", headers={"Origin": "http://testserver"}).status_code == 200
        assert client.get("/api/status", headers={"Origin": "http://127.0.0.1:8000"}).status_code == 403

    def test_sem_cors_outra_porta_de_loopback_nao_le_nada(self, client_sem_token: TestClient):
        """Outra pagina em localhost:3000 nao pode ler '/' (token) nem a API."""
        for origem in ("http://localhost:3000", "http://127.0.0.1:8080"):
            resp = client_sem_token.get("/", headers={"Origin": origem})
            assert resp.status_code == 403
            assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}
            assert get_session_token() not in resp.text
            api = client_sem_token.get("/api/cases/active", headers={"Origin": origem, TOKEN_HEADER: get_session_token()})
            assert api.status_code == 403
        pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": TOKEN_HEADER}
        local = client_sem_token.options("/api/settings", headers={"Origin": "http://localhost:8123", **pre})
        assert local.status_code != 200
        assert "access-control-allow-origin" not in {k.lower() for k in local.headers}

    @pytest.mark.parametrize("site", ["cross-site", "same-site"])
    def test_sec_fetch_site_cruzado_recusado_em_raiz_e_static(self, client_sem_token: TestClient, site: str):
        h = {"Sec-Fetch-Site": site, "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}
        assert client_sem_token.get("/", headers=h).status_code == 403
        assert client_sem_token.get("/static/js/app.js", headers=h).status_code == 403
        # Navegacao de documento (digitar/clicar um link) nao permite ler a resposta.
        nav = {"Sec-Fetch-Site": site, "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        assert client_sem_token.get("/", headers=nav).status_code == 200
        assert client_sem_token.get("/", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200

    def test_testserver_so_sob_pytest(self, monkeypatch: pytest.MonkeyPatch):
        assert security.host_permitido("testserver") is True
        monkeypatch.delitem(sys.modules, "pytest")
        assert security.host_permitido("testserver") is False
        assert security.host_permitido("127.0.0.1:8000") is True
        assert security.origem_permitida("http://testserver") is False
    def test_porta_configuravel(self, monkeypatch: pytest.MonkeyPatch):
        assert security.get_port("9123") == 9123
        assert security.get_port("abc") == 8000
        assert security.get_port("70000") == 8000
        monkeypatch.setenv("JVDFM_PORT", "8765")
        assert security.get_port() == 8765

    def test_logging_rotativo_em_arquivo(self, tmp_path: Path):
        destino = security.configurar_logging(tmp_path, forcar=True)
        try:
            assert destino == tmp_path / "app.log"
            assert security.configurar_logging(tmp_path, forcar=True) == destino  # idempotente
            logging.getLogger("app.teste").warning("registro de teste v2")
            for h in logging.getLogger().handlers:
                h.flush()
            assert "registro de teste v2" in destino.read_text(encoding="utf-8")
        finally:
            security.remover_handlers_log([destino])
        assert security.configurar_logging(tmp_path) is None  # sob pytest, sem forcar: não grava arquivo


# ==============================================================================
# Status, configurações e cofre
# ==============================================================================


class TestStatusConfiguracoesCofre:
    def test_status_detalhado_sem_settings(self, client: TestClient):
        dados = client.get("/api/status").json()
        assert {"hardware", "ollama", "laya_system1", "ocr", "transcricao", "privacidade", "session"} <= set(dados)
        assert "settings" not in dados
        assert "gpu_available" in dados["hardware"]
        assert dados["laya_system1"]["metricas"] is None  # sem telemetria fictícia
        assert dados["privacidade"]["permitir_envio_externo"] is False

    def test_settings_parcial_e_chave_legada_vai_para_o_cofre(self, client: TestClient, sandbox):
        resp = client.post("/api/settings", json={"comarca_nome": "COMARCA NOVA", "gemini_api_key": "chave-legada-123"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["settings"]["comarca_nome"] == "COMARCA NOVA"
        gravado = json.loads(sandbox["settings_file"].read_text(encoding="utf-8"))
        assert "gemini_api_key" not in gravado
        assert "chave-legada-123" not in sandbox["settings_file"].read_text(encoding="utf-8")
        assert client.get("/api/secrets/gemini").json()["configurada"] is True

    def test_settings_invalido_422(self, client: TestClient):
        resp = client.post("/api/settings", json={"whisper_device": "gpu-quantica"})
        assert resp.status_code == 422
        assert resp.json()["campos"] == ["whisper_device"]

    def test_cofre_gemini_nunca_devolve_a_chave(self, client: TestClient, sandbox):
        assert client.get("/api/secrets/gemini").json() == {"configurada": False, "backend": "arquivo"}
        assert client.post("/api/secrets/gemini", json={"api_key": "   "}).status_code == 422
        resp = client.post("/api/secrets/gemini", json={"api_key": "segredo-xyz"})
        assert resp.json() == {"configurada": True, "backend": "arquivo"}
        assert "segredo-xyz" not in client.get("/api/secrets/gemini").text
        assert (sandbox["data_dir"] / "secrets" / "secrets.json").exists()
        assert client.delete("/api/secrets/gemini").json()["configurada"] is False


# ==============================================================================
# Erros de domínio, metadados e simulação
# ==============================================================================


class TestErrosDeDominioEMetadados:
    def test_simulacao_sem_data_do_fato_422_e_complementacao(self, client: TestClient):
        _ativar(_bundle_sintetico_sem_datas())
        resp = client.post("/api/dosimetria/simulate", json={})
        assert resp.status_code == 422
        corpo = resp.json()
        assert corpo["erro"] == "dados_insuficientes"
        assert corpo["campos"] == ["data_fato"]
        assert corpo["crimes_pendentes"] == []
        assert corpo["detail"]

        ativo = client.get("/api/cases/active").json()
        assert any("Data do fato" in a for a in ativo["alertas_extracao"])

        ov = client.post("/api/cases/metadata-override", json={"metadados": {"data_fato": "10/02/2025"}})
        assert ov.status_code == 200, ov.text
        assert ov.json()["metadados_override"] == {"data_fato": "2025-02-10"}
        assert ov.json()["active_case"]["data_fato"] == "2025-02-10"

        sim = client.post("/api/dosimetria/simulate", json={})
        assert sim.status_code == 200, sim.text
        dados = sim.json()
        assert dados["data_fato"] == "2025-02-10"
        assert dados["data_recebimento_denuncia"] is None  # nunca inventada
        assert any("recebimento" in a for a in dados["alertas"])
        assert dados["simulacao_presume_condenacao"] is True
        assert dados["decisoes_crimes"][0]["presumido"] is True
        assert dados["criterio_primeira_fase"] in ("um_oitavo_intervalo", "um_sexto_minima")
        assert dados["tipo_concurso"] in ("nenhum", "material", "formal", "continuado")
        assert isinstance(dados["detracao_periodo"], str)

    def test_geracao_caso_real_dados_faltantes_e_veredicto_pendente(self, client: TestClient):
        _ativar(_bundle_sintetico_sem_datas())
        resp = client.post("/api/sentence/generate", json={"use_ollama": False})
        assert resp.status_code == 422
        assert set(resp.json()["campos"]) >= {"numero_processo", "data_fato", "data_recebimento_denuncia"}

        client.post(
            "/api/cases/metadata-override",
            json={
                "metadados": {
                    "numero_processo": "0809999-11.2025.8.20.5001",
                    "data_fato": "2025-02-10",
                    "data_recebimento_denuncia": "01/03/2025",
                    "nome_ofendida": "OFENDIDA SINTÉTICA",
                    "nome_reu": "ACUSADO SINTÉTICO",
                }
            },
        )
        pendente = client.post("/api/sentence/generate", json={"use_ollama": False})
        assert pendente.status_code == 422
        corpo = pendente.json()
        assert corpo["erro"] == "decisao_judicial_pendente"
        assert corpo["crimes_pendentes"] == ["art_147_cp"]
        assert corpo["campos"] == ["veredicto.art_147_cp"]

        ok = client.post(
            "/api/sentence/generate",
            json={"use_ollama": False, "crimes_config": [{"codigo": "art_147_cp", "veredicto": "absolver", "fundamento_absolvicao_art386": "VII"}]},
        )
        assert ok.status_code == 200, ok.text
        dados = ok.json()
        assert dados["sentenca"]["numero_processo"] == "0809999-11.2025.8.20.5001"
        assert dados["sentenca"]["decisoes_crimes"][0]["veredicto"] == "absolver"
        assert dados["simulacao_presume_condenacao"] is False
        assert dados["exportacao_liberada"] is dados["auditoria"]["aprovada_sem_alucinacao"]

    def test_metadata_override_validacoes(self, client: TestClient):
        _ativar(_bundle_sintetico_sem_datas())
        assert client.post("/api/cases/metadata-override", json={"metadados": {"campo_inexistente": 1}}).status_code == 422
        invalida = client.post("/api/cases/metadata-override", json={"metadados": {"data_fato": "31/02/2025"}})
        assert invalida.status_code == 422
        assert invalida.json()["campos"] == ["data_fato"]

        client.post("/api/cases/metadata-override", json={"metadados": {"data_fato": "2025-02-10", "crimes_imputados": "art_147_cp; art_129_p13_cp"}})
        bundle = ACTIVE_CASE_STATE["bundle"]
        assert bundle["metadados_override"]["crimes_imputados"] == ["art_147_cp", "art_129_p13_cp"]
        removido = client.post("/api/cases/metadata-override", json={"metadados": {"data_fato": None}})
        assert removido.json()["metadados_override"] == {"crimes_imputados": ["art_147_cp", "art_129_p13_cp"]}
        substituido = client.post("/api/cases/metadata-override", json={"metadados": {}, "substituir": True})
        assert substituido.json()["metadados_override"] == {}
        assert "metadados_override" not in ACTIVE_CASE_STATE["bundle"]

    def test_metadata_override_descarta_producao(self, client: TestClient):
        client.post("/api/cases/load-demo")
        assert client.post("/api/sentence/generate", json={"use_ollama": False}).status_code == 200
        assert ACTIVE_CASE_STATE["last_sentence"] is not None
        client.post("/api/cases/metadata-override", json={"metadados": {"nome_reu": "OUTRO NOME"}})
        assert ACTIVE_CASE_STATE["last_sentence"] is None
        assert ACTIVE_CASE_STATE["explicit_demo"] is True

    def test_demo_reconhecido_somente_pela_flag(self, client: TestClient):
        bundle = get_demo_case_bundle()
        assert bundle["is_demo"] is True
        sem_flag = copy.deepcopy(bundle)
        del sem_flag["is_demo"]
        # IDs ID-4100... sem a flag: tratado como caso REAL (exige veredicto).
        resp = client.post("/api/sentence/generate-sectional", json={"case_bundle": sem_flag, "use_ollama": False})
        assert resp.status_code == 422
        assert resp.json()["erro"] == "decisao_judicial_pendente"
        demo = client.post("/api/sentence/generate-sectional", json={"case_bundle": bundle, "use_ollama": False})
        assert demo.status_code == 200, demo.text
        assert ACTIVE_CASE_STATE["explicit_demo"] is True

    def test_valor_dano_moral_nao_inventado_por_padrao(self, client: TestClient):
        from app.api.models import DosimetriaSimulationRequest, GenerateSentenceRequest

        assert DosimetriaSimulationRequest().to_override() == {}
        assert GenerateSentenceRequest(valor_dano_moral=5000).to_override() == {"valor_dano_moral": 5000.0}
        assert GenerateSentenceRequest(custom_dosimetria_override={"reincidente": True}, reincidente=False).to_override() == {
            "reincidente": False
        }


class TestGeminiPrivacidade:
    def test_gemini_sem_permissao_403(self, client: TestClient, sandbox):
        client.post("/api/cases/load-demo")
        resp = client.post("/api/gemini/generate", json={"api_key": "chave-teste", "consentimento_envio_externo": True})
        assert resp.status_code == 403
        assert resp.json()["erro"] == "envio_externo_nao_autorizado"

        _gravar_settings(sandbox, permitir_envio_externo=True)
        sem_consentimento = client.post("/api/gemini/generate", json={"api_key": "chave-teste"})
        assert sem_consentimento.status_code == 403

    def test_gemini_indisponivel_503(self, client: TestClient, sandbox, monkeypatch: pytest.MonkeyPatch):
        from app.generation import gemini_integration

        def _falha(*_a, **_k):
            raise gemini_integration.GeminiIndisponivelError("Gemini indisponível no teste", "sem rede")

        monkeypatch.setattr(gemini_integration, "_chamar_gemini", _falha)
        _gravar_settings(sandbox, permitir_envio_externo=True)
        client.post("/api/cases/load-demo")
        resp = client.post("/api/gemini/generate", json={"api_key": "chave-teste", "consentimento_envio_externo": True})
        assert resp.status_code == 503
        assert resp.json()["erro"] == "gemini_indisponivel"
        assert ACTIVE_CASE_STATE["last_sentence"] is None

    def test_import_hash_de_mapa_invalido_422(self, client: TestClient):
        client.post("/api/cases/load-demo")
        resp = client.post(
            "/api/gemini/import-sentence",
            json={"texto_sentenca": "I – RELATÓRIO\nTexto.", "mapa_reversao": {"[OFENDIDA]": "X"}, "mapa_hash": "hash-que-nao-confere"},
        )
        assert resp.status_code == 422


# ==============================================================================
# Upload em streaming, jobs e upload-status
# ==============================================================================


class TestUpload:
    def _upload(self, client: TestClient, nome: str, conteudo: bytes, **data: Any):
        tipo = "application/pdf" if nome.endswith(".pdf") else "application/octet-stream"
        return client.post("/api/cases/upload-files", files=[("files", (nome, conteudo, tipo))], data=data)

    def test_extensao_nao_suportada_menciona_m4a(self, client: TestClient):
        resp = self._upload(client, "planilha.xlsx", b"abc")
        assert resp.status_code == 422
        assert ".m4a" in resp.json()["detail"]
        midia = client.post("/api/cases/upload-medias", files=[("files", ("doc.pdf", b"x", "application/pdf"))])
        assert midia.status_code == 422 and ".m4a" in midia.json()["detail"]

    def test_limite_de_tamanho_413_sem_residuo(self, client: TestClient, sandbox):
        _gravar_settings(sandbox, max_upload_mb_pdf=1)
        resp = self._upload(client, "grande.pdf", b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 10))
        assert resp.status_code == 413
        assert [p.name for p in sandbox["cases_dir"].iterdir() if p.is_file()] == []
        assert list((sandbox["cases_dir"] / ".upload_tmp").glob("*")) == []
        assert PROGRESSO.obter()["status"] == "error"
        assert INGESTAO_LOCK.locked() is False

    def test_nao_sobrescreve_e_deduplica(self, client: TestClient, sandbox, monkeypatch: pytest.MonkeyPatch):
        case2 = _carregar("case_scenario_2_descumprimento_mpu.json")
        monkeypatch.setattr(main_module, "segmentar_autos_pdf_pje", lambda _p: copy.deepcopy(case2["raw_documents"]))
        cases_dir = sandbox["cases_dir"]
        existente = cases_dir / "autos.pdf"
        existente.write_bytes(_pdf("ID-1111111 - autos ANTIGOS"))
        hash_antigo = hashlib.sha256(existente.read_bytes()).hexdigest()

        novo = _pdf("ID-2222222 - autos NOVOS")
        assert self._upload(client, "autos.pdf", novo).status_code == 200
        assert hashlib.sha256(existente.read_bytes()).hexdigest() == hash_antigo  # não sobrescrito
        segundo = cases_dir / "autos (2).pdf"
        assert segundo.read_bytes() == novo

        # Mesmo conteúdo de novo: reaproveita o arquivo existente (sem "autos (3).pdf").
        assert self._upload(client, "autos.pdf", novo).status_code == 200
        assert sorted(p.name for p in cases_dir.iterdir() if p.is_file()) == ["autos (2).pdf", "autos.pdf"]
        status = client.get("/api/cases/upload-status").json()
        assert status["upload_status"]["pdfs_processo"][0]["filename"] == "autos (2).pdf"

    def test_lock_de_exclusividade_409(self, client: TestClient):
        assert INGESTAO_LOCK.acquire(blocking=False)
        try:
            resp = self._upload(client, "autos.pdf", _pdf())
            assert resp.status_code == 409
            assert client.post("/api/cases/load-from-disk").status_code == 409
            assert client.post("/api/cases/reset", json={"mode": "completo"}).status_code == 409
            assert client.post("/api/cases/load-demo").status_code == 409
        finally:
            INGESTAO_LOCK.release()

    def test_temporarios_orfaos_removidos_na_inicializacao(self, sandbox):
        tmp_dir = sandbox["cases_dir"] / ".upload_tmp"
        tmp_dir.mkdir()
        (tmp_dir / "abc.pdf.part").write_bytes(b"parcial")
        with TestClient(app, headers={TOKEN_HEADER: get_session_token()}):
            pass
        assert list(tmp_dir.glob("*.part")) == []

    def test_falha_inesperada_nao_prende_progresso_nem_lock(self, sandbox, monkeypatch: pytest.MonkeyPatch):
        case2 = _carregar("case_scenario_2_descumprimento_mpu.json")
        monkeypatch.setattr(main_module, "segmentar_autos_pdf_pje", lambda _p: copy.deepcopy(case2["raw_documents"]))

        def _explode(*_a, **_k):
            raise RuntimeError("falha inesperada simulada")

        monkeypatch.setattr(main_module, "_construir_bundle_real", _explode)
        bundle_antes = ACTIVE_CASE_STATE["bundle"]
        with TestClient(app, headers={TOKEN_HEADER: get_session_token()}, raise_server_exceptions=False) as c:
            resp = c.post("/api/cases/upload-files", files=[("files", ("autos.pdf", _pdf(), "application/pdf"))])
        assert resp.status_code == 500
        assert resp.json()["erro"] == "erro_interno"
        progresso = PROGRESSO.obter()
        assert progresso["active"] is False and progresso["status"] == "error"
        assert INGESTAO_LOCK.locked() is False
        assert not (sandbox["cases_dir"] / "autos.pdf").exists()  # rollback do arquivo novo
        assert ACTIVE_CASE_STATE["bundle"] is bundle_antes

    def test_upload_assincrono_com_job(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        case3 = _carregar("case_scenario_3_lesao_palavra_vitima.json")
        monkeypatch.setattr(main_module, "segmentar_autos_pdf_pje", lambda _p: copy.deepcopy(case3["raw_documents"]))
        resp = self._upload(client, "autos.pdf", _pdf(), assincrono="true")
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["job_id"]
        job: Dict[str, Any] = {}
        for _ in range(200):
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("concluido", "erro"):
                break
            time.sleep(0.05)
        assert job["status"] == "concluido", job
        assert job["resultado"]["numero_processo"] == case3["numero_processo"]
        assert job["progress"]["status"] == "completed"
        assert INGESTAO_LOCK.locked() is False
        assert client.get("/api/jobs/inexistente").status_code == 404

    def test_transcricao_falha_e_consentimento_repassado(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        recebido: Dict[str, Any] = {}

        def _fake_transcrever(m_path, depoente="", papel="", known_ofendida="", known_reu="", *, consentimento_envio_externo=False):
            recebido["consentimento"] = consentimento_envio_externo
            return {
                "media_id": "ID-MIDIA-ABCDEF01", "arquivo": Path(m_path).name, "depoente": depoente, "papel": papel,
                "duracao": "", "segmentos": [], "engine": "nenhum", "status": "FALHOU",
                "erro": "nenhum motor de transcrição disponível",
            }

        monkeypatch.setattr(main_module, "transcrever_midia_audiencia", _fake_transcrever)
        caso = _carregar("case_scenario_2_descumprimento_mpu.json")
        monkeypatch.setattr(main_module, "segmentar_autos_pdf_pje", lambda _p: copy.deepcopy(caso["raw_documents"]))
        assert self._upload(client, "autos.pdf", _pdf()).status_code == 200
        resp = self._upload(client, "audiencia.m4a", b"\x00\x01audio", consentimento_envio_externo="true")
        assert resp.status_code == 200, resp.text
        assert recebido["consentimento"] is True
        midia = next(m for m in resp.json()["upload_status"]["midias_audiencia"] if m["filename"] == "audiencia.m4a")
        assert midia["status"] == "FALHOU"
        assert "nenhum motor" in midia["erro"]
        transcricao = next(t for t in resp.json()["transcricoes"] if t["arquivo"] == "audiencia.m4a")
        assert transcricao["status"] == "FALHOU"
        assert transcricao["segmentos"] == 0

    def test_upload_status_nao_recalcula_motores_a_cada_poll(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        chamadas = {"n": 0}
        original = main_module.executar_motores_py_separados

        def _contador(*a, **k):
            chamadas["n"] += 1
            return original(*a, **k)

        monkeypatch.setattr(main_module, "executar_motores_py_separados", _contador)
        client.post("/api/cases/load-demo")
        for _ in range(3):
            dados = client.get("/api/cases/upload-status").json()
            assert len(dados["motores_py"]) == 5
        assert chamadas["n"] == 1
        client.post("/api/cases/metadata-override", json={"metadados": {"nome_reu": "OUTRO"}})
        client.get("/api/cases/upload-status")
        assert chamadas["n"] == 2

    def test_upload_status_motores_com_dados_insuficientes(self, client: TestClient):
        _ativar(_bundle_sintetico_sem_datas())
        dados = client.get("/api/cases/upload-status").json()
        assert dados["motores_py"] == []
        assert dados["motores_erro"]["erro"] == "dados_insuficientes"
        assert "data_fato" in dados["motores_erro"]["campos"]


# ==============================================================================
# Exportação
# ==============================================================================


class TestExportacao:
    def test_sem_sentenca_409(self, client: TestClient):
        assert client.get("/api/export/docx").status_code == 409

    def test_aprovada_exporta_sem_marca_em_data_exports(self, client: TestClient, sandbox):
        client.post("/api/cases/load-demo")
        gerada = client.post("/api/sentence/generate", json={"use_ollama": False}).json()
        assert gerada["auditoria"]["aprovada_sem_alucinacao"] is True
        for formato in ("docx", "pdf", "html"):
            resp = client.get(f"/api/export/{formato}")
            assert resp.status_code == 200, resp.text
            assert resp.headers["x-jvdfm-marca-dagua"] == "0"
        nomes = sorted(p.name for p in sandbox["exports_dir"].iterdir())
        assert all(n.startswith("Sentença - Condenatória - Proc. ") and "RASCUNHO" not in n for n in nomes)
        assert len(nomes) == 3
        assert not any(eh_arquivo_exportacao(p.name) for p in sandbox["cases_dir"].iterdir())

    def test_nao_aprovada_exige_rascunho_com_marca(self, client: TestClient, sandbox):
        bundle = _carregar("case_scenario_1_ameaca_lesao.json")
        gerada = client.post(
            "/api/sentence/generate-sectional",
            json={"case_bundle": bundle, "use_ollama": False, "custom_dosimetria_override": _veredictos(bundle)},
        ).json()
        assert gerada["auditoria"]["aprovada_sem_alucinacao"] is False  # marcadores pendentes
        assert gerada["exportacao_liberada"] is False
        assert "MINUTA NÃO AUDITADA" in gerada["html_pje"]

        bloqueada = client.get("/api/export/docx")
        assert bloqueada.status_code == 409
        assert bloqueada.json()["erro"] == "auditoria_nao_aprovada"
        assert bloqueada.json()["marcadores_pendentes"]

        rascunho = client.get("/api/export/docx", params={"rascunho": "true"})
        assert rascunho.status_code == 200
        assert rascunho.headers["x-jvdfm-marca-dagua"] == "1"
        arquivos = [p.name for p in sandbox["exports_dir"].iterdir()]
        assert len(arquivos) == 1
        assert arquivos[0].startswith("Sentença - ") and "Proc. 0801245-78.2025.8.20.5001" in arquivos[0]
        assert arquivos[0].endswith("(RASCUNHO).docx")

        html = client.get("/api/export/html", params={"rascunho": "true"})
        assert "MINUTA NÃO AUDITADA" in html.text

    def test_token_em_query_para_download(self, client: TestClient, client_sem_token: TestClient):
        client.post("/api/cases/load-demo")
        client.post("/api/sentence/generate", json={"use_ollama": False})
        assert client_sem_token.get("/api/export/pdf").status_code == 401
        resp = client_sem_token.get("/api/export/pdf", params={TOKEN_QUERY_PARAM: get_session_token()})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"

    def test_nome_de_exportacao_sanitizado(self):
        from types import SimpleNamespace

        sent = SimpleNamespace(numero_processo="../../etc/passwd\\x", reu="Fulano: <de> Tal?", decisoes_crimes=[], texto_completo="")
        nome = main_module._nome_exportacao(sent, "docx", rascunho=False)
        assert "/" not in nome and "\\" not in nome and ".." not in nome
        assert not any(ch in nome for ch in '<>:"|?*')
        assert nome.startswith("Sentença - ") and nome.endswith(".docx")
        vazio = SimpleNamespace(numero_processo="", reu="", decisoes_crimes=[], texto_completo="")
        assert main_module._nome_exportacao(vazio, "pdf", rascunho=True).endswith("(RASCUNHO).pdf")

    def test_ultima_sentenca_recuperavel(self, client: TestClient):
        assert client.get("/api/sentence/last").status_code == 404
        client.post("/api/cases/load-demo")
        gerada = client.post("/api/sentence/generate", json={"use_ollama": False}).json()
        ultima = client.get("/api/sentence/last").json()
        assert ultima["generation_id"] == gerada["generation_id"]
        assert ultima["origem"] == "monolitica"
        assert ultima["sentenca"]["texto_completo"] == gerada["sentenca"]["texto_completo"]


# ==============================================================================
# Registro único de geração
# ==============================================================================


class TestRegistroGeracao:
    def test_register_generation_unico(self):
        class _S:
            dosimetria_dados = {"x": 1}

        with session_state.STATE_LOCK:
            ACTIVE_CASE_STATE["pipeline_engine"] = object()
            antes = ACTIVE_CASE_STATE["generation_id"]
        gid = session_state.register_generation(_S(), "auditoria", origem="monolitica")
        assert gid == antes + 1
        assert ACTIVE_CASE_STATE["pipeline_engine"] is None  # geração não seccional invalida o engine
        assert ACTIVE_CASE_STATE["last_dosimetria"] == {"x": 1}
        assert ACTIVE_CASE_STATE["last_generation_origin"] == "monolitica"

    def test_geracao_descartada_se_caso_mudou(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        client.post("/api/cases/load-demo")
        original = main_module.gerar_sentenca_estruturada

        def _troca_caso(*a, **k):
            resultado = original(*a, **k)
            with session_state.STATE_LOCK:
                ACTIVE_CASE_STATE["bundle"] = get_demo_case_bundle()  # outro objeto (troca concorrente)
            return resultado

        monkeypatch.setattr(main_module, "gerar_sentenca_estruturada", _troca_caso)
        resp = client.post("/api/sentence/generate", json={"use_ollama": False})
        assert resp.status_code == 409
        assert ACTIVE_CASE_STATE["last_sentence"] is None


# ==============================================================================
# Versões, comparação, modelos de formatação e banco de trechos (API)
# ==============================================================================


class TestVersoesFormatosTrechos:
    def test_historico_edicao_diff_e_restauracao(self, client: TestClient):
        client.post("/api/cases/load-demo")
        client.post("/api/sentence/generate", json={"use_ollama": False})
        v = client.get("/api/sentence/versions").json()["versoes"]
        assert [x["id"] for x in v] == ["v1"] and v[0]["rotulo"] == "Gerada pelo motor local"

        atual = client.get("/api/sentence/last").json()["sentenca"]
        texto = atual["texto_completo"]
        editado = texto.replace("Vistos etc.", "Vistos, relatados e examinados.")
        upd = client.post(
            "/api/sentence/update-and-audit",
            json={
                "texto_completo": editado,
                "texto_html": atual["texto_html"].replace("Vistos etc.", "Vistos, relatados e examinados."),
                "revisao_esperada": atual["revisao_documento"],
            },
        )
        assert upd.status_code == 200, upd.text
        v = client.get("/api/sentence/versions").json()["versoes"]
        assert [x["id"] for x in v] == ["v2", "v1"] and v[0]["rotulo"] == "Edição salva e re-auditada"

        diff = client.post("/api/sentence/diff", json={"de": "v1", "para": "v2"}).json()
        assert diff["resumo"]["identicos"] is False
        assert any(s["op"] == "+" and "relatados" in s["t"] for b in diff["blocos"] for s in b["segmentos"])
        contra_folha = client.post("/api/sentence/diff", json={"de": "v1", "texto_atual": texto}).json()
        assert contra_folha["resumo"]["identicos"] is True

        v1 = client.get("/api/sentence/versions/v1").json()
        assert "Vistos etc." in v1["texto_html"] and "text-align" in v1["texto_html"]
        assert client.get("/api/sentence/versions/v99").status_code == 404
        assert client.post("/api/sentence/diff", json={"de": "v99"}).status_code == 404

    def test_nova_geracao_reinicia_historico(self, client: TestClient):
        client.post("/api/cases/load-demo")
        client.post("/api/sentence/generate", json={"use_ollama": False})
        atual = client.get("/api/sentence/last").json()["sentenca"]
        client.post("/api/sentence/update-and-audit", json={
            "texto_completo": atual["texto_completo"] + "\n\nParágrafo extra.",
            "revisao_esperada": atual["revisao_documento"],
        })
        assert len(client.get("/api/sentence/versions").json()["versoes"]) == 2
        client.post("/api/sentence/generate", json={"use_ollama": False})
        assert [x["id"] for x in client.get("/api/sentence/versions").json()["versoes"]] == ["v1"]

    def test_auditoria_edicao_e_quatro_saidas_usam_mesma_revisao(self, client: TestClient):
        from io import BytesIO

        import pymupdf
        from docx import Document

        client.post("/api/cases/load-demo")
        inicial = client.post("/api/sentence/generate", json={"use_ollama": False}).json()
        folha = inicial["sentenca"]
        revisao_inicial = folha["revisao_documento"]
        assert revisao_inicial and inicial["exportacao_liberada"] is True

        formatada = client.post("/api/sentence/save-format", json={
            "texto_html": folha["texto_html"].replace("Vistos etc.", "<strong>Vistos etc.</strong>"),
            "formatacao": {"margens_cm": {"esquerda": 2.5}},
            "revisao_esperada": revisao_inicial,
        })
        assert formatada.status_code == 200, formatada.text
        revisao_formatada = formatada.json()["revisao_documento"]
        assert revisao_formatada != revisao_inicial
        assert client.get("/api/sentence/last").json()["exportacao_liberada"] is True

        novo_trecho = "Observação editorial sintética registrada."
        edicao = client.post("/api/sentence/update-and-audit", json={
            "texto_completo": folha["texto_completo"] + "\n\n" + novo_trecho,
            "texto_html": folha["texto_html"] + f"<p><strong>{novo_trecho}</strong></p>",
            "revisao_esperada": revisao_formatada,
        })
        assert edicao.status_code == 200, edicao.text
        final = edicao.json()
        assert final["exportacao_liberada"] is True
        assert f'<span style="font-weight: bold">{novo_trecho}</span>' in final["sentenca"]["texto_html"]
        revisao_final = final["sentenca"]["revisao_documento"]
        assert revisao_final not in (revisao_inicial, revisao_formatada)
        for formato in ("docx", "pdf", "html", "pje"):
            assert client.get(f"/api/export/{formato}", params={"revisao_esperada": revisao_formatada}).status_code == 409
            resp = client.get(f"/api/export/{formato}", params={"revisao_esperada": revisao_final})
            assert resp.status_code == 200, (formato, resp.text if formato in {"html", "pje"} else resp.status_code)
            if formato == "docx":
                texto = "\n".join(p.text for p in Document(BytesIO(resp.content)).paragraphs)
            elif formato == "pdf":
                with pymupdf.open(stream=resp.content, filetype="pdf") as pdf:
                    texto = "\n".join(p.get_text() for p in pdf)
            else:
                texto = resp.json()["html_pje"] if formato == "pje" else resp.text
            assert novo_trecho in texto, formato

    def test_duas_abas_nao_sobrescrevem_a_revisao_ativa(self, client: TestClient):
        client.post("/api/cases/load-demo")
        folha = client.post("/api/sentence/generate", json={"use_ollama": False}).json()["sentenca"]
        primeira_aba = folha["revisao_documento"]
        segunda_aba = client.get("/api/sentence/last").json()["sentenca"]["revisao_documento"]
        assert primeira_aba == segunda_aba
        params = {"texto_html": folha["texto_html"], "revisao_esperada": primeira_aba}
        salva = client.post("/api/sentence/save-format", json=params)
        assert salva.status_code == 200, salva.text
        nova = salva.json()["revisao_documento"]

        assert client.post("/api/sentence/save-format", json={**params, "revisao_esperada": segunda_aba}).status_code == 409
        assert client.post("/api/sentence/update-and-audit", json={
            "texto_completo": folha["texto_completo"], "revisao_esperada": segunda_aba,
        }).status_code == 409

        assert client.post("/api/sentence/save-format", json={
            "texto_html": folha["texto_html"] + "<p>Alteração de conteúdo.</p>", "revisao_esperada": nova,
        }).status_code == 409
        assert client.post("/api/sentence/update-and-audit", json={
            "texto_completo": folha["texto_completo"], "texto_html": "<p>Outro conteúdo.</p>",
            "revisao_esperada": nova,
        }).status_code == 422
        ultima = client.get("/api/sentence/last").json()
        assert ultima["sentenca"]["revisao_documento"] == nova
        assert ultima["exportacao_liberada"] is True

    def test_modelos_e_trechos_crud(self, client: TestClient, sandbox, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(core_config, "TEMPLATES_DIR", sandbox["templates_dir"])
        modelos = client.get("/api/formatos").json()["modelos"]
        assert any(m["padrao"] for m in modelos)
        r = client.post("/api/formatos", json={"nome": "Meu", "fonte": "Arial", "tamanho_pt": 11, "numerar_paginas": False})
        assert r.status_code == 200 and r.json()["modelo"]["numerar_paginas"] is False
        assert client.post("/api/formatos", json={"nome": modelos[0]["nome"]}).status_code == 422
        assert client.delete("/api/formatos", params={"nome": "Meu"}).status_code == 200
        assert client.delete("/api/formatos", params={"nome": "Meu"}).status_code == 404

        t = client.post("/api/trechos", json={"titulo": "Tese", "texto": "Texto.", "atalho": "/tese"}).json()["trecho"]
        assert t["atalho"] == "tese"
        assert client.post("/api/trechos", json={"titulo": "Outra", "texto": "x", "atalho": "tese"}).status_code == 422
        assert any(x["id"] == t["id"] for x in client.get("/api/trechos").json()["trechos"])
        assert client.delete("/api/trechos", params={"id": t["id"]}).status_code == 200
        assert client.delete("/api/trechos", params={"id": t["id"]}).status_code == 404

class TestPastaDeExportacaoEPainelDeRevisao:
    def test_pasta_invalida_e_copia_na_exportacao(self, client: TestClient, tmp_path: Path):
        from urllib.parse import unquote

        r = client.post("/api/settings", json={"pasta_exportacao": "relativa/pasta"})
        assert r.status_code == 422 and r.json()["erro"] == "pasta_exportacao_invalida"

        saida = tmp_path / "minhas sentencas"
        r = client.post("/api/settings", json={"pasta_exportacao": str(saida), "subpasta_por_processo": True})
        assert r.status_code == 200 and r.json()["settings"]["pasta_exportacao"] == str(saida.resolve())

        client.post("/api/cases/load-demo")
        client.post("/api/sentence/generate", json={"use_ollama": False})
        resp = client.get("/api/export/docx")
        assert resp.status_code == 200, resp.text
        copia = Path(unquote(resp.headers["x-jvdfm-copia-em"]))
        assert copia.exists() and copia.parent.name.startswith("Proc. ") and copia.parent.parent == saida.resolve()
        # nova exportação idêntica não duplica; o download segue funcionando
        assert client.get("/api/export/docx").status_code == 200
        assert len(list(copia.parent.glob("*.docx"))) == 1

        r = client.post("/api/settings", json={"pasta_exportacao": ""})
        assert r.status_code == 200
        assert "x-jvdfm-copia-em" not in client.get("/api/export/docx").headers

    def test_copiar_exportacao_docx_ignora_timestamp_mas_preserva_edicao(self, tmp_path: Path):
        import zipfile
        from app.export.destino import copiar_exportacao

        def _make_docx(p: Path, body: str, created: str) -> None:
            with zipfile.ZipFile(p, "w") as zf:
                zf.writestr("word/document.xml", f"<w:document>{body}</w:document>")
                zf.writestr("docProps/core.xml", f"<cp:coreProperties><dcterms:created>{created}</dcterms:created></cp:coreProperties>")

        src1 = tmp_path / "src1" / "Sentenca.docx"
        src2 = tmp_path / "src2" / "Sentenca.docx"
        src3 = tmp_path / "src3" / "Sentenca.docx"
        for s in (src1, src2, src3):
            s.parent.mkdir(parents=True, exist_ok=True)
        _make_docx(src1, "Texto da sentença", "2026-11-21T18:00:01Z")
        _make_docx(src2, "Texto da sentença", "2026-11-21T18:00:02Z")  # mesmo corpo, outro segundo
        _make_docx(src3, "Texto editado pelo juiz", "2026-11-21T18:00:03Z")

        destino = tmp_path / "saida"
        p1 = copiar_exportacao(src1, str(destino), "0800123-45.2025.8.20.5001", por_processo=True)
        p2 = copiar_exportacao(src2, str(destino), "0800123-45.2025.8.20.5001", por_processo=True)
        p3 = copiar_exportacao(src3, str(destino), "0800123-45.2025.8.20.5001", por_processo=True)

        assert p1 == p2  # mesmo conteúdo em segundos diferentes não gera "(2)"
        assert p3 != p1 and "(2)" in p3.name  # conteúdo diferente cria "(2)" sem sobrescrever

    def test_pasta_dentro_dos_dados_do_programa_e_recusada(self, client: TestClient, sandbox):
        r = client.post("/api/settings", json={"pasta_exportacao": str(sandbox["data_dir"] / "exports" / "x")})
        assert r.status_code == 422

    def test_painel_de_revisao(self, client: TestClient):
        assert client.post("/api/sentence/review", json={}).status_code == 409
        client.post("/api/cases/load-demo")
        client.post("/api/sentence/generate", json={"use_ollama": False})
        r = client.post("/api/sentence/review", json={})
        assert r.status_code == 200
        corpo = r.json()
        assert set(corpo["resumo"]) == {"obrigatorios", "recomendados", "observacoes"}
        assert all({"id", "origem", "severidade", "titulo", "base"} <= set(i) for i in corpo["itens"])
        # o texto da folha (não salvo) é o que vale
        r2 = client.post("/api/sentence/review", json={"texto_atual": "só um parágrafo [COMPLETAR PELO JUÍZO: x]"}).json()
        tipos = {i["tipo"] for i in r2["itens"]}
        assert {"marcador", "falta_relatorio", "falta_dispositivo", "autenticacao"} <= tipos
    def test_nome_do_arquivo_mantem_tipo_e_artigos_apos_edicao_reauditada(self, client: TestClient):
        from urllib.parse import unquote

        client.post("/api/cases/load-demo")
        client.post("/api/sentence/generate", json={"use_ollama": False})
        antes = unquote(client.get("/api/export/docx", params={"rascunho": "true"}).headers["content-disposition"])
        texto = client.get("/api/sentence/last").json()["sentenca"]["texto_completo"]
        revisao = client.get("/api/sentence/last").json()["sentenca"]["revisao_documento"]
        assert client.post("/api/sentence/update-and-audit", json={
            "texto_completo": texto + "\n\nParágrafo extra.", "revisao_esperada": revisao,
        }).status_code == 200
        depois = unquote(client.get("/api/export/docx", params={"rascunho": "true"}).headers["content-disposition"])
        assert "Condenatória" in antes and "Condenatória" in depois
        assert "art. " in antes and "art. " in depois  # tipos penais preservados após a re-auditoria

def test_marcos_de_recebimento_por_imputacao_sem_data_global(client):
    from tests.test_identidade_imputacoes import _denuncia, _julgado

    bundle = {
        "case_id": "CASO-FICTICIO-IMPUTACOES",
        "is_demo": False,
        "raw_documents": [
            _denuncia(),
            _julgado("ID-PRIMEIRO-ATO", "Recebo a denúncia quanto ao Fato 1.", "2024-04-12"),
            _julgado("ID-SEGUNDO-ATO", "Recebo a denúncia quanto ao Fato 2.", "2024-05-02"),
        ],
        "transcriptions": [],
    }
    _ativar(bundle)
    caso = client.get("/api/cases/active").json()
    assert caso["metadados"]["data_recebimento_denuncia"] == ""
    ids = [i["id_imputacao"] for i in caso["metadados"]["imputacoes"]]
    payload = {
        "crimes_config": [{"id_imputacao": id_, "veredicto": "condenar"} for id_ in ids],
        "data_sentenca": "2024-06-01",
    }
    resposta = client.post("/api/dosimetria/simulate", json=payload)
    assert resposta.status_code == 200, resposta.json()
    resultado = resposta.json()
    assert len(resultado["dosimetria"]["crimes_calculados"]) == 2
    assert not resultado["simulacao_presume_condenacao"]
    prescricao = resultado["prescricao"]["por_crime"]
    assert [(i["id_imputacao"], i["data_recebimento_denuncia"]) for i in prescricao] == [
        (ids[0], "2024-04-12"), (ids[1], "2024-05-02"),
    ]
    assert all(i["verificacao_completa"] for i in prescricao)


def _rotas_plano(override, texto, revisao=None, *, usar_ollama=False):
    externo = {"custom_dosimetria_override": override, "pseudonimizar": False}
    return (
        ("/api/sentence/generate", {**override, "use_ollama": False}),
        ("/api/sentence/generate-sectional", {**override, "use_ollama": usar_ollama, "usar_duplo_crivo": False}),
        ("/api/gemini/build-script", externo),
        ("/api/gemini/generate", {**externo, "consentimento_envio_externo": True}),
        ("/api/gemini/import-sentence", {**externo, "texto_sentenca": texto}),
        ("/api/sentence/update-and-audit", {**externo, "texto_completo": texto, "revisao_esperada": revisao}),
    )


@pytest.mark.parametrize("gemini_direto", [False, True])
def test_plano_e_data_do_override_sao_iguais_em_todos_os_modos(client, sandbox, monkeypatch, gemini_direto):
    from app.generation import gemini_integration, sentence_generator

    _gravar_settings(sandbox, permitir_envio_externo=True)
    monkeypatch.setattr(gemini_integration, "resolver_chave_gemini", lambda _: "chave-ficticia" if gemini_direto else "")
    bundle = _carregar("case_scenario_1_ameaca_lesao.json")
    bundle["data_sentenca"] = "2040-01-01"
    _ativar(bundle)
    override = {**_veredictos(bundle), "data_sentenca": "2025-07-08"}
    calcular = sentence_generator.resolver_dosimetria_e_decisoes
    chamadas = []

    def conferir_calculo(**kwargs):
        chamadas.append(kwargs["data_sentenca"].isoformat())
        return calcular(**kwargs)

    monkeypatch.setattr(sentence_generator, "resolver_dosimetria_e_decisoes", conferir_calculo)
    rotas = _rotas_plano(override, "")
    primeira = client.post(rotas[0][0], json=rotas[0][1])
    assert primeira.status_code == 200, primeira.json()
    local = primeira.json()["sentenca"]
    texto = local["texto_completo"]
    revisao = local["revisao_documento"]
    envios = []

    def redigir_ficticio(script, _chave, _modelo):
        envios.append(script)
        return texto, {
            "modelo_utilizado": "ficticio",
            "modelo_solicitado": "ficticio",
            "motivo_troca": None,
            "finish_reason": "STOP",
        }

    monkeypatch.setattr(gemini_integration, "_chamar_gemini_detalhado", redigir_ficticio)
    for rota, corpo in _rotas_plano(override, texto)[1:]:
        if rota.endswith("update-and-audit"):
            corpo["revisao_esperada"] = revisao
        chamadas.clear()
        resposta = client.post(rota, json=corpo)
        assert resposta.status_code == 200, (rota, resposta.json())
        assert chamadas == ["2025-07-08"], rota
        dados = resposta.json()
        if rota.endswith("build-script"):
            script = dados["script"]["script_json"]
            assert script["data_sentenca"] == "2025-07-08"
            memoria_local = dict(local["dosimetria_dados"])
            assert memoria_local.pop("decisoes_acessorias")
            assert script["dosimetria_calculada"] == memoria_local
            assert script["prescricao_calculada"] == local["prescricao_dados"]
        else:
            sentenca = dados["sentenca"]
            assert sentenca["data_prolatacao"] == "08/07/2025"
            assert sentenca["dosimetria_dados"] == local["dosimetria_dados"]
            assert sentenca["prescricao_dados"] == local["prescricao_dados"]
            revisao = sentenca["revisao_documento"]
    assert len(envios) == int(gemini_direto)


@pytest.mark.parametrize(("data_sentenca", "erro"), [
    ("2040-01-01", "decisao_judicial_pendente"),
    ("31/02/2025", "dados_insuficientes"),
])
def test_plano_bloqueia_todos_os_modos_antes_de_consultar_modelos(client, sandbox, monkeypatch, data_sentenca, erro):
    """Conserva o bloqueio existente de prescrição em abstrato, CP arts. 107, IV, e 109.
    Fonte primária conferida em 22/11/2026: https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm
    """
    from app.generation import gemini_integration, sectional_pipeline

    _gravar_settings(sandbox, permitir_envio_externo=True)
    monkeypatch.setattr(gemini_integration, "resolver_chave_gemini", lambda _: "chave-ficticia")
    monkeypatch.setattr(gemini_integration, "_chamar_gemini_detalhado", lambda *args: pytest.fail("Gemini chamado antes da liberação"))
    monkeypatch.setattr(sectional_pipeline, "resolver_modelo_ollama", lambda: pytest.fail("Ollama consultado antes da liberação"))
    bundle = _carregar("case_scenario_1_ameaca_lesao.json")
    _ativar(bundle)
    override = {**_veredictos(bundle), "data_sentenca": data_sentenca}
    respostas = []
    for rota, corpo in _rotas_plano(override, "Texto fictício.", usar_ollama=True):
        resposta = client.post(rota, json=corpo)
        assert resposta.status_code == 422, (rota, resposta.json())
        dados = resposta.json()
        assert dados["erro"] == erro, dados
        respostas.append(dados)
    assert all(r == respostas[0] for r in respostas)
    assert ACTIVE_CASE_STATE["last_sentence"] is None
