// SentencIA — Módulo ES: telemetria.js (TASK-015)
import { hooks, state } from "./estado.js?v=2.3.0";
import { $, escapeHtml, openModal } from "./dom.js?v=2.3.0";
import { apiJson, handleApiError } from "./api.js?v=2.3.0";
import { renderOcrBanner, renderUploadStatusTables } from "./estacao-autos.js?v=2.3.0";

function txtNum(v, sufixo = "") {
  return v === null || v === undefined || v === "" || Number.isNaN(Number(v)) ? "indisponível" : `${v}${sufixo}`;
}

async function refreshStatus() {
  try {
    const s = await apiJson("/api/status");
    state.status = s;
    if (s.versao) {
      const ver = String(s.versao).trim();
      document.documentElement.setAttribute("data-app-version", ver);
      const sigVer = $("sidebarAuthorVersion");
      if (sigVer) sigVer.textContent = `v${ver}`;
      const aboutVer = $("aboutSentenciaVersion");
      if (aboutVer) aboutVer.textContent = `v${ver}`;
      const telVer = $("telemetryAppVersion");
      if (telVer) telVer.textContent = `SentencIA v${ver}`;
    }
    const hw = s.hardware || {};
    const ol = s.ollama || {};

    const pol = s.politica_vram || {};
    const modoGpu = String(pol.modo_gpu || "normal").toLowerCase();
    const modoGpuLabel = modoGpu === "moderado" ? "Moderado" : "Normal";
    const olPol = pol.ollama || {};
    const filaGpu = pol.fila_gpu || {};
    const aguardandoGpu = Number(filaGpu.aguardando || 0) > 0;

    let label;
    let ok;
    if (aguardandoGpu) {
      label = "GPU: aguardando a placa de vídeo";
      ok = false;
    } else if (ol.online && ol.active_model) {
      label = `Ollama: ${ol.active_model}`;
      ok = true;
    } else if (ol.online) {
      label = "Ollama: modelo não instalado";
      ok = false;
    } else {
      label = "Ollama offline · motor determinístico";
      ok = false;
    }
    $("hwTelemetryLabel").textContent = label;
    $("hwTelemetryDot").className = `telemetry-dot ${ok ? "" : "warn"}`;

    const temNumeros = hw.gpu_available && hw.vram_total_mb !== null && hw.vram_total_mb !== undefined;
    const backendTxt =
      hw.backend === "apple_silicon"
        ? "Apple Silicon (memória unificada; VRAM não medida)"
        : hw.backend === "cuda"
        ? "GPU NVIDIA (CUDA)"
        : "Sem GPU dedicada detectada (CPU)";
    const resumoPoliticaGpu =
      `<br />Modo GPU: <strong>${escapeHtml(modoGpuLabel)}</strong>` +
      ` · Camadas GPU: <strong>${escapeHtml(olPol.num_gpu === null || olPol.num_gpu === undefined ? "auto" : String(olPol.num_gpu))}</strong>` +
      ` · Contexto: <strong>${escapeHtml(txtNum(olPol.num_ctx))}</strong>` +
      (olPol.modelo ? ` · Modelo: <strong>${escapeHtml(olPol.modelo)}</strong>` : "") +
      (aguardandoGpu
        ? `<br /><strong style="color:var(--semantic-amber);">Fila GPU: ${escapeHtml(filaGpu.mensagem || "aguardando a placa de vídeo")} (${filaGpu.aguardando})</strong>`
        : filaGpu.ativa
        ? `<br /><span class="muted">GPU em uso por: ${escapeHtml(filaGpu.ativa)}</span>`
        : "");
    $("telemetryGpuStatus").innerHTML =
      (temNumeros
        ? `<strong>${escapeHtml(hw.gpu_name || "GPU")}</strong><br />VRAM: ${escapeHtml(txtNum(hw.vram_used_mb))} / ${escapeHtml(txtNum(hw.vram_total_mb, " MB"))}${
            hw.vram_percent !== null && hw.vram_percent !== undefined ? ` (${escapeHtml(hw.vram_percent)}%)` : ""
          }<br />Uso da GPU: ${escapeHtml(txtNum(hw.gpu_utilization_percent, "%"))}`
        : `<strong>${escapeHtml(hw.gpu_name || backendTxt)}</strong><br />VRAM: indisponível${hw.detalhe ? `<br /><span class="muted">${escapeHtml(hw.detalhe)}</span>` : ""}`) +
      resumoPoliticaGpu;

    const instalados = (ol.installed_models || []).length;
    $("telemetryOllamaStatus").innerHTML = ol.online
      ? `<strong>Online</strong> <span class="muted mono">${escapeHtml(ol.url || "")}</span><br />${
          ol.active_model
            ? `Modelo ativo: <strong>${escapeHtml(ol.active_model)}</strong>`
            : `Modelo configurado <strong>${escapeHtml(ol.configured_model || "—")}</strong> não instalado (execute <span class="mono">ollama pull ${escapeHtml(
                ol.configured_model || ""
              )}</span>)`
        }<br /><span class="muted">${instalados} modelo(s) instalado(s)</span>`
      : `<strong>Offline</strong>${ol.erro ? ` <span class="muted">(${escapeHtml(ol.erro)})</span>` : ""}<br /><span class="muted">A geração usa somente o motor determinístico local.</span>`;

    const ocr = s.ocr || {};
    const tr = s.transcricao || {};
    $("telemetryOcrStatus").innerHTML = `${
      ocr.motor
        ? `OCR: <strong>${escapeHtml(ocr.motor)}</strong>`
        : "OCR desativado: <strong>páginas digitalizadas não terão texto</strong> <span class=\"muted\">(motor de OCR não instalado — feche o programa e abra pelo 'Iniciar SentencIA')</span>"
    }<br />${
      tr.whisper_local_instalado
        ? `Whisper local: <strong>instalado</strong> (${escapeHtml(tr.whisper_model || "—")}, ${escapeHtml(tr.whisper_device || "auto")})`
        : "Whisper local: <strong>não instalado</strong> <span class=\"muted\">(mídias não serão transcritas sem motor externo autorizado)</span>"
    }`;
    renderOcrBanner();

    const pv = s.privacidade || {};
    const laya = s.laya_system1 || {};
    $("telemetryPrivacyStatus").innerHTML = `Envio externo: <strong>${pv.permitir_envio_externo ? "permitido (com consentimento)" : "bloqueado"}</strong><br />Chave Gemini: ${
      pv.gemini_configurada ? `configurada (cofre: ${escapeHtml(pv.backend_segredos || "—")})` : "não configurada"
    }<br />Laya: ${escapeHtml(laya.status || "—")} <span class="muted">— ${escapeHtml(laya.tipo || "")}</span>${
      (s.ingestao || {}).em_andamento ? "<br /><strong>Ingestão de autos em andamento</strong>" : ""
    }`;
  } catch (err) {
    $("hwTelemetryLabel").textContent = err.status === 401 ? "Sessão expirada" : "Servidor indisponível";
    $("hwTelemetryDot").className = "telemetry-dot warn";
    if (err.status === 401) handleApiError(err, { silent: true });
  }
}

function badgeMotor(status) {
  const s = String(status || "").toUpperCase();
  if (["CONCLUIDO", "OK", "EXECUTADO"].includes(s)) return "badge-success";
  if (["SEM_DADOS", "SIMULACAO", "PARCIAL", "PENDENTE"].includes(s)) return "badge-warn";
  if (["ERRO", "FALHOU"].includes(s)) return "badge-danger";
  return "badge-neutral";
}

async function refreshUploadAndMotores() {
  try {
    const r = await apiJson("/api/cases/upload-status");
    if (r.upload_status) hooks.renderUploadStatusTables(r.upload_status);
    const mot = r.motores_py || [];
    const erro = r.motores_erro;
    const erroHtml = erro
      ? `<div class="alert-banner alert-warn"><div><strong>Motores sem dados suficientes:</strong> ${escapeHtml(erro.detail || "")}</div>${
          erro.erro === "dados_insuficientes"
            ? `<div class="alert-actions"><button type="button" class="btn btn-sm btn-primary" data-action="abrir-metadados" data-campos="${escapeHtml((erro.campos || []).join(","))}">Informar dados</button></div>`
            : ""
        }</div>`
      : "";
    $("motoresPyStatusList").innerHTML =
      erroHtml +
      (mot.length
        ? mot
            .map(
              (m) => `
        <div class="inner-tile" style="margin-bottom:8px;">
          <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
            <strong>${escapeHtml(m.nome)}</strong>
            <span class="badge-tag ${badgeMotor(m.status)}">${escapeHtml(m.status)}</span>
          </div>
          <div class="muted" style="font-size:12px; margin-top:3px;">${escapeHtml(m.resumo_execucao || m.funcao || "")}</div>
        </div>`
            )
            .join("")
        : erro
        ? ""
        : `<p class="muted">Carregue um caso para visualizar a execução dos 5 motores.</p>`);
  } catch (err) {
    if (err.status === 401) handleApiError(err, { silent: true });
  }
}

async function abrirModalTelemetria() {
  openModal("modalTelemetry");
  await Promise.allSettled([refreshStatus(), refreshUploadAndMotores()]);
}

function bindTelemetriaEvents() {
  $("btnOpenTelemetryModal")?.addEventListener("click", abrirModalTelemetria);
}

export {
  abrirModalTelemetria,
  badgeMotor,
  bindTelemetriaEvents,
  refreshStatus,
  refreshUploadAndMotores,
  txtNum,
};
