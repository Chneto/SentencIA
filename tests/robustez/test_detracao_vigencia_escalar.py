"""TASK-093: detração por quantidade sob a mesma regra de vigência da documentada. Dados fictícios.

Lei 12.736/2012, arts. 2º e 3º (vigência na publicação, 03/12/2012):
https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2012/lei/l12736.htm
"""

from datetime import date

import pytest

from app.generation.sentence_generator import _resolver_detracao
from app.ingestion.laya_classifier import MetadadosProcesso
from app.legal_engine.detracao import IntervaloCustodia, calcular_detracao, detracao_por_quantidade


def _meta() -> MetadadosProcesso:
    return MetadadosProcesso(
        numero_processo="", nome_ofendida="", nome_reu="", data_fato="2012-10-01",
        data_recebimento_denuncia="", data_intimacao_mpu="", intimidado_pessoalmente_mpu=False,
        reu_primario_sumula444=False, pedido_dano_moral_expresso_mp=False, valor_dano_moral_pedido_mp=0.0,
        crimes_imputados=["art_147_cp"], teses_defesa=[],
    )


INTERVALO = IntervaloCustodia(inicio=date(2012, 10, 1), fim=date(2012, 10, 2), fonte_inicio="ID-A", fonte_fim="ID-B")


@pytest.mark.parametrize("dia, dias", [(2, 0), (3, 2), (4, 2)])
def test_quantidade_e_intervalos_seguem_a_mesma_vigencia(dia, dias):
    sentenca = date(2012, 12, dia)
    escalar = detracao_por_quantidade(2, data_referencia=sentenca)
    documentada = calcular_detracao([INTERVALO], data_referencia=sentenca)
    assert escalar.dias == documentada.dias == dias
    assert escalar.origem == "legado" and documentada.origem == "autos"
    if dias == 0:
        assert all("Lei 12.736/2012" in "".join(r.pendencias) for r in (escalar, documentada))


@pytest.mark.parametrize("opcoes", [
    {"dias_prisao_cautelar": 2},
])
@pytest.mark.parametrize("dia, dias", [(2, 0), (3, 2), (4, 2)])
def test_caminhos_escalares_do_gerador_aplicam_a_vigencia(opcoes, dia, dias):
    sentenca = date(2012, 12, dia)
    assert _resolver_detracao(_meta(), [], opcoes, None, sentenca, False).dias == dias
    assert _resolver_detracao(_meta(), [], {}, {"dias": 2}, sentenca, False).dias == dias
    assert _resolver_detracao(_meta(), [], {}, 2, sentenca, False).dias == dias


def test_sem_data_de_referencia_mantem_a_quantidade():
    assert detracao_por_quantidade(5).dias == 5
