"""TASK-056. Valoração do juízo com fontes fictícias.

CPP, arts. 155, 381, III, e 387, § 1º:
https://www.planalto.gov.br/ccivil_03/decreto-lei/del3689compilado.htm
A suíte verifica registro e redação. A tarefa não altera marcos de vigência.
"""
from __future__ import annotations

import pytest

from app.core.errors import DadosInsuficientesError
from app.generation.case_script_engine import gerar_script_estruturado_caso
from app.generation.gemini_integration import importar_e_auditar_sentenca_externa
from app.generation.sectional_pipeline import SecaoTipo, SectionalPipelineEngine
from app.generation.sentence_generator import calcular_secoes_deterministicas, gerar_sentenca_estruturada
from tests.test_geracao_v2 import _cenario, _veredictos


def _registro(elemento, conclusao, fundamento, favoravel=None, contraria=None):
    def referencias(ref):
        return [{"fonte_id": ref[0], "trecho": ref[1]}] if ref else []

    return {
        "elemento": elemento,
        "conclusao": conclusao,
        "fundamento": fundamento,
        "provas_favoraveis": referencias(favoravel),
        "provas_contrarias": referencias(contraria),
    }


def _config(bundle, codigo, *registros):
    override = _veredictos(bundle)
    next(c for c in override["crimes_config"] if c["codigo"] == codigo)["elementos_provados"] = list(registros)
    return override


def test_veredicto_nao_atesta_laudo_nem_harmonia_das_provas():
    bundle = _cenario(1)
    bundle["raw_documents"] = [
        p for p in bundle["raw_documents"] if "laudo" not in (p.get("titulo") or "").lower()
    ]
    sentenca = gerar_sentenca_estruturada(bundle, _veredictos(bundle), use_ollama_if_available=False)

    assert "[COMPLETAR PELO JUÍZO: lesão corporal da imputação]" in sentenca.secao_merito
    assert "em plena harmonia" not in sentenca.secao_merito
    assert "a materialidade e a autoria do delito" not in sentenca.secao_merito.lower()


def test_depoimentos_divergentes_mantem_trechos_literais_e_conclusao_do_juizo():
    bundle = _cenario(1)
    trecho_a = "Eu ouvi duas frases em 2024-01-15."
    trecho_b = "Eu ouvi apenas uma frase."
    bundle["transcriptions"] += [
        {"media_id": "ID-MIDIA-FICTICIO-A", "segmentos": [{"texto_literal": trecho_a}]},
        {"media_id": "ID-MIDIA-FICTICIO-B", "segmentos": [{"texto": trecho_b}]},
    ]
    registro = _registro(
        "promessa_mal_grave", "indeterminado", "Os depoimentos divergem sobre o ponto em análise.",
        favoravel=("ID-MIDIA-FICTICIO-A", trecho_a), contraria=("ID-MIDIA-FICTICIO-B", trecho_b),
    )
    override = _config(bundle, "art_147_cp", registro)
    resultado = calcular_secoes_deterministicas(bundle, override)
    merito = resultado.secao(SecaoTipo.MERITO)

    assert f'Prova favorável: ID-MIDIA-FICTICIO-A: “{trecho_a}”' in merito
    assert f'Prova contrária: ID-MIDIA-FICTICIO-B: “{trecho_b}”' in merito
    assert "Os depoimentos divergem" in merito
    assert "[COMPLETAR PELO JUÍZO: conclusão sobre promessa de mal injusto e grave]" in merito

    script = gerar_script_estruturado_caso(bundle, override, pseudonimizar=True)
    assert "Prova favorável: ID-MIDIA-FICTICIO-A." in script.script_markdown
    assert registro["fundamento"] not in script.script_markdown

    registro["provas_contrarias"][0]["fonte_id"] = "ID-MIDIA-FICTICIO-A"
    with pytest.raises(DadosInsuficientesError, match="trecho literal de uma fonte"):
        calcular_secoes_deterministicas(bundle, override)


def test_certidao_de_ciencia_posterior_nao_vira_ciencia_previa():
    bundle = _cenario(2)
    trecho = "A ciência ocorreu depois do fato narrado."
    fonte = "ID-9900199"
    bundle["raw_documents"].append({
        "doc_id": fonte, "titulo": "Certidão de Intimação", "conteudo": trecho,
    })
    registro = _registro(
        "ciencia_previa", "nao_provado", "A data comprovada é posterior à conduta.",
        contraria=(fonte, trecho),
    )
    override = _config(bundle, "art_24a_lmp", registro)
    resultado = calcular_secoes_deterministicas(bundle, override)

    assert "Ciência prévia. Reconhecido como não provado." in resultado.secao(SecaoTipo.MERITO)
    assert f'Prova contrária: {fonte}: “{trecho}”' in resultado.secao(SecaoTipo.MERITO)
    assert "Comprovados o conhecimento prévio" not in resultado.secao(SecaoTipo.MERITO)


def test_script_e_importacao_nao_substituem_registros_por_suposicao():
    bundle = _cenario(1)
    override = _veredictos(bundle)
    deterministica = gerar_sentenca_estruturada(bundle, override, use_ollama_if_available=False)
    script = gerar_script_estruturado_caso(bundle, override, pseudonimizar=True)
    assert "Conclusões probatórias registradas pelo juízo" in script.script_markdown
    assert "[COMPLETAR PELO JUÍZO: situação prisional do réu]" in script.script_markdown

    adulterada = deterministica.texto_completo.replace(
        deterministica.secao_merito, deterministica.secao_merito + "\n\nO depoimento confirmou a autoria."
    )
    importada, auditoria, telemetria = importar_e_auditar_sentenca_externa(
        bundle, adulterada, custom_dosimetria_override=override, auto_blindar_auditoria=False,
    )
    assert importada.secao_merito == deterministica.secao_merito
    assert importada.secao_dispositivo == deterministica.secao_dispositivo
    assert "O depoimento confirmou a autoria." not in importada.texto_completo
    assert importada.marcadores_pendentes == auditoria.marcadores_pendentes
    assert "merito" in telemetria["secoes_fallback"]


def test_seccional_mantem_merito_e_dispositivo_deterministicos(monkeypatch):
    bundle = _cenario(1)
    override = _veredictos(bundle)
    motor = SectionalPipelineEngine(use_ollama=True)
    chamadas = []

    def redigir(secao, *_):
        chamadas.append(secao)
        return motor._texto_deterministico(secao)

    monkeypatch.setattr(motor, "_redigir_llm", redigir)
    monkeypatch.setattr("app.generation.sectional_pipeline.resolver_modelo_ollama", lambda: {})
    sentenca = motor.executar_pipeline(bundle, custom_dosimetria_override=override)
    base = gerar_sentenca_estruturada(bundle, override, use_ollama_if_available=False)
    assert set(chamadas) == {SecaoTipo.RELATORIO, SecaoTipo.PRELIMINARES, SecaoTipo.DOSIMETRIA}
    assert sentenca.secao_merito == base.secao_merito
    assert sentenca.secao_dispositivo == base.secao_dispositivo
