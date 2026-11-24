import datetime
from typing import Dict, List, Literal
from pydantic import BaseModel

from app.core.errors import DadosInsuficientesError


class PenaAnosMesesDias(BaseModel):
    """Quantum de pena expresso em anos, meses e dias.

    Convenção de unidades (documentada e uniforme em todo o motor de dosimetria):
    para o CÁLCULO DA PENA usa-se o ano penal de 360 dias e o mês de 30 dias
    (``to_total_days``/``from_total_days``), o que torna exatas as frações de 1/6,
    1/8 etc. sobre anos e meses. Já os PRAZOS PRESCRICIONAIS são contados pelo
    calendário comum (art. 10 do CP), somando anos/meses à data de início — ver
    ``app.legal_engine.dosimetria_calculator.somar_meses``.
    """

    anos: int = 0
    meses: int = 0
    dias: int = 0

    def to_total_days(self) -> int:
        """Total de dias pela convenção penal (ano = 360 dias, mês = 30 dias)."""
        return (self.anos * 360) + (self.meses * 30) + self.dias

    @classmethod
    def from_total_days(cls, total_days: int) -> "PenaAnosMesesDias":
        total_days = max(0, int(round(total_days)))
        anos = total_days // 360
        rem = total_days % 360
        meses = rem // 30
        dias = rem % 30
        return cls(anos=anos, meses=meses, dias=dias)

    def format_extenso(self) -> str:
        partes = []
        if self.anos > 0:
            partes.append(f"{self.anos} ({_num_extenso(self.anos)}) {'ano' if self.anos == 1 else 'anos'}")
        if self.meses > 0:
            partes.append(f"{self.meses} ({_num_extenso(self.meses)}) {'mês' if self.meses == 1 else 'meses'}")
        if self.dias > 0 or not partes:
            partes.append(f"{self.dias} ({_num_extenso(self.dias)}) {'dia' if self.dias == 1 else 'dias'}")
        if len(partes) == 1:
            return partes[0]
        return ", ".join(partes[:-1]) + " e " + partes[-1]

    def format_curto(self) -> str:
        partes = []
        if self.anos:
            partes.append(f"{self.anos}a")
        if self.meses:
            partes.append(f"{self.meses}m")
        if self.dias or not partes:
            partes.append(f"{self.dias}d")
        return " ".join(partes)


def _num_extenso(n: int) -> str:
    mapa = {
        0: "zero", 1: "um", 2: "dois", 3: "três", 4: "quatro", 5: "cinco",
        6: "seis", 7: "sete", 8: "oito", 9: "nove", 10: "dez",
        11: "onze", 12: "doze", 13: "treze", 14: "quatorze", 15: "quinze",
        16: "dezesseis", 17: "dezessete", 18: "dezoito", 19: "dezenove", 20: "vinte",
        21: "vinte e um", 22: "vinte e dois", 23: "vinte e três", 24: "vinte e quatro",
        25: "vinte e cinco", 26: "vinte e seis", 27: "vinte e sete", 28: "vinte e oito",
        29: "vinte e nove", 30: "trinta",
    }
    return mapa.get(n, str(n))


class PenalTypeSpec(BaseModel):
    code: str
    nome: str
    artigo_formatado: str
    lei_vigente: str
    min_pena: PenaAnosMesesDias
    max_pena: PenaAnosMesesDias
    especie_pena: Literal["reclusao", "detencao", "prisao_simples"]
    acao_penal: Literal[
        "publica_incondicionada",
        "publica_condicionada_representacao",
        "privada",
    ]
    has_builtin_domestic_violence_element: bool
    has_violence_or_grave_threat: bool
    tem_multa_alternativa: bool = False
    tem_multa_cumulativa: bool = False
    # Tipos que admitem modalidades COM e SEM violência/grave ameaça (147-A, 147-B, 150, § 1º,
    # 163, p.u.): a vedação da Súmula 588/STJ depende da modalidade praticada, que é decisão do
    # juízo (``CrimeDosimetriaInput.violencia_ou_grave_ameaca``).
    violencia_ou_grave_ameaca_ambigua: bool = False
    # Crimes contra a honra (138/139/140): a partir da Lei 14.994/2024 admitem a majorante do art. 141, § 3º.
    admite_aumento_art141_p3: bool = False
    admite_aumento_art24a_p4: bool = False
    observacao_intertemporal: str


# Data de entrada em vigor da Lei nº 14.994/2024 (Pacote Antifeminicídio): 10/10/2024
DATA_VIGENCIA_LEI_14994_2024 = datetime.date(2024, 10, 10)
# Lei nº 14.132/2021 (cria o art. 147-A do CP): publicada e vigente em 01/04/2021
DATA_VIGENCIA_LEI_14132_2021 = datetime.date(2021, 4, 1)
# Lei nº 14.188/2021 (cria o art. 129, § 13, e o art. 147-B do CP): publicada e vigente em 29/07/2021
DATA_VIGENCIA_LEI_14188_2021 = datetime.date(2021, 7, 29)

DATA_VIGENCIA_LEI_13641_2018 = datetime.date(2018, 4, 4)
DATA_VIGENCIA_LEI_15280_2025 = datetime.date(2025, 12, 8)
DATA_VIGENCIA_LEI_15383_2026 = datetime.date(2026, 4, 10)
DATA_VIGENCIA_LEI_15455_2026 = datetime.date(2026, 7, 2)


def _aviso_fato_anterior_ao_tipo(lei: str, vigencia: datetime.date) -> str:
    return (
        f" ATENÇÃO: o fato é anterior à vigência da {lei} ({vigencia.strftime('%d/%m/%Y')}), que criou este tipo penal; "
        "a conduta não pode ser punida por ele (art. 1º do CP e art. 5º, XXXIX e XL, da CF) — verificar a tipificação."
    )


class TipoPenalInexistenteNaDataError(DadosInsuficientesError):
    """O tipo penal imputado ainda não existia na data do fato (art. 1º do CP; art. 5º, XXXIX, da CF)."""

    def __init__(self, code: str, vigencia: datetime.date, lei: str, data_fato: datetime.date) -> None:
        self.crime_code = code
        self.vigencia = vigencia
        super().__init__(
            [f"crimes_config.{code}.veredicto"],
            f"O tipo penal {code} foi criado pela {lei} (vigência em {vigencia.strftime('%d/%m/%Y')}) e não existia na data do fato "
            f"({data_fato.strftime('%d/%m/%Y')}): a conduta era atípica (art. 1º do CP e art. 5º, XXXIX, da CF). Não há pena a calcular; "
            "informe o veredicto 'absolver' (art. 386, III, do CPP) ou a desclassificação para tipo vigente à época.",
        )


# Tipos criados por lei posterior (código -> (vigência, lei)).
_TIPOS_CRIADOS_POR_LEI = {
    "art_24a_lmp": (DATA_VIGENCIA_LEI_13641_2018, "Lei nº 13.641/2018"),
    "art_338a_cp": (DATA_VIGENCIA_LEI_15280_2025, "Lei nº 15.280/2025"),
    "art_147a_cp": (DATA_VIGENCIA_LEI_14132_2021, "Lei nº 14.132/2021"),
    "art_147b_cp": (DATA_VIGENCIA_LEI_14188_2021, "Lei nº 14.188/2021"),
}


def verificar_tipo_existente_na_data(code: str, data_fato: datetime.date) -> None:
    """Levanta ``TipoPenalInexistenteNaDataError`` se o tipo não existia na data do fato."""
    criado = _TIPOS_CRIADOS_POR_LEI.get(code)
    if criado and data_fato < criado[0]:
        raise TipoPenalInexistenteNaDataError(code, criado[0], criado[1], data_fato)


def get_penal_type_for_date(
    code: str, data_fato: datetime.date, *, ameaca_qualificada: bool = False
) -> PenalTypeSpec:
    """Retorna a especificação exata do tipo penal respeitando o Direito Intertemporal (Tempus Regit Actum - art. 5º, XL, CF e art. 2º CP)."""
    pos_pacote_antifeminicidio = data_fato >= DATA_VIGENCIA_LEI_14994_2024

    # Art. 129, § 13, só existe a partir da Lei nº 14.188/2021; fatos anteriores seguem o § 9º
    # (detenção de 3 meses a 3 anos), vedada a retroatividade da lex gravior.
    if code == "art_129_p13_cp" and data_fato < DATA_VIGENCIA_LEI_14188_2021:
        spec_p9 = get_penal_type_for_date("art_129_p9_cp", data_fato)
        return spec_p9.model_copy(
            update={
                "code": code,
                "observacao_intertemporal": (
                    "Fato anterior a 29/07/2021 (Lei nº 14.188/2021, que criou o § 13 do art. 129): aplica-se o art. 129, § 9º, do CP "
                    "(detenção de 3 meses a 3 anos), vedada a retroatividade da lei mais gravosa (art. 5º, XL, CF)."
                ),
            }
        )

    if code == "art_129_p9_cp":
        obs_p9 = (
            "Art. 129, § 9º: reclusão de 2 a 5 anos desde 10/10/2024 (Lei nº 14.994/2024)."
            if pos_pacote_antifeminicidio
            else "Antes de 10/10/2024: detenção de 3 meses a 3 anos (art. 5º, XL, CF)."
        )
        lei = "Código Penal, art. 129, § 9º"
        if data_fato >= DATA_VIGENCIA_LEI_15455_2026:
            lei = "Lei nº 15.455/2026 (redação) e Lei nº 14.994/2024 (pena)"
            obs_p9 += " Desde 02/07/2026, a redação inclui relações de trabalho doméstico (Lei nº 15.455/2026)."
        elif pos_pacote_antifeminicidio:
            lei = "Lei nº 14.994/2024"
        return PenalTypeSpec(
            code=code,
            nome="Lesão Corporal em Contexto de Violência Doméstica e Familiar",
            artigo_formatado="art. 129, § 9º, do Código Penal",
            lei_vigente=lei,
            min_pena=PenaAnosMesesDias(anos=2) if pos_pacote_antifeminicidio else PenaAnosMesesDias(meses=3),
            max_pena=PenaAnosMesesDias(anos=5 if pos_pacote_antifeminicidio else 3),
            especie_pena="reclusao" if pos_pacote_antifeminicidio else "detencao",
            acao_penal="publica_incondicionada",
            has_builtin_domestic_violence_element=True,
            has_violence_or_grave_threat=True,
            observacao_intertemporal=obs_p9,
        )

    if code == "art_129_p13_cp":
        if pos_pacote_antifeminicidio:
            return PenalTypeSpec(
                code=code,
                nome="Lesão Corporal no Contexto de Violência Doméstica / Contra a Mulher",
                artigo_formatado="art. 129, § 13, do Código Penal (com a redação dada pela Lei nº 14.994/2024)",
                lei_vigente="Lei nº 14.994/2024 (vigente na data do fato)",
                min_pena=PenaAnosMesesDias(anos=2, meses=0, dias=0),
                max_pena=PenaAnosMesesDias(anos=5, meses=0, dias=0),
                especie_pena="reclusao",
                acao_penal="publica_incondicionada",
                has_builtin_domestic_violence_element=True,
                has_violence_or_grave_threat=True,
                observacao_intertemporal="Fato praticado a partir de 10/10/2024: aplica-se a pena de reclusão de 2 a 5 anos (Lei nº 14.994/2024) e Súmula 542/STJ.",
            )
        else:
            return PenalTypeSpec(
                code=code,
                nome="Lesão Corporal contra a Mulher por Razões da Condição do Sexo Feminino",
                artigo_formatado="art. 129, § 13, do Código Penal (redação dada pela Lei nº 14.188/2021, anterior à Lei nº 14.994/2024)",
                lei_vigente="Lei nº 14.188/2021 (irretroatividade da Lei nº 14.994/2024)",
                min_pena=PenaAnosMesesDias(anos=1, meses=0, dias=0),
                max_pena=PenaAnosMesesDias(anos=4, meses=0, dias=0),
                especie_pena="reclusao",
                acao_penal="publica_incondicionada",
                has_builtin_domestic_violence_element=True,
                has_violence_or_grave_threat=True,
                observacao_intertemporal="Fato anterior a 10/10/2024: aplica-se a pena de reclusão de 1 a 4 anos vigente à época do fato (vedação da lex gravior retroativa).",
            )

    if code == "art_147_cp":
        if ameaca_qualificada:
            if not pos_pacote_antifeminicidio:
                raise TipoPenalInexistenteNaDataError(code, DATA_VIGENCIA_LEI_14994_2024, "Lei nº 14.994/2024 (§ 1º do art. 147)", data_fato)
            return PenalTypeSpec(
                code=code,
                nome="Ameaça contra a Mulher por Razões da Condição do Sexo Feminino",
                artigo_formatado="art. 147, caput e §§ 1º e 2º, do Código Penal (com a redação dada pela Lei nº 14.994/2024)",
                lei_vigente="Lei nº 14.994/2024 (pena em dobro e ação pública incondicionada)",
                min_pena=PenaAnosMesesDias(anos=0, meses=2, dias=0),
                max_pena=PenaAnosMesesDias(anos=1, meses=0, dias=0),
                especie_pena="detencao",
                acao_penal="publica_incondicionada",
                has_builtin_domestic_violence_element=True,
                has_violence_or_grave_threat=True,
                tem_multa_alternativa=True,
                observacao_intertemporal="Art. 147, § 1º, aplicável desde 10/10/2024: pena em dobro (2 meses a 1 ano de detenção), ação penal pública incondicionada e vedação de bis in idem com art. 61, II, 'f', CP.",
            )
        return PenalTypeSpec(
            code=code,
            nome="Ameaça (caput)",
            # A agravante do art. 61, II, 'f', só entra na capitulação quando efetivamente aplicada
            # (``calcular_dosimetria_crime`` anexa "c/c art. 61, II, f" a ``agravantes_ativas``).
            artigo_formatado="art. 147, caput, do Código Penal",
            lei_vigente="Código Penal, art. 147, caput",
            min_pena=PenaAnosMesesDias(anos=0, meses=1, dias=0),
            max_pena=PenaAnosMesesDias(anos=0, meses=6, dias=0),
            especie_pena="detencao",
            acao_penal="publica_condicionada_representacao",
            has_builtin_domestic_violence_element=False,
            has_violence_or_grave_threat=True,
            tem_multa_alternativa=True,
            observacao_intertemporal="Art. 147, caput: detenção de 1 a 6 meses ou multa. A ação depende de representação.",
        )

    if code == "art_338a_cp":
        return PenalTypeSpec(
            code=code,
            nome="Descumprimento de Medida Protetiva de Urgência (Código Penal)",
            artigo_formatado="art. 338-A do Código Penal",
            lei_vigente="Lei nº 15.280/2025",
            min_pena=PenaAnosMesesDias(anos=2),
            max_pena=PenaAnosMesesDias(anos=5),
            especie_pena="reclusao",
            acao_penal="publica_incondicionada",
            has_builtin_domestic_violence_element=False,
            has_violence_or_grave_threat=False,
            tem_multa_cumulativa=True,
            observacao_intertemporal=(
                _aviso_fato_anterior_ao_tipo("Lei nº 15.280/2025", DATA_VIGENCIA_LEI_15280_2025)
                if data_fato < DATA_VIGENCIA_LEI_15280_2025
                else "Vigente desde 08/12/2025. O enquadramento deve corresponder à medida e à imputação confirmadas pelo juízo."
            ),
        )

    if code == "art_24a_lmp":
        if pos_pacote_antifeminicidio:
            return PenalTypeSpec(
                code=code,
                nome="Descumprimento de Medida Protetiva de Urgência",
                artigo_formatado="art. 24-A da Lei nº 11.340/2006 (com a redação dada pela Lei nº 14.994/2024)",
                lei_vigente="Lei nº 14.994/2024",
                min_pena=PenaAnosMesesDias(anos=2, meses=0, dias=0),
                max_pena=PenaAnosMesesDias(anos=5, meses=0, dias=0),
                especie_pena="reclusao",
                acao_penal="publica_incondicionada",
                has_builtin_domestic_violence_element=True,
                has_violence_or_grave_threat=False,
                tem_multa_cumulativa=True,
                admite_aumento_art24a_p4=data_fato >= DATA_VIGENCIA_LEI_15383_2026,
                observacao_intertemporal="Fato posterior a 10/10/2024: pena de reclusão de 2 a 5 anos e multa (Lei nº 14.994/2024). Pressupõe ciência prévia da decisão judicial (art. 24-A da Lei nº 11.340/2006).",
            )
        return PenalTypeSpec(
            code=code,
            nome="Descumprimento de Medida Protetiva de Urgência",
            artigo_formatado="art. 24-A da Lei nº 11.340/2006 (redação dada pela Lei nº 13.641/2018)",
            lei_vigente="Lei nº 13.641/2018 (irretroatividade da Lei nº 14.994/2024)",
            min_pena=PenaAnosMesesDias(anos=0, meses=3, dias=0),
            max_pena=PenaAnosMesesDias(anos=2, meses=0, dias=0),
            especie_pena="detencao",
            acao_penal="publica_incondicionada",
            has_builtin_domestic_violence_element=True,
            has_violence_or_grave_threat=False,
            observacao_intertemporal=(
                _aviso_fato_anterior_ao_tipo("Lei nº 13.641/2018", DATA_VIGENCIA_LEI_13641_2018)
                if data_fato < DATA_VIGENCIA_LEI_13641_2018
                else "De 04/04/2018 até 09/10/2024: detenção de 3 meses a 2 anos (Lei nº 13.641/2018)."
            ),
        )

    if code == "art_147a_cp":
        obs_147a = (
            "Pena de reclusão de 6 meses a 2 anos e multa. A causa de aumento de 1/2 (metade) do § 1º, II (crime contra mulher por razões "
            "da condição de sexo feminino) é aplicada na 3ª fase quando descrita na denúncia (parâmetro explícito na dosimetria)."
        )
        if data_fato < DATA_VIGENCIA_LEI_14132_2021:
            obs_147a += _aviso_fato_anterior_ao_tipo("Lei nº 14.132/2021", DATA_VIGENCIA_LEI_14132_2021)
        return PenalTypeSpec(
            code=code,
            nome="Perseguição (Stalking) contra a Mulher",
            artigo_formatado="art. 147-A, caput e § 1º, inciso II, do Código Penal",
            lei_vigente="Lei nº 14.132/2021",
            min_pena=PenaAnosMesesDias(anos=0, meses=6, dias=0),
            max_pena=PenaAnosMesesDias(anos=2, meses=0, dias=0),
            especie_pena="reclusao",
            acao_penal="publica_condicionada_representacao",
            has_builtin_domestic_violence_element=True,
            has_violence_or_grave_threat=True,
            violencia_ou_grave_ameaca_ambigua=True,
            tem_multa_cumulativa=True,
            observacao_intertemporal=obs_147a,
        )

    if code == "art_147b_cp":
        obs_147b = "Tipo penal específico de violência psicológica contra a mulher (reclusão de 6 meses a 2 anos e multa); vedado bis in idem com art. 61, II, 'f', do CP."
        if data_fato < DATA_VIGENCIA_LEI_14188_2021:
            obs_147b += _aviso_fato_anterior_ao_tipo("Lei nº 14.188/2021", DATA_VIGENCIA_LEI_14188_2021)
        return PenalTypeSpec(
            code=code,
            nome="Violência Psicológica contra a Mulher",
            artigo_formatado="art. 147-B do Código Penal",
            lei_vigente="Lei nº 14.188/2021",
            min_pena=PenaAnosMesesDias(anos=0, meses=6, dias=0),
            max_pena=PenaAnosMesesDias(anos=2, meses=0, dias=0),
            especie_pena="reclusao",
            acao_penal="publica_incondicionada",
            has_builtin_domestic_violence_element=True,
            has_violence_or_grave_threat=True,
            violencia_ou_grave_ameaca_ambigua=True,
            tem_multa_cumulativa=True,
            observacao_intertemporal=obs_147b,
        )

    if code == "art_150_cp":
        return PenalTypeSpec(
            code=code,
            nome="Violação de Domicílio Simples",
            artigo_formatado="art. 150, caput, do Código Penal",
            lei_vigente="Código Penal (Decreto-Lei nº 2.848/1940)",
            min_pena=PenaAnosMesesDias(anos=0, meses=1, dias=0),
            max_pena=PenaAnosMesesDias(anos=0, meses=3, dias=0),
            especie_pena="detencao",
            acao_penal="publica_incondicionada",
            has_builtin_domestic_violence_element=False,
            has_violence_or_grave_threat=False,
            tem_multa_alternativa=True,
            observacao_intertemporal="Pena de detenção de 1 a 3 meses, ou multa. Bem jurídico autônomo (inviolabilidade do domicílio — art. 5º, XI, CF), não absorvido pela lesão corporal (STJ, 6ª Turma, Informativo 846).",
        )

    if code == "art_150_p1_cp":
        return PenalTypeSpec(
            code=code,
            nome="Violação de Domicílio Qualificada",
            artigo_formatado="art. 150, § 1º, do Código Penal",
            lei_vigente="Código Penal (Decreto-Lei nº 2.848/1940)",
            min_pena=PenaAnosMesesDias(anos=0, meses=6, dias=0),
            max_pena=PenaAnosMesesDias(anos=2, meses=0, dias=0),
            especie_pena="detencao",
            acao_penal="publica_incondicionada",
            has_builtin_domestic_violence_element=False,
            has_violence_or_grave_threat=True,
            violencia_ou_grave_ameaca_ambigua=True,
            observacao_intertemporal="Pena de detenção de 6 meses a 2 anos, além da pena correspondente à violência, se cometido durante a noite, em lugar ermo, ou com emprego de violência ou de arma, ou por duas ou mais pessoas.",
        )

    if code == "art_139_cp":
        return PenalTypeSpec(
            code=code,
            nome="Difamação em Contexto de Violência Doméstica",
            artigo_formatado="art. 139, caput, do Código Penal",
            lei_vigente="Código Penal (Decreto-Lei nº 2.848/1940)",
            min_pena=PenaAnosMesesDias(anos=0, meses=3, dias=0),
            max_pena=PenaAnosMesesDias(anos=1, meses=0, dias=0),
            especie_pena="detencao",
            acao_penal="privada",
            has_builtin_domestic_violence_element=False,
            has_violence_or_grave_threat=False,
            tem_multa_cumulativa=True,
            admite_aumento_art141_p3=pos_pacote_antifeminicidio,
            observacao_intertemporal="Delito contra a honra de ação penal privada, com pena de detenção de 3 meses a 1 ano e multa (sujeito ao prazo decadencial aplicável à imputação — arts. 103 e 107, IV, do CP).",
        )

    if code == "art_140_cp":
        return PenalTypeSpec(
            code=code,
            nome="Injúria em Contexto de Violência Doméstica",
            artigo_formatado="art. 140, caput, do Código Penal",
            lei_vigente="Código Penal / Lei nº 14.994/2024",
            min_pena=PenaAnosMesesDias(anos=0, meses=1, dias=0),
            max_pena=PenaAnosMesesDias(anos=0, meses=6, dias=0),
            especie_pena="detencao",
            acao_penal="privada",
            has_builtin_domestic_violence_element=False,
            has_violence_or_grave_threat=False,
            tem_multa_alternativa=True,
            admite_aumento_art141_p3=pos_pacote_antifeminicidio,
            observacao_intertemporal="Delito contra a honra sem violência ou grave ameaça à pessoa (sujeito ao prazo decadencial aplicável à imputação — art. 103 CP).",
        )

    if code == "art_140_p2_cp":
        return PenalTypeSpec(
            code=code,
            nome="Injúria Real em Contexto de Violência Doméstica",
            artigo_formatado="art. 140, § 2º, do Código Penal",
            lei_vigente="Código Penal (Decreto-Lei nº 2.848/1940)",
            min_pena=PenaAnosMesesDias(anos=0, meses=3, dias=0),
            max_pena=PenaAnosMesesDias(anos=1, meses=0, dias=0),
            especie_pena="detencao",
            acao_penal="publica_incondicionada",
            has_builtin_domestic_violence_element=False,
            has_violence_or_grave_threat=True,
            tem_multa_cumulativa=True,
            admite_aumento_art141_p3=pos_pacote_antifeminicidio,
            observacao_intertemporal="Injúria consistente em violência ou vias de fato aviltantes: pena de detenção de 3 meses a 1 ano e multa, além da pena correspondente à violência.",
        )

    if code == "art_138_cp":
        return PenalTypeSpec(
            code=code,
            nome="Calúnia em Contexto de Violência Doméstica",
            artigo_formatado="art. 138, caput, do Código Penal",
            lei_vigente="Código Penal (Decreto-Lei nº 2.848/1940)",
            min_pena=PenaAnosMesesDias(anos=0, meses=6, dias=0),
            max_pena=PenaAnosMesesDias(anos=2, meses=0, dias=0),
            especie_pena="detencao",
            acao_penal="privada",
            has_builtin_domestic_violence_element=False,
            has_violence_or_grave_threat=False,
            tem_multa_cumulativa=True,
            admite_aumento_art141_p3=pos_pacote_antifeminicidio,
            observacao_intertemporal="Delito contra a honra objetiva com pena de detenção de 6 meses a 2 anos e multa (ação penal privada, sujeita à decadência em 6 meses — art. 103 CP).",
        )

    if code == "art_163_cp":
        return PenalTypeSpec(
            code=code,
            nome="Dano Simples em Contexto Doméstico",
            artigo_formatado="art. 163, caput, do Código Penal",
            lei_vigente="Código Penal (Decreto-Lei nº 2.848/1940)",
            min_pena=PenaAnosMesesDias(anos=0, meses=1, dias=0),
            max_pena=PenaAnosMesesDias(anos=0, meses=6, dias=0),
            especie_pena="detencao",
            acao_penal="privada",
            has_builtin_domestic_violence_element=False,
            has_violence_or_grave_threat=False,
            tem_multa_alternativa=True,
            observacao_intertemporal="Crime patrimonial de ação penal privada (art. 167 CP), com pena de detenção de 1 a 6 meses ou multa.",
        )

    if code == "art_163_pu_cp":
        return PenalTypeSpec(
            code=code,
            nome="Dano Qualificado em Contexto de Violência Doméstica",
            artigo_formatado="art. 163, parágrafo único, do Código Penal",
            lei_vigente="Código Penal (Decreto-Lei nº 2.848/1940)",
            min_pena=PenaAnosMesesDias(anos=0, meses=6, dias=0),
            max_pena=PenaAnosMesesDias(anos=3, meses=0, dias=0),
            especie_pena="detencao",
            acao_penal="publica_incondicionada",
            has_builtin_domestic_violence_element=False,
            has_violence_or_grave_threat=True,
            violencia_ou_grave_ameaca_ambigua=True,
            tem_multa_cumulativa=True,
            observacao_intertemporal="Dano qualificado por violência à pessoa, grave ameaça ou motivo egoístico (ação penal pública incondicionada, pena de 6 meses a 3 anos de detenção e multa).",
        )

    if code == "art_148_p1_cp":
        return PenalTypeSpec(
            code=code,
            nome="Sequestro e Cárcere Privado Qualificado contra Cônjuge ou Companheiro",
            artigo_formatado="art. 148, § 1º, inciso I, do Código Penal",
            lei_vigente="Código Penal (Decreto-Lei nº 2.848/1940)",
            min_pena=PenaAnosMesesDias(anos=2, meses=0, dias=0),
            max_pena=PenaAnosMesesDias(anos=5, meses=0, dias=0),
            especie_pena="reclusao",
            acao_penal="publica_incondicionada",
            has_builtin_domestic_violence_element=True,
            has_violence_or_grave_threat=True,
            observacao_intertemporal="Cárcere privado qualificado pela condição de cônjuge ou companheiro (reclusão de 2 a 5 anos); vedado bis in idem com o art. 61, II, 'f', do CP.",
        )

    if code == "art_146_cp":
        return PenalTypeSpec(
            code=code,
            nome="Constrangimento Ilegal em Contexto de Violência Doméstica",
            artigo_formatado="art. 146, caput, do Código Penal",
            lei_vigente="Código Penal (Decreto-Lei nº 2.848/1940)",
            min_pena=PenaAnosMesesDias(anos=0, meses=3, dias=0),
            max_pena=PenaAnosMesesDias(anos=1, meses=0, dias=0),
            especie_pena="detencao",
            acao_penal="publica_incondicionada",
            has_builtin_domestic_violence_element=False,
            has_violence_or_grave_threat=True,
            tem_multa_alternativa=True,
            observacao_intertemporal="Crime contra a liberdade pessoal praticado mediante violência ou grave ameaça (pena de detenção de 3 meses a 1 ano, ou multa).",
        )

    if code == "art_158_cp":
        return PenalTypeSpec(
            code=code,
            nome="Extorsão em Contexto de Violência Doméstica",
            artigo_formatado="art. 158, caput, do Código Penal",
            lei_vigente="Código Penal (Decreto-Lei nº 2.848/1940)",
            min_pena=PenaAnosMesesDias(anos=4, meses=0, dias=0),
            max_pena=PenaAnosMesesDias(anos=10, meses=0, dias=0),
            especie_pena="reclusao",
            acao_penal="publica_incondicionada",
            has_builtin_domestic_violence_element=False,
            has_violence_or_grave_threat=True,
            tem_multa_cumulativa=True,
            observacao_intertemporal="Crime complexo praticado mediante violência ou grave ameaça (reclusão de 4 a 10 anos e multa); Súmula 588/STJ aplicável.",
        )

    if code == "art_215a_cp":
        return PenalTypeSpec(
            code=code,
            nome="Importunação Sexual",
            artigo_formatado="art. 215-A do Código Penal",
            lei_vigente="Lei nº 13.718/2018",
            min_pena=PenaAnosMesesDias(anos=1, meses=0, dias=0),
            max_pena=PenaAnosMesesDias(anos=5, meses=0, dias=0),
            especie_pena="reclusao",
            acao_penal="publica_incondicionada",
            has_builtin_domestic_violence_element=False,
            has_violence_or_grave_threat=False,
            observacao_intertemporal="Crime contra a dignidade sexual de ação penal pública incondicionada (art. 225 CP — Lei nº 13.718/2018), pena de reclusão de 1 a 5 anos.",
        )

    if code == "art_218c_cp":
        return PenalTypeSpec(
            code=code,
            nome="Divulgação de Cena de Estupro, Sexo ou Nudez sem Consentimento",
            artigo_formatado="art. 218-C, caput e § 1º, do Código Penal",
            lei_vigente="Lei nº 13.718/2018",
            min_pena=PenaAnosMesesDias(anos=1, meses=0, dias=0),
            max_pena=PenaAnosMesesDias(anos=5, meses=0, dias=0),
            especie_pena="reclusao",
            acao_penal="publica_incondicionada",
            has_builtin_domestic_violence_element=True,
            has_violence_or_grave_threat=False,
            observacao_intertemporal="Pornografia de vingança (reclusão de 1 a 5 anos), com causa de aumento de 1/3 a 2/3 se praticado por agente que mantém ou manteve relação íntima de afeto com a vítima (§ 1º).",
        )

    if code == "art_330_cp":
        return PenalTypeSpec(
            code=code,
            nome="Desobediência",
            artigo_formatado="art. 330 do Código Penal",
            lei_vigente="Código Penal (Decreto-Lei nº 2.848/1940)",
            min_pena=PenaAnosMesesDias(anos=0, meses=0, dias=15),
            max_pena=PenaAnosMesesDias(anos=0, meses=6, dias=0),
            especie_pena="detencao",
            acao_penal="publica_incondicionada",
            has_builtin_domestic_violence_element=False,
            has_violence_or_grave_threat=False,
            tem_multa_cumulativa=True,
            observacao_intertemporal="Crime contra a Administração Pública (detenção de 15 dias a 6 meses e multa); em relação às medidas protetivas, prevalece a tipificação especial do art. 24-A da Lei nº 11.340/2006.",
        )

    if code == "art_331_cp":
        return PenalTypeSpec(
            code=code,
            nome="Desacato",
            artigo_formatado="art. 331 do Código Penal",
            lei_vigente="Código Penal (Decreto-Lei nº 2.848/1940)",
            min_pena=PenaAnosMesesDias(anos=0, meses=6, dias=0),
            max_pena=PenaAnosMesesDias(anos=2, meses=0, dias=0),
            especie_pena="detencao",
            acao_penal="publica_incondicionada",
            has_builtin_domestic_violence_element=False,
            has_violence_or_grave_threat=False,
            tem_multa_alternativa=True,
            observacao_intertemporal="Crime praticado por particular contra a Administração da Justiça/Pública (detenção de 6 meses a 2 anos, ou multa).",
        )

    if code == "art_21_lcp":
        return PenalTypeSpec(
            code=code,
            nome="Contravenção Penal de Vias de Fato em Violência Doméstica",
            artigo_formatado="art. 21 do Decreto-Lei nº 3.688/1941 (LCP)",
            lei_vigente="Decreto-Lei nº 3.688/1941",
            min_pena=PenaAnosMesesDias(anos=0, meses=0, dias=15),
            max_pena=PenaAnosMesesDias(anos=0, meses=3, dias=0),
            especie_pena="prisao_simples",
            acao_penal="publica_incondicionada",
            has_builtin_domestic_violence_element=False,
            has_violence_or_grave_threat=True,
            tem_multa_alternativa=True,
            observacao_intertemporal="Aplica-se a Súmula 588/STJ e Súmula 589/STJ às contravenções praticadas com violência doméstica contra a mulher.",
        )

    raise ValueError(f"Código de tipo penal desconhecido: {code}")


def list_all_penal_types(reference_date: datetime.date | None = None) -> List[Dict[str, str]]:
    ref = reference_date or datetime.date.today()
    codes = [
        "art_129_p13_cp",
        "art_129_p9_cp",
        "art_147_cp",
        "art_24a_lmp",
        "art_338a_cp",
        "art_147a_cp",
        "art_147b_cp",
        "art_150_cp",
        "art_150_p1_cp",
        "art_138_cp",
        "art_139_cp",
        "art_140_cp",
        "art_140_p2_cp",
        "art_146_cp",
        "art_148_p1_cp",
        "art_158_cp",
        "art_163_cp",
        "art_163_pu_cp",
        "art_215a_cp",
        "art_218c_cp",
        "art_330_cp",
        "art_331_cp",
        "art_21_lcp",
    ]
    out = []
    for c in codes:
        spec = get_penal_type_for_date(c, ref)
        out.append(
            {
                "code": spec.code,
                "nome": spec.nome,
                "artigo_formatado": spec.artigo_formatado,
                "pena_min": spec.min_pena.format_curto(),
                "pena_max": spec.max_pena.format_curto(),
                "especie": spec.especie_pena,
            }
        )
    return out
