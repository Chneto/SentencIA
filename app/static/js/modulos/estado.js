// SentencIA — Módulo ES: estado.js (TASK-015)

const APP_VERSION = "2.1.0";
const TOKEN_HEADER = "X-SentencIA-Token";
const LEGACY_GEMINI_KEY_STORAGE = "jvdfm_gemini_api_key";
const THEME_STORAGE = "sentencia_theme";
const THEME_MODE_STORAGE = "sentencia_theme_mode";
const DESIGN_STORAGE = "sentencia_design";
const DESIGNS_ATIVOS = new Set(["original", "classico", "moderno", "cockpit"]);
const DESIGNS_VALIDOS = new Set(["original", "classico", "moderno", "cockpit"]);
const MARCADOR_RE = /\[COMPLETAR PELO JU[IÍ]ZO[^\]]*\]/g;

const VETORES_59_META = [
  { key: "culpabilidade", label: "a) Culpabilidade" },
  { key: "antecedentes", label: "b) Antecedentes Criminais" },
  { key: "conduta_social", label: "c) Conduta Social" },
  { key: "personalidade", label: "d) Personalidade do Agente" },
  { key: "motivos", label: "e) Motivos do Crime" },
  { key: "circunstancias", label: "f) Circunstâncias do Crime" },
  { key: "consequencias", label: "g) Consequências do Crime" },
  { key: "comportamento_vitima", label: "h) Comportamento da Vítima" },
];

const VEREDICTO_LABELS = {
  condenar: "Condenar",
  absolver: "Absolver",
  desclassificar: "Desclassificar",
  extinguir: "Extinguir",
};

// Valor = inciso (o motor extrai o inciso do texto; a fundamentação vai em campo próprio).
const ART386_OPTIONS = [
  { value: "VII", label: "Art. 386, VII, CPP — Insuficiência de provas para a condenação" },
  { value: "V", label: "Art. 386, V, CPP — Não existir prova de ter o réu concorrido para a infração" },
  { value: "IV", label: "Art. 386, IV, CPP — Estar provado que o réu não concorreu para a infração" },
  { value: "III", label: "Art. 386, III, CPP — Não constituir o fato infração penal" },
  { value: "II", label: "Art. 386, II, CPP — Não haver prova da existência do fato" },
  { value: "I", label: "Art. 386, I, CPP — Estar provada a inexistência do fato" },
  { value: "VI", label: "Art. 386, VI, CPP — Excludente de ilicitude/culpabilidade ou dúvida fundada" },
];

const ART107_OPTIONS = [
  { value: "IV", label: "Art. 107, IV, CP — Prescrição, decadência ou perempção" },
  { value: "I", label: "Art. 107, I, CP — Morte do agente" },
  { value: "V", label: "Art. 107, V, CP — Renúncia ou perdão aceito" },
  { value: "VI", label: "Art. 107, VI, CP — Retratação admitida em lei" },
];

const TENTATIVA_OPTIONS = [
  { value: "", label: "Crime consumado (sem redução)" },
  { value: "1/3", label: "Tentativa — redução de 1/3 (Art. 14, p.ú., CP)" },
  { value: "1/2", label: "Tentativa — redução de 1/2 (Art. 14, p.ú., CP)" },
  { value: "2/3", label: "Tentativa — redução de 2/3 (Art. 14, p.ú., CP)" },
];

const PENAL_CATALOG_LABELS = {
  art_129_p13_cp: "Lesão Corporal (Art. 129, § 13, CP)",
  art_129_p9_cp: "Lesão Corporal Doméstica (Art. 129, § 9º, CP)",
  art_147_cp: "Ameaça (Art. 147, CP)",
  art_147a_cp: "Perseguição / Stalking (Art. 147-A, CP)",
  art_147b_cp: "Violência Psicológica (Art. 147-B, CP)",
  art_24a_lmp: "Descumprimento de MPU (Art. 24-A, LMP)",
  art_338a_cp: "Descumprimento de MPU (Art. 338-A, CP)",
  art_150_cp: "Violação de Domicílio (Art. 150, CP)",
  art_150_p1_cp: "Violação de Domicílio Qualificada (Art. 150, § 1º, CP)",
  art_21_lcp: "Vias de Fato (Art. 21, LCP)",
  art_138_cp: "Calúnia (Art. 138, CP)",
  art_139_cp: "Difamação (Art. 139, CP)",
  art_140_cp: "Injúria (Art. 140, CP)",
  art_140_p2_cp: "Injúria Real (Art. 140, § 2º, CP)",
  art_163_cp: "Dano Simples (Art. 163, CP)",
  art_163_pu_cp: "Dano Qualificado (Art. 163, p.ú., CP)",
  art_148_p1_cp: "Cárcere Privado Qualificado (Art. 148, § 1º, CP)",
  art_146_cp: "Constrangimento Ilegal (Art. 146, CP)",
  art_158_cp: "Extorsão (Art. 158, CP)",
  art_215a_cp: "Importunação Sexual (Art. 215-A, CP)",
  art_218c_cp: "Divulgação de Cena Íntima (Art. 218-C, CP)",
  art_330_cp: "Desobediência (Art. 330, CP)",
  art_331_cp: "Desacato (Art. 331, CP)",
};

const META_CAMPOS_FORM = {
  numero_processo: "metaNumeroProcesso",
  autos_mpu_apenso: "metaAutosMpu",
  nome_ofendida: "metaNomeOfendida",
  nome_reu: "metaNomeReu",
  data_fato: "metaDataFato",
  data_recebimento_denuncia: "metaDataRecebimento",
  data_intimacao_mpu: "metaDataIntimacaoMpu",
  data_nascimento_reu: "metaDataNascimentoReu",
  valor_dano_moral_pedido_mp: "metaValorDanoMoral",
};

const META_CAMPOS_LABELS = {
  numero_processo: "número do processo",
  autos_mpu_apenso: "autos de MPU em apenso",
  nome_ofendida: "nome da ofendida",
  nome_reu: "nome do réu",
  data_fato: "data do fato",
  data_recebimento_denuncia: "data de recebimento da denúncia",
  data_intimacao_mpu: "data de intimação das MPU",
  data_nascimento_reu: "data de nascimento do réu",
  valor_dano_moral_pedido_mp: "valor de dano moral pedido",
  pedido_dano_moral_expresso_mp: "pedido de dano moral",
  crimes_imputados: "crimes imputados",
};

const FASES_PROGRESSO = {
  aguardando: "Aguardando",
  recebendo_arquivos: "Recebendo arquivos",
  validando_arquivos: "Validando arquivos",
  segmentando_pdf_ocr: "Segmentando o PDF dos autos",
  ocr: "Reconhecimento de texto (OCR) das páginas digitalizadas",
  transcrevendo_midia: "Transcrevendo mídia de audiência",
  classificando_laya: "Classificando as peças (Laya)",
  carregando_autos_do_disco: "Carregando autos da pasta de casos",
  concluido: "Concluído",
};

const state = {
  activeStation: "autos",
  activeCase: null,
  caseKey: "",
  settings: null,
  status: null,
  penalCatalog: [],
  dosimetry: null,
  prescricao: null,
  lastSimulation: null,
  simulacaoPresume: false,
  dadosFaltantes: null,
  crimeConfigs: [],
  migracaoImputacaoPendente: false,
  selectedCrimeIdx: 0,
  destacarPendentes: false,
  lastSentence: null,
  lastAudit: null,
  lastPayload: null,
  lastHtmlPje: "",
  lastTelemetry: null,
  generationId: 0,
  uploadTimer: null,
  pendingRetry: null,
  exportPendente: null,
  sessaoExpirada: false,
};

/**
 * Registro de ganchos (hooks) de coordenação entre camadas superiores.
 * Preenchido uma única vez no ponto de entrada (app.js) para manter o grafo
 * de imports estritamente acíclico (camadas inferiores nunca importam superiores).
 *
 * Catálogo dos hooks registrados em `app.js`:
 * - `applyActiveCase(data, opts)`       — registrado em: `app.js` | chamado por: `configuracoes.js`, `estacao-analise.js`, `estacao-autos.js`, `lote.js`
 * - `loadActiveCase(clearPrev)`         — registrado em: `app.js` | chamado por: reservado para recarga do caso ativo por camadas inferiores
 * - `abrirComplementacao(campos, ...)`  — registrado em: `app.js` | chamado por: `api.js`, `estacao-dosimetria.js`
 * - `exigirVeredictos(crimes, detail)`  — registrado em: `app.js` | chamado por: `api.js`
 * - `garantirDecisaoJudicial(retry)`    — registrado em: `app.js` | chamado por: `estacao-sentenca.js`, `gemini.js`
 * - `buildDosimetryPayload()`           — registrado em: `app.js` | chamado por: `estacao-sentenca.js`, `gemini.js`
 * - `simulateDosimetry(silencioso)`     — registrado em: `app.js` | chamado por: `estacao-analise.js`
 * - `irParaMarcador(idx)`               — registrado em: `app.js` | chamado por: `estacao-analise.js`
 * - `renderSentenceOnSheet(data)`       — registrado em: `app.js` | chamado por: `configuracoes.js`
 * - `reauditarEdicao()`                 — registrado em: `app.js` | chamado por: `folha-editor.js`
 * - `renderUploadStatusTables(status)`  — registrado em: `app.js` | chamado por: `telemetria.js`
 * - `renderDiskBanner()`                — registrado em: `app.js` | chamado por: reservado para atualização do banner de disco
 * - `carregarSettings()`                — registrado em: `app.js` | chamado por: reservado para recarga de configurações
 * - `preencherFormSettings()`           — registrado em: `app.js` | chamado por: reservado para preenchimento do formulário de configurações
 * - `atualizarStatusCofre()`            — registrado em: `app.js` | chamado por: reservado para atualização do status do cofre Gemini
 * - `refreshKnowledge()`                — registrado em: `app.js` | chamado por: reservado para recarga da base de jurisprudência
 * - `refreshStatus()`                   — registrado em: `app.js` | chamado por: reservado para atualização da telemetria
 * - `refreshUploadAndMotores()`         — registrado em: `app.js` | chamado por: reservado para atualização de status de upload/motores
 * - `atualizarOpcoesReset()`            — registrado em: `app.js` | chamado por: reservado para atualização das opções de reinício
 * - `abrirModalLote()`                  — registrado em: `app.js` | chamado por: `temas.js` (menu do logo e Paleta de Comandos)
 */
export const hooks = {};

export {
  APP_VERSION,
  ART107_OPTIONS,
  ART386_OPTIONS,
  DESIGNS_ATIVOS,
  DESIGNS_VALIDOS,
  DESIGN_STORAGE,
  FASES_PROGRESSO,
  LEGACY_GEMINI_KEY_STORAGE,
  MARCADOR_RE,
  META_CAMPOS_FORM,
  META_CAMPOS_LABELS,
  PENAL_CATALOG_LABELS,
  TENTATIVA_OPTIONS,
  THEME_MODE_STORAGE,
  THEME_STORAGE,
  TOKEN_HEADER,
  VEREDICTO_LABELS,
  VETORES_59_META,
  state,
};
