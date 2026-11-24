"""Troca e restauração de casos em pasta temporária com autos fictícios."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.core import config, session_state
from app.lote.manager import LOTE_MANAGER


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    cases.mkdir()
    monkeypatch.setattr(main_mod, "CASES_DIR", cases)
    monkeypatch.setattr(config, "CASES_DIR", cases)
    monkeypatch.setattr(session_state, "CASES_DIR", cases)
    for chave in session_state.ACTIVE_CASE_STATE:
        monkeypatch.setitem(session_state.ACTIVE_CASE_STATE, chave, session_state.ACTIVE_CASE_STATE[chave])
    session_state.set_active_bundle(None, explicit_demo=False, upload_status={})
    return tmp_path, cases


def test_troca_do_lote_preserva_autos_e_manifesto_ao_falhar_e_restaurar(ambiente, monkeypatch):
    raiz, cases = ambiente
    pdf_a = cases / "autos.pdf"
    pdf_a.write_bytes(b"autos ficticios A")
    (cases / "gravacao.mp4").write_bytes(b"audiencia ficticia A")
    pasta_lote = raiz / "lote_ficticio"
    pasta_lote.mkdir()
    pdf_b = pasta_lote / "autos.pdf"
    pdf_b.write_bytes(b"autos ficticios B")
    (cases / "gravacao (2).mp4").write_bytes(b"audiencia ficticia B")

    def segmentar(pdf, **kwargs):
        return [{"doc_id": "ID-FICTICIO", "titulo": "Denúncia", "conteudo": pdf.read_text()}]

    def localizar(_lote, pid):
        return (pdf_a if pid == "a" else pdf_b), {"origem": "fictícia"}

    monkeypatch.setattr(main_mod, "_segmentar_pdf_seguro", segmentar)
    monkeypatch.setattr(LOTE_MANAGER, "localizar_pdf_processo", localizar)
    chamadas = []
    def transcrever(caminho, **kwargs):
        chamadas.append((caminho, kwargs["identidade_caso"]))
        return {"arquivo": caminho.name, "media_id": "ID-MIDIA-FICTICIA", "segmentos": []}

    monkeypatch.setattr(main_mod, "_transcrever", transcrever)
    client = TestClient(main_mod.app)

    def abrir(pid):
        return client.post(f"/api/lote/ficticio/processos/{pid}/abrir", json={"confirmar_troca": True})

    resp_a = abrir("a")
    assert resp_a.status_code == 200
    manifesto_a = resp_a.json()["manifesto_caso"]
    assert manifesto_a["documentos"][0]["arquivo"] == "autos.pdf"
    assert manifesto_a["midias"] == []
    assert {m["status"] for m in resp_a.json()["upload_status"]["midias_audiencia"]} == {"PENDENTE_VINCULACAO"}
    assert not chamadas
    recarga = client.post("/api/cases/load-from-disk")
    assert recarga.status_code == 200  # recarrega os PDFs registrados, sem escolher por data
    manual = client.post(
        "/api/cases/upload-medias",
        files=[("files", ("gravacao.mp4", b"audiencia ficticia A", "video/mp4"))],
    )
    assert manual.status_code == 200
    manifesto_a = manual.json()["manifesto_caso"]
    assert manifesto_a["midias"][0]["arquivo"] == "gravacao.mp4"
    assert manifesto_a["midias"][0]["id"] == "MIDIA-" + manifesto_a["midias"][0]["sha256"]
    assert chamadas == [(cases / "gravacao.mp4", manifesto_a["id"])]

    sentenca_anterior = {"texto_completo": "Minuta fictícia"}
    bundle_a = session_state.ACTIVE_CASE_STATE["bundle"]
    session_state.ACTIVE_CASE_STATE["last_sentence"] = sentenca_anterior
    ultimo = session_state._caminho_ultimo_caso()
    estado_a = ultimo.read_bytes()

    def falhar_copia(_origem, destino):
        Path(destino).write_bytes(b"somente parte")
        raise OSError("falha fictícia de cópia")

    with monkeypatch.context() as m:
        m.setattr(shutil, "copy2", falhar_copia)
        assert abrir("b").status_code == 503
    assert session_state.ACTIVE_CASE_STATE["bundle"] is bundle_a
    assert session_state.ACTIVE_CASE_STATE["last_sentence"] is sentenca_anterior
    assert pdf_a.read_bytes() == b"autos ficticios A"
    assert not (cases / "autos (2).pdf").exists()
    assert not list((cases / ".upload_tmp").iterdir())
    assert ultimo.read_bytes() == estado_a

    with monkeypatch.context() as m:
        m.setattr(session_state, "salvar_ultimo_caso_real", lambda *a, **kw: False)
        assert abrir("b").status_code == 503
    assert session_state.ACTIVE_CASE_STATE["bundle"] is bundle_a
    assert session_state.ACTIVE_CASE_STATE["last_sentence"] is sentenca_anterior
    assert not (cases / "autos (2).pdf").exists()
    assert ultimo.read_bytes() == estado_a

    session_state.set_active_bundle(None, explicit_demo=False, upload_status={})
    assert session_state.restaurar_ultimo_caso_real()
    assert session_state.ACTIVE_CASE_STATE["bundle"]["manifesto_caso"] == manifesto_a

    resp_b = abrir("b")
    assert resp_b.status_code == 200
    manifesto_b = resp_b.json()["manifesto_caso"]
    assert manifesto_b["id"] != manifesto_a["id"]
    assert manifesto_b["documentos"][0]["arquivo"] == "autos (2).pdf"
    assert manifesto_b["documentos"][0]["sha256"] != manifesto_a["documentos"][0]["sha256"]
    assert manifesto_b["midias"] == []
    assert chamadas == [(cases / "gravacao.mp4", manifesto_a["id"])]
    assert pdf_a.is_file()
    assert pdf_b.read_bytes() == b"autos ficticios B"
    assert session_state.ACTIVE_CASE_STATE["last_sentence"] is None
    monkeypatch.setattr(main_mod, "motor_ocr_disponivel", lambda: "fictício")
    assert client.post("/api/cases/refazer-ocr", json={"filename": pdf_a.name}).status_code == 404
    ocr = client.post("/api/cases/refazer-ocr")
    assert ocr.status_code == 200
    assert ocr.json()["manifesto_caso"] == manifesto_b

    session_state.set_active_bundle(None, explicit_demo=False, upload_status={})
    assert session_state.restaurar_ultimo_caso_real()
    assert session_state.ACTIVE_CASE_STATE["bundle"]["manifesto_caso"] == manifesto_b
    reaberto = abrir("a")
    assert reaberto.status_code == 200
    assert reaberto.json()["manifesto_caso"] == manifesto_a
    assert chamadas == [(cases / "gravacao.mp4", manifesto_a["id"])] * 2
    assert abrir("b").status_code == 200
    (cases / "autos (2).pdf").write_bytes(b"outro PDF no mesmo caminho")
    session_state.set_active_bundle(None, explicit_demo=False, upload_status={})
    assert not session_state.restaurar_ultimo_caso_real()
    assert session_state.ACTIVE_CASE_STATE["bundle"] is None


def test_sem_caso_a_escolha_de_pdf_e_midia_e_explicita(ambiente):
    _, cases = ambiente
    (cases / "autos.pdf").write_bytes(b"autos ficticios A")
    (cases / "autos (2).pdf").write_bytes(b"autos ficticios B")
    client = TestClient(main_mod.app)
    assert client.post("/api/cases/load-from-disk").status_code == 409
    resposta = client.post(
        "/api/cases/upload-medias",
        files=[("files", ("gravacao.mp4", b"audiencia ficticia", "video/mp4"))],
    )
    assert resposta.status_code == 409
    assert not (cases / "gravacao.mp4").exists()
    assert session_state.ACTIVE_CASE_STATE["bundle"] is None


def test_categoria_confirmada_persiste_ao_reabrir_autos_ficticios(ambiente, monkeypatch):
    _, cases = ambiente
    pdf = cases / "autos-ficticios.pdf"
    pdf.write_bytes(b"autos ficticios para classificacao")
    conteudo = "A defesa requer a absolvição. O Promotor de Justiça requer condenação."

    def segmentar(_pdf, **_kwargs):
        return [{"doc_id": "ID-PECA-FICTICIA", "titulo": "Memoriais", "conteudo": conteudo}]

    monkeypatch.setattr(main_mod, "_segmentar_pdf_seguro", segmentar)
    client = TestClient(main_mod.app)
    aberto = client.post("/api/cases/load-from-disk")
    assert aberto.status_code == 200
    caso = aberto.json()
    id_caso = caso["manifesto_caso"]["id"]
    assert caso["pecas"][0]["categoria"] == "NAO_IDENTIFICADA"
    assert any("ID-PECA-FICTICIA" in aviso for aviso in caso["alertas_extracao"])
    dados = {"doc_id": "ID-PECA-FICTICIA", "categoria": "ALEGACOES_FINAIS_DEFESA", "manifesto_id": id_caso}
    assert client.post("/api/cases/document-category", json={**dados, "manifesto_id": "OUTRO-CASO"}).status_code == 409
    r = client.post("/api/cases/document-category", json=dados)
    assert r.status_code == 200
    peca = r.json()["active_case"]["pecas"][0]
    assert peca["categoria_confirmada"] and peca["categoria"] == "ALEGACOES_FINAIS_DEFESA"
    assert peca["conteudo"] == conteudo

    session_state.set_active_bundle(None, explicit_demo=False, upload_status={})
    assert session_state.restaurar_ultimo_caso_real()
    assert client.get("/api/cases/active").json()["pecas"][0]["categoria_confirmada"]
    reprocessado = client.post("/api/cases/load-from-disk")
    assert reprocessado.status_code == 200
    assert reprocessado.json()["pecas"][0]["categoria"] == "ALEGACOES_FINAIS_DEFESA"
    assert reprocessado.json()["pecas"][0]["conteudo"] == conteudo

    automatica = client.post("/api/cases/document-category", json={**dados, "categoria": None})
    assert automatica.status_code == 200
    assert automatica.json()["active_case"]["pecas"][0]["categoria"] == "NAO_IDENTIFICADA"
    assert client.post("/api/cases/load-from-disk").json()["pecas"][0]["categoria"] == "NAO_IDENTIFICADA"
