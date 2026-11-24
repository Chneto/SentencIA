"""Prescrição por imputação, com atos documentados e estados separados."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.legal_engine.dosimetria_calculator import (
    AlvoPrescricaoAbstrata,
    CrimeDosimetriaInput,
    calcular_dosimetria_global,
    calcular_prescricao_por_crime,
)
from app.legal_engine.linha_tempo_prescricao import (
    LinhaTempoPrescricao,
    calcular_lapsos_prescricionais,
    intervalos_suspensos,
    validar_vigencia_marcos,
)
from app.legal_engine.penal_types_catalog import get_penal_type_for_date
from app.main import app


@pytest.mark.parametrize("inicio,ultimo_dia,consumacao", [
    (date(2021, 2, 1), date(2025, 1, 31), date(2025, 2, 1)),
    (date(2020, 2, 29), date(2024, 2, 28), date(2024, 2, 29)),
    (date(2096, 2, 29), date(2100, 2, 28), date(2100, 3, 1)),
])
def test_fronteira_do_prazo_em_calendario_comum(inicio, ultimo_dia, consumacao):
    """CP, art. 10, redação da Lei 7.209/1984 vigente seis meses após 13/07/1984.
    https://www.planalto.gov.br/ccivil_03/leis/1980-1988/l7209.htm
    """
    v = calcular_lapsos_prescricionais(inicio, ultimo_dia, 48, set(), [])[0]
    a = calcular_lapsos_prescricionais(inicio, consumacao, 48, set(), [])[0]
    assert not v.consumado
    assert a.consumado
    assert v.primeiro_dia_consumado == a.primeiro_dia_consumado == consumacao


def test_interrupcao_reinicia_e_suspensoes_sobrepostas_nao_somam_duas_vezes():
    """CP, arts. 116, I e II, e 117, redação da Lei 7.209/1984, DOU 13/07/1984.
    https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm
    """
    dados = LinhaTempoPrescricao(suspensoes=[
        {"tipo": "questao_prejudicial", "data": "2023-07-01", "data_fim": "2023-07-10", "fonte_id": "ID-ATO-A"},
        {"tipo": "cumprimento_pena_exterior", "data": "2023-07-05", "data_fim": "2023-07-20", "fonte_id": "ID-ATO-B"},
    ])
    fim = date(2026, 2, 20)
    intervalos = intervalos_suspensos(dados.suspensoes, fim, 36)
    pre, pos = calcular_lapsos_prescricionais(date(2022, 1, 1), fim, 36, {date(2023, 2, 1)}, intervalos)
    assert pre.dias_suspensos == 0 and not pre.consumado
    assert pos.dias_suspensos == 19 and pos.consumado
    assert pos.primeiro_dia_consumado == fim


def test_suspensao_por_edital_tem_limite_do_prazo_em_abstrato():
    """Art. 366 do CPP (Lei 9.271/1996, publicada 18/04/1996, vigência após 60 dias) e Tema 438/STF.
    https://www.planalto.gov.br/ccivil_03/leis/l9271.htm
    https://portal.stf.jus.br/jurisprudenciaRepercussao/tema.asp?num=438
    """
    dados = LinhaTempoPrescricao(suspensoes=[
        {"tipo": "citacao_edital_art366", "data": "2023-03-02", "fonte_id": "ID-ATO-A"},
    ])
    assert intervalos_suspensos(dados.suspensoes, date(2029, 1, 1), 36) == [(date(2023, 3, 2), date(2026, 3, 2))]


@pytest.mark.parametrize("dia,esperado", [(16, True), (17, False), (18, False)])
def test_intertemporal_da_suspensao_dos_arts_366_e_368(dia, esperado):
    """Lei 9.271/1996, DOU de 18/04/1996, vigente 60 dias após a publicação, em 17/06/1996.
    https://www.planalto.gov.br/ccivil_03/leis/l9271.htm
    """
    linha = LinhaTempoPrescricao(suspensoes=[
        {"tipo": "citacao_edital_art366", "data": "1998-03-02", "fonte_id": "ID-ATO-A"},
    ])
    assert bool(validar_vigencia_marcos(linha, date(1996, 6, dia))) is esperado


@pytest.mark.parametrize("fato,esperado", [
    (date(2007, 11, 29), True), (date(2007, 11, 30), False), (date(2007, 12, 1), False),
])
def test_intertemporal_do_acordao_confirmatorio(fato, esperado):
    """Lei 11.596/2007, publicada e vigente em 30/11/2007. Tema 1100/STJ.
    https://www.planalto.gov.br/ccivil_03/_ato2007-2010/2007/lei/l11596.htm
    https://processo.stj.jus.br/jurisprudencia/externo/informativo/?livre=%40CNOT%3D019265
    """
    linha = LinhaTempoPrescricao(interrupcoes=[
        {"tipo": "publicacao_acordao_confirmatorio", "data": "2012-03-02", "fonte_id": "ID-ATO-A"},
    ])
    assert bool(validar_vigencia_marcos(linha, fato)) is esperado


@pytest.mark.parametrize("dia,esperado", [(22, True), (23, False), (24, False)])
def test_intertemporal_de_suspensao_do_anpp(dia, esperado):
    """Lei 13.964/2019, vigente em 23/01/2020, art. 116, IV, do CP.
    https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2019/lei/l13964.htm
    """
    linha = LinhaTempoPrescricao(suspensoes=[
        {"tipo": "acordo_nao_persecucao_penal", "data": "2021-03-02", "fonte_id": "ID-ATO-A"},
    ])
    assert bool(validar_vigencia_marcos(linha, date(2020, 1, dia))) is esperado


def test_projecao_retroativa_e_reconhecimento_expressamente_documentado():
    """CP, art. 110, § 1º, redação da Lei 12.234/2010, vigente em 06/05/2010.
    https://www.planalto.gov.br/ccivil_03/_ato2007-2010/2010/lei/l12234.htm
    """
    fato, recebimento = date(2022, 1, 1), date(2022, 1, 15)
    spec = get_penal_type_for_date("art_129_p13_cp", fato)
    entrada = CrimeDosimetriaInput(crime_code=spec.code, data_fato=fato, linha_tempo_prescricao=LinhaTempoPrescricao())
    dos = calcular_dosimetria_global([entrada])
    projetada = calcular_prescricao_por_crime(dos, fato, recebimento, date(2026, 1, 15)).por_crime[0]
    assert projetada.estado == "projetada"
    assert projetada.projecao_retroativa and not projetada.prescricao_reconhecida

    anteriores = LinhaTempoPrescricao(
        pena_documentada={"data": "2026-01-15", "fonte_id": "ID-PENA", "anos": 1, "meses": 0, "dias": 0},
        estabilidade_acusacao={"tipo": "transito_para_acusacao", "data": "2026-02-15", "fonte_id": "ID-TRANSITO"},
    )
    alvo = AlvoPrescricaoAbstrata(
        crime_spec=spec, data_fato=fato, data_recebimento=recebimento, linha_tempo_prescricao=anteriores,
    )
    sem_pena = calcular_dosimetria_global([])
    pendente = calcular_prescricao_por_crime(
        sem_pena, fato, recebimento, date(2026, 3, 1), imputacoes_sem_pena=[alvo],
    ).por_crime[0]
    assert pendente.estado == "pendente"
    assert not pendente.prescricao_reconhecida

    validada = anteriores.model_dump()
    validada["reconhecimento"] = {"modalidade": "retroativa", "data": "2026-02-16", "fonte_id": "ID-DECISAO"}
    alvo.linha_tempo_prescricao = LinhaTempoPrescricao.model_validate(validada)
    reconhecida = calcular_prescricao_por_crime(
        sem_pena, fato, recebimento, date(2026, 3, 1), imputacoes_sem_pena=[alvo],
    ).por_crime[0]
    assert reconhecida.estado == "reconhecida"
    assert reconhecida.prescricao_retroativa_reconhecida
    assert "ID-DECISAO" in reconhecida.fundamentacao


def test_prescricao_abstrata_sem_pena_convive_com_marco_inconclusivo():
    """CP, arts. 109, 117 e 119, arts. com redação da Lei 7.209/1984, DOU de 13/07/1984.
    https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm
    """
    fato, recebimento, data_analise = date(2019, 1, 1), date(2024, 1, 1), date(2026, 1, 1)
    spec = get_penal_type_for_date("art_147_cp", fato)
    dosimetria = calcular_dosimetria_global([])
    res = calcular_prescricao_por_crime(
        dosimetria, fato, recebimento, data_analise,
        imputacoes_sem_pena=[
            AlvoPrescricaoAbstrata(crime_spec=spec, data_fato=fato, data_recebimento=recebimento, id_imputacao="A"),
            AlvoPrescricaoAbstrata(crime_spec=spec, data_fato=fato, id_imputacao="B"),
        ],
    )
    a, b = res.por_crime
    assert a.estado == "abstrata" and a.pena_concreta is None
    assert a.indicio_abstrato and not a.prescricao_reconhecida
    assert b.estado == "inconclusiva" and not b.verificacao_completa


def test_api_rejeita_linha_sem_fonte_nos_autos():
    cliente = TestClient(app)
    assert cliente.post("/api/cases/load-demo").status_code == 200
    linha = {"interrupcoes": [{
        "tipo": "pronuncia", "data": "2025-12-01", "fonte_id": "ID-FICTICIO-AUSENTE",
    }]}
    resposta = cliente.post("/api/dosimetria/simulate", json={
        "crimes_config": [{"codigo": "art_147_cp", "linha_tempo_prescricao": linha}],
    })
    assert resposta.status_code == 422
    assert resposta.json()["campos"][0].endswith("linha_tempo_prescricao.interrupcoes.0.fonte_id")
