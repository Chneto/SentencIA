"""Segurança da API local: token de sessão, checagem de Host/Origin, porta e logging.

Modelo de ameaça: o servidor escuta apenas em ``127.0.0.1``, mas qualquer página aberta no
navegador do usuário pode tentar requisições para ``http://127.0.0.1:<porta>`` (CSRF) ou usar
*DNS rebinding* para ler respostas. Defesas:

1. **Token de sessão** (``secrets.token_urlsafe``) gerado a cada inicialização e injetado no
   ``index.html`` servido em ``/`` como ``<meta name="jvdfm-token" content="...">``. Toda rota
   ``/api/*`` exige o header ``X-JVDFM-Token`` (exceto ``GET /api/health``, que é mínimo). Páginas
   de outras origens não conseguem ler o ``index.html`` (sem CORS para elas), logo não obtêm o token.
2. **Host permitido** (127.0.0.1, localhost, [::1] e ``testserver`` do TestClient): bloqueia DNS
   rebinding, pois o navegador envia o domínio do atacante no header ``Host``.
3. **Origin**: quando presente, deve ser uma origem de loopback.
4. **Sem CORS**: o frontend e same-origin; ``Origin`` (quando presente) deve igualar o Host e
   ``Sec-Fetch-Site`` cross-site/same-site e recusado tambem em ``/`` e ``/static``.

Testes: ``JVDFM_DISABLE_TOKEN=1`` desativa a exigência do token SOMENTE quando o processo roda sob
pytest (``"pytest" in sys.modules``); fora dele a variável é ignorada. Prefira enviar o token real
(``get_session_token()``) nos testes.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import secrets
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - requires-python >= 3.11
    tomllib = None  # type: ignore[assignment]

from fastapi import Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

_VERSAO_FALLBACK = "2.1.0"


def ler_versao_app(raiz: Optional[Path] = None) -> str:
    """Lê a versão do aplicativo em ``pyproject.toml`` (fonte única de verdade)."""
    base = raiz or Path(__file__).resolve().parent.parent.parent
    pyproject = base / "pyproject.toml"
    try:
        texto = pyproject.read_text(encoding="utf-8")
        if tomllib is not None:
            versao = str(tomllib.loads(texto).get("project", {}).get("version", "")).strip()
            if versao:
                return versao
        m = re.search(r'^version\s*=\s*"([^"]+)"', texto, re.M)
        if m:
            return m.group(1).strip()
    except OSError:
        pass
    return _VERSAO_FALLBACK


VERSAO_APP = ler_versao_app()

TOKEN_HEADER = "X-JVDFM-Token"
TOKEN_META_NAME = "jvdfm-token"
#: Parâmetro de query aceito SOMENTE em GET de download/stream (``<a href>``/``EventSource`` não
#: conseguem enviar headers customizados).
TOKEN_QUERY_PARAM = "jvdfm_token"
PREFIXOS_TOKEN_EM_QUERY = ("/api/export/", "/api/cases/upload-progress", "/api/jobs/")

PORTA_PADRAO = 8000
HOSTS_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
HOST_TESTCLIENT = "testserver"

_ORIGEM_LOOPBACK_RE = re.compile(r"^https?://(?:127\.0\.0\.1|localhost|\[::1\])(?::\d{1,5})?$", re.IGNORECASE)
CORS_ORIGIN_REGEX = r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d{1,5})?$"

#: Rotas /api acessíveis sem token (status mínimo, sem dados do processo nem configurações).
ROTAS_PUBLICAS = frozenset({("GET", "/api/health"), ("HEAD", "/api/health")})

_SESSION_TOKEN: str = secrets.token_urlsafe(32)

_LOG_HANDLER_MARCA = "_jvdfm_app_log"


# ----------------------------------------------------------------------------------------------
# Token
# ----------------------------------------------------------------------------------------------
def get_session_token() -> str:
    """Token desta execução do servidor (muda a cada inicialização)."""
    return _SESSION_TOKEN


def token_headers() -> dict:
    """Header pronto para clientes (ex.: ``TestClient(app, headers=token_headers())``)."""
    return {TOKEN_HEADER: _SESSION_TOKEN}


def exigencia_token_desativada() -> bool:
    """``JVDFM_DISABLE_TOKEN=1`` só tem efeito dentro do pytest."""
    return os.environ.get("JVDFM_DISABLE_TOKEN", "").strip() == "1" and "pytest" in sys.modules


def _comparar(fornecido: Optional[str]) -> bool:
    if not fornecido:
        return False
    return hmac.compare_digest(fornecido.encode("utf-8"), _SESSION_TOKEN.encode("utf-8"))


def token_valido(request: Request) -> bool:
    if _comparar(request.headers.get(TOKEN_HEADER)):
        return True
    path = request.url.path
    if request.method in ("GET", "HEAD") and path.startswith(PREFIXOS_TOKEN_EM_QUERY):
        return _comparar(request.query_params.get(TOKEN_QUERY_PARAM))
    return False


def injetar_token_html(html: str, token: Optional[str] = None) -> str:
    """Insere (ou substitui) ``<meta name="jvdfm-token" content="...">`` no ``<head>``."""
    token = token or _SESSION_TOKEN
    meta = f'<meta name="{TOKEN_META_NAME}" content="{token}">'
    padrao_existente = re.compile(r'<meta\s+name=["\']' + re.escape(TOKEN_META_NAME) + r'["\'][^>]*>', re.IGNORECASE)
    if padrao_existente.search(html):
        return padrao_existente.sub(meta, html, count=1)
    m_head = re.search(r"<head\b[^>]*>", html, re.IGNORECASE)
    if m_head:
        return html[: m_head.end()] + "\n    " + meta + html[m_head.end():]
    return meta + "\n" + html


# ----------------------------------------------------------------------------------------------
# Host / Origin
# ----------------------------------------------------------------------------------------------
def _hosts_extras() -> set[str]:
    bruto = os.environ.get("JVDFM_ALLOWED_HOSTS", "")
    return {h.strip().lower() for h in bruto.split(",") if h.strip()}


def hosts_permitidos() -> frozenset[str]:
    # "testserver" (TestClient) so existe sob pytest: em producao um nome de rotulo unico seria
    # alvo de DNS rebinding (LLMNR/mDNS/sufixo de busca).
    base = set(HOSTS_LOOPBACK)
    if "pytest" in sys.modules:
        base.add(HOST_TESTCLIENT)
    return frozenset(base | _hosts_extras())


def extrair_hostname(host_header: Optional[str]) -> str:
    """``"[::1]:8000"`` → ``"::1"``; ``"localhost:8000"`` → ``"localhost"``."""
    host = (host_header or "").strip().lower()
    if not host:
        return ""
    if host.startswith("["):
        fim = host.find("]")
        return host[1:fim] if fim > 0 else host
    if host.count(":") == 1:
        return host.split(":", 1)[0]
    return host


def host_permitido(host_header: Optional[str]) -> bool:
    return extrair_hostname(host_header) in hosts_permitidos()


def origem_permitida(origin: Optional[str]) -> bool:
    if origin is None or origin == "":
        return True
    if _ORIGEM_LOOPBACK_RE.match(origin.strip()):
        return True
    if "pytest" not in sys.modules:
        return False
    # O TestClient não envia Origin; se um teste enviar, aceita a própria origem do TestClient.
    return origin.strip().lower() in {f"http://{HOST_TESTCLIENT}", f"https://{HOST_TESTCLIENT}"}


def origem_mesma_origem(origin: Optional[str], host_header: Optional[str]) -> bool:
    """``Origin`` ausente, ou igual a ``<esquema>://<Host>`` desta requisicao (same-origin)."""
    if origin is None or origin == "":
        return True
    o = origin.strip().lower()
    host = (host_header or "").strip().lower()
    return bool(host) and o in {f"http://{host}", f"https://{host}"}


def requisicao_cross_site(request: Request) -> bool:
    """``Sec-Fetch-Site`` cross-site/same-site (outra porta em localhost e same-site), exceto
    navegacao de documento de nivel superior (nao permite ler a resposta)."""
    site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if site not in ("cross-site", "same-site"):
        return False
    navegacao = (
        (request.headers.get("sec-fetch-mode") or "").strip().lower() == "navigate"
        and (request.headers.get("sec-fetch-dest") or "").strip().lower() == "document"
    )
    return not navegacao

# ----------------------------------------------------------------------------------------------
# Porta
# ----------------------------------------------------------------------------------------------
def get_port(valor: Optional[str] = None) -> int:
    """Porta do servidor: ``JVDFM_PORT`` (1–65535) ou 8000."""
    bruto = os.environ.get("JVDFM_PORT", "") if valor is None else valor
    try:
        porta = int(str(bruto).strip())
    except (TypeError, ValueError):
        return PORTA_PADRAO
    return porta if 1 <= porta <= 65535 else PORTA_PADRAO


# ----------------------------------------------------------------------------------------------
# Middleware
# ----------------------------------------------------------------------------------------------
def _erro(status: int, detail: str, erro: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail, "erro": erro})


async def middleware_seguranca(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Host permitido em todas as rotas; Origin de loopback e token em ``/api/*``."""
    if not host_permitido(request.headers.get("host")):
        logger.warning("Requisição recusada: Host não permitido (%r).", request.headers.get("host"))
        return _erro(400, "Host não permitido. Acesse o sistema por http://127.0.0.1.", "host_nao_permitido")

    # Origin (quando presente) precisa ser a propria origem; o frontend e same-origin e nao usa
    # CORS. Vale tambem para "/" e "/static" (de onde o token poderia ser lido).
    if not origem_mesma_origem(request.headers.get("origin"), request.headers.get("host")) or requisicao_cross_site(request):
        logger.warning("Requisição recusada: origem cruzada (Origin=%r).", request.headers.get("origin"))
        return _erro(403, "Origem não permitida.", "origem_nao_permitida")

    path = request.url.path or ""
    if path.startswith("/api/") or path == "/api":
        if (request.method.upper(), path.rstrip("/") or path) not in ROTAS_PUBLICAS:
            if not origem_permitida(request.headers.get("origin")):
                logger.warning("Requisição recusada: Origin não permitida (%r).", request.headers.get("origin"))
                return _erro(403, "Origem não permitida.", "origem_nao_permitida")
            if not exigencia_token_desativada() and not token_valido(request):
                return _erro(
                    401,
                    "Token de sessão ausente ou inválido. Recarregue a página do sistema (o token muda a cada "
                    "inicialização do servidor).",
                    "token_invalido",
                )

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ----------------------------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------------------------
def configurar_logging(
    logs_dir: Optional[Path] = None,
    *,
    forcar: bool = False,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
) -> Optional[Path]:
    """Log rotativo em ``<logs_dir>/app.log`` (idempotente).

    Sob pytest não grava arquivo (evita poluir ``data/logs``), salvo ``forcar=True`` ou
    ``JVDFM_LOG_EM_TESTES=1``. Retorna o caminho do log configurado (ou ``None``).
    """
    if "pytest" in sys.modules and not forcar and os.environ.get("JVDFM_LOG_EM_TESTES") != "1":
        return None
    if logs_dir is None:
        from app.core import config as core_config

        logs_dir = Path(core_config.LOGS_DIR)
    destino = Path(logs_dir) / "app.log"
    raiz = logging.getLogger()
    for h in raiz.handlers:
        if getattr(h, _LOG_HANDLER_MARCA, None) == str(destino):
            return destino
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(destino, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    except OSError as err:  # pragma: no cover - disco somente leitura
        logger.warning("Não foi possível configurar o log em arquivo (%s): %s", destino, err)
        return None
    setattr(handler, _LOG_HANDLER_MARCA, str(destino))
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    handler.setLevel(logging.INFO)
    raiz.addHandler(handler)
    if raiz.level == logging.NOTSET or raiz.level > logging.INFO:
        raiz.setLevel(logging.INFO)
    return destino


def remover_handlers_log(destinos: Iterable[Path] | None = None) -> None:
    """Remove os handlers de arquivo instalados por ``configurar_logging`` (usado em testes)."""
    alvo = {str(d) for d in destinos} if destinos else None
    raiz = logging.getLogger()
    for h in list(raiz.handlers):
        marca = getattr(h, _LOG_HANDLER_MARCA, None)
        if marca and (alvo is None or marca in alvo):
            raiz.removeHandler(h)
            try:
                h.close()
            except Exception:  # pragma: no cover
                pass
