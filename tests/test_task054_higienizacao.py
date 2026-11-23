"""Regressões da TASK-054: compatibilidade Python 3.11, escape \\u200B e citação do art. 147 na demo."""

from pathlib import Path

from app.demo.demo_case import get_demo_case_bundle
from app.export.rich_text import html_para_blocos
from app.generation.sentence_generator import gerar_sentenca_estruturada


ROOT = Path(__file__).resolve().parent.parent


def test_sentence_generator_sem_aspas_duplas_aninhadas_em_fstring():
    """Garante compatibilidade com Python 3.11 nas f-strings de sentence_generator.py."""
    src = (ROOT / "app" / "generation" / "sentence_generator.py").read_text(encoding="utf-8")
    assert 'obter_item_oficial_por_id("SUMULA-589-STJ")' not in src


def test_rich_text_usa_escape_u200b_em_vez_de_caractere_invisivel():
    """Garante que rich_text.py usa o escape literal \\u200B e continua removendo zero-width space."""
    src = (ROOT / "app" / "export" / "rich_text.py").read_text(encoding="utf-8")
    assert "\u200b" not in src
    assert r'data.replace("\u200B", "")' in src

    blocos = html_para_blocos("<p>Texto\u200bLimpo</p>")
    assert len(blocos) == 1
    assert "".join(t.texto for t in blocos[0].trechos) == "TextoLimpo"


def test_demo_sentence_cita_art147_p1_e_p2_parte_final():
    """Verifica a citação correta da ação penal incondicionada no art. 147 do CP na sentença demo.

    Fonte oficial: Lei nº 14.994/2024 (publicada e vigente em 10/10/2024;
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2024/lei/L14994.htm), que incluiu
    os §§ 1º e 2º ao art. 147 do Código Penal: o § 2º fixa a regra geral de representação,
    excetuando expressamente a hipótese do § 1º (ameaça contra a mulher por razões da
    condição do sexo feminino).
    """
    demo = get_demo_case_bundle()
    sentenca = gerar_sentenca_estruturada(case_bundle=demo, use_ollama_if_available=False)
    texto = sentenca.texto_completo

    assert "(art. 147, § 2º, do CP, acrescentado pela Lei nº 14.994/2024)" not in texto
    assert "art. 147, § 1º e § 2º, parte final, do CP, acrescentados pela Lei nº 14.994/2024" in texto
