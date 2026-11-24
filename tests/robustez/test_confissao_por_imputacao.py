"""Detecção e vinculação de falas exclusivamente fictícias."""

import datetime as dt

import pytest

from app.ingestion.confissao import AlvoConfissao, detectar_confissoes
from app.legal_engine.dosimetria_calculator import CrimeDosimetriaInput, calcular_dosimetria_crime


def _alvos():
    return [
        AlvoConfissao("lesao-1", "art_129_p13_cp", "1"),
        AlvoConfissao("ameaca-2", "art_147_cp", "2"),
        AlvoConfissao("ameaca-3", "art_147_cp", "3"),
    ]


def _transcricao(texto, papel="INTERROGATORIO_REU", papel_segmento=""):
    return {"media_id": "MIDIA-FICTICIA", "papel": papel, "segmentos": [
        {"texto_literal": texto, "inicio": "01:20", "papel": papel_segmento},
    ]}


@pytest.mark.parametrize(("fala", "tipo"), [
    ("Fato 1: O réu confessou que empurrou a vítima.", "total"),
    ("Fato 1: O réu admitiu o empurrão, mas negou o soco.", "parcial"),
    ("Fato 1: O réu admitiu que agrediu para se defender.", "qualificada"),
    ("Fato 1: O réu não confessou a agressão.", "negativa"),
    ("Fato 1: O réu não empurrou a vítima.", "negativa"),
    ("Fato 1: Agressão atribuída a outra pessoa.", "ausente"),
    ("A testemunha disse que o réu confessou a agressão do fato 1.", "ausente"),
    ("Fato 1: O réu disse que o vizinho confessou a agressão.", "ausente"),
])
def test_classifica_fala_atribuida_sem_transformar_mencao_em_confissao(fala, tipo):
    sugestoes, _ = detectar_confissoes([], [_transcricao(fala)], _alvos())
    assert sugestoes["lesao-1"].tipo == tipo
    if tipo != "ausente":
        assert sugestoes["lesao-1"].minuto == "01:20"
        assert sugestoes["lesao-1"].fonte_id == "MIDIA-FICTICIA"


def test_fala_de_terceiro_com_rotulo_de_segmento_prevalece():
    fala = _transcricao("Ameacei no fato 2.", papel_segmento="TESTEMUNHA")
    sugestoes, _ = detectar_confissoes([], [fala], _alvos())
    assert sugestoes["ameaca-2"].tipo == "ausente"


def test_tipos_repetidos_exigem_referencia_e_contradicao_fica_inconclusiva():
    fala = _transcricao("O réu confessou a ameaça.")
    sugestoes, _ = detectar_confissoes([], [fala], _alvos())
    assert sugestoes["ameaca-2"].tipo == sugestoes["ameaca-3"].tipo == "ausente"
    falas = [
        _transcricao("Fato 2: O réu confessou que ameaçou a vítima."),
        _transcricao("Fato 2: O réu negou a ameaça."),
    ]
    sugestoes, _ = detectar_confissoes([], falas, _alvos())
    assert sugestoes["ameaca-2"].tipo == "inconclusiva"
    assert sugestoes["ameaca-3"].tipo == "ausente"


def test_negativa_na_primeira_pessoa_com_marcacao_do_fato():
    fala = _transcricao("Fato 2: Eu nunca ameacei ninguém.")
    sugestoes, _ = detectar_confissoes([], [fala], _alvos())
    assert sugestoes["ameaca-2"].tipo == "negativa"


@pytest.mark.parametrize("dia, pendente", [(15, False), (16, False), (17, True)])
def test_tema_1194_nao_antecipa_efeito_prejudicial(dia, pendente):
    """DJEN 16/09/2025. Efeitos prejudiciais aos réus somente para fatos posteriores.
    https://processo.stj.jus.br/repetitivos/temas_repetitivos/pesquisa.jsp?cod_tema_final=1194&cod_tema_inicial=1194&novaConsulta=true&tipo_pesquisa=T
    """
    entrada = CrimeDosimetriaInput(
        crime_code="art_147_cp", data_fato=dt.date(2025, 9, dia),
        confissao_espontanea=True, tipo_confissao="qualificada", reincidente=True,
    )
    saida = calcular_dosimetria_crime(entrada)
    assert saida.revisao_proporcao_confissao_pendente is pendente
    assert saida.compensacao_confissao_reincidencia is not pendente
    assert any("confissão" in item for item in saida.atenuantes_ativas)
