"""Gerador determinístico da sentença penal (JVDFM).

Princípios (docs/v2/PLANO_V2.md):

* Nada de dados inventados: sem crime padrão, sem antecedentes fixos, sem datas fictícias. Dado
  essencial ausente => ``DadosInsuficientesError`` (a API devolve 422 com os campos).
* Juiz decide: na geração de sentença de caso real cada crime imputado exige veredicto explícito
  (``condenar|absolver|desclassificar|extinguir``) em ``crimes_config``; sem ele =>
  ``DecisaoJudicialPendenteError``. A simulação (``exigir_veredicto=False``) presume condenação e
  marca ``simulacao_presume_condenacao``.
* Onde o motor não tem fundamento concreto (preliminares sem resposta específica, teses sem
  tratamento, situação prisional, valor da reparação pedida sem valor etc.) a minuta traz o marcador
  ``[COMPLETAR PELO JUÍZO: ...]``.
* O caso demonstrativo só é reconhecido por ``bundle["is_demo"] is True`` e seu texto vive em
  ``app/demo/demo_sentence.py``.
* Classificação/metadados/matriz oral vêm do ``CaseContext`` (calculados uma vez por conteúdo).
"""

from __future__ import annotations

import datetime
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from app.core.case_context import CaseContext, obter_contexto, obter_contexto_geracao
from app.core.config import load_settings
from app.core.errors import DadosInsuficientesError, DecisaoJudicialPendenteError
from app.core.text_utils import normalizar
from app.demo.demo_sentence import (
    DEMO_CRIMES,
    REPARACAO_ARBITRADA_DEMO,
    construir_inputs_dosimetria_demo,
    gerar_secoes_demo,
)
from app.generation.date_formatter import (
    converter_mencoes_textuais_para_extenso,
    formatar_data_br,
    formatar_moeda_com_extenso,
    mascarar_e_padronizar_datas_texto,
    numero_por_extenso,
    parse_qualquer_data,
)
from app.generation.juizo_sobre_provas import JuizoSobreProvas, resolver_juizo_sobre_provas
from app.generation.prova_oral_confirmada import MARCADOR_PAPEL
from app.generation.decisoes_imputacao import (
    datas_dos_fatos,
    ha_recebimento_por_imputacao,
    resolver_configs_imputacao,
    serializar_decisoes_imputacao,
)
from app.generation.dispositivo import (
    ComandosDispositivo,
    DecisaoAcessoria,
    DecisoesAcessorias,
    construir_comandos_dispositivo,
    decidir_registro_medidas_protetivas,
    resolver_acessorias,
)
from app.ingestion.confissao import AlvoConfissao, TIPOS_AFIRMATIVOS, detectar_confissoes
from app.ingestion.custodia import extrair_intervalos_custodia
from app.ingestion.laya_classifier import (
    CATEGORIAS_SEM_FONTE_DE_FATOS,
    MetadadosProcesso,
    PecaClassificada,
    TeseDefensivaItem,
)
from app.knowledge.knowledge_base import (
    formatar_citacao_acervo,
    obter_doutrina_por_id,
    obter_item_oficial_por_id,
)
from app.knowledge.style_cloner import get_style_profile
from app.legal_engine.detracao import (
    IntervaloCustodia, ResultadoDetracao, calcular_detracao, detracao_por_quantidade,
)
from app.legal_engine.antecedentes import RegistroAntecedente, validar_usos_antecedentes
from app.legal_engine.condicoes_acao_penal import CondicoesAcaoPenal, analisar_acao_penal
from app.legal_engine.linha_tempo_prescricao import LinhaTempoPrescricao
from app.legal_engine.dosimetria_calculator import (
    AlvoPrescricaoAbstrata,
    ROTULOS_VETORES_ART_59,
    VETORES_ART_59,
    CircunstanciaArt59,
    ConcursoCrimesConfig,
    CrimeDosimetriaInput,
    DosimetriaConfig,
    DosimetriaGlobalOutput,
    MajoranteArt24aP4,
    PenaAnosMesesDias,
    PrescricaoGlobalOutput,
    calcular_dosimetria_global,
    calcular_prescricao_por_crime,
    formatar_especie_pena,
)
from app.legal_engine.penal_types_catalog import DATA_VIGENCIA_LEI_14994_2024, get_penal_type_for_date
from app.transcription.oral_proof_analyzer import (
    MatrizCorroboracaoOral,
    consolidar_prova_oral_por_interlocutor,
)

logger = logging.getLogger(__name__)

#: Veredictos aceitos por crime (decisão do juízo).
VEREDICTOS_VALIDOS = ("condenar", "absolver", "desclassificar", "extinguir")

#: Marcador padronizado para trechos que dependem de decisão/dado que só o juízo pode fornecer.
PREFIXO_MARCADOR = "[COMPLETAR PELO JUÍZO:"
_MARCADOR_RE = re.compile(r"\[\s*COMPLETAR\s+PELO\s+JU[ÍI]ZO\b[^\]\n]*\]?")

MOTOR_DETERMINISTICO = "Motor Determinístico: Laya System 1 + Python (Dosimetria e Base Fechada)"


def marcador(descricao: str) -> str:
    """``[COMPLETAR PELO JUÍZO: <descricao>]``."""
    return f"{PREFIXO_MARCADOR} {descricao.strip().rstrip('.')}]"


def listar_marcadores_pendentes(texto: str) -> List[str]:
    """Marcadores ``[COMPLETAR PELO JUÍZO: ...]`` presentes no texto (ordem de aparição, sem repetição)."""
    return list(dict.fromkeys(m.group(0).strip() for m in _MARCADOR_RE.finditer(texto or "")))


class SentencaGerada(BaseModel):
    numero_processo: str
    comarca_juizado: str
    ofendida: str
    reu: str
    data_prolatacao: str
    secao_cabecalho: str
    secao_relatorio: str
    secao_preliminares: str
    secao_merito: str
    secao_dosimetria: str
    secao_dispositivo: str
    texto_completo: str
    dosimetria_dados: Dict[str, Any]
    prescricao_dados: Dict[str, Any]
    motor_utilizado: str
    # v2 — transparência para a API/frontend/auditor
    alertas: List[str] = Field(default_factory=list)
    marcadores_pendentes: List[str] = Field(default_factory=list)
    secoes_fallback: List[str] = Field(default_factory=list)
    simulacao_presume_condenacao: bool = False
    decisoes_crimes: List[Dict[str, Any]] = Field(default_factory=list)
    # Edição na folha: HTML limpo (negrito, alinhamento, recuos...) e formatação da página (margens).
    # Vazio = usar o padrão forense aplicado ao ``texto_completo``.
    texto_html: str = ""
    formatacao: Dict[str, Any] = Field(default_factory=dict)
    revisao_documento: str = ""


# ==============================================================================
# Inferências a partir dos autos (sempre conservadoras)
# ==============================================================================

def inferir_confissao_por_crime(
    pecas: List[PecaClassificada],
    transcriptions: List[Dict[str, Any]],
    crimes_codes: List[str],
) -> Dict[str, bool]:
    """Compatibilidade com o consumidor antigo de sugestões afirmativas integrais."""
    sugestoes, _ = detectar_confissoes(
        pecas, transcriptions, [AlvoConfissao(codigo, codigo) for codigo in crimes_codes],
    )
    return {codigo: item.tipo in ("total", "qualificada") for codigo, item in sugestoes.items()}


_RE_LIBERDADE = re.compile(
    r"alvar[áa]\s+de\s+soltura|liberdade\s+provis[óo]ria|relaxamento\s+da\s+pris[ãa]o|relaxo\s+a\s+pris[ãa]o|"
    r"concedo\s+a\s+liberdade|revoga[çc][ãa]o\s+da\s+(?:pris[ãa]o\s+)?preventiva",
    re.IGNORECASE,
)


def localizar_documento_liberdade(pecas: List[PecaClassificada]) -> Optional[PecaClassificada]:
    """Peça que documenta a soltura/liberdade do réu (alvará, liberdade provisória, relaxamento)."""
    for p in pecas or []:
        if _RE_LIBERDADE.search(p.titulo or ""):
            return p
    for p in pecas or []:
        if p.categoria in ("DECISAO_MPU", "TERMO_AUDIENCIA", "OUTROS") or "cust" in (p.titulo or "").lower():
            if _RE_LIBERDADE.search(p.conteudo or ""):
                return p
    return None


def inferir_detracao_cautelar(pecas: List[PecaClassificada], data_fato_iso: str | None = None) -> tuple[int, str]:
    """Adaptador legado. A data do fato não integra o período de custódia."""
    intervalos, _ = extrair_intervalos_custodia(pecas)
    resultado = calcular_detracao(intervalos)
    return resultado.dias, resultado.descricao_periodos


def _resolver_detracao(
    meta: MetadadosProcesso, pecas: List[PecaClassificada], opcoes: Dict[str, Any],
    confirmacao: Any, data_sentenca: datetime.date | None, is_demo: bool,
) -> ResultadoDetracao:
    quantidade = opcoes.get("dias_prisao_cautelar")
    raw = opcoes.get("intervalos_prisao")
    fonte = "intervalos_prisao"
    if raw is not None and quantidade is not None:
        raise DadosInsuficientesError(
            [fonte, "dias_prisao_cautelar"], "Escolha intervalos documentados ou quantidade informada pelo juízo.",
        )
    if quantidade is not None and quantidade != "":
        return detracao_por_quantidade(quantidade, data_referencia=data_sentenca)
    if raw is None and isinstance(confirmacao, dict):
        if type(confirmacao.get("dias")) is bool:
            raise DadosInsuficientesError(
                ["pontos_decisivos_confirmados.prisao_provisoria.dias"],
                "Informe dias de prisão como quantidade inteira, não como confirmação.",
            )
        fonte = "pontos_decisivos_confirmados.prisao_provisoria"
        raw = confirmacao.get("intervalos")
        if raw is None and (confirmacao.get("data_prisao") or confirmacao.get("data_soltura")):
            inicio = parse_qualquer_data(confirmacao.get("data_prisao") or "")
            fim = parse_qualquer_data(confirmacao.get("data_soltura") or "")
            correspondentes = [
                intervalo for intervalo in meta.intervalos_custodia
                if intervalo.inicio == inicio and intervalo.fim == fim
            ]
            if len(correspondentes) != 1:
                raise DadosInsuficientesError(
                    [fonte], "Informe na estação de Dosimetria as fontes das datas corrigidas de custódia.",
                )
            raw = correspondentes
        if raw is None and confirmacao.get("dias") is not None:
            return detracao_por_quantidade(confirmacao["dias"], fonte + ".dias", data_referencia=data_sentenca)
    if raw is None and type(confirmacao) is int:
        return detracao_por_quantidade(
            confirmacao, "pontos_decisivos_confirmados.prisao_provisoria", data_referencia=data_sentenca,
        )
    if raw is None:
        if is_demo:
            return ResultadoDetracao()
        resultado = calcular_detracao(meta.intervalos_custodia, data_referencia=data_sentenca)
        resultado.pendencias.extend(meta.alertas_custodia)
        return resultado
    if not isinstance(raw, list):
        raise DadosInsuficientesError([fonte], "Informe uma lista de intervalos de custódia.")
    intervalos = []
    for posicao, dado in enumerate(raw):
        try:
            intervalos.append(IntervaloCustodia.model_validate(dado))
        except ValidationError as exc:
            caminhos = [".".join((fonte, str(posicao), *map(str, erro["loc"]))) for erro in exc.errors()]
            raise DadosInsuficientesError(caminhos, "Confira as datas e as fontes da custódia.") from exc
    return calcular_detracao(
        intervalos, data_referencia=data_sentenca, origem="juizo",
        fontes_validas={peca.doc_id for peca in pecas}, caminho=fonte,
    )


# ==============================================================================
# Entradas da dosimetria
# ==============================================================================

_ROTULO_VETOR_MARCADOR = {
    "culpabilidade": "culpabilidade",
    "antecedentes": "antecedentes",
    "conduta_social": "conduta social",
    "personalidade": "personalidade",
    "motivos": "motivos do crime",
    "circunstancias": "circunstâncias do crime",
    "consequencias": "consequências do crime",
}


def _fundamentacao_vetor(vetor: str, desfavoravel: bool, texto: str = "") -> str:
    """Valoração negativa sem fundamento concreto informado => marcador para o juízo."""
    texto = (texto or "").strip()
    if desfavoravel and not texto:
        return marcador(
            f"elementos concretos dos autos que justificam a valoração negativa de {_ROTULO_VETOR_MARCADOR.get(vetor, vetor)}"
        )
    return texto


def _referencia_antecedente(registro: RegistroAntecedente) -> str:
    fontes = ", ".join(dict.fromkeys(
        f"{fonte.fonte_id} ({fonte.folha})" if fonte.folha else fonte.fonte_id for fonte in registro.fontes
    ))
    transito = formatar_data_br(registro.data_transito.isoformat()) if registro.data_transito else ""
    return f"{fontes}, trânsito em julgado em {transito}" if fontes and transito else fontes


def _antecedentes_na_imputacao(
    item: Dict[str, Any], meta: MetadadosProcesso, data_fato: datetime.date, data_sentenca: datetime.date,
    circs: Dict[str, CircunstanciaArt59], reincidencia_geral: bool, prefixo: str, is_demo: bool,
) -> Dict[str, Any]:
    registros = meta.registros_antecedentes
    escolhas = item.get("usos_antecedentes")
    vetor = circs.get("antecedentes")
    reincidente = _bool_ov(item, "reincidente", reincidencia_geral, prefixo)
    if escolhas is None and (is_demo or not registros):
        if not is_demo and reincidente and vetor and vetor.desfavoravel and vetor.possui_transito_em_julgado_anterior:
            raise DadosInsuficientesError(
                [prefixo + "usos_antecedentes"],
                "Individualize as condenações usadas nas duas fases, conforme a Súmula 241/STJ.",
            )
        return {"reincidente": reincidente}

    caminho = prefixo + "usos_antecedentes"
    por_fase = validar_usos_antecedentes(
        [] if escolhas is None else escolhas, registros, data_fato, data_sentenca, caminho,
    )
    antecedentes = por_fase.get("antecedentes", [])
    reincidencias = por_fase.get("reincidencia", [])
    if escolhas is None and (reincidente or (vetor and vetor.desfavoravel)):
        raise DadosInsuficientesError([caminho], "Selecione os registros das certidões e a fase em que serão valorados.")
    if item.get("reincidente") is not None and reincidente != bool(reincidencias):
        raise DadosInsuficientesError([prefixo + "reincidente"], "A decisão sobre reincidência diverge dos registros selecionados.")
    if antecedentes:
        fontes = "; ".join(_referencia_antecedente(r) for r in antecedentes)
        fundamento = f"Valoro os antecedentes pelas condenações definitivas registradas em {fontes}."
        if vetor and vetor.fundamentacao and not vetor.fundamentacao.startswith("[COMPLETAR PELO JUÍZO"):
            fundamento += f" {vetor.fundamentacao}"
        circs["antecedentes"] = CircunstanciaArt59(desfavoravel=True, fundamentacao=fundamento)
    else:
        circs.pop("antecedentes", None)
    ids_decididos = {r.id_registro for lista in por_fase.values() for r in lista}
    pendencia = any(
        r.id_registro not in ids_decididos and (r.conflito or r.situacao in ("definitiva", "indeterminada"))
        for r in registros
    )
    return {
        "reincidente": bool(reincidencias),
        "antecedentes_pendentes": pendencia,
        "fontes_reincidencia": [_referencia_antecedente(r) for r in reincidencias],
        "registros_valorados": {
            fase: [r.id_registro for r in lista] for fase, lista in por_fase.items() if fase != "desconsiderar"
        },
    }


def build_crimes_inputs_for_case(
    crimes_codes: List[str],
    dt_fato: datetime.date,
    culpabilidade_lesao: bool,
    motivos_lesao: bool,
    circunstancias_lesao: bool,
    consequencias_lesao: bool,
    confissao_art24a: bool,
    reincidente: bool,
    agravante_art61f: bool,
    is_demo: bool = False,
    confissao_por_crime: Dict[str, bool] | None = None,
    menoridade_relativa_ou_senilidade: bool = False,
) -> List[CrimeDosimetriaInput]:
    """Entradas da dosimetria a partir dos parâmetros "planos" (simulação/legado).

    * Caso demonstrativo (``is_demo=True`` e os três crimes do enredo): parâmetros de simulação de
      ``app.demo.demo_sentence``.
    * Caso real: NENHUM vetor é valorado por padrão (antecedentes inclusive); os parâmetros
      ``*_lesao`` incidem sobre o crime de lesão corporal (ou o primeiro imputado) e, quando
      desfavoráveis, exigem fundamentação concreta (marcador ``[COMPLETAR PELO JUÍZO: ...]``).
    * Sem crimes imputados => ``DadosInsuficientesError`` (nunca há crime padrão).
    """
    codes = [str(c).strip() for c in (crimes_codes or []) if str(c).strip()]
    if not codes:
        raise DadosInsuficientesError(
            ["crimes_imputados"],
            "Nenhum crime imputado foi extraído da denúncia. Informe os crimes imputados (metadados) antes de calcular a dosimetria.",
        )
    if is_demo and codes == list(DEMO_CRIMES):
        return construir_inputs_dosimetria_demo(
            dt_fato,
            culpabilidade_lesao=culpabilidade_lesao,
            motivos_lesao=motivos_lesao,
            circunstancias_lesao=circunstancias_lesao,
            consequencias_lesao=consequencias_lesao,
            confissao_art24a=confissao_art24a,
            reincidente=reincidente,
            agravante_art61f=agravante_art61f,
        )

    alvo_vetores = next((c for c in codes if c.startswith("art_129")), codes[0])
    alvo_confissao = next((c for c in codes if c.startswith("art_24a")), codes[0])
    flags = {
        "culpabilidade": culpabilidade_lesao,
        "motivos": motivos_lesao,
        "circunstancias": circunstancias_lesao,
        "consequencias": consequencias_lesao,
    }
    out: List[CrimeDosimetriaInput] = []
    for code in codes:
        circs: Dict[str, CircunstanciaArt59] = {}
        if code == alvo_vetores:
            for vetor, desfav in flags.items():
                if desfav:
                    circs[vetor] = CircunstanciaArt59(
                        desfavoravel=True, fundamentacao=_fundamentacao_vetor(vetor, True)
                    )
        if confissao_por_crime is not None:
            conf = bool(confissao_por_crime.get(code, False))
        else:
            conf = bool(confissao_art24a) if code == alvo_confissao else False
        out.append(
            CrimeDosimetriaInput(
                crime_code=code,
                data_fato=dt_fato,
                circunstancias_art59=circs,
                agravantes_art61_f_violencia_domestica=agravante_art61f,
                confissao_espontanea=conf,
                reincidente=reincidente,
                menoridade_relativa_ou_senilidade=menoridade_relativa_ou_senilidade,
            )
        )
    return out


def parse_fracao_str(frac_str: str | None) -> tuple[int, int] | None:
    if not frac_str:
        return None
    if isinstance(frac_str, (list, tuple)) and len(frac_str) == 2:
        try:
            num, den = int(frac_str[0]), int(frac_str[1])
        except (TypeError, ValueError):
            return None
        return (num, den) if 0 < num < den else None
    s = str(frac_str).strip()
    m = re.match(r"^(\d+)\s*/\s*(\d+)$", s)
    if not m:
        return None
    num, den = int(m.group(1)), int(m.group(2))
    if not 0 < num < den:
        return None
    return (num, den)


_INCISOS_ART386 = {
    "I": "estar provada a inexistência do fato",
    "II": "não haver prova da existência do fato",
    "III": "não constituir o fato infração penal",
    "IV": "estar provado que o réu não concorreu para a infração penal",
    "V": "não existir prova de ter o réu concorrido para a infração penal",
    "VI": "existirem circunstâncias que excluam o crime ou isentem o réu de pena, ou se houver fundada dúvida sobre sua existência",
    "VII": "não existir prova suficiente para a condenação",
}

_INCISOS_ART107 = {
    "I": "pela morte do agente",
    "IV": "pela prescrição, decadência ou perempção",
    "V": "pela renúncia do direito de queixa ou pelo perdão aceito",
    "VI": "pela retratação do agente, nos casos em que a lei a admite",
}


def _como_dict(item: Any) -> Dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return dict(getattr(item, "__dict__", {}) or {})


def _bool_ov(ov: Dict[str, Any], chave: str, padrao: bool, prefixo: str = "") -> bool:
    valor = ov.get(chave)
    if valor is None:
        return padrao
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, str) and valor.strip().lower() in ("true", "false"):
        return valor.strip().lower() == "true"
    raise DadosInsuficientesError([prefixo + chave], "Informe um valor booleano (true ou false).")


def _fracao_override(ov: Dict[str, Any], chave: str, prefixo: str = "") -> tuple[int, int] | None:
    valor = ov.get(chave)
    if valor is None:
        return None
    fracao = parse_fracao_str(valor)
    if fracao is None:
        raise DadosInsuficientesError([prefixo + chave], "Informe uma fração positiva menor que 1.")
    return fracao


def _majorante_art24a_p4(
    item: Dict[str, Any], codigo: str, pecas: List[PecaClassificada]
) -> MajoranteArt24aP4 | None:
    dados = item.get("majorante_art24a_p4")
    if dados is None:
        return None
    campo = f"crimes_config.{codigo}.majorante_art24a_p4"
    if not isinstance(dados, dict):
        raise DadosInsuficientesError([campo], "Informe os dados da majorante do art. 24-A, § 4º.")
    fonte_id = str(dados.get("fonte_id") or "").strip()
    trecho = str(dados.get("trecho") or "").strip()
    fracao = parse_fracao_str(dados.get("fracao"))
    hipotese = dados.get("hipotese")
    peca = next((p for p in pecas if p.doc_id == fonte_id), None)
    if (hipotese not in ("area_exclusao", "dispositivo_monitoracao") or fracao is None
            or not peca or not trecho or " ".join(trecho.split()) not in " ".join(peca.conteudo.split())):
        raise DadosInsuficientesError(
            [campo], "A majorante do art. 24-A, § 4º, exige hipótese, fração e trecho da peça indicada."
        )
    return MajoranteArt24aP4(hipotese=hipotese, fonte_id=fonte_id, trecho=trecho, fracao=fracao)


def _quantidade_multa(item: Dict[str, Any], prefixo: str) -> int | None:
    dias = item.get("dias_multa")
    if dias is not None and (type(dias) is not int or not (dias == 0 or 10 <= dias <= 360)):
        raise DadosInsuficientesError([prefixo + "dias_multa"], "Informe de 10 a 360 dias-multa (art. 49 do CP).")
    return dias


def _linha_tempo_prescricao(item: dict, prefixo: str, fontes: set[str]) -> LinhaTempoPrescricao:
    bruto = item.get("linha_tempo_prescricao")
    try:
        linha = LinhaTempoPrescricao() if bruto is None else LinhaTempoPrescricao.model_validate(bruto)
    except ValidationError as exc:
        base = prefixo + "linha_tempo_prescricao"
        campos = [".".join((base, *map(str, erro["loc"]))) for erro in exc.errors()]
        raise DadosInsuficientesError(campos, "Corrija os marcos prescricionais da imputação.") from exc
    marcos = {
        "termo_inicial": linha.termo_inicial,
        "pena_documentada": linha.pena_documentada,
        "estabilidade_acusacao": linha.estabilidade_acusacao,
        "reconhecimento": linha.reconhecimento,
        **{f"interrupcoes.{i}": m for i, m in enumerate(linha.interrupcoes)},
        **{f"suspensoes.{i}": m for i, m in enumerate(linha.suspensoes)},
    }
    sem_fonte = [
        f"{prefixo}linha_tempo_prescricao.{campo}.fonte_id"
        for campo, marco in marcos.items()
        if marco is not None and marco.fonte_id not in fontes
    ]
    if sem_fonte:
        raise DadosInsuficientesError(sem_fonte, "Escolha uma peça dos autos ativos para cada marco prescricional.")
    return linha


def _beneficios_registrados(dados: Any) -> tuple[str | None, bool]:
    """Hipótese do art. 77, § 2º, e exceção do art. 44, § 3º, registradas nas decisões acessórias (TASK-088).

    A validação completa do registro fica em ``resolver_acessorias``; aqui só se leem os dados que mudam o cálculo.
    """
    if not isinstance(dados, dict):
        return None, False
    sursis = dados.get("sursis") if isinstance(dados.get("sursis"), dict) else {}
    hipotese = sursis.get("hipotese_art77_p2")
    substituicao = dados.get("substituicao") if isinstance(dados.get("substituicao"), dict) else {}
    excecao = substituicao.get("excecao_art44_p3")
    reconhecida = (
        isinstance(excecao, dict)
        and excecao.get("socialmente_recomendavel") is True
        and excecao.get("reincidencia_nao_especifica") is True
    )
    return (hipotese if hipotese in ("maior_70", "saude") else None), reconhecida


_ROTULOS_CONFISSAO = {
    "total": "confissão integral", "parcial": "confissão parcial", "qualificada": "confissão qualificada",
    "negativa": "Negativa do fato", "ausente": "sem confissão reconhecida",
}
# Tema 1194/STJ, acórdão publicado em 16/09/2025: a proporção menor da atenuante só alcança fatos posteriores.
_MARCO_TEMA_1194 = datetime.date(2025, 9, 16)


def _decisao_unica_confissao(
    confissao: bool, item: Dict[str, Any], registrada: Any, tipo_legado: str | None,
    data_fato: datetime.date, prefixo: str, is_demo: bool,
) -> str | None:
    """Uma decisão de confissão por imputação (TASK-086).

    O registro da Análise e a escolha da Dosimetria precisam coincidir; o conflito bloqueia o cálculo
    em vez de reconhecer (ou afastar) a atenuante em silêncio. Devolve o tipo que vai ao cálculo.
    """
    tipo_informado = item.get("tipo_confissao")
    if tipo_informado is not None and tipo_informado not in TIPOS_AFIRMATIVOS:
        raise DadosInsuficientesError(
            [prefixo + "tipo_confissao"], "Informe o tipo da confissão: total, parcial ou qualificada.",
        )
    if tipo_informado and not confissao:
        raise DadosInsuficientesError(
            [prefixo + "tipo_confissao"], "O tipo da confissão só se informa quando a atenuante é reconhecida.",
        )
    if registrada is not None:
        afirmativa = registrada.tipo in TIPOS_AFIRMATIVOS
        rotulo = _ROTULOS_CONFISSAO.get(registrada.tipo, registrada.tipo)
        if item.get("confissao") is not None and confissao != afirmativa:
            escolha = "reconhece" if confissao else "afasta"
            raise DadosInsuficientesError(
                [prefixo + "confissao"],
                f"Confissão em conflito: a Análise registrou \"{rotulo}\", e a Dosimetria {escolha} a atenuante. "
                "Escolha \"Conforme decisão na Análise\" ou corrija o registro na Análise.",
            )
        if tipo_informado and afirmativa and tipo_informado != registrada.tipo:
            raise DadosInsuficientesError(
                [prefixo + "tipo_confissao"],
                f"Confissão em conflito: a Análise registrou \"{rotulo}\", e a Dosimetria informa "
                f"\"{_ROTULOS_CONFISSAO[tipo_informado]}\". Mantenha uma única decisão.",
            )
        return registrada.tipo if confissao and afirmativa else None
    if not confissao:
        return None
    tipo = tipo_informado or (tipo_legado if tipo_legado in TIPOS_AFIRMATIVOS else None)
    if tipo is None and not is_demo and data_fato > _MARCO_TEMA_1194:
        raise DadosInsuficientesError(
            [prefixo + "tipo_confissao"],
            "Informe o tipo da confissão (total, parcial ou qualificada): em fato posterior a 16/09/2025 ele "
            "define a proporção da atenuante (Tema 1194/STJ).",
        )
    return tipo


def resolver_dosimetria_e_decisoes(
    pecas: List[PecaClassificada],
    meta: MetadadosProcesso,
    transcriptions: List[Dict[str, Any]],
    is_demo: bool,
    dt_fato: datetime.date,
    dt_rec: datetime.date | None,
    custom_dosimetria_override: Dict[str, Any] | None = None,
    exigir_veredicto: bool = False,
    data_sentenca: datetime.date | None = None,
) -> tuple[DosimetriaGlobalOutput, PrescricaoGlobalOutput, List[Dict[str, Any]], str, str]:
    """Resolve as decisões por crime e calcula dosimetria e prescrição (por crime).

    ``exigir_veredicto=True`` (geração de sentença de caso real): todo crime imputado precisa de
    veredicto explícito em ``crimes_config`` — senão ``DecisaoJudicialPendenteError``.
    ``exigir_veredicto=False`` (simulação): crimes sem veredicto têm a condenação PRESUMIDA apenas
    para cálculo; cada decisão presumida traz ``simulacao_presume_condenacao: True`` e
    ``dosimetria.alertas_globais`` recebe o aviso.

    Retorna ``(dosimetria_global, prescricao_por_crime, decisoes_crimes, criterio_1fase, detracao_periodo)``.
    """
    settings = load_settings()
    ov = dict(custom_dosimetria_override or {})
    criterio_1fase = str(ov.get("criterio_primeira_fase") or settings.criterio_primeira_fase)
    criterio_1fase = "um_sexto_minima" if criterio_1fase == "um_sexto_minima" else "um_oitavo_intervalo"

    crimes_imputados = [str(c).strip() for c in (meta.crimes_imputados or []) if str(c).strip()]
    config_raw = [_como_dict(c) for c in (ov.get("crimes_config") or [])]
    alvos, configs, tipos_repetidos = resolver_configs_imputacao(meta, config_raw)

    # --- Veredicto obrigatório (geração de caso real) ---------------------------------------
    if exigir_veredicto:
        if not crimes_imputados and not configs:
            raise DadosInsuficientesError(
                ["crimes_imputados"],
                "Nenhum crime imputado foi extraído da denúncia. Informe os crimes imputados e o veredicto de cada um.",
            )
        pendentes = [
            chave if alvo.codigo in tipos_repetidos else alvo.codigo
            for chave, alvo in alvos.items()
            if str(configs.get(chave, {}).get("veredicto") or "").strip().lower() not in VEREDICTOS_VALIDOS
        ]
        if pendentes:
            raise DecisaoJudicialPendenteError(pendentes)
        sem_destino = [
            chave if cfg.get("id_imputacao") or alvos[chave].codigo in tipos_repetidos else alvos[chave].codigo
            for chave, cfg in configs.items()
            if str(cfg.get("veredicto") or "").strip().lower() == "desclassificar"
            and not str(cfg.get("crime_desclassificado") or "").strip()
        ]
        if sem_destino:
            raise DadosInsuficientesError(
                [f"crimes_config.{c}.crime_desclassificado" for c in sem_destino],
                "Informe o tipo penal de destino da desclassificação para: " + ", ".join(sem_destino) + ".",
            )

    default_culp = True if is_demo else False
    culpabilidade_lesao = _bool_ov(ov, "culpabilidade_lesao", default_culp)
    motivos_lesao = _bool_ov(ov, "motivos_lesao", False)
    circunstancias_lesao = _bool_ov(ov, "circunstancias_lesao", False)
    consequencias_lesao = _bool_ov(ov, "consequencias_lesao", False)

    confirmados = getattr(meta, "pontos_decisivos_confirmados", None) or {}
    confissao_art24a = _bool_ov(ov, "confissao_art24a", is_demo)
    legado = confirmados.get("confissao") if len(alvos) == 1 else None
    tipo_legado = legado.get("tipo") if isinstance(legado, dict) else None
    confissao_legada = legado is True or (isinstance(tipo_legado, str) and tipo_legado in TIPOS_AFIRMATIVOS)

    detracao = _resolver_detracao(
        meta, pecas, ov, confirmados.get("prisao_provisoria"), data_sentenca or datetime.date.today(), is_demo,
    )
    # Atenuante do art. 65, I, do CP (menor de 21 na data do fato / maior de 70 na sentença) e art. 115:
    # as flags são calculadas ANTES das entradas para que a atenuante obrigatória entre na 2ª fase.
    menor_21, maior_70 = flags_idade_art115(
        getattr(meta, "data_nascimento_reu", ""),
        dt_fato,
        data_sentenca or datetime.date.today(),
        ov.get("reu_menor_21_na_data_fato"),
        ov.get("reu_maior_70_na_sentenca"),
    )
    reincidente_global = _bool_ov(ov, "reincidente", False)
    agravante_art61f_global = _bool_ov(ov, "agravante_art61f", True if is_demo else False)
    dano_pedido = (
        float(getattr(meta, "valor_dano_moral_pedido_mp", 0.0) or 0.0) or None
        if meta.pedido_dano_moral_expresso_mp else None
    )

    decisoes_do_enredo_demo = is_demo and not config_raw and crimes_imputados == list(DEMO_CRIMES)
    valor_dano_decidido = ov.get("valor_dano_moral", REPARACAO_ARBITRADA_DEMO if decisoes_do_enredo_demo else None)

    decisoes_crimes: List[Dict[str, Any]] = []
    crimes_condenados_inputs: List[CrimeDosimetriaInput] = []
    crimes_nao_condenados: List[Dict[str, str]] = []
    alvos_sem_pena: List[AlvoPrescricaoAbstrata] = []
    fontes_dos_autos = {peca.doc_id for peca in pecas}
    alertas_resolucao: List[str] = []
    condicoes_por_imputacao: dict[str, tuple[CondicoesAcaoPenal, datetime.date | None]] = {}
    data_analise = data_sentenca or datetime.date.today()

    tipos_presumidos = list(dict.fromkeys(
        alvo.codigo for chave, alvo in alvos.items()
        if str(configs.get(chave, {}).get("veredicto") or "").strip().lower() not in VEREDICTOS_VALIDOS
    ))
    modelos_presumidos: Dict[str, CrimeDosimetriaInput] = {}
    if tipos_presumidos:
        modelos_presumidos = {
            modelo.crime_code: modelo
            for modelo in build_crimes_inputs_for_case(
                crimes_codes=tipos_presumidos,
                dt_fato=dt_fato,
                culpabilidade_lesao=culpabilidade_lesao,
                motivos_lesao=motivos_lesao,
                circunstancias_lesao=circunstancias_lesao,
                consequencias_lesao=consequencias_lesao,
                confissao_art24a=confissao_art24a,
                reincidente=reincidente_global,
                agravante_art61f=agravante_art61f_global,
                is_demo=is_demo,
                menoridade_relativa_ou_senilidade=bool(menor_21 or maior_70),
            )
        }

    datas_expressas = {a.data_fato for a in alvos.values() if a.data_fato}
    nomes_presumidos: List[str] = []
    for chave, alvo in alvos.items():
        orig_code = alvo.codigo
        repetido = orig_code in tipos_repetidos
        item = configs.get(chave, {})
        veredicto = str(item.get("veredicto") or "").strip().lower()
        campo = chave if repetido or item.get("id_imputacao") else orig_code
        prefixo = f"crimes_config.{campo}."
        linha_tempo = _linha_tempo_prescricao(item, prefixo, fontes_dos_autos)
        if exigir_veredicto and linha_tempo.reconhecimento and veredicto != "extinguir":
            raise DadosInsuficientesError(
                [prefixo + "veredicto"],
                "O reconhecimento judicial da prescrição exige o veredicto de extinção da punibilidade nesta imputação.",
            )
        data_informada = item.get("data_fato")
        data_item = _resolver_data(data_informada) if data_informada else _resolver_data(
            alvo.data_fato if repetido or len(datas_expressas) > 1 else None
        )
        if data_item is None and (repetido or data_informada):
            raise DadosInsuficientesError(
                [f"crimes_config.{campo}.data_fato"],
                "Informe a data do fato para esta imputação antes de aplicar o catálogo penal.",
            )
        data_item = data_item or dt_fato
        menor_item, maior_item = flags_idade_art115(
            getattr(meta, "data_nascimento_reu", ""), data_item, data_sentenca or datetime.date.today(),
            ov.get("reu_menor_21_na_data_fato"), ov.get("reu_maior_70_na_sentenca"),
        )
        atenuante_item = _bool_ov(item, "menoridade_relativa_ou_senilidade", menor_item or maior_item)
        identidade = {"id_imputacao": alvo.id_imputacao, "referencia_imputacao": alvo.referencia}
        violencia_sexual = item.get("violencia_sexual_contra_mulher")
        if violencia_sexual is not None and type(violencia_sexual) is not bool:
            raise DadosInsuficientesError(
                [f"crimes_config.{campo}.violencia_sexual_contra_mulher"],
                "Confirme a hipótese da Lei 15.160/2025 com true ou false.",
            )
        condicoes_raw = item.get("condicoes_acao_penal")
        try:
            condicoes = CondicoesAcaoPenal() if condicoes_raw is None else CondicoesAcaoPenal.model_validate(condicoes_raw)
        except ValidationError as exc:
            base = f"crimes_config.{campo}.condicoes_acao_penal"
            caminhos = [".".join((base, *map(str, erro["loc"]))) for erro in exc.errors()]
            raise DadosInsuficientesError(caminhos, "Corrija os dados de procedibilidade da imputação.") from exc
        recebimento_individual = _resolver_data(alvo.data_recebimento)
        recebimento_acao = recebimento_individual or (dt_rec if len(alvos) == 1 and not alvo.recebimento_individual else None)
        condicoes_por_imputacao[chave] = (condicoes, recebimento_acao)
        dados_calculo = {
            **identidade,
            "data_recebimento_individual": recebimento_individual,
            "recebimento_por_imputacao": alvo.recebimento_individual,
            "reduzir_prescricao_art115": atenuante_item,
            "violencia_sexual_contra_mulher": violencia_sexual,
            "linha_tempo_prescricao": linha_tempo if not is_demo or item.get("linha_tempo_prescricao") else None,
        }
        confissao_registrada = meta.confissoes_confirmadas.get(chave)
        if confissao_registrada is not None:
            confissao_padrao = confissao_registrada.tipo in TIPOS_AFIRMATIVOS
        elif is_demo:
            confissao_padrao = confissao_art24a and orig_code.startswith("art_24a")
        elif ov.get("confissao_art24a") is not None and orig_code.startswith("art_24a") and not repetido:
            confissao_padrao = confissao_art24a
        else:
            confissao_padrao = confissao_legada
        confissao = _bool_ov(item, "confissao", confissao_padrao, prefixo)
        tipo_confissao = _decisao_unica_confissao(
            confissao, item, confissao_registrada, tipo_legado if confissao_legada else None,
            data_item, prefixo, is_demo,
        )
        spec_orig = get_penal_type_for_date(orig_code, data_item)

        if veredicto in ("absolver", "extinguir") and not is_demo:
            recebimento = _resolver_data(alvo.data_recebimento) if alvo.recebimento_individual else dt_rec
            alvos_sem_pena.append(AlvoPrescricaoAbstrata(
                **identidade, crime_spec=spec_orig, data_fato=data_item, data_recebimento=recebimento,
                reduzir_metade_art115=atenuante_item, violencia_sexual_contra_mulher=violencia_sexual,
                linha_tempo_prescricao=linha_tempo,
            ))

        if veredicto not in VEREDICTOS_VALIDOS:
            modelo = modelos_presumidos[orig_code]
            circs_simulacao = dict(modelo.circunstancias_art59)
            antecedentes = _antecedentes_na_imputacao(
                item, meta, data_item, data_analise, circs_simulacao, reincidente_global, prefixo, is_demo,
            )
            ci = modelo.model_copy(update={
                **dados_calculo,
                **antecedentes,
                "circunstancias_art59": circs_simulacao,
                "data_fato": data_item,
                "menoridade_relativa_ou_senilidade": atenuante_item,
                "confissao_espontanea": modelo.confissao_espontanea if is_demo and not config_raw else confissao,
                "tipo_confissao": tipo_confissao,
                "dias_multa": modelo.dias_multa if decisoes_do_enredo_demo else _quantidade_multa(item, prefixo),
                "valor_unitario_multa": modelo.valor_unitario_multa if decisoes_do_enredo_demo else item.get("valor_unitario_multa"),
                "aplicar_aumento_art147a_p1_ii": _bool_ov(item, "aplicar_aumento_art147a_p1_ii", False, prefixo),
            })
            if not is_demo and orig_code == "art_147_cp" and data_item >= DATA_VIGENCIA_LEI_14994_2024:
                alternativa = item.get("enquadramento_ameaca")
                if alternativa == "paragrafo_1":
                    ci.ameaca_qualificada = True
                elif alternativa != "caput":
                    alertas_resolucao.append("Ameaça: caput usado apenas na simulação. Confirme o enquadramento do art. 147.")
            spec_orig = get_penal_type_for_date(orig_code, data_item, ameaca_qualificada=ci.ameaca_qualificada)
            nomes_presumidos.append(spec_orig.artigo_formatado)
            crimes_condenados_inputs.append(ci)
            decisoes_crimes.append(
                {
                    **identidade,
                    "data_fato": data_item,
                    "crime_code": orig_code,
                    "crime_efetivo": orig_code,
                    "veredicto": "condenar",
                    "fundamento_legal": spec_orig.artigo_formatado,
                    "spec_original": spec_orig,
                    "spec_efetivo": spec_orig,
                    "desclassificado_de": "",
                    "presumido": True,
                    "simulacao_presume_condenacao": True,
                }
            )
            continue

        if veredicto == "absolver":
            raw_inc = str(item.get("fundamento_absolvicao_art386") or "").strip()
            m_inc = re.search(r"\b(VII|VI|IV|V|III|II|I)\b", raw_inc.upper())
            if m_inc:
                inciso = m_inc.group(1)
            elif exigir_veredicto:
                # O inciso é decisão do juízo: na sentença de caso real o motor não o escolhe.
                inciso = ""
            else:
                inciso = "VII"
            if inciso:
                desc_inc = _INCISOS_ART386.get(inciso, "insuficiência probatória")
                fund_legal = f"art. 386, inciso {inciso}, do Código de Processo Penal ({desc_inc})"
            else:
                fund_legal = f"art. 386, inciso {marcador('inciso do art. 386 do CPP aplicável')}, do Código de Processo Penal"
            justificativa = str(item.get("fundamentacao_absolvicao") or item.get("fundamentacao") or "").strip()
            if not justificativa and len(raw_inc) > 10:
                justificativa = raw_inc
            decisoes_crimes.append(
                {
                    **identidade,
                    "data_fato": data_item,
                    "crime_code": orig_code,
                    "crime_efetivo": orig_code,
                    "veredicto": "absolver",
                    "fundamento_legal": fund_legal,
                    "inciso": inciso,
                    "justificativa_juizo": justificativa,
                    "spec_original": spec_orig,
                    "spec_efetivo": spec_orig,
                    "presumido": False,
                }
            )
            crimes_nao_condenados.append(
                {
                    **identidade,
                    "crime_code": orig_code,
                    "crime_nome": spec_orig.nome,
                    "artigo_formatado": spec_orig.artigo_formatado,
                    "veredicto": "absolver",
                    "fundamento_legal": fund_legal,
                }
            )
            continue

        if veredicto == "extinguir":
            raw_ext = str(item.get("fundamento_extincao_art107") or "").strip().upper()
            m_ext = re.search(r"\b(VI|IV|V|I)\b", raw_ext)
            if m_ext:
                inciso_ext = m_ext.group(1)
            elif exigir_veredicto:
                inciso_ext = ""
            else:
                inciso_ext = "IV"
            if inciso_ext:
                desc_ext = _INCISOS_ART107.get(inciso_ext, "causa extintiva da punibilidade")
                fund_legal = f"art. 107, inciso {inciso_ext}, do Código Penal ({desc_ext})"
            else:
                fund_legal = f"art. 107, inciso {marcador('causa extintiva do art. 107 do CP aplicável')}, do Código Penal"
            decisoes_crimes.append(
                {
                    **identidade,
                    "data_fato": data_item,
                    "crime_code": orig_code,
                    "crime_efetivo": orig_code,
                    "veredicto": "extinguir",
                    "fundamento_legal": fund_legal,
                    "inciso": inciso_ext,
                    "justificativa_juizo": str(item.get("fundamentacao_extincao") or item.get("fundamentacao") or "").strip(),
                    "spec_original": spec_orig,
                    "spec_efetivo": spec_orig,
                    "presumido": False,
                }
            )
            crimes_nao_condenados.append(
                {
                    **identidade,
                    "crime_code": orig_code,
                    "crime_nome": spec_orig.nome,
                    "artigo_formatado": spec_orig.artigo_formatado,
                    "veredicto": "extinguir",
                    "fundamento_legal": fund_legal,
                }
            )
            continue

        # condenar | desclassificar
        effective_code = orig_code
        desclassificado_de = ""
        if veredicto == "desclassificar":
            alvo = str(item.get("crime_desclassificado") or "").strip()
            if not alvo or alvo == orig_code:
                raise DadosInsuficientesError(
                    [prefixo + "crime_desclassificado"],
                    "Informe um tipo penal de destino diferente do tipo da imputação.",
                )
            effective_code = alvo
            desclassificado_de = orig_code

        ameaca_qualificada = False
        if effective_code == "art_147_cp":
            enquadramento = item.get("enquadramento_ameaca")
            if enquadramento not in (None, "", "caput", "paragrafo_1"):
                raise DadosInsuficientesError(
                    [f"crimes_config.{campo}.enquadramento_ameaca"],
                    "Selecione caput ou § 1º do art. 147.",
                )
            if data_item >= DATA_VIGENCIA_LEI_14994_2024 and enquadramento in (None, ""):
                if exigir_veredicto and not is_demo:
                    raise DadosInsuficientesError(
                        [f"crimes_config.{campo}.enquadramento_ameaca"],
                        "Confirme o enquadramento da ameaça: caput ou § 1º do art. 147.",
                    )
                if not is_demo:
                    alertas_resolucao.append("Ameaça: caput usado apenas na simulação. Confirme o enquadramento do art. 147.")
            ameaca_qualificada = enquadramento == "paragrafo_1" or (
                is_demo and enquadramento in (None, "") and data_item >= DATA_VIGENCIA_LEI_14994_2024
            )
        spec_efetivo = get_penal_type_for_date(effective_code, data_item, ameaca_qualificada=ameaca_qualificada)
        majorante_24a = _majorante_art24a_p4(item, campo, pecas)
        vetores_in = item.get("vetores_art59") or {}
        circs_map: Dict[str, CircunstanciaArt59] = {}
        for v_key in VETORES_ART_59:
            v_raw = vetores_in.get(v_key) if isinstance(vetores_in, dict) else None
            if isinstance(v_raw, dict):
                prefixo_vetor = f"{prefixo}vetores_art59.{v_key}."
                desfav = _bool_ov(v_raw, "desfavoravel", False, prefixo_vetor)
                circs_map[v_key] = CircunstanciaArt59(
                    desfavoravel=desfav,
                    possui_transito_em_julgado_anterior=_bool_ov(v_raw, "possui_transito_em_julgado_anterior", True, prefixo_vetor),
                    fundamentacao=_fundamentacao_vetor(v_key, desfav and v_key != "comportamento_vitima", str(v_raw.get("fundamentacao") or "")),
                )

        antecedentes = _antecedentes_na_imputacao(
            item, meta, data_item, data_analise, circs_map, reincidente_global, prefixo, is_demo,
        )
        tent_frac = _fracao_override(item, "tentativa_fracao", prefixo)
        aum_frac = _fracao_override(item, "fracao_aumento", prefixo)
        crimes_condenados_inputs.append(
            CrimeDosimetriaInput(
                **dados_calculo,
                crime_code=effective_code,
                data_fato=data_item,
                circunstancias_art59=circs_map,
                agravantes_art61_f_violencia_domestica=_bool_ov(item, "agravante_art61f", agravante_art61f_global, prefixo),
                **antecedentes,
                confissao_espontanea=confissao,
                tipo_confissao=tipo_confissao,
                fracao_aumento_terceira_fase=aum_frac,
                fracao_diminuicao_terceira_fase=tent_frac,
                dias_multa=_quantidade_multa(item, prefixo),
                valor_unitario_multa=item.get("valor_unitario_multa"),
                desclassificado_de=desclassificado_de,
                ameaca_qualificada=ameaca_qualificada,
                majorante_art24a_p4=majorante_24a,
                aplicar_aumento_art147a_p1_ii=_bool_ov(item, "aplicar_aumento_art147a_p1_ii", False, prefixo),
                aplicar_aumento_art141_p3=_bool_ov(item, "aplicar_aumento_art141_p3", False, prefixo),
                menoridade_relativa_ou_senilidade=atenuante_item,
                violencia_ou_grave_ameaca=(
                    None if item.get("violencia_ou_grave_ameaca") is None else _bool_ov(item, "violencia_ou_grave_ameaca", False, prefixo)
                ),
            )
        )
        decisoes_crimes.append(
            {
                **identidade,
                "data_fato": data_item,
                "crime_code": orig_code,
                "crime_efetivo": effective_code,
                "veredicto": "desclassificar" if desclassificado_de else "condenar",
                "fundamento_legal": (
                    f"art. 383 do CPP ({spec_orig.artigo_formatado} -> {spec_efetivo.artigo_formatado})"
                    if desclassificado_de
                    else spec_efetivo.artigo_formatado
                ),
                "spec_original": spec_orig,
                "spec_efetivo": spec_efetivo,
                "desclassificado_de": desclassificado_de,
                "presumido": False,
            }
        )

    for decisao in decisoes_crimes:
        chave = decisao["id_imputacao"] or decisao["crime_code"]
        condicoes, recebimento_acao = condicoes_por_imputacao[chave]
        enquadramento_pendente = (
            not is_demo and decisao["presumido"]
            and decisao["crime_code"] == "art_147_cp"
            and decisao["data_fato"] >= DATA_VIGENCIA_LEI_14994_2024
            and configs.get(chave, {}).get("enquadramento_ameaca") not in ("caput", "paragrafo_1")
        )
        analise = analisar_acao_penal(
            decisao["spec_efetivo"], decisao["data_fato"], data_analise, recebimento_acao, condicoes,
            enquadramento_pendente=enquadramento_pendente,
        )
        decisao["analise_acao_penal"] = analise.model_dump(mode="json")
        if condicoes.model_fields_set:
            referencia = decisao["referencia_imputacao"] or decisao["spec_efetivo"].artigo_formatado
            alertas_resolucao.extend(analise.resumir(referencia))

    if not decisoes_crimes:
        raise DadosInsuficientesError(
            ["crimes_imputados"],
            "Nenhum crime imputado foi extraído da denúncia. Informe os crimes imputados (metadados) antes de calcular a dosimetria.",
        )

    n_cond = len(crimes_condenados_inputs)
    tipo_concurso_raw = str(ov.get("tipo_concurso") or ("material" if n_cond > 1 else "nenhum")).strip().lower()
    if tipo_concurso_raw not in ("nenhum", "material", "formal", "continuado"):
        tipo_concurso_raw = "material" if n_cond > 1 else "nenhum"
    # Fração ausente => o motor sugere conforme o número de crimes (a do juízo prevalece).
    frac_conc = _fracao_override(ov, "concurso_fracao")

    hipotese_p2, excecao_p3 = _beneficios_registrados(ov.get("decisoes_acessorias"))
    cfg_dos = DosimetriaConfig(
        criterio_primeira_fase=criterio_1fase,
        aplicar_sumula_231_stj=settings.aplicar_sumula_231_stj,
        aplicar_sumula_444_stj=settings.aplicar_sumula_444_stj,
        hipotese_sursis_art77_p2=hipotese_p2,
        excecao_art44_p3=excecao_p3,
    )
    dosimetria_global = calcular_dosimetria_global(
        crimes=crimes_condenados_inputs,
        config=cfg_dos,
        concurso=ConcursoCrimesConfig(
            tipo_concurso=tipo_concurso_raw if n_cond > 1 else "nenhum",
            fracao_exasperacao=frac_conc,
        ),
        pedido_expresso_dano_moral_mp=bool(meta.pedido_dano_moral_expresso_mp),
        valor_dano_moral_pedido=dano_pedido,
        valor_dano_moral_sugerido=dano_pedido,
        valor_dano_moral_decidido=valor_dano_decidido,
        detracao=detracao,
        crimes_nao_condenados=crimes_nao_condenados,
    )

    if nomes_presumidos:
        nomes = ", ".join(nomes_presumidos)
        dosimetria_global.alertas_globais.append(
            f"Simulação: condenação PRESUMIDA, apenas para cálculo, quanto a {nomes} (sem veredicto explícito do juízo — "
            "simulacao_presume_condenacao). A sentença de caso real exige veredicto por crime."
        )
    dosimetria_global.alertas_globais.extend(alertas_resolucao)
    dosimetria_global.alertas_globais.extend(detracao.pendencias)
    if detracao.origem == "autos" and detracao.dias > 365:
        dosimetria_global.alertas_globais.append(
            f"Detração apurada dos autos: {detracao.dias} dias. Confira o período documentado."
        )
    prescricao = calcular_prescricao_por_crime(
        dosimetria_global,
        data_fato=dt_fato,
        data_recebimento_denuncia=dt_rec,
        data_sentenca=data_sentenca,
        reu_menor_21_na_data_fato=menor_21,
        reu_maior_70_na_sentenca=maior_70,
        imputacoes_sem_pena=alvos_sem_pena,
    )
    periodo = dosimetria_global.detracao.descricao_periodos if dosimetria_global.detracao else ""
    return dosimetria_global, prescricao, decisoes_crimes, criterio_1fase, periodo


def _idade_em(nascimento: datetime.date, referencia: datetime.date) -> int:
    return referencia.year - nascimento.year - ((referencia.month, referencia.day) < (nascimento.month, nascimento.day))


def flags_idade_art115(
    data_nascimento_reu: Any,
    dt_fato: datetime.date | None,
    data_sentenca: datetime.date | None,
    override_menor_21: Any = None,
    override_maior_70: Any = None,
) -> tuple[bool, bool]:
    """Flags do art. 115 do CP (prazo prescricional reduzido de metade).

    O valor informado explicitamente pelo juízo (override, ``True``/``False``) prevalece. Sem ele, as
    flags são calculadas a partir de ``meta.data_nascimento_reu`` (menor de 21 anos na data do fato;
    70 anos completos na data da sentença). Sem data de nascimento, nada é presumido (``False``).
    """
    nasc = _resolver_data(data_nascimento_reu)
    menor_21 = bool(nasc and dt_fato and _idade_em(nasc, dt_fato) < 21)
    maior_70 = bool(nasc and data_sentenca and _idade_em(nasc, data_sentenca) >= 70)
    menor_21 = _bool_ov({"reu_menor_21_na_data_fato": override_menor_21}, "reu_menor_21_na_data_fato", menor_21)
    maior_70 = _bool_ov({"reu_maior_70_na_sentenca": override_maior_70}, "reu_maior_70_na_sentenca", maior_70)
    return menor_21, maior_70


# ==============================================================================
# Trechos reutilizáveis (dosimetria e dispositivo)
# ==============================================================================

def _dias_multa_extenso(n: int) -> str:
    return f"{n} ({numero_por_extenso(n)}) dias-multa"


def _texto_valor_unitario_multa(valor: str) -> str:
    return f"no valor unitário de {valor} do salário mínimo vigente ao tempo do fato (art. 49, § 1º, do CP)"


def texto_multa_dispositivo(dosimetria_global: DosimetriaGlobalOutput | ComandosDispositivo) -> str:
    comandos = (
        dosimetria_global if isinstance(dosimetria_global, ComandosDispositivo)
        else construir_comandos_dispositivo(dosimetria_global)
    )
    partes = [
        f"{_dias_multa_extenso(multa.dias)}"
        + (f" pela imputação de {multa.imputacao}" if len(comandos.multas) > 1 else "")
        + f", {_texto_valor_unitario_multa(multa.valor_unitario)}"
        for multa in comandos.multas
    ]
    texto = ", além do pagamento de " + "; ".join(partes) if partes else ""
    if comandos.multa_pendente:
        texto += f". {marcador('decisão sobre a quantidade e o valor unitário da multa')}"
    return texto


_ROTULO_ESPECIE = {"reclusao": "reclusão", "detencao": "detenção", "prisao_simples": "prisão simples"}


def texto_regime_dispositivo(dosimetria_global: DosimetriaGlobalOutput | ComandosDispositivo) -> str:
    """"REGIME INICIAL X" ou, para penas de espécies distintas com regimes diferentes, um por espécie."""
    por_especie = dict(getattr(dosimetria_global, "regime_por_especie", {}) or {})
    if len(set(por_especie.values())) > 1:
        partes = [
            f"REGIME INICIAL {reg.upper()} quanto à pena de {_ROTULO_ESPECIE.get(esp, esp)}"
            for esp, reg in por_especie.items()
        ]
        return " e ".join(partes)
    return f"REGIME INICIAL {dosimetria_global.regime_inicial.upper()}"


def texto_dano_moral_dispositivo(
    dosimetria_global: DosimetriaGlobalOutput,
    *,
    nome_ofendida: str,
    data_fato_br: str,
    id_den: str,
    infinitivo: bool = True,
) -> str:
    """Comando do dispositivo sobre a reparação mínima (art. 387, IV, do CPP).

    Distingue arbitramento positivo, decisão de deixar de fixar, arbitramento pendente e ausência de pedido.
    """
    valor = float(getattr(dosimetria_global, "indenizacao_dano_moral_tema983", 0.0) or 0.0)
    vitima = f" {nome_ofendida}" if nome_ofendida else ""
    if valor > 0:
        corpo = (
            f"ao pagamento do valor mínimo de {formatar_moeda_com_extenso(valor)} a título de reparação pelos danos morais sofridos pela ofendida{vitima}, "
            f"nos termos do art. 387, inciso IV, do Código de Processo Penal e do Tema Repetitivo 983/STJ, montante que deverá ser corrigido monetariamente pelo IPCA "
            f"a partir da publicação desta sentença (Súmula 362/STJ) e acrescido de juros de mora a contar do evento danoso ({data_fato_br} — Súmula 54/STJ), "
            f"calculados pela Taxa Selic deduzido o índice de atualização monetária, na forma do art. 406, § 1º, do Código Civil (Lei nº 14.905/2024)"
        )
        return f"CONDENAR o acusado {corpo}" if infinitivo else f"CONDENO o acusado {corpo}"
    if dosimetria_global.valor_dano_moral_decidido == 0:
        corpo = "valor mínimo para reparação do dano moral, conforme decisão expressa do juízo"
        return f"DEIXAR DE FIXAR {corpo}" if infinitivo else f"DEIXO DE FIXAR {corpo}"
    if dosimetria_global.dano_moral_arbitramento_pendente:
        return marcador("decisão sobre a reparação mínima pedida pelo Ministério Público")
    corpo = (
        f"valor mínimo indenizatório a título de danos morais (art. 387, inciso IV, do CPP e Tema Repetitivo 983/STJ), "
        f"ante a ausência de pedido expresso na peça acusatória ({id_den})"
    )
    return f"DEIXAR DE FIXAR {corpo}" if infinitivo else f"DEIXO DE FIXAR {corpo}"


def _texto_segunda_fase(c: Any, spec: Any) -> List[str]:
    frases: List[str] = []
    if c.bis_in_idem_art61f_bloqueado:
        if getattr(spec, "code", "") in ("art_147a_cp", "art_218c_cp"):
            motivo_bis = (
                f"porquanto a condição de mulher/relação de afeto já é considerada na causa de aumento específica do {spec.artigo_formatado}, aplicada na terceira fase"
            )
        else:
            motivo_bis = (
                f"porquanto a situação de violência doméstica e familiar e as razões da condição do sexo feminino já qualificam ou integram as elementares típicas do {spec.artigo_formatado}"
            )
        frases.append(
            f"Deixo de aplicar a circunstância agravante genérica do art. 61, inciso II, alínea 'f', do Código Penal por vedado bis in idem, {motivo_bis}."
        )
    if c.regra_atenuante_etaria == "pendente":
        frases.append(marcador("hipótese de violência sexual contra a mulher para a incidência da atenuante etária"))
    elif c.regra_atenuante_etaria == "excluida":
        frases.append("A Lei 15.160/2025 afasta a atenuante do art. 65, I, do CP nesta imputação por violência sexual contra a mulher.")
    if c.fontes_reincidencia:
        frases.append("A reincidência decorre de condenação documentada em " + "; ".join(c.fontes_reincidencia) + ".")
    if c.revisao_proporcao_confissao_pendente:
        frases.append(marcador("proporção da atenuante da confissão e eventual compensação conforme o Tema 1194 do STJ"))
        return frases
    if c.compensacao_multirreincidencia_pendente:
        frases.append(marcador("compensação proporcional entre multirreincidência e confissão, conforme o Tema 585 do STJ"))
        return frases
    agrav = list(getattr(c, "agravantes_ativas", []) or [])
    aten = list(getattr(c, "atenuantes_ativas", []) or [])
    if c.compensacao_confissao_reincidencia:
        frases.append(
            "Presentes a atenuante da confissão espontânea (art. 65, inciso III, alínea 'd', do Código Penal e Súmula 545/STJ) "
            "e a agravante da reincidência (art. 61, inciso I, do CP), opera-se a compensação integral entre ambas, "
            "em observância ao art. 67 do Código Penal e ao Tema Repetitivo 585/STJ."
        )
        agrav = [a for a in agrav if not a.startswith("reincidência")]
        aten = [a for a in aten if not a.startswith("confissão")]
    if agrav and aten:
        n_comp = min(len(agrav), len(aten))
        if len(agrav) == len(aten):
            desfecho = "que se compensam integralmente nos termos do art. 67 do Código Penal (concurso de circunstâncias agravantes e atenuantes)"
        elif len(agrav) > len(aten):
            saldo = len(agrav) - len(aten)
            desfecho = (
                f"compensando-se {n_comp} delas nos termos do art. 67 do Código Penal e remanescendo {saldo} agravante(s), "
                f"pelo que elevo a pena em 1/6 (um sexto) por circunstância remanescente"
            )
        else:
            saldo = len(aten) - len(agrav)
            desfecho = (
                f"compensando-se {n_comp} delas nos termos do art. 67 do Código Penal e remanescendo {saldo} atenuante(s), "
                f"pelo que reduzo a pena em 1/6 (um sexto) por circunstância remanescente, observado o mínimo legal"
            )
        frases.append(
            f"Concorrem a(s) atenuante(s) de {', '.join(aten)} e a(s) agravante(s) de {', '.join(agrav)}, {desfecho}."
        )
    elif agrav:
        frases.append(
            f"Incide(m) a(s) circunstância(s) agravante(s) de {', '.join(agrav)}, elevando-se a pena nesta fase em 1/6 (um sexto) por circunstância."
        )
    elif aten:
        frases.append(f"Reconheço a(s) atenuante(s) de {', '.join(aten)}.")
    if c.sumula_231_aplicada:
        frases.append(
            "Em estrita observância à Súmula 231/STJ ('A incidência da circunstância atenuante não pode conduzir à redução da pena abaixo do mínimo legal'), "
            "a reprimenda intermediária não pode ser reduzida aquém do piso legal cominado em abstrato."
        )
    if not frases:
        frases.append("Inexistem circunstâncias agravantes ou atenuantes a serem sopesadas nesta segunda fase.")
    return frases


def _texto_terceira_fase(c: Any, spec: Any) -> str:
    partes: List[str] = []
    aum = getattr(c, "fracao_aumento_aplicada", None)
    dim = getattr(c, "fracao_diminuicao_aplicada", None)
    if getattr(c, "aumento_art147a_aplicado", False):
        partes.append(
            "Reconheço a causa especial de aumento de pena do art. 147-A, § 1º, inciso II, do Código Penal (+1/2), majorando a reprimenda na metade "
            "por ter sido o crime de perseguição cometido contra mulher por razões da condição do sexo feminino (Lei nº 11.340/2006 e Enunciado 39 FONAVID)."
        )
    elif getattr(c, "aumento_art141_p3_aplicado", False):
        partes.append(
            "Reconheço a causa de aumento do art. 141, § 3º, do Código Penal (Lei nº 14.994/2024), aplicando a pena em dobro por ter sido o crime contra a honra "
            "cometido contra mulher por razões da condição do sexo feminino."
        )
    elif majorante_24a := getattr(c, "majorante_art24a_p4", None):
        n, d = majorante_24a.fracao
        hipotese = (
            "violação de área de exclusão monitorada eletronicamente"
            if majorante_24a.hipotese == "area_exclusao"
            else "intervenção no dispositivo de monitoração sem autorização judicial"
        )
        partes.append(
            f"Reconheço a causa de aumento do art. 24-A, § 4º, da Lei Maria da Penha, "
            f"por {hipotese} ({majorante_24a.fonte_id}), na fração de {n}/{d}."
        )
    elif aum:
        partes.append(f"Aplico a causa de aumento de pena reconhecida, na fração de {aum[0]}/{aum[1]}.")
    if dim:
        partes.append(f"Aplico a causa de diminuição de pena reconhecida, na fração de {dim[0]}/{dim[1]}.")
    if not partes:
        return "c) Terceira Fase (Causas de Aumento e de Diminuição): Não concorrem causas gerais ou especiais de aumento ou de diminuição de pena."
    return "c) Terceira Fase (Causas de Aumento e de Diminuição): " + " ".join(partes)


def _texto_antecedentes_neutro(id_cac: str) -> str:
    """Antecedentes neutros sem afirmar o conteúdo de uma certidão que o motor não leu."""
    if id_cac:
        return (
            f"Não reconhecida condenação definitiva anterior apta a configurar maus antecedentes, à vista da certidão de antecedentes "
            f"criminais juntada aos autos ({id_cac}), sendo vedada a valoração de inquéritos policiais e ações penais em curso "
            f"(Súmula 444/STJ). Circunstância judicial neutra."
        )
    return (
        "Não consta dos autos certidão de antecedentes criminais; ausente prova de condenação definitiva anterior, "
        "e vedada a valoração de inquéritos policiais e ações penais em curso (Súmula 444/STJ), a circunstância judicial é neutra."
    )


def _bloco_prescricao_dosimetria(prescricao: PrescricaoGlobalOutput | None, prefixo: str) -> str:
    if prescricao is None or not getattr(prescricao, "por_crime", None) or not prescricao_exige_topico(prescricao):
        return ""
    linhas = [
        f"{prefixo} Da Prescrição pela Pena Aplicada a Cada Crime (Arts. 109, 110, § 1º, 115 e 119 do Código Penal)",
        "",
        "Nos termos do art. 119 do Código Penal, a prescrição incide sobre a pena de cada crime, isoladamente, sem o acréscimo decorrente "
        "de concurso formal ou de continuidade delitiva:",
    ]
    for p in prescricao.por_crime:
        linhas.append(f"• {p.fundamentacao}")
    return "\n".join(linhas)


def formatar_secao_dosimetria_completa(
    dosimetria_global: DosimetriaGlobalOutput,
    criterio_1fase: str,
    id_cac: str = "",
    id_den: str = "",
    id_mp: str = "",
    id_def: str = "",
    ofendida_nome: str = "",
    detracao_periodo: str = "",
    domicilio_atual_sursis: str = "",
    data_fato_br: str = "",
    mpu_revogada_ref: str = "",
    incluir_bloco_custas_disposicoes: bool = True,
    prescricao: PrescricaoGlobalOutput | None = None,
    fundamento_liberdade: str = "",
    medidas_protetivas: DecisaoAcessoria | None = None,
    decisoes_acessorias: DecisoesAcessorias | None = None,
) -> str:
    """Capítulo da dosimetria: 8 vetores do art. 59 e cálculo trifásico por crime, concurso,
    prescrição por crime (art. 119), regime (por espécie), substituição, sursis, reparação mínima e,
    opcionalmente, custas/disposições finais.

    ``fundamento_liberdade``: frase do item 5 das disposições finais (direito de recorrer em
    liberdade). Vazio => marcador ``[COMPLETAR PELO JUÍZO: situação prisional do réu]`` — o motor
    nunca afirma que o réu "respondeu solto" sem documento nos autos.
    """
    doutrina_ab = obter_doutrina_por_id("DOUT-04")
    blocos: List[str] = []

    if getattr(dosimetria_global, "sem_condenacao", False) or not dosimetria_global.crimes_calculados:
        return (
            "3. Da Dosimetria da Pena (Arts. 59 e 68 do Código Penal)\n\n"
            "Ante a absolvição integral ou extinção da punibilidade reconhecida na fundamentação de mérito, "
            "resta prejudicada a aplicação de pena privativa de liberdade, de pena de multa, a análise das circunstâncias judiciais do art. 59 do Código Penal "
            "e a fixação de valor mínimo de reparação de danos (art. 387, inciso IV, do CPP)."
        )

    ref_def_intro = f" e à apreciação dos pedidos subsidiários da Defesa Técnica ({id_def})" if id_def else ""
    blocos.append(
        "3. Da Dosimetria da Pena (Arts. 59 e 68 do Código Penal)\n\n"
        "Em atenção ao princípio constitucional da individualização da pena (art. 5º, inciso XLVI, da CF/88), "
        "ao dever inafastável de fundamentação das decisões judiciais (art. 93, inciso IX, da CF/88 e art. 381, inciso III, do CPP)"
        f"{ref_def_intro}, "
        "passo à dosimetria trifásica das penas (art. 68 do Código Penal) para cada delito imputado cuja procedência restou reconhecida."
    )

    for idx, c in enumerate(dosimetria_global.crimes_calculados, start=1):
        spec = c.crime_spec
        prefixo_delito = f"3.{idx}."
        especie_formatada = formatar_especie_pena(spec.especie_pena)

        escolha_multa_txt = ""
        if spec.tem_multa_alternativa:
            escolha_multa_txt = (
                "\n\nEscolha do preceito secundário: Embora o tipo penal preveja alternativamente pena privativa de liberdade ou multa, "
                "tratando-se de delito praticado em contexto de violência doméstica e familiar contra a mulher, revela-se insuficiente a imposição isolada de multa "
                "(art. 17 da Lei nº 11.340/2006 e Tema Repetitivo 1189/STJ), razão pela qual opto pela aplicação da pena privativa de liberdade."
            )
        multa_cumulativa_txt = ", e multa" if getattr(spec, "tem_multa_cumulativa", False) else ""

        bloco_crime_cabecalho = (
            f"{prefixo_delito} Do Crime de {spec.nome} ({spec.artigo_formatado}):\n\n"
            f"Preceito secundário cominado em abstrato: sanção de {spec.min_pena.format_extenso()} a {spec.max_pena.format_extenso()} de {especie_formatada}"
            f"{', ou multa' if spec.tem_multa_alternativa else multa_cumulativa_txt}."
            f"{escolha_multa_txt}"
        )

        v = c.vetores_detalhados
        culp_texto = v["culpabilidade"].fundamentacao
        tem_filhos = any(termo in culp_texto.lower() for termo in ("filh", "infant", "criança", "descendente"))
        if v["culpabilidade"].desfavoravel and tem_filhos and "Alice Bianchini" not in culp_texto:
            culp_texto = (
                f"{culp_texto} Em consonância com o magistério doutrinário de {doutrina_ab['autor']} ({doutrina_ab.get('referencia_abnt', doutrina_ab['obra'])}), "
                "a violência perpetrada no ambiente doméstico na presença de descendente infante revela gravidade concreta desbordante do tipo penal e autoriza a exasperação da pena-base."
            )

        ant_texto = (
            v["antecedentes"].fundamentacao
            if v["antecedentes"].desfavoravel
            else _texto_antecedentes_neutro(id_cac)
        )
        if c.antecedentes_pendentes:
            pendencia_antecedentes = marcador("destinação dos registros das certidões por fase nesta imputação")
            ant_texto = (f"{ant_texto} {pendencia_antecedentes}"
                         if v["antecedentes"].desfavoravel else pendencia_antecedentes)
        bloco_art59 = (
            f"{prefixo_delito}1. Análise individualizada das circunstâncias judiciais (art. 59 do Código Penal):\n\n"
            f"a) CULPABILIDADE: {culp_texto}\n"
            f"b) ANTECEDENTES CRIMINAIS: {ant_texto}\n"
            f"c) CONDUTA SOCIAL: {v['conduta_social'].fundamentacao}\n"
            f"d) PERSONALIDADE DO AGENTE: {v['personalidade'].fundamentacao}\n"
            f"e) MOTIVOS DO CRIME: {v['motivos'].fundamentacao}\n"
            f"f) CIRCUNSTÂNCIAS DO CRIME: {v['circunstancias'].fundamentacao}\n"
            f"g) CONSEQUÊNCIAS DO CRIME: {v['consequencias'].fundamentacao}\n"
            f"h) COMPORTAMENTO DA VÍTIMA: {v['comportamento_vitima'].fundamentacao}"
        )

        if criterio_1fase == "um_oitavo_intervalo":
            criterio_expl = (
                f"Adota-se o critério objetivo-subjetivo com elevação de 1/8 (um oitavo) calculado sobre o intervalo entre as penas mínima e máxima cominadas em abstrato "
                f"(jurisprudência assente do Superior Tribunal de Justiça), "
                f"equivalente ao patamar de {PenaAnosMesesDias.from_total_days(c.acrescimo_por_vetor_dias).format_extenso()} de exasperação para cada circunstância judicial desfavorável."
            )
        else:
            criterio_expl = (
                f"Adota-se a fração de 1/6 (um sexto) sobre a pena mínima em abstrato, "
                f"equivalente a {PenaAnosMesesDias.from_total_days(c.acrescimo_por_vetor_dias).format_extenso()} para cada vetor desfavorável."
            )

        if len(c.vetores_negativos_efetivos) == 0:
            texto_1fase = (
                f"a) Primeira Fase (Pena-Base): {criterio_expl} "
                f"Sendo favoráveis ou neutras todas as 8 circunstâncias judiciais do art. 59 do Código Penal, "
                f"fixo a pena-base no mínimo legal em {c.pena_base.format_extenso()} de {especie_formatada}."
            )
        else:
            nomes_negativos = ", ".join(ROTULOS_VETORES_ART_59[k] for k in c.vetores_negativos_efetivos)
            texto_1fase = (
                f"a) Primeira Fase (Pena-Base): {criterio_expl} "
                f"Diante da valoração negativa de {len(c.vetores_negativos_efetivos)} circunstância(s) judicial(is) ({nomes_negativos}), "
                f"fixo a pena-base em {c.pena_base.format_extenso()} de {especie_formatada}."
            )

        texto_2fase = (
            f"b) Segunda Fase (Circunstâncias Legais): {' '.join(_texto_segunda_fase(c, spec))} "
            f"Fixo a pena intermediária em {c.pena_intermediaria.format_extenso()} de {especie_formatada}."
        )
        texto_3fase = _texto_terceira_fase(c, spec)

        multa_crime_txt = ""
        if c.multa_pendente:
            multa_crime_txt = f". {marcador('decisão sobre a quantidade e o valor unitário da multa desta imputação')}"
        elif c.dias_multa and c.valor_unitario_multa:
            multa_crime_txt = f", além de {_dias_multa_extenso(c.dias_multa)}, {_texto_valor_unitario_multa(c.valor_unitario_multa)}"
        texto_def = (
            f"d) Pena Definitiva do Delito: Torno concreta e definitiva a reprimenda privativa de liberdade fixada para o crime de "
            f"{spec.nome} ({spec.artigo_formatado}) em {c.pena_definitiva.format_extenso()} de {especie_formatada}{multa_crime_txt}."
        )

        bloco_trifasico = (
            f"{prefixo_delito}2. Cálculo trifásico da pena (art. 68 do Código Penal):\n\n"
            f"{texto_1fase}\n\n"
            f"{texto_2fase}\n\n"
            f"{texto_3fase}\n\n"
            f"{texto_def}"
        )
        blocos.append(f"{bloco_crime_cabecalho}\n\n{bloco_art59}\n\n{bloco_trifasico}")

    n_crimes = len(dosimetria_global.crimes_calculados)
    proximo = n_crimes + 1
    if n_crimes > 1:
        prefixo_unif = f"3.{proximo}."
        proximo += 1
        total_ext = dosimetria_global.pena_total_unificada.format_extenso()

        linhas_especies = []
        if "reclusao" in dosimetria_global.penas_por_especie:
            linhas_especies.append(f"• Pena Total de Reclusão: {dosimetria_global.penas_por_especie['reclusao'].format_extenso()} de reclusão;")
        if "detencao" in dosimetria_global.penas_por_especie:
            linhas_especies.append(f"• Pena Total de Detenção: {dosimetria_global.penas_por_especie['detencao'].format_extenso()} de detenção;")
        if "prisao_simples" in dosimetria_global.penas_por_especie:
            linhas_especies.append(f"• Pena Total de Prisão Simples: {dosimetria_global.penas_por_especie['prisao_simples'].format_extenso()} de prisão simples;")
        tipo_conc = getattr(dosimetria_global, "tipo_concurso", "material")
        frac_conc = getattr(dosimetria_global, "fracao_concurso", None)
        frac_str = f"{frac_conc[0]}/{frac_conc[1]}" if frac_conc else "1/6"
        beneficio = bool(getattr(dosimetria_global, "concurso_material_beneficio", False))
        reconhecido = getattr(dosimetria_global, "tipo_concurso_reconhecido", tipo_conc)

        if getattr(dosimetria_global, "total_dias_multa", 0) > 0:
            if tipo_conc == "continuado" and frac_conc:
                linhas_especies.append(
                    f"• Pena Total de Multa (Art. 71 CP — multa de um só crime exasperada de {frac_str}, vedada a soma do art. 72 no crime continuado): "
                    f"{_dias_multa_extenso(dosimetria_global.total_dias_multa)};"
                )
            else:
                linhas_especies.append(f"• Pena Total de Multa (Art. 72 CP): {_dias_multa_extenso(dosimetria_global.total_dias_multa)};")
        linhas_especies.append(f"• Total Global Unificado: {total_ext}.")
        lista_formatada = "\n".join(linhas_especies)

        if beneficio and reconhecido in ("formal", "continuado"):
            if reconhecido == "formal":
                titulo_ben = "Art. 70 do Código Penal - Concurso Formal com Cúmulo Material Benéfico"
                base_ben = (
                    "Reconhecido o concurso formal (art. 70, caput, do Código Penal), isto é, a prática dos delitos mediante uma única ação ou omissão, "
                    "a exasperação de uma só das penas elevada da fração legal resultaria em reprimenda superior ao cúmulo material das penas; "
                    "aplica-se, por isso, o cúmulo material benéfico do art. 70, parágrafo único, do Código Penal, somando-se as sanções impostas"
                )
            else:
                titulo_ben = "Art. 71 do Código Penal - Crime Continuado com Cúmulo Material Benéfico"
                base_ben = (
                    "Reconhecida a continuidade delitiva (art. 71, caput, do Código Penal), a exasperação de uma só das penas resultaria em reprimenda superior "
                    "ao cúmulo material das penas; adota-se, por analogia ao art. 70, parágrafo único, do Código Penal, o somatório das sanções, "
                    "mais benéfico ao acusado"
                )
            bloco_concurso = (
                f"{prefixo_unif} Da Unificação das Penas e do Concurso de Crimes ({titulo_ben})\n\n"
                f"{base_ben}:\n"
                f"{lista_formatada}"
            )
        elif tipo_conc == "formal":
            bloco_concurso = (
                f"{prefixo_unif} Da Unificação das Penas e do Concurso de Crimes (Art. 70 do Código Penal - Concurso Formal)\n\n"
                f"Praticados os delitos mediante uma única ação ou omissão com pluralidade de resultados, "
                f"incide a regra da exasperação do concurso formal prevista no art. 70, caput, do Código Penal, aplicando-se a pena mais grave elevada de {frac_str}, "
                f"fração proporcional ao número de infrações ({n_crimes}):\n"
                f"{lista_formatada}"
            )
        elif tipo_conc == "continuado":
            bloco_concurso = (
                f"{prefixo_unif} Da Unificação das Penas e do Concurso de Crimes (Art. 71 do Código Penal - Crime Continuado)\n\n"
                f"Praticadas as infrações da mesma espécie em idênticas condições de tempo, lugar e maneira de execução, "
                f"incide a regra da continuidade delitiva prevista no art. 71, caput, do Código Penal, aplicando-se a pena mais grave exasperada de {frac_str}, "
                f"fração proporcional ao número de infrações ({n_crimes}):\n"
                f"{lista_formatada}"
            )
        else:
            ressalva_exec = (
                "\n\nRessalva-se que, em sede executória perante a Vara de Execução Penal, deverá ser rigorosamente observada "
                "a diretriz do art. 69, caput, in fine, do Código Penal, executando-se primeiramente a pena de reclusão."
                if ("reclusao" in dosimetria_global.penas_por_especie and "detencao" in dosimetria_global.penas_por_especie)
                else ""
            )
            bloco_concurso = (
                f"{prefixo_unif} Da Unificação das Penas e do Concurso de Crimes (Art. 69 do Código Penal - Concurso Material)\n\n"
                f"Tendo o agente praticado os delitos mediante pluralidade de condutas autônomas e desígnios autônomos, "
                f"incide a regra do concurso material prevista no art. 69 do Código Penal, somando-se cumulativamente as sanções impostas:\n"
                f"{lista_formatada}"
                f"{ressalva_exec}"
            )
        blocos.append(bloco_concurso)

    bloco_presc = _bloco_prescricao_dosimetria(prescricao, f"3.{proximo}.")
    if bloco_presc:
        blocos.append(bloco_presc)

    # 4. Regime inicial e detração
    if dosimetria_global.detracao_dias > 0:
        resultado_detracao = dosimetria_global.detracao
        descricao = resultado_detracao.descricao_periodos if resultado_detracao else detracao_periodo
        if resultado_detracao and resultado_detracao.origem == "legado":
            descricao = "quantidade informada pelo juízo"
        periodo_detr_txt = f" ({descricao})" if descricao else ""
        regime_sem = getattr(dosimetria_global, "regime_sem_detracao", None)
        remanescente = getattr(dosimetria_global, "pena_considerada_regime", None)
        rem_txt = f" ({remanescente.format_extenso()} de pena remanescente)" if remanescente else ""
        if regime_sem and regime_sem != dosimetria_global.regime_inicial:
            efeito_detr = (
                f"A detração, considerada para a fixação do regime inicial (art. 387, § 2º, do CPP){rem_txt}, ALTEROU o regime: "
                f"sem o desconto seria o {regime_sem.upper()}; com ele, fixa-se o {dosimetria_global.regime_inicial.upper()}."
            )
        else:
            efeito_detr = (
                f"A detração foi considerada para a fixação do regime inicial (art. 387, § 2º, do CPP){rem_txt}, "
                f"e não alterou o regime resultante ({dosimetria_global.regime_inicial.upper()})."
            )
        bloco_detracao = (
            f"Nos termos do art. 387, § 2º, do Código de Processo Penal, registro que o acusado permaneceu preso cautelarmente "
            f"por {dosimetria_global.detracao_dias:02d} dia(s){periodo_detr_txt}, período computado para fins de detração penal. {efeito_detr}"
        )
    else:
        bloco_detracao = (
            "No tocante à detração penal (art. 387, § 2º, do CPP), não constam dos autos dias de prisão cautelar computáveis para a fixação do regime; "
            "caso existam, o período deverá ser descontado da pena para a definição do regime inicial, competindo ao Juízo da Vara de Execução Penal "
            "o cômputo definitivo e as adequações necessárias ao cumprimento da reprimenda."
        )
    if dosimetria_global.detracao and dosimetria_global.detracao.pendencias:
        bloco_detracao += " " + " ".join(dosimetria_global.detracao.pendencias)
    fund_regime = str(getattr(dosimetria_global, "fundamentacao_regime", "") or "").strip()
    fund_regime_txt = f"\n\n{fund_regime}" if fund_regime else ""
    bloco_regime = (
        f"4. Do Regime Inicial de Cumprimento de Pena (Art. 33, §§ 2º e 3º, do CP c/c Súmulas 718 e 719 do STF e 440 do STJ)\n\n"
        f"{bloco_detracao}\n\n"
        f"Fixo o {texto_regime_dispositivo(dosimetria_global)} para o início do cumprimento da pena privativa de liberdade, "
        f"com fulcro no art. 33, §§ 2º e 3º, e no art. 59, ambos do Código Penal.{fund_regime_txt}\n\n"
        f"Observam-se com rigor os verbetes sumulares vinculantes da jurisprudência pátria: "
        f"Súmula 440/STJ ('Fixada a pena-base no mínimo legal, é vedado o estabelecimento de regime prisional mais gravoso do que o cabível em razão da sanção imposta, com base apenas na gravidade abstrata do delito'), "
        f"Súmula 718/STF ('A opinião do julgador sobre a gravidade em abstrato do crime não constitui motivação idônea para a imposição de regime mais severo do que o permitido segundo a pena aplicada') "
        f"e Súmula 719/STF ('A imposição do regime de cumprimento mais severo do que a pena aplicada permitir exige motivação idônea')."
    )
    blocos.append(bloco_regime)

    if decisoes_acessorias is not None:
        blocos.extend((
            "5. Da Substituição da Pena Privativa de Liberdade (art. 44 do CP)\n\n"
            + decisoes_acessorias.substituicao.fundamento + "\n\n" + decisoes_acessorias.substituicao.comando,
            "6. Da Suspensão Condicional da Pena (art. 77 do CP)\n\n"
            + decisoes_acessorias.sursis.fundamento + "\n\n" + decisoes_acessorias.sursis.comando,
        ))
    else:
        # 5. Substituição (art. 44 do CP)
        if dosimetria_global.substituicao_art44_cabivel:
            bloco_subst = (
                f"5. Da Substituição da Pena Privativa de Liberdade por Restritivas de Direitos (Art. 44 do CP c/c Súmula 588 do STJ)\n\n"
                f"{dosimetria_global.motivo_vedacao_substituicao} "
                f"Ressalva-se que, nos termos do art. 17 da Lei nº 11.340/2006 e da tese repetitiva firmada no Tema Repetitivo 1189/STJ, "
                f"permanece vedada a fixação de pena de cesta básica ou outras de prestação pecuniária, bem como o pagamento isolado de multa."
            )
        elif getattr(dosimetria_global, "substituicao_pendente_modalidade", False):
            bloco_subst = (
                f"5. Da Substituição da Pena Privativa de Liberdade por Restritivas de Direitos (Art. 44 do CP c/c Súmula 588 do STJ)\n\n"
                f"{dosimetria_global.motivo_vedacao_substituicao} "
                f"Em qualquer hipótese, nos termos do art. 17 da Lei nº 11.340/2006 e do Tema Repetitivo 1189/STJ, "
                f"permanece vedada a fixação de pena de cesta básica ou outras de prestação pecuniária, bem como o pagamento isolado de multa."
            )
        else:
            ref_def_subst = f"Rejeito o pedido subsidiário defensivo ({id_def}): " if id_def else ""
            bloco_subst = (
                f"5. Da Substituição da Pena Privativa de Liberdade por Restritivas de Direitos (Art. 44 do CP c/c Súmula 588 do STJ)\n\n"
                f"{ref_def_subst}{dosimetria_global.motivo_vedacao_substituicao}\n\n"
                f"Reafirma-se a incidência cogente da Súmula 588/STJ: 'A prática de crime ou contravenção penal contra a mulher com violência ou grave ameaça no ambiente doméstico impossibilita a substituição da pena privativa de liberdade por restritiva de direitos'. "
                f"De igual modo, por expressa disposição do art. 17 da Lei nº 11.340/2006 e da tese repetitiva firmada no Tema Repetitivo 1189/STJ, "
                f"é peremptoriamente vedada a fixação de pena de cesta básica ou outras de prestação pecuniária, bem como a substituição de pena que implique o pagamento isolado de multa."
            )
        blocos.append(bloco_subst)

        # 6. Sursis (art. 77 do CP)
        conds_sursis_txt = ""
        if dosimetria_global.sursis_art77_cabivel and dosimetria_global.sursis_condicoes_legais:
            dom_info = f" (com fiscalização deprecada à comarca de domicílio atual: {domicilio_atual_sursis})" if domicilio_atual_sursis else ""
            conds_sursis_txt = (
                f"\n\nCondições do período de prova do Sursis Penal{dom_info}:\n"
                + "\n".join(dosimetria_global.sursis_condicoes_legais)
            )
        bloco_sursis = (
            f"6. Da Suspensão Condicional da Pena - Sursis Penal (Art. 77 do CP c/c Súmula 536 do STJ)\n\n"
            f"{dosimetria_global.fundamentacao_sursis}{conds_sursis_txt}\n\n"
            f"Ademais, relembre-se que, por força da Súmula 536/STJ ('A suspensão condicional do processo e a transação penal não se aplicam na hipótese de delitos sujeitos ao rito da Lei Maria da Penha'), "
            f"do art. 41 da Lei nº 11.340/2006, da ADC 19/STF e da ADI nº 4.424/DF, são constitucionalmente inaplicáveis os institutos despenalizadores da Lei nº 9.099/1995, sem prejuízo do sursis penal previsto nos arts. 77 a 82 do Código Penal."
        )
        blocos.append(bloco_sursis)

    # 7. Reparação mínima (art. 387, IV, do CPP)
    nome_vitima_ref = f" {ofendida_nome}" if ofendida_nome else ""
    tema_983_literal = formatar_citacao_acervo(obter_item_oficial_por_id("TEMA-983-STJ"), com_aspas=False)
    den_ref = f" na exordial acusatória ({id_den})" if id_den else " na exordial acusatória"
    if dosimetria_global.indenizacao_dano_moral_tema983 > 0:
        valor_formatado = formatar_moeda_com_extenso(dosimetria_global.indenizacao_dano_moral_tema983)
        mp_ref = f" e reiterado em alegações finais ({id_mp})" if id_mp else ""
        dt_ev = f" ({data_fato_br})" if data_fato_br else ""
        bloco_dano_especifico = (
            f"Havendo requerimento expresso formulado pelo Ministério Público{den_ref}{mp_ref}, "
            f"fixo a importância mínima de {valor_formatado} a título de indenização por danos morais in re ipsa em benefício da ofendida{nome_vitima_ref}, "
            f"montante que deverá ser corrigido monetariamente pelo IPCA a partir desta sentença (Súmula 362/STJ) e acrescido de juros legais de mora a contar da data do evento danoso{dt_ev} "
            f"(art. 398 do Código Civil e Súmula 54/STJ), calculados pela Taxa Selic deduzido o índice de atualização monetária, na forma do art. 406, § 1º, do Código Civil (Lei nº 14.905/2024)."
        )
    elif dosimetria_global.dano_moral_arbitramento_pendente:
        bloco_dano_especifico = (
            f"Houve requerimento expresso de reparação mínima formulado pelo Ministério Público{den_ref}. "
            f"{marcador('decisão sobre a reparação mínima')}"
        )
    elif dosimetria_global.valor_dano_moral_decidido == 0:
        bloco_dano_especifico = (
            f"Houve requerimento expresso de reparação mínima formulado pelo Ministério Público{den_ref}. "
            "Deixo de fixar valor mínimo indenizatório, conforme decisão expressa do juízo."
        )
    else:
        bloco_dano_especifico = (
            f"Deixo de fixar valor indenizatório mínimo em razão da ausência de pedido expresso na exordial acusatória ({id_den or 'denúncia'}), "
            f"assegurando-se o postulado do contraditório e da ampla defesa."
        )

    blocos.append(
        f"7. Da Reparação Mínima de Danos Morais (Art. 387, IV, do CPP c/c Tema Repetitivo 983 do STJ)\n\n"
        f"Nos termos do art. 387, inciso IV, do Código de Processo Penal, com a redação outorgada pela Lei nº 11.719/2008, "
        f"é dever do julgador incluir na condenação um valor mínimo para reparação dos danos causados pela infração penal, "
        f"abrangendo expressamente os danos de natureza moral.\n\n"
        f"A questão repousa pacificada sob a autoridade de recurso repetitivo perante o Superior Tribunal de Justiça (Tema Repetitivo 983/STJ): "
        f"\"{tema_983_literal}\".\n\n"
        f"{bloco_dano_especifico}"
    )

    # 8. Custas e disposições finais (opcional — no fluxo real evita duplicar o Dispositivo)
    if incluir_bloco_custas_disposicoes:
        mpu = medidas_protetivas or decidir_registro_medidas_protetivas(
            mpu_revogada_ref, datetime.date.today(),
        )
        mpu_txt = f"4. {mpu.fundamento} {mpu.comando}"
        liberdade_txt = fundamento_liberdade.strip() or (
            "5. Quanto ao direito de recorrer em liberdade (art. 387, § 1º, do CPP): " + marcador("situação prisional do réu") + "."
        )
        blocos.append(
            f"8. Das Custas Processuais e das Disposições Finais (Art. 804 do CPP, Art. 21 da Lei nº 11.340/2006, Art. 15, III, da CF/88)\n\n"
            f"Condeno a parte acusada ao pagamento das custas processuais, na forma do art. 804 do Código de Processo Penal, "
            f"ressalvada a suspensão de sua exigibilidade por 5 (cinco) anos (art. 98, § 3º, do CPC c/c art. 3º do CPP) caso concedida a gratuidade da justiça ou tenha sido assistido pela Defensoria Pública.\n\n"
            f"Em cumprimento ao mandamento cogente do art. 21 da Lei nº 11.340/2006 e do art. 201, § 2º, do CPP, determino a intimação pessoal da ofendida{nome_vitima_ref} acerca desta sentença condenatória "
            f"e de todos os atos subsequentes da execução penal, inclusive sobre eventual concessão de benefícios prisionais ou soltura do réu.\n\n"
            f"Com o trânsito em julgado desta decisão condenatória:\n"
            f"1. Suspendam-se os direitos políticos do sentenciado nos termos do art. 15, inciso III, da Constituição Federal, oficiando-se com urgência ao Tribunal Regional Eleitoral (TRE);\n"
            f"2. Expeça-se a competente Guia de Execução Definitiva ao Juízo da Execução Penal (SEEU);\n"
            f"3. Oficie-se aos órgãos competentes de identificação civil e criminal (Instituto de Identificação/SINIC/SINESP) para os registros estatísticos de estilo;\n"
            f"{mpu_txt}\n"
            f"{liberdade_txt}"
        )

    return "\n\n".join(blocos)


# ==============================================================================
# Extração de elementos fáticos (sem dados de outros processos)
# ==============================================================================

def _extrair_resumo_fatico_denuncia_e_bo(
    peca_den: PecaClassificada | None,
    peca_bo: PecaClassificada | None,
    valid_ids: set[str] | None = None,
) -> tuple[str, List[str], str]:
    """Narrativa fática da denúncia, códigos de laudos citados (ex.: ``UN-XXXX-XXXX``) e local dos fatos."""

    def _limpar_cabecalhos_pje(txt: str) -> str:
        if not txt:
            return ""
        limpo = re.sub(r"^\s*\d*\s*Num\.\s*\d+\s*-\s*P[áa]g\.\s*\d+[^\n]*$", " ", txt, flags=re.IGNORECASE | re.MULTILINE)
        limpo = re.sub(r"P[áa]g\.\s*Total\s*[-–]?\s*\d+[^\n]*", " ", limpo, flags=re.IGNORECASE)
        limpo = re.sub(r"Assinado\s+eletronicamente\s+por:[^\n]*", " ", limpo, flags=re.IGNORECASE)
        limpo = re.sub(r"N[úu]mero\s+do\s+documento:\s*\d+", " ", limpo, flags=re.IGNORECASE)
        limpo = re.sub(r"https?://\S+", " ", limpo, flags=re.IGNORECASE)
        limpo = re.sub(
            r"(?:MINIST[ÉE]RIO\s+P[ÚU]BLICO\s+DO\s+ESTADO[^\n]*\n)?(?:N[úu]cleo\s+de\s+Promotorias[^\n]*\n)?\d{1,2}\.?[ªa]?\s*Promotoria\s+de\s+Justi[çc]a[^\n]*(?:\n[^\n]*(?:CEP|Fones?|\.mp\.br|e-mail:)[^\n]*){0,4}",
            " ",
            limpo,
            flags=re.IGNORECASE,
        )
        limpo = re.sub(
            r"N[úu]cleo\s+de\s+Promotorias\s+de\s+Justi[çc]a[^\n]*(?:\n[^\n]*(?:CEP|Fones?|\.mp\.br|e-mail:)[^\n]*){0,4}",
            " ",
            limpo,
            flags=re.IGNORECASE,
        )
        return re.sub(r"\s+", " ", limpo).strip()

    den_txt = _limpar_cabecalhos_pje(peca_den.conteudo if peca_den else "")
    bo_txt = _limpar_cabecalhos_pje(peca_bo.conteudo if peca_bo else "")

    local_fatos = ""
    m_loc = re.search(
        r"(?:na\s+resid[êe]ncia\s+localizada\s+(?:na|à)|no\s+local\s*:)\s*([^\n;]{10,120}?)(?=\s*,\s*o\s+denunciado|\s*,\s*nesta\s+capital|\s*;|\n|$)",
        den_txt + " " + bo_txt,
        flags=re.IGNORECASE,
    )
    if m_loc:
        local_fatos = m_loc.group(1).strip().rstrip(",")

    narrativa_den = ""
    if den_txt:
        m_narr = re.search(
            r"(Consta\s+dos\s+autos.{40,2400}?)(?:As\s+infra[çc][õo]es\s+praticadas|A\s+materialidade|Extrai-se\s+dos\s+autos|Ouvida\s+pela\s+autoridade|ISTO\s+POSTO)",
            den_txt,
            flags=re.IGNORECASE,
        )
        if m_narr:
            narrativa_den = m_narr.group(1).strip()
        else:
            m_alt = re.search(r"((?:Consta\s+dos\s+autos|Segundo\s+apurado|Narra\s+o\s+inqu[eé]rito).{40,900})", den_txt, flags=re.IGNORECASE)
            narrativa_den = m_alt.group(1).strip() if m_alt else ""
        if narrativa_den and narrativa_den[-1] not in ".!?;":
            ultimo_ponto = max(narrativa_den.rfind("."), narrativa_den.rfind(";"))
            if ultimo_ponto > 60:
                narrativa_den = narrativa_den[: ultimo_ponto + 1].strip()
            else:
                narrativa_den = narrativa_den.rstrip(",:- ") + "."

    if narrativa_den and valid_ids is not None:
        def _sanitizar_sub_id(m: re.Match) -> str:
            canon = f"ID-{m.group(1)}"
            return m.group(0) if canon in valid_ids else "registro anexo dos autos"

        narrativa_den = re.sub(
            r"\bID[-\s]+(?:n\.?\s*[º°]?\s*)?(\d{6,12})\b",
            _sanitizar_sub_id,
            narrativa_den,
            flags=re.IGNORECASE,
        )

    laudos_encontrados: List[str] = []
    for m_l in re.finditer(r"\b(UN-[A-Z0-9-]{4,20})\b", f"{bo_txt} {den_txt}", flags=re.IGNORECASE):
        cod = m_l.group(1).upper()
        if cod not in laudos_encontrados:
            laudos_encontrados.append(cod)

    return narrativa_den, laudos_encontrados, local_fatos


_UFS = "AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO"
_RE_CIDADE_UF = re.compile(
    rf"\b([A-ZÀ-Ú][A-ZÀ-Ú]+(?:\s+(?:D[AEO]S?|[A-ZÀ-Ú][A-ZÀ-Ú]+)){{0,4}})\s*(?:/|-|–)\s*({_UFS})\b"
)
_PALAVRAS_MINUSCULAS = {"de", "da", "do", "das", "dos"}


def _titulo_cidade(nome: str) -> str:
    partes = nome.strip().split()
    return " ".join(p.lower() if (i > 0 and p.lower() in _PALAVRAS_MINUSCULAS) else p.capitalize() for i, p in enumerate(partes))


def _extrair_domicilio_atualizado_pecas(pecas: List[PecaClassificada]) -> tuple[str, str]:
    """Cidade/UF de comprovante/certidão de endereço atualizado do réu (qualquer UF; sem cidade fixa)."""
    peca_end = next(
        (p for p in pecas if "endereço atualizado" in p.titulo.lower() or "comprovante de endereço" in p.titulo.lower()),
        None,
    )
    if not peca_end:
        return "", ""
    txt_up = re.sub(r"\s+", " ", peca_end.conteudo or "").upper()
    m = _RE_CIDADE_UF.search(txt_up)
    if m:
        return f"{_titulo_cidade(m.group(1))}/{m.group(2)}", peca_end.doc_id
    return "", peca_end.doc_id


# ==============================================================================
# Teses defensivas: classificação e resposta coerente com o veredicto
# ==============================================================================

def _classe_tese(t: TeseDefensivaItem) -> str:
    txt = normalizar(f"{t.codigo} {t.titulo} {t.descricao_defesa}")
    tit = normalizar(t.titulo)
    if t.tipo == "PRELIMINAR" or t.codigo.upper().startswith("PREL"):
        if "emendatio" in txt:
            return "emendatio"
        if "inepcia" in txt:
            return "inepcia"
        if "incompetencia" in txt:
            return "incompetencia"
        return "preliminar_outra"
    if "revelia" in tit:
        return "revelia"
    if t.tipo == "SUBSIDIARIA" or "subsidi" in tit:
        if any(k in txt for k in ("pena-base", "minimo legal", "pena no minimo", "atenuante", "dosimetria", "confissao")):
            return "subsidiaria_pena"
        return "subsidiaria_dispositivo"
    if "amea" in tit and any(k in txt for k in ("animo", "ira", "exaltad", "colera", "cabeca quente")):
        return "ameaca_animo"
    if "dolo" in txt and ("protetiva" in txt or "24-a" in txt):
        return "dolo_24a"
    if "insuficiencia" in txt or "in dubio" in txt:
        return "insuficiencia"
    if "negativa de autoria" in txt:
        return "negativa_autoria"
    if "legitima defesa" in txt or "reciproc" in txt:
        return "legitima_defesa"
    if "vias de fato" in txt:
        return "desclassificacao"
    if "persegui" in txt and ("reitera" in txt or "habitual" in txt):
        return "perseguicao"
    if "inexigibilidade" in txt or "estado de necessidade" in txt or "coacao moral" in txt:
        return "excludente"
    if "domicilio" in txt:
        return "domicilio"
    return "outra"


_CRIMES_DA_CLASSE = {
    "ameaca_animo": lambda c: c == "art_147_cp" or c.startswith("art_147_"),
    "dolo_24a": lambda c: c.startswith("art_24a"),
    "desclassificacao": lambda c: c.startswith("art_129"),
    "perseguicao": lambda c: c.startswith("art_147a"),
    "domicilio": lambda c: c.startswith("art_150"),
}


def _referencia_tese(t: TeseDefensivaItem) -> str:
    escopo = f" · Fato {t.numero_fato}" if t.numero_fato else (
        f" · Imputação {t.id_imputacao}" if t.id_imputacao else ""
    )
    return f"{t.codigo} — {t.titulo}{escopo}"


def _responder_tese(t: TeseDefensivaItem) -> str:
    classe = _classe_tese(t)
    if classe == "revelia":
        return (
            "Assiste razão à Defesa ao pontuar que a revelia (art. 367 do CPP) não gera confissão ficta no processo penal, "
            "devendo a valoração observar o art. 155 do CPP."
        )
    if classe == "subsidiaria_pena":
        return (
            "Os pedidos subsidiários relativos à fixação da pena-base no mínimo legal, à incidência de atenuantes (Súmula 545/STJ — "
            "observada a vedação da Súmula 231/STJ quanto à redução da pena abaixo do mínimo legal), à detração penal (art. 387, § 2º, do CPP), "
            "ao regime inicial e à suspensão condicional da pena (art. 77 do CP) são apreciados no capítulo próprio da dosimetria."
        )
    if classe == "subsidiaria_dispositivo":
        return (
            "Os requerimentos relativos à substituição da pena (art. 44 do CP), à suspensão condicional da pena (art. 77 do CP), à gratuidade da justiça "
            "(art. 98, § 3º, do CPC), ao valor mínimo de reparação civil (art. 387, inciso IV, do CPP e Tema Repetitivo 983/STJ) e ao direito "
            "de recorrer em liberdade (art. 387, § 1º, do CPP) são decididos fundamentadamente no capítulo da dosimetria e no Dispositivo."
        )
    if classe == "outra":
        return marcador(f"apreciação específica da tese {_referencia_tese(t)}")

    return marcador(f"apreciação fundamentada da tese {_referencia_tese(t)}")


def _inciso_art5_lmp(vinculo: str) -> str:
    """Inciso do art. 5º da Lei nº 11.340/2006 (``"I"``, ``"II"``, ``"III"``) que o vínculo extraído dos autos
    sustenta, ou ``""`` quando os autos não o definem (nunca se presume relação íntima de afeto)."""
    v = (vinculo or "").strip()
    if not v:
        return ""
    m = re.search(r"art\.?\s*5º?\s*,?\s*(?:inciso\s+)?(III|II|I)\b", v, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    low = v.lower()
    if re.search(r"companheir|uni[ãa]o est[áa]vel|namor|c[ôo]njuge|espos[ao]|marido|\bex-|conviv[êe]ncia ínt|rela[çc][ãa]o [íi]ntima", low):
        return "III"
    if re.search(r"\bfilh|genitor|\bm[ãa]e\b|\bpai\b|\birm[ãa]|\bneto|\bneta|\bav[ôó]|sobrinh|padrast|madrast|entead|cunhad|sogr|\bgenro|\bnora\b|parent|famil", low):
        return "II"
    if re.search(r"coabit|empregad|agregad|esporadicamente", low):
        return "I"
    return ""


_MARC_ART5 = "inciso aplicável do art. 5º da Lei nº 11.340/2006 (I, II ou III) conforme o vínculo entre réu e ofendida"


def _art5_art7_txt(meta: MetadadosProcesso, plural: bool = False) -> str:
    """'art. 5º, inciso X, e art. 7º da Lei nº 11.340/2006' (ou marcador quando o inciso não consta dos autos)."""
    inc = _inciso_art5_lmp(getattr(meta, "vinculo_domestico", ""))
    ref = f"inciso {inc}" if inc else marcador(_MARC_ART5)
    if plural:
        return f"arts. 5º, {ref}, e 7º da Lei nº 11.340/2006"
    return f"art. 5º, {ref}, e art. 7º da Lei nº 11.340/2006"


def _responder_preliminar(
    t: TeseDefensivaItem,
    decisoes: List[Dict[str, Any]],
    refs: Dict[str, str],
    meta: MetadadosProcesso,
) -> str:
    classe = _classe_tese(t)
    if classe == "inepcia":
        return (
            f"Rejeito a preliminar. A denúncia ({refs['id_den']}) expõe o fato criminoso com suas circunstâncias, qualifica o acusado e classifica os crimes, "
            f"nos termos do art. 41 do Código de Processo Penal, o que viabilizou o pleno exercício do contraditório e da ampla defesa durante a instrução. "
            f"Ademais, conforme jurisprudência pacífica dos Tribunais Superiores, a superveniência de sentença após instrução exauriente esvazia a alegação de inépcia formal."
        )
    if classe == "incompetencia":
        inc = _inciso_art5_lmp(meta.vinculo_domestico)
        if inc == "III":
            ambito = f"no âmbito de relação íntima de afeto ({meta.vinculo_domestico})"
        elif meta.vinculo_domestico:
            ambito = f"no âmbito doméstico e familiar ({meta.vinculo_domestico})"
        else:
            ambito = "no âmbito doméstico e familiar (" + marcador("vínculo entre réu e ofendida extraído dos autos") + ")"
        return (
            f"Rejeito a preliminar. Os fatos imputados foram praticados contra mulher {ambito}, "
            f"o que atrai a competência deste Juizado Especializado ({_art5_art7_txt(meta)}), sendo prescindível a coabitação (Súmula 600/STJ)."
        )
    if classe == "emendatio":
        descl = [d for d in decisoes if d.get("veredicto") == "desclassificar" and d.get("desclassificado_de")]
        if descl:
            itens = "; ".join(
                f"{d['spec_original'].artigo_formatado} para {d['spec_efetivo'].artigo_formatado}" for d in descl
            )
            return (
                f"O acusado se defende dos fatos narrados, e não da capitulação jurídica provisória. Nos termos do art. 383 do Código de Processo Penal, "
                f"opero a emendatio libelli ({itens}), conforme fundamentado no mérito."
            )
        return marcador(f"decisão sobre o pedido de emendatio libelli (art. 383 do CPP) — {t.titulo}")
    return marcador(f"fundamentação do acolhimento ou da rejeição da preliminar {t.codigo} — {t.titulo}")


# ==============================================================================
# Absolvição e extinção por inciso
# ==============================================================================

def _texto_absolvicao(dec: Dict[str, Any]) -> str:
    inciso = dec.get("inciso")
    justificativa = str(dec.get("justificativa_juizo") or "").strip()
    if not inciso:
        return marcador("inciso do art. 386 do CPP aplicável")
    fundamento = dec["fundamento_legal"]
    base = f"Fundamento registrado: {fundamento}."
    return f"{base} {justificativa}" if justificativa else base


def _texto_extincao(dec: Dict[str, Any]) -> str:
    inciso = dec.get("inciso")
    inciso = "" if inciso == "" else (inciso or "IV")
    justificativa = str(dec.get("justificativa_juizo") or "").strip()
    sp = dec["spec_original"]
    base = (
        f"Relativamente à imputação do delito de {sp.nome} ({sp.artigo_formatado}), declaro extinta a punibilidade "
        f"com fundamento no {dec.get('fundamento_legal', 'art. 107 do Código Penal')}, nos termos do art. 61 do CPP"
    )
    if justificativa:
        return f"{base}: {justificativa}"
    if inciso == "IV":
        return f"{base}, conforme {marcador('marcos e cálculo da prescrição, decadência ou perempção reconhecida')}."
    detalhe = {
        "I": "certidão de óbito juntada aos autos (art. 62 do CPP)",
        "V": "ato de renúncia ou de perdão aceito constante dos autos",
        "VI": "retratação do agente nos casos em que a lei a admite",
    }.get(inciso, "documento dos autos que comprova a causa extintiva")
    return f"{base}, à vista de {marcador(detalhe)}."


# ==============================================================================
# Prescrição — síntese para as preliminares
# ==============================================================================

def prescricao_exige_topico(prescricao: PrescricaoGlobalOutput | None) -> bool:
    """A sentença só trata da prescrição quando ela ocorreu ou quando a verificação ficou incompleta.

    Ordem do usuário (2026-11-22): sem prescrição, não se informa que o processo não está prescrito.
    """
    if prescricao is None:
        return False
    if getattr(prescricao, "prescricao_reconhecida", False):
        return True
    por_crime = list(getattr(prescricao, "por_crime", []) or [])
    return any(
        p.prescricao_reconhecida or not p.verificacao_completa
        or getattr(p, "estado", None) in ("abstrata", "projetada", "pendente")
        for p in por_crime
    )


def texto_prescricao_preliminares(prescricao: PrescricaoGlobalOutput) -> Tuple[str, str]:
    """(título, texto) da prescrição nas preliminares, ou ("", "") quando não há o que tratar.

    O demonstrativo por crime fica na dosimetria, também só quando há prescrição ou verificação incompleta.
    """
    por_crime = list(getattr(prescricao, "por_crime", []) or [])
    if not por_crime or not prescricao_exige_topico(prescricao):
        return "", ""
    if any(getattr(p, "estado", None) for p in por_crime):
        tratados = [p for p in por_crime if p.estado in ("abstrata", "projetada", "pendente", "inconclusiva", "reconhecida")]
        partes = []
        for p in tratados:
            texto = p.fundamentacao
            if p.estado in ("abstrata", "pendente", "inconclusiva"):
                texto += " " + marcador(f"conferir e decidir os marcos prescricionais de {p.referencia_imputacao or p.artigo_formatado}")
            partes.append(texto)
        return "Da Prescrição da Pretensão Punitiva", " ".join(partes)

    prescritos = [p for p in por_crime if p.prescricao_reconhecida]
    if not prescritos:
        # Verificação incompleta (marco interruptivo ausente): o juízo precisa conferir antes de concluir.
        return "Da Prescrição", marcador(
            "conferir os marcos interruptivos ausentes nos autos para a verificação completa da prescrição; "
            "não havendo prescrição, excluir este tópico"
        )
    abstratos = [p for p in prescritos if p.prescricao_abstrata_reconhecida]
    so_retroativos = [p for p in prescritos if not p.prescricao_abstrata_reconhecida]
    partes: List[str] = []
    if abstratos:
        nomes = ", ".join(f"{p.crime_nome} ({p.artigo_formatado})" for p in abstratos)
        partes.append(
            f"O cálculo individualizado (art. 119 do Código Penal) indica a consumação da prescrição da pretensão punitiva em abstrato (art. 109 do CP) quanto a: {nomes}. "
            + marcador("declarar a extinção da punibilidade (art. 107, IV, do CP) quanto ao(s) delito(s) prescrito(s) e ajustar o veredicto correspondente")
        )
    if so_retroativos:
        nomes_r = ", ".join(f"{p.crime_nome} ({p.artigo_formatado})" for p in so_retroativos)
        partes.append(
            f"Quanto a {nomes_r}, a pena ora aplicada conduzirá à prescrição retroativa da pretensão punitiva após o trânsito em julgado para a acusação "
            "(art. 110, § 1º, do CP; Súmula 438/STJ: vedada a prescrição por pena hipotética), cujo reconhecimento não cabe nesta sentença."
        )
    # Resultado misto (revisão cruzada do CODEX, TASK-046): um crime pode ter prescrição e outro, marco ausente.
    inconclusivos = [p for p in por_crime if not p.prescricao_reconhecida and not p.verificacao_completa]
    if inconclusivos:
        nomes_i = ", ".join(f"{p.crime_nome} ({p.artigo_formatado})" for p in inconclusivos)
        partes.append(
            f"Quanto a {nomes_i}, "
            + marcador("conferir os marcos interruptivos ausentes nos autos para a verificação completa da prescrição")
        )
    return "Da Prescrição da Pretensão Punitiva", " ".join(partes)


# ==============================================================================
# Seções do caso real
# ==============================================================================

def _ref(p: Optional[PecaClassificada]) -> str:
    return f"{p.doc_id} ({p.folha})" if p and p.folha else (p.doc_id if p else "")


def _revogacao_mpu_documentada(pecas: List[PecaClassificada]) -> PecaClassificada | None:
    # Um título não demonstra qual de várias decisões continua aplicável.
    atos = [peca for peca in pecas if peca.categoria == "DECISAO_MPU"]
    if len(atos) != 1:
        return None
    ato = atos[0]
    if "revoga" not in normalizar(ato.titulo):
        return None
    comando = re.compile(r"(?:^|[.:] )(?:(?:ante o exposto|diante do exposto)[, :]*)?revogo (?:integralmente )?(?:as )?medidas protetivas\b")
    return ato if any(comando.search(normalizar(linha)) for linha in ato.conteudo.splitlines()) else None


def _gerar_secoes_caso_real(
    pecas: List[PecaClassificada],
    meta: MetadadosProcesso,
    matriz_oral: MatrizCorroboracaoOral,
    dosimetria_global: DosimetriaGlobalOutput,
    prescricao: PrescricaoGlobalOutput,
    criterio_1fase: str,
    style: Dict[str, Any],
    detracao_periodo: str = "",
    decisoes_crimes: List[Dict[str, Any]] | None = None,
    data_sentenca: datetime.date | None = None,
    settings: Any = None,
    juizo_sobre_provas: JuizoSobreProvas | None = None,
    decisoes_acessorias: DecisoesAcessorias | None = None,
) -> tuple[str, str, str, str, str]:
    """Relatório, preliminares, mérito, dosimetria e dispositivo de um processo real, ancorados nos
    IDs reais dos autos, sem dado de outro processo. Onde o motor não tem fundamento concreto,
    insere ``[COMPLETAR PELO JUÍZO: ...]``."""
    settings = settings or load_settings()
    data_sentenca = data_sentenca or datetime.date.today()
    juizo_sobre_provas = juizo_sobre_provas or JuizoSobreProvas()
    decisoes_crimes = list(decisoes_crimes or [])

    peca_bo = next(
        (p for p in pecas if p.categoria == "BO" and p.doc_id != "ID-CAPA-PJE" and "-SUB-" not in p.doc_id), None
    ) or next((p for p in pecas if p.categoria == "BO" and p.doc_id != "ID-CAPA-PJE"), None) or next(
        (p for p in pecas if p.categoria == "BO"), None
    )
    peca_den = next((p for p in pecas if p.categoria == "DENUNCIA"), None)
    peca_rec = next(
        (p for p in pecas if p.categoria == "RECEBIMENTO_DENUNCIA" and "saneamento" not in p.titulo.lower()), None
    ) or next((p for p in pecas if p.categoria == "RECEBIMENTO_DENUNCIA"), None)
    peca_san = next((p for p in pecas if p.categoria == "RECEBIMENTO_DENUNCIA" and "saneamento" in p.titulo.lower()), None)
    peca_resp = next((p for p in pecas if p.categoria == "RESPOSTA_ACUSACAO"), None)
    peca_ata = next(
        (p for p in pecas if p.categoria == "TERMO_AUDIENCIA" and "juntada" not in p.titulo.lower() and "custódia" not in p.titulo.lower()),
        None,
    ) or next((p for p in pecas if p.categoria == "TERMO_AUDIENCIA" and "custódia" not in p.titulo.lower()), None) or next(
        (p for p in pecas if p.categoria == "TERMO_AUDIENCIA"), None
    )
    peca_midia_cert = next(
        (p for p in pecas if "juntada de mídias" in p.titulo.lower() and "custódia" not in p.titulo.lower()), None
    ) or next((p for p in pecas if "juntada de mídias" in p.titulo.lower()), None)
    peca_cac = next((p for p in pecas if p.categoria == "CAC_FAC"), None)
    peca_mp = next((p for p in pecas if p.categoria == "ALEGACOES_FINAIS_MP"), None)
    peca_def = next((p for p in pecas if p.categoria == "ALEGACOES_FINAIS_DEFESA"), None)
    peca_custodia = next((p for p in pecas if "audiência de custódia" in p.titulo.lower()), None)
    peca_mpu = next(
        (p for p in pecas if p.categoria == "DECISAO_MPU" and "revoga" not in p.titulo.lower() and "audiência de custódia" not in p.titulo.lower()),
        None,
    )
    peca_rev_mpu = _revogacao_mpu_documentada(pecas)
    peca_liberdade = localizar_documento_liberdade(pecas)
    peca_cit = next((p for p in pecas if p.categoria == "CERTIDAO_INTIMACAO_MPU"), None)
    peca_laudo_lesao = next(
        (
            p for p in pecas
            if p.categoria == "LAUDO_PERICIAL" and re.search(r"les[ãa]o|corpo\s+de\s+delito", f"{p.titulo} {p.conteudo}", re.IGNORECASE)
        ),
        None,
    ) or next((p for p in pecas if p.categoria == "LAUDO_PERICIAL"), None)

    fallback_id = peca_den.doc_id if peca_den else (pecas[0].doc_id if pecas else "")
    id_bo = _ref(peca_bo) or fallback_id
    id_den = _ref(peca_den) or fallback_id
    id_rec = _ref(peca_rec) or fallback_id
    id_san = _ref(peca_san)
    id_resp = _ref(peca_resp) or fallback_id
    id_ata = _ref(peca_ata) or fallback_id
    id_cac = _ref(peca_cac)  # sem certidão nos autos => vazio (nunca outra peça no lugar)
    id_mp = _ref(peca_mp) or (peca_ata.doc_id if peca_ata else fallback_id)
    id_def = _ref(peca_def) or (peca_resp.doc_id if peca_resp else fallback_id)
    id_cit = peca_cit.doc_id if peca_cit else (peca_rec.doc_id if peca_rec else fallback_id)

    domicilio_atual, id_end = _extrair_domicilio_atualizado_pecas(pecas)
    narrativa_den, laudos_encontrados, local_fatos = _extrair_resumo_fatico_denuncia_e_bo(
        peca_den, peca_bo, valid_ids={p.doc_id for p in pecas}
    )
    refs = {"id_den": id_den}

    dt_f_ref = parse_qualquer_data(meta.data_fato) or data_sentenca
    if decisoes_crimes:
        crimes_imputados_nomes = ", ".join(
            f"{d['spec_original'].artigo_formatado} ({d['spec_original'].nome})" for d in decisoes_crimes
        )
    else:
        crimes_imputados_nomes = ", ".join(
            f"{sp.artigo_formatado} ({sp.nome})"
            for sp in (get_penal_type_for_date(code, dt_f_ref) for code in (meta.crimes_imputados or []))
        )

    interlocutores = consolidar_prova_oral_por_interlocutor(matriz_oral.depoentes, pecas=pecas)
    if interlocutores:
        midias_cit = ", ".join(f"{it['nome_base']} (Mídia {', '.join(it['media_ids'])})" for it in interlocutores)
    else:
        midias_cit = f"registros audiovisuais consignados no Termo de Audiência ({id_ata})"

    dt_fato_br = formatar_data_br(meta.data_fato)
    dt_fato_ext = formatar_data_br(meta.data_fato, por_extenso=True)
    dt_rec_ext = formatar_data_br(meta.data_recebimento_denuncia, por_extenso=True)
    datas_fatos = datas_dos_fatos(meta)
    fatos_em_datas_distintas = len(datas_fatos) > 1
    datas_fatos_texto = ", ".join(formatar_data_br(d, por_extenso=True) for d in datas_fatos) if fatos_em_datas_distintas else dt_fato_ext

    dt_aij_ext = (
        formatar_data_br(peca_ata.data_peca, por_extenso=True)
        if (peca_ata and parse_qualquer_data(peca_ata.data_peca))
        else "data designada nos autos"
    )

    # ------------------------------------------------------------------ I – RELATÓRIO
    inciso_art5 = _inciso_art5_lmp(meta.vinculo_domestico)
    if inciso_art5 == "III":
        vinculo_frase = f"que o réu e a ofendida mantiveram vínculo afetivo ({meta.vinculo_domestico})"
    elif meta.vinculo_domestico:
        vinculo_frase = f"que o réu e a ofendida mantinham o seguinte vínculo doméstico/familiar: {meta.vinculo_domestico}"
    else:
        vinculo_frase = "quanto ao vínculo entre o réu e a ofendida: " + marcador("vínculo doméstico/familiar entre réu e ofendida extraído dos autos")
    bloco_investigativo = (
        f"Narra a peça acusatória ({id_den}), lastreada nos elementos do procedimento investigatório ({id_bo}), "
        f"{vinculo_frase}. "
        + (
            narrativa_den
            if narrativa_den
            else f"Segundo a denúncia, os fatos imputados ocorreram em {datas_fatos_texto}" + (f" ({local_fatos})" if local_fatos else "") + "."
        )
    )

    trechos_intercorrencias: List[str] = []
    if peca_liberdade is not None:
        c_refs = ", ".join(dict.fromkeys(p.doc_id for p in (peca_custodia, peca_liberdade, peca_mpu) if p))
        trechos_intercorrencias.append(
            f"Apreciadas a custódia cautelar e as medidas protetivas de urgência ({c_refs}), foi concedida liberdade ao acusado ({peca_liberdade.doc_id})."
        )
    elif peca_custodia is not None:
        c_refs = ", ".join(dict.fromkeys(p.doc_id for p in (peca_custodia, peca_mpu) if p))
        trechos_intercorrencias.append(f"Realizada audiência de custódia ({c_refs}).")
    if peca_rev_mpu:
        trechos_intercorrencias.append(
            f"Posteriormente, foi proferida decisão revogando as medidas protetivas de urgência ({peca_rev_mpu.doc_id}), sem prejuízo do regular prosseguimento da ação penal."
        )

    texto_recebimento = (
        f"Por decisão interlocutória proferida em {dt_rec_ext} ({id_rec}), a denúncia foi regularmente recebida. "
        if meta.data_recebimento_denuncia else
        "Os marcos de recebimento constam individualmente nos atos de cada imputação. "
    )
    bloco_recebimento_resp = (
        texto_recebimento
        + f"Realizadas as diligências de citação e intimação ({id_cit}), a Defesa Técnica ofertou Resposta à Acusação ({id_resp}), "
        + (
            f"seguindo-se decisão de saneamento e afastamento da absolvição sumária nos termos do art. 397 do CPP ({id_san})."
            if id_san
            else "seguindo o feito seu regular trâmite processual."
        )
    )

    cert_mid_txt = f" e certidão de juntada de mídias {peca_midia_cert.doc_id}" if peca_midia_cert else ""
    end_txt = f" Consta nos autos comprovante de endereço atualizado do réu em {domicilio_atual} ({id_end})." if (domicilio_atual and id_end) else ""
    revelia_txt = (
        " Foi decretada a revelia do acusado nos termos do art. 367 do CPP, diante de sua ausência injustificada."
        if (peca_ata and "revelia" in peca_ata.conteudo.lower())
        else ""
    )
    cac_txt = f" Juntou-se a Certidão Estadual de Antecedentes Criminais atualizada ({id_cac})." if peca_cac else ""
    bloco_aij = (
        f"Em Audiência de Instrução e Julgamento realizada em {dt_aij_ext} ({id_ata}{cert_mid_txt}), "
        f"colheu-se a prova oral mediante registro audiovisual: {midias_cit}.{revelia_txt}{cac_txt}{end_txt}"
    )

    if peca_mp:
        mp_aleg_txt = (
            f"Em sede de alegações finais por memoriais ({id_mp}), o Ministério Público pugnou pela procedência da ação penal"
            + (" e pela fixação de valor mínimo de indenização por danos morais (Tema Repetitivo 983/STJ)." if meta.pedido_dano_moral_expresso_mp else ".")
        )
    else:
        mp_aleg_txt = (
            f"Em alegações finais orais colhidas em audiência ({id_ata}), o Ministério Público requereu a condenação do réu nos termos da exordial"
            + (" e a fixação de indenização mínima por danos morais (Tema Repetitivo 983/STJ)." if meta.pedido_dano_moral_expresso_mp else ".")
        )

    if meta.teses_defesa:
        teses_resumo_rel = "; ".join(f"({t.codigo}) {t.titulo}" for t in meta.teses_defesa)
        def_aleg_txt = (
            f"A Defesa Técnica, em suas razões finais ({id_def}), requereu a observância das garantias processuais e apresentou as seguintes teses: {teses_resumo_rel}."
        )
    else:
        def_aleg_txt = f"A Defesa Técnica apresentou razões finais ({id_def})."

    dm_rel_txt = (
        ", requerendo ainda a fixação de valor mínimo para reparação dos danos morais (art. 387, inciso IV, do CPP e Tema Repetitivo 983/STJ)"
        if meta.pedido_dano_moral_expresso_mp
        else ""
    )

    partes_rel = [
        "I – RELATÓRIO",
        style["abertura_relatorio"],
        (
            f"O MINISTÉRIO PÚBLICO DO ESTADO ofereceu denúncia ({id_den}) em face de {meta.nome_reu}, qualificado nos autos, "
            f"imputando-lhe a prática do(s) delito(s) previsto(s) no {crimes_imputados_nomes}, em contexto de violência doméstica e familiar contra a mulher "
            f"({_art5_art7_txt(meta)}), figurando como ofendida {meta.nome_ofendida}{dm_rel_txt}."
        ),
        bloco_investigativo,
        *trechos_intercorrencias,
        bloco_recebimento_resp,
        bloco_aij,
        mp_aleg_txt,
        def_aleg_txt,
        style["fechamento_relatorio"],
    ]
    relatorio = "\n\n".join(p for p in partes_rel if p.strip())

    # ------------------------------------------------------------------ II – FUNDAMENTAÇÃO (preliminares)
    prelims_list = [t for t in meta.teses_defesa if t.tipo == "PRELIMINAR"]
    blocos_prelims: List[str] = [
        "II – FUNDAMENTAÇÃO",
        "1. Das Questões Preliminares, Prejudiciais de Mérito, Direito Intertemporal e Prescrição",
    ]
    if prelims_list:
        for idx_p, pt in enumerate(prelims_list, start=1):
            corpo_p = f"{pt.descricao_defesa} {_responder_preliminar(pt, decisoes_crimes, refs, meta)}"
            blocos_prelims.append(f"1.{idx_p}. {_referencia_tese(pt)}:\n{corpo_p}")
    else:
        blocos_prelims.append(
            f"1.1. Da Regularidade Formal do Processo e das Condições da Ação Penal:\n"
            f"Não foram arguidas preliminares. A peça acusatória ({id_den}) atende aos requisitos do art. 41 do CPP e não se verificam nulidades a declarar de ofício."
        )

    idx_comp = max(2, len(prelims_list) + 1)
    antes_lei_14994 = (parse_qualquer_data(meta.data_fato) or data_sentenca) < datetime.date(2024, 10, 10)
    intertemp_txt = (
        f"Os fatos apurados datam de {dt_fato_ext}, anteriormente à Lei nº 14.994/2024 (10/10/2024), razão pela qual aplica-se o preceito secundário vigente à época "
        "(art. 5º, inciso XL, da CF/88 e art. 2º, parágrafo único, do CP), vedada a retroatividade da lex gravior."
        if antes_lei_14994
        else f"Os fatos datam de {dt_fato_ext}, atraindo a legislação vigente à época da conduta (tempus regit actum — art. 2º do CP)."
    )
    if fatos_em_datas_distintas:
        intertemp_txt = (
            f"Os fatos se reportam às datas de {datas_fatos_texto}. "
            "Aplica-se a cada imputação a legislação vigente na data da conduta (art. 2º do CP)."
        )
    blocos_prelims.append(
        f"1.{idx_comp}. Da Competência Especializada, da Ação Penal e do Direito Intertemporal:\n"
        f"A competência deste Juizado Especializado firma-se nos termos do {_art5_art7_txt(meta)}, "
        f"incidindo a Súmula 600/STJ (\"{obter_item_oficial_por_id('SUMULA-600-STJ')['texto_literal']}\") e o Enunciado 1 FONAVID. "
        f"{intertemp_txt} Por força do art. 41 da Lei nº 11.340/2006, da Súmula 536/STJ, da ADC 19/STF e da ADI nº 4.424/DF, são constitucionalmente inaplicáveis os institutos despenalizadores da Lei nº 9.099/1995."
    )
    titulo_presc, texto_presc = texto_prescricao_preliminares(prescricao)
    if titulo_presc:
        blocos_prelims.append(f"1.{idx_comp + 1}. {titulo_presc}:\n{texto_presc}")
    preliminares = "\n\n".join(blocos_prelims)

    # ------------------------------------------------------------------ II – FUNDAMENTAÇÃO (mérito)
    if interlocutores:
        depoimentos_texto = "\n\n".join(
            f"• {it['nome_base']} ({it['papel']} — {', '.join(it['media_ids'])}): {' '.join(it['sintese_consolidada'])}"
            for it in interlocutores
        )
    else:
        depoimentos_texto = (
            f"• Termo de audiência disponível ({peca_ata.doc_id})."
            if peca_ata
            else marcador("prova oral produzida em audiência")
        )

    laudos_cit_txt = ""
    if peca_laudo_lesao is not None:
        laudos_cit_txt = f" Além do acervo documental, consta o exame pericial juntado aos autos ({_ref(peca_laudo_lesao)})."
    elif laudos_encontrados:
        laudos_cit_txt = f" Há menção no boletim a exame pericial ({', '.join(laudos_encontrados)} — {id_bo})."

    doutrina_mbd = obter_doutrina_por_id("DOUT-01")
    tese_ed41_13 = obter_item_oficial_por_id("TESE-STJ-ED41-13")

    blocos_merito: List[str] = [
        "2. Do Mérito: Valoração Probatória sob Perspectiva de Gênero (Resolução CNJ nº 492/2023), Análise da Prova Oral e Apreciação das Teses Defensivas",
        (
            "2.1. Da Diretriz Hermenêutica da Resolução CNJ nº 492/2023, do Enunciado 45 FONAVID e da Jurisprudência do STJ:\n"
            f"{matriz_oral.fundamentacao_sintese}\n\n"
            "Em consonância com a jurisprudência consolidada do Superior Tribunal de Justiça (AgRg no AREsp 2.682.075/SE; APn 1.079/DF) e o Enunciado 45 FONAVID, "
            "a palavra da mulher em situação de violência doméstica possui especial relevo probatório quando harmônica com os elementos indiciários e periciais coligidos nos autos. "
            "Rejeita-se qualquer estereótipo discriminatório ou apelo à legítima defesa da honra (ADPF 779/STF). "
            "Nesse sentido, a Jurisprudência em Teses do STJ (Edição 41, Tese 13):\n\n"
            f"    {tese_ed41_13['texto_literal']}\n\n"
            f"Na mesma diretriz, consoante a síntese doutrinária de {doutrina_mbd['autor']} ({doutrina_mbd.get('referencia_abnt', doutrina_mbd['obra'])}), {doutrina_mbd['sintese'][0].lower() + doutrina_mbd['sintese'][1:]}"
        ),
        (
            "2.2. Relatos da Prova Oral e Acervo Pericial:\n\n"
            f"{depoimentos_texto}\n\n"
            f"A valoração dos relatos e dos documentos é registrada por imputação.{laudos_cit_txt} "
            f"Ressalte-se, na esteira do Superior Tribunal de Justiça (APn 1.079/DF), a relevância probatória da palavra da vítima quando corroborada por perícia e depoimentos testemunhais harmônicos com o contexto fático."
        ),
    ]

    sec_idx = 3
    itens_decisao = decisoes_crimes or [
        {"veredicto": "condenar", "spec_original": c.crime_spec, "spec_efetivo": c.crime_spec, "desclassificado_de": "", "fundamento_legal": ""}
        for c in dosimetria_global.crimes_calculados
    ]
    for dec in itens_decisao:
        verd = dec.get("veredicto", "condenar")
        sp_orig = dec["spec_original"]
        sp = dec["spec_efetivo"]

        referencia_fato = dec.get("referencia_imputacao") or ""
        referencia_merito = f" — {referencia_fato}" if referencia_fato else ""

        if verd == "absolver":
            blocos_merito.append(
                f"2.{sec_idx}. Da Absolvição — {sp_orig.nome} ({sp_orig.artigo_formatado}){referencia_merito}:\n"
                f"Impõe-se a absolvição. {_texto_absolvicao(dec)}\n\n{juizo_sobre_provas.merito(dec)}"
            )
            sec_idx += 1
            continue

        if verd == "extinguir":
            blocos_merito.append(
                f"2.{sec_idx}. Da Extinção da Punibilidade — {sp_orig.nome} ({sp_orig.artigo_formatado}){referencia_merito}:\n{_texto_extincao(dec)}"
            )
            sec_idx += 1
            continue

        descl_prefix = ""
        if verd == "desclassificar" and dec.get("desclassificado_de"):
            descl_prefix = (
                f"Opero a desclassificação / emendatio libelli (art. 383 do Código de Processo Penal) "
                f"da imputação inicial de {sp_orig.nome} ({sp_orig.artigo_formatado}) "
                f"para {sp.nome} ({sp.artigo_formatado}), conforme a decisão registrada pelo juízo.\n\n"
            )
        corpo_c = descl_prefix + juizo_sobre_provas.merito(dec)
        blocos_merito.append(f"2.{sec_idx}. Da Análise Típica e Probatória — {sp.nome} ({sp.artigo_formatado}){referencia_merito}:\n{corpo_c}")
        sec_idx += 1

    teses_subsidiarias = [
        t for t in meta.teses_defesa if _classe_tese(t).startswith("subsidiaria")
    ]
    teses_merito = [
        t for t in meta.teses_defesa
        if t.tipo != "PRELIMINAR" and not _classe_tese(t).startswith("subsidiaria")
    ]
    if teses_merito:
        linhas_teses = [
            f"• {_referencia_tese(t)} ({t.fonte_id or id_def}): {t.descricao_defesa} {_responder_tese(t)}"
            for t in teses_merito
        ]
        blocos_merito.append(
            f"2.{sec_idx}. Da Apreciação Individualizada das Teses Defensivas (Art. 381, Inciso III, do CPP):\n\n" + "\n\n".join(linhas_teses)
        )
    merito = "\n\n".join(blocos_merito)

    # ------------------------------------------------------------------ III – DOSIMETRIA
    assert decisoes_acessorias is not None
    acessorias = decisoes_acessorias
    dosimetria_texto = formatar_secao_dosimetria_completa(
        dosimetria_global=dosimetria_global,
        criterio_1fase=criterio_1fase,
        id_cac=id_cac,
        id_den=id_den,
        id_mp=id_mp,
        id_def=id_def,
        ofendida_nome=meta.nome_ofendida,
        detracao_periodo=detracao_periodo,
        domicilio_atual_sursis=domicilio_atual,
        data_fato_br=dt_fato_br,
        decisoes_acessorias=acessorias,
        incluir_bloco_custas_disposicoes=False,
        prescricao=prescricao,
    )

    if teses_subsidiarias:
        linhas_subsidiarias = []
        for tese in teses_subsidiarias:
            referencia = _referencia_tese(tese)
            if not dosimetria_global.crimes_calculados:
                desfecho = "Declaro prejudicada a análise porque a sentença não aplica pena nesta causa."
            elif tese.codigo == "SUB-01" and "abaixo do minimo" in normalizar(tese.trecho):
                fundamento_pedido = (
                    "pela confissão espontânea" if "confissao" in normalizar(tese.trecho)
                    else "no exame das atenuantes"
                )
                desfecho = (
                    f"Rejeito o pedido de fixação de pena abaixo do mínimo {fundamento_pedido}, "
                    "pois a incidência de atenuante na segunda fase respeita o mínimo cominado ao delito "
                    "(Súmula 231 do STJ)."
                )
            else:
                desfecho = marcador(f"apreciação da tese subsidiária {referencia}")
            linhas_subsidiarias.append(f"• {referencia} ({tese.fonte_id or id_def}): {desfecho}")
        dosimetria_texto += "\n\nPedidos subsidiários da defesa\n\n" + "\n\n".join(linhas_subsidiarias)

    dosimetria_texto += "\n\n" + "\n\n".join(
        f"{titulo}\n\n{decisao.fundamento}" for titulo, decisao in (
            ("Da Situação Cautelar", acessorias.cautelar),
            ("Das Medidas Protetivas de Urgência", acessorias.medidas_protetivas),
            ("Dos Efeitos da Sentença", acessorias.efeitos),
            ("Das Intimações", acessorias.intimacoes),
        )
    )
    data_extenso = formatar_data_br(data_sentenca, por_extenso=True)

    comandos = construir_comandos_dispositivo(dosimetria_global)
    nao_condenados = [item for item in comandos.imputacoes if item.veredicto in ("absolver", "extinguir")]
    comando_abs = "; e ".join(item.texto(meta.nome_reu) for item in nao_condenados)
    if comandos.sem_condenacao or not dosimetria_global.crimes_calculados:
        if not comando_abs:
            comando_abs = marcador("comando absolutório ou extintivo de cada imputação")
        dispositivo = (
            f"III – DISPOSITIVO\n\n"
            f"Ante o exposto, JULGO IMPROCEDENTE a pretensão punitiva estatal deduzida na denúncia ({id_den}) para {comando_abs}.\n\n"
            f"Sem condenação em custas processuais (art. 804 do CPP a contrario sensu) e prejudicada a fixação de valor indenizatório mínimo (art. 387, inciso IV, do CPP).\n\n"
            f"{acessorias.cautelar.comando}\n\n{acessorias.medidas_protetivas.comando}\n\n"
            f"Providências finais:\n{acessorias.intimacoes.comando}\n\n"
            f"{acessorias.efeitos.comando}\n\n"
            f"Publique-se. Registre-se. Intimem-se. Cumpra-se.\n\n"
            f"{settings.comarca_nome}, {data_extenso}.\n\n"
            f"{settings.magistrado_nome}\n"
            f"Juiz(a) de Direito — {settings.juizado_nome}"
        )
        return relatorio, preliminares, merito, dosimetria_texto, dispositivo

    pena_disp_str = comandos.texto_penas() + texto_multa_dispositivo(comandos)

    tipo_conc = getattr(dosimetria_global, "tipo_concurso", "material")
    if len(dosimetria_global.crimes_calculados) <= 1:
        concurso_nota = ""
    elif tipo_conc == "formal":
        concurso_nota = " em concurso formal (art. 70 do Código Penal),"
    elif getattr(dosimetria_global, "concurso_material_beneficio", False) and getattr(
        dosimetria_global, "tipo_concurso_reconhecido", ""
    ) == "formal":
        concurso_nota = " em concurso formal (art. 70, caput, do Código Penal), com aplicação do cúmulo material benéfico (art. 70, parágrafo único),"
    elif getattr(dosimetria_global, "concurso_material_beneficio", False) and getattr(
        dosimetria_global, "tipo_concurso_reconhecido", ""
    ) == "continuado":
        concurso_nota = " em continuidade delitiva (art. 71 do Código Penal), com aplicação do cúmulo material benéfico (art. 70, parágrafo único, por analogia),"
    elif tipo_conc == "continuado":
        concurso_nota = " em continuidade delitiva (art. 71 do Código Penal),"
    else:
        concurso_nota = " em concurso material (art. 69 do Código Penal),"

    dano_disp = texto_dano_moral_dispositivo(
        dosimetria_global, nome_ofendida=meta.nome_ofendida, data_fato_br=dt_fato_br, id_den=id_den, infinitivo=False
    )
    dano_disp = f"Com fundamento no art. 387, inciso IV, do Código de Processo Penal e no Tema Repetitivo nº 983 do Superior Tribunal de Justiça, {dano_disp}."

    tem_defensoria = any(
        "defensoria" in p.titulo.lower() or "defensoria" in p.conteudo.lower()
        for p in pecas if p.categoria not in CATEGORIAS_SEM_FONTE_DE_FATOS
    )
    custas_disp = (
        "CONDENO o acusado ao pagamento das custas processuais, nos termos do art. 804 do Código de Processo Penal. "
        + (
            f"Todavia, considerando que foi assistido pela Defensoria Pública, DEFIRO-LHE os benefícios da justiça gratuita ({id_def}), suspendendo a exigibilidade da cobrança pelo prazo de 05 (cinco) anos (art. 98, § 3º, do CPC c/c art. 3º do CPP).\n\n"
            f"Observem-se as prerrogativas legais conferidas à Defensoria Pública quanto à intimação pessoal mediante vista dos autos e à contagem em dobro de todos os prazos processuais (art. 128, inciso I, da LC nº 80/1994)."
            if tem_defensoria
            else "Ressalva-se eventual exame da gratuidade da justiça na fase executória (art. 98, § 3º, do CPC)."
        )
    )

    condenacoes = "; e ".join(
        item.texto(meta.nome_reu) for item in comandos.imputacoes if item.veredicto in ("condenar", "desclassificar")
    )
    bloco_parcial_abs = f"\n\nOutrossim, {comando_abs}." if comando_abs else ""

    verbo_julgo = "JULGO PARCIALMENTE PROCEDENTE" if nao_condenados else "JULGO PROCEDENTE"
    dispositivo = (
        f"III – DISPOSITIVO\n\n"
        f"Ante o exposto, {verbo_julgo} a pretensão punitiva estatal deduzida na denúncia ({id_den}) para {condenacoes}. "
        f"Aplicam-se os {_art5_art7_txt(meta, plural=True)}{concurso_nota.rstrip(',')}.{bloco_parcial_abs}\n\n"
        f"Consequentemente, fixo a pena definitiva em {pena_disp_str}, a ser cumprida no {texto_regime_dispositivo(comandos)} (art. 33, § 2º, do CP)."
        f"{comandos.texto_soma_penas()}\n\n"
        f"{acessorias.substituicao.comando}\n\n{acessorias.sursis.comando}\n\n"
        f"{dano_disp}\n\n"
        f"{acessorias.cautelar.comando}\n\n{acessorias.medidas_protetivas.comando}\n\n"
        f"{custas_disp}\n\n"
        f"Providências finais:\n{acessorias.intimacoes.comando}\n\n"
        f"{acessorias.efeitos.comando}\n\n"
        f"Publique-se. Registre-se. Intimem-se. Cumpra-se.\n\n"
        f"{settings.comarca_nome}, {data_extenso}.\n\n"
        f"{settings.magistrado_nome}\n"
        f"Juiz(a) de Direito — {settings.juizado_nome}"
    )

    return relatorio, preliminares, merito, dosimetria_texto, dispositivo


# ==============================================================================
# Cálculo único das seções determinísticas
# ==============================================================================

ORDEM_SECOES = ("relatorio", "preliminares", "merito", "dosimetria", "dispositivo")


@dataclass
class ResultadoDeterministico:
    """Tudo o que a geração determinística calcula — UMA vez por geração (reutilizado por seções e fallbacks)."""

    ctx: CaseContext
    meta: MetadadosProcesso
    pecas: List[PecaClassificada]
    matriz_oral: MatrizCorroboracaoOral
    is_demo: bool
    dt_fato: datetime.date
    dt_rec: datetime.date | None
    data_sentenca: datetime.date
    dosimetria_global: DosimetriaGlobalOutput
    prescricao: PrescricaoGlobalOutput
    decisoes_crimes: List[Dict[str, Any]]
    criterio_1fase: str
    detracao_periodo: str
    cabecalho: str
    secoes: Dict[str, str]
    alertas: List[str] = field(default_factory=list)
    simulacao_presume_condenacao: bool = False
    texto_demo: bool = False
    juizo_sobre_provas: JuizoSobreProvas = field(default_factory=JuizoSobreProvas)
    decisoes_acessorias: DecisoesAcessorias | None = None
    contexto_geracao: dict[str, Any] = field(default_factory=dict)

    def secao(self, nome: Any) -> str:
        chave = str(getattr(nome, "value", nome)).strip().lower()
        return self.secoes[chave]

    def decisoes_serializaveis(self) -> List[Dict[str, Any]]:
        return serializar_decisoes_imputacao(self.decisoes_crimes)


def _validar_bundle(case_bundle: Any) -> None:
    if not isinstance(case_bundle, dict):
        raise ValueError("O case_bundle fornecido deve ser um dicionário.")
    raw_docs = case_bundle.get("raw_documents")
    if raw_docs is None:
        raise KeyError("Campo obrigatório 'raw_documents' ausente no case_bundle.")
    if len(raw_docs) == 0:
        raise ValueError("Lista de documentos 'raw_documents' está vazia no case_bundle.")


def _resolver_data(*candidatos: Any) -> Optional[datetime.date]:
    for c in candidatos:
        if isinstance(c, datetime.datetime):
            return c.date()
        if isinstance(c, datetime.date):
            return c
        dt = parse_qualquer_data(c) if c else None
        if dt:
            return dt
    return None


#: API pública: primeira data válida entre os candidatos (date, datetime, ISO ou dd/mm/aaaa); None se nenhuma.
resolver_data = _resolver_data


def montar_cabecalho(settings: Any, meta: MetadadosProcesso, apenso: str | None) -> str:
    apenso_txt = str(apenso or "").strip()
    # Só há "Nº" quando o apenso é um número de processo; a descrição "Medidas Protetivas apreciadas no ID-..."
    # (sem autos próprios) não vai para o cabeçalho, onde ficava "Nº Medidas Protetivas apreciadas...".
    tem_numero_apenso = bool(re.search(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}", apenso_txt))
    linha_apenso = (
        f"MEDIDA PROTETIVA EM APENSO Nº {apenso_txt}\n"
        if apenso_txt and "sem apenso" not in apenso_txt.lower() and tem_numero_apenso
        else ""
    )
    return (
        f"{settings.tribunal_nome}\n"
        f"{settings.juizado_nome} — {settings.comarca_nome}\n"
        f"PROCESSO CRIMINAL Nº {meta.numero_processo}\n"
        f"{linha_apenso}"
        f"AUTOR: MINISTÉRIO PÚBLICO ESTADUAL\n"
        f"OFENDIDA: {meta.nome_ofendida}\n"
        f"ACUSADO: {meta.nome_reu}\n"
        f"NATUREZA: Ação Penal — Violência Doméstica e Familiar contra a Mulher (Lei nº 11.340/2006)\n\n"
        f"SENTENÇA"
    )


def padronizar_texto(texto: str) -> str:
    """Datas no padrão brasileiro e menções narrativas por extenso."""
    partes = re.split(r"(“[^”]*”)", texto or "")
    return "".join(
        parte if i % 2 else converter_mencoes_textuais_para_extenso(mascarar_e_padronizar_datas_texto(parte))
        for i, parte in enumerate(partes)
    )


def _coletar_alertas(
    meta: MetadadosProcesso,
    dosimetria_global: DosimetriaGlobalOutput,
    prescricao: PrescricaoGlobalOutput,
    extras: List[str],
) -> List[str]:
    alertas: List[str] = []
    alertas.extend(getattr(meta, "alertas_extracao", []) or [])
    alertas.extend(extras)
    alertas.extend(dosimetria_global.alertas_globais or [])
    for c in dosimetria_global.crimes_calculados:
        for a in c.alertas_juridicos or []:
            if a and a.strip():
                alertas.append(a)
    alertas.extend(getattr(prescricao, "alertas", []) or [])
    return list(dict.fromkeys(a for a in alertas if a and a.strip()))


@dataclass(frozen=True)
class PlanoSentenca:
    """Dados e decisões validados antes de redigir ou auditar uma minuta."""

    ctx: CaseContext
    meta: MetadadosProcesso
    dt_fato: datetime.date
    dt_rec: datetime.date | None
    data_sentenca: datetime.date
    dosimetria_global: DosimetriaGlobalOutput
    prescricao: PrescricaoGlobalOutput
    decisoes_crimes: List[Dict[str, Any]]
    criterio_1fase: str
    detracao_periodo: str
    alertas: List[str]
    simulacao_presume_condenacao: bool
    usar_texto_demo: bool
    juizo_sobre_provas: JuizoSobreProvas = field(default_factory=JuizoSobreProvas)
    decisoes_acessorias: DecisoesAcessorias | None = None
    contexto_geracao: dict[str, Any] = field(default_factory=dict)


def planejar_sentenca(
    case_bundle: Dict[str, Any],
    custom_dosimetria_override: Dict[str, Any] | None = None,
    *,
    exigir_veredicto: bool | None = None,
    contexto: CaseContext | None = None,
) -> PlanoSentenca:
    """Aplica os mesmos pré-requisitos e bloqueios nos modos de elaboração da minuta."""
    _validar_bundle(case_bundle)
    ctx = contexto or obter_contexto(case_bundle)
    contexto_geracao = obter_contexto_geracao(
        case_bundle, decisoes=custom_dosimetria_override, contexto=ctx, exigir_papel_confirmado=True,
    )
    ctx = contexto_geracao.get("contexto", ctx)
    prova_oral = contexto_geracao.get("prova_oral")
    meta = ctx.copia_meta()
    pecas = ctx.pecas
    is_demo = ctx.is_demo
    ov = dict(custom_dosimetria_override or {})
    exigir = (not is_demo) if exigir_veredicto is None else bool(exigir_veredicto)

    # Dados essenciais: extraídos dos autos, informados pelo juízo (metadados_override, já aplicados
    # no CaseContext) ou presentes no próprio bundle — nunca inventados.
    dt_fato = _resolver_data(meta.data_fato, case_bundle.get("data_fato"), *datas_dos_fatos(meta))
    dt_rec = _resolver_data(meta.data_recebimento_denuncia, case_bundle.get("data_recebimento_denuncia"))
    if not meta.crimes_imputados and isinstance(case_bundle.get("crimes_imputados"), list):
        meta.crimes_imputados = [str(c).strip() for c in case_bundle["crimes_imputados"] if str(c).strip()]
    faltantes = []
    if not str(meta.numero_processo or "").strip():
        faltantes.append("numero_processo")
    if dt_fato is None:
        faltantes.append("data_fato")
    recebimento_documentado = any(
        marco.get("tipo") == "recebimento_denuncia_ou_queixa"
        for item in ov.get("crimes_config") or []
        for marco in (_como_dict(item).get("linha_tempo_prescricao") or {}).get("interrupcoes", [])
    )
    if dt_rec is None and not ha_recebimento_por_imputacao(meta) and not recebimento_documentado:
        faltantes.append("data_recebimento_denuncia")
    if not meta.crimes_imputados and not ov.get("crimes_config"):
        faltantes.append("crimes_imputados")
    if faltantes:
        raise DadosInsuficientesError(
            faltantes,
            "Não foi possível gerar a sentença: dados essenciais não foram extraídos dos autos ("
            + ", ".join(faltantes)
            + "). Complemente-os nos metadados do processo.",
        )
    meta.data_fato = dt_fato.isoformat()
    meta.data_recebimento_denuncia = dt_rec.isoformat() if dt_rec else ""
    data_informada = ov.get("data_sentenca")
    if data_informada in (None, ""):
        data_informada = case_bundle.get("data_sentenca")
    if data_informada in (None, ""):
        data_sentenca = datetime.date.today()
    else:
        data_sentenca = _resolver_data(data_informada)
        if data_sentenca is None:
            raise DadosInsuficientesError(["data_sentenca"], "Informe uma data válida para a sentença.")

    dosimetria_global, prescricao, decisoes_crimes, criterio_1fase, detracao_periodo = resolver_dosimetria_e_decisoes(
        pecas=pecas,
        meta=meta,
        transcriptions=list(ctx.transcriptions),
        is_demo=is_demo,
        dt_fato=dt_fato,
        dt_rec=dt_rec,
        custom_dosimetria_override=ov,
        exigir_veredicto=exigir,
        data_sentenca=data_sentenca,
    )
    presume = any(d.get("presumido") for d in decisoes_crimes)

    # Prescrição EM ABSTRATO consumada impede o julgamento de mérito: na sentença de caso real o motor não
    # condena nem fixa pena para o crime já prescrito — exige o veredicto 'extinguir' (art. 107, IV, do CP).
    ids_informados = {
        str(_como_dict(item).get("id_imputacao") or "") for item in (ov.get("crimes_config") or [])
    } - {""}
    codigos_repetidos = Counter(d["crime_code"] for d in decisoes_crimes)
    decisoes_por_id = {d.get("id_imputacao"): d for d in decisoes_crimes if d.get("id_imputacao")}
    decisoes_sem_id = {d["crime_code"]: d for d in decisoes_crimes if not d.get("id_imputacao")}
    prescritos_abstrato = []
    for p in prescricao.por_crime:
        decisao = decisoes_por_id.get(p.id_imputacao) or decisoes_sem_id.get(p.crime_code, {})
        if decisao.get("veredicto") in ("absolver", "extinguir") or not (
            p.prescricao_abstrata_reconhecida or p.indicio_abstrato
        ):
            continue
        codigo = decisao.get("crime_code", p.crime_code)
        identidade = p.id_imputacao if p.id_imputacao in ids_informados or codigos_repetidos[codigo] > 1 else codigo
        prescritos_abstrato.append(identidade or codigo)
    if exigir and prescritos_abstrato:
        raise DecisaoJudicialPendenteError(
            list(dict.fromkeys(prescritos_abstrato)),
            "Prescrição da pretensão punitiva consumada em abstrato (art. 109 c/c art. 107, IV, do CP) para: "
            + ", ".join(dict.fromkeys(prescritos_abstrato))
            + ". Não é possível condenar nem fixar pena: informe o veredicto 'extinguir' (extinção da punibilidade) para esse(s) crime(s).",
        )

    alertas_extra: List[str] = []
    prescritos_retro = [
        p.crime_code for p in prescricao.por_crime if (p.projecao_retroativa or p.prescricao_retroativa_reconhecida)
        and not (p.indicio_abstrato or p.prescricao_abstrata_reconhecida) and p.estado != "reconhecida"
    ]
    if prescritos_abstrato:
        alertas_extra.append(
            "Prescrição em abstrato consumada para crime(s) com veredicto condenatório: "
            + ", ".join(dict.fromkeys(prescritos_abstrato))
            + ". Ajuste o veredicto para 'extinguir' (art. 107, IV, do CP)."
        )
    if prescritos_retro:
        alertas_extra.append(
            "Há projeção em concreto ou decisão pendente sobre a prescrição retroativa (art. 110, § 1º, do CP) "
            "quanto a: " + ", ".join(dict.fromkeys(prescritos_retro)) + ". Consulte os marcos de cada imputação."
        )

    usar_texto_demo = is_demo and not (ov.get("crimes_config") or ov.get("decisoes_acessorias") or ov.get("tipo_concurso") in ("formal", "continuado"))
    if usar_texto_demo and [c.crime_spec.code for c in dosimetria_global.crimes_calculados] != list(DEMO_CRIMES):
        usar_texto_demo = False

    juizo_sobre_provas = resolver_juizo_sobre_provas(ctx, meta, ov, decisoes_crimes, prova_oral=prova_oral)
    acessorias = None
    if not usar_texto_demo:
        revogacao = _revogacao_mpu_documentada(pecas)
        acessorias = resolver_acessorias(
            dosimetria_global, juizo_sobre_provas, dados=ov.get("decisoes_acessorias"),
            data_sentenca=data_sentenca, fontes_ativas={peca.doc_id for peca in pecas},
            revogacao_documentada=revogacao.doc_id if revogacao else "",
            absolveu=bool(decisoes_crimes) and all(d.get("veredicto") == "absolver" for d in decisoes_crimes),
            nome_ofendida=meta.nome_ofendida, destino_execucao=_extrair_domicilio_atualizado_pecas(pecas),
        )
    return PlanoSentenca(
        ctx=ctx, meta=meta, dt_fato=dt_fato, dt_rec=dt_rec, data_sentenca=data_sentenca,
        dosimetria_global=dosimetria_global, prescricao=prescricao, decisoes_crimes=decisoes_crimes,
        criterio_1fase=criterio_1fase, detracao_periodo=detracao_periodo,
        alertas=_coletar_alertas(meta, dosimetria_global, prescricao, alertas_extra),
        simulacao_presume_condenacao=presume, usar_texto_demo=usar_texto_demo,
        juizo_sobre_provas=juizo_sobre_provas, decisoes_acessorias=acessorias,
        contexto_geracao=contexto_geracao,
    )


def calcular_secoes_deterministicas(
    case_bundle: Dict[str, Any],
    custom_dosimetria_override: Dict[str, Any] | None = None,
    *,
    exigir_veredicto: bool | None = None,
    contexto: CaseContext | None = None,
    plano: PlanoSentenca | None = None,
) -> ResultadoDeterministico:
    """Redige as seções determinísticas a partir de um plano validado."""
    plano = plano or planejar_sentenca(
        case_bundle, custom_dosimetria_override, exigir_veredicto=exigir_veredicto, contexto=contexto,
    )
    settings = load_settings()
    style = get_style_profile()
    ctx = plano.ctx
    meta = plano.meta
    pecas = ctx.pecas
    dosimetria_global = plano.dosimetria_global
    prescricao = plano.prescricao
    decisoes_crimes = plano.decisoes_crimes
    criterio_1fase = plano.criterio_1fase
    detracao_periodo = plano.detracao_periodo
    data_sentenca = plano.data_sentenca
    usar_texto_demo = plano.usar_texto_demo
    if usar_texto_demo:
        titulo_presc, texto_presc = texto_prescricao_preliminares(prescricao)
        relatorio, preliminares, merito, dosimetria_texto, dispositivo = gerar_secoes_demo(
            meta=meta,
            pecas=pecas,
            matriz_oral=ctx.matriz_oral,
            dosimetria_global=dosimetria_global,
            prescricao=prescricao,
            criterio_1fase=criterio_1fase,
            style=style,
            settings=settings,
            titulo_prescricao=titulo_presc,
            texto_prescricao=texto_presc,
            data_sentenca=data_sentenca,
        )
    else:
        relatorio, preliminares, merito, dosimetria_texto, dispositivo = _gerar_secoes_caso_real(
            pecas=pecas,
            meta=meta,
            matriz_oral=ctx.matriz_oral,
            dosimetria_global=dosimetria_global,
            prescricao=prescricao,
            criterio_1fase=criterio_1fase,
            style=style,
            detracao_periodo=detracao_periodo,
            decisoes_crimes=decisoes_crimes,
            data_sentenca=data_sentenca,
            settings=settings,
            juizo_sobre_provas=plano.juizo_sobre_provas,
            decisoes_acessorias=plano.decisoes_acessorias,
        )

    prova_oral = plano.contexto_geracao.get("prova_oral")
    if prova_oral is not None and prova_oral.pendentes and MARCADOR_PAPEL not in merito:
        merito += "\n\n" + MARCADOR_PAPEL

    secoes = {
        "relatorio": padronizar_texto(relatorio),
        "preliminares": padronizar_texto(preliminares),
        "merito": padronizar_texto(merito),
        "dosimetria": padronizar_texto(dosimetria_texto),
        "dispositivo": padronizar_texto(dispositivo),
    }
    cabecalho = montar_cabecalho(settings, meta, case_bundle.get("autos_mpu_apenso") or meta.autos_mpu_apenso)

    return ResultadoDeterministico(
        ctx=ctx,
        meta=meta,
        pecas=pecas,
        matriz_oral=ctx.matriz_oral,
        is_demo=ctx.is_demo,
        dt_fato=plano.dt_fato,
        dt_rec=plano.dt_rec,
        data_sentenca=data_sentenca,
        dosimetria_global=dosimetria_global,
        prescricao=prescricao,
        decisoes_crimes=decisoes_crimes,
        criterio_1fase=criterio_1fase,
        detracao_periodo=detracao_periodo,
        cabecalho=cabecalho,
        secoes=secoes,
        alertas=list(plano.alertas),
        simulacao_presume_condenacao=plano.simulacao_presume_condenacao,
        texto_demo=usar_texto_demo,
        juizo_sobre_provas=plano.juizo_sobre_provas,
        decisoes_acessorias=plano.decisoes_acessorias,
        contexto_geracao=plano.contexto_geracao,
    )


def montar_sentenca_gerada(
    resultado: ResultadoDeterministico,
    secoes: Dict[str, str],
    *,
    motor_utilizado: str,
    secoes_fallback: List[str] | None = None,
    alertas_extras: List[str] | None = None,
) -> SentencaGerada:
    """Monta a ``SentencaGerada`` a partir das seções finais (determinísticas ou redigidas)."""
    settings = load_settings()
    meta = resultado.meta
    finais = {nome: padronizar_texto(secoes.get(nome, "")) for nome in ORDEM_SECOES}
    texto_completo = "\n\n".join(p for p in [resultado.cabecalho, *(finais[n] for n in ORDEM_SECOES)] if p and p.strip())
    texto_completo = padronizar_texto(texto_completo)
    marcadores = listar_marcadores_pendentes(texto_completo)
    alertas = list(resultado.alertas)
    if marcadores:
        alertas.append(f"{len(marcadores)} trecho(s) aguardam complementação pelo juízo ([COMPLETAR PELO JUÍZO: ...]).")
    for a in alertas_extras or []:
        if a not in alertas:
            alertas.append(a)
    dosimetria_dados = resultado.dosimetria_global.model_dump(mode="json")
    if resultado.decisoes_acessorias is not None:
        dosimetria_dados["decisoes_acessorias"] = resultado.decisoes_acessorias.dados_auditoria()

    return SentencaGerada(
        numero_processo=meta.numero_processo,
        comarca_juizado=f"{settings.juizado_nome} — {settings.comarca_nome}",
        ofendida=meta.nome_ofendida,
        reu=meta.nome_reu,
        data_prolatacao=formatar_data_br(resultado.data_sentenca),
        secao_cabecalho=resultado.cabecalho,
        secao_relatorio=finais["relatorio"],
        secao_preliminares=finais["preliminares"],
        secao_merito=finais["merito"],
        secao_dosimetria=finais["dosimetria"],
        secao_dispositivo=finais["dispositivo"],
        texto_completo=texto_completo,
        dosimetria_dados=dosimetria_dados,
        prescricao_dados=resultado.prescricao.model_dump(mode="json"),
        motor_utilizado=motor_utilizado,
        alertas=alertas,
        marcadores_pendentes=marcadores,
        secoes_fallback=list(secoes_fallback or []),
        simulacao_presume_condenacao=resultado.simulacao_presume_condenacao,
        decisoes_crimes=resultado.decisoes_serializaveis(),
    )


def gerar_sentenca_estruturada(
    case_bundle: Dict[str, Any],
    custom_dosimetria_override: Dict[str, Any] | None = None,
    use_ollama_if_available: bool = True,
    *,
    plano: PlanoSentenca | None = None,
) -> SentencaGerada:
    """Gera a sentença penal completa de forma DETERMINÍSTICA (Laya + Python + Base Fechada).

    * Caso real: exige veredicto explícito por crime em ``custom_dosimetria_override["crimes_config"]``
      (``DecisaoJudicialPendenteError``) e os dados essenciais (``DadosInsuficientesError``).
    * Caso demonstrativo (``bundle["is_demo"] is True``): usa os parâmetros de simulação do demo.

    ``use_ollama_if_available`` é mantido por compatibilidade: esta geração não usa LLM, e o
    rótulo ``motor_utilizado`` diz exatamente isso (a redação assistida por LLM é feita pelo
    pipeline seccional).
    """
    resultado = calcular_secoes_deterministicas(case_bundle, custom_dosimetria_override, plano=plano)
    alertas_extras: List[str] = []
    if use_ollama_if_available:
        alertas_extras.append(
            "Geração integral determinística: nenhum trecho foi redigido por LLM (para redação assistida pelo Ollama use o pipeline seccional)."
        )
    return montar_sentenca_gerada(
        resultado,
        resultado.secoes,
        motor_utilizado=MOTOR_DETERMINISTICO,
        alertas_extras=alertas_extras,
    )
