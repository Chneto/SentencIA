# -*- coding: utf-8 -*-
"""
Orquestrador Modular dos Motores Python e Gerador de Script Estruturado para IA (Gemini / Local).

Separa os 5 motores determinísticos em Python por função para que angariem e estruturem
100% das informações fáticas, probatórias, jurídicas e matemáticas antes da redação textual
pelo modelo de IA (Gemini API / Gemini Web / LLM Local), incluindo suporte nativo a
Pseudonimização Reversível (Segredo de Justiça / LGPD — Resolução CNJ nº 615/2025).

v2.0.0:

- Os motores usam ``app.core.case_context.obter_contexto`` (classificação/metadados/matriz oral
  calculados uma única vez por conteúdo) e a flag demo explícita (``bundle["is_demo"]``).
- Dados essenciais ausentes (data do fato, recebimento da denúncia) levantam
  ``DadosInsuficientesError`` — nada de datas fictícias.
- Caso real: o Script exige veredicto explícito por crime (``DecisaoJudicialPendenteError``),
  porque o Script instrui a IA a redigir a decisão.
- Pseudonimização: cobre nº CNJ dos autos, prenomes isolados (inclusive em nomes de arquivo de
  mídia), CPF, RG, telefones, endereços, testemunhas e descendentes. O mapa é DETERMINÍSTICO
  (independe da ordem das peças/mídias), acompanha o Script com ``mapa_hash`` (sha256) e pode ser
  devolvido na importação; se não for, é reconstruído da mesma forma.
"""

import datetime
import difflib
import hashlib
import logging
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple
from pydantic import BaseModel, Field

from app.core.case_context import CaseContext
from app.core.config import load_settings
from app.core.errors import EnvioExternoNaoAutorizadoError
from app.core.text_utils import normalizar, remover_acentos
from app.generation.date_formatter import formatar_data_br, formatar_moeda_com_extenso
from app.generation.decisoes_imputacao import serializar_decisoes_imputacao
from app.generation.dispositivo import construir_comandos_dispositivo
from app.generation.sentence_generator import (
    PlanoSentenca,
    planejar_sentenca,
    prescricao_exige_topico,
    texto_dano_moral_dispositivo,
    texto_multa_dispositivo,
    texto_regime_dispositivo,
)
from app.ingestion.laya_classifier import (
    CATEGORIAS_SEM_FONTE_DE_FATOS,
    MetadadosProcesso,
    PecaClassificada,
)
from app.knowledge.knowledge_base import (
    DOUTRINA_VERIFICADA,
    SUMULAS_E_TEMAS_VERIFICADOS,
    get_all_knowledge_base,
    item_citavel_automaticamente,
)
from app.knowledge.style_cloner import get_style_profile
from app.legal_engine.dosimetria_calculator import (
    DosimetriaGlobalOutput,
    PrescricaoOutput,
    formatar_especie_pena,
)
from app.transcription.oral_proof_analyzer import (
    MatrizCorroboracaoOral,
    consolidar_prova_oral_por_interlocutor,
)

#: Veredictos válidos por crime (decisão exclusiva do juiz).


class StatusMotorPy(BaseModel):
    codigo: str
    nome: str
    funcao: str
    status: str = "CONCLUIDO"
    resumo_execucao: str
    itens_processados: int = 0


class ScriptCasoEstruturado(BaseModel):
    numero_processo: str
    gerado_em: str
    pseudonimizado: bool
    densidade: str = "compacto"
    pecas_deduplicadas_removidas: int = 0
    mapa_reversao: Dict[str, str] = Field(default_factory=dict)
    #: sha256 do mapa de reversão (JSON canônico) — permite conferir, na importação, que o mapa
    #: devolvido é o mesmo emitido com o Script.
    mapa_hash: str = ""
    motores_status: List[StatusMotorPy] = Field(default_factory=list)
    script_markdown: str
    script_json: Dict[str, Any] = Field(default_factory=dict)
    alertas: List[str] = Field(default_factory=list)


#: Alias local mantido por compatibilidade (fonte única: ``app.core.text_utils``).
_norm_sem_acento = remover_acentos


def _limpar_rodapes_pje_texto(txt: str) -> str:
    raw = txt or ""
    raw = re.sub(r"^\s*\d*\s*Num\.\s*\d+\s*-\s*P[áa]g\.\s*\d+[^\n]*$", " ", raw, flags=re.IGNORECASE | re.MULTILINE)
    raw = re.sub(r"P[áa]g\.\s*Total\s*[-–]?\s*\d+[^\n]*", " ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"Assinado\s+eletronicamente\s+por:[^\n]*", " ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"N[úu]mero\s+do\s+documento:\s*\d+", " ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"https?://\S+", " ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\[(?:P[áa]g\.|OCR\s+P[áa]gina)\s*\d+(?:\s*-\s*OCR)?\]", " ", raw, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", raw).strip()


def _limpar_titulo_para_dedup(titulo: str) -> str:
    t = re.sub(r"\([^)]*\b(?:ID-|fls?\.|p[áa]g)[^)]*\)", "", titulo or "", flags=re.IGNORECASE)
    t = _norm_sem_acento(t).lower()
    return re.sub(r"\s+", " ", t).strip(" -–—:")


def deduplicar_pecas_equivalentes(
    pecas: List[PecaClassificada],
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Agrupa peças equivalentes entre APF e Inquérito Policial (ou cópias idênticas nos autos)
    por hash e similaridade normalizada de texto/título, unificando as referências de ID
    (ex.: ``ID-4100101-SUB-05 / ID-4100202-SUB-05``) sem perder nenhuma prova.
    """
    grupos: List[Dict[str, Any]] = []
    removidas = 0
    sub_parents = {p.doc_id.split("-SUB-")[0] for p in pecas if "-SUB-" in p.doc_id}

    for p in pecas:
        is_sub_parent = p.doc_id in sub_parents
        norm_tit = _limpar_titulo_para_dedup(p.titulo)
        clean_body = _limpar_rodapes_pje_texto(p.conteudo or "")
        norm_body = _norm_sem_acento(clean_body).lower()
        body_hash = hashlib.md5(norm_body.encode("utf-8"), usedforsecurity=False).hexdigest()

        matched_group: Optional[Dict[str, Any]] = None
        if not is_sub_parent and len(norm_body) >= 80:
            for grp in grupos:
                if grp["is_sub_parent"]:
                    continue
                if grp["hash"] == body_hash:
                    matched_group = grp
                    break
                same_title = bool(norm_tit and norm_tit == grp["norm_tit"])
                same_family = p.categoria == grp["primary"].categoria or (
                    {p.categoria, grp["primary"].categoria} <= {"BO", "LAUDO_PERICIAL"}
                )
                if same_title and same_family:
                    len_a, len_b = len(norm_body), len(grp["norm_body"])
                    if min(len_a, len_b) / max(len_a, len_b, 1) >= 0.72:
                        sm = difflib.SequenceMatcher(None, norm_body[:2800], grp["norm_body"][:2800])
                        if sm.quick_ratio() >= 0.84 and sm.ratio() >= 0.80:
                            matched_group = grp
                            break

        if matched_group is not None:
            if p.doc_id not in matched_group["doc_ids"]:
                matched_group["doc_ids"].append(p.doc_id)
                matched_group["folhas"].append(p.folha)
                matched_group["pecas"].append(p)
                removidas += 1
        else:
            grupos.append(
                {
                    "primary": p,
                    "pecas": [p],
                    "doc_ids": [p.doc_id],
                    "folhas": [p.folha],
                    "norm_tit": norm_tit,
                    "norm_body": norm_body,
                    "clean_body": clean_body,
                    "hash": body_hash,
                    "is_sub_parent": is_sub_parent,
                }
            )

    for grp in grupos:
        grp["unified_id"] = " / ".join(grp["doc_ids"])
        grp["unified_folha"] = " / ".join(dict.fromkeys(grp["folhas"]))

    return grupos, removidas


_STOPWORDS_PESSOA_PSEUDO = {
    "SEGREDO", "JUSTIÇA", "JUSTICA", "MINISTÉRIO", "MINISTERIO", "PÚBLICO", "PUBLICO",
    "POLICIAL", "MILITAR", "DELEGADO", "DELEGADA", "PROMOTOR", "PROMOTORA",
    "DEFENSOR", "DEFENSORA", "JUIZ", "JUIZA", "OFICIAL", "VITIMA", "VÍTIMA",
    "OFENDIDA", "ACUSADO", "REU", "RÉU", "TESTEMUNHA", "DEPOENTE", "DECLARANTE",
    "AUTOR", "AUTUADO", "CONDUZIDO", "NÃO", "NAO", "CONSTA",
}

#: Prenomes que não podem ser pseudonimizados isoladamente (palavras comuns/forenses).
_PRENOMES_NAO_ISOLAVEIS = {
    "DE", "DA", "DO", "DOS", "DAS", "E", "SR", "SRA", "DR", "DRA", "DONA", "SEU",
}

#: Expressões públicas que contêm prenomes comuns e NUNCA devem ser pseudonimizadas
#: (ex.: "Lei Maria da Penha"). Autores da doutrina autorizada são acrescentados em tempo de execução.
_EXPRESSOES_PROTEGIDAS_BASE = ("Maria da Penha",)

#: Número único CNJ (Resolução CNJ nº 65/2008): NNNNNNN-DD.AAAA.J.TR.OOOO.
_PADRAO_NUMERO_CNJ = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
#: Número CNJ SEM pontuação (20 dígitos corridos).
_PADRAO_NUMERO_CNJ_PLANO = re.compile(r"(?<!\d)\d{20}(?!\d)")
#: CPF sem pontuação, apenas quando rotulado (11 dígitos corridos sem rótulo poderiam ser outro número).
_PADRAO_CPF_PLANO_ROTULADO = re.compile(r"\bCPF\s*(?:n[º°\.]*\s*)?[:\-]?\s*(\d{11})(?!\d)", re.IGNORECASE)
#: Filiação ("filha de Joana Batista Lima e Pedro Souza Lima"): nomes com iniciais maiúsculas.
_NOME_PROPRIO = r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+(?:\s+(?:d[aeo]s?\s+)?[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+){1,5}"
_PADRAO_FILIACAO = re.compile(
    rf"\b[Ff]ilh[oa]s?\s+d[eo]\s+({_NOME_PROPRIO})(?:\s+e\s+(?:de\s+)?({_NOME_PROPRIO}))?"
)


def _cnj_formatado(valor: str) -> str:
    """Formata 20 dígitos no padrão CNJ (NNNNNNN-DD.AAAA.J.TR.OOOO); devolve o valor original se não casar."""
    d = re.sub(r"\D", "", valor or "")
    if len(d) == 20 and d == (valor or "").strip():
        return f"{d[:7]}-{d[7:9]}.{d[9:13]}.{d[13]}.{d[14:16]}.{d[16:]}"
    return (valor or "").strip()

#: Fronteira de palavra que trata "_" como separador (nomes em nomes de arquivo: "Vítima Fernanda_001").
_LETRA = r"0-9A-Za-zÀ-ÖØ-öø-ÿ"
_PRE = rf"(?<![{_LETRA}])"
_POS = rf"(?![{_LETRA}])"


def _eh_nome_identificado(valor: Optional[str], marcador: str) -> bool:
    texto = (valor or "").strip()
    return bool(texto) and marcador not in texto.upper()


def _numeros_cnj_publicos() -> set:
    """Números CNJ que constam da Base Fechada (precedentes públicos): não são dados do caso."""
    publicos: set = set()
    try:
        kb = get_all_knowledge_base()
        for item in list(kb.get("sumulas_e_temas", [])) + list(kb.get("precedentes_gabinete", [])):
            for campo in ("referencia", "texto_literal", "fonte_oficial", "titulo"):
                publicos.update(_PADRAO_NUMERO_CNJ.findall(str(item.get(campo, "") or "")))
    except Exception as err:  # pragma: no cover - acervo indisponível não impede a pseudonimização
        logging.getLogger(__name__).debug("Acervo indisponível para CNJs públicos: %s", err)
    return publicos


def _expressoes_protegidas() -> List[str]:
    expressoes = list(_EXPRESSOES_PROTEGIDAS_BASE)
    for d in DOUTRINA_VERIFICADA:
        autor = str(d.get("autor", "") or "")
        for parte in re.split(r"\s+e\s+|;", autor):
            parte = parte.strip()
            if len(parte.split()) >= 2:
                expressoes.append(parte)
    # Mais longas primeiro, sem duplicatas (determinístico).
    return sorted(set(expressoes), key=lambda s: (-len(s), s))


def _limpar_nome_testemunha(bruto: Any) -> str:
    cand = re.sub(r"\s*\(Parte\s+\d+\)\s*$", "", str(bruto or "").strip(), flags=re.IGNORECASE)
    cand = re.sub(
        r"^(?:Cb\.?\s*PM|Sgt\.?\s*PM|Sd\.?\s*PM|Ten\.?\s*PM|PM)\s+", "", cand, flags=re.IGNORECASE
    ).strip()
    return re.sub(r"\s+", " ", cand)


def _nome_valido_pessoa(cand: str, usados: set, minimo_chars: int = 6, minimo_palavras: int = 2) -> bool:
    toks_up = set(cand.upper().split())
    return (
        len(cand) >= minimo_chars
        and len(cand.split()) >= minimo_palavras
        and not (toks_up & _STOPWORDS_PESSOA_PSEUDO)
        and normalizar(cand) not in usados
    )


def _ordenar_nomes(nomes: List[str]) -> List[str]:
    """Deduplica (sem acento/caixa) e ordena alfabeticamente: o mapa não depende da ordem de entrada."""
    vistos: Dict[str, str] = {}
    for nome in nomes:
        chave = normalizar(nome)
        if chave and chave not in vistos:
            vistos[chave] = nome
    return [vistos[k] for k in sorted(vistos)]


def calcular_hash_mapa(mapa_reversao: Dict[str, str]) -> str:
    """sha256 do JSON canônico do mapa de reversão (chaves ordenadas)."""
    canon = json.dumps(dict(sorted((mapa_reversao or {}).items())), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def construir_mapa_pseudonimizacao(
    meta: MetadadosProcesso,
    texto_base: str = "",
    extra_testemunhas: Optional[List[str]] = None,
) -> Dict[str, str]:
    """
    Constrói o mapa reversível {token_pseudonimo: valor_real} para proteger dados sensíveis
    (Segredo de Justiça — art. 189 do CPC / LGPD / Resolução CNJ nº 615/2025) durante o envio ao Gemini:
    cobre [OFENDIDA], [ACUSADO], [PROCESSO]/[PROCESSO_APENSO]/[PROCESSO_XX] (nº CNJ dos autos),
    [CPF_XX], [RG_XX], [TELEFONE_XX], [ENDERECO_XX], [TESTEMUNHA_XX], [DESCENDENTE_XX] e os
    prenomes isolados ([PRENOME_OFENDIDA], [PRENOME_ACUSADO], [PRENOME_TESTEMUNHA_XX], ...).

    O mapa é DETERMINÍSTICO: as listas são deduplicadas e ordenadas, de modo que a mesma entrada
    (em qualquer ordem de peças/mídias) gera sempre o mesmo mapa.
    """
    mapa: Dict[str, str] = {}
    usados_vals: set[str] = set()

    nome_ofendida = getattr(meta, "nome_ofendida", "") or ""
    nome_reu = getattr(meta, "nome_reu", "") or ""
    if _eh_nome_identificado(nome_ofendida, "IDENTIFICAD"):
        val = re.sub(r"\s+", " ", nome_ofendida).strip()
        mapa["[OFENDIDA]"] = val
        usados_vals.add(normalizar(val))
    if _eh_nome_identificado(nome_reu, "IDENTIFICAD"):
        val = re.sub(r"\s+", " ", nome_reu).strip()
        mapa["[ACUSADO]"] = val
        usados_vals.add(normalizar(val))

    txt = texto_base or ""

    # 0. Números CNJ dos autos (exceto os de precedentes públicos da Base Fechada).
    publicos = _numeros_cnj_publicos()
    numero_principal = _cnj_formatado(str(getattr(meta, "numero_processo", "") or "").strip())
    numero_apenso_bruto = str(getattr(meta, "autos_mpu_apenso", "") or "")
    m_apenso = _PADRAO_NUMERO_CNJ.search(numero_apenso_bruto)
    if not m_apenso:
        m_plano = _PADRAO_NUMERO_CNJ_PLANO.search(numero_apenso_bruto)
        numero_apenso = _cnj_formatado(m_plano.group(0)) if m_plano else ""
    else:
        numero_apenso = m_apenso.group(0)
    if _PADRAO_NUMERO_CNJ.fullmatch(numero_principal):
        mapa["[PROCESSO]"] = numero_principal
        usados_vals.add(numero_principal)
    if numero_apenso and numero_apenso not in usados_vals:
        mapa["[PROCESSO_APENSO]"] = numero_apenso
        usados_vals.add(numero_apenso)
    cnj_encontrados = set(_PADRAO_NUMERO_CNJ.findall(txt))
    cnj_encontrados.update(_cnj_formatado(n) for n in _PADRAO_NUMERO_CNJ_PLANO.findall(txt))
    outros_cnj = sorted(n for n in cnj_encontrados if n not in usados_vals and n not in publicos)
    for idx, num in enumerate(outros_cnj, start=1):
        mapa[f"[PROCESSO_{idx:02d}]"] = num
        usados_vals.add(num)

    # 1. CPFs
    cpfs_set = set(re.findall(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", txt))
    for m_cpf in _PADRAO_CPF_PLANO_ROTULADO.finditer(txt):
        d_cpf = m_cpf.group(1)
        # Já mapeado na forma pontuada? (mesmo CPF escrito de dois modos usa um único token)
        pont = f"{d_cpf[:3]}.{d_cpf[3:6]}.{d_cpf[6:9]}-{d_cpf[9:]}"
        cpfs_set.add(pont if pont in cpfs_set else d_cpf)
    cpfs = sorted(cpfs_set)
    for idx, cpf in enumerate(cpfs, start=1):
        mapa[f"[CPF_{idx:02d}]"] = cpf
        usados_vals.add(cpf.lower())

    # 2. RGs (apenas números de identidade explícitos com >= 6 dígitos)
    rgs_encontrados: set = set()
    for m_rg in re.finditer(
        r"\b(?:RG|Identidade)\s*(?:n[º°\.]?\s*)?[:\-]?\s*(\d[\d\.\-]{4,12}[0-9Xx]?)\b",
        txt,
        flags=re.IGNORECASE,
    ):
        cand_rg = m_rg.group(1).strip(" .-,;")
        digits_rg = re.sub(r"\D", "", cand_rg)
        if 6 <= len(digits_rg) <= 11 and cand_rg.lower() not in usados_vals:
            rgs_encontrados.add(cand_rg)
    for idx, rg in enumerate(sorted(rgs_encontrados), start=1):
        mapa[f"[RG_{idx:02d}]"] = rg
        usados_vals.add(rg.lower())

    # 3. Telefones brasileiros (DDD + 8 ou 9 dígitos)
    tels_encontrados: set = set()
    for m_tel in re.finditer(
        r"(?:\+55\s*)?(?:\(\d{2}\)\s*|\b\d{2}\s+)9?\d{4}[-\s]\d{4}\b",
        txt,
    ):
        cand_tel = re.sub(r"\s+", " ", m_tel.group(0)).strip()
        digits_tel = re.sub(r"\D", "", cand_tel)
        if 10 <= len(digits_tel) <= 13 and cand_tel.lower() not in usados_vals:
            tels_encontrados.add(cand_tel)
    for idx, tel in enumerate(sorted(tels_encontrados), start=1):
        mapa[f"[TELEFONE_{idx:02d}]"] = tel
        usados_vals.add(tel.lower())

    # 4. Endereços residenciais (logradouro + número)
    ends_encontrados: set = set()
    for m_end in re.finditer(
        r"\b(?:Rua|Avenida|Av\.|Travessa|Tv\.|Alameda|Rodovia|Estrada|Conjunto)\s+"
        r"[A-Za-zÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç][A-Za-zÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç0-9\s\.º°\-]{3,60}?"
        r",\s*(?:n[\.º°]*\s*\d+|s/n|casa\s*\d+|apto?\.?\s*\d+)(?:\s*,\s*(?:Bairro\s+)?[A-Za-zÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç\s]{3,28})?",
        txt,
    ):
        cand_end = re.sub(r"\s+", " ", m_end.group(0)).strip(" ,.;:")
        if len(cand_end) >= 12 and cand_end.lower() not in usados_vals:
            ends_encontrados.add(cand_end)
    for idx, end in enumerate(sorted(ends_encontrados, key=lambda s: (-len(s), s)), start=1):
        mapa[f"[ENDERECO_{idx:02d}]"] = end
        usados_vals.add(end.lower())

    # 5. Testemunhas e Descendentes identificáveis por rótulo explícito ou pela matriz oral
    candidatos_test: List[str] = []
    for extra_t in extra_testemunhas or ():
        cand_ex = _limpar_nome_testemunha(extra_t)
        if _nome_valido_pessoa(cand_ex, usados_vals):
            candidatos_test.append(cand_ex)
    for m_t in re.finditer(
        r"\b(?:Testemunha(?:\s+(?:Policial|de\s+(?:Acusação|Defesa)|do\s+Ju[ií]zo|Informante))?|Depoente)\s*:\s*(?:(?:Cb\.?|Sgt\.?|Sd\.?|Ten\.?)\s*PM\s+)?([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-zÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç\s]{5,45}?)(?=\s*(?:\n|,|\.|CPF|RG|\(|$))",
        txt,
        flags=re.IGNORECASE,
    ):
        cand_t = re.sub(r"\s+", " ", m_t.group(1)).strip()
        if _nome_valido_pessoa(cand_t, usados_vals):
            candidatos_test.append(cand_t)
    testemunhas = _ordenar_nomes(candidatos_test)
    for idx, t_nome in enumerate(testemunhas, start=1):
        mapa[f"[TESTEMUNHA_{idx:02d}]"] = t_nome
        usados_vals.add(normalizar(t_nome))

    candidatos_desc: List[str] = []
    for m_d in re.finditer(
        r"\b(?:filh[oa]|menor|infante|descendente)(?:\s+comum)?\s*(?::\s*|\s+(?:de\s+nome\s+|chamad[oa]\s+))([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-zÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç\s]{4,45}?)(?=\s*(?:\n|,|\.|de\s+\d+\s+anos|\(|$))",
        txt,
        flags=re.IGNORECASE,
    ):
        cand_d = re.sub(r"\s+", " ", m_d.group(1)).strip()
        if _nome_valido_pessoa(cand_d, usados_vals, minimo_chars=5, minimo_palavras=1):
            candidatos_desc.append(cand_d)
    descendentes = _ordenar_nomes(candidatos_desc)
    for idx, d_nome in enumerate(descendentes, start=1):
        mapa[f"[DESCENDENTE_{idx:02d}]"] = d_nome
        usados_vals.add(normalizar(d_nome))

    # 5.1. Filiação ("filha de Joana Batista Lima e Pedro Souza Lima"): nomes dos genitores.
    candidatos_filiacao: List[str] = []
    for m_f in _PADRAO_FILIACAO.finditer(txt):
        for grupo in m_f.groups():
            if grupo:
                cand_f = re.sub(r"\s+", " ", grupo).strip()
                if _nome_valido_pessoa(cand_f, usados_vals):
                    candidatos_filiacao.append(cand_f)
    for idx, f_nome in enumerate(_ordenar_nomes(candidatos_filiacao), start=1):
        mapa[f"[FILIACAO_{idx:02d}]"] = f_nome
        usados_vals.add(normalizar(f_nome))

    # 6. Prenomes isolados das pessoas mapeadas ("Fernanda disse...", "Vítima Fernanda_001.mp4").
    pessoas: List[Tuple[str, str]] = []
    for token in ("[OFENDIDA]", "[ACUSADO]"):
        if token in mapa:
            pessoas.append((token, mapa[token]))
    pessoas += [(tok, val) for tok, val in mapa.items() if tok.startswith(("[TESTEMUNHA_", "[DESCENDENTE_", "[FILIACAO_"))]
    prenomes: Dict[str, List[str]] = {}
    grafia: Dict[str, str] = {}
    for token, nome in pessoas:
        partes = nome.split()
        if len(partes) < 2:
            continue
        prenome = partes[0].strip(".,;:")
        chave = normalizar(prenome)
        if len(prenome) < 3 or prenome.upper() in _PRENOMES_NAO_ISOLAVEIS or prenome.upper() in _STOPWORDS_PESSOA_PSEUDO:
            continue
        prenomes.setdefault(chave, []).append(token)
        grafia.setdefault(chave, prenome[:1].upper() + prenome[1:].lower())
    ambiguos = 0
    for chave in sorted(prenomes):
        donos = prenomes[chave]
        if len(donos) == 1:
            base = donos[0].strip("[]")
            mapa[f"[PRENOME_{base}]"] = grafia[chave]
        else:
            ambiguos += 1
            mapa[f"[PRENOME_AMBIGUO_{ambiguos:02d}]"] = grafia[chave]

    return mapa


def _eh_token_pessoa(token: str) -> bool:
    return token in ("[OFENDIDA]", "[ACUSADO]") or token.startswith(
        ("[TESTEMUNHA_", "[DESCENDENTE_", "[FILIACAO_", "[PRENOME_")
    )


def pseudonimizar_texto(texto: str, mapa_reversao: Dict[str, str]) -> str:
    """
    Substitui nomes reais das partes, testemunhas, descendentes (inclusive prenomes isolados e
    nomes dentro de nomes de arquivo), nº CNJ dos autos, CPFs, RGs, telefones e endereços pelos
    tokens pseudonimizados ([OFENDIDA], [ACUSADO], [PROCESSO], [RG_01], etc.).

    Expressões públicas (ex.: "Lei Maria da Penha", autores da doutrina autorizada) são preservadas.
    """
    if not texto or not mapa_reversao:
        return texto

    out = texto
    protegidas: List[str] = []

    def _proteger(m: "re.Match[str]") -> str:
        protegidas.append(m.group(0))
        return f"\x00{len(protegidas) - 1}\x00"

    # 1º: valores completos (do maior para o menor), evitando substituições parciais prematuras.
    # Os VALORES DO MAPA são substituídos ANTES da proteção das expressões públicas: um nome real que
    # contenha "Maria da Penha" (ex.: "Maria da Penha Souza Lima") não pode escapar por casar com a expressão.
    itens_ordenados = sorted(
        ((tok, val) for tok, val in mapa_reversao.items() if val and not tok.startswith("[PRENOME_")),
        key=lambda kv: (-len(kv[1]), kv[0]),
    )
    for token, real_val in itens_ordenados:
        if _eh_token_pessoa(token):
            padrao = _PRE + r"\s+".join(re.escape(p) for p in real_val.split()) + _POS
        elif real_val.isdigit():
            padrao = rf"(?<!\d){re.escape(real_val)}(?!\d)"
        else:
            padrao = r"\s+".join(re.escape(p) for p in real_val.split()) if real_val.split() else re.escape(real_val)
        out = re.sub(padrao, token, out, flags=re.IGNORECASE)
        # Mesmo CPF/CNJ escrito SEM pontuação nas peças.
        if token.startswith(("[CPF_", "[PROCESSO")):
            digitos = re.sub(r"\D", "", real_val)
            if digitos and digitos != real_val and len(digitos) in (11, 20):
                out = re.sub(rf"(?<!\d){digitos}(?!\d)", token, out)

    # 2º: expressões públicas preservadas. "Maria da Penha" só quando é a lei ("Lei Maria da Penha");
    # os autores da doutrina autorizada ficam protegidos em qualquer posição.
    for expr in _expressoes_protegidas():
        if expr in _EXPRESSOES_PROTEGIDAS_BASE:
            padrao_expr = _PRE + r"Lei\s+(?:federal\s+)?" + re.escape(expr) + _POS
        else:
            padrao_expr = _PRE + re.escape(expr) + _POS
        out = re.sub(padrao_expr, _proteger, out, flags=re.IGNORECASE)

    # 3º: nome real que COMEÇA por uma expressão pública (ex.: "Sra. Maria da Penha confirmou").
    for token, real_val in itens_ordenados:
        if not _eh_token_pessoa(token):
            continue
        for expr in _EXPRESSOES_PROTEGIDAS_BASE:
            if normalizar(real_val).startswith(normalizar(expr) + " ") or normalizar(real_val) == normalizar(expr):
                out = re.sub(_PRE + re.escape(expr) + _POS, token, out, flags=re.IGNORECASE)

    # 4º: forma curta "Prenome Sobrenome" das pessoas.
    for token, real_val in itens_ordenados:
        partes = real_val.split()
        if len(partes) >= 2 and _eh_token_pessoa(token):
            curto_2 = f"{partes[0]} {partes[1]}"
            if len(curto_2) >= 8:
                out = re.sub(_PRE + re.escape(partes[0]) + r"\s+" + re.escape(partes[1]) + _POS, token, out, flags=re.IGNORECASE)

    # 3º: prenomes isolados (também quando colados a "_001" em nomes de arquivo).
    prenomes = sorted(
        ((tok, val) for tok, val in mapa_reversao.items() if val and tok.startswith("[PRENOME_")),
        key=lambda kv: (-len(kv[1]), kv[0]),
    )
    for token, prenome in prenomes:
        # Só ocorrências com inicial maiúscula (nome próprio): evita trocar palavras comuns
        # homógrafas em minúsculas (ex.: "vitória", "rosa", "clara").
        out = re.sub(
            _PRE + re.escape(prenome) + _POS,
            lambda m, t=token: t if m.group(0)[:1].isupper() else m.group(0),
            out,
            flags=re.IGNORECASE,
        )

    if protegidas:
        out = re.sub(r"\x00(\d+)\x00", lambda m: protegidas[int(m.group(1))], out)
    return out


class PseudonimizacaoIncompletaError(EnvioExternoNaoAutorizadoError):
    """Algum valor do mapa de pseudonimização ainda aparece em claro no material a ser enviado ao Gemini."""

    def __init__(self, tokens_vazados: List[str]) -> None:
        self.tokens_vazados = list(tokens_vazados)
        super().__init__(
            "Envio bloqueado: a pseudonimização está incompleta — dado(s) real(is) ainda presente(s) no material "
            f"({', '.join(self.tokens_vazados)}). Nenhum dado do processo foi enviado."
        )


def detectar_vazamento_pseudonimizacao(texto: str, mapa_reversao: Dict[str, str]) -> List[str]:
    """Tokens do mapa cujo valor real ainda aparece no texto (sem acento/caixa; dígitos corridos incluídos).

    Verificação final de segurança do envio externo. Ignoram-se os prenomes isolados (palavras comuns) e as
    expressões públicas protegidas (ex.: "Lei Maria da Penha").
    """
    if not texto or not mapa_reversao:
        return []
    bruto = texto
    for expr in _expressoes_protegidas():
        if expr in _EXPRESSOES_PROTEGIDAS_BASE:
            bruto = re.sub(_PRE + r"Lei\s+(?:federal\s+)?" + re.escape(expr) + _POS, " ", bruto, flags=re.IGNORECASE)
        else:
            bruto = re.sub(_PRE + re.escape(expr) + _POS, " ", bruto, flags=re.IGNORECASE)
    norm_texto = normalizar(bruto)
    vazados: List[str] = []
    for token, real_val in sorted(mapa_reversao.items()):
        if not real_val or token.startswith("[PRENOME_"):
            continue
        val_norm = normalizar(real_val)
        if len(val_norm) < 6:
            continue
        achou = bool(re.search(_PRE + re.escape(val_norm) + _POS, norm_texto))
        digitos = re.sub(r"\D", "", real_val)
        if not achou and len(digitos) >= 10 and token.startswith(("[CPF_", "[PROCESSO", "[RG_", "[TELEFONE_")):
            achou = bool(re.search(rf"(?<!\d){digitos}(?!\d)", bruto))
        if achou:
            vazados.append(token)
    return vazados


def despseudonimizar_texto(texto: str, mapa_reversao: Dict[str, str]) -> str:
    """
    Restaura 100% dos nomes reais das partes, números dos autos e demais dados na sentença
    retornada pelo Gemini (tolerante a variações de caixa nos tokens).
    """
    if not texto or not mapa_reversao:
        return texto

    out = texto
    for token, real_val in sorted(mapa_reversao.items(), key=lambda kv: (-len(kv[0]), kv[0])):
        if not real_val:
            continue
        out = re.sub(re.escape(token), lambda _m, v=real_val: v, out, flags=re.IGNORECASE)
    return out


def _pseudonimizar_estrutura(obj: Any, mapa_reversao: Dict[str, str]) -> Any:
    """Aplica a pseudonimização a todas as strings de uma estrutura JSON (dict/list)."""
    if isinstance(obj, str):
        return pseudonimizar_texto(obj, mapa_reversao)
    if isinstance(obj, dict):
        return {k: _pseudonimizar_estrutura(v, mapa_reversao) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_pseudonimizar_estrutura(v, mapa_reversao) for v in obj]
    return obj


def construir_mapa_do_contexto(ctx: CaseContext) -> Dict[str, str]:
    """Mapa de pseudonimização determinístico a partir do contexto do caso."""
    texto_base_busca = "\n".join(p.conteudo for p in ctx.pecas)
    nomes_test_oral = [
        d.depoente for d in ctx.matriz_oral.depoentes if "TESTEMUNHA" in str(d.papel or "").upper()
    ]
    return construir_mapa_pseudonimizacao(ctx.meta, texto_base_busca, extra_testemunhas=nomes_test_oral)


# ------------------------------------------------------------------------------
# Motores Python
# ------------------------------------------------------------------------------


@dataclass
class ResultadoMotores:
    """Resultado completo dos 5 motores (inclui o contexto e a sinalização de presunção)."""

    ctx: CaseContext
    pecas: List[PecaClassificada]
    meta: MetadadosProcesso
    matriz_oral: MatrizCorroboracaoOral
    dosimetria_global: DosimetriaGlobalOutput
    prescricao: Any
    motores_status: List[StatusMotorPy]
    plano: PlanoSentenca
    presume_condenacao: bool = False
    alertas: Optional[List[str]] = None
    decisoes_crimes: List[Dict[str, Any]] = field(default_factory=list)


def executar_motores_completo(
    case_bundle: Dict[str, Any],
    custom_dosimetria_override: Optional[Dict[str, Any]] = None,
    exigir_veredicto: Optional[bool] = None,
) -> ResultadoMotores:
    """
    Executa os 5 motores e devolve o resultado completo.

    ``exigir_veredicto``: ``True`` exige veredicto explícito por crime (geração para caso real);
    ``False`` permite a simulação (presunção de condenação sinalizada); ``None`` = exige apenas
    para caso real (``bundle["is_demo"]`` diferente de ``True``).
    """
    plano = planejar_sentenca(case_bundle, custom_dosimetria_override, exigir_veredicto=exigir_veredicto)
    ctx = plano.ctx
    pecas = ctx.pecas
    meta = plano.meta
    matriz_oral = ctx.matriz_oral
    dosimetria_global = plano.dosimetria_global
    prescricao = plano.prescricao
    presume = plano.simulacao_presume_condenacao

    ocr_count = sum(1 for p in pecas if p.ocr_aplicado)
    n_depoentes = len(matriz_oral.depoentes)
    try:
        n_modelos = len(get_style_profile().get("modelos_cadastrados", []))
    except Exception:  # pragma: no cover
        n_modelos = 0
    motores_status = [
        StatusMotorPy(
            codigo="MOTOR_PY_01_INGESTAO_PDF",
            nome="Motor Py 1 — Ingestor e Segmentador PJe + OCR Seletivo",
            funcao="Segmenta os autos em PDF pela árvore TOC/Bookmarks do PJe e extrai laudos/termos via OCR nativo da plataforma (quando disponível).",
            status="CONCLUIDO" if pecas else "SEM_DADOS",
            resumo_execucao=f"{len(pecas)} peças processuais relevantes indexadas ({ocr_count} com OCR seletivo aplicado).",
            itens_processados=len(pecas),
        ),
        StatusMotorPy(
            codigo="MOTOR_PY_02_CLASSIFICADOR_LAYA",
            nome="Motor Py 2 — Classificador Laya System 1 e Extrator de Teses",
            funcao="Classifica peças por espécie forense e extrai metadados, crimes imputados e catálogo de teses defensivas (Art. 381, III, CPP).",
            status="CONCLUIDO",
            resumo_execucao=(
                f"Processo {meta.numero_processo or 'não identificado'} | {len(meta.crimes_imputados)} crime(s) imputado(s) | "
                f"{len(meta.teses_defesa)} tese(s) defensiva(s) mapeada(s)."
            ),
            itens_processados=len(meta.teses_defesa),
        ),
        StatusMotorPy(
            codigo="MOTOR_PY_03_PROVA_ORAL",
            nome="Motor Py 3 — Analisador Forense de Mídias de Audiência",
            funcao="Organiza as transcrições das mídias da AIJ por interlocutor e consolida síntese indireta sob a Res. CNJ 492/2023.",
            status="CONCLUIDO" if n_depoentes else "SEM_DADOS",
            resumo_execucao=(
                f"{n_depoentes} registro(s) audiovisual(is) consolidados por interlocutor."
                if n_depoentes
                else "Nenhuma mídia de audiência transcrita foi fornecida."
            ),
            itens_processados=n_depoentes,
        ),
        StatusMotorPy(
            codigo="MOTOR_PY_04_DOSIMETRIA_PRESCRICAO",
            nome="Motor Py 4 — Calculadora Determinística de Dosimetria e Prescrição",
            funcao="Calcula 100% da pena trifásica (Arts. 59 e 68 CP), direito intertemporal, concurso material, regime, sursis e prescrição.",
            status="SIMULACAO" if presume else "CONCLUIDO",
            resumo_execucao=(
                f"Pena total unificada: {dosimetria_global.pena_total_unificada.format_curto()} | "
                f"Regime: {dosimetria_global.regime_inicial} | "
                f"Prescrição: {'SIM' if getattr(prescricao, 'prescricao_reconhecida', False) else 'NÃO'}"
                + (" | SIMULAÇÃO (presume condenação)." if presume else ".")
            ),
            itens_processados=len(dosimetria_global.crimes_calculados),
        ),
        StatusMotorPy(
            codigo="MOTOR_PY_05_BASE_FECHADA_ESTILO",
            nome="Motor Py 5 — Base Jurídica Fechada e Perfil de Estilo",
            funcao="Fornece os verbetes autorizados (STJ/STF/FONAVID/TJRN), doutrina ABNT e o perfil de estilo/modelos do gabinete.",
            status="CONCLUIDO",
            resumo_execucao=(
                f"{len(SUMULAS_E_TEMAS_VERIFICADOS)} súmulas/temas/acórdãos + "
                f"{len(DOUTRINA_VERIFICADA)} obras doutrinárias + {n_modelos} modelo(s) cadastrado(s)."
            ),
            itens_processados=len(SUMULAS_E_TEMAS_VERIFICADOS) + len(DOUTRINA_VERIFICADA),
        ),
    ]

    return ResultadoMotores(
        ctx=ctx,
        pecas=pecas,
        meta=meta,
        matriz_oral=matriz_oral,
        dosimetria_global=dosimetria_global,
        prescricao=prescricao,
        motores_status=motores_status,
        plano=plano,
        presume_condenacao=presume,
        alertas=[
            "Simulação: há crime(s) sem veredicto explícito do juízo; a dosimetria de referência presume "
            "condenação apenas para cálculo (simulacao_presume_condenacao)."
        ] if presume else [],
        decisoes_crimes=serializar_decisoes_imputacao(plano.decisoes_crimes),
    )


def executar_motores_py_separados(
    case_bundle: Dict[str, Any],
    custom_dosimetria_override: Optional[Dict[str, Any]] = None,
    exigir_veredicto: Optional[bool] = False,
) -> Tuple[
    List[PecaClassificada],
    MetadadosProcesso,
    MatrizCorroboracaoOral,
    DosimetriaGlobalOutput,
    PrescricaoOutput,
    List[StatusMotorPy],
]:
    """
    Executa de forma isolada e auditável os 5 Motores Python do sistema:
    - Motor 1: Ingestão e Segmentação de PDFs PJe + OCR seletivo
    - Motor 2: Classificador Laya System 1 + Extrator de Metadados e Teses Defensivas
    - Motor 3: Transcritor e Analisador Forense de Mídias de Audiência (Res. CNJ 492/2023)
    - Motor 4: Calculadora Determinística de Dosimetria Trifásica, Concurso e Prescrição
    - Motor 5: Base Jurídica Fechada (STJ/STF/FONAVID/TJRN) e Perfil de Estilo Forense

    Mantém o retorno em tupla (compatibilidade). Por padrão (``exigir_veredicto=False``) funciona
    como simulação/telemetria; use ``executar_motores_completo`` para o resultado detalhado.
    """
    r = executar_motores_completo(case_bundle, custom_dosimetria_override, exigir_veredicto=exigir_veredicto)
    return r.pecas, r.meta, r.matriz_oral, r.dosimetria_global, r.prescricao, r.motores_status


def _fmt_data_script(valor: Any, por_extenso: bool = False) -> str:
    if not valor:
        return "[COMPLETAR PELO JUÍZO: data não extraída dos autos]"
    try:
        return formatar_data_br(valor, por_extenso=por_extenso) if por_extenso else formatar_data_br(valor)
    except Exception:
        return str(valor)


def gerar_script_estruturado_caso(
    case_bundle: Dict[str, Any],
    custom_dosimetria_override: Optional[Dict[str, Any]] = None,
    pseudonimizar: bool = False,
    densidade: Literal["compacto", "integral"] = "compacto",
    exigir_veredicto: Optional[bool] = None,
    *,
    resultado_motores: ResultadoMotores | None = None,
) -> ScriptCasoEstruturado:
    """
    Constrói o Script Estruturado do Caso a partir dos 5 Motores Python para que o agente Gemini
    (via API direta ou via copiar/colar no Gemini Web/Pro) redija a sentença com base exclusivamente
    nas informações fáticas, probatórias, jurídicas e dosimétricas já apuradas e validadas.
    Suporta deduplicação APF x IP e seletor de densidade ('compacto' ~45-60KB vs 'integral').

    Caso real: exige veredicto explícito por crime (``DecisaoJudicialPendenteError``), salvo
    ``exigir_veredicto=False`` (simulação sinalizada em ``alertas``). Dados essenciais ausentes
    levantam ``DadosInsuficientesError``. Com ``pseudonimizar=True`` o Markdown e o JSON são
    pseudonimizados e o mapa determinístico acompanha o Script (``mapa_reversao`` + ``mapa_hash``).
    """
    settings = load_settings()
    style = get_style_profile()
    densidade_norm = "integral" if str(densidade).strip().lower() == "integral" else "compacto"
    resultado_motores = resultado_motores or executar_motores_completo(
        case_bundle, custom_dosimetria_override, exigir_veredicto=exigir_veredicto
    )
    ctx = resultado_motores.ctx
    pecas = resultado_motores.pecas
    meta = resultado_motores.meta
    matriz_oral = resultado_motores.matriz_oral
    dosimetria_global = resultado_motores.dosimetria_global
    prescricao = resultado_motores.prescricao
    motores_status = resultado_motores.motores_status
    recortes = resultado_motores.plano.contexto_geracao

    grupos_dedup, dedup_removidas = deduplicar_pecas_equivalentes(pecas)
    ids_pecas = [f"{g['unified_id']} ({g['unified_folha']}) — {g['primary'].titulo}" for g in grupos_dedup]
    ids_midias = [f"{d.media_id} ({d.arquivo}, {d.duracao}) — {d.depoente} [{d.papel}]" for d in matriz_oral.depoentes]
    universo_ids = sorted({p.doc_id for p in pecas} | set(ctx.ids_midias) | {d.media_id for d in matriz_oral.depoentes})

    linhas_pecas_list = []
    for g in grupos_dedup:
        p = g["primary"]
        if p.categoria in CATEGORIAS_SEM_FONTE_DE_FATOS:
            continue
        tit_low = p.titulo.lower()
        if densidade_norm == "compacto" and (
            g["is_sub_parent"]
            or "decurso de prazo" in tit_low
            or "juntada de mídia" in tit_low
            or (p.categoria == "OUTRAS" and any(k in tit_low for k in ("certidão", "intimação", "mandado", "alvará", "juízo 100%")))
        ):
            continue
        ref_id = g["unified_id"]
        linha = f"- **{ref_id}** ({g['unified_folha']} | {p.categoria} | data: {formatar_data_br(p.data_peca) or 'não informada'}): {p.titulo}"
        if ctx.is_demo:
            resumo_item = p.resumo_estruturado[:240].strip() if densidade_norm == "compacto" else p.resumo_estruturado
            linha += f"\n  - *Síntese*: {resumo_item}"
        linhas_pecas_list.append(linha)
    linhas_pecas = "\n".join(linhas_pecas_list)

    interlocutores = consolidar_prova_oral_por_interlocutor(matriz_oral.depoentes, pecas=pecas) if ctx.is_demo else []
    if not ctx.is_demo:
        linhas_oral = "\n".join(
            f"- {item['depoente']} (papel {item['papel']}, mídia {item['media_id']}): {item['trecho']}"
            for item in recortes.get("trechos_orais", ()) if item["trecho"]
        ) or "- Sem trecho oral confirmado no recorte vinculado às decisões."
    elif interlocutores:
        linhas_oral = "\n".join(
            f"- **{it['nome_base']}** (Papel: `{it['papel']}` | Mídia(s): `{', '.join(it['media_ids'])}` | Durações: `{', '.join(it['duracoes'])}` | Coerência: `{it['coerencia']}`):\n"
            f"  - *Síntese Consolidada em Discurso Indireto Forense*: {' '.join(it['sintese_consolidada'])}"
            for it in interlocutores
        )
    elif matriz_oral.depoentes:
        linhas_oral = "\n".join(
            f"- **{d.depoente}** (Papel: `{d.papel}` | Mídia: `{d.media_id}` | Duração: `{d.duracao}` | Coerência: `{d.coerencia_delegacia_juizo}`):\n"
            f"  - *Síntese em Discurso Indireto Forense*: {d.discurso_indireto_sentenca}\n"
            f"  - *Relato na Fase Policial*: {d.relato_fase_policial}\n"
            f"  - *Corroboração Pericial/Documental*: {d.corroboracao_pericial_documental}"
            for d in matriz_oral.depoentes
        )
    else:
        linhas_oral = "- Nenhuma mídia audiovisual individualizada anexada além do Termo de Audiência."

    excertos_chave = [
        f"### [{item['doc_id']}] {item['titulo']}\n{item['trecho']}"
        for item in recortes.get("trechos_pecas", ()) if item["trecho"]
    ]
    if ctx.is_demo:
        excertos_chave = []
        cats_excertos = (
            "DENUNCIA",
            "BO",
            "LAUDO_PERICIAL",
            "ALEGACOES_FINAIS_MP",
            "ALEGACOES_FINAIS_DEFESA",
            "RESPOSTA_ACUSACAO",
            "RECEBIMENTO_DENUNCIA",
            "DECISAO_MPU",
            "TERMO_AUDIENCIA",
            "CAC_FAC",
        )
        cats_prioritarias = {"DENUNCIA", "LAUDO_PERICIAL", "ALEGACOES_FINAIS_MP", "ALEGACOES_FINAIS_DEFESA", "TERMO_AUDIENCIA"}
        for g in grupos_dedup:
            if g["is_sub_parent"]:
                continue
            p = g["primary"]
            if p.categoria in CATEGORIAS_SEM_FONTE_DE_FATOS:
                continue
            tit_low = p.titulo.lower()
            if densidade_norm == "compacto":
                if p.doc_id == "ID-CAPA-PJE" or "decurso de prazo" in tit_low or "alvará" in tit_low or "juízo 100%" in tit_low:
                    continue
            if p.categoria in cats_excertos or "endereço" in tit_low:
                trecho_limpo = g["clean_body"]
                if densidade_norm == "compacto":
                    limite = 900 if p.categoria in cats_prioritarias else 340
                else:
                    limite = 4500
                trecho_limpo = trecho_limpo[:limite].strip()
                if trecho_limpo:
                    excertos_chave.append(
                        f"### [{g['unified_id']}] {p.titulo} ({formatar_data_br(p.data_peca) or 'data não informada'})\n{trecho_limpo}"
                    )
    bloco_excertos_md = "\n\n".join(excertos_chave) or "Sem trecho documental vinculado ao recorte."
    linhas_teses = "\n".join(
        f"- **{t.codigo}** (`{t.tipo}`): **{t.titulo}** — {t.descricao_defesa}"
        for t in meta.teses_defesa
    )

    linhas_crimes_dos = []
    for idx, c in enumerate(dosimetria_global.crimes_calculados, start=1):
        spec = c.crime_spec
        esp = formatar_especie_pena(spec.especie_pena)
        v = c.vetores_detalhados
        if densidade_norm == "compacto":
            bloco_art59_md = "\n".join(
                f"  - {rot}: {'DESFAVORÁVEL' if v[k].desfavoravel else 'NEUTRO/FAV'} — {v[k].fundamentacao[:190]}"
                for k, rot in [
                    ("culpabilidade", "Culpabilidade"),
                    ("antecedentes", "Antecedentes Criminais"),
                    ("conduta_social", "Conduta Social"),
                    ("personalidade", "Personalidade do Agente"),
                    ("motivos", "Motivos do Crime"),
                    ("circunstancias", "Circunstâncias do Crime"),
                    ("consequencias", "Consequências do Crime"),
                    ("comportamento_vitima", "Comportamento da Vítima"),
                ]
            )
        else:
            bloco_art59_md = (
                f"  - a) Culpabilidade: {v['culpabilidade'].fundamentacao}\n"
                f"  - b) Antecedentes Criminais: {v['antecedentes'].fundamentacao}\n"
                f"  - c) Conduta Social: {v['conduta_social'].fundamentacao}\n"
                f"  - d) Personalidade do Agente: {v['personalidade'].fundamentacao}\n"
                f"  - e) Motivos do Crime: {v['motivos'].fundamentacao}\n"
                f"  - f) Circunstâncias do Crime: {v['circunstancias'].fundamentacao}\n"
                f"  - g) Consequências do Crime: {v['consequencias'].fundamentacao}\n"
                f"  - h) Comportamento da Vítima: {v['comportamento_vitima'].fundamentacao}"
            )
        multa_line = ""
        if c.multa_pendente:
            multa_line = " | Multa: preserve o marcador de decisão pendente."
        elif c.dias_multa and c.valor_unitario_multa:
            multa_line = f" | Multa: {c.dias_multa} dias-multa, à razão de {c.valor_unitario_multa} do salário mínimo ao tempo do fato."
        linhas_crimes_dos.append(
            f"### Crime {idx}: {spec.nome} ({spec.artigo_formatado})\n"
            f"- Preceito secundário aplicável (direito intertemporal já resolvido): {spec.min_pena.format_extenso()} a {spec.max_pena.format_extenso()} de {esp}\n"
            f"- **1ª Fase (Art. 59 do CP — 8 circunstâncias judiciais obrigatórias)**:\n"
            f"{bloco_art59_md}\n"
            f"  - **Pena-Base Calculada**: `{c.pena_base.format_extenso()} de {esp}`\n"
            f"- **2ª Fase (Circunstâncias Legais)**: Pena Intermediária = `{c.pena_intermediaria.format_extenso()} de {esp}` "
            f"(Súmula 231/STJ aplicada: `{c.sumula_231_aplicada}`; Bis in idem art. 61, II, 'f', CP bloqueado: `{c.bis_in_idem_art61f_bloqueado}`)\n"
            f"- **3ª Fase (Causas de Aumento/Diminuição)** e **Pena Definitiva**: `{c.pena_definitiva.format_extenso()} de {esp}`{multa_line}"
        )

    for nc in getattr(dosimetria_global, "crimes_nao_condenados", []) or []:
        verbo_nc = "EXTINÇÃO DA PUNIBILIDADE" if nc.get("veredicto") == "extinguir" else "ABSOLVIÇÃO"
        linhas_crimes_dos.append(
            f"### Imputação sem Condenação ({verbo_nc}): {nc['crime_nome']} ({nc['artigo_formatado']})\n"
            f"- **Desfecho Determinístico**: {verbo_nc} com fundamento no `{nc['fundamento_legal']}` (sem dosimetria de pena)."
        )
    bloco_dosimetria_md = "\n\n".join(linhas_crimes_dos)

    kb_all = get_all_knowledge_base()
    itens_citaveis = [
        item
        for item in SUMULAS_E_TEMAS_VERIFICADOS + kb_all.get("precedentes_gabinete", [])
        if item_citavel_automaticamente(item)
    ]
    if densidade_norm == "compacto":
        verbetes_autorizados = "\n".join(
            f"- `{item['referencia']}` ({item['tribunal']}): {item['texto_literal'][:130]}"
            for item in itens_citaveis
        )
        doutrina_autorizada = "\n".join(
            f"- **{d['autor']}** ({d['referencia_abnt']}) [síntese, sem aspas]: {d['sintese'][:180]}"
            for d in DOUTRINA_VERIFICADA
        )
    else:
        verbetes_autorizados = "\n".join(
            f"- `{item['referencia']}` ({item['tribunal']}): {item['texto_literal']}"
            for item in itens_citaveis
        )
        doutrina_autorizada = "\n".join(
            f"- **{d['autor']}** ({d['referencia_abnt']}) [síntese, sem aspas]: {d['sintese']}"
            for d in DOUTRINA_VERIFICADA
        )

    dano_str = (
        formatar_moeda_com_extenso(dosimetria_global.indenizacao_dano_moral_tema983)
        if dosimetria_global.indenizacao_dano_moral_tema983 > 0
        else (
            "Valor não fixado pelos motores — conferir os alertas da dosimetria (pedido sem valor "
            "aguardando arbitramento pelo juízo, ausência de pedido ou absolvição integral)"
        )
    )

    tipo_conc = getattr(dosimetria_global, "tipo_concurso", "material")
    frac_conc = getattr(dosimetria_global, "fracao_concurso", None)
    conc_label = (
        f"Concurso Formal (Art. 70 CP, +{frac_conc[0]}/{frac_conc[1]})"
        if tipo_conc == "formal" and frac_conc
        else (
            f"Crime Continuado (Art. 71 CP, +{frac_conc[0]}/{frac_conc[1]})"
            if tipo_conc == "continuado" and frac_conc
            else "Concurso Material (Art. 69 CP)"
        )
    )
    bloco_unificacao_md = (
        "- **Resultado Global**: `ABSOLVIÇÃO INTEGRAL / EXTINÇÃO DA PUNIBILIDADE` — dosimetria de pena e dano moral prejudicados.\n"
        if getattr(dosimetria_global, "sem_condenacao", False)
        else (
            f"- **Unificação, Regime, Substituição, Detração, Sursis, Dano Moral e Prescrição**:\n"
            f"- **Pena Total Unificada ({conc_label})**: `{dosimetria_global.pena_total_unificada.format_extenso()}`\n"
            f"- **Penas por Espécie**: `{ {k: v.format_extenso() for k, v in dosimetria_global.penas_por_especie.items()} }`\n"
            f"- **Detração Penal (Art. 387, § 2º, CPP)**: `{dosimetria_global.detracao_dias} dia(s) de prisão cautelar`\n"
            f"- **Regime Inicial de Cumprimento (Art. 33, §§ 2º e 3º, CP)**: `REGIME INICIAL {dosimetria_global.regime_inicial.upper()}`\n"
            + (
                f"- **Substituição por Restritivas de Direitos (Art. 44 CP c/c Súmula 588/STJ)**: {dosimetria_global.motivo_vedacao_substituicao}\n"
                f"- **Suspensão Condicional da Pena — Sursis Penal (Art. 77 CP)**: {dosimetria_global.fundamentacao_sursis}\n"
                if ctx.is_demo else ""
            )
            + (
                "  - Condições legais obrigatórias do Sursis:\n"
                + "\n".join(f"    - {cond}" for cond in dosimetria_global.sursis_condicoes_legais) + "\n"
                if ctx.is_demo and dosimetria_global.sursis_condicoes_legais else ""
            )
            + f"- **Reparação Mínima por Danos Morais in re ipsa (Art. 387, IV, CPP e Tema Repetitivo 983/STJ)**: `{dano_str}` (correção pelo IPCA desde a sentença — Súmula 362/STJ; juros pela Selic deduzido o IPCA desde o fato — Súmula 54/STJ e art. 406, § 1º, CC)\n"
            + (f"- **Análise de Prescrição (Arts. 109 e 117, I, CP)**: {prescricao.fundamentacao}\n\n" if prescricao_exige_topico(prescricao) else "- **Prescrição**: não consumada para nenhum crime. NÃO abra tópico nem mencione a prescrição na sentença.\n\n")
        )
    )

    comandos = construir_comandos_dispositivo(dosimetria_global)
    linhas_dispositivo = [f"- {item.texto(meta.nome_reu)}." for item in comandos.imputacoes]
    if comandos.sem_condenacao:
        linhas_dispositivo.append("- Nenhuma pena, multa ou reparação mínima decorrente de condenação.")
    else:
        linhas_dispositivo.extend([
            f"- Fixe apenas os totais por espécie: {comandos.texto_penas()}{texto_multa_dispositivo(comandos)}."
            + comandos.texto_soma_penas(),
            f"- {texto_regime_dispositivo(comandos)}.",
            "- " + texto_dano_moral_dispositivo(
                dosimetria_global, nome_ofendida=meta.nome_ofendida,
                data_fato_br=formatar_data_br(meta.data_fato), id_den="denúncia",
            ) + ".",
        ])
        if not comandos.multas and not comandos.multa_pendente:
            linhas_dispositivo.append("- Nenhuma multa penal calculada.")
    texto_comandos_dispositivo = "\n".join(linhas_dispositivo)

    juizo = resultado_motores.plano.juizo_sobre_provas
    registros_prova = ""
    if not ctx.is_demo:
        linhas_registros = [
            "### Conclusões probatórias registradas pelo juízo",
            "Relatos, dados da acusação e o veredicto não autorizam inferir prova de um elemento. "
            "Use apenas as conclusões registradas por imputação e mantenha os marcadores nas lacunas. "
            "A aplicação insere a fundamentação e os trechos literais localmente.",
            "A aplicação insere localmente as decisões acessórias e seus fundamentos. "
            "Não redija comandos sobre substituição, sursis, medidas protetivas, cautelares, efeitos ou intimações com base em suposições.",
        ]
        for decisao in resultado_motores.plano.decisoes_crimes:
            if decisao.get("veredicto") == "extinguir":
                continue
            rotulo = decisao.get("referencia_imputacao") or decisao["crime_code"]
            linhas_registros.extend((f"**{rotulo}**", juizo.merito(decisao, incluir_trechos=False)))
        if dosimetria_global.crimes_calculados:
            linhas_registros.extend(("### Decisão cautelar", juizo.texto_cautelar(incluir_trechos=False)))
        registros_prova = "\n\n".join(linhas_registros) + "\n\n"
    script_md_raw = (
        f"# SCRIPT ESTRUTURADO DOS MOTORES PYTHON PARA REDAÇÃO DE SENTENÇA PENAL (JVDFM)\n\n"
        f"Você é um Assessor Jurídico Sênior de Magistrado de Juizado de Violência Doméstica e Familiar contra a Mulher.\n"
        f"Os **5 Motores Python Determinísticos** já realizaram a leitura integral do PDF do PJe, o OCR dos laudos periciais, "
        f"a transcrição das mídias de audiência, o mapeamento das teses defensivas e o cálculo aritmético exato da dosimetria trifásica e da prescrição.\n\n"
        f"**SUA TAREFA EXCLUSIVA**: Redigir o texto integral da sentença penal em língua portuguesa culta forense, "
        f"seguindo rigorosamente a estrutura canônica (`I – RELATÓRIO`, `II – FUNDAMENTAÇÃO`, `3. Da Dosimetria da Pena (Arts. 59 e 68 do Código Penal)` e `III – DISPOSITIVO`), "
        f"**usando exclusivamente os fatos, IDs, valores e precedentes fornecidos neste Script**.\n\n"
        f"---\n\n"
        f"## 1. DADOS OBJETIVOS E METADADOS DO PROCESSO (MOTOR PY 2 — LAYA S1)\n"
        f"- **Tribunal / Juízo**: {settings.tribunal_nome} — {settings.juizado_nome} ({settings.comarca_nome})\n"
        f"- **Magistrado(a)**: {settings.magistrado_nome}\n"
        f"- **Processo nº**: `{meta.numero_processo}`\n"
        f"- **Medida Protetiva / Apenso**: `{meta.autos_mpu_apenso}`\n"
        f"- **Ofendida (Vítima)**: `{meta.nome_ofendida}`\n"
        f"- **Acusado (Réu)**: `{meta.nome_reu}`\n"
        f"- **Vínculo Doméstico/Familiar (Art. 5º, Lei 11.340/06)**: {meta.vinculo_domestico or '[NÃO EXTRAÍDO DOS AUTOS — o juízo informa o vínculo e o inciso do art. 5º]'}\n"
        f"- **Data do Fato**: `{_fmt_data_script(meta.data_fato)}` ({_fmt_data_script(meta.data_fato, por_extenso=True)})\n"
        f"- **Data do Recebimento da Denúncia**: `{_fmt_data_script(meta.data_recebimento_denuncia)}` ({_fmt_data_script(meta.data_recebimento_denuncia, por_extenso=True)})\n"
        f"- **Data da Sentença**: {formatar_data_br(resultado_motores.plano.data_sentenca, por_extenso=True)}\n"
        f"- **Crimes Imputados / Analisados**: `{', '.join(meta.crimes_imputados)}`\n"
        + (
            "- **ATENÇÃO — SIMULAÇÃO**: há crime(s) sem veredicto explícito do juízo; os cálculos abaixo presumem "
            "condenação apenas para simulação e NÃO autorizam redigir condenação.\n"
            if resultado_motores.presume_condenacao
            else ""
        )
        + "\n"
        f"---\n\n"
        f"---\n\n"
        f"{registros_prova}"
        f"## 2. CATÁLOGO FECHADO DE PEÇAS E IDs REAIS DOS AUTOS (MOTOR PY 1 — INGESTÃO + OCR)\n"
        f"**REGRA ANTI-ALUCINAÇÃO CAMADA 1**: Cite apenas os tokens de ID listados abaixo (`{', '.join(universo_ids)}`). Jamais invente IDs ou folhas.\n\n"
        f"{linhas_pecas}\n\n"
        f"---\n\n"
        f"## 3. MATRIZ DE PROVA ORAL E MÍDIAS DE AUDIÊNCIA (MOTOR PY 3 — RES. CNJ Nº 492/2023)\n"
        f"- **Diretriz Hermenêutica**: {matriz_oral.fundamentacao_sintese}\n\n"
        f"{linhas_oral}\n\n"
        f"---\n\n"
        f"## 4. TESES DEFENSIVAS DE APRECIAÇÃO OBRIGATÓRIA (ART. 381, III, CPP E ART. 93, IX, CF)\n"
        f"**REGRA ANTI-ALUCINAÇÃO CAMADA 3**: Enfrente expressamente 100% das teses abaixo (mencionando o código `PREL-01`, `MER-01`, etc.). Nunca use fórmulas evasivas.\n\n"
        f"{linhas_teses}\n\n"
        f"---\n\n"
        f"## 5. CÁLCULO MATEMÁTICO PRONTO DA DOSIMETRIA TRIFÁSICA E PRESCRIÇÃO (MOTOR PY 4)\n"
        f"**REGRA DE OURO**: Não refaça contas de cabeça. Transcreva os valores exatos calculados abaixo e enuncie nominalmente as 8 circunstâncias do art. 59 do CP (`Culpabilidade`, `Antecedentes Criminais`, `Conduta Social`, `Personalidade do Agente`, `Motivos do Crime`, `Circunstâncias do Crime`, `Consequências do Crime`, `Comportamento da Vítima`).\n\n"
        f"{bloco_dosimetria_md}\n\n"
        f"{bloco_unificacao_md}"
        f"### Comandos obrigatórios do dispositivo\n"
        f"Reproduza cada decisão com seu fundamento, cada pena total por espécie e os demais comandos abaixo. "
        f"As penas individuais por crime pertencem à dosimetria, e não substituem os totais no dispositivo.\n\n"
        f"{texto_comandos_dispositivo}\n\n"
        f"---\n\n"
        f"## 5.1. EXCERTOS LITERAIS DAS PEÇAS ESSENCIAIS DOS AUTOS (ÂNCORA FÁTICA PROIBINDO INVENÇÕES)\n"
        f"{bloco_excertos_md}\n\n"
        f"---\n\n"
        f"## 6. BASE JURÍDICA FECHADA AUTORIZADA E ESTILO DO MAGISTRADO (MOTOR PY 5)\n"
        f"**REGRA ANTI-ALUCINAÇÃO CAMADA 2**: Cite exclusivamente Súmulas, Temas, Enunciados FONAVID, Acórdãos e Doutrina constantes nesta lista oficial:\n\n"
        f"### Súmulas, Temas Repetitivos, Enunciados e Precedentes:\n"
        f"{verbetes_autorizados}\n\n"
        f"### Doutrina Autorizada (Citação ABNT NBR 10520 com recuo de 4 espaços, sem aspas externas):\n"
        f"{doutrina_autorizada}\n\n"
        f"### Regras Formais Obrigatórias (Camada 4):\n"
        f"1. Inclua os títulos exatos: `I – RELATÓRIO`, `II – FUNDAMENTAÇÃO`, `3. Da Dosimetria da Pena (Arts. 59 e 68 do Código Penal)` e `III – DISPOSITIVO`.\n"
        f"2. No Dispositivo, determine expressamente a intimação pessoal da ofendida nos termos do `art. 21 da Lei nº 11.340/2006`.\n"
        f"3. Grafia da LC 95/98: use ordinal apenas até o `art. 9º` e cardinal a partir do `art. 10` (nunca escreva `art. 129º`).\n"
        f"4. Datas exclusivamente no formato brasileiro (`DD/MM/AAAA` ou por extenso; jamais `AAAA-MM-DD`).\n"
        f"5. Abertura do Relatório: `{style['abertura_relatorio']}` | Fechamento do Relatório: `{style['fechamento_relatorio']}` | Abertura do Dispositivo: `{style['abertura_dispositivo']}`.\n"
    )

    mapa_reversao = construir_mapa_do_contexto(ctx) if pseudonimizar else {}
    script_md_final = pseudonimizar_texto(script_md_raw, mapa_reversao) if pseudonimizar else script_md_raw

    script_json_payload: Dict[str, Any] = {
        "numero_processo": meta.numero_processo,
        "ofendida": meta.nome_ofendida,
        "reu": meta.nome_reu,
        "vinculo_domestico": meta.vinculo_domestico,
        "data_fato": meta.data_fato,
        "data_recebimento_denuncia": meta.data_recebimento_denuncia,
        "data_sentenca": resultado_motores.plano.data_sentenca.isoformat(),
        "crimes_imputados": meta.crimes_imputados,
        "ids_autorizados": universo_ids,
        "pecas_indexadas": ids_pecas,
        "pecas_deduplicadas_removidas": dedup_removidas,
        "densidade": densidade_norm,
        "midias_audiencia": ids_midias,
        "teses_defesa": [t.model_dump(mode="json") for t in meta.teses_defesa],
        "dosimetria_calculada": dosimetria_global.model_dump(mode="json"),
        "prescricao_calculada": prescricao.model_dump(mode="json"),
        "simulacao_presume_condenacao": resultado_motores.presume_condenacao,
    }
    if pseudonimizar:
        # Todo o JSON (não apenas ofendida/réu) passa pela pseudonimização: títulos de peças,
        # nomes de arquivos de mídia, teses e fundamentações podem conter nomes e números.
        script_json_payload = _pseudonimizar_estrutura(script_json_payload, mapa_reversao)
        # Verificação final: se qualquer valor real do mapa ainda aparece no material, o envio é bloqueado.
        vazados = detectar_vazamento_pseudonimizacao(
            script_md_final + "\n" + json.dumps(script_json_payload, ensure_ascii=False, default=str), mapa_reversao
        )
        if vazados:
            raise PseudonimizacaoIncompletaError(vazados)

    return ScriptCasoEstruturado(
        numero_processo=meta.numero_processo,
        gerado_em=datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        pseudonimizado=pseudonimizar,
        densidade=densidade_norm,
        pecas_deduplicadas_removidas=dedup_removidas,
        mapa_reversao=mapa_reversao,
        mapa_hash=calcular_hash_mapa(mapa_reversao) if pseudonimizar else "",
        motores_status=motores_status,
        script_markdown=script_md_final,
        script_json=script_json_payload,
        alertas=list(resultado_motores.alertas or []),
    )
