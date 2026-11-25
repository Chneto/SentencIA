"""TASK-087: efeitos do art. 92, § 2º, do CP (Lei 14.994/2024, vigência em 10/10/2024). Dados fictícios.

Lei 14.994/2024, arts. 1º e 10: https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2024/lei/l14994.htm
CP compilado (art. 92; art. 121-A, § 1º): https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm
As alterações de 2026 no art. 92 (Lei 15.358/2026: inciso IV e §§ 3º e 4º, CNPJ na receptação; Lei 15.487/2026:
crimes do ECA no § 2º) não alcançam os crimes de violência doméstica do JVDFM e ficam fora do modelo.
"""

from datetime import date

import pytest

from app.core.errors import DadosInsuficientesError
from app.generation.dispositivo import resolver_acessorias, verificar_decisoes_acessorias
from app.generation.juizo_sobre_provas import JuizoSobreProvas
from app.legal_engine.dosimetria_calculator import CrimeDosimetriaInput, calcular_dosimetria_global

SENTENCA = date(2026, 11, 20)


def _acessorias(fato: date, registro: dict | None = None, *, condenar: bool = True):
    crimes = [CrimeDosimetriaInput(crime_code="art_129_p13_cp", data_fato=fato)] if condenar else []
    dosimetria = calcular_dosimetria_global(crimes)
    dados = {"efeitos_art92": registro} if registro is not None else None
    return resolver_acessorias(
        dosimetria, JuizoSobreProvas(), dados=dados, data_sentenca=SENTENCA, fontes_ativas={"ID-FICTICIO"},
        revogacao_documentada="", absolveu=not condenar, nome_ofendida="", destino_execucao=("", ""),
    )


ENQUADRADO = {"enquadramento": "violencia_domestica", "cargo_publico": "nao", "poder_familiar": "nao"}


@pytest.mark.parametrize("dia, incide", [(9, False), (10, True), (11, True)])
def test_vigencia_por_fato_vespera_dia_e_dia_seguinte(dia, incide):
    efeitos = _acessorias(date(2024, 10, dia), ENQUADRADO).efeitos
    assert ("art. 92, § 2º" in efeitos.comando) is incide
    assert efeitos.estado == "decidida"
    if not incide:
        assert "anteriores à vigência da Lei nº 14.994/2024" in efeitos.fundamento


def test_sem_registro_depois_da_vigencia_fica_pendente():
    efeitos = _acessorias(date(2024, 10, 11)).efeitos
    assert efeitos.estado == "pendente"
    assert "[COMPLETAR PELO JUÍZO" in efeitos.comando and "art. 92, § 2º" in efeitos.comando


def test_sem_registro_antes_da_vigencia_nao_cria_pendencia():
    efeitos = _acessorias(date(2024, 10, 9)).efeitos
    assert efeitos.estado == "decidida" and "art. 92" not in efeitos.comando


def test_enquadramento_afastado_registra_sem_efeitos_especiais():
    efeitos = _acessorias(date(2025, 1, 5), {"enquadramento": "nao_se_aplica", "fundamento": "Fato sem razões de gênero."}).efeitos
    assert efeitos.estado == "decidida"
    assert "Fato sem razões de gênero." in efeitos.fundamento
    assert "DECLARO" not in efeitos.comando


def test_enquadrado_com_cargo_e_poder_familiar_declara_efeitos_e_vedacao():
    registro = {
        "enquadramento": "violencia_domestica", "cargo_publico": "sim", "descricao_cargo": "servidor municipal fictício",
        "poder_familiar": "sim",
    }
    efeitos = _acessorias(date(2025, 1, 5), registro).efeitos
    assert efeitos.estado == "decidida"
    assert "DECLARO a perda do cargo, função pública ou mandato eletivo (servidor municipal fictício)" in efeitos.comando
    assert "DECLARO a incapacidade para o exercício do poder familiar, da tutela ou da curatela" in efeitos.comando
    assert "vedadas a nomeação, a designação ou a diplomação" in efeitos.comando
    assert "art. 92, § 2º, III" in efeitos.fundamento


def test_sem_cargo_nem_poder_familiar_mantem_so_a_vedacao():
    efeitos = _acessorias(date(2025, 1, 5), ENQUADRADO).efeitos
    assert "DECLARO a perda" not in efeitos.comando and "DECLARO a incapacidade" not in efeitos.comando
    assert "vedadas a nomeação, a designação ou a diplomação" in efeitos.comando


@pytest.mark.parametrize("campo", ["cargo_publico", "poder_familiar"])
def test_dado_faltante_vira_pendencia_do_juizo(campo):
    registro = {**ENQUADRADO, campo: "nao_informado"}
    efeitos = _acessorias(date(2025, 1, 5), registro).efeitos
    assert efeitos.estado == "pendente"
    assert "[COMPLETAR PELO JUÍZO" in efeitos.comando


def test_cargo_sem_descricao_e_recusado():
    with pytest.raises(DadosInsuficientesError):
        _acessorias(date(2025, 1, 5), {**ENQUADRADO, "cargo_publico": "sim"})


def test_sem_condenacao_registro_e_recusado():
    with pytest.raises(DadosInsuficientesError):
        _acessorias(date(2025, 1, 5), ENQUADRADO, condenar=False)


def test_auditoria_acusa_comando_do_art92_retirado_da_minuta():
    efeitos = _acessorias(date(2025, 1, 5), ENQUADRADO).efeitos
    registro = {"efeitos": {"fundamento": efeitos.fundamento, "comando": efeitos.comando, "estado": efeitos.estado}}
    assert not verificar_decisoes_acessorias(efeitos.fundamento, efeitos.comando, registro)
    sem_vedacao = efeitos.comando.split(" Ficam vedadas")[0]
    assert verificar_decisoes_acessorias(efeitos.fundamento, sem_vedacao, registro)
