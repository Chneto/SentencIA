"""Análise de procedibilidade com os atos confirmados pelo juízo por imputação."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import BeforeValidator, BaseModel, ConfigDict, StrictBool

from app.legal_engine.penal_types_catalog import PenalTypeSpec


VIGENCIA_LEI_15160 = date(2025, 7, 4)
VIGENCIA_LEI_15380 = date(2026, 4, 7)
VIGENCIA_LEI_15438 = date(2026, 6, 19)


def _validar_data_de_ato(valor: object) -> object:
    if type(valor) is date:
        return valor
    if isinstance(valor, str) and len(valor) == 10 and valor[4] == valor[7] == "-":
        return date.fromisoformat(valor)
    raise ValueError("informe uma data no formato AAAA-MM-DD")


DataDeAto = Annotated[date, BeforeValidator(_validar_data_de_ato)]


class CondicoesAcaoPenal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    violencia_domestica_familiar_contra_mulher: StrictBool | None = None
    data_ciencia_autoria: DataDeAto | None = None
    queixa_subsidiaria: StrictBool = False
    data_esgotamento_prazo_denuncia: DataDeAto | None = None
    direito_exercido: StrictBool | None = None
    data_exercicio_direito: DataDeAto | None = None
    pedido_expresso_retratacao: StrictBool | None = None
    data_pedido_retratacao: DataDeAto | None = None
    denuncia_ainda_nao_recebida: StrictBool | None = None
    data_recebimento_denuncia: DataDeAto | None = None
    data_audiencia_retratacao: DataDeAto | None = None


class AnaliseDecadencia(BaseModel):
    estado: Literal["nao_aplicavel", "pendente", "em_curso", "tempestivo", "intempestivo", "prazo_transcorrido"]
    prazo_meses: int | None = None
    data_inicio: date | None = None
    ultimo_dia: date | None = None
    fundamento: str = ""


class AnaliseRetratacao(BaseModel):
    estado: Literal["nao_aplicavel", "pendente", "sem_pedido_expresso", "posterior_ao_recebimento", "cabivel"]
    fundamento: str = ""


class AnaliseAcaoPenal(BaseModel):
    acao_penal: str
    decadencia: AnaliseDecadencia
    retratacao: AnaliseRetratacao

    def resumir(self, imputacao: str) -> list[str]:
        avisos = []
        if self.decadencia.estado != "nao_aplicavel":
            rotulos = {
                "pendente": "pendente de confirmação",
                "em_curso": "prazo em curso",
                "tempestivo": "direito exercido no prazo",
                "intempestivo": "exercício após o prazo",
                "prazo_transcorrido": "prazo transcorrido sem exercício do direito, conforme registro do juízo",
            }
            prazo = f" Último dia: {self.decadencia.ultimo_dia:%d/%m/%Y}." if self.decadencia.ultimo_dia else ""
            avisos.append(f"{imputacao} — decadência: {rotulos[self.decadencia.estado]}.{prazo}")
        if self.retratacao.estado != "nao_aplicavel":
            rotulos = {
                "pendente": "pendente de confirmação",
                "sem_pedido_expresso": "sem pedido expresso confirmado",
                "posterior_ao_recebimento": "manifestação ou ato depois do recebimento",
                "cabivel": "requisitos para a audiência preenchidos",
            }
            avisos.append(f"{imputacao} — retratação: {rotulos[self.retratacao.estado]}.")
        return avisos


RegraEtaria = Literal["aplicavel", "excluida", "pendente", "nao_incidente"]


def estado_beneficio_etario(
    data_fato: date, requisito_etario: bool, violencia_sexual_contra_mulher: bool | None,
) -> RegraEtaria:
    """Arts. 65, I, e 115 do CP. A restrição da Lei 15.160/2025 respeita a data do fato."""
    if not requisito_etario:
        return "nao_incidente"
    if data_fato < VIGENCIA_LEI_15160:
        return "aplicavel"
    if violencia_sexual_contra_mulher is None:
        return "pendente"
    return "excluida" if violencia_sexual_contra_mulher else "aplicavel"


def _ultimo_dia_prazo(inicio: date, meses: int) -> date:
    """Art. 10 do CP. Calendário comum, com inclusão do dia inicial."""
    ano, mes = divmod(inicio.year * 12 + inicio.month - 1 + meses, 12)
    mes += 1
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    if inicio.day > ultimo_dia:
        return date(ano, mes, ultimo_dia)
    return date(ano, mes, inicio.day) - timedelta(days=1)


def _analisar_decadencia(
    acao: str, data_fato: date, data_analise: date, registro: CondicoesAcaoPenal,
) -> AnaliseDecadencia:
    subsidiaria = registro.queixa_subsidiaria
    if acao not in ("privada", "publica_condicionada_representacao") and not subsidiaria:
        return AnaliseDecadencia(estado="nao_aplicavel")
    contexto_domestico = registro.violencia_domestica_familiar_contra_mulher
    if data_fato >= VIGENCIA_LEI_15438 and contexto_domestico is None:
        return AnaliseDecadencia(estado="pendente", fundamento="Confirme o contexto de violência doméstica e familiar desta imputação.")
    prazo = 12 if contexto_domestico and data_fato >= VIGENCIA_LEI_15438 else 6
    marco = registro.data_esgotamento_prazo_denuncia if subsidiaria else registro.data_ciencia_autoria
    fundamento = "art. 103 do CP e art. 38 do CPP"
    if prazo == 12:
        fundamento = "art. 103, parágrafo único, do CP, art. 38, § 2º, do CPP e art. 16-A da Lei Maria da Penha"
    if marco is None or not data_fato <= marco <= data_analise:
        evento = "o esgotamento do prazo para oferecimento da denúncia" if subsidiaria else "a ciência da autoria"
        return AnaliseDecadencia(estado="pendente", prazo_meses=prazo, fundamento=f"Confirme a data de {evento}.")
    fim = _ultimo_dia_prazo(marco, prazo)
    exercicio = registro.data_exercicio_direito
    if registro.direito_exercido and exercicio and marco <= exercicio <= data_analise:
        estado = "tempestivo" if exercicio <= fim else "intempestivo"
    elif registro.direito_exercido is False and exercicio is None:
        estado = "em_curso" if data_analise <= fim else "prazo_transcorrido"
    else:
        estado = "pendente"
    return AnaliseDecadencia(
        estado=estado, prazo_meses=prazo, data_inicio=marco, ultimo_dia=fim, fundamento=fundamento,
    )


def _analisar_retratacao(
    acao: str, data_fato: date, data_analise: date, recebimento: date | None, registro: CondicoesAcaoPenal,
) -> AnaliseRetratacao:
    contexto_domestico = registro.violencia_domestica_familiar_contra_mulher
    if acao != "publica_condicionada_representacao" or contexto_domestico is False:
        return AnaliseRetratacao(estado="nao_aplicavel")
    data_ato = registro.data_audiencia_retratacao or data_analise
    fundamento = (
        "art. 16, caput e parágrafo único, da Lei Maria da Penha"
        if data_ato >= VIGENCIA_LEI_15380 else "art. 16 da Lei Maria da Penha e Tema 1167/STJ"
    )
    if (contexto_domestico is None or registro.pedido_expresso_retratacao is None
            or not data_fato <= data_ato <= data_analise
            or (recebimento is not None and recebimento < data_fato)):
        return AnaliseRetratacao(estado="pendente", fundamento=fundamento)
    if registro.pedido_expresso_retratacao is False and registro.data_pedido_retratacao is None:
        return AnaliseRetratacao(estado="sem_pedido_expresso", fundamento=fundamento)
    pedido = registro.data_pedido_retratacao
    if not registro.pedido_expresso_retratacao or pedido is None or not data_fato <= pedido <= data_ato or pedido == recebimento:
        return AnaliseRetratacao(estado="pendente", fundamento=fundamento)
    if recebimento is None and registro.denuncia_ainda_nao_recebida is not True:
        return AnaliseRetratacao(estado="pendente", fundamento=fundamento)
    if recebimento is not None and (pedido > recebimento or data_ato > recebimento):
        return AnaliseRetratacao(estado="posterior_ao_recebimento", fundamento=fundamento)
    if recebimento == data_ato:
        return AnaliseRetratacao(estado="pendente", fundamento=fundamento)
    return AnaliseRetratacao(estado="cabivel", fundamento=fundamento)


def analisar_acao_penal(
    spec: PenalTypeSpec,
    data_fato: date,
    data_analise: date,
    recebimento: date | None,
    registro: CondicoesAcaoPenal,
    *,
    enquadramento_pendente: bool = False,
) -> AnaliseAcaoPenal:
    if enquadramento_pendente:
        aviso = "Confirme o enquadramento da ameaça para definir a natureza da ação penal."
        return AnaliseAcaoPenal(
            acao_penal="enquadramento_pendente",
            decadencia=AnaliseDecadencia(estado="pendente", fundamento=aviso),
            retratacao=AnaliseRetratacao(estado="pendente", fundamento=aviso),
        )
    acao = spec.acao_penal
    recebimento_confirmado = registro.data_recebimento_denuncia or recebimento
    return AnaliseAcaoPenal(
        acao_penal=acao,
        decadencia=_analisar_decadencia(acao, data_fato, data_analise, registro),
        retratacao=_analisar_retratacao(acao, data_fato, data_analise, recebimento_confirmado, registro),
    )
