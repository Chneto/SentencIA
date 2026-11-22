# -*- coding: utf-8 -*-
"""Motor de pré-análise determinística de processos em lote (Fase 5 / TASK-016).

Regras:
- 100% local (zero chamadas externas, zero geração de sentença).
- Nunca altera ``ACTIVE_CASE_STATE`` durante o processamento do lote.
- Concorrência 1 (fila sequencial respeitando ``INGESTAO_LOCK``).
- PDF corrompido marca apenas aquele processo como ``falhou`` e o lote continua.
- Cada processo gera ``data/lote/<lote_id>/<pid>/dossie.json`` sem inventar dados.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
import shutil
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core import config as app_config
from app.core.case_context import obter_contexto
from app.ingestion.pdf_ingestor import segmentar_autos_pdf_pje

logger = logging.getLogger(__name__)

_ID_SEGURO_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
MAX_ARQUIVOS_LOTE = 50
_CAMPOS_ESSENCIAIS = (
    "numero_processo",
    "nome_ofendida",
    "nome_reu",
    "data_fato",
    "data_recebimento_denuncia",
    "crimes_imputados",
)


def _agora_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def diretorio_lotes() -> Path:
    """Resolve ``data/lote`` respeitando ``JVDFM_DATA_DIR`` / ``app.core.config.DATA_DIR``."""
    base = Path(app_config.DATA_DIR)
    destino = base / "lote"
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def _sanitizar_pid(numero_processo: str, sha_prefix: str) -> str:
    limpo = re.sub(r"[^0-9A-Za-z._-]+", "_", (numero_processo or "").strip()).strip("_")
    if limpo and _ID_SEGURO_RE.match(limpo):
        return limpo
    return f"proc_{sha_prefix}"


def _validar_segmento(valor: str, nome: str) -> str:
    v = (valor or "").strip()
    if not v or not _ID_SEGURO_RE.match(v) or ".." in v or "/" in v or "\\" in v:
        raise ValueError(f"Identificador inválido para {nome}: {valor!r}")
    return v


def _truncar_nome_arquivo(nome: str, max_len: int = 80) -> str:
    """Limita o nome do arquivo em disco para evitar estouro de MAX_PATH no Windows,
    preservando unicidade com sufixo de hash curto quando ocorre truncamento."""
    base = Path(nome or "processo.pdf").name
    if len(base) <= max_len:
        return base
    stem = Path(base).stem
    ext = Path(base).suffix or ".pdf"
    sufixo_hash = hashlib.sha256(base.encode("utf-8")).hexdigest()[:8]
    corte = max(4, max_len - len(ext) - len(sufixo_hash) - 1)
    return f"{stem[:corte]}_{sufixo_hash}{ext}"


def _gravar_json_atomico(caminho: Path, dados: Dict[str, Any]) -> None:
    import time

    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(caminho.suffix + ".tmp")
    conteudo = json.dumps(dados, ensure_ascii=False, indent=2)
    tmp.write_text(conteudo, encoding="utf-8")
    for tentativa in range(6):
        try:
            tmp.replace(caminho)
            return
        except PermissionError:
            if tentativa == 5:
                raise
            time.sleep(0.02 * (tentativa + 1))


def _alertas_prescricao_dossie(meta: Any) -> List[str]:
    """Calcula alertas determinísticos de prescrição quando há data do fato e crimes imputados."""
    crimes = list(getattr(meta, "crimes_imputados", None) or [])
    data_fato = getattr(meta, "data_fato", None)
    if not crimes or not data_fato:
        return []
    try:
        from app.legal_engine.dosimetria_calculator import calcular_prescricao_abstrata

        alertas: List[str] = []
        for code in crimes:
            res = calcular_prescricao_abstrata(
                crime_code=str(code),
                data_fato=str(data_fato),
                data_recebimento_denuncia=getattr(meta, "data_recebimento_denuncia", None),
            )
            if isinstance(res, dict) and res.get("alerta"):
                alertas.append(str(res["alerta"]))
        return alertas
    except Exception:
        # Fallback determinístico conservador sem inventar prazos
        return []


def analisar_pdf_para_dossie(
    pdf_path: Path,
    *,
    lote_id: str,
    arquivo_original: str,
    sha_prefix: str,
    destino_lote: Path,
) -> Dict[str, Any]:
    """Executa a pré-análise determinística de um único PDF sem tocar no caso ativo."""
    raw_docs = segmentar_autos_pdf_pje(pdf_path)
    if not raw_docs:
        raise ValueError("Nenhuma peça/página legível extraída do PDF.")

    bundle_local: Dict[str, Any] = {
        "raw_documents": raw_docs,
        "transcriptions": [],
        "is_demo": False,
    }
    ctx = obter_contexto(bundle_local)
    meta = ctx.meta
    pecas = ctx.pecas

    numero_processo = str(getattr(meta, "numero_processo", "") or "").strip()
    pid = _sanitizar_pid(numero_processo, sha_prefix)

    # Evita colisão se dois PDFs no mesmo lote tiverem o mesmo número
    proc_dir = destino_lote / pid
    if proc_dir.exists():
        pid = f"{pid}_{sha_prefix[:6]}"
        proc_dir = destino_lote / pid
    proc_dir.mkdir(parents=True, exist_ok=True)

    pdf_destino = proc_dir / (Path(arquivo_original).name or f"{pid}.pdf")
    shutil.copy2(pdf_path, pdf_destino)

    pendencias: List[str] = []
    for campo in _CAMPOS_ESSENCIAIS:
        val = getattr(meta, campo, None)
        if not val:
            pendencias.append(campo)

    pecas_indexadas = [
        {
            "doc_id": str(getattr(p, "doc_id", "") or ""),
            "titulo": str(getattr(p, "titulo", "") or ""),
            "categoria": str(getattr(p, "categoria", "") or ""),
            "data_peca": str(getattr(p, "data_peca", "") or ""),
        }
        for p in pecas
    ]
    teses = [
        {
            "codigo": str(getattr(t, "codigo", "") or ""),
            "titulo": str(getattr(t, "titulo", "") or ""),
            "categoria": str(getattr(t, "categoria", "") or ""),
        }
        for t in (getattr(meta, "teses_defensivas", None) or [])
    ]
    tipos_penais = [str(c) for c in (getattr(meta, "crimes_imputados", None) or [])]
    alertas_extracao = [str(a) for a in (getattr(meta, "alertas_extracao", None) or [])]
    alertas_prescricao = _alertas_prescricao_dossie(meta)

    status = "pendencias" if pendencias else "pronto"
    dossie: Dict[str, Any] = {
        "pid": pid,
        "lote_id": lote_id,
        "arquivo_original": Path(arquivo_original).name,
        "pdf_nome": pdf_destino.name,
        "numero_processo": numero_processo or None,
        "pecas_indexadas": pecas_indexadas,
        "total_pecas": len(pecas_indexadas),
        "tipos_penais": tipos_penais,
        "teses_defensivas": teses,
        "pendencias": pendencias,
        "alertas_prescricao": alertas_prescricao,
        "alertas_extracao": alertas_extracao,
        "status": status,
        "motivo": None,
        "processado_em": _agora_iso(),
    }
    _gravar_json_atomico(proc_dir / "dossie.json", dossie)
    return dossie


class LoteManager:
    """Gerencia lotes de pré-análise em ``data/lote/`` com concorrência 1."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cancel_events: Dict[str, threading.Event] = {}

    def _manifest_path(self, lote_id: str) -> Path:
        lid = _validar_segmento(lote_id, "lote_id")
        return diretorio_lotes() / lid / "lote.json"

    def _salvar_manifest(self, manifest: Dict[str, Any]) -> None:
        manifest["atualizado_em"] = _agora_iso()
        with self._lock:
            _gravar_json_atomico(self._manifest_path(manifest["lote_id"]), manifest)

    def criar_staging(self) -> Tuple[str, Path]:
        """Cria um novo ``lote_id`` e sua pasta ``_entrada/`` para gravação em streaming."""
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        lote_id = f"lote_{ts}_{uuid.uuid4().hex[:6]}"
        staging_dir = diretorio_lotes() / lote_id / "_entrada"
        staging_dir.mkdir(parents=True, exist_ok=True)
        return lote_id, staging_dir

    def descartar_lote_parcial(self, lote_id: str) -> None:
        """Remove a pasta do lote em caso de aborto durante o upload (sem deixar resíduos)."""
        try:
            lid = _validar_segmento(lote_id, "lote_id")
            alvo = diretorio_lotes() / lid
            if alvo.exists():
                shutil.rmtree(alvo, ignore_errors=True)
        except Exception:
            pass

    def preparar_lote_de_caminhos(
        self,
        lote_id: str,
        entradas: List[Tuple[str, Path, str]],
        job_id: str,
    ) -> Dict[str, Any]:
        """Cria o manifesto ``lote.json`` a partir de PDFs já gravados em streaming no disco."""
        lid = _validar_segmento(lote_id, "lote_id")
        itens_iniciais: List[Dict[str, Any]] = []
        for idx, (nome_orig, _staged, sha) in enumerate(entradas, 1):
            itens_iniciais.append(
                {
                    "pid": f"pendente_{idx:03d}_{sha}",
                    "arquivo_original": Path(nome_orig or f"processo_{idx}.pdf").name,
                    "numero_processo": None,
                    "total_pecas": 0,
                    "tipos_penais": [],
                    "pendencias": [],
                    "alertas_prescricao": [],
                    "status": "pendente",
                    "motivo": None,
                }
            )

        manifest: Dict[str, Any] = {
            "lote_id": lid,
            "job_id": job_id,
            "status": "pendente",
            "total": len(entradas),
            "processados": 0,
            "prontos": 0,
            "com_pendencias": 0,
            "falhas": 0,
            "cancelados": 0,
            "percentual": 0,
            "criado_em": _agora_iso(),
            "atualizado_em": _agora_iso(),
            "processos": itens_iniciais,
        }
        with self._lock:
            self._cancel_events[lid] = threading.Event()
            self._salvar_manifest(manifest)
        return manifest

    def executar_lote(
        self,
        lote_id: str,
        entradas: List[Tuple[str, Path, str]],
        *,
        analisador: Optional[Callable[..., Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Executa a pré-análise sequencial (concorrência 1) sem tocar no caso ativo."""
        fn = analisador or analisar_pdf_para_dossie
        lote_dir = diretorio_lotes() / _validar_segmento(lote_id, "lote_id")
        cancel_ev = self._cancel_events.get(lote_id) or threading.Event()

        manifest = self.obter_lote(lote_id)
        if manifest is None:
            raise ValueError(f"Lote não encontrado: {lote_id}")

        manifest["status"] = "executando"
        self._salvar_manifest(manifest)

        total = len(entradas)
        for idx, (nome_orig, staged_path, sha) in enumerate(entradas):
            if cancel_ev.is_set():
                for rem_idx in range(idx, total):
                    manifest["processos"][rem_idx]["status"] = "cancelado"
                    manifest["processos"][rem_idx]["motivo"] = "Lote cancelado pelo usuário."
                    manifest["cancelados"] += 1
                manifest["status"] = "cancelado"
                manifest["percentual"] = int((manifest["processados"] / total) * 100) if total else 100
                self._salvar_manifest(manifest)
                break

            try:
                from app.core.vram_manager import reservar_gpu

                with reservar_gpu("lote"):
                    dossie = fn(
                        staged_path,
                        lote_id=lote_id,
                        arquivo_original=nome_orig,
                        sha_prefix=sha,
                        destino_lote=lote_dir,
                    )
                manifest["processos"][idx] = {
                    "pid": dossie["pid"],
                    "arquivo_original": dossie["arquivo_original"],
                    "numero_processo": dossie.get("numero_processo"),
                    "total_pecas": dossie.get("total_pecas", 0),
                    "tipos_penais": dossie.get("tipos_penais", []),
                    "teses_defensivas": len(dossie.get("teses_defensivas") or []),
                    "pendencias": dossie.get("pendencias", []),
                    "alertas_prescricao": dossie.get("alertas_prescricao", []),
                    "status": dossie["status"],
                    "motivo": dossie.get("motivo"),
                }
                if dossie["status"] == "pronto":
                    manifest["prontos"] += 1
                else:
                    manifest["com_pendencias"] += 1
            except Exception as exc:
                pid_erro = f"falhou_{idx + 1:03d}_{sha}"
                proc_dir = lote_dir / pid_erro
                proc_dir.mkdir(parents=True, exist_ok=True)
                motivo = f"{type(exc).__name__}: {exc}"
                dossie_erro = {
                    "pid": pid_erro,
                    "lote_id": lote_id,
                    "arquivo_original": nome_orig,
                    "pdf_nome": None,
                    "numero_processo": None,
                    "pecas_indexadas": [],
                    "total_pecas": 0,
                    "tipos_penais": [],
                    "teses_defensivas": [],
                    "pendencias": list(_CAMPOS_ESSENCIAIS),
                    "alertas_prescricao": [],
                    "alertas_extracao": [],
                    "status": "falhou",
                    "motivo": motivo,
                    "processado_em": _agora_iso(),
                }
                _gravar_json_atomico(proc_dir / "dossie.json", dossie_erro)
                manifest["processos"][idx] = {
                    "pid": pid_erro,
                    "arquivo_original": nome_orig,
                    "numero_processo": None,
                    "total_pecas": 0,
                    "tipos_penais": [],
                    "teses_defensivas": 0,
                    "pendencias": list(_CAMPOS_ESSENCIAIS),
                    "alertas_prescricao": [],
                    "status": "falhou",
                    "motivo": motivo,
                }
                manifest["falhas"] += 1
                logger.warning("Lote %s: falha no arquivo %s (%s); continuando lote.", lote_id, nome_orig, motivo)
            finally:
                manifest["processados"] = idx + 1
                manifest["percentual"] = int(((idx + 1) / total) * 100) if total else 100
                self._salvar_manifest(manifest)
        else:
            manifest["status"] = "concluido"
            manifest["percentual"] = 100
            self._salvar_manifest(manifest)

        # Limpa staging após concluir ou cancelar
        staging = lote_dir / "_entrada"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

        return manifest

    def cancelar_lote(self, lote_id: str) -> Optional[Dict[str, Any]]:
        lid = _validar_segmento(lote_id, "lote_id")
        manifest = self.obter_lote(lid)
        if manifest is None:
            return None
        with self._lock:
            ev = self._cancel_events.get(lid)
            if ev is not None:
                ev.set()
            if manifest.get("status") in ("pendente", "executando"):
                manifest["status"] = "cancelando"
                self._salvar_manifest(manifest)
        return manifest

    def obter_lote(self, lote_id: str) -> Optional[Dict[str, Any]]:
        path = self._manifest_path(lote_id)
        with self._lock:
            if not path.is_file():
                return None
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None

    def listar_lotes(self) -> List[Dict[str, Any]]:
        raiz = diretorio_lotes()
        itens: List[Dict[str, Any]] = []
        for sub in raiz.iterdir():
            if not sub.is_dir():
                continue
            mf = sub / "lote.json"
            if not mf.is_file():
                continue
            try:
                dados = json.loads(mf.read_text(encoding="utf-8"))
                itens.append(dados)
            except Exception:
                continue
        itens.sort(key=lambda d: str(d.get("criado_em") or ""), reverse=True)
        return itens

    def localizar_pdf_processo(self, lote_id: str, pid: str) -> Tuple[Path, Dict[str, Any]]:
        lid = _validar_segmento(lote_id, "lote_id")
        pid_seg = _validar_segmento(pid, "pid")
        proc_dir = (diretorio_lotes() / lid / pid_seg).resolve()
        raiz = diretorio_lotes().resolve()
        if raiz not in proc_dir.parents or not proc_dir.is_dir():
            raise FileNotFoundError(f"Processo '{pid}' não encontrado no lote '{lote_id}'.")
        dossie_path = proc_dir / "dossie.json"
        if not dossie_path.is_file():
            raise FileNotFoundError(f"Dossiê não encontrado para '{pid}'.")
        dossie = json.loads(dossie_path.read_text(encoding="utf-8"))
        pdf_nome = dossie.get("pdf_nome")
        if not pdf_nome or dossie.get("status") == "falhou":
            raise ValueError(f"Processo '{pid}' não pode ser aberto (status={dossie.get('status')}).")
        pdf_path = (proc_dir / Path(str(pdf_nome)).name).resolve()
        if pdf_path.parent != proc_dir or not pdf_path.is_file():
            raise FileNotFoundError(f"Arquivo PDF do processo '{pid}' não encontrado no disco.")
        return pdf_path, dossie


def arquivar_lotes_no_reset(archive_root: Path, *, excluir_definitivamente: bool = False) -> int:
    """Arquiva (ou apaga, se ``excluir_definitivamente``) ``data/lote/`` durante o reset completo."""
    raiz = Path(app_config.DATA_DIR) / "lote"
    if not raiz.exists() or not raiz.is_dir():
        return 0
    arquivos = [p for p in raiz.rglob("*") if p.is_file()]
    if not arquivos:
        shutil.rmtree(raiz, ignore_errors=True)
        return 0
    if not excluir_definitivamente:
        archive_root.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        destino_zip = archive_root / f"{ts}_lote.zip"
        contador = 2
        while destino_zip.exists():
            destino_zip = archive_root / f"{ts}_lote_{contador}.zip"
            contador += 1
        with zipfile.ZipFile(destino_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for arq in arquivos:
                rel = arq.relative_to(raiz).as_posix()
                zf.write(arq, arcname=f"lote/{rel}")
    shutil.rmtree(raiz, ignore_errors=True)
    return len(arquivos)


LOTE_MANAGER = LoteManager()
