// SentencIA — Módulo ES: estacao-analise.js (TASK-015)
import { META_CAMPOS_FORM, META_CAMPOS_LABELS, hooks, state } from "./estado.js?v=2.1.0";
import { $, closeModal, codigosCatalogo, escapeHtml, fmtDateBr, fmtMoeda, isoDate, openModal, rotuloCrime, showToast } from "./dom.js?v=2.1.0";
import { ApiError, acompanharJob, apiFetch, apiJson, handleApiError, lerJson, postJson } from "./api.js?v=2.1.0";

function rotuloCoerencia(valor) {
  const v = String(valor || "");
  if (v === "PENDENTE_AVALIACAO_JUIZO") return { txt: "Pendente de avaliação do juízo", cls: "badge-warn" };
  if (v === "ALTA" || v === "COERENTE") return { txt: v, cls: "badge-success" };
  if (!v) return { txt: "Não avaliada", cls: "badge-neutral" };
  return { txt: v, cls: "badge-neutral" };
}

function renderOralFalhas(c) {
  const cont = $("oralFalhasContainer");
  if (!cont) return;
  const falhasTr = (c.transcricoes || []).filter((t) => String(t.status || "").toUpperCase() === "FALHOU");
  const semTr = ((c.matriz_oral || {}).midias_sem_transcricao || []).slice();
  const vistos = new Set();
  const linhas = [];
  falhasTr.forEach((t) => {
    const k = t.media_id || t.arquivo;
    vistos.add(k);
    linhas.push(`<li><strong>${escapeHtml(t.arquivo || t.media_id || "mídia")}</strong>${t.media_id ? ` <span class="mono">(${escapeHtml(t.media_id)})</span>` : ""}: ${escapeHtml(t.erro || "transcrição não realizada")}</li>`);
  });
  semTr.forEach((m) => {
    const k = m.media_id || m.arquivo;
    if (vistos.has(k)) return;
    linhas.push(`<li><strong>${escapeHtml(m.arquivo || m.media_id || "mídia")}</strong>${m.media_id ? ` <span class="mono">(${escapeHtml(m.media_id)})</span>` : ""}: ${escapeHtml(m.motivo || "sem transcrição utilizável")}</li>`);
  });
  if (!linhas.length) {
    cont.innerHTML = "";
    return;
  }
  cont.innerHTML = `
    <div class="alert-banner alert-warn">
      <div>
        <strong>Mídias sem transcrição utilizável (não consideradas na prova oral):</strong>
        <ul class="alert-list">${linhas.join("")}</ul>
        <div class="muted" style="font-size:11.5px; margin-top:6px;">Nenhum texto foi inventado para essas mídias. Instale o Whisper local ou, se permitido, reenvie com consentimento de envio externo.</div>
      </div>
    </div>`;
}

function renderAlertasExtracao(c) {
  const cont = $("alertasExtracaoContainer");
  if (!cont) return;
  if (!c || c.empty) {
    cont.innerHTML = "";
    return;
  }
  const alertas = (c.alertas_extracao && c.alertas_extracao.length ? c.alertas_extracao : (c.metadados || {}).alertas_extracao) || [];
  const ov = c.metadados_override || {};
  const ovKeys = Object.keys(ov);
  const partes = [];
  if (alertas.length) {
    partes.push(`
      <div class="alert-banner alert-warn">
        <div>
          <strong>Alertas da extração automática (confira e complemente se necessário):</strong>
          <ul class="alert-list">${alertas.map((a) => `<li>${escapeHtml(a)}</li>`).join("")}</ul>
        </div>
      </div>`);
  }
  if (ovKeys.length) {
    partes.push(`
      <div class="alert-banner alert-info">
        <div>
          <strong>Dados informados pelo juízo (prevalecem sobre a extração):</strong>
          <ul class="alert-list">${ovKeys
            .map((k) => {
              const v = ov[k];
              const txt = Array.isArray(v) ? v.map(rotuloCrime).join("; ") : k.startsWith("data_") ? fmtDateBr(v) : typeof v === "boolean" ? (v ? "sim" : "não") : String(v);
              return `<li>${escapeHtml(META_CAMPOS_LABELS[k] || k)}: ${escapeHtml(txt)}</li>`;
            })
            .join("")}</ul>
        </div>
      </div>`);
  }
  cont.innerHTML = partes.join("");
}

function renderAnaliseProvas() {
  const c = state.activeCase;
  if (!c || c.empty) {
    $("timelineContainer").innerHTML = `<p class="muted">Nenhum processo carregado.</p>`;
    $("oralConsolidadoContainer").innerHTML = `<p class="muted">Sem prova oral carregada.</p>`;
    $("oralCardsContainer").innerHTML = "";
    $("oralFalhasContainer").innerHTML = "";
    $("cnjChecklistContainer").innerHTML = `<p class="muted">Aguardando autos.</p>`;
    $("sinopseCrimeContainer").innerHTML = `<p class="muted">Aguardando autos.</p>`;
    $("tesesListContainer").innerHTML = `<p class="muted">Aguardando autos.</p>`;
    renderAlertasExtracao(null);
    return;
  }

  const pecas = c.pecas || [];
  const comData = pecas.filter((p) => isoDate(p.data_peca));
  const semData = pecas.filter((p) => !isoDate(p.data_peca));
  const sorted = [...comData.sort((a, b) => isoDate(a.data_peca).localeCompare(isoDate(b.data_peca))), ...semData];
  $("timelineContainer").innerHTML = sorted.length
    ? sorted
        .slice(0, 60)
        .map(
          (p) => `
    <div class="timeline-item">
      <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap;">
        <div>
          <button type="button" class="id-badge" data-inspect-id="${escapeHtml(p.doc_id)}">${escapeHtml(p.doc_id)}</button>
          <strong>${escapeHtml(p.titulo)}</strong>
        </div>
        <span class="mono muted" style="font-size:11.5px;">${escapeHtml(fmtDateBr(p.data_peca, "data não identificada"))} &bull; ${escapeHtml(p.folha || "")}</span>
      </div>
      <div class="text-sec" style="font-size:12.5px; margin-top:5px;">${escapeHtml(p.resumo_estruturado || "")}</div>
    </div>`
        )
        .join("")
    : `<p class="muted">Nenhuma peça extraída.</p>`;

  renderOralFalhas(c);

  const deps = (c.matriz_oral || {}).depoentes || [];
  if (!deps.length) {
    $("oralConsolidadoContainer").innerHTML = `<p class="muted">Nenhuma mídia transcrita ainda. Envie os vídeos/áudios na Etapa 1.</p>`;
    $("oralCardsContainer").innerHTML = `<p class="muted">Sem registros individuais.</p>`;
  } else {
    const grouped = new Map();
    deps.forEach((d) => {
      const k = `${d.papel}::${d.depoente}`;
      if (!grouped.has(k)) grouped.set(k, []);
      grouped.get(k).push(d);
    });
    $("oralConsolidadoContainer").innerHTML = Array.from(grouped.values())
      .map((list) => {
        const first = list[0];
        const ids = list.map((x) => `<button type="button" class="id-badge" data-inspect-id="${escapeHtml(x.media_id)}">${escapeHtml(x.media_id)}</button>`).join(" ");
        const sintese = list.map((x) => x.discurso_indireto_sentenca || "").filter(Boolean).join(" ");
        return `
          <div class="forensic-tape">
            <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:6px;">
              <div><strong>${escapeHtml(first.depoente)}</strong> <span class="badge-tag badge-neutral">${escapeHtml(first.papel)}</span></div>
              <div>${ids}</div>
            </div>
            <div style="font-size:13px;">${escapeHtml(sintese)}</div>
          </div>`;
      })
      .join("");

    $("oralCardsContainer").innerHTML = deps
      .map((d) => {
        const coer = rotuloCoerencia(d.coerencia_delegacia_juizo);
        return `
      <div class="inner-tile" style="margin-bottom:10px;">
        <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:6px; flex-wrap:wrap;">
          <div>
            <button type="button" class="id-badge" data-inspect-id="${escapeHtml(d.media_id)}">${escapeHtml(d.media_id)}</button>
            <strong>${escapeHtml(d.depoente)}</strong> (${escapeHtml(d.arquivo || "")} &bull; ${escapeHtml(d.duracao || "duração não informada")})
          </div>
          <span class="badge-tag ${coer.cls}" title="Coerência delegacia x juízo">${escapeHtml(coer.txt)}</span>
        </div>
        <div class="text-sec" style="font-size:12.5px;">${escapeHtml(d.discurso_indireto_sentenca || "")}</div>
        <div style="margin-top:6px;"><button type="button" class="btn btn-sm" data-inspect-id="${escapeHtml(d.media_id)}">Ver transcrição integral e citar (${(d.segmentos_literais || []).length} trechos)</button></div>
      </div>`;
      })
      .join("");
  }

  if (state.analisesOrais === undefined && deps.length) carregarAnalisesOrais();
  else renderAlegacoesOrais();

  const meta = c.metadados || {};
  const valorPedido = Number(meta.valor_dano_moral_pedido_mp || 0);
  const statusDano = meta.pedido_dano_moral_expresso_mp
    ? valorPedido > 0
      ? { txt: `PEDIDO: ${fmtMoeda(valorPedido)}`, cls: "badge-success" }
      : { txt: "PEDIDO SEM VALOR — ARBITRAMENTO PELO JUÍZO", cls: "badge-warn" }
    : { txt: "PEDIDO NÃO IDENTIFICADO NOS AUTOS", cls: "badge-neutral" };
  const cnjItems = [
    { titulo: "Especial relevância probatória da palavra da vítima", status: { txt: "DIRETRIZ", cls: "badge-neutral" }, obs: "Valoração a cargo do juízo, à luz do conjunto probatório dos autos." },
    { titulo: "Vedação de revitimização e valoração não estereotipada (Res. CNJ 492/2023)", status: { txt: "DIRETRIZ", cls: "badge-neutral" }, obs: "Síntese da prova oral em discurso indireto neutro, sem juízos morais." },
    { titulo: "Natureza incondicionada da ação penal na lesão corporal doméstica (Súmula 542/STJ)", status: { txt: "DIRETRIZ", cls: "badge-neutral" }, obs: "Eventual retratação informal não extingue a punibilidade na lesão corporal." },
    { titulo: "Reparação mínima por dano moral (Tema Repetitivo 983/STJ)", status: statusDano, obs: "Fixação condicionada a pedido expresso na denúncia/alegações; o valor é arbitrado pelo juízo." },
  ];
  $("cnjChecklistContainer").innerHTML = cnjItems
    .map(
      (it) => `
    <div class="inner-tile" style="display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:8px; flex-wrap:wrap;">
      <div>
        <div style="font-weight:600;">${escapeHtml(it.titulo)}</div>
        <div class="muted" style="font-size:12px;">${escapeHtml(it.obs)}</div>
      </div>
      <span class="badge-tag ${it.status.cls}">${escapeHtml(it.status.txt)}</span>
    </div>`
    )
    .join("");

  const crimes = meta.crimes_imputados || [];
  const naoExtraido = '<span style="color:var(--semantic-amber);">não extraído — informe</span>';
  const dtFato = c.data_fato || meta.data_fato;
  const dtRec = c.data_recebimento_denuncia || meta.data_recebimento_denuncia;
  $("sinopseCrimeContainer").innerHTML = `
    <div class="grid-3">
      <div class="inner-tile"><div class="kpi-label">Crimes Imputados</div><div style="margin-top:4px; font-weight:700;">${
        crimes.length ? escapeHtml(crimes.map(rotuloCrime).join(" | ")) : naoExtraido
      }</div></div>
      <div class="inner-tile"><div class="kpi-label">Data do Fato</div><div class="mono" style="margin-top:4px; font-weight:700;">${
        dtFato ? escapeHtml(fmtDateBr(dtFato)) : naoExtraido
      }</div></div>
      <div class="inner-tile"><div class="kpi-label">Recebimento Denúncia</div><div class="mono" style="margin-top:4px; font-weight:700;">${
        dtRec ? escapeHtml(fmtDateBr(dtRec)) : naoExtraido
      }</div></div>
    </div>`;
  renderAlertasExtracao(c);

  const teses = meta.teses_defesa || [];
  $("tesesListContainer").innerHTML = !teses.length
    ? `<p class="muted">Nenhuma tese defensiva identificada automaticamente nos autos (confira as alegações finais).</p>`
    : teses
        .map(
          (t) => `
      <div class="forensic-tape">
        <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
          <strong>${escapeHtml(t.codigo)} — ${escapeHtml(t.titulo)}</strong>
          <span class="badge-tag badge-neutral">${escapeHtml(t.tipo)}</span>
        </div>
        <div class="text-sec" style="font-size:12.5px; margin-top:4px;">${escapeHtml(t.descricao_defesa || "")}</div>
      </div>`
        )
        .join("");
}

/* --------------------------------------------------------------------------
   COMPLEMENTAÇÃO DE METADADOS (422 dados_insuficientes)
   -------------------------------------------------------------------------- */
function preencherFormMetadados(camposFaltantes) {
  const c = state.activeCase || {};
  const meta = c.metadados || {};
  Object.entries(META_CAMPOS_FORM).forEach(([campo, id]) => {
    const el = $(id);
    if (!el) return;
    const v = meta[campo];
    if (campo.startsWith("data_")) el.value = isoDate(v);
    else if (campo === "valor_dano_moral_pedido_mp") el.value = Number(v || 0) > 0 ? String(v) : "";
    else el.value = v ? String(v) : "";
  });
  $("metaPedidoDanoMoral").checked = !!meta.pedido_dano_moral_expresso_mp;

  const atuais = new Set(meta.crimes_imputados || []);
  const codigos = codigosCatalogo();
  atuais.forEach((c2) => {
    if (!codigos.includes(c2)) codigos.push(c2);
  });
  $("metaCrimesGrid").innerHTML = codigos
    .map(
      (code) => `
      <label><input type="checkbox" value="${escapeHtml(code)}" ${atuais.has(code) ? "checked" : ""} /><span>${escapeHtml(rotuloCrime(code))}</span></label>`
    )
    .join("");

  const faltantes = new Set(camposFaltantes || []);
  document.querySelectorAll("#formMetadata [data-meta-field]").forEach((el) => {
    el.classList.toggle("campo-faltante", faltantes.has(el.dataset.metaField));
  });

  const alertas = c.alertas_extracao || meta.alertas_extracao || [];
  $("metadataAlertas").innerHTML = alertas.length
    ? `<div class="alert-banner alert-warn"><div><strong>Alertas da extração:</strong><ul class="alert-list">${alertas
        .map((a) => `<li>${escapeHtml(a)}</li>`)
        .join("")}</ul></div></div>`
    : "";
}

function abrirComplementacao(campos, mensagem, retry) {
  if (!state.activeCase || state.activeCase.empty) {
    showToast(mensagem || "Carregue os autos antes de complementar os dados.", "warn");
    return;
  }
  state.pendingRetry = typeof retry === "function" ? retry : null;
  const lista = (campos || []).map((c) => META_CAMPOS_LABELS[c] || c);
  $("metadataSubtitle").textContent = lista.length
    ? `${mensagem || "Dados não extraídos dos autos."} Campos necessários: ${lista.join(", ")}.`
    : "Informe os dados que não puderam ser extraídos com segurança dos autos. Nada é preenchido automaticamente.";
  preencherFormMetadados(campos);
  openModal("modalMetadata");
}

async function enviarMetadados(e) {
  e.preventDefault();
  const c = state.activeCase || {};
  const meta = c.metadados || {};
  const out = {};
  Object.entries(META_CAMPOS_FORM).forEach(([campo, id]) => {
    const el = $(id);
    if (!el) return;
    const v = String(el.value || "").trim();
    if (!v) return;
    if (campo === "valor_dano_moral_pedido_mp") {
      const n = Number(v);
      if (Number.isFinite(n) && n > 0 && n !== Number(meta[campo] || 0)) out[campo] = n;
      return;
    }
    const atual = campo.startsWith("data_") ? isoDate(meta[campo]) : String(meta[campo] || "");
    if (v !== atual) out[campo] = v;
  });
  const pedido = !!$("metaPedidoDanoMoral").checked;
  if (pedido !== !!meta.pedido_dano_moral_expresso_mp) out.pedido_dano_moral_expresso_mp = pedido;
  const crimesSel = Array.from($("metaCrimesGrid").querySelectorAll("input[type=checkbox]:checked")).map((i) => i.value);
  const crimesAtuais = (meta.crimes_imputados || []).slice();
  if (crimesSel.length && crimesSel.slice().sort().join(",") !== crimesAtuais.slice().sort().join(",")) {
    out.crimes_imputados = crimesSel;
  }

  const retry = state.pendingRetry;
  if (!Object.keys(out).length) {
    showToast("Nenhum dado novo informado.", "warn");
    return;
  }
  const botao = $("btnSubmitMetadata");
  botao.disabled = true;
  try {
    const res = await postJson("/api/cases/metadata-override", { metadados: out });
    state.pendingRetry = null;
    closeModal("modalMetadata");
    await hooks.applyActiveCase(res.active_case, { clearSentence: true });
    showToast("Dados dos autos complementados pelo juízo. A minuta anterior foi descartada.", "success");
    if (typeof retry === "function") await retry();
  } catch (err) {
    if (err.body && err.body.erro === "metadados_invalidos") {
      const campos = err.body.campos || [];
      document.querySelectorAll("#formMetadata [data-meta-field]").forEach((el) => {
        el.classList.toggle("campo-faltante", campos.includes(el.dataset.metaField));
      });
      showToast(err.message, "error");
    } else {
      handleApiError(err);
    }
  } finally {
    botao.disabled = false;
  }
}

const ROTULO_FALANTE = { JUIZ: "Juiz(a)", MP: "MP", DEFESA: "Defesa", DEPOENTE: "Depoente", INCERTO: "a conferir" };

async function carregarAnalisesOrais() {
  try {
    const r = await apiJson("/api/cases/oral/analise");
    state.analisesOrais = r.analises || {};
  } catch (_) {
    state.analisesOrais = {};
  }
  renderAlegacoesOrais();
}

function renderAlegacoesOrais() {
  const box = $("oralAlegacoesContainer");
  if (!box) return;
  const deps = state.activeCase?.matriz_oral?.depoentes || [];
  const an = state.analisesOrais || {};
  const comAnalise = deps.filter((d) => an[d.media_id] && (an[d.media_id].alegacoes || []).length);
  if (!comAnalise.length) {
    box.innerHTML = Object.keys(an).length
      ? `<p class="muted">Análise concluída, mas nenhuma alegação com trecho comprovado foi encontrada. Consulte a transcrição integral.</p>`
      : `<p class="muted">Ainda não analisado.</p>`;
    return;
  }
  box.innerHTML = comAnalise
    .map((d) => {
      const a = an[d.media_id];
      const itens = a.alegacoes
        .map(
          (x, i) => `
        <li style="margin:6px 0;">
          ${escapeHtml(x.alegacao)}
          <div class="muted" style="font-size:12px; margin-top:2px;">“${escapeHtml(x.trecho)}” <span class="mono">(${escapeHtml(x.timestamp || "--:--")})</span>
            <button type="button" class="btn btn-sm" data-citar-alegacao="${escapeHtml(d.media_id)}" data-citar-idx="${i}">Citar</button></div>
        </li>`
        )
        .join("");
      const desc = a.descartadas_sem_lastro ? ` <span class="muted" style="font-size:11.5px;">(${a.descartadas_sem_lastro} descartada(s) sem trecho comprovado)</span>` : "";
      return `<div class="inner-tile" style="margin-bottom:10px;"><strong>${escapeHtml(d.depoente)}</strong> <span class="badge-tag badge-neutral">${escapeHtml(d.papel)}</span>${desc}<ul style="margin:6px 0 0 18px; padding:0;">${itens}</ul></div>`;
    })
    .join("");
}

async function analisarFalas() {
  const btn = $("btnAnalisarFalas");
  if (btn) btn.disabled = true;
  showToast("Analisando falas e alegações com a IA local. Pode levar alguns minutos.", "info");
  try {
    const resp = await apiFetch("/api/cases/oral/analisar", { method: "POST" });
    const body = await lerJson(resp);
    if (!resp.ok) throw new ApiError(resp.status, body);
    const resultado = await acompanharJob(body.job_id);
    state.analisesOrais = resultado.analises || {};
    renderAlegacoesOrais();
    showToast("Análise concluída. Confira as alegações com o áudio antes de citar.", "ok");
  } catch (err) {
    handleApiError(err, {});
  } finally {
    if (btn) btn.disabled = false;
  }
}

function openEvidenceInspector(rawId) {
  const idClean = String(rawId || "").replace(/^FONTE:\s*/i, "").trim().split("/")[0].trim();
  const pecas = state.activeCase?.pecas || [];
  const deps = state.activeCase?.matriz_oral?.depoentes || [];

  const peca = pecas.find((p) => p.doc_id === idClean);
  const dep = deps.find((d) => d.media_id === idClean);

  if (peca) {
    $("evidenceDrawerTitle").textContent = `${peca.doc_id} — ${peca.titulo}`;
    $("evidenceDrawerMeta").textContent = `${peca.categoria} | ${fmtDateBr(peca.data_peca, "data não identificada")} | ${peca.folha || ""}`;
    const ents = (peca.entidades_extraidas || []).filter((e) => e && e.valor);
    $("evidenceDrawerEntities").innerHTML = ents
      .slice(0, 12)
      .map((e) => `<span class="badge-tag badge-neutral">${escapeHtml(e.tipo ? `${e.tipo}: ${e.valor}` : e.valor)}</span>`)
      .join("");
    let bodyHtml = escapeHtml(peca.conteudo || peca.resumo_estruturado || "");
    const valores = Array.from(new Set(ents.map((e) => String(e.valor || "").trim()).filter((v) => v.length >= 4))).sort((a, b) => b.length - a.length);
    if (valores.length) {
      const alt = valores.map((v) => escapeHtml(v).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
      bodyHtml = bodyHtml.replace(new RegExp(`(${alt})`, "gi"), '<mark class="forensic-entity-mark">$1</mark>');
    }
    if (peca.ocr_estado === "OCR_INDISPONIVEL" || peca.ocr_pendente) {
      bodyHtml = `<strong style="color:var(--semantic-red);">OCR indisponível: motor de OCR não instalado. Feche o programa e abra pelo 'Iniciar SentencIA'.</strong>\n\n` + bodyHtml;
    } else if (peca.ocr_estado === "OCR_FALHOU" || Number(peca.ocr_falhou || 0) > 0) {
      bodyHtml = `<strong style="color:var(--semantic-red);">Falha no motor de OCR em ${Number(peca.ocr_falhou || 1)} página(s) digitalizada(s).</strong>\n\n` + bodyHtml;
    }
    $("evidenceDrawerBody").innerHTML = bodyHtml;
    openModal("modalEvidence");
    return;
  }

  if (dep) {
    $("evidenceDrawerTitle").textContent = `${dep.media_id} — ${dep.depoente}`;
    $("evidenceDrawerMeta").textContent = `${dep.papel} | ${dep.arquivo || ""} | Duração: ${dep.duracao || "não informada"}`;
    $("evidenceDrawerEntities").innerHTML = `<span class="badge-tag badge-neutral">${escapeHtml(dep.papel)}</span>`;
    const segs = (dep.segmentos_literais || dep.segmentos || []).filter((s) => s && String(s.texto_literal || "").trim());
    const falantes = ((state.analisesOrais || {})[dep.media_id] || {}).falantes || [];
    const body = $("evidenceDrawerBody");
    if (!segs.length) {
      body.textContent = dep.discurso_indireto_sentenca || "";
    } else {
      body.innerHTML =
        `<div class="muted" style="font-size:12px; margin-bottom:8px;">Transcrição automática (Whisper), sem revisão: confira com o áudio antes de citar. Clique em “Citar” para copiar o trecho com a referência da mídia e do minuto.</div>` +
        segs
          .map(
            (s, i) => `
          <div class="trans-seg" style="display:flex; gap:10px; align-items:flex-start; padding:5px 0; border-bottom:1px solid var(--border-subtle, rgba(128,128,128,.2));">
            <span class="mono muted" style="flex:0 0 96px; font-size:11.5px;">${escapeHtml(s.timestamp || "--:--")}</span>
            ${falantes[i] ? `<span class="badge-tag ${falantes[i] === "INCERTO" ? "badge-neutral" : "badge-success"}" title="Sugestão da IA local; confira com o áudio" style="flex:0 0 auto;">${escapeHtml(ROTULO_FALANTE[falantes[i]] || falantes[i])}</span>` : ""}
            <span style="flex:1;">${escapeHtml(s.texto_literal)}${s.texto_whisper ? ` <span class="muted" style="font-size:11px;" title="Texto original do Whisper: ${escapeHtml(s.texto_whisper)}">(nome/número ajustado: ${escapeHtml(Array.isArray(s.ajustes) ? s.ajustes.join("; ") : (s.ajustes || ""))})</span>` : ""}</span>
            <button type="button" class="btn btn-sm" data-citar-media="${escapeHtml(dep.media_id)}" data-citar-idx="${i}">Citar</button>
          </div>`
          )
          .join("") +
        `<div style="margin-top:10px;"><button type="button" class="btn btn-sm" data-citar-tudo="${escapeHtml(dep.media_id)}">Copiar transcrição integral</button></div>`;
    }
    openModal("modalEvidence");
    return;
  }

  showToast(`Peça/mídia ${idClean} não encontrada nos autos ativos.`, "warn");
}

function bindAnaliseEvents() {
  // Clique delegado: inspeção de peças/mídias, marcadores e atalhos de complementação.
  document.addEventListener("click", (e) => {
    const goto = e.target.closest("[data-marcador-goto]");
    if (goto) {
      e.preventDefault();
      hooks.irParaMarcador(goto.dataset.marcadorGoto);
      return;
    }
    const acao = e.target.closest("[data-action='abrir-metadados']");
    if (acao) {
      e.preventDefault();
      const campos = String(acao.dataset.campos || "").split(",").filter(Boolean);
      closeModal("modalTelemetry");
      abrirComplementacao(campos, "", () => hooks.simulateDosimetry(false));
      return;
    }
    const citarAleg = e.target.closest("[data-citar-alegacao]");
    if (citarAleg) {
      e.preventDefault();
      const mid = citarAleg.dataset.citarAlegacao;
      const dep = (state.activeCase?.matriz_oral?.depoentes || []).find((d) => d.media_id === mid);
      const x = (((state.analisesOrais || {})[mid] || {}).alegacoes || [])[Number(citarAleg.dataset.citarIdx)];
      if (!dep || !x) return;
      const texto = `“${String(x.trecho).trim()}” (${dep.depoente}, mídia ${dep.media_id}${dep.arquivo ? ` (${dep.arquivo})` : ""}, ${x.timestamp || "minutagem não registrada"})`;
      (navigator.clipboard ? navigator.clipboard.writeText(texto) : Promise.reject())
        .then(() => showToast("Citação copiada.", "ok"))
        .catch(() => showToast("Não foi possível copiar automaticamente.", "warn"));
      return;
    }
    const citar = e.target.closest("[data-citar-media],[data-citar-tudo]");
    if (citar) {
      e.preventDefault();
      const mid = citar.dataset.citarMedia || citar.dataset.citarTudo;
      const dep = (state.activeCase?.matriz_oral?.depoentes || []).find((d) => d.media_id === mid);
      const segs = (dep?.segmentos_literais || []).filter((s) => s && String(s.texto_literal || "").trim());
      if (!dep || !segs.length) return;
      const ref = (s) => `${dep.depoente}, mídia ${dep.media_id}${dep.arquivo ? ` (${dep.arquivo})` : ""}, ${s.timestamp || "minutagem não registrada"}`;
      const texto =
        citar.dataset.citarTudo !== undefined
          ? `${dep.depoente} — ${dep.papel} — mídia ${dep.media_id}\n\n` + segs.map((s) => `[${s.timestamp || "--:--"}] ${s.texto_literal}`).join("\n")
          : (() => {
              const s = segs[Number(citar.dataset.citarIdx)];
              return `“${String(s.texto_literal).trim()}” (${ref(s)})`;
            })();
      (navigator.clipboard ? navigator.clipboard.writeText(texto) : Promise.reject())
        .then(() => showToast(citar.dataset.citarTudo !== undefined ? "Transcrição copiada." : "Citação copiada.", "ok"))
        .catch(() => showToast("Não foi possível copiar automaticamente.", "warn"));
      return;
    }
    const target = e.target.closest("[data-inspect-id]");
    if (target) {
      e.preventDefault();
      openEvidenceInspector(target.dataset.inspectId);
    }
  });

  // Popover ao passar o mouse sobre .id-badge
  document.addEventListener("mouseover", (e) => {
    const badge = e.target.closest(".id-badge");
    const pop = $("liquidIdPopover");
    if (!badge || !pop) return;
    const idClean = String(badge.dataset.inspectId || "").trim();
    const peca = (state.activeCase?.pecas || []).find((p) => p.doc_id === idClean);
    const dep = (state.activeCase?.matriz_oral?.depoentes || []).find((d) => d.media_id === idClean);
    if (!peca && !dep) return;
    pop.innerHTML = peca
      ? `<strong>${escapeHtml(peca.doc_id)}</strong> &bull; ${escapeHtml(peca.titulo)}<div class="muted" style="margin-top:4px;">${escapeHtml((peca.resumo_estruturado || "").slice(0, 160))}</div>`
      : `<strong>${escapeHtml(dep.media_id)}</strong> &bull; ${escapeHtml(dep.depoente)}<div class="muted" style="margin-top:4px;">${escapeHtml((dep.discurso_indireto_sentenca || "").slice(0, 160))}</div>`;
    const rect = badge.getBoundingClientRect();
    pop.style.left = `${Math.min(window.innerWidth - 380, Math.max(12, rect.left))}px`;
    pop.style.top = `${Math.min(window.innerHeight - 140, rect.bottom + 8)}px`;
    pop.hidden = false;
  });

  document.addEventListener("mouseout", (e) => {
    if (e.target.closest(".id-badge")) {
      const pop = $("liquidIdPopover");
      if (pop) pop.hidden = true;
    }
  });

  $("btnReloadPage")?.addEventListener("click", () => window.location.reload());

  $("btnOpenMetadataModal")?.addEventListener("click", () => abrirComplementacao([], "", null));
  $("formMetadata")?.addEventListener("submit", enviarMetadados);
}

export {
  ROTULO_FALANTE,
  abrirComplementacao,
  analisarFalas,
  bindAnaliseEvents,
  carregarAnalisesOrais,
  enviarMetadados,
  openEvidenceInspector,
  preencherFormMetadados,
  renderAlegacoesOrais,
  renderAlertasExtracao,
  renderAnaliseProvas,
  renderOralFalhas,
  rotuloCoerencia,
};
