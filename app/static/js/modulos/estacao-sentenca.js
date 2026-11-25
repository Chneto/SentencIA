// SentencIA — Módulo ES: estacao-sentenca.js (TASK-015)
import { hooks, state } from "./estado.js?v=2.3.1";
import { $, closeModal, escapeHtml, listarMarcadores, openModal, setActiveStation, showToast, updateHeroRibbon } from "./dom.js?v=2.3.1";
import { ApiError, apiFetch, apiJson, consentirOllamaNaOperacao, handleApiError, lerJson, postJson } from "./api.js?v=2.3.1";
import { buildDosimetryPayload, garantirDecisaoJudicial, renderCalcMemoryDrawer } from "./estacao-dosimetria.js?v=2.3.1";
import { aplicarMargensFolha, atualizarBarraFormatacao, capturarBaseDaFolha, decorarFolha, extractEditedSentenceText, htmlLimpoDaFolha, normalizarFormatacaoFolha, setBarraFormatacaoAtiva, sincronizarFolha, textoPuroParaHtml } from "./folha-editor.js?v=2.3.1";
import { atualizarContagemRevisao, atualizarRevisao } from "./folha-ferramentas.js?v=2.3.1";
import { marcarMinutaGerada, renderDecisoesAcessorias } from "./registro-decisoes-acessorias.js?v=2.3.1";
import { ROTULOS_FONTE, renderSectionalTelemetry } from "./telemetria-sentenca.js?v=2.3.1";

function renderPendenciasPanel() {
  const panel = $("sentencePendenciasPanel");
  if (!panel) return;
  const sent = state.lastSentence;
  if (!sent || !sent.texto_completo) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  const payload = state.lastPayload || {};
  const aud = state.lastAudit || {};
  const marcadores = listarMarcadores(sent.texto_completo);
  const fallback = payload.secoes_fallback || sent.secoes_fallback || aud.secoes_fallback || [];
  const alertas = payload.alertas || sent.alertas || [];
  const presume = !!(
    payload.simulacao_presume_condenacao ||
    sent.simulacao_presume_condenacao ||
    (sent.dosimetria_dados && sent.dosimetria_dados.simulacao_presume_condenacao)
  );
  const liberada = payload.exportacao_liberada ?? !!aud.aprovada_sem_alucinacao;
  const blocos = [
    '<div class="alert-banner alert-info" data-revisao-judicial><strong>Revisão judicial necessária</strong></div>',
  ];

  if (presume) {
    blocos.push(`
      <div class="alert-banner alert-danger"><div><strong>Minuta de SIMULAÇÃO:</strong> a condenação foi presumida para crime(s) sem veredicto do juízo. Não utilize como decisão; informe os veredictos na Etapa 3 e gere novamente.</div></div>`);
  }
  if (marcadores.length) {
    blocos.push(`
      <div class="alert-banner alert-warn">
        <div>
          <strong>${marcadores.length} trecho(s) a completar pelo juízo</strong> — destacados na folha. Edite o texto e use <strong>Re-Auditar</strong>.
          <div style="margin-top:6px;">${marcadores
            .map((m, i) => `<button type="button" class="marcador-chip" data-marcador-goto="${i}">${escapeHtml(m.length > 110 ? m.slice(0, 107) + "...]" : m)}</button>`)
            .join("")}</div>
        </div>
      </div>`);
  }
  if (fallback.length) {
    blocos.push(`
      <div class="alert-banner alert-warn"><div><strong>Seções substituídas pelo modelo determinístico (fallback):</strong> ${escapeHtml(
        fallback.join(", ")
      )}. O laudo de auditoria sinaliza essas seções.</div></div>`);
  }
  if (alertas.length) {
    blocos.push(`
      <div class="alert-banner alert-info"><div><strong>Alertas da geração:</strong><ul class="alert-list">${alertas
        .map((a) => `<li>${escapeHtml(a)}</li>`)
        .join("")}</ul></div></div>`);
  }
  if (!liberada) {
    blocos.push(`
      <div class="alert-banner alert-warn"><div><strong>Exportação bloqueada:</strong> a auditoria não aprovou a minuta (score ${escapeHtml(
        aud.score_confiabilidade ?? "—"
      )}). Resolva as pendências ou exporte como rascunho com a marca "MINUTA NÃO AUDITADA".</div></div>`);
  }
  panel.innerHTML = blocos.join("");
  panel.hidden = !blocos.length;
}

function irParaMarcador(i) {
  const mark = document.querySelector(`#sentenceRenderBody mark.marcador-pendente[data-marcador-idx="${i}"]`);
  if (!mark) return;
  mark.scrollIntoView({ behavior: "smooth", block: "center" });
  mark.classList.add("flash");
  setTimeout(() => mark.classList.remove("flash"), 1600);
}

function renderSentenceOnSheet(data) {
  renderDecisoesAcessorias();
  state.lastSentence = data.sentenca || null;
  if (state.lastSentence) marcarMinutaGerada();
  state.lastAudit = data.auditoria || null;
  state.lastHtmlPje = data.html_pje || "";
  state.lastPayload = data.sentenca
    ? {
        alertas: data.alertas,
        marcadores_pendentes: data.marcadores_pendentes,
        secoes_fallback: data.secoes_fallback,
        simulacao_presume_condenacao: data.simulacao_presume_condenacao,
        exportacao_liberada: data.exportacao_liberada,
        origem: data.origem,
      }
    : null;
  if (data.generation_id) state.generationId = data.generation_id;

  const body = $("sentenceRenderBody");
  const sent = state.lastSentence;
  if (!sent || !sent.texto_completo) {
    setBarraFormatacaoAtiva(false);
    state.editorSujo = false;
    body.setAttribute("contenteditable", "false");
    body.innerHTML = `<div class="empty-studio-placeholder">Nenhuma sentença gerada ainda para os autos ativos.<br />Informe os veredictos na Etapa 3 e use <strong>Gerar (motor local)</strong>, <strong>Copiar Script Py (.md)</strong> ou <strong>API Gemini</strong>.</div>`;
    $("btnReauditEdited").disabled = true;
    $("sentenceGenStatus").textContent = "Aguardando geração";
    $("sentenceOriginLabel").textContent = "SentencIA";
    state.lastTelemetry = null;
    renderSectionalTelemetry(null);
    renderPendenciasPanel();
    renderAuditDrawer();
    state.revisao = null;
    atualizarContagemRevisao();
    return;
  }

  // A folha vem do servidor como HTML forense LIMPO (o mesmo que vira .docx/.pdf/PJe): justificado,
  // recuo de 1,25 cm, títulos curtos em destaque. As marcações de apoio (pendências e IDs de peças)
  // são aplicadas por cima, só na tela, e removidas ao salvar/exportar.
  body.innerHTML = sent.texto_html || textoPuroParaHtml(sent.texto_completo);
  decorarFolha(body);
  state.formatacao = normalizarFormatacaoFolha(sent.formatacao);
  aplicarMargensFolha();
  body.setAttribute("contenteditable", "true");
  setBarraFormatacaoAtiva(true);
  capturarBaseDaFolha();
  atualizarBarraFormatacao();
  $("btnReauditEdited").disabled = false;
  $("sentenceGenStatus").textContent = `Geração #${state.generationId || 1}`;
  $("sentenceOriginLabel").textContent = sent.motor_utilizado || "SentencIA";

  if (sent.dosimetria_dados && Object.keys(sent.dosimetria_dados).length) {
    state.dosimetry = sent.dosimetria_dados;
    state.prescricao = sent.prescricao_dados || state.prescricao;
    state.simulacaoPresume = !!sent.simulacao_presume_condenacao;
    updateHeroRibbon();
    renderCalcMemoryDrawer();
  }
  renderPendenciasPanel();
  renderAuditDrawer();
  atualizarRevisao(); // contagem do bot\u00e3o "Revis\u00e3o" (n\u00e3o bloqueia a renderiza\u00e7\u00e3o)
}

function badgeStatusCamada(status) {
  const s = String(status || "").toUpperCase();
  if (s === "APROVADO") return "badge-success";
  if (s === "REPROVADO") return "badge-danger";
  if (s === "SINALIZADO") return "badge-warn";
  if (s === "NAO_APLICAVEL") return "badge-neutral";
  return "badge-warn";
}

const ROTULOS_VERIFICACAO = {
  ids_autos: "IDs de peças e mídias citados existem nos autos",
  citacoes_base_fechada: "Citações jurídicas dentro da base fechada",
  teses_defensivas: "Apreciação das teses defensivas",
  marcadores_completar_pelo_juizo: "Trechos a completar pelo juízo",
  marcadores_pendentes: "Trechos a completar pelo juízo",
  penas_dispositivo_x_dosimetria: "Penas do dispositivo conferidas com a dosimetria",
  secoes_fallback: "Seções substituídas por modelo determinístico",
};

function renderAuditDrawer() {
  const aud = state.lastAudit;
  const badge = $("btnOpenAuditDrawer");
  const badgeText = $("auditScoreBadge");
  const mini = $("badgeAuditMini");
  if (!aud) {
    badgeText.textContent = "Verificação pendente";
    badge.className = "badge-tag badge-neutral";
    mini.textContent = "—";
    $("auditDrawerScore").textContent = "Nenhuma auditoria executada";
    $("auditScoreGrid").innerHTML = "";
    $("auditLayersSummary").innerHTML = `<p class="muted">Gere ou importe uma sentença para inspecionar o laudo de auditoria.</p>`;
    $("auditConteudoContainer").innerHTML = "";
    $("auditTraceabilityTableBody").innerHTML = "";
    return;
  }

  const aprovada = !!aud.aprovada_sem_alucinacao;
  const scoreNum = aud.score_confiabilidade;
  const score = Number.isFinite(scoreNum) ? Math.round(scoreNum) : null;
  const scoreTxt = score === null ? "—" : `${score}%`;
  badgeText.textContent = aprovada ? "Verificação concluída" : "Verificação com pendências";
  badge.className = `badge-tag ${aprovada ? "badge-success" : "badge-warn"}`;
  mini.textContent = scoreTxt;
  const detalhes = aud.detalhes_camadas || [];
  $("auditDrawerTitle").textContent = `Auditoria automatizada (${detalhes.length || "—"} camadas)`;
  $("auditDrawerScore").textContent = aprovada ? "Checagens automáticas concluídas" : "Checagens automáticas com pendências";

  const marcadores = aud.marcadores_pendentes || [];
  const penasDiv = aud.penas_divergentes || [];
  const scoreForma = aud.score_forma;
  $("auditScoreGrid").innerHTML = `
    <div class="score-grid">
      <div class="inner-tile"><div class="kpi-label">Pontuação da auditoria</div><div class="score-value">${escapeHtml(scoreTxt)}</div></div>
      <div class="inner-tile"><div class="kpi-label">Pontuação de forma</div><div class="score-value">${escapeHtml(Number.isFinite(scoreForma) ? Math.round(scoreForma) + "%" : "—")}</div></div>
      <div class="inner-tile"><div class="kpi-label">Pendências de conteúdo</div><div class="score-value">${escapeHtml(String(marcadores.length + penasDiv.length))}</div></div>
    </div>`;

  $("auditLayersSummary").innerHTML = detalhes.length
    ? detalhes
        .map(
          (c) => `
    <div class="inner-tile" style="margin-bottom:8px;">
      <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
        <strong>${escapeHtml(c.camada || "")}</strong>
        <span class="badge-tag ${badgeStatusCamada(c.status)}">${escapeHtml(c.status || "—")}</span>
      </div>
      <div class="text-sec" style="font-size:12px; margin-top:4px;">${escapeHtml(c.descricao || "")}</div>
    </div>`
        )
        .join("")
    : `<p class="muted">Sem detalhes das camadas.</p>`;

  const verifs = aud.verificacoes_conteudo || [];
  $("auditConteudoContainer").innerHTML = verifs.length
    ? verifs
        .map((v) => {
          const itens = Array.isArray(v.itens) ? v.itens : [];
          return `
      <div class="verif-item">
        <div class="verif-item-head">
          <strong>${escapeHtml(ROTULOS_VERIFICACAO[v.verificacao] || v.verificacao || "")}</strong>
          <span class="badge-tag ${badgeStatusCamada(v.status)}">${escapeHtml(v.status || "—")}</span>
        </div>
        <div class="text-sec" style="margin-top:4px;">${escapeHtml(v.descricao || "")}</div>
        ${itens.length ? `<ul>${itens.map((i) => `<li>${escapeHtml(typeof i === "string" ? i : JSON.stringify(i))}</li>`).join("")}</ul>` : ""}
      </div>`;
        })
        .join("")
    : `<p class="muted">Este laudo não traz verificações de conteúdo.</p>`;

  const validIds = (aud.ids_citados_verificados || []).map((id) => ({ id, ok: true, obs: "ID localizado nos autos ou mídias", inspecionavel: true }));
  const invalidIds = (aud.ids_inexistentes || []).map((id) => ({ id, ok: false, obs: "ID inexistente nos autos", inspecionavel: true }));
  const citInvalidas = (aud.citacoes_nao_autorizadas || []).map((id) => ({ id, ok: false, obs: "Citação fora da base fechada", inspecionavel: false }));
  const allIds = [...invalidIds, ...citInvalidas, ...validIds];
  $("auditTraceabilityTableBody").innerHTML = allIds.length
    ? allIds
        .map(
          (r) => `
      <tr>
        <td>${
          r.inspecionavel
            ? `<button type="button" class="id-badge" data-inspect-id="${escapeHtml(r.id)}">${escapeHtml(r.id)}</button>`
            : `<span class="mono" style="font-size:12px;">${escapeHtml(r.id)}</span>`
        }</td>
        <td><span class="badge-tag ${r.ok ? "badge-success" : "badge-danger"}">${r.ok ? "VÁLIDO" : "INVÁLIDO"}</span></td>
        <td style="font-size:12px;">${escapeHtml(r.obs)}</td>
      </tr>`
        )
        .join("")
    : `<tr><td colspan="3" class="muted">Nenhum ID citado na minuta.</td></tr>`;
}

function nomeDoContentDisposition(cd) {
  const s = String(cd || "");
  const star = s.match(/filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/);
  if (star) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^"|"$/g, ""));
    } catch (_) {
      // ignora e tenta o formato simples
    }
  }
  const simples = s.match(/filename\s*=\s*"?([^";]+)"?/);
  return simples ? simples[1].trim() : "";
}

function baixarBlob(blob, nome) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = nome;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

async function exportarArquivo(formato, rascunho = false) {
  if (!state.lastSentence) {
    showToast("Gere a minuta antes de exportar.", "warn");
    return;
  }
  if (!(await sincronizarFolha())) return;
  try {
    const query = new URLSearchParams({ revisao_esperada: state.lastSentence.revisao_documento });
    if (rascunho) query.set("rascunho", "true");
    const resp = await apiFetch(`/api/export/${formato}?${query}`);
    if (!resp.ok) {
      const body = await lerJson(resp);
      if (resp.status === 409 && body.erro === "auditoria_nao_aprovada") {
        abrirModalRascunho(formato, body);
        return;
      }
      throw new ApiError(resp.status, body);
    }
    const blob = await resp.blob();
    const nome = nomeDoContentDisposition(resp.headers.get("Content-Disposition")) || `Sentença${rascunho ? " (RASCUNHO)" : ""}.${formato}`;
    baixarBlob(blob, nome);
    if (resp.headers.get("X-SentencIA-Marca-Dagua") === "1") {
      showToast(`Exportado como RASCUNHO (${nome}) com a marca "MINUTA NÃO AUDITADA".`, "warn");
    } else {
      showToast(`Arquivo ${nome} exportado.`, "success");
    }
    const copiaEm = resp.headers.get("X-SentencIA-Copia-Em");
    const copiaErro = resp.headers.get("X-SentencIA-Copia-Erro");
    if (copiaEm) showToast(`Cópia salva em: ${decodeURIComponent(copiaEm)}`, "success", 8000);
    else if (copiaErro) showToast(`Não consegui copiar para a sua pasta de exportação (${decodeURIComponent(copiaErro)}). O download foi feito normalmente.`, "warn", 9000);
  } catch (err) {
    handleApiError(err);
  }
}

function abrirModalRascunho(formato, body) {
  state.exportPendente = formato;
  const marcadores = body.marcadores_pendentes || [];
  $("exportRascunhoBody").innerHTML = `
    <p style="margin:0 0 8px;">${escapeHtml(body.detail || "A minuta não foi aprovada pela auditoria.")}</p>
    ${
      marcadores.length
        ? `<p style="margin:0 0 6px;"><strong>${marcadores.length} trecho(s) a completar pelo juízo:</strong></p><ul class="alert-list">${marcadores
            .slice(0, 8)
            .map((m) => `<li>${escapeHtml(m)}</li>`)
            .join("")}</ul>`
        : ""
    }
    <p class="muted" style="margin:10px 0 0; font-size:12px;">O rascunho sai com a marca d'água "MINUTA NÃO AUDITADA" e não deve ser juntado aos autos como decisão final.</p>`;
  openModal("modalExportRascunho");
}

function htmlPjeParaTextoSimples(htmlPje) {
  const doc = new DOMParser().parseFromString(String(htmlPje || ""), "text/html");
  const blocos = [];

  function extrairTextoElemento(el) {
    let out = "";
    for (const node of el.childNodes) {
      if (node.nodeType === Node.TEXT_NODE) {
        out += node.nodeValue || "";
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        if (node.tagName === "BR") {
          out += "\n";
        } else {
          out += extrairTextoElemento(node);
        }
      }
    }
    return out
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n[ \t]+/g, "\n")
      .trim();
  }

  function coletarBlocos(raiz) {
    for (const child of raiz.children) {
      const tag = child.tagName;
      if (tag === "UL" || tag === "OL") {
        coletarBlocos(child);
        continue;
      }
      const txt = extrairTextoElemento(child);
      if (txt) blocos.push(txt);
    }
  }

  coletarBlocos(doc.body);
  if (!blocos.length) {
    const bruto = (doc.body.textContent || "").trim();
    if (bruto) blocos.push(bruto);
  }
  return blocos.join("\n\n");
}

async function copiarParaPje() {
  if (!state.lastSentence) {
    showToast("Gere uma sentença antes de copiar para o PJe.", "warn");
    return;
  }
  if (!(await sincronizarFolha())) return;
  let copia;
  try {
    const query = new URLSearchParams({ revisao_esperada: state.lastSentence.revisao_documento });
    copia = await apiJson(`/api/export/pje?${query}`);
  } catch (err) {
    handleApiError(err);
    return;
  }
  const marcadores = copia.marcadores_pendentes || [];
  if (marcadores.length) {
    const n = marcadores.length;
    const msg = `Há ${n} ${n === 1 ? "trecho" : "trechos"} a completar. Copiar assim mesmo?`;
    if (!window.confirm(msg)) return;
  }
  const aprovada = copia.exportacao_liberada;
  const textoSimples = htmlPjeParaTextoSimples(copia.html_pje);
  try {
    if (window.ClipboardItem && navigator.clipboard?.write) {
      const item = new window.ClipboardItem({
        "text/html": new Blob([copia.html_pje], { type: "text/html" }),
        "text/plain": new Blob([textoSimples], { type: "text/plain" }),
      });
      await navigator.clipboard.write([item]);
      showToast(
        aprovada ? "Texto formatado para o PJe copiado." : 'Copiado para o PJe COM a marca "MINUTA NÃO AUDITADA" (a auditoria não aprovou a minuta).',
        aprovada ? "success" : "warn"
      );
    } else {
      await navigator.clipboard.writeText(textoSimples);
      showToast("Seu navegador copiou só o texto; a formatação não foi incluída.", "warn");
    }
  } catch (err) {
    showToast(`Não foi possível copiar: ${err.message || err}`, "error");
  }
}

async function carregarUltimaSentenca() {
  try {
    const res = await apiJson("/api/sentence/last");
    renderSentenceOnSheet(res);
    if (res.telemetria && res.telemetria.secoes) {
      state.lastTelemetry = res.telemetria;
      renderSectionalTelemetry(res.telemetria);
    }
  } catch (err) {
    if (err.status !== 404) handleApiError(err, { silent: true });
  }
}

async function gerarSentencaLocal() {
  if (!hooks.garantirDecisaoJudicial(gerarSentencaLocal)) return;
  const botao = $("btnGenerateSentence");
  botao.disabled = true;
  try {
    const res = await postJson("/api/sentence/generate", { use_ollama: true, ...buildDosimetryPayload() });
    renderSentenceOnSheet(res);
    setActiveStation("sentenca");
    avisarResultadoGeracao(res, "Sentença gerada pelo motor local e auditada.");
  } catch (err) {
    handleApiError(err, { retry: gerarSentencaLocal });
  } finally {
    botao.disabled = false;
  }
}

function avisarResultadoGeracao(res, mensagemOk) {
  const marcadores = (res.marcadores_pendentes || []).length || listarMarcadores(res.sentenca?.texto_completo).length;
  if (res.simulacao_presume_condenacao) {
    showToast("Minuta de SIMULAÇÃO (condenação presumida para crime sem veredicto). Não utilize como decisão.", "warn");
  } else if (!res.exportacao_liberada) {
    showToast(
      `${mensagemOk} A auditoria não aprovou a minuta${marcadores ? `: ${marcadores} trecho(s) a completar pelo juízo` : ""}.`,
      "warn"
    );
  } else {
    showToast(mensagemOk, "success");
  }
}

function indicarTentativaDeRedacao() {
  const campo = $("sentenceGenStatus");
  const mensagem = "Tentativa de redação em andamento";
  const anterior = campo.textContent;
  campo.setAttribute("role", "status");
  campo.textContent = mensagem;
  return () => {
    if (campo.textContent === mensagem) campo.textContent = anterior;
  };
}

async function gerarSeccional() {
  if (!hooks.garantirDecisaoJudicial(gerarSeccional)) return;
  const botao = $("btnGenerateSectional");
  botao.disabled = true;
  let encerrarTentativa;
  try {
    const consentimento = await consentirOllamaNaOperacao("redigir e revisar as seções da minuta");
    encerrarTentativa = indicarTentativaDeRedacao();
    const res = await postJson("/api/sentence/generate-sectional", {
      use_ollama: consentimento !== null,
      ...consentimento,
      custom_dosimetria_override: hooks.buildDosimetryPayload(),
      usar_duplo_crivo: true,
    });
    renderSentenceOnSheet(res);
    state.lastTelemetry = res.telemetria || null;
    renderSectionalTelemetry(res.telemetria);
    avisarResultadoGeracao(res, "Sentença gerada pelo Pipeline Seccional e auditada.");
  } catch (err) {
    handleApiError(err, { retry: gerarSeccional });
  } finally {
    encerrarTentativa?.();
    botao.disabled = false;
  }
}

async function regenerarSecao() {
  const botao = $("btnRegenerateSection");
  botao.disabled = true;
  let encerrarTentativa;
  try {
    const consentimento = await consentirOllamaNaOperacao("regenerar e revisar esta seção");
    encerrarTentativa = indicarTentativaDeRedacao();
    const res = await postJson("/api/sentence/regenerate-section", {
      secao: $("selRegenSection").value,
      revisao_esperada: state.lastSentence?.revisao_documento,
      instrucoes_cirurgicas: $("inputSurgicalNote").value || null,
      use_ollama: consentimento !== null,
      ...consentimento,
    });
    // A resposta traz só a seção; recupera a minuta completa atualizada.
    const ultima = await apiJson("/api/sentence/last");
    renderSentenceOnSheet(ultima);
    state.lastTelemetry = res.telemetria || ultima.telemetria || null;
    renderSectionalTelemetry(state.lastTelemetry);
    showToast(
      res.exportacao_liberada ? "Seção regenerada e reauditada." : "Seção regenerada; a auditoria ainda aponta pendências.",
      res.exportacao_liberada ? "success" : "warn"
    );
  } catch (err) {
    handleApiError(err);
  } finally {
    encerrarTentativa?.();
    botao.disabled = false;
  }
}

async function reauditarEdicao() {
  const txt = extractEditedSentenceText();
  if (!txt) {
    showToast("O texto na folha está vazio.", "warn");
    return false;
  }
  if (!hooks.garantirDecisaoJudicial(reauditarEdicao)) return false;
  const botao = $("btnReauditEdited");
  botao.disabled = true;
  try {
    const res = await postJson("/api/sentence/update-and-audit", {
      texto_completo: txt,
      texto_html: htmlLimpoDaFolha(),
      revisao_esperada: state.lastSentence?.revisao_documento,
      formatacao: state.formatacao,
      custom_dosimetria_override: hooks.buildDosimetryPayload(),
    });
    renderSentenceOnSheet(res);
    avisarResultadoGeracao(res, "Edição salva e reauditada.");
    return true;
  } catch (err) {
    handleApiError(err);
    return false;
  } finally {
    botao.disabled = false;
  }
}

function bindSentencaEvents() {
  renderDecisoesAcessorias();
  document.addEventListener("click", (evento) => {
    if (evento.target.closest('.station-btn[data-station="sentenca"]')) renderDecisoesAcessorias();
  });
  $("btnGenerateSentence")?.addEventListener("click", gerarSentencaLocal);
  $("btnReauditEdited")?.addEventListener("click", reauditarEdicao);
  $("btnOpenAuditDrawer")?.addEventListener("click", () => openModal("modalAuditDrawer"));
  $("btnGenerateSectional")?.addEventListener("click", gerarSeccional);
  $("btnRegenerateSection")?.addEventListener("click", regenerarSecao);

  $("btnDownloadDocx")?.addEventListener("click", () => exportarArquivo("docx"));
  $("btnDownloadPdf")?.addEventListener("click", () => exportarArquivo("pdf"));
  $("btnDownloadHtml")?.addEventListener("click", () => exportarArquivo("html"));
  $("btnCopyPje")?.addEventListener("click", copiarParaPje);
  $("btnConfirmExportRascunho")?.addEventListener("click", async () => {
    const formato = state.exportPendente;
    closeModal("modalExportRascunho");
    if (formato) await exportarArquivo(formato, true);
  });
}

export {
  ROTULOS_FONTE,
  ROTULOS_VERIFICACAO,
  abrirModalRascunho,
  avisarResultadoGeracao,
  badgeStatusCamada,
  baixarBlob,
  bindSentencaEvents,
  carregarUltimaSentenca,
  copiarParaPje,
  exportarArquivo,
  gerarSeccional,
  gerarSentencaLocal,
  htmlPjeParaTextoSimples,
  irParaMarcador,
  nomeDoContentDisposition,
  reauditarEdicao,
  regenerarSecao,
  renderAuditDrawer,
  renderPendenciasPanel,
  renderSectionalTelemetry,
  renderSentenceOnSheet,
};
