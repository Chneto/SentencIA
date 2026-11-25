"""TASK-090: a reauditoria aponta comando contraditório com o registro do juízo em qualquer seção. Dados fictícios."""

import pytest

from app.generation.dispositivo import verificar_decisoes_acessorias

FUNDAMENTO = "Persiste a situação de risco descrita pela ofendida fictícia (Fontes: ID-FICTICIO)."
COMANDO = "MANTENHO as medidas protetivas de urgência identificadas nesta fundamentação."
REGISTRO = {"medidas_protetivas": {"fundamento": FUNDAMENTO, "comando": COMANDO, "estado": "decidida"}}


def _fundamentacao(extra: str = "") -> str:
    return (
        "II - FUNDAMENTAÇÃO\n\nDo mérito\n\nTexto fictício do mérito.\n\n"
        f"Das medidas protetivas de urgência\n\n{FUNDAMENTO} {extra}\n\nDa dosimetria\n\nTexto fictício."
    )


def _dispositivo(extra: str = "") -> str:
    return f"Ante o exposto, condeno o réu fictício. {COMANDO} {extra}"


def test_minuta_coerente_nao_tem_divergencia():
    assert verificar_decisoes_acessorias(_fundamentacao(), _dispositivo(), REGISTRO) == []


@pytest.mark.parametrize("frase", [
    "Ficam revogadas as medidas protetivas.",
    "Revogam-se as medidas protetivas de urgência.",
    "Julgo revogadas as medidas protetivas anteriormente deferidas.",
    "As medidas protetivas ficam revogadas a partir desta data.",
])
def test_revogacao_inserida_na_fundamentacao_e_apontada_com_a_secao(frase):
    divergencias = verificar_decisoes_acessorias(_fundamentacao(frase), _dispositivo(), REGISTRO)
    assert len(divergencias) == 1
    assert "medidas protetivas" in divergencias[0]
    assert "Das medidas protetivas de urgência" in divergencias[0]


def test_revogacao_inserida_em_secao_diversa_tambem_e_apontada():
    texto = _fundamentacao().replace("Da dosimetria\n\nTexto fictício.", "Da dosimetria\n\nFicam revogadas as medidas protetivas.")
    divergencias = verificar_decisoes_acessorias(texto, _dispositivo(), REGISTRO)
    assert len(divergencias) == 1 and "Da dosimetria" in divergencias[0]


def test_revogacao_somada_ao_dispositivo_continua_apontada():
    divergencias = verificar_decisoes_acessorias(_fundamentacao(), _dispositivo("Revoguem-se as medidas protetivas."), REGISTRO)
    assert len(divergencias) == 1 and "dispositivo" in divergencias[0]


def test_registro_de_revogacao_aponta_manutencao_inserida():
    registro = {"medidas_protetivas": {
        "fundamento": "Cessou o risco fictício.",
        "comando": "REVOGO as medidas protetivas de urgência identificadas nesta fundamentação.",
        "estado": "decidida",
    }}
    texto = "Das medidas protetivas\n\nCessou o risco fictício. Ficam mantidas as medidas protetivas."
    disp = "Ante o exposto. REVOGO as medidas protetivas de urgência identificadas nesta fundamentação."
    divergencias = verificar_decisoes_acessorias(texto, disp, registro)
    assert len(divergencias) == 1 and "Das medidas protetivas" in divergencias[0]


def test_mencao_descritiva_nao_e_comando():
    texto = _fundamentacao("A Defesa pediu que fossem revogadas as medidas protetivas, pedido que não prospera.")
    assert verificar_decisoes_acessorias(texto, _dispositivo(), REGISTRO) == []
