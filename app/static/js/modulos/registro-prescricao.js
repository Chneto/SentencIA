import { escapeHtml, fmtPena, rotuloCrime } from "./dom.js?v=2.2.0";
import { state } from "./estado.js?v=2.2.0";

const TIPOS = {
  termo_inicial: {
    consumacao: "Consumação",
    cessacao_tentativa: "Cessação da atividade na tentativa",
    cessacao_permanencia: "Cessação da permanência",
    ciencia_fato_registro_civil: "Ciência do fato no registro civil",
    implemento_idade_vitima: "Idade da vítima (art. 111, V, CP)",
  },
  interrupcoes: {
    recebimento_denuncia_ou_queixa: "Recebimento da denúncia ou queixa",
    pronuncia: "Pronúncia",
    confirmacao_pronuncia: "Confirmação da pronúncia",
    publicacao_sentenca_condenatoria: "Publicação da sentença condenatória",
    publicacao_acordao_condenatorio: "Publicação do acórdão condenatório",
    publicacao_acordao_confirmatorio: "Publicação do acórdão confirmatório",
  },
  suspensoes: {
    questao_prejudicial: "Questão prejudicial em outro processo",
    cumprimento_pena_exterior: "Cumprimento de pena no exterior",
    recurso_inadmissivel: "Recurso inadmissível (art. 116, III, CP)",
    acordo_nao_persecucao_penal: "Acordo de não persecução penal",
    citacao_edital_art366: "Citação por edital (art. 366, CPP)",
    citacao_exterior_art368: "Citação no exterior (art. 368, CPP)",
    suspensao_condicional_processo: "Suspensão condicional do processo",
  },
  estabilidade_acusacao: {
    transito_para_acusacao: "Trânsito em julgado para a acusação",
    recurso_da_acusacao_improvido: "Recurso da acusação improvido",
  },
  reconhecimento: { abstrata: "Em abstrato", retroativa: "Retroativa" },
};

function campo(rotulo, chave, tipo, dados, atributos = "") {
  return `<label class="form-label">${rotulo}
    <input class="form-control" type="${tipo}" data-presc-campo="${chave}"
      value="${escapeHtml(String(dados[chave] ?? ""))}" ${atributos}>
  </label>`;
}

function linha(grupo, dados = {}, removivel = false) {
  const chave = grupo === "reconhecimento" ? "modalidade" : "tipo";
  const opcoes = Object.entries(TIPOS[grupo] || {});
  const tipo = opcoes.length ? `<label class="form-label">${chave === "tipo" ? "Tipo" : "Modalidade"}
    <select class="form-select" data-presc-campo="${chave}">
      <option value="">Selecione</option>
      ${opcoes.map(([valor, texto]) => `<option value="${valor}" ${dados[chave] === valor ? "selected" : ""}>${texto}</option>`).join("")}
    </select>
  </label>` : "";
  const pena = grupo === "pena_documentada" ? `<div class="grid-3">
    ${campo("Anos", "anos", "number", dados, 'min="0" max="150"')}
    ${campo("Meses", "meses", "number", dados, 'min="0" max="11"')}
    ${campo("Dias", "dias", "number", dados, 'min="0" max="29"')}
  </div>` : "";
  return `<div class="inner-tile" data-presc-linha="${grupo}">
    ${tipo}
    ${campo(grupo === "suspensoes" ? "Início da suspensão" : "Data", "data", "date", dados)}
    ${grupo === "suspensoes" ? campo("Retomada do prazo", "data_fim", "date", dados) : ""}
    ${pena}
    ${campo("ID da peça", "fonte_id", "text", dados, 'list="fontesPrescricao"')}
    ${removivel ? '<button class="btn btn-ghost" type="button" data-presc-remover>Remover marco</button>' : ""}
  </div>`;
}

function grupoRepetido(grupo, rotulo, dados) {
  return `<fieldset data-presc-grupo="${grupo}">
    <legend>${rotulo}</legend>
    <div data-presc-lista>${(dados[grupo] || []).map((registro) => linha(grupo, registro, true)).join("")}</div>
    <button class="btn btn-ghost" type="button" data-presc-adicionar="${grupo}">Adicionar marco</button>
  </fieldset>`;
}

export function camposPrescricao(cfg) {
  const dados = cfg.linha_tempo_prescricao || {};
  const fontes = (state.activeCase?.pecas || []).map((peca) =>
    `<option value="${escapeHtml(peca.doc_id || "")}">${escapeHtml(peca.titulo || "")}</option>`
  ).join("");
  return `<details class="inner-tile" data-registro-prescricao>
    <summary>Marcos da prescrição desta imputação</summary>
    <datalist id="fontesPrescricao">${fontes}</datalist>
    <fieldset><legend>Termo inicial específico, se houver</legend>${linha("termo_inicial", dados.termo_inicial)}</fieldset>
    ${grupoRepetido("interrupcoes", "Interrupções", dados)}
    ${grupoRepetido("suspensoes", "Suspensões", dados)}
    <fieldset><legend>Pena anterior e data de publicação</legend>${linha("pena_documentada", dados.pena_documentada)}</fieldset>
    <fieldset><legend>Estabilidade para a acusação</legend>${linha("estabilidade_acusacao", dados.estabilidade_acusacao)}</fieldset>
    <fieldset><legend>Prescrição reconhecida em decisão</legend>${linha("reconhecimento", dados.reconhecimento)}</fieldset>
  </details>`;
}

export function ligarRegistroPrescricao(edEl, cfg) {
  const editor = edEl.querySelector("[data-registro-prescricao]");
  if (!editor) return;
  editor.addEventListener("input", () => {
    const dados = {};
    for (const row of editor.querySelectorAll("[data-presc-linha]")) {
      const grupo = row.dataset.prescLinha;
      const registro = {};
      for (const entrada of row.querySelectorAll("[data-presc-campo]")) {
        if (entrada.value.trim()) {
          registro[entrada.dataset.prescCampo] = entrada.type === "number" ? Number(entrada.value) : entrada.value.trim();
        }
      }
      if (!Object.keys(registro).length) continue;
      if (grupo === "interrupcoes" || grupo === "suspensoes") (dados[grupo] ||= []).push(registro);
      else dados[grupo] = registro;
    }
    cfg.linha_tempo_prescricao = dados;
  });
  editor.addEventListener("click", (e) => {
    const adicionar = e.target.closest("[data-presc-adicionar]");
    const remover = e.target.closest("[data-presc-remover]");
    if (adicionar) {
      const lista = adicionar.closest("[data-presc-grupo]").querySelector("[data-presc-lista]");
      lista.insertAdjacentHTML("beforeend", linha(adicionar.dataset.prescAdicionar, {}, true));
      lista.lastElementChild.querySelector("select").focus();
    }
    if (remover) {
      remover.closest("[data-presc-linha]").remove();
      editor.dispatchEvent(new Event("input", { bubbles: true }));
    }
  });
}

const ESTADOS = {
  sem_indicio: ["Sem lapso consumado nos marcos informados", "success"],
  abstrata: ["Lapso em abstrato a decidir", "warn"],
  projetada: ["Projeção pela pena em concreto", "warn"],
  pendente: ["Pendente de decisão ou de marco", "warn"],
  inconclusiva: ["Verificação inconclusiva", "warn"],
  reconhecida: ["Prescrição reconhecida pelo juízo", "danger"],
};

function dataBr(valor) {
  return valor ? valor.split("-").reverse().join("/") : "";
}

function listaLapsos(lapsos, rotulo) {
  if (!lapsos?.length) return "";
  return `<details>
    <summary>${rotulo}</summary>
    <ul>${lapsos.map((lapso) => `
      <li>${escapeHtml(dataBr(lapso.inicio))} a ${escapeHtml(dataBr(lapso.fim))}
        · ${lapso.dias_suspensos} dia(s) suspenso(s)
        · primeiro dia após o prazo em ${escapeHtml(dataBr(lapso.primeiro_dia_consumado))}
      </li>`).join("")}</ul>
  </details>`;
}

export function painelPrescricao(prescricao) {
  if (!prescricao) return '<p class="muted">Simule a dosimetria para ver a prescrição.</p>';
  const itens = (prescricao.por_crime || []).map((item) => {
    const estado = item.estado || (item.verificacao_completa === false ? "inconclusiva"
      : item.indicio_abstrato ? "abstrata" : item.projecao_retroativa ? "projetada" : "sem_indicio");
    const [rotulo, cor] = ESTADOS[estado] || ESTADOS.inconclusiva;
    return `<article class="inner-tile">
      <strong>${escapeHtml(item.crime_nome || rotuloCrime(item.crime_code))}
        ${escapeHtml(item.artigo_formatado || "")} ${escapeHtml(item.referencia_imputacao || "")}</strong>
      <span class="badge-tag badge-${cor}">${rotulo}</span>
      ${item.pena_concreta ? `<div class="muted mono">Pena concreta ${escapeHtml(fmtPena(item.pena_concreta))}
        · prazo ${item.prazo_concreto_meses_efetivo} meses</div>` : ""}
      <p>${escapeHtml(item.fundamentacao || "")}</p>
      ${listaLapsos(item.lapsos_abstratos, "Lapsos em abstrato")}
      ${listaLapsos(item.lapsos_concretos, "Lapsos em concreto")}
    </article>`;
  }).join("");
  return itens || `<p class="muted">${escapeHtml(prescricao.fundamentacao || "")}</p>`;
}
