"""Comandos do dispositivo derivados da dosimetria e das decisões do juízo."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.legal_engine.dosimetria_calculator import (
    DosimetriaGlobalOutput,
    PenaAnosMesesDias,
    formatar_especie_pena,
)
from app.legal_engine.penal_types_catalog import get_penal_type_for_date


@dataclass(frozen=True)
class ComandoImputacao:
    codigo: str
    nome: str
    artigo: str
    veredicto: str
    fundamento: str = ""
    artigo_original: str = ""
    id_imputacao: str = ""
    referencia_imputacao: str = ""

    @property
    def rotulo(self) -> str:
        referencia = f", quanto ao {self.referencia_imputacao}" if self.referencia_imputacao else ""
        return f"{self.nome} ({self.artigo}){referencia}"

    def texto(self, nome_reu: str) -> str:
        if self.veredicto == "absolver":
            return f"ABSOLVER o réu {nome_reu} quanto ao delito de {self.rotulo}, com fundamento no {self.fundamento}"
        if self.veredicto == "extinguir":
            return f"EXTINGUIR A PUNIBILIDADE do acusado quanto ao delito de {self.rotulo}, com fundamento no {self.fundamento}"
        if self.veredicto not in ("condenar", "desclassificar"):
            return f"[COMPLETAR PELO JUÍZO: veredicto da imputação {self.rotulo}]"
        condenacao = f"CONDENAR o réu {nome_reu} pelo delito de {self.rotulo}"
        if self.veredicto == "desclassificar" and self.artigo_original:
            referencia = f", quanto ao {self.referencia_imputacao}" if self.referencia_imputacao else ""
            return f"DESCLASSIFICAR a imputação de {self.artigo_original}{referencia}, com fundamento no {self.fundamento}, e {condenacao}"
        return condenacao


@dataclass(frozen=True)
class ComandoMulta:
    dias: int
    valor_unitario: str
    imputacao: str


@dataclass(frozen=True)
class ComandosDispositivo:
    imputacoes: tuple[ComandoImputacao, ...]
    penas_por_especie: dict[str, PenaAnosMesesDias]
    pena_total_unificada: PenaAnosMesesDias | None
    regime_inicial: str
    regime_por_especie: dict[str, str]
    total_dias_multa: int
    reparacao: Decimal | None
    reparacao_pendente: bool
    sem_condenacao: bool

    multas: tuple[ComandoMulta, ...] = ()
    multa_pendente: bool = False

    def texto_penas(self) -> str:
        return " e ".join(
            f"{pena.format_extenso()} de {formatar_especie_pena(especie)}"
            for especie, pena in self.penas_por_especie.items()
        )

    def texto_soma_penas(self) -> str:
        if len(self.penas_por_especie) < 2 or self.pena_total_unificada is None:
            return ""
        return f" A soma das penas privativas de liberdade é de {self.pena_total_unificada.format_extenso()}."


def construir_comandos_dispositivo(
    dosimetria: DosimetriaGlobalOutput | dict[str, Any],
) -> ComandosDispositivo:
    dados = dosimetria.model_dump(mode="json") if isinstance(dosimetria, DosimetriaGlobalOutput) else dosimetria
    imputacoes = []
    sem_condenacao = bool(dados.get("sem_condenacao"))
    condenacoes = () if sem_condenacao else (dados.get("crimes_calculados") or ())
    for crime in condenacoes:
        spec = crime.get("crime_spec") or {}
        data = crime.get("data_fato")
        original = crime.get("desclassificado_de")
        artigo_original = ""
        if original and data:
            dia = data if isinstance(data, date) else date.fromisoformat(data)
            artigo_original = get_penal_type_for_date(original, dia).artigo_formatado
        imputacoes.append(ComandoImputacao(
            codigo=original or spec.get("code", ""),
            nome=spec.get("nome", ""),
            artigo=spec.get("artigo_formatado", ""),
            veredicto="desclassificar" if original else "condenar",
            fundamento="art. 383 do CPP" if original else spec.get("artigo_formatado", ""),
            artigo_original=artigo_original,
            id_imputacao=crime.get("id_imputacao", ""),
            referencia_imputacao=crime.get("referencia_imputacao", ""),
        ))
    for crime in dados.get("crimes_nao_condenados") or []:
        imputacoes.append(ComandoImputacao(
            codigo=crime.get("crime_code", ""),
            nome=crime.get("crime_nome", ""),
            artigo=crime.get("artigo_formatado", ""),
            veredicto=crime.get("veredicto", ""),
            fundamento=crime.get("fundamento_legal", ""),
            id_imputacao=crime.get("id_imputacao", ""),
            referencia_imputacao=crime.get("referencia_imputacao", ""),
        ))

    total_bruto = dados.get("pena_total_unificada")
    total = PenaAnosMesesDias.model_validate(total_bruto) if total_bruto is not None else None
    penas = {
        especie: pena
        for especie, valor in (dados.get("penas_por_especie") or {}).items()
        if (pena := PenaAnosMesesDias.model_validate(valor)).to_total_days() > 0
    }
    if total and total.to_total_days() > 0 and not penas and not sem_condenacao:
        especies = {c.get("crime_spec", {}).get("especie_pena") for c in dados.get("crimes_calculados") or []}
        if len(especies) == 1 and None not in especies:
            penas[especies.pop()] = total
    regime = dados.get("regime_inicial") or ""
    regimes = dados.get("regime_por_especie") or {}
    valor = dados.get("indenizacao_dano_moral_tema983")
    reparacao = Decimal(f"{float(valor):.2f}") if valor is not None else None
    if sem_condenacao:
        reparacao = Decimal("0")
    total_multa = int(dados.get("total_dias_multa") or 0)
    multas = tuple(
        ComandoMulta(int(c["dias_multa"]), c["valor_unitario_multa"], imputacao.rotulo)
        for c, imputacao in zip(condenacoes, imputacoes)
        if c.get("dias_multa") and c.get("valor_unitario_multa")
    )
    multa_pendente = any(
        c.get("multa_pendente") or (c.get("dias_multa") and not c.get("valor_unitario_multa"))
        for c in condenacoes
    ) or bool(total_multa and not multas)
    if dados.get("tipo_concurso") == "continuado" and multas:
        maior = max(m.dias for m in multas)
        maiores = tuple(m for m in multas if m.dias == maior)
        if len({m.valor_unitario for m in maiores}) > 1:
            multa_pendente = True
        multas = (ComandoMulta(total_multa, maiores[0].valor_unitario, ""),) if not multa_pendente else ()
    return ComandosDispositivo(
        imputacoes=tuple(imputacoes),
        penas_por_especie={} if sem_condenacao else penas,
        pena_total_unificada=None if sem_condenacao else total,
        regime_inicial="" if sem_condenacao else regime,
        regime_por_especie={} if sem_condenacao else {e: regimes.get(e, regime) for e in penas if regimes.get(e, regime)},
        total_dias_multa=0 if sem_condenacao or multa_pendente else total_multa,
        reparacao=reparacao,
        reparacao_pendente=not sem_condenacao and bool(dados.get("dano_moral_arbitramento_pendente")),
        sem_condenacao=sem_condenacao,
        multas=multas,
        multa_pendente=not sem_condenacao and multa_pendente,
    )
