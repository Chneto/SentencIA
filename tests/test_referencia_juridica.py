# -*- coding: utf-8 -*-
"""Testes de aceitação jurídica com fontes oficiais (Planalto/STF/STJ) e datas de vigência (TASK-070).

Regra do Ciclo 7 (§2): cada teste registra a fonte oficial e a data de vigência, e verifica
explicitamente a véspera, o dia de início da vigência e o dia seguinte.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.legal_engine.condicoes_acao_penal import estado_beneficio_etario
from app.legal_engine.penal_types_catalog import (
    TipoPenalInexistenteNaDataError,
    get_penal_type_for_date,
    verificar_tipo_existente_na_data,
)

pytestmark = pytest.mark.aceitacao_juridica


def test_lei_14994_2024_pacote_antifeminicidio_vigencia():
    """
    Fonte oficial: Lei nº 14.994/2024 (Planalto:
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2024/lei/l14994.htm).
    Vigência: 10/10/2024 (publicação no DOU em 10/10/2024, art. 9º).
    Altera art. 129, § 9º e § 13, do CP (reclusão de 2 a 5 anos).
    """
    vespera = date(2024, 10, 9)
    dia = date(2024, 10, 10)
    dia_seguinte = date(2024, 10, 11)

    spec_vespera = get_penal_type_for_date("art_129_p9_cp", vespera)
    spec_dia = get_penal_type_for_date("art_129_p9_cp", dia)
    spec_seguinte = get_penal_type_for_date("art_129_p9_cp", dia_seguinte)

    # Antes da Lei 14.994/2024: detenção de 3 meses a 3 anos
    assert (spec_vespera.min_pena.anos, spec_vespera.min_pena.meses) == (0, 3)
    assert (spec_vespera.max_pena.anos, spec_vespera.max_pena.meses) == (3, 0)
    assert spec_vespera.especie_pena == "detencao"
    # A partir de 10/10/2024: reclusão de 2 a 5 anos
    for sp in (spec_dia, spec_seguinte):
        assert (sp.min_pena.anos, sp.min_pena.meses) == (2, 0)
        assert (sp.max_pena.anos, sp.max_pena.meses) == (5, 0)
        assert sp.especie_pena == "reclusao"


def test_lei_14132_2021_perseguicao_art_147_a_vigencia():
    """
    Fonte oficial: Lei nº 14.132/2021 (Planalto:
    https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14132.htm).
    Vigência: 01/04/2021 (art. 4º). Cria o crime de perseguição (art. 147-A do CP).
    """
    vespera = date(2021, 3, 31)
    dia = date(2021, 4, 1)
    dia_seguinte = date(2021, 4, 2)

    with pytest.raises(TipoPenalInexistenteNaDataError):
        verificar_tipo_existente_na_data("art_147a_cp", vespera)

    verificar_tipo_existente_na_data("art_147a_cp", dia)
    verificar_tipo_existente_na_data("art_147a_cp", dia_seguinte)


def test_lei_14188_2021_art_129_p13_e_147_b_vigencia():
    """
    Fonte oficial: Lei nº 14.188/2021 (Planalto:
    https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14188.htm).
    Vigência: 29/07/2021 (art. 6º). Cria o art. 129, § 13, e o art. 147-B do CP.
    """
    vespera = date(2021, 7, 28)
    dia = date(2021, 7, 29)
    dia_seguinte = date(2021, 7, 30)

    with pytest.raises(TipoPenalInexistenteNaDataError):
        verificar_tipo_existente_na_data("art_147b_cp", vespera)
    verificar_tipo_existente_na_data("art_147b_cp", dia)
    verificar_tipo_existente_na_data("art_147b_cp", dia_seguinte)

    # Para o art. 129, § 13, fato na véspera aplica o § 9º (3m a 3a); no dia e seguinte aplica 1a a 4a
    spec_vespera = get_penal_type_for_date("art_129_p13_cp", vespera)
    spec_dia = get_penal_type_for_date("art_129_p13_cp", dia)
    spec_seguinte = get_penal_type_for_date("art_129_p13_cp", dia_seguinte)
    assert (spec_vespera.min_pena.anos, spec_vespera.min_pena.meses) == (0, 3)
    assert (spec_dia.min_pena.anos, spec_dia.min_pena.meses) == (1, 0)
    assert (spec_seguinte.min_pena.anos, spec_seguinte.min_pena.meses) == (1, 0)


def test_lei_13641_2018_art_24_a_lmp_vigencia():
    """
    Fonte oficial: Lei nº 13.641/2018 (Planalto:
    https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13641.htm).
    Vigência: 04/04/2018 (art. 3º). Cria o art. 24-A da Lei nº 11.340/2006.
    """
    vespera = date(2018, 4, 3)
    dia = date(2018, 4, 4)
    dia_seguinte = date(2018, 4, 5)

    with pytest.raises(TipoPenalInexistenteNaDataError):
        verificar_tipo_existente_na_data("art_24a_lmp", vespera)

    verificar_tipo_existente_na_data("art_24a_lmp", dia)
    verificar_tipo_existente_na_data("art_24a_lmp", dia_seguinte)


def test_lei_15160_2025_restricao_beneficio_etario_vigencia():
    """
    Fonte oficial: Lei nº 15.160/2025 (Planalto:
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2025/lei/l15160.htm).
    Vigência: 04/07/2025 (art. 3º). Afasta arts. 65, I, e 115 do CP em violência sexual contra a mulher.
    """
    vespera = date(2025, 7, 3)
    dia = date(2025, 7, 4)
    dia_seguinte = date(2025, 7, 5)

    assert estado_beneficio_etario(vespera, True, True) == "aplicavel"
    assert estado_beneficio_etario(dia, True, True) == "excluida"
    assert estado_beneficio_etario(dia_seguinte, True, True) == "excluida"
