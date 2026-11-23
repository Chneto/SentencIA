import datetime

from app.legal_engine.penal_types_catalog import (
    get_penal_type_for_date,
)
from app.legal_engine.dosimetria_calculator import (
    CircunstanciaArt59,
    CrimeDosimetriaInput,
    ConcursoCrimesConfig,
    DosimetriaConfig,
    calcular_dosimetria_crime,
    calcular_dosimetria_global,
    calcular_prescricao,
    PenaAnosMesesDias,
)


def test_tempus_regit_actum_lesao_corporal_pre_e_pos_lei_14994_2024():
    """Verifica Direito Intertemporal: Lei 14.994/2024 (vigência em 10/10/2024) elevou a pena da lesão doméstica de 1-4 anos para 2-5 anos."""
    crime_pre = get_penal_type_for_date("art_129_p13_cp", datetime.date(2024, 5, 15))
    assert crime_pre.min_pena == PenaAnosMesesDias(anos=1, meses=0, dias=0)
    assert crime_pre.max_pena == PenaAnosMesesDias(anos=4, meses=0, dias=0)
    assert crime_pre.has_builtin_domestic_violence_element is True
    assert crime_pre.has_violence_or_grave_threat is True

    crime_pos = get_penal_type_for_date("art_129_p13_cp", datetime.date(2025, 2, 10))
    assert crime_pos.min_pena == PenaAnosMesesDias(anos=2, meses=0, dias=0)
    assert crime_pos.max_pena == PenaAnosMesesDias(anos=5, meses=0, dias=0)
    assert crime_pos.especie_pena == "reclusao"
    assert crime_pos.has_builtin_domestic_violence_element is True


def test_tempus_regit_actum_ameaca_contra_mulher():
    """Verifica a ameaça contra a mulher antes e depois da Lei 14.994/2024 (pena em dobro e ação incondicionada)."""
    ameaca_pre = get_penal_type_for_date("art_147_cp", datetime.date(2024, 3, 1))
    assert ameaca_pre.min_pena == PenaAnosMesesDias(anos=0, meses=1, dias=0)
    assert ameaca_pre.max_pena == PenaAnosMesesDias(anos=0, meses=6, dias=0)
    assert ameaca_pre.acao_penal == "publica_condicionada_representacao"
    assert ameaca_pre.has_builtin_domestic_violence_element is False

    ameaca_pos = get_penal_type_for_date("art_147_cp", datetime.date(2025, 1, 20), ameaca_qualificada=True)
    assert ameaca_pos.min_pena == PenaAnosMesesDias(anos=0, meses=2, dias=0)
    assert ameaca_pos.max_pena == PenaAnosMesesDias(anos=1, meses=0, dias=0)
    assert ameaca_pos.acao_penal == "publica_incondicionada"
    assert ameaca_pos.has_builtin_domestic_violence_element is True


def test_dosimetria_primeira_fase_um_oitavo_intervalo_e_sumula_444():
    """Testa cálculo de 1/8 sobre o intervalo na 1ª fase e bloqueio de maus antecedentes sem trânsito em julgado (Súmula 444 STJ)."""
    config = DosimetriaConfig(
        criterio_primeira_fase="um_oitavo_intervalo",
        aplicar_sumula_231_stj=True,
        aplicar_sumula_444_stj=True,
    )
    # Lesão corporal pós-Lei 14.994/24: 2 a 5 anos (intervalo = 3 anos = 36 meses; 1/8 = 4 meses e 15 dias)
    entrada = CrimeDosimetriaInput(
        crime_code="art_129_p13_cp",
        data_fato=datetime.date(2025, 3, 10),
        circunstancias_art59={
            "culpabilidade": CircunstanciaArt59(desfavoravel=True, fundamentacao="Agressão na presença dos filhos menores."),
            "antecedentes": CircunstanciaArt59(
                desfavoravel=True,
                possui_transito_em_julgado_anterior=False,
                fundamentacao="Anotações de inquéritos em curso.",
            ),
            "comportamento_vitima": CircunstanciaArt59(desfavoravel=True, fundamentacao="Tentativa indevida de valorar contra o réu."),
        },
        agravantes_art61_f_violencia_domestica=True,  # Deve ser bloqueada por bis in idem!
        confissao_espontanea=False,
        reincidente=False,
    )
    resultado = calcular_dosimetria_crime(entrada, config)

    # Antecedentes sem trânsito (Súmula 444 STJ) e comportamento da vítima devem ser neutralizados!
    assert resultado.vetores_negativos_efetivos == ["culpabilidade"]
    assert any("Súmula 444/STJ" in alerta for alerta in resultado.alertas_juridicos)
    assert any("comportamento da vítima" in alerta.lower() for alerta in resultado.alertas_juridicos)

    # 2 anos + 4 meses e 15 dias = 2 anos, 4 meses e 15 dias
    assert resultado.pena_base == PenaAnosMesesDias(anos=2, meses=4, dias=15)

    # Bloqueio de bis in idem na 2ª fase (art. 61, II, 'f' já integra o tipo do art. 129, §13 CP)
    assert resultado.bis_in_idem_art61f_bloqueado is True
    assert resultado.pena_intermediaria == PenaAnosMesesDias(anos=2, meses=4, dias=15)
    assert resultado.pena_definitiva == PenaAnosMesesDias(anos=2, meses=4, dias=15)


def test_sumula_231_stj_e_compensacao_confissao_reincidencia():
    """Verifica que atenuante não reduz abaixo do mínimo legal (Súmula 231/STJ) e compensação entre confissão e reincidência."""
    config = DosimetriaConfig(criterio_primeira_fase="um_oitavo_intervalo", aplicar_sumula_231_stj=True)
    # Crime de Descumprimento de Medida Protetiva (art. 24-A da Lei 11.340/06) pós-Lei 14.994/24 (2 a 5 anos)
    entrada_minima = CrimeDosimetriaInput(
        crime_code="art_24a_lmp",
        data_fato=datetime.date(2025, 1, 15),
        circunstancias_art59={},
        confissao_espontanea=True,
        reincidente=False,
    )
    res_min = calcular_dosimetria_crime(entrada_minima, config)
    assert res_min.pena_base == PenaAnosMesesDias(anos=2, meses=0, dias=0)
    assert res_min.pena_intermediaria == PenaAnosMesesDias(anos=2, meses=0, dias=0)
    assert res_min.sumula_231_aplicada is True

    # Com confissão + reincidência: devem compensar integralmente
    entrada_comp = CrimeDosimetriaInput(
        crime_code="art_24a_lmp",
        data_fato=datetime.date(2025, 1, 15),
        circunstancias_art59={
            "circunstancias": CircunstanciaArt59(desfavoravel=True, fundamentacao="Descumprimento reiterado no período noturno.")
        },
        confissao_espontanea=True,
        reincidente=True,
    )
    res_comp = calcular_dosimetria_crime(entrada_comp, config)
    assert res_comp.compensacao_confissao_reincidencia is True
    assert res_comp.pena_intermediaria == res_comp.pena_base


def test_concurso_material_sumula_588_stj_e_prescricao():
    """Verifica concurso material (art. 69 CP), vedação de substituição por restritiva de direitos (Súmula 588 STJ) e cálculo de prescrição."""
    config = DosimetriaConfig()
    c1 = CrimeDosimetriaInput(
        crime_code="art_129_p9_cp",
        data_fato=datetime.date(2024, 2, 10),
        circunstancias_art59={},
    )
    c2 = CrimeDosimetriaInput(
        crime_code="art_147_cp",
        data_fato=datetime.date(2024, 2, 10),
        circunstancias_art59={},
        agravantes_art61_f_violencia_domestica=True,  # Pré-Lei 14.994/24 na ameaça incide art. 61, II, f!
    )
    global_res = calcular_dosimetria_global(
        crimes=[c1, c2],
        config=config,
        concurso=ConcursoCrimesConfig(tipo_concurso="material"),
        pedido_expresso_dano_moral_mp=True,
        valor_dano_moral_decidido=3000.0,
    )
    assert global_res.substituicao_art44_cabivel is False
    assert "Súmula 588/STJ" in global_res.motivo_vedacao_substituicao
    assert global_res.indenizacao_dano_moral_tema983 == 3000.0

    # Teste de prescrição
    presc = calcular_prescricao(
        pena_aplicada=PenaAnosMesesDias(anos=1, meses=0, dias=0),
        data_fato=datetime.date(2023, 1, 10),
        data_recebimento_denuncia=datetime.date(2023, 6, 10),
        data_sentenca=datetime.date(2026, 11, 10),
        reu_menor_21_ou_maior_70=False,
    )
    # Pena de 1 ano -> prescreve em 4 anos (art. 109, V, CP). Entre 2023 e 2026 (< 4 anos), não prescreveu.
    assert presc.prazo_prescricional_anos == 4
    assert presc.prescricao_reconhecida is False


def test_oito_circunstancias_judiciais_art59_geradas_individualmente():
    """Garante que todas as 8 circunstâncias judiciais do art. 59 do CP são geradas individualmente com blindagem de gênero e Súmula 444/STJ."""
    from app.legal_engine.dosimetria_calculator import VETORES_ART_59, gerar_vetores_art59_detalhados

    spec = get_penal_type_for_date("art_129_p13_cp", datetime.date(2025, 2, 10))

    # Tenta forçar comportamento da vítima e antecedentes sem trânsito em julgado como negativos
    circs_entrada = {
        "culpabilidade": CircunstanciaArt59(desfavoravel=True, fundamentacao="Agressão grave no lar."),
        "antecedentes": CircunstanciaArt59(desfavoravel=True, possui_transito_em_julgado_anterior=False),
        "comportamento_vitima": CircunstanciaArt59(desfavoravel=True, fundamentacao="Tentativa de valorar negativamente."),
    }

    vetores = gerar_vetores_art59_detalhados(spec, circs_entrada, aplicar_sumula_444_stj=True)

    # 1. Todas as 8 circunstâncias devem estar presentes
    assert len(vetores) == 8
    for v_cod in VETORES_ART_59:
        assert v_cod in vetores, f"Vetor obrigatório {v_cod} não encontrado"
        assert vetores[v_cod].rotulo != ""
        assert vetores[v_cod].fundamentacao != ""

    # 2. Blindagem de gênero: comportamento da vítima SEMPRE neutro sob a Resolução CNJ 492/2023
    assert vetores["comportamento_vitima"].desfavoravel is False
    assert "Resolução CNJ nº 492/2023" in vetores["comportamento_vitima"].fundamentacao

    # 3. Blindagem da Súmula 444/STJ nos antecedentes
    assert vetores["antecedentes"].desfavoravel is False
    assert "Súmula 444 do STJ" in vetores["antecedentes"].fundamentacao

    # 4. Culpabilidade validada como desfavorável
    assert vetores["culpabilidade"].desfavoravel is True


def test_obter_fundamentacao_padrao_vetor_sempre_conclui_com_valoracao():
    """Garante que todos os 8 vetores sempre possuem conclusão com valoração jurídica explícita, mesmo com detalhes customizados."""
    from app.legal_engine.dosimetria_calculator import VETORES_ART_59, obter_fundamentacao_padrao_vetor

    for v_cod in VETORES_ART_59:
        # Teste neutro sem custom
        texto_n, status_n = obter_fundamentacao_padrao_vetor(v_cod, desfavoravel=False)
        assert any(termo in texto_n.lower() for termo in ("neutra", "favorável", "resolução cnj")), f"Vetor neutro {v_cod} sem valoração explícita: {texto_n}"

        # Teste neutro com custom factual
        texto_nc, status_nc = obter_fundamentacao_padrao_vetor(v_cod, desfavoravel=False, detalhes_customizados="detalhe fático do caso concreto")
        assert any(termo in texto_nc.lower() for termo in ("neutra", "favorável", "resolução cnj")), f"Vetor neutro {v_cod} com custom sem valoração: {texto_nc}"
        if v_cod != "comportamento_vitima":
            assert "detalhe fático do caso concreto" in texto_nc

        # Teste desfavorável
        texto_d, status_d = obter_fundamentacao_padrao_vetor(v_cod, desfavoravel=True)
        if v_cod != "comportamento_vitima":
            assert "desfavorável" in texto_d.lower(), f"Vetor desfavorável {v_cod} sem valoração expressa: {texto_d}"
        else:
            # Comportamento da vítima é SEMPRE neutro mesmo com desfavoravel=True
            assert "neutra" in texto_d.lower()


def test_gerar_vetores_art59_detalhados_flexibilidade_spec_string_e_none():
    """Garante que gerar_vetores_art59_detalhados funciona com None, string de código do crime ou PenalTypeSpec."""
    from app.legal_engine.dosimetria_calculator import gerar_vetores_art59_detalhados

    # 1. Com None
    v_none = gerar_vetores_art59_detalhados(spec=None)
    assert len(v_none) == 8

    # 2. Com string de código
    v_str = gerar_vetores_art59_detalhados(spec="art_147a_cp")
    assert len(v_str) == 8

    # 3. Com PenalTypeSpec
    spec = get_penal_type_for_date("art_24a_lmp", datetime.date(2025, 1, 15))
    v_spec = gerar_vetores_art59_detalhados(spec=spec)
    assert len(v_spec) == 8


def test_formatar_especie_pena_acentuacao():
    """Verifica a correta formatação ortográfica das espécies de pena na dosimetria e na sentença."""
    from app.legal_engine.dosimetria_calculator import formatar_especie_pena
    assert formatar_especie_pena("reclusao") == "reclusão"
    assert formatar_especie_pena("detencao") == "detenção"
    assert formatar_especie_pena("prisao_simples") == "prisão simples"
    assert formatar_especie_pena("outra") == "outra"


def test_doutrina_alice_bianchini_apenas_quando_filhos_presentes():
    """Garante que a citação doutrinária de Alice Bianchini (violência perante filhos) só é incluída quando descendentes estavam presentes."""
    from app.demo.demo_case import get_demo_case_bundle
    from app.generation.sentence_generator import gerar_sentenca_estruturada

    demo = get_demo_case_bundle()
    sentenca = gerar_sentenca_estruturada(demo, use_ollama_if_available=False)
    # No caso demo, a lesão ocorreu diante da filha de 5 anos -> citação legítima
    assert "Alice Bianchini" in sentenca.texto_completo
    assert "descendente infante" in sentenca.texto_completo


def test_estrutura_completa_dosimetria_topicos_e_subtopicos():
    """Garante a estruturação individualizada de cada delito procedente com tópicos 3.X, 3.X.1, 3.X.2, 3.Y e tópicos 4 a 8."""
    from app.demo.demo_case import get_demo_case_bundle
    from app.generation.sentence_generator import gerar_sentenca_estruturada

    demo = get_demo_case_bundle()
    sentenca = gerar_sentenca_estruturada(demo, use_ollama_if_available=False)
    texto = sentenca.texto_completo

    # 1. Estrutura individualizada dos crimes
    assert "3.1. Do Crime de Descumprimento de Medida Protetiva de Urgência" in texto
    assert "3.1.1. Análise individualizada das circunstâncias judiciais (art. 59 do Código Penal):" in texto
    assert "3.1.2. Cálculo trifásico da pena (art. 68 do Código Penal):" in texto

    assert "3.2. Do Crime de Lesão Corporal no Contexto de Violência Doméstica / Contra a Mulher" in texto
    assert "3.2.1. Análise individualizada das circunstâncias judiciais (art. 59 do Código Penal):" in texto
    assert "3.2.2. Cálculo trifásico da pena (art. 68 do Código Penal):" in texto

    assert "3.3. Do Crime de Ameaça contra a Mulher por Razões da Condição do Sexo Feminino" in texto
    assert "3.3.1. Análise individualizada das circunstâncias judiciais (art. 59 do Código Penal):" in texto
    assert "3.3.2. Cálculo trifásico da pena (art. 68 do Código Penal):" in texto

    # 2. Verificação de todas as 8 letras a) a h)
    for letra in ("a) CULPABILIDADE:", "b) ANTECEDENTES CRIMINAIS:", "c) CONDUTA SOCIAL:",
                  "d) PERSONALIDADE DO AGENTE:", "e) MOTIVOS DO CRIME:", "f) CIRCUNSTÂNCIAS DO CRIME:",
                  "g) CONSEQUÊNCIAS DO CRIME:", "h) COMPORTAMENTO DA VÍTIMA:"):
        assert letra in texto, f"Subitem judicial {letra} ausente na sentença"

    # 3. Tópico de Unificação das Penas (Concurso Material art. 69 CP)
    assert "3.4. Da Unificação das Penas e do Concurso de Crimes (Art. 69 do Código Penal - Concurso Material)" in texto

    # 4. Tópicos numerados 4 a 8
    assert "4. Do Regime Inicial de Cumprimento de Pena (Art. 33, §§ 2º e 3º, do CP c/c Súmulas 718 e 719 do STF e 440 do STJ)" in texto
    assert "5. Da Substituição da Pena Privativa de Liberdade por Restritivas de Direitos (Art. 44 do CP c/c Súmula 588 do STJ)" in texto
    assert "6. Da Suspensão Condicional da Pena - Sursis Penal (Art. 77 do CP c/c Súmula 536 do STJ)" in texto
    assert "7. Da Reparação Mínima de Danos Morais (Art. 387, IV, do CPP c/c Tema Repetitivo 983 do STJ)" in texto
    assert "8. Das Custas Processuais e das Disposições Finais (Art. 804 do CPP, Art. 21 da Lei nº 11.340/2006, Art. 15, III, da CF/88)" in texto


def test_concurso_crimes_apenas_reclusao_ou_apenas_detencao():
    """Garante que a unificação das penas (art. 69 CP) não exibe '0 dia' para espécies não incidentes no caso."""
    from app.generation.sentence_generator import formatar_secao_dosimetria_completa

    # 1. Caso com 2 crimes de detenção apenas (ex: art. 147 CP pré-2024 e art. 24-A LMP pré-2024)
    c1 = CrimeDosimetriaInput(
        crime_code="art_147_cp",
        data_fato=datetime.date(2024, 3, 1),
        circunstancias_art59={},
    )
    c2 = CrimeDosimetriaInput(
        crime_code="art_147_cp",
        data_fato=datetime.date(2024, 3, 1),
        circunstancias_art59={},
    )
    res_det = calcular_dosimetria_global([c1, c2])
    texto_det = formatar_secao_dosimetria_completa(res_det, criterio_1fase="um_oitavo_intervalo")

    assert "Pena Total de Detenção" in texto_det
    assert "Pena Total de Reclusão: 0 (zero) dia" not in texto_det
    assert "0 (zero) dia de reclusão" not in texto_det

    # 2. Caso com concurso formal (art. 70 CP)
    res_formal = calcular_dosimetria_global(
        [c1, c2],
        concurso=ConcursoCrimesConfig(tipo_concurso="formal", fracao_exasperacao=(1, 6)),
    )
    assert res_formal.pena_total_unificada.to_total_days() < res_det.pena_total_unificada.to_total_days()
