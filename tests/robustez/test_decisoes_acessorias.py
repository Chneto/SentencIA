from datetime import date

import pytest

from app.core.errors import DadosInsuficientesError
from app.generation.case_script_engine import gerar_script_estruturado_caso
from app.generation.dispositivo import decidir_registro_medidas_protetivas
from app.generation.hallucination_auditor import auditar_sentenca
from app.generation.sentence_generator import calcular_secoes_deterministicas, gerar_sentenca_estruturada
from tests.test_geracao_v2 import _cenario, _veredictos


@pytest.mark.parametrize(
    ("data_sentenca", "referencia_ao_paragrafo"),
    [(date(2023, 4, 19), False), (date(2023, 4, 20), True), (date(2023, 4, 21), True)],
)
def test_medida_protetiva_indefinida_fica_pendente(data_sentenca, referencia_ao_paragrafo):
    """Lei 14.550/2023, vigente em 20/04/2023.
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2023/lei/l14550.htm
    """
    decisao = decidir_registro_medidas_protetivas("", data_sentenca)
    assert decisao.estado == "pendente"
    assert ("§§ 5º e 6º" in decisao.fundamento) is referencia_ao_paragrafo
    assert "COMPLETAR PELO JUÍZO" in decisao.comando


def test_medida_protetiva_revogada_preserva_fonte_nos_dois_blocos():
    decisao = decidir_registro_medidas_protetivas("ID-MPU-FICTICIA", date(2025, 4, 20))
    assert decisao.estado == "decidida"
    assert "ID-MPU-FICTICIA" in decisao.fundamento
    assert "ID-MPU-FICTICIA" in decisao.comando


def test_decisoes_do_juizo_produzem_fundamentos_e_comandos_auditaveis():
    bundle = _cenario(1)
    opcoes = _veredictos(bundle)
    opcoes["decisoes_acessorias"] = {
        "substituicao": {"decisao": "negar", "fundamento": "Fundamento fictício da substituição."},
        "sursis": {
            "decisao": "negar", "fundamento": "Fundamento fictício sobre a suspensão condicional.",
        },
        "medidas_protetivas": {
            "decisao": "manter", "fundamento": "Fundamento fictício sobre a continuidade do risco.",
            "fontes": [bundle["raw_documents"][0]["doc_id"]],
        },
    }
    resultado = calcular_secoes_deterministicas(bundle, opcoes)
    sentenca = gerar_sentenca_estruturada(bundle, opcoes, use_ollama_if_available=False)
    for chave in ("substituicao", "sursis", "medidas_protetivas"):
        acessoria = getattr(resultado.decisoes_acessorias, chave)
        assert acessoria.fundamento in sentenca.secao_dosimetria
        assert acessoria.comando in sentenca.secao_dispositivo

    def auditar(texto):
        return auditar_sentenca(texto, resultado.pecas, resultado.meta.teses_defesa, dosimetria_dados=sentenca.dosimetria_dados)

    assert not auditar(sentenca.texto_completo).penas_divergentes
    alterado = sentenca.texto_completo.replace("MANTENHO as medidas", "REVOGO as medidas")
    assert any("medidas protetivas" in erro for erro in auditar(alterado).penas_divergentes)
    duplicado = sentenca.texto_completo.replace(
        resultado.decisoes_acessorias.medidas_protetivas.comando,
        resultado.decisoes_acessorias.medidas_protetivas.comando + " REVOGO as medidas protetivas.",
    )
    assert any("comando adicional" in erro for erro in auditar(duplicado).penas_divergentes)


def test_sugestao_de_sursis_nao_vira_concessao_sem_decisao():
    bundle = _cenario(1)
    resultado = calcular_secoes_deterministicas(bundle, _veredictos(bundle))
    assert resultado.decisoes_acessorias.sursis.estado == "pendente"
    assert "CONCEDO" not in resultado.decisoes_acessorias.sursis.comando
    assert "[COMPLETAR PELO JUÍZO:" in resultado.decisoes_acessorias.sursis.comando


@pytest.mark.parametrize("confirmado", [False, True])
def test_revogacao_mpu_exige_oitivas_e_fonte_ativa(confirmado):
    """Tema 1249/STJ, julgamento em 13/11/2024.
    https://scon.stj.jus.br/jurisprudencia/externo/informativo/?livre=%40CNOT%3D021177
    """
    bundle = _cenario(1)
    opcoes = _veredictos(bundle)
    opcoes["decisoes_acessorias"] = {
        "medidas_protetivas": {
            "decisao": "revogar", "fundamento": "Oitiva fictícia reavaliada pelo juízo.",
            "fontes": ["ID-FORA-DOS-AUTOS"], "contraditorio_confirmado": confirmado,
        },
    }
    with pytest.raises(DadosInsuficientesError) as excinfo:
        calcular_secoes_deterministicas(bundle, opcoes)
    assert excinfo.value.campos == [
        "decisoes_acessorias.medidas_protetivas.fontes" if confirmado else "decisoes_acessorias",
    ]


def test_sursis_exige_decisao_de_substituicao_e_preserva_prazo_e_condicoes():
    bundle = _cenario(1)
    opcoes = _veredictos(bundle)
    lesao = next(item for item in opcoes["crimes_config"] if item["codigo"] == "art_129_p13_cp")
    lesao.update(veredicto="absolver", fundamento_absolvicao_art386="VII")
    registro = {"sursis": {
        "decisao": "conceder",
        "fundamento": "Fundamento fictício sobre o cabimento da suspensão.",
        "prazo_anos": 3,
        "modalidades": ["Limitação de fim de semana no primeiro ano."],
    }}
    opcoes["decisoes_acessorias"] = registro
    with pytest.raises(DadosInsuficientesError):
        calcular_secoes_deterministicas(bundle, opcoes)
    registro["substituicao"] = {
        "decisao": "negar", "fundamento": "A condenação remanescente é por grave ameaça.",
    }
    sentenca = gerar_sentenca_estruturada(bundle, opcoes, use_ollama_if_available=False)
    assert "pelo prazo de 3 anos" in sentenca.secao_dosimetria and "pelo prazo de 3 anos" in sentenca.secao_dispositivo
    assert "Limitação de fim de semana no primeiro ano." in sentenca.secao_dispositivo

    script = gerar_script_estruturado_caso(bundle, opcoes, pseudonimizar=True)
    assert registro["sursis"]["fundamento"] not in script.script_markdown
    assert registro["sursis"]["modalidades"][0] not in script.script_markdown
    assert "decisoes_acessorias" not in script.script_json["dosimetria_calculada"]
