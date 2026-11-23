# -*- coding: utf-8 -*-
"""Regressoes da revisao adversarial v2.0.0 (area seguranca/API): CORS/Host, Ollama local, reset,
exportacao atomica, upload em streaming, presuncao de condenacao e pasta de dados sincronizada."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Dict
from unittest import mock

import httpx
import pytest
from fastapi.testclient import TestClient

import app.core.session_state as session_state
import app.main as main_module
from app.api.jobs import INGESTAO_LOCK, JOBS, PROGRESSO
from app.api.security import TOKEN_HEADER, get_session_token
from app.core import config as core_config
from app.core import ollama_client
from app.core import secrets as cofre
from app.core.case_context import _CACHE, obter_contexto
from app.main import ACTIVE_CASE_STATE, app

# Fixtures/ajudantes compartilhados com a suite de API (sandbox autouse isola data/ e o estado).
from test_api_v2 import _carregar, _gravar_settings, _pdf, _veredictos, client, client_sem_token, sandbox  # noqa: F401


# ------------------------------------------------------------------------------
# 1 / 16: Ollama local sem proxy do sistema e sem host remoto
# ------------------------------------------------------------------------------
class TestOllamaLocal:
    def test_cliente_ignora_proxy_do_ambiente_e_do_sistema(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HTTP_PROXY", "http://proxy.corp.example:3128")
        monkeypatch.setenv("http_proxy", "http://proxy.corp.example:3128")
        monkeypatch.setenv("ALL_PROXY", "http://proxy.corp.example:3128")
        monkeypatch.setattr("urllib.request.getproxies_environment", lambda: {"http": "http://proxy.corp.example:3128"})
        monkeypatch.setattr("urllib.request.getproxies_registry", lambda: {"http": "http://proxy.tribunal.local:3128"}, raising=False)
        url = httpx.URL("http://127.0.0.1:11434/api/generate")
        padrao = httpx.Client()
        try:
            # prova de que o cliente padrao do httpx entregaria o corpo ao proxy
            assert padrao._transport_for_url(url) is not padrao._transport
        finally:
            padrao.close()
        seguro = ollama_client.criar_cliente(5.0)
        try:
            assert seguro._mounts == {}
            assert seguro._transport_for_url(url) is seguro._transport
        finally:
            seguro.close()

    def test_todas_as_chamadas_usam_trust_env_false(self, sandbox, monkeypatch: pytest.MonkeyPatch):
        capturados = []

        class _Resp:
            status_code = 200

            def json(self):
                return {"response": "ok", "models": [{"name": "gemma4:e2b"}]}

        class _Cliente:
            def __init__(self, *a, **kw):
                capturados.append(kw)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, *a, **kw):
                return _Resp()

            def get(self, *a, **kw):
                return _Resp()

        monkeypatch.setattr(httpx, "Client", _Cliente)
        assert ollama_client.gerar_texto("x") == "ok"
        ollama_client.ollama_disponivel()
        ollama_client.descarregar_modelo()
        from app.generation.adversarial_critic import AdversarialJudicialCritic

        critico = AdversarialJudicialCritic(use_ollama=True)
        with mock.patch.object(critico, "_construir_prompt_critica", return_value="p"):
            critico._avaliar_llm(None, None, None)
        assert len(capturados) == 4
        assert all(kw.get("trust_env") is False for kw in capturados), capturados

    def test_settings_recusa_ollama_remoto_sem_permitir_envio_externo(self, client: TestClient):
        r = client.post("/api/settings", json={"ollama_url": "http://203.0.113.10:11434"})
        assert r.status_code == 422
        assert r.json()["erro"] == "ollama_url_externa_nao_permitida"
        assert core_config.load_settings().ollama_url == "http://127.0.0.1:11434"
        for ok in ("http://localhost:11434", "http://127.0.0.1:11500", "http://[::1]:11434"):
            assert client.post("/api/settings", json={"ollama_url": ok}).status_code == 200
        # Com permissao explicita o host remoto e aceito.
        r2 = client.post("/api/settings", json={"ollama_url": "http://203.0.113.10:11434", "permitir_envio_externo": True})
        assert r2.status_code == 200

    def test_chamada_bloqueada_em_tempo_de_execucao_se_url_remota_sem_permissao(self, sandbox, monkeypatch: pytest.MonkeyPatch):
        _gravar_settings(sandbox, ollama_url="http://203.0.113.10:11434", permitir_envio_externo=False)

        def _proibido(*a, **kw):  # pragma: no cover - nao pode ser chamado
            raise AssertionError("nenhuma conexao pode ser aberta")

        monkeypatch.setattr(httpx, "Client", _proibido)
        assert ollama_client.gerar_texto("autos sigilosos") is None
        assert ollama_client.descarregar_modelo() is False
        status = ollama_client.ollama_disponivel()
        assert status["online"] is False and "remoto" in (status["erro"] or "")
        from app.generation.adversarial_critic import AdversarialJudicialCritic

        assert AdversarialJudicialCritic(use_ollama=True)._avaliar_llm(None, None, None) is None

    def test_ollama_url_loopback(self):
        for u in ("http://127.0.0.1:11434", "http://localhost:1", "http://[::1]:11434", "http://127.1.2.3"):
            assert core_config.ollama_url_loopback(u), u
        for u in ("http://203.0.113.10:11434", "http://ollama.corp:11434", "ftp://127.0.0.1", "", "127.0.0.1:11434"):
            assert not core_config.ollama_url_loopback(u), u


# ------------------------------------------------------------------------------
# 13: reset completo nao deixa o caso anterior em /api/jobs nem no cache de contexto
# ------------------------------------------------------------------------------
class TestResetLimpaJobsECache:
    def test_reset_completo_limpa_jobs_e_cache(self, client: TestClient):
        job = JOBS.criar("upload")
        JOBS.atualizar(job["job_id"], status="concluido", resultado={"reu": "FULANA SIGILOSA"})
        obter_contexto(ACTIVE_CASE_STATE["bundle"])
        assert len(_CACHE) >= 1
        assert client.get(f"/api/jobs/{job['job_id']}").status_code == 200

        r = client.post("/api/cases/reset", json={"mode": "completo"})
        assert r.status_code == 200
        assert client.get(f"/api/jobs/{job['job_id']}").status_code == 404
        assert len(_CACHE) == 0

    def test_reset_producao_mantem_jobs(self, client: TestClient):
        job = JOBS.criar("upload")
        assert client.post("/api/cases/reset", json={"mode": "producao"}).status_code == 200
        assert client.get(f"/api/jobs/{job['job_id']}").status_code == 200


# ------------------------------------------------------------------------------
# 27: reset completo nao segura o STATE_LOCK durante o I/O pesado
# ------------------------------------------------------------------------------
class TestResetSemTravarEstado:
    def test_status_responde_durante_arquivamento(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        original = session_state._archive_case_files
        em_io = threading.Event()
        liberar = threading.Event()

        def _lento(**kw):
            em_io.set()
            liberar.wait(10)
            return original(**kw)

        monkeypatch.setattr(session_state, "_archive_case_files", _lento)
        resultado: Dict[str, Any] = {}
        t = threading.Thread(target=lambda: resultado.update(r=session_state.reset_session_state("completo")))
        t.start()
        try:
            assert em_io.wait(5)
            adquirido = session_state.STATE_LOCK.acquire(timeout=1.0)
            assert adquirido, "STATE_LOCK segurado durante o arquivamento"
            session_state.STATE_LOCK.release()
            inicio = time.perf_counter()
            assert client.get("/api/cases/active").status_code == 200
            assert time.perf_counter() - inicio < 1.5
        finally:
            liberar.set()
            t.join(10)
        assert resultado["r"]["mode"] == "completo"
        assert ACTIVE_CASE_STATE["bundle"] is None


# ------------------------------------------------------------------------------
# 8: exportacao com o arquivo aberto no Word / leitor de PDF
# ------------------------------------------------------------------------------
class TestExportacaoResiliente:
    def _gerar(self, client: TestClient):
        client.post("/api/cases/load-demo")
        assert client.post("/api/sentence/generate", json={"use_ollama": False}).status_code == 200

    def test_destino_travado_gera_nome_versionado_sem_500(self, client: TestClient, sandbox, monkeypatch: pytest.MonkeyPatch):
        self._gerar(client)
        r1 = client.get("/api/export/docx")
        assert r1.status_code == 200
        destino = next(sandbox["exports_dir"].glob("Sentença - *.docx"))
        real_replace = os.replace

        def _replace(src, dst, *a, **kw):
            if Path(dst) == destino:
                raise PermissionError(13, "arquivo aberto no Word")
            return real_replace(src, dst, *a, **kw)

        monkeypatch.setattr(main_module.os, "replace", _replace)
        monkeypatch.setattr(main_module.time, "sleep", lambda *_: None)
        r2 = client.get("/api/export/docx")
        assert r2.status_code == 200
        from urllib.parse import unquote

        assert "(2)" in unquote(r2.headers["content-disposition"])
        # o arquivo original ficou intacto e nao restou temporario
        assert destino.exists() and destino.stat().st_size > 0
        assert not [p for p in sandbox["exports_dir"].iterdir() if p.name.endswith(".tmp.docx")]

    def test_falha_de_gravacao_nao_trunca_destino_e_devolve_409(self, client: TestClient, sandbox, monkeypatch: pytest.MonkeyPatch):
        self._gerar(client)
        assert client.get("/api/export/html").status_code == 200
        destino = next(sandbox["exports_dir"].glob("Sentença - *.html"))
        antes = destino.read_bytes()

        def _quebra(*a, **kw):
            raise PermissionError(13, "negado")

        monkeypatch.setattr(main_module, "exportar_html_pje", lambda *a, **kw: "novo conteudo")
        monkeypatch.setattr(main_module.os, "replace", _quebra)
        monkeypatch.setattr(main_module.time, "sleep", lambda *_: None)
        r = client.get("/api/export/html")
        assert r.status_code == 409
        assert destino.read_bytes() == antes
        assert not [p for p in sandbox["exports_dir"].iterdir() if ".tmp" in p.name]


# ------------------------------------------------------------------------------
# 28: metadata-override durante upload assincrono
# ------------------------------------------------------------------------------
class TestMetadataOverrideDuranteIngestao:
    def test_409_enquanto_ingestao_em_andamento(self, client: TestClient):
        assert INGESTAO_LOCK.acquire(blocking=False)
        try:
            r = client.post("/api/cases/metadata-override", json={"metadados": {"data_fato": "2024-01-15"}})
            assert r.status_code == 409
        finally:
            INGESTAO_LOCK.release()


# ------------------------------------------------------------------------------
# 29 / 31: upload em streaming com limite e limpeza em falha de colocacao
# ------------------------------------------------------------------------------
class TestUploadStreaming:
    def test_limite_aborta_durante_a_leitura(self, client: TestClient, sandbox):
        _gravar_settings(sandbox, max_upload_mb_pdf=1)
        tmp_dir = sandbox["cases_dir"] / ".upload_tmp"
        corpo = b"%PDF-1.4\n" + b"0" * (30 * 1024 * 1024)

        resp = client.post("/api/cases/upload-files", files=[("files", ("grande.pdf", corpo, "application/pdf"))])
        assert resp.status_code == 413
        assert "limite de 1 MB" in resp.json()["detail"]
        assert not list(tmp_dir.glob("*"))

    def test_limite_nao_grava_o_excedente_em_disco(self, sandbox, monkeypatch: pytest.MonkeyPatch):
        """O arquivo .part nunca passa do limite (+1 bloco): nada de gravar os 30 MB inteiros."""
        _gravar_settings(sandbox, max_upload_mb_pdf=1)
        tamanhos = []
        real_unlink = Path.unlink

        def _espia(self, *a, **kw):
            if self.name.endswith(".part") and self.exists():
                tamanhos.append(self.stat().st_size)
            return real_unlink(self, *a, **kw)

        monkeypatch.setattr(Path, "unlink", _espia)
        with TestClient(app, headers={TOKEN_HEADER: get_session_token()}) as c:
            corpo = b"%PDF-1.4\n" + b"0" * (30 * 1024 * 1024)
            r = c.post("/api/cases/upload-files", files=[("files", ("grande.pdf", corpo, "application/pdf"))])
        assert r.status_code == 413
        assert tamanhos and max(tamanhos) <= 2 * 1024 * 1024 + 65536, tamanhos

    def test_campos_de_formulario_continuam_funcionando(self, client: TestClient):
        r = client.post("/api/cases/upload-pdfs", files=[("files", ("doc.txt", b"x", "text/plain"))])
        assert r.status_code == 422 and "doc.txt" in r.json()["detail"]
        r = client.post("/api/cases/upload-files", files=[("assincrono", (None, "true"))])
        assert r.status_code == 422 and "Nenhum arquivo" in r.json()["detail"]

    def test_falha_na_colocacao_desfaz_tudo_e_marca_erro(self, client: TestClient, sandbox, monkeypatch: pytest.MonkeyPatch):
        real = main_module._colocar_arquivo
        chamadas = {"n": 0}

        def _segunda_falha(tmp, cases_dir, filename):
            chamadas["n"] += 1
            if chamadas["n"] == 2:
                raise PermissionError(13, "antivirus")
            return real(tmp, cases_dir, filename)

        monkeypatch.setattr(main_module, "_colocar_arquivo", _segunda_falha)
        client = TestClient(app, headers={TOKEN_HEADER: get_session_token()}, raise_server_exceptions=False)
        r = client.post(
            "/api/cases/upload-files",
            files=[
                ("files", ("autos_novos.pdf", _pdf(), "application/pdf")),
                ("files", ("depoimento.mp3", b"\x00" * 64, "audio/mpeg")),
            ],
            data={"assincrono": "true"},
        )
        assert r.status_code == 500
        assert [p.name for p in sandbox["cases_dir"].iterdir() if p.is_file()] == []
        assert list((sandbox["cases_dir"] / ".upload_tmp").glob("*")) == []
        estado = PROGRESSO.obter()
        assert estado["status"] == "error" and estado["active"] is False
        assert INGESTAO_LOCK.locked() is False


# ------------------------------------------------------------------------------
# 11 / 12: sem CORS e Host testserver so em pytest (ver tambem test_api_v2)
# ------------------------------------------------------------------------------
class TestHostEOrigem:
    def test_sem_cors_middleware(self):
        nomes = {m.cls.__name__ for m in app.user_middleware}
        assert "CORSMiddleware" not in nomes

    def test_host_testserver_recusado_fora_do_pytest(self, monkeypatch: pytest.MonkeyPatch):
        import sys

        from app.api import security

        monkeypatch.delitem(sys.modules, "pytest")
        assert security.hosts_permitidos() == frozenset({"127.0.0.1", "localhost", "::1"})


# ------------------------------------------------------------------------------
# 17: pasta de dados fora de pastas sincronizadas
# ------------------------------------------------------------------------------
class TestPastaDeDados:
    def test_projeto_no_onedrive_usa_localappdata(self, tmp_path: Path):
        base = tmp_path / "OneDrive" / "Area de Trabalho" / "Sistema"
        base.mkdir(parents=True)
        local = tmp_path / "Local"
        destino = core_config._resolver_data_dir(
            "", base_dir=base, plataforma="win32", ambiente={"LOCALAPPDATA": str(local)}, home=tmp_path / "home"
        )
        assert destino == local / "SentencaJVDFM"

    def test_instalacao_existente_nao_e_abandonada(self, tmp_path: Path):
        base = tmp_path / "OneDrive" / "Sistema"
        (base / "data" / "cases").mkdir(parents=True)
        (base / "data" / "cases" / "autos.pdf").write_bytes(b"x")
        destino = core_config._resolver_data_dir("", base_dir=base, plataforma="win32", ambiente={}, home=tmp_path)
        assert destino == base / "data"

    def test_fora_de_pasta_sincronizada_e_env_prevalece(self, tmp_path: Path):
        base = tmp_path / "Projetos" / "Sistema"
        base.mkdir(parents=True)
        assert core_config._resolver_data_dir("", base_dir=base, ambiente={}, home=tmp_path / "h") == base / "data"
        alvo = tmp_path / "meus_dados"
        assert core_config._resolver_data_dir(str(alvo), base_dir=base) == alvo.resolve()

    def test_icloud_desktop_no_mac(self, tmp_path: Path):
        casa = tmp_path / "home"
        (casa / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Desktop").mkdir(parents=True)
        projeto = casa / "Desktop" / "Sistema"
        projeto.mkdir(parents=True)
        assert core_config.pasta_sincronizada(projeto / "data", home=casa, ambiente={})
        destino = core_config._resolver_data_dir("", base_dir=projeto, plataforma="darwin", ambiente={}, home=casa)
        assert destino == casa / "Library" / "Application Support" / "SentencaJVDFM"

    def test_cofre_em_arquivo_recusa_pasta_sincronizada(self, sandbox, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        sincronizada = tmp_path / "OneDrive" / "data"
        sincronizada.mkdir(parents=True)
        monkeypatch.setattr(core_config, "DATA_DIR", sincronizada)
        with pytest.raises(cofre.SegredoInseguroError):
            cofre.set_secret("gemini_api_key", "chave-secreta-de-teste")
        assert not (sincronizada / "secrets" / "secrets.json").exists()

    def test_api_de_segredo_devolve_409_em_pasta_sincronizada(self, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        sincronizada = tmp_path / "OneDrive" / "data"
        sincronizada.mkdir(parents=True)
        monkeypatch.setattr(core_config, "DATA_DIR", sincronizada)
        r = client.post("/api/secrets/gemini", json={"api_key": "chave-secreta-de-teste"})
        assert r.status_code == 409 and r.json()["erro"] == "cofre_inseguro"


# ------------------------------------------------------------------------------
# 39: generate-sectional aceita crimes_config no topo do JSON (mesmo contrato do generate)
# ------------------------------------------------------------------------------
class TestSectionalContrato:
    def test_crimes_config_no_topo_do_json(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        recebidos = {}

        def _espia(payload, case_bundle):
            recebidos["override"] = payload.to_override()
            raise RuntimeError("interrompe aqui")

        monkeypatch.setattr(main_module, "_executar_pipeline_seccional", _espia)
        client.post("/api/cases/load-demo")
        cfg = [{"codigo": "art_147_cp", "veredicto": "condenar", "enquadramento_ameaca": "caput"}]
        with pytest.raises(RuntimeError):
            client.post("/api/sentence/generate-sectional", json={"use_ollama": False, "crimes_config": cfg})
        assert recebidos["override"]["crimes_config"] == cfg

    def test_override_aninhado_continua_valendo(self):
        from app.api.models import GenerateSectionalRequest

        p = GenerateSectionalRequest(custom_dosimetria_override={"crimes_config": [{"codigo": "a"}]}, reincidente=True)
        assert p.to_override() == {"crimes_config": [{"codigo": "a"}], "reincidente": True}


# ------------------------------------------------------------------------------
# 0: reauditoria/importacao sem veredicto nao pode virar "aprovada e exportavel"
# ------------------------------------------------------------------------------
class TestPresuncaoDeCondenacao:
    def _caso_real(self):
        from test_api_v2 import _ativar

        bundle = _carregar("case_scenario_1_ameaca_lesao.json")
        bundle["is_demo"] = False
        _ativar(bundle)
        return bundle

    @pytest.mark.parametrize(("veredicto", "presumido"), [("", True), ("talvez", True), ("Condenar", False)])
    def test_veredicto_vazio_nao_conta_como_informado(self, veredicto, presumido):
        from app.generation.sentence_generator import planejar_sentenca

        bundle = self._caso_real()
        codigo = obter_contexto(bundle).meta.crimes_imputados[0]
        plano = planejar_sentenca(
            bundle, {"crimes_config": [{"codigo": codigo, "veredicto": veredicto}]},
            exigir_veredicto=False,
        )
        decisao = next(d for d in plano.decisoes_crimes if d["crime_code"] == codigo)
        assert decisao["presumido"] is presumido

    def test_motores_sinalizam_presuncao_com_veredicto_vazio(self):
        from app.generation.case_script_engine import executar_motores_completo

        bundle = self._caso_real()
        codigo = obter_contexto(bundle).meta.crimes_imputados[0]
        r = executar_motores_completo(bundle, {"crimes_config": [{"codigo": codigo}]}, exigir_veredicto=False)
        assert r.presume_condenacao is True

    @pytest.mark.parametrize("veredicto", [None, ""])
    def test_import_e_reauditoria_de_caso_real_sem_veredicto_422(self, client: TestClient, veredicto):
        bundle = self._caso_real()
        codigo = obter_contexto(bundle).meta.crimes_imputados[0]
        item = {"codigo": codigo}
        if veredicto is not None:
            item["veredicto"] = veredicto
        override = {"crimes_config": [item]}
        texto = "I - RELATÓRIO\nTexto.\nII - FUNDAMENTAÇÃO\nTexto.\nIII - DISPOSITIVO\nCONDENO o acusado à pena de 9 (nove) anos de reclusão."
        for rota, corpo in (
            ("/api/gemini/import-sentence", {"texto_sentenca": texto, "custom_dosimetria_override": override}),
            ("/api/sentence/update-and-audit", {"texto_completo": texto, "custom_dosimetria_override": override}),
        ):
            r = client.post(rota, json=corpo)
            assert r.status_code == 422, (rota, r.text)
            assert r.json()["erro"] == "decisao_judicial_pendente"
        assert ACTIVE_CASE_STATE["last_sentence"] is None

    def test_import_de_caso_real_com_veredicto_e_aceito_e_confere_penas(self, client: TestClient):
        bundle = self._caso_real()
        gerada = client.post("/api/sentence/generate", json={"use_ollama": False, **_veredictos(bundle)})
        assert gerada.status_code == 200
        texto = gerada.json()["sentenca"]["texto_completo"]
        ok = client.post(
            "/api/sentence/update-and-audit",
            json={
                "texto_completo": texto, "custom_dosimetria_override": _veredictos(bundle),
                "revisao_esperada": gerada.json()["sentenca"]["revisao_documento"],
            },
        )
        assert ok.status_code == 200
        assert ok.json()["simulacao_presume_condenacao"] is False

    def test_demo_continua_simulando_com_sinalizacao(self, client: TestClient):
        client.post("/api/cases/load-demo")
        gerada = client.post("/api/sentence/generate", json={"use_ollama": False}).json()
        r = client.post("/api/sentence/update-and-audit", json={
            "texto_completo": gerada["sentenca"]["texto_completo"],
            "revisao_esperada": gerada["sentenca"]["revisao_documento"],
        })
        assert r.status_code == 200
        assert r.json()["simulacao_presume_condenacao"] is True
        assert r.json()["sentenca"]["simulacao_presume_condenacao"] is True

# ------------------------------------------------------------------------------
# 38 / 10 / 0 (frontend): contratos estaticos do HTML/JS
# ------------------------------------------------------------------------------
class TestFrontendContratos:
    STATIC = Path(__file__).resolve().parent.parent / "app" / "static"

    def test_campos_numericos_aceitam_centavos(self):
        import re

        html = (self.STATIC / "index.html").read_text(encoding="utf-8")
        for campo in ("metaValorDanoMoral", "inputValorDanoMoral"):
            tag = re.search(r'<input[^>]*id="%s"[^>]*>' % campo, html).group(0)
            assert 'step="100"' not in tag and 'step="0.01"' in tag, tag
        ctx = re.search(r'<input[^>]*id="setOllamaNumCtx"[^>]*>', html).group(0)
        assert 'step="512"' not in ctx and 'step="1"' in ctx

    def _ler_todo_js(self) -> str:
        js_dir = self.STATIC / "js"
        arquivos = [js_dir / "app.js", *sorted((js_dir / "modulos").glob("*.js"))]
        return "\n".join(p.read_text(encoding="utf-8") for p in arquivos)

    def test_reauditoria_e_importacao_exigem_decisao_judicial(self):
        js = self._ler_todo_js()
        for fn in ("importarSentencaGemini", "reauditarEdicao"):
            corpo = js[js.index(f"async function {fn}()"):]
            corpo = corpo[: corpo.index("\n}\n")]
            assert f"garantirDecisaoJudicial({fn})" in corpo, fn
        assert "dosimetria_dados.simulacao_presume_condenacao" in js

    def test_decisoes_do_juizo_persistem_por_caso_na_sessao(self):
        js = self._ler_todo_js()
        assert "sessionStorage.setItem(" in js and "lerDecisoesSessao(state.caseKey)" in js