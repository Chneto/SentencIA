# -*- coding: utf-8 -*-
"""Gera ``docs/identidade/captura_caso_demo.png`` a partir do app atual (só o caso de demonstração).

Uso::

    uv run --extra nuvem python scripts/gerar_captura_demo.py [--etapa analise|autos|dosimetria|sentenca] [--saida caminho.png]

Sobe o servidor numa pasta de dados TEMPORÁRIA (``SENTENCIA_DATA_DIR``), carrega o caso de demonstração (dados 100%
fictícios), navega até a etapa pedida e salva a captura em 1366×768. Nunca toca na pasta de dados real.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SAIDA_PADRAO = RAIZ / "docs" / "identidade" / "captura_caso_demo.png"


def _porta_livre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--etapa", default="analise", choices=["autos", "analise", "dosimetria", "sentenca"])
    ap.add_argument("--saida", default=str(SAIDA_PADRAO))
    ap.add_argument("--tema", default="light", choices=["light", "dark"])
    args = ap.parse_args(argv)

    from playwright.sync_api import sync_playwright

    dados = tempfile.mkdtemp(prefix="sentencia_captura_")
    porta = _porta_livre()
    env = dict(os.environ, SENTENCIA_DATA_DIR=dados, PYTHONUTF8="1")
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(porta), "--log-level", "error"],
        cwd=str(RAIZ), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
    )
    base = f"http://127.0.0.1:{porta}"
    try:
        limite = time.time() + 30
        while time.time() < limite:
            try:
                with urllib.request.urlopen(f"{base}/api/health", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.3)
        with sync_playwright() as pw:
            nav = None
            for opcoes in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
                try:
                    nav = pw.chromium.launch(headless=True, **opcoes)
                    break
                except Exception as exc:  # canal ausente nesta máquina: tenta o próximo
                    print(f"Navegador {opcoes or 'chromium'} indisponível ({type(exc).__name__}).", file=sys.stderr)
            if nav is None:
                print("Nenhum navegador disponível: rode 'uv run playwright install chromium'.", file=sys.stderr)
                return 2
            pagina = nav.new_page(viewport={"width": 1366, "height": 768})
            pagina.add_init_script(f"localStorage.setItem('sentencia_theme', '{args.tema}');")
            pagina.goto(base, wait_until="networkidle")
            # Pasta de dados nova abre o assistente de primeiro uso: fecha-o antes de carregar a demo.
            pagina.wait_for_timeout(800)
            if pagina.is_visible("#modalAssistente"):
                pagina.click('#modalAssistente [data-close-modal="modalAssistente"] >> nth=0')
                pagina.wait_for_selector("#modalAssistente", state="hidden", timeout=5000)
            pagina.click("#btnLoadDemo")
            pagina.wait_for_function(
                "() => { const el = document.getElementById('heroCaseNumber');"
                " return el && el.textContent && !el.textContent.includes('Sem autos'); }",
                timeout=20000,
            )
            pagina.click(f'.station-btn[data-station="{args.etapa}"]')
            pagina.wait_for_timeout(1500)
            # Fecha avisos (toasts) para a captura ficar limpa.
            pagina.evaluate("() => document.querySelectorAll('.toast-item').forEach(el => el.remove())")
            pagina.wait_for_timeout(300)
            Path(args.saida).parent.mkdir(parents=True, exist_ok=True)
            pagina.screenshot(path=args.saida)
            nav.close()
        print(f"Captura salva em {args.saida} (etapa: {args.etapa}).")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(dados, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
