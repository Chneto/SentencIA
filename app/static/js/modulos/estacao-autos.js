// SentencIA — Módulo ES: estacao-autos.js (TASK-015)
import { hooks, state } from "./estado.js?v=2.1.0";
import { $, envioExternoPermitido, escapeHtml, fmtDateBr, openModal, showToast } from "./dom.js?v=2.1.0";
import { ApiError, acompanharJob, apiFetch, atualizarBarraProgresso, esconderProgresso, handleApiError, lerJson, mostrarProgresso, postJson, startProgressPolling, stopProgressPolling } from "./api.js?v=2.1.0";

function initDropzones() {
  const setupZone = (zoneId, inputId, badgeId, emptyLabel) => {
    const zone = $(zoneId);
    const input = $(inputId);
    const badge = $(badgeId);
    if (!zone || !input || !badge) return;

    const refreshBadge = () => {
      const files = input.files ? Array.from(input.files) : [];
      if (!files.length) {
        zone.classList.remove("has-files");
        badge.textContent = emptyLabel;
        return;
      }
      zone.classList.add("has-files");
      const totalKb = Math.max(1, Math.round(files.reduce((acc, f) => acc + (f.size || 0), 0) / 1024));
      const names = files.slice(0, 2).map((f) => f.name).join(", ");
      const extra = files.length > 2 ? ` +${files.length - 2}` : "";
      badge.textContent = `${files.length} selecionado(s): ${names}${extra} (${totalKb} KB)`;
    };

    input.addEventListener("change", refreshBadge);

    ["dragenter", "dragover"].forEach((evName) => {
      zone.addEventListener(evName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        zone.classList.add("drag-over");
      });
    });

    ["dragleave", "dragend", "drop"].forEach((evName) => {
      zone.addEventListener(evName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        zone.classList.remove("drag-over");
      });
    });

    zone.addEventListener("drop", (e) => {
      const dtFiles = e.dataTransfer?.files;
      if (dtFiles && dtFiles.length) {
        try {
          input.files = dtFiles;
        } catch (_) {
          // navegador não permite atribuir FileList diretamente
        }
        refreshBadge();
      }
    });
  };

  setupZone("dropzonePdf", "inputPdfFiles", "selectedPdfFilesBadge", "Nenhum arquivo selecionado");
  setupZone("dropzoneMedia", "inputMediaFiles", "selectedMediaFilesBadge", "Nenhuma mídia selecionada");
}

function ocrMotorDisponivel() {
  return Boolean(state.status?.ocr?.motor);
}

function renderOcrBanner() {
  const banner = $("ocrUnavailableBanner");
  if (!banner) return;
  const semMotor = Boolean(state.status) && !ocrMotorDisponivel();
  banner.hidden = !semMotor;
  if (state.activeCase?.upload_status) {
    renderUploadStatusTables(state.activeCase.upload_status);
  }
}

function badgeStatusArquivo(r) {
  const st = String(r.status || "").toUpperCase();
  const ocrEstado = String(r.ocr_estado || "").toUpperCase();
  if (st === "FALHOU") return '<span class="badge-tag badge-danger">FALHOU</span>';
  if (ocrEstado === "OCR_EM_ANDAMENTO") {
    return '<span class="badge-tag badge-info">OCR EM ANDAMENTO</span>';
  }
  const nIndisp = Number(r.ocr_indisponivel || r.ocr_pendente || 0);
  if (ocrEstado === "OCR_INDISPONIVEL" || (st === "PROCESSADO" && nIndisp > 0)) {
    const tip = `Motor de OCR não instalado. ${nIndisp} página(s) digitalizada(s) ficaram sem texto. Feche o programa e abra pelo 'Iniciar SentencIA'.`;
    return `<span class="badge-tag badge-danger" title="${escapeHtml(tip)}">OCR INDISPONÍVEL</span>`;
  }
  const nFalhou = Number(r.ocr_falhou || 0);
  if (ocrEstado === "OCR_FALHOU" || nFalhou > 0) {
    const pags = Array.isArray(r.ocr_paginas_falha) ? r.ocr_paginas_falha : [];
    const rotulo = pags.length === 1 ? `OCR FALHOU NA PÁGINA ${pags[0]}` : `OCR FALHOU (${nFalhou} PÁG.)`;
    return `<span class="badge-tag badge-danger">${escapeHtml(rotulo)}</span>`;
  }
  const nRec = Number(r.ocr_paginas_reconhecidas || 0);
  if (ocrEstado === "OCR_CONCLUIDO" || nRec > 0) {
    return `<span class="badge-tag badge-success" title="${escapeHtml(`${nRec} página(s) reconhecida(s) por OCR`)}">OCR CONCLUÍDO</span>`;
  }
  if (st === "PROCESSADO") return '<span class="badge-tag badge-success">PROCESSADO</span>';
  return `<span class="badge-tag badge-warn">${escapeHtml(st || "—")}</span>`;
}

function renderUploadStatusTables(upStatus) {
  const pdfs = upStatus?.pdfs_processo || [];
  const medias = upStatus?.midias_audiencia || [];
  const temMotorOcr = ocrMotorDisponivel();

  const renderList = (rows, emptyMsg, isPdf = false) => {
    if (!rows.length) {
      return `<p class="muted" style="margin:6px 0 0; font-size:12px;">${escapeHtml(emptyMsg)}</p>`;
    }
    return `
      <table class="data-table" style="margin-top:8px;">
        <thead>
          <tr>
            <th>Arquivo</th>
            <th>Status</th>
            <th>Enviado em</th>
            <th>Processado em</th>
          </tr>
        </thead>
        <tbody>
          ${rows
            .map((r) => {
              const extra = [];
              if (r.media_id) extra.push(`ID: ${r.media_id}`);
              if (r.engine) extra.push(`Motor: ${r.engine}`);
              const precisaRefazer =
                isPdf &&
                (Number(r.ocr_indisponivel || r.ocr_pendente || 0) > 0 ||
                  Number(r.ocr_falhou || 0) > 0 ||
                  String(r.ocr_estado || "").toUpperCase() === "OCR_INDISPONIVEL" ||
                  String(r.ocr_estado || "").toUpperCase() === "OCR_FALHOU");
              const btnRefazer = precisaRefazer
                ? `<div style="margin-top:6px;"><button type="button" class="btn btn-sm" data-refazer-ocr="${escapeHtml(r.filename)}" ${
                    temMotorOcr ? "" : 'disabled title="Motor de OCR não instalado"'
                  }>Refazer OCR</button></div>`
                : "";
              return `
            <tr>
              <td>
                <div style="font-weight:600;">${escapeHtml(r.filename)}</div>
                <div class="muted" style="font-size:11px;">${escapeHtml(r.detalhes || `${r.tamanho_kb || 0} KB`)}</div>
                ${extra.length ? `<div class="muted mono" style="font-size:10.5px;">${escapeHtml(extra.join(" · "))}</div>` : ""}
              </td>
              <td>${badgeStatusArquivo(r)}${btnRefazer}</td>
              <td class="mono" style="font-size:11.5px;">${escapeHtml(r.uploaded_at || "—")}</td>
              <td class="mono" style="font-size:11.5px;">${escapeHtml(r.processed_at || "—")}</td>
            </tr>`;
            })
            .join("")}
        </tbody>
      </table>`;
  };

  $("uploadPdfStatusList").innerHTML = renderList(pdfs, "Nenhum PDF enviado nesta sessão.", true);
  $("uploadMediaStatusList").innerHTML = renderList(medias, "Nenhuma mídia enviada nesta sessão.", false);
}

function renderDiskBanner() {
  const c = state.activeCase;
  const banner = $("diskAutosBanner");
  if (!banner) return;
  const disco = (c && c.arquivos_em_disco) || { pdfs: 0, midias: 0 };
  const mostrar = !!c && (c.empty || c.is_demo) && Number(disco.pdfs || 0) > 0;
  banner.hidden = !mostrar;
  if (mostrar) {
    $("diskAutosBannerText").textContent =
      ` A pasta de casos contém ${disco.pdfs} PDF(s) e ${disco.midias || 0} mídia(s) ainda não carregados nesta sessão. ` +
      "O carregamento segmenta o PDF e transcreve as mídias localmente.";
  }
}

function renderPecasIndice(filterStr = "") {
  const pecas = state.activeCase?.pecas || [];
  const q = filterStr.trim().toLowerCase();
  const filtradas = q
    ? pecas.filter(
        (p) =>
          (p.doc_id || "").toLowerCase().includes(q) ||
          (p.titulo || "").toLowerCase().includes(q) ||
          (p.categoria || "").toLowerCase().includes(q)
      )
    : pecas;

  if (!filtradas.length) {
    $("pecasListContainer").innerHTML = `<p class="muted">Nenhuma peça encontrada.</p>`;
  } else {
    $("pecasListContainer").innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            <th>ID PJe</th>
            <th>Peça</th>
            <th>Categoria</th>
            <th>Data</th>
            <th>OCR</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${filtradas
            .map(
              (p) => `
            <tr>
              <td class="mono" style="font-weight:700; color:var(--accent-primary);">${escapeHtml(p.doc_id)}</td>
              <td>
                <div style="font-weight:600;">${escapeHtml(p.titulo)}</div>
                <div class="muted" style="font-size:11.5px;">${escapeHtml((p.resumo_estruturado || "").slice(0, 140))}</div>
              </td>
              <td><span class="badge-tag badge-neutral">${escapeHtml(p.categoria)}</span></td>
              <td class="mono" style="font-size:12px;">${escapeHtml(fmtDateBr(p.data_peca, "não identificada"))}</td>
              <td>${
                p.ocr_estado === "OCR_INDISPONIVEL" || p.ocr_pendente
                  ? '<span class="badge-tag badge-danger" title="Motor de OCR não instalado. Feche o programa e abra pelo \'Iniciar SentencIA\'.">OCR INDISPONÍVEL</span>'
                  : p.ocr_estado === "OCR_FALHOU" || Number(p.ocr_falhou || 0) > 0
                  ? `<span class="badge-tag badge-danger">${
                      Array.isArray(p.ocr_paginas_falha) && p.ocr_paginas_falha.length === 1
                        ? `OCR FALHOU NA PÁGINA ${p.ocr_paginas_falha[0]}`
                        : "OCR FALHOU"
                    }</span>`
                  : p.ocr_aplicado
                  ? '<span class="badge-tag badge-success">OCR CONCLUÍDO</span>'
                  : '<span class="badge-tag badge-neutral">Nativo</span>'
              }</td>
              <td><button type="button" class="btn btn-sm" data-inspect-id="${escapeHtml(p.doc_id)}">Abrir</button></td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  }

  const meta = state.activeCase?.metadados || {};
  $("cacDetailsContainer").innerHTML =
    !state.activeCase || state.activeCase.empty
      ? `<p class="muted">Carregue um processo para inspecionar a CAC/FAC.</p>`
      : `<div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap;">
        <span class="badge-tag ${meta.reu_primario_sumula444 ? "badge-success" : "badge-warn"}">
          ${meta.reu_primario_sumula444 ? "Primário / Bons Antecedentes (Súmula 444 STJ)" : "Verificar Certidões de Antecedentes"}
        </span>
        <span class="text-sec">Autos MPU Apenso: <strong class="mono">${escapeHtml(meta.autos_mpu_apenso || "Não identificado")}</strong></span>
        <span class="text-sec">Vínculo: <strong>${escapeHtml(meta.vinculo_domestico || "—")}</strong></span>
      </div>`;
}

function arquivoAcimaDoLimite(files, campoLimite) {
  const limiteMb = Number((state.settings || {})[campoLimite] || 0);
  if (!limiteMb) return null;
  const limiteBytes = limiteMb * 1024 * 1024;
  const grande = Array.from(files || []).find((f) => Number(f.size || 0) > limiteBytes);
  return grande ? { nome: grande.name, limiteMb } : null;
}

async function enviarUpload(endpoint, fd, botao, mensagemSucesso) {
  fd.append("assincrono", "true");
  if (botao) botao.disabled = true;
  mostrarProgresso("Enviando arquivos ao servidor local...");
  try {
    const resp = await apiFetch(endpoint, { method: "POST", body: fd });
    const body = await lerJson(resp);
    let resultado;
    if (resp.status === 202 && body.job_id) {
      resultado = await acompanharJob(body.job_id);
    } else if (resp.ok) {
      resultado = body;
    } else {
      throw new ApiError(resp.status, body);
    }
    atualizarBarraProgresso({ percent: 100, phase: "concluido" });
    await hooks.applyActiveCase(resultado, { clearSentence: true });
    const midias = resultado?.upload_status?.midias_audiencia || [];
    const pdfs = resultado?.upload_status?.pdfs_processo || [];
    const falhas = midias.filter((m) => String(m.status || "").toUpperCase() === "FALHOU");
    const pdfsSemOcr = pdfs.filter(
      (p) =>
        String(p.ocr_estado || "").toUpperCase() === "OCR_INDISPONIVEL" ||
        Number(p.ocr_indisponivel || p.ocr_pendente || 0) > 0
    );
    if (pdfsSemOcr.length) {
      showToast(
        "OCR desativado: motor de OCR não instalado. Páginas digitalizadas ficaram sem texto — feche o programa e abra pelo 'Iniciar SentencIA'.",
        "warn"
      );
    } else if (falhas.length) {
      showToast(
        `${mensagemSucesso} Atenção: ${falhas.length} mídia(s) não foram transcritas (veja o status e o erro na lista).`,
        "warn"
      );
    } else {
      showToast(mensagemSucesso, "success");
    }
    esconderProgresso(1500);
  } catch (err) {
    esconderProgresso();
    handleApiError(err);
  } finally {
    if (botao) botao.disabled = false;
  }
}

async function carregarAutosDoDisco() {
  const botao = $("btnLoadFromDisk");
  if (botao) botao.disabled = true;
  startProgressPolling();
  try {
    const res = await postJson("/api/cases/load-from-disk", {
      consentimento_envio_externo: envioExternoPermitido() && !!$("chkConsentimentoDisco")?.checked,
    });
    await hooks.applyActiveCase(res, { clearSentence: true });
    showToast("Autos da pasta de casos carregados.", "success");
  } catch (err) {
    handleApiError(err);
  } finally {
    stopProgressPolling();
    esconderProgresso(800);
    if (botao) botao.disabled = false;
  }
}

async function refazerOcrPdf(filename, botao) {
  if (botao) botao.disabled = true;
  startProgressPolling();
  try {
    const res = await postJson("/api/cases/refazer-ocr", { filename: filename || null });
    atualizarBarraProgresso({ percent: 100, phase: "concluido", active: false });
    await hooks.applyActiveCase(res, { clearSentence: true });
    showToast(`OCR reprocessado para ${filename || "o PDF do processo"}.`, "success");
  } catch (err) {
    handleApiError(err);
  } finally {
    stopProgressPolling();
    esconderProgresso(1000);
    if (botao) botao.disabled = !ocrMotorDisponivel();
  }
}

function bindAutosEvents() {
  $("searchPecasInput")?.addEventListener("input", (e) => renderPecasIndice(e.target.value));

  $("uploadPdfStatusList")?.addEventListener("click", (e) => {
    const btn = e.target?.closest?.("[data-refazer-ocr]");
    if (!btn || btn.disabled) return;
    refazerOcrPdf(btn.getAttribute("data-refazer-ocr"), btn);
  });

  $("btnCancelarIngestao")?.addEventListener("click", async () => {
    try {
      await postJson("/api/cases/cancelar-ingestao", {});
      showToast("Cancelamento do OCR solicitado.", "warn");
    } catch (err) {
      handleApiError(err);
    }
  });

  $("btnOcrUnavailableInfo")?.addEventListener("click", () => {
    openModal("modalTelemetry");
  });

  $("formUploadPdfs")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const files = $("inputPdfFiles")?.files;
    if (!files || !files.length) return;
    const excede = arquivoAcimaDoLimite(files, "max_upload_mb_pdf");
    if (excede) {
      showToast(`O arquivo '${excede.nome}' excede o limite de ${excede.limiteMb} MB para PDFs (ajustável em max_upload_mb_pdf).`, "error");
      return;
    }
    const fd = new FormData();
    Array.from(files).forEach((f) => fd.append("files", f));
    await enviarUpload("/api/cases/upload-pdfs", fd, $("btnUploadPdfs"), "PDFs do processo segmentados e classificados.");
  });

  $("formUploadMedias")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const files = $("inputMediaFiles")?.files;
    if (!files || !files.length) return;
    const excede = arquivoAcimaDoLimite(files, "max_upload_mb_midia");
    if (excede) {
      showToast(`A mídia '${excede.nome}' excede o limite de ${excede.limiteMb} MB (ajustável em max_upload_mb_midia).`, "error");
      return;
    }
    const fd = new FormData();
    Array.from(files).forEach((f) => fd.append("files", f));
    fd.append("depoente_padrao", $("inputDepoenteMedia")?.value || "OFENDIDA / TESTEMUNHA");
    const consent = envioExternoPermitido() && !!$("chkConsentimentoMidia")?.checked;
    fd.append("consentimento_envio_externo", consent ? "true" : "false");
    await enviarUpload("/api/cases/upload-medias", fd, $("btnUploadMedias"), "Mídias processadas e consolidadas.");
  });

  $("btnLoadFromDisk")?.addEventListener("click", carregarAutosDoDisco);

  $("btnLoadDemo")?.addEventListener("click", async () => {
    try {
      const data = await postJson("/api/cases/load-demo", {});
      await hooks.applyActiveCase(data, { clearSentence: true });
      showToast("Caso demonstrativo carregado (dados fictícios).", "success");
    } catch (err) {
      handleApiError(err);
    }
  });
}

export {
  arquivoAcimaDoLimite,
  badgeStatusArquivo,
  bindAutosEvents,
  carregarAutosDoDisco,
  enviarUpload,
  initDropzones,
  refazerOcrPdf,
  renderDiskBanner,
  renderOcrBanner,
  renderPecasIndice,
  renderUploadStatusTables,
};
