"""TASK-066. Separação de trechos confirmados e pendentes com mídia fictícia."""

from app.generation import gemini_integration
from app.generation.case_script_engine import gerar_script_estruturado_caso
from app.generation.prova_oral_confirmada import MARCADOR_PAPEL, separar_prova_oral
from app.generation.sectional_pipeline import SecaoTipo, construir_dossie_secao
from app.generation.sentence_generator import calcular_secoes_deterministicas
from tests.test_geracao_v2 import _cenario, _veredictos


def _segmento(texto, papel, estado="CONFIRMADO", confirmado=True):
    return {
        "texto_literal": texto, "papel_segmento": papel, "papel_estado": estado,
        "papel_confirmado": confirmado,
    }


def test_separa_por_papel_confirmado_e_preserva_a_fonte():
    midia = {
        "media_id": "ID-MIDIA-FICTICIA",
        "depoente": "Pessoa Fictícia",
        "papel": "OFENDIDA",
        "texto_completo": "Texto aglutinado da mídia fictícia.",
        "segmentos": [
            _segmento("Primeiro depoimento confirmado.", "OFENDIDA"),
            _segmento("Alegação sugerida sem confirmação.", "ALEGACOES_FINAIS_MP", "SUGERIDO", False),
            _segmento("Outro depoimento confirmado.", "TESTEMUNHA_POLICIAL"),
            _segmento("Flag textual sem confirmação válida.", "OFENDIDA", "CONFIRMADO", "true"),
            _segmento("Papel indeterminado.", "INDETERMINADO"),
        ],
    }

    prova = separar_prova_oral([midia])
    assert [t["papel"] for t in prova.transcricoes] == ["OFENDIDA", "TESTEMUNHA_POLICIAL"]
    assert [t["depoente"] for t in prova.transcricoes] == ["Pessoa Fictícia", "Pessoa ouvida"]
    assert all("texto_completo" not in t and t["media_id"] == midia["media_id"] for t in prova.transcricoes)
    assert [len(t["segmentos"]) for t in prova.transcricoes] == [1, 1]
    assert prova.situacao(midia["media_id"], "depoimento confirmado") == "confirmado"
    assert prova.situacao(midia["media_id"], "sem confirmação") == "pendente"
    assert prova.situacao(midia["media_id"], "Flag textual") == "pendente"
    assert prova.situacao(midia["media_id"], "Papel indeterminado") == "pendente"
    assert prova.situacao(midia["media_id"], "Trecho ausente") == "ausente"
    assert len(midia["segmentos"]) == 5


def test_referencia_ambigua_permanece_pendente():
    midia = {
        "media_id": "ID-MIDIA-FICTICIA",
        "segmentos": [
            _segmento("Resposta repetida na gravação.", "OFENDIDA"),
            _segmento("Resposta repetida na gravação.", "TESTEMUNHA_DEFESA", "SUGERIDO", False),
        ],
    }
    prova = separar_prova_oral([midia])
    assert prova.situacao(midia["media_id"], "Resposta repetida") == "pendente"


def test_confirmacao_da_midia_nao_promove_segmento_sem_confirmacao():
    midia = {
        "media_id": "ID-MIDIA-FICTICIA",
        "papel_confirmado": True,
        "papel": "OFENDIDA",
        "segmentos": [{"texto_literal": "Excerto legado sem papel do segmento."}],
    }
    prova = separar_prova_oral([midia])
    assert not prova.transcricoes
    assert prova.situacao(midia["media_id"], "Excerto legado") == "pendente"


def _vincular_materialidade(opcoes, fonte, trecho):
    opcoes["crimes_config"][0]["elementos_provados"] = [{
        "elemento": "materialidade", "conclusao": "provado", "fundamento": "Fundamento fictício.",
        "provas_favoraveis": [{"fonte_id": fonte, "trecho": trecho}],
    }]


def test_audio_pendente_nao_alimenta_merito_dossie_nem_script():
    bundle = _cenario(1)
    fonte = "ID-MIDIA-FICTICIA"
    trecho = "Trecho fictício aguardando confirmação do papel."
    segmento = _segmento(trecho, "OFENDIDA", "SUGERIDO", False)
    bundle["transcriptions"] = [{"media_id": fonte, "papel": "OFENDIDA", "segmentos": [segmento]}]
    opcoes = _veredictos(bundle)
    _vincular_materialidade(opcoes, fonte, trecho)
    resultado = calcular_secoes_deterministicas(bundle, opcoes)
    merito = resultado.secao("merito")
    dossie = construir_dossie_secao(SecaoTipo.MERITO, bundle, resultado=resultado)
    script = gerar_script_estruturado_caso(bundle, opcoes)
    assert MARCADOR_PAPEL in merito and trecho not in merito
    assert trecho not in "\n".join(dossie.fatos_delimitados)
    assert MARCADOR_PAPEL in script.script_markdown and trecho not in script.script_markdown

    segmento.update(papel_estado="CONFIRMADO", papel_confirmado=True)
    resultado = calcular_secoes_deterministicas(bundle, opcoes)
    assert trecho in resultado.secao("merito") and MARCADOR_PAPEL not in resultado.secao("merito")
    assert any(t["media_id"] == fonte and trecho in t["trecho"] for t in resultado.contexto_geracao["trechos_orais"])


def test_dossie_script_e_gemini_consumem_janela_vinculada(monkeypatch):
    bundle = _cenario(1)
    fonte = "ID-PECA-FICTICIA"
    prefixo = "Marcador fictício do começo irrelevante."
    trecho = "Registro fictício relevante no fim da peça."
    bundle["raw_documents"].append({
        "doc_id": fonte, "titulo": "Certidão fictícia de conferência", "folha": "fls. 120",
        "conteudo": prefixo + "\n" + "Texto de intervalo. " * 180 + trecho,
    })
    opcoes = _veredictos(bundle)
    _vincular_materialidade(opcoes, fonte, trecho)
    resultado = calcular_secoes_deterministicas(bundle, opcoes)
    dossie = construir_dossie_secao(SecaoTipo.MERITO, bundle, resultado=resultado)
    provas = "\n".join(dossie.fatos_delimitados)
    assert any(linha.startswith(f"Trecho documental em {fonte}") and trecho in linha for linha in dossie.fatos_delimitados)
    assert prefixo not in provas
    script = gerar_script_estruturado_caso(bundle, opcoes)
    assert trecho in script.script_markdown and prefixo not in script.script_markdown

    capturado = []
    monkeypatch.setattr(gemini_integration, "resolver_chave_gemini", lambda _: "chave-ficticia")
    monkeypatch.setattr(gemini_integration, "verificar_autorizacao_envio_externo", lambda *_: None)

    def simular_gemini(markdown, *_):
        capturado.append(markdown)
        return "\n\n".join(resultado.secoes.values()), {
            "modelo_utilizado": "ficticio", "modelo_solicitado": "ficticio",
            "motivo_troca": None, "finish_reason": "STOP",
        }

    monkeypatch.setattr(gemini_integration, "_chamar_gemini_detalhado", simular_gemini)
    gemini_integration.gerar_sentenca_via_gemini_api(bundle, custom_dosimetria_override=opcoes)
    assert len(capturado) == 1 and trecho in capturado[0] and prefixo not in capturado[0]
