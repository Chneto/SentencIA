import { $, escapeHtml, showToast } from "./dom.js?v=2.2.1";
import { state } from "./estado.js?v=2.2.1";

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
const ROTULOS_ACESSORIOS = {
  substituicao: "Substituição da pena",
  sursis: "Suspensão condicional da pena",
  medidas_protetivas: "Medidas protetivas",
};

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

function limparDestaquesAcessorios(editor) {
  if (!editor) return;
  editor.querySelectorAll('[aria-invalid="true"]').forEach((el) => {
    el.removeAttribute("aria-invalid");
    el.style.outline = "";
  });
}

function destacarAlvoAcessorio(editor, alvo) {
  if (!editor) return;
  editor.open = true;
  if (!alvo) return;
  const detalhesPai = alvo.closest("details");
  if (detalhesPai && detalhesPai !== editor) detalhesPai.open = true;
  alvo.setAttribute("aria-invalid", "true");
  alvo.style.outline = "2px solid var(--semantic-danger)";
  const limpar = () => {
    alvo.removeAttribute("aria-invalid");
    alvo.style.outline = "";
  };
  alvo.addEventListener("input", limpar, { once: true });
  alvo.addEventListener("change", limpar, { once: true });
  if (typeof alvo.scrollIntoView === "function") {
    alvo.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  if (typeof alvo.focus === "function") alvo.focus();
}

export function validarDecisoesAcessorias() {
  const editor = $("editorDecisoesAcessorias");
  if (!editor || !state.caseKey || casoDoEditor !== state.caseKey) return { ok: true };

  const porChave = {};
  for (const campo of editor.querySelectorAll("[data-acessorio]")) {
    porChave[campo.dataset.acessorio] = {
      campo,
      decisao: campo.querySelector("[data-decisao-acessoria]")?.value || "",
    };
  }

  for (const [chave, info] of Object.entries(porChave)) {
    const { campo, decisao } = info;
    if (!decisao) continue;
    const rotulo = ROTULOS_ACESSORIOS[chave] || chave;
    const txtFundamento = campo.querySelector("[data-fundamento-acessorio] textarea");
    const fundamento = (txtFundamento?.value || "").trim();
    if (!fundamento) {
      return { ok: false, mensagem: `${rotulo}: informe o fundamento da decisão.`, alvo: txtFundamento };
    }
    if (chave === "medidas_protetivas") {
      const fontes = [...campo.querySelectorAll("[data-fonte-acessoria]:checked")];
      if (!fontes.length) {
        const blocoFontes = campo.querySelector("details") || campo.querySelector("[data-detalhes-acessorios]");
        return { ok: false, mensagem: "Medidas protetivas: selecione ao menos uma peça dos autos.", alvo: blocoFontes };
      }
      const chkOitivas = campo.querySelector("[data-confirmacao-oitivas] input");
      if (decisao === "revogar" && !chkOitivas?.checked) {
        return {
          ok: false,
          mensagem: "Medidas protetivas: confirme a prévia oitiva da ofendida e do requerido para revogar.",
          alvo: chkOitivas,
        };
      }
    } else if (chave === "substituicao" && decisao === "conceder") {
      const mods = [...campo.querySelectorAll("[data-modalidade]:checked")].map((i) => i.value);
      if (!mods.length) {
        return {
          ok: false,
          mensagem: "Substituição da pena: selecione ao menos uma modalidade.",
          alvo: campo.querySelector("[data-modalidade]"),
        };
      }
      const precisaEspec = mods.includes("interdicao_direitos") || mods.includes("perda_bens");
      const txtEspec = campo.querySelector("[data-especificacao-acessoria]");
      if (precisaEspec && !(txtEspec?.value || "").trim()) {
        return {
          ok: false,
          mensagem: "Substituição da pena: especifique o direito a interditar ou os bens e valores atingidos.",
          alvo: txtEspec,
        };
      }
    } else if (chave === "sursis" && decisao === "conceder") {
      if (porChave.substituicao?.decisao !== "negar") {
        return {
          ok: false,
          mensagem: "Suspensão condicional da pena: registre a negativa da substituição antes de conceder o sursis (art. 77, III, do CP).",
          alvo: porChave.substituicao?.campo?.querySelector("[data-decisao-acessoria]"),
        };
      }
      const inputPrazo = campo.querySelector("[data-prazo-acessorio]");
      const prazoBruto = (inputPrazo?.value || "").trim();
      const prazo = prazoBruto ? Number(prazoBruto) : NaN;
      if (!Number.isInteger(prazo) || prazo < 2 || prazo > 4) {
        return {
          ok: false,
          mensagem: "Suspensão condicional da pena: informe o prazo entre 2 e 4 anos.",
          alvo: inputPrazo,
        };
      }
      const txtCondicoes = campo.querySelector("[data-condicoes-acessorias]");
      const condicoes = (txtCondicoes?.value || "").split("\n").map((c) => c.trim()).filter(Boolean);
      if (!condicoes.length) {
        return {
          ok: false,
          mensagem: "Suspensão condicional da pena: informe ao menos uma condição.",
          alvo: txtCondicoes,
        };
      }
    }
  }
  return { ok: true };
}

export function garantirDecisoesAcessoriasValidas({ silent = false } = {}) {
  const editor = $("editorDecisoesAcessorias");
  limparDestaquesAcessorios(editor);
  const res = validarDecisoesAcessorias();
  if (res.ok) return true;
  if (!silent) {
    destacarAlvoAcessorio(editor, res.alvo);
    showToast(res.mensagem, "warn");
  }
  return false;
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
