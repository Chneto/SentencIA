# -*- coding: utf-8 -*-
"""
Auditor v2.0.0 (Agente C): cabeçalhos com qualquer travessão, score coerente (toda reprovação
desconta), marcadores [COMPLETAR PELO JUÍZO] pendentes, penas do dispositivo x dosimetria
calculada, seções de fallback sinalizadas, casamento EXATO de acórdãos, cache do índice invalidado
quando a base muda e ausência de IDs "demo" fixos.
"""

import copy

import pytest

import app.knowledge.knowledge_base as kb
from app.core.case_context import obter_contexto
from app.demo.demo_case import get_demo_case_bundle
from app.generation.hallucination_auditor import (
    RelatorioAuditoriaSentenca,
    _limpar_cache_base_fechada,
    auditar_secao,
    auditar_sentenca,
)
from app.generation.sentence_generator import gerar_sentenca_estruturada
from app.ingestion.laya_classifier import PecaClassificada


# ------------------------------------------------------------------------------
# Fixtures auxiliares
# ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def demo_ctx_e_sentenca():
    demo = get_demo_case_bundle()
    ctx = obter_contexto(demo)
    sentenca = gerar_sentenca_estruturada(demo, use_ollama_if_available=False)
    return ctx, sentenca


def _peca(doc_id: str, conteudo: str = "Conteúdo da peça.", categoria: str = "OUTRAS") -> PecaClassificada:
    return PecaClassificada(
        doc_id=doc_id,
        folha="fls. 1",
        titulo=f"Peça {doc_id}",
        data_peca="2025-01-10",
        fase="JUDICIAL",
        categoria=categoria,
        laya_confidence=0.9,
        resumo_estruturado="Síntese.",
        conteudo=conteudo,
    )


_VETORES_OK = (
    "3. Da Dosimetria da Pena\n\n"
    "a) CULPABILIDADE: normal à espécie. Circunstância judicial neutra.\n"
    "b) ANTECEDENTES CRIMINAIS: réu primário, bons antecedentes. Circunstância judicial neutra.\n"
    "c) CONDUTA SOCIAL: nada a valorar. Circunstância judicial neutra.\n"
    "d) PERSONALIDADE DO AGENTE: nada a valorar. Circunstância judicial neutra.\n"
    "e) MOTIVOS DO CRIME: normais ao tipo. Circunstância judicial neutra.\n"
    "f) CIRCUNSTÂNCIAS DO CRIME: normais ao tipo. Circunstância judicial neutra.\n"
    "g) CONSEQUÊNCIAS DO CRIME: normais ao tipo. Circunstância judicial neutra.\n"
    "h) COMPORTAMENTO DA VÍTIMA: não concorreu para o crime. Circunstância judicial neutra.\n"
)


def _sentenca_sintetica(dispositivo: str, travessao: str = "–") -> str:
    return (
        "SENTENÇA\n\n"
        f"I {travessao} RELATÓRIO\n\nO Ministério Público ofereceu denúncia (ID-500).\n\n"
        f"II {travessao} FUNDAMENTAÇÃO\n\nA materialidade está comprovada.\n\n"
        f"{_VETORES_OK}\n"
        f"III {travessao} DISPOSITIVO\n\n{dispositivo}\n\n"
        "Determino a intimação pessoal da ofendida, nos termos do art. 21 da Lei nº 11.340/2006."
    )


def _dosimetria_sintetica() -> dict:
    return {
        "crimes_calculados": [
            {
                "crime_spec": {"code": "art_129_p13_cp", "especie_pena": "reclusao"},
                "pena_definitiva": {"anos": 2, "meses": 4, "dias": 15},
                "dias_multa": 0,
            },
            {
                "crime_spec": {"code": "art_147_cp", "especie_pena": "detencao"},
                "pena_definitiva": {"anos": 0, "meses": 2, "dias": 0},
                "dias_multa": 10,
                "valor_unitario_multa": "1/30",
            },
        ],
        "penas_por_especie": {
            "reclusao": {"anos": 2, "meses": 4, "dias": 15},
            "detencao": {"anos": 0, "meses": 2, "dias": 0},
        },
        "pena_total_unificada": {"anos": 2, "meses": 6, "dias": 15},
        "pena_remanescente_detracao": {"anos": 0, "meses": 0, "dias": 0},
        "total_dias_multa": 10,
        "sem_condenacao": False,
    }


_DISPOSITIVO_CORRETO = (
    "Ante o exposto, JULGO PROCEDENTE a denúncia para CONDENAR o acusado às penas definitivas de "
    "2 (dois) anos, 4 (quatro) meses e 15 (quinze) dias de reclusão e 2 (dois) meses de detenção, "
    "além de 10 (dez) dias-multa, no valor unitário de 1/30 do salário mínimo vigente ao tempo do fato. A soma das penas privativas de liberdade é de 2 anos, 6 meses e 15 dias."
)


# ------------------------------------------------------------------------------
# Contrato do laudo
# ------------------------------------------------------------------------------


def test_relatorio_mantem_campos_antigos_e_acrescenta_campos_v2():
    campos = set(RelatorioAuditoriaSentenca.model_fields)
    antigos = {
        "aprovada_sem_alucinacao", "score_confiabilidade", "ids_citados_verificados", "ids_inexistentes",
        "citacoes_juridicas_verificadas", "citacoes_nao_autorizadas", "teses_defesa_enfrentadas",
        "teses_omitidas", "conformidade_art381_cpp", "conformidade_art21_lmp", "conformidade_lc95_98",
        "conformidade_datas_padrao_br", "conformidade_abnt_citacoes", "conformidade_estilo_cnj_amb",
        "conformidade_oito_circunstancias_art59", "detalhes_camadas",
    }
    novos = {"score_forma", "verificacoes_conteudo", "secoes_fallback", "penas_divergentes"}
    assert antigos <= campos
    assert novos <= campos


def test_sentenca_demo_aprovada_com_penas_conferidas(demo_ctx_e_sentenca):
    ctx, sentenca = demo_ctx_e_sentenca
    dados = copy.deepcopy(sentenca.dosimetria_dados)
    dados.pop("simulacao_presume_condenacao", None)
    laudo = auditar_sentenca(
        sentenca.texto_completo, ctx.pecas, ctx.meta.teses_defesa, list(ctx.ids_midias), dosimetria_dados=dados
    )
    assert laudo.aprovada_sem_alucinacao is True, laudo.model_dump()
    assert laudo.score_confiabilidade == 100.0
    assert laudo.score_forma == 100.0
    assert laudo.penas_divergentes == []
    status = {v["verificacao"]: v["status"] for v in laudo.verificacoes_conteudo}
    assert status["penas_dispositivo_x_dosimetria"] == "APROVADO"
    assert status["marcadores_completar_pelo_juizo"] == "APROVADO"


# ------------------------------------------------------------------------------
# Cabeçalhos e score coerente
# ------------------------------------------------------------------------------


@pytest.mark.parametrize("travessao", ["–", "-", "—"])
def test_cabecalhos_aceitam_qualquer_travessao(travessao):
    texto = _sentenca_sintetica(_DISPOSITIVO_CORRETO, travessao=travessao)
    laudo = auditar_sentenca(texto, [_peca("ID-500")], [])
    assert laudo.conformidade_art381_cpp is True


def test_art381_reprovado_desconta_score_e_reprova():
    texto = _sentenca_sintetica(_DISPOSITIVO_CORRETO).replace("I – RELATÓRIO", "RELATO DOS FATOS")
    laudo = auditar_sentenca(texto, [_peca("ID-500")], [])
    assert laudo.conformidade_art381_cpp is False
    assert laudo.aprovada_sem_alucinacao is False
    assert laudo.score_confiabilidade == 75.0
    assert laudo.score_forma == 75.0
    camada4 = next(d for d in laudo.detalhes_camadas if d["camada"].startswith("4."))
    assert camada4["status"] == "REVISAR"
    assert "Art. 381" in camada4["descricao"]


@pytest.mark.parametrize(
    "mutacao",
    [
        lambda t: t,
        lambda t: t + "\nConforme ID-99999999.",
        lambda t: t + "\nConforme Súmula 999/STJ.",
        lambda t: t.replace("art. 21 da Lei nº 11.340/2006", "lei"),
        lambda t: t + "\nCertidão de 2025-01-10.",
        lambda t: t + "\n[COMPLETAR PELO JUÍZO: análise da preliminar]",
        lambda t: t.replace("III – DISPOSITIVO", "DECISÃO"),
    ],
)
def test_aprovacao_coerente_com_score(mutacao):
    texto = mutacao(_sentenca_sintetica(_DISPOSITIVO_CORRETO))
    laudo = auditar_sentenca(texto, [_peca("ID-500")], [], dosimetria_dados=_dosimetria_sintetica())
    assert laudo.aprovada_sem_alucinacao is (laudo.score_confiabilidade == 100.0)
    assert laudo.score_forma >= laudo.score_confiabilidade


# ------------------------------------------------------------------------------
# Verificação de conteúdo: marcadores e penas
# ------------------------------------------------------------------------------


def test_marcador_completar_pelo_juizo_reprova_com_explicacao(demo_ctx_e_sentenca):
    ctx, sentenca = demo_ctx_e_sentenca
    texto = sentenca.texto_completo.replace(
        "II – FUNDAMENTAÇÃO",
        "II – FUNDAMENTAÇÃO\n\n[COMPLETAR PELO JUÍZO: enfrentamento da preliminar de nulidade]",
        1,
    )
    laudo = auditar_sentenca(texto, ctx.pecas, ctx.meta.teses_defesa, list(ctx.ids_midias))
    assert laudo.aprovada_sem_alucinacao is False
    assert laudo.conformidade_marcadores_pendentes is False
    assert laudo.marcadores_pendentes == ["[COMPLETAR PELO JUÍZO: enfrentamento da preliminar de nulidade]"]
    assert laudo.score_confiabilidade == 85.0
    assert laudo.score_forma == 100.0
    verif = next(v for v in laudo.verificacoes_conteudo if v["verificacao"] == "marcadores_completar_pelo_juizo")
    assert verif["status"] == "REPROVADO"
    camada5 = next(d for d in laudo.detalhes_camadas if d["camada"].startswith("5."))
    assert "COMPLETAR PELO JUÍZO" in camada5["descricao"]


def test_auditar_secao_acusa_marcador_pendente():
    texto = (
        "2.1. Da preliminar de inépcia (PREL-01). A defesa sustenta inépcia. "
        "[COMPLETAR PELO JUÍZO: decidir a preliminar com base nos autos]"
    )
    laudo = auditar_secao("preliminares", texto, ["ID-500"], [])
    assert laudo.aprovado is False
    assert laudo.conformidade_marcadores_pendentes is False
    assert laudo.marcadores_pendentes
    assert any("COMPLETAR PELO JUÍZO" in e for e in laudo.erros)
    assert laudo.score == 85.0


def test_penas_corretas_no_dispositivo_aprovam():
    laudo = auditar_sentenca(
        _sentenca_sintetica(_DISPOSITIVO_CORRETO), [_peca("ID-500")], [], dosimetria_dados=_dosimetria_sintetica()
    )
    assert laudo.penas_divergentes == []
    assert laudo.conformidade_penas_dosimetria is True
    assert laudo.aprovada_sem_alucinacao is True, laudo.model_dump()


def test_pena_divergente_no_dispositivo_reprova():
    disp = _DISPOSITIVO_CORRETO.replace("2 (dois) anos, 4 (quatro) meses e 15 (quinze) dias de reclusão", "3 (três) anos de reclusão")
    laudo = auditar_sentenca(_sentenca_sintetica(disp), [_peca("ID-500")], [], dosimetria_dados=_dosimetria_sintetica())
    assert laudo.aprovada_sem_alucinacao is False
    assert laudo.conformidade_penas_dosimetria is False
    assert any("3 (três) anos de reclusão" in d for d in laudo.penas_divergentes)
    # A pena de reclusão calculada deixou de constar do dispositivo.
    assert any("não consta do dispositivo" in d for d in laudo.penas_divergentes)
    assert laudo.score_confiabilidade < 100.0


def test_pena_de_especie_omitida_e_multa_divergente_reprovam():
    disp = (
        "Ante o exposto, CONDENO o acusado à pena definitiva de 2 (dois) anos, 4 (quatro) meses e "
        "15 (quinze) dias de reclusão, além de 30 (trinta) dias-multa."
    )
    laudo = auditar_sentenca(_sentenca_sintetica(disp), [_peca("ID-500")], [], dosimetria_dados=_dosimetria_sintetica())
    assert any("detencao" in d or "detenção" in d for d in laudo.penas_divergentes)
    assert any("30 (trinta) dias-multa" in d for d in laudo.penas_divergentes)
    assert laudo.aprovada_sem_alucinacao is False


def test_pena_citada_sem_condenacao_calculada_reprova():
    dados = {"crimes_calculados": [], "penas_por_especie": {}, "sem_condenacao": True}
    laudo = auditar_sentenca(_sentenca_sintetica(_DISPOSITIVO_CORRETO), [_peca("ID-500")], [], dosimetria_dados=dados)
    assert laudo.penas_divergentes
    assert laudo.aprovada_sem_alucinacao is False


def test_penas_nao_conferidas_sem_dosimetria_e_presuncao_fica_pendente():
    dados = _dosimetria_sintetica()
    dados["simulacao_presume_condenacao"] = True
    disp_errado = "Ante o exposto, CONDENO o acusado à pena de 9 (nove) anos de reclusão."
    laudo_sim = auditar_sentenca(_sentenca_sintetica(disp_errado), [_peca("ID-500")], [], dosimetria_dados=dados)
    laudo_sem = auditar_sentenca(_sentenca_sintetica(disp_errado), [_peca("ID-500")], [])
    # Sem dosimetria calculada: nada a conferir.
    verif = next(v for v in laudo_sem.verificacoes_conteudo if v["verificacao"] == "penas_dispositivo_x_dosimetria")
    assert verif["status"] == "NAO_APLICAVEL"
    assert laudo_sem.penas_divergentes == []
    # Presuncao de condenacao (crime sem veredicto): as penas NAO foram conferidas -> PENDENTE,
    # nunca aprovada nem exportavel sem marca d'agua.
    verif = next(v for v in laudo_sim.verificacoes_conteudo if v["verificacao"] == "penas_dispositivo_x_dosimetria")
    assert verif["status"] == "PENDENTE"
    assert laudo_sim.penas_divergentes
    assert laudo_sim.conformidade_penas_dosimetria is False
    assert laudo_sim.aprovada_sem_alucinacao is False
    # O demo (ficticio) usa dosimetria_para_auditoria e segue aprovavel.
    from app.generation.hallucination_auditor import dosimetria_para_auditoria

    assert dosimetria_para_auditoria(dados, True)["simulacao_presume_condenacao"] is False
    assert dosimetria_para_auditoria(dados, False)["simulacao_presume_condenacao"] is True


def test_secoes_fallback_sao_sinalizadas_sem_alterar_score():
    texto = _sentenca_sintetica(_DISPOSITIVO_CORRETO)
    laudo = auditar_sentenca(texto, [_peca("ID-500")], [], secoes_fallback=["dosimetria", "dispositivo"])
    assert laudo.secoes_fallback == ["dosimetria", "dispositivo"]
    assert laudo.score_confiabilidade == 100.0
    sinal = next(d for d in laudo.detalhes_camadas if d["camada"].startswith("6."))
    assert sinal["status"] == "SINALIZADO"
    assert "dosimetria" in sinal["descricao"]
    verif = next(v for v in laudo.verificacoes_conteudo if v["verificacao"] == "secoes_fallback")
    assert verif["status"] == "SINALIZADO"


# ------------------------------------------------------------------------------
# Camada 1: sem IDs "demo" fixos
# ------------------------------------------------------------------------------


def test_ids_de_midia_nao_sao_presumidos_pelo_prefixo_4100():
    pecas = [_peca("ID-4100101"), _peca("ID-4100102")]
    texto = _sentenca_sintetica(_DISPOSITIVO_CORRETO) + "\nDepoimento da ofendida (ID-4100301)."
    laudo = auditar_sentenca(texto, pecas, [])
    assert "ID-4100301" in laudo.ids_inexistentes


def test_midia_registrada_no_termo_dos_autos_e_valida():
    termo = _peca(
        "ID-7000300",
        conteudo="Termo de audiência. Declarações da ofendida (Mídia ID-7000301, duração 06min42s).",
        categoria="TERMO_AUDIENCIA",
    )
    texto = _sentenca_sintetica(_DISPOSITIVO_CORRETO).replace("(ID-500)", "(ID-7000300)") + "\nVide ID-7000301."
    laudo = auditar_sentenca(texto, [termo], [])
    assert "ID-7000301" in laudo.ids_citados_verificados
    assert laudo.ids_inexistentes == []


def test_midias_informadas_explicitamente_sao_validas():
    texto = _sentenca_sintetica(_DISPOSITIVO_CORRETO) + "\nVide ID-MIDIA-ABCDEF12."
    laudo = auditar_sentenca(texto, [_peca("ID-500")], [], ["ID-MIDIA-ABCDEF12"])
    assert laudo.ids_inexistentes == []


# ------------------------------------------------------------------------------
# Camada 2: casamento exato e cache invalidado
# ------------------------------------------------------------------------------


def test_acordao_e_apelacao_casam_por_numero_exato():
    base = _sentenca_sintetica(_DISPOSITIVO_CORRETO)
    ok = auditar_sentenca(base + "\nConforme REsp 1.675.874/MS.", [_peca("ID-500")], [])
    assert ok.citacoes_nao_autorizadas == []
    truncado = auditar_sentenca(base + "\nConforme REsp 1.675/MS.", [_peca("ID-500")], [])
    assert any("1.675/MS" in c for c in truncado.citacoes_nao_autorizadas)
    apelacao_truncada = auditar_sentenca(
        base + "\nConforme TJRN - Apelação Criminal nº 0801498-50.2023 do TJRN.", [_peca("ID-500")], []
    )
    assert apelacao_truncada.citacoes_nao_autorizadas


def test_cache_do_indice_invalidado_quando_gabinete_cadastra_precedente(tmp_path, monkeypatch):
    monkeypatch.setattr(kb, "CUSTOM_KB_FILE", tmp_path / "custom_precedentes.json")
    _limpar_cache_base_fechada()
    referencia = "TJRN - Apelação Criminal nº 0809999-11.2024.8.20.5001"
    texto = _sentenca_sintetica(_DISPOSITIVO_CORRETO) + f"\nConforme {referencia}."
    antes = auditar_sentenca(texto, [_peca("ID-500")], [])
    assert antes.citacoes_nao_autorizadas, "precedente ainda não cadastrado deve ser bloqueado"

    kb.add_custom_precedente(
        referencia=referencia,
        titulo="Precedente do gabinete",
        texto_literal="A palavra da vítima tem especial relevo em violência doméstica.",
    )
    depois = auditar_sentenca(texto, [_peca("ID-500")], [])
    assert depois.citacoes_nao_autorizadas == []
    assert any("0809999-11.2024.8.20.5001" in c for c in depois.citacoes_juridicas_verificadas)
    _limpar_cache_base_fechada()


@pytest.mark.parametrize(("alterar", "mensagem"), [
    (lambda t: t.replace("2 (dois) anos, 4 (quatro) meses e 15 (quinze) dias", "1 (um) ano"), "pena total da espécie"),
    (lambda t: t.replace(" e 2 (dois) meses de detenção", ""), "detenção não consta"),
    (lambda t: t.replace("reclusão", "detenção"), "pena total da espécie"),
    (lambda t: t.replace(", além de 10 (dez) dias-multa, no valor unitário de 1/30 do salário mínimo vigente ao tempo do fato", ""), "10 dias-multa não consta"),
    (lambda t: t.replace("10 (dez) dias-multa", "5 (cinco) dias-multa"), "não corresponde à calculada"),
    (lambda t: t.replace("1/30", "1/15"), "valor unitário divergente"),
    (lambda t: t.replace("ao tempo do fato", "na data da sentença"), "valor unitário divergente"),
    (lambda t: t.replace("2 (dois) meses de detenção", "2 (dois) meses de detenção e 2 meses de detenção"), "repetida"),
    (lambda t: t.replace("2 anos, 6 meses e 15 dias", "2 anos"), "Soma explícita"),
    (lambda t: t.split(" A soma das penas")[0], "A soma das penas privativas de liberdade"),
])
def test_dispositivo_exige_totais_e_multa_sem_omissoes_ou_repeticoes(alterar, mensagem):
    dados = _dosimetria_sintetica()
    dados["crimes_calculados"][0]["pena_definitiva"]["anos"] = 1
    dados["crimes_calculados"].append({
        "crime_spec": {"especie_pena": "reclusao"},
        "pena_definitiva": {"anos": 1},
        "dias_multa": 0,
    })
    laudo = auditar_sentenca(f"III – DISPOSITIVO\n\n{alterar(_DISPOSITIVO_CORRETO)}", [], [], dosimetria_dados=dados)
    assert not laudo.aprovada_sem_alucinacao
    assert any(mensagem in erro for erro in laudo.penas_divergentes), laudo.penas_divergentes


def test_dispositivo_misto_confere_veredictos_fundamento_regimes_e_reparacao():
    dados = _dosimetria_sintetica()
    dados["crimes_calculados"][0]["crime_spec"]["artigo_formatado"] = "art. 129, § 13, do CP"
    dados["crimes_calculados"][1]["crime_spec"]["artigo_formatado"] = "art. 147, caput, do CP"
    dados["crimes_nao_condenados"] = [{
        "crime_code": "art_150_cp", "artigo_formatado": "art. 150, caput, do CP",
        "veredicto": "absolver", "fundamento_legal": "art. 386, inciso VII, do CPP",
    }]
    dados["regime_por_especie"] = {"reclusao": "fechado", "detencao": "semiaberto"}
    dados["indenizacao_dano_moral_tema983"] = 1250.50
    dispositivo = (
        "III – DISPOSITIVO\n\n"
        "CONDENAR pelo art. 129, § 13, do Código Penal e pelo art. 147, caput, do CP.\n\n"
        "ABSOLVER quanto ao art. 150, caput, do CP, com fulcro no art. 386, inciso VII, do CPP.\n\n"
        "Fixo 2 anos, 4 meses e 15 dias de reclusão e 2 meses de detenção, além de 10 dias-multa, no valor unitário de 1/30 do salário mínimo vigente ao tempo do fato. "
        "Regime inicial fechado quanto à pena de reclusão e regime inicial semiaberto quanto à pena de detenção. "
        "A soma das penas privativas de liberdade é de 2 anos, 6 meses e 15 dias.\n\n"
        "Fixo reparação mínima em R$ 1.250,50."
    )
    assert auditar_sentenca(dispositivo, [], [], dosimetria_dados=dados).penas_divergentes == []
    alterado = (
        dispositivo.replace("art. 147, caput, do CP", "art. 147, § 1º, do CP")
        .replace("art. 129, § 13, do Código Penal", "art. 129, § 13, da Lei nº 11.340/2006")
        .replace("inciso VII", "inciso II")
        .replace("semiaberto", "aberto")
        .replace("R$ 1.250,50", "R$ 1.250,00")
    )
    erros = auditar_sentenca(alterado, [], [], dosimetria_dados=dados).penas_divergentes
    for mensagem in (
        "condenar para art. 147", "condenar para art. 129", "Fundamento do comando absolver",
        "Regime aberto divergente", "Valor de reparação",
    ):
        assert any(mensagem in erro for erro in erros), erros


def test_dispositivo_sem_condenacao_rejeita_multa_e_reparacao_indevidas():
    dados = {"sem_condenacao": True}
    dispositivo = (
        "III – DISPOSITIVO\n\nABSOLVER o acusado. Fixo 10 dias-multa, no valor unitário de 1/30 do salário mínimo vigente ao tempo do fato. "
        "Regime inicial aberto. Fixo reparação mínima em R$ 200,00."
    )
    erros = auditar_sentenca(dispositivo, [], [], dosimetria_dados=dados).penas_divergentes
    for mensagem in ("Multa citada", "Regime aberto", "Valor de reparação"):
        assert any(mensagem in erro for erro in erros), erros


@pytest.mark.parametrize(("texto", "mensagem"), [
    ("Fixo um ano de reclusão.", "Quantifique em algarismos"),
    ("Aplico a pena de multa.", "Comando de multa sem quantidade"),
    ("Condeno ao pagamento de dez dias-multa.", "Quantifique em algarismos"),
])
def test_dispositivo_sem_condenacao_bloqueia_ate_penas_sem_algarismos(texto, mensagem):
    laudo = auditar_sentenca(f"III – DISPOSITIVO\n\nABSOLVO. {texto}", [], [], dosimetria_dados={"sem_condenacao": True})
    assert any(mensagem in erro for erro in laudo.penas_divergentes), laudo.penas_divergentes


def test_dispositivo_bloqueia_pena_com_extenso_e_algarismos_contraditorios():
    texto = _DISPOSITIVO_CORRETO.replace("2 (dois) anos", "2 (um) anos")
    laudo = auditar_sentenca(_sentenca_sintetica(texto), [_peca("ID-500")], [], dosimetria_dados=_dosimetria_sintetica())
    assert any("contradiz sua redação por extenso" in erro for erro in laudo.penas_divergentes)


def test_crivo_seccional_recusa_dispositivo_do_demo_sem_um_total():
    from app.generation.sectional_pipeline import (
        SecaoRascunho, SecaoTipo, construir_dossie_secao, default_in_flight_gating_validator,
    )
    from app.generation.sentence_generator import calcular_secoes_deterministicas

    bundle = get_demo_case_bundle()
    resultado = calcular_secoes_deterministicas(bundle)
    secao = SecaoTipo.DISPOSITIVO
    dossie = construir_dossie_secao(secao, bundle, resultado=resultado)
    corpo = resultado.secao(secao)
    assert dossie.comandos_dispositivo is not None
    quantia = dossie.comandos_dispositivo.penas_por_especie["detencao"].format_extenso()
    truncado = corpo.replace(f"{quantia} de detenção", "[pena retirada]")
    assert truncado != corpo
    rascunho = SecaoRascunho(secao=secao, texto_redigido=truncado, versao_tentativa=1)
    gate = default_in_flight_gating_validator(secao, rascunho, dossie)
    laudo = auditar_secao(secao, truncado, resultado.pecas, [], comandos_dispositivo=dossie.comandos_dispositivo)
    assert not gate.aprovado
    assert not laudo.aprovado
    assert any("detenção não consta" in erro for erro in gate.erros)
    assert any("detenção não consta" in erro for erro in laudo.erros)
