// Registro probatório e decisão cautelar do juízo.
import { state } from "./estado.js?v=2.5.0";
import { escapeHtml } from "./dom.js?v=2.5.0";

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

function campoRespostaAcao(rotulo, chave, valor) {
  return `
    <label class="form-label">${rotulo}
      <select class="form-select" data-condicao-acao="${chave}" data-resposta-acao>
        <option value="">Pendente</option>
        <option value="sim" ${valor === true ? "selected" : ""}>Sim</option>
        <option value="nao" ${valor === false ? "selected" : ""}>Não</option>
      </select>
    </label>`;
}

function campoDataAcao(rotulo, chave, valor) {
  return `
    <label class="form-label">${rotulo}
      <input class="form-control" type="date" data-condicao-acao="${chave}" value="${escapeHtml(valor || "")}">
    </label>`;
}

function camposCondicoesAcao(cfg) {
  const dados = cfg.condicoes_acao_penal || {};
  return `
    <details class="inner-tile" data-registro-condicoes>
      <summary>Condições da ação e regra etária desta imputação</summary>
      ${campoRespostaAcao("Violência sexual contra a mulher", "violencia_sexual_contra_mulher", cfg.violencia_sexual_contra_mulher)}
      ${campoRespostaAcao("Violência doméstica e familiar contra a mulher", "violencia_domestica_familiar_contra_mulher", dados.violencia_domestica_familiar_contra_mulher)}
      <fieldset>
        <legend>Prazo de representação ou de queixa</legend>
        ${campoDataAcao("Ciência da autoria", "data_ciencia_autoria", dados.data_ciencia_autoria)}
        ${campoRespostaAcao("Queixa subsidiária por inércia do MP", "queixa_subsidiaria", dados.queixa_subsidiaria)}
        ${campoDataAcao("Esgotamento do prazo para o MP denunciar", "data_esgotamento_prazo_denuncia", dados.data_esgotamento_prazo_denuncia)}
        ${campoRespostaAcao("Direito de representação ou queixa exercido", "direito_exercido", dados.direito_exercido)}
        ${campoDataAcao("Data da representação ou da queixa", "data_exercicio_direito", dados.data_exercicio_direito)}
      </fieldset>
      <fieldset>
        <legend>Audiência de retratação</legend>
        ${campoRespostaAcao("Pedido expresso da ofendida", "pedido_expresso_retratacao", dados.pedido_expresso_retratacao)}
        ${campoDataAcao("Data da manifestação expressa", "data_pedido_retratacao", dados.data_pedido_retratacao)}
        ${campoRespostaAcao("Denúncia ainda não recebida", "denuncia_ainda_nao_recebida", dados.denuncia_ainda_nao_recebida)}
        ${campoDataAcao("Recebimento da denúncia desta imputação", "data_recebimento_denuncia", dados.data_recebimento_denuncia)}
        ${campoDataAcao("Data da audiência, se realizada", "data_audiencia_retratacao", dados.data_audiencia_retratacao)}
      </fieldset>
    </details>`;
}

export function dadosCondicoesAcao(cfg) {
  const dados = {};
  if (typeof cfg.violencia_sexual_contra_mulher === "boolean") {
    dados.violencia_sexual_contra_mulher = cfg.violencia_sexual_contra_mulher;
  }
  if (Object.keys(cfg.condicoes_acao_penal || {}).length) dados.condicoes_acao_penal = cfg.condicoes_acao_penal;
  return dados;
}

export function camposConclusoes(cfg, codigo) {
  if (state.activeCase?.is_demo) return "";
  const condicoes = camposCondicoesAcao(cfg);
  if (!cfg.veredicto || cfg.veredicto === "extinguir") return condicoes;
  const elementos = Object.entries(elementosDoCrime(codigo));
  if (!elementos.length) return condicoes;
  return `${condicoes}
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
  edEl.querySelectorAll("[data-condicao-acao]").forEach((campo) => {
    campo.addEventListener("input", () => {
      const chave = campo.dataset.condicaoAcao;
      const valor = campo.hasAttribute("data-resposta-acao") ? campo.value === "sim" : campo.value;
      cfg.condicoes_acao_penal ||= {};
      const registro = chave === "violencia_sexual_contra_mulher" ? cfg : cfg.condicoes_acao_penal;
      if (campo.value) registro[chave] = valor;
      else delete registro[chave];
    });
  });
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
