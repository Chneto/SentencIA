"""Identidade, posição e alcance das ocorrências extraídas de manifestações."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from app.core.text_utils import normalizar
from app.ingestion.imputacoes import ImputacaoExtraida, localizar_tipos_penais


class OcorrenciaProcessual(BaseModel):
    parte: Literal["mp", "defesa", "juizo", ""] = ""
    ordem: int = 0
    posicao: int = 0
    natureza: Literal["principal", "subsidiario"] = "principal"
    id_imputacao: str = ""
    numero_fato: str = ""


_FATO = re.compile(r"\bfato\s*(?:n[º°o.]?\s*)?(\d+)\b")
_DATA = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b")
_SUBSIDIARIO = re.compile(r"\b(?:subsidiariamente|subsidiari[oa]s?|alternativamente|sucessivamente)\b")


def natureza_ocorrencia(texto: str, posicao: int, *, subsidiario: bool = False) -> Literal["principal", "subsidiario"]:
    antes = texto[max(0, posicao - 180):posicao]
    trecho = re.split(r"[.!?;\n]", antes)[-1]
    return "subsidiario" if subsidiario or _SUBSIDIARIO.search(normalizar(trecho)) else "principal"


def vincular_ocorrencia(ocorrencia: OcorrenciaProcessual, texto: str, imputacoes: list[ImputacaoExtraida], codigo: str = "") -> None:
    """Vincula somente quando as referências disponíveis resolvem um fato único."""
    numeros = set(_FATO.findall(normalizar(texto)))
    if len(numeros) == 1:
        ocorrencia.numero_fato = next(iter(numeros))
    datas = set()
    for data in _DATA.findall(texto):
        if "/" in data:
            dia, mes, ano = data.split("/")
            data = f"{ano}-{mes.zfill(2)}-{dia.zfill(2)}"
        datas.add(data)
    codigos_explicitos = {c for _, c in localizar_tipos_penais(texto)}
    codigos = codigos_explicitos or ({codigo} if codigo else set())
    if len(numeros) > 1 or len(codigos) > 1 or not (numeros or datas or codigos):
        return
    candidatos = [
        i for i in imputacoes
        if i.estado == "extraida"
        and (not numeros or i.numero_fato in numeros)
        and (not datas or i.data_fato in datas)
        and (not codigos or i.codigo in codigos)
    ]
    if len(candidatos) == 1:
        ocorrencia.id_imputacao = candidatos[0].id_imputacao
        ocorrencia.numero_fato = ocorrencia.numero_fato or candidatos[0].numero_fato
