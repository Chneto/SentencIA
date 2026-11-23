# -*- coding: utf-8 -*-
"""
Agente 7 — Juiz Corregedor e Revisor Forense Adversarial (Duplo Crivo).

Submete minutas de sentenças penais em Juizados de Violência Doméstica e
Familiar contra a Mulher (JVDFM) a escrutínio dialético rígido:
- Feature F07: Matriz de Corroboração Oral (depoimentos vs laudos médicos/periciais)
- Feature F08: Extrator Determinístico de Teses Defensivas
- Feature F09: Adversarial Judicial Critic LLM (Ollama, prompt bounding, JSON ladder)
- Feature F10: Schema Pydantic RelatorioCriticaAdversarial (severidade, itens, score)
- Feature F11: Trava de Omissão de Teses Defensivas (Art. 381, III, CPP; Art. 489 CPC)
- Feature F12: Crivo de Standard Probatório e Gênero (Resolução CNJ nº 492/2023)
- Tier 2 Fallback: Rule Engine 100% determinístico e resiliente (sem paradas no fluxo)
"""

import json
import logging
import re
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
from pydantic import BaseModel, Field, field_validator

from app.core.text_utils import remover_acentos
from app.ingestion.laya_classifier import TeseDefensivaItem, MetadadosProcesso
from app.transcription.oral_proof_analyzer import MatrizCorroboracaoOral

logger = logging.getLogger("sentenca_jvdfm.adversarial_critic")


# ==============================================================================
# 1. Pydantic Schemas & Contracts (F10)
# ==============================================================================


class TipoCritica(str, Enum):
    """Categorias taxativas de vícios forenses e dialéticos auditados pelo Agente 7."""
    DIALETICA = "dialetica"
    OMISSAO_TESE = "omissao_tese"
    STANDARD_PROBATORIO = "standard_probatorio"
    GENERO_CNJ492 = "genero_cnj492"
    CONTRADICAO = "contradicao"


class GravidadeCritica(str, Enum):
    """Níveis de severidade do vício detectado na minuta judicial."""
    CRITICA = "critica"      # Bloqueio mandatório: reprova a minuta imediatamente (-30 pts)
    MODERADA = "moderada"    # Vício relevante (-15 pts); 2 reprovam
    SUGESTAO = "sugestao"    # Recomendação de estilo/clareza (-5 pts)


class ItemCritica(BaseModel):
    """Apontamento individualizado de vício forense, dialético ou normativo."""
    tipo: str = Field(
        ...,
        description="Categoria do vício: dialetica | omissao_tese | standard_probatorio | genero_cnj492 | contradicao",
    )
    descricao: str = Field(
        ...,
        description="Descrição circunstanciada do vício encontrado na minuta.",
    )
    paragrafo_afetado: Optional[str] = Field(
        None,
        description="Trecho ou parágrafo exato onde o vício foi identificado.",
    )
    gravidade: str = Field(
        ...,
        description="Nível de severidade: critica | moderada | sugestao",
    )
    feedback_cirurgico: str = Field(
        ...,
        description="Comando imperativo e cirúrgico para correção do parágrafo/seção na próxima tentativa.",
    )

    @field_validator("tipo", mode="before")
    @classmethod
    def normalizar_tipo(cls, v: Any) -> str:
        val = str(v).lower().strip()
        mapa = {
            "omissao_tese": "omissao_tese",
            "omissao_tese_defesa": "omissao_tese",
            "omissao_de_tese": "omissao_tese",
            "omissao": "omissao_tese",
            "dialetica": "dialetica",
            "coerencia": "dialetica",
            "coerencia_dialetica": "dialetica",
            "standard_probatorio": "standard_probatorio",
            "standard": "standard_probatorio",
            "probatorio": "standard_probatorio",
            "probatario": "standard_probatorio",
            "genero_cnj492": "genero_cnj492",
            "genero": "genero_cnj492",
            "cnj492": "genero_cnj492",
            "cnj_492": "genero_cnj492",
            "protocolo_genero": "genero_cnj492",
            "contradicao": "contradicao",
            "incoerencia": "contradicao",
        }
        if val in mapa:
            return mapa[val]
        valores_validos = [t.value for t in TipoCritica]
        if val in valores_validos:
            return val
        # Se contiver termos-chave
        if "omiss" in val or "tese" in val:
            return "omissao_tese"
        if "genero" in val or "cnj" in val:
            return "genero_cnj492"
        if "probat" in val or "standard" in val:
            return "standard_probatorio"
        if "contrad" in val:
            return "contradicao"
        if "dialet" in val:
            return "dialetica"
        raise ValueError(f"TipoCritica inválido: {v}. Valores aceitos: {valores_validos}")

    @field_validator("gravidade", mode="before")
    @classmethod
    def normalizar_gravidade(cls, v: Any) -> str:
        val = str(v).lower().strip()
        mapa = {
            "critica": "critica",
            "critico": "critica",
            "bloqueio": "critica",
            "grave": "critica",
            "alta": "critica",
            "severa": "critica",
            "moderada": "moderada",
            "media": "moderada",
            "medio": "moderada",
            "alerta": "moderada",
            "sugestao": "sugestao",
            "leve": "sugestao",
            "baixa": "sugestao",
            "recomendacao": "sugestao",
        }
        if val in mapa:
            return mapa[val]
        valores_validos = [g.value for g in GravidadeCritica]
        if val in valores_validos:
            return val
        raise ValueError(f"GravidadeCritica inválida: {v}. Valores aceitos: {valores_validos}")


class RelatorioCriticaAdversarial(BaseModel):
    """Laudo pericial emitido pelo Agente 7 (Adversarial Judicial Critic)."""
    aprovado: bool = Field(
        ...,
        description="True se a minuta estiver apta a ser selada; False se demandar auto-correção cirúrgica.",
    )
    score_dialetico: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Nota dialética e forense da minuta (0.0 a 100.0).",
    )
    itens: List[ItemCritica] = Field(
        default_factory=list,
        description="Relação analítica de vícios e feedbacks cirúrgicos.",
    )
    parecer_sintetico: str = Field(
        ...,
        description="Parecer consolidado do Juiz Corregedor/Revisor justificando a aprovação ou reprovação.",
    )

    def tem_falha_critica(self) -> bool:
        """Verifica se há algum vício de gravidade crítica na minuta."""
        return any(item.gravidade == GravidadeCritica.CRITICA.value for item in self.itens)

    def extrair_feedbacks_cirurgicos(self) -> List[str]:
        """Extrai todos os comandos cirúrgicos de correção para injeção no prompt de retry."""
        return [item.feedback_cirurgico for item in self.itens if item.feedback_cirurgico]

    def to_resultado_gating(self, secao: Any) -> Any:
        """Converte este relatório no contrato ResultadoGating do sectional_pipeline."""
        from app.generation.sectional_pipeline import ResultadoGating, SecaoTipo
        sec = secao if isinstance(secao, SecaoTipo) else SecaoTipo(str(secao))
        erros = [
            f"[{i.tipo.upper()}] {i.descricao}"
            for i in self.itens
            if i.gravidade == GravidadeCritica.CRITICA.value
        ]
        return ResultadoGating(
            secao=sec,
            aprovado=self.aprovado,
            score=self.score_dialetico,
            erros=erros,
            feedbacks_cirurgicos=self.extrair_feedbacks_cirurgicos(),
            acionar_fallback=False,
        )


# ==============================================================================
# 2. Catálogo Canônico de Teses Defensivas no JVDFM (Feature F08)
# ==============================================================================

CODIGO_TESE_VEDADA = "VED-01"

# A conduta precisa ser usada para justificar a violência ou atribuir culpa à ofendida.
# Menção isolada a ciúme, traição ou término como motivo do fato não basta.
_MOTIVO_MORAL = r"(?:traicao|traiu|trair|infidelidade|ciumes?|provocacao\s+moral|provoc\w*\s+moralmente|desonra|reputacao)"
_VIOLENCIA = r"(?:agressao|agressoes|reacao|violencia|conduta|ameaca|pena|reprovacao)"
_JUSTIFICACAO = (
    r"(?:justific\w*|legitim\w*|autoriz\w*|desculp\w*|atenu\w*|abrand\w*|"
    r"compreens[ií]vel|culpad[ao]|responsavel|culpabiliz\w*|deu\s+causa|provocou)"
)
_JANELA = r"[^.!?;\n]{0,95}"
_PADRAO_JUSTIFICACAO_MORAL = (
    rf"(?:{_JUSTIFICACAO}{_JANELA}{_VIOLENCIA}{_JANELA}{_MOTIVO_MORAL}|"
    rf"{_MOTIVO_MORAL}{_JANELA}{_JUSTIFICACAO}{_JANELA}{_VIOLENCIA}|"
    rf"{_MOTIVO_MORAL}{_JANELA}{_VIOLENCIA}{_JANELA}{_JUSTIFICACAO}|"
    rf"(?:vitima|ofendida){_JANELA}(?:deu\s+causa|provocou|culpad[ao]){_JANELA}"
    rf"{_VIOLENCIA}{_JANELA}{_MOTIVO_MORAL})"
)
_PADRAO_TESE_HONRA = (
    r"\b(?:legitima\s+defesa\s+d[ae]\s+(?:sua\s+)?(?:honra|reputacao)|"
    r"em\s+defesa\s+d[ae]\s+(?:(?:sua|propria)\s+)?(?:honra|reputacao)|"
    r"(?:defend\w*|proteg\w*|preserv\w*|resguard\w*)\s+"
    r"(?:(?:a|sua|a\s+sua|a\s+propria)\s+)?(?:honra|reputacao)|"
    rf"{_PADRAO_JUSTIFICACAO_MORAL})\b"
)
_RE_JUSTIFICACAO_MORAL = re.compile(rf"\b{_PADRAO_JUSTIFICACAO_MORAL}\b")
_RE_ATRIBUICAO_TESE = re.compile(
    rf"\b(?:defesa|reu|acusad[ao]|denunciad[ao]){_JANELA}"
    r"(?:aleg\w*|sustent\w*|invoc\w*|argu\w*|afirm\w*|pede|pediu|postul\w*|requer\w*)\b"
)
_RE_TESE_HONRA = re.compile(_PADRAO_TESE_HONRA)
_RE_REPUDIO_HONRA = re.compile(
    r"\b(?:inconstitucion\w*|inadmiss[ií]v\w*|vedad\w*|vedacao|veda|proibid\w*|"
    r"rejeit\w*|afast\w*|repudi\w*|repel\w*|refut\w*|"
    r"(?:nao|jamais|nunca)\s+(?:(?:se|pode|podem|serve\s+para|se\s+pode|e\s+possivel)\s+)?"
    r"(?:acolh\w*|admit\w*|justific\w*|legitim\w*|autoriz\w*|atenu\w*))\b"
)
_RE_NEGACAO_REPUDIO = re.compile(
    r"\b(?:nao|jamais|nunca)\s+(?:(?:se|pode|deve)\s+)?"
    r"(?:rejeit\w*|afast\w*|repudi\w*|repel\w*|refut\w*|"
    r"(?:e|era|seria|foi)\s+(?:manifestamente\s+)?"
    r"(?:inconstitucion\w*|inadmissiv\w*|vedad\w*|proibid\w*))\b"
)
_PARTE_REPUDIO = r"(?:defesa|ministerio\s+publico|acusacao|promotor[a]?|reu|acusad[ao]|mp)"
_RE_REPUDIO_ALHEIO = re.compile(
    rf"\b(?:(?:segundo|para)\s+(?:o\s+|a\s+)?{_PARTE_REPUDIO}|"
    rf"{_PARTE_REPUDIO}\s+(?:(?:expressamente|tambem|ja)\s+)?"
    r"(?:sustent\w*|aleg\w*|consider\w*|entend\w*|afirm\w*|rejeit\w*|"
    r"afast\w*|repudi\w*|repel\w*|refut\w*|opin\w*|pediu|requereu))\b"
)
_RE_REPUDIO_DO_JUIZO = re.compile(r"\b(?:rejeito|afasto|repudio|repilo|refuto|considero|julgo|declaro)\b")
_RE_ACOLHIMENTO_HONRA = re.compile(
    r"\b(?:acolho|acolhe-se|admito|admite-se|reconheco|reconhece-se)\b"
)
_RE_RETOMADA_HONRA = re.compile(r"\b(?:ess[ae]|est[ae]|tal|referid[ao])\s+(?:tese|argumento|alegacao)\b")


def tese_vedada_honra(item: Any) -> bool:
    """Reconhece VED-01 em objetos ou verbetes textuais do dossiê."""
    if isinstance(item, str):
        return re.match(r"^\s*VED-01\b", item, re.IGNORECASE) is not None
    if isinstance(item, dict):
        codigo = item.get("codigo") or item.get("codigo_tese") or ""
        tipo = item.get("tipo") or ""
    else:
        codigo = getattr(item, "codigo", "") or ""
        tipo = getattr(item, "tipo", "") or ""
    return str(codigo).upper() == CODIGO_TESE_VEDADA or str(tipo).upper() == "VEDADA"


def _frases_analise(texto: str) -> List[str]:
    return re.split(r"(?<=[.!?;])\s+|\n+", _remover_acentos(texto or ""))


def _repudio_na_frase(frase: str) -> bool:
    return bool(_RE_REPUDIO_HONRA.search(frase) and not _RE_NEGACAO_REPUDIO.search(frase))


def _repudio_do_juizo(frase: str) -> bool:
    return _repudio_na_frase(frase) and (
        not _RE_REPUDIO_ALHEIO.search(frase) or bool(_RE_REPUDIO_DO_JUIZO.search(frase))
    )


def repudio_explicito_honra(texto: str) -> bool:
    """Exige rejeição vinculada à tese, inclusive quando retomada na frase seguinte."""
    frases = _frases_analise(texto)
    for idx, frase in enumerate(frases):
        if not _RE_TESE_HONRA.search(frase):
            continue
        if _repudio_do_juizo(frase):
            return True
        if idx + 1 < len(frases):
            seguinte = frases[idx + 1]
            if _RE_RETOMADA_HONRA.search(seguinte) and _repudio_do_juizo(seguinte):
                return True
    return False


def violacao_vedacao_honra(texto: str, *, repudio_exigido: bool = False, permitir_relato: bool = False) -> bool:
    """Detecta acolhimento ou ausência de rejeição expressa de argumento vedado."""
    mencoes = [frase for frase in _frases_analise(texto) if _RE_TESE_HONRA.search(frase)]
    for frase in mencoes:
        if _repudio_na_frase(frase):
            continue
        if _RE_ACOLHIMENTO_HONRA.search(frase):
            return True
        if _RE_JUSTIFICACAO_MORAL.search(frase) and not _RE_ATRIBUICAO_TESE.search(frase):
            return True
    return bool((repudio_exigido or (mencoes and not permitir_relato)) and not repudio_explicito_honra(texto))


TABELA_TESES_CANONICAS_JVDFM = [
    # --- PRELIMINARES ---
    {
        "codigo": "PREL-01",
        "tipo": "PRELIMINAR",
        "titulo": "Inépcia da Denúncia e Ausência de Justa Causa",
        "artigos": "Art. 41 e Art. 395, I e III, do CPP",
        "regex": r"\b(?:in[eé]pcia\s+da\s+den[uú]ncia|den[uú]ncia\s+gen[eé]rica|falta\s+de\s+justa\s+causa|lastro\s+probat[oó]rio\s+m[ií]nimo|art\.?\s*41|art\.?\s*395,\s*i\b)\b",
        "descricao_padrao": "Alega narrativa genérica dos fatos ou ausência de lastro probatório mínimo para a deflagração da ação penal.",
    },
    {
        "codigo": "PREL-02",
        "tipo": "PRELIMINAR",
        "titulo": "Nulidade da Prova / Ausência de Laudo Pericial Válido / Ilicitude da Prova Telemática",
        "artigos": "Art. 157 e Art. 158 do CPP e Art. 12, § 3º, da Lei nº 11.340/2006",
        "regex": r"\b(?:nulidade\s+d[oa]\s+(?:processo|prova|laudo)|art\.?\s*158\b|art\.?\s*157\b|aus[eê]ncia\s+de\s+laudo|corpo\s+de\s+delito\s+inconclusivo|cadeia\s+de\s+cust[oó]dia|busca\s+domiciliar\s+il[ií]cita|prova\s+il[ií]cita|ilicitude\s+da\s+prova|prova\s+telem[aá]tica|whatsapp\s+sem\s+autoriza[cç][aã]o|quebra\s+il[ií]cita\s+de\s+sigilo)\b",
        "descricao_padrao": "Sustenta a nulidade ou ilicitude da prova (ausência de exame de corpo de delito ou impugnação de prova telemática / mensagens de WhatsApp).",
    },
    {
        "codigo": "PREL-03",
        "tipo": "PRELIMINAR",
        "titulo": "Extinção da Punibilidade pela Decadência do Direito de Queixa / Representação",
        "artigos": "Art. 38 do CPP e Art. 103 e 107, IV, do CP",
        "regex": r"\b(?:decad[eê]ncia|prazo\s+decadencial|art\.?\s*38\s+d[oe]\s+cpp|art\.?\s*103\s+d[oe]\s+cp|art\.?\s*107,\s*iv|falta\s+de\s+representa[cç][aã]o)\b",
        "descricao_padrao": "Argui o decurso do prazo decadencial sem oferecimento de queixa-crime ou manifestação formal de representação da vítima.",
    },
    {
        "codigo": "PREL-04",
        "tipo": "PRELIMINAR",
        "titulo": "Incompetência do Juizado de Violência Doméstica",
        "artigos": "Art. 5º da Lei nº 11.340/2006 e Súmula 600 do STJ",
        "regex": r"\b(?:incompet[eê]ncia\s+d[oe]\s+ju[ií]zo|aus[eê]ncia\s+de\s+motiva[cç][aã]o\s+de\s+g[eê]nero|art\.?\s*5[ºo]\s+d[oa]\s+lei\s+11\.?340|vara\s+criminal\s+comum)\b",
        "descricao_padrao": "Pugna pela remessa dos autos à Vara Criminal Comum por ausência de motivação de gênero ou vulnerabilidade da vítima.",
    },
    # --- MÉRITO ---
    {
        "codigo": "MER-01",
        "tipo": "MERITO",
        "titulo": "Insuficiência Probatória e Princípio do In Dubio Pro Reo",
        "artigos": "Art. 386, VII, do CPP",
        "regex": r"\b(?:insufici[eê]ncia\s+probat[oó]ria|in\s+dubio\s+pro\s+reo|fragilidade\s+probat[oó]ria|art\.?\s*386,\s*vii|falta\s+de\s+provas|d[uú]vida\s+razo[aá]vel)\b",
        "descricao_padrao": "Postula a absolvição com fundamento na fragilidade probatória e no princípio in dubio pro reo ante a ausência de testemunhas presenciais.",
    },
    {
        "codigo": "MER-02",
        "tipo": "MERITO",
        "titulo": "Negativa de Autoria",
        "artigos": "Art. 386, IV ou V, do CPP",
        "regex": r"\b(?:negativa\s+de\s+autoria|n[aã]o\s+(?:cometeu|praticou|concorreu)|art\.?\s*386,\s*iv\b|art\.?\s*386,\s*v\b|[aá]libi|n[aã]o\s+estava\s+no\s+local)\b",
        "descricao_padrao": "Sustenta que o acusado não foi o autor dos fatos delituosos descritos na denúncia.",
    },
    {
        "codigo": "MER-03",
        "tipo": "MERITO",
        "titulo": "Legítima Defesa / Agressões Recíprocas",
        "artigos": "Art. 23, II, e Art. 25 do CP",
        "regex": r"\b(?:leg[ií]tima\s+defesa|agress[oõ]es\s+rec[ií]procas|art\.?\s*25\s+d[oe]\s+cp|art\.?\s*23,\s*ii|repulsa\s+a\s+agress[aã]o\s+injusta|moderadamente)\b",
        "descricao_padrao": "Alega excludente de ilicitude por repulsa a injusta e atual agressão perpetrada pela ofendida ou agressões físicas mútuas.",
    },
    {
        "codigo": "MER-04",
        "tipo": "MERITO",
        "titulo": "Atipicidade do Crime de Ameaça por Ânimo Exaltado / Estado de Ira",
        "artigos": "Art. 147 do CP e Art. 386, III, do CPP",
        "regex": r"\b(?:atipicidade\s+d[oa]\s+amea[cç]a|[aâ]nimo\s+exaltado|estado\s+de\s+ira|c[oó]lera|desabafo|cabe[cç]a\s+quente|sem\s+seriedade|mal\s+injusto\s+e\s+grave)\b",
        "descricao_padrao": "Argumenta atipicidade da conduta de ameaça sob a alegação de palavras proferidas em estado de exaltação emocional passageira.",
    },
    {
        "codigo": "MER-05",
        "tipo": "MERITO",
        "titulo": "Ausência de Dolo no Descumprimento de Medidas Protetivas",
        "artigos": "Art. 24-A da Lei nº 11.340/2006 e Art. 386, III, do CPP",
        "regex": r"\b(?:aus[eê]ncia\s+de\s+dolo|sem\s+inten[cç][aã]o\s+de\s+descumprir|autoriza[cç][aã]o\s+da\s+v[ií]tima|chamado\s+pela\s+ofendida|ver\s+o\s+filho|pegar\s+pertences)\b",
        "descricao_padrao": "Sustenta ausência de dolo na aproximação da ofendida em razão de convite da própria vítima ou necessidade de contato familiar.",
    },
    {
        "codigo": "MER-06",
        "tipo": "MERITO",
        "titulo": "Desclassificação de Lesão Corporal para Vias de Fato",
        "artigos": "Art. 21 da LCP e Art. 383 do CPP",
        "regex": r"\b(?:vias\s+de\s+fato|art\.?\s*21\s+d[oa]\s+lcp|ofensa\s+integridade\s+corporal)\b",
        "descricao_padrao": "Pede a desclassificação do crime de lesão corporal para a contravenção penal de vias de fato por ausência de dano anatômico permanente.",
    },
    {
        "codigo": "MER-07",
        "tipo": "MERITO",
        "titulo": "Atipicidade Material da Perseguição por Ausência de Reiteração",
        "artigos": "Art. 147-A do CP e Art. 386, III, do CPP",
        "regex": r"\b(?:atipicidade.*persegui[cç][aã]o|aus[eê]ncia\s+de\s+reitera[cç][aã]o|falta\s+de\s+habitualidade|atos\s+isolados|art\.?\s*147-a)\b",
        "descricao_padrao": "Alega atipicidade do delito de perseguição (stalking) por ausência de habitualidade ou reiteração de condutas no tempo.",
    },
    {
        "codigo": "MER-08",
        "tipo": "MERITO",
        "titulo": "Inexigibilidade de Conduta Diversa / Urgência Médica ou Familiar",
        "artigos": "Art. 22 do CP e Art. 386, VI, do CPP",
        "regex": r"\b(?:inexigibilidade\s+de\s+conduta\s+diversa|coac[aã]o\s+moral\s+irresist[ií]vel|art\.?\s*22\s+d[oe]\s+cp|urg[eê]ncia\s+m[eé]dica|estado\s+de\s+necessidade)\b",
        "descricao_padrao": "Sustenta que o réu agiu compelido por urgência incontornável de saúde de dependente menor ou coação moral irresistível.",
    },
    {
        "codigo": "MER-09",
        "tipo": "MERITO",
        "titulo": "Efeitos da Revelia no Processo Penal e Ônus Probatório Acusatório",
        "artigos": "Art. 367 e Art. 155 do CPP",
        "regex": r"\b(?:revelia|art\.?\s*367\s+d[oe]\s+cpp|confiss[aã]o\s+ficta|art\.?\s*155\s+d[oe]\s+cpp|elementos\s+informativos\s+do\s+inqu[eé]rito)\b",
        "descricao_padrao": "Ressalta que a revelia do réu (art. 367 CPP) não induz presunção de culpa nem dispensa prova judicial sob o contraditório (art. 155 CPP).",
    },
    {
        "codigo": "MER-10",
        "tipo": "MERITO",
        "titulo": "Ausência de Dolo ou Consunção na Violação de Domicílio",
        "artigos": "Art. 150 do CP e Art. 386, III, do CPP",
        "regex": r"\b(?:consun[cç][aã]o|princ[ií]pio\s+da\s+consun[cç][aã]o|ingresso\s+consentido|aus[eê]ncia\s+de\s+dolo\s+(?:na\s+viola[cç][aã]o|de\s+invadir)|art\.?\s*150,\s*§\s*1[ºo])\b",
        "descricao_padrao": "Sustenta ausência de dolo específico, consentimento prévio ou absorção do delito de violação de domicílio pelo delito fim.",
    },
    # --- SUBSIDIÁRIAS (DOSIMETRIA) ---
    {
        "codigo": "SUB-01",
        "tipo": "SUBSIDIARIA",
        "titulo": "Fixação da Pena-Base no Mínimo Legal e Atenuantes",
        "artigos": "Art. 59 e Art. 65 do CP, Súmula 231, 444 e 545 do STJ",
        "regex": r"\b(?:pena[- ]base\s+no\s+m[ií]nimo|apenamento.*m[ií]nimo\s+legal|pena\s+no\s+m[ií]nimo\s+legal|pena\s+abaixo\s+d[oe]\s+m[ií]nimo|atenua[cç][aã]o\s+pela\s+confiss[aã]o|circunst[aâ]ncias\s+favor[aá]veis|confiss[aã]o\s+espont[aâ]nea|art\.?\s*65,\s*iii,\s*['\"]?d['\"]?|atenuantes?\s+(?:porventura\s+)?aplic[áa]veis|s[uú]mula\s+231|s[uú]mula\s+444|s[uú]mula\s+545)\b",
        "descricao_padrao": "Requer fixação da pena-base no patamar mínimo legal, eventual redução aquém do piso e incidência da atenuante da confissão espontânea (art. 65, III, 'd', CP e Súmula 545/STJ).",
    },
    {
        "codigo": "SUB-02",
        "tipo": "SUBSIDIARIA",
        "titulo": "Substituição por Restritiva de Direitos, Sursis, Gratuidade e Dano Moral",
        "artigos": "Art. 44 e Art. 77 do CP, Art. 98, § 3º, CPC, Súmula 588 e Tema 983 do STJ",
        "regex": r"\b(?:substitui[cç][aã]o\s+por\s+restritiva|art\.?\s*44\s+d[oe]\s+cp|sursis|art\.?\s*77\s+d[oe]\s+cp|afastamento\s+d[oe]\s+dano\s+moral|tema\s+983|justi[cç]a\s+gratuita|gratuidade\s+(?:de|da)\s+justi[cç]a)\b",
        "descricao_padrao": "Pede substituição por restritivas de direitos, sursis penal (art. 77 CP), gratuidade da justiça ou afastamento/modicidade do dano moral.",
    },
    {
        "codigo": "PREL-05",
        "tipo": "PRELIMINAR",
        "titulo": "Prescrição da Pretensão Punitiva ou Executória",
        "artigos": "Arts. 107, IV, 109 e seguintes do CP",
        "regex": r"\b(?:prescri[cç][aã]o\s+(?:da\s+pretens[aã]o\s+(?:punitiva|execut[oó]ria)|penal|em\s+abstrato|retroativa|intercorrente)|(?:reconhec\w*|declar\w*|argu\w*|ocorr[eê]ncia)\s+(?:a\s+|da\s+|de\s+)?prescri[cç][aã]o|extin[cç][aã]o\s+da\s+punibilidade\s+pela\s+prescri[cç][aã]o|prescri[cç][aã]o\s+consumada)\b",
        "descricao_padrao": "Argui a extinção da punibilidade pela prescrição, com exame do prazo e dos marcos pertinentes.",
    },
    {
        "codigo": "MER-11",
        "tipo": "MERITO",
        "titulo": "Embriaguez e Imputabilidade Penal",
        "artigos": "Art. 28, II e §§ 1º e 2º, do CP",
        "regex": r"\b(?:embriaguez\s+(?:fortuita|acidental|involunt[aá]ria|completa\s+(?:por|decorrente))|(?:alega|sustenta|invoca|em\s+raz[aã]o\s+d[ae])\s+(?:a\s+)?embriaguez|embriaguez[^.!?;\n]{0,65}(?:exclu\w*|afast\w*|reduz\w*|isen[cç][aã]o|absolvi[cç][aã]o|incapacidade|imputabilidade)|(?:isen[cç][aã]o|absolvi[cç][aã]o|redu[cç][aã]o\s+da\s+pena|aus[eê]ncia\s+de\s+dolo)[^.!?;\n]{0,65}\bembriaguez|embriagad[oa][^.!?;\n]{0,65}(?:incapaz|n[aã]o\s+(?:tinha|podia)\s+(?:discern\w*|entender)))\b",
        "descricao_padrao": "Alega que a embriaguez afetou a imputabilidade; examine a origem e o grau invocados.",
    },
    {
        "codigo": "MER-12",
        "tipo": "MERITO",
        "titulo": "Inimputabilidade ou Semi-imputabilidade",
        "artigos": "Art. 26, caput e parágrafo único, do CP",
        "regex": r"\b(?:(?:inimputabilidade|semi[-\s]?imputabilidade)(?:\s+penal|\s+d[oa]\s+(?:r[eé]u|acusad[oa]))?|(?:incapaz|incapacidade)\s+(?:de\s+)?(?:entender\s+o\s+car[aá]ter\s+il[ií]cito|se\s+determinar\s+de\s+acordo)|absolvi[cç][aã]o\s+impr[oó]pria)\b",
        "descricao_padrao": "Suscita inimputabilidade ou capacidade reduzida em razão da alegada condição mental.",
    },
    {
        "codigo": "MER-13",
        "tipo": "MERITO",
        "titulo": "Desclassificação da Lesão do § 13 para o § 9º",
        "artigos": "Art. 129, §§ 9º e 13, do CP",
        "regex": r"\b(?:desclassific\w*|reenquadr\w*|readequ\w*|desloc\w*|afast\w*)(?:[^.!?;\n]|\.(?=\s*\d)){0,130}(?:§\s*13[ºo]?\s+(?:(?![.!?;\n]).){0,45}?(?:§\s*)?9[ºo]?(?!\d)|(?:para|ao|pelo|no)\s+(?:o\s+)?§\s*9[ºo]?(?!\d))",
        "descricao_padrao": "Pede alteração do enquadramento do § 13 para o § 9º do art. 129 do CP.",
    },
    {
        "codigo": "MER-14",
        "tipo": "MERITO",
        "titulo": "Desclassificação de Lesão Corporal Grave para Leve",
        "artigos": "Art. 129, caput e § 1º, do CP",
        "regex": r"\b(?:desclassific\w*|reenquadr\w*|readequ\w*)[^.!?;\n]{0,90}(?:les[aã]o(?:\s+corporal)?\s+leve|(?:les[aã]o(?:\s+corporal)?\s+)?grave\s+(?:para|em)\s+leve)|\b(?:les[aã]o(?:\s+corporal)?\s+grave)[^.!?;\n]{0,90}(?:pass\w*|convert\w*|desclassific\w*)[^.!?;\n]{0,35}\bleve\b",
        "descricao_padrao": "Pede reconhecimento de lesão leve em lugar da modalidade grave imputada.",
    },
    {
        "codigo": "MER-15",
        "tipo": "MERITO",
        "titulo": "Retratação, Reconciliação ou Desinteresse da Vítima",
        "artigos": "Art. 16 da Lei nº 11.340/2006",
        "regex": r"\b(?:retrata[cç][aã]o\s+d[ae]\s+(?:v[ií]tima|ofendida|representa[cç][aã]o)|(?:requ[ei]r\w*|invoc\w*|pleitei\w*)[^.!?;\n]{0,55}(?:reconcilia[cç][aã]o|desinteresse\s+d[ae]\s+(?:v[ií]tima|ofendida))|(?:reconcilia[cç][aã]o|desinteresse|perd[aã]o|retomada\s+da\s+conviv[eê]ncia)[^.!?;\n]{0,85}(?:extin[cç][aã]o|exting\w*|absolv\w*|arquiv\w*|impede|afast\w*|d[aá]\s+causa\s+ao\s+encerramento)|(?:absolv\w*|arquiv\w*|extin[cç][aã]o|exting\w*|improced\w*)[^.!?;\n]{0,85}(?:reconcilia[cç][aã]o|desinteresse\s+d[ae]\s+(?:v[ií]tima|ofendida)|perd[aã]o\s+d[ae]\s+(?:v[ií]tima|ofendida)))\b",
        "descricao_padrao": "Invoca retratação, reconciliação ou desinteresse da ofendida como fundamento de pedido processual ou absolutório.",
    },
    {
        "codigo": "MER-16",
        "tipo": "MERITO",
        "titulo": "Insignificância ou Bagatela",
        "artigos": "Súmula 589/STJ",
        "regex": r"\b(?:princ[ií]pio\s+d[ae]\s+(?:insignific[aâ]ncia|bagatela)|(?:aplica[cç][aã]o|reconhec\w*|invoca\w*|pede|postul\w*|atipicidade\s+material)[^.!?;\n]{0,48}(?:insignific[aâ]ncia|bagatela))\b",
        "descricao_padrao": "Requer o reconhecimento de atipicidade material por alegada insignificância.",
    },
    {
        "codigo": "MER-17",
        "tipo": "MERITO",
        "titulo": "Consentimento da Vítima no Descumprimento de Medida Protetiva",
        "artigos": "Art. 24-A da Lei nº 11.340/2006",
        "regex": r"\b(?:(?:(?:consentimento|anu[eê]ncia|autoriza[cç][aã]o|convite)\s+d[ae]\s+(?:v[ií]tima|ofendida)|(?:v[ií]tima|ofendida)\s+(?:consentiu|anuiu|autorizou|convidou))(?:[^.!?;\n]|\.(?=\s*\d)){0,115}(?:medidas?\s+protetivas?|descumprimento\s+(?:d[ae]\s+)?(?:mpu|ordem)|24-?a)|(?:medidas?\s+protetivas?|descumprimento\s+(?:d[ae]\s+)?(?:mpu|ordem)|24-?a)(?:[^.!?;\n]|\.(?=\s*\d)){0,115}(?:(?:consentimento|anu[eê]ncia|autoriza[cç][aã]o|convite)\s+d[ae]\s+(?:v[ií]tima|ofendida)|(?:v[ií]tima|ofendida)\s+(?:consentiu|anuiu|autorizou|convidou)))\b",
        "descricao_padrao": "A defesa alega consentimento da ofendida à aproximação no episódio de suposto descumprimento da medida protetiva.",
    },
    {
        "codigo": "SUB-03",
        "tipo": "SUBSIDIARIA",
        "titulo": "Bis in Idem na Dosimetria",
        "artigos": "Art. 59 e art. 61, caput, do CP",
        "regex": r"\b(?:bis\s+in\s+idem|dupla\s+valora[cç][aã]o|dupla\s+contagem|valorad[oa]\s+(?:duas\s+vezes|novamente))[^.!?;\n]{0,95}(?:dosimetria|pena[-\s]base|agravante|qualificadora|circunst[aâ]ncia\s+judicial)|\b(?:dosimetria|pena[-\s]base|agravante|qualificadora|circunst[aâ]ncia\s+judicial)[^.!?;\n]{0,95}(?:bis\s+in\s+idem|dupla\s+valora[cç][aã]o|dupla\s+contagem|valorad[oa]\s+duas\s+vezes)\b",
        "descricao_padrao": "Impugna a alegada dupla valoração da mesma circunstância na fixação da pena.",
    },
    {
        "codigo": CODIGO_TESE_VEDADA,
        "tipo": "VEDADA",
        "titulo": "Legítima Defesa da Honra — Tese Vedada",
        "artigos": "ADPF 779/STF",
        "regex": _PADRAO_TESE_HONRA,
        "descricao_padrao": "Argumento vedado. Repudie expressamente qualquer justificação ou atenuação da violência fundada em desonra, ciúme ou culpabilização da vítima (ADPF 779/STF).",
    },
]


def _corresponde_tese(padrao: re.Pattern, texto_norm: str, codigo: str) -> Optional[re.Match]:
    for match in padrao.finditer(texto_norm):
        inicio = max(texto_norm.rfind(sep, 0, match.start()) for sep in (". ", ";", "\n")) + 1
        fins = [texto_norm.find(sep, match.end()) for sep in (". ", ";", "\n")]
        fim = min((pos for pos in fins if pos >= 0), default=len(texto_norm))
        frase = texto_norm[inicio:fim]
        if codigo == CODIGO_TESE_VEDADA and (
            _repudio_na_frase(frase)
            or re.search(
                r"\bnao\s+(?:(?:se|a\s+defesa)\s+)?(?:invoc\w*|aleg\w*|sustent\w*|argu\w*)\b",
                frase,
            )
        ):
            continue
        if codigo == "MER-03" and _RE_TESE_HONRA.search(frase) and not re.search(
            r"\b(?:agressoes\s+reciprocas|agressao\s+injusta|injusta\s+agressao|agressao\s+atual)\b",
            frase,
        ):
            continue
        return match
    return None


def extrair_teses_defensivas(pecas: Any) -> List[TeseDefensivaItem]:
    """
    Feature F08: Mineração determinística exaustiva de preliminares, mérito e
    subsidiárias a partir de peças defensivas (Resposta à Acusação, Memoriais e
    alegações finais orais), preservando o trecho real (<= 300 chars) e a fonte.

    Sem tese reconhecida, retorna lista vazia (nunca injeta MER-01 artificialmente).
    """
    from app.ingestion.laya_classifier import _recortar_trecho

    fontes_defesa: List[Tuple[str, str, str, str]] = []  # (texto, fonte_id, folha, origem)

    # Se recebeu lista de PecaClassificada ou dicts
    if isinstance(pecas, list):
        for item in pecas:
            if hasattr(item, "categoria") and hasattr(item, "conteudo"):
                cat = getattr(item, "categoria", "")
                fid = str(getattr(item, "doc_id", "") or "DEFESA")
                fls = str(getattr(item, "folha", "") or "")
                orig = str(getattr(item, "origem", "memoriais") or "memoriais")
                if cat in ("RESPOSTA_ACUSACAO", "ALEGACOES_FINAIS_DEFESA", "DEFESA_PRELIMINAR") or "DEFESA" in cat:
                    fontes_defesa.append((item.conteudo, fid, fls, orig))
                elif hasattr(item, "resumo_estruturado") and item.resumo_estruturado:
                    if cat in ("RESPOSTA_ACUSACAO", "ALEGACOES_FINAIS_DEFESA"):
                        fontes_defesa.append((item.resumo_estruturado, fid, fls, orig))
            elif isinstance(item, dict):
                cat = str(item.get("categoria", item.get("tipo", "")) or "")
                txt = str(item.get("conteudo", item.get("texto", item.get("raw_text", ""))) or "")
                fid = str(item.get("doc_id") or item.get("fonte_id") or item.get("id") or "DEFESA")
                fls = str(item.get("folhas") or item.get("folha") or "")
                orig = str(item.get("origem") or "memoriais")
                if cat in ("RESPOSTA_ACUSACAO", "ALEGACOES_FINAIS_DEFESA") or "DEFESA" in cat:
                    fontes_defesa.append((txt, fid, fls, orig))
                elif not cat and re.match(r"^\s*(?:resposta à acusação|memoriais da defesa)\b", txt, re.IGNORECASE):
                    fontes_defesa.append((txt, fid, fls, orig))
            elif isinstance(item, str):
                fontes_defesa.append((item, "DEFESA", "", "memoriais"))
    elif isinstance(pecas, dict):
        raw_docs = pecas.get("raw_documents", pecas.get("pecas", []))
        if isinstance(raw_docs, list):
            return extrair_teses_defensivas(raw_docs)
        for k, v in pecas.items():
            if "defesa" in k.lower() or "resposta" in k.lower() or "memoriais" in k.lower():
                fontes_defesa.append((str(v), k, "", "memoriais"))
    elif isinstance(pecas, str):
        fontes_defesa.append((pecas, "DEFESA", "", "memoriais"))

    fontes_normalizadas = [(_remover_acentos(txt), txt, fid, fls, orig) for txt, fid, fls, orig in fontes_defesa if txt]
    teses_detectadas: List[TeseDefensivaItem] = []
    codigos_vistos = set()

    for def_tese in TABELA_TESES_CANONICAS_JVDFM:
        padrao = re.compile(_remover_acentos(def_tese["regex"]))
        cod = def_tese["codigo"]
        if cod in codigos_vistos:
            continue
        for texto_norm, txt_fonte, fid, fls, orig in fontes_normalizadas:
            match = _corresponde_tese(padrao, texto_norm, cod)
            if match is None:
                continue
            trecho = _recortar_trecho(txt_fonte, match.start(), match.end())
            codigos_vistos.add(cod)
            teses_detectadas.append(
                TeseDefensivaItem(
                    codigo=cod,
                    tipo=def_tese["tipo"],
                    titulo=def_tese["titulo"],
                    descricao_defesa=def_tese["descricao_padrao"],
                    obrigatorio_enfrentamento_art381=True,
                    fonte_id=fid,
                    folha=fls,
                    trecho=trecho,
                    origem=orig,
                )
            )
            break

    return teses_detectadas


# ==============================================================================
# 3. Constantes, Regras Forenses & Blacklists de Gênero (F12)
# ==============================================================================

ESTEREOTIPOS_GENERO_BLACK_LIST: List[Tuple[str, str]] = [
    (
        r"comportamento da v[ií]tima contribuiu",
        "Culpabilização da ofendida pelo comportamento anterior ou simultâneo à agressão.",
    ),
    (
        r"provocou a agress[aã]o",
        "Alegação de que a vítima provocou a violência física ou verbal do agressor.",
    ),
    (
        r"ci[uú]mes? m[uú]tuos? justificam",
        "Justificação ilícita de violência com base em ciúmes do casal.",
    ),
    (
        r"mulher hist[eé]rica",
        "Estereótipo pejorativo de desqualificação emocional da mulher.",
    ),
    (
        r"discuss[aã]o de casal n[aã]o enseja crime",
        "Minimização patriarcal da violência doméstica sob a alegação de mera desavença conjugal.",
    ),
    (
        r"vida pregressa da v[ií]tima desabona",
        "Juízo moral ofensivo sobre a vida social, sexual ou amorosa pregressa da mulher.",
    ),
    (
        r"vida sexual da (?:ofendida|v[ií]tima)",
        "Invasão discriminatória e irrelevante sobre a intimidade sexual da ofendida.",
    ),
    (
        r"falta de recato|roupas? provocativas?|vestimenta da v[ií]tima",
        "Julgamento sobre moralidade, recato ou trajes da vítima para mitigar a culpa do agressor.",
    ),
    (
        r"demora em denunciar enfraquece|atraso na den[uú]ncia retira a credibilidade",
        "Desconsideração do tempo necessário para que a mulher rompa a relação de dependência ou coação.",
    ),
    (
        r"reconcilia[cç][aã]o tempor[aá]ria descaracteriza|voltou a conviver demonstrando que n[aã]o houve crime",
        "Desconsideração da dinâmica cíclica da violência doméstica (fase de lua de mel / reconciliação).",
    ),
]


def _remover_acentos(texto: str) -> str:
    """Sem acentos e em minúsculas (comparações heurísticas); delega a ``app.core.text_utils``."""
    return remover_acentos(texto or "").lower()


# ==============================================================================
# 4. Agente 7: Adversarial Judicial Critic Engine
# ==============================================================================

SISTEMA_ADVERSARIAL_CRITIC = """\
VOCÊ É O JUIZ CORREGEDOR E DESEMBARGADOR REVISOR ADVERSARIAL (AGENTE 7) DO TRIBUNAL DE JUSTIÇA.
Sua missão é realizar escrutínio implacável e rigoroso de minutas judiciais de sentenças penais em Juizados de Violência Doméstica e Familiar contra a Mulher (JVDFM).

DIRETRIZES FORENSES DE INSPEÇÃO:
1. TRAVA MANDATÓRIA DE APRECIAÇÃO DAS TESES (Art. 381, III, CPP; Art. 93, IX, CF; Art. 489, § 1º, IV, CPC):
   - Cada uma das teses defensivas listadas para esta seção DEVE ter sido enfrentada e fundamentada.
   - Omissão total ou mera citação genérica sem enfrentamento fático-jurídico é vício de severidade "critica".

2. PROTOCOLO PARA JULGAMENTO COM PERSPECTIVA DE GÊNERO (Resolução CNJ nº 492/2023):
   - A palavra da ofendida no âmbito doméstico possui ESPECIAL RELEVO PROBATÓRIO, pois as agressões ocorrem comumente às ocultas.
   - É TERMINANTEMENTE PROIBIDO qualquer juízo moral sobre recato, vida pregressa, vestimenta, consumo de álcool, atraso na denúncia ou reconciliação no ciclo da violência.
   - VED-01 exige REPÚDIO expresso da legítima defesa da honra e de sua invocação dissimulada (ADPF 779/STF).
   - Qualquer descumprimento do Protocolo de Gênero é vício de severidade "critica".

3. STANDARD PROBATÓRIO E CORROBORAÇÃO ORAL (Art. 158 e 167 do CPP; Art. 12, § 3º, da Lei nº 11.340/2006):
   - Condenação exige certeza moral para além de qualquer dúvida razoável.
   - Em lesão corporal, verifique a corroboração pericial (laudo IML) ou indireta (prontuário médico de emergência / UPA e fotos datadas).

4. COERÊNCIA DIALÉTICA E NÃO-CONTRADIÇÃO:
   - A fundamentação não pode incorrer em contradições internas ou bis in idem (ex.: agravar art. 61, II, 'f' em crime de violência doméstica específico).

FORMATO EXCLUSIVO DE SAÍDA (JSON):
{
  "aprovado": true | false,
  "score_dialetico": 0.0 a 100.0,
  "itens": [
    {
      "tipo": "omissao_tese" | "dialetica" | "standard_probatorio" | "genero_cnj492" | "contradicao",
      "descricao": "Explicação circunstanciada do vício encontrado",
      "paragrafo_afetado": "Parágrafo ou trecho afetado",
      "gravidade": "critica" | "moderada" | "sugestao",
      "feedback_cirurgico": "Comando imperativo de correção para a próxima tentativa de redação"
    }
  ],
  "parecer_sintetico": "Síntese conclusiva da avaliação dialética em até 3 frases."
}
"""


class AdversarialJudicialCritic:
    """
    Agente 7 — Juiz Corregedor e Revisor Forense Adversarial (Duplo Crivo).
    Submete minutas de sentenças a escrutínio dialético rígido:
    - F07: Matriz de Corroboração Oral (depoimentos vs laudos)
    - F08: Extrator e confrontador de teses defensivas
    - F09: Coerência probatória, ausência de contradições e de bis in idem
    - F10: Emissão do schema Pydantic RelatorioCriticaAdversarial
    - F11: Trava mandatória de omissão de teses (Art. 381, III, CPP)
    - F12: Standard probatório e perspectiva de gênero (Resolução CNJ nº 492/2023)
    """

    def __init__(
        self,
        use_ollama: bool = False,
        ollama_url: Optional[str] = None,
        ollama_model: Optional[str] = None,
        temperature: float = 0.1,
        timeout: float = 60.0,
        min_score_aprovacao: float = 80.0,
        settings: Optional[Any] = None,
    ):
        self.use_ollama = use_ollama
        self.temperature = temperature
        self.timeout = timeout
        self.min_score_aprovacao = min_score_aprovacao
        self.settings = settings
        self.num_ctx: Optional[int] = None

        # URL/modelo: argumentos explícitos > settings informados > configuração do usuário
        # (nunca um modelo fixo que pode nem estar instalado).
        cfg = settings
        if cfg is None and use_ollama and (ollama_url is None or ollama_model is None):
            try:
                from app.core.config import load_settings

                cfg = load_settings()
            except Exception:  # pragma: no cover - settings sempre carregam
                cfg = None
        self.ollama_url = ollama_url or getattr(cfg, "ollama_url", None) or "http://127.0.0.1:11434"
        self.ollama_model = ollama_model or getattr(cfg, "ollama_model", None) or ""
        self.num_ctx = getattr(cfg, "ollama_num_ctx", None)

        if settings is not None:
            if hasattr(settings, "use_ollama"):
                self.use_ollama = settings.use_ollama
            if hasattr(settings, "ollama_url"):
                self.ollama_url = settings.ollama_url
            if hasattr(settings, "ollama_model"):
                self.ollama_model = settings.ollama_model

    def criticar_secao(
        self,
        rascunho: Any,
        dossie: Any,
        matriz_oral: Optional[MatrizCorroboracaoOral] = None,
        contexto_processual: Optional[Dict[str, Any]] = None,
    ) -> RelatorioCriticaAdversarial:
        """
        Ponto de entrada primário: avalia uma seção redigida contra o dossiê delimitado.
        Executa LLM (se configurado) ou Fallback Determinístico seguro em Python.
        """
        # A vedação constitucional passa pelo crivo determinístico também no modo LLM.
        texto = getattr(rascunho, "texto_redigido", str(rascunho))
        secao = getattr(rascunho, "secao", None)
        if self.use_ollama and not self._crivo_vedacao_honra(texto, secao, dossie):
            try:
                relatorio_llm = self._avaliar_llm(rascunho, dossie, matriz_oral)
                if relatorio_llm is not None:
                    return relatorio_llm
            except Exception as e:
                logger.warning(
                    f"Tier 1 (Ollama) falhou ou retornou erro. Acionando Tier 2 Determinístico: {e}"
                )

        # Tier 2: Fallback Determinístico seguro em Python
        return self._avaliar_deterministico(rascunho, dossie, matriz_oral)

    def avaliar_minuta_completa(
        self,
        texto_sentenca: str,
        meta: Optional[MetadadosProcesso] = None,
        matriz_oral: Optional[MatrizCorroboracaoOral] = None,
    ) -> RelatorioCriticaAdversarial:
        """Avalia a sentença integral para auditoria global e coerência de capítulos."""
        from app.generation.sectional_pipeline import DossieRestricaoSecao, SecaoRascunho, SecaoTipo

        teses = [f"{t.codigo}: {t.titulo}" for t in meta.teses_defesa] if meta and meta.teses_defesa else []
        dossie = DossieRestricaoSecao(
            secao=SecaoTipo.MERITO,
            folhas_permitidas=[],
            fatos_delimitados=[],
            teses_defesa=teses,
            diretrizes_jurisprudenciais=[],
        )
        rascunho = SecaoRascunho(secao=SecaoTipo.MERITO, texto_redigido=texto_sentenca)
        return self._avaliar_deterministico(rascunho, dossie, matriz_oral=matriz_oral)

    # --------------------------------------------------------------------------
    # Tier 2: Deterministic Rule Engine
    # --------------------------------------------------------------------------

    def _avaliar_deterministico(
        self,
        rascunho: Any,
        dossie: Any,
        matriz_oral: Optional[MatrizCorroboracaoOral] = None,
    ) -> RelatorioCriticaAdversarial:
        """
        Tier 2 Deterministic Rule Engine:
        Garante avaliação em <5ms sem dependência de rede, com escrutínio rigoroso de:
        1. Sanidade mínima de texto
        2. Omissão de teses defensivas (Art. 381, III, CPP)
        3. Standard probatório e gênero (CNJ 492/2023, ADPF 779)
        4. Direito ao silêncio (art. 186 CPP) e efeitos da revelia (art. 367 CPP)
        5. Matriz de corroboração oral vs laudos periciais (art. 167 CPP c/c art. 12 LMP)
        6. Regras de Dosimetria e Dispositivo
        """
        from app.generation.sectional_pipeline import SecaoTipo

        secao = rascunho.secao if hasattr(rascunho, "secao") else SecaoTipo.MERITO
        texto = rascunho.texto_redigido if hasattr(rascunho, "texto_redigido") else str(rascunho)
        itens: List[ItemCritica] = []

        # 1. Sanidade de texto
        if not texto or len(texto.strip()) < 40:
            itens.append(
                ItemCritica(
                    tipo="dialetica",
                    descricao="Minuta da seção vazia ou insuficiente para exame dialético forense.",
                    paragrafo_afetado="Seção " + str(secao.value).upper(),
                    gravidade="critica",
                    feedback_cirurgico="Redija a íntegra da fundamentação jurídica e fática da seção correspondente.",
                )
            )
            return RelatorioCriticaAdversarial(
                aprovado=False,
                score_dialetico=0.0,
                itens=itens,
                parecer_sintetico="Minuta rejeitada por insuficiência quantitativa ou ausência de redação.",
            )

        # 2. Trava de Omissão de Teses Defensivas (Feature F11)
        teses_dossie = dossie.teses_defesa if hasattr(dossie, "teses_defesa") else []
        itens_teses = self._crivo_omissao_teses(texto, secao, teses_dossie)
        itens.extend(itens_teses)

        # 3. Standard Probatório e Protocolo de Gênero (Feature F12)
        itens_genero = self._crivo_standard_probatorio_e_genero(texto, secao, dossie, matriz_oral)
        itens.extend(itens_genero)

        # 4. Coerência dialética, silêncio e revelia (Feature F09)
        itens_dialetica = self._crivo_coerencia_dialetica_e_contradicoes(texto, secao, dossie)
        itens.extend(itens_dialetica)

        # 5. Matriz de Corroboração Oral (Feature F07)
        itens_oral = self._crivo_corroboracao_oral(texto, matriz_oral)
        itens.extend(itens_oral)

        # 6. Escrutínios seccionais específicos (Dosimetria e Dispositivo)
        if secao == SecaoTipo.DOSIMETRIA:
            itens.extend(self._crivo_dosimetria(texto, dossie))
        elif secao == SecaoTipo.DISPOSITIVO:
            itens.extend(self._crivo_dispositivo(texto, dossie))

        score, aprovado = self._calcular_score_e_status(itens)
        parecer = self._gerar_parecer_sintetico(aprovado, score, itens, secao)

        return RelatorioCriticaAdversarial(
            aprovado=aprovado,
            score_dialetico=score,
            itens=itens,
            parecer_sintetico=parecer,
        )

    # --------------------------------------------------------------------------
    # Crivos Forenses Especializados
    # --------------------------------------------------------------------------

    def _crivo_omissao_teses(
        self,
        texto: str,
        secao: Any,
        teses_defesa: List[Any],
    ) -> List[ItemCritica]:
        """
        Feature F11: Enforça o art. 381, III, do CPP garantindo que 100% das teses
        aplicáveis à seção foram enfrentadas motivadamente.
        """
        from app.generation.sectional_pipeline import SecaoTipo
        itens: List[ItemCritica] = []
        if not teses_defesa:
            return itens

        # Só preliminares, mérito e dosimetria exigem enfrentamento de teses defensivas
        if secao not in (SecaoTipo.PRELIMINARES, SecaoTipo.MERITO, SecaoTipo.DOSIMETRIA):
            return itens

        texto_norm = _remover_acentos(texto)

        palavras_decisao = [
            "rejeit", "rejeic", "afast", "nao assiste razao", "inacolhiv", "improced",
            "nao prospera", "nao merece acolhida", "resta superad", "desacolh",
            "acolh", "reconhec", "declar", "absolv", "conden", "inaplicav",
            "descabid", "sem razao", "desprovid", "inadmissiv"
        ]

        for item_t in teses_defesa:
            if tese_vedada_honra(item_t):
                continue
            # Normalização polimórfica do item
            if isinstance(item_t, TeseDefensivaItem):
                codigo = item_t.codigo
                titulo = item_t.titulo
                tipo = item_t.tipo
            elif isinstance(item_t, dict):
                codigo = item_t.get("codigo", "")
                titulo = item_t.get("titulo", "")
                tipo = item_t.get("tipo", "")
            else:
                str_val = str(item_t).strip()
                m_cod = re.match(r"^([A-Za-z]+-\d+)\s*[:—\-]\s*(.*)$", str_val)
                if m_cod:
                    codigo = m_cod.group(1).upper()
                    titulo = m_cod.group(2).strip()
                else:
                    partes = re.split(r"[:—]|\s+-\s+", str_val, maxsplit=1)
                    codigo = partes[0].strip()
                    titulo = partes[1].strip() if len(partes) > 1 else str_val
                tipo = ""

            # Inferência de tipo por código se não especificado
            cod_upper = codigo.upper()
            if not tipo:
                if cod_upper.startswith("PREL") or "inépcia" in titulo.lower() or "decadência" in titulo.lower():
                    tipo = "PRELIMINAR"
                elif cod_upper.startswith("SUB") or "pena-base" in titulo.lower() or "sursis" in titulo.lower():
                    tipo = "SUBSIDIARIA"
                else:
                    tipo = "MERITO"

            # Pertinência seccional
            if secao == SecaoTipo.PRELIMINARES and tipo != "PRELIMINAR":
                continue
            if secao == SecaoTipo.MERITO and tipo != "MERITO":
                continue
            if secao == SecaoTipo.DOSIMETRIA and tipo != "SUBSIDIARIA":
                continue

            codigo_norm = _remover_acentos(codigo)
            titulo_norm = _remover_acentos(titulo)

            # Busca por código exato com delimitação de palavras ou palavras-chave expressivas do título
            codigo_presente = bool(re.search(r"\b" + re.escape(codigo_norm) + r"\b", texto_norm)) if codigo_norm else False
            
            # Palavras-chave significativas do título (>4 letras, excluindo stopwords)
            stopwords = {"para", "como", "sobre", "pelo", "pela", "artigo", "codigo", "penal", "crime", "autos", "defesa"}
            tokens_titulo = [p for p in re.findall(r"\w{4,}", titulo_norm) if p not in stopwords]
            
            # Se for legítima defesa, inépcia, etc.
            titulo_presente = False
            if tokens_titulo:
                # Se pelo menos 2 tokens (ou 1 se muito específico) estiverem presentes
                matches = sum(1 for tok in tokens_titulo if tok in texto_norm)
                if matches >= min(2, len(tokens_titulo)):
                    titulo_presente = True
            
            # Casos específicos conhecidos
            if "inepcia" in titulo_norm and ("inepcia" in texto_norm or "justa causa" in texto_norm):
                titulo_presente = True
            elif "laudo" in titulo_norm and ("laudo" in texto_norm or "art. 158" in texto_norm or "158 do cpp" in texto_norm):
                titulo_presente = True
            elif "decadencia" in titulo_norm and "decadencia" in texto_norm:
                titulo_presente = True
            elif "incompetencia" in titulo_norm and "incompetencia" in texto_norm:
                titulo_presente = True
            elif "legitima defesa" in titulo_norm and ("legitima defesa" in texto_norm or "agressoes reciprocas" in texto_norm):
                titulo_presente = True
            elif "in dubio" in titulo_norm and ("in dubio" in texto_norm or "insuficiencia probatoria" in texto_norm or "fragilidade probatoria" in texto_norm or "falta de provas" in texto_norm):
                titulo_presente = True
            elif "negativa" in titulo_norm and ("negativa de autoria" in texto_norm or "nao cometeu" in texto_norm or "nao praticou" in texto_norm or "alibi" in texto_norm):
                titulo_presente = True
            elif "animo exaltado" in titulo_norm and ("animo exaltado" in texto_norm or "estado de ira" in texto_norm or "colera" in texto_norm or "sem seriedade" in texto_norm):
                titulo_presente = True
            elif "ausencia de dolo" in titulo_norm and ("ausencia de dolo" in texto_norm or "sem intencao" in texto_norm or "autorizacao da vitima" in texto_norm or "chamado pela ofendida" in texto_norm):
                titulo_presente = True
            elif "vias de fato" in titulo_norm and ("vias de fato" in texto_norm or "desclassificacao" in texto_norm):
                titulo_presente = True
            elif "perseguicao" in titulo_norm and ("perseguicao" in texto_norm or "habitualidade" in texto_norm or "reiteracao" in texto_norm):
                titulo_presente = True
            elif "inexigibilidade" in titulo_norm and ("inexigibilidade" in texto_norm or "urgencia medica" in texto_norm or "coacao moral" in texto_norm):
                titulo_presente = True
            elif "minimo" in titulo_norm and ("minimo legal" in texto_norm or "pena-base no minimo" in texto_norm or "pena base no minimo" in texto_norm):
                titulo_presente = True
            elif "sursis" in titulo_norm and ("sursis" in texto_norm or "restritiva de direitos" in texto_norm or "art. 77" in texto_norm or "dano moral" in texto_norm):
                titulo_presente = True

            if not (codigo_presente or titulo_presente):
                # Omissão completa -> CRITICA
                itens.append(
                    ItemCritica(
                        tipo="omissao_tese",
                        descricao=f"Omissão total de enfrentamento da tese defensiva {codigo} ({titulo}). Violação ao art. 381, III, do CPP.",
                        paragrafo_afetado="Seção " + secao.value.upper(),
                        gravidade="critica",
                        feedback_cirurgico=(
                            f"O parágrafo correspondente omitiu a tese defensiva {codigo} ({titulo}). "
                            f"Redija fundamentadamente refutando ou acolhendo a tese nos termos do art. 381, III, do CPP."
                        ),
                    )
                )
            else:
                # Se mencionou a tese, verifica se houve julgamento substancial
                trecho_busca = texto_norm
                houve_decisao = any(pd in trecho_busca for pd in palavras_decisao)
                if not houve_decisao:
                    itens.append(
                        ItemCritica(
                            tipo="dialetica",
                            descricao=f"A tese {codigo} foi tangenciada, mas carece de julgamento explícito e fundamentado (acolhimento ou refutação).",
                            paragrafo_afetado="Seção " + secao.value.upper(),
                            gravidade="moderada",
                            feedback_cirurgico=f"Explicite os motivos fáticos e legais para rejeição ou acolhimento fundamentado da tese {codigo}.",
                        )
                    )

        return itens

    def _crivo_vedacao_honra(self, texto: str, secao: Any, dossie: Any) -> List[ItemCritica]:
        from app.generation.sectional_pipeline import SecaoTipo

        teses = getattr(dossie, "teses_defesa", []) or []
        tese_suscitada = any(tese_vedada_honra(tese) for tese in teses)
        if not violacao_vedacao_honra(
            texto,
            repudio_exigido=tese_suscitada and secao == SecaoTipo.MERITO,
            permitir_relato=tese_suscitada and secao == SecaoTipo.RELATORIO,
        ):
            return []
        return [
            ItemCritica(
                tipo="genero_cnj492",
                descricao="Legítima defesa da honra sem repúdio expresso ou acolhida (VED-01, ADPF 779/STF).",
                paragrafo_afetado="Vedação constitucional da legítima defesa da honra",
                gravidade="critica",
                feedback_cirurgico="Repudie expressamente a legítima defesa da honra e a culpabilização da vítima (ADPF 779/STF).",
            )
        ]

    def _crivo_standard_probatorio_e_genero(
        self,
        texto: str,
        secao: Any,
        dossie: Any,
        matriz_oral: Optional[MatrizCorroboracaoOral] = None,
    ) -> List[ItemCritica]:
        """
        Feature F12: Audita a minuta contra a Resolução CNJ nº 492/2023,
        ADPF 779/STF e valoração probatória no contexto de violência doméstica.
        """
        itens: List[ItemCritica] = []
        texto_norm = _remover_acentos(texto)

        # 1. Blacklist de estereótipos discriminatórios de gênero
        for padrao, msg in ESTEREOTIPOS_GENERO_BLACK_LIST:
            padrao_norm = _remover_acentos(padrao)
            m = re.search(padrao_norm, texto_norm, flags=re.IGNORECASE)
            if m:
                trecho = texto[max(0, m.start() - 20): min(len(texto), m.end() + 20)]
                itens.append(
                    ItemCritica(
                        tipo="genero_cnj492",
                        descricao=f"Violação da Resolução CNJ nº 492/2023 (Protocolo de Gênero): {msg}",
                        paragrafo_afetado=trecho,
                        gravidade="critica",
                        feedback_cirurgico=(
                            "Expurgue imediatamente qualquer juízo moral ou estereótipo de gênero da fundamentação, "
                            "julgando sob a perspectiva de gênero da Resolução CNJ nº 492/2023."
                        ),
                    )
                )

        itens.extend(self._crivo_vedacao_honra(texto, secao, dossie))

        # 3. Especial relevo probatório da palavra da ofendida (crimes no âmbito doméstico)
        from app.generation.sectional_pipeline import SecaoTipo
        if secao == SecaoTipo.MERITO:
            # Se a sentença desqualifica a palavra da vítima meramente por estar desacompanhada de testemunha presencial
            pat_exigencia_testemunha = r"(?:palavra da v[ií]tima isolada|sem testemunhas presenciais|aus[eê]ncia de testemunha presencial).{1,80}(?:n[aã]o autoriza|insuficiente para condena|imp[oõ]e a absolvi[cç][aã]o|inviabiliza a condena)"
            if re.search(pat_exigencia_testemunha, texto_norm, flags=re.IGNORECASE):
                itens.append(
                    ItemCritica(
                        tipo="standard_probatorio",
                        descricao=(
                            "Desconsideração do especial relevo probatório da palavra da vítima em crimes cometidos na clandestinidade do lar. "
                            "Violação à jurisprudência pacífica do STJ e ao Protocolo CNJ nº 492/2023."
                        ),
                        paragrafo_afetado="Mérito / Valoração Probatória",
                        gravidade="critica",
                        feedback_cirurgico=(
                            "Valorize a palavra da ofendida em conjunto com os elementos periféricos de corroboração, "
                            "haja vista que delitos domésticos ocorrem predominantemente sem testemunhas presenciais."
                        ),
                    )
                )

        return itens

    def _crivo_coerencia_dialetica_e_contradicoes(
        self,
        texto: str,
        secao: Any,
        dossie: Any,
    ) -> List[ItemCritica]:
        """
        Audita a coerência lógica, vedação de presunção de culpa pelo silêncio (art. 186 CPP)
        e revelia penal (art. 367 CPP).
        """
        itens: List[ItemCritica] = []
        texto_norm = _remover_acentos(texto)

        # 1. Violação ao direito ao silêncio (art. 186 CPP) e presunção de culpa pela revelia (art. 367 CPP)
        pat_silencio = r"\b(?:sil[eê]ncio|calou|revelia)\b.{1,100}\b(?:presum|confiss|preju[ií]zo|desfavor|culpa|autoria)\b"
        if re.search(pat_silencio, texto_norm, flags=re.IGNORECASE):
            # Exceto se for expressamente afirmando que NÃO prejudica / NÃO presume
            trecho_m = re.search(pat_silencio, texto_norm, flags=re.IGNORECASE).group(0)
            if not any(neg in trecho_m for neg in ("nao importara", "nao pode ser interpretado", "nao induz", "nao gera presuncao", "nao importa em confissao")):
                itens.append(
                    ItemCritica(
                        tipo="dialetica",
                        descricao=(
                            "Violação ao direito constitucional ao silêncio (art. 5º, LXIII, CF e art. 186, parágrafo único, do CPP) "
                            "ou aos efeitos da revelia penal (art. 367 do CPP). O silêncio ou a ausência do réu não autorizam presunção de culpa nem confissão ficta."
                        ),
                        paragrafo_afetado=trecho_m[:60] + "...",
                        gravidade="critica",
                        feedback_cirurgico=(
                            "Expurgue qualquer ilação desfavorável decorrente do silêncio ou revelia do réu, "
                            "fundamentando a convicção exclusivamente no conjunto probatório produzido pela acusação."
                        ),
                    )
                )

        return itens

    def _crivo_corroboracao_oral(
        self,
        texto: str,
        matriz_oral: Optional[MatrizCorroboracaoOral],
    ) -> List[ItemCritica]:
        """
        Feature F07: Confronto entre relatos orais e laudos periciais/médicos.
        Resolve cenários de exame pericial direto inconcluso por decurso de prazo
        (Art. 167 CPP c/c Art. 12, §3º, da Lei nº 11.340/2006).
        """
        itens: List[ItemCritica] = []
        if not matriz_oral:
            return itens

        texto_norm = _remover_acentos(texto)

        # Verifica se algum depoente tem menção a exame pericial direto inconclusivo
        laudo_inconclusivo = False
        tem_prova_indireta_upa = False

        for dep in matriz_oral.depoentes:
            corp_doc = _remover_acentos(dep.corroboracao_pericial_documental)
            if "inconclusivo" in corp_doc or "decurso de prazo" in corp_doc or "tardio" in corp_doc:
                laudo_inconclusivo = True
            if "prontuario" in corp_doc or "upa" in corp_doc or "hospital" in corp_doc or "fotografia" in corp_doc or "fotos" in corp_doc:
                tem_prova_indireta_upa = True

        if laudo_inconclusivo:
            # 1. Se a minuta afirma falsamente que o laudo direto foi conclusivo/positivo
            if "laudo pericial direto comprovou" in texto_norm or "laudo pericial direto conclusivo positivo" in texto_norm:
                itens.append(
                    ItemCritica(
                        tipo="contradicao",
                        descricao="Contradição fática: a minuta afirma que o laudo pericial direto comprovou as lesões, mas o exame pericial foi inconcluso por decurso de prazo.",
                        paragrafo_afetado="Fundamentação probatória / Materialidade",
                        gravidade="critica",
                        feedback_cirurgico="Reconheça a inconclusividade do laudo direto e fundamente a materialidade na prova pericial indireta (art. 167 CPP c/c art. 12, §3º LMP).",
                    )
                )

            # 2. Se a minuta absolve por falta de materialidade ignorando prontuários de emergência e fotos
            if tem_prova_indireta_upa:
                pat_absolv_sem_laudo = r"(?:absolvo|improcede).{1,100}(?:falta de laudo|ausencia de exame de corpo de delito direto)"
                if re.search(pat_absolv_sem_laudo, texto_norm, flags=re.IGNORECASE):
                    itens.append(
                        ItemCritica(
                            tipo="standard_probatorio",
                            descricao="Erro de standard probatório: absolvição por ausência de laudo direto ignorando prontuários médicos e fotografias hábeis a suprir o exame nos termos do art. 167 do CPP c/c art. 12, §3º da Lei nº 11.340/2006.",
                            paragrafo_afetado="Mérito / Materialidade",
                            gravidade="critica",
                            feedback_cirurgico="Acolha a prova documental hospitalar e fotos datadas como prova indireta válida da materialidade delitiva (art. 167 CPP c/c art. 12, §3º da Lei nº 11.340/2006).",
                        )
                    )

        return itens

    def _crivo_dosimetria(
        self,
        texto: str,
        dossie: Any,
    ) -> List[ItemCritica]:
        """Audita vetores do art. 59, Súmula 231, Súmula 444 e bis in idem."""
        itens: List[ItemCritica] = []
        texto_norm = _remover_acentos(texto)

        # 1. Checagem dos 8 vetores do art. 59 do Código Penal
        vetores_art59 = [
            ("culpabilidade", "culpabilidade"),
            ("antecedentes", "antecedentes"),
            ("conduta social", "conduta social"),
            ("personalidade", "personalidade"),
            ("motivos", "motivos"),
            ("circunstancias", "circunstancias"),
            ("consequencias", "consequencias"),
            ("comportamento da vitima", "comportamento da vitima"),
        ]
        vetores_faltantes = [nome for nome, tag in vetores_art59 if tag not in texto_norm]
        if vetores_faltantes:
            itens.append(
                ItemCritica(
                    tipo="dialetica",
                    descricao=f"Omissão de vetores do art. 59 do CP na primeira fase da dosimetria: {', '.join(vetores_faltantes)}.",
                    paragrafo_afetado="Dosimetria / 1ª Fase",
                    gravidade="moderada",
                    feedback_cirurgico=f"Analise individualmente todos os 8 vetores do art. 59 do Código Penal, incluindo: {', '.join(vetores_faltantes)}.",
                )
            )

        # 2. Vedação ao Bis in Idem: agravar art. 61, II, 'f' em tipos já domésticos (ex: art. 129 §9º/§13 ou art. 24-A)
        delito_esp = getattr(dossie, "delito_especifico", "") or ""
        delito_norm = _remover_acentos(delito_esp)
        if "129" in delito_norm or "24-a" in delito_norm:
            if "art. 61, ii, 'f'" in texto_norm or "art. 61, ii, f" in texto_norm or "artigo 61, ii, f" in texto_norm or "agravante das relacoes domesticas" in texto_norm:
                itens.append(
                    ItemCritica(
                        tipo="dialetica",
                        descricao="Bis in idem detectado: aplicação da agravante genérica do art. 61, II, 'f' do CP a tipo penal que já contém a relação doméstica como elementar.",
                        paragrafo_afetado="Dosimetria / 2ª Fase",
                        gravidade="critica",
                        feedback_cirurgico="Afastar a incidência da agravante do art. 61, II, 'f' do CP para evitar indevido bis in idem.",
                    )
                )

        # 3. Trava Súmula 231 do STJ (Pena abaixo do mínimo na 2ª fase)
        if re.search(r"pena.*(?:aqu[eé]m|abaixo)\s+do\s+m[ií]nimo\s+legal", texto_norm):
            itens.append(
                ItemCritica(
                    tipo="dialetica",
                    descricao="Violação da Súmula 231 do STJ: incidência de circunstância atenuante reduzindo a pena abaixo do mínimo legal.",
                    paragrafo_afetado="Dosimetria / 2ª Fase",
                    gravidade="critica",
                    feedback_cirurgico="Observe a Súmula 231 do STJ, mantendo a pena provisória no patamar mínimo legal cominado.",
                )
            )

        # 4. Trava Súmula 444 do STJ (Inquéritos como maus antecedentes)
        if re.search(r"(?:inqu[eé]ritos?|a[cç][oõ]es?\s+penais?\s+em\s+curso).{1,50}(?:maus\s+antecedentes|exaspera)", texto_norm):
            itens.append(
                ItemCritica(
                    tipo="dialetica",
                    descricao="Violação da Súmula 444 do STJ: utilização de inquéritos policiais ou ações penais em curso para agravar a pena-base a título de maus antecedentes.",
                    paragrafo_afetado="Dosimetria / 1ª Fase",
                    gravidade="critica",
                    feedback_cirurgico="Aplique a Súmula 444 do STJ, considerando o réu tecnicamente primário e com bons antecedentes.",
                )
            )

        return itens

    def _crivo_dispositivo(
        self,
        texto: str,
        dossie: Any,
    ) -> List[ItemCritica]:
        """Audita presença de comando absolutório/condenatório, art. 21 LMP e Tema 983 STJ."""
        itens: List[ItemCritica] = []
        texto_norm = _remover_acentos(texto)

        # 1. Comando decisório
        if not ("condenar" in texto_norm or "condeno" in texto_norm or "absolver" in texto_norm or "absolvo" in texto_norm):
            itens.append(
                ItemCritica(
                    tipo="dialetica",
                    descricao="Omissão do comando judicial principal no dispositivo (CONDENAR ou ABSOLVER).",
                    paragrafo_afetado="Dispositivo",
                    gravidade="critica",
                    feedback_cirurgico="Insira expressamente o provimento jurisdicional (CONDENAR ou ABSOLVER o acusado).",
                )
            )

        # 2. Comunicação à vítima (Art. 21 da Lei nº 11.340/2006)
        if not ("art. 21" in texto_norm or "artigo 21" in texto_norm or "intimacao da vitima" in texto_norm or "notificacao da vitima" in texto_norm or "intime-se a ofendida" in texto_norm or "comunicacao a vitima" in texto_norm):
            itens.append(
                ItemCritica(
                    tipo="dialetica",
                    descricao="Omissão da determinação de intimação pessoal da ofendida quanto aos atos processuais (Art. 21 da Lei nº 11.340/2006).",
                    paragrafo_afetado="Dispositivo",
                    gravidade="moderada",
                    feedback_cirurgico="Inclua determinação de intimação da ofendida acerca da sentença, nos termos do art. 21 da Lei nº 11.340/2006.",
                )
            )

        return itens

    # --------------------------------------------------------------------------
    # Tier 1: LLM Inference & Resilient Repair Ladder
    # --------------------------------------------------------------------------

    def _avaliar_llm(
        self,
        rascunho: Any,
        dossie: Any,
        matriz_oral: Optional[MatrizCorroboracaoOral],
    ) -> Optional[RelatorioCriticaAdversarial]:
        """
        Executa chamada ao Ollama com extração de JSON via ladder de 4 estágios.
        Retorna RelatorioCriticaAdversarial ou None se falhar.
        """
        prompt = self._construir_prompt_critica(rascunho, dossie, matriz_oral)
        payload = {
            "model": self.ollama_model,
            "prompt": prompt,
            "system": SISTEMA_ADVERSARIAL_CRITIC,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                **({"num_ctx": int(self.num_ctx)} if self.num_ctx else {}),
            },
        }

        try:
            from app.core.config import load_settings
            from app.core.envio_externo import preparar_prompt_ollama, restaurar_resposta_ollama

            preparado = preparar_prompt_ollama(self.ollama_url, load_settings(), prompt)
            if preparado is None:
                logger.warning("Crítico remoto bloqueado: operação sem autorização ou pseudonimização incompleta.")
                return None
            payload["prompt"], mapa = preparado
            # O cliente não encaminha autos pelo proxy do sistema.
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                resp = client.post(f"{self.ollama_url}/api/generate", json=payload)
                if resp.status_code != 200:
                    logger.warning(f"Ollama retornou HTTP {resp.status_code}")
                    return None
                raw_response = resp.json().get("response", "").strip()
        except Exception as err:
            logger.warning(f"Erro de conexão com Ollama: {err}")
            return None

        return self._reparar_e_parsear_json(restaurar_resposta_ollama(raw_response, mapa))

    def _reparar_e_parsear_json(self, raw_text: str) -> Optional[RelatorioCriticaAdversarial]:
        """
        Ladder de recuperação em 4 estágios:
        1. json.loads direto
        2. Remoção de blocos markdown (```json ... ```)
        3. Regex de chaves mais externas
        4. Validação e normalização Pydantic
        """
        if not raw_text:
            return None

        candidato = raw_text.strip()

        # Estágio 2: Strip markdown
        m_md = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", candidato)
        if m_md:
            candidato = m_md.group(1).strip()

        # Estágio 1 ou pós-strip
        parsed_dict = None
        try:
            parsed_dict = json.loads(candidato)
        except Exception:
            # Estágio 3: Encontrar outermost {...}
            m_brace = re.search(r"\{[\s\S]*\}", candidato)
            if m_brace:
                try:
                    parsed_dict = json.loads(m_brace.group(0))
                except Exception:
                    parsed_dict = None

        if not parsed_dict or not isinstance(parsed_dict, dict):
            return None

        # Estágio 4: Validação Pydantic
        try:
            relatorio = RelatorioCriticaAdversarial.model_validate(parsed_dict)
            # Revalida cálculo do score para garantir invariante do Agente 7
            score_calculado, aprovado_calculado = self._calcular_score_e_status(relatorio.itens)
            relatorio.score_dialetico = score_calculado
            relatorio.aprovado = aprovado_calculado
            return relatorio
        except Exception as val_err:
            logger.warning(f"Erro de validação Pydantic no retorno do Ollama: {type(val_err).__name__}")
            return None

    def _construir_prompt_critica(
        self,
        rascunho: Any,
        dossie: Any,
        matriz_oral: Optional[MatrizCorroboracaoOral],
    ) -> str:
        secao_str = rascunho.secao.value.upper() if hasattr(rascunho, "secao") else "MÉRITO"
        texto_minuta = rascunho.texto_redigido if hasattr(rascunho, "texto_redigido") else str(rascunho)

        teses = getattr(dossie, "teses_defesa", []) or []
        teses_str = "\n".join(f"- {t}" for t in teses) if teses else "- Nenhuma tese formal informada."

        fatos = getattr(dossie, "fatos_delimitados", []) or []
        fatos_str = "\n".join(f"- {f}" for f in fatos) if fatos else "- Fatos da denúncia e instrução processual."

        oral_str = matriz_oral.fundamentacao_sintese if matriz_oral else "Sem matriz oral especificada."

        return (
            f"=== [AUDITORIA ADVERSARIAL: SEÇÃO {secao_str}] ===\n\n"
            f"UNIVERSO FECHADO DO DOSSIÊ DE RESTRIÇÃO:\n"
            f"Fatos Delimitados:\n{fatos_str}\n\n"
            f"Teses Defensivas de Apreciação Obrigatória (Art. 381, III, CPP):\n{teses_str}\n\n"
            f"Síntese da Prova Oral e Pericial:\n{oral_str}\n\n"
            f"TEXTO DA MINUTA SUBMETIDA AO CRIVO:\n"
            f"--------------------------------------------------\n"
            f"{texto_minuta}\n"
            f"--------------------------------------------------\n\n"
            f"Emita exclusivamente o JSON de crítica adversarial conforme o schema especificado:"
        )

    # --------------------------------------------------------------------------
    # Scoring & Parecer
    # --------------------------------------------------------------------------

    def _calcular_score_e_status(self, itens: List[ItemCritica]) -> Tuple[float, bool]:
        """
        Fórmula Dialética Formal:
        score_dialetico = max(0.0, 100.0 - (30.0 * N_critica + 15.0 * N_moderada + 5.0 * N_sugestao))
        aprovado = (N_critica == 0) and (score_dialetico >= min_score_aprovacao)
        """
        n_critica = sum(1 for i in itens if i.gravidade == GravidadeCritica.CRITICA.value)
        n_moderada = sum(1 for i in itens if i.gravidade == GravidadeCritica.MODERADA.value)
        n_sugestao = sum(1 for i in itens if i.gravidade == GravidadeCritica.SUGESTAO.value)

        deducoes = (30.0 * n_critica) + (15.0 * n_moderada) + (5.0 * n_sugestao)
        score = max(0.0, min(100.0, round(100.0 - deducoes, 1)))

        aprovado = (n_critica == 0) and (score >= self.min_score_aprovacao)
        return score, aprovado

    def _gerar_parecer_sintetico(
        self,
        aprovado: bool,
        score: float,
        itens: List[ItemCritica],
        secao: Any,
    ) -> str:
        secao_nome = getattr(secao, "value", str(secao)).upper()
        if aprovado:
            if not itens:
                return (
                    f"Minuta da seção {secao_nome} aprovada com louvor (Score {score:.1f}/100.0). "
                    f"Plena conformidade dialética, apreciação das teses e respeito ao Protocolo CNJ nº 492/2023."
                )
            return (
                f"Minuta da seção {secao_nome} aprovada com ressalvas leves (Score {score:.1f}/100.0). "
                f"Inexistência de vícios críticos, contendo sugestões pontuais de aprimoramento redacional."
            )
        else:
            qtd_criticas = sum(1 for i in itens if i.gravidade == GravidadeCritica.CRITICA.value)
            return (
                f"Minuta da seção {secao_nome} REPROVADA (Score {score:.1f}/100.0) com {qtd_criticas} falha(s) crítica(s). "
                f"Necessária auto-correção cirúrgica imediata com base nos feedbacks fornecidos."
            )


# ==============================================================================
# 5. Funções de Top-Level & Gating Adapter para Sectional Pipeline
# ==============================================================================


def criticar_secao(
    rascunho: Any,
    dossie: Any,
    use_ollama: bool = False,
    matriz_oral: Optional[MatrizCorroboracaoOral] = None,
) -> RelatorioCriticaAdversarial:
    """Função de conveniência para escrutínio seccional imediato."""
    critic = AdversarialJudicialCritic(use_ollama=use_ollama)
    return critic.criticar_secao(rascunho, dossie, matriz_oral=matriz_oral)


def criar_gating_validator_adversarial(
    critic: Optional[AdversarialJudicialCritic] = None,
    combinar_com_default: bool = True,
    matriz_oral: Optional[MatrizCorroboracaoOral] = None,
) -> Callable[[Any, Any, Any], Any]:
    """
    Adaptador oficial para o SectionalPipelineEngine:
    Retorna uma função (secao, rascunho, dossie) -> ResultadoGating compatível
    com o in-flight gating da máquina de estados M1.
    """
    from app.generation.sectional_pipeline import (
        ResultadoGating,
        SecaoRascunho,
        SecaoTipo,
        default_in_flight_gating_validator,
    )

    critic_instance = critic or AdversarialJudicialCritic(use_ollama=False)

    def gating_validator(
        secao: SecaoTipo,
        rascunho: SecaoRascunho,
        dossie: Any,
    ) -> ResultadoGating:
        # 1. Se configurado, executa validador padrão de base (Camada 1 folhas, etc.)
        resultado_default: Optional[ResultadoGating] = None
        if combinar_com_default:
            resultado_default = default_in_flight_gating_validator(secao, rascunho, dossie)

        # 2. Executa escrutínio adversarial do Agente 7
        relatorio_adversarial = critic_instance.criticar_secao(
            rascunho=rascunho,
            dossie=dossie,
            matriz_oral=matriz_oral,
        )
        resultado_adv: ResultadoGating = relatorio_adversarial.to_resultado_gating(secao)

        if not combinar_com_default or resultado_default is None:
            return resultado_adv

        # Combinação harmoniosa dos dois crivos
        erros_totais = list(resultado_default.erros) + list(resultado_adv.erros)
        feedbacks_totais = list(resultado_default.feedbacks_cirurgicos) + list(
            resultado_adv.feedbacks_cirurgicos
        )
        aprovado_total = resultado_default.aprovado and resultado_adv.aprovado
        score_combinado = min(resultado_default.score, resultado_adv.score)

        return ResultadoGating(
            secao=secao,
            aprovado=aprovado_total,
            score=score_combinado,
            erros=erros_totais,
            feedbacks_cirurgicos=feedbacks_totais,
            acionar_fallback=False,
        )

    return gating_validator
