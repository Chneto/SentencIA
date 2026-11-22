from app.core.case_flags import is_demo_bundle
from app.ingestion.laya_classifier import classificar_pecas_autos, extrair_metadados_processo
from app.transcription.oral_proof_analyzer import gerar_matriz_corroboracao_oral
from app.demo.demo_case import get_demo_case_bundle


def test_laya_system1_classification_and_metadata_extraction():
    """Verifica a triagem estruturada System 1 (Laya) e extração determinística de IDs, datas e artigos imputados."""
    demo = get_demo_case_bundle()
    is_demo = is_demo_bundle(demo)
    assert is_demo is True
    pecas_classificadas = classificar_pecas_autos(demo["raw_documents"], is_demo=is_demo)

    categorias = {p.categoria for p in pecas_classificadas}
    assert "BO" in categorias
    assert "LAUDO_PERICIAL" in categorias
    assert "DECISAO_MPU" in categorias
    assert "CERTIDAO_INTIMACAO_MPU" in categorias
    assert "DENUNCIA" in categorias
    assert "TERMO_AUDIENCIA" in categorias
    assert "CAC_FAC" in categorias
    assert "ALEGACOES_FINAIS_MP" in categorias
    assert "ALEGACOES_FINAIS_DEFESA" in categorias

    for peca in pecas_classificadas:
        assert 0.0 < peca.laya_confidence <= 1.0
        assert peca.doc_id.startswith("ID-")

    meta = extrair_metadados_processo(pecas_classificadas, is_demo=is_demo)
    assert meta.numero_processo == "0804590-12.2025.8.00.0001"
    assert meta.nome_ofendida == "MARIANA ALVES FERREIRA"
    assert meta.nome_reu == "CARLOS EDUARDO ROCHA MENDES"
    assert len(meta.teses_defesa) >= 3
    assert meta.intimidado_pessoalmente_mpu is True


def test_oral_proof_matrix_res_cnj_492_2023():
    """Verifica a construção da Matriz de Corroboração (Delegacia vs. Juízo vs. Laudos) sob a Resolução CNJ 492/2023."""
    demo = get_demo_case_bundle()
    pecas = classificar_pecas_autos(demo["raw_documents"], is_demo=True)
    matriz = gerar_matriz_corroboracao_oral(pecas, demo["transcriptions"], is_demo=True)

    assert len(matriz.depoentes) >= 3
    assert matriz.perspectiva_genero_aplicada is True
    assert "Resolução CNJ nº 492/2023" in matriz.fundamentacao_sintese
    vitima_entry = next(d for d in matriz.depoentes if d.papel == "OFENDIDA")
    assert vitima_entry.discurso_indireto_sentenca
    assert vitima_entry.coerencia_delegacia_juizo in ("ALTA_COERENCIA", "RETRATACAO_CICLO_VIOLENCIA")


def test_conteudo_demo_sem_flag_nao_recebe_dados_fixos_da_demo():
    """Sem ``is_demo=True`` explícito, os autos são tratados como reais (nada de textos fixos da demo)."""
    demo = get_demo_case_bundle()
    pecas = classificar_pecas_autos(demo["raw_documents"])
    assert all(not p.resumo_estruturado.startswith("Síntese Laya S1: Notitia criminis e prisão") for p in pecas)

    meta = extrair_metadados_processo(pecas)
    # Os dados vêm do texto dos autos, não das constantes da demo.
    assert meta.numero_processo == "0804590-12.2025.8.00.0001"
    assert meta.nome_reu == "CARLOS EDUARDO ROCHA MENDES"
    assert meta.data_recebimento_denuncia == "2025-02-28"
    assert set(meta.crimes_imputados) == {"art_24a_lmp", "art_129_p13_cp", "art_147_cp"}
    assert meta.valor_dano_moral_pedido_mp == 3000.0

    matriz = gerar_matriz_corroboracao_oral(pecas, demo["transcriptions"])
    vitima = next(d for d in matriz.depoentes if d.papel == "OFENDIDA")
    # Sem flag, a síntese é construída dos temas efetivamente transcritos (sem a narrativa fixa da demo).
    assert "narrou de forma firme" not in vitima.discurso_indireto_sentenca
    assert vitima.coerencia_delegacia_juizo == "PENDENTE_AVALIACAO_JUIZO"
