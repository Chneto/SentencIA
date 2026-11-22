# -*- coding: utf-8 -*-
"""Auditoria automatizada de layout (Playwright) — TASK-003.

Verifica em 5 configurações de viewport × 2 temas (claro e escuro):
1. Ausência de requisições externas (fonts.googleapis.com / fonts.gstatic.com) e
   carregamento das fontes locais em ``/static/fonts/`` com rede externa bloqueada.
2. Zero transbordo de texto (``scrollWidth > clientWidth + 1``,
   ``scrollHeight > clientHeight + 1`` e filhos cortados por pai com
   ``overflow: hidden``) nas 4 estações, gavetas, modais e barra de formatação,
   usando exclusivamente o caso de demonstração (nunca autos reais).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from tests.e2e.conftest import _LocalAppServer

ROOT_DIR = Path(__file__).resolve().parent.parent.parent

VIEWPORTS: List[Tuple[str, int, int, float]] = [
    ("1280x720", 1280, 720, 1.0),
    ("1366x768", 1366, 768, 1.0),
    ("1536x864", 1536, 864, 1.0),
    ("1920x1080", 1920, 1080, 1.0),
    ("1366x768@1.25", 1366, 768, 1.25),
]

THEMES = ("light", "dark")
DESIGNS = ("original", "classico", "moderno", "cockpit")


@dataclass
class OverflowIssue:
    tela: str
    viewport: str
    tema: str
    seletor: str
    tipo: str
    detalhe: str
    texto: str


OVERFLOW_JS = r"""
() => {
  const issues = [];
  const seen = new Set();

  function isVisible(el) {
    if (!el || el.nodeType !== 1) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) {
      return false;
    }
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return false;
    return true;
  }

  function hasDirectText(el) {
    for (const node of el.childNodes) {
      if (node.nodeType === 3 && node.textContent.trim().length > 0) {
        return true;
      }
    }
    return false;
  }

  function describeEl(el) {
    let sel = el.tagName.toLowerCase();
    if (el.id) {
      sel += '#' + el.id;
    } else if (el.className && typeof el.className === 'string') {
      const cls = el.className.trim().split(/\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) sel += '.' + cls.join('.');
    }
    return sel;
  }

  function shortText(el) {
    const raw = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    return raw.length > 40 ? raw.slice(0, 40) + '…' : raw;
  }

  // Ancestor hidden/clipped check
  function findHiddenOverflowClip(el) {
    const rect = el.getBoundingClientRect();
    let cur = el.parentElement;
    while (cur && cur !== document.body && cur !== document.documentElement) {
      const st = window.getComputedStyle(cur);
      const ox = st.overflowX;
      const oy = st.overflowY;
      // Ignore intentional ellipsis containers with title
      if (st.textOverflow === 'ellipsis' && (ox === 'hidden' || ox === 'clip')) {
        return null;
      }
      if (ox === 'hidden' || ox === 'clip' || oy === 'hidden' || oy === 'clip') {
        const pr = cur.getBoundingClientRect();
        if (pr.width > 0 && pr.height > 0) {
          const dxRight = (ox === 'hidden' || ox === 'clip') ? (rect.right - pr.right) : 0;
          const dxLeft = (ox === 'hidden' || ox === 'clip') ? (pr.left - rect.left) : 0;
          const dyBottom = (oy === 'hidden' || oy === 'clip') ? (rect.bottom - pr.bottom) : 0;
          const dyTop = (oy === 'hidden' || oy === 'clip') ? (pr.top - rect.top) : 0;
          if (dxRight > 2 || dxLeft > 2 || dyBottom > 2 || dyTop > 2) {
            return {
              parent: describeEl(cur),
              dx: Math.round(Math.max(dxRight, dxLeft)),
              dy: Math.round(Math.max(dyBottom, dyTop)),
            };
          }
        }
      }
      cur = cur.parentElement;
    }
    return null;
  }

  const all = document.querySelectorAll('body *');
  for (const el of all) {
    const tag = el.tagName.toLowerCase();
    if (tag === 'svg' || tag === 'path' || tag === 'rect' || tag === 'circle' || tag === 'script' || tag === 'style' || tag === 'option' || tag === 'br') {
      continue;
    }
    if (!isVisible(el)) continue;
    if (!hasDirectText(el)) continue;

    const st = window.getComputedStyle(el);
    const ox = st.overflowX;
    const oy = st.overflowY;
    const isScrollX = ox === 'auto' || ox === 'scroll';
    const isScrollY = oy === 'auto' || oy === 'scroll';
    const isIntentionalEllipsis = st.textOverflow === 'ellipsis' && (ox === 'hidden' || ox === 'clip');

    const sw = el.scrollWidth;
    const cw = el.clientWidth;
    const sh = el.scrollHeight;
    const ch = el.clientHeight;

    const sel = describeEl(el);
    const txt = shortText(el);
    if (!txt) continue;

    if (!isScrollX && !isIntentionalEllipsis && cw > 0 && sw > cw + 1) {
      const key = sel + '|X|' + txt;
      if (!seen.has(key)) {
        seen.add(key);
        issues.push({
          seletor: sel,
          tipo: 'scrollWidth',
          detalhe: `scrollWidth=${sw} > clientWidth=${cw}`,
          texto: txt,
        });
      }
    }

    if (!isScrollY && !isIntentionalEllipsis && ch > 0 && sh > ch + 1) {
      // Ignore single-line native <input>/<select> height quirks where browser renders 1-2px padding
      if (!(tag === 'input' || tag === 'select')) {
        const key = sel + '|Y|' + txt;
        if (!seen.has(key)) {
          seen.add(key);
          issues.push({
            seletor: sel,
            tipo: 'scrollHeight',
            detalhe: `scrollHeight=${sh} > clientHeight=${ch}`,
            texto: txt,
          });
        }
      }
    }

    const clip = findHiddenOverflowClip(el);
    if (clip) {
      const key = sel + '|CLIP|' + clip.parent + '|' + txt;
      if (!seen.has(key)) {
        seen.add(key);
        issues.push({
          seletor: sel,
          tipo: 'clipped',
          detalhe: `cortado por ${clip.parent} (dx=${clip.dx}px, dy=${clip.dy}px)`,
          texto: txt,
        });
      }
    }

    // Achado 4 (TASK-007): botão com palavra única curta não pode quebrar em 2+ linhas (ex.: "O / K")
    if ((tag === 'button' || (el.classList && el.classList.contains('btn'))) && !/\s/.test(txt) && txt.length <= 16) {
      const lhRaw = parseFloat(st.lineHeight);
      const fsRaw = parseFloat(st.fontSize) || 13;
      const lh = Number.isFinite(lhRaw) && lhRaw > 0 ? lhRaw : fsRaw * 1.3;
      let textH = 0;
      for (const node of el.childNodes) {
        if (node.nodeType === 3 && node.textContent.trim().length > 0) {
          const rng = document.createRange();
          rng.selectNodeContents(node);
          const rect = rng.getBoundingClientRect();
          if (rect.height > textH) textH = rect.height;
        }
      }
      if (textH > lh * 1.6) {
        const key = sel + '|WRAP|' + txt;
        if (!seen.has(key)) {
          seen.add(key);
          issues.push({
            seletor: sel,
            tipo: 'wordBreak',
            detalhe: `palavra curta quebrou linha (alturaTexto=${Math.round(textH)}px > 1.6*lh=${Math.round(lh * 1.6)}px)`,
            texto: txt,
          });
        }
      }
    }
  }

  return issues;
}
"""


def _launch_chromium(pw: Any):
    """Inicia o Chromium do Playwright, descobrindo binários já instalados se necessário."""
    try:
        return pw.chromium.launch(headless=True)
    except Exception:
        pass

    local_app = os.environ.get("LOCALAPPDATA", "")
    candidates: List[Path] = []
    if local_app:
        ms_pw = Path(local_app) / "ms-playwright"
        if ms_pw.is_dir():
            candidates.extend(sorted(ms_pw.glob("chromium-*/chrome-win64/chrome.exe"), reverse=True))
            candidates.extend(sorted(ms_pw.glob("chromium-*/chrome-win/chrome.exe"), reverse=True))
    for exe in candidates:
        if exe.is_file():
            return pw.chromium.launch(headless=True, executable_path=str(exe))

    for channel in ("msedge", "chrome"):
        try:
            return pw.chromium.launch(headless=True, channel=channel)
        except Exception:
            continue
    pytest.skip("Nenhum navegador Chromium/Edge disponível para o Playwright; execute 'uv run playwright install chromium'.")


def _close_overlays(page: Any) -> None:
    """Fecha modais, gavetas e popovers abertos para garantir isolamento entre telas."""
    page.evaluate(
        """() => {
          document.querySelectorAll('.modal-backdrop').forEach(el => { el.hidden = true; });
          document.querySelectorAll('[id$="Pop"]').forEach(el => { el.hidden = true; });
        }"""
    )
    page.wait_for_timeout(80)


def _set_theme(page: Any, theme: str, design: str = "original") -> None:
    page.evaluate(
        """({t, d}) => {
          if (d && d !== 'original' && !document.querySelector(`link[href*="/temas/${d}.css"]`)) {
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = `/static/css/temas/${d}.css`;
            document.head.appendChild(link);
          }
          document.documentElement.setAttribute('data-theme', t);
          document.documentElement.setAttribute('data-design', d);
          localStorage.setItem('jvdfm_theme', t);
          localStorage.setItem('sentencia_design', d);
        }""",
        {"t": theme, "d": design},
    )
    page.wait_for_timeout(120)


def _load_demo_and_sentence(page: Any) -> None:
    """Carrega o caso de demonstração e gera a minuta local (dados 100% fictícios)."""
    page.click("#btnLoadDemo")
    page.wait_for_function(
        """() => {
          const el = document.getElementById('heroCaseNumber');
          return el && el.textContent && !el.textContent.includes('Sem autos');
        }""",
        timeout=15000,
    )
    # Navega para Sentença e gera minuta pelo motor local determinístico
    page.click('.station-btn[data-station="sentenca"]')
    page.wait_for_timeout(200)
    page.click("#btnGenerateSentence")
    page.wait_for_function(
        """() => {
          const ed = document.getElementById('sentenceRenderBody');
          return ed && (ed.innerText || '').trim().length > 200;
        }""",
        timeout=20000,
    )
    page.wait_for_timeout(250)


def run_audit(
    base_url: str,
    *,
    screenshots_dir: Optional[Path] = None,
    block_external: bool = True,
    design: str = "original",
) -> Tuple[List[OverflowIssue], List[str], Dict[str, Any]]:
    """Executa a auditoria completa nos 5 viewports × 2 temas.

    Retorna ``(issues, external_requests, font_report)``.
    """
    from playwright.sync_api import sync_playwright

    issues: List[OverflowIssue] = []
    external_requests: List[str] = []
    font_report: Dict[str, Any] = {}

    if screenshots_dir is not None:
        screenshots_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            for vp_name, width, height, scale in VIEWPORTS:
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=scale,
                )
                page = context.new_page()

                def _route_handler(route: Any) -> None:
                    url = route.request.url
                    if not (url.startswith(base_url) or url.startswith("data:") or url.startswith("blob:")):
                        external_requests.append(url)
                        if block_external:
                            route.abort()
                            return
                    route.continue_()

                page.route("**/*", _route_handler)
                page.goto(base_url, wait_until="networkidle")
                page.wait_for_timeout(300)

                if not font_report:
                    font_report = page.evaluate(
                        """async () => {
                          await document.fonts.ready;
                          const loaded = [];
                          for (const f of document.fonts) {
                            if (f.status === 'loaded') {
                              loaded.push({ family: f.family.replace(/['"]/g, ''), weight: f.weight, style: f.style });
                            }
                          }
                          return { loadedCount: loaded.length, loaded };
                        }"""
                    )

                _load_demo_and_sentence(page)

                for theme in THEMES:
                    _set_theme(page, theme, design=design)

                    # 1) As 4 estações
                    stations = [
                        ("estacao_autos", "autos"),
                        ("estacao_analise", "analise"),
                        ("estacao_dosimetria", "dosimetria"),
                        ("estacao_sentenca", "sentenca"),
                    ]
                    for label, st_name in stations:
                        _close_overlays(page)
                        page.click(f'.station-btn[data-station="{st_name}"]')
                        page.wait_for_timeout(150)
                        found = page.evaluate(OVERFLOW_JS)
                        for item in found:
                            issues.append(
                                OverflowIssue(
                                    tela=label,
                                    viewport=vp_name,
                                    tema=theme,
                                    seletor=item["seletor"],
                                    tipo=item["tipo"],
                                    detalhe=item["detalhe"],
                                    texto=item["texto"],
                                )
                            )
                        if screenshots_dir is not None and vp_name == "1366x768" and theme == "light":
                            page.screenshot(path=str(screenshots_dir / f"{label}_1366x768_light.png"))

                    # 2) Gavetas, modais e popovers da barra de formatação (na estação Sentença)
                    _close_overlays(page)
                    page.click('.station-btn[data-station="sentenca"]')
                    page.wait_for_timeout(120)

                    overlays = [
                        ("gaveta_auditoria", "#btnOpenAuditDrawer"),
                        ("modal_nova_sentenca", "#btnResetProduction"),
                        ("modal_configuracoes", ("#btnAppMenu", '#appMainMenu [data-menu-action="settings"]')),
                        ("popover_modelos", "#btnFmtModelos"),
                        ("popover_trechos", "#btnFmtTrechos"),
                        ("modal_versoes", "#btnOpenVersoes"),
                        ("modal_revisao", "#btnOpenRevisao"),
                        ("popover_localizar", "#btnFmtLocalizar"),
                    ]
                    for label, trigger_sel in overlays:
                        _close_overlays(page)
                        if isinstance(trigger_sel, tuple):
                            opened = True
                            for sel in trigger_sel:
                                el = page.locator(sel)
                                if el.count() > 0 and el.first.is_visible():
                                    el.first.click()
                                    page.wait_for_timeout(120)
                                else:
                                    opened = False
                                    break
                            if not opened:
                                continue
                        else:
                            btn = page.locator(trigger_sel)
                            if not (btn.count() > 0 and btn.first.is_visible()):
                                continue
                            btn.first.click()
                        page.wait_for_timeout(200)
                        found = page.evaluate(OVERFLOW_JS)
                        for item in found:
                            issues.append(
                                OverflowIssue(
                                    tela=label,
                                    viewport=vp_name,
                                    tema=theme,
                                    seletor=item["seletor"],
                                    tipo=item["tipo"],
                                    detalhe=item["detalhe"],
                                    texto=item["texto"],
                                )
                            )
                        if (
                            screenshots_dir is not None
                            and vp_name == "1366x768"
                            and theme == "light"
                            and found
                        ):
                            page.screenshot(path=str(screenshots_dir / f"{label}_1366x768_light.png"))

                context.close()
        finally:
            browser.close()

    return issues, external_requests, font_report


def write_audit_markdown(
    output_md: Path,
    title: str,
    issues: List[OverflowIssue],
    external_requests: List[str],
    font_report: Dict[str, Any],
) -> None:
    output_md.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {title}",
        "",
        f"- **Total de ocorrências de transbordo/corte:** {len(issues)}",
        f"- **Requisições externas bloqueadas/detectadas:** {len(external_requests)}",
        f"- **Fontes carregadas offline (`document.fonts`):** {font_report.get('loadedCount', 0)}",
        "",
    ]
    if external_requests:
        lines.append("## Requisições externas detectadas")
        for url in sorted(set(external_requests)):
            lines.append(f"- `{url}`")
        lines.append("")

    lines.append("## Ocorrências por tela, viewport e tema")
    lines.append("")
    if not issues:
        lines.append("Nenhuma ocorrência de transbordo ou corte detectada nas 5 configurações de viewport × 2 temas.")
    else:
        lines.append("| Tela | Viewport | Tema | Seletor | Tipo | Detalhe | Texto (até 40 chars) |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for iss in issues:
            safe_txt = iss.texto.replace("|", "\\|")
            safe_det = iss.detalhe.replace("|", "\\|")
            lines.append(
                f"| `{iss.tela}` | `{iss.viewport}` | `{iss.tema}` | `{iss.seletor}` | `{iss.tipo}` | {safe_det} | {safe_txt} |"
            )
    output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ==============================================================================
# Testes Pytest (pulados automaticamente se o extra Playwright não estiver instalado)
# ==============================================================================


def test_sem_requisicoes_externas_e_fontes_locais(_live_server: _LocalAppServer):
    """Nenhuma requisição pode sair para Google Fonts e as 3 famílias devem carregar offline."""
    from playwright.sync_api import sync_playwright

    external_urls: List[str] = []
    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            page = context.new_page()

            def _route(route: Any) -> None:
                url = route.request.url
                if not (url.startswith(_live_server.base_url) or url.startswith("data:") or url.startswith("blob:")):
                    external_urls.append(url)
                    route.abort()
                    return
                route.continue_()

            page.route("**/*", _route)
            page.goto(_live_server.base_url, wait_until="networkidle")
            families = page.evaluate(
                """async () => {
                  await document.fonts.ready;
                  const fams = new Set();
                  for (const f of document.fonts) {
                    if (f.status === 'loaded') {
                      fams.add(f.family.replace(/['"]/g, ''));
                    }
                  }
                  return Array.from(fams);
                }"""
            )
            context.close()
        finally:
            browser.close()

    assert not external_urls, f"Requisições externas proibidas detectadas: {external_urls}"
    for expected in ("Plus Jakarta Sans", "Newsreader", "JetBrains Mono"):
        assert expected in families, f"Fonte local '{expected}' não carregou offline. Carregadas: {families}"


@pytest.mark.parametrize("design", DESIGNS)
def test_layout_sem_transbordo_todos_viewports_e_temas(_live_server: _LocalAppServer, design: str):
    """Garante 0 ocorrências de transbordo/corte nas 5 configs de viewport × 2 temas por desenho."""
    issues, external_requests, _ = run_audit(_live_server.base_url, block_external=True, design=design)
    assert not external_requests, f"Requisições externas detectadas ({design}): {external_requests}"
    if issues:
        sample = "\n".join(
            f"- [{design}/{i.viewport}/{i.tema}] {i.tela} {i.seletor} ({i.tipo}: {i.detalhe}): {i.texto!r}"
            for i in issues[:25]
        )
        pytest.fail(f"Detectadas {len(issues)} ocorrências de transbordo/corte ({design}):\n{sample}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "antes"
    audit_dir = ROOT_DIR / "docs" / "design" / "auditoria"
    shots = audit_dir / mode
    md_path = audit_dir / f"{mode.upper()}.md"
    with _LocalAppServer() as srv:
        found_issues, ext_reqs, fonts = run_audit(srv.base_url, screenshots_dir=shots, block_external=True)
    write_audit_markdown(
        md_path,
        f"Auditoria de Layout — {mode.upper()}",
        found_issues,
        ext_reqs,
        fonts,
    )
    print(f"[{mode.upper()}] issues={len(found_issues)} external={len(ext_reqs)} fonts_loaded={fonts.get('loadedCount', 0)}")
