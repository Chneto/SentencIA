import json
from pathlib import Path
from fastapi.testclient import TestClient

from app.knowledge.knowledge_base import (
    ACERVO_COMPLETO_FILE,
    CUSTOM_KB_FILE,
    DOUTRINA_VERIFICADA,
    SUMULAS_E_TEMAS_VERIFICADOS,
    add_custom_precedente,
    buscar_precedente_por_id_ou_referencia,
)
from app.generation.hallucination_auditor import auditar_sentenca
from app.demo.demo_case import get_demo_case_bundle
from app.ingestion.laya_classifier import classificar_pecas_autos, extrair_metadados_processo
from app.generation.sentence_generator import gerar_sentenca_estruturada
from app.main import app


def test_acervo_completo_integridade_e_veracidade():
    """Garante a completude, autenticidade e campos obrigatórios do acervo oficial."""
    assert len(SUMULAS_E_TEMAS_VERIFICADOS) >= 35, f"Acervo deve conter no mínimo 35 verbetes (atual: {len(SUMULAS_E_TEMAS_VERIFICADOS)})"
    assert len(DOUTRINA_VERIFICADA) >= 4, f"Doutrina verificada deve conter no mínimo 4 obras verificadas (atual: {len(DOUTRINA_VERIFICADA)})"

    tribunais = {item.get("tribunal") for item in SUMULAS_E_TEMAS_VERIFICADOS}
    assert {"STF", "STJ", "FONAVID", "CNJ"}.issubset(tribunais), f"Tribunais incompletos: {tribunais}"

    categorias = {item.get("categoria") for item in SUMULAS_E_TEMAS_VERIFICADOS}
    assert {"SUMULAS", "TEMAS_REPETITIVOS", "STF_VINCULANTE", "TESES_STJ", "FONAVID", "NORMAS_CNJ"}.issubset(categorias)

    # Cada item deve conter chaves mínimas válidas
    for it in SUMULAS_E_TEMAS_VERIFICADOS:
        assert it.get("id"), "Item sem ID"
        assert it.get("referencia"), f"Item {it['id']} sem referencia"
        assert it.get("titulo"), f"Item {it['id']} sem titulo"
        assert it.get("texto_literal"), f"Item {it['id']} sem texto_literal"
        assert len(it["texto_literal"]) >= 20, f"Texto literal muito curto no item {it['id']}"

    # Validação de autores doutrinários verificados
    autores_esperados = [
        "Maria Berenice Dias",
        "Rogério Sanches Cunha",
        "Alice Bianchini",
    ]
    autores_presentes = " ".join(d["autor"] for d in DOUTRINA_VERIFICADA)
    for esperado in autores_esperados:
        assert esperado in autores_presentes, f"Autor doutrinário obrigatório ausente: {esperado}"


def test_acervo_persistencia_json_em_disco(tmp_path, monkeypatch):
    """Garante que os arquivos JSON em data/knowledge_base/ existem e contêm os dados sincronizados."""
    import app.knowledge.knowledge_base as kb

    assert ACERVO_COMPLETO_FILE.exists(), f"Arquivo persistente não existe: {ACERVO_COMPLETO_FILE}"
    conteudo = json.loads(ACERVO_COMPLETO_FILE.read_text(encoding="utf-8"))
    assert "sumulas_e_temas" in conteudo
    assert "doutrina" in conteudo
    assert len(conteudo["sumulas_e_temas"]) == len(SUMULAS_E_TEMAS_VERIFICADOS)

    # Adição de precedente no arquivo customizado — isolada em tmp_path para nunca regravar o
    # acervo real do gabinete (data/knowledge_base/custom_precedentes.json) durante a suíte.
    arquivo_custom = tmp_path / CUSTOM_KB_FILE.name
    monkeypatch.setattr(kb, "CUSTOM_KB_FILE", arquivo_custom)
    add_custom_precedente(
        referencia="Enunciado Teste 99 TJRN",
        titulo="Enunciado Teste de Gabinete",
        texto_literal="Texto de teste para verificação da persistência em disco do gabinete.",
        tipo="PRECEDENTE_TJ",
        tribunal="TJRN",
    )
    assert arquivo_custom.exists()
    conteudo_custom = json.loads(arquivo_custom.read_text(encoding="utf-8"))
    assert any(c["referencia"] == "Enunciado Teste 99 TJRN" for c in conteudo_custom)


def test_busca_precedente_por_id_ou_referencia():
    """Garante a recuperação rápida de qualquer verbete do acervo por ID ou Referência."""
    item_588 = buscar_precedente_por_id_ou_referencia("SUMULA-588-STJ")
    assert item_588 is not None
    assert "restritiva de direitos" in item_588["texto_literal"]

    item_adc19 = buscar_precedente_por_id_ou_referencia("ADC 19/STF")
    assert item_adc19 is not None
    assert "constitucionalidade" in item_adc19["texto_literal"]

    item_adpf779 = buscar_precedente_por_id_ou_referencia("ADPF 779/STF")
    assert item_adpf779 is not None
    assert "legítima defesa da honra" in item_adpf779["texto_literal"].lower()


def test_auditor_valida_novas_categorias_e_bloqueia_ficticias():
    """Garante que a Camada 02 da auditoria valida todas as novas categorias autênticas e bloqueia invenções."""
    demo = get_demo_case_bundle()
    pecas = classificar_pecas_autos(demo["raw_documents"], is_demo=True)
    meta = extrair_metadados_processo(pecas, is_demo=True)

    sentenca_base = gerar_sentenca_estruturada(case_bundle=demo, use_ollama_if_available=False)

    # Injeta citações 100% autênticas do STF, FONAVID e STJ
    texto_com_citacoes_oficiais = sentenca_base.texto_completo + (
        "\nFundamentação suplementar com base em precedentes autênticos do acervo:\n"
        "1. Conforme ADC 19/STF e ADI 4424/STF, reafirma-se a natureza pública incondicionada e a validade da LMP;\n"
        "2. Aplica-se o Enunciado 47 FONAVID quanto à ineficácia de consentimento da ofendida no art. 24-A;\n"
        "3. Em consonância com a jurisprudência consolidada no Tema Repetitivo 1197/STJ, "
        "a aplicação da agravante da violência doméstica observa a sistemática legal vigente;\n"
        "4. Observa-se a diretriz da ADPF 779/STF quanto à vedação da tese da legítima defesa da honra."
    )

    audit_ok = auditar_sentenca(
        texto_sentenca=texto_com_citacoes_oficiais,
        pecas_autos=pecas,
        teses_defesa=meta.teses_defesa,
    )
    assert audit_ok.citacoes_nao_autorizadas == [], f"Citações oficiais foram indevidamente reprovadas: {audit_ok.citacoes_nao_autorizadas}"
    assert "ADC 19/STF" in audit_ok.citacoes_juridicas_verificadas or any("ADC 19" in c for c in audit_ok.citacoes_juridicas_verificadas)
    assert "ADI 4424/STF" in audit_ok.citacoes_juridicas_verificadas or any("ADI 4424" in c for c in audit_ok.citacoes_juridicas_verificadas)
    assert any("Enunciado 47" in c for c in audit_ok.citacoes_juridicas_verificadas)

    # Injeta citações ALUCINADAS / FICTÍCIAS para garantir bloqueio
    texto_com_alucinacoes = sentenca_base.texto_completo + (
        "\nCitações inexistentes que devem ser bloqueadas:\n"
        "Conforme Súmula 999/STJ, Tema Repetitivo 7777/STJ, ADPF 9999/STF, "
        "Enunciado 999 FONAVID e REsp 9.876.543/XY."
    )

    audit_reprovada = auditar_sentenca(
        texto_sentenca=texto_com_alucinacoes,
        pecas_autos=pecas,
        teses_defesa=meta.teses_defesa,
    )
    assert audit_reprovada.aprovada_sem_alucinacao is False
    assert len(audit_reprovada.citacoes_nao_autorizadas) >= 4
    assert any("999" in c for c in audit_reprovada.citacoes_nao_autorizadas)
    assert any("7777" in c for c in audit_reprovada.citacoes_nao_autorizadas)
    assert any("9.876.543" in c for c in audit_reprovada.citacoes_nao_autorizadas)


def test_api_knowledge_endpoints_and_filtering():
    """Garante que a rota /api/knowledge suporta listagem completa e filtros por categoria e busca textual."""
    client = TestClient(app)

    # 1. Listagem completa
    r_all = client.get("/api/knowledge")
    assert r_all.status_code == 200
    data_all = r_all.json()
    assert len(data_all["knowledge"]["sumulas_e_temas"]) >= 35
    assert len(data_all["knowledge"]["doutrina"]) >= 4

    # 2. Filtro por Categoria SUMULAS
    r_sum = client.get("/api/knowledge?categoria=SUMULAS")
    assert r_sum.status_code == 200
    data_sum = r_sum.json()
    items_sum = data_sum["knowledge"]["sumulas_e_temas"]
    assert len(items_sum) >= 4
    assert all(it["categoria"] == "SUMULAS" for it in items_sum)

    # 3. Filtro por Categoria FONAVID
    r_fonavid = client.get("/api/knowledge?categoria=FONAVID")
    assert r_fonavid.status_code == 200
    data_fonavid = r_fonavid.json()
    items_fonavid = data_fonavid["knowledge"]["sumulas_e_temas"]
    assert len(items_fonavid) >= 10
    assert all("FONAVID" in it["tribunal"] or it["categoria"] == "FONAVID" for it in items_fonavid)

    # 4. Filtro por Categoria STF_VINCULANTE
    r_stf = client.get("/api/knowledge?categoria=STF_VINCULANTE")
    assert r_stf.status_code == 200
    data_stf = r_stf.json()
    items_stf = data_stf["knowledge"]["sumulas_e_temas"]
    assert len(items_stf) >= 4
    assert all(it["tribunal"] == "STF" for it in items_stf)

    # 5. Busca textual (q=insignificância)
    r_q = client.get("/api/knowledge?q=insignificância")
    assert r_q.status_code == 200
    data_q = r_q.json()
    items_q = data_q["knowledge"]["sumulas_e_temas"]
    assert len(items_q) >= 1
    assert any("589" in it["referencia"] for it in items_q)


def test_auditor_bloqueia_variacoes_sintaticas_de_citacoes_ficticias():
    """Garante que a Camada 02 bloqueia todas as variações sintáticas de citações fictícias (nº, do STJ, Vinculante, etc.)."""
    demo = get_demo_case_bundle()
    pecas = classificar_pecas_autos(demo["raw_documents"], is_demo=True)
    meta = extrair_metadados_processo(pecas, is_demo=True)
    sentenca_base = gerar_sentenca_estruturada(case_bundle=demo, use_ollama_if_available=False)

    # 1. Testa variações sintáticas fictícias que antes passavam despercebidas
    texto_com_falsos_variados = sentenca_base.texto_completo + (
        "\nCitações adulteradas com sintaxes variadas que devem ser bloqueadas:\n"
        "1. Conforme Súmula 999 do STJ e Súmula nº 888/STJ;\n"
        "2. Aplica-se a Súmula Vinculante 999/STF;\n"
        "3. Em atenção ao Tema 7777 do STF e Tema nº 6666/STJ;\n"
        "4. Nos termos do Enunciado FONAVID nº 999, Enunciado 999/FONAVID e Enunciado 999 (FONAVID);\n"
        "5. Consoante Apelação Criminal nº 9999999-99.2023.8.20.5001;\n"
        "6. Conforme Resolução CNJ nº 999/2099;\n"
        "7. Conforme Recurso Especial 9.999.999/RN e REsp 9.876.543/XY."
    )

    audit_reprovada = auditar_sentenca(
        texto_sentenca=texto_com_falsos_variados,
        pecas_autos=pecas,
        teses_defesa=meta.teses_defesa,
    )
    assert audit_reprovada.aprovada_sem_alucinacao is False
    assert len(audit_reprovada.citacoes_nao_autorizadas) >= 7
    assert any("999" in c for c in audit_reprovada.citacoes_nao_autorizadas)
    assert any("888" in c for c in audit_reprovada.citacoes_nao_autorizadas)
    assert any("7777" in c for c in audit_reprovada.citacoes_nao_autorizadas)
    assert any("6666" in c for c in audit_reprovada.citacoes_nao_autorizadas)
    assert any("9999999" in c for c in audit_reprovada.citacoes_nao_autorizadas)
    assert any("9.876.543" in c for c in audit_reprovada.citacoes_nao_autorizadas)

    # 2. Testa variações sintáticas AUTÊNTICAS (devem ser aprovadas com 100%)
    texto_com_autenticos_variados = sentenca_base.texto_completo + (
        "\nCitações autênticas em formatos variados:\n"
        "1. Conforme Súmula 588 do STJ e Súmula nº 231/STJ;\n"
        "2. Aplica-se a Súmula Vinculante 56 do STF;\n"
        "3. Em consonância com o Tema 983 do STJ, Tema 1249/STJ, Tema 1186 do STJ e Tema 1197 do STJ;\n"
        "4. Observa-se o Enunciado FONAVID nº 45, o Enunciado 1 do FONAVID, o Enunciado 45/FONAVID e o Enunciado 37 (FONAVID);\n"
        "5. Conforme Resolução CNJ nº 492/2023;\n"
        "6. Conforme ADPF 779/STF;\n"
        "7. Conforme REsp 1.675.874/MS, Recurso Especial 1.675.874/MS e REsp 2.070.717/MG."
    )

    audit_aprovada = auditar_sentenca(
        texto_sentenca=texto_com_autenticos_variados,
        pecas_autos=pecas,
        teses_defesa=meta.teses_defesa,
    )
    assert audit_aprovada.citacoes_nao_autorizadas == []
    assert audit_aprovada.aprovada_sem_alucinacao is True


def test_novos_temas_e_enunciados_integridade():
    """Valida a presença e integridade dos novos precedentes consolidados (Tema 1249/STJ, Tema 1186/STJ, Enunciados FONAVID 37, 50, 52, 54)."""
    assert len(SUMULAS_E_TEMAS_VERIFICADOS) >= 60

    t1249 = buscar_precedente_por_id_ou_referencia("TEMA-1249-STJ")
    assert t1249 is not None
    assert "tutela inibitória" in t1249["texto_literal"]

    t1186 = buscar_precedente_por_id_ou_referencia("TEMA-1186-STJ")
    assert t1186 is not None
    assert "ECA" in t1186["titulo"]

    e37 = buscar_precedente_por_id_ou_referencia("ENUNCIADO-37-FONAVID")
    assert e37 is not None
    assert "ilícito penal" in e37["texto_literal"]

    e50 = buscar_precedente_por_id_ou_referencia("ENUNCIADO-50-FONAVID")
    assert e50 is not None
    assert "não se expressar" in e50["texto_literal"]


# ------------------------------------------------------------------------------
# v2.0.0 — acervo sem regravação a cada import, IDs únicos, versão do acervo e estilo real
# ------------------------------------------------------------------------------


def test_acervo_em_disco_so_e_gravado_quando_ausente_ou_desatualizado(tmp_path, monkeypatch):
    import app.knowledge.knowledge_base as kb

    destino = tmp_path / "acervo_completo.json"
    monkeypatch.setattr(kb, "ACERVO_COMPLETO_FILE", destino)
    assert kb._salvar_acervo_em_disco() is True  # ausente: grava
    mtime = destino.stat().st_mtime_ns
    assert kb._salvar_acervo_em_disco() is False  # já sincronizado: NÃO regrava
    assert destino.stat().st_mtime_ns == mtime
    destino.write_text("{corrompido", encoding="utf-8")
    assert kb._salvar_acervo_em_disco() is True  # ilegível: regrava
    assert len(json.loads(destino.read_text(encoding="utf-8"))["sumulas_e_temas"]) == len(SUMULAS_E_TEMAS_VERIFICADOS)
    assert not list(tmp_path.glob("*.tmp")), "escrita atômica não pode deixar temporários"


def test_ids_de_precedentes_do_gabinete_sao_unicos_e_versao_muda(tmp_path, monkeypatch):
    import app.knowledge.knowledge_base as kb

    arquivo = tmp_path / "custom_precedentes.json"
    monkeypatch.setattr(kb, "CUSTOM_KB_FILE", arquivo)
    v0 = kb.versao_acervo()
    a = kb.add_custom_precedente("Ref A", "Título A", "Texto literal suficientemente longo A.")
    b = kb.add_custom_precedente("Ref B", "Título B", "Texto literal suficientemente longo B.")
    assert a["id"] != b["id"]
    v1 = kb.versao_acervo()
    assert v1 != v0

    # Remove o primeiro item (exclusão manual): o próximo ID não pode repetir o de "Ref B".
    itens = [it for it in json.loads(arquivo.read_text(encoding="utf-8")) if it["referencia"] != "Ref A"]
    arquivo.write_text(json.dumps(itens, ensure_ascii=False), encoding="utf-8")
    c = kb.add_custom_precedente("Ref C", "Título C", "Texto literal suficientemente longo C.")
    ids = [it["id"] for it in json.loads(arquivo.read_text(encoding="utf-8"))]
    assert len(ids) == len(set(ids))
    assert c["id"] not in {b["id"]}
    assert kb.versao_acervo() != v1


def test_add_custom_precedente_rejeita_referencia_vazia(tmp_path, monkeypatch):
    import pytest
    import app.knowledge.knowledge_base as kb

    monkeypatch.setattr(kb, "CUSTOM_KB_FILE", tmp_path / "custom_precedentes.json")
    with pytest.raises(ValueError):
        kb.add_custom_precedente("   ", "Título", "Texto")


def test_perfil_padrao_sem_numeros_de_processos_reais():
    import re
    from app.knowledge.style_cloner import DEFAULT_STYLE_PROFILE, STYLE_PROFILE_FILE

    padrao_cnj = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")
    assert not padrao_cnj.search(json.dumps(DEFAULT_STYLE_PROFILE, ensure_ascii=False))
    distribuido = Path(__file__).resolve().parent.parent / "data" / "templates_gabinete" / "style_profile.json"
    if distribuido.exists():
        assert not padrao_cnj.search(distribuido.read_text(encoding="utf-8"))
    assert STYLE_PROFILE_FILE.name == "style_profile.json"


def test_perfil_antigo_com_numero_real_e_saneado(tmp_path, monkeypatch):
    import app.knowledge.style_cloner as sc

    arquivo = tmp_path / "style_profile.json"
    antigo = json.loads(json.dumps(sc.DEFAULT_STYLE_PROFILE))
    antigo["modelos_cadastrados"][1]["resumo"] = "Modelo oficial (.docx): Processo 0804590-12.2025.8.00.0001."
    arquivo.write_text(json.dumps(antigo, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sc, "STYLE_PROFILE_FILE", arquivo)
    perfil = sc.get_style_profile()
    assert "0804590-12" not in json.dumps(perfil, ensure_ascii=False)
    assert "0804590-12" not in arquivo.read_text(encoding="utf-8")


def test_amostra_de_estilo_guardada_integralmente_com_perfil_extraido(tmp_path, monkeypatch):
    import hashlib
    import app.knowledge.style_cloner as sc

    monkeypatch.setattr(sc, "STYLE_PROFILE_FILE", tmp_path / "style_profile.json")
    monkeypatch.setattr(sc, "AMOSTRAS_DIR", tmp_path / "amostras")
    corpo_longo = "Texto de fundamentação. " * 40
    amostra = (
        "SENTENÇA\n\n"
        "I – RELATÓRIO\n\n"
        "Vistos e examinados estes autos de ação penal. O Ministério Público ofereceu denúncia.\n\n"
        "É o breve relatório. Decido.\n\n"
        "II – FUNDAMENTAÇÃO\n\n"
        f"Com efeito, a materialidade está provada. Ademais, a autoria é certa. Com efeito, nada a acrescentar. {corpo_longo}\n\n"
        "III – DISPOSITIVO\n\n"
        "Isso posto, JULGO PROCEDENTE a denúncia. Publique-se."
    )
    perfil = sc.register_cabinet_sentence_sample("Modelo X", "CONDENATORIA", amostra)
    novo = perfil["modelos_cadastrados"][-1]
    assert novo["id"] == "MOD-04"
    salvo = (tmp_path / novo["arquivo_amostra"]).read_text(encoding="utf-8")
    assert salvo == amostra, "a amostra deve ser guardada integralmente"
    assert novo["sha256"] == hashlib.sha256(amostra.encode("utf-8")).hexdigest()
    assert sc.ler_amostra_completa("MOD-04") == amostra
    extraido = novo["perfil_extraido"]
    assert extraido["abertura_relatorio"].startswith("Vistos e examinados")
    assert extraido["fechamento_relatorio"] == "Decido."
    assert extraido["abertura_dispositivo"].startswith("Isso posto, JULGO PROCEDENTE")
    conectivos = {c["conectivo"]: c["ocorrencias"] for c in extraido["conectivos_frequentes"]}
    assert conectivos["Com efeito,"] == 2
    assert conectivos["Ademais,"] == 1
    assert perfil["perfil_observado"]["amostras_analisadas"] == 1
    # As fórmulas ativas não são trocadas silenciosamente pela amostra.
    assert perfil["abertura_relatorio"] == sc.DEFAULT_STYLE_PROFILE["abertura_relatorio"]

    perfil2 = sc.register_cabinet_sentence_sample("Modelo Y", "ABSOLUTORIA", "Outra amostra completa.")
    ids = [m["id"] for m in perfil2["modelos_cadastrados"]]
    assert len(ids) == len(set(ids))


# ------------------------------------------------------------------------------
# TASK-024 — acervo verificado, natureza/verificacao, FONAVID e regressão Tema 1197
# ------------------------------------------------------------------------------


def test_task024_todos_itens_oficiais_tem_verificacao_natureza_e_ids_unicos():
    from app.knowledge.knowledge_base import FONAVID_REVOGADOS

    ids_esperados_0_a_10 = [
        "SUMULA-536-STJ",
        "SUMULA-542-STJ",
        "SUMULA-588-STJ",
        "SUMULA-589-STJ",
        "SUMULA-600-STJ",
        "SUMULA-231-STJ",
        "SUMULA-444-STJ",
        "SUMULA-545-STJ",
        "TEMA-983-STJ",
        "TEMA-1197-STJ",
        "RES-CNJ-492-2023",
    ]
    assert [it["id"] for it in SUMULAS_E_TEMAS_VERIFICADOS[:11]] == ids_esperados_0_a_10

    # Tema 1197 corrigido no índice 9
    t1197 = SUMULAS_E_TEMAS_VERIFICADOS[9]
    assert "61" in t1197["texto_literal"] and "bis in idem" in t1197["texto_literal"]
    assert "2.027.794/MS" in t1197["fonte_oficial"]

    # Sem IDs duplicados
    ids_oficiais = [it["id"] for it in SUMULAS_E_TEMAS_VERIFICADOS]
    assert len(ids_oficiais) == len(set(ids_oficiais)), "IDs duplicados em SUMULAS_E_TEMAS_VERIFICADOS"
    ids_dout = [d["id"] for d in DOUTRINA_VERIFICADA]
    assert len(ids_dout) == len(set(ids_dout)), "IDs duplicados em DOUTRINA_VERIFICADA"

    status_validos = {"verificado", "parcial", "pendente"}
    for it in SUMULAS_E_TEMAS_VERIFICADOS:
        assert it.get("natureza") in {"LITERAL", "RESUMO", "SINTESE_DO_ENTENDIMENTO"}, f"{it['id']} sem natureza válida"
        ver = it.get("verificacao")
        assert isinstance(ver, dict), f"{it['id']} sem verificacao"
        assert ver.get("status") in status_validos, f"{it['id']} status inválido: {ver}"
        assert ver.get("fonte_url"), f"{it['id']} sem fonte_url"
        assert ver.get("conferido_em") == "2026-11-21", f"{it['id']} sem conferido_em"

    for d in DOUTRINA_VERIFICADA:
        assert "citacao" not in d, f"{d['id']} não deve mais usar o campo 'citacao'"
        assert d.get("sintese"), f"{d['id']} sem sintese"
        assert d.get("natureza") == "SINTESE_DO_ENTENDIMENTO"
        ver = d.get("verificacao")
        assert isinstance(ver, dict) and ver.get("status") in status_validos

    # Enunciados FONAVID revogados não podem estar no acervo
    for it in SUMULAS_E_TEMAS_VERIFICADOS:
        if it.get("tribunal") == "FONAVID" or it.get("categoria") == "FONAVID":
            import re

            m = re.search(r"ENUNCIADO-(\d+)-FONAVID", it["id"])
            assert m, f"ID FONAVID fora do padrão: {it['id']}"
            assert int(m.group(1)) not in FONAVID_REVOGADOS, f"Enunciado revogado presente: {it['id']}"


def test_task024_regressao_art_24a_sem_tema_1197_e_sem_enunciado_48():
    import datetime
    from app.demo.demo_case import get_demo_case_bundle
    from app.generation.sentence_generator import gerar_sentenca_estruturada
    from app.ingestion.laya_classifier import SINTESES_EXECUTIVAS_LAYA
    from app.legal_engine.penal_types_catalog import get_penal_type_for_date

    demo = get_demo_case_bundle()
    sent_demo = gerar_sentenca_estruturada(case_bundle=demo, use_ollama_if_available=False)
    assert "Tema Repetitivo 1197" not in sent_demo.texto_completo
    assert "Tema 1197" not in sent_demo.texto_completo
    assert "Enunciado 48 FONAVID" not in sent_demo.texto_completo

    # Também valida na síntese Laya e no catálogo penal do art. 24-A
    assert "1197" not in SINTESES_EXECUTIVAS_LAYA["CERTIDAO_INTIMACAO_MPU"]
    spec_24a = get_penal_type_for_date("art_24a_lmp", datetime.date(2025, 5, 1))
    assert "1197" not in (spec_24a.observacao_intertemporal or "")


def test_task024_formatar_citacao_nunca_poe_aspas_em_resumo_parcial_ou_pendente():
    from app.knowledge.knowledge_base import (
        formatar_citacao_acervo,
        item_citavel_automaticamente,
        pode_citar_literal,
    )

    literal_ok = buscar_precedente_por_id_ou_referencia("SUMULA-589-STJ")
    resumo_ok = buscar_precedente_por_id_ou_referencia("ADC-19-STF")
    parcial_item = {
        "id": "SYNTH-PARCIAL",
        "texto_literal": "Resumo sintético de teste parcial.",
        "natureza": "RESUMO",
        "verificacao": {"status": "parcial", "fonte_url": "https://portal.stf.jus.br"},
    }
    pendente_item = {
        "id": "SYNTH-PENDENTE",
        "texto_literal": "Resumo sintético de teste pendente.",
        "natureza": "RESUMO",
        "verificacao": {"status": "pendente", "fonte_url": "https://scon.stj.jus.br"},
    }
    dout_item = DOUTRINA_VERIFICADA[0]

    assert pode_citar_literal(literal_ok) is True
    fmt_lit = formatar_citacao_acervo(literal_ok)
    assert fmt_lit.startswith('"') and fmt_lit.endswith('"')

    assert pode_citar_literal(resumo_ok) is False
    fmt_res = formatar_citacao_acervo(resumo_ok)
    assert fmt_res and not fmt_res.startswith('"') and not fmt_res.endswith('"')

    assert pode_citar_literal(parcial_item) is False
    fmt_par = formatar_citacao_acervo(parcial_item)
    assert fmt_par and not fmt_par.startswith('"') and not fmt_par.endswith('"')

    assert pode_citar_literal(pendente_item) is False
    assert item_citavel_automaticamente(pendente_item) is False
    assert formatar_citacao_acervo(pendente_item) == ""

    assert pode_citar_literal(dout_item) is False
    fmt_dout = formatar_citacao_acervo(dout_item)
    assert fmt_dout and not fmt_dout.startswith('"') and not fmt_dout.endswith('"')


def test_task024_secao_2_1_cita_tese_13_e_nao_tema_983():
    from app.demo.demo_case import get_demo_case_bundle
    from app.generation.sentence_generator import gerar_sentenca_estruturada
    from app.knowledge.knowledge_base import obter_item_oficial_por_id

    tese_13 = obter_item_oficial_por_id("TESE-STJ-ED41-13")["texto_literal"]
    tema_983 = obter_item_oficial_por_id("TEMA-983-STJ")["texto_literal"]
    tema_1197 = obter_item_oficial_por_id("TEMA-1197-STJ")["texto_literal"]
    tema_1333 = obter_item_oficial_por_id("TEMA-1333-STJ")["texto_literal"]

    # Conferência literal contra a página oficial do STJ
    assert "alínea f, do Código Penal (CP)" in tema_1197
    assert '1 - A agravante prevista no art. 61, II, "f", do Código Penal' in tema_1333

    # Caso demo: seção 2.1 cita a Tese 13 e NÃO contém Tema 983 (que fica só na reparação/dosimetria)
    demo = get_demo_case_bundle()
    sent_demo = gerar_sentenca_estruturada(case_bundle=demo, use_ollama_if_available=False)
    merito = sent_demo.secao_merito
    ini = merito.index("2.1.")
    fim = merito.index("2.2.")
    secao_2_1 = merito[ini:fim]

    assert "Edição 41, Tese 13" in secao_2_1
    assert tese_13 in secao_2_1
    assert "Tema Repetitivo 983" not in secao_2_1
    assert tema_983 not in secao_2_1
    assert tema_983 not in merito
    assert tema_983 in sent_demo.secao_dosimetria


def test_task040_acervo_100_por_cento_verificado_e_com_fonte_url():
    """Todo item do acervo oficial e da doutrina deve estar 'verificado' e com fonte_url preenchida."""
    for lista_nome, lista in (
        ("SUMULAS_E_TEMAS_VERIFICADOS", SUMULAS_E_TEMAS_VERIFICADOS),
        ("DOUTRINA_VERIFICADA", DOUTRINA_VERIFICADA),
    ):
        assert lista, f"{lista_nome} não pode estar vazia"
        for it in lista:
            verif = it.get("verificacao") or {}
            assert verif.get("status") == "verificado", (
                f"Item não verificado em {lista_nome}: {it.get('id')} ({verif.get('status')})"
            )
            url = str(verif.get("fonte_url") or "").strip()
            assert url.startswith(("http://", "https://")), (
                f"Item sem fonte_url válida em {lista_nome}: {it.get('id')} -> {url!r}"
            )


def test_task040_nenhum_acesso_posicional_ao_acervo_em_app():
    """Proíbe indexação posicional de SUMULAS_E_TEMAS_VERIFICADOS[n] ou DOUTRINA_VERIFICADA[n] em app/."""
    import re

    raiz_app = Path(__file__).resolve().parent.parent / "app"
    padrao = re.compile(r"(?:SUMULAS_E_TEMAS_VERIFICADOS|DOUTRINA_VERIFICADA)\[\s*\d+")
    violacoes = []
    for arq in sorted(raiz_app.rglob("*.py")):
        texto = arq.read_text(encoding="utf-8")
        for num, linha in enumerate(texto.splitlines(), start=1):
            if padrao.search(linha):
                violacoes.append(f"{arq.relative_to(raiz_app.parent)}:{num}: {linha.strip()}")
    assert not violacoes, "Acessos posicionais proibidos ao acervo em app/:\n" + "\n".join(violacoes)


def test_task040_adpf_779_verificada_e_crivo_reprova_legitima_defesa_da_honra():
    """ADPF-779-STF permanece verificada no acervo e os crivos reprovam legítima defesa da honra sem repúdio."""
    from app.generation.adversarial_critic import AdversarialJudicialCritic
    from app.knowledge.knowledge_base import obter_item_oficial_por_id

    adpf = obter_item_oficial_por_id("ADPF-779-STF")
    assert adpf["verificacao"]["status"] == "verificado"
    assert str(adpf["verificacao"].get("fonte_url", "")).startswith("https://")

    texto_inadmissivel = (
        "No mérito, reconheço que o acusado agiu sob legítima defesa da honra "
        "ao agredir a ofendida após discussão doméstica, impondo-se a absolvição."
    )
    rel_auditor = auditar_sentenca(texto_inadmissivel, pecas_autos=[], teses_defesa=[])
    assert rel_auditor.aprovada_sem_alucinacao is False
    assert any(
        "legítima defesa da honra" in str(d).lower() or "adpf 779" in str(d).lower()
        for d in rel_auditor.detalhes_camadas
    ), rel_auditor.detalhes_camadas

    critic = AdversarialJudicialCritic()
    apontamentos = critic._crivo_standard_probatorio_e_genero(texto_inadmissivel, secao=None, dossie=None)
    assert any("779" in str(a.descricao) or "honra" in str(a.descricao).lower() for a in apontamentos), apontamentos


def test_task040_demo_e_geracao_local_sem_referencia_vazia():
    """Demo e geração local produzem sentença completa sem None, [] ou citação quebrada."""
    from app.knowledge.knowledge_base import obter_doutrina_por_id

    assert obter_doutrina_por_id("DOUT-01")["autor"] == "Maria Berenice Dias"
    demo = get_demo_case_bundle()
    sent = gerar_sentenca_estruturada(case_bundle=demo, use_ollama_if_available=False)
    texto = sent.texto_completo
    assert len(texto) > 10000
    assert "None" not in texto
    assert "[]" not in texto
    assert "()" not in texto
    assert "Valéria Diez Scarance" not in texto
    assert "ADPF 779" in texto
    assert "art. 24-A" in texto


def test_task039_precedentes_novos_verificados():
    from app.knowledge.knowledge_base import obter_item_oficial_por_id

    esperados = {
        "SUMULA-636-STJ": (
            "A folha de antecedentes criminais é documento suficiente a comprovar "
            "os maus antecedentes e a reincidência."
        ),
        "TEMA-129-STF": (
            "A existência de inquéritos policiais ou de ações penais sem trânsito "
            "em julgado não pode ser considerada como maus antecedentes para fins de dosimetria da pena."
        ),
    }
    for item_id, texto in esperados.items():
        item = obter_item_oficial_por_id(item_id)
        assert item["texto_literal"] == texto
        assert item["natureza"] == "LITERAL"
        assert item["verificacao"]["status"] == "verificado"
    assert "Súmula 444/STJ" in obter_item_oficial_por_id("TEMA-129-STF")["observacao"]


def test_task039_sinteses_tjrn_sem_cnj_e_sem_aspas():
    import re

    from app.knowledge.knowledge_base import formatar_citacao_acervo

    itens_tjrn = [item for item in SUMULAS_E_TEMAS_VERIFICADOS if item["tribunal"] == "TJRN"]
    assert {
        "TJRN-1G-INSUFICIENCIA-PROBATORIA",
        "TJRN-1G-EMBRIAGUEZ-VOLUNTARIA",
        "TJRN-1G-DESCLASSIFICACAO",
        "TJRN-1G-BIS-IN-IDEM",
        "TJRN-1G-CIENCIA-MPU",
        "TJRN-1G-CONCURSO-MATERIAL",
    } <= {item["id"] for item in itens_tjrn}
    padrao_cnj = re.compile(r"\b\d{7}-\d{2}[._]\d{4}[._]\d[._]\d{2}[._]\d{4}\b")
    for item in itens_tjrn:
        assert item["natureza"] == "SINTESE_DO_ENTENDIMENTO", item["id"]
        assert item["verificacao"]["status"] == "verificado", item["id"]
        assert not padrao_cnj.search(json.dumps(item, ensure_ascii=False)), item["id"]
        assert formatar_citacao_acervo(item) == item["texto_literal"], item["id"]


def test_task039_teses_existentes_recebem_recorrencia():
    from app.knowledge.knowledge_base import obter_item_oficial_por_id

    recorrencias = {
        "TESE-STJ-ED41-13": "161",
        "ADPF-779-STF": "45",
        "SUMULA-542-STJ": "43",
        "SUMULA-589-STJ": "16",
        "SUMULA-588-STJ": "72",
        "TEMA-983-STJ": "98",
        "SUMULA-545-STJ": "89",
        "RES-CNJ-492-2023": "27",
    }
    for item_id, total in recorrencias.items():
        assert f"{total} sentenças" in obter_item_oficial_por_id(item_id)["observacao"]
    assert obter_item_oficial_por_id("ADPF-779-STF")["observacao"].startswith(
        "A ADPF 779 veda a legítima defesa da honra"
    )



def test_task069_acervo_tem_proposicao_recorte_vigencia_e_data_revisao():
    """TASK-069: todos os verbetes oficiais possuem proposição jurídica, vigência explícita (ou nao_verificada),
    data_revisao (ou vazio) e recorte_fatico (vazio quando ausente, nunca sintetizado).
    """
    import datetime as dt
    import app.knowledge.knowledge_base as kb

    for it in kb.SUMULAS_E_TEMAS_VERIFICADOS:
        assert isinstance(it.get("proposicao_juridica"), str) and len(it["proposicao_juridica"].strip()) >= 15, it["id"]
        assert it.get("proposicao_origem") in {"primeira_frase", "revisada"}, it["id"]
        assert isinstance(it.get("recorte_fatico"), str), it["id"]
        assert it.get("vigencia") in set(kb.VIGENCIAS_VALIDAS), it["id"]
        if it.get("data_revisao"):
            dt.date.fromisoformat(str(it["data_revisao"]))
        assert isinstance(it.get("questoes_juridicas"), list) and len(it["questoes_juridicas"]) >= 1, it["id"]

    # Regra estrita: nada decidido por padrão / nada sintetizado
    sem_dados = {"id": "TESTE-1", "titulo": "Título Qualquer", "materia": "Penal", "texto_literal": "Primeira frase válida aqui. Segunda frase."}
    kb._enriquecer_item_acervo(sem_dados)
    assert sem_dados["vigencia"] == "nao_verificada"
    assert sem_dados["data_revisao"] == ""
    assert sem_dados["recorte_fatico"] == ""
    assert sem_dados["proposicao_origem"] == "primeira_frase"


def test_task069_selecionar_por_questao_juridica_pertinencia_e_vigencia(tmp_path, monkeypatch):
    """TASK-069: seleção por questão jurídica ordena por pertinência, sinaliza nao_verificada e exclui superados."""
    import app.knowledge.knowledge_base as kb

    # nao_verificada nunca é tratada como vigente quando apenas_vigentes=True e incluir_nao_verificadas=False
    so_vigentes = kb.selecionar_por_questao_juridica("substituicao_pena", apenas_vigentes=True)
    assert all(it.get("vigencia") == "vigente" for it in so_vigentes)

    res_subst = kb.selecionar_por_questao_juridica("substituicao_pena", incluir_nao_verificadas=True)
    assert res_subst
    assert any(it["id"] == "SUMULA-588-STJ" for it in res_subst[:3])

    res_rep = kb.selecionar_por_questao_juridica(
        "reparacao_danos",
        incluir_nao_verificadas=True,
        palavras_faticas=["dano moral", "pedido expresso", "valor mínimo", "violência doméstica"],
    )
    assert res_rep
    assert res_rep[0]["id"] == "TEMA-983-STJ"

    # Precedente do gabinete superado não aparece quando apenas_vigentes=True (mesmo com incluir_nao_verificadas=True)
    monkeypatch.setattr(kb, "CUSTOM_KB_FILE", tmp_path / "custom_precedentes.json")
    kb.add_custom_precedente(
        referencia="Precedente Fictício 999/TJRN",
        tribunal="TJRN",
        titulo="Tese superada sobre ação penal",
        texto_literal="Entendimento superado em matéria de ação penal.",
        proposicao_juridica="Tese superada sobre ação penal.",
        recorte_fatico="Lesão corporal leve em violência doméstica.",
        vigencia="superado",
        questoes_juridicas=["acao_penal"],
    )
    vigentes = kb.selecionar_por_questao_juridica(
        "acao_penal", incluir_gabinete=True, apenas_vigentes=True, incluir_nao_verificadas=True, limite=50
    )
    assert all(it.get("vigencia") in {"vigente", "nao_verificada"} for it in vigentes)
    assert not any("999/TJRN" in str(it.get("referencia")) for it in vigentes)

    todos = kb.selecionar_por_questao_juridica("acao_penal", incluir_gabinete=True, apenas_vigentes=False, limite=50)
    assert any("999/TJRN" in str(it.get("referencia")) for it in todos)


def test_task069_get_all_knowledge_base_filtra_por_questao():
    import app.knowledge.knowledge_base as kb

    filtrado = kb.get_all_knowledge_base(questao="legitima_defesa_honra")
    ids = {it["id"] for it in filtrado["sumulas_e_temas"]}
    assert "ADPF-779-STF" in ids
    assert len(ids) < len(kb.SUMULAS_E_TEMAS_VERIFICADOS)
