// SentencIA — Módulo ES: dom.js (TASK-015)
import { MARCADOR_RE, PENAL_CATALOG_LABELS, state } from "./estado.js?v=2.3.0";

const $ = (id) => document.getElementById(id);

/* --------------------------------------------------------------------------
   UTILITÁRIOS
   -------------------------------------------------------------------------- */
function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmtDateBr(isoOrBr, vazio = "—") {
  const s = String(isoOrBr || "").trim();
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[3]}/${m[2]}/${m[1]}`;
  return s || vazio;
}

function isoDate(valor) {
  const s = String(valor || "").trim();
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[1]}-${m[2]}-${m[3]}`;
  const br = s.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  if (br) return `${br[3]}-${br[2]}-${br[1]}`;
  return "";
}

function fmtMoeda(valor) {
  return `R$ ${Number(valor || 0).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtPena(p) {
  if (!p) return "—";
  return `${p.anos || 0}a ${p.meses || 0}m ${p.dias || 0}d`;
}

function rotuloCrime(code) {
  if (!code) return "—";
  if (PENAL_CATALOG_LABELS[code]) return PENAL_CATALOG_LABELS[code];
  const cat = state.penalCatalog.find((c) => c.code === code);
  return cat ? `${cat.nome} (${cat.artigo_formatado})` : code;
}

function codigosCatalogo() {
  const set = new Set(Object.keys(PENAL_CATALOG_LABELS));
  state.penalCatalog.forEach((c) => c.code && set.add(c.code));
  return Array.from(set);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function debounce(fn, ms) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

function lsGet(chave) {
  try {
    return window.localStorage.getItem(chave);
  } catch (_) {
    return null;
  }
}

function lsSet(chave, valor) {
  try {
    window.localStorage.setItem(chave, valor);
  } catch (_) {
    // armazenamento indisponível (janela privada / bloqueado): apenas conveniência
  }
}

function lsRemove(chave) {
  try {
    window.localStorage.removeItem(chave);
  } catch (_) {
    // idem
  }
}

function listarMarcadores(texto) {
  return String(texto || "").match(MARCADOR_RE) || [];
}

const TOAST_INFO_DURATION_MS = 6000;
const TOAST_MAX_STACK = 3;

function showToast(msg, kind = "info", duracaoMs = TOAST_INFO_DURATION_MS) {
  const container = $("toastContainer");
  if (!container) return;

  const textoMsg = String(msg ?? "");
  const normalizedKind = kind === "ok" ? "success" : (kind || "info");
  const existente = Array.from(container.children).find(
    (el) => el.dataset.toastMsg === textoMsg && el.dataset.toastKind === normalizedKind
  );
  if (existente) {
    if (typeof existente._renewToast === "function") existente._renewToast(duracaoMs);
    return;
  }

  const isSimulacao = /minuta de simula[cç][aã]o/i.test(textoMsg);
  const isPersistent = normalizedKind === "error" || normalizedKind === "warn" || isSimulacao;

  // Respeita o limite máximo de 3 toasts empilhados:
  // 1) remove informativos mais antigos primeiro;
  // 2) se todos forem persistentes, remove não-simulação antes do aviso de SIMULAÇÃO (que só sai por último).
  while (container.children.length >= TOAST_MAX_STACK) {
    const filhos = Array.from(container.children);
    const oldestInfo = filhos.find((el) => el.dataset.toastPersistent !== "true");
    const oldestNonSimulacao = filhos.find((el) => el.dataset.toastSimulacao !== "true");
    const victim = oldestInfo || oldestNonSimulacao || container.firstElementChild;
    if (!victim) break;
    if (typeof victim._dismissToast === "function") victim._dismissToast();
    else victim.remove();
  }

  const item = document.createElement("div");
  item.className = "toast-item";
  item.dataset.toastMsg = textoMsg;
  item.dataset.toastKind = normalizedKind;
  item.dataset.toastPersistent = isPersistent ? "true" : "false";
  item.dataset.toastSimulacao = isSimulacao ? "true" : "false";
  item.setAttribute("role", normalizedKind === "error" ? "alert" : "status");
  if (normalizedKind === "error") item.style.borderLeftColor = "var(--semantic-danger)";
  else if (normalizedKind === "success") item.style.borderLeftColor = "var(--semantic-emerald)";
  else if (normalizedKind === "warn" || isSimulacao) item.style.borderLeftColor = "var(--semantic-amber)";
  item.innerHTML = `<span>${escapeHtml(textoMsg)}</span><button type="button" class="btn btn-sm" style="padding:2px 7px;">OK</button>`;

  let timerId = null;
  let startedAt = 0;
  let remainingMs = typeof duracaoMs === "number" && duracaoMs > 0 ? duracaoMs : TOAST_INFO_DURATION_MS;
  let paused = false;

  const dismiss = () => {
    if (timerId !== null) {
      clearTimeout(timerId);
      timerId = null;
    }
    item.remove();
  };
  item._dismissToast = dismiss;

  const startTimer = () => {
    if (isPersistent || paused || timerId !== null) return;
    startedAt = Date.now();
    timerId = setTimeout(dismiss, Math.max(0, remainingMs));
  };

  item._renewToast = (novaDuracaoMs) => {
    if (isPersistent) return;
    if (timerId !== null) {
      clearTimeout(timerId);
      timerId = null;
    }
    remainingMs = typeof novaDuracaoMs === "number" && novaDuracaoMs > 0 ? novaDuracaoMs : TOAST_INFO_DURATION_MS;
    if (!paused) startTimer();
  };

  const pauseTimer = () => {
    if (isPersistent) return;
    if (timerId !== null) {
      clearTimeout(timerId);
      timerId = null;
      remainingMs = Math.max(0, remainingMs - (Date.now() - startedAt));
    }
    paused = true;
  };

  const resumeTimer = () => {
    if (isPersistent || !paused) return;
    if (item.matches(":hover") || item.contains(document.activeElement)) return;
    paused = false;
    if (remainingMs <= 0) dismiss();
    else startTimer();
  };

  item.querySelector("button").addEventListener("click", dismiss);
  if (!isPersistent) {
    item.addEventListener("mouseenter", pauseTimer);
    item.addEventListener("mouseleave", resumeTimer);
    item.addEventListener("focusin", pauseTimer);
    item.addEventListener("focusout", () => {
      setTimeout(resumeTimer, 0);
    });
  }

  container.appendChild(item);
  startTimer();
  return item;
}
if (typeof window !== "undefined") {
  try {
    const params = new URLSearchParams(window.location.search || "");
    if (params.get("teste") === "1") {
      window.__sentenciaShowToast = showToast;
    }
  } catch (_) {
    // ambiente sem location.search válido
  }
}

function setActiveStation(stationKey) {
  if (!stationKey) return;
  state.activeStation = stationKey;
  document.querySelectorAll(".station-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.station === stationKey);
  });
  document.querySelectorAll(".stepper-node[data-goto-station]").forEach((node) => {
    node.classList.toggle("active", node.dataset.gotoStation === stationKey);
  });
  document.querySelectorAll(".station-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.stationPanel === stationKey);
  });
  try {
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (_) {
    window.scrollTo(0, 0);
  }
}

function activateSubtab(subtabId) {
  const btn = document.querySelector(`.subtab-btn[data-subtab="${subtabId}"]`);
  if (btn) btn.click();
}

function openModal(id) {
  const el = $(id);
  if (el) el.hidden = false;
}

function closeModal(id) {
  const el = $(id);
  if (el) el.hidden = true;
  if (id === "modalMetadata") state.pendingRetry = null;
  if (id === "modalExportRascunho") state.exportPendente = null;
}

/* --------------------------------------------------------------------------
   HERO RIBBON & CONTADORES DA SIDEBAR
   -------------------------------------------------------------------------- */
function updateHeroRibbon() {
  const c = state.activeCase;
  if (!c || c.empty) {
    $("heroCaseNumber").textContent = "Sem autos carregados";
    $("heroVictimName").textContent = "—";
    $("heroDefendantName").textContent = "—";
    $("heroTotalPenalty").textContent = "—";
    $("badgeCountAutos").textContent = "0";
    $("badgeCountOral").textContent = "0";
    $("badgeCountCrimes").textContent = "0";
    const hintEl = $("step1FooterHint");
    if (hintEl) {
      hintEl.textContent = "Nenhum processo carregado. Envie os autos em PDF ou carregue do disco para iniciar.";
    }
    return;
  }
  const meta = c.metadados || {};
  const numero = c.numero_processo || meta.numero_processo || "";
  $("heroCaseNumber").textContent = (numero || "Processo sem número identificado") + (c.is_demo ? " (demonstração)" : "");
  $("heroVictimName").textContent = c.ofendida || meta.nome_ofendida || "não identificada";
  $("heroDefendantName").textContent = c.reu || meta.nome_reu || "não identificado";

  const pecasCount = (c.pecas || []).length;
  const oralCount = ((c.matriz_oral || {}).depoentes || []).length;
  const crimesCount = (meta.crimes_imputados || []).length;
  $("badgeCountAutos").textContent = String(pecasCount);
  $("badgeCountOral").textContent = String(oralCount);
  $("badgeCountCrimes").textContent = String(crimesCount);

  const hintEl = $("step1FooterHint");
  if (hintEl) {
    hintEl.textContent = `Processo ${numero || "sem número identificado"} com ${pecasCount} peça(s) extraída(s) e ${oralCount} depoimento(s) consolidado(s). Avance para revisar a Análise & Provas.`;
  }

  const d = state.dosimetry;
  if (d) {
    if (d.sem_condenacao) {
      $("heroTotalPenalty").textContent = "Absolvição / Extinção";
    } else {
      $("heroTotalPenalty").textContent = fmtPena(d.pena_total_unificada) + (state.simulacaoPresume ? " (simulação)" : "");
    }
  } else {
    $("heroTotalPenalty").textContent = `${crimesCount} crime(s)`;
  }
}

function envioExternoPermitido() {
  return !!(state.settings && state.settings.permitir_envio_externo);
}

function atualizarControlesConsentimento() {
  const permitido = envioExternoPermitido();
  const motivo = "Envio externo desativado nas Configurações do Gabinete (padrão de segredo de justiça).";
  [
    ["chkConsentimentoMidia", "consentMidiaBox", "consentMidiaHint"],
    ["chkConsentimentoGemini", null, "geminiConsentHint"],
    ["chkConsentimentoDisco", null, null],
  ].forEach(([chkId, boxId, hintId]) => {
    const chk = $(chkId);
    if (!chk) return;
    chk.disabled = !permitido;
    if (!permitido) chk.checked = false;
    if (boxId && $(boxId)) $(boxId).classList.toggle("is-disabled", !permitido);
    if (hintId && $(hintId)) {
      $(hintId).textContent = permitido
        ? chkId === "chkConsentimentoGemini"
          ? "Recomendado manter a pseudonimização ativa na barra de ferramentas."
          : "Sem a marcação, apenas a transcrição local (Whisper) é usada."
        : motivo;
    }
  });
}

export {
  $,
  TOAST_INFO_DURATION_MS,
  TOAST_MAX_STACK,
  activateSubtab,
  atualizarControlesConsentimento,
  closeModal,
  codigosCatalogo,
  debounce,
  envioExternoPermitido,
  escapeHtml,
  fmtDateBr,
  fmtMoeda,
  fmtPena,
  isoDate,
  listarMarcadores,
  lsGet,
  lsRemove,
  lsSet,
  openModal,
  rotuloCrime,
  setActiveStation,
  showToast,
  sleep,
  updateHeroRibbon,
};
