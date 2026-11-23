"""
SentencIA — Pipeline Seccional com In-Flight Gating.

Coordena a geração da sentença em 5 seções (relatório, preliminares, mérito, dosimetria e
dispositivo) com dossiê fático restrito por seção, prompt delimitado (universo fechado), crivo
in-flight com até ``max_retries`` retentativas cirúrgicas e fallback determinístico.

Princípios v2 (docs/v2/PLANO_V2.md):

* As seções determinísticas (e a dosimetria/prescrição) são calculadas UMA vez por geração
  (``calcular_secoes_deterministicas`` → ``ResultadoDeterministico``) e reutilizadas pelos
  dossiês, pelos fallbacks e pela montagem final — nada de recomputar a sentença por seção.
* Classificação/metadados/matriz oral vêm do ``CaseContext`` (cache por conteúdo); o caso demo
  só é reconhecido por ``bundle["is_demo"] is True``.
* Ollama via ``app.core.ollama_client.gerar_texto`` (com ``num_ctx``); o contexto das seções
  anteriores entra RESUMIDO no prompt.
* Telemetria honesta: seções substituídas pelo modelo determinístico ficam em
  ``SentencaGerada.secoes_fallback`` e o rótulo ``motor_utilizado`` diz exatamente o que foi usado.
* Onde faltar dado concreto, o texto usa ``[COMPLETAR PELO JUÍZO: ...]`` — o gating não pressiona
  o modelo a remover esses marcadores (o auditor final os reporta como pendências).
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import httpx  # noqa: F401 — mantido no namespace: integrações/testes aplicam patch em ``sectional_pipeline.httpx.Client``
from pydantic import BaseModel, Field

from app.core.case_context import CaseContext, obter_contexto
from app.core.config import load_settings
from app.core.errors import DadosInsuficientesError
from app.core.ollama_client import gerar_texto, resolver_modelo_ollama
from app.core.text_utils import remover_acentos
from app.demo.demo_sentence import DEMO_FUNDAMENTO_LIBERDADE, DEMO_MEDIA_ALIASES, DEMO_MEDIA_IDS
from app.generation.adversarial_critic import tese_vedada_honra, violacao_vedacao_honra
from app.generation.date_formatter import formatar_data_br, parse_qualquer_data
from app.generation.dispositivo import ComandosDispositivo, construir_comandos_dispositivo
from app.generation.hallucination_auditor import verificar_comandos_dispositivo
from app.generation.sentence_generator import (
    MOTOR_DETERMINISTICO,
    ResultadoDeterministico,
    SentencaGerada,
    _CRIMES_DA_CLASSE,
    _classe_tese,
    _validar_bundle,
    calcular_secoes_deterministicas,
    listar_marcadores_pendentes,
    marcador,
    montar_sentenca_gerada,
    texto_dano_moral_dispositivo,
    texto_multa_dispositivo,
    prescricao_exige_topico,
    texto_prescricao_preliminares,
    texto_regime_dispositivo,
)
from app.legal_engine.dosimetria_calculator import ROTULOS_VETORES_ART_59
from app.legal_engine.penal_types_catalog import get_penal_type_for_date

logger = logging.getLogger(__name__)

#: Alias de compatibilidade (a função compartilhada vive em ``app.core.text_utils``).
_remover_acentos = remover_acentos


# ==============================================================================
# CONTRATOS DE DADOS
# ==============================================================================

class SecaoTipo(str, Enum):
    """Identificador canônico das 5 seções do provimento judicial."""
    RELATORIO = "relatorio"
    PRELIMINARES = "preliminares"
    MERITO = "merito"
    DOSIMETRIA = "dosimetria"
    DISPOSITIVO = "dispositivo"


ORDEM_SECOES: List[SecaoTipo] = [
    SecaoTipo.RELATORIO,
    SecaoTipo.PRELIMINARES,
    SecaoTipo.MERITO,
    SecaoTipo.DOSIMETRIA,
    SecaoTipo.DISPOSITIVO,
]


class StatusSecao(str, Enum):
    """Estados operacionais de uma seção durante o ciclo de vida in-flight."""
    PENDENTE = "pendente"
    GERANDO = "gerando"
    APROVADO = "aprovado"
    REPROVADO_RETRY = "reprovado_retry"
    FALLBACK_ACIONADO = "fallback_acionado"


class DossieRestricaoSecao(BaseModel):
    """Dossiê fático e normativo restrito por seção: o universo fechado daquela seção."""
    secao: SecaoTipo
    delito_especifico: Optional[str] = None
    folhas_permitidas: List[str] = Field(
        default_factory=list,
        description="Lista taxativa de IDs e folhas de peças e mídias autorizadas para citação.",
    )
    fatos_delimitados: List[str] = Field(
        default_factory=list,
        description="Fatos apurados e delimitados a serem abordados na seção.",
    )
    teses_defesa: List[str] = Field(
        default_factory=list,
        description="Teses defensivas que obrigatoriamente devem ser enfrentadas na seção.",
    )
    diretrizes_jurisprudenciais: List[str] = Field(
        default_factory=list,
        description="Precedentes vinculantes, súmulas e diretrizes normativas autorizadas.",
    )
    comandos_dispositivo: ComandosDispositivo | None = None
    parametros_dosimetria: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Memória matemática da dosimetria pré-calculada pelo Python.",
    )


class SecaoRascunho(BaseModel):
    """Minuta redigida para uma seção sob escopo delimitado, submetida ao crivo in-flight."""
    secao: SecaoTipo
    delito_especifico: Optional[str] = None
    texto_redigido: str
    versao_tentativa: int = 1


class ResultadoGating(BaseModel):
    """Decisão do crivo seccional (In-Flight Gating)."""
    secao: SecaoTipo
    aprovado: bool
    score: float = 100.0
    erros: List[str] = Field(default_factory=list)
    feedbacks_cirurgicos: List[str] = Field(default_factory=list)
    acionar_fallback: bool = False


class RegistroTentativaSecao(BaseModel):
    """Registro histórico de telemetria de uma tentativa de redação seccional."""
    secao: SecaoTipo
    tentativa: int
    status: StatusSecao
    resultado_gating: Optional[ResultadoGating] = None
    timestamp: str


class EstadoPipelineSeccional(BaseModel):
    """Estado persistente da máquina de estados do pipeline seccional."""
    secao_atual: Optional[SecaoTipo] = None
    status_secoes: Dict[SecaoTipo, StatusSecao] = Field(default_factory=dict)
    secoes_seladas: Dict[SecaoTipo, str] = Field(default_factory=dict)
    historico_tentativas: Dict[SecaoTipo, List[RegistroTentativaSecao]] = Field(default_factory=dict)


# ==============================================================================
# MOTOR DETERMINÍSTICO (cálculo único por geração)
# ==============================================================================

def calcular_resultado_seccional(
    bundle: Dict[str, Any],
    custom_dosimetria_override: Optional[Dict[str, Any]] = None,
) -> ResultadoDeterministico:
    """Valida o bundle e calcula, UMA vez, as 5 seções determinísticas + dosimetria + prescrição.

    Levanta ``ValueError``/``KeyError`` (bundle inválido), ``DadosInsuficientesError`` (dados
    essenciais ausentes) e ``DecisaoJudicialPendenteError`` (caso real sem veredicto por crime).
    """
    _validar_bundle(bundle)
    return calcular_secoes_deterministicas(bundle, custom_dosimetria_override)


def gerar_fallback_deterministico_secao(
    secao: SecaoTipo,
    bundle: Dict[str, Any],
    custom_dosimetria_override: Optional[Dict[str, Any]] = None,
    *,
    resultado: Optional[ResultadoDeterministico] = None,
) -> str:
    """Texto determinístico de UMA seção. Passe ``resultado`` para reutilizar o cálculo da geração."""
    resultado = resultado or calcular_resultado_seccional(bundle, custom_dosimetria_override)
    return resultado.secao(secao)


def _tentar_resultado(
    bundle: Dict[str, Any],
    custom_dosimetria_override: Optional[Dict[str, Any]],
) -> Optional[ResultadoDeterministico]:
    """Resultado determinístico quando os dados/decisões existem; ``None`` caso contrário (dossiês informativos)."""
    try:
        return calcular_resultado_seccional(bundle, custom_dosimetria_override)
    except DadosInsuficientesError as exc:
        logger.info("Dossiê seccional sem resultado determinístico: %s", exc)
        return None


# ==============================================================================
# DOSSIÊ DE RESTRIÇÃO FÁTICA SECCIONAL
# ==============================================================================

_CATEGORIAS_ITINERARIO = (
    "BO",
    "DENUNCIA",
    "RECEBIMENTO_DENUNCIA",
    "RESPOSTA_ACUSACAO",
    "DECISAO_MPU",
    "CERTIDAO_INTIMACAO_MPU",
    "TERMO_AUDIENCIA",
    "LAUDO_PERICIAL",
    "CAC_FAC",
    "ALEGACOES_FINAIS_MP",
    "ALEGACOES_FINAIS_DEFESA",
)
_CATEGORIAS_PROVA = ("BO", "LAUDO_PERICIAL", "DECISAO_MPU", "CERTIDAO_INTIMACAO_MPU", "TERMO_AUDIENCIA")
_ROTULO_CATEGORIA = {
    "BO": "Boletim de ocorrência / peça inquisitorial",
    "DENUNCIA": "Denúncia",
    "RECEBIMENTO_DENUNCIA": "Recebimento da denúncia / saneamento",
    "RESPOSTA_ACUSACAO": "Resposta à acusação",
    "DECISAO_MPU": "Decisão de medidas protetivas / custódia",
    "CERTIDAO_INTIMACAO_MPU": "Certidão de intimação das medidas protetivas",
    "TERMO_AUDIENCIA": "Termo de audiência",
    "LAUDO_PERICIAL": "Laudo pericial",
    "CAC_FAC": "Certidão de antecedentes criminais",
    "ALEGACOES_FINAIS_MP": "Alegações finais do Ministério Público",
    "ALEGACOES_FINAIS_DEFESA": "Alegações finais da Defesa",
}
_LIMITE_PECAS_DOSSIE = 30


def _ids_permitidos(ctx: CaseContext) -> List[str]:
    """IDs reais das peças + mídias transcritas; aliases canônicos SOMENTE no caso demo (flag explícita)."""
    midias = set(ctx.ids_midias)
    if ctx.is_demo:
        midias.update(DEMO_MEDIA_IDS)
        midias.update(DEMO_MEDIA_ALIASES)
    return sorted({p.doc_id for p in ctx.pecas} | midias)


def _data_ou_marcador(valor: Any, descricao: str) -> str:
    return formatar_data_br(valor) if parse_qualquer_data(valor) else marcador(descricao)


def _ref_peca(p: Any) -> str:
    folha = f", {p.folha}" if getattr(p, "folha", "") else ""
    data = f", {formatar_data_br(p.data_peca)}" if parse_qualquer_data(getattr(p, "data_peca", "")) else ""
    return f"{p.doc_id}{folha}{data}"


def _indice_pecas(ctx: CaseContext, categorias: tuple) -> List[str]:
    linhas: List[str] = []
    for p in ctx.pecas:
        if p.categoria not in categorias or p.doc_id == "ID-CAPA-PJE":
            continue
        rotulo = _ROTULO_CATEGORIA.get(p.categoria, p.categoria)
        titulo = re.sub(r"\s+", " ", p.titulo or "").strip()[:140]
        linhas.append(f"{rotulo}: {_ref_peca(p)} — {titulo}")
        if len(linhas) >= _LIMITE_PECAS_DOSSIE:
            break
    return linhas


def _nomes_crimes(meta: Any, resultado: Optional[ResultadoDeterministico]) -> str:
    if resultado is not None and resultado.decisoes_crimes:
        return "; ".join(
            f"{d['spec_original'].nome} ({d['spec_original'].artigo_formatado})" for d in resultado.decisoes_crimes
        )
    codigos = [c for c in (getattr(meta, "crimes_imputados", None) or []) if c]
    if not codigos:
        return marcador("crimes imputados na denúncia")
    dt = parse_qualquer_data(getattr(meta, "data_fato", "")) or datetime.date.today()
    nomes = []
    for code in codigos:
        try:
            sp = get_penal_type_for_date(code, dt)
            nomes.append(f"{sp.nome} ({sp.artigo_formatado})")
        except Exception:
            nomes.append(str(code))
    return "; ".join(nomes)


def _rotulo_veredicto(dec: Dict[str, Any]) -> str:
    verd = dec.get("veredicto", "")
    rotulo = {
        "condenar": "CONDENAÇÃO",
        "absolver": "ABSOLVIÇÃO",
        "desclassificar": "DESCLASSIFICAÇÃO (art. 383 do CPP)",
        "extinguir": "EXTINÇÃO DA PUNIBILIDADE",
    }.get(verd, str(verd).upper())
    if dec.get("presumido"):
        rotulo += " — PRESUMIDA apenas para simulação (sem veredicto do juízo)"
    return rotulo


def _fatos_decisoes(resultado: Optional[ResultadoDeterministico], delito: Optional[str] = None) -> List[str]:
    if resultado is None:
        return [
            "Veredicto por crime: " + marcador("veredicto do juízo para cada crime imputado (condenar, absolver, desclassificar ou extinguir)")
        ]
    fatos = []
    for d in resultado.decisoes_crimes:
        if delito and delito not in (d.get("crime_code"), d.get("crime_efetivo")):
            continue
        sp = d["spec_original"]
        fatos.append(
            f"Decisão do juízo — {sp.nome} ({sp.artigo_formatado}): {_rotulo_veredicto(d)}; fundamento: {d.get('fundamento_legal', '')}."
        )
    return fatos


def _fatos_pendencias(resultado: Optional[ResultadoDeterministico], secao: SecaoTipo) -> List[str]:
    if resultado is None:
        return []
    pend = listar_marcadores_pendentes(resultado.secao(secao))
    if not pend:
        return []
    return ["Pontos que dependem de decisão/dado do juízo (reproduza o marcador literalmente; não invente): " + "; ".join(pend)]


def _filtrar_teses_por_delito(teses: List[Any], delito: Optional[str]) -> List[Any]:
    if not delito:
        return teses
    filtradas = []
    for t in teses:
        filtro = _CRIMES_DA_CLASSE.get(_classe_tese(t), lambda c: True)
        if filtro(delito):
            filtradas.append(t)
    return filtradas


def _fmt_tese(t: Any) -> str:
    if tese_vedada_honra(t):
        return f"{t.codigo}: {t.titulo} - {t.descricao_defesa} Não cogite seu acolhimento."
    return f"{t.codigo}: {t.titulo} - {t.descricao_defesa}"


def construir_dossie_secao(
    secao: SecaoTipo,
    bundle: Dict[str, Any],
    delito_especifico: Optional[str] = None,
    custom_dosimetria_override: Optional[Dict[str, Any]] = None,
    *,
    resultado: Optional[ResultadoDeterministico] = None,
) -> DossieRestricaoSecao:
    """Constrói a restrição fática e jurídica exclusiva da seção.

    ``resultado`` (cálculo único da geração) é reutilizado quando informado. Relatório,
    preliminares e mérito funcionam mesmo sem dados completos (dossiê informativo, com marcadores);
    dosimetria e dispositivo exigem o cálculo (propagam ``DadosInsuficientesError``).
    """
    secao = SecaoTipo(getattr(secao, "value", secao))
    if resultado is None:
        _validar_bundle(bundle)
    ctx = resultado.ctx if resultado is not None else obter_contexto(bundle)
    meta = resultado.meta if resultado is not None else ctx.meta
    folhas = _ids_permitidos(ctx)

    if secao in (SecaoTipo.DOSIMETRIA, SecaoTipo.DISPOSITIVO) and resultado is None:
        resultado = calcular_resultado_seccional(bundle, custom_dosimetria_override)
    elif resultado is None:
        resultado = _tentar_resultado(bundle, custom_dosimetria_override)

    data_fato = _data_ou_marcador(meta.data_fato, "data do fato")
    data_rec = _data_ou_marcador(meta.data_recebimento_denuncia, "data do recebimento da denúncia")

    if secao == SecaoTipo.RELATORIO:
        fatos = [
            "Ação penal em contexto de violência doméstica e familiar contra a mulher (Lei nº 11.340/2006).",
            f"Processo nº {meta.numero_processo or marcador('número do processo')}.",
            f"Ofendida: {meta.nome_ofendida or marcador('nome da ofendida')}; Réu: {meta.nome_reu or marcador('nome do réu')}.",
            f"Vínculo doméstico/afetivo: {meta.vinculo_domestico or marcador('vínculo entre réu e ofendida (art. 5º da Lei nº 11.340/2006)')}.",
            f"Crimes imputados na denúncia: {_nomes_crimes(meta, resultado)}.",
            f"Data do fato: {data_fato}; recebimento da denúncia: {data_rec}.",
            "Narre apenas o itinerário documentado nas peças abaixo (não presuma atos processuais sem peça correspondente).",
            *(f"Peça: {linha}" for linha in _indice_pecas(ctx, _CATEGORIAS_ITINERARIO)),
            *_fatos_pendencias(resultado, secao),
        ]
        return DossieRestricaoSecao(
            secao=secao,
            delito_especifico=None,
            folhas_permitidas=folhas,
            fatos_delimitados=fatos,
            teses_defesa=[_fmt_tese(t) for t in meta.teses_defesa],
            diretrizes_jurisprudenciais=[
                "Art. 381, incisos I e II, do Código de Processo Penal (requisitos formais do relatório).",
                "Narrativa histórica e objetiva do itinerário da persecução penal sem emissão de juízos de culpa.",
                "Equidistância e síntese fiel dos requerimentos finais do Ministério Público e da Defesa Técnica.",
            ],
            parametros_dosimetria=None,
        )

    if secao == SecaoTipo.PRELIMINARES:
        prelims = [t for t in meta.teses_defesa if t.tipo == "PRELIMINAR" or t.codigo.startswith("PREL-")]
        if resultado is not None:
            titulo_presc, texto_presc = texto_prescricao_preliminares(resultado.prescricao)
            fato_presc = (
                f"{titulo_presc} (cálculo do motor por crime, art. 119 do CP): {texto_presc}"
                if titulo_presc
                else "Prescrição: não consumada para nenhum crime. NÃO abra tópico de prescrição na sentença."
            )
        else:
            fato_presc = "Prescrição: " + marcador("marcos interruptivos (data do fato e do recebimento da denúncia) para o cálculo da prescrição")
        fatos = [
            f"Data do fato: {data_fato} (direito intertemporal: Lei nº 14.994/2024, vigente desde 10/10/2024).",
            f"Marco interruptivo do art. 117, inciso I, do Código Penal: recebimento da denúncia em {data_rec}.",
            fato_presc,
            (
                "Preliminares arguidas pela Defesa: " + "; ".join(f"{t.codigo} — {t.titulo}" for t in prelims)
                if prelims
                else "Nenhuma preliminar foi arguida pela Defesa nos autos."
            ),
            "Responda cada preliminar com fundamento concreto dos autos; sem fundamento específico, use o marcador [COMPLETAR PELO JUÍZO: ...].",
            *_fatos_pendencias(resultado, secao),
        ]
        return DossieRestricaoSecao(
            secao=secao,
            delito_especifico=None,
            folhas_permitidas=folhas,
            fatos_delimitados=fatos,
            teses_defesa=[_fmt_tese(t) for t in prelims],
            diretrizes_jurisprudenciais=[
                "Art. 41 e Art. 395 do Código de Processo Penal (requisitos formais da denúncia e rejeição de inépcia).",
                "Súmula 542 do STJ (ação penal pública incondicionada para o crime de lesão corporal em violência doméstica).",
                "Súmula 536 do STJ e Art. 41 da Lei nº 11.340/2006 (inaplicabilidade de sursis processual e transação penal da Lei 9.099/95, ADC 19 e ADI 4424/STF).",
                "Lei nº 14.994/2024 (tempus regit actum e ação pública incondicionada para crimes no contexto de violência contra a mulher).",
                "Arts. 109, 110, § 1º, 115, 117 e 119 do Código Penal (prescrição analisada isoladamente por crime).",
            ],
            parametros_dosimetria=None,
        )

    if secao == SecaoTipo.MERITO:
        fatos = [
            f"Vínculo doméstico/afetivo: {meta.vinculo_domestico or marcador('vínculo entre réu e ofendida (art. 5º da Lei nº 11.340/2006)')}.",
            *(f"Prova documental: {linha}" for linha in _indice_pecas(ctx, _CATEGORIAS_PROVA)),
        ]
        if ctx.matriz_oral and ctx.matriz_oral.depoentes:
            for d in ctx.matriz_oral.depoentes:
                fatos.append(f"Depoimento de {d.depoente} ({d.papel} - Mídia {d.media_id}): {d.discurso_indireto_sentenca}")
        else:
            fatos.append(
                "Prova oral: nenhuma transcrição disponível — cite apenas o termo de audiência e use "
                + marcador("síntese da prova oral colhida em audiência")
                + "."
            )
        fatos.extend(_fatos_decisoes(resultado, delito_especifico))
        if delito_especifico:
            fatos.append(f"Enfoque fático restrito ao delito delimitado: {delito_especifico}.")
        if resultado is not None and not resultado.is_demo:
            for decisao in resultado.decisoes_crimes:
                if decisao.get("veredicto") != "extinguir" and (
                    not delito_especifico or delito_especifico in (decisao.get("crime_code"), decisao.get("crime_efetivo"))
                ):
                    fatos.append(resultado.juizo_sobre_provas.merito(decisao))
        fatos.extend(_fatos_pendencias(resultado, secao))

        teses_merito = [
            t for t in meta.teses_defesa
            if t.tipo == "MERITO" or t.codigo.startswith("MER-") or tese_vedada_honra(t)
        ]
        teses_merito = _filtrar_teses_por_delito(teses_merito, delito_especifico)
        return DossieRestricaoSecao(
            secao=secao,
            delito_especifico=delito_especifico,
            folhas_permitidas=folhas,
            fatos_delimitados=fatos,
            teses_defesa=[_fmt_tese(t) for t in teses_merito],
            diretrizes_jurisprudenciais=[
                "Resolução CNJ nº 492/2023 (Protocolo para Julgamento com Perspectiva de Gênero — especial relevo probatório da palavra da vítima).",
                "Súmula 600 do STJ (dispensabilidade de coabitação para incidência da Lei Maria da Penha).",
                "Súmula 589 do STJ (inaplicabilidade do princípio da insignificância em crimes e contravenções com violência doméstica).",
                "Art. 24-A da Lei nº 11.340/2006 (descumprimento de decisão judicial que defere medida protetiva, pressupondo ciência prévia do agente).",
                "Tema Repetitivo 1197 do STJ (a aplicação da agravante do art. 61, inc. II, alínea f, do Código Penal com a Lei Maria da Penha não configura bis in idem).",
                "Tema Repetitivo 1333 do STJ, item 2 (inaplicabilidade da agravante às vias de fato com incidência do §2º do art. 21 da LCP) e vedação de bis in idem quando a violência doméstica já é elementar do tipo (ex.: art. 129, § 13, do CP).",
                "ADPF 779 do STF (inconstitucionalidade da legítima defesa da honra).",
                "Enunciados 1, 39 e 45 do FONAVID.",
                "Art. 155 e art. 386 do CPP (prova judicializada; absolvição pelo inciso indicado pelo juízo).",
            ],
            parametros_dosimetria=None,
        )

    # Dosimetria e dispositivo: o cálculo é obrigatório.
    assert resultado is not None
    dg = resultado.dosimetria_global

    if secao == SecaoTipo.DOSIMETRIA:
        fatos: List[str] = []
        if getattr(dg, "sem_condenacao", False) or not dg.crimes_calculados:
            fatos.append("Absolvição integral ou extinção da punibilidade: prejudicada a aplicação de pena (arts. 59 e 68 do CP).")
        for c in dg.crimes_calculados:
            if delito_especifico and delito_especifico not in (c.crime_spec.code, getattr(c, "desclassificado_de", "")):
                continue
            sp = c.crime_spec
            negativos = [ROTULOS_VETORES_ART_59.get(k, k) for k in c.vetores_negativos_efetivos]
            fatos.append(
                f"{sp.nome} ({sp.artigo_formatado}): pena-base {c.pena_base.format_extenso()}; intermediária "
                f"{c.pena_intermediaria.format_extenso()}; definitiva {c.pena_definitiva.format_extenso()}"
                + (f" e {c.dias_multa} dias-multa, cada qual a {c.valor_unitario_multa} salário mínimo ao tempo do fato"
                   if c.dias_multa and c.valor_unitario_multa else "")
                + f"; vetores do art. 59 desfavoráveis: {', '.join(negativos) if negativos else 'nenhum (pena-base no mínimo legal)'}."
            )
            for k in c.vetores_negativos_efetivos:
                fund = c.vetores_detalhados[k].fundamentacao
                fatos.append(f"Fundamento do vetor {ROTULOS_VETORES_ART_59.get(k, k)} ({sp.artigo_formatado}): {fund}")
        if dg.crimes_calculados:
            fatos.append(f"Pena total: {dg.pena_total_unificada.format_extenso()}{texto_multa_dispositivo(dg)}.")
            fatos.append(f"Regime: {texto_regime_dispositivo(dg)}. {getattr(dg, 'fundamentacao_regime', '') or ''}".strip())
            fatos.append(f"Substituição (art. 44 do CP): {dg.motivo_vedacao_substituicao}")
            fatos.append(f"Sursis (art. 77 do CP): {dg.fundamentacao_sursis}")
            fatos.append(
                "Reparação mínima (art. 387, IV, do CPP): "
                + texto_dano_moral_dispositivo(
                    dg,
                    nome_ofendida=meta.nome_ofendida,
                    data_fato_br=formatar_data_br(meta.data_fato),
                    id_den="denúncia",
                )
                + "."
            )
            fatos.append(f"Detração (art. 387, § 2º, do CPP): {dg.detracao_dias} dia(s) de prisão cautelar documentada.")
        if prescricao_exige_topico(resultado.prescricao):
            for p in getattr(resultado.prescricao, "por_crime", []) or []:
                fatos.append(f"Prescrição por crime (art. 119 do CP): {p.fundamentacao}")
        else:
            fatos.append("Prescrição: não consumada para nenhum crime. NÃO abra tópico de prescrição na dosimetria.")
        for alerta in resultado.alertas[:10]:
            fatos.append(f"Alerta do motor (registre-o, não o contradiga): {alerta}")
        fatos.extend(_fatos_pendencias(resultado, secao))

        teses = [
            t for t in meta.teses_defesa
            if t.tipo == "SUBSIDIARIA" or t.codigo.startswith("SUB-") or "dosimetria" in t.titulo.lower()
            or "mínimo" in t.descricao_defesa.lower() or _classe_tese(t) == "subsidiaria_pena"
        ]
        parametros = dg.model_dump(mode="json")
        parametros["prescricao"] = resultado.prescricao.model_dump(mode="json")
        return DossieRestricaoSecao(
            secao=secao,
            delito_especifico=delito_especifico,
            folhas_permitidas=folhas,
            fatos_delimitados=fatos,
            teses_defesa=[_fmt_tese(t) for t in teses],
            diretrizes_jurisprudenciais=[
                "Art. 59, Art. 68 e Arts. 69 a 71 do Código Penal (cálculo trifásico e concurso de crimes).",
                "Súmula 444 do STJ (vedada a utilização de inquéritos policiais e ações em curso para agravar a pena-base).",
                "Súmula 231 do STJ (a incidência de circunstância atenuante não pode conduzir à redução da pena abaixo do mínimo legal).",
                "Súmula 588 do STJ (a prática de crime ou contravenção penal contra a mulher com violência ou grave ameaça veda a substituição por pena restritiva de direitos).",
                "Súmula 536 do STJ (a suspensão condicional do processo não se aplica na violência doméstica).",
                "Súmula 269 do STJ e art. 33 do CP (regime inicial; detenção não inicia em regime fechado).",
                "Tema Repetitivo 983 do STJ (o dano moral é presumido in re ipsa nos casos de violência doméstica contra a mulher).",
                "Arts. 109, 110, § 1º, 115 e 119 do Código Penal (prescrição por crime).",
            ],
            parametros_dosimetria=parametros,
        )

    # DISPOSITIVO
    comandos = construir_comandos_dispositivo(dg)
    fatos = list(_fatos_decisoes(resultado))
    fatos.extend(f"Comando a reproduzir: {item.texto(meta.nome_reu)}." for item in comandos.imputacoes)
    if comandos.sem_condenacao or not dg.crimes_calculados:
        fatos.extend(
            [
                f"Absolvição integral / extinção da punibilidade do acusado {meta.nome_reu}.",
                "Prejudicada a dosimetria de pena e a reparação mínima (art. 387, IV, do CPP).",
                "Sem condenação em custas processuais (art. 804 do CPP a contrario sensu).",
            ]
        )
    else:
        tipo_c_label = {
            "formal": "concurso formal (art. 70 do Código Penal)",
            "continuado": "continuidade delitiva (art. 71 do Código Penal)",
            "material": "concurso material (art. 69 do Código Penal)",
        }.get(getattr(dg, "tipo_concurso", "nenhum"), "")
        fatos.extend(
            [
                f"Penas definitivas: {comandos.texto_penas()}{texto_multa_dispositivo(comandos)}"
                + (f", em {tipo_c_label}" if tipo_c_label and len(dg.crimes_calculados) > 1 else "")
                + "." + comandos.texto_soma_penas(),
                f"Regime: {texto_regime_dispositivo(comandos)}.",
                "Substituição por restritivas de direitos: "
                + ("cabível (art. 44 do CP)." if dg.substituicao_art44_cabivel else "vedada (art. 44 do CP e Súmula 588/STJ)."),
                "Sursis: " + ("concedido (art. 77 do CP)." if dg.sursis_art77_cabivel else "não concedido (art. 77 do CP)."),
                "Reparação mínima: "
                + texto_dano_moral_dispositivo(
                    dg, nome_ofendida=meta.nome_ofendida, data_fato_br=formatar_data_br(meta.data_fato), id_den="denúncia"
                )
                + ".",
                "Custas processuais (art. 804 do CPP), com ressalva da gratuidade quando cabível.",
                "Comunicações pós-trânsito em julgado (art. 15, III, da CF/88; guia de execução no SEEU; Instituto de Identificação/SINIC).",
            ]
        )
        if resultado.texto_demo:
            fatos.append(f"Direito de recorrer em liberdade (caso demonstrativo): {DEMO_FUNDAMENTO_LIBERDADE}")
        else:
            fatos.append(resultado.juizo_sobre_provas.texto_cautelar())
    fatos.append("Intimação pessoal da ofendida acerca da sentença (art. 21 da Lei nº 11.340/2006).")
    fatos.append("Medidas protetivas de urgência: manter as vigentes ou registrar a revogação documentada nos autos (art. 19, § 6º, da Lei nº 11.340/2006).")
    fatos.extend(_fatos_pendencias(resultado, secao))

    return DossieRestricaoSecao(
        secao=secao,
        delito_especifico=None,
        folhas_permitidas=folhas,
        fatos_delimitados=fatos,
        teses_defesa=[f"{t.codigo}: {t.titulo}" for t in meta.teses_defesa],
        diretrizes_jurisprudenciais=[
            "Art. 387 e Art. 804 do Código de Processo Penal.",
            "Art. 21 e Art. 19, § 6º, da Lei nº 11.340/2006.",
            "Tema Repetitivo 983 do STJ e Tema Repetitivo 1249 do STJ.",
            "Art. 15, inciso III, da Constituição Federal.",
        ],
        comandos_dispositivo=comandos,
        parametros_dosimetria={
            "indenizacao_dano_moral": dg.indenizacao_dano_moral_tema983,
            "dano_moral_arbitramento_pendente": bool(getattr(dg, "dano_moral_arbitramento_pendente", False)),
            "sem_condenacao": bool(getattr(dg, "sem_condenacao", False)),
        },
    )


# ==============================================================================
# PROMPT BOUNDING COM RESTRIÇÃO ESTRITA
# ==============================================================================

def _resumo_parametros_dosimetria(parametros: Dict[str, Any]) -> str:
    """Resumo compacto da memória de cálculo (evita estourar ``num_ctx`` com o dump completo)."""
    crimes = parametros.get("crimes_calculados") if isinstance(parametros, dict) else None
    if not isinstance(crimes, list):
        return json.dumps(parametros, ensure_ascii=False, indent=2, default=str)

    def _pena(p: Any) -> str:
        if isinstance(p, dict):
            return f"{p.get('anos', 0)}a {p.get('meses', 0)}m {p.get('dias', 0)}d"
        return str(p)

    linhas = []
    for c in crimes:
        spec = c.get("crime_spec") or {}
        linhas.append(
            f"- {spec.get('artigo_formatado') or spec.get('code')}: base {_pena(c.get('pena_base'))}; "
            f"intermediária {_pena(c.get('pena_intermediaria'))}; definitiva {_pena(c.get('pena_definitiva'))}; "
            f"dias-multa {c.get('dias_multa', 0)}; vetores negativos {c.get('vetores_negativos_efetivos') or []}"
        )
    linhas.append(f"- Pena total unificada: {_pena(parametros.get('pena_total_unificada'))}; concurso: {parametros.get('tipo_concurso')}")
    linhas.append(f"- Regime inicial: {parametros.get('regime_inicial')} (por espécie: {parametros.get('regime_por_especie') or {}})")
    linhas.append(f"- Substituição art. 44 cabível: {parametros.get('substituicao_art44_cabivel')}; sursis cabível: {parametros.get('sursis_art77_cabivel')}")
    linhas.append(
        f"- Reparação mínima: {parametros.get('indenizacao_dano_moral_tema983')}"
        + (" (pedido sem valor — arbitramento pendente)" if parametros.get("dano_moral_arbitramento_pendente") else "")
    )
    presc = parametros.get("prescricao") or {}
    por_crime_presc = [p for p in (presc.get("por_crime") or []) if isinstance(p, dict)]
    # Sem prescrição e com verificação completa, nada sobre prescrição vai para o texto do redator
    # (a memória estruturada continua em ``parametros``).
    if presc.get("prescricao_reconhecida") or any(
        p.get("prescricao_reconhecida") or not p.get("verificacao_completa", True) for p in por_crime_presc
    ):
        for p in por_crime_presc:
            linhas.append(f"- Prescrição {p.get('artigo_formatado') or p.get('crime_code')}: reconhecida={p.get('prescricao_reconhecida')}")
    return "\n".join(linhas)


def construir_prompt_seccional(
    dossie: DossieRestricaoSecao,
    contexto_anterior: Optional[str] = None,
    feedback_corretivo: Optional[str] = None,
) -> str:
    """Prompt seccional delimitado (universo fechado + instruções negativas estritas).

    O feedback corretivo vai no topo; ``contexto_anterior`` deve chegar RESUMIDO
    (``resumir_contexto_anterior``) para caber em ``num_ctx``.
    """
    blocos = []

    if feedback_corretivo:
        blocos.append(
            f"=== [FEEDBACK CIRÚRGICO DA TENTATIVA ANTERIOR - CORREÇÃO MANDATÓRIA] ===\n"
            f"{feedback_corretivo}\n"
            f"ATENÇÃO: Você deve corrigir OBRIGATORIAMENTE os pontos apontados acima nesta nova tentativa.\n"
            f"========================================================================\n"
        )

    if contexto_anterior:
        blocos.append(
            f"=== [CONTEXTO DE SEÇÕES ANTERIORES SELADAS - LEITURA OBRIGATÓRIA - NÃO REPETIR] ===\n"
            f"{contexto_anterior}\n"
            f"===================================================================================\n"
        )

    folhas_str = ", ".join(dossie.folhas_permitidas)
    fatos_str = "\n".join(f"- {f}" for f in dossie.fatos_delimitados)
    teses_str = (
        "\n".join(f"- {t}" for t in dossie.teses_defesa)
        if dossie.teses_defesa
        else "- Nenhuma tese formal específica delimitada para esta seção."
    )
    diretrizes_str = "\n".join(f"- {d}" for d in dossie.diretrizes_jurisprudenciais)

    instrucoes_especificas = ""
    if dossie.secao == SecaoTipo.DOSIMETRIA:
        instrucoes_especificas = (
            "DIRETRIZ MATEMÁTICA RÍGIDA DA DOSIMETRIA:\n"
            "É TERMINANTEMENTE VEDADO alterar, recalcular ou modificar frações (1/8, 1/6) ou valores de penas calculados pelo Python.\n"
            "Mantenha integralmente a fundamentação individualizada dos 8 vetores do art. 59 do CP, a aplicação da Súmula 231 do STJ, Súmula 444 do STJ e Súmula 588 do STJ.\n"
        )
        presc_par = (dossie.parametros_dosimetria or {}).get("prescricao") or {}
        if any(
            p.get("prescricao_reconhecida") or not p.get("verificacao_completa", True)
            for p in (presc_par.get("por_crime") or [])
            if isinstance(p, dict)
        ):
            instrucoes_especificas += "Cite a prescrição analisada isoladamente para cada crime (art. 119 do CP).\n"
        else:
            instrucoes_especificas += "Não houve prescrição: NÃO abra tópico nem mencione a prescrição.\n"
        if dossie.delito_especifico:
            instrucoes_especificas += f"Delito específico em exame: {dossie.delito_especifico}\n"
        if dossie.parametros_dosimetria:
            instrucoes_especificas += (
                f"Parâmetros matemáticos pré-calculados:\n{_resumo_parametros_dosimetria(dossie.parametros_dosimetria)}\n"
            )

    corpo = (
        f"VOCÊ É O REDATOR JUDICIAL ESPECIALIZADO DO JUIZADO DE VIOLÊNCIA DOMÉSTICA E FAMILIAR CONTRA A MULHER.\n"
        f"Sua missão é redigir com linguagem forense sóbria, escorreita e técnica a seção: {dossie.secao.value.upper()}.\n\n"
        f"DIRETRIZES DE UNIVERSO FECHADO E RESTRIÇÃO ESTRITA (PROMPT BOUNDING):\n"
        f"1. É TERMINANTEMENTE PROIBIDO inventar IDs de folhas ou números de documentos não listados expressamente em folhas_permitidas.\n"
        f"2. É TERMINANTEMENTE PROIBIDO invocar julgados, súmulas, enunciados ou precedentes que não constem das diretrizes_jurisprudenciais autorizadas.\n"
        f"3. É VEDADO criar fatos novos, datas divergentes ou modificar os relatos probatórios constantes dos autos.\n"
        f"4. É OBRIGATÓRIO apreciar e decidir fundamentadamente todas as teses da defesa listadas (art. 381, inciso III, do Código de Processo Penal).\n"
        f"5. Onde faltar dado concreto ou decisão do juízo, escreva o marcador [COMPLETAR PELO JUÍZO: descrição] — é VEDADO preencher a lacuna com suposições.\n"
        f"6. Datas sempre no padrão brasileiro (dd/mm/aaaa ou por extenso); nunca no formato ISO.\n"
        f"7. Nos casos reais, preserve a separação entre relatos, conclusões probatórias e decisão cautelar. "
        f"A aplicação incorpora as conclusões registradas no mérito e no dispositivo.\n\n"
        f"LISTA TAXATIVA DE FOLHAS AUTORIZADAS (folhas_permitidas):\n"
        f"{folhas_str}\n\n"
        f"FATOS DELIMITADOS DOS AUTOS:\n"
        f"{fatos_str}\n\n"
        f"TESES DEFENSIVAS A ENFRENTAR:\n"
        f"{teses_str}\n\n"
        f"DIRETRIZES JURISPRUDENCIAIS E NORMATIVAS AUTORIZADAS:\n"
        f"{diretrizes_str}\n\n"
        f"{instrucoes_especificas}\n"
        f"Redija a seção {dossie.secao.value.upper()} observando estritamente as diretrizes acima:"
    )
    blocos.append(corpo)
    return "\n\n".join(blocos)


_RE_CABECALHO = re.compile(r"^\s*(?:[IVX]+\s*[–—-]|\d+(?:\.\d+)*\.\s|[a-h]\)\s)")
_RE_DECISIVO = re.compile(
    r"\b(?:CONDENO|CONDENAR|ABSOLVO|ABSOLVER|EXTINGUIR|JULGO|Rejeito|Acolho|[Ff]ixo|REGIME|COMPLETAR PELO JU[ÍI]ZO)\b"
)


def resumir_contexto_anterior(
    secoes_seladas: Dict[SecaoTipo, str],
    excluir: Optional[SecaoTipo] = None,
    limite_por_secao: int = 1200,
    limite_total: int = 4000,
) -> str:
    """Resumo das seções já seladas para o prompt: cabeçalhos e frases decisórias, com limite de tamanho."""
    partes: List[str] = []
    total = 0
    for s in ORDEM_SECOES:
        if s == excluir or s not in secoes_seladas:
            continue
        linhas_resumo: List[str] = []
        tamanho = 0
        for linha in (secoes_seladas[s] or "").splitlines():
            linha = linha.strip()
            if not linha:
                continue
            if _RE_CABECALHO.match(linha):
                trecho = linha[:160]
            elif _RE_DECISIVO.search(linha):
                frase = next((f for f in re.split(r"(?<=[.;])\s+", linha) if _RE_DECISIVO.search(f)), linha)
                trecho = frase[:300]
            else:
                continue
            if tamanho + len(trecho) > limite_por_secao:
                break
            linhas_resumo.append(trecho)
            tamanho += len(trecho)
        bloco = f"[{s.value.upper()} SELADO — RESUMO]\n" + "\n".join(linhas_resumo)
        if total + len(bloco) > limite_total:
            break
        partes.append(bloco)
        total += len(bloco)
    return "\n\n".join(partes)


# ==============================================================================
# IN-FLIGHT GATING VALIDATOR
# ==============================================================================

def default_in_flight_gating_validator(
    secao: SecaoTipo,
    rascunho: SecaoRascunho,
    dossie: DossieRestricaoSecao,
) -> ResultadoGating:
    """Validador padrão do crivo seccional imediato.

    Marcadores ``[COMPLETAR PELO JUÍZO: ...]`` NÃO reprovam a seção: são lacunas honestas que o
    juízo preenche (o auditor final os lista como pendências).
    """
    texto = rascunho.texto_redigido.strip()
    erros: List[str] = []
    feedbacks: List[str] = []

    if not texto or len(texto) < 40:
        return ResultadoGating(
            secao=secao,
            aprovado=False,
            score=0.0,
            erros=["Texto da seção vazio ou excessivamente curto."],
            feedbacks_cirurgicos=["Redija o texto substantivo completo da seção."],
            acionar_fallback=False,
        )

    # 1. IDs citados x whitelist do dossiê (qualquer token ID-..., inclusive hashes de mídia)
    ids_encontrados = sorted(set(re.findall(r"\bID-[A-Za-z0-9_-]+\b", texto, re.IGNORECASE)))
    folhas_validas = set(dossie.folhas_permitidas)
    folhas_validas_upper = {f.upper() for f in dossie.folhas_permitidas}
    ids_falsos = [i for i in ids_encontrados if i not in folhas_validas and i.upper() not in folhas_validas_upper]
    if ids_falsos:
        erros.append(f"Citação de IDs não autorizados em folhas_permitidas: {', '.join(ids_falsos)}")
        feedbacks.append(f"Remova ou adeque os IDs {', '.join(ids_falsos)} para a lista de folhas_permitidas.")

    # 2. Teses defensivas (art. 381, III, CPP)
    if secao == SecaoTipo.PRELIMINARES and dossie.teses_defesa:
        codigos = [t.split(":")[0] for t in dossie.teses_defesa]
        if not any(c in texto for c in codigos):
            erros.append("Omissão de enfrentamento de teses defensivas preliminares.")
            feedbacks.append("Aprecie expressamente as teses preliminares suscitadas pela defesa.")

    elif secao == SecaoTipo.MERITO and dossie.teses_defesa:
        codigos = [t.split(":")[0] for t in dossie.teses_defesa if not tese_vedada_honra(t)]
        exigido = any(tese_vedada_honra(t) for t in dossie.teses_defesa)
        if exigido and violacao_vedacao_honra(texto, repudio_exigido=True):
            erros.append("Ausência de repúdio expresso à tese vedada VED-01.")
            feedbacks.append("Repudie expressamente a legítima defesa da honra (ADPF 779/STF).")
        if codigos and not any(c in texto for c in codigos):
            erros.append("Omissão de enfrentamento de teses de mérito da defesa.")
            feedbacks.append("Aprecie todas as teses de mérito da defesa técnica (art. 381, III, CPP).")

    # 3. Os 8 vetores do art. 59 na dosimetria (comparação sem acentos)
    elif secao == SecaoTipo.DOSIMETRIA:
        sem_condenacao = bool((dossie.parametros_dosimetria or {}).get("sem_condenacao"))
        texto_norm = remover_acentos(texto.lower())
        if not (
            sem_condenacao
            or (
                "prejudicada a aplicacao de pena" in texto_norm
                and ("absolvicao" in texto_norm or "extincao da punibilidade" in texto_norm)
            )
        ):
            vetores_art59_map = [
                ("culpabilidade", ["culpabilidade"]),
                ("antecedentes", ["antecedentes"]),
                ("conduta social", ["conduta social"]),
                ("personalidade", ["personalidade"]),
                ("motivos", ["motivos"]),
                ("circunstâncias", ["circunstancias", "circunstancia"]),
                ("consequências", ["consequencias", "consequencia"]),
                ("comportamento da vítima", ["comportamento da vitima", "comportamento da ofendida", "comportamento vitima", "comportamento ofendida"]),
            ]
            vetores_faltantes = [
                nome for nome, variantes in vetores_art59_map
                if not any(remover_acentos(v.lower()) in texto_norm for v in variantes)
            ]
            if vetores_faltantes:
                erros.append(f"Omissão dos vetores do art. 59: {', '.join(vetores_faltantes)}")
                feedbacks.append(f"Valore expressamente os vetores do art. 59 do CP: {', '.join(vetores_faltantes)}.")

    # 4. Comandos mandatórios no dispositivo
    elif secao == SecaoTipo.DISPOSITIVO:
        up_disp = texto.upper()
        if "CONDENAR" not in up_disp and "CONDENO" not in up_disp and "ABSOLVER" not in up_disp and "EXTINGUIR" not in up_disp:
            erros.append("Dispositivo não contém comando condenatório ou absolutório explícito.")
            feedbacks.append("Insira comando decisório de condenação ou absolvição individualizado.")
        if "art. 21" not in texto and "Lei nº 11.340" not in texto and "ofendida" not in texto.lower():
            erros.append("Omissão de determinação de intimação pessoal da ofendida (art. 21 da Lei 11.340/2006).")
            feedbacks.append("Determine expressamente a intimação pessoal da ofendida pelo art. 21 da LMP.")

        if dossie.comandos_dispositivo is not None:
            divergencias = verificar_comandos_dispositivo(texto, dossie.comandos_dispositivo)
            erros.extend(divergencias)
            feedbacks.extend(divergencias)

    aprovado = len(erros) == 0
    score = 100.0 if aprovado else max(0.0, 100.0 - 25.0 * len(erros))
    return ResultadoGating(
        secao=secao,
        aprovado=aprovado,
        score=score,
        erros=erros,
        feedbacks_cirurgicos=feedbacks,
        acionar_fallback=False,
    )


# ==============================================================================
# MÁQUINA DE ESTADOS SECCIONAL
# ==============================================================================

#: Limite de tokens gerados por seção (o mérito e a dosimetria são mais longos).
NUM_PREDICT_POR_SECAO: Dict[SecaoTipo, int] = {
    SecaoTipo.RELATORIO: 1200,
    SecaoTipo.PRELIMINARES: 1200,
    SecaoTipo.MERITO: 2000,
    SecaoTipo.DOSIMETRIA: 2400,
    SecaoTipo.DISPOSITIVO: 1200,
}
_TIMEOUT_OLLAMA = 120.0
_ERRO_OLLAMA = "Ollama indisponível ou retornou resposta vazia"

#: Origem do texto selado de cada seção (telemetria honesta).
FONTE_LLM = "llm"
FONTE_DETERMINISTICA = "deterministico"
FONTE_FALLBACK = "fallback_deterministico"


def _agora() -> str:
    return datetime.datetime.now().isoformat()


class SectionalPipelineEngine:
    """Máquina de estados seccional com In-Flight Gating.

    Calcula o ``ResultadoDeterministico`` UMA vez por execução e o reutiliza nos dossiês, nos
    fallbacks, nas regenerações e na montagem final.
    """

    def __init__(
        self,
        use_ollama: bool = False,
        max_retries: int = 2,
        gating_validator: Optional[Callable[[SecaoTipo, SecaoRascunho, DossieRestricaoSecao], ResultadoGating]] = None,
        settings: Optional[Any] = None,
    ):
        self.use_ollama = use_ollama
        self.max_retries = max_retries
        self.gating_validator = gating_validator or default_in_flight_gating_validator
        self.settings = settings or load_settings()
        self.estado = EstadoPipelineSeccional()
        self._last_bundle: Optional[Dict[str, Any]] = None
        self._custom_override: Optional[Dict[str, Any]] = None
        self._secoes_habilitadas: Optional[set[str]] = None
        self._resultado: Optional[ResultadoDeterministico] = None
        self._dossies: Dict[SecaoTipo, DossieRestricaoSecao] = {}
        self._fontes: Dict[SecaoTipo, str] = {}
        self._ollama_execucao: bool = bool(use_ollama)

    # ------------------------------------------------------------------ compatibilidade
    @property
    def resultado_deterministico(self) -> Optional[ResultadoDeterministico]:
        return self._resultado

    @property
    def _last_meta(self):
        return self._resultado.meta if self._resultado else None

    @property
    def _last_pecas(self):
        return self._resultado.pecas if self._resultado else None

    @property
    def _last_dosimetria(self):
        return self._resultado.dosimetria_global if self._resultado else None

    @property
    def _last_prescricao(self):
        return self._resultado.prescricao if self._resultado else None

    @property
    def secoes_fallback(self) -> List[str]:
        return [s.value for s in ORDEM_SECOES if self.estado.status_secoes.get(s) == StatusSecao.FALLBACK_ACIONADO]

    # ------------------------------------------------------------------ apoio
    def _garantir_resultado(self) -> ResultadoDeterministico:
        if self._resultado is None:
            if not self._last_bundle:
                raise ValueError("Nenhum bundle processual ativo. Execute o pipeline antes de regenerar uma seção.")
            self._resultado = calcular_resultado_seccional(self._last_bundle, self._custom_override)
        return self._resultado

    def _dossie(self, secao: SecaoTipo) -> DossieRestricaoSecao:
        if secao not in self._dossies:
            self._dossies[secao] = construir_dossie_secao(
                secao,
                self._last_bundle or {},
                custom_dosimetria_override=self._custom_override,
                resultado=self._garantir_resultado(),
            )
        return self._dossies[secao]

    def _texto_deterministico(self, secao: SecaoTipo) -> str:
        return self._garantir_resultado().secao(secao)

    def _redigir_llm(self, secao: SecaoTipo, dossie: DossieRestricaoSecao, contexto: str, feedback: Optional[str]) -> str:
        prompt = construir_prompt_seccional(dossie=dossie, contexto_anterior=contexto or None, feedback_corretivo=feedback)
        info_ollama = getattr(self, "_info_modelo_ollama", None) or {}
        modelo_fixo = info_ollama.get("modelo")
        kwargs: Dict[str, Any] = {
            "num_predict": NUM_PREDICT_POR_SECAO.get(secao, 1200),
            "temperature": 0.1,
            "timeout": _TIMEOUT_OLLAMA,
            "num_ctx": getattr(self.settings, "ollama_num_ctx", None),
        }
        if modelo_fixo:
            kwargs["modelo"] = str(modelo_fixo)
        texto = gerar_texto(prompt, **kwargs)
        return (texto or "").strip()

    def _registrar(self, secao: SecaoTipo, tentativa: int, status: StatusSecao, gating: Optional[ResultadoGating]) -> None:
        self.estado.historico_tentativas.setdefault(secao, []).append(
            RegistroTentativaSecao(
                secao=secao,
                tentativa=tentativa,
                status=status,
                resultado_gating=gating,
                timestamp=_agora(),
            )
        )

    def _ciclo_gating(
        self,
        secao: SecaoTipo,
        *,
        usar_llm: bool,
        feedback_inicial: Optional[str],
        numerar: Callable[[int], int],
    ) -> tuple[bool, str, Optional[SecaoRascunho], str]:
        """Laço de tentativas com gating. Retorna (aprovado, texto, último rascunho, fonte)."""
        if not self._resultado.is_demo and secao in (SecaoTipo.MERITO, SecaoTipo.DISPOSITIVO):
            usar_llm = False
        dossie = self._dossie(secao)
        contexto = resumir_contexto_anterior(self.estado.secoes_seladas, excluir=secao) if usar_llm else ""
        feedback = feedback_inicial
        ultimo: Optional[SecaoRascunho] = None
        for i in range(1, self.max_retries + 2):
            tentativa = numerar(i)
            if usar_llm:
                texto = self._redigir_llm(secao, dossie, contexto, feedback)
                if not texto:
                    self._registrar(
                        secao,
                        tentativa,
                        StatusSecao.FALLBACK_ACIONADO,
                        ResultadoGating(secao=secao, aprovado=False, score=0.0, erros=[_ERRO_OLLAMA], acionar_fallback=True),
                    )
                    break
            else:
                texto = self._texto_deterministico(secao)

            rascunho = SecaoRascunho(
                secao=secao, delito_especifico=dossie.delito_especifico, texto_redigido=texto, versao_tentativa=tentativa
            )
            ultimo = rascunho
            gating = self.gating_validator(secao, rascunho, dossie)
            if gating.aprovado:
                status = StatusSecao.APROVADO
            elif gating.acionar_fallback:
                status = StatusSecao.FALLBACK_ACIONADO
            else:
                status = StatusSecao.REPROVADO_RETRY
            self._registrar(secao, tentativa, status, gating)

            if gating.aprovado:
                self.estado.status_secoes[secao] = StatusSecao.APROVADO
                return True, texto, rascunho, (FONTE_LLM if usar_llm else FONTE_DETERMINISTICA)
            if gating.acionar_fallback:
                break
            self.estado.status_secoes[secao] = StatusSecao.REPROVADO_RETRY
            feedback = " ".join(gating.feedbacks_cirurgicos) or feedback
        return False, "", ultimo, FONTE_FALLBACK

    # ------------------------------------------------------------------ execução
    def executar_pipeline(
        self,
        case_bundle: Dict[str, Any],
        use_ollama: Optional[bool] = None,
        custom_dosimetria_override: Optional[Dict[str, Any]] = None,
        secoes_habilitadas: Optional[List[str]] = None,
    ) -> SentencaGerada:
        """Executa a máquina de estados através das 5 seções judiciais."""
        _validar_bundle(case_bundle)
        self._last_bundle = case_bundle
        self._custom_override = custom_dosimetria_override
        self._ollama_execucao = bool(self.use_ollama if use_ollama is None else use_ollama)
        self._info_modelo_ollama: Dict[str, Any] = {}

        # Valida o plano antes de consultar ou acionar o redator.
        self._resultado = calcular_resultado_seccional(case_bundle, custom_dosimetria_override)
        if self._ollama_execucao:
            self._info_modelo_ollama = resolver_modelo_ollama()
        self._dossies = {}
        self._fontes = {}

        self.estado = EstadoPipelineSeccional(
            status_secoes={s: StatusSecao.PENDENTE for s in SecaoTipo},
            secoes_seladas={},
            historico_tentativas={s: [] for s in SecaoTipo},
        )
        self._secoes_habilitadas = (
            {str(s).strip().lower() for s in secoes_habilitadas}
            if secoes_habilitadas is not None
            else {s.value for s in ORDEM_SECOES}
        )

        for secao in ORDEM_SECOES:
            if secao.value not in self._secoes_habilitadas:
                self.estado.status_secoes[secao] = StatusSecao.PENDENTE
                continue
            self.estado.secao_atual = secao
            self.estado.status_secoes[secao] = StatusSecao.GERANDO

            aprovado, texto, _ultimo, fonte = self._ciclo_gating(
                secao, usar_llm=self._ollama_execucao, feedback_inicial=None, numerar=lambda i: i
            )
            if not aprovado:
                texto = self._texto_deterministico(secao)
                self.estado.status_secoes[secao] = StatusSecao.FALLBACK_ACIONADO
            self._fontes[secao] = fonte
            self.estado.secoes_seladas[secao] = texto

        return self._montar(exigir_completa=False)

    def regenerar_secao_atomica(
        self,
        secao: SecaoTipo,
        feedback: Optional[str] = None,
        use_ollama: Optional[bool] = None,
    ) -> SecaoRascunho:
        """Regenera apenas uma seção, preservando as demais seladas.

        Exige as seções anteriores seladas. Com LLM: gating + retentativas + fallback. Sem LLM:
        restaura o texto de referência do motor determinístico (sem gating, pois não há texto novo
        a avaliar).
        """
        if not self._last_bundle:
            raise ValueError("Nenhum bundle processual ativo. Execute o pipeline antes de regenerar uma seção.")
        secao = SecaoTipo(getattr(secao, "value", secao))
        for pre in ORDEM_SECOES[: ORDEM_SECOES.index(secao)]:
            if pre not in self.estado.secoes_seladas:
                raise ValueError("Pipeline incompleto ou seções prévias ausentes")

        usar_llm = bool(self.use_ollama if use_ollama is None else use_ollama)
        proxima_versao = lambda _i: len(self.estado.historico_tentativas.get(secao, [])) + 1  # noqa: E731

        if usar_llm:
            if not getattr(self, "_info_modelo_ollama", None):
                self._info_modelo_ollama = resolver_modelo_ollama()
            aprovado, texto, ultimo, fonte = self._ciclo_gating(
                secao, usar_llm=True, feedback_inicial=feedback, numerar=proxima_versao
            )
            if not aprovado:
                texto = self._texto_deterministico(secao)
                self.estado.status_secoes[secao] = StatusSecao.FALLBACK_ACIONADO
                ultimo = SecaoRascunho(secao=secao, texto_redigido=texto, versao_tentativa=proxima_versao(0))
            if fonte == FONTE_LLM:
                self._ollama_execucao = True
        else:
            texto = self._texto_deterministico(secao)
            ultimo = SecaoRascunho(secao=secao, texto_redigido=texto, versao_tentativa=proxima_versao(0))
            self._registrar(secao, ultimo.versao_tentativa, StatusSecao.APROVADO, None)
            self.estado.status_secoes[secao] = StatusSecao.APROVADO
            fonte = FONTE_DETERMINISTICA

        self._fontes[secao] = fonte
        self.estado.secoes_seladas[secao] = texto
        return ultimo

    def _modelo_real_ollama(self) -> str:
        info = getattr(self, "_info_modelo_ollama", None) or {}
        if info.get("modelo"):
            return str(info["modelo"])
        try:
            from app.core.vram_manager import _ULTIMA_DECISAO_OLLAMA

            ultima = _ULTIMA_DECISAO_OLLAMA or {}
            if ultima.get("modelo"):
                return str(ultima["modelo"])
        except Exception:
            pass
        return getattr(self.settings, "ollama_model", "") or "modelo local"

    # ------------------------------------------------------------------ montagem e telemetria
    def _rotulo_motor(self) -> str:
        seladas = [s for s in ORDEM_SECOES if s in self.estado.secoes_seladas]
        fallback = [s.value for s in seladas if self.estado.status_secoes.get(s) == StatusSecao.FALLBACK_ACIONADO]
        llm = [s.value for s in seladas if self._fontes.get(s) == FONTE_LLM and s.value not in fallback]
        modelo = self._modelo_real_ollama()
        if llm and fallback:
            return (
                f"Motor Híbrido: Ollama ({modelo}) em {', '.join(llm)} + Fallback Determinístico em {', '.join(fallback)} "
                "(Laya System 1 + Python, Pipeline Seccional)"
            )
        if llm:
            return f"Motor Híbrido: Ollama ({modelo}) + Laya System 1 + Sectional In-Flight Gating (redigidas pelo LLM: {', '.join(llm)})"
        if fallback and self._ollama_execucao:
            return (
                "Motor Híbrido: Fallback Determinístico em "
                + ", ".join(fallback)
                + " (Ollama indisponível ou reprovado no gating; nenhuma seção redigida por LLM)"
            )
        if fallback:
            return (
                f"{MOTOR_DETERMINISTICO} — Pipeline Seccional sem LLM; seções reprovadas no gating mantidas no modelo "
                f"determinístico (fallback): {', '.join(fallback)}"
            )
        return f"{MOTOR_DETERMINISTICO} — Pipeline Seccional sem LLM"

    def _montar(self, exigir_completa: bool) -> SentencaGerada:
        if not self._last_bundle:
            raise ValueError("Nenhum bundle processual ativo. Execute o pipeline antes de montar a sentença.")
        if exigir_completa:
            faltantes = [s.value for s in SecaoTipo if s not in self.estado.secoes_seladas]
            if faltantes:
                raise ValueError(f"Pipeline incompleto ou seções prévias ausentes: {', '.join(faltantes)}")
        resultado = self._garantir_resultado()
        secoes = {s.value: self.estado.secoes_seladas.get(s, "") for s in ORDEM_SECOES}
        fallback = self.secoes_fallback
        alertas: List[str] = []
        llm = [s.value for s in ORDEM_SECOES if self._fontes.get(s) == FONTE_LLM and s.value not in fallback]
        info_ollama = getattr(self, "_info_modelo_ollama", None) or {}
        if info_ollama.get("aviso"):
            alertas.append(str(info_ollama["aviso"]))
        if llm:
            alertas.append(
                f"Seções redigidas pelo LLM local ({self._modelo_real_ollama()}): {', '.join(llm)} — revisar integralmente."
            )
        if fallback:
            alertas.append(
                "Seções substituídas pelo modelo determinístico (fallback): " + ", ".join(fallback) + "."
            )
        nao_geradas = [s.value for s in ORDEM_SECOES if s not in self.estado.secoes_seladas]
        if nao_geradas:
            alertas.append("Seções não geradas nesta execução: " + ", ".join(nao_geradas) + ".")
        return montar_sentenca_gerada(
            resultado,
            secoes,
            motor_utilizado=self._rotulo_motor(),
            secoes_fallback=fallback,
            alertas_extras=alertas,
        )

    def montar_sentenca(self) -> SentencaGerada:
        """Monta a ``SentencaGerada`` a partir das 5 seções seladas (usada após a regeneração atômica)."""
        if not self._last_bundle:
            raise ValueError("Nenhum bundle processual ativo. Execute o pipeline antes de montar a sentença.")
        return self._montar(exigir_completa=True)

    def snapshot_telemetria(self) -> Dict[str, Any]:
        """Telemetria do estado seccional para a API (status, tentativas, score, fallback, fonte)."""
        secoes: Dict[str, Any] = {}
        totais = {
            "tentativas": 0,
            "aprovadas": 0,
            "reprovadas_retry": 0,
            "fallback_acionado": 0,
            "secoes_seladas": 0,
            "caracteres_secoes": 0,
            "retries": 0,
            "fallbacks": 0,
        }

        for secao in ORDEM_SECOES:
            historico = list(self.estado.historico_tentativas.get(secao, []) or [])
            ultimo_registro = historico[-1] if historico else None
            status = self.estado.status_secoes.get(secao)
            status_str = status.value if isinstance(status, StatusSecao) else (status or StatusSecao.PENDENTE.value)
            score = None
            if ultimo_registro is not None and ultimo_registro.resultado_gating is not None:
                score = float(ultimo_registro.resultado_gating.score)
            texto_selado = self.estado.secoes_seladas.get(secao)
            fallback = status == StatusSecao.FALLBACK_ACIONADO
            aprovada = status == StatusSecao.APROVADO
            reprovadas_secao = sum(1 for reg in historico if reg.status == StatusSecao.REPROVADO_RETRY)

            secoes[secao.value] = {
                "status": status_str,
                "tentativas": len(historico),
                "reprovadas_retry": reprovadas_secao,
                "score": score,
                "fallback": fallback,
                "aprovada": aprovada,
                "selada": texto_selado is not None,
                "caracteres": len(texto_selado) if texto_selado else 0,
                "fonte": self._fontes.get(secao),
                "marcadores_pendentes": len(listar_marcadores_pendentes(texto_selado or "")),
            }

            totais["tentativas"] += len(historico)
            totais["aprovadas"] += 1 if aprovada else 0
            totais["reprovadas_retry"] += reprovadas_secao
            totais["fallback_acionado"] += 1 if fallback else 0
            totais["secoes_seladas"] += 1 if texto_selado else 0
            totais["caracteres_secoes"] += len(texto_selado) if texto_selado else 0

        secao_atual = self.estado.secao_atual
        totais["retries"] = totais["reprovadas_retry"]
        totais["fallbacks"] = totais["fallback_acionado"]
        algum_llm = any(f == FONTE_LLM for f in self._fontes.values())
        if not self._ollama_execucao and not algum_llm:
            modo_ger = "DETERMINISTICO_OFFLINE"
        elif totais["fallback_acionado"] > 0:
            modo_ger = "FALLBACK_DETERMINISTICO"
        else:
            modo_ger = "LLM_OLLAMA"
        habilitadas = self._secoes_habilitadas or {s.value for s in ORDEM_SECOES}
        info_ollama = getattr(self, "_info_modelo_ollama", None) or {}
        return {
            "case_id": (self._last_bundle or {}).get("case_id"),
            "numero_processo": getattr(self._last_meta, "numero_processo", None),
            "secao_atual": secao_atual.value if isinstance(secao_atual, SecaoTipo) else None,
            "ordem_secoes": [s.value for s in ORDEM_SECOES],
            "secoes": secoes,
            "totais": totais,
            "use_ollama": bool(self.use_ollama),
            "modo_geracao": modo_ger,
            "modelo_ollama_usado": info_ollama.get("modelo") if self._ollama_execucao else None,
            "modelo_ollama_configurado": info_ollama.get("configurado") if self._ollama_execucao else None,
            "modelo_ollama_trocado": bool(info_ollama.get("trocado")) if self._ollama_execucao else False,
            "aviso_ollama": info_ollama.get("aviso") if self._ollama_execucao else None,
            "max_retries": int(self.max_retries),
            "secoes_fallback": self.secoes_fallback,
            "pipeline_completo": all(s in self.estado.secoes_seladas for s in ORDEM_SECOES if s.value in habilitadas),
        }
