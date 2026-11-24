"""Recorte da prova oral que pode alimentar a redação da minuta."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Literal, Mapping

from app.transcription.oral_proof_analyzer import PAPEIS_CONCRETOS

MARCADOR_PAPEL = "[COMPLETAR PELO JUÍZO: confirmar papel]"


@dataclass(frozen=True)
class ProvaOralConfirmada:
    transcricoes: list[dict]
    confirmados: dict[str, list[str]]
    pendentes: dict[str, list[str]]

    def situacao(self, fonte: str, trecho: str) -> Literal["confirmado", "pendente", "ausente"]:
        excerto = " ".join(trecho.split())
        if not excerto:
            return "ausente"
        if any(excerto in texto for texto in self.pendentes.get(fonte, ())):
            return "pendente"
        if any(excerto in texto for texto in self.confirmados.get(fonte, ())):
            return "confirmado"
        return "ausente"


def separar_prova_oral(transcricoes: Iterable[Mapping]) -> ProvaOralConfirmada:
    """Forma grupos pelo papel do segmento que o juízo confirmou."""
    permitidas = []
    confirmados = defaultdict(list)
    pendentes = defaultdict(list)
    for midia in transcricoes:
        if not isinstance(midia, Mapping) or str(midia.get("status") or "").upper() == "FALHOU":
            continue
        fonte = str(midia.get("media_id") or "").strip()
        if not fonte:
            continue
        por_papel = defaultdict(list)
        for seg in midia.get("segmentos") or ():
            if not isinstance(seg, Mapping):
                continue
            texto = seg.get("texto_literal") or seg.get("texto")
            if not isinstance(texto, str) or not texto.strip():
                continue
            trecho = " ".join(texto.split())
            papel = seg.get("papel_segmento")
            confirmado = seg.get("papel_estado") == "CONFIRMADO" and seg.get("papel_confirmado") is True
            if papel not in PAPEIS_CONCRETOS or not confirmado:
                pendentes[fonte].append(trecho)
                continue
            confirmados[fonte].append(trecho)
            por_papel[papel].append(dict(seg))
        for papel, segmentos in por_papel.items():
            depoente = midia.get("depoente") if midia.get("papel") == papel else None
            permitidas.append({
                "media_id": fonte,
                "arquivo": midia.get("arquivo") or "",
                "duracao": midia.get("duracao") or "",
                "depoente": depoente or "Pessoa ouvida",
                "papel": papel,
                "papel_confirmado": True,
                "segmentos": segmentos,
            })
    return ProvaOralConfirmada(permitidas, dict(confirmados), dict(pendentes))
