"""Localiza eventos documentados de início e liberação para calcular detração."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Iterable, Literal, Protocol

from app.core.text_utils import normalizar
from app.legal_engine.detracao import IntervaloCustodia


class PecaCustodia(Protocol):
    doc_id: str
    folha: str
    titulo: str
    categoria: str
    conteudo: str
    data_peca: str


@dataclass(frozen=True)
class _Evento:
    data: dt.date
    tipo: Literal["inicio", "fim"]
    peca: PecaCustodia


_DATA = r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b"
_INICIO = re.compile(
    r"\b(?:pres[oa] em flagrante|foi pres[oa] preventivamente|mandado de prisao (?:foi )?cumprido|"
    r"prisao (?:em flagrante|preventiva) (?:foi )?(?:efetuada|cumprida)|capturad[oa])\b"
)
_FIM = re.compile(
    r"\b(?:alvara de soltura (?:foi )?cumprido|cumprido (?:o )?alvara de soltura|"
    r"cumprimento d[oe] alvara de soltura|efetivada a soltura|(?:posto|posta|colocado|colocada) em liberdade|"
    r"solt[oa] em)\b"
)
_TITULO_APF = re.compile(r"^(?:apf\b|auto de prisao em flagrante\b)")
_TITULO_CUMPRIMENTO = re.compile(r"(?:certidao de cumprimento (?:do |de )?alvara de soltura|alvara de soltura cumprido)")
_FONTES = {"BO", "DECISAO_MPU", "DESPACHO", "DECISAO_INTERLOCUTORIA", "CERTIDAO", "TERMO_AUDIENCIA", "OUTROS"}


def _data(valor: str) -> dt.date | None:
    try:
        if "/" in valor:
            dia, mes, ano = map(int, valor.split("/"))
            return dt.date(ano, mes, dia)
        return dt.date.fromisoformat(valor)
    except ValueError:
        return None


def _datas_rotuladas(texto: str, sinal: re.Pattern[str]) -> Iterable[dt.date]:
    for match in sinal.finditer(texto):
        antes = texto[max(0, match.start() - 24):match.start()]
        if re.search(r"\b(?:nao|se|quando) (?:foi )?$", antes):
            continue
        candidato = re.match(r"[^.;\n]{0,45}?" + _DATA, texto[match.end():match.end() + 60])
        if candidato and (data := _data(candidato.group(1))):
            yield data


def extrair_intervalos_custodia(pecas: Iterable[PecaCustodia]) -> tuple[list[IntervaloCustodia], list[str]]:
    """Reúne custódias sucessivas e mantém abertos os intervalos sem soltura documentada."""
    eventos: list[_Evento] = []
    for peca in pecas:
        if peca.categoria not in _FONTES or not peca.doc_id or peca.doc_id == "ID-CAPA-PJE":
            continue
        titulo = normalizar(peca.titulo)
        conteudo = "\n".join(normalizar(linha) for linha in peca.conteudo.splitlines())
        inicios = list(_datas_rotuladas(conteudo, _INICIO))
        fins = list(_datas_rotuladas(conteudo, _FIM))
        data_peca = _data(peca.data_peca)
        if data_peca and not inicios and _TITULO_APF.search(titulo):
            inicios.append(data_peca)
        if data_peca and not fins and _TITULO_CUMPRIMENTO.search(titulo):
            fins.append(data_peca)
        eventos.extend(_Evento(data, "inicio", peca) for data in inicios)
        eventos.extend(_Evento(data, "fim", peca) for data in fins)

    intervalos: list[IntervaloCustodia] = []
    pendencias: list[str] = []
    inicio: _Evento | None = None
    ultima_soltura: dt.date | None = None
    for evento in sorted(eventos, key=lambda e: (e.data, e.tipo == "fim")):
        if evento.tipo == "inicio":
            if inicio is None:
                inicio = evento
            continue
        if inicio is None:
            if evento.data != ultima_soltura:
                pendencias.append(f"Soltura em {evento.peca.doc_id} sem data de prisão vinculada.")
            continue
        intervalos.append(IntervaloCustodia(
            inicio=inicio.data, fim=evento.data,
            fonte_inicio=inicio.peca.doc_id, fonte_fim=evento.peca.doc_id,
        ))
        ultima_soltura = evento.data
        inicio = None

    if inicio is not None:
        intervalos.append(IntervaloCustodia(inicio=inicio.data, fonte_inicio=inicio.peca.doc_id))
    return intervalos, pendencias
