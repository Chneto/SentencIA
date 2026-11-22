# -*- coding: utf-8 -*-
"""Tier 2: Boundary & Corner Cases Test Suite.

Exhaustively verifies statutory, constitutional, and mathematical boundaries:
- Tempus Regit Actum (10/10/2024 boundary: Art. 129 § 13 CP, Art. 24-A LMP, Art. 147 CP)
- Súmula 231 STJ: Attempting to reduce intermediary penalty below abstract statutory minimum
- Súmula 444 STJ: Police inquiries and pending lawsuits cannot worsen maus antecedentes
- Art. 61, II, 'f' CP: Strict bis in idem lock for domestic crimes
- Concurso Material (Art. 69 CP): Strict segregation of reclusão and detenção
- Súmula 588 STJ & Súmula 536 STJ: Strict prohibition of PRD substitution and despenalização
- Prescrição da Pretensão Punitiva (Art. 109 CP & Art. 115 CP)
"""

import datetime
from typing import Dict
import pytest

from app.legal_engine.dosimetria_calculator import (
    CircunstanciaArt59,
    ConcursoCrimesConfig,
    CrimeDosimetriaInput,
    DosimetriaConfig,
    PenaAnosMesesDias,
    calcular_dosimetria_global,
    calcular_prescricao,
)
from app.legal_engine.penal_types_catalog import (
    get_penal_type_for_date,
)


def _make_c59_dict(
    culpabilidade: bool = False,
    antecedentes_transitado: bool = False,
    motivos: bool = False,
    circunstancias: bool = False,
    consequencias: bool = False,
) -> Dict[str, CircunstanciaArt59]:
  """Helper to construct 8 circumstances dictionary with fine-grained control."""
  vetores = [
      "culpabilidade",
      "antecedentes",
      "conduta_social",
      "personalidade",
      "motivos",
      "circunstancias",
      "consequencias",
      "comportamento_vitima",
  ]
  res = {}
  for v in vetores:
    desfav = False
    transitado = False
    if v == "culpabilidade" and culpabilidade:
      desfav = True
    elif v == "antecedentes" and antecedentes_transitado:
      desfav = True
      transitado = True
    elif v == "motivos" and motivos:
      desfav = True
    elif v == "circunstancias" and circunstancias:
      desfav = True
    elif v == "consequencias" and consequencias:
      desfav = True
    res[v] = CircunstanciaArt59(
        desfavoravel=desfav,
        possui_transito_em_julgado_anterior=transitado,
        fundamentacao=f"Analise de {v}",
    )
  return res


# ==============================================================================
# 1. Tempus Regit Actum (10/10/2024 Boundary — Lei 14.994/2024)
# ==============================================================================
class TestTempusRegitActumBoundary:
  """Verifies absolute boundary of 10/10/2024 and non-retroactivity of lex gravior."""

  def test_art129_p13_boundary_2024_10_09_vs_2024_10_10(self):
    """Art. 129 § 13 CP: 1 to 4 years on 09/10/2024 vs 2 to 5 years on 10/10/2024."""
    pre_date = datetime.date(2024, 10, 9)
    post_date = datetime.date(2024, 10, 10)

    pre_spec = get_penal_type_for_date("art_129_p13_cp", pre_date)
    post_spec = get_penal_type_for_date("art_129_p13_cp", post_date)

    assert pre_spec.min_pena.anos == 1
    assert pre_spec.max_pena.anos == 4
    assert pre_spec.especie_pena == "reclusao"

    assert post_spec.min_pena.anos == 2
    assert post_spec.max_pena.anos == 5
    assert post_spec.especie_pena == "reclusao"

    # Verifies step 1/8 calculation on both intervals
    # Pre: 4y - 1y = 3y = 1080 days -> step = 135 days (4m 15d)
    pre_interval = (
        pre_spec.max_pena.to_total_days() - pre_spec.min_pena.to_total_days()
    )
    assert pre_interval // 8 == 135

    # Post: 5y - 2y = 3y = 1080 days -> step = 135 days (4m 15d)
    post_interval = (
        post_spec.max_pena.to_total_days() - post_spec.min_pena.to_total_days()
    )
    assert post_interval // 8 == 135

  def test_art24a_lmp_specie_shift_detencao_to_reclusao(self):
    """Art. 24-A LMP: Detenção 3m-2a pre-14.994 vs Reclusão 2a-5a post-14.994."""
    pre_date = datetime.date(2024, 10, 9)
    post_date = datetime.date(2024, 10, 10)

    pre_spec = get_penal_type_for_date("art_24a_lmp", pre_date)
    post_spec = get_penal_type_for_date("art_24a_lmp", post_date)

    # Pre-14.994 is detenção, 3 months to 2 years
    assert pre_spec.especie_pena == "detencao"
    assert pre_spec.min_pena.anos == 0
    assert pre_spec.min_pena.meses == 3
    assert pre_spec.max_pena.anos == 2

    # Post-14.994 changed both species and range: reclusão, 2 to 5 years
    assert post_spec.especie_pena == "reclusao"
    assert post_spec.min_pena.anos == 2
    assert post_spec.max_pena.anos == 5

  def test_art147_cp_simple_vs_double_threat(self):
    """Art. 147 CP: 1 to 6 months pre-14.994 vs 2 months to 1 year post-14.994."""
    pre_date = datetime.date(2024, 10, 9)
    post_date = datetime.date(2024, 10, 10)

    pre_spec = get_penal_type_for_date("art_147_cp", pre_date)
    post_spec = get_penal_type_for_date("art_147_cp", post_date)

    assert pre_spec.especie_pena == "detencao"
    assert pre_spec.min_pena.meses == 1
    assert pre_spec.max_pena.meses == 6

    assert post_spec.especie_pena == "detencao"
    assert post_spec.min_pena.meses == 2
    assert post_spec.max_pena.anos == 1

  def test_non_retroactivity_constitutional_principle(self):
    """Fact occurring on 2024-10-09 adjudicated in 2026 MUST use pre-14.994 limits."""
    fact_date = datetime.date(2024, 10, 9)
    crime_input = CrimeDosimetriaInput(
        crime_code="art_129_p13_cp",
        data_fato=fact_date,
        circunstancias_art59=_make_c59_dict(),
    )
    res = calcular_dosimetria_global(
        crimes=[crime_input],
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc = res.crimes_calculados[0]
    # Base penalty must be 1 year (pre), never 2 years (post)
    assert calc.pena_base.anos == 1
    assert calc.pena_definitiva.anos == 1

  def test_calculator_uses_crime_spec_per_fact_date(self):
    """Fact on 2024-10-10 uses post-14.994 minimum of 2 years."""
    fact_date = datetime.date(2024, 10, 10)
    crime_input = CrimeDosimetriaInput(
        crime_code="art_129_p13_cp",
        data_fato=fact_date,
        circunstancias_art59=_make_c59_dict(),
    )
    res = calcular_dosimetria_global(
        crimes=[crime_input],
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc = res.crimes_calculados[0]
    assert calc.pena_base.anos == 2
    assert calc.pena_definitiva.anos == 2


# ==============================================================================
# 2. Súmula 231 STJ (Intermediary Penalty Floor)
# ==============================================================================
class TestSumula231STJCornerCases:
  """Verifies that mitigating factors can never reduce intermediary penalty below abstract minimum."""

  def test_base_at_floor_with_confession_cannot_drop(self):
    """Pena-base at statutory minimum (2 years) + confissão espontânea -> 2 years."""
    crime_input = CrimeDosimetriaInput(
        crime_code="art_129_p13_cp",
        data_fato=datetime.date(2025, 1, 1),
        circunstancias_art59=_make_c59_dict(),
        confissao_espontanea=True,
    )
    res = calcular_dosimetria_global(
        crimes=[crime_input],
        config=DosimetriaConfig(aplicar_sumula_231_stj=True),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc = res.crimes_calculados[0]
    assert calc.pena_base.anos == 2
    assert calc.pena_intermediaria.anos == 2
    assert calc.pena_intermediaria.meses == 0
    assert calc.pena_intermediaria.dias == 0
    assert calc.sumula_231_aplicada is True

  def test_base_slightly_above_floor_fraction_truncated_at_floor(self):
    """Pena-base exasperated by 1 circumstance (+135 days = 855 days). Confissão (-1/6 = 142 days) would reach 713 days (<720). Truncated to 720 days."""
    crime_input = CrimeDosimetriaInput(
        crime_code="art_129_p13_cp",
        data_fato=datetime.date(2025, 1, 1),
        circunstancias_art59=_make_c59_dict(culpabilidade=True),
        confissao_espontanea=True,
    )
    res = calcular_dosimetria_global(
        crimes=[crime_input],
        config=DosimetriaConfig(aplicar_sumula_231_stj=True),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc = res.crimes_calculados[0]
    # Pena base = 2y 4m 15d (855 days)
    assert calc.pena_base.to_total_days() == 855
    # Mathematically: 855 - (855 // 6 = 142) = 713 days.
    # But statutory floor is 2 years (720 days). Truncated to exactly 720 days (2 years).
    assert calc.pena_intermediaria.to_total_days() == 720
    assert calc.pena_intermediaria.anos == 2
    assert calc.pena_intermediaria.meses == 0
    assert calc.sumula_231_aplicada is True

  def test_multiple_mitigating_circumstances_at_floor(self):
    """Both confissão and menoridade 21 with base at floor still stop at minimum."""
    crime_input = CrimeDosimetriaInput(
        crime_code="art_129_p13_cp",
        data_fato=datetime.date(2025, 1, 1),
        circunstancias_art59=_make_c59_dict(),
        confissao_espontanea=True,
        menoridade_relativa_ou_senilidade=True,
    )
    res = calcular_dosimetria_global(
        crimes=[crime_input],
        config=DosimetriaConfig(aplicar_sumula_231_stj=True),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc = res.crimes_calculados[0]
    assert calc.pena_intermediaria.anos == 2
    assert calc.pena_intermediaria.meses == 0
    assert calc.sumula_231_aplicada is True

  def test_sumula231_logged_in_memoria_e_alertas(self):
    """Súmula 231 citation is explicitly logged in memoria_calculo or alertas_juridicos."""
    crime_input = CrimeDosimetriaInput(
        crime_code="art_129_p13_cp",
        data_fato=datetime.date(2025, 1, 1),
        circunstancias_art59=_make_c59_dict(),
        confissao_espontanea=True,
    )
    res = calcular_dosimetria_global(
        crimes=[crime_input],
        config=DosimetriaConfig(aplicar_sumula_231_stj=True),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc = res.crimes_calculados[0]
    memoria_all = " ".join(calc.memoria_calculo + calc.alertas_juridicos).lower()
    assert "231" in memoria_all

  def test_sumula231_toggle_disabled_allows_subfloor(self):
    """When aplicar_sumula_231_stj=False (config corner), penalty is allowed to drop below floor."""
    crime_input = CrimeDosimetriaInput(
        crime_code="art_129_p13_cp",
        data_fato=datetime.date(2025, 1, 1),
        circunstancias_art59=_make_c59_dict(),
        confissao_espontanea=True,
    )
    res = calcular_dosimetria_global(
        crimes=[crime_input],
        config=DosimetriaConfig(aplicar_sumula_231_stj=False),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc = res.crimes_calculados[0]
    # 720 - (720 // 6 = 120) = 600 days (< 720)
    assert calc.pena_intermediaria.to_total_days() < 720
    assert calc.sumula_231_aplicada is False


# ==============================================================================
# 3. Súmula 444 STJ (Maus Antecedentes Blindagem)
# ==============================================================================
class TestSumula444STJAntecedentes:
  """Verifies that police inquiries and ongoing lawsuits cannot exasperate base penalty."""

  def test_inqueritos_e_processos_em_curso_neutral(self):
    """Antecedentes without trânsito em julgado anterior MUST NOT increase penalty."""
    c59 = _make_c59_dict()
    # Mark antecedentes with active inquiry (no trânsito)
    c59["antecedentes"] = CircunstanciaArt59(
        desfavoravel=True,  # user mistakenly flags it
        possui_transito_em_julgado_anterior=False,  # but no trânsito!
        fundamentacao="Possui 5 inquéritos policiais e 2 ações penais em andamento",
    )
    crime_input = CrimeDosimetriaInput(
        crime_code="art_129_p13_cp",
        data_fato=datetime.date(2025, 1, 1),
        circunstancias_art59=c59,
    )
    res = calcular_dosimetria_global(
        crimes=[crime_input],
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc = res.crimes_calculados[0]
    # Súmula 444 must neutralize this vector
    assert "antecedentes" not in calc.vetores_negativos_efetivos
    assert calc.pena_base.anos == 2
    assert calc.pena_base.meses == 0

  def test_maus_antecedentes_requires_transito_anterior(self):
    """Only convictions with trânsito em julgado anterior increase pena-base."""
    c59 = _make_c59_dict(antecedentes_transitado=True)
    crime_input = CrimeDosimetriaInput(
        crime_code="art_129_p13_cp",
        data_fato=datetime.date(2025, 1, 1),
        circunstancias_art59=c59,
    )
    res = calcular_dosimetria_global(
        crimes=[crime_input],
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc = res.crimes_calculados[0]
    assert "antecedentes" in calc.vetores_negativos_efetivos
    assert calc.pena_base.anos == 2
    assert calc.pena_base.meses == 4
    assert calc.pena_base.dias == 15

  def test_sumula444_alert_issued_in_calculation(self):
    """When ongoing inquiries are filtered out, system emits explicit legal alert."""
    c59 = _make_c59_dict()
    c59["antecedentes"] = CircunstanciaArt59(
        desfavoravel=True,
        possui_transito_em_julgado_anterior=False,
        fundamentacao="Inquérito 123/2023",
    )
    crime_input = CrimeDosimetriaInput(
        crime_code="art_129_p13_cp",
        data_fato=datetime.date(2025, 1, 1),
        circunstancias_art59=c59,
    )
    res = calcular_dosimetria_global(
        crimes=[crime_input],
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc = res.crimes_calculados[0]
    all_text = " ".join(calc.memoria_calculo + calc.alertas_juridicos).lower()
    assert "444" in all_text

  def test_reincidencia_vs_maus_antecedentes_segregation(self):
    """Clear separation: maus antecedentes in 1st phase vs reincidência in 2nd phase."""
    c59 = _make_c59_dict(antecedentes_transitado=True)
    crime_input = CrimeDosimetriaInput(
        crime_code="art_129_p13_cp",
        data_fato=datetime.date(2025, 1, 1),
        circunstancias_art59=c59,
        reincidente=True,
    )
    res = calcular_dosimetria_global(
        crimes=[crime_input],
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc = res.crimes_calculados[0]
    # 1st phase: 2y + 135d = 855d (2y 4m 15d)
    assert calc.pena_base.to_total_days() == 855
    # 2nd phase: 855 + (855 // 6 = 142) = 997d (2y 9m 7d)
    assert calc.pena_intermediaria.to_total_days() == 997


# ==============================================================================
# 4. Art. 61, II, 'f', CP (Bis in Idem Lock)
# ==============================================================================
class TestBisInIdemDomesticViolenceLock:
  """Verifies strict blocking of art. 61 II f on offenses with domestic violence elements."""

  def test_art129_p13_blocks_art61_ii_f(self):
    """Art. 129 § 13 CP: Attempt to add Art. 61 II f must be intercepted and blocked."""
    crime_input = CrimeDosimetriaInput(
        crime_code="art_129_p13_cp",
        data_fato=datetime.date(2025, 1, 1),
        circunstancias_art59=_make_c59_dict(),
        agravantes_art61_f_violencia_domestica=True,
    )
    res = calcular_dosimetria_global(
        crimes=[crime_input],
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc = res.crimes_calculados[0]
    assert calc.bis_in_idem_art61f_bloqueado is True
    assert (
        calc.pena_intermediaria.to_total_days()
        == calc.pena_base.to_total_days()
    )

  def test_art24a_lmp_blocks_art61_ii_f(self):
    """Art. 24-A LMP: Intercepts and blocks Art. 61 II f."""
    crime_input = CrimeDosimetriaInput(
        crime_code="art_24a_lmp",
        data_fato=datetime.date(2025, 1, 1),
        circunstancias_art59=_make_c59_dict(),
        agravantes_art61_f_violencia_domestica=True,
    )
    res = calcular_dosimetria_global(
        crimes=[crime_input],
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc = res.crimes_calculados[0]
    assert calc.bis_in_idem_art61f_bloqueado is True
    assert (
        calc.pena_intermediaria.to_total_days()
        == calc.pena_base.to_total_days()
    )

  def test_art147_p2_post14994_blocks_art61_ii_f(self):
    """Art. 147 § 2º (post-14.994) has domestic violence as elementar; blocks Art. 61 II f."""
    spec = get_penal_type_for_date("art_147_cp", datetime.date(2025, 1, 1))
    assert spec.has_builtin_domestic_violence_element is True

    crime_input = CrimeDosimetriaInput(
        crime_code="art_147_cp",
        data_fato=datetime.date(2025, 1, 1),
        circunstancias_art59=_make_c59_dict(),
        agravantes_art61_f_violencia_domestica=True,
    )
    res = calcular_dosimetria_global(
        crimes=[crime_input],
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc = res.crimes_calculados[0]
    assert calc.bis_in_idem_art61f_bloqueado is True
    assert (
        calc.pena_intermediaria.to_total_days()
        == calc.pena_base.to_total_days()
    )

  def test_art147_caput_pre14994_permits_art61_ii_f(self):
    """Art. 147 caput pre-14.994 is generic threat: Art. 61 II f IS applied (+1/6)."""
    spec = get_penal_type_for_date("art_147_cp", datetime.date(2024, 1, 1))
    assert spec.has_builtin_domestic_violence_element is False

    crime_input = CrimeDosimetriaInput(
        crime_code="art_147_cp",
        data_fato=datetime.date(2024, 1, 1),
        circunstancias_art59=_make_c59_dict(),
        agravantes_art61_f_violencia_domestica=True,
    )
    res = calcular_dosimetria_global(
        crimes=[crime_input],
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    calc = res.crimes_calculados[0]
    assert calc.bis_in_idem_art61f_bloqueado is False
    # Base = 1 month (30 days). +1/6 = +5 days -> 35 days (1m 5d)
    assert calc.pena_intermediaria.to_total_days() == 35


# ==============================================================================
# 5. Concurso Material Heterogêneo (Art. 69 CP)
# ==============================================================================
class TestConcursoMaterialHeterogeneo:
  """Verifies segregation of reclusão and detenção under Art. 69 CP."""

  def test_segregation_reclusao_detencao_pre_14994(self):
    """Concurso between Art. 129 § 13 (reclusão) and Art. 24-A (detenção pré-14.994)."""
    fact_date = datetime.date(2024, 1, 1)
    crime_lesao = CrimeDosimetriaInput(
        crime_code="art_129_p13_cp",
        data_fato=fact_date,
        circunstancias_art59=_make_c59_dict(),
    )
    crime_descump = CrimeDosimetriaInput(
        crime_code="art_24a_lmp",
        data_fato=fact_date,
        circunstancias_art59=_make_c59_dict(),
    )
    res = calcular_dosimetria_global(
        crimes=[crime_lesao, crime_descump],
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="material"),
    )
    # Must separate species in penas_por_especie
    assert "reclusao" in res.penas_por_especie
    assert "detencao" in res.penas_por_especie

    # Reclusão (Art. 129 § 13 pré): 1 ano
    assert res.penas_por_especie["reclusao"].anos == 1
    # Detenção (Art. 24-A LMP pré): 3 meses
    assert res.penas_por_especie["detencao"].meses == 3

    # Total unificada reflects sum of days (360 + 90 = 450 days = 1 ano e 3 meses)
    assert res.pena_total_unificada.anos == 1
    assert res.pena_total_unificada.meses == 3

  def test_homogeneo_reclusao_post_14994(self):
    """Post-14.994: Both Art. 129 § 13 and Art. 24-A are reclusão."""
    fact_date = datetime.date(2025, 1, 1)
    crime_lesao = CrimeDosimetriaInput(
        crime_code="art_129_p13_cp",
        data_fato=fact_date,
        circunstancias_art59=_make_c59_dict(),
    )
    crime_descump = CrimeDosimetriaInput(
        crime_code="art_24a_lmp",
        data_fato=fact_date,
        circunstancias_art59=_make_c59_dict(),
    )
    res = calcular_dosimetria_global(
        crimes=[crime_lesao, crime_descump],
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="material"),
    )
    assert "reclusao" in res.penas_por_especie
    assert "detencao" not in res.penas_por_especie or res.penas_por_especie["detencao"].to_total_days() == 0

    # 2y + 2y = 4y reclusão
    assert res.penas_por_especie["reclusao"].anos == 4
    assert res.pena_total_unificada.anos == 4

  def test_execution_order_alert_reclusao_first(self):
    """Art. 69 CP mandates that reclusão is executed before detenção; species remain strictly segregated."""
    fact_date = datetime.date(2024, 1, 1)
    crimes = [
        CrimeDosimetriaInput(
            crime_code="art_129_p13_cp",
            data_fato=fact_date,
            circunstancias_art59=_make_c59_dict(),
        ),
        CrimeDosimetriaInput(
            crime_code="art_24a_lmp",
            data_fato=fact_date,
            circunstancias_art59=_make_c59_dict(),
        ),
    ]
    res = calcular_dosimetria_global(
        crimes=crimes,
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="material"),
    )
    # Both species segregated and preserved
    assert "reclusao" in res.penas_por_especie
    assert "detencao" in res.penas_por_especie
    assert res.penas_por_especie["reclusao"].to_total_days() > 0
    assert res.penas_por_especie["detencao"].to_total_days() > 0
    # Reclusão is greater or equal in dignity, executed first
    assert res.penas_por_especie["reclusao"].anos >= 1


# ==============================================================================
# 6. Súmula 588 STJ & Súmula 536 STJ (Substitution & Despenalização)
# ==============================================================================
class TestSumulas588e536STJSubstitution:
  """Verifies prohibition of Art. 44 CP substitution and LMP despenalização."""

  def test_sumula588_violencia_blocks_art44_prd(self):
    """Art. 129 § 13 CP: Violence against woman blocks Art. 44 CP substitution."""
    crime = CrimeDosimetriaInput(
        crime_code="art_129_p13_cp",
        data_fato=datetime.date(2025, 1, 1),
        circunstancias_art59=_make_c59_dict(),
    )
    res = calcular_dosimetria_global(
        crimes=[crime],
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    assert res.substituicao_art44_cabivel is False
    assert (
        "588" in res.motivo_vedacao_substituicao
        or "violência" in res.motivo_vedacao_substituicao.lower()
    )

  def test_sumula588_grave_ameaca_blocks_art44_prd(self):
    """Art. 147 CP: Grave ameaça blocks Art. 44 CP substitution."""
    crime = CrimeDosimetriaInput(
        crime_code="art_147_cp",
        data_fato=datetime.date(2024, 1, 1),
        circunstancias_art59=_make_c59_dict(),
    )
    res = calcular_dosimetria_global(
        crimes=[crime],
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    assert res.substituicao_art44_cabivel is False
    assert (
        "588" in res.motivo_vedacao_substituicao
        or "ameaça" in res.motivo_vedacao_substituicao.lower()
    )

  def test_sursis_special_conditions_evaluated_when_prd_blocked(self):
    """Penalty <= 2 years with PRD prohibited qualifies for Sursis (Art. 77 CP)."""
    crime = CrimeDosimetriaInput(
        crime_code="art_129_p13_cp",
        data_fato=datetime.date(2025, 1, 1),
        circunstancias_art59=_make_c59_dict(),  # pena = 2 years
    )
    res = calcular_dosimetria_global(
        crimes=[crime],
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    assert res.sursis_art77_cabivel is True
    assert "77" in res.fundamentacao_sursis or "78" in res.fundamentacao_sursis

  def test_sursis_inapplicable_when_penalty_exceeds_two_years(self):
    """Penalty > 2 years is ineligible for Art. 77 CP Sursis."""
    crime = CrimeDosimetriaInput(
        crime_code="art_129_p13_cp",
        data_fato=datetime.date(2025, 1, 1),
        circunstancias_art59=_make_c59_dict(culpabilidade=True),  # pena = 2y 4m 15d
    )
    res = calcular_dosimetria_global(
        crimes=[crime],
        config=DosimetriaConfig(),
        concurso=ConcursoCrimesConfig(tipo_concurso="nenhum"),
    )
    assert res.sursis_art77_cabivel is False


# ==============================================================================
# 7. Prescrição da Pretensão Punitiva (Art. 109 CP & Art. 115 CP)
# ==============================================================================
class TestPrescricaoPretensaoPunitiva:
  """Verifies prescription calculator across all Art. 109 CP brackets and Art. 115 CP."""

  @pytest.mark.parametrize(
      "anos,meses,dias,expected_prescricao_anos",
      [
          (0, 6, 0, 3),   # < 1 ano -> 3 anos (Art. 109, VI)
          (1, 0, 0, 4),   # 1 a 2 anos -> 4 anos (Art. 109, V)
          (2, 0, 0, 4),   # até 2 anos -> 4 anos (Art. 109, V)
          (2, 6, 0, 8),   # > 2 a 4 anos -> 8 anos (Art. 109, IV)
          (4, 0, 0, 8),   # até 4 anos -> 8 anos (Art. 109, IV)
          (5, 0, 0, 12),  # > 4 a 8 anos -> 12 anos (Art. 109, III)
      ],
  )
  def test_prescricao_art109_brackets(
      self, anos, meses, dias, expected_prescricao_anos
  ):
    """Tests exact statutory brackets of Art. 109 CP."""
    pena = PenaAnosMesesDias(anos=anos, meses=meses, dias=dias)
    data_fato = datetime.date(2024, 1, 1)
    data_rec = datetime.date(2024, 6, 1)
    data_sent = datetime.date(2024, 12, 1)

    out = calcular_prescricao(
        pena_aplicada=pena,
        data_fato=data_fato,
        data_recebimento_denuncia=data_rec,
        data_sentenca=data_sent,
        reu_menor_21_ou_maior_70=False,
    )
    assert out.prazo_prescricional_anos == expected_prescricao_anos
    assert out.prescricao_reconhecida is False

  def test_prescricao_boundary_day_before_vs_day_after(self):
    """Pena 1 ano -> prescrição em 4 anos (1460 dias). Intercorrente 1459d (não prescrito) vs 1461d (prescrito)."""
    pena = PenaAnosMesesDias(anos=1, meses=0, dias=0)
    data_fato = datetime.date(2021, 1, 1)
    data_rec = datetime.date(2021, 2, 1)

    # 4 anos * 365 = 1460 dias
    data_sent_antes = data_rec + datetime.timedelta(days=1459)
    data_sent_depois = data_rec + datetime.timedelta(days=1461)

    out_nao = calcular_prescricao(
        pena_aplicada=pena,
        data_fato=data_fato,
        data_recebimento_denuncia=data_rec,
        data_sentenca=data_sent_antes,
    )
    assert out_nao.prescricao_reconhecida is False

    out_sim = calcular_prescricao(
        pena_aplicada=pena,
        data_fato=data_fato,
        data_recebimento_denuncia=data_rec,
        data_sentenca=data_sent_depois,
    )
    assert out_sim.prescricao_reconhecida is True

  def test_prescricao_art115_halving_for_young_or_elderly(self):
    """Art. 115 CP: Reu menor de 21 anos reduz prazo prescricional pela metade."""
    pena = PenaAnosMesesDias(anos=1, meses=0, dias=0)  # normally 4 years
    data_fato = datetime.date(2021, 1, 1)
    data_rec = datetime.date(2021, 2, 1)
    # Reduced by half: 1460 // 2 = 730 days. 735 days later -> Prescrito!
    data_sent = data_rec + datetime.timedelta(days=735)

    out = calcular_prescricao(
        pena_aplicada=pena,
        data_fato=data_fato,
        data_recebimento_denuncia=data_rec,
        data_sentenca=data_sent,
        reu_menor_21_ou_maior_70=True,
    )
    assert out.reduzido_metade_art115 is True
    assert out.prazo_prescricional_anos == 4
    assert out.prescricao_reconhecida is True
