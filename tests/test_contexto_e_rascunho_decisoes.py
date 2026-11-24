# -*- coding: utf-8 -*-
"""Testes da TASK-068: contexto de geração reaproveitado, recorte por trechos vinculados e rascunho persistido por caso."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.api.security import TOKEN_HEADER, get_session_token
from app.core import case_context
from app.main import ACTIVE_CASE_STATE, app, set_active_bundle


def _bundle_ficticio() -> dict:
    return {
        "is_demo": True,
        "raw_documents": [
            {
                "doc_id": "ID-DEN-01",
                "folha": "fls. 01/03",
                "titulo": "Denúncia",
                "data_peca": "2025-02-10",
                "fase": "JUDICIAL",
                "conteudo": "O Ministério Público oferece denúncia por ameaça (art. 147 do CP) praticada em 01/02/2025.",
            },
            {
                "doc_id": "ID-LAUDO-02",
                "folha": "fls. 04/05",
                "titulo": "Laudo Pericial",
                "data_peca": "2025-02-02",
                "fase": "INQUISITORIAL",
                "conteudo": "Exame de corpo de delito com lesão corporal leve compatível com agressão.",
            },
            {
                "doc_id": "ID-NAO-VINC-99",
                "folha": "fls. 90/95",
                "titulo": "Ofício Administrativo Irrelevante",
                "data_peca": "2025-02-15",
                "fase": "JUDICIAL",
                "conteudo": "Conteúdo administrativo extenso não vinculado a nenhuma decisão " + ("X" * 500),
            },
        ],
        "transcriptions": [],
        "metadados_override": {
            "data_fato": "2025-02-01",
            "data_recebimento_denuncia": "2025-02-20",
            "crimes_imputados": ["art_147_cp"],
        },
    }


def test_contexto_geracao_reaproveitado_e_recorte_por_trechos_vinculados():
    case_context.limpar_cache_contexto()
    bundle = _bundle_ficticio()

    g1 = case_context.obter_contexto_geracao(bundle, ids_vinculados=["ID-LAUDO-02"], max_chars_por_peca=120)
    g2 = case_context.obter_contexto_geracao(bundle, ids_vinculados=["ID-LAUDO-02"], max_chars_por_peca=120)
    assert g1 is g2  # reaproveitado em cache

    ids_recortados = {p["doc_id"] for p in g1["trechos_pecas"]}
    assert "ID-LAUDO-02" in ids_recortados
    assert "ID-NAO-VINC-99" not in ids_recortados
    assert all(len(p["trecho"]) <= 120 for p in g1["trechos_pecas"])

    # Alterar rascunho_decisoes no bundle não invalida o fingerprint do contexto
    bundle_com_rascunho = {**bundle, "rascunho_decisoes": {"case_key": "k1", "crime_configs": []}}
    g3 = case_context.obter_contexto_geracao(bundle_com_rascunho, ids_vinculados=["ID-LAUDO-02"], max_chars_por_peca=120)
    assert g3 is g1


def test_api_rascunho_decisoes_persiste_registro_probatorio_por_caso():
    bundle = _bundle_ficticio()
    set_active_bundle(bundle, explicit_demo=True)
    client = TestClient(app)
    headers = {TOKEN_HEADER: get_session_token()}

    payload = {
        "case_key": "caso-ficticio-068",
        "crime_configs": [
            {
                "codigo": "art_147_cp",
                "veredicto": "condenar",
                "elementos_probatorios": {
                    "materialidade": {"conclusao": "provado", "fonte_id": "ID-LAUDO-02", "trecho": "Exame de corpo de delito"}
                },
            }
        ],
        "extras": {"decisaoCautelar": {"manter_mpu": True}, "valor": "1500"},
    }

    r_bad = client.post("/api/cases/rascunho-decisoes", json={**payload, "campo_desconhecido": 1}, headers=headers)
    assert r_bad.status_code == 422

    r_save = client.post("/api/cases/rascunho-decisoes", json=payload, headers=headers)
    assert r_save.status_code == 200, r_save.text
    assert r_save.json()["rascunho"]["case_key"] == "caso-ficticio-068"

    r_get = client.get("/api/cases/rascunho-decisoes", headers=headers)
    assert r_get.status_code == 200
    rasc = r_get.json()["rascunho"]
    assert rasc["crime_configs"][0]["elementos_probatorios"]["materialidade"]["fonte_id"] == "ID-LAUDO-02"
    assert rasc["extras"]["decisaoCautelar"] == {"manter_mpu": True}

    # /api/cases/active também devolve o rascunho persistido
    r_act = client.get("/api/cases/active", headers=headers)
    assert r_act.status_code == 200
    assert r_act.json()["rascunho_decisoes"]["case_key"] == "caso-ficticio-068"

    # DELETE limpa o rascunho
    r_del = client.delete("/api/cases/rascunho-decisoes", headers=headers)
    assert r_del.status_code == 200
    assert ACTIVE_CASE_STATE["bundle"].get("rascunho_decisoes") is None


def test_modulo_js_registro_rascunho_integrado():
    raiz = Path(__file__).resolve().parents[1]
    reg_js = (raiz / "app" / "static" / "js" / "modulos" / "registro-rascunho.js").read_text(encoding="utf-8")
    dos_js = (raiz / "app" / "static" / "js" / "modulos" / "estacao-dosimetria.js").read_text(encoding="utf-8")
    assert "/api/cases/rascunho-decisoes" in reg_js
    assert "registro-rascunho.js" in dos_js
    assert len(dos_js.splitlines()) <= 900


def test_recorte_localiza_trecho_no_fim_de_peca_longa_e_marca_origem():
    case_context.limpar_cache_contexto()
    trecho_alvo = "CONCLUSAO PERICIAL DECISIVA: equimose periorbital esquerda compatível com instrumento contundente."
    bundle = {
        "is_demo": True,
        "raw_documents": [
            {
                "doc_id": "ID-LAUDO-LONGO",
                "folha": "fls. 10/20",
                "titulo": "Laudo Pericial Completo",
                "data_peca": "2025-02-02",
                "fase": "INQUISITORIAL",
                "conteudo": ("Texto introdutório padronizado do laudo. " * 120) + trecho_alvo,
            },
            {
                "doc_id": "ID-SEM-CITACAO",
                "folha": "fls. 21/22",
                "titulo": "Certidão",
                "data_peca": "2025-02-03",
                "fase": "JUDICIAL",
                "conteudo": "Início da certidão processual sem trecho citado específico. " * 30,
            },
        ],
        "transcriptions": [],
    }
    decisoes = {
        "crimes_config": [
            {
                "crime_code": "art_129_p13_cp",
                "elementos_probatorios": {
                    "materialidade": {"fonte_id": "ID-LAUDO-LONGO", "trecho": trecho_alvo},
                },
            }
        ]
    }
    res = case_context.obter_contexto_geracao(
        bundle, decisoes=decisoes, ids_vinculados=["ID-SEM-CITACAO"], max_chars_por_peca=300
    )
    por_id = {p["doc_id"]: p for p in res["trechos_pecas"]}
    assert por_id["ID-LAUDO-LONGO"]["recorte"] == "trecho_vinculado"
    assert trecho_alvo in por_id["ID-LAUDO-LONGO"]["trecho"]
    assert por_id["ID-SEM-CITACAO"]["recorte"] == "inicio"


def test_geracao_reaproveita_contexto_e_invalida_quando_decisao_muda(monkeypatch):
    from app.generation import sentence_generator as sg

    case_context.limpar_cache_contexto()
    bundle = _bundle_ficticio()
    chamadas = {"n": 0}
    orig = case_context.recorte_trechos_vinculados

    def _contar(*args, **kwargs):
        chamadas["n"] += 1
        return orig(*args, **kwargs)

    monkeypatch.setattr(case_context, "recorte_trechos_vinculados", _contar)

    decisao_a = {"crimes_config": [{"crime_code": "art_147_cp", "veredicto": "condenar"}]}
    decisao_b = {"crimes_config": [{"crime_code": "art_147_cp", "veredicto": "absolver", "art_386_inciso": "VII"}]}

    sg.planejar_sentenca(bundle, decisao_a)
    sg.planejar_sentenca(bundle, decisao_a)
    assert chamadas["n"] == 1  # segunda geração reaproveitou o contexto em cache

    sg.planejar_sentenca(bundle, decisao_b)
    assert chamadas["n"] == 2  # mudança de decisão invalidou e recalculou
