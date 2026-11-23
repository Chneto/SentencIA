# -*- coding: utf-8 -*-
"""
Agente 9 — Auditor Anti-Alucinação em Quatro Camadas (M3: auditoria seccional granular).

Este módulo implementa o firewall anti-alucinação do SentencIA em duas frentes
retrocompatíveis:

1. ``auditar_sentenca`` — auditoria MONOLÍTICA histórica (Milestone 1/Gate 4), com
   assinatura e retorno preservados byte a byte em relação à versão anterior
   (``RelatorioAuditoriaSentenca``). Nenhum teste existente pode regredir.

2. ``auditar_secao`` + wrappers ``auditar_secao_relatorio/preliminares/merito/
   dosimetria/dispositivo`` (Milestone 3) — auditoria GRANULAR por seção, reutilizando
   os mesmos helpers extraídos da auditoria monolítica, mas aplicando apenas as camadas
   pertinentes ao estágio seccional corrente.

Semântica das quatro camadas (idêntica nas duas frentes):

- **Camada 1 — Ancoragem Probatória de IDs/Folhas:** todo token ``ID-*`` citado no texto
  deve existir fisicamente no índice de peças dos autos ou no catálogo de mídias
  audiovisuais (PJe Mídias). Na auditoria seccional usa-se o padrão amplo
  ``ID-[A-Za-z0-9_-]+`` (o mesmo do in-flight gating do M1), de modo a capturar
  prefixos forjados como ``ID-DOC-9999``/``ID-FAKE-8888``.

- **Camada 2 — Blindagem Jurisprudencial (Base Fechada):** toda Súmula, Tema
  Repetitivo/Repercussão Geral, Ação de Controle Concentrado (ADC/ADI/ADPF), Enunciado
  FONAVID, Resolução CNJ, Apelação Criminal TJRN e acórdão avulso (REsp/HC/RHC/AgRg/
  AREsp/ARE/RE) citado deve constar da Base Fechada Autorizada do gabinete
  (``get_all_knowledge_base``). Citação externa é bloqueada por definição — não há
  acesso a repositórios jurídicos em tempo de execução. Fundamento: Agente 5 (base
  fechada) e Critério de Qualidade do Agente 9 ("proibição irrestrita de citações a
  julgados ou súmulas inexistentes").

- **Camada 3 — Completude de Enfrentamento Defensivo (art. 381, III, CPP; art. 93, IX,
  CF):** 100% das teses defensivas aplicáveis à seção devem ser enfrentadas. Na
  auditoria seccional a aplicabilidade é filtrada por tipo: PRELIMINARES exige as teses
  PRELIMINARES; MÉRITO exige as teses de MÉRITO; DOSIMETRIA exige apenas as teses
  SUBSIDIÁRIAS (pedidos dosimétricos); RELATÓRIO e DISPOSITIVO não exigem (o relatório é
  narrativo e o dispositivo é executório — a completude global é conferida quando
  ``texto_sentenca_completo`` é fornecido). Fórmulas evasivas de não enfrentamento
  ("deixo de analisar", "sem necessidade de enfrentar") também reprovam a seção.

- **Camada 4 — Conformidade Formal e Normativa:** art. 381, I a III, do CPP (estrutura
  canônica integral quando houver texto completo); art. 21 da Lei nº 11.340/2006
  (intimação pessoal da ofendida, exigível no DISPOSITIVO); LC 95/98 (grafia de
  artigos); padrão brasileiro de datas DD/MM/AAAA ou por extenso (ISO 8601 é vedado na
  narrativa); ABNT NBR 10520 (citação longa com recuo de 4 espaços não pode vir entre
  aspas externas); diretrizes CNJ/AMB e Resolução CNJ nº 492/2023 (vedação da "legítima
  defesa da honra", ADPF 779/STF, e de solecismos); e, **apenas na DOSIMETRIA**, as 8
  circunstâncias judiciais do art. 59 do CP enunciadas e expressamente valoradas.

O score seccional (0.0 a 100.0) é obtido por desconto determinístico e multiplicativo
linear: 25 por ID inexistente, 25 por citação não autorizada, 20 por tese omitida,
20 por omissão dos vetores do art. 59, 15 por omissão do art. 21 da LMP, 15 por fórmula
evasiva, 15 por marcador ``[COMPLETAR PELO JUÍZO ...]`` pendente, 10 por violação de
datas/ABNT/estilo, 5 por violação da LC 95/98 e 25 por estrutura canônica ausente (somente
quando o texto integral é fornecido). A seção é aprovada apenas com score 100.0 e todas as
conformidades aplicáveis satisfeitas.

v2.0.0 (auditoria honesta):

- **Toda reprovação desconta** (inclusive o art. 381 do CPP na auditoria monolítica), de modo
  que ``aprovada_sem_alucinacao`` é verdadeiro se, e somente se, o score é 100.0.
- ``score_forma`` isola as verificações formais (Camada 4); ``score_confiabilidade`` soma as
  verificações formais e as de conteúdo.
- **Verificação de conteúdo**: marcadores ``[COMPLETAR PELO JUÍZO: ...]`` pendentes reprovam a
  minuta; quando ``dosimetria_dados`` é fornecido, as penas definitivas citadas no DISPOSITIVO
  são confrontadas com as calculadas pela calculadora determinística (``penas_divergentes``).
- Seções substituídas por modelo determinístico (``secoes_fallback``) são sinalizadas no laudo.
- Cabeçalhos canônicos aceitam ``–``, ``-`` e ``—``.
- Não há IDs de mídia "demo" fixos: o universo da Camada 1 é formado pelas peças, pelas mídias
  informadas e pelas mídias expressamente registradas no texto dos autos (ex.: "Mídia ID-...").
- O índice da Base Fechada é invalidado automaticamente quando o acervo muda
  (``knowledge_base.versao_acervo()``); acórdãos e apelações casam por número EXATO.
"""

import re
import threading
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from app.core.text_utils import normalizar, remover_acentos
from app.generation.adversarial_critic import repudio_explicito_honra, tese_vedada_honra, violacao_vedacao_honra
from app.generation.date_formatter import numero_por_extenso
from app.generation.dispositivo import ComandosDispositivo, construir_comandos_dispositivo
from app.legal_engine.dosimetria_calculator import formatar_especie_pena
from app.ingestion.laya_classifier import PecaClassificada, TeseDefensivaItem
from app.knowledge.knowledge_base import get_all_knowledge_base, versao_acervo


# ==============================================================================
# 1. CONTRATOS PYDANTIC (M1/Gate 4 — monolítico; M3 — seccional granular)
# ==============================================================================


class RelatorioAuditoriaSentenca(BaseModel):
    """Laudo monolítico do Auditor Anti-Alucinação (4 camadas sobre a sentença integral)."""

    aprovada_sem_alucinacao: bool
    score_confiabilidade: float
    ids_citados_verificados: List[str]
    ids_inexistentes: List[str]
    citacoes_juridicas_verificadas: List[str]
    citacoes_nao_autorizadas: List[str]
    teses_defesa_enfrentadas: List[str]
    teses_omitidas: List[str]
    conformidade_art381_cpp: bool
    conformidade_art21_lmp: bool
    conformidade_lc95_98: bool
    conformidade_datas_padrao_br: bool = True
    conformidade_abnt_citacoes: bool = True
    conformidade_estilo_cnj_amb: bool = True
    conformidade_oito_circunstancias_art59: bool = True
    detalhes_camadas: List[Dict[str, str]]
    # --- v2.0.0: auditoria de conteúdo e sinalização honesta ---
    score_forma: float = 100.0
    verificacoes_conteudo: List[Dict[str, Any]] = Field(default_factory=list)
    secoes_fallback: List[str] = Field(default_factory=list)
    penas_divergentes: List[str] = Field(default_factory=list)
    marcadores_pendentes: List[str] = Field(default_factory=list)
    conformidade_marcadores_pendentes: bool = True
    conformidade_penas_dosimetria: bool = True


class RelatorioAuditoriaSecao(BaseModel):
    """
    Laudo granular do Auditor Anti-Alucinação para UMA seção do pipeline (M3).

    ``secao`` é o identificador canônico (relatorio|preliminares|merito|dosimetria|
    dispositivo). Os campos ``conformidade_*`` marcam ``True`` quando a verificação é
    aplicável e satisfeita OU quando não é aplicável àquela seção (ex.: os 8 vetores do
    art. 59 só são exigíveis na dosimetria). Os detalhes listam as camadas executadas
    para telemetria e depuração.
    """

    secao: str
    aprovado: bool
    score: float = Field(ge=0.0, le=100.0)
    erros: List[str] = Field(default_factory=list)
    feedbacks_cirurgicos: List[str] = Field(default_factory=list)
    ids_inexistentes: List[str] = Field(default_factory=list)
    ids_citados_verificados: List[str] = Field(default_factory=list)
    citacoes_nao_autorizadas: List[str] = Field(default_factory=list)
    citacoes_juridicas_verificadas: List[str] = Field(default_factory=list)
    teses_omitidas: List[str] = Field(default_factory=list)
    teses_defesa_enfrentadas: List[str] = Field(default_factory=list)
    conformidade_art381_cpp: bool = True
    conformidade_art21_lmp: bool = True
    conformidade_lc95_98: bool = True
    conformidade_datas_padrao_br: bool = True
    conformidade_abnt_citacoes: bool = True
    conformidade_estilo_cnj_amb: bool = True
    conformidade_oito_circunstancias_art59: bool = True
    conformidade_fundamentacao_evasiva: bool = True
    marcadores_pendentes: List[str] = Field(default_factory=list)
    conformidade_marcadores_pendentes: bool = True
    detalhes_camadas: List[Dict[str, str]] = Field(default_factory=list)


# ==============================================================================
# 2. CONSTANTES E HELPERS GENÉRICOS
# ==============================================================================

#: Seções canônicas do pipeline (M1).
SECOES_VALIDAS = ("relatorio", "preliminares", "merito", "dosimetria", "dispositivo")

#: Padrão histórico de IDs usado pelo auditor monolítico (suporta 'ID-123456', 'ID 123456' e IDs de mídia compostos).
_PADRAO_ID_SENTENCA = re.compile(
    r"\bID(?:[-\s](?:MIDIA-(?:[A-Z0-9]+-)*\d+|MIDIA-[A-Z0-9_-]+|CAPA-PJE|PDF-[A-Z0-9_-]+|\d+))\b"
)

#: Padrão amplo de IDs usado na auditoria seccional (equivalente ao do gating M1).
_PADRAO_ID_SECAO = re.compile(r"\bID-[A-Za-z0-9_-]+\b")


def _normalizar_id_citado(token: str) -> str:
    """Normaliza 'ID 120503000' -> 'ID-120503000' para confronto unificado contra o índice."""
    t = re.sub(r"\s+", "-", token.strip())
    return t

_MAPA_SECAO_ALIASES = {
    "relatorio": "relatorio",
    "relatório": "relatorio",
    "preliminares": "preliminares",
    "preliminar": "preliminares",
    "merito": "merito",
    "mérito": "merito",
    "dosimetria": "dosimetria",
    "dispositivo": "dispositivo",
}

_FORMULAS_EVASIVAS = (
    "deixo de analisar",
    "deixo de apreciar",
    "deixo de me manifestar",
    "nao analisada",
    "sem necessidade de enfrentar",
    "sem necessidade de apreciar",
    "sem apreciacao",
    "dispenso a analise",
    "deixo de enfrentar",
)


def _normalizar_secao(secao: Any) -> str:
    """Normaliza o identificador de seção (Enum SecaoTipo, str com acento/alias) para str canônica."""
    valor = getattr(secao, "value", secao)
    valor = str(valor or "").strip().lower()
    normalizado = _MAPA_SECAO_ALIASES.get(valor)
    if normalizado is None:
        raise ValueError(
            f"Seção inválida: {secao!r}. Valores aceitos: {', '.join(SECOES_VALIDAS)}."
        )
    return normalizado


#: Alias local mantido por compatibilidade (fonte única: ``app.core.text_utils``).
_remover_acentos = remover_acentos


#: Mídias expressamente registradas no texto dos autos (ex.: termo de audiência:
#: "Mídia ID-4100301", "mídia nº ID 123", "Mídia (ID-MIDIA-...)").
_PADRAO_MIDIA_REGISTRADA = re.compile(
    r"\bm[íi]dias?\s*(?:audiovisua(?:l|is)\s*)?(?:n[º°\.]?\s*)?[\(\[]?\s*(ID[-\s][A-Za-z0-9_-]+)",
    flags=re.IGNORECASE,
)


def _conteudo_peca(peca: Any) -> str:
    if isinstance(peca, str):
        return ""
    if isinstance(peca, dict):
        return str(peca.get("conteudo") or "")
    return str(getattr(peca, "conteudo", "") or "")


def _ids_midia_registrados_nos_autos(pecas_autos: Optional[Iterable[Any]]) -> set:
    """
    IDs de mídia certificados no próprio texto dos autos (ex.: termo de audiência que lista
    "Mídia ID-4100301, duração 06min42s"). São âncoras verificáveis — constam dos autos — e
    substituem a antiga lista fixa de IDs do caso demonstrativo.
    """
    ids: set = set()
    for peca in pecas_autos or ():
        conteudo = _conteudo_peca(peca)
        if not conteudo:
            continue
        for m in _PADRAO_MIDIA_REGISTRADA.finditer(conteudo):
            token = _normalizar_id_citado(m.group(1)).rstrip("-_")
            if token:
                ids.add(token)
    return ids


def _coletar_ids_validos(
    pecas_autos: Optional[Iterable[Any]],
    ids_midias: Optional[Iterable[Any]],
) -> set:
    """
    Monta o universo fechado de IDs autorizados (Camada 1).

    Aceita tanto objetos ``PecaClassificada``/dicionários com ``doc_id`` quanto strings
    contendo diretamente o ID autorizado — flexibilidade necessária para o adaptador do
    in-flight gating (M1), que opera apenas com o dossiê seccional. Inclui também as mídias
    expressamente registradas no texto dos autos.
    """
    validos: set = set()
    pecas_lista = list(pecas_autos or ())
    for peca in pecas_lista:
        if isinstance(peca, str):
            token = peca.strip()
        elif isinstance(peca, dict):
            token = str(peca.get("doc_id") or "").strip()
        else:
            token = str(getattr(peca, "doc_id", "") or "").strip()
        if token:
            validos.add(token)
    for midia in ids_midias or ():
        token = str(midia or "").strip()
        if token:
            validos.add(token)
    validos |= _ids_midia_registrados_nos_autos(pecas_lista)
    return validos


def _auditar_ids(
    texto: str,
    ids_validos: Iterable[str],
    padrao: "re.Pattern[str]" = _PADRAO_ID_SECAO,
    case_insensitive: bool = True,
) -> Tuple[List[str], List[str]]:
    """Extrai e confronta os IDs citados contra o universo fechado (Camada 1)."""
    ids_encontrados = sorted(set(padrao.findall(texto or "")))
    validos = set(ids_validos)
    if not case_insensitive:
        ids_ok = [i for i in ids_encontrados if i in validos]
        ids_falsos = [i for i in ids_encontrados if i not in validos]
        return ids_ok, ids_falsos

    validos_upper = {v.upper(): v for v in validos}
    ids_ok = [i for i in ids_encontrados if i.upper() in validos_upper]
    ids_falsos = [i for i in ids_encontrados if i.upper() not in validos_upper]
    return ids_ok, ids_falsos


# ==============================================================================
# 3. CAMADA 2 — ÍNDICE DA BASE FECHADA E CONFRONTO JURISPRUDENCIAL
# ==============================================================================


@dataclass
class _IndiceBaseFechada:
    """Conjuntos normalizados extraídos da Base Fechada Autorizada do gabinete."""

    refs_validas: set
    sumulas_stj: set
    sumulas_stf: set
    sumulas_vinculantes: set
    temas_stj: set
    temas_stf: set
    acoes_stf: set
    fonavid: set
    cnj: set
    tjrn_numeros: set
    acordaos: set
    #: Números de acórdãos reduzidos a dígitos (casamento EXATO, nunca por substring).
    acordaos_digitos: set = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.acordaos_digitos is None:
            self.acordaos_digitos = {
                _so_digitos(a) for a in self.acordaos if len(_so_digitos(a)) >= 4
            }


def _so_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


#: Números processuais/de acórdão dentro de uma referência (ex.: "0807111-11.2025.8.20.0001").
_PADRAO_NUMERO_REFERENCIA = re.compile(r"\d[\d\.\-/]{4,}\d")

_INDICE_CACHE: Dict[str, Any] = {"versao": None, "indice": None}
_INDICE_LOCK = threading.Lock()


def _construir_indice_base_fechada() -> _IndiceBaseFechada:
    """
    Constrói (e memoriza) o índice da Base Fechada Autorizada (Agente 5).

    O índice é memorizado por ``versao_acervo()``: enquanto o acervo (oficial + precedentes
    do gabinete) não muda, a auditoria seccional das 5 seções reutiliza o mesmo índice; quando
    o gabinete cadastra um precedente (ou o arquivo é alterado), o índice é reconstruído.
    """
    versao = versao_acervo()
    with _INDICE_LOCK:
        if _INDICE_CACHE["indice"] is not None and _INDICE_CACHE["versao"] == versao:
            return _INDICE_CACHE["indice"]
    indice = _indexar_base_fechada()
    with _INDICE_LOCK:
        _INDICE_CACHE["versao"] = versao
        _INDICE_CACHE["indice"] = indice
    return indice


def _indexar_base_fechada() -> _IndiceBaseFechada:
    """Indexa o acervo atual da Base Fechada (sem cache)."""
    kb = get_all_knowledge_base()
    all_kb_items = kb.get("sumulas_e_temas", []) + kb.get("precedentes_gabinete", [])

    refs_validas: set = set()
    valid_sumulas_stj: set = set()
    valid_sumulas_stf: set = set()
    valid_sumulas_vinculantes: set = set()
    valid_temas_stj: set = set()
    valid_temas_stf: set = set()
    valid_acoes_stf: set = set()
    valid_fonavid: set = set()
    valid_cnj: set = set()
    valid_tjrn_numeros: set = set()
    valid_acordaos: set = set()

    for item in all_kb_items:
        ref = item.get("referencia", "").strip()
        ref_id = item.get("id", "").strip()
        tipo = item.get("tipo", "").strip()
        tribunal = item.get("tribunal", "").strip()
        fonte = item.get("fonte_oficial", "")
        texto = item.get("texto_literal", "")

        if ref:
            refs_validas.add(ref)
        if ref_id:
            refs_validas.add(ref_id)

        # Súmula Vinculante
        if "SUMULA_VINCULANTE" in tipo or "Súmula Vinculante" in ref:
            m = re.search(r"\d+", ref)
            if m:
                valid_sumulas_vinculantes.add(int(m.group(0)))
        elif "SUMULA" in tipo or "Súmula" in ref:
            m = re.search(r"\d+", ref)
            if m:
                num = int(m.group(0))
                if tribunal == "STF" or "/STF" in ref:
                    valid_sumulas_stf.add(num)
                else:
                    valid_sumulas_stj.add(num)

        # Temas Repetitivos (STJ) e Repercussão Geral (STF)
        if "REPETITIVO" in tipo or "Tema Repetitivo" in ref:
            m = re.search(r"[\d\.]+", ref)
            if m:
                valid_temas_stj.add(int(m.group(0).replace(".", "")))
        elif "REPERCUSSAO_GERAL" in tipo or ("Tema" in ref and (tribunal == "STF" or "/STF" in ref)):
            m = re.search(r"[\d\.]+", ref)
            if m:
                valid_temas_stf.add(int(m.group(0).replace(".", "")))

        # Ações do STF (ADC, ADI, ADPF, ACO)
        for sigla in ("ADC", "ADI", "ADPF", "ACO"):
            if sigla in ref or sigla in ref_id:
                m = re.search(rf"\b{sigla}\s*([\d\.]+)", f"{ref} {ref_id}")
                if m:
                    valid_acoes_stf.add((sigla, int(m.group(1).replace(".", ""))))

        # Enunciados FONAVID
        if "FONAVID" in ref or "FONAVID" in tipo or tribunal == "FONAVID":
            m = re.search(r"\d+", ref)
            if m:
                valid_fonavid.add(int(m.group(0)))

        # Normas CNJ
        if "CNJ" in ref or "CNJ" in tribunal:
            m = re.search(r"\b(\d+)(?:/\d+)?\b", ref)
            if m:
                valid_cnj.add(int(m.group(1)))

        # Precedentes TJRN
        if tribunal == "TJRN" or "TJRN" in ref or "Apelação Criminal" in ref:
            for num_ref in _PADRAO_NUMERO_REFERENCIA.findall(ref):
                num_clean = _so_digitos(num_ref)
                if len(num_clean) >= 6:
                    valid_tjrn_numeros.add(num_clean)

        # Acórdãos avulsos constantes na referência/fundamentação/fonte oficial
        acordao_extract_pat = r"\b(?:REsp|HC|RHC|AgRg|AREsp|ARE|RE|Recurso\s+Especial|Recurso\s+Extraordinário|Habeas\s+Corpus|Agravo\s+em\s+Recurso\s+Especial|Agravo\s+em\s+Recurso\s+Extraordinário)\s+(?:no\s+(?:AREsp|REsp|HC|RHC|RE|ARE)\s+)?(?:Repetitivo\s+)?(?:n[º°\.]?\s+)?([\d\.\-/A-Z]+)\b"
        for src_txt in (ref, fonte, texto):
            for r in re.findall(acordao_extract_pat, src_txt, flags=re.IGNORECASE):
                valid_acordaos.add(r.strip())
            for r in re.findall(r"\b(?:REsp|HC|RHC|AgRg|AREsp|ARE|RE)\s+(?:no\s+(?:AREsp|REsp|HC|RHC|RE|ARE)\s+)?(?:Repetitivo\s+)?(?:n[º°\.]?\s+)?[\d\.\-/A-Z]+\b", src_txt):
                valid_acordaos.add(re.sub(r"\s+", " ", r.strip()))

    return _IndiceBaseFechada(
        refs_validas=refs_validas,
        sumulas_stj=valid_sumulas_stj,
        sumulas_stf=valid_sumulas_stf,
        sumulas_vinculantes=valid_sumulas_vinculantes,
        temas_stj=valid_temas_stj,
        temas_stf=valid_temas_stf,
        acoes_stf=valid_acoes_stf,
        fonavid=valid_fonavid,
        cnj=valid_cnj,
        tjrn_numeros=valid_tjrn_numeros,
        acordaos=valid_acordaos,
    )


def _limpar_cache_base_fechada() -> None:
    """Invalida o cache do índice da Base Fechada (útil em testes de hot-reload do acervo)."""
    with _INDICE_LOCK:
        _INDICE_CACHE["versao"] = None
        _INDICE_CACHE["indice"] = None


# Compatibilidade com o antigo ``lru_cache`` (``_construir_indice_base_fechada.cache_clear()``).
_construir_indice_base_fechada.cache_clear = _limpar_cache_base_fechada  # type: ignore[attr-defined]


def _auditar_citacoes_juridicas(
    texto_sentenca: str,
    indice: _IndiceBaseFechada,
) -> Tuple[List[str], List[str]]:
    """
    Confronta todas as citações jurídicas do texto contra a Base Fechada (Camada 2).

    Retorna ``(citacoes_verificadas, citacoes_nao_autorizadas)``. A lógica é idêntica à
    auditoria monolítica histórica, extraída para reutilização na auditoria seccional.
    """
    refs_validas = indice.refs_validas
    valid_sumulas_stj = indice.sumulas_stj
    valid_sumulas_stf = indice.sumulas_stf
    valid_sumulas_vinculantes = indice.sumulas_vinculantes
    valid_temas_stj = indice.temas_stj
    valid_temas_stf = indice.temas_stf
    valid_acoes_stf = indice.acoes_stf
    valid_fonavid = indice.fonavid
    valid_cnj = indice.cnj
    valid_tjrn_numeros = indice.tjrn_numeros

    citacoes_ok: List[str] = []
    citacoes_invalidas: List[str] = []

    # 1. Auditoria de Súmulas Vinculantes
    for m in re.finditer(r"\bSúmula\s+Vinculante\s+(?:nº?\s+)?(\d+)(?:/(?:STF|DF)|\s+d[oe]\s+STF)?\b", texto_sentenca, flags=re.IGNORECASE):
        num_str = m.group(1)
        full_cit = m.group(0)
        if int(num_str) in valid_sumulas_vinculantes:
            citacoes_ok.append(full_cit)
        else:
            citacoes_invalidas.append(full_cit)

    # 2. Auditoria de Súmulas ordinárias (STJ ou STF)
    for m in re.finditer(r"\bSúmula\s+(?:nº?\s+)?(\d+)(?:\s*(?:/|\s+d[oe]\s+)(ST[JF]))?\b", texto_sentenca, flags=re.IGNORECASE):
        start = m.start()
        prefix = texto_sentenca[max(0, start - 15):start].lower()
        if "vinculante" in prefix:
            continue
        num_str = m.group(1)
        trib = (m.group(2) or "").upper()
        full_cit = m.group(0)
        num = int(num_str)
        if trib == "STF":
            is_ok = num in valid_sumulas_stf
        elif trib == "STJ":
            is_ok = num in valid_sumulas_stj
        else:
            is_ok = num in valid_sumulas_stj or num in valid_sumulas_stf
        if is_ok:
            citacoes_ok.append(full_cit)
        else:
            citacoes_invalidas.append(full_cit)

    # 3. Auditoria de Temas (Repetitivo ou Repercussão Geral)
    for m in re.finditer(r"\bTema(?:\s+(?:Repetitivo|de\s+Repercussão\s+Geral))?\s+(?:n[º°\.]?\s+)?(\d+(?:\.\d+)?)(?:\s*(?:/|\s+d[oe]\s+)(ST[JF]))?\b", texto_sentenca, flags=re.IGNORECASE):
        num_str = m.group(1).replace(".", "")
        trib = (m.group(2) or "").upper()
        full_cit = m.group(0)
        num = int(num_str)
        if trib == "STF":
            is_ok = num in valid_temas_stf
        elif trib == "STJ":
            is_ok = num in valid_temas_stj
        else:
            is_ok = num in valid_temas_stj or num in valid_temas_stf
        if is_ok:
            citacoes_ok.append(full_cit)
        else:
            citacoes_invalidas.append(full_cit)

    # 4. Auditoria de Ações de Controle Concentrado STF (ADC, ADI, ADPF)
    for m in re.finditer(r"\b(ADC|ADI|ADPF)\s+(?:n[º°\.]?\s+)?(\d+(?:\.\d+)?)(?:/(?:STF|DF)|\s+d[oe]\s+STF)?\b", texto_sentenca, flags=re.IGNORECASE):
        sigla = m.group(1).upper()
        num = int(m.group(2).replace(".", ""))
        full_cit = m.group(0)
        if (sigla, num) in valid_acoes_stf:
            citacoes_ok.append(full_cit)
        else:
            citacoes_invalidas.append(full_cit)

    # 5. Auditoria de Enunciados FONAVID
    for m in re.finditer(r"\b(?:Enunciado\s+(?:nº?\s+)?(\d+)(?:[\s/,\-\(]+(?:d[oae]\s+)?FONAVID(?:\)|\b)|\s+d[oae]\s+FONAVID\b)|Enunciado\s+FONAVID\s+(?:nº?\s+)?(\d+)\b)", texto_sentenca, flags=re.IGNORECASE):
        num_str = m.group(1) or m.group(2)
        num = int(num_str)
        full_cit = m.group(0)
        if num in valid_fonavid:
            citacoes_ok.append(full_cit)
        else:
            citacoes_invalidas.append(full_cit)

    # 6. Auditoria de Resoluções do CNJ
    for m in re.finditer(r"\bResolução\s+(?:CNJ\s+)?(?:nº?\s+)?(\d+)(?:/(\d+))?(?:\s+d[oe]\s+CNJ)?\b", texto_sentenca, flags=re.IGNORECASE):
        num = int(m.group(1))
        full_cit = m.group(0)
        if num in valid_cnj:
            citacoes_ok.append(full_cit)
        else:
            citacoes_invalidas.append(full_cit)

    # 7. Auditoria de Precedentes TJRN
    for m in re.finditer(r"\b(?:TJRN\s*[-–]\s*)?Apelação\s+Criminal\s+(?:nº?\s+)?([\d\.\-/]+)(?:\s+d[oe]\s+TJRN)?\b", texto_sentenca, flags=re.IGNORECASE):
        num_clean = _so_digitos(m.group(1))
        full_cit = m.group(0)
        # Casamento EXATO do número (antes: substring, o que aceitava números truncados/forjados).
        if len(num_clean) >= 6 and num_clean in valid_tjrn_numeros:
            citacoes_ok.append(full_cit)
        else:
            citacoes_invalidas.append(full_cit)

    # 8. Auditoria de Acórdãos avulsos (REsp, HC, RHC, AgRg, AREsp, ARE, RE, Recurso Especial, etc.)
    for m in re.finditer(r"\b(REsp|HC|RHC|AgRg|AREsp|ARE|RE|Recurso\s+Especial|Recurso\s+Extraordinário|Habeas\s+Corpus|Agravo\s+em\s+Recurso\s+Especial|Agravo\s+em\s+Recurso\s+Extraordinário)\s+(?:no\s+(?:AREsp|REsp|HC|RHC|RE|ARE)\s+)?(?:n[º°\.]?\s+)?(?:Repetitivo\s+)?([\d\.\-/A-Z]+)\b", texto_sentenca, flags=re.IGNORECASE):
        full_cit = m.group(0).strip()
        num_part = m.group(2)
        clean_cand = _so_digitos(num_part)
        if len(clean_cand) < 4:
            continue
        # Casamento EXATO por número do acórdão (antes: substring de dígitos, que aceitava
        # "REsp 1.675" como se fosse o "REsp 1.675.874/MS").
        match_found = clean_cand in indice.acordaos_digitos
        if match_found or full_cit in refs_validas:
            citacoes_ok.append(full_cit)
        else:
            citacoes_invalidas.append(full_cit)

    citacoes_ok = sorted(set(citacoes_ok))
    citacoes_invalidas = sorted(set(citacoes_invalidas))
    return citacoes_ok, citacoes_invalidas


# ==============================================================================
# 4. CAMADA 4 — CONFORMIDADE FORMAL E NORMATIVA (HELPERS REUTILIZÁVEIS)
# ==============================================================================


#: Cabeçalhos canônicos do art. 381 do CPP; aceitam travessão (–), hífen (-) e travessão longo (—).
_PADROES_CABECALHOS_ART381 = (
    re.compile(r"(?<![A-Za-z])I\s*[-–—]\s*RELAT[ÓO]RIO\b"),
    re.compile(r"(?<![A-Za-z])II\s*[-–—]\s*FUNDAMENTA[ÇC][ÃA]O\b"),
    re.compile(r"(?<![A-Za-z])III\s*[-–—]\s*DISPOSITIVO\b"),
)


def _avaliar_art381_cpp(texto_sentenca: str) -> bool:
    """Verifica a presença dos três blocos canônicos do art. 381, I a III, do CPP."""
    texto = texto_sentenca or ""
    return all(p.search(texto) for p in _PADROES_CABECALHOS_ART381)


# ------------------------------------------------------------------------------
# Verificações de CONTEÚDO (v2.0.0)
# ------------------------------------------------------------------------------

#: Marcador de complementação obrigatória pelo magistrado (gerado pelo motor quando falta dado).
_PADRAO_MARCADOR_COMPLETAR = re.compile(
    r"\[\s*COMPLETAR\s+PELO\s+JU[ÍI]ZO\b[^\]\n]*\]?",
    flags=re.IGNORECASE,
)


def _detectar_marcadores_pendentes(texto: str) -> List[str]:
    """Lista os marcadores ``[COMPLETAR PELO JUÍZO: ...]`` ainda presentes no texto (na ordem)."""
    return [m.group(0).strip() for m in _PADRAO_MARCADOR_COMPLETAR.finditer(texto or "")]


_PADRAO_DISPOSITIVO = re.compile(r"(?<![A-Za-z])III\s*[-–—]\s*DISPOSITIVO\b([\s\S]*)$", flags=re.IGNORECASE)

_UNIDADE_PENA = r"(?:anos?|m[eê]s(?:es)?|dias?)(?!-)"
_COMPONENTE_PENA = rf"\d+\s*(?:\([^)]*\)\s*)?{_UNIDADE_PENA}"
_PADRAO_PENA_COM_ESPECIE = re.compile(
    rf"((?:{_COMPONENTE_PENA})(?:(?:\s*,\s*|\s+e\s+)(?:{_COMPONENTE_PENA}))*)"
    r"\s+de\s+(reclus[ãa]o|deten[çc][ãa]o|pris[ãa]o\s+simples)\b",
    flags=re.IGNORECASE,
)
_PADRAO_COMPONENTE = re.compile(r"(\d+)\s*(?:\([^)]*\)\s*)?(anos?|m[eê]s(?:es)?|dias?)", flags=re.IGNORECASE)
_PADRAO_DIAS_MULTA = re.compile(r"(\d+)\s*(?:\([^)]*\)\s*)?dias?\s*[-– ]\s*multa\b", flags=re.IGNORECASE)
_PADRAO_UNIDADE_MULTA = re.compile(
    r"valor\s+unit[áa]rio\s+(?:de\s+)?([0-9]{1,9}(?:/[0-9]{1,9})?)"
    r"\s+do\s+sal[áa]rio\s+m[íi]nimo\s+vigente\s+ao\s+tempo\s+do\s+fato\b",
    re.IGNORECASE,
)



def _dias_de_trecho_pena(trecho: str) -> int:
    """Converte "4 (quatro) anos, 4 (quatro) meses e 15 (quinze) dias" em dias (convenção 360/30)."""
    total = 0
    for num, unidade in _PADRAO_COMPONENTE.findall(trecho or ""):
        n = int(num)
        u = remover_acentos(unidade.lower())
        if u.startswith("ano"):
            total += n * 360
        elif u.startswith("mes"):
            total += n * 30
        else:
            total += n
    return total


def _normalizar_especie(especie: str) -> str:
    e = remover_acentos(str(especie or "").lower()).strip().replace(" ", "_")
    if e.startswith("reclus"):
        return "reclusao"
    if e.startswith("deten"):
        return "detencao"
    if e.startswith("prisao"):
        return "prisao_simples"
    return e


def dosimetria_para_auditoria(dosimetria_dados: Any, is_demo: bool) -> Any:
    """No caso DEMO (ficticio), a presuncao de condenacao e inerente a simulacao e nao reprova as penas;
    em caso real a bandeira ``simulacao_presume_condenacao`` mantem as penas como PENDENTE."""
    if not is_demo or not isinstance(dosimetria_dados, dict) or not dosimetria_dados.get("simulacao_presume_condenacao"):
        return dosimetria_dados
    copia = dict(dosimetria_dados)
    copia["simulacao_presume_condenacao"] = False
    return copia


def _dosimetria_presume_condenacao(dosimetria_dados: Dict[str, Any]) -> bool:
    return bool(dosimetria_dados.get("simulacao_presume_condenacao"))


_PALAVRAS_ESPECIE = r"reclus[ãa]o|deten[çc][ãa]o|pris[ãa]o\s+simples"
_CARDINAL_EXTENSO = (
    r"(?:um|dois|tr[êe]s|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|treze|[cq]u?atorze|"
    r"quinze|dezesseis|dezessete|dezoito|dezenove|vinte|trinta|quarenta|cinquenta|sessenta|"
    r"setenta|oitenta|noventa|cem|cento|duzentos|trezentos|quatrocentos|quinhentos|"
    r"seiscentos|setecentos|oitocentos|novecentos|mil)"
)
_PADRAO_QUANTUM_SEM_ALGARISMOS = re.compile(
    rf"\b({_CARDINAL_EXTENSO}(?:\s+(?:e\s+)?{_CARDINAL_EXTENSO})*\s+"
    rf"(?:dias?\s*[-– ]\s*multa|{_UNIDADE_PENA}\s+de\s+(?:{_PALAVRAS_ESPECIE})))\b",
    re.IGNORECASE,
)
_PADRAO_MULTA_SEM_QUANTUM = re.compile(
    r"\b(?:(?:fixo|aplico|imponho)\s+(?:a\s+)?(?:pena\s+de\s+)?multa|"
    r"conden(?:o|ar)\b[^.;\n]*?\bao pagamento de (?:uma\s+)?multa|"
    r"al[ée]m (?:do pagamento )?de multa)\b",
    re.IGNORECASE,
)
_PADRAO_ALGARISMOS_E_EXTENSO = re.compile(r"(\d+)\s*\(([^()]+)\)")
_PADRAO_REGIME_DISPOSITIVO = re.compile(
    rf"\bregime\s+(?:inicial\s+)?(fechado|semi[- ]?aberto|aberto)\b"
    rf"(?:\s+(?:quanto [àa] pena de|para a pena de)\s+({_PALAVRAS_ESPECIE}))?",
    re.IGNORECASE,
)
_PADRAO_SOMA_PENAS = re.compile(
    rf"\b(?:a\s+soma\s+das\s+penas(?:\s+privativas\s+de\s+liberdade)?\s+(?:é|e)\s+de|"
    rf"pena\s+total\s+unificada\s+(?:de|em))\s+"
    rf"({_COMPONENTE_PENA}(?:(?:\s*,\s*|\s+e\s+){_COMPONENTE_PENA})*)",
    re.IGNORECASE,
)
_PADRAO_REPARACAO = re.compile(r"\b(?:repara[çc][ãa]o|indeniza[çc][ãa]o|indenizat[óo]rio|danos?\s+morais?)\b", re.IGNORECASE)
_PADRAO_VALOR = re.compile(r"R\$\s*([0-9]+(?:\.[0-9]{3})*(?:,[0-9]{2})?)")
_PADRAO_VEREDICTO = re.compile(
    r"\b(condenar|condeno|absolver|absolvo|(?:extinguir|extingo)\s+a\s+punibilidade|"
    r"(?:declaro|declarar)\s+extinta\s+a\s+punibilidade|desclassificar|desclassifico)\b",
    re.IGNORECASE,
)
_PADRAO_ARTIGO_DISPOSITIVO = re.compile(
    r"\bart\.?\s*(\d+(?:\s*[-–]\s*[a-z])?)"
    r"(?:\s*,?\s*caput(?:\s+e)?)?"
    r"(?:\s*,?\s*(?:§{1,2}\s*(\d+)(?:[º°])?(?!\d)|par[áa]grafo\s+[úu]nico\b))?"
    r"(?:\s*,?\s*inciso\s+([ivxlc]+)\b)?",
    re.IGNORECASE,
)
_PADRAO_DIPLOMA_PENAL = re.compile(
    r"\b(?:c[óo]digo\s+penal|CP|(?:lei\s+(?:n[º°.]?\s*)?)?11\.340/2006|Lei\s+Maria\s+da\s+Penha|"
    r"(?:decreto[- ]lei\s+(?:n[º°.]?\s*)?)?3\.688/1941|LCP|Lei\s+das\s+Contraven[çc][õo]es\s+Penais)\b",
    re.IGNORECASE,
)
_PADRAO_FUNDAMENTO = re.compile(r"\bart\.?\s*(\d+)\s*,?\s*(?:inciso\s+)?(VII|VI|IV|V|III|II|I)\b", re.IGNORECASE)
_ARTIGOS_ACESSORIOS = {"5", "7", "15", "19", "33", "44", "49", "59", "61", "68", "69", "70", "71", "77", "107",
                       "201", "312", "313", "381", "383", "386", "387", "406", "804"}


def _referencia_imputacao(citacao: re.Match) -> tuple[str, str, str, str]:
    numero = re.sub(r"\s+", "", citacao[1]).lower().replace("–", "-")
    paragrafo = citacao[2] or ("unico" if re.search(r"par[áa]grafo", citacao[0], re.IGNORECASE) else "")
    trecho = re.split(r"[();]|\bart\.?\s*\d|c/c", citacao.string[citacao.end():], maxsplit=1, flags=re.IGNORECASE)[0]
    diploma = _PADRAO_DIPLOMA_PENAL.search(trecho)
    origem = ""
    if diploma:
        norma = remover_acentos(diploma[0]).lower()
        if "11.340" in norma or "maria da penha" in norma:
            origem = "LMP"
        elif "3.688" in norma or "contravencoes" in norma or norma == "lcp":
            origem = "LCP"
        else:
            origem = "CP"
    return numero, paragrafo, (citacao[3] or "").upper(), origem


def _fundamento_correto(veredicto: str, fundamento: str, clausula: str) -> bool:
    if veredicto == "desclassificar":
        return bool(re.search(r"\bart\.?\s*383\b", clausula, re.IGNORECASE))
    esperado = _PADRAO_FUNDAMENTO.search(fundamento)
    if esperado:
        citados = {(artigo, inciso.upper()) for artigo, inciso in _PADRAO_FUNDAMENTO.findall(clausula)}
        return (esperado[1], esperado[2].upper()) in citados
    return bool(_PADRAO_MARCADOR_COMPLETAR.search(clausula))


def _divergencias_imputacoes(dispositivo: str, comandos: ComandosDispositivo) -> list[str]:
    previstos: dict[tuple[str, tuple[str, str, str, str]], list[tuple[str, str, str]]] = {}
    contagem_tipos = Counter(item.codigo for item in comandos.imputacoes)
    for item in comandos.imputacoes:
        referencia_fato = item.referencia_imputacao if contagem_tipos[item.codigo] > 1 else ""
        artigo = _PADRAO_ARTIGO_DISPOSITIVO.search(item.artigo)
        if artigo:
            verbo = "condenar" if item.veredicto == "desclassificar" else item.veredicto
            fundamento = item.fundamento if item.veredicto in ("absolver", "extinguir") else ""
            previstos.setdefault((verbo, _referencia_imputacao(artigo)), []).append((item.artigo, fundamento, referencia_fato))
        original = _PADRAO_ARTIGO_DISPOSITIVO.search(item.artigo_original)
        if item.veredicto == "desclassificar" and original:
            previstos.setdefault(("desclassificar", _referencia_imputacao(original)), []).append(
                (item.artigo_original, item.fundamento, referencia_fato)
            )

    if not previstos and not comandos.sem_condenacao:
        return []

    divergencias: list[str] = []
    encontrados: dict[tuple, set[int]] = {}
    citacoes_previstas = {chave[1][0] for chave in previstos}
    partes = _PADRAO_VEREDICTO.split(dispositivo)
    for verbo_texto, trecho in zip(partes[1::2], partes[2::2]):
        verbo = verbo_texto.lower()
        if verbo.startswith("conden"):
            verbo = "condenar"
        elif verbo.startswith("absol"):
            verbo = "absolver"
        elif verbo.startswith("desclassific"):
            verbo = "desclassificar"
        else:
            verbo = "extinguir"

        clausula = re.split(r";|\n\s*\n|(?<=[.!?])\s+(?=[A-ZÀ-Ú])", trecho.strip(), maxsplit=1)[0]
        if verbo == "condenar" and re.search(r"\bao pagamento\b", clausula, re.IGNORECASE):
            continue  # Custas e reparação são comandos distintos do veredicto penal.
        if comandos.sem_condenacao and verbo == "condenar":
            divergencias.append("O dispositivo contém um comando condenatório penal sem condenação calculada.")
            continue

        artigos = [
            (m.group(0), referencia) for m in _PADRAO_ARTIGO_DISPOSITIVO.finditer(clausula)
            if (referencia := _referencia_imputacao(m))[0] in citacoes_previstas or referencia[0] not in _ARTIGOS_ACESSORIOS
        ]
        if not artigos and previstos:
            divergencias.append(f"Comando {verbo} sem indicação da imputação no dispositivo.")
        for rotulo, referencia in artigos:
            chave = verbo, referencia
            obrigacoes = previstos.get(chave, [])
            conferidos = encontrados.setdefault(chave, set())
            normalizada = normalizar(clausula)
            posicao = next((
                indice for indice, (_, _, fato) in enumerate(obrigacoes)
                if indice not in conferidos and (not fato or normalizar(fato) in normalizada)
            ), None)
            if posicao is None:
                divergencias.append(f"Comando {verbo} para {rotulo} excedente ou incompatível com a imputação registrada.")
                continue
            conferidos.add(posicao)
            rotulo, fundamento, _ = obrigacoes[posicao]
            if fundamento and not _fundamento_correto(verbo, fundamento, clausula):
                divergencias.append(f"Fundamento do comando {verbo} para {rotulo} ausente ou divergente: esperado {fundamento}.")

    for chave, obrigacoes in previstos.items():
        for indice, (rotulo, _, fato) in enumerate(obrigacoes):
            if indice not in encontrados.get(chave, ()):
                referencia = f" ({fato})" if fato else ""
                divergencias.append(f"O comando {chave[0]} para {rotulo}{referencia} não consta do dispositivo.")
    return divergencias


def _divergencias_regime(dispositivo: str, comandos: ComandosDispositivo) -> list[str]:
    regimes = _PADRAO_REGIME_DISPOSITIVO.findall(dispositivo)
    if comandos.sem_condenacao:
        return [f"Regime {regime} indicado sem condenação calculada." for regime, _ in regimes]
    previstos = comandos.regime_por_especie
    if not previstos:
        return []
    conferidos: Counter = Counter()
    divergencias: list[str] = []
    for regime, rotulo in regimes:
        regime = re.sub(r"[-\s]", "", regime.lower())
        especies = [_normalizar_especie(rotulo)] if rotulo else list(previstos)
        for especie in especies:
            esperado = previstos.get(especie)
            if regime != esperado:
                divergencias.append(f"Regime {regime} divergente para {formatar_especie_pena(especie)}: esperado {esperado or 'nenhum'}.")
            else:
                conferidos[especie] += 1
                if conferidos[especie] > 1:
                    divergencias.append(f"Regime inicial duplicado para {formatar_especie_pena(especie)}.")
    for especie, regime in previstos.items():
        if not conferidos[especie]:
            divergencias.append(f"O regime inicial {regime} para {formatar_especie_pena(especie)} não consta do dispositivo.")
    return divergencias


def _divergencias_reparacao(dispositivo: str, comandos: ComandosDispositivo) -> list[str]:
    if comandos.reparacao is None:
        return []
    paragrafos = re.split(r"\n\s*\n|[;]", dispositivo)
    citados = [
        Decimal(valor.replace(".", "").replace(",", "."))
        for paragrafo in paragrafos if _PADRAO_REPARACAO.search(paragrafo)
        for valor in _PADRAO_VALOR.findall(paragrafo)
    ]
    if comandos.reparacao_pendente and not any(
        "reparação mínima" in marcador.lower() for marcador in _detectar_marcadores_pendentes(dispositivo)
    ):
        return ["A reparação mínima aguarda arbitramento e a pendência não consta do dispositivo."] + [
            f"Valor de reparação {valor} indicado sem arbitramento." for valor in citados
        ]
    esperado = [comandos.reparacao] if comandos.reparacao > 0 else []
    divergencias = [f"Valor de reparação {valor} divergente ou excedente no dispositivo." for valor in (Counter(citados) - Counter(esperado)).elements()]
    if esperado and comandos.reparacao not in citados:
        divergencias.append(f"O valor calculado da reparação mínima ({comandos.reparacao}) não consta do dispositivo.")
    return divergencias


def _quantias_contraditorias(trecho: str) -> list[str]:
    divergencias = []
    for numero, extenso in _PADRAO_ALGARISMOS_E_EXTENSO.findall(trecho):
        literal = " ".join(remover_acentos(extenso).lower().split())
        esperado = remover_acentos(numero_por_extenso(int(numero))).replace("quatorze", "catorze")
        if literal.replace("quatorze", "catorze") not in (esperado, numero):
            divergencias.append(f"Quantidade {numero} contradiz sua redação por extenso ({extenso}).")
    return divergencias


def verificar_comandos_dispositivo(dispositivo: str, comandos: ComandosDispositivo) -> list[str]:
    """Compara os comandos, as penas definitivas por espécie e os valores efetivamente declarados."""
    divergencias: list[str] = []
    for quantum in _PADRAO_QUANTUM_SEM_ALGARISMOS.findall(dispositivo):
        divergencias.append(f"Quantifique em algarismos a pena ou a multa indicada em '{quantum}' para conferência.")
    penas = comandos.penas_por_especie
    conferidas: Counter = Counter()
    for m in _PADRAO_PENA_COM_ESPECIE.finditer(dispositivo):
        trecho = " ".join(m[0].split())
        divergencias.extend(_quantias_contraditorias(m[1]))
        especie = _normalizar_especie(m[2])
        esperada = penas.get(especie)
        if comandos.sem_condenacao:
            divergencias.append(f"O dispositivo cita a pena '{trecho}', mas a dosimetria calculada não contém condenação.")
        elif esperada is None or _dias_de_trecho_pena(m[1]) != esperada.to_total_days():
            ref = esperada.format_extenso() if esperada else "nenhuma pena desta espécie calculada"
            divergencias.append(f"Pena citada no dispositivo '{trecho}' não corresponde à pena total da espécie ({ref}).")
        else:
            conferidas[especie] += 1
            if conferidas[especie] > 1:
                divergencias.append(f"Pena total de {formatar_especie_pena(especie)} repetida no dispositivo: '{trecho}'.")
    for especie, pena in penas.items():
        if not conferidas[especie]:
            divergencias.append(f"A pena calculada de {pena.format_extenso()} de {formatar_especie_pena(especie)} não consta do dispositivo.")

    total = comandos.pena_total_unificada
    somas_conferidas = 0
    for m in _PADRAO_SOMA_PENAS.finditer(dispositivo):
        divergencias.extend(_quantias_contraditorias(m[1]))
        if total is None or _dias_de_trecho_pena(m[1]) != total.to_total_days():
            divergencias.append(f"Soma explícita das penas divergente ou indevida no dispositivo: '{m[0]}'.")
        else:
            somas_conferidas += 1
            if somas_conferidas > 1:
                divergencias.append("Soma das penas privativas de liberdade repetida no dispositivo.")
    if len(penas) > 1 and total is not None and not somas_conferidas:
        divergencias.append(f"A soma das penas privativas de liberdade ({total.format_extenso()}) não consta do dispositivo.")

    multas = list(_PADRAO_DIAS_MULTA.finditer(dispositivo))
    for obrigacao in _PADRAO_MULTA_SEM_QUANTUM.finditer(dispositivo):
        trecho = re.split(r"[.;\n]", dispositivo[obrigacao.end():], maxsplit=1)[0]
        if not _PADRAO_DIAS_MULTA.search(trecho):
            divergencias.append(f"Comando de multa sem quantidade conferível no dispositivo: '{obrigacao[0]}'.")
    esperadas = Counter((multa.dias, multa.valor_unitario) for multa in comandos.multas)
    for indice, multa in enumerate(multas):
        trecho = " ".join(multa[0].split())
        divergencias.extend(_quantias_contraditorias(trecho))
        fim = multas[indice + 1].start() if indice + 1 < len(multas) else len(dispositivo)
        complemento = re.split(r"[;\n]", dispositivo[multa.end():fim], maxsplit=1)[0]
        unidade = _PADRAO_UNIDADE_MULTA.search(complemento)
        chave = (int(multa[1]), unidade[1] if unidade else "")
        if esperadas[chave]:
            esperadas[chave] -= 1
        else:
            divergencias.append(f"Multa citada no dispositivo '{trecho}' não corresponde à calculada ou tem valor unitário divergente.")
    for dias, unidade in esperadas.elements():
        divergencias.append(f"A multa calculada de {dias} dias-multa não consta com o valor unitário de {unidade}.")
    if comandos.multa_pendente and not any(
        "valor unitário da multa" in marcador.lower() for marcador in _detectar_marcadores_pendentes(dispositivo)
    ):
        divergencias.append("A decisão sobre a quantidade e o valor unitário da multa permanece pendente.")

    divergencias.extend(_divergencias_imputacoes(dispositivo, comandos))
    divergencias.extend(_divergencias_regime(dispositivo, comandos))
    divergencias.extend(_divergencias_reparacao(dispositivo, comandos))
    return divergencias


def _verificar_penas_dispositivo(
    texto_sentenca: str,
    dosimetria_dados: Optional[Dict[str, Any]],
) -> Tuple[str, List[str], str]:
    """Confronta o dispositivo com os comandos calculados e preserva o contrato público do laudo."""
    if hasattr(dosimetria_dados, "model_dump"):
        dosimetria_dados = dosimetria_dados.model_dump(mode="json")
    if not isinstance(dosimetria_dados, dict) or not dosimetria_dados:
        return "NAO_APLICAVEL", [], "Dosimetria calculada não fornecida ao auditor; penas não conferidas."
    if _dosimetria_presume_condenacao(dosimetria_dados):
        mensagem = (
            "A dosimetria de referência presume condenação (crime sem veredicto do juízo): as penas do "
            "dispositivo não puderam ser conferidas. Informe o veredicto de cada crime."
        )
        return "PENDENTE", [mensagem], mensagem

    m_disp = _PADRAO_DISPOSITIVO.search(texto_sentenca or "")
    if not m_disp:
        mensagem = "Dispositivo não localizado no texto; comandos e penas não conferidos."
        return "REPROVADO", [mensagem], mensagem
    try:
        comandos = construir_comandos_dispositivo(dosimetria_dados)
    except (ArithmeticError, TypeError, ValueError):
        mensagem = "A referência de dosimetria contém comandos inválidos e deve ser recalculada."
        return "PENDENTE", [mensagem], mensagem
    divergencias = verificar_comandos_dispositivo(m_disp.group(1), comandos)
    if divergencias:
        return "REPROVADO", divergencias, f"{len(divergencias)} divergência(s) entre o dispositivo e os comandos calculados."
    return "APROVADO", [], "Comandos e penas do dispositivo conferidos com a dosimetria calculada."


def _avaliar_art21_lmp(texto: str) -> bool:
    """Verifica a determinação de intimação pessoal da ofendida (art. 21 da Lei 11.340/2006)."""
    return "art. 21 da Lei nº 11.340/2006" in (texto or "")


def _avaliar_lc95_98(texto: str) -> bool:
    """Verifica a grafia de artigos de lei conforme a LC 95/98 (ex.: 'art. 129', não 'art. 129º')."""
    erros_lc95 = re.findall(r"\bart\.\s*[1-9]\d+º", texto or "", flags=re.IGNORECASE)
    return len(erros_lc95) == 0


def _avaliar_datas_padrao_br(texto: str) -> bool:
    """Veda datas em formato estrangeiro (ISO 8601 AAAA-MM-DD / AAAA/MM/DD / DD-MM-AAAA)."""
    datas_invalidas = re.findall(r"\b(19\d{2}|20\d{2})[-/](\d{2})[-/](\d{2})\b", texto or "")
    datas_hifen = re.findall(r"\b(\d{2})-(\d{2})-(19\d{2}|20\d{2})\b", texto or "")
    return (len(datas_invalidas) + len(datas_hifen)) == 0


def _avaliar_abnt_citacoes(texto: str) -> bool:
    """ABNT NBR 10520: citação longa com recuo de 4 espaços não pode vir entre aspas externas."""
    blocos = (texto or "").split("\n\n")
    for bloco in blocos:
        if bloco.startswith("    "):
            conteudo = bloco.strip()
            if conteudo.startswith(('"', "'", "“", "‘")) or conteudo.endswith(('"', "'", "”", "’")):
                return False
    return True


def _avaliar_estilo_cnj_amb(texto: str, *, permitir_relato: bool = False) -> bool:
    """
    Diretrizes CNJ/AMB: vedação da tese de legítima defesa da honra (ADPF 779/STF) e
    expurgo de solecismos forenses.
    """
    ldh_afastada = not violacao_vedacao_honra(texto, permitir_relato=permitir_relato)
    erros_gramatica = re.findall(
        r"\b(?:hajam\s+vistas?|haja\s+visto|ao\s+meu\s+ver|a\s+nível\s+de)\b",
        texto or "",
        flags=re.IGNORECASE,
    )
    return ldh_afastada and len(erros_gramatica) == 0


#: Regex de isolamento da seção de dosimetria (evita corte prematuro pela palavra 'dispositivo' minúscula no texto).
_PADRAO_TRECHO_DOSIMETRIA = re.compile(
    r"(?:(?:2\.6|3)\.\s+Da\s+Dosimetria|DOSIMETRIA\s+(?:DA\s+PENA|TRIF[ÁA]SICA)|Art\.?\s*59\s+d[oe]\s+C[óo]digo\s+Penal)([\s\S]*?)(?:\n\s*III\s*[-–—]\s*DISPOSITIVO\b|\n\s*(?-i:DISPOSITIVO)\b|$)",
    flags=re.IGNORECASE,
)

_VETORES_ART59_PADROES = [
    ("Culpabilidade", r"\bculpabilidade\b"),
    ("Antecedentes Criminais", r"\bantecedentes(?:\s+criminais)?\b"),
    ("Conduta Social", r"\bconduta\s+social\b"),
    ("Personalidade do Agente", r"\bpersonalidade(?:\s+do\s+agente)?\b"),
    ("Motivos do Crime", r"\bmotivos(?:\s+do\s+crime)?\b"),
    ("Circunstâncias do Crime", r"\bcircunstâncias(?:\s+do\s+(?:crime|delito))?\b"),
    ("Consequências do Crime", r"\bconsequências(?:\s+do\s+(?:crime|delito))?\b"),
    ("Comportamento da Vítima", r"\bcomportamento\s+da\s+(?:vítima|ofendida)\b"),
]

_PADRAO_VALORACAO_ART59 = re.compile(
    r"(?:desfavor[áa]ve(?:l|is)|favor[áa]ve(?:l|is)|neutr[ao]s?|atenuad[ao]s?|exasperad[ao]s?|"
    r"m[íi]nimo\s+legal|resolu[çc][ãa]o\s+cnj\s+n[ºo]?\s*492|n[ãa]o\s+concorreu|s[úu]mula\s+444|"
    r"valorar\s+negativamente|nada\s+(?:havendo\s+)?a\s+valorar|deixo\s+de\s+valor|bons\s+antecedentes|prim[áa]rio|"
    r"n[ãa]o\s+desbord|n[ãa]o\s+contribu|em\s+nada\s+contribuiu|normal\s+[àa]\s+esp[ée]cie|normais\s+ao\s+tipo|"
    r"normalidade\s+d[ao]s|desabonem|aferi[çc][ãa]o\s+desfavor[áa]vel|inerente\s+ao\s+contexto|n[ãa]o\s+produziram\s+sequelas|"
    r"mantendo-se\s+neutr[ao]|sendo\s+a\s+circunst[âa]ncia\s+neutr[ao]|mesmos\s+fundamentos|reportando-me|me\s+reporto)",
    flags=re.IGNORECASE,
)

#: Variantes desacentuadas (tolerância redacional exigida pelo in-flight gating do M1).
_VETORES_ART59_PADROES_SEM_ACENTO = [
    ("Culpabilidade", r"\bculpabilidade\b"),
    ("Antecedentes Criminais", r"\bantecedentes(?:\s+criminais)?\b"),
    ("Conduta Social", r"\bconduta\s+social\b"),
    ("Personalidade do Agente", r"\bpersonalidade(?:\s+do\s+agente)?\b"),
    ("Motivos do Crime", r"\bmotivos(?:\s+do\s+crime)?\b"),
    ("Circunstâncias do Crime", r"\bcircunstancias(?:\s+do\s+(?:crime|delito))?\b"),
    ("Consequências do Crime", r"\bconsequencias(?:\s+do\s+(?:crime|delito))?\b"),
    ("Comportamento da Vítima", r"\bcomportamento\s+da\s+(?:vitima|ofendida)\b"),
]

_PADRAO_VALORACAO_ART59_SEM_ACENTO = re.compile(
    r"(?:desfavorave(?:l|is)|favorave(?:l|is)|neutr[ao]s?|atenuad[ao]s?|exasperad[ao]s?|"
    r"minimo\s+legal|resolucao\s+cnj\s+n[ºo]?\s*492|nao\s+concorreu|sumula\s+444|"
    r"valorar\s+negativamente|nada\s+(?:havendo\s+)?a\s+valorar|deixo\s+de\s+valor|bons\s+antecedentes|primario|"
    r"nao\s+desbord|nao\s+contribu|em\s+nada\s+contribuiu|normal\s+a\s+especie|normais\s+ao\s+tipo|"
    r"normalidade\s+d[ao]s|desabonem|afericao\s+desfavoravel|inerente\s+ao\s+contexto|nao\s+produziram\s+sequelas|"
    r"mantendo-se\s+neutr[ao]|sendo\s+a\s+circunstancia\s+neutr[ao]|mesmos\s+fundamentos|reportando-me|me\s+reporto)",
    flags=re.IGNORECASE,
)


def _extrair_trecho_dosimetria(texto_sentenca: str) -> str:
    """Isola a seção de dosimetria para impedir falsos positivos do art. 59 no relatório/mérito."""
    m_dos = _PADRAO_TRECHO_DOSIMETRIA.search(texto_sentenca or "")
    return m_dos.group(1) if m_dos else (texto_sentenca or "")


def _eh_absolvicao_ou_extincao_integral(texto_sentenca: str) -> bool:
    """Identifica sentenças de absolvição integral ou extinção integral da punibilidade (sem condenação)."""
    if not texto_sentenca:
        return False
    m_disp = re.search(r"III\s*[-–—]\s*DISPOSITIVO\b([\s\S]*)$", texto_sentenca, flags=re.IGNORECASE)
    if not m_disp:
        return False
    disp = m_disp.group(1)
    disp_up = disp.upper()
    tem_condenacao = bool(re.search(r"\bCONDENAR\b", disp_up)) or (
        "CONDENO" in disp_up and "CUSTAS" not in disp_up.split("CONDENO")[0][-30:]
    )
    if tem_condenacao:
        return False
    tem_absolvicao_ou_ext = (
        "JULGO IMPROCEDENTE" in disp_up
        or "ABSOLVER" in disp_up
        or "EXTINGUIR A PUNIBILIDADE" in disp_up
    )
    dos_norm = _remover_acentos(_extrair_trecho_dosimetria(texto_sentenca).lower())
    dos_prejudicada = (
        "prejudicada" in dos_norm
        and ("absolvicao" in dos_norm or "extincao" in dos_norm)
    )
    return tem_absolvicao_ou_ext and dos_prejudicada


def _avaliar_oito_circunstancias_art59(
    texto_sentenca: str,
    tolerante_acentos: bool = False,
) -> Tuple[bool, List[str], List[str]]:
    """
    Verifica a enunciação e a valoração expressa das 8 circunstâncias judiciais do art. 59.

    Retorna ``(conformidade, vetores_ausentes, vetores_sem_valoracao)``. Quando
    ``tolerante_acentos=True`` (usado na auditoria seccional da dosimetria), aceita
    grafias sem acento — "circunstancias", "consequencias", "comportamento da vitima" —
    em alinhamento com o validador in-flight do M1.
    """
    if tolerante_acentos:
        trecho_dosimetria = _remover_acentos(_extrair_trecho_dosimetria(texto_sentenca)).lower()
        vetores_padroes = _VETORES_ART59_PADROES_SEM_ACENTO
        padrao_valoracao = _PADRAO_VALORACAO_ART59_SEM_ACENTO
    else:
        trecho_dosimetria = _extrair_trecho_dosimetria(texto_sentenca)
        vetores_padroes = _VETORES_ART59_PADROES
        padrao_valoracao = _PADRAO_VALORACAO_ART59

    vetores_ausentes: List[str] = []
    vetores_sem_valoracao: List[str] = []

    for nome, padrao in vetores_padroes:
        matches = list(re.finditer(padrao, trecho_dosimetria, flags=re.IGNORECASE))
        if not matches:
            vetores_ausentes.append(nome)
        else:
            tem_valoracao = False
            for m in matches:
                fim_paragrafo = trecho_dosimetria.find("\n", m.start())
                if fim_paragrafo == -1 or (fim_paragrafo - m.start() < 50):
                    fim_paragrafo = min(len(trecho_dosimetria), m.start() + 800)
                janela = trecho_dosimetria[m.start():fim_paragrafo + 50]
                if padrao_valoracao.search(janela):
                    tem_valoracao = True
                    break
            if not tem_valoracao:
                vetores_sem_valoracao.append(nome)

    conformidade = len(vetores_ausentes) == 0 and len(vetores_sem_valoracao) == 0
    return conformidade, vetores_ausentes, vetores_sem_valoracao


# ==============================================================================
# 5. CAMADA 3 — NORMALIZAÇÃO E CONFRONTO DE TESES DEFENSIVAS
# ==============================================================================


@dataclass
class _TeseAuditavel:
    """Representação normalizada e polimórfica de uma tese defensiva."""

    codigo: str
    titulo: str
    tipo: str  # PRELIMINAR | MERITO | SUBSIDIARIA
    descricao: str

    @property
    def referencia(self) -> str:
        if self.codigo:
            return f"{self.codigo} — {self.titulo}" if self.titulo else self.codigo
        return self.titulo or "tese sem código"


_STOPWORDS_TESE = {
    "para", "como", "sobre", "pelo", "pela", "artigo", "codigo", "penal", "crime", "crimes",
    "autos", "defesa", "tese", "teses", "acusado", "processo", "processual", "principio",
    "aplicacao", "pena", "penas", "legal", "fatos", "quanto", "sustenta", "alega", "peca",
    "pecas", "acusatoria", "termos", "ainda", "apenas", "tambem", "seja", "qual", "quais",
    "onde", "quando", "porque", "porem", "contudo", "entao", "assim", "deve", "pode", "nos",
    "das", "dos", "uma", "que", "com", "por", "sem", "sua", "seu", "aos",
    "prova", "provas", "material", "inexistencia", "existencia", "ausencia",
}

#: Sinônimos forenses aceitos no confronto semântico das teses (tolerância redacional).
_SINONIMOS_TESE: Dict[str, Tuple[str, ...]] = {
    "inepcia": ("inepcia", "justa causa", "aptidao da denuncia", "requisitos do art. 41"),
    "decadencia": ("decadencia",),
    "legitima defesa": ("legitima defesa", "agressoes reciprocas"),
    "in dubio": ("in dubio", "insuficiencia probatoria", "fragilidade probatoria", "falta de provas"),
    "negativa": ("negativa de autoria", "nao cometeu", "nao praticou", "alibi"),
    "animo exaltado": ("animo exaltado", "estado de ira", "colera", "sem seriedade"),
    "ausencia de dolo": ("ausencia de dolo", "sem intencao", "dolo especifico"),
    "vias de fato": ("vias de fato", "desclassificacao"),
    "habitualidade": ("habitualidade", "reiteracao", "perseguicao"),
    "perseguicao": ("perseguicao", "habitualidade", "reiteracao"),
    "inexigibilidade": ("inexigibilidade", "urgencia medica", "coacao moral"),
    "sursis": ("sursis", "restritiva de direitos", "art. 77", "dano moral"),
    "minimo": ("minimo legal", "pena-base no minimo", "pena base no minimo"),
    "substituicao": ("substituicao", "restritiva de direitos"),
}


def _normalizar_tipo_tese(tipo_bruto: Any, codigo: str, titulo: str) -> str:
    """
    Normaliza os tipos das teses, incluindo a tese vedada com repúdio obrigatório.

    Teses cujo objeto é a dosimetria (pena no mínimo, sursis, substituição por
    restritivas, pedidos subsidiários) são classificadas como SUBSIDIARIA ainda que o
    operador as tenha tipificado como MERITO: por sua natureza, pertencem à seção de
    dosimetria (art. 68 do CP e Súmulas 231/444/588 do STJ), não à fundamentação de
    mérito. Isso evita falso positivo de omissão de tese no MÉRITO quando o pedido
    dosimétrico é corretamente enfrentado na DOSIMETRIA.
    """
    valor = _remover_acentos(str(tipo_bruto or "").strip().upper())
    codigo_upper = (codigo or "").upper()

    if tese_vedada_honra({"codigo": codigo_upper, "tipo": valor}):
        return "VEDADA"
    if valor.startswith("PREL") or codigo_upper.startswith("PREL"):
        return "PRELIMINAR"
    if valor.startswith("SUB") or codigo_upper.startswith("SUB"):
        return "SUBSIDIARIA"

    titulo_norm = _remover_acentos((titulo or "").lower())
    if any(chave in titulo_norm for chave in ("dosimetr", "sursis", "pena-base", "pena base", "subsidiar")):
        return "SUBSIDIARIA"

    return "MERITO"


def _normalizar_teses(teses_defesa: Optional[Iterable[Any]]) -> List[_TeseAuditavel]:
    """Normaliza teses heterogêneas (TeseDefensivaItem, dict, str do dossiê) em _TeseAuditavel."""
    normalizadas: List[_TeseAuditavel] = []
    for item in teses_defesa or ():
        if isinstance(item, TeseDefensivaItem):
            codigo = item.codigo or ""
            titulo = item.titulo or ""
            tipo_bruto = item.tipo
            descricao = item.descricao_defesa or ""
        elif isinstance(item, dict):
            codigo = str(item.get("codigo") or item.get("codigo_tese") or "").strip()
            titulo = str(item.get("titulo") or "").strip()
            tipo_bruto = item.get("tipo")
            descricao = str(item.get("descricao_defesa") or item.get("descricao") or "").strip()
        elif isinstance(item, str):
            texto = item.strip()
            match_codigo = re.match(r"^([A-Za-z]+-\d+)\s*[:—\-]\s*(.*)$", texto)
            if match_codigo:
                codigo = match_codigo.group(1).strip()
                resto = match_codigo.group(2).strip()
            else:
                codigo = ""
                resto = texto
            partes = re.split(r"\s+[-–—]\s+", resto, maxsplit=1)
            titulo = partes[0].strip()
            descricao = partes[1].strip() if len(partes) > 1 else ""
            tipo_bruto = ""
        else:
            codigo = str(getattr(item, "codigo", "") or "").strip()
            titulo = str(getattr(item, "titulo", "") or "").strip()
            tipo_bruto = getattr(item, "tipo", "")
            descricao = str(getattr(item, "descricao_defesa", "") or "").strip()

        if not (codigo or titulo):
            continue
        tipo = _normalizar_tipo_tese(tipo_bruto, codigo, titulo)
        normalizadas.append(
            _TeseAuditavel(codigo=codigo, titulo=titulo, tipo=tipo, descricao=descricao)
        )
    return normalizadas


def _tokens_significativos(texto: str) -> List[str]:
    """Extrai tokens jurídicos significativos (sem acento, >=4 letras, fora da stoplist)."""
    tokens = re.findall(r"\w{4,}", _remover_acentos((texto or "").lower()))
    unicos = sorted({t for t in tokens if t not in _STOPWORDS_TESE})
    return unicos


def _tese_linha_enfrentada(texto: str, texto_norm: str, tese: _TeseAuditavel) -> bool:
    """
    Confronto determinístico de enfrentamento de tese: código formal, prefixo do título,
    sobreposição de tokens significativos e sinônimos forenses conhecidos.
    """
    if tese.tipo == "VEDADA":
        return repudio_explicito_honra(texto)

    codigo = (tese.codigo or "").strip()
    if codigo and re.search(r"\b" + re.escape(codigo) + r"\b", texto_norm, flags=re.IGNORECASE):
        return True

    titulo = (tese.titulo or "").strip()
    if titulo:
        prefixo = _remover_acentos(titulo[:20].lower()).strip()
        if prefixo and prefixo in texto_norm:
            return True

        tokens = _tokens_significativos(titulo)
        if tokens:
            presentes = [t for t in tokens if t in texto_norm]
            minimo = max(2, (len(tokens) * 3 + 4) // 5) if len(tokens) >= 2 else 1
            if len(presentes) >= minimo:
                return True

    titulo_norm = _remover_acentos((titulo or "").lower())
    for chave, sinonimos in _SINONIMOS_TESE.items():
        if chave in titulo_norm:
            if any(_remover_acentos(s) in texto_norm for s in sinonimos):
                return True

    return False


def _tese_com_evasiva(texto_norm: str, tese: _TeseAuditavel) -> Optional[str]:
    """Detecta tangenciamento evasivo de uma tese (menção sem enfrentamento decisório)."""
    alvos = []
    if tese.codigo:
        alvos.append(_remover_acentos(tese.codigo.lower()))
    if tese.titulo:
        alvos.extend(_tokens_significativos(tese.titulo)[:4])
    for alvo in alvos:
        pos = texto_norm.find(alvo)
        if pos == -1:
            continue
        janela = texto_norm[max(0, pos - 200):pos + 260]
        for evasiva in _FORMULAS_EVASIVAS:
            if evasiva in janela:
                return evasiva
    return None


def _detectar_formulas_evasivas(texto: str) -> List[str]:
    """Retorna as fórmulas evasivas de não enfrentamento presentes no texto (art. 381, III, CPP)."""
    texto_norm = _remover_acentos((texto or "").lower())
    return [evasiva for evasiva in _FORMULAS_EVASIVAS if evasiva in texto_norm]


def _filtrar_teses_aplicaveis_secao(
    secao: str,
    teses: Sequence[_TeseAuditavel],
) -> List[_TeseAuditavel]:
    """
    Filtra as teses defensivas exigíveis na seção auditada.

    - PRELIMINARES: apenas teses PRELIMINARES (art. 381, III, CPP);
    - MÉRITO: apenas teses de MÉRITO;
    - DOSIMETRIA: apenas teses SUBSIDIÁRIAS (pedidos dosimétricos/sursis/substituição);
    - RELATÓRIO e DISPOSITIVO: não exigem enfrentamento (completude global fica a cargo
      do texto integral, quando fornecido).
    """
    if secao == "preliminares":
        return [t for t in teses if t.tipo == "PRELIMINAR"]
    if secao == "merito":
        return [t for t in teses if t.tipo in ("MERITO", "VEDADA")]
    if secao == "dosimetria":
        return [t for t in teses if t.tipo == "SUBSIDIARIA"]
    return []


def _auditar_teses_seccionais(
    texto: str,
    teses_aplicaveis: Sequence[_TeseAuditavel],
) -> Tuple[List[str], List[str]]:
    """Confronta as teses aplicáveis contra o texto seccional (Camada 3)."""
    texto_norm = _remover_acentos(texto or "").lower()
    enfrentadas: List[str] = []
    omitidas: List[str] = []
    for tese in teses_aplicaveis:
        if not _tese_linha_enfrentada(texto, texto_norm, tese):
            omitidas.append(tese.referencia)
            continue
        evasiva = _tese_com_evasiva(texto_norm, tese)
        if evasiva:
            omitidas.append(f"{tese.referencia} (tangenciada por fórmula evasiva: '{evasiva}')")
        else:
            enfrentadas.append(tese.referencia)
    return enfrentadas, omitidas


# ==============================================================================
# 6. AUDITORIA MONOLÍTICA (M1/GATE 4) — ASSINATURA E RETORNO PRESERVADOS
# ==============================================================================


def auditar_sentenca(
    texto_sentenca: str,
    pecas_autos: List[PecaClassificada],
    teses_defesa: List[TeseDefensivaItem],
    ids_midias: List[str] | None = None,
    *,
    dosimetria_dados: Optional[Dict[str, Any]] = None,
    secoes_fallback: Optional[List[str]] = None,
) -> RelatorioAuditoriaSentenca:
    """
    Executa a Auditoria Anti-Alucinação em 4 Camadas + verificação de conteúdo:
    Camada 1: Verificação de existência física de todos os IDs citados no índice dos autos e nas mídias;
    Camada 2: Verificação de citações jurisprudenciais (Súmulas STJ/STF, Temas Repetitivos, Julgados STF,
              Enunciados FONAVID, Jurisprudência TJRN e CNJ) contra a Base Fechada Autorizada;
    Camada 3: Checklist System 1 (Laya) de enfrentamento individualizado de 100% das teses defensivas (Art. 381, III, CPP);
    Camada 4: Conformidade formal e legislativa (art. 381 do CPP, LC 95/98, art. 21 da Lei 11.340/2006,
              datas, ABNT, CNJ/AMB e 8 vetores do art. 59 do CP);
    Conteúdo: marcadores ``[COMPLETAR PELO JUÍZO ...]`` pendentes e, quando ``dosimetria_dados`` é
              fornecido, penas definitivas do dispositivo x penas calculadas.

    ``secoes_fallback`` lista as seções substituídas por modelo determinístico (ex.: blindagem da
    importação externa) — elas são apenas SINALIZADAS no laudo (não alteram o score).

    Toda reprovação desconta do score; ``aprovada_sem_alucinacao`` é verdadeiro se, e somente se,
    nenhuma verificação reprovou (score 100.0).
    """
    texto_sentenca = texto_sentenca or ""
    pecas_autos = list(pecas_autos or [])
    teses_defesa = list(teses_defesa or [])

    # Camada 1: universo fechado = peças + mídias informadas + mídias registradas nos autos.
    ids_validos = _coletar_ids_validos(pecas_autos, ids_midias)
    ids_encontrados = sorted(
        {_normalizar_id_citado(tok) for tok in _PADRAO_ID_SENTENCA.findall(texto_sentenca)}
    )
    ids_ok = [i for i in ids_encontrados if i in ids_validos]
    ids_falsos = [i for i in ids_encontrados if i not in ids_validos]

    # Camada 2: Súmulas, Temas Repetitivos, STF Vinculante, Teses STJ, Enunciados FONAVID, TJRN e CNJ
    indice = _construir_indice_base_fechada()
    citacoes_ok, citacoes_invalidas = _auditar_citacoes_juridicas(texto_sentenca, indice)

    # Camada 3: Enfrentamento das Teses da Defesa (Art. 381, III, CPP)
    teses_respondidas: List[str] = []
    teses_omitidas: List[str] = []
    texto_norm_c3 = _remover_acentos(texto_sentenca).lower()
    for t_norm in _normalizar_teses(teses_defesa):
        referencia = f"{t_norm.codigo} — {t_norm.titulo}" if t_norm.codigo else t_norm.referencia
        if _tese_linha_enfrentada(texto_sentenca, texto_norm_c3, t_norm):
            teses_respondidas.append(referencia)
        else:
            teses_omitidas.append(referencia)

    # Camada 4: Conformidade Art. 381 CPP, Art. 21 Lei 11.340/2006, LC 95/1998, ABNT NBR 10520,
    # Padrão de Datas BR e Magistratura (CNJ/AMB)
    conf_art381 = _avaliar_art381_cpp(texto_sentenca)
    conf_art21_lmp = _avaliar_art21_lmp(texto_sentenca)
    conf_lc95 = _avaliar_lc95_98(texto_sentenca)
    conf_datas = _avaliar_datas_padrao_br(texto_sentenca)
    conf_abnt = _avaliar_abnt_citacoes(texto_sentenca)
    conf_estilo = _avaliar_estilo_cnj_amb(texto_sentenca)
    if _eh_absolvicao_ou_extincao_integral(texto_sentenca):
        conf_oito_circunstancias = True
        vetores_ausentes: List[str] = []
        vetores_sem_valoracao: List[str] = []
    else:
        (
            conf_oito_circunstancias,
            vetores_ausentes,
            vetores_sem_valoracao,
        ) = _avaliar_oito_circunstancias_art59(texto_sentenca)

    # Conteúdo: marcadores pendentes e penas do dispositivo x dosimetria calculada.
    marcadores = _detectar_marcadores_pendentes(texto_sentenca)
    conf_marcadores = not marcadores
    status_penas, penas_divergentes, desc_penas = _verificar_penas_dispositivo(texto_sentenca, dosimetria_dados)
    conf_penas = not penas_divergentes
    secoes_fb = [str(s) for s in (secoes_fallback or []) if str(s or "").strip()]

    # ------------------------------------------------------------------
    # Score: TODA reprovação desconta (forma e conteúdo).
    # ------------------------------------------------------------------
    desconto_forma = 0.0
    if not conf_art381:
        desconto_forma += 25.0
    if not conf_art21_lmp:
        desconto_forma += 15.0
    if not conf_lc95:
        desconto_forma += 5.0
    if not conf_datas:
        desconto_forma += 10.0
    if not conf_abnt:
        desconto_forma += 10.0
    if not conf_estilo:
        desconto_forma += 10.0
    if not conf_oito_circunstancias:
        desconto_forma += 20.0

    desconto_conteudo = 0.0
    desconto_conteudo += len(ids_falsos) * 25.0
    desconto_conteudo += len(citacoes_invalidas) * 25.0
    desconto_conteudo += len(teses_omitidas) * 20.0
    desconto_conteudo += len(marcadores) * 15.0
    desconto_conteudo += len(penas_divergentes) * 25.0

    score_forma = max(0.0, round(100.0 - desconto_forma, 1))
    score = max(0.0, round(100.0 - desconto_forma - desconto_conteudo, 1))

    aprovada = (
        len(ids_falsos) == 0
        and len(citacoes_invalidas) == 0
        and len(teses_omitidas) == 0
        and conf_art381
        and conf_art21_lmp
        and conf_lc95
        and conf_datas
        and conf_abnt
        and conf_estilo
        and conf_oito_circunstancias
        and conf_marcadores
        and conf_penas
    )

    # ------------------------------------------------------------------
    # Laudo
    # ------------------------------------------------------------------
    problemas_forma: List[str] = []
    if not conf_art381:
        problemas_forma.append(
            "Art. 381 do CPP: estrutura canônica incompleta (exigidos 'I – RELATÓRIO', "
            "'II – FUNDAMENTAÇÃO' e 'III – DISPOSITIVO')"
        )
    if not conf_art21_lmp:
        problemas_forma.append("Art. 21 da Lei nº 11.340/2006: omissão da intimação pessoal da ofendida")
    if not conf_lc95:
        problemas_forma.append("LC 95/98: grafia incorreta de artigo (ex.: 'art. 129º')")
    if not conf_datas:
        problemas_forma.append("Datas fora do padrão brasileiro (ISO ou DD-MM-AAAA)")
    if not conf_abnt:
        problemas_forma.append("ABNT NBR 10520: citação longa recuada entre aspas externas")
    if not conf_estilo:
        problemas_forma.append("Diretrizes CNJ/AMB: 'legítima defesa da honra' sem repúdio ou solecismo forense")
    if not conf_oito_circunstancias:
        if vetores_ausentes:
            problemas_forma.append(
                f"Art. 59 do CP: ausência de enunciação das circunstâncias judiciais: {', '.join(vetores_ausentes)}"
            )
        else:
            problemas_forma.append(
                "Art. 59 do CP: ausência de valoração expressa das circunstâncias judiciais: "
                f"{', '.join(vetores_sem_valoracao)}"
            )

    detalhes = [
        {
            "camada": "1. Ancoragem Probatória de IDs/Folhas",
            "status": "APROVADO" if not ids_falsos else "ALERTA",
            "descricao": f"{len(ids_ok)} IDs de peças e mídias verificados nos autos; {len(ids_falsos)} IDs inexistentes.",
        },
        {
            "camada": "2. Blindagem Jurisprudencial e Súmulas (Base Fechada)",
            "status": "APROVADO" if not citacoes_invalidas else "BLOQUEADO",
            "descricao": f"{len(citacoes_ok)} Súmulas/Temas/Precedentes conferidos literalmente; {len(citacoes_invalidas)} citações externas bloqueadas.",
        },
        {
            "camada": "3. Completude de Enfrentamento Defensivo (Art. 381, III, CPP)",
            "status": "APROVADO" if not teses_omitidas else "OMISSÃO DETECTADA",
            "descricao": f"{len(teses_respondidas)}/{len(teses_respondidas) + len(teses_omitidas)} preliminares e teses de mérito respondidas individualmente.",
        },
        {
            "camada": "4. Padronização Forense, Art. 59 CP, ABNT, Datas e Diretrizes CNJ/AMB",
            "status": "APROVADO" if not problemas_forma else "REVISAR",
            "descricao": (
                "Art. 381 CPP, Art. 21 LMP, LC 95/98, ABNT NBR 10520, padrão brasileiro de datas e todas as 8 "
                "circunstâncias judiciais do art. 59 do CP expressamente enunciadas e valoradas."
                if not problemas_forma
                else "; ".join(problemas_forma) + "."
            ),
        },
        {
            "camada": "5. Verificação de Conteúdo (Marcadores Pendentes e Penas x Dosimetria)",
            "status": "APROVADO" if (conf_marcadores and conf_penas) else "REPROVADO",
            "descricao": (
                (
                    f"{len(marcadores)} marcador(es) [COMPLETAR PELO JUÍZO] pendente(s) — o magistrado deve "
                    "complementar esses trechos antes da assinatura. "
                    if marcadores
                    else "Nenhum marcador [COMPLETAR PELO JUÍZO] pendente. "
                )
                + desc_penas
            ),
        },
    ]
    if secoes_fb:
        detalhes.append(
            {
                "camada": "6. Seções Substituídas por Modelo Determinístico (Fallback)",
                "status": "SINALIZADO",
                "descricao": (
                    "As seguintes seções NÃO são do texto original: foram substituídas pelo modelo "
                    f"determinístico: {', '.join(secoes_fb)}."
                ),
            }
        )

    verificacoes_conteudo: List[Dict[str, Any]] = [
        {
            "verificacao": "ids_autos",
            "status": "APROVADO" if not ids_falsos else "REPROVADO",
            "descricao": f"{len(ids_ok)} ID(s) citado(s) conferido(s) nos autos; {len(ids_falsos)} inexistente(s).",
            "itens": list(ids_falsos),
        },
        {
            "verificacao": "citacoes_base_fechada",
            "status": "APROVADO" if not citacoes_invalidas else "REPROVADO",
            "descricao": f"{len(citacoes_ok)} citação(ões) conferida(s); {len(citacoes_invalidas)} fora da base fechada.",
            "itens": list(citacoes_invalidas),
        },
        {
            "verificacao": "teses_defensivas",
            "status": "APROVADO" if not teses_omitidas else "REPROVADO",
            "descricao": f"{len(teses_respondidas)} tese(s) enfrentada(s); {len(teses_omitidas)} omitida(s).",
            "itens": list(teses_omitidas),
        },
        {
            "verificacao": "marcadores_completar_pelo_juizo",
            "status": "APROVADO" if conf_marcadores else "REPROVADO",
            "descricao": (
                "Nenhum marcador pendente."
                if conf_marcadores
                else f"{len(marcadores)} trecho(s) aguardando complementação pelo juízo."
            ),
            "itens": list(marcadores),
        },
        {
            "verificacao": "penas_dispositivo_x_dosimetria",
            "status": status_penas,
            "descricao": desc_penas,
            "itens": list(penas_divergentes),
        },
        {
            "verificacao": "secoes_fallback",
            "status": "SINALIZADO" if secoes_fb else "NAO_APLICAVEL",
            "descricao": (
                f"Seções substituídas pelo modelo determinístico: {', '.join(secoes_fb)}."
                if secoes_fb
                else "Nenhuma seção substituída por modelo determinístico."
            ),
            "itens": list(secoes_fb),
        },
    ]

    return RelatorioAuditoriaSentenca(
        aprovada_sem_alucinacao=aprovada,
        score_confiabilidade=score,
        ids_citados_verificados=ids_ok,
        ids_inexistentes=ids_falsos,
        citacoes_juridicas_verificadas=citacoes_ok,
        citacoes_nao_autorizadas=citacoes_invalidas,
        teses_defesa_enfrentadas=teses_respondidas,
        teses_omitidas=teses_omitidas,
        conformidade_art381_cpp=conf_art381,
        conformidade_art21_lmp=conf_art21_lmp,
        conformidade_lc95_98=conf_lc95,
        conformidade_datas_padrao_br=conf_datas,
        conformidade_abnt_citacoes=conf_abnt,
        conformidade_estilo_cnj_amb=conf_estilo,
        conformidade_oito_circunstancias_art59=conf_oito_circunstancias,
        detalhes_camadas=detalhes,
        score_forma=score_forma,
        verificacoes_conteudo=verificacoes_conteudo,
        secoes_fallback=secoes_fb,
        penas_divergentes=penas_divergentes,
        marcadores_pendentes=marcadores,
        conformidade_marcadores_pendentes=conf_marcadores,
        conformidade_penas_dosimetria=conf_penas,
    )


# ==============================================================================
# 7. AUDITORIA SECCIONAL GRANULAR (M3) — art. 381, III, CPP / CNJ 492/2023 / Base Fechada
# ==============================================================================


def auditar_secao(
    secao: Any,
    texto_secao: str,
    pecas_autos: List[Any],
    teses_defesa: List[Any],
    ids_midias: List[str] | None = None,
    texto_sentenca_completo: str | None = None,
    comandos_dispositivo: ComandosDispositivo | None = None,
) -> RelatorioAuditoriaSecao:
    """
    Auditoria anti-alucinação granular de UMA seção do pipeline (M3).

    Aplicação das camadas por seção:

    +----------------+--------+---------+----------+----------+--------+---------+
    | Camada         | RELAT. | PRELIM. | MÉRITO   | DOSIM.   | DISP.  | Regra   |
    +================+========+=========+==========+==========+========+=========+
    | 1 — IDs        |   sim  |   sim   |   sim    |   sim    |  sim   | sempre  |
    | 2 — Base       |   sim  |   sim   |   sim    |   sim    |  sim   | sempre  |
    | 3 — Teses      |   não  | PRELIM. | MÉRITO   | SUBSID.  |  não   | art.381 |
    | 4 — Datas      |   sim  |   sim   |   sim    |   sim    |  sim   | sempre  |
    | 4 — ABNT       |   sim  |   sim   |   sim    |   sim    |  sim   | sempre  |
    | 4 — Estilo     |   sim  |   sim   |   sim    |   sim    |  sim   | sempre  |
    | 4 — LC 95/98   |   sim  |   sim   |   sim    |   sim    |  sim   | sempre  |
    | 4 — Art. 59    |   n/a  |   n/a   |   n/a    |   sim    |  n/a   | dosim.  |
    | 4 — Art. 21    |   n/a  |   n/a   |   n/a    |   n/a    |  sim   | disp.   |
    | 4 — Art. 381   | texto integral fornecido (estrutura canônica I/II/III)       |
    +----------------+--------+---------+----------+----------+--------+---------+

    Parâmetros
    ----------
    secao:
        ``SecaoTipo`` (Enum do M1) ou string canônica
        (``relatorio|preliminares|merito|dosimetria|dispositivo``).
    texto_secao:
        Texto da minuta daquela seção (rascunho ou seção selada).
    pecas_autos:
        Peças dos autos (``PecaClassificada``), IDs em string ou dicionários com
        ``doc_id``. O universo fechado da Camada 1 é a união destes com ``ids_midias``.
    teses_defesa:
        Teses defensivas heterogêneas (``TeseDefensivaItem``, dicts ou strings no formato
        ``"PREL-01: Título - Descrição"`` do dossiê seccional).
    ids_midias:
        IDs de mídias audiovisuais autorizados (PJe Mídias).
    texto_sentenca_completo:
        Quando fornecido (ex.: validação final da sentença montada), ativa a checagem
        estrutural do art. 381, I a III, do CPP no texto integral. Quando ``None``, essa
        checagem é considerada não aplicável e não penaliza a seção.

    Retorno
    -------
    ``RelatorioAuditoriaSecao`` com score determinístico (0.0 a 100.0), erros,
    feedbacks cirúrgicos e conformidades. ``aprovado`` exige score 100.0 e todas as
    conformidades aplicáveis satisfeitas.
    """
    secao_nome = _normalizar_secao(secao)
    texto = texto_secao or ""

    erros: List[str] = []
    feedbacks: List[str] = []
    detalhes: List[Dict[str, str]] = []

    # Sanidade mínima: seção vazia ou insuficiente reprova imediatamente (score 0.0).
    if len(texto.strip()) < 40:
        return RelatorioAuditoriaSecao(
            secao=secao_nome,
            aprovado=False,
            score=0.0,
            erros=["Seção vazia ou insuficiente (menos de 40 caracteres) para auditoria forense."],
            feedbacks_cirurgicos=[
                "Redija integralmente a seção com fundamentação fática e jurídica substantiva antes de submetê-la ao crivo."
            ],
            detalhes_camadas=[
                {
                    "camada": "0. Sanidade Textual",
                    "status": "REPROVADO",
                    "descricao": "Texto vazio ou excessivamente curto.",
                }
            ],
        )

    # ------------------------------------------------------------------
    # Camada 1 — Ancoragem Probatória de IDs/Folhas (universo fechado)
    # ------------------------------------------------------------------
    ids_validos = _coletar_ids_validos(pecas_autos, ids_midias)
    ids_ok, ids_falsos = _auditar_ids(
        texto,
        ids_validos,
        padrao=_PADRAO_ID_SECAO,
        case_insensitive=True,
    )
    if ids_falsos:
        erros.append(
            "Camada 1 — Ancoragem Probatória: IDs inexistentes ou não autorizados citados "
            f"na seção: {', '.join(ids_falsos)}."
        )
        feedbacks.append(
            f"Remova ou substitua os IDs {', '.join(ids_falsos)} por IDs reais do índice de "
            "peças/mídias dos autos (proibida a citação de documento inexistente)."
        )
    detalhes.append(
        {
            "camada": "1. Ancoragem Probatória de IDs/Folhas",
            "status": "APROVADO" if not ids_falsos else "ALERTA",
            "descricao": f"{len(ids_ok)} IDs verificados no universo fechado da seção; "
            f"{len(ids_falsos)} IDs inexistentes.",
        }
    )

    # ------------------------------------------------------------------
    # Camada 2 — Blindagem Jurisprudencial (Base Fechada)
    # ------------------------------------------------------------------
    indice = _construir_indice_base_fechada()
    citacoes_ok, citacoes_invalidas = _auditar_citacoes_juridicas(texto, indice)
    if citacoes_invalidas:
        erros.append(
            "Camada 2 — Base Fechada: citações não autorizadas (Súmulas, Temas, acórdãos ou "
            f"normas fora do acervo verificado): {', '.join(citacoes_invalidas)}."
        )
        feedbacks.append(
            f"Substitua ou elimine as citações não constantes da base fechada: "
            f"{', '.join(citacoes_invalidas)}. É vedada a invocação de precedente inexistente."
        )
    detalhes.append(
        {
            "camada": "2. Blindagem Jurisprudencial e Súmulas (Base Fechada)",
            "status": "APROVADO" if not citacoes_invalidas else "BLOQUEADO",
            "descricao": f"{len(citacoes_ok)} citações conferidas na base fechada; "
            f"{len(citacoes_invalidas)} citações externas bloqueadas.",
        }
    )

    # ------------------------------------------------------------------
    # Camada 3 — Enfrentamento das teses aplicáveis (art. 381, III, CPP)
    # ------------------------------------------------------------------
    teses_normalizadas = _normalizar_teses(teses_defesa)
    teses_aplicaveis = _filtrar_teses_aplicaveis_secao(secao_nome, teses_normalizadas)
    teses_enfrentadas, teses_omitidas = _auditar_teses_seccionais(texto, teses_aplicaveis)
    referencias_vedadas = {t.referencia for t in teses_aplicaveis if t.tipo == "VEDADA"}
    for tese_omitida in teses_omitidas:
        if any(tese_omitida.startswith(ref) for ref in referencias_vedadas):
            erros.append(f"Camada 3 — Ausência de repúdio expresso à tese vedada {tese_omitida}.")
            feedbacks.append("Repudie expressamente a legítima defesa da honra, nos termos da ADPF 779/STF.")
            continue
        erros.append(
            "Camada 3 — Art. 381, III, CPP: omissão de enfrentamento da tese defensiva "
            f"{tese_omitida}."
        )
        feedbacks.append(
            f"Enfrente motivadamente a tese defensiva {tese_omitida}, refutando-a ou "
            "acolhendo-a com fundamentação fática e jurídica expressa (art. 381, III, do CPP)."
        )

    evasivas = _detectar_formulas_evasivas(texto)
    conf_evasiva = len(evasivas) == 0
    if evasivas:
        erros.append(
            "Camada 3 — Art. 381, III, CPP: fórmula evasiva de não enfrentamento detectada "
            f"({', '.join(evasivas)}); a tese não pode ser tangenciada nem preterida."
        )
        feedbacks.append(
            "Expurgue as fórmulas evasivas e enfrente expressamente cada tese defensiva "
            "aplicável à seção, com deliberação fundamentada."
        )
    detalhes.append(
        {
            "camada": "3. Completude de Enfrentamento Defensivo (Art. 381, III, CPP)",
            "status": "APROVADO" if (not teses_omitidas and conf_evasiva) else "OMISSÃO DETECTADA",
            "descricao": f"{len(teses_enfrentadas)}/{len(teses_aplicaveis)} teses aplicáveis "
            f"enfrentadas; {len(teses_omitidas)} omissões; {len(evasivas)} fórmulas evasivas.",
        }
    )

    # ------------------------------------------------------------------
    # Camada 4 — Conformidade formal e normativa
    # ------------------------------------------------------------------
    conf_art381 = _avaliar_art381_cpp(texto_sentenca_completo) if texto_sentenca_completo else True
    if texto_sentenca_completo and not conf_art381:
        erros.append(
            "Camada 4 — Art. 381, I a III, CPP: estrutura canônica incompleta no texto "
            "integral da sentença (ausência de 'I – RELATÓRIO', 'II – FUNDAMENTAÇÃO' ou "
            "'III – DISPOSITIVO')."
        )
        feedbacks.append(
            "Recomponha a sentença nos três blocos canônicos exigidos pelo art. 381 do CPP."
        )

    # Art. 21 da LMP: exigível apenas no DISPOSITIVO (comando de intimação pessoal da ofendida).
    conf_art21 = _avaliar_art21_lmp(texto) if secao_nome == "dispositivo" else True
    if secao_nome == "dispositivo" and not conf_art21:
        erros.append(
            "Camada 4 — Art. 21 da Lei nº 11.340/2006: omissão da determinação de "
            "intimação pessoal da ofendida acerca da sentença."
        )
        feedbacks.append(
            "Determine expressamente a intimação pessoal da ofendida nos termos do art. 21 "
            "da Lei nº 11.340/2006."
        )

    conf_lc95 = _avaliar_lc95_98(texto)
    if not conf_lc95:
        erros.append(
            "Camada 4 — LC 95/98: grafia incorreta de artigos de lei detectada "
            "(ex.: 'art. 129º' em vez de 'art. 129')."
        )
        feedbacks.append("Padronize a grafia dos artigos de lei conforme a LC 95/98.")

    conf_datas = _avaliar_datas_padrao_br(texto)
    if not conf_datas:
        datas_fora = re.findall(r"\b(?:19|20)\d{2}[-/]\d{2}[-/]\d{2}\b", texto)
        datas_fora += re.findall(r"\b\d{2}-\d{2}-(?:19|20)\d{2}\b", texto)
        erros.append(
            "Camada 4 — Padrão de Datas BR: data(s) em formato estrangeiro (ISO) na "
            f"narrativa: {', '.join(sorted(set(datas_fora)))}. Use DD/MM/AAAA ou por extenso."
        )
        feedbacks.append("Converta as datas estrangeiras para o padrão brasileiro DD/MM/AAAA ou por extenso.")

    conf_abnt = _avaliar_abnt_citacoes(texto)
    if not conf_abnt:
        erros.append(
            "Camada 4 — ABNT NBR 10520: citação direta longa com recuo de 4 espaços não "
            "pode vir entre aspas externas."
        )
        feedbacks.append("Remova as aspas externas dos blocos de citação longa recuados (ABNT NBR 10520).")

    conf_estilo = _avaliar_estilo_cnj_amb(
        texto,
        permitir_relato=secao_nome == "relatorio" and any(t.tipo == "VEDADA" for t in teses_normalizadas),
    )
    if not conf_estilo:
        erros.append(
            "Camada 4 — Diretrizes CNJ/AMB e Resolução CNJ nº 492/2023: uso de 'legítima "
            "defesa da honra' sem repúdio expresso (ADPF 779/STF) ou solecismo forense."
        )
        feedbacks.append(
            "Afasta expressamente a tese de legítima defesa da honra (ADPF 779/STF) e "
            "expurgue solecismos, julgando com perspectiva de gênero (Resolução CNJ nº 492/2023)."
        )

    if secao_nome == "dosimetria":
        dos_norm_sec = _remover_acentos(texto.lower())
        if (
            (texto_sentenca_completo and _eh_absolvicao_ou_extincao_integral(texto_sentenca_completo))
            or (
                "prejudicada" in dos_norm_sec
                and ("absolvicao" in dos_norm_sec or "extincao da punibilidade" in dos_norm_sec)
            )
        ):
            conf_oito = True
            vetores_ausentes = []
            vetores_sem_valoracao = []
        else:
            (
                conf_oito,
                vetores_ausentes,
                vetores_sem_valoracao,
            ) = _avaliar_oito_circunstancias_art59(texto, tolerante_acentos=True)
        if not conf_oito:
            if vetores_ausentes:
                erros.append(
                    "Camada 4 — Art. 59 do CP: ausência de enunciação das circunstâncias "
                    f"judiciais: {', '.join(vetores_ausentes)}."
                )
                feedbacks.append(
                    f"Enuncie e valore todas as 8 circunstâncias judiciais do art. 59 do CP "
                    f"(ausentes: {', '.join(vetores_ausentes)})."
                )
            else:
                erros.append(
                    "Camada 4 — Art. 59 do CP: ausência de valoração expressa das "
                    f"circunstâncias judiciais: {', '.join(vetores_sem_valoracao)}."
                )
                feedbacks.append(
                    "Valore expressamente cada vetor do art. 59 do CP (favorável, "
                    "desfavorável ou neutro), vedada a mera enunciação nominal."
                )
    else:
        conf_oito = True
        vetores_ausentes = []
        vetores_sem_valoracao = []

    divergencias = (
        verificar_comandos_dispositivo(texto, comandos_dispositivo)
        if secao_nome == "dispositivo" and comandos_dispositivo is not None
        else []
    )
    erros.extend(divergencias)
    feedbacks.extend(divergencias)

    # Conteúdo — marcadores de complementação pelo juízo ainda pendentes.
    marcadores = _detectar_marcadores_pendentes(texto)
    conf_marcadores = not marcadores
    if marcadores:
        erros.append(
            "Conteúdo — marcador(es) de complementação obrigatória pendente(s): "
            f"{'; '.join(marcadores)}. A minuta depende de dado ou decisão que só o juízo pode fornecer."
        )
        feedbacks.append(
            "Substitua cada marcador [COMPLETAR PELO JUÍZO: ...] pelo conteúdo efetivamente decidido "
            "pelo magistrado (não invente o dado faltante)."
        )

    detalhes.append(
        {
            "camada": "4. Padronização Forense, Art. 59 CP, ABNT, Datas e Diretrizes CNJ/AMB",
            "status": "APROVADO"
            if (conf_art381 and conf_art21 and conf_lc95 and conf_datas and conf_abnt and conf_estilo and conf_oito)
            else "REVISAR",
            "descricao": (
                f"Art. 381 CPP: {'ok' if conf_art381 else 'estrutura incompleta'}; "
                f"Art. 21 LMP: {'ok' if conf_art21 else 'omissão'}; "
                f"LC 95/98: {'ok' if conf_lc95 else 'violação'}; "
                f"datas BR: {'ok' if conf_datas else 'violação'}; "
                f"ABNT NBR 10520: {'ok' if conf_abnt else 'violação'}; "
                f"CNJ/AMB: {'ok' if conf_estilo else 'violação'}; "
                f"art. 59 CP: {'ok' if conf_oito else 'incompleto'}"
                + (f" (ausentes: {', '.join(vetores_ausentes)})" if vetores_ausentes else "")
                + (
                    f" (sem valoração: {', '.join(vetores_sem_valoracao)})"
                    if vetores_sem_valoracao
                    else ""
                )
                + "."
            ),
        }
    )

    # ------------------------------------------------------------------
    # Score determinístico e veredito
    # ------------------------------------------------------------------
    score = 100.0
    score -= len(ids_falsos) * 25.0
    score -= len(citacoes_invalidas) * 25.0
    score -= len(teses_omitidas) * 20.0
    if not conf_art381:
        score -= 25.0
    if not conf_art21:
        score -= 15.0
    if not conf_lc95:
        score -= 5.0
    if not conf_datas:
        score -= 10.0
    if not conf_abnt:
        score -= 10.0
    if not conf_estilo:
        score -= 10.0
    if not conf_oito:
        score -= 20.0
    if not conf_evasiva:
        score -= 15.0
    score -= len(marcadores) * 15.0
    score -= len(divergencias) * 25.0

    aprovado = (
        not divergencias and
        conf_marcadores and
        len(ids_falsos) == 0
        and len(citacoes_invalidas) == 0
        and len(teses_omitidas) == 0
        and conf_art381
        and conf_art21
        and conf_lc95
        and conf_datas
        and conf_abnt
        and conf_estilo
        and conf_oito
        and conf_evasiva
    )

    return RelatorioAuditoriaSecao(
        secao=secao_nome,
        aprovado=aprovado,
        score=max(0.0, round(score, 1)),
        erros=erros,
        feedbacks_cirurgicos=feedbacks,
        ids_inexistentes=ids_falsos,
        ids_citados_verificados=ids_ok,
        citacoes_nao_autorizadas=citacoes_invalidas,
        citacoes_juridicas_verificadas=citacoes_ok,
        teses_omitidas=teses_omitidas,
        teses_defesa_enfrentadas=teses_enfrentadas,
        conformidade_art381_cpp=conf_art381,
        conformidade_art21_lmp=conf_art21,
        conformidade_lc95_98=conf_lc95,
        conformidade_datas_padrao_br=conf_datas,
        conformidade_abnt_citacoes=conf_abnt,
        conformidade_estilo_cnj_amb=conf_estilo,
        conformidade_oito_circunstancias_art59=conf_oito,
        conformidade_fundamentacao_evasiva=conf_evasiva,
        marcadores_pendentes=marcadores,
        conformidade_marcadores_pendentes=conf_marcadores,
        detalhes_camadas=detalhes,
    )


# ==============================================================================
# 8. WRAPPERS SECCIONAIS EXPLÍCITOS (M3)
# ==============================================================================


def auditar_secao_relatorio(
    texto_secao: str,
    pecas_autos: List[Any],
    teses_defesa: List[Any],
    ids_midias: List[str] | None = None,
    texto_sentenca_completo: str | None = None,
) -> RelatorioAuditoriaSecao:
    """Audita a seção RELATÓRIO (Camadas 1, 2 e 4; sem trava de teses — narrativa pura)."""
    return auditar_secao(
        "relatorio", texto_secao, pecas_autos, teses_defesa,
        ids_midias=ids_midias, texto_sentenca_completo=texto_sentenca_completo,
    )


def auditar_secao_preliminares(
    texto_secao: str,
    pecas_autos: List[Any],
    teses_defesa: List[Any],
    ids_midias: List[str] | None = None,
    texto_sentenca_completo: str | None = None,
) -> RelatorioAuditoriaSecao:
    """Audita a seção PRELIMINARES (Camadas 1, 2, 3 para teses PRELIMINARES e 4)."""
    return auditar_secao(
        "preliminares", texto_secao, pecas_autos, teses_defesa,
        ids_midias=ids_midias, texto_sentenca_completo=texto_sentenca_completo,
    )


def auditar_secao_merito(
    texto_secao: str,
    pecas_autos: List[Any],
    teses_defesa: List[Any],
    ids_midias: List[str] | None = None,
    texto_sentenca_completo: str | None = None,
) -> RelatorioAuditoriaSecao:
    """Audita a seção MÉRITO (Camadas 1, 2, 3 para teses de MÉRITO e 4)."""
    return auditar_secao(
        "merito", texto_secao, pecas_autos, teses_defesa,
        ids_midias=ids_midias, texto_sentenca_completo=texto_sentenca_completo,
    )


def auditar_secao_dosimetria(
    texto_secao: str,
    pecas_autos: List[Any],
    teses_defesa: List[Any],
    ids_midias: List[str] | None = None,
    texto_sentenca_completo: str | None = None,
) -> RelatorioAuditoriaSecao:
    """Audita a seção DOSIMETRIA (Camadas 1, 2, 3 para teses SUBSIDIÁRIAS e 4 com os 8 vetores do art. 59)."""
    return auditar_secao(
        "dosimetria", texto_secao, pecas_autos, teses_defesa,
        ids_midias=ids_midias, texto_sentenca_completo=texto_sentenca_completo,
    )


def auditar_secao_dispositivo(
    texto_secao: str,
    pecas_autos: List[Any],
    teses_defesa: List[Any],
    ids_midias: List[str] | None = None,
    texto_sentenca_completo: str | None = None,
) -> RelatorioAuditoriaSecao:
    """Audita a seção DISPOSITIVO (Camadas 1, 2 e 4, com art. 21 da Lei nº 11.340/2006)."""
    return auditar_secao(
        "dispositivo", texto_secao, pecas_autos, teses_defesa,
        ids_midias=ids_midias, texto_sentenca_completo=texto_sentenca_completo,
    )
