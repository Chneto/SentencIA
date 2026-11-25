// SentencIA — Módulo ES: folha-ferramentas.js (TASK-015)
import { state } from "./estado.js?v=2.5.0";
import { $, closeModal, escapeHtml, openModal, showToast } from "./dom.js?v=2.5.0";
import { ApiError, apiFetch, handleApiError, lerJson, postJson } from "./api.js?v=2.5.0";
import { analisarFalas } from "./estacao-analise.js?v=2.5.0";
import { MARGENS_PRESETS, abrirLocalizar, acaoBarra, alternarPopover, alternarPopoverMargens, aplicarEstiloParagrafo, aplicarMargensDoPopover, aplicarMargensFolha, aplicarModelo, aplicarTamanho, aposEdicao, atualizarBarraFormatacao, carregarModelos, decorarFolha, emBlocos, excluirModeloSelecionado, execFormato, extractEditedSentenceText, folhaEditavel, folhaMudou, indiceDeTexto, inserirSubstituindo, irParaOcorrencia, marcarSujo, mostrarAchado, normalizarFormatacaoFolha, offsetDaSelecao, posParaNo, preencherMargens, renumerarMarcadores, restaurarSelecao, salvarModeloAtual, salvarSelecao, substituirAtual, substituirTudo, trechoTocaBadge } from "./folha-editor.js?v=2.5.0";

async function carregarTrechos() {
  try {
    state.trechos = (await (await apiFetch("/api/trechos")).json()).trechos || [];
  } catch (_) {
    state.trechos = state.trechos || [];
  }
  renderTrechos();
}

function renderTrechos() {
  const lista = $("fmtTrechosLista");
  const q = $("fmtTrechosBusca").value.trim().toLowerCase();
  const itens = (state.trechos || []).filter(
    (t) => !q || `${t.titulo} ${t.atalho} ${t.categoria} ${t.texto}`.toLowerCase().includes(q)
  );
  if (!itens.length) {
    lista.innerHTML = `<p class="muted" style="margin:8px; font-size:12px;">Nenhum trecho encontrado.</p>`;
    return;
  }
  let cat = null;
  lista.innerHTML = itens
    .map((t) => {
      const cab = t.categoria !== cat ? `<div class="trechos-cat">${escapeHtml(t.categoria)}</div>` : "";
      cat = t.categoria;
      const acoes = t.padrao
        ? ""
        : `<button type="button" class="fmt-btn" data-trecho-editar="${escapeHtml(t.id)}" title="Editar" aria-label="Editar trecho">✎</button>
           <button type="button" class="fmt-btn" data-trecho-excluir="${escapeHtml(t.id)}" title="Excluir" aria-label="Excluir trecho">×</button>`;
      return `${cab}<div class="trecho-item" role="listitem">
        <button type="button" class="trecho-usar" data-trecho-usar="${escapeHtml(t.id)}" title="${escapeHtml(t.texto.slice(0, 240))}">
          <span>${escapeHtml(t.titulo)}</span>${t.atalho ? `<span class="trecho-atalho">/${escapeHtml(t.atalho)}</span>` : ""}
        </button>${acoes}</div>`;
    })
    .join("");
}

// Insere `texto` na posição do cursor (ou no lugar da seleção) e destaca marcadores/IDs novos.
function inserirTexto(texto) {
  if (!folhaEditavel()) {
    showToast("Gere a minuta para editar a folha.", "warn");
    return;
  }
  restaurarSelecao();
  document.execCommand("insertText", false, texto);
  const { nos } = indiceDeTexto();
  const pos = offsetDaSelecao(nos, true);
  decorarFolha($("sentenceRenderBody"));
  if (pos != null) {
    const alvo = posParaNo(indiceDeTexto().nos, pos, true);
    if (alvo) {
      const r = document.createRange();
      r.setStart(alvo.node, alvo.offset);
      r.collapse(true);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(r);
    }
  }
  aposEdicao();
}

function usarTrecho(id) {
  const t = (state.trechos || []).find((x) => x.id === id);
  if (!t) return;
  inserirTexto(t.texto);
  alternarPopover("fmtTrechosPop", false);
}

// `/atalho` + Tab: expande o trecho cujo atalho está imediatamente antes do cursor.
function expandirAtalhoNoCursor() {
  const sel = window.getSelection();
  if (!sel.rangeCount || !sel.isCollapsed || sel.anchorNode.nodeType !== Node.TEXT_NODE) return false;
  const no = sel.anchorNode;
  const antes = no.nodeValue.slice(0, sel.anchorOffset);
  const m = /(?:^|\s)\/([a-z0-9_-]{2,24})$/i.exec(antes);
  if (!m) return false;
  const t = (state.trechos || []).find((x) => x.atalho && x.atalho === m[1].toLowerCase());
  if (!t) return false;
  const r = document.createRange();
  r.setStart(no, sel.anchorOffset - m[1].length - 1);
  r.setEnd(no, sel.anchorOffset);
  sel.removeAllRanges();
  sel.addRange(r);
  inserirTexto(t.texto);
  return true;
}

function limparFormTrecho() {
  ["fmtTrechoTitulo", "fmtTrechoAtalho", "fmtTrechoCategoria", "fmtTrechoTexto"].forEach((id) => ($(id).value = ""));
  delete $("fmtTrechoTitulo").dataset.id;
  $("fmtTrechoFormTitulo").textContent = "Novo trecho";
}

function editarTrecho(id) {
  const t = (state.trechos || []).find((x) => x.id === id);
  if (!t) return;
  $("fmtTrechoTitulo").value = t.titulo;
  $("fmtTrechoTitulo").dataset.id = t.id;
  $("fmtTrechoAtalho").value = t.atalho;
  $("fmtTrechoCategoria").value = t.categoria;
  $("fmtTrechoTexto").value = t.texto;
  $("fmtTrechoFormTitulo").textContent = `Editando: ${t.titulo}`;
}

async function salvarTrecho() {
  try {
    await postJson("/api/trechos", {
      id: $("fmtTrechoTitulo").dataset.id || undefined,
      titulo: $("fmtTrechoTitulo").value,
      atalho: $("fmtTrechoAtalho").value,
      categoria: $("fmtTrechoCategoria").value || "Geral",
      texto: $("fmtTrechoTexto").value,
    });
    limparFormTrecho();
    await carregarTrechos();
    showToast("Trecho salvo no banco do gabinete.", "success", 3000);
  } catch (err) {
    handleApiError(err);
  }
}

async function excluirTrecho(id) {
  const t = (state.trechos || []).find((x) => x.id === id);
  if (!t || !window.confirm(`Excluir o trecho “${t.titulo}”?`)) return;
  try {
    const resp = await apiFetch(`/api/trechos?id=${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!resp.ok) throw new ApiError(resp.status, await lerJson(resp));
    await carregarTrechos();
  } catch (err) {
    handleApiError(err);
  }
}

function usarSelecaoNoForm() {
  const txt = state.selecaoSalva ? state.selecaoSalva.toString().replace(/ /g, " ").trim() : "";
  if (!txt) {
    showToast("Selecione na folha o trecho que deseja guardar.", "warn");
    return;
  }
  $("fmtTrechoTexto").value = txt;
  if (!$("fmtTrechoTitulo").value) $("fmtTrechoTitulo").value = txt.slice(0, 60);
}

/* --------------------------------------------------------------------------
   HISTÓRICO DE VERSÕES E COMPARAÇÃO
   -------------------------------------------------------------------------- */
const fmtHora = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
};

async function abrirVersoes() {
  if (!state.lastSentence) {
    showToast("Gere a minuta para ter um histórico de versões.", "warn");
    return;
  }
  try {
    state.versoes = (await (await apiFetch("/api/sentence/versions")).json()).versoes || [];
  } catch (err) {
    handleApiError(err);
    return;
  }
  if (!state.versoes.length) {
    showToast("Ainda não há versões registradas nesta sessão.", "info");
    return;
  }
  // A comparação mais útil: a minuta como foi gerada (mais antiga) × a folha atual.
  state.versaoSelecionada = state.versoes[state.versoes.length - 1].id;
  renderVersoes();
  openModal("modalVersoes");
  await mostrarDiffVersoes();
}

function renderVersoes() {
  $("versoesLista").innerHTML = state.versoes
    .map(
      (v) => `<button type="button" class="versao-item" role="option" data-versao-id="${escapeHtml(v.id)}" aria-selected="${v.id === state.versaoSelecionada}">
        <strong>${escapeHtml(v.id)}</strong> · ${escapeHtml(v.rotulo)}
        <div class="muted">${escapeHtml(fmtHora(v.criado_em))} · ${v.palavras} palavras${v.score != null ? ` · auditoria ${escapeHtml(v.score)}%` : ""}${v.aprovada ? " ✓" : ""}</div>
      </button>`
    )
    .join("");
  const contra = $("versoesContra");
  const anterior = contra.value;
  contra.innerHTML =
    `<option value="">Folha atual (inclui edições não salvas)</option>` +
    state.versoes.filter((v) => v.id !== state.versaoSelecionada).map((v) => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.id)} — ${escapeHtml(v.rotulo)}</option>`).join("");
  if (Array.from(contra.options).some((o) => o.value === anterior)) contra.value = anterior;
}

function htmlSegmentos(segs) {
  return segs
    .map((s) => (s.op === "+" ? `<ins>${escapeHtml(s.t)}</ins>` : s.op === "-" ? `<del>${escapeHtml(s.t)}</del>` : escapeHtml(s.t)))
    .join("");
}

async function mostrarDiffVersoes() {
  const painel = $("versoesDiff");
  painel.innerHTML = `<p class="muted">Comparando…</p>`;
  const contra = $("versoesContra").value;
  try {
    const res = await postJson("/api/sentence/diff", {
      de: state.versaoSelecionada,
      para: contra || null,
      texto_atual: contra ? null : extractEditedSentenceText(),
    });
    const r = res.resumo;
    $("versoesResumo").textContent = r.identicos
      ? "Os dois textos são idênticos."
      : `${r.paragrafos_alterados} parágrafo(s) alterado(s), ${r.paragrafos_adicionados} adicionado(s), ${r.paragrafos_removidos} removido(s) · +${r.palavras_adicionadas} / −${r.palavras_removidas} palavras. Sublinhado verde = acrescentado; tachado vermelho = retirado.`;
    const soAlteracoes = $("versoesSoAlteracoes").checked;
    const saida = [];
    let iguais = 0;
    const fecharIguais = () => {
      if (iguais) saida.push(`<div class="diff-colapso">… ${iguais} parágrafo(s) sem alteração</div>`);
      iguais = 0;
    };
    res.blocos.forEach((b) => {
      if (b.tipo === "igual" && soAlteracoes) {
        iguais++;
        return;
      }
      fecharIguais();
      const cls = b.tipo === "igual" ? "diff-igual" : b.tipo === "adicionado" ? "diff-novo" : b.tipo === "removido" ? "diff-velho" : "";
      saida.push(`<p class="${cls}">${htmlSegmentos(b.segmentos)}</p>`);
    });
    fecharIguais();
    painel.innerHTML = saida.join("") || `<p class="muted">Sem diferenças.</p>`;
  } catch (err) {
    painel.innerHTML = "";
    handleApiError(err);
  }
}

async function restaurarVersao() {
  const id = state.versaoSelecionada;
  if (!id || !folhaEditavel()) return;
  if (folhaMudou() && !window.confirm("A folha tem alterações não salvas. Substituí-las pelo texto da versão selecionada?")) return;
  try {
    const v = await (await apiFetch(`/api/sentence/versions/${encodeURIComponent(id)}`)).json();
    const body = $("sentenceRenderBody");
    body.innerHTML = v.texto_html;
    decorarFolha(body);
    state.formatacao = normalizarFormatacaoFolha(v.formatacao);
    aplicarMargensFolha();
    state.selecaoSalva = null;
    renumerarMarcadores();
    marcarSujo(true);
    closeModal("modalVersoes");
    showToast(`Versão ${id} colocada na folha. Revise e use Re-Auditar para validar e registrar como nova versão.`, "info", 6000);
  } catch (err) {
    handleApiError(err);
  }
}

async function abrirPastaExportacao() {
  try {
    const res = await postJson("/api/export/abrir-pasta", {});
    showToast(`Pasta aberta: ${res.pasta}`, "info", 3500);
  } catch (err) {
    handleApiError(err);
  }
}

/* --------------------------------------------------------------------------
   PAINEL DE REVISÃO (pendências do laudo + conferência pelo manual)
   -------------------------------------------------------------------------- */
const ROTULO_SEVERIDADE = { obrigatorio: "Obrigatório / impede assinar", recomendado: "Recomendado pelo manual", observacao: "Observações" };

function revisaoPendentes() {
  return (state.revisao?.itens || []).filter((i) => i.severidade !== "observacao" && !state.revisaoFeitos.has(i.id)).length;
}

function atualizarContagemRevisao() {
  const el = $("revisaoContagem");
  if (!el) return;
  const n = state.lastSentence ? revisaoPendentes() : 0;
  el.textContent = String(n);
  el.hidden = !state.lastSentence;
  el.className = `badge-tag ${n ? "badge-warn" : "badge-success"}`;
}

async function atualizarRevisao() {
  state.revisaoFeitos = state.revisaoFeitos || new Set();
  if (!state.lastSentence) {
    state.revisao = null;
    atualizarContagemRevisao();
    return null;
  }
  try {
    state.revisao = await postJson("/api/sentence/review", { texto_atual: extractEditedSentenceText() });
  } catch (_) {
    return null; // painel é apoio: falha silenciosa não pode atrapalhar a edição
  }
  atualizarContagemRevisao();
  if (!$("modalRevisao").hidden) renderRevisao();
  return state.revisao;
}

function renderRevisao() {
  const r = state.revisao;
  const lista = $("revisaoLista");
  if (!r) {
    lista.innerHTML = `<p class="muted">Gere a minuta para revisar.</p>`;
    return;
  }
  const ocultar = $("revisaoSoPendentes").checked;
  const feitos = r.itens.filter((i) => state.revisaoFeitos.has(i.id)).length;
  $("revisaoResumo").textContent = `${r.resumo.obrigatorios} obrigatório(s) · ${r.resumo.recomendados} recomendado(s) · ${r.resumo.observacoes} observação(ões) — ${feitos} conferido(s)`;
  const visiveis = r.itens.filter((i) => !(ocultar && state.revisaoFeitos.has(i.id)));
  if (!visiveis.length) {
    lista.innerHTML = `<p class="muted" style="padding:14px;">${r.itens.length ? "Tudo conferido. Faça a leitura final e assine." : "Nenhuma pendência nem divergência encontrada pelas regras do sistema. Faça a leitura final antes de assinar."}</p>`;
    return;
  }
  let grupo = null;
  lista.innerHTML = visiveis
    .map((i) => {
      const cab = i.severidade !== grupo ? `<div class="rev-grupo">${escapeHtml(ROTULO_SEVERIDADE[i.severidade] || i.severidade)}</div>` : "";
      grupo = i.severidade;
      const feito = state.revisaoFeitos.has(i.id);
      return `${cab}<div class="rev-item" data-sev="${escapeHtml(i.severidade)}" data-feito="${feito}">
        <input type="checkbox" data-rev-feito="${escapeHtml(i.id)}" ${feito ? "checked" : ""} aria-label="Marcar como conferido: ${escapeHtml(i.titulo)}" />
        <div>
          <div class="rev-titulo">${escapeHtml(i.titulo)}</div>
          ${i.detalhe ? `<div class="rev-detalhe">${escapeHtml(i.detalhe)}</div>` : ""}
          ${i.sugestao != null && i.trecho ? `<div class="rev-sugestao">Sugestão: trocar “${escapeHtml(i.trecho)}” por “${escapeHtml(i.sugestao)}”</div>` : ""}
          ${i.base ? `<div class="rev-base">Base: ${escapeHtml(i.base)}</div>` : ""}
          <div class="rev-acoes">
            ${i.trecho ? `<button type="button" class="btn btn-sm" data-rev-ir="${escapeHtml(i.id)}">Ir ao trecho</button>` : ""}
            ${i.sugestao != null && i.trecho ? `<button type="button" class="btn btn-sm btn-gold" data-rev-aplicar="${escapeHtml(i.id)}">Aplicar sugestão</button>` : ""}
          </div>
        </div>
      </div>`;
    })
    .join("");
}

// Seleciona e destaca `trecho` na folha. Devolve o Range ou null.
function localizarTrechoNaFolha(trecho) {
  const { nos, texto } = indiceDeTexto();
  let ini = texto.indexOf(trecho);
  if (ini < 0) ini = texto.toLowerCase().indexOf(String(trecho).toLowerCase());
  if (ini < 0) return null;
  const a = posParaNo(nos, ini);
  const b = posParaNo(nos, ini + trecho.length, true);
  if (!a || !b) return null;
  const range = document.createRange();
  range.setStart(a.node, a.offset);
  range.setEnd(b.node, b.offset);
  mostrarAchado(range);
  return range;
}

function revisaoIrAoTrecho(id) {
  const item = state.revisao?.itens.find((i) => i.id === id);
  if (!item || !item.trecho) return;
  closeModal("modalRevisao");
  if (!localizarTrechoNaFolha(item.trecho)) {
    showToast("Não encontrei esse trecho na folha: ele pode já ter sido alterado. Use “Reconferir folha atual”.", "warn", 5000);
  }
}

async function revisaoAplicarSugestao(id) {
  const item = state.revisao?.itens.find((i) => i.id === id);
  if (!item || item.sugestao == null || !folhaEditavel()) return;
  const range = localizarTrechoNaFolha(item.trecho);
  if (!range) {
    showToast("Não encontrei esse trecho na folha para aplicar a sugestão.", "warn", 5000);
    return;
  }
  if (trechoTocaBadge(range)) {
    showToast("O trecho envolve uma referência a peça (ID) e não é alterado automaticamente.", "warn", 5000);
    return;
  }
  restaurarSelecao();
  inserirSubstituindo(item.sugestao);
  aposEdicao();
  showToast("Sugestão aplicada na folha. Desfaça com Ctrl+Z se preferir o texto anterior.", "success", 4500);
  await atualizarRevisao();
}

async function abrirRevisao() {
  if (!state.lastSentence) {
    showToast("Gere a minuta para abrir o painel de revisão.", "warn");
    return;
  }
  openModal("modalRevisao");
  $("revisaoLista").innerHTML = `<p class="muted">Conferindo…</p>`;
  await atualizarRevisao();
  renderRevisao();
}

function initBarraFormatacao() {
  const barra = $("fmtToolbar");
  const body = $("sentenceRenderBody");
  if (!barra || !body) return;
  try {
    document.execCommand("defaultParagraphSeparator", false, "p");
  } catch (_) {
    /* navegadores sem suporte seguem com o padr\u00e3o */
  }
  // Mant\u00e9m a sele\u00e7\u00e3o do texto ao clicar nos bot\u00f5es (selects/inputs precisam do foco e usam a sele\u00e7\u00e3o salva).
  barra.addEventListener("mousedown", (e) => {
    if (e.target.closest("button")) e.preventDefault();
  });
  barra.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-fmt]");
    if (btn) {
      e.preventDefault();
      acaoBarra(btn.dataset.fmt);
    }
  });
  $("fmtEstilo").addEventListener("change", (e) => {
    aplicarEstiloParagrafo(e.target.value);
    e.target.selectedIndex = 0;
  });
  $("fmtFonte").addEventListener("change", (e) => execFormato("fontName", e.target.value));
  $("fmtTamanho").addEventListener("change", (e) => aplicarTamanho(parseFloat(e.target.value)));
  $("fmtEntrelinhas").addEventListener("change", (e) => emBlocos((b) => (b.style.lineHeight = e.target.value)));
  $("fmtCor").addEventListener("input", (e) => execFormato("foreColor", e.target.value));
  $("btnAnalisarFalas")?.addEventListener("click", analisarFalas);
  $("btnFmtMargens").addEventListener("click", () => alternarPopoverMargens());
  $("btnFmtMargensFechar").addEventListener("click", () => alternarPopoverMargens(false));
  $("btnFmtMargensAplicar").addEventListener("click", aplicarMargensDoPopover);
  $("fmtMargensPreset").addEventListener("change", (e) => {
    const p = MARGENS_PRESETS[e.target.value];
    if (p) preencherMargens(p);
  });
  $("btnFmtModelos").addEventListener("click", () => {
    if (alternarPopover("fmtModelosPop")) carregarModelos($("fmtModeloSelect").value || undefined);
  });
  $("btnFmtLocalizar").addEventListener("click", abrirLocalizar);
  $("btnFmtTrechos").addEventListener("click", () => {
    if (alternarPopover("fmtTrechosPop")) {
      carregarTrechos();
      $("fmtTrechosBusca").focus();
    }
  });
  $("fmtTrechosBusca").addEventListener("input", renderTrechos);
  $("fmtTrechosLista").addEventListener("click", (e) => {
    const usar = e.target.closest("[data-trecho-usar]");
    const edit = e.target.closest("[data-trecho-editar]");
    const del = e.target.closest("[data-trecho-excluir]");
    if (usar) usarTrecho(usar.dataset.trechoUsar);
    else if (edit) editarTrecho(edit.dataset.trechoEditar);
    else if (del) excluirTrecho(del.dataset.trechoExcluir);
  });
  $("btnFmtTrechoSalvar").addEventListener("click", salvarTrecho);
  $("btnFmtTrechoLimpar").addEventListener("click", limparFormTrecho);
  $("btnFmtTrechoSelecao").addEventListener("click", usarSelecaoNoForm);
  $("btnOpenVersoes").addEventListener("click", abrirVersoes);
  $("btnOpenRevisao").addEventListener("click", abrirRevisao);
  $("btnRevisaoAtualizar").addEventListener("click", async () => {
    await atualizarRevisao();
    renderRevisao();
  });
  $("revisaoSoPendentes").addEventListener("change", renderRevisao);
  $("revisaoLista").addEventListener("click", (e) => {
    const ir = e.target.closest("[data-rev-ir]");
    const ap = e.target.closest("[data-rev-aplicar]");
    if (ir) revisaoIrAoTrecho(ir.dataset.revIr);
    else if (ap) revisaoAplicarSugestao(ap.dataset.revAplicar);
  });
  $("revisaoLista").addEventListener("change", (e) => {
    const cb = e.target.closest("[data-rev-feito]");
    if (!cb) return;
    if (cb.checked) state.revisaoFeitos.add(cb.dataset.revFeito);
    else state.revisaoFeitos.delete(cb.dataset.revFeito);
    atualizarContagemRevisao();
    renderRevisao();
  });
  $("btnAbrirPastaExport").addEventListener("click", abrirPastaExportacao);
  $("btnAbrirPastaSettings").addEventListener("click", abrirPastaExportacao);
  state.revisaoFeitos = new Set();
  $("btnVersoesRestaurar").addEventListener("click", restaurarVersao);
  $("versoesContra").addEventListener("change", mostrarDiffVersoes);
  $("versoesSoAlteracoes").addEventListener("change", mostrarDiffVersoes);
  $("versoesLista").addEventListener("click", (e) => {
    const item = e.target.closest("[data-versao-id]");
    if (!item) return;
    state.versaoSelecionada = item.dataset.versaoId;
    renderVersoes();
    mostrarDiffVersoes();
  });
  $("btnFmtModeloAplicar").addEventListener("click", () => {
    const m = (state.modelos || []).find((x) => x.nome === $("fmtModeloSelect").value);
    if (m) aplicarModelo(m);
  });
  $("btnFmtModeloSalvar").addEventListener("click", salvarModeloAtual);
  $("btnFmtModeloExcluir").addEventListener("click", excluirModeloSelecionado);
  $("btnFmtProximo").addEventListener("click", () => irParaOcorrencia(1));
  $("btnFmtAnterior").addEventListener("click", () => irParaOcorrencia(-1));
  $("btnFmtSubstituir").addEventListener("click", substituirAtual);
  $("btnFmtSubstituirTudo").addEventListener("click", substituirTudo);
  ["fmtLocalizarTexto", "fmtSubstituirTexto"].forEach((id) =>
    $(id).addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        irParaOcorrencia(e.shiftKey ? -1 : 1);
      } else if (e.key === "Escape") {
        alternarPopover("fmtLocalizarPop", false);
      }
    })
  );
  document.addEventListener("click", (e) => {
    // composedPath: o alvo pode ter sido removido do DOM por um re-render (lista de trechos).
    if (!e.composedPath().some((el) => el.classList && el.classList.contains("fmt-group-pop"))) {
      document.querySelectorAll(".fmt-popover").forEach((p) => alternarPopover(p.id, false));
    }
  });
  document.addEventListener("selectionchange", () => {
    const sel = window.getSelection();
    if (sel.rangeCount && body.contains(sel.anchorNode)) {
      salvarSelecao();
      atualizarBarraFormatacao();
    }
  });
  body.addEventListener("input", () => {
    renumerarMarcadores();
    marcarSujo(true);
  });
  // Colar sempre como texto simples: evita trazer fontes, cores e estilos de outros sites/Word.
  body.addEventListener("paste", (e) => {
    if (!folhaEditavel()) return;
    e.preventDefault();
    const texto = (e.clipboardData || window.clipboardData).getData("text/plain");
    if (texto) document.execCommand("insertText", false, texto);
  });
  // Atalhos do LibreOffice Writer: Ctrl+L / E / R / J (alinhamento), Ctrl+M (limpar), Ctrl+B/I/U nativos.
  body.addEventListener("keydown", (e) => {
    if (e.key === "Tab" && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey && folhaEditavel() && expandirAtalhoNoCursor()) {
      e.preventDefault();
      return;
    }
    if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
    if (e.key.toLowerCase() === "h" || e.key.toLowerCase() === "f") {
      e.preventDefault();
      abrirLocalizar();
      return;
    }
    const mapa = { l: "justifyLeft", e: "justifyCenter", r: "justifyRight", j: "justifyFull", m: "limpar" };
    const acao = mapa[e.key.toLowerCase()];
    if (acao) {
      e.preventDefault();
      acaoBarra(acao);
    }
  });
  state.formatacao = normalizarFormatacaoFolha(null);
  carregarModelos();
  carregarTrechos();
}

// Antes de exportar/copiar: garante que o arquivo reflita o que est\u00e1 na folha.

export {
  ROTULO_SEVERIDADE,
  abrirPastaExportacao,
  abrirRevisao,
  abrirVersoes,
  atualizarContagemRevisao,
  atualizarRevisao,
  carregarTrechos,
  editarTrecho,
  excluirTrecho,
  expandirAtalhoNoCursor,
  fmtHora,
  htmlSegmentos,
  initBarraFormatacao,
  inserirTexto,
  limparFormTrecho,
  localizarTrechoNaFolha,
  mostrarDiffVersoes,
  renderRevisao,
  renderTrechos,
  renderVersoes,
  restaurarVersao,
  revisaoAplicarSugestao,
  revisaoIrAoTrecho,
  revisaoPendentes,
  salvarTrecho,
  usarSelecaoNoForm,
  usarTrecho,
};
