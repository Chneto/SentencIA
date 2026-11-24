import { $, escapeHtml } from "./dom.js?v=2.1.0";
import { state } from "./estado.js?v=2.1.0";

let casoDoEditor = null;

function opcoesDecisao(opcoes) {
  return '<option value="">Pendente</option>' + opcoes.map(([codigo, rotulo]) =>
    `<option value="${codigo}">${rotulo}</option>`
  ).join("");
}

const beneficios = [["conceder", "Conceder"], ["negar", "Negar"]];
const modalidades = [
  ["prestacao_servicos", "Prestação de serviços à comunidade ou a entidades públicas"],
  ["interdicao_direitos", "Interdição temporária de direitos"],
  ["limitacao_fim_semana", "Limitação de fim de semana"],
  ["perda_bens", "Perda de bens e valores"],
];
const medidas = [["manter", "Manter"], ["revogar", "Revogar"], ["sem_medidas", "Registrar que não há medidas vigentes"]];

function campos(chave, rotulo, opcoes, extras) {
  return `<fieldset data-acessorio="${chave}">
    <legend>${rotulo}</legend>
    <label class="form-label">Decisão
      <select class="form-select" data-decisao-acessoria>${opcoesDecisao(opcoes)}</select>
    </label>
    <label class="form-label" data-fundamento-acessorio hidden>Fundamento
      <textarea class="form-textarea" rows="2"></textarea>
    </label>
    <div data-detalhes-acessorios hidden>${extras}</div>
  </fieldset>`;
}

export function renderDecisoesAcessorias() {
  const ancora = $("sentencePendenciasPanel");
  if (!ancora) return;
  let editor = $("editorDecisoesAcessorias");
  if (!editor) {
    editor = document.createElement("details");
    editor.id = "editorDecisoesAcessorias";
    editor.className = "inner-tile";
    ancora.after(editor);
    editor.addEventListener("change", (evento) => {
      if (!evento.target.matches("[data-decisao-acessoria]")) return;
      const campo = evento.target.closest("[data-acessorio]");
      const valor = evento.target.value;
      campo.querySelector("[data-fundamento-acessorio]").hidden = !valor;
      campo.querySelector("[data-detalhes-acessorios]").hidden = !valor || valor === "negar";
      const contraditorio = campo.querySelector("[data-confirmacao-oitivas]");
      if (contraditorio) contraditorio.hidden = valor !== "revogar";
    });
  }
  const caso = state.activeCase;
  editor.hidden = !state.caseKey || !caso || caso.empty;
  if (casoDoEditor === state.caseKey) return;
  casoDoEditor = state.caseKey;
  editor.open = false;
  const fontes = (caso?.pecas || []).map(({doc_id, titulo}) =>
    `<label class="switch-inline"><input type="checkbox" data-fonte-acessoria value="${escapeHtml(doc_id)}">
      <span>${escapeHtml(doc_id + " · " + titulo)}</span></label>`
  ).join("");
  editor.innerHTML = "<summary>Decisões acessórias</summary>"
    + campos("substituicao", "Substituição da pena", beneficios, modalidades.map(([codigo, rotulo]) =>
      `<label class="switch-inline"><input type="checkbox" data-modalidade value="${codigo}"><span>${rotulo}</span></label>`
    ).join("") + '<label class="form-label">Especificação das restritivas <textarea class="form-textarea" rows="2" data-especificacao-acessoria></textarea></label>')
    + campos("sursis", "Suspensão condicional da pena", beneficios,
      '<p class="muted">Registre a negativa da substituição para conceder a suspensão.</p>'
      + '<label class="form-label">Prazo em anos <input class="form-control" type="number" min="2" max="4" step="1" data-prazo-acessorio></label>'
      + '<label class="form-label">Condições, uma por linha <textarea class="form-textarea" rows="3" data-condicoes-acessorias></textarea></label>')
    + campos("medidas_protetivas", "Medidas protetivas de urgência", medidas,
      '<details><summary>Peças dos autos</summary>' + fontes + '</details>'
      + '<label class="switch-inline" data-confirmacao-oitivas hidden><input type="checkbox"><span>A ofendida e o requerido já foram ouvidos sobre a revogação</span></label>');
}

export function dadosAcessorias() {
  if (!state.caseKey || casoDoEditor !== state.caseKey) return {};
  const dados = {};
  for (const campo of document.querySelectorAll("#editorDecisoesAcessorias [data-acessorio]")) {
    const decisao = campo.querySelector("[data-decisao-acessoria]").value;
    if (!decisao) continue;
    const chave = campo.dataset.acessorio;
    const registro = { decisao, fundamento: campo.querySelector("[data-fundamento-acessorio] textarea").value.trim() };
    const selecionados = (seletor) => [...campo.querySelectorAll(seletor)].map((item) => item.value);
    if (chave === "medidas_protetivas") {
      registro.fontes = selecionados("[data-fonte-acessoria]:checked");
      registro.contraditorio_confirmado = decisao === "revogar" && campo.querySelector("[data-confirmacao-oitivas] input").checked;
    } else if (decisao === "conceder" && chave === "sursis") {
      const prazo = campo.querySelector("[data-prazo-acessorio]").value;
      registro.prazo_anos = prazo ? Number(prazo) : null;
      registro.modalidades = campo.querySelector("[data-condicoes-acessorias]").value.split("\n").map((c) => c.trim()).filter(Boolean);
    } else if (decisao === "conceder") {
      registro.modalidades = selecionados("[data-modalidade]:checked");
      registro.especificacao = campo.querySelector("[data-especificacao-acessoria]").value.trim() || null;
    }
    dados[chave] = registro;
  }
  return Object.keys(dados).length ? { decisoes_acessorias: dados } : {};
}
