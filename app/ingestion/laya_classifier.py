"""Laya System 1: classificação determinística das peças e extração de metadados do processo.

Princípios (v2):
- o caso demonstrativo só é tratado como demo com ``is_demo=True`` explícito (nunca pelo prefixo
  ``ID-4100`` — IDs reais do PJe podem começar com 4100);
- dados não extraídos ficam VAZIOS (``""``/``[]``/``0.0``) com alerta em
  ``MetadadosProcesso.alertas_extracao`` — nada de datas, crimes, números de processo ou valores fictícios.
"""

import datetime
import re
from typing import Any, Dict, List, Literal, Optional, Tuple
from pydantic import BaseModel, Field

from app.core.text_utils import normalizar
from app.ingestion.imputacoes import EventoImputacao, ImputacaoExtraida, extrair_imputacoes, localizar_tipos_penais


class PecaClassificada(BaseModel):
    doc_id: str
    folha: str
    titulo: str
    data_peca: str
    fase: str
    categoria: str
    laya_confidence: float
    laya_scores: Dict[str, float] = Field(default_factory=dict)
    ocr_aplicado: bool = False
    resumo_estruturado: str
    entidades_extraidas: List[Dict[str, str]] = Field(default_factory=list)
    conteudo: str


class PedidoParteItem(BaseModel):
    tipo: str
    descricao: str
    crime: str = ""
    fonte_id: str
    folha: str = ""
    trecho: str
    origem: str = "memoriais"  # "memoriais" | "oral"
    valor: Optional[float] = None


class PontoDecisivoItem(BaseModel):
    chave: str
    valor: Any = None
    estado: Literal["extraido", "a_confirmar", "ausente"] = "ausente"
    fonte_id: str = ""
    folha: str = ""
    trecho: str = ""
    efeito: str = ""
    confirmado: bool = False


class TeseDefensivaItem(BaseModel):
    codigo: str
    tipo: str  # PRELIMINAR ou MERITO ou SUBSIDIARIA
    titulo: str
    descricao_defesa: str
    obrigatorio_enfrentamento_art381: bool = True
    fonte_id: str = ""
    folha: str = ""
    trecho: str = ""
    origem: str = "memoriais"


class MetadadosProcesso(BaseModel):
    numero_processo: str
    autos_mpu_apenso: str = "Sem apenso registrado"
    nome_ofendida: str
    nome_reu: str
    # Vazio = vínculo (e inciso do art. 5º da LMP) não extraído dos autos; nunca se presume o inciso III.
    vinculo_domestico: str = ""
    data_fato: str
    data_recebimento_denuncia: str
    data_intimacao_mpu: str
    intimidado_pessoalmente_mpu: bool
    reu_primario_sumula444: bool
    pedido_dano_moral_expresso_mp: bool
    valor_dano_moral_pedido_mp: float
    crimes_imputados: List[str]
    imputacoes: List[ImputacaoExtraida] = Field(default_factory=list)
    eventos_imputacao_pendentes: List[EventoImputacao] = Field(default_factory=list)
    teses_defesa: List[TeseDefensivaItem]
    pedidos_mp: List[PedidoParteItem] = Field(default_factory=list)
    pedidos_defesa: List[PedidoParteItem] = Field(default_factory=list)
    pontos_decisivos: List[PontoDecisivoItem] = Field(default_factory=list)
    pontos_decisivos_confirmados: Dict[str, Any] = Field(default_factory=dict)
    # Data de nascimento do réu (AAAA-MM-DD). Não é extraída automaticamente (risco de confundir com a
    # da ofendida); o juízo a informa via metadados_override para o art. 115 do CP (prazo prescricional
    # pela metade: menor de 21 anos na data do fato ou maior de 70 na data da sentença).
    data_nascimento_reu: str = ""
    # Avisos sobre dados não extraídos/inferidos (para o juiz complementar via metadados_override).
    alertas_extracao: List[str] = Field(default_factory=list)


REGRAS_LAYA_SYSTEM1: Dict[str, List[str]] = {
    "BO": [
        "boletim de ocorrência",
        "auto de prisão em flagrante",
        "delegacia especializada",
        "deam",
        "inquérito policial",
        "termo de declarações",
        "avaliação de risco",
    ],
    "LAUDO_PERICIAL": [
        "laudo pericial",
        "exame de corpo de delito",
        "instituto médico legal",
        "iml",
        "quesitos",
        "equimose",
    ],
    "DECISAO_MPU": [
        "decisão de medidas protetivas",
        "art. 22 da lei",
        "proibição de aproximação",
        "medidas protetivas de urgência",
    ],
    "CERTIDAO_INTIMACAO_MPU": [
        "certidão do oficial de justiça",
        "oficiala de justiça",
        "central de cumprimento de mandados",
        "intimei pessoalmente",
        "ciência inequívoca",
        "mandado",
        "diligência de citação",
    ],
    "DENUNCIA": [
        "oferece denúncia",
        "vem oferecer denúncia",
        "denúncia ministerial",
        "fato delituoso",
        "pedido expresso de reparação",
    ],
    "RECEBIMENTO_DENUNCIA": [
        "recebo a denúncia",
        "recebimento de denúncia",
        "art. 395 do cpp",
        "cite-se",
        "declaração de decadência",
    ],
    "RESPOSTA_ACUSACAO": [
        "resposta à acusação",
        "art. 396",
        "defesa preliminar",
    ],
    "TERMO_AUDIENCIA": [
        "termo de audiência",
        "ata da audiência",
        "instrução e julgamento",
        "pje mídias",
        "registro audiovisual",
        "declarada aberta a audiência",
    ],
    "CAC_FAC": [
        "certidão de antecedentes criminais",
        "certidão estadual de antecedentes",
        "folha de antecedentes",
        "sinic",
        "nada constar",
        "primário",
    ],
    "ALEGACOES_FINAIS_MP": [
        "memoriais do ministério público",
        "alegações finais por memoriais do ministério público",
        "apresentar suas alegações finais",
        "integral procedência da pretensão punitiva",
        "julgada procedente, in totum",
    ],
    "ALEGACOES_FINAIS_DEFESA": [
        "memoriais da defesa",
        "alegações finais por memoriais da defesa",
        "defensoria pública",
        "pugnando pela absolvição",
        "do pedido de absolvição",
        "in dubio pro reo",
        "inépcia da denúncia",
    ],
}


SINTESES_EXECUTIVAS_LAYA: Dict[str, str] = {
    "BO": "Síntese Laya S1: Notitia criminis e prisão em flagrante narrando invasão de domicílio, agressão facial/braquial e ameaça de morte em descumprimento de medida protetiva.",
    "LAUDO_PERICIAL": "Síntese Laya S1: Prova pericial material positiva (Quesito 1º SIM) atestando equimose malar esquerda (4,5x3,0 cm) e escoriações no antebraço direito por ação contundente.",
    "DECISAO_MPU": "Síntese Laya S1: Ordem judicial protetiva vigente (art. 22 da Lei 11.340/06) fixando afastamento mínimo de 300 metros e proibição total de contato.",
    "CERTIDAO_INTIMACAO_MPU": "Síntese Laya S1: Ciência pessoal inequívoca do agressor certificada por Oficial de Justiça antes do fato delituoso (pressuposto do art. 24-A da Lei nº 11.340/2006).",
    "DENUNCIA": "Síntese Laya S1: Imputação tríplice em concurso material (art. 24-A LMP + art. 129, § 13, CP + art. 147, § 1º, CP) com pedido expresso de dano moral (R$ 3.000,00 — Tema 983/STJ).",
    "RECEBIMENTO_DENUNCIA": "Síntese Laya S1: Marco interruptivo da prescrição (art. 117, I, CP) com juízo positivo de admissibilidade da acusação.",
    "RESPOSTA_ACUSACAO": "Síntese Laya S1: Defesa preliminar (art. 396-A CPP) reservando o mérito para após a instrução e arrolando testemunha abonatória.",
    "TERMO_AUDIENCIA": "Síntese Laya S1: Registro audiovisual PJe Mídias de 4 depoimentos (ofendida, PM, testemunha defensiva e interrogatório) sob o Protocolo CNJ 492/2023.",
    "CAC_FAC": "Síntese Laya S1: Certidão atestando primariedade técnica do acusado; registro sem trânsito em julgado bloqueado pela Súmula 444/STJ.",
    "ALEGACOES_FINAIS_MP": "Síntese Laya S1: Memoriais ministeriais pugnando pela condenação integral, exasperação pela culpabilidade diante da filha menor e reparação mínima.",
    "ALEGACOES_FINAIS_DEFESA": "Síntese Laya S1: Memoriais defensivos suscitando 1 preliminar (inépcia) e 4 teses de mérito/subsidiárias de apreciação obrigatória (art. 381, III, CPP).",
}


def _recortar_trecho(texto: str, start: int, end: int, max_len: int = 300) -> str:
    """Recorta uma janela textual limpa ao redor do match, limitada a ``max_len`` caracteres."""
    if not texto:
        return ""
    # Tenta expandir até fronteiras de frase/parágrafo dentro do limite
    ini = max(0, start - 90)
    fim = min(len(texto), end + 150)
    # Ajusta início para depois de quebra dupla ou ponto anterior, se próximo
    for sep in ("\n\n", ". ", "; ", "\n"):
        pos = texto.rfind(sep, ini, start)
        if pos != -1 and (start - (pos + len(sep))) <= 100:
            ini = pos + len(sep)
            break
    # Ajusta fim para ponto/quebra seguinte, ignorando abreviações jurídicas (art., fls., inc., etc.)
    for sep in ("\n\n", "\n"):
        pos = texto.find(sep, end, fim)
        if pos != -1:
            fim = pos
            break
    else:
        pos_busca = end
        while pos_busca < fim:
            pos = texto.find(". ", pos_busca, fim)
            if pos == -1:
                break
            antes = texto[max(0, pos - 6): pos + 1].lower()
            if re.search(r"\b(?:art|arts|fl|fls|inc|par|dr|dra|sr|sra|n|min|des|proc)\.$", antes):
                pos_busca = pos + 2
                continue
            fim = pos + 1
            break
    trecho = re.sub(r"\s+", " ", texto[ini:fim]).strip()
    if len(trecho) > max_len:
        trecho = trecho[: max_len - 3].rstrip() + "..."
    return trecho


def _detectar_crime_no_trecho(trecho: str) -> str:
    """Identifica menção a tipo penal específico no trecho do pedido (vazio se geral)."""
    t = trecho.lower()
    regras_crime = (
        (r"art\.?\s*129\s*,?\s*§\s*13", "art_129_p13_cp"),
        (r"art\.?\s*129\s*,?\s*§\s*9", "art_129_p9_cp"),
        (r"art\.?\s*24-?a", "art_24a_lmp"),
        (r"art\.?\s*147-?a|persegui[çc][aã]o|stalking", "art_147a_cp"),
        (r"art\.?\s*147-?b|viol[êe]ncia\s+psicol[óo]gica", "art_147b_cp"),
        (r"art\.?\s*147\b|amea[çc]a", "art_147_cp"),
        (r"art\.?\s*150\s*,?\s*§\s*1", "art_150_p1_cp"),
        (r"art\.?\s*150\b|viola[çc][aã]o\s+de\s+domic[íi]lio|invas[aã]o\s+de\s+domic[íi]lio", "art_150_cp"),
        (r"vias\s+de\s+fato|art\.?\s*21\s+da\s+lcp", "art_21_lcp"),
        (r"les[aã]o\s+corporal", "art_129_p13_cp"),
        (r"descumprimento\s+de\s+medida\s+protetiva", "art_24a_lmp"),
    )
    for pat, code in regras_crime:
        if re.search(pat, t, flags=re.IGNORECASE):
            return code
    return ""


def extrair_pedidos_mp_texto(
    texto: str,
    *,
    fonte_id: str,
    folha: str = "",
    origem: str = "memoriais",
) -> List[PedidoParteItem]:
    """Extrai pedidos reais formulados pelo Ministério Público com trecho (<= 300 chars) e fonte."""
    if not texto or not texto.strip():
        return []

    pedidos: List[PedidoParteItem] = []

    # 1. Pedido de absolvição pelo MP (com eventual inciso do art. 386 do CPP)
    m_abs = re.search(
        r"(?:requer(?:eu|endo)?|pugna(?:ndo)?|opina(?:ndo)?|manifesta(?:-se)?|pleiteia(?:ndo)?|postula(?:ndo)?|pede)"
        r"[^.\n;]{0,80}(?:absolvi[çc][aã]o|improced[êe]ncia\s+da\s+(?:den[úu]ncia|a[çc][aã]o\s+penal|pretens[aã]o\s+punitiva))"
        r"|absolvi[çc][aã]o\s+do\s+(?:r[ée]u|acusado|denunciado)",
        texto,
        flags=re.IGNORECASE,
    )
    # 2. Pedido de condenação pelo MP (cuidando para não confundir com "não requer condenação" / "impossibilidade de condenação")
    m_cond = None
    for m_cand in re.finditer(
        r"(?:condena[çc][aã]o\s+(?:parcial\s+|integral\s+)?d[oa]\s+(?:r[ée]u|acusad[oa]|denunciad[oa])"
        r"|proced[êe]ncia\s+(?:parcial\s+|integral\s+)?da\s+(?:den[úu]ncia|a[çc][aã]o\s+penal|pretens[aã]o\s+punitiva)"
        r"|(?:requer(?:eu|endo)?|pugna(?:ndo)?|pleiteia(?:ndo)?|postula(?:ndo)?)\s+(?:pela\s+|a\s+)?condena[çc][aã]o"
        r"|seja\s+(?:o\s+(?:r[ée]u|acusado|denunciado)\s+)?condenado\s+nas\s+penas"
        r"|julgad[ao]\s+(?:parcialmente\s+|totalmente\s+)?procedente)",
        texto,
        flags=re.IGNORECASE,
    ):
        prefixo = texto[max(0, m_cand.start() - 45): m_cand.start()].lower()
        if any(neg in prefixo for neg in ("impossibilidade de", "inviável a", "não há prova para", "não requer", "afastar a", "incabível a")):
            continue
        m_cond = m_cand
        break

    # 3. Desclassificação pelo MP
    m_desc = re.search(
        r"(?:requer(?:eu|endo)?|pugna(?:ndo)?|opina(?:ndo)?|pleiteia(?:ndo)?|postula(?:ndo)?)"
        r"[^.\n;]{0,70}(?:desclassifica[çc][aã]o|emendatio\s+libelli|art\.?\s*383\s+do\s+cpp)"
        r"|desclassifica[çc][aã]o\s+(?:do\s+delito|da\s+conduta|para\s+o)",
        texto,
        flags=re.IGNORECASE,
    )

    if m_abs:
        trecho_abs = _recortar_trecho(texto, m_abs.start(), m_abs.end())
        janela_abs = texto[max(0, m_abs.start() - 40): min(len(texto), m_abs.end() + 140)]
        m_inc = re.search(r"art\.?\s*386\s*,?\s*(?:inciso\s*)?(I{1,3}V?|VI{0,2})\b", janela_abs, flags=re.IGNORECASE)
        inc_txt = f" (art. 386, {m_inc.group(1).upper()}, CPP)" if m_inc else ""
        pedidos.append(
            PedidoParteItem(
                tipo="absolvicao",
                descricao=f"absolvição{inc_txt}",
                crime=_detectar_crime_no_trecho(trecho_abs),
                fonte_id=fonte_id,
                folha=folha,
                trecho=trecho_abs,
                origem=origem,
            )
        )

    if m_cond:
        trecho_cond = _recortar_trecho(texto, m_cond.start(), m_cond.end())
        parcial = bool(
            m_abs
            or m_desc
            or re.search(r"\bparcial(?:mente)?\b", trecho_cond, flags=re.IGNORECASE)
        )
        pedidos.append(
            PedidoParteItem(
                tipo="condenacao_parcial" if parcial else "condenacao_total",
                descricao="condenação parcial" if parcial else "condenação total nos termos da denúncia",
                crime=_detectar_crime_no_trecho(trecho_cond),
                fonte_id=fonte_id,
                folha=folha,
                trecho=trecho_cond,
                origem=origem,
            )
        )

    if m_desc:
        trecho_desc = _recortar_trecho(texto, m_desc.start(), m_desc.end())
        pedidos.append(
            PedidoParteItem(
                tipo="desclassificacao",
                descricao="desclassificação da imputação",
                crime=_detectar_crime_no_trecho(trecho_desc),
                fonte_id=fonte_id,
                folha=folha,
                trecho=trecho_desc,
                origem=origem,
            )
        )

    # 4. Dano moral / reparação mínima (art. 387, IV, CPP / Tema 983/STJ)
    m_dm = re.search(
        r"(?:dano(?:s)?\s+mora(?:l|is)|repara[çc][aã]o\s+(?:m[íi]nima|pelos\s+danos)|valor\s+m[íi]nimo\s+(?:de|para)\s+repara[çc][aã]o|art\.?\s*387\s*,?\s*(?:inciso\s*)?iv)",
        texto,
        flags=re.IGNORECASE,
    )
    if m_dm:
        trecho_dm = _recortar_trecho(texto, m_dm.start(), m_dm.end())
        # Procura valor R$ no trecho ou na janela próxima
        janela_dm = texto[max(0, m_dm.start() - 60): min(len(texto), m_dm.end() + 180)]
        m_val = re.search(r"R\$\s*([\d\.]+,\d{2})", janela_dm, flags=re.IGNORECASE)
        valor_dm: Optional[float] = None
        if m_val:
            try:
                valor_dm = float(m_val.group(1).replace(".", "").replace(",", "."))
            except ValueError:
                valor_dm = None
        desc_dm = (
            f"reparação mínima por dano moral (R$ {m_val.group(1)})"
            if m_val and valor_dm
            else "reparação mínima por dano moral (art. 387, IV, CPP)"
        )
        pedidos.append(
            PedidoParteItem(
                tipo="dano_moral",
                descricao=desc_dm,
                crime=_detectar_crime_no_trecho(trecho_dm),
                fonte_id=fonte_id,
                folha=folha,
                trecho=trecho_dm,
                origem=origem,
                valor=valor_dm,
            )
        )

    # 5. Agravante / qualificadora / exasperação
    m_agr = re.search(
        r"(?:agravante\s+do\s+art\.?\s*61|art\.?\s*61\s*,?\s*(?:inciso\s*)?ii|reincid[êe]ncia|qualificadora|exaspera[çc][aã]o\s+da\s+pena(?:-base)?|causa\s+de\s+aumento|presen[çc]a\s+d[eo]s?\s+filh[oa]s?\s+menor)",
        texto,
        flags=re.IGNORECASE,
    )
    if m_agr:
        trecho_agr = _recortar_trecho(texto, m_agr.start(), m_agr.end())
        pedidos.append(
            PedidoParteItem(
                tipo="agravante_qualificadora",
                descricao="reconhecimento de agravante/qualificadora ou exasperação",
                crime=_detectar_crime_no_trecho(trecho_agr),
                fonte_id=fonte_id,
                folha=folha,
                trecho=trecho_agr,
                origem=origem,
            )
        )

    # 6. Regime inicial
    m_reg = re.search(
        r"regime\s+(?:inicial\s+)?(?:de\s+cumprimento\s+(?:de\s+pena\s+)?)?(fechado|semiaberto|aberto)",
        texto,
        flags=re.IGNORECASE,
    )
    if m_reg:
        trecho_reg = _recortar_trecho(texto, m_reg.start(), m_reg.end())
        pedidos.append(
            PedidoParteItem(
                tipo="regime",
                descricao=f"fixação de regime inicial {m_reg.group(1).lower()}",
                crime=_detectar_crime_no_trecho(trecho_reg),
                fonte_id=fonte_id,
                folha=folha,
                trecho=trecho_reg,
                origem=origem,
            )
        )

    # 7. Outro pedido expresso (quando nenhum dos tipos acima apareceu, mas há verbo de requerimento claro)
    if not pedidos:
        m_outro = re.search(
            r"\b(?:requer(?:eu)?|pugna|pleiteia|postula)\s+(?:a\s+|o\s+|pela\s+|pelo\s+)?(?:manuten[çc][aã]o|revoga[çc][aã]o|decreta[çc][aã]o|extin[çc][aã]o|prorroga[çc][aã]o|medida\s+protetiva)[^.\n]{0,120}",
            texto,
            flags=re.IGNORECASE,
        )
        if m_outro:
            trecho_o = _recortar_trecho(texto, m_outro.start(), m_outro.end())
            pedidos.append(
                PedidoParteItem(
                    tipo="outro",
                    descricao="outro requerimento ministerial",
                    crime=_detectar_crime_no_trecho(trecho_o),
                    fonte_id=fonte_id,
                    folha=folha,
                    trecho=trecho_o,
                    origem=origem,
                )
            )

    return pedidos


_REGRAS_PEDIDOS_SUBSIDIARIOS_DEFESA: List[Tuple[str, str, str]] = [
    (
        "pena_minima",
        "fixação da pena-base no mínimo legal",
        r"pena(?:-|\s+)base\s+no\s+m[íi]nimo|m[íi]nimo\s+legal|circunst[âa]ncias\s+judiciais\s+favor[áa]veis",
    ),
    (
        "atenuante",
        "reconhecimento de atenuante (confissão/menoridade/art. 65 CP)",
        r"confiss[aã]o\s+espont[âa]nea|s[úu]mula\s+545|atenuante\s+d[oa]\s+art\.?\s*65|menoridade\s+relativa",
    ),
    (
        "regime",
        "fixação de regime inicial mais brando / aberto",
        r"regime\s+(?:inicial\s+)?(?:mais\s+brando|menos\s+gravoso|aberto)",
    ),
    (
        "substituicao",
        "substituição da pena por restritiva de direitos (art. 44 CP)",
        r"substitui[çc][aã]o\s+da\s+pena|penas?\s+restritivas?\s+de\s+direitos?|art\.?\s*44\s+do\s+(?:cp|c[óo]digo\s+penal)",
    ),
    (
        "sursis",
        "suspensão condicional da pena (sursis — art. 77 CP)",
        r"\bsursis\b|suspens[aã]o\s+condicional\s+da\s+pena|art\.?\s*77\s+do\s+(?:cp|c[óo]digo\s+penal)",
    ),
    (
        "gratuidade",
        "gratuidade da justiça / isenção de custas",
        r"justi[çc]a\s+gratuita|gratuidade\s+(?:da\s+justi[çc]a|judici[áa]ria)|isen[çc][aã]o\s+d[eo]\s+custas",
    ),
    (
        "dano_moral",
        "afastamento ou redução do valor de dano moral",
        r"(?:afastamento|exclus[aã]o|improced[êe]ncia|redu[çc][aã]o|modicidade|indevid[oa])[^.\n;]{0,50}dano(?:s)?\s+mora(?:l|is)|dano(?:s)?\s+mora(?:l|is)[^.\n;]{0,50}(?:indevid|afastad|exclu|reduz|m[íi]nim)",
    ),
    (
        "recorrer_liberdade",
        "direito de recorrer em liberdade",
        r"recorrer\s+em\s+liberdade|direito\s+de\s+apelar\s+em\s+liberdade|revoga[çc][aã]o\s+da\s+pris[aã]o\s+preventiva",
    ),
]


def extrair_pedidos_subsidiarios_defesa_texto(
    texto: str,
    *,
    fonte_id: str,
    folha: str = "",
    origem: str = "memoriais",
) -> List[PedidoParteItem]:
    """Extrai pedidos subsidiários explícitos formulados pela Defesa com trecho e fonte."""
    if not texto or not texto.strip():
        return []
    pedidos: List[PedidoParteItem] = []
    for tipo, desc, pat in _REGRAS_PEDIDOS_SUBSIDIARIOS_DEFESA:
        m = re.search(pat, texto, flags=re.IGNORECASE)
        if m:
            trecho = _recortar_trecho(texto, m.start(), m.end())
            pedidos.append(
                PedidoParteItem(
                    tipo=tipo,
                    descricao=desc,
                    crime=_detectar_crime_no_trecho(trecho),
                    fonte_id=fonte_id,
                    folha=folha,
                    trecho=trecho,
                    origem=origem,
                )
            )
    return pedidos


def extrair_alegacoes_orais_ata(
    texto: str,
    *,
    fonte_id: str,
    folha: str = "",
) -> Tuple[List[PedidoParteItem], List[PedidoParteItem], List[str], List[str]]:
    """Extrai alegações finais orais registradas em Termo de Audiência, atribuindo à parte certa.

    Retorna ``(pedidos_mp, pedidos_defesa, textos_defesa_oral, alertas)``.
    Se a ata registrar alegações finais orais sem identificar se são do MP ou da Defesa, não inventa
    atribuição e registra alerta de ambiguidade.
    """
    if not texto or not re.search(r"alega[çc][õo]es\s+finais|debates\s+orais|memoriais\s+orais", texto, flags=re.IGNORECASE):
        return [], [], [], []

    # Localiza o início da seção de alegações finais na ata
    m_ini = re.search(r"alega[çc][õo]es\s+finais|debates\s+orais|memoriais\s+orais", texto, flags=re.IGNORECASE)
    bloco = texto[max(0, m_ini.start() - 40):] if m_ini else texto

    # Se a ata apenas diz que alegações serão por memoriais escritos / prazo aberto, não há alegação oral aqui
    if re.search(
        r"alega[çc][õo]es\s+finais\s+(?:por\s+memoriais|escritas|em\s+memoriais)|convertid[ao]s?\s+em\s+memoriais|vista\s+sucessiva\s+para\s+memoriais",
        bloco,
        flags=re.IGNORECASE,
    ) and not re.search(
        r"(?:o\s+minist[ée]rio\s+p[úu]blico|pelo\s+minist[ée]rio\s+p[úu]blico|a\s+defesa|pela\s+defesa)\s+(?:requereu|pugnou|pediu|manifestou|sustentou|pleiteou)",
        bloco,
        flags=re.IGNORECASE,
    ):
        return [], [], [], []

    # Divide em segmentos por marcador de parte ou por frases/parágrafos
    pat_mp = re.compile(
        r"(?:pel[oa]\s+|o\s+|a\s+)?(?:minist[ée]rio\s+p[úu]blico|promotor(?:a|ia)?(?:\s+de\s+justi[çc]a)?|parquet|representante\s+ministerial|[óo]rg[aã]o\s+ministerial)\b",
        flags=re.IGNORECASE,
    )
    pat_def = re.compile(
        r"(?:pel[oa]\s+|a\s+|o\s+)?(?:defesa(?:\s+t[ée]cnica)?|defensor(?:a|ia)?(?:\s+p[úu]blic[ao])?|advogad[ao](?:\s+do\s+(?:r[ée]u|acusado))?|patrono\s+do\s+(?:r[ée]u|acusado))\b",
        flags=re.IGNORECASE,
    )

    pedidos_mp: List[PedidoParteItem] = []
    pedidos_def: List[PedidoParteItem] = []
    textos_def_oral: List[str] = []
    alertas: List[str] = []

    segmentos = [s.strip() for s in re.split(r"(?<=[.;\n])\s+|\n+", bloco) if s.strip()]
    parte_corrente: Optional[str] = None
    trechos_mp: List[str] = []
    trechos_def: List[str] = []
    trechos_ambiguos: List[str] = []

    for seg in segmentos:
        tem_mp = bool(pat_mp.search(seg))
        tem_def = bool(pat_def.search(seg))
        if tem_mp and not tem_def:
            parte_corrente = "MP"
            trechos_mp.append(seg)
        elif tem_def and not tem_mp:
            parte_corrente = "DEFESA"
            trechos_def.append(seg)
        elif tem_mp and tem_def:
            # Frase menciona ambas as partes (ex.: "O MP requereu X; a Defesa requereu Y")
            m_mp = pat_mp.search(seg)
            m_df = pat_def.search(seg)
            if m_mp and m_df:
                if m_mp.start() < m_df.start():
                    trechos_mp.append(seg[m_mp.start(): m_df.start()].strip())
                    trechos_def.append(seg[m_df.start():].strip())
                    parte_corrente = "DEFESA"
                else:
                    trechos_def.append(seg[m_df.start(): m_mp.start()].strip())
                    trechos_mp.append(seg[m_mp.start():].strip())
                    parte_corrente = "MP"
        else:
            if parte_corrente == "MP":
                trechos_mp.append(seg)
            elif parte_corrente == "DEFESA":
                trechos_def.append(seg)
            elif re.search(r"requer|pugn|pedi|pleite|absolvi|condena|pena|sursis|desclassifica|dano\s+moral", seg, flags=re.IGNORECASE):
                trechos_ambiguos.append(seg)

    if trechos_mp:
        pedidos_mp.extend(
            extrair_pedidos_mp_texto(" ".join(trechos_mp), fonte_id=fonte_id, folha=folha, origem="oral")
        )
    if trechos_def:
        txt_def = " ".join(trechos_def)
        textos_def_oral.append(txt_def)
        pedidos_def.extend(
            extrair_pedidos_subsidiarios_defesa_texto(txt_def, fonte_id=fonte_id, folha=folha, origem="oral")
        )
    if trechos_ambiguos and not trechos_mp and not trechos_def:
        alertas.append(
            f"Alegações finais orais com autoria ambígua (MP ou Defesa não identificados) em {fonte_id}: conferir a ata."
        )

    return pedidos_mp, pedidos_def, textos_def_oral, alertas


def _parse_data_br_ou_iso(texto_data: str) -> Optional[datetime.date]:
    """Converte dd/mm/aaaa ou aaaa-mm-dd para ``datetime.date`` (None se inválida)."""
    t = (texto_data or "").strip()
    if not t:
        return None
    m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", t[:10])
    if m_iso:
        try:
            return datetime.date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
        except ValueError:
            return None
    m_br = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$", t)
    if m_br:
        try:
            return datetime.date(int(m_br.group(3)), int(m_br.group(2)), int(m_br.group(1)))
        except ValueError:
            return None
    return None


def _extrair_data_nascimento_reu_ponto(
    pecas: List[PecaClassificada],
    nome_reu: str,
    nome_ofendida: str,
) -> PontoDecisivoItem:
    """Extrai data de nascimento somente da qualificação ligada ao réu; nunca de outra pessoa.

    Sempre retorna ``estado='a_confirmar'`` quando encontrada (nunca preenche ``meta.data_nascimento_reu``
    automaticamente antes da confirmação do juiz).
    """
    efeito = (
        "Atenuante do art. 65, I, do CP e redução pela metade do prazo prescricional (art. 115 do CP) "
        "se menor de 21 anos na data do fato ou maior de 70 na data da sentença."
    )
    cats_validas = {"DENUNCIA", "BO", "CAC_FAC", "TERMO_AUDIENCIA", "RESPOSTA_ACUSACAO", "ALEGACOES_FINAIS_DEFESA"}
    pat_nasc = re.compile(
        r"(?:nascid[oa]\s+em|data\s+de\s+nascimento\s*:?|d\.?\s*n\.?\s*:?|dn\s*:?)\s*(\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{4}-\d{2}-\d{2})",
        flags=re.IGNORECASE,
    )
    pat_reu_label = re.compile(
        r"\b(?:denunciado|acusado|r[ée]u|indiciado|conduzido|flagranteado|interrogado|requerido|agressor)\b",
        flags=re.IGNORECASE,
    )
    pat_vit_label = re.compile(
        r"\b(?:v[íi]tima|ofendida|requerente|noticiante|declarante|testemunha)\b",
        flags=re.IGNORECASE,
    )

    nome_reu_low = (nome_reu or "").strip().lower()
    nome_vit_low = (nome_ofendida or "").strip().lower()
    primeiro_reu = nome_reu_low.split()[0] if len(nome_reu_low.split()) >= 2 else ""

    for p in pecas:
        if p.categoria not in cats_validas:
            continue
        txt = p.conteudo or ""
        if not txt:
            continue
        for m in pat_nasc.finditer(txt):
            dt = _parse_data_br_ou_iso(m.group(1))
            if dt is None or dt.year < 1920 or dt.year > datetime.date.today().year - 12:
                continue
            # Analisa o contexto imediatamente anterior (até 260 chars antes da data)
            ini_ctx = max(0, m.start() - 260)
            ctx_antes = txt[ini_ctx: m.start()]
            ctx_antes_low = ctx_antes.lower()

            # Última posição de marcador do réu vs. marcador da vítima/outra pessoa
            pos_reu = -1
            if nome_reu_low and nome_reu_low in ctx_antes_low:
                pos_reu = max(pos_reu, ctx_antes_low.rfind(nome_reu_low))
            elif primeiro_reu and len(primeiro_reu) >= 3:
                m_pr = list(re.finditer(rf"\b{re.escape(primeiro_reu)}\b", ctx_antes_low))
                if m_pr:
                    pos_reu = max(pos_reu, m_pr[-1].start())
            for mr in pat_reu_label.finditer(ctx_antes):
                pos_reu = max(pos_reu, mr.start())

            pos_vit = -1
            if nome_vit_low and nome_vit_low in ctx_antes_low:
                pos_vit = max(pos_vit, ctx_antes_low.rfind(nome_vit_low))
            for mv in pat_vit_label.finditer(ctx_antes):
                pos_vit = max(pos_vit, mv.start())

            # Exige vínculo inequívoco com o réu e mais próximo do que qualquer marcador da vítima/terceiro
            if pos_reu == -1 or pos_vit > pos_reu:
                continue

            return PontoDecisivoItem(
                chave="data_nascimento_reu",
                valor=dt.isoformat(),
                estado="a_confirmar",
                fonte_id=p.doc_id,
                folha=p.folha,
                trecho=_recortar_trecho(txt, m.start(), m.end()),
                efeito=efeito,
            )

    return PontoDecisivoItem(
        chave="data_nascimento_reu",
        valor=None,
        estado="ausente",
        efeito=efeito,
    )


def _extrair_antecedentes_ponto(
    peca_cac: Optional[PecaClassificada],
    data_fato: str,
) -> PontoDecisivoItem:
    """Analisa CAC/FAC separando reincidência (≤ 5 anos, art. 64, I, CP), maus antecedentes (> 5 anos) e em curso (Súmula 444/Tema 129)."""
    efeito_base = (
        "Define reincidência (≤ 5 anos do trânsito/extinção — art. 64, I, CP), maus antecedentes (> 5 anos — art. 59 CP) "
        "ou neutralidade (ações/inquéritos em curso — Súmula 444/STJ e Tema 129/STF)."
    )
    if not peca_cac or not (peca_cac.conteudo or "").strip():
        return PontoDecisivoItem(
            chave="antecedentes",
            valor=None,
            estado="ausente",
            efeito=efeito_base,
        )

    txt = peca_cac.conteudo
    low = txt.lower()
    ref_dt = _parse_data_br_ou_iso(data_fato) or datetime.date.today()

    m_cond = None
    for m_cand in re.finditer(
        r"(?:condena[çc][aã]o\s+(?:penal\s+)?(?:definitiva|transitada\s+em\s+julgado)|tr[âa]nsito\s+em\s+julgado|transitada\s+em\s+julgado|senten[çc]a\s+condenat[óo]ria\s+definitiva)",
        txt,
        flags=re.IGNORECASE,
    ):
        # Negação na mesma oração, mesmo com palavras no meio ("sem denúncia ou sentença transitada em julgado").
        prefixo = txt[max(0, m_cand.start() - 70): m_cand.start()].lower()
        if re.search(r"\b(?:sem|n[aã]o\s+h[áa]|inexistem?|aus[êe]ncia\s+de|n[aã]o)\b[^.;\n]{0,50}$", prefixo):
            continue
        m_cond = m_cand
        break
    tem_nada = any(
        x in low for x in ("nada consta", "nada existe", "nada existir", "não constam condenações", "sem antecedentes")
    )

    if m_cond and not tem_nada:
        janela = txt[max(0, m_cond.start() - 80): min(len(txt), m_cond.end() + 180)]
        m_dt = re.search(r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{4}-\d{2}-\d{2})", janela)
        dt_tr = _parse_data_br_ou_iso(m_dt.group(1)) if m_dt else None
        trecho = _recortar_trecho(txt, m_cond.start(), m_cond.end())
        if dt_tr is not None and _parse_data_br_ou_iso(data_fato) is None:
            # Sem a data do fato não dá para separar reincidência (art. 63) de maus antecedentes: nada se conclui.
            return PontoDecisivoItem(
                chave="antecedentes",
                valor={"classificacao": "verificar_data_do_fato", "data_transito": dt_tr.isoformat()},
                estado="a_confirmar",
                fonte_id=peca_cac.doc_id,
                folha=peca_cac.folha,
                trecho=trecho,
                efeito="Condenação definitiva encontrada, mas a data do fato não foi extraída: confira se o fato é posterior ao trânsito em julgado (reincidência, art. 63 do CP) ou anterior (maus antecedentes, art. 59).",
            )
        if dt_tr is not None:
            anos = (ref_dt - dt_tr).days / 365.25
            if dt_tr > ref_dt:
                # Trânsito em julgado POSTERIOR ao fato: não há reincidência (art. 63 do CP exige crime cometido
                # depois do trânsito); a condenação definitiva pode valer como maus antecedentes.
                return PontoDecisivoItem(
                    chave="antecedentes",
                    valor={"classificacao": "maus_antecedentes", "data_transito": dt_tr.isoformat(), "anos_decorridos": round(anos, 2)},
                    estado="a_confirmar",
                    fonte_id=peca_cac.doc_id,
                    folha=peca_cac.folha,
                    trecho=trecho,
                    efeito="Condenação com trânsito em julgado posterior ao fato: não gera reincidência (art. 63 do CP), mas pode configurar maus antecedentes na 1ª fase (art. 59 do CP).",
                )
            if anos <= 5.0:
                # O período depurador do art. 64, I, conta do cumprimento ou da extinção da pena, que é posterior ao
                # trânsito: fato até 5 anos depois do trânsito está, com certeza, dentro do período.
                return PontoDecisivoItem(
                    chave="antecedentes",
                    valor={"classificacao": "reincidencia", "data_transito": dt_tr.isoformat(), "anos_decorridos": round(anos, 2)},
                    estado="a_confirmar",
                    fonte_id=peca_cac.doc_id,
                    folha=peca_cac.folha,
                    trecho=trecho,
                    efeito="Agravante da reincidência (arts. 61, I, e 63 do CP): fato praticado até 5 anos após o trânsito em julgado, logo dentro do período depurador do art. 64, I, do CP; reflexos no regime e no sursis.",
                )
            return PontoDecisivoItem(
                chave="antecedentes",
                valor={"classificacao": "verificar_periodo_depurador", "data_transito": dt_tr.isoformat(), "anos_decorridos": round(anos, 2)},
                estado="a_confirmar",
                fonte_id=peca_cac.doc_id,
                folha=peca_cac.folha,
                trecho=trecho,
                efeito="Mais de 5 anos entre o trânsito em julgado e o fato: será reincidência se o fato ocorreu até 5 anos após o cumprimento ou a extinção da pena (art. 64, I, do CP); se não, maus antecedentes (art. 59). Conferir na certidão a data de cumprimento/extinção.",
            )
        return PontoDecisivoItem(
            chave="antecedentes",
            valor={"classificacao": "condenacao_sem_data"},
            estado="a_confirmar",
            fonte_id=peca_cac.doc_id,
            folha=peca_cac.folha,
            trecho=trecho,
            efeito="Condenação definitiva sem data de trânsito/extinção extraída: conferir período depurador de 5 anos (art. 64, I, CP).",
        )

    m_curso = re.search(
        r"(?:a[çc][aã]o\s+penal\s+em\s+curso|inqu[ée]rito\s+(?:policial\s+)?em\s+(?:curso|andamento)|processo\s+em\s+andamento|sem\s+tr[âa]nsito\s+em\s+julgado)",
        txt,
        flags=re.IGNORECASE,
    )
    if m_curso:
        return PontoDecisivoItem(
            chave="antecedentes",
            valor={"classificacao": "neutro_em_curso"},
            estado="extraido",
            fonte_id=peca_cac.doc_id,
            folha=peca_cac.folha,
            trecho=_recortar_trecho(txt, m_curso.start(), m_curso.end()),
            efeito="Neutro na dosimetria: inquéritos e ações penais em curso não configuram maus antecedentes nem reincidência (Súmula 444/STJ e Tema 129/STF).",
        )

    return PontoDecisivoItem(
        chave="antecedentes",
        valor={"classificacao": "primario"},
        estado="extraido",
        fonte_id=peca_cac.doc_id,
        folha=peca_cac.folha,
        trecho=_recortar_trecho(txt, 0, min(len(txt), 200)),
        efeito="Réu tecnicamente primário e sem maus antecedentes (Súmula 444/STJ).",
    )


def _extrair_confissao_ponto(
    pecas: List[PecaClassificada],
    transcricoes: Optional[List[Dict[str, Any]]] = None,
) -> PontoDecisivoItem:
    """Extrai confissão (total, parcial ou qualificada) do interrogatório (transcrição ou termo), sempre ``a_confirmar``."""
    efeito = "Atenuante da confissão espontânea na 2ª fase (art. 65, III, 'd', do CP e Súmula 545/STJ)."
    pat_conf = re.compile(
        r"(?:confess(?:ou|a|o)\s+(?:parcialmente\s+|integralmente\s+|que\s+|ter\s+|o\s+fato|os\s+fatos|a\s+pr[áa]tica)?|admiti(?:u|o)\s+(?:parcialmente\s+|que\s+|ter\s+|a\s+pr[áa]tica)|disse\s+que\s+(?:de\s+fato\s+)?(?:empurrou|bateu|agrediu|amea[çc]ou|foi\s+at[ée]\s+a\s+resid[êe]ncia|sabia\s+da\s+medida))",
        flags=re.IGNORECASE,
    )
    pat_qualif = re.compile(
        r"(?:leg[íi]tima\s+defesa|para\s+se\s+defender|apenas\s+se\s+defendeu|em\s+defesa|s[óo]\s+reagiu|foi\s+provocado|para\s+ver\s+a\s+filha)",
        flags=re.IGNORECASE,
    )
    pat_parcial = re.compile(
        r"(?:parcialmente|confessou\s+apenas|admitiu\s+apenas|mas\s+neg(?:ou|a)|por[ée]m\s+neg(?:ou|a)|nega\s+a\s+(?:amea[çc]a|les[aã]o|agress[aã]o))",
        flags=re.IGNORECASE,
    )

    # 1. Prioriza transcrições de interrogatório (com minuto/timestamp)
    for tr in transcricoes or []:
        if not isinstance(tr, dict):
            continue
        papel = str(tr.get("papel") or "").upper()
        arq = str(tr.get("arquivo") or "").lower()
        if papel != "INTERROGATORIO_REU" and "interrogat" not in arq:
            continue
        media_id = str(tr.get("media_id") or tr.get("arquivo") or "INTERROGATORIO")
        segs = tr.get("segmentos") or tr.get("segmentos_literais") or []
        for seg in segs:
            if not isinstance(seg, dict):
                continue
            txt_seg = str(seg.get("texto_literal") or seg.get("texto") or "")
            m = pat_conf.search(txt_seg)
            if m:
                minuto = str(seg.get("timestamp") or seg.get("inicio") or "")
                tipo_conf = "qualificada" if pat_qualif.search(txt_seg) else ("parcial" if pat_parcial.search(txt_seg) else "total")
                return PontoDecisivoItem(
                    chave="confissao",
                    valor={"tipo": tipo_conf, "minuto": minuto},
                    estado="a_confirmar",
                    fonte_id=media_id,
                    trecho=_recortar_trecho(txt_seg, m.start(), m.end()),
                    efeito=efeito,
                )
        txt_full = str(tr.get("texto") or "")
        m_full = pat_conf.search(txt_full)
        if m_full:
            trecho = _recortar_trecho(txt_full, m_full.start(), m_full.end())
            tipo_conf = "qualificada" if pat_qualif.search(trecho) else ("parcial" if pat_parcial.search(trecho) else "total")
            return PontoDecisivoItem(
                chave="confissao",
                valor={"tipo": tipo_conf, "minuto": ""},
                estado="a_confirmar",
                fonte_id=media_id,
                trecho=trecho,
                efeito=efeito,
            )

    # 2. Busca em termos de interrogatório / audiência / APF
    for p in pecas:
        if p.categoria not in ("TERMO_AUDIENCIA", "BO") and "interrogat" not in p.titulo.lower():
            continue
        txt = p.conteudo or ""
        m = pat_conf.search(txt)
        if m:
            trecho = _recortar_trecho(txt, m.start(), m.end())
            tipo_conf = "qualificada" if pat_qualif.search(trecho) else ("parcial" if pat_parcial.search(trecho) else "total")
            m_min = re.search(r"\b(\d{1,2}:\d{2}(?::\d{2})?|\d{2}min\d{2}s)\b", trecho)
            return PontoDecisivoItem(
                chave="confissao",
                valor={"tipo": tipo_conf, "minuto": m_min.group(1) if m_min else ""},
                estado="a_confirmar",
                fonte_id=p.doc_id,
                folha=p.folha,
                trecho=trecho,
                efeito=efeito,
            )

    return PontoDecisivoItem(chave="confissao", valor=None, estado="ausente", efeito=efeito)


def extrair_pontos_decisivos(
    pecas: List[PecaClassificada],
    *,
    nome_reu: str = "",
    nome_ofendida: str = "",
    data_fato: str = "",
    peca_cac: Optional[PecaClassificada] = None,
    peca_int: Optional[PecaClassificada] = None,
    data_intimacao_mpu: str = "",
    intimidado_mpu: bool = False,
    pedidos_mp: Optional[List[PedidoParteItem]] = None,
    transcricoes: Optional[List[Dict[str, Any]]] = None,
) -> List[PontoDecisivoItem]:
    """Extrai os 10 pontos decisivos obrigatórios dos autos, sempre com fonte e estado explícito."""
    pontos: List[PontoDecisivoItem] = []

    # 1. data_nascimento_reu
    pontos.append(_extrair_data_nascimento_reu_ponto(pecas, nome_reu, nome_ofendida))

    # 2. antecedentes
    pontos.append(_extrair_antecedentes_ponto(peca_cac, data_fato))

    # 3. confissao
    pontos.append(_extrair_confissao_ponto(pecas, transcricoes))

    # 4. prisao_provisoria (detração — art. 387, § 2º, CPP)
    efeito_detr = "Detração penal (art. 387, § 2º, do CPP) para fins de definição do regime inicial."
    dt_prisao: Optional[datetime.date] = None
    dt_soltura: Optional[datetime.date] = None
    fonte_prisao = ""
    folha_prisao = ""
    trecho_prisao = ""
    for p in pecas:
        txt = p.conteudo or ""
        if dt_prisao is None:
            m_pr = re.search(
                r"(?:preso\s+em\s+flagrante|pris[aã]o\s+(?:em\s+flagrante|preventiva)|auto\s+de\s+pris[aã]o\s+em\s+flagrante)[^.\n]{0,80}?(\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{4}-\d{2}-\d{2})",
                txt,
                flags=re.IGNORECASE,
            )
            if m_pr:
                dt_prisao = _parse_data_br_ou_iso(m_pr.group(1))
                fonte_prisao = p.doc_id
                folha_prisao = p.folha
                trecho_prisao = _recortar_trecho(txt, m_pr.start(), m_pr.end())
        if dt_soltura is None:
            m_so = re.search(
                r"(?:alvar[áa]\s+de\s+soltura|liberdade\s+provis[óo]ria|posto\s+em\s+liberdade|solto\s+em)[^.\n]{0,80}?(\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{4}-\d{2}-\d{2})",
                txt,
                flags=re.IGNORECASE,
            )
            if m_so:
                dt_soltura = _parse_data_br_ou_iso(m_so.group(1))
                if not fonte_prisao:
                    fonte_prisao = p.doc_id
                    folha_prisao = p.folha
                    trecho_prisao = _recortar_trecho(txt, m_so.start(), m_so.end())
    if dt_prisao or dt_soltura:
        dias = max(0, (dt_soltura - dt_prisao).days) if (dt_prisao and dt_soltura and dt_soltura >= dt_prisao) else None
        pontos.append(
            PontoDecisivoItem(
                chave="prisao_provisoria",
                valor={
                    "data_prisao": dt_prisao.isoformat() if dt_prisao else "",
                    "data_soltura": dt_soltura.isoformat() if dt_soltura else "",
                    "dias": dias,
                },
                estado="a_confirmar",
                fonte_id=fonte_prisao,
                folha=folha_prisao,
                trecho=trecho_prisao,
                efeito=efeito_detr,
            )
        )
    else:
        pontos.append(PontoDecisivoItem(chave="prisao_provisoria", valor=None, estado="ausente", efeito=efeito_detr))

    # 5. representacao (condição de procedibilidade / decadência)
    efeito_rep = "Condição de procedibilidade para crimes de ação penal pública condicionada e aferição de decadência (art. 38 CPP / art. 103 CP)."
    ponto_rep = PontoDecisivoItem(chave="representacao", valor=None, estado="ausente", efeito=efeito_rep)
    for p in pecas:
        txt = p.conteudo or ""
        m_rep = re.search(
            r"(?:termo\s+de\s+representa[çc][aã]o|deseja\s+representar\s+criminalmente|representa\s+criminalmente|manifestou\s+(?:inequ[íi]voco\s+)?interesse\s+na\s+persecu[çc][aã]o)",
            txt,
            flags=re.IGNORECASE,
        )
        if m_rep:
            janela = txt[max(0, m_rep.start() - 40): min(len(txt), m_rep.end() + 100)]
            m_dt_rep = re.search(r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{4}-\d{2}-\d{2})", janela)
            dt_rep = _parse_data_br_ou_iso(m_dt_rep.group(1)) if m_dt_rep else _parse_data_br_ou_iso(p.data_peca)
            ponto_rep = PontoDecisivoItem(
                chave="representacao",
                valor={"presente": True, "data": dt_rep.isoformat() if dt_rep else ""},
                estado="extraido",
                fonte_id=p.doc_id,
                folha=p.folha,
                trecho=_recortar_trecho(txt, m_rep.start(), m_rep.end()),
                efeito=efeito_rep,
            )
            break
    pontos.append(ponto_rep)

    # 6. intimacao_mpu (art. 24-A da LMP)
    efeito_mpu = "Pressuposto objetivo do crime do art. 24-A da Lei nº 11.340/2006 (ciência pessoal prévia da medida protetiva)."
    if peca_int and (intimidado_mpu or data_intimacao_mpu):
        pontos.append(
            PontoDecisivoItem(
                chave="intimacao_mpu",
                valor={"intimidado_pessoalmente": intimidado_mpu, "data": data_intimacao_mpu},
                estado="extraido",
                fonte_id=peca_int.doc_id,
                folha=peca_int.folha,
                trecho=_recortar_trecho(peca_int.conteudo, 0, min(len(peca_int.conteudo), 220)),
                efeito=efeito_mpu,
            )
        )
    else:
        pontos.append(PontoDecisivoItem(chave="intimacao_mpu", valor=None, estado="ausente", efeito=efeito_mpu))

    # 7. pedido_dano_moral (derivado de pedidos_mp)
    efeito_dm = "Autoriza a fixação de valor mínimo de reparação por dano moral in re ipsa (art. 387, IV, CPP e Tema 983/STJ)."
    ped_dm = next((p for p in (pedidos_mp or []) if p.tipo == "dano_moral"), None)
    if ped_dm:
        pontos.append(
            PontoDecisivoItem(
                chave="pedido_dano_moral",
                valor={"presente": True, "valor": ped_dm.valor},
                estado="extraido",
                fonte_id=ped_dm.fonte_id,
                folha=ped_dm.folha,
                trecho=ped_dm.trecho,
                efeito=efeito_dm,
            )
        )
    else:
        pontos.append(PontoDecisivoItem(chave="pedido_dano_moral", valor=None, estado="ausente", efeito=efeito_dm))

    # 8. vitima_condicao (gestante, idosa, com deficiência ou na presença de descendente)
    efeito_vc = "Possível agravante ou causa de aumento (art. 61, II, 'h', CP; art. 129, § 13 / art. 147-A, § 1º, CP)."
    condicoes_achadas: List[str] = []
    fonte_vc, folha_vc, trecho_vc = "", "", ""
    regras_vc = (
        ("gestante", r"\bgestante\b|\bgr[áa]vida\b"),
        ("idosa", r"\bidosa\b|maior\s+de\s+60\s+anos"),
        ("deficiencia", r"pessoa\s+com\s+defici[êe]ncia|\bpcd\b|portadora\s+de\s+defici[êe]ncia"),
        ("presenca_descendente", r"presen[çc]a\s+d[aeo]s?\s+(?:filh[oa]s?|descendentes?|crian[çc]as?)\s*(?:menor(?:es)?)?|diante\s+d[aeo]s?\s+filh[oa]s?"),
    )
    for p in pecas:
        if p.categoria not in ("DENUNCIA", "BO", "LAUDO_PERICIAL", "TERMO_AUDIENCIA", "ALEGACOES_FINAIS_MP"):
            continue
        txt = p.conteudo or ""
        for rotulo, pat in regras_vc:
            if rotulo in condicoes_achadas:
                continue
            m_vc = re.search(pat, txt, flags=re.IGNORECASE)
            if m_vc:
                condicoes_achadas.append(rotulo)
                if not fonte_vc:
                    fonte_vc, folha_vc = p.doc_id, p.folha
                    trecho_vc = _recortar_trecho(txt, m_vc.start(), m_vc.end())
    if condicoes_achadas:
        pontos.append(
            PontoDecisivoItem(
                chave="vitima_condicao",
                valor=condicoes_achadas,
                estado="a_confirmar",
                fonte_id=fonte_vc,
                folha=folha_vc,
                trecho=trecho_vc,
                efeito=efeito_vc,
            )
        )
    else:
        pontos.append(PontoDecisivoItem(chave="vitima_condicao", valor=None, estado="ausente", efeito=efeito_vc))

    # 9. revelia (art. 367 CPP)
    efeito_rev = "Prosseguimento do processo sem a presença do acusado (art. 367 do CPP); ausência de interrogatório judicial."
    ponto_rev = PontoDecisivoItem(chave="revelia", valor=None, estado="ausente", efeito=efeito_rev)
    for p in pecas:
        m_rev = re.search(r"decretad[ao]\s+a\s+revelia|revelia\s+do\s+(?:r[ée]u|acusado)|art\.?\s*367\s+do\s+cpp", p.conteudo or "", flags=re.IGNORECASE)
        if m_rev:
            ponto_rev = PontoDecisivoItem(
                chave="revelia",
                valor=True,
                estado="a_confirmar",
                fonte_id=p.doc_id,
                folha=p.folha,
                trecho=_recortar_trecho(p.conteudo, m_rev.start(), m_rev.end()),
                efeito=efeito_rev,
            )
            break
    pontos.append(ponto_rev)

    # 10. reu_preso (direito de recorrer em liberdade — art. 387, § 1º, CPP)
    efeito_rp = "Define o direito de recorrer em liberdade ou a manutenção da prisão preventiva (art. 387, § 1º, do CPP)."
    ponto_rp = PontoDecisivoItem(chave="reu_preso", valor=None, estado="ausente", efeito=efeito_rp)
    for p in reversed(pecas):
        txt = p.conteudo or ""
        m_solto = re.search(r"alvar[áa]\s+de\s+soltura|liberdade\s+provis[óo]ria|responde(?:u)?\s+em\s+liberdade|posto\s+em\s+liberdade", txt, flags=re.IGNORECASE)
        m_preso = re.search(r"preso\s+preventivamente|pris[aã]o\s+preventiva\s+(?:decretada|mantida|vigente)|recolhido\s+ao\s+sistema\s+prisional", txt, flags=re.IGNORECASE)
        if m_solto:
            ponto_rp = PontoDecisivoItem(
                chave="reu_preso",
                valor="solto",
                estado="a_confirmar",
                fonte_id=p.doc_id,
                folha=p.folha,
                trecho=_recortar_trecho(txt, m_solto.start(), m_solto.end()),
                efeito=efeito_rp,
            )
            break
        if m_preso:
            ponto_rp = PontoDecisivoItem(
                chave="reu_preso",
                valor="preso",
                estado="a_confirmar",
                fonte_id=p.doc_id,
                folha=p.folha,
                trecho=_recortar_trecho(txt, m_preso.start(), m_preso.end()),
                efeito=efeito_rp,
            )
            break
    pontos.append(ponto_rp)

    return pontos


def _gerar_sintese_dinamica_peca(cat: str, titulo: str, conteudo: str, doc_id: str, is_demo: bool = False) -> str:
    if is_demo:
        return SINTESES_EXECUTIVAS_LAYA.get(cat, conteudo[:180].strip())

    up = conteudo.upper()
    norm_up = re.sub(r"\s+", " ", up)
    titulo_up = titulo.upper()

    if "LAUDO DE PERÍCIA" in titulo_up or "EXAME DE CORPO DE DELITO" in titulo_up or cat == "LAUDO_PERICIAL":
        laudos = sorted(set(re.findall(r"\bUN-[A-Z0-9-]+\b", up)))
        laudo_cod = laudos[0] if laudos else doc_id
        natureza_lesao = "natureza leve" if "NATUREZA LEVE" in norm_up else ("ação contundente" if "CONTUNDENTE" in norm_up else "exame pericial oficial")
        return f"Síntese Laya S1: Laudo de Perícia Criminal {laudo_cod} ({natureza_lesao}) extraído integralmente dos autos."
    if "TERMO DE QUALIFICAÇÃO E INTERROGATÓRIO" in titulo_up:
        return "Síntese Laya S1: Termo de Qualificação e Interrogatório do conduzido lavrado na Polícia Civil."
    if "TERMO DE DECLARAÇÕES DA OFENDIDA" in titulo_up:
        return "Síntese Laya S1: Termo de Declarações prestado pela ofendida na Polícia Civil relatando a dinâmica fática."
    if "TERMO DE DEPOIMENTO POLICIAL" in titulo_up:
        return f"Síntese Laya S1: {titulo.split('(')[0].strip()} colhido na fase inquisitorial."
    if "AUTO DE PRISÃO EM FLAGRANTE" in titulo_up or "LAUDOS PERICIAIS" in titulo_up:
        laudos = sorted(set(re.findall(r"\bUN-[A-Z0-9-]+\b", up)))
        bos = re.findall(r"BOLETIM\s+DE\s+OCORR[ÊE]NCIA\s*(?:N[º°\.]?\s*)?([\d/\.-]+)", norm_up)
        extra_laudo = f" e Laudo(s) Pericial(is) {', '.join(laudos[:3])}" if laudos else ""
        extra_bo = f" (BO nº {bos[0]})" if bos else ""
        return (
            f"Síntese Laya S1: Auto de Prisão em Flagrante{extra_bo}, Termos de Depoimento/Declarações, "
            f"Interrogatório Policial{extra_laudo}."
        )
    if "AUDIÊNCIA DE CUSTÓDIA" in titulo_up:
        return (
            "Síntese Laya S1: Termo de Audiência de Custódia com deliberação sobre homologação do flagrante, "
            "liberdade provisória e Medidas Protetivas de Urgência (art. 22 da Lei nº 11.340/2006)."
        )
    if "ALVARÁ DE SOLTURA" in titulo_up:
        return "Síntese Laya S1: Alvará de Soltura / Termo de Liberdade Provisória expedido em favor do autuado."
    if "REVOGAÇÃO DE MEDIDAS PROTETIVAS" in titulo_up or "SOBRE MEDIDAS PROTETIVAS" in titulo_up:
        return (
            "Síntese Laya S1: Manifestação ou decisão sobre Medidas Protetivas de Urgência (art. 22 da Lei nº 11.340/2006), "
            "sem prejuízo do regular prosseguimento da ação penal."
        )
    if "SANEAMENTO (ART. 397" in titulo_up:
        return (
            "Síntese Laya S1: Decisão interlocutória que afastou as hipóteses de absolvição sumária (art. 397 do CPP) "
            "e designou Audiência de Instrução e Julgamento."
        )
    if "DECURSO DE PRAZO" in titulo_up:
        return "Síntese Laya S1: Certidão cartorária automática de decurso de prazo processual."
    if "ENDEREÇO ATUALIZADO" in titulo_up:
        m_cid = re.search(r"([A-ZÀ-Úa-zà-ú\s]{3,25}/[A-Z]{2})", conteudo)
        cid_txt = f" ({m_cid.group(1).strip()})" if m_cid else ""
        return f"Síntese Laya S1: Certidão e comprovante de endereço atualizado da parte nos autos{cid_txt}."
    if "JUNTADA DE MÍDIAS" in titulo_up:
        return "Síntese Laya S1: Certidão cartorária de juntada das mídias audiovisuais da instrução no PJe Mídias."
    if "JUÍZO 100% DIGITAL" in titulo_up:
        return "Síntese Laya S1: Certidão de intimação e termo de adesão ao Juízo 100% Digital."
    if "HABILITAÇÃO" in titulo_up:
        return "Síntese Laya S1: Requerimento/termo de habilitação da Defensoria Pública ou assistência qualificada nos autos."
    if cat == "DENUNCIA":
        arts = sorted(set(re.findall(r"art(?:igo)?s?\.?\s*\d+(?:-[A-Z])?(?:\s*,\s*§\s*\d+[º°]?)?", conteudo, flags=re.IGNORECASE)))
        arts_str = f" ({', '.join(arts[:4])})" if arts else ""
        tem_dano = "dano moral" in conteudo.lower() or "danos morais" in conteudo.lower() or "387" in conteudo.lower()
        dano_str = ", com pedido expresso de reparação mínima (art. 387, IV, CPP)" if tem_dano else ""
        return f"Síntese Laya S1: Denúncia ministerial em contexto de violência doméstica (Lei nº 11.340/2006){arts_str}{dano_str}."
    if cat == "RECEBIMENTO_DENUNCIA":
        if "DECADÊNCIA" in norm_up:
            return "Síntese Laya S1: Decisão interlocutória de recebimento da denúncia (marco interruptivo — art. 117, I, CP) e declaração parcial de decadência."
        return "Síntese Laya S1: Decisão judicial de recebimento da denúncia (art. 396 CPP — marco interruptivo art. 117, I, CP) e citação do acusado."
    if cat == "RESPOSTA_ACUSACAO":
        return "Síntese Laya S1: Resposta à Acusação (arts. 396 e 396-A do CPP) apresentada pela Defesa Técnica."
    if cat == "TERMO_AUDIENCIA":
        partes_ata = []
        if "VÍTIMA" in norm_up or "OFENDIDA" in norm_up:
            partes_ata.append("oitiva da ofendida")
        if "TESTEMUNHA" in norm_up:
            partes_ata.append("depoimentos testemunhais")
        if "INTERROGATÓRIO" in norm_up:
            partes_ata.append("interrogatório do acusado")
        if "REVELIA" in norm_up:
            partes_ata.append("decretação de revelia (art. 367 CPP)")
        if "ALEGAÇÕES FINAIS" in norm_up:
            partes_ata.append("alegações finais orais do MP")
        resumo_ata = ", ".join(partes_ata) if partes_ata else "colheita da prova oral em contraditório"
        return f"Síntese Laya S1: Termo de Audiência de Instrução e Julgamento registrando {resumo_ata}."
    if cat == "CAC_FAC":
        nada = "NADA CONSTAR" in norm_up or "NADA EXISTIR" in norm_up
        return (
            "Síntese Laya S1: Certidão Estadual de Antecedentes Criminais atestando NADA CONSTAR (primariedade técnica — Súmula 444/STJ)."
            if nada
            else "Síntese Laya S1: Certidão Estadual de Antecedentes Criminais juntada aos autos (análise sob a Súmula 444/STJ)."
        )
    if cat == "ALEGACOES_FINAIS_MP":
        pedidos_mp_peca = extrair_pedidos_mp_texto(conteudo, fonte_id=doc_id, origem="memoriais")
        if pedidos_mp_peca:
            descricoes = "; ".join(p.descricao for p in pedidos_mp_peca[:5])
            return f"Síntese Laya S1: Memoriais finais do Ministério Público requerendo: {descricoes}."
        return "Síntese Laya S1: Memoriais finais do Ministério Público — pedidos não identificados automaticamente: conferir a peça."
    if cat == "ALEGACOES_FINAIS_DEFESA":
        pedidos_def_peca = extrair_pedidos_subsidiarios_defesa_texto(conteudo, fonte_id=doc_id, origem="memoriais")
        pontos = []
        if re.search(r"\babsolvi[çc][aã]o\b", conteudo, flags=re.IGNORECASE):
            pontos.append("absolvição")
        for pd in pedidos_def_peca:
            if pd.descricao not in pontos:
                pontos.append(pd.descricao)
        if pontos:
            return f"Síntese Laya S1: Memoriais finais da Defesa Técnica postulando: {'; '.join(pontos[:6])}."
        return "Síntese Laya S1: Memoriais finais da Defesa Técnica — pedidos não identificados automaticamente: conferir a peça."
    if cat == "CERTIDAO_INTIMACAO_MPU":
        return "Síntese Laya S1: Certidão de Oficial de Justiça certificando diligências de citação/intimação e verificação de contatos/endereços."
    if "NÃO FOI ENCONTRADO AS IMAGENS" in norm_up or "ORDEM DE MISSÃO POLICIAL" in norm_up:
        return "Síntese Laya S1: Certidão e Relatório de Missão Policial da DEAM sobre diligências complementares na investigação."
    return "Síntese Laya S1: Peça informativa do procedimento policial/investigativo (BO, Termos de Declarações, Laudos ou Relatório Final)."


# ------------------------------------------------------------------------------
# Nomes de pessoas (réu/ofendida) em textos de PDF com quebras de linha no meio do nome
# ------------------------------------------------------------------------------

#: Trecho bruto após um rótulo: até 80 caracteres na mesma linha e, se houver, mais uma linha (nome quebrado).
_TRECHO_NOME = r"[A-ZÁÉÍÓÚÂÊÔÃÕÇÀÜ][^\n,;]{0,80}(?:\n[^\n,;]{0,60})?"

_PARTICULAS_NOME = {"de", "da", "do", "dos", "das", "e", "d'"}

#: Palavras que encerram um nome: qualificação, rótulos do processo e nomes de instituições.
_PARADA_NOME = {
    "brasileiro", "brasileira", "estrangeiro", "estrangeira", "solteiro", "solteira", "casado", "casada",
    "divorciado", "divorciada", "viuvo", "viuva", "convivente", "natural", "nascido", "nascida", "filho", "filha",
    "portador", "portadora", "inscrito", "inscrita", "cpf", "rg", "residente", "domiciliado", "domiciliada",
    "qualificado", "qualificada", "vulgo", "alcunha", "conhecido", "conhecida", "menor", "maior", "idade", "anos",
    "vitima", "ofendida", "ofendido", "delito", "autor", "autora", "advogado", "advogada", "defensor", "defensora",
    "reu", "re", "denunciado", "denunciada", "acusado", "acusada", "investigado", "investigada", "indiciado",
    "indiciada", "com", "pela", "pelo", "que", "por", "pelas", "pelos", "no", "na", "nos", "nas", "em", "ao", "a",
    "o", "as", "os", "sentenca", "processo", "classe", "assunto", "tribunal", "justica", "poder", "judiciario",
    "estado", "ministerio", "publico", "promotoria", "delegacia", "juizado", "vara", "comarca", "policia",
    "secretaria", "pje", "assinado", "documento", "numero", "pagina", "fls", "id", "procedimento", "acao",
    "penal", "violencia", "domestica", "lei", "art", "artigo",
}


def _limpar_nome_pessoa(bruto: str) -> str:
    """Do trecho bruto, mantém só a sequência inicial de palavras que formam um nome (em maiúsculas)."""
    nome: List[str] = []
    for tok in re.split(r"\s+", (bruto or "").strip()):
        t = tok.strip(",.;:()[]\"'“”")
        if not t:
            break
        base = normalizar(t)
        if base in _PARADA_NOME:
            break
        if base in _PARTICULAS_NOME:
            if nome:
                nome.append(t)
            continue
        if not re.match(r"^[A-ZÁÉÍÓÚÂÊÔÃÕÇÀÜ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]*$", t):
            break
        nome.append(t)
        fim = tok.rstrip()
        if fim.endswith((",", ";", ":")) or (fim.endswith(".") and len(t) > 1):
            break  # pontuação encerra o nome; inicial abreviada ("A.") não
    while nome and normalizar(nome[-1]) in _PARTICULAS_NOME:
        nome.pop()
    if not nome or len(" ".join(nome)) < 3:
        return ""
    return re.sub(r"\s+", " ", " ".join(nome)).strip().upper()


def _escolher_nome_mais_completo(candidatos: List[str]) -> str:
    """Prefere o candidato com mais palavras (um nome quebrado no PDF rende candidatos truncados)."""
    validos = [c for c in candidatos if c]
    if not validos:
        return ""
    return max(validos, key=lambda c: (len(c.split()), len(c)))


def _nomes_compativeis(a: str, b: str) -> bool:
    """Um é o começo do outro, palavra por palavra (o mesmo nome, só mais ou menos completo)."""
    pa, pb = a.split(), b.split()
    curto, longo = (pa, pb) if len(pa) <= len(pb) else (pb, pa)
    return longo[: len(curto)] == curto


def _resolver_nome(candidatos: List[str]) -> Tuple[str, bool]:
    """(nome, há_conflito). Os candidatos vêm em ordem de prioridade da fonte (denúncia antes da capa etc.).

    Só completa o nome com candidatos compatíveis com o de maior prioridade; um nome diferente (mesmo mais longo)
    nunca o substitui: vira conflito para o juízo conferir (revisão cruzada do CODEX na TASK-046).
    """
    validos = [c for c in candidatos if c]
    if not validos:
        return "", False
    base = validos[0]
    compativeis = [c for c in validos if _nomes_compativeis(base, c)]
    escolhido = _escolher_nome_mais_completo(compativeis)
    conflito = any(not _nomes_compativeis(escolhido, c) for c in validos)
    return escolhido, conflito


def _completar_nome_pela_qualificacao(prenome: str, texto: str) -> str:
    """Se só o prenome foi achado, procura o nome completo na qualificação (seguido de "brasileiro", "CPF" etc.)."""
    pat = re.compile(
        re.escape(prenome) + r"[ \t]*\n?[ \t]*((?:[A-ZÁÉÍÓÚÂÊÔÃÕÇÀÜ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+[ \t]*\n?[ \t]*){1,8}?)"
        r"[ \t]*,?[ \t]*(?:brasileir|portador|inscrit|nascid|filh|qualificad|natural|vulgo|CPF|RG)",
        flags=re.IGNORECASE,
    )
    melhores: List[str] = []
    for m in pat.finditer(texto or ""):
        cand = _limpar_nome_pessoa(f"{prenome} {m.group(1)}")
        if len(cand.split()) >= 2:
            melhores.append(cand)
    return _escolher_nome_mais_completo(melhores)


def _extrair_entidades_peca(conteudo: str, doc_id: str, data_peca: str, is_demo: bool = False) -> List[Dict[str, str]]:
    entidades: List[Dict[str, str]] = [{"tipo": "ID", "valor": doc_id}]
    if data_peca:
        entidades.append({"tipo": "DATA", "valor": data_peca})
    for m in re.findall(r"\b\d{2}/\d{2}/\d{4}\b", conteudo):
        if not any(e["valor"] == m for e in entidades):
            entidades.append({"tipo": "DATA", "valor": m})

    candidate_names: List[str] = []
    if is_demo:
        candidate_names.extend([
            "MARIANA ALVES FERREIRA",
            "CARLOS EDUARDO ROCHA MENDES",
            "ROBERTO SILVA",
            "JOÃO BATISTA SOARES",
        ])

    for pat in (
        r"(?:V[íi]tima|Ofendida|Denunciado|Acusado|R[ée]u|DECLARANTE|AUTUADO|Conduzido)\s*:\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-zÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç\s]{5,55}?)(?:\n|,|\.|\(|CPF|RG|Id|Delito)",
    ):
        for found_name in re.findall(pat, conteudo):
            clean_n = re.sub(r"\s+", " ", found_name).strip().upper()
            if (
                len(clean_n) >= 6
                and "SEGREDO" not in clean_n
                and not re.match(r"^[A-Z]\.\s", clean_n)
                and clean_n not in candidate_names
            ):
                candidate_names.append(clean_n)

    conteudo_norm = normalizar(conteudo)
    for nome in candidate_names:
        if normalizar(nome) in conteudo_norm:
            if not any(e["valor"] == nome.upper() for e in entidades):
                entidades.append({"tipo": "PARTE", "valor": nome.upper()})

    for ref in re.findall(
        r"(?:ID[-\s]?\d+|UN-[A-Z0-9-]+|\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}|art\.\s*\d+[º°]?(?:-[A-Z])?(?:,\s*§\s*\d+[º°]?)?|Súmula\s*\d+/STJ|Tema\s*\d+/STJ|Resolução\s*CNJ\s*nº\s*492/2023|R\$\s*[\d\.,]+)",
        conteudo,
        flags=re.IGNORECASE,
    ):
        val = ref.strip()
        if not any(e["valor"].lower() == val.lower() for e in entidades):
            entidades.append({"tipo": "NORMA_REF", "valor": val})
    return entidades[:12]


_RE_CODIGO_LAUDO = re.compile(r"\bun-[a-z0-9]{3,}-\d{3,}\b")


def classificar_pecas_autos(raw_docs: List[Dict[str, Any]], is_demo: bool = False) -> List[PecaClassificada]:
    """
    Motor System 1 (Laya Decision Engine): classifica cada peça processual por regras determinísticas,
    atribuindo pontuações por categoria e um resumo estruturado sem alucinação.

    ``is_demo=True`` (explícito, vindo de ``bundle["is_demo"]``) habilita as sínteses fixas do caso
    demonstrativo; nunca é inferido pelo prefixo do ID.
    """
    pecas: List[PecaClassificada] = []
    for raw in raw_docs:
        titulo = str(raw.get("titulo") or "")
        conteudo = str(raw.get("conteudo") or "")
        doc_id = str(raw.get("doc_id") or "ID-0000")
        data_peca = str(raw.get("data_peca") or "")
        texto_full = f"{titulo} {conteudo}".lower()
        titulo_low = titulo.lower()

        scores: Dict[str, float] = {}
        for cat, keywords in REGRAS_LAYA_SYSTEM1.items():
            hits = sum(1 for kw in keywords if kw in texto_full)
            scores[cat] = round(min(0.99, 0.15 + (hits * 0.24)), 3)

        def _pin(target_cat: str, val: float = 0.99) -> None:
            for k in scores:
                scores[k] = min(scores[k], 0.65)
            scores[target_cat] = val

        # Prioriza sinal forte do título já validado pelo segmentador PJe
        if "laudo de perícia" in titulo_low or "exame de corpo de delito" in titulo_low or "atestado odontológico" in titulo_low:
            _pin("LAUDO_PERICIAL", 0.99)
        elif "denúncia ministerial" in titulo_low:
            _pin("DENUNCIA", 0.99)
        elif "recebimento da denúncia" in titulo_low or "saneamento (art. 397" in titulo_low:
            _pin("RECEBIMENTO_DENUNCIA", 0.99)
        elif "resposta à acusação" in titulo_low:
            _pin("RESPOSTA_ACUSACAO", 0.99)
        elif (
            "oficial de justiça" in titulo_low
            or "mandado de citação" in titulo_low
            or "intimação pessoal" in titulo_low
            or "certidão positiva" in titulo_low
            or "endereço atualizado" in titulo_low
            or "juízo 100% digital" in titulo_low
            or "habilitação" in titulo_low
        ):
            _pin("CERTIDAO_INTIMACAO_MPU", 0.97)
        elif (
            "audiência de custódia" in titulo_low
            or "revogação de medidas protetivas" in titulo_low
            or "sobre medidas protetivas" in titulo_low
            or ("decisão" in titulo_low and "medidas protetivas" in titulo_low)
        ):
            _pin("DECISAO_MPU", 0.98)
        elif "termo de audiência" in titulo_low or "ata da audiência" in titulo_low or "juntada de mídias" in titulo_low:
            _pin("TERMO_AUDIENCIA", 0.99)
        elif "antecedentes criminais" in titulo_low:
            _pin("CAC_FAC", 0.99)
        elif (
            "memoriais do ministério público" in titulo_low
            or "alegações finais do ministério público" in titulo_low
            or "alegações finais (mp)" in titulo_low
        ):
            _pin("ALEGACOES_FINAIS_MP", 0.99)
        elif "memoriais da defesa" in titulo_low or "alegações finais da defesa" in titulo_low:
            _pin("ALEGACOES_FINAIS_DEFESA", 0.99)
        elif (
            "auto de prisão em flagrante" in titulo_low
            or "inquérito policial" in titulo_low
            or "diligências policiais" in titulo_low
            or "capa e autuação" in titulo_low
        ):
            _pin("BO", 0.98)
            if "laudos periciais" in titulo_low or _RE_CODIGO_LAUDO.search(texto_full) or "exame de lesão corporal" in texto_full:
                scores["LAUDO_PERICIAL"] = 0.95

        melhor_cat = max(scores, key=lambda k: scores[k])
        # Confiança = pontuação real da regra vencedora (sem piso artificial).
        conf = scores[melhor_cat]

        resumo = _gerar_sintese_dinamica_peca(melhor_cat, titulo, conteudo, doc_id, is_demo=is_demo)
        entidades = _extrair_entidades_peca(conteudo, doc_id, data_peca, is_demo=is_demo)
        pecas.append(
            PecaClassificada(
                doc_id=doc_id,
                folha=str(raw.get("folha", "fl. s/n")),
                titulo=titulo,
                data_peca=data_peca,
                fase=str(raw.get("fase", "JUDICIAL")),
                categoria=melhor_cat,
                laya_confidence=conf,
                laya_scores=scores,
                ocr_aplicado=bool(raw.get("ocr_aplicado", False)),
                resumo_estruturado=resumo,
                entidades_extraidas=entidades,
                conteudo=conteudo,
            )
        )
    return pecas


def _format_cnj_number(raw_digits_or_cnj: str) -> str:
    s = raw_digits_or_cnj.strip()
    if re.match(r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$", s):
        return s
    digits = re.sub(r"\D", "", s)
    if len(digits) == 20:
        return f"{digits[:7]}-{digits[7:9]}.{digits[9:13]}.{digits[13:14]}.{digits[14:16]}.{digits[16:20]}"
    return s


_MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3,
    "abril": 4, "maio": 5, "junho": 6, "julho": 7,
    "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}


def _data_iso_valida(ano: int, mes: int, dia: int) -> str:
    try:
        return datetime.date(ano, mes, dia).isoformat()
    except ValueError:
        return ""


def _parse_data_fato_dinamica(
    peca_bo: PecaClassificada | None,
    peca_den: PecaClassificada | None,
    texto_unificado: str,
    alertas: Optional[List[str]] = None,
) -> str:
    """Data do fato em ISO ou ``""`` (com alerta) — nunca uma data padrão."""
    alertas = alertas if alertas is not None else []
    # 1. Data/Hora explícita em Boletim de Ocorrência / APF
    bo_txt = peca_bo.conteudo if peca_bo else texto_unificado
    for pat in (
        r"Data/Hora do Fato In[íi]cio:\s*(\d{2})/(\d{2})/((?:19|20)\d{2})",
        r"ocorrido(?:a)?\s+(?:no\s+dia|em)\s+(\d{2})/(\d{2})/((?:19|20)\d{2})",
        r"DATA DO FATO:\s*(\d{2})/(\d{2})/((?:19|20)\d{2})",
    ):
        m = re.search(pat, bo_txt, flags=re.IGNORECASE)
        if m:
            data = _data_iso_valida(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            if data:
                return data

    # 2. Data por extenso na Denúncia (ex.: "no dia 16 de fevereiro de 2024")
    den_txt = peca_den.conteudo if peca_den else texto_unificado
    m_ext = re.search(
        r"(?:no\s+dia|em)\s+(\d{1,2})\s+de\s+(janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s+de\s+((?:19|20)\d{2})",
        den_txt,
        flags=re.IGNORECASE,
    )
    if m_ext:
        mes = _MESES_PT.get(m_ext.group(2).lower())
        if mes:
            data = _data_iso_valida(int(m_ext.group(3)), mes, int(m_ext.group(1)))
            if data:
                return data

    # 2b. Data numérica na denúncia (ex.: "No dia 14/02/2025, às 21h30min")
    m_num = re.search(r"\bno\s+dia\s+(\d{2})/(\d{2})/((?:19|20)\d{2})", den_txt, flags=re.IGNORECASE)
    if m_num:
        data = _data_iso_valida(int(m_num.group(3)), int(m_num.group(2)), int(m_num.group(1)))
        if data:
            return data

    # 3. Mês/ano sem dia preciso (ex.: "desde junho de 2022"): NÃO inventa o dia.
    m_mes_ano = re.search(
        r"(?:em|desde|m[êe]s\s+de)\s+(janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s+de\s+((?:19|20)\d{2})",
        den_txt,
        flags=re.IGNORECASE,
    )
    if m_mes_ano:
        alertas.append(
            f"Data do fato não extraída: a peça acusatória indica apenas '{m_mes_ano.group(1)} de {m_mes_ano.group(2)}' "
            "(sem o dia). Informe a data exata (metadados_override.data_fato)."
        )
        return ""

    # 4. Data do registro policial: inferência (não é necessariamente a data do fato).
    if peca_bo and peca_bo.data_peca:
        alertas.append(
            f"Data do fato inferida da data do registro policial ({peca_bo.doc_id}: {peca_bo.data_peca}); "
            "confirme ou corrija (metadados_override.data_fato)."
        )
        return peca_bo.data_peca
    alertas.append("Data do fato não extraída dos autos. Informe-a (metadados_override.data_fato).")
    return ""


def _detectar_horario_diurno_bo(pecas: List[PecaClassificada]) -> tuple[bool, str]:
    """Verifica se os registros policiais/laudos atestam período diurno (06h-17h59) e extrai o horário real."""
    bo_texts = "\n".join(
        p.conteudo for p in pecas if p.categoria in ("BO", "LAUDO_PERICIAL") and p.doc_id != "ID-CAPA-PJE"
    )
    for pat in (
        r"por\s+volta\s+das\s+(0?[6-9]|1[0-7])\s*[h:]\s*(\d{2})",
        r"Data/Hora do Fato In[íi]cio:\s*\d{2}/\d{2}/\d{4}\s+(0?[6-9]|1[0-7]):(\d{2})",
        r"HORA\s*DO\s*FATO:\s*(0?[6-9]|1[0-7]):(\d{2})",
    ):
        m = re.search(pat, bo_texts, flags=re.IGNORECASE)
        if m:
            return True, f"{int(m.group(1)):02d}h{m.group(2)}min"
    return False, ""


def _extrair_crimes_dinamicamente(
    pecas: List[PecaClassificada],
    peca_den: PecaClassificada | None,
    peca_rec: PecaClassificada | None,
    peca_mp_af: PecaClassificada | None,
) -> tuple[List[str], bool, str]:
    """
    Extrai os tipos penais imputados na peça acusatória (inclusive listas sequenciais como
    'artigos 129, § 13, 150, § 1º e 147, caput') sem depender de nomes fixos de processos.
    Retorna (crimes_codes, emendatio_art150_sugerida, horario_diurno_bo). Lista vazia quando nada é
    identificado (nunca um crime padrão).
    """
    trechos_imput: List[str] = []
    for p in (peca_den, peca_rec, peca_mp_af):
        if not p or not p.conteudo:
            continue
        p_norm = re.sub(r"\s+", " ", p.conteudo)
        # Captura frases de tipificação específicas sem truncar em abreviações como 'art.', 'arts.', 'n.', 'p.ú.'
        for m in re.finditer(
            r"(?:tipificam\s+os\s+crimes(?:(?!\.\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ])[^\n]){0,240}|"
            r"incurs[oa]\s+nas\s+(?:san[çc][õo]es|penas)(?:(?!\.\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ])[^\n]){0,240}|"
            r"previstos?\s+(?:respectivamente\s+)?nos\s+art(?:igos|\.)(?:(?!\.\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ])[^\n]){0,240}|"
            r"infração\(ões\)\s+tipificada\(s\)(?:(?!\.\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ])[^\n]){0,240}|"
            r"imputa(?:(?!\.\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ])[^\n]){0,200}art(?:(?!\.\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ])[^\n]){0,200}|"
            r"Delito\(s\):[^\n]{0,160})",
            p_norm,
            flags=re.IGNORECASE,
        ):
            trechos_imput.append(m.group(0))

    imput_txt = " \n ".join(trechos_imput).lower()
    if not imput_txt.strip():
        imput_txt = re.sub(
            r"\s+",
            " ",
            f"{peca_den.conteudo if peca_den else ''} {peca_rec.conteudo if peca_rec else ''} {peca_mp_af.conteudo if peca_mp_af else ''}",
        ).lower()

    candidatos = localizar_tipos_penais(imput_txt)

    crimes_ordenados: List[str] = []
    for _, code in sorted(candidatos, key=lambda x: x[0]):
        if code not in crimes_ordenados:
            # Evita incluir art. 147 simples como subproduto de '147-A' / '147-B' caso já não citado separadamente
            if code == "art_147_cp" and not re.search(r"(?:^|[^\w-])147(?!\s*-[ab])\b", imput_txt):
                continue
            crimes_ordenados.append(code)

    # Sinaliza possível emendatio libelli (art. 383 CPP) para art. 150, § 1º -> caput em fato diurno.
    # A imputação NÃO é alterada aqui: a readequação típica é decisão do juiz.
    houve_emendatio_150 = False
    diurno_bo, hora_bo = _detectar_horario_diurno_bo(pecas)
    if "art_150_p1_cp" in crimes_ordenados and diurno_bo:
        den_low = peca_den.conteudo.lower() if peca_den else ""
        sem_arma_ou_concurso = ("emprego de arma" not in den_low and "duas ou mais pessoas" not in den_low)
        if sem_arma_ou_concurso:
            houve_emendatio_150 = True

    # Sem imputação identificada: lista vazia (o chamador alerta) — nunca um crime padrão.
    return crimes_ordenados, houve_emendatio_150, hora_bo


def extrair_metadados_processo(
    pecas: List[PecaClassificada],
    is_demo: bool = False,
    transcricoes: Optional[List[Dict[str, Any]]] = None,
) -> MetadadosProcesso:
    """
    Extração determinística (Regex + Regras System 1) dos dados objetivos do processo.

    ``is_demo=True`` (explícito) devolve os dados fixos do caso demonstrativo. Para processos reais,
    o que não for encontrado fica vazio e é listado em ``alertas_extracao``.
    """
    from app.generation.adversarial_critic import extrair_teses_defensivas

    alertas: List[str] = []
    texto_unificado = "\n".join(f"[{p.doc_id}] {p.titulo}: {p.conteudo}" for p in pecas)
    texto_low = texto_unificado.lower()

    peca_capa = next((p for p in pecas if p.doc_id == "ID-CAPA-PJE"), None)
    peca_den = next((p for p in pecas if p.categoria == "DENUNCIA"), None)
    peca_bo = next((p for p in pecas if p.categoria == "BO" and p.doc_id != "ID-CAPA-PJE"), None) or next(
        (p for p in pecas if p.categoria == "BO"), None
    )
    peca_rec = next(
        (p for p in pecas if p.categoria == "RECEBIMENTO_DENUNCIA" and "saneamento" not in p.titulo.lower()),
        None,
    ) or next((p for p in pecas if p.categoria == "RECEBIMENTO_DENUNCIA"), None)
    peca_int = next((p for p in pecas if p.categoria == "CERTIDAO_INTIMACAO_MPU"), None)
    peca_mpu = next(
        (p for p in pecas if p.categoria == "DECISAO_MPU" and "revoga" not in p.titulo.lower()),
        None,
    )
    peca_rev_mpu = next(
        (p for p in pecas if p.categoria == "DECISAO_MPU" and "revoga" in p.titulo.lower()),
        None,
    )
    peca_cac = next((p for p in pecas if p.categoria == "CAC_FAC"), None)
    peca_mp_af = next((p for p in pecas if p.categoria == "ALEGACOES_FINAIS_MP"), None)
    peca_def_af = next((p for p in pecas if p.categoria == "ALEGACOES_FINAIS_DEFESA"), None)

    # 1. Número CNJ do Processo Principal
    source_for_num = (
        peca_capa.conteudo
        if peca_capa
        else (peca_den.conteudo if peca_den else texto_unificado)
    )
    m_proc = re.search(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b", source_for_num)
    if m_proc:
        numero_processo = m_proc.group(0)
    elif is_demo:
        numero_processo = "0804590-12.2025.8.00.0001"
    else:
        numero_processo = ""
        alertas.append("Número do processo (CNJ) não extraído dos autos.")

    # 2. Autos de Medida Protetiva / Processo Referência em Apenso
    autos_mpu_apenso = "0801122-44.2025.8.00.0001" if is_demo else "Sem apenso ativo"
    m_ref = re.search(r"Processo refer[êe]ncia:\s*(\d[\d\.\-]{18,24})", texto_unificado, flags=re.IGNORECASE)
    if m_ref:
        autos_mpu_apenso = _format_cnj_number(m_ref.group(1))
    else:
        m_ap = re.search(
            r"(?:autos|processo)\s*(?:em apenso\s*)?(?:n[º°]\s*)?(\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})",
            texto_unificado,
            flags=re.IGNORECASE,
        )
        if m_ap and m_ap.group(1) != numero_processo:
            autos_mpu_apenso = m_ap.group(1)
        elif peca_mpu and peca_rev_mpu:
            autos_mpu_apenso = f"Medidas Protetivas concedidas no {peca_mpu.doc_id} e revogadas no {peca_rev_mpu.doc_id}"
        elif peca_mpu:
            autos_mpu_apenso = f"Medidas Protetivas apreciadas no {peca_mpu.doc_id}"

    # 3. Nome da Ofendida / Vítima (prioriza Denúncia e Alegações Finais sobre quebras de linha de OCR)
    nome_ofendida = "MARIANA ALVES FERREIRA" if is_demo else ""
    source_nomes = (
        (peca_den.conteudo + "\n" if peca_den else "")
        + (peca_mp_af.conteudo + "\n" if peca_mp_af else "")
        + texto_unificado
    )
    # Fontes em ordem de prioridade (denúncia, memoriais do MP, demais peças): a ORIGEM decide a prioridade, não o
    # padrão de captura; todas as ocorrências de cada peça entram, para que divergências virem alerta
    # (revisão cruzada do CODEX na TASK-071).
    fontes_nomes: List[str] = [p.conteudo for p in (peca_den, peca_mp_af) if p is not None]
    fontes_nomes += [p.conteudo for p in pecas if p is not peca_den and p is not peca_mp_af]
    if not fontes_nomes:
        fontes_nomes = [texto_unificado]

    def _candidatos_por_fonte(padroes: Tuple[str, ...], filtro) -> List[str]:
        achados: List[str] = []
        for texto_fonte in fontes_nomes:
            for padrao in padroes:
                for m_n in re.finditer(padrao, texto_fonte or ""):
                    cand_n = filtro(m_n.group(1))
                    if cand_n and (not achados or achados[-1] != cand_n):
                        achados.append(cand_n)
        return achados

    def _filtro_vitima(bruto: str) -> str:
        if "SEGREDO" in bruto.upper() or re.match(r"^[A-Z]\.\s", bruto.strip()):
            return ""
        cand_v = _limpar_nome_pessoa(bruto)
        return cand_v if len(cand_v) > 6 else ""

    candidatos_vit: List[str] = [] if is_demo else _candidatos_por_fonte(
        (
            r"(?i:v[íi]tima)\s*:\s*(" + _TRECHO_NOME + r")",
            r"(?i:ofendida)\s*:\s*(" + _TRECHO_NOME + r")",
            r"(?:sua|da\s+sua)\s+ex\s*-?\s*companheira,?\s*(" + _TRECHO_NOME + r")",
            r"V[ÍI]TIMA\(S\)\s*:\s*(" + _TRECHO_NOME + r")",
        ),
        _filtro_vitima,
    )
    if candidatos_vit:
        nome_ofendida, conflito_vit = _resolver_nome(candidatos_vit)
        if conflito_vit:
            alertas.append("Nome da ofendida diverge entre as peças dos autos: confira antes de gerar a sentença.")
    if not nome_ofendida:
        alertas.append("Nome da ofendida não extraído dos autos.")

    # 4. Nome do Réu / Denunciado
    # Os PDFs do PJe quebram linhas no meio do nome ("TICIO\nFICTICIO DA SILVA"): o trecho capturado pode atravessar
    # uma quebra de linha, e _limpar_nome_pessoa corta na primeira palavra que não é nome (qualificação, rótulo).
    nome_reu = "CARLOS EDUARDO ROCHA MENDES" if is_demo else ""
    if not is_demo:
        def _filtro_reu(bruto: str) -> str:
            cand_r = _limpar_nome_pessoa(bruto)
            return cand_r if cand_r and "MINIST" not in cand_r else ""

        candidatos_reu = _candidatos_por_fonte(
            (
                r"DEN[ÚU]NCIA\s+(?:contra|em\s+desfavor\s+de)\s+(" + _TRECHO_NOME + r")",
                r"[Ee]m\s+desfavor\s+de\s+(" + _TRECHO_NOME + r")",
                r"(?i:denunciad[oa])\s*:\s*(" + _TRECHO_NOME + r")",
                r"(?i:acusad[oa])\s*:\s*(" + _TRECHO_NOME + r")",
                r"(?i:r[ée]u(?:\(s\))?)\s*:\s*(" + _TRECHO_NOME + r")",
                r"(?i:polo\s+passivo)\s*:?\s*(" + _TRECHO_NOME + r")",
                r"([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ \t]{5,60}?)\s*\(R[ÉE]U\)",
            ),
            _filtro_reu,
        )
        nome_reu, conflito_reu = _resolver_nome(candidatos_reu)
        if conflito_reu:
            alertas.append("Nome do acusado diverge entre as peças dos autos: confira antes de gerar a sentença.")
        if nome_reu and len(nome_reu.split()) == 1:
            completo = _completar_nome_pela_qualificacao(nome_reu, source_nomes)
            if completo:
                nome_reu = completo
                alertas.append("Nome do acusado completado a partir da qualificação nos autos: confira.")
            else:
                alertas.append("Nome do acusado incompleto (só um nome encontrado): confira e complete.")
    if not nome_reu:
        alertas.append("Nome do acusado não extraído dos autos.")

    # 5. Vínculo doméstico e familiar (extraído dinamicamente da peça acusatória/BO)
    if is_demo:
        vinculo_domestico = "Ex-companheiro (união estável por 6 anos, com uma filha comum de 5 anos - art. 5º, III, da Lei nº 11.340/2006)"
    else:
        den_clean = re.sub(r"\s+", " ", peca_den.conteudo if peca_den else texto_unificado)
        m_vinc = re.search(
            r"(conviv(?:eram|ência)\s+(?:maritalmente|em\s+uni[ãa]o\s+est[áa]vel)\s+por\s+(?:cerca\s+de|aproximadamente)?\s*[^\.;]{5,180}?)(?:\s*,\s*e\s+na\s+ocasi[ãa]o|\s*,\s*sem\s+filhos|\s*,\s*com\s+filh|\.|;)",
            den_clean,
            flags=re.IGNORECASE,
        )
        if not m_vinc:
            m_vinc = re.search(
                r"(conviv(?:eram|ência)\s+(?:maritalmente|em\s+uni[ãa]o\s+est[áa]vel)\s+por\s+(?:cerca\s+de|aproximadamente)?\s*[^\.]{5,140})",
                den_clean,
                flags=re.IGNORECASE,
            )
        if m_vinc:
            det_vinc = m_vinc.group(1).strip().rstrip(".,;")
            if det_vinc.count("(") > det_vinc.count(")"):
                det_vinc = det_vinc + ")"
            vinculo_domestico = f"Ex-companheiro — {det_vinc} (art. 5º, III, da Lei nº 11.340/2006)"
        elif "ex-companheir" in texto_low:
            vinculo_domestico = "Ex-companheiro em contexto de relação íntima de afeto (art. 5º, III, da Lei nº 11.340/2006)"
        elif "união estável" in texto_low:
            vinculo_domestico = "Ex-companheiro / união estável (art. 5º, III, da Lei nº 11.340/2006)"
        elif re.search(
            r"\b(?:filh[oa]|genitor[a]?|m[ãa]e|pai|irm[ãa]o?|neto|neta|av[ôó]|sobrinh[oa]|padrasto|madrasta|entead[oa]|cunhad[oa]|sogr[oa]|genro|nora)\b"
            r"[^.]{0,40}\b(?:da|d[ao]s?)\s+(?:v[ií]tima|ofendida)|(?:agrediu|ameaçou|ameacou)\s+(?:a\s+)?sua\s+(?:genitora|m[ãa]e|irm[ãa]|av[ôó]|filha)",
            den_clean,
            flags=re.IGNORECASE,
        ):
            vinculo_domestico = "Vínculo familiar com a ofendida, consoante a denúncia (art. 5º, II, da Lei nº 11.340/2006)"
        else:
            # Sem base nos autos: NÃO se presume relação íntima de afeto (art. 5º, III). O juízo informa o
            # vínculo e o inciso aplicável (metadados_override.vinculo_domestico).
            vinculo_domestico = ""
            alertas.append(
                "Vínculo doméstico/familiar entre réu e ofendida (e inciso do art. 5º da Lei nº 11.340/2006) não extraído dos autos. "
                "Informe-o (metadados_override.vinculo_domestico)."
            )

    # 6. Datas-chave (Data do Fato, Recebimento da Denúncia e Intimação MPU)
    if is_demo:
        data_fato = peca_bo.data_peca if peca_bo else "2025-02-14"
        data_rec = peca_rec.data_peca if peca_rec else "2025-02-28"
        data_int = peca_int.data_peca if peca_int else "2025-01-22"
    else:
        data_fato = _parse_data_fato_dinamica(peca_bo, peca_den, texto_unificado, alertas)
        data_rec = peca_rec.data_peca if peca_rec else ""
        data_int = peca_int.data_peca if (peca_int and peca_int.data_peca) else ""
        if not data_int and peca_mpu:
            alertas.append(
                f"Data da intimação das medidas protetivas não extraída (decisão em {peca_mpu.doc_id}); "
                "informe-a se houver imputação do art. 24-A da Lei 11.340/2006."
            )

    intimidado_mpu = bool(
        peca_int and ("intimei pessoalmente" in peca_int.conteudo.lower() or "oficial de justiça" in peca_int.conteudo.lower())
    )
    if not peca_cac:
        # Sem prova de maus antecedentes/reincidência prevalece a primariedade (presunção favorável),
        # mas o juiz é alertado de que a certidão não foi localizada.
        primario = True
        if not is_demo:
            alertas.append("Certidão de antecedentes criminais não localizada nos autos; primariedade presumida.")
    else:
        cac_low = peca_cac.conteudo.lower()
        tem_negativa = any(x in cac_low for x in ("nada constar", "não constam", "primário", "sem antecedentes"))
        tem_cond_def = ("condenação definitiva" in cac_low or "transitada em julgado" in cac_low) and not tem_negativa
        primario = not tem_cond_def

    # 6. Pedidos reais do MP e da Defesa (memoriais + alegações finais orais na ata e em transcrições)
    pedidos_mp: List[PedidoParteItem] = []
    pedidos_defesa: List[PedidoParteItem] = []
    textos_defesa_orais: List[Dict[str, str]] = []

    pecas_mp_af = [p for p in pecas if p.categoria == "ALEGACOES_FINAIS_MP"]
    pecas_def_af = [p for p in pecas if p.categoria == "ALEGACOES_FINAIS_DEFESA"]
    pecas_ata = [p for p in pecas if p.categoria == "TERMO_AUDIENCIA"]

    for p_mp in pecas_mp_af:
        extraidos_mp = extrair_pedidos_mp_texto(p_mp.conteudo, fonte_id=p_mp.doc_id, folha=p_mp.folha, origem="memoriais")
        if extraidos_mp:
            pedidos_mp.extend(extraidos_mp)
        elif not is_demo:
            alertas.append(
                f"Memoriais do MP ({p_mp.doc_id}): pedidos não identificados automaticamente: conferir a peça."
            )

    # Pedido expresso de dano moral constante da denúncia (se ainda não veio dos memoriais do MP)
    if peca_den and not any(p.tipo == "dano_moral" for p in pedidos_mp):
        dm_den = [
            p for p in extrair_pedidos_mp_texto(peca_den.conteudo, fonte_id=peca_den.doc_id, folha=peca_den.folha, origem="memoriais")
            if p.tipo == "dano_moral"
        ]
        pedidos_mp.extend(dm_den)

    # Alegações finais orais registradas em Termo de Audiência
    for p_ata in pecas_ata:
        mp_oral, def_oral, txts_def_oral, alertas_ata = extrair_alegacoes_orais_ata(
            p_ata.conteudo, fonte_id=p_ata.doc_id, folha=p_ata.folha
        )
        pedidos_mp.extend(mp_oral)
        pedidos_defesa.extend(def_oral)
        for t_oral in txts_def_oral:
            textos_defesa_orais.append(
                {"categoria": "ALEGACOES_FINAIS_DEFESA", "conteudo": t_oral, "doc_id": p_ata.doc_id, "folhas": p_ata.folha, "origem": "oral"}
            )
        alertas.extend(alertas_ata)

    # Alegações finais orais oriundas de transcrições gravadas
    for tr in transcricoes or []:
        if not isinstance(tr, dict):
            continue
        papel_tr = str(tr.get("papel", "")).upper().strip()
        media_id = str(tr.get("media_id") or tr.get("arquivo") or "MIDIA-ORAL")
        segs = tr.get("segmentos") or tr.get("segmentos_literais") or []
        txt_tr = str(tr.get("texto") or "").strip()
        if not txt_tr and isinstance(segs, list):
            txt_tr = " ".join(str(s.get("texto_literal") or s.get("texto") or "") for s in segs if isinstance(s, dict)).strip()
        if not txt_tr:
            continue
        if papel_tr == "ALEGACOES_FINAIS_MP":
            pedidos_mp.extend(extrair_pedidos_mp_texto(txt_tr, fonte_id=media_id, folha="", origem="oral"))
        elif papel_tr in ("ALEGACOES_FINAIS_DEFESA", "DEFESA"):
            textos_defesa_orais.append(
                {"categoria": "ALEGACOES_FINAIS_DEFESA", "conteudo": txt_tr, "doc_id": media_id, "folhas": "", "origem": "oral"}
            )
            pedidos_defesa.extend(extrair_pedidos_subsidiarios_defesa_texto(txt_tr, fonte_id=media_id, folha="", origem="oral"))
        elif re.search(r"alega[çc][õo]es\s+finais", f"{tr.get('arquivo', '')} {txt_tr[:200]}", flags=re.IGNORECASE):
            mp_o, def_o, txts_o, al_o = extrair_alegacoes_orais_ata(txt_tr, fonte_id=media_id, folha="")
            pedidos_mp.extend(mp_o)
            pedidos_defesa.extend(def_o)
            for t_o in txts_o:
                textos_defesa_orais.append(
                    {"categoria": "ALEGACOES_FINAIS_DEFESA", "conteudo": t_o, "doc_id": media_id, "folhas": "", "origem": "oral"}
                )
            if al_o:
                alertas.extend(al_o)
            elif not mp_o and not def_o:
                alertas.append(
                    f"Alegações finais orais em transcrição ({media_id}) com autoria indefinida (MP ou Defesa): conferir a mídia."
                )

    # Sincroniza dano moral do MP diretamente a partir de pedidos_mp (Requisito 6)
    pedidos_dm_mp = [p for p in pedidos_mp if p.tipo == "dano_moral"]
    pedido_dano_moral = bool(pedidos_dm_mp)
    valor_dm = 0.0
    if pedido_dano_moral:
        for p_dm in pedidos_dm_mp:
            if p_dm.valor and p_dm.valor > 0:
                valor_dm = float(p_dm.valor)
                break
        if not valor_dm:
            if is_demo:
                valor_dm = 3000.0
            else:
                alertas.append(
                    "Pedido de reparação por dano moral sem valor indicado pelo MP — arbitramento pendente (Tema 983/STJ)."
                )

    # 7. Imputações com fonte e fatos próprios. A lista de códigos ainda atende aos consumidores legados.
    imputacoes, eventos_pendentes = extrair_imputacoes(pecas, _limpar_nome_pessoa)
    if is_demo:
        crimes_imputados = ["art_24a_lmp", "art_129_p13_cp", "art_147_cp"]
        houve_emendatio_150 = False
        hora_bo = ""
    else:
        crimes_imputados, houve_emendatio_150, hora_bo = _extrair_crimes_dinamicamente(
            pecas, peca_den, peca_rec, peca_mp_af
        )
        por_codigo: Dict[str, List[ImputacaoExtraida]] = {}
        for item in imputacoes:
            if item.estado == "extraida":
                por_codigo.setdefault(item.codigo, []).append(item)

        imputacoes_ativas: List[ImputacaoExtraida] = []
        for codigo, itens in por_codigo.items():
            ativos = [
                item for item in itens
                if not any(evento.tipo in ("rejeicao", "extincao") for evento in item.eventos)
            ]
            if not ativos:
                crimes_imputados = [c for c in crimes_imputados if c != codigo]
                continue
            imputacoes_ativas.extend(ativos)
            if codigo not in crimes_imputados:
                crimes_imputados.append(codigo)
            if len(ativos) > 1:
                alertas.append(f"Há {len(ativos)} imputações distintas de {codigo}. Confira os fatos individualmente.")
        recebimento_pendente = any(e.tipo == "recebimento" for e in eventos_pendentes)
        if recebimento_pendente or any(e.tipo == "recebimento" for item in imputacoes for e in item.eventos):
            datas_por_item = [
                {e.data_decisao for e in item.eventos if e.tipo == "recebimento" and e.data_decisao}
                for item in imputacoes_ativas
            ]
            datas_ativas = set().union(*datas_por_item)
            if not recebimento_pendente and len(datas_ativas) == 1 and all(datas_por_item):
                data_rec = next(iter(datas_ativas))
            else:
                data_rec = ""
                alertas.append("Não há data única de recebimento confirmada para as imputações ativas. Confira os atos por fato.")
        elif not data_rec:
            alertas.append(
                "Data do recebimento da denúncia não extraída (marco interruptivo da prescrição — art. 117, I, CP). "
                "Informe-a (metadados_override.data_recebimento_denuncia)."
            )
        if any(item.estado == "sugestao_a_confirmar" for item in imputacoes):
            alertas.append("Há capitulação sugerida por referência nominal sem artigo identificado. Confirme o tipo antes de adotá-lo.")
        for fonte in dict.fromkeys(evento.fonte_id for evento in eventos_pendentes):
            alertas.append(f"Ato judicial em {fonte} sem vínculo inequívoco com uma imputação. Confira o alcance da decisão.")
        if not crimes_imputados:
            alertas.append(
                "Nenhum tipo penal imputado foi identificado na denúncia/alegações finais. "
                "Informe os crimes (metadados_override.crimes_imputados)."
            )

    # 8. Teses Defensivas Extraídas (Art. 381, III, CPP)
    if is_demo:
        fonte_def_demo = peca_def_af.doc_id if peca_def_af else "ID-4100420"
        folha_def_demo = peca_def_af.folha if peca_def_af else ""
        teses_defesa = [
            TeseDefensivaItem(
                codigo="PREL-01",
                tipo="PRELIMINAR",
                titulo="Preliminar de Inépcia da Denúncia e Ausência de Justa Causa (Art. 41 e Art. 395, I e III, do CPP)",
                descricao_defesa="A Defesa alega descrição genérica dos fatos na peça acusatória e ausência de suporte probatório mínimo.",
                fonte_id=fonte_def_demo,
                folha=folha_def_demo,
                trecho="Preliminar de inépcia da denúncia (art. 41 e art. 395, I e III, do CPP).",
                origem="memoriais",
            ),
            TeseDefensivaItem(
                codigo="MER-01",
                tipo="MERITO",
                titulo="Tese de Insuficiência Probatória (In Dubio Pro Reo - Art. 386, VII, do CPP)",
                descricao_defesa="Sustenta que os fatos ocorreram sem testemunhas oculares presenciais, questionando o valor probante da palavra da ofendida e do policial militar.",
                fonte_id=fonte_def_demo,
                folha=folha_def_demo,
                trecho="Insuficiência probatória e in dubio pro reo (art. 386, VII, do CPP).",
                origem="memoriais",
            ),
            TeseDefensivaItem(
                codigo="MER-02",
                tipo="MERITO",
                titulo="Tese de Atipicidade do Crime de Ameaça por Estado de Ira / Ânimo Exaltado (Art. 147, § 1º, do CP)",
                descricao_defesa="Alega que eventual discussão ocorreu de cabeça quente, sem seriedade idônea do mal injusto e grave prometido.",
                fonte_id=fonte_def_demo,
                folha=folha_def_demo,
                trecho="Atipicidade da ameaça por ânimo exaltado / discussão acalorada.",
                origem="memoriais",
            ),
            TeseDefensivaItem(
                codigo="MER-03",
                tipo="MERITO",
                titulo="Tese de Ausência de Dolo no Descumprimento de Medida Protetiva (Art. 24-A da Lei nº 11.340/2006)",
                descricao_defesa="Sustenta que o acusado compareceu ao imóvel apenas para ver a filha comum e tratar de assuntos familiares, sem dolo de desobedecer à ordem judicial.",
                fonte_id=fonte_def_demo,
                folha=folha_def_demo,
                trecho="Ausência de dolo no descumprimento de medida protetiva (art. 24-A da LMP).",
                origem="memoriais",
            ),
            TeseDefensivaItem(
                codigo="MER-04",
                tipo="MERITO",
                titulo="Pedidos Subsidiários na Dosimetria (Pena no Mínimo, Confissão, Substituição por Restritiva de Direitos e Exclusão do Dano Moral)",
                descricao_defesa="Pugna pela fixação da pena-base no mínimo legal, incidência da confissão espontânea, substituição da pena privativa de liberdade e afastamento da indenização do Tema 983/STJ.",
                fonte_id=fonte_def_demo,
                folha=folha_def_demo,
                trecho="Pena-base no mínimo legal, confissão espontânea, substituição e exclusão do dano moral.",
                origem="memoriais",
            ),
        ]
    else:
        fontes_defesa: List[Any] = list(pecas) + textos_defesa_orais
        teses_defesa = extrair_teses_defensivas(fontes_defesa)
        cods = {t.codigo for t in teses_defesa}

        # Emendatio libelli (art. 383 CPP) é decisão do juiz: apenas sinaliza, sem alterar a imputação
        # nem atribuir à Defesa uma tese que ela não formulou.
        if houve_emendatio_150:
            alertas.append(
                "Possível emendatio libelli (art. 383 do CPP): a denúncia capitula o art. 150, § 1º, do CP, mas o registro "
                f"policial ({peca_bo.doc_id if peca_bo else 'APF/BO'}) indica horário diurno ({hora_bo or 'não informado'}). "
                "A readequação típica depende de decisão do Juízo."
            )
        if peca_rec and ("decadência" in peca_rec.conteudo.lower() or "extinta a punibilidade" in peca_rec.conteudo.lower()) and "PREL-01" not in cods:
            teses_defesa.insert(
                0,
                TeseDefensivaItem(
                    codigo="PREL-01",
                    tipo="PRELIMINAR",
                    titulo="Extinção Parcial da Punibilidade pela Decadência Já Reconhecida nos Autos (Art. 103 e Art. 107, IV, do CP)",
                    descricao_defesa=f"Questão preliminar já examinada na decisão de {peca_rec.doc_id} relativamente à imputação de crime contra a honra de ação penal privada.",
                    fonte_id=peca_rec.doc_id,
                    folha=peca_rec.folha,
                    trecho=_recortar_trecho(peca_rec.conteudo, 0, min(len(peca_rec.conteudo), 200)),
                    origem="memoriais",
                ),
            )

        # Nenhuma tese identificada: nunca inventar MER-01; registrar alerta explícito (TASK-041).
        if not teses_defesa:
            alertas.append(
                "Teses da defesa não identificadas: conferir"
                + (f" os memoriais da Defesa ({peca_def_af.doc_id})" if peca_def_af else " as manifestações defensivas nos autos")
                + "."
            )

    # Consolida pedidos_defesa: teses reconhecidas + pedidos subsidiários reais dos memoriais/alegações orais
    chaves_def_vistas = {(p.tipo, p.fonte_id) for p in pedidos_defesa}
    for t in teses_defesa:
        fonte_t = t.fonte_id or (peca_def_af.doc_id if peca_def_af else "DEFESA")
        trecho_t = (t.trecho or t.descricao_defesa or t.titulo)[:300]
        chave = (t.codigo, fonte_t)
        if chave not in chaves_def_vistas:
            chaves_def_vistas.add(chave)
            pedidos_defesa.append(
                PedidoParteItem(
                    tipo=t.codigo,
                    descricao=t.titulo,
                    crime=_detectar_crime_no_trecho(trecho_t),
                    fonte_id=fonte_t,
                    folha=t.folha or (peca_def_af.folha if peca_def_af else ""),
                    trecho=trecho_t,
                    origem=t.origem or "memoriais",
                )
            )
    for p_def in pecas_def_af:
        for pd in extrair_pedidos_subsidiarios_defesa_texto(p_def.conteudo, fonte_id=p_def.doc_id, folha=p_def.folha, origem="memoriais"):
            chave = (pd.tipo, pd.fonte_id)
            if chave not in chaves_def_vistas:
                chaves_def_vistas.add(chave)
                pedidos_defesa.append(pd)

    pontos_decisivos = extrair_pontos_decisivos(
        pecas,
        nome_reu=nome_reu,
        nome_ofendida=nome_ofendida,
        data_fato=data_fato,
        peca_cac=peca_cac,
        peca_int=peca_int,
        data_intimacao_mpu=data_int,
        intimidado_mpu=intimidado_mpu,
        pedidos_mp=pedidos_mp,
        transcricoes=transcricoes,
    )

    return MetadadosProcesso(
        numero_processo=numero_processo,
        autos_mpu_apenso=autos_mpu_apenso,
        nome_ofendida=nome_ofendida,
        nome_reu=nome_reu,
        vinculo_domestico=vinculo_domestico,
        data_fato=data_fato,
        data_recebimento_denuncia=data_rec,
        data_intimacao_mpu=data_int,
        intimidado_pessoalmente_mpu=intimidado_mpu,
        reu_primario_sumula444=primario,
        pedido_dano_moral_expresso_mp=pedido_dano_moral,
        valor_dano_moral_pedido_mp=valor_dm,
        crimes_imputados=crimes_imputados,
        imputacoes=imputacoes,
        eventos_imputacao_pendentes=eventos_pendentes,
        teses_defesa=teses_defesa,
        pedidos_mp=pedidos_mp,
        pedidos_defesa=pedidos_defesa,
        pontos_decisivos=pontos_decisivos,
        alertas_extracao=alertas,
    )
