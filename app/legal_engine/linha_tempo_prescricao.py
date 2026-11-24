"""Linha do tempo documentada da prescrição da pretensão punitiva por imputação."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator


def _validar_data(valor: object) -> date:
    if type(valor) is date:
        return valor
    if isinstance(valor, str) and len(valor) == 10 and valor[4] == valor[7] == "-":
        return date.fromisoformat(valor)
    raise ValueError("informe uma data no formato AAAA-MM-DD")


DataMarco = Annotated[date, BeforeValidator(_validar_data)]
FonteMarco = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ModalidadePrescricao = Literal["abstrata", "retroativa"]
EstadoPrescricao = Literal["sem_indicio", "abstrata", "projetada", "pendente", "inconclusiva", "reconhecida"]

_LEI_9271 = date(1996, 6, 17)
_LEI_11596 = date(2007, 11, 30)
_LEI_13964 = date(2020, 1, 23)
_UM_DIA = timedelta(days=1)


class MarcoComFonte(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: DataMarco
    fonte_id: FonteMarco


class TermoInicialPrescricao(MarcoComFonte):
    tipo: Literal["consumacao", "cessacao_tentativa", "cessacao_permanencia", "ciencia_fato_registro_civil", "implemento_idade_vitima"]


class MarcoInterruptivo(MarcoComFonte):
    tipo: Literal[
        "recebimento_denuncia_ou_queixa", "pronuncia", "confirmacao_pronuncia",
        "publicacao_sentenca_condenatoria", "publicacao_acordao_condenatorio",
        "publicacao_acordao_confirmatorio",
    ]


class SuspensaoPrescricao(MarcoComFonte):
    tipo: Literal[
        "questao_prejudicial", "cumprimento_pena_exterior", "recurso_inadmissivel", "acordo_nao_persecucao_penal",
        "citacao_edital_art366", "citacao_exterior_art368", "suspensao_condicional_processo",
    ]
    data_fim: DataMarco | None = None

    @model_validator(mode="after")
    def validar_ordem(self) -> SuspensaoPrescricao:
        if self.data_fim is not None and self.data_fim <= self.data:
            raise ValueError("a retomada do prazo deve ser posterior ao início da suspensão")
        return self


class EstabilidadeParaAcusacao(MarcoComFonte):
    tipo: Literal["transito_para_acusacao", "recurso_da_acusacao_improvido"]


class DecisaoPrescricao(MarcoComFonte):
    modalidade: ModalidadePrescricao


class PenaDocumentada(MarcoComFonte):
    """Pena concreta e data de publicação da decisão de referência."""

    anos: int = Field(strict=True, ge=0, le=150)
    meses: int = Field(strict=True, ge=0, le=11)
    dias: int = Field(strict=True, ge=0, le=29)

    @model_validator(mode="after")
    def validar_quantidade(self) -> PenaDocumentada:
        if not (self.anos or self.meses or self.dias):
            raise ValueError("informe a quantidade da pena privativa de liberdade documentada")
        return self


class LinhaTempoPrescricao(BaseModel):
    model_config = ConfigDict(extra="forbid")

    termo_inicial: TermoInicialPrescricao | None = None
    interrupcoes: list[MarcoInterruptivo] = Field(default_factory=list, max_length=100)
    suspensoes: list[SuspensaoPrescricao] = Field(default_factory=list, max_length=100)
    pena_documentada: PenaDocumentada | None = None
    estabilidade_acusacao: EstabilidadeParaAcusacao | None = None
    reconhecimento: DecisaoPrescricao | None = None

    @model_validator(mode="after")
    def validar_recebimento_unico(self) -> LinhaTempoPrescricao:
        if sum(m.tipo == "recebimento_denuncia_ou_queixa" for m in self.interrupcoes) > 1:
            raise ValueError("confirme um único recebimento efetivo para esta imputação")
        return self


class LapsoPrescricional(BaseModel):
    inicio: date
    fim: date
    primeiro_dia_consumado: date
    dias_suspensos: int
    consumado: bool


def primeiro_dia_consumado(inicio: date, meses: int) -> date:
    """Contagem pelo calendário comum com inclusão do dia inicial (CP, art. 10)."""
    ano, mes = divmod(inicio.year * 12 + inicio.month - 1 + meses, 12)
    mes += 1
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    if inicio.day > ultimo_dia:
        return date(ano, mes, ultimo_dia) + _UM_DIA
    return date(ano, mes, inicio.day)


def validar_vigencia_marcos(dados: LinhaTempoPrescricao, data_fato: date) -> list[str]:
    avisos = []
    if data_fato < _LEI_9271 and any(m.tipo in ("citacao_edital_art366", "citacao_exterior_art368") for m in dados.suspensoes):
        avisos.append("Revise a suspensão para fato anterior à vigência das hipóteses da Lei 9.271/1996.")
    if data_fato < _LEI_11596 and any(m.tipo.startswith("publicacao_acordao") for m in dados.interrupcoes):
        avisos.append("Revise o efeito interruptivo do acórdão para o fato anterior à vigência da Lei 11.596/2007.")
    if data_fato < _LEI_13964 and any(m.tipo in ("recurso_inadmissivel", "acordo_nao_persecucao_penal") for m in dados.suspensoes):
        avisos.append("Revise a suspensão indicada para fato anterior à vigência das hipóteses da Lei 13.964/2019.")
    return avisos


def intervalos_suspensos(
    suspensoes: list[SuspensaoPrescricao], fim: date, prazo_abstrato_meses: int,
) -> list[tuple[date, date]]:
    """Une sobreposições. O termo de retomada não é dia suspenso."""
    intervalos = []
    for marco in suspensoes:
        if marco.data >= fim:
            continue
        retomada = min(fim, marco.data_fim or fim)
        if marco.tipo == "citacao_edital_art366":
            retomada = min(retomada, primeiro_dia_consumado(marco.data, prazo_abstrato_meses))
        if retomada > marco.data:
            intervalos.append((marco.data, retomada))
    unidos: list[tuple[date, date]] = []
    for inicio, retorno in sorted(intervalos):
        if unidos and inicio <= unidos[-1][1]:
            unidos[-1] = (unidos[-1][0], max(unidos[-1][1], retorno))
        else:
            unidos.append((inicio, retorno))
    return unidos


def calcular_lapsos_prescricionais(
    inicio: date,
    fim: date,
    prazo_meses: int,
    interrupcoes: set[date],
    suspensoes: list[tuple[date, date]],
) -> list[LapsoPrescricional]:
    """Avalia cada lapso antes da interrupção, que reinicia o prazo."""
    if inicio > fim:
        return []
    marcos = [inicio, *sorted(data for data in interrupcoes if inicio < data < fim), fim]
    lapsos = []
    for termo, limite in zip(marcos, marcos[1:]):
        consumacao = primeiro_dia_consumado(termo, prazo_meses)
        dias_suspensos = 0
        for inicio_suspensao, retorno in suspensoes:
            suspensao_efetiva = max(termo, inicio_suspensao)
            if suspensao_efetiva >= min(consumacao, limite):
                break
            dias = (min(limite, retorno) - suspensao_efetiva).days
            if dias > 0:
                dias_suspensos += dias
                consumacao += timedelta(days=dias)
        lapsos.append(LapsoPrescricional(
            inicio=termo, fim=limite, primeiro_dia_consumado=consumacao,
            dias_suspensos=dias_suspensos, consumado=limite >= consumacao,
        ))
    return lapsos
