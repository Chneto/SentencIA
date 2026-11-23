"""Configuração do sistema: diretórios de dados e preferências do usuário (``user_settings.json``).

- ``JVDFM_DATA_DIR`` (variável de ambiente) permite relocar a pasta ``data/`` — útil para instalar
  fora do OneDrive ou, no macOS, em ``~/Library/Application Support/SentencaJVDFM``.
- A chave da API Gemini NÃO é mais persistida no JSON: fica no cofre do sistema
  (``app.core.secrets``). Arquivos antigos que ainda a contenham são migrados na primeira carga.
- ``save_settings`` grava de forma atômica (arquivo temporário + ``os.replace``).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Literal

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def pasta_sincronizada(caminho: Path, *, home: Path | None = None, ambiente: Dict[str, str] | None = None) -> bool:
    """Heuristica: ``caminho`` esta numa pasta sincronizada com a nuvem (OneDrive, iCloud Drive,
    Dropbox, Google Drive)? Autos, transcricoes e segredos nao podem ir para la sem consentimento."""
    env = os.environ if ambiente is None else ambiente
    try:
        alvo = Path(caminho).resolve()
    except OSError:
        alvo = Path(caminho)
    texto = str(alvo).replace("\\", "/").lower()
    partes = [p for p in texto.split("/") if p]
    if any(p.startswith("onedrive") or p in ("dropbox", "google drive", "icloud drive", "com~apple~clouddocs", "mobile documents") for p in partes):
        return True
    for chave in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        raiz = (env.get(chave) or "").strip()
        if raiz:
            try:
                alvo.relative_to(Path(raiz).resolve())
                return True
            except (ValueError, OSError):
                pass
    # macOS: "Mesa e Documentos no iCloud" move ~/Desktop e ~/Documents para o iCloud Drive.
    casa = (home or Path.home())
    icloud = casa / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    try:
        if icloud.is_dir() and (icloud / "Desktop").is_dir():
            for sub in ("Desktop", "Documents"):
                try:
                    alvo.relative_to((casa / sub).resolve())
                    return True
                except (ValueError, OSError):
                    continue
    except OSError:
        pass
    if "/library/cloudstorage/" in texto:
        return True
    return False


ARQUIVO_PASTA_DADOS = ".pasta_dados"


def pasta_dados_local_segura(*, plataforma: str | None = None, ambiente: Dict[str, str] | None = None, home: Path | None = None) -> Path:
    """Pasta de dados fora de qualquer sincronizacao: ``%LOCALAPPDATA%\\SentencaJVDFM`` (Windows),
    ``~/Library/Application Support/SentencaJVDFM`` (macOS) ou ``~/.local/share/SentencaJVDFM``."""
    plat = plataforma or sys.platform
    env = os.environ if ambiente is None else ambiente
    casa = home or Path.home()
    if plat == "win32":
        base = (env.get("LOCALAPPDATA") or "").strip()
        return (Path(base) if base else casa / "AppData" / "Local") / "SentencaJVDFM"
    if plat == "darwin":
        return casa / "Library" / "Application Support" / "SentencaJVDFM"
    return Path((env.get("XDG_DATA_HOME") or "").strip() or casa / ".local" / "share") / "SentencaJVDFM"


def ler_pasta_dados_configurada(base_dir: Path) -> Path | None:
    """Lê ``<base_dir>/.pasta_dados`` (uma linha: caminho absoluto gravado pelo instalador)."""
    marcador = Path(base_dir) / ARQUIVO_PASTA_DADOS
    if not marcador.is_file():
        return None
    try:
        for linha in marcador.read_text(encoding="utf-8-sig").splitlines():
            limpa = linha.strip()
            if not limpa or limpa.startswith("#"):
                continue
            candidato = Path(os.path.expandvars(limpa)).expanduser()
            if candidato.is_absolute():
                return candidato.resolve()
            return None
    except OSError:
        return None
    return None


def _dados_legados_em_uso(pasta: Path) -> bool:
    """True se ``pasta`` ja guarda autos/configuracoes (nao abandona dados de instalacoes existentes)."""
    try:
        if (pasta / "user_settings.json").is_file():
            return True
        cases = pasta / "cases"
        if cases.is_dir():
            return any(p.name != "_arquivados" and not p.name.startswith(".") for p in cases.iterdir())
    except OSError:
        return False
    return False


def _resolver_data_dir(
    valor_env: str | None = None,
    *,
    base_dir: Path | None = None,
    plataforma: str | None = None,
    ambiente: Dict[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve a pasta de dados.

    1. ``JVDFM_DATA_DIR`` (se definida e nao vazia);
    2. ``<base_dir>/.pasta_dados`` (gravado pelo instalador, quando existir e contiver caminho absoluto);
    3. ``<projeto>/data``, EXCETO se o projeto estiver numa pasta sincronizada (OneDrive/iCloud...):
       nesse caso, pasta local (``%LOCALAPPDATA%/SentencaJVDFM`` / ``~/Library/Application Support/...``),
       a menos que ``<projeto>/data`` ja guarde dados (instalacao existente: nao os abandona; avisa).
    """
    env = os.environ if ambiente is None else ambiente
    bruto = env.get("JVDFM_DATA_DIR") if valor_env is None else valor_env
    if bruto and bruto.strip():
        return Path(os.path.expandvars(bruto.strip())).expanduser().resolve()
    base = base_dir or BASE_DIR
    configurada = ler_pasta_dados_configurada(base)
    if configurada is not None:
        return configurada
    padrao = base / "data"
    if pasta_sincronizada(padrao, home=home, ambiente=ambiente):
        if _dados_legados_em_uso(padrao):
            logger.warning(
                "A pasta de dados %s esta numa pasta sincronizada com a nuvem (OneDrive/iCloud): autos e "
                "transcricoes sao enviados ao provedor. Defina JVDFM_DATA_DIR para uma pasta local.",
                padrao,
            )
            return padrao
        return pasta_dados_local_segura(plataforma=plataforma, ambiente=ambiente, home=home)
    return padrao

DATA_DIR = _resolver_data_dir()
CASES_DIR = DATA_DIR / "cases"
KNOWLEDGE_DIR = DATA_DIR / "knowledge_base"
TEMPLATES_DIR = DATA_DIR / "templates_gabinete"
EXPORTS_DIR = DATA_DIR / "exports"
LOGS_DIR = DATA_DIR / "logs"
ARCHIVE_DIR = CASES_DIR / "_arquivados"  # criado sob demanda por quem arquiva
SETTINGS_FILE = DATA_DIR / "user_settings.json"

for _d in (DATA_DIR, CASES_DIR, KNOWLEDGE_DIR, TEMPLATES_DIR, EXPORTS_DIR, LOGS_DIR):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except OSError as _err:  # pragma: no cover - disco somente leitura etc.
        logger.warning("Não foi possível criar o diretório %s: %s", _d, _err)

# Chaves antigas que guardavam segredos no JSON e devem ir para o cofre.
_CHAVES_SEGREDO_LEGADAS = ("gemini_api_key",)

#: Incrementado a cada ``save_settings`` para invalidar caches derivados das configurações.
SETTINGS_VERSION: int = 0


class UserSettings(BaseModel):
    """Preferências persistidas. Chaves extras no JSON são ignoradas (leitura tolerante)."""

    tribunal_nome: str = "TRIBUNAL DE JUSTIÇA DO ESTADO"
    juizado_nome: str = "1º JUIZADO DE VIOLÊNCIA DOMÉSTICA E FAMILIAR CONTRA A MULHER"
    comarca_nome: str = "COMARCA DA CAPITAL"
    magistrado_nome: str = "Juiz(a) de Direito Titular"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "auto"
    ollama_num_ctx: int = 8192
    whisper_model: str = "auto"
    whisper_preprocessar: bool = True
    whisper_device: Literal["auto", "cuda", "cpu"] = "auto"
    laya_enabled: bool = True
    criterio_primeira_fase: Literal["um_oitavo_intervalo", "um_sexto_minima"] = "um_oitavo_intervalo"
    aplicar_sumula_231_stj: bool = True
    aplicar_sumula_444_stj: bool = True
    fonte_docx: str = "Times New Roman"
    tamanho_fonte_docx: int = 12
    formato_referencia_pje: Literal["id_hifen", "id_ponto"] = "id_hifen"
    tema_interface: Literal["dark", "light", "system"] = "dark"
    tema_design: Literal["original", "classico", "moderno", "cockpit"] = "original"
    vram_limit_mb: int = 6144
    modo_gpu: Literal["normal", "moderado"] = "normal"
    gemini_model: str = "auto"
    gemini_pseudonimizar: bool = True
    permitir_envio_externo: bool = False
    max_upload_mb_pdf: int = 600
    max_upload_mb_midia: int = 4096
    # Pasta do usuário que recebe uma cópia de cada exportação ("" = só data/exports e o download).
    pasta_exportacao: str = ""
    subpasta_por_processo: bool = True
    assistente_concluido: bool = False


def ollama_url_loopback(url: str) -> bool:
    """True se a URL do Ollama aponta para loopback (127.0.0.0/8, localhost, ::1)."""
    from urllib.parse import urlsplit
    import ipaddress

    try:
        partes = urlsplit((url or "").strip())
        host = (partes.hostname or "").lower()
    except ValueError:
        return False
    if partes.scheme not in ("http", "https") or not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validar_tolerante(data: Dict[str, Any]) -> UserSettings:
    """Valida o dicionário descartando apenas os campos inválidos (mantém o restante do usuário)."""
    dados = {k: v for k, v in data.items() if k in UserSettings.model_fields}
    for _ in range(len(dados) + 1):
        try:
            return UserSettings(**dados)
        except ValidationError as err:
            invalidos = {str(e["loc"][0]) for e in err.errors() if e.get("loc")}
            invalidos &= set(dados)
            if not invalidos:
                break
            for chave in invalidos:
                logger.warning("Configuração inválida descartada (usando padrão): %s", chave)
                dados.pop(chave, None)
    return UserSettings()


def _gravar_json_atomico(destino: Path, conteudo: str) -> None:
    """Grava ``conteudo`` em ``destino`` via arquivo temporário no mesmo diretório + ``os.replace``."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{destino.name}.", suffix=".tmp", dir=str(destino.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(conteudo)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        ultimo_erro: Exception | None = None
        # No Windows, antivírus/OneDrive podem segurar o arquivo por instantes.
        for tentativa in range(6):
            try:
                os.replace(tmp_path, destino)
                return
            except PermissionError as err:
                ultimo_erro = err
                time.sleep(0.05 * (tentativa + 1))
        if ultimo_erro is not None:
            raise ultimo_erro
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _migrar_segredos_legados(data: Dict[str, Any]) -> bool:
    """Move segredos antigos do JSON para o cofre. Retorna True se o JSON precisa ser regravado."""
    alterou = False
    for chave in _CHAVES_SEGREDO_LEGADAS:
        if chave not in data:
            continue
        valor = data.get(chave)
        if isinstance(valor, str) and valor.strip():
            try:
                from app.core.secrets import set_secret

                set_secret(chave, valor.strip())
            except Exception as err:  # mantém no JSON para não perder a chave
                logger.warning("Falha ao migrar '%s' para o cofre de segredos: %s", chave, type(err).__name__)
                continue
            logger.info("Segredo '%s' migrado de user_settings.json para o cofre.", chave)
        data.pop(chave, None)
        alterou = True
    return alterou


def _preservar_arquivo_corrompido(caminho: Path) -> None:
    try:
        destino = caminho.with_name(f"{caminho.stem}.corrompido-{time.strftime('%Y%m%d-%H%M%S')}{caminho.suffix}")
        os.replace(caminho, destino)
        logger.warning("user_settings.json ilegível preservado em %s; usando padrões.", destino.name)
    except OSError:
        pass


def load_settings() -> UserSettings:
    settings_file = SETTINGS_FILE
    if settings_file.exists():
        data: Any = None
        try:
            data = json.loads(settings_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            logger.warning("Falha ao ler %s: %s", settings_file, err)
            if isinstance(err, ValueError):
                _preservar_arquivo_corrompido(settings_file)
        if isinstance(data, dict):
            precisa_regravar = _migrar_segredos_legados(data)
            settings = _validar_tolerante(data)
            if precisa_regravar:
                try:
                    _gravar_json_atomico(
                        settings_file,
                        json.dumps({**data, **settings.model_dump()}, ensure_ascii=False, indent=2),
                    )
                except OSError as err:
                    logger.warning("Falha ao regravar settings após migração: %s", err)
            return settings
        if data is not None:
            _preservar_arquivo_corrompido(settings_file)
        elif settings_file.exists():
            # Erro de E/S transitório: não sobrescreve o arquivo do usuário.
            return UserSettings()
    default_settings = UserSettings()
    try:
        save_settings(default_settings)
    except OSError as err:
        logger.warning("Falha ao gravar settings padrão: %s", err)
    return default_settings


def save_settings(settings: UserSettings) -> UserSettings:
    global SETTINGS_VERSION
    _gravar_json_atomico(
        SETTINGS_FILE,
        json.dumps(settings.model_dump(), ensure_ascii=False, indent=2),
    )
    SETTINGS_VERSION += 1
    return settings
