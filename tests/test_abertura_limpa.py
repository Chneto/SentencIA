# -*- coding: utf-8 -*-
"""Testes da TASK-031: abertura limpa, persistência só de caso real, --limpar-demo e isolamento de testes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import launcher
from app.api.security import token_headers
from app.core import config as core_config
from app.core import session_state
from app.core.case_flags import is_demo_bundle, is_demo_or_test_bundle
from app.core.limpeza_demo import ULTIMO_CASO_FILENAME, limpar_artefatos_demo, listar_artefatos_demo
from app.demo.demo_case import get_demo_case_bundle
from app.main import app
from tests.conftest import REPO_ROOT, verificar_data_dir_isolado


def _bundle_real_ficticio(numero: str = "0807111-11.2025.8.20.0001") -> dict:
    return {
        "case_id": f"REAL-{numero}",
        "is_demo": False,
        "numero_processo": numero,
        "ofendida": "MARIA FICTICIA",
        "reu": "JOSE FICTICIO",
        "raw_documents": [
            {
                "id": "ID-900100",
                "title": "Denúncia",
                "text": "O Ministério Público oferece denúncia em face de JOSE FICTICIO pelo art. 147 do CP.",
                "date": "10/03/2025",
            }
        ],
        "transcriptions": [],
    }


def test_salvaguarda_falha_se_tentar_usar_pasta_de_dados_real():
    """Critério de aceite: a suíte falha se tentar usar a pasta de dados real do repositório."""
    verificar_data_dir_isolado(core_config.DATA_DIR)
    assert core_config.DATA_DIR.resolve() != (REPO_ROOT / "data").resolve()

    with pytest.raises(AssertionError, match="pasta de dados real"):
        verificar_data_dir_isolado(REPO_ROOT / "data")
    with pytest.raises(AssertionError, match="pasta de dados real"):
        verificar_data_dir_isolado(REPO_ROOT / "data" / "cases")


def test_case_flags_distingue_demo_teste_e_real():
    demo = get_demo_case_bundle()
    assert is_demo_bundle(demo) is True
    assert is_demo_or_test_bundle(demo) is True

    assert is_demo_or_test_bundle({"case_id": "TEST-01", "is_demo": False}) is True
    assert is_demo_or_test_bundle({"case_id": "E2E-01", "is_demo": False}) is True
    assert is_demo_or_test_bundle({"case_id": "REAL-1", "is_test": True}) is True
    assert is_demo_or_test_bundle({"case_id": "REAL-1", "is_simulation": True}) is True
    assert is_demo_or_test_bundle({"case_id": "REAL-1", "origem": "demo"}) is True

    # Nunca inferir demo por ID-4100
    real_4100 = _bundle_real_ficticio()
    real_4100["raw_documents"][0]["id"] = "ID-4100001"
    assert is_demo_bundle(real_4100) is False
    assert is_demo_or_test_bundle(real_4100) is False


def test_restauracao_ignora_demo_e_teste_e_restaura_apenas_caso_real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(session_state, "CASES_DIR", cases_dir)
    session_state.set_active_bundle(None, explicit_demo=False)

    # 1) Sem arquivo -> abre limpo
    assert session_state.restaurar_ultimo_caso_real() is False
    assert session_state.ACTIVE_CASE_STATE["bundle"] is None

    # 2) Arquivo persistido com demo -> NÃO restaura, abre limpo
    ultimo = tmp_path / ULTIMO_CASO_FILENAME
    ultimo.write_text(json.dumps({"versao": 1, "bundle": get_demo_case_bundle()}), encoding="utf-8")
    assert session_state.restaurar_ultimo_caso_real() is False
    assert session_state.ACTIVE_CASE_STATE["bundle"] is None

    # 3) Arquivo persistido com marcador de teste -> NÃO restaura, abre limpo
    ultimo.write_text(
        json.dumps({"versao": 1, "bundle": {"case_id": "E2E-99", "is_demo": False, "is_test": True}}),
        encoding="utf-8",
    )
    assert session_state.restaurar_ultimo_caso_real() is False
    assert session_state.ACTIVE_CASE_STATE["bundle"] is None

    # 4) Salvar caso real via set_active_bundle -> persiste e restaura
    real = _bundle_real_ficticio("0807111-11.2025.8.20.0001")
    session_state.set_active_bundle(real, explicit_demo=False)
    assert ultimo.is_file()

    # Simular reinício limpando memória
    with session_state.STATE_LOCK:
        session_state.ACTIVE_CASE_STATE["bundle"] = None
    assert session_state.restaurar_ultimo_caso_real() is True
    assert session_state.ACTIVE_CASE_STATE["bundle"]["numero_processo"] == "0807111-11.2025.8.20.0001"

    # 5) Reset completo apaga .ultimo_caso_real.json -> próxima abertura é limpa
    session_state.reset_session_state(mode="completo")
    assert not ultimo.exists()
    assert session_state.restaurar_ultimo_caso_real() is False
    assert session_state.ACTIVE_CASE_STATE["bundle"] is None


def test_limpar_demo_cli_nao_remove_nada_real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    """Critério de aceite: --limpar-demo remove só demo/teste e nunca toca em autos/dados reais."""
    data_dir = tmp_path / "data"
    cases_dir = data_dir / "cases"
    exports_dir = data_dir / "exports"
    secrets_dir = data_dir / "secrets"
    arquivados_dir = cases_dir / "_arquivados"
    lote_demo_dir = data_dir / "lote" / "lote_demo_01"
    lote_real_dir = data_dir / "lote" / "lote_real_01"
    for d in (cases_dir, exports_dir, secrets_dir, arquivados_dir, lote_demo_dir, lote_real_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Artefatos DEMO / TESTE (devem ser removidos)
    ultimo_demo = data_dir / ULTIMO_CASO_FILENAME
    ultimo_demo.write_text(json.dumps({"versao": 1, "bundle": get_demo_case_bundle()}), encoding="utf-8")
    cache_demo_nome = cases_dir / "demo_autos.pdf.pje_cache_v2.json"
    cache_demo_nome.write_text(json.dumps({"pecas": []}), encoding="utf-8")
    cache_test_flag = cases_dir / "qualquer.mp4.transcript_v2.json"
    cache_test_flag.write_text(json.dumps({"is_test": True, "texto": "teste"}), encoding="utf-8")
    (lote_demo_dir / "manifest.json").write_text(json.dumps({"is_demo": True}), encoding="utf-8")

    # Artefatos REAIS (NUNCA podem ser tocados)
    pdf_real = cases_dir / "0807111-11.2025.8.20.0001.pdf"
    pdf_real.write_bytes(b"%PDF-1.4 real")
    cache_real = cases_dir / "0807111-11.2025.8.20.0001.pdf.pje_cache_v2.json"
    cache_real.write_text(json.dumps({"is_demo": False, "pecas": [{"id": "ID-1"}]}), encoding="utf-8")
    export_real = exports_dir / "Sentença - Condenatória.docx"
    export_real.write_bytes(b"PK\x03\x04")
    secret_real = secrets_dir / "secrets.json"
    secret_real.write_text(json.dumps({"gemini_api_key": "fake"}), encoding="utf-8")
    settings_real = data_dir / "user_settings.json"
    settings_real.write_text(json.dumps({"comarca_nome": "Natal"}), encoding="utf-8")
    arquivo_morto = arquivados_dir / "20261121_120000.zip"
    arquivo_morto.write_bytes(b"PK\x05\x06")
    lote_real_manifest = lote_real_dir / "manifest.json"
    lote_real_manifest.write_text(json.dumps({"is_demo": False, "lote_id": "real"}), encoding="utf-8")

    # Simulação lista exatamente os 4 itens de demo/teste
    previa = limpar_artefatos_demo(data_dir, simular=True)
    assert previa["total"] == 4
    assert all(p.exists() for p in (ultimo_demo, cache_demo_nome, cache_test_flag, lote_demo_dir))

    # Sem confirmar=True deve falhar
    with pytest.raises(ValueError, match="confirmar=True"):
        limpar_artefatos_demo(data_dir, simular=False, confirmar=False)

    # Via CLI launcher.py --limpar-demo --sim
    monkeypatch.setenv("SENTENCIA_DATA_DIR", str(data_dir))
    rc = launcher.main(["--limpar-demo", "--sim"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Removido(s) 4 item(ns)" in out

    # Todos os artefatos de demo/teste foram removidos
    assert not ultimo_demo.exists()
    assert not cache_demo_nome.exists()
    assert not cache_test_flag.exists()
    assert not lote_demo_dir.exists()

    # NENHUM artefato real foi tocado
    for preservado in (pdf_real, cache_real, export_real, secret_real, settings_real, arquivo_morto, lote_real_manifest):
        assert preservado.exists(), f"Artefato real foi removido indevidamente: {preservado}"

    # Segunda execução não encontra nada
    assert listar_artefatos_demo(data_dir) == []


def test_api_limpar_demo_nao_apaga_caso_real_em_memoria(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(session_state, "CASES_DIR", cases_dir)

    client = TestClient(app, headers=token_headers())

    # Com demo ativa em memória -> simular reporta demo_ativa_em_memoria=True, confirmar limpa
    r_demo = client.post("/api/cases/load-demo")
    assert r_demo.status_code == 200
    assert r_demo.json()["is_demo"] is True

    r_sim = client.post("/api/cases/limpar-demo", json={"simular": True, "confirmar": False})
    assert r_sim.status_code == 200
    assert r_sim.json()["demo_ativa_em_memoria"] is True

    r_exec = client.post("/api/cases/limpar-demo", json={"simular": False, "confirmar": True})
    assert r_exec.status_code == 200
    r_act = client.get("/api/cases/active")
    assert r_act.json()["empty"] is True

    # Com caso real ativo em memória -> limpar-demo NÃO toca no caso real
    real = _bundle_real_ficticio("0807222-22.2025.8.20.0001")
    session_state.set_active_bundle(real, explicit_demo=False)
    r_real = client.post("/api/cases/limpar-demo", json={"simular": False, "confirmar": True})
    assert r_real.status_code == 200
    assert r_real.json()["demo_ativa_em_memoria"] is False
    assert client.get("/api/cases/active").json()["numero_processo"] == "0807222-22.2025.8.20.0001"
