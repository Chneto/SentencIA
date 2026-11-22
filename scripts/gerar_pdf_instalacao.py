#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gera `docs/INSTALACAO.pdf` a partir de `docs/INSTALACAO.md` usando ReportLab.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
import re
from typing import List

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer

RAIZ = Path(__file__).resolve().parent.parent
MD_PADRAO = RAIZ / "docs" / "INSTALACAO.md"
PDF_PADRAO = RAIZ / "docs" / "INSTALACAO.pdf"


def _formatar_inline(texto: str) -> str:
    s = escape(texto)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", s)
    s = re.sub(r"`([^`]+)`", r"<font name='Courier'>\1</font>", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    return s


def gerar_pdf_instalacao(origem_md: Path = MD_PADRAO, destino_pdf: Path = PDF_PADRAO) -> Path:
    texto_md = Path(origem_md).read_text(encoding="utf-8")
    destino_pdf = Path(destino_pdf)
    destino_pdf.parent.mkdir(parents=True, exist_ok=True)

    base = getSampleStyleSheet()
    st_h1 = ParagraphStyle(
        "InstH1",
        parent=base["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#1E3A5F"),
        spaceAfter=10,
    )
    st_h2 = ParagraphStyle(
        "InstH2",
        parent=base["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12.5,
        leading=16,
        textColor=colors.HexColor("#1E3A5F"),
        spaceBefore=10,
        spaceAfter=6,
    )
    st_h3 = ParagraphStyle(
        "InstH3",
        parent=base["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor("#2C3E50"),
        spaceBefore=6,
        spaceAfter=4,
    )
    st_body = ParagraphStyle(
        "InstBody",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13.5,
        spaceAfter=5,
    )
    st_code = ParagraphStyle(
        "InstCode",
        fontName="Courier",
        fontSize=8.2,
        leading=11,
        backColor=colors.HexColor("#F4F5F7"),
        borderPadding=5,
        leftIndent=8,
        rightIndent=8,
        spaceBefore=4,
        spaceAfter=6,
    )

    story: List[object] = []
    em_codigo = False
    bloco_codigo: List[str] = []

    for linha in texto_md.splitlines():
        if linha.strip().startswith("```"):
            if em_codigo:
                story.append(Preformatted("\n".join(bloco_codigo), st_code))
                bloco_codigo = []
                em_codigo = False
            else:
                em_codigo = True
            continue
        if em_codigo:
            bloco_codigo.append(linha)
            continue

        limpa = linha.strip()
        if not limpa or limpa == "---":
            story.append(Spacer(1, 0.15 * cm))
            continue
        if limpa.startswith("# "):
            story.append(Paragraph(_formatar_inline(limpa[2:].strip()), st_h1))
        elif limpa.startswith("## "):
            story.append(Paragraph(_formatar_inline(limpa[3:].strip()), st_h2))
        elif limpa.startswith("### "):
            story.append(Paragraph(_formatar_inline(limpa[4:].strip()), st_h3))
        elif limpa.startswith("|"):
            if set(limpa.replace("|", "").replace(":", "").replace("-", "").strip()) == set():
                continue
            colunas = [c.strip() for c in limpa.strip("|").split("|")]
            story.append(Paragraph(" • ".join(_formatar_inline(c) for c in colunas if c), st_body))
        elif limpa.startswith(("- ", "* ")):
            story.append(Paragraph("• " + _formatar_inline(limpa[2:].strip()), st_body))
        elif limpa.startswith(">"):
            story.append(Paragraph(_formatar_inline(limpa.lstrip("> ").strip()), st_body))
        else:
            story.append(Paragraph(_formatar_inline(limpa), st_body))

    doc = SimpleDocTemplate(
        str(destino_pdf),
        pagesize=A4,
        leftMargin=2.0 * cm,
        rightMargin=2.0 * cm,
        topMargin=2.0 * cm,
        bottomMargin=2.0 * cm,
        title="Guia de Instalação — SentencIA 2.1.0",
        author="FChNeto",
    )
    doc.build(story)
    return destino_pdf


if __name__ == "__main__":
    gerado = gerar_pdf_instalacao()
    print(f"PDF gerado em {gerado} ({gerado.stat().st_size} bytes).")
