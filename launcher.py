# -*- coding: utf-8 -*-
"""Iniciador multiplataforma do SentencIA (Windows, macOS e Linux).

O que faz, nesta ordem:

1. Se já houver um servidor desta versão respondendo (registrado em ``<dados>/.servidor.json``),
   apenas abre o navegador nele.
2. Escolhe uma porta livre em 127.0.0.1 (``JVDFM_PORT``/``--porta`` ou 8000; se ocupada, a próxima
   livre) e a repassa ao servidor pela variável ``JVDFM_PORT``.
3. Inicia o Ollama em segundo plano, se estiver instalado e parado (opcional).
4. Sobe o uvicorn com o MESMO interpretador do ambiente virtual do projeto (``.venv``), desacoplado
   deste processo (o servidor continua rodando depois que esta janela fecha).
5. Aguarda ``GET /api/health`` responder e abre o navegador: Chrome/Edge/Brave em modo aplicativo
   quando existir (``platform_utils.find_chrome``), senão o navegador padrão.

Logs: ``<dados>/logs/launcher.log`` (este iniciador) e ``<dados>/logs/servidor.log`` (saída e erros
do uvicorn — nada é descartado). ``<dados>`` é ``JVDFM_DATA_DIR`` ou a pasta ``data/`` do projeto.

Uso: ``python launcher.py [--porta N] [--sem-navegador] [--parar] [--status]``.
"""

from __future__ import annotations

__author__ = "FChNeto"

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_DIR = Path(__file__).resolve().parent


def _ler_versao_projeto(raiz: Path = PROJECT_DIR) -> str:
    pyproject = raiz / "pyproject.toml"
    try:
        import tomllib

        dados = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        versao = str(dados.get("project", {}).get("version", "")).strip()
        if versao:
            return versao
    except Exception:
        pass
    return "2.1.0"


VERSAO_ESPERADA = _ler_versao_projeto()
HOST = "127.0.0.1"
PORTA_PADRAO = 8000
TENTATIVAS_PORTA = 20
TIMEOUT_HEALTH_S = 180.0
IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

logger = logging.getLogger("jvdfm.launcher")


# ----------------------------------------------------------------------------------------------
# Pastas e log
# ----------------------------------------------------------------------------------------------
def data_dir() -> Path:
    """Mesma regra de ``app.core.config``: ``JVDFM_DATA_DIR`` -> ``<projeto>/.pasta_dados`` -> ``<projeto>/data``."""
    bruto = (os.environ.get("JVDFM_DATA_DIR") or "").strip()
    if bruto:
        return Path(os.path.expandvars(bruto)).expanduser()
    marcador = PROJECT_DIR / ".pasta_dados"
    if marcador.is_file():
        try:
            for linha in marcador.read_text(encoding="utf-8-sig").splitlines():
                limpa = linha.strip()
                if not limpa or limpa.startswith("#"):
                    continue
                cand = Path(os.path.expandvars(limpa)).expanduser()
                if cand.is_absolute():
                    return cand
                break
        except OSError:
            pass
    return PROJECT_DIR / "data"


def logs_dir() -> Path:
    return data_dir() / "logs"


def arquivo_estado() -> Path:
    return data_dir() / ".servidor.json"


def _modo_console() -> bool:
    """Verdadeiro quando chamado pelo instalador (``JVDFM_LAUNCHER_CONSOLE=1``): mensagens vão para a
    saída padrão (que o instalador mostra e registra) em vez de caixas de diálogo."""
    return (os.environ.get("JVDFM_LAUNCHER_CONSOLE") or "").strip() == "1"


def configurar_log() -> Path:
    destino = logs_dir()
    destino.mkdir(parents=True, exist_ok=True)
    arquivo = destino / "launcher.log"
    handler = RotatingFileHandler(arquivo, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.setLevel(logging.INFO)
    logger.handlers[:] = [handler]
    if _modo_console() and sys.stdout is not None:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(console)
    elif sys.stderr is not None and getattr(sys.stderr, "isatty", lambda: False)():
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(console)
    logger.propagate = False
    return arquivo


def _girar_log_servidor(caminho: Path, limite: int = 5 * 1024 * 1024, copias: int = 3) -> None:
    try:
        if not caminho.exists() or caminho.stat().st_size < limite:
            return
        for i in range(copias - 1, 0, -1):
            antigo = caminho.with_name(f"{caminho.name}.{i}")
            if antigo.exists():
                os.replace(antigo, caminho.with_name(f"{caminho.name}.{i + 1}"))
        os.replace(caminho, caminho.with_name(f"{caminho.name}.1"))
    except OSError as err:
        logger.warning("Não foi possível girar %s: %s", caminho, err)


def _final_do_arquivo(caminho: Path, linhas: int = 25) -> str:
    try:
        texto = caminho.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(texto.splitlines()[-linhas:])


# ----------------------------------------------------------------------------------------------
# Avisos ao usuário (sem console no Windows: caixa de diálogo)
# ----------------------------------------------------------------------------------------------
def _tem_console() -> bool:
    if _modo_console():
        return True
    try:
        return sys.stderr is not None and bool(sys.stderr.isatty())
    except Exception:
        return False


def avisar(mensagem: str, titulo: str = "SentencIA", erro: bool = True) -> None:
    """Registra no log e avisa o usuário: no terminal, se houver; senão, em caixa de diálogo."""
    (logger.error if erro else logger.info)(mensagem)  # no terminal, o handler de console já exibe
    if _tem_console():
        return
    try:
        if IS_WINDOWS:
            import ctypes

            icone = 0x10 if erro else 0x40
            ctypes.windll.user32.MessageBoxW(None, mensagem, titulo, icone)  # type: ignore[attr-defined]
            return
        if IS_MACOS:
            texto = mensagem.replace("\\", "\\\\").replace('"', '\\"')
            tit = titulo.replace('"', '\\"')
            subprocess.run(
                ["osascript", "-e", f'display dialog "{texto}" with title "{tit}" buttons {{"OK"}} default button 1'],
                check=False,
                timeout=300,
            )
            return
    except Exception:  # o aviso nunca pode derrubar o iniciador
        pass
    try:
        print(f"{titulo}: {mensagem}", file=sys.stderr)
    except Exception:
        pass


# ----------------------------------------------------------------------------------------------
# Rede
# ----------------------------------------------------------------------------------------------
_SEM_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def consultar_health(porta: int, timeout: float = 1.5) -> Optional[Dict[str, Any]]:
    """JSON de ``/api/health`` ou ``None`` se não responder com 200."""
    try:
        with _SEM_PROXY.open(f"http://{HOST}:{porta}/api/health", timeout=timeout) as resp:
            if resp.status != 200:
                return None
            dados = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
            return dados if isinstance(dados, dict) else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def url_responde(url: str, timeout: float = 1.5) -> bool:
    try:
        with _SEM_PROXY.open(url, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _porta_atende(porta: int) -> bool:
    """Há algum processo aceitando conexões nesta porta do loopback?"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        try:
            return s.connect_ex((HOST, porta)) == 0
        except OSError:
            return False


def porta_livre(porta: int) -> bool:
    # No Windows é possível vincular 127.0.0.1:N mesmo com outro programa em 0.0.0.0:N; por isso
    # testa também se alguém já atende na porta.
    if _porta_atende(porta):
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if not IS_WINDOWS:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((HOST, porta))
        except OSError:
            return False
    return True


def escolher_porta(preferida: int) -> int:
    for candidata in range(preferida, min(preferida + TENTATIVAS_PORTA, 65536)):
        if porta_livre(candidata):
            return candidata
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:  # deixa o sistema escolher
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


def porta_preferida(argumento: Optional[int]) -> int:
    if argumento:
        return argumento
    try:
        valor = int((os.environ.get("JVDFM_PORT") or "").strip())
        if 1 <= valor <= 65535:
            return valor
    except ValueError:
        pass
    return PORTA_PADRAO


# ----------------------------------------------------------------------------------------------
# Estado do servidor em execução
# ----------------------------------------------------------------------------------------------
def ler_estado() -> Dict[str, Any]:
    try:
        dados = json.loads(arquivo_estado().read_text(encoding="utf-8"))
        return dados if isinstance(dados, dict) else {}
    except (OSError, ValueError):
        return {}


def gravar_estado(dados: Dict[str, Any]) -> None:
    destino = arquivo_estado()
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_name(destino.name + ".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, destino)


def servidor_existente() -> Optional[int]:
    """Porta de um servidor desta versão já em execução (registrado por este iniciador)."""
    estado = ler_estado()
    porta = estado.get("porta")
    if not isinstance(porta, int):
        return None
    health = consultar_health(porta)
    if health and health.get("status") == "ok" and health.get("versao") == VERSAO_ESPERADA:
        return porta
    return None


# ----------------------------------------------------------------------------------------------
# Trava simples para dois cliques seguidos não subirem dois servidores
# ----------------------------------------------------------------------------------------------
class TravaIniciador:
    def __init__(self, caminho: Path) -> None:
        self.caminho = caminho
        self._fh = None

    def __enter__(self) -> "TravaIniciador":
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.caminho, "a+")
        limite = time.monotonic() + TIMEOUT_HEALTH_S + 30
        while True:
            try:
                if IS_WINDOWS:
                    import msvcrt

                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() > limite:
                    raise TimeoutError("Outro iniciador está em execução há tempo demais.")
                time.sleep(0.5)

    def __exit__(self, *exc: Any) -> None:
        try:
            if self._fh is not None:
                if IS_WINDOWS:
                    import msvcrt

                    self._fh.seek(0)
                    try:
                        msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                self._fh.close()
        finally:
            self._fh = None


# ----------------------------------------------------------------------------------------------
# Processos
# ----------------------------------------------------------------------------------------------
def python_do_venv() -> str:
    """Interpretador do ``.venv`` do projeto (``pythonw`` no Windows, sem janela); senão o atual."""
    if IS_WINDOWS:
        candidatos = [PROJECT_DIR / ".venv" / "Scripts" / "pythonw.exe", PROJECT_DIR / ".venv" / "Scripts" / "python.exe"]
    else:
        candidatos = [PROJECT_DIR / ".venv" / "bin" / "python3", PROJECT_DIR / ".venv" / "bin" / "python"]
    for c in candidatos:
        if c.exists():
            return str(c)
    return sys.executable


def _kwargs_desacoplado() -> Dict[str, Any]:
    if IS_WINDOWS:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": True, "close_fds": True}


def _carregar_settings() -> Any:
    try:
        from app.core.config import load_settings

        return load_settings()
    except Exception as err:  # o iniciador funciona mesmo sem conseguir ler as preferências
        logger.warning("Preferências indisponíveis (%s); usando padrões.", err)
        return None


def garantir_ollama(settings: Any) -> None:
    """Inicia o Ollama em segundo plano se instalado e parado. Opcional: nunca bloqueia a abertura."""
    url = (getattr(settings, "ollama_url", "") or "http://127.0.0.1:11434").rstrip("/")
    if url_responde(f"{url}/api/tags"):
        logger.info("Ollama já está ativo em %s.", url)
        return
    try:
        if IS_MACOS and Path("/Applications/Ollama.app").exists():
            subprocess.Popen(["open", "-g", "-a", "Ollama"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("Ollama.app iniciado em segundo plano.")
            return
        try:
            from app.core.platform_utils import find_binary

            exe = find_binary("ollama")
        except Exception:
            exe = None
        if not exe:
            logger.info("Ollama não instalado: a minuta usará o motor determinístico (opcional).")
            return
        log_ollama = open(logs_dir() / "ollama.log", "ab")
        subprocess.Popen(
            [exe, "serve"],
            cwd=str(PROJECT_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_ollama,
            stderr=subprocess.STDOUT,
            **_kwargs_desacoplado(),
        )
        log_ollama.close()
        logger.info("Ollama iniciado em segundo plano (%s).", exe)
    except Exception as err:
        logger.warning("Não foi possível iniciar o Ollama: %s", err)


def iniciar_servidor(porta: int) -> subprocess.Popen:
    python = python_do_venv()
    log_servidor = logs_dir() / "servidor.log"
    _girar_log_servidor(log_servidor)
    env = dict(os.environ)
    env["JVDFM_PORT"] = str(porta)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    cmd = [python, "-m", "uvicorn", "app.main:app", "--host", HOST, "--port", str(porta), "--log-level", "info"]
    logger.info("Iniciando servidor: %s (cwd=%s)", " ".join(cmd), PROJECT_DIR)
    with open(log_servidor, "ab") as saida:
        saida.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} porta {porta} =====\n".encode("utf-8"))
        saida.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=saida,
            stderr=subprocess.STDOUT,
            **_kwargs_desacoplado(),
        )
    return proc


def aguardar_health(porta: int, proc: Optional[subprocess.Popen], timeout: float = TIMEOUT_HEALTH_S) -> bool:
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        health = consultar_health(porta)
        if health and health.get("status") == "ok":
            return True
        if proc is not None and proc.poll() is not None:
            logger.error("O servidor encerrou durante a inicialização (código %s).", proc.returncode)
            return False
        time.sleep(0.5)
    return False


def abrir_navegador(url: str) -> None:
    chrome = None
    try:
        from app.core.platform_utils import find_chrome

        chrome = find_chrome()
    except Exception as err:
        logger.warning("find_chrome indisponível: %s", err)
    if chrome:
        try:
            kwargs: Dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if IS_WINDOWS:
                kwargs["close_fds"] = True
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen([chrome, f"--app={url}", "--new-window", "--start-maximized"], **kwargs)
            logger.info("Aberto em modo aplicativo: %s", chrome)
            return
        except OSError as err:
            logger.warning("Falha ao abrir %s (%s); usando o navegador padrão.", chrome, err)
    if not webbrowser.open(url):
        avisar(f"Abra no navegador o endereço: {url}", erro=False)
    else:
        logger.info("Aberto no navegador padrão: %s", url)


def _dizer(mensagem: str) -> None:
    """Registra no log e mostra no terminal (sem duplicar quando o log já escreve no console)."""
    logger.info(mensagem)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in logger.handlers):
        print(mensagem)


def parar_servidor() -> int:
    estado = ler_estado()
    porta, pid = estado.get("porta"), estado.get("pid")
    if not isinstance(pid, int) or not isinstance(porta, int) or consultar_health(porta) is None:
        _dizer("Nenhum servidor do SentencIA registrado em execução.")
        return 0
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
        else:
            import signal

            os.kill(pid, signal.SIGTERM)
    except OSError as err:
        logger.error("Não foi possível encerrar o processo %s: %s", pid, err)
        _dizer(f"Não foi possível encerrar o processo {pid}: {err}")
        return 1
    for _ in range(20):
        if consultar_health(porta) is None:
            break
        time.sleep(0.25)
    try:
        arquivo_estado().unlink()
    except OSError:
        pass
    _dizer(f"Servidor encerrado (pid {pid}, porta {porta}).")
    return 0


def limpar_demo_cli(*, confirmar_auto: bool = False) -> int:
    """Lista e (mediante confirmação) remove apenas artefatos de demonstração/teste."""
    from app.core.limpeza_demo import limpar_artefatos_demo

    pasta = data_dir()
    previa = limpar_artefatos_demo(pasta, simular=True)
    itens = previa.get("itens") or []
    if not itens:
        print(f"Nenhum dado de demonstração ou teste encontrado em {pasta}.")
        return 0

    print(f"Itens de demonstração/teste identificados em {pasta}:")
    for item in itens:
        print(f"  - {item['caminho']} ({item['motivo']})")
    print("Autos reais, exportações, cofre e configurações NÃO serão tocados.")

    if not confirmar_auto:
        try:
            resposta = input("Confirma a remoção destes itens? [s/N]: ").strip().lower()
        except EOFError:
            resposta = ""
        if resposta not in ("s", "sim"):
            print("Operação cancelada. Nada foi removido.")
            return 0

    res = limpar_artefatos_demo(pasta, simular=False, confirmar=True)
    print(f"Removido(s) {res['total']} item(ns) de demonstração/teste.")
    return 0


# ----------------------------------------------------------------------------------------------
# Principal
# ----------------------------------------------------------------------------------------------
def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Inicia o SentencIA e abre o navegador.")
    parser.add_argument("--porta", type=int, default=None, help="porta preferida (padrão: JVDFM_PORT ou 8000)")
    parser.add_argument("--sem-navegador", action="store_true", help="não abrir o navegador")
    parser.add_argument("--sem-ollama", action="store_true", help="não tentar iniciar o Ollama")
    parser.add_argument("--parar", action="store_true", help="encerrar o servidor iniciado por este iniciador")
    parser.add_argument("--status", action="store_true", help="mostrar se há servidor em execução")
    parser.add_argument(
        "--limpar-demo",
        action="store_true",
        help="listar e remover apenas dados de demonstração/teste (nunca toca em autos reais)",
    )
    parser.add_argument(
        "--sim",
        action="store_true",
        help="usado com --limpar-demo para confirmar sem perguntar interativamente",
    )
    args = parser.parse_args(argv)

    os.chdir(PROJECT_DIR)
    arquivo_log = configurar_log()
    logger.info("Iniciador %s (Python %s, %s)", VERSAO_ESPERADA, sys.version.split()[0], sys.platform)

    if args.limpar_demo:
        return limpar_demo_cli(confirmar_auto=args.sim)
    if args.parar:
        return parar_servidor()
    if args.status:
        porta = servidor_existente()
        _dizer(f"Em execução: http://{HOST}:{porta}/" if porta else "Servidor parado.")
        return 0

    try:
        with TravaIniciador(logs_dir() / ".launcher.lock"):
            porta = servidor_existente()
            if porta is not None:
                logger.info("Servidor já em execução na porta %s; reutilizando.", porta)
            else:
                settings = _carregar_settings()
                if not args.sem_ollama:
                    garantir_ollama(settings)
                porta = escolher_porta(porta_preferida(args.porta))
                proc = iniciar_servidor(porta)
                if not aguardar_health(porta, proc):
                    detalhes = _final_do_arquivo(logs_dir() / "servidor.log")
                    logger.error("Saída do servidor:\n%s", detalhes)
                    if proc.poll() is None:
                        proc.terminate()
                    avisar(
                        "O servidor não respondeu a tempo.\n\n"
                        f"Veja os registros em:\n{logs_dir() / 'servidor.log'}\n{arquivo_log}\n\n"
                        "Dica: execute novamente o instalador (Iniciar SentencIA) para reparar o ambiente."
                    )
                    return 1
                gravar_estado(
                    {
                        "porta": porta,
                        "pid": proc.pid,
                        "versao": VERSAO_ESPERADA,
                        "python": python_do_venv(),
                        "iniciado_em": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                )
                logger.info("Servidor pronto em http://%s:%s/ (pid %s).", HOST, porta, proc.pid)
    except TimeoutError as err:
        avisar(str(err))
        return 1
    except Exception as err:  # registra tudo; nada some em silêncio
        logger.exception("Falha inesperada no iniciador")
        avisar(f"Falha inesperada ao iniciar: {err}\n\nRegistro: {arquivo_log}")
        return 1

    url = f"http://{HOST}:{porta}/"
    if args.sem_navegador:
        _dizer(f"Endereço: {url}")
    else:
        abrir_navegador(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
