"""Motor determinístico de dosimetria penal (arts. 59, 68, 33, 44, 77, 109-119 do CP).

Convenções de unidades
----------------------
* Pena: ano penal de 360 dias e mês de 30 dias (``PenaAnosMesesDias.to_total_days``),
  para que as frações (1/6, 1/8, 1/2...) incidam de forma exata e reprodutível.
* Prescrição: contagem pelo calendário comum (art. 10 do CP) — os anos/meses do prazo
  são somados à data de início (``somar_meses``), nunca ``365 * anos``. O dia do começo
  inclui-se no cômputo; por isso o prazo se consuma na data "aniversário" do marco inicial.

A memória separa sugestões do motor de dados arbitrados pelo juízo.
"""

import calendar
import datetime
import re
from fractions import Fraction
from typing import Annotated, Dict, List, Literal, Tuple

from pydantic import AfterValidator, BaseModel, Field

from app.core.errors import DadosInsuficientesError
from app.legal_engine.penal_types_catalog import (
    PenaAnosMesesDias,
    PenalTypeSpec,
    get_penal_type_for_date,
    verificar_tipo_existente_na_data,
)

# Lei nº 12.234/2010 (publicada e vigente em 06/05/2010): deu nova redação ao art. 110, § 1º
# (vedada prescrição retroativa com termo inicial anterior à denúncia/queixa) e elevou de 2
# para 3 anos o prazo do art. 109, VI. Não retroage a fatos anteriores (lex gravior).
DATA_VIGENCIA_LEI_12234_2010 = datetime.date(2010, 5, 6)

# Circunstâncias judiciais exigidas como favoráveis pelos arts. 44, III, e 77, II, do CP
# (o art. 77, II, não menciona consequências do crime nem comportamento da vítima).
VETORES_ART_77_II = [
    "culpabilidade",
    "antecedentes",
    "conduta_social",
    "personalidade",
    "motivos",
    "circunstancias",
]

VETORES_ART_59 = [
    "culpabilidade",
    "antecedentes",
    "conduta_social",
    "personalidade",
    "motivos",
    "circunstancias",
    "consequencias",
    "comportamento_vitima",
]

ROTULOS_VETORES_ART_59 = {
    "culpabilidade": "CULPABILIDADE",
    "antecedentes": "ANTECEDENTES CRIMINAIS",
    "conduta_social": "CONDUTA SOCIAL",
    "personalidade": "PERSONALIDADE DO AGENTE",
    "motivos": "MOTIVOS DO CRIME",
    "circunstancias": "CIRCUNSTÂNCIAS DO CRIME",
    "consequencias": "CONSEQUÊNCIAS DO CRIME",
    "comportamento_vitima": "COMPORTAMENTO DA VÍTIMA",
}


def formatar_especie_pena(especie: str) -> str:
    """Retorna a espécie da pena privativa de liberdade com a devida acentuação ortográfica da língua portuguesa."""
    mapa = {
        "reclusao": "reclusão",
        "detencao": "detenção",
        "prisao_simples": "prisão simples",
    }
    return mapa.get(especie.lower().strip(), especie)


class CircunstanciaArt59(BaseModel):
    desfavoravel: bool = False
    possui_transito_em_julgado_anterior: bool = True
    fundamentacao: str = ""


class VetorArt59Detalhado(BaseModel):
    codigo: str
    rotulo: str
    desfavoravel: bool = False
    status: Literal["desfavoravel", "neutro", "favoravel"] = "neutro"
    fundamentacao: str = ""


def obter_fundamentacao_padrao_vetor(
    vetor: str,
    desfavoravel: bool,
    crime_spec: PenalTypeSpec | None = None,
    detalhes_customizados: str = "",
    id_cac: str = "",
) -> Tuple[str, Literal["desfavoravel", "neutro", "favoravel"]]:
    """
    Retorna a fundamentação jurídica padrão refinada e adequada à violência doméstica e familiar
    para cada um dos 8 vetores do art. 59 do CP, em estrita consonância com os manuais da ENFAM,
    a jurisprudência do TJRN (Juizados da Mulher) e do STJ.
    Garante que qualquer texto fático customizado seja harmonizado com a fundamentação padrão,
    concluindo impreterivelmente com a valoração judicial expressa (desfavorável, neutra ou favorável).
    """
    custom = detalhes_customizados.strip()

    if vetor == "culpabilidade":
        if desfavoravel:
            detalhe_extra = f" ({custom})" if custom else " (maior ousadia e intensidade no desvalor da ação)"
            return (
                f"O réu excedeu a normalidade do tipo penal. A conduta praticada desbordou do dolo inerente ao tipo, "
                f"revelando reprovabilidade acentuada{detalhe_extra}, "
                f"justificando a exasperação da pena-base. Circunstância judicial desfavorável.",
                "desfavoravel",
            )
        detalhe_extra = f" ({custom})" if custom else ""
        return (
            f"O réu agiu com dolo inerente ao tipo penal violado, não havendo nos autos elementos probatórios que extrapolem a reprovabilidade ordinária comum à espécie delitiva{detalhe_extra}. Circunstância judicial neutra.",
            "neutro",
        )

    if vetor == "antecedentes":
        if desfavoravel:
            detalhe_extra = f" ({custom})" if custom else " (conforme certidão de antecedentes criminais juntada aos autos)"
            return (
                f"O acusado ostenta condenação criminal definitiva transitada em julgado que não configura reincidência "
                f"(ou com trânsito em julgado há mais de 5 anos - período depurador do art. 64, I, do Código Penal), "
                f"idônea a exasperar a pena-base a título de maus antecedentes{detalhe_extra}, "
                f"em conformidade com a Súmula 444/STJ. Circunstância judicial desfavorável.",
                "desfavoravel",
            )
        custom_ref = f" ({custom})" if custom else ""
        sumula_444 = (
            "incidindo a Súmula 444 do STJ ('É vedada a utilização de inquéritos policiais e ações penais em curso "
            "para agravar a pena-base'). Circunstância judicial favorável/neutra."
        )
        if id_cac:
            # Só se afirma o conteúdo da certidão quando ela foi efetivamente localizada nos autos.
            return (
                f"A certidão de antecedentes criminais constante nos autos ({id_cac}){custom_ref} não registra condenação "
                f"definitiva transitada em julgado apta a macular os antecedentes do acusado, {sumula_444}",
                "favoravel",
            )
        return (
            f"Não há nos autos notícia de condenação definitiva transitada em julgado apta a configurar maus antecedentes"
            f"{custom_ref}, prevalecendo a presunção de primariedade, {sumula_444}",
            "favoravel",
        )

    if vetor == "conduta_social":
        if desfavoravel:
            detalhe_extra = f" ({custom})" if custom else " (comportamento desajustado no meio comunitário, familiar ou laboral)"
            return (
                f"Constatou-se desvio relevante e comprovado na conduta social do acusado{detalhe_extra}, "
                f"desvinculado dos fatos delituosos em si e respaldado em prova testemunhal idônea. Circunstância judicial desfavorável.",
                "desfavoravel",
            )
        detalhe_extra = f" ({custom})" if custom else ""
        return (
            f"Inexistem nos autos elementos probatórios desfavoráveis suficientes acerca da conduta social do acusado no seio familiar, laboral e comunitário{detalhe_extra}. "
            f"Conforme remansosa jurisprudência do Superior Tribunal de Justiça, veda-se valorar negativamente a conduta social com fulcro em atos infracionais, inquéritos ou meros conceitos morais abstratos sem suporte probatório específico. Circunstância judicial neutra.",
            "neutro",
        )

    if vetor == "personalidade":
        if desfavoravel:
            detalhe_extra = f" ({custom})" if custom else " (laudo pericial técnico conclusivo)"
            return (
                f"Aferição negativa lastreada em laudo pericial ou relatório psicossocial conclusivo constante nos autos{detalhe_extra}, "
                f"evidenciando distorção da personalidade voltada à violência ou desvio psíquico comprovado. Circunstância judicial desfavorável.",
                "desfavoravel",
            )
        detalhe_extra = f" ({custom})" if custom else ""
        return (
            f"Não há nos autos laudos periciais, relatórios psicossociais ou elementos científicos seguros e idôneos que permitam aquilatar desfavoravelmente o perfil psíquico ou a personalidade do sentenciado{detalhe_extra}. "
            f"Segundo pacífica jurisprudência do Superior Tribunal de Justiça e as diretrizes da Escola Nacional de Formação e Aperfeiçoamento de Magistrados (ENFAM), é defeso ao julgador emitir juízo subjetivo sobre a personalidade sem subsídios técnicos formais. Circunstância judicial neutra.",
            "neutro",
        )

    if vetor == "motivos":
        if desfavoravel:
            detalhe_extra = f" ({custom})" if custom else " (motivação extraordinariamente fútil ou torpe)"
            return (
                f"O delito foi impulsionado por motivação extraordinária que desborda substancialmente do tipo{detalhe_extra}, "
                f"sem configurar bis in idem com eventuais qualificadoras ou agravantes legais. Circunstância judicial desfavorável.",
                "desfavoravel",
            )
        detalhe_extra = f" ({custom})" if custom else ""
        return (
            f"Os motivos do crime{detalhe_extra} não extrapolam os inerentes ao tipo penal, não havendo nos autos elementos que justifiquem a exasperação da pena-base. "
            f"Circunstância judicial neutra.",
            "neutro",
        )

    if vetor == "circunstancias":
        if desfavoravel:
            detalhe_extra = f" ({custom})" if custom else " (prática do crime em período noturno, com invasão de domicílio e transposição de obstáculos)"
            return (
                f"As circunstâncias de tempo, lugar e modo de execução desbordaram expressivamente da regularidade típica{detalhe_extra}, "
                f"revelando maior audácia do agente. Circunstância judicial desfavorável.",
                "desfavoravel",
            )
        detalhe_extra = f" ({custom})" if custom else ""
        return (
            f"As circunstâncias de tempo, lugar e modo de execução{detalhe_extra} não extrapolam o padrão ordinário intrínseco à figura típica violada. Circunstância judicial neutra.",
            "neutro",
        )

    if vetor == "consequencias":
        if desfavoravel:
            detalhe_extra = f" ({custom})" if custom else " (severo trauma psicológico, necessidade premente de alteração de domicílio e tratamento terapêutico continuado)"
            return (
                f"As consequências do delito revelaram-se extraordinariamente graves{detalhe_extra}, "
                f"excedendo o resultado naturalístico próprio do tipo penal. Circunstância judicial desfavorável.",
                "desfavoravel",
            )
        detalhe_extra = f" ({custom})" if custom else ""
        return (
            f"As consequências do delito{detalhe_extra} são as normais e inerentes ao resultado lesivo previsto no tipo penal pelo legislador, "
            f"não tendo sido demonstrados desdobramentos lesivos anormais ou sequelas extraordinárias. Circunstância judicial neutra.",
            "neutro",
        )

    # comportamento_vitima (estritamente blindado sob a Resolução CNJ nº 492/2023)
    return (
        "A ofendida em nada concorreu ou contribuiu para a eclosão da prática delituosa. Ademais, em estrita observância ao Protocolo para Julgamento com Perspectiva de Gênero do Conselho Nacional de Justiça (Resolução CNJ nº 492/2023), veda-se peremptoriamente a culpabilização da mulher em situação de violência ou a utilização de estereótipos patriarcais para mitigar ou modular a sanção penal do agressor, mantendo-se a circunstância judicial rigorosamente neutra.",
        "neutro",
    )


def gerar_vetores_art59_detalhados(
    spec: PenalTypeSpec | str | None = None,
    circunstancias_input: Dict[str, CircunstanciaArt59] | None = None,
    aplicar_sumula_444_stj: bool = True,
    id_cac: str = "",
) -> Dict[str, VetorArt59Detalhado]:
    """
    Gera analiticamente e individualmente a estrutura completa dos 8 vetores do art. 59 do Código Penal
    para qualquer delito julgado no âmbito da violência doméstica e familiar, garantindo:
    1. Que todas as 8 circunstâncias sejam explicitamente enunciadas;
    2. A blindagem da Súmula 444/STJ para antecedentes;
    3. A blindagem do 'comportamento da vítima' sob a Resolução CNJ nº 492/2023 (sempre neutro);
    4. Textos de fundamentação elegantes nos padrões da ENFAM e do TJRN.
    """
    spec_obj: PenalTypeSpec | None = None
    if isinstance(spec, PenalTypeSpec):
        spec_obj = spec
    elif isinstance(spec, str):
        spec_obj = get_penal_type_for_date(spec, datetime.date.today())

    circs_in = circunstancias_input or {}
    resultado: Dict[str, VetorArt59Detalhado] = {}

    for codigo in VETORES_ART_59:
        rotulo = ROTULOS_VETORES_ART_59.get(codigo, codigo.upper())
        dados_input = circs_in.get(codigo)

        desfav = False
        fund_custom = ""
        transito = True

        if dados_input:
            desfav = dados_input.desfavoravel
            fund_custom = dados_input.fundamentacao.strip()
            transito = dados_input.possui_transito_em_julgado_anterior

        # Blindagem 1: Comportamento da Vítima é SEMPRE neutro sob a Resolução CNJ 492/2023
        if codigo == "comportamento_vitima":
            desfav = False
            fund_texto, status = obter_fundamentacao_padrao_vetor(
                codigo, desfavoravel=False, crime_spec=spec_obj, detalhes_customizados="", id_cac=id_cac
            )
        # Blindagem 2: Súmula 444 STJ para antecedentes sem trânsito em julgado
        elif codigo == "antecedentes":
            if desfav and aplicar_sumula_444_stj and not transito:
                desfav = False
                fund_texto, status = obter_fundamentacao_padrao_vetor(
                    codigo, desfavoravel=False, crime_spec=spec_obj, detalhes_customizados="", id_cac=id_cac
                )
            else:
                fund_texto, status = obter_fundamentacao_padrao_vetor(
                    codigo, desfavoravel=desfav, crime_spec=spec_obj, detalhes_customizados=fund_custom, id_cac=id_cac
                )
        else:
            fund_texto, status = obter_fundamentacao_padrao_vetor(
                codigo, desfavoravel=desfav, crime_spec=spec_obj, detalhes_customizados=fund_custom, id_cac=id_cac
            )

        resultado[codigo] = VetorArt59Detalhado(
            codigo=codigo,
            rotulo=rotulo,
            desfavoravel=desfav,
            status=status,
            fundamentacao=fund_texto,
        )

    return resultado


class DosimetriaConfig(BaseModel):
    criterio_primeira_fase: Literal["um_oitavo_intervalo", "um_sexto_minima"] = "um_oitavo_intervalo"
    fracao_segunda_fase: Tuple[int, int] = (1, 6)
    aplicar_sumula_231_stj: bool = True
    aplicar_sumula_444_stj: bool = True


class MajoranteArt24aP4(BaseModel):
    hipotese: Literal["area_exclusao", "dispositivo_monitoracao"]
    fonte_id: str = Field(min_length=1)
    trecho: str = Field(min_length=1)
    fracao: Tuple[int, int]


def _validar_valor_unitario_multa(valor: str) -> str:
    """Multiplicador exato do salário mínimo ao tempo do fato (art. 49, § 1º, CP)."""
    texto = valor.strip().replace(",", ".")
    if not re.fullmatch(r"[0-9]{1,9}(?:/[0-9]{1,9}|\.[0-9]{1,9})?", texto):
        raise ValueError("informe um multiplicador do salário mínimo, como 1/30 ou 1,5")
    try:
        fator = Fraction(texto)
    except ZeroDivisionError:
        raise ValueError("o denominador do valor unitário deve ser positivo") from None
    if not Fraction(1, 30) <= fator <= 5:
        raise ValueError("o valor unitário deve estar entre 1/30 e 5 salários mínimos")
    return str(fator)


ValorUnitarioMulta = Annotated[str, AfterValidator(_validar_valor_unitario_multa)]


class CrimeDosimetriaInput(BaseModel):
    crime_code: str
    data_fato: datetime.date
    id_imputacao: str = ""
    referencia_imputacao: str = ""
    data_recebimento_individual: datetime.date | None = None
    recebimento_por_imputacao: bool = False
    reduzir_prescricao_art115: bool | None = None
    circunstancias_art59: Dict[str, CircunstanciaArt59] = Field(default_factory=dict)
    agravantes_art61_f_violencia_domestica: bool = False
    reincidente: bool = False
    confissao_espontanea: bool = False
    menoridade_relativa_ou_senilidade: bool = False
    fracao_aumento_terceira_fase: Tuple[int, int] | None = None
    fracao_diminuicao_terceira_fase: Tuple[int, int] | None = None
    # 0 = não parametrizado pelo juízo (para tipos com multa cumulativa o motor sugere um
    # quantum proporcional e emite alerta).
    dias_multa: int | None = Field(default=None, strict=True, ge=0, le=360)
    valor_unitario_multa: ValorUnitarioMulta | None = None
    desclassificado_de: str = ""
    ameaca_qualificada: bool = False
    majorante_art24a_p4: MajoranteArt24aP4 | None = None
    # Majorante do art. 147-A, § 1º, II, do CP (+1/2: crime contra mulher por razões da
    # condição de sexo feminino). PADRÃO DOCUMENTADO: True, pois no JVDFM a perseguição é
    # praticada contra mulher em contexto de violência doméstica (art. 121-A, § 1º, I, do CP);
    # exige reconhecimento expresso do juízo.
    # Passe False quando a majorante não tiver sido descrita na peça acusatória.
    # Ignorada se ``fracao_aumento_terceira_fase`` for informada (a fração do juízo prevalece).
    aplicar_aumento_art147a_p1_ii: bool = False
    # Majorante do art. 141, § 3º, do CP (pena em dobro: crime contra a honra contra a mulher por razões
    # da condição do sexo feminino), incluída pela Lei nº 14.994/2024. Só se aplica a fatos a partir de
    # 10/10/2024 nos arts. 138, 139, 140 e 140, § 2º. PADRÃO False (depende de a majorante constar da
    # peça acusatória e de decisão do juízo); desativada, gera alerta. Ignorada se
    # ``fracao_aumento_terceira_fase`` for informada.
    aplicar_aumento_art141_p3: bool = False
    # Modalidade praticada COM violência ou grave ameaça (Súmula 588/STJ), para os tipos que admitem
    # modalidades com e sem (147-A, 147-B, 150, § 1º, 163, p.u.). None = não decidido pelo juízo: para
    # esses tipos a substituição do art. 44 não é negada automaticamente com base na Súmula 588.
    violencia_ou_grave_ameaca: bool | None = None


class CrimeDosimetriaOutput(BaseModel):
    crime_spec: PenalTypeSpec
    id_imputacao: str = ""
    referencia_imputacao: str = ""
    data_recebimento_individual: datetime.date | None = None
    recebimento_por_imputacao: bool = False
    reduzir_prescricao_art115: bool | None = None
    vetores_negativos_efetivos: List[str]
    acrescimo_por_vetor_dias: int
    pena_base: PenaAnosMesesDias
    bis_in_idem_art61f_bloqueado: bool
    compensacao_confissao_reincidencia: bool
    sumula_231_aplicada: bool
    pena_intermediaria: PenaAnosMesesDias
    pena_definitiva: PenaAnosMesesDias
    agravantes_ativas: List[str] = Field(default_factory=list)
    atenuantes_ativas: List[str] = Field(default_factory=list)
    alertas_juridicos: List[str]
    memoria_calculo: List[str]
    vetores_detalhados: Dict[str, VetorArt59Detalhado] = Field(default_factory=dict)
    dias_multa: int = 0
    desclassificado_de: str = ""
    data_fato: datetime.date | None = None
    reincidente: bool = False
    fracao_aumento_aplicada: Tuple[int, int] | None = None
    fracao_diminuicao_aplicada: Tuple[int, int] | None = None
    aumento_art147a_aplicado: bool = False
    aumento_art141_p3_aplicado: bool = False
    majorante_art24a_p4: MajoranteArt24aP4 | None = None
    dias_multa_informados: int | None = None
    valor_unitario_multa: str | None = None
    dias_multa_sugeridos: int | None = None
    multa_pendente: bool = False
    dias_multa_sugeridos_pelo_motor: bool = False
    # True/False = modalidade decidida (ou inerente ao tipo); None = tipo com modalidades com e sem
    # violência/grave ameaça e o juízo ainda não decidiu.
    violencia_ou_grave_ameaca_efetiva: bool | None = None


class ConcursoCrimesConfig(BaseModel):
    tipo_concurso: Literal["nenhum", "material", "formal", "continuado"] = "material"
    # None = o motor sugere a fração conforme o número de crimes (ver
    # ``sugerir_fracao_concurso``); uma fração informada pelo juízo sempre prevalece.
    fracao_exasperacao: Tuple[int, int] | None = None


def sugerir_fracao_concurso(tipo_concurso: str, numero_crimes: int) -> Tuple[int, int] | None:
    """Sugere a fração de exasperação pelo número de infrações (critério consolidado no STJ).

    * Concurso formal (art. 70, caput, 1ª parte): 2 crimes = 1/6; 3 = 1/5; 4 = 1/4; 5 = 1/3; 6 ou mais = 1/2.
    * Crime continuado (art. 71, caput — Súmula 659/STJ): 2 = 1/6; 3 = 1/5; 4 = 1/4; 5 = 1/3; 6 = 1/2; 7 ou mais = 2/3.

    É apenas sugestão: o juízo pode sobrescrever informando ``fracao_exasperacao``.
    """
    if tipo_concurso not in ("formal", "continuado") or numero_crimes < 2:
        return None
    tabela = {2: (1, 6), 3: (1, 5), 4: (1, 4), 5: (1, 3), 6: (1, 2)}
    if numero_crimes in tabela:
        return tabela[numero_crimes]
    return (1, 2) if tipo_concurso == "formal" else (2, 3)


class DosimetriaGlobalOutput(BaseModel):
    crimes_calculados: List[CrimeDosimetriaOutput]
    penas_por_especie: Dict[str, PenaAnosMesesDias]
    pena_total_unificada: PenaAnosMesesDias
    detracao_dias: int = 0
    pena_remanescente_detracao: PenaAnosMesesDias | None = None
    regime_inicial: Literal["aberto", "semiaberto", "fechado"]
    substituicao_art44_cabivel: bool
    motivo_vedacao_substituicao: str
    sursis_art77_cabivel: bool
    fundamentacao_sursis: str
    sursis_condicoes_legais: List[str] = Field(default_factory=list)
    indenizacao_dano_moral_tema983: float
    alertas_globais: List[str]
    tipo_concurso: Literal["nenhum", "material", "formal", "continuado"] = "material"
    fracao_concurso: Tuple[int, int] | None = None
    total_dias_multa: int = 0
    sem_condenacao: bool = False
    crimes_nao_condenados: List[Dict[str, str]] = Field(default_factory=list)
    # Campos v2 (aditivos)
    regime_por_especie: Dict[str, Literal["aberto", "semiaberto", "fechado"]] = Field(default_factory=dict)
    fundamentacao_regime: str = ""
    fracao_concurso_sugerida: Tuple[int, int] | None = None
    sursis_negado_por_circunstancias_judiciais: bool = False
    circunstancias_desfavoraveis_art77_ii: List[str] = Field(default_factory=list)
    dano_moral_pedido_mp: bool = False
    valor_dano_moral_pedido: float | None = None
    valor_dano_moral_sugerido: float | None = None
    valor_dano_moral_decidido: float | None = None
    dano_moral_arbitramento_pendente: bool = False
    # Detração (art. 387, § 2º, do CPP): o regime é fixado sobre a pena remanescente; guarda-se o
    # regime que resultaria SEM a detração e a pena considerada para o regime.
    regime_sem_detracao: Literal["aberto", "semiaberto", "fechado"] | None = None
    pena_considerada_regime: PenaAnosMesesDias | None = None
    # Concurso formal/continuado convertido em cúmulo material por ser mais benéfico (art. 70, p.u.):
    # ``tipo_concurso`` passa a "material", mas o tipo reconhecido é preservado aqui.
    concurso_material_beneficio: bool = False
    tipo_concurso_reconhecido: Literal["nenhum", "material", "formal", "continuado"] = "material"
    # Substituição (art. 44) pendente de decisão do juízo sobre a modalidade (com/sem violência ou
    # grave ameaça) em tipo que admite ambas (Súmula 588/STJ não pode ser afirmada automaticamente).
    substituicao_pendente_modalidade: bool = False


class PrescricaoOutput(BaseModel):
    prazo_prescricional_anos: int
    reduzido_metade_art115: bool
    prescricao_reconhecida: bool
    dias_decorridos_maior_hiato: int
    fundamentacao: str
    # Campos v2 (aditivos)
    prescricao_abstrata_reconhecida: bool = False
    prescricao_retroativa_reconhecida: bool = False
    lei_12234_aplicavel: bool = True
    alertas: List[str] = Field(default_factory=list)


class PrescricaoCrimeOutput(BaseModel):
    """Prescrição da pretensão punitiva de UM crime (art. 119 do CP)."""

    crime_code: str
    id_imputacao: str = ""
    referencia_imputacao: str = ""
    crime_nome: str = ""
    artigo_formatado: str = ""
    data_fato: datetime.date | None = None
    data_recebimento_denuncia: datetime.date | None = None
    data_sentenca: datetime.date
    pena_concreta: PenaAnosMesesDias
    pena_maxima_abstrata: PenaAnosMesesDias
    prazo_concreto_anos: int  # art. 109 pela pena aplicada (antes do art. 115)
    prazo_abstrato_anos: int  # art. 109 pela pena máxima cominada (antes do art. 115)
    prazo_concreto_meses_efetivo: int  # após eventual redução do art. 115
    prazo_abstrato_meses_efetivo: int
    reduzido_metade_art115: bool = False
    lei_12234_aplicavel: bool = True
    lapsos_considerados_retroativa: List[str] = Field(default_factory=list)
    prescricao_abstrata_reconhecida: bool = False
    prescricao_retroativa_reconhecida: bool = False
    prescricao_reconhecida: bool = False
    verificacao_completa: bool = True
    fundamentacao: str = ""
    alertas: List[str] = Field(default_factory=list)


class PrescricaoGlobalOutput(BaseModel):
    """Prescrição verificada crime a crime (art. 119 do CP e Súmula 497/STF)."""

    por_crime: List[PrescricaoCrimeOutput] = Field(default_factory=list)
    prescricao_reconhecida: bool = False
    crimes_prescritos: List[str] = Field(default_factory=list)
    fundamentacao: str = ""
    alertas: List[str] = Field(default_factory=list)


def calcular_dosimetria_crime(
    entrada: CrimeDosimetriaInput,
    config: DosimetriaConfig | None = None,
) -> CrimeDosimetriaOutput:
    cfg = config or DosimetriaConfig()
    # Conduta atípica na data do fato (tipo criado por lei posterior): não há pena a calcular.
    verificar_tipo_existente_na_data(entrada.crime_code, entrada.data_fato)
    spec = get_penal_type_for_date(
        entrada.crime_code, entrada.data_fato, ameaca_qualificada=entrada.ameaca_qualificada
    )
    majorante_24a = entrada.majorante_art24a_p4
    if majorante_24a:
        n, d = majorante_24a.fracao
        if (not spec.admite_aumento_art24a_p4 or d <= 0 or 3 * n < d or 2 * n > d
                or not majorante_24a.fonte_id.strip() or not majorante_24a.trecho.strip()
                or entrada.fracao_aumento_terceira_fase is not None):
            raise DadosInsuficientesError(
                [f"crimes_config.{entrada.crime_code}.majorante_art24a_p4"],
                "Informe a hipótese, a fonte e a fração (de 1/3 a 1/2) do art. 24-A, § 4º, "
                "da Lei Maria da Penha, vigente desde 10/04/2026, sem cumular com fração genérica.",
            )

    alertas: List[str] = [spec.observacao_intertemporal]
    memoria: List[str] = [
        f"Tipo Penal: {spec.nome} ({spec.artigo_formatado})",
        f"Preceito Secundário Aplicável ({spec.lei_vigente}): {spec.min_pena.format_extenso()} a {spec.max_pena.format_extenso()} de {formatar_especie_pena(spec.especie_pena)}.",
    ]

    # 1ª Fase: Circunstâncias Judiciais (Art. 59 CP)
    vetores_detalhados = gerar_vetores_art59_detalhados(
        spec=spec,
        circunstancias_input=entrada.circunstancias_art59,
        aplicar_sumula_444_stj=cfg.aplicar_sumula_444_stj,
    )
    vetores_negativos: List[str] = [
        codigo for codigo, vetor_info in vetores_detalhados.items() if vetor_info.desfavoravel
    ]

    # Alertas específicos do Art. 59 CP (Resolução CNJ 492/2023 e Súmula 444/STJ)
    if "comportamento_vitima" in entrada.circunstancias_art59 and entrada.circunstancias_art59["comportamento_vitima"].desfavoravel:
        alertas.append(
            "Vetor 'comportamento da vítima' mantido neutro: na sistemática penal e sob a Resolução CNJ nº 492/2023, o comportamento da vítima jamais pode ser valorado negativamente para exasperar a pena do réu."
        )
    if (
        "antecedentes" in entrada.circunstancias_art59
        and entrada.circunstancias_art59["antecedentes"].desfavoravel
        and cfg.aplicar_sumula_444_stj
        and not entrada.circunstancias_art59["antecedentes"].possui_transito_em_julgado_anterior
    ):
        alertas.append(
            "Súmula 444/STJ aplicada: inquéritos policiais e ações penais em curso sem trânsito em julgado não podem ser utilizados para exasperar a pena-base a título de maus antecedentes."
        )

    min_days = spec.min_pena.to_total_days()
    max_days = spec.max_pena.to_total_days()

    if cfg.criterio_primeira_fase == "um_oitavo_intervalo":
        intervalo = max(0, max_days - min_days)
        passo_vetor = intervalo // 8
        criterio_label = "1/8 sobre o intervalo entre a pena mínima e máxima em abstrato"
    else:
        passo_vetor = max(1, min_days // 6)
        criterio_label = "1/6 sobre a pena mínima em abstrato"

    acrescimo_1fase = passo_vetor * len(vetores_negativos)
    pena_base_days = min(max_days, min_days + acrescimo_1fase)
    pena_base = PenaAnosMesesDias.from_total_days(pena_base_days)

    memoria.append(
        f"1ª Fase (Art. 59 CP): {len(vetores_negativos)} circunstância(s) desfavorável(is) ({', '.join(vetores_negativos) if vetores_negativos else 'todas favoráveis/neutras'}) -> Critério: {criterio_label} (+{PenaAnosMesesDias.from_total_days(passo_vetor).format_curto()} por vetor) -> Pena-Base fixada em {pena_base.format_extenso()}."
    )

    # 2ª Fase: Circunstâncias Agravantes e Atenuantes (Arts. 61 a 67 CP)
    bis_in_idem_bloqueado = False
    agravantes_ativas: List[str] = []
    atenuantes_ativas: List[str] = []

    if entrada.reincidente:
        agravantes_ativas.append("reincidência (art. 61, I, CP)")

    # Nos arts. 147-A e 218-C a condição de mulher/relação de afeto é CAUSA DE AUMENTO (§ 1º, II, do
    # 147-A; § 1º do 218-C), não elementar: a agravante só é bloqueada (bis in idem) quando a
    # majorante correspondente é efetivamente aplicada na 3ª fase.
    bloqueia_61f = spec.has_builtin_domestic_violence_element
    if entrada.crime_code in ("art_147a_cp", "art_218c_cp"):
        majorante_aplicada = bool(entrada.fracao_aumento_terceira_fase) or (
            entrada.crime_code == "art_147a_cp" and entrada.aplicar_aumento_art147a_p1_ii
        )
        bloqueia_61f = majorante_aplicada

    if entrada.agravantes_art61_f_violencia_domestica:
        if bloqueia_61f:
            bis_in_idem_bloqueado = True
            alertas.append(
                f"Bloqueio automático de bis in idem: a agravante genérica do art. 61, II, 'f', do CP não incide no delito do {spec.artigo_formatado}, pois a situação de violência doméstica/condição do sexo feminino já integra a qualificadora/elementar do tipo penal ou a causa de aumento específica aplicada."
            )
        else:
            agravantes_ativas.append("violência doméstica e familiar contra a mulher (art. 61, II, 'f', CP)")

    if entrada.confissao_espontanea:
        atenuantes_ativas.append("confissão espontânea (art. 65, III, 'd', CP - Súmula 545/STJ)")
    if entrada.menoridade_relativa_ou_senilidade:
        atenuantes_ativas.append("menoridade relativa/senilidade (art. 65, I, CP)")

    compensacao = bool(entrada.reincidente and entrada.confissao_espontanea)
    sumula_231_aplicada = False
    saldo_2fase = len(agravantes_ativas) - len(atenuantes_ativas)

    if len(agravantes_ativas) > 0 and len(atenuantes_ativas) > 0:
        if compensacao:
            # Tema Repetitivo 585/STJ trata EXCLUSIVAMENTE da compensação entre a confissão
            # espontânea e a reincidência.
            alertas.append(
                "Compensação na 2ª Fase: compensada integralmente a atenuante da confissão espontânea com a agravante da reincidência "
                "(art. 67 do CP e Tema Repetitivo 585/STJ; em caso de multirreincidência, a compensação deve ser apenas proporcional, a critério do juízo)."
            )
        outras_agravantes = [a for a in agravantes_ativas if not (compensacao and a.startswith("reincidência"))]
        outras_atenuantes = [a for a in atenuantes_ativas if not (compensacao and a.startswith("confissão espontânea"))]
        if outras_agravantes and outras_atenuantes:
            n_ag, n_at = len(outras_agravantes), len(outras_atenuantes)
            if n_ag == n_at:
                desfecho = "compensam-se integralmente (quantidade igual)"
            elif n_ag > n_at:
                desfecho = f"compensam-se {n_at} e remanesce saldo de {n_ag - n_at} agravante(s), que eleva(m) a pena"
            else:
                desfecho = f"compensam-se {n_ag} e remanesce saldo de {n_at - n_ag} atenuante(s), que reduz(em) a pena"
            alertas.append(
                "Concurso de agravantes e atenuantes na 2ª Fase (art. 67 do CP): "
                f"{', '.join(outras_atenuantes)} x {', '.join(outras_agravantes)}: {desfecho}; "
                "cabe ao juízo verificar eventual preponderância (motivos determinantes, personalidade e reincidência)."
            )

    num_f, den_f = cfg.fracao_segunda_fase
    delta_2fase = (pena_base_days * num_f) // den_f

    if saldo_2fase > 0:
        pena_inter_days = min(max_days, pena_base_days + (delta_2fase * saldo_2fase))
    elif saldo_2fase < 0:
        proposta_days = pena_base_days - (delta_2fase * abs(saldo_2fase))
        if cfg.aplicar_sumula_231_stj and proposta_days < min_days:
            pena_inter_days = min_days
            sumula_231_aplicada = True
            alertas.append(
                "Súmula 231/STJ aplicada: a incidência da circunstância atenuante não pode conduzir à redução da pena abaixo do mínimo legal na segunda fase da dosimetria."
            )
        else:
            pena_inter_days = max(1, proposta_days)
    else:
        pena_inter_days = pena_base_days

    pena_intermediaria = PenaAnosMesesDias.from_total_days(pena_inter_days)
    memoria.append(
        f"2ª Fase (Arts. 61-67 CP): Agravantes={agravantes_ativas or ['nenhuma']} | Atenuantes={atenuantes_ativas or ['nenhuma']} -> Pena Intermediária: {pena_intermediaria.format_extenso()}."
    )

    # 3ª Fase: Causas de Aumento e Diminuição
    pena_def_days = pena_inter_days
    fracao_aumento_aplicada: Tuple[int, int] | None = None
    fracao_diminuicao_aplicada: Tuple[int, int] | None = None
    aumento_147a_aplicado = False
    aumento_141_aplicado = False
    if majorante_24a:
        n, d = majorante_24a.fracao
        pena_def_days += (pena_def_days * n) // d
        fracao_aumento_aplicada = majorante_24a.fracao
        alertas.append(
            f"Art. 24-A, § 4º, da Lei Maria da Penha: aumento de {n}/{d} segundo a decisão do juízo "
            f"e a fonte {majorante_24a.fonte_id}."
        )
    elif entrada.fracao_aumento_terceira_fase:
        n_aum, d_aum = entrada.fracao_aumento_terceira_fase
        pena_def_days = pena_def_days + ((pena_def_days * n_aum) // d_aum)
        fracao_aumento_aplicada = (n_aum, d_aum)
    elif spec.admite_aumento_art141_p3:
        if entrada.aplicar_aumento_art141_p3:
            # Art. 141, § 3º, do CP (Lei nº 14.994/2024): pena em dobro. A fração (1/1) entra também no
            # máximo em abstrato da prescrição (``pena_maxima_abstrata_do_crime``).
            pena_def_days = pena_def_days * 2
            fracao_aumento_aplicada = (1, 1)
            aumento_141_aplicado = True
            alertas.append(
                "Causa de aumento do art. 141, § 3º, do CP aplicada (pena em dobro: crime contra a honra praticado contra a mulher por razões "
                "da condição do sexo feminino — Lei nº 14.994/2024). Confirmar que a majorante está descrita na denúncia/queixa (princípio da correlação)."
            )
        else:
            alertas.append(
                "Causa de aumento do art. 141, § 3º, do CP (pena em dobro: crime contra a honra contra a mulher por razões da condição do sexo feminino — "
                "Lei nº 14.994/2024) NÃO aplicada: parâmetro 'aplicar_aumento_art141_p3' desativado. Fato posterior a 10/10/2024: confirme se a majorante "
                "consta da peça acusatória; se aplicável, ative o parâmetro (a pena e o prazo prescricional em abstrato dobram)."
            )
    elif entrada.crime_code == "art_147a_cp":
        if entrada.aplicar_aumento_art147a_p1_ii:
            # Majorante do § 1º, II, do art. 147-A CP (+1/2).
            pena_def_days = pena_def_days + (pena_def_days // 2)
            fracao_aumento_aplicada = (1, 2)
            aumento_147a_aplicado = True
            alertas.append(
                "Causa de aumento do art. 147-A, § 1º, II, do CP aplicada (+1/2) por decisão expressa do juízo (crime de perseguição contra mulher "
                "por razões da condição de sexo feminino). Confirmar que a majorante está descrita na denúncia (princípio da correlação); "
                "se não estiver, desative o parâmetro 'aplicar_aumento_art147a_p1_ii'."
            )
        else:
            alertas.append(
                "Causa de aumento do art. 147-A, § 1º, II, do CP NÃO aplicada: sem reconhecimento expresso pelo juízo."
            )

    if entrada.fracao_diminuicao_terceira_fase:
        n_dim, d_dim = entrada.fracao_diminuicao_terceira_fase
        pena_def_days = max(1, pena_def_days - ((pena_def_days * n_dim) // d_dim))
        fracao_diminuicao_aplicada = (n_dim, d_dim)

    pena_definitiva = PenaAnosMesesDias.from_total_days(pena_def_days)
    dias_informados = entrada.dias_multa or None
    if dias_informados is not None and dias_informados < 10:
        raise DadosInsuficientesError(["dias_multa"], "Informe de 10 a 360 dias-multa (art. 49 do CP).")
    unidade = entrada.valor_unitario_multa
    if not spec.tem_multa_cumulativa and (dias_informados is not None or unidade is not None):
        campo = "dias_multa" if dias_informados is not None else "valor_unitario_multa"
        imputacao = entrada.id_imputacao or entrada.crime_code
        raise DadosInsuficientesError(
            [f"crimes_config.{imputacao}.{campo}"],
            "Este cálculo de pena privativa de liberdade só admite multa quando a lei a comina cumulativamente.",
        )
    multa_pendente = spec.tem_multa_cumulativa and (dias_informados is None or unidade is None)
    dias_multa_final = dias_informados if not multa_pendente and dias_informados is not None else 0
    dias_sugeridos = None
    if multa_pendente and dias_informados is None:
        base_multa = sugerir_dias_multa(pena_inter_days, min_days, max_days)
        razao_3fase = pena_def_days / pena_inter_days if pena_inter_days > 0 else 1.0
        dias_sugeridos = int(min(360, max(10, round(base_multa * razao_3fase))))
    if multa_pendente:
        alerta_multa = f"Multa de {spec.artigo_formatado} pendente de decisão do juízo sobre quantidade e valor unitário."
        if dias_sugeridos is not None:
            alerta_multa += f" Sugestão de cálculo: {dias_sugeridos} dias-multa."
        alertas.append(alerta_multa)
    multa_txt = f" e {dias_multa_final} dias-multa" if dias_multa_final > 0 else ""
    memoria.append(
        f"3ª Fase (Causas de Aumento/Diminuição): Pena Definitiva fixada em {pena_definitiva.format_extenso()} de {formatar_especie_pena(spec.especie_pena)}{multa_txt}."
    )

    # Capitulação: a agravante do art. 61, II, 'f', só consta quando efetivamente aplicada.
    if any("art. 61, II, 'f'" in a for a in agravantes_ativas) and "art. 61" not in spec.artigo_formatado:
        spec = spec.model_copy(
            update={"artigo_formatado": f"{spec.artigo_formatado} c/c art. 61, inciso II, alínea 'f', do Código Penal"}
        )

    if entrada.violencia_ou_grave_ameaca is not None:
        violencia_efetiva: bool | None = bool(entrada.violencia_ou_grave_ameaca)
    elif spec.violencia_ou_grave_ameaca_ambigua:
        violencia_efetiva = None
    else:
        violencia_efetiva = bool(spec.has_violence_or_grave_threat)

    return CrimeDosimetriaOutput(
        id_imputacao=entrada.id_imputacao,
        referencia_imputacao=entrada.referencia_imputacao,
        data_recebimento_individual=entrada.data_recebimento_individual,
        recebimento_por_imputacao=entrada.recebimento_por_imputacao,
        reduzir_prescricao_art115=entrada.reduzir_prescricao_art115,
        crime_spec=spec,
        vetores_negativos_efetivos=vetores_negativos,
        acrescimo_por_vetor_dias=passo_vetor,
        pena_base=pena_base,
        bis_in_idem_art61f_bloqueado=bis_in_idem_bloqueado,
        compensacao_confissao_reincidencia=compensacao,
        sumula_231_aplicada=sumula_231_aplicada,
        pena_intermediaria=pena_intermediaria,
        pena_definitiva=pena_definitiva,
        agravantes_ativas=agravantes_ativas,
        atenuantes_ativas=atenuantes_ativas,
        alertas_juridicos=alertas,
        memoria_calculo=memoria,
        vetores_detalhados=vetores_detalhados,
        dias_multa=dias_multa_final,
        desclassificado_de=entrada.desclassificado_de,
        data_fato=entrada.data_fato,
        reincidente=entrada.reincidente,
        fracao_aumento_aplicada=fracao_aumento_aplicada,
        fracao_diminuicao_aplicada=fracao_diminuicao_aplicada,
        aumento_art147a_aplicado=aumento_147a_aplicado,
        aumento_art141_p3_aplicado=aumento_141_aplicado,
        majorante_art24a_p4=majorante_24a,
        dias_multa_informados=dias_informados,
        valor_unitario_multa=unidade,
        dias_multa_sugeridos=dias_sugeridos,
        multa_pendente=multa_pendente,
        dias_multa_sugeridos_pelo_motor=dias_sugeridos is not None,
        violencia_ou_grave_ameaca_efetiva=violencia_efetiva,
    )


def sugerir_dias_multa(pena_definitiva_dias: int, pena_min_dias: int, pena_max_dias: int) -> int:
    """Sugere dias-multa proporcionais à posição da pena privativa no intervalo cominado.

    Critério da proporcionalidade (art. 49 c/c art. 68 do CP): pena no mínimo => 10 dias-multa;
    pena no máximo => 360 dias-multa; interpolação linear entre ambos, limitada a [10, 360].
    """
    intervalo = max(0, pena_max_dias - pena_min_dias)
    if intervalo == 0:
        return 10
    posicao = (pena_definitiva_dias - pena_min_dias) / intervalo
    posicao = min(1.0, max(0.0, posicao))
    return int(min(360, max(10, round(10 + posicao * 350))))


RegimeLiteral = Literal["aberto", "semiaberto", "fechado"]
_GRAVIDADE_REGIME = {"aberto": 0, "semiaberto": 1, "fechado": 2}
_ROTULO_ESPECIE_PLURAL = {"reclusao": "reclusão", "detencao": "detenção", "prisao_simples": "prisão simples"}


def calcular_regime_art33(
    anos_pena: float,
    reincidente: bool,
    circunstancias_desfavoraveis: bool,
    especie_pena: str = "reclusao",
) -> Tuple[RegimeLiteral, str]:
    """Regime inicial sugerido (art. 33, §§ 2º e 3º, do CP; Súmulas 269 e 440/STJ, 718 e 719/STF).

    Detenção e prisão simples NUNCA iniciam no regime fechado (art. 33, caput, do CP e
    art. 6º da LCP), qualquer que seja o quantum: o teto é o semiaberto.
    Retorna (regime, regra aplicada em texto).
    """
    regime: RegimeLiteral
    if anos_pena > 8.0:
        regime, regra = "fechado", "pena superior a 8 anos (art. 33, § 2º, 'a', do CP)"
    elif anos_pena > 4.0:
        if reincidente:
            regime, regra = "fechado", "réu reincidente com pena superior a 4 anos (art. 33, § 2º, 'b', do CP, a contrario sensu)"
        else:
            regime, regra = "semiaberto", "réu não reincidente com pena superior a 4 e não excedente a 8 anos (art. 33, § 2º, 'b', do CP)"
    elif reincidente:
        if circunstancias_desfavoraveis:
            regime, regra = "fechado", "réu reincidente com pena até 4 anos e circunstâncias judiciais desfavoráveis (Súmula 269/STJ, a contrario sensu)"
        else:
            regime, regra = "semiaberto", "réu reincidente com pena até 4 anos e circunstâncias judiciais favoráveis (Súmula 269/STJ)"
    elif circunstancias_desfavoraveis and anos_pena > 2.0:
        regime, regra = "semiaberto", "réu não reincidente com pena até 4 anos, mas com circunstâncias judiciais desfavoráveis (art. 33, § 3º, c/c art. 59 do CP)"
    else:
        regime, regra = "aberto", "réu não reincidente com pena não superior a 4 anos (art. 33, § 2º, 'c', do CP)"

    if especie_pena in ("detencao", "prisao_simples") and regime == "fechado":
        regime = "semiaberto"
        regra += (
            "; tratando-se de pena de detenção/prisão simples, o regime inicial não pode ser o fechado "
            "(art. 33, caput, do CP e art. 6º da LCP), fixando-se o semiaberto"
        )
    return regime, regra


def calcular_dosimetria_global(
    crimes: List[CrimeDosimetriaInput],
    config: DosimetriaConfig | None = None,
    concurso: ConcursoCrimesConfig | None = None,
    pedido_expresso_dano_moral_mp: bool = False,
    valor_dano_moral_decidido: float | None = None,
    dias_prisao_cautelar: int = 0,
    crimes_nao_condenados: List[Dict[str, str]] | None = None,
    *,
    valor_dano_moral_pedido: float | None = None,
    valor_dano_moral_sugerido: float | None = None,
) -> DosimetriaGlobalOutput:
    """Unifica as penas, fixa regime, substituição, sursis e reparação mínima (art. 387, IV, CPP).

    Com pedido expresso, somente o valor decidido pode integrar a condenação.
    Zero registra a decisão de deixar de fixar reparação nesta sentença.
    """
    cfg = config or DosimetriaConfig()
    conc = concurso or ConcursoCrimesConfig(tipo_concurso="material" if len(crimes) > 1 else "nenhum")
    nao_condenados = list(crimes_nao_condenados or [])
    valor_dano = valor_dano_moral_decidido
    dados_dano = {
        "valor_dano_moral_pedido": valor_dano_moral_pedido,
        "valor_dano_moral_sugerido": valor_dano_moral_sugerido,
    }

    if not crimes:
        zero_p = PenaAnosMesesDias(anos=0, meses=0, dias=0)
        return DosimetriaGlobalOutput(
            crimes_calculados=[],
            penas_por_especie={},
            pena_total_unificada=zero_p,
            detracao_dias=0,
            pena_remanescente_detracao=None,
            regime_inicial="aberto",
            substituicao_art44_cabivel=False,
            motivo_vedacao_substituicao="Prejudicada a substituição da pena ante a ausência de condenação criminal.",
            sursis_art77_cabivel=False,
            fundamentacao_sursis="Prejudicada a suspensão condicional da pena ante a ausência de condenação criminal.",
            sursis_condicoes_legais=[],
            indenizacao_dano_moral_tema983=0.0,
            alertas_globais=[
                "Ausência de condenação penal: dosimetria e reparação mínima do art. 387, IV, do CPP prejudicadas."
            ],
            tipo_concurso="nenhum",
            fracao_concurso=None,
            total_dias_multa=0,
            sem_condenacao=True,
            crimes_nao_condenados=nao_condenados,
            fundamentacao_regime="Prejudicada a fixação de regime ante a ausência de condenação criminal.",
            dano_moral_pedido_mp=bool(pedido_expresso_dano_moral_mp),
            **dados_dano,
        )

    calculados = [calcular_dosimetria_crime(c, cfg) for c in crimes]
    penas_especie_days: Dict[str, int] = {}

    for item in calculados:
        esp = item.crime_spec.especie_pena
        penas_especie_days[esp] = penas_especie_days.get(esp, 0) + item.pena_definitiva.to_total_days()

    sum_days = sum(c.pena_definitiva.to_total_days() for c in calculados)
    tipo_concurso_aplicado: Literal["nenhum", "material", "formal", "continuado"] = (
        conc.tipo_concurso if len(calculados) > 1 else "nenhum"
    )
    fracao_aplicada: Tuple[int, int] | None = None
    fracao_sugerida: Tuple[int, int] | None = None
    alertas_concurso: List[str] = []
    tipo_reconhecido: Literal["nenhum", "material", "formal", "continuado"] = tipo_concurso_aplicado
    concurso_beneficio = False

    if tipo_concurso_aplicado in ("formal", "continuado") and len(calculados) > 1:
        fracao_sugerida = sugerir_fracao_concurso(tipo_concurso_aplicado, len(calculados))
        rotulo_conc = "concurso formal (art. 70 do CP)" if tipo_concurso_aplicado == "formal" else "crime continuado (art. 71 do CP)"
        if conc.fracao_exasperacao:
            num_e, den_e = conc.fracao_exasperacao
            if fracao_sugerida and (num_e, den_e) != fracao_sugerida:
                alertas_concurso.append(
                    f"Fração de exasperação do {rotulo_conc} informada pelo juízo ({num_e}/{den_e}) difere da sugerida pelo número de "
                    f"infrações ({len(calculados)} crimes => {fracao_sugerida[0]}/{fracao_sugerida[1]}); prevalece a fração do juízo, que deve ser fundamentada."
                )
        else:
            num_e, den_e = fracao_sugerida or (1, 6)
            alertas_concurso.append(
                f"Fração de exasperação do {rotulo_conc} sugerida pelo sistema conforme o número de infrações "
                f"({len(calculados)} crimes => {num_e}/{den_e}, critério consolidado no STJ"
                + ("; Súmula 659/STJ" if tipo_concurso_aplicado == "continuado" else "")
                + "). O juízo pode sobrescrevê-la."
            )
        maior_crime = max(
            calculados,
            key=lambda c: (
                c.pena_definitiva.to_total_days(),
                1 if c.crime_spec.especie_pena == "reclusao" else 0,
            ),
        )
        maior_days = maior_crime.pena_definitiva.to_total_days()
        exasp_days = maior_days + ((maior_days * num_e) // den_e)
        if exasp_days > sum_days:
            # Concurso material benéfico (art. 70, parágrafo único, do CP): o tipo reconhecido
            # (formal/continuado) é preservado em ``tipo_concurso_reconhecido`` — a redação do
            # gerador não pode afirmar pluralidade de condutas/desígnios autônomos.
            total_days = sum_days
            tipo_reconhecido = tipo_concurso_aplicado
            concurso_beneficio = True
            tipo_concurso_aplicado = "material"
            alertas_concurso.append(
                f"Concurso material benéfico aplicado (art. 70, parágrafo único, do CP): reconhecido o {rotulo_conc}, "
                "a exasperação superaria o cúmulo material das penas, aplicando-se a soma (mais benéfica ao réu)."
            )
        else:
            total_days = exasp_days
            fracao_aplicada = (num_e, den_e)
            penas_especie_days = {maior_crime.crime_spec.especie_pena: total_days}
    else:
        total_days = sum_days

    pena_total = PenaAnosMesesDias.from_total_days(total_days)
    penas_por_especie = {
        k: PenaAnosMesesDias.from_total_days(v) for k, v in penas_especie_days.items()
    }
    if any(c.multa_pendente for c in calculados):
        total_dias_multa = 0
    elif tipo_concurso_aplicado == "continuado" and fracao_aplicada:
        # Crime continuado: o art. 72 do CP não se aplica (STJ); a multa segue o art. 71 — a do crime mais
        # grave (maior quantum) exasperada pela mesma fração da pena privativa de liberdade.
        maior_multa = max((c.dias_multa for c in calculados), default=0)
        total_dias_multa = maior_multa + (maior_multa * fracao_aplicada[0]) // fracao_aplicada[1]
        if maior_multa > 0:
            alertas_concurso.append(
                f"Multa no crime continuado: não incide o art. 72 do CP (soma das multas); aplica-se a multa de um só crime ({maior_multa} dias-multa) "
                f"exasperada de {fracao_aplicada[0]}/{fracao_aplicada[1]} (art. 71 do CP), totalizando {total_dias_multa} dias-multa."
            )
    else:
        total_dias_multa = sum(c.dias_multa for c in calculados)

    algum_reincidente = any(c.reincidente for c in crimes)
    algum_vetores_negativos = any(len(c.vetores_negativos_efetivos) > 0 for c in calculados)
    # Súmula 588/STJ: só com violência/grave ameaça CONFIRMADA. Tipos com modalidades com e sem
    # (147-A, 147-B, 150, § 1º, 163, p.u.) ficam pendentes da decisão do juízo quando não informada.
    alguma_violencia_ou_ameaca = any(c.violencia_ou_grave_ameaca_efetiva is True for c in calculados)
    modalidade_pendente = any(c.violencia_ou_grave_ameaca_efetiva is None for c in calculados)
    vetores_art77_negativos = [
        v for v in VETORES_ART_77_II if any(v in c.vetores_negativos_efetivos for c in calculados)
    ]
    rotulos_art77_negativos = [ROTULOS_VETORES_ART_59.get(v, v).lower() for v in vetores_art77_negativos]
    alertas_regras: List[str] = []

    # Regime Inicial (Art. 33, §§ 2º e 3º, CP) — por espécie de pena.
    # Regra documentada para penas mistas: o quantum considerado é a soma/unificação de todas
    # as penas (art. 111 da LEP); para cada espécie aplica-se o art. 33, §§ 2º e 3º, com a
    # ressalva de que detenção/prisão simples nunca iniciam no fechado (art. 33, caput, CP).
    # O regime "global" é o da espécie mais grave, executada em primeiro lugar (arts. 69 e 76 do CP).
    # Detração (art. 387, § 2º, do CPP): a prisão cautelar já cumprida é considerada para a fixação do
    # regime inicial — o art. 33 incide sobre a pena REMANESCENTE (pena total menos os dias detraídos).
    # Substituição (art. 44) e sursis (art. 77) continuam aferidos pela pena aplicada (``anos_totais``).
    anos_totais = total_days / 360.0
    detracao_efetiva = max(0, int(dias_prisao_cautelar))
    dias_regime = max(0, total_days - detracao_efetiva) if detracao_efetiva > 0 else total_days
    anos_regime = dias_regime / 360.0
    especies_ordenadas = sorted(penas_especie_days.keys(), key=lambda e: {"reclusao": 0, "detencao": 1, "prisao_simples": 2}.get(e, 3))
    regime_por_especie: Dict[str, RegimeLiteral] = {}
    regime_por_especie_sem_detracao: Dict[str, RegimeLiteral] = {}
    regras_regime: List[str] = []
    for especie in especies_ordenadas:
        reg_esp, regra_esp = calcular_regime_art33(anos_regime, algum_reincidente, algum_vetores_negativos, especie)
        regime_por_especie[especie] = reg_esp
        regime_por_especie_sem_detracao[especie] = calcular_regime_art33(
            anos_totais, algum_reincidente, algum_vetores_negativos, especie
        )[0]
        regras_regime.append(f"pena de {_ROTULO_ESPECIE_PLURAL.get(especie, especie)}: regime {reg_esp.upper()} — {regra_esp}")
    regime: RegimeLiteral = max(regime_por_especie.values(), key=lambda r: _GRAVIDADE_REGIME[r]) if regime_por_especie else "aberto"
    regime_sem_detracao: RegimeLiteral | None = None
    pena_considerada_regime: PenaAnosMesesDias | None = None
    if detracao_efetiva > 0:
        regime_sem_detracao = (
            max(regime_por_especie_sem_detracao.values(), key=lambda r: _GRAVIDADE_REGIME[r])
            if regime_por_especie_sem_detracao
            else "aberto"
        )
        pena_considerada_regime = PenaAnosMesesDias.from_total_days(dias_regime)
        fundamentacao_regime = (
            f"Quantum total das penas: {pena_total.format_extenso()} (soma/unificação — art. 111 da LEP). "
            f"Detração de {detracao_efetiva} dia(s) de prisão cautelar (art. 387, § 2º, do CPP): pena remanescente considerada para o regime de "
            f"{pena_considerada_regime.format_extenso()}. "
            + "; ".join(regras_regime)
            + "."
        )
        if regime_sem_detracao != regime:
            fundamentacao_regime += (
                f" A detração alterou o regime inicial: sem ela seria o {regime_sem_detracao.upper()} (pena total de {pena_total.format_extenso()}); "
                f"com o desconto do período de prisão cautelar, fixa-se o {regime.upper()}."
            )
    else:
        fundamentacao_regime = (
            f"Quantum considerado para o regime: {pena_total.format_extenso()} (soma/unificação das penas — art. 111 da LEP). "
            + "; ".join(regras_regime)
            + "."
        )
    if len(regime_por_especie) > 1:
        alertas_regras.append(
            "Penas de espécies distintas (reclusão e detenção/prisão simples): regime fixado por espécie sobre o quantum unificado "
            "(art. 111 da LEP), executando-se primeiro a reclusão (arts. 69 e 76 do CP); a detenção não inicia no regime fechado."
        )

    # Substituição por Restritivas de Direitos (Art. 44 CP + Súmula 588 STJ)
    subst_pendente = False
    if alguma_violencia_ou_ameaca:
        subst_cabivel = False
        motivo_vedacao = (
            "Incabível a substituição da pena privativa de liberdade por restritiva de direitos (art. 44, I, do CP), "
            "nos termos da Súmula 588/STJ ('A prática de crime ou contravenção penal contra a mulher com violência ou grave ameaça no ambiente doméstico impossibilita a substituição da pena privativa de liberdade por restritiva de direitos')."
        )
    elif anos_totais > 4.0 or algum_reincidente:
        subst_cabivel = False
        motivo_vedacao = "Incabível a substituição da pena privativa de liberdade por restritiva de direitos por ausência dos requisitos do art. 44 do Código Penal."
    elif modalidade_pendente:
        # Tipo com modalidades com e sem violência/grave ameaça, sem decisão do juízo: a Súmula 588/STJ
        # não pode ser afirmada nem afastada automaticamente.
        subst_cabivel = False
        subst_pendente = True
        motivo_vedacao = (
            "Substituição da pena privativa de liberdade por restritiva de direitos (art. 44 do CP) não concedida automaticamente: o tipo penal "
            "admite modalidades praticadas com e sem violência ou grave ameaça à pessoa, e a Súmula 588/STJ só incide na primeira. "
            "[COMPLETAR PELO JUÍZO: se a conduta foi praticada com violência ou grave ameaça (vedação do art. 44, I, do CP e Súmula 588/STJ) "
            "ou sem elas (art. 44 do CP admitido, se preenchidos os demais requisitos)]."
        )
        alertas_regras.append(
            "Substituição (art. 44 do CP) pendente de decisão do juízo: tipo penal com modalidades com e sem violência ou grave ameaça "
            "(147-A, 147-B, 150, § 1º, 163, p.u.). Informe 'violencia_ou_grave_ameaca' (crimes_config) para definir a incidência da Súmula 588/STJ."
        )
    elif vetores_art77_negativos:
        subst_cabivel = False
        motivo_vedacao = (
            "Incabível a substituição da pena privativa de liberdade por restritiva de direitos, pois a valoração negativa das circunstâncias judiciais "
            f"({', '.join(rotulos_art77_negativos)}) não indica que a substituição seja suficiente (art. 44, III, do CP)."
        )
        alertas_regras.append(
            "Substituição (art. 44 do CP) não concedida automaticamente: há circunstância(s) judicial(is) desfavorável(is) "
            f"({', '.join(rotulos_art77_negativos)}) — art. 44, III. Reavalie se entender a substituição suficiente."
        )
    else:
        subst_cabivel = True
        motivo_vedacao = "Preenchidos os requisitos do art. 44 do CP (delito praticado sem violência ou grave ameaça à pessoa)."

    # Suspensão Condicional da Pena - Sursis (Art. 77 CP)
    sursis_negado_circ = False
    sursis_condicoes: List[str] = []
    if subst_pendente:
        sursis_cabivel = False
        fund_sursis = (
            "Suspensão condicional da pena (art. 77 do CP) não concedida automaticamente: depende da definição da modalidade do crime "
            "(com ou sem violência/grave ameaça) e, por conseguinte, da substituição do art. 44 do CP. "
            "[COMPLETAR PELO JUÍZO: concessão ou denegação do sursis, conforme a decisão sobre a substituição]."
        )
    elif anos_totais <= 2.0 and not algum_reincidente and not subst_cabivel and vetores_art77_negativos:
        sursis_cabivel = False
        sursis_negado_circ = True
        fund_sursis = (
            "Embora a pena aplicada não supere 2 (dois) anos e o réu não seja reincidente em crime doloso, deixo de conceder a suspensão "
            f"condicional da pena, pois a valoração negativa das circunstâncias judiciais ({', '.join(rotulos_art77_negativos)}) "
            "não autoriza o benefício (art. 77, II, do Código Penal)."
        )
        alertas_regras.append(
            "Sursis (art. 77 do CP) NÃO concedido automaticamente: requisitos objetivos presentes, mas há circunstância(s) judicial(is) "
            f"desfavorável(is) ({', '.join(rotulos_art77_negativos)}) — art. 77, II. Cabe ao juízo decidir fundamentadamente."
        )
    elif anos_totais <= 2.0 and not algum_reincidente and not subst_cabivel:
        sursis_cabivel = True
        fund_sursis = (
            "Preenchidos os requisitos objetivos e subjetivos do art. 77 do Código Penal (pena não superior a 2 anos, réu não reincidente em crime doloso, "
            "circunstâncias judiciais do art. 77, II, favoráveis e incabível a substituição do art. 44 do CP), "
            "concedo a suspensão condicional da execução da pena (Sursis Penal) pelo prazo de 2 (dois) anos (arts. 77 a 82 do Código Penal), "
            "nos termos dos arts. 77 e 78 do Código Penal, art. 158 da Lei nº 7.210/1984 (LEP) e art. 45 da Lei nº 11.340/2006."
        )
        sursis_condicoes = [
            "a) No primeiro ano do período de prova, prestar serviços à comunidade (art. 46 do CP) ou submeter-se à limitação de fim de semana (art. 48 do CP), conforme designação do Juízo da Execução Penal (art. 78, § 1º, do CP);",
            "b) Comparecimento obrigatório a programas de recuperação, reeducação e grupos reflexivos para autores de violência doméstica e familiar contra a mulher (art. 152, parágrafo único, da LEP e art. 22, VI e VII, da Lei nº 11.340/2006);",
            "c) Proibição de frequentar a residência ou o local de trabalho da ofendida, bem como de manter qualquer contato hostil ou intimidatório (art. 78, § 2º, 'a', do CP);",
            "d) Proibição de ausentar-se da comarca onde reside por mais de 8 (oito) dias sem prévia autorização do Juízo da Execução (art. 78, § 2º, 'b', do CP); e",
            "e) Comparecimento pessoal e obrigatório em Juízo, mensal/bimestralmente conforme fixado na audiência admonitória, para informar e justificar suas atividades (art. 78, § 2º, 'c', do CP).",
        ]
    else:
        sursis_cabivel = False
        fund_sursis = (
            "Incabível a suspensão condicional da pena (art. 77 do CP) porquanto a reprimenda total supera o patamar de 2 (dois) anos ou ausentes os requisitos subjetivos/objetivos legais."
        )

    pena_remanescente = (
        PenaAnosMesesDias.from_total_days(max(0, total_days - detracao_efetiva))
        if detracao_efetiva > 0
        else None
    )

    alertas_globais = [
        "Súmula 536/STJ e art. 41 da Lei 11.340/2006: inaplicáveis os institutos despenalizadores da Lei nº 9.099/1995 (transação penal, composição civil extintiva e suspensão condicional do processo).",
        "Súmula 589/STJ: inaplicável o princípio da insignificância nos crimes ou contravenções praticados contra a mulher no âmbito das relações domésticas.",
    ]

    # Reparação mínima (art. 387, IV, CPP e Tema Repetitivo 983/STJ)
    indenizacao = 0.0
    arbitramento_pendente = False
    if pedido_expresso_dano_moral_mp:
        if valor_dano is not None:
            indenizacao = valor_dano
            alertas_globais.append(
                f"Tema Repetitivo 983/STJ aplicado: fixado valor mínimo indenizatório a título de danos morais in re ipsa em R$ {indenizacao:,.2f}."
                if valor_dano > 0 else "O juízo decidiu deixar de fixar valor mínimo para a reparação de danos morais."
            )
        else:
            arbitramento_pendente = True
            alertas_globais.append(
                "Dano moral (Tema Repetitivo 983/STJ): HOUVE pedido expresso de reparação mínima e o juízo não decidiu o arbitramento — "
                "arbitramento pendente. Informe o valor a ser fixado; o dispositivo NÃO pode afirmar ausência de pedido."
            )
    elif valor_dano is not None and valor_dano > 0:
        alertas_globais.append(
            "Valor de dano moral informado desconsiderado: não consta pedido expresso de reparação mínima na denúncia "
            "(art. 387, IV, do CPP e Tema Repetitivo 983/STJ exigem pedido expresso). Se houver pedido nos autos, corrija os metadados."
        )

    alertas_globais.extend(alertas_concurso)
    alertas_globais.extend(alertas_regras)

    return DosimetriaGlobalOutput(
        crimes_calculados=calculados,
        penas_por_especie=penas_por_especie,
        pena_total_unificada=pena_total,
        detracao_dias=detracao_efetiva,
        pena_remanescente_detracao=pena_remanescente,
        regime_inicial=regime,
        substituicao_art44_cabivel=subst_cabivel,
        motivo_vedacao_substituicao=motivo_vedacao,
        sursis_art77_cabivel=sursis_cabivel,
        fundamentacao_sursis=fund_sursis,
        sursis_condicoes_legais=sursis_condicoes,
        indenizacao_dano_moral_tema983=indenizacao,
        alertas_globais=alertas_globais,
        tipo_concurso=tipo_concurso_aplicado,
        fracao_concurso=fracao_aplicada,
        total_dias_multa=total_dias_multa,
        sem_condenacao=False,
        crimes_nao_condenados=nao_condenados,
        regime_por_especie=regime_por_especie,
        fundamentacao_regime=fundamentacao_regime,
        fracao_concurso_sugerida=fracao_sugerida,
        sursis_negado_por_circunstancias_judiciais=sursis_negado_circ,
        circunstancias_desfavoraveis_art77_ii=vetores_art77_negativos,
        dano_moral_pedido_mp=bool(pedido_expresso_dano_moral_mp),
        dano_moral_arbitramento_pendente=arbitramento_pendente,
        valor_dano_moral_decidido=valor_dano if pedido_expresso_dano_moral_mp else None,
        **dados_dano,
        regime_sem_detracao=regime_sem_detracao,
        pena_considerada_regime=pena_considerada_regime,
        concurso_material_beneficio=concurso_beneficio,
        tipo_concurso_reconhecido=tipo_reconhecido,
        substituicao_pendente_modalidade=subst_pendente,
    )


# ==============================================================================
# Prescrição da pretensão punitiva (arts. 109, 110, 115, 117 e 119 do CP)
# ==============================================================================
_INCISO_ART109 = {20: "I", 16: "II", 12: "III", 8: "IV", 4: "V", 3: "VI", 2: "VI (redação anterior à Lei nº 12.234/2010)"}


def somar_meses(data: datetime.date, meses: int) -> datetime.date:
    """Soma meses pelo calendário comum (art. 10 do CP), ajustando o dia ao fim do mês quando necessário."""
    total = data.year * 12 + (data.month - 1) + int(meses)
    ano, mes0 = divmod(total, 12)
    mes = mes0 + 1
    dia = min(data.day, calendar.monthrange(ano, mes)[1])
    return datetime.date(ano, mes, dia)


def prazo_prescricional_art109_anos(pena: PenaAnosMesesDias, data_fato: datetime.date | None = None) -> int:
    """Prazo do art. 109 do CP para a pena dada (pena em concreto ou máximo cominado).

    O inciso VI ("inferior a 1 ano") passou de 2 para 3 anos com a Lei nº 12.234/2010 (vigente em
    06/05/2010), que não retroage: fatos anteriores mantêm o prazo de 2 anos.
    """
    dias_pena = pena.to_total_days()
    if dias_pena < 360:  # inferior a 1 ano
        if data_fato is not None and data_fato < DATA_VIGENCIA_LEI_12234_2010:
            return 2
        return 3
    if dias_pena <= 720:  # igual a 1 ano ou, sendo superior, não excede a 2
        return 4
    if dias_pena <= 1440:  # superior a 2 e não excede a 4
        return 8
    if dias_pena <= 2880:  # superior a 4 e não excede a 8
        return 12
    if dias_pena <= 4320:  # superior a 8 e não excede a 12
        return 16
    return 20


def _lapso_consumado(inicio: datetime.date, fim: datetime.date, prazo_meses: int) -> bool:
    """O prazo, contado com inclusão do dia do começo (art. 10 do CP), consuma-se na data-aniversário."""
    return fim >= somar_meses(inicio, prazo_meses)


def _fmt_data(d: datetime.date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "não informada"


def _prescricao_um_crime(
    crime_code: str,
    crime_nome: str,
    artigo_formatado: str,
    pena_concreta: PenaAnosMesesDias,
    pena_maxima_abstrata: PenaAnosMesesDias | None,
    data_fato: datetime.date | None,
    data_recebimento_denuncia: datetime.date | None,
    data_sentenca: datetime.date,
    reduzir_metade_art115: bool,
) -> PrescricaoCrimeOutput:
    alertas: List[str] = []
    lei_nova = data_fato is None or data_fato >= DATA_VIGENCIA_LEI_12234_2010
    verificacao_completa = True

    prazo_c_anos = prazo_prescricional_art109_anos(pena_concreta, data_fato)
    pena_max = pena_maxima_abstrata or pena_concreta
    prazo_a_anos = prazo_prescricional_art109_anos(pena_max, data_fato)
    prazo_c_meses = prazo_c_anos * 12 // (2 if reduzir_metade_art115 else 1)
    prazo_a_meses = prazo_a_anos * 12 // (2 if reduzir_metade_art115 else 1)
    rotulo = f"o crime do {artigo_formatado}" if artigo_formatado else "a pena aplicada"

    if data_fato is None:
        verificacao_completa = False
        alertas.append(f"Data do fato não informada para {rotulo}: lapso fato → recebimento da denúncia não verificado.")
    if data_recebimento_denuncia is None:
        verificacao_completa = False
        alertas.append(
            f"Data de recebimento da denúncia não informada para {rotulo}: sem o marco interruptivo (art. 117, I, do CP) "
            "a prescrição não pode ser reconhecida pelo sistema — informe a data."
        )

    # 1) Prescrição em abstrato (pena máxima cominada, art. 109) — independe de trânsito para a acusação.
    abstrata = False
    if pena_maxima_abstrata is not None:
        if data_fato and data_recebimento_denuncia and _lapso_consumado(data_fato, data_recebimento_denuncia, prazo_a_meses):
            abstrata = True
        if data_recebimento_denuncia and _lapso_consumado(data_recebimento_denuncia, data_sentenca, prazo_a_meses):
            abstrata = True

    # 2) Prescrição pela pena em concreto (retroativa — art. 110, § 1º).
    retroativa = False
    lapsos: List[str] = []
    if data_recebimento_denuncia:
        lapsos.append("recebimento da denúncia → sentença")
        if _lapso_consumado(data_recebimento_denuncia, data_sentenca, prazo_c_meses):
            retroativa = True
        if not lei_nova and data_fato:
            lapsos.insert(0, "fato → recebimento da denúncia")
            if _lapso_consumado(data_fato, data_recebimento_denuncia, prazo_c_meses):
                retroativa = True

    reconhecida = abstrata or retroativa
    if retroativa and not abstrata:
        alertas.append(
            f"Prescrição pela pena em concreto ({rotulo}): seu reconhecimento pressupõe o trânsito em julgado para a acusação "
            "ou o improvimento de seu recurso (art. 110, § 1º, do CP) — verifique o momento processual adequado para declará-la."
        )

    inciso_c = _INCISO_ART109.get(prazo_c_anos, "")
    inciso_a = _INCISO_ART109.get(prazo_a_anos, "")
    reducao_txt = ", reduzido de metade pelo art. 115 do CP" if reduzir_metade_art115 else ""
    cabecalho = f"{crime_nome or crime_code} ({artigo_formatado}): a pena aplicada" if artigo_formatado else "A pena aplicada"
    partes = [
        f"{cabecalho} de {pena_concreta.format_extenso()} "
        f"atrai o prazo prescricional de {prazo_c_anos} anos (art. 109, {inciso_c}, do CP){reducao_txt}"
    ]
    if pena_maxima_abstrata is not None:
        partes.append(
            f"; a pena máxima cominada de {pena_max.format_extenso()} corresponde ao prazo de {prazo_a_anos} anos em abstrato (art. 109, {inciso_a}, do CP){reducao_txt}"
        )
    partes.append(". ")
    if lei_nova:
        partes.append(
            "Tratando-se de fato praticado a partir de 06/05/2010, a prescrição pela pena em concreto não pode ter por termo inicial data anterior "
            "ao recebimento da denúncia (art. 110, § 1º, do CP, com a redação da Lei nº 12.234/2010); o lapso entre o fato e o recebimento "
            "é aferido apenas pela pena máxima em abstrato. "
        )
    else:
        partes.append(
            "Tratando-se de fato anterior à Lei nº 12.234/2010 (vigente em 06/05/2010), que não retroage, a prescrição pela pena em concreto "
            "considera também o lapso entre a data do fato e o recebimento da denúncia (redação anterior do art. 110, §§ 1º e 2º, do CP). "
        )
    partes.append(
        f"Marcos: fato em {_fmt_data(data_fato)}; recebimento da denúncia em {_fmt_data(data_recebimento_denuncia)} (art. 117, I); "
        f"sentença em {_fmt_data(data_sentenca)} (art. 117, IV). "
    )
    if abstrata:
        partes.append("Consumou-se a prescrição da pretensão punitiva pela pena máxima em abstrato (art. 107, IV, c/c art. 109 do CP).")
    elif retroativa:
        partes.append(
            "A pena ora aplicada conduzirá à prescrição retroativa da pretensão punitiva (art. 107, IV, c/c art. 110, § 1º, do CP) após o trânsito "
            "em julgado para a acusação (Súmula 438/STJ: vedada a prescrição por pena hipotética); seu reconhecimento não cabe nesta sentença."
        )
    elif not verificacao_completa:
        partes.append("Verificação da prescrição inconclusiva por ausência de marco temporal nos autos; não reconhecida.")
    else:
        partes.append("Não transcorreu o prazo prescricional entre os marcos interruptivos (art. 117 do CP), mantendo-se hígida a pretensão punitiva estatal.")

    return PrescricaoCrimeOutput(
        crime_code=crime_code,
        crime_nome=crime_nome,
        artigo_formatado=artigo_formatado,
        data_fato=data_fato,
        data_recebimento_denuncia=data_recebimento_denuncia,
        data_sentenca=data_sentenca,
        pena_concreta=pena_concreta,
        pena_maxima_abstrata=pena_max,
        prazo_concreto_anos=prazo_c_anos,
        prazo_abstrato_anos=prazo_a_anos,
        prazo_concreto_meses_efetivo=prazo_c_meses,
        prazo_abstrato_meses_efetivo=prazo_a_meses,
        reduzido_metade_art115=reduzir_metade_art115,
        lei_12234_aplicavel=lei_nova,
        lapsos_considerados_retroativa=lapsos,
        prescricao_abstrata_reconhecida=abstrata,
        prescricao_retroativa_reconhecida=retroativa,
        prescricao_reconhecida=reconhecida,
        verificacao_completa=verificacao_completa,
        fundamentacao="".join(partes).strip(),
        alertas=alertas,
    )


def pena_maxima_abstrata_do_crime(crime: CrimeDosimetriaOutput) -> PenaAnosMesesDias:
    """Máximo cominado considerando a causa de aumento aplicada (pela fração aplicada).

    Causas de diminuição são desconsideradas no cálculo em abstrato (critério conservador,
    que nunca antecipa indevidamente a prescrição).
    """
    max_days = crime.crime_spec.max_pena.to_total_days()
    if crime.fracao_aumento_aplicada:
        n, d = crime.fracao_aumento_aplicada
        max_days = max_days + (max_days * n) // d
    return PenaAnosMesesDias.from_total_days(max_days)


def calcular_prescricao_por_crime(
    dosimetria_global: DosimetriaGlobalOutput,
    data_fato: datetime.date | None,
    data_recebimento_denuncia: datetime.date | None,
    data_sentenca: datetime.date | None = None,
    reu_menor_21_na_data_fato: bool = False,
    reu_maior_70_na_sentenca: bool = False,
) -> PrescricaoGlobalOutput:
    """Prescrição da pretensão punitiva verificada isoladamente para cada crime (art. 119 do CP).

    * Usa a pena de cada crime SEM o acréscimo do concurso formal/continuado (Súmula 497/STF).
    * Fatos a partir de 06/05/2010: retroativa só no lapso recebimento → sentença (art. 110, § 1º);
      fatos anteriores: também fato → recebimento.
    * Em abstrato (pena máxima, art. 109): fato → recebimento e recebimento → sentença.
    * Art. 115: metade do prazo se menor de 21 na data do fato ou maior de 70 na sentença.
    * Contagem pelo calendário comum (art. 10 do CP).
    A data do fato de cada crime vem de ``CrimeDosimetriaOutput.data_fato`` quando disponível.
    """
    dt_sent = data_sentenca or datetime.date.today()
    reduz = bool(reu_menor_21_na_data_fato or reu_maior_70_na_sentenca)
    alertas: List[str] = []

    if dosimetria_global.sem_condenacao or not dosimetria_global.crimes_calculados:
        return PrescricaoGlobalOutput(
            por_crime=[],
            prescricao_reconhecida=False,
            crimes_prescritos=[],
            fundamentacao="Prejudicada a análise da prescrição pela pena em concreto ante a ausência de condenação.",
            alertas=[],
        )

    if data_sentenca is None:
        alertas.append(f"Data da sentença não informada: adotada a data de hoje ({_fmt_data(dt_sent)}) como marco do art. 117, IV, do CP.")
    if reduz:
        motivo = "menor de 21 anos na data do fato" if reu_menor_21_na_data_fato else "maior de 70 anos na data da sentença"
        alertas.append(f"Art. 115 do CP: prazos prescricionais reduzidos de metade (réu {motivo}).")

    por_crime: List[PrescricaoCrimeOutput] = []
    for crime in dosimetria_global.crimes_calculados:
        spec = crime.crime_spec
        recebimento = crime.data_recebimento_individual if crime.recebimento_por_imputacao else data_recebimento_denuncia
        reducao = crime.reduzir_prescricao_art115
        item = _prescricao_um_crime(
            crime_code=spec.code,
            crime_nome=spec.nome,
            artigo_formatado=spec.artigo_formatado,
            pena_concreta=crime.pena_definitiva,
            pena_maxima_abstrata=pena_maxima_abstrata_do_crime(crime),
            data_fato=crime.data_fato or data_fato,
            data_recebimento_denuncia=recebimento,
            data_sentenca=dt_sent,
            reduzir_metade_art115=reduz if reducao is None else reducao,
        )
        item.id_imputacao = crime.id_imputacao
        item.referencia_imputacao = crime.referencia_imputacao
        if crime.referencia_imputacao:
            item.fundamentacao = f"{crime.referencia_imputacao}. {item.fundamentacao}"
        por_crime.append(item)
        alertas.extend(item.alertas)

    if len(por_crime) > 1:
        alertas.append("Art. 119 do CP: a extinção da punibilidade incide sobre a pena de cada crime, isoladamente.")
    if dosimetria_global.tipo_concurso in ("formal", "continuado") or dosimetria_global.fracao_concurso:
        alertas.append(
            "Súmula 497/STF: a prescrição regula-se pela pena imposta a cada crime, sem o acréscimo decorrente do concurso formal "
            "ou da continuidade delitiva (art. 119 do CP)."
        )

    crimes_prescritos = [p.crime_code for p in por_crime if p.prescricao_reconhecida]
    intro = (
        "Prescrição da pretensão punitiva analisada isoladamente para cada delito (art. 119 do CP"
        + ("; Súmula 497/STF" if dosimetria_global.tipo_concurso in ("formal", "continuado") or dosimetria_global.fracao_concurso else "")
        + "). "
    )
    fundamentacao = intro + " ".join(p.fundamentacao for p in por_crime)
    # Remove duplicidades preservando a ordem
    alertas_unicos = list(dict.fromkeys(alertas))

    return PrescricaoGlobalOutput(
        por_crime=por_crime,
        prescricao_reconhecida=bool(crimes_prescritos),
        crimes_prescritos=crimes_prescritos,
        fundamentacao=fundamentacao,
        alertas=alertas_unicos,
    )


def calcular_prescricao(
    pena_aplicada: PenaAnosMesesDias,
    data_fato: datetime.date | None,
    data_recebimento_denuncia: datetime.date | None,
    data_sentenca: datetime.date | None = None,
    reu_menor_21_ou_maior_70: bool = False,
    pena_maxima_abstrata: PenaAnosMesesDias | None = None,
) -> PrescricaoOutput:
    """Compatibilidade (v1): prescrição para UMA pena.

    Para sentenças com mais de um crime use ``calcular_prescricao_por_crime`` (art. 119 do CP):
    passar aqui a pena total unificada é incorreto. Aplica o art. 110, § 1º (Lei nº 12.234/2010)
    e a contagem pelo calendário; a verificação em abstrato só ocorre se ``pena_maxima_abstrata``
    for informada.
    """
    dt_sent = data_sentenca or datetime.date.today()
    item = _prescricao_um_crime(
        crime_code="",
        crime_nome="Pena aplicada",
        artigo_formatado="",
        pena_concreta=pena_aplicada,
        pena_maxima_abstrata=pena_maxima_abstrata,
        data_fato=data_fato,
        data_recebimento_denuncia=data_recebimento_denuncia,
        data_sentenca=dt_sent,
        reduzir_metade_art115=reu_menor_21_ou_maior_70,
    )
    hiatos: List[int] = []
    if data_recebimento_denuncia:
        hiatos.append((dt_sent - data_recebimento_denuncia).days)
        if data_fato and (not item.lei_12234_aplicavel or pena_maxima_abstrata is not None):
            hiatos.append((data_recebimento_denuncia - data_fato).days)
    fundamentacao = item.fundamentacao
    return PrescricaoOutput(
        prazo_prescricional_anos=item.prazo_concreto_anos,
        reduzido_metade_art115=reu_menor_21_ou_maior_70,
        prescricao_reconhecida=item.prescricao_reconhecida,
        dias_decorridos_maior_hiato=max(hiatos) if hiatos else 0,
        fundamentacao=fundamentacao,
        prescricao_abstrata_reconhecida=item.prescricao_abstrata_reconhecida,
        prescricao_retroativa_reconhecida=item.prescricao_retroativa_reconhecida,
        lei_12234_aplicavel=item.lei_12234_aplicavel,
        alertas=item.alertas,
    )
