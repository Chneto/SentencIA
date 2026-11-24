"""Progresso de ingestão e jobs em segundo plano.

- ``PROGRESSO``: estado thread-safe exibido em ``GET /api/cases/upload-progress``.
- ``INGESTAO_LOCK``: exclusividade — só uma ingestão (upload ou carga do disco) por vez; uma
  segunda requisição concorrente recebe HTTP 409 em vez de corromper o estado.
- ``JOBS``: registro dos jobs assíncronos (``POST /api/cases/upload-files`` com
  ``assincrono=true``), consultados em ``GET /api/jobs/{job_id}``.
"""

from __future__ import annotations

import datetime
import threading
import uuid
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional

from app.api.erros import descrever_erro

_ESTADO_INICIAL: Dict[str, Any] = {
    "active": False,
    "status": "idle",
    "phase": "aguardando",
    "etapa": "",
    "percent": 0,
    "current_file": "",
    "total_files": 0,
    "processed_files": 0,
    "pagina_atual": 0,
    "paginas_total": 0,
    "iniciado_em": None,
    "ultimo_avanco_em": None,
    "cancelado": False,
    "files": [],
    "job_id": None,
    "erro": None,
    "updated_at": "",
}


def _agora_br() -> str:
    return datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")


def _agora_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


class ProgressoIngestao:
    """Estado de progresso compartilhado entre a thread de processamento e o polling/SSE."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._estado: Dict[str, Any] = dict(_ESTADO_INICIAL)

    def atualizar(self, **kwargs: Any) -> Dict[str, Any]:
        with self._lock:
            agora_iso = _agora_iso()
            chaves_avanco = ("pagina_atual", "paginas_total", "processed_files", "percent", "etapa", "phase", "current_file")
            houve_avanco = any(
                k in kwargs and kwargs[k] != self._estado.get(k)
                for k in chaves_avanco
            )
            self._estado.update(kwargs)
            if self._estado.get("active") and not self._estado.get("iniciado_em"):
                self._estado["iniciado_em"] = agora_iso
            if "ultimo_avanco_em" not in kwargs and (houve_avanco or (self._estado.get("active") and not self._estado.get("ultimo_avanco_em"))):
                self._estado["ultimo_avanco_em"] = agora_iso
            self._estado["updated_at"] = _agora_br()
            return dict(self._estado)

    def obter(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._estado)

    def reiniciar(self) -> Dict[str, Any]:
        with self._lock:
            self._estado = dict(_ESTADO_INICIAL)
            self._estado["updated_at"] = _agora_br()
            return dict(self._estado)


PROGRESSO = ProgressoIngestao()
INGESTAO_LOCK = threading.Lock()
CANCELAR_INGESTAO = threading.Event()


class RegistroJobs:
    """Registro em memória dos últimos jobs (mantém os ``limite`` mais recentes)."""

    def __init__(self, limite: int = 20) -> None:
        self._lock = threading.Lock()
        self._jobs: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._limite = limite

    def criar(self, tipo: str) -> Dict[str, Any]:
        job = {
            "job_id": uuid.uuid4().hex,
            "tipo": tipo,
            "status": "pendente",
            "criado_em": _agora_iso(),
            "atualizado_em": _agora_iso(),
            "resultado": None,
            "erro": None,
            "status_http": None,
        }
        with self._lock:
            self._jobs[job["job_id"]] = job
            while len(self._jobs) > self._limite:
                self._jobs.popitem(last=False)
            return dict(job)

    def atualizar(self, job_id: str, **campos: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.update(campos)
            job["atualizado_em"] = _agora_iso()

    def limpar(self) -> None:
        """Esquece todos os jobs (o ``resultado`` de um upload guarda o caso inteiro)."""
        with self._lock:
            self._jobs.clear()

    def obter(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job is not None else None

    def executar_em_thread(
        self,
        job_id: str,
        alvo: Callable[[], Dict[str, Any]],
        ao_finalizar: Optional[Callable[[], None]] = None,
    ) -> threading.Thread:
        """Executa ``alvo`` numa thread daemon; o resultado/erro fica no registro do job."""

        def _rodar() -> None:
            self.atualizar(job_id, status="executando")
            try:
                resultado = alvo()
                self.atualizar(job_id, status="concluido", resultado=resultado, status_http=200)
            except BaseException as exc:  # noqa: BLE001 - o job nunca pode morrer sem registrar o erro
                status, corpo = descrever_erro(exc)
                self.atualizar(job_id, status="erro", erro=corpo, status_http=status)
            finally:
                if ao_finalizar is not None:
                    ao_finalizar()

        thread = threading.Thread(target=_rodar, name=f"sentencia-job-{job_id[:8]}", daemon=True)
        thread.start()
        return thread


JOBS = RegistroJobs()
