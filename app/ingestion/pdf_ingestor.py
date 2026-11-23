"""Ingestão de autos em PDF (PJe/eproc).

- Segmentação pela árvore de bookmarks (TOC) respeitando os NÍVEIS: a página final de uma peça é a
  página anterior ao próximo marcador de nível igual ou superior (filhos não truncam o pai).
- OCR por plataforma, opcional e preguiçoso: ``winocr`` (Windows), ``ocrmac`` (macOS) ou nenhum.
  As páginas são renderizadas em sequência (PyMuPDF não é thread-safe) e reconhecidas em paralelo
  num pool de threads reutilizado entre chamadas. Sem motor de OCR, a página é marcada como
  ``ocr_pendente`` — nada é inventado.
- Cache em disco validado pelo **sha256** do conteúdo (não pelo tamanho), gravado de forma atômica.
- ``data_peca`` fica vazia quando o marcador não traz data (nunca a data de hoje).
- O documento PyMuPDF é sempre fechado, inclusive em exceção.
"""

from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import contextlib
import datetime
import functools
import hashlib
import io
import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import pymupdf

from app.core.platform_utils import IS_MACOS, IS_WINDOWS
from app.ingestion.imputacoes import contem_ato_judicial_sobre_imputacoes, titulo_indica_aditamento

logger = logging.getLogger(__name__)

# Versão do esquema do cache ``<arquivo>.pdf.pje_cache_v2.json`` (o nome do arquivo é mantido por
# compatibilidade com a rotina de reset/arquivamento). Caches de esquemas anteriores — validados só
# pelo tamanho e com ``data_peca`` = data de hoje quando ausente — são simplesmente ignorados.
PDF_CACHE_SCHEMA = 7
PDF_CACHE_SUFFIX = ".pje_cache_v2.json"

# Limiares de texto "substantivo" abaixo dos quais a página é tratada como digitalizada.
_LIMIAR_OCR_SEGMENTACAO = 75
_LIMIAR_OCR_HIBRIDO = 60
_OCR_DPI = 150
_OCR_TIMEOUT_S = 90.0
_OCR_MAX_TIMEOUTS_SEGUIDOS = 3

# Títulos de marcadores sempre irrelevantes para a sentença (não precisam de OCR nem de segmentação).
_TITULOS_SEMPRE_IRRELEVANTES = {"ÍNDICE", "INTIMAÇÃO", "INTIMAÇÃO DE AUDIÊNCIA", "ATO ORDINATÓRIO", "DESPACHO"}

ProgressoCallback = Callable[[str, int, int], None]

_PJE_FOOTER_RE = re.compile(
    r"(?:Num\.?\s*\d+\s*[-–]\s*P[áa]g\.?\s*\d+|P[áa]g\.?\s*Total\s*[-–]?\s*\d+|Assinado eletronicamente por:[^\n]*|https?://\S+|N[úu]mero do documento:\s*\d+)",
    flags=re.IGNORECASE,
)


def _strip_pje_footer(text: str) -> str:
    cleaned = _PJE_FOOTER_RE.sub("", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned.upper() in ("ANEXO", "EM ANEXO"):
        return ""
    return cleaned


# ----------------------------------------------------------------------------------------------
# OCR multiplataforma
# ----------------------------------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def _detectar_motor_ocr() -> Optional[str]:
    """Motor de OCR nativo disponível nesta máquina (importação preguiçosa)."""
    if IS_WINDOWS:
        try:
            import winocr  # type: ignore[import-not-found]  # noqa: F401

            return "winocr"
        except Exception:
            logger.info("OCR indisponível: pacote opcional 'winocr' não instalado (extra ocr-windows).")
            return None
    if IS_MACOS:
        try:
            from ocrmac import ocrmac  # type: ignore[import-not-found]  # noqa: F401

            return "ocrmac"
        except Exception:
            logger.info("OCR indisponível: pacote opcional 'ocrmac' não instalado (extra ocr-macos).")
            return None
    logger.info("OCR nativo indisponível nesta plataforma; páginas digitalizadas ficam marcadas como pendentes.")
    return None


def _motor_ocr() -> Optional[str]:
    if os.environ.get("JVDFM_OCR", "").strip().lower() in {"0", "off", "desligado", "nao", "não"}:
        return None
    return _detectar_motor_ocr()


def motor_ocr_disponivel() -> Optional[str]:
    """Nome do motor de OCR que será usado (``winocr``/``ocrmac``) ou ``None``."""
    return _motor_ocr()


def _ocr_imagem_png(png_bytes: bytes, motor: str) -> str:
    """Reconhece o texto de uma imagem PNG. Executado em thread do pool (sem loop asyncio ativo)."""
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes))
    img.load()
    if motor == "winocr":
        import winocr  # type: ignore[import-not-found]

        async def _reconhecer():
            return await winocr.recognize_pil(img, "pt")

        res = asyncio.run(_reconhecer())
        texto = res.get("text", "") if isinstance(res, dict) else getattr(res, "text", "")
        return (texto or "").strip()
    if motor == "ocrmac":
        from ocrmac import ocrmac  # type: ignore[import-not-found]

        anotacoes = ocrmac.OCR(img, language_preference=["pt-BR"]).recognize()
        linhas = [str(a[0]).strip() for a in (anotacoes or []) if a and str(a[0]).strip()]
        return "\n".join(linhas).strip()
    return ""


_OCR_POOL: Optional[concurrent.futures.ThreadPoolExecutor] = None
_OCR_POOL_LOCK = threading.Lock()


def _obter_pool_ocr() -> concurrent.futures.ThreadPoolExecutor:
    """Pool de OCR reutilizado entre chamadas (criado sob demanda)."""
    global _OCR_POOL
    with _OCR_POOL_LOCK:
        if _OCR_POOL is None:
            workers = max(1, min(4, os.cpu_count() or 2))
            _OCR_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="jvdfm-ocr")
        return _OCR_POOL


def _descartar_pool_ocr() -> None:
    """Abandona o pool de OCR (threads presas não são interrompíveis) e cancela tarefas ainda não iniciadas."""
    global _OCR_POOL
    with _OCR_POOL_LOCK:
        pool, _OCR_POOL = _OCR_POOL, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


def _ocr_paginas(
    doc: Any,
    indices: Sequence[int],
    ao_progresso: Optional[ProgressoCallback] = None,
    *,
    falhas_out: Optional[set[int]] = None,
    cancelar: Optional[threading.Event] = None,
) -> Dict[int, str]:
    """OCR das páginas ``indices`` (base 0). Retorna {indice: texto} só para páginas reconhecidas.

    Se ``falhas_out`` for passado, registra os índices de páginas cujo motor ou renderização falhou.
    Se ``cancelar`` for sinalizado, interrompe novas páginas cooperativamente.
    """
    motor = _motor_ocr()
    if not motor or not indices:
        return {}
    pool = _obter_pool_ocr()
    max_em_voo = max(2, getattr(pool, "_max_workers", 2) * 2)  # limita memória de pixmaps
    resultados: Dict[int, str] = {}
    fila: "collections.deque[tuple[int, concurrent.futures.Future]]" = collections.deque()
    total = len(indices)
    feitas = 0

    timeouts_seguidos = 0
    abortado = False
    _notificar(ao_progresso, "ocr", 0, total)

    def _coletar() -> None:
        nonlocal feitas, timeouts_seguidos, abortado
        idx, fut = fila.popleft()
        if abortado or (cancelar is not None and cancelar.is_set()):
            fut.cancel()
            if abortado and falhas_out is not None:
                falhas_out.add(idx)
            feitas += 1
            _notificar(ao_progresso, "ocr", feitas, total)
            return
        try:
            texto = fut.result(timeout=_OCR_TIMEOUT_S)
            timeouts_seguidos = 0
        except Exception as err:  # timeout ou falha do motor: página fica sem OCR e falha é registrada
            fut.cancel()
            if falhas_out is not None:
                falhas_out.add(idx)
            logger.warning("OCR falhou na página %s (%s).", idx + 1, type(err).__name__)
            texto = ""
            if isinstance(err, concurrent.futures.TimeoutError):
                # Thread presa não é interrompível: descarta o pool (os próximos PDFs usam um novo)
                # e cancela o que ainda não começou, em vez de esperar o timeout página a página.
                timeouts_seguidos += 1
                _descartar_pool_ocr()
                if timeouts_seguidos >= _OCR_MAX_TIMEOUTS_SEGUIDOS:
                    abortado = True
                    logger.warning("OCR abortado após %s timeouts seguidos; páginas restantes ficam sem OCR.", timeouts_seguidos)
        if texto:
            resultados[idx] = texto
        feitas += 1
        _notificar(ao_progresso, "ocr", feitas, total)

    for idx in indices:
        if cancelar is not None and cancelar.is_set():
            break
        if abortado:
            if falhas_out is not None:
                falhas_out.add(idx)
            continue
        try:
            png = doc[idx].get_pixmap(dpi=_OCR_DPI).tobytes("png")
        except Exception as err:
            if falhas_out is not None:
                falhas_out.add(idx)
            logger.warning("Não foi possível renderizar a página %s para OCR (%s).", idx + 1, type(err).__name__)
            feitas += 1
            continue
        pool = _obter_pool_ocr()  # pode ter sido recriado após um timeout
        fila.append((idx, pool.submit(_ocr_imagem_png, png, motor)))
        while len(fila) >= max_em_voo:
            _coletar()
    while fila:
        _coletar()
    return resultados


def _notificar(cb: Optional[ProgressoCallback], etapa: str, atual: int, total: int) -> None:
    if cb is None:
        return
    try:
        cb(etapa, atual, total)
    except Exception:  # callback de progresso nunca derruba a ingestão
        logger.debug("Callback de progresso falhou.", exc_info=True)


# ----------------------------------------------------------------------------------------------
# Hash e cache
# ----------------------------------------------------------------------------------------------
def sha256_arquivo(caminho: Path, bloco: int = 1024 * 1024) -> Optional[str]:
    """sha256 hexadecimal do conteúdo do arquivo (``None`` se não puder ser lido)."""
    try:
        h = hashlib.sha256()
        with open(caminho, "rb") as fh:
            for parte in iter(lambda: fh.read(bloco), b""):
                h.update(parte)
        return h.hexdigest()
    except OSError:
        return None


def _caminho_cache(pdf_path: Path) -> Path:
    return pdf_path.with_name(pdf_path.name + PDF_CACHE_SUFFIX)


def _ler_cache(cache_file: Path, sha: str) -> Optional[List[Dict[str, Any]]]:
    try:
        if not cache_file.exists():
            return None
        dados = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        isinstance(dados, dict)
        and dados.get("schema_version") == PDF_CACHE_SCHEMA
        and dados.get("sha256") == sha
        and isinstance(dados.get("pecas"), list)
    ):
        return dados["pecas"]
    return None


def gravar_json_atomico(destino: Path, dados: Any) -> bool:
    """Grava JSON via arquivo temporário no mesmo diretório + ``os.replace``. Nunca levanta."""
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{destino.name}.", suffix=".tmp", dir=str(destino.parent))
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(dados, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, destino)
        tmp_name = None
        return True
    except Exception as err:
        logger.warning("Falha ao gravar cache %s: %s", destino.name, type(err).__name__)
        return False
    finally:
        if tmp_name and os.path.exists(tmp_name):
            with contextlib.suppress(OSError):
                os.remove(tmp_name)


# ----------------------------------------------------------------------------------------------
# Extração página a página (sem TOC)
# ----------------------------------------------------------------------------------------------
def extrair_texto_pdf_hibrido(
    pdf_path: Path,
    *,
    ao_progresso: Optional[ProgressoCallback] = None,
    cancelar: Optional[threading.Event] = None,
) -> List[Dict[str, Any]]:
    """
    Lê as páginas de um PDF via PyMuPDF e aplica OCR nativo (quando disponível) nas páginas
    digitalizadas. ``ocr_aplicado`` só é verdadeiro quando o OCR efetivamente devolveu texto.
    """
    falhas_ocr: set[int] = set()
    with contextlib.closing(pymupdf.open(str(pdf_path))) as doc:
        brutos = [doc[i].get_text("text").strip() for i in range(len(doc))]
        precisa_ocr = [i for i, t in enumerate(brutos) if len(_strip_pje_footer(t)) < _LIMIAR_OCR_HIBRIDO]
        ocr = _ocr_paginas(doc, precisa_ocr, ao_progresso, falhas_out=falhas_ocr, cancelar=cancelar)
    sem_motor = _motor_ocr() is None
    paginas: List[Dict[str, Any]] = []
    for idx, texto in enumerate(brutos):
        ocr_txt = ocr.get(idx, "")
        paginas.append(
            {
                "pagina": idx + 1,
                "doc_id": f"ID-PDF-{idx + 1:04d}",
                "folha": f"fl. {idx + 1}",
                "ocr_aplicado": bool(ocr_txt),
                "ocr_pendente": idx in precisa_ocr and not ocr_txt and sem_motor,
                "ocr_falhou": idx in falhas_ocr,
                "conteudo": f"[OCR Página {idx + 1}] {ocr_txt}" if ocr_txt else texto,
            }
        )
    return paginas


# ----------------------------------------------------------------------------------------------
# Inferência de título/fase (regras PJe)
# ----------------------------------------------------------------------------------------------
def _infer_title_and_phase(toc_title: str, body_text: str, doc_id: str) -> tuple[str, str, bool]:
    """
    Retorna (titulo_formatado, fase_processual, is_relevante) para cada peça do PJe.
    """
    base_title = toc_title.split("|")[0].strip()
    body_sem_tag = re.sub(r"\[(?:P[áa]g\.|OCR P[áa]gina)\s*\d+(?:\s*-\s*OCR)?\]", " ", body_text or "", flags=re.IGNORECASE)
    up = body_sem_tag.upper()
    norm_up = re.sub(r"\s+", " ", up).strip()
    head_up = norm_up[:1400]
    base_up = base_title.upper()
    clean_len = len(_strip_pje_footer(body_sem_tag))

    if base_up in ("CABEÇALHO", "ÍNDICE"):
        return ("Capa e Autuação PJe dos Autos Principais", "INQUISITORIAL", True)

    # Ignora capas de anexo de 1 linha ("ANEXO" / "EM ANEXO") mesmo quando acompanhadas de metadados PJe
    if base_up == "ANEXO" and clean_len < 80:
        return (f"{base_title} ({doc_id})", "JUDICIAL", False)
    if clean_len < 40 and ("ANEXO" in norm_up or not norm_up):
        return (f"{base_title} ({doc_id})", "JUDICIAL", False)

    if titulo_indica_aditamento(base_title):
        return (f"Aditamento à denúncia ({doc_id})", "JUDICIAL", True)

    if (
        re.match(r"^(?:DECIS[ÃA]O|SENTEN[ÇC]A|DESPACHO)\b", base_up)
        and "RECEBO A DENÚNCIA" not in norm_up
        and contem_ato_judicial_sobre_imputacoes(body_sem_tag)
    ):
        return (f"Decisão sobre imputações ({doc_id})", "JUDICIAL", True)

    # Petição Inicial que contém Auto de Prisão em Flagrante, BO, Termos e Laudos Periciais
    if base_up == "PETIÇÃO INICIAL":
        if clean_len > 1200 or any(
            kw in norm_up
            for kw in (
                "AUTO DE PRISÃO EM FLAGRANTE",
                "BOLETIM DE OCORRÊNCIA",
                "LAUDO DE PERÍCIA",
                "EXAME DE LESÃO CORPORAL",
                "TERMO DE DECLARAÇÕES",
                "TERMO DE DEPOIMENTO",
                "NOTA DE CULPA",
            )
        ):
            return (
                f"Auto de Prisão em Flagrante, Boletim de Ocorrência, Termos e Laudos Periciais — Polícia Civil ({doc_id})",
                "INQUISITORIAL",
                True,
            )
        return (f"{base_title} ({doc_id})", "JUDICIAL", False)

    # Filtra intimações automáticas de secretaria e atos ordinatórios meramente cartorários
    if base_up in ("INTIMAÇÃO", "INTIMAÇÃO DE AUDIÊNCIA", "ATO ORDINATÓRIO", "DESPACHO"):
        return (f"{base_title} ({doc_id})", "JUDICIAL", False)

    # 1. Alegações Finais (MP ou Defesa) — inclusive quando protocoladas como Petição Incidental
    if (
        "APRESENTAR SUAS ALEGAÇÕES FINAIS" in norm_up
        or "APRESENTAR ALEGAÇÕES FINAIS" in norm_up
        or "OFERTA DE ALEGAÇÕES FINAIS" in norm_up
        or "AS DEVIDAS ALEGAÇÕES FINAIS" in norm_up
        or "ALEGAÇÕES FINAIS" in head_up
        or "MEMORIAIS" in head_up
    ):
        head_top = norm_up[:550]
        if (
            "MINISTÉRIO PÚBLICO" in head_top
            or "PROMOTORIA DE JUSTIÇA" in head_top
            or "PROMOTOR DE JUSTIÇA" in head_up
            or "PROMOTORA DE JUSTIÇA" in head_up
        ) and "DEFENSORIA" not in head_top:
            return (f"Alegações Finais por Memoriais do Ministério Público ({doc_id})", "JUDICIAL", True)
        if (
            "DEFENSORIA PÚBLICA" in head_up
            or "DEFENSORIA CRIMINAL" in head_up
            or "DEFENSOR PÚBLICO" in norm_up
            or "DEFENSORA PÚBLICA" in norm_up
            or "PUGNANDO PELA ABSOLVIÇÃO" in norm_up[:2200]
            or "DO PEDIDO DE ABSOLVIÇÃO" in norm_up
            or "SEJA O ACUSADO ABSOLVIDO" in norm_up
            or "APENAMENTO DO ACUSADO" in norm_up
        ):
            return (f"Alegações Finais por Memoriais da Defesa Técnica ({doc_id})", "JUDICIAL", True)
        if "MINISTÉRIO PÚBLICO" in head_up or "PROMOTORIA DE JUSTIÇA" in head_up:
            return (f"Alegações Finais por Memoriais do Ministério Público ({doc_id})", "JUDICIAL", True)

    # 2. Resposta à Acusação (art. 396 e 396-A do CPP)
    if (
        "RESPOSTA À ACUSAÇÃO" in base_up
        or "OFERECER RESPOSTA À ACUSAÇÃO" in norm_up
        or ("RESPOSTA À ACUSAÇÃO" in head_up and ("396" in norm_up or "DEFENSORIA" in norm_up))
    ):
        return (f"Resposta à Acusação — Defensoria Pública / Defesa Técnica ({doc_id})", "JUDICIAL", True)

    # 3. Termo de Audiência de Instrução e Julgamento (AIJ) vs Audiência de Custódia
    if "ATA DA AUDIÊNCIA" in base_up or "TERMO DE AUDIÊNCIA" in base_up or "TERMO DE AUDIÊNCIA" in head_up:
        if clean_len < 60:
            return (f"{base_title} ({doc_id})", "JUDICIAL", False)
        is_aij = (
            "AUDIÊNCIA DE INSTRUÇÃO" in norm_up
            or "INSTRUÇÃO E JULGAMENTO" in norm_up
            or "ALEGAÇÕES FINAIS ORAIS" in norm_up
            or "INTERROGATÓRIO DO ACUSADO" in norm_up
        )
        is_custodia = (
            "AUDIÊNCIA DE CUSTÓDIA" in head_up
            or "CENTRAL DE FLAGRANTES" in head_up
            or "CONCEDO LIBERDADE PROVISÓRIA" in norm_up
            or "HOMOLOGO O FLAGRANTE" in norm_up
            or "HOMOLOGAÇÃO DO FLAGRANTE" in norm_up
        )
        if is_aij and not ("CENTRAL DE FLAGRANTES" in head_up or "CONCEDO LIBERDADE PROVISÓRIA" in norm_up):
            return (f"Termo de Audiência de Instrução e Julgamento ({doc_id})", "JUDICIAL", True)
        if is_custodia:
            return (
                f"Termo de Audiência de Custódia e Decisão de Liberdade Provisória / Medidas Protetivas ({doc_id})",
                "MEDIDA_PROTETIVA",
                True,
            )
        return (f"Termo de Audiência de Instrução e Julgamento ({doc_id})", "JUDICIAL", True)

    # 3b. Alvará de Soltura / Termo de Liberdade Provisória
    if "ALVARÁ" in base_up or "ALVARÁ DE SOLTURA" in head_up:
        return (f"Alvará de Soltura / Liberdade Provisória ({doc_id})", "MEDIDA_PROTETIVA", True)

    # 4. Certidão de Antecedentes Criminais
    if "CERTIDÃO DE ANTECEDENTES CRIMINAIS" in base_up or "CERTIDÃO DE ANTECEDENTES CRIMINAIS" in head_up:
        return (f"Certidão Estadual de Antecedentes Criminais — TJ ({doc_id})", "JUDICIAL", True)

    # 5. Decisões Judiciais (Recebimento da Denúncia, Revogação de MPU, Saneamento Art. 397 CPP)
    if "RECEBIMENTO DE DENÚNCIA" in head_up or ("DECISÃO" in base_up and "RECEBO A DENÚNCIA" in norm_up):
        return (f"Decisão de Recebimento da Denúncia e Extinção Parcial de Punibilidade ({doc_id})", "JUDICIAL", True)

    if "DECISÃO" in base_up:
        if "REVOGO AS MEDIDAS PROTETIVAS" in norm_up or "REVOGAÇÃO DAS MEDIDAS PROTETIVAS" in norm_up or "SEJAM REVOGADAS" in norm_up:
            return (f"Decisão Judicial de Revogação de Medidas Protetivas de Urgência ({doc_id})", "MEDIDA_PROTETIVA", True)
        if "ART. 397" in norm_up or "ABSOLVIÇÃO SUMÁRIA" in norm_up or "DESIGNO AUDIÊNCIA DE INSTRUÇÃO" in norm_up:
            return (f"Decisão Interlocutória de Saneamento (Art. 397 do CPP) e Designação de AIJ ({doc_id})", "JUDICIAL", True)

    # 6. Denúncia Ministerial
    if "VEM OFERECER DENÚNCIA" in norm_up or "OFERECE DENÚNCIA" in norm_up or ("DENÚNCIA" in base_up and clean_len >= 120):
        return (f"Denúncia Ministerial — Ministério Público Estadual ({doc_id})", "JUDICIAL", True)

    # 7. Inquérito Policial Principal / Relatório Final
    if "INQUÉRITO POLICIAL" in base_up:
        return (f"Inquérito Policial, Boletim de Ocorrência e Termo de Declarações — DEAM ({doc_id})", "INQUISITORIAL", True)

    # 8. Manifestações sobre Medidas Protetivas, Parecer Ministerial e Habilitação da Defensoria
    if (
        "REVOGAÇÃO DE MPU" in norm_up
        or "REVOGAÇÃO DAS MEDIDAS PROTETIVAS" in norm_up
        or "REVOGAR AS MEDIDAS PROTETIVAS" in norm_up
        or "NÃO TEM MAIS INTERESSE" in norm_up
        or ("MEDIDAS PROTETIVAS" in norm_up and "REVOGADAS" in norm_up and "MANIFESTAÇÃO" in norm_up)
    ):
        return (f"Termo / Manifestação sobre Medidas Protetivas de Urgência ({doc_id})", "MEDIDA_PROTETIVA", True)

    if base_up == "TERMO DE DECLARAÇÃO":
        return (f"Termo / Manifestação sobre Medidas Protetivas de Urgência ({doc_id})", "MEDIDA_PROTETIVA", True)

    if base_up == "HABILITAÇÃO" or (base_up == "PETIÇÃO" and "HABILITAÇÃO" in norm_up and "DEFENSORIA" in norm_up):
        return (f"Habilitação da Defensoria Pública / Assistência Qualificada ({doc_id})", "JUDICIAL", True)

    # 9. Certidões Relevantes (Decurso de Prazo, Juízo 100% Digital, Endereço, Juntada de Mídia)
    if "CERTIDÃO DE DECURSO DE PRAZO" in base_up or "DECURSO DE PRAZO" in head_up:
        return (f"Certidão Cartorária de Decurso de Prazo ({doc_id})", "JUDICIAL", True)

    if base_up == "CERTIDÃO":
        if "JUÍZO 100% DIGITAL" in norm_up and "INTIMAÇÃO" in norm_up:
            return (f"Certidão de Intimação e Adesão ao Juízo 100% Digital ({doc_id})", "MEDIDA_PROTETIVA", True)
        if "MÍDIA" in norm_up or "GRAVAÇÃO DA AUDIÊNCIA" in norm_up or "AUDIOVISUAL" in norm_up or "PJE MÍDIAS" in norm_up:
            if "AUDIÊNCIA DE CUSTÓDIA" in norm_up or "CUSTÓDIA" in norm_up:
                return (f"Certidão de Juntada de Mídia — Audiência de Custódia ({doc_id})", "MEDIDA_PROTETIVA", True)
            return (f"Certidão de Juntada de Mídias Audiovisuais da Instrução ({doc_id})", "JUDICIAL", True)
        if any(
            kw in norm_up
            for kw in (
                "COMPROVANTE DE ENDEREÇO",
                "COMPROVANTE DE RESIDÊNCIA",
                "ENDEREÇO ATUALIZADO",
                "ATUALIZAÇÃO DE ENDEREÇO",
                "CONTA DE ENERGIA",
                "FATURA DE ENERGIA",
                "CONTA DE ÁGUA",
                "NOVO ENDEREÇO",
            )
        ):
            return (f"Certidão e Comprovante de Endereço Atualizado do Réu ({doc_id})", "JUDICIAL", True)

    # 10. Certidões e Diligências Policiais da DEAM (sem confundir com cabeçalho AUTOR: DEAM)
    if (
        "NÃO FOI ENCONTRADO AS IMAGENS" in norm_up
        or "ORDEM DE MISSÃO POLICIAL" in norm_up
        or ("POLÍCIA CIVIL" in head_up and "DEAM" in head_up)
        or "DELEGADO DE POLÍCIA" in norm_up
        or "ESCRIVÃ DE POLÍCIA" in norm_up
    ):
        return (f"Certidão e Relatório de Diligências Policiais — DEAM ({doc_id})", "INQUISITORIAL", True)

    # 11. Certidões de Oficial de Justiça e Registros de Diligências (Citação / Intimação)
    if base_up in ("CITAÇÃO", "DEVOLUÇÃO DE MANDADO", "DILIGÊNCIA"):
        return (f"Certidão de Oficial de Justiça — Diligência de Citação/Intimação ({doc_id})", "JUDICIAL", True)

    if "MANIFESTAÇÃO DO MP PARA DELEGACIA" in base_up:
        return (f"Requisição Ministerial de Diligências Complementares ({doc_id})", "INQUISITORIAL", True)

    return (f"{base_title} ({doc_id})", "JUDICIAL", False)


def _detectar_subtitulo_pagina_policial(page_txt: str) -> tuple[str, str] | None:
    """Identifica marcadores internos de peças compostas (APF / Inquérito Policial)."""
    norm = re.sub(r"\s+", " ", page_txt or "").upper()
    m_laudo = re.search(r"\b(UN-[A-Z0-9-]{4,20})\b", norm)
    if (
        "LAUDO DE PERÍCIA CRIMINAL" in norm
        or "ATESTADO ODONTOLÓGICO" in norm
        or "EXAME DE LESÃO CORPORAL" in norm
        or "EXAME DE CORPO DE DELITO" in norm
        or m_laudo
    ) and "REQUISIÇÃO DE EXAME PERICIAL" not in norm[:500]:
        cod = m_laudo.group(1) if m_laudo else ""
        suf = f" ({cod})" if cod else ""
        return ("LAUDO", f"Laudo de Perícia Criminal / Exame de Corpo de Delito{suf}")
    if "TERMO DE QUALIFICAÇÃO E INTERROGATÓRIO" in norm or (
        "INTERROGATÓRIO" in norm[:500] and ("CONDUZIDO" in norm[:600] or "AUTUADO" in norm[:600])
    ):
        return ("INTERROGATORIO", "Termo de Qualificação e Interrogatório Policial do Autuado")
    if "TERMO DE DECLARAÇÕES" in norm[:500] or ("DECLARANTE:" in norm[:600] and "POLÍCIA CIVIL" in norm[:400]):
        return ("DECLARACOES", "Termo de Declarações da Ofendida na Polícia Civil")
    if "TERMO DE DEPOIMENTO" in norm[:500]:
        m_dep = re.search(r"TERMO DE DEPOIMENTO\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ\s]{4,45}?)(?:\s*\(|\s+APF|\s+IP|\n)", norm)
        dep_nm = f" — {m_dep.group(1).strip().title()}" if m_dep else ""
        return ("DEPOIMENTO", f"Termo de Depoimento Policial{dep_nm}")
    if "BOLETIM DE OCORRÊNCIA" in norm[:450] and "DADOS DO REGISTRO" in norm[:800]:
        m_bo = re.search(r"BOLETIM DE OCORR[ÊE]NCIA\s*(?:N[º°O\.]?\s*)?(\d{5,12}/\d{4})", norm)
        bo_num = f" nº {m_bo.group(1)}" if m_bo else ""
        return ("BO", f"Boletim de Ocorrência{bo_num}")
    return None


def _subsegmentar_peca_composta(
    doc_id: str,
    titulo_pai: str,
    data_iso: str,
    start_page: int,
    page_entries: List[tuple[int, str, bool]],
) -> List[Dict[str, Any]]:
    """
    Sub-segmenta APFs e Inquéritos Policiais compostos em sub-peças indexadas por páginas exatas
    (BO, Termo da Vítima, Depoimentos, Interrogatório Policial e Laudos Periciais), preservando o ID pai.
    """
    tit_up = titulo_pai.upper()
    if len(page_entries) < 4 or not (
        "AUTO DE PRISÃO EM FLAGRANTE" in tit_up or "INQUÉRITO POLICIAL" in tit_up
    ):
        return []

    blocos: List[Dict[str, Any]] = []
    atual: Dict[str, Any] | None = None

    for pno_1b, p_txt, p_ocr in page_entries:
        det = _detectar_subtitulo_pagina_policial(p_txt)
        if det is not None:
            kind, sub_tit = det
            # Agrupa continuação do mesmo laudo ou BO/termo em páginas adjacentes
            if atual is not None and atual["kind"] == kind and kind in ("BO", "LAUDO", "DECLARACOES", "INTERROGATORIO"):
                atual["pages"].append(pno_1b)
                atual["texts"].append(p_txt)
                atual["ocr"] = atual["ocr"] or p_ocr
            else:
                if atual is not None:
                    blocos.append(atual)
                atual = {
                    "kind": kind,
                    "titulo": sub_tit,
                    "pages": [pno_1b],
                    "texts": [p_txt],
                    "ocr": p_ocr,
                }
        elif atual is not None:
            # Continua pertencendo ao sub-documento corrente se for continuação imediata (máx 3 págs por termo/laudo)
            p_up = re.sub(r"\s+", " ", p_txt.upper())
            if len(atual["pages"]) < 4 and "NOTA DE CULPA" not in p_up and "FORMULÁRIO NACIONAL" not in p_up:
                atual["pages"].append(pno_1b)
                atual["texts"].append(p_txt)
                atual["ocr"] = atual["ocr"] or p_ocr
            else:
                blocos.append(atual)
                atual = None

    if atual is not None:
        blocos.append(atual)

    sub_pecas: List[Dict[str, Any]] = []
    for idx_sub, blk in enumerate(blocos, start=1):
        pg_ini = blk["pages"][0]
        pg_fim = blk["pages"][-1]
        sub_id = f"{doc_id}-SUB-{idx_sub:02d}"
        sub_pecas.append(
            {
                "doc_id": sub_id,
                "parent_doc_id": doc_id,
                "folha": f"fls. {pg_ini:02d}/{pg_fim:02d}",
                "titulo": f"{blk['titulo']} ({doc_id}, fls. {pg_ini:02d}/{pg_fim:02d})",
                "data_peca": data_iso,
                "fase": "INQUISITORIAL",
                "ocr_aplicado": blk["ocr"],
                "conteudo": "\n\n".join(blk["texts"]).strip(),
            }
        )
    return sub_pecas


# ----------------------------------------------------------------------------------------------
# Segmentação pelo TOC
# ----------------------------------------------------------------------------------------------
def _data_do_titulo(raw_title: str) -> str:
    """Data ``dd/mm/aaaa`` do marcador PJe em ISO; vazia quando ausente ou inválida (nunca hoje)."""
    m_dt = re.search(r"(\d{2})/(\d{2})/(\d{4})", raw_title or "")
    if not m_dt:
        return ""
    try:
        return datetime.date(int(m_dt.group(3)), int(m_dt.group(2)), int(m_dt.group(1))).isoformat()
    except ValueError:
        return ""


def _normalizar_toc(toc: Sequence[Any], n_paginas: int) -> List[tuple[int, str, int]]:
    entradas: List[tuple[int, str, int]] = []
    for item in toc or []:
        try:
            lvl, titulo, pagina = int(item[0]), str(item[1]), int(item[2])
        except (TypeError, ValueError, IndexError):
            continue
        if pagina < 1 or (n_paginas and pagina > n_paginas):
            continue  # destino inválido no PDF
        entradas.append((lvl, titulo, pagina))
    return entradas


def _pagina_final(entradas: Sequence[tuple[int, str, int]], i: int, n_paginas: int) -> int:
    """Página final (base 1) da entrada ``i``: antes do próximo marcador de nível igual ou superior."""
    lvl, _titulo, inicio = entradas[i]
    for lvl_j, _t, inicio_j in entradas[i + 1:]:
        if lvl_j <= lvl:
            return max(inicio, inicio_j - 1)
    return max(inicio, n_paginas)


def _classificar_estado_ocr(
    *,
    n_indisponivel: int,
    n_falhou: int,
    n_reconhecidas: int,
) -> str:
    if n_indisponivel > 0:
        return "OCR_INDISPONIVEL"
    if n_falhou > 0:
        return "OCR_FALHOU"
    if n_reconhecidas > 0:
        return "OCR_CONCLUIDO"
    return "NATIVO"


def _peca_unica_sem_toc(
    pdf_path: Path,
    *,
    ao_progresso: Optional[ProgressoCallback] = None,
    cancelar: Optional[threading.Event] = None,
) -> List[Dict[str, Any]]:
    paginas = extrair_texto_pdf_hibrido(pdf_path, ao_progresso=ao_progresso, cancelar=cancelar)
    texto_concat = "\n\n".join(str(p.get("conteudo", "")) for p in paginas).strip()
    paginas_falha = [int(p["pagina"]) for p in paginas if p.get("ocr_falhou")]
    n_falhou = len(paginas_falha)
    n_indisponivel = sum(1 for p in paginas if p.get("ocr_pendente"))
    n_reconhecidas = sum(1 for p in paginas if p.get("ocr_aplicado"))
    ocr_estado = _classificar_estado_ocr(
        n_indisponivel=n_indisponivel,
        n_falhou=n_falhou,
        n_reconhecidas=n_reconhecidas,
    )
    peca: Dict[str, Any] = {
        "doc_id": f"ID-PDF-{pdf_path.stem[:12].upper()}",
        "folha": f"fls. 01/{len(paginas):02d}",
        "titulo": f"Autos Processuais Importados ({pdf_path.name})",
        "data_peca": "",
        "fase": "JUDICIAL",
        "ocr_aplicado": n_reconhecidas > 0,
        "ocr_pendente": n_indisponivel > 0,
        "ocr_indisponivel": n_indisponivel,
        "ocr_paginas_reconhecidas": n_reconhecidas,
        "ocr_estado": ocr_estado,
        "conteudo": texto_concat,
    }
    if n_falhou > 0:
        peca["ocr_falhou"] = n_falhou
        peca["ocr_paginas_falha"] = paginas_falha
    if not texto_concat:
        peca["alertas"] = [
            "Motor de OCR não instalado ou sem texto extraível do PDF. Feche o programa e abra pelo 'Iniciar SentencIA' ou junte PDF pesquisável."
        ]
    return [peca]


def _segmentar_por_toc(
    doc: Any,
    toc: Sequence[Any],
    ao_progresso: Optional[ProgressoCallback] = None,
    *,
    cancelar: Optional[threading.Event] = None,
) -> List[Dict[str, Any]]:
    n_paginas = len(doc)
    entradas = _normalizar_toc(toc, n_paginas)

    # Intervalos (base 0, fim exclusivo) de cada entrada que precisa ser lida.
    intervalos: List[tuple[int, str, int, int]] = []
    paginas_necessarias: set[int] = set()
    for i, (_lvl, raw_title, start_page) in enumerate(entradas):
        base_up = raw_title.split("|")[0].strip().upper()
        if base_up in _TITULOS_SEMPRE_IRRELEVANTES:
            continue
        end_page = _pagina_final(entradas, i, n_paginas)
        intervalos.append((i, raw_title, start_page, end_page))
        paginas_necessarias.update(range(start_page - 1, min(end_page, n_paginas)))

    # 1) Texto nativo de todas as páginas necessárias (sequencial).
    textos: Dict[int, str] = {}
    ordenadas = sorted(paginas_necessarias)
    for k, pno in enumerate(ordenadas, start=1):
        try:
            textos[pno] = doc[pno].get_text("text").strip()
        except Exception as err:
            logger.warning("Falha ao ler o texto da página %s (%s).", pno + 1, type(err).__name__)
            textos[pno] = ""
        if k % 25 == 0 or k == len(ordenadas):
            _notificar(ao_progresso, "texto", k, len(ordenadas))

    # 2) OCR em paralelo das páginas digitalizadas.
    precisa_ocr = [p for p in ordenadas if len(_strip_pje_footer(textos[p])) < _LIMIAR_OCR_SEGMENTACAO]
    falhas_ocr: set[int] = set()
    ocr = _ocr_paginas(doc, precisa_ocr, ao_progresso, falhas_out=falhas_ocr, cancelar=cancelar)
    sem_motor = _motor_ocr() is None

    pecas_extraidas: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    for _i, raw_title, start_page, end_page in intervalos:
        m_num = re.search(r"NUM:\s*(\d+)", raw_title, flags=re.IGNORECASE)
        if raw_title.strip().upper() == "CABEÇALHO":
            doc_id = "ID-CAPA-PJE"
        elif m_num:
            doc_id = f"ID-{m_num.group(1)}"
        else:
            doc_id = f"ID-PJE-{start_page:03d}"

        if doc_id in seen_ids:
            continue

        data_iso = _data_do_titulo(raw_title)

        page_texts: List[str] = []
        page_entries: List[tuple[int, str, bool]] = []
        ocr_used = False
        n_reconhecidas_peca = 0
        n_indisponivel_peca = 0
        paginas_falha_peca: List[int] = []

        for pno in range(start_page - 1, min(end_page, n_paginas)):
            raw_p_txt = textos.get(pno, "")
            if pno in precisa_ocr:
                ocr_txt = ocr.get(pno, "")
                if ocr_txt:
                    p_txt_final = f"[Pág. {pno + 1} - OCR] {ocr_txt}"
                    page_texts.append(p_txt_final)
                    page_entries.append((pno + 1, p_txt_final, True))
                    ocr_used = True
                    n_reconhecidas_peca += 1
                    continue
                if pno in falhas_ocr:
                    paginas_falha_peca.append(pno + 1)
                if sem_motor:
                    n_indisponivel_peca += 1
                if raw_p_txt:
                    page_texts.append(raw_p_txt)
                    page_entries.append((pno + 1, raw_p_txt, False))
            else:
                page_texts.append(raw_p_txt)
                page_entries.append((pno + 1, raw_p_txt, False))

        falhas_na_peca = len(paginas_falha_peca)
        full_body = "\n\n".join(page_texts).strip()
        alertas_peca: List[str] = []
        if not full_body:
            if falhas_na_peca > 0:
                alertas_peca.append(f"Falha no motor de OCR em {falhas_na_peca} página(s) digitalizada(s).")
            elif n_indisponivel_peca > 0:
                alertas_peca.append(
                    f"Motor de OCR não instalado: {n_indisponivel_peca} página(s) digitalizada(s) sem texto."
                )
            else:
                continue

        titulo_fmt, fase_peca, is_relevante = _infer_title_and_phase(raw_title, full_body, doc_id)
        if not is_relevante:
            continue

        seen_ids.add(doc_id)
        ocr_estado = _classificar_estado_ocr(
            n_indisponivel=n_indisponivel_peca,
            n_falhou=falhas_na_peca,
            n_reconhecidas=n_reconhecidas_peca,
        )
        peca: Dict[str, Any] = {
            "doc_id": doc_id,
            "folha": f"fls. {start_page:02d}/{end_page:02d}",
            "titulo": titulo_fmt,
            "data_peca": data_iso,
            "fase": fase_peca,
            "ocr_aplicado": ocr_used,
            "ocr_indisponivel": n_indisponivel_peca,
            "ocr_paginas_reconhecidas": n_reconhecidas_peca,
            "ocr_estado": ocr_estado,
            "conteudo": full_body,
        }
        if alertas_peca:
            peca["alertas"] = alertas_peca
        if n_indisponivel_peca > 0:
            peca["ocr_pendente"] = True
        if falhas_na_peca > 0:
            peca["ocr_falhou"] = falhas_na_peca
            peca["ocr_paginas_falha"] = paginas_falha_peca
        pecas_extraidas.append(peca)
        for sp in _subsegmentar_peca_composta(doc_id, titulo_fmt, data_iso, start_page, page_entries):
            if sp["doc_id"] not in seen_ids:
                seen_ids.add(sp["doc_id"])
                pecas_extraidas.append(sp)

    return pecas_extraidas


def segmentar_autos_pdf_pje(
    pdf_path: Path,
    *,
    usar_cache: bool = True,
    ao_progresso: Optional[ProgressoCallback] = None,
    cancelar: Optional[threading.Event] = None,
) -> List[Dict[str, Any]]:
    """
    Segmenta um PDF integral do PJe/eproc utilizando a árvore de Bookmarks/TOC oficial
    (ex.: 'Petição | NUM: 4100101 | 15/02/2025'), sub-segmenta peças compostas (APF/IP)
    e aplica OCR seletivo (quando houver motor na plataforma) nas páginas digitalizadas.

    Cache em ``<arquivo>.pdf.pje_cache_v2.json`` validado pelo sha256 do conteúdo.
    ``ao_progresso(etapa, atual, total)`` é opcional (etapas: ``texto``, ``ocr``).
    """
    pdf_path = Path(pdf_path)
    sha = sha256_arquivo(pdf_path)
    cache_file = _caminho_cache(pdf_path)
    if usar_cache and sha:
        cached = _ler_cache(cache_file, sha)
        if cached is not None:
            return cached

    pecas: Optional[List[Dict[str, Any]]] = None
    with contextlib.closing(pymupdf.open(str(pdf_path))) as doc:
        toc = doc.get_toc() or []
        if len(toc) >= 3:
            pecas = _segmentar_por_toc(doc, toc, ao_progresso, cancelar=cancelar)
    if pecas is None:
        pecas = _peca_unica_sem_toc(pdf_path, ao_progresso=ao_progresso, cancelar=cancelar)

    if sha and not (cancelar is not None and cancelar.is_set()):
        tamanho = None
        with contextlib.suppress(OSError):
            tamanho = pdf_path.stat().st_size
        gravar_json_atomico(
            cache_file,
            {"schema_version": PDF_CACHE_SCHEMA, "sha256": sha, "size": tamanho, "pecas": pecas},
        )
    return pecas
