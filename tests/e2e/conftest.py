"""Harness e Fixtures E2E para o SentencIA.

Fornece isolamento de estado, cliente TestClient da FastAPI, mocks determinísticos
para Ollama e Whisper, e helpers de asserção estrita.
"""

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Dict, Generator, List, Optional
from unittest.mock import MagicMock, patch
import urllib.request

from fastapi.testclient import TestClient
import pytest

from app.api.security import TOKEN_HEADER, get_session_token
from app.core.case_context import obter_contexto
from app.demo.demo_case import get_demo_case_bundle
from app.generation.hallucination_auditor import auditar_sentenca
from app.generation.sentence_generator import gerar_sentenca_estruturada
from app.main import ACTIVE_CASE_STATE, app

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


# ==============================================================================
# Controlador de Mock do Serviço de IA (Ollama / LLM)
# ==============================================================================


class AIServiceMockController:
  """Controlador determinístico para chamadas a serviços de IA (Ollama/HTTP)."""

  def __init__(self):
    self.fault_mode: Optional[str] = None
    self.transient: bool = False
    self.call_count: int = 0
    self.calls: List[Dict[str, Any]] = []

  def inject_fault(self, fault_type: Optional[str], transient: bool = False):
    """Injeta falha controlada: 'fake_id', 'omit_thesis', 'foreign_date', 'fake_sumula', 'error_500'.

    Se transient=True, a falha afeta apenas a 1ª chamada, recuperando na seguinte.
    """
    self.fault_mode = fault_type
    self.transient = transient

  def reset(self):
    self.fault_mode = None
    self.transient = False
    self.call_count = 0
    self.calls.clear()

  def handle_generate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    self.call_count += 1
    self.calls.append(payload)

    # Chamada de keep_alive / descarregamento de VRAM
    if payload.get("keep_alive") == 0:
      return {"done": True, "response": "Model unloaded from VRAM"}

    current_fault = self.fault_mode
    if self.transient and self.call_count > 1:
      current_fault = None  # Recupera após a 1ª tentativa para testar auto-correção

    # Simulação de injeção de falhas controladas
    if current_fault == "fake_id":
      return {
          "response": (
              "Conforme certificado na folha fictícia ID-9999999 e mídia"
              " ID-MIDIA-999, o réu praticou o delito narrado."
          ),
          "done": True,
      }
    elif current_fault == "omit_thesis":
      return {
          "response": (
              "Passo à análise do mérito, restando provada a autoria e"
              " materialidade sem necessidade de enfrentar as preliminares."
          ),
          "done": True,
      }
    elif current_fault == "foreign_date":
      return {
          "response": (
              "Os fatos foram cometidos em 2025-02-14 consoante depoimento."
          ),
          "done": True,
      }
    elif current_fault == "fake_sumula":
      return {
          "response": (
              "Aplica-se ao caso a orientação da Súmula 999 do STJ que autoriza"
              " o pleito."
          ),
          "done": True,
      }
    elif current_fault == "error_500":
      raise RuntimeError("Ollama service unavailable (CUDA out of memory)")

    # Resposta padrão determinística bem comportada
    return {
        "response": (
            "Com base nos elementos de convicção coligidos e respeitadas as"
            " diretrizes dos autos, a pretensão punitiva merece procedência nos"
            " exatos termos do laudo pericial e da palavra da vítima sob o"
            " Protocolo CNJ 492/2023."
        ),
        "done": True,
    }


# ==============================================================================
# Fixtures Pytest
# ==============================================================================


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
  return FIXTURES_DIR


@pytest.fixture
def load_fixture_case() -> Callable[[str], Dict[str, Any]]:
  """Carrega qualquer um dos 5 cenários judiciais por nome ou alias."""

  def _loader(case_name: str) -> Dict[str, Any]:
    name_str = str(case_name).lower()
    if "1" in name_str or "ameaca" in name_str:
      filename = "case_scenario_1_ameaca_lesao.json"
    elif "2" in name_str or "descumprimento" in name_str or "mpu" in name_str:
      filename = "case_scenario_2_descumprimento_mpu.json"
    elif "3" in name_str or "palavra" in name_str or "inconclusivo" in name_str:
      filename = "case_scenario_3_lesao_palavra_vitima.json"
    elif (
        "4" in name_str
        or "multirreincidente" in name_str
        or "444" in name_str
        or "sursis" in name_str
    ):
      filename = "case_scenario_4_multirreincidente_sumula444.json"
    elif (
        "5" in name_str or "autocorrecao" in name_str or "fallback" in name_str
    ):
      filename = "case_scenario_5_autocorrection_fallback.json"
    else:
      filename = case_name if case_name.endswith(".json") else f"{case_name}.json"

    p = FIXTURES_DIR / filename
    if not p.exists():
      raise FileNotFoundError(f"Fixture não encontrada: {p}")
    with open(p, encoding="utf-8") as f:
      return json.load(f)

  return _loader


@pytest.fixture
def load_mock_transcriptions() -> Dict[str, Any]:
  p = FIXTURES_DIR / "mock_transcriptions.json"
  with open(p, encoding="utf-8") as f:
    return json.load(f)


@pytest.fixture
def api_client() -> Generator[TestClient, None, None]:
  """TestClient da aplicação FastAPI enviando o token de sessão REAL (header X-JVDFM-Token)."""
  with TestClient(app, headers={TOKEN_HEADER: get_session_token()}) as client:
    yield client


@pytest.fixture
def api_client_sem_token() -> Generator[TestClient, None, None]:
  """TestClient sem token (para verificar a proteção das rotas /api/*)."""
  with TestClient(app) as client:
    yield client


_ESTADO_ISOLADO = (
    "last_dosimetria",
    "pipeline_engine",
    "pipeline_snapshot",
    "generation_id",
    "explicit_reset",
    "last_reset",
    "upload_status",
    "motores_cache",
)


@pytest.fixture(autouse=True)
def isolated_active_case() -> Generator[None, None, None]:
  """Garante isolamento estrito de ACTIVE_CASE_STATE antes e depois de cada teste."""
  saved_bundle = deepcopy(ACTIVE_CASE_STATE.get("bundle"))
  saved_sent = ACTIVE_CASE_STATE.get("last_sentence")
  saved_audit = ACTIVE_CASE_STATE.get("last_audit")
  saved_demo = ACTIVE_CASE_STATE.get("explicit_demo")
  saved_outros = {k: ACTIVE_CASE_STATE.get(k) for k in _ESTADO_ISOLADO}

  yield

  ACTIVE_CASE_STATE["bundle"] = (
      saved_bundle if saved_bundle else get_demo_case_bundle()
  )
  ACTIVE_CASE_STATE["last_sentence"] = saved_sent
  ACTIVE_CASE_STATE["last_audit"] = saved_audit
  ACTIVE_CASE_STATE["explicit_demo"] = (
      saved_demo if saved_demo is not None else True
  )
  for chave, valor in saved_outros.items():
    ACTIVE_CASE_STATE[chave] = valor


@pytest.fixture(autouse=True)
def isolate_cases_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
  """Isola data/cases e data/exports durante os testes (nenhum arquivo real é lido/gravado)."""
  sandbox_cases = tmp_path / "cases"
  sandbox_cases.mkdir(parents=True, exist_ok=True)
  sandbox_exports = tmp_path / "exports"
  sandbox_exports.mkdir(parents=True, exist_ok=True)
  monkeypatch.setattr("app.main.CASES_DIR", sandbox_cases)
  monkeypatch.setattr("app.core.config.CASES_DIR", sandbox_cases)
  monkeypatch.setattr("app.core.session_state.CASES_DIR", sandbox_cases)
  monkeypatch.setattr("app.main.EXPORTS_DIR", sandbox_exports)
  monkeypatch.setattr("app.core.config.EXPORTS_DIR", sandbox_exports)
  monkeypatch.setattr("app.core.session_state.EXPORTS_DIR", sandbox_exports)
  return sandbox_cases


# ==============================================================================
# Decisão do juízo (v2: a sentença de caso real exige veredicto por crime)
# ==============================================================================


def veredictos_condenatorios(bundle: Dict[str, Any]) -> Dict[str, Any]:
  """Override com o veredicto ``condenar`` para cada crime imputado do cenário.

  Na v2 o motor nunca condena por padrão: os cenários de fixture simulam a decisão
  explícita do magistrado (``crimes_config``).
  """
  crimes = list(obter_contexto(bundle).meta.crimes_imputados) or list(
      bundle.get("crimes_imputados") or []
  )
  assert crimes, "O cenário precisa ter crimes imputados."
  return {
      "crimes_config": [{"codigo": c, "veredicto": "condenar"} for c in crimes]
  }


def gerar_sentenca_caso_real(bundle: Dict[str, Any], **override_extra: Any):
  """Gera a sentença determinística de um cenário real com o veredicto do juízo."""
  override = veredictos_condenatorios(bundle)
  override.update(override_extra)
  return gerar_sentenca_estruturada(
      bundle, custom_dosimetria_override=override, use_ollama_if_available=False
  )


def auditar_com_dosimetria(sentenca: Any, bundle: Dict[str, Any]):
  """Auditoria completa (forma + conteúdo: penas do dispositivo x dosimetria calculada)."""
  ctx = obter_contexto(bundle)
  return auditar_sentenca(
      sentenca.texto_completo,
      ctx.pecas,
      ctx.meta.teses_defesa,
      list(ctx.ids_midias),
      dosimetria_dados=sentenca.dosimetria_dados,
      secoes_fallback=list(getattr(sentenca, "secoes_fallback", []) or []),
  )


@pytest.fixture
def mock_ai_services() -> Generator[AIServiceMockController, None, None]:
  """Fixture para interceptar e controlar respostas do Ollama/httpx de forma determinística."""
  controller = AIServiceMockController()

  def fake_post(url: str, *args, **kwargs):
    url_str = str(url)
    payload = kwargs.get("json", {})
    resp = MagicMock()
    if "/api/generate" in url_str:
      resp.status_code = 200
      try:
        data = controller.handle_generate(payload)
        resp.json.return_value = data
        resp.text = json.dumps(data)
      except RuntimeError as e:
        resp.status_code = 500
        resp.text = str(e)
    else:
      resp.status_code = 200
      resp.json.return_value = {"status": "ok"}
    return resp

  def fake_get(url: str, *args, **kwargs):
    url_str = str(url)
    resp = MagicMock()
    resp.status_code = 200
    if "/api/tags" in url_str:
      resp.json.return_value = {
          "models": [
              {"name": "qwen2.5:7b", "size": 4700000000},
              {"name": "translategemma:4b", "size": 3300000000},
          ]
      }
    else:
      resp.json.return_value = {"status": "ok"}
    return resp

  with (
      patch("httpx.Client.post", side_effect=fake_post),
      patch("httpx.Client.get", side_effect=fake_get),
  ):
    yield controller


@pytest.fixture
def mock_whisper_services(
    load_mock_transcriptions: Dict[str, Any],
) -> Generator[None, None, None]:
  """Mock que intercepta transcrever_midia_audiencia usando fixture prebaked."""

  def fake_transcribe(
      media_path,
      depoente="DEPOENTE",
      papel="TESTEMUNHA_ACUSACAO",
      known_ofendida="",
      known_reu="",
      **_kwargs,
  ):
    m_name = Path(media_path).name
    if m_name in load_mock_transcriptions:
      return deepcopy(load_mock_transcriptions[m_name])
    return {
        "media_id": f"ID-{m_name[:8].upper()}",
        "arquivo": m_name,
        "depoente": depoente,
        "papel": papel if papel != "ACUSADO" else "INTERROGATORIO_REU",
        "duracao": "04:30",
        "segmentos": [{
            "timestamp": "00:00 - 04:30",
            "texto_literal": (
                f"Transcrição mock para {m_name} em ambiente de teste."
            ),
        }],
        "engine": "MockAudioPipeline",
    }

  with patch(
      "app.transcription.audio_transcriber.transcrever_midia_audiencia",
      side_effect=fake_transcribe,
  ):
    yield


# ==============================================================================
# Funções de Asserção Estrita (Zero Tolerance)
# ==============================================================================


def assert_auditor_score_100(audit_data: Any) -> None:
  """Valida que o relatório de auditoria confere score 100.0 sem alucinações."""
  if isinstance(audit_data, dict):
    score = audit_data.get("score_confiabilidade", 0.0)
    aprovada = audit_data.get("aprovada_sem_alucinacao", False)
    ids_inexistentes = audit_data.get("ids_inexistentes", [])
    citacoes_invalidas = audit_data.get("citacoes_nao_autorizadas", [])
    teses_omitidas = audit_data.get("teses_omitidas", [])
  else:
    score = getattr(audit_data, "score_confiabilidade", 0.0)
    aprovada = getattr(audit_data, "aprovada_sem_alucinacao", False)
    ids_inexistentes = getattr(audit_data, "ids_inexistentes", [])
    citacoes_invalidas = getattr(audit_data, "citacoes_nao_autorizadas", [])
    teses_omitidas = getattr(audit_data, "teses_omitidas", [])

  assert (
      score == 100.0
  ), f"Score de confiabilidade deve ser 100.0, obtido: {score}"
  assert aprovada is True, "A sentença deve ser aprovada_sem_alucinacao"
  assert ids_inexistentes == [], f"IDs inexistentes detectados: {ids_inexistentes}"
  assert (
      citacoes_invalidas == []
  ), f"Citações não autorizadas detectadas: {citacoes_invalidas}"
  assert teses_omitidas == [], f"Teses defensivas omitidas: {teses_omitidas}"


def _campo(obj: Any, nome: str, padrao: Any = None) -> Any:
  return obj.get(nome, padrao) if isinstance(obj, dict) else getattr(obj, nome, padrao)


def assert_auditoria_honesta(audit_data: Any, sentenca: Any) -> None:
  """Auditoria de caso real na v2: forma 100.0, zero alucinação (IDs e base fechada), 100% das
  teses enfrentadas e penas do dispositivo conferidas com a dosimetria. As lacunas que só o juízo
  pode preencher aparecem como marcadores ``[COMPLETAR PELO JUÍZO: ...]`` e são as ÚNICAS que
  podem impedir a aprovação (score 100 ⇔ nenhum marcador pendente)."""
  marcadores = list(_campo(sentenca, "marcadores_pendentes", []) or [])
  assert _campo(audit_data, "score_forma") == 100.0, _campo(audit_data, "detalhes_camadas")
  assert _campo(audit_data, "ids_inexistentes") == []
  assert _campo(audit_data, "citacoes_nao_autorizadas") == []
  assert _campo(audit_data, "teses_omitidas") == []
  assert _campo(audit_data, "penas_divergentes") == []
  assert set(_campo(audit_data, "marcadores_pendentes", []) or []) == set(marcadores)
  assert _campo(audit_data, "aprovada_sem_alucinacao") is (not marcadores)
  if marcadores:
    assert _campo(audit_data, "score_confiabilidade") < 100.0
  else:
    assert _campo(audit_data, "score_confiabilidade") == 100.0


def assert_all_teses_answered(sentenca_texto: str, teses_defesa: List[Any]) -> None:
  """Verifica que 100% das teses defensivas foram substantivamente rebatidas (Art. 381, III, CPP).

  Critérios rígidos:
  1. Enfrentamento obrigatório na Fundamentação ou Dispositivo (menção isolada no Relatório é REJEITADA).
  2. Identificação por código formal (ex.: PREL-01, MER-01) ou termos jurídicos do título.
  3. Deliberação judicial substantiva (decisória ou contextual) dentro da janela da tese.
  4. Proibição estrita de evasivas forenses ("deixo de analisar", "não conheço da tese",
     "prejudicada a análise", "prejudicado o exame", ...). A evasiva reprova a asserção
     MESMO que termos genéricos de dispositivo ("pena", "condeno", "regime") apareçam na
     mesma janela de 1200 caracteres — termos genéricos jamais "curam" uma evasiva.
  """
  assert sentenca_texto and len(sentenca_texto.strip()) > 100, (
      "Texto da sentença vazio ou insuficiente."
  )

  # Separa Fundamentação e Dispositivo para impedir falsos positivos no mero Relatório
  if "II – FUNDAMENTAÇÃO" in sentenca_texto:
    texto_analise = sentenca_texto.split("II – FUNDAMENTAÇÃO", 1)[1]
  elif "FUNDAMENTAÇÃO" in sentenca_texto:
    texto_analise = sentenca_texto.split("FUNDAMENTAÇÃO", 1)[1]
  else:
    texto_analise = sentenca_texto

  texto_lower = texto_analise.lower()

  # Termos que expressam deliberação judicial efetiva sobre a tese (rejeitar, acolher,
  # afastar, absolver, operar-se a decadência etc.). Estes são o respaldo forte.
  termos_decisorios = [
      "afasto", "afasta-se", "afastado", "afastada", "rejeito", "rejeita-se",
      "rejeitada", "rejeitado", "desacolho", "desacolhe-se", "desacolhida",
      "acolho", "acolhe-se", "acolhida", "acolhido", "reconheço", "reconhece-se",
      "improcede", "improcedente", "improcedência", "procede", "procedente",
      "procedência", "não prospera", "não merece prosperar", "não se sustenta",
      "inviável", "operou-se", "declaro extinta", "declaro", "inaplicável",
      "não há que se falar", "restou superada", "carece de amparo", "insubsistente",
      "absolvo", "fixo", "mantenho", "julgo", "incabível", "inacolhível",
      "concedo", "deferido", "indeferido", "deferida", "indeferida",
      "desprovejo", "não acolho",
  ]
  # Termos meramente contextuais: isolados NÃO demonstram enfrentamento deliberativo.
  # Só são aceitos como respaldo residual quando a janela da tese está livre de evasiva.
  termos_contextuais = [
      "deixo de aplicar", "condeno", "pena", "regime", "laudo", "perícia",
      "corpo de delito", "materialidade", "autoria", "valoração probatória",
      "cotejo", "tipicidade", "relevo", "suficiência", "insuficiência",
      "in dubio pro reo", "exigindo", "sursis", "dano moral", "vedada", "vedado",
      "aplicação", "concessão", "indeferimento", "decadência", "exigência",
  ]
  evasivas_proibidas = [
      "deixo de analisar",
      "deixo de apreciar",
      "não analisada",
      "sem necessidade de enfrentar",
      "deixo de me manifestar",
      "sem apreciação",
      "não conheço da tese",
      "não conheço",
      "prejudicada a análise",
      "prejudicado o exame",
  ]

  for t in teses_defesa:
    codigo = (
        (t.get("codigo") or t.get("codigo_tese") or "")
        if isinstance(t, dict)
        else (getattr(t, "codigo", "") or getattr(t, "codigo_tese", ""))
    )
    titulo = (
        t.get("titulo", "")
        if isinstance(t, dict)
        else getattr(t, "titulo", "")
    )

    codigo_encontrado = bool(codigo and codigo.lower() in texto_lower)
    titulo_keywords = [
        k.lower()
        for k in [
            "decadência", "inépcia", "legítima defesa", "atipicidade",
            "ausência de dolo", "insuficiência probatória", "fragilidade probatória",
            "súmula 444", "mínimo legal", "confissão", "restituição",
            "habitualidade", "revelia", "corroboração periférica", "desclassificação",
            "nulidade", "laudo", "pericial", "telemática", "materialidade",
            "vias de fato", "autoria", "sursis", "dano moral", "conduta diversa",
            "pena abaixo",
        ]
        if k.lower() in titulo.lower()
    ]
    titulo_encontrado = any(k in texto_lower for k in titulo_keywords)

    assert codigo_encontrado or titulo_encontrado, (
        f"Tese {codigo} ('{titulo}') ausente na fundamentação da sentença! "
        f"Menção exclusiva no Relatório viola o Art. 381, III, do CPP."
    )

    # Tokens candidatos: o código formal (quando presente) e os termos do título que
    # efetivamente aparecem na fundamentação. Examinamos TODAS as ocorrências para
    # impedir que uma menção protocolar "limpa" encubra uma evasiva em outra menção.
    tokens_candidatos = [codigo.lower()] if codigo_encontrado else []
    tokens_candidatos.extend(
        k for k in titulo_keywords if k and k in texto_lower
    )
    if not tokens_candidatos:
      tokens_candidatos.append(
          codigo.lower() if codigo else (titulo.lower() if titulo else "")
      )

    janelas: List[str] = []
    for token in dict.fromkeys(t for t in tokens_candidatos if t):
      for match in re.finditer(re.escape(token), texto_lower):
        pos = match.start()
        # Janela do token da tese: 300 caracteres antes e 1200 depois.
        janelas.append(texto_lower[max(0, pos - 300) : pos + 1200])

    if not janelas:
      continue

    for contexto_janela in janelas:
      # (4) Evasiva forense => reprovação imediata. Termos genéricos de dispositivo
      # ("pena", "condeno", "regime") NÃO curam evasiva dentro da janela da tese.
      for evasiva in evasivas_proibidas:
        assert evasiva not in contexto_janela, (
            f"Evasiva judicial detectada para tese {codigo}: '{evasiva}'! "
            f"Omissão inconstitucional (Art. 381, III, CPP e Art. 93, IX, CF)."
        )

    # (3) Deliberação substantiva: verbos decisórios são o respaldo forte; na
    # ausência deles, aceita-se respaldo contextual apenas porque a janela já foi
    # blindada contra evasivas acima (termos genéricos jamais validam evasiva).
    tem_decisao = any(
        termo in janela for janela in janelas for termo in termos_decisorios
    )
    tem_contexto = any(
        termo in janela for janela in janelas for termo in termos_contextuais
    )
    assert tem_decisao or tem_contexto, (
        f"Tese {codigo} ('{titulo}') mencionada sem deliberação substantiva ou"
        " fundamentação decisória."
    )


# ==============================================================================
# Servidor E2E local reutilizável com teardown resiliente no Windows (TASK-025)
# ==============================================================================

_ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def _find_free_port() -> int:
  with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.bind(("127.0.0.1", 0))
    return int(s.getsockname()[1])


def _encerrar_arvore_processos(proc: subprocess.Popen[bytes]) -> None:
  """Encerra o subprocesso uvicorn e toda a árvore de filhos/netos, aguardando término."""
  try:
    proc.terminate()
  except OSError:
    pass
  try:
    proc.wait(timeout=5)
    return
  except subprocess.TimeoutExpired:
    pass

  if sys.platform == "win32" and getattr(proc, "pid", None):
    try:
      subprocess.run(
          ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
          stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL,
          check=False,
          timeout=10,
      )
    except Exception:
      pass

  try:
    proc.kill()
  except OSError:
    pass
  try:
    proc.wait(timeout=5)
  except subprocess.TimeoutExpired:
    pass


def _limpar_diretorio_temporario(
    tmp_dir: tempfile.TemporaryDirectory[str],
    *,
    tentativas: int = 5,
    intervalo_s: float = 0.2,
) -> None:
  """Remove a pasta temporária com retentativas curtas em ``PermissionError`` (lock transitório do ``app.log`` no Windows)."""
  caminho = Path(tmp_dir.name)
  for tentativa in range(max(1, tentativas)):
    if not caminho.exists():
      break
    try:
      shutil.rmtree(caminho)
      break
    except (PermissionError, OSError):
      if tentativa + 1 < tentativas:
        time.sleep(intervalo_s)
  try:
    tmp_dir.cleanup()
  except (PermissionError, OSError):
    pass


class _LocalAppServer:
  """Sobe ``app.main:app`` em porta livre com ``JVDFM_DATA_DIR`` isolado (nunca toca ``data/``)."""

  def __init__(self) -> None:
    self._tmp = tempfile.TemporaryDirectory(
        prefix="sentencia_layout_e2e_",
        ignore_cleanup_errors=True,
    )
    self.port = _find_free_port()
    self.base_url = f"http://127.0.0.1:{self.port}"
    self._proc: Optional[subprocess.Popen[bytes]] = None

  def __enter__(self) -> "_LocalAppServer":
    settings_e2e = Path(self._tmp.name) / "user_settings.json"
    if not settings_e2e.exists():
      settings_e2e.write_text(json.dumps({"assistente_concluido": True}), encoding="utf-8")
    env = os.environ.copy()
    env["JVDFM_DATA_DIR"] = self._tmp.name
    env["PYTHONUTF8"] = "1"
    popen_kwargs: Dict[str, Any] = {
        "cwd": str(_ROOT_DIR),
        "env": env,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
      popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    self._proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--log-level",
            "error",
        ],
        **popen_kwargs,
    )
    deadline = time.time() + 25.0
    while time.time() < deadline:
      try:
        with urllib.request.urlopen(f"{self.base_url}/api/health", timeout=1.0) as resp:
          if resp.status == 200:
            return self
      except Exception:
        time.sleep(0.2)
    self.__exit__(None, None, None)
    raise RuntimeError(f"Servidor local não respondeu em {self.base_url}/api/health")

  def __exit__(self, *args: Any) -> None:
    if self._proc is not None:
      _encerrar_arvore_processos(self._proc)
      self._proc = None
    _limpar_diretorio_temporario(self._tmp)


@pytest.fixture(scope="module")
def _live_server() -> Generator[_LocalAppServer, None, None]:
  """Servidor E2E compartilhado por módulo (usado por todos os testes Playwright)."""
  pytest.importorskip("playwright.sync_api")
  with _LocalAppServer() as srv:
    yield srv

