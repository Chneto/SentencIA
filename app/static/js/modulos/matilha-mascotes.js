// SentencIA — Mascotes do tema Matilha 2.0 (TASK-103). Carregado só pelo tema-matilha.js.
// O pug (curioso e atrapalhado) e o rottweiler (sério e protetor) andam pela tela inteira atrás do mouse, bebem,
// comem, fazem cocô no canto (um clique limpa), dormem juntinhos, brincam de bolinha (duplo clique numa área
// vazia), podem ser arrastados, perseguem um ao outro e reagem ao programa. Enquanto se digita, sentam no canto.
// Sempre animados (pedido do usuário), mas desligáveis nas Configurações; o laço para com a janela oculta.

import { criarCao } from "./matilha-caes-svg.js?v=2.5.0";

const RAIZ_ID = "matilhaPets";
const TAMANHO = 80;
const LARGURA = Math.round(TAMANHO * 1.25);
const MARGEM = 6;
const TOPO_MIN = 120;
const CHEGOU_PX = 8;
const ARRASTE_PX = 5;
const MAX_COCOS = 3;
const POTES = { agua: 34, racao: 86 };
const MIN = 60_000;
const NECESSIDADES = { beber: [3 * MIN, 5 * MIN], comer: [3 * MIN, 5 * MIN], coco: [4 * MIN, 7 * MIN] };
const PERSEGUICAO_MS = [2 * MIN, 4 * MIN];
const SENTAR_MS = 6000;
const DEITAR_MS = 25_000;
const SONO_MS = 90_000;
const DURACAO = { beber: 3500, comer: 4000, coco: 1800 };
const BOLINHA_MAX_MS = 25_000;
// Só em repouso o desenho do cão recebe cliques (carinho/arrastar); andando ou correndo, os cliques atravessam.
const POSES_REPOUSO = new Set(["parado", "sentado", "deitado", "dormindo", "atento", "farejando", "feliz",
  "bebendo", "comendo", "latindo"]);
const QUADRO_OCIOSO_MS = 200;

// Pontos do desenho (viewBox 125x100, cão virado para a direita), medidos pelo módulo de arte: o cangote fica sob o
// mouse ao carregar e a boca segura a bolinha. Escala para a caixa de LARGURA x TAMANHO px.
const ESCALA = TAMANHO / 100;
const ANCORAS = {
  pug: { cangote: { x: 87, y: 49 }, boca: { x: 113, y: 57 } },
  rott: { cangote: { x: 82, y: 36 }, boca: { x: 116, y: 39 } },
};
// Ao beber/comer o cão fica à direita do pote, virado para a esquerda, com a boca sobre ele (boca de cabeça baixa:
// pug (108, 90), rott (116, 84) no desenho): distância do centro do cão ao centro do pote, em px.
const BOCA_AO_POTE = { pug: 36, rott: 43 };

const CAES = {
  pug: { andar: 115, correr: 270, deslocamento: { x: -72, y: 46 }, cantinho: 170 },
  rott: { andar: 135, correr: 310, deslocamento: { x: 72, y: 46 }, cantinho: 270 },
};

let _ctx = null;

const _agora = () => performance.now();
const _sorteio = ([min, max]) => min + Math.random() * (max - min);
const _vw = () => document.documentElement.clientWidth || window.innerWidth || 1280;
const _vh = () => document.documentElement.clientHeight || window.innerHeight || 720;

function _limitar(x, y) {
  return {
    x: Math.max(LARGURA / 2 + MARGEM, Math.min(_vw() - LARGURA / 2 - MARGEM, x)),
    y: Math.max(TOPO_MIN, Math.min(_vh() - MARGEM, y)),
  };
}

function _criarEl(tag, classe, attrs = {}) {
  const el = document.createElement(tag);
  if (classe) el.className = classe;
  Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
  return el;
}

function _posicionar(el, x, y, largura, altura) {
  el.style.transform = `translate(${Math.round(x - largura / 2)}px, ${Math.round(y - altura)}px)`;
}

// --- Cães -----------------------------------------------------------------------------------------------

function _novoCao(nome, agora) {
  const variante = _ctx.variantes()[nome];
  const arte = criarCao(nome, variante, { tamanho: TAMANHO });
  const wrap = _criarEl("div", "matilha-pet", { "data-cao": nome, "data-estado": "parado" });
  const balao = _criarEl("span", "matilha-pet-balao");
  wrap.append(arte.el, balao);
  const inicio = _limitar(CAES[nome].cantinho, _vh() - MARGEM);
  const cao = {
    nome, arte, wrap, balao, variante, x: inicio.x, y: inicio.y, direcao: "direita", pose: "",
    temp: null, acao: null, arraste: null, comBolinha: false, idleDesde: agora, render: {},
    proximo: {
      beber: agora + _sorteio(NECESSIDADES.beber),
      comer: agora + _sorteio(NECESSIDADES.comer),
      coco: agora + _sorteio(NECESSIDADES.coco),
    },
    proximoPiscar: agora + 2000 + Math.random() * 4000,
  };
  wrap.addEventListener("pointerdown", (e) => _iniciarArraste(cao, e));
  return cao;
}

function _definirPose(cao, pose) {
  if (cao.pose === pose) return;
  cao.pose = pose;
  cao.arte.definirPose(pose);
  cao.wrap.dataset.estado = pose;
}

function _temporario(cao, pose, ms, balao = "") {
  cao.temp = { pose, ate: _agora() + ms, balao };
}

// Só escreve no DOM o que mudou (sessões de horas com o laço rodando).
function _renderizar(cao) {
  const r = cao.render;
  const transform = `translate(${Math.round(cao.x - LARGURA / 2)}px, ${Math.round(cao.y - TAMANHO)}px)`;
  if (r.transform !== transform) cao.wrap.style.transform = r.transform = transform;
  if (r.direcao !== cao.direcao) {
    r.direcao = cao.direcao;
    cao.arte.definirDirecao(cao.direcao);
    cao.wrap.dataset.direcao = cao.direcao;
  }
  const comBolinha = cao.comBolinha ? "1" : "0";
  if (r.comBolinha !== comBolinha) cao.wrap.dataset.comBolinha = r.comBolinha = comBolinha;
  const interativo = !cao.arraste && POSES_REPOUSO.has(cao.pose) && !_digitando();
  if (r.interativo !== interativo || cao.arraste) {
    r.interativo = interativo || Boolean(cao.arraste);
    cao.wrap.classList.toggle("mcao-interativo", r.interativo);
  }
  const balao = cao.temp?.balao || (cao.pose === "dormindo" ? "Zzz" : "");
  if (cao.balao.textContent !== balao) cao.balao.textContent = balao;
}

// Anda em direção ao alvo; devolve true ao chegar.
function _mover(cao, alvo, dt, correndo) {
  const dx = alvo.x - cao.x;
  const dy = alvo.y - cao.y;
  const dist = Math.hypot(dx, dy);
  if (dist <= CHEGOU_PX) return true;
  const velocidade = CAES[cao.nome][correndo ? "correr" : "andar"] * (cao.tropeco ? 0.4 : 1);
  const passo = Math.min(dist, velocidade * dt / 1000);
  cao.x += dx / dist * passo;
  cao.y += dy / dist * passo;
  if (Math.abs(dx) > 1) cao.direcao = dx < 0 ? "esquerda" : "direita";
  return dist - passo <= CHEGOU_PX;
}

// --- Ações (necessidades, bolinha, perseguição) ---------------------------------------------------------

function _alvoDaAcao(cao, tipo) {
  const chao = _vh() - MARGEM;
  if (tipo === "beber") return _limitar(POTES.agua + BOCA_AO_POTE[cao.nome], chao);
  if (tipo === "comer") return _limitar(POTES.racao + BOCA_AO_POTE[cao.nome], chao);
  if (tipo === "coco") return _limitar(_vw() - 70 - _ctx.raiz.querySelectorAll("[data-coco]").length * 38, chao);
  return null;
}

function _iniciarAcao(cao, tipo, extra = {}) {
  if (tipo === "coco" && _ctx.raiz.querySelectorAll("[data-coco]").length >= MAX_COCOS) return;
  if (cao.comBolinha && tipo !== "bolinha") _largarBolinha(cao);
  cao.acao = { tipo, fase: "indo", fim: 0, ...extra };
  cao.idleDesde = _agora();
}

function _concluirAcao(cao) {
  const tipo = cao.acao.tipo;
  if (tipo === "coco") _soltarCoco(cao.x + (cao.direcao === "direita" ? -LARGURA * 0.35 : LARGURA * 0.35));
  if (NECESSIDADES[tipo]) cao.proximo[tipo] = _agora() + _sorteio(NECESSIDADES[tipo]);
  cao.wrap.dataset.acaoConcluida = tipo;
  cao.acao = null;
  cao.idleDesde = _agora();
}

function _soltarCoco(x) {
  const coco = _criarEl("span", "matilha-coco", { "data-coco": "", title: "Limpar" });
  coco.textContent = "💩";
  coco.style.transform = `translateX(${Math.round(Math.min(_vw() - 34, Math.max(6, x - 14)))}px)`;
  // pointerup (e não click): o pointerdown pode cair no elemento de baixo e o click iria ao ancestral comum.
  coco.addEventListener("pointerup", (e) => {
    e.stopPropagation();
    if (!coco.isConnected) return;
    coco.remove();
    _ctx?.aoLimpar?.();
  });
  _ctx.raiz.appendChild(coco);
}

function _passoAcao(cao, agora, dt) {
  const acao = cao.acao;
  if (acao.tipo === "perseguir") {
    const fugitivo = _ctx.caes.find((c) => c.nome === acao.fugitivo);
    if (agora > acao.fim) return _concluirAcao(cao);
    const alvo = cao.nome === acao.fugitivo ? acao.destino : { x: fugitivo.x - 40, y: fugitivo.y };
    if (cao.nome === acao.fugitivo && _mover(cao, acao.destino, dt, true)) {
      acao.destino = _limitar(Math.random() * _vw(), TOPO_MIN + Math.random() * (_vh() - TOPO_MIN));
    } else if (cao.nome !== acao.fugitivo) {
      _mover(cao, alvo, dt, true);
    }
    return _definirPose(cao, "correndo");
  }
  if (acao.tipo === "bolinha") return _passoBolinha(cao, acao, dt);
  if (acao.fase === "indo") {
    const alvo = _alvoDaAcao(cao, acao.tipo);
    if (!_mover(cao, alvo, dt, false)) return _definirPose(cao, _poseAndando(cao));
    acao.fase = "fazendo";
    acao.fim = agora + DURACAO[acao.tipo];
    if (acao.tipo !== "coco") cao.direcao = "esquerda";
  }
  _definirPose(cao, { beber: "bebendo", comer: "comendo", coco: "coco" }[acao.tipo]);
  if (agora >= acao.fim) _concluirAcao(cao);
}

function _poseAndando(cao) {
  return cao.tropeco ? "tropecando" : "andando";
}

// --- Bolinha ------------------------------------------------------------------------------------------------

export function jogarBolinha(x, y) {
  if (!_ctx || _ctx.bolinha) return;
  const el = _criarEl("span", "matilha-bolinha", { "data-bolinha": "" });
  const pos = _limitar(x, y);
  const bolinha = { el, x: pos.x, y: pos.y, dono: null, criadaEm: _agora() };
  _posicionar(el, bolinha.x, bolinha.y, 22, 22);
  _ctx.raiz.appendChild(el);
  _ctx.bolinha = bolinha;
  _ctx.caes.forEach((cao) => {
    if (!cao.arraste) _iniciarAcao(cao, "bolinha", { fase: "buscando" });
  });
}

// Converte um ponto do desenho para a tela, considerando para que lado o cão está virado.
function _pontoNaTela(cao, ponto) {
  const px = ponto.x * ESCALA;
  const esquerda = cao.x - LARGURA / 2;
  return {
    x: esquerda + (cao.direcao === "direita" ? px : LARGURA - px),
    y: cao.y - TAMANHO + ponto.y * ESCALA,
  };
}

function _bocaDe(cao) {
  return _pontoNaTela(cao, ANCORAS[cao.nome].boca);
}

function _passoBolinha(cao, acao, dt) {
  const bola = _ctx.bolinha;
  if (!bola) return _concluirAcao(cao);
  if (bola.dono && bola.dono !== cao) {
    // Perdeu a corrida: comemora com o outro e segue o dono por um instante.
    _concluirAcao(cao);
    return _temporario(cao, "feliz", 1500);
  }
  if (!bola.dono) {
    if (_mover(cao, { x: bola.x, y: bola.y + 4 }, dt, true)) {
      bola.dono = cao;
      cao.comBolinha = true;
      bola.el.classList.add("matilha-bolinha-na-boca");
    }
    return _definirPose(cao, "correndo");
  }
  // Leva a bolinha até o mouse (ou ao centro, se o mouse ainda não se mexeu) e solta.
  const destino = _ctx.mouse ? _limitar(_ctx.mouse.x, _ctx.mouse.y + 60) : _limitar(_vw() / 2, _vh() - MARGEM);
  const chegou = _mover(cao, destino, dt, true);
  const boca = _bocaDe(cao);
  bola.x = boca.x;
  bola.y = boca.y + 11; // a bolinha (22 px) com o centro na boca
  _posicionar(bola.el, bola.x, bola.y, 22, 22);
  _definirPose(cao, "correndo");
  if (chegou) {
    _removerBolinha();
    _concluirAcao(cao);
    _temporario(cao, "feliz", 1600, "♥");
  }
  return undefined;
}

// --- Arrastar e carinho ---------------------------------------------------------------------------------------

function _iniciarArraste(cao, e) {
  if (e.button !== 0 || e.ctrlKey || !cao.wrap.classList.contains("mcao-interativo")) return;
  e.preventDefault();
  cao.wrap.setPointerCapture?.(e.pointerId);
  cao.arraste = { inicioX: e.clientX, inicioY: e.clientY, movido: false };
  const mover = (ev) => {
    const a = cao.arraste;
    if (!a) return;
    if (!a.movido && Math.hypot(ev.clientX - a.inicioX, ev.clientY - a.inicioY) < ARRASTE_PX) return;
    a.movido = true;
    if (cao.comBolinha) _largarBolinha(cao);
    cao.acao = null;
    // O cangote fica sob o mouse: centro e pés calculados a partir do ponto do desenho.
    const cangote = ANCORAS[cao.nome].cangote;
    const dx = (cao.direcao === "direita" ? cangote.x * ESCALA : LARGURA - cangote.x * ESCALA) - LARGURA / 2;
    cao.x = ev.clientX - dx;
    cao.y = ev.clientY + TAMANHO - cangote.y * ESCALA;
    _definirPose(cao, "carregado");
    _renderizar(cao);
  };
  const soltar = () => {
    if (!cao.arraste) return;
    cao.wrap.removeEventListener("pointermove", mover);
    cao.wrap.removeEventListener("pointerup", soltar);
    cao.wrap.removeEventListener("pointercancel", soltar);
    cao.wrap.removeEventListener("lostpointercapture", soltar);
    window.removeEventListener("blur", soltar);
    const movido = cao.arraste?.movido;
    cao.arraste = null;
    cao.idleDesde = _agora();
    if (movido) {
      _temporario(cao, "pulando", 600);
    } else {
      _temporario(cao, "feliz", 1300, "♥");
      if (_ctx.sons()) _ctx.tocarSom(cao.nome === "pug" ? "squeak" : "bark");
    }
  };
  cao.wrap.addEventListener("pointermove", mover);
  cao.wrap.addEventListener("pointerup", soltar);
  cao.wrap.addEventListener("pointercancel", soltar);
  cao.wrap.addEventListener("lostpointercapture", soltar);
  window.addEventListener("blur", soltar);
}

// O cão que carregava foi interrompido (arrastado, outra ação): a bolinha cai e os cães livres vão buscá-la.
function _largarBolinha(cao) {
  const bola = _ctx.bolinha;
  cao.comBolinha = false;
  if (!bola) return;
  bola.dono = null;
  bola.el.classList.remove("matilha-bolinha-na-boca");
  _ctx.caes.forEach((outro) => {
    if (outro !== cao && !outro.arraste && outro.acao?.tipo !== "bolinha") {
      outro.acao = { tipo: "bolinha", fase: "buscando", fim: 0 };
    }
  });
}

function _removerBolinha() {
  const bola = _ctx.bolinha;
  if (!bola) return;
  _ctx.bolinha = null;
  _ctx.caes.forEach((cao) => { cao.comBolinha = false; });
  bola.el.classList.remove("matilha-bolinha-na-boca");
  bola.el.classList.add("matilha-bolinha-solta");
  setTimeout(() => bola.el.remove(), 1400);
}

// --- Comportamento padrão --------------------------------------------------------------------------------------

function _digitando() {
  const el = document.activeElement;
  if (!el || el === document.body) return false;
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  return tag === "INPUT" && !["checkbox", "radio", "button", "submit", "range", "color", "file"].includes(el.type);
}

function _passoLivre(cao, agora, dt) {
  const chao = _vh() - MARGEM;
  if (_digitando()) {
    const canto = _limitar(CAES[cao.nome].cantinho, chao);
    return _definirPose(cao, _mover(cao, canto, dt, false) ? "sentado" : _poseAndando(cao));
  }
  const parado = agora - _ctx.ultimoMovimento;
  if (_ctx.mouse && parado > SONO_MS) {
    // Dormem juntinhos, lado a lado, perto dos potes.
    const ninho = _limitar(cao.nome === "pug" ? 190 : 260, chao);
    return _definirPose(cao, _mover(cao, ninho, dt, false) ? "dormindo" : _poseAndando(cao));
  }
  if (!_ctx.mouse) return _definirPose(cao, "parado");
  const { x: ox, y: oy } = CAES[cao.nome].deslocamento;
  let alvo = _limitar(_ctx.mouse.x + ox, _ctx.mouse.y + oy);
  if (Math.abs(alvo.x - (_ctx.mouse.x + ox)) > 20) alvo = _limitar(_ctx.mouse.x - ox, _ctx.mouse.y + oy);
  const dist = Math.hypot(alvo.x - cao.x, alvo.y - cao.y);
  // Cursor em cima do cão (o usuário quer fazer carinho ou arrastar): ele fica e olha, em vez de fugir do clique.
  const mouseSobre = Math.abs(_ctx.mouse.x - cao.x) < LARGURA / 2 + 12
    && _ctx.mouse.y > cao.y - TAMANHO - 12 && _ctx.mouse.y < cao.y + 12;
  // Parado, só sai do lugar quando o alvo se afasta de verdade (evita passinhos a cada tremida do mouse).
  const emMovimento = cao.pose === "andando" || cao.pose === "correndo" || cao.pose === "tropecando";
  if (mouseSobre || (!emMovimento && dist <= 40)) {
    if (mouseSobre && cao.pose !== "deitado" && cao.pose !== "dormindo") {
      cao.direcao = _ctx.mouse.x < cao.x ? "esquerda" : "direita";
      return _definirPose(cao, "atento");
    }
  } else if (dist > CHEGOU_PX) {
    if (cao.pose !== "andando" && cao.pose !== "correndo" && cao.nome === "pug" && dist > 220 && Math.random() < 0.12) {
      cao.tropeco = agora + 700; // o pug às vezes tropeça ao arrancar
    }
    if (cao.tropeco && agora > cao.tropeco) cao.tropeco = 0;
    _mover(cao, alvo, dt, dist > 260);
    cao.idleDesde = agora;
    return _definirPose(cao, cao.tropeco ? "tropecando" : (dist > 260 ? "correndo" : "andando"));
  }
  const ocioso = agora - cao.idleDesde;
  if (ocioso > DEITAR_MS) return _definirPose(cao, "deitado");
  if (ocioso > SENTAR_MS) return _definirPose(cao, cao.nome === "rott" ? "sentado" : (Math.floor(ocioso / 4000) % 2 ? "farejando" : "sentado"));
  return _definirPose(cao, cao.nome === "rott" ? "atento" : "parado");
}

function _passoCao(cao, agora, dt) {
  if (cao.arraste) return;
  if (agora >= cao.proximoPiscar) {
    cao.arte.piscar();
    cao.proximoPiscar = agora + 2500 + Math.random() * 4500;
  }
  if (cao.temp) {
    if (agora < cao.temp.ate) return _definirPose(cao, cao.temp.pose);
    cao.temp = null;
  }
  if (!cao.acao && !_digitando()) {
    const devida = ["beber", "comer", "coco"].find((tipo) => agora >= cao.proximo[tipo]);
    if (devida) _iniciarAcao(cao, devida);
    else if (cao.nome === "pug" && agora >= _ctx.proximaPerseguicao && _ctx.caes.every((c) => !c.acao)) {
      _perseguir();
    }
  }
  if (cao.acao) return _passoAcao(cao, agora, dt);
  return _passoLivre(cao, agora, dt);
}

function _perseguir() {
  const fim = _agora() + 6000;
  const destino = _limitar(Math.random() * _vw(), TOPO_MIN + Math.random() * (_vh() - TOPO_MIN));
  _ctx.caes.forEach((cao) => _iniciarAcao(cao, "perseguir", { fugitivo: "pug", fim, destino }));
  _ctx.proximaPerseguicao = _agora() + _sorteio(PERSEGUICAO_MS);
}

function _todosDescansando() {
  return !_ctx.bolinha && _ctx.caes.every((c) => !c.acao && !c.temp && !c.arraste
    && (c.pose === "dormindo" || c.pose === "deitado" || c.pose === "sentado"));
}

function _quadro(ts) {
  if (!_ctx) return;
  _ctx.raf = requestAnimationFrame(_quadro);
  if (_todosDescansando() && ts - (_ctx.ultimoTs || 0) < QUADRO_OCIOSO_MS && !_ctx.mexeu) return;
  _ctx.mexeu = false;
  const dt = Math.min(100, _ctx.ultimoTs ? ts - _ctx.ultimoTs : 16);
  _ctx.ultimoTs = ts;
  const agora = _agora();
  if (_ctx.bolinha && agora - _ctx.bolinha.criadaEm > BOLINHA_MAX_MS) _removerBolinha();
  _ctx.caes.forEach((cao) => {
    try {
      _passoCao(cao, agora, dt);
      _renderizar(cao);
    } catch (erro) {
      // Um erro num cão não pode parar a animação de vez.
      if (!_ctx.avisouErro) console.warn("Matilha: falha num passo do mascote", erro);
      _ctx.avisouErro = true;
      cao.acao = null;
      cao.temp = null;
    }
  });
}

// --- Eventos -----------------------------------------------------------------------------------------------------

const _INTERATIVOS = "input,textarea,select,button,a,label,summary,[contenteditable],[tabindex],[role='button'],"
  + "[role='tab'],[role='menuitem'],[role='menuitemradio'],[role='option'],[role='switch'],.sentence-paper-wrapper,"
  + ".modal-backdrop,.matilha-pet,.toast-item,#appMainMenu";

// O ponto está mesmo em cima de um texto (e não só perto dele)?
function _pontoSobreTexto(x, y) {
  const pos = document.caretRangeFromPoint?.(x, y);
  const no = pos?.startContainer;
  if (!no || no.nodeType !== Node.TEXT_NODE || !no.textContent.trim()) return false;
  const faixa = document.createRange();
  faixa.selectNodeContents(no);
  return [...faixa.getClientRects()].some((r) => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom);
}

function _ligarEventos() {
  const aoMover = (e) => {
    _ctx.mouse = { x: e.clientX, y: e.clientY };
    _ctx.ultimoMovimento = _agora();
    _ctx.mexeu = true;
  };
  const aoDuploClique = (e) => {
    if (e.target.closest?.(_INTERATIVOS)) return;
    if (_pontoSobreTexto(e.clientX, e.clientY)) return; // duplo clique em texto é para selecionar a palavra
    // Em área vazia o navegador seleciona a palavra mais próxima; essa seleção acidental é desfeita.
    window.getSelection?.().removeAllRanges();
    jogarBolinha(e.clientX, e.clientY);
  };
  const aoVisibilidade = () => {
    if (document.hidden) {
      cancelAnimationFrame(_ctx.raf);
      _ctx.raf = null;
    } else if (!_ctx.raf) {
      _ctx.ultimoTs = 0;
      _ctx.raf = requestAnimationFrame(_quadro);
    }
  };
  document.addEventListener("pointermove", aoMover, { passive: true });
  document.addEventListener("dblclick", aoDuploClique);
  document.addEventListener("visibilitychange", aoVisibilidade);
  const toasts = document.getElementById("toastContainer");
  const obs = new MutationObserver((mutacoes) => {
    mutacoes.forEach((m) => m.addedNodes.forEach((n) => {
      const tipo = n.dataset?.toastKind;
      if (tipo === "success") reagirMascotes("sucesso");
      else if (tipo === "error") reagirMascotes("erro");
    }));
  });
  if (toasts) obs.observe(toasts, { childList: true });
  return () => {
    document.removeEventListener("pointermove", aoMover);
    document.removeEventListener("dblclick", aoDuploClique);
    document.removeEventListener("visibilitychange", aoVisibilidade);
    obs.disconnect();
  };
}

// --- API -----------------------------------------------------------------------------------------------------------

/**
 * Liga os mascotes (idempotente: se já estão na tela, só troca as variantes).
 * opcoes: { variantes: () => ({pug, rott}), sons: () => bool, tocarSom(tipo), aoLimpar() }
 */
export function iniciarMascotes(opcoes) {
  if (_ctx) {
    Object.assign(_ctx, opcoes);
    const variantes = _ctx.variantes();
    _ctx.caes.forEach((cao) => {
      if (cao.variante !== variantes[cao.nome]) {
        cao.variante = variantes[cao.nome];
        cao.arte.definirVariante(cao.variante);
      }
    });
    return;
  }
  const raiz = _criarEl("div", "matilha-pets", { id: RAIZ_ID, "aria-hidden": "true" });
  raiz.append(
    _criarEl("span", "matilha-pote matilha-pote-agua", { "data-pote": "agua" }),
    _criarEl("span", "matilha-pote matilha-pote-racao", { "data-pote": "racao" }),
  );
  const agora = _agora();
  _ctx = {
    ...opcoes, raiz, caes: [], mouse: null, ultimoMovimento: agora, raf: null, ultimoTs: 0, bolinha: null,
    proximaPerseguicao: agora + _sorteio(PERSEGUICAO_MS),
  };
  document.body.appendChild(raiz);
  raiz.querySelector("[data-pote='agua']").style.transform = `translateX(${POTES.agua - 20}px)`;
  raiz.querySelector("[data-pote='racao']").style.transform = `translateX(${POTES.racao - 20}px)`;
  _ctx.caes = Object.keys(CAES).map((nome) => _novoCao(nome, agora));
  _ctx.caes.forEach((cao) => { raiz.appendChild(cao.wrap); _definirPose(cao, "parado"); _renderizar(cao); });
  // Ao redimensionar, os cães voltam para dentro da tela.
  const aoRedimensionar = () => _ctx?.caes.forEach((cao) => Object.assign(cao, _limitar(cao.x, cao.y)));
  window.addEventListener("resize", aoRedimensionar);
  const desligarEventos = _ligarEventos();
  _ctx.desligar = () => {
    desligarEventos();
    window.removeEventListener("resize", aoRedimensionar);
  };
  _ctx.raf = requestAnimationFrame(_quadro);
}

/** Remove os mascotes e todos os ouvintes. */
export function pararMascotes() {
  if (!_ctx) return;
  if (_ctx.raf) cancelAnimationFrame(_ctx.raf);
  _ctx.desligar?.();
  _ctx.caes.forEach((cao) => cao.arte.destruir());
  _ctx.raiz.remove();
  _ctx = null;
}

/** Ações sob demanda: beber, comer, coco, dormir, carinho, perseguir (paleta, reações e testes). */
export function acaoMascote(nome, acao) {
  const cao = _ctx?.caes.find((c) => c.nome === nome);
  if (!cao || cao.arraste) return;
  if (acao === "carinho") return _temporario(cao, "feliz", 1300, "♥");
  if (acao === "dormir") {
    _ctx.ultimoMovimento = _agora() - SONO_MS - 1;
    return undefined;
  }
  if (acao === "perseguir") return _perseguir();
  if (DURACAO[acao]) _iniciarAcao(cao, acao);
  return undefined;
}

/** Reações ao programa: sucesso, erro, auditoria_aprovada, gerando. */
export function reagirMascotes(evento) {
  if (!_ctx) return;
  _ctx.caes.forEach((cao) => {
    if (cao.arraste) return;
    if (evento === "sucesso") _temporario(cao, "feliz", 1500);
    else if (evento === "erro") _temporario(cao, cao.nome === "rott" ? "latindo" : "atento", 1600, "!");
    else if (evento === "auditoria_aprovada") _temporario(cao, "pulando", 2400, "♥");
    else if (evento === "gerando") _temporario(cao, "correndo", 1800);
  });
}

/** Estado atual (centro x, pés y, pose, direção) — para testes e diagnóstico. */
export function estadoMascotes() {
  return (_ctx?.caes || []).map(({ nome, x, y, pose, direcao, comBolinha }) => (
    { cao: nome, x, y, estado: pose, direcao, comBolinha }));
}
