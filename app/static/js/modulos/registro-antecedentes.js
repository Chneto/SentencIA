import { escapeHtml, fmtDateBr } from "./dom.js?v=2.4.0";

export function camposAntecedentes(registros, cfg) {
  return registros.map((registro) => {
    const id = registro.id_registro;
    const fase = cfg.usos_antecedentes?.[id] || "";
    const situacao = registro.conflito ? "Dados divergentes" : {
      definitiva: "Condenação definitiva", em_curso: "Em curso", sem_efeito: "Sem efeito penal",
      indeterminada: "Situação indeterminada",
    }[registro.situacao];
    const datas = [
      ["Fato anterior", registro.data_fato_anterior],
      ["Trânsito", registro.data_transito],
      ["Cumprimento ou extinção", registro.data_cumprimento_extincao],
    ].map(([rotulo, data]) => rotulo + " " + (data ? fmtDateBr(data) : "a conferir")).join(" · ");
    const verFonte = (registro.fontes || []).map((fonte) =>
      `<details><summary>${escapeHtml([fonte.fonte_id, fonte.folha].filter(Boolean).join(" · "))}</summary><p>${escapeHtml(fonte.trecho)}</p></details>`
    ).join("");
    const opcoes = [
      ["", "A decidir"],
      ...(registro.situacao === "definitiva" && registro.data_transito && !registro.conflito
        ? [["antecedentes", "1ª fase · valorar antecedentes"], ["reincidencia", "2ª fase · reconhecer reincidência"]]
        : []),
      ["desconsiderar", "Sem valoração nesta imputação"],
    ];
    return `<div class="inner-tile">
      <label class="form-label" for="uso-${escapeHtml(id)}">${escapeHtml(registro.processo || registro.fontes?.[0]?.fonte_id || "Registro da certidão")}</label>
      <p class="muted">${escapeHtml([situacao, datas].filter(Boolean).join(" · "))}</p>
      ${verFonte}
      <select class="form-select" id="uso-${escapeHtml(id)}" data-uso-antecedente="${escapeHtml(id)}">
        ${opcoes.map(([opcao, rotulo]) => `<option value="${opcao}" ${fase === opcao ? "selected" : ""}>${rotulo}</option>`).join("")}
      </select>
    </div>`;
  }).join("");
}

export function ligarAntecedentes(container, cfg, aoMudar) {
  container.querySelectorAll("[data-uso-antecedente]").forEach((campo) => {
    campo.addEventListener("change", (e) => {
      cfg.usos_antecedentes[e.target.dataset.usoAntecedente] = e.target.value;
      aoMudar();
    });
  });
}

export function dadosAntecedentes(registros, cfg) {
  if (registros.length) {
    const usos = registros.flatMap(({ id_registro }) => {
      const fase = cfg.usos_antecedentes?.[id_registro];
      return fase ? [{ id_registro, fase }] : [];
    });
    return { usos_antecedentes: usos };
  }
  if (cfg.reincidente === "sim" || cfg.reincidente === "nao") {
    return { reincidente: cfg.reincidente === "sim" };
  }
  return {};
}
