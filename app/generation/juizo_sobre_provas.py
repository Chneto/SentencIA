"""Registro explícito da valoração probatória e da decisão cautelar pelo juízo."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.core.case_context import CaseContext
from app.core.errors import DadosInsuficientesError
from app.generation.decisoes_imputacao import resolver_configs_imputacao
from app.ingestion.laya_classifier import MetadadosProcesso

Texto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PENDENCIA = "[COMPLETAR PELO JUÍZO: {}]"
ELEMENTOS_COMUNS = {"materialidade": "Materialidade", "autoria": "Autoria"}
ELEMENTOS_ESPECIFICOS = {
    "art_129_p9_cp": {"lesao_corporal": "Lesão corporal"},
    "art_129_p13_cp": {"lesao_corporal": "Lesão corporal"},
    "art_147_cp": {"promessa_mal_grave": "Promessa de mal injusto e grave"},
    "art_147a_cp": {"reiteracao": "Reiteração da conduta"},
    "art_150_cp": {"ingresso_permanencia_indevida": "Ingresso ou permanência indevida"},
    "art_150_p1_cp": {"ingresso_permanencia_indevida": "Ingresso ou permanência indevida"},
    "art_24a_lmp": {"ordem_protetiva": "Decisão protetiva", "ciencia_previa": "Ciência prévia", "descumprimento": "Conduta de descumprimento"},
    "art_338a_cp": {"ordem_protetiva": "Decisão protetiva", "ciencia_previa": "Ciência prévia", "descumprimento": "Conduta de descumprimento"},
}
CONCLUSOES = {
    "provado": "Reconhecido como provado",
    "nao_provado": "Reconhecido como não provado",
    "indeterminado": "Valoração pendente",
}
COMANDOS_CAUTELARES = {
    "conceder": "CONCEDO ao réu o direito de recorrer em liberdade",
    "manter_prisao": "MANTENHO a prisão preventiva",
    "impor_prisao": "DECRETO a prisão preventiva",
    "outra_medida": "DETERMINO a medida cautelar registrada pelo juízo",
}


class ReferenciaProva(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fonte_id: Texto
    trecho: Texto


class ElementoProvado(BaseModel):
    model_config = ConfigDict(extra="forbid")
    elemento: Texto
    conclusao: Literal["provado", "nao_provado", "indeterminado"]
    fundamento: Texto
    provas_favoraveis: list[ReferenciaProva] = Field(default_factory=list)
    provas_contrarias: list[ReferenciaProva] = Field(default_factory=list)


class DecisaoCautelarSentenca(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decisao: Literal["conceder", "manter_prisao", "impor_prisao", "outra_medida"]
    fundamento: Texto
    referencias: list[ReferenciaProva] = Field(default_factory=list)


def elementos_por_tipo(codigo: str) -> dict[str, str]:
    return {**ELEMENTOS_COMUNS, **ELEMENTOS_ESPECIFICOS.get(codigo, {})}


def _comparavel(texto: str) -> str:
    return " ".join(texto.split())


def _indice_provas(ctx: CaseContext) -> dict[str, list[str]]:
    fontes: dict[str, list[str]] = {}
    for peca in ctx.pecas:
        fontes.setdefault(peca.doc_id, []).append(_comparavel(peca.conteudo))
    for transcricao in ctx.transcriptions:
        fonte = transcricao.get("media_id")
        if not fonte:
            continue
        for segmento in transcricao.get("segmentos") or []:
            if isinstance(segmento, dict):
                texto = segmento.get("texto_literal") or segmento.get("texto")
                if isinstance(texto, str) and texto.strip():
                    fontes.setdefault(fonte, []).append(_comparavel(texto))
    return fontes


def _verificar_provas(referencias: list[ReferenciaProva], fontes: dict[str, list[str]], campo: str) -> None:
    for ref in referencias:
        if not any(_comparavel(ref.trecho) in texto for texto in fontes.get(ref.fonte_id, [])):
            raise DadosInsuficientesError([campo], "Indique um trecho literal de uma fonte do caso ativo.")


def _texto_referencias(referencias: list[ReferenciaProva]) -> str:
    return " | ".join(f'{ref.fonte_id}: “{_comparavel(ref.trecho)}”' for ref in referencias)


@dataclass
class JuizoSobreProvas:
    por_imputacao: dict[str, dict[str, ElementoProvado]] = field(default_factory=dict)
    cautelar: DecisaoCautelarSentenca | None = None

    def merito(self, decisao: dict[str, Any], *, incluir_trechos: bool = True) -> str:
        chave = decisao.get("id_imputacao") or decisao.get("crime_code") or ""
        codigo = decisao.get("crime_efetivo") or decisao.get("crime_code") or ""
        registros = self.por_imputacao.get(chave, {})
        esperados = elementos_por_tipo(codigo)
        linhas = []
        for elemento, rotulo in esperados.items():
            registro = registros.get(elemento)
            if registro is None:
                linhas.append(PENDENCIA.format(f"{rotulo.lower()} da imputação"))
                continue
            partes = [f"{rotulo}. {CONCLUSOES[registro.conclusao]}."]
            if incluir_trechos:
                partes.append(f"Fundamentação do juízo: {registro.fundamento}")
            for titulo, referencias in (
                ("Prova favorável", registro.provas_favoraveis),
                ("Prova contrária", registro.provas_contrarias),
            ):
                if referencias:
                    descricao = _texto_referencias(referencias) if incluir_trechos else ", ".join(
                        dict.fromkeys(ref.fonte_id for ref in referencias)
                    )
                    partes.append(f"{titulo}: {descricao}.")
            if registro.conclusao == "indeterminado":
                partes.append(PENDENCIA.format(f"conclusão sobre {rotulo.lower()}"))
            linhas.append(" ".join(partes))
        return "\n\n".join(linhas)

    def texto_cautelar(self, *, incluir_trechos: bool = True) -> str:
        if self.cautelar is None:
            return "Decisão judicial sobre o direito de recorrer em liberdade (art. 387, § 1º, do CPP): " + PENDENCIA.format(
                "situação prisional do réu"
            ) + "."
        comando = f"{COMANDOS_CAUTELARES[self.cautelar.decisao]} (art. 387, § 1º, do CPP)."
        if not incluir_trechos:
            return comando
        partes = [comando, f"Fundamentação do juízo: {self.cautelar.fundamento}"]
        if self.cautelar.referencias:
            partes.append(f"Referência: {_texto_referencias(self.cautelar.referencias)}.")
        return " ".join(partes)


def resolver_juizo_sobre_provas(
    ctx: CaseContext, meta: MetadadosProcesso, override: dict[str, Any], decisoes: list[dict[str, Any]]
) -> JuizoSobreProvas:
    """Valida a origem literal das referências e o alvo individual de cada registro."""
    resultado = JuizoSobreProvas()
    itens = [item for item in (override.get("crimes_config") or []) if isinstance(item, dict)]
    if not any("elementos_provados" in item for item in itens) and override.get("decisao_cautelar") is None:
        return resultado
    fontes = _indice_provas(ctx)

    cautelar = override.get("decisao_cautelar")
    if cautelar is not None:
        try:
            resultado.cautelar = DecisaoCautelarSentenca.model_validate(cautelar)
        except ValidationError as exc:
            raise DadosInsuficientesError(["decisao_cautelar"], "Informe uma decisão cautelar e seu fundamento.") from exc
        _verificar_provas(resultado.cautelar.referencias, fontes, "decisao_cautelar.referencias")

    _, configs, _ = resolver_configs_imputacao(meta, itens)
    for decisao in decisoes:
        chave = decisao.get("id_imputacao") or decisao.get("crime_code")
        registros = configs.get(chave, {}).get("elementos_provados")
        if registros is None:
            continue
        campo = f"crimes_config.{chave}.elementos_provados"
        if not isinstance(registros, list):
            raise DadosInsuficientesError([campo], "Informe os elementos provados em uma lista.")
        por_elemento: dict[str, ElementoProvado] = {}
        esperados = elementos_por_tipo(decisao.get("crime_efetivo") or decisao["crime_code"])
        for entrada in registros:
            try:
                registro = ElementoProvado.model_validate(entrada)
            except ValidationError as exc:
                raise DadosInsuficientesError([campo], "Informe o elemento, a conclusão e o seu fundamento.") from exc
            if registro.elemento not in esperados or registro.elemento in por_elemento:
                raise DadosInsuficientesError([campo], "Confira os elementos da imputação e registre cada um uma única vez.")
            if registro.conclusao == "provado" and not registro.provas_favoraveis:
                raise DadosInsuficientesError([campo], "A conclusão de que o elemento foi provado exige referência à prova favorável.")
            _verificar_provas(registro.provas_favoraveis, fontes, campo)
            _verificar_provas(registro.provas_contrarias, fontes, campo)
            por_elemento[registro.elemento] = registro
        resultado.por_imputacao[chave] = por_elemento
    return resultado
