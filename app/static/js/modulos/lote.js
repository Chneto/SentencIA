// SentencIA — Módulo ES: lote.js (TASK-018: Pré-análise em lote — interface)
import { hooks } from "./estado.js?v=2.2.1";
import { $, closeModal, escapeHtml, openModal, setActiveStation, showToast } from "./dom.js?v=2.2.1";
import { apiFetch, apiJson, handleApiError, postJson } from "./api.js?v=2.2.1";

const _loteState = {
  arquivosSelecionados: [],
  loteAtualId: null,
  lotes: [],
  manifest: null,
  pollTimer: null,
  trocaPendente: null, // { loteId, pid, rotulo }
};

const _ROTULOS_STATUS = {
  pronto: "Pronto",
  pendencias: "Pendências",
  falhou: "Falhou",
  pendente: "Na fila",
  cancelado: "Cancelado",
};

const _ROTULOS_PENDENCIA = {
  numero_processo: "Nº do processo",
  nome_ofendida: "Ofendida",
  nome_reu: "Acusado",
  data_fato: "Data do fato",
  data_recebimento_denuncia: "Receb. denúncia",
  crimes_imputados: "Crimes",
};

function _pararPollingLote() {
  if (_loteState.pollTimer) {
    clearTimeout(_loteState.pollTimer);
    _loteState.pollTimer = null;
  }
}

function _ocultarConfirmacaoTroca() {
  _loteState.trocaPendente = null;
  const box = $("loteConfirmTrocaBox");
  const chk = $("chkLoteConfirmarTroca");
  const btn = $("btnLoteConfirmarTroca");
  if (box) box.hidden = true;
  if (chk) chk.checked = false;
  if (btn) btn.disabled = true;
}

function _mostrarErroLote(msg) {
  const el = $("loteStatusErro");
  if (!el) return;
  if (!msg) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.textContent = msg;
  el.hidden = false;
}

function _renderArquivosSelecionados() {
  const info = $("loteSelectedFiles");
  const btnEnviar = $("btnLoteEnviar");
  const btnLimpar = $("btnLoteLimparSelecao");
  const lista = _loteState.arquivosSelecionados;
  if (btnEnviar) btnEnviar.disabled = lista.length === 0;
  if (btnLimpar) btnLimpar.hidden = lista.length === 0;
  if (!info) return;
  if (!lista.length) {
    info.textContent = "Nenhum PDF selecionado.";
    return;
  }
  const nomes = lista.slice(0, 4).map((f) => f.name).join(", ");
  const extra = lista.length > 4 ? ` (+${lista.length - 4})` : "";
  info.textContent = `${lista.length} PDF(s) pronto(s) para envio: ${nomes}${extra}`;
}

function _adicionarArquivos(fileList) {
  const arr = Array.from(fileList || []);
  const pdfs = arr.filter((f) => (f.name || "").toLowerCase().endsWith(".pdf"));
  if (arr.length && !pdfs.length) {
    _mostrarErroLote("Envie apenas arquivos .pdf na pré-análise em lote.");
    return;
  }
  if (pdfs.length < arr.length) {
    showToast("Alguns arquivos ignorados: o lote aceita apenas PDFs.", "warning");
  }
  _mostrarErroLote("");
  const existentes = new Set(_loteState.arquivosSelecionados.map((f) => `${f.name}:${f.size}`));
  for (const f of pdfs) {
    const k = `${f.name}:${f.size}`;
    if (!existentes.has(k)) {
      _loteState.arquivosSelecionados.push(f);
      existentes.add(k);
    }
  }
  _renderArquivosSelecionados();
}

function _renderSeletorLotes() {
  const sel = $("loteSelect");
  if (!sel) return;
  const lotes = _loteState.lotes || [];
  if (!lotes.length) {
    sel.innerHTML = `<option value="">Nenhum lote anterior</option>`;
    sel.disabled = true;
    return;
  }
  sel.disabled = false;
  sel.innerHTML = lotes
    .map((l) => {
      const id = escapeHtml(l.lote_id || "");
      const st = escapeHtml(l.status || "");
      const tot = Number(l.total || 0);
      const proc = Number(l.processados || 0);
      const selected = l.lote_id === _loteState.loteAtualId ? " selected" : "";
      return `<option value="${id}"${selected}>${id} — ${proc}/${tot} (${st})</option>`;
    })
    .join("");
}

function _formatarPendencias(proc) {
  if (proc.status === "falhou") {
    const motivo = proc.motivo ? String(proc.motivo) : "Falha ao processar o PDF.";
    return `<span class="lote-motivo-erro">${escapeHtml(motivo)}</span>`;
  }
  if (proc.status === "cancelado") {
    return `<span class="muted">Cancelado</span>`;
  }
  if (proc.status === "pendente") {
    return `<span class="muted">Aguardando…</span>`;
  }
  const pends = Array.isArray(proc.pendencias) ? proc.pendencias : [];
  if (!pends.length) {
    return `<span class="muted">Nenhuma</span>`;
  }
  return pends
    .map((p) => {
      const rot = _ROTULOS_PENDENCIA[p] || String(p);
      return `<span class="lote-pend-chip">${escapeHtml(rot)}</span>`;
    })
    .join(" ");
}

function _renderManifest(manifest) {
  _loteState.manifest = manifest;
  const progressWrap = $("loteProgressWrap");
  const fill = $("loteProgressFill");
  const text = $("loteProgressText");
  const pills = $("loteSummaryPills");
  const btnCancelar = $("btnLoteCancelar");
  const tbody = $("loteTableBody");
  const emptyMsg = $("loteEmptyMsg");

  if (!manifest) {
    if (progressWrap) progressWrap.hidden = true;
    if (btnCancelar) btnCancelar.hidden = true;
    if (tbody) tbody.innerHTML = "";
    if (emptyMsg) emptyMsg.hidden = false;
    return;
  }

  const total = Number(manifest.total || 0);
  const processados = Number(manifest.processados || 0);
  const pct = Math.max(0, Math.min(100, Number(manifest.percentual ?? (total ? Math.round((processados / total) * 100) : 0))));
  const emAndamento = manifest.status === "pendente" || manifest.status === "executando";

  if (progressWrap) progressWrap.hidden = false;
  if (fill) fill.style.width = `${pct}%`;
  if (text) {
    const rotStatus =
      manifest.status === "concluido"
        ? "Concluído"
        : manifest.status === "cancelado"
        ? "Cancelado"
        : "Pré-analisando…";
    text.textContent = `${rotStatus} — ${processados} de ${total} processo(s) (${pct}%)`;
  }
  if (pills) {
    pills.innerHTML = [
      `<span class="lote-status-badge is-pronto">Prontos: ${Number(manifest.prontos || 0)}</span>`,
      `<span class="lote-status-badge is-pendencias">Pendências: ${Number(manifest.com_pendencias || 0)}</span>`,
      `<span class="lote-status-badge is-falhou">Falhas: ${Number(manifest.falhas || 0)}</span>`,
    ].join("");
  }
  if (btnCancelar) {
    btnCancelar.hidden = !emAndamento;
    btnCancelar.disabled = !emAndamento;
  }

  const processos = Array.isArray(manifest.processos) ? manifest.processos : [];
  if (emptyMsg) emptyMsg.hidden = processos.length > 0;
  if (!tbody) return;

  const loteIdEsc = escapeHtml(manifest.lote_id || "");
  tbody.innerHTML = processos
    .map((proc) => {
      const numero = proc.numero_processo ? String(proc.numero_processo) : "";
      const arq = proc.arquivo_original ? String(proc.arquivo_original) : "";
      const st = String(proc.status || "pendente");
      const stLabel = _ROTULOS_STATUS[st] || st;
      const stCls =
        st === "pronto"
          ? "is-pronto"
          : st === "pendencias"
          ? "is-pendencias"
          : st === "falhou"
          ? "is-falhou"
          : "is-pendente";
      const pecas = Number(proc.total_pecas || 0);
      const teses = Array.isArray(proc.teses_defensivas)
        ? proc.teses_defensivas.length
        : Number(proc.teses_defensivas || 0);
      const podeAbrir = st === "pronto" || st === "pendencias";
      const pidEsc = escapeHtml(proc.pid || "");
      const rotuloProc = escapeHtml(numero || arq || proc.pid || "Processo");

      return `<tr>
        <td class="lote-col-proc">
          <div class="mono lote-proc-num">${escapeHtml(numero || "Sem número extraído")}</div>
          <div class="muted lote-proc-arq">${escapeHtml(arq)}</div>
        </td>
        <td><span class="lote-status-badge ${stCls}">${escapeHtml(stLabel)}</span></td>
        <td class="mono">${pecas}</td>
        <td class="mono">${teses}</td>
        <td class="lote-col-pend">${_formatarPendencias(proc)}</td>
        <td class="lote-col-acao">
          <button type="button" class="btn btn-sm btn-primary btn-lote-abrir"
            data-lote-id="${loteIdEsc}" data-pid="${pidEsc}" data-rotulo="${rotuloProc}"
            ${podeAbrir ? "" : "disabled"}>Abrir no estúdio</button>
        </td>
      </tr>`;
    })
    .join("");
}

async function _carregarLote(loteId, { agendarPoll = true } = {}) {
  if (!loteId) {
    _renderManifest(null);
    return;
  }
  try {
    const manifest = await apiJson(`/api/lote/${encodeURIComponent(loteId)}`);
    _loteState.loteAtualId = manifest.lote_id;
    _renderManifest(manifest);
    const modal = $("modalLote");
    const emAndamento = manifest.status === "pendente" || manifest.status === "executando";
    if (agendarPoll && emAndamento && modal && !modal.hidden) {
      _pararPollingLote();
      _loteState.pollTimer = setTimeout(() => _carregarLote(loteId, { agendarPoll: true }), 400);
    } else if (!emAndamento) {
      _pararPollingLote();
      await _carregarListaLotes({ selecionarPrimeiro: false });
    }
  } catch (err) {
    _pararPollingLote();
    _mostrarErroLote(err.message || "Falha ao consultar o status do lote.");
  }
}

async function _carregarListaLotes({ selecionarPrimeiro = true } = {}) {
  try {
    const data = await apiJson("/api/lote");
    _loteState.lotes = Array.isArray(data.lotes) ? data.lotes : [];
    if (selecionarPrimeiro && !_loteState.loteAtualId && _loteState.lotes.length) {
      _loteState.loteAtualId = _loteState.lotes[0].lote_id;
    }
    _renderSeletorLotes();
  } catch (err) {
    _mostrarErroLote(err.message || "Não foi possível listar os lotes.");
  }
}

async function _enviarLote() {
  const lista = _loteState.arquivosSelecionados;
  if (!lista.length) return;
  _mostrarErroLote("");
  _ocultarConfirmacaoTroca();

  const btn = $("btnLoteEnviar");
  if (btn) btn.disabled = true;

  try {
    const fd = new FormData();
    for (const f of lista) {
      fd.append("files", f, f.name);
    }
    const resp = await apiFetch("/api/lote", { method: "POST", body: fd });
    let body = {};
    try {
      body = await resp.json();
    } catch (_) {
      body = {};
    }
    if (!resp.ok) {
      const msg = typeof body.detail === "string" ? body.detail : `Erro HTTP ${resp.status} ao enviar lote.`;
      _mostrarErroLote(msg);
      showToast(msg, "error");
      return;
    }

    _loteState.arquivosSelecionados = [];
    const input = $("loteFileInput");
    if (input) input.value = "";
    _renderArquivosSelecionados();

    _loteState.loteAtualId = body.lote_id;
    await _carregarListaLotes({ selecionarPrimeiro: false });
    await _carregarLote(body.lote_id, { agendarPoll: true });
    showToast(`Lote iniciado com ${Number(body.total || 0)} processo(s).`, "info");
  } catch (err) {
    _mostrarErroLote(err.message || "Falha ao enviar os PDFs do lote.");
    handleApiError(err);
  } finally {
    _renderArquivosSelecionados();
  }
}

async function _cancelarLoteAtual() {
  const loteId = _loteState.loteAtualId;
  if (!loteId) return;
  try {
    const manifest = await postJson(`/api/lote/${encodeURIComponent(loteId)}/cancelar`, {});
    _renderManifest(manifest);
    showToast("Cancelamento do lote solicitado.", "warning");
  } catch (err) {
    handleApiError(err);
  }
}

async function _abrirProcessoNoEstudio(loteId, pid, rotulo, { confirmarTroca = false } = {}) {
  _mostrarErroLote("");
  try {
    const resp = await postJson(
      `/api/lote/${encodeURIComponent(loteId)}/processos/${encodeURIComponent(pid)}/abrir`,
      { confirmar_troca: confirmarTroca }
    );
    _ocultarConfirmacaoTroca();
    _pararPollingLote();
    if (typeof hooks.applyActiveCase === "function") {
      await hooks.applyActiveCase(resp, { clearSentence: true });
    }
    closeModal("modalLote");
    setActiveStation("autos");
    showToast(`Processo aberto no estúdio: ${rotulo || pid}.`, "success");
  } catch (err) {
    const body = (err && err.body) || {};
    const detailObj = body.detail && typeof body.detail === "object" ? body.detail : null;
    const codigoErro = (detailObj && detailObj.erro) || body.erro || "";
    if (err && err.status === 409 && codigoErro === "producao_em_curso") {
      _loteState.trocaPendente = { loteId, pid, rotulo };
      const box = $("loteConfirmTrocaBox");
      const resumo = $("loteConfirmTrocaResumo");
      const chk = $("chkLoteConfirmarTroca");
      const btn = $("btnLoteConfirmarTroca");
      if (resumo) {
        resumo.textContent = `Há produção ou processo ativo em curso. Confirmar abertura de "${rotulo || pid}" substituirá o caso ativo e descartará a produção atual.`;
      }
      if (chk) chk.checked = false;
      if (btn) btn.disabled = true;
      if (box) {
        box.hidden = false;
        box.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
      return;
    }
    const msg = (detailObj && detailObj.detail) || err.message || "Falha ao abrir o processo no estúdio.";
    _mostrarErroLote(msg);
    showToast(msg, "error");
  }
}

async function abrirModalLote() {
  _mostrarErroLote("");
  _ocultarConfirmacaoTroca();
  _renderArquivosSelecionados();
  openModal("modalLote");
  await _carregarListaLotes({ selecionarPrimeiro: true });
  if (_loteState.loteAtualId) {
    await _carregarLote(_loteState.loteAtualId, { agendarPoll: true });
  } else {
    _renderManifest(null);
  }
}

function bindLoteEvents() {
  const dropzone = $("loteDropzone");
  const input = $("loteFileInput");

  dropzone?.addEventListener("click", (e) => {
    if (e.target.closest("button")) return;
    input?.click();
  });
  dropzone?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      input?.click();
    }
  });
  dropzone?.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("is-dragover");
  });
  dropzone?.addEventListener("dragleave", () => {
    dropzone.classList.remove("is-dragover");
  });
  dropzone?.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("is-dragover");
    if (e.dataTransfer?.files?.length) _adicionarArquivos(e.dataTransfer.files);
  });

  $("btnLoteEscolherPdfs")?.addEventListener("click", () => input?.click());
  input?.addEventListener("change", () => {
    if (input.files?.length) _adicionarArquivos(input.files);
  });
  $("btnLoteLimparSelecao")?.addEventListener("click", () => {
    _loteState.arquivosSelecionados = [];
    if (input) input.value = "";
    _renderArquivosSelecionados();
  });
  $("btnLoteEnviar")?.addEventListener("click", _enviarLote);
  $("btnLoteCancelar")?.addEventListener("click", _cancelarLoteAtual);

  $("loteSelect")?.addEventListener("change", (e) => {
    const val = e.target.value;
    _loteState.loteAtualId = val || null;
    _ocultarConfirmacaoTroca();
    _carregarLote(_loteState.loteAtualId, { agendarPoll: true });
  });

  $("loteTableBody")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".btn-lote-abrir");
    if (!btn || btn.disabled) return;
    _abrirProcessoNoEstudio(btn.dataset.loteId, btn.dataset.pid, btn.dataset.rotulo, { confirmarTroca: false });
  });

  $("chkLoteConfirmarTroca")?.addEventListener("change", (e) => {
    const btn = $("btnLoteConfirmarTroca");
    if (btn) btn.disabled = !e.target.checked;
  });
  $("btnLoteCancelarTroca")?.addEventListener("click", _ocultarConfirmacaoTroca);
  $("btnLoteConfirmarTroca")?.addEventListener("click", () => {
    const pend = _loteState.trocaPendente;
    if (!pend) return;
    _abrirProcessoNoEstudio(pend.loteId, pend.pid, pend.rotulo, { confirmarTroca: true });
  });

  document.querySelectorAll('[data-close-modal="modalLote"]').forEach((btn) => {
    btn.addEventListener("click", () => {
      _pararPollingLote();
      _ocultarConfirmacaoTroca();
    });
  });
}

export { abrirModalLote, bindLoteEvents };
