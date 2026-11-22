import datetime
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional, Union

MESES_PT = {
    1: "janeiro",
    2: "fevereiro",
    3: "março",
    4: "abril",
    5: "maio",
    6: "junho",
    7: "julho",
    8: "agosto",
    9: "setembro",
    10: "outubro",
    11: "novembro",
    12: "dezembro",
}

MESES_REVERSO = {v.lower(): k for k, v in MESES_PT.items()}

UNIDADES_PT = ["", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove"]
DEZ_A_DEZENOVE_PT = [
    "dez", "onze", "doze", "treze", "quatorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove"
]
DEZENAS_PT = ["", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa"]
CENTENAS_PT = [
    "", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos", "seiscentos", "setecentos", "oitocentos", "novecentos"
]


def _extenso_menor_1000(n: int) -> str:
    """Converte inteiros de 1 a 999 em palavras no português brasileiro culto."""
    if n <= 0:
        return ""
    if n == 100:
        return "cem"
    partes = []
    c = n // 100
    resto = n % 100
    if c > 0:
        partes.append(CENTENAS_PT[c])
    if resto > 0:
        if 10 <= resto <= 19:
            partes.append(DEZ_A_DEZENOVE_PT[resto - 10])
        else:
            d = resto // 10
            u = resto % 10
            if d > 0:
                partes.append(DEZENAS_PT[d])
            if u > 0:
                partes.append(UNIDADES_PT[u])
    return " e ".join(partes)


def numero_por_extenso(n: int) -> str:
    """Cardinal por extenso (0 a 999.999.999), ex.: 10 -> "dez", 1250 -> "mil duzentos e cinquenta"."""
    n = int(n)
    if n == 0:
        return "zero"
    if n < 0:
        return f"menos {numero_por_extenso(-n)}"
    milhoes, resto = divmod(n, 1_000_000)
    milhares, unidades = divmod(resto, 1_000)
    grupos = []
    if milhoes:
        grupos.append("um milhão" if milhoes == 1 else f"{_extenso_menor_1000(milhoes)} milhões")
    if milhares:
        grupos.append("mil" if milhares == 1 else f"{_extenso_menor_1000(milhares)} mil")
    if unidades:
        grupos.append(_extenso_menor_1000(unidades))
    if len(grupos) == 1:
        return grupos[0]
    # "e" antes do último grupo apenas quando ele é menor que 100 ou centena exata (norma culta).
    ultimo = unidades if unidades else (milhares if milhares else 0)
    conector = " e " if (ultimo < 100 or ultimo % 100 == 0) else " "
    return " ".join(grupos[:-1]) + conector + grupos[-1]


def valor_por_extenso(valor: Union[float, int]) -> str:
    """
    Converte quantias monetárias em reais para sua representação textual formal por extenso
    conforme as normas cultas da Língua Portuguesa e padrões do Judiciário brasileiro.
    Exemplos:
    - 3000.0 -> "três mil reais"
    - 5000.0 -> "cinco mil reais"
    - 12500.75 -> "doze mil e quinhentos reais e setenta e cinco centavos"
    - 1000000.0 -> "um milhão de reais"
    """
    # Trabalha em centavos inteiros (Decimal) para evitar erros de ponto flutuante
    # (ex.: 2.995 virava "dois reais e cem centavos").
    total_centavos = int(
        (Decimal(str(float(valor))) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    total_centavos = abs(total_centavos)
    inteiro, centavos = divmod(total_centavos, 100)

    if inteiro == 0 and centavos == 0:
        return "zero real"

    partes_extenso = []
    if inteiro > 0:
        milhoes = inteiro // 1_000_000
        milhares = (inteiro % 1_000_000) // 1_000
        unidades = inteiro % 1_000

        grupos = []
        if milhoes > 0:
            grupos.append("um milhão" if milhoes == 1 else f"{_extenso_menor_1000(milhoes)} milhões")
        if milhares > 0:
            grupos.append("mil" if milhares == 1 else f"{_extenso_menor_1000(milhares)} mil")
        if unidades > 0:
            grupos.append(_extenso_menor_1000(unidades))

        texto_inteiro = " e ".join(grupos)
        if milhoes > 0 and milhares == 0 and unidades == 0:
            moeda = "de real" if inteiro == 1 else "de reais"
        else:
            moeda = "real" if inteiro == 1 else "reais"
        partes_extenso.append(f"{texto_inteiro} {moeda}")

    if centavos > 0:
        texto_cent = _extenso_menor_1000(centavos)
        moeda_cent = "centavo" if centavos == 1 else "centavos"
        partes_extenso.append(f"{texto_cent} {moeda_cent}")

    return " e ".join(partes_extenso)


def parse_qualquer_data(data_val: Union[str, datetime.date, datetime.datetime, None]) -> Optional[datetime.date]:
    """
    Interpreta datas em múltiplos formatos comumente encontrados nos autos do PJe e textos forenses:
    - Formal extenso: "14 de fevereiro de 2025", "1º de março de 2024"
    - Padrão brasileiro: "14/02/2025", "14-02-2025", "14.02.2025"
    - Padrão ISO: "2025-02-14", "2025/02/14" (com ou sem carimbo de hora)
    """
    if not data_val:
        return None
    if isinstance(data_val, datetime.datetime):
        return data_val.date()
    if isinstance(data_val, datetime.date):
        return data_val

    s = str(data_val).strip()

    # 1. Extenso formal judiciário: "14 de fevereiro de 2025" ou "1º de março de 2024"
    m_ext = re.match(r"^(\d{1,2}|1[º°ª])\s+de\s+([a-zç]+)\s+de\s+(\d{4})", s, flags=re.IGNORECASE)
    if m_ext:
        dia_raw = re.sub(r"[º°ª]", "", m_ext.group(1))
        mes_nome = m_ext.group(2).lower()
        ano = int(m_ext.group(3))
        if mes_nome in MESES_REVERSO:
            try:
                return datetime.date(ano, MESES_REVERSO[mes_nome], int(dia_raw))
            except ValueError:
                pass

    # 2. Padrão ISO: AAAA-MM-DD ou AAAA/MM/DD
    m_iso = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m_iso:
        try:
            return datetime.date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
        except ValueError:
            pass

    # 3. Padrão brasileiro: DD/MM/AAAA, DD-MM-AAAA ou DD.MM.AAAA
    m_br = re.match(r"^(\d{1,2})[/\.-](\d{1,2})[/\.-](\d{4})", s)
    if m_br:
        try:
            return datetime.date(int(m_br.group(3)), int(m_br.group(2)), int(m_br.group(1)))
        except ValueError:
            pass

    return None


def formatar_data_br(
    data_val: Union[str, datetime.date, datetime.datetime, None],
    por_extenso: bool = False,
    com_zero_a_esquerda: bool = True,
) -> str:
    """
    Formata data estritamente segundo as normas padrão da Língua Portuguesa no Brasil:
    - Técnico (mascaramento oficial para campos técnicos): DD/MM/AAAA (ex.: 14/02/2025, 11/11/2026)
    - Extenso formal judiciário/forense para menções textuais: D de [mês] de AAAA (ex.: 14 de fevereiro de 2025; 1º de março de 2024)
    """
    dt = parse_qualquer_data(data_val)
    if not dt:
        return str(data_val or "")
    if por_extenso:
        dia_str = "1º" if dt.day == 1 else str(dt.day)
        return f"{dia_str} de {MESES_PT[dt.month]} de {dt.year}"
    if com_zero_a_esquerda:
        return f"{dt.day:02d}/{dt.month:02d}/{dt.year:04d}"
    return f"{dt.day}/{dt.month}/{dt.year}"


def mascarar_e_padronizar_datas_texto(texto: str) -> str:
    """
    Varre o texto da sentença e converte qualquer data residual em padrão internacional/ISO (AAAA-MM-DD ou AAAA/MM/DD)
    ou variações pontuadas/hifenizadas para o formato brasileiro estrito DD/MM/AAAA, preservando números de processos
    unificados (CNJ NNNNNNN-DD.AAAA.J.TR.OOOO) e números de leis (ex.: 11.340/2006).
    """
    if not texto:
        return ""

    # 1. Converte ISO AAAA-MM-DD ou AAAA/MM/DD
    def _repl_iso(m: re.Match) -> str:
        ano = int(m.group(1))
        mes = int(m.group(2))
        dia = int(m.group(3))
        try:
            dt = datetime.date(ano, mes, dia)
            return f"{dt.day:02d}/{dt.month:02d}/{dt.year:04d}"
        except ValueError:
            return m.group(0)

    res = re.sub(r"\b(19\d{2}|20\d{2})[-/](\d{2})[-/](\d{2})\b", _repl_iso, texto)

    # 2. Converte DD-MM-AAAA para DD/MM/AAAA
    def _repl_dash(m: re.Match) -> str:
        dia = int(m.group(1))
        mes = int(m.group(2))
        ano = int(m.group(3))
        try:
            dt = datetime.date(ano, mes, dia)
            return f"{dt.day:02d}/{dt.month:02d}/{dt.year:04d}"
        except ValueError:
            return m.group(0)

    res = re.sub(r"\b(\d{2})-(\d{2})-(19\d{2}|20\d{2})\b", _repl_dash, res)

    return res


def converter_mencoes_textuais_para_extenso(texto: str) -> str:
    """
    Substitui menções de datas que ocorrem após preposições narrativas clássicas
    ('no dia', 'em', 'proferida em', 'realizada em', 'recebida em', 'intimado em', 'desde')
    pela sua forma formal em português culto por extenso ('DD de [mês] de AAAA'),
    preservando as datas em referências técnicas e parênteses probatórios.
    """
    if not texto:
        return ""

    padrao = re.compile(
        r"\b(no\s+dia|realizada\s+em|proferida\s+em|recebida\s+em|intimado\s+em|ocorreram\s+em|ocorrido\s+em|desde)\s+(\d{1,2})/(\d{1,2})/(\d{4})\b",
        flags=re.IGNORECASE,
    )

    def _repl(m: re.Match) -> str:
        prep = m.group(1)
        dia = int(m.group(2))
        mes = int(m.group(3))
        ano = int(m.group(4))
        try:
            dt = datetime.date(ano, mes, dia)
            dia_str = "1º" if dt.day == 1 else str(dt.day)
            return f"{prep} {dia_str} de {MESES_PT[dt.month]} de {dt.year}"
        except ValueError:
            return m.group(0)

    return padrao.sub(_repl, texto)


def formatar_moeda_br(valor: Union[float, int]) -> str:
    """Formata quantias em reais no padrão oficial brasileiro (R$ 3.000,00)."""
    val = float(valor)
    return f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_moeda_com_extenso(valor: Union[float, int]) -> str:
    """
    Formata valor monetário com a representação numérica padrão e a descrição por extenso em parênteses.
    Exemplo: 3000.0 -> "R$ 3.000,00 (três mil reais)"
    """
    return f"{formatar_moeda_br(valor)} ({valor_por_extenso(valor)})"
