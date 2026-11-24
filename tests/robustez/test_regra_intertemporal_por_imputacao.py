"""Limites e condições das Leis 15.160/2025, 15.438/2026 e 15.380/2026 por imputação."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.generation.sentence_generator import resolver_dosimetria_e_decisoes
from app.ingestion.laya_classifier import MetadadosProcesso
from app.legal_engine.condicoes_acao_penal import CondicoesAcaoPenal, analisar_acao_penal
from app.legal_engine.dosimetria_calculator import (
    CrimeDosimetriaInput,
    calcular_dosimetria_global,
    calcular_prescricao_por_crime,
)
from app.legal_engine.penal_types_catalog import get_penal_type_for_date
from app.main import app


@pytest.mark.parametrize("dia,aplica", [(3, True), (4, False), (5, False)])
def test_lei_15160_por_fato_com_atenuante_e_prescricao(dia, aplica):
    """Lei 15.160/2025, vigente em 04/07/2025, arts. 65, I, e 115 do CP.
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2025/lei/l15160.htm
    """
    fato = date(2025, 7, dia)
    base = {
        "data_fato": fato,
        "menoridade_relativa_ou_senilidade": True,
        "reduzir_prescricao_art115": True,
    }
    dolo_sexual = CrimeDosimetriaInput(crime_code="art_215a_cp", violencia_sexual_contra_mulher=True, **base)
    ameaca = CrimeDosimetriaInput(crime_code="art_147_cp", violencia_sexual_contra_mulher=False, **base)
    dos = calcular_dosimetria_global([dolo_sexual, ameaca])
    prescricao = calcular_prescricao_por_crime(dos, fato, date(2025, 8, 1), date(2025, 9, 1))
    assert dos.crimes_calculados[0].regra_atenuante_etaria == ("aplicavel" if aplica else "excluida")
    assert bool(dos.crimes_calculados[0].atenuantes_ativas) is aplica
    assert prescricao.por_crime[0].reduzido_metade_art115 is aplica
    assert dos.crimes_calculados[1].regra_atenuante_etaria == "aplicavel"
    assert prescricao.por_crime[1].reduzido_metade_art115 is True


def test_hipotese_sexual_pendente_nao_afirma_ausencia_de_prescricao():
    """Lei 15.160/2025, vigente em 04/07/2025.
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2025/lei/l15160.htm
    """
    data_fato = date(2025, 7, 4)
    dos = calcular_dosimetria_global([
        CrimeDosimetriaInput(
            crime_code="art_215a_cp", data_fato=data_fato,
            menoridade_relativa_ou_senilidade=True, reduzir_prescricao_art115=True,
        ),
    ])
    assert dos.crimes_calculados[0].regra_atenuante_etaria == "pendente"
    item = calcular_prescricao_por_crime(dos, data_fato, date(2025, 8, 1), date(2025, 9, 1)).por_crime[0]
    assert item.regra_reducao_etaria == "pendente"
    assert not item.verificacao_completa
    assert not item.reduzido_metade_art115


@pytest.mark.parametrize("dia,meses,estado", [(18, 6, "intempestivo"), (19, 12, "tempestivo"), (20, 12, "tempestivo")])
def test_lei_15438_vigencia_e_ciencia_da_autoria(dia, meses, estado):
    """Lei 15.438/2026, vigente em 19/06/2026, e inclusão do dia inicial pelo art. 10 do CP.
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2026/lei/l15438.htm
    https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm#art10
    """
    fato = date(2026, 6, dia)
    ciencia = date(2026, 7, 1)
    dados = CondicoesAcaoPenal(
        violencia_domestica_familiar_contra_mulher=True, data_ciencia_autoria=ciencia,
        direito_exercido=True, data_exercicio_direito=date(2027, 1, 1),
    )
    spec = get_penal_type_for_date("art_147_cp", fato)
    resultado = analisar_acao_penal(spec, fato, date(2027, 1, 2), None, dados).decadencia
    assert resultado.estado == estado
    assert resultado.prazo_meses == meses
    assert resultado.data_inicio == ciencia
    assert resultado.ultimo_dia == (date(2026, 12, 31) if dia == 18 else date(2027, 6, 30))


def test_decadencia_nao_reabre_prazo_e_depende_do_exercicio_confirmado():
    """Lei 15.438/2026, vigente em 19/06/2026 (Planalto, arts. 2º a 5º).
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2026/lei/l15438.htm
    """
    fato = date(2025, 12, 1)
    dados = CondicoesAcaoPenal(violencia_domestica_familiar_contra_mulher=True, data_ciencia_autoria=fato)
    spec = get_penal_type_for_date("art_147_cp", fato)
    analise = analisar_acao_penal(spec, fato, date(2026, 6, 19), None, dados)
    assert analise.decadencia.estado == "pendente"
    dados.direito_exercido = False
    analise = analisar_acao_penal(spec, fato, date(2026, 6, 19), None, dados)
    assert analise.decadencia.prazo_meses == 6
    assert analise.decadencia.ultimo_dia == date(2026, 5, 31)
    assert analise.decadencia.estado == "prazo_transcorrido"


@pytest.mark.parametrize("dia,fonte_nova", [(6, False), (7, True), (8, True)])
def test_lei_15380_regra_do_ato_e_tema_1167_precedente(dia, fonte_nova):
    """Lei 15.380/2026, vigente em 07/04/2026, art. 2º do CPP e Tema 1167/STJ.
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2026/lei/l15380.htm
    https://www.planalto.gov.br/ccivil_03/decreto-lei/del3689compilado.htm#art2
    https://scon.stj.jus.br/SCON/jurisprudencia/toc.jsp?O=JT&b=ACOR&livre=202103239601.REG.
    """
    fato = date(2026, 1, 1)
    dados = CondicoesAcaoPenal(
        violencia_domestica_familiar_contra_mulher=True, pedido_expresso_retratacao=True,
        data_pedido_retratacao=date(2026, 4, 1), data_audiencia_retratacao=date(2026, 4, dia),
    )
    spec = get_penal_type_for_date("art_147_cp", fato)
    resultado = analisar_acao_penal(spec, fato, date(2026, 6, 1), date(2026, 4, 12), dados).retratacao
    assert resultado.estado == "cabivel"
    assert ("parágrafo único" in resultado.fundamento) is fonte_nova


def test_procedibilidade_por_imputacao_usa_acao_efetiva():
    """Lei 15.380/2026, vigente em 07/04/2026, e Lei 15.438/2026, vigente em 19/06/2026.
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2026/lei/l15380.htm
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2026/lei/l15438.htm
    """
    fato = date(2026, 6, 19)
    analise = date(2026, 10, 1)
    meta = MetadadosProcesso(
        numero_processo="", nome_ofendida="", nome_reu="", data_fato=fato.isoformat(),
        data_recebimento_denuncia="", data_intimacao_mpu="", intimidado_pessoalmente_mpu=False,
        reu_primario_sumula444=False, pedido_dano_moral_expresso_mp=False, valor_dano_moral_pedido_mp=0.0,
        crimes_imputados=["art_147_cp", "art_147a_cp"], teses_defesa=[],
    )
    dados = {
        "violencia_domestica_familiar_contra_mulher": True, "pedido_expresso_retratacao": True,
        "data_pedido_retratacao": "2026-09-29", "denuncia_ainda_nao_recebida": True,
        "data_ciencia_autoria": "2026-06-19", "direito_exercido": False,
    }
    _, _, decisoes, _, _ = resolver_dosimetria_e_decisoes(
        [], meta, [], False, fato, None,
        {"crimes_config": [
            {"codigo": "art_147_cp", "enquadramento_ameaca": "paragrafo_1", "condicoes_acao_penal": dados},
            {"codigo": "art_147a_cp", "condicoes_acao_penal": dados},
        ]},
        data_sentenca=analise,
    )
    primeira, segunda = [decisao["analise_acao_penal"] for decisao in decisoes]
    assert primeira["decadencia"]["estado"] == primeira["retratacao"]["estado"] == "nao_aplicavel"
    assert segunda["decadencia"]["prazo_meses"] == 12
    assert segunda["decadencia"]["estado"] == "em_curso"
    assert segunda["retratacao"]["estado"] == "cabivel"
    _, _, simuladas, _, _ = resolver_dosimetria_e_decisoes(
        [], meta, [], False, fato, None,
        {"crimes_config": [{"codigo": "art_147_cp", "condicoes_acao_penal": dados}]},
        data_sentenca=analise,
    )
    pendente = simuladas[0]["analise_acao_penal"]
    assert pendente["acao_penal"] == "enquadramento_pendente"
    assert pendente["decadencia"]["estado"] == pendente["retratacao"]["estado"] == "pendente"



@pytest.mark.parametrize("pedido,recebimento,esperado", [
    (None, None, "pendente"),
    ("2026-07-03", "2026-07-01", "posterior_ao_recebimento"),
    ("2026-07-01", "2026-07-01", "pendente"),
])
def test_retratacao_depende_da_ordem_confirmada_dos_atos(pedido, recebimento, esperado):
    """Lei 15.380/2026, vigente em 07/04/2026, e art. 16 da Lei Maria da Penha.
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2026/lei/l15380.htm
    """
    fato = date(2026, 5, 1)
    registro = CondicoesAcaoPenal(
        violencia_domestica_familiar_contra_mulher=True,
        pedido_expresso_retratacao=True,
        data_pedido_retratacao=date.fromisoformat(pedido) if pedido else None,
    )
    recebimento = date.fromisoformat(recebimento) if recebimento else None
    spec = get_penal_type_for_date("art_147_cp", fato)
    assert analisar_acao_penal(spec, fato, date(2026, 8, 1), recebimento, registro).retratacao.estado == esperado


def test_api_aceita_registros_tipados_e_serializa_analise_por_imputacao():
    cliente = TestClient(app)
    assert cliente.post("/api/cases/load-demo").status_code == 200
    crime = {
        "codigo": "art_147a_cp", "data_fato": "2026-06-19",
        "violencia_sexual_contra_mulher": False,
        "condicoes_acao_penal": {
            "violencia_domestica_familiar_contra_mulher": True,
            "data_ciencia_autoria": "2026-06-19", "direito_exercido": False,
            "pedido_expresso_retratacao": False,
        },
    }
    payload = {"crimes_config": [crime], "data_sentenca": "2026-07-01"}
    r = cliente.post("/api/dosimetria/simulate", json=payload)
    assert r.status_code == 200
    resultado = next(x for x in r.json()["decisoes_crimes"] if x["crime_code"] == "art_147a_cp")
    assert resultado["analise_acao_penal"]["decadencia"]["prazo_meses"] == 12
    assert resultado["analise_acao_penal"]["retratacao"]["estado"] == "sem_pedido_expresso"
    crime["condicoes_acao_penal"]["direito_exercido"] = "não"
    invalida = cliente.post("/api/dosimetria/simulate", json=payload)
    assert invalida.status_code == 422
    assert ["body", "crimes_config", 0, "condicoes_acao_penal", "direito_exercido"] in [
        detalhe["loc"] for detalhe in invalida.json()["detail"]
    ]
    crime["condicoes_acao_penal"]["direito_exercido"] = False
    crime["condicoes_acao_penal"]["data_ciencia_autoria"] = 1781827200
    assert cliente.post("/api/dosimetria/simulate", json=payload).status_code == 422



def test_recebimento_informado_pelo_juizo_e_especifico_da_imputacao():
    """A Lei 15.380/2026, vigente em 07/04/2026, exige ato anterior ao recebimento.
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2026/lei/l15380.htm
    """
    fato = date(2026, 5, 1)
    registro = CondicoesAcaoPenal(
        violencia_domestica_familiar_contra_mulher=True,
        pedido_expresso_retratacao=True,
        data_pedido_retratacao=date(2026, 6, 9),
        data_audiencia_retratacao=date(2026, 6, 10),
        data_recebimento_denuncia=date(2026, 6, 12),
    )
    spec = get_penal_type_for_date("art_147_cp", fato)
    assert analisar_acao_penal(spec, fato, date(2026, 6, 20), date(2026, 6, 8), registro).retratacao.estado == "cabivel"
