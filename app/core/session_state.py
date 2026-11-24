"""SentencIA — Gestão autoritativa do estado da sessão local.

Centraliza o dicionário global ``ACTIVE_CASE_STATE`` (mantido como ``dict`` por compatibilidade com
as fixtures E2E) e implementa:

- registro ÚNICO de geração (``register_generation``) usado por todas as rotas que produzem minuta;
- reset de produção/conteúdo, com arquivamento deduplicado por sha256 em
  ``data/cases/_arquivados/<timestamp>.zip`` (+ ``indice_hashes.json``) ou exclusão definitiva
  confirmada;
- locks: ``STATE_LOCK`` (estado) e ``PIPELINE_LOCK`` (mutação do engine seccional).

Chaves canônicas do estado:

- ``bundle``             — autos do processo ativo (``dict``) ou ``None`` quando vazio.
- ``last_sentence``      — última ``SentencaGerada`` produzida.
- ``last_audit``         — último ``RelatorioAuditoriaSentenca``.
- ``explicit_demo``      — ``True`` quando o usuário carregou o caso demo manualmente.
- ``last_dosimetria``    — ``dosimetria_dados`` da última sentença gerada.
- ``pipeline_engine``    — ``SectionalPipelineEngine`` da última geração seccional.
- ``pipeline_snapshot``  — telemetria serializada do engine seccional.
- ``generation_id``      — contador monotônico de gerações/resets da sessão.
- ``explicit_reset``     — ``True`` após reset completo.
- ``last_reset``         — timestamp ISO do último reset.
- ``last_generation_origin`` — origem da última minuta (``monolitica``, ``seccional``, ``gemini``...).
- ``motores_cache``      — ``{fingerprint, motores_py}`` (status dos motores sem recomputar a cada poll).
- ``upload_status``      — status por arquivo do último carregamento.

Regras invioláveis de segurança:

- Remoção/arquivamento de arquivos SÓ dentro de ``CASES_DIR`` (filhos diretos, validados por
  ``Path.resolve()``) e de ``EXPORTS_DIR`` (exportações ``Sentença - ...`` e legado ``Sentenca_JVDFM_*``).
- ``data/knowledge_base/*``, ``data/templates_gabinete/*``, ``data/user_settings.json`` e o cofre de
  segredos NUNCA são tocados pelo reset.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import shutil
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core import config as core_config
from app.core.case_flags import is_demo_or_test_bundle
from app.core.case_manifest import sha256_arquivo, validar_manifesto_caso
from app.core.limpeza_demo import ULTIMO_CASO_FILENAME
from app.export.nomeacao import eh_arquivo_exportacao

logger = logging.getLogger(__name__)

# Diretórios capturados no import — usados para detectar monkeypatch (testes podem sobrescrever
# ``CASES_DIR``/``EXPORTS_DIR`` aqui ou em ``app.core.config``).
CASES_DIR: Path = core_config.CASES_DIR
EXPORTS_DIR: Path = core_config.EXPORTS_DIR
_BOOT_CASES_DIR: Path = Path(CASES_DIR).resolve()
_BOOT_EXPORTS_DIR: Path = Path(EXPORTS_DIR).resolve()

EXPORT_FILE_PREFIX = "Sentenca_JVDFM_"  # prefixo legado reconhecido na limpeza (novos usam "Sentença - ")
MEDIA_SUFFIXES = {".mp4", ".mp3", ".wav", ".ogg", ".mkv", ".m4a"}
PDF_SUFFIXES = {".pdf"}
DOCX_SUFFIXES = {".docx"}
HTML_SUFFIXES = {".html", ".htm"}
PJE_CACHE_SUFFIXES = (".pje_cache_v2.json",)
TRANSCRIPT_CACHE_SUFFIXES = (".transcript_v2.json",)

#: Reset "completo" não destrói autos reais: eles são compactados em
#: ``data/cases/_arquivados/<YYYYMMDD_HHMMSS>.zip`` (arquivo morto recuperável), com
#: deduplicação por sha256 registrada em ``_arquivados/indice_hashes.json``.
ARCHIVE_DIR_NAME = "_arquivados"
ARCHIVE_INDEX_NAME = "indice_hashes.json"
ARCHIVE_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
#: Extensões já comprimidas (gravadas no zip sem recompressão).
_SEM_RECOMPRESSAO = MEDIA_SUFFIXES | {".zip", ".jpg", ".jpeg", ".png"}

VALID_RESET_MODES = ("producao", "completo")

#: Caminhos preservados de forma incondicional pelo reset (apenas documentativo).
PRESERVED_PATHS: List[str] = [
    "data/knowledge_base/*",
    "data/templates_gabinete/*",
    "data/user_settings.json",
    "data/secrets/*",
]

STATE_LOCK = threading.RLock()
#: Serializa a mutação do ``SectionalPipelineEngine`` ativo (regeneração de seção).
PIPELINE_LOCK = threading.Lock()

_UPLOAD_STATUS_VAZIO = {"pdfs_processo": [], "midias_audiencia": []}

#: Estado em memória do processo ativo na sessão local.
#: Mantido global e mutável por compatibilidade com as fixtures E2E.
ACTIVE_CASE_STATE: Dict[str, Any] = {
    # Chaves históricas (compatibilidade E2E) — TASK-031: abertura limpa por padrão (sem demo automática)
    "bundle": None,
    "last_sentence": None,
    "last_audit": None,
    "explicit_demo": False,
    # Reset / anti-poluição / telemetria
    "last_dosimetria": None,
    "pipeline_engine": None,
    "pipeline_snapshot": None,
    "generation_id": 0,
    "explicit_reset": False,
    "last_reset": None,
    "last_generation_origin": None,
    "historico_versoes": [],
    "motores_cache": None,
    "gemini_script_mapa": None,
    "upload_status": dict(_UPLOAD_STATUS_VAZIO),
}


# ----------------------------------------------------------------------------------------------
# Diretórios
# ----------------------------------------------------------------------------------------------
def _resolve_cases_dir() -> Path:
    """Resolve o ``CASES_DIR`` corrente tolerando monkeypatch de testes.

    Precedência: override neste módulo → ``app.core.config.CASES_DIR`` (lido dinamicamente).
    """
    module_override = globals().get("CASES_DIR")
    if module_override is not None and Path(module_override).resolve() != _BOOT_CASES_DIR:
        return Path(module_override)
    return Path(core_config.CASES_DIR)


def resolve_exports_dir() -> Path:
    """Diretório das exportações (``data/exports``), tolerante a monkeypatch.

    Precedência: override neste módulo → ``app.core.config.EXPORTS_DIR`` (se alterado) → quando só
    o ``CASES_DIR`` foi relocado (sandbox de teste), a pasta irmã ``<cases>/../exports`` — nunca a
    pasta real do usuário.
    """
    module_override = globals().get("EXPORTS_DIR")
    if module_override is not None and Path(module_override).resolve() != _BOOT_EXPORTS_DIR:
        return Path(module_override)
    config_exports = Path(core_config.EXPORTS_DIR)
    if config_exports.resolve() != _BOOT_EXPORTS_DIR:
        return config_exports
    cases_dir = _resolve_cases_dir()
    if cases_dir.resolve() != _BOOT_CASES_DIR:
        return cases_dir.parent / "exports"
    return config_exports


def resolve_cases_dir() -> Path:
    """Versão pública de ``_resolve_cases_dir``."""
    return _resolve_cases_dir()


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _safe_unlink(file_path: Path, base_dir: Path) -> bool:
    """Remove ``file_path`` apenas se ele for filho direto de ``base_dir``."""
    try:
        resolved = file_path.resolve()
        resolved_base = base_dir.resolve()
        if resolved.parent != resolved_base:
            return False
        if not resolved.is_file():
            return False
        resolved.unlink()
        return True
    except (OSError, RuntimeError):
        return False


def _is_export_file(name: str) -> bool:
    """Exportação gerada pelo sistema: ``Sentença - ...`` (atual) ou ``Sentenca_JVDFM_*`` (legado)."""
    return eh_arquivo_exportacao(name)


def _classify_removed_file(name: str) -> str:
    """Categoriza um arquivo removido/arquivado para o relatório ``cleared``."""
    lower = name.lower()
    if _is_export_file(name):
        return "exportacoes"
    if lower.endswith(PJE_CACHE_SUFFIXES) or lower.endswith(TRANSCRIPT_CACHE_SUFFIXES):
        return "caches"
    suffix = Path(lower).suffix
    if suffix in MEDIA_SUFFIXES:
        return "midias"
    if suffix in PDF_SUFFIXES:
        return "autos_pdf"
    if suffix in DOCX_SUFFIXES:
        return "documentos"
    return "outros"


def _categorias_vazias() -> Dict[str, List[str]]:
    return {"exportacoes": [], "caches": [], "midias": [], "autos_pdf": [], "documentos": [], "outros": []}


def _listar_arquivos_raiz(diretorio: Path) -> List[Path]:
    if not diretorio.exists():
        return []
    try:
        return sorted(p for p in diretorio.iterdir() if p.is_file() and not p.name.startswith("."))
    except OSError:
        return []


def _delete_case_files(*, only_exports: bool, apagar_exportacoes: bool) -> Dict[str, Any]:
    """Remove arquivos diretos de ``CASES_DIR`` conforme o modo.

    - ``only_exports=True``: remove apenas exportações (``Sentença - ...`` e legado
      ``Sentenca_JVDFM_*``) em docx/pdf/html de ``CASES_DIR`` (legado) e de ``EXPORTS_DIR``.
    - ``only_exports=False``: remove todos os arquivos diretos de ``CASES_DIR`` (exclusão definitiva);
      exportações só quando ``apagar_exportacoes``.

    Nunca remove subdiretórios nem arquivos fora de ``CASES_DIR``/``EXPORTS_DIR``.
    """
    cases_dir = _resolve_cases_dir()
    removed: List[str] = []
    categorias = _categorias_vazias()
    exportacoes_removidas = 0

    for candidate in _listar_arquivos_raiz(cases_dir):
        name = candidate.name
        is_export = _is_export_file(name)
        if only_exports and not (is_export and Path(name.lower()).suffix in PDF_SUFFIXES | DOCX_SUFFIXES | HTML_SUFFIXES):
            continue
        if not only_exports and is_export and not apagar_exportacoes:
            continue
        if _safe_unlink(candidate, cases_dir):
            removed.append(name)
            categorias[_classify_removed_file(name)].append(name)
            if is_export:
                exportacoes_removidas += 1

    if apagar_exportacoes:
        exports_dir = resolve_exports_dir()
        for candidate in _listar_arquivos_raiz(exports_dir):
            if _is_export_file(candidate.name) and _safe_unlink(candidate, exports_dir):
                removed.append(candidate.name)
                categorias["exportacoes"].append(candidate.name)
                exportacoes_removidas += 1

    return {
        "removidos": removed,
        "categorias": {k: v for k, v in categorias.items() if v},
        "total": len(removed),
        "exportacoes_removidas": exportacoes_removidas,
    }


def _display_path(target: Path, base_dir: Path) -> str:
    """Representa ``target`` como caminho relativo a ``base_dir`` quando possível."""
    try:
        return str(Path(target).resolve().relative_to(Path(base_dir).resolve())).replace("\\", "/")
    except ValueError:
        return str(Path(target).resolve())


# ----------------------------------------------------------------------------------------------
# Arquivo morto (zip deduplicado por hash)
# ----------------------------------------------------------------------------------------------
def _archive_root(cases_dir: Path) -> Optional[Path]:
    root = cases_dir / ARCHIVE_DIR_NAME
    try:
        if root.resolve().parent != cases_dir.resolve():
            return None
    except (OSError, RuntimeError):
        return None
    return root


def ler_indice_arquivo_morto(cases_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Índice ``{"versao": 1, "arquivos": {sha256: {nome, zip, tamanho, arquivado_em}}}``."""
    root = _archive_root(cases_dir or _resolve_cases_dir())
    vazio: Dict[str, Any] = {"versao": 1, "arquivos": {}}
    if root is None:
        return vazio
    indice = root / ARCHIVE_INDEX_NAME
    if not indice.exists():
        return vazio
    try:
        dados = json.loads(indice.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Índice do arquivo morto ilegível; a deduplicação recomeça do zero.")
        return vazio
    if not isinstance(dados, dict) or not isinstance(dados.get("arquivos"), dict):
        return vazio
    return dados


def _gravar_indice(root: Path, indice: Dict[str, Any]) -> None:
    destino = root / ARCHIVE_INDEX_NAME
    fd, tmp_name = tempfile.mkstemp(prefix=f".{ARCHIVE_INDEX_NAME}.", suffix=".tmp", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(indice, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp_name, destino)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _nome_zip_livre(root: Path, timestamp: str) -> Path:
    candidato = root / f"{timestamp}.zip"
    sufixo = 2
    while candidato.exists():
        candidato = root / f"{timestamp}_{sufixo}.zip"
        sufixo += 1
    return candidato


def _archive_case_files(*, apagar_exportacoes: bool) -> Dict[str, Any]:
    """Compacta os arquivos do caso anterior em ``_arquivados/<timestamp>.zip`` e os retira da raiz.

    1. Exportações (``Sentença - ...`` e legado ``Sentenca_JVDFM_*``, regeneráveis) são apagadas
       quando ``apagar_exportacoes``; senão são arquivadas como os demais.
    2. Cada arquivo restante da raiz de ``CASES_DIR`` (autos, mídias, caches) tem o sha256 calculado:
       se o mesmo conteúdo já está no arquivo morto (``indice_hashes.json`` + zip existente), ele NÃO é
       gravado de novo (``deduplicados``); senão entra no zip novo.
    3. O zip é gravado em arquivo temporário e renomeado de forma atômica; só então os originais são
       removidos da raiz. Em falha nada é removido (``erro`` preenchido).
    """
    cases_dir = _resolve_cases_dir()
    result: Dict[str, Any] = {
        "movidos": [],
        "deduplicados": [],
        "removidos": [],
        "categorias": _categorias_vazias(),
        "arquivados_em": None,
        "arquivos_movidos": 0,
        "exportacoes_removidas": 0,
        "erro": None,
    }

    # Exportações em data/exports (apagadas quando solicitado; nunca arquivadas).
    if apagar_exportacoes:
        exports_dir = resolve_exports_dir()
        for candidate in _listar_arquivos_raiz(exports_dir):
            if _is_export_file(candidate.name) and _safe_unlink(candidate, exports_dir):
                result["removidos"].append(candidate.name)
                result["exportacoes_removidas"] += 1
                result["categorias"]["exportacoes"].append(candidate.name)

    candidatos = _listar_arquivos_raiz(cases_dir)
    a_arquivar: List[Path] = []
    for candidate in candidatos:
        if _is_export_file(candidate.name) and apagar_exportacoes:
            if _safe_unlink(candidate, cases_dir):
                result["removidos"].append(candidate.name)
                result["exportacoes_removidas"] += 1
                result["categorias"]["exportacoes"].append(candidate.name)
            continue
        a_arquivar.append(candidate)

    if a_arquivar:
        root = _archive_root(cases_dir)
        if root is None:
            result["erro"] = "Diretório de arquivo morto inválido."
        else:
            try:
                root.mkdir(parents=True, exist_ok=True)
                indice = ler_indice_arquivo_morto(cases_dir)
                registros: Dict[str, Any] = indice["arquivos"]
                timestamp = datetime.datetime.now().strftime(ARCHIVE_TIMESTAMP_FORMAT)
                zip_final = _nome_zip_livre(root, timestamp)
                novos: List[tuple[Path, str]] = []
                duplicados: List[Path] = []
                hashes_neste_zip: set[str] = set()
                for arquivo in a_arquivar:
                    sha = sha256_arquivo(arquivo)
                    reg = registros.get(sha)
                    if (reg and (root / str(reg.get("zip", ""))).exists()) or sha in hashes_neste_zip:
                        duplicados.append(arquivo)
                        continue
                    novos.append((arquivo, sha))
                    hashes_neste_zip.add(sha)

                if novos:
                    tmp_zip = root / f".{zip_final.name}.part"
                    try:
                        with zipfile.ZipFile(tmp_zip, "w", allowZip64=True) as zf:
                            for arquivo, _sha in novos:
                                compressao = (
                                    zipfile.ZIP_STORED
                                    if arquivo.suffix.lower() in _SEM_RECOMPRESSAO
                                    else zipfile.ZIP_DEFLATED
                                )
                                zf.write(arquivo, arcname=arquivo.name, compress_type=compressao)
                        os.replace(tmp_zip, zip_final)
                    finally:
                        if tmp_zip.exists():
                            try:
                                tmp_zip.unlink()
                            except OSError:
                                pass
                    agora = _now_iso()
                    for arquivo, sha in novos:
                        registros[sha] = {
                            "nome": arquivo.name,
                            "zip": zip_final.name,
                            "tamanho": arquivo.stat().st_size,
                            "arquivado_em": agora,
                        }
                    _gravar_indice(root, indice)
                    result["arquivados_em"] = _display_path(zip_final, cases_dir.parent)

                # Somente após o zip/índice gravados os originais saem da raiz.
                for arquivo, _sha in novos:
                    if _safe_unlink(arquivo, cases_dir):
                        result["movidos"].append(arquivo.name)
                        result["categorias"][_classify_removed_file(arquivo.name)].append(arquivo.name)
                for arquivo in duplicados:
                    if _safe_unlink(arquivo, cases_dir):
                        result["deduplicados"].append(arquivo.name)
                        result["categorias"][_classify_removed_file(arquivo.name)].append(arquivo.name)
            except (OSError, zipfile.BadZipFile, RuntimeError) as err:
                logger.error("Falha ao arquivar os autos do caso anterior: %s", type(err).__name__)
                result["erro"] = f"Falha ao arquivar os arquivos do caso ({type(err).__name__}); nada foi removido."

    result["arquivos_movidos"] = len(result["movidos"]) + len(result["deduplicados"])
    result["categorias"] = {k: v for k, v in result["categorias"].items() if v}
    return result


def _apagar_arquivo_morto() -> bool:
    cases_dir = _resolve_cases_dir()
    root = _archive_root(cases_dir)
    if root is None or not root.exists():
        return False
    try:
        if not root.resolve().is_relative_to(cases_dir.resolve()):
            return False
        shutil.rmtree(root)
        return True
    except (OSError, RuntimeError) as err:
        logger.error("Falha ao apagar o arquivo morto: %s", type(err).__name__)
        return False


# ----------------------------------------------------------------------------------------------
# Estado
# ----------------------------------------------------------------------------------------------
def obter_estado(*chaves: str) -> Dict[str, Any]:
    """Leitura consistente (sob lock) de várias chaves do estado."""
    with STATE_LOCK:
        if not chaves:
            return dict(ACTIVE_CASE_STATE)
        return {k: ACTIVE_CASE_STATE.get(k) for k in chaves}


def clear_production_state() -> Dict[str, bool]:
    """Limpa minuta/auditoria/dosimetria/engine/snapshot sem tocar em arquivos.

    Usado nas trocas de caso (``load-demo`` / upload / metadados) e internamente pelo reset. Mantém
    ``bundle``/``explicit_demo`` intactos. Retorna quais chaves possuíam valor antes da limpeza.
    """
    with STATE_LOCK:
        cleared = {
            "minuta": ACTIVE_CASE_STATE.get("last_sentence") is not None,
            "auditoria": ACTIVE_CASE_STATE.get("last_audit") is not None,
            "dosimetria": ACTIVE_CASE_STATE.get("last_dosimetria") is not None,
            "engine": ACTIVE_CASE_STATE.get("pipeline_engine") is not None,
            "snapshot": ACTIVE_CASE_STATE.get("pipeline_snapshot") is not None,
        }
        ACTIVE_CASE_STATE["last_sentence"] = None
        ACTIVE_CASE_STATE["last_audit"] = None
        ACTIVE_CASE_STATE["last_dosimetria"] = None
        ACTIVE_CASE_STATE["pipeline_engine"] = None
        ACTIVE_CASE_STATE["pipeline_snapshot"] = None
        ACTIVE_CASE_STATE["last_generation_origin"] = None
        ACTIVE_CASE_STATE["historico_versoes"] = []
        return cleared


# ----------------------------------------------------------------------------------------------
# Histórico de versões da minuta (somente em memória, na sessão)
# ----------------------------------------------------------------------------------------------

HISTORICO_MAXIMO = 30
#: Origens que ACRESCENTAM versão ao histórico; qualquer outra (nova geração) o reinicia.
_ORIGENS_INCREMENTAIS = ("edicao_direta", "secao_regenerada")
_ROTULOS_ORIGEM = {
    "edicao_direta": "Edição salva e re-auditada",
    "secao_regenerada": "Seção regenerada",
    "importacao_externa": "Texto importado (Gemini/colado)",
    "monolitica": "Gerada pelo motor local",
    "seccional": "Gerada seção a seção",
    "gemini": "Gerada com Gemini",
}


def _registrar_versao(sentenca: Any, auditoria: Any, origem: str) -> None:
    """Acrescenta um instantâneo da minuta. Nova geração (≠ edição direta) reinicia o histórico.

    Chamado sob ``STATE_LOCK``. Guarda texto, HTML formatado, formatação e o resultado da auditoria.
    """
    historico: List[Dict[str, Any]] = ACTIVE_CASE_STATE.get("historico_versoes") or []
    if origem not in _ORIGENS_INCREMENTAIS:
        historico = []
    numero = (historico[-1]["numero"] + 1) if historico else 1
    texto = str(getattr(sentenca, "texto_completo", "") or "")
    historico.append(
        {
            "id": f"v{numero}",
            "numero": numero,
            "origem": origem,
            "rotulo": _ROTULOS_ORIGEM.get(origem, f"Gerada ({origem})"),
            "criado_em": _now_iso(),
            "palavras": len(texto.split()),
            "score": getattr(auditoria, "score_confiabilidade", None),
            "aprovada": bool(getattr(auditoria, "aprovada_sem_alucinacao", False)),
            "texto_completo": texto,
            "texto_html": str(getattr(sentenca, "texto_html", "") or ""),
            "formatacao": dict(getattr(sentenca, "formatacao", None) or {}),
        }
    )
    ACTIVE_CASE_STATE["historico_versoes"] = historico[-HISTORICO_MAXIMO:]


def listar_versoes() -> List[Dict[str, Any]]:
    """Metadados das versões (mais recente primeiro), sem os textos."""
    with STATE_LOCK:
        historico = list(ACTIVE_CASE_STATE.get("historico_versoes") or [])
    corpo = ("texto_completo", "texto_html", "formatacao")
    return [{k: v for k, v in item.items() if k not in corpo} for item in reversed(historico)]


def obter_versao(versao_id: str) -> Optional[Dict[str, Any]]:
    with STATE_LOCK:
        for item in ACTIVE_CASE_STATE.get("historico_versoes") or []:
            if item["id"] == versao_id:
                return dict(item)
    return None


def _caminho_ultimo_caso(cases_dir: Optional[Path] = None) -> Path:
    return (cases_dir or _resolve_cases_dir()).parent / ULTIMO_CASO_FILENAME


def _ler_registro_de_casos(destino: Path) -> Dict[str, Any]:
    try:
        dados = json.loads(destino.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


def _manifestos_registrados(destino: Path) -> Dict[str, Any]:
    manifestos = _ler_registro_de_casos(destino).get("manifestos")
    return manifestos if isinstance(manifestos, dict) else {}


def consultar_categorias_pecas(identidade: str, *, cases_dir: Path) -> Dict[str, str]:
    registro = _ler_registro_de_casos(_caminho_ultimo_caso(cases_dir)).get("categorias_pecas")
    categorias = registro.get(identidade) if isinstance(registro, dict) else None
    return categorias if isinstance(categorias, dict) else {}


def consultar_manifesto_caso(identidade: str, *, cases_dir: Path) -> Dict[str, Any]:
    manifesto = _manifestos_registrados(_caminho_ultimo_caso(cases_dir)).get(identidade)
    return manifesto if isinstance(manifesto, dict) else {}


def salvar_ultimo_caso_real(
    bundle: Optional[Dict[str, Any]],
    upload_status: Optional[Dict[str, Any]] = None,
    *,
    cases_dir: Optional[Path] = None,
) -> bool:
    """Persiste o último caso real e os manifestos registrados em um arquivo atômico.

    Nunca persiste demonstração, simulação ou artefato de teste.
    """
    if not isinstance(bundle, dict) or is_demo_or_test_bundle(bundle):
        return False
    destino = _caminho_ultimo_caso(cases_dir)
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        registro = _ler_registro_de_casos(destino)
        manifestos = registro.get("manifestos")
        manifestos = manifestos if isinstance(manifestos, dict) else {}
        categorias = registro.get("categorias_pecas")
        categorias = categorias if isinstance(categorias, dict) else {}
        manifesto = bundle.get("manifesto_caso")
        if isinstance(manifesto, dict):
            identidade = manifesto["id"]
            manifestos[identidade] = manifesto
            escolhas = {
                doc["doc_id"]: doc["categoria_confirmada"]
                for doc in bundle.get("raw_documents") or []
                if isinstance(doc, dict) and isinstance(doc.get("doc_id"), str)
                and isinstance(doc.get("categoria_confirmada"), str)
            }
            if escolhas:
                categorias[identidade] = escolhas
            else:
                categorias.pop(identidade, None)
        payload = {
            "versao": 1,
            "manifestos": manifestos,
            "categorias_pecas": categorias,
            "salvo_em": _now_iso(),
            "bundle": bundle,
            "upload_status": dict(upload_status) if isinstance(upload_status, dict) else dict(_UPLOAD_STATUS_VAZIO),
        }
        fd, tmp_name = tempfile.mkstemp(prefix=".ultimo_caso_", suffix=".tmp", dir=str(destino.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp_name, destino)
        finally:
            if os.path.exists(tmp_name):
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Não foi possível persistir o último caso real: %s", type(exc).__name__)
        return False


def remover_ultimo_caso_real() -> bool:
    """Remove ``.ultimo_caso_real.json`` quando existir."""
    destino = _caminho_ultimo_caso()
    try:
        if destino.is_file():
            destino.unlink()
            return True
    except OSError:
        pass
    return False


def restaurar_ultimo_caso_real() -> bool:
    """Restaura o último caso REAL persistido na inicialização.

    Se não houver arquivo, ou se o bundle persistido for demo/teste/simulação, não restaura e
    garante abertura limpa (``bundle = None``).
    """
    destino = _caminho_ultimo_caso()
    bundle_valido: Optional[Dict[str, Any]] = None
    upload_status_valido: Dict[str, Any] = dict(_UPLOAD_STATUS_VAZIO)

    if destino.is_file():
        try:
            dados = json.loads(destino.read_text(encoding="utf-8"))
            candidato = dados.get("bundle") if isinstance(dados, dict) else None
            if isinstance(candidato, dict) and not is_demo_or_test_bundle(candidato):
                if "manifesto_caso" in candidato:
                    validar_manifesto_caso(_resolve_cases_dir(), candidato["manifesto_caso"])
                bundle_valido = candidato
                st_up = dados.get("upload_status")
                if isinstance(st_up, dict):
                    upload_status_valido = st_up
            else:
                logger.info("Estado persistido ignorado na inicialização (demo/teste/inválido); abertura limpa.")
        except (OSError, ValueError, RuntimeError):
            logger.warning("Último caso ilegível ou manifesto inválido na inicialização; abertura limpa.")

    with STATE_LOCK:
        if bundle_valido is not None:
            clear_production_state()
            ACTIVE_CASE_STATE["bundle"] = bundle_valido
            ACTIVE_CASE_STATE["explicit_demo"] = False
            ACTIVE_CASE_STATE["explicit_reset"] = False
            ACTIVE_CASE_STATE["motores_cache"] = None
            ACTIVE_CASE_STATE["gemini_script_mapa"] = None
            ACTIVE_CASE_STATE["upload_status"] = upload_status_valido
            return True
        if destino.is_file() and is_demo_or_test_bundle(ACTIVE_CASE_STATE.get("bundle")):
            clear_production_state()
            ACTIVE_CASE_STATE["bundle"] = None
            ACTIVE_CASE_STATE["explicit_demo"] = False
            ACTIVE_CASE_STATE["upload_status"] = dict(_UPLOAD_STATUS_VAZIO)
        return False


def set_active_bundle(
    bundle: Optional[Dict[str, Any]],
    *,
    explicit_demo: bool,
    upload_status: Optional[Dict[str, Any]] = None,
    limpar_producao: bool = True,
    cases_dir: Optional[Path] = None,
) -> None:
    """Valida e persiste o manifesto antes de publicar um caso preparado."""
    persistir = bundle is not None and not explicit_demo and not is_demo_or_test_bundle(bundle)
    preparado = persistir and "manifesto_caso" in bundle
    if preparado:
        validar_manifesto_caso(cases_dir or _resolve_cases_dir(), bundle["manifesto_caso"])
    with STATE_LOCK:
        status_para_salvar = dict(
            upload_status if upload_status is not None else ACTIVE_CASE_STATE.get("upload_status") or _UPLOAD_STATUS_VAZIO
        )
        if preparado and not salvar_ultimo_caso_real(bundle, status_para_salvar, cases_dir=cases_dir):
            raise OSError("Não foi possível persistir o caso preparado.")
        if limpar_producao:
            clear_production_state()
        ACTIVE_CASE_STATE["bundle"] = bundle
        ACTIVE_CASE_STATE["explicit_demo"] = bool(explicit_demo)
        ACTIVE_CASE_STATE["explicit_reset"] = False
        ACTIVE_CASE_STATE["motores_cache"] = None
        ACTIVE_CASE_STATE["gemini_script_mapa"] = None
        if upload_status is not None:
            ACTIVE_CASE_STATE["upload_status"] = upload_status
    if persistir and not preparado:
        salvar_ultimo_caso_real(bundle, status_para_salvar)


def register_generation(
    sentenca: Any,
    auditoria: Any,
    *,
    origem: str,
    pipeline_engine: Any = None,
    pipeline_snapshot: Optional[Dict[str, Any]] = None,
    bundle: Optional[Dict[str, Any]] = None,
    explicit_demo: Optional[bool] = None,
) -> int:
    """Bloco ÚNICO de registro de geração (todas as rotas que produzem minuta usam este).

    Persiste sentença, auditoria, dosimetria, engine/snapshot (``None`` para gerações que não são
    seccionais — a geração monolítica invalida o engine anterior) e incrementa ``generation_id``.
    Quando ``bundle`` é informado, ele passa a ser o caso ativo (``explicit_demo`` opcional).
    """
    with STATE_LOCK:
        if bundle is not None:
            if bundle is not ACTIVE_CASE_STATE.get("bundle"):
                ACTIVE_CASE_STATE["motores_cache"] = None
            ACTIVE_CASE_STATE["bundle"] = bundle
            if explicit_demo is not None:
                ACTIVE_CASE_STATE["explicit_demo"] = bool(explicit_demo)
        sentenca.revisao_documento = uuid.uuid4().hex
        ACTIVE_CASE_STATE["last_sentence"] = sentenca
        ACTIVE_CASE_STATE["last_audit"] = auditoria
        ACTIVE_CASE_STATE["last_dosimetria"] = getattr(sentenca, "dosimetria_dados", None)
        ACTIVE_CASE_STATE["pipeline_engine"] = pipeline_engine
        ACTIVE_CASE_STATE["pipeline_snapshot"] = pipeline_snapshot
        ACTIVE_CASE_STATE["last_generation_origin"] = origem
        ACTIVE_CASE_STATE["explicit_reset"] = False
        generation_id = int(ACTIVE_CASE_STATE.get("generation_id") or 0) + 1
        ACTIVE_CASE_STATE["generation_id"] = generation_id
        _registrar_versao(sentenca, auditoria, origem)
        return generation_id


def update_regenerated_section(sentenca: Any, auditoria: Any, pipeline_engine: Any, pipeline_snapshot: Any) -> int:
    """Registra a regeneração atômica de seção (mesmo ``generation_id``: é a mesma minuta)."""
    with STATE_LOCK:
        sentenca.revisao_documento = uuid.uuid4().hex
        ACTIVE_CASE_STATE["last_sentence"] = sentenca
        ACTIVE_CASE_STATE["last_audit"] = auditoria
        ACTIVE_CASE_STATE["last_dosimetria"] = getattr(sentenca, "dosimetria_dados", None)
        ACTIVE_CASE_STATE["pipeline_engine"] = pipeline_engine
        ACTIVE_CASE_STATE["pipeline_snapshot"] = pipeline_snapshot
        _registrar_versao(sentenca, auditoria, "secao_regenerada")
        return int(ACTIVE_CASE_STATE.get("generation_id") or 0)


def reset_session_state(
    mode: str = "producao",
    apagar_exportacoes: bool = True,
    *,
    excluir_definitivamente: bool = False,
    apagar_arquivo_morto: bool = False,
    confirmar_exclusao_definitiva: bool = False,
) -> Dict[str, Any]:
    """Executa o reset autoritativo da sessão local.

    ``"producao"`` — limpa minuta, auditoria, dosimetria, engine, snapshot e (se
    ``apagar_exportacoes``) as exportações (``Sentença - ...`` e legado ``Sentenca_JVDFM_*``), MANTENDO os autos.

    ``"completo"`` — tudo do modo produção e retira da raiz de ``CASES_DIR`` os arquivos do caso:
    por padrão compacta-os em ``_arquivados/<timestamp>.zip`` (deduplicado por sha256); com
    ``excluir_definitivamente=True`` + ``confirmar_exclusao_definitiva=True`` apaga-os sem arquivar
    (``apagar_arquivo_morto=True`` apaga também ``_arquivados``). Zera ``bundle``/``explicit_demo`` e
    marca ``explicit_reset=True``.

    Levanta ``ValueError`` para modo inválido ou exclusão definitiva sem confirmação.
    """
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in VALID_RESET_MODES:
        raise ValueError(f"Modo de reset inválido: {mode!r}. Use 'producao' ou 'completo'.")
    destrutivo = excluir_definitivamente or apagar_arquivo_morto
    if destrutivo and normalized_mode != "completo":
        raise ValueError("A exclusão definitiva só é permitida no reset 'completo'.")
    if destrutivo and confirmar_exclusao_definitiva is not True:
        raise ValueError(
            "Exclusão definitiva exige confirmação explícita (confirmar_exclusao_definitiva=true). "
            "Os arquivos apagados não poderão ser recuperados."
        )

    # I/O pesado (sha256 + zip de midias de varios GB, exclusao) FORA do STATE_LOCK: as rotas que leem
    # o estado (/api/status, /api/cases/active...) nao podem ficar presas durante o arquivamento. A
    # exclusividade com a ingestao e garantida pelo INGESTAO_LOCK de quem chama a rota.
    archived: Dict[str, Any] = {}
    excluidos_definitivamente: List[str] = []
    arquivo_morto_apagado = False
    if normalized_mode == "completo":
        if excluir_definitivamente:
            removed_total = _delete_case_files(only_exports=False, apagar_exportacoes=True)
            archived = {
                "arquivados_em": None,
                "arquivos_movidos": 0,
                "movidos": [],
                "deduplicados": [],
                "exportacoes_removidas": removed_total["exportacoes_removidas"],
                "categorias": removed_total["categorias"],
                "erro": None,
            }
            excluidos_definitivamente = removed_total["removidos"]
        else:
            archived = _archive_case_files(apagar_exportacoes=apagar_exportacoes)
        try:
            from app.lote.manager import arquivar_lotes_no_reset

            arquivar_lotes_no_reset(
                _archive_root(_resolve_cases_dir()),
                excluir_definitivamente=excluir_definitivamente,
            )
        except Exception as exc:
            logger.warning("Falha ao arquivar data/lote no reset completo: %s", exc)
        arquivo_morto_apagado = _apagar_arquivo_morto() if apagar_arquivo_morto else False
        remover_ultimo_caso_real()
    elif apagar_exportacoes:
        removed = _delete_case_files(only_exports=True, apagar_exportacoes=True)
    else:
        removed = {"removidos": [], "categorias": {}, "total": 0, "exportacoes_removidas": 0}

    with STATE_LOCK:
        cleared: Dict[str, Any] = clear_production_state()

        if normalized_mode == "completo":
            had_bundle = ACTIVE_CASE_STATE.get("bundle") is not None
            cleared["arquivos_excluidos_definitivamente"] = excluidos_definitivamente
            cleared["arquivo_morto_apagado"] = arquivo_morto_apagado
            cleared["bundle"] = had_bundle
            cleared["explicit_demo"] = bool(ACTIVE_CASE_STATE.get("explicit_demo"))
            ACTIVE_CASE_STATE["bundle"] = None
            ACTIVE_CASE_STATE["explicit_demo"] = False
            ACTIVE_CASE_STATE["explicit_reset"] = True
            ACTIVE_CASE_STATE["motores_cache"] = None
            ACTIVE_CASE_STATE["gemini_script_mapa"] = None
            ACTIVE_CASE_STATE["upload_status"] = dict(_UPLOAD_STATUS_VAZIO)
            cleared["arquivados_em"] = archived["arquivados_em"]
            cleared["arquivos_movidos"] = archived["arquivos_movidos"]
            cleared["arquivos_movidos_lista"] = archived["movidos"]
            cleared["arquivos_deduplicados"] = archived["deduplicados"]
            cleared["exportacoes_removidas"] = archived["exportacoes_removidas"]
            cleared["categorias_arquivadas"] = archived["categorias"]
            cleared["arquivos_removidos"] = archived["exportacoes_removidas"] + len(
                cleared["arquivos_excluidos_definitivamente"]
            )
            cleared["erro_arquivamento"] = archived["erro"]
            cleared["exclusao_definitiva"] = bool(excluir_definitivamente)
        else:
            cleared["bundle"] = False
            cleared["explicit_demo"] = False
            ACTIVE_CASE_STATE["explicit_reset"] = False
            cleared["arquivados_em"] = None
            cleared["arquivos_movidos"] = 0
            cleared["arquivos_movidos_lista"] = []
            cleared["arquivos_deduplicados"] = []
            cleared["exportacoes_removidas"] = removed["exportacoes_removidas"]
            cleared["exportacoes_removidas_lista"] = removed["removidos"]
            cleared["categorias_arquivadas"] = removed["categorias"]
            cleared["arquivos_removidos"] = removed["total"]
            cleared["erro_arquivamento"] = None
            cleared["exclusao_definitiva"] = False

        cleared["preservados"] = list(PRESERVED_PATHS)
        generation_id = int(ACTIVE_CASE_STATE.get("generation_id") or 0) + 1
        ACTIVE_CASE_STATE["generation_id"] = generation_id
        timestamp = _now_iso()
        ACTIVE_CASE_STATE["last_reset"] = timestamp

        return {
            "mode": normalized_mode,
            "generation_id": generation_id,
            "last_reset": timestamp,
            "cleared": cleared,
        }


def session_health() -> Dict[str, Any]:
    """Resumo do estado da sessão (sem dados do processo), exposto em ``GET /api/status``."""
    with STATE_LOCK:
        bundle = ACTIVE_CASE_STATE.get("bundle")
        return {
            "generation_id": int(ACTIVE_CASE_STATE.get("generation_id") or 0),
            "case_loaded": bundle is not None,
            "is_demo": bool(isinstance(bundle, dict) and bundle.get("is_demo") is True),
            "has_sentence": ACTIVE_CASE_STATE.get("last_sentence") is not None,
            "last_reset": ACTIVE_CASE_STATE.get("last_reset"),
        }
