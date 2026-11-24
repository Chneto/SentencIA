// Persistência do rascunho de decisões por caso (servidor + fallback sessionStorage) — TASK-068.
import { apiFetch } from "./api.js?v=2.1.0";

export const CHAVE_DECISOES_SESSAO = "jvdfm.decisoes.v1";

export function salvarRascunhoDecisoes(payload) {
  if (!payload || !payload.caseKey || !Array.isArray(payload.crimeConfigs) || !payload.crimeConfigs.length) return;
  try {
    sessionStorage.setItem(CHAVE_DECISOES_SESSAO, JSON.stringify(payload));
  } catch (_) {
    /* sessionStorage indisponível */
  }
  apiFetch("/api/cases/rascunho-decisoes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      case_key: payload.caseKey,
      crime_configs: payload.crimeConfigs,
      extras: payload.extras || {},
    }),
  }).catch(() => {
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
    const bruto = sessionStorage.getItem(CHAVE_DECISOES_SESSAO);
    if (!bruto) return null;
    const d = JSON.parse(bruto);
    return d && d.caseKey === caseKey && Array.isArray(d.crimeConfigs) ? d : null;
  } catch (_) {
    return null;
  }
}
