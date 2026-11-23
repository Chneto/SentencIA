# -*- coding: utf-8 -*-
"""
Duplo Crivo Seccional (M3) — Agente 9 (Auditor Python Determinístico) + Agente 7
(Juiz Corregedor Adversarial / LLM).

Este módulo compõe, em um único veredito de gating, os dois crivos independentes do
SentencIA sobre UMA seção do pipeline seccional (M1), sem alterar o motor
``sectional_pipeline.py``:

SEMÂNTICA DE CADA CRIVO
=======================

1. **Crivo I — Auditor Python Determinístico (Agente 9,**
   ``app/generation/hallucination_auditor.auditar_secao`` **):**
   - Camada 1: todo ID de peça/folha/mídia citado existe fisicamente no universo fechado
     do dossiê seccional (``dossie.folhas_permitidas`` + mídias);
   - Camada 2: toda Súmula/Tema/Enunciado FONAVID/Resolução CNJ/apelação TJRN/acórdão
     citado consta da Base Fechada Autorizada do gabinete (vedação absoluta de citação
     apócrifa);
   - Camada 3: enfrentamento de 100% das teses defensivas aplicáveis à seção
     (art. 381, III, do CPP e art. 93, IX, da CF/88), com vedação de fórmulas evasivas;
   - Camada 4: art. 381, I a III, do CPP (estrutura canônica integral, quando o texto
     completo é fornecido), art. 21 da Lei nº 11.340/2006 no dispositivo, LC 95/98,
     datas no padrão brasileiro (vedado ISO 8601), ABNT NBR 10520, diretrizes CNJ/AMB e
     Resolução CNJ nº 492/2023, e — exclusivamente na dosimetria — as 8 circunstâncias
     judiciais do art. 59 do CP enunciadas e valoradas.

2. **Crivo II — Juiz Corregedor Adversarial (Agente 7,**
   ``AdversarialJudicialCritic.criticar_secao`` **):**
   escrutínio dialético forense de coerência, standard probatório, perspectiva de gênero
   (CNJ nº 492/2023), direito ao silêncio/revelia (art. 186 e art. 367 do CPP), bis in
   idem (art. 61, II, 'f', do CP), Súmulas 231/444/588 do STJ, omissão de teses
   (art. 381, III, do CPP) e comandos dispositivos (CONDENAR/ABSOLVER + art. 21 da LMP).
   Em ``use_ollama=False`` opera 100% determinístico (Tier 2 do M2); com
   ``use_ollama=True`` usa o Ollama local e degrada graciosamente para o Tier 2 em caso
   de falha de rede/JSON inválido.

REGRA DE COMPOSIÇÃO
===================

- ``aprovado = auditor_python_aprovado AND critico_llm_aprovado AND score >= limiar``;
- ``score = min(score_auditor_python, score_critico_llm)`` (o veredito mais severo
  prevalece — nenhum crivo consegue "compensar" a falha do outro);
- erros e feedbacks cirúrgicos são agregados para alimentar o laço de retentativa
  cirúrgica do ``SectionalPipelineEngine`` (injeção no prompt bounding);
- ``acionar_fallback_deterministico`` só é sinalizado quando a falha persiste após as
  retentativas tratadas pelo engine (``versao_tentativa > max_retries``; com o padrão
  ``max_retries=2``, apenas na 3ª tentativa). O fallback em si continua sendo executado
  pelo M1 (``gerar_fallback_deterministico_secao``) e fica registrado em
  ``SentencaGerada.secoes_fallback``: este módulo apenas SINALIZA, jamais modifica o pipeline.
- marcadores ``[COMPLETAR PELO JUÍZO: ...]`` não bloqueiam o gating in-flight (pressionar o
  redator a removê-los o induziria a inventar o dado); são devolvidos em
  ``marcadores_pendentes`` e cobrados pela auditoria final da sentença.

PRÉ-NORMALIZAÇÃO FORENSE DE CITAÇÕES LITERAIS
=============================================

Antes de submeter o texto ao Agente 7, as citações literais de súmulas/precedentes
(reproduzidas entre aspas ou parênteses após a referência, p. ex. a própria Súmula 231
do STJ transcreve "...redução da pena abaixo do mínimo legal") são elididas e
substituídas por um marcador. Justificativa: o regex do Tier 2 do Agente 7 detecta a
literalidade transcrita como se fosse a fundamentação do julgador — falso positivo
recorrente em textos determinísticos de dosimetria. A elisão NÃO reduz a verificação
anti-alucinação, pois a Camada 2 do Auditor Python confere a referência contra a Base
Fechada e a Camada 1 confere os IDs. A sanitização é desativável via
``sanear_citacoes_literais=False``.

RESTRIÇÕES DE ESCOPO
====================

- Não importa nem modifica ``app/main.py``;
- Não modifica ``app/generation/sectional_pipeline.py`` (usa apenas seus contratos
  públicos: ``SecaoTipo``, ``SecaoRascunho``, ``DossieRestricaoSecao``,
  ``ResultadoGating``);
- Não modifica ``app/generation/adversarial_critic.py`` (usa apenas sua API pública).
"""

import logging
import re
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from app.generation.adversarial_critic import (
    AdversarialJudicialCritic,
    GravidadeCritica,
    RelatorioCriticaAdversarial,
)
from app.generation.hallucination_auditor import (
    RelatorioAuditoriaSecao,
    auditar_secao,
)
from app.generation.sectional_pipeline import (
    DossieRestricaoSecao,
    ResultadoGating,
    SecaoRascunho,
    SecaoTipo,
)

logger = logging.getLogger("sentenca_jvdfm.duplo_crivo")


# ==============================================================================
# 1. CONTRATO DO RESULTADO DO CRIVO DUPLO (PROJECT.md § M3 ↔ M1)
# ==============================================================================


class ResultadoCrivoDuplo(BaseModel):
    """
    Veredito consolidado do Duplo Crivo (Auditor Python + Agente 7) para uma seção.

    ``erros_auditor`` agrega TODOS os erros bloqueantes do crivo duplo: os apontados pelo
    Auditor Python (Camadas 1 a 4) e as falhas de gravidade ``critica`` do Agente 7,
    prefixadas com ``[AGENTE 7]`` para rastreabilidade. ``feedbacks_cirurgicos`` agrega os
    comandos corretivos de ambos os crivos para injeção na retentativa cirúrgica.
    """

    secao: SecaoTipo = Field(..., description="Seção auditada.")
    aprovado: bool = Field(
        ...,
        description="True somente se AMBOS os crivos aprovarem e o score mínimo atingir o limiar.",
    )
    score_auditor_python: float = Field(
        ..., ge=0.0, le=100.0, description="Score do Auditor Python determinístico (0.0 a 100.0)."
    )
    score_critico_llm: float = Field(
        ..., ge=0.0, le=100.0, description="Score dialético do Agente 7 (0.0 a 100.0)."
    )
    auditor_python_aprovado: bool = Field(..., description="Veredito isolado do Auditor Python.")
    critico_llm_aprovado: bool = Field(..., description="Veredito isolado do Agente 7.")
    erros_auditor: List[str] = Field(
        default_factory=list, description="Erros bloqueantes agregados (auditor + falhas críticas do crítico)."
    )
    feedbacks_cirurgicos: List[str] = Field(
        default_factory=list, description="Comandos cirúrgicos de correção agregados de ambos os crivos."
    )
    acionar_fallback_deterministico: bool = Field(
        default=False,
        description="True quando a falha persiste após as retentativas: o engine deve acionar o fallback seguro.",
    )
    texto_final_secao: str = Field(
        default="", description="Texto avaliado pelo crivo duplo (apto à selagem quando aprovado)."
    )
    marcadores_pendentes: List[str] = Field(
        default_factory=list,
        description=(
            "Marcadores [COMPLETAR PELO JUÍZO: ...] presentes na seção. No gating in-flight eles não "
            "bloqueiam (são lacunas honestas para o juízo); a auditoria final os reporta como pendências."
        ),
    )


#: Limiar mínimo de score para aprovação combinada (alinhado ao Tier 2 do Agente 7).
LIMIAR_SCORE_PADRAO = 80.0

#: Número de retentativas cirúrgicas tratadas pelo ``SectionalPipelineEngine`` (M1).
MAX_RETRIES_CRIVO_PADRAO = 2


# ==============================================================================
# 2. NORMALIZAÇÃO E SANITIZAÇÃO
# ==============================================================================


def _normalizar_secao(secao: Any) -> SecaoTipo:
    """Normaliza Enum/str/alias de seção para o contrato ``SecaoTipo`` do M1."""
    if isinstance(secao, SecaoTipo):
        return secao
    valor = str(getattr(secao, "value", secao) or "").strip().lower()
    aliases = {
        "relatorio": SecaoTipo.RELATORIO,
        "relatório": SecaoTipo.RELATORIO,
        "preliminares": SecaoTipo.PRELIMINARES,
        "preliminar": SecaoTipo.PRELIMINARES,
        "merito": SecaoTipo.MERITO,
        "mérito": SecaoTipo.MERITO,
        "dosimetria": SecaoTipo.DOSIMETRIA,
        "dispositivo": SecaoTipo.DISPOSITIVO,
    }
    secao_normalizada = aliases.get(valor)
    if secao_normalizada is None:
        raise ValueError(f"Seção inválida para o Duplo Crivo: {secao!r}.")
    return secao_normalizada


def _obter_texto(rascunho: Any) -> str:
    """Extrai o texto redigido de ``SecaoRascunho`` (ou de uma string simples)."""
    if isinstance(rascunho, str):
        return rascunho
    texto = getattr(rascunho, "texto_redigido", None)
    if texto is None and isinstance(rascunho, dict):
        texto = rascunho.get("texto_redigido")
    return str(texto or "")


def _obter_tentativa(rascunho: Any) -> int:
    """Extrai o número da tentativa corrente (``versao_tentativa``) do rascunho."""
    tentativa = getattr(rascunho, "versao_tentativa", None)
    if tentativa is None and isinstance(rascunho, dict):
        tentativa = rascunho.get("versao_tentativa")
    try:
        return max(1, int(tentativa or 1))
    except (TypeError, ValueError):
        return 1


def _montar_universo_fechado(
    dossie: Any,
    bundle: Optional[Dict[str, Any]],
) -> tuple:
    """
    Monta o universo fechado de IDs (Camada 1) a partir do dossiê seccional e, quando
    disponível, do case bundle (autos + transcrições).

    O dossiê é a autoridade primária (é ele que o M1 usa no prompt bounding); o bundle
    complementa com IDs de mídias transcritas e documentos dos autos brutos.
    """
    pecas: List[Any] = []
    for folha in getattr(dossie, "folhas_permitidas", []) or []:
        token = str(folha or "").strip()
        if token:
            pecas.append(token)

    ids_midias: List[str] = []
    if isinstance(bundle, dict):
        for transcricao in bundle.get("transcriptions") or []:
            if isinstance(transcricao, dict):
                for chave in ("media_id", "alias"):
                    token = str(transcricao.get(chave) or "").strip()
                    if token:
                        ids_midias.append(token)
                for alias in transcricao.get("aliases") or []:
                    token = str(alias or "").strip()
                    if token:
                        ids_midias.append(token)
        for documento in bundle.get("raw_documents") or []:
            if isinstance(documento, dict):
                token = str(documento.get("doc_id") or "").strip()
                if token:
                    pecas.append(token)

    return pecas, sorted(set(ids_midias))


#: Citações literais reproduzidas entre aspas/parênteses após a referência normativa.
_PADROES_CITACAO_LITERAL = (
    re.compile(
        r"(\b(?:S[úu]mula|Tema(?:\s+Repetitivo|\s+de\s+Repercussão\s+Geral)?|Enunciado|"
        r"ADPF|ADI|ADC|REsp|HC|AgRg|AREsp|RE)\b[^\n()\[\]]{0,90}?)\s*[(\[]\s*"
        r"['\"“”‘’]([^()\[\]\n]{20,900}?)['\"“”‘’]\s*[)\]]",
        re.IGNORECASE,
    ),
    re.compile(
        r"(\b(?:S[úu]mula|Tema(?:\s+Repetitivo|\s+de\s+Repercussão\s+Geral)?|Enunciado|"
        r"ADPF|ADI|ADC)\b[^\n:]{0,90}?)\s*:\s*['\"“”‘’]([^\"“”‘’\n]{20,900}?)['\"“”‘’]",
        re.IGNORECASE,
    ),
)


def _sanear_citacoes_literais_para_critico(texto: str) -> str:
    """
    Elide citações literais de súmulas/precedentes do texto submetido ao Agente 7.

    O Tier 2 determinístico do Agente 7 detecta a transcrição literal da Súmula 231 do
    STJ ("...abaixo do mínimo legal") como se fosse fundamentação do julgador. Como a
    referência é conferida pela Camada 2 do Auditor Python, a elisão do corpo citado é
    segura e evita falso positivo de composição do Duplo Crivo.
    """

    def _substituir(match: "re.Match[str]") -> str:
        prefixo = match.group(1).strip()
        return f"{prefixo} [citação literal da base fechada, conferida pela Camada 2 do Auditor Python]"

    texto_saneado = texto or ""
    for padrao in _PADROES_CITACAO_LITERAL:
        texto_saneado = padrao.sub(_substituir, texto_saneado)
    return texto_saneado


# ==============================================================================
# 3. COLETA DE ERROS E FEEDBACKS DO AGENTE 7
# ==============================================================================


def _erros_criticos_critico(relatorio: RelatorioCriticaAdversarial) -> List[str]:
    """Extrai os apontamentos de gravidade crítica do Agente 7 (bloqueantes)."""
    return [
        f"[AGENTE 7/{item.tipo.upper()}] {item.descricao}"
        for item in relatorio.itens
        if item.gravidade == GravidadeCritica.CRITICA.value
    ]


def _relatorio_critico_indisponivel(motivo: str) -> RelatorioCriticaAdversarial:
    """Relatório de contingência quando o Agente 7 falha de forma inesperada (nunca silencia)."""
    return RelatorioCriticaAdversarial(
        aprovado=False,
        score_dialetico=0.0,
        itens=[],
        parecer_sintetico=f"Crivo adversarial indisponível: {motivo}",
    )


#: Conformidades do auditor seccional que, todas verdadeiras, indicam aprovação salvo marcadores.
_CONFORMIDADES_AUDITOR = (
    "conformidade_art381_cpp",
    "conformidade_art21_lmp",
    "conformidade_lc95_98",
    "conformidade_datas_padrao_br",
    "conformidade_abnt_citacoes",
    "conformidade_estilo_cnj_amb",
    "conformidade_oito_circunstancias_art59",
    "conformidade_fundamentacao_evasiva",
)
_PREFIXO_ERRO_MARCADOR = "Conteúdo — marcador"
_PREFIXO_FEEDBACK_MARCADOR = "Substitua cada marcador [COMPLETAR PELO JU"


def _neutralizar_marcadores(relatorio: RelatorioAuditoriaSecao) -> tuple:
    """(aprovado, score, erros, feedbacks) do auditor desconsiderando os marcadores pendentes.

    Pressionar o redator (LLM) a remover ``[COMPLETAR PELO JUÍZO: ...]`` o induziria a inventar o
    dado faltante; por isso, no gating in-flight, marcadores não bloqueiam. A auditoria final da
    sentença continua a reprová-los como pendências do juízo.
    """
    marcadores = list(getattr(relatorio, "marcadores_pendentes", []) or [])
    erros = [e for e in relatorio.erros if not str(e).startswith(_PREFIXO_ERRO_MARCADOR)]
    feedbacks = [f for f in relatorio.feedbacks_cirurgicos if not str(f).startswith(_PREFIXO_FEEDBACK_MARCADOR)]
    if not marcadores:
        return bool(relatorio.aprovado), float(relatorio.score), erros, feedbacks
    demais_ok = (
        not relatorio.ids_inexistentes
        and not relatorio.citacoes_nao_autorizadas
        and not relatorio.teses_omitidas
        and all(bool(getattr(relatorio, campo, True)) for campo in _CONFORMIDADES_AUDITOR)
    )
    score = min(100.0, float(relatorio.score) + 15.0 * len(marcadores))
    return bool(demais_ok), score, erros, feedbacks


# ==============================================================================
# 4. EXECUÇÃO DO DUPLO CRIVO
# ==============================================================================


def executar_crivo_duplo(
    secao: Any,
    rascunho: Any,
    dossie: DossieRestricaoSecao,
    *,
    bundle: Optional[Dict[str, Any]] = None,
    use_ollama: bool = False,
    critic: Optional[AdversarialJudicialCritic] = None,
    settings: Optional[Any] = None,
    max_retries: int = MAX_RETRIES_CRIVO_PADRAO,
    limiar_score: float = LIMIAR_SCORE_PADRAO,
    sanear_citacoes_literais: bool = True,
    marcadores_bloqueiam: bool = False,
) -> ResultadoCrivoDuplo:
    """
    Executa o Duplo Crivo sobre uma seção e devolve o veredito consolidado.

    ``marcadores_bloqueiam=False`` (padrão do gating in-flight): marcadores
    ``[COMPLETAR PELO JUÍZO: ...]`` não reprovam a seção — ficam em ``marcadores_pendentes`` e são
    cobrados pela auditoria final. Com ``True``, o veredito do auditor seccional é usado tal qual.

    Parâmetros
    ----------
    secao:
        ``SecaoTipo`` ou string canônica.
    rascunho:
        ``SecaoRascunho`` do M1 (usa ``texto_redigido`` e ``versao_tentativa``). Também
        aceita ``str`` diretamente (tentativa 1).
    dossie:
        ``DossieRestricaoSecao`` com o universo fechado de folhas e as teses exigíveis.
    bundle:
        Case bundle opcional; complementa o universo fechado com mídias/autos brutos.
        Quando omitido (hook do engine M1), o universo é derivado do próprio dossiê.
    use_ollama:
        Se ``True``, o Agente 7 usa o Ollama local (Tier 1 do M2), degradando para o Tier 2
        determinístico em caso de falha. Se ``False`` (default), 100% determinístico.
    critic:
        Instância pré-configurada de ``AdversarialJudicialCritic`` (reutilização/caching).
    settings:
        Objeto de settings opcional repassado à construção do crítico.
    max_retries:
        Número de retentativas cirúrgicas tratadas pelo engine (default 2). A sinalização
        de fallback ocorre quando ``versao_tentativa > max_retries``.
    limiar_score:
        Limiar mínimo de score combinado para aprovação (default 80.0).
    sanear_citacoes_literais:
        Elide citações literais de súmulas do texto enviado ao Agente 7 (default ``True``).

    Retorno
    -------
    ``ResultadoCrivoDuplo`` com os dois scores, os dois vereditos, erros/feedbacks
    agregados e o sinal de fallback determinístico.
    """
    secao_enum = _normalizar_secao(secao)
    texto = _obter_texto(rascunho)
    tentativa = _obter_tentativa(rascunho)

    if dossie is None:
        dossie = DossieRestricaoSecao(
            secao=secao_enum,
            folhas_permitidas=[],
            fatos_delimitados=[],
            teses_defesa=[],
            diretrizes_jurisprudenciais=[],
        )

    # ------------------------------------------------------------------
    # Crivo I — Auditor Python Determinístico (Camadas 1 a 4)
    # ------------------------------------------------------------------
    pecas_autos, ids_midias = _montar_universo_fechado(dossie, bundle)
    teses_defesa = list(getattr(dossie, "teses_defesa", []) or [])
    relatorio_auditor: RelatorioAuditoriaSecao = auditar_secao(
        secao_enum,
        texto,
        pecas_autos,
        teses_defesa,
        ids_midias=ids_midias,
        comandos_dispositivo=dossie.comandos_dispositivo,
    )

    # ------------------------------------------------------------------
    # Crivo II — Juiz Corregedor Adversarial (Agente 7)
    # ------------------------------------------------------------------
    critic_instance = critic or AdversarialJudicialCritic(use_ollama=use_ollama, settings=settings)
    texto_critico = (
        _sanear_citacoes_literais_para_critico(texto) if sanear_citacoes_literais else texto
    )
    rascunho_critico = SecaoRascunho(
        secao=secao_enum,
        delito_especifico=(
            getattr(rascunho, "delito_especifico", None)
            or getattr(dossie, "delito_especifico", None)
        ),
        texto_redigido=texto_critico,
        versao_tentativa=tentativa,
    )
    try:
        relatorio_critico = critic_instance.criticar_secao(
            rascunho=rascunho_critico,
            dossie=dossie,
        )
        erro_critico_indisponivel: Optional[str] = None
    except Exception as exc:  # pragma: no cover - salvaguarda defensiva (M2 não deve lançar)
        logger.warning("Agente 7 falhou de forma inesperada; degradando para veredito bloqueante: %s", exc)
        erro_critico_indisponivel = f"[AGENTE 7] Crivo adversarial indisponível: {exc}"
        relatorio_critico = _relatorio_critico_indisponivel(str(exc))

    # ------------------------------------------------------------------
    # Composição: aprovado = auditor E crítico E score mínimo >= limiar
    # ------------------------------------------------------------------
    marcadores = list(getattr(relatorio_auditor, "marcadores_pendentes", []) or [])
    if marcadores_bloqueiam:
        auditor_aprovado = bool(relatorio_auditor.aprovado)
        score_auditor = float(relatorio_auditor.score)
        erros_auditor = list(relatorio_auditor.erros)
        feedbacks_auditor = list(relatorio_auditor.feedbacks_cirurgicos)
    else:
        auditor_aprovado, score_auditor, erros_auditor, feedbacks_auditor = _neutralizar_marcadores(relatorio_auditor)
    score_critico = float(relatorio_critico.score_dialetico)
    score_combinado = min(score_auditor, score_critico)

    critico_aprovado = bool(
        relatorio_critico.aprovado
        and score_critico >= float(limiar_score)
        and not relatorio_critico.tem_falha_critica()
    )
    aprovado = bool(
        auditor_aprovado
        and critico_aprovado
        and score_combinado >= float(limiar_score)
    )

    # Sinalização de fallback: somente após esgotar as retentativas tratadas pelo engine.
    acionar_fallback = bool((not aprovado) and tentativa > int(max_retries))

    erros_agregados: List[str] = list(erros_auditor)
    if erro_critico_indisponivel:
        erros_agregados.append(erro_critico_indisponivel)
    erros_agregados.extend(_erros_criticos_critico(relatorio_critico))
    feedbacks_agregados: List[str] = list(feedbacks_auditor)
    for feedback in relatorio_critico.extrair_feedbacks_cirurgicos():
        if feedback and feedback not in feedbacks_agregados:
            feedbacks_agregados.append(feedback)

    return ResultadoCrivoDuplo(
        secao=secao_enum,
        aprovado=aprovado,
        score_auditor_python=max(0.0, min(100.0, round(score_auditor, 1))),
        score_critico_llm=max(0.0, min(100.0, round(score_critico, 1))),
        auditor_python_aprovado=auditor_aprovado,
        critico_llm_aprovado=critico_aprovado,
        erros_auditor=erros_agregados,
        feedbacks_cirurgicos=feedbacks_agregados,
        acionar_fallback_deterministico=acionar_fallback,
        texto_final_secao=texto,
        marcadores_pendentes=marcadores,
    )


# ==============================================================================
# 5. ADAPTADOR DE GATING PARA O SECTIONAL PIPELINE ENGINE (M1)
# ==============================================================================


def criar_gating_validator_duplo_crivo(
    *,
    use_ollama: bool = False,
    critic: Optional[AdversarialJudicialCritic] = None,
    settings: Optional[Any] = None,
    max_retries: int = MAX_RETRIES_CRIVO_PADRAO,
    limiar_score: float = LIMIAR_SCORE_PADRAO,
    sanear_citacoes_literais: bool = True,
    marcadores_bloqueiam: bool = False,
) -> Callable[[SecaoTipo, SecaoRascunho, DossieRestricaoSecao], ResultadoGating]:
    """
    Cria um ``gating_validator`` compatível com ``SectionalPipelineEngine``.

    O callable retornado tem a assinatura exata esperada pelo M1:
    ``(secao, rascunho, dossie) -> ResultadoGating`` e mapeia o veredito do Duplo Crivo
    para o contrato de gating (aprovado, score mínimo, erros, feedbacks cirúrgicos e
    sinal de fallback determinístico). O engine permanece responsável pelo laço de
    retentativas (máx. 2) e pela execução do fallback seguro em Python.

    Exemplo de uso::

        from app.generation.duplo_crivo import criar_gating_validator_duplo_crivo
        from app.generation.sectional_pipeline import SectionalPipelineEngine

        engine = SectionalPipelineEngine(
            use_ollama=False,
            gating_validator=criar_gating_validator_duplo_crivo(use_ollama=False),
        )
        sentenca = engine.executar_pipeline(bundle)
    """
    critic_instance = critic or AdversarialJudicialCritic(use_ollama=use_ollama, settings=settings)

    def gating_validator(
        secao: SecaoTipo,
        rascunho: SecaoRascunho,
        dossie: DossieRestricaoSecao,
    ) -> ResultadoGating:
        secao_enum = _normalizar_secao(secao)
        resultado = executar_crivo_duplo(
            secao_enum,
            rascunho,
            dossie,
            bundle=None,
            use_ollama=use_ollama,
            critic=critic_instance,
            settings=settings,
            max_retries=max_retries,
            limiar_score=limiar_score,
            sanear_citacoes_literais=sanear_citacoes_literais,
            marcadores_bloqueiam=marcadores_bloqueiam,
        )
        return ResultadoGating(
            secao=secao_enum,
            aprovado=resultado.aprovado,
            score=min(resultado.score_auditor_python, resultado.score_critico_llm),
            erros=list(resultado.erros_auditor),
            feedbacks_cirurgicos=list(resultado.feedbacks_cirurgicos),
            acionar_fallback=resultado.acionar_fallback_deterministico,
        )

    return gating_validator


__all__ = [
    "ResultadoCrivoDuplo",
    "LIMIAR_SCORE_PADRAO",
    "MAX_RETRIES_CRIVO_PADRAO",
    "criar_gating_validator_duplo_crivo",
    "executar_crivo_duplo",
]
