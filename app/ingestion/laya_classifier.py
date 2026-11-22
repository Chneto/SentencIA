"""Laya System 1: classificação determinística das peças e extração de metadados do processo.

Princípios (v2):
- o caso demonstrativo só é tratado como demo com ``is_demo=True`` explícito (nunca pelo prefixo
  ``ID-4100`` — IDs reais do PJe podem começar com 4100);
- dados não extraídos ficam VAZIOS (``""``/``[]``/``0.0``) com alerta em
  ``MetadadosProcesso.alertas_extracao`` — nada de datas, crimes, números de processo ou valores fictícios.
"""

import datetime
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.core.text_utils import normalizar


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


class TeseDefensivaItem(BaseModel):
    codigo: str
    tipo: str  # PRELIMINAR ou MERITO
    titulo: str
    descricao_defesa: str
    obrigatorio_enfrentamento_art381: bool = True


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
    teses_defesa: List[TeseDefensivaItem]
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
    "ALEGACOES_FINAIS_DEFESA": "Síntese Laya S1: Memoriais defensivos suscitando 1 preliminar (inépcia) e 4 teses de mérito/subsidiárias de enfrentamento obrigatório (art. 381, III, CPP).",
}


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
        return "Síntese Laya S1: Memoriais finais do Ministério Público (art. 403, § 3º, CPP) pugnando pela condenação e reparação mínima (Tema 983/STJ)."
    if cat == "ALEGACOES_FINAIS_DEFESA":
        pontos = []
        if "ABSOLVIÇÃO" in norm_up:
            pontos.append("absolvição")
        if "CONFISSÃO" in norm_up:
            pontos.append("confissão espontânea (Súmula 545/STJ)")
        if "MÍNIMO LEGAL" in norm_up:
            pontos.append("pena-base no mínimo legal")
        if "SURSIS" in norm_up:
            pontos.append("sursis penal")
        if "JUSTIÇA GRATUITA" in norm_up or "GRATUIDADE" in norm_up:
            pontos.append("gratuidade da justiça")
        if "DANO MORAL" in norm_up or "DANOS MORAIS" in norm_up:
            pontos.append("exclusão/modicidade do dano moral")
        pontos_str = ", ".join(pontos) if pontos else "teses absolutórias e pedidos subsidiários"
        return f"Síntese Laya S1: Memoriais finais da Defesa Técnica postulando {pontos_str}."
    if cat == "CERTIDAO_INTIMACAO_MPU":
        return "Síntese Laya S1: Certidão de Oficial de Justiça certificando diligências de citação/intimação e verificação de contatos/endereços."
    if "NÃO FOI ENCONTRADO AS IMAGENS" in norm_up or "ORDEM DE MISSÃO POLICIAL" in norm_up:
        return "Síntese Laya S1: Certidão e Relatório de Missão Policial da DEAM sobre diligências complementares na investigação."
    return "Síntese Laya S1: Peça informativa do procedimento policial/investigativo (BO, Termos de Declarações, Laudos ou Relatório Final)."


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
        elif "memoriais do ministério público" in titulo_low:
            _pin("ALEGACOES_FINAIS_MP", 0.99)
        elif "memoriais da defesa" in titulo_low:
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

    # Mapeia ocorrências em ordem de aparição no texto de imputação
    candidatos: List[tuple[int, str]] = []
    pat_crimes = (
        r"(?:art(?:igo)?s?\.?\s*|(?<=,\s)|(?<=e\s)|(?<=:\s))"
        r"(147\s*-\s*[ab]|215\s*-\s*a|218\s*-\s*c|24\s*-\s*a|"
        r"129\s*,\s*§\s*(?:13|9)[º°]?|"
        r"140\s*,\s*§\s*2[º°]|"
        r"148\s*,\s*§\s*1[º°]|"
        r"150\s*,\s*§\s*1[º°]|"
        r"163\s*,\s*(?:par[áa]grafo\s+[úu]nico|p\.?\s*[úu]\.?|§\s*[úu]nico)|"
        r"(?:138|139|140|146|147|148|150|158|163|330|331)(?:\s*,\s*caput)?|"
        r"21\s+(?:d[ao]\s+)?(?:lcp|lei\s+das\s+contraven[çc][õo]es\s+penais|decreto[-\s]*lei\s*(?:n[º°\.]?\s*)?3\.?688))(?![\wº°])"
    )
    for m in re.finditer(pat_crimes, imput_txt, flags=re.IGNORECASE):
        tok = re.sub(r"\s+", " ", m.group(1)).lower()
        tok_compact = re.sub(r"\s+", "", tok)
        pos = m.start()
        if "147-a" in tok_compact:
            candidatos.append((pos, "art_147a_cp"))
        elif "147-b" in tok_compact:
            candidatos.append((pos, "art_147b_cp"))
        elif "215-a" in tok_compact:
            candidatos.append((pos, "art_215a_cp"))
        elif "218-c" in tok_compact:
            candidatos.append((pos, "art_218c_cp"))
        elif "24-a" in tok_compact:
            candidatos.append((pos, "art_24a_lmp"))
        elif "129" in tok and "13" in tok:
            candidatos.append((pos, "art_129_p13_cp"))
        elif "129" in tok and "9" in tok:
            candidatos.append((pos, "art_129_p9_cp"))
        elif tok.startswith("140") and "§" in tok:
            candidatos.append((pos, "art_140_p2_cp"))
        elif tok.startswith("140"):
            candidatos.append((pos, "art_140_cp"))
        elif tok.startswith("148"):
            candidatos.append((pos, "art_148_p1_cp"))
        elif tok.startswith("150") and "§" in tok:
            candidatos.append((pos, "art_150_p1_cp"))
        elif tok.startswith("150"):
            candidatos.append((pos, "art_150_cp"))
        elif tok.startswith("163") and ("par" in tok or "p. u" in tok or "p.u" in tok or "p.ú" in tok or "p. ú" in tok or "§" in tok):
            candidatos.append((pos, "art_163_pu_cp"))
        elif tok.startswith("163"):
            candidatos.append((pos, "art_163_cp"))
        elif tok.startswith("147"):
            candidatos.append((pos, "art_147_cp"))
        elif tok.startswith("138"):
            candidatos.append((pos, "art_138_cp"))
        elif tok.startswith("139"):
            candidatos.append((pos, "art_139_cp"))
        elif tok.startswith("146"):
            candidatos.append((pos, "art_146_cp"))
        elif tok.startswith("158"):
            candidatos.append((pos, "art_158_cp"))
        elif tok.startswith("330"):
            candidatos.append((pos, "art_330_cp"))
        elif tok.startswith("331"):
            candidatos.append((pos, "art_331_cp"))
        elif tok.startswith("21"):
            candidatos.append((pos, "art_21_lcp"))

    # Fallback nominal se não houver numeração expressa no trecho capturado
    if not candidatos:
        full_acc = f"{peca_den.conteudo if peca_den else ''} {peca_mp_af.conteudo if peca_mp_af else ''}".lower()
        if "perseguição" in full_acc or "147-a" in full_acc:
            candidatos.append((0, "art_147a_cp"))
        if "24-a" in full_acc or "descumprimento de medida protetiva" in full_acc:
            candidatos.append((1, "art_24a_lmp"))
        if "lesão corporal" in full_acc:
            candidatos.append((2, "art_129_p13_cp"))
        if "violação de domicílio" in full_acc:
            candidatos.append((3, "art_150_cp"))
        if "ameaça" in full_acc and all(c != "art_147a_cp" for _, c in candidatos):
            candidatos.append((4, "art_147_cp"))

    crimes_ordenados: List[str] = []
    for _, code in sorted(candidatos, key=lambda x: x[0]):
        if code not in crimes_ordenados:
            # Evita incluir art. 147 simples como subproduto de '147-A' / '147-B' caso já não citado separadamente
            if code == "art_147_cp" and not re.search(r"(?:^|[^\w-])147(?!\s*-[ab])\b", imput_txt):
                continue
            crimes_ordenados.append(code)

    # Verifica se algum delito (ex.: art. 139) já teve a punibilidade extinta na decisão de recebimento
    if peca_rec and "art_139_cp" in crimes_ordenados:
        rec_low = peca_rec.conteudo.lower()
        if ("extinta" in rec_low or "decadência" in rec_low) and "139" in rec_low:
            crimes_ordenados = [c for c in crimes_ordenados if c != "art_139_cp"]

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


def extrair_metadados_processo(pecas: List[PecaClassificada], is_demo: bool = False) -> MetadadosProcesso:
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
    for pat_vit in (
        r"V[íi]tima:\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-záéíóúâêôãõç\s]{5,60}?)(?:\.|,|\n|Delito|AUTOR)",
        r"Ofendida:\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-záéíóúâêôãõç\s]{5,60}?)(?:\.|,|\n|Indiciado)",
        r"(?:sua|da\s+sua)\s+ex\s*-?\s*companheira,?\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-záéíóúâêôãõç\s]{5,60}?)(?:,|\.|;)",
        r"V[ÍI]TIMA\(S\):\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-záéíóúâêôãõç\s]{5,60}?)(?:,|\.|AUTOR|\n)",
    ):
        m_v = re.search(pat_vit, source_nomes)
        if m_v:
            cand = re.sub(r"\s+", " ", m_v.group(1)).strip()
            if (
                "SEGREDO" not in cand.upper()
                and len(cand) > 6
                and not re.match(r"^[A-Z]\.\s", cand)
            ):
                nome_ofendida = cand.upper()
                break
    if not nome_ofendida:
        alertas.append("Nome da ofendida não extraído dos autos.")

    # 4. Nome do Réu / Denunciado
    nome_reu = "CARLOS EDUARDO ROCHA MENDES" if is_demo else ""
    for pat_reu in (
        r"DENÚNCIA\s+(?:contra|em desfavor de)\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-záéíóúâêôãõç\s]{5,60}?)(?:,|\n)",
        r"Denunciado:\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-záéíóúâêôãõç\s]{5,60}?)(?:\n|,|\.|V[íi]tima)",
        r"Acusado:\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-záéíóúâêôãõç\s]{5,60}?)(?:\n|,|\.)",
        r"Polo [Pp]assivo:\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-záéíóúâêôãõç\s]{5,60}?)(?:\n|,|\.)",
        r"([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ\s]{5,60}?)\s*\(REU\)",
    ):
        m_r = re.search(pat_reu, source_nomes)
        if m_r:
            cand_r = re.sub(r"\s+", " ", m_r.group(1)).strip().upper()
            if len(cand_r) >= 6 and "MINISTÉRIO" not in cand_r:
                nome_reu = cand_r
                break
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
        if peca_rec and peca_rec.data_peca:
            data_rec = peca_rec.data_peca
        else:
            data_rec = ""
            alertas.append(
                "Data do recebimento da denúncia não extraída (marco interruptivo da prescrição — art. 117, I, CP). "
                "Informe-a (metadados_override.data_recebimento_denuncia)."
            )
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

    den_and_mp = f"{peca_den.conteudo if peca_den else ''} {peca_mp_af.conteudo if peca_mp_af else ''}".lower()
    pedido_dano_moral = any(
        termo in den_and_mp
        for termo in (
            "danos morais",
            "dano moral",
            "reparação pelos danos",
            "valor mínimo para reparação",
            "art. 387, iv",
            "387, inciso iv",
        )
    )
    valor_dm = 0.0
    if pedido_dano_moral:
        m_val = re.search(r"R\$\s*([\d\.]+,\d{2})", den_and_mp, flags=re.IGNORECASE)
        if m_val:
            try:
                valor_dm = float(m_val.group(1).replace(".", "").replace(",", "."))
            except ValueError:
                valor_dm = 0.0
        if not valor_dm:
            if is_demo:
                valor_dm = 3000.0
            else:
                alertas.append(
                    "Pedido de reparação por dano moral sem valor indicado pelo MP — arbitramento pendente (Tema 983/STJ)."
                )

    # 7. Crimes Imputados na Denúncia / Recebimento / Alegações Finais do MP
    if is_demo:
        crimes_imputados = ["art_24a_lmp", "art_129_p13_cp", "art_147_cp"]
        houve_emendatio_150 = False
        hora_bo = ""
    else:
        crimes_imputados, houve_emendatio_150, hora_bo = _extrair_crimes_dinamicamente(
            pecas, peca_den, peca_rec, peca_mp_af
        )
        if not crimes_imputados:
            alertas.append(
                "Nenhum tipo penal imputado foi identificado na denúncia/alegações finais. "
                "Informe os crimes (metadados_override.crimes_imputados)."
            )

    # 8. Teses Defensivas Extraídas (Art. 381, III, CPP)
    if is_demo:
        teses_defesa = [
            TeseDefensivaItem(
                codigo="PREL-01",
                tipo="PRELIMINAR",
                titulo="Preliminar de Inépcia da Denúncia e Ausência de Justa Causa (Art. 41 e Art. 395, I e III, do CPP)",
                descricao_defesa="A Defesa alega descrição genérica dos fatos na peça acusatória e ausência de suporte probatório mínimo.",
            ),
            TeseDefensivaItem(
                codigo="MER-01",
                tipo="MERITO",
                titulo="Tese de Insuficiência Probatória (In Dubio Pro Reo - Art. 386, VII, do CPP)",
                descricao_defesa="Sustenta que os fatos ocorreram sem testemunhas oculares presenciais, questionando o valor probante da palavra da ofendida e do policial militar.",
            ),
            TeseDefensivaItem(
                codigo="MER-02",
                tipo="MERITO",
                titulo="Tese de Atipicidade do Crime de Ameaça por Estado de Ira / Ânimo Exaltado (Art. 147, § 1º, do CP)",
                descricao_defesa="Alega que eventual discussão ocorreu de cabeça quente, sem seriedade idônea do mal injusto e grave prometido.",
            ),
            TeseDefensivaItem(
                codigo="MER-03",
                tipo="MERITO",
                titulo="Tese de Ausência de Dolo no Descumprimento de Medida Protetiva (Art. 24-A da Lei nº 11.340/2006)",
                descricao_defesa="Sustenta que o acusado compareceu ao imóvel apenas para ver a filha comum e tratar de assuntos familiares, sem dolo de desobedecer à ordem judicial.",
            ),
            TeseDefensivaItem(
                codigo="MER-04",
                tipo="MERITO",
                titulo="Pedidos Subsidiários na Dosimetria (Pena no Mínimo, Confissão, Substituição por Restritiva de Direitos e Exclusão do Dano Moral)",
                descricao_defesa="Pugna pela fixação da pena-base no mínimo legal, incidência da confissão espontânea, substituição da pena privativa de liberdade e afastamento da indenização do Tema 983/STJ.",
            ),
        ]
    else:
        teses_defesa = extrair_teses_defensivas(pecas)
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
                ),
            )

        # Nenhuma tese identificada: não se atribui à Defesa tese que ela não formulou (art. 381, III, CPP).
        if not teses_defesa:
            alertas.append(
                "Nenhuma tese defensiva foi identificada automaticamente"
                + (f" nos memoriais da Defesa ({peca_def_af.doc_id})" if peca_def_af else " (memoriais da Defesa não localizados)")
                + "; verifique as manifestações defensivas antes de sentenciar."
            )
        elif not any(t.codigo.startswith("MER-") for t in teses_defesa) and peca_def_af and "absolvi" in peca_def_af.conteudo.lower():
            teses_defesa.append(
                TeseDefensivaItem(
                    codigo="MER-01",
                    tipo="MERITO",
                    titulo="Tese Defensiva Genérica de Absolvição / In Dubio Pro Reo (Art. 386 do CPP)",
                    descricao_defesa=f"Nos termos dos memoriais defensivos ({peca_def_af.doc_id}), a Defesa ressalva a possibilidade de absolvição por eventual excludente ou insuficiência probatória.",
                )
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
        teses_defesa=teses_defesa,
        alertas_extracao=alertas,
    )
