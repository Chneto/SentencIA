"""Testes de regressão da TASK-091: múltiplas janelas por peça, todas as fontes e mapa de posições."""

from __future__ import annotations

from app.core import case_context
from app.generation import gemini_integration, sentence_generator
from app.generation.case_script_engine import gerar_script_estruturado_caso
from app.generation.sectional_pipeline import (
    SecaoTipo,
    calcular_secoes_deterministicas,
    construir_dossie_secao,
)


def _bundle_peca_longa_e_intervalo_duas_fontes() -> dict:
    bloco = "Parágrafo introdutório genérico de contexto processual sem citação direta. " * 25
    excerto_1_pdf = "A ofendida relatou\n  ameaça grave\r\n  com faca na cozinha."
    excerto_2_pdf = "O laudo complementar atestou equimose no antebraço esquerdo."
    return {
        "manifesto_caso": {"id": "manif-091-ficticio"},
        "metadados_override": {
            "numero_processo": "0800001-00.2025.8.20.5001",
            "nome_acusado": "Marcos Ficticio",
            "nome_ofendida": "Helena Ficticia",
            "data_fato": "01/01/2025",
            "data_recebimento_denuncia": "10/02/2025",
            "crimes_imputados": ["art_147_cp"],
        },
        "metadados": {
            "numero_processo": "0800001-00.2025.8.20.5001",
            "reu_nome": "Marcos Ficticio",
            "vitima_nome": "Helena Ficticia",
            "data_fato": "01/01/2025",
            "data_recebimento_denuncia": "10/02/2025",
            "crimes_imputados": ["art_147_cp"],
            "imputacoes": [
                {
                    "id": "imp-1",
                    "crime_code": "art_147_cp",
                    "descricao": "Ameaça (art. 147, CP)",
                }
            ],
            "intervalos_custodia": [
                {
                    "inicio": "05/01/2025",
                    "fim": "20/01/2025",
                    "fonte_inicio": "ID-PRISAO-01",
                    "fonte_fim": "ID-SOLTURA-02",
                }
            ],
        },
        "raw_documents": [
            {
                "doc_id": "ID-PECA-LONGA",
                "titulo": "Auto de Prisão e Relatório Policial",
                "folha": "fl. 10-22",
                "categoria_confirmada": "INQUERITO_POLICIAL",
                "conteudo": f"{bloco}{excerto_1_pdf}\n{bloco}{excerto_2_pdf}\n{bloco}",
            },
            {
                "doc_id": "ID-PRISAO-01",
                "titulo": "Auto de Prisão em Flagrante",
                "folha": "fl. 25",
                "conteudo": "Certifico o recolhimento provisório em 05/01/2025 por ordem judicial.",
            },
            {
                "doc_id": "ID-SOLTURA-02",
                "titulo": "Alvará de Soltura Cumprido",
                "folha": "fl. 40",
                "conteudo": "Alvará de soltura devidamente cumprido em 20/01/2025 às 14h.",
            },
            {
                "doc_id": "ID-LISTA-EXTRA",
                "titulo": "Certidão Complementar",
                "folha": "fl. 45",
                "conteudo": "Certidão complementar referenciada em lista de fontes.",
            },
        ],
        "transcriptions": [],
    }


def _decisoes_ficticias() -> dict:
    return {
        "crimes_config": [
            {
                "crime_code": "art_147_cp",
                "veredicto": "condenar",
                "enquadramento_ameaca": "caput",
                "elementos_probatorios": {
                    "materialidade": {
                        "fonte_id": "ID-PECA-LONGA",
                        # Quebrado em linhas no PDF, mas citado com espaços colapsados
                        "trecho": "A ofendida relatou ameaça grave com faca na cozinha.",
                    },
                    "autoria": {
                        "fonte_id": "ID-PECA-LONGA",
                        "trecho": "O laudo complementar atestou equimose no antebraço esquerdo.",
                    },
                },
            }
        ],
        "intervalos_custodia": [
            {
                "inicio": "05/01/2025",
                "fim": "20/01/2025",
                "fonte_inicio": "ID-PRISAO-01",
                "fonte_fim": "ID-SOLTURA-02",
                "fontes": ["ID-LISTA-EXTRA"],
            }
        ],
    }


def test_normalizacao_mapa_e_multiplas_janelas_por_peca() -> None:
    case_context.limpar_cache_contexto()
    bundle = _bundle_peca_longa_e_intervalo_duas_fontes()
    decisoes = _decisoes_ficticias()

    rec = case_context.obter_contexto_geracao(bundle, decisoes=decisoes, max_chars_por_peca=500)
    assert {"ID-PECA-LONGA", "ID-PRISAO-01", "ID-SOLTURA-02", "ID-LISTA-EXTRA"}.issubset(
        set(rec["ids_vinculados"])
    )

    por_id = {p["doc_id"]: p for p in rec["trechos_pecas"]}
    assert set(por_id) >= {"ID-PECA-LONGA", "ID-PRISAO-01", "ID-SOLTURA-02", "ID-LISTA-EXTRA"}

    longa = por_id["ID-PECA-LONGA"]
    assert longa["recorte"] == "trecho_vinculado"
    assert longa["janelas_incluidas"] == 2
    assert longa["janelas_omitidas"] == 0
    assert longa["omitido_por_orcamento"] is False
    # Ambos os excertos distantes preservados no texto original (inclusive o quebrado em linhas)
    assert "ameaça grave" in longa["trecho"]
    assert "com faca na cozinha." in longa["trecho"]
    assert "equimose no antebraço esquerdo." in longa["trecho"]
    assert "[...]" in longa["trecho"]
    assert len(longa["trecho"]) <= 500


def test_orcamento_apertado_sinaliza_janelas_e_fontes_omitidas() -> None:
    case_context.limpar_cache_contexto()
    bundle = _bundle_peca_longa_e_intervalo_duas_fontes()
    decisoes = _decisoes_ficticias()

    # Orçamento por peça suficiente só para a primeira citação (~55 chars), omitindo a segunda
    rec = case_context.obter_contexto_geracao(
        bundle, decisoes=decisoes, max_chars_por_peca=80, max_total_chars=120
    )
    por_id = {p["doc_id"]: p for p in rec["trechos_pecas"]}
    longa = por_id["ID-PECA-LONGA"]
    assert longa["janelas_incluidas"] == 1
    assert longa["janelas_omitidas"] == 1
    assert longa["omitido_por_orcamento"] is True
    assert any(item["fonte_id"] == "ID-PECA-LONGA" for item in rec["janelas_omitidas_por_orcamento"])
    assert len(rec["fontes_omitidas_por_orcamento"]) >= 1


def test_paridade_fontes_e_excertos_entre_plano_dossie_script_e_gemini_simulado(monkeypatch) -> None:
    case_context.limpar_cache_contexto()
    bundle = _bundle_peca_longa_e_intervalo_duas_fontes()
    decisoes = _decisoes_ficticias()
    marcadores = ("ID-PECA-LONGA", "ID-PRISAO-01", "ID-SOLTURA-02", "ameaça grave", "equimose no antebraço esquerdo.")

    # 1. Plano único (sentence_generator)
    plano = sentence_generator.planejar_sentenca(bundle, decisoes)
    rec_plano = plano.contexto_geracao
    ids_plano = {p["doc_id"] for p in rec_plano["trechos_pecas"]}
    assert {"ID-PECA-LONGA", "ID-PRISAO-01", "ID-SOLTURA-02", "ID-LISTA-EXTRA"}.issubset(ids_plano)
    texto_plano = "\n".join(p["trecho"] for p in rec_plano["trechos_pecas"])
    assert "ameaça grave" in texto_plano and "equimose no antebraço esquerdo." in texto_plano

    # 2. Dossiê (sectional_pipeline)
    resultado = calcular_secoes_deterministicas(bundle, decisoes)
    dossie = construir_dossie_secao(SecaoTipo.MERITO, bundle, resultado=resultado)
    provas = "\n".join(dossie.fatos_delimitados)
    for marcador in marcadores:
        assert marcador in provas, f"Dossiê não contém {marcador!r}"

    # 3. Script (case_script_engine)
    script = gerar_script_estruturado_caso(bundle, decisoes, pseudonimizar=False)
    for marcador in marcadores:
        assert marcador in script.script_markdown, f"Script não contém {marcador!r}"

    # 4. Gemini simulado (prompt enviado pelo pipeline Gemini)
    capturado = []
    monkeypatch.setattr(gemini_integration, "resolver_chave_gemini", lambda _: "chave-ficticia")
    monkeypatch.setattr(gemini_integration, "verificar_autorizacao_envio_externo", lambda *_: None)

    def _simular_gemini(markdown, *_):
        capturado.append(markdown)
        return "\n\n".join(resultado.secoes.values()), {
            "modelo_utilizado": "ficticio",
            "modelo_solicitado": "ficticio",
            "motivo_troca": None,
            "finish_reason": "STOP",
        }

    monkeypatch.setattr(gemini_integration, "_chamar_gemini_detalhado", _simular_gemini)
    gemini_integration.gerar_sentenca_via_gemini_api(bundle, custom_dosimetria_override=decisoes)
    assert len(capturado) == 1
    for marcador in marcadores:
        assert marcador in capturado[0], f"Prompt Gemini não contém {marcador!r}"
