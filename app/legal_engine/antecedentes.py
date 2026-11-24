"""Critérios de admissibilidade dos registros e seleção por imputação."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.errors import DadosInsuficientesError

VIGENCIA_LEI_7209 = dt.date(1985, 1, 13)
FaseAntecedente = Literal["antecedentes", "reincidencia", "desconsiderar"]


class FonteAntecedente(BaseModel):
    fonte_id: str
    folha: str
    trecho: str


class RegistroAntecedente(BaseModel):
    id_registro: str
    processo: str = ""
    situacao: Literal["definitiva", "em_curso", "sem_efeito", "indeterminada"]
    data_fato_anterior: dt.date | None = None
    data_transito: dt.date | None = None
    data_cumprimento_extincao: dt.date | None = None
    pena_em_curso: bool = False
    inicio_periodo_prova: dt.date | None = None
    periodo_prova_mencionado: bool = False
    prova_sem_revogacao: bool = False
    exclui_reincidencia: bool = False
    conflito: bool = False
    fontes: list[FonteAntecedente] = Field(default_factory=list)


class UsoAntecedente(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id_registro: str = Field(min_length=1)
    fase: FaseAntecedente


def _cinco_anos(data: dt.date) -> dt.date:
    # Lei 810/1949, art. 3º, para aniversário sem o dia correspondente.
    # https://planalto.gov.br/ccivil_03/leis/1930-1949/l810-49.htm
    if data.year > 9994:
        return dt.date.max
    try:
        return data.replace(year=data.year + 5)
    except ValueError:
        return dt.date(data.year + 5, 3, 1)


def fases_admissiveis(
    registro: RegistroAntecedente, fato: dt.date | None, sentenca: dt.date,
) -> set[FaseAntecedente]:
    """Verifica as datas documentadas. A aplicação depende de ato do juízo.

    CP, arts. 59, 63 e 64, I (Lei 7.209/1984, vigência 13/01/1985):
    https://www.planalto.gov.br/ccivil_03/leis/1980-1988/l7209.htm
    Tema 150/STF: https://portal.stf.jus.br/jurisprudenciaRepercussao/verAndamentoProcesso.asp?classeProcesso=RE&incidente=2642160&numeroProcesso=593818&numeroTema=150
    """
    transito = registro.data_transito
    anterior = registro.data_fato_anterior
    extincao = registro.data_cumprimento_extincao
    fases: set[FaseAntecedente] = {"desconsiderar"}
    if (
        registro.situacao != "definitiva" or registro.conflito
        or not fato or fato < VIGENCIA_LEI_7209 or not transito or transito > sentenca
        or (anterior and (anterior >= fato or anterior > transito))
        or (extincao and anterior and extincao < anterior)
    ):
        return fases
    if (anterior and anterior < fato) or transito < fato:
        fases.add("antecedentes")
    if registro.exclui_reincidencia:
        return fases
    if registro.prova_sem_revogacao:
        inicio_prova = registro.inicio_periodo_prova
        if not (inicio_prova and extincao and transito <= inicio_prova <= extincao):
            return fases
        extincao = inicio_prova
    elif registro.periodo_prova_mencionado:
        return fases
    if transito < fato and (
        (extincao is not None and (extincao >= fato or fato <= _cinco_anos(extincao)))
        or (extincao is None and registro.pena_em_curso)
    ):
        fases.add("reincidencia")
    return fases


def validar_usos_antecedentes(
    dados: object,
    registros: list[RegistroAntecedente],
    fato: dt.date,
    sentenca: dt.date,
    caminho: str,
) -> dict[FaseAntecedente, list[RegistroAntecedente]]:
    por_fase: dict[FaseAntecedente, list[RegistroAntecedente]] = {}
    if not isinstance(dados, list):
        raise DadosInsuficientesError([caminho], "Informe uma lista de registros com uma decisão por item.")
    por_id = {registro.id_registro: registro for registro in registros}
    usados = set()
    for indice, dado in enumerate(dados):
        campo = f"{caminho}.{indice}"
        try:
            uso = UsoAntecedente.model_validate(dado)
        except ValidationError as exc:
            caminhos = [".".join((campo, *map(str, erro["loc"]))) for erro in exc.errors()]
            raise DadosInsuficientesError(caminhos, "Confira o registro e a fase de valoração.") from exc
        registro = por_id.get(uso.id_registro)
        if registro is None:
            raise DadosInsuficientesError([campo + ".id_registro"], "O registro não pertence às certidões destes autos.")
        if uso.id_registro in usados:
            raise DadosInsuficientesError([campo + ".id_registro"], "Selecione somente uma destinação por registro nesta imputação.")
        usados.add(uso.id_registro)
        if uso.fase not in fases_admissiveis(registro, fato, sentenca):
            raise DadosInsuficientesError([campo + ".fase"], "As datas e a situação documentadas não permitem a valoração na fase escolhida.")
        por_fase.setdefault(uso.fase, []).append(registro)
    antecedentes = por_fase.get("antecedentes", [])
    reincidencia = por_fase.get("reincidencia", [])
    if antecedentes and reincidencia:
        processos_1 = {r.processo for r in antecedentes}
        processos_2 = {r.processo for r in reincidencia}
        if "" in processos_1 | processos_2 or not processos_1.isdisjoint(processos_2):
            raise DadosInsuficientesError(
                [caminho],
                "Confira o número dos processos antes de valorar registros em duas fases. É preciso confirmar que são condenações distintas.",
            )
    return por_fase
