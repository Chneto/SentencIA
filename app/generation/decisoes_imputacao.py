"""Vínculo entre o formulário de decisão e as imputações extraídas dos autos."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.core.errors import DadosInsuficientesError, DecisaoJudicialPendenteError
from app.ingestion.laya_classifier import MetadadosProcesso


@dataclass(frozen=True)
class AlvoImputacao:
    codigo: str
    id_imputacao: str = ""
    referencia: str = ""
    data_fato: str = ""
    data_recebimento: str = ""
    recebimento_individual: bool = False


def alvos_imputacao(meta: MetadadosProcesso) -> dict[str, AlvoImputacao]:
    codigos = list(dict.fromkeys(c for c in meta.crimes_imputados if c))
    itens = [
        i for i in meta.imputacoes
        if i.estado == "extraida" and i.codigo in codigos
        and not any(e.tipo in ("extincao", "rejeicao") for e in i.eventos)
    ]
    por_codigo = Counter(i.codigo for i in itens)
    atos_identificados = any(e.tipo == "recebimento" for i in itens for e in i.eventos) or any(
        e.tipo == "recebimento" for e in meta.eventos_imputacao_pendentes
    )
    alvos = {}
    for codigo in codigos:
        correspondentes = [i for i in itens if i.codigo == codigo]
        if not correspondentes:
            alvos[codigo] = AlvoImputacao(codigo)
            continue
        for ordem, item in enumerate(correspondentes, 1):
            if item.numero_fato:
                referencia = f"Fato {item.numero_fato} da peça {item.fonte_id}"
            elif item.data_fato:
                partes = item.data_fato.split("-")
                referencia = f"Fato de {'/'.join(reversed(partes))} da peça {item.fonte_id}"
            else:
                referencia = f"Imputação {ordem} da peça {item.fonte_id}"
            datas = {e.data_decisao for e in item.eventos if e.tipo == "recebimento" and e.data_decisao}
            alvos[item.id_imputacao] = AlvoImputacao(
                codigo=codigo, id_imputacao=item.id_imputacao, referencia=referencia,
                data_fato=item.data_fato, data_recebimento=next(iter(datas)) if len(datas) == 1 else "",
                recebimento_individual=atos_identificados or por_codigo[codigo] > 1,
            )
    return alvos


def ha_recebimento_por_imputacao(meta: MetadadosProcesso) -> bool:
    alvos = alvos_imputacao(meta)
    return bool(alvos) and all(a.recebimento_individual and a.data_recebimento for a in alvos.values())


def datas_dos_fatos(meta: MetadadosProcesso) -> list[str]:
    return list(dict.fromkeys(a.data_fato for a in alvos_imputacao(meta).values() if a.data_fato))


def resolver_configs_imputacao(
    meta: MetadadosProcesso, itens: list[dict[str, Any]],
) -> tuple[dict[str, AlvoImputacao], dict[str, dict[str, Any]], set[str]]:
    alvos = alvos_imputacao(meta)
    por_codigo: dict[str, list[str]] = {}
    for chave, alvo in alvos.items():
        por_codigo.setdefault(alvo.codigo, []).append(chave)
    repetidos = {codigo for codigo, chaves in por_codigo.items() if len(chaves) > 1}
    configs = {}
    pendentes: list[str] = []
    encerrados = {
        i.codigo for i in meta.imputacoes if i.estado == "extraida"
        and any(e.tipo in ("extincao", "rejeicao") for e in i.eventos)
    } - set(por_codigo)
    for item in itens:
        codigo = str(item.get("codigo") or item.get("crime_code") or "").strip()
        identidade = str(item.get("id_imputacao") or "").strip()
        if identidade:
            alvo = alvos.get(identidade)
            if alvo is None or not alvo.id_imputacao or (codigo and codigo != alvo.codigo):
                raise DadosInsuficientesError(
                    [f"crimes_config.{identidade}.id_imputacao"],
                    "A imputação indicada não pertence aos autos ativos ou a capitulação informada não corresponde ao item.",
                )
            chave = identidade
        elif codigo:
            chaves = por_codigo.get(codigo, [])
            if codigo in repetidos:
                pendentes.extend(chaves)
                continue
            if codigo in encerrados:
                raise DadosInsuficientesError(
                    [f"crimes_config.{codigo}.id_imputacao"],
                    "A imputação já foi rejeitada ou teve a punibilidade extinta no ato documentado.",
                )
            chave = chaves[0] if chaves else codigo
            alvos.setdefault(chave, AlvoImputacao(codigo))
        else:
            continue
        if chave in configs:
            pendentes.append(chave)
        else:
            configs[chave] = item
    if pendentes:
        raise DecisaoJudicialPendenteError(
            list(dict.fromkeys(pendentes)),
            "Há decisões antigas ou repetidas para o mesmo tipo penal. Confirme separadamente o veredicto e os parâmetros de cada imputação.",
        )
    return alvos, configs, repetidos


def serializar_decisoes_imputacao(decisoes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chaves = (
        "crime_code", "crime_efetivo", "veredicto", "fundamento_legal",
        "desclassificado_de", "inciso", "id_imputacao", "referencia_imputacao", "analise_acao_penal",
    )
    return [
        {k: d[k] for k in chaves if k in d} | {"presumido": bool(d.get("presumido"))}
        for d in decisoes
    ]
