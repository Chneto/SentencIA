# Criado por FChNeto
"""API local do SentencIA (FastAPI).

Segurança (ver ``app/api/security.py``): servidor só em 127.0.0.1; token de sessão injetado no
``index.html`` e exigido em ``/api/*`` (header ``X-JVDFM-Token``), checagem de Host/Origin, CORS
restrito a loopback. ``GET /api/health`` é público e mínimo; o status detalhado fica em
``GET /api/status``.

Erros de domínio: ``DadosInsuficientesError``/``DecisaoJudicialPendenteError`` → 422
``{detail, erro, campos, crimes_pendentes}``; ``EnvioExternoNaoAutorizadoError`` → 403;
``GeminiIndisponivelError`` → 503.

Todas as rotas usam ``obter_contexto(bundle)`` (classificação/metadados/matriz oral calculados uma
vez por conteúdo) e reconhecem o caso demonstrativo apenas pela flag ``bundle["is_demo"]``.
"""

__author__ = "FChNeto"

import asyncio
import copy
import datetime
import hashlib
import importlib.util
import inspect
import json
import threading
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote
from typing import Any, Callable, Dict, List, Optional

import pymupdf
from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from app.api.erros import corpo_dados_insuficientes, descrever_erro, eh_erro_de_dominio, registrar_handlers
from app.api.upload_stream import ArquivoRecebido, receber_multipart
from app.api.jobs import CANCELAR_INGESTAO, INGESTAO_LOCK, JOBS, PROGRESSO
from app.api.media_filter import (
    MEDIA_EXTENSIONS,
    PDF_EXTENSIONS,
    _extract_aij_dates_from_pecas,  # noqa: F401 (usado pelos testes via app.main)
    _filter_media_files_for_case,
    descrever_extensoes_midia,
)
from app.api.models import (
    BuildGeminiScriptRequest,
    CustomPrecedenteRequest,
    DosimetriaSimulationRequest,
    GeminiSecretRequest,
    GenerateGeminiSentenceRequest,
    GenerateSectionalRequest,
    GenerateSentenceRequest,
    ImportExternalSentenceRequest,
    LimparDemoRequest,
    LoadFromDiskRequest,
    MetadataOverrideRequest,
    OllamaPullRequest,
    RegenerateSectionRequest,
    ResetCaseRequest,
    RetencaoArquivoMortoRequest,
    AbrirProcessoLoteRequest,
    ImportarGabineteRequest,
    StyleSampleRequest,
    SaveSentenceFormatRequest,
    UpdateAndAuditSentenceRequest,
    UpdatePrecedenteRequest,
)
from app.api.security import (
    TOKEN_HEADER,  # noqa: F401 (usado pelos testes via app.main)
    VERSAO_APP,
    configurar_logging,
    get_port,
    get_session_token,
    injetar_token_html,
    middleware_seguranca,
)
from app.core import secrets as cofre
from app.core.case_context import limpar_cache_contexto, normalizar_override_metadado, obter_contexto
from app.core.case_flags import is_demo_bundle, is_demo_or_test_bundle
from app.core.config import CASES_DIR, EXPORTS_DIR, UserSettings, load_settings, ollama_url_loopback, save_settings
from app.core.errors import DadosInsuficientesError
from app.core.limpeza_demo import limpar_artefatos_demo
from app.core.ollama_client import listar_modelos_ollama, ollama_disponivel
from app.core.platform_utils import get_gpu_status
from app.core.session_state import (
    ACTIVE_CASE_STATE,
    PIPELINE_LOCK,
    STATE_LOCK,
    clear_production_state,  # noqa: F401 - reexportado por compatibilidade
    listar_versoes,
    obter_estado,
    obter_versao,
    register_generation,
    reset_session_state,
    resolve_cases_dir,
    resolve_exports_dir,
    restaurar_ultimo_caso_real,
    session_health,
    set_active_bundle,
    update_regenerated_section,
)
from app.demo.demo_case import get_demo_case_bundle
from app.export.document_exporter import MARCA_DAGUA_NAO_AUDITADA, exportar_docx, exportar_html_pje, exportar_pdf
from app.export.comparador import comparar
from app.export.destino import abrir_pasta, copiar_exportacao, validar_pasta_exportacao
from app.generation.revisor_estilo import montar_revisao
from app.export.formatos import excluir_modelo, listar_modelos, salvar_modelo
from app.export.trechos import excluir_trecho, listar_trechos, salvar_trecho
from app.export.nomeacao import eh_arquivo_exportacao, nome_arquivo_sentenca
from app.export.rich_text import html_da_sentenca, normalizar_formatacao, sanitizar_html, texto_do_html
from app.generation.case_script_engine import executar_motores_py_separados, gerar_script_estruturado_caso
from app.generation.gemini_integration import gerar_sentenca_via_gemini_api, importar_e_auditar_sentenca_externa
from app.generation.hallucination_auditor import auditar_sentenca, dosimetria_para_auditoria
from app.generation.sectional_pipeline import SecaoTipo, SectionalPipelineEngine, default_in_flight_gating_validator
from app.generation.sentence_generator import (
    gerar_sentenca_estruturada,
    resolver_data,
    resolver_dosimetria_e_decisoes,
)
from app.ingestion.laya_classifier import MetadadosProcesso
from app.ingestion.pdf_ingestor import motor_ocr_disponivel, segmentar_autos_pdf_pje
from app.knowledge.knowledge_base import (
    PrecedenteDuplicadoError,
    PrecedenteNaoEncontradoError,
    PrecedenteOficialReadOnlyError,
    add_custom_precedente,
    delete_custom_precedente,
    exportar_precedentes_gabinete,
    get_all_knowledge_base,
    importar_precedentes_gabinete,
    update_custom_precedente,
)
from app.knowledge.style_cloner import get_style_profile, register_cabinet_sentence_sample
from app.legal_engine.dosimetria_calculator import PenaAnosMesesDias
from app.legal_engine.penal_types_catalog import list_all_penal_types
from app.transcription.audio_transcriber import (
    descarregar_modelo_whisper,
    rotulo_log_arquivo as _rotulo_log,
    transcrever_midia_audiencia,
)

# M3 (Duplo Crivo) pode estar ausente: fallback para o validador determinístico padrão.
try:
    from app.generation.duplo_crivo import criar_gating_validator_duplo_crivo
except Exception:  # pragma: no cover
    criar_gating_validator_duplo_crivo = None  # type: ignore[assignment]

logger = logging.getLogger("app.main")

__all__ = ["app", "ACTIVE_CASE_STATE", "run"]

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

VALID_SECTION_NAMES = ("relatorio", "preliminares", "merito", "dosimetria", "dispositivo")
NOME_SEGREDO_GEMINI = "gemini_api_key"
_BOOT_CASES_DIR = Path(CASES_DIR).resolve()
_BOOT_EXPORTS_DIR = Path(EXPORTS_DIR).resolve()
_UPLOAD_TMP_DIRNAME = ".upload_tmp"
_CHUNK_UPLOAD = 1024 * 1024


def _limpar_temporarios_upload() -> int:
    """Remove ``data/cases/.upload_tmp/*.part`` deixados por uploads interrompidos (ex.: queda de energia)."""
    tmp_dir = _cases_dir() / _UPLOAD_TMP_DIRNAME
    removidos = 0
    if not tmp_dir.is_dir():
        return 0
    for parcial in tmp_dir.glob("*.part"):
        if INGESTAO_LOCK.locked():  # nunca remove o temporário de um upload em andamento
            break
        try:
            parcial.unlink()
            removidos += 1
        except OSError:
            pass
    return removidos


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    destino = configurar_logging()
    removidos = _limpar_temporarios_upload()
    restaurou = restaurar_ultimo_caso_real()
    logger.info(
        "SentencIA %s iniciado (porta %s, log: %s, temporários removidos: %d, último caso real restaurado: %s).",
        VERSAO_APP,
        get_port(),
        destino or "console",
        removidos,
        "sim" if restaurou else "não (abertura limpa)",
    )
    yield
    logger.info("SentencIA encerrado.")


app = FastAPI(
    title="SentencIA — Sistema de Prolatação de Sentenças Penais (Lei 11.340/2006)",
    version=VERSAO_APP,
    lifespan=_lifespan,
)

# Ordem: o último middleware adicionado é o mais externo → CORS responde aos preflights e a
# segurança (Host/Origin/token) envolve todas as rotas.
app.middleware("http")(middleware_seguranca)
# Sem CORSMiddleware: o frontend e same-origin (outra porta em loopback nao le o token).
registrar_handlers(app)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ==============================================================================
# Utilitários
# ==============================================================================


def _cases_dir() -> Path:
    """``CASES_DIR`` corrente (tolerante a monkeypatch em ``app.main`` ou em ``app.core.config``)."""
    atual = globals().get("CASES_DIR")
    if atual is not None and Path(atual).resolve() != _BOOT_CASES_DIR:
        return Path(atual)
    return resolve_cases_dir()


def _exports_dir() -> Path:
    atual = globals().get("EXPORTS_DIR")
    if atual is not None and Path(atual).resolve() != _BOOT_EXPORTS_DIR:
        return Path(atual)
    return resolve_exports_dir()


def _chamar_compat(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Chama ``func`` passando apenas os kwargs que ela aceita (tolera mocks com assinatura antiga)."""
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return func(*args, **kwargs)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return func(*args, **kwargs)
    return func(*args, **{k: v for k, v in kwargs.items() if k in params})


def _fmt_file_ts(dt: Optional[datetime.datetime] = None) -> str:
    return (dt or datetime.datetime.now()).strftime("%d/%m/%Y %H:%M:%S")


def _sha256_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as fh:
        for parte in iter(lambda: fh.read(_CHUNK_UPLOAD), b""):
            h.update(parte)
    return h.hexdigest()


def _empty_metadados() -> Dict[str, Any]:
    """Metadados processuais vazios para o estado sem caso ativo."""
    return {
        "numero_processo": "",
        "autos_mpu_apenso": "",
        "nome_ofendida": "",
        "nome_reu": "",
        "vinculo_domestico": "",
        "data_fato": "",
        "data_recebimento_denuncia": "",
        "data_intimacao_mpu": "",
        "intimidado_pessoalmente_mpu": False,
        "reu_primario_sumula444": False,
        "pedido_dano_moral_expresso_mp": False,
        "valor_dano_moral_pedido_mp": 0.0,
        "crimes_imputados": [],
        "teses_defesa": [],
        "alertas_extracao": [],
    }


def _arquivos_em_disco() -> Dict[str, int]:
    """Contagem barata (sem processar) dos autos/mídias na raiz de ``data/cases``."""
    cases_dir = _cases_dir()
    pdfs = midias = 0
    try:
        for p in cases_dir.iterdir():
            if not p.is_file() or eh_arquivo_exportacao(p.name):
                continue
            ext = p.suffix.lower()
            if ext in PDF_EXTENSIONS:
                pdfs += 1
            elif ext in MEDIA_EXTENSIONS:
                midias += 1
    except OSError:
        pass
    return {"pdfs": pdfs, "midias": midias}


def _upload_status_atual() -> Dict[str, Any]:
    with STATE_LOCK:
        return copy.deepcopy(ACTIVE_CASE_STATE.get("upload_status") or {"pdfs_processo": [], "midias_audiencia": []})


def _empty_case_payload() -> Dict[str, Any]:
    """Payload canônico de sessão vazia (nenhum caso ativo)."""
    return {
        "empty": True,
        "is_demo": False,
        "case_id": None,
        "numero_processo": "",
        "autos_mpu_apenso": "",
        "ofendida": "",
        "reu": "",
        "vinculo_domestico": "",
        "data_fato": "",
        "data_recebimento_denuncia": "",
        "pecas": [],
        "metadados": _empty_metadados(),
        "metadados_override": {},
        "alertas_extracao": [],
        "matriz_oral": {"depoentes": [], "fundamentacao_sintese": "", "midias_sem_transcricao": []},
        "transcricoes": [],
        "upload_status": _upload_status_atual(),
        "arquivos_em_disco": _arquivos_em_disco(),
    }


def _resumo_transcricoes(bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    saida = []
    for t in bundle.get("transcriptions") or []:
        if not isinstance(t, dict):
            continue
        saida.append(
            {
                "media_id": t.get("media_id", ""),
                "arquivo": t.get("arquivo", ""),
                "depoente": t.get("depoente", ""),
                "papel": t.get("papel", ""),
                "duracao": t.get("duracao", ""),
                "status": t.get("status") or ("OK" if t.get("segmentos") else "FALHOU"),
                "erro": t.get("erro") or "",
                "engine": t.get("engine", ""),
                "segmentos": len(t.get("segmentos") or []),
            }
        )
    return saida


def _serialize_active_bundle(bundle: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Serializa os autos ativos (via CaseContext) ou devolve o payload vazio."""
    if not bundle or not isinstance(bundle, dict) or not bundle.get("raw_documents"):
        return _empty_case_payload()
    ctx = obter_contexto(bundle)
    meta = ctx.meta
    return {
        "empty": False,
        "is_demo": ctx.is_demo,
        "case_id": bundle.get("case_id") or f"REAL-{meta.numero_processo or 'SEM-NUMERO'}",
        "numero_processo": meta.numero_processo or bundle.get("numero_processo") or "",
        "autos_mpu_apenso": meta.autos_mpu_apenso or bundle.get("autos_mpu_apenso") or "",
        "ofendida": meta.nome_ofendida or bundle.get("ofendida") or "",
        "reu": meta.nome_reu or bundle.get("reu") or "",
        "vinculo_domestico": meta.vinculo_domestico or bundle.get("vinculo_domestico") or "",
        "data_fato": meta.data_fato or bundle.get("data_fato") or "",
        "data_recebimento_denuncia": meta.data_recebimento_denuncia or bundle.get("data_recebimento_denuncia") or "",
        "pecas": [p.model_dump() for p in ctx.pecas],
        "metadados": meta.model_dump(),
        "metadados_override": dict(bundle.get("metadados_override") or {}),
        "alertas_extracao": list(getattr(meta, "alertas_extracao", []) or []),
        "matriz_oral": ctx.matriz_oral.model_dump(),
        "transcricoes": _resumo_transcricoes(bundle),
        "upload_status": _upload_status_atual(),
        "arquivos_em_disco": _arquivos_em_disco(),
    }


def _require_active_bundle() -> Dict[str, Any]:
    """Devolve o bundle ativo ou aborta com 409 quando a sessão está vazia."""
    with STATE_LOCK:
        bundle = ACTIVE_CASE_STATE.get("bundle")
    if not bundle:
        raise HTTPException(
            status_code=409,
            detail=(
                "Nenhum caso ativo na sessão. Carregue os autos reais ou o caso "
                "demonstrativo antes de prosseguir."
            ),
        )
    return bundle


def _auditar(sentenca: Any, ctx: Any) -> Any:
    """Auditoria completa: IDs (peças + mídias do contexto), base fechada, teses, forma e conteúdo
    (penas do dispositivo x dosimetria calculada; marcadores pendentes; seções de fallback)."""
    dosimetria = sentenca.dosimetria_dados if isinstance(getattr(sentenca, "dosimetria_dados", None), dict) else None
    if dosimetria is not None:
        dosimetria = dict(dosimetria)
        dosimetria["simulacao_presume_condenacao"] = bool(dosimetria.get("simulacao_presume_condenacao")) or bool(
            getattr(sentenca, "simulacao_presume_condenacao", False)
        )
        # Caso real com presuncao de condenacao: penas PENDENTES (nao aprova nem exporta sem marca d'agua).
        dosimetria = dosimetria_para_auditoria(dosimetria, bool(getattr(ctx, "is_demo", False)))
    return auditar_sentenca(
        texto_sentenca=sentenca.texto_completo,
        pecas_autos=ctx.pecas,
        teses_defesa=ctx.meta.teses_defesa,
        ids_midias=list(ctx.ids_midias),
        dosimetria_dados=dosimetria,
        secoes_fallback=list(getattr(sentenca, "secoes_fallback", []) or []),
    )


def _auditoria_aprovada(auditoria: Any) -> bool:
    return bool(getattr(auditoria, "aprovada_sem_alucinacao", False))


def _payload_geracao(sentenca: Any, auditoria: Any, generation_id: int, **extras: Any) -> Dict[str, Any]:
    """Resposta comum das rotas de geração (sentença, laudo, HTML p/ PJe e sinalizações honestas)."""
    aprovada = _auditoria_aprovada(auditoria)
    dados_sentenca = sentenca.model_dump(mode="json")
    # Folha do editor: HTML forense limpo (editado pelo usuário ou padrão aplicado ao texto do motor).
    dados_sentenca["texto_html"] = html_da_sentenca(sentenca)
    dados_sentenca["formatacao"] = normalizar_formatacao(getattr(sentenca, "formatacao", None))
    return {
        "sentenca": dados_sentenca,
        "auditoria": auditoria.model_dump(mode="json"),
        "html_pje": exportar_html_pje(sentenca, marca_dagua=None if aprovada else MARCA_DAGUA_NAO_AUDITADA),
        "generation_id": generation_id,
        "alertas": list(getattr(sentenca, "alertas", []) or []),
        "marcadores_pendentes": list(getattr(sentenca, "marcadores_pendentes", []) or []),
        "secoes_fallback": list(getattr(sentenca, "secoes_fallback", []) or []),
        "simulacao_presume_condenacao": bool(getattr(sentenca, "simulacao_presume_condenacao", False)),
        "exportacao_liberada": aprovada,
        **extras,
    }


def _registrar_se_caso_ativo(bundle: Dict[str, Any], **kwargs: Any) -> int:
    """Registra a geração somente se o caso ativo não mudou durante o processamento."""
    with STATE_LOCK:
        if ACTIVE_CASE_STATE.get("bundle") is not bundle:
            raise HTTPException(
                status_code=409,
                detail="O caso ativo mudou durante a geração (novo upload, reset ou troca de caso). Gere novamente.",
            )
        return register_generation(**kwargs)


# ==============================================================================
# Página, saúde, status e configurações
# ==============================================================================


@app.get("/", response_class=HTMLResponse)
def serve_index() -> HTMLResponse:
    index_file = STATIC_DIR / "index.html"
    html = index_file.read_text(encoding="utf-8") if index_file.exists() else "<html><head></head><body><h1>SentencIA</h1></body></html>"
    return HTMLResponse(injetar_token_html(html, get_session_token()))


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    """Ícone da aba para navegadores que pedem ``/favicon.ico`` direto (os demais usam o ``<link>``)."""
    return FileResponse(STATIC_DIR / "assets" / "app_icon.ico", media_type="image/x-icon")


@app.get("/api/health")
def api_health() -> Dict[str, Any]:
    """Status mínimo e público (usado pelo inicializador): sem dados do processo nem configurações."""
    return {"status": "ok", "versao": VERSAO_APP}


@app.get("/api/status")
def api_status() -> Dict[str, Any]:
    """Status detalhado e MEDIDO (exige token). Valores não medidos vêm ``None``/"indisponível"."""
    settings = load_settings()
    try:
        gemini_configurada = cofre.has_secret(NOME_SEGREDO_GEMINI)
        backend = cofre.backend_ativo()
    except Exception as err:  # pragma: no cover - cofre indisponível
        logger.warning("Cofre de segredos indisponível: %s", type(err).__name__)
        gemini_configurada, backend = False, "indisponivel"
    hw = get_gpu_status()
    from app.core.vram_manager import obter_politica_vram

    return {
        "status": "ok",
        "versao": VERSAO_APP,
        "hardware": hw,
        "politica_vram": obter_politica_vram(settings=settings, gpu_status=hw),
        "ollama": ollama_disponivel(),
        "laya_system1": {
            "status": "disponivel" if settings.laya_enabled else "desativado",
            "tipo": "classificador local por regras (Python), sem modelo neural",
            "metricas": None,
        },
        "ocr": {"motor": motor_ocr_disponivel()},
        "transcricao": {
            "whisper_local_instalado": importlib.util.find_spec("faster_whisper") is not None,
            "whisper_model": settings.whisper_model,
            "whisper_device": settings.whisper_device,
        },
        "privacidade": {
            "permitir_envio_externo": bool(settings.permitir_envio_externo),
            "gemini_configurada": gemini_configurada,
            "backend_segredos": backend,
        },
        "ingestao": {"em_andamento": INGESTAO_LOCK.locked()},
        "session": session_health(),
    }


@app.get("/api/ollama/modelos")
def api_ollama_modelos() -> Dict[str, Any]:
    """Lista modelos instalados no Ollama local (/api/tags) e o modelo recomendado pela política de VRAM."""
    return listar_modelos_ollama()


@app.get("/api/settings")
def api_get_settings() -> Dict[str, Any]:
    return load_settings().model_dump()


@app.post("/api/settings")
def api_update_settings(novos: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Atualização PARCIAL das preferências (campos ausentes mantêm o valor atual).

    Uma ``gemini_api_key`` enviada por clientes antigos vai para o cofre, nunca para o JSON.
    """
    if not isinstance(novos, dict):
        raise HTTPException(status_code=422, detail="Envie um objeto JSON com as configurações.")
    dados = dict(novos)
    chave_legada = dados.pop("gemini_api_key", None)
    atual = load_settings().model_dump()
    desconhecidos = sorted(k for k in dados if k not in UserSettings.model_fields)
    try:
        novo = UserSettings(**{**atual, **{k: v for k, v in dados.items() if k in UserSettings.model_fields}})
    except ValidationError as exc:
        campos = sorted({str(e["loc"][0]) for e in exc.errors() if e.get("loc")})
        return JSONResponse(
            status_code=422,
            content={"detail": "Configuração inválida: " + ", ".join(campos), "erro": "configuracao_invalida", "campos": campos},
        )
    if (("ollama_url" in dados) or ("permitir_envio_externo" in dados)) and not ollama_url_loopback(novo.ollama_url) and not novo.permitir_envio_externo:
        return JSONResponse(
            status_code=422,
            content={
                "detail": "ollama_url fora do loopback envia os autos para outra maquina: habilite "
                "permitir_envio_externo ou use http://127.0.0.1:11434.",
                "erro": "ollama_url_externa_nao_permitida",
                "campos": ["ollama_url"],
            },
        )
    avisos: List[str] = []
    if "pasta_exportacao" in dados:
        if (novo.pasta_exportacao or "").strip():
            from app.core import config as _cfg

            try:
                pasta, avisos = validar_pasta_exportacao(
                    novo.pasta_exportacao, bloqueadas=[_cfg.DATA_DIR, Path(__file__).resolve().parent.parent]
                )
            except ValueError as exc:
                return JSONResponse(
                    status_code=422,
                    content={"detail": f"Pasta de exportação: {exc}", "erro": "pasta_exportacao_invalida", "campos": ["pasta_exportacao"]},
                )
            novo = novo.model_copy(update={"pasta_exportacao": str(pasta)})
        else:
            novo = novo.model_copy(update={"pasta_exportacao": ""})
    saved = save_settings(novo)
    if isinstance(chave_legada, str) and chave_legada.strip():
        cofre.set_secret(NOME_SEGREDO_GEMINI, chave_legada.strip())
    return {"status": "saved", "settings": saved.model_dump(), "campos_ignorados": desconhecidos, "avisos": avisos}


@app.get("/api/secrets/gemini")
def api_get_gemini_secret() -> Dict[str, Any]:
    """Nunca devolve a chave: só se está configurada e em qual cofre."""
    return {"configurada": cofre.has_secret(NOME_SEGREDO_GEMINI), "backend": cofre.backend_ativo()}


@app.post("/api/secrets/gemini")
def api_set_gemini_secret(req: GeminiSecretRequest) -> Dict[str, Any]:
    chave = (req.api_key or "").strip()
    if not chave:
        raise HTTPException(status_code=422, detail="Informe a chave da API Gemini (use DELETE para removê-la).")
    cofre.set_secret(NOME_SEGREDO_GEMINI, chave)
    return {"configurada": True, "backend": cofre.backend_ativo()}


@app.delete("/api/secrets/gemini")
def api_delete_gemini_secret() -> Dict[str, Any]:
    cofre.delete_secret(NOME_SEGREDO_GEMINI)
    return {"configurada": cofre.has_secret(NOME_SEGREDO_GEMINI), "backend": cofre.backend_ativo()}


# ==============================================================================
# Ingestão: PDF, mídias, carga do disco
# ==============================================================================


def _validar_pdf_arquivo(filename: str, caminho: Path) -> None:
    """Valida o PDF recebido (em arquivo temporário) ANTES de colocá-lo em ``data/cases``.

    A exceção do PyMuPDF não é encadeada: o traceback manteria referências ao arquivo aberto e,
    no Windows, impediria a remoção do temporário rejeitado.
    """
    erro: Optional[str] = None
    paginas = 0
    try:
        doc = pymupdf.open(str(caminho))
        try:
            paginas = doc.page_count
        finally:
            doc.close()
    except Exception as exc:  # FileDataError, RuntimeError, etc.
        erro = type(exc).__name__
    if erro is not None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"O arquivo '{filename}' não é um PDF válido ou está corrompido "
                f"({erro}). Envie o PDF integral dos autos do PJe."
            ),
        )
    if paginas <= 0:
        raise HTTPException(status_code=422, detail=f"O arquivo PDF '{filename}' não possui páginas legíveis.")


def _validar_pdf_upload(filename: str, content_bytes: bytes) -> None:
    """Compatibilidade: valida PDF em memória (vazio/corrompido/sem páginas → 422)."""
    if len(content_bytes) == 0:
        raise HTTPException(
            status_code=422,
            detail=f"O arquivo PDF '{filename}' está vazio (0 bytes). Envie o PDF integral dos autos do PJe.",
        )
    try:
        doc = pymupdf.open(stream=content_bytes, filetype="pdf")
        try:
            paginas = doc.page_count
        finally:
            doc.close()
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"O arquivo '{filename}' não é um PDF válido ou está corrompido "
                f"({type(exc).__name__}). Envie o PDF integral dos autos do PJe."
            ),
        ) from exc
    if paginas <= 0:
        raise HTTPException(status_code=422, detail=f"O arquivo PDF '{filename}' não possui páginas legíveis.")


def _segmentar_pdf_seguro(
    pdf_path: Path,
    remover_em_falha: bool = False,
    ao_progresso: Optional[Callable[[str, int, int], None]] = None,
    *,
    usar_cache: bool = True,
    cancelar: Optional[threading.Event] = None,
) -> List[Dict[str, Any]]:
    """Segmenta um PDF convertendo falhas inesperadas em 422 (nunca 500).

    ``remover_em_falha`` só deve ser usado para arquivos recém-colocados pelo próprio upload — um
    arquivo que já existia em ``data/cases`` nunca é apagado.
    """
    erro: Optional[str] = None
    try:
        kwargs: Dict[str, Any] = {}
        if ao_progresso is not None:
            kwargs["ao_progresso"] = ao_progresso
        if not usar_cache:
            kwargs["usar_cache"] = False
        if cancelar is not None:
            kwargs["cancelar"] = cancelar
        if kwargs:
            return _chamar_compat(segmentar_autos_pdf_pje, pdf_path, **kwargs)
        return segmentar_autos_pdf_pje(pdf_path)
    except HTTPException:
        raise
    except Exception as exc:
        # Sem encadear: o traceback do PyMuPDF prenderia o arquivo aberto no Windows.
        erro = f"{type(exc).__name__}: {exc}"
        logger.warning("Falha ao segmentar PDF [%s]: %s", _rotulo_log(pdf_path.name), type(exc).__name__)
    if remover_em_falha:
        try:
            pdf_path.unlink(missing_ok=True)
        except OSError:
            pass
    raise HTTPException(
        status_code=422,
        detail=f"Não foi possível segmentar o PDF '{pdf_path.name}' ({erro}). Verifique a integridade do arquivo.",
    )


def _liberar_whisper() -> None:
    """Descarrega o modelo Whisper (VRAM) ao fim de um lote de transcrição; nunca propaga erro."""
    try:
        descarregar_modelo_whisper()
    except Exception:
        logger.debug("Falha ao descarregar o modelo Whisper.", exc_info=True)


def _transcrever(
    m_path: Path,
    *,
    depoente: str,
    papel: str,
    known_ofendida: str,
    known_reu: str,
    consentimento_envio_externo: bool,
) -> Dict[str, Any]:
    return _chamar_compat(
        transcrever_midia_audiencia,
        m_path,
        depoente=depoente,
        papel=papel,
        known_ofendida=known_ofendida,
        known_reu=known_reu,
        consentimento_envio_externo=consentimento_envio_externo,
    )


def _status_pdf(pdf_path: Path, docs_pdf: List[Dict[str, Any]], uploaded_at: str) -> Dict[str, Any]:
    ocr_indisponivel = sum(
        int(d.get("ocr_indisponivel") or (1 if d.get("ocr_pendente") else 0))
        for d in docs_pdf
        if isinstance(d, dict)
    )
    ocr_pendente = sum(1 for d in docs_pdf if isinstance(d, dict) and d.get("ocr_pendente"))
    ocr_falhou = sum(int(d.get("ocr_falhou") or 0) for d in docs_pdf if isinstance(d, dict) and d.get("ocr_falhou"))
    ocr_paginas_falha: List[int] = sorted(
        {
            int(p)
            for d in docs_pdf
            if isinstance(d, dict)
            for p in (d.get("ocr_paginas_falha") or [])
        }
    )
    ocr_reconhecidas = sum(
        int(d.get("ocr_paginas_reconhecidas") or (1 if d.get("ocr_aplicado") else 0))
        for d in docs_pdf
        if isinstance(d, dict)
    )
    if ocr_indisponivel > 0:
        ocr_estado = "OCR_INDISPONIVEL"
        detalhes = (
            f"{len(docs_pdf)} peça(s) extraída(s); "
            f"Motor de OCR não instalado. {ocr_indisponivel} página(s) digitalizada(s) ficaram sem texto. "
            "Feche o programa e abra pelo 'Iniciar SentencIA'."
        )
    elif ocr_falhou > 0:
        ocr_estado = "OCR_FALHOU"
        suf_pags = (
            f" (página(s) {', '.join(str(p) for p in ocr_paginas_falha)})" if ocr_paginas_falha else ""
        )
        detalhes = f"{len(docs_pdf)} peça(s) extraída(s); {ocr_falhou} página(s) com falha no OCR{suf_pags}"
    elif ocr_reconhecidas > 0:
        ocr_estado = "OCR_CONCLUIDO"
        detalhes = f"{len(docs_pdf)} peça(s) extraída(s); OCR concluído em {ocr_reconhecidas} página(s)"
    else:
        ocr_estado = "PROCESSADO"
        detalhes = f"{len(docs_pdf)} peça(s) PJe extraída(s) e classificada(s)"
    return {
        "filename": pdf_path.name,
        "tipo": "PDF_PROCESSO",
        "tamanho_kb": round(pdf_path.stat().st_size / 1024, 1),
        "status": "PROCESSADO",
        "ocr_estado": ocr_estado,
        "uploaded_at": uploaded_at,
        "processed_at": _fmt_file_ts(),
        "detalhes": detalhes,
        "ocr_pendente": ocr_pendente,
        "ocr_indisponivel": ocr_indisponivel,
        "ocr_paginas_reconhecidas": ocr_reconhecidas,
        "ocr_falhou": ocr_falhou,
        "ocr_paginas_falha": ocr_paginas_falha,
    }


def _status_midia(m_path: Path, tr: Dict[str, Any], uploaded_at: str) -> Dict[str, Any]:
    falhou = str(tr.get("status") or "").upper() == "FALHOU" or not tr.get("segmentos")
    n_seg = len(tr.get("segmentos") or [])
    if falhou:
        detalhes = (
            f"{tr.get('depoente', m_path.name)} [{tr.get('papel', 'ORAL')}] — transcrição FALHOU: "
            f"{tr.get('erro') or 'nenhum motor de transcrição disponível'}"
        )
    else:
        detalhes = f"{tr.get('depoente', m_path.name)} [{tr.get('papel', 'ORAL')}] — {n_seg} segmento(s) ({tr.get('engine', '')})"
    return {
        "filename": m_path.name,
        "tipo": "MIDIA_AUDIENCIA",
        "tamanho_kb": round(m_path.stat().st_size / 1024, 1),
        "status": "FALHOU" if falhou else "PROCESSADO",
        "uploaded_at": uploaded_at,
        "processed_at": _fmt_file_ts(),
        "detalhes": detalhes,
        "media_id": tr.get("media_id", ""),
        "engine": tr.get("engine", ""),
        "erro": tr.get("erro") or "",
        "segmentos": n_seg,
    }


def _construir_bundle_real(
    novos_docs: List[Dict[str, Any]],
    transcricoes: List[Dict[str, Any]],
    metadados_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Bundle de caso real a partir das peças/transcrições (metadados via CaseContext, sem inventar)."""
    base: Dict[str, Any] = {"raw_documents": novos_docs, "transcriptions": transcricoes, "is_demo": False}
    if metadados_override:
        base["metadados_override"] = dict(metadados_override)
    meta = obter_contexto(base).meta if novos_docs else MetadadosProcesso.model_validate(_empty_metadados())
    settings = load_settings()
    return {
        "case_id": f"REAL-{meta.numero_processo or 'SEM-NUMERO'}",
        "is_demo": False,
        "numero_processo": meta.numero_processo,
        "autos_mpu_apenso": meta.autos_mpu_apenso,
        "comarca": settings.comarca_nome,
        "juizado": settings.juizado_nome,
        "ofendida": meta.nome_ofendida,
        "reu": meta.nome_reu,
        "vinculo_domestico": meta.vinculo_domestico,
        "data_fato": meta.data_fato,
        "data_recebimento_denuncia": meta.data_recebimento_denuncia,
        "raw_documents": novos_docs,
        "transcriptions": transcricoes,
        **({"metadados_override": dict(metadados_override)} if metadados_override else {}),
    }


def _nomes_conhecidos(novos_docs: List[Dict[str, Any]]) -> tuple[List[Any], str, str, str]:
    if not novos_docs:
        return [], "", "", ""
    ctx = obter_contexto({"raw_documents": novos_docs, "transcriptions": [], "is_demo": False})
    return list(ctx.pecas), ctx.meta.nome_ofendida or "", ctx.meta.nome_reu or "", ctx.meta.numero_processo or ""


def _pdfs_na_raiz() -> List[Path]:
    cases_dir = _cases_dir()
    return sorted(
        p
        for p in cases_dir.glob("*.pdf")
        if p.is_file() and not eh_arquivo_exportacao(p.name) and not p.name.startswith(".")
    )


def _load_real_case_from_disk_if_present(
    consentimento_envio_externo: bool = False,
    status_saida: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any] | None:
    """Carrega os autos presentes na raiz de ``data/cases`` (varredura NÃO recursiva).

    Com vários PDFs, usa o modificado mais recentemente e vincula dinamicamente as mídias do caso.
    ``status_saida`` (opcional) recebe ``{pdfs_processo, midias_audiencia}``.
    """
    cases_dir = _cases_dir()
    pdf_files = _pdfs_na_raiz()
    try:
        media_files = sorted(p for p in cases_dir.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS)
    except OSError:
        media_files = []
    if not pdf_files and not media_files:
        return None
    if len(pdf_files) > 1:
        pdf_files = [max(pdf_files, key=lambda p: p.stat().st_mtime)]

    novos_docs: List[Dict[str, Any]] = []
    pdfs_status_list: List[Dict[str, Any]] = []
    for pdf_path in pdf_files:
        try:
            up_ts = _fmt_file_ts(datetime.datetime.fromtimestamp(pdf_path.stat().st_mtime))
            docs_pdf = _segmentar_pdf_seguro(pdf_path, remover_em_falha=False)
            novos_docs.extend(docs_pdf)
            pdfs_status_list.append(_status_pdf(pdf_path, docs_pdf, up_ts))
        except HTTPException:
            # PDF corrompido residual: ignorado e preservado (nunca apagado).
            continue

    if not novos_docs:
        return None

    pecas_prelim, known_ofendida, known_reu, _numero = _nomes_conhecidos(novos_docs)
    media_files = _filter_media_files_for_case(
        media_files=media_files,
        pecas=pecas_prelim,
        known_ofendida=known_ofendida,
        known_reu=known_reu,
    )

    novas_transcricoes: List[Dict[str, Any]] = []
    midias_status_list: List[Dict[str, Any]] = []
    try:
        for idx, m_path in enumerate(media_files):
            PROGRESSO.atualizar(
                phase="transcrevendo_midia",
                current_file=m_path.name,
                percent=min(92, 40 + int(50 * idx / max(1, len(media_files)))),
            )
            up_ts = _fmt_file_ts(datetime.datetime.fromtimestamp(m_path.stat().st_mtime))
            tr = _transcrever(
                m_path,
                depoente="OFENDIDA / TESTEMUNHA",
                papel="OFENDIDA",
                known_ofendida=known_ofendida,
                known_reu=known_reu,
                consentimento_envio_externo=consentimento_envio_externo,
            )
            novas_transcricoes.append(tr)
            midias_status_list.append(_status_midia(m_path, tr, up_ts))
    finally:
        _liberar_whisper()  # devolve a VRAM antes da geração com Ollama

    if status_saida is not None:
        status_saida["pdfs_processo"] = pdfs_status_list
        status_saida["midias_audiencia"] = midias_status_list

    return _construir_bundle_real(novos_docs, novas_transcricoes)


@app.get("/api/cases/active")
def api_get_active_case() -> Dict[str, Any]:
    """Serializa o caso ativo. Não tem efeitos colaterais (não carrega autos do disco nem transcreve:
    use ``POST /api/cases/load-from-disk``); ``arquivos_em_disco`` indica se há autos para carregar."""
    with STATE_LOCK:
        bundle = ACTIVE_CASE_STATE.get("bundle")
    return _serialize_active_bundle(bundle)


@app.post("/api/cases/load-demo")
@app.post("/api/cases/demo")
def api_load_demo_case() -> Dict[str, Any]:
    if INGESTAO_LOCK.locked():
        raise HTTPException(status_code=409, detail="Há um carregamento de autos em andamento. Aguarde a conclusão.")
    bundle = get_demo_case_bundle()
    set_active_bundle(bundle, explicit_demo=True, upload_status={"pdfs_processo": [], "midias_audiencia": []})
    return _serialize_active_bundle(bundle)


@app.post("/api/cases/load-from-disk")
def api_load_from_disk(req: Optional[LoadFromDiskRequest] = Body(default=None)) -> Dict[str, Any]:
    """Carrega (segmenta/transcreve) os autos já presentes em ``data/cases`` — operação pesada e explícita."""
    payload = req or LoadFromDiskRequest()
    if not INGESTAO_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Já existe um carregamento de autos em andamento. Aguarde a conclusão.")
    try:
        PROGRESSO.atualizar(
            active=True, status="processing", phase="carregando_autos_do_disco", percent=5,
            current_file="", total_files=0, processed_files=0, files=[], erro=None, job_id=None,
        )
        status_saida: Dict[str, Any] = {}
        bundle = _chamar_compat(
            _load_real_case_from_disk_if_present,
            consentimento_envio_externo=payload.consentimento_envio_externo,
            status_saida=status_saida,
        )
        if bundle is None:
            PROGRESSO.atualizar(active=False, status="idle", phase="aguardando", percent=0)
            raise HTTPException(
                status_code=404,
                detail="Nenhum PDF de autos válido foi encontrado em data/cases. Envie os autos pelo upload.",
            )
        upload_status = status_saida if status_saida else None
        set_active_bundle(bundle, explicit_demo=False, upload_status=upload_status)
        arquivos = [*(status_saida.get("pdfs_processo") or []), *(status_saida.get("midias_audiencia") or [])]
        PROGRESSO.atualizar(active=False, status="completed", phase="concluido", percent=100, current_file="", files=arquivos)
        return {"status": "loaded", **_serialize_active_bundle(bundle)}
    except HTTPException:
        raise
    except BaseException as exc:
        _status, corpo = descrever_erro(exc)
        PROGRESSO.atualizar(active=False, status="error", phase=f"erro: {corpo.get('detail')}", percent=0, erro=corpo)
        raise
    finally:
        INGESTAO_LOCK.release()


@app.post("/api/cases/metadata-override")
def api_metadata_override(req: MetadataOverrideRequest) -> Dict[str, Any]:
    """Complementação dos metadados pelo juízo (ex.: datas e crimes não extraídos dos autos).

    Valor ``null``/``""``/``[]`` remove o override do campo. A produção anterior (minuta/auditoria)
    é descartada, pois foi calculada com os metadados antigos.
    """
    if INGESTAO_LOCK.locked():
        # O job de upload trabalha sobre um snapshot do bundle e o substitui ao terminar: um override
        # gravado agora seria descartado em silencio.
        raise HTTPException(
            status_code=409,
            detail="Há um carregamento de autos em andamento. Aguarde a conclusão para complementar os metadados.",
        )
    bundle = _require_active_bundle()
    campos_validos = set(MetadadosProcesso.model_fields)
    desconhecidos = sorted(k for k in req.metadados if k not in campos_validos)
    if desconhecidos:
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Campos de metadados desconhecidos: " + ", ".join(desconhecidos) + ".",
                "erro": "metadados_invalidos",
                "campos": desconhecidos,
            },
        )

    base_meta = obter_contexto(bundle).meta.model_dump()
    overrides = {} if req.substituir else dict(bundle.get("metadados_override") or {})
    invalidos: List[str] = []
    for campo, valor in req.metadados.items():
        if valor is None or (isinstance(valor, str) and not valor.strip()) or (isinstance(valor, (list, tuple)) and not valor):
            overrides.pop(campo, None)
            continue
        aceito, normalizado = normalizar_override_metadado(campo, valor)
        if not aceito:
            invalidos.append(campo)
            continue
        try:
            MetadadosProcesso.model_validate({**base_meta, campo: normalizado})
        except ValidationError:
            invalidos.append(campo)
            continue
        overrides[campo] = normalizado
    if invalidos:
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Valores inválidos para: " + ", ".join(sorted(invalidos)) + " (datas em AAAA-MM-DD ou DD/MM/AAAA).",
                "erro": "metadados_invalidos",
                "campos": sorted(invalidos),
            },
        )

    novo = copy.deepcopy(bundle)
    if overrides:
        novo["metadados_override"] = overrides
    else:
        novo.pop("metadados_override", None)
    with STATE_LOCK:
        if ACTIVE_CASE_STATE.get("bundle") is not bundle:
            raise HTTPException(status_code=409, detail="O caso ativo mudou durante a atualização. Tente novamente.")
        set_active_bundle(novo, explicit_demo=bool(ACTIVE_CASE_STATE.get("explicit_demo")))
    return {"status": "ok", "metadados_override": overrides, "active_case": _serialize_active_bundle(novo)}


def _processar_arquivos_upload_sync(
    saved_pdfs: List[Path],
    saved_medias: List[Path],
    uploaded_ts_map: Dict[str, str],
    depoente_padrao: str,
    bundle: Dict[str, Any],
    is_currently_demo: bool,
    total_files: int,
    consentimento_envio_externo: bool = False,
) -> Dict[str, Any]:
    """Segmenta/classifica os PDFs, transcreve as mídias e torna o resultado o caso ativo."""
    cur_up_status = _upload_status_atual()
    pdfs_status_list = [] if (is_currently_demo or saved_pdfs) else list(cur_up_status.get("pdfs_processo", []))
    midias_status_list = [] if is_currently_demo else list(cur_up_status.get("midias_audiencia", []))
    total_files = max(1, total_files)

    processed_count = 0
    novos_docs: List[Dict[str, Any]] = []
    CANCELAR_INGESTAO.clear()
    if saved_pdfs:
        for pdf_path in saved_pdfs:
            base = 15 + int(40 * processed_count / total_files)
            span = max(1, int(40 / total_files))
            nome_seguro = _nome_arquivo_seguro(pdf_path.name, "autos.pdf")
            PROGRESSO.atualizar(
                phase="segmentando_pdf_ocr",
                etapa="texto",
                current_file=nome_seguro,
                percent=min(55, base),
                processed_files=processed_count,
                pagina_atual=0,
                paginas_total=0,
            )

            def _cb(
                etapa: str,
                atual: int,
                total: int,
                _base: int = base,
                _span: int = span,
                _nome: str = nome_seguro,
            ) -> None:
                frac = (atual / total) if total else 0.0
                PROGRESSO.atualizar(
                    phase="ocr" if etapa == "ocr" else "segmentando_pdf_ocr",
                    etapa=etapa,
                    current_file=_nome,
                    pagina_atual=int(atual),
                    paginas_total=int(total),
                    percent=min(55, _base + int(_span * frac)),
                )

            docs_pdf = _segmentar_pdf_seguro(pdf_path, ao_progresso=_cb, cancelar=CANCELAR_INGESTAO)
            processed_count += 1
            novos_docs.extend(docs_pdf)
            pdfs_status_list.append(_status_pdf(pdf_path, docs_pdf, uploaded_ts_map.get(pdf_path.name, _fmt_file_ts())))
    elif is_currently_demo:
        # Mídias enviadas com o demo ativo: vincula aos autos reais já presentes em data/cases.
        disk_pdfs = _pdfs_na_raiz()
        if len(disk_pdfs) > 1:
            disk_pdfs = [max(disk_pdfs, key=lambda p: p.stat().st_mtime)]
        for pdf_path in disk_pdfs:
            try:
                docs_pdf = _segmentar_pdf_seguro(pdf_path, remover_em_falha=False)
            except HTTPException:
                continue
            novos_docs.extend(docs_pdf)
            pdfs_status_list.append(
                _status_pdf(pdf_path, docs_pdf, _fmt_file_ts(datetime.datetime.fromtimestamp(pdf_path.stat().st_mtime)))
            )
    else:
        novos_docs = list(bundle.get("raw_documents") or [])

    pecas_prelim, known_ofendida, known_reu, numero_prelim = _nomes_conhecidos(novos_docs)
    del pecas_prelim
    is_new_process_pdf = bool(saved_pdfs and bundle.get("numero_processo") and numero_prelim != bundle.get("numero_processo"))

    existing_real_transcriptions = (
        []
        if (is_currently_demo or is_new_process_pdf or (saved_pdfs and saved_medias))
        else list(bundle.get("transcriptions") or [])
    )
    if is_new_process_pdf or (saved_pdfs and saved_medias):
        midias_status_list = []

    if saved_medias:
        por_arquivo: Dict[str, Dict[str, Any]] = {
            t.get("arquivo", ""): t for t in existing_real_transcriptions if isinstance(t, dict) and t.get("arquivo")
        }
        status_por_arquivo: Dict[str, Dict[str, Any]] = {
            item.get("filename", ""): item for item in midias_status_list if item.get("filename")
        }
        try:
            for m_path in saved_medias:
                PROGRESSO.atualizar(
                    phase="transcrevendo_midia",
                    current_file=m_path.name,
                    percent=min(92, 55 + int(35 * (processed_count / total_files))),
                    processed_files=processed_count,
                )
                tr = _transcrever(
                    m_path,
                    depoente=depoente_padrao,
                    papel="OFENDIDA",
                    known_ofendida=known_ofendida,
                    known_reu=known_reu,
                    consentimento_envio_externo=consentimento_envio_externo,
                )
                processed_count += 1
                por_arquivo[m_path.name] = tr
                status_por_arquivo[m_path.name] = _status_midia(
                    m_path, tr, uploaded_ts_map.get(m_path.name, _fmt_file_ts())
                )
        finally:
            _liberar_whisper()  # devolve a VRAM antes da geração com Ollama
        novas_transcricoes = list(por_arquivo.values())
        midias_status_list = list(status_por_arquivo.values())
    else:
        novas_transcricoes = existing_real_transcriptions

    PROGRESSO.atualizar(phase="classificando_laya", current_file="", percent=95, processed_files=processed_count)
    overrides_mantidos = (
        bundle.get("metadados_override")
        if (not is_currently_demo and not is_new_process_pdf and not saved_pdfs)
        else None
    )
    new_bundle = _construir_bundle_real(novos_docs, novas_transcricoes, overrides_mantidos)
    upload_status = {"pdfs_processo": pdfs_status_list, "midias_audiencia": midias_status_list}
    set_active_bundle(new_bundle, explicit_demo=False, upload_status=upload_status)

    PROGRESSO.atualizar(
        active=False,
        status="completed",
        phase="concluido",
        percent=100,
        current_file="",
        processed_files=total_files,
        files=[*pdfs_status_list, *midias_status_list],
    )
    return _serialize_active_bundle(new_bundle)


def _nome_arquivo_seguro(nome_original: Optional[str], padrao: str) -> str:
    nome = Path(str(nome_original or "")).name
    nome = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", nome).strip().strip(".")
    return nome or padrao


def _colocar_arquivo(tmp: Path, cases_dir: Path, filename: str) -> tuple[Path, bool]:
    """Move ``tmp`` para ``cases_dir`` SEM sobrescrever nem apagar arquivo existente.

    Mesmo conteúdo já presente (mesmo nome ou ``nome (N)``) → reutiliza o existente (``novo=False``).
    Nome ocupado por conteúdo diferente → grava como ``nome (2).ext``, ``nome (3).ext``...
    """
    destino = cases_dir / filename
    stem, suffix = Path(filename).stem, Path(filename).suffix
    tamanho = tmp.stat().st_size
    sha_tmp: Optional[str] = None
    n = 2
    while destino.exists():
        try:
            mesmo_tamanho = destino.is_file() and destino.stat().st_size == tamanho
        except OSError:
            mesmo_tamanho = False
        if mesmo_tamanho:
            sha_tmp = sha_tmp or _sha256_arquivo(tmp)
            if _sha256_arquivo(destino) == sha_tmp:
                tmp.unlink(missing_ok=True)
                return destino, False
        destino = cases_dir / f"{stem} ({n}){suffix}"
        n += 1
    os.replace(tmp, destino)
    return destino, True


def _campo_bool(valor: Optional[str]) -> bool:
    return str(valor or "").strip().lower() in ("1", "true", "on", "yes", "sim")


async def _receber_upload(
    request: Request,
    extensoes_aceitas: Optional[frozenset] = None,
    mensagem_extensao: Optional[Callable[[str], str]] = None,
    aceita_campos_extras: bool = True,
) -> Any:
    """Recebe os arquivos em streaming REAL (``request.stream()``, limite por arquivo aplicado durante
    a leitura: 413 sem gravar o excedente), valida, coloca em ``data/cases`` sem sobrescrever e
    processa (síncrono ou job em segundo plano)."""
    aceitas = extensoes_aceitas or (PDF_EXTENSIONS | MEDIA_EXTENSIONS)

    if not INGESTAO_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Já existe um processamento de autos em andamento. Aguarde a conclusão antes de enviar novos arquivos.",
        )
    liberar_lock = True
    try:
        with STATE_LOCK:
            bundle = ACTIVE_CASE_STATE.get("bundle") or {}
        is_currently_demo = is_demo_bundle(bundle)
        settings = load_settings()
        cases_dir = _cases_dir()
        cases_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir = cases_dir / _UPLOAD_TMP_DIRNAME
        tmp_dir.mkdir(parents=True, exist_ok=True)

        PROGRESSO.atualizar(
            active=True, status="processing", phase="recebendo_arquivos", percent=2, current_file="",
            total_files=0, processed_files=0, files=[], erro=None, job_id=None,
        )

        def _validar_parte(filename: str, ext: str) -> int:
            if ext not in aceitas:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        mensagem_extensao(filename)
                        if mensagem_extensao
                        else (
                            f"Arquivo '{filename}' não suportado. Envie os PDFs dos autos e as mídias de audiência "
                            f"({descrever_extensoes_midia()})."
                        )
                    ),
                )
            PROGRESSO.atualizar(current_file=_nome_arquivo_seguro(filename, "arquivo"), phase="recebendo_arquivos")
            limite_mb = settings.max_upload_mb_pdf if ext in PDF_EXTENSIONS else settings.max_upload_mb_midia
            return int(limite_mb) * 1024 * 1024

        depoente_padrao = "OFENDIDA / TESTEMUNHA"
        consentimento_envio_externo = False
        assincrono = False
        recebidos_parts: List[ArquivoRecebido] = []
        recebidos: List[tuple[str, Path, str, str]] = []
        files: List[ArquivoRecebido] = recebidos_parts
        try:
            campos = await receber_multipart(request, tmp_dir, validar_arquivo=_validar_parte, arquivos=recebidos_parts)
            if not recebidos_parts:
                raise HTTPException(status_code=422, detail="Nenhum arquivo enviado.")
            if aceita_campos_extras:
                depoente_padrao = campos.get("depoente_padrao") or "OFENDIDA / TESTEMUNHA"
                consentimento_envio_externo = _campo_bool(campos.get("consentimento_envio_externo"))
            assincrono = _campo_bool(campos.get("assincrono"))
            PROGRESSO.atualizar(total_files=len(files))
            for idx, parte in enumerate(recebidos_parts):
                ext = parte.ext
                filename = _nome_arquivo_seguro(parte.filename_original, f"arquivo_{idx + 1}{ext}")
                recebidos.append((filename, parte.tmp, ext, _fmt_file_ts()))
                if parte.total == 0:
                    if ext in PDF_EXTENSIONS:
                        raise HTTPException(
                            status_code=422,
                            detail=f"O arquivo PDF '{filename}' está vazio (0 bytes). Envie o PDF integral dos autos do PJe.",
                        )
                    raise HTTPException(status_code=422, detail=f"O arquivo de mídia '{filename}' está vazio (0 bytes).")
                if ext in PDF_EXTENSIONS:
                    PROGRESSO.atualizar(phase="validando_arquivos")
                    _validar_pdf_arquivo(filename, parte.tmp)
                PROGRESSO.atualizar(percent=min(14, 2 + int(12 * (idx + 1) / len(files))))
        except BaseException as exc:
            for parte in recebidos_parts:
                try:
                    parte.tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            _status, corpo = descrever_erro(exc)
            PROGRESSO.atualizar(active=False, status="error", phase=f"erro: {corpo.get('detail')}", percent=0, erro=corpo)
            raise

        # Colocação definitiva (sem sobrescrever/apagar arquivos existentes). Qualquer falha
        # (antivírus/OneDrive, disco cheio) desfaz o que ESTE upload já colocou e remove os .part.
        saved_pdfs: List[Path] = []
        saved_medias: List[Path] = []
        novos_arquivos: List[Path] = []
        uploaded_ts_map: Dict[str, str] = {}
        try:
            for filename, tmp, ext, ts in recebidos:
                destino, novo = _colocar_arquivo(tmp, cases_dir, filename)
                uploaded_ts_map[destino.name] = ts
                (saved_pdfs if ext in PDF_EXTENSIONS else saved_medias).append(destino)
                if novo:
                    novos_arquivos.append(destino)
        except BaseException as exc:
            for arquivo in novos_arquivos:
                try:
                    arquivo.unlink(missing_ok=True)
                except OSError:
                    pass
            for _filename, tmp, _ext, _ts in recebidos:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            _status, corpo = descrever_erro(exc)
            PROGRESSO.atualizar(active=False, status="error", phase=f"erro: {corpo.get('detail')}", percent=0, erro=corpo)
            raise

        def _processar() -> Dict[str, Any]:
            try:
                serialized = _processar_arquivos_upload_sync(
                    saved_pdfs,
                    saved_medias,
                    uploaded_ts_map,
                    depoente_padrao,
                    bundle,
                    is_currently_demo,
                    len(files),
                    consentimento_envio_externo,
                )
            except BaseException as exc:
                # Rollback: só os arquivos colocados POR ESTE upload (nunca os preexistentes).
                for arquivo in novos_arquivos:
                    try:
                        arquivo.unlink(missing_ok=True)
                    except OSError:
                        pass
                _status, corpo = descrever_erro(exc)
                PROGRESSO.atualizar(active=False, status="error", phase=f"erro: {corpo.get('detail')}", percent=0, erro=corpo)
                raise
            return {"status": "uploaded", "files_processed": len(files), **serialized}

        if assincrono:
            job = JOBS.criar("upload")
            PROGRESSO.atualizar(job_id=job["job_id"])
            JOBS.executar_em_thread(job["job_id"], _processar, ao_finalizar=INGESTAO_LOCK.release)
            liberar_lock = False
            return JSONResponse(
                status_code=202,
                content={
                    "status": "processing",
                    "job_id": job["job_id"],
                    "job_url": f"/api/jobs/{job['job_id']}",
                    "progress_url": "/api/cases/upload-progress",
                },
            )
        return await asyncio.to_thread(_processar)
    finally:
        if liberar_lock:
            INGESTAO_LOCK.release()


@app.post("/api/cases/upload-files")
async def api_upload_case_files(request: Request) -> Any:
    """Upload de autos (PDF) e/ou mídias (campo ``files``; opcionais ``depoente_padrao``,
    ``consentimento_envio_externo``). ``assincrono=true`` devolve 202 + ``job_id``."""
    return await _receber_upload(request)


@app.post("/api/cases/upload-pdfs")
async def api_upload_case_pdfs(request: Request) -> Any:
    """Upload da Parte 1: PDFs do processo (PJe / eproc)."""
    return await _receber_upload(
        request,
        extensoes_aceitas=PDF_EXTENSIONS,
        mensagem_extensao=lambda nome: f"A área de Autos Processuais aceita exclusivamente arquivos .PDF (recebido: '{nome}').",
        aceita_campos_extras=False,
    )


@app.post("/api/cases/upload-medias")
async def api_upload_case_medias(request: Request) -> Any:
    """Upload da Parte 2: mídias de audiência (.m4a, .mkv, .mp3, .mp4, .ogg, .wav)."""
    return await _receber_upload(
        request,
        extensoes_aceitas=MEDIA_EXTENSIONS,
        mensagem_extensao=lambda nome: (
            "A área de Mídias de Audiência aceita apenas arquivos audiovisuais "
            f"({descrever_extensoes_midia()}). Recebido: '{nome}'."
        ),
    )

def _analises_orais_em_disco() -> Dict[str, Any]:
    """Análises (falantes e alegações) gravadas, por ``media_id``."""
    from app.transcription.falas import ler_analise

    with STATE_LOCK:
        bundle = ACTIVE_CASE_STATE.get("bundle") or {}
    saida: Dict[str, Any] = {}
    for tr in bundle.get("transcriptions") or []:
        if not isinstance(tr, dict) or not tr.get("arquivo"):
            continue
        analise = ler_analise(_cases_dir() / str(tr["arquivo"]))
        if analise:
            saida[str(tr.get("media_id") or tr["arquivo"])] = analise
    return saida


@app.get("/api/cases/oral/analise")
def api_analise_oral() -> Dict[str, Any]:
    """Análises já feitas (quem fala + alegações com trecho literal), por mídia."""
    return {"analises": _analises_orais_em_disco()}


@app.post("/api/cases/oral/analisar")
def api_analisar_oral(refazer: bool = Query(False)) -> Any:
    """Marca quem fala e extrai as alegações de cada depoimento com a IA local (job em segundo plano)."""
    from app.transcription.falas import analisar_midia

    with STATE_LOCK:
        bundle = ACTIVE_CASE_STATE.get("bundle") or {}
    transcricoes = [
        t for t in (bundle.get("transcriptions") or []) if isinstance(t, dict) and t.get("segmentos") and t.get("arquivo")
    ]
    if not transcricoes:
        raise HTTPException(status_code=409, detail="Não há mídias transcritas para analisar.")
    if not INGESTAO_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Há um processamento em andamento. Aguarde terminar.")
    modelo = load_settings().ollama_model

    def _rodar() -> Dict[str, Any]:
        _liberar_whisper()  # libera a VRAM para o modelo de linguagem
        total = len(transcricoes)
        feitas: List[str] = []
        for n, tr in enumerate(transcricoes, 1):
            PROGRESSO.atualizar(
                active=True, status="processing", phase=f"Analisando falas e alegações ({n}/{total})",
                percent=int(100 * (n - 1) / total), current_file=str(tr.get("arquivo") or ""),
            )
            analisar_midia(_cases_dir() / str(tr["arquivo"]), tr, modelo, forcar=refazer)
            feitas.append(str(tr.get("media_id") or ""))
        PROGRESSO.atualizar(active=False, status="done", phase="Análise concluída", percent=100)
        return {"analisadas": feitas, "analises": _analises_orais_em_disco()}

    job = JOBS.criar("analise_oral")
    JOBS.executar_em_thread(job["job_id"], _rodar, ao_finalizar=INGESTAO_LOCK.release)
    return JSONResponse(status_code=202, content={"status": "processing", "job_id": job["job_id"], "job_url": f"/api/jobs/{job['job_id']}"})


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str) -> Dict[str, Any]:
    job = JOBS.obter(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado (ou expirado).")
    return {**job, "progress": PROGRESSO.obter()}


def _motores_status_cacheado(bundle: Dict[str, Any]) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Status dos 5 motores, recalculado só quando o conteúdo do caso muda (fingerprint)."""
    ctx = obter_contexto(bundle)
    with STATE_LOCK:
        cache = ACTIVE_CASE_STATE.get("motores_cache")
    if isinstance(cache, dict) and cache.get("fingerprint") == ctx.fingerprint:
        return cache.get("motores_py") or [], cache.get("erro")
    erro: Optional[Dict[str, Any]] = None
    try:
        *_rest, motores_status = executar_motores_py_separados(bundle)
        motores_info = [m.model_dump(mode="json") for m in motores_status]
    except DadosInsuficientesError as exc:
        motores_info, erro = [], corpo_dados_insuficientes(exc)
    except Exception as exc:  # telemetria não pode derrubar o polling
        logger.warning("Falha ao calcular o status dos motores: %s", type(exc).__name__)
        motores_info, erro = [], {"detail": f"Falha ao calcular o status dos motores ({type(exc).__name__}).", "erro": "erro_interno"}
    with STATE_LOCK:
        if ACTIVE_CASE_STATE.get("bundle") is bundle:
            ACTIVE_CASE_STATE["motores_cache"] = {"fingerprint": ctx.fingerprint, "motores_py": motores_info, "erro": erro}
    return motores_info, erro


@app.get("/api/cases/upload-status")
def api_get_upload_status() -> Dict[str, Any]:
    """Status do carregamento (PDFs/mídias), dos motores (cacheado) e o progresso corrente."""
    with STATE_LOCK:
        up_status = copy.deepcopy(ACTIVE_CASE_STATE.get("upload_status") or {"pdfs_processo": [], "midias_audiencia": []})
        bundle = ACTIVE_CASE_STATE.get("bundle")
    motores_info: List[Dict[str, Any]] = []
    motores_erro: Optional[Dict[str, Any]] = None
    transcricoes: List[Dict[str, Any]] = []
    if bundle and bundle.get("raw_documents"):
        motores_info, motores_erro = _motores_status_cacheado(bundle)
        transcricoes = _resumo_transcricoes(bundle)
    return {
        "status": "ok",
        "upload_status": up_status,
        "motores_py": motores_info,
        "motores_erro": motores_erro,
        "transcricoes": transcricoes,
        "progress": PROGRESSO.obter(),
    }


@app.post("/api/cases/cancelar-ingestao")
def api_cancelar_ingestao() -> Dict[str, Any]:
    """Sinaliza cancelamento cooperativo do OCR/ingestão em andamento (TASK-027)."""
    CANCELAR_INGESTAO.set()
    PROGRESSO.atualizar(cancelado=True, phase="cancelando")
    return {"status": "ok", "cancelado": True}


class RefazerOcrRequest(BaseModel):
    filename: Optional[str] = None


@app.post("/api/cases/refazer-ocr")
def api_refazer_ocr(req: Optional[RefazerOcrRequest] = Body(default=None)) -> Dict[str, Any]:
    """Reprocessa o OCR das páginas digitalizadas de um PDF já carregado em ``data/cases/`` (sem novo upload)."""
    if motor_ocr_disponivel() is None:
        raise HTTPException(
            status_code=503,
            detail="Motor de OCR não instalado. Feche o programa e abra pelo 'Iniciar SentencIA'.",
        )
    if not INGESTAO_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Há uma ingestão ou OCR em andamento. Aguarde a conclusão.",
        )
    try:
        cases_dir = _cases_dir().resolve()
        alvo_nome = _nome_arquivo_seguro(req.filename, "") if (req and req.filename) else ""
        if alvo_nome:
            pdf_path = (cases_dir / alvo_nome).resolve()
            if pdf_path.parent != cases_dir or not pdf_path.is_file():
                raise HTTPException(status_code=404, detail=f"PDF '{alvo_nome}' não encontrado na pasta do caso.")
            alvos = [pdf_path]
        else:
            alvos = _pdfs_na_raiz()
            if not alvos:
                raise HTTPException(status_code=404, detail="Nenhum PDF encontrado na pasta do caso para refazer OCR.")

        CANCELAR_INGESTAO.clear()
        PROGRESSO.reiniciar()
        PROGRESSO.atualizar(
            active=True,
            status="running",
            phase="ocr",
            etapa="ocr",
            total_files=len(alvos),
            processed_files=0,
        )

        with STATE_LOCK:
            bundle_atual = copy.deepcopy(ACTIVE_CASE_STATE.get("bundle") or {})
        transcricoes_atuais = list(bundle_atual.get("transcriptions") or [])
        overrides_atuais = bundle_atual.get("metadados_override")
        up_status = _upload_status_atual()
        pdfs_status_map: Dict[str, Dict[str, Any]] = {
            str(it.get("filename")): dict(it)
            for it in (up_status.get("pdfs_processo") or [])
            if isinstance(it, dict) and it.get("filename")
        }

        # Reúne TODOS os PDFs do caso (os já registrados em upload_status existentes no disco + alvos)
        # para que refazer o OCR de 1 PDF nunca apague as peças dos outros PDFs do mesmo processo.
        alvos_nomes = {p.name for p in alvos}
        todos_pdfs_ordem: List[Path] = []
        vistos_pdfs: set[str] = set()
        for nome_reg in pdfs_status_map.keys():
            cand = (cases_dir / _nome_arquivo_seguro(nome_reg, "")).resolve()
            if cand.parent == cases_dir and cand.is_file() and cand.name not in vistos_pdfs:
                todos_pdfs_ordem.append(cand)
                vistos_pdfs.add(cand.name)
        for p_alvo in alvos:
            if p_alvo.name not in vistos_pdfs:
                todos_pdfs_ordem.append(p_alvo)
                vistos_pdfs.add(p_alvo.name)

        todos_docs: List[Dict[str, Any]] = []
        alvos_processados = 0
        try:
            for pdf_path in todos_pdfs_ordem:
                nome_seguro = _nome_arquivo_seguro(pdf_path.name, "autos.pdf")
                if pdf_path.name in alvos_nomes:
                    idx_alvo = alvos_processados

                    def _cb(
                        etapa: str,
                        atual: int,
                        total: int,
                        _nome: str = nome_seguro,
                        _idx: int = idx_alvo,
                    ) -> None:
                        frac = (atual / total) if total else 0.0
                        PROGRESSO.atualizar(
                            phase="ocr" if etapa == "ocr" else "segmentando_pdf_ocr",
                            etapa=etapa,
                            current_file=_nome,
                            pagina_atual=int(atual),
                            paginas_total=int(total),
                            processed_files=_idx,
                            percent=min(92, max(5, int(92 * ((_idx + frac) / max(1, len(alvos)))))),
                        )

                    docs_pdf = _segmentar_pdf_seguro(
                        pdf_path,
                        remover_em_falha=False,
                        ao_progresso=_cb,
                        usar_cache=False,
                        cancelar=CANCELAR_INGESTAO,
                    )
                    alvos_processados += 1
                    up_ts = (pdfs_status_map.get(pdf_path.name) or {}).get("uploaded_at") or _fmt_file_ts(
                        datetime.datetime.fromtimestamp(pdf_path.stat().st_mtime)
                    )
                    pdfs_status_map[pdf_path.name] = _status_pdf(pdf_path, docs_pdf, up_ts)
                else:
                    # Outro PDF do mesmo caso: preserva do cache em disco sem refazer OCR.
                    docs_pdf = _segmentar_pdf_seguro(pdf_path, remover_em_falha=False, usar_cache=True)
                todos_docs.extend(docs_pdf)

            PROGRESSO.atualizar(phase="classificando_laya", etapa="classificacao", current_file="", percent=95)
            up_status["pdfs_processo"] = list(pdfs_status_map.values())
            novo_bundle = _construir_bundle_real(todos_docs, transcricoes_atuais, overrides_atuais)
            set_active_bundle(novo_bundle, explicit_demo=False, upload_status=up_status)
            PROGRESSO.atualizar(
                active=False,
                status="completed",
                phase="concluido",
                etapa="concluido",
                percent=100,
                current_file="",
                processed_files=len(alvos),
            )
            return _serialize_active_bundle(novo_bundle)
        except BaseException as exc:
            detalhe_err = getattr(exc, "detail", None) or type(exc).__name__
            PROGRESSO.atualizar(
                active=False,
                status="error",
                phase=f"erro: {detalhe_err}",
                etapa="erro",
                erro=str(detalhe_err),
            )
            raise
    finally:
        INGESTAO_LOCK.release()


@app.get("/api/cases/upload-progress")
async def api_get_upload_progress(request: Request, stream: int = 0) -> Any:
    """Progresso em tempo real (JSON leve; SSE com ``?stream=1`` ou ``Accept: text/event-stream``).

    O SSE encerra quando o processamento termina, o cliente desconecta ou após ~15 minutos.
    """
    accept_hdr = request.headers.get("accept", "").lower()
    if stream == 1 or "text/event-stream" in accept_hdr:

        async def _event_stream():
            for _ in range(1800):
                snap = PROGRESSO.obter()
                yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
                if not snap.get("active"):
                    break
                if await request.is_disconnected():
                    break
                await asyncio.sleep(0.5)

        return StreamingResponse(_event_stream(), media_type="text/event-stream")
    return PROGRESSO.obter()


# ==============================================================================
# Dosimetria e geração
# ==============================================================================


def _decisoes_serializaveis(decisoes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    chaves = ("crime_code", "crime_efetivo", "veredicto", "fundamento_legal", "desclassificado_de", "inciso", "presumido")
    return [{k: d.get(k) for k in chaves if k in d} | {"presumido": bool(d.get("presumido", False))} for d in decisoes]


def _fracao_txt(fracao: Any) -> Optional[str]:
    if isinstance(fracao, (list, tuple)) and len(fracao) == 2:
        return f"{fracao[0]}/{fracao[1]}"
    return None


@app.post("/api/dosimetria/simulate")
def api_simulate_dosimetria(req: Optional[DosimetriaSimulationRequest] = Body(default=None)) -> Dict[str, Any]:
    """Simulação de dosimetria: crimes sem veredicto têm a condenação PRESUMIDA apenas para cálculo
    (``simulacao_presume_condenacao``). Sem data do fato nos autos/override → 422 (nada de data fictícia)."""
    payload = req or DosimetriaSimulationRequest()
    bundle = _require_active_bundle()
    ctx = obter_contexto(bundle)
    meta = ctx.copia_meta()
    override = payload.to_override()

    dt_fato = resolver_data(meta.data_fato, bundle.get("data_fato"))
    dt_rec = resolver_data(meta.data_recebimento_denuncia, bundle.get("data_recebimento_denuncia"))
    if not meta.crimes_imputados and isinstance(bundle.get("crimes_imputados"), list):
        meta.crimes_imputados = [str(c).strip() for c in bundle["crimes_imputados"] if str(c).strip()]
    faltantes = []
    if dt_fato is None:
        faltantes.append("data_fato")
    if not meta.crimes_imputados and not override.get("crimes_config"):
        faltantes.append("crimes_imputados")
    if faltantes:
        raise DadosInsuficientesError(
            faltantes,
            "Não é possível simular a dosimetria: dados não extraídos dos autos ("
            + ", ".join(faltantes)
            + "). Complemente-os em POST /api/cases/metadata-override.",
        )
    data_sentenca = resolver_data(override.get("data_sentenca"), bundle.get("data_sentenca")) or datetime.date.today()

    res, presc, decisoes, criterio_1fase, detracao_periodo = resolver_dosimetria_e_decisoes(
        pecas=list(ctx.pecas),
        meta=meta,
        transcriptions=list(ctx.transcriptions),
        is_demo=ctx.is_demo,
        dt_fato=dt_fato,
        dt_rec=dt_rec,
        custom_dosimetria_override=override,
        exigir_veredicto=False,
        data_sentenca=data_sentenca,
    )
    alertas: List[str] = []
    if dt_rec is None:
        alertas.append(
            "Data de recebimento da denúncia não extraída: a prescrição não pôde ser verificada (informe-a nos metadados)."
        )
    zero_pena = PenaAnosMesesDias(anos=0, meses=0, dias=0)
    return {
        "dosimetria": res.model_dump(mode="json"),
        "prescricao": presc.model_dump(mode="json"),
        "decisoes_crimes": _decisoes_serializaveis(decisoes),
        "simulacao_presume_condenacao": any(d.get("presumido") for d in decisoes),
        "criterio_primeira_fase": criterio_1fase,
        "detracao_periodo": detracao_periodo,
        "detracao_dias": res.detracao_dias,
        "tipo_concurso": res.tipo_concurso,
        "fracao_concurso": _fracao_txt(res.fracao_concurso),
        "fracao_concurso_sugerida": _fracao_txt(res.fracao_concurso_sugerida),
        "data_fato": dt_fato.isoformat(),
        "data_recebimento_denuncia": dt_rec.isoformat() if dt_rec else None,
        "data_sentenca": data_sentenca.isoformat(),
        "alertas": alertas,
        "resumo_formatado": {
            "pena_total_extenso": res.pena_total_unificada.format_extenso(),
            "pena_total_curto": res.pena_total_unificada.format_curto(),
            "reclusao_extenso": res.penas_por_especie.get("reclusao", zero_pena).format_extenso(),
            "detencao_extenso": res.penas_por_especie.get("detencao", zero_pena).format_extenso(),
        },
    }


@app.post("/api/sentence/generate")
def api_generate_sentence(req: Optional[GenerateSentenceRequest] = Body(default=None)) -> Dict[str, Any]:
    """Geração integral determinística. Caso real exige veredicto por crime em ``crimes_config``
    (senão 422 ``decisao_judicial_pendente``)."""
    payload = req or GenerateSentenceRequest()
    bundle = _require_active_bundle()
    ctx = obter_contexto(bundle)
    sentenca = gerar_sentenca_estruturada(
        case_bundle=bundle,
        custom_dosimetria_override=payload.to_override(),
        use_ollama_if_available=payload.use_ollama,
    )
    auditoria = _auditar(sentenca, ctx)
    generation_id = _registrar_se_caso_ativo(
        bundle, sentenca=sentenca, auditoria=auditoria, origem="monolitica", pipeline_engine=None, pipeline_snapshot=None
    )
    return _payload_geracao(sentenca, auditoria, generation_id)


def _build_gating_validator(use_ollama: bool, usar_duplo_crivo: bool):
    """Duplo Crivo (M3) quando disponível e solicitado; senão o validador determinístico padrão."""
    if not usar_duplo_crivo or criar_gating_validator_duplo_crivo is None:
        return default_in_flight_gating_validator
    try:
        validator = criar_gating_validator_duplo_crivo(use_ollama=use_ollama)
    except Exception:
        validator = None
    return validator or default_in_flight_gating_validator


def _executar_pipeline_seccional(payload: GenerateSectionalRequest, case_bundle: Dict[str, Any]):
    """Executa o pipeline seccional. Erros de domínio (dados/veredicto) sobem como estão (→ 422);
    falha em runtime do Duplo Crivo reexecuta uma única vez com o validador padrão."""
    gating_validator = _build_gating_validator(payload.use_ollama, payload.usar_duplo_crivo)

    def _run(validator):
        engine_local = SectionalPipelineEngine(use_ollama=payload.use_ollama, gating_validator=validator)
        sentenca_local = engine_local.executar_pipeline(
            case_bundle=case_bundle,
            use_ollama=payload.use_ollama,
            custom_dosimetria_override=payload.to_override() or None,
            secoes_habilitadas=payload.secoes_habilitadas,
        )
        return engine_local, sentenca_local

    try:
        return _run(gating_validator)
    except Exception as exc:
        if eh_erro_de_dominio(exc):
            raise
        if gating_validator is default_in_flight_gating_validator:
            if isinstance(exc, (ValueError, KeyError, TypeError)):
                raise HTTPException(status_code=422, detail=f"Payload de geração seccional inválido: {exc}") from exc
            raise
        logger.warning("Validador Duplo Crivo falhou (%s); reexecutando com o validador padrão.", type(exc).__name__)
        try:
            return _run(default_in_flight_gating_validator)
        except Exception as fallback_exc:
            if eh_erro_de_dominio(fallback_exc):
                raise
            if isinstance(fallback_exc, (ValueError, KeyError, TypeError)):
                raise HTTPException(
                    status_code=422, detail=f"Payload de geração seccional inválido: {fallback_exc}"
                ) from fallback_exc
            raise


@app.post("/api/sentence/generate-sectional")
def api_generate_sectional(req: Optional[GenerateSectionalRequest] = Body(default=None)) -> Dict[str, Any]:
    """Executa o pipeline seccional (5 seções) e audita a minuta resultante."""
    payload = req or GenerateSectionalRequest()

    if payload.secoes_habilitadas is not None:
        secoes_normalizadas = [str(s).strip().lower() for s in payload.secoes_habilitadas]
        invalidas = [s for s in secoes_normalizadas if s not in VALID_SECTION_NAMES]
        if invalidas:
            raise HTTPException(
                status_code=422,
                detail=f"secoes_habilitadas inválidas: {invalidas}. Válidas: {list(VALID_SECTION_NAMES)}.",
            )
    else:
        secoes_normalizadas = list(VALID_SECTION_NAMES)

    with STATE_LOCK:
        active_bundle = ACTIVE_CASE_STATE.get("bundle")

    case_bundle = payload.case_bundle if payload.case_bundle is not None else active_bundle
    if case_bundle is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Nenhum caso ativo para geração seccional. Carregue autos reais ou o "
                "caso demonstrativo (POST /api/cases/load-demo)."
            ),
        )
    if not isinstance(case_bundle, dict) or not case_bundle.get("raw_documents"):
        raise HTTPException(status_code=422, detail="Payload inválido: 'case_bundle' deve conter 'raw_documents' não vazio.")

    ctx = obter_contexto(case_bundle)
    engine, sentenca = _executar_pipeline_seccional(payload, case_bundle)
    auditoria = _auditar(sentenca, ctx)
    snapshot = engine.snapshot_telemetria()

    if payload.case_bundle is not None:
        # Bundle fornecido explicitamente passa a ser o caso ativo da sessão.
        generation_id = register_generation(
            sentenca,
            auditoria,
            origem="seccional",
            pipeline_engine=engine,
            pipeline_snapshot=snapshot,
            bundle=case_bundle,
            explicit_demo=is_demo_bundle(case_bundle),
        )
    else:
        generation_id = _registrar_se_caso_ativo(
            case_bundle,
            sentenca=sentenca,
            auditoria=auditoria,
            origem="seccional",
            pipeline_engine=engine,
            pipeline_snapshot=snapshot,
        )

    return _payload_geracao(
        sentenca,
        auditoria,
        generation_id,
        status="completed",
        pipeline_seccional=True,
        secoes={
            "relatorio": sentenca.secao_relatorio,
            "preliminares": sentenca.secao_preliminares,
            "merito": sentenca.secao_merito,
            "dosimetria": sentenca.secao_dosimetria,
            "dispositivo": sentenca.secao_dispositivo,
        },
        score_auditoria=auditoria.score_confiabilidade,
        telemetria=snapshot,
        usar_duplo_crivo=bool(payload.usar_duplo_crivo and criar_gating_validator_duplo_crivo is not None),
        secoes_habilitadas=secoes_normalizadas,
    )


@app.post("/api/sentence/regenerate-section")
def api_regenerate_section(req: RegenerateSectionRequest) -> Dict[str, Any]:
    """Regenera atomicamente UMA seção da minuta seccional ativa e a reaudita (serializado por lock)."""
    secao_nome = str(req.secao or "").strip().lower()
    if secao_nome not in VALID_SECTION_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Seção inválida: {req.secao!r}. Seções válidas: {list(VALID_SECTION_NAMES)}.",
        )

    if not PIPELINE_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Já existe uma regeneração de seção em andamento. Aguarde.")
    try:
        with STATE_LOCK:
            engine = ACTIVE_CASE_STATE.get("pipeline_engine")

        if engine is None or not isinstance(engine, SectionalPipelineEngine):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Pipeline seccional não iniciado. Gere a sentença seccional "
                    "(POST /api/sentence/generate-sectional) antes de regenerar uma seção."
                ),
            )

        secoes_faltantes = [s.value for s in SecaoTipo if s not in engine.estado.secoes_seladas]
        if not getattr(engine, "_last_bundle", None) or secoes_faltantes:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Pipeline seccional incompleto ou sem bundle associado: "
                    f"seções ausentes: {secoes_faltantes}. Gere a sentença seccional completa."
                ),
            )
        bundle = engine._last_bundle
        if not bundle.get("raw_documents"):
            raise HTTPException(status_code=409, detail="Bundle do pipeline seccional sem 'raw_documents'.")

        secao_enum = SecaoTipo(secao_nome)
        partes_feedback: List[str] = []
        if req.instrucoes_cirurgicas:
            partes_feedback.append(str(req.instrucoes_cirurgicas))
        if req.paragrafo_especifico:
            partes_feedback.append(f"Foco cirúrgico no parágrafo: {req.paragrafo_especifico}")
        feedback = "\n".join(partes_feedback) or None
        effective_use_ollama = bool(req.use_ollama) if req.use_ollama is not None else bool(getattr(engine, "use_ollama", False))

        def _regenerar():
            rascunho_local = engine.regenerar_secao_atomica(secao=secao_enum, feedback=feedback, use_ollama=effective_use_ollama)
            return rascunho_local, engine.montar_sentenca()

        try:
            rascunho, sentenca = _regenerar()
        except Exception as exc:
            if eh_erro_de_dominio(exc):
                raise
            if isinstance(exc, ValueError):
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            # Validador do Duplo Crivo instável em runtime: reexecuta com o padrão (sob o lock).
            if getattr(engine, "gating_validator", None) is default_in_flight_gating_validator:
                raise
            engine.gating_validator = default_in_flight_gating_validator
            try:
                rascunho, sentenca = _regenerar()
            except ValueError as exc2:
                if eh_erro_de_dominio(exc2):
                    raise
                raise HTTPException(status_code=409, detail=str(exc2)) from exc2

        texto_regenerado = rascunho.texto_redigido if rascunho is not None else engine.estado.secoes_seladas.get(secao_enum, "")
        auditoria = _auditar(sentenca, obter_contexto(bundle))
        snapshot = engine.snapshot_telemetria()

        with STATE_LOCK:
            if ACTIVE_CASE_STATE.get("pipeline_engine") is not engine:
                raise HTTPException(
                    status_code=409,
                    detail="A sessão mudou durante a regeneração (reset, troca de caso ou nova geração). Gere novamente.",
                )
            generation_id = update_regenerated_section(sentenca, auditoria, engine, snapshot)
    finally:
        PIPELINE_LOCK.release()

    return {
        "status": "regenerated",
        "secao": secao_nome,
        "texto_regenerado": texto_regenerado,
        "auditoria": auditoria.model_dump(mode="json"),
        "score_confiabilidade": auditoria.score_confiabilidade,
        "generation_id": generation_id,
        "telemetria": snapshot,
        "marcadores_pendentes": list(getattr(sentenca, "marcadores_pendentes", []) or []),
        "secoes_fallback": list(getattr(sentenca, "secoes_fallback", []) or []),
        "exportacao_liberada": _auditoria_aprovada(auditoria),
    }


@app.get("/api/sentence/last")
def api_last_sentence() -> Dict[str, Any]:
    """Última minuta da sessão (para a interface recuperar o estado após recarregar a página)."""
    st = obter_estado("bundle", "last_sentence", "last_audit", "generation_id", "last_generation_origin", "pipeline_snapshot")
    if st["bundle"] is None or st["last_sentence"] is None or st["last_audit"] is None:
        raise HTTPException(status_code=404, detail="Nenhuma sentença gerada nesta sessão.")
    return _payload_geracao(
        st["last_sentence"],
        st["last_audit"],
        int(st["generation_id"] or 0),
        origem=st["last_generation_origin"],
        telemetria=st["pipeline_snapshot"],
    )


@app.post("/api/cases/limpar-demo")
def api_limpar_demo(
    req: Optional[LimparDemoRequest] = Body(default=None),
) -> Dict[str, Any]:
    """Simula ou remove apenas artefatos de demonstração/teste (nunca toca em autos reais)."""
    payload = req or LimparDemoRequest()
    if not payload.simular and not payload.confirmar:
        raise HTTPException(
            status_code=422,
            detail="A remoção de dados de demonstração exige simular=false e confirmar=true.",
        )
    data_dir_atual = _cases_dir().parent
    try:
        resultado = limpar_artefatos_demo(
            data_dir_atual,
            simular=payload.simular,
            confirmar=payload.confirmar,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    with STATE_LOCK:
        bundle_atual = ACTIVE_CASE_STATE.get("bundle")
        demo_em_memoria = is_demo_or_test_bundle(bundle_atual)
        if not payload.simular and payload.confirmar and demo_em_memoria:
            clear_production_state()
            ACTIVE_CASE_STATE["bundle"] = None
            ACTIVE_CASE_STATE["explicit_demo"] = False
            ACTIVE_CASE_STATE["motores_cache"] = None
            ACTIVE_CASE_STATE["gemini_script_mapa"] = None
            ACTIVE_CASE_STATE["upload_status"] = {"pdfs_processo": [], "midias_audiencia": []}

    resultado["demo_ativa_em_memoria"] = demo_em_memoria
    return resultado


# ==============================================================================
# Reset
# ==============================================================================


@app.post("/api/cases/arquivo-morto/retencao")
def api_retencao_arquivo_morto(
    req: Optional[RetencaoArquivoMortoRequest] = Body(default=None),
) -> Dict[str, Any]:
    """Aplica (ou simula) a política de retenção sobre ``data/cases/_arquivados/``.
    Remoção definitiva exige ``simular=false`` e ``confirmar=true``."""
    from app.core.retencao_arquivo_morto import aplicar_retencao

    payload = req or RetencaoArquivoMortoRequest()
    if not payload.simular and not payload.confirmar:
        raise HTTPException(
            status_code=422,
            detail="A remoção definitiva do arquivo morto exige simular=false e confirmar=true.",
        )
    if not INGESTAO_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Há um carregamento de autos em andamento. Aguarde antes de executar a retenção.",
        )
    try:
        try:
            return aplicar_retencao(
                manter_dias=payload.manter_dias,
                manter_ultimos=payload.manter_ultimos,
                simular=payload.simular,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        INGESTAO_LOCK.release()


# ----------------------------------------------------------------------------------------------
# PRÉ-ANÁLISE DE PROCESSOS EM LOTE (Fase 5 / TASK-016)
# ----------------------------------------------------------------------------------------------
@app.post("/api/lote")
async def api_criar_lote(files: List[UploadFile] = File(...)) -> Dict[str, Any]:
    """Recebe múltiplos PDFs em streaming para disco e inicia pré-análise determinística em
    segundo plano (concorrência 1). Nunca mantém os PDFs em RAM nem altera o caso ativo."""
    import hashlib
    from app.lote.manager import LOTE_MANAGER, MAX_ARQUIVOS_LOTE, _truncar_nome_arquivo

    if not files:
        raise HTTPException(status_code=422, detail="Envie ao menos um arquivo PDF para o lote.")
    if len(files) > MAX_ARQUIVOS_LOTE:
        raise HTTPException(
            status_code=413,
            detail=f"O lote aceita no máximo {MAX_ARQUIVOS_LOTE} arquivos por envio (recebidos: {len(files)}).",
        )

    for uf in files:
        nome = Path(uf.filename or "").name
        if not nome.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=415,
                detail=f"Tipo de arquivo não suportado no lote ('{nome}'): envie apenas arquivos .pdf.",
            )

    if not INGESTAO_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Já existe um carregamento ou lote em andamento. Aguarde a conclusão.",
        )

    settings = load_settings()
    limite_mb = int(getattr(settings, "max_upload_mb_pdf", 250) or 250)
    limite_arquivo_bytes = limite_mb * 1024 * 1024
    limite_lote_bytes = limite_arquivo_bytes * 10

    lote_id, staging_dir = LOTE_MANAGER.criar_staging()
    entradas: List[tuple[str, Path, str]] = []
    total_bytes_lote = 0

    try:
        for idx, uf in enumerate(files, 1):
            nome_orig = Path(uf.filename or f"processo_{idx}.pdf").name
            nome_curto = _truncar_nome_arquivo(nome_orig, max_len=80)
            part_path = staging_dir / f"{idx:03d}.part"
            hasher = hashlib.sha256()
            bytes_arq = 0
            try:
                with open(part_path, "wb") as fh:
                    while True:
                        chunk = await uf.read(1024 * 1024)
                        if not chunk:
                            break
                        bytes_arq += len(chunk)
                        total_bytes_lote += len(chunk)
                        if bytes_arq > limite_arquivo_bytes:
                            raise HTTPException(
                                status_code=413,
                                detail=f"O arquivo '{nome_orig}' excede o limite de {limite_mb} MB para PDFs.",
                            )
                        if total_bytes_lote > limite_lote_bytes:
                            raise HTTPException(
                                status_code=413,
                                detail=f"O tamanho total do lote excede o limite de {limite_mb * 10} MB.",
                            )
                        hasher.update(chunk)
                        fh.write(chunk)
            except BaseException:
                if part_path.exists():
                    try:
                        part_path.unlink()
                    except OSError:
                        pass
                raise

            sha_prefix = hasher.hexdigest()[:12]
            staged_path = staging_dir / f"{idx:03d}_{sha_prefix}_{nome_curto}"
            part_path.replace(staged_path)
            entradas.append((nome_orig, staged_path, sha_prefix))

        job = JOBS.criar("lote")
        manifest = LOTE_MANAGER.preparar_lote_de_caminhos(lote_id, entradas, job_id=job["job_id"])
        JOBS.executar_em_thread(
            job["job_id"],
            lambda: LOTE_MANAGER.executar_lote(lote_id, entradas),
            ao_finalizar=INGESTAO_LOCK.release,
        )
        return {
            "status": "accepted",
            "lote_id": lote_id,
            "job_id": job["job_id"],
            "total": manifest["total"],
        }
    except BaseException:
        LOTE_MANAGER.descartar_lote_parcial(lote_id)
        INGESTAO_LOCK.release()
        raise


@app.get("/api/lote")
def api_listar_lotes() -> Dict[str, Any]:
    """Lista os lotes de pré-análise existentes em ``data/lote/``."""
    from app.lote.manager import LOTE_MANAGER

    lotes = LOTE_MANAGER.listar_lotes()
    return {"lotes": lotes, "total": len(lotes)}


@app.get("/api/lote/{lote_id}")
def api_obter_lote(lote_id: str) -> Dict[str, Any]:
    """Retorna progresso (n de N) e resumo por processo de um lote."""
    from app.lote.manager import LOTE_MANAGER

    try:
        lote = LOTE_MANAGER.obter_lote(lote_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if lote is None:
        raise HTTPException(status_code=404, detail=f"Lote '{lote_id}' não encontrado.")
    return lote


@app.post("/api/lote/{lote_id}/cancelar")
def api_cancelar_lote(lote_id: str) -> Dict[str, Any]:
    """Solicita cancelamento de um lote em andamento."""
    from app.lote.manager import LOTE_MANAGER

    try:
        lote = LOTE_MANAGER.cancelar_lote(lote_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if lote is None:
        raise HTTPException(status_code=404, detail=f"Lote '{lote_id}' não encontrado.")
    return lote


@app.post("/api/lote/{lote_id}/processos/{pid}/abrir")
def api_abrir_processo_lote(
    lote_id: str,
    pid: str,
    req: Optional[AbrirProcessoLoteRequest] = Body(default=None),
) -> Dict[str, Any]:
    """Torna um processo pré-analisado do lote o caso ativo pelo mesmo caminho de 'Carregar do disco'."""
    import shutil
    from app.lote.manager import LOTE_MANAGER

    payload = req or AbrirProcessoLoteRequest()
    try:
        pdf_path, dossie = LOTE_MANAGER.localizar_pdf_processo(lote_id, pid)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    with STATE_LOCK:
        producao_ativa = bool(
            ACTIVE_CASE_STATE.get("last_sentence")
            or (ACTIVE_CASE_STATE.get("bundle") and not ACTIVE_CASE_STATE.get("explicit_demo"))
        )
    if producao_ativa and not payload.confirmar_troca:
        raise HTTPException(
            status_code=409,
            detail={
                "erro": "producao_em_curso",
                "detail": "Há produção ou processo ativo em curso. Confirme a troca (confirmar_troca=true) para substituir o caso ativo.",
            },
        )

    if not INGESTAO_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Já existe um carregamento de autos em andamento. Aguarde a conclusão.",
        )
    try:
        cases_dir = _cases_dir()
        cases_dir.mkdir(parents=True, exist_ok=True)
        # Retira PDFs antigos da raiz de data/cases antes de copiar o selecionado (preservando _arquivados)
        for antigo in _pdfs_na_raiz():
            try:
                antigo.unlink()
            except OSError:
                pass
        destino_pdf = cases_dir / pdf_path.name
        shutil.copy2(pdf_path, destino_pdf)

        status_saida: Dict[str, Any] = {}
        bundle = _chamar_compat(
            _load_real_case_from_disk_if_present,
            consentimento_envio_externo=False,
            status_saida=status_saida,
        )
        if bundle is None:
            raise HTTPException(status_code=422, detail="Não foi possível carregar o PDF do processo selecionado.")
        set_active_bundle(bundle, explicit_demo=False, upload_status=status_saida if status_saida else None)
        return {
            "status": "loaded",
            "lote_id": lote_id,
            "pid": pid,
            "dossie": dossie,
            **_serialize_active_bundle(bundle),
        }
    finally:
        INGESTAO_LOCK.release()


@app.post("/api/cases/reset")
def api_reset_case(req: Optional[ResetCaseRequest] = Body(default=None)) -> Dict[str, Any]:
    """Zera a produção (``producao``) ou o caso inteiro (``completo``: arquivamento em zip deduplicado
    ou exclusão definitiva confirmada)."""
    payload = req or ResetCaseRequest()
    if not INGESTAO_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Há um carregamento de autos em andamento. Aguarde antes de reiniciar.")
    try:
        try:
            resultado = reset_session_state(
                mode=payload.mode,
                apagar_exportacoes=payload.apagar_exportacoes,
                excluir_definitivamente=payload.excluir_definitivamente,
                apagar_arquivo_morto=payload.apagar_arquivo_morto,
                confirmar_exclusao_definitiva=payload.confirmar_exclusao_definitiva,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if resultado["mode"] == "completo":
            PROGRESSO.reiniciar()
            # Nenhum dado do caso anterior pode sobreviver: jobs (resultado do upload) e cache de contexto.
            JOBS.limpar()
            limpar_cache_contexto()
    finally:
        INGESTAO_LOCK.release()

    with STATE_LOCK:
        bundle_atual = ACTIVE_CASE_STATE.get("bundle")
    active_case = _serialize_active_bundle(bundle_atual)
    cleared = resultado["cleared"]
    if resultado["mode"] == "completo":
        if cleared.get("exclusao_definitiva"):
            mensagem = (
                "Sessão completamente reiniciada: os arquivos do processo anterior foram EXCLUÍDOS "
                "definitivamente de data/cases (sem arquivamento)."
            )
        elif cleared.get("erro_arquivamento"):
            mensagem = (
                "Sessão reiniciada, mas o arquivamento falhou e os arquivos do processo anterior foram "
                f"mantidos em data/cases: {cleared['erro_arquivamento']}"
            )
        else:
            mensagem = (
                "Sessão completamente reiniciada: autos, mídias e caches do processo anterior foram "
                "compactados em data/cases/_arquivados (sem duplicar conteúdo já arquivado) e as "
                "exportações descartadas. Nenhum dado do caso anterior permanece ativo."
            )
    elif active_case.get("empty"):
        mensagem = "Produção zerada: não havia autos ativos na sessão."
    else:
        mensagem = (
            "Produção zerada: minuta, auditoria, dosimetria, telemetria e exportações "
            "foram descartadas; os autos do processo permanecem carregados."
        )

    return {
        "status": "reset",
        "mode": resultado["mode"],
        "generation_id": resultado["generation_id"],
        "cleared": cleared,
        "active_case": active_case,
        "mensagem": mensagem,
        "last_reset": resultado["last_reset"],
    }


# ==============================================================================
# Exportação
# ==============================================================================

_MEDIA_TYPES_EXPORT = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "html": "text/html; charset=utf-8",
}


def _nome_exportacao(sentenca: Any, formato: str, rascunho: bool) -> str:
    """``Sentença - {tipo} - Proc. {nº} - {sentenciado} - {tipo(s) penal(is)}.{ext}`` (seguro p/ Windows)."""
    return nome_arquivo_sentenca(sentenca, formato, rascunho=rascunho)


def _publicar_exportacao(tmp: Path, destino: Path) -> Path:
    """``os.replace`` com novas tentativas; destino travado (arquivo aberto no Windows) → nome versionado."""
    for tentativa in range(6):
        try:
            os.replace(tmp, destino)
            return destino
        except PermissionError:
            time.sleep(0.05 * (tentativa + 1))
    for n in range(2, 100):
        alternativo = destino.with_name(f"{destino.stem} ({n}){destino.suffix}")
        try:
            os.replace(tmp, alternativo)
            return alternativo
        except PermissionError:
            continue
    raise PermissionError(f"Destino da exportacao travado: {destino.name}")


def _exportar(formato: str, rascunho: bool) -> Any:
    st = obter_estado("last_sentence", "last_audit")
    sentenca, auditoria = st["last_sentence"], st["last_audit"]
    if sentenca is None:
        raise HTTPException(status_code=409, detail="Nenhuma sentença gerada nesta sessão. Gere a minuta antes de exportar.")
    aprovada = _auditoria_aprovada(auditoria)
    if not aprovada and not rascunho:
        return JSONResponse(
            status_code=409,
            content={
                "detail": (
                    "A minuta não foi aprovada pela auditoria (score "
                    f"{getattr(auditoria, 'score_confiabilidade', 0.0)}). Resolva as pendências ou exporte como "
                    "rascunho (?rascunho=true), que leva a marca d'água 'MINUTA NÃO AUDITADA'."
                ),
                "erro": "auditoria_nao_aprovada",
                "score_confiabilidade": getattr(auditoria, "score_confiabilidade", None),
                "marcadores_pendentes": list(getattr(auditoria, "marcadores_pendentes", []) or []),
                "rascunho_disponivel": True,
            },
        )
    marca = None if aprovada else MARCA_DAGUA_NAO_AUDITADA
    exports_dir = _exports_dir()
    exports_dir.mkdir(parents=True, exist_ok=True)
    out_file = exports_dir / _nome_exportacao(sentenca, formato, rascunho=marca is not None)
    if out_file.resolve().parent != exports_dir.resolve():  # defesa em profundidade
        raise HTTPException(status_code=400, detail="Nome de arquivo de exportação inválido.")
    # Gera num temporario no mesmo diretorio e publica com os.replace (com novas tentativas): o
    # destino nunca fica truncado e, se o Word/leitor de PDF o mantiver aberto no Windows, a
    # exportacao cai num nome versionado em vez de 500.
    tmp_file = out_file.with_name(f".{uuid.uuid4().hex}.tmp{out_file.suffix}")
    try:
        if formato == "docx":
            exportar_docx(sentenca, tmp_file, marca_dagua=marca)
        elif formato == "pdf":
            exportar_pdf(sentenca, tmp_file, marca_dagua=marca)
        else:
            tmp_file.write_text(exportar_html_pje(sentenca, marca_dagua=marca), encoding="utf-8")
        out_file = _publicar_exportacao(tmp_file, out_file)
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Não foi possível gravar a exportação ({type(exc).__name__}). Feche o arquivo no Word/leitor de PDF e tente novamente.",
        ) from exc
    finally:
        try:
            tmp_file.unlink(missing_ok=True)
        except OSError:
            pass
    headers = {"X-JVDFM-Marca-Dagua": "1" if marca else "0"}
    cfg = load_settings()
    if (cfg.pasta_exportacao or "").strip():
        # Cópia na pasta do usuário: nunca derruba a exportação (o download segue valendo).
        try:
            copia = copiar_exportacao(out_file, cfg.pasta_exportacao, sentenca.numero_processo, cfg.subpasta_por_processo)
            headers["X-JVDFM-Copia-Em"] = quote(str(copia), safe="")
        except OSError as exc:
            logger.warning("Cópia para a pasta de exportação do usuário falhou: %s", exc)
            headers["X-JVDFM-Copia-Erro"] = quote(exc.strerror or type(exc).__name__, safe="")
    return FileResponse(
        path=str(out_file),
        filename=out_file.name,
        media_type=_MEDIA_TYPES_EXPORT[formato],
        headers=headers,
    )


@app.post("/api/export/abrir-pasta")
def api_abrir_pasta_exportacao() -> Dict[str, Any]:
    """Abre no gerenciador de arquivos a pasta de exportação do usuário (ou ``data/exports``)."""
    cfg = load_settings()
    alvo = Path(cfg.pasta_exportacao) if (cfg.pasta_exportacao or "").strip() else _exports_dir()
    if not alvo.is_dir():
        raise HTTPException(status_code=404, detail="A pasta de exportação não existe (ou o disco não está disponível).")
    try:
        abrir_pasta(alvo)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Não foi possível abrir a pasta: {exc.strerror or type(exc).__name__}") from exc
    return {"pasta": str(alvo)}


class _RevisaoRequest(BaseModel):
    texto_atual: Optional[str] = None  # texto da folha (com edições não salvas); vazio = última minuta


@app.post("/api/sentence/review")
def api_revisar_minuta(req: _RevisaoRequest) -> Dict[str, Any]:
    """Painel de revisão: pendências do laudo + conferência pelo art. 381 do CPP e pelo manual."""
    st = obter_estado("last_sentence", "last_audit")
    sentenca = st["last_sentence"]
    if sentenca is None:
        raise HTTPException(status_code=409, detail="Nenhuma sentença gerada nesta sessão.")
    texto = req.texto_atual if (req.texto_atual or "").strip() else sentenca.texto_completo
    return montar_revisao(texto, reu=getattr(sentenca, "reu", ""), auditoria=st["last_audit"], sentenca=sentenca)


@app.get("/api/export/docx")
def api_export_docx(rascunho: bool = Query(False)) -> Any:
    return _exportar("docx", rascunho)


@app.get("/api/export/pdf")
def api_export_pdf(rascunho: bool = Query(False)) -> Any:
    return _exportar("pdf", rascunho)


@app.get("/api/export/html")
def api_export_html(rascunho: bool = Query(False)) -> Any:
    return _exportar("html", rascunho)


# ==============================================================================
# Base de conhecimento e estilo
# ==============================================================================


@app.get("/api/knowledge")
def api_get_knowledge(categoria: str | None = None, q: str | None = None) -> Dict[str, Any]:
    return {
        "knowledge": get_all_knowledge_base(categoria=categoria, busca=q),
        "style_profile": get_style_profile(),
        "penal_catalog": list_all_penal_types(),
    }


@app.post("/api/knowledge/precedente")
def api_add_precedente(req: CustomPrecedenteRequest) -> Any:
    try:
        novo = add_custom_precedente(
            req.referencia,
            req.titulo,
            req.texto_literal,
            req.tipo,
            tribunal=req.tribunal,
            materia=req.materia,
        )
    except PrecedenteDuplicadoError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc),
                "origem": exc.origem,
                "item_existente": exc.item_existente,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "added", "item": novo, "knowledge": get_all_knowledge_base()}


@app.put("/api/knowledge/precedente/{item_id}")
def api_update_precedente(item_id: str, req: UpdatePrecedenteRequest) -> Any:
    try:
        atualizado = update_custom_precedente(
            item_id,
            referencia=req.referencia,
            titulo=req.titulo,
            texto_literal=req.texto_literal,
            tipo=req.tipo,
            tribunal=req.tribunal,
            materia=req.materia,
        )
    except PrecedenteOficialReadOnlyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PrecedenteNaoEncontradoError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PrecedenteDuplicadoError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc),
                "origem": exc.origem,
                "item_existente": exc.item_existente,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "updated", "item": atualizado, "knowledge": get_all_knowledge_base()}


@app.delete("/api/knowledge/precedente/{item_id}")
def api_delete_precedente(item_id: str) -> Dict[str, Any]:
    with STATE_LOCK:
        versoes = [dict(v) for v in (ACTIVE_CASE_STATE.get("historico_versoes") or [])]
    try:
        resultado = delete_custom_precedente(item_id, versoes_salvas=versoes)
    except PrecedenteOficialReadOnlyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PrecedenteNaoEncontradoError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "deleted",
        "item": resultado["removido"],
        "citado_em_versoes": resultado["citado_em_versoes"],
        "aviso": resultado["aviso"],
        "knowledge": get_all_knowledge_base(),
    }


@app.get("/api/knowledge/gabinete/exportar")
def api_exportar_gabinete() -> Dict[str, Any]:
    return exportar_precedentes_gabinete()


@app.post("/api/knowledge/gabinete/importar")
def api_importar_gabinete(req: ImportarGabineteRequest) -> Dict[str, Any]:
    try:
        resultado = importar_precedentes_gabinete(
            {"formato_versao": req.formato_versao, "itens": req.itens},
            confirmar=req.confirmar,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if resultado.get("gravado"):
        resultado["knowledge"] = get_all_knowledge_base()
    return resultado


@app.post("/api/knowledge/style-sample")
def api_add_style_sample(req: StyleSampleRequest) -> Dict[str, Any]:
    try:
        profile = register_cabinet_sentence_sample(req.titulo, req.tipo, req.texto_amostra)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "added", "style_profile": profile}


# ==============================================================================
# Gemini (opcional, com consentimento) e edição direta
# ==============================================================================


@app.get("/api/gemini/modelos")
def api_gemini_modelos() -> Dict[str, Any]:
    """Lista os modelos Gemini disponíveis na conta do usuário sem enviar nenhum dado do processo."""
    from app.generation.gemini_integration import listar_modelos_disponiveis_gemini

    return listar_modelos_disponiveis_gemini()


@app.post("/api/gemini/build-script")
def api_gemini_build_script(req: Optional[BuildGeminiScriptRequest] = Body(default=None)) -> Dict[str, Any]:
    """Script Estruturado do Caso (Markdown + JSON) para o Gemini, com pseudonimização reversível.
    Caso real exige veredicto por crime (422). O mapa de reversão fica guardado na sessão."""
    payload = req or BuildGeminiScriptRequest()
    bundle = _require_active_bundle()
    script_obj = gerar_script_estruturado_caso(
        case_bundle=bundle,
        custom_dosimetria_override=payload.custom_dosimetria_override,
        pseudonimizar=payload.pseudonimizar,
        densidade=payload.densidade,
    )
    if getattr(script_obj, "mapa_reversao", None):
        with STATE_LOCK:
            if ACTIVE_CASE_STATE.get("bundle") is bundle:
                ACTIVE_CASE_STATE["gemini_script_mapa"] = {
                    "fingerprint": obter_contexto(bundle).fingerprint,
                    "mapa_reversao": dict(script_obj.mapa_reversao),
                    "mapa_hash": getattr(script_obj, "mapa_hash", "") or "",
                }
    return {"status": "ok", "script": script_obj.model_dump(mode="json")}


@app.post("/api/gemini/generate")
def api_gemini_generate(req: Optional[GenerateGeminiSentenceRequest] = Body(default=None)) -> Dict[str, Any]:
    """Geração via API Gemini (somente com ``permitir_envio_externo`` + ``consentimento_envio_externo``;
    senão 403) ou, sem chave configurada, síntese local determinística rotulada como tal."""
    payload = req or GenerateGeminiSentenceRequest()
    bundle = _require_active_bundle()
    sentenca, auditoria, script_obj, telemetria = gerar_sentenca_via_gemini_api(
        case_bundle=bundle,
        api_key=payload.api_key,
        model_name=payload.model,
        pseudonimizar=payload.pseudonimizar,
        densidade=payload.densidade,
        custom_dosimetria_override=payload.custom_dosimetria_override,
        consentimento_envio_externo=payload.consentimento_envio_externo,
        auto_blindar_auditoria=payload.auto_blindar_auditoria,
    )
    generation_id = _registrar_se_caso_ativo(
        bundle, sentenca=sentenca, auditoria=auditoria, origem="gemini", pipeline_engine=None, pipeline_snapshot=None
    )
    return _payload_geracao(
        sentenca,
        auditoria,
        generation_id,
        status="completed",
        script=script_obj.model_dump(mode="json"),
        telemetria=telemetria,
    )


def _mapa_guardado(bundle: Dict[str, Any]) -> tuple[Optional[Dict[str, str]], Optional[str]]:
    with STATE_LOCK:
        guardado = ACTIVE_CASE_STATE.get("gemini_script_mapa")
    if isinstance(guardado, dict) and guardado.get("fingerprint") == obter_contexto(bundle).fingerprint:
        return guardado.get("mapa_reversao") or None, guardado.get("mapa_hash") or None
    return None, None


@app.post("/api/gemini/import-sentence")
def api_gemini_import_sentence(req: ImportExternalSentenceRequest) -> Dict[str, Any]:
    """Importa a sentença redigida no Gemini Web/Pro, desfaz a pseudonimização e audita.
    Auto-blindagem só quando solicitada (e sinalizada)."""
    if not (req.texto_sentenca or "").strip():
        raise HTTPException(status_code=422, detail="Cole o texto da sentença gerada pelo Gemini antes de auditar e formatar.")
    bundle = _require_active_bundle()
    mapa, mapa_hash = (req.mapa_reversao, req.mapa_hash) if req.mapa_reversao else _mapa_guardado(bundle)
    try:
        sentenca, auditoria, telemetria = importar_e_auditar_sentenca_externa(
            case_bundle=bundle,
            texto_sentenca_externa=req.texto_sentenca,
            custom_dosimetria_override=req.custom_dosimetria_override,
            auto_blindar_auditoria=req.auto_blindar_auditoria,
            mapa_reversao=mapa,
            mapa_hash=mapa_hash,
        )
    except ValueError as exc:
        if eh_erro_de_dominio(exc):
            raise
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    generation_id = _registrar_se_caso_ativo(
        bundle, sentenca=sentenca, auditoria=auditoria, origem="importacao_externa", pipeline_engine=None, pipeline_snapshot=None
    )
    return _payload_geracao(sentenca, auditoria, generation_id, status="imported", telemetria=telemetria)


@app.post("/api/sentence/update-and-audit")
def api_update_and_audit_sentence(req: UpdateAndAuditSentenceRequest) -> Dict[str, Any]:
    """Edição direta na folha: re-extrai as seções, reaudita e atualiza o estado ativo."""
    if not (req.texto_completo or "").strip():
        raise HTTPException(status_code=422, detail="O texto da sentença editada não pode ficar vazio.")
    bundle = _require_active_bundle()
    try:
        sentenca, auditoria, telemetria = importar_e_auditar_sentenca_externa(
            case_bundle=bundle,
            texto_sentenca_externa=req.texto_completo,
            custom_dosimetria_override=req.custom_dosimetria_override,
            auto_blindar_auditoria=False,
            origem_label="Edição Direta em Gabinete (Re-Auditada 4 Camadas)",
        )
    except ValueError as exc:
        if eh_erro_de_dominio(exc):
            raise
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # A re-auditoria reconstrói a sentença a partir do texto e perde o veredicto por crime; ele não muda
    # ao editar o texto e alimenta o nome do arquivo (tipo da sentença e tipos penais).
    anterior = obter_estado("last_sentence")["last_sentence"]
    if anterior is not None and not sentenca.decisoes_crimes and anterior.numero_processo == sentenca.numero_processo:
        sentenca.decisoes_crimes = list(anterior.decisoes_crimes)
    # A auditoria roda sobre o texto; a formatação (negrito, alinhamento, recuos, margens) é preservada
    # para a exportação exatamente como o usuário a deixou na folha.
    if (req.texto_html or "").strip():
        html_limpo = sanitizar_html(req.texto_html)
        if html_limpo:
            sentenca.texto_html = html_limpo
            sentenca.formatacao = normalizar_formatacao(req.formatacao)
            if re.sub(r"\s+", " ", texto_do_html(html_limpo)).strip() != re.sub(r"\s+", " ", sentenca.texto_completo).strip():
                sentenca.alertas = list(sentenca.alertas) + [
                    "O arquivo exportado segue o texto exatamente como está na folha; a auditoria usou a versão "
                    "normalizada (datas por extenso, nomes restaurados). Revise as diferenças antes de assinar."
                ]
    elif req.formatacao:
        sentenca.formatacao = normalizar_formatacao(req.formatacao)
    generation_id = _registrar_se_caso_ativo(
        bundle, sentenca=sentenca, auditoria=auditoria, origem="edicao_direta", pipeline_engine=None, pipeline_snapshot=None
    )
    return _payload_geracao(sentenca, auditoria, generation_id, status="updated", telemetria=telemetria)


class _DiffRequest(BaseModel):
    de: str  # id da versão-base (ex.: "v1")
    para: Optional[str] = None  # id da versão a comparar; vazio = texto atual
    texto_atual: Optional[str] = None  # texto da folha (com edições ainda não salvas), quando "para" é vazio


class _TrechoRequest(BaseModel):
    id: Optional[str] = None
    titulo: str
    texto: str
    atalho: str = ""
    categoria: str = "Geral"


@app.get("/api/sentence/versions")
def api_listar_versoes() -> Dict[str, Any]:
    """Histórico de versões da minuta nesta sessão (mais recente primeiro)."""
    return {"versoes": listar_versoes()}


@app.get("/api/sentence/versions/{versao_id}")
def api_obter_versao(versao_id: str) -> Dict[str, Any]:
    v = obter_versao(versao_id)
    if v is None:
        raise HTTPException(status_code=404, detail="Versão não encontrada.")
    # HTML da folha (padrão forense quando a versão não foi editada na folha)
    from types import SimpleNamespace

    v["texto_html"] = html_da_sentenca(SimpleNamespace(**{k: v[k] for k in ("texto_completo", "texto_html", "formatacao")}))
    v["formatacao"] = normalizar_formatacao(v.get("formatacao"))
    return v


@app.post("/api/sentence/diff")
def api_comparar_versoes(req: _DiffRequest) -> Dict[str, Any]:
    base = obter_versao(req.de)
    if base is None:
        raise HTTPException(status_code=404, detail="Versão-base não encontrada.")
    if req.para:
        outra = obter_versao(req.para)
        if outra is None:
            raise HTTPException(status_code=404, detail="Versão de comparação não encontrada.")
        texto_b = outra["texto_completo"]
    elif req.texto_atual is not None:
        texto_b = req.texto_atual
    else:
        sent = obter_estado("last_sentence")["last_sentence"]
        if sent is None:
            raise HTTPException(status_code=409, detail="Nenhuma sentença gerada nesta sessão.")
        texto_b = sent.texto_completo
    return comparar(base["texto_completo"], texto_b)


@app.get("/api/trechos")
def api_listar_trechos() -> Dict[str, Any]:
    """Banco de trechos do gabinete (exemplos + os do usuário)."""
    return {"trechos": listar_trechos()}


@app.post("/api/trechos")
def api_salvar_trecho(req: _TrechoRequest) -> Dict[str, Any]:
    try:
        trecho = salvar_trecho(req.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"trecho": trecho, "trechos": listar_trechos()}


@app.delete("/api/trechos")
def api_excluir_trecho(id: str = Query(...)) -> Dict[str, Any]:
    if not excluir_trecho(id):
        raise HTTPException(status_code=404, detail="Trecho não encontrado (os de exemplo não podem ser excluídos).")
    return {"trechos": listar_trechos()}


class _ModeloFormatoRequest(BaseModel):
    nome: str
    fonte: Optional[str] = None
    tamanho_pt: Optional[float] = None
    entrelinhas: Optional[float] = None
    recuo_primeira_cm: Optional[float] = None
    margens_cm: Optional[Dict[str, Any]] = None
    numerar_paginas: bool = True


@app.get("/api/formatos")
def api_listar_formatos() -> Dict[str, Any]:
    """Modelos de formatação do gabinete (de fábrica + os salvos pelo usuário)."""
    return {"modelos": listar_modelos()}


@app.post("/api/formatos")
def api_salvar_formato(req: _ModeloFormatoRequest) -> Dict[str, Any]:
    try:
        modelo = salvar_modelo(req.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"modelo": modelo, "modelos": listar_modelos()}


@app.delete("/api/formatos")
def api_excluir_formato(nome: str = Query(...)) -> Dict[str, Any]:
    if not excluir_modelo(nome):
        raise HTTPException(status_code=404, detail="Modelo não encontrado (os de fábrica não podem ser excluídos).")
    return {"modelos": listar_modelos()}


@app.post("/api/sentence/save-format")
def api_save_sentence_format(req: SaveSentenceFormatRequest) -> Dict[str, Any]:
    """Grava a formatação da folha (HTML limpo + margens) sem reexecutar a auditoria.

    Usado ao exportar quando o usuário só mudou a formatação (negrito, alinhamento, margens...).
    Se o texto foi alterado, a interface usa ``/api/sentence/update-and-audit``.
    """
    html_limpo = sanitizar_html(req.texto_html)
    if not html_limpo:
        raise HTTPException(status_code=422, detail="O texto da sentença editada não pode ficar vazio.")
    with STATE_LOCK:
        sentenca = ACTIVE_CASE_STATE.get("last_sentence")
        auditoria = ACTIVE_CASE_STATE.get("last_audit")
        if sentenca is None:
            raise HTTPException(status_code=409, detail="Nenhuma sentença gerada nesta sessão.")
        sentenca.texto_html = html_limpo
        sentenca.formatacao = normalizar_formatacao(req.formatacao)
        aprovada = _auditoria_aprovada(auditoria)
    return {
        "status": "saved",
        "formatacao": sentenca.formatacao,
        "html_pje": exportar_html_pje(sentenca, marca_dagua=None if aprovada else MARCA_DAGUA_NAO_AUDITADA),
    }


# ==============================================================================
# Assistente de primeira execução e Guia do Usuário (TASK-033)
# ==============================================================================


@app.get("/api/assistente/diagnostico")
def api_assistente_diagnostico() -> Dict[str, Any]:
    """Diagnóstico de Ollama, OCR pt-BR e estado do assistente de primeira execução."""
    from app.core.assistente import diagnosticar_assistente

    return diagnosticar_assistente(load_settings())


@app.post("/api/assistente/ollama/pull")
def api_assistente_ollama_pull(req: OllamaPullRequest) -> Dict[str, Any]:
    """Inicia o download do modelo Ollama escolhido (somente sob clique do usuário)."""
    from app.core.assistente import iniciar_download_modelo_ollama

    settings = load_settings()
    try:
        return iniciar_download_modelo_ollama(req.modelo, settings.ollama_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/assistente/ollama/pull-status")
def api_assistente_ollama_pull_status() -> Dict[str, Any]:
    """Progresso atual do download de modelo Ollama iniciado pelo assistente."""
    from app.core.assistente import obter_progresso_pull

    return obter_progresso_pull()


@app.get("/guia-usuario", response_class=HTMLResponse)
def pagina_guia_usuario() -> HTMLResponse:
    """Serve ``docs/GUIA_DO_USUARIO.md`` formatado em HTML local para leitura no navegador."""
    import html as _html

    guia_md = Path(__file__).resolve().parent.parent / "docs" / "GUIA_DO_USUARIO.md"
    if not guia_md.is_file():
        raise HTTPException(status_code=404, detail="Guia do Usuário não encontrado.")
    bruto = guia_md.read_text(encoding="utf-8")
    blocos: List[str] = []
    for linha in bruto.splitlines():
        s = linha.strip()
        if not s:
            continue
        if s.startswith("### "):
            blocos.append(f"<h3>{_html.escape(s[4:])}</h3>")
        elif s.startswith("## "):
            blocos.append(f"<h2>{_html.escape(s[3:])}</h2>")
        elif s.startswith("# "):
            blocos.append(f"<h1>{_html.escape(s[2:])}</h1>")
        elif s.startswith(("- ", "* ")):
            blocos.append(f"<p style='margin:4px 0 4px 16px;'>&bull; {_html.escape(s[2:])}</p>")
        else:
            blocos.append(f"<p>{_html.escape(s)}</p>")
    corpo = "\n".join(blocos)
    pagina = (
        "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
        "<title>Guia do Usuário — SentencIA</title>"
        "<style>body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:820px;"
        "margin:32px auto;padding:0 20px;line-height:1.65;color:#1e293b;background:#f8fafc;}"
        "h1,h2,h3{color:#0f172a;margin-top:24px;}p{margin:8px 0;}</style></head>"
        f"<body>{corpo}</body></html>"
    )
    return HTMLResponse(content=pagina)


# ==============================================================================
# Execução direta
# ==============================================================================


def run() -> None:
    """Inicia o servidor em 127.0.0.1 na porta ``JVDFM_PORT`` (padrão 8000)."""
    import uvicorn

    configurar_logging()
    uvicorn.run(app, host="127.0.0.1", port=get_port(), log_level="info")


if __name__ == "__main__":  # pragma: no cover
    run()
