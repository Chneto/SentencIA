// SentencIA — Módulo ES: estacao-analise.js (TASK-015)
import { focarTrecho, realcarTrecho } from "./estacao-autos.js?v=2.2.1";
import { META_CAMPOS_FORM, META_CAMPOS_LABELS, hooks, state } from "./estado.js?v=2.2.1";
import { $, closeModal, codigosCatalogo, escapeHtml, fmtDateBr, fmtMoeda, isoDate, openModal, rotuloCrime, setActiveStation, showToast } from "./dom.js?v=2.2.1";
import { acompanharJob, apiJson, consentirOllamaNaOperacao, handleApiError, postJson } from "./api.js?v=2.2.1";
import { camposConfissao, salvarConfissao } from "./registro-confissao.js?v=2.2.1";
import { renderPedidos } from "./registro-pedidos.js?v=2.2.1";

const PONTOS = {
  data_nascimento_reu: { titulo: "Nascimento do réu", pena: true, campos: [["", "Data de nascimento", "date"]] },
  antecedentes: { titulo: "Antecedentes", pena: true, campos: [
    ["classificacao", "Resultado da leitura", "select", {
      certidao_negativa: "Declaração negativa",
      certidao_inconclusiva: "Certidão inconclusiva",
      registros_distintos: "Registros distintos na certidão",
      neutro_em_curso: "Ações ou inquéritos em curso",
      reincidencia: "Registro a conferir para a 2ª fase",
      maus_antecedentes: "Registro a conferir para a 1ª fase",
      condenacao_sem_data: "Condenação sem data",
      verificar_periodo_depurador: "Verificar período depurador",
      verificar_data_do_fato: "Verificar data do fato",
    }],
  ] },
  confissao: { titulo: "Confissão", pena: true, campos: [
    ["tipo", "Tipo", "select", { total: "Total", parcial: "Parcial", qualificada: "Qualificada", ausente: "Ausente" }],
    ["minuto", "Minutagem", "text"],
  ] },
  prisao_provisoria: { titulo: "Prisão provisória", pena: true, campos: [
    ["data_prisao", "Início da prisão", "date"], ["fonte_inicio", "Peça de início (ex.: ID-...)", "text"],
    ["data_soltura", "Soltura", "date"], ["fonte_fim", "Peça da soltura ou da custódia em curso", "text"],
    ["em_curso", "Custódia em curso na data da sentença?", "sim-nao"],
  ] },
  representacao: { titulo: "Representação", campos: [
    ["presente", "Há representação?", "sim-nao"], ["data", "Data da representação", "date"],
  ] },
  intimacao_mpu: { titulo: "Intimação da medida protetiva", campos: [
    ["intimidado_pessoalmente", "Houve intimação pessoal?", "sim-nao"], ["data", "Data da intimação", "date"],
  ] },
  pedido_dano_moral: { titulo: "Pedido de reparação por dano moral", campos: [
    ["presente", "Há pedido expresso?", "sim-nao"], ["valor", "Valor pedido (R$)", "number"],
  ] },
  vitima_condicao: { titulo: "Condições da vítima", pena: true, opcoes: {
    gestante: "Gestante", idosa: "Idosa", deficiencia: "Pessoa com deficiência", presenca_descendente: "Presença de descendente",
  } },
  revelia: { titulo: "Revelia", campos: [["presente", "Houve revelia?", "sim-nao"]] },
  reu_preso: { titulo: "Situação prisional do réu", campos: [
    ["", "Situação", "select", { preso: "Preso", solto: "Solto" }],
  ] },
};

const SIM_NAO = { sim: "Sim", nao: "Não" };
const pontosAtuais = () => state.activeCase?.metadados?.pontos_decisivos || [];

function trechoBotao(item, rotulo = "Ver trecho") {
  if (!item.fonte_id) return "";
  return '<button type="button" class="btn btn-sm" data-inspect-id="' + escapeHtml(item.fonte_id) +
    '" data-inspect-folha="' + escapeHtml(item.folha) + '" data-inspect-trecho="' +
    escapeHtml(item.trecho) + '">' + rotulo + '</button>';
}

function rotuloFonte(item) {
  return [item.fonte_id, item.folha].filter(Boolean).join(" · ");
}

function idadeAnos(nascimento, referencia) {
  const a = isoDate(nascimento);
  const b = isoDate(referencia);
  if (!a || !b || a > b) return null;
  return Number(b.slice(0, 4)) - Number(a.slice(0, 4)) - Number(b.slice(5) < a.slice(5));
}

function valorDoPonto(ponto, meta) {
  if (ponto.chave === "data_nascimento_reu" && meta.data_nascimento_reu) return meta.data_nascimento_reu;
  return ponto.valor;
}

function estadoDoPonto(ponto, meta) {
  if (ponto.chave === "confissao") {
    const sugestoes = Object.entries(meta.confissoes_sugeridas || {});
    if (sugestoes.some(([chave, item]) => item.tipo !== "ausente" && !meta.confissoes_confirmadas?.[chave])) {
      return "pendente";
    }
    if (Object.keys(meta.confissoes_confirmadas || {}).length) return "confirmado";
    return ponto.estado === "ausente" ? "ausente" : "extraido";
  }
  if (ponto.chave === "antecedentes" && ponto.estado === "extraido") return "extraido";
  if (ponto.confirmado || (ponto.chave === "data_nascimento_reu" && meta.data_nascimento_reu)) return "confirmado";
  return ponto.estado === "ausente" ? "ausente" : "pendente";
}

function campoValor(valor, chave, tipo, opcoes) {
  const bruto = chave ? (typeof valor === "boolean" && chave === "presente" ? valor : valor?.[chave]) : valor;
  if (bruto === undefined || bruto === null || bruto === "") return "";
  if (tipo === "sim-nao") return bruto ? "Sim" : "Não";
  if (tipo === "select") return opcoes[bruto] || bruto;
  if (tipo === "date") return fmtDateBr(bruto);
  if (tipo === "number") return fmtMoeda(bruto);
  return bruto;
}

function textoValorPonto(valor, def) {
  if (def.opcoes) return Array.isArray(valor) ? valor.map((v) => def.opcoes[v] || v).join(", ") || "Nenhuma condição" : "";
  return def.campos.map(([chave, rotulo, tipo, opcoes]) => {
    const exibido = campoValor(valor, chave, tipo, opcoes);
    return exibido && (chave ? rotulo.replace(/\?$/, "") + ": " + exibido : exibido);
  }).filter(Boolean).join(" · ");
}

function idadeEfeito(valor, meta) {
  const idade = idadeAnos(valor, meta.data_fato);
  if (idade === null) return "";
  return idade + " anos na data do fato" + (idade < 21 ? " → atenuante do art. 65, I, do CP" : "");
}


function avisoPontosPendentes(meta) {
  const n = (meta.pontos_decisivos || []).filter((p) =>
    PONTOS[p.chave]?.pena && estadoDoPonto(p, meta) === "pendente").length;
  if (!n) return "";
  return n + (n === 1 ? " ponto a confirmar pode" : " pontos a confirmar podem") +
    " alterar a pena. Confira Pontos decisivos na etapa 2.";
}

function renderPontosDecisivos(c) {
  const meta = c?.metadados || {};
  const pontos = meta.pontos_decisivos || [];
  const n = pontos.filter((p) => estadoDoPonto(p, meta) === "pendente").length;
  $("pdContador").textContent = n + (n === 1 ? " ponto a confirmar" : " pontos a confirmar");
  $("pdContador").hidden = !c || c.empty;
  $("pdAvisoGeracao").textContent = avisoPontosPendentes(meta);
  $("pdAvisoGeracao").hidden = !$("pdAvisoGeracao").textContent;
  $("pdQuadro").innerHTML = c && !c.empty ? renderPedidos(c, meta, trechoBotao, rotuloFonte) : '<p class="muted">Nenhum processo carregado.</p>';
  $("pdCards").innerHTML = pontos.filter((p) => PONTOS[p.chave]).map((p) => {
    const def = PONTOS[p.chave];
    const estado = estadoDoPonto(p, meta);
    const rotulo = { confirmado: "Confirmado", pendente: "A confirmar", ausente: "Ausente", extraido: "Informativo" }[estado];
    const valor = valorDoPonto(p, meta);
    const idade = p.chave === "data_nascimento_reu" ? idadeEfeito(valor, meta) : "";
    const fonte = rotuloFonte(p);
    const efeito = p.efeito;
    const titulo = escapeHtml(def.titulo);
    if (p.chave === "confissao") {
      return '<article class="pd-cartao inner-tile" data-ponto="confissao" data-estado="' + estado + '">' +
        '<h3 tabindex="-1">' + titulo + '</h3><p>' + escapeHtml(efeito || "") + '</p>' +
        camposConfissao(meta) + '</article>';
    }
    return '<article class="pd-cartao inner-tile" data-ponto="' + p.chave + '" data-estado="' + estado + '">' +
      '<div class="pd-cartao-topo"><h3 tabindex="-1">' + titulo + '</h3><span class="pd-estado">' + rotulo + '</span></div>' +
      '<p class="pd-valor">' + escapeHtml(p.chave === "confissao" && valor === "" ? "Ausente" : textoValorPonto(valor, def) || "Sem dado registrado") + '</p>' +
      (idade ? '<p>' + escapeHtml(idade) + '</p>' : '') + '<p>' + escapeHtml(efeito || "") + '</p>' +
      '<div class="pd-fonte">' + escapeHtml(fonte ? "Fonte: " + fonte : estado === "confirmado" ? "Fonte: informado pelo juízo" : "Fonte não localizada") +
      (fonte ? trechoBotao(p, "Ver fonte") : "") + '</div>' +
      '<div class="pd-acoes">' + (p.chave === "antecedentes"
        ? 'Confira cada registro na estação de dosimetria.'
        : (estado === "pendente" && valor != null
          ? '<button type="button" class="btn btn-sm btn-primary" data-pd-confirmar aria-label="Confirmar ' + titulo + '">Confirmar</button>'
          : '') + '<button type="button" class="btn btn-sm" data-pd-corrigir aria-label="Corrigir ' + titulo + '">Corrigir</button>') + '</div></article>';
  }).join("");
}

function campoCorrecao([chave, rotulo, tipo, opcoes], valor) {
  const nome = chave || "valor";
  const bruto = chave ? (typeof valor === "boolean" && chave === "presente" ? valor : valor?.[chave]) : valor;
  const label = '<label class="form-label pd-campo">' + escapeHtml(rotulo);
  if (tipo === "select" || tipo === "sim-nao") {
    const atual = tipo === "sim-nao" && typeof bruto === "boolean" ? (bruto ? "sim" : "nao") : bruto;
    const opcoesHtml = Object.entries(opcoes || SIM_NAO).map(([k, v]) =>
      '<option value="' + k + '"' + (k === atual ? " selected" : "") + '>' + escapeHtml(v) + '</option>').join("");
    return label + '<select class="form-select" name="' + nome + '" required><option value="">Selecione</option>' + opcoesHtml + '</select></label>';
  }
  const exibido = tipo === "date" ? isoDate(bruto) : bruto ?? "";
  const atributos = (!chave ? " required" : "") + (tipo === "number" ? ' min="0" step="0.01"' : "");
  return label + '<input class="form-control" name="' + nome + '" type="' + tipo + '" value="' +
    escapeHtml(exibido) + '"' + atributos + '></label>';
}

function abrirCorrecao(ponto, botao) {
  if (ponto.chave === "prisao_provisoria") {
    setActiveStation("dosimetria");
    const chk = $("usarIntervalosCustodia");
    if (chk && !chk.checked) { chk.checked = true; chk.dispatchEvent(new Event("change", { bubbles: true })); }
    const editor = $("editorCustodia");
    editor?.scrollIntoView?.({ behavior: "smooth", block: "center" });
    editor?.querySelector("input, select, button")?.focus({ preventScroll: true });
    showToast("Edite os intervalos de custódia com fonte na estação de Dosimetria.", "info");
    return;
  }
  const dialogo = $("pdCorrecao");
  const def = PONTOS[ponto.chave];
  const valor = valorDoPonto(ponto, state.activeCase.metadados);
  dialogo.dataset.corrigirPonto = ponto.chave;
  $("pdCorrecaoTitulo").textContent = "Corrigir " + def.titulo.toLowerCase();
  $("pdCorrecaoErro").textContent = "";
  $("pdCorrecaoCampos").innerHTML = def.opcoes
    ? Object.entries(def.opcoes).map(([k, v]) => '<label class="pd-opcao"><input type="checkbox" name="condicao" value="' +
      k + '"' + (valor?.includes(k) ? " checked" : "") + '> ' + escapeHtml(v) + '</label>').join("")
    : def.campos.map((campo) => campoCorrecao(campo, valor)).join("");
  dialogo.addEventListener("close", () => {
    if (botao.isConnected) botao.focus();
  }, { once: true });
  dialogo.showModal();
}

function lerCorrecao(form, chave) {
  const dados = new FormData(form);
  const def = PONTOS[chave];
  if (def.opcoes) return dados.getAll("condicao");
  const valores = Object.fromEntries(def.campos.map(([nome, , tipo]) => {
    const bruto = dados.get(nome || "valor");
    const valor = tipo === "sim-nao" ? bruto === "sim" : tipo === "number" ? (bruto ? Number(bruto) : null) : bruto;
    return [nome || "valor", valor];
  }));
  if (chave === "prisao_provisoria") {
    const { data_prisao, fonte_inicio, data_soltura, fonte_fim, em_curso } = valores;
    if (!data_prisao || !String(fonte_inicio || "").trim()) throw new Error("Informe o início da prisão e a peça de origem.");
    if (!em_curso && data_soltura && data_soltura < data_prisao) throw new Error("A soltura não pode anteceder o início da prisão.");
    if ((em_curso || data_soltura) && !String(fonte_fim || "").trim()) throw new Error("Informe a peça da soltura ou da custódia em curso.");
    const fim = em_curso ? null : (data_soltura || null);
    return {
      data_prisao, data_soltura: fim,
      intervalos: [{ inicio: data_prisao, fim, fonte_inicio: String(fonte_inicio).trim(), fonte_fim: String(fonte_fim || "").trim(), em_curso: Boolean(em_curso) }],
    };
  }
  return def.campos[0][0] ? valores : valores.valor;
}

let salvandoPonto = false;

async function salvarPonto(ponto, correcao, botao) {
  if (salvandoPonto) return;
  const c = state.activeCase;
  let valor = correcao ?? valorDoPonto(ponto, c.metadados);
  if (ponto.chave === "revelia" && typeof valor === "boolean") valor = { presente: valor };
  const metadados = {
    pontos_decisivos_confirmados: { ...(c.metadados_override?.pontos_decisivos_confirmados || {}), [ponto.chave]: valor },
  };
  if (ponto.chave === "data_nascimento_reu") metadados.data_nascimento_reu = valor;
  salvandoPonto = true;
  botao.disabled = true;
  try {
    const resposta = await postJson("/api/cases/metadata-override", { metadados });
    if (state.activeCase?.case_id !== c.case_id) return;
    await hooks.applyActiveCase(resposta.active_case, { clearSentence: true });
    if ($("pdCorrecao").open) $("pdCorrecao").close();
    $("pdCards").querySelector('[data-ponto="' + ponto.chave + '"] h3')?.focus({ preventScroll: true });
    showToast("Ponto confirmado. A minuta anterior foi descartada.", "success");
  } catch (err) {
    if ($("pdCorrecao").open) $("pdCorrecaoErro").textContent = err.message;
    else handleApiError(err);
  } finally {
    salvandoPonto = false;
    botao.disabled = false;
  }
}

function bindPontosDecisivos() {
  $("pdCards").addEventListener("submit", salvarConfissao);
  $("pdCards").addEventListener("click", (e) => {
    const botao = e.target.closest("[data-pd-confirmar],[data-pd-corrigir]");
    if (!botao || salvandoPonto) return;
    const ponto = pontosAtuais().find((p) => p.chave === botao.closest("[data-ponto]").dataset.ponto);
    if (!ponto) return;
    if (botao.hasAttribute("data-pd-corrigir")) abrirCorrecao(ponto, botao);
    else salvarPonto(ponto, null, botao);
  });
  $("pdCancelarCorrecao").addEventListener("click", () => $("pdCorrecao").close());
  $("pdCorrecaoForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const ponto = pontosAtuais().find((p) => p.chave === $("pdCorrecao").dataset.corrigirPonto);
    if (!ponto) return;
    try {
      salvarPonto(ponto, lerCorrecao(e.currentTarget, ponto.chave), $("pdSalvarCorrecao"));
    } catch (err) {
      $("pdCorrecaoErro").textContent = err.message;
    }
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest("[data-aviso-pontos]")) return;
    const aviso = avisoPontosPendentes(state.activeCase?.metadados || {});
    if (aviso) showToast(aviso, "warn");
  }, { capture: true });
}

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
  renderPontosDecisivos(c);
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
        const pend = Number(list.reduce((acc, x) => acc + Number(x.segmentos_pendentes_confirmacao || 0), 0));
        const badgePapel = first.papel === "INDETERMINADO"
          ? `<span class="badge-tag badge-warn">INDETERMINADO — confirmar papel</span>`
          : `<span class="badge-tag badge-neutral">${escapeHtml(first.papel)}</span>`;
        const badgePend = pend > 0
          ? ` <span class="badge-tag badge-warn">${pend} trecho(s) sem confirmação</span>`
          : "";
        return `
          <div class="forensic-tape">
            <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:6px;">
              <div><strong>${escapeHtml(first.depoente)}</strong> ${badgePapel}${badgePend}</div>
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
  try {
    const consentimento = await consentirOllamaNaOperacao("identificar falantes e alegações");
    if (consentimento === null) return;
    showToast("Analisando falas e alegações. Pode levar alguns minutos.", "info");
    const body = await postJson("/api/cases/oral/analisar", consentimento);
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

function openEvidenceInspector(rawId, { folha = "", trecho = "" } = {}) {
  const idClean = String(rawId || "").replace(/^FONTE:\s*/i, "").trim().split("/")[0].trim();
  const pecas = state.activeCase?.pecas || [];
  const deps = state.activeCase?.matriz_oral?.depoentes || [];

  const peca = pecas.find((p) => p.doc_id === idClean);
  const dep = deps.find((d) => d.media_id === idClean);

  if (peca) {
    $("evidenceDrawerTitle").textContent = `${peca.doc_id} — ${peca.titulo}`;
    $("evidenceDrawerMeta").textContent = [peca.categoria, fmtDateBr(peca.data_peca, "data não identificada"), folha || peca.folha].filter(Boolean).join(" | ");
    const ents = (peca.entidades_extraidas || []).filter((e) => e && e.valor);
    $("evidenceDrawerEntities").innerHTML = ents
      .slice(0, 12)
      .map((e) => `<span class="badge-tag badge-neutral">${escapeHtml(e.tipo ? `${e.tipo}: ${e.valor}` : e.valor)}</span>`)
      .join("");
    const textoPeca = peca.conteudo || peca.resumo_estruturado || "";
    const realce = realcarTrecho(textoPeca, trecho);
    let bodyHtml = realce || escapeHtml(textoPeca);
    const valores = Array.from(new Set(ents.map((e) => String(e.valor || "").trim()).filter((v) => v.length >= 4))).sort((a, b) => b.length - a.length);
    if (valores.length && !realce) {
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
    if (trecho) focarTrecho($("evidenceDrawerBody"), $("modalEvidence").querySelector("[data-close-modal]"));
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
            ${s.papel_segmento ? `<span class="badge-tag ${s.papel_estado === "CONFIRMADO" ? "badge-success" : "badge-warn"}" title="Papel do segmento (${escapeHtml(s.papel_estado || "INDETERMINADO")})" style="flex:0 0 auto;">${escapeHtml(s.papel_segmento)}${s.papel_estado && s.papel_estado !== "CONFIRMADO" ? ` (${escapeHtml(s.papel_estado.toLowerCase())})` : ""}</span>` : ""}
            ${falantes[i] ? `<span class="badge-tag ${falantes[i] === "INCERTO" ? "badge-neutral" : "badge-success"}" title="Sugestão da IA local; confira com o áudio" style="flex:0 0 auto;">${escapeHtml(ROTULO_FALANTE[falantes[i]] || falantes[i])}</span>` : ""}
            <span style="flex:1;">${realcarTrecho(s.texto_literal, trecho) || escapeHtml(s.texto_literal)}${s.texto_whisper ? ` <span class="muted" style="font-size:11px;" title="Texto original do Whisper: ${escapeHtml(s.texto_whisper)}">(nome/número ajustado: ${escapeHtml(Array.isArray(s.ajustes) ? s.ajustes.join("; ") : (s.ajustes || ""))})</span>` : ""}</span>
            <span style="display:flex; gap:4px; align-items:center; flex:0 0 auto;">
              <select class="input-sm" data-papel-select="${escapeHtml(dep.media_id)}:${i}" aria-label="Papel do segmento ${i + 1}" style="font-size:11px; padding:2px 4px;">
                <option value="OFENDIDA" ${s.papel_segmento === "OFENDIDA" ? "selected" : ""}>OFENDIDA</option>
                <option value="TESTEMUNHA_POLICIAL" ${s.papel_segmento === "TESTEMUNHA_POLICIAL" ? "selected" : ""}>TESTEMUNHA_POLICIAL</option>
                <option value="TESTEMUNHA_ACUSACAO" ${s.papel_segmento === "TESTEMUNHA_ACUSACAO" ? "selected" : ""}>TESTEMUNHA_ACUSACAO</option>
                <option value="TESTEMUNHA_DEFESA" ${s.papel_segmento === "TESTEMUNHA_DEFESA" ? "selected" : ""}>TESTEMUNHA_DEFESA</option>
                <option value="INTERROGATORIO_REU" ${s.papel_segmento === "INTERROGATORIO_REU" ? "selected" : ""}>INTERROGATORIO_REU</option>
                <option value="ALEGACOES_FINAIS_MP" ${s.papel_segmento === "ALEGACOES_FINAIS_MP" ? "selected" : ""}>ALEGACOES_FINAIS_MP</option>
                <option value="ALEGACOES_FINAIS_DEFESA" ${s.papel_segmento === "ALEGACOES_FINAIS_DEFESA" ? "selected" : ""}>ALEGACOES_FINAIS_DEFESA</option>
              </select>
              <button type="button" class="btn btn-sm" data-confirmar-papel-media="${escapeHtml(dep.media_id)}" data-confirmar-papel-idx="${i}">Confirmar papel</button>
              <button type="button" class="btn btn-sm" data-citar-media="${escapeHtml(dep.media_id)}" data-citar-idx="${i}">Citar</button>
            </span>
          </div>`
          )
          .join("") +
        `<div style="margin-top:10px;"><button type="button" class="btn btn-sm" data-citar-tudo="${escapeHtml(dep.media_id)}">Copiar transcrição integral</button></div>`;
    }
    openModal("modalEvidence");
    if (trecho) focarTrecho($("evidenceDrawerBody"), $("modalEvidence").querySelector("[data-close-modal]"));
    return;
  }

  showToast(`Peça/mídia ${idClean} não encontrada nos autos ativos.`, "warn");
}

function bindAnaliseEvents() {
  bindPontosDecisivos();
  // Clique delegado: inspeção de peças/mídias, marcadores e atalhos de complementação.
  document.addEventListener("click", async (e) => {
    const btnPapel = e.target.closest("[data-confirmar-papel-media]");
    if (btnPapel) {
      const mediaId = btnPapel.dataset.confirmarPapelMedia;
      const idx = Number(btnPapel.dataset.confirmarPapelIdx);
      const sel = document.querySelector(`[data-papel-select="${CSS.escape(`${mediaId}:${idx}`)}"]`);
      const papel = sel ? sel.value : "";
      if (!papel) return;
      try {
        const resp = await apiJson("/api/cases/transcricao/papel-segmento", {
          method: "POST",
          body: JSON.stringify({ media_id: mediaId, indice: idx, papel, confirmar: true, manifesto_id: state.activeCase?.manifesto_caso?.id || null }),
        });
        if (state.activeCase && resp.matriz_oral) {
          state.activeCase.matriz_oral = resp.matriz_oral;
          renderAnalise();
        }
        inspecionarEvidence(mediaId);
        showToast("Papel do segmento confirmado.", "success");
      } catch (err) {
        handleApiError(err);
      }
      return;
    }
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
      openEvidenceInspector(target.dataset.inspectId, { folha: target.dataset.inspectFolha, trecho: target.dataset.inspectTrecho });
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
