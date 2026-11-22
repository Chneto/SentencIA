# -*- coding: utf-8 -*-
"""Gera a identidade visual do SentencIA (símbolo, ícones, miniaturas e banner).

Tudo nasce do mesmo desenho vetorial (``svg_marca``), então qualquer ajuste de forma ou de cor se
propaga para todos os formatos. Usa apenas o PyMuPDF (já dependência do programa); sem Pillow.

Conceito: uma **balança cujo topo vira uma centelha**. A balança é a Justiça (continuidade com a marca
anterior: azul-marinho e dourado); a centelha é a inteligência artificial que ilumina, mas não decide.
O nome mistura tradição e tecnologia: "Sentenc" em serifa (a peça jurídica) e "IA" em sans-serif
dourada (a assistência).

Saídas (relativas à raiz do projeto)::

    app/static/assets/sentencia_mark.svg        símbolo completo (vetor)
    app/static/assets/favicon.svg               versão simplificada, legível a 16 px
    app/static/assets/app_icon.ico              ícone do Windows (16 a 256 px, versões simplificadas nos menores)
    app/static/assets/logo_sentencia.png        512 px
    app/static/assets/apple-touch-icon.png      180 px
    SentencIA.app/Contents/Resources/AppIcon.icns   ícone do macOS
    docs/identidade/banner.png                  1280x640 (README / pré-visualização social)
    docs/identidade/miniatura.png               512 px
    docs/identidade/logo_horizontal_claro.png   para fundos claros (transparente)
    docs/identidade/logo_horizontal_escuro.png  para fundos escuros (transparente)

Uso: ``uv run python scripts/gerar_identidade.py``
"""

from __future__ import annotations

__author__ = "FChNeto"

import struct
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pymupdf

RAIZ = Path(__file__).resolve().parent.parent
ASSETS = RAIZ / "app" / "static" / "assets"
IDENTIDADE = RAIZ / "docs" / "identidade"
ICNS = RAIZ / "SentencIA.app" / "Contents" / "Resources" / "AppIcon.icns"

# Paleta (continua a do sistema: azul-marinho e dourado)
MARINHO_0 = "#0A1A33"
MARINHO_1 = "#16335E"
OURO_CLARO = "#F7DC8C"
OURO_ESCURO = "#C99A2E"
OURO = (0.878, 0.722, 0.302)  # #E0B84D
CREME = "#FFF3CF"

_GEOMETRIA = {
    # pilar (x, y, largura, altura), viga (x, y, largura, altura), espessura do fio, raio do prato,
    # y do prato, centro e raio da centelha
    "detalhada": dict(pilar=(244, 170, 24, 206), base="M176 400h160l-16-30H192z", viga=(100, 196, 312, 20),
                      fio=8, raio=58, y_prato=322, estrela=(256, 116, 62)),
    "simples": dict(pilar=(234, 168, 44, 210), base="M164 408h184l-20-36H184z", viga=(88, 188, 336, 34),
                    fio=15, raio=66, y_prato=334, estrela=(256, 108, 72)),
}


def _estrela(cx: float, cy: float, r: float, q: float = 0.17) -> str:
    """Estrela de quatro pontas com lados côncavos (a "centelha")."""
    k = r * q
    return (
        f"M{cx} {cy - r}Q{cx + k} {cy - k} {cx + r} {cy}Q{cx + k} {cy + k} {cx} {cy + r}"
        f"Q{cx - k} {cy + k} {cx - r} {cy}Q{cx - k} {cy - k} {cx} {cy - r}Z"
    )


def _miolo(g: dict) -> str:
    px, py, pw, ph = g["pilar"]
    vx, vy, vw, vh = g["viga"]
    fio, r, yp = g["fio"], g["raio"], g["y_prato"]
    ey = vy + vh / 2
    ry = r * 0.62
    partes: List[str] = []
    for cx in (vx + 26, vx + vw - 26):  # extremos da viga
        partes.append(
            f'<path d="M{cx} {ey}L{cx - r + 8} {yp}M{cx} {ey}L{cx + r - 8} {yp}" stroke="url(#ouro)" '
            f'stroke-width="{fio}" stroke-linecap="round" fill="none"/>'
        )
        partes.append(f'<path d="M{cx - r} {yp}H{cx + r}A{r} {ry} 0 0 1 {cx - r} {yp}Z" fill="url(#ouro)"/>')
    partes.append(f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="{pw / 4}" fill="url(#ouro)"/>')
    partes.append(f'<path d="{g["base"]}" fill="url(#ouro)"/>')
    partes.append(f'<rect x="{vx}" y="{vy}" width="{vw}" height="{vh}" rx="{vh / 2}" fill="url(#ouro)"/>')
    ex, ey2, er = g["estrela"]
    partes.append(f'<circle cx="{ex}" cy="{ey2}" r="{er * 1.5}" fill="url(#brilho)"/>')
    partes.append(f'<path d="{_estrela(ex, ey2, er)}" fill="{CREME}"/>')
    return "".join(partes)


_DEFS = (
    f'<defs><linearGradient id="fundo" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{MARINHO_1}"/>'
    f'<stop offset="1" stop-color="{MARINHO_0}"/></linearGradient>'
    f'<linearGradient id="ouro" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{OURO_CLARO}"/>'
    f'<stop offset="1" stop-color="{OURO_ESCURO}"/></linearGradient>'
    '<radialGradient id="brilho"><stop offset="0" stop-color="#FFE9A6" stop-opacity="0.55"/>'
    '<stop offset="1" stop-color="#FFE9A6" stop-opacity="0"/></radialGradient></defs>'
)


def svg_marca(simples: bool = False, raio_fundo: int = 116, margem: float = 0.0, tamanho: int = 512) -> str:
    """SVG do símbolo (viewBox 512). ``margem`` (0 a 0.2) reserva respiro, como pede o macOS."""
    g = _GEOMETRIA["simples" if simples else "detalhada"]
    borda = "" if simples else (
        f'<rect x="14" y="14" width="484" height="484" rx="{max(raio_fundo - 12, 0)}" fill="none" '
        'stroke="#E0B84D" stroke-opacity="0.30" stroke-width="3"/>'
    )
    corpo = f'<rect width="512" height="512" rx="{raio_fundo}" fill="url(#fundo)"/>{borda}{_miolo(g)}'
    if margem:
        s = 1 - 2 * margem
        corpo = f'<g transform="translate({512 * margem:.2f} {512 * margem:.2f}) scale({s:.4f})">{corpo}</g>'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{tamanho}" height="{tamanho}" viewBox="0 0 512 512">'
        f"{_DEFS}{corpo}</svg>"
    )


def _svg_fragmento(simples: bool = False) -> str:
    """Símbolo sem o envelope ``<svg>``, para compor em outras peças (banner, logotipo)."""
    g = _GEOMETRIA["simples" if simples else "detalhada"]
    return (
        f'<rect width="512" height="512" rx="116" fill="url(#fundo)"/>'
        f'<rect x="14" y="14" width="484" height="484" rx="104" fill="none" stroke="#E0B84D" '
        f'stroke-opacity="0.30" stroke-width="3"/>{_miolo(g)}'
    )


# ----------------------------------------------------------------------------------------------
# Rasterização e contêineres de ícone
# ----------------------------------------------------------------------------------------------
_FUNDO_PLANO = "#12284B"
_OURO_PLANO = "#E3B64A"


def _planificar(svg: str) -> str:
    """Troca os degradês por cores sólidas: o renderizador de SVG do PyMuPDF não os suporta.

    O brilho da centelha vira discos concêntricos translúcidos. Os arquivos ``.svg`` entregues ao
    navegador continuam com degradê; só os rasters (ícones, miniaturas, banner) usam a versão plana.
    """
    import re

    def _brilho(m: "re.Match[str]") -> str:
        cx, cy, r = (float(m.group(i)) for i in (1, 2, 3))
        return "".join(
            f'<circle cx="{cx}" cy="{cy}" r="{r * f:.1f}" fill="#FFE9A6" fill-opacity="0.045"/>' for f in (1.0, 0.85, 0.7, 0.55, 0.4)
        )

    svg = re.sub(r'<circle cx="([\d.]+)" cy="([\d.]+)" r="([\d.]+)" fill="url\(#brilho\)"/>', _brilho, svg)
    return svg.replace("url(#ouro)", _OURO_PLANO).replace("url(#fundo)", _FUNDO_PLANO)


def png_do_svg(svg: str, tamanho: int, plano: bool = True) -> bytes:
    doc = pymupdf.open(stream=(_planificar(svg) if plano else svg).encode("utf-8"), filetype="svg")
    pagina = doc[0]
    zoom = tamanho / pagina.rect.width
    return pagina.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True).tobytes("png")


def escrever_ico(destino: Path, imagens: Dict[int, bytes]) -> None:
    """ICO com entradas PNG (Windows Vista+). ``imagens``: {lado_em_px: png}."""
    lados = sorted(imagens)
    cabecalho = struct.pack("<HHH", 0, 1, len(lados))
    deslocamento = 6 + 16 * len(lados)
    diretorio, dados = b"", b""
    for lado in lados:
        png = imagens[lado]
        byte_lado = 0 if lado >= 256 else lado
        diretorio += struct.pack("<BBBBHHII", byte_lado, byte_lado, 0, 0, 1, 32, len(png), deslocamento + len(dados))
        dados += png
    destino.write_bytes(cabecalho + diretorio + dados)


def escrever_icns(destino: Path, imagens: List[Tuple[bytes, int, bytes]]) -> None:
    """ICNS com entradas PNG. ``imagens``: [(tipo, lado, png)]."""
    corpo = b"".join(tipo + struct.pack(">I", 8 + len(png)) + png for tipo, _lado, png in imagens)
    destino.write_bytes(b"icns" + struct.pack(">I", 8 + len(corpo)) + corpo)


# ----------------------------------------------------------------------------------------------
# Texto (banner e logotipos): fontes do sistema, com plano B embutido do PyMuPDF
# ----------------------------------------------------------------------------------------------
def _fonte(caminho: str, reserva: str) -> Tuple[str, str, "pymupdf.Font"]:
    arq = Path("C:/Windows/Fonts") / caminho
    if arq.exists():
        return arq.stem, str(arq), pymupdf.Font(fontfile=str(arq))
    return reserva, "", pymupdf.Font(reserva)


def _escrever(pagina, x: float, y: float, texto: str, fonte: Tuple[str, str, "pymupdf.Font"], tamanho: float,
              cor: Tuple[float, float, float], espaco: float = 0.0) -> float:
    """Escreve ``texto`` em (x, y-linha de base); devolve a abscissa final. ``espaco`` = tracking em px."""
    nome, arquivo, fnt = fonte
    kw = dict(fontname=nome, fontsize=tamanho, color=cor)
    if arquivo:
        kw["fontfile"] = arquivo
    if not espaco:
        pagina.insert_text((x, y), texto, **kw)
        return x + fnt.text_length(texto, fontsize=tamanho)
    for ch in texto:
        pagina.insert_text((x, y), ch, **kw)
        x += fnt.text_length(ch, fontsize=tamanho) + espaco
    return x


def _hex(h: str) -> Tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _pagina_do_svg(svg: str):
    base = pymupdf.open(stream=_planificar(svg).encode("utf-8"), filetype="svg")
    pdf = pymupdf.open("pdf", base.convert_to_pdf())
    return pdf, pdf[0]


def _wordmark(pagina, x: float, base_y: float, tamanho: float, cor_texto: Tuple[float, float, float],
              cor_ia: Tuple[float, float, float]) -> float:
    serifa = _fonte("georgiab.ttf", "tibo")
    sans = _fonte("seguibl.ttf", "hebo")
    x = _escrever(pagina, x, base_y, "Sentenc", serifa, tamanho, cor_texto)
    return _escrever(pagina, x + tamanho * 0.01, base_y, "IA", sans, tamanho * 0.94, cor_ia)


def _largura_wordmark(tamanho: float) -> float:
    serifa = _fonte("georgiab.ttf", "tibo")[2]
    sans = _fonte("seguibl.ttf", "hebo")[2]
    return serifa.text_length("Sentenc", fontsize=tamanho) + tamanho * 0.01 + sans.text_length("IA", fontsize=tamanho * 0.94)


def gerar_banner() -> bytes:
    def _mistura(a: str, b: str, t: float) -> str:
        ca, cb = _hex(a), _hex(b)
        return "#" + "".join(f"{round((x + (y - x) * t) * 255):02x}" for x, y in zip(ca, cb))

    # Degradê diagonal aproximado por faixas verticais (o renderizador não tem degradês) + halo dourado no canto
    faixas = "".join(
        f'<rect x="{i * 20}" y="0" width="21" height="640" fill="{_mistura(MARINHO_0, MARINHO_1, i / 63)}"/>' for i in range(64)
    )
    halo = "".join(
        f'<circle cx="1120" cy="40" r="{r}" fill="#E0B84D" fill-opacity="0.010"/>' for r in range(620, 80, -45)
    )
    fundo = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="640" viewBox="0 0 1280 640">'
        f"{_DEFS}{faixas}{halo}"
        + "".join(
            f'<circle cx="1240" cy="620" r="{r}" fill="none" stroke="#E0B84D" stroke-opacity="{o}" stroke-width="2"/>'
            for r, o in ((150, 0.16), (260, 0.11), (380, 0.07), (510, 0.05))
        )
        + f'<rect x="0" y="632" width="1280" height="8" fill="#E0B84D" fill-opacity="0.85"/>'
        f'<g transform="translate(88 128) scale(0.75)">{_svg_fragmento()}</g></svg>'
    )
    pdf, pagina = _pagina_do_svg(fundo)
    x0 = 520.0
    tam = min(124.0, (1280 - x0 - 70) / _largura_wordmark(1.0))
    _wordmark(pagina, x0, 292, tam, _hex("#F7F2E4"), OURO)
    _escrever(pagina, x0 + 4, 358, "A inteligência que auxilia. A decisão que é sua.",
              _fonte("georgiai.ttf", "tiit"), 36, _hex("#E9E2CC"))
    _escrever(pagina, x0 + 4, 418, "REDAÇÃO FORENSE ASSISTIDA", _fonte("segoeuib.ttf", "hebo"), 20, OURO, espaco=3.4)
    ref = _fonte("segoeui.ttf", "helv")
    for i, linha in enumerate(("Auditoria anti-alucinação em quatro camadas", "Fundamentação rastreável às peças dos autos",
                               "Execução local por padrão — o segredo de justiça fica com você")):
        _escrever(pagina, x0 + 4, 470 + i * 34, "•  " + linha, ref, 22, _hex("#AEB9CF"))
    return pagina.get_pixmap(alpha=False).tobytes("png")


def gerar_logo_horizontal(escuro: bool) -> bytes:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="980" height="240" viewBox="0 0 980 240">'
        f'{_DEFS}<g transform="translate(20 17.6) scale(0.4)">{_svg_fragmento()}</g></svg>'
    )
    pdf, pagina = _pagina_do_svg(svg)
    texto = _hex("#F7F2E4") if escuro else _hex(MARINHO_0)
    ia = OURO if escuro else _hex("#A87A12")
    _wordmark(pagina, 256, 146, 112, texto, ia)
    _escrever(pagina, 260, 194, "REDAÇÃO FORENSE ASSISTIDA", _fonte("segoeuib.ttf", "hebo"), 17,
              ia, espaco=3.0)
    return pagina.get_pixmap(alpha=True).tobytes("png")


# ----------------------------------------------------------------------------------------------
def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    IDENTIDADE.mkdir(parents=True, exist_ok=True)

    (ASSETS / "sentencia_mark.svg").write_text(svg_marca(), encoding="utf-8")
    (ASSETS / "favicon.svg").write_text(svg_marca(simples=True, raio_fundo=108), encoding="utf-8")

    detalhada = svg_marca()
    (ASSETS / "logo_sentencia.png").write_bytes(png_do_svg(detalhada, 512))
    (IDENTIDADE / "miniatura.png").write_bytes(png_do_svg(detalhada, 512))
    # iOS aplica a própria máscara: entrega o quadrado inteiro, sem cantos arredondados
    (ASSETS / "apple-touch-icon.png").write_bytes(png_do_svg(svg_marca(raio_fundo=0), 180))

    simples = svg_marca(simples=True, raio_fundo=108)
    ico = {t: png_do_svg(simples if t <= 48 else detalhada, t) for t in (16, 24, 32, 48, 64, 128, 256)}
    escrever_ico(ASSETS / "app_icon.ico", ico)

    mac = svg_marca(raio_fundo=116, margem=0.09)
    pngs = [(b"icp4", 16), (b"icp5", 32), (b"icp6", 64), (b"ic07", 128), (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024)]
    ICNS.parent.mkdir(parents=True, exist_ok=True)
    escrever_icns(ICNS, [(tipo, lado, png_do_svg(simples if lado <= 32 else mac, lado)) for tipo, lado in pngs])

    (IDENTIDADE / "banner.png").write_bytes(gerar_banner())
    (IDENTIDADE / "logo_horizontal_claro.png").write_bytes(gerar_logo_horizontal(escuro=False))
    (IDENTIDADE / "logo_horizontal_escuro.png").write_bytes(gerar_logo_horizontal(escuro=True))

    for arq in sorted([*ASSETS.glob("sentencia_mark.svg"), *ASSETS.glob("favicon.svg"), *ASSETS.glob("app_icon.ico"),
                       *ASSETS.glob("logo_sentencia.png"), *ASSETS.glob("apple-touch-icon.png"), ICNS,
                       *IDENTIDADE.glob("*.png")]):
        print(f"{arq.relative_to(RAIZ)}  ({arq.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
