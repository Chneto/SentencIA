# -*- coding: utf-8 -*-
"""Identificação de quem fala e extração do que foi alegado em cada depoimento (IA local, sempre conferível).

Princípios (o resultado vai para a fundamentação de uma sentença):
- tudo é **sugestão**: a interface rotula e mantém o texto literal ao lado;
- uma alegação só é aceita se o ``trecho`` citado existe, palavra por palavra, na transcrição
  (alegações sem lastro literal são descartadas e contadas);
- na dúvida, o falante é ``INCERTO`` (nunca se chuta).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.core import ollama_client

logger = logging.getLogger(__name__)

SUFIXO_ANALISE = ".analise_v1.json"
FALANTES = ("JUIZ", "MP", "DEFESA", "DEPOENTE", "INCERTO")
ROTULOS = {"JUIZ": "Juiz(a)", "MP": "Ministério Público", "DEFESA": "Defesa", "DEPOENTE": "Depoente", "INCERTO": "A conferir"}
LOTE = 24


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _impressao(segmentos: List[Dict[str, Any]], papel: str, modelo: str) -> str:
    base = "\n".join(str(s.get("texto_literal") or "") for s in segmentos) + f"|{papel}|{modelo}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def caminho_analise(media_path: Path) -> Path:
    return media_path.with_name(media_path.name + SUFIXO_ANALISE)


def ler_analise(media_path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(caminho_analise(media_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _extrair_json(texto: str) -> Any:
    if not texto:
        return None
    limpo = re.sub(r"^```(?:json)?|```$", "", texto.strip(), flags=re.MULTILINE).strip()
    for ini, fim in (("[", "]"), ("{", "}")):
        a, b = limpo.find(ini), limpo.rfind(fim)
        if a != -1 and b > a:
            try:
                return json.loads(limpo[a : b + 1])
            except ValueError:
                continue
    return None


_DESCRICAO_PAPEL = {
    "OFENDIDA": "a ofendida (vítima), ouvida como declarante",
    "INTERROGATORIO_REU": "o acusado (réu), em interrogatório",
    "TESTEMUNHA_POLICIAL": "testemunha policial",
    "TESTEMUNHA_ACUSACAO": "testemunha de acusação",
    "TESTEMUNHA_DEFESA": "testemunha de defesa",
}


def marcar_falantes(
    segmentos: List[Dict[str, Any]],
    papel: str,
    depoente: str,
    gerar: Callable[..., Optional[str]] = ollama_client.gerar_texto,
) -> List[str]:
    """Rótulo de falante (um por segmento). Falha ou resposta inválida → ``INCERTO``."""
    quem = _DESCRICAO_PAPEL.get(str(papel or "").upper(), "o depoente")
    rotulos = ["INCERTO"] * len(segmentos)
    anteriores: List[str] = []
    for ini in range(0, len(segmentos), LOTE):
        bloco = segmentos[ini : ini + LOTE]
        linhas = "\n".join(f"{ini + k + 1}. {str(s.get('texto_literal') or '').strip()}" for k, s in enumerate(bloco))
        contexto = (
            "Rótulos dos trechos imediatamente anteriores (para continuidade): " + ", ".join(anteriores[-4:]) + "\n"
            if anteriores
            else ""
        )
        prompt = (
            "Você auxilia um magistrado. Abaixo há trechos numerados de uma audiência criminal (violência doméstica), "
            "transcritos automaticamente e com possíveis erros. Indique QUEM FALA em cada trecho, usando só:\n"
            "JUIZ (conduz a audiência, qualifica, adverte, defere, faz perguntas), MP (promotor de justiça), "
            "DEFESA (defensor/advogado), DEPOENTE, INCERTO.\n"
            f"O depoente desta mídia é {quem}: {depoente}. Perguntas costumam vir de JUIZ, MP ou DEFESA; "
            "as respostas e os relatos são do DEPOENTE. Se não houver base no próprio texto, responda INCERTO. "
            "Não invente.\n"
            f"{contexto}Responda SOMENTE com JSON: [{{\"i\": 1, \"f\": \"JUIZ\"}}, ...]\n\nTRECHOS:\n{linhas}"
        )
        resposta = None
        for _tentativa in range(2):  # uma nova tentativa se o modelo local falhar (ex.: HTTP 500 por memória)
            resposta = gerar(prompt, num_predict=1200, temperature=0.0, timeout=240.0, pensar=False)
            if resposta:
                break
        dados = _extrair_json(resposta or "")
        if isinstance(dados, list):
            for item in dados:
                try:
                    i = int(item.get("i")) - 1
                    f = str(item.get("f") or "").upper().strip()
                except (AttributeError, TypeError, ValueError):
                    continue
                if 0 <= i < len(segmentos) and f in FALANTES:
                    rotulos[i] = f
        anteriores.extend(rotulos[ini : ini + len(bloco)])
    return rotulos


def extrair_alegacoes(
    segmentos: List[Dict[str, Any]],
    rotulos: List[str],
    papel: str,
    depoente: str,
    gerar: Callable[..., Optional[str]] = ollama_client.gerar_texto,
) -> Dict[str, Any]:
    """Alegações do depoente, cada uma com o trecho literal que a sustenta (verificado na transcrição)."""
    quem = _DESCRICAO_PAPEL.get(str(papel or "").upper(), "o depoente")
    # só o que o depoente disse (e o que é incerto), com a pergunta anterior para dar contexto
    linhas: List[str] = []
    for i, s in enumerate(segmentos):
        if rotulos[i] in ("DEPOENTE", "INCERTO"):
            linhas.append(f"{i + 1}. {str(s.get('texto_literal') or '').strip()}")
    texto = "\n".join(linhas)
    if len(texto) > 14000:  # limita o contexto enviado ao modelo local
        texto = texto[:14000]
    prompt = (
        "Você auxilia um magistrado. Abaixo estão trechos numerados da fala de "
        f"{quem} ({depoente}) em audiência criminal. Extraia as ALEGAÇÕES RELEVANTES para o julgamento "
        "(fatos narrados, agressões, ameaças, local e data, relação entre as partes, negativa ou confissão, "
        "medidas protetivas, perdão ou retratação, versão da defesa). IGNORE qualificação (nome, idade, profissão, "
        "endereço), cumprimentos, advertências e questões de condução da audiência: só interessam fatos ligados à "
        "imputação, à defesa e ao contexto da relação.\n"
        "Para cada alegação devolva: \"i\" (número do trecho), \"alegacao\" (uma frase em 3ª pessoa, neutra, "
        "sem juízo de valor) e \"trecho\" (cópia EXATA, palavra por palavra, de até 25 palavras do próprio "
        "trecho que sustenta a alegação). Não acrescente fatos que não estejam no texto. No máximo 12 itens.\n"
        "Responda SOMENTE com JSON: [{\"i\": 3, \"alegacao\": \"...\", \"trecho\": \"...\"}]\n\nTRECHOS:\n" + texto
    )
    bruto = gerar(prompt, num_predict=2500, temperature=0.0, timeout=360.0, pensar=False) or ""
    dados = _extrair_json(bruto)
    aceitas: List[Dict[str, Any]] = []
    descartadas = 0
    if isinstance(dados, list):
        norm_seg = [_norm(str(s.get("texto_literal") or "")) for s in segmentos]
        for item in dados:
            if not isinstance(item, dict):
                continue
            trecho = _norm(str(item.get("trecho") or ""))
            alegacao = str(item.get("alegacao") or "").strip()
            if len(trecho) < 12 or not alegacao:
                descartadas += 1
                continue
            idx = None
            try:
                i = int(item.get("i")) - 1
                if 0 <= i < len(segmentos) and trecho in norm_seg[i]:
                    idx = i
            except (TypeError, ValueError):
                pass
            if idx is None:  # o modelo errou o número: procura o trecho literal em outro segmento
                idx = next((k for k, n in enumerate(norm_seg) if trecho in n), None)
            if idx is None:
                descartadas += 1
                continue
            aceitas.append(
                {
                    "alegacao": alegacao,
                    "trecho": str(segmentos[idx].get("texto_literal") or "").strip(),
                    "timestamp": str(segmentos[idx].get("timestamp") or ""),
                    "segmento": idx,
                }
            )
    return {"alegacoes": aceitas, "descartadas_sem_lastro": descartadas, "resposta_valida": isinstance(dados, list)}


def analisar_midia(
    media_path: Path,
    transcricao: Dict[str, Any],
    modelo: str,
    *,
    gerar: Callable[..., Optional[str]] = ollama_client.gerar_texto,
    forcar: bool = False,
) -> Dict[str, Any]:
    """Analisa uma mídia (com cache em disco enquanto a transcrição e o modelo não mudarem)."""
    segmentos = [s for s in (transcricao.get("segmentos") or []) if str(s.get("texto_literal") or "").strip()]
    papel = str(transcricao.get("papel") or "")
    depoente = str(transcricao.get("depoente") or "depoente")
    marca = _impressao(segmentos, papel, modelo)
    if not forcar:
        antigo = ler_analise(media_path)
        if antigo and antigo.get("impressao") == marca:
            return antigo
    rotulos = marcar_falantes(segmentos, papel, depoente, gerar)
    alegacoes = extrair_alegacoes(segmentos, rotulos, papel, depoente, gerar)
    resultado = {
        "impressao": marca,
        "modelo": modelo,
        "media_id": transcricao.get("media_id", ""),
        "falantes": rotulos,
        "total_segmentos": len(segmentos),
        "confiavel": any(r != "INCERTO" for r in rotulos),
        **alegacoes,
    }
    try:
        caminho_analise(media_path).write_text(json.dumps(resultado, ensure_ascii=False), encoding="utf-8")
    except OSError:
        logger.warning("Não foi possível gravar a análise da mídia em disco.", exc_info=True)
    return resultado

