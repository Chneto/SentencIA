import { $, escapeHtml } from "./dom.js?v=2.3.0";

export const ROTULOS_FONTE = {
  llm: "LLM local (Ollama)",
  deterministico: "Motor determinístico",
  fallback_deterministico: "Substituição pelo motor determinístico",
};

const ROTULOS_SECAO = {
  relatorio: "Relatório",
  preliminares: "Preliminares",
  merito: "Mérito",
  dosimetria: "Dosimetria",
  dispositivo: "Dispositivo",
};

function selo(tipo, classe, rotulo) {
  return `<span data-estado-secao="${tipo}" class="badge-tag ${classe}">${rotulo}</span>`;
}

function estadosSecao(secao) {
  const substituida = secao.fallback || secao.fonte === "fallback_deterministico";
  const validada = secao.aprovada && secao.selada && !substituida;
  const estados = [];
  if (secao.status === "gerando") {
    estados.push(selo("tentativa", "badge-neutral", "Tentativa em andamento"));
  }
  if (substituida) {
    estados.push(selo("substituida", "badge-warn", "Seção substituída"));
  }
  if (validada) {
    estados.push(selo("validada", "badge-success", "Seção validada automaticamente"));
  } else if (secao.status === "reprovado_retry") {
    estados.push(selo("reprovada", "badge-warn", "Tentativa com pendências"));
  } else if (!substituida && secao.status !== "gerando") {
    estados.push(selo("pendente", "badge-neutral", "Validação pendente"));
  }
  return estados.join(" ");
}

export function renderSectionalTelemetry(tel) {
  const grid = $("sectionalTelemetryGrid");
  if (!grid) return;
  const secoes = tel?.secoes;
  if (!secoes || typeof secoes !== "object") {
    grid.innerHTML = "";
    return;
  }
  const ordem = tel.ordem_secoes || Object.keys(secoes);
  grid.innerHTML = ordem
    .filter((chave) => secoes[chave])
    .map((chave) => {
      const secao = secoes[chave];
      const fonte = ROTULOS_FONTE[secao.fonte] || secao.fonte || "—";
      const tentativas = secao.tentativas;
      const total = typeof tentativas === "number" && tentativas >= 0 ? tentativas : "—";
      const score = secao.score;
      const pontuacao = typeof score === "number" && Number.isFinite(score) ? Math.round(score) + "%" : "—";
      const pendencias = Number(secao.marcadores_pendentes || 0);
      return `<div class="inner-tile" data-secao-telemetria="${escapeHtml(chave)}" style="margin-bottom:6px; font-size:12px;">
        <div style="display:flex; justify-content:space-between; gap:8px; flex-wrap:wrap;">
          <strong>${escapeHtml(ROTULOS_SECAO[chave] || chave)}</strong>
          <div>${estadosSecao(secao)}</div>
        </div>
        <div class="muted" style="margin-top:3px;">${escapeHtml(fonte)} · Tentativas ${total} · Pontuação da última tentativa ${pontuacao}${
          pendencias > 0 ? ` · ${pendencias} trecho(s) a completar` : ""
        }</div>
      </div>`;
    })
    .join("");
}
