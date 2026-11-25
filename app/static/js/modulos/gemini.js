// SentencIA — Módulo ES: gemini.js (TASK-015)
import { LEGACY_GEMINI_KEY_STORAGE, hooks, state } from "./estado.js?v=2.3.0";
import { $, atualizarControlesConsentimento, closeModal, envioExternoPermitido, escapeHtml, lsGet, lsRemove, openModal, setActiveStation, showToast } from "./dom.js?v=2.3.0";
import { apiJson, handleApiError, postJson } from "./api.js?v=2.3.0";
import { buildDosimetryPayload, garantirDecisaoJudicial } from "./estacao-dosimetria.js?v=2.3.0";
import { avisarResultadoGeracao, renderSentenceOnSheet } from "./estacao-sentenca.js?v=2.3.0";

async function statusCofreGemini() {
  try {
    return await apiJson("/api/secrets/gemini");
  } catch (err) {
    if (err.status === 401) handleApiError(err, { silent: true });
    return null;
  }
}

function textoCofre(st) {
  if (!st) return "Não foi possível consultar o cofre de segredos.";
  const backend = st.backend === "keyring" ? "cofre do sistema operacional" : st.backend === "arquivo" ? "arquivo protegido na pasta de dados" : st.backend || "—";
  return st.configurada ? `Chave configurada (${backend}).` : "Nenhuma chave configurada.";
}

async function atualizarStatusCofre() {
  const st = await statusCofreGemini();
  if ($("geminiVaultStatus")) $("geminiVaultStatus").textContent = textoCofre(st);
  if ($("geminiApiVaultStatus")) {
    $("geminiApiVaultStatus").innerHTML = st && st.configurada
      ? `${escapeHtml(textoCofre(st))} O script será enviado ao Google Gemini somente com o seu consentimento abaixo.`
      : `${escapeHtml(textoCofre(st))} Sem chave, o sistema gera uma <strong>síntese local determinística</strong> (Gemini NÃO utilizado). Configure a chave em <strong>Configurações do Gabinete</strong>.`;
  }
  return st;
}

async function salvarSegredoGemini(chave) {
  return postJson("/api/secrets/gemini", { api_key: chave });
}

function verificarChaveLegada() {
  const antiga = lsGet(LEGACY_GEMINI_KEY_STORAGE);
  if (antiga && antiga.trim()) openModal("modalLegacyKey");
}

async function buildScript() {
  const res = await postJson("/api/gemini/build-script", {
    pseudonimizar: !!$("chkPseudonimizarScript")?.checked,
    densidade: $("selScriptDensidade")?.value || "compacto",
    custom_dosimetria_override: hooks.buildDosimetryPayload(),
  });
  const sc = res.script || {};
  state.lastScriptMarkdown = sc.script_markdown || "";
  return sc;
}

async function copiarScript() {
  if (!hooks.garantirDecisaoJudicial(copiarScript)) return;
  try {
    const sc = await buildScript();
    await navigator.clipboard.writeText(sc.script_markdown || "");
    const kb = Math.round((sc.script_markdown || "").length / 1024);
    showToast(
      `Script Py (${sc.densidade || "compacto"}, ~${kb} KB${sc.pseudonimizado ? ", pseudonimizado" : ", SEM pseudonimização"}) copiado.`,
      sc.pseudonimizado ? "success" : "warn"
    );
  } catch (err) {
    handleApiError(err, { retry: copiarScript });
  }
}

async function verScript() {
  if (!hooks.garantirDecisaoJudicial(verScript)) return;
  try {
    const sc = await buildScript();
    $("scriptPreviewArea").value = sc.script_markdown || "";
    const kb = Math.round((sc.script_markdown || "").length / 1024);
    const alertas = (sc.alertas || []).length ? ` | ${sc.alertas.length} alerta(s): ${sc.alertas.join(" · ")}` : "";
    $("scriptPreviewMeta").textContent = `Densidade: ${sc.densidade || "compacto"} | ~${kb} KB | Pseudonimizado: ${sc.pseudonimizado ? "Sim" : "Não"}${alertas}`;
    openModal("modalScriptPreview");
  } catch (err) {
    handleApiError(err, { retry: verScript });
  }
}

let _ultimoRecomendadoGemini = null;

function _atualizarLabelModeloGemini() {
  const el = $("geminiStatusModelo");
  const sel = $("selGeminiModel");
  if (!el || !sel) return;
  if (sel.disabled) {
    el.textContent = "Modelo que será usado: indisponível (cadastre uma chave)";
    el.className = "gemini-status-item gemini-status-warn";
    return;
  }
  const escolhido = (sel.value || "auto").trim();
  const efetivo = escolhido === "auto" || !escolhido ? (_ultimoRecomendadoGemini || "automático") : escolhido;
  el.textContent = `Modelo que será usado: ${efetivo}`;
  el.className = "gemini-status-item gemini-status-ok";
}

function _atualizarPainelEstadoGemini(cofre, dadosModelos) {
  const stChave = $("geminiStatusChave");
  const btnKey = $("btnGeminiGoSettingsKey");
  const stEnvio = $("geminiStatusEnvio");
  const btnEnvio = $("btnGeminiGoSettingsEnvio");
  const temChave = !!(
    (dadosModelos && dadosModelos.chave_configurada) ||
    (cofre && cofre.configurada)
  );
  const envioOk = envioExternoPermitido();

  if (stChave) {
    if (temChave) {
      const backend = cofre && cofre.backend ? ` (${cofre.backend})` : "";
      stChave.textContent = `✓ Chave configurada${backend}`;
      stChave.className = "gemini-status-item gemini-status-ok";
    } else {
      stChave.textContent = "✗ Chave não configurada";
      stChave.className = "gemini-status-item gemini-status-err";
    }
  }
  if (btnKey) btnKey.hidden = temChave;

  if (stEnvio) {
    if (envioOk) {
      stEnvio.textContent = '✓ "Permitir envio externo" ativado';
      stEnvio.className = "gemini-status-item gemini-status-ok";
    } else {
      stEnvio.textContent = '✗ "Permitir envio externo" desativado';
      stEnvio.className = "gemini-status-item gemini-status-warn";
    }
  }
  if (btnEnvio) btnEnvio.hidden = envioOk;
}

async function carregarModelosGeminiNoModal(cofre) {
  const sel = $("selGeminiModel");
  const stModelo = $("geminiStatusModelo");
  if (!sel) return null;
  sel.disabled = true;
  sel.innerHTML = '<option value="auto">Carregando modelos disponíveis...</option>';
  if (stModelo) {
    stModelo.textContent = "Modelo que será usado: consultando modelos...";
    stModelo.className = "gemini-status-item";
  }
  try {
    const dados = await apiJson("/api/gemini/modelos");
    _atualizarPainelEstadoGemini(cofre, dados);
    _ultimoRecomendadoGemini = dados.recomendado || null;

    if (!dados.chave_configurada) {
      sel.innerHTML = '<option value="">Cadastre uma chave em Configurações para listar modelos</option>';
      sel.disabled = true;
      _atualizarLabelModeloGemini();
      return dados;
    }

    if (dados.erro) {
      sel.innerHTML = '<option value="auto">Automático (erro ao listar modelos)</option>';
      sel.disabled = false;
      if (stModelo) {
        stModelo.textContent = `✗ ${dados.erro}`;
        stModelo.className = "gemini-status-item gemini-status-err";
      }
      return dados;
    }

    const modelos = Array.isArray(dados.modelos) ? dados.modelos : [];
    const rec = dados.recomendado || (modelos[0] && modelos[0].name) || "automático";
    sel.innerHTML = "";

    const optAuto = document.createElement("option");
    optAuto.value = "auto";
    optAuto.textContent = `Automático (recomendado: ${rec})`;
    sel.appendChild(optAuto);

    for (const m of modelos) {
      const nome = typeof m === "string" ? m : (m && m.name) || "";
      if (!nome) continue;
      const opt = document.createElement("option");
      opt.value = nome;
      const disp = typeof m === "object" && m && m.display_name && m.display_name !== nome
        ? `${nome} — ${m.display_name}`
        : nome;
      opt.textContent = disp;
      sel.appendChild(opt);
    }

    sel.value = "auto";
    sel.disabled = false;
    _atualizarLabelModeloGemini();
    return dados;
  } catch (err) {
    _atualizarPainelEstadoGemini(cofre, null);
    sel.innerHTML = '<option value="auto">Automático</option>';
    sel.disabled = !(cofre && cofre.configurada);
    if (stModelo) {
      stModelo.textContent = `✗ Não foi possível listar modelos: ${err && err.message ? err.message : "erro desconhecido"}`;
      stModelo.className = "gemini-status-item gemini-status-err";
    }
    return null;
  }
}

async function testarChaveGemini() {
  const out = $("geminiKeyTestResult");
  const btn = $("btnTestGeminiKey");
  if (!out) return;
  if (btn) btn.disabled = true;
  out.hidden = false;
  out.className = "gemini-test-result";
  out.textContent = "Testando chave (listando modelos disponíveis sem enviar dados do processo)...";
  try {
    const dados = await apiJson("/api/gemini/modelos");
    if (!dados.chave_configurada) {
      out.className = "gemini-test-result gemini-status-warn";
      out.textContent = "✗ Nenhuma chave salva no cofre. Salve a chave antes de testar.";
      return;
    }
    if (dados.erro) {
      out.className = "gemini-test-result gemini-status-err";
      out.textContent = `✗ ${dados.erro}`;
      return;
    }
    const total = Array.isArray(dados.modelos) ? dados.modelos.length : 0;
    const rec = dados.recomendado ? ` (recomendado: ${dados.recomendado})` : "";
    out.className = "gemini-test-result gemini-status-ok";
    out.textContent = `✓ Chave válida: ${total} modelo(s) disponível(is)${rec}.`;
  } catch (err) {
    out.className = "gemini-test-result gemini-status-err";
    out.textContent = `✗ Falha ao testar chave: ${err && err.message ? err.message : String(err)}`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function executarGeminiApi() {
  if (!hooks.garantirDecisaoJudicial(null)) {
    closeModal("modalGeminiApi");
    return;
  }
  const cofre = await statusCofreGemini();
  const consentiu = !!$("chkConsentimentoGemini")?.checked;
  if (cofre && cofre.configurada) {
    if (!envioExternoPermitido()) {
      showToast('O envio externo está desativado. Ative "Permitir envio externo" nas Configurações do Gabinete para usar o Gemini.', "warn");
      return;
    }
    if (!consentiu) {
      showToast("Marque o consentimento de envio externo para enviar o script ao Gemini.", "warn");
      return;
    }
  }
  const botao = $("btnRunGeminiApi");
  botao.disabled = true;
  try {
    const res = await postJson("/api/gemini/generate", {
      model: $("selGeminiModel").value || "auto",
      pseudonimizar: !!$("chkPseudonimizarScript")?.checked,
      densidade: $("selScriptDensidade")?.value || "compacto",
      custom_dosimetria_override: hooks.buildDosimetryPayload(),
      consentimento_envio_externo: consentiu && envioExternoPermitido(),
      auto_blindar_auditoria: !!$("chkAutoBlindarGeminiApi")?.checked,
    });
    renderSentenceOnSheet(res);
    closeModal("modalGeminiApi");
    const tel = res.telemetria || {};
    const usado = !!tel.gemini_utilizado;
    let msgFinal;
    if (usado) {
      const modUsado = tel.modelo_utilizado || tel.modelo_gemini || "Gemini";
      const modSolicitado = tel.modelo_solicitado || "";
      if (tel.motivo_troca || (modSolicitado && modSolicitado !== "auto" && modSolicitado !== modUsado)) {
        msgFinal = `Modelo ${modSolicitado || "solicitado"} indisponível; usado ${modUsado}. Sentença auditada.`;
      } else {
        msgFinal = `Sentença gerada via Gemini (${modUsado}) e auditada.`;
      }
    } else {
      msgFinal = tel.aviso_api || "Gemini NÃO utilizado: minuta gerada por síntese local determinística e auditada.";
    }
    avisarResultadoGeracao(res, msgFinal);
  } catch (err) {
    handleApiError(err);
  } finally {
    botao.disabled = false;
  }
}

async function importarSentencaGemini() {
  const txt = $("textareaPasteGemini").value || "";
  if (!txt.trim()) {
    showToast("Cole o texto da sentença antes de importar.", "warn");
    return;
  }
  // Caso real: sem veredicto por crime a referencia seria uma condenacao presumida.
  if (!hooks.garantirDecisaoJudicial(importarSentencaGemini)) return;
  const botao = $("btnSubmitPasteGemini");
  botao.disabled = true;
  try {
    const corpo = {
      texto_sentenca: txt,
      auto_blindar_auditoria: !!$("chkAutoBlindarGemini").checked,
      custom_dosimetria_override: hooks.buildDosimetryPayload(),
    };
    const res = await postJson("/api/gemini/import-sentence", corpo);
    renderSentenceOnSheet(res);
    closeModal("modalPasteGemini");
    setActiveStation("sentenca");
    avisarResultadoGeracao(res, "Sentença importada, despseudonimizada e auditada.");
  } catch (err) {
    handleApiError(err);
  } finally {
    botao.disabled = false;
  }
}


function bindGeminiEvents() {
  $("btnCopyScriptMd")?.addEventListener("click", copiarScript);
  $("btnOpenScriptModal")?.addEventListener("click", verScript);
  $("btnCopyScriptModal")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("scriptPreviewArea").value || "");
      showToast("Script copiado.", "success");
    } catch (err) {
      showToast(`Não foi possível copiar: ${err.message || err}`, "error");
    }
  });

  $("btnOpenPasteGeminiModal")?.addEventListener("click", () => {
    const info = $("pasteGeminiMapaInfo");
    if (info) {
      info.textContent = "O servidor usa o mapa local do caso ativo para restaurar o texto pseudonimizado.";
    }
    openModal("modalPasteGemini");
  });
  $("btnSubmitPasteGemini")?.addEventListener("click", importarSentencaGemini);

  $("btnOpenGeminiApiModal")?.addEventListener("click", async () => {
    atualizarControlesConsentimento();
    openModal("modalGeminiApi");
    const cofre = await atualizarStatusCofre();
    await carregarModelosGeminiNoModal(cofre);
  });
  $("selGeminiModel")?.addEventListener("change", _atualizarLabelModeloGemini);
  $("btnGeminiGoSettingsKey")?.addEventListener("click", async () => {
    closeModal("modalGeminiApi");
    openModal("modalSettings");
    await atualizarStatusCofre();
    $("setGeminiApiKey")?.focus();
  });
  $("btnGeminiGoSettingsEnvio")?.addEventListener("click", async () => {
    closeModal("modalGeminiApi");
    openModal("modalSettings");
    await atualizarStatusCofre();
    $("setPermitirEnvioExterno")?.focus();
  });
  $("btnRunGeminiApi")?.addEventListener("click", executarGeminiApi);

  $("btnLegacyKeyMigrate")?.addEventListener("click", async () => {
    const antiga = (lsGet(LEGACY_GEMINI_KEY_STORAGE) || "").trim();
    try {
      if (antiga) await salvarSegredoGemini(antiga);
      lsRemove(LEGACY_GEMINI_KEY_STORAGE);
      closeModal("modalLegacyKey");
      showToast("Chave movida para o cofre e apagada do navegador.", "success");
    } catch (err) {
      handleApiError(err);
    }
  });
  $("btnLegacyKeyDiscard")?.addEventListener("click", () => {
    lsRemove(LEGACY_GEMINI_KEY_STORAGE);
    closeModal("modalLegacyKey");
    showToast("Chave apagada do navegador.", "success");
  });
}

export {
  _atualizarLabelModeloGemini,
  _atualizarPainelEstadoGemini,
  _ultimoRecomendadoGemini,
  atualizarStatusCofre,
  bindGeminiEvents,
  buildScript,
  carregarModelosGeminiNoModal,
  copiarScript,
  executarGeminiApi,
  importarSentencaGemini,
  salvarSegredoGemini,
  statusCofreGemini,
  testarChaveGemini,
  textoCofre,
  verScript,
  verificarChaveLegada,
};
