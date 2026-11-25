// SentencIA — Módulo ES: assistente.js (TASK-033: Assistente de Primeira Execução)
import { hooks, state } from "./estado.js?v=2.3.0";
import { $, closeModal, escapeHtml, openModal, showToast } from "./dom.js?v=2.3.0";
import { apiJson, handleApiError, postJson } from "./api.js?v=2.3.0";

let _pullTimer = null;
let _pullAcompanhado = false;
let _modeloSugeridoAtual = "";

function _pararPollingPull() {
  if (_pullTimer) {
    clearInterval(_pullTimer);
    _pullTimer = null;
  }
}

function _renderOllamaCard(ol, pull) {
  const box = $("assistenteOllamaBox");
  const btnPull = $("btnAssistentePullOllama");
  if (!box) return;
  _modeloSugeridoAtual = String(ol.modelo_sugerido || "gemma4:e2b");

  if (!ol.instalado) {
    box.innerHTML =
      `<p style="margin:0 0 6px;"><strong>Ollama não detectado neste computador.</strong></p>` +
      `<p style="margin:0 0 8px;" class="muted">O SentencIA funciona integralmente sem ele, usando o <strong>motor determinístico local</strong>. ` +
      `Se quiser redação refinada por IA local (100% no seu computador), instale o Ollama pelo site oficial:</p>` +
      `<p style="margin:0;"><a href="${escapeHtml(ol.link_download || "https://ollama.com/download")}" target="_blank" rel="noopener noreferrer" class="btn btn-sm">Baixar Ollama (site oficial)</a></p>`;
    if (btnPull) btnPull.hidden = true;
    return;
  }

  if (!ol.online) {
    box.innerHTML =
      `<p style="margin:0 0 6px;"><strong>Ollama instalado, mas parado no momento.</strong></p>` +
      `<p style="margin:0;" class="muted">Abra o aplicativo Ollama e clique em <em>Verificar novamente</em> para baixar o modelo sugerido ` +
      `(<strong>${escapeHtml(_modeloSugeridoAtual)}</strong> — ${escapeHtml(ol.motivo_sugestao || "")}).</p>`;
    if (btnPull) btnPull.hidden = true;
    return;
  }

  if (ol.modelo_sugerido_instalado) {
    box.innerHTML =
      `<p style="margin:0 0 4px;"><span class="badge-tag badge-success">PRONTO</span> Ollama online com o modelo sugerido <strong>${escapeHtml(_modeloSugeridoAtual)}</strong> já instalado.</p>` +
      `<p style="margin:0;" class="muted">${escapeHtml(ol.motivo_sugestao || "")}</p>`;
    if (btnPull) btnPull.hidden = true;
  } else {
    box.innerHTML =
      `<p style="margin:0 0 4px;"><span class="badge-tag badge-info">OLLAMA ONLINE</span> Modelo sugerido para este computador: <strong>${escapeHtml(_modeloSugeridoAtual)}</strong></p>` +
      `<p style="margin:0;" class="muted">${escapeHtml(ol.motivo_sugestao || "")} O download só começa se você clicar no botão abaixo.</p>`;
    if (btnPull) {
      btnPull.hidden = false;
      btnPull.disabled = !!pull?.ativo;
      btnPull.textContent = pull?.ativo ? "Baixando modelo..." : `Baixar ${_modeloSugeridoAtual}`;
    }
  }
}

function _renderOcrCard(ocr) {
  const box = $("assistenteOcrBox");
  if (!box) return;
  if (ocr.disponivel) {
    box.innerHTML =
      `<p style="margin:0;"><span class="badge-tag badge-success">PRONTO</span> OCR nativo com idioma <strong>Português (pt-BR)</strong> pronto para páginas digitalizadas.</p>`;
    return;
  }
  const cmd = String(ocr.comando_admin || "");
  const passos = (ocr.passos || []).map((p) => `<li>${escapeHtml(p)}</li>`).join("");
  box.innerHTML =
    `<p style="margin:0 0 6px;"><span class="badge-tag badge-warn">ATENÇÃO</span> OCR em Português (pt-BR) não detectado.</p>` +
    `<p style="margin:0 0 6px;" class="muted">PDFs com texto já funcionam normalmente. Para ler páginas escaneadas, siga os passos (o SentencIA <strong>não</strong> executa comandos administrativos sozinho):</p>` +
    (passos ? `<ol style="margin:0 0 8px; padding-left:18px; font-size:12.5px;">${passos}</ol>` : "") +
    (cmd
      ? `<div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">` +
        `<code class="mono" id="assistenteOcrCmd" style="padding:4px 8px; border-radius:6px; background:var(--bg-surface-2); font-size:12px;">${escapeHtml(cmd)}</code>` +
        `<button type="button" class="btn btn-sm" id="btnCopiarCmdOcr">Copiar comando</button>` +
        `</div>`
      : "");
  const btnCopy = $("btnCopiarCmdOcr");
  if (btnCopy && cmd) {
    btnCopy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(cmd);
        showToast("Comando copiado. Execute-o no PowerShell como Administrador.", "success");
      } catch (_) {
        showToast("Selecione e copie o comando manualmente.", "info");
      }
    });
  }
}

function _atualizarUiPull(pull) {
  const wrap = $("assistentePullWrap");
  const bar = $("assistentePullBar");
  const txt = $("assistentePullStatus");
  const btn = $("btnAssistentePullOllama");
  if (!wrap || !bar || !txt) return;
  if (!pull || pull.status === "idle") {
    wrap.hidden = true;
    return;
  }
  wrap.hidden = false;
  const pct = Math.max(0, Math.min(100, Number(pull.percent || 0)));
  bar.style.width = `${pct}%`;
  txt.textContent = `${pull.mensagem || pull.status} (${pct}%)`;
  if (btn) btn.disabled = !!pull.ativo;
  if (!pull.ativo) {
    _pararPollingPull();
    // Só avisa uma vez, ao fim de um download acompanhado nesta tela. O diagnóstico recarregado abaixo devolve o
    // mesmo status "completed"; sem esta trava, cada recarga gerava outro aviso, sem fim.
    if (!_pullAcompanhado) return;
    _pullAcompanhado = false;
    if (pull.status === "completed") {
      showToast(`Modelo ${pull.modelo || ""} baixado com sucesso.`, "success");
      carregarDiagnosticoAssistente();
      hooks.refreshStatus?.();
    } else if (pull.status === "error") {
      showToast(pull.mensagem || "Erro ao baixar modelo Ollama.", "error");
    }
  }
}

async function carregarDiagnosticoAssistente() {
  try {
    const diag = await apiJson("/api/assistente/diagnostico");
    _renderOllamaCard(diag.ollama || {}, diag.pull || {});
    _renderOcrCard(diag.ocr || {});
    _atualizarUiPull(diag.pull || {});
    if (diag.pull?.ativo && !_pullTimer) {
      _iniciarPollingPull();
    }
    return diag;
  } catch (err) {
    handleApiError(err, { silent: true });
    return null;
  }
}

function _iniciarPollingPull() {
  _pararPollingPull();
  _pullAcompanhado = true;
  _pullTimer = setInterval(async () => {
    try {
      const st = await apiJson("/api/assistente/ollama/pull-status");
      _atualizarUiPull(st);
    } catch (_) {
      _pararPollingPull();
    }
  }, 1000);
}

async function abrirAssistente({ automatico = false } = {}) {
  openModal("modalAssistente");
  await carregarDiagnosticoAssistente();
  if (automatico) {
    $("modalAssistente")?.setAttribute("data-abertura", "automatica");
  }
}

async function verificarAssistentePrimeiraExecucao() {
  if (state.settings && state.settings.assistente_concluido) return;
  await abrirAssistente({ automatico: true });
}

async function _concluirAssistente() {
  try {
    const base = state.settings && typeof state.settings === "object" ? { ...state.settings } : {};
    const res = await postJson("/api/settings", { ...base, assistente_concluido: true });
    if (res && res.settings) state.settings = res.settings;
    closeModal("modalAssistente");
    showToast("Assistente concluído. Você pode reabri-lo a qualquer momento pelo menu do logo.", "success");
  } catch (err) {
    handleApiError(err);
  }
}

async function _baixarModeloSugerido() {
  if (!_modeloSugeridoAtual) return;
  const btn = $("btnAssistentePullOllama");
  if (btn) btn.disabled = true;
  try {
    const st = await postJson("/api/assistente/ollama/pull", { modelo: _modeloSugeridoAtual });
    _pullAcompanhado = true;
    _atualizarUiPull(st);
    if (st.ativo) _iniciarPollingPull();
  } catch (err) {
    if (btn) btn.disabled = false;
    handleApiError(err);
  }
}

function bindAssistenteEvents() {
  $("btnAssistenteRefresh")?.addEventListener("click", () => carregarDiagnosticoAssistente());
  $("btnAssistentePullOllama")?.addEventListener("click", _baixarModeloSugerido);
  $("btnAssistenteConcluir")?.addEventListener("click", _concluirAssistente);
}

export { abrirAssistente, bindAssistenteEvents, carregarDiagnosticoAssistente, verificarAssistentePrimeiraExecucao };
