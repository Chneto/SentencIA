"""Comandos do dispositivo derivados da dosimetria e das decisões do juízo."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, model_validator

from app.core.errors import DadosInsuficientesError
from app.core.text_utils import normalizar
from app.generation.juizo_sobre_provas import JuizoSobreProvas, Texto

from app.legal_engine.dosimetria_calculator import (
    DosimetriaGlobalOutput,
    PenaAnosMesesDias,
    formatar_especie_pena,
)
from app.legal_engine.penal_types_catalog import get_penal_type_for_date


@dataclass(frozen=True)
class DecisaoAcessoria:
    fundamento: str
    comando: str
    estado: Literal["decidida", "pendente", "prejudicada"]


def decidir_registro_medidas_protetivas(
    revogacao_documentada: str, data_sentenca: date,
) -> DecisaoAcessoria:
    # Lei 14.550/2023, vigente desde 20/04/2023, e Tema 1249/STJ.
    # https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2023/lei/l14550.htm
    # https://processo.stj.jus.br/repetitivos/temas_repetitivos/pesquisa.jsp?novaConsulta=true&num_processo_classe=2070857&sg_classe=REsp&tipo_pesquisa=T
    if revogacao_documentada:
        return DecisaoAcessoria(
            fundamento=(
                "Consta decisão anterior de revogação das medidas protetivas de urgência "
                f"({revogacao_documentada})."
            ),
            comando=(
                "Registre-se a revogação das medidas protetivas de urgência "
                f"já decidida nos autos ({revogacao_documentada})."
            ),
            estado="decidida",
        )
    fundamento = "Cabe verificar a situação das medidas protetivas de urgência nos autos."
    if data_sentenca >= date(2023, 4, 20):
        fundamento = (
            "As medidas protetivas de urgência têm autonomia em relação ao desfecho penal "
            "e sua duração depende da persistência da situação de risco "
            "(art. 19, §§ 5º e 6º, da Lei nº 11.340/2006)."
        )
    return DecisaoAcessoria(
        fundamento=fundamento,
        comando="[COMPLETAR PELO JUÍZO: situação das medidas protetivas e eventual decisão fundamentada].",
        estado="pendente",
    )


class DecisaoBeneficio(BaseModel):
    """Decisão explícita sobre a pena substitutiva ou a suspensão condicional."""

    model_config = ConfigDict(extra="forbid")
    decisao: Literal["conceder", "negar"]
    fundamento: Texto
    modalidades: list[Texto] = Field(default_factory=list)
    especificacao: Texto | None = None
    prazo_anos: int | None = Field(default=None, strict=True, ge=2, le=4)

    @model_validator(mode="after")
    def validar_detalhes(self) -> DecisaoBeneficio:
        if self.decisao == "negar" and (self.modalidades or self.especificacao or self.prazo_anos is not None):
            raise ValueError("Uma decisão negativa não fixa modalidades, condições ou prazo.")
        return self


class DecisaoMedidasProtetivas(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decisao: Literal["manter", "revogar", "sem_medidas"]
    fundamento: Texto
    fontes: list[Texto] = Field(min_length=1)
    contraditorio_confirmado: StrictBool = False

    @model_validator(mode="after")
    def validar_revogacao(self) -> DecisaoMedidasProtetivas:
        if self.decisao == "revogar" and not self.contraditorio_confirmado:
            raise ValueError("Confirme a prévia oitiva da ofendida e do requerido.")
        return self


MODALIDADES_SUBSTITUICAO = {
    "prestacao_servicos": "prestação de serviços à comunidade ou a entidades públicas",
    "interdicao_direitos": "interdição temporária de direitos",
    "limitacao_fim_semana": "limitação de fim de semana",
    "perda_bens": "perda de bens e valores",
}


class RegistroAcessorio(BaseModel):
    model_config = ConfigDict(extra="forbid")
    substituicao: DecisaoBeneficio | None = None
    sursis: DecisaoBeneficio | None = None
    medidas_protetivas: DecisaoMedidasProtetivas | None = None

    @model_validator(mode="after")
    def validar_beneficios(self) -> RegistroAcessorio:
        subst = self.substituicao
        sursis = self.sursis
        if subst and subst.decisao == "conceder":
            if not subst.modalidades or subst.prazo_anos is not None:
                raise ValueError("Informe as modalidades da substituição da pena.")
            if any(codigo not in MODALIDADES_SUBSTITUICAO for codigo in subst.modalidades):
                raise ValueError("Use modalidades de substituição admitidas no procedimento.")
            if len(set(subst.modalidades)) != len(subst.modalidades):
                raise ValueError("Informe cada modalidade de substituição uma única vez.")
            if {"interdicao_direitos", "perda_bens"}.intersection(subst.modalidades) and not subst.especificacao:
                raise ValueError("Identifique o direito a interditar ou os bens e valores atingidos.")
        if sursis and sursis.decisao == "conceder":
            if not sursis.modalidades or sursis.prazo_anos is None:
                raise ValueError("Informe as condições e o prazo do sursis.")
            if sursis.especificacao:
                raise ValueError("Informe as condições no campo próprio do sursis.")
        if sursis and sursis.decisao == "conceder" and (not subst or subst.decisao != "negar"):
            raise ValueError("Decida a substituição antes de conceder o sursis (art. 77, III, do CP).")
        return self


@dataclass(frozen=True)
class DecisoesAcessorias:
    substituicao: DecisaoAcessoria
    sursis: DecisaoAcessoria
    cautelar: DecisaoAcessoria
    medidas_protetivas: DecisaoAcessoria
    efeitos: DecisaoAcessoria
    intimacoes: DecisaoAcessoria

    def dados_auditoria(self) -> dict[str, Any]:
        return asdict(self)


_ROTULOS_ACESSORIAS = {
    "substituicao": "substituição da pena",
    "sursis": "suspensão condicional da pena",
    "cautelar": "situação cautelar",
    "medidas_protetivas": "medidas protetivas de urgência",
    "efeitos": "efeitos da sentença",
    "intimacoes": "intimações",
}


_OUTRO_COMANDO = {
    "substituicao": r"\b(?:concedo|defiro|indefiro|nego|determino|deixo de conceder) (?:a )?substituicao\b",
    "sursis": r"\b(?:concedo|defiro|indefiro|nego|deixo de conceder) (?:a |o )?(?:sursis|suspensao condicional)\b",
    "cautelar": r"\b(?:(?:concedo|nego) (?:ao reu )?o direito de recorrer|(?:mantenho|decreto|revogo) a prisao)\b",
    "medidas_protetivas": r"\b(?:(?:mantenho|revogo|defiro|indefiro) (?:as )?medidas protetivas|(?:mantenham-se|revoguem-se) as medidas protetivas)\b",
}


def verificar_decisoes_acessorias(
    fundamentacao: str, dispositivo: str, registro: dict[str, Any],
) -> list[str]:
    """Confere a presença de cada fundamento e comando registrado, tolerando formatação."""
    secoes = {"fundamento": normalizar(fundamentacao), "comando": normalizar(dispositivo)}
    divergencias = []
    for chave, rotulo in _ROTULOS_ACESSORIAS.items():
        decisao = registro.get(chave)
        if not isinstance(decisao, dict):
            continue
        for campo, texto in secoes.items():
            esperado = decisao.get(campo)
            if chave in ("substituicao", "sursis") and not decisao.get("comando"):
                continue
            if not isinstance(esperado, str) or not esperado:
                continue
            trecho = normalizar(esperado)
            if trecho not in texto:
                divergencias.append(f"Decisão de {rotulo}: {campo} registrado ausente ou alterado na minuta.")
            elif campo == "comando" and chave in _OUTRO_COMANDO:
                restante = texto.replace(trecho, "", 1)
                if re.search(_OUTRO_COMANDO[chave], restante):
                    divergencias.append(f"Decisão de {rotulo}: comando adicional incompatível com o registro.")
    return divergencias


def _beneficio(
    entrada: DecisaoBeneficio | None, nome: str, base: str, fundamento_calculo: str,
) -> DecisaoAcessoria:
    if entrada is None:
        return DecisaoAcessoria(
            fundamento_calculo,
            f"[COMPLETAR PELO JUÍZO: decisão fundamentada sobre {nome} ({base})].",
            "pendente",
        )
    if entrada.decisao == "negar":
        return DecisaoAcessoria(entrada.fundamento, f"DEIXO DE CONCEDER a {nome} ({base}).", "decidida")
    if entrada.prazo_anos is None:
        detalhes = ", ".join(MODALIDADES_SUBSTITUICAO[valor] for valor in entrada.modalidades)
        if entrada.especificacao:
            detalhes += f" ({entrada.especificacao})"
    else:
        condicoes = "; ".join(condicao.rstrip(".;") for condicao in entrada.modalidades)
        detalhes = f"pelo prazo de {entrada.prazo_anos} anos, cumpridas estas condições: {condicoes}"
    return DecisaoAcessoria(
        entrada.fundamento, f"CONCEDO a {nome} ({base}): {detalhes}.", "decidida",
    )


def resolver_acessorias(
    dosimetria: DosimetriaGlobalOutput, juizo: JuizoSobreProvas, *,
    dados: Any, data_sentenca: date, fontes_ativas: set[str], revogacao_documentada: str,
    absolveu: bool, nome_ofendida: str, destino_execucao: tuple[str, str],
) -> DecisoesAcessorias:
    try:
        registro = RegistroAcessorio.model_validate(dados if dados is not None else {})
    except ValidationError as exc:
        raise DadosInsuficientesError(
            ["decisoes_acessorias"], "Confira os fundamentos, modalidades, prazos e fontes das decisões acessórias.",
        ) from exc
    sem_condenacao = dosimetria.sem_condenacao or not dosimetria.crimes_calculados
    if sem_condenacao and (registro.substituicao or registro.sursis):
        raise DadosInsuficientesError(["decisoes_acessorias"], "Os benefícios da pena pressupõem condenação.")
    if registro.substituicao and registro.substituicao.decisao == "conceder":
        dias = dosimetria.pena_total_unificada.to_total_days()
        modalidades_exigidas = 1 if dias <= 360 else 2
        nao_cabe_servico = dias <= 180 and "prestacao_servicos" in registro.substituicao.modalidades
        if (
            not dosimetria.substituicao_art44_cabivel or nao_cabe_servico
            or len(registro.substituicao.modalidades) != modalidades_exigidas
        ):
            raise DadosInsuficientesError(
                ["decisoes_acessorias.substituicao"],
                "Confira o cabimento e o número de restritivas exigido pelo art. 44 do Código Penal.",
            )
    if registro.sursis and registro.sursis.decisao == "conceder" and not dosimetria.sursis_art77_cabivel:
        raise DadosInsuficientesError(
            ["decisoes_acessorias.sursis"], "Confira o cabimento do sursis do art. 77, caput, do Código Penal.",
        )

    if sem_condenacao:
        substituicao = sursis = DecisaoAcessoria(
            "A ausência de condenação prejudica a decisão sobre os benefícios da pena.", "", "prejudicada",
        )
    else:
        motivo_subst = dosimetria.motivo_vedacao_substituicao.partition("[COMPLETAR")[0].strip()
        substituicao = _beneficio(registro.substituicao, "substituição da pena", "art. 44 do CP", motivo_subst)
        if registro.sursis is None and registro.substituicao and registro.substituicao.decisao == "conceder":
            sursis = DecisaoAcessoria(
                "A substituição concedida prejudica a suspensão condicional da pena (art. 77, III, do CP).",
                "DECLARO prejudicado o sursis (art. 77, III, do CP).",
                "prejudicada",
            )
        else:
            # O parecer de cabimento do calculador não é a decisão sobre concessão ou condições.
            parecer_sursis = (
                "O cálculo aponta cabimento em tese da suspensão condicional da pena (art. 77 do CP)."
                if dosimetria.sursis_art77_cabivel else
                "O cálculo indica impedimento ou condição pendente à concessão do sursis (art. 77 do CP)."
            )
            sursis = _beneficio(registro.sursis, "suspensão condicional da pena", "art. 77 do CP", parecer_sursis)

    if sem_condenacao and juizo.cautelar and juizo.cautelar.decisao != "conceder":
        raise DadosInsuficientesError(
            ["decisao_cautelar"], "O comando cautelar condenatório é incompatível com a ausência de condenação.",
        )
    if sem_condenacao and absolveu:
        cautelar = DecisaoAcessoria(
            "Na absolvição, cessam as medidas cautelares penais vinculadas às imputações absolvidas (art. 386, parágrafo único, I e II, do CPP).",
            "Revoguem-se as cautelares penais relativas às imputações absolvidas. Ponha-se o réu em liberdade se estiver preso somente por este processo.",
            "decidida",
        )
    elif juizo.cautelar is None:
        titulo = "cautelares penais remanescentes" if sem_condenacao else "situação prisional do réu"
        base = "Após a extinção da punibilidade, cabe verificar as cautelares vinculadas aos autos." if sem_condenacao else (
            "A cautelar deve ser decidida com fundamento próprio (art. 387, § 1º, do CPP)."
        )
        cautelar = DecisaoAcessoria(base, f"[COMPLETAR PELO JUÍZO: {titulo}].", "pendente")
    else:
        fundamento = juizo.cautelar.fundamento
        comando = juizo.texto_cautelar(incluir_trechos=False)
        if sem_condenacao:
            comando = "Revoguem-se as cautelares penais vinculadas a este processo. Ponha-se o réu em liberdade se por outro motivo não estiver preso."
        comando += f" Fundamento do juízo: {juizo.cautelar.fundamento}"
        if juizo.cautelar.referencias:
            fontes = ", ".join(dict.fromkeys(ref.fonte_id for ref in juizo.cautelar.referencias))
            fundamento += f" (Fontes: {fontes})."
            comando += f" Fontes da decisão: {fontes}."
        cautelar = DecisaoAcessoria(fundamento, comando, "decidida")

    medidas = decidir_registro_medidas_protetivas(revogacao_documentada, data_sentenca)
    if registro.medidas_protetivas:
        entrada = registro.medidas_protetivas
        if any(fonte not in fontes_ativas for fonte in entrada.fontes):
            raise DadosInsuficientesError(
                ["decisoes_acessorias.medidas_protetivas.fontes"], "Indique peças dos autos ativos.",
            )
        comando = {
            "manter": "MANTENHO as medidas protetivas de urgência identificadas nesta fundamentação.",
            "revogar": "REVOGO as medidas protetivas de urgência identificadas nesta fundamentação.",
            "sem_medidas": "REGISTRO a inexistência de medidas protetivas de urgência vigentes nestes autos.",
        }[entrada.decisao]
        medidas = DecisaoAcessoria(
            entrada.fundamento + f" (Fontes: {', '.join(dict.fromkeys(entrada.fontes))}).",
            comando,
            "decidida",
        )

    comarca, fonte_endereco = destino_execucao
    destino = f", com remessa ao Juízo da Execução Penal da Comarca de {comarca} ({fonte_endereco})" if comarca and fonte_endereco else ""
    efeitos = DecisaoAcessoria(
        "Sem condenação, ficam prejudicados os efeitos próprios da sentença penal condenatória.",
        "Após o trânsito em julgado, dê-se baixa na distribuição e arquivem-se os autos.",
        "prejudicada",
    ) if sem_condenacao else DecisaoAcessoria(
        "A condenação torna certa a obrigação de indenizar o dano causado pelo crime (art. 91, I, do CP). "
        "O trânsito em julgado enseja a comunicação eleitoral (art. 15, III, da Constituição Federal) e as providências de execução penal.",
        "Após o trânsito em julgado, comunique-se à Justiça Eleitoral, oficie-se aos órgãos de identificação para os registros pertinentes "
        f"e expeça-se a guia de execução definitiva{destino}.",
        "decidida",
    )
    vitima = f" {nome_ofendida}" if nome_ofendida else ""
    intimacoes = DecisaoAcessoria(
        "A ofendida deve ser comunicada da sentença (art. 201, § 2º, do CPP) e dos atos pertinentes ao agressor (art. 21 da Lei nº 11.340/2006). A intimação do acusado segue o art. 392 do CPP.",
        "Intimem-se o Ministério Público, a Defesa e o acusado, observados os meios legais. "
        f"Intime-se a ofendida{vitima} do teor desta sentença, nos termos do art. 21 da Lei nº 11.340/2006 e do art. 201, § 2º, do CPP.",
        "decidida",
    )
    return DecisoesAcessorias(substituicao, sursis, cautelar, medidas, efeitos, intimacoes)


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
