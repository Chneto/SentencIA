// SentencIA — Módulo ES: configuracoes.js (TASK-015)
import { DESIGNS_VALIDOS, hooks, state } from "./estado.js?v=2.1.0";
import { $, atualizarControlesConsentimento, closeModal, escapeHtml, openModal, showToast } from "./dom.js?v=2.1.0";
import { apiJson, handleApiError, postJson } from "./api.js?v=2.1.0";
import { applyDesign, applyThemeMode } from "./temas.js?v=2.1.0";
import { carregarUltimaSentenca, renderSentenceOnSheet } from "./estacao-sentenca.js?v=2.1.0";
import { atualizarStatusCofre, salvarSegredoGemini, testarChaveGemini } from "./gemini.js?v=2.1.0";

async function carregarSettings() {
  try {
    state.settings = await apiJson("/api/settings");
    if (state.settings && DESIGNS_VALIDOS.has(state.settings.tema_design)) {
      applyDesign(state.settings.tema_design, { persist: false });
    }
    if (state.settings && ["light", "dark", "system"].includes(state.settings.tema_interface)) {
      applyThemeMode(state.settings.tema_interface, { persist: false });
    }
  } catch (err) {
    if (err.status === 401) handleApiError(err, { silent: true });
  }
  // Critério da 1ª fase: padrão do gabinete até o juiz escolher outro nesta página.
  const selCrit = $("selCriterio1Fase");
  const critPadrao = state.settings && state.settings.criterio_primeira_fase;
  if (selCrit && critPadrao && !selCrit.dataset.alteradoPeloJuiz && Array.from(selCrit.options).some((o) => o.value === critPadrao)) {
    selCrit.value = critPadrao;
  }
  atualizarControlesConsentimento();
}

function _formatarRotuloModeloOllama(m) {
  const extras = [];
  if (typeof m.tamanho_gb === "number" && Number.isFinite(m.tamanho_gb)) {
    extras.push(`${m.tamanho_gb.toFixed(1).replace(".", ",")} GB`);
  }
  if (m.quantizacao) extras.push(m.quantizacao);
  else if (m.parametros) extras.push(m.parametros);
  return extras.length ? `${m.nome} (${extras.join(" · ")})` : m.nome;
}

function _sincronizarCampoOutroOllama() {
  const sel = $("setOllamaModelSelect");
  const inp = $("setOllamaModel");
  if (!sel || !inp) return;
  const ehOutro = !sel.disabled && sel.value === "__outro__";
  inp.hidden = !ehOutro;
}

async function carregarModelosOllamaSettings() {
  const sel = $("setOllamaModelSelect");
  const inp = $("setOllamaModel");
  const hint = $("ollamaModelHint");
  if (!sel || !inp) return;

  const configurado = (state.settings && state.settings.ollama_model) || "auto";
  try {
    const res = await apiJson("/api/ollama/modelos");
    if (!res || !res.online) {
      sel.disabled = true;
      sel.innerHTML = `<option value="auto">Ollama não está em execução</option>`;
      inp.hidden = true;
      if (hint) {
        hint.hidden = false;
        hint.innerHTML = `Ollama não está em execução. <button type="button" class="btn btn-sm" id="btnOllamaLinkTelemetria" style="padding:1px 6px; font-size:11px;">Ver telemetria</button>`;
        $("btnOllamaLinkTelemetria")?.addEventListener("click", () => {
          closeModal("modalSettings");
          $("btnOpenTelemetryModal")?.click();
        });
      }
      return;
    }

    sel.disabled = false;
    const instalados = Array.isArray(res.instalados) ? res.instalados : [];
    const rec = res.recomendado || null;
    const autoLabel = rec
      ? `Automático (recomendado: ${rec})`
      : "Automático (nenhum modelo instalado)";

    const opts = [`<option value="auto">${escapeHtml(autoLabel)}</option>`];
    instalados.forEach((m) => {
      opts.push(`<option value="${escapeHtml(m.nome)}">${escapeHtml(_formatarRotuloModeloOllama(m))}</option>`);
    });
    opts.push(`<option value="__outro__">Outro…</option>`);
    sel.innerHTML = opts.join("");

    const nomes = new Set(instalados.map((m) => m.nome));
    if (!configurado || configurado.toLowerCase() === "auto") {
      sel.value = "auto";
      inp.value = "";
    } else if (nomes.has(configurado)) {
      sel.value = configurado;
      inp.value = configurado;
    } else {
      sel.value = "__outro__";
      inp.value = configurado;
    }
    _sincronizarCampoOutroOllama();

    if (hint) {
      if (!instalados.length) {
        hint.hidden = false;
        hint.textContent = "Nenhum modelo instalado no Ollama. A geração usará o motor determinístico.";
      } else if (configurado && configurado.toLowerCase() !== "auto" && !res.configurado_instalado) {
        hint.hidden = false;
        hint.textContent = `O modelo configurado (${configurado}) não está instalado; ao gerar, o sistema usará ${rec || "o motor determinístico"}.`;
      } else {
        hint.hidden = true;
        hint.textContent = "";
      }
    }
  } catch (_) {
    sel.disabled = true;
    sel.innerHTML = `<option value="auto">Ollama não está em execução</option>`;
    inp.hidden = true;
    if (hint) {
      hint.hidden = false;
      hint.innerHTML = `Ollama não está em execução. <button type="button" class="btn btn-sm" id="btnOllamaLinkTelemetria" style="padding:1px 6px; font-size:11px;">Ver telemetria</button>`;
      $("btnOllamaLinkTelemetria")?.addEventListener("click", () => {
        closeModal("modalSettings");
        $("btnOpenTelemetryModal")?.click();
      });
    }
  }
}

function preencherFormSettings() {
  const s = state.settings || {};
  $("setTribunal").value = s.tribunal_nome || "";
  $("setJuizado").value = s.juizado_nome || "";
  $("setComarca").value = s.comarca_nome || "";
  $("setMagistrado").value = s.magistrado_nome || "";
  $("setOllamaUrl").value = s.ollama_url || "";
  $("setOllamaModel").value = s.ollama_model && s.ollama_model !== "auto" ? s.ollama_model : "";
  $("setOllamaNumCtx").value = s.ollama_num_ctx ? String(s.ollama_num_ctx) : "";
  $("setWhisperModel").value = s.whisper_model || "auto";
  $("setWhisperDevice").value = s.whisper_device || "auto";
  if ($("setWhisperPreprocessar")) $("setWhisperPreprocessar").checked = s.whisper_preprocessar !== false;
  if ($("setModoGpu")) $("setModoGpu").value = s.modo_gpu || "normal";
  $("setFonteDocx").value = s.fonte_docx || "";
  if ($("setFormatoReferenciaPje")) $("setFormatoReferenciaPje").value = s.formato_referencia_pje || "id_hifen";
  $("setPastaExportacao").value = s.pasta_exportacao || "";
  $("setSubpastaProcesso").checked = s.subpasta_por_processo !== false;
  $("setPermitirEnvioExterno").checked = !!s.permitir_envio_externo;
  $("setGeminiApiKey").value = "";
}

async function confirmarReset() {
  const completo = !!$("chkResetCompleto").checked;
  const excluir = completo && !!$("chkExcluirDefinitivamente").checked;
  const apagarMorto = completo && !!$("chkApagarArquivoMorto").checked;
  const confirmado = !!$("chkConfirmarExclusao").checked;
  if ((excluir || apagarMorto) && !confirmado) {
    showToast("Confirme que a exclusão definitiva é irreversível (ou desmarque as opções de exclusão).", "warn");
    return;
  }
  const botao = $("btnConfirmResetProduction");
  botao.disabled = true;
  try {
    const res = await postJson("/api/cases/reset", {
      mode: completo ? "completo" : "producao",
      apagar_exportacoes: true,
      excluir_definitivamente: excluir,
      apagar_arquivo_morto: apagarMorto,
      confirmar_exclusao_definitiva: (excluir || apagarMorto) && confirmado,
    });
    state.generationId = res.generation_id || 0;
    hooks.renderSentenceOnSheet({ sentenca: null, auditoria: null, html_pje: "" });
    if (res.active_case) await hooks.applyActiveCase(res.active_case, { clearSentence: true });
    closeModal("modalResetProduction");
    ["chkResetCompleto", "chkExcluirDefinitivamente", "chkApagarArquivoMorto", "chkConfirmarExclusao"].forEach((id) => {
      if ($(id)) $(id).checked = false;
    });
    atualizarOpcoesReset();
    const arquivado = res.cleared && res.cleared.arquivados_em ? ` Arquivo: ${res.cleared.arquivados_em}.` : "";
    const erroArq = res.cleared && res.cleared.erro_arquivamento;
    showToast((res.mensagem || "Sessão reiniciada.") + arquivado, erroArq ? "warn" : "success");
  } catch (err) {
    handleApiError(err);
  } finally {
    botao.disabled = false;
  }
}

function atualizarOpcoesReset() {
  const completo = !!$("chkResetCompleto")?.checked;
  if ($("resetCompletoOpcoes")) $("resetCompletoOpcoes").hidden = !completo;
  const destrutivo = completo && (!!$("chkExcluirDefinitivamente")?.checked || !!$("chkApagarArquivoMorto")?.checked);
  if ($("wrapConfirmarExclusao")) $("wrapConfirmarExclusao").hidden = !destrutivo;
  if (!destrutivo && $("chkConfirmarExclusao")) $("chkConfirmarExclusao").checked = false;
}

async function abrirModalSettings() {
  await carregarSettings();
  preencherFormSettings();
  openModal("modalSettings");
  await Promise.allSettled([atualizarStatusCofre(), carregarModelosOllamaSettings()]);
}

function bindConfiguracoesEvents() {
  $("btnOpenSettingsModal")?.addEventListener("click", abrirModalSettings);

  $("setOllamaModelSelect")?.addEventListener("change", () => {
    _sincronizarCampoOutroOllama();
    if ($("setOllamaModelSelect")?.value === "__outro__") {
      $("setOllamaModel")?.focus();
    }
  });

  // Configurações: atualização PARCIAL (só os campos deste formulário).
  $("formSettings")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const numCtx = parseInt($("setOllamaNumCtx").value || "", 10);
    const selOllama = $("setOllamaModelSelect");
    let ollamaModel = (state.settings && state.settings.ollama_model) || "auto";
    if (selOllama && !selOllama.disabled) {
      if (selOllama.value === "__outro__") {
        ollamaModel = ($("setOllamaModel").value || "").trim() || "auto";
      } else {
        ollamaModel = selOllama.value || "auto";
      }
    }
    const corpo = {
      tribunal_nome: $("setTribunal").value,
      juizado_nome: $("setJuizado").value,
      comarca_nome: $("setComarca").value,
      magistrado_nome: $("setMagistrado").value,
      ollama_url: $("setOllamaUrl").value,
      ollama_model: ollamaModel,
      whisper_model: $("setWhisperModel").value.trim() || "auto",
      whisper_device: $("setWhisperDevice").value || "auto",
      whisper_preprocessar: $("setWhisperPreprocessar")?.checked !== false,
      modo_gpu: $("setModoGpu")?.value || "normal",
      fonte_docx: $("setFonteDocx").value || "Times New Roman",
      formato_referencia_pje: $("setFormatoReferenciaPje")?.value || "id_hifen",
      pasta_exportacao: $("setPastaExportacao").value.trim(),
      subpasta_por_processo: !!$("setSubpastaProcesso").checked,
      permitir_envio_externo: !!$("setPermitirEnvioExterno").checked,
    };
    if (Number.isFinite(numCtx) && numCtx > 0) corpo.ollama_num_ctx = numCtx;
    const formatoAnterior = state.settings?.formato_referencia_pje || "id_hifen";
    try {
      const res = await postJson("/api/settings", corpo);
      state.settings = res.settings || state.settings;
      atualizarControlesConsentimento();
      if (state.lastSentence && (state.settings?.formato_referencia_pje || "id_hifen") !== formatoAnterior) {
        await carregarUltimaSentenca();
      }
      closeModal("modalSettings");
      showToast("Configurações do gabinete salvas.", "success");
      (res.avisos || []).forEach((a) => showToast(a, "warn", 9000));
    } catch (err) {
      if (err.body && (err.body.erro === "configuracao_invalida" || err.body.erro === "pasta_exportacao_invalida")) showToast(err.message, "error");
      else handleApiError(err);
    }
  });

  $("btnSaveGeminiSecret")?.addEventListener("click", async () => {
    const chave = ($("setGeminiApiKey").value || "").trim();
    if (!chave) {
      showToast("Informe a chave da API Gemini.", "warn");
      return;
    }
    try {
      await salvarSegredoGemini(chave);
      $("setGeminiApiKey").value = "";
      await atualizarStatusCofre();
      showToast("Chave Gemini guardada no cofre.", "success");
    } catch (err) {
      handleApiError(err);
    }
  });

  $("btnTestGeminiKey")?.addEventListener("click", testarChaveGemini);

  $("btnDeleteGeminiSecret")?.addEventListener("click", async () => {
    try {
      await apiJson("/api/secrets/gemini", { method: "DELETE" });
      await atualizarStatusCofre();
      if ($("geminiKeyTestResult")) $("geminiKeyTestResult").hidden = true;
      showToast("Chave Gemini removida do cofre.", "success");
    } catch (err) {
      handleApiError(err);
    }
  });

  /* ------------------------------------------------------------------------
     RETENÇÃO DO ARQUIVO MORTO (TASK-013 / POST /api/cases/arquivo-morto/retencao)
     ------------------------------------------------------------------------ */
  let _retencaoSimulacao = null;

  function _formatarBytesRetencao(bytes) {
    const n = Number(bytes) || 0;
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1).replace(".", ",")} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(2).replace(".", ",")} MB`;
    return `${(n / (1024 * 1024 * 1024)).toFixed(2).replace(".", ",")} GB`;
  }

  function _lerPayloadRetencao() {
    const rawDias = ($("retManterDias")?.value || "").trim();
    const rawUltimos = ($("retManterUltimos")?.value || "").trim();
    const parseCampo = (raw) => {
      if (raw === "") return null;
      const n = Number(raw);
      return Number.isFinite(n) ? n : raw;
    };
    const manter_dias = parseCampo(rawDias);
    const manter_ultimos = parseCampo(rawUltimos);
    const chave = `${rawDias}|${rawUltimos}`;
    return { manter_dias, manter_ultimos, chave };
  }

  function _resetConfirmacaoRetencao() {
    const box = $("retencaoConfirmBox");
    const chk = $("chkRetencaoConfirmar");
    const btnExec = $("btnRetencaoConfirmarExec");
    if (box) box.hidden = true;
    if (chk) chk.checked = false;
    if (btnExec) btnExec.disabled = true;
  }

  function _invalidarSimulacaoRetencao() {
    _retencaoSimulacao = null;
    const btnAplicar = $("btnRetencaoAplicar");
    if (btnAplicar) btnAplicar.disabled = true;
    _resetConfirmacaoRetencao();
  }

  function _renderizarListaZips(titulo, lista) {
    if (!lista || !lista.length) {
      return `<div class="muted">${escapeHtml(titulo)}: nenhum</div>`;
    }
    const itens = lista
      .map((nome) => `<code class="mono" style="font-size:11.5px;">${escapeHtml(nome)}</code>`)
      .join(", ");
    return `<div><strong>${escapeHtml(titulo)} (${lista.length}):</strong> ${itens}</div>`;
  }

  function _mostrarErroRetencao(err) {
    const el = $("retencaoResultado");
    const status = err && err.status;
    const msg =
      status === 409
        ? err.message || "Há um carregamento de autos em andamento. Aguarde antes de executar a retenção."
        : status === 422
          ? err.message || "Critérios de retenção inválidos. Informe ao menos um número inteiro maior que zero."
          : (err && err.message) || "Erro ao consultar a retenção do arquivo morto.";
    if (el) {
      el.hidden = false;
      el.innerHTML = `<div style="color:var(--semantic-danger); font-weight:600;">${escapeHtml(msg)}</div>`;
    }
    showToast(msg, status === 409 ? "warn" : "error");
  }

  $("retManterDias")?.addEventListener("input", _invalidarSimulacaoRetencao);
  $("retManterUltimos")?.addEventListener("input", _invalidarSimulacaoRetencao);

  $("btnRetencaoSimular")?.addEventListener("click", async () => {
    const { manter_dias, manter_ultimos, chave } = _lerPayloadRetencao();
    _invalidarSimulacaoRetencao();
    const btnSim = $("btnRetencaoSimular");
    if (btnSim) btnSim.disabled = true;
    try {
      const res = await postJson("/api/cases/arquivo-morto/retencao", {
        manter_dias,
        manter_ultimos,
        simular: true,
        confirmar: false,
      });
      const mantidos = Array.isArray(res.mantidos) ? res.mantidos : [];
      const elegiveis = Array.isArray(res.elegiveis_remocao) ? res.elegiveis_remocao : [];
      const espacoTxt =
        res.bytes_liberados > 0
          ? _formatarBytesRetencao(res.bytes_liberados)
          : elegiveis.length === 0
            ? "0 B"
            : "calculado na aplicação";
      const el = $("retencaoResultado");
      if (el) {
        el.hidden = false;
        el.innerHTML = `
          <div><strong>Simulação concluída:</strong> ${mantidos.length} mantido(s), ${elegiveis.length} elegível(is) para remoção · Espaço liberado estimado: ${escapeHtml(espacoTxt)}.</div>
          ${_renderizarListaZips("Elegíveis para remoção", elegiveis)}
          ${_renderizarListaZips("Mantidos", mantidos)}
        `;
      }
      _retencaoSimulacao = { chave, manter_dias, manter_ultimos, elegiveis };
      const btnAplicar = $("btnRetencaoAplicar");
      if (btnAplicar) btnAplicar.disabled = elegiveis.length === 0;
    } catch (err) {
      _mostrarErroRetencao(err);
    } finally {
      if (btnSim) btnSim.disabled = false;
    }
  });

  $("btnRetencaoAplicar")?.addEventListener("click", () => {
    const atual = _lerPayloadRetencao();
    if (!_retencaoSimulacao || _retencaoSimulacao.chave !== atual.chave || !_retencaoSimulacao.elegiveis.length) {
      _invalidarSimulacaoRetencao();
      showToast("Execute uma simulação com os critérios atuais antes de aplicar a remoção.", "warn");
      return;
    }
    const box = $("retencaoConfirmBox");
    const resumo = $("retencaoConfirmResumo");
    if (resumo) {
      resumo.textContent = `Remover definitivamente ${_retencaoSimulacao.elegiveis.length} arquivamento(s) do arquivo morto?`;
    }
    if ($("chkRetencaoConfirmar")) $("chkRetencaoConfirmar").checked = false;
    if ($("btnRetencaoConfirmarExec")) $("btnRetencaoConfirmarExec").disabled = true;
    if (box) box.hidden = false;
  });

  $("chkRetencaoConfirmar")?.addEventListener("change", (e) => {
    const btnExec = $("btnRetencaoConfirmarExec");
    if (btnExec) btnExec.disabled = !e.target.checked;
  });

  $("btnRetencaoCancelarConfirm")?.addEventListener("click", _resetConfirmacaoRetencao);

  $("btnRetencaoConfirmarExec")?.addEventListener("click", async () => {
    const chk = $("chkRetencaoConfirmar");
    const atual = _lerPayloadRetencao();
    if (!chk || !chk.checked) {
      showToast("Marque a confirmação de que a remoção é definitiva.", "warn");
      return;
    }
    if (!_retencaoSimulacao || _retencaoSimulacao.chave !== atual.chave) {
      _invalidarSimulacaoRetencao();
      showToast("Os critérios mudaram. Simule novamente antes de remover.", "warn");
      return;
    }
    const btnExec = $("btnRetencaoConfirmarExec");
    if (btnExec) btnExec.disabled = true;
    try {
      const res = await postJson("/api/cases/arquivo-morto/retencao", {
        manter_dias: atual.manter_dias,
        manter_ultimos: atual.manter_ultimos,
        simular: false,
        confirmar: true,
      });
      const removidos = Array.isArray(res.removidos) ? res.removidos : [];
      const mantidos = Array.isArray(res.mantidos) ? res.mantidos : [];
      const falhas = Array.isArray(res.falhas) ? res.falhas : [];
      const falhasHtml = falhas.length
        ? `<div style="color:var(--semantic-danger);"><strong>Falhas (${falhas.length}):</strong> ${falhas
            .map((f) => `<code class="mono">${escapeHtml(f.zip)}</code> (${escapeHtml(f.erro)})`)
            .join("; ")}</div>`
        : "";
      const el = $("retencaoResultado");
      if (el) {
        el.hidden = false;
        el.innerHTML = `
          <div><strong>Remoção concluída:</strong> ${removidos.length} removido(s), ${mantidos.length} mantido(s) · Espaço liberado: ${escapeHtml(_formatarBytesRetencao(res.bytes_liberados))} · Entradas limpas no índice: ${Number(res.indice_entradas_removidas) || 0}.</div>
          ${_renderizarListaZips("Removidos", removidos)}
          ${_renderizarListaZips("Mantidos", mantidos)}
          ${falhasHtml}
        `;
      }
      _invalidarSimulacaoRetencao();
      showToast(
        falhas.length
          ? `Retenção aplicada com ${removidos.length} removido(s) e ${falhas.length} falha(s).`
          : `Retenção aplicada: ${removidos.length} arquivamento(s) removido(s).`,
        falhas.length ? "warn" : "success"
      );
    } catch (err) {
      _mostrarErroRetencao(err);
    } finally {
      if (btnExec && $("chkRetencaoConfirmar")?.checked) btnExec.disabled = false;
    }
  });

  $("btnResetProduction")?.addEventListener("click", () => {
    atualizarOpcoesReset();
    openModal("modalResetProduction");
  });
  ["chkResetCompleto", "chkExcluirDefinitivamente", "chkApagarArquivoMorto"].forEach((id) => {
    $(id)?.addEventListener("change", atualizarOpcoesReset);
  });
  $("btnConfirmResetProduction")?.addEventListener("click", confirmarReset);
}

export {
  abrirModalSettings,
  atualizarOpcoesReset,
  bindConfiguracoesEvents,
  carregarSettings,
  confirmarReset,
  preencherFormSettings,
};
