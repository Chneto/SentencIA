// SentencIA — Módulo ES: temas.js (TASK-015)
import { APP_VERSION, DESIGNS_ATIVOS, DESIGN_STORAGE, THEME_MODE_STORAGE, THEME_STORAGE, hooks, state } from "./estado.js?v=2.5.0";
import { $, closeModal, lsGet, lsSet, openModal, setActiveStation, showToast } from "./dom.js?v=2.5.0";
import { handleApiError, postJson } from "./api.js?v=2.5.0";

function initNavigationAndModals() {
  document.querySelectorAll(".station-btn").forEach((btn) => {
    btn.addEventListener("click", () => setActiveStation(btn.dataset.station));
  });

  document.querySelectorAll("[data-goto-station]").forEach((btn) => {
    btn.addEventListener("click", () => setActiveStation(btn.dataset.gotoStation));
  });

  document.querySelectorAll("[data-subtab-group]").forEach((bar) => {
    bar.querySelectorAll(".subtab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const targetId = btn.dataset.subtab;
        bar.querySelectorAll(".subtab-btn").forEach((b) => b.classList.toggle("active", b === btn));
        const panel = bar.closest(".station-panel");
        if (!panel) return;
        panel.querySelectorAll(".subtab-pane").forEach((pane) => {
          pane.classList.toggle("active", pane.id === targetId);
        });
      });
    });
  });

  document.querySelectorAll("[data-close-modal]").forEach((btn) => {
    btn.addEventListener("click", () => closeModal(btn.dataset.closeModal));
  });

  document.querySelectorAll(".modal-backdrop").forEach((backdrop) => {
    backdrop.addEventListener("click", (e) => {
      if (e.target === backdrop && backdrop.id !== "modalLegacyKey") closeModal(backdrop.id);
    });
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    document.querySelectorAll(".modal-backdrop:not([hidden])").forEach((m) => {
      if (m.id !== "modalLegacyKey") closeModal(m.id);
    });
  });

  applyDesign(lsGet(DESIGN_STORAGE) || "original", { persist: false });
  applyThemeMode(lsGet(THEME_MODE_STORAGE) || lsGet(THEME_STORAGE) || "light", { persist: false });
  $("btnToggleTheme")?.addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme") || "light";
    applyThemeMode(cur === "light" ? "dark" : "light", { persist: true });
  });
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onSysChange = () => {
      if ((lsGet(THEME_MODE_STORAGE) || "") === "system") {
        applyThemeMode("system", { persist: false });
      }
    };
    if (mq.addEventListener) mq.addEventListener("change", onSysChange);
  }
  initAppLogoMenu();
  initCommandPalette();
}

async function _persistirPreferenciaTema(campos) {
  try {
    const base = state.settings && typeof state.settings === "object" ? { ...state.settings } : {};
    const res = await postJson("/api/settings", { ...base, ...campos });
    if (res && res.settings) state.settings = res.settings;
  } catch (_) {
    /* espelho localStorage já garante persistência imediata mesmo offline/sem sessão */
  }
}

function _garantirFolhaTema(design) {
  if (!design || design === "original") return;
  const id = `theme-css-${design}`;
  if (document.getElementById(id)) return;
  const href = `/static/css/temas/${design}.css?v=${APP_VERSION}`;
  if (document.querySelector(`link[href^="/static/css/temas/${design}.css"]`)) return;
  const link = document.createElement("link");
  link.id = id;
  link.rel = "stylesheet";
  link.href = href;
  document.head.appendChild(link);
}

function _sincronizarTemasHabilitados() {
  document.querySelectorAll("[data-set-design]").forEach((btn) => {
    const ativo = DESIGNS_ATIVOS.has(btn.dataset.setDesign);
    btn.disabled = !ativo;
    btn.setAttribute("aria-disabled", ativo ? "false" : "true");
    const soon = btn.querySelector(".app-menu-soon");
    if (soon) soon.hidden = ativo;
  });
}

let _modMatilha = null;

function _sincronizarModuloMatilha(design) {
  if (design === "matilha") {
    import(`./tema-matilha.js?v=${APP_VERSION}`)
      .then((m) => {
        _modMatilha = m;
        if (document.documentElement.getAttribute("data-design") === "matilha") {
          m.ativarTemaMatilha();
        }
      })
      .catch(() => {
        /* módulo opcional */
      });
  } else if (_modMatilha && typeof _modMatilha.desativarTemaMatilha === "function") {
    _modMatilha.desativarTemaMatilha();
  }
}

function applyDesign(design, { persist = false } = {}) {
  const d = DESIGNS_ATIVOS.has(design) ? design : "original";
  _garantirFolhaTema(d);
  document.documentElement.setAttribute("data-design", d);
  lsSet(DESIGN_STORAGE, d);
  _sincronizarTemasHabilitados();
  document.querySelectorAll("[data-set-design]").forEach((btn) => {
    btn.setAttribute("aria-checked", btn.dataset.setDesign === d ? "true" : "false");
  });
  _sincronizarModuloMatilha(d);
  if (persist) _persistirPreferenciaTema({ tema_design: d });
}

function _syncThemeToggleButton() {
  const resolved = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  const nextLabel = resolved === "light" ? "Tema Escuro" : "Tema Claro";
  const lbl = $("btnToggleThemeLabel");
  if (lbl) lbl.textContent = nextLabel;
  const btn = $("btnToggleTheme");
  if (btn) {
    btn.setAttribute("aria-label", `Ativar ${nextLabel}`);
    btn.title = `Ativar ${nextLabel}`;
  }
}

function applyThemeMode(mode, { persist = false } = {}) {
  const m = mode === "dark" || mode === "system" ? mode : "light";
  const resolved =
    m === "system"
      ? window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"
      : m;
  document.documentElement.setAttribute("data-theme", resolved);
  lsSet(THEME_MODE_STORAGE, m);
  lsSet(THEME_STORAGE, resolved);
  _syncThemeToggleButton();
  document.querySelectorAll("[data-set-mode]").forEach((btn) => {
    btn.setAttribute("aria-checked", btn.dataset.setMode === m ? "true" : "false");
  });
  if (persist) _persistirPreferenciaTema({ tema_interface: m });
}

if (typeof MutationObserver !== "undefined") {
  new MutationObserver(_syncThemeToggleButton).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
}

function initAppLogoMenu() {
  const anchor = $("brandMenuAnchor");
  const trigger = $("btnAppMenu");
  const menu = $("appMainMenu");
  const temasBtn = $("menuItemTemas");
  const submenu = $("appThemesSubmenu");
  if (!anchor || !trigger || !menu) return;

  const mainItems = () =>
    Array.from(menu.querySelectorAll(':scope > .app-menu-item, :scope > .app-menu-subwrap > .app-menu-item')).filter(
      (el) => !el.disabled && el.getAttribute("aria-disabled") !== "true"
    );
  const subItems = () =>
    submenu
      ? Array.from(submenu.querySelectorAll(".app-menu-item")).filter(
          (el) => !el.disabled && el.getAttribute("aria-disabled") !== "true"
        )
      : [];

  const closeSubmenu = () => {
    if (!submenu) return;
    submenu.hidden = true;
    temasBtn?.setAttribute("aria-expanded", "false");
  };

  const openSubmenu = (focusFirst = false) => {
    if (!submenu) return;
    submenu.hidden = false;
    temasBtn?.setAttribute("aria-expanded", "true");
    if (focusFirst) {
      const items = subItems();
      if (items.length) items[0].focus();
    }
  };

  const closeMenu = (returnFocus = false) => {
    closeSubmenu();
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    if (returnFocus) trigger.focus();
  };

  const openMenu = (focusFirst = false) => {
    menu.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    if (focusFirst) {
      const items = mainItems();
      if (items.length) items[0].focus();
    }
  };

  trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    if (menu.hidden) openMenu(false);
    else closeMenu(false);
  });

  trigger.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openMenu(true);
    } else if (e.key === "Escape" && !menu.hidden) {
      e.preventDefault();
      closeMenu(true);
    }
  });

  temasBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    openSubmenu(false);
  });

  temasBtn?.addEventListener("mouseenter", () => {
    if (!menu.hidden) openSubmenu(false);
  });

  menu.addEventListener("keydown", (e) => {
    const inSub = submenu && !submenu.hidden && submenu.contains(document.activeElement);
    if (inSub) {
      const items = subItems();
      const idx = items.indexOf(document.activeElement);
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (items.length) items[(idx + 1) % items.length].focus();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (items.length) items[(idx - 1 + items.length) % items.length].focus();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        closeSubmenu();
        temasBtn?.focus();
      } else if (e.key === "Escape") {
        e.preventDefault();
        closeMenu(true);
      }
      return;
    }

    const items = mainItems();
    const idx = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (items.length) items[(idx + 1) % items.length].focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (items.length) items[(idx - 1 + items.length) % items.length].focus();
    } else if (e.key === "ArrowRight" && document.activeElement === temasBtn) {
      e.preventDefault();
      openSubmenu(true);
    } else if (e.key === "Escape") {
      e.preventDefault();
      closeMenu(true);
    }
  });

  submenu?.querySelectorAll("[data-set-design]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (btn.disabled) return;
      applyDesign(btn.dataset.setDesign, { persist: true });
      closeMenu(true);
    });
  });

  submenu?.querySelectorAll("[data-set-mode]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      applyThemeMode(btn.dataset.setMode, { persist: true });
      closeMenu(true);
    });
  });

  menu.querySelectorAll("[data-menu-action]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const act = btn.dataset.menuAction;
      closeMenu(false);
      executarAcaoMenuApp(act);
    });
  });

  document.addEventListener("click", (e) => {
    if (!menu.hidden && !anchor.contains(e.target)) {
      closeMenu(false);
    }
  });
}

async function _executarLimpezaDemo() {
  try {
    const previa = await postJson("/api/cases/limpar-demo", { simular: true, confirmar: false });
    const itens = previa.itens || [];
    const temDemoMemoria = !!previa.demo_ativa_em_memoria;
    if (!itens.length && !temDemoMemoria) {
      showToast("Nenhum dado de demonstração ou teste encontrado.", "info");
      return;
    }
    const linhas = [];
    if (temDemoMemoria) linhas.push("• Caso de demonstração ativo na tela");
    for (const it of itens) {
      linhas.push(`• ${it.caminho} (${it.motivo})`);
    }
    const msg =
      "Os seguintes dados de demonstração/teste serão removidos:\n\n" +
      linhas.join("\n") +
      "\n\nAutos reais, exportações, cofre e configurações NÃO serão tocados.\nDeseja continuar?";
    if (!window.confirm(msg)) return;

    const res = await postJson("/api/cases/limpar-demo", { simular: false, confirmar: true });
    if (hooks.loadActiveCase) await hooks.loadActiveCase(true);
    const total = Number(res.total || 0) + (res.demo_ativa_em_memoria ? 1 : 0);
    showToast(`Dados de demonstração limpos (${total} item(ns)).`, "success");
  } catch (err) {
    handleApiError(err);
  }
}

function executarAcaoMenuApp(act) {
  switch (act) {
    case "settings":
      hooks.abrirModalSettings?.();
      break;
    case "acervo":
      hooks.abrirModalAcervo?.();
      break;
    case "telemetry":
      hooks.abrirModalTelemetria?.();
      break;
    case "demo":
      $("btnLoadDemo")?.click();
      break;
    case "limpar-demo":
      _executarLimpezaDemo();
      break;
    case "lote":
      hooks.abrirModalLote?.();
      break;
    case "reset":
      $("btnResetProduction")?.click();
      break;
    case "palette":
      openCommandPalette();
      break;
    case "reload":
      window.location.reload();
      break;
    case "about":
      openModal("modalAboutSentencia");
      break;
    case "assistente":
      hooks.abrirAssistente?.({ automatico: false });
      break;
    default:
      break;
  }
}

const _COMANDOS_PALETA = [
  { label: "Tema: Original (Liquid Glass)", hint: "Tema", run: () => applyDesign("original", { persist: true }) },
  { label: "Tema: Gabinete Clássico", hint: "Tema", run: () => applyDesign("classico", { persist: true }) },
  { label: "Tema: Estúdio Moderno", hint: "Tema", run: () => applyDesign("moderno", { persist: true }) },
  { label: "Tema: Cockpit", hint: "Tema", run: () => applyDesign("cockpit", { persist: true }) },
  { label: "Tema: Matilha (Pug & Rottweiler)", hint: "Tema", run: () => applyDesign("matilha", { persist: true }) },
  { label: "Aparência: Claro", hint: "Tema", run: () => applyThemeMode("light", { persist: true }) },
  { label: "Aparência: Escuro", hint: "Tema", run: () => applyThemeMode("dark", { persist: true }) },
  { label: "Aparência: Seguir o sistema", hint: "Tema", run: () => applyThemeMode("system", { persist: true }) },
  { label: "Configurações…", hint: "Gabinete", run: () => executarAcaoMenuApp("settings") },
  { label: "Acervo de jurisprudência…", hint: "Base", run: () => executarAcaoMenuApp("acervo") },
  { label: "Telemetria e status…", hint: "Sistema", run: () => executarAcaoMenuApp("telemetry") },
  { label: "Carregar caso de demonstração", hint: "Autos", run: () => executarAcaoMenuApp("demo") },
  { label: "Pré-análise em lote…", hint: "Autos", run: () => executarAcaoMenuApp("lote") },
  { label: "Nova sentença…", hint: "Reset", run: () => executarAcaoMenuApp("reset") },
  { label: "Recarregar interface", hint: "Sistema", run: () => executarAcaoMenuApp("reload") },
  { label: "Sobre o SentencIA", hint: "Ajuda", run: () => executarAcaoMenuApp("about") },
];

function _comandosSecretosMatilha(q) {
  if (document.documentElement.getAttribute("data-design") !== "matilha") return [];
  const norm = (q || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
  if (!norm) return [];
  const secretos = [];
  if (norm.includes("au au") || norm === "au") {
    secretos.push({
      label: "Matilha: Au au! (Pug e Rottweiler cumprimentam o gabinete)",
      hint: "Segredo",
      run: () => _modMatilha?.acionarSegredoPaleta?.("au_au"),
    });
  }
  if (norm.includes("petisco")) {
    secretos.push({
      label: "Matilha: Distribuir petiscos para a dupla",
      hint: "Segredo",
      run: () => _modMatilha?.acionarSegredoPaleta?.("petisco"),
    });
  }
  if (norm.includes("bom garoto")) {
    secretos.push({
      label: "Matilha: Quem é um bom garoto? (os dois abanam!)",
      hint: "Segredo",
      run: () => _modMatilha?.acionarSegredoPaleta?.("bom_garoto"),
    });
  }
  return secretos;
}

function renderCommandPalette(filtro = "") {
  const list = $("cmdPaletteList");
  if (!list) return;
  const q = (filtro || "").trim().toLowerCase();
  const filtrados = [
    ..._comandosSecretosMatilha(q),
    ..._COMANDOS_PALETA.filter((c) => !q || c.label.toLowerCase().includes(q) || c.hint.toLowerCase().includes(q)),
  ];
  list.innerHTML = "";
  for (const cmd of filtrados) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "cmd-palette-item";
    btn.setAttribute("role", "option");
    if (cmd.disabled) btn.disabled = true;
    const spanLbl = document.createElement("span");
    spanLbl.textContent = cmd.label;
    const spanHint = document.createElement("span");
    spanHint.className = "app-menu-shortcut";
    spanHint.textContent = cmd.hint;
    btn.appendChild(spanLbl);
    btn.appendChild(spanHint);
    btn.addEventListener("click", () => {
      if (cmd.disabled) return;
      closeModal("modalCommandPalette");
      cmd.run();
    });
    list.appendChild(btn);
  }
}

function openCommandPalette() {
  const input = $("cmdPaletteInput");
  if (input) input.value = "";
  renderCommandPalette("");
  openModal("modalCommandPalette");
  if (input) setTimeout(() => input.focus(), 20);
}

function initCommandPalette() {
  const input = $("cmdPaletteInput");
  input?.addEventListener("input", () => renderCommandPalette(input.value));
  input?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const first = $("cmdPaletteList")?.querySelector(".cmd-palette-item:not(:disabled)");
      if (first) {
        e.preventDefault();
        first.click();
      }
    }
  });
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "k") {
      e.preventDefault();
      const modal = $("modalCommandPalette");
      if (modal && !modal.hidden) closeModal("modalCommandPalette");
      else openCommandPalette();
    }
  });
}

export {
  _COMANDOS_PALETA,
  _garantirFolhaTema,
  _persistirPreferenciaTema,
  _sincronizarTemasHabilitados,
  _syncThemeToggleButton,
  applyDesign,
  applyThemeMode,
  executarAcaoMenuApp,
  initAppLogoMenu,
  initCommandPalette,
  initNavigationAndModals,
  openCommandPalette,
  renderCommandPalette,
};
