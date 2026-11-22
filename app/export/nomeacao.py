"""
Nome do arquivo da sentença exportada.

Padrão: ``Sentença - {tipo} - Proc. {nº do processo} - {sentenciado} - {tipo(s) penal(is)}``

* ``{tipo}``: natureza do julgamento — Condenatória, Absolutória, Parcialmente condenatória,
  Extintiva da punibilidade... — deduzida do veredicto de cada crime (``decisoes_crimes``) e, na
  falta dele (sentença importada/editada), do dispositivo do texto;
* ``{tipo(s) penal(is)}``: artigos efetivamente julgados (``art. 129, § 13, CP; art. 147, CP``).

O nome é seguro para o Windows (sem ``<>:"/\\|?*``, sem ponto/espaço no fim, limitado em tamanho).
Os arquivos novos são reconhecidos pelo reset da sessão por :func:`eh_arquivo_exportacao`, que
continua reconhecendo o prefixo legado ``Sentenca_JVDFM_``.
"""

from __future__ import annotations

import datetime
import re
import unicodedata
from typing import Any, Dict, List

PREFIXO_NOME = "Sentença - "
PREFIXO_LEGADO = "Sentenca_JVDFM_"
SUFIXOS_EXPORTACAO = (".docx", ".pdf", ".html")

_ILEGAIS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_LIMITE_SENTENCIADO = 60
_LIMITE_TIPOS = 80
_LIMITE_TOTAL = 170  # + extensão; deixa folga para o caminho (MAX_PATH = 260 no Windows)


def eh_arquivo_exportacao(nome: str) -> bool:
    """``True`` para exportações geradas pelo sistema (padrão atual e legado)."""
    n = unicodedata.normalize("NFC", str(nome or ""))
    if n.startswith(PREFIXO_LEGADO):
        return True
    return n.startswith(PREFIXO_NOME) and n.lower().endswith(SUFIXOS_EXPORTACAO)


def _limpar(texto: Any, limite: int) -> str:
    t = unicodedata.normalize("NFC", str(texto or "")).replace("/", "-")
    t = _ILEGAIS_RE.sub(" ", t)
    t = re.sub(r"\.{2,}", ".", t)  # nunca ".." (travessia de diretório), mesmo sem barras
    t = re.sub(r"\s+", " ", t).strip(" .")
    if len(t) > limite:
        t = t[:limite].rstrip(" .,;-—–")
    return t


def limpar_nome(texto: Any, limite: int = 60) -> str:
    """Componente de nome de arquivo/pasta seguro para o Windows (sem caracteres ilegais nem ``..``)."""
    return _limpar(texto, limite)


def _veredictos(sentenca: Any) -> List[str]:
    saida: List[str] = []
    for d in getattr(sentenca, "decisoes_crimes", None) or []:
        v = str((d or {}).get("veredicto") or "").strip().lower()
        if v:
            saida.append(v)
    return saida


def _tipo_pelo_texto(texto: str) -> str:
    cauda = (texto or "")[-6000:].upper()
    condena = bool(re.search(r"\bCONDENAR\b|\bCONDENO\b|JULGO\s+(?:PARCIALMENTE\s+)?PROCEDENTE", cauda))
    absolve = bool(re.search(r"\bABSOLVER\b|\bABSOLVO\b|JULGO\s+IMPROCEDENTE", cauda))
    extingue = bool(re.search(r"EXTIN[ÇC][ÃA]O\s+DA\s+PUNIBILIDADE|EXTINGO\s+A\s+PUNIBILIDADE|EXTINGUIR\s+A\s+PUNIBILIDADE", cauda))
    return _rotular(condena, absolve, extingue, desclassifica=False) or "Não classificada"


def _rotular(condena: bool, absolve: bool, extingue: bool, desclassifica: bool) -> str:
    if condena and (absolve or extingue):
        return "Parcialmente condenatória"
    if condena:
        return "Condenatória (desclassificação)" if desclassifica else "Condenatória"
    if absolve and extingue:
        return "Absolutória e extintiva da punibilidade"
    if absolve:
        return "Absolutória"
    if extingue:
        return "Extintiva da punibilidade"
    return ""


def _crimes_da_dosimetria(sentenca: Any) -> List[Dict[str, Any]]:
    dados = getattr(sentenca, "dosimetria_dados", None)
    crimes = (dados or {}).get("crimes_calculados") if isinstance(dados, dict) else None
    return [c for c in (crimes or []) if isinstance(c, dict)]


def tipo_sentenca(sentenca: Any) -> str:
    """Natureza do julgamento (``Condenatória``, ``Absolutória``, ...)."""
    vs = _veredictos(sentenca)
    if not vs:
        # Sentença importada/reconstruída sem veredictos: usa o que a dosimetria calculou.
        dados = getattr(sentenca, "dosimetria_dados", None)
        dados = dados if isinstance(dados, dict) else {}
        if _crimes_da_dosimetria(sentenca) and not dados.get("sem_condenacao"):
            return "Parcialmente condenatória" if dados.get("crimes_nao_condenados") else "Condenatória"
    if vs:
        rotulo = _rotular(
            condena=any(v in ("condenar", "desclassificar") for v in vs),
            absolve=("absolver" in vs),
            extingue=("extinguir" in vs),
            desclassifica=("desclassificar" in vs),
        )
        if rotulo:
            return rotulo
    return _tipo_pelo_texto(str(getattr(sentenca, "texto_completo", "") or ""))


def _compactar_artigo(artigo: str) -> str:
    """``art. 129, § 13, do Código Penal (com a redação...)`` -> ``art. 129, § 13, CP``."""
    a = re.sub(r"\s*\([^)]*\)", "", str(artigo or "")).strip()
    a = re.sub(r"\bdo\s+C[óo]digo\s+Penal\b", "CP", a, flags=re.IGNORECASE)
    a = re.sub(r"\bdo\s+C[óo]digo\s+de\s+Processo\s+Penal\b", "CPP", a, flags=re.IGNORECASE)
    a = re.sub(r"\bda\s+Lei\s+n[ºo°]\s*", "Lei ", a, flags=re.IGNORECASE)
    a = re.sub(r"\bda\s+Lei\s+(?:das\s+)?Contraven[çc][õo]es\s+Penais\b", "LCP", a, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", a).strip(" ,")


def _artigo_do_codigo(codigo: str, data: datetime.date) -> str:
    try:
        from app.legal_engine.penal_types_catalog import get_penal_type_for_date

        return _compactar_artigo(get_penal_type_for_date(codigo, data).artigo_formatado)
    except Exception:  # código desconhecido/tipo inexistente na data: cai no texto do fundamento
        return ""


def tipos_penais(sentenca: Any) -> List[str]:
    """Artigos dos crimes julgados, sem repetição e na ordem da sentença."""
    data = datetime.date.today()
    vistos: dict[str, None] = {}
    for d in getattr(sentenca, "decisoes_crimes", None) or []:
        d = d or {}
        codigo = str(d.get("crime_efetivo") or d.get("crime_code") or "").strip()
        rotulo = _artigo_do_codigo(codigo, data) if codigo else ""
        if not rotulo:
            rotulo = _compactar_artigo(d.get("fundamento_legal") or "")
        if rotulo:
            vistos.setdefault(rotulo, None)
    if not vistos:  # sem decisões por crime: usa os crimes condenados da dosimetria
        for c in _crimes_da_dosimetria(sentenca):
            spec = c.get("crime_spec") or {}
            rotulo = _compactar_artigo(spec.get("artigo_formatado") or "")
            if rotulo:
                vistos.setdefault(rotulo, None)
    return list(vistos)


def _resumir_tipos(tipos: List[str]) -> str:
    """Une os artigos sem cortar um no meio: o que não couber vira ``e outros``."""
    limpos = [_limpar(t, _LIMITE_TIPOS) for t in tipos]
    limpos = [t for t in limpos if t]
    juntos = "; ".join(limpos)
    if len(juntos) <= _LIMITE_TIPOS:
        return juntos
    cabem: List[str] = []
    for t in limpos:
        if len("; ".join(cabem + [t])) + len("; e outros") > _LIMITE_TIPOS:
            break
        cabem.append(t)
    return ("; ".join(cabem) + "; e outros") if cabem else _limpar(juntos, _LIMITE_TIPOS)


def nome_base_sentenca(sentenca: Any, *, rascunho: bool = False) -> str:
    """``Sentença - Condenatória - Proc. 0804590-12.2025.8.00.0001 - NOME - art. 129, § 13, CP`` (sem extensão)."""
    numero = _limpar(getattr(sentenca, "numero_processo", ""), 40) or "sem número"
    reu = _limpar(getattr(sentenca, "reu", ""), _LIMITE_SENTENCIADO) or "sentenciado não identificado"
    partes = [f"Sentença - {tipo_sentenca(sentenca)}", f"Proc. {numero}", reu]
    tipos = _resumir_tipos(tipos_penais(sentenca))
    if tipos:
        partes.append(tipos)
    base = _limpar(" - ".join(partes), _LIMITE_TOTAL)
    return f"{base} (RASCUNHO)" if rascunho else base


def nome_arquivo_sentenca(sentenca: Any, formato: str, *, rascunho: bool = False) -> str:
    ext = re.sub(r"[^a-z0-9]", "", str(formato or "").lower()) or "docx"
    return f"{nome_base_sentenca(sentenca, rascunho=rascunho)}.{ext}"
