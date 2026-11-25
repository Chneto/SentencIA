import { escapeHtml, fmtDateBr, rotuloCrime, showToast } from "./dom.js?v=2.4.0";
import { handleApiError, postJson } from "./api.js?v=2.4.0";
import { hooks, state } from "./estado.js?v=2.4.0";

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

// TASK-086: uma decisão de confissão por imputação. A Dosimetria segue a Análise por padrão;
// escolha divergente aparece como conflito e o servidor bloqueia o cálculo até a correção.
const TIPOS_CONFISSAO = { total: "integral", parcial: "parcial", qualificada: "qualificada" };
const ROTULOS_REGISTRO_CONFISSAO = {
  total: "confissão integral", parcial: "confissão parcial", qualificada: "confissão qualificada",
  negativa: "negativa do fato", ausente: "sem confissão reconhecida",
};

function confissaoRegistrada(cfg) {
  const chave = cfg.id_imputacao || cfg.codigo;
  return state.activeCase?.metadados?.confissoes_confirmadas?.[chave]?.tipo || "";
}

export function opcoesConfissao(cfg) {
  const registrada = confissaoRegistrada(cfg);
  const auto = registrada ? `Conforme a Análise (${ROTULOS_REGISTRO_CONFISSAO[registrada] || registrada})`
    : "Conforme decisão na Análise (a decidir)";
  const opcoes = [["auto", auto]];
  if (cfg.confissao === "sim") opcoes.push(["sim", "Sim — tipo a informar"]);
  Object.entries(TIPOS_CONFISSAO).forEach(([tipo, rotulo]) => opcoes.push([tipo, `Sim — confissão ${rotulo}`]));
  opcoes.push(["nao", "Não reconhecer"]);
  return opcoes.map(([valor, rotulo]) =>
    `<option value="${valor}" ${cfg.confissao === valor ? "selected" : ""}>${escapeHtml(rotulo)}</option>`
  ).join("");
}

function conflitoConfissao(cfg) {
  const registrada = confissaoRegistrada(cfg);
  if (!registrada || !cfg.confissao || cfg.confissao === "auto") return "";
  const afirmativa = Object.hasOwn(TIPOS_CONFISSAO, registrada);
  const reconhece = cfg.confissao !== "nao";
  const tipoDivergente = afirmativa && Object.hasOwn(TIPOS_CONFISSAO, cfg.confissao) && cfg.confissao !== registrada;
  if (reconhece === afirmativa && !tipoDivergente) return "";
  return `A Análise registrou "${ROTULOS_REGISTRO_CONFISSAO[registrada] || registrada}". `
    + 'Escolha "Conforme a Análise" ou corrija o registro na Análise antes de calcular.';
}

export function avisoConflitoConfissao(cfg) {
  const conflito = conflitoConfissao(cfg);
  return conflito ? `<p class="muted" role="alert" data-conflito-confissao style="color:var(--semantic-danger)">${escapeHtml(conflito)}</p>` : "";
}

/** Campos de confissão da imputação no payload da Dosimetria. */
export function dadosConfissaoDosimetria(cfg) {
  if (cfg.confissao === "sim") return { confissao: true };
  if (Object.hasOwn(TIPOS_CONFISSAO, cfg.confissao)) return { confissao: true, tipo_confissao: cfg.confissao };
  if (cfg.confissao === "nao") return { confissao: false };
  return {};
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
