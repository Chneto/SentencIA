// SentencIA — Módulo ES sob demanda: tema-matilha.js — Fase B2 (TASK-099)
// Carregado exclusivamente quando data-design="matilha". Zero recursos externos; CSP-safe.
import { postJson } from "./api.js?v=2.3.0";
import { $, lsGet, lsSet, showToast } from "./dom.js?v=2.3.0";
import { state } from "./estado.js?v=2.3.0";

const KEY_EASTER_EGGS = "sentencia_matilha_easter_eggs";
const KEY_SONS = "sentencia_matilha_sons";
const KEY_PUG = "sentencia_matilha_pug";
const KEY_ROTT = "sentencia_matilha_rott";
const IMG_BASE = "/static/img/matilha";
const KONAMI_SEQ = [
  "arrowup", "arrowup", "arrowdown", "arrowdown",
  "arrowleft", "arrowright", "arrowleft", "arrowright",
  "b", "a",
];

let _ativo = false;
let _cleanups = [];
let _logoClicks = 0;
let _logoTimer = null;
let _konamiBuf = [];
let _overlayAtual = null;
let _overlayTimer = null;
let _idleTimer = null;

function _reducedMotion() {
  return Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}

function _easterEggsAtivos() {
  return lsGet(KEY_EASTER_EGGS) !== "0";
}

function _sonsAtivos() {
  // Sons desligados por padrão (regra inegociável da TASK-099).
  return lsGet(KEY_SONS) === "1";
}

export function obterVariantesMatilha() {
  const pugSalvo = lsGet(KEY_PUG) || state.settings?.matilha_pug || "abrico";
  const rottSalvo = lsGet(KEY_ROTT) || state.settings?.matilha_rott || "classico";
  return {
    pug: pugSalvo === "branco_bege" ? "branco_bege" : "abrico",
    rott: rottSalvo === "atletico_feliz" ? "atletico_feliz" : "classico",
  };
}

function _imgPug(pose) {
  const { pug } = obterVariantesMatilha();
  return pug === "branco_bege" ? `pug-branco-${pose}.webp` : `pug-${pose}.webp`;
}

function _imgRott(pose) {
  const { rott } = obterVariantesMatilha();
  return rott === "atletico_feliz" ? `rott-feliz-${pose}.webp` : `rott-${pose}.webp`;
}

// Gravações agrupadas e em série: duas trocas seguidas (pug e rottweiler) não podem chegar fora de ordem ao
// servidor e uma desfazer a outra; sempre se envia o estado final mais recente.
let _timerPersistir = null;
let _gravacaoEmCurso = Promise.resolve();

function _persistirVariantes() {
  const { pug, rott } = obterVariantesMatilha();
  if (state.settings) Object.assign(state.settings, { matilha_pug: pug, matilha_rott: rott });
  clearTimeout(_timerPersistir);
  _timerPersistir = setTimeout(() => {
    _gravacaoEmCurso = _gravacaoEmCurso.then(() => {
      const atual = obterVariantesMatilha();
      return postJson("/api/settings", { matilha_pug: atual.pug, matilha_rott: atual.rott }).catch(() => {
        /* localStorage já preservou a escolha */
      });
    });
  }, 150);
}

function _tocarSom(tipo = "bark") {
  if (!_sonsAtivos() || _reducedMotion()) return;
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    const now = ctx.currentTime;
    if (tipo === "squeak") {
      osc.type = "sine";
      osc.frequency.setValueAtTime(920, now);
      osc.frequency.exponentialRampToValueAtTime(1480, now + 0.08);
      gain.gain.setValueAtTime(0.08, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.14);
      osc.start(now);
      osc.stop(now + 0.15);
    } else {
      osc.type = "triangle";
      osc.frequency.setValueAtTime(340, now);
      osc.frequency.exponentialRampToValueAtTime(170, now + 0.11);
      gain.gain.setValueAtTime(0.09, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.13);
      osc.start(now);
      osc.stop(now + 0.14);
    }
    setTimeout(() => ctx.close?.().catch?.(() => {}), 260);
  } catch (_) {
    /* Web Audio indisponível */
  }
}

function _fecharOverlay() {
  if (_overlayTimer !== null) {
    clearTimeout(_overlayTimer);
    _overlayTimer = null;
  }
  if (_overlayAtual) {
    _overlayAtual.remove();
    _overlayAtual = null;
  }
}

function _mostrarOverlay(conteudoBuilder, duracaoMs = 2400) {
  _fecharOverlay();
  const root = document.createElement("div");
  root.className = "matilha-fx-overlay";
  root.id = "matilhaFxOverlay";
  root.setAttribute("aria-hidden", "true");
  conteudoBuilder(root);
  root.addEventListener("click", _fecharOverlay);
  document.body.appendChild(root);
  _overlayAtual = root;
  _overlayTimer = setTimeout(_fecharOverlay, duracaoMs);
}

function _imgDecorativa(arquivo, { idle = false } = {}) {
  const img = document.createElement("img");
  img.src = `${IMG_BASE}/${arquivo}`;
  img.alt = "";
  img.loading = "lazy";
  img.decoding = "async";
  img.setAttribute("aria-hidden", "true");
  if (idle && !_reducedMotion() && _easterEggsAtivos()) img.classList.add("matilha-idle-alive");
  return img;
}

function _spriteCorrendo(tipo) {
  const el = document.createElement("span");
  el.className = `matilha-sprite matilha-sprite-${tipo}`;
  el.setAttribute("aria-hidden", "true");
  return el;
}

// Encadeia troca de poses com transição suave (ex.: sentado -> carimbando -> feliz)
function _encadearPoses(imgEl, sequenciaArquivos, intervaloMs = 520) {
  if (!imgEl || !sequenciaArquivos.length) return;
  let passo = 0;
  const aplicar = () => {
    if (!_ativo || !imgEl.isConnected) return;
    imgEl.src = `${IMG_BASE}/${sequenciaArquivos[passo]}`;
    imgEl.classList.remove("matilha-pose-swap");
    void imgEl.offsetWidth;
    imgEl.classList.add("matilha-pose-swap");
    passo += 1;
    if (passo < sequenciaArquivos.length && !_reducedMotion()) {
      setTimeout(aplicar, intervaloMs);
    }
  };
  aplicar();
}

// Idle vivo: piscar aleatório a cada 3,5–6,5 s nos mascotes visíveis
function _agendarPiscarIdle() {
  if (_idleTimer !== null) clearTimeout(_idleTimer);
  if (!_ativo || _reducedMotion() || !_easterEggsAtivos()) return;
  const espera = 3500 + Math.floor(Math.random() * 3000);
  _idleTimer = setTimeout(() => {
    if (!_ativo) return;
    document.querySelectorAll(".matilha-idle-alive").forEach((el) => {
      el.classList.add("matilha-blink");
      setTimeout(() => el.classList.remove("matilha-blink"), 240);
    });
    _agendarPiscarIdle();
  }, espera);
}

// Easter egg #1: 3 cliques na logo -> pug inclina cabeça; 7 cliques -> zoomies com sprite sheet (2,2s)
function _acionarLogoClick() {
  if (!_easterEggsAtivos()) return;
  _logoClicks += 1;
  clearTimeout(_logoTimer);
  _logoTimer = setTimeout(() => { _logoClicks = 0; }, 1600);

  if (_logoClicks === 3) {
    _tocarSom("squeak");
    _mostrarOverlay((root) => {
      const b = document.createElement("div");
      b.className = "matilha-fx-banner";
      b.dataset.matilhaEgg = "logo-3";
      const img = _imgDecorativa(_imgPug("feliz"));
      b.appendChild(img);
      _encadearPoses(img, [_imgPug("feliz"), _imgPug("ouvindo")], 380);
      const txt = document.createElement("span");
      txt.textContent = "O pug inclinou a cabeça curioso com o seu clique!";
      b.appendChild(txt);
      root.appendChild(b);
    }, 2000);
  } else if (_logoClicks >= 7) {
    _logoClicks = 0;
    _tocarSom("bark");
    _mostrarOverlay((root) => {
      if (!_reducedMotion()) {
        const runner = document.createElement("div");
        runner.className = "matilha-zoomies-runner";
        runner.dataset.matilhaEgg = "logo-7-zoomies";
        runner.appendChild(_spriteCorrendo("pug"));
        runner.appendChild(_spriteCorrendo("rott"));
        root.appendChild(runner);
      }
      const b = document.createElement("div");
      b.className = "matilha-fx-banner";
      b.dataset.matilhaEgg = "logo-7";
      b.appendChild(_imgDecorativa(_imgPug("zoomies")));
      const txt = document.createElement("span");
      txt.textContent = "Zoomies no gabinete! (Esc ou clique para dispensar)";
      b.appendChild(txt);
      root.appendChild(b);
    }, 2300);
  }
}

// Easter egg #2: Código Konami -> desfile fluido com sprite sheets + petiscos (2,8s)
function _acionarKonami() {
  if (!_easterEggsAtivos()) return;
  _tocarSom("bark");
  _mostrarOverlay((root) => {
    if (!_reducedMotion()) {
      const runner = document.createElement("div");
      runner.className = "matilha-zoomies-runner";
      runner.dataset.matilhaEgg = "konami-desfile";
      runner.appendChild(_spriteCorrendo("pug"));
      runner.appendChild(_spriteCorrendo("rott"));
      root.appendChild(runner);
      for (let i = 0; i < 6; i += 1) {
        const drop = document.createElement("span");
        drop.className = "matilha-treat-drop";
        drop.textContent = "🦴";
        drop.style.left = `${14 + i * 14}%`;
        drop.style.animationDelay = `${i * 0.14}s`;
        root.appendChild(drop);
      }
    }
    const b = document.createElement("div");
    b.className = "matilha-fx-banner";
    b.dataset.matilhaEgg = "konami";
    b.appendChild(_imgDecorativa(_imgRott("zoomies")));
    const txt = document.createElement("span");
    txt.textContent = "Desfile da Matilha! Petiscos liberados para a dupla.";
    b.appendChild(txt);
    root.appendChild(b);
  }, 2800);
}

// Easter egg #8: Paleta de comandos ("au au", "petisco", "quem é um bom garoto?")
export function acionarSegredoPaleta(chave) {
  if (!_ativo || !_easterEggsAtivos()) return;
  _tocarSom(chave === "petisco" ? "squeak" : "bark");
  const mensagens = {
    au_au: { img: _imgPug("feliz"), txt: "Au au! O Pug e o Rottweiler estão prontos para o expediente." },
    petisco: { img: _imgRott("zoomies"), txt: "Petisco entregue! A dupla agradece abanando o rabo." },
    bom_garoto: { img: _imgPug("feliz"), txt: "Os dois abanam o rabo felizes — melhores estagiários do gabinete!" },
  };
  const info = mensagens[chave] || mensagens.au_au;
  _mostrarOverlay((root) => {
    const b = document.createElement("div");
    b.className = "matilha-fx-banner";
    b.dataset.matilhaEgg = `paleta-${chave}`;
    b.appendChild(_imgDecorativa(info.img));
    if (chave === "bom_garoto") b.appendChild(_imgDecorativa(_imgRott("carimbando")));
    const txt = document.createElement("span");
    txt.textContent = info.txt;
    b.appendChild(txt);
    root.appendChild(b);
  }, 2500);
}

function _sincronizarSeloCabecalho() {
  const ribbonRight = document.querySelector(".case-hero-ribbon .ribbon-right, .case-hero-ribbon");
  if (!ribbonRight) return;
  let badge = $("matilhaRibbonBadge");
  if (!badge) {
    badge = document.createElement("span");
    badge.id = "matilhaRibbonBadge";
    badge.className = "matilha-ribbon-badge";
    badge.setAttribute("aria-hidden", "true");
    ribbonRight.prepend(badge);
  }
  const agora = new Date();
  const diaDog = agora.getMonth() === 7 && agora.getDate() === 26;
  const escuro = document.documentElement.getAttribute("data-theme") === "dark";
  const apos18h = agora.getHours() >= 18;
  badge.innerHTML = "";
  if (escuro) {
    badge.appendChild(_imgDecorativa(_imgPug("dormindo"), { idle: true }));
    badge.appendChild(document.createTextNode("A Matilha Dorme"));
  } else if (diaDog) {
    badge.appendChild(_imgDecorativa(_imgPug("feliz"), { idle: true }));
    badge.appendChild(document.createTextNode("Dia do Cão 🎉"));
  } else if (apos18h) {
    badge.appendChild(_imgDecorativa(_imgPug("bocejando"), { idle: true }));
    badge.appendChild(document.createTextNode("Expediente noturno"));
  } else {
    badge.appendChild(_imgDecorativa(_imgRott("guardiao"), { idle: true }));
    badge.appendChild(document.createTextNode("Matilha de guarda"));
  }
}

function _sincronizarEstadoVazio() {
  const dropzone = document.querySelector("#dropZoneAutos, .dropzone, #station-autos .card");
  if (!dropzone) return;
  let card = $("matilhaEmptyCard");
  const semCaso = !state.activeCase || Boolean(state.activeCase.empty);
  if (!semCaso) {
    if (card) card.hidden = true;
    return;
  }
  if (!card) {
    card = document.createElement("div");
    card.id = "matilhaEmptyCard";
    card.className = "matilha-empty-card";
    dropzone.after(card);
  }
  const escuro = document.documentElement.getAttribute("data-theme") === "dark";
  const { pug } = obterVariantesMatilha();
  card.hidden = false;
  card.innerHTML = "";
  const arq = escuro ? "caminha-noite.webp" : (pug === "branco_bege" ? "pug-branco-dormindo.webp" : "caminha-claro.webp");
  card.appendChild(_imgDecorativa(arq, { idle: true }));
  const p = document.createElement("p");
  p.textContent = escuro
    ? "Nenhum processo carregado… a matilha tirou uma soneca."
    : "Nenhum processo carregado… o pug tirou uma soneca na caminha.";
  card.appendChild(p);
}

function _sincronizarAuditoria() {
  const painel = $("sentencePendenciasPanel");
  if (!painel) return;
  let badge = $("matilhaAuditBadge");
  const audit = state.lastAudit;
  if (!audit) {
    if (badge) badge.hidden = true;
    return;
  }
  if (!badge) {
    badge = document.createElement("div");
    badge.id = "matilhaAuditBadge";
    badge.className = "matilha-guard-badge";
    badge.setAttribute("aria-hidden", "true");
    painel.prepend(badge);
  }
  const aprovado = Number(audit.score_confiabilidade ?? 0) >= 100 && audit.aprovado !== false;
  badge.hidden = false;
  badge.innerHTML = "";
  const img = _imgDecorativa(_imgRott(aprovado ? "sentado" : "atento"), { idle: true });
  badge.appendChild(img);
  if (aprovado) {
    // Transição encadeada: sentado -> carimbando -> guardião feliz
    _encadearPoses(img, [_imgRott("sentado"), _imgRott("carimbando")], 450);
  }
  const txt = document.createElement("span");
  txt.textContent = aprovado
    ? "Rottweiler carimbou: auditoria 100% aprovada."
    : "Rottweiler atento: confira os itens apontados na auditoria.";
  badge.appendChild(txt);
}

function _sincronizarConfiguracoes() {
  const sec = $("matilhaSettingsSection");
  if (sec) sec.hidden = !_ativo;
  const chkEggs = $("chkMatilhaEasterEggs");
  const chkSons = $("chkMatilhaSons");
  const selPug = $("selMatilhaPug");
  const selRott = $("selMatilhaRott");
  const { pug, rott } = obterVariantesMatilha();
  if (chkEggs) chkEggs.checked = _easterEggsAtivos();
  if (chkSons) chkSons.checked = _sonsAtivos();
  if (selPug) selPug.value = pug;
  if (selRott) selRott.value = rott;
}

function _reaplicarVisual() {
  _sincronizarSeloCabecalho();
  _sincronizarEstadoVazio();
  _sincronizarAuditoria();
  _sincronizarConfiguracoes();
}

export function ativarTemaMatilha() {
  if (_ativo) {
    _reaplicarVisual();
    return;
  }
  _ativo = true;
  _reaplicarVisual();
  _agendarPiscarIdle();

  const onLogoClick = () => _acionarLogoClick();
  const btnLogo = $("btnAppMenu");
  btnLogo?.addEventListener("click", onLogoClick);
  _cleanups.push(() => btnLogo?.removeEventListener("click", onLogoClick));

  const onKeyDown = (e) => {
    if (e.key === "Escape" && _overlayAtual) {
      _fecharOverlay();
      return;
    }
    const alvo = e.target;
    if (alvo && (alvo.tagName === "INPUT" || alvo.tagName === "TEXTAREA" || alvo.isContentEditable)) return;
    _konamiBuf.push(String(e.key || "").toLowerCase());
    if (_konamiBuf.length > KONAMI_SEQ.length) _konamiBuf.shift();
    if (_konamiBuf.length === KONAMI_SEQ.length && KONAMI_SEQ.every((k, i) => _konamiBuf[i] === k)) {
      _konamiBuf = [];
      _acionarKonami();
    }
  };
  document.addEventListener("keydown", onKeyDown);
  _cleanups.push(() => document.removeEventListener("keydown", onKeyDown));

  const dropzone = document.querySelector("#dropZoneAutos, .dropzone");
  if (dropzone) {
    const onDragEnter = () => dropzone.classList.add("matilha-drop-active");
    const onDragLeave = () => dropzone.classList.remove("matilha-drop-active");
    const onDrop = () => {
      dropzone.classList.remove("matilha-drop-active");
      if (_easterEggsAtivos()) showToast("O pug foi buscar os autos!", "info");
    };
    dropzone.addEventListener("dragenter", onDragEnter);
    dropzone.addEventListener("dragleave", onDragLeave);
    dropzone.addEventListener("drop", onDrop);
    _cleanups.push(() => {
      dropzone.removeEventListener("dragenter", onDragEnter);
      dropzone.removeEventListener("dragleave", onDragLeave);
      dropzone.removeEventListener("drop", onDrop);
    });
  }

  const chkEggs = $("chkMatilhaEasterEggs");
  const chkSons = $("chkMatilhaSons");
  const selPug = $("selMatilhaPug");
  const selRott = $("selMatilhaRott");
  const onEggsChange = () => {
    lsSet(KEY_EASTER_EGGS, chkEggs?.checked ? "1" : "0");
    _reaplicarVisual();
    _agendarPiscarIdle();
  };
  const onSonsChange = () => lsSet(KEY_SONS, chkSons?.checked ? "1" : "0");
  const onPugChange = () => {
    const val = selPug?.value === "branco_bege" ? "branco_bege" : "abrico";
    lsSet(KEY_PUG, val);
    _persistirVariantes();
    _reaplicarVisual();
  };
  const onRottChange = () => {
    const val = selRott?.value === "atletico_feliz" ? "atletico_feliz" : "classico";
    lsSet(KEY_ROTT, val);
    _persistirVariantes();
    _reaplicarVisual();
  };
  chkEggs?.addEventListener("change", onEggsChange);
  chkSons?.addEventListener("change", onSonsChange);
  selPug?.addEventListener("change", onPugChange);
  selRott?.addEventListener("change", onRottChange);
  _cleanups.push(() => {
    chkEggs?.removeEventListener("change", onEggsChange);
    chkSons?.removeEventListener("change", onSonsChange);
    selPug?.removeEventListener("change", onPugChange);
    selRott?.removeEventListener("change", onRottChange);
  });

  if (typeof MutationObserver !== "undefined") {
    const obs = new MutationObserver(() => {
      if (_ativo) _reaplicarVisual();
    });
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    _cleanups.push(() => obs.disconnect());
  }
}

export function desativarTemaMatilha() {
  if (!_ativo) return;
  _ativo = false;
  if (_idleTimer !== null) {
    clearTimeout(_idleTimer);
    _idleTimer = null;
  }
  _fecharOverlay();
  _cleanups.forEach((fn) => fn());
  _cleanups = [];
  $("matilhaRibbonBadge")?.remove();
  $("matilhaEmptyCard")?.remove();
  $("matilhaAuditBadge")?.remove();
  const sec = $("matilhaSettingsSection");
  if (sec) sec.hidden = true;
}
