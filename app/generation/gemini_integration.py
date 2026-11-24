# -*- coding: utf-8 -*-
"""
Integração Dupla Via com o Modelo de IA Google Gemini (API Direta google-genai + Fluxo Web 1-Clique)
com Pseudonimização Reversível (Segredo de Justiça / LGPD) e Auditoria em 4 Camadas.

v2.0.0 — privacidade e honestidade:

- **Nenhum dado do processo sai da máquina** sem ``settings.permitir_envio_externo == True`` E
  consentimento explícito na requisição (``consentimento_envio_externo=True``); caso contrário
  levanta ``EnvioExternoNaoAutorizadoError``. A chave vem do cofre (``app.core.secrets``), do
  parâmetro da requisição ou das variáveis ``GEMINI_API_KEY``/``GOOGLE_API_KEY``.
- ``google-genai`` é opcional: se não estiver instalado (ou a API falhar/responder vazio), levanta
  ``GeminiIndisponivelError`` com mensagem clara — nunca cai silenciosamente em outro motor
  dizendo "concluído".
- Sem chave configurada, a sentença é gerada localmente pelo motor determinístico UMA única vez e
  auditada diretamente, com rótulo e telemetria verdadeiros (``gemini_utilizado: False``).
- **Sem auto-blindagem silenciosa:** a substituição de seções reprovadas pelo modelo determinístico
  só ocorre quando solicitada (``auto_blindar_auditoria=True``) e é sinalizada no laudo
  (``secoes_fallback``), na telemetria e no rótulo do motor.
- O mapa de pseudonimização emitido com o Script pode ser devolvido na importação
  (``mapa_reversao`` + ``mapa_hash``); se não for, é reconstruído de forma determinística.
"""

import hashlib
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import load_settings
from app.core.errors import EnvioExternoNaoAutorizadoError
from app.generation.case_script_engine import (
    ResultadoMotores,
    ScriptCasoEstruturado,
    calcular_hash_mapa,
    construir_mapa_do_contexto,
    despseudonimizar_texto,
    executar_motores_completo,
    gerar_script_estruturado_caso,
)
from app.generation.date_formatter import (
    converter_mencoes_textuais_para_extenso,
    formatar_data_br,
    mascarar_e_padronizar_datas_texto,
)
from app.generation.hallucination_auditor import (
    RelatorioAuditoriaSentenca,
    auditar_secao,
    auditar_sentenca,
    dosimetria_para_auditoria,
)
from app.generation.sentence_generator import (
    SentencaGerada,
    gerar_sentenca_estruturada,
)

logger = logging.getLogger(__name__)

NOME_SEGREDO_GEMINI = "gemini_api_key"

#: Ordem canônica das seções de ``SentencaGerada``.
SECOES_ORDEM = ("cabecalho", "relatorio", "preliminares", "merito", "dosimetria", "dispositivo")


class GeminiIndisponivelError(RuntimeError):
    """O motor Gemini não pôde ser usado (pacote opcional ausente, falha de rede/API ou resposta vazia)."""

    def __init__(self, mensagem: str, causa: Optional[str] = None) -> None:
        self.mensagem = mensagem
        self.causa = causa
        super().__init__(mensagem if not causa else f"{mensagem} ({causa})")

    def to_dict(self) -> Dict[str, Any]:
        return {"erro": "gemini_indisponivel", "mensagem": self.mensagem, "causa": self.causa}


# ------------------------------------------------------------------------------
# Privacidade: autorização de envio externo e chave
# ------------------------------------------------------------------------------


def verificar_autorizacao_envio_externo(consentimento_envio_externo: bool, settings: Any = None) -> None:
    """
    Exige ``settings.permitir_envio_externo`` E consentimento explícito na requisição antes de
    qualquer envio de dado do processo para serviço externo. Levanta
    ``EnvioExternoNaoAutorizadoError`` caso contrário.
    """
    from app.core.envio_externo import motivo_bloqueio_envio_externo

    motivo = motivo_bloqueio_envio_externo(settings or load_settings(), consentimento_envio_externo)
    if motivo:
        raise EnvioExternoNaoAutorizadoError(f"Operação bloqueada: {motivo}. Confirme o envio do script ao Google Gemini.")



def resolver_chave_gemini(api_key: str = "") -> str:
    """Chave do Gemini: parâmetro da requisição → cofre local → variáveis de ambiente."""
    chave = (api_key or "").strip()
    if chave:
        return chave
    try:
        from app.core.secrets import get_secret

        chave = (get_secret(NOME_SEGREDO_GEMINI) or "").strip()
    except Exception as exc:  # cofre indisponível não pode derrubar a geração local
        logger.warning("Cofre de segredos indisponível: %s", exc)
        chave = ""
    return (
        chave
        or os.environ.get("GEMINI_API_KEY", "").strip()
        or os.environ.get("GOOGLE_API_KEY", "").strip()
    )


_CACHE_MODELOS_TTL_SEG = 3600.0
_CACHE_MODELOS: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_MODELOS_INDISPONIVEIS: Dict[str, float] = {}
_FALHAS_TIMEOUT_MODELO: Dict[str, int] = {}
_SUFIXOS_IGNORADOS_AUTO = (
    "-preview",
    "-exp",
    "-tts",
    "-image",
    "-live",
    "-embedding",
    "embedding",
    "imagen",
    "veo",
    "aqa",
    "learnlm",
)
_SUFIXOS_NAO_TEXTO = ("-tts", "-image", "-live", "-embedding", "embedding", "imagen", "veo", "aqa")
_MAX_OUTPUT_TOKENS_PADRAO = 32768
_TIMEOUT_GEMINI_MS = 180_000
_TIMEOUT_FALLBACK_504_MS = 90_000


def limpar_cache_modelos_gemini() -> None:
    """Limpa o cache em memória da listagem de modelos Gemini e de modelos indisponíveis."""
    _CACHE_MODELOS.clear()
    _MODELOS_INDISPONIVEIS.clear()
    _FALHAS_TIMEOUT_MODELO.clear()


def marcar_modelo_indisponivel(nome: str) -> None:
    """Marca um modelo como indisponível em memória por 1 hora para evitar novas falhas repetidas."""
    norm = _normalizar_nome_modelo(nome)
    if norm and norm != "auto":
        _MODELOS_INDISPONIVEIS[norm] = time.monotonic()


def _registrar_falha_timeout(nome: str) -> bool:
    """Conta falhas seguidas de 504/timeout por modelo; só marca indisponível por 1h após 2 falhas seguidas."""
    norm = _normalizar_nome_modelo(nome)
    if not norm or norm == "auto":
        return False
    contagem = _FALHAS_TIMEOUT_MODELO.get(norm, 0) + 1
    _FALHAS_TIMEOUT_MODELO[norm] = contagem
    if contagem >= 2:
        marcar_modelo_indisponivel(norm)
        return True
    return False


def _limpar_falha_timeout(nome: str) -> None:
    norm = _normalizar_nome_modelo(nome)
    if norm:
        _FALHAS_TIMEOUT_MODELO.pop(norm, None)


def _modelo_esta_indisponivel(nome: str) -> bool:
    norm = _normalizar_nome_modelo(nome)
    ts = _MODELOS_INDISPONIVEIS.get(norm)
    if ts is None:
        return False
    if (time.monotonic() - ts) >= _CACHE_MODELOS_TTL_SEG:
        _MODELOS_INDISPONIVEIS.pop(norm, None)
        return False
    return True


def _dormir(segundos: float) -> None:
    """Wrapper de ``time.sleep`` isolado para facilitar testes determinísticos."""
    time.sleep(segundos)


def _sanitizar_erro(texto: str, chave: str = "") -> str:
    """Remove qualquer vestígio da chave da API Gemini de mensagens e logs."""
    limpo = str(texto or "")
    if chave and chave in limpo:
        limpo = limpo.replace(chave, "[CHAVE_OCULTA]")
    limpo = re.sub(r"AQ\.[A-Za-z0-9._-]{10,}", "[CHAVE_OCULTA]", limpo)
    limpo = re.sub(r"AIza[A-Za-z0-9_-]{10,}", "[CHAVE_OCULTA]", limpo)
    return limpo


def _chave_cache_hash(chave: str) -> str:
    if not chave:
        return ""
    return hashlib.sha256(chave.encode("utf-8")).hexdigest()[:16]


def _normalizar_nome_modelo(nome: str) -> str:
    limpo = (nome or "").strip()
    if limpo.startswith("models/"):
        return limpo[len("models/") :]
    return limpo


def _versao_modelo(nome: str) -> Tuple[int, ...]:
    m = re.search(r"gemini-(\d+(?:\.\d+)*)", nome.lower())
    if not m:
        return (0,)
    partes = []
    for p in m.group(1).split("."):
        try:
            partes.append(int(p))
        except ValueError:
            partes.append(0)
    return tuple(partes)


def _eh_variante_ignorada(nome: str) -> bool:
    low = nome.lower()
    return any(s in low for s in _SUFIXOS_IGNORADOS_AUTO)


def _eh_nao_texto(nome: str) -> bool:
    low = nome.lower()
    return any(s in low for s in _SUFIXOS_NAO_TEXTO)


def _pontuar_modelo(nome: str, preferencia_familia: str = "pro") -> Tuple[int, int, int, Tuple[int, ...], int, int, str]:
    low = nome.lower()
    eh_pro = "pro" in low
    eh_flash = "flash" in low
    eh_lite = "lite" in low
    eh_pro_ou_flash = 1 if (eh_pro or eh_flash) else 0
    if preferencia_familia == "flash":
        pref = 2 if eh_flash else (1 if eh_pro else 0)
    else:
        pref = 2 if eh_pro else (1 if eh_flash else 0)
    eh_gemini = 1 if low.startswith("gemini-") else 0
    nao_lite = 0 if eh_lite else 1
    estavel = 0 if _eh_variante_ignorada(low) else 1
    # Geração mais recente dentro de pro/flash vem antes de família antiga (ex.: 3.8-flash > 2.5-pro);
    # na mesma geração, pro vence flash (ou flash vence pro na transcrição).
    return (estavel, eh_gemini, eh_pro_ou_flash, _versao_modelo(low), pref, nao_lite, low)


def _suporta_generate_content(modelo_obj: Any) -> bool:
    acoes = getattr(modelo_obj, "supported_actions", None)
    if acoes is None:
        acoes = getattr(modelo_obj, "supported_generation_methods", None)
    if not acoes:
        return False
    return any("generatecontent" in str(a).lower() for a in acoes)


def listar_modelos_gemini(
    client: Any,
    *,
    chave_cache: str = "",
    forcar: bool = False,
) -> List[Dict[str, Any]]:
    """Lista os modelos compatíveis com ``generateContent`` via ``client.models.list()`` com cache de 1h."""
    agora = time.monotonic()
    if chave_cache and not forcar and chave_cache in _CACHE_MODELOS:
        ts, cached = _CACHE_MODELOS[chave_cache]
        if (agora - ts) < _CACHE_MODELOS_TTL_SEG:
            return list(cached)

    itens: List[Dict[str, Any]] = []
    for m in client.models.list():
        if not _suporta_generate_content(m):
            continue
        nome = _normalizar_nome_modelo(str(getattr(m, "name", "") or ""))
        if not nome:
            continue
        out_lim = getattr(m, "output_token_limit", None)
        in_lim = getattr(m, "input_token_limit", None)
        itens.append(
            {
                "name": nome,
                "display_name": str(getattr(m, "display_name", "") or nome),
                "output_token_limit": int(out_lim) if isinstance(out_lim, int) and out_lim > 0 else None,
                "input_token_limit": int(in_lim) if isinstance(in_lim, int) and in_lim > 0 else None,
            }
        )

    if chave_cache:
        _CACHE_MODELOS[chave_cache] = (agora, list(itens))
    return itens


def _escolher_recomendado(modelos: List[Dict[str, Any]], preferencia_familia: str = "pro") -> Dict[str, Any]:
    disponiveis = [m for m in modelos if not _modelo_esta_indisponivel(m["name"])] or modelos
    estaveis = [m for m in disponiveis if not _eh_variante_ignorada(m["name"])]
    candidatos = estaveis or [m for m in disponiveis if not _eh_nao_texto(m["name"])] or disponiveis
    return max(candidatos, key=lambda m: _pontuar_modelo(m["name"], preferencia_familia))


def resolver_modelo_gemini(
    client: Any,
    preferido: str = "auto",
    *,
    preferencia_familia: str = "pro",
    chave_cache: str = "",
    forcar_listagem: bool = False,
) -> Dict[str, Any]:
    """Resolve o modelo Gemini em tempo de execução a partir dos modelos ativos na conta.

    - Se ``preferido`` existir na listagem e não estiver marcado como indisponível, mantém ``preferido``.
    - Caso contrário (ou quando ``preferido`` é ``"auto"``/vazio), escolhe o melhor disponível
      (família ``pro`` mais recente → ``flash`` mais recente, ignorando variantes preview/exp/tts/image/live/embedding).
    - Registra ``modelo_solicitado``, ``modelo_utilizado`` e ``motivo_troca``.
    """
    solicitado = _normalizar_nome_modelo(preferido) or "auto"

    # Retrocompatibilidade com clientes simulados legados que não implementam ``models.list()``.
    if not hasattr(getattr(client, "models", None), "list"):
        modelo_legado = solicitado if solicitado != "auto" else "gemini-2.5-pro"
        return {
            "modelo_solicitado": solicitado,
            "modelo_utilizado": modelo_legado,
            "motivo_troca": None,
            "output_token_limit": None,
            "modelos": [],
        }

    modelos = listar_modelos_gemini(client, chave_cache=chave_cache, forcar=forcar_listagem)
    if not modelos:
        raise GeminiIndisponivelError(
            "Nenhum modelo Gemini compatível com generateContent está disponível para a chave configurada. "
            "Verifique as permissões da chave no Google AI Studio."
        )

    por_nome = {m["name"]: m for m in modelos}
    if solicitado != "auto" and solicitado in por_nome and not _modelo_esta_indisponivel(solicitado):
        escolhido = por_nome[solicitado]
        return {
            "modelo_solicitado": solicitado,
            "modelo_utilizado": escolhido["name"],
            "motivo_troca": None,
            "output_token_limit": escolhido.get("output_token_limit"),
            "modelos": modelos,
        }

    recomendado = _escolher_recomendado(modelos, preferencia_familia=preferencia_familia)
    motivo_troca: Optional[str] = None
    if solicitado != "auto":
        if _modelo_esta_indisponivel(solicitado):
            motivo_troca = (
                f"Modelo '{solicitado}' marcado como indisponível recentemente; substituído automaticamente por "
                f"'{recomendado['name']}'."
            )
        else:
            motivo_troca = (
                f"Modelo '{solicitado}' não disponível na conta; substituído automaticamente por "
                f"'{recomendado['name']}'."
            )
    return {
        "modelo_solicitado": solicitado,
        "modelo_utilizado": recomendado["name"],
        "motivo_troca": motivo_troca,
        "output_token_limit": recomendado.get("output_token_limit"),
        "modelos": modelos,
    }


def _criar_cliente_gemini(chave: str, *, timeout_ms: int = _TIMEOUT_GEMINI_MS) -> Tuple[Any, Any]:
    """Importa ``google-genai`` de forma preguiçosa e cria o cliente com timeout configurável (padrão 180s)."""
    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except ImportError as exc:
        raise GeminiIndisponivelError(
            "O pacote opcional 'google-genai' não está instalado. Instale o extra de nuvem "
            "(uv sync --extra nuvem) ou use o fluxo Copiar Script / Colar Sentença.",
            causa=f"{type(exc).__name__}: {exc}",
        ) from exc

    http_options = None
    if hasattr(types, "HttpOptions"):
        try:
            http_options = types.HttpOptions(timeout=timeout_ms)
        except Exception:
            http_options = None
    if http_options is not None:
        try:
            return genai.Client(api_key=chave, http_options=http_options), types
        except TypeError:
            pass
    return genai.Client(api_key=chave), types


def _codigo_http_erro(exc: Exception) -> Optional[int]:
    for attr in ("code", "status_code"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    msg = str(exc)
    for code in (400, 401, 403, 404, 429, 500, 503, 504):
        if re.search(rf"\b{code}\b", msg):
            return code
    return None


def _eh_erro_rede(exc: Exception) -> bool:
    nome = type(exc).__name__.lower()
    msg = str(exc).lower()
    termos = (
        "connection",
        "timeout",
        "timed out",
        "deadline_exceeded",
        "deadline expired",
        "504",
        "getaddrinfo",
        "dns",
        "network",
        "unreachable",
        "connecterror",
    )
    return any(t in nome or t in msg for t in termos)


def _traduzir_erro_gemini(exc: Exception, chave: str = "") -> GeminiIndisponivelError:
    codigo = _codigo_http_erro(exc)
    msg_bruta = _sanitizar_erro(f"{type(exc).__name__}: {exc}", chave)
    low = msg_bruta.lower()
    if codigo in (400, 401, 403) or "api_key_invalid" in low or "permission_denied" in low:
        return GeminiIndisponivelError(
            "Chave da API Gemini inválida ou sem permissão (401/403). Verifique ou substitua a chave em Configurações.",
            causa=msg_bruta,
        )
    if codigo == 404 or "not_found" in low:
        return GeminiIndisponivelError(
            "Modelo Gemini indisponível (404). Abra o modal do Gemini para escolher um modelo ativo ou use 'Automático'.",
            causa=msg_bruta,
        )
    if codigo == 429 or "resource_exhausted" in low or "quota" in low:
        return GeminiIndisponivelError(
            "Cota da API Gemini esgotada ou limite de requisições atingido (429). Aguarde alguns instantes ou verifique seu plano no Google AI Studio.",
            causa=msg_bruta,
        )
    if _eh_erro_rede(exc):
        return GeminiIndisponivelError(
            "Sem conexão com a API do Gemini (rede indisponível ou tempo limite de 180s excedido). Verifique sua internet ou use o motor local.",
            causa=msg_bruta,
        )
    return GeminiIndisponivelError(
        "Falha ao chamar a API do Gemini; nenhuma sentença foi gerada por ela.",
        causa=msg_bruta,
    )


def _extrair_finish_reason(resp: Any) -> str:
    cands = getattr(resp, "candidates", None)
    if not cands:
        return ""
    primeiro = cands[0]
    reason = getattr(primeiro, "finish_reason", None)
    if reason is None:
        return ""
    # Enum do google-genai pode ter .name ou str() como "FinishReason.MAX_TOKENS"
    nome = getattr(reason, "name", None) or str(reason)
    return str(nome).split(".")[-1].upper().strip()


def _validar_finish_reason(reason: str) -> None:
    if not reason or reason in ("STOP", "FINISH_REASON_UNSPECIFIED", "OTHER"):
        return
    if reason == "MAX_TOKENS":
        raise GeminiIndisponivelError(
            "A resposta do Gemini foi cortada por atingir o limite de tokens (MAX_TOKENS). "
            "Reduza a densidade do Script ou tente novamente com outro modelo.",
            causa="finish_reason=MAX_TOKENS",
        )
    if reason in ("SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"):
        raise GeminiIndisponivelError(
            f"A resposta do Gemini foi bloqueada pelo filtro de segurança da API ({reason}). "
            "Use o motor local ou o fluxo Copiar Script / Colar Sentença.",
            causa=f"finish_reason={reason}",
        )
    if reason == "RECITATION":
        raise GeminiIndisponivelError(
            "A resposta do Gemini foi interrompida por política de recitação (RECITATION). "
            "Tente gerar novamente ou use o motor local.",
            causa="finish_reason=RECITATION",
        )


def _chamar_gemini_detalhado(script_markdown: str, chave: str, modelo: str) -> Tuple[str, Dict[str, Any]]:
    """Chama a API oficial ``google-genai`` com resolução dinâmica, timeout 180s, retry e checagem de ``finish_reason``."""
    client, types = _criar_cliente_gemini(chave)
    cache_key = _chave_cache_hash(chave)
    try:
        resolucao = resolver_modelo_gemini(client, modelo, preferencia_familia="pro", chave_cache=cache_key)
    except GeminiIndisponivelError:
        raise
    except Exception as exc:
        raise _traduzir_erro_gemini(exc, chave) from exc

    modelo_atual = resolucao["modelo_utilizado"]
    output_limit = resolucao.get("output_token_limit")
    max_tokens = min(_MAX_OUTPUT_TOKENS_PADRAO, output_limit) if output_limit else _MAX_OUTPUT_TOKENS_PADRAO

    ja_tentou_fallback_modelo = False
    tentativas_transitorias = 0
    resp = None

    while True:
        try:
            resp = client.models.generate_content(
                model=modelo_atual,
                contents=script_markdown,
                config=types.GenerateContentConfig(temperature=0.15, max_output_tokens=max_tokens),
            )
            _limpar_falha_timeout(modelo_atual)
            break
        except Exception as exc:
            codigo = _codigo_http_erro(exc)
            eh_404 = codigo == 404 or "not_found" in str(exc).lower()
            eh_504_ou_timeout = codigo == 504 or _eh_erro_rede(exc)
            if codigo in (429, 500, 503) and tentativas_transitorias < 2:
                tentativas_transitorias += 1
                espera = float(2 ** (tentativas_transitorias - 1))
                logger.warning(
                    "Gemini retornou erro transitório (%s); tentativa %d/2 em %.1fs.",
                    codigo,
                    tentativas_transitorias,
                    espera,
                )
                _dormir(espera)
                continue
            if (eh_404 or eh_504_ou_timeout) and not ja_tentou_fallback_modelo:
                ja_tentou_fallback_modelo = True
                if hasattr(getattr(client, "models", None), "list"):
                    try:
                        # Em 404, marca indisponível imediatamente por 1h.
                        # Em 504/timeout, só marca por 1h após 2 falhas seguidas.
                        if eh_404:
                            marcar_modelo_indisponivel(modelo_atual)
                        else:
                            _registrar_falha_timeout(modelo_atual)
                        modelos_atuais = listar_modelos_gemini(client, chave_cache=cache_key, forcar=eh_404)
                        outros = [
                            m
                            for m in modelos_atuais
                            if m["name"] != modelo_atual and not _modelo_esta_indisponivel(m["name"])
                        ]
                        if outros:
                            rec = _escolher_recomendado(outros, preferencia_familia="pro")
                            motivo_cod = "404" if eh_404 else ("504" if codigo == 504 else "timeout")
                            if eh_504_ou_timeout:
                                # Na troca após 504/timeout, usa timeout menor (90s) para não somar 180s + 180s.
                                client, types = _criar_cliente_gemini(chave, timeout_ms=_TIMEOUT_FALLBACK_504_MS)
                                resolucao["motivo_troca"] = (
                                    f"Modelo '{modelo_atual}' falhou ({motivo_cod}); substituído automaticamente por "
                                    f"'{rec['name']}' (timeout de 90s)."
                                )
                            else:
                                resolucao["motivo_troca"] = (
                                    f"Modelo '{modelo_atual}' falhou ({motivo_cod}); substituído automaticamente por "
                                    f"'{rec['name']}'."
                                )
                            modelo_atual = rec["name"]
                            resolucao["modelo_utilizado"] = modelo_atual
                            out_lim_rec = rec.get("output_token_limit")
                            max_tokens = (
                                min(_MAX_OUTPUT_TOKENS_PADRAO, out_lim_rec)
                                if out_lim_rec
                                else _MAX_OUTPUT_TOKENS_PADRAO
                            )
                            continue
                    except Exception:
                        pass
            raise _traduzir_erro_gemini(exc, chave) from exc

    finish_reason = _extrair_finish_reason(resp)
    _validar_finish_reason(finish_reason)

    texto = (getattr(resp, "text", "") or "").strip()
    if not texto:
        raise GeminiIndisponivelError("A API do Gemini respondeu sem texto; nenhuma sentença foi gerada.")

    meta = {
        "modelo_solicitado": resolucao["modelo_solicitado"],
        "modelo_utilizado": modelo_atual,
        "motivo_troca": resolucao.get("motivo_troca"),
        "finish_reason": finish_reason or "STOP",
    }
    return texto, meta


def _chamar_gemini(script_markdown: str, chave: str, modelo: str) -> str:
    """Chama a API oficial (google-genai). Levanta ``GeminiIndisponivelError`` em qualquer falha."""
    texto, _ = _chamar_gemini_detalhado(script_markdown, chave, modelo)
    return texto


def listar_modelos_disponiveis_gemini(api_key: str = "") -> Dict[str, Any]:
    """Consulta os modelos ativos na conta do usuário sem enviar nenhum dado do processo."""
    chave = resolver_chave_gemini(api_key)
    if not chave:
        return {"modelos": [], "recomendado": None, "chave_configurada": False}
    try:
        client, _ = _criar_cliente_gemini(chave)
        res = resolver_modelo_gemini(client, "auto", preferencia_familia="pro", chave_cache=_chave_cache_hash(chave))
        return {
            "modelos": res["modelos"],
            "recomendado": res["modelo_utilizado"],
            "chave_configurada": True,
        }
    except GeminiIndisponivelError as exc:
        return {
            "modelos": [],
            "recomendado": None,
            "chave_configurada": True,
            "erro": _sanitizar_erro(exc.mensagem, chave),
        }
    except Exception as exc:
        traduzido = _traduzir_erro_gemini(exc, chave)
        return {
            "modelos": [],
            "recomendado": None,
            "chave_configurada": True,
            "erro": traduzido.mensagem,
        }


# ------------------------------------------------------------------------------
# Segmentação do texto externo
# ------------------------------------------------------------------------------


def _segmentar_secoes(texto: str) -> Dict[str, str]:
    """
    Segmenta um texto integral de sentença nas 6 seções de ``SentencaGerada``. Seções não
    localizadas ficam VAZIAS (nunca são preenchidas silenciosamente com outro texto).
    """
    secoes = {k: "" for k in SECOES_ORDEM}
    limpo = (texto or "").strip()
    if not limpo:
        return secoes

    m_rel = re.search(r"(?:^|\n)\s*(I\s*[-–—]\s*RELAT[ÓO]RIO\b[\s\S]*)", limpo, flags=re.IGNORECASE)
    if not m_rel:
        secoes["merito"] = limpo
        return secoes

    secoes["cabecalho"] = limpo[: m_rel.start()].strip()
    resto_rel = m_rel.group(1).strip()

    m_fund = re.search(r"(?:^|\n)\s*(II\s*[-–—]\s*FUNDAMENTA[ÇC][ÃA]O\b[\s\S]*)", resto_rel, flags=re.IGNORECASE)
    if not m_fund:
        m_disp_sem_fund = re.search(r"(?:^|\n)\s*(III\s*[-–—]\s*DISPOSITIVO\b[\s\S]*)", resto_rel, flags=re.IGNORECASE)
        if m_disp_sem_fund:
            secoes["relatorio"] = resto_rel[: m_disp_sem_fund.start()].strip()
            secoes["dispositivo"] = m_disp_sem_fund.group(1).strip()
        else:
            secoes["relatorio"] = resto_rel
        return secoes

    secoes["relatorio"] = resto_rel[: m_fund.start()].strip()
    resto_fund = m_fund.group(1).strip()

    m_disp = re.search(r"(?:^|\n)\s*(III\s*[-–—]\s*DISPOSITIVO\b[\s\S]*)", resto_fund, flags=re.IGNORECASE)
    if m_disp:
        bloco_fund = resto_fund[: m_disp.start()].strip()
        secoes["dispositivo"] = m_disp.group(1).strip()
    else:
        bloco_fund = resto_fund

    m_dos = re.search(
        r"(?:^|\n)\s*((?:3|2\.6)\.\s+Da\s+Dosimetria[\s\S]*)",
        bloco_fund,
        flags=re.IGNORECASE,
    )
    if m_dos:
        bloco_pre_mer = bloco_fund[: m_dos.start()].strip()
        secoes["dosimetria"] = m_dos.group(1).strip()
    else:
        bloco_pre_mer = bloco_fund

    m_mer = re.search(r"(?:^|\n)\s*(2\.\s+Do\s+M[ée]rito[\s\S]*)", bloco_pre_mer, flags=re.IGNORECASE)
    if m_mer:
        secoes["preliminares"] = bloco_pre_mer[: m_mer.start()].strip()
        secoes["merito"] = m_mer.group(1).strip()
    else:
        # Aceita também estrutura dos modelos .docx com "II – FUNDAMENTAÇÃO\n\n2.1..."
        m_sub_mer = re.search(r"(?:^|\n)\s*(2\.1\.\s+[\s\S]*)", bloco_pre_mer, flags=re.IGNORECASE)
        if m_sub_mer:
            secoes["preliminares"] = bloco_pre_mer[: m_sub_mer.start()].strip()
            secoes["merito"] = m_sub_mer.group(1).strip()
        else:
            secoes["merito"] = bloco_pre_mer
    return secoes


def _montar_texto(secoes: Dict[str, str]) -> str:
    texto = "\n\n".join(secoes[n] for n in SECOES_ORDEM if (secoes.get(n) or "").strip())
    return converter_mencoes_textuais_para_extenso(mascarar_e_padronizar_datas_texto(texto))


# ------------------------------------------------------------------------------
# Importação + auditoria (fluxo Web e API)
# ------------------------------------------------------------------------------


def importar_e_auditar_sentenca_externa(
    case_bundle: Dict[str, Any],
    texto_sentenca_externa: str,
    custom_dosimetria_override: Optional[Dict[str, Any]] = None,
    auto_blindar_auditoria: bool = False,
    origem_label: str = "Gemini Web/Pro (Script Estruturado Py + Despseudonimização + Auditoria 4 Camadas)",
    *,
    mapa_reversao: Optional[Dict[str, str]] = None,
    mapa_hash: Optional[str] = None,
    resultado_motores: ResultadoMotores | None = None,
) -> Tuple[SentencaGerada, RelatorioAuditoriaSentenca, Dict[str, Any]]:
    """
    Recebe uma sentença gerada externamente (ex.: colada do Gemini Web/Pro a partir do Script Py),
    restaura os nomes reais (despseudonimização reversível), aplica a formatação forense e executa o
    Auditor Anti-Alucinação (4 camadas + conteúdo, inclusive penas x dosimetria calculada).

    ``auto_blindar_auditoria=True`` (somente quando solicitado) substitui as seções reprovadas pelo
    modelo determinístico e SINALIZA a substituição (laudo ``secoes_fallback``, telemetria e rótulo).

    ``mapa_reversao``/``mapa_hash``: o mapa emitido com o Script; se ``mapa_hash`` for informado e não
    conferir, levanta ``ValueError``. Sem mapa, ele é reconstruído de forma determinística.
    """
    settings = load_settings()
    # exigir_veredicto=None: em caso REAL o veredicto de cada crime e obrigatorio (422
    # decisao_judicial_pendente); so o demo simula, com a presuncao sinalizada.
    motores = resultado_motores or executar_motores_completo(case_bundle, custom_dosimetria_override)
    ctx = motores.ctx
    pecas = motores.pecas
    meta = motores.meta
    dosimetria_global = motores.dosimetria_global
    prescricao = motores.prescricao

    if mapa_reversao:
        mapa_usado = {str(k): str(v) for k, v in mapa_reversao.items()}
        if mapa_hash and calcular_hash_mapa(mapa_usado) != str(mapa_hash).strip():
            raise ValueError(
                "O mapa de pseudonimização informado não confere com o hash emitido junto do Script."
            )
        origem_mapa = "informado"
    else:
        mapa_usado = construir_mapa_do_contexto(ctx)
        origem_mapa = "reconstruido"

    texto_restaurado = despseudonimizar_texto(texto_sentenca_externa or "", mapa_usado)
    texto_restaurado = converter_mencoes_textuais_para_extenso(
        mascarar_e_padronizar_datas_texto(texto_restaurado)
    )

    secoes = _segmentar_secoes(texto_restaurado)
    secoes_ausentes = [n for n in SECOES_ORDEM if not secoes[n].strip()]
    texto_montado = _montar_texto(secoes)

    ids_midias = list(ctx.ids_midias)
    dosimetria_dados = dosimetria_global.model_dump(mode="json")
    acessorias = motores.plano.decisoes_acessorias
    if acessorias is not None:
        dosimetria_dados["decisoes_acessorias"] = acessorias.dados_auditoria()
    if motores.presume_condenacao:
        dosimetria_dados["simulacao_presume_condenacao"] = True

    dosimetria_auditoria = dosimetria_para_auditoria(dosimetria_dados, bool(ctx.is_demo))

    auditoria_inicial = auditar_sentenca(
        texto_sentenca=texto_montado,
        pecas_autos=pecas,
        teses_defesa=meta.teses_defesa,
        ids_midias=ids_midias,
        dosimetria_dados=dosimetria_auditoria,
    )

    secoes_substituidas: List[str] = []
    auditoria_final = auditoria_inicial
    fallback: Dict[str, str] = {}

    def _substituir(nome: str) -> None:
        if not fallback:
            sentenca_local = gerar_sentenca_estruturada(
                case_bundle,
                custom_dosimetria_override=custom_dosimetria_override,
                use_ollama_if_available=False,
                plano=motores.plano,
            )
            fallback.update({n: getattr(sentenca_local, f"secao_{n}") for n in SECOES_ORDEM})
        if secoes[nome] != fallback[nome]:
            secoes[nome] = fallback[nome]
            if nome not in secoes_substituidas:
                secoes_substituidas.append(nome)

    if not ctx.is_demo:
        _substituir("merito")
        if not origem_label.startswith("Edição Direta"):
            _substituir("dosimetria")
        _substituir("dispositivo")
        texto_montado = _montar_texto(secoes)
        auditoria_final = auditar_sentenca(
            texto_sentenca=texto_montado,
            pecas_autos=pecas,
            teses_defesa=meta.teses_defesa,
            ids_midias=ids_midias,
            dosimetria_dados=dosimetria_auditoria,
            secoes_fallback=secoes_substituidas,
        )

    if auto_blindar_auditoria and not auditoria_final.aprovada_sem_alucinacao:
        if not secoes["cabecalho"].strip():
            _substituir("cabecalho")
        for nome_sec in SECOES_ORDEM[1:]:
            if not secoes[nome_sec].strip():
                _substituir(nome_sec)
                continue
            r_sec = auditar_secao(nome_sec, secoes[nome_sec], pecas, meta.teses_defesa, ids_midias=ids_midias)
            if not r_sec.aprovado:
                _substituir(nome_sec)
        if not auditoria_final.conformidade_oito_circunstancias_art59:
            _substituir("dosimetria")
        if not auditoria_final.conformidade_art21_lmp:
            _substituir("dispositivo")
        if auditoria_final.penas_divergentes:
            _substituir("dispositivo")

        texto_montado = _montar_texto(secoes)
        auditoria_final = auditar_sentenca(
            texto_sentenca=texto_montado,
            pecas_autos=pecas,
            teses_defesa=meta.teses_defesa,
            ids_midias=ids_midias,
            dosimetria_dados=dosimetria_auditoria,
            secoes_fallback=secoes_substituidas,
        )
        # Requisitos globais remanescentes (estrutura, teses inter-seções, IDs/citações, penas).
        if not auditoria_final.aprovada_sem_alucinacao:
            if not auditoria_final.conformidade_art381_cpp:
                _substituir("relatorio")
            if auditoria_final.teses_omitidas:
                _substituir("preliminares")
                _substituir("merito")
            if auditoria_final.penas_divergentes:
                _substituir("dispositivo")
            if auditoria_final.ids_inexistentes or auditoria_final.citacoes_nao_autorizadas:
                for nome_sec in SECOES_ORDEM[1:]:
                    txt_cur = secoes[nome_sec]
                    if any(i in txt_cur for i in auditoria_final.ids_inexistentes) or any(
                        c in txt_cur for c in auditoria_final.citacoes_nao_autorizadas
                    ):
                        _substituir(nome_sec)
            texto_montado = _montar_texto(secoes)
            auditoria_final = auditar_sentenca(
                texto_sentenca=texto_montado,
                pecas_autos=pecas,
                teses_defesa=meta.teses_defesa,
                ids_midias=ids_midias,
                dosimetria_dados=dosimetria_auditoria,
                secoes_fallback=secoes_substituidas,
            )

    rotulo = origem_label
    if secoes_substituidas:
        rotulo = (
            f"{origem_label} — seções substituídas pelo modelo determinístico: "
            f"{', '.join(secoes_substituidas)}"
        )

    sentenca = SentencaGerada(
        numero_processo=meta.numero_processo or "",
        comarca_juizado=f"{settings.juizado_nome} — {settings.comarca_nome}",
        ofendida=meta.nome_ofendida or "",
        reu=meta.nome_reu or "",
        data_prolatacao=formatar_data_br(motores.plano.data_sentenca),
        secao_cabecalho=secoes["cabecalho"],
        secao_relatorio=secoes["relatorio"],
        secao_preliminares=secoes["preliminares"],
        secao_merito=secoes["merito"],
        secao_dosimetria=secoes["dosimetria"],
        secao_dispositivo=secoes["dispositivo"],
        texto_completo=texto_montado,
        dosimetria_dados=dosimetria_dados,
        prescricao_dados=prescricao.model_dump(mode="json"),
        motor_utilizado=rotulo,
        simulacao_presume_condenacao=bool(motores.presume_condenacao),
        decisoes_crimes=motores.decisoes_crimes,
        marcadores_pendentes=list(auditoria_final.marcadores_pendentes),
    )

    telemetria = {
        "despseudonimizacao_aplicada": bool(mapa_usado),
        "tokens_restaurados": sorted(mapa_usado.keys()),
        "mapa_pseudonimizacao": origem_mapa,
        "mapa_hash": calcular_hash_mapa(mapa_usado) if mapa_usado else "",
        "score_inicial_antes_blindagem": auditoria_inicial.score_confiabilidade,
        "score_final_auditado": auditoria_final.score_confiabilidade,
        "blindagem_solicitada": bool(auto_blindar_auditoria),
        "reparo_deterministico_aplicado": bool(secoes_substituidas),
        "secoes_fallback": list(secoes_substituidas),
        "secoes_ausentes_no_texto": [n for n in secoes_ausentes if n not in secoes_substituidas],
        "simulacao_presume_condenacao": bool(motores.presume_condenacao),
        "motores_py": [m.model_dump(mode="json") for m in motores.motores_status],
    }
    return sentenca, auditoria_final, telemetria


# ------------------------------------------------------------------------------
# Geração via API (ou local, honestamente rotulada, quando não há chave)
# ------------------------------------------------------------------------------


def gerar_sentenca_via_gemini_api(
    case_bundle: Dict[str, Any],
    api_key: str = "",
    model_name: str = "",
    pseudonimizar: bool = True,
    custom_dosimetria_override: Optional[Dict[str, Any]] = None,
    densidade: str = "compacto",
    *,
    consentimento_envio_externo: bool = False,
    auto_blindar_auditoria: bool = False,
) -> Tuple[SentencaGerada, RelatorioAuditoriaSentenca, ScriptCasoEstruturado, Dict[str, Any]]:
    """
    Executa os 5 Motores Python para gerar o Script Estruturado do Caso e:

    - com chave do Gemini disponível: exige autorização de envio externo
      (``settings.permitir_envio_externo`` + ``consentimento_envio_externo=True``), envia o Script
      (pseudonimizado por padrão) à API oficial ``google-genai``, despseudonimiza e audita. Falhas
      levantam ``GeminiIndisponivelError`` (sem troca silenciosa de motor);
    - sem chave: gera a sentença localmente pelo motor determinístico (uma única vez), sem enviar
      nada para fora, e a audita — com rótulo e telemetria dizendo que o Gemini NÃO foi usado.
    """
    settings = load_settings()
    resolved_key = resolver_chave_gemini(api_key)
    cfg_model = (getattr(settings, "gemini_model", "auto") or "auto").strip()
    # Valores legados gemini-2.5-* salvos nas configurações caem em "auto" para evitar falhas repetidas.
    if cfg_model.lower().startswith("gemini-2.5-"):
        cfg_model = "auto"
    resolved_model = (model_name or "").strip() or cfg_model or "auto"

    if resolved_key:
        # A autorização é verificada ANTES de qualquer processamento destinado ao envio.
        verificar_autorizacao_envio_externo(consentimento_envio_externo, settings)

    motores = executar_motores_completo(case_bundle, custom_dosimetria_override)
    script_obj = gerar_script_estruturado_caso(
        case_bundle=case_bundle,
        custom_dosimetria_override=custom_dosimetria_override,
        pseudonimizar=pseudonimizar,
        densidade=densidade,
        resultado_motores=motores,
    )

    if resolved_key:
        texto_bruto_gemini, meta_chamada = _chamar_gemini_detalhado(
            script_obj.script_markdown, resolved_key, resolved_model
        )
        modelo_real = meta_chamada["modelo_utilizado"]
        sentenca, auditoria, telemetria = importar_e_auditar_sentenca_externa(
            case_bundle=case_bundle,
            texto_sentenca_externa=texto_bruto_gemini,
            custom_dosimetria_override=custom_dosimetria_override,
            auto_blindar_auditoria=auto_blindar_auditoria,
            origem_label=f"Gemini API ({modelo_real}) sobre o Script dos 5 Motores Python",
            mapa_reversao=script_obj.mapa_reversao or None,
            mapa_hash=script_obj.mapa_hash or None,
            resultado_motores=motores,
        )
        telemetria["modo_gemini"] = "API_DIRETA_GOOGLE_GENAI"
        telemetria["gemini_utilizado"] = True
        telemetria["envio_externo"] = True
        telemetria["modelo_solicitado"] = meta_chamada["modelo_solicitado"]
        telemetria["modelo_utilizado"] = modelo_real
        telemetria["modelo_gemini"] = modelo_real
        telemetria["motivo_troca"] = meta_chamada.get("motivo_troca")
        telemetria["finish_reason"] = meta_chamada.get("finish_reason")
        telemetria["pseudonimizacao_ativa"] = pseudonimizar
        if not pseudonimizar:
            telemetria["aviso_privacidade"] = (
                "O Script foi enviado SEM pseudonimização (nomes e números reais), conforme solicitado."
            )
        return sentenca, auditoria, script_obj, telemetria

    # Sem chave: síntese local determinística, executada UMA vez, auditada diretamente.
    sentenca = gerar_sentenca_estruturada(
        case_bundle=case_bundle,
        custom_dosimetria_override=custom_dosimetria_override,
        use_ollama_if_available=False,
        plano=motores.plano,
    )
    auditoria = auditar_sentenca(
        texto_sentenca=sentenca.texto_completo,
        pecas_autos=motores.pecas,
        teses_defesa=motores.meta.teses_defesa,
        ids_midias=list(motores.ctx.ids_midias),
        dosimetria_dados=dosimetria_para_auditoria(sentenca.dosimetria_dados, bool(motores.ctx.is_demo)),
    )
    sentenca.motor_utilizado = (
        "Motor Determinístico Local (Gemini NÃO utilizado: nenhuma chave configurada; nenhum dado enviado)"
    )
    telemetria: Dict[str, Any] = {
        "modo_gemini": "SINTESE_LOCAL_SEM_GEMINI",
        "gemini_utilizado": False,
        "envio_externo": False,
        "modelo_gemini": None,
        "pseudonimizacao_ativa": pseudonimizar,
        "score_final_auditado": auditoria.score_confiabilidade,
        "reparo_deterministico_aplicado": False,
        "secoes_fallback": [],
        "motores_py": [m.model_dump(mode="json") for m in motores.motores_status],
        "aviso_api": (
            "Nenhuma chave do Gemini configurada: a sentença foi gerada localmente pelo motor determinístico. "
            "Você também pode copiar o Script e usá-lo no Gemini Web/Pro."
        ),
    }
    return sentenca, auditoria, script_obj, telemetria
