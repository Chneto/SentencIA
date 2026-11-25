"""Localiza eventos documentados de início e liberação para calcular detração."""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass, field
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


TipoEvento = Literal["inicio", "conversao", "fim"]


@dataclass(frozen=True)
class EventoCustodia:
    id: str
    data: dt.date
    tipo: TipoEvento
    fonte_id: str
    folha: str
    trecho: str
    contexto: str
    data_da_peca: bool = False
    referencias: tuple[str, ...] = ()
    datas_vinculadas: tuple[dt.date, ...] = ()


@dataclass
class ExtracaoCustodia:
    eventos: list[EventoCustodia] = field(default_factory=list)
    intervalos: list[IntervaloCustodia] = field(default_factory=list)
    pendencias: list[str] = field(default_factory=list)


_DATA = r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b"
_INICIO = re.compile(
    r"\b(?:pres[oa] em flagrante|foi pres[oa] preventivamente|mandado de prisao (?:foi )?cumprido|"
    r"prisao (?:em flagrante|preventiva) (?:foi )?(?:efetuada|cumprida)|capturad[oa])\b"
)
_FIM = re.compile(
    r"\b(?:alvara de soltura (?:foi )?cumprido|cumprido (?:o )?alvara(?: de soltura)?|"
    r"cumprimento d[oe] alvara de soltura|efetivada a soltura|(?:posto|posta|colocado|colocada) em liberdade|"
    r"solt[oa] em)\b"
)
_CONVERSAO = re.compile(
    r"\b(?:converteu(?:-se)? (?:a )?prisao em flagrante|prisao em flagrante (?:foi )?convertida) em (?:prisao )?preventiva\b"
)
_REFERENCIA_FONTE = re.compile(r"\bid(?:-[a-z0-9]+(?:-[a-z0-9]+)*|(?:\s+|\s*[:#]\s*)\d+)\b")
_REFERENCIA_DATA = re.compile(
    r"\b(?:custodia|prisao|flagrante)\s+(?:(?:iniciad[ao]|efetuada|cumprida|ocorrid[ao])\s+)?(?:em|de)\s+" + _DATA
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


def _eventos_da_peca(peca: PecaCustodia) -> Iterable[EventoCustodia]:
    titulo = normalizar(peca.titulo)
    extraidos: list[tuple[dt.date, TipoEvento, str, str, bool]] = []
    for linha in peca.conteudo.splitlines():
        texto = normalizar(linha)
        for tipo, sinal in (("inicio", _INICIO), ("conversao", _CONVERSAO), ("fim", _FIM)):
            for match in sinal.finditer(texto):
                if match[0].endswith("alvara") and "soltura" not in titulo:
                    continue
                antes = texto[max(0, match.start() - 24):match.start()]
                if re.search(r"\b(?:nao|se|quando) (?:foi )?$", antes):
                    continue
                candidato = re.match(r"\s*[,:\-]?\s*(?:(?:em|no dia|na data de)\s+)?" + _DATA, texto[match.end():])
                if candidato is None or not (data := _data(candidato.group(1))):
                    continue
                fim = match.end() + candidato.end()
                # Limita os vínculos à mesma oração do evento. Evita colher IDs e
                # datas narrados para outro evento no restante do documento.
                limites = [texto.find(delim, fim) for delim in ".;"]
                limite = min((pos for pos in limites if pos >= 0), default=len(texto))
                extraidos.append((data, tipo, linha.strip(), texto[fim:limite], False))

    data_peca = _data(peca.data_peca)
    for tipo, padrao_titulo in (("inicio", _TITULO_APF), ("fim", _TITULO_CUMPRIMENTO)):
        if data_peca and padrao_titulo.search(titulo) and not any(item[1] == tipo for item in extraidos):
            extraidos.append((data_peca, tipo, peca.titulo, "", True))

    for indice, (data, tipo, trecho, vinculo, data_da_peca) in enumerate(dict.fromkeys(extraidos), start=1):
        yield EventoCustodia(
            id=f"{peca.doc_id}:{indice}",
            data=data,
            tipo=tipo,
            fonte_id=peca.doc_id,
            folha=getattr(peca, "folha", ""),
            trecho=trecho[:240],
            contexto=peca.titulo,
            data_da_peca=data_da_peca,
            referencias=tuple(dict.fromkeys(
                re.sub(r"^id[\s:#]+", "id-", ref) for ref in _REFERENCIA_FONTE.findall(vinculo)
            )),
            datas_vinculadas=tuple(dict.fromkeys(
                data_ref for valor in _REFERENCIA_DATA.findall(vinculo) if (data_ref := _data(valor))
            )),
        )


def analisar_custodia(pecas: Iterable[PecaCustodia]) -> ExtracaoCustodia:
    """Faz vínculos documentais entre eventos. A ordem das datas só valida o período."""
    resultado = ExtracaoCustodia()
    resultado.eventos = sorted(
        (evento for peca in pecas
         if peca.categoria in _FONTES and peca.doc_id and peca.doc_id != "ID-CAPA-PJE"
         for evento in _eventos_da_peca(peca)),
        key=lambda evento: (evento.data, evento.tipo != "inicio", evento.tipo == "fim", evento.id),
    )
    inicios: dict[str, EventoCustodia] = {}
    por_fonte: dict[str, list[EventoCustodia]] = defaultdict(list)
    por_data: dict[dt.date, list[EventoCustodia]] = defaultdict(list)
    ancora: dict[str, EventoCustodia] = {}
    encerramentos: dict[str, list[EventoCustodia]] = defaultdict(list)

    for evento in resultado.eventos:
        if evento.tipo == "inicio":
            inicios[evento.id] = evento
            if not evento.data_da_peca:
                ancora[evento.id] = evento
        else:
            grupos = [por_fonte[ref] for ref in evento.referencias]
            grupos += [por_data[data] for data in evento.datas_vinculadas]
            candidatos = [
                {ancora[item.id].id for item in grupo if item.id in ancora and item.data <= evento.data}
                for grupo in grupos
            ]
            comuns = set.intersection(*candidatos) if candidatos else set()
            if len(comuns) != 1 or evento.data_da_peca:
                descricao = "Conversão" if evento.tipo == "conversao" else "Soltura"
                resultado.pendencias.append(
                    f"{descricao} em {evento.data:%d/%m/%Y} ({evento.fonte_id}) sem vínculo documental único. "
                    "Confirme o intervalo de custódia."
                )
                continue
            inicio = inicios[comuns.pop()]
            if evento.tipo == "fim":
                encerramentos[inicio.id].append(evento)
                continue
            ancora[evento.id] = inicio
        por_fonte[normalizar(evento.fonte_id)].append(evento)
        por_data[evento.data].append(evento)

    for inicio in inicios.values():
        fins = encerramentos[inicio.id]
        if len(fins) > 1:
            fontes = ", ".join(fim.fonte_id for fim in fins)
            resultado.pendencias.append(
                f"Custódia em {inicio.data:%d/%m/%Y} ({inicio.fonte_id}) com solturas concorrentes ({fontes}). "
                "Confirme o intervalo."
            )
        fim = fins[0] if len(fins) == 1 else None
        resultado.intervalos.append(IntervaloCustodia(
            inicio=inicio.data, fim=fim.data if fim else None,
            fonte_inicio=inicio.fonte_id, fonte_fim=fim.fonte_id if fim else "",
        ))
    return resultado


def extrair_intervalos_custodia(pecas: Iterable[PecaCustodia]) -> tuple[list[IntervaloCustodia], list[str]]:
    resultado = analisar_custodia(pecas)
    return resultado.intervalos, resultado.pendencias
