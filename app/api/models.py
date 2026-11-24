"""Modelos de requisição da API (deduplicados).

Parâmetros de dosimetria são OPCIONAIS: ``None`` significa "não informado pelo juízo" e deixa o
motor aplicar o padrão correto (que, em caso real, nunca valora vetor desfavorável nem inventa
valor de dano moral). Antes, os modelos traziam ``culpabilidade_lesao=True``,
``agravante_art61f=True`` e ``valor_dano_moral=3000.0`` por padrão — dados que o juiz não informou.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import AfterValidator, AliasChoices, BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, model_validator

from app.generation.date_formatter import parse_qualquer_data
from app.generation.dispositivo import RegistroAcessorio
from app.generation.juizo_sobre_provas import DecisaoCautelarSentenca, ElementoProvado
from app.legal_engine.antecedentes import UsoAntecedente
from app.legal_engine.detracao import IntervaloCustodia
from app.legal_engine.condicoes_acao_penal import CondicoesAcaoPenal
from app.legal_engine.dosimetria_calculator import VETORES_ART_59, ValorUnitarioMulta
from app.legal_engine.linha_tempo_prescricao import LinhaTempoPrescricao
from app.legal_engine.penal_types_catalog import list_all_penal_types

_CODIGOS_PENAIS = frozenset(tipo["code"] for tipo in list_all_penal_types())


def _booleano_de_decisao(valor: object) -> object:
    if isinstance(valor, str) and valor.strip().lower() in ("true", "false"):
        return valor.strip().lower() == "true"
    return valor


def _fracao_propria(valor: str) -> str:
    partes = re.fullmatch(r"([0-9]{1,9})\s*/\s*([0-9]{1,9})", valor.strip())
    if partes is None or not 0 < int(partes[1]) < int(partes[2]):
        raise ValueError("informe uma fração positiva menor que 1 (por exemplo, 1/3)")
    return f"{int(partes[1])}/{int(partes[2])}"


def _data_valida(valor: str) -> str:
    if parse_qualquer_data(valor) is None:
        raise ValueError("informe uma data válida")
    return valor


def _codigo_penal(valor: str) -> str:
    codigo = valor.strip()
    if codigo not in _CODIGOS_PENAIS:
        raise ValueError("informe um código do catálogo penal")
    return codigo


def _veredicto_omitido(valor: object) -> object:
    return None if isinstance(valor, str) and not valor.strip() else valor


BooleanoDecisao = Annotated[StrictBool, BeforeValidator(_booleano_de_decisao)]
FracaoPropria = Annotated[str, AfterValidator(_fracao_propria)]
DataValida = Annotated[str, AfterValidator(_data_valida)]
CodigoPenal = Annotated[str, AfterValidator(_codigo_penal)]


class VetorArt59Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    desfavoravel: BooleanoDecisao = False
    possui_transito_em_julgado_anterior: BooleanoDecisao = True
    fundamentacao: str = ""


def _chaves_vetores_validas(vetores: dict[str, VetorArt59Request]) -> dict[str, VetorArt59Request]:
    desconhecidos = vetores.keys() - set(VETORES_ART_59)
    if desconhecidos:
        raise ValueError("vetor desconhecido: " + ", ".join(sorted(desconhecidos)))
    return vetores


class Majorante24ARequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hipotese: Literal["area_exclusao", "dispositivo_monitoracao"]
    fonte_id: str = ""
    trecho: str = ""
    fracao: FracaoPropria | None = None


class CrimeDosimetriaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: CodigoPenal | None = Field(default=None, validation_alias=AliasChoices("codigo", "crime_code"))
    id_imputacao: str | None = None
    linha_tempo_prescricao: LinhaTempoPrescricao | None = None
    data_fato: DataValida | None = None
    veredicto: Annotated[
        Literal["condenar", "absolver", "desclassificar", "extinguir"] | None,
        BeforeValidator(_veredicto_omitido),
    ] = None
    crime_desclassificado: CodigoPenal | None = None
    fundamento_absolvicao_art386: str | None = None
    fundamento_extincao_art107: str | None = None
    fundamentacao: str | None = None
    fundamentacao_absolvicao: str | None = None
    fundamentacao_extincao: str | None = None
    elementos_provados: list[ElementoProvado] | None = None
    vetores_art59: Annotated[dict[str, VetorArt59Request], AfterValidator(_chaves_vetores_validas)] | None = None
    confissao: BooleanoDecisao | None = None
    reincidente: BooleanoDecisao | None = None
    usos_antecedentes: list[UsoAntecedente] | None = None
    agravante_art61f: BooleanoDecisao | None = None
    menoridade_relativa_ou_senilidade: BooleanoDecisao | None = None
    violencia_ou_grave_ameaca: BooleanoDecisao | None = None
    violencia_sexual_contra_mulher: StrictBool | None = None
    condicoes_acao_penal: CondicoesAcaoPenal | None = None
    tentativa_fracao: FracaoPropria | None = None
    fracao_aumento: FracaoPropria | None = None
    dias_multa: int | None = Field(default=None, strict=True, ge=0, le=360)
    valor_unitario_multa: ValorUnitarioMulta | None = None
    enquadramento_ameaca: Literal["caput", "paragrafo_1"] | None = None
    aplicar_aumento_art147a_p1_ii: BooleanoDecisao | None = None
    aplicar_aumento_art141_p3: BooleanoDecisao | None = None
    majorante_art24a_p4: Majorante24ARequest | None = None

    @model_validator(mode="after")
    def validar_identificacao(self) -> CrimeDosimetriaRequest:
        if not (self.codigo or "").strip() and not (self.id_imputacao or "").strip():
            raise ValueError("informe codigo ou id_imputacao")
        return self


class DosimetriaOverride(BaseModel):
    """Campos de decisão compartilhados por todos os caminhos de dosimetria."""

    model_config = ConfigDict(extra="forbid")

    criterio_primeira_fase: Optional[Literal["um_oitavo_intervalo", "um_sexto_minima"]] = None
    culpabilidade_lesao: Optional[BooleanoDecisao] = None
    motivos_lesao: Optional[BooleanoDecisao] = None
    circunstancias_lesao: Optional[BooleanoDecisao] = None
    consequencias_lesao: Optional[BooleanoDecisao] = None
    confissao_art24a: Optional[BooleanoDecisao] = None
    reincidente: Optional[BooleanoDecisao] = None
    agravante_art61f: Optional[BooleanoDecisao] = None
    valor_dano_moral: Optional[float] = Field(default=None, strict=True, ge=0, allow_inf_nan=False)
    crimes_config: Optional[List[CrimeDosimetriaRequest]] = None
    tipo_concurso: Optional[Literal["nenhum", "material", "formal", "continuado"]] = None
    concurso_fracao: Optional[FracaoPropria] = None
    data_sentenca: Optional[str] = None
    reu_menor_21_na_data_fato: Optional[BooleanoDecisao] = None
    reu_maior_70_na_sentenca: Optional[BooleanoDecisao] = None
    dias_prisao_cautelar: Optional[int] = Field(default=None, strict=True, ge=0)
    intervalos_prisao: list[IntervaloCustodia] | None = None
    decisao_cautelar: Optional[DecisaoCautelarSentenca] = None
    decisoes_acessorias: RegistroAcessorio | None = None


class ParametrosDosimetria(DosimetriaOverride):
    custom_dosimetria_override: Optional[DosimetriaOverride] = None

    def to_override(self) -> Dict[str, Any]:
        """Os campos planos explícitos prevalecem sobre os do envelope alternativo."""
        override = self.custom_dosimetria_override.model_dump(exclude_none=True) if self.custom_dosimetria_override else {}
        override.update(self.model_dump(include=set(DosimetriaOverride.model_fields), exclude_none=True))
        return override


class DosimetriaSimulationRequest(ParametrosDosimetria):
    """Payload de ``POST /api/dosimetria/simulate`` (simulação: pode presumir condenação, sinalizado)."""

    # Clientes podem reutilizar o payload da geração; a simulação sempre usa o motor local.
    use_ollama: BooleanoDecisao | None = None


class GenerateSentenceRequest(ParametrosDosimetria):
    """Payload de ``POST /api/sentence/generate`` (geração integral determinística)."""

    use_ollama: bool = True


class ConsentimentoOllamaRequest(BaseModel):
    consentimento_envio_externo: StrictBool = False
    destino_ollama_confirmado: Optional[str] = None


class GenerateSectionalRequest(ParametrosDosimetria, ConsentimentoOllamaRequest):
    """Payload de ``POST /api/sentence/generate-sectional``.

    Mesmo contrato de ``/api/sentence/generate``: campos planos (``crimes_config`` etc.) e/ou
    ``custom_dosimetria_override`` (``to_override()``). ``case_bundle`` é opcional: quando ausente,
    usa o bundle ativo da sessão.
    """

    case_bundle: Optional[Dict[str, Any]] = None
    use_ollama: bool = True
    secoes_habilitadas: Optional[List[str]] = None
    usar_duplo_crivo: bool = True


class RegenerateSectionRequest(ConsentimentoOllamaRequest):
    """Payload de ``POST /api/sentence/regenerate-section`` (``secao`` validada na rota → 400)."""

    secao: str
    revisao_esperada: Optional[str] = None
    instrucoes_cirurgicas: Optional[str] = None
    paragrafo_especifico: Optional[str] = None
    use_ollama: Optional[bool] = None


class ResetCaseRequest(BaseModel):
    """Payload de ``POST /api/cases/reset``.

    - ``producao``: descarta minuta/auditoria/dosimetria/engine/exportações; mantém os autos.
    - ``completo``: também arquiva os arquivos do caso em ``data/cases/_arquivados/<timestamp>.zip``
      (deduplicado por sha256) e esvazia a sessão.
    - ``excluir_definitivamente=True`` (só no modo completo) apaga os arquivos do caso SEM arquivar;
      exige ``confirmar_exclusao_definitiva=True``. ``apagar_arquivo_morto=True`` também apaga
      ``data/cases/_arquivados`` (idem, exige a confirmação).
    """

    mode: Literal["producao", "completo"] = "producao"
    apagar_exportacoes: bool = True
    excluir_definitivamente: bool = False
    apagar_arquivo_morto: bool = False
    confirmar_exclusao_definitiva: bool = False


class BuildGeminiScriptRequest(BaseModel):
    pseudonimizar: bool = True
    densidade: Literal["compacto", "integral"] = "compacto"
    custom_dosimetria_override: Optional[DosimetriaOverride] = None


class GenerateGeminiSentenceRequest(BaseModel):
    """``api_key`` é aceita por compatibilidade, mas o caminho recomendado é o cofre
    (``POST /api/secrets/gemini``). O envio exige ``consentimento_envio_externo=True`` e
    ``settings.permitir_envio_externo``."""

    api_key: str = ""
    model: str = ""
    pseudonimizar: bool = True
    densidade: Literal["compacto", "integral"] = "compacto"
    custom_dosimetria_override: Optional[DosimetriaOverride] = None
    consentimento_envio_externo: StrictBool = False
    auto_blindar_auditoria: bool = False


class ImportExternalSentenceRequest(BaseModel):
    texto_sentenca: str
    auto_blindar_auditoria: bool = False
    custom_dosimetria_override: Optional[DosimetriaOverride] = None
    mapa_reversao: Optional[Dict[str, str]] = None
    mapa_hash: Optional[str] = None


class UpdateAndAuditSentenceRequest(BaseModel):
    texto_completo: str
    revisao_esperada: Optional[str] = None
    custom_dosimetria_override: Optional[DosimetriaOverride] = None
    # Formatação da folha (HTML do editor, sanitizado no servidor) e margens da página.
    texto_html: Optional[str] = None
    formatacao: Optional[Dict[str, Any]] = None


class SaveSentenceFormatRequest(BaseModel):
    """Grava só a formatação (texto inalterado): não reexecuta a auditoria."""

    texto_html: str
    revisao_esperada: str = Field(min_length=1)
    formatacao: Optional[Dict[str, Any]] = None


class CustomPrecedenteRequest(BaseModel):
    referencia: str
    titulo: str
    texto_literal: str
    tipo: str = "PRECEDENTE_TJ"
    tribunal: str = "TJRN"
    materia: str = "Violência Doméstica e Familiar"


class UpdatePrecedenteRequest(BaseModel):
    referencia: Optional[str] = None
    titulo: Optional[str] = None
    texto_literal: Optional[str] = None
    tipo: Optional[str] = None
    tribunal: Optional[str] = None
    materia: Optional[str] = None


class ImportarGabineteRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    formato_versao: int = 1
    itens: List[Dict[str, Any]] = Field(default_factory=list)
    confirmar: bool = False


class StyleSampleRequest(BaseModel):
    titulo: str
    tipo: str = "CONDENATORIA"
    texto_amostra: str


class CategoriaPecaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str = Field(min_length=1, max_length=150)
    categoria: str | None
    manifesto_id: str | None = None


class MetadataOverrideRequest(BaseModel):
    """Payload de ``POST /api/cases/metadata-override``.

    ``metadados``: campos de ``MetadadosProcesso`` informados pelo juízo (ex.: ``data_fato``,
    ``data_recebimento_denuncia`` em ISO ou dd/mm/aaaa, ``crimes_imputados`` lista ou texto separado
    por vírgula, ``nome_ofendida``, ``nome_reu``, ``numero_processo``). Valor ``null``/``""`` remove o
    override daquele campo. ``substituir=True`` descarta os overrides anteriores.
    """

    model_config = ConfigDict(extra="forbid")

    metadados: Dict[str, Any] = Field(default_factory=dict)
    manifesto_id: str | None = None
    substituir: BooleanoDecisao = False


class GeminiSecretRequest(BaseModel):
    api_key: str


class LoadFromDiskRequest(BaseModel):
    """Payload de ``POST /api/cases/load-from-disk``."""

    consentimento_envio_externo: StrictBool = False


class RetencaoArquivoMortoRequest(BaseModel):
    """Payload de ``POST /api/cases/arquivo-morto/retencao``."""

    manter_dias: Optional[int] = None
    manter_ultimos: Optional[int] = None
    simular: bool = True
    confirmar: bool = False


class AbrirProcessoLoteRequest(BaseModel):
    """Payload de ``POST /api/lote/{lote_id}/processos/{pid}/abrir``."""

    confirmar_troca: bool = False


class LimparDemoRequest(BaseModel):
    """Payload de ``POST /api/cases/limpar-demo`` (TASK-031)."""

    simular: bool = True
    confirmar: bool = False


class OllamaPullRequest(BaseModel):
    """Payload de ``POST /api/assistente/ollama/pull`` (TASK-033)."""

    modelo: str
