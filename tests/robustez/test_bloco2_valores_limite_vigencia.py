# -*- coding: utf-8 -*-
"""BLOCO 2 — Análise de valor-limite (boundary value analysis) das vigências legais.

Método: para cada lei que cria, altera ou majora um tipo, testa-se a VÉSPERA, o DIA e o DIA SEGUINTE da vigência.
As datas são LITERAIS, tiradas das publicações oficiais (não das constantes do catálogo testado: se uma constante
mudar por engano, o teste acusa — revisão cruzada do CODEX na TASK-072). Uma lei nova = uma linha nas tabelas.

Fontes oficiais (vigência na data da publicação, salvo indicação):
- Lei 13.641/2018 (art. 24-A da LMP): publicada no DOU de 04/04/2018.
  https://www2.camara.leg.br/legin/fed/lei/2018/lei-13641-3-abril-2018-786397-publicacaooriginal-155153-pl.html
- Lei 14.132/2021 (art. 147-A do CP): DOU de 01/04/2021. https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14132.htm
- Lei 14.188/2021 (art. 129, § 13, e art. 147-B do CP): DOU de 29/07/2021.
  https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14188.htm
- Lei 14.994/2024 (§ 9º e § 13 do art. 129, § 1º do art. 147, pena do art. 24-A): DOU de 10/10/2024.
  https://planalto.gov.br/ccivil_03/_ato2023-2026/2024/lei/l14994.htm
- Lei 15.280/2025 (art. 338-A do CP): vigente em 08/12/2025. https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2025/lei/l15280.htm
- Lei 15.383/2026 (art. 24-A, § 4º, da LMP): DOU de 10/04/2026. https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2026/lei/l15383.htm
"""

from __future__ import annotations

import datetime

import pytest

from app.core.errors import DadosInsuficientesError
from app.legal_engine.dosimetria_calculator import CrimeDosimetriaInput, PenaAnosMesesDias, calcular_dosimetria_crime
from app.legal_engine.penal_types_catalog import get_penal_type_for_date, verificar_tipo_existente_na_data

D = datetime.date
UM_DIA = datetime.timedelta(days=1)

LEI_13641_2018 = D(2018, 4, 4)
LEI_14132_2021 = D(2021, 4, 1)
LEI_14188_2021 = D(2021, 7, 29)
LEI_14994_2024 = D(2024, 10, 10)
LEI_15280_2025 = D(2025, 12, 8)
LEI_15383_2026 = D(2026, 4, 10)


def _d(anos: int = 0, meses: int = 0) -> int:
    """Dias pela convenção do próprio motor (ano = 12 meses; ver PenaAnosMesesDias)."""
    return PenaAnosMesesDias(anos=anos, meses=meses).to_total_days()


CRIACAO = [
    ("art_24a_lmp", LEI_13641_2018),
    ("art_147a_cp", LEI_14132_2021),
    ("art_147b_cp", LEI_14188_2021),
    ("art_338a_cp", LEI_15280_2025),
]

# (código, kwargs, data de mudança, (espécie, min, max) antes, depois)
MUDANCA_DE_PENA = [
    ("art_129_p9_cp", {}, LEI_14994_2024, ("detencao", _d(meses=3), _d(anos=3)), ("reclusao", _d(anos=2), _d(anos=5))),
    ("art_24a_lmp", {}, LEI_14994_2024, ("detencao", _d(meses=3), _d(anos=2)), ("reclusao", _d(anos=2), _d(anos=5))),
    # § 13 do art. 129: criado pela Lei 14.188/2021 (reclusão de 1 a 4 anos) e majorado pela Lei 14.994/2024 (2 a 5).
    ("art_129_p13_cp", {}, LEI_14188_2021, ("detencao", _d(meses=3), _d(anos=3)), ("reclusao", _d(anos=1), _d(anos=4))),
    ("art_129_p13_cp", {}, LEI_14994_2024, ("reclusao", _d(anos=1), _d(anos=4)), ("reclusao", _d(anos=2), _d(anos=5))),
]


def _faixa(spec):
    return spec.especie_pena, spec.min_pena.to_total_days(), spec.max_pena.to_total_days()


@pytest.mark.parametrize("codigo,criacao", CRIACAO)
def test_tipo_inexistente_na_vespera_e_existente_no_dia_e_no_dia_seguinte(codigo, criacao):
    with pytest.raises(Exception) as exc:
        verificar_tipo_existente_na_data(codigo, criacao - UM_DIA)
    assert type(exc.value).__name__ == "TipoPenalInexistenteNaDataError"
    for dia in (criacao, criacao + UM_DIA):
        verificar_tipo_existente_na_data(codigo, dia)
        assert get_penal_type_for_date(codigo, dia).code == codigo


@pytest.mark.parametrize("codigo,kw,mudanca,antes,depois", MUDANCA_DE_PENA,
                         ids=[f"{m[0]}@{m[2]}" for m in MUDANCA_DE_PENA])
def test_pena_muda_exatamente_no_dia_da_vigencia(codigo, kw, mudanca, antes, depois):
    assert _faixa(get_penal_type_for_date(codigo, mudanca - UM_DIA, **kw)) == antes, (codigo, "véspera")
    for dia in (mudanca, mudanca + UM_DIA):
        assert _faixa(get_penal_type_for_date(codigo, dia, **kw)) == depois, (codigo, dia)


def test_ameaca_qualificada_so_existe_a_partir_de_10_10_2024():
    with pytest.raises(Exception):
        get_penal_type_for_date("art_147_cp", LEI_14994_2024 - UM_DIA, ameaca_qualificada=True)
    for dia in (LEI_14994_2024, LEI_14994_2024 + UM_DIA):
        q = get_penal_type_for_date("art_147_cp", dia, ameaca_qualificada=True)
        c = get_penal_type_for_date("art_147_cp", dia)
        assert q.max_pena.to_total_days() == 2 * c.max_pena.to_total_days()  # pena em dobro (§ 1º)
        assert q.acao_penal == "publica_incondicionada"


def test_majorante_do_24a_p4_so_admitida_a_partir_de_10_04_2026():
    assert not get_penal_type_for_date("art_24a_lmp", LEI_15383_2026 - UM_DIA).admite_aumento_art24a_p4
    for dia in (LEI_15383_2026, LEI_15383_2026 + UM_DIA):
        assert get_penal_type_for_date("art_24a_lmp", dia).admite_aumento_art24a_p4


@pytest.mark.parametrize("deslocamento", [-1, 0, 1])
def test_majorante_do_147a_requer_vigencia_e_reconhecimento_expresso(deslocamento):
    """Lei 14.132/2021, em vigor desde 01/04/2021 (publicação oficial no Planalto)."""
    dia = LEI_14132_2021 + deslocamento * UM_DIA
    entrada = CrimeDosimetriaInput(crime_code="art_147a_cp", data_fato=dia)
    expressa = entrada.model_copy(update={"aplicar_aumento_art147a_p1_ii": True})
    if deslocamento < 0:
        with pytest.raises(DadosInsuficientesError):
            calcular_dosimetria_crime(expressa)
        return
    assert calcular_dosimetria_crime(entrada).pena_definitiva.to_total_days() == 180
    assert calcular_dosimetria_crime(expressa).pena_definitiva.to_total_days() == 270
