"""Testes de migração e compatibilidade dos identificadores técnicos SentencIA (TASK-082).

Garante que a transição de ``JVDFM_*`` / ``X-JVDFM-*`` / ``SentencaJVDFM`` para
``SENTENCIA_*`` / ``X-SentencIA-*`` / ``SentencIA`` preserva instalações existentes
sem perda de dados nem exposição de segredos em logs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

import pytest
from fastapi.testclient import TestClient

from app.api import security as sec
from app.core import config as core_config
from app.core import env as core_env
from app.core import secrets as core_secrets


class _FakeKeyring:
    """Backend keyring em memória para testar migração sem tocar o cofre do SO."""

    def __init__(self, inicial: Optional[Dict[Tuple[str, str], str]] = None, *, falhar_set: bool = False) -> None:
        self.dados: Dict[Tuple[str, str], str] = dict(inicial or {})
        self.falhar_set = falhar_set

    def get_password(self, servico: str, nome: str) -> Optional[str]:
        return self.dados.get((servico, nome))

    def set_password(self, servico: str, nome: str, valor: str) -> None:
        if self.falhar_set:
            raise RuntimeError("falha simulada ao gravar no cofre")
        self.dados[(servico, nome)] = valor

    def delete_password(self, servico: str, nome: str) -> None:
        if (servico, nome) not in self.dados:
            raise KeyError("ausente")
        del self.dados[(servico, nome)]


class TestVariaveisAmbiente:
    def test_sentencia_tem_precedencia_sobre_jvdfm(self, caplog: pytest.LogCaptureFixture) -> None:
        core_env.reset_avisos_env_legado()
        env = {"SENTENCIA_PORT": "9100", "JVDFM_PORT": "9200"}
        with caplog.at_level(logging.WARNING, logger="app.core.env"):
            assert core_env.ler_env("PORT", "8000", env=env) == "9100"
        assert not caplog.records

    def test_legado_jvdfm_aceito_com_aviso_unico(self, caplog: pytest.LogCaptureFixture) -> None:
        core_env.reset_avisos_env_legado()
        env = {"JVDFM_PORT": "9333"}
        with caplog.at_level(logging.WARNING, logger="app.core.env"):
            assert core_env.ler_env("PORT", "8000", env=env) == "9333"
            assert core_env.ler_env("PORT", "8000", env=env) == "9333"
        avisos = [r.getMessage() for r in caplog.records if "JVDFM_PORT" in r.getMessage()]
        assert len(avisos) == 1
        assert "SENTENCIA_PORT" in avisos[0]


class TestTokenECabecalhos:
    def test_index_injeta_meta_sentencia_token(self) -> None:
        html = sec.injetar_token_html("<html><head><title>x</title></head><body></body></html>", "tok123")
        assert '<meta name="sentencia-token" content="tok123">' in html
        assert "jvdfm-token" not in html

    def test_middleware_aceita_header_novo_e_legado(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.main import app

        monkeypatch.delenv("SENTENCIA_DISABLE_TOKEN", raising=False)
        monkeypatch.delenv("JVDFM_DISABLE_TOKEN", raising=False)
        token = sec._SESSION_TOKEN

        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            r_sem = client.get("/api/status")
            assert r_sem.status_code == 401

            r_novo = client.get("/api/status", headers={sec.TOKEN_HEADER: token})
            assert r_novo.status_code == 200

            r_legado = client.get("/api/status", headers={sec.TOKEN_HEADER_LEGADO: token})
            assert r_legado.status_code == 200

    def test_query_param_novo_e_legado_aceitos_em_rota_stream_download(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.main import app

        monkeypatch.delenv("SENTENCIA_DISABLE_TOKEN", raising=False)
        monkeypatch.delenv("JVDFM_DISABLE_TOKEN", raising=False)
        token = sec._SESSION_TOKEN

        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            # Job inexistente devolve 404 (prova que passou da autenticação 401)
            r_novo = client.get(f"/api/jobs/inexistente?{sec.TOKEN_QUERY_PARAM}={token}")
            assert r_novo.status_code == 404

            r_legado = client.get(f"/api/jobs/inexistente?{sec.TOKEN_QUERY_PARAM_LEGADO}={token}")
            assert r_legado.status_code == 404

            r_sem = client.get("/api/jobs/inexistente")
            assert r_sem.status_code == 401


class TestMigracaoPastaDados:
    def test_renomeia_sentenca_jvdfm_para_sentencia(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        antiga = tmp_path / "SentencaJVDFM"
        antiga.mkdir()
        sentinela = antiga / "user_settings.json"
        sentinela.write_text('{"tema":"claro"}', encoding="utf-8")

        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        monkeypatch.setattr(core_config.sys, "platform", "win32")

        destino = core_config.pasta_dados_local_segura()
        assert destino == tmp_path / "SentencIA"
        assert destino.is_dir()
        assert not antiga.exists()
        assert (destino / "user_settings.json").read_text(encoding="utf-8") == '{"tema":"claro"}'

    def test_oserror_na_renomeacao_mantem_pasta_legada_intacta(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        antiga = tmp_path / "SentencaJVDFM"
        antiga.mkdir()
        sentinela = antiga / "arquivo.txt"
        sentinela.write_text("preservado", encoding="utf-8")

        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        monkeypatch.setattr(core_config.sys, "platform", "win32")

        def _falhar_rename(self: Path, target: Path) -> Path:
            raise OSError("arquivo em uso")

        monkeypatch.setattr(Path, "rename", _falhar_rename)

        with caplog.at_level(logging.WARNING, logger="app.core.config"):
            destino = core_config.pasta_dados_local_segura()

        assert destino == antiga
        assert sentinela.read_text(encoding="utf-8") == "preservado"
        assert any("SentencaJVDFM" in r.getMessage() for r in caplog.records)


class TestMigracaoCofreSegredos:
    def test_get_secret_migra_de_legado_para_sentencia_sem_logar_valor(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        segredo_ficticio = "CHAVE-FICTICIA-NAO-VAZAR-98765"
        fake = _FakeKeyring({(core_secrets.SERVICE_NAME_LEGADO, "gemini_api_key"): segredo_ficticio})
        monkeypatch.setattr(core_secrets, "_keyring", lambda: fake)

        with caplog.at_level(logging.DEBUG, logger="app.core.secrets"):
            obtido = core_secrets.get_secret("gemini_api_key")

        assert obtido == segredo_ficticio
        assert fake.dados.get((core_secrets.SERVICE_NAME, "gemini_api_key")) == segredo_ficticio
        assert (core_secrets.SERVICE_NAME_LEGADO, "gemini_api_key") not in fake.dados
        # Nunca expor o valor do segredo nos logs
        for rec in caplog.records:
            assert segredo_ficticio not in rec.getMessage()

    def test_get_secret_preserva_legado_se_gravacao_falhar(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        segredo_ficticio = "CHAVE-FICTICIA-PRESERVADA-123"
        fake = _FakeKeyring(
            {(core_secrets.SERVICE_NAME_LEGADO, "gemini_api_key"): segredo_ficticio},
            falhar_set=True,
        )
        monkeypatch.setattr(core_secrets, "_keyring", lambda: fake)

        assert core_secrets.get_secret("gemini_api_key") == segredo_ficticio
        assert fake.dados.get((core_secrets.SERVICE_NAME_LEGADO, "gemini_api_key")) == segredo_ficticio

    def test_delete_secret_remove_novo_e_legado(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake = _FakeKeyring(
            {
                (core_secrets.SERVICE_NAME, "gemini_api_key"): "val-novo",
                (core_secrets.SERVICE_NAME_LEGADO, "gemini_api_key"): "val-legado",
            }
        )
        monkeypatch.setattr(core_secrets, "_keyring", lambda: fake)
        monkeypatch.setattr(core_config, "DATA_DIR", tmp_path)

        core_secrets.delete_secret("gemini_api_key")
        assert not fake.dados
