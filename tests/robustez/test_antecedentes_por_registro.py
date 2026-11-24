"""Anotações fictícias e decisões por imputação na primeira e segunda fases."""

import datetime as dt

import pytest

from app.core.errors import DadosInsuficientesError
from app.generation.sentence_generator import resolver_dosimetria_e_decisoes
from app.ingestion.laya_classifier import classificar_pecas_autos, extrair_metadados_processo
from app.legal_engine.antecedentes import RegistroAntecedente, fases_admissiveis, validar_usos_antecedentes
from app.legal_engine.dosimetria_calculator import CrimeDosimetriaInput, calcular_dosimetria_crime
from tests.test_pontos_decisivos import _doc

SENTENCA = dt.date(2026, 6, 1)
FATO = dt.date(2024, 3, 10)
CERTIDAO = (
    "Nada consta em uma comarca.\n"
    "Processo nº 0001234-56.2024.8.09.0001; condenação definitiva.\n"
    "Data do fato anterior: 10/03/2015. Trânsito em julgado: 11/12/2018. Pena extinta em 15/02/2019.\n"
    "Registro 2: ação penal em curso, sem trânsito em julgado.\n"
    "Registro 3: condenação definitiva. Data do fato: 10/03/2020. "
    "Trânsito em julgado: 05/01/2022. Pena em cumprimento."
)


def test_certidao_mista_conserva_fontes_e_datas_rotuladas():
    certidao = _doc("ID-CAC-A", "Certidão de Antecedentes Criminais", CERTIDAO, folha="fls. 2")
    espelho = _doc("ID-CAC-B", "Certidão de Antecedentes Criminais", CERTIDAO.split("Registro 2:")[0], folha="fls. 3")
    pecas = classificar_pecas_autos([certidao, espelho])
    meta = extrair_metadados_processo(pecas)
    assert meta.reu_primario_sumula444 is None
    a, b, c = meta.registros_antecedentes
    assert a.situacao == c.situacao == "definitiva"
    assert b.situacao == "em_curso" and b.data_transito is None
    assert a.data_fato_anterior == dt.date(2015, 3, 10)
    assert a.data_transito == dt.date(2018, 12, 11)
    assert a.data_cumprimento_extincao == dt.date(2019, 2, 15)
    assert [(f.fonte_id, f.folha) for f in a.fontes] == [("ID-CAC-A", "fls. 2"), ("ID-CAC-B", "fls. 3")]


@pytest.mark.parametrize("data, reincide", [(dt.date(2024, 2, 14), True), (dt.date(2024, 2, 15), True), (dt.date(2024, 2, 16), False)])
def test_periodo_depurador_comeca_no_cumprimento_ou_extincao(data, reincide):
    """CP art. 64, I (Lei 7.209/1984, vigência 13/01/1985).
    https://www.planalto.gov.br/ccivil_03/leis/1980-1988/l7209.htm
    """
    anterior = RegistroAntecedente(
        id_registro="ficticio", situacao="definitiva", data_transito=dt.date(2010, 1, 1),
        data_cumprimento_extincao=dt.date(2019, 2, 15),
    )
    fases = fases_admissiveis(anterior, data, SENTENCA)
    assert ("reincidencia" in fases) is reincide
    assert "antecedentes" in fases
    incompleto = anterior.model_copy(update={"data_cumprimento_extincao": None})
    assert "reincidencia" not in fases_admissiveis(incompleto, data, SENTENCA)


@pytest.mark.parametrize("dia, vigente", [(12, False), (13, True), (14, True)])
def test_vigencia_da_redacao_do_codigo_penal(dia, vigente):
    """Arts. 59, 63 e 64 na redação da Lei 7.209/1984. Vigência em 13/01/1985.
    https://www.planalto.gov.br/ccivil_03/leis/1980-1988/l7209.htm
    """
    registro = RegistroAntecedente(
        id_registro="ficticio", situacao="definitiva", data_transito=dt.date(1984, 1, 1),
        data_cumprimento_extincao=dt.date(1984, 5, 1),
    )
    assert ("reincidencia" in fases_admissiveis(registro, dt.date(1985, 1, dia), SENTENCA)) is vigente


def test_impede_valorar_duas_vezes_o_mesmo_registro_e_valorar_inquerito():
    """Súmulas 241 (DJ 15/09/2000) e 444 (DJe 13/05/2010), ambas do STJ.
    https://www.stj.jus.br/publicacaoinstitucional/index.php/sumstj/article/viewFile/5715/5835
    https://scon.stj.jus.br/SCON/sumstj/doc.jsp?b=SUMU&i=1&l=10&livre=%22444%22+INPATH%28NUM%29
    """
    condenacao = RegistroAntecedente(
        id_registro="condenacao-ficticia", situacao="definitiva",
        data_transito=dt.date(2020, 1, 1), pena_em_curso=True,
    )
    inquerito = RegistroAntecedente(id_registro="inquerito-ficticio", situacao="em_curso")
    caminho = "crimes_config.imputacao-ficticia.usos_antecedentes"
    dados = [
        {"id_registro": condenacao.id_registro, "fase": "antecedentes"},
        {"id_registro": condenacao.id_registro, "fase": "reincidencia"},
    ]
    with pytest.raises(DadosInsuficientesError, match="somente uma destinação"):
        validar_usos_antecedentes(dados, [condenacao], FATO, SENTENCA, caminho)
    for fase in ("antecedentes", "reincidencia"):
        with pytest.raises(DadosInsuficientesError, match="não permitem"):
            validar_usos_antecedentes([{"id_registro": inquerito.id_registro, "fase": fase}], [inquerito], FATO, SENTENCA, caminho)


def test_escolha_por_imputacao_altera_exclusivamente_a_fase_escolhida():
    """CP, arts. 59 e 61, I (Lei 7.209/1984, vigência 13/01/1985) e Súmula 241/STJ (15/09/2000).
    https://www.planalto.gov.br/ccivil_03/leis/1980-1988/l7209.htm
    https://www.stj.jus.br/publicacaoinstitucional/index.php/sumstj/article/viewFile/5715/5835
    """
    pecas = classificar_pecas_autos([
        _doc("ID-DEN", "Denúncia Ministerial", "Fato ocorrido em 10/03/2024. Imputação do art. 147 do CP."),
        _doc("ID-CAC", "Certidão de Antecedentes Criminais", CERTIDAO, folha="fls. 2"),
    ])
    meta = extrair_metadados_processo(pecas)
    primeiro, _, terceiro = meta.registros_antecedentes

    def calcular(escolhas):
        item = {"codigo": "art_147_cp", "veredicto": "condenar", "usos_antecedentes": escolhas}
        dosimetria, *_ = resolver_dosimetria_e_decisoes(
            pecas, meta, [], False, FATO, None, {"crimes_config": [item]}, data_sentenca=SENTENCA,
        )
        return dosimetria.crimes_calculados[0]

    pendente = calcular([])
    assert pendente.antecedentes_pendentes and not pendente.reincidente
    assert "antecedentes" not in pendente.vetores_negativos_efetivos

    selecionado = calcular([{"id_registro": primeiro.id_registro, "fase": "antecedentes"}])
    assert selecionado.pena_base.to_total_days() > pendente.pena_base.to_total_days()
    assert selecionado.registros_valorados == {"antecedentes": [primeiro.id_registro]}
    assert "ID-CAC" in selecionado.vetores_detalhados["antecedentes"].fundamentacao
    assert not selecionado.reincidente

    reincidente = calcular([{"id_registro": terceiro.id_registro, "fase": "reincidencia"}])
    assert reincidente.reincidente and "ID-CAC" in reincidente.fontes_reincidencia[0]
    assert reincidente.pena_base == pendente.pena_base



def test_certidao_nao_fornece_data_ou_numero_do_processo_principal():
    pecas = classificar_pecas_autos([_doc("ID-CAC", "Certidão de Antecedentes Criminais", CERTIDAO)])
    meta = extrair_metadados_processo(pecas)
    assert not meta.data_fato and not meta.numero_processo
    assert len(meta.registros_antecedentes) == 3


def test_periodo_de_prova_e_exclusoes_do_artigo_64():
    """CP art. 64, I e II (Lei 7.209/1984, vigência 13/01/1985).
    https://www.planalto.gov.br/ccivil_03/leis/1980-1988/l7209.htm
    """
    registro = RegistroAntecedente(
        id_registro="ficticio", situacao="definitiva", data_transito=dt.date(2015, 1, 1),
        data_cumprimento_extincao=dt.date(2021, 3, 10),
    )
    assert "reincidencia" in fases_admissiveis(registro, FATO, SENTENCA)
    prova = registro.model_copy(update={
        "inicio_periodo_prova": dt.date(2018, 3, 10), "prova_sem_revogacao": True,
    })
    assert fases_admissiveis(prova, FATO, SENTENCA) == {"antecedentes", "desconsiderar"}
    exclusao = registro.model_copy(update={"exclui_reincidencia": True})
    assert "reincidencia" not in fases_admissiveis(exclusao, FATO, SENTENCA)


def test_condenacoes_distintas_podem_ser_valoradas_em_fases_distintas():
    """Súmula 241/STJ (DJ 15/09/2000), com dois registros independentes.
    https://www.stj.jus.br/publicacaoinstitucional/index.php/sumstj/article/viewFile/5715/5835
    """
    a = RegistroAntecedente(
        id_registro="a", processo="autos fictícios A", situacao="definitiva",
        data_transito=dt.date(2019, 1, 1), pena_em_curso=True,
    )
    b = a.model_copy(update={"id_registro": "b", "processo": "autos fictícios B"})
    escolhas = [{"id_registro": "a", "fase": "reincidencia"}, {"id_registro": "b", "fase": "antecedentes"}]
    assert validar_usos_antecedentes(escolhas, [a, b], FATO, SENTENCA, "usos") == {
        "reincidencia": [a], "antecedentes": [b],
    }
    duplicado = b.model_copy(update={"processo": a.processo})
    with pytest.raises(DadosInsuficientesError, match="condenações distintas"):
        validar_usos_antecedentes(escolhas, [a, duplicado], FATO, SENTENCA, "usos")


def test_varios_registros_e_confissao_exigem_decisao_sobre_compensacao():
    """Tema 585/STJ, tese revisada publicada em 24/06/2022.
    https://processo.stj.jus.br/repetitivos/temas_repetitivos/pesquisa.jsp?cod_tema_final=585&cod_tema_inicial=585&novaConsulta=true&tipo_pesquisa=T
    """
    entrada = CrimeDosimetriaInput(
        crime_code="art_147_cp", data_fato=FATO, reincidente=True, confissao_espontanea=True,
        fontes_reincidencia=["ID-CAC-A", "ID-CAC-B"],
    )
    calculo = calcular_dosimetria_crime(entrada)
    assert calculo.compensacao_multirreincidencia_pendente
    assert not calculo.compensacao_confissao_reincidencia
    assert any("Tema 585/STJ" in aviso for aviso in calculo.alertas_juridicos)
