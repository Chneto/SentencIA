import { $, escapeHtml } from "./dom.js?v=2.4.0";

const intervaloVazio = () => ({ inicio: "", fim: "", fonte_inicio: "", fonte_fim: "", em_curso: false });

function opcoesFonte(pecas, id) {
  return '<option value="">Selecione a peça</option>' + pecas.map((peca) =>
    `<option value="${escapeHtml(peca.doc_id)}" ${peca.doc_id === id ? "selected" : ""}>
      ${escapeHtml(peca.doc_id + " · " + peca.titulo)}
    </option>`
  ).join("");
}

function linha(intervalo, pecas) {
  return `<div class="inner-tile" data-intervalo-custodia>
    <label class="form-label">Início da custódia
      <input class="form-control" type="date" name="inicio" value="${escapeHtml(intervalo.inicio || "")}">
    </label>
    <label class="form-label">Fonte do início
      <select class="form-select" name="fonte_inicio">${opcoesFonte(pecas, intervalo.fonte_inicio)}</select>
    </label>
    <label class="form-label">Soltura
      <input class="form-control" type="date" name="fim" value="${escapeHtml(intervalo.fim || "")}" ${intervalo.em_curso ? "disabled" : ""}>
    </label>
    <label class="switch-inline"><input type="checkbox" name="em_curso" ${intervalo.em_curso ? "checked" : ""}>
      <span>Custódia em curso na data da sentença</span>
    </label>
    <label class="form-label">Fonte da soltura ou da custódia em curso
      <select class="form-select" name="fonte_fim">${opcoesFonte(pecas, intervalo.fonte_fim)}</select>
    </label>
    <button class="btn btn-sm" type="button" data-custodia-remover>Remover intervalo</button>
  </div>`;
}

export function dadosDetracao() {
  if (!$("usarIntervalosCustodia")?.checked) return {};
  const intervalos = $("intervalosCustodia").querySelectorAll("[data-intervalo-custodia]");
  return {
    intervalos_prisao: [...intervalos].map((item) => {
      const valor = (nome) => item.querySelector('[name="' + nome + '"]').value;
      const emCurso = item.querySelector('[name="em_curso"]').checked;
      return {
        inicio: valor("inicio"),
        fim: emCurso ? null : valor("fim") || null,
        fonte_inicio: valor("fonte_inicio"),
        fonte_fim: valor("fonte_fim"),
        em_curso: emCurso,
      };
    }),
  };
}

export function montarDetracao(caso, salvos, aoMudar) {
  const numero = $("inputDiasPrisaoCautelar");
  if (!numero) return;
  $("editorCustodia")?.remove();
  numero.disabled = false;
  if (!caso || caso.empty) return;
  const editor = document.createElement("div");
  editor.id = "editorCustodia";
  editor.className = "inner-tile";
  editor.innerHTML = `
    <label class="switch-inline"><input id="usarIntervalosCustodia" type="checkbox" ${Array.isArray(salvos) ? "checked" : ""}>
      <span>Informar intervalos documentados de custódia</span>
    </label>
    <div id="edicaoCustodia">
      <div id="intervalosCustodia"></div>
      <button type="button" class="btn btn-sm" data-custodia-adicionar>Adicionar intervalo</button>
    </div>`;
  numero.closest("div").parentElement.after(editor);
  const pecas = caso.pecas || [];
  const periodos = salvos || caso.metadados?.intervalos_custodia || [];
  const linhas = $("intervalosCustodia");
  linhas.innerHTML = periodos.map((periodo) => linha(periodo, pecas)).join("");
  const atualizarModo = () => {
    numero.disabled = $("usarIntervalosCustodia").checked;
    $("edicaoCustodia").hidden = !numero.disabled;
  };
  atualizarModo();
  editor.addEventListener("change", (e) => {
    atualizarModo();
    if (e.target.name === "em_curso") {
      e.target.closest("[data-intervalo-custodia]").querySelector('[name="fim"]').disabled = e.target.checked;
    }
    aoMudar();
  });
  editor.addEventListener("click", (e) => {
    if (e.target.closest("[data-custodia-adicionar]")) {
      linhas.insertAdjacentHTML("beforeend", linha(intervaloVazio(), pecas));
    } else if (e.target.closest("[data-custodia-remover]")) {
      e.target.closest("[data-intervalo-custodia]").remove();
    } else return;
    aoMudar();
  });
}
