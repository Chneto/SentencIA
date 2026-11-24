"""Testes de regressão para Cache-Control no-store em /api/*, CSP e token em query mínimo (TASK-094)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import security as sec
from app.main import app


@pytest.fixture()
def client_com_token(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("SENTENCIA_DISABLE_TOKEN", raising=False)
    monkeypatch.delenv("JVDFM_DISABLE_TOKEN", raising=False)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client


class TestCacheControlECsp:
    @pytest.mark.parametrize(
        "rota",
        [
            "/api/health",
            "/api/status",
            "/api/settings",
            "/api/cases/active",
        ],
    )
    def test_api_retorna_cache_control_no_store_e_csp(
        self, client_com_token: TestClient, rota: str
    ) -> None:
        headers = {sec.TOKEN_HEADER: sec.get_session_token()}
        resp = client_com_token.get(rota, headers=headers)
        assert resp.status_code == 200
        cc = resp.headers.get("cache-control", "").lower()
        assert "no-store" in cc
        assert "no-cache" in cc
        assert resp.headers.get("pragma", "").lower() == "no-cache"
        assert resp.headers.get("expires") == "0"
        csp = resp.headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "object-src 'none'" in csp

    def test_rota_com_dados_de_processo_nao_permite_cache(
        self, client_com_token: TestClient
    ) -> None:
        headers = {sec.TOKEN_HEADER: sec.get_session_token()}
        r_demo = client_com_token.post("/api/cases/load-demo", headers=headers)
        assert r_demo.status_code == 200
        assert "no-store" in r_demo.headers.get("cache-control", "").lower()

        r_ativo = client_com_token.get("/api/cases/active", headers=headers)
        assert r_ativo.status_code == 200
        assert "no-store" in r_ativo.headers.get("cache-control", "").lower()
        assert r_ativo.headers.get("x-content-type-options") == "nosniff"
        assert r_ativo.headers.get("x-frame-options") == "DENY"

    def test_index_tem_csp_sem_unsafe_inline_em_script(
        self, client_com_token: TestClient
    ) -> None:
        resp = client_com_token.get("/")
        assert resp.status_code == 200
        csp = resp.headers.get("content-security-policy", "")
        assert "script-src 'self'" in csp
        # Garante que script-src não libera unsafe-inline nem unsafe-eval
        script_dir = next(
            (p.strip() for p in csp.split(";") if p.strip().startswith("script-src")), ""
        )
        assert "'unsafe-inline'" not in script_dir
        assert "'unsafe-eval'" not in script_dir
        assert "<script>" not in resp.text

    def test_erro_401_tambem_inclui_no_store_e_csp(
        self, client_com_token: TestClient
    ) -> None:
        resp = client_com_token.get("/api/cases/active")
        assert resp.status_code == 401
        assert "no-store" in resp.headers.get("cache-control", "").lower()
        assert "default-src 'self'" in resp.headers.get("content-security-policy", "")


class TestTokenEmQueryMinimo:
    @pytest.mark.parametrize(
        "rota",
        [
            "/api/cases/upload-progress",
            "/api/jobs/job-ficticio-123",
            "/api/export/pje",
            "/api/cases/active",
            "/api/status",
        ],
    )
    def test_rotas_fetch_recusam_token_em_query(
        self, client_com_token: TestClient, rota: str
    ) -> None:
        resp = client_com_token.get(rota, params={sec.TOKEN_QUERY_PARAM: sec.get_session_token()})
        assert resp.status_code == 401

    @pytest.mark.parametrize(
        "rota",
        [
            "/api/export/docx",
            "/api/export/pdf",
            "/api/export/html",
        ],
    )
    def test_downloads_nativos_aceitam_token_em_query(
        self, client_com_token: TestClient, rota: str
    ) -> None:
        # Sem minuta gerada devolve 400/404, mas nunca 401 (autenticação aceita)
        resp = client_com_token.get(rota, params={sec.TOKEN_QUERY_PARAM: sec.get_session_token()})
        assert resp.status_code != 401
