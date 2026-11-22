"""Nome do arquivo da sentença e texto rico (padrão forense + edição preservada na exportação)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import docx

from app.export.document_exporter import exportar_docx, exportar_html_pje
from app.export.nomeacao import (
    eh_arquivo_exportacao,
    nome_arquivo_sentenca,
    tipo_sentenca,
    tipos_penais,
)
from app.export.rich_text import (
    html_da_sentenca,
    html_para_blocos,
    nivel_titulo,
    normalizar_formatacao,
    sanitizar_html,
    texto_do_html,
    texto_para_blocos,
)


def _sent(veredictos=(), reu="CARLOS EDUARDO ROCHA MENDES", numero="0804590-12.2025.8.00.0001", texto="", crimes=None, html="", formatacao=None):
    codigos = crimes or ["art_129_p13_cp", "art_147_cp", "art_24a_lmp"]
    decisoes = [
        {"crime_code": c, "crime_efetivo": c, "veredicto": v, "fundamento_legal": ""}
        for c, v in zip(codigos, veredictos)
    ]
    return SimpleNamespace(
        numero_processo=numero,
        reu=reu,
        decisoes_crimes=decisoes,
        texto_completo=texto,
        texto_html=html,
        formatacao=formatacao or {},
    )


# ------------------------------------------------------------------------------
# Nome do arquivo
# ------------------------------------------------------------------------------
class TestNomeDoArquivo:
    def test_padrao_condenatoria(self):
        s = _sent(["condenar", "condenar"], crimes=["art_129_p13_cp", "art_147_cp"])
        nome = nome_arquivo_sentenca(s, "docx")
        assert nome.startswith("Sentença - Condenatória - Proc. 0804590-12.2025.8.00.0001 - CARLOS EDUARDO ROCHA MENDES - ")
        assert "art. 129, § 13, CP" in nome and "art. 147" in nome
        assert nome.endswith(".docx")

    def test_tipos_de_sentenca(self):
        assert tipo_sentenca(_sent(["absolver", "absolver"])) == "Absolutória"
        assert tipo_sentenca(_sent(["condenar", "absolver"])) == "Parcialmente condenatória"
        assert tipo_sentenca(_sent(["extinguir"])) == "Extintiva da punibilidade"
        assert tipo_sentenca(_sent(["absolver", "extinguir"])) == "Absolutória e extintiva da punibilidade"
        assert tipo_sentenca(_sent(["desclassificar"])) == "Condenatória (desclassificação)"

    def test_tipo_deduzido_do_texto_quando_nao_ha_decisoes(self):
        s = _sent(texto="Ante o exposto, JULGO IMPROCEDENTE a pretensão e ABSOLVER o acusado.")
        assert tipo_sentenca(s) == "Absolutória"

    def test_sem_decisoes_usa_a_dosimetria_calculada(self):
        s = _sent(texto="Sentença importada.")
        s.dosimetria_dados = {
            "crimes_calculados": [{"crime_spec": {"artigo_formatado": "art. 147, caput, do Código Penal"}}],
            "crimes_nao_condenados": [],
            "sem_condenacao": False,
        }
        assert tipo_sentenca(s) == "Condenatória" and tipos_penais(s) == ["art. 147, caput, CP"]
        s.dosimetria_dados["crimes_nao_condenados"] = [{"crime": "x"}]
        assert tipo_sentenca(s) == "Parcialmente condenatória"
    def test_caracteres_ilegais_no_windows_sao_removidos(self):
        s = _sent(["condenar"], reu='FULANO: "DE" <TAL> / CIA?*|', numero="08/04:590")
        nome = nome_arquivo_sentenca(s, "pdf")
        for ch in '<>:"/\\|?*':
            assert ch not in nome
        assert not nome.rstrip(".pdf").endswith((" ", "."))

    def test_rascunho_e_tamanho_limitado(self):
        s = _sent(["condenar"], reu="NOME " * 60)
        nome = nome_arquivo_sentenca(s, "docx", rascunho=True)
        assert nome.endswith("(RASCUNHO).docx")
        assert len(nome) < 240

    def test_tipos_penais_sem_repeticao(self):
        s = _sent(["condenar", "condenar"], crimes=["art_147_cp", "art_147_cp"])
        assert len(tipos_penais(s)) == 1

    def test_reconhece_exportacoes_novas_e_legadas(self):
        assert eh_arquivo_exportacao("Sentença - Condenatória - Proc. 1 - X - art. 1.docx")
        assert eh_arquivo_exportacao("Sentenca_JVDFM_123.docx")
        assert not eh_arquivo_exportacao("Sentença - modelo do colega.txt")
        assert not eh_arquivo_exportacao("autos_0804590.pdf")


# ------------------------------------------------------------------------------
# Padrão forense: o parágrafo numerado longo NÃO é título
# ------------------------------------------------------------------------------
class TestPadraoForense:
    def test_titulo_so_quando_curto_e_sem_ponto_final(self):
        assert nivel_titulo("I – RELATÓRIO") == 1
        assert nivel_titulo("1. Das Questões Preliminares") == 1
        assert nivel_titulo("1.1. Da Ausência de Prescrição:") == 2
        longo = "1. " + "O réu praticou a conduta descrita na denúncia e confirmou os fatos em juízo " * 4 + "."
        assert nivel_titulo(longo) == 0
        assert nivel_titulo("1. Suspendam-se os direitos políticos do sentenciado.") == 0

    def test_titulo_com_corpo_no_mesmo_bloco_vira_dois_paragrafos(self):
        texto = "SENTENÇA\n\nII – FUNDAMENTAÇÃO\n\n1.1. Da Preliminar:\nNão merece acolhimento a tese defensiva, pois a denúncia é apta."
        # idx 0 é o cabeçalho; reposiciona para que a seção caia no corpo
        blocos = texto_para_blocos("TRIBUNAL\n\n" + texto)
        titulo = next(b for b in blocos if b.texto.startswith("1.1."))
        corpo = next(b for b in blocos if b.texto.startswith("Não merece"))
        assert titulo.alinhamento == "justify" and titulo.recuo_primeira_cm == 0
        assert titulo.trechos[0].italico and not titulo.trechos[0].negrito
        assert corpo.alinhamento == "justify" and corpo.recuo_primeira_cm == 1.25
        assert not corpo.trechos[0].negrito

    def test_secao_principal_em_negrito_sem_cor(self):
        b = next(b for b in texto_para_blocos("X\n\nII – FUNDAMENTAÇÃO") if b.texto.startswith("II"))
        assert b.trechos[0].negrito and b.trechos[0].cor is None
        assert 'color' not in html_da_sentenca(_sent(texto="X\n\nII – FUNDAMENTAÇÃO"))

    def test_citacao_ordens_e_assinatura(self):
        texto = (
            "CAB\n\nSENTENÇA\n\n    \"Citação longa de precedente.\"\n\n"
            "1. Lance-se o nome do réu no rol dos culpados.\n\n"
            "Publique-se. Registre-se. Intimem-se.\n\nCOMARCA, 20 de novembro de 2026.\n\nJuiz(a) de Direito Titular"
        )
        por_texto = {b.texto: b for b in texto_para_blocos(texto)}
        cit = por_texto["Citação longa de precedente."]  # sem aspas externas (NBR 10520)
        assert cit.recuo_esquerda_cm == 4.0 and cit.entrelinhas == 1.0 and cit.trechos[0].tamanho_pt == 10.0
        assert por_texto["1. Lance-se o nome do réu no rol dos culpados."].recuo_esquerda_cm == 1.25
        assert por_texto["Publique-se. Registre-se. Intimem-se."].alinhamento == "center"
        assert por_texto["COMARCA, 20 de novembro de 2026."].alinhamento == "right"
        assert por_texto["Juiz(a) de Direito Titular"].alinhamento == "center"


# ------------------------------------------------------------------------------
# HTML da folha: sanitização e idempotência
# ------------------------------------------------------------------------------
class TestHtmlDaFolha:
    def test_remove_script_e_atributos_perigosos(self):
        sujo = '<p onclick="x()" style="text-align:center;background:url(javascript:1)">Oi <script>alert(1)</script><b>forte</b></p>'
        limpo = sanitizar_html(sujo)
        assert "script" not in limpo and "onclick" not in limpo and "javascript" not in limpo and "alert" not in limpo
        assert "text-align: center" in limpo and "font-weight: bold" in limpo

    def test_idempotente_e_preserva_texto(self):
        s = _sent(texto="TRIBUNAL X\n\nSENTENÇA\n\nI – RELATÓRIO\n\nVistos etc.\n\nO réu foi denunciado.")
        h = html_da_sentenca(s)
        assert sanitizar_html(h) == h
        assert texto_do_html(h).replace("\n", " ").split() == [w for w in s.texto_completo.replace("\n", " ").split()]

    def test_formatacao_do_editor_e_lida(self):
        html = (
            '<div style="text-align: right;"><span style="font-weight: bold; font-size: 14pt; color: rgb(255, 0, 0)">Oi</span>'
            "<i> tudo</i><u> bem</u></div>"
        )
        (b,) = html_para_blocos(html)
        assert b.alinhamento == "right"
        assert b.trechos[0].negrito and b.trechos[0].tamanho_pt == 14.0 and b.trechos[0].cor == "#ff0000"
        assert b.trechos[1].italico and b.trechos[2].sublinhado

    def test_lista_vira_paragrafos_com_marcador(self):
        blocos = html_para_blocos("<ol><li>Primeiro</li><li>Segundo</li></ol><ul><li>Item</li></ul>")
        assert [b.texto for b in blocos] == ["1. Primeiro", "2. Segundo", "• Item"]

    def test_margens_validadas(self):
        assert normalizar_formatacao(None)["margens_cm"] == {"superior": 3.0, "esquerda": 3.0, "inferior": 2.0, "direita": 2.0}
        m = normalizar_formatacao({"margens_cm": {"superior": 99, "esquerda": "abc", "inferior": -3, "direita": 2.5}})["margens_cm"]
        assert m == {"superior": 6.0, "esquerda": 3.0, "inferior": 0.5, "direita": 2.5}


# ------------------------------------------------------------------------------
# Modelos de formatação do gabinete
# ------------------------------------------------------------------------------
class TestModelosDeFormatacao:
    def test_fabrica_usuario_substituicao_e_exclusao(self, tmp_path: Path, monkeypatch):
        import app.core.config as core_config
        from app.export import formatos

        monkeypatch.setattr(core_config, "TEMPLATES_DIR", tmp_path)
        base = formatos.listar_modelos()
        assert len(base) == len(formatos.MODELOS_PADRAO) and all(m["padrao"] for m in base)

        salvo = formatos.salvar_modelo(
            {"nome": "  Minha   sentença ", "fonte": "Arial", "tamanho_pt": 99, "entrelinhas": 1.15,
             "margens_cm": {"superior": 2, "esquerda": 2, "inferior": 2, "direita": 99}, "numerar_paginas": False}
        )
        assert salvo["nome"] == "Minha sentença" and salvo["tamanho_pt"] == 18.0 and salvo["margens_cm"]["direita"] == 6.0
        assert salvo["numerar_paginas"] is False
        formatos.salvar_modelo({"nome": "minha sentença", "fonte": "Calibri"})  # mesmo nome: substitui
        usuario = [m for m in formatos.listar_modelos() if not m["padrao"]]
        assert len(usuario) == 1 and usuario[0]["fonte"] == "Calibri"

        assert formatos.excluir_modelo("MINHA SENTENÇA") is True
        assert formatos.excluir_modelo("MINHA SENTENÇA") is False

    def test_nome_de_modelo_de_fabrica_e_nome_vazio_sao_recusados(self, tmp_path: Path, monkeypatch):
        import app.core.config as core_config
        import pytest

        from app.export import formatos

        monkeypatch.setattr(core_config, "TEMPLATES_DIR", tmp_path)
        with pytest.raises(ValueError):
            formatos.salvar_modelo({"nome": formatos.MODELOS_PADRAO[0]["nome"]})
        with pytest.raises(ValueError):
            formatos.salvar_modelo({"nome": "   "})

    def test_arquivo_corrompido_nao_derruba_a_lista(self, tmp_path: Path, monkeypatch):
        import app.core.config as core_config
        from app.export import formatos

        monkeypatch.setattr(core_config, "TEMPLATES_DIR", tmp_path)
        (tmp_path / "formatos_gabinete.json").write_text("{não é json", encoding="utf-8")
        assert len(formatos.listar_modelos()) == len(formatos.MODELOS_PADRAO)


# ------------------------------------------------------------------------------
# Exportação preserva a edição da folha
# ------------------------------------------------------------------------------
class TestExportacaoPreservaEdicao:
    def test_docx_com_formatacao_e_margens_da_folha(self, tmp_path: Path):
        html = (
            '<p style="text-align: center">Título</p>'
            '<p style="text-align: justify; text-indent: 2cm"><span style="font-weight: bold">Negrito</span> '
            '<span style="font-style: italic">itálico</span> <span style="text-decoration: underline">sub</span></p>'
        )
        s = _sent(texto="ignorado", html=html, formatacao={"margens_cm": {"superior": 2, "esquerda": 2.5, "inferior": 1.5, "direita": 1.5}})
        arq = exportar_docx(s, tmp_path / "x.docx")
        d = docx.Document(str(arq))
        sec = d.sections[0]
        assert round(sec.left_margin.cm, 2) == 2.5 and round(sec.top_margin.cm, 2) == 2.0
        corpo = [p for p in d.paragraphs if p.text.startswith("Negrito")][0]
        assert round(corpo.paragraph_format.first_line_indent.cm, 2) == 2.0
        assert corpo.runs[0].bold and any(r.italic for r in corpo.runs) and any(r.underline for r in corpo.runs)
        assert "ignorado" not in "\n".join(p.text for p in d.paragraphs)

    def test_docx_numera_paginas_com_campos_e_usa_fonte_do_modelo(self, tmp_path: Path):
        s = _sent(texto="CAB\n\nSENTENÇA\n\nI – RELATÓRIO\n\nVistos etc.", formatacao={"fonte": "Arial", "tamanho_pt": 11})
        d = docx.Document(str(exportar_docx(s, tmp_path / "n.docx")))
        xml = d.sections[0].footer._element.xml
        assert "PAGE" in xml and "NUMPAGES" in xml and "Página" in xml
        assert d.styles["Normal"].font.name == "Arial" and d.styles["Normal"].font.size.pt == 11
        sem = docx.Document(str(exportar_docx(_sent(texto="X", formatacao={"numerar_paginas": False}), tmp_path / "s.docx")))
        assert "NUMPAGES" not in sem.sections[0].footer._element.xml

    def test_titulo_nao_fica_orfao_no_fim_da_pagina(self, tmp_path: Path):
        s = _sent(texto="CAB\n\nII – FUNDAMENTAÇÃO\n\nCorpo do texto da fundamentação.")
        d = docx.Document(str(exportar_docx(s, tmp_path / "k.docx")))
        titulo = next(p for p in d.paragraphs if p.text.startswith("II –"))
        corpo = next(p for p in d.paragraphs if p.text.startswith("Corpo"))
        assert titulo.paragraph_format.keep_with_next is True
        assert not corpo.paragraph_format.keep_with_next

    def test_pdf_numera_paginas(self, tmp_path: Path):
        import pymupdf

        from app.export.document_exporter import exportar_pdf

        s = _sent(texto="CAB\n\n" + "\n\n".join(f"Parágrafo {i} " + "texto " * 80 for i in range(40)))
        pdf = pymupdf.open(str(exportar_pdf(s, tmp_path / "p.pdf")))
        total = pdf.page_count
        assert total > 1
        assert f"Página 1 de {total}" in pdf[0].get_text()
        assert f"Página {total} de {total}" in pdf[total - 1].get_text()

    def test_html_pje_usa_a_folha_editada(self):
        s = _sent(texto="velho", html='<p style="text-align: right">novo texto</p>')
        h = exportar_html_pje(s)
        assert "novo texto" in h and "velho" not in h and "text-align: right" in h
