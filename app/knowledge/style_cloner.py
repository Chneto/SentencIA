"""
Perfil de estilo do gabinete e cadastro de amostras de sentença.

v2.0.0 — o que este módulo faz de fato (sem "clonagem" mágica):

- ``get_style_profile()`` devolve o perfil de estilo usado pelos geradores (aberturas, fechos e
  conectivos preferidos) e o catálogo de modelos cadastrados.
- ``register_cabinet_sentence_sample()`` GUARDA A AMOSTRA COMPLETA em
  ``data/templates_gabinete/amostras/<MOD-NN>.txt`` (escrita atômica, com sha256) e extrai um
  PERFIL OBSERVADO simples e verificável: abertura do relatório, fecho do relatório, abertura do
  dispositivo e frequência de conectivos. O perfil observado é registrado no modelo e agregado em
  ``perfil_observado`` — não substitui automaticamente as fórmulas do perfil ativo (o gabinete
  decide se adota).

O perfil padrão não contém números de processos reais (segredo de justiça / LGPD).
"""

import copy
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import TEMPLATES_DIR

logger = logging.getLogger(__name__)

STYLE_PROFILE_FILE = TEMPLATES_DIR / "style_profile.json"
AMOSTRAS_DIR = TEMPLATES_DIR / "amostras"

_LOCK = threading.RLock()

#: Conectivos forenses cuja frequência é medida nas amostras do gabinete.
CONECTIVOS_MONITORADOS: List[str] = [
    "Com efeito,",
    "Sob essa ótica,",
    "Cumpre ressaltar",
    "Nesse diapasão,",
    "Outrossim,",
    "Ademais,",
    "Nesse sentido,",
    "Dessa forma,",
    "Assim,",
    "Por conseguinte,",
    "Destarte,",
    "Todavia,",
    "Contudo,",
    "No caso dos autos,",
    "In casu,",
    "Registre-se",
    "Ressalte-se",
    "De fato,",
    "Diante disso,",
    "Isso posto,",
    "Ante o exposto,",
]

DEFAULT_STYLE_PROFILE: Dict[str, Any] = {
    "nome_perfil": "Padrão Magistratura Especializada JVDFM (Clássico-Contemporâneo)",
    "abertura_relatorio": "Vistos etc.",
    "fechamento_relatorio": "É o relatório do essencial. Passo a decidir e fundamentar (art. 93, inciso IX, da Constituição Federal e art. 381 do Código de Processo Penal).",
    "abertura_dispositivo": "Ante o exposto, e por tudo mais que dos autos consta, JULGO PROCEDENTE a pretensão punitiva estatal deduzida na denúncia para:",
    "conectivos_preferidos": [
        "Com efeito,",
        "Sob essa ótica,",
        "Cumpre ressaltar, ademais, que",
        "Nesse diapasão,",
        "Outrossim,",
    ],
    "modelos_cadastrados": [
        {
            "id": "MOD-01",
            "titulo": "Sentença Condenatória Padrão — Lesão Corporal + Ameaça + Art. 24-A LMP",
            "tipo": "CONDENATORIA",
            "resumo": "Estrutura completa com análise de preliminares, perspectiva de gênero (Res. CNJ 492/2023), dosimetria em fração de 1/8 do intervalo, Tema 983/STJ e art. 21 da Lei 11.340/2006.",
        },
        {
            "id": "MOD-02",
            "titulo": "Sentença Condenatória — Perseguição (Stalking — Art. 147-A, § 1º, II, CP) + Decadência (Art. 139 CP) + Sursis Penal",
            "tipo": "CONDENATORIA",
            "resumo": "Modelo do gabinete (.docx): extinção parcial pela decadência (art. 139 CP), mérito sob perspectiva de gênero (Res. CNJ 492/2023), causa de aumento de 1/2, sursis penal (art. 77 CP) e dano moral in re ipsa (Tema 983/STJ).",
        },
        {
            "id": "MOD-03",
            "titulo": "Sentença Condenatória — Lesão Corporal (Art. 129, § 13, CP) + Violação de Domicílio via Emendatio Libelli (Art. 150, caput, CP) + Ameaça (Art. 147, caput, CP)",
            "tipo": "CONDENATORIA",
            "resumo": "Modelo do gabinete (.docx): exame pericial traumatológico, afastamento de consunção (STJ, Informativo 846), emendatio libelli (art. 383 CPP), confissão espontânea, sursis penal com condições especiais (arts. 77 a 82 do CP) e reparação mínima (Súmulas 54 e 362/STJ).",
        },
    ],
}

#: Número único CNJ de processo (NNNNNNN-DD.AAAA.J.TR.OOOO) — não pode constar do perfil padrão.
_PADRAO_NUMERO_CNJ = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")


def _escrever_atomico(destino: Path, texto: str) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{destino.name}.", suffix=".tmp", dir=str(destino.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(texto)
            fh.flush()
            os.fsync(fh.fileno())
        for tentativa in range(5):
            try:
                os.replace(tmp, destino)
                return
            except PermissionError:
                if tentativa == 4:
                    raise
                time.sleep(0.05 * (tentativa + 1))
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _salvar_perfil(profile: Dict[str, Any]) -> None:
    _escrever_atomico(STYLE_PROFILE_FILE, json.dumps(profile, ensure_ascii=False, indent=2))


def _sanear_modelos_padrao(loaded: Dict[str, Any]) -> bool:
    """
    Garante os modelos padrão e remove números de processos reais dos resumos dos modelos
    padrão herdados de versões anteriores. Retorna ``True`` se alterou o perfil.
    """
    alterado = False
    modelos = loaded.setdefault("modelos_cadastrados", [])
    if not isinstance(modelos, list):
        loaded["modelos_cadastrados"] = modelos = []
        alterado = True
    padrao_por_id = {m["id"]: m for m in DEFAULT_STYLE_PROFILE["modelos_cadastrados"]}
    existentes = {m.get("id") for m in modelos if isinstance(m, dict)}
    for mod in DEFAULT_STYLE_PROFILE["modelos_cadastrados"]:
        if mod["id"] not in existentes:
            modelos.append(copy.deepcopy(mod))
            alterado = True
    for mod in modelos:
        if not isinstance(mod, dict):
            continue
        if mod.get("id") in padrao_por_id and _PADRAO_NUMERO_CNJ.search(str(mod.get("resumo", ""))):
            mod["resumo"] = padrao_por_id[mod["id"]]["resumo"]
            alterado = True
    return alterado


def get_style_profile() -> Dict[str, Any]:
    """Perfil de estilo ativo (cria o padrão em disco se ausente; tolera arquivo corrompido)."""
    with _LOCK:
        if STYLE_PROFILE_FILE.exists():
            try:
                loaded = json.loads(STYLE_PROFILE_FILE.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    for chave in ("abertura_relatorio", "fechamento_relatorio", "abertura_dispositivo", "conectivos_preferidos"):
                        loaded.setdefault(chave, copy.deepcopy(DEFAULT_STYLE_PROFILE[chave]))
                    if _sanear_modelos_padrao(loaded):
                        try:
                            _salvar_perfil(loaded)
                        except OSError as exc:
                            logger.warning("Não foi possível atualizar o perfil de estilo: %s", exc)
                    return loaded
            except (OSError, ValueError) as exc:
                logger.warning("Perfil de estilo ilegível (%s); usando o padrão: %s", STYLE_PROFILE_FILE, exc)
                return copy.deepcopy(DEFAULT_STYLE_PROFILE)
        perfil = copy.deepcopy(DEFAULT_STYLE_PROFILE)
        try:
            _salvar_perfil(perfil)
        except OSError as exc:
            logger.warning("Não foi possível gravar o perfil de estilo padrão: %s", exc)
        return perfil


# ------------------------------------------------------------------------------
# Extração simples e verificável do perfil observado numa amostra
# ------------------------------------------------------------------------------

_CAB_RELATORIO = re.compile(r"(?im)^\s*I\s*[-–—]\s*RELAT[ÓO]RIO\b.*$")
_CAB_FUNDAMENTACAO = re.compile(r"(?im)^\s*II\s*[-–—]\s*FUNDAMENTA[ÇC][ÃA]O\b.*$")
_CAB_DISPOSITIVO = re.compile(r"(?im)^\s*III\s*[-–—]\s*DISPOSITIVO\b.*$")


def _paragrafos(texto: str) -> List[str]:
    return [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n|\r?\n", texto or "") if p.strip()]


def _primeira_frase(paragrafo: str, limite: int = 220) -> str:
    m = re.match(r"(.+?[.:;!?])(?:\s|$)", paragrafo)
    frase = (m.group(1) if m else paragrafo).strip()
    return frase[:limite]


def _ultima_frase(paragrafo: str, limite: int = 260) -> str:
    frases = [f.strip() for f in re.findall(r"[^.!?]+[.!?]", paragrafo)] or [paragrafo.strip()]
    return frases[-1][:limite]


def extrair_perfil_estilo(texto: str) -> Dict[str, Any]:
    """
    Extrai um perfil de estilo OBSERVADO na amostra (sem inferências subjetivas):

    - ``abertura_relatorio``: primeira frase após o cabeçalho "I – RELATÓRIO";
    - ``fechamento_relatorio``: última frase antes de "II – FUNDAMENTAÇÃO";
    - ``abertura_dispositivo``: primeira frase após "III – DISPOSITIVO";
    - ``conectivos_frequentes``: conectivos monitorados encontrados, com contagem (desc.).

    Campos não localizados ficam vazios (nunca são inventados).
    """
    texto = texto or ""
    perfil: Dict[str, Any] = {
        "abertura_relatorio": "",
        "fechamento_relatorio": "",
        "abertura_dispositivo": "",
        "conectivos_frequentes": [],
        "total_palavras": len(re.findall(r"\w+", texto)),
    }
    m_rel = _CAB_RELATORIO.search(texto)
    m_fund = _CAB_FUNDAMENTACAO.search(texto)
    m_disp = _CAB_DISPOSITIVO.search(texto)
    if m_rel:
        fim_rel = m_fund.start() if (m_fund and m_fund.start() > m_rel.end()) else len(texto)
        paras_rel = _paragrafos(texto[m_rel.end():fim_rel])
        if paras_rel:
            perfil["abertura_relatorio"] = _primeira_frase(paras_rel[0])
            if m_fund and m_fund.start() > m_rel.end():
                perfil["fechamento_relatorio"] = _ultima_frase(paras_rel[-1])
    if m_disp:
        paras_disp = _paragrafos(texto[m_disp.end():])
        if paras_disp:
            perfil["abertura_dispositivo"] = _primeira_frase(paras_disp[0])

    contagem: Counter = Counter()
    for conectivo in CONECTIVOS_MONITORADOS:
        padrao = re.compile(r"(?<![\wÀ-ÿ])" + re.escape(conectivo.rstrip(",")) + r"(?![\wÀ-ÿ])", re.IGNORECASE)
        n = len(padrao.findall(texto))
        if n:
            contagem[conectivo] = n
    perfil["conectivos_frequentes"] = [
        {"conectivo": c, "ocorrencias": n} for c, n in sorted(contagem.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return perfil


def _agregar_perfil_observado(modelos: List[Dict[str, Any]]) -> Dict[str, Any]:
    contagem: Counter = Counter()
    aberturas: Counter = Counter()
    fechos: Counter = Counter()
    disp: Counter = Counter()
    amostras = 0
    for mod in modelos:
        perfil = mod.get("perfil_extraido") if isinstance(mod, dict) else None
        if not isinstance(perfil, dict):
            continue
        amostras += 1
        for item in perfil.get("conectivos_frequentes") or []:
            contagem[item.get("conectivo", "")] += int(item.get("ocorrencias", 0) or 0)
        if perfil.get("abertura_relatorio"):
            aberturas[perfil["abertura_relatorio"]] += 1
        if perfil.get("fechamento_relatorio"):
            fechos[perfil["fechamento_relatorio"]] += 1
        if perfil.get("abertura_dispositivo"):
            disp[perfil["abertura_dispositivo"]] += 1
    return {
        "amostras_analisadas": amostras,
        "conectivos_frequentes": [
            {"conectivo": c, "ocorrencias": n}
            for c, n in sorted(contagem.items(), key=lambda kv: (-kv[1], kv[0]))
            if c
        ],
        "aberturas_relatorio": [a for a, _ in aberturas.most_common(3)],
        "fechamentos_relatorio": [f for f, _ in fechos.most_common(3)],
        "aberturas_dispositivo": [d for d, _ in disp.most_common(3)],
    }


def _proximo_id_modelo(modelos: List[Dict[str, Any]]) -> str:
    maior = 0
    existentes = set()
    for mod in modelos:
        ident = str(mod.get("id", "")) if isinstance(mod, dict) else ""
        existentes.add(ident)
        m = re.fullmatch(r"MOD-(\d+)", ident)
        if m:
            maior = max(maior, int(m.group(1)))
    proximo = maior + 1
    while f"MOD-{proximo:02d}" in existentes:
        proximo += 1
    return f"MOD-{proximo:02d}"


def register_cabinet_sentence_sample(titulo: str, tipo: str, texto_amostra: str) -> Dict[str, Any]:
    """
    Cadastra uma sentença-amostra do gabinete: guarda o texto COMPLETO em
    ``amostras/<MOD-NN>.txt`` (com sha256), extrai o perfil observado e atualiza o perfil
    agregado. Retorna o perfil de estilo atualizado.
    """
    texto = texto_amostra or ""
    if not texto.strip():
        raise ValueError("A amostra de sentença não pode ser vazia.")
    with _LOCK:
        profile = get_style_profile()
        modelos: List[Dict[str, Any]] = [m for m in profile.get("modelos_cadastrados", []) if isinstance(m, dict)]
        novo_id = _proximo_id_modelo(modelos)
        arquivo = AMOSTRAS_DIR / f"{novo_id}.txt"
        _escrever_atomico(arquivo, texto)
        novo = {
            "id": novo_id,
            "titulo": (titulo or "").strip() or f"Amostra {novo_id}",
            "tipo": (tipo or "").strip() or "NAO_INFORMADO",
            "resumo": texto[:220].strip() + ("..." if len(texto) > 220 else ""),
            "arquivo_amostra": f"amostras/{arquivo.name}",
            "sha256": hashlib.sha256(texto.encode("utf-8")).hexdigest(),
            "caracteres": len(texto),
            "perfil_extraido": extrair_perfil_estilo(texto),
        }
        modelos.append(novo)
        profile["modelos_cadastrados"] = modelos
        profile["perfil_observado"] = _agregar_perfil_observado(modelos)
        _salvar_perfil(profile)
        return profile


def ler_amostra_completa(modelo_id: str) -> Optional[str]:
    """Texto integral de uma amostra cadastrada (``None`` se não houver arquivo)."""
    if not re.fullmatch(r"MOD-\d+", str(modelo_id or "")):
        return None
    arquivo = AMOSTRAS_DIR / f"{modelo_id}.txt"
    try:
        return arquivo.read_text(encoding="utf-8")
    except OSError:
        return None
