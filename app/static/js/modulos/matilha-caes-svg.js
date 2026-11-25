// SentencIA — Arte SVG dos cães do tema Matilha (TASK-103): pug e rottweiler de perfil, virados para a direita.
// Desenho original montado com createElementNS (seguro para CSP, sem innerHTML). Cores vêm de variáveis CSS
// (variantes = conjuntos de variáveis) e as poses/animações ficam em css/temas/matilha-caes.css.
// Cada parte móvel é um <g class="mc-p ..."> com um retângulo invisível (.mc-pivo) centrado no ponto de giro:
// com transform-box: fill-box e transform-origin 50% 50%, o giro cai exatamente nesse ponto.

const NS = "http://www.w3.org/2000/svg";

export const POSES = [
  "parado", "andando", "correndo", "sentado", "deitado", "dormindo", "bebendo", "comendo",
  "coco", "feliz", "atento", "carregado", "pulando", "farejando", "tropecando", "latindo",
];

const VARIANTES = { pug: ["abrico", "branco_bege"], rott: ["classico", "atletico_feliz"] };
const LARGURA_VB = 125;
const ALTURA_VB = 100;

let _seq = 0;

function no(tag, attrs = {}, filhos = []) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== null && v !== undefined) el.setAttribute(k, String(v));
  }
  for (const f of filhos) if (f) el.appendChild(f);
  return el;
}

const r1 = (n) => Math.round(n * 100) / 100;

// Grupo com ponto de giro (px, py); o retângulo invisível precisa conter toda a parte.
function pivo(classe, px, py, raio, filhos) {
  return no("g", { class: `mc-p ${classe}` }, [
    no("rect", { class: "mc-pivo", x: r1(px - raio), y: r1(py - raio), width: r1(raio * 2), height: r1(raio * 2) }),
    ...filhos,
  ]);
}

// Dois níveis no mesmo ponto: o externo recebe a pose (estática), o interno a animação.
function pivoDuplo(classe, px, py, raio, filhos) {
  return pivo(classe, px, py, raio * 1.7, [pivo(`${classe}-mov`, px, py, raio, filhos)]);
}

// Forma com contorno; "marcas" são desenhadas recortadas pela própria forma (manchas, sombras).
function forma(ctx, d, cor, marcas = []) {
  if (!marcas.length) return no("path", { d, class: `${cor} tr` });
  const id = `mcc${ctx.n}-${ctx.k++}`;
  return no("g", {}, [
    no("clipPath", { id }, [no("path", { d })]),
    no("path", { d, class: cor }),
    no("g", { "clip-path": `url(#${id})` }, marcas),
    no("path", { d, class: "tr sf" }),
  ]);
}

function elipse(cx, cy, rx, ry, classe) {
  return no("ellipse", { cx, cy, rx, ry, class: classe });
}

function traco(d, classe = "tr sf") {
  return no("path", { d, class: classe });
}

// ---------- pernas ----------

// Perna reta com pata arredondada apontando para a frente (x = centro da perna).
function pernaReta(x, y0, y1, w) {
  const e = r1(x - w / 2);
  const d = r1(x + w / 2);
  return `M${e} ${y0 - 8}L${r1(e + 0.3)} ${y1 - 3.2}Q${r1(e + 0.3)} ${y1} ${r1(e + 3.3)} ${y1}` +
    `L${r1(d + 1.8)} ${y1}Q${r1(d + 4.4)} ${y1} ${r1(d + 3.6)} ${r1(y1 - 2.7)}` +
    `Q${r1(d + 2.8)} ${r1(y1 - 4.8)} ${d} ${r1(y1 - 5.2)}L${d} ${y0 - 8}Z`;
}

// Perna traseira com coxa e jarrete (rottweiler).
function pernaTras(x, y0, y1, s = 1) {
  const p = (dx) => r1(x + dx * s);
  return `M${p(-6)} ${y0 - 6}C${p(-9.5)} ${y0 + 11} ${p(-7.5)} ${y1 - 17} ${p(-5.5)} ${y1 - 11}` +
    `L${p(-4)} ${y1 - 3}Q${p(-4)} ${y1} ${p(-1)} ${y1}L${p(5)} ${y1}Q${p(7.8)} ${y1} ${p(7)} ${r1(y1 - 2.7)}` +
    `Q${p(6.2)} ${r1(y1 - 4.7)} ${p(3.4)} ${y1 - 5}L${p(3)} ${y1 - 10}` +
    `C${p(4)} ${y1 - 17} ${p(9)} ${y0 + 12} ${p(8)} ${y0 - 6}Z`;
}

function perna(ctx, classe, d, x, y0, y1, cor, alturaMarca, corMarca, dedoX) {
  const marcas = alturaMarca
    ? [no("rect", { x: x - 14, y: y1 - alturaMarca, width: 30, height: alturaMarca + 2, class: corMarca })]
    : [];
  const dedos = traco(`M${r1(dedoX)} ${y1 - 0.4}v-2.1M${r1(dedoX + 2.4)} ${y1 - 0.4}v-1.9`, "tr sf mc-dedos");
  return pivo(`mc-perna ${classe}`, x, y0, y1 - y0 + 8, [forma(ctx, d, cor, marcas), dedos]);
}

// ---------- olhos ----------

function olho(cx, cy, r, raca, classe) {
  const partes = raca === "pug"
    ? [
        no("circle", { cx, cy, r, class: "f-branco tr mc-olho-borda" }),
        no("circle", { cx: r1(cx + r * 0.2), cy: r1(cy + r * 0.05), r: r1(r * 0.8), class: "f-iris" }),
        no("circle", { cx: r1(cx + r * 0.25), cy: r1(cy + r * 0.05), r: r1(r * 0.45), class: "f-pupila" }),
      ]
    : [
        no("circle", { cx, cy, r, class: "f-iris tr mc-olho-borda" }),
        no("circle", { cx: r1(cx + r * 0.15), cy, r: r1(r * 0.58), class: "f-pupila" }),
      ];
  partes.push(
    no("circle", { cx: r1(cx + r * 0.35), cy: r1(cy - r * 0.35), r: r1(r * 0.3), class: "f-brilho" }),
    no("circle", { cx: r1(cx - r * 0.25), cy: r1(cy + r * 0.38), r: r1(r * 0.13), class: "f-brilho" }),
  );
  const palpebra = pivo("mc-palpebra", cx, r1(cy - r), r1(2 * r + 0.6), [
    no("circle", { cx, cy, r: r1(r + 1), class: "f-palpebra" }),
    traco(`M${r1(cx - r * 0.9)} ${r1(cy + r * 0.05)}Q${cx} ${r1(cy + r * 0.85)} ${r1(cx + r * 0.9)} ${r1(cy + r * 0.05)}`,
      "sf mc-palpebra-linha"),
  ]);
  return no("g", { class: `mc-olho ${classe}` }, [...partes, palpebra]);
}

// Cauda desenhada como traço grosso (contorno embaixo, pelo em cima).
function cauda(d, largura, px, py, raio) {
  return pivo("mc-cauda", px, py, raio, [
    no("path", { d, class: "sf mc-cauda-borda", "stroke-width": r1(largura + 2.8) }),
    no("path", { d, class: "sf mc-cauda-pelo", "stroke-width": largura }),
  ]);
}

// ---------- pug ----------

function montarPug(ctx) {
  const Y0 = 76;
  const Y1 = 98;
  const pernas = [
    perna(ctx, "mc-pt-longe", pernaReta(35, Y0, Y1, 10), 35, Y0, Y1, "f-sombra", 0, "", 37),
    perna(ctx, "mc-pf-longe", pernaReta(77, Y0, Y1, 10), 77, Y0, Y1, "f-sombra", 0, "", 79),
    perna(ctx, "mc-pt-perto", pernaReta(42, Y0, Y1, 11), 42, Y0, Y1, "f-pelo", 0, "", 44.5),
    perna(ctx, "mc-pf-perto", pernaReta(85, Y0, Y1, 11), 85, Y0, Y1, "f-pelo", 0, "", 87.5),
  ];
  const dTorso = "M30 61C29 51 43 48 58 49C72 50 84 47 92 54C99 60 99 75 91 82C81 89 51 89 38 85C27 81 26 69 30 61Z";
  const torso = pivo("mc-torso", 62, 88, 62, [
    forma(ctx, dTorso, "f-pelo", [
      elipse(60, 93, 44, 12, "f-sombra mc-sombreado"),
      elipse(62, 51, 26, 4.5, "f-claro mc-brilho-pelo"),
    ]),
    traco("M36 74C40 70 47 71 50 77", "sf mc-dobra"),
  ]);
  const rabo = cauda("M33 56C25 51 25 40 33 38.5C41 37 45 45 40 49C36.5 51.5 33.5 47.5 36 45.5", 5.6, 33, 50, 24);
  const dCabeca = "M82 44C82 32 90 26 99 26C109 26 115 33 115 42C115 48 112 54 106 56C100 60 92 62 88 58C83 54 82 50 82 44Z";
  const mandibula = pivo("mc-mandibula", 104.5, 55.3, 16, [
    no("path", { d: "M104 55.4L116.5 54.6L114.6 47.4Z", class: "f-boca" }),
    no("path", { d: "M103 55C109 57.6 113.5 57 116.4 54.8C115 60.4 110.5 61.4 108.5 61.3C104.8 61 103.4 58.6 103 55Z",
      class: "f-mascara tr" }),
    pivo("mc-lingua", 110, 57, 11, [
      no("path", { d: "M106.6 56.6Q106.8 65.2 110.1 65.2Q113.4 65.2 113.6 56.6Z", class: "f-lingua tr mc-lingua-borda" }),
      traco("M110.1 58.4V62.6", "sf mc-lingua-risca"),
    ]),
  ]);
  const cabeca = pivoDuplo("mc-cabeca", 90, 56, 52, [
    pivo("mc-orelha mc-orelha-longe", 104, 27.5, 10, [
      no("path", { d: "M100.5 28.5C102 23.5 108 22.5 110.5 26.5C110 30 106 31.5 102 30.5Z", class: "f-orelha tr" }),
    ]),
    mandibula,
    forma(ctx, dCabeca, "f-pelo", [
      elipse(110, 50.5, 10, 9.5, "f-mascara mc-mascara-face"),
      elipse(94, 57, 12, 5, "f-sombra mc-sombreado"),
      elipse(96, 31, 10, 4, "f-claro mc-brilho-pelo"),
    ]),
    elipse(110, 49.2, 7.6, 6.8, "f-focinho tr"),
    elipse(109.5, 45.4, 4.6, 1.8, "f-claro mc-brilho-focinho"),
    traco("M104.5 44.2Q109 41.6 113.2 42.6", "sf mc-ruga-focinho"),
    traco("M115.2 47Q115.5 51.2 112.4 52.6", "sf mc-ruga-focinho"),
    elipse(115.4, 44.6, 3.2, 2.4, "f-nariz tr mc-nariz"),
    elipse(114.4, 43.8, 1.1, 0.65, "f-brilho"),
    traco("M93 33.6Q99.5 30.6 106 33.4M96 37Q101 34.8 106.5 37.2M90.5 37.4Q92.5 35.6 95 35.6", "sf mc-ruga"),
    olho(100, 42.2, 5, "pug", "mc-olho-perto"),
    olho(110.6, 39.6, 3.2, "pug", "mc-olho-longe"),
    pivo("mc-orelha mc-orelha-perto", 89.5, 30, 13, [
      no("path", { d: "M85.6 31.4C85 26 91.6 23.4 96.4 27C97.6 31 94.4 36.4 90.4 37.6C87 36.8 85.8 34.4 85.6 31.4Z",
        class: "f-orelha tr" }),
      traco("M88.6 30.6Q91.6 30 93.6 32", "sf mc-orelha-dobra"),
    ]),
  ]);
  const pescoco = pescocoCom(86, 66, "M80 52C83 46 92 46 96 52L99 62C95 69 86 69 81 63Z",
    "M83 50.5Q85.5 59.5 93.5 65.5", [92, 68.4], cabeca);
  const anca = ancaCom(ctx, {
    pata: "M45 92L58.5 92Q63 92 63 95Q63 98 58.5 98L45 98Z", dedos: "M58.2 97.6v-2.1M60.6 97.2v-1.8",
    coxa: "M26.5 86C26.5 77 34 73 42 74C51 75 55.5 82.5 54.5 89.5C53.5 96 47 98 39 98C31 98 26.5 93 26.5 86Z",
    dobra: "M38 75.2C48 75.4 54.8 82.6 54.5 89.5C54.3 94 52 96.6 48.6 97.6",
    borda: "M29.6 78C27.4 80.6 26.5 83.4 26.5 86.6C26.5 93.6 31.4 98 39 98L49 98",
  });
  return { pernas, torso, caudaAtras: null, caudaFrente: rabo, pescoco, anca, tronco: [85, 76] };
}

// Pescoço com coleira; a cabeça vai dentro, então girar o pescoço leva a cabeça junto.
function pescocoCom(px, py, dForma, dColeira, [plx, ply], cabeca) {
  return pivo("mc-pescoco", px, py, 150, [
    no("path", { d: dForma, class: "f-pelo" }),
    traco(dColeira, "sf mc-coleira"),
    no("circle", { cx: plx, cy: ply, r: 2.3, class: "f-placa tr mc-placa" }),
    cabeca,
  ]);
}

// Coxa dobrada (sentado/deitado): preenchimento sem contorno, para fundir com o corpo, mais dobra e borda externa.
function ancaCom(ctx, { pata, dedos, coxa, dobra, borda, marcaPata = [] }) {
  return pivo("mc-anca", 44, 86, 34, [
    forma(ctx, pata, "f-pelo", marcaPata),
    traco(dedos),
    no("path", { d: coxa, class: "f-pelo" }),
    traco(dobra, "sf mc-dobra-coxa"),
    traco(borda),
  ]);
}

// ---------- rottweiler ----------

function montarRott(ctx, atletico) {
  const Y0 = atletico ? 60 : 62;
  const Y1 = 98;
  const w = atletico ? 8.2 : 10;
  const s = atletico ? 0.98 : 1.16;
  const tan = 12;
  const pernas = [
    perna(ctx, "mc-pt-longe", pernaTras(36, Y0, Y1, s), 36, Y0, Y1, "f-sombra", tan, "f-marca-sombra", 37),
    perna(ctx, "mc-pf-longe", pernaReta(81, Y0, Y1, w), 81, Y0, Y1, "f-sombra", tan, "f-marca-sombra", 82.4),
    perna(ctx, "mc-pt-perto", pernaTras(43, Y0, Y1, s), 43, Y0, Y1, "f-pelo", tan, "f-marca", 44),
    perna(ctx, "mc-pf-perto", pernaReta(89, Y0, Y1, w), 89, Y0, Y1, "f-pelo", tan, "f-marca", 90.4),
  ];
  const dTorso = atletico
    ? "M32 51C31 43 42 40 55 41C67 42 78 38 88 40C97 42 100 54 95 65C91 72 81 73 72 70C63 67 53 68 45 67C35 66 29 59 32 51Z"
    : "M30 50C29 41 40 38 54 39C66 40 78 36 88 38C99 40 104 54 98 67C94 76 82 78 72 75C62 72 52 73 44 72C33 71 27 61 30 50Z";
  const torso = pivo("mc-torso", 64, 77, 62, [
    forma(ctx, dTorso, "f-pelo", [
      no("path", { d: "M90 51C99 52 103 63 97 73C91 71 87 62 90 51Z", class: "f-marca" }),
      elipse(60, 43, 24, 4, "f-claro mc-brilho-pelo"),
      elipse(60, 80, 40, 8, "f-sombra mc-sombreado"),
    ]),
    traco(atletico ? "M35 53C42 50 49 56 47 65" : "M34 52C42 48 51 56 49 68", "sf mc-musculo"),
    traco(atletico ? "M83 47C79 53 81 60 85 64" : "M84 46C79 53 81 62 86 67", "sf mc-musculo"),
  ]);
  const rabo = cauda("M31.5 45Q28 41.6 26.2 37.4", atletico ? 4.6 : 5.6, 31, 44, 14);
  const dCabeca = "M84 32C83 21 90 15 99 15C106 15 110.5 18.5 111.5 23L119 24.4C121.8 24.9 122.9 27 122.6 29.8L122.2 33.2" +
    "C119 35.8 111 36.6 106.5 36.8C100.5 38.6 95 44.5 90 45.5C86 43.5 84 38 84 32Z";
  const mandibula = pivo("mc-mandibula", 105, 37, 19, [
    no("path", { d: "M105 37.2L120 34.8L118.4 28.8Z", class: "f-boca" }),
    no("path", { d: "M104 36.2L120.4 35Q120.6 37.6 117.6 39.2Q111.5 41.2 104.6 40.2Z", class: "f-marca tr mc-queixo" }),
    pivo("mc-lingua", 112.5, 38.5, 12, [
      no("path", { d: "M108.4 38Q108.6 47.6 112.6 47.6Q116.6 47.6 116.2 38Z", class: "f-lingua tr mc-lingua-borda" }),
      traco("M112.4 40V44.6", "sf mc-lingua-risca"),
    ]),
  ]);
  const cabeca = pivoDuplo("mc-cabeca", 89, 40, 56, [
    pivo("mc-orelha mc-orelha-longe", 104, 16.5, 10, [
      no("path", { d: "M100.5 17.5C103 12.8 108.6 13.6 109.2 17.6C106.8 19 103.6 19.2 100.5 17.5Z", class: "f-orelha tr" }),
    ]),
    mandibula,
    forma(ctx, dCabeca, "f-pelo", [
      no("path", { d: "M105 29C110 28.5 117 29 125 29.5L125 48L100 48C99.5 40 101 31.5 105 29Z", class: "f-marca" }),
      elipse(98.5, 36, 4.6, 3.4, "f-marca"),
      elipse(96, 19, 10, 3, "f-claro mc-brilho-pelo"),
    ]),
    traco("M107 29.2Q112.5 28.2 117.6 28.6", "sf mc-ruga-focinho"),
    elipse(121, 27.2, 3.2, 2.6, "f-nariz tr mc-nariz"),
    elipse(120.2, 26.3, 1, 0.6, "f-brilho"),
    elipse(103.2, 20.6, 2.7, 1.7, "f-marca"),
    olho(104, 25, 3, "rott", "mc-olho-perto"),
    traco("M100.6 22.4Q104 21 107.6 23.2", "sf mc-sobrancelha"),
    pivo("mc-orelha mc-orelha-perto", 92, 19, 18, [
      no("path", { d: "M86.4 20.6C89.4 16.8 96.6 16.8 99.2 20.4C99 27 96.4 32 93 35.4C89.4 33 86.6 27.2 86.4 20.6Z",
        class: "f-orelha tr" }),
    ]),
  ]);
  const pescoco = pescocoCom(82, 54,
    atletico ? "M78 47C80 35 85 28 92 28L101 39C101 44 99 48 97 52L82 54Z" : "M76 47C78 34 84 26 92 26L102 39C102 45 100 49 98 53L80 55Z",
    atletico ? "M87.5 31.5Q90 42 97 50" : "M86 30Q89 42.5 97.5 51", [97.6, 54], cabeca);
  const anca = ancaCom(ctx, {
    pata: "M51 90L66 90Q71 90 71 94Q71 98 66 98L51 98Z", dedos: "M66.2 97.6v-2.1M68.6 97.2v-1.8",
    marcaPata: [no("rect", { x: 48, y: 88, width: 26, height: 12, class: "f-marca" })],
    coxa: "M29 85C28 73 37 68 47 69C58 70 62 80 60 89C58 96 51 98 43 98C34 98 29 93 29 85Z",
    dobra: "M41 69.5C53 69 61 79 60 89C59.5 93.5 57 96.5 53 97.5",
    borda: "M33 74C30 77 29 81 29 86C29 93 34 98 42 98L53 98",
  });
  return { pernas, torso, caudaAtras: rabo, caudaFrente: null, pescoco, anca, tronco: [89, 62] };
}

// ---------- montagem ----------

function montar(svg, tipo, variante) {
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  const ctx = { n: ++_seq, k: 0 };
  const g = tipo === "rott" ? montarRott(ctx, variante === "atletico_feliz") : montarPug(ctx);
  const [tx, ty] = g.tronco;
  const tronco = pivoDuplo("mc-tronco", tx, ty, 300, [
    g.pernas[0], g.pernas[1], g.pernas[2], g.pernas[3],
    g.caudaAtras, g.torso, g.caudaFrente, g.pescoco,
  ]);
  const cao = pivoDuplo("mc-cao", 62, 98, 800, [tronco, g.anca]);
  const flip = no("g", { class: "mc-flip" }, [
    no("ellipse", { class: "mc-sombra", cx: 62, cy: 97.6, rx: tipo === "rott" ? 44 : 40, ry: 3.2 }),
    cao,
  ]);
  svg.appendChild(flip);
  return flip;
}

function normalizarTipo(tipo) {
  return tipo === "rott" ? "rott" : "pug";
}

function normalizarVariante(tipo, variante) {
  const lista = VARIANTES[tipo];
  return lista.includes(variante) ? variante : lista[0];
}

function normalizarPose(tipo, pose) {
  if (tipo === "rott" && pose === "tropecando") return "andando";
  return POSES.includes(pose) ? pose : "parado";
}

export function criarCao(tipo, variante, { tamanho = 80 } = {}) {
  const raca = normalizarTipo(tipo);
  let varAtual = normalizarVariante(raca, variante);
  let poseAtual = "parado";
  let direcao = "direita";
  let timerPiscar = 0;
  const altura = Math.max(1, Math.round(Number(tamanho) || 80));

  const el = no("svg", {
    xmlns: NS,
    viewBox: `0 0 ${LARGURA_VB} ${ALTURA_VB}`,
    width: Math.round(altura * 1.25),
    height: altura,
    class: `mcao mcao-${raca}`,
    "aria-hidden": "true",
    focusable: "false",
    "data-variante": varAtual,
    "data-pose": poseAtual,
    "data-direcao": direcao,
  });
  let flip = montar(el, raca, varAtual);

  function aplicarDirecao() {
    if (direcao === "esquerda") flip.setAttribute("transform", `translate(${LARGURA_VB} 0) scale(-1 1)`);
    else flip.removeAttribute("transform");
    el.setAttribute("data-direcao", direcao);
  }

  return {
    el,
    definirPose(pose) {
      poseAtual = normalizarPose(raca, pose);
      el.setAttribute("data-pose", poseAtual);
    },
    get pose() {
      return poseAtual;
    },
    definirDirecao(dir) {
      direcao = dir === "esquerda" ? "esquerda" : "direita";
      aplicarDirecao();
    },
    definirVariante(nova) {
      const v = normalizarVariante(raca, nova);
      if (v === varAtual) return;
      varAtual = v;
      el.setAttribute("data-variante", v);
      flip = montar(el, raca, v);
      aplicarDirecao();
    },
    piscar() {
      el.classList.remove("mc-piscando");
      void el.getBoundingClientRect(); // reinicia a animação
      el.classList.add("mc-piscando");
      clearTimeout(timerPiscar);
      timerPiscar = setTimeout(() => el.classList.remove("mc-piscando"), 260);
    },
    destruir() {
      clearTimeout(timerPiscar);
      el.remove();
    },
  };
}
