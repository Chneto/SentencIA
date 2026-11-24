"""Sugestões de confissão ancoradas na fala do acusado e no fato imputado."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from app.core.text_utils import normalizar
from app.ingestion.imputacoes import ImputacaoExtraida, localizar_tipos_penais

TipoConfissao = Literal["total", "parcial", "qualificada", "negativa", "inconclusiva", "ausente"]
TIPOS_AFIRMATIVOS = frozenset(("total", "parcial", "qualificada"))
EFEITO_CONFISSAO = "Atenuante na segunda fase, mediante decisão do juízo (art. 65, III, d, do CP e Súmula 545/STJ)."


class SugestaoConfissao(BaseModel):
    tipo: TipoConfissao = "ausente"
    fonte_id: str = ""
    folha: str = ""
    trecho: str = ""
    minuto: str = ""


class DecisaoConfissao(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: Literal["total", "parcial", "qualificada", "negativa", "ausente"]


class PecaConfissao(Protocol):
    doc_id: str
    folha: str
    titulo: str
    categoria: str
    conteudo: str


@dataclass(frozen=True)
class AlvoConfissao:
    chave: str
    codigo: str
    numero_fato: str = ""
    data_fato: str = ""


def alvos_confissao(codigos: Iterable[str], imputacoes: Iterable[ImputacaoExtraida]) -> list[AlvoConfissao]:
    por_codigo: dict[str, list[AlvoConfissao]] = defaultdict(list)
    for item in imputacoes:
        if item.estado == "extraida" and not any(e.tipo in ("rejeicao", "extincao") for e in item.eventos):
            por_codigo[item.codigo].append(AlvoConfissao(
                item.id_imputacao, item.codigo, item.numero_fato, item.data_fato,
            ))
    return [
        alvo
        for codigo in dict.fromkeys(codigos)
        for alvo in por_codigo.get(codigo, [AlvoConfissao(codigo, codigo)])
    ]


_GRUPOS = (
    ("art_129", r"\b(?:lesao corporal|lesoes|agressa\w*|agredi\w*|soco\w*|bat[ei]\w*|tapa\w*|empurr\w*|chut\w*)\b"),
    ("art_150", r"\b(?:violacao de domicilio|invad\w*|invasao|entre[io] na casa)\b"),
    ("art_147a", r"\bpersegui\w*\b"),
    ("art_147b", r"\bviolencia psicologica\b"),
    ("art_147_", r"\bameac\w*\b"),
    ("art_24a", r"\b(?:medidas? protetivas?|descumpri\w*)\b"),
    ("art_21_lcp", r"\bvias de fato\b"),
    ("art_138", r"\bcaluni\w*\b"),
    ("art_139", r"\bdifam\w*\b"),
    ("art_140", r"\b(?:injuri\w*|xing\w*)\b"),
    ("art_163", r"\b(?:dano|quebr\w*|destru\w*)\b"),
)
_PADROES_GRUPO = {prefixo: re.compile(expressao) for prefixo, expressao in _GRUPOS}
_REU = r"(?:reu|acusad[oa]|interrogand[oa]|denunciad[oa]|autuad[oa])"
_ASSUNCAO = r"(?:confess(?:ou|o|a|ei)|admit(?:iu|o|i|e)|reconhec(?:eu|o|i|e))"
_NEGOU = (
    r"(?:neg(?:ou|o|uei|a)|(?:nao|nunca|jamais|nem) "
    r"(?:se lembra|me lembro|recordo|recorda|confess\w*|admit\w*|pratic\w*|comet\w*|"
    r"agred\w*|ameac\w*|descumpr\w*|persegu\w*|empurr\w*|entr(?:ei|ou)|"
    r"bat(?:i|eu)|soquei|socou|xing\w*|invad\w*|fiz|fez))"
)
_ACAO_REU = r"(?:empurrei|ameacei|agredi|descumpri|persegui|bati|soquei|xinguei|invadi|entrei)"
_ACAO_TERC = r"(?:empurrou|ameacou|agrediu|descumpriu|perseguiu|bateu|socou|xingou|invadiu|entrou)"
_DOCUMENTO = re.compile(
    rf"\b{_REU}\s+(?:(?:disse|declarou|afirmou) que\s+)?"
    rf"(?:{_ASSUNCAO}|{_NEGOU}|{_ACAO_TERC})\b"
)
_FALA_REU = re.compile(
    rf"^\s*(?:fato\s*\d+\s*:\s*)?(?:{_REU}\s*:\s*)?"
    rf"(?:eu\s+)?(?:{_ASSUNCAO}|{_NEGOU}|{_ACAO_REU})\b"
)
_FALA_TERCEIRO = re.compile(
    r"^\s*(?:a |o )?(?:ofendida|vitima|testemunha|promotor[a]?|defensor[a]?|advogad[oa]|policial)\b"
    r"|\b(?:que|vi|ouvi) (?:o |a )?(?:reu|acusad[oa]|testemunha|vitima|ofendida|terceiro|vizinh[oa]|outr[oa])\b"
)
_TERCEIRO_ANTES = re.compile(
    r"\b(?:ofendida|vitima|testemunha|terceiro|policial|promotor[a]?|defensor[a]?|advogad[oa]|defesa|acusacao|ministerio publico|delegad[oa])\b"
)
_QUALIFICADORA = re.compile(r"\b(?:legitima defesa|para me defender|para se defender|so reagi(?:u)?|agiu em defesa)\b")
_PARCIAL = re.compile(r"\b(?:parcialmente|em parte|apenas|somente|mas neg\w*|porem neg\w*|mas nao)\b")
_NEGATIVA = re.compile(rf"\b{_NEGOU}\b")
_MARCAS_FATO = re.compile(r"\bfato\s*(?:n(?:[º°o.]|\s)*\s*)?(\d+)\b")
_DATA = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b")
_PAPEIS_REU = {"REU", "ACUSADO", "INTERROGATORIO_REU", "INTERROGATORIO"}


def _papel(valor: Any) -> str:
    return re.sub(r"[^A-Z]+", "_", normalizar(str(valor or "")).upper()).strip("_")


def _iso(data: str) -> str:
    if "/" not in data:
        return data
    dia, mes, ano = data.split("/")
    return f"{ano}-{mes.zfill(2)}-{dia.zfill(2)}"


def _fontes(
    pecas: Iterable[PecaConfissao], transcricoes: Iterable[Mapping[str, Any]],
) -> Iterable[tuple[str, str, str, str, bool]]:
    for transcricao in transcricoes:
        if not isinstance(transcricao, Mapping):
            continue
        papel = _papel(transcricao.get("papel"))
        fonte = str(transcricao.get("media_id") or "")
        if not fonte:
            continue
        segmentos = transcricao.get("segmentos") or transcricao.get("segmentos_literais") or []
        if segmentos:
            for segmento in segmentos:
                if not isinstance(segmento, Mapping):
                    continue
                papel_segmento = _papel(segmento.get("papel")) or papel
                if papel_segmento in _PAPEIS_REU:
                    yield (
                        str(segmento.get("texto_literal") or segmento.get("texto") or ""), fonte, "",
                        str(segmento.get("timestamp") or segmento.get("inicio") or ""), True,
                    )
        elif papel in _PAPEIS_REU:
            yield str(transcricao.get("transcript") or transcricao.get("texto") or ""), fonte, "", "", True

    for peca in pecas:
        if peca.categoria in ("TERMO_AUDIENCIA", "BO"):
            yield peca.conteudo, peca.doc_id, peca.folha, "", False


def _classificar_fala(texto: str, fala_reu: bool) -> tuple[TipoConfissao, str] | None:
    autor = _FALA_REU.search(texto) if fala_reu else None
    autor = autor or _DOCUMENTO.search(texto)
    if not autor or _FALA_TERCEIRO.search(texto):
        return None
    if _TERCEIRO_ANTES.search(texto[:autor.start()]):
        return None
    saida = texto
    # Uma negativa que abre a declaração prevalece; a negativa de parte do relato pode delimitá-lo.
    primeira = re.split(r"\b(?:mas|porem|contudo)\b", saida, maxsplit=1)[0]
    if _NEGATIVA.search(primeira):
        return "negativa", saida
    if _QUALIFICADORA.search(saida) and "defesa da honra" not in saida:
        return "qualificada", saida
    if _PARCIAL.search(saida) or _NEGATIVA.search(saida):
        return "parcial", saida
    return "total", saida


def _associar(texto: str, alvos: list[AlvoConfissao]) -> list[AlvoConfissao]:
    fatos = set(_MARCAS_FATO.findall(texto))
    datas = {_iso(data) for data in _DATA.findall(texto)}
    codigos = {codigo for _, codigo in localizar_tipos_penais(texto)}
    grupos = {prefixo for prefixo, regra in _PADROES_GRUPO.items() if regra.search(texto)}
    candidatos = [
        alvo for alvo in alvos
        if (not fatos or alvo.numero_fato in fatos)
        and (not datas or not alvo.data_fato or _iso(alvo.data_fato) in datas)
        and (not codigos or alvo.codigo in codigos)
        and (not grupos or any(alvo.codigo.startswith(grupo) for grupo in grupos))
    ]
    # A frase deve resolver uma única imputação. Uma admissão genérica sem vínculo fica no cartão informativo.
    if len(candidatos) == 1 and (fatos or datas or codigos or grupos):
        return candidatos
    return []


def detectar_confissoes(
    pecas: Iterable[PecaConfissao], transcricoes: Iterable[Mapping[str, Any]], alvos: list[AlvoConfissao],
) -> tuple[dict[str, SugestaoConfissao], SugestaoConfissao]:
    """Classifica evidência probatória sem reconhecer a atenuante."""
    achados: dict[str, list[SugestaoConfissao]] = defaultdict(list)
    geral = SugestaoConfissao()
    for texto, fonte, folha, minuto, fala_reu in _fontes(pecas, transcricoes):
        for frase in re.split(r"(?<=[!?;])\s+|(?<![Aa]rt\.)(?<=\.)\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ])|\n+", texto):
            frase = frase.strip()
            if not frase:
                continue
            normalizada = normalizar(frase)
            resultado = _classificar_fala(normalizada, fala_reu)
            if not resultado:
                continue
            tipo, teor = resultado
            registro = SugestaoConfissao(tipo=tipo, fonte_id=fonte, folha=folha, trecho=frase, minuto=minuto)
            if geral.tipo == "ausente":
                geral = registro
            partes = re.split(r"\b(?:mas|porem|contudo)\b", teor, maxsplit=1)
            primeira = _associar(partes[0], alvos)
            for alvo in primeira:
                achados[alvo.chave].append(registro)
            if len(partes) == 2 and _NEGATIVA.search(partes[1]):
                negativa = registro.model_copy(update={"tipo": "negativa"})
                for alvo in _associar(partes[1], alvos):
                    if alvo not in primeira:
                        achados[alvo.chave].append(negativa)

    sugestoes = {}
    for alvo in alvos:
        itens = achados[alvo.chave]
        if not itens:
            sugestoes[alvo.chave] = SugestaoConfissao()
            continue
        tipos = {item.tipo for item in itens}
        if "negativa" in tipos and tipos & TIPOS_AFIRMATIVOS:
            sugestoes[alvo.chave] = itens[0].model_copy(update={"tipo": "inconclusiva"})
        else:
            sugestoes[alvo.chave] = next(
                (item for item in itens if item.tipo in ("qualificada", "parcial")), itens[0],
            )
    return sugestoes, geral
