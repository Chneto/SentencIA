"""Vinculação dinâmica das mídias de audiência em ``data/cases`` ao processo ativo.

Sem nomes fixos: confronta a data da gravação (no nome do arquivo) com as datas da AIJ nas peças e
os nomes após "Vítima"/"Acusado"/"Testemunha" com as partes e demais nomes dos autos.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, List, Optional

from app.core.text_utils import normalizar

MEDIA_EXTENSIONS = frozenset({".mp4", ".mp3", ".wav", ".ogg", ".mkv", ".m4a"})
PDF_EXTENSIONS = frozenset({".pdf"})

_MEDIA_PERSON_STOP_TOKENS = frozenset(
    {
        "da", "de", "do", "das", "dos", "para", "por", "com", "sem",
        "pm", "policial", "militar", "cb", "cabo", "sgt", "sargento", "sd", "soldado",
        "ten", "tenente", "cap", "capitao", "delegado", "delegada", "agente", "perito", "perita",
        "dr", "dra", "sr", "sra",
        "acusacao", "defesa", "abonatoria", "informante", "juizo", "parte",
        "video", "audio", "audiencia", "gravacao",
        "mp", "ministerio", "publico", "oral", "orais", "alegacoes", "finais",
        "sala", "complemento", "continuacao", "reperguntas", "anexo", "trecho",
        "oitiva", "depoimento", "interrogatorio", "declaracoes", "declaracao", "termo",
        "jvd", "jvdfm", "pje", "div", "final", "inicio",
    }
)


def descrever_extensoes_midia() -> str:
    """Texto legível com TODAS as extensões aceitas (ex.: ".m4a, .mkv, .mp3, .mp4, .ogg ou .wav")."""
    exts = sorted(MEDIA_EXTENSIONS)
    return ", ".join(exts[:-1]) + " ou " + exts[-1]


def _normalize_match_text(text: str) -> str:
    """Compatibilidade: alias de ``app.core.text_utils.normalizar``."""
    return normalizar(text)


def _extract_aij_dates_from_pecas(pecas: List[Any]) -> set[str]:
    """Extrai datas (YYYYMMDD) da(s) audiência(s) de instrução e julgamento das peças."""
    aij_dates: set[str] = set()
    fallback_dates: set[str] = set()
    for p in pecas:
        cat = str(getattr(p, "categoria", "") or "")
        titulo_norm = normalizar(str(getattr(p, "titulo", "") or ""))
        if "custodia" in titulo_norm:
            continue
        if cat != "TERMO_AUDIENCIA" and not ("termo de audiencia" in titulo_norm and cat != "DECISAO_MPU"):
            continue

        conteudo_norm = normalizar(str(getattr(p, "conteudo", "") or ""))
        for pat in (
            r"\baudiencia(?:\s+de\s+instrucao(?:\s+e\s+julgamento)?)?(?:\s+una)?(?:\s+realizada)?\s*(?:em|no\s+dia|:)?\s*(\d{2})/(\d{2})/(\d{4})",
            r"\btermo\s+de\s+audiencia(?:\s+de\s+instrucao(?:\s+e\s+julgamento)?)?\s*(?:em|do\s+dia|:|-)?\s*(\d{2})/(\d{2})/(\d{4})",
            r"\bem\s+(\d{2})/(\d{2})/(\d{4})\s+as\s+\d{1,2}[:h]\d{2}(?=\s*,?\s*(?:na\s+sala|neste\s+juizado|sob\s+a\s+presidencia|presente|aberta|audiencia))",
        ):
            for dd, mm, yyyy in re.findall(pat, conteudo_norm):
                if 1 <= int(mm) <= 12 and 1 <= int(dd) <= 31:
                    aij_dates.add(f"{yyyy}{mm}{dd}")

        data_peca = str(getattr(p, "data_peca", "") or "").strip()
        m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", data_peca)
        if m_iso:
            iso_compact = f"{m_iso.group(1)}{m_iso.group(2)}{m_iso.group(3)}"
            if "certid" not in titulo_norm:
                aij_dates.add(iso_compact)
            else:
                fallback_dates.add(iso_compact)

    return aij_dates or fallback_dates


def _extract_date_from_media_filename(filename: str) -> Optional[str]:
    """Extrai data YYYYMMDD do nome do arquivo de mídia (ex.: 20260305 ou 05-03-2026)."""
    stem = Path(filename).stem
    for yyyy, mm, dd in re.findall(r"(?<!\d)(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)(?!\d)", stem):
        if 1 <= int(mm) <= 12 and 1 <= int(dd) <= 31:
            return f"{yyyy}{mm}{dd}"
    for dd, mm, yyyy in re.findall(r"(?<!\d)([0-3]\d)[-_]([01]\d)[-_](20\d{2})(?!\d)", stem):
        if 1 <= int(mm) <= 12 and 1 <= int(dd) <= 31:
            return f"{yyyy}{mm}{dd}"
    return None


def _extract_role_and_person_tokens_from_media_filename(filename: str) -> tuple[Optional[str], List[str]]:
    """Extrai o papel processual e os tokens do nome próprio após Vítima/Acusado/Testemunha."""
    stem = Path(filename).stem
    stem_no_part = re.sub(r"[_-]\d{2,3}$", "", stem)
    norm_name = normalizar(stem_no_part.replace("_", " "))

    role_patterns = (
        ("OFENDIDA", r"\b(?:da\s+|de\s+)?(?:vitima|ofendida|declarante)\b\s*[:-]?\s*(.+)$"),
        ("INTERROGATORIO_REU", r"\b(?:do\s+|de\s+)?(?:acusado|reu)\b\s*[:-]?\s*(.+)$"),
        ("INTERROGATORIO_REU", r"\binterrogatorio\s+(?:da|do|de)\b\s*[:-]?\s*(.+)$"),
        (
            "TESTEMUNHA",
            r"\b(?:da\s+|de\s+|do\s+)?(?:testemunha(?:\s+(?:de\s+(?:acusacao|defesa)|informante|do\s+juizo))?|informante)\b\s*[:-]?\s*(.+)$",
        ),
        ("DEPOENTE", r"\b(?:declaracoes|declaracao|depoimento|oitiva)\s+(?:da|do|de)\b\s*[:-]?\s*(.+)$"),
    )
    for role, pattern in role_patterns:
        match = re.search(pattern, norm_name)
        if match:
            tail = match.group(1)
            tokens = [tok for tok in re.findall(r"[a-z]{3,}", tail) if tok not in _MEDIA_PERSON_STOP_TOKENS]
            return role, tokens
    return None, []


def _tokens_nome(nome: str) -> set[str]:
    return {tok for tok in re.findall(r"[a-z]{3,}", normalizar(nome)) if tok not in _MEDIA_PERSON_STOP_TOKENS}


def _filter_media_files_for_case(
    media_files: List[Path],
    pecas: List[Any],
    known_ofendida: str,
    known_reu: str,
) -> List[Path]:
    """Filtra dinamicamente as mídias de audiência pertencentes ao processo ativo."""
    if not media_files or not pecas:
        return media_files

    aij_dates = _extract_aij_dates_from_pecas(pecas)
    ofendida_tokens = _tokens_nome(known_ofendida)
    reu_tokens = _tokens_nome(known_reu)
    case_text_norm = normalizar(
        " ".join(f"{getattr(p, 'titulo', '')} {getattr(p, 'conteudo', '')}" for p in pecas)
    )

    filtradas: List[Path] = []
    for m_path in media_files:
        media_date = _extract_date_from_media_filename(m_path.name)
        if aij_dates and media_date and media_date not in aij_dates:
            continue

        role, person_tokens = _extract_role_and_person_tokens_from_media_filename(m_path.name)
        if person_tokens:
            if role == "OFENDIDA" and ofendida_tokens:
                if not all(tok in ofendida_tokens for tok in person_tokens):
                    continue
            elif role == "INTERROGATORIO_REU" and reu_tokens:
                if not all(tok in reu_tokens for tok in person_tokens):
                    continue
            elif case_text_norm:
                if not all(re.search(rf"\b{re.escape(tok)}\b", case_text_norm) for tok in person_tokens):
                    continue

        filtradas.append(m_path)
    return filtradas
