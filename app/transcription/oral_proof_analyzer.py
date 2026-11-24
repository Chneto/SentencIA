"""Matriz de corroboração da prova oral (Resolução CNJ nº 492/2023).

Princípios (v2):
- transcrições com ``status == "FALHOU"`` ou sem segmentos com texto são IGNORADAS (listadas em
  ``midias_sem_transcricao``) — nenhum discurso é inventado;
- a síntese em discurso indireto aponta apenas os TEMAS efetivamente presentes na transcrição, com
  âncora (mídia + intervalo), em verbos neutros ("abordou", "referiu-se a") — sem acrescentar detalhes
  fáticos que não estejam no áudio e sem converter negativas em confissões;
- coerência delegacia × juízo e corroboração são juízos do magistrado: para casos reais ficam
  ``PENDENTE_AVALIACAO_JUIZO``, com a indicação dos documentos disponíveis para o cotejo;
- o caso demonstrativo só é tratado como tal com ``is_demo=True`` explícito.
"""

import logging
import re
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from app.core.text_utils import normalizar
from app.ingestion.laya_classifier import CATEGORIAS_SEM_FONTE_DE_FATOS, PecaClassificada

logger = logging.getLogger(__name__)

PapelLiteral = Literal[
    "OFENDIDA",
    "TESTEMUNHA_POLICIAL",
    "TESTEMUNHA_ACUSACAO",
    "TESTEMUNHA_DEFESA",
    "INTERROGATORIO_REU",
    "ALEGACOES_FINAIS_MP",
    "ALEGACOES_FINAIS_DEFESA",
    "INDETERMINADO",
]
CoerenciaLiteral = Literal[
    "ALTA_COERENCIA",
    "CONVERGENTE_PARCIAL",
    "RETRATACAO_CICLO_VIOLENCIA",
    "NAO_OUVIDO_DELEGACIA",
    "PENDENTE_AVALIACAO_JUIZO",
]
PAPEIS_VALIDOS = (
    "OFENDIDA",
    "TESTEMUNHA_POLICIAL",
    "TESTEMUNHA_ACUSACAO",
    "TESTEMUNHA_DEFESA",
    "INTERROGATORIO_REU",
    "ALEGACOES_FINAIS_MP",
    "ALEGACOES_FINAIS_DEFESA",
    "INDETERMINADO",
)
PAPEIS_CONCRETOS = tuple(p for p in PAPEIS_VALIDOS if p != "INDETERMINADO")
ESTADOS_PAPEL_SEGMENTO = ("INDETERMINADO", "SUGERIDO", "CONFIRMADO")
_SINONIMOS_PAPEL = {
    "VITIMA": "OFENDIDA",
    "ACUSADO": "INTERROGATORIO_REU",
    "REU": "INTERROGATORIO_REU",
    "INTERROGATORIO": "INTERROGATORIO_REU",
    "TESTEMUNHA": "TESTEMUNHA_ACUSACAO",
    "POLICIAL": "TESTEMUNHA_POLICIAL",
    "MP": "ALEGACOES_FINAIS_MP",
    "DEFESA": "ALEGACOES_FINAIS_DEFESA",
}


class DepoenteAnaliseOral(BaseModel):
    media_id: str
    arquivo: str
    depoente: str
    papel: PapelLiteral
    papel_confirmado: bool = False
    duracao: str
    segmentos_literais: List[Dict[str, Any]]
    segmentos_pendentes_confirmacao: int = 0
    discurso_indireto_sentenca: str
    relato_fase_policial: str
    coerencia_delegacia_juizo: CoerenciaLiteral
    corroboracao_pericial_documental: str


class MatrizCorroboracaoOral(BaseModel):
    perspectiva_genero_aplicada: bool
    protocolo_cnj: str
    depoentes: List[DepoenteAnaliseOral]
    fundamentacao_sintese: str
    # Mídias cuja transcrição falhou/está vazia: não entram na matriz (nada é inventado).
    midias_sem_transcricao: List[Dict[str, str]] = Field(default_factory=list)
    segmentos_pendentes_confirmacao: int = 0


_FILLER_ASR_RE = re.compile(
    r"\b(?:tá bom|tá|né|eh|ahn|uhum|tipo assim|entendeu|quer dizer|peraí|só um pouquinho)\b",
    flags=re.IGNORECASE,
)


_PROCEDURAL_NOISE_RE = re.compile(
    r"(?:(?:este|esse)\s+[ée]\s+o\s+processo\s+(?:de\s+)?n[úu]mero\s+[\d\s\-digito]+|"
    r"ouviremos\s+agora\s+(?:uma?\s+)?testemunha[^\.,;]*|"
    r"sobre\s+pena\s+de\s+cometer\s+o\s+crime\s+de\s+falso\s+testemunho|"
    r"direito\s+constitucional\s+(?:de|a)\s+ficar\s+calado|"
    r"obrigad[oa]\s+pela\s+sua\s+presen[çc]a|"
    r"satisfeit[oa]\s+excel[êe]ncia|"
    r"n[ãa]o\s+tenho\s+(?:mais\s+)?perguntas|"
    r"iniciada\s+doutor[^\.,;]*)",
    flags=re.IGNORECASE,
)


def _higienizar_segmento_asr(txt: str) -> str:
    """Limpa vícios de transcrição automática (ASR) e ruído procedimental preservando o núcleo fático."""
    limpo = re.sub(r"\s+", " ", (txt or "")).strip()
    if not limpo:
        return ""
    limpo = _PROCEDURAL_NOISE_RE.sub(" ", limpo)
    limpo = _FILLER_ASR_RE.sub("", limpo)
    limpo = re.sub(r"\s{2,}", " ", limpo).strip(" ,;-")
    return limpo


# ----------------------------------------------------------------------------------------------
# Utilidades de transcrição
# ----------------------------------------------------------------------------------------------
def _texto_segmento(seg: Any) -> str:
    if not isinstance(seg, dict):
        return ""
    return str(seg.get("texto_literal") or seg.get("texto") or "").strip()


def transcricao_utilizavel(tr: Any) -> bool:
    """Verdadeiro se a transcrição não falhou e tem ao menos um segmento com texto."""
    if not isinstance(tr, dict):
        return False
    if str(tr.get("status", "")).upper() == "FALHOU":
        return False
    return any(_texto_segmento(s) for s in (tr.get("segmentos") or []))


def _normalizar_papel(valor: Any) -> str:
    papel = str(valor or "").strip().upper()
    if papel in PAPEIS_VALIDOS:
        return papel
    if papel in _SINONIMOS_PAPEL:
        return _SINONIMOS_PAPEL[papel]
    if papel:
        logger.warning("Papel processual desconhecido '%s'; tratado como INDETERMINADO.", papel)
    return "INDETERMINADO"


def _normalizar_segmento_papel(seg: Dict[str, Any], papel_midia: str) -> Dict[str, Any]:
    """Garante ``papel_segmento``, ``papel_estado`` e ``papel_confirmado`` explícitos no segmento."""
    out = dict(seg)
    papel_seg_bruto = out.get("papel_segmento") or out.get("papel")
    papel_seg = _normalizar_papel(papel_seg_bruto) if papel_seg_bruto is not None else papel_midia
    estado_bruto = str(out.get("papel_estado") or "").strip().upper()
    confirmado = bool(out.get("papel_confirmado")) or estado_bruto == "CONFIRMADO"
    if papel_seg == "INDETERMINADO":
        estado = "INDETERMINADO"
        confirmado = False
    elif confirmado:
        estado = "CONFIRMADO"
    elif estado_bruto == "SUGERIDO" or papel_seg_bruto is None:
        estado = "SUGERIDO"
    else:
        estado = "SUGERIDO"
    out["papel_segmento"] = papel_seg
    out["papel_estado"] = estado
    out["papel_confirmado"] = confirmado
    return out


def confirmar_papel_segmento(
    tr: Dict[str, Any],
    indice_segmento: int,
    papel: str,
    *,
    confirmar: bool = True,
) -> Dict[str, Any]:
    """Atribui e opcionalmente confirma o papel processual de um segmento específico de áudio."""
    if not isinstance(tr, dict):
        raise ValueError("Transcrição inválida.")
    segmentos = tr.get("segmentos")
    if not isinstance(segmentos, list) or not (0 <= indice_segmento < len(segmentos)):
        raise IndexError(f"Índice de segmento fora dos limites: {indice_segmento}")
    papel_norm = _normalizar_papel(papel)
    if confirmar and papel_norm not in PAPEIS_CONCRETOS:
        raise ValueError(
            f"Confirmação humana exige papel processual concreto ({', '.join(PAPEIS_CONCRETOS)}), recebido: {papel!r}"
        )
    if not confirmar and papel_norm not in PAPEIS_VALIDOS:
        raise ValueError(f"Papel inválido: {papel!r}")
    seg = dict(segmentos[indice_segmento])
    seg["papel_segmento"] = papel_norm
    seg["papel_confirmado"] = bool(confirmar)
    seg["papel_estado"] = "CONFIRMADO" if confirmar else ("INDETERMINADO" if papel_norm == "INDETERMINADO" else "SUGERIDO")
    segmentos[indice_segmento] = seg
    return seg


def _fmt_ref(media_id: str, ts: str) -> str:
    return f"{media_id}, {ts}" if ts else media_id


# ----------------------------------------------------------------------------------------------
# Temas por papel (palavras-chave normalizadas, sem acento) -> frase NEUTRA
# ----------------------------------------------------------------------------------------------
_TEMAS: Dict[str, List[Tuple[Tuple[str, ...], str]]] = {
    "OFENDIDA": [
        (("agred", "agress", "tapa", "soco", "chute", "empurr", "bateu", "bater", "machuc", "lesao", "enforc", "esgan"),
         "abordou a agressão física imputada"),
        (("ameac", "matar", "acabar com", "medo"), "referiu-se a ameaças e ao temor experimentado"),
        (("medida protetiva", "protetiva"), "mencionou a medida protetiva de urgência"),
        (("invadiu", "pulou", "muro", "portao", "arromb", "entrou na casa", "entrou em casa"),
         "referiu-se ao ingresso do acusado na residência"),
        (("persegu", "mensag", "whatsapp", "ligac", "ligava", "telefon", "instagram", "rede social", "redes sociais"),
         "descreveu contatos e mensagens atribuídos ao acusado"),
        (("policia", "viatura", "delegacia", "190"), "mencionou o acionamento da polícia ou o registro da ocorrência"),
        (("nao quero mais", "nao tenho mais interesse", "perdoei", "reatamos", "voltamos a"),
         "fez referência a retratação, desinteresse no prosseguimento ou reaproximação com o acusado"),
    ],
    "TESTEMUNHA_POLICIAL": [
        (("ocorrencia", "acionad", "chamad", "copom", "190", "atend"), "relatou o atendimento da ocorrência"),
        (("chorando", "abalada", "nervosa", "hematoma", "sangue", "machucad", "vermelh", "lesao", "marca"),
         "descreveu o estado da ofendida e as lesões aparentes que afirma ter constatado"),
        (("ameac",), "mencionou ameaças"),
        (("conduz", "delegacia", "flagrante", "prend", "deteve", "detid", "algem"),
         "referiu-se à abordagem, condução ou prisão do acusado"),
        (("nao presenciei", "nao vi", "nao lembro", "nao me lembro", "nao recordo"),
         "ressalvou não ter presenciado ou não se recordar de parte dos fatos"),
    ],
    "INTERROGATORIO_REU": [
        (("vou ficar calado", "prefiro ficar calado", "vou permanecer em silencio", "prefiro permanecer em silencio",
          "nao vou responder", "nao quero responder"),
         "exerceu, total ou parcialmente, o direito ao silêncio (art. 5º, LXIII, da CF e art. 186 do CPP)"),
        (("eu nego", "nego que", "nego ter", "nao fiz", "nao agredi", "nao bati", "nao ameacei", "mentira",
          "nunca agredi", "nunca ameacei", "nunca bati"),
         "negou, ao menos em parte, a imputação"),
        (("confesso", "admito", "eu bati", "eu agredi", "dei um tapa", "dei um soco", "dei um empurr"),
         "admitiu parte dos fatos (o alcance da admissão deve ser aferido pelo Juízo na transcrição)"),
        (("discuss", "briga", "brigamos", "confusao"), "referiu-se a discussão ou desentendimento com a ofendida"),
        (("medida protetiva", "protetiva", "oficial de justica", "intimad"),
         "manifestou-se sobre a medida protetiva e a ciência da ordem judicial"),
        (("muro", "portao", "fui ate", "fui la", "entrei"), "manifestou-se sobre sua ida ao local dos fatos"),
    ],
    "ALEGACOES_FINAIS_MP": [
        (("condena", "procedencia", "procedente"), "pugnou pela condenação do acusado"),
        (("absolvi", "improcedencia"), "manifestou-se pela absolvição quanto a parte ou à totalidade das imputações"),
        (("materialidade", "autoria", "palavra da vitima", "laudo"),
         "sustentou a materialidade e a autoria com base na prova produzida"),
        (("dano moral", "danos morais", "indeniza", "reparacao"),
         "requereu a fixação de valor mínimo para reparação dos danos (art. 387, IV, do CPP)"),
    ],
    "_TESTEMUNHA": [
        (("nao presenciei", "nao vi", "nao estava", "nao sei dizer", "nao lembro", "nao recordo"),
         "afirmou não ter presenciado ou não se recordar dos fatos"),
        (("agred", "agress", "bateu", "soco", "tapa", "ameac", "briga", "discuss"),
         "referiu-se aos fatos imputados"),
        (("trabalhador", "boa pessoa", "conheco", "honesto", "tranquilo", "conduta"),
         "prestou informações sobre a conduta social do acusado"),
    ],
}

_RE_NEGACAO_ANTES = re.compile(r"\b(?:nao|nunca|jamais)\s+(?:\w+\s+){0,2}$")


def _contem_keyword(texto_norm: str, keyword: str, permitir_negacao: bool) -> bool:
    """Casa ``keyword`` no início de palavra; para temas de admissão, descarta casamentos negados."""
    inicio = 0
    while True:
        pos = texto_norm.find(keyword, inicio)
        if pos < 0:
            return False
        inicio = pos + len(keyword)
        if pos > 0 and texto_norm[pos - 1].isalnum():
            continue
        if permitir_negacao or keyword.startswith("nao ") or not _RE_NEGACAO_ANTES.search(texto_norm[max(0, pos - 30):pos]):
            return True


def _identificar_temas(segs_ancorados: Sequence[Tuple[str, Dict[str, Any]]], papel: str) -> List[str]:
    """Frases neutras dos temas presentes, cada uma com a âncora (mídia, intervalo) do 1º trecho."""
    temas = _TEMAS.get(papel) or _TEMAS["_TESTEMUNHA"]
    normalizados = [
        (mid, str(seg.get("timestamp", "")).strip(), normalizar(_higienizar_segmento_asr(_texto_segmento(seg))))
        for mid, seg in segs_ancorados
    ]
    pontos: List[str] = []
    for keywords, frase in temas:
        # Para temas "positivos" de admissão, uma negação próxima descarta o casamento.
        permitir_negacao = not frase.startswith("admitiu")
        for mid, ts, txt in normalizados:
            if txt and any(_contem_keyword(txt, kw, permitir_negacao) for kw in keywords):
                pontos.append(f"{frase} ({_fmt_ref(mid, ts)})")
                break
    return pontos


def _intervalo(segs: Sequence[Dict[str, Any]]) -> str:
    ts = [str(s.get("timestamp", "")).strip() for s in segs if str(s.get("timestamp", "")).strip()]
    if not ts:
        return ""
    return f"{ts[0].split('-')[0].strip()} a {ts[-1].split('-')[-1].strip()}"


def _sintetizar_nucleo_forense_oral(
    segs: List[Dict[str, str]],
    papel: str,
    nome: str,
    media_ids: List[str],
    duracao_total: str,
    id_ata: str,
    id_den: str,
    id_bo: str,
    laudos_str: str = "",
    segs_ancorados: Optional[Sequence[Tuple[str, Dict[str, Any]]]] = None,
) -> str:
    """
    Síntese forense em discurso indireto (3ª pessoa) a partir dos TEMAS presentes nos segmentos
    transcritos, com âncoras de mídia/intervalo. Não acrescenta fatos ausentes da transcrição.
    """
    if segs_ancorados is None:
        mid_padrao = media_ids[0] if media_ids else ""
        segs_ancorados = [(mid_padrao, s) for s in segs]
    mids_fmt = ", ".join(m for m in media_ids if m) or "mídia sem identificação"
    partes_ref = [p for p in (id_ata, f"Mídia(s) {mids_fmt}") if p]
    if duracao_total:
        partes_ref.append(f"duração {duracao_total}")
    span = _intervalo(segs)
    if span:
        partes_ref.append(f"intervalo {span}")
    ref = "; ".join(partes_ref)

    if papel == "INDETERMINADO":
        return (
            f"Registro audiovisual ({nome}, {ref}) com papel processual INDETERMINADO, "
            "aguardando confirmação humana antes de alimentar a valoração do mérito ou a dosimetria."
        )
    pontos = _identificar_temas(segs_ancorados, papel)
    nucleo = "; ".join(pontos) if pontos else "prestou declarações registradas na transcrição integral da mídia, cujo teor deve ser examinado pelo Juízo"

    if papel == "OFENDIDA":
        return f"A ofendida {nome}, ouvida em juízo sob o crivo do contraditório mediante gravação audiovisual ({ref}), {nucleo}."
    if papel == "TESTEMUNHA_POLICIAL":
        return f"A testemunha policial {nome}, ouvida sob compromisso legal ({ref}), {nucleo}."
    if papel == "ALEGACOES_FINAIS_MP":
        nucleo_mp = "; ".join(pontos) if pontos else "apresentou alegações finais orais, cujo teor consta da gravação"
        return f"Em alegações finais orais na Audiência de Instrução e Julgamento ({ref}), o Ministério Público {nucleo_mp}."
    if papel == "INTERROGATORIO_REU":
        return f"O acusado {nome}, em seu interrogatório judicial sob o crivo da ampla defesa ({ref}), {nucleo}."
    rotulo = "A testemunha arrolada pela Defesa" if papel == "TESTEMUNHA_DEFESA" else "A testemunha"
    return f"{rotulo} {nome}, ouvida em juízo ({ref}), {nucleo}."


def _extrair_refs_documentais_pecas(pecas: List[PecaClassificada]) -> Dict[str, str]:
    """IDs de BO, denúncia e ata realmente presentes (vazios quando ausentes — sem atribuir outra peça)."""
    bo_peca = next((p for p in pecas if p.categoria == "BO" and p.doc_id != "ID-CAPA-PJE"), None) or next(
        (p for p in pecas if p.categoria == "BO"), None
    )
    den_peca = next((p for p in pecas if p.categoria == "DENUNCIA"), None)
    ata_peca = next(
        (p for p in pecas if p.categoria == "TERMO_AUDIENCIA" and "instrução" in p.titulo.lower()),
        None,
    ) or next((p for p in pecas if p.categoria == "TERMO_AUDIENCIA"), None)

    laudos_encontrados: List[str] = []
    bos_encontrados: List[str] = []
    laudo_ids: List[str] = []
    for p in pecas:
        if p.categoria == "LAUDO_PERICIAL" and p.doc_id not in laudo_ids:
            laudo_ids.append(p.doc_id)
        if p.categoria in ("BO", "LAUDO_PERICIAL"):
            for m in re.findall(r"\bUN-[A-Z0-9-]+\b", p.conteudo, flags=re.IGNORECASE):
                u = m.upper()
                if u not in laudos_encontrados:
                    laudos_encontrados.append(u)
            for m in re.findall(r"Boletim\s+de\s+Ocorr[êe]ncia\s*(?:n[º°\.]?\s*)?([\d/\.-]+)", p.conteudo, flags=re.IGNORECASE):
                b = m.strip()
                if len(b) >= 4 and b not in bos_encontrados:
                    bos_encontrados.append(b)

    return {
        "id_bo": bo_peca.doc_id if bo_peca else "",
        "id_den": den_peca.doc_id if den_peca else "",
        "id_ata": ata_peca.doc_id if ata_peca else "",
        "laudos": ", ".join(laudos_encontrados[:4]),
        "laudo_ids": ", ".join(laudo_ids[:4]),
        "bos": ", ".join(bos_encontrados[:2]),
    }


def _localizar_oitiva_policial(pecas: List[PecaClassificada], nome: str, papel: str) -> Optional[PecaClassificada]:
    """Peça da fase policial que registra a oitiva (ou menção nominal) do depoente, se houver."""
    nome_n = normalizar(re.sub(r"\s*\(Parte\s+\d+\)\s*$", "", nome or "", flags=re.IGNORECASE))
    tokens = [t for t in re.split(r"[^a-z0-9]+", nome_n) if len(t) > 2]
    candidatas = [
        p for p in pecas
        if p.categoria not in CATEGORIAS_SEM_FONTE_DE_FATOS and (p.fase == "INQUISITORIAL" or p.categoria == "BO")
    ]
    for p in candidatas:
        titulo_n = normalizar(p.titulo)
        if papel == "OFENDIDA" and "termo de declaracoes" in titulo_n:
            return p
        if papel == "INTERROGATORIO_REU" and "interrogatorio" in titulo_n:
            return p
        if "termo de depoimento" in titulo_n and len(tokens) >= 2:
            alvo = normalizar(p.titulo + " " + p.conteudo[:800])
            if all(t in alvo for t in tokens[-2:]):
                return p
    if len(tokens) >= 2:
        alvo_nome = " ".join(tokens)
        for p in candidatas:
            if alvo_nome in normalizar(p.conteudo):
                return p
    return None


def _build_real_case_oral_analysis(
    tr: Dict[str, Any],
    pecas: List[PecaClassificada],
) -> DepoenteAnaliseOral:
    """
    Análise oral em 3ª pessoa construída exclusivamente a partir dos segmentos realmente transcritos e
    das peças indexadas. Coerência/corroboração ficam pendentes de avaliação judicial.
    """
    m_id = str(tr.get("media_id") or "")
    nome = str(tr.get("depoente") or "Depoente")
    papel = _normalizar_papel(tr.get("papel"))
    papel_confirmado_midia = bool(tr.get("papel_confirmado"))
    segs_brutos = [s for s in (tr.get("segmentos") or []) if _texto_segmento(s)]
    segs: List[Dict[str, Any]] = [_normalizar_segmento_papel(s, papel) for s in segs_brutos]
    pendentes_seg = sum(1 for s in segs if s.get("papel_estado") != "CONFIRMADO")
    segs_para_sintese = [s for s in segs if s.get("papel_segmento") != "INDETERMINADO"]
    dur = str(tr.get("duracao") or "")
    arquivo = str(tr.get("arquivo") or "")

    refs = _extrair_refs_documentais_pecas(pecas)
    nome_base = re.sub(r"\s*\(Parte\s+\d+\)\s*$", "", nome, flags=re.IGNORECASE).strip()
    if not segs_para_sintese:
        indireto = (
            f"Registro audiovisual ({nome_base or nome}, Mídia {m_id or 'sem ID'}) com todos os trechos "
            "em papel INDETERMINADO, aguardando confirmação humana antes de alimentar a valoração do mérito ou a dosimetria."
        )
    else:
        indireto = _sintetizar_nucleo_forense_oral(
            segs=segs_para_sintese,
            papel=papel,
            nome=nome_base,
            media_ids=[m_id] if m_id else [],
            duracao_total=dur,
            id_ata=refs["id_ata"],
            id_den=refs["id_den"],
            id_bo=refs["id_bo"],
            laudos_str=refs["laudos"],
        )

    if papel == "ALEGACOES_FINAIS_MP":
        relato_policial = "Não se aplica (manifestação final do Ministério Público)."
    else:
        oitiva = _localizar_oitiva_policial(pecas, nome_base, papel)
        relato_policial = (
            f"Há registro do depoente na fase policial ({oitiva.doc_id}); o cotejo com o relato judicial cabe ao Juízo."
            if oitiva
            else "Não localizado termo de oitiva policial deste depoente nas peças indexadas."
        )

    elementos = []
    if refs["id_bo"]:
        elementos.append(f"registro policial ({refs['id_bo']})")
    if refs["laudo_ids"] or refs["laudos"]:
        elementos.append(f"laudo(s) pericial(is) ({refs['laudos'] or refs['laudo_ids']})")
    if refs["id_ata"]:
        elementos.append(f"termo de audiência ({refs['id_ata']})")
    corroboracao = (
        "Elementos disponíveis para o cotejo: " + ", ".join(elementos) + ". Valoração pendente pelo Juízo (Resolução CNJ nº 492/2023)."
        if elementos
        else "Nenhum registro policial, laudo ou termo de audiência indexado para cotejo."
    )

    return DepoenteAnaliseOral(
        media_id=m_id,
        arquivo=arquivo,
        depoente=nome,
        papel=papel,  # type: ignore[arg-type]
        papel_confirmado=(papel != "INDETERMINADO") and (papel_confirmado_midia or pendentes_seg == 0),
        duracao=dur,
        segmentos_literais=segs,
        segmentos_pendentes_confirmacao=pendentes_seg,
        discurso_indireto_sentenca=indireto,
        relato_fase_policial=relato_policial,
        coerencia_delegacia_juizo="PENDENTE_AVALIACAO_JUIZO",
        corroboracao_pericial_documental=corroboracao,
    )


def _somar_duracoes(duracoes: List[str]) -> str:
    total_sec = 0
    algum = False
    for d in duracoes:
        partes = str(d or "").strip().split(":")
        try:
            if len(partes) == 2:
                total_sec += int(partes[0]) * 60 + int(partes[1])
                algum = True
            elif len(partes) == 3:
                total_sec += int(partes[0]) * 3600 + int(partes[1]) * 60 + int(partes[2])
                algum = True
        except ValueError:
            continue
    if not algum:
        return ""
    mm, ss = divmod(total_sec, 60)
    return f"{mm:02d}:{ss:02d}"


def consolidar_prova_oral_por_interlocutor(
    depoentes: List[DepoenteAnaliseOral],
    pecas: List[PecaClassificada] | None = None,
) -> List[Dict[str, Any]]:
    """
    Agrupa arquivos divididos em partes (_001, _002, _003) pertencentes ao mesmo interlocutor,
    consolidando todos os segmentos em UMA única síntese forense indireta (sem repetir o cabeçalho
    por parte) e preservando todos os media_ids para ancoragem probatória.
    """
    refs = _extrair_refs_documentais_pecas(pecas or [])
    grupos: Dict[tuple[str, str], Dict[str, Any]] = {}
    ancorados: Dict[tuple[str, str], List[Tuple[str, Dict[str, Any]]]] = {}
    for d in depoentes:
        nome_base = re.sub(r"\s*\(Parte\s+\d+\)\s*$", "", d.depoente, flags=re.IGNORECASE).strip()
        chave = (d.papel, nome_base.upper())
        if chave not in grupos:
            grupos[chave] = {
                "nome_base": nome_base,
                "papel": d.papel,
                "media_ids": [],
                "duracoes": [],
                "segmentos_all": [],
                "sintese_consolidada": [],
                "relato_policial": d.relato_fase_policial,
                "coerencia": d.coerencia_delegacia_juizo,
                "corroboracao": d.corroboracao_pericial_documental,
            }
            ancorados[chave] = []
        grupos[chave]["media_ids"].append(d.media_id)
        grupos[chave]["duracoes"].append(d.duracao)
        segs_validos = [s for s in (d.segmentos_literais or []) if _texto_segmento(s)]
        grupos[chave]["segmentos_all"].extend(segs_validos)
        ancorados[chave].extend((d.media_id, s) for s in segs_validos)

    out: List[Dict[str, Any]] = []
    for chave, g in grupos.items():
        dur_total = _somar_duracoes(g["duracoes"])
        if g["segmentos_all"]:
            texto_unico = _sintetizar_nucleo_forense_oral(
                segs=g["segmentos_all"],
                papel=g["papel"],
                nome=g["nome_base"],
                media_ids=g["media_ids"],
                duracao_total=dur_total,
                id_ata=refs["id_ata"],
                id_den=refs["id_den"],
                id_bo=refs["id_bo"],
                laudos_str=refs.get("laudos", ""),
                segs_ancorados=ancorados[chave],
            )
            g["sintese_consolidada"] = [texto_unico]
        else:
            g["sintese_consolidada"] = [d.discurso_indireto_sentenca for d in depoentes if d.media_id in g["media_ids"]][:1]
        g["duracao_total"] = dur_total
        g.pop("segmentos_all", None)
        out.append(g)
    return out


def _analise_demo(tr: Dict[str, Any], m_id: str, pecas: List[PecaClassificada]) -> DepoenteAnaliseOral:
    """Textos fixos do caso DEMONSTRATIVO (usados apenas com ``is_demo=True``)."""
    bo_peca = next((p for p in pecas if p.categoria == "BO"), None)
    laudo_peca = next((p for p in pecas if p.categoria == "LAUDO_PERICIAL"), None)
    int_peca = next((p for p in pecas if p.categoria == "CERTIDAO_INTIMACAO_MPU"), None)
    id_bo = bo_peca.doc_id if bo_peca else "ID-4100101"
    id_laudo = laudo_peca.doc_id if laudo_peca else "ID-4100108"
    id_int = int_peca.doc_id if int_peca else "ID-4100118"

    papel = _normalizar_papel(tr.get("papel", "TESTEMUNHA_ACUSACAO"))
    nome = tr.get("depoente", "")
    segs = tr.get("segmentos", [])

    if papel == "OFENDIDA":
        indireto = (
            f"A ofendida {nome}, ouvida em juízo sob o crivo do contraditório e mediante sistema audiovisual "
            f"({m_id}, trechos de 00min18s a 05min10s), narrou de forma firme, segura e harmônica que já possuía "
            f"medida protetiva de urgência deferida contra o ex-companheiro Carlos Eduardo Rocha Mendes, mas que, "
            f"em 14/02/2025, por volta das 21h30min, o acusado invadiu o quintal de sua residência, na presença da "
            f"filha comum de 5 (cinco) anos de idade ({m_id}, aos 01min46s). Relatou que o réu exigiu a retirada "
            f"da medida protetiva, segurou-a com força pelo braço direito e desferiu-lhe um soco no lado esquerdo "
            f"do rosto ({m_id}, aos 02min30s), além de ameaçá-la de morte ao afirmar que voltaria para 'acabar com "
            f"a sua vida' caso não retirasse a queixa e não reatasse o relacionamento ({m_id}, aos 03min31s), "
            f"causando-lhe intenso temor e abalo psicológico."
        )
        relato_policial = (
            f"Na fase inquisitorial ({id_bo}, fls. 02/05), prestou declarações com idêntico teor fático logo após "
            f"a prisão em flagrante, descrevendo a invasão do imóvel, o soco na região malar esquerda, o agarramento "
            f"pelos braços e a ameaça de morte."
        )
        coerencia: CoerenciaLiteral = "ALTA_COERENCIA"
        corroboracao = (
            f"Plenamente corroborado pelo Laudo de Exame de Corpo de Delito ({id_laudo}, fls. 12/14 — que atestou "
            f"equimose violácea malar/zigomática esquerda e escoriações no antebraço direito), pela Certidão de "
            f"Intimação Pessoal da Medida Protetiva ({id_int}, fls. 22/23) e pelo depoimento judicial do policial "
            f"militar Cb. PM Roberto Silva (ID-4100302)."
        )
    elif papel == "TESTEMUNHA_POLICIAL":
        indireto = (
            f"A testemunha policial {nome}, ouvida em juízo ({m_id}, trechos de 00min25s a 03min55s), declarou "
            f"que foi acionada via COPOM para atender ocorrência de violência doméstica com violação de medida "
            f"protetiva. Afirmou que, ao chegar ao imóvel, encontrou a vítima Mariana visivelmente abalada, chorando "
            f"ao lado da filha menor, apresentando edema e vermelhidão na face esquerda, bem como marcas no antebraço "
            f"({m_id}, aos 01min10s). Acrescentou que a ofendida exibiu a decisão da medida protetiva vigente e "
            f"relatou imediatamente as agressões e a ameaça de morte, tendo a guarnição localizado e detido o "
            f"acusado a cerca de duzentos metros da residência ({m_id}, aos 02min11s)."
        )
        relato_policial = f"Confirmou integralmente o histórico constante no Auto de Prisão em Flagrante ({id_bo})."
        coerencia = "ALTA_COERENCIA"
        corroboracao = f"Harmônico com as declarações da ofendida e com o Laudo Pericial do IML ({id_laudo})."
    elif papel == "TESTEMUNHA_DEFESA":
        indireto = (
            f"A testemunha arrolada pela Defesa, {nome}, ouvida em juízo ({m_id}, aos 00min30s), declarou ser "
            f"colega de trabalho do réu há três anos e afirmou expressamente não ter presenciado os fatos ocorridos "
            f"em 14/02/2025, limitando-se a tecer considerações abonatórias sobre a conduta profissional do acusado."
        )
        relato_policial = "Não ouvida na fase inquisitorial (testemunha meramente abonatória)."
        coerencia = "NAO_OUVIDO_DELEGACIA"
        corroboracao = "Depoimento sem conhecimento direto dos fatos imputados na denúncia."
    else:
        indireto = (
            f"O acusado {nome}, em seu interrogatório judicial ({m_id}, trechos de 00min40s a 04min50s), admitiu "
            f"expressamente que tinha plena ciência da decisão concessiva de medidas protetivas de urgência, "
            f"confirmando ter sido intimado pessoalmente pelo Oficial de Justiça em janeiro de 2025 ({m_id}, aos "
            f"00min40s), bem como admitiu ter ido até a residência da ofendida no dia 14/02/2025 e tê-la segurado "
            f"pelo braço durante discussão ({m_id}, aos 02min16s), negando, contudo, ter desferido o soco na face "
            f"ou proferido ameaça de morte."
        )
        relato_policial = f"Manteve versão exculpatória parcial semelhante à apresentada por ocasião do flagrante ({id_bo})."
        coerencia = "CONVERGENTE_PARCIAL"
        corroboracao = (
            f"A confissão quanto à ciência da medida protetiva e ida ao imóvel confirma o crime do art. 24-A da Lei "
            f"11.340/2006; a negativa quanto ao soco no rosto restou isolada e infirmada pelo Laudo Pericial ({id_laudo})."
        )

    return DepoenteAnaliseOral(
        media_id=m_id,
        arquivo=tr.get("arquivo", ""),
        depoente=nome,
        papel=papel,  # type: ignore[arg-type]
        duracao=tr.get("duracao", "") or "",
        segmentos_literais=segs,
        discurso_indireto_sentenca=indireto,
        relato_fase_policial=relato_policial,
        coerencia_delegacia_juizo=coerencia,
        corroboracao_pericial_documental=corroboracao,
    )


_SINTESE_DEMO = (
    "Em estrita observância ao Protocolo para Julgamento com Perspectiva de Gênero do Conselho Nacional de Justiça "
    "(Resolução CNJ nº 492/2023) e à jurisprudência consolidada do Superior Tribunal de Justiça, nos crimes praticados "
    "no âmbito doméstico e familiar — habitualmente perpetrados na clandestinidade, longe de olhares de terceiros —, "
    "a palavra da mulher vítima reveste-se de especial relevância probatória, mormente quando coesa entre a fase "
    "policial e a judicial e corroborada por elementos objetivos independentes (Laudo de Exame de Corpo de Delito e "
    "depoimento do policial militar que atendeu à ocorrência)."
)
_SINTESE_REAL = (
    "Em estrita observância ao Protocolo para Julgamento com Perspectiva de Gênero do Conselho Nacional de Justiça "
    "(Resolução CNJ nº 492/2023) e à jurisprudência consolidada do Superior Tribunal de Justiça, a palavra da mulher "
    "em situação de violência doméstica e familiar possui especial relevância probatória nos delitos praticados na "
    "clandestinidade, devendo ser valorada sob a ótica assimétrica de gênero e em confronto harmônico com o contexto "
    "probatório judicial e os elementos informativos colhidos na investigação preliminar (art. 155 do CPP)."
)


def gerar_matriz_corroboracao_oral(
    pecas: List[PecaClassificada],
    transcriptions: List[Dict[str, Any]],
    is_demo: bool = False,
) -> MatrizCorroboracaoOral:
    """
    Converte as transcrições de audiência em discurso indireto (com citação de mídia e intervalo) e
    constrói a Matriz de Corroboração (Fase Policial × Fase Judicial × Acervo Documental/Pericial), sob a
    Resolução CNJ nº 492/2023. Transcrições falhas ou vazias são ignoradas e listadas em
    ``midias_sem_transcricao``. ``is_demo=True`` (explícito) habilita os textos do caso demonstrativo.
    """
    depoentes_out: List[DepoenteAnaliseOral] = []
    ignoradas: List[Dict[str, str]] = []
    seen_media_ids: set[str] = set()

    for idx_tr, tr in enumerate(transcriptions or []):
        if not isinstance(tr, dict):
            continue
        if not transcricao_utilizavel(tr):
            ignoradas.append(
                {
                    "media_id": str(tr.get("media_id") or ""),
                    "arquivo": str(tr.get("arquivo") or ""),
                    "motivo": str(tr.get("erro") or "transcrição sem segmentos com texto"),
                }
            )
            continue

        m_id = str(tr.get("media_id") or f"ID-MIDIA-SEM-ID-{idx_tr + 1:02d}")
        if m_id in seen_media_ids:
            papel_tr = _normalizar_papel(tr.get("papel"))
            suffix_num = m_id.split("-")[-1]
            if papel_tr == "INTERROGATORIO_REU":
                m_id = f"ID-MIDIA-REU-{suffix_num}"
            elif papel_tr == "ALEGACOES_FINAIS_MP":
                m_id = f"ID-MIDIA-MP-{suffix_num}"
            else:
                m_id = f"{m_id}-{idx_tr + 1:02d}"
        seen_media_ids.add(m_id)
        tr = {**tr, "media_id": m_id}

        # Textos fixos apenas para as mídias do próprio caso demonstrativo (flag explícita).
        if is_demo and m_id.startswith("ID-4100"):
            depoentes_out.append(_analise_demo(tr, m_id, pecas))
        else:
            depoentes_out.append(_build_real_case_oral_analysis(tr, pecas))

    if ignoradas:
        logger.warning(
            "%d mídia(s) sem transcrição utilizável foram excluídas da matriz oral: %s",
            len(ignoradas),
            ", ".join(i["arquivo"] or i["media_id"] for i in ignoradas),
        )

    return MatrizCorroboracaoOral(
        perspectiva_genero_aplicada=True,
        protocolo_cnj="Resolução CNJ nº 492/2023 (Protocolo para Julgamento com Perspectiva de Gênero)",
        depoentes=depoentes_out,
        fundamentacao_sintese=_SINTESE_DEMO if is_demo else _SINTESE_REAL,
        midias_sem_transcricao=ignoradas,
        segmentos_pendentes_confirmacao=sum(d.segmentos_pendentes_confirmacao for d in depoentes_out),
    )
