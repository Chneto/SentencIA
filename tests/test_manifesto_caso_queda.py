"""Testes de queda abrupta do servidor (SystemExit / KeyboardInterrupt) durante a troca de caso."""

import asyncio
import shutil
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.core import config, session_state
from app.core.case_manifest import validar_manifesto_caso
from app.lote.manager import LOTE_MANAGER


@pytest.fixture
def ambiente_queda(tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    cases.mkdir()
    monkeypatch.setattr(main_mod, "CASES_DIR", cases)
    monkeypatch.setattr(config, "CASES_DIR", cases)
    monkeypatch.setattr(session_state, "CASES_DIR", cases)
    for chave in session_state.ACTIVE_CASE_STATE:
        monkeypatch.setitem(session_state.ACTIVE_CASE_STATE, chave, session_state.ACTIVE_CASE_STATE[chave])
    session_state.set_active_bundle(None, explicit_demo=False, upload_status={})
    return tmp_path, cases


def _preparar_caso_a_no_disco(raiz: Path, cases: Path, monkeypatch):
    """Carrega o caso fictício A e valida seu manifesto antes de simular a troca para B."""
    pdf_a = cases / "autos_a.pdf"
    pdf_a.write_bytes(b"%PDF-1.4\nautos ficticios A")
    pasta_lote = raiz / "lote_ficticio"
    pasta_lote.mkdir(exist_ok=True)
    pdf_b = pasta_lote / "autos_b.pdf"
    pdf_b.write_bytes(b"%PDF-1.4\nautos ficticios B")

    def segmentar(pdf, **kwargs):
        return [{"doc_id": "ID-FICTICIO", "titulo": "Denúncia", "conteudo": pdf.read_text(errors="ignore")}]

    def localizar(_lote, pid):
        return (pdf_a if pid == "a" else pdf_b), {"origem": "fictícia"}

    monkeypatch.setattr(main_mod, "_segmentar_pdf_seguro", segmentar)
    monkeypatch.setattr(main_mod, "_validar_pdf_arquivo", lambda _nome, _tmp: None)
    monkeypatch.setattr(LOTE_MANAGER, "localizar_pdf_processo", localizar)

    client = TestClient(main_mod.app)
    resp_a = client.post("/api/lote/ficticio/processos/a/abrir", json={"confirmar_troca": True})
    assert resp_a.status_code == 200
    manifesto_a = resp_a.json()["manifesto_caso"]
    validar_manifesto_caso(cases, manifesto_a)
    return manifesto_a, pdf_a, pdf_b


def _simular_reinicio_servidor():
    """Descarta o estado em memória e recarrega a partir da pasta de dados como no arranque."""
    with session_state.STATE_LOCK:
        session_state.ACTIVE_CASE_STATE["bundle"] = None
        session_state.ACTIVE_CASE_STATE["explicit_demo"] = False
        session_state.ACTIVE_CASE_STATE["upload_status"] = {}
        session_state.ACTIVE_CASE_STATE["last_sentence"] = None
    return session_state.restaurar_ultimo_caso_real()


def test_queda_entre_preparacao_e_publicacao_preserva_caso_a(ambiente_queda, monkeypatch):
    """Queda por SystemExit após preparação e antes da publicação mantém o caso A intacto ao reiniciar."""
    raiz, cases = ambiente_queda
    manifesto_a, pdf_a, _pdf_b = _preparar_caso_a_no_disco(raiz, cases, monkeypatch)

    def simular_queda_na_publicacao(*_args, **_kwargs):
        raise SystemExit(1)

    with monkeypatch.context() as m:
        m.setattr(main_mod, "_publicar_caso_preparado", simular_queda_na_publicacao)
        with pytest.raises(SystemExit):
            main_mod.api_abrir_processo_lote(
                "ficticio",
                "b",
                req=main_mod.AbrirProcessoLoteRequest(confirmar_troca=True),
            )

    # Reinício do processo: descarta memória e restaura do disco
    assert _simular_reinicio_servidor() is True
    bundle_restaurado = session_state.ACTIVE_CASE_STATE["bundle"]
    assert bundle_restaurado is not None
    assert bundle_restaurado["manifesto_caso"] == manifesto_a
    validar_manifesto_caso(cases, bundle_restaurado["manifesto_caso"])

    # Nenhum arquivo de B foi registrado em A nem sobrou em disco
    arquivos_em_a = {d["arquivo"] for d in bundle_restaurado["manifesto_caso"]["documentos"]}
    assert arquivos_em_a == {pdf_a.name}
    assert not (cases / "autos_b.pdf").exists()
    assert not list((cases / ".upload_tmp").iterdir())
    assert not main_mod.INGESTAO_LOCK.locked()


def test_queda_durante_copia_segundo_arquivo_no_upload_preserva_caso_a(ambiente_queda, monkeypatch):
    """Queda por KeyboardInterrupt ao copiar o 2º arquivo de B preserva A e limpa arquivos parciais de B."""
    raiz, cases = ambiente_queda
    manifesto_a, pdf_a, _pdf_b = _preparar_caso_a_no_disco(raiz, cases, monkeypatch)

    colocar_real = main_mod._colocar_arquivo
    chamadas = {"n": 0}

    def interromper_no_segundo_arquivo(tmp: Path, cases_dir: Path, filename: str):
        chamadas["n"] += 1
        if chamadas["n"] == 2:
            raise KeyboardInterrupt("queda simulada durante a cópia do 2º arquivo de B")
        return colocar_real(tmp, cases_dir, filename)

    async def _enviar_dois_arquivos_b():
        transport = httpx.ASGITransport(app=main_mod.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
            await async_client.post(
                "/api/cases/upload-files",
                files=[
                    ("files", ("autos_b_parte1.pdf", b"%PDF-1.4\nparte 1 caso B", "application/pdf")),
                    ("files", ("autos_b_parte2.pdf", b"%PDF-1.4\nparte 2 caso B", "application/pdf")),
                ],
            )

    with monkeypatch.context() as m:
        m.setattr(main_mod, "_colocar_arquivo", interromper_no_segundo_arquivo)
        with pytest.raises(KeyboardInterrupt):
            asyncio.run(_enviar_dois_arquivos_b())

    assert chamadas["n"] == 2

    # Reinício do processo: restaura A sem contaminação por B
    assert _simular_reinicio_servidor() is True
    bundle_restaurado = session_state.ACTIVE_CASE_STATE["bundle"]
    assert bundle_restaurado is not None
    assert bundle_restaurado["manifesto_caso"] == manifesto_a
    validar_manifesto_caso(cases, bundle_restaurado["manifesto_caso"])

    arquivos_em_a = {d["arquivo"] for d in bundle_restaurado["manifesto_caso"]["documentos"]}
    assert arquivos_em_a == {pdf_a.name}
    assert not (cases / "autos_b_parte1.pdf").exists()
    assert not (cases / "autos_b_parte2.pdf").exists()
    assert not list((cases / ".upload_tmp").iterdir())
    assert not main_mod.INGESTAO_LOCK.locked()


def test_queda_durante_copia_lote_shutil_copy2_preserva_caso_a(ambiente_queda, monkeypatch):
    """Queda por KeyboardInterrupt dentro de shutil.copy2 ao abrir processo B do lote preserva A."""
    raiz, cases = ambiente_queda
    manifesto_a, pdf_a, _pdf_b = _preparar_caso_a_no_disco(raiz, cases, monkeypatch)

    def copy2_interrompido(_origem, destino):
        Path(destino).write_bytes(b"copia parcial de B")
        raise KeyboardInterrupt("queda abrupta durante shutil.copy2")

    with monkeypatch.context() as m:
        m.setattr(shutil, "copy2", copy2_interrompido)
        with pytest.raises(KeyboardInterrupt):
            main_mod.api_abrir_processo_lote(
                "ficticio",
                "b",
                req=main_mod.AbrirProcessoLoteRequest(confirmar_troca=True),
            )

    assert _simular_reinicio_servidor() is True
    bundle_restaurado = session_state.ACTIVE_CASE_STATE["bundle"]
    assert bundle_restaurado is not None
    assert bundle_restaurado["manifesto_caso"] == manifesto_a
    validar_manifesto_caso(cases, bundle_restaurado["manifesto_caso"])

    arquivos_em_a = {d["arquivo"] for d in bundle_restaurado["manifesto_caso"]["documentos"]}
    assert arquivos_em_a == {pdf_a.name}
    assert not (cases / "autos_b.pdf").exists()
    assert not list((cases / ".upload_tmp").iterdir())
    assert not main_mod.INGESTAO_LOCK.locked()
