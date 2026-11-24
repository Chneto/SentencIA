"""Leitura de CAC/FAC sem atribuir efeitos penais a uma anotação automaticamente."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import unicodedata
from collections.abc import Iterable
from typing import Protocol

from app.legal_engine.antecedentes import FonteAntecedente, RegistroAntecedente


class Certidao(Protocol):
    doc_id: str
    folha: str
    conteudo: str


_CNJ = r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}"
_DATA = r"\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{4}"
_REGISTRO = re.compile(
    rf"(?:^|(?<=[\n;]))[ \t]*(?:\d+[.)]\s*)?(?:"
    rf"(?:processo|autos|acao penal)\s*(?:n[º°o.]?\s*)?[: ]*\s*{_CNJ}\b"
    rf"|{_CNJ}\b|(?:registro|anotacao|ocorrencia)\s*(?:n[º°o.]?\s*)?\d+\s*[:.)-]|\d+[.)][ \t]+)",
)
_NEGATIVA = re.compile(r"\b(?:nada consta(?:r)?|nada existe|nao constam condenacoes|sem antecedentes)\b")
_CENSURA = re.compile(r"\b(?:sem|nao|inexistem?|ausencia de)\b[^.;\n]{0,70}$")
_DEFINITIVA = re.compile(
    r"\b(?:condenacao (?:penal )?definitiva|sentenca condenatoria definitiva|"
    r"(?:condenacao (?:penal )?|sentenca condenatoria )transitad[ao] em julgado)\b"
    r"(?!\s+(?:pendente|para (?:a |o )?(?:acusacao|defesa|ministerio publico)))"
)
_CONDENACAO = re.compile(r"\b(?:condenacao(?: penal)?|sentenca condenatoria)\b")
_EM_CURSO = re.compile(
    r"\b(?:acao penal em (?:curso|andamento)|inquerito(?: policial)? em (?:curso|andamento)|"
    r"processo em (?:curso|andamento)|sem transito em julgado|transito em julgado pendente)\b"
)
_SEM_EFEITO = re.compile(r"\b(?:absolvicao definitiva|sentenca absolutoria|arquivamento definitivo|prescricao da pretensao punitiva)\b")
_PENA_EM_CURSO = re.compile(r"\b(?:pena em cumprimento|execucao penal em curso|pena em execucao)\b")
_DATAS_ROTULADAS = {
    "data_fato_anterior": r"(?:data d[oa] (?:fato(?: anterior)?|delito)|fato (?:anterior |criminoso )?(?:ocorrido|praticado))",
    "data_transito": r"(?:data d[oe] )?transit(?:o|ad[ao]) em julgado(?: definitivo| para ambas as partes)?",
    "inicio_periodo_prova": r"(?:inicio d[oe] periodo de prova|periodo de prova iniciado)",
    "data_cumprimento_extincao": (
        r"(?:data (?:do |de |da )?)?(?:cumprimento(?: integral)? (?:da )?pena|extincao da pena|"
        r"pena (?:integralmente )?cumprida|pena extinta)"
    ),
}


def _normalizar(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if not unicodedata.combining(c))


def _afirmado(padrao: re.Pattern[str], texto: str) -> bool:
    return any(
        not _CENSURA.search(texto[max(0, achado.start() - 72):achado.start()])
        for achado in padrao.finditer(texto)
    )


def _data(valor: str) -> dt.date | None:
    if not valor:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor):
            return dt.date.fromisoformat(valor)
        dia, mes, ano = (int(p) for p in re.split(r"[/.-]", valor))
        return dt.date(ano, mes, dia)
    except ValueError:
        return None


def _datas(texto: str) -> tuple[dict[str, dt.date | None], bool]:
    datas = {}
    conflito = False
    for campo, rotulo in _DATAS_ROTULADAS.items():
        padrao = re.compile(rf"\b{rotulo}[ \t]*(?:(?:em|no dia)[ \t]+|[:=-][ \t]*)?({_DATA})\b")
        valores = {
            _data(m.group(1)) for m in padrao.finditer(texto)
            if not _CENSURA.search(texto[max(0, m.start() - 72):m.start()])
        }
        if len(valores) > 1 or (valores and None in valores):
            conflito = True
        datas[campo] = next(iter(valores)) if len(valores) == 1 else None
    return datas, conflito


def _partes(texto: str) -> Iterable[str]:
    for bloco in re.split(r"\n[ \t]*\n", texto):
        limites = [0, *(m.start() for m in _REGISTRO.finditer(_normalizar(bloco)) if m.start()), len(bloco)]
        for ini, fim in zip(limites, limites[1:]):
            if trecho := bloco[ini:fim].strip(" \r\n;"):
                yield trecho


def ler_certidoes(certidoes: Iterable[Certidao]) -> tuple[list[RegistroAntecedente], FonteAntecedente | None]:
    """Separa registros identificáveis e conserva as fontes de cada ocorrência."""
    registros: dict[str, RegistroAntecedente] = {}
    negativa = None
    for certidao in certidoes:
        for numero, trecho in enumerate(_partes(certidao.conteudo), start=1):
            texto = _normalizar(trecho)
            fonte = FonteAntecedente(fonte_id=certidao.doc_id, folha=certidao.folha, trecho=trecho)
            if achado := _NEGATIVA.search(texto):
                negativa = negativa or fonte
                # Uma cláusula negativa pode citar a expressão "condenação transitada".
                texto = texto[:achado.start()] + re.sub(r"^[^.;\n]*", "", texto[achado.end():], count=1)
            texto = re.sub(r"\s+", " ", texto)
            em_curso = bool(_EM_CURSO.search(texto))
            sem_efeito = _afirmado(_SEM_EFEITO, texto)
            cnjs = set(re.findall(_CNJ, texto))
            datas, conflito = _datas(texto)
            definitivo = _afirmado(_DEFINITIVA, texto) or (
                bool(datas["data_transito"]) and _afirmado(_CONDENACAO, texto)
            )
            processo = next(iter(cnjs)) if len(cnjs) == 1 else ""
            if not (cnjs or definitivo or em_curso or sem_efeito or any(datas.values()) or conflito):
                continue
            if em_curso and definitivo:
                # Em texto sem separadores confiáveis, o trânsito pode pertencer a outro registro.
                conflito = True
            situacao = (
                "definitiva" if definitivo else "em_curso" if em_curso
                else "sem_efeito" if sem_efeito else "indeterminada"
            )
            identidade = processo or f"{certidao.doc_id}:{numero}:{texto}"
            id_registro = "ant-" + hashlib.sha256(identidade.encode()).hexdigest()[:20]
            registro = RegistroAntecedente(
                id_registro=id_registro, processo=processo, situacao=situacao,
                pena_em_curso=_afirmado(_PENA_EM_CURSO, texto),
                periodo_prova_mencionado=bool(re.search(r"\b(?:periodo de prova|sursis|livramento condicional)\b", texto)),
                prova_sem_revogacao=bool(re.search(r"\b(?:sem revogacao|nao revogado|nao revogada)\b", texto)),
                exclui_reincidencia=bool(re.search(r"\b(?:crime militar proprio|crime politico)\b", texto)),
                conflito=conflito or len(cnjs) > 1 or (definitivo and sem_efeito),
                fontes=[fonte], **datas,
            )
            anterior = registros.setdefault(id_registro, registro)
            if anterior is registro:
                continue
            anterior.fontes.append(fonte)
            anterior.conflito |= registro.conflito or anterior.situacao != registro.situacao
            anterior.pena_em_curso |= registro.pena_em_curso
            anterior.periodo_prova_mencionado |= registro.periodo_prova_mencionado
            anterior.prova_sem_revogacao |= registro.prova_sem_revogacao
            anterior.exclui_reincidencia |= registro.exclui_reincidencia
            for campo in _DATAS_ROTULADAS:
                atual, novo = getattr(anterior, campo), getattr(registro, campo)
                if atual and novo and atual != novo:
                    anterior.conflito = True
                elif not atual:
                    setattr(anterior, campo, novo)
    return list(registros.values()), negativa
