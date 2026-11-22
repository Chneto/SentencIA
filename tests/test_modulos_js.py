# -*- coding: utf-8 -*-
"""Testes estruturais dos módulos ES do frontend (TASK-015).

Garante:
1. Grafo de imports estritamente acíclico (DAG) entre app.js e modulos/*.js.
2. Nenhum módulo excede 900 linhas; ponto de entrada app.js tem <= 150 linhas.
3. Todos os imports relativos existem no disco e usam a mesma versão ?v=.
4. FastAPI serve app.js e todos os módulos com Content-Type JavaScript válido.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Set

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "app" / "static" / "js"
MOD_DIR = JS_DIR / "modulos"
IMPORT_RE = re.compile(r'import\s+(?:[^"\']+?\s+from\s+)?["\'](\.[^"\']+)["\']')


def _coletar_grafo() -> Dict[Path, List[Path]]:
    arquivos = [JS_DIR / "app.js"] + sorted(MOD_DIR.glob("*.js"))
    grafo: Dict[Path, List[Path]] = {}
    for arq in arquivos:
        texto = arq.read_text(encoding="utf-8")
        deps: List[Path] = []
        for match in IMPORT_RE.finditer(texto):
            spec = match.group(1)
            rel_path = spec.split("?", 1)[0]
            alvo = (arq.parent / rel_path).resolve()
            assert alvo.is_file(), f"Import quebrado em {arq.name}: {spec} -> {alvo}"
            deps.append(alvo)
        grafo[arq.resolve()] = deps
    return grafo


def test_grafo_imports_es_sem_ciclos_e_limites_de_linhas():
    grafo = _coletar_grafo()
    assert len(grafo) >= 10, "Esperados pelo menos 10 arquivos (app.js + modulos/*.js)"

    # Limites de tamanho (TASK-015)
    app_lines = len((JS_DIR / "app.js").read_text(encoding="utf-8").splitlines())
    assert app_lines <= 150, f"app.js deve ser um ponto de entrada pequeno (<=150 linhas), tem {app_lines}"
    for mod in sorted(MOD_DIR.glob("*.js")):
        n = len(mod.read_text(encoding="utf-8").splitlines())
        assert n <= 900, f"Módulo {mod.name} excedeu 900 linhas ({n})"

    # DFS 3-cores para provar ausência de ciclos
    estado: Dict[Path, int] = {}  # 0=não visitado, 1=em visita, 2=concluído

    def dfs(no: Path, pilha: List[Path]) -> None:
        estado[no] = 1
        for dep in grafo.get(no, []):
            st = estado.get(dep, 0)
            if st == 1:
                ciclo = " -> ".join(p.name for p in pilha[pilha.index(dep) :] + [dep])
                raise AssertionError(f"Ciclo de import detectado: {ciclo}")
            if st == 0:
                dfs(dep, pilha + [dep])
        estado[no] = 2

    for no in grafo:
        if estado.get(no, 0) == 0:
            dfs(no, [no])


def test_versao_cache_consistente_e_script_module_no_html():
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    m = re.search(r'<script\s+type="module"\s+src="/static/js/app\.js\?v=([^"]+)"', html)
    assert m, 'index.html deve carregar app.js com type="module" e ?v='
    versao_html = m.group(1)

    versoes_imports: Set[str] = set()
    for arq in [JS_DIR / "app.js"] + sorted(MOD_DIR.glob("*.js")):
        for match in IMPORT_RE.finditer(arq.read_text(encoding="utf-8")):
            spec = match.group(1)
            assert "?v=" in spec, f"Import sem ?v= em {arq.name}: {spec}"
            versoes_imports.add(spec.split("?v=", 1)[1])

    assert versoes_imports == {versao_html}, (
        f"Versões de cache divergentes: html={versao_html}, imports={versoes_imports}"
    )


def test_fastapi_serve_modulos_es_com_mime_javascript():
    from app.main import app

    client = TestClient(app)
    urls = ["/static/js/app.js"] + [f"/static/js/modulos/{p.name}" for p in sorted(MOD_DIR.glob("*.js"))]
    for url in urls:
        resp = client.get(url)
        assert resp.status_code == 200, f"Falha ao servir {url}: HTTP {resp.status_code}"
        ct = (resp.headers.get("content-type") or "").lower()
        assert "javascript" in ct, f"MIME inválido para {url}: {ct!r}"
