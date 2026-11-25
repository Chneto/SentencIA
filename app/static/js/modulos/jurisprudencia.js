// SentencIA — Módulo ES: jurisprudencia.js (TASK-019: Painel de jurisprudência do gabinete)
import { state } from "./estado.js?v=2.5.0";
import { $, escapeHtml, openModal, showToast } from "./dom.js?v=2.5.0";
import { apiJson, handleApiError, postJson } from "./api.js?v=2.5.0";

const MIN_TEXTO_LITERAL = 20;

const _jurisState = {
  oficiais: [],
  gabinete: [],
  tribunais: [],
  editandoId: null,
  excluindoItem: null,
  importPayload: null,
  importPrevia: null,
};

function _removerAcentos(texto) {
  return String(texto || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "");
}

function _extrairTribunalDaRef(semAcento) {
  const tokens = semAcento.toUpperCase().match(/[A-Z0-9]+/g) || [];
  const setTrib = new Set(_jurisState.tribunais);
  for (let i = tokens.length - 1; i >= 0; i -= 1) {
    if (setTrib.has(tokens[i])) return tokens[i];
  }
  return null;
}

function normalizarReferenciaLocal(referencia, tribunal) {
  const limpa = String(referencia || "").trim().replace(/\s+/g, " ");
  if (!limpa) return "";
  const tribFallback = String(tribunal || "").trim().toUpperCase() || null;
  const sem = _removerAcentos(limpa);

  const mSv = sem.match(/^(?:sumula\s+vinculante|sv)\s*(?:n[.º°o]*)?\s*(\d+)(?:\s*(?:[/-]|do|da|de)?\s*([a-z0-9-]+))?$/i);
  if (mSv) return `Súmula Vinculante ${parseInt(mSv[1], 10)}/STF`;

  const mSum = sem.match(/^sumula\s*(?:n[.º°o]*)?\s*(\d+)(?:\s*(?:[/-]|do|da|de)?\s*([a-z0-9-]+))?$/i);
  if (mSum) {
    const num = parseInt(mSum[1], 10);
    const trib = _extrairTribunalDaRef(mSum[2] || "") || tribFallback;
    return trib ? `Súmula ${num}/${trib}` : `Súmula ${num}`;
  }

  const mTema = sem.match(
    /^tema(?:\s+repetitivo|\s+de\s+repercussao\s+geral|\s+rg)?\s*(?:n[.º°o]*)?\s*([\d.]+)(?:\s*(?:[/-]|do|da|de)?\s*([a-z0-9-]+))?$/i
  );
  if (mTema) {
    const num = parseInt(mTema[1].replace(/\./g, ""), 10);
    const trib = _extrairTribunalDaRef(mTema[2] || "") || tribFallback;
    return trib ? `Tema ${num}/${trib}` : `Tema ${num}`;
  }

  const mAcao = sem.match(/^(adc|adi|adpf|aco)\s*(?:n[.º°o]*)?\s*([\d.]+)(?:\s*(?:[/-]|do|da|de)?\s*([a-z0-9-]+))?$/i);
  if (mAcao) {
    const sigla = mAcao[1].toUpperCase();
    const num = parseInt(mAcao[2].replace(/\./g, ""), 10);
    const trib = _extrairTribunalDaRef(mAcao[3] || "") || tribFallback || "STF";
    return `${sigla} ${num}/${trib}`;
  }

  const mFon = sem.match(/^enunciado(?:\s+fonavid)?\s*(?:n[.º°o]*)?\s*(\d+)(?:\s*(?:[/-]|do|da|de)?\s*fonavid)?$/i);
  if (mFon && (sem.toLowerCase().includes("fonavid") || tribFallback === "FONAVID")) {
    return `Enunciado ${parseInt(mFon[1], 10)}/FONAVID`;
  }

  const mRes = sem.match(/^resolucao(?:\s+cnj)?\s*(?:n[.º°o]*)?\s*(\d+(?:\/\d{2,4})?)(?:\s*(?:[/-]|do|da|de)?\s*cnj)?$/i);
  if (mRes && (sem.toLowerCase().includes("cnj") || tribFallback === "CNJ")) {
    return `Resolução CNJ nº ${mRes[1]}`;
  }

  return limpa;
}

function _chaveDuplicidadeLocal(referencia, tribunal) {
  const norm = normalizarReferenciaLocal(referencia, tribunal);
  return _removerAcentos(norm).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function _todosItensComOrigem() {
  const gab = _jurisState.gabinete.map((it) => ({ ...it, _origem: "gabinete" }));
  const ofi = _jurisState.oficiais.map((it) => ({ ...it, _origem: "oficial" }));
  return [...gab, ...ofi];
}

function _buscarDuplicadoLocal(referencia, tribunal, ignorarId = null) {
  const chave = _chaveDuplicidadeLocal(referencia, tribunal);
  if (!chave) return null;
  const ig = (ignorarId || "").toUpperCase();
  for (const it of _jurisState.oficiais) {
    if (_chaveDuplicidadeLocal(it.referencia, it.tribunal) === chave) {
      return { item: it, origem: "oficial" };
    }
  }
  for (const it of _jurisState.gabinete) {
    if (ig && String(it.id || "").toUpperCase() === ig) continue;
    if (_chaveDuplicidadeLocal(it.referencia, it.tribunal) === chave) {
      return { item: it, origem: "gabinete" };
    }
  }
  return null;
}

function _popularSelectsFiltrosEForm() {
  const todos = _todosItensComOrigem();
  const tribSet = new Set(todos.map((it) => String(it.tribunal || "").trim().toUpperCase()).filter(Boolean));
  const tipoSet = new Set(todos.map((it) => String(it.tipo || "").trim()).filter(Boolean));

  const selTribFiltro = $("jurisFiltroTribunal");
  if (selTribFiltro) {
    const atual = selTribFiltro.value || "TODOS";
    selTribFiltro.innerHTML =
      `<option value="TODOS">Todos os tribunais</option>` +
      Array.from(tribSet)
        .sort()
        .map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`)
        .join("");
    selTribFiltro.value = tribSet.has(atual) ? atual : "TODOS";
  }

  const selTipoFiltro = $("jurisFiltroTipo");
  if (selTipoFiltro) {
    const atual = selTipoFiltro.value || "TODOS";
    selTipoFiltro.innerHTML =
      `<option value="TODOS">Todos os tipos</option>` +
      Array.from(tipoSet)
        .sort()
        .map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`)
        .join("");
    selTipoFiltro.value = tipoSet.has(atual) ? atual : "TODOS";
  }

  const selPrecTrib = $("precTribunal");
  if (selPrecTrib && _jurisState.tribunais.length) {
    const atual = selPrecTrib.value || "TJRN";
    selPrecTrib.innerHTML = _jurisState.tribunais
      .map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`)
      .join("");
    selPrecTrib.value = _jurisState.tribunais.includes(atual) ? atual : "TJRN";
  }
}

function _renderListaPrecedentes() {
  const container = $("precedentesContainer");
  const resumo = $("jurisListaResumo");
  if (!container) return;

  const busca = ($("jurisBusca")?.value || "").trim().toLowerCase();
  const origem = $("jurisFiltroOrigem")?.value || "TODOS";
  const tribunal = $("jurisFiltroTribunal")?.value || "TODOS";
  const tipo = $("jurisFiltroTipo")?.value || "TODOS";

  const todos = _todosItensComOrigem();
  const filtrados = todos.filter((it) => {
    if (origem !== "TODOS" && it._origem !== origem) return false;
    if (tribunal !== "TODOS" && String(it.tribunal || "").toUpperCase() !== tribunal) return false;
    if (tipo !== "TODOS" && String(it.tipo || "") !== tipo) return false;
    if (busca) {
      const hay = `${it.id || ""} ${it.referencia || ""} ${it.titulo || ""} ${it.texto_literal || ""} ${it.materia || ""}`.toLowerCase();
      if (!hay.includes(busca)) return false;
    }
    return true;
  });

  if (resumo) {
    resumo.innerHTML =
      `Exibindo ${filtrados.length} de ${todos.length} precedente(s) (${_jurisState.gabinete.length} do gabinete). ` +
      `<a href="https://paineisanalytics.cnj.jus.br/single/?appid=f3bb4296-6c88-4c1f-b3bb-8a51e4268a58&sheet=03bb002c-6256-4b1d-9c93-a421f1bf8833" target="_blank" rel="noopener noreferrer">Banco CNJ: Protocolo de Gênero ↗</a>`;
  }

  if (!filtrados.length) {
    container.innerHTML = `<div class="muted juris-empty">Nenhum precedente encontrado com os filtros atuais.</div>`;
    return;
  }

  container.innerHTML = filtrados
    .map((item) => {
      const ehOficial = item._origem === "oficial";
      const idEsc = escapeHtml(item.id || "");
      const refEsc = escapeHtml(item.referencia || "");
      const titEsc = escapeHtml(item.titulo || "");
      const tribEsc = escapeHtml(item.tribunal || "");
      const tipoEsc = escapeHtml(item.tipo || "");
      const txtEsc = escapeHtml(item.texto_literal || "");
      const statusVerif = item.verificacao && typeof item.verificacao === "object" ? item.verificacao.status : "";
      const seloVerif =
        ehOficial && statusVerif === "pendente"
          ? `<span class="juris-badge badge-warn">não verificado</span>`
          : ehOficial && statusVerif === "parcial"
            ? `<span class="juris-badge is-meta">verificação parcial</span>`
            : "";
      const selo = ehOficial
        ? `<span class="juris-badge is-oficial">Acervo oficial</span>${seloVerif}`
        : `<span class="juris-badge is-gabinete">Gabinete</span>`;
      const acoes = ehOficial
        ? `<span class="muted juris-readonly-hint">Somente leitura</span>`
        : `<div class="juris-card-actions">
             <button type="button" class="btn btn-sm btn-juris-editar" data-item-id="${idEsc}">Editar</button>
             <button type="button" class="btn btn-sm btn-danger-ghost btn-juris-excluir" data-item-id="${idEsc}">Excluir</button>
           </div>`;
      return `<article class="inner-tile juris-card" data-item-id="${idEsc}" data-origem="${ehOficial ? "oficial" : "gabinete"}">
        <div class="juris-card-head">
          <div class="juris-card-meta">
            <strong class="juris-card-ref">${refEsc}</strong>
            <span class="juris-badge is-meta">${tribEsc}</span>
            <span class="juris-badge is-meta">${tipoEsc}</span>
            ${selo}
          </div>
          ${acoes}
        </div>
        <div class="juris-card-title">${titEsc}</div>
        <div class="text-sec juris-card-text">${txtEsc}</div>
      </article>`;
    })
    .join("");
}

function _irParaItemExistente(itemId) {
  if (!itemId) return;
  if ($("jurisBusca")) $("jurisBusca").value = "";
  if ($("jurisFiltroOrigem")) $("jurisFiltroOrigem").value = "TODOS";
  if ($("jurisFiltroTribunal")) $("jurisFiltroTribunal").value = "TODOS";
  if ($("jurisFiltroTipo")) $("jurisFiltroTipo").value = "TODOS";
  _renderListaPrecedentes();

  const container = $("precedentesContainer");
  if (!container) return;
  const cards = Array.from(container.querySelectorAll(".juris-card"));
  const alvo = cards.find((c) => (c.dataset.itemId || "").toUpperCase() === String(itemId).toUpperCase());
  if (alvo) {
    alvo.scrollIntoView({ behavior: "smooth", block: "center" });
    alvo.classList.add("is-highlighted");
    setTimeout(() => alvo.classList.remove("is-highlighted"), 2200);
  }
}

function _renderFeedbackDuplicidade(itemExistente, origem) {
  const fb = $("precLiveFeedback");
  if (!fb) return;
  const local = origem === "oficial" ? "acervo oficial" : "acervo do gabinete";
  const idEsc = escapeHtml(itemExistente.id || "");
  const refEsc = escapeHtml(itemExistente.referencia || "");
  fb.hidden = false;
  fb.className = "juris-live-feedback is-error";
  fb.innerHTML =
    `Duplicidade detectada no ${escapeHtml(local)}: <strong>${refEsc}</strong> (${idEsc}). ` +
    `<button type="button" class="btn-link-inline btn-juris-ir-existente" data-target-id="${idEsc}">Ver item existente</button>`;
}

function _validarFormularioAoVivo() {
  const ref = ($("precReferencia")?.value || "").trim();
  const trib = ($("precTribunal")?.value || "TJRN").trim();
  const texto = ($("precTexto")?.value || "").trim();
  const normEl = $("precLiveNorm");
  const fbEl = $("precLiveFeedback");
  const countEl = $("precTextoCount");

  if (countEl) {
    countEl.textContent = `${texto.length} / ${MIN_TEXTO_LITERAL} caracteres mín.`;
    countEl.classList.toggle("is-short", texto.length > 0 && texto.length < MIN_TEXTO_LITERAL);
  }

  if (normEl) {
    if (ref) {
      const norm = normalizarReferenciaLocal(ref, trib);
      normEl.hidden = false;
      normEl.textContent = `Referência normalizada: ${norm}`;
    } else {
      normEl.hidden = true;
      normEl.textContent = "";
    }
  }

  if (!fbEl) return;
  if (ref) {
    const dup = _buscarDuplicadoLocal(ref, trib, _jurisState.editandoId);
    if (dup) {
      _renderFeedbackDuplicidade(dup.item, dup.origem);
      return;
    }
  }
  if (texto.length > 0 && texto.length < MIN_TEXTO_LITERAL) {
    fbEl.hidden = false;
    fbEl.className = "juris-live-feedback is-warn";
    fbEl.textContent = `Texto literal muito curto: informe pelo menos ${MIN_TEXTO_LITERAL} caracteres.`;
    return;
  }
  fbEl.hidden = true;
  fbEl.textContent = "";
}

function _sairModoEdicao() {
  _jurisState.editandoId = null;
  const form = $("formCustomPrecedente");
  if (form) form.reset();
  if ($("precTribunal") && _jurisState.tribunais.includes("TJRN")) $("precTribunal").value = "TJRN";
  if ($("precFormHeading")) $("precFormHeading").textContent = "Adicionar Precedente do Gabinete";
  if ($("btnPrecSubmit")) $("btnPrecSubmit").textContent = "Adicionar ao Acervo";
  if ($("btnPrecCancelarEdicao")) $("btnPrecCancelarEdicao").hidden = true;
  _validarFormularioAoVivo();
}

function _entrarModoEdicao(itemId) {
  const item = _jurisState.gabinete.find((it) => String(it.id || "").toUpperCase() === String(itemId || "").toUpperCase());
  if (!item) return;
  _jurisState.editandoId = item.id;
  if ($("precReferencia")) $("precReferencia").value = item.referencia || "";
  if ($("precTribunal") && item.tribunal) $("precTribunal").value = item.tribunal;
  if ($("precTipo") && item.tipo) $("precTipo").value = item.tipo;
  if ($("precTitulo")) $("precTitulo").value = item.titulo || "";
  if ($("precTexto")) $("precTexto").value = item.texto_literal || "";
  if ($("precFormHeading")) $("precFormHeading").textContent = `Editando precedente (${item.id})`;
  if ($("btnPrecSubmit")) $("btnPrecSubmit").textContent = "Salvar alterações";
  if ($("btnPrecCancelarEdicao")) $("btnPrecCancelarEdicao").hidden = false;
  _validarFormularioAoVivo();
  $("precReferencia")?.focus();
}

function _ocultarConfirmacaoExclusao() {
  _jurisState.excluindoItem = null;
  const box = $("jurisDeleteConfirmBox");
  if (box) box.hidden = true;
}

function _abrirConfirmacaoExclusao(itemId) {
  const item = _jurisState.gabinete.find((it) => String(it.id || "").toUpperCase() === String(itemId || "").toUpperCase());
  if (!item) return;
  _jurisState.excluindoItem = item;
  const box = $("jurisDeleteConfirmBox");
  const txt = $("jurisDeleteConfirmTexto");
  if (txt) {
    txt.textContent = `Excluir o precedente do gabinete "${item.referencia}" (${item.id})? Um backup será salvo em custom_precedentes.bak.json.`;
  }
  if (box) {
    box.hidden = false;
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function _ocultarPreviaImportacao() {
  _jurisState.importPayload = null;
  _jurisState.importPrevia = null;
  const box = $("jurisImportPreviewBox");
  const inp = $("jurisImportFile");
  if (box) box.hidden = true;
  if (inp) inp.value = "";
}

function _renderPreviaImportacao(previa) {
  const box = $("jurisImportPreviewBox");
  const body = $("jurisImportPreviewBody");
  const btnConf = $("btnJurisImportConfirmar");
  if (!box || !body) return;

  const novos = Array.isArray(previa.novos) ? previa.novos : [];
  const dups = Array.isArray(previa.duplicados_ignorados) ? previa.duplicados_ignorados : [];
  const invs = Array.isArray(previa.invalidos) ? previa.invalidos : [];

  const partes = [
    `<div><strong>Prévia da importação:</strong> ${novos.length} novo(s) entram, ${dups.length} duplicado(s) ignorado(s), ${invs.length} inválido(s).</div>`,
  ];
  if (novos.length) {
    const listaNovos = novos.map((n) => `<li><strong>${escapeHtml(n.referencia)}</strong> — ${escapeHtml(n.titulo)}</li>`).join("");
    partes.push(`<div class="juris-import-sec"><span>Novos a importar:</span><ul>${listaNovos}</ul></div>`);
  }
  if (dups.length) {
    const listaDups = dups
      .map((d) => `<li>${escapeHtml(d.referencia)} <span class="muted">(${escapeHtml(d.origem_existente || "duplicado")})</span></li>`)
      .join("");
    partes.push(`<div class="juris-import-sec"><span>Duplicados ignorados:</span><ul>${listaDups}</ul></div>`);
  }
  if (invs.length) {
    const listaInvs = invs.map((iv) => `<li>${escapeHtml(iv.referencia || `Item #${iv.indice}`)}: ${escapeHtml(iv.erro)}</li>`).join("");
    partes.push(`<div class="juris-import-sec"><span>Inválidos ignorados:</span><ul>${listaInvs}</ul></div>`);
  }

  body.innerHTML = partes.join("");
  if (btnConf) btnConf.disabled = novos.length === 0;
  box.hidden = false;
}

async function refreshKnowledge() {
  try {
    const data = await apiJson("/api/knowledge");
    const kb = data.knowledge || {};
    _jurisState.oficiais = Array.isArray(kb.sumulas_e_temas) ? kb.sumulas_e_temas : [];
    _jurisState.gabinete = Array.isArray(kb.precedentes_gabinete) ? kb.precedentes_gabinete : [];
    _jurisState.tribunais = Array.isArray(kb.tribunais_reconhecidos) ? kb.tribunais_reconhecidos : [];

    if (Array.isArray(data.penal_catalog)) state.penalCatalog = data.penal_catalog;
    const badge = $("badgeAcervoCount");
    if (badge) badge.textContent = String(_jurisState.oficiais.length + _jurisState.gabinete.length);

    _popularSelectsFiltrosEForm();
    _renderListaPrecedentes();
    _validarFormularioAoVivo();

    const st = data.style_profile || {};
    const modelos = st.modelos_cadastrados || [];
    const styleInfo = $("styleProfileInfo");
    if (styleInfo) {
      styleInfo.innerHTML = `<strong>${escapeHtml(st.nome_perfil || "Perfil do Gabinete")}</strong><br />Modelos cadastrados: ${modelos.length}`;
    }
  } catch (err) {
    if (err.status === 401) handleApiError(err, { silent: true });
  }
}

function _ativarAbaAcervo(abaId) {
  document.querySelectorAll("[data-acervo-tab]").forEach((btn) => {
    const ativo = btn.dataset.acervoTab === abaId;
    btn.classList.toggle("active", ativo);
    btn.setAttribute("aria-selected", ativo ? "true" : "false");
  });
  document.querySelectorAll(".acervo-tab-pane").forEach((pane) => {
    pane.hidden = pane.id !== abaId;
  });
}

async function abrirModalAcervo() {
  _ocultarConfirmacaoExclusao();
  _ocultarPreviaImportacao();
  const aviso = $("jurisAvisoBanner");
  if (aviso) aviso.hidden = true;
  _ativarAbaAcervo("acervoTabJuris");
  openModal("modalAcervo");
  await refreshKnowledge();
}

function bindJurisprudenciaEvents() {
  $("btnOpenAcervoModal")?.addEventListener("click", abrirModalAcervo);

  document.querySelectorAll("[data-acervo-tab]").forEach((btn) => {
    btn.addEventListener("click", () => _ativarAbaAcervo(btn.dataset.acervoTab));
  });

  ["jurisBusca", "jurisFiltroOrigem", "jurisFiltroTribunal", "jurisFiltroTipo"].forEach((id) => {
    $(id)?.addEventListener("input", _renderListaPrecedentes);
    $(id)?.addEventListener("change", _renderListaPrecedentes);
  });

  $("precReferencia")?.addEventListener("input", _validarFormularioAoVivo);
  $("precTexto")?.addEventListener("input", _validarFormularioAoVivo);
  $("precTribunal")?.addEventListener("change", _validarFormularioAoVivo);

  $("precLiveFeedback")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".btn-juris-ir-existente");
    if (!btn) return;
    _irParaItemExistente(btn.dataset.targetId);
  });

  $("btnPrecCancelarEdicao")?.addEventListener("click", _sairModoEdicao);

  $("precedentesContainer")?.addEventListener("click", (e) => {
    const btnEd = e.target.closest(".btn-juris-editar");
    if (btnEd) {
      _ocultarConfirmacaoExclusao();
      _entrarModoEdicao(btnEd.dataset.itemId);
      return;
    }
    const btnEx = e.target.closest(".btn-juris-excluir");
    if (btnEx) {
      _abrirConfirmacaoExclusao(btnEx.dataset.itemId);
    }
  });

  $("btnJurisCancelarExclusao")?.addEventListener("click", _ocultarConfirmacaoExclusao);
  $("btnJurisConfirmarExclusao")?.addEventListener("click", async () => {
    const alvo = _jurisState.excluindoItem;
    if (!alvo) return;
    try {
      const resp = await apiJson(`/api/knowledge/precedente/${encodeURIComponent(alvo.id)}`, { method: "DELETE" });
      if (_jurisState.editandoId === alvo.id) _sairModoEdicao();
      _ocultarConfirmacaoExclusao();
      await refreshKnowledge();
      if (resp.aviso) {
        const banner = $("jurisAvisoBanner");
        if (banner) {
          banner.textContent = resp.aviso;
          banner.hidden = false;
        }
        showToast(resp.aviso, "warning");
      } else {
        showToast(`Precedente "${alvo.referencia}" excluído do gabinete.`, "success");
      }
    } catch (err) {
      handleApiError(err);
    }
  });

  $("formCustomPrecedente")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      referencia: $("precReferencia").value,
      titulo: $("precTitulo").value,
      texto_literal: $("precTexto").value,
      tipo: $("precTipo")?.value || "PRECEDENTE_TJ",
      tribunal: $("precTribunal")?.value || "TJRN",
    };
    try {
      if (_jurisState.editandoId) {
        await postJson(`/api/knowledge/precedente/${encodeURIComponent(_jurisState.editandoId)}`, payload, "PUT");
        _sairModoEdicao();
        await refreshKnowledge();
        showToast("Precedente do gabinete atualizado.", "success");
      } else {
        await postJson("/api/knowledge/precedente", payload, "POST");
        _sairModoEdicao();
        await refreshKnowledge();
        showToast("Precedente adicionado ao acervo fechado.", "success");
      }
    } catch (err) {
      if (err && err.status === 409 && err.body && err.body.item_existente) {
        _renderFeedbackDuplicidade(err.body.item_existente, err.body.origem || "gabinete");
        showToast(err.message || "Precedente duplicado.", "warning");
        return;
      }
      handleApiError(err);
    }
  });

  $("btnJurisExportar")?.addEventListener("click", async () => {
    try {
      const dados = await apiJson("/api/knowledge/gabinete/exportar");
      const blob = new Blob([JSON.stringify(dados, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "precedentes_gabinete.json";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      showToast(`Exportados ${Number(dados.total || 0)} precedente(s) do gabinete.`, "success");
    } catch (err) {
      handleApiError(err);
    }
  });

  $("btnJurisImportarAbrir")?.addEventListener("click", () => $("jurisImportFile")?.click());
  $("jurisImportFile")?.addEventListener("change", async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    try {
      const texto = await file.text();
      const parsed = JSON.parse(texto);
      const payload = {
        formato_versao: parsed.formato_versao ?? 1,
        itens: Array.isArray(parsed.itens) ? parsed.itens : [],
      };
      const previa = await postJson("/api/knowledge/gabinete/importar", { ...payload, confirmar: false });
      _jurisState.importPayload = payload;
      _jurisState.importPrevia = previa;
      _renderPreviaImportacao(previa);
    } catch (err) {
      _ocultarPreviaImportacao();
      showToast(err.message || "Arquivo JSON de importação inválido.", "error");
    }
  });

  $("btnJurisImportCancelar")?.addEventListener("click", _ocultarPreviaImportacao);
  $("btnJurisImportConfirmar")?.addEventListener("click", async () => {
    if (!_jurisState.importPayload) return;
    try {
      const res = await postJson("/api/knowledge/gabinete/importar", {
        ..._jurisState.importPayload,
        confirmar: true,
      });
      _ocultarPreviaImportacao();
      await refreshKnowledge();
      const qtd = Array.isArray(res.adicionados) ? res.adicionados.length : 0;
      const dups = Array.isArray(res.duplicados_ignorados) ? res.duplicados_ignorados.length : 0;
      showToast(`Importação concluída: ${qtd} adicionado(s), ${dups} duplicado(s) ignorado(s).`, "success");
    } catch (err) {
      handleApiError(err);
    }
  });

  $("formStyleSample")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await postJson("/api/knowledge/style-sample", {
        titulo: $("styleSampleTitulo").value,
        tipo: $("styleSampleTipo").value,
        texto_amostra: $("styleSampleTexto").value,
      });
      $("formStyleSample").reset();
      await refreshKnowledge();
      showToast("Amostra de estilo registrada no perfil do gabinete.", "success");
    } catch (err) {
      handleApiError(err);
    }
  });
}

export { abrirModalAcervo, bindJurisprudenciaEvents, normalizarReferenciaLocal, refreshKnowledge };
