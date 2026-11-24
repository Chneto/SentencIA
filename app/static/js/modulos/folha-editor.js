// SentencIA — Módulo ES: folha-editor.js (TASK-015)
import { MARCADOR_RE, hooks, state } from "./estado.js?v=2.2.0";
import { $, escapeHtml, showToast } from "./dom.js?v=2.2.0";
import { ApiError, apiFetch, handleApiError, lerJson, postJson } from "./api.js?v=2.2.0";

const SELETOR_BLOCOS = "p,li,div,h1,h2,h3,h4,h5,h6,blockquote";
const ORDEM_POS_RE = /^(?:\d+\.|[a-z]\))\s+(?:Lance-se|Oficie-se|Expe\u00e7a-se|Cumpra-se|Intime-se|Intimem-se|Comunique-se|Proceda-se|Suspendam-se|Mantenham-se|Concedo|Registre-se|Arquivem-se)\b/i;
const MARGENS_ABNT = { superior: 3, esquerda: 3, inferior: 2, direita: 2 };
const MARGENS_PRESETS = {
  abnt: MARGENS_ABNT,
  lo: { superior: 2, esquerda: 2, inferior: 2, direita: 2 },
  moderada: { superior: 2.54, esquerda: 1.91, inferior: 2.54, direita: 1.91 },
  estreita: { superior: 1.27, esquerda: 1.27, inferior: 1.27, direita: 1.27 },
  larga: { superior: 2.54, esquerda: 5.08, inferior: 2.54, direita: 5.08 },
};

// Plano B (texto puro sem HTML do servidor): um par\u00e1grafo justificado por bloco.
function textoPuroParaHtml(texto) {
  return String(texto || "")
    .split(/\n{2,}/)
    .map((t) => t.trim())
    .filter(Boolean)
    .map((t) => `<p style="text-align:justify;text-indent:1.25cm;line-height:1.5;margin:0 0 4pt;">${escapeHtml(t).replace(/\n/g, "<br />")}</p>`)
    .join("");
}

// Troca, nos n\u00f3s de texto da folha, as ocorr\u00eancias de `re` por elementos criados por `criar`.
function substituirNoTexto(raiz, re, criar, rejeitar = ".id-badge") {
  const walker = document.createTreeWalker(raiz, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement && n.parentElement.closest(rejeitar) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  const nos = [];
  while (walker.nextNode()) nos.push(walker.currentNode);
  nos.forEach((no) => {
    const txt = no.nodeValue;
    re.lastIndex = 0;
    if (!re.test(txt)) return;
    re.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let ultimo = 0;
    let m;
    while ((m = re.exec(txt))) {
      if (m.index > ultimo) frag.append(txt.slice(ultimo, m.index));
      frag.append(criar(m[0], m));
      ultimo = m.index + m[0].length;
    }
    if (ultimo < txt.length) frag.append(txt.slice(ultimo));
    no.replaceWith(frag);
  });
}

// Destaca marcadores [COMPLETAR PELO JU\u00cdZO: ...] e IDs de pe\u00e7as/m\u00eddias (badges clic\u00e1veis).
// Marcadores primeiro: um [FONTE: ID-...] dentro de um marcador fica aninhado DENTRO do <mark>.
function decorarFolha(body) {
  const contador = { n: 0 };
  substituirNoTexto(body, new RegExp(MARCADOR_RE.source, "g"), (m) => {
    const mark = document.createElement("mark");
    mark.className = "marcador-pendente";
    mark.dataset.marcadorIdx = String(contador.n++);
    mark.title = "Trecho a completar pelo ju\u00edzo";
    mark.textContent = m;
    return mark;
  }, ".id-badge, mark.marcador-pendente"); // idempotente: não reembrulha marcadores já destacados
  substituirNoTexto(body, /(\[FONTE:\s*([^\]]+)\]|ID-[A-Za-z0-9-]+(?:\s*\/\s*ID-[A-Za-z0-9-]+)?)/g, (full, m) => {
    const inner = m[2] || full;
    const span = document.createElement("span");
    span.className = "id-badge";
    span.setAttribute("contenteditable", "false");
    span.dataset.inspectId = inner.replace(/^FONTE:\s*/i, "").trim().split("/")[0].trim();
    span.textContent = full;
    return span;
  });
}

function renumerarMarcadores() {
  const body = $("sentenceRenderBody");
  let n = 0;
  body.querySelectorAll("mark.marcador-pendente").forEach((mark) => {
    if (!new RegExp(MARCADOR_RE.source).test(mark.textContent)) {
      // marcador j\u00e1 completado/editado pelo ju\u00edzo: remove s\u00f3 o realce, preservando o texto
      mark.replaceWith(...mark.childNodes);
      return;
    }
    mark.dataset.marcadorIdx = String(n++);
  });
  body.normalize();
}

function htmlLimpoDaFolha() {
  const clone = $("sentenceRenderBody").cloneNode(true);
  let el;
  while ((el = clone.querySelector(".id-badge, mark.marcador-pendente"))) el.replaceWith(...el.childNodes);
  clone.normalize();
  return clone.innerHTML;
}

function blocosDaFolha() {
  const body = $("sentenceRenderBody");
  return Array.from(body.querySelectorAll(SELETOR_BLOCOS)).filter(
    (el) => !el.querySelector(SELETOR_BLOCOS) && !el.closest(".empty-studio-placeholder")
  );
}

function recuoEsquerdoCm(el) {
  const m = /(-?[\d.]+)\s*cm/.exec(el.style.marginLeft || "");
  return m ? parseFloat(m[1]) : 0;
}

// Texto de um n\u00f3 sem depender do layout (innerText muda com a aba oculta): <br> vira quebra de linha.
function textoDoNo(no) {
  if (no.nodeType === Node.TEXT_NODE) return no.nodeValue;
  if (no.nodeType !== Node.ELEMENT_NODE) return "";
  if (no.tagName === "BR") return "\n";
  return Array.from(no.childNodes).map(textoDoNo).join("");
}

function extractEditedSentenceText() {
  const body = $("sentenceRenderBody");
  const blocos = blocosDaFolha();
  if (!blocos.length) return textoDoNo(body).replace(/\u00a0/g, " ").trim();
  const partes = [];
  blocos.forEach((el) => {
    let txt = textoDoNo(el).replace(/\u00a0/g, " ").replace(/[ \t]*\n[ \t]*/g, "\n").trim();
    if (!txt) return;
    if (el.tagName === "LI" && el.parentElement) {
      const irmaos = Array.from(el.parentElement.children).filter((c) => c.tagName === "LI");
      txt = (el.parentElement.tagName === "OL" ? `${irmaos.indexOf(el) + 1}. ` : "\u2022 ") + txt;
    }
    if (recuoEsquerdoCm(el) >= 3 && el.tagName !== "LI") txt = `    ${txt}`; // cita\u00e7\u00e3o longa (ABNT)
    partes.push(txt);
  });
  // O cabe\u00e7alho (tribunal, ju\u00edzo, identifica\u00e7\u00e3o) fica num s\u00f3 bloco, como no texto do motor, e as
  // ordens do p\u00f3s-julgamento (1. Lance-se...; a) Comunique-se...) colam no par\u00e1grafo que as introduz.
  const idxSentenca = partes.findIndex((t) => t === "SENTEN\u00c7A");
  return partes
    .map((t, i) => {
      if (i === 0) return t;
      const sep =
        (idxSentenca > 0 && i < idxSentenca) || (ORDEM_POS_RE.test(t) && (partes[i - 1].endsWith(":") || ORDEM_POS_RE.test(partes[i - 1])))
          ? "\n"
          : "\n\n";
      return sep + t;
    })
    .join("");
}

function normalizarFormatacaoFolha(bruta) {
  const m = { ...MARGENS_ABNT };
  const inf = bruta && bruta.margens_cm;
  if (inf) {
    Object.keys(m).forEach((k) => {
      const v = parseFloat(inf[k]);
      if (Number.isFinite(v)) m[k] = Math.min(6, Math.max(0.5, Math.round(v * 100) / 100));
    });
  }
  const saida = { margens_cm: m, numerar_paginas: !(bruta && bruta.numerar_paginas === false) };
  if (bruta && typeof bruta.fonte === "string" && bruta.fonte) saida.fonte = bruta.fonte;
  const tam = bruta ? parseFloat(bruta.tamanho_pt) : NaN;
  if (Number.isFinite(tam)) saida.tamanho_pt = Math.min(18, Math.max(8, tam));
  return saida;
}

function aplicarMargensFolha() {
  const body = $("sentenceRenderBody");
  const f = state.formatacao || {};
  body.style.fontFamily = f.fonte ? `"${f.fonte}", "Times New Roman", Times, serif` : "";
  body.style.fontSize = f.tamanho_pt ? `${f.tamanho_pt}pt` : "";
  const m = (f.margens_cm) || MARGENS_ABNT;
  body.style.setProperty("--folha-mt", `${m.superior}cm`);
  body.style.setProperty("--folha-mb", `${m.inferior}cm`);
  body.style.setProperty("--folha-me", `${m.esquerda}cm`);
  body.style.setProperty("--folha-md", `${m.direita}cm`);
}

// "Foto" da folha logo ap\u00f3s renderizar/salvar: permite saber se o usu\u00e1rio mudou texto ou s\u00f3 formata\u00e7\u00e3o.
function capturarBaseDaFolha() {
  state.baseTexto = extractEditedSentenceText();
  state.baseHtml = htmlLimpoDaFolha();
  state.baseFormatacao = JSON.stringify(state.formatacao);
  state.editorSujo = false;
  marcarSujo(false);
}

function folhaMudou() {
  return (
    htmlLimpoDaFolha() !== state.baseHtml || JSON.stringify(state.formatacao) !== state.baseFormatacao
  );
}

function marcarSujo(sim) {
  state.editorSujo = !!sim;
  const aviso = $("fmtDirty");
  if (aviso) aviso.hidden = !sim;
}

/* --------------------------------------------------------------------------
   BARRA DE FORMATA\u00c7\u00c3O (estilo LibreOffice Writer)
   -------------------------------------------------------------------------- */
const ESTILOS_PARAGRAFO = {
  corpo: { align: "justify", indent: 1.25, left: 0, lh: 1.5, before: 0, after: 4, bold: false, italic: false, size: null },
  secao: { align: "justify", indent: 0, left: 0, lh: 1.5, before: 12, after: 6, bold: true, italic: false, size: null },
  subsecao: { align: "justify", indent: 0, left: 0, lh: 1.5, before: 8, after: 4, bold: false, italic: true, size: null },
  citacao: { align: "justify", indent: 0, left: 4, lh: 1, before: 6, after: 6, bold: false, italic: false, size: 10 },
  ordem: { align: "justify", indent: 0, left: 1.25, lh: 1.5, before: 2, after: 4, bold: false, italic: false, size: null },
  lista: { align: "justify", indent: 0, left: 1.25, lh: 1.5, before: 0, after: 2, bold: false, italic: false, size: null },
  centro: { align: "center", indent: 0, left: 0, lh: 1.5, before: 6, after: 12, bold: true, italic: false, size: null },
  data: { align: "right", indent: 0, left: 0, lh: 1.5, before: 12, after: 16, bold: false, italic: false, size: null },
};
const TAMANHO_BRUTO_RE = /xxx-large|-webkit-xxx-large/;

function setBarraFormatacaoAtiva(ativa) {
  const barra = $("fmtToolbar");
  if (barra) barra.dataset.disabled = ativa ? "false" : "true";
}

function folhaEditavel() {
  return $("sentenceRenderBody").getAttribute("contenteditable") === "true";
}

function salvarSelecao() {
  const sel = window.getSelection();
  const body = $("sentenceRenderBody");
  if (sel && sel.rangeCount && body.contains(sel.anchorNode)) state.selecaoSalva = sel.getRangeAt(0).cloneRange();
}

function restaurarSelecao() {
  const body = $("sentenceRenderBody");
  body.focus({ preventScroll: true });
  const sel = window.getSelection();
  if (state.selecaoSalva && (!sel.rangeCount || !body.contains(sel.anchorNode))) {
    sel.removeAllRanges();
    sel.addRange(state.selecaoSalva);
  }
}

function aposEdicao() {
  renumerarMarcadores();
  marcarSujo(folhaMudou());
  salvarSelecao();
  atualizarBarraFormatacao();
}

function execFormato(comando, valor) {
  if (!folhaEditavel()) return;
  restaurarSelecao();
  document.execCommand("styleWithCSS", false, true);
  document.execCommand(comando, false, valor ?? null);
  aposEdicao();
}

function blocosSelecionados() {
  const sel = window.getSelection();
  const body = $("sentenceRenderBody");
  if (!sel.rangeCount || !body.contains(sel.anchorNode)) return [];
  const range = sel.getRangeAt(0);
  return blocosDaFolha().filter((b) => range.intersectsNode(b));
}

function emBlocos(fn) {
  if (!folhaEditavel()) return;
  restaurarSelecao();
  const blocos = blocosSelecionados();
  if (!blocos.length) return;
  blocos.forEach(fn);
  aposEdicao();
}

// Remove negrito/it\u00e1lico/tamanho "diretos" de um par\u00e1grafo (preserva marcadores e badges).
function limparFormatoDireto(bloco) {
  bloco.querySelectorAll("b,strong,i,em,font").forEach((el) => el.replaceWith(...el.childNodes));
  bloco.querySelectorAll("span:not(.id-badge)").forEach((sp) => {
    ["fontWeight", "fontStyle", "fontSize", "fontFamily", "color", "textDecoration"].forEach((p) => (sp.style[p] = ""));
    if (!sp.getAttribute("style")) sp.replaceWith(...sp.childNodes);
  });
  bloco.normalize();
}

function aplicarEstiloParagrafo(nome) {
  const est = ESTILOS_PARAGRAFO[nome];
  if (!est) return;
  emBlocos((b) => {
    limparFormatoDireto(b);
    b.style.textAlign = est.align;
    b.style.textIndent = `${est.indent}cm`;
    b.style.marginLeft = `${est.left}cm`;
    b.style.lineHeight = String(est.lh);
    b.style.marginTop = `${est.before}pt`;
    b.style.marginBottom = `${est.after}pt`;
    if (est.bold || est.italic || est.size) {
      const span = document.createElement("span");
      if (est.bold) span.style.fontWeight = "bold";
      if (est.italic) span.style.fontStyle = "italic";
      if (est.size) span.style.fontSize = `${est.size}pt`;
      span.append(...b.childNodes);
      b.append(span);
    }
  });
}

function aplicarTamanho(pt) {
  if (!folhaEditavel()) return;
  restaurarSelecao();
  document.execCommand("styleWithCSS", false, true);
  document.execCommand("fontSize", false, "7"); // marca provis\u00f3ria; convertida abaixo para pontos exatos
  const body = $("sentenceRenderBody");
  body.querySelectorAll("font[size='7'], span").forEach((el) => {
    if (el.tagName === "FONT") {
      const sp = document.createElement("span");
      sp.style.fontSize = `${pt}pt`;
      sp.append(...el.childNodes);
      el.replaceWith(sp);
    } else if (TAMANHO_BRUTO_RE.test(el.style.fontSize || "")) {
      el.style.fontSize = `${pt}pt`;
    }
  });
  aposEdicao();
}

function ajustarRecuoEsquerdo(delta) {
  emBlocos((b) => {
    const novo = Math.max(0, Math.round((recuoEsquerdoCm(b) + delta) * 100) / 100);
    b.style.marginLeft = `${novo}cm`;
  });
}

function alternarPrimeiraLinha() {
  emBlocos((b) => {
    const atual = parseFloat(b.style.textIndent) || 0;
    b.style.textIndent = atual > 0 ? "0cm" : "1.25cm";
  });
}

function limparFormatacao() {
  if (!folhaEditavel()) return;
  restaurarSelecao();
  document.execCommand("removeFormat", false, null);
  emBlocos((b) => limparFormatoDireto(b));
}

function alinhar(valor) {
  emBlocos((b) => {
    b.style.textAlign = valor;
  });
}

function acaoBarra(acao) {
  switch (acao) {
    case "undo":
    case "redo":
      restaurarSelecao();
      document.execCommand(acao);
      aposEdicao();
      break;
    case "justifyLeft":
      alinhar("left");
      break;
    case "justifyCenter":
      alinhar("center");
      break;
    case "justifyRight":
      alinhar("right");
      break;
    case "justifyFull":
      alinhar("justify");
      break;
    case "recuoMais":
      ajustarRecuoEsquerdo(1.25);
      break;
    case "recuoMenos":
      ajustarRecuoEsquerdo(-1.25);
      break;
    case "primeiraLinha":
      alternarPrimeiraLinha();
      break;
    case "limpar":
      limparFormatacao();
      break;
    default:
      execFormato(acao);
  }
}

function atualizarBarraFormatacao() {
  const barra = $("fmtToolbar");
  if (!barra || barra.dataset.disabled === "true") return;
  const sel = window.getSelection();
  const body = $("sentenceRenderBody");
  if (!sel.rangeCount || !body.contains(sel.anchorNode)) return;
  ["bold", "italic", "underline", "strikeThrough", "superscript", "subscript", "insertUnorderedList", "insertOrderedList"].forEach((cmd) => {
    const btn = barra.querySelector(`[data-fmt="${cmd}"]`);
    let on = false;
    try {
      on = document.queryCommandState(cmd);
    } catch (_) {
      on = false;
    }
    if (btn) btn.classList.toggle("active", on);
  });
  const bloco = blocosSelecionados()[0];
  const alinhamento = bloco ? getComputedStyle(bloco).textAlign.replace("start", "left").replace("-webkit-", "") : "";
  barra.querySelectorAll("[data-align]").forEach((btn) => btn.classList.toggle("active", btn.dataset.align === alinhamento));
  if (bloco) {
    const lh = parseFloat(bloco.style.lineHeight);
    const selLh = $("fmtEntrelinhas");
    if (selLh && Number.isFinite(lh)) {
      const opt = Array.from(selLh.options).find((o) => Math.abs(parseFloat(o.value) - lh) < 0.01);
      if (opt) selLh.value = opt.value;
    }
  }
  const alvo = sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement;
  if (alvo) {
    const cs = getComputedStyle(alvo);
    const pt = Math.round(parseFloat(cs.fontSize) * 0.75);
    const selTam = $("fmtTamanho");
    if (selTam && Array.from(selTam.options).some((o) => o.value === String(pt))) selTam.value = String(pt);
    const fam = cs.fontFamily.split(",")[0].replace(/["']/g, "").trim();
    const selFonte = $("fmtFonte");
    if (selFonte && Array.from(selFonte.options).some((o) => o.value === fam)) selFonte.value = fam;
  }
}

function preencherMargens(m) {
  $("fmtMargemSuperior").value = m.superior;
  $("fmtMargemInferior").value = m.inferior;
  $("fmtMargemEsquerda").value = m.esquerda;
  $("fmtMargemDireita").value = m.direita;
  const igual = (a, b) => ["superior", "inferior", "esquerda", "direita"].every((k) => Math.abs(a[k] - b[k]) < 0.005);
  const preset = Object.keys(MARGENS_PRESETS).find((k) => igual(MARGENS_PRESETS[k], m));
  $("fmtMargensPreset").value = preset || "custom";
}

// Um único popover aberto por vez (Margens, Modelos, Localizar).
function alternarPopover(id, abrir) {
  const pop = $(id);
  const mostrar = abrir ?? pop.hidden;
  document.querySelectorAll(".fmt-popover").forEach((p) => {
    if (p !== pop && !p.hidden) {
      p.hidden = true;
      document.querySelector(`[data-pop-toggle="${p.id}"]`)?.setAttribute("aria-expanded", "false");
    }
  });
  pop.hidden = !mostrar;
  document.querySelector(`[data-pop-toggle="${id}"]`)?.setAttribute("aria-expanded", String(mostrar));
  return mostrar;
}

function alternarPopoverMargens(abrir) {
  const mostrar = alternarPopover("fmtMargensPop", abrir);
  if (mostrar) {
    preencherMargens(state.formatacao.margens_cm);
    $("fmtNumerarPaginas").checked = state.formatacao.numerar_paginas !== false;
  }
}

function aplicarMargensDoPopover() {
  const ler = (id) => parseFloat($(id).value);
  state.formatacao = normalizarFormatacaoFolha({
    ...state.formatacao,
    numerar_paginas: $("fmtNumerarPaginas").checked,
    margens_cm: {
      superior: ler("fmtMargemSuperior"),
      inferior: ler("fmtMargemInferior"),
      esquerda: ler("fmtMargemEsquerda"),
      direita: ler("fmtMargemDireita"),
    },
  });
  aplicarMargensFolha();
  marcarSujo(folhaMudou());
  alternarPopoverMargens(false);
  showToast("P\u00e1gina ajustada: margens e numera\u00e7\u00e3o valem para a folha e para o .docx/.pdf.", "success", 3400);
}

/* --------------------------------------------------------------------------
   MODELOS DE FORMATA\u00c7\u00c3O DO GABINETE
   -------------------------------------------------------------------------- */
async function carregarModelos(selecionar) {
  try {
    const res = await (await apiFetch("/api/formatos")).json();
    state.modelos = res.modelos || [];
  } catch (_) {
    state.modelos = state.modelos || [];
  }
  const sel = $("fmtModeloSelect");
  sel.innerHTML = state.modelos
    .map((m) => `<option value="${escapeHtml(m.nome)}">${escapeHtml(m.nome)}${m.padrao ? "" : " \u2605"}</option>`)
    .join("");
  if (selecionar) sel.value = selecionar;
}

// Entrelinhas e recuo de 1\u00aa linha do corpo do texto, lidos da pr\u00f3pria folha.
function formatoDoCorpo() {
  const corpo = blocosDaFolha().find((b) => (parseFloat(b.style.textIndent) || 0) > 0 && recuoEsquerdoCm(b) === 0);
  return {
    entrelinhas: corpo ? parseFloat(corpo.style.lineHeight) || 1.5 : 1.5,
    recuo_primeira_cm: corpo ? parseFloat(corpo.style.textIndent) || 1.25 : 1.25,
  };
}

function aplicarModelo(modelo) {
  if (!folhaEditavel()) return;
  const antes = formatoDoCorpo();
  state.formatacao = normalizarFormatacaoFolha({
    margens_cm: modelo.margens_cm,
    numerar_paginas: modelo.numerar_paginas,
    fonte: modelo.fonte,
    tamanho_pt: modelo.tamanho_pt,
  });
  // S\u00f3 o texto "de corpo" acompanha o modelo (corpo, ordens, listas); cita\u00e7\u00f5es, t\u00edtulo e assinatura mant\u00eam o seu estilo.
  blocosDaFolha().forEach((b) => {
    const lh = parseFloat(b.style.lineHeight);
    if (Math.abs(lh - antes.entrelinhas) < 0.01) b.style.lineHeight = String(modelo.entrelinhas);
    if (Math.abs((parseFloat(b.style.textIndent) || 0) - antes.recuo_primeira_cm) < 0.01 && recuoEsquerdoCm(b) === 0) {
      b.style.textIndent = `${modelo.recuo_primeira_cm}cm`;
    }
  });
  aplicarMargensFolha();
  aposEdicao();
  alternarPopover("fmtModelosPop", false);
  showToast(`Modelo \u201c${modelo.nome}\u201d aplicado. Confira a folha e exporte quando quiser.`, "success", 3600);
}

async function salvarModeloAtual() {
  const nome = $("fmtModeloNome").value.trim();
  if (!nome) {
    showToast("D\u00ea um nome ao modelo.", "warn");
    return;
  }
  const f = state.formatacao;
  const corpo = formatoDoCorpo();
  try {
    await postJson("/api/formatos", {
      nome,
      fonte: f.fonte || $("fmtFonte").value,
      tamanho_pt: f.tamanho_pt || parseFloat($("fmtTamanho").value) || 12,
      entrelinhas: corpo.entrelinhas,
      recuo_primeira_cm: corpo.recuo_primeira_cm,
      margens_cm: f.margens_cm,
      numerar_paginas: f.numerar_paginas !== false,
    });
    await carregarModelos(nome);
    $("fmtModeloNome").value = "";
    showToast(`Modelo \u201c${nome}\u201d salvo na sua lista (\u2605).`, "success", 3200);
  } catch (err) {
    handleApiError(err);
  }
}

async function excluirModeloSelecionado() {
  const nome = $("fmtModeloSelect").value;
  const modelo = (state.modelos || []).find((m) => m.nome === nome);
  if (!modelo || modelo.padrao) {
    showToast("Os modelos de f\u00e1brica n\u00e3o podem ser exclu\u00eddos.", "warn");
    return;
  }
  try {
    const resp = await apiFetch(`/api/formatos?nome=${encodeURIComponent(nome)}`, { method: "DELETE" });
    if (!resp.ok) throw new ApiError(resp.status, await lerJson(resp));
    await carregarModelos();
    showToast(`Modelo \u201c${nome}\u201d exclu\u00eddo.`, "info", 2800);
  } catch (err) {
    handleApiError(err);
  }
}

/* --------------------------------------------------------------------------
   LOCALIZAR E SUBSTITUIR (Ctrl+H)
   -------------------------------------------------------------------------- */
function indiceDeTexto() {
  const body = $("sentenceRenderBody");
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  const nos = [];
  let texto = "";
  while (walker.nextNode()) {
    const no = walker.currentNode;
    nos.push({ no, ini: texto.length });
    texto += no.nodeValue;
  }
  return { nos, texto };
}

// `fim`: a posição é o FIM de um trecho/cursor — prefere o final do nó anterior ao início do seguinte
// (evita cair em nós de espaço entre parágrafos).
function posParaNo(nos, pos, fim = false) {
  for (let i = nos.length - 1; i >= 0; i--) {
    if (fim ? pos > nos[i].ini || i === 0 : pos >= nos[i].ini) {
      return { node: nos[i].no, offset: Math.min(Math.max(pos - nos[i].ini, 0), nos[i].no.nodeValue.length) };
    }
  }
  return null;
}

function offsetDaSelecao(nos, fim) {
  // Com o foco nos campos do popover a seleção do documento é a do campo: usa a última da folha.
  const sel = window.getSelection();
  const body = $("sentenceRenderBody");
  const r = sel.rangeCount && body.contains(sel.anchorNode) ? sel.getRangeAt(0) : state.selecaoSalva;
  if (!r) return null;
  const alvo = fim ? [r.endContainer, r.endOffset] : [r.startContainer, r.startOffset];
  const item = nos.find((n) => n.no === alvo[0]);
  return item ? item.ini + alvo[1] : null;
}

function achar(direcao, aPartirDe) {
  const termo = $("fmtLocalizarTexto").value;
  if (!termo) return null;
  const cs = $("fmtLocalizarCase").checked;
  const { nos, texto } = indiceDeTexto();
  const palheiro = cs ? texto : texto.toLowerCase();
  const agulha = cs ? termo : termo.toLowerCase();
  let ini;
  if (direcao > 0) {
    const de = aPartirDe ?? offsetDaSelecao(nos, true) ?? 0;
    ini = palheiro.indexOf(agulha, de);
    if (ini < 0) ini = palheiro.indexOf(agulha, 0);
  } else {
    const de = (aPartirDe ?? offsetDaSelecao(nos, false) ?? palheiro.length) - 1;
    ini = de >= 0 ? palheiro.lastIndexOf(agulha, de) : -1;
    if (ini < 0) ini = palheiro.lastIndexOf(agulha);
  }
  if (ini < 0) return null;
  const a = posParaNo(nos, ini);
  const b = posParaNo(nos, ini + agulha.length, true);
  if (!a || !b) return null;
  const range = document.createRange();
  range.setStart(a.node, a.offset);
  range.setEnd(b.node, b.offset);
  return { range, ini, fim: ini + agulha.length, total: contarOcorrencias(palheiro, agulha) };
}

function contarOcorrencias(palheiro, agulha) {
  let n = 0;
  for (let i = palheiro.indexOf(agulha); i >= 0; i = palheiro.indexOf(agulha, i + agulha.length)) n++;
  return n;
}

function mostrarAchado(range) {
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  salvarSelecao();
  if (window.CSS && CSS.highlights && window.Highlight) CSS.highlights.set("fmt-achado", new Highlight(range));
  (range.startContainer.parentElement || $("sentenceRenderBody")).scrollIntoView({ block: "center", behavior: "smooth" });
}

function irParaOcorrencia(direcao) {
  const info = $("fmtLocalizarInfo");
  const r = achar(direcao);
  if (!r) {
    info.textContent = $("fmtLocalizarTexto").value ? "Nenhuma ocorr\u00eancia encontrada." : "Digite o texto a localizar.";
    return null;
  }
  mostrarAchado(r.range);
  info.textContent = `${r.total} ocorr\u00eancia(s) na folha.`;
  return r;
}

function trechoTocaBadge(range) {
  const ini = range.startContainer.parentElement;
  const fim = range.endContainer.parentElement;
  return !!(ini?.closest(".id-badge") || fim?.closest(".id-badge") || range.cloneContents().querySelector(".id-badge"));
}

// Troca a seleção por `texto` (com desfazer nativo) e repõe o espaço que o navegador descarta quando o
// trecho substituído abre um parágrafo e o texto seguinte começava por espaço.
function inserirSubstituindo(texto) {
  let seguinte = "";
  const { nos, texto: antes } = indiceDeTexto();
  const fim = offsetDaSelecao(nos, true);
  if (fim != null) seguinte = antes.charAt(fim);
  document.execCommand("insertText", false, texto);
  if (/[  ]/.test(seguinte)) {
    const { nos: n2, texto: depois } = indiceDeTexto();
    const pos = offsetDaSelecao(n2, true);
    const proximo = pos != null ? depois.charAt(pos) : "";
    if (proximo && !/\s/.test(proximo)) document.execCommand("insertText", false, " ");
  }
}

function substituirAtual() {
  if (!folhaEditavel()) return;
  restaurarSelecao(); // devolve o foco à folha, mantendo o trecho localizado selecionado
  const termo = $("fmtLocalizarTexto").value;
  const sel = window.getSelection();
  const cs = $("fmtLocalizarCase").checked;
  const selecionado = sel.rangeCount ? sel.toString() : "";
  const confere = termo && (cs ? selecionado === termo : selecionado.toLowerCase() === termo.toLowerCase());
  if (!confere) {
    irParaOcorrencia(1); // primeiro posiciona; o pr\u00f3ximo clique substitui
    return;
  }
  if (trechoTocaBadge(sel.getRangeAt(0))) {
    $("fmtLocalizarInfo").textContent = "Esse trecho \u00e9 uma refer\u00eancia a pe\u00e7a/m\u00eddia (ID) e n\u00e3o \u00e9 substitu\u00eddo.";
    irParaOcorrencia(1);
    return;
  }
  inserirSubstituindo($("fmtSubstituirTexto").value);
  aposEdicao();
  irParaOcorrencia(1);
}

function substituirTudo() {
  if (!folhaEditavel()) return;
  const termo = $("fmtLocalizarTexto").value;
  if (!termo) return;
  $("sentenceRenderBody").focus({ preventScroll: true });
  let n = 0;
  let ignorados = 0;
  let desde = 0;
  while (n + ignorados < 5000) {
    const r = achar(1, desde);
    if (!r || r.ini < desde) break; // deu a volta no documento
    if (trechoTocaBadge(r.range)) {
      ignorados++;
      desde = r.fim;
      continue;
    }
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(r.range);
    inserirSubstituindo($("fmtSubstituirTexto").value);
    n++;
    desde = r.ini + $("fmtSubstituirTexto").value.length;
  }
  aposEdicao();
  $("fmtLocalizarInfo").textContent = `${n} substitui\u00e7\u00e3o(\u00f5es)` + (ignorados ? `; ${ignorados} em refer\u00eancias de ID foram preservadas.` : ".");
  if (n) showToast(`${n} substitui\u00e7\u00e3o(\u00f5es) feitas. Desfa\u00e7a com Ctrl+Z se precisar.`, "success", 3600);
}

function abrirLocalizar() {
  const sel = window.getSelection();
  const body = $("sentenceRenderBody");
  const usado = sel.rangeCount && body.contains(sel.anchorNode) ? sel.toString().trim() : "";
  alternarPopover("fmtLocalizarPop", true);
  if (usado && usado.length < 80 && !usado.includes("\n")) $("fmtLocalizarTexto").value = usado;
  $("fmtLocalizarTexto").focus();
  $("fmtLocalizarTexto").select();
}

async function sincronizarFolha() {
  if (!state.lastSentence || !folhaEditavel()) return true;
  if (!folhaMudou()) return true;
  if (extractEditedSentenceText() !== state.baseTexto) {
    showToast("O texto foi alterado: salvando e re-auditando antes de exportar\u2026", "info", 3600);
    return await hooks.reauditarEdicao();
  }
  try {
    const res = await postJson("/api/sentence/save-format", {
      texto_html: htmlLimpoDaFolha(),
      formatacao: state.formatacao,
      revisao_esperada: state.lastSentence.revisao_documento,
    });
    state.lastSentence.revisao_documento = res.revisao_documento;
    if (res.html_pje) state.lastHtmlPje = res.html_pje;
    capturarBaseDaFolha();
    return true;
  } catch (err) {
    handleApiError(err);
    return false;
  }
}

export {
  ESTILOS_PARAGRAFO,
  MARGENS_ABNT,
  MARGENS_PRESETS,
  ORDEM_POS_RE,
  SELETOR_BLOCOS,
  TAMANHO_BRUTO_RE,
  abrirLocalizar,
  acaoBarra,
  achar,
  ajustarRecuoEsquerdo,
  alinhar,
  alternarPopover,
  alternarPopoverMargens,
  alternarPrimeiraLinha,
  aplicarEstiloParagrafo,
  aplicarMargensDoPopover,
  aplicarMargensFolha,
  aplicarModelo,
  aplicarTamanho,
  aposEdicao,
  atualizarBarraFormatacao,
  blocosDaFolha,
  blocosSelecionados,
  capturarBaseDaFolha,
  carregarModelos,
  contarOcorrencias,
  decorarFolha,
  emBlocos,
  excluirModeloSelecionado,
  execFormato,
  extractEditedSentenceText,
  folhaEditavel,
  folhaMudou,
  formatoDoCorpo,
  htmlLimpoDaFolha,
  indiceDeTexto,
  inserirSubstituindo,
  irParaOcorrencia,
  limparFormatacao,
  limparFormatoDireto,
  marcarSujo,
  mostrarAchado,
  normalizarFormatacaoFolha,
  offsetDaSelecao,
  posParaNo,
  preencherMargens,
  recuoEsquerdoCm,
  renumerarMarcadores,
  restaurarSelecao,
  salvarModeloAtual,
  salvarSelecao,
  setBarraFormatacaoAtiva,
  sincronizarFolha,
  substituirAtual,
  substituirNoTexto,
  substituirTudo,
  textoDoNo,
  textoPuroParaHtml,
  trechoTocaBadge,
};
