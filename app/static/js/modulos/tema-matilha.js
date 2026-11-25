// SentencIA — Módulo ES sob demanda: tema-matilha.js — Matilha 2.0 (TASK-099, TASK-103: cães vetoriais)
// Carregado exclusivamente quando data-design="matilha". Zero recursos externos; CSP-safe.
import { postJson } from "./api.js?v=2.5.0";
import { $, lsGet, lsSet, showToast } from "./dom.js?v=2.5.0";
import { state } from "./estado.js?v=2.5.0";
import { criarCao } from "./matilha-caes-svg.js?v=2.5.0";
import { acaoMascote, iniciarMascotes, pararMascotes, reagirMascotes } from "./matilha-mascotes.js?v=2.5.0";

const KEY_EASTER_EGGS = "sentencia_matilha_easter_eggs";
const KEY_SONS = "sentencia_matilha_sons";
const KEY_PUG = "sentencia_matilha_pug";
const KEY_ROTT = "sentencia_matilha_rott";
const KEY_MASCOTES = "sentencia_matilha_mascotes";
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
let _auditoriaAprovada = false;
const _caesEstaticos = new Set();

function _reducedMotion() {
  return Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}

function _easterEggsAtivos() {
  return lsGet(KEY_EASTER_EGGS) !== "0";
}

function _mascotesAtivos() {
  return lsGet(KEY_MASCOTES) !== "0";
}

function _sincronizarMascotes() {
  if (!_ativo || !_easterEggsAtivos() || !_mascotesAtivos()) {
    pararMascotes();
    return;
  }
  iniciarMascotes({
    variantes: obterVariantesMatilha, sons: _sonsAtivos, tocarSom: _tocarSom,
    aoLimpar: () => showToast("Obrigado por limpar! O gabinete agradece.", "success"),
  });
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

// Cão vetorial parado numa pose (selos, estado vazio, auditoria, easter eggs), na variante escolhida.
function _cao(tipo, pose, tamanho = 36) {
  const api = criarCao(tipo, obterVariantesMatilha()[tipo], { tamanho });
  api.definirPose(pose);
  api.el.classList.add("matilha-cao-estatico");
  _caesEstaticos.add(api);
  return api.el;
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

// Idle vivo: os cães dos selos piscam a cada 3,5–6,5 s.
function _agendarPiscarIdle() {
  if (_idleTimer !== null) clearTimeout(_idleTimer);
  if (!_ativo || !_easterEggsAtivos()) return;
  const espera = 3500 + Math.floor(Math.random() * 3000);
  _idleTimer = setTimeout(() => {
    if (!_ativo) return;
    _caesEstaticos.forEach((api) => {
      if (api.el.isConnected) api.piscar();
      else _caesEstaticos.delete(api);
    });
    _agendarPiscarIdle();
  }, espera);
}

function _corredores(root, egg) {
  const runner = document.createElement("div");
  runner.className = "matilha-zoomies-runner";
  runner.dataset.matilhaEgg = egg;
  runner.append(_cao("pug", "correndo", 56), _cao("rott", "correndo", 56));
  root.appendChild(runner);
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
      b.appendChild(_cao("pug", "atento", 44));
      const txt = document.createElement("span");
      txt.textContent = "O pug inclinou a cabeça curioso com o seu clique!";
      b.appendChild(txt);
      root.appendChild(b);
    }, 2000);
  } else if (_logoClicks >= 7) {
    _logoClicks = 0;
    _tocarSom("bark");
    _mostrarOverlay((root) => {
      _corredores(root, "logo-7-zoomies");
      const b = document.createElement("div");
      b.className = "matilha-fx-banner";
      b.dataset.matilhaEgg = "logo-7";
      b.appendChild(_cao("pug", "feliz", 44));
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
    _corredores(root, "konami-desfile");
    {
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
    b.appendChild(_cao("rott", "feliz", 44));
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
    au_au: { cao: ["pug", "latindo"], txt: "Au au! O Pug e o Rottweiler estão prontos para o expediente." },
    petisco: { cao: ["rott", "comendo"], txt: "Petisco entregue! A dupla agradece abanando o rabo." },
    bom_garoto: { cao: ["pug", "feliz"], txt: "Os dois abanam o rabo felizes — melhores estagiários do gabinete!" },
  };
  const info = mensagens[chave] || mensagens.au_au;
  // Os mascotes do rodapé reagem ao segredo: petisco → vão comer; bom garoto → abanam o rabo.
  const reacao = { petisco: "comer", bom_garoto: "carinho" }[chave];
  if (reacao) ["pug", "rott"].forEach((cao) => acaoMascote(cao, reacao));
  _mostrarOverlay((root) => {
    const b = document.createElement("div");
    b.className = "matilha-fx-banner";
    b.dataset.matilhaEgg = `paleta-${chave}`;
    b.appendChild(_cao(info.cao[0], info.cao[1], 44));
    if (chave === "bom_garoto") b.appendChild(_cao("rott", "feliz", 44));
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
    badge.appendChild(_cao("pug", "dormindo"));
    badge.appendChild(document.createTextNode("A Matilha Dorme"));
  } else if (diaDog) {
    badge.appendChild(_cao("pug", "feliz"));
    badge.appendChild(document.createTextNode("Dia do Cão 🎉"));
  } else if (apos18h) {
    badge.appendChild(_cao("pug", "deitado"));
    badge.appendChild(document.createTextNode("Expediente noturno"));
  } else {
    badge.appendChild(_cao("rott", "sentado"));
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
  card.hidden = false;
  card.innerHTML = "";
  const caminha = document.createElement("span");
  caminha.className = "matilha-caminha";
  caminha.setAttribute("aria-hidden", "true");
  caminha.appendChild(_cao("pug", "dormindo", 56));
  card.appendChild(caminha);
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
  badge.appendChild(_cao("rott", aprovado ? "feliz" : "atento", 44));
  // Os mascotes comemoram quando a auditoria passa a aprovar a minuta.
  if (aprovado && !_auditoriaAprovada) reagirMascotes("auditoria_aprovada");
  _auditoriaAprovada = aprovado;
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
  const chkMascotes = $("chkMatilhaMascotes");
  if (chkMascotes) chkMascotes.checked = _mascotesAtivos();
  if (selPug) selPug.value = pug;
  if (selRott) selRott.value = rott;
}

function _reaplicarVisual() {
  _caesEstaticos.forEach((api) => { if (!api.el.isConnected) _caesEstaticos.delete(api); });
  _sincronizarSeloCabecalho();
  _sincronizarMascotes();
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
    if (alvo && (alvo.tagName === "INPUT" || alvo.tagName === "TEXTAREA" || alvo.tagName === "SELECT"
      || alvo.isContentEditable)) return;
    _konamiBuf.push(String(e.key || "").toLowerCase());
    if (_konamiBuf.length > KONAMI_SEQ.length) _konamiBuf.shift();
    if (_konamiBuf.length === KONAMI_SEQ.length && KONAMI_SEQ.every((k, i) => _konamiBuf[i] === k)) {
      _konamiBuf = [];
      _acionarKonami();
    }
  };
  document.addEventListener("keydown", onKeyDown);
  _cleanups.push(() => document.removeEventListener("keydown", onKeyDown));

  // A estação de sentença avisa quando chega auditoria nova (o selo e a reação dos cães dependem dela).
  const onAuditoria = () => { if (_ativo) _sincronizarAuditoria(); };
  document.addEventListener("sentencia:auditoria", onAuditoria);
  _cleanups.push(() => document.removeEventListener("sentencia:auditoria", onAuditoria));

  const onGerar = () => reagirMascotes("gerando");
  const btnGerar = $("btnGenerateSentence");
  btnGerar?.addEventListener("click", onGerar);
  _cleanups.push(() => btnGerar?.removeEventListener("click", onGerar));

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
  const chkMascotes = $("chkMatilhaMascotes");
  const onMascotesChange = () => {
    lsSet(KEY_MASCOTES, chkMascotes?.checked ? "1" : "0");
    _sincronizarMascotes();
  };
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
  chkMascotes?.addEventListener("change", onMascotesChange);
  selPug?.addEventListener("change", onPugChange);
  selRott?.addEventListener("change", onRottChange);
  _cleanups.push(() => {
    chkEggs?.removeEventListener("change", onEggsChange);
    chkSons?.removeEventListener("change", onSonsChange);
    chkMascotes?.removeEventListener("change", onMascotesChange);
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
  clearTimeout(_logoTimer);
  _logoTimer = null;
  _logoClicks = 0;
  _fecharOverlay();
  pararMascotes();
  _cleanups.forEach((fn) => fn());
  _cleanups = [];
  _caesEstaticos.clear();
  _auditoriaAprovada = false;
  $("matilhaRibbonBadge")?.remove();
  $("matilhaEmptyCard")?.remove();
  $("matilhaAuditBadge")?.remove();
  const sec = $("matilhaSettingsSection");
  if (sec) sec.hidden = true;
}
