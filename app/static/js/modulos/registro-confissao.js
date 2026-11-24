import { escapeHtml, fmtDateBr, rotuloCrime, showToast } from "./dom.js?v=2.1.0";
import { handleApiError, postJson } from "./api.js?v=2.1.0";
import { hooks, state } from "./estado.js?v=2.1.0";

const TIPOS = {
  total: "Confissão integral",
  parcial: "Confissão parcial",
  qualificada: "Confissão qualificada",
  negativa: "Negativa do fato",
  ausente: "Sem confissão reconhecida",
  inconclusiva: "Relatos divergentes",
};

export function camposConfissao(meta) {
  const imputacoes = (meta.imputacoes || []).filter((i) =>
    i.estado === "extraida" && !(i.eventos || []).some((e) => ["rejeicao", "extincao"].includes(e.tipo))
  );
  const alvos = (meta.crimes_imputados || []).flatMap((codigo) => {
    const itens = imputacoes.filter((i) => i.codigo === codigo);
    return itens.length ? itens : [{ codigo }];
  });
  if (!alvos.length) return '<p class="muted">Identifique a imputação antes de registrar a decisão.</p>';
  return alvos.map((alvo, indice) => {
    const chave = alvo.id_imputacao || alvo.codigo;
    const sugestao = meta.confissoes_sugeridas?.[chave];
    const decisao = meta.confissoes_confirmadas?.[chave];
    const rotulo = alvo.numero_fato ? "Fato " + alvo.numero_fato
      : alvo.data_fato ? "Fato de " + fmtDateBr(alvo.data_fato) : "Imputação " + (indice + 1);
    const info = [sugestao?.fonte_id, sugestao?.folha, sugestao?.minuto].filter(Boolean).join(" · ");
    const selecionado = decisao?.tipo || "";
    const opcoes = Object.entries(TIPOS).filter(([tipo]) => tipo !== "inconclusiva").map(([tipo, descricao]) =>
      `<option value="${tipo}" ${tipo === selecionado ? "selected" : ""}>${descricao}</option>`
    ).join("");
    return `<form class="inner-tile" data-confissao-imputacao="${escapeHtml(chave)}">
      <h4>${escapeHtml(rotulo + " · " + rotuloCrime(alvo.codigo))}</h4>
      ${sugestao?.tipo && sugestao.tipo !== "ausente"
        ? `<p>Indício: ${escapeHtml(TIPOS[sugestao.tipo])}</p>` : '<p class="muted">Sem indício vinculado a esta imputação.</p>'}
      ${info ? `<details><summary>${escapeHtml(info)}</summary><p>${escapeHtml(sugestao.trecho)}</p></details>` : ""}
      <label class="form-label">Decisão do juízo
        <select class="form-select" name="tipo">
          <option value="">A decidir</option>
          ${opcoes}
        </select>
      </label>
      <button class="btn btn-sm" type="submit">Registrar decisão</button>
    </form>`;
  }).join("");
}

let salvandoConfissao = false;

export async function salvarConfissao(e) {
  const form = e.target.closest("[data-confissao-imputacao]");
  if (!form) return;
  e.preventDefault();
  if (salvandoConfissao) return;
  const caso = state.activeCase;
  const tipo = new FormData(form).get("tipo");
  const chave = form.dataset.confissaoImputacao;
  const botao = e.submitter;
  salvandoConfissao = true;
  if (botao) botao.disabled = true;
  try {
    const resposta = await postJson("/api/cases/metadata-override", {
      manifesto_id: caso.manifesto_caso?.id,
      metadados: { confissoes_confirmadas: { [chave]: tipo ? { tipo } : null } },
    });
    if (state.activeCase?.case_id !== caso.case_id) return;
    await hooks.applyActiveCase(resposta.active_case, { clearSentence: true });
    showToast(tipo ? "Decisão de confissão registrada." : "Decisão de confissão removida.", "success");
  } catch (erro) {
    handleApiError(erro);
  } finally {
    salvandoConfissao = false;
    if (botao) botao.disabled = false;
  }
}
