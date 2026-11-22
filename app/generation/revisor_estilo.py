"""
Painel de revisão da minuta: conferência estrutural/estilística e pendências do laudo.

Duas fontes de itens, no mesmo formato:

* ``origem="manual"`` — regras determinísticas fundamentadas no CPP (art. 381) e no *Manual da
  Sentença Penal Condenatória* (Taipina, 6ª ed., cap. I, item 4: relatório, fundamentação, dispositivo
  e autenticação), que acompanha o sistema em ``Modelos de Sentenças``. Cada item traz a ``base``
  (dispositivo/seção do manual) para o magistrado confrontar; são APOIO à revisão, não decisão.
* ``origem="laudo"`` — pendências já apuradas pelo auditor (marcadores a completar, IDs inexistentes,
  citações fora da base fechada, teses não enfrentadas, penas divergentes, seções substituídas).

Severidade: ``obrigatorio`` (falta de requisito do art. 381 ou pendência que impede assinar),
``recomendado`` (boa prática do manual) e ``observacao`` (heurística ou escolha de estilo).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional

_SECOES = ("relatorio", "fundamentacao", "dispositivo")
_TITULO_SECAO_RE = re.compile(
    r"^\s*(?:I{1,3}|IV|V)\s*[–—-]\s*(RELAT[ÓO]RIO|FUNDAMENTA[ÇC][ÃA]O|DISPOSITIVO)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
_MARCADOR_RE = re.compile(r"\[\s*COMPLETAR\s+PELO\s+JU[ÍI]ZO\b[^\]\n]*\]?")
_DATA_RE = re.compile(r"\d{1,2}\s+de\s+[a-zç]+\s+de\s+\d{4}", re.IGNORECASE)
_RELATORIO_MAX_PALAVRAS = 1500  # heurística: o manual pede relatório "não prolixo"


def _chave(origem: str, tipo: str, ref: str) -> str:
    return hashlib.sha1(f"{origem}|{tipo}|{ref}".encode("utf-8")).hexdigest()[:10]


def _item(origem: str, tipo: str, categoria: str, severidade: str, titulo: str, detalhe: str = "",
          base: str = "", trecho: str = "", sugestao: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": _chave(origem, tipo, trecho or titulo),
        "origem": origem,
        "tipo": tipo,
        "categoria": categoria,
        "severidade": severidade,
        "titulo": titulo,
        "detalhe": detalhe,
        "base": base,
        "trecho": trecho,
        "sugestao": sugestao,
    }


def _secoes(texto: str) -> Dict[str, str]:
    """Texto de cada seção (relatório/fundamentação/dispositivo) a partir dos títulos ``I – RELATÓRIO``..."""
    achados = []
    for m in _TITULO_SECAO_RE.finditer(texto):
        nome = re.sub(r"[ÓO]", "o", m.group(1).lower())
        chave = "relatorio" if nome.startswith("relat") else "fundamentacao" if nome.startswith("fundament") else "dispositivo"
        achados.append((chave, m.start(), m.end()))
    saida: Dict[str, str] = {}
    for i, (chave, _ini, fim) in enumerate(achados):
        proximo = achados[i + 1][1] if i + 1 < len(achados) else len(texto)
        saida.setdefault(chave, texto[fim:proximo])
    saida["_ordem"] = ",".join(c for c, _, _ in achados)
    return saida


def _primeiro(regex: "re.Pattern[str]", texto: str) -> Optional["re.Match[str]"]:
    return regex.search(texto)


_TUDO_MAIS_RE = re.compile(
    r"(?:(?:Ante|Diante)\s+o\s+exposto,?\s+)?e\s+(?:por\s+)?tudo\s+(?:o\s+)?mais\s+que\s+(?:dos|nos)\s+autos\s+consta,?",
    re.IGNORECASE,
)
_PROCEDENTE_ACAO_RE = re.compile(r"procedente\s+a\s+a[çc][ãa]o\b", re.IGNORECASE)
_DUPLO_ESPACO_RE = re.compile(r"(?<=\S)  +(?=\S)")
_REPETIDA_RE = re.compile(r"\b([A-Za-zÀ-ÿ]{3,})\s+\1\b", re.IGNORECASE)
_ESPACO_ANTES_PONT_RE = re.compile(r"(?<=\w) +[,;](?=\s)")


def revisar_estilo(texto: str, *, reu: str = "") -> List[Dict[str, Any]]:
    """Conferência da minuta pelo art. 381 do CPP e pelo manual. Devolve itens ordenados por gravidade."""
    texto = str(texto or "")
    itens: List[Dict[str, Any]] = []
    sec = _secoes(texto)
    M = "manual"
    base_rel = "CPP, art. 381, I e II; Manual (Taipina, 6ª ed.), cap. I, item 4.3"
    base_fund = "CPP, art. 381, III; Manual, cap. I, item 4.4"
    base_disp = "CPP, art. 381, IV; Manual, cap. I, item 4.5"

    # --- estrutura -------------------------------------------------------------------------
    for chave, titulo, base in (
        ("relatorio", "Relatório", base_rel),
        ("fundamentacao", "Fundamentação", base_fund),
        ("dispositivo", "Dispositivo", base_disp),
    ):
        if chave not in sec:
            itens.append(_item(M, f"falta_{chave}", "Estrutura", "obrigatorio", f"Seção ausente: {titulo}",
                               f"Não encontrei o título “{titulo.upper()}” (ex.: “I – RELATÓRIO”).", base))
    presentes = [c for c in sec.get("_ordem", "").split(",") if c]
    esperado = [c for c in _SECOES if c in presentes]
    if presentes and presentes != esperado:
        itens.append(_item(M, "ordem_secoes", "Estrutura", "obrigatorio", "Ordem das seções fora do padrão do art. 381",
                           "A sentença deve seguir: relatório, fundamentação e dispositivo.", "CPP, art. 381; Manual, cap. I, item 4"))

    cauda = texto[-1200:]
    if not _DATA_RE.search(cauda) or not re.search(r"Ju[íi]z|Ju[íi]za", cauda):
        itens.append(_item(M, "autenticacao", "Estrutura", "obrigatorio", "Data e assinatura do magistrado",
                           "Não identifiquei, no fim do texto, a data por extenso e o bloco de assinatura (com o local).",
                           "CPP, art. 381, VI; Manual, cap. I, item 4.6"))

    # --- relatório -------------------------------------------------------------------------
    rel = sec.get("relatorio", "")
    if rel:
        nome = " ".join(str(reu or "").split())
        partes = nome.split()
        if nome and nome.casefold() not in rel.casefold() and not (len(partes) > 1 and partes[0].casefold() in rel.casefold() and partes[-1].casefold() in rel.casefold()):
            itens.append(_item(M, "reu_no_relatorio", "Relatório", "obrigatorio", "Identificação do acusado no relatório",
                               f"O nome “{nome}” não aparece no relatório.", base_rel))
        if not re.search(r"receb\w+[^.]{0,80}den[úu]ncia|den[úu]ncia[^.]{0,100}receb\w+", rel, re.IGNORECASE):
            itens.append(_item(M, "recebimento_denuncia", "Relatório", "recomendado", "Data de recebimento da denúncia",
                               "Registrar a data: é marco interruptivo da prescrição (art. 117, I, CP).", base_rel))
        if not re.search(r"\bcita(?:do|da|dos|das|ção|ç[ãa]o)\b", rel, re.IGNORECASE):
            itens.append(_item(M, "citacao", "Relatório", "recomendado", "Citação do acusado",
                               "Mencionar a citação: sua falta é nulidade (art. 564, III, e, CPP).", base_rel))
        if not re.search(r"alega[çc][õo]es\s+finais|memoriais", rel, re.IGNORECASE):
            itens.append(_item(M, "pedidos_finais", "Relatório", "recomendado", "Pedidos das partes nas alegações finais",
                               "O relatório deve historiar os pedidos (condenação, absolvição, desclassificação, atenuantes...) "
                               "para que todos sejam enfrentados na fundamentação.", base_rel))
        palavras = len(rel.split())
        if palavras > _RELATORIO_MAX_PALAVRAS:
            itens.append(_item(M, "relatorio_longo", "Relatório", "observacao", "Relatório extenso",
                               f"{palavras} palavras. O manual pede relatório “não prolixo”, circunscrito ao necessário "
                               f"(heurística do sistema: acima de {_RELATORIO_MAX_PALAVRAS} palavras).", base_rel))

    # --- fundamentação ---------------------------------------------------------------------
    fund = sec.get("fundamentacao", "")
    if fund and not re.search(r"\bart(?:igo)?s?\.?\s*\d", fund, re.IGNORECASE):
        itens.append(_item(M, "fundamentacao_sem_artigos", "Fundamentação", "obrigatorio", "Fundamentação sem indicação de artigos de lei",
                           "É imperativo citar expressamente os artigos que amparam cada conclusão.", base_fund))
    disp = sec.get("dispositivo", "")
    dosimetria_re = re.compile(r"dosimetria\s+da\s+pena|c[áa]lculo\s+trif[áa]sico|pena-base", re.IGNORECASE)
    if fund and dosimetria_re.search(fund) and not dosimetria_re.search(disp):
        itens.append(_item(M, "dosimetria_na_fundamentacao", "Fundamentação", "observacao", "Dosimetria feita dentro da fundamentação",
                           "É prática comum, mas o manual recomenda que a dosimetria venha no dispositivo, depois da decisão "
                           "condenatória, e que todo o conteúdo decisório (pena, regime, prisão cautelar) fique ali. "
                           "Avalie conforme o padrão do seu gabinete.", "Manual, cap. I, item 4.5 (último parágrafo antes das observações)"))

    # --- dispositivo -----------------------------------------------------------------------
    m = _primeiro(_TUDO_MAIS_RE, disp or texto)
    if m:
        trecho = m.group(0)
        sug = "à vista das expostas razões," if trecho[:1].islower() else "À vista das expostas razões,"
        itens.append(_item(M, "tudo_mais_autos", "Dispositivo", "recomendado", "Evitar “e tudo mais que dos autos consta”",
                           "A expressão é abstrata e foge da fundamentação; basta “à vista das expostas razões”.",
                           "Manual, cap. I, item 4.5, Observação importante (primeira)", trecho, sug))
    m = _primeiro(_PROCEDENTE_ACAO_RE, disp or texto)
    if m:
        trecho = m.group(0)
        sug = re.sub(r"a\s+a[çc][ãa]o", "o pedido", trecho, flags=re.IGNORECASE)
        if trecho.isupper():
            sug = sug.upper()
        itens.append(_item(M, "procedente_acao", "Dispositivo", "recomendado", "Julgar procedente “o pedido”, não “a ação”",
                           "A ação penal já foi reconhecida como procedente; o que se julga é o pedido do Ministério Público.",
                           "Manual, cap. I, item 4.5, Observação importante (segunda)", trecho, sug))
    if disp and re.search(r"\bCONDENAR\b|\bcondeno\b", disp, re.IGNORECASE) and not re.search(r"\bart(?:igo)?s?\.?\s*\d", disp, re.IGNORECASE):
        itens.append(_item(M, "dispositivo_sem_artigos", "Dispositivo", "obrigatorio", "Condenação sem indicar os dispositivos legais",
                           "O decreto condenatório deve mencionar os artigos em que o sentenciado fica incurso, além dos de "
                           "agravantes, atenuantes, majorantes e minorantes.", base_disp))

    # --- forma -----------------------------------------------------------------------------
    m = _primeiro(_DUPLO_ESPACO_RE, texto)
    if m:
        ctx = texto[max(0, m.start() - 25): m.end() + 25].replace("\n", " ")
        itens.append(_item(M, "espacos_duplos", "Estilo", "observacao", "Espaços duplicados",
                           f"Ex.: “…{ctx}…”", "Forma do texto (ABNT NBR 14724)", m.group(0), " "))
    m = _primeiro(_REPETIDA_RE, texto)
    if m:
        itens.append(_item(M, "palavra_repetida", "Estilo", "observacao", "Palavra repetida",
                           f"“{m.group(0)}”", "Forma do texto", m.group(0), m.group(1)))
    m = _primeiro(_ESPACO_ANTES_PONT_RE, texto)
    if m:
        ctx = texto[max(0, m.start() - 25): m.end() + 20].replace("\n", " ")
        itens.append(_item(M, "espaco_antes_pontuacao", "Estilo", "observacao", "Espaço antes de vírgula/ponto e vírgula",
                           f"Ex.: “…{ctx}…”", "Forma do texto", m.group(0), m.group(0).strip()))
    return _ordenar(itens)


_PESO = {"obrigatorio": 0, "recomendado": 1, "observacao": 2}


def _ordenar(itens: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(itens, key=lambda i: _PESO.get(i["severidade"], 9))


def pendencias_do_laudo(texto: str, auditoria: Any = None, sentenca: Any = None) -> List[Dict[str, Any]]:
    """Pendências que impedem ou condicionam a assinatura: marcadores (do texto atual) + achados do laudo."""
    L = "laudo"
    itens: List[Dict[str, Any]] = []
    vistos = set()
    for m in _MARCADOR_RE.finditer(str(texto or "")):
        t = m.group(0).strip()
        if t in vistos:
            continue
        vistos.add(t)
        itens.append(_item(L, "marcador", "Pendências", "obrigatorio", "Trecho a completar pelo juízo", t, "Marcador do sistema", t))
    if auditoria is not None:
        for id_ in getattr(auditoria, "ids_inexistentes", []) or []:
            itens.append(_item(L, "id_inexistente", "Laudo", "obrigatorio", "ID citado não existe nos autos", str(id_),
                               "Auditoria — camada de IDs", str(id_)))
        for c in getattr(auditoria, "citacoes_nao_autorizadas", []) or []:
            itens.append(_item(L, "citacao_nao_autorizada", "Laudo", "obrigatorio", "Citação fora da base fechada", str(c),
                               "Auditoria — base jurídica fechada", str(c)))
        for t in getattr(auditoria, "teses_omitidas", []) or []:
            itens.append(_item(L, "tese_omitida", "Laudo", "obrigatorio", "Tese defensiva não enfrentada", str(t),
                               "Auditoria — teses da defesa; CPP, art. 315, § 2º, IV"))
        for p in getattr(auditoria, "penas_divergentes", []) or []:
            itens.append(_item(L, "pena_divergente", "Laudo", "obrigatorio", "Pena do dispositivo diverge da dosimetria", str(p),
                               "Auditoria — penas × dosimetria"))
        for s in getattr(auditoria, "secoes_fallback", []) or []:
            itens.append(_item(L, "secao_fallback", "Laudo", "recomendado", "Seção substituída por modelo determinístico", str(s),
                               "Revise a seção: o texto veio do modelo-padrão"))
    alertas = [str(a) for a in (getattr(sentenca, "alertas", []) or []) if str(a).strip()]
    if alertas:  # um único item: a geração costuma emitir dezenas de avisos e cada um não merece uma linha
        itens.append(_item(L, "alerta", "Laudo", "observacao", f"Alertas da geração ({len(alertas)})",
                           "\n".join(f"• {a}" for a in alertas[:25]) + (f"\n… e mais {len(alertas) - 25}." if len(alertas) > 25 else ""),
                           "Avisos emitidos pelo motor ao gerar a minuta"))
    return _ordenar(itens)


def montar_revisao(texto: str, *, reu: str = "", auditoria: Any = None, sentenca: Any = None) -> Dict[str, Any]:
    itens = pendencias_do_laudo(texto, auditoria, sentenca) + revisar_estilo(texto, reu=reu)
    itens = _ordenar(itens)
    contagem = {k: sum(1 for i in itens if i["severidade"] == k) for k in _PESO}
    return {"itens": itens, "resumo": {"obrigatorios": contagem["obrigatorio"], "recomendados": contagem["recomendado"], "observacoes": contagem["observacao"]}}
