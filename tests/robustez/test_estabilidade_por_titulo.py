"""TASK-089: estabilidade da acusação vinculada ao título da pena usada (art. 110, § 1º, do CP). Dados fictícios.

CP, art. 110, § 1º (Lei 12.234/2010): https://www.planalto.gov.br/ccivil_03/_ato2007-2010/2010/lei/l12234.htm
"""

from datetime import date

import pytest

from app.legal_engine.dosimetria_calculator import (
    AlvoPrescricaoAbstrata,
    calcular_dosimetria_global,
    calcular_prescricao_por_crime,
)
from app.legal_engine.linha_tempo_prescricao import LinhaTempoPrescricao
from app.legal_engine.penal_types_catalog import get_penal_type_for_date

FATO, RECEBIMENTO, ANALISE = date(2022, 1, 1), date(2022, 1, 15), date(2026, 6, 1)


def _resultado(estabilidade: dict, *, com_titulo_a: bool = True):
    spec = get_penal_type_for_date("art_129_p13_cp", FATO)
    interrupcoes = [
        {"tipo": "publicacao_sentenca_condenatoria", "data": "2026-01-20", "fonte_id": "ID-SENTENCA-A"},
    ] if com_titulo_a else []
    linha = LinhaTempoPrescricao.model_validate({
        "interrupcoes": interrupcoes,
        # Pena de 1 ano do acórdão B: prazo de 4 anos (art. 109, V), consumado entre o recebimento (15/01/2022) e A (20/01/2026).
        "pena_documentada": {"data": "2026-02-10", "fonte_id": "ID-ACORDAO-B", "anos": 1, "meses": 0, "dias": 0},
        "estabilidade_acusacao": estabilidade,
        "reconhecimento": {"modalidade": "retroativa", "data": "2026-04-01", "fonte_id": "ID-DECISAO"},
    })
    alvo = AlvoPrescricaoAbstrata(crime_spec=spec, data_fato=FATO, data_recebimento=RECEBIMENTO, linha_tempo_prescricao=linha)
    return calcular_prescricao_por_crime(
        calcular_dosimetria_global([]), FATO, RECEBIMENTO, ANALISE, imputacoes_sem_pena=[alvo],
    ).por_crime[0]


def test_estabilidade_de_a_nao_se_transfere_para_a_pena_de_b():
    res = _resultado({
        "tipo": "transito_para_acusacao", "data": "2026-01-25", "fonte_id": "ID-TRANSITO-A",
        "titulo_fonte_id": "ID-SENTENCA-A",
    })
    assert res.estado != "reconhecida" and not res.prescricao_reconhecida
    assert any("não se transfere" in alerta for alerta in res.alertas)


def test_sem_vinculo_e_com_dois_titulos_pede_o_titulo():
    res = _resultado({"tipo": "transito_para_acusacao", "data": "2026-03-01", "fonte_id": "ID-TRANSITO"})
    assert not res.prescricao_reconhecida
    assert any("Informe a qual decisão condenatória" in alerta for alerta in res.alertas)


@pytest.mark.parametrize("vinculo", ["ID-ACORDAO-B", None])
def test_estabilidade_do_proprio_titulo_permite_o_reconhecimento(vinculo):
    estabilidade = {"tipo": "transito_para_acusacao", "data": "2026-03-01", "fonte_id": "ID-TRANSITO-B"}
    if vinculo:
        estabilidade["titulo_fonte_id"] = vinculo
    res = _resultado(estabilidade, com_titulo_a=vinculo is not None)
    assert res.estado == "reconhecida" and res.prescricao_retroativa_reconhecida


def test_estabilidade_anterior_ao_proprio_titulo_nao_vale():
    res = _resultado({
        "tipo": "transito_para_acusacao", "data": "2026-01-25", "fonte_id": "ID-TRANSITO",
        "titulo_fonte_id": "ID-ACORDAO-B",
    })
    assert not res.prescricao_reconhecida
