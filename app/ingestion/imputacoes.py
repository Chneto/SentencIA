"""Extração de imputações e atos judiciais com referência à peça de origem."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import TYPE_CHECKING, Callable, Literal

from pydantic import BaseModel, Field

from app.core.text_utils import normalizar
from app.generation.date_formatter import parse_qualquer_data

if TYPE_CHECKING:
    from app.ingestion.laya_classifier import PecaClassificada


class EventoImputacao(BaseModel):
    tipo: Literal["recebimento", "rejeicao", "extincao"]
    fonte_id: str
    folha: str
    data_decisao: str = ""
    trecho: str


class ImputacaoExtraida(BaseModel):
    id_imputacao: str
    codigo: str
    fonte_id: str
    folha: str
    origem: Literal["denuncia", "aditamento"]
    estado: Literal["extraida", "sugestao_a_confirmar"]
    numero_fato: str = ""
    pessoa_acusada: str = ""
    data_fato: str = ""
    fato: str
    eventos: list[EventoImputacao] = Field(default_factory=list)


def localizar_tipos_penais(texto: str) -> list[tuple[int, str]]:
    """Localiza referências do catálogo em ordem textual, mantendo sua posição."""
    texto = re.sub(r"\s+", " ", texto).lower()
    # Mapeia ocorrências em ordem de aparição no texto de imputação
    candidatos: list[tuple[int, str]] = []
    pat_crimes = (
        r"(?:art(?:igo)?s?\.?\s*|(?<=,\s)|(?<=e\s)|(?<=:\s))"
        r"(147\s*-\s*[ab]|215\s*-\s*a|218\s*-\s*c|338\s*-\s*a|24\s*-\s*a|"
        r"129\s*,\s*§\s*(?:13|9)[º°]?|"
        r"140\s*,\s*§\s*2[º°]|"
        r"148\s*,\s*§\s*1[º°]|"
        r"150\s*,\s*§\s*1[º°]|"
        r"163\s*,\s*(?:par[áa]grafo\s+[úu]nico|p\.?\s*[úu]\.?|§\s*[úu]nico)|"
        r"(?:138|139|140|146|147|148|150|158|163|330|331)(?:\s*,\s*caput)?|"
        r"21\s+(?:d[ao]\s+)?(?:lcp|lei\s+das\s+contraven[çc][õo]es\s+penais|decreto[-\s]*lei\s*(?:n[º°\.]?\s*)?3\.?688))(?![\wº°])"
    )
    for m in re.finditer(pat_crimes, texto, flags=re.IGNORECASE):
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
        elif "338-a" in tok_compact:
            candidatos.append((pos, "art_338a_cp"))
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

    return candidatos




_ETIQUETA_PAGINA = re.compile(r"\[(?:p[áa]g\.|ocr\s+p[áa]gina)\s*\d+(?:\s*-\s*ocr)?\][ \t]*", re.I)
_ORDINAIS = {
    "primeiro": 1, "segundo": 2, "terceiro": 3, "quarto": 4, "quinto": 5,
    "sexto": 6, "setimo": 7, "oitavo": 8, "nono": 9, "decimo": 10,
}
_ROMANOS = {n: i for i, n in enumerate(("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"), 1)}
_NUMERO = r"(?:\d{1,3}[ºª°]?|[ivx]+|primeiro|segundo|terceiro|quarto|quinto|sexto|s[ée]timo|oitavo|nono|d[ée]cimo)"
_ROTULO_FATO = r"(?:fato(?:s|\s+delituoso)?|epis[óo]dio(?:s)?)"
_CABECALHO_FATO = re.compile(
    rf"^[ \t]*(?:do\s+)?(?:{_ROTULO_FATO}\s+(?:n[º°.]?\s*)?(?P<depois>{_NUMERO})"
    rf"|(?P<antes>{_NUMERO})\s+{_ROTULO_FATO})(?=[ \t]*(?:[:.?–—-]|$))[ \t]*(?:[:.?–—-][ \t]*)?",
    re.IGNORECASE | re.MULTILINE,
)
_FIM_NARRATIVA = re.compile(
    r"^[ \t]*(?:da\s+(?:tipifica[çc][ãa]o|capitula[çc][ãa]o)|dos\s+pedidos|requerimentos)"
    r"[ \t]*(?:[:.–—-]|$)",
    re.IGNORECASE | re.MULTILINE,
)
_DATA_ESCRITA = (
    r"(?:\d{1,2}[/.-]\d{1,2}[/.-](?:19|20)\d{2}|(?:19|20)\d{2}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}[º°]?\s+de\s+[a-zç]+\s+de\s+(?:19|20)\d{2})"
)
_MARCO_DATA = re.compile(rf"\b(?:no\s+dia|na\s+data\s+de|fato\s+(?:de|do\s+dia)|em)\s+({_DATA_ESCRITA})\b", re.I)
_DATA_REFERENCIADA = re.compile(
    rf"\b(?:fatos?|epis[óo]dios?)(?:\s+{_NUMERO}\b)?"
    rf"\s+(?:(?:ocorrid[oa]|narrad[oa]|praticad[oa])\s+)?"
    rf"(?:de|em|do\s+dia|no\s+dia)\s+({_DATA_ESCRITA})\b",
    re.I,
)
_INICIO_FATO_DATADO = re.compile(
    rf"^[ \t]*(?=(?:no\s+dia|na\s+data\s+de|em)\s+{_DATA_ESCRITA}\b)",
    re.IGNORECASE | re.MULTILINE,
)
_PESSOA_ACUSADA = re.compile(
    r"(?:\b(?:acusad[oa]|denunciad[oa]|querelad[oa])\s*:\s*|"
    r"\b(?:oferece|oferecer)\s+(?:a\s+)?(?:den[úu]ncia|queixa)\s+(?:contra|em\s+desfavor\s+de)\s+)"
    r"([^,.;\r\n]+)",
    re.IGNORECASE,
)
_ADITAMENTO = re.compile(r"^(?:(?:peticao|manifestacao) (?:de )?)?aditamento\b")
_NOMES_DELITOS = (
    (r"\bperseguicao\b", "art_147a_cp"),
    (r"\bdescumprimento (?:de |das? )?medidas? protetivas?\b", "art_24a_lmp"),
    (r"\bles(?:ao|oes) corpor(?:al|ais)\b", "art_129_p13_cp"),
    (r"\bviolacao de domicilio\b", "art_150_cp"),
    (r"\bameacas?\b", "art_147_cp"),
    (r"\bcalunia\b", "art_138_cp"),
    (r"\bdifamacao\b", "art_139_cp"),
    (r"\binjuria\b", "art_140_cp"),
)
_ACAO_JUDICIAL = re.compile(
    r"(?:^|(?<=[.;\n])|(?<= e ))[ \t]*(?:(?:ante o exposto|isso posto|pelo exposto),[ \t]*)?"
    r"(?:(?P<recebimento>recebo(?:\s*,?\s*(?:parcialmente|em parte)\s*,?)?\s+(?:a|o)\s+(?:den[úu]ncia|queixa|aditamento))"
    r"|(?P<rejeicao>rejeito(?:\s*,?\s*(?:parcialmente|em parte)\s*,?)?\s+(?:a|o)\s+(?:den[úu]ncia|queixa|aditamento))"
    r"|(?P<extincao>(?:declaro|julgo)\s+extinta\s+a\s+punibilidade|"
    r"(?:reconhe[çc]o|declaro)\s+(?:a\s+)?decad[êe]ncia|extingo\s+a\s+punibilidade))\b",
    re.IGNORECASE | re.MULTILINE,
)
_REFERENCIA_FATOS = re.compile(
    rf"(?:\b{_ROTULO_FATO}\s+(?:n[º°.]?\s*)?(?P<lista>{_NUMERO}(?:\s*(?:,|\be\b)\s*{_NUMERO})*)"
    rf"|(?P<antes>\b{_NUMERO})\s+{_ROTULO_FATO})\b(?![/\d])",
    re.IGNORECASE,
)
_CLAUSULA = re.compile(r";|\n\s*\n|(?<=[.!?])\s+(?=[A-ZÀ-Ú])")
_MENCIONA_FONTE = re.compile(r"\bID(?:\s*-\s*|\s+)([A-Z0-9]+(?:[-_][A-Z0-9]+)*)", re.I)
_CONTINUACAO_RESTRITIVA = re.compile(
    r"^[ \t]*(?:apenas|somente|exclusivamente|exceto|salvo|quanto|em rela[çc][ãa]o|"
    r"o recebimento (?:se limita|fica restrito))\b", re.I,
)
_RESTRICAO = re.compile(r"\b(?:em parte|parcialmente|apenas|somente|exclusivamente|quanto|em rela[çc][ãa]o|no tocante|restrit[ao])\b", re.I)
_EXCLUSAO = re.compile(r"\b(?:exceto|salvo|exclu[ií]d[oa]s?|[àa] exce[çc][ãa]o)\b", re.I)


def titulo_indica_aditamento(titulo: str) -> bool:
    return bool(_ADITAMENTO.match(normalizar(titulo)))


def contem_ato_judicial_sobre_imputacoes(texto: str) -> bool:
    return bool(_ACAO_JUDICIAL.search(_ETIQUETA_PAGINA.sub("", texto)))


def _numero_fato(texto: str) -> str:
    token = normalizar(texto.rstrip("ºª°"))
    numero = int(token) if token.isdecimal() else _ORDINAIS.get(token, _ROMANOS.get(token))
    return str(numero) if numero else ""


def _datas_do_fato(texto: str, padrao: re.Pattern[str] = _MARCO_DATA) -> set[str]:
    return {
        data.isoformat()
        for m in padrao.finditer(texto)
        if (data := parse_qualquer_data(m[1])) is not None
    }


def _pessoas_acusadas(texto: str, limpar_nome: Callable[[str], str]) -> set[str]:
    return {
        nome for m in _PESSOA_ACUSADA.finditer(texto)
        if (nome := limpar_nome(m[1]))
    }


def _trechos_de_fato(texto: str) -> tuple[str, list[tuple[str, str]]]:
    cabecalhos = list(_CABECALHO_FATO.finditer(texto))
    if cabecalhos:
        preambulo = texto[:cabecalhos[0].start()]
        trechos = []
        for i, cabecalho in enumerate(cabecalhos):
            fim = cabecalhos[i + 1].start() if i + 1 < len(cabecalhos) else len(texto)
            trecho = texto[cabecalho.end():fim]
            resumo = _FIM_NARRATIVA.search(trecho)
            if resumo:
                trecho = trecho[:resumo.start()]
            trechos.append((_numero_fato(cabecalho["depois"] or cabecalho["antes"]), trecho))
        return preambulo, trechos

    datados = list(_INICIO_FATO_DATADO.finditer(texto))
    if len(datados) > 1:
        trechos = []
        for i, inicio in enumerate(datados):
            fim = datados[i + 1].start() if i + 1 < len(datados) else len(texto)
            trecho = texto[inicio.start():fim]
            resumo = _FIM_NARRATIVA.search(trecho)
            trechos.append(("", trecho[:resumo.start()] if resumo else trecho))
        return texto[:datados[0].start()], trechos

    return "", [("", texto)]


def _sugestoes_nominais(trecho: str) -> list[str]:
    texto = normalizar(trecho)
    sugestoes = [codigo for expressao, codigo in _NOMES_DELITOS if re.search(expressao, texto)]
    if "art_147a_cp" in sugestoes and "art_147_cp" in sugestoes:
        sugestoes.remove("art_147_cp")
    return sugestoes


def _imputacoes_da_peca(
    peca: PecaClassificada, origem: Literal["denuncia", "aditamento"], limpar_nome: Callable[[str], str],
) -> list[ImputacaoExtraida]:
    preambulo, trechos = _trechos_de_fato(_ETIQUETA_PAGINA.sub("", peca.conteudo))
    pessoas_preambulo = _pessoas_acusadas(preambulo, limpar_nome)
    numeros = Counter(numero for numero, _ in trechos if numero)
    itens: list[ImputacaoExtraida] = []
    for numero, trecho in trechos:
        codigos = list(dict.fromkeys(codigo for _, codigo in localizar_tipos_penais(trecho)))
        pessoas = _pessoas_acusadas(trecho, limpar_nome) or pessoas_preambulo
        pessoa = next(iter(pessoas)) if len(pessoas) == 1 else ""
        datas = _datas_do_fato(trecho)
        data = next(iter(datas)) if len(datas) == 1 else ""
        estado = "extraida" if codigos else "sugestao_a_confirmar"
        for codigo in codigos or _sugestoes_nominais(trecho):
            tem_referencia = peca.doc_id and peca.doc_id != "ID-0000"
            ancora = f"fato:{numero}" if tem_referencia and numero and numeros[numero] == 1 else normalizar(trecho)
            chave = f"{peca.doc_id}\x1f{origem}\x1f{ancora}\x1f{codigo}".encode("utf-8")
            id_imputacao = f"IMP-{hashlib.sha256(chave).hexdigest()[:20]}"
            itens.append(ImputacaoExtraida(
                id_imputacao=id_imputacao, codigo=codigo, fonte_id=peca.doc_id, folha=peca.folha,
                origem=origem, estado=estado, numero_fato=numero, pessoa_acusada=pessoa,
                data_fato=data, fato=" ".join(trecho.split())[:800],
            ))
    return itens


def _numeros_mencionados(trecho: str) -> set[str]:
    tokens = []
    for referencia in _REFERENCIA_FATOS.finditer(trecho):
        tokens.extend(re.split(r"\s*,\s*|\s+e\s+", referencia["lista"], flags=re.I)
                      if referencia["lista"] else [referencia["antes"]])
    return {n for token in tokens if (n := _numero_fato(token))}


def _alvos_evento(evento: EventoImputacao, itens: list[ImputacaoExtraida]) -> list[ImputacaoExtraida]:
    trecho = evento.trecho
    if _EXCLUSAO.search(trecho):
        return []
    fontes = {f"ID-{m[1].upper()}" for m in _MENCIONA_FONTE.finditer(trecho)}
    if len(fontes) > 1:
        return []
    candidatos = [item for item in itens if item.estado == "extraida"]
    if fontes:
        candidatos = [item for item in candidatos if item.fonte_id.upper() in fontes]
    trecho_normalizado = normalizar(trecho)
    pessoas = {
        pessoa for item in candidatos if (pessoa := normalizar(item.pessoa_acusada))
        and re.search(rf"(?<!\w){re.escape(pessoa)}(?!\w)", trecho_normalizado)
    }
    if pessoas:
        candidatos = [item for item in candidatos if normalizar(item.pessoa_acusada) in pessoas]
    if evento.tipo in ("recebimento", "rejeicao"):
        origem = "aditamento" if re.search(r"\baditamento\b", trecho, re.I) else "denuncia"
        candidatos = [item for item in candidatos if item.origem == origem]

    numeros = _numeros_mencionados(trecho)
    codigos = {codigo for _, codigo in localizar_tipos_penais(trecho)}
    datas = _datas_do_fato(trecho, _DATA_REFERENCIADA)
    if datas:
        candidatos = [item for item in candidatos if item.data_fato in datas]
    if numeros:
        candidatos = [item for item in candidatos if item.numero_fato in numeros]
    if codigos:
        candidatos = [item for item in candidatos if item.codigo in codigos]
    if not candidatos:
        return []
    if numeros:
        for numero in numeros:
            do_fato = [i for i in candidatos if i.numero_fato == numero]
            if not do_fato or len({(i.fonte_id, i.fato) for i in do_fato}) != 1:
                return []
        return candidatos if not codigos or {i.codigo for i in candidatos} == codigos else []
    if codigos:
        if Counter(item.codigo for item in candidatos) == Counter(codigos):
            return candidatos
        return []
    if datas:
        return candidatos if Counter(item.data_fato for item in candidatos) == Counter(datas) else []
    if evento.tipo != "extincao" and (pessoas or not _RESTRICAO.search(trecho)):
        if len({item.fonte_id for item in candidatos}) == 1:
            return candidatos
    return []


def extrair_imputacoes(
    pecas: list[PecaClassificada], limpar_nome: Callable[[str], str],
) -> tuple[list[ImputacaoExtraida], list[EventoImputacao]]:
    """Relaciona apenas referências inequívocas; atos ambíguos aguardam conferência."""
    por_id: dict[str, ImputacaoExtraida] = {}
    for peca in pecas:
        titulo = normalizar(peca.titulo)
        if titulo_indica_aditamento(peca.titulo):
            origem = "aditamento"
        elif peca.categoria == "DENUNCIA" or titulo in ("denuncia", "queixa-crime", "queixa crime"):
            origem = "denuncia"
        else:
            continue
        for item in _imputacoes_da_peca(peca, origem, limpar_nome):
            por_id.setdefault(item.id_imputacao, item)

    itens = list(por_id.values())
    pendentes: list[EventoImputacao] = []
    if not itens:
        return itens, pendentes
    for peca in pecas:
        titulo = normalizar(peca.titulo)
        if peca.categoria != "RECEBIMENTO_DENUNCIA" and not re.match(r"^(?:decisao|sentenca|despacho)\b", titulo):
            continue
        data = parse_qualquer_data(peca.data_peca)
        texto = _ETIQUETA_PAGINA.sub("", peca.conteudo)
        comandos = list(_ACAO_JUDICIAL.finditer(texto))
        for i, comando in enumerate(comandos):
            fim = comandos[i + 1].start() if i + 1 < len(comandos) else len(texto)
            bruto = texto[comando.start():fim].strip()
            partes = _CLAUSULA.split(bruto, maxsplit=1)
            clausula = partes[0]
            if len(partes) > 1 and _CONTINUACAO_RESTRITIVA.match(partes[1]):
                clausula += " " + _CLAUSULA.split(partes[1], maxsplit=1)[0]
            evento = EventoImputacao(
                tipo=comando.lastgroup, fonte_id=peca.doc_id, folha=peca.folha,
                data_decisao=data.isoformat() if data else "", trecho=" ".join(clausula.split()),
            )
            alvos = _alvos_evento(evento, itens)
            if alvos:
                for item in alvos:
                    item.eventos.append(evento)
            else:
                pendentes.append(evento)
    return itens, pendentes
