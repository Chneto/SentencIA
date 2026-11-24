"""
Exportação da sentença em .docx (ABNT/Magistratura), HTML compatível com o PJe e .pdf.

v2.0.0: todas as funções aceitam ``marca_dagua`` (ex.: :data:`MARCA_DAGUA_NAO_AUDITADA`). Quando
informada, o documento é marcado de forma visível em todas as páginas (cabeçalho do .docx,
faixa no topo do HTML e texto diagonal semitransparente em cada página do .pdf), para que uma
minuta não aprovada pelo auditor jamais seja confundida com a versão final.

Os três formatos partem dos MESMOS blocos de parágrafo (:mod:`app.export.rich_text`): o texto
editado na folha — com negrito, itálico, alinhamento, recuos e margens — chega ao arquivo como o
usuário o deixou; sem edição, vale o padrão forense (justificado, recuo de 1,25 cm, entrelinhas 1,5).
"""

import io
import os
import tempfile
from pathlib import Path
import re
from typing import Optional

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor
import html
import pymupdf

from app.core.config import TEMPLATES_DIR, load_settings
from app.export.rich_text import (
    _para_fonte,
    adicionar_blocos_docx,
    blocos_da_sentenca,
    blocos_para_html,
    eh_ordem_pos_julgamento,
    formatacao_da_sentenca,
    nivel_titulo,
)
from app.generation.sentence_generator import SentencaGerada

#: Texto padrão da marca d'água de minuta não aprovada pelo auditor.
MARCA_DAGUA_NAO_AUDITADA = "MINUTA NÃO AUDITADA"

#: Pasta de modelos distribuída com o programa (usada quando ``SENTENCIA_DATA_DIR`` reloca ``data/``).
_TEMPLATES_EMBUTIDOS = Path(__file__).resolve().parents[2] / "data" / "templates_gabinete"


def _localizar_timbre() -> Optional[Path]:
    """Timbre do tribunal: primeiro na pasta de dados do usuário, depois na distribuída com o programa."""
    for pasta in (TEMPLATES_DIR, _TEMPLATES_EMBUTIDOS):
        candidato = pasta / "timbre_tjrn.docx"
        if candidato.exists():
            return candidato
    return None


def _marca_normalizada(marca_dagua: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(marca_dagua or "")).strip()


# Atalhos de classificação (mantidos por compatibilidade; a regra vive em ``rich_text``).
def _is_section_heading(text: str) -> bool:
    """Título formal de seção/subseção: linha curta, sem ponto final, que não seja ordem do pós-julgamento."""
    return nivel_titulo(text) > 0


def _is_post_sentence_order(text: str) -> bool:
    """Comando ordenado do pós-julgamento (1. Suspendam-se..., a) Comunique-se...)."""
    return eh_ordem_pos_julgamento(text)


def _aplicar_marca_dagua_docx(doc: "docx.document.Document", marca: str) -> None:
    """Insere a marca em destaque no cabeçalho de todas as seções (aparece em todas as páginas)."""
    for section in doc.sections:
        header = section.header
        header.is_linked_to_previous = False
        p = header.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(f"*** {marca} ***")
        run.bold = True
        run.font.size = Pt(14)
        run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
    try:
        doc.core_properties.subject = marca
        doc.core_properties.keywords = marca
    except Exception:
        pass


def _campo_docx(paragrafo: "docx.text.paragraph.Paragraph", instrucao: str, tamanho_pt: float) -> None:
    """Insere um campo do Word (``PAGE``/``NUMPAGES``) que o Word/LibreOffice atualiza sozinho."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    run = paragrafo.add_run()
    run.font.size = Pt(tamanho_pt)
    for tipo, texto in (("begin", None), (None, instrucao), ("separate", None), (None, "1"), ("end", None)):
        if tipo:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), tipo)
        elif texto == instrucao:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = f" {instrucao} "
        else:
            el = OxmlElement("w:t")
            el.text = texto
        run._r.append(el)


def _numerar_paginas_docx(doc: "docx.document.Document") -> None:
    """Rodapé centralizado ``Página x de y`` em todas as seções (preserva o rodapé institucional do timbre)."""
    for section in doc.sections:
        rodape = section.footer
        rodape.is_linked_to_previous = False
        p = rodape.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run("Página ").font.size = Pt(10)
        _campo_docx(p, "PAGE", 10)
        p.add_run(" de ").font.size = Pt(10)
        _campo_docx(p, "NUMPAGES", 10)


def _numerar_paginas_pdf(conteudo_pdf: bytes, margem_inferior_cm: float) -> bytes:
    """Carimba ``Página x de y`` no centro da margem inferior de cada página."""
    with pymupdf.open(stream=conteudo_pdf, filetype="pdf") as documento:
        total = documento.page_count
        for i, pagina in enumerate(documento, start=1):
            texto = f"Página {i} de {total}"
            largura = pymupdf.get_text_length(texto, fontname="helv", fontsize=9)
            y = pagina.rect.height - min(margem_inferior_cm * 72.0 / 2.54 / 2, 40)
            pagina.insert_text(pymupdf.Point((pagina.rect.width - largura) / 2, y), texto, fontsize=9, fontname="helv", color=(0.25, 0.25, 0.25))
        return documento.tobytes(garbage=3, deflate=True)


def exportar_docx(sentenca: SentencaGerada, output_path: Path, marca_dagua: Optional[str] = None) -> Path:
    """
    Exporta a sentença em formato Word (.docx) com estrita observância às normas da ABNT e da Magistratura:
    - Margens (padrão ABNT NBR 14724: sup. 3,0 / esq. 3,0 / inf. 2,0 / dir. 2,0 cm), ajustáveis na folha;
    - Corpo do texto: Times New Roman 12pt, justificado, entrelinhas 1,5 e recuo de primeira linha de 1,25 cm;
    - Citações diretas longas: recuo de 4,0 cm, entrelinhas simples, fonte 10pt e sem aspas externas (NBR 10520);
    - Títulos de seção em negrito (principais) e itálico (subseções), justificados e sem cor;
    - Comandos executórios pós-trânsito com recuo à esquerda; fórmulas de publicação, datação e assinatura
      alinhadas adequadamente;
    - Qualquer formatação aplicada na folha do editor (negrito, itálico, alinhamento, recuos) é preservada.

    ``marca_dagua`` (ex.: "MINUTA NÃO AUDITADA"): quando informada, é inserida em destaque no
    cabeçalho de todas as páginas e numa faixa no início do corpo.
    """
    settings = load_settings()
    marca = _marca_normalizada(marca_dagua)
    timbre_path = _localizar_timbre()
    if timbre_path is not None:
        doc = docx.Document(str(timbre_path))
        # Remove parágrafos vazios do corpo do template mantendo cabeçalho/rodapé institucional (timbre TJRN)
        for p in list(doc.paragraphs):
            p._element.getparent().remove(p._element)
    else:
        doc = docx.Document()

    fmt = formatacao_da_sentenca(sentenca)
    margens = fmt["margens_cm"]
    fonte = fmt.get("fonte") or settings.fonte_docx
    tamanho = float(fmt.get("tamanho_pt") or settings.tamanho_fonte_docx)
    for section in doc.sections:
        section.top_margin = Cm(margens["superior"])
        section.left_margin = Cm(margens["esquerda"])
        section.bottom_margin = Cm(margens["inferior"])
        section.right_margin = Cm(margens["direita"])

    style_normal = doc.styles["Normal"]
    style_normal.font.name = fonte
    style_normal.font.size = Pt(tamanho)
    if fmt["numerar_paginas"]:
        _numerar_paginas_docx(doc)

    if marca:
        _aplicar_marca_dagua_docx(doc, marca)
        faixa = doc.add_paragraph()
        faixa.alignment = WD_ALIGN_PARAGRAPH.CENTER
        faixa.paragraph_format.first_line_indent = Cm(0)
        faixa.paragraph_format.space_after = Pt(12)
        run_faixa = faixa.add_run(f"{marca} — documento de trabalho; não assinar nem juntar aos autos.")
        run_faixa.bold = True
        run_faixa.font.size = Pt(12)
        run_faixa.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)

    adicionar_blocos_docx(doc, blocos_da_sentenca(sentenca), fonte, tamanho)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    return output_path


_REF_ID_PJE_RE = re.compile(r"\bID-(\d+)\b")


def _formatar_referencias_pje(texto: str, formato: str) -> str:
    """Converte ``ID-4100201`` em ``Id. 4100201`` quando ``formato == 'id_ponto'`` (só na saída PJe)."""
    if formato == "id_ponto":
        return _REF_ID_PJE_RE.sub(r"Id. \1", texto)
    return texto


def exportar_html_pje(
    sentenca: SentencaGerada,
    marca_dagua: Optional[str] = None,
    *,
    formato_referencia: Optional[str] = None,
) -> str:
    """
    Gera HTML com estilos inline estritamente compatíveis com o editor do PJe / eproc / Projudi / SAJ,
    seguindo a lista branca de tags/atributos/propriedades CSS, as normas da ABNT (recuo de 1,25 cm no
    corpo e 4 cm nas citações longas destacadas) e preservando a formatação aplicada na folha do editor.

    ``marca_dagua``: quando informada, acrescenta uma faixa de aviso no topo do documento (único bloco
    com ``color`` permitido).
    ``formato_referencia``: ``"id_hifen"`` (padrão: ``ID-4100201``) ou ``"id_ponto"`` (``Id. 4100201``).
    Afeta apenas o HTML retornado, nunca o texto auditado da sentença.
    """
    settings = load_settings()
    blocos_html = []
    marca = _marca_normalizada(marca_dagua)
    fmt = formatacao_da_sentenca(sentenca)
    fonte_segura = _para_fonte(fmt.get("fonte") or settings.fonte_docx) or "Times New Roman"
    tamanho_pt = float(fmt.get("tamanho_pt") or settings.tamanho_fonte_docx)
    fmt_ref = formato_referencia or getattr(settings, "formato_referencia_pje", "id_hifen")
    transformador = (lambda t: _formatar_referencias_pje(t, fmt_ref)) if fmt_ref == "id_ponto" else None

    if marca:
        blocos_html.append(
            f'<p style="text-align: center; font-family: \'{fonte_segura}\', serif; '
            f'font-size: 14pt; font-weight: bold; color: #c00000; '
            f'margin-top: 0pt; margin-bottom: 12pt; text-indent: 0cm;">{html.escape(marca)} — documento de trabalho; '
            f'não assinar nem juntar aos autos.</p>'
        )
    blocos_html.append(
        blocos_para_html(
            blocos_da_sentenca(sentenca),
            fonte=fonte_segura,
            tamanho_pt=tamanho_pt,
            permitir_cor=False,
            transformar_texto=transformador,
        )
    )
    return "\n".join(blocos_html)


def _aplicar_marca_dagua_pdf(conteudo_pdf: bytes, marca: str) -> bytes:
    """Carimba ``marca`` em diagonal (45 graus), semitransparente, no centro de cada página do PDF."""
    with pymupdf.open(stream=conteudo_pdf, filetype="pdf") as documento:
        for pagina in documento:
            rect = pagina.rect
            fontsize = 54
            largura = pymupdf.get_text_length(marca, fontname="helv", fontsize=fontsize)
            centro = pymupdf.Point(rect.width / 2, rect.height / 2)
            origem = pymupdf.Point(centro.x - largura / 2, centro.y + fontsize / 3)
            pagina.insert_text(
                origem,
                marca,
                fontsize=fontsize,
                fontname="helv",
                color=(0.8, 0.0, 0.0),
                fill_opacity=0.18,
                stroke_opacity=0.18,
                morph=(centro, pymupdf.Matrix(-45)),
                overlay=True,
            )
            pagina.insert_text(
                pymupdf.Point(rect.x0 + 40, rect.y0 + 28),
                f"{marca} - documento de trabalho; não assinar nem juntar aos autos.",
                fontsize=9,
                fontname="helv",
                color=(0.75, 0.0, 0.0),
            )
        return documento.tobytes(garbage=3, deflate=True)


def _gravar_bytes_atomico(destino: Path, conteudo: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{destino.stem}.", suffix=destino.suffix or ".tmp", dir=str(destino.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(conteudo)
        os.replace(tmp, destino)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def exportar_pdf(sentenca: SentencaGerada, output_path: Path, marca_dagua: Optional[str] = None) -> Path:
    """
    Gera versão em PDF paginada da sentença via PyMuPDF Story/HTML layout com as margens da folha
    (padrão ABNT: sup. 3 cm, esq. 3 cm, inf. 2 cm, dir. 2 cm).

    ``marca_dagua``: quando informada, cada página recebe o texto em diagonal e uma linha de aviso.
    """
    settings = load_settings()
    marca = _marca_normalizada(marca_dagua)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = formatacao_da_sentenca(sentenca)
    html_content = f"""
    <html>
    <head>
        <style>
            @page {{
                margin: 0;
            }}
            body {{
                margin: 0;
                font-family: '{fmt.get("fonte") or settings.fonte_docx}', serif;
                font-size: {fmt.get("tamanho_pt") or settings.tamanho_fonte_docx}pt;
                line-height: 1.5;
            }}
            p {{ text-align: justify; margin-bottom: 8pt; }}
        </style>
    </head>
    <body>
        {exportar_html_pje(sentenca, marca_dagua=marca or None, formato_referencia="id_hifen")}
    </body>
    </html>
    """
    story = pymupdf.Story(html=html_content)
    # Renderiza em memória: evita manter o arquivo aberto (Windows) e permite carimbar a marca
    # antes de gravar o arquivo final uma única vez.
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    mediabox = pymupdf.paper_rect("a4")
    m = fmt["margens_cm"]
    cm = 72.0 / 2.54
    where = mediabox + (m["esquerda"] * cm, m["superior"] * cm, -m["direita"] * cm, -m["inferior"] * cm)
    more = True
    while more:
        device = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    conteudo = buffer.getvalue()
    if fmt["numerar_paginas"]:
        conteudo = _numerar_paginas_pdf(conteudo, m["inferior"])
    if marca:
        conteudo = _aplicar_marca_dagua_pdf(conteudo, marca)
    _gravar_bytes_atomico(output_path, conteudo)
    return output_path
