// SentencIA — Mascotes do tema Matilha (TASK-100). Carregado só pelo tema-matilha.js.
// O pug e o rottweiler andam numa faixa no rodapé da tela: seguem o mouse, bebem água, comem, fazem cocô no
// canto direito (um clique limpa) e dormem quando o mouse fica parado (acordam quando ele se mexe).
// Regras: nunca bloqueiam cliques (pointer-events: none, exceto o cocô), nada externo (CSP), "reduzir movimento"
// os deixa parados e tudo para quando a janela está oculta.

const RAIZ_ID = "matilhaPets";
const POTE_AGUA_X = 30;
const POTE_RACAO_X = 82;
const MIN_X = 130;
const MARGEM_DIREITA = 46;
const CHEGOU_PX = 6;
const MAX_COCOS = 3;
const SONO_SEM_MOUSE_MS = 90_000;
const DURACAO_MS = { beber: 3500, comer: 4000, coco: 1600, carinho: 1200 };

const CAES = {
  pug: { velocidade: 150, deslocamento: -62, inicioX: 180 },
  rott: { velocidade: 175, deslocamento: 62, inicioX: 250 },
};

// Poses por estado: arquivos de imagem existentes (as variantes vêm de imgPug/imgRott).
const POSES = {
  pug: { parado: "feliz", andando: "zoomies", beber: "farejando", comer: "farejando", coco: "ouvindo", dormindo: "dormindo", carinho: "feliz" },
  rott: { parado: "sentado", andando: "zoomies", beber: "atento", comer: "atento", coco: "sentado", dormindo: "sentado", carinho: "guardiao" },
};

let _ctx = null;

function _aleatorio(minS, maxS) {
  return (minS + Math.random() * (maxS - minS)) * 1000;
}

function _largura() {
  return document.documentElement.clientWidth || window.innerWidth || 1280;
}

function _limitar(x) {
  return Math.max(MIN_X, Math.min(_largura() - MARGEM_DIREITA, x));
}

function _criarEl(tag, classe, attrs = {}) {
  const el = document.createElement(tag);
  if (classe) el.className = classe;
  Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
  return el;
}

function _criarDom() {
  const raiz = _criarEl("div", "matilha-pets", { id: RAIZ_ID, "aria-hidden": "true" });
  const agua = _criarEl("span", "matilha-pote matilha-pote-agua", { "data-pote": "agua" });
  const racao = _criarEl("span", "matilha-pote matilha-pote-racao", { "data-pote": "racao" });
  agua.style.transform = `translateX(${POTE_AGUA_X - 18}px)`;
  racao.style.transform = `translateX(${POTE_RACAO_X - 18}px)`;
  raiz.append(agua, racao);
  return raiz;
}

function _novoCao(nome, agora) {
  // el: posição (translateX inline); corpo: animação do estado; img: vira para o lado da caminhada.
  const el = _criarEl("div", "matilha-pet", { "data-cao": nome, "data-estado": "parado" });
  const corpo = _criarEl("span", "matilha-pet-corpo");
  const img = _criarEl("img", "matilha-pet-img", { alt: "", draggable: "false", width: "56", height: "56" });
  const balao = _criarEl("span", "matilha-pet-balao");
  corpo.appendChild(img);
  el.append(corpo, balao);
  return {
    nome, el, img, balao,
    x: CAES[nome].inicioX, direcao: "direita", estado: "parado", pose: "",
    acao: null, alvoAcao: null, fimAcao: 0,
    proximaSede: agora + _aleatorio(60, 140),
    proximaFome: agora + _aleatorio(90, 180),
    proximoCoco: agora + _aleatorio(180, 360),
  };
}

function _definirPose(cao, estado) {
  const pose = POSES[cao.nome][estado] || POSES[cao.nome].parado;
  if (cao.pose === pose) return;
  cao.pose = pose;
  cao.img.src = `/static/img/matilha/${cao.nome === "pug" ? _ctx.imgPug(pose) : _ctx.imgRott(pose)}`;
}

function _renderizar(cao) {
  cao.el.style.transform = `translateX(${Math.round(cao.x - 28)}px)`;
  cao.el.dataset.estado = cao.estado;
  cao.el.dataset.direcao = cao.direcao;
  cao.balao.textContent = cao.estado === "dormindo" ? "Zzz" : "";
  _definirPose(cao, cao.estado);
}

function _iniciarAcao(cao, acao, agora) {
  const cocos = _ctx.raiz.querySelectorAll("[data-coco]").length;
  if (acao === "coco" && cocos >= MAX_COCOS) return;
  const alvos = {
    beber: POTE_AGUA_X + 40,
    comer: POTE_RACAO_X + 40,
    coco: _largura() - 60 - cocos * 34,
    carinho: cao.x,
  };
  cao.acao = acao;
  cao.alvoAcao = alvos[acao] ?? cao.x;
  cao.fimAcao = 0;
  cao.inicioAcao = agora;
}

function _concluirAcao(cao, agora) {
  if (cao.acao === "coco") _soltarCoco(cao.x + 18);
  cao.el.dataset.acaoConcluida = cao.acao;
  if (cao.acao === "beber") cao.proximaSede = agora + _aleatorio(60, 140);
  if (cao.acao === "comer") cao.proximaFome = agora + _aleatorio(90, 180);
  if (cao.acao === "coco") cao.proximoCoco = agora + _aleatorio(180, 360);
  cao.acao = null;
  cao.alvoAcao = null;
}

function _soltarCoco(x) {
  const coco = _criarEl("span", "matilha-coco", { "data-coco": "", title: "Limpar" });
  coco.textContent = "💩";
  coco.style.transform = `translateX(${Math.round(Math.min(_largura() - 30, x))}px)`;
  // pointerup (e não click): o pointerdown pode cair no elemento de baixo e o click iria para o ancestral comum.
  coco.addEventListener("pointerup", (e) => {
    e.stopPropagation();
    if (!coco.isConnected) return;
    coco.remove();
    _ctx?.aoLimpar?.();
  });
  _ctx.raiz.appendChild(coco);
}

function _andar(cao, alvo, dt) {
  const dist = alvo - cao.x;
  if (Math.abs(dist) <= CHEGOU_PX) return true;
  cao.direcao = dist < 0 ? "esquerda" : "direita";
  const passo = Math.sign(dist) * Math.min(Math.abs(dist), CAES[cao.nome].velocidade * dt / 1000);
  cao.x += passo;
  return Math.abs(alvo - cao.x) <= CHEGOU_PX;
}

function _passoCao(cao, agora, dt) {
  if (cao.sono) {
    cao.estado = "dormindo";
    return;
  }
  if (!cao.acao) {
    if (agora >= cao.proximaSede) _iniciarAcao(cao, "beber", agora);
    else if (agora >= cao.proximaFome) _iniciarAcao(cao, "comer", agora);
    else if (agora >= cao.proximoCoco) _iniciarAcao(cao, "coco", agora);
  }
  if (cao.acao) {
    if (!cao.fimAcao) {
      if (_andar(cao, _limitarAcao(cao.alvoAcao), dt)) {
        cao.fimAcao = agora + DURACAO_MS[cao.acao];
        if (cao.acao === "beber" || cao.acao === "comer") cao.direcao = "esquerda";
      } else {
        cao.estado = "andando";
        return;
      }
    }
    cao.estado = cao.acao;
    if (agora >= cao.fimAcao) _concluirAcao(cao, agora);
    return;
  }
  if (_ctx.mouseX !== null && agora - _ctx.ultimoMovimento > SONO_SEM_MOUSE_MS) {
    cao.sono = true;
    cao.estado = "dormindo";
    return;
  }
  if (_ctx.mouseX === null) {
    cao.estado = "parado";
    return;
  }
  const alvo = _limitar(_ctx.mouseX + CAES[cao.nome].deslocamento);
  cao.estado = _andar(cao, alvo, dt) ? "parado" : "andando";
}

function _limitarAcao(x) {
  return Math.max(POTE_AGUA_X + 30, Math.min(_largura() - 30, x));
}

function _quadro(ts) {
  if (!_ctx) return;
  const dt = Math.min(100, _ctx.ultimoTs ? ts - _ctx.ultimoTs : 16);
  _ctx.ultimoTs = ts;
  const agora = performance.now();
  _ctx.caes.forEach((cao) => {
    _passoCao(cao, agora, dt);
    _renderizar(cao);
  });
  _ctx.raf = requestAnimationFrame(_quadro);
}

function _caoSobPonto(x, y) {
  return _ctx.caes.find((cao) => {
    const r = cao.el.getBoundingClientRect();
    return x >= r.left - 8 && x <= r.right + 8 && y >= r.top - 8 && y <= r.bottom + 8;
  });
}

function _ligarEventos() {
  const aoMover = (e) => {
    _ctx.mouseX = e.clientX;
    _ctx.ultimoMovimento = performance.now();
    _ctx.caes.forEach((cao) => { cao.sono = false; });
  };
  const aoClicar = (e) => {
    const cao = _caoSobPonto(e.clientX, e.clientY);
    if (!cao) return;
    cao.sono = false;
    _iniciarAcao(cao, "carinho", performance.now());
    if (_ctx.sons()) _ctx.tocarSom(cao.nome === "pug" ? "squeak" : "bark");
  };
  const aoVisibilidade = () => {
    if (document.hidden) {
      cancelAnimationFrame(_ctx.raf);
      _ctx.raf = null;
    } else if (!_ctx.raf && !_ctx.reducedMotion) {
      _ctx.ultimoTs = 0;
      _ctx.raf = requestAnimationFrame(_quadro);
    }
  };
  document.addEventListener("pointermove", aoMover, { passive: true });
  document.addEventListener("pointerdown", aoClicar, { capture: true, passive: true });
  document.addEventListener("visibilitychange", aoVisibilidade);
  return () => {
    document.removeEventListener("pointermove", aoMover);
    document.removeEventListener("pointerdown", aoClicar, { capture: true });
    document.removeEventListener("visibilitychange", aoVisibilidade);
  };
}

/** Liga os mascotes (idempotente: se já estão na tela, só atualiza as imagens das variantes). */
export function iniciarMascotes(opcoes) {
  if (_ctx) {
    Object.assign(_ctx, { imgPug: opcoes.imgPug, imgRott: opcoes.imgRott });
    _ctx.caes.forEach((cao) => { cao.pose = ""; _renderizar(cao); });
    return;
  }
  const agora = performance.now();
  const raiz = _criarDom();
  _ctx = {
    ...opcoes, raiz, caes: [], mouseX: null, ultimoMovimento: agora, raf: null, ultimoTs: 0, desligar: null,
  };
  _ctx.caes = Object.keys(CAES).map((nome) => _novoCao(nome, agora));
  _ctx.caes.forEach((cao) => { raiz.appendChild(cao.el); _renderizar(cao); });
  document.body.appendChild(raiz);
  if (opcoes.reducedMotion) {
    _ctx.caes.forEach((cao) => { cao.estado = "parado"; _renderizar(cao); });
    return;
  }
  _ctx.desligar = _ligarEventos();
  _ctx.raf = requestAnimationFrame(_quadro);
}

/** Remove os mascotes e todos os ouvintes. */
export function pararMascotes() {
  if (!_ctx) return;
  if (_ctx.raf) cancelAnimationFrame(_ctx.raf);
  _ctx.desligar?.();
  _ctx.raiz.remove();
  _ctx = null;
}

/** Dispara uma ação ("beber", "comer", "coco", "carinho", "dormir") — usado pela paleta e pelos testes. */
export function acaoMascote(nome, acao) {
  const cao = _ctx?.caes.find((c) => c.nome === nome);
  if (cao && acao === "dormir" && !_ctx.reducedMotion) {
    cao.acao = null;
    cao.sono = true;
    return;
  }
  if (cao && DURACAO_MS[acao] && !_ctx.reducedMotion) _iniciarAcao(cao, acao, performance.now());
}

/** Estado atual dos cães (centro em px, estado e direção) — para testes e diagnóstico. */
export function estadoMascotes() {
  return (_ctx?.caes || []).map(({ nome, x, estado, direcao }) => ({ cao: nome, x, estado, direcao }));
}
