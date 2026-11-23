# -*- coding: utf-8 -*-
"""BLOCO 1 — Testes baseados em propriedades (invariantes) da dosimetria.

Método: property-based testing com gerador determinístico. Em vez de exemplos fixos, sorteia combinações de tipo
penal, data do fato, circunstâncias judiciais, agravantes, atenuantes e frações, e verifica regras que precisam valer
SEMPRE (invariantes), além de propriedades metamórficas (monotonicidade) e de determinismo.
Base legal das invariantes: arts. 59, 65, 68 do CP; Súmula 231/STJ (atenuante não leva abaixo do mínimo).
"""

from __future__ import annotations

import datetime
import random

import pytest

from app.legal_engine.dosimetria_calculator import (
    VETORES_ART_59,
    CircunstanciaArt59,
    CrimeDosimetriaInput,
    calcular_dosimetria_crime,
    calcular_dosimetria_global,
)
from app.legal_engine.penal_types_catalog import get_penal_type_for_date
from tests.robustez._gerador import casos, falha

CODIGOS = ["art_129_p13_cp", "art_129_p9_cp", "art_147_cp", "art_24a_lmp", "art_147a_cp", "art_150_cp", "art_163_cp"]
DATAS = [datetime.date(2023, 5, 10), datetime.date(2024, 10, 9), datetime.date(2024, 10, 10), datetime.date(2025, 6, 1),
         datetime.date(2026, 8, 1)]


def _entrada(rng: random.Random, **forcar) -> CrimeDosimetriaInput:
    codigo = forcar.pop("crime_code", rng.choice(CODIGOS))
    data = forcar.pop("data_fato", rng.choice(DATAS))
    negativos = rng.sample(VETORES_ART_59, rng.randint(0, 4))
    dados = dict(
        crime_code=codigo,
        data_fato=data,
        circunstancias_art59={v: CircunstanciaArt59(desfavoravel=True, fundamentacao="fictícia") for v in negativos},
        reincidente=rng.random() < 0.3,
        confissao_espontanea=rng.random() < 0.4,
        menoridade_relativa_ou_senilidade=rng.random() < 0.2,
        agravantes_art61_f_violencia_domestica=rng.random() < 0.3,
    )
    dados.update(forcar)
    return CrimeDosimetriaInput(**dados)


def _dias(p) -> int:
    return p.to_total_days()


def _calcular(entrada):
    try:
        return calcular_dosimetria_crime(entrada)
    except Exception as exc:  # tipo inexistente na data é domínio válido, não falha
        if type(exc).__name__ in ("TipoPenalInexistenteNaDataError", "DadosInsuficientesError"):
            return None
        raise


def test_pena_base_sempre_dentro_dos_limites_legais():
    calculados = gerados = 0
    for i, ent in casos(_entrada):
        gerados += 1
        out = _calcular(ent)
        if out is None:
            continue
        calculados += 1
        spec = out.crime_spec
        assert _dias(spec.min_pena) <= _dias(out.pena_base) <= _dias(spec.max_pena), falha(i, f"pena-base fora da faixa: {ent}")
    # Cobertura do gerador: a maioria dos casos sorteados precisa ser efetivamente calculada.
    assert calculados >= 0.6 * gerados, f"gerador pouco efetivo: só {calculados} de {gerados} casos calculados"


def test_atenuante_nunca_leva_a_pena_intermediaria_abaixo_do_minimo_sumula_231():
    for i, ent in casos(lambda r: _entrada(r, confissao_espontanea=True, menoridade_relativa_ou_senilidade=True, reincidente=False)):
        out = _calcular(ent)
        if out is None:
            continue
        assert _dias(out.pena_intermediaria) >= _dias(out.crime_spec.min_pena), falha(i, f"abaixo do mínimo: {ent}")


def test_mais_circunstancias_negativas_nunca_diminuem_a_pena_base():
    """Propriedade metamórfica (monotonicidade): acrescentar um vetor negativo não reduz a pena-base."""
    for i, ent in casos(_entrada):
        out = _calcular(ent)
        if out is None:
            continue
        restantes = [v for v in VETORES_ART_59 if v not in ent.circunstancias_art59]
        if not restantes:
            continue
        mais = ent.model_copy(update={"circunstancias_art59": {
            **ent.circunstancias_art59, restantes[0]: CircunstanciaArt59(desfavoravel=True, fundamentacao="fictícia")}})
        out2 = _calcular(mais)
        if out2 is None:
            continue
        assert _dias(out2.pena_base) >= _dias(out.pena_base), falha(i, f"pena-base diminuiu ao agravar {restantes[0]}")


def test_confissao_nunca_aumenta_a_pena_intermediaria():
    for i, ent in casos(lambda r: _entrada(r, confissao_espontanea=False)):
        sem = _calcular(ent)
        com = _calcular(ent.model_copy(update={"confissao_espontanea": True}))
        if sem is None or com is None:
            continue
        assert _dias(com.pena_intermediaria) <= _dias(sem.pena_intermediaria), falha(i, f"confissão aumentou a pena: {ent}")


def test_calculo_e_deterministico():
    for i, ent in casos(_entrada, n=25):
        a, b = _calcular(ent), _calcular(ent)
        if a is None:
            continue
        assert a.model_dump() == b.model_dump(), falha(i, "mesma entrada, resultados diferentes")


def test_pena_definitiva_sempre_positiva_e_especie_do_tipo():
    for i, ent in casos(_entrada):
        out = _calcular(ent)
        if out is None:
            continue
        assert _dias(out.pena_definitiva) > 0, falha(i, "pena definitiva zerada")
        assert out.crime_spec.especie_pena == get_penal_type_for_date(
            ent.crime_code, ent.data_fato, ameaca_qualificada=ent.ameaca_qualificada).especie_pena


def test_concurso_material_soma_por_especie_igual_a_soma_das_penas():
    def _dois(rng):
        return [_entrada(rng, data_fato=datetime.date(2025, 6, 1)), _entrada(rng, data_fato=datetime.date(2025, 6, 1))]

    validos = 0
    for i, entradas in casos(_dois, n=30):
        try:
            glob = calcular_dosimetria_global(entradas)
        except Exception as exc:
            if type(exc).__name__ in ("TipoPenalInexistenteNaDataError", "DadosInsuficientesError"):
                continue
            raise
        if glob.sem_condenacao or getattr(glob, "tipo_concurso", "material") != "material":
            continue
        validos += 1
        por_especie: dict[str, int] = {}
        for c in glob.crimes_calculados:
            por_especie[c.crime_spec.especie_pena] = por_especie.get(c.crime_spec.especie_pena, 0) + _dias(c.pena_definitiva)
        # Mapa completo (revisão cruzada do CODEX): nenhuma espécie pode sumir nem sobrar no total.
        assert {e: _dias(p) for e, p in glob.penas_por_especie.items() if _dias(p) > 0} == por_especie, falha(i, "soma por espécie divergente")
    assert validos >= 1, "a amostra não produziu nenhum concurso material válido"
