"""
Texto rico da sentença: uma única representação para o editor, o Word, o PDF e o PJe.

Antes (v2.0.0 inicial) o texto da sentença trafegava como texto puro e cada camada (folha do editor,
.docx, HTML do PJe) reclassificava os parágrafos por conta própria. Um parágrafo numerado longo
("1.1. Rejeição da preliminar: Não merece acolhimento...") era confundido com título e saía em
negrito, à esquerda e (na folha) azul. Este módulo concentra a regra e permite que a edição feita
na folha (negrito, itálico, alinhamento, recuos, margens...) chegue intacta ao arquivo exportado:

* :func:`texto_para_blocos` — aplica o padrão forense (ABNT NBR 14724/6024/10520 + manuais de
  sentença do CNJ/ENFAM) ao texto puro gerado pelo motor;
* :func:`html_para_blocos` / :func:`blocos_para_html` — leem o HTML do editor (``contenteditable``)
  e o regravam LIMPO (lista branca de tags e de propriedades CSS), de modo que nada do que o usuário
  cola ou digita injeta script/estilo arbitrário;
* :func:`adicionar_blocos_docx` — grava os blocos num ``docx.Document``.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable, Dict, Iterable, List, Optional

# ----------------------------------------------------------------------------------------------
# Modelo
# ----------------------------------------------------------------------------------------------

#: Margens ABNT NBR 14724 (cm) — padrão da folha.
MARGENS_ABNT: Dict[str, float] = {"superior": 3.0, "esquerda": 3.0, "inferior": 2.0, "direita": 2.0}
_MARGEM_MIN, _MARGEM_MAX = 0.5, 6.0

_ALINHAMENTOS = ("left", "center", "right", "justify")
_PT_POR_CM = 72.0 / 2.54


@dataclass
class Trecho:
    """Sequência de texto com formatação de caractere uniforme (ou uma quebra de linha)."""

    texto: str = ""
    negrito: bool = False
    italico: bool = False
    sublinhado: bool = False
    tachado: bool = False
    sobrescrito: bool = False
    subscrito: bool = False
    tamanho_pt: Optional[float] = None
    cor: Optional[str] = None
    fonte: Optional[str] = None
    quebra: bool = False

    def _chave(self) -> tuple:
        return (
            self.negrito, self.italico, self.sublinhado, self.tachado, self.sobrescrito,
            self.subscrito, self.tamanho_pt, self.cor, self.fonte, self.quebra,
        )


@dataclass
class Bloco:
    """Um parágrafo."""

    trechos: List[Trecho] = field(default_factory=list)
    alinhamento: str = "justify"
    recuo_primeira_cm: float = 1.25
    recuo_esquerda_cm: float = 0.0
    entrelinhas: float = 1.5
    antes_pt: float = 0.0
    depois_pt: float = 4.0

    @property
    def texto(self) -> str:
        return "".join("\n" if t.quebra else t.texto for t in self.trechos)


#: Estilos de parágrafo (padrão forense). ``negrito``/``italico`` valem para o parágrafo inteiro.
ESTILOS: Dict[str, Dict[str, Any]] = {
    "corpo": dict(alinhamento="justify", recuo_primeira_cm=1.25, recuo_esquerda_cm=0.0, entrelinhas=1.5, antes_pt=0.0, depois_pt=4.0),
    "secao": dict(alinhamento="justify", recuo_primeira_cm=0.0, recuo_esquerda_cm=0.0, entrelinhas=1.5, antes_pt=12.0, depois_pt=6.0, negrito=True),
    "subsecao": dict(alinhamento="justify", recuo_primeira_cm=0.0, recuo_esquerda_cm=0.0, entrelinhas=1.5, antes_pt=8.0, depois_pt=4.0, italico=True),
    "citacao": dict(alinhamento="justify", recuo_primeira_cm=0.0, recuo_esquerda_cm=4.0, entrelinhas=1.0, antes_pt=6.0, depois_pt=6.0, tamanho_pt=10.0),
    "ordem": dict(alinhamento="justify", recuo_primeira_cm=0.0, recuo_esquerda_cm=1.25, entrelinhas=1.5, antes_pt=2.0, depois_pt=4.0),
    "centro": dict(alinhamento="center", recuo_primeira_cm=0.0, recuo_esquerda_cm=0.0, entrelinhas=1.5, antes_pt=6.0, depois_pt=12.0, negrito=True),
    "fecho": dict(alinhamento="center", recuo_primeira_cm=0.0, recuo_esquerda_cm=0.0, entrelinhas=1.5, antes_pt=14.0, depois_pt=8.0, negrito=True),
    "data": dict(alinhamento="right", recuo_primeira_cm=0.0, recuo_esquerda_cm=0.0, entrelinhas=1.5, antes_pt=12.0, depois_pt=16.0),
    "assinatura": dict(alinhamento="center", recuo_primeira_cm=0.0, recuo_esquerda_cm=0.0, entrelinhas=1.15, antes_pt=20.0, depois_pt=6.0),
    "identificacao": dict(alinhamento="left", recuo_primeira_cm=0.0, recuo_esquerda_cm=0.0, entrelinhas=1.15, antes_pt=0.0, depois_pt=2.0),
    "lista": dict(alinhamento="justify", recuo_primeira_cm=0.0, recuo_esquerda_cm=1.25, entrelinhas=1.5, antes_pt=0.0, depois_pt=2.0),
}


def _novo_bloco(texto: str, estilo: str, *, quebras: bool = True) -> Bloco:
    """Bloco com o ``estilo`` indicado; ``\\n`` do texto vira quebra de linha (``quebras=True``)."""
    cfg = dict(ESTILOS[estilo])
    negrito = bool(cfg.pop("negrito", False))
    italico = bool(cfg.pop("italico", False))
    tamanho = cfg.pop("tamanho_pt", None)
    bloco = Bloco(**cfg)
    linhas = texto.split("\n") if quebras else [texto.replace("\n", " ")]
    for i, linha in enumerate(linhas):
        if i:
            bloco.trechos.append(Trecho(quebra=True))
        if linha:
            bloco.trechos.append(Trecho(texto=linha, negrito=negrito, italico=italico, tamanho_pt=tamanho))
    return bloco


# ----------------------------------------------------------------------------------------------
# Texto puro (motor) -> blocos, com o padrão forense
# ----------------------------------------------------------------------------------------------

_POST_SENTENCE_VERBS = (
    r"Lance-se|Oficie-se|Expeça-se|Cumpra-se|Intime-se|Intimem-se|"
    r"Comunique-se|Proceda-se|Suspendam-se|Mantenham-se|Concedo|Registre-se|Arquivem-se"
)
_ROMANO_RE = re.compile(r"^(?:I|II|III|IV|V|VI|VII|VIII|IX|X)\s*[–—-]\s+\S")
_NUM_N1_RE = re.compile(r"^\d+\.\s+[A-ZÀ-Ú]")
_NUM_N2_RE = re.compile(r"^\d+(?:\.\d+)+\.?\s+[A-ZÀ-Ú]")
_ORDEM_RE = re.compile(rf"^(?:\d+\.|[a-z]\))\s+(?:{_POST_SENTENCE_VERBS})\b", re.IGNORECASE)
_FECHO_RE = re.compile(r"^Publique-se\.\s*Registre-se\.\s*Intimem-se\.", re.IGNORECASE)
_DATA_RE = re.compile(r",\s*\d{1,2}\s+de\s+[a-zç]+\s+de\s+\d{4}\.?$", re.IGNORECASE)
_TITULO_MAX = 160
_ID_CABECALHO_RE = re.compile(r"^(?:PROCESSO|AUTOR|AUTORA|PROCEDIMENTO|A[ÇC][ÃA]O|AUTOS|OFENDIDA|ACUSAD[OA]|R[ÉE]U|NATUREZA|MEDIDA)\b", re.IGNORECASE)


def nivel_titulo(linha: str) -> int:
    """0 = não é título; 1 = seção principal (``I –``, ``1.``); 2 = subseção (``1.1.``, ``3.1.1.``).

    Título é uma linha CURTA, sem ponto final e que não seja ordem do pós-julgamento: um parágrafo
    numerado longo ("1. O réu foi...") é texto corrido e deve sair justificado, não em destaque.
    """
    t = (linha or "").strip()
    if not t or len(t) > _TITULO_MAX or t.endswith((".", ";")):
        return 0
    if _ORDEM_RE.match(t):
        return 0
    if _ROMANO_RE.match(t):
        return 1
    if _NUM_N2_RE.match(t):
        return 2
    if _NUM_N1_RE.match(t):
        return 1
    return 0


def eh_ordem_pos_julgamento(texto: str) -> bool:
    return bool(_ORDEM_RE.match((texto or "").strip()))


def eh_formula_encerramento(texto: str) -> bool:
    return bool(_FECHO_RE.match((texto or "").strip()))


def eh_linha_de_data(texto: str) -> bool:
    return bool(_DATA_RE.search((texto or "").strip()))


def eh_bloco_assinatura(texto: str) -> bool:
    return "Juiz(a) de Direito" in texto or "Juiz de Direito" in texto or "Juíza de Direito" in texto


def _blocos_cabecalho(texto: str) -> List[Bloco]:
    """Cabeçalho: tribunal/juízo centralizados em negrito; identificação do processo à esquerda."""
    linhas = [linha.strip() for linha in texto.split("\n") if linha.strip()]
    titulo: List[str] = []
    ident: List[str] = []
    for linha in linhas:
        (ident if (ident or _ID_CABECALHO_RE.match(linha)) else titulo).append(linha)
    blocos: List[Bloco] = []
    if titulo:
        blocos.append(_novo_bloco("\n".join(titulo), "centro"))
        blocos[-1].depois_pt = 10.0
    if ident:
        for i, linha in enumerate(ident):
            b = _novo_bloco(linha, "identificacao")
            if i == len(ident) - 1:
                b.depois_pt = 10.0
            blocos.append(b)
    return blocos


def texto_para_blocos(texto: str) -> List[Bloco]:
    """Converte o texto puro da sentença (parágrafos separados por linha em branco) em blocos forenses."""
    saida: List[Bloco] = []
    for idx, bruto in enumerate(str(texto or "").split("\n\n")):
        conteudo = bruto.strip()
        if not conteudo:
            continue
        eh_citacao = bruto.startswith("    ") and not bruto.strip().startswith("•")
        linhas = [linha.strip() for linha in conteudo.split("\n") if linha.strip()]

        if idx == 0 or conteudo.startswith(("PODER JUDICIÁRIO", "TRIBUNAL DE JUSTIÇA")):
            saida.extend(_blocos_cabecalho(conteudo))
            continue
        if conteudo == "SENTENÇA" or conteudo.startswith("SENTENÇA\n"):
            saida.append(_novo_bloco(linhas[0], "centro"))
            conteudo = "\n".join(linhas[1:])
            linhas = linhas[1:]
            if not linhas:
                continue
        if eh_citacao:
            miolo = " ".join(linhas).strip().strip('"').strip("'").strip("“”")
            saida.append(_novo_bloco(miolo, "citacao", quebras=False))
            continue
        if eh_formula_encerramento(conteudo):
            saida.append(_novo_bloco(conteudo, "fecho", quebras=False))
            continue
        if eh_bloco_assinatura(conteudo):
            saida.append(_novo_bloco("\n".join(linhas), "assinatura"))
            continue

        for linha in linhas:
            nivel = nivel_titulo(linha)
            if nivel == 1:
                saida.append(_novo_bloco(linha, "secao", quebras=False))
            elif nivel == 2:
                saida.append(_novo_bloco(linha, "subsecao", quebras=False))
            elif eh_formula_encerramento(linha):
                saida.append(_novo_bloco(linha, "fecho", quebras=False))
            elif eh_linha_de_data(linha) and len(linha) < 120:
                saida.append(_novo_bloco(linha, "data", quebras=False))
            elif eh_ordem_pos_julgamento(linha):
                saida.append(_novo_bloco(linha, "ordem", quebras=False))
            elif linha.startswith("•"):
                saida.append(_novo_bloco(linha, "lista", quebras=False))
            else:
                saida.append(_novo_bloco(linha, "corpo", quebras=False))
    return saida


# ----------------------------------------------------------------------------------------------
# HTML (editor) -> blocos, com lista branca
# ----------------------------------------------------------------------------------------------

_VOID = {"br", "hr", "img", "wbr", "meta", "link", "input"}
_BLOCOS_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre"}
_IGNORAR = {"script", "style", "head", "title", "template", "noscript"}
_TAMANHO_FONT_TAG = {1: 8.0, 2: 10.0, 3: 12.0, 4: 14.0, 5: 18.0, 6: 24.0, 7: 36.0}
_TAMANHO_PALAVRA = {
    "xx-small": 8.0, "x-small": 9.0, "small": 10.0, "medium": 12.0, "large": 14.0,
    "x-large": 18.0, "xx-large": 24.0, "xxx-large": 32.0,
}
_CORES_NOMEADAS = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000", "blue": "#0000ff", "green": "#008000",
    "gray": "#808080", "grey": "#808080", "orange": "#ffa500", "purple": "#800080", "yellow": "#ffff00",
    "navy": "#000080", "maroon": "#800000",
}
_FONTE_OK_RE = re.compile(r"^[A-Za-zÀ-ú0-9 \-]{1,40}$")


def _declaracoes(estilo: str) -> Dict[str, str]:
    saida: Dict[str, str] = {}
    for parte in str(estilo or "").split(";"):
        if ":" not in parte:
            continue
        k, v = parte.split(":", 1)
        k, v = k.strip().lower(), v.strip().lower()
        if k and v:
            saida[k] = v
    return saida


_NUM_UNIDADE_RE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*(cm|mm|pt|px|in|em|rem|%|)$")


def _para_cm(valor: Optional[str]) -> Optional[float]:
    m = _NUM_UNIDADE_RE.match((valor or "").strip())
    if not m:
        return None
    n, un = float(m.group(1)), m.group(2)
    if un == "cm":
        return n
    if un == "mm":
        return n / 10.0
    if un == "pt":
        return n / _PT_POR_CM
    if un in ("px", ""):
        return n * 0.75 / _PT_POR_CM
    if un == "in":
        return n * 2.54
    if un in ("em", "rem"):
        return n * 12.0 / _PT_POR_CM
    return None


def _para_pt(valor: Optional[str]) -> Optional[float]:
    v = (valor or "").strip()
    if v in _TAMANHO_PALAVRA:
        return _TAMANHO_PALAVRA[v]
    m = _NUM_UNIDADE_RE.match(v)
    if not m:
        return None
    n, un = float(m.group(1)), m.group(2)
    if un == "pt":
        pt = n
    elif un in ("px", ""):
        pt = n * 0.75
    elif un in ("em", "rem"):
        pt = n * 12.0
    elif un == "%":
        pt = n * 12.0 / 100.0
    elif un == "cm":
        pt = n * _PT_POR_CM
    elif un == "mm":
        pt = n * _PT_POR_CM / 10.0
    elif un == "in":
        pt = n * 72.0
    else:
        return None
    return round(min(max(pt, 6.0), 72.0), 1)


def _para_entrelinhas(valor: Optional[str]) -> Optional[float]:
    v = (valor or "").strip()
    m = _NUM_UNIDADE_RE.match(v)
    if not m:
        return None
    n, un = float(m.group(1)), m.group(2)
    if un == "":
        fator = n
    elif un == "%":
        fator = n / 100.0
    elif un in ("pt", "px"):
        fator = (n * (0.75 if un == "px" else 1.0)) / 12.0
    elif un in ("em", "rem"):
        fator = n
    else:
        return None
    return round(min(max(fator, 0.8), 3.0), 2)


def _para_cor(valor: Optional[str]) -> Optional[str]:
    v = (valor or "").strip().lower()
    if v in _CORES_NOMEADAS:
        return _CORES_NOMEADAS[v]
    m = re.match(r"^#([0-9a-f]{3}|[0-9a-f]{6})$", v)
    if m:
        h = m.group(1)
        return "#" + (h if len(h) == 6 else "".join(c * 2 for c in h))
    m = re.match(r"^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})", v)
    if m:
        r, g, b = (min(int(x), 255) for x in m.groups())
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


def _para_fonte(valor: Optional[str]) -> Optional[str]:
    primeira = (valor or "").split(",")[0].strip().strip("'\"")
    return primeira if _FONTE_OK_RE.match(primeira) else None


def _delta_caractere(tag: str, attrs: Dict[str, str], decl: Dict[str, str]) -> Dict[str, Any]:
    d: Dict[str, Any] = {}
    if tag in ("b", "strong", "h1", "h2", "h3", "h4", "h5", "h6"):
        d["negrito"] = True
    if tag in ("i", "em"):
        d["italico"] = True
    if tag == "u":
        d["sublinhado"] = True
    if tag in ("s", "strike", "del"):
        d["tachado"] = True
    if tag == "sup":
        d["sobrescrito"], d["subscrito"] = True, False
    if tag == "sub":
        d["subscrito"], d["sobrescrito"] = True, False
    if tag == "font":
        try:
            d["tamanho_pt"] = _TAMANHO_FONT_TAG.get(int(attrs.get("size", "")))
        except ValueError:
            pass
        if attrs.get("color"):
            d["cor"] = _para_cor(attrs["color"])
        if attrs.get("face"):
            d["fonte"] = _para_fonte(attrs["face"])
    peso = decl.get("font-weight")
    if peso:
        if peso in ("bold", "bolder") or (peso.isdigit() and int(peso) >= 600):
            d["negrito"] = True
        elif peso in ("normal", "lighter") or (peso.isdigit() and int(peso) < 600):
            d["negrito"] = False
    est = decl.get("font-style")
    if est:
        d["italico"] = est in ("italic", "oblique")
    deco = decl.get("text-decoration-line") or decl.get("text-decoration") or ""
    if "underline" in deco:
        d["sublinhado"] = True
    if "line-through" in deco:
        d["tachado"] = True
    if "font-size" in decl:
        d["tamanho_pt"] = _para_pt(decl["font-size"])
    if "color" in decl:
        d["cor"] = _para_cor(decl["color"])
    if "font-family" in decl:
        d["fonte"] = _para_fonte(decl["font-family"])
    va = decl.get("vertical-align")
    if va in ("super", "sup"):
        d["sobrescrito"], d["subscrito"] = True, False
    elif va == "sub":
        d["subscrito"], d["sobrescrito"] = True, False
    elif va == "baseline":
        d["sobrescrito"] = d["subscrito"] = False
    return {k: v for k, v in d.items() if v is not None}


class _Leitor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocos: List[Bloco] = []
        self._atual: Optional[Bloco] = None
        self._pilha: List[Dict[str, Any]] = []
        self._listas: List[List[Any]] = []
        self._ignorar = 0

    # -- utilidades -------------------------------------------------------------------------
    def _fmt(self) -> Dict[str, Any]:
        fmt: Dict[str, Any] = {}
        for item in self._pilha:
            fmt.update(item["delta"])
        return fmt

    def _alinhamento_herdado(self) -> str:
        for item in reversed(self._pilha):
            if item.get("alinhamento"):
                return item["alinhamento"]
        return "justify"

    def _abrir_bloco(self, **props: Any) -> None:
        self._fechar_bloco()
        base: Dict[str, Any] = dict(alinhamento=self._alinhamento_herdado())
        base.update(props)
        self._atual = Bloco(**base)

    def _fechar_bloco(self) -> None:
        b = self._atual
        self._atual = None
        if b is None:
            return
        while b.trechos and not b.trechos[-1].quebra and not b.trechos[-1].texto.strip():
            b.trechos.pop()
        if b.trechos and not b.trechos[-1].quebra:
            b.trechos[-1].texto = b.trechos[-1].texto.rstrip()
        # Um <p><br></p> do editor é um parágrafo em branco (mantido); bloco sem nada é descartado.
        if b.trechos:
            self.blocos.append(b)

    # -- parser -----------------------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        tag = tag.lower()
        if tag in _IGNORAR:
            self._ignorar += 1
            return
        if self._ignorar:
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        decl = _declaracoes(a.get("style", ""))
        if tag == "br":
            if self._atual is None:
                self._abrir_bloco(**self._props_padrao_p())
            self._atual.trechos.append(Trecho(quebra=True))
            return
        if tag in _VOID:
            return
        entrada: Dict[str, Any] = {"tag": tag, "delta": _delta_caractere(tag, a, decl)}
        if tag in ("ul", "ol"):
            self._listas.append([tag, 0])
        if tag in _BLOCOS_TAGS:
            props = self._props_bloco(tag, a, decl)
            entrada["alinhamento"] = props["alinhamento"]
            self._pilha.append(entrada)
            self._abrir_bloco(**props)
            if tag == "li" and self._listas:
                lista = self._listas[-1]
                lista[1] += 1
                marcador = "• " if lista[0] == "ul" else f"{lista[1]}. "
                self._atual.trechos.append(Trecho(texto=marcador))
            return
        if decl.get("text-align") in _ALINHAMENTOS:
            entrada["alinhamento"] = decl["text-align"]
        self._pilha.append(entrada)

    def _props_padrao_p(self) -> Dict[str, Any]:
        return dict(alinhamento=self._alinhamento_herdado())

    def _props_bloco(self, tag: str, a: Dict[str, str], decl: Dict[str, str]) -> Dict[str, Any]:
        props: Dict[str, Any] = {}
        alin = decl.get("text-align") or a.get("align", "").lower()
        props["alinhamento"] = alin if alin in _ALINHAMENTOS else self._alinhamento_herdado()
        if tag == "li":
            props["recuo_primeira_cm"] = 0.0
            props["recuo_esquerda_cm"] = 1.25 * max(len(self._listas), 1)
            props["depois_pt"] = 2.0
        if tag == "blockquote":
            props["recuo_primeira_cm"] = 0.0
            props["recuo_esquerda_cm"] = 4.0
        ti = _para_cm(decl.get("text-indent"))
        if ti is not None:
            props["recuo_primeira_cm"] = round(max(min(ti, 15.0), -10.0), 2)
        ml = _para_cm(decl.get("margin-left") or decl.get("padding-left"))
        if ml is not None:
            props["recuo_esquerda_cm"] = round(max(min(ml, 15.0), 0.0), 2)
        entre = _para_entrelinhas(decl.get("line-height"))
        if entre is not None:
            props["entrelinhas"] = entre
        for chave, css in (("antes_pt", "margin-top"), ("depois_pt", "margin-bottom")):
            cm = _para_cm(decl.get(css))
            if cm is not None:
                props[chave] = round(max(min(cm * _PT_POR_CM, 72.0), 0.0), 1)
        return props

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _IGNORAR:
            self._ignorar = max(self._ignorar - 1, 0)
            return
        if self._ignorar or tag in _VOID:
            return
        if tag in ("ul", "ol") and self._listas:
            self._listas.pop()
        for i in range(len(self._pilha) - 1, -1, -1):
            if self._pilha[i]["tag"] == tag:
                del self._pilha[i:]
                break
        if tag in _BLOCOS_TAGS:
            self._fechar_bloco()

    def handle_data(self, data: str) -> None:
        if self._ignorar or not data:
            return
        texto = data.replace("\u200B", "").replace(" ", " ")
        texto = re.sub(r"[ \t\r\n\f]+", " ", texto)
        if not texto:
            return
        if self._atual is None:
            if not texto.strip():
                return
            self._abrir_bloco()
        trechos = self._atual.trechos
        if not trechos or trechos[-1].quebra:
            texto = texto.lstrip()
        elif trechos[-1].texto.endswith(" "):
            texto = texto.lstrip()
        if not texto:
            return
        fmt = self._fmt()
        novo = Trecho(texto=texto, **fmt)
        if trechos and not trechos[-1].quebra and trechos[-1]._chave() == novo._chave():
            trechos[-1].texto += texto
        else:
            trechos.append(novo)

    def concluir(self) -> List[Bloco]:
        self._fechar_bloco()
        return self.blocos


def html_para_blocos(html: str) -> List[Bloco]:
    """Lê o HTML do editor e devolve os blocos (somente o que a lista branca reconhece)."""
    leitor = _Leitor()
    leitor.feed(str(html or ""))
    leitor.close()
    return leitor.concluir()


# ----------------------------------------------------------------------------------------------
# Blocos -> HTML limpo
# ----------------------------------------------------------------------------------------------


def _num(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") or "0"


def _entrelinhas_css(v: float) -> str:
    s = f"{v:.2f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


def _estilo_bloco(b: Bloco, fonte: Optional[str], tamanho_pt: Optional[float]) -> str:
    partes = [
        f"text-align: {b.alinhamento}",
        f"text-indent: {_num(b.recuo_primeira_cm)}cm",
        f"margin-left: {_num(b.recuo_esquerda_cm)}cm",
        f"line-height: {_entrelinhas_css(b.entrelinhas)}",
        f"margin-top: {_num(b.antes_pt)}pt",
        f"margin-bottom: {_num(b.depois_pt)}pt",
    ]
    fonte_segura = _para_fonte(fonte) if fonte else None
    if fonte_segura:
        partes.append(f"font-family: '{fonte_segura}', serif")
    if tamanho_pt:
        partes.append(f"font-size: {_num(tamanho_pt)}pt")
    return "; ".join(partes)


def _html_trecho(
    t: Trecho,
    *,
    permitir_cor: bool = True,
    transformar_texto: Optional[Callable[[str], str]] = None,
) -> str:
    if t.quebra:
        return "<br />"
    css: List[str] = []
    if t.negrito:
        css.append("font-weight: bold")
    if t.italico:
        css.append("font-style: italic")
    deco = [d for d, on in (("underline", t.sublinhado), ("line-through", t.tachado)) if on]
    if deco:
        css.append("text-decoration: " + " ".join(deco))
    if t.tamanho_pt:
        css.append(f"font-size: {_num(t.tamanho_pt)}pt")
    if t.cor and permitir_cor:
        css.append(f"color: {t.cor}")
    fonte_segura = _para_fonte(t.fonte) if t.fonte else None
    if fonte_segura:
        css.append(f"font-family: '{fonte_segura}', serif")
    texto = transformar_texto(t.texto) if transformar_texto else t.texto
    corpo = _html.escape(texto, quote=False)
    if t.sobrescrito:
        corpo = f"<sup>{corpo}</sup>"
    elif t.subscrito:
        corpo = f"<sub>{corpo}</sub>"
    return f'<span style="{";".join(css)}">{corpo}</span>' if css else corpo


def blocos_para_html(
    blocos: Iterable[Bloco],
    *,
    fonte: Optional[str] = None,
    tamanho_pt: Optional[float] = None,
    permitir_cor: bool = True,
    transformar_texto: Optional[Callable[[str], str]] = None,
) -> str:
    """HTML limpo (um ``<p>`` por bloco, estilos inline). ``fonte``/``tamanho_pt`` entram em cada ``<p>``."""
    linhas: List[str] = []
    for b in blocos:
        miolo = "".join(
            _html_trecho(t, permitir_cor=permitir_cor, transformar_texto=transformar_texto)
            for t in b.trechos
        ) or "<br />"
        linhas.append(f'<p style="{_estilo_bloco(b, fonte, tamanho_pt)}">{miolo}</p>')
    return "\n".join(linhas)


def sanitizar_html(html: str) -> str:
    """HTML do editor -> HTML limpo (idempotente). Vazio se não restar conteúdo."""
    return blocos_para_html(html_para_blocos(html))


def texto_do_html(html: str) -> str:
    """Texto puro (parágrafos separados por linha em branco) do HTML do editor."""
    return "\n\n".join(b.texto for b in html_para_blocos(html) if b.texto.strip())


# ----------------------------------------------------------------------------------------------
# Formatação da página (margens) e blocos da sentença
# ----------------------------------------------------------------------------------------------


def normalizar_formatacao(bruta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Valida a formatação da folha.

    ``margens_cm`` (superior/esquerda/inferior/direita), ``numerar_paginas`` (padrão: sim) e, quando
    informados, ``fonte`` e ``tamanho_pt`` (fonte padrão do documento).
    """
    bruta = bruta if isinstance(bruta, dict) else {}
    margens = dict(MARGENS_ABNT)
    informadas = bruta.get("margens_cm")
    if isinstance(informadas, dict):
        for lado in margens:
            try:
                v = float(informadas.get(lado))
            except (TypeError, ValueError):
                continue
            if v == v:  # descarta NaN
                margens[lado] = round(min(max(v, _MARGEM_MIN), _MARGEM_MAX), 2)
    saida: Dict[str, Any] = {"margens_cm": margens, "numerar_paginas": bool(bruta.get("numerar_paginas", True))}
    fonte = _para_fonte(str(bruta.get("fonte") or ""))
    if fonte:
        saida["fonte"] = fonte
    try:
        tam = float(bruta.get("tamanho_pt"))
        if tam == tam:
            saida["tamanho_pt"] = round(min(max(tam, 8.0), 18.0), 1)
    except (TypeError, ValueError):
        pass
    return saida


def formatacao_da_sentenca(sentenca: Any) -> Dict[str, Any]:
    return normalizar_formatacao(getattr(sentenca, "formatacao", None))


def blocos_da_sentenca(sentenca: Any) -> List[Bloco]:
    """Blocos a exportar: o HTML editado na folha, se houver; senão o texto do motor no padrão forense."""
    html = str(getattr(sentenca, "texto_html", "") or "").strip()
    if html:
        blocos = html_para_blocos(html)
        if blocos:
            return blocos
    return texto_para_blocos(str(getattr(sentenca, "texto_completo", "") or ""))


def html_da_sentenca(sentenca: Any) -> str:
    """HTML limpo da sentença para a folha do editor (editado, ou padrão forense do texto)."""
    return blocos_para_html(blocos_da_sentenca(sentenca))


# ----------------------------------------------------------------------------------------------
# Blocos -> .docx
# ----------------------------------------------------------------------------------------------

def adicionar_blocos_docx(doc: Any, blocos: Iterable[Bloco], fonte: str, tamanho_pt: float) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt, RGBColor

    mapa = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    }
    for b in blocos:
        p = doc.add_paragraph()
        fmt = p.paragraph_format
        # Título (linha curta em destaque, sem recuo) nunca fica sozinho no fim da página.
        if len(b.trechos) == 1 and len(b.texto) <= _TITULO_MAX and b.recuo_primeira_cm == 0 and (b.trechos[0].negrito or b.trechos[0].italico):
            fmt.keep_with_next = True
        p.alignment = mapa.get(b.alinhamento, WD_ALIGN_PARAGRAPH.JUSTIFY)
        fmt.first_line_indent = Cm(b.recuo_primeira_cm)
        fmt.left_indent = Cm(b.recuo_esquerda_cm)
        fmt.line_spacing = b.entrelinhas
        fmt.space_before = Pt(b.antes_pt)
        fmt.space_after = Pt(b.depois_pt)
        for t in b.trechos:
            if t.quebra:
                p.add_run().add_break()
                continue
            run = p.add_run(t.texto)
            run.font.name = t.fonte or fonte
            run.font.size = Pt(t.tamanho_pt or tamanho_pt)
            if t.negrito:
                run.bold = True
            if t.italico:
                run.italic = True
            if t.sublinhado:
                run.underline = True
            if t.tachado:
                run.font.strike = True
            if t.sobrescrito:
                run.font.superscript = True
            if t.subscrito:
                run.font.subscript = True
            if t.cor:
                run.font.color.rgb = RGBColor.from_string(t.cor.lstrip("#").upper())
