"""Cliente único do Ollama local.

- ``gerar_texto`` nunca levanta exceção: devolve ``None`` em falha (com ``logging.warning``).
- ``ollama_disponivel`` reporta apenas o que foi medido: offline → ``installed_models == []``.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

from app.core import config as _cfg
from app.core.config import load_settings, ollama_url_loopback
from app.core.envio_externo import preparar_prompt_ollama, restaurar_resposta_ollama

logger = logging.getLogger(__name__)

AVISO_OLLAMA_SEM_MODELO = "Ollama sem modelo instalado: redação pelo motor determinístico"
_INSTRUCT_RE = re.compile(r"(?:^|[:_\-/])(instruct|it)(?:$|[:_\-/0-9])", re.IGNORECASE)
_CACHE_AUTO_TTL_S = 600.0
_CACHE_AUTO_LOCK = threading.RLock()
_CACHE_AUTO: Dict[tuple[str, str, int], tuple[str, float]] = {}

__all__ = [
    "AVISO_OLLAMA_SEM_MODELO",
    "gerar_texto",
    "ollama_disponivel",
    "listar_modelos_ollama",
    "resolver_modelo_ollama",
    "invalidar_cache_modelo_auto",
    "descarregar_modelo",
    "criar_cliente",
    "url_ollama_autorizada",
]


def invalidar_cache_modelo_auto() -> None:
    """Limpa o cache em memória da escolha automática de modelo Ollama."""
    with _CACHE_AUTO_LOCK:
        _CACHE_AUTO.clear()


def _obter_modelo_auto_cache(url: str, configurado: str) -> Optional[str]:
    chave = (url, configurado, int(getattr(_cfg, "SETTINGS_VERSION", 0)))
    agora = time.monotonic()
    with _CACHE_AUTO_LOCK:
        entrada = _CACHE_AUTO.get(chave)
        if not entrada:
            return None
        modelo, ts = entrada
        if (agora - ts) > _CACHE_AUTO_TTL_S:
            _CACHE_AUTO.pop(chave, None)
            return None
        return modelo


def _gravar_modelo_auto_cache(url: str, configurado: str, modelo: str) -> None:
    chave = (url, configurado, int(getattr(_cfg, "SETTINGS_VERSION", 0)))
    with _CACHE_AUTO_LOCK:
        _CACHE_AUTO[chave] = (modelo, time.monotonic())


def criar_cliente(timeout: float) -> httpx.Client:
    """``httpx.Client`` que NAO le proxy do ambiente/sistema (``trust_env=False``).

    O Ollama e local: o proxy do Windows (registro) ou do macOS nao isenta o loopback e
    receberia os autos em HTTP puro.
    """
    return httpx.Client(timeout=timeout, trust_env=False)


def url_ollama_autorizada(url: str, permitir_envio_externo: bool) -> bool:
    """Consultas técnicas e comandos sem conteúdo: loopback sempre; outro host somente com ``permitir_envio_externo=True``."""
    return ollama_url_loopback(url) or bool(permitir_envio_externo)


def _url_base(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _mesmo_modelo(a: str, b: str) -> bool:
    """``llama3`` e ``llama3:latest`` designam o mesmo modelo no Ollama."""

    def _canon(nome: str) -> str:
        nome = (nome or "").strip().lower()
        return nome if ":" in nome else f"{nome}:latest"

    return bool(a and b) and _canon(a) == _canon(b)


def _eh_auto(modelo: str) -> bool:
    return (modelo or "").strip().lower() in ("", "auto")


def _eh_instruct(nome: str) -> bool:
    return bool(_INSTRUCT_RE.search(nome or "")) or "instruct" in (nome or "").lower()


def _estimar_vram_modelo_mb(item: Dict[str, Any]) -> Optional[float]:
    """Estima VRAM necessária (MB) com margem de 15% a partir do tamanho em GB ou da tabela VRAM."""
    tam_gb = item.get("tamanho_gb")
    if isinstance(tam_gb, (int, float)) and tam_gb > 0:
        return float(tam_gb) * 1024.0 * 1.15
    try:
        from app.core.vram_manager import _estimar_vram_ollama_mb

        est = _estimar_vram_ollama_mb(str(item.get("nome") or ""))
        return float(est) * 1.15 if est else None
    except Exception:
        return None


def _escolher_recomendado(
    instalados: List[Dict[str, Any]],
    configurado: str,
    configurado_instalado_nome: Optional[str],
) -> Optional[str]:
    if not instalados:
        return None
    if not _eh_auto(configurado) and configurado_instalado_nome:
        return configurado_instalado_nome

    vram_ref_mb: Optional[float] = None
    try:
        from app.core.vram_manager import MODO_GPU_MODERADO_VRAM_FATOR, get_gpu_vram_status

        hw = get_gpu_vram_status()
        if hw.get("gpu_available"):
            # Usa VRAM total (evita que um modelo já carregado no Ollama reduza vram_free_mb e se expulse).
            total = hw.get("vram_total_mb")
            livre = hw.get("vram_free_mb")
            if isinstance(total, (int, float)) and total > 0:
                vram_ref_mb = float(total)
            elif isinstance(livre, (int, float)) and livre > 0:
                vram_ref_mb = float(livre)
        if vram_ref_mb is not None:
            modo_atual = str(getattr(load_settings(), "modo_gpu", "normal") or "normal").strip().lower()
            if modo_atual == "moderado":
                vram_ref_mb = vram_ref_mb * MODO_GPU_MODERADO_VRAM_FATOR
    except Exception:
        vram_ref_mb = None

    if vram_ref_mb is None:
        # Sem medição de VRAM: prefere o menor modelo instruct/it (roda em qualquer máquina); senão o primeiro instalado.
        instructs = [it for it in instalados if _eh_instruct(str(it.get("nome") or ""))]
        if instructs:
            menor_instruct = min(instructs, key=lambda it: float(it.get("tamanho_gb") or 1e9))
            return str(menor_instruct["nome"])
        return str(instalados[0]["nome"])

    cabem: List[Dict[str, Any]] = []
    for item in instalados:
        est_mb = _estimar_vram_modelo_mb(item)
        if est_mb is None or est_mb <= vram_ref_mb:
            cabem.append(item)

    if not cabem:
        # Nenhum cabe com folga: escolhe o menor instalado
        menor = min(instalados, key=lambda it: float(it.get("tamanho_gb") or 1e9))
        return str(menor["nome"])

    # Maior modelo que cabe na VRAM medida, preferindo nomes com instruct/it
    preferidos = [it for it in cabem if _eh_instruct(str(it.get("nome") or ""))]
    candidatos = preferidos or cabem
    escolhido = max(
        candidatos,
        key=lambda it: (float(it.get("tamanho_gb") or 0.0), _estimar_vram_modelo_mb(it) or 0.0),
    )
    return str(escolhido["nome"])


def _extrair_modelos_tags(dados: Any) -> List[Dict[str, Any]]:
    modelos_brutos = dados.get("models", []) if isinstance(dados, dict) else []
    saida: List[Dict[str, Any]] = []
    for m in modelos_brutos:
        if not isinstance(m, dict) or not m.get("name"):
            continue
        size_raw = m.get("size")
        tamanho_gb: Optional[float] = None
        if isinstance(size_raw, (int, float)) and size_raw > 0:
            tamanho_gb = round(float(size_raw) / (1024**3), 2)
        details = m.get("details") if isinstance(m.get("details"), dict) else {}
        parametros = details.get("parameter_size")
        quantizacao = details.get("quantization_level")
        saida.append(
            {
                "nome": str(m["name"]),
                "tamanho_gb": tamanho_gb,
                "parametros": str(parametros) if isinstance(parametros, str) and parametros.strip() else None,
                "quantizacao": str(quantizacao) if isinstance(quantizacao, str) and quantizacao.strip() else None,
            }
        )
    return saida


def listar_modelos_ollama(timeout: float = 2.5) -> Dict[str, Any]:
    """Lista os modelos instalados no Ollama com metadados reais de ``/api/tags`` e modelo recomendado."""
    try:
        settings = load_settings()
        url = _url_base(settings.ollama_url)
        configurado = settings.ollama_model
        permitir_externo = settings.permitir_envio_externo
    except Exception:
        return {
            "online": False,
            "instalados": [],
            "configurado": "auto",
            "configurado_instalado": False,
            "recomendado": None,
        }

    base: Dict[str, Any] = {
        "online": False,
        "instalados": [],
        "configurado": configurado,
        "configurado_instalado": False,
        "recomendado": None,
    }
    if not url_ollama_autorizada(url, permitir_externo):
        return base

    try:
        with criar_cliente(timeout) as client:
            resp = client.get(f"{url}/api/tags")
        if getattr(resp, "status_code", 0) != 200:
            return base
        instalados = _extrair_modelos_tags(resp.json())
        nomes = [it["nome"] for it in instalados]
        instalado_nome = None if _eh_auto(configurado) else next((n for n in nomes if _mesmo_modelo(n, configurado)), None)
        recomendado = _escolher_recomendado(instalados, configurado, instalado_nome)
        return {
            "online": True,
            "instalados": instalados,
            "configurado": configurado,
            "configurado_instalado": instalado_nome is not None,
            "recomendado": recomendado,
        }
    except Exception:
        return base


def resolver_modelo_ollama(timeout: float = 2.5) -> Dict[str, Any]:
    """Resolve qual modelo usar nesta execução sem falha silenciosa."""
    info = listar_modelos_ollama(timeout=timeout)
    configurado = str(info.get("configurado") or "auto")
    if not info.get("online"):
        return {
            "online": False,
            "modelo": None,
            "configurado": configurado,
            "trocado": False,
            "motivo": "Ollama indisponível.",
            "aviso": None,
        }
    instalados = info.get("instalados") or []
    recomendado = info.get("recomendado")
    if not instalados or not recomendado:
        return {
            "online": True,
            "modelo": None,
            "configurado": configurado,
            "trocado": False,
            "motivo": AVISO_OLLAMA_SEM_MODELO,
            "aviso": AVISO_OLLAMA_SEM_MODELO,
        }
    if _eh_auto(configurado):
        return {
            "online": True,
            "modelo": recomendado,
            "configurado": configurado,
            "trocado": False,
            "motivo": f"Seleção automática escolheu '{recomendado}'.",
            "aviso": None,
        }
    if info.get("configurado_instalado"):
        return {
            "online": True,
            "modelo": recomendado,
            "configurado": configurado,
            "trocado": False,
            "motivo": None,
            "aviso": None,
        }
    return {
        "online": True,
        "modelo": recomendado,
        "configurado": configurado,
        "trocado": True,
        "motivo": f"Modelo configurado '{configurado}' ausente; usando '{recomendado}'.",
        "aviso": f"Modelo Ollama '{configurado}' não está instalado; usando '{recomendado}'.",
    }


def gerar_texto(
    prompt: str,
    *,
    num_predict: int = 1200,
    temperature: float = 0.1,
    timeout: float = 120.0,
    num_ctx: int | None = None,
    pensar: bool | None = None,
    modelo: str | None = None,
) -> str | None:
    """Gera texto com o modelo informado ou configurado (ou recomendado se ausente/auto). ``None`` em qualquer falha.

    ``modelo`` permite ao chamador (ex.: ``SectionalPipelineEngine``) fixar o modelo já resolvido para
    evitar oscilação entre seções da mesma sentença.
    ``pensar=False`` desliga o raciocínio interno dos modelos "thinking" (que, senão, consome o limite de
    ``num_predict`` e pode devolver resposta vazia em tarefas de classificação)."""
    try:
        settings = load_settings()
        url = _url_base(settings.ollama_url)
        preparado = preparar_prompt_ollama(url, settings, prompt)
        if preparado is None:
            logger.warning("Ollama remoto bloqueado: operação sem autorização ou pseudonimização incompleta.")
            return None
        prompt, mapa = preparado
        from app.core.vram_manager import calcular_opcoes_ollama, registrar_ultima_chamada_ollama, reservar_gpu

        configurado = settings.ollama_model
        modo_gpu = str(getattr(settings, "modo_gpu", "normal") or "normal").strip().lower()
        modelo_forcado = (modelo or "").strip() if isinstance(modelo, str) else ""
        usando_forcado = bool(modelo_forcado) and not _eh_auto(modelo_forcado)
        modelo_efetivo: Optional[str] = modelo_forcado if usando_forcado else configurado
        modelo_trocado = False
        aviso_modelo: Optional[str] = None
        motivo_modelo: Optional[str] = None
        veio_do_cache = False

        with reservar_gpu("ollama", modo_gpu=modo_gpu), criar_cliente(timeout) as client:
            if not usando_forcado and _eh_auto(configurado):
                em_cache = _obter_modelo_auto_cache(url, configurado)
                if em_cache:
                    modelo_efetivo = em_cache
                    motivo_modelo = f"Seleção automática (cache) manteve '{em_cache}'."
                    veio_do_cache = True
                else:
                    try:
                        resp_tags = client.get(f"{url}/api/tags")
                    except Exception:
                        resp_tags = None
                    if getattr(resp_tags, "status_code", 0) == 200:
                        instalados = _extrair_modelos_tags(resp_tags.json())
                        recomendado = _escolher_recomendado(instalados, configurado, None)
                        if not recomendado:
                            logger.warning(AVISO_OLLAMA_SEM_MODELO)
                            registrar_ultima_chamada_ollama(
                                {
                                    "modelo": None,
                                    "modelo_configurado": configurado,
                                    "modelo_trocado": False,
                                    "aviso_modelo": AVISO_OLLAMA_SEM_MODELO,
                                    "num_gpu": None,
                                    "num_ctx": int(num_ctx or settings.ollama_num_ctx),
                                    "vram_medida": False,
                                    "vram_livre_mb": None,
                                    "motivo": AVISO_OLLAMA_SEM_MODELO,
                                }
                            )
                            return None
                        modelo_efetivo = recomendado
                        motivo_modelo = f"Seleção automática escolheu '{recomendado}'."
                        _gravar_modelo_auto_cache(url, configurado, recomendado)

            def _chamar_generate(modelo_alvo: str) -> tuple[Any, Dict[str, Any], int]:
                politica = calcular_opcoes_ollama(
                    modelo=modelo_alvo,
                    num_ctx_padrao=int(num_ctx or settings.ollama_num_ctx),
                    modo_gpu=modo_gpu,
                )
                if num_ctx is not None:
                    ctx_efetivo = min(int(num_ctx), int(politica["num_ctx"])) if modo_gpu == "moderado" else int(num_ctx)
                else:
                    ctx_efetivo = int(politica["num_ctx"])
                opcoes: Dict[str, Any] = {
                    "temperature": temperature,
                    "num_predict": int(num_predict),
                    "num_ctx": ctx_efetivo,
                }
                if politica.get("num_gpu") is not None:
                    opcoes["num_gpu"] = int(politica["num_gpu"])
                payload: Dict[str, Any] = {
                    "model": modelo_alvo,
                    "prompt": prompt,
                    "stream": False,
                    "options": opcoes,
                }
                if politica.get("keep_alive") is not None:
                    payload["keep_alive"] = politica["keep_alive"]
                if pensar is not None:
                    payload["think"] = bool(pensar)
                return client.post(f"{url}/api/generate", json=payload), politica, ctx_efetivo

            resp, politica, ctx_efetivo = _chamar_generate(str(modelo_efetivo))

            # Se o modelo explícito ou cacheado não estiver instalado (HTTP 404), re-resolve via /api/tags.
            if getattr(resp, "status_code", 0) == 404 and (veio_do_cache or (not usando_forcado and not _eh_auto(configurado))):
                if veio_do_cache:
                    invalidar_cache_modelo_auto()
                try:
                    resp_tags = client.get(f"{url}/api/tags")
                except Exception:
                    resp_tags = None
                if getattr(resp_tags, "status_code", 0) == 200:
                    instalados = _extrair_modelos_tags(resp_tags.json())
                    recomendado = _escolher_recomendado(instalados, "auto", None)
                    if recomendado and not _mesmo_modelo(recomendado, str(modelo_efetivo or "")):
                        modelo_efetivo = recomendado
                        if veio_do_cache:
                            _gravar_modelo_auto_cache(url, configurado, recomendado)
                            motivo_modelo = f"Seleção automática escolheu '{recomendado}'."
                        else:
                            modelo_trocado = True
                            motivo_modelo = f"Modelo configurado '{configurado}' ausente; usando '{recomendado}'."
                            aviso_modelo = f"Modelo Ollama '{configurado}' não está instalado; usando '{recomendado}'."
                            logger.warning(aviso_modelo)
                        resp, politica, ctx_efetivo = _chamar_generate(modelo_efetivo)
                    elif not instalados:
                        aviso_modelo = AVISO_OLLAMA_SEM_MODELO
                        motivo_modelo = AVISO_OLLAMA_SEM_MODELO
                        logger.warning(AVISO_OLLAMA_SEM_MODELO)

        registrar_ultima_chamada_ollama(
            {
                "modelo": modelo_efetivo,
                "modelo_configurado": configurado,
                "modelo_trocado": modelo_trocado,
                "aviso_modelo": aviso_modelo,
                "num_gpu": int(politica["num_gpu"]) if politica.get("num_gpu") is not None else None,
                "num_ctx": ctx_efetivo,
                "vram_medida": bool(politica.get("vram_medida")),
                "vram_livre_mb": politica.get("vram_livre_mb"),
                "motivo": motivo_modelo or politica.get("motivo"),
            }
        )
        if getattr(resp, "status_code", 0) != 200:
            logger.warning("Ollama retornou HTTP %s em /api/generate.", getattr(resp, "status_code", 0))
            return None
        dados = resp.json()
        texto = dados.get("response") if isinstance(dados, dict) else None
        if not isinstance(texto, str) or not texto.strip():
            logger.warning("Ollama retornou resposta vazia.")
            return None
        return restaurar_resposta_ollama(texto, mapa).strip()
    except Exception as err:
        logger.warning("Falha ao gerar texto no Ollama: %s: %s", type(err).__name__, err)
        return None


def ollama_disponivel(timeout: float = 2.5) -> Dict[str, Any]:
    """Status medido do Ollama.

    Retorna ``{online, url, active_model, installed_models}`` e, adicionalmente,
    ``configured_model``, ``model_installed``, ``recommended_model`` e ``erro``.
    """
    try:
        settings = load_settings()
        url = _url_base(settings.ollama_url)
        configurado = settings.ollama_model
        permitir_externo = settings.permitir_envio_externo
    except Exception as err:  # pragma: no cover - settings sempre carregam
        return {
            "online": False,
            "url": "",
            "active_model": None,
            "installed_models": [],
            "configured_model": None,
            "model_installed": False,
            "recommended_model": None,
            "erro": f"configuração indisponível: {type(err).__name__}",
        }
    status: Dict[str, Any] = {
        "online": False,
        "url": url,
        "active_model": None,
        "installed_models": [],
        "configured_model": configurado,
        "model_installed": False,
        "recommended_model": None,
        "erro": None,
    }
    if not url_ollama_autorizada(url, permitir_externo):
        status["erro"] = "host remoto bloqueado: habilite permitir_envio_externo"
        return status
    try:
        with criar_cliente(timeout) as client:
            resp = client.get(f"{url}/api/tags")
        if resp.status_code != 200:
            status["erro"] = f"HTTP {resp.status_code}"
            return status
        instalados_det = _extrair_modelos_tags(resp.json())
        modelos: List[str] = [it["nome"] for it in instalados_det]
        instalado = None if _eh_auto(configurado) else next((m for m in modelos if _mesmo_modelo(m, configurado)), None)
        recomendado = _escolher_recomendado(instalados_det, configurado, instalado)
        ativo = recomendado if _eh_auto(configurado) else instalado
        status.update(
            {
                "online": True,
                "installed_models": modelos,
                "model_installed": ativo is not None,
                "active_model": ativo,
                "recommended_model": recomendado,
            }
        )
        if not modelos:
            status["erro"] = AVISO_OLLAMA_SEM_MODELO
        elif not _eh_auto(configurado) and instalado is None:
            status["erro"] = f"modelo configurado '{configurado}' não está instalado"
    except Exception as err:
        status["erro"] = f"indisponível: {type(err).__name__}"
    return status


def descarregar_modelo(timeout: float = 3.0) -> bool:
    """Pede ao Ollama para liberar o modelo da memória (``keep_alive=0``). True se o servidor aceitou."""
    try:
        settings = load_settings()
        if not url_ollama_autorizada(settings.ollama_url, settings.permitir_envio_externo):
            return False
        with criar_cliente(timeout) as client:
            resp = client.post(
                f"{_url_base(settings.ollama_url)}/api/generate",
                json={"model": settings.ollama_model, "prompt": "", "keep_alive": 0},
            )
        return getattr(resp, "status_code", 0) == 200
    except Exception as err:
        logger.debug("Falha ao descarregar modelo do Ollama: %s", type(err).__name__)
        return False
