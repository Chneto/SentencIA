"""Testes de regressão da TASK-095: INGESTAO_LOCK, manifesto_id e promoção do papel do depoente."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import security as sec
from app.api.jobs import INGESTAO_LOCK
from app.core.session_state import ACTIVE_CASE_STATE, set_active_bundle
from app.main import app


def _bundle_ficticio_com_dois_segmentos(manifesto_id: str = "manif-ficticio-095") -> dict:
    return {
        "manifesto_caso": {"id": manifesto_id},
        "metadados": {"numero_processo": "0809999-11.2025.8.20.5001"},
        "transcriptions": [
            {
                "media_id": "midia-aud-01",
                "arquivo": "audiencia_ficticia.mp4",
                "depoente": "Participante Ficticio",
                "papel": "INDETERMINADO",
                "duracao": "00:30",
                "segmentos": [
                    {
                        "timestamp": "00:00 - 00:12",
                        "texto_literal": "Primeiro trecho ficticio da audiencia para validacao.",
                    },
                    {
                        "timestamp": "00:12 - 00:25",
                        "texto_literal": "Segundo trecho ficticio da audiencia para validacao.",
                    },
                ],
            }
        ],
        "matriz_oral": {
            "depoentes": [
                {
                    "media_id": "midia-aud-01",
                    "nome": "Depoente Ficticio",
                    "papel": "INDETERMINADO",
                    "papel_confirmado": False,
                    "segmentos_pendentes_confirmacao": 2,
                    "segmentos_literais": [],
                    "discurso_indireto_sentenca": "",
                }
            ],
            "segmentos_pendentes_confirmacao": 2,
        },
    }


@pytest.fixture()
def client_com_bundle():
    headers = {sec.TOKEN_HEADER: sec.get_session_token()}
    with TestClient(app, base_url="http://127.0.0.1:8000", headers=headers) as client:
        anterior = ACTIVE_CASE_STATE.get("bundle")
        demo_ant = bool(ACTIVE_CASE_STATE.get("explicit_demo"))
        set_active_bundle(_bundle_ficticio_com_dois_segmentos(), explicit_demo=True, limpar_producao=False)
        try:
            yield client
        finally:
            if anterior is not None:
                set_active_bundle(anterior, explicit_demo=demo_ant, limpar_producao=False)


class TestIngestaoLockEManifesto:
    def test_retorna_409_durante_troca_de_autos_em_rascunho_e_papel(
        self, client_com_bundle: TestClient
    ) -> None:
        assert INGESTAO_LOCK.acquire(blocking=False)
        try:
            r_post = client_com_bundle.post(
                "/api/cases/rascunho-decisoes",
                json={
                    "case_key": "caso-095",
                    "crime_configs": [{"veredicto": "condenar"}],
                    "manifesto_id": "manif-ficticio-095",
                },
            )
            assert r_post.status_code == 409

            r_del = client_com_bundle.delete(
                "/api/cases/rascunho-decisoes",
                params={"manifesto_id": "manif-ficticio-095"},
            )
            assert r_del.status_code == 409

            r_papel = client_com_bundle.post(
                "/api/cases/transcricao/papel-segmento",
                json={
                    "media_id": "midia-aud-01",
                    "indice": 0,
                    "papel": "VITIMA",
                    "confirmar": True,
                    "manifesto_id": "manif-ficticio-095",
                },
            )
            assert r_papel.status_code == 409
        finally:
            INGESTAO_LOCK.release()

    def test_recusa_manifesto_divergente_com_409_e_aceita_correspondente(
        self, client_com_bundle: TestClient
    ) -> None:
        r_post_err = client_com_bundle.post(
            "/api/cases/rascunho-decisoes",
            json={
                "case_key": "caso-095",
                "crime_configs": [{"veredicto": "condenar"}],
                "manifesto_id": "manif-outro-caso",
            },
        )
        assert r_post_err.status_code == 409

        r_papel_err = client_com_bundle.post(
            "/api/cases/transcricao/papel-segmento",
            json={
                "media_id": "midia-aud-01",
                "indice": 0,
                "papel": "VITIMA",
                "confirmar": True,
                "manifesto_id": "manif-outro-caso",
            },
        )
        assert r_papel_err.status_code == 409

        # Manifesto correspondente é aceito
        r_post_ok = client_com_bundle.post(
            "/api/cases/rascunho-decisoes",
            json={
                "case_key": "caso-095",
                "crime_configs": [{"veredicto": "condenar"}],
                "manifesto_id": "manif-ficticio-095",
            },
        )
        assert r_post_ok.status_code == 200
        assert r_post_ok.json()["rascunho"]["manifesto_id"] == "manif-ficticio-095"

        r_del_err = client_com_bundle.delete(
            "/api/cases/rascunho-decisoes",
            params={"manifesto_id": "manif-outro-caso"},
        )
        assert r_del_err.status_code == 409

        r_del_ok = client_com_bundle.delete(
            "/api/cases/rascunho-decisoes",
            params={"manifesto_id": "manif-ficticio-095"},
        )
        assert r_del_ok.status_code == 200


class TestPromocaoPapelDepoente:
    def test_confirmar_um_trecho_nao_promove_depoente_indeterminado(
        self, client_com_bundle: TestClient
    ) -> None:
        r1 = client_com_bundle.post(
            "/api/cases/transcricao/papel-segmento",
            json={
                "media_id": "midia-aud-01",
                "indice": 0,
                "papel": "OFENDIDA",
                "confirmar": True,
                "manifesto_id": "manif-ficticio-095",
            },
        )
        assert r1.status_code == 200
        dep1 = r1.json()["matriz_oral"]["depoentes"][0]
        assert dep1["papel"] == "INDETERMINADO"
        assert dep1["segmentos_pendentes_confirmacao"] == 1

        # Segundo trecho com papel diferente: ainda NÃO promove o depoente inteiro
        r2_diff = client_com_bundle.post(
            "/api/cases/transcricao/papel-segmento",
            json={
                "media_id": "midia-aud-01",
                "indice": 1,
                "papel": "TESTEMUNHA_ACUSACAO",
                "confirmar": True,
                "manifesto_id": "manif-ficticio-095",
            },
        )
        assert r2_diff.status_code == 200
        dep2_diff = r2_diff.json()["matriz_oral"]["depoentes"][0]
        assert dep2_diff["papel"] == "INDETERMINADO"
        assert dep2_diff["segmentos_pendentes_confirmacao"] == 0

        # Ajustando o segundo trecho para o mesmo papel (OFENDIDA): agora promove para OFENDIDA
        r2_igual = client_com_bundle.post(
            "/api/cases/transcricao/papel-segmento",
            json={
                "media_id": "midia-aud-01",
                "indice": 1,
                "papel": "OFENDIDA",
                "confirmar": True,
                "manifesto_id": "manif-ficticio-095",
            },
        )
        assert r2_igual.status_code == 200
        dep2_igual = r2_igual.json()["matriz_oral"]["depoentes"][0]
        assert dep2_igual["papel"] == "OFENDIDA"
        assert dep2_igual["segmentos_pendentes_confirmacao"] == 0
