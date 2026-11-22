"""Testes da gestão e validação da jurisprudência do gabinete (TASK-017)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.knowledge.knowledge_base as kb
from app.api.security import TOKEN_HEADER, get_session_token
from app.core.session_state import ACTIVE_CASE_STATE, STATE_LOCK
from app.generation.hallucination_auditor import _construir_indice_base_fechada
from app.main import app


@pytest.fixture
def ambiente_gabinete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isola ``CUSTOM_KB_FILE`` em ``tmp_path`` e devolve cliente HTTP autenticado."""
    arquivo_custom = tmp_path / "custom_precedentes.json"
    monkeypatch.setattr(kb, "CUSTOM_KB_FILE", arquivo_custom)
    headers = {TOKEN_HEADER: get_session_token()}
    with TestClient(app) as client:
        yield client, headers, arquivo_custom


def test_crud_completo_gabinete_e_backup(ambiente_gabinete):
    client, headers, arquivo_custom = ambiente_gabinete
    arquivo_bak = arquivo_custom.with_name("custom_precedentes.bak.json")

    # 1. Adicionar com normalização de referência e preservação integral do texto literal
    texto_usuario = "  Texto  LITERAL com   pontuação... e grafia propria do gabinete!  "
    r_add = client.post(
        "/api/knowledge/precedente",
        headers=headers,
        json={
            "referencia": "súmula n. 999 do stj",
            "titulo": "Súmula Hipotética de Teste",
            "texto_literal": texto_usuario,
            "tipo": "SUMULA_STJ",
            "tribunal": "stj",
            "materia": "Teste Gabinete",
        },
    )
    assert r_add.status_code == 200, r_add.text
    item = r_add.json()["item"]
    assert item["id"] == "GAB-001"
    assert item["referencia"] == "Súmula 999/STJ"
    assert item["tribunal"] == "STJ"
    # Nunca corrigir silenciosamente o texto literal do usuário
    assert item["texto_literal"] == texto_usuario.strip()
    assert arquivo_custom.exists()

    # 2. Editar (mantendo a própria referência não dispara 409) -> gera backup
    r_upd = client.put(
        f"/api/knowledge/precedente/{item['id']}",
        headers=headers,
        json={
            "referencia": "Súmula 999/STJ",
            "titulo": "Súmula Hipotética Atualizada",
            "texto_literal": "Novo texto literal do gabinete suficientemente longo para validação.",
            "tribunal": "STJ",
        },
    )
    assert r_upd.status_code == 200, r_upd.text
    atualizado = r_upd.json()["item"]
    assert atualizado["id"] == "GAB-001"
    assert atualizado["titulo"] == "Súmula Hipotética Atualizada"
    assert arquivo_bak.exists()
    bak_itens = json.loads(arquivo_bak.read_text(encoding="utf-8"))
    assert len(bak_itens) == 1
    assert bak_itens[0]["titulo"] == "Súmula Hipotética de Teste"

    # 3. Excluir -> atualiza backup e remove item
    r_del = client.delete(f"/api/knowledge/precedente/{item['id']}", headers=headers)
    assert r_del.status_code == 200, r_del.text
    body_del = r_del.json()
    assert body_del["status"] == "deleted"
    assert body_del["item"]["id"] == "GAB-001"
    assert body_del["aviso"] is None
    assert json.loads(arquivo_custom.read_text(encoding="utf-8")) == []
    bak_apos_del = json.loads(arquivo_bak.read_text(encoding="utf-8"))
    assert bak_apos_del[0]["titulo"] == "Súmula Hipotética Atualizada"


def test_item_oficial_somente_leitura_retorna_403(ambiente_gabinete):
    client, headers, _ = ambiente_gabinete

    r_put = client.put(
        "/api/knowledge/precedente/SUMULA-231-STJ",
        headers=headers,
        json={"titulo": "Tentativa de edição"},
    )
    assert r_put.status_code == 403
    assert "oficial" in r_put.json()["detail"].lower()

    r_del = client.delete("/api/knowledge/precedente/SUMULA-231-STJ", headers=headers)
    assert r_del.status_code == 403
    assert "oficial" in r_del.json()["detail"].lower()


def test_id_inexistente_retorna_404(ambiente_gabinete):
    client, headers, _ = ambiente_gabinete

    r_put = client.put(
        "/api/knowledge/precedente/GAB-999",
        headers=headers,
        json={"titulo": "Não existe"},
    )
    assert r_put.status_code == 404

    r_del = client.delete("/api/knowledge/precedente/GAB-999", headers=headers)
    assert r_del.status_code == 404


def test_duplicidade_contra_oficial_e_gabinete_retorna_409(ambiente_gabinete):
    client, headers, _ = ambiente_gabinete

    # Duplicidade contra o acervo oficial (variações de escrita)
    r_dup_sum = client.post(
        "/api/knowledge/precedente",
        headers=headers,
        json={
            "referencia": "sumula n. 231 do stj",
            "titulo": "Duplicada",
            "texto_literal": "Texto suficientemente longo para passar na validação de tamanho.",
            "tribunal": "STJ",
        },
    )
    assert r_dup_sum.status_code == 409
    body_sum = r_dup_sum.json()
    assert body_sum["origem"] == "oficial"
    assert body_sum["item_existente"]["id"] == "SUMULA-231-STJ"

    r_dup_tema = client.post(
        "/api/knowledge/precedente",
        headers=headers,
        json={
            "referencia": "Tema 1197/STJ",
            "titulo": "Duplicado de Tema Repetitivo",
            "texto_literal": "Texto suficientemente longo para passar na validação de tamanho.",
            "tribunal": "STJ",
        },
    )
    assert r_dup_tema.status_code == 409
    assert r_dup_tema.json()["item_existente"]["id"] == "TEMA-1197-STJ"

    # Cadastra dois itens do gabinete e testa colisão entre eles (POST e PUT)
    r1 = client.post(
        "/api/knowledge/precedente",
        headers=headers,
        json={
            "referencia": "Tema 9100/STJ",
            "titulo": "Primeiro do Gabinete",
            "texto_literal": "Enunciado exclusivo do gabinete com mais de vinte caracteres.",
            "tribunal": "STJ",
        },
    )
    assert r1.status_code == 200
    id1 = r1.json()["item"]["id"]

    r_dup_gab = client.post(
        "/api/knowledge/precedente",
        headers=headers,
        json={
            "referencia": "tema repetitivo n. 9100 do stj",
            "titulo": "Tentativa Duplicada",
            "texto_literal": "Outro texto suficientemente longo para validar duplicidade.",
            "tribunal": "STJ",
        },
    )
    assert r_dup_gab.status_code == 409
    assert r_dup_gab.json()["origem"] == "gabinete"
    assert r_dup_gab.json()["item_existente"]["id"] == id1

    r2 = client.post(
        "/api/knowledge/precedente",
        headers=headers,
        json={
            "referencia": "Tema 9101/STJ",
            "titulo": "Segundo do Gabinete",
            "texto_literal": "Segundo enunciado exclusivo do gabinete com texto longo.",
            "tribunal": "STJ",
        },
    )
    assert r2.status_code == 200
    id2 = r2.json()["item"]["id"]

    r_put_dup = client.put(
        f"/api/knowledge/precedente/{id2}",
        headers=headers,
        json={"referencia": "Tema 9100/STJ"},
    )
    assert r_put_dup.status_code == 409
    assert r_put_dup.json()["item_existente"]["id"] == id1


def test_validacao_campos_obrigatorios_tamanho_e_tribunal(ambiente_gabinete):
    client, headers, _ = ambiente_gabinete

    # Texto literal abaixo do mínimo
    r_curto = client.post(
        "/api/knowledge/precedente",
        headers=headers,
        json={
            "referencia": "Tema 9200/STJ",
            "titulo": "Título válido",
            "texto_literal": "Curto demais",
            "tribunal": "STJ",
        },
    )
    assert r_curto.status_code == 422
    assert "curto" in r_curto.json()["detail"].lower()

    # Título vazio
    r_sem_titulo = client.post(
        "/api/knowledge/precedente",
        headers=headers,
        json={
            "referencia": "Tema 9201/STJ",
            "titulo": "   ",
            "texto_literal": "Texto suficientemente longo para o teste de validação.",
            "tribunal": "STJ",
        },
    )
    assert r_sem_titulo.status_code == 422

    # Tribunal não reconhecido
    r_trib = client.post(
        "/api/knowledge/precedente",
        headers=headers,
        json={
            "referencia": "Precedente 1/XYZ",
            "titulo": "Título válido",
            "texto_literal": "Texto suficientemente longo para o teste de tribunal.",
            "tribunal": "TRIBUNAL_FICTICIO",
        },
    )
    assert r_trib.status_code == 422
    assert "tribunal" in r_trib.json()["detail"].lower()


def test_exportar_e_importar_com_previa_e_duplicados_ignorados(ambiente_gabinete, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client, headers, arquivo_custom = ambiente_gabinete

    # Cadastra um item inicial e exporta
    r_add = client.post(
        "/api/knowledge/precedente",
        headers=headers,
        json={
            "referencia": "Enunciado 901/FONAVID",
            "titulo": "Enunciado Gabinete 901",
            "texto_literal": "Texto literal exportável do enunciado 901 do gabinete.",
            "tribunal": "FONAVID",
        },
    )
    assert r_add.status_code == 200

    r_exp = client.get("/api/knowledge/gabinete/exportar", headers=headers)
    assert r_exp.status_code == 200
    exportado = r_exp.json()
    assert exportado["formato_versao"] == 1
    assert exportado["total"] == 1
    assert len(exportado["itens"]) == 1

    # Monta pacote de importação contendo:
    # - 1 duplicado do gabinete (o próprio exportado)
    # - 1 duplicado oficial (Súmula 231/STJ)
    # - 1 item novo válido
    # - 1 duplicado interno do próprio pacote (mesma ref do novo)
    # - 1 item inválido (texto curto)
    pacote = {
        "formato_versao": 1,
        "itens": [
            exportado["itens"][0],
            {
                "referencia": "Súmula 231/STJ",
                "titulo": "Duplicado oficial",
                "texto_literal": "Texto suficientemente longo que colide com o acervo oficial.",
                "tribunal": "STJ",
            },
            {
                "referencia": "Apelação Criminal 0900123-45.2025.8.20.0001",
                "titulo": "Precedente Importado TJRN",
                "texto_literal": "Ementa sintética do precedente importado da Câmara Criminal do TJRN.",
                "tribunal": "TJRN",
            },
            {
                "referencia": "Apelação Criminal 0900123-45.2025.8.20.0001",
                "titulo": "Duplicado no mesmo lote",
                "texto_literal": "Texto duplicado dentro do mesmo arquivo de importação.",
                "tribunal": "TJRN",
            },
            {
                "referencia": "Item Inválido",
                "titulo": "Curto",
                "texto_literal": "Muito curto",
                "tribunal": "TJRN",
            },
        ],
    }

    # 1. Prévia (confirmar=False) não altera o arquivo em disco
    conteudo_antes = arquivo_custom.read_text(encoding="utf-8")
    r_prev = client.post(
        "/api/knowledge/gabinete/importar",
        headers=headers,
        json={**pacote, "confirmar": False},
    )
    assert r_prev.status_code == 200, r_prev.text
    prev = r_prev.json()
    assert prev["modo"] == "previa"
    assert prev["gravado"] is False
    assert len(prev["novos"]) == 1
    assert len(prev["duplicados_ignorados"]) == 3
    assert len(prev["invalidos"]) == 1
    assert arquivo_custom.read_text(encoding="utf-8") == conteudo_antes

    # 2. Confirmação (confirmar=True) grava só o novo e cria backup
    r_conf = client.post(
        "/api/knowledge/gabinete/importar",
        headers=headers,
        json={**pacote, "confirmar": True},
    )
    assert r_conf.status_code == 200, r_conf.text
    conf = r_conf.json()
    assert conf["modo"] == "confirmado"
    assert conf["gravado"] is True
    assert len(conf["adicionados"]) == 1
    assert len(conf["duplicados_ignorados"]) == 3
    assert len(conf["invalidos"]) == 1
    assert conf["total_gabinete"] == 2
    assert arquivo_custom.with_name("custom_precedentes.bak.json").exists()


def test_exclusao_citada_em_versao_salva_gera_aviso_sem_bloquear(ambiente_gabinete):
    client, headers, _ = ambiente_gabinete

    r_add = client.post(
        "/api/knowledge/precedente",
        headers=headers,
        json={
            "referencia": "Enunciado 902/FONAVID",
            "titulo": "Enunciado Citado",
            "texto_literal": "Enunciado utilizado em versão salva de minuta para teste de aviso.",
            "tribunal": "FONAVID",
        },
    )
    assert r_add.status_code == 200
    item_id = r_add.json()["item"]["id"]

    with STATE_LOCK:
        historico_anterior = list(ACTIVE_CASE_STATE.get("historico_versoes") or [])
        ACTIVE_CASE_STATE["historico_versoes"] = [
            {
                "id": "v1",
                "numero": 1,
                "origem": "seccional",
                "rotulo": "Gerada seção a seção",
                "criado_em": "2026-01-01T10:00:00Z",
                "texto_completo": "Conforme Enunciado 902/FONAVID, aplica-se a tutela protetiva.",
            }
        ]
    try:
        r_del = client.delete(f"/api/knowledge/precedente/{item_id}", headers=headers)
        assert r_del.status_code == 200, r_del.text
        body = r_del.json()
        assert body["status"] == "deleted"
        assert body["aviso"] is not None
        assert "v1" in body["aviso"]
        assert len(body["citado_em_versoes"]) == 1
        assert body["citado_em_versoes"][0]["id"] == "v1"
    finally:
        with STATE_LOCK:
            ACTIVE_CASE_STATE["historico_versoes"] = historico_anterior


def test_auditor_camada2_enxerga_adicao_edicao_e_exclusao_na_hora(ambiente_gabinete):
    client, headers, _ = ambiente_gabinete

    # Antes de adicionar, Súmula 987/STJ não está no índice da camada 2
    v0 = kb.versao_acervo()
    indice_0 = _construir_indice_base_fechada()
    assert 987 not in indice_0.sumulas_stj

    # Adiciona Súmula 987/STJ -> camada 2 enxerga imediatamente
    r_add = client.post(
        "/api/knowledge/precedente",
        headers=headers,
        json={
            "referencia": "Súmula 987/STJ",
            "titulo": "Súmula Teste 987",
            "texto_literal": "Texto literal da súmula 987 cadastrada pelo gabinete para auditoria.",
            "tipo": "SUMULA_STJ",
            "tribunal": "STJ",
        },
    )
    assert r_add.status_code == 200
    item_id = r_add.json()["item"]["id"]
    v1 = kb.versao_acervo()
    assert v1 != v0
    indice_1 = _construir_indice_base_fechada()
    assert 987 in indice_1.sumulas_stj

    # Edita para Súmula 988/STJ -> camada 2 passa a enxergar 988 e deixa de enxergar 987
    r_upd = client.put(
        f"/api/knowledge/precedente/{item_id}",
        headers=headers,
        json={"referencia": "Súmula 988/STJ"},
    )
    assert r_upd.status_code == 200
    v2 = kb.versao_acervo()
    assert v2 != v1
    indice_2 = _construir_indice_base_fechada()
    assert 988 in indice_2.sumulas_stj
    assert 987 not in indice_2.sumulas_stj

    # Exclui -> camada 2 deixa de enxergar 988
    r_del = client.delete(f"/api/knowledge/precedente/{item_id}", headers=headers)
    assert r_del.status_code == 200
    v3 = kb.versao_acervo()
    assert v3 != v2
    indice_3 = _construir_indice_base_fechada()
    assert 988 not in indice_3.sumulas_stj


def test_gravacao_atomica_falha_no_meio_nao_corrompe_arquivo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    arquivo_custom = tmp_path / "custom_precedentes.json"
    monkeypatch.setattr(kb, "CUSTOM_KB_FILE", arquivo_custom)

    item_inicial = kb.add_custom_precedente(
        referencia="Tema 9500/STJ",
        titulo="Precedente Original Íntegro",
        texto_literal="Texto literal íntegro que não pode ser corrompido em caso de falha.",
        tribunal="STJ",
    )
    conteudo_original = arquivo_custom.read_text(encoding="utf-8")

    # Simula falha no meio da substituição atômica de custom_precedentes.json
    replace_real = os.replace

    def replace_com_falha(src, dst):
        if Path(dst) == arquivo_custom:
            raise OSError("Falha simulada de disco durante gravação atômica")
        return replace_real(src, dst)

    monkeypatch.setattr(kb.os, "replace", replace_com_falha)

    with pytest.raises(OSError, match="Falha simulada"):
        kb.update_custom_precedente(item_inicial["id"], titulo="Título Que Falhou")

    # Arquivo principal permanece intacto e válido, sem resíduos .tmp
    assert arquivo_custom.read_text(encoding="utf-8") == conteudo_original
    dados = json.loads(arquivo_custom.read_text(encoding="utf-8"))
    assert len(dados) == 1
    assert dados[0]["titulo"] == "Precedente Original Íntegro"
    tmps = list(tmp_path.glob("*.tmp"))
    assert tmps == []


def test_importacao_acima_de_5000_itens_retorna_422(ambiente_gabinete):
    """TASK-022 (TASK-017 r1): importação com mais de 5.000 itens falha com ValueError/422."""
    client, headers, _ = ambiente_gabinete
    itens_excesso = [
        {
            "referencia": f"Tema {i}/STJ",
            "titulo": f"Título {i}",
            "texto_literal": "Texto literal sintético suficientemente longo para validação.",
            "tribunal": "STJ",
        }
        for i in range(5001)
    ]
    with pytest.raises(ValueError, match="5\\.000"):
        kb.importar_precedentes_gabinete({"formato_versao": 1, "itens": itens_excesso}, confirmar=False)

    r = client.post(
        "/api/knowledge/gabinete/importar",
        headers=headers,
        json={"formato_versao": 1, "itens": itens_excesso, "confirmar": False},
    )
    assert r.status_code == 422
    assert "5.000" in r.json().get("detail", "")

