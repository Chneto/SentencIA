# -*- coding: utf-8 -*-
"""Correções jurídicas (área "juridico"): dosimetria, catálogo penal, gerador e pseudonimização.

Cada teste prova a causa raiz de um achado confirmado em revisão adversarial (detração e regime,
atenuante da menoridade, prescrição em abstrato, inciso do art. 5º da LMP, concurso benéfico, multa no
crime continuado, capitulação com art. 61, II, f, majorantes dos arts. 141 e 147-A/218-C, tipo
inexistente na data do fato, Súmula 588, texto da 2ª fase, § 9º do art. 129 e pseudonimização).
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.case_context import obter_contexto
from app.core.errors import DadosInsuficientesError, DecisaoJudicialPendenteError
from app.generation import case_script_engine as cse
from app.generation.sentence_generator import (
    calcular_secoes_deterministicas,
    formatar_secao_dosimetria_completa,
    gerar_sentenca_estruturada,
)
from app.legal_engine.dosimetria_calculator import (
    ConcursoCrimesConfig,
    CrimeDosimetriaInput,
    gerar_vetores_art59_detalhados,
    calcular_dosimetria_crime,
    calcular_dosimetria_global,
    calcular_prescricao_por_crime,
)
from app.legal_engine.penal_types_catalog import (
    PenaAnosMesesDias,
    TipoPenalInexistenteNaDataError,
    get_penal_type_for_date,
)

D = datetime.date
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _cenario(n: int = 1) -> dict:
    nome = sorted(FIXTURES.glob(f"case_scenario_{n}_*.json"))[0]
    return json.loads(nome.read_text(encoding="utf-8"))


def _veredictos(bundle: dict, veredicto: str = "condenar", **extra) -> dict:
    crimes = obter_contexto(bundle).meta.crimes_imputados
    return {"crimes_config": [
        {"codigo": c, "veredicto": veredicto, **({"enquadramento_ameaca": "caput"} if c == "art_147_cp" else {}), **extra}
        for c in crimes
    ]}


# ==============================================================================
# 4. Detração e regime (art. 387, § 2º, do CPP)
# ==============================================================================
def test_detracao_entra_na_fixacao_do_regime_e_o_texto_acompanha():
    crimes = [CrimeDosimetriaInput(crime_code="art_158_cp", data_fato=D(2025, 1, 10), agravantes_art61_f_violencia_domestica=True)]
    sem = calcular_dosimetria_global(crimes)
    assert sem.pena_total_unificada == PenaAnosMesesDias(anos=4, meses=8)
    assert sem.regime_inicial == "semiaberto"

    from app.legal_engine.detracao import IntervaloCustodia, calcular_detracao

    periodo = IntervaloCustodia(
        inicio=datetime.date(2025, 1, 1), fim=datetime.date(2025, 9, 7),
        fonte_inicio="ID-PRISAO-FICTICIA", fonte_fim="ID-SOLTURA-FICTICIA",
    )
    com = calcular_dosimetria_global(crimes, detracao=calcular_detracao([periodo]))
    assert com.pena_remanescente_detracao == PenaAnosMesesDias(anos=3, meses=11, dias=20)
    assert com.regime_inicial == "aberto"  # regime calculado sobre a pena remanescente
    assert com.regime_sem_detracao == "semiaberto"
    assert com.pena_considerada_regime == com.pena_remanescente_detracao
    assert "250" in com.fundamentacao_regime and "SEMIABERTO" in com.fundamentacao_regime

    texto = formatar_secao_dosimetria_completa(com, "um_oitavo_intervalo")
    assert "ALTEROU o regime" in texto
    assert "sem implicar alteração do regime" not in texto
    assert "REGIME INICIAL ABERTO" in texto


def test_detracao_que_nao_muda_o_regime_diz_que_nao_mudou():
    crimes = [CrimeDosimetriaInput(crime_code="art_158_cp", data_fato=D(2025, 1, 10))]  # 4 anos -> aberto? (base 4a: <= 4)
    com = calcular_dosimetria_global(crimes, dias_prisao_cautelar=30)
    assert com.regime_sem_detracao == com.regime_inicial
    texto = formatar_secao_dosimetria_completa(com, "um_oitavo_intervalo")
    assert "não alterou o regime resultante" in texto


def test_detracao_acima_de_365_dias_nao_e_descartada():
    import app.generation.sentence_generator as sg

    peca_apf = SimpleNamespace(
        doc_id="ID-1", data_peca="2023-01-01", titulo="Auto de Prisão em Flagrante",
        categoria="BO", conteudo="Foi preso em flagrante em 01/01/2023",
    )
    peca_solt = SimpleNamespace(
        doc_id="ID-2", data_peca="2024-06-01", titulo="Certidão de cumprimento de alvará de soltura",
        categoria="CERTIDAO", conteudo="Cumprido o alvará de soltura em 01/06/2024 para a prisão do ID-1.",
    )
    dias, periodo = sg.inferir_detracao_cautelar([peca_apf, peca_solt])
    assert dias > 365
    assert "01/01/2023" in periodo and "01/06/2024" in periodo


def test_menoridade_relativa_entra_na_segunda_fase_do_fluxo_real():
    bundle = _cenario(1)
    bundle["metadados_override"] = {"data_nascimento_reu": "2005-06-01"}  # 19 anos em 20/01/2025
    ov = _veredictos(bundle)
    ov["crimes_config"][0]["vetores_art59"] = {"culpabilidade": {"desfavoravel": True, "fundamentacao": "Excesso concreto."}}
    res = calcular_secoes_deterministicas(bundle, ov)
    for c in res.dosimetria_global.crimes_calculados:
        assert any("menoridade" in a for a in c.atenuantes_ativas)
    crime_culp = res.dosimetria_global.crimes_calculados[0]
    assert crime_culp.pena_intermediaria.to_total_days() <= crime_culp.pena_base.to_total_days()
    assert res.prescricao.por_crime[0].reduzido_metade_art115 is True
    assert "menoridade relativa" in res.secoes["dosimetria"]

    # Sem data de nascimento nada é presumido.
    sem = calcular_secoes_deterministicas(_cenario(1), _veredictos(_cenario(1)))
    assert all(not c.atenuantes_ativas for c in sem.dosimetria_global.crimes_calculados)

    # Override por crime (False) prevalece.
    ov2 = _veredictos(bundle, menoridade_relativa_ou_senilidade=False)
    off = calcular_secoes_deterministicas(bundle, ov2)
    assert all(not c.atenuantes_ativas for c in off.dosimetria_global.crimes_calculados)


# ==============================================================================
# 7. Prescrição consumada: nada de condenação
# ==============================================================================
def test_prescricao_em_abstrato_consumada_bloqueia_condenacao_no_caso_real():
    bundle = _cenario(1)
    bundle["data_sentenca"] = "2040-01-01"
    with pytest.raises(DecisaoJudicialPendenteError) as exc:
        gerar_sentenca_estruturada(bundle, _veredictos(bundle), use_ollama_if_available=False)
    assert "art_147_cp" in exc.value.crimes_pendentes
    assert "extinguir" in exc.value.mensagem

    # Com veredicto 'extinguir' para os crimes prescritos, a sentença é gerada sem condenar o crime prescrito.
    ov = _veredictos(bundle, "extinguir")
    ov["crimes_config"] = [dict(c, fundamento_extincao_art107="IV") for c in ov["crimes_config"]]
    sent = gerar_sentenca_estruturada(bundle, ov, use_ollama_if_available=False)
    assert "EXTINGUIR A PUNIBILIDADE" in sent.secao_dispositivo
    assert "CONDENAR o réu" not in sent.secao_dispositivo


def test_simulacao_mantem_apenas_alerta_para_prescricao_em_abstrato():
    bundle = _cenario(1)
    bundle["data_sentenca"] = "2040-01-01"
    res = calcular_secoes_deterministicas(bundle, exigir_veredicto=False)
    assert any("Prescrição em abstrato consumada" in a for a in res.alertas)


def test_prescricao_so_pela_pena_em_concreto_nao_afirma_consumacao():
    dos = calcular_dosimetria_global([CrimeDosimetriaInput(crime_code="art_129_p13_cp", data_fato=D(2022, 1, 10))])  # 1 ano
    presc = calcular_prescricao_por_crime(dos, D(2022, 1, 10), D(2022, 3, 1), D(2026, 4, 1))  # 4 anos e 1 mês após o recebimento
    item = presc.por_crime[0]
    assert item.prescricao_retroativa_reconhecida is True and item.prescricao_abstrata_reconhecida is False
    assert "Consumou-se" not in item.fundamentacao
    assert "art. 110, § 1º" in item.fundamentacao and "trânsito em julgado para a acusação" in item.fundamentacao


# ==============================================================================
# 18. Inciso do art. 5º da LMP: nunca se presume relação íntima de afeto
# ==============================================================================
def test_inciso_do_art5_vem_dos_autos_e_nao_e_presumido():
    bundle = _cenario(1)
    bundle["metadados_override"] = {"vinculo_domestico": "Filho da ofendida, que agrediu sua genitora (art. 5º, II, da Lei nº 11.340/2006)"}
    sent = gerar_sentenca_estruturada(bundle, _veredictos(bundle), use_ollama_if_available=False)
    texto = sent.texto_completo
    assert "arts. 5º, inciso II, e 7º" in sent.secao_dispositivo
    assert "art. 5º, inciso III" not in texto
    assert "mantiveram vínculo afetivo" not in texto

    bundle3 = _cenario(1)
    bundle3["metadados_override"] = {"vinculo_domestico": "Ex-companheiro (união estável por cerca de 4 anos)"}
    sent3 = gerar_sentenca_estruturada(bundle3, _veredictos(bundle3), use_ollama_if_available=False)
    assert "arts. 5º, inciso III, e 7º" in sent3.secao_dispositivo
    assert "mantiveram vínculo afetivo" in sent3.texto_completo


def test_sem_vinculo_extraido_o_inciso_vira_marcador():
    from app.generation.sentence_generator import _art5_art7_txt, _inciso_art5_lmp

    assert _inciso_art5_lmp("") == ""
    assert _inciso_art5_lmp("Relação de vizinhança") == ""
    meta = SimpleNamespace(vinculo_domestico="")
    assert "[COMPLETAR PELO JUÍZO" in _art5_art7_txt(meta)
    assert "inciso III" not in _art5_art7_txt(meta)
    assert _inciso_art5_lmp("Ex-companheiro (união estável)") == "III"
    assert _inciso_art5_lmp("Filho da vítima") == "II"


# ==============================================================================
# 19. Concurso formal/continuado convertido em cúmulo material benéfico
# ==============================================================================
def test_concurso_formal_convertido_em_cumulo_benefico_preserva_o_tipo_reconhecido():
    crimes = [
        CrimeDosimetriaInput(crime_code="art_129_p13_cp", data_fato=D(2025, 1, 10)),  # 2a
        CrimeDosimetriaInput(crime_code="art_147_cp", data_fato=D(2025, 1, 10)),  # 2m
    ]
    dos = calcular_dosimetria_global(crimes, concurso=ConcursoCrimesConfig(tipo_concurso="formal"))
    assert dos.tipo_concurso == "material"  # soma (2a2m) < exasperação (2a4m)
    assert dos.concurso_material_beneficio is True
    assert dos.tipo_concurso_reconhecido == "formal"
    texto = formatar_secao_dosimetria_completa(dos, "um_oitavo_intervalo")
    assert "cúmulo material benéfico do art. 70, parágrafo único" in texto
    assert "pluralidade de condutas autônomas e desígnios autônomos" not in texto
    assert "Art. 69" not in texto


# ==============================================================================
# 20. Multa no crime continuado (art. 71, não art. 72)
# ==============================================================================
def test_crime_continuado_exaspera_a_multa_em_vez_de_somar():
    crimes = [CrimeDosimetriaInput(crime_code="art_24a_lmp", data_fato=D(2025, 1, 10), dias_multa=10, valor_unitario_multa="1/30") for _ in range(3)]
    dos = calcular_dosimetria_global(crimes, concurso=ConcursoCrimesConfig(tipo_concurso="continuado"))
    assert dos.tipo_concurso == "continuado" and dos.fracao_concurso == (1, 5)
    assert dos.total_dias_multa == 12  # 10 + 1/5
    assert any("art. 72" in a for a in dos.alertas_globais)
    assert "Art. 71 CP" in formatar_secao_dosimetria_completa(dos, "um_oitavo_intervalo")

    material = calcular_dosimetria_global(crimes, concurso=ConcursoCrimesConfig(tipo_concurso="material"))
    assert material.total_dias_multa == 30  # soma preservada no concurso material


# ==============================================================================
# 21. Capitulação com art. 61, II, f, somente quando aplicada
# ==============================================================================
@pytest.mark.parametrize("codigo", ["art_147_cp", "art_21_lcp"])
def test_capitulacao_so_cita_art61_f_quando_a_agravante_foi_aplicada(codigo):
    sem = calcular_dosimetria_crime(CrimeDosimetriaInput(crime_code=codigo, data_fato=D(2024, 3, 1)))
    assert sem.agravantes_ativas == []
    assert "61" not in sem.crime_spec.artigo_formatado
    com = calcular_dosimetria_crime(
        CrimeDosimetriaInput(crime_code=codigo, data_fato=D(2024, 3, 1), agravantes_art61_f_violencia_domestica=True)
    )
    assert len(com.agravantes_ativas) == 1
    assert "c/c art. 61, inciso II, alínea 'f'" in com.crime_spec.artigo_formatado
    assert "61" not in get_penal_type_for_date(codigo, D(2024, 3, 1)).artigo_formatado


# ==============================================================================
# 22. Bis in idem do art. 61, II, f, só com a majorante aplicada (147-A e 218-C)
# ==============================================================================
def test_agravante_61f_so_e_bloqueada_se_a_majorante_do_147a_for_aplicada():
    base = dict(crime_code="art_147a_cp", data_fato=D(2024, 3, 1), agravantes_art61_f_violencia_domestica=True)
    com_majorante = calcular_dosimetria_crime(CrimeDosimetriaInput(**base, aplicar_aumento_art147a_p1_ii=True))
    assert com_majorante.bis_in_idem_art61f_bloqueado is True and com_majorante.aumento_art147a_aplicado is True

    sem_majorante = calcular_dosimetria_crime(CrimeDosimetriaInput(**base, aplicar_aumento_art147a_p1_ii=False))
    assert sem_majorante.bis_in_idem_art61f_bloqueado is False
    assert len(sem_majorante.agravantes_ativas) == 1
    assert sem_majorante.pena_definitiva.to_total_days() == 180 + 30  # 6 meses + 1/6


def test_agravante_61f_no_218c_so_e_bloqueada_com_a_majorante_do_paragrafo_1():
    base = dict(crime_code="art_218c_cp", data_fato=D(2025, 3, 1), agravantes_art61_f_violencia_domestica=True)
    sem = calcular_dosimetria_crime(CrimeDosimetriaInput(**base))
    assert sem.bis_in_idem_art61f_bloqueado is False
    assert sem.pena_definitiva.to_total_days() == 360 + 60
    com = calcular_dosimetria_crime(CrimeDosimetriaInput(**base, fracao_aumento_terceira_fase=(1, 3)))
    assert com.bis_in_idem_art61f_bloqueado is True


# ==============================================================================
# 23. Art. 141, § 3º, do CP (Lei 14.994/2024) nos crimes contra a honra
# ==============================================================================
def test_majorante_do_art141_p3_dobra_pena_e_prazo_prescricional_em_abstrato():
    spec_pos = get_penal_type_for_date("art_140_cp", D(2025, 1, 10))
    spec_pre = get_penal_type_for_date("art_140_cp", D(2024, 9, 1))
    assert spec_pos.admite_aumento_art141_p3 is True and spec_pre.admite_aumento_art141_p3 is False

    padrao = calcular_dosimetria_crime(CrimeDosimetriaInput(crime_code="art_140_cp", data_fato=D(2025, 1, 10)))
    assert padrao.pena_definitiva == PenaAnosMesesDias(meses=1) and padrao.aumento_art141_p3_aplicado is False
    assert any("141, § 3º" in a and "NÃO aplicada" in a for a in padrao.alertas_juridicos)
    pre = calcular_dosimetria_crime(CrimeDosimetriaInput(crime_code="art_140_cp", data_fato=D(2024, 9, 1)))
    assert not any("141" in a for a in pre.alertas_juridicos)

    dobro = calcular_dosimetria_crime(
        CrimeDosimetriaInput(crime_code="art_140_cp", data_fato=D(2025, 1, 10), aplicar_aumento_art141_p3=True)
    )
    assert dobro.pena_definitiva == PenaAnosMesesDias(meses=2) and dobro.aumento_art141_p3_aplicado is True

    def _presc(crime_in):
        dos = calcular_dosimetria_global([crime_in])
        return calcular_prescricao_por_crime(dos, D(2025, 1, 10), D(2025, 2, 1), D(2028, 6, 1)).por_crime[0]

    sem_majorante = _presc(CrimeDosimetriaInput(crime_code="art_140_cp", data_fato=D(2025, 1, 10)))
    com_majorante = _presc(CrimeDosimetriaInput(crime_code="art_140_cp", data_fato=D(2025, 1, 10), aplicar_aumento_art141_p3=True))
    assert sem_majorante.prazo_abstrato_anos == 3 and sem_majorante.prescricao_abstrata_reconhecida is True
    assert com_majorante.pena_maxima_abstrata == PenaAnosMesesDias(anos=1)
    # Máximo em dobro (1 ano) => 4 anos em abstrato: não prescreve em 3 anos e 4 meses (a retroativa, pela pena
    # em concreto, é apenas projeção a verificar após o trânsito para a acusação).
    assert com_majorante.prazo_abstrato_anos == 4 and com_majorante.prescricao_abstrata_reconhecida is False


# ==============================================================================
# 24. Motivos neutros sem fato concreto inventado
# ==============================================================================
def test_motivos_neutros_nao_afirmam_fato_nao_provado():
    texto = gerar_vetores_art59_detalhados("art_163_cp")["motivos"].fundamentacao
    assert "inconformismo" not in texto.lower() and "posse" not in texto.lower() and "rompimento" not in texto.lower()
    assert "inerentes ao tipo penal" in texto and "neutra" in texto


# ==============================================================================
# 25. Fato anterior à lei que criou o tipo: conduta atípica
# ==============================================================================
@pytest.mark.parametrize(
    "codigo,data",
    [("art_147a_cp", D(2020, 5, 1)), ("art_147a_cp", D(2021, 3, 31)), ("art_147b_cp", D(2021, 7, 28))],
)
def test_fato_anterior_a_lei_que_criou_o_tipo_nao_gera_pena(codigo, data):
    with pytest.raises(TipoPenalInexistenteNaDataError) as exc:
        calcular_dosimetria_crime(CrimeDosimetriaInput(crime_code=codigo, data_fato=data))
    assert "absolver" in exc.value.mensagem and "art. 386, III" in exc.value.mensagem
    assert exc.value.campos == [f"crimes_config.{codigo}.veredicto"]
    with pytest.raises(TipoPenalInexistenteNaDataError):
        calcular_dosimetria_global([CrimeDosimetriaInput(crime_code=codigo, data_fato=data)])


def test_tipo_na_data_da_lei_e_o_catalogo_continuam_funcionando():
    assert calcular_dosimetria_crime(CrimeDosimetriaInput(crime_code="art_147a_cp", data_fato=D(2021, 4, 1))).pena_definitiva.to_total_days() > 0
    assert "ATENÇÃO" in get_penal_type_for_date("art_147a_cp", D(2021, 3, 1)).observacao_intertemporal
    # A absolvição do crime atípico não passa pela dosimetria.
    assert calcular_dosimetria_global([]).sem_condenacao is True


# ==============================================================================
# 26. Súmula 588/STJ depende da modalidade (com ou sem violência/grave ameaça)
# ==============================================================================
@pytest.mark.parametrize(
    "codigo,data",
    [("art_163_pu_cp", D(2025, 3, 1)), ("art_150_p1_cp", D(2025, 3, 1)), ("art_147a_cp", D(2025, 3, 1)), ("art_147b_cp", D(2025, 3, 1))],
)
def test_sumula_588_nao_e_afirmada_sem_decisao_sobre_a_modalidade(codigo, data):
    base = dict(crime_code=codigo, data_fato=data)
    if codigo == "art_147a_cp":
        base["aplicar_aumento_art147a_p1_ii"] = False
    pendente = calcular_dosimetria_global([CrimeDosimetriaInput(**base)])
    assert pendente.substituicao_art44_cabivel is False and pendente.substituicao_pendente_modalidade is True
    assert "[COMPLETAR PELO JUÍZO" in pendente.motivo_vedacao_substituicao
    assert "[COMPLETAR PELO JUÍZO" in pendente.fundamentacao_sursis and pendente.sursis_art77_cabivel is False
    texto = formatar_secao_dosimetria_completa(pendente, "um_oitavo_intervalo")
    assert "Reafirma-se a incidência cogente da Súmula 588" not in texto

    sem_violencia = calcular_dosimetria_global([CrimeDosimetriaInput(**base, violencia_ou_grave_ameaca=False)])
    assert sem_violencia.substituicao_art44_cabivel is True and sem_violencia.substituicao_pendente_modalidade is False

    com_violencia = calcular_dosimetria_global([CrimeDosimetriaInput(**base, violencia_ou_grave_ameaca=True)])
    assert com_violencia.substituicao_art44_cabivel is False and "Súmula 588" in com_violencia.motivo_vedacao_substituicao
    assert com_violencia.substituicao_pendente_modalidade is False


def test_tipos_sempre_violentos_continuam_vedando_a_substituicao():
    res = calcular_dosimetria_global([CrimeDosimetriaInput(crime_code="art_147_cp", data_fato=D(2024, 3, 1))])
    assert res.substituicao_art44_cabivel is False and "Súmula 588" in res.motivo_vedacao_substituicao
    assert res.substituicao_pendente_modalidade is False


# ==============================================================================
# 43. Texto da 2ª fase quando agravantes e atenuantes não se equivalem
# ==============================================================================
def test_segunda_fase_explica_o_saldo_em_vez_de_dizer_que_se_compensam():
    crime = CrimeDosimetriaInput(
        crime_code="art_147_cp",
        data_fato=D(2024, 1, 10),
        reincidente=True,
        agravantes_art61_f_violencia_domestica=True,
        menoridade_relativa_ou_senilidade=True,
    )
    dos = calcular_dosimetria_global([crime])
    c = dos.crimes_calculados[0]
    assert c.pena_base == PenaAnosMesesDias(meses=1)
    assert c.pena_intermediaria.to_total_days() == 30 + 5  # +1/6 pelo saldo de uma agravante
    texto = formatar_secao_dosimetria_completa(dos, "um_oitavo_intervalo")
    assert "remanescendo 1 agravante(s)" in texto and "elevo a pena em 1/6" in texto
    assert "que se compensam nos termos do art. 67" not in texto

    igual = calcular_dosimetria_global(
        [CrimeDosimetriaInput(crime_code="art_147_cp", data_fato=D(2024, 1, 10), reincidente=True, menoridade_relativa_ou_senilidade=True)]
    )
    assert "se compensam integralmente" in formatar_secao_dosimetria_completa(igual, "um_oitavo_intervalo")


# ==============================================================================
# 44. Art. 129, § 9º, nunca vira o § 13 em silêncio
# ==============================================================================
@pytest.mark.parametrize("dia", [9, 10, 11])
def test_art129_p9_mantem_capitulacao_e_observa_pena_da_lei_14994(dia):
    """Lei 14.994/2024, vigência 10/10/2024.
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2024/lei/l14994.htm
    """
    data = D(2024, 10, dia)
    spec = get_penal_type_for_date("art_129_p9_cp", data)
    assert spec.code == "art_129_p9_cp"
    assert "§ 9º" in spec.artigo_formatado and "§ 13" not in spec.artigo_formatado
    assert (spec.especie_pena, spec.min_pena, spec.max_pena) == (
        ("detencao", PenaAnosMesesDias(meses=3), PenaAnosMesesDias(anos=3))
        if dia < 10 else ("reclusao", PenaAnosMesesDias(anos=2), PenaAnosMesesDias(anos=5))
    )
    res = calcular_dosimetria_crime(CrimeDosimetriaInput(crime_code=spec.code, data_fato=data))
    assert res.pena_definitiva == spec.min_pena


@pytest.mark.parametrize("dia", [1, 2, 3])
def test_art129_p9_redacao_trabalho_domestico(dia):
    """Lei 15.455/2026, art. 4º, vigência 02/07/2026.
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2026/lei/l15455.htm
    """
    spec = get_penal_type_for_date("art_129_p9_cp", D(2026, 7, dia))
    assert ("trabalho doméstico" in spec.observacao_intertemporal) is (dia >= 2)
    assert spec.min_pena == PenaAnosMesesDias(anos=2)


@pytest.mark.parametrize("dia", [9, 10, 11])
def test_ameaca_caput_e_paragrafo_1_tem_acao_distinta(dia):
    """Lei 14.994/2024, art. 1º, vigência 10/10/2024.
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2024/lei/l14994.htm
    """
    data = D(2024, 10, dia)
    caput = get_penal_type_for_date("art_147_cp", data)
    assert caput.acao_penal == "publica_condicionada_representacao"
    assert caput.max_pena == PenaAnosMesesDias(meses=6)
    if dia == 9:
        with pytest.raises(DadosInsuficientesError):
            get_penal_type_for_date("art_147_cp", data, ameaca_qualificada=True)
    else:
        qualificada = get_penal_type_for_date("art_147_cp", data, ameaca_qualificada=True)
        assert qualificada.acao_penal == "publica_incondicionada"
        assert qualificada.max_pena == PenaAnosMesesDias(anos=1)


def test_veredicto_de_ameaca_exige_enquadramento_confirmado():
    """Lei 14.994/2024, art. 1º, vigência 10/10/2024.
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2024/lei/l14994.htm
    """
    bundle = _cenario(1)
    override = _veredictos(bundle)
    ameaca = next(c for c in override["crimes_config"] if c["codigo"] == "art_147_cp")
    del ameaca["enquadramento_ameaca"]
    with pytest.raises(DadosInsuficientesError, match="Confirme o enquadramento da ameaça"):
        calcular_secoes_deterministicas(bundle, override)

    ameaca["enquadramento_ameaca"] = "paragrafo_1"
    resultado = calcular_secoes_deterministicas(bundle, override)
    especial = next(c.crime_spec for c in resultado.dosimetria_global.crimes_calculados if c.crime_spec.code == "art_147_cp")
    assert especial.acao_penal == "publica_incondicionada"


@pytest.mark.parametrize("dia", [3, 4, 5])
def test_art24a_inexistente_antes_da_publicacao(dia):
    """Lei 13.641/2018, vigência 04/04/2018.
    https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13641.htm
    """
    entrada = CrimeDosimetriaInput(crime_code="art_24a_lmp", data_fato=D(2018, 4, dia))
    if dia == 3:
        with pytest.raises(DadosInsuficientesError):
            calcular_dosimetria_crime(entrada)
    else:
        resultado = calcular_dosimetria_crime(entrada)
        assert resultado.pena_definitiva == PenaAnosMesesDias(meses=3)


@pytest.mark.parametrize("dia", [7, 8, 9])
def test_art338a_inexistente_antes_da_publicacao(dia):
    """Lei 15.280/2025, arts. 1º e 7º, vigência 08/12/2025.
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2025/lei/l15280.htm
    """
    entrada = CrimeDosimetriaInput(crime_code="art_338a_cp", data_fato=D(2025, 12, dia))
    if dia == 7:
        with pytest.raises(DadosInsuficientesError):
            calcular_dosimetria_crime(entrada)
    else:
        resultado = calcular_dosimetria_crime(entrada)
        assert resultado.crime_spec.code == "art_338a_cp"
        assert resultado.crime_spec.max_pena == PenaAnosMesesDias(anos=5)
        assert resultado.pena_definitiva == PenaAnosMesesDias(anos=2)
        assert resultado.dias_multa == 0
        assert resultado.dias_multa_sugeridos > 0
        assert resultado.multa_pendente is True


@pytest.mark.parametrize("dia", [9, 10, 11])
def test_majorante_monitoracao_art24a_exige_reconhecimento_e_vigencia(dia):
    """Lei 15.383/2026, art. 2º, vigência 10/04/2026.
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2026/lei/l15383.htm
    """
    entrada = CrimeDosimetriaInput(crime_code="art_24a_lmp", data_fato=D(2026, 4, dia))
    assert calcular_dosimetria_crime(entrada).fracao_aumento_aplicada is None
    com_majorante = CrimeDosimetriaInput(
        crime_code=entrada.crime_code, data_fato=entrada.data_fato,
        majorante_art24a_p4={
            "hipotese": "area_exclusao", "fonte_id": "ID-MONITORAMENTO",
            "trecho": "Relatório fictício de violação de área monitorada.", "fracao": (1, 3),
        },
    )
    if dia == 9:
        with pytest.raises(DadosInsuficientesError):
            calcular_dosimetria_crime(com_majorante)
    else:
        ajustada = calcular_dosimetria_crime(com_majorante)
        assert ajustada.fracao_aumento_aplicada == (1, 3)
        assert ajustada.pena_definitiva == PenaAnosMesesDias(anos=2, meses=8)
        assert ajustada.majorante_art24a_p4.fonte_id == "ID-MONITORAMENTO"


def test_majorante_monitoracao_confere_trecho_no_documento_indicado():
    """Lei 15.383/2026, art. 2º, vigência 10/04/2026.
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2026/lei/l15383.htm
    """
    from types import SimpleNamespace
    from app.generation.sentence_generator import _majorante_art24a_p4

    peca = SimpleNamespace(doc_id="ID-MONITORAMENTO", conteudo="Relatório fictício de violação de área monitorada.")
    campo = {
        "hipotese": "area_exclusao", "fonte_id": peca.doc_id,
        "fracao": "1/2", "trecho": peca.conteudo,
    }
    assert _majorante_art24a_p4({"majorante_art24a_p4": campo}, "art_24a_lmp", [peca]).fracao == (1, 2)
    campo["trecho"] = "A afirmação não consta da peça."
    with pytest.raises(DadosInsuficientesError, match="trecho da peça indicada"):
        _majorante_art24a_p4({"majorante_art24a_p4": campo}, "art_24a_lmp", [peca])


# ==============================================================================
# 3. Pseudonimização: Maria da Penha, filiação, CPF e CNJ sem pontuação
# ==============================================================================
_SCRIPT_VAZAMENTO = (
    "A vítima Maria da Penha Souza Lima, filha de Joana Batista Lima e Pedro Souza Lima, CPF "
    + "12345"
    + "678900, "
    "registrou ocorrência nos Autos 08012457820258205001 (processo 0801245-78.2025.8.20.5001). "
    "A Sra. Maria da Penha confirmou os fatos praticados por Carlos Alberto Ferreira. "
    "A Lei Maria da Penha (Lei nº 11.340/2006) aplica-se ao caso."
)


def _meta_vazamento():
    return SimpleNamespace(
        nome_ofendida="Maria da Penha Souza Lima",
        nome_reu="Carlos Alberto Ferreira",
        numero_processo="0801245-78.2025.8.20.5001",
        autos_mpu_apenso="",
    )


def test_pseudonimizacao_protege_nome_com_maria_da_penha_filiacao_cpf_e_cnj_planos():
    mapa = cse.construir_mapa_pseudonimizacao(_meta_vazamento(), _SCRIPT_VAZAMENTO)
    assert mapa["[OFENDIDA]"] == "Maria da Penha Souza Lima"
    assert "12345678900" in mapa.values()
    assert any(v == "Joana Batista Lima" for v in mapa.values())
    assert any(v == "Pedro Souza Lima" for v in mapa.values())

    saida = cse.pseudonimizar_texto(_SCRIPT_VAZAMENTO, mapa)
    for real in ("Souza Lima", "Joana", "Pedro", "12345678900", "08012457820258205001", "0801245-78.2025.8.20.5001", "Carlos"):
        assert real not in saida, f"'{real}' vazou: {saida}"
    assert "Sra. [OFENDIDA]" in saida  # forma curta "Maria da Penha" do nome real é mapeada
    assert "Lei Maria da Penha" in saida  # a lei pública é preservada
    assert cse.detectar_vazamento_pseudonimizacao(saida, mapa) == []


def test_verificacao_final_detecta_valor_do_mapa_ainda_em_claro():
    mapa = cse.construir_mapa_pseudonimizacao(_meta_vazamento(), _SCRIPT_VAZAMENTO)
    assert "[OFENDIDA]" in cse.detectar_vazamento_pseudonimizacao("Relato de MARIA DA PENHA SOUZA LIMA.", mapa)
    assert "[PROCESSO]" in cse.detectar_vazamento_pseudonimizacao("Autos 08012457820258205001.", mapa)
    assert cse.detectar_vazamento_pseudonimizacao("Lei Maria da Penha e [OFENDIDA].", mapa) == []
    erro = cse.PseudonimizacaoIncompletaError(["[OFENDIDA]"])
    assert isinstance(erro, PermissionError) and "[OFENDIDA]" in str(erro)
