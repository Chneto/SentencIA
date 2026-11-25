// Persistência do rascunho de decisões por caso (servidor + fallback sessionStorage) — TASK-068.
import { apiFetch } from "./api.js?v=2.3.1";
import { state } from "./estado.js?v=2.3.1";

export const CHAVE_DECISOES_SESSAO = "sentencia.decisoes.v1";
const CHAVE_DECISOES_LEGADO = "jvdfm.decisoes.v1";

// TASK-092: último corpo enviado; rascunho idêntico não gera nova requisição.
let ultimoEnvio = "";

// TASK-092: gravação agrupada (~1,5 s depois da última edição); ao sair da página, grava o que estiver pendente.
const ATRASO_SALVAR_RASCUNHO_MS = 1500;
let timerRascunho = null;
let salvarPendente = null;

export function agendarRascunho(salvar) {
  clearTimeout(timerRascunho);
  salvarPendente = salvar;
  timerRascunho = setTimeout(gravarPendente, ATRASO_SALVAR_RASCUNHO_MS);
}

function gravarPendente() {
  clearTimeout(timerRascunho);
  timerRascunho = null;
  const salvar = salvarPendente;
  salvarPendente = null;
  if (salvar) salvar();
}

window.addEventListener("pagehide", gravarPendente);

export function salvarRascunhoDecisoes(payload) {
  if (!payload || !payload.caseKey || !Array.isArray(payload.crimeConfigs) || !payload.crimeConfigs.length) return;
  try {
    sessionStorage.setItem(CHAVE_DECISOES_SESSAO, JSON.stringify(payload));
  } catch (_) {
    /* sessionStorage indisponível */
  }
  const manifestoId = payload.manifestoId ?? state.activeCase?.manifesto_caso?.id ?? null;
  const corpo = JSON.stringify({
    case_key: payload.caseKey,
    crime_configs: payload.crimeConfigs,
    extras: payload.extras || {},
    manifesto_id: manifestoId,
  });
  if (corpo === ultimoEnvio) return;
  ultimoEnvio = corpo;
  apiFetch("/api/cases/rascunho-decisoes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: corpo,
  })
    .then((res) => {
      if (!res?.ok && ultimoEnvio === corpo) ultimoEnvio = "";
      return res && typeof res.text === "function" ? res.text() : null;
    })
    .catch(() => {
      if (ultimoEnvio === corpo) ultimoEnvio = "";
      /* falha de rede silenciosa: sessionStorage preserva o rascunho local */
    });
}

export function lerRascunhoDecisoes(caseKey, caseData = null) {
  if (!caseKey) return null;
  const salvoServidor = caseData?.rascunho_decisoes;
  if (
    salvoServidor &&
    salvoServidor.case_key === caseKey &&
    Array.isArray(salvoServidor.crime_configs)
  ) {
    return {
      caseKey: salvoServidor.case_key,
      crimeConfigs: salvoServidor.crime_configs,
      extras: salvoServidor.extras || {},
    };
  }
  try {
    let bruto = sessionStorage.getItem(CHAVE_DECISOES_SESSAO);
    if (!bruto) {
      const legado = sessionStorage.getItem(CHAVE_DECISOES_LEGADO);
      if (legado) {
        sessionStorage.setItem(CHAVE_DECISOES_SESSAO, legado);
        sessionStorage.removeItem(CHAVE_DECISOES_LEGADO);
        bruto = legado;
      }
    }
    if (!bruto) return null;
    const d = JSON.parse(bruto);
    return d && d.caseKey === caseKey && Array.isArray(d.crimeConfigs) ? d : null;
  } catch (_) {
    return null;
  }
}
