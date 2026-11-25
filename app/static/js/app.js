/* ==========================================================================
   SentencIA v2.0.0 — PONTO DE ENTRADA MODULAR (TASK-015, ES Modules sem bundler)
   Criado por FChNeto

   Grafo de dependências (DAG estritamente acíclico, de cima para baixo):
     Camada 0: modulos/estado.js          (constantes, state, hooks)
     Camada 1: modulos/dom.js             (utilitários DOM, toasts, modais, hero)
     Camada 2: modulos/api.js             (token, apiJson/postJson, erros, jobs)
     Camada 3: modulos/temas.js           (4 desenhos, claro/escuro, menu, Paleta)
     Camada 4: modulos/estacao-autos.js       (ingestão, índice de peças, demo)
               modulos/estacao-analise.js     (provas, oral, metadados, inspetor)
               modulos/estacao-dosimetria.js  (dosimetria, veredictos, prescrição)
               modulos/folha-editor.js        (editor, formatação, margens, localizar)
               modulos/folha-ferramentas.js   (trechos, versões, revisão, barra)
               modulos/estacao-sentenca.js    (folha, auditoria, exportação, geração)
               modulos/gemini.js              (cofre, script Py, modelos, API Gemini)
               modulos/configuracoes.js       (configurações, acervo, retenção, reset)
               modulos/telemetria.js          (status, GPU/Ollama, motores)
     Camada 5: app.js (orquestração, registro de hooks e bootstrap)
   ========================================================================== */

import { APP_VERSION, state, hooks } from "./modulos/estado.js?v=2.4.0";
import { $, setActiveStation, updateHeroRibbon } from "./modulos/dom.js?v=2.4.0";
import { SESSION_TOKEN, apiJson, showSessionExpired, handleApiError } from "./modulos/api.js?v=2.4.0";
import { initNavigationAndModals } from "./modulos/temas.js?v=2.4.0";
import { initDropzones, renderUploadStatusTables, renderDiskBanner, renderPecasIndice, bindAutosEvents } from "./modulos/estacao-autos.js?v=2.4.0";
import { renderAnaliseProvas, abrirComplementacao, bindAnaliseEvents } from "./modulos/estacao-analise.js?v=2.4.0";
import { chaveCaso, initCrimeConfigsFromCase, garantirDecisaoJudicial, exigirVeredictos, renderDosimetryCrimeTabsAndEditor, buildDosimetryPayload, renderDadosBanner, limparDosimetria, simulateDosimetry, bindDosimetriaEvents } from "./modulos/estacao-dosimetria.js?v=2.4.0";
import { initBarraFormatacao } from "./modulos/folha-ferramentas.js?v=2.4.0";
import { irParaMarcador, renderSentenceOnSheet, carregarUltimaSentenca, reauditarEdicao, bindSentencaEvents } from "./modulos/estacao-sentenca.js?v=2.4.0";
import { atualizarStatusCofre, verificarChaveLegada, bindGeminiEvents } from "./modulos/gemini.js?v=2.4.0";
import { abrirModalSettings, carregarSettings, preencherFormSettings, atualizarOpcoesReset, bindConfiguracoesEvents } from "./modulos/configuracoes.js?v=2.4.0";
import { abrirModalAcervo, refreshKnowledge, bindJurisprudenciaEvents } from "./modulos/jurisprudencia.js?v=2.4.0";
import { abrirModalTelemetria, refreshStatus, refreshUploadAndMotores, bindTelemetriaEvents } from "./modulos/telemetria.js?v=2.4.0";
import { abrirModalLote, bindLoteEvents } from "./modulos/lote.js?v=2.4.0";
import { abrirAssistente, bindAssistenteEvents, verificarAssistentePrimeiraExecucao } from "./modulos/assistente.js?v=2.4.0";

async function applyActiveCase(data, opts = {}) {
  if (!data || typeof data !== "object") return;
  const novaChave = chaveCaso(data);
  const mesmoCaso = !!novaChave && novaChave === state.caseKey;
  state.activeCase = data;
  state.caseKey = novaChave;
  initCrimeConfigsFromCase(data, mesmoCaso);
  renderUploadStatusTables(data.upload_status);
  renderDiskBanner();
  renderPecasIndice($("searchPecasInput")?.value || "");
  renderAnaliseProvas();
  renderDosimetryCrimeTabsAndEditor();
  updateHeroRibbon();

  if (opts.clearSentence || data.empty || !mesmoCaso) {
    if (opts.clearSentence || state.lastSentence) {
      renderSentenceOnSheet({ sentenca: null, auditoria: null, html_pje: "" });
    }
  }
  if (!data.empty) {
    await simulateDosimetry(true);
  } else {
    limparDosimetria();
    state.dadosFaltantes = null;
    renderDadosBanner();
    updateHeroRibbon();
  }
}

async function loadActiveCase(clearPreviousSentence = false) {
  try {
    const data = await apiJson("/api/cases/active");
    await applyActiveCase(data, { clearSentence: clearPreviousSentence });
  } catch (err) {
    handleApiError(err);
  }
}

Object.assign(hooks, {
  applyActiveCase, loadActiveCase, abrirComplementacao, exigirVeredictos,
  garantirDecisaoJudicial, buildDosimetryPayload, simulateDosimetry,
  irParaMarcador, renderSentenceOnSheet, reauditarEdicao,
  renderUploadStatusTables, renderDiskBanner, carregarSettings,
  preencherFormSettings, atualizarStatusCofre, refreshKnowledge,
  refreshStatus, refreshUploadAndMotores, atualizarOpcoesReset,
  abrirModalLote, abrirModalSettings, abrirModalAcervo, abrirModalTelemetria, abrirAssistente,
});

async function init() {
  initNavigationAndModals();
  initDropzones();
  bindAutosEvents();
  bindAnaliseEvents();
  bindDosimetriaEvents();
  bindSentencaEvents();
  bindGeminiEvents();
  bindConfiguracoesEvents();
  bindJurisprudenciaEvents();
  bindTelemetriaEvents();
  bindLoteEvents();
  bindAssistenteEvents();
  initBarraFormatacao();
  setActiveStation("autos");
  if (!SESSION_TOKEN) {
    showSessionExpired("Token de sessão ausente: abra o sistema pelo endereço local exibido pelo inicializador (http://127.0.0.1:<porta>/).");
  }
  await carregarSettings();
  await refreshKnowledge();
  await Promise.allSettled([loadActiveCase(), refreshStatus()]);
  if (state.activeCase && !state.activeCase.empty) {
    await carregarUltimaSentenca();
  }
  verificarChaveLegada();
  await verificarAssistentePrimeiraExecucao();
  document.documentElement.setAttribute("data-app-version", APP_VERSION);
  const sigVer = $("sidebarAuthorVersion");
  if (sigVer) sigVer.textContent = `v${APP_VERSION}`;
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
