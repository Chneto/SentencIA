# -*- coding: utf-8 -*-
"""Testes do contrato HTML para o PJe, formato de referências e página offline (TASK-020)."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Tuple

import pytest
from fastapi.testclient import TestClient

from app.api.security import TOKEN_HEADER, get_session_token
from app.core import config as cfg
from app.demo.demo_case import get_demo_case_bundle
from app.export.document_exporter import MARCA_DAGUA_NAO_AUDITADA, exportar_html_pje
from app.generation.sentence_generator import SentencaGerada, gerar_sentenca_estruturada
from app.main import app


PJE_TAGS_PERMITIDAS = frozenset(
    {"p", "br", "strong", "b", "em", "i", "u", "s", "sup", "sub", "ul", "ol", "li", "blockquote", "span"}
)
PJE_ATRIBUTOS_PERMITIDOS = frozenset({"style"})
PJE_CSS_BASE = frozenset(
    {
        "font-family",
        "font-size",
        "font-weight",
        "font-style",
        "text-decoration",
        "text-align",
        "text-indent",
        "margin-left",
        "margin-top",
        "margin-bottom",
        "line-height",
    }
)
PJE_CSS_MARCA_DAGUA = PJE_CSS_BASE | frozenset({"color"})
_URL_PROIBIDA_RE = re.compile(r"(?:url\s*\(|https?://|javascript:|data:)", re.IGNORECASE)


class _ValidadorContratoPje(HTMLParser):
    """Analisa o HTML gerado para o PJe e registra qualquer violação da lista branca."""

    def __init__(self, *, texto_marca: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.texto_marca = texto_marca.strip()
        self.violacoes: List[str] = []
        self._em_paragrafo = False
        self._style_paragrafo = ""
        self._texto_paragrafo: List[str] = []
        self._cores_no_paragrafo: List[str] = []

    def _checar_style(self, tag: str, style: str) -> None:
        if _URL_PROIBIDA_RE.search(style):
            self.violacoes.append(f"URL proibida em style de <{tag}>: {style!r}")
        for parte in style.split(";"):
            parte = parte.strip()
            if not parte:
                continue
            if ":" not in parte:
                self.violacoes.append(f"Declaração CSS malformada em <{tag}>: {parte!r}")
                continue
            prop, val = parte.split(":", 1)
            prop = prop.strip().lower()
            val = val.strip()
            if prop not in PJE_CSS_MARCA_DAGUA:
                self.violacoes.append(f"Propriedade CSS proibida em <{tag}>: {prop!r}")
            elif prop == "color":
                if self._em_paragrafo:
                    self._cores_no_paragrafo.append(f"<{tag}> color: {val}")
                else:
                    self.violacoes.append(f"Propriedade 'color' fora de parágrafo de marca d'água em <{tag}>")

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str | None]]) -> None:
        tag_l = tag.lower()
        if tag_l not in PJE_TAGS_PERMITIDAS:
            self.violacoes.append(f"Tag proibida: <{tag_l}>")
        if tag_l == "p":
            self._em_paragrafo = True
            self._texto_paragrafo = []
            self._cores_no_paragrafo = []
        for nome, valor in attrs:
            nome_l = nome.lower()
            if nome_l not in PJE_ATRIBUTOS_PERMITIDOS:
                self.violacoes.append(f"Atributo proibido em <{tag_l}>: {nome_l!r}")
            elif nome_l == "style" and valor:
                self._checar_style(tag_l, valor)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "p" and self._em_paragrafo:
            texto = "".join(self._texto_paragrafo).strip()
            eh_marca = bool(self.texto_marca) and self.texto_marca in texto
            if self._cores_no_paragrafo and not eh_marca:
                for item in self._cores_no_paragrafo:
                    self.violacoes.append(f"Propriedade 'color' permitida apenas na marca d'água: {item}")
            self._em_paragrafo = False

    def handle_data(self, data: str) -> None:
        if self._em_paragrafo:
            self._texto_paragrafo.append(data)

    def handle_comment(self, data: str) -> None:
        self.violacoes.append(f"Comentário HTML proibido: {data!r}")


def _validar_html_pje(html_str: str, *, texto_marca: str = "") -> List[str]:
    validador = _ValidadorContratoPje(texto_marca=texto_marca)
    validador.feed(html_str)
    validador.close()
    return validador.violacoes


def _headers_auth() -> dict:
    return {TOKEN_HEADER: get_session_token()}


def test_contrato_html_pje_demo_com_e_sem_marca_dagua():
    """HTML do caso de demonstração (com citação longa e marca d'água) respeita estritamente a lista branca."""
    demo = get_demo_case_bundle()
    sentenca = gerar_sentenca_estruturada(case_bundle=demo, use_ollama_if_available=False)

    html_limpo = exportar_html_pje(sentenca)
    assert "<p " in html_limpo
    assert "margin-left: 4cm" in html_limpo  # citação longa preservada
    assert "data-marca-dagua" not in html_limpo
    assert _validar_html_pje(html_limpo) == []

    html_marcado = exportar_html_pje(sentenca, marca_dagua=MARCA_DAGUA_NAO_AUDITADA)
    assert MARCA_DAGUA_NAO_AUDITADA in html_marcado
    assert "data-marca-dagua" not in html_marcado
    assert "border:" not in html_marcado
    assert "padding:" not in html_marcado
    assert _validar_html_pje(html_marcado, texto_marca=MARCA_DAGUA_NAO_AUDITADA) == []


def _sentenca_sintetica(texto_completo: str, texto_html: str = "") -> SentencaGerada:
    return SentencaGerada(
        numero_processo="0800001-00.2026.8.20.0001",
        comarca_juizado="1º JUIZADO MODELO",
        ofendida="MARIA FICTÍCIA",
        reu="JOÃO FICTÍCIO",
        data_prolatacao="21 de novembro de 2026",
        secao_cabecalho="PODER JUDICIÁRIO",
        secao_relatorio="Relatório fictício.",
        secao_preliminares="Sem preliminares.",
        secao_merito="Mérito fictício.",
        secao_dosimetria="Dosimetria fictícia.",
        secao_dispositivo="Dispositivo fictício.",
        secao_assinatura="Juiz(a) de Direito",
        dosimetria_dados={},
        prescricao_dados={},
        texto_completo=texto_completo,
        texto_html=texto_html,
        motor_utilizado="teste",
    )


def test_contrato_html_pje_saneia_html_rico_sujo_e_remove_cor_do_corpo():
    """HTML editado com tags/atributos/estilos proibidos sai limpo e sem color no corpo."""
    html_sujo = (
        '<div class="externo" id="raiz" data-x="1">'
        '<!-- comentario secreto -->'
        '<script>alert("xss")</script>'
        '<style>.x { color: red; }</style>'
        '<p class="cls" data-foo="bar" style="text-align: justify; color: #ff0000; background: url(https://evil.test/x.png); border: 1px solid black;">'
        'Texto com <span style="color: #0000ff; font-weight: bold;">negrito azul</span>, '
        '<em>itálico</em>, <u>sublinhado</u>, <s>tachado</s>, <sup>1</sup> e <sub>2</sub>.'
        '</p>'
        '<blockquote style="margin-left: 4cm; font-size: 10pt; line-height: 1.0;">'
        'Citação longa de teste conforme ID-4100201.'
        '</blockquote>'
        '</div>'
    )
    sentenca = _sentenca_sintetica("Texto base com ID-4100201.", texto_html=html_sujo)

    html_pje = exportar_html_pje(sentenca, marca_dagua=MARCA_DAGUA_NAO_AUDITADA)
    assert "alert(" not in html_pje
    assert "comentario secreto" not in html_pje
    assert "evil.test" not in html_pje
    assert "data-foo" not in html_pje
    assert "class=" not in html_pje
    assert "id=" not in html_pje
    assert "<div" not in html_pje
    assert _validar_html_pje(html_pje, texto_marca=MARCA_DAGUA_NAO_AUDITADA) == []


def test_formato_referencia_pje_padrao_e_id_ponto(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Opção formato_referencia_pje: padrão mantém ID-4100201; id_ponto converte só no HTML PJe."""
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "user_settings.json")
    s_padrao = cfg.load_settings()
    assert s_padrao.formato_referencia_pje == "id_hifen"

    texto_original = (
        "PODER JUDICIÁRIO\n\n"
        "SENTENÇA\n\n"
        "A denúncia de ID-4100201 e o laudo de ID-4100108 (Mídia ID-4100301) comprovam os fatos."
    )
    sentenca = _sentenca_sintetica(texto_original)

    html_hifen = exportar_html_pje(sentenca)
    assert "ID-4100201" in html_hifen
    assert "Id. 4100201" not in html_hifen

    cfg.save_settings(s_padrao.model_copy(update={"formato_referencia_pje": "id_ponto"}))
    html_ponto = exportar_html_pje(sentenca)
    assert "Id. 4100201" in html_ponto
    assert "Id. 4100108" in html_ponto
    assert "Id. 4100301" in html_ponto
    assert "ID-4100201" not in html_ponto
    # Nunca altera o texto auditado da sentença
    assert sentenca.texto_completo == texto_original
    assert "ID-4100201" in sentenca.texto_completo
    assert _validar_html_pje(html_ponto) == []


def test_api_settings_aceita_e_valida_formato_referencia_pje(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "user_settings.json")
    client = TestClient(app)

    r_ok = client.post("/api/settings", json={"formato_referencia_pje": "id_ponto"}, headers=_headers_auth())
    assert r_ok.status_code == 200
    assert r_ok.json()["settings"]["formato_referencia_pje"] == "id_ponto"

    r_back = client.post("/api/settings", json={"formato_referencia_pje": "id_hifen"}, headers=_headers_auth())
    assert r_back.status_code == 200
    assert r_back.json()["settings"]["formato_referencia_pje"] == "id_hifen"

    r_bad = client.post("/api/settings", json={"formato_referencia_pje": "invalido"}, headers=_headers_auth())
    assert r_bad.status_code == 422


def test_pagina_verificacao_offline_pje_existe_e_respeita_contrato():
    """docs/pje/teste-colagem.html abre offline, usa apenas dados fictícios e traz checklist 1 a 6."""
    caminho = Path(__file__).resolve().parents[1] / "docs" / "pje" / "teste-colagem.html"
    assert caminho.is_file(), "docs/pje/teste-colagem.html deve existir"
    conteudo = caminho.read_text(encoding="utf-8")

    # Sem dependências externas (offline)
    assert not re.search(r'(?:src|href)\s*=\s*["\']https?://', conteudo, re.IGNORECASE)
    # Instruções dos itens 1 a 6
    assert "PJe" in conteudo
    for n in range(1, 7):
        assert f"{n}." in conteudo or f"({n})" in conteudo
    # Bloco da sentença fictícia embutida passa na lista branca do PJe
    m = re.search(r'<template\s+id="sentencaPjeHtml">([\s\S]*?)</template>', conteudo)
    assert m, "Página deve conter <template id=\"sentencaPjeHtml\"> com o HTML da sentença fictícia"
    assert _validar_html_pje(m.group(1)) == []
