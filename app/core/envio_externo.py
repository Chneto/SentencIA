"""Autorização e tratamento de conteúdo de autos destinado a serviços externos."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator, Mapping
from urllib.parse import urlsplit

from app.core.config import ollama_url_loopback
from app.core.errors import EnvioExternoNaoAutorizadoError


def motivo_bloqueio_envio_externo(settings: object, consentimento: bool) -> str:
    if not bool(getattr(settings, "permitir_envio_externo", False)):
        return "envio externo desabilitado nas configurações (permitir_envio_externo=False)"
    if consentimento is not True:
        return "consentimento explícito ausente nesta operação"
    return ""


_AutorizacaoOllama = tuple[str, Mapping[str, str]]
_autorizacao_ollama: ContextVar[_AutorizacaoOllama | None] = ContextVar("autorizacao_ollama", default=None)


def _destino(url: str) -> str:
    return (url or "").strip().rstrip("/")


def destino_publico_ollama(url: str) -> str:
    """Mostra apenas a origem do destino a confirmar, sem credenciais ou caminhos."""
    try:
        partes = urlsplit(_destino(url))
    except ValueError:
        return ""
    if partes.scheme not in ("http", "https") or not partes.hostname:
        return ""
    return f"{partes.scheme}://{partes.netloc.rsplit('@', 1)[-1]}"


@contextmanager
def permissao_ollama_na_operacao(
    settings: object,
    consentimento: bool,
    destino_confirmado: str | None,
    obter_mapa: Callable[[], Mapping[str, str]],
) -> Iterator[None]:
    url = _destino(getattr(settings, "ollama_url", ""))
    permissao = None
    if not ollama_url_loopback(url) and consentimento is True:
        motivo = motivo_bloqueio_envio_externo(settings, consentimento)
        if motivo:
            raise EnvioExternoNaoAutorizadoError(motivo)
        destino = destino_publico_ollama(url)
        if not destino:
            raise EnvioExternoNaoAutorizadoError("O destino do Ollama remoto é inválido.")
        if destino_confirmado and destino_confirmado != destino:
            raise EnvioExternoNaoAutorizadoError("O destino do Ollama mudou. Confirme a operação novamente.")
        permissao = (url, dict(obter_mapa()))
    token = _autorizacao_ollama.set(permissao)
    try:
        yield
    finally:
        _autorizacao_ollama.reset(token)


def preparar_prompt_ollama(
    url: str, settings: object, prompt: str,
) -> tuple[str, Mapping[str, str]] | None:
    """Autoriza apenas o destino da operação e pseudonimiza os trechos enviados para fora."""
    if ollama_url_loopback(url):
        return prompt, {}
    if motivo_bloqueio_envio_externo(settings, _autorizacao_ollama.get() is not None):
        return None
    permissao = _autorizacao_ollama.get()
    if permissao is None or permissao[0] != _destino(url):
        return None

    from app.generation.case_script_engine import detectar_vazamento_pseudonimizacao, pseudonimizar_texto

    mapa = permissao[1]
    externo = pseudonimizar_texto(prompt, mapa)
    if detectar_vazamento_pseudonimizacao(externo, mapa):
        return None
    return externo, mapa


def restaurar_resposta_ollama(texto: str, mapa: Mapping[str, str]) -> str:
    if not mapa:
        return texto
    from app.generation.case_script_engine import despseudonimizar_texto

    return despseudonimizar_texto(texto, mapa)
