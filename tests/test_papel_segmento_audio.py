# -*- coding: utf-8 -*-
"""Testes da TASK-065: papel por segmento de áudio, estado INDETERMINADO e confirmação humana."""

from __future__ import annotations

import pytest

from app.transcription import audio_transcriber as at
from app.transcription import oral_proof_analyzer as opa


def test_inferir_e_normalizar_papel_ambiguo_retorna_indeterminado():
    assert at._inferir_papel("gravacao_audiencia_01", "", "") == "INDETERMINADO"
    assert at._inferir_papel("parte_02", "Pessoa Ficticia", "") == "INDETERMINADO"
    assert opa._normalizar_papel("") == "INDETERMINADO"
    assert opa._normalizar_papel(None) == "INDETERMINADO"
    assert opa._normalizar_papel("INDETERMINADO") == "INDETERMINADO"
    assert opa._normalizar_papel("PAPEL_INEXISTENTE") == "INDETERMINADO"


def test_papeis_explicitos_continuam_resolvidos():
    assert at._inferir_papel("Depoimento da Testemunha Kempman", "", "") == "TESTEMUNHA_ACUSACAO"
    assert at._inferir_papel("Oitiva da Vitima Ficticia", "", "") == "OFENDIDA"
    assert at._inferir_papel("Interrogatorio do Reu", "", "") == "INTERROGATORIO_REU"
    assert opa._normalizar_papel("VITIMA") == "OFENDIDA"


def test_matriz_oral_nao_atribui_papel_indeterminado_nem_conta_como_confirmado():
    trs = [
        {
            "media_id": "ID-MIDIA-901",
            "arquivo": "gravacao_01.mp4",
            "depoente": "Participante 1",
            "papel": "INDETERMINADO",
            "duracao": "01:10",
            "segmentos": [
                {"timestamp": "00:00 - 00:25", "texto_literal": "Houve agressão e ameaça dentro da residência."},
                {"timestamp": "00:25 - 00:50", "texto_literal": "A medida protetiva já havia sido comunicada."},
            ],
        }
    ]
    matriz = opa.gerar_matriz_corroboracao_oral([], trs, is_demo=False)
    assert len(matriz.depoentes) == 1
    dep = matriz.depoentes[0]
    assert dep.papel == "INDETERMINADO"
    assert dep.papel_confirmado is False
    assert dep.coerencia_delegacia_juizo == "PENDENTE_AVALIACAO_JUIZO"
    assert dep.segmentos_pendentes_confirmacao == 2
    assert matriz.segmentos_pendentes_confirmacao == 2
    assert "indeterminado" in dep.discurso_indireto_sentenca.lower()
    assert all(s.get("papel_estado") == "INDETERMINADO" for s in dep.segmentos_literais)


def test_confirmar_papel_segmento_exige_papel_concreto_e_atualiza_pendencias():
    tr = {
        "media_id": "ID-MIDIA-902",
        "arquivo": "gravacao_02.mp4",
        "depoente": "Participante 2",
        "papel": "INDETERMINADO",
        "duracao": "00:50",
        "segmentos": [
            {"timestamp": "00:00 - 00:20", "texto_literal": "Ele me empurrou e ameaçou de morte."},
            {"timestamp": "00:20 - 00:40", "texto_literal": "Chamei a polícia militar logo depois."},
        ],
    }

    with pytest.raises(ValueError):
        opa.confirmar_papel_segmento(tr, 0, "INDETERMINADO", confirmar=True)

    with pytest.raises(ValueError):
        opa.confirmar_papel_segmento(tr, 0, "PAPEL_INVALIDO", confirmar=True)

    opa.confirmar_papel_segmento(tr, 0, "OFENDIDA", confirmar=True)
    assert tr["segmentos"][0]["papel_segmento"] == "OFENDIDA"
    assert tr["segmentos"][0]["papel_estado"] == "CONFIRMADO"
    assert tr["segmentos"][0]["papel_confirmado"] is True

    # Segmento 1 ainda está pendente de confirmação
    matriz = opa.gerar_matriz_corroboracao_oral([], [tr], is_demo=False)
    assert matriz.depoentes[0].segmentos_pendentes_confirmacao == 1
    assert matriz.segmentos_pendentes_confirmacao == 1

    # Confirmando o segundo segmento, zera pendências
    opa.confirmar_papel_segmento(tr, 1, "OFENDIDA", confirmar=True)
    matriz2 = opa.gerar_matriz_corroboracao_oral([], [tr], is_demo=False)
    assert matriz2.depoentes[0].segmentos_pendentes_confirmacao == 0
    assert matriz2.segmentos_pendentes_confirmacao == 0


def test_midia_100_por_cento_indeterminada_nao_alimenta_sintese_de_merito():
    """Mesmo se o papel da mídia for preenchido, segmentos 100% INDETERMINADO não alimentam a síntese."""
    trs = [
        {
            "media_id": "ID-MIDIA-903",
            "arquivo": "gravacao_03.mp4",
            "depoente": "Participante 3",
            "papel": "OFENDIDA",
            "duracao": "00:40",
            "segmentos": [
                {
                    "timestamp": "00:00 - 00:20",
                    "texto_literal": "Houve agressão física com soco e ameaça de morte.",
                    "papel_segmento": "INDETERMINADO",
                }
            ],
        }
    ]
    matriz = opa.gerar_matriz_corroboracao_oral([], trs, is_demo=False)
    dep = matriz.depoentes[0]
    assert dep.segmentos_pendentes_confirmacao == 1
    assert "aguardando confirmação humana" in dep.discurso_indireto_sentenca.lower()
    assert "agress" not in dep.discurso_indireto_sentenca.lower()
    assert "ameaç" not in dep.discurso_indireto_sentenca.lower()


def test_api_confirmar_papel_segmento_persiste_no_caso_ativo():
    from fastapi.testclient import TestClient

    from app.api.security import TOKEN_HEADER, get_session_token
    from app.main import ACTIVE_CASE_STATE, app, set_active_bundle

    bundle = {
        "is_demo": True,
        "pecas": [],
        "transcriptions": [
            {
                "media_id": "ID-MIDIA-API-1",
                "arquivo": "aud_ficticia.mp4",
                "depoente": "Participante API",
                "papel": "INDETERMINADO",
                "duracao": "00:30",
                "segmentos": [
                    {"timestamp": "00:00 - 00:15", "texto_literal": "Ele me ameaçou na residência."}
                ],
            }
        ],
        "matriz_oral": opa.gerar_matriz_corroboracao_oral(
            [],
            [
                {
                    "media_id": "ID-MIDIA-API-1",
                    "arquivo": "aud_ficticia.mp4",
                    "depoente": "Participante API",
                    "papel": "INDETERMINADO",
                    "duracao": "00:30",
                    "segmentos": [
                        {"timestamp": "00:00 - 00:15", "texto_literal": "Ele me ameaçou na residência."}
                    ],
                }
            ],
            is_demo=False,
        ).model_dump(),
    }
    set_active_bundle(bundle, explicit_demo=True)
    client = TestClient(app)
    headers = {TOKEN_HEADER: get_session_token()}

    r_bad = client.post(
        "/api/cases/transcricao/papel-segmento",
        json={"media_id": "ID-MIDIA-API-1", "indice": 0, "papel": "INDETERMINADO", "confirmar": True},
        headers=headers,
    )
    assert r_bad.status_code == 422

    r_extra = client.post(
        "/api/cases/transcricao/papel-segmento",
        json={"media_id": "ID-MIDIA-API-1", "indice": 0, "papel": "OFENDIDA", "extra_invalido": 1},
        headers=headers,
    )
    assert r_extra.status_code == 422

    r_ok = client.post(
        "/api/cases/transcricao/papel-segmento",
        json={"media_id": "ID-MIDIA-API-1", "indice": 0, "papel": "OFENDIDA", "confirmar": True},
        headers=headers,
    )
    assert r_ok.status_code == 200
    body = r_ok.json()
    assert body["segmento"]["papel_estado"] == "CONFIRMADO"
    assert body["matriz_oral"]["segmentos_pendentes_confirmacao"] == 0

    ativo = ACTIVE_CASE_STATE["bundle"]
    assert ativo["transcriptions"][0]["segmentos"][0]["papel_segmento"] == "OFENDIDA"
    assert ativo["transcriptions"][0]["segmentos"][0]["papel_confirmado"] is True
    assert ativo["matriz_oral"]["depoentes"][0]["papel"] == "OFENDIDA"
