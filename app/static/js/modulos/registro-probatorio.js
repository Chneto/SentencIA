// Registro probatório e decisão cautelar do juízo.
import { state } from "./estado.js?v=2.1.0";
import { escapeHtml } from "./dom.js?v=2.1.0";

function camposProva(rotulo, chave, ref = {}) {
  return `
    <label class="form-label">${rotulo} — ID da peça ou mídia
      <input class="form-control" data-fonte="${chave}" value="${escapeHtml(ref.fonte_id || "")}">
    </label>
    <label class="form-label">${rotulo} — trecho literal
      <textarea class="form-textarea" rows="2" data-trecho="${chave}">${escapeHtml(ref.trecho || "")}</textarea>
    </label>`;
}

function campoConclusao(opcoes, valor) {
  return `<select class="form-select" data-conclusao>
    <option value="">Pendente</option>
    ${opcoes.map(([v, label]) => `<option value="${v}" ${valor === v ? "selected" : ""}>${label}</option>`).join("")}
  </select>`;
}

export function elementosDoCrime(codigo) {
  const grupos = state.activeCase?.elementos_probatorios;
  return grupos ? { ...grupos.comuns, ...grupos.especificos[codigo] } : {};
}

export function camposConclusoes(cfg, codigo) {
  if (state.activeCase?.is_demo || !cfg.veredicto || cfg.veredicto === "extinguir") return "";
  const elementos = Object.entries(elementosDoCrime(codigo));
  if (!elementos.length) return "";
  return `
    <details class="inner-tile">
      <summary>Conclusões sobre a prova desta imputação</summary>
      <p class="muted">Conclusões em branco ficam pendentes na minuta. Use o ID e copie o trecho exato da fonte.</p>
      ${elementos.map(([chave, rotulo]) => {
        const item = cfg.elementos_provados?.[chave] || {};
        return `
          <fieldset class="inner-tile" data-elemento-probatorio="${escapeHtml(chave)}">
            <legend>${escapeHtml(rotulo)}</legend>
            <label class="form-label">Conclusão
              ${campoConclusao([["provado", "Provado"], ["nao_provado", "Não provado"],
                ["indeterminado", "Indeterminado"]], item.conclusao)}
            </label>
            <label class="form-label">Fundamento
              <textarea class="form-textarea" rows="2" data-fundamento>${escapeHtml(item.fundamento || "")}</textarea>
            </label>
            ${camposProva("Prova favorável", "favor", item.provas_favoraveis?.[0])}
            ${camposProva("Prova contrária", "contra", item.provas_contrarias?.[0])}
          </fieldset>`;
      }).join("")}
    </details>`;
}

export function camposCautelar() {
  if (state.activeCase?.is_demo || !state.activeCase?.elementos_probatorios) return "";
  const item = state.decisaoCautelar || {};
  return `
    <details class="inner-tile" id="registroCautelar">
      <summary>Decisão cautelar da sentença</summary>
      <label class="form-label">Decisão do juízo
        ${campoConclusao([["conceder", "Conceder o direito de recorrer em liberdade"],
          ["manter_prisao", "Manter a prisão preventiva"],
          ["impor_prisao", "Decretar a prisão preventiva"]], item.decisao)}
      </label>
      <label class="form-label">Fundamento
        <textarea class="form-textarea" rows="2" data-fundamento>${escapeHtml(item.fundamento || "")}</textarea>
      </label>
      ${camposProva("Referência", "cautelar", item.referencias?.[0])}
    </details>`;
}

function lerReferencia(el, chave) {
  const fonte_id = el.querySelector(`[data-fonte="${chave}"]`).value.trim();
  const trecho = el.querySelector(`[data-trecho="${chave}"]`).value.trim();
  return fonte_id || trecho ? [{ fonte_id, trecho }] : [];
}

export function ligarRegistroProbatorio(edEl, cfg) {
  edEl.querySelectorAll("[data-elemento-probatorio]").forEach((el) => {
    el.addEventListener("input", () => {
      const elemento = el.dataset.elementoProbatorio;
      cfg.elementos_provados ||= {};
      cfg.elementos_provados[elemento] = {
        elemento,
        conclusao: el.querySelector("[data-conclusao]").value,
        fundamento: el.querySelector("[data-fundamento]").value.trim(),
        provas_favoraveis: lerReferencia(el, "favor"),
        provas_contrarias: lerReferencia(el, "contra"),
      };
    });
  });
  const el = edEl.querySelector("#registroCautelar");
  el?.addEventListener("input", () => {
    state.decisaoCautelar = {
      decisao: el.querySelector("[data-conclusao]").value,
      fundamento: el.querySelector("[data-fundamento]").value.trim(),
      referencias: lerReferencia(el, "cautelar"),
    };
  });
}
