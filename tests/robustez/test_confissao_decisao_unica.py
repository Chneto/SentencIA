"""TASK-086: uma decisão de confissão por imputação (Análise × Dosimetria). Dados fictícios.

CP, art. 65, III, d: https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm
Tema 1194/STJ, acórdão publicado em 16/09/2025 (efeito prejudicial só para fatos posteriores).
"""

from datetime import date

import pytest

from app.core.errors import DadosInsuficientesError
from app.generation.sentence_generator import resolver_dosimetria_e_decisoes
from app.ingestion.confissao import DecisaoConfissao
from app.ingestion.laya_classifier import MetadadosProcesso

SENTENCA = date(2026, 11, 20)


def _meta(fato: date, registrada: str | None) -> MetadadosProcesso:
    return MetadadosProcesso(
        numero_processo="", nome_ofendida="", nome_reu="", data_fato=fato.isoformat(),
        data_recebimento_denuncia="", data_intimacao_mpu="", intimidado_pessoalmente_mpu=False,
        reu_primario_sumula444=False, pedido_dano_moral_expresso_mp=False, valor_dano_moral_pedido_mp=0.0,
        crimes_imputados=["art_147_cp"], teses_defesa=[],
        confissoes_confirmadas={"art_147_cp": DecisaoConfissao(tipo=registrada)} if registrada else {},
    )


def _resolver(fato: date, registrada: str | None, **item):
    config = {"codigo": "art_147_cp", "veredicto": "condenar", "enquadramento_ameaca": "caput", **item}
    return resolver_dosimetria_e_decisoes(
        [], _meta(fato, registrada), [], False, fato, None, {"crimes_config": [config]}, data_sentenca=SENTENCA,
    )


def _crime(fato: date, registrada: str | None, **item):
    dosimetria, *_ = _resolver(fato, registrada, **item)
    return dosimetria.crimes_calculados[0]


def test_negativa_na_analise_e_sim_na_dosimetria_bloqueiam_o_calculo():
    with pytest.raises(DadosInsuficientesError) as erro:
        _resolver(date(2025, 3, 10), "negativa", confissao=True)
    assert "crimes_config.art_147_cp.confissao" in erro.value.campos
    assert "Análise" in str(erro.value) and "Negativa" in str(erro.value)


def test_confissao_na_analise_e_nao_na_dosimetria_bloqueiam_o_calculo():
    with pytest.raises(DadosInsuficientesError):
        _resolver(date(2025, 3, 10), "total", confissao=False)


def test_tipo_divergente_entre_estacoes_bloqueia():
    with pytest.raises(DadosInsuficientesError) as erro:
        _resolver(date(2025, 3, 10), "total", confissao=True, tipo_confissao="parcial")
    assert "crimes_config.art_147_cp.tipo_confissao" in erro.value.campos


def test_conforme_a_analise_segue_o_registro_sem_atenuante_silenciosa():
    negativa = _crime(date(2025, 3, 10), "negativa")
    assert not any("confissão" in a for a in negativa.atenuantes_ativas)
    confessada = _crime(date(2025, 3, 10), "total")
    assert any("confissão" in a for a in confessada.atenuantes_ativas)


def test_mesma_decisao_nas_duas_estacoes_e_aceita():
    crime = _crime(date(2025, 3, 10), "parcial", confissao=True, tipo_confissao="parcial")
    assert any("confissão" in a for a in crime.atenuantes_ativas)


@pytest.mark.parametrize("dia, exige_tipo", [(15, False), (16, False), (17, True)])
def test_sim_sem_tipo_e_sem_registro_exige_tipo_so_depois_do_tema_1194(dia, exige_tipo):
    fato = date(2025, 9, dia)
    if exige_tipo:
        with pytest.raises(DadosInsuficientesError) as erro:
            _resolver(fato, None, confissao=True)
        assert "crimes_config.art_147_cp.tipo_confissao" in erro.value.campos
    else:
        assert any("confissão" in a for a in _crime(fato, None, confissao=True).atenuantes_ativas)


@pytest.mark.parametrize("tipo, pendente", [("total", False), ("parcial", True), ("qualificada", True)])
def test_tipo_informado_na_dosimetria_chega_ao_calculo(tipo, pendente):
    crime = _crime(date(2025, 9, 17), None, confissao=True, tipo_confissao=tipo)
    assert crime.revisao_proporcao_confissao_pendente is pendente


def test_tipo_sem_reconhecimento_e_recusado():
    with pytest.raises(DadosInsuficientesError) as erro:
        _resolver(date(2025, 3, 10), None, confissao=False, tipo_confissao="total")
    assert "crimes_config.art_147_cp.tipo_confissao" in erro.value.campos
