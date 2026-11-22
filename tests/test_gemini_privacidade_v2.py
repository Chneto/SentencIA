# -*- coding: utf-8 -*-
"""
Gemini e pseudonimização v2.0.0 (Agente C):

- envio externo só com ``settings.permitir_envio_externo`` E consentimento na requisição;
- chave vinda do cofre (``app.core.secrets``);
- ``google-genai`` opcional com erro claro (sem cair em outro motor dizendo "concluído");
- sem chave: geração determinística local executada UMA vez e rotulada honestamente;
- sem auto-blindagem silenciosa (substituição só quando solicitada, e sinalizada);
- pseudonimização com nº CNJ, prenomes isolados, nomes em arquivos, mapa determinístico + hash.
"""

import copy
import dataclasses
import random
import sys
import types
from types import SimpleNamespace

import pytest

import app.generation.case_script_engine as cse
import app.generation.gemini_integration as gi
from app.core.case_context import obter_contexto
from app.core.errors import DadosInsuficientesError, DecisaoJudicialPendenteError, EnvioExternoNaoAutorizadoError
from app.demo.demo_case import get_demo_case_bundle
from app.generation.case_script_engine import (
    calcular_hash_mapa,
    construir_mapa_pseudonimizacao,
    despseudonimizar_texto,
    gerar_script_estruturado_caso,
    pseudonimizar_texto,
)
from app.generation.sentence_generator import gerar_sentenca_estruturada

NOME_OFENDIDA_DEMO = "MARIANA ALVES FERREIRA"
NOME_REU_DEMO = "CARLOS EDUARDO ROCHA MENDES"


# ------------------------------------------------------------------------------
# Auxiliares
# ------------------------------------------------------------------------------


def _instalar_genai_falso(monkeypatch, texto_resposta="", erro=None):
    chamadas = {"clientes": 0, "conteudos": []}

    class _Models:
        def generate_content(self, model, contents, config=None):
            chamadas["conteudos"].append(contents)
            chamadas["modelo"] = model
            if erro is not None:
                raise erro
            return SimpleNamespace(text=texto_resposta)

    class _Client:
        def __init__(self, api_key):
            chamadas["clientes"] += 1
            chamadas["api_key"] = api_key
            self.models = _Models()

    fake_google = types.ModuleType("google")
    fake_genai = types.ModuleType("google.genai")
    fake_types = types.ModuleType("google.genai.types")
    fake_types.GenerateContentConfig = lambda **kw: kw
    fake_genai.Client = _Client
    fake_genai.types = fake_types
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)
    return chamadas


def _permitir_envio(monkeypatch, permitir=True):
    settings = gi.load_settings().model_copy(update={"permitir_envio_externo": permitir})
    monkeypatch.setattr(gi, "load_settings", lambda: settings)


def _sem_chave(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr("app.core.secrets.get_secret", lambda nome: "")


@pytest.fixture(scope="module")
def sentenca_demo():
    return gerar_sentenca_estruturada(get_demo_case_bundle(), use_ollama_if_available=False)


# ------------------------------------------------------------------------------
# Autorização de envio externo
# ------------------------------------------------------------------------------


def test_envio_externo_bloqueado_quando_desabilitado_nas_configuracoes(monkeypatch):
    chamadas = _instalar_genai_falso(monkeypatch, texto_resposta="qualquer")
    _permitir_envio(monkeypatch, permitir=False)
    with pytest.raises(EnvioExternoNaoAutorizadoError):
        gi.gerar_sentenca_via_gemini_api(
            get_demo_case_bundle(), api_key="chave-teste", consentimento_envio_externo=True
        )
    assert chamadas["clientes"] == 0 and chamadas["conteudos"] == []


def test_envio_externo_bloqueado_sem_consentimento_na_requisicao(monkeypatch):
    chamadas = _instalar_genai_falso(monkeypatch, texto_resposta="qualquer")
    _permitir_envio(monkeypatch, permitir=True)
    with pytest.raises(EnvioExternoNaoAutorizadoError):
        gi.gerar_sentenca_via_gemini_api(get_demo_case_bundle(), api_key="chave-teste")
    assert chamadas["clientes"] == 0


def test_envio_autorizado_usa_script_pseudonimizado_e_restaura_nomes(monkeypatch, sentenca_demo):
    demo = get_demo_case_bundle()
    mapa = cse.construir_mapa_do_contexto(obter_contexto(demo))
    resposta = pseudonimizar_texto(sentenca_demo.texto_completo, mapa)
    assert NOME_OFENDIDA_DEMO not in resposta
    chamadas = _instalar_genai_falso(monkeypatch, texto_resposta=resposta)
    _permitir_envio(monkeypatch, permitir=True)

    sentenca, auditoria, script, tel = gi.gerar_sentenca_via_gemini_api(
        demo, api_key="chave-teste", model_name="gemini-teste", consentimento_envio_externo=True
    )
    assert chamadas["clientes"] == 1 and chamadas["api_key"] == "chave-teste"
    enviado = chamadas["conteudos"][0]
    for real in (NOME_OFENDIDA_DEMO, NOME_REU_DEMO, "Mariana", "0804590-12.2025.8.00.0001"):
        assert real not in enviado, f"dado real '{real}' vazou para o serviço externo"
    assert tel["modo_gemini"] == "API_DIRETA_GOOGLE_GENAI"
    assert tel["gemini_utilizado"] is True and tel["envio_externo"] is True
    assert tel["mapa_pseudonimizacao"] == "informado"
    assert NOME_OFENDIDA_DEMO in sentenca.texto_completo
    assert "[OFENDIDA]" not in sentenca.texto_completo
    assert auditoria.aprovada_sem_alucinacao is True, auditoria.model_dump()
    assert auditoria.secoes_fallback == []
    assert "Gemini API (gemini-teste)" in sentenca.motor_utilizado


def test_chave_do_cofre_e_usada(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr("app.core.secrets.get_secret", lambda nome: "chave-do-cofre" if nome == "gemini_api_key" else "")
    assert gi.resolver_chave_gemini("") == "chave-do-cofre"
    assert gi.resolver_chave_gemini("  chave-da-requisicao ") == "chave-da-requisicao"


# ------------------------------------------------------------------------------
# google-genai opcional / falhas: erro claro, nunca "concluído" com outro motor
# ------------------------------------------------------------------------------


def test_google_genai_ausente_gera_erro_claro(monkeypatch):
    _permitir_envio(monkeypatch, permitir=True)
    monkeypatch.setitem(sys.modules, "google.genai", None)  # força ImportError
    with pytest.raises(gi.GeminiIndisponivelError) as exc:
        gi.gerar_sentenca_via_gemini_api(
            get_demo_case_bundle(), api_key="chave-teste", consentimento_envio_externo=True
        )
    assert "google-genai" in exc.value.mensagem
    assert exc.value.to_dict()["erro"] == "gemini_indisponivel"


@pytest.mark.parametrize("texto,erro", [("", None), ("x", RuntimeError("quota"))])
def test_falha_ou_resposta_vazia_da_api_gera_erro(monkeypatch, texto, erro):
    _instalar_genai_falso(monkeypatch, texto_resposta=texto, erro=erro)
    _permitir_envio(monkeypatch, permitir=True)
    with pytest.raises(gi.GeminiIndisponivelError):
        gi.gerar_sentenca_via_gemini_api(
            get_demo_case_bundle(), api_key="chave-teste", consentimento_envio_externo=True
        )


def test_sem_chave_gera_localmente_uma_unica_vez_e_rotula_honestamente(monkeypatch):
    _sem_chave(monkeypatch)
    contador = {"n": 0}
    original = gi.gerar_sentenca_estruturada

    def _contando(*args, **kwargs):
        contador["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(gi, "gerar_sentenca_estruturada", _contando)
    sentenca, auditoria, script, tel = gi.gerar_sentenca_via_gemini_api(get_demo_case_bundle())
    assert contador["n"] == 1
    assert tel["gemini_utilizado"] is False
    assert tel["envio_externo"] is False
    assert tel["modo_gemini"] == "SINTESE_LOCAL_SEM_GEMINI"
    assert "Gemini NÃO utilizado" in sentenca.motor_utilizado
    assert auditoria.aprovada_sem_alucinacao is True


# ------------------------------------------------------------------------------
# Importação: sem auto-blindagem silenciosa
# ------------------------------------------------------------------------------


def _texto_com_dosimetria_quebrada(sent):
    return "\n\n".join(
        [
            sent.secao_cabecalho,
            sent.secao_relatorio,
            sent.secao_preliminares,
            sent.secao_merito,
            "3. Da Dosimetria da Pena\n\nPena fixada diretamente sem análise dos vetores judiciais.",
            sent.secao_dispositivo,
        ]
    )


def test_importacao_sem_blindagem_nao_substitui_secoes(sentenca_demo):
    texto = _texto_com_dosimetria_quebrada(sentenca_demo)
    sentenca, auditoria, tel = gi.importar_e_auditar_sentenca_externa(get_demo_case_bundle(), texto)
    assert auditoria.aprovada_sem_alucinacao is False
    assert auditoria.conformidade_oito_circunstancias_art59 is False
    assert "sem análise dos vetores judiciais" in sentenca.secao_dosimetria
    assert auditoria.secoes_fallback == []
    assert tel["reparo_deterministico_aplicado"] is False
    assert tel["blindagem_solicitada"] is False


def test_importacao_com_blindagem_solicitada_sinaliza_substituicao(sentenca_demo):
    texto = _texto_com_dosimetria_quebrada(sentenca_demo)
    sentenca, auditoria, tel = gi.importar_e_auditar_sentenca_externa(
        get_demo_case_bundle(), texto, auto_blindar_auditoria=True
    )
    assert auditoria.aprovada_sem_alucinacao is True
    assert "dosimetria" in auditoria.secoes_fallback
    assert "dosimetria" in tel["secoes_fallback"]
    assert tel["reparo_deterministico_aplicado"] is True
    assert "substituídas pelo modelo determinístico" in sentenca.motor_utilizado
    assert any(d["camada"].startswith("6.") for d in auditoria.detalhes_camadas)


def test_importacao_nao_preenche_secoes_ausentes_silenciosamente(sentenca_demo):
    # Texto sem cabeçalho e sem preliminares/mérito separados: nada é completado com o modelo.
    texto = "I – RELATÓRIO\n\nRelato breve.\n\nII – FUNDAMENTAÇÃO\n\nFundamentos.\n\nIII – DISPOSITIVO\n\nDecido."
    sentenca, auditoria, tel = gi.importar_e_auditar_sentenca_externa(get_demo_case_bundle(), texto)
    assert sentenca.secao_cabecalho == ""
    assert sentenca.secao_preliminares == ""
    assert sentenca_demo.secao_preliminares not in sentenca.texto_completo
    assert "cabecalho" in tel["secoes_ausentes_no_texto"]
    assert auditoria.secoes_fallback == []


def test_importacao_confere_hash_do_mapa(sentenca_demo):
    demo = get_demo_case_bundle()
    script = gerar_script_estruturado_caso(demo, pseudonimizar=True)
    texto = pseudonimizar_texto(sentenca_demo.texto_completo, script.mapa_reversao)
    with pytest.raises(ValueError):
        gi.importar_e_auditar_sentenca_externa(
            demo, texto, mapa_reversao=script.mapa_reversao, mapa_hash="0" * 64
        )
    sentenca, _aud, tel = gi.importar_e_auditar_sentenca_externa(
        demo, texto, mapa_reversao=script.mapa_reversao, mapa_hash=script.mapa_hash
    )
    assert tel["mapa_pseudonimizacao"] == "informado"
    assert NOME_OFENDIDA_DEMO in sentenca.texto_completo


# ------------------------------------------------------------------------------
# Pseudonimização
# ------------------------------------------------------------------------------


def test_pseudonimizacao_cobre_cnj_prenomes_e_nomes_de_arquivo():
    meta = SimpleNamespace(
        nome_ofendida="MARINA LOPES FERREIRA",
        nome_reu="CARLOS EDUARDO FERREIRA",
        numero_processo="0804590-12.2025.8.00.0001",
        autos_mpu_apenso="MPU nº 0801122-44.2025.8.00.0001",
    )
    texto = (
        "Processo 0804590-12.2025.8.00.0001, apenso 0801122-44.2025.8.00.0001. "
        "Marina relatou que Carlos a agrediu. Arquivo: Depoimento da Vítima Marina_001.mp4. "
        "Precedente público: TJRN - Apelação Criminal nº 0801498-50.2023.8.20.5114. "
        "Aplica-se a Lei Maria da Penha."
    )
    mapa = construir_mapa_pseudonimizacao(meta, texto)
    pseudo = pseudonimizar_texto(texto, mapa)
    for real in ("0804590-12.2025.8.00.0001", "0801122-44.2025.8.00.0001", "Marina", "Carlos"):
        assert real not in pseudo, real
    assert "[PROCESSO]" in pseudo and "[PROCESSO_APENSO]" in pseudo
    assert "[PRENOME_OFENDIDA]_001.mp4" in pseudo
    # Precedente público e a Lei Maria da Penha são preservados.
    assert "0801498-50.2023.8.20.5114" in pseudo
    assert "Lei Maria da Penha" in pseudo
    assert despseudonimizar_texto(pseudo, mapa) == texto


def test_prenome_homografo_em_minusculas_nao_e_trocado():
    meta = SimpleNamespace(nome_ofendida="Vitória Santos Lima", nome_reu="Paulo Rocha Dias")
    texto = "Vitória relatou os fatos; a vitória processual não importa. VITÓRIA confirmou."
    mapa = construir_mapa_pseudonimizacao(meta, texto)
    pseudo = pseudonimizar_texto(texto, mapa)
    assert "Vitória relatou" not in pseudo and "VITÓRIA confirmou" not in pseudo
    assert "a vitória processual" in pseudo


def test_mapa_e_deterministico_independente_da_ordem():
    meta = SimpleNamespace(nome_ofendida="Ana Paula Souza", nome_reu="Bruno Lima Costa")
    cpf_a = ".".join(("111", "222", "333")) + "-44"
    cpf_b = ".".join(("999", "888", "777")) + "-66"
    tel_a = "(" + "84) " + "99988-7766"
    tel_b = "(" + "84) " + "98877-6655"
    linhas = [
        "Testemunha: Carla Mendes Rocha",
        "Testemunha: Daniel Alves Pinto",
        f"CPF {cpf_a} e CPF {cpf_b}",
        "Filha: Elisa Souza Lima",
        f"Telefone {tel_a} e {tel_b}",
    ]
    extras = ["Sd. PM Gustavo Henrique Reis", "Fabio Nunes Prado"]
    mapa_ref = construir_mapa_pseudonimizacao(meta, "\n".join(linhas), extra_testemunhas=extras)
    rnd = random.Random(7)
    for _ in range(5):
        l2, e2 = linhas[:], extras[:]
        rnd.shuffle(l2)
        rnd.shuffle(e2)
        assert construir_mapa_pseudonimizacao(meta, "\n".join(l2), extra_testemunhas=e2) == mapa_ref
    assert calcular_hash_mapa(mapa_ref) == calcular_hash_mapa(dict(reversed(list(mapa_ref.items()))))


def test_script_pseudonimizado_inclui_json_sem_nomes_e_hash_do_mapa():
    script = gerar_script_estruturado_caso(get_demo_case_bundle(), pseudonimizar=True)
    assert script.mapa_hash == calcular_hash_mapa(script.mapa_reversao)
    import json

    json_txt = json.dumps(script.script_json, ensure_ascii=False)
    for real in (NOME_OFENDIDA_DEMO, NOME_REU_DEMO, "0804590-12.2025.8.00.0001"):
        assert real not in json_txt
        assert real not in script.script_markdown
    script_aberto = gerar_script_estruturado_caso(get_demo_case_bundle(), pseudonimizar=False)
    assert script_aberto.mapa_reversao == {} and script_aberto.mapa_hash == ""


# ------------------------------------------------------------------------------
# Juiz decide / dados insuficientes
# ------------------------------------------------------------------------------


def _bundle_real_a_partir_do_demo():
    bundle = copy.deepcopy(get_demo_case_bundle())
    bundle["is_demo"] = False
    return bundle


def test_script_de_caso_real_exige_veredicto_explicito():
    bundle = _bundle_real_a_partir_do_demo()
    with pytest.raises(DecisaoJudicialPendenteError) as exc:
        gerar_script_estruturado_caso(bundle, pseudonimizar=True)
    assert exc.value.crimes_pendentes

    crimes = obter_contexto(bundle).meta.crimes_imputados
    override = {"crimes_config": [{"codigo": c, "veredicto": "condenar"} for c in crimes]}
    script = gerar_script_estruturado_caso(bundle, custom_dosimetria_override=override, pseudonimizar=True)
    assert script.alertas == []
    assert script.script_json["simulacao_presume_condenacao"] is False


def test_simulacao_sinaliza_presuncao_de_condenacao():
    script = gerar_script_estruturado_caso(_bundle_real_a_partir_do_demo(), exigir_veredicto=False)
    assert script.script_json["simulacao_presume_condenacao"] is True
    assert any("presume" in a for a in script.alertas)
    assert "SIMULAÇÃO" in script.script_markdown


def test_data_do_fato_ausente_levanta_dados_insuficientes(monkeypatch):
    demo = get_demo_case_bundle()
    ctx = obter_contexto(demo)
    meta_sem_data = ctx.meta.model_copy(update={"data_fato": None})
    ctx_sem_data = dataclasses.replace(ctx, meta=meta_sem_data)
    monkeypatch.setattr(cse, "obter_contexto", lambda bundle: ctx_sem_data)
    with pytest.raises(DadosInsuficientesError) as exc:
        cse.executar_motores_completo(demo)
    assert exc.value.campos == ["data_fato"]
