// SentencIA — Módulo ES: api.js (TASK-015)
import { FASES_PROGRESSO, TOKEN_HEADER, hooks, state } from "./estado.js?v=2.1.0";
import { $, showToast, sleep } from "./dom.js?v=2.1.0";

function lerTokenSessao() {
  const el = document.querySelector('meta[name="sentencia-token"]');
  return (el && el.getAttribute("content")) || "";
}

const SESSION_TOKEN = lerTokenSessao();

function mensagemDeErro(status, body) {
  const d = body ? body.detail : null;
  if (typeof d === "string" && d.trim()) return d;
  if (Array.isArray(d) && d.length) {
    return (
      "Requisição inválida: " +
      d
        .map((e) => {
          const loc = Array.isArray(e.loc) ? e.loc.filter((x) => x !== "body").join(".") : "";
          return `${loc}${loc ? ": " : ""}${e.msg || ""}`;
        })
        .join("; ")
    );
  }
  if (body && body.mensagem) return String(body.mensagem);
  if (status === 0) return "Não foi possível contactar o servidor local.";
  return `Erro HTTP ${status}`;
}

class ApiError extends Error {
  constructor(status, body) {
    super(mensagemDeErro(status, body || {}));
    this.name = "ApiError";
    this.status = status;
    this.body = body || {};
    this.erro = (body && body.erro) || "";
  }
}

function showSessionExpired(texto) {
  state.sessaoExpirada = true;
  const banner = $("sessionBanner");
  if (!banner) return;
  if (texto) $("sessionBannerText").textContent = texto;
  banner.hidden = false;
}

async function apiFetch(url, options = {}) {
  const opts = { ...options };
  const headers = new Headers(opts.headers || {});
  if (SESSION_TOKEN) headers.set(TOKEN_HEADER, SESSION_TOKEN);
  opts.headers = headers;
  opts.credentials = "same-origin";
  let resp;
  try {
    resp = await fetch(url, opts);
  } catch (_) {
    throw new ApiError(0, {
      detail: "Não foi possível contactar o servidor local. Verifique se o programa continua em execução.",
      erro: "servidor_indisponivel",
    });
  }
  if (resp.status === 401) showSessionExpired();
  return resp;
}

async function lerJson(resp) {
  try {
    return await resp.json();
  } catch (_) {
    return {};
  }
}

async function apiJson(url, options = {}) {
  const resp = await apiFetch(url, options);
  const data = await lerJson(resp);
  if (!resp.ok) throw new ApiError(resp.status, data);
  return data;
}

function postJson(url, body, method = "POST") {
  return apiJson(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

/**
 * Tratamento único dos erros da API.
 * ctx.retry: função a reexecutar depois que o juiz complementar os dados (422 dados_insuficientes).
 * ctx.silent: não exibir toast genérico.
 */
function handleApiError(err, ctx = {}) {
  const status = err && err.status;
  const body = (err && err.body) || {};
  const erro = body.erro || "";

  if (status === 401 || erro === "token_invalido") {
    showSessionExpired();
    if (!ctx.silent) showToast("Sessão expirada: recarregue a página do sistema.", "error");
    return;
  }
  if (erro === "decisao_judicial_pendente") {
    hooks.exigirVeredictos(body.crimes_pendentes || [], body.detail);
    return;
  }
  if (erro === "dados_insuficientes") {
    const campos = Array.isArray(body.campos) ? body.campos : [];
    const camposCrime = campos.filter((c) => String(c).startsWith("crimes_config."));
    const camposMeta = campos.filter((c) => !String(c).startsWith("crimes_config."));
    if (camposCrime.length && !camposMeta.length) {
      const codigos = camposCrime.map((c) => String(c).split(".")[1]).filter(Boolean);
      hooks.exigirVeredictos(codigos, body.detail);
      return;
    }
    hooks.abrirComplementacao(camposMeta, body.detail, ctx.retry || null);
    return;
  }
  if (erro === "envio_externo_nao_autorizado") {
    showToast(
      `${err.message} Para enviar dados ao Gemini/Google, ative "Permitir envio externo" nas Configurações e marque o consentimento na operação.`,
      "warn"
    );
    return;
  }
  if (erro === "gemini_indisponivel" || (status === 503 && body.mensagem)) {
    const msg = body.mensagem || err.message || "Falha ao comunicar com a API do Gemini.";
    showToast(msg, "error");
    return;
  }
  if (status === 413) {
    showToast(`${err.message}`, "error");
    return;
  }
  if (!ctx.silent) showToast(err && err.message ? err.message : String(err), status === 409 ? "warn" : "error");
}

function formatarDuracaoCurta(segundos) {
  const s = Math.max(0, Math.floor(Number(segundos) || 0));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m} min ${r} s`;
}

function mostrarProgresso(texto) {
  const box = $("uploadProgressContainer");
  if (box) box.hidden = false;
  $("uploadProgressPhase").textContent = texto || "Processando arquivos...";
  $("uploadProgressPercent").textContent = "2%";
  $("uploadProgressBar").style.width = "2%";
  $("uploadProgressDetail").textContent = "";
  const stall = $("uploadProgressStall");
  if (stall) stall.hidden = true;
}

function esconderProgresso(atrasoMs = 0) {
  const fechar = () => {
    if ($("uploadProgressContainer")) $("uploadProgressContainer").hidden = true;
    if ($("uploadProgressStall")) $("uploadProgressStall").hidden = true;
  };
  if (atrasoMs) setTimeout(fechar, atrasoMs);
  else fechar();
}

function atualizarBarraProgresso(p) {
  if (!p) return;
  const pct = Math.max(2, Math.min(100, Number(p.percent || 0)));
  const faseBruta = String(p.phase || "");
  const etapa = String(p.etapa || faseBruta || "").toLowerCase();
  const pagAtual = Number(p.pagina_atual || 0);
  const pagTotal = Number(p.paginas_total || 0);
  const agora = Date.now();
  const iniciadoMs = p.iniciado_em ? Date.parse(p.iniciado_em) : NaN;
  const decorridoS = Number.isFinite(iniciadoMs) ? Math.max(0, Math.floor((agora - iniciadoMs) / 1000)) : 0;

  if (etapa === "ocr" && pagTotal > 0) {
    const arq = p.current_file ? ` · ${p.current_file}` : "";
    $("uploadProgressPhase").textContent = `OCR: página ${pagAtual} de ${pagTotal} · ${formatarDuracaoCurta(decorridoS)}${arq}`;
  } else {
    let fase = FASES_PROGRESSO[faseBruta] || faseBruta || "Processando";
    if (faseBruta.startsWith("erro")) fase = faseBruta.replace(/^erro:\s*/i, "Erro: ");
    $("uploadProgressPhase").textContent = `${fase}${p.current_file ? " — " + p.current_file : ""}`;
  }

  $("uploadProgressPercent").textContent = `${pct}%`;
  $("uploadProgressBar").style.width = `${pct}%`;
  const total = Number(p.total_files || 0);
  $("uploadProgressDetail").textContent = total
    ? `${Number(p.processed_files || 0)} de ${total} arquivo(s) processado(s)${p.updated_at ? " · atualizado " + p.updated_at : ""}`
    : "";

  const stallBox = $("uploadProgressStall");
  const stallTxt = $("uploadProgressStallText");
  if (stallBox && stallTxt) {
    const ultimoMs = p.ultimo_avanco_em ? Date.parse(p.ultimo_avanco_em) : NaN;
    const semAvancoS = Number.isFinite(ultimoMs) ? Math.max(0, Math.floor((agora - ultimoMs) / 1000)) : 0;
    const ativo = p.active !== false && faseBruta !== "concluido";
    if (ativo && semAvancoS > 60) {
      stallTxt.textContent = `Sem avanço há ${semAvancoS} s. O OCR de páginas grandes pode demorar; se passar de 5 min, cancele e tente de novo.`;
      stallBox.hidden = false;
    } else {
      stallBox.hidden = true;
    }
  }
}

function startProgressPolling() {
  stopProgressPolling();
  mostrarProgresso("Iniciando...");
  state.uploadTimer = setInterval(async () => {
    try {
      const p = await apiJson("/api/cases/upload-progress");
      atualizarBarraProgresso(p);
    } catch (_) {
      // erro transitório no polling: ignora
    }
  }, 700);
}

function stopProgressPolling() {
  if (state.uploadTimer) {
    clearInterval(state.uploadTimer);
    state.uploadTimer = null;
  }
}

/** Acompanha um job assíncrono (202 + job_id) até concluir; devolve o resultado do job. */
async function acompanharJob(jobId) {
  let falhasSeguidas = 0;
  for (;;) {
    await sleep(800);
    let job;
    try {
      job = await apiJson(`/api/jobs/${encodeURIComponent(jobId)}`);
      falhasSeguidas = 0;
    } catch (err) {
      if (err.status === 401) throw err;
      if (err.status === 404) {
        throw new ApiError(404, {
          detail: "O acompanhamento do processamento expirou. Use o botão de recarregar o caso ativo para ver o resultado.",
        });
      }
      falhasSeguidas += 1;
      if (falhasSeguidas >= 15) throw err;
      continue;
    }
    atualizarBarraProgresso(job.progress);
    if (job.status === "concluido") return job.resultado || {};
    if (job.status === "erro") throw new ApiError(job.status_http || 500, job.erro || {});
  }
}

async function consentirOllamaNaOperacao(finalidade) {
  const status = await apiJson("/api/ollama/destino");
  if (!status.remoto) return {};
  if (!status.permitido) {
    showToast("O envio ao Ollama remoto está desabilitado nas configurações.", "warn");
    return null;
  }
  if (!window.confirm(`Você autoriza o envio dos trechos necessários dos autos a ${status.destino} para ${finalidade}?`)) {
    return null;
  }
  return { consentimento_envio_externo: true, destino_ollama_confirmado: status.destino };
}

export {
  ApiError,
  consentirOllamaNaOperacao,
  SESSION_TOKEN,
  acompanharJob,
  apiFetch,
  apiJson,
  atualizarBarraProgresso,
  esconderProgresso,
  handleApiError,
  lerJson,
  lerTokenSessao,
  mensagemDeErro,
  mostrarProgresso,
  postJson,
  showSessionExpired,
  startProgressPolling,
  stopProgressPolling,
};
