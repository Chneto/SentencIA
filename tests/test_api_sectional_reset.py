"""Testes da API seccional real + reset de produção (v2.0.0).

Cobertura:
- ``POST /api/sentence/generate-sectional`` com os 5 cenários de
  ``tests/fixtures/case_scenario_*.json`` (pipeline real, 5 seções, auditoria), exigindo o
  veredicto do juízo por crime (sem veredicto → 422 ``decisao_judicial_pendente``);
- ``POST /api/sentence/regenerate-section`` (válido, 400 inválida, 409 sem pipeline/incompleto);
- ``POST /api/cases/reset`` modos ``producao`` e ``completo`` (arquivamento em zip deduplicado
  por hash em ``data/cases/_arquivados/<timestamp>.zip``, exclusão definitiva só com confirmação,
  preservação de knowledge_base/templates/user_settings);
- ``GET /api/cases/active`` sem efeitos colaterais e ``POST /api/cases/load-from-disk``;
- anti-poluição A → reset → B (nenhum dado do caso A na sentença B);
- troca de caso (``load-demo`` / ``upload-files``) limpa produção anterior;
- ``GET /api/status`` com bloco ``session`` (``/api/health`` é mínimo);
- comprovação de que as rotas reais vêm de ``app.main``.

Isolamento: TODOS os testes usam ``tmp_path`` + ``monkeypatch`` para ``CASES_DIR``/``EXPORTS_DIR``/
``KNOWLEDGE_DIR``/``TEMPLATES_DIR``/``SETTINGS_FILE`` — nenhum dado real do usuário é lido,
escrito ou apagado. O cliente envia o token de sessão REAL obtido do app.
"""

from __future__ import annotations

import copy
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

import app.core.config as core_config
import app.core.session_state as session_state
import app.main as main_module
from app.api.security import TOKEN_HEADER, get_session_token
from app.core.case_context import obter_contexto
from app.demo.demo_case import get_demo_case_bundle
from app.generation.sectional_pipeline import SecaoTipo
from app.main import ACTIVE_CASE_STATE, app

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

CASE_FIXTURES = [
    "case_scenario_1_ameaca_lesao.json",
    "case_scenario_2_descumprimento_mpu.json",
    "case_scenario_3_lesao_palavra_vitima.json",
    "case_scenario_4_multirreincidente_sumula444.json",
    "case_scenario_5_autocorrection_fallback.json",
]


def _pdf_minimo_valido(texto: str = "ID-0000000 - Autos de teste") -> bytes:
    """PDF mínimo REAL (PyMuPDF) — o upload valida fisicamente o PDF antes de segmentar."""
    import pymupdf

    doc = pymupdf.open()
    try:
        pagina = doc.new_page()
        pagina.insert_text((50, 72), texto)
        return doc.tobytes()
    finally:
        doc.close()


STATE_KEYS = [
    "bundle",
    "last_sentence",
    "last_audit",
    "explicit_demo",
    "last_dosimetria",
    "pipeline_engine",
    "pipeline_snapshot",
    "generation_id",
    "explicit_reset",
    "last_reset",
    "upload_status",
    "motores_cache",
]

PRODUCTION_KEYS = [
    "last_sentence",
    "last_audit",
    "last_dosimetria",
    "pipeline_engine",
    "pipeline_snapshot",
]


def load_case(filename: str) -> Dict[str, Any]:
    with open(FIXTURES_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


def veredictos_condenatorios(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Override com o veredicto do juízo (``condenar``) para cada crime imputado do cenário.

    Na v2 a sentença de caso real exige veredicto explícito por crime (o motor nunca condena por
    padrão); os cenários de fixture simulam a decisão do magistrado.
    """
    crimes = list(obter_contexto(bundle).meta.crimes_imputados) or list(bundle.get("crimes_imputados") or [])
    assert crimes, "O cenário precisa ter crimes imputados para o teste."
    return {"crimes_config": [{"codigo": c, "veredicto": "condenar"} for c in crimes]}


def _generate(client: TestClient, bundle: Dict[str, Any] | None = None, *, com_veredicto: bool = True, **overrides):
    payload: Dict[str, Any] = {"use_ollama": False}
    if bundle is not None:
        payload["case_bundle"] = bundle
        if com_veredicto and not bundle.get("is_demo"):
            payload["custom_dosimetria_override"] = veredictos_condenatorios(bundle)
    payload.update(overrides)
    return client.post("/api/sentence/generate-sectional", json=payload)


def _reset(client: TestClient, mode: str = "producao", apagar_exportacoes: bool = True, **extras):
    return client.post(
        "/api/cases/reset",
        json={"mode": mode, "apagar_exportacoes": apagar_exportacoes, **extras},
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_auditoria_honesta(auditoria: Dict[str, Any], marcadores_sentenca: List[str]) -> None:
    """Forma 100, zero alucinação, teses enfrentadas e penas conferidas; os marcadores
    ``[COMPLETAR PELO JUÍZO: ...]`` (lacunas que só o juízo preenche) são os únicos que podem
    impedir a aprovação."""
    assert auditoria["score_forma"] == 100.0, auditoria["detalhes_camadas"]
    assert auditoria["ids_inexistentes"] == []
    assert auditoria["citacoes_nao_autorizadas"] == []
    assert auditoria["teses_omitidas"] == []
    assert auditoria["penas_divergentes"] == []
    assert set(auditoria["marcadores_pendentes"]) == set(marcadores_sentenca)
    assert auditoria["aprovada_sem_alucinacao"] is (not marcadores_sentenca)
    if not marcadores_sentenca:
        assert auditoria["score_confiabilidade"] == 100.0
    else:
        assert auditoria["score_confiabilidade"] < 100.0


# ==============================================================================
# Fixtures de isolamento
# ==============================================================================


@pytest.fixture(autouse=True)
def isolated_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dict[str, Path]:
    """Sandbox de dados + snapshot/restauração do ACTIVE_CASE_STATE global."""
    data_dir = tmp_path / "data"
    cases_dir = data_dir / "cases"
    knowledge_dir = data_dir / "knowledge_base"
    templates_dir = data_dir / "templates_gabinete"
    exports_dir = data_dir / "exports"
    for directory in (cases_dir, knowledge_dir, templates_dir, exports_dir):
        directory.mkdir(parents=True, exist_ok=True)
    settings_file = data_dir / "user_settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "tribunal_nome": "TRIBUNAL DE JUSTIÇA DO ESTADO (SANDBOX)",
                "juizado_nome": "JUIZADO SANDBOX JVDFM",
                "comarca_nome": "COMARCA SANDBOX",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Sandbox em TODOS os pontos de resolução de diretórios (main, config e session_state).
    monkeypatch.setattr(core_config, "DATA_DIR", data_dir)
    monkeypatch.setattr(core_config, "CASES_DIR", cases_dir)
    monkeypatch.setattr(core_config, "KNOWLEDGE_DIR", knowledge_dir)
    monkeypatch.setattr(core_config, "TEMPLATES_DIR", templates_dir)
    monkeypatch.setattr(core_config, "EXPORTS_DIR", exports_dir)
    monkeypatch.setattr(core_config, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(main_module, "CASES_DIR", cases_dir)
    monkeypatch.setattr(main_module, "EXPORTS_DIR", exports_dir)
    monkeypatch.setattr(session_state, "CASES_DIR", cases_dir)
    monkeypatch.setattr(session_state, "EXPORTS_DIR", exports_dir)

    state = ACTIVE_CASE_STATE
    with session_state.STATE_LOCK:
        saved = {key: copy.deepcopy(state.get(key)) for key in STATE_KEYS}
        state["bundle"] = get_demo_case_bundle()
        state["last_sentence"] = None
        state["last_audit"] = None
        state["explicit_demo"] = False
        state["last_dosimetria"] = None
        state["pipeline_engine"] = None
        state["pipeline_snapshot"] = None
        state["generation_id"] = 0
        state["explicit_reset"] = False
        state["last_reset"] = None
        state["upload_status"] = {"pdfs_processo": [], "midias_audiencia": []}
        state["motores_cache"] = None

    sandbox = {
        "data_dir": data_dir,
        "cases_dir": cases_dir,
        "exports_dir": exports_dir,
        "knowledge_dir": knowledge_dir,
        "templates_dir": templates_dir,
        "settings_file": settings_file,
    }
    try:
        yield sandbox
    finally:
        with session_state.STATE_LOCK:
            for key, value in saved.items():
                state[key] = value


@pytest.fixture
def client(isolated_session: Dict[str, Path]):
    with TestClient(app, headers={TOKEN_HEADER: get_session_token()}) as test_client:
        yield test_client


# ==============================================================================
# Rotas reais registradas
# ==============================================================================


class TestRotasReais:
    def test_sectional_routes_registered_from_app_main(self):
        paths: Dict[str, List[str]] = {}
        for route in app.routes:
            if hasattr(route, "endpoint"):
                paths.setdefault(route.path, []).append(getattr(route.endpoint, "__module__", ""))
        for path in (
            "/api/sentence/generate-sectional",
            "/api/sentence/regenerate-section",
            "/api/cases/reset",
            "/api/cases/load-from-disk",
            "/api/cases/metadata-override",
        ):
            assert path in paths, f"Rota real ausente em app.main: {path}"
            assert paths[path] == ["app.main"], f"{path} deve ser registrada exclusivamente por app.main, obtido: {paths[path]}"


# ==============================================================================
# Geração seccional real
# ==============================================================================


class TestGenerateSectional:
    @pytest.mark.parametrize("fixture_name", CASE_FIXTURES)
    def test_generate_sectional_5_fixtures(self, client: TestClient, fixture_name: str):
        bundle = load_case(fixture_name)
        response = _generate(client, bundle)
        assert response.status_code == 200, response.text
        data = response.json()

        assert data["status"] == "completed"
        assert data["pipeline_seccional"] is True
        assert set(data["secoes"]) == {"relatorio", "preliminares", "merito", "dosimetria", "dispositivo"}
        for secao_nome, texto in data["secoes"].items():
            assert isinstance(texto, str) and len(texto) > 200, secao_nome
        assert_auditoria_honesta(data["auditoria"], data["sentenca"]["marcadores_pendentes"])
        assert data["score_auditoria"] == data["auditoria"]["score_confiabilidade"]
        assert data["marcadores_pendentes"] == data["sentenca"]["marcadores_pendentes"]
        assert data["exportacao_liberada"] is data["auditoria"]["aprovada_sem_alucinacao"]
        # Veredicto informado: nada é presumido.
        assert data["simulacao_presume_condenacao"] is False
        assert all(d["presumido"] is False for d in data["sentenca"]["decisoes_crimes"])
        assert isinstance(data["generation_id"], int) and data["generation_id"] >= 1

        telemetria = data["telemetria"]
        assert set(telemetria["secoes"]) == set(data["secoes"])
        for secao_telemetria in telemetria["secoes"].values():
            assert secao_telemetria["selada"] is True
            assert secao_telemetria["status"] in ("aprovado", "fallback_acionado")
            assert secao_telemetria["tentativas"] >= 1
            assert isinstance(secao_telemetria["fallback"], bool)
            assert secao_telemetria["fonte"] in ("deterministico", "llm", "fallback_deterministico")
        assert telemetria["totais"]["secoes_seladas"] == 5
        assert telemetria["pipeline_completo"] is True
        assert data["secoes_fallback"] == telemetria["secoes_fallback"]

        # HTML PJe (com marca d'água quando a minuta não está aprovada).
        assert isinstance(data["html_pje"], str) and len(data["html_pje"]) > 1000
        assert "RELATÓRIO" in data["html_pje"].upper()
        assert ("MINUTA NÃO AUDITADA" in data["html_pje"]) is (not data["auditoria"]["aprovada_sem_alucinacao"])

        assert ACTIVE_CASE_STATE["last_sentence"] is not None
        assert ACTIVE_CASE_STATE["last_audit"] is not None
        assert ACTIVE_CASE_STATE["last_dosimetria"] is not None
        assert ACTIVE_CASE_STATE["pipeline_engine"] is not None
        assert ACTIVE_CASE_STATE["pipeline_snapshot"]["totais"]["secoes_seladas"] == 5
        assert ACTIVE_CASE_STATE["bundle"]["numero_processo"] == data["sentenca"]["numero_processo"]

    @pytest.mark.parametrize("fixture_name", CASE_FIXTURES[:2])
    def test_generate_sectional_sem_veredicto_422_com_crimes_pendentes(self, client: TestClient, fixture_name: str):
        bundle = load_case(fixture_name)
        response = _generate(client, bundle, com_veredicto=False)
        assert response.status_code == 422, response.text
        corpo = response.json()
        assert corpo["erro"] == "decisao_judicial_pendente"
        crimes = veredictos_condenatorios(bundle)["crimes_config"]
        assert set(corpo["crimes_pendentes"]) == {c["codigo"] for c in crimes}
        assert corpo["campos"] == [f"veredicto.{c}" for c in corpo["crimes_pendentes"]]
        assert "veredicto" in corpo["detail"]
        assert ACTIVE_CASE_STATE["last_sentence"] is None

    def test_generate_sectional_sem_bundle_409(self, client: TestClient):
        assert _reset(client, "completo").status_code == 200
        response = _generate(client)
        assert response.status_code == 409

    def test_generate_sectional_bundle_invalido_422(self, client: TestClient):
        for bundle_invalido in ({"case_id": "SEM-RAW-DOCS"}, {"raw_documents": []}):
            response = _generate(client, bundle_invalido, com_veredicto=False)
            assert response.status_code == 422, response.text

    def test_generate_sectional_secoes_habilitadas_invalidas_422(self, client: TestClient):
        response = _generate(client, secoes_habilitadas=["relatorio", "secao-que-nao-existe"])
        assert response.status_code == 422

    def test_generate_sectional_aceita_override_dosimetria_da_ui(self, client: TestClient):
        """Formato EXATO do payload da UI (sem case_bundle, override dict) no caso demonstrativo."""
        payload = {
            "use_ollama": False,
            "usar_duplo_crivo": True,
            "custom_dosimetria_override": {
                "criterio_primeira_fase": "um_oitavo_intervalo",
                "culpabilidade_lesao": True,
                "motivos_lesao": False,
                "circunstancias_lesao": False,
                "consequencias_lesao": False,
                "confissao_art24a": True,
                "reincidente": False,
                "agravante_art61f": False,
                "valor_dano_moral": 5000.0,
            },
        }
        response = client.post("/api/sentence/generate-sectional", json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "completed"
        assert data["sentenca"]["dosimetria_dados"] is not None
        assert isinstance(data["usar_duplo_crivo"], bool)

    def test_generate_sectional_usa_bundle_ativo_sem_case_bundle(self, client: TestClient):
        demo = client.post("/api/cases/load-demo").json()
        response = _generate(client)
        assert response.status_code == 200, response.text
        assert response.json()["sentenca"]["numero_processo"] == demo["numero_processo"]

    def test_generate_sectional_sincroniza_bundle_fornecido(self, client: TestClient):
        case2 = load_case(CASE_FIXTURES[1])
        response = _generate(client, case2)
        assert response.status_code == 200, response.text
        assert ACTIVE_CASE_STATE["bundle"]["numero_processo"] == case2["numero_processo"]
        active = client.get("/api/cases/active").json()
        assert active["empty"] is False
        assert active["is_demo"] is False
        assert active["numero_processo"] == case2["numero_processo"]

    def test_generation_id_incrementa_entre_geracoes(self, client: TestClient):
        case1 = load_case(CASE_FIXTURES[0])
        first = _generate(client, case1).json()
        second = _generate(client, case1).json()
        assert second["generation_id"] == first["generation_id"] + 1


# ==============================================================================
# Regeneração atômica de seção
# ==============================================================================


class TestRegenerateSection:
    def test_regenerate_section_valida_reaudita_e_preserva_vizinhas(self, client: TestClient):
        case1 = load_case(CASE_FIXTURES[0])
        gerada = _generate(client, case1)
        assert gerada.status_code == 200, gerada.text
        sentenca_anterior = gerada.json()["sentenca"]
        generation_id = gerada.json()["generation_id"]

        response = client.post(
            "/api/sentence/regenerate-section",
            json={
                "secao": "merito",
                "instrucoes_cirurgicas": "Aprofundar o enfrentamento da tese de legítima defesa.",
                "paragrafo_especifico": "2.4",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()

        assert data["status"] == "regenerated"
        assert data["secao"] == "merito"
        assert isinstance(data["texto_regenerado"], str) and len(data["texto_regenerado"]) > 200
        assert_auditoria_honesta(data["auditoria"], data["marcadores_pendentes"])
        assert data["score_confiabilidade"] == data["auditoria"]["score_confiabilidade"]
        assert data["generation_id"] == generation_id  # mesma minuta
        assert "secoes" not in data  # contrato enxuto da regeneração

        sentenca_atual = ACTIVE_CASE_STATE["last_sentence"]
        assert sentenca_atual is not None
        assert sentenca_atual.secao_merito == data["texto_regenerado"]
        assert sentenca_atual.secao_relatorio == sentenca_anterior["secao_relatorio"]
        assert sentenca_atual.secao_preliminares == sentenca_anterior["secao_preliminares"]
        assert sentenca_atual.secao_dosimetria == sentenca_anterior["secao_dosimetria"]
        assert sentenca_atual.secao_dispositivo == sentenca_anterior["secao_dispositivo"]
        assert ACTIVE_CASE_STATE["last_audit"] is not None
        assert ACTIVE_CASE_STATE["pipeline_snapshot"]["secoes"]["merito"]["selada"] is True

    def test_regenerate_section_invalida_400(self, client: TestClient):
        assert _generate(client, load_case(CASE_FIXTURES[0])).status_code == 200
        response = client.post("/api/sentence/regenerate-section", json={"secao": "secao_inexistente"})
        assert response.status_code == 400

    def test_regenerate_section_sem_pipeline_409(self, client: TestClient):
        assert ACTIVE_CASE_STATE["pipeline_engine"] is None
        response = client.post("/api/sentence/regenerate-section", json={"secao": "merito"})
        assert response.status_code == 409

    def test_regenerate_section_pipeline_incompleto_409(self, client: TestClient):
        assert _generate(client, load_case(CASE_FIXTURES[0])).status_code == 200
        engine = ACTIVE_CASE_STATE["pipeline_engine"]
        assert engine is not None
        engine.estado.secoes_seladas.pop(SecaoTipo.PRELIMINARES, None)
        response = client.post("/api/sentence/regenerate-section", json={"secao": "dispositivo"})
        assert response.status_code == 409

    def test_regenerate_section_concorrente_409(self, client: TestClient):
        assert _generate(client, load_case(CASE_FIXTURES[0])).status_code == 200
        assert session_state.PIPELINE_LOCK.acquire(blocking=False)
        try:
            response = client.post("/api/sentence/regenerate-section", json={"secao": "merito"})
        finally:
            session_state.PIPELINE_LOCK.release()
        assert response.status_code == 409
        assert "andamento" in response.json()["detail"]


# ==============================================================================
# Reset modo "producao"
# ==============================================================================


class TestResetProducao:
    def test_reset_producao_limpa_producao_e_preserva_autos(self, client: TestClient, isolated_session: Dict[str, Path]):
        cases_dir = isolated_session["cases_dir"]
        exports_dir = isolated_session["exports_dir"]
        case1 = load_case(CASE_FIXTURES[0])
        assert _generate(client, case1).status_code == 200
        generation_id_antes = ACTIVE_CASE_STATE["generation_id"]

        autos_pdf = cases_dir / "0801245-78.2025.8.20.5001.pdf"
        midia = cases_dir / "audiencia.mp4"
        cache_pje = cases_dir / "0801245.pje_cache_v2.json"
        cache_transcript = cases_dir / "0801245.transcript_v2.json"
        export_legado = cases_dir / "Sentenca_JVDFM_0801245_78_2025_8_20_5001.docx"
        export_docx = exports_dir / "Sentenca_JVDFM_0801245_78_2025_8_20_5001.docx"
        export_pdf = exports_dir / "Sentenca_JVDFM_0801245_78_2025_8_20_5001.pdf"
        autos_pdf.write_bytes(b"%PDF-1.4 autos reais (fake)")
        midia.write_bytes(b"video fake")
        cache_pje.write_text("{}", encoding="utf-8")
        cache_transcript.write_text("{}", encoding="utf-8")
        export_legado.write_text("export legado", encoding="utf-8")
        export_docx.write_text("export docx", encoding="utf-8")
        export_pdf.write_text("export pdf", encoding="utf-8")

        response = _reset(client, "producao")
        assert response.status_code == 200, response.text
        data = response.json()

        assert data["status"] == "reset"
        assert data["mode"] == "producao"
        assert data["generation_id"] == generation_id_antes + 1
        cleared = data["cleared"]
        assert cleared["minuta"] is True
        assert cleared["auditoria"] is True
        assert cleared["dosimetria"] is True
        assert cleared["engine"] is True
        assert cleared["snapshot"] is True
        assert cleared["arquivos_movidos"] == 0
        assert cleared["exportacoes_removidas"] == 3

        for key in PRODUCTION_KEYS:
            assert ACTIVE_CASE_STATE[key] is None, f"{key} deveria estar None"
        assert ACTIVE_CASE_STATE["bundle"] is not None
        assert ACTIVE_CASE_STATE["bundle"]["numero_processo"] == case1["numero_processo"]

        assert not export_legado.exists()
        assert not export_docx.exists()
        assert not export_pdf.exists()
        assert autos_pdf.exists()
        assert midia.exists()
        assert cache_pje.exists()
        assert cache_transcript.exists()

        assert data["active_case"]["empty"] is False
        assert data["active_case"]["numero_processo"] == case1["numero_processo"]
        active = client.get("/api/cases/active").json()
        assert active["empty"] is False
        assert active["numero_processo"] == case1["numero_processo"]

    def test_reset_producao_com_apagar_exportacoes_false_preserva_exportacoes(
        self, client: TestClient, isolated_session: Dict[str, Path]
    ):
        export_docx = isolated_session["exports_dir"] / "Sentenca_JVDFM_999.docx"
        export_docx.write_text("export", encoding="utf-8")
        response = _reset(client, "producao", apagar_exportacoes=False)
        assert response.status_code == 200
        assert response.json()["cleared"]["exportacoes_removidas"] == 0
        assert export_docx.exists()

    def test_reset_producao_mantem_explicit_demo(self, client: TestClient):
        client.post("/api/cases/load-demo")
        assert ACTIVE_CASE_STATE["explicit_demo"] is True
        assert _reset(client, "producao").status_code == 200
        assert ACTIVE_CASE_STATE["explicit_demo"] is True
        assert ACTIVE_CASE_STATE["bundle"] is not None

    def test_reset_producao_sem_producao_anterior_e_idempotente(self, client: TestClient):
        first = _reset(client, "producao")
        second = _reset(client, "producao")
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["cleared"]["minuta"] is False
        assert second.json()["cleared"]["engine"] is False
        assert second.json()["generation_id"] == first.json()["generation_id"] + 1

    def test_reset_producao_rejeita_exclusao_definitiva(self, client: TestClient, isolated_session: Dict[str, Path]):
        autos = isolated_session["cases_dir"] / "autos.pdf"
        autos.write_bytes(b"%PDF-1.4")
        response = _reset(client, "producao", excluir_definitivamente=True, confirmar_exclusao_definitiva=True)
        assert response.status_code == 422
        assert autos.exists()


# ==============================================================================
# Reset modo "completo" (arquivamento em zip deduplicado)
# ==============================================================================


def _zip_do_reset(cases_dir: Path, cleared: Dict[str, Any]) -> Path:
    assert cleared["arquivados_em"].startswith("cases/_arquivados/")
    assert cleared["arquivados_em"].endswith(".zip")
    zip_path = cases_dir / session_state.ARCHIVE_DIR_NAME / cleared["arquivados_em"].rsplit("/", 1)[-1]
    assert zip_path.is_file()
    return zip_path


class TestResetCompleto:
    def test_reset_completo_arquiva_autos_em_zip_e_limpa_raiz(self, client: TestClient, isolated_session: Dict[str, Path]):
        cases_dir = isolated_session["cases_dir"]
        case1 = load_case(CASE_FIXTURES[0])
        gen = _generate(client, case1)
        assert gen.status_code == 200
        generation_id_antes = ACTIVE_CASE_STATE["generation_id"]

        arquivos_autos = {
            "0801245-78.2025.8.20.5001.pdf": b"%PDF-1.4 autos",
            "audiencia_ofendida.mp4": b"video",
        }
        arquivos_caches = {
            "0801245.pje_cache_v2.json": b'{"cache": 1}',
            "0801245.transcript_v2.json": b'{"cache": 2}',
        }
        export_docx = "Sentenca_JVDFM_0801245_78_2025_8_20_5001.docx"
        for name, content in {**arquivos_autos, **arquivos_caches}.items():
            (cases_dir / name).write_bytes(content)
        (cases_dir / export_docx).write_text("export", encoding="utf-8")

        response = _reset(client, "completo")
        assert response.status_code == 200, response.text
        data = response.json()

        assert data["status"] == "reset"
        assert data["mode"] == "completo"
        assert data["generation_id"] == generation_id_antes + 1
        cleared = data["cleared"]
        assert cleared["arquivos_movidos"] == 4
        assert cleared["arquivos_deduplicados"] == []
        assert cleared["exportacoes_removidas"] == 1
        assert cleared["bundle"] is True
        assert cleared["exclusao_definitiva"] is False

        assert [p.name for p in cases_dir.iterdir() if p.is_file()] == []

        zip_path = _zip_do_reset(cases_dir, cleared)
        with zipfile.ZipFile(zip_path) as zf:
            assert set(zf.namelist()) == set(arquivos_autos) | set(arquivos_caches)
            for name, content in {**arquivos_autos, **arquivos_caches}.items():
                assert zf.read(name) == content
        indice = json.loads((zip_path.parent / session_state.ARCHIVE_INDEX_NAME).read_text(encoding="utf-8"))
        hashes = {hashlib.sha256(c).hexdigest() for c in {**arquivos_autos, **arquivos_caches}.values()}
        assert set(indice["arquivos"]) == hashes
        assert all(reg["zip"] == zip_path.name for reg in indice["arquivos"].values())

        assert ACTIVE_CASE_STATE["bundle"] is None
        assert ACTIVE_CASE_STATE["explicit_reset"] is True
        assert ACTIVE_CASE_STATE["explicit_demo"] is False
        assert ACTIVE_CASE_STATE["last_reset"] is not None
        for key in PRODUCTION_KEYS:
            assert ACTIVE_CASE_STATE[key] is None

        assert data["active_case"] == client.get("/api/cases/active").json()
        assert data["active_case"]["empty"] is True

    def test_reset_completo_deduplica_conteudo_ja_arquivado(self, client: TestClient, isolated_session: Dict[str, Path]):
        cases_dir = isolated_session["cases_dir"]
        (cases_dir / "autos.pdf").write_bytes(b"%PDF-1.4 conteudo A")
        primeiro = _reset(client, "completo").json()["cleared"]
        zip1 = _zip_do_reset(cases_dir, primeiro)

        # O mesmo conteúdo volta (mesmo ou outro nome) + um arquivo novo.
        (cases_dir / "autos_copia.pdf").write_bytes(b"%PDF-1.4 conteudo A")
        (cases_dir / "midia_nova.mp3").write_bytes(b"audio B")
        segundo = _reset(client, "completo").json()["cleared"]
        assert segundo["arquivos_deduplicados"] == ["autos_copia.pdf"]
        assert segundo["arquivos_movidos_lista"] == ["midia_nova.mp3"]
        assert segundo["arquivos_movidos"] == 2
        zip2 = _zip_do_reset(cases_dir, segundo)
        assert zip2 != zip1
        with zipfile.ZipFile(zip2) as zf:
            assert zf.namelist() == ["midia_nova.mp3"]
        assert [p.name for p in cases_dir.iterdir() if p.is_file()] == []

        # Só duplicatas: nenhum zip novo é criado.
        (cases_dir / "de_novo.pdf").write_bytes(b"%PDF-1.4 conteudo A")
        terceiro = _reset(client, "completo").json()["cleared"]
        assert terceiro["arquivados_em"] is None
        assert terceiro["arquivos_deduplicados"] == ["de_novo.pdf"]
        assert sorted(p.name for p in (cases_dir / session_state.ARCHIVE_DIR_NAME).glob("*.zip")) == sorted(
            [zip1.name, zip2.name]
        )

    def test_reset_completo_apagar_exportacoes_false_arquiva_exportacoes(
        self, client: TestClient, isolated_session: Dict[str, Path]
    ):
        cases_dir = isolated_session["cases_dir"]
        assert _generate(client, load_case(CASE_FIXTURES[0])).status_code == 200
        export = cases_dir / "Sentenca_JVDFM_777.docx"
        export.write_text("export", encoding="utf-8")

        response = _reset(client, "completo", apagar_exportacoes=False)
        assert response.status_code == 200
        cleared = response.json()["cleared"]
        assert cleared["exportacoes_removidas"] == 0
        assert cleared["arquivos_movidos"] == 1
        assert not export.exists()
        assert [p.name for p in cases_dir.iterdir() if p.is_file()] == []
        with zipfile.ZipFile(_zip_do_reset(cases_dir, cleared)) as zf:
            assert zf.namelist() == [export.name]

    def test_reset_completo_exclusao_definitiva_exige_confirmacao(
        self, client: TestClient, isolated_session: Dict[str, Path]
    ):
        cases_dir = isolated_session["cases_dir"]
        autos = cases_dir / "autos.pdf"
        autos.write_bytes(b"%PDF-1.4 sigiloso")

        sem_confirmacao = _reset(client, "completo", excluir_definitivamente=True)
        assert sem_confirmacao.status_code == 422
        assert "confirma" in sem_confirmacao.json()["detail"].lower()
        assert autos.exists()
        assert ACTIVE_CASE_STATE["bundle"] is not None  # nada foi reiniciado

        confirmado = _reset(client, "completo", excluir_definitivamente=True, confirmar_exclusao_definitiva=True)
        assert confirmado.status_code == 200, confirmado.text
        cleared = confirmado.json()["cleared"]
        assert cleared["exclusao_definitiva"] is True
        assert cleared["arquivos_excluidos_definitivamente"] == ["autos.pdf"]
        assert cleared["arquivados_em"] is None
        assert not autos.exists()
        assert not (cases_dir / session_state.ARCHIVE_DIR_NAME).exists()
        assert "EXCLUÍDOS" in confirmado.json()["mensagem"]

    def test_reset_completo_apagar_arquivo_morto_confirmado(self, client: TestClient, isolated_session: Dict[str, Path]):
        cases_dir = isolated_session["cases_dir"]
        (cases_dir / "autos.pdf").write_bytes(b"%PDF-1.4")
        assert _reset(client, "completo").status_code == 200
        archive_root = cases_dir / session_state.ARCHIVE_DIR_NAME
        assert archive_root.is_dir()

        assert _reset(client, "completo", apagar_arquivo_morto=True).status_code == 422
        assert archive_root.is_dir()
        response = _reset(client, "completo", apagar_arquivo_morto=True, confirmar_exclusao_definitiva=True)
        assert response.status_code == 200
        assert response.json()["cleared"]["arquivo_morto_apagado"] is True
        assert not archive_root.exists()

    def test_reset_completo_preserva_knowledge_templates_e_settings(
        self, client: TestClient, isolated_session: Dict[str, Path]
    ):
        knowledge_dir = isolated_session["knowledge_dir"]
        templates_dir = isolated_session["templates_dir"]
        settings_file = isolated_session["settings_file"]
        cases_dir = isolated_session["cases_dir"]

        knowledge_file = knowledge_dir / "sumulas_verificadas.json"
        template_file = templates_dir / "modelo_gabinete.docx"
        knowledge_file.write_text('{"sumulas": ["Súmula 588 STJ"]}', encoding="utf-8")
        template_file.write_bytes(b"template-docx")
        (cases_dir / "autos_antigos.pdf").write_bytes(b"%PDF-1.4")

        hashes_antes = {
            "knowledge": _sha256(knowledge_file),
            "template": _sha256(template_file),
            "settings": _sha256(settings_file),
        }

        assert _reset(client, "completo").status_code == 200

        assert knowledge_file.exists() and _sha256(knowledge_file) == hashes_antes["knowledge"]
        assert template_file.exists() and _sha256(template_file) == hashes_antes["template"]
        assert settings_file.exists() and _sha256(settings_file) == hashes_antes["settings"]
        assert not (cases_dir / "autos_antigos.pdf").exists()

    def test_reset_completo_nao_apaga_arquivos_fora_de_cases_dir(self, client: TestClient, isolated_session: Dict[str, Path]):
        data_dir = isolated_session["data_dir"]
        cases_dir = isolated_session["cases_dir"]
        fora_do_cases = data_dir / "documento_importante.txt"
        fora_do_cases.write_text("não pode ser apagado", encoding="utf-8")
        (cases_dir / "autos.pdf").write_bytes(b"%PDF-1.4")

        assert _reset(client, "completo", excluir_definitivamente=True, confirmar_exclusao_definitiva=True).status_code == 200

        assert fora_do_cases.exists()
        assert fora_do_cases.read_text(encoding="utf-8") == "não pode ser apagado"

    def test_reset_completo_idempotente(self, client: TestClient, isolated_session: Dict[str, Path]):
        cases_dir = isolated_session["cases_dir"]
        (cases_dir / "autos.pdf").write_bytes(b"%PDF-1.4")
        first = _reset(client, "completo")
        second = _reset(client, "completo")
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["cleared"]["arquivos_movidos"] == 1
        assert second.json()["cleared"]["arquivos_movidos"] == 0
        assert second.json()["cleared"]["arquivados_em"] is None
        assert second.json()["generation_id"] == first.json()["generation_id"] + 1
        assert second.json()["active_case"]["empty"] is True
        assert ACTIVE_CASE_STATE["bundle"] is None
        assert [p.name for p in cases_dir.iterdir() if p.is_file()] == []


# ==============================================================================
# GET /api/cases/active (sem efeitos) e POST /api/cases/load-from-disk
# ==============================================================================


class TestGetActiveCase:
    def test_get_active_vazio_apos_reset_completo(self, client: TestClient):
        assert _reset(client, "completo").status_code == 200
        active = client.get("/api/cases/active").json()
        assert active["empty"] is True
        assert active["case_id"] is None
        assert active["numero_processo"] == ""
        assert active["ofendida"] == ""
        assert active["reu"] == ""
        assert active["pecas"] == []
        assert active["metadados"]["crimes_imputados"] == []
        assert active["matriz_oral"]["depoentes"] == []

    def test_get_active_nunca_carrega_autos_do_disco(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, isolated_session: Dict[str, Path]
    ):
        (isolated_session["cases_dir"] / "autos.pdf").write_bytes(b"%PDF-1.4")
        with session_state.STATE_LOCK:
            ACTIVE_CASE_STATE["bundle"] = get_demo_case_bundle()
            ACTIVE_CASE_STATE["explicit_demo"] = False
            ACTIVE_CASE_STATE["explicit_reset"] = False

        def _autoload_proibido(**_kwargs):
            raise AssertionError("GET /api/cases/active não pode carregar/transcrever autos do disco.")

        monkeypatch.setattr(main_module, "_load_real_case_from_disk_if_present", _autoload_proibido)
        response = client.get("/api/cases/active")
        assert response.status_code == 200
        data = response.json()
        assert data["empty"] is False
        assert data["is_demo"] is True
        assert "MARIANA" in data["ofendida"].upper()
        assert data["arquivos_em_disco"] == {"pdfs": 1, "midias": 0}

    def test_load_from_disk_carrega_autos_explicitamente(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        case2 = load_case(CASE_FIXTURES[1])
        recebido: Dict[str, Any] = {}

        def _fake_load(consentimento_envio_externo: bool = False, status_saida=None):
            recebido["consentimento"] = consentimento_envio_externo
            if status_saida is not None:
                status_saida["pdfs_processo"] = [{"filename": "autos.pdf", "status": "PROCESSADO"}]
                status_saida["midias_audiencia"] = []
            return copy.deepcopy(case2)

        monkeypatch.setattr(main_module, "_load_real_case_from_disk_if_present", _fake_load)
        assert _generate(client, load_case(CASE_FIXTURES[0])).status_code == 200

        response = client.post("/api/cases/load-from-disk", json={"consentimento_envio_externo": False})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "loaded"
        assert data["numero_processo"] == case2["numero_processo"]
        assert recebido["consentimento"] is False
        assert ACTIVE_CASE_STATE["bundle"]["numero_processo"] == case2["numero_processo"]
        for key in PRODUCTION_KEYS:
            assert ACTIVE_CASE_STATE[key] is None
        assert data["upload_status"]["pdfs_processo"][0]["filename"] == "autos.pdf"

    def test_load_from_disk_sem_autos_404(self, client: TestClient):
        response = client.post("/api/cases/load-from-disk")
        assert response.status_code == 404
        assert ACTIVE_CASE_STATE["bundle"] is not None  # caso ativo preservado


# ==============================================================================
# Troca de caso: load-demo / upload-files limpam produção anterior
# ==============================================================================


class TestTrocaDeCasoLimpaProducao:
    def test_load_demo_limpa_producao_anterior(self, client: TestClient):
        assert _generate(client, load_case(CASE_FIXTURES[0])).status_code == 200
        assert ACTIVE_CASE_STATE["pipeline_engine"] is not None

        response = client.post("/api/cases/load-demo")
        assert response.status_code == 200
        data = response.json()
        assert data["empty"] is False
        assert data["is_demo"] is True
        assert "MARIANA" in data["ofendida"].upper()

        for key in PRODUCTION_KEYS:
            assert ACTIVE_CASE_STATE[key] is None, f"{key} deveria ser limpo no load-demo"
        assert ACTIVE_CASE_STATE["explicit_demo"] is True
        assert ACTIVE_CASE_STATE["explicit_reset"] is False

    def test_upload_files_limpa_producao_anterior(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        assert _generate(client, load_case(CASE_FIXTURES[0])).status_code == 200
        case3 = load_case(CASE_FIXTURES[2])
        monkeypatch.setattr(main_module, "segmentar_autos_pdf_pje", lambda _path: copy.deepcopy(case3["raw_documents"]))

        response = client.post(
            "/api/cases/upload-files",
            files=[("files", ("autos_caso3.pdf", _pdf_minimo_valido("ID-1234567 - Autos do caso 3"), "application/pdf"))],
            data={"depoente_padrao": "OFENDIDA / TESTEMUNHA"},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "uploaded"
        assert data["empty"] is False
        assert data["is_demo"] is False
        assert data["numero_processo"] == case3["numero_processo"]

        for key in PRODUCTION_KEYS:
            assert ACTIVE_CASE_STATE[key] is None, f"{key} deveria ser limpo no upload"
        assert ACTIVE_CASE_STATE["explicit_demo"] is False
        assert ACTIVE_CASE_STATE["explicit_reset"] is False
        assert ACTIVE_CASE_STATE["bundle"]["numero_processo"] == case3["numero_processo"]
        assert ACTIVE_CASE_STATE["bundle"]["is_demo"] is False

    def test_upload_invalido_preserva_producao_em_curso(self, client: TestClient, isolated_session: Dict[str, Path]):
        """Upload corrompido (422) NÃO pode apagar a sentença/engine em curso nem deixar resíduo."""
        assert _generate(client, load_case(CASE_FIXTURES[0])).status_code == 200
        sentenca_antes = ACTIVE_CASE_STATE["last_sentence"]
        auditoria_antes = ACTIVE_CASE_STATE["last_audit"]
        engine_antes = ACTIVE_CASE_STATE["pipeline_engine"]
        numero_antes = ACTIVE_CASE_STATE["bundle"]["numero_processo"]
        assert sentenca_antes is not None and engine_antes is not None

        response = client.post(
            "/api/cases/upload-files",
            files=[("files", ("autos_corrompidos.pdf", b"nao-e-pdf\x00", "application/pdf"))],
        )

        assert response.status_code == 422
        assert ACTIVE_CASE_STATE["last_sentence"] is sentenca_antes
        assert ACTIVE_CASE_STATE["last_audit"] is auditoria_antes
        assert ACTIVE_CASE_STATE["pipeline_engine"] is engine_antes
        assert ACTIVE_CASE_STATE["bundle"]["numero_processo"] == numero_antes
        assert not (isolated_session["cases_dir"] / "autos_corrompidos.pdf").exists()
        assert list((isolated_session["cases_dir"] / ".upload_tmp").glob("*")) == []

    def test_upload_files_apos_reset_completo_funciona(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        assert _reset(client, "completo").status_code == 200
        assert ACTIVE_CASE_STATE["bundle"] is None
        case2 = load_case(CASE_FIXTURES[1])
        monkeypatch.setattr(main_module, "segmentar_autos_pdf_pje", lambda _path: copy.deepcopy(case2["raw_documents"]))

        response = client.post(
            "/api/cases/upload-files",
            files=[("files", ("autos_novos.pdf", _pdf_minimo_valido("ID-7654321 - Autos novos"), "application/pdf"))],
        )
        assert response.status_code == 200, response.text
        assert response.json()["numero_processo"] == case2["numero_processo"]
        assert ACTIVE_CASE_STATE["bundle"]["numero_processo"] == case2["numero_processo"]
        assert client.get("/api/cases/active").json()["empty"] is False


# ==============================================================================
# Anti-poluição A → reset → B
# ==============================================================================


class TestAntiPoluicao:
    def test_caso_b_nao_contem_dados_do_caso_a(self, client: TestClient):
        case_a = load_case(CASE_FIXTURES[0])
        case_b = load_case(CASE_FIXTURES[1])

        gen_a = _generate(client, case_a)
        assert gen_a.status_code == 200, gen_a.text
        texto_a = gen_a.json()["sentenca"]["texto_completo"]
        assert case_a["numero_processo"] in texto_a
        assert case_a["ofendida"].upper() in texto_a.upper()

        assert _reset(client, "completo").status_code == 200
        assert client.get("/api/cases/active").json()["empty"] is True

        gen_b = _generate(client, case_b)
        assert gen_b.status_code == 200, gen_b.text
        texto_b = gen_b.json()["sentenca"]["texto_completo"]

        assert case_b["numero_processo"] in texto_b
        assert case_b["ofendida"].upper() in texto_b.upper()
        assert case_a["numero_processo"] not in texto_b
        assert case_a["ofendida"].upper() not in texto_b.upper()
        assert case_a["reu"].upper() not in texto_b.upper()

        active = client.get("/api/cases/active").json()
        assert active["empty"] is False
        assert active["numero_processo"] == case_b["numero_processo"]
        assert {p["doc_id"] for p in active["pecas"]} == {d["doc_id"] for d in case_b["raw_documents"]}


# ==============================================================================
# Status e guardas de estado vazio
# ==============================================================================


class TestStatusESessao:
    def test_health_minimo_e_status_com_bloco_session(self, client: TestClient):
        health = client.get("/api/health").json()
        assert health == {"status": "ok", "versao": "2.1.0"}

        response = client.get("/api/status")
        assert response.status_code == 200
        session = response.json()["session"]
        assert set(session) >= {"generation_id", "case_loaded", "last_reset", "is_demo"}
        assert session["generation_id"] == ACTIVE_CASE_STATE["generation_id"]
        assert session["case_loaded"] is True
        assert session["last_reset"] is None

        assert _reset(client, "completo").status_code == 200
        session_apos = client.get("/api/status").json()["session"]
        assert session_apos["case_loaded"] is False
        assert isinstance(session_apos["last_reset"], str)

    def test_endpoints_de_producao_retornam_409_com_sessao_vazia(self, client: TestClient):
        assert _reset(client, "completo").status_code == 200
        assert client.post("/api/dosimetria/simulate", json={}).status_code == 409
        assert client.post("/api/sentence/generate", json={"use_ollama": False}).status_code == 409
        assert client.get("/api/export/docx").status_code == 409
