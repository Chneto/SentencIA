# -*- coding: utf-8 -*-
"""Testes da política de retenção do arquivo morto (TASK-002).

Usa apenas zips fictícios em ``tmp_path`` (nunca toca ``data/`` real).
"""

from __future__ import annotations

import datetime
import json
import zipfile
from pathlib import Path
from typing import Dict

import pytest
from fastapi.testclient import TestClient

import app.core.session_state as session_state
from app.api.security import TOKEN_HEADER, get_session_token
from app.core.retencao_arquivo_morto import RETENCAO_LOG_NAME, aplicar_retencao
from app.main import app


def _criar_zip_ficticio(root: Path, nome: str, conteudo: bytes = b"conteudo-ficticio") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    caminho = root / nome
    with zipfile.ZipFile(caminho, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("peca_sigilosa_interna.pdf", conteudo)
    return caminho


def _gravar_indice_ficticio(root: Path, entradas: Dict[str, Dict[str, object]]) -> Path:
    destino = root / session_state.ARCHIVE_INDEX_NAME
    destino.write_text(
        json.dumps({"versao": 1, "arquivos": entradas}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return destino


@pytest.fixture()
def sandbox_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(session_state, "CASES_DIR", cases_dir)
    return cases_dir


def test_pasta_inexistente_retorna_vazio_sem_erro(sandbox_cases: Path):
    res = aplicar_retencao(manter_dias=30, manter_ultimos=None, simular=True, cases_dir=sandbox_cases)
    assert res["mantidos"] == []
    assert res["elegiveis_remocao"] == []
    assert res["removidos"] == []
    assert res["bytes_liberados"] == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"manter_dias": None, "manter_ultimos": None},
        {"manter_dias": 0, "manter_ultimos": None},
        {"manter_dias": -5, "manter_ultimos": None},
        {"manter_dias": None, "manter_ultimos": 0},
        {"manter_dias": None, "manter_ultimos": -2},
        {"manter_dias": 10, "manter_ultimos": 0},
    ],
)
def test_criterios_ausentes_ou_nao_positivos_levantam_value_error(sandbox_cases: Path, kwargs: Dict[str, object]):
    with pytest.raises(ValueError):
        aplicar_retencao(**kwargs, simular=True, cases_dir=sandbox_cases)  # type: ignore[arg-type]


def test_retencao_somente_dias_por_nome_e_simulacao_nao_apaga(sandbox_cases: Path):
    root = sandbox_cases / session_state.ARCHIVE_DIR_NAME
    z_antigo = _criar_zip_ficticio(root, "20260101_100000.zip")
    z_recente = _criar_zip_ficticio(root, "20261115_120000.zip")
    agora = datetime.datetime(2026, 11, 21, 12, 0, 0)

    res = aplicar_retencao(manter_dias=30, manter_ultimos=None, simular=True, cases_dir=sandbox_cases, agora=agora)
    assert res["simular"] is True
    assert res["mantidos"] == ["20261115_120000.zip"]
    assert res["elegiveis_remocao"] == ["20260101_100000.zip"]
    assert res["removidos"] == []
    # Simulação não apaga nada nem cria log
    assert z_antigo.exists()
    assert z_recente.exists()
    assert not (root / RETENCAO_LOG_NAME).exists()


def test_retencao_somente_ultimos_n(sandbox_cases: Path):
    root = sandbox_cases / session_state.ARCHIVE_DIR_NAME
    _criar_zip_ficticio(root, "20261001_080000.zip")
    _criar_zip_ficticio(root, "20261101_080000.zip")
    _criar_zip_ficticio(root, "20261101_080000_2.zip")

    res = aplicar_retencao(manter_dias=None, manter_ultimos=2, simular=True, cases_dir=sandbox_cases)
    assert res["mantidos"] == ["20261101_080000_2.zip", "20261101_080000.zip"]
    assert res["elegiveis_remocao"] == ["20261001_080000.zip"]


def test_retencao_combinada_mantem_se_atender_qualquer_criterio(sandbox_cases: Path):
    root = sandbox_cases / session_state.ARCHIVE_DIR_NAME
    _criar_zip_ficticio(root, "20250101_090000.zip")  # fora de ambos
    _criar_zip_ficticio(root, "20250601_090000.zip")  # antigo, mas está nos últimos 2
    _criar_zip_ficticio(root, "20261120_090000.zip")  # recente e nos últimos 2
    agora = datetime.datetime(2026, 11, 21, 9, 0, 0)

    res = aplicar_retencao(manter_dias=10, manter_ultimos=2, simular=True, cases_dir=sandbox_cases, agora=agora)
    assert res["mantidos"] == ["20261120_090000.zip", "20250601_090000.zip"]
    assert res["elegiveis_remocao"] == ["20250101_090000.zip"]


def test_ignora_nomes_fora_do_padrao_part_e_subpastas(sandbox_cases: Path):
    root = sandbox_cases / session_state.ARCHIVE_DIR_NAME
    _criar_zip_ficticio(root, "20250101_090000.zip")
    _criar_zip_ficticio(root, "20261120_090000.zip")
    part = root / "20250101_090000.zip.part"
    part.write_bytes(b"parcial")
    estranho = root / "backup_manual.zip"
    estranho.write_bytes(b"manual")
    sub = root / "subpasta"
    sub.mkdir()
    (sub / "20200101_000000.zip").write_bytes(b"sub")

    res = aplicar_retencao(manter_dias=None, manter_ultimos=1, simular=False, cases_dir=sandbox_cases)
    assert res["removidos"] == ["20250101_090000.zip"]
    assert part.exists()
    assert estranho.exists()
    assert (sub / "20200101_000000.zip").exists()


def test_remocao_real_atualiza_indice_grava_log_sem_nomes_internos_e_permite_rearquivar(sandbox_cases: Path):
    # 1) Cria arquivo na raiz de cases e faz reset completo para arquivar
    pdf = sandbox_cases / "autos_ficticios.pdf"
    conteudo = b"%PDF-1.4 processo ficticio para teste de retencao"
    pdf.write_bytes(conteudo)

    r1 = session_state.reset_session_state(mode="completo", apagar_exportacoes=True)
    assert r1["cleared"]["arquivos_movidos"] == 1
    root = sandbox_cases / session_state.ARCHIVE_DIR_NAME
    zips = sorted(p.name for p in root.glob("*.zip"))
    assert len(zips) == 1
    zip_original = zips[0]

    # Renomeia o zip (e referência no índice) para data antiga para expirar por dias
    zip_antigo = "20250101_100000.zip"
    (root / zip_original).rename(root / zip_antigo)
    indice = session_state.ler_indice_arquivo_morto(sandbox_cases)
    for reg in indice["arquivos"].values():
        reg["zip"] = zip_antigo
    _gravar_indice_ficticio(root, indice["arquivos"])

    # 2) Executa remoção real
    agora = datetime.datetime(2026, 11, 21, 12, 0, 0)
    res = aplicar_retencao(manter_dias=30, manter_ultimos=None, simular=False, cases_dir=sandbox_cases, agora=agora)
    assert res["removidos"] == [zip_antigo]
    assert res["bytes_liberados"] > 0
    assert res["indice_entradas_removidas"] == 1
    assert not (root / zip_antigo).exists()

    # Índice não aponta mais para o zip removido
    indice_apos = session_state.ler_indice_arquivo_morto(sandbox_cases)
    assert indice_apos["arquivos"] == {}

    # Log contém apenas nomes de zip, nunca nomes dos arquivos internos
    log_text = (root / RETENCAO_LOG_NAME).read_text(encoding="utf-8").strip()
    registro = json.loads(log_text.splitlines()[-1])
    assert registro["zips_removidos"] == [zip_antigo]
    assert registro["bytes_liberados"] == res["bytes_liberados"]
    assert "autos_ficticios.pdf" not in log_text
    assert "peca_sigilosa_interna.pdf" not in log_text

    # 3) Reset completo com o MESMO conteúdo volta a arquivar (não é deduplicado contra zip apagado)
    pdf.write_bytes(conteudo)
    r2 = session_state.reset_session_state(mode="completo", apagar_exportacoes=True)
    assert r2["cleared"]["arquivos_movidos"] == 1
    assert r2["cleared"]["arquivos_deduplicados"] == []
    assert len(list(root.glob("*.zip"))) == 1


def test_permission_error_registra_falha_e_continua_demais(sandbox_cases: Path, monkeypatch: pytest.MonkeyPatch):
    root = sandbox_cases / session_state.ARCHIVE_DIR_NAME
    z1 = _criar_zip_ficticio(root, "20250101_080000.zip")
    z2 = _criar_zip_ficticio(root, "20250102_080000.zip")
    _criar_zip_ficticio(root, "20261120_080000.zip")

    real_unlink = Path.unlink

    def _unlink_com_erro(self: Path, *a, **kw):
        if self.name == z1.name:
            raise PermissionError("arquivo em uso")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", _unlink_com_erro)
    res = aplicar_retencao(manter_dias=None, manter_ultimos=1, simular=False, cases_dir=sandbox_cases)
    assert res["removidos"] == [z2.name]
    assert len(res["falhas"]) == 1
    assert res["falhas"][0]["zip"] == z1.name
    assert z1.exists()
    assert not z2.exists()


def test_rota_http_retencao_simulacao_bloqueio_sem_confirmar_e_remocao_confirmada(sandbox_cases: Path):
    root = sandbox_cases / session_state.ARCHIVE_DIR_NAME
    z_antigo = _criar_zip_ficticio(root, "20250101_090000.zip")
    z_recente = _criar_zip_ficticio(root, "20261120_090000.zip")

    client = TestClient(app)
    headers = {TOKEN_HEADER: get_session_token()}

    # 1) Simulação -> 200 (não apaga)
    r_sim = client.post(
        "/api/cases/arquivo-morto/retencao",
        headers=headers,
        json={"manter_ultimos": 1, "simular": True, "confirmar": False},
    )
    assert r_sim.status_code == 200, r_sim.text
    assert r_sim.json()["elegiveis_remocao"] == [z_antigo.name]
    assert z_antigo.exists()

    # 2) Remoção sem confirmar -> 422
    r_sem_conf = client.post(
        "/api/cases/arquivo-morto/retencao",
        headers=headers,
        json={"manter_ultimos": 1, "simular": False, "confirmar": False},
    )
    assert r_sem_conf.status_code == 422
    assert z_antigo.exists()

    # 3) Remoção confirmada -> 200
    r_ok = client.post(
        "/api/cases/arquivo-morto/retencao",
        headers=headers,
        json={"manter_ultimos": 1, "simular": False, "confirmar": True},
    )
    assert r_ok.status_code == 200, r_ok.text
    assert r_ok.json()["removidos"] == [z_antigo.name]
    assert not z_antigo.exists()
    assert z_recente.exists()


def test_falha_ao_gravar_log_nao_vira_500_e_gera_aviso_log(tmp_path: Path, monkeypatch):
    """TASK-022 (TASK-002 r1/r2): falha ao gravar log após apagar zips retorna aviso_log sem 500."""
    from app.core import retencao_arquivo_morto as mod_ret

    cases_dir = tmp_path / "cases"
    arq_dir = cases_dir / "_arquivados"
    arq_dir.mkdir(parents=True)
    z_antigo = arq_dir / "20200101_080000.zip"
    z_antigo.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    def _falhar_log(*args, **kwargs):
        raise PermissionError("C:/SentencIA/data/cases/_arquivados/retencao_log.jsonl bloqueado")

    monkeypatch.setattr(mod_ret, "_registrar_log_retencao", _falhar_log)

    res = mod_ret.aplicar_retencao(
        manter_dias=30,
        manter_ultimos=None,
        simular=False,
        cases_dir=cases_dir,
        agora=datetime.datetime(2026, 11, 21, 12, 0, 0),
    )
    assert res["removidos"] == ["20200101_080000.zip"]
    assert not z_antigo.exists()
    assert "aviso_log" in res
    assert "PermissionError" in res["aviso_log"]
    assert "data/" not in res["aviso_log"] and "data\\" not in res["aviso_log"]


def test_falha_remocao_registra_apenas_tipo_da_excecao_sem_caminhos(tmp_path: Path, monkeypatch):
    """TASK-022 (TASK-002 r1/r2): log e falhas gravam só type(exc).__name__, sem caminhos de data/."""
    from app.core import retencao_arquivo_morto as mod_ret

    cases_dir = tmp_path / "data" / "cases"
    arq_dir = cases_dir / "_arquivados"
    arq_dir.mkdir(parents=True)
    z_antigo = arq_dir / "20200101_080000.zip"
    z_antigo.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    orig_unlink = Path.unlink

    def _unlink_com_caminho(self, *args, **kwargs):
        if self.name == "20200101_080000.zip":
            raise PermissionError(f"Arquivo em uso: {self}")
        return orig_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _unlink_com_caminho)

    res = mod_ret.aplicar_retencao(
        manter_dias=30,
        manter_ultimos=None,
        simular=False,
        cases_dir=cases_dir,
        agora=datetime.datetime(2026, 11, 21, 12, 0, 0),
    )
    assert res["falhas"] == [{"zip": "20200101_080000.zip", "erro": "PermissionError"}]
    log_txt = (arq_dir / mod_ret.RETENCAO_LOG_NAME).read_text(encoding="utf-8")
    assert '"erro": "PermissionError"' in log_txt
    assert "data" not in log_txt.lower()

