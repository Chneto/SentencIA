# -*- coding: utf-8 -*-
"""Testes do backend de pré-análise de processos em lote (Fase 5 / TASK-016)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import main as main_mod
from app.core import config as app_config
from app.core import session_state as ss
from app.lote import manager as lote_mod


def _pdf_sintetico_bytes(texto: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), texto, fontsize=11)
    raw = doc.tobytes()
    doc.close()
    return raw


def _headers() -> dict[str, str]:
    return {main_mod.TOKEN_HEADER: main_mod.get_session_token()}


def _aguardar_lote(client: TestClient, lote_id: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/lote/{lote_id}", headers=_headers())
        assert resp.status_code == 200
        dados = resp.json()
        if dados["status"] in ("concluido", "cancelado"):
            return dados
        time.sleep(0.05)
    raise AssertionError(f"Lote {lote_id} não concluiu em {timeout}s")


@pytest.fixture
def _env_lote(tmp_path: Path, monkeypatch):
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_config, "CASES_DIR", cases_dir)
    monkeypatch.setattr(ss, "CASES_DIR", cases_dir)
    monkeypatch.setattr(main_mod, "CASES_DIR", cases_dir, raising=False)
    ss.ACTIVE_CASE_STATE["bundle"] = None
    ss.ACTIVE_CASE_STATE["last_sentence"] = None
    ss.ACTIVE_CASE_STATE["explicit_demo"] = False
    yield tmp_path
    ss.ACTIVE_CASE_STATE["bundle"] = None
    ss.ACTIVE_CASE_STATE["last_sentence"] = None
    ss.ACTIVE_CASE_STATE["explicit_demo"] = False


def test_lote_3_pdfs_com_1_corrompido_nao_altera_caso_ativo_e_abre_processo(_env_lote: Path):
    client = TestClient(main_mod.app)

    # Define um caso ativo sentinela para garantir que o lote NÃO o altera
    sentinela = {"numero_processo": "SENTINELA-0001", "raw_documents": [], "transcriptions": [], "is_demo": True}
    ss.set_active_bundle(sentinela, explicit_demo=True)

    pdf1 = _pdf_sintetico_bytes(
        "PODER JUDICIARIO\nProcesso nº 0801111-11.2025.8.20.0001\n"
        "DENUNCIA\nO Ministerio Publico denuncia CARLOS ALBERTO por ameaca art. 147 do CP "
        "contra MARIA DA SILVA em 10/01/2025. Recebimento da denuncia em 15/02/2025."
    )
    pdf2 = _pdf_sintetico_bytes(
        "PODER JUDICIARIO\nProcesso nº 0802222-22.2025.8.20.0001\n"
        "BOLETIM DE OCORRENCIA\nFato relatado sem denuncia completa."
    )
    pdf_corrompido = b"%PDF-1.4 lixo-corrompido-sem-estrutura-valida"

    resp = client.post(
        "/api/lote",
        headers=_headers(),
        files=[
            ("files", ("proc1.pdf", pdf1, "application/pdf")),
            ("files", ("proc_quebrado.pdf", pdf_corrompido, "application/pdf")),
            ("files", ("proc2.pdf", pdf2, "application/pdf")),
        ],
    )
    assert resp.status_code == 200, resp.text
    info = resp.json()
    lote_id = info["lote_id"]
    assert info["total"] == 3 and info["job_id"]

    lote = _aguardar_lote(client, lote_id)
    assert lote["status"] == "concluido"
    assert lote["processados"] == 3 and lote["total"] == 3 and lote["percentual"] == 100
    assert lote["falhas"] == 1
    assert (lote["prontos"] + lote["com_pendencias"]) == 2

    # Caso ativo permaneceu intocado durante o lote
    assert ss.ACTIVE_CASE_STATE["bundle"] is sentinela

    # Lista de lotes contém o lote criado
    lista = client.get("/api/lote", headers=_headers()).json()
    assert any(item["lote_id"] == lote_id for item in lista["lotes"])

    # Dossiês gravados em data/lote/<id>/<pid>/dossie.json
    validos = [p for p in lote["processos"] if p["status"] in ("pronto", "pendencias")]
    falhos = [p for p in lote["processos"] if p["status"] == "falhou"]
    assert len(validos) == 2 and len(falhos) == 1
    assert falhos[0]["motivo"]
    pid_abrir = validos[0]["pid"]
    dossie_path = _env_lote / "lote" / lote_id / pid_abrir / "dossie.json"
    assert dossie_path.is_file()
    dossie = json.loads(dossie_path.read_text(encoding="utf-8"))
    assert dossie["lote_id"] == lote_id and isinstance(dossie["pecas_indexadas"], list)

    # Abrir processo com produção em curso exige confirmação (409)
    ss.ACTIVE_CASE_STATE["last_sentence"] = {"texto_completo": "Minuta em andamento"}
    r_409 = client.post(
        f"/api/lote/{lote_id}/processos/{pid_abrir}/abrir",
        headers=_headers(),
        json={"confirmar_troca": False},
    )
    assert r_409.status_code == 409

    # Com confirmar_troca=True, torna o processo o caso ativo
    r_ok = client.post(
        f"/api/lote/{lote_id}/processos/{pid_abrir}/abrir",
        headers=_headers(),
        json={"confirmar_troca": True},
    )
    assert r_ok.status_code == 200, r_ok.text
    assert ss.ACTIVE_CASE_STATE["bundle"] is not sentinela
    assert ss.ACTIVE_CASE_STATE["bundle"] is not None
    assert ss.ACTIVE_CASE_STATE["explicit_demo"] is False


def test_lote_cancelamento_e_validacao_tipo(_env_lote: Path, monkeypatch):
    client = TestClient(main_mod.app)

    # Tipo inválido -> 415
    r_415 = client.post(
        "/api/lote",
        headers=_headers(),
        files=[("files", ("nota.txt", b"oi", "text/plain"))],
    )
    assert r_415.status_code == 415

    # Cancelamento após o primeiro item
    primeiro_iniciou = threading.Event()
    liberar_primeiro = threading.Event()
    orig_analisar = lote_mod.analisar_pdf_para_dossie
    chamadas = {"n": 0}

    def slow_analisar(*args, **kwargs):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            primeiro_iniciou.set()
            liberar_primeiro.wait(timeout=5.0)
        return orig_analisar(*args, **kwargs)

    monkeypatch.setattr(lote_mod, "analisar_pdf_para_dossie", slow_analisar)

    pdf = _pdf_sintetico_bytes("Processo nº 0803333-33.2025.8.20.0001\nTexto sintetico.")
    r = client.post(
        "/api/lote",
        headers=_headers(),
        files=[
            ("files", ("a.pdf", pdf, "application/pdf")),
            ("files", ("b.pdf", pdf, "application/pdf")),
            ("files", ("c.pdf", pdf, "application/pdf")),
        ],
    )
    assert r.status_code == 200
    lote_id = r.json()["lote_id"]
    assert primeiro_iniciou.wait(timeout=5.0)

    r_cancel = client.post(f"/api/lote/{lote_id}/cancelar", headers=_headers())
    assert r_cancel.status_code == 200
    liberar_primeiro.set()

    lote = _aguardar_lote(client, lote_id)
    assert lote["status"] == "cancelado"
    assert lote["cancelados"] >= 1


def test_lote_sigilo_gitignore_build_release_e_reset_completo(_env_lote: Path):
    raiz_repo = Path(__file__).resolve().parent.parent
    gitignore = (raiz_repo / ".gitignore").read_text(encoding="utf-8")
    assert "data/lote/" in gitignore

    import scripts.build_release as br

    assert "data/lote" in br.PASTAS_EXCLUIDAS
    assert "data/lote/" in br.PROIBIDOS
    problemas = br.verificar_lista(["data/lote/x/dossie.json"])
    assert any("data/lote" in p for p in problemas), problemas

    # Reset completo arquiva data/lote/ em _arquivados/ e remove data/lote/
    arq_lote = _env_lote / "lote" / "lote_teste" / "proc_1" / "dossie.json"
    arq_lote.parent.mkdir(parents=True, exist_ok=True)
    arq_lote.write_text('{"pid": "proc_1"}', encoding="utf-8")

    ss.reset_session_state(mode="completo")
    assert not (_env_lote / "lote").exists()
    zips = list((_env_lote / "cases" / "_arquivados").glob("*_lote.zip"))
    assert len(zips) == 1



def test_lote_streaming_limite_tamanho_e_sem_residuo(_env_lote: Path, monkeypatch):
    from types import SimpleNamespace

    client = TestClient(main_mod.app)
    # Limite de 1 MB por arquivo para testar aborto em streaming sem deixar resíduo em _entrada/
    monkeypatch.setattr(main_mod, "load_settings", lambda: SimpleNamespace(max_upload_mb_pdf=1))
    grande = b"%PDF-1.4\n" + (b"A" * (1024 * 1024 + 64))

    r_413 = client.post(
        "/api/lote",
        headers=_headers(),
        files=[("files", ("enorme_" + "x" * 120 + ".pdf", grande, "application/pdf"))],
    )
    assert r_413.status_code == 413
    # Nenhum arquivo .part ou resíduo em data/lote/
    lote_root = _env_lote / "lote"
    residuos = [p for p in lote_root.rglob("*") if p.is_file()] if lote_root.exists() else []
    assert residuos == [], f"Resíduos encontrados após 413: {residuos}"

    # Limite de quantidade (> 50 arquivos) -> 413
    pequeno = b"%PDF-1.4\n"
    muitos = [("files", (f"p{i}.pdf", pequeno, "application/pdf")) for i in range(51)]
    r_qtd = client.post("/api/lote", headers=_headers(), files=muitos)
    assert r_qtd.status_code == 413


def test_truncar_nome_arquivo_preserva_unicidade_mesmo_prefixo_80():
    """TASK-022 (TASK-016 r2): dois arquivos com o mesmo prefixo de 80 chars continuam únicos após truncar."""
    from app.lote.manager import _truncar_nome_arquivo

    prefixo = "processo_violencia_domestica_autos_principais_" + ("a" * 50)
    assert len(prefixo) >= 80
    nome_a = f"{prefixo}_parte_01.pdf"
    nome_b = f"{prefixo}_parte_02.pdf"

    curto_a = _truncar_nome_arquivo(nome_a, max_len=80)
    curto_b = _truncar_nome_arquivo(nome_b, max_len=80)

    assert len(curto_a) <= 80
    assert len(curto_b) <= 80
    assert curto_a.endswith(".pdf") and curto_b.endswith(".pdf")
    assert curto_a != curto_b
    # Determinístico para o mesmo nome
    assert _truncar_nome_arquivo(nome_a, max_len=80) == curto_a

