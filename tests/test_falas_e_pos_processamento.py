# -*- coding: utf-8 -*-
"""Ajuste de nomes/número do processo na transcrição e análise de falas/alegações (com IA simulada)."""

from __future__ import annotations

import json

from app.transcription import audio_transcriber as at
from app.transcription.falas import analisar_midia, extrair_alegacoes, marcar_falantes
from app.transcription.pos_processamento import aprimorar_segmentos

NOMES = ["CARLOS EDUARDO FERREIRA", "MARINA LOPES FERREIRA"]


def _seg(t: str) -> dict:
    return {"timestamp": "00:01 - 00:05", "texto_literal": t}


def test_ajusta_nome_parecido_e_guarda_original():
    r = aprimorar_segmentos([_seg("senhor Carlos Eduarto Ferreira, presente")], nomes=NOMES)[0]
    assert "Carlos Eduardo Ferreira" in r["texto_literal"]
    assert r["texto_whisper"] == "senhor Carlos Eduarto Ferreira, presente"
    assert r["ajustes"]


def test_nao_altera_texto_comum_nem_nomes_desconhecidos():
    textos = ["ela disse que a carta chegou de manhã", "Marfilha Jacibor Carlos"]
    saida = aprimorar_segmentos([_seg(t) for t in textos], nomes=NOMES)
    assert [s["texto_literal"] for s in saida] == textos
    assert all("texto_whisper" not in s for s in saida)


def test_numero_do_processo_ditado_vira_numero_cnj():
    t = "Essa é a audiência do processo 0804, 590, 12, 2025, 8, 25, 0, 0, 1, é uma ação"
    r = aprimorar_segmentos([_seg(t)], numero_processo="0804590-12.2025.8.00.0001")[0]
    assert "0804590-12.2025.8.00.0001" in r["texto_literal"]


def test_idempotente():
    um = aprimorar_segmentos([_seg("Marina Lopis Ferreira")], nomes=NOMES)
    dois = aprimorar_segmentos(um, nomes=NOMES)
    assert um == dois


def test_numero_cnj_pelo_nome_do_pdf(tmp_path):
    (tmp_path / "0804590-12.2025.8.00.0001.pdf").write_bytes(b"%PDF")
    assert at._numero_cnj_vizinho(tmp_path / "x.mp4") == "0804590-12.2025.8.00.0001"
    assert at._numero_cnj_vizinho(tmp_path / "sem" / "x.mp4") == ""


def test_modelo_mudou():
    class S:
        whisper_model = "large-v3"

    assert at._modelo_mudou({"engine": "faster-whisper small (cpu/int8)"}, S())
    assert not at._modelo_mudou({"engine": "faster-whisper large-v3 (cuda/int8_float16)"}, S())
    assert not at._modelo_mudou({"engine": "gemini"}, S())


SEGS = [
    _seg("A senhora jura dizer a verdade?"),
    _seg("Ele me empurrou contra a parede e me ameaçou de morte"),
    _seg("Isso foi na frente dos filhos?"),
    _seg("Foi sim, na cozinha de casa, no sábado à noite"),
]


def _gerar_falso(prompt: str, **_kw) -> str:
    if "ALEGAÇÕES" in prompt:
        return json.dumps(
            [
                {"i": 2, "alegacao": "Afirma ter sido empurrada e ameaçada de morte.", "trecho": "me empurrou contra a parede e me ameaçou de morte"},
                {"i": 4, "alegacao": "Afirma que o fato ocorreu no hospital.", "trecho": "aconteceu no hospital municipal de manhã"},
            ]
        )
    return json.dumps([{"i": 1, "f": "JUIZ"}, {"i": 2, "f": "DEPOENTE"}, {"i": 3, "f": "MP"}, {"i": 4, "f": "DEPOENTE"}])


def test_marca_falantes():
    assert marcar_falantes(SEGS, "OFENDIDA", "Karla", _gerar_falso) == ["JUIZ", "DEPOENTE", "MP", "DEPOENTE"]


def test_falha_do_modelo_vira_incerto():
    assert marcar_falantes(SEGS, "OFENDIDA", "Karla", lambda *a, **k: None) == ["INCERTO"] * 4
    assert marcar_falantes(SEGS, "OFENDIDA", "Karla", lambda *a, **k: "lixo") == ["INCERTO"] * 4


def test_alegacao_sem_trecho_literal_e_descartada():
    r = extrair_alegacoes(SEGS, ["JUIZ", "DEPOENTE", "MP", "DEPOENTE"], "OFENDIDA", "Karla", _gerar_falso)
    assert len(r["alegacoes"]) == 1
    assert r["descartadas_sem_lastro"] == 1
    assert r["alegacoes"][0]["segmento"] == 1
    assert r["alegacoes"][0]["timestamp"] == "00:01 - 00:05"


def test_analise_em_cache(tmp_path):
    media = tmp_path / "m.mp4"
    media.write_bytes(b"x")
    tr = {"segmentos": SEGS, "papel": "OFENDIDA", "depoente": "Karla", "media_id": "ID-1"}
    a = analisar_midia(media, tr, "modelo-x", gerar=_gerar_falso)
    chamadas = []
    b = analisar_midia(media, tr, "modelo-x", gerar=lambda *a, **k: chamadas.append(1))
    assert a == b and not chamadas
    assert a["confiavel"] is True
