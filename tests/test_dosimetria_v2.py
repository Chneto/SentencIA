"""Testes da v2.0.0 do motor de dosimetria (Agente A).

Cobrem: prescrição por crime (art. 119 CP / Súmula 497 STF), art. 110, § 1º (Lei 12.234/2010),
prescrição em abstrato, art. 115, contagem pelo calendário, regime para detenção, Tema 585/STJ,
sursis (art. 77, II), multa não parametrizada, dano moral sem valor, fração do concurso e a
majorante do art. 147-A, § 1º, II.
"""

import datetime

import pytest

from app.legal_engine.dosimetria_calculator import (
    CircunstanciaArt59,
    ConcursoCrimesConfig,
    CrimeDosimetriaInput,
    DosimetriaConfig,
    PrescricaoGlobalOutput,
    calcular_dosimetria_crime,
    calcular_dosimetria_global,
    calcular_prescricao,
    calcular_prescricao_por_crime,
    calcular_regime_art33,
    prazo_prescricional_art109_anos,
    somar_meses,
    sugerir_fracao_concurso,
)
from app.legal_engine.penal_types_catalog import PenaAnosMesesDias, get_penal_type_for_date

D = datetime.date

VETORES_NEGATIVOS_ART59 = {
    "culpabilidade": CircunstanciaArt59(desfavoravel=True),
    "antecedentes": CircunstanciaArt59(desfavoravel=True, possui_transito_em_julgado_anterior=True),
    "conduta_social": CircunstanciaArt59(desfavoravel=True),
    "personalidade": CircunstanciaArt59(desfavoravel=True),
    "motivos": CircunstanciaArt59(desfavoravel=True),
    "circunstancias": CircunstanciaArt59(desfavoravel=True),
    "consequencias": CircunstanciaArt59(desfavoravel=True),
}


# ==============================================================================
# Prescrição
# ==============================================================================
def test_prescricao_por_crime_um_prescrito_e_outro_nao():
    """Art. 119 CP: cada crime prescreve isoladamente; a pena unificada não pode ser usada."""
    fato = D(2022, 1, 10)
    dos = calcular_dosimetria_global(
        [
            CrimeDosimetriaInput(crime_code="art_147_cp", data_fato=fato),  # 1 mês -> 3 anos
            CrimeDosimetriaInput(crime_code="art_129_p13_cp", data_fato=fato),  # 1 ano -> 4 anos
        ],
        concurso=ConcursoCrimesConfig(tipo_concurso="material"),
    )
    assert dos.crimes_calculados[0].pena_definitiva == PenaAnosMesesDias(meses=1)
    assert dos.crimes_calculados[1].pena_definitiva == PenaAnosMesesDias(anos=1)

    rec = D(2022, 3, 1)
    sent = D(2025, 6, 1)  # 3 anos e 3 meses após o recebimento
    presc = calcular_prescricao_por_crime(dos, data_fato=fato, data_recebimento_denuncia=rec, data_sentenca=sent)

    assert isinstance(presc, PrescricaoGlobalOutput)
    assert len(presc.por_crime) == 2
    por_codigo = {p.crime_code: p for p in presc.por_crime}
    assert por_codigo["art_147_cp"].prazo_concreto_anos == 3
    assert por_codigo["art_147_cp"].prescricao_reconhecida is True
    assert por_codigo["art_129_p13_cp"].prazo_concreto_anos == 4
    assert por_codigo["art_129_p13_cp"].prescricao_reconhecida is False
    assert presc.prescricao_reconhecida is True
    assert presc.crimes_prescritos == ["art_147_cp"]
    assert "art. 119" in presc.fundamentacao
    assert any("119" in a for a in presc.alertas)

    # Pela pena total unificada (1 ano e 1 mês -> 4 anos) o crime de ameaça não "prescreveria":
    legado_total = calcular_prescricao(dos.pena_total_unificada, fato, rec, sent)
    assert legado_total.prescricao_reconhecida is False


def test_fato_posterior_lei_12234_nao_conta_lapso_fato_recebimento_na_retroativa():
    """Fato a partir de 06/05/2010: art. 110, § 1º, veda termo inicial anterior à denúncia."""
    out = calcular_prescricao(
        pena_aplicada=PenaAnosMesesDias(anos=1),  # prazo 4 anos
        data_fato=D(2015, 1, 1),
        data_recebimento_denuncia=D(2019, 6, 1),  # 4 anos e 5 meses após o fato
        data_sentenca=D(2020, 1, 1),
    )
    assert out.lei_12234_aplicavel is True
    assert out.prescricao_retroativa_reconhecida is False
    assert out.prescricao_reconhecida is False
    assert "06/05/2010" in out.fundamentacao

    # Mesma situação pelo caminho por crime (art. 129, § 9º — fato de 2015, pena mínima de 3 meses = prazo 3 anos)
    dos = calcular_dosimetria_global([CrimeDosimetriaInput(crime_code="art_129_p9_cp", data_fato=D(2015, 1, 1))])
    presc = calcular_prescricao_por_crime(dos, D(2015, 1, 1), D(2018, 6, 1), D(2018, 12, 1))
    item = presc.por_crime[0]
    assert item.lapsos_considerados_retroativa == ["recebimento da denúncia → sentença"]
    # 3 anos e 5 meses entre fato e recebimento: superaria o prazo concreto (3 anos), mas não conta;
    # e o máximo em abstrato (3 anos -> 8 anos) também não se consumou.
    assert item.prescricao_reconhecida is False


def test_fato_anterior_lei_12234_conta_lapso_fato_recebimento():
    out = calcular_prescricao(
        pena_aplicada=PenaAnosMesesDias(anos=1),  # prazo 4 anos
        data_fato=D(2009, 1, 1),
        data_recebimento_denuncia=D(2013, 6, 1),  # 4 anos e 5 meses após o fato
        data_sentenca=D(2014, 1, 1),
    )
    assert out.lei_12234_aplicavel is False
    assert out.prescricao_retroativa_reconhecida is True
    assert out.prescricao_reconhecida is True
    assert "anterior à Lei nº 12.234/2010" in out.fundamentacao


def test_prescricao_em_abstrato_pela_pena_maxima_no_lapso_fato_recebimento():
    """Mesmo com fato pós-2010, o lapso fato -> recebimento é verificado pela pena máxima (art. 109)."""
    fato = D(2019, 1, 10)
    dos = calcular_dosimetria_global([CrimeDosimetriaInput(crime_code="art_147_cp", data_fato=fato)])  # máx. 6 meses -> 3 anos
    presc = calcular_prescricao_por_crime(dos, fato, D(2022, 2, 1), D(2022, 6, 1))
    item = presc.por_crime[0]
    assert item.prazo_abstrato_anos == 3
    assert item.pena_maxima_abstrata == PenaAnosMesesDias(meses=6)
    assert item.prescricao_abstrata_reconhecida is True
    assert item.prescricao_retroativa_reconhecida is False
    assert presc.prescricao_reconhecida is True

    # Legado: a verificação em abstrato exige a pena máxima informada
    legado = calcular_prescricao(
        PenaAnosMesesDias(meses=1), fato, D(2022, 2, 1), D(2022, 6, 1), pena_maxima_abstrata=PenaAnosMesesDias(meses=6)
    )
    assert legado.prescricao_abstrata_reconhecida is True
    assert legado.prescricao_reconhecida is True


def test_prescricao_em_abstrato_considera_majorante_aplicada_147a():
    """Máximo do art. 147-A (2 anos) majorado de 1/2 = 3 anos -> 8 anos em abstrato (não 4)."""
    dos = calcular_dosimetria_global([CrimeDosimetriaInput(crime_code="art_147a_cp", data_fato=D(2023, 1, 1))])
    presc = calcular_prescricao_por_crime(dos, D(2023, 1, 1), D(2023, 3, 1), D(2023, 9, 1))
    assert presc.por_crime[0].pena_maxima_abstrata == PenaAnosMesesDias(anos=3)
    assert presc.por_crime[0].prazo_abstrato_anos == 8


def test_art115_reduz_metade_menor_21_e_maior_70():
    fato = D(2019, 6, 1)
    dos = calcular_dosimetria_global([CrimeDosimetriaInput(crime_code="art_129_p13_cp", data_fato=D(2022, 1, 1))])  # 1 ano -> 4 anos
    rec = D(2022, 3, 1)
    sent = D(2024, 3, 1)  # exatamente 2 anos depois

    sem = calcular_prescricao_por_crime(dos, fato, rec, sent)
    assert sem.prescricao_reconhecida is False

    menor = calcular_prescricao_por_crime(dos, fato, rec, sent, reu_menor_21_na_data_fato=True)
    assert menor.por_crime[0].reduzido_metade_art115 is True
    assert menor.por_crime[0].prazo_concreto_meses_efetivo == 24
    assert menor.prescricao_reconhecida is True
    assert any("115" in a for a in menor.alertas)

    idoso = calcular_prescricao_por_crime(dos, fato, rec, sent, reu_maior_70_na_sentenca=True)
    assert idoso.prescricao_reconhecida is True

    # Um dia antes do aniversário de 2 anos ainda não se consumou (art. 10 do CP)
    vespera = calcular_prescricao_por_crime(dos, fato, rec, D(2024, 2, 29), reu_menor_21_na_data_fato=True)
    assert vespera.prescricao_reconhecida is False


def test_prescricao_contada_pelo_calendario_e_nao_por_365_dias():
    """2021-02-01 + 1460 dias = 2025-01-31 (ano bissexto no meio): 4 anos ainda não se completaram."""
    assert somar_meses(D(2021, 2, 1), 48) == D(2025, 2, 1)
    assert somar_meses(D(2020, 2, 29), 12) == D(2021, 2, 28)
    rec = D(2021, 2, 1)
    out = calcular_prescricao(PenaAnosMesesDias(anos=1), D(2021, 1, 1), rec, rec + datetime.timedelta(days=1460))
    assert out.prescricao_reconhecida is False
    out2 = calcular_prescricao(PenaAnosMesesDias(anos=1), D(2021, 1, 1), rec, D(2025, 2, 1))
    assert out2.prescricao_reconhecida is True


def test_art109_vi_tres_anos_so_para_fatos_apos_lei_12234():
    pena = PenaAnosMesesDias(meses=6)
    assert prazo_prescricional_art109_anos(pena, D(2010, 5, 6)) == 3
    assert prazo_prescricional_art109_anos(pena, D(2010, 5, 5)) == 2
    assert prazo_prescricional_art109_anos(PenaAnosMesesDias(anos=12, dias=1)) == 20
    assert prazo_prescricional_art109_anos(PenaAnosMesesDias(anos=12)) == 16


def test_prescricao_sem_data_de_recebimento_nao_e_reconhecida():
    dos = calcular_dosimetria_global([CrimeDosimetriaInput(crime_code="art_147_cp", data_fato=D(2015, 1, 1))])
    presc = calcular_prescricao_por_crime(dos, D(2015, 1, 1), None, D(2025, 1, 1))
    assert presc.prescricao_reconhecida is False
    assert presc.por_crime[0].verificacao_completa is False
    assert any("recebimento da denúncia não informada" in a for a in presc.alertas)


def test_prescricao_sem_condenacao_prejudicada():
    dos = calcular_dosimetria_global([])
    presc = calcular_prescricao_por_crime(dos, D(2020, 1, 1), D(2020, 6, 1), D(2026, 1, 1))
    assert presc.por_crime == []
    assert presc.prescricao_reconhecida is False
    assert "Prejudicada" in presc.fundamentacao


def test_concurso_formal_prescricao_pela_pena_de_cada_crime_sumula_497():
    """Concurso formal: o acréscimo de 1/6 não entra no cálculo prescricional (art. 119 / Súmula 497 STF)."""
    fato = D(2021, 1, 10)
    circs = {k: v for k, v in VETORES_NEGATIVOS_ART59.items()}
    crimes = [
        CrimeDosimetriaInput(crime_code="art_146_cp", data_fato=fato, circunstancias_art59=circs),
        CrimeDosimetriaInput(crime_code="art_146_cp", data_fato=fato, circunstancias_art59=circs),
    ]
    dos = calcular_dosimetria_global(crimes, concurso=ConcursoCrimesConfig(tipo_concurso="formal"))
    pena_crime = dos.crimes_calculados[0].pena_definitiva.to_total_days()
    assert pena_crime < 360  # cada crime: inferior a 1 ano -> 3 anos
    assert dos.tipo_concurso == "formal"
    assert dos.fracao_concurso == (1, 6)
    assert dos.pena_total_unificada.to_total_days() >= 360  # exasperada: >= 1 ano -> seria 4 anos

    rec = D(2021, 3, 1)
    sent = D(2024, 5, 1)  # 3 anos e 2 meses
    presc = calcular_prescricao_por_crime(dos, fato, rec, sent)
    assert all(p.pena_concreta.to_total_days() == pena_crime for p in presc.por_crime)
    assert all(p.prazo_concreto_anos == 3 for p in presc.por_crime)
    assert presc.prescricao_reconhecida is True
    assert len(presc.crimes_prescritos) == 2
    assert any("497" in a for a in presc.alertas)
    assert "Súmula 497/STF" in presc.fundamentacao
    # Pela pena exasperada, a conclusão (indevida) seria outra
    assert calcular_prescricao(dos.pena_total_unificada, fato, rec, sent).prescricao_reconhecida is False


# ==============================================================================
# Concurso: fração sugerida pelo número de crimes
# ==============================================================================
def test_fracao_concurso_sugerida_pelo_numero_de_crimes_e_juiz_sobrescreve():
    assert sugerir_fracao_concurso("formal", 2) == (1, 6)
    assert sugerir_fracao_concurso("formal", 3) == (1, 5)
    assert sugerir_fracao_concurso("formal", 5) == (1, 3)
    assert sugerir_fracao_concurso("formal", 9) == (1, 2)
    assert sugerir_fracao_concurso("continuado", 7) == (2, 3)
    assert sugerir_fracao_concurso("material", 3) is None

    fato = D(2025, 1, 10)
    tres = [CrimeDosimetriaInput(crime_code="art_24a_lmp", data_fato=fato) for _ in range(3)]
    auto = calcular_dosimetria_global(tres, concurso=ConcursoCrimesConfig(tipo_concurso="formal"))
    assert auto.fracao_concurso_sugerida == (1, 5)
    assert auto.fracao_concurso == (1, 5)
    assert auto.pena_total_unificada.to_total_days() == 720 + 720 // 5
    assert any("sugerida pelo sistema" in a for a in auto.alertas_globais)

    juiz = calcular_dosimetria_global(tres, concurso=ConcursoCrimesConfig(tipo_concurso="formal", fracao_exasperacao=(1, 4)))
    assert juiz.fracao_concurso == (1, 4)
    assert juiz.fracao_concurso_sugerida == (1, 5)
    assert any("difere da sugerida" in a for a in juiz.alertas_globais)


# ==============================================================================
# Regime inicial
# ==============================================================================
def test_regime_pena_so_de_detencao_acima_de_8_anos_nunca_fechado():
    fato = D(2020, 1, 1)  # art. 129, § 9º (detenção de 3 meses a 3 anos)
    crimes = [
        CrimeDosimetriaInput(crime_code="art_129_p9_cp", data_fato=fato, circunstancias_art59=dict(VETORES_NEGATIVOS_ART59), reincidente=True)
        for _ in range(3)
    ]
    dos = calcular_dosimetria_global(crimes, concurso=ConcursoCrimesConfig(tipo_concurso="material"))
    assert set(dos.penas_por_especie) == {"detencao"}
    assert dos.pena_total_unificada.to_total_days() > 8 * 360
    assert dos.regime_inicial == "semiaberto"
    assert dos.regime_por_especie == {"detencao": "semiaberto"}
    assert "não pode ser o fechado" in dos.fundamentacao_regime

    assert calcular_regime_art33(9.0, False, False, "detencao")[0] == "semiaberto"
    assert calcular_regime_art33(9.0, False, False, "prisao_simples")[0] == "semiaberto"
    assert calcular_regime_art33(9.0, False, False, "reclusao")[0] == "fechado"


def test_regime_por_especie_em_penas_mistas_documenta_regra():
    fato = D(2024, 1, 1)
    dos = calcular_dosimetria_global(
        [
            CrimeDosimetriaInput(crime_code="art_129_p13_cp", data_fato=fato, reincidente=True, circunstancias_art59={"culpabilidade": CircunstanciaArt59(desfavoravel=True)}),
            CrimeDosimetriaInput(crime_code="art_147_cp", data_fato=fato, reincidente=True),
        ],
        concurso=ConcursoCrimesConfig(tipo_concurso="material"),
    )
    # Reincidente, pena <= 4 anos e circunstância desfavorável: reclusão no fechado (Súmula 269/STJ a contrario),
    # detenção limitada ao semiaberto.
    assert dos.regime_por_especie["reclusao"] == "fechado"
    assert dos.regime_por_especie["detencao"] == "semiaberto"
    assert dos.regime_inicial == "fechado"
    assert "art. 111 da LEP" in dos.fundamentacao_regime
    assert any("espécies distintas" in a for a in dos.alertas_globais)


def test_regime_sumula_269_reincidente_com_circunstancias_favoraveis():
    assert calcular_regime_art33(3.0, True, False, "reclusao")[0] == "semiaberto"
    assert calcular_regime_art33(3.0, False, False, "reclusao")[0] == "aberto"
    assert calcular_regime_art33(5.0, True, False, "reclusao")[0] == "fechado"
    assert calcular_regime_art33(5.0, False, False, "reclusao")[0] == "semiaberto"


# ==============================================================================
# 2ª fase: Tema 585/STJ somente para reincidência x confissão
# ==============================================================================
def test_tema_585_citado_somente_na_compensacao_reincidencia_confissao():
    # Confissão x agravante do art. 61, II, 'f' (ameaça anterior à Lei 14.994/2024): art. 67, sem Tema 585
    res = calcular_dosimetria_crime(
        CrimeDosimetriaInput(
            crime_code="art_147_cp",
            data_fato=D(2024, 1, 1),
            agravantes_art61_f_violencia_domestica=True,
            confissao_espontanea=True,
        )
    )
    alertas = " ".join(res.alertas_juridicos)
    assert "585" not in alertas
    assert "art. 67" in alertas
    assert res.compensacao_confissao_reincidencia is False

    # Reincidência x confissão: Tema 585
    res2 = calcular_dosimetria_crime(
        CrimeDosimetriaInput(crime_code="art_24a_lmp", data_fato=D(2025, 1, 1), reincidente=True, confissao_espontanea=True)
    )
    assert res2.compensacao_confissao_reincidencia is True
    assert any("Tema Repetitivo 585/STJ" in a for a in res2.alertas_juridicos)


# ==============================================================================
# Sursis (art. 77, II) e substituição (art. 44, III)
# ==============================================================================
def test_sursis_negado_com_vetor_negativo_do_art77_ii():
    entrada = CrimeDosimetriaInput(
        crime_code="art_147_cp",
        data_fato=D(2024, 1, 1),
        circunstancias_art59={"culpabilidade": CircunstanciaArt59(desfavoravel=True, fundamentacao="ameaça com faca")},
    )
    dos = calcular_dosimetria_global([entrada])
    assert dos.pena_total_unificada.to_total_days() <= 720
    assert dos.substituicao_art44_cabivel is False
    assert dos.sursis_art77_cabivel is False
    assert dos.sursis_negado_por_circunstancias_judiciais is True
    assert dos.circunstancias_desfavoraveis_art77_ii == ["culpabilidade"]
    assert "art. 77, II" in dos.fundamentacao_sursis
    assert "circunstâncias judiciais favoráveis" not in dos.fundamentacao_sursis
    assert dos.sursis_condicoes_legais == []
    assert any("Sursis" in a and "77, II" in a for a in dos.alertas_globais)


def test_sursis_concedido_quando_vetor_negativo_nao_integra_art77_ii():
    entrada = CrimeDosimetriaInput(
        crime_code="art_147_cp",
        data_fato=D(2024, 1, 1),
        circunstancias_art59={"consequencias": CircunstanciaArt59(desfavoravel=True)},
    )
    dos = calcular_dosimetria_global([entrada])
    assert dos.sursis_art77_cabivel is True
    assert dos.sursis_negado_por_circunstancias_judiciais is False
    assert dos.sursis_condicoes_legais


def test_substituicao_art44_nao_automatica_com_vetor_negativo():
    entrada = CrimeDosimetriaInput(
        crime_code="art_24a_lmp",
        data_fato=D(2024, 1, 1),  # detenção, sem violência
        circunstancias_art59={"motivos": CircunstanciaArt59(desfavoravel=True)},
    )
    dos = calcular_dosimetria_global([entrada])
    assert dos.substituicao_art44_cabivel is False
    assert "art. 44, III" in dos.motivo_vedacao_substituicao

    sem_vetor = calcular_dosimetria_global([CrimeDosimetriaInput(crime_code="art_24a_lmp", data_fato=D(2024, 1, 1))])
    assert sem_vetor.substituicao_art44_cabivel is True


# ==============================================================================
# Dano moral (Tema 983/STJ)
# ==============================================================================
def test_dano_moral_pedido_sem_valor_zero_e_alerta_de_arbitramento_pendente():
    crimes = [CrimeDosimetriaInput(crime_code="art_129_p13_cp", data_fato=D(2025, 1, 1))]
    dos = calcular_dosimetria_global(crimes, pedido_expresso_dano_moral_mp=True, valor_dano_moral_sugerido=0.0)
    assert dos.indenizacao_dano_moral_tema983 == 0.0
    assert dos.dano_moral_pedido_mp is True
    assert dos.dano_moral_arbitramento_pendente is True
    assert any("pedido sem valor — arbitramento pendente" in a for a in dos.alertas_globais)
    assert not any("R$ 0.00" in a or "R$ 0,00" in a for a in dos.alertas_globais)

    dos_none = calcular_dosimetria_global(crimes, pedido_expresso_dano_moral_mp=True, valor_dano_moral_sugerido=None)
    assert dos_none.indenizacao_dano_moral_tema983 == 0.0
    assert dos_none.dano_moral_arbitramento_pendente is True


def test_dano_moral_com_valor_e_sem_pedido():
    crimes = [CrimeDosimetriaInput(crime_code="art_129_p13_cp", data_fato=D(2025, 1, 1))]
    com_valor = calcular_dosimetria_global(crimes, pedido_expresso_dano_moral_mp=True, valor_dano_moral_sugerido=2500.0)
    assert com_valor.indenizacao_dano_moral_tema983 == 2500.0
    assert com_valor.dano_moral_arbitramento_pendente is False

    sem_pedido = calcular_dosimetria_global(crimes, pedido_expresso_dano_moral_mp=False, valor_dano_moral_sugerido=2500.0)
    assert sem_pedido.indenizacao_dano_moral_tema983 == 0.0
    assert sem_pedido.dano_moral_arbitramento_pendente is False
    assert any("pedido expresso" in a for a in sem_pedido.alertas_globais)

    # Sem argumentos: nenhum valor é presumido
    padrao = calcular_dosimetria_global(crimes)
    assert padrao.indenizacao_dano_moral_tema983 == 0.0


# ==============================================================================
# Multa e majorante do art. 147-A
# ==============================================================================
def test_multa_cumulativa_nao_parametrizada_gera_sugestao_com_alerta():
    res = calcular_dosimetria_crime(CrimeDosimetriaInput(crime_code="art_24a_lmp", data_fato=D(2025, 1, 1)))
    assert res.dias_multa == 10  # pena no mínimo => mínimo legal
    assert res.dias_multa_sugeridos_pelo_motor is True
    assert any("Multa cumulativa" in a and "não parametrizada" in a for a in res.alertas_juridicos)

    exasperada = calcular_dosimetria_crime(
        CrimeDosimetriaInput(crime_code="art_24a_lmp", data_fato=D(2025, 1, 1), circunstancias_art59=dict(VETORES_NEGATIVOS_ART59))
    )
    assert exasperada.dias_multa > 10  # proporcional à pena

    informada = calcular_dosimetria_crime(CrimeDosimetriaInput(crime_code="art_24a_lmp", data_fato=D(2025, 1, 1), dias_multa=30))
    assert informada.dias_multa == 30
    assert informada.dias_multa_sugeridos_pelo_motor is False
    assert not any("Multa cumulativa" in a for a in informada.alertas_juridicos)

    # Ameaça (multa apenas alternativa): nenhuma multa sugerida
    ameaca = calcular_dosimetria_crime(CrimeDosimetriaInput(crime_code="art_147_cp", data_fato=D(2024, 1, 1)))
    assert ameaca.dias_multa == 0


def test_aumento_art147a_parametro_explicito_com_padrao_documentado():
    padrao = calcular_dosimetria_crime(CrimeDosimetriaInput(crime_code="art_147a_cp", data_fato=D(2023, 1, 1)))
    assert padrao.aumento_art147a_aplicado is True
    assert padrao.pena_definitiva.to_total_days() == 180 + 90
    assert any("147-A, § 1º, II" in a and "por padrão" in a for a in padrao.alertas_juridicos)

    sem = calcular_dosimetria_crime(
        CrimeDosimetriaInput(crime_code="art_147a_cp", data_fato=D(2023, 1, 1), aplicar_aumento_art147a_p1_ii=False)
    )
    assert sem.aumento_art147a_aplicado is False
    assert sem.pena_definitiva.to_total_days() == 180
    assert any("NÃO aplicada" in a for a in sem.alertas_juridicos)

    fracao_juiz = calcular_dosimetria_crime(
        CrimeDosimetriaInput(crime_code="art_147a_cp", data_fato=D(2023, 1, 1), fracao_aumento_terceira_fase=(1, 3))
    )
    assert fracao_juiz.aumento_art147a_aplicado is False
    assert fracao_juiz.pena_definitiva.to_total_days() == 240


# ==============================================================================
# Catálogo: direito intertemporal
# ==============================================================================
def test_art129_p13_fato_anterior_a_lei_14188_usa_paragrafo_9():
    spec = get_penal_type_for_date("art_129_p13_cp", D(2021, 1, 1))
    assert spec.especie_pena == "detencao"
    assert spec.min_pena == PenaAnosMesesDias(meses=3)
    assert spec.max_pena == PenaAnosMesesDias(anos=3)
    assert "14.188/2021" in spec.observacao_intertemporal

    pos = get_penal_type_for_date("art_129_p13_cp", D(2021, 7, 29))
    assert pos.especie_pena == "reclusao"
    assert pos.min_pena == PenaAnosMesesDias(anos=1)


@pytest.mark.parametrize("codigo", ["art_147a_cp", "art_147b_cp", "art_139_cp"])
def test_tipos_com_multa_cumulativa(codigo):
    assert get_penal_type_for_date(codigo, D(2023, 1, 1)).tem_multa_cumulativa is True


def test_tipo_anterior_a_lei_criadora_recebe_aviso():
    assert "ATENÇÃO" in get_penal_type_for_date("art_147a_cp", D(2021, 3, 1)).observacao_intertemporal
    assert "ATENÇÃO" not in get_penal_type_for_date("art_147a_cp", D(2021, 4, 1)).observacao_intertemporal


def test_dosimetria_config_default_inalterada():
    """Garantia de compatibilidade dos padrões de configuração."""
    cfg = DosimetriaConfig()
    assert cfg.criterio_primeira_fase == "um_oitavo_intervalo"
    assert cfg.fracao_segunda_fase == (1, 6)
