"""Cômputo de dias cautelares para o regime inicial (CPP, art. 387, § 2º)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, model_validator

from app.core.errors import DadosInsuficientesError

# Lei 12.736/2012, art. 3º: vigência na data da publicação em 03/12/2012.
# https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2012/lei/l12736.htm
VIGENCIA_DETRACAO_REGIME = dt.date(2012, 12, 3)


def _data_civil(valor: object) -> object:
    if valor is None or type(valor) is dt.date or isinstance(valor, str):
        return valor
    raise ValueError("informe uma data civil em AAAA-MM-DD")


DataCustodia = Annotated[dt.date, BeforeValidator(_data_civil)]
OrigemDetracao = Literal["autos", "juizo", "legado"]


class IntervaloCustodia(BaseModel):
    """O fim documenta a soltura ou o último dia verificado de custódia."""

    model_config = ConfigDict(extra="forbid")
    inicio: DataCustodia
    fim: DataCustodia | None = None
    fonte_inicio: str = Field(min_length=1)
    fonte_fim: str = ""
    em_curso: StrictBool = False

    @model_validator(mode="after")
    def validar_fim(self) -> IntervaloCustodia:
        if self.fim and self.fim < self.inicio:
            raise ValueError("a soltura deve ser posterior ou igual ao início da custódia")
        if self.fim and self.em_curso:
            raise ValueError("informe a soltura ou confirme que a prisão está em curso")
        if (self.fim or self.em_curso) and not self.fonte_fim.strip():
            raise ValueError("informe a fonte da soltura ou da custódia em curso")
        if not self.fonte_inicio.strip():
            raise ValueError("informe a fonte do início da custódia")
        return self


class PeriodoDetraido(BaseModel):
    inicio: dt.date
    fim: dt.date
    fontes: list[str]
    em_curso: bool = False


class ResultadoDetracao(BaseModel):
    dias: int = Field(default=0, strict=True, ge=0)
    origem: OrigemDetracao = "autos"
    periodos: list[PeriodoDetraido] = Field(default_factory=list)
    pendencias: list[str] = Field(default_factory=list)

    @property
    def descricao_periodos(self) -> str:
        return "; ".join(
            f"entre {p.inicio:%d/%m/%Y} e {p.fim:%d/%m/%Y} ({' e '.join(p.fontes)})"
            + (" — custódia em curso até a data de referência" if p.em_curso else "")
            for p in self.periodos
        )


def detracao_por_quantidade(valor: object, caminho: str = "dias_prisao_cautelar") -> ResultadoDetracao:
    """Conversão legada explícita, sem vincular a quantidade a datas não comprovadas."""
    if type(valor) is not int or valor < 0:
        raise DadosInsuficientesError([caminho], "Informe uma quantidade inteira e não negativa de dias.")
    return ResultadoDetracao(dias=valor, origem="legado")


def calcular_detracao(
    intervalos: list[IntervaloCustodia],
    *,
    data_referencia: dt.date | None = None,
    origem: OrigemDetracao = "autos",
    fontes_validas: set[str] | None = None,
    caminho: str = "intervalos_prisao",
) -> ResultadoDetracao:
    """Soma a união dos dias de custódia comprovados, incluindo os dias inicial e final."""
    periodos = []
    pendencias = []
    for indice, intervalo in enumerate(intervalos):
        campo = f"{caminho}.{indice}"
        if fontes_validas is not None:
            for nome in ("fonte_inicio", "fonte_fim"):
                valor = getattr(intervalo, nome)
                if valor and valor not in fontes_validas:
                    raise DadosInsuficientesError([f"{campo}.{nome}"], "A fonte de custódia deve pertencer aos autos ativos.")
        fim = data_referencia if intervalo.em_curso else intervalo.fim
        if fim is None:
            pendencias.append(f"Confira a soltura ou a continuidade da custódia indicada em {intervalo.fonte_inicio}.")
            continue
        if fim < intervalo.inicio or (data_referencia and fim > data_referencia):
            if origem == "juizo":
                raise DadosInsuficientesError([campo], "O intervalo deve estar dentro da data de referência da sentença.")
            pendencias.append(f"Confira as datas da custódia indicada em {intervalo.fonte_inicio}.")
            continue
        periodos.append(PeriodoDetraido(
            inicio=intervalo.inicio, fim=fim,
            fontes=list(dict.fromkeys((intervalo.fonte_inicio, intervalo.fonte_fim))),
            em_curso=intervalo.em_curso,
        ))

    unidos: list[PeriodoDetraido] = []
    for periodo in sorted(periodos, key=lambda p: (p.inicio, p.fim)):
        if not unidos or (periodo.inicio - unidos[-1].fim).days > 1:
            unidos.append(periodo)
            continue
        anterior = unidos[-1]
        anterior.fim = max(anterior.fim, periodo.fim)
        anterior.fontes = list(dict.fromkeys(anterior.fontes + periodo.fontes))
        anterior.em_curso |= periodo.em_curso

    if data_referencia is not None and data_referencia < VIGENCIA_DETRACAO_REGIME:
        return ResultadoDetracao(origem=origem, pendencias=pendencias)
    return ResultadoDetracao(
        dias=sum((p.fim - p.inicio).days + 1 for p in unidos),
        origem=origem, periodos=unidos, pendencias=pendencias,
    )
