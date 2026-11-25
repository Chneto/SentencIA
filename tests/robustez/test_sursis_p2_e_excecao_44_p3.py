"""TASK-088: sursis etário/humanitário (art. 77, § 2º) e exceção do art. 44, § 3º, do CP. Dados fictícios.

CP compilado (arts. 44 e 77, redação da Lei 9.714/1998):
https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm
https://www.planalto.gov.br/ccivil_03/leis/l9714.htm
"""

from datetime import date

import pytest

from app.core.errors import DadosInsuficientesError
from app.generation.dispositivo import resolver_acessorias
from app.generation.juizo_sobre_provas import JuizoSobreProvas
from app.legal_engine.dosimetria_calculator import (
    CrimeDosimetriaInput,
    DosimetriaConfig,
    calcular_dosimetria_global,
)

FATO = date(2025, 3, 10)


def _crimes(*codigos: str, **extra) -> list[CrimeDosimetriaInput]:
    return [CrimeDosimetriaInput(crime_code=codigo, data_fato=FATO, **extra) for codigo in codigos]


def _global(crimes, **cfg):
    return calcular_dosimetria_global(crimes, config=DosimetriaConfig(**cfg))


# Lesão em violência doméstica + ameaça: 2 anos e 1 mês, substituição vedada (Súmula 588/STJ).
DOIS_ANOS_E_UM_MES = ("art_129_p13_cp", "art_147_cp")
QUATRO_ANOS = ("art_129_p13_cp", "art_24a_lmp")
ACIMA_DE_QUATRO = ("art_129_p13_cp", "art_24a_lmp", "art_147_cp")


def _anos(saida) -> float:
    return saida.pena_total_unificada.to_total_days() / 360


def test_sem_hipotese_do_p2_nao_ha_sursis_e_o_fundamento_aponta_o_p2():
    saida = _global(_crimes(*DOIS_ANOS_E_UM_MES))
    assert 2.0 < _anos(saida) <= 4.0 and not saida.substituicao_art44_cabivel
    assert saida.sursis_art77_cabivel is False and saida.sursis_modalidade is None
    assert "art. 77, § 2º" in saida.fundamentacao_sursis


@pytest.mark.parametrize("codigos", [DOIS_ANOS_E_UM_MES, QUATRO_ANOS])
@pytest.mark.parametrize("hipotese", ["maior_70", "saude"])
def test_sursis_p2_cabivel_com_prova_de_quatro_a_seis_anos(codigos, hipotese):
    saida = _global(_crimes(*codigos), hipotese_sursis_art77_p2=hipotese)
    assert saida.sursis_art77_cabivel is True
    assert saida.sursis_modalidade == "art77_p2"
    assert "4 (quatro) a 6 (seis) anos" in saida.fundamentacao_sursis


def test_sursis_p2_respeita_o_limite_de_quatro_anos():
    saida = _global(_crimes(*ACIMA_DE_QUATRO), hipotese_sursis_art77_p2="maior_70")
    assert _anos(saida) > 4.0 and saida.sursis_art77_cabivel is False


def test_sursis_p2_nao_alcanca_reincidente_em_crime_doloso():
    saida = _global(_crimes(*DOIS_ANOS_E_UM_MES, reincidente=True), hipotese_sursis_art77_p2="saude")
    assert saida.sursis_art77_cabivel is False


def test_pena_de_ate_dois_anos_segue_o_caput_salvo_hipotese_registrada():
    assert _global(_crimes("art_129_p13_cp")).sursis_modalidade == "caput"
    saida = _global(_crimes("art_129_p13_cp"), hipotese_sursis_art77_p2="maior_70")
    assert saida.sursis_art77_cabivel is True and saida.sursis_modalidade == "art77_p2"


def test_reincidente_sem_excecao_nao_substitui_e_o_motivo_aponta_o_p3():
    saida = _global(_crimes("art_24a_lmp", reincidente=True))
    assert saida.substituicao_art44_cabivel is False
    assert "art. 44, § 3º" in saida.motivo_vedacao_substituicao


def test_reincidente_com_excecao_reconhecida_admite_substituicao():
    saida = _global(_crimes("art_24a_lmp", reincidente=True), excecao_art44_p3=True)
    assert saida.substituicao_art44_cabivel is True
    assert "art. 44, § 3º" in saida.motivo_vedacao_substituicao


def test_excecao_nao_supera_a_vedacao_por_violencia():
    saida = _global(_crimes("art_129_p13_cp", reincidente=True), excecao_art44_p3=True)
    assert saida.substituicao_art44_cabivel is False


def _resolver(dosimetria, registro):
    return resolver_acessorias(
        dosimetria, JuizoSobreProvas(), dados=registro, data_sentenca=date(2026, 11, 20), fontes_ativas=set(),
        revogacao_documentada="", absolveu=False, nome_ofendida="", destino_execucao=("", ""),
    )


def _registro_sursis(prazo: int) -> dict:
    return {
        "substituicao": {"decisao": "negar", "fundamento": "Negativa fictícia."},
        "sursis": {
            "decisao": "conceder", "fundamento": "Fundamento fictício.", "modalidades": ["Condição fictícia."],
            "prazo_anos": prazo,
        },
    }


def test_prazo_de_prova_do_p2_e_conferido_na_decisao():
    saida = _global(_crimes(*DOIS_ANOS_E_UM_MES), hipotese_sursis_art77_p2="maior_70")
    decisoes = _resolver(saida, _registro_sursis(5))
    assert "art. 77, § 2º" in decisoes.sursis.comando and "5 anos" in decisoes.sursis.comando
    with pytest.raises(DadosInsuficientesError):
        _resolver(saida, _registro_sursis(3))
    with pytest.raises(DadosInsuficientesError):
        _resolver(saida, _registro_sursis(7))


def test_prazo_do_caput_nao_admite_cinco_anos():
    saida = _global(_crimes("art_129_p13_cp"))
    assert saida.sursis_modalidade == "caput"
    assert "art. 77 do CP" in _resolver(saida, _registro_sursis(2)).sursis.comando
    with pytest.raises(DadosInsuficientesError):
        _resolver(saida, _registro_sursis(5))
