"""Modelos de requisição da API (deduplicados).

Parâmetros de dosimetria são OPCIONAIS: ``None`` significa "não informado pelo juízo" e deixa o
motor aplicar o padrão correto (que, em caso real, nunca valora vetor desfavorável nem inventa
valor de dano moral). Antes, os modelos traziam ``culpabilidade_lesao=True``,
``agravante_art61f=True`` e ``valor_dano_moral=3000.0`` por padrão — dados que o juiz não informou.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool

#: Campos "planos" de dosimetria aceitos tanto na simulação quanto na geração.
CAMPOS_OVERRIDE_DOSIMETRIA = (
    "criterio_primeira_fase",
    "culpabilidade_lesao",
    "motivos_lesao",
    "circunstancias_lesao",
    "consequencias_lesao",
    "confissao_art24a",
    "reincidente",
    "agravante_art61f",
    "valor_dano_moral",
    "crimes_config",
    "tipo_concurso",
    "concurso_fracao",
    "data_sentenca",
    "reu_menor_21_na_data_fato",
    "reu_maior_70_na_sentenca",
    "dias_prisao_cautelar",
    "decisao_cautelar",
)


class ParametrosDosimetria(BaseModel):
    """Parâmetros de dosimetria/decisão informados pelo juízo (todos opcionais).

    ``crimes_config``: lista de ``{id_imputacao, codigo, veredicto (condenar|absolver|desclassificar|extinguir),
    crime_desclassificado, fundamento_absolvicao_art386, fundamento_extincao_art107, fundamentacao,
    elementos_provados,
    vetores_art59, confissao, reincidente, agravante_art61f, tentativa_fracao, fracao_aumento,
    dias_multa, aplicar_aumento_art147a_p1_ii, enquadramento_ameaca (caput|paragrafo_1),
    majorante_art24a_p4 {hipotese, fonte_id, trecho, fracao}}``.

    ``custom_dosimetria_override`` (dict) é aceito como alternativa; os campos planos informados
    (não ``None``) prevalecem sobre as mesmas chaves do dict.
    """

    model_config = ConfigDict(extra="ignore")

    criterio_primeira_fase: Optional[str] = None
    culpabilidade_lesao: Optional[bool] = None
    motivos_lesao: Optional[bool] = None
    circunstancias_lesao: Optional[bool] = None
    consequencias_lesao: Optional[bool] = None
    confissao_art24a: Optional[bool] = None
    reincidente: Optional[bool] = None
    agravante_art61f: Optional[bool] = None
    valor_dano_moral: Optional[float] = Field(default=None, ge=0)
    crimes_config: Optional[List[Dict[str, Any]]] = None
    tipo_concurso: Optional[Literal["nenhum", "material", "formal", "continuado"]] = None
    concurso_fracao: Optional[str] = None
    data_sentenca: Optional[str] = None
    reu_menor_21_na_data_fato: Optional[bool] = None
    reu_maior_70_na_sentenca: Optional[bool] = None
    dias_prisao_cautelar: Optional[int] = Field(default=None, ge=0)
    decisao_cautelar: Optional[Dict[str, Any]] = None
    custom_dosimetria_override: Optional[Dict[str, Any]] = None

    def to_override(self) -> Dict[str, Any]:
        """Dict de override para os motores, sem as chaves não informadas."""
        override: Dict[str, Any] = dict(self.custom_dosimetria_override or {})
        for campo in CAMPOS_OVERRIDE_DOSIMETRIA:
            valor = getattr(self, campo)
            if valor is None:
                continue
            if isinstance(valor, str) and not valor.strip():
                continue
            override[campo] = valor
        return override


class DosimetriaSimulationRequest(ParametrosDosimetria):
    """Payload de ``POST /api/dosimetria/simulate`` (simulação: pode presumir condenação, sinalizado)."""


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
    custom_dosimetria_override: Optional[Dict[str, Any]] = None


class GenerateGeminiSentenceRequest(BaseModel):
    """``api_key`` é aceita por compatibilidade, mas o caminho recomendado é o cofre
    (``POST /api/secrets/gemini``). O envio exige ``consentimento_envio_externo=True`` e
    ``settings.permitir_envio_externo``."""

    api_key: str = ""
    model: str = ""
    pseudonimizar: bool = True
    densidade: Literal["compacto", "integral"] = "compacto"
    custom_dosimetria_override: Optional[Dict[str, Any]] = None
    consentimento_envio_externo: StrictBool = False
    auto_blindar_auditoria: bool = False


class ImportExternalSentenceRequest(BaseModel):
    texto_sentenca: str
    auto_blindar_auditoria: bool = False
    custom_dosimetria_override: Optional[Dict[str, Any]] = None
    mapa_reversao: Optional[Dict[str, str]] = None
    mapa_hash: Optional[str] = None


class UpdateAndAuditSentenceRequest(BaseModel):
    texto_completo: str
    revisao_esperada: Optional[str] = None
    custom_dosimetria_override: Optional[Dict[str, Any]] = None
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


class MetadataOverrideRequest(BaseModel):
    """Payload de ``POST /api/cases/metadata-override``.

    ``metadados``: campos de ``MetadadosProcesso`` informados pelo juízo (ex.: ``data_fato``,
    ``data_recebimento_denuncia`` em ISO ou dd/mm/aaaa, ``crimes_imputados`` lista ou texto separado
    por vírgula, ``nome_ofendida``, ``nome_reu``, ``numero_processo``). Valor ``null``/``""`` remove o
    override daquele campo. ``substituir=True`` descarta os overrides anteriores.
    """

    metadados: Dict[str, Any] = Field(default_factory=dict)
    substituir: bool = False


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
