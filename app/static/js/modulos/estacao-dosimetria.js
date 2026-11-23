// SentencIA — Módulo ES: estacao-dosimetria.js (TASK-015)
import { ART107_OPTIONS, ART386_OPTIONS, META_CAMPOS_LABELS, TENTATIVA_OPTIONS, VEREDICTO_LABELS, VETORES_59_META, hooks, state } from "./estado.js?v=2.1.0";
import { $, activateSubtab, codigosCatalogo, debounce, escapeHtml, fmtMoeda, fmtPena, rotuloCrime, setActiveStation, showToast, updateHeroRibbon } from "./dom.js?v=2.1.0";
import { handleApiError, postJson } from "./api.js?v=2.1.0";
import { abrirComplementacao } from "./estacao-analise.js?v=2.1.0";
import { camposCautelar, camposConclusoes, elementosDoCrime, ligarRegistroProbatorio } from "./registro-probatorio.js?v=2.1.0";

function novoCrimeConfig(code, isDemo) {
  const vetores = {};
  VETORES_59_META.forEach((v) => {
    vetores[v.key] = false;
  });
  // Parâmetros próprios do caso demonstrativo (nunca aplicados a caso real).
  if (isDemo && code === "art_129_p13_cp") vetores.culpabilidade = true;
  return {
    codigo: code,
    nome: rotuloCrime(code),
    veredicto: "",
    fundamento_absolvicao_art386: "",
    fundamento_extincao_art107: "",
    crime_desclassificado: "",
    fundamentacao: "",
    vetores,
    vetores_fundamentacao: {},
    confissao: isDemo && code === "art_24a_lmp" ? "sim" : "auto",
    reincidente: false,
    agravante_art61f: !!isDemo,
    tentativa_fracao: "",
    dias_multa: 0,
    aplicar_aumento_art147a_p1_ii: true,
  };
}

function chaveCaso(c, usarManifesto = true) {
  if (!c || c.empty) return "";
  if (usarManifesto && c.manifesto_caso?.id) return c.manifesto_caso.id;
  const ids = (c.pecas || []).slice(0, 6).map((p) => p.doc_id).join(",");
  return `${c.case_id || ""}|${c.is_demo ? "demo" : "real"}|${(c.pecas || []).length}|${ids}`;
}

/* Decisoes do juizo (veredicto, vetores, fundamentacoes) sobrevivem ao F5: sessionStorage por caso. */
const CHAVE_DECISOES_SESSAO = "jvdfm.decisoes.v1";
let salvarDecisoesTimer = null;

function salvarDecisoesSessao() {
  try {
    if (!state.caseKey || !state.crimeConfigs.length) return;
    sessionStorage.setItem(
      CHAVE_DECISOES_SESSAO,
      JSON.stringify({
        caseKey: state.caseKey,
        crimeConfigs: state.crimeConfigs,
        extras: {
          valor: $("inputValorDanoMoral")?.value || "",
          dias: $("inputDiasPrisaoCautelar")?.value || "",
          fracao: $("selFracaoConcurso")?.value || "",
          menor21: !!$("chkReuMenor21")?.checked,
          maior70: !!$("chkReuMaior70")?.checked,
          decisaoCautelar: state.decisaoCautelar,
        },
      })
    );
  } catch (e) {
    /* armazenamento indisponivel: segue sem persistir */
  }
}

function agendarSalvarDecisoes() {
  clearTimeout(salvarDecisoesTimer);
  salvarDecisoesTimer = setTimeout(salvarDecisoesSessao, 250);
}

function lerDecisoesSessao(caseKey) {
  try {
    const bruto = sessionStorage.getItem(CHAVE_DECISOES_SESSAO);
    if (!bruto || !caseKey) return null;
    const d = JSON.parse(bruto);
    return d && d.caseKey === caseKey && Array.isArray(d.crimeConfigs) ? d : null;
  } catch (e) {
    return null;
  }
}

document.addEventListener("input", agendarSalvarDecisoes);
document.addEventListener("change", agendarSalvarDecisoes);

function identidadeCrime(cfg) {
  return cfg.id_imputacao || cfg.codigo;
}

function itensDaDosimetria(caseData) {
  const meta = caseData?.metadados || {};
  const codigos = new Set(meta.crimes_imputados || []);
  const extraidos = (meta.imputacoes || []).filter((item) =>
    item.estado === "extraida" && item.id_imputacao && codigos.has(item.codigo)
    && !(item.eventos || []).some((evento) => ["extincao", "rejeicao"].includes(evento.tipo))
  );
  return [...codigos].flatMap((codigo) => {
    const itens = extraidos.filter((item) => item.codigo === codigo);
    return itens.length ? itens : [{ codigo }];
  });
}

function initCrimeConfigsFromCase(caseData, mesmoCaso) {
  const itens = itensDaDosimetria(caseData);
  const isDemo = !!caseData?.is_demo;
  const salvas = !mesmoCaso && !state.crimeConfigs.length
    ? (lerDecisoesSessao(state.caseKey) || lerDecisoesSessao(chaveCaso(caseData, false)))
    : null;
  const anteriores = mesmoCaso ? state.crimeConfigs : (salvas?.crimeConfigs || []);
  const selecionada = mesmoCaso && anteriores[state.selectedCrimeIdx];
  const porIdentidade = new Map(anteriores.filter((c) => c.id_imputacao).map((c) => [c.id_imputacao, c]));
  const antigos = anteriores.filter((c) => !c.id_imputacao);
  const multiplicidade = new Map();
  itens.forEach(({ codigo }) => multiplicidade.set(codigo, (multiplicidade.get(codigo) || 0) + 1));
  state.migracaoImputacaoPendente = (
    (mesmoCaso && state.migracaoImputacaoPendente)
    || antigos.some((c) => (multiplicidade.get(c.codigo) || 0) > 1
      || antigos.filter((outro) => outro.codigo === c.codigo).length > 1)
  );
  state.crimeConfigs = itens.map((item, idx) => {
    const inicial = novoCrimeConfig(item.codigo, isDemo);
    const mesmoId = porIdentidade.get(item.id_imputacao);
    const candidatosLegados = antigos.filter((c) => c.codigo === item.codigo);
    const anterior = mesmoId?.codigo === item.codigo ? mesmoId :
      (multiplicidade.get(item.codigo) === 1 && candidatosLegados.length === 1 ? candidatosLegados[0] : null);
    const dataAutos = item.data_fato || "";
    const etiqueta = item.numero_fato ? `Fato ${item.numero_fato}` : (dataAutos || `Imputação ${idx + 1}`);
    const referencia = item.fonte_id ? `${etiqueta} · ${item.fonte_id}` : "";
    return {
      ...inicial, ...anterior,
      id_imputacao: item.id_imputacao || "",
      data_fato_autos: dataAutos,
      referencia,
    };
  });
  if (selecionada) {
    const index = state.crimeConfigs.findIndex((c) => identidadeCrime(c) === identidadeCrime(selecionada));
    state.selectedCrimeIdx = Math.max(0, index);
  }
  if (!mesmoCaso) {
    state.selectedCrimeIdx = 0;
    state.decisaoCautelar = salvas?.extras?.decisaoCautelar || null;
    state.destacarPendentes = false;
    if ($("inputValorDanoMoral")) $("inputValorDanoMoral").value = "";
    if ($("inputDiasPrisaoCautelar")) $("inputDiasPrisaoCautelar").value = "";
    if ($("selFracaoConcurso")) $("selFracaoConcurso").value = "";
    if ($("chkReuMenor21")) $("chkReuMenor21").checked = false;
    if ($("chkReuMaior70")) $("chkReuMaior70").checked = false;
    if (salvas && salvas.extras) {
      const ex = salvas.extras;
      if ($("inputValorDanoMoral")) $("inputValorDanoMoral").value = ex.valor || "";
      if ($("inputDiasPrisaoCautelar")) $("inputDiasPrisaoCautelar").value = ex.dias || "";
      if ($("selFracaoConcurso")) $("selFracaoConcurso").value = ex.fracao || "";
      if ($("chkReuMenor21")) $("chkReuMenor21").checked = !!ex.menor21;
      if ($("chkReuMaior70")) $("chkReuMaior70").checked = !!ex.maior70;
    }
  }
  atualizarHintDanoMoral();
  agendarSalvarDecisoes();
}

function atualizarHintDanoMoral() {
  const hint = $("hintDanoMoral");
  if (!hint) return;
  const meta = state.activeCase?.metadados || {};
  const valor = Number(meta.valor_dano_moral_pedido_mp || 0);
  if (!state.activeCase || state.activeCase.empty) hint.textContent = "";
  else if (!meta.pedido_dano_moral_expresso_mp) hint.textContent = "Pedido do MP não identificado nos autos.";
  else if (valor > 0) hint.textContent = `Pedido do MP: ${fmtMoeda(valor)}.`;
  else hint.textContent = "MP pediu sem indicar valor: arbitramento pendente se o campo ficar vazio.";
}

function tipoEfetivoCrime(cfg) {
  return cfg.veredicto === "desclassificar" && cfg.crime_desclassificado ? cfg.crime_desclassificado : cfg.codigo;
}

function dataDoFatoConfig(cfg) {
  const repetido = state.crimeConfigs.filter((c) => c.codigo === cfg.codigo).length > 1;
  const datasDistintas = new Set(state.crimeConfigs.map((c) => c.data_fato_autos).filter(Boolean)).size > 1;
  if (cfg.data_fato_informada) return cfg.data_fato_informada;
  if (repetido || datasDistintas) return cfg.data_fato_autos || "";
  return state.activeCase?.metadados?.data_fato || cfg.data_fato_autos || "";
}

function rotuloImputacao(cfg) {
  return cfg.referencia ? `${cfg.nome} · ${cfg.referencia}` : cfg.nome;
}

function ameacaExigeConfirmacao(cfg) {
  return tipoEfetivoCrime(cfg) === "art_147_cp" && dataDoFatoConfig(cfg) >= "2024-10-10";
}

function crimeIncompleto(cfg) {
  if (cfg.id_imputacao && !dataDoFatoConfig(cfg)) return "data do fato não informada";
  if (!cfg.veredicto) return "veredicto não informado";
  if (cfg.veredicto === "absolver" && !cfg.fundamento_absolvicao_art386) return "inciso do art. 386 do CPP não informado";
  if (cfg.veredicto === "extinguir" && !cfg.fundamento_extincao_art107) return "causa do art. 107 do CP não informada";
  if (cfg.veredicto === "desclassificar" && !cfg.crime_desclassificado) return "tipo penal resultante não informado";
  if (["condenar", "desclassificar"].includes(cfg.veredicto) && ameacaExigeConfirmacao(cfg) && !cfg.enquadramento_ameaca) return "confirme caput ou § 1º do art. 147";
  const majorante = cfg.majorante_art24a_p4;
  if (tipoEfetivoCrime(cfg) === "art_24a_lmp" && majorante?.hipotese && (!majorante.fracao || !majorante.fonte_id?.trim() || !majorante.trecho?.trim())) {
    return "complete a fração e a fonte do art. 24-A, § 4º";
  }
  return "";
}

/** Caso real: todo crime precisa de decisão completa do juízo antes de gerar a sentença. */
function garantirDecisaoJudicial(retry) {
  const c = state.activeCase;
  if (!c || c.empty) {
    showToast("Carregue os autos (ou o Caso Demo) antes de gerar a sentença.", "warn");
    return false;
  }
  if (c.is_demo) return true; // demonstração: a simulação é sinalizada na minuta
  if (!state.crimeConfigs.length) {
    hooks.abrirComplementacao(["crimes_imputados"], "Nenhum crime imputado foi extraído da denúncia.", retry);
    return false;
  }
  const pendentes = state.crimeConfigs.filter((cfg) => crimeIncompleto(cfg));
  if (pendentes.length) {
    exigirVeredictos(
      pendentes.map(identidadeCrime),
      "A sentença de caso real exige a decisão do juízo para cada crime: " +
        pendentes.map((p) => `${rotuloImputacao(p)} (${crimeIncompleto(p)})`).join("; ") +
        "."
    );
    return false;
  }
  return true;
}

function exigirVeredictos(codigos, mensagem) {
  const isDemo = !!state.activeCase?.is_demo;
  (codigos || []).forEach((code) => {
    if (codigosCatalogo().includes(code) && !state.crimeConfigs.some((c) => c.codigo === code)) {
      state.crimeConfigs.push(novoCrimeConfig(code, isDemo));
    }
  });
  state.destacarPendentes = true;
  const idx = state.crimeConfigs.findIndex((c) => (codigos || []).includes(identidadeCrime(c)) || (codigos || []).includes(c.codigo));
  if (idx >= 0) state.selectedCrimeIdx = idx;
  setActiveStation("dosimetria");
  activateSubtab("sub-dos-calculo");
  renderDosimetryCrimeTabsAndEditor();
  showToast(mensagem || "Informe o veredicto de cada crime antes de gerar a sentença.", "warn");
}

function renderVeredictoBanner() {
  const banner = $("dosimetryVeredictoBanner");
  if (!banner) return;
  const c = state.activeCase;
  if (!c || c.empty || !state.crimeConfigs.length) {
    banner.hidden = true;
    return;
  }
  const pendentes = state.crimeConfigs.filter((cfg) => crimeIncompleto(cfg));
  if (!pendentes.length) {
    banner.hidden = true;
    return;
  }
  banner.hidden = false;
  banner.className = `alert-banner ${c.is_demo ? "alert-info" : "alert-warn"}`;
  const lista = pendentes.map((p) => `${rotuloImputacao(p)} — ${crimeIncompleto(p)}`);
  if (state.migracaoImputacaoPendente) {
    lista.unshift("As decisões antigas por artigo não identificam o fato. Confirme as imputações separadamente.");
  }
  $("dosimetryVeredictoBannerText").innerHTML = c.is_demo
    ? `<strong>Caso demonstrativo:</strong> crimes sem decisão são calculados com condenação <strong>presumida</strong> (simulação sinalizada na minuta).<ul class="alert-list">${lista
        .map((l) => `<li>${escapeHtml(l)}</li>`)
        .join("")}</ul>`
    : `<strong>Decisão do juízo pendente.</strong> A geração da sentença exige o veredicto de cada crime; até lá, a simulação abaixo presume condenação apenas para cálculo.<ul class="alert-list">${lista
        .map((l) => `<li>${escapeHtml(l)}</li>`)
        .join("")}</ul>`;
}

const simularAposEdicao = debounce(() => simulateDosimetry(true), 700);


function renderDosimetryCrimeTabsAndEditor() {
  const tabsEl = $("dosimetryCrimeTabs");
  const edEl = $("dosimetryCrimeEditor");
  if (!tabsEl || !edEl) return;
  renderVeredictoBanner();

  if (!state.crimeConfigs.length) {
    tabsEl.innerHTML = "";
    const c = state.activeCase;
    edEl.innerHTML =
      !c || c.empty
        ? `<p class="muted">Carregue um processo para configurar a dosimetria por delito.</p>`
        : `<div class="alert-banner alert-warn"><div>Nenhum crime imputado foi extraído dos autos. Informe os crimes da denúncia para prosseguir.</div><div class="alert-actions"><button type="button" class="btn btn-sm btn-primary" data-action="abrir-metadados" data-campos="crimes_imputados">Informar crimes imputados</button></div></div>`;
    return;
  }
  if (state.selectedCrimeIdx >= state.crimeConfigs.length) state.selectedCrimeIdx = 0;

  tabsEl.innerHTML = state.crimeConfigs
    .map((cfg, idx) => {
      const incompleto = crimeIncompleto(cfg);
      const verdictLabel = cfg.veredicto ? VEREDICTO_LABELS[cfg.veredicto] || cfg.veredicto : "Decisão pendente";
      return `
        <button type="button" class="crime-tab-pill ${idx === state.selectedCrimeIdx ? "active" : ""} ${incompleto ? "pendente" : ""}" data-crime-tab="${idx}" title="${escapeHtml(incompleto || "Decisão informada")}">
          ${escapeHtml(rotuloImputacao(cfg))} &middot; <span style="opacity:.85">${escapeHtml(verdictLabel)}</span>
        </button>`;
    })
    .join("");

  tabsEl.querySelectorAll("[data-crime-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.selectedCrimeIdx = Number(btn.dataset.crimeTab);
      renderDosimetryCrimeTabsAndEditor();
    });
  });

  const cfg = state.crimeConfigs[state.selectedCrimeIdx];
  const calcMatch = (state.dosimetry?.crimes_calculados || []).find(
    (c) => cfg.id_imputacao ? c.id_imputacao === cfg.id_imputacao :
      (c.crime_spec?.code || c.crime_spec?.codigo) === cfg.codigo || c.desclassificado_de === cfg.codigo
  );
  const destacar = state.destacarPendentes;
  const condenatorio = cfg.veredicto === "condenar" || cfg.veredicto === "desclassificar";
  const codigoEfetivo = tipoEfetivoCrime(cfg);
  const oferecerMajorante24a = codigoEfetivo === "art_24a_lmp" && dataDoFatoConfig(cfg) >= "2026-04-10";
  const majorante = cfg.majorante_art24a_p4 || {};

  const vetoresHtml = VETORES_59_META.map((v) => {
    const checked = !!cfg.vetores[v.key];
    const customFund = cfg.vetores_fundamentacao?.[v.key] || "";
    const semFund = checked && !customFund.trim() && v.key !== "comportamento_vitima";
    return `
      <div class="vetor-row">
        <label class="vetor-check-label">
          <input type="checkbox" data-vetor-check="${v.key}" ${checked ? "checked" : ""} />
          <span>${escapeHtml(v.label)}</span>
        </label>
        <input type="text" class="form-control ${semFund ? "campo-pendente" : ""}" data-vetor-fund="${v.key}" value="${escapeHtml(customFund)}" placeholder="${
          checked
            ? "Fundamentação concreta obrigatória (sem ela, a minuta recebe [COMPLETAR PELO JUÍZO])"
            : "Fundamentação opcional deste vetor"
        }" />
      </div>`;
  }).join("");

  let resultado;
  if (cfg.veredicto === "absolver") resultado = "Absolvição — sem pena";
  else if (cfg.veredicto === "extinguir") resultado = "Extinção da punibilidade";
  else if (calcMatch) resultado = fmtPena(calcMatch.pena_definitiva) + (cfg.veredicto ? "" : " (simulação)");
  else resultado = "A calcular";

  const obrigatorio = (vazio) => (vazio && destacar ? "campo-pendente" : "");

  edEl.innerHTML = `
    <div class="inner-tile ${!cfg.veredicto ? "veredicto-obrigatorio" : ""}" style="margin-bottom:12px;">
      ${cfg.id_imputacao ? `
        <p class="muted">${escapeHtml(rotuloImputacao(cfg))}</p>
        <label class="form-label" for="inputImputacaoDataFato">Data do fato desta imputação</label>
        <input type="date" id="inputImputacaoDataFato" class="form-control ${obrigatorio(!dataDoFatoConfig(cfg))}"
          value="${escapeHtml(dataDoFatoConfig(cfg))}" />
      ` : ""}
      <div class="grid-3" style="align-items:end;">
        <div>
          <label class="form-label" for="selCrimeVeredicto">Decisão do juízo para este delito (obrigatória)</label>
          <select class="form-select ${obrigatorio(!cfg.veredicto)}" id="selCrimeVeredicto">
            <option value="" ${!cfg.veredicto ? "selected" : ""}>— Selecione a decisão —</option>
            <option value="condenar" ${cfg.veredicto === "condenar" ? "selected" : ""}>Condenar (Procedente)</option>
            <option value="absolver" ${cfg.veredicto === "absolver" ? "selected" : ""}>Absolver (Art. 386 CPP)</option>
            <option value="desclassificar" ${cfg.veredicto === "desclassificar" ? "selected" : ""}>Desclassificar (Art. 383 CPP)</option>
            <option value="extinguir" ${cfg.veredicto === "extinguir" ? "selected" : ""}>Extinguir Punibilidade (Art. 107 CP)</option>
          </select>
        </div>
        <div ${cfg.veredicto === "absolver" ? "" : "hidden"}>
          <label class="form-label" for="selFundArt386">Fundamento legal (Art. 386 CPP)</label>
          <select class="form-select ${obrigatorio(!cfg.fundamento_absolvicao_art386)}" id="selFundArt386">
            <option value="">— Selecione o inciso —</option>
            ${ART386_OPTIONS.map((o) => `<option value="${escapeHtml(o.value)}" ${cfg.fundamento_absolvicao_art386 === o.value ? "selected" : ""}>${escapeHtml(o.label)}</option>`).join("")}
          </select>
        </div>
        <div ${cfg.veredicto === "extinguir" ? "" : "hidden"}>
          <label class="form-label" for="selFundArt107">Causa extintiva (Art. 107 CP)</label>
          <select class="form-select ${obrigatorio(!cfg.fundamento_extincao_art107)}" id="selFundArt107">
            <option value="">— Selecione a causa —</option>
            ${ART107_OPTIONS.map((o) => `<option value="${escapeHtml(o.value)}" ${cfg.fundamento_extincao_art107 === o.value ? "selected" : ""}>${escapeHtml(o.label)}</option>`).join("")}
          </select>
        </div>
        <div ${cfg.veredicto === "desclassificar" ? "" : "hidden"}>
          <label class="form-label" for="selCrimeDesclass">Tipo penal resultante</label>
          <select class="form-select ${obrigatorio(!cfg.crime_desclassificado)}" id="selCrimeDesclass">
            <option value="">— Selecione o tipo penal —</option>
            ${codigosCatalogo()
              .filter((k) => k !== cfg.codigo)
              .map((k) => `<option value="${escapeHtml(k)}" ${cfg.crime_desclassificado === k ? "selected" : ""}>${escapeHtml(rotuloCrime(k))}</option>`)
              .join("")}
          </select>
        </div>
        <div>
          <div class="kpi-label">Resultado calculado</div>
          <div class="mono" style="font-weight:700; margin-top:4px;">${escapeHtml(resultado)}</div>
        </div>
      </div>
      <div ${cfg.veredicto === "absolver" || cfg.veredicto === "extinguir" ? "" : "hidden"} style="margin-top:10px;">
        <label class="form-label" for="inputCrimeFundamentacao">Fundamentação do juízo (opcional; sem ela a minuta indica o trecho a completar)</label>
        <textarea class="form-textarea" id="inputCrimeFundamentacao" rows="3">${escapeHtml(cfg.fundamentacao || "")}</textarea>
      </div>
    </div>

    ${camposConclusoes(cfg, tipoEfetivoCrime(cfg))}
    ${camposCautelar()}
    <div ${condenatorio ? "" : "hidden"}>
      ${ameacaExigeConfirmacao(cfg) ? `
        <div>
          <label class="form-label" for="selCrimeAmeaca">Enquadramento da ameaça (art. 147 do CP)</label>
          <select class="form-select ${obrigatorio(!cfg.enquadramento_ameaca)}" id="selCrimeAmeaca">
            <option value="">A confirmar</option>
            <option value="caput" ${cfg.enquadramento_ameaca === "caput" ? "selected" : ""}>Caput — ação condicionada à representação</option>
            <option value="paragrafo_1" ${cfg.enquadramento_ameaca === "paragrafo_1" ? "selected" : ""}>§ 1º — ameaça por razões da condição do sexo feminino</option>
          </select>
          ${state.activeCase?.metadados?.vinculo_domestico ? '<p class="muted">Sugestão a confirmar: § 1º, se comprovado o contexto de violência doméstica e familiar contra a mulher.</p>' : ''}
        </div>` : ""}
      <h3 style="font-size:13px; margin:0 0 8px;">1ª Fase — 8 Circunstâncias Judiciais (Art. 59 do CP)</h3>
      ${vetoresHtml}

      <h3 style="font-size:13px; margin:14px 0 8px;">2ª e 3ª Fases — Agravantes, Atenuantes, Causas Especiais e Multa</h3>
      <div class="grid-4" style="align-items:end;">
        <div>
          <label class="form-label" for="selCrimeConfissao">Confissão (Art. 65, III, 'd')</label>
          <select class="form-select" id="selCrimeConfissao">
            <option value="auto" ${cfg.confissao === "auto" ? "selected" : ""}>Conforme os autos (detecção automática)</option>
            <option value="sim" ${cfg.confissao === "sim" ? "selected" : ""}>Sim — reconhecer</option>
            <option value="nao" ${cfg.confissao === "nao" ? "selected" : ""}>Não reconhecer</option>
          </select>
        </div>
        <label class="switch-inline"><input type="checkbox" id="chkCrimeReincidente" ${cfg.reincidente ? "checked" : ""} /><span>Reincidência (Art. 61, I)</span></label>
        <label class="switch-inline"><input type="checkbox" id="chkCrimeArt61f" ${cfg.agravante_art61f ? "checked" : ""} /><span>Agravante Art. 61, II, 'f'</span></label>
        <div>
          <label class="form-label" for="selCrimeTentativa">3ª Fase — Tentativa (Art. 14 CP)</label>
          <select class="form-select" id="selCrimeTentativa">
            ${TENTATIVA_OPTIONS.map((o) => `<option value="${escapeHtml(o.value)}" ${(cfg.tentativa_fracao || "") === o.value ? "selected" : ""}>${escapeHtml(o.label)}</option>`).join("")}
          </select>
        </div>
        <div>
          <label class="form-label" for="inputCrimeMulta">Dias-multa (0 = sugestão do motor, com alerta)</label>
          <input class="form-control mono" type="number" id="inputCrimeMulta" min="0" value="${Number(cfg.dias_multa || 0)}" />
        </div>
        <label class="switch-inline" ${codigoEfetivo === "art_147a_cp" ? "" : "hidden"} title="Art. 147-A, § 1º, II, CP (contra mulher por razões da condição de sexo feminino)">
          <input type="checkbox" id="chkCrime147aAumento" ${cfg.aplicar_aumento_art147a_p1_ii ? "checked" : ""} />
          <span>Aumento do art. 147-A, § 1º, II (metade)</span>
        </label>
      </div>
      ${oferecerMajorante24a ? `
        <div class="grid-4">
          <label class="form-label">Art. 24-A, § 4º — hipótese
            <select class="form-select" data-majorante-24a="hipotese">
              <option value="">Sem reconhecimento informado</option>
              <option value="area_exclusao" ${majorante.hipotese === "area_exclusao" ? "selected" : ""}>Violação da área de exclusão monitorada</option>
              <option value="dispositivo_monitoracao" ${majorante.hipotese === "dispositivo_monitoracao" ? "selected" : ""}>Intervenção não autorizada no dispositivo</option>
            </select>
          </label>
          <label class="form-label">Fração de aumento (1/3 a 1/2)
            <input class="form-control" data-majorante-24a="fracao" value="${escapeHtml(majorante.fracao || "")}" />
          </label>
          <label class="form-label">ID da peça
            <input class="form-control" data-majorante-24a="fonte_id" value="${escapeHtml(majorante.fonte_id || "")}" />
          </label>
          <label class="form-label">Trecho literal da peça
            <input class="form-control" data-majorante-24a="trecho" value="${escapeHtml(majorante.trecho || "")}" />
          </label>
        </div>` : ""}
    </div>
    <p class="muted" style="font-size:12px; margin:8px 0 0;" ${condenatorio ? "hidden" : ""}>${
      cfg.veredicto
        ? "Sem pena a dosar para este delito."
        : "Selecione a decisão para configurar a dosimetria deste delito. Enquanto não houver decisão, a simulação presume condenação com os parâmetros padrão do motor."
    }</p>
  `;

  ligarRegistroProbatorio(edEl, cfg);
  const aoMudar = (rerender) => {
    if (rerender) renderDosimetryCrimeTabsAndEditor();
    else renderVeredictoBanner();
    simularAposEdicao();
  };

  $("inputImputacaoDataFato")?.addEventListener("change", (e) => {
    cfg.data_fato_informada = e.target.value;
    aoMudar(true);
  });

  $("selCrimeVeredicto")?.addEventListener("change", (e) => {
    cfg.veredicto = e.target.value;
    aoMudar(true);
  });
  $("selFundArt386")?.addEventListener("change", (e) => {
    cfg.fundamento_absolvicao_art386 = e.target.value;
    aoMudar(true);
  });
  $("selFundArt107")?.addEventListener("change", (e) => {
    cfg.fundamento_extincao_art107 = e.target.value;
    aoMudar(true);
  });
  $("selCrimeDesclass")?.addEventListener("change", (e) => {
    cfg.crime_desclassificado = e.target.value;
    aoMudar(true);
  });
  $("inputCrimeFundamentacao")?.addEventListener("input", (e) => {
    cfg.fundamentacao = e.target.value;
  });
  edEl.querySelectorAll("[data-vetor-check]").forEach((cb) => {
    cb.addEventListener("change", (e) => {
      cfg.vetores[e.target.dataset.vetorCheck] = e.target.checked;
      aoMudar(true);
    });
  });
  edEl.querySelectorAll("[data-vetor-fund]").forEach((inp) => {
    inp.addEventListener("input", (e) => {
      cfg.vetores_fundamentacao[e.target.dataset.vetorFund] = e.target.value;
      e.target.classList.toggle("campo-pendente", !!cfg.vetores[e.target.dataset.vetorFund] && !e.target.value.trim());
    });
  });
  $("selCrimeAmeaca")?.addEventListener("change", (e) => {
    cfg.enquadramento_ameaca = e.target.value;
    aoMudar(false);
  });
  edEl.querySelectorAll("[data-majorante-24a]").forEach((campo) => {
    campo.addEventListener("change", (e) => {
      cfg.majorante_art24a_p4 = {
        ...cfg.majorante_art24a_p4,
        [e.target.dataset.majorante24a]: e.target.value,
      };
      aoMudar(false);
    });
  });
  $("selCrimeConfissao")?.addEventListener("change", (e) => {
    cfg.confissao = e.target.value;
    aoMudar(false);
  });
  $("chkCrimeReincidente")?.addEventListener("change", (e) => {
    cfg.reincidente = e.target.checked;
    aoMudar(false);
  });
  $("chkCrimeArt61f")?.addEventListener("change", (e) => {
    cfg.agravante_art61f = e.target.checked;
    aoMudar(false);
  });
  $("selCrimeTentativa")?.addEventListener("change", (e) => {
    cfg.tentativa_fracao = e.target.value;
    aoMudar(false);
  });
  $("inputCrimeMulta")?.addEventListener("input", (e) => {
    cfg.dias_multa = Math.max(0, parseInt(e.target.value || "0", 10) || 0);
    aoMudar(false);
  });
  $("chkCrime147aAumento")?.addEventListener("change", (e) => {
    cfg.aplicar_aumento_art147a_p1_ii = e.target.checked;
    aoMudar(false);
  });
}

function buildCrimesConfig() {
  return state.crimeConfigs.map((c) => {
    const item = { codigo: c.codigo };
    if (c.id_imputacao) item.id_imputacao = c.id_imputacao;
    if (c.data_fato_informada) item.data_fato = c.data_fato_informada;
    if (!c.veredicto) return item; // sem decisão: simulação presume condenação (sinalizada); geração real exige
    item.veredicto = c.veredicto;
    if (c.veredicto !== "extinguir") {
      const aceitos = elementosDoCrime(tipoEfetivoCrime(c));
      const registros = Object.values(c.elementos_provados || {}).filter(
        (r) => r.conclusao && Object.hasOwn(aceitos, r.elemento)
      );
      if (registros.length) item.elementos_provados = registros;
    }
    const fund = (c.fundamentacao || "").trim();
    if (c.veredicto === "absolver") {
      if (c.fundamento_absolvicao_art386) item.fundamento_absolvicao_art386 = c.fundamento_absolvicao_art386;
      if (fund) item.fundamentacao = fund;
      return item;
    }
    if (c.veredicto === "extinguir") {
      if (c.fundamento_extincao_art107) item.fundamento_extincao_art107 = c.fundamento_extincao_art107;
      if (fund) item.fundamentacao = fund;
      return item;
    }
    if (c.veredicto === "desclassificar") item.crime_desclassificado = c.crime_desclassificado || "";
    const vetores_art59 = {};
    VETORES_59_META.forEach((v) => {
      const f = (c.vetores_fundamentacao?.[v.key] || "").trim();
      vetores_art59[v.key] = { desfavoravel: !!c.vetores[v.key] };
      if (f) vetores_art59[v.key].fundamentacao = f;
    });
    item.vetores_art59 = vetores_art59;
    if (c.confissao === "sim") item.confissao = true;
    else if (c.confissao === "nao") item.confissao = false;
    item.reincidente = !!c.reincidente;
    item.agravante_art61f = !!c.agravante_art61f;
    item.tentativa_fracao = (c.tentativa_fracao || "").trim() || null;
    item.dias_multa = Number(c.dias_multa || 0);
    const efetivo = tipoEfetivoCrime(c);
    if (efetivo === "art_147_cp" && c.enquadramento_ameaca) item.enquadramento_ameaca = c.enquadramento_ameaca;
    if (efetivo === "art_24a_lmp" && c.majorante_art24a_p4?.hipotese) item.majorante_art24a_p4 = c.majorante_art24a_p4;
    if (efetivo === "art_147a_cp") item.aplicar_aumento_art147a_p1_ii = !!c.aplicar_aumento_art147a_p1_ii;
    return item;
  });
}

/** Somente o que o juízo informou; ausente = padrão correto do motor (sem valores inventados). */
function buildDosimetryPayload() {
  const p = { crimes_config: buildCrimesConfig() };
  if (state.decisaoCautelar?.decisao) p.decisao_cautelar = state.decisaoCautelar;
  const crit = $("selCriterio1Fase")?.value;
  if (crit) p.criterio_primeira_fase = crit;
  const tipo = $("selTipoConcurso")?.value;
  if (tipo) p.tipo_concurso = tipo;
  const fracao = $("selFracaoConcurso")?.value;
  if (fracao) p.concurso_fracao = fracao;
  const valor = Number($("inputValorDanoMoral")?.value || 0);
  if (Number.isFinite(valor) && valor > 0) p.valor_dano_moral = valor;
  const dataSent = $("inputDataSentenca")?.value;
  if (dataSent) p.data_sentenca = dataSent;
  const diasRaw = String($("inputDiasPrisaoCautelar")?.value || "").trim();
  if (diasRaw !== "") {
    const dias = parseInt(diasRaw, 10);
    if (Number.isFinite(dias) && dias >= 0) p.dias_prisao_cautelar = dias;
  }
  if ($("chkReuMenor21")?.checked) p.reu_menor_21_na_data_fato = true;
  if ($("chkReuMaior70")?.checked) p.reu_maior_70_na_sentenca = true;
  return p;
}

function renderDadosBanner() {
  const banner = $("dosimetryDadosBanner");
  if (!banner) return;
  const f = state.dadosFaltantes;
  if (!f) {
    banner.hidden = true;
    return;
  }
  banner.hidden = false;
  const campos = (f.campos || []).map((c) => META_CAMPOS_LABELS[c] || c);
  $("dosimetryDadosBannerText").innerHTML = `<strong>Dados insuficientes para a dosimetria:</strong> ${escapeHtml(
    campos.join(", ") || f.detail || "dados não extraídos dos autos"
  )}. Nenhum valor fictício é usado: informe os dados para prosseguir.`;
}

function renderDosimetryAlerts(res) {
  const card = $("dosimetryAlertsCard");
  if (!card) return;
  const d = res?.dosimetria || {};
  const p = res?.prescricao || {};
  const vistos = new Set();
  const itens = [];
  const add = (txt, tipo) => {
    const t = String(txt || "").trim();
    if (!t || vistos.has(t)) return;
    vistos.add(t);
    itens.push({ t, tipo });
  };
  (res?.alertas || []).forEach((a) => add(a, "warn"));
  (d.alertas_globais || []).forEach((a) => add(a, "warn"));
  (d.crimes_calculados || []).forEach((c) => {
    (c.alertas_juridicos || []).forEach((a) => {
      if (a && a !== c.crime_spec?.observacao_intertemporal) add(`${c.crime_spec?.nome || ""}: ${a}`, "info");
    });
    if (c.dias_multa_sugeridos_pelo_motor) add(`${c.crime_spec?.nome || ""}: ${c.dias_multa} dia(s)-multa SUGERIDOS pelo motor (confirme ou informe o quantum).`, "warn");
  });
  (p.alertas || []).forEach((a) => add(`Prescrição: ${a}`, "info"));
  const presumidos = (res?.decisoes_crimes || []).filter((x) => x.presumido);
  if (presumidos.length) {
    add(
      `Simulação: condenação PRESUMIDA para ${presumidos.map((x) => x.referencia_imputacao || rotuloCrime(x.crime_code)).join(", ")} (crime(s) sem veredicto). Não utilize como decisão.`,
      "danger"
    );
  }
  if (!itens.length) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  $("dosimetryAlertsContainer").innerHTML = `<ul class="alert-list">${itens
    .map((i) => `<li${i.tipo === "danger" ? ' style="color:var(--semantic-danger); font-weight:600;"' : ""}>${escapeHtml(i.t)}</li>`)
    .join("")}</ul>`;
}

function renderDosimetryResults(res) {
  state.lastSimulation = res;
  state.dosimetry = res.dosimetria;
  state.prescricao = res.prescricao;
  state.simulacaoPresume = !!res.simulacao_presume_condenacao;
  state.dadosFaltantes = null;
  renderDadosBanner();

  const d = res.dosimetria || {};
  const p = res.prescricao;
  const kpiRegime = $("kpiRegime");
  if (d.sem_condenacao) {
    $("kpiPenaTotal").textContent = "Sem condenação";
    kpiRegime.textContent = "Prejudicado";
    kpiRegime.title = "";
    $("kpiSursis").textContent = "Prejudicado";
    $("kpiDetracao").textContent = "0 dia(s)";
    $("kpiDanoMoral").textContent = "—";
  } else {
    $("kpiPenaTotal").textContent = (res.resumo_formatado?.pena_total_curto || fmtPena(d.pena_total_unificada)) + (state.simulacaoPresume ? " (simulação)" : "");
    const porEspecie = d.regime_por_especie || {};
    const especies = Object.keys(porEspecie);
    kpiRegime.textContent =
      String(d.regime_inicial || "—").toUpperCase() +
      (especies.length > 1 ? ` (${especies.map((e) => `${e}: ${porEspecie[e]}`).join("; ")})` : "");
    kpiRegime.title = d.fundamentacao_regime || "";
    $("kpiSursis").textContent = d.sursis_art77_cabivel
      ? "Cabível"
      : d.sursis_negado_por_circunstancias_judiciais
      ? "Negado (art. 77, II)"
      : "Incabível";
    $("kpiSursis").title = d.fundamentacao_sursis || "";
    $("kpiDetracao").textContent = `${Number(res.detracao_dias ?? d.detracao_dias ?? 0)} dia(s)`;
    $("kpiDetracao").title = res.detracao_periodo || "";
    if (d.dano_moral_arbitramento_pendente) $("kpiDanoMoral").textContent = "Arbitramento pendente";
    else if (!d.dano_moral_pedido_mp && !Number(d.indenizacao_dano_moral_tema983 || 0)) $("kpiDanoMoral").textContent = "Sem pedido";
    else $("kpiDanoMoral").textContent = fmtMoeda(d.indenizacao_dano_moral_tema983);
  }

  const hintFr = $("hintFracaoSugerida");
  if (hintFr) {
    const partes = [];
    if (res.fracao_concurso_sugerida) partes.push(`Sugerida: ${res.fracao_concurso_sugerida}`);
    if (res.fracao_concurso) partes.push(`Aplicada: ${res.fracao_concurso}`);
    hintFr.textContent = partes.join(" · ");
  }

  if (p) {
    const porCrime = p.por_crime || [];
    const incompleta = porCrime.some((item) => item.verificacao_completa === false);
    const semRecebimento = porCrime.some((item) => !item.data_recebimento_denuncia);
    $("prescricaoPanelContainer").innerHTML = `
      <div style="margin-bottom:8px;">
        <span class="badge-tag ${p.prescricao_reconhecida ? "badge-danger" : incompleta ? "badge-warn" : "badge-success"}">
          ${p.prescricao_reconhecida ? "PRESCRIÇÃO RECONHECIDA" : incompleta ? "VERIFICAÇÃO INCOMPLETA" : "NÃO PRESCRITO"}
        </span>
        ${semRecebimento ? '<span class="badge-tag badge-warn">Recebimento não informado para alguma imputação</span>' : ""}
      </div>
      ${porCrime
        .map(
          (pc) => `
        <div class="inner-tile" style="margin-bottom:8px; font-size:12.5px;">
          <div style="display:flex; justify-content:space-between; gap:8px; flex-wrap:wrap;">
            <strong>${escapeHtml(pc.crime_nome || rotuloCrime(pc.crime_code))} ${pc.referencia_imputacao ? "· " + escapeHtml(pc.referencia_imputacao) : ""} ${pc.artigo_formatado ? "(" + escapeHtml(pc.artigo_formatado) + ")" : ""}</strong>
            <span class="badge-tag ${pc.prescricao_reconhecida ? "badge-danger" : pc.verificacao_completa === false ? "badge-warn" : "badge-success"}">${
              pc.prescricao_reconhecida ? "PRESCRITO" : pc.verificacao_completa === false ? "VERIFICAÇÃO INCOMPLETA" : "NÃO PRESCRITO"
            }</span>
          </div>
          <div class="muted mono" style="font-size:11px; margin-top:3px;">Pena concreta ${escapeHtml(fmtPena(pc.pena_concreta))} · prazo ${escapeHtml(String(pc.prazo_concreto_anos ?? "—"))} ano(s)${pc.reduzido_metade_art115 ? " (reduzido à metade, art. 115)" : ""}</div>
          <div style="margin-top:4px;">${escapeHtml(pc.fundamentacao || "")}</div>
        </div>`
        )
        .join("")}
      <div style="font-size:12.5px;">${escapeHtml(p.fundamentacao || "")}</div>`;
  } else {
    $("prescricaoPanelContainer").innerHTML = `<p class="muted">Simule a dosimetria para ver a prescrição.</p>`;
  }

  const leisHtml = (d.crimes_calculados || [])
    .map(
      (c) => `
    <div class="inner-tile" style="margin-bottom:8px;">
      <strong>${escapeHtml(c.crime_spec?.nome || "")} (${escapeHtml(c.crime_spec?.artigo_formatado || "")})</strong>
      <div class="muted" style="font-size:12px;">${escapeHtml(c.crime_spec?.observacao_intertemporal || "Lei vigente à época dos fatos.")}</div>
    </div>`
    )
    .join("");
  $("intertemporalPanelContainer").innerHTML = leisHtml || `<p class="muted">Nenhuma pena calculada (absolvição/extinção).</p>`;

  renderDosimetryAlerts(res);
  renderDosimetryCrimeTabsAndEditor();
  updateHeroRibbon();
  renderCalcMemoryDrawer();
}

function limparDosimetria() {
  state.dosimetry = null;
  state.prescricao = null;
  state.lastSimulation = null;
  state.simulacaoPresume = false;
  ["kpiPenaTotal", "kpiRegime", "kpiSursis", "kpiDanoMoral"].forEach((id) => {
    if ($(id)) $(id).textContent = "—";
  });
  if ($("kpiDetracao")) $("kpiDetracao").textContent = "0 dia(s)";
  if ($("prescricaoPanelContainer")) $("prescricaoPanelContainer").innerHTML = `<p class="muted">Simule a dosimetria para ver a prescrição.</p>`;
  if ($("intertemporalPanelContainer")) $("intertemporalPanelContainer").innerHTML = "";
  if ($("dosimetryAlertsCard")) $("dosimetryAlertsCard").hidden = true;
  renderCalcMemoryDrawer();
}

async function simulateDosimetry(silent = false) {
  if (!state.activeCase || state.activeCase.empty) return;
  try {
    const res = await postJson("/api/dosimetria/simulate", buildDosimetryPayload());
    renderDosimetryResults(res);
    if (!silent) {
      showToast(
        res.simulacao_presume_condenacao
          ? "Dosimetria recalculada (simulação com condenação presumida para crimes sem veredicto)."
          : "Dosimetria recalculada.",
        res.simulacao_presume_condenacao ? "warn" : "success"
      );
    }
  } catch (err) {
    if (err.body && err.body.erro === "dados_insuficientes") {
      limparDosimetria();
      state.dadosFaltantes = { campos: err.body.campos || [], detail: err.body.detail || "" };
      renderDadosBanner();
      renderDosimetryCrimeTabsAndEditor();
      updateHeroRibbon();
      if (!silent) handleApiError(err, { retry: () => simulateDosimetry(false) });
      return;
    }
    if (!silent) handleApiError(err);
    else if (err.status === 401) handleApiError(err, { silent: true });
  }
}

function renderCalcMemoryDrawer() {
  const d = state.dosimetry;
  if (!d) {
    $("calcMemoryContent").innerHTML = "Aguardando cálculo.";
    return;
  }
  if (d.sem_condenacao) {
    $("calcMemoryContent").innerHTML = "Absolvição integral ou extinção da punibilidade: sem memória de cálculo condenatória.";
    return;
  }
  const lines = (d.crimes_calculados || []).map((c) => {
    return `<div style="margin-bottom:6px;"><strong>${escapeHtml(c.crime_spec?.nome || "")}</strong>: Base ${escapeHtml(fmtPena(c.pena_base))} &rarr; Interm. ${escapeHtml(
      fmtPena(c.pena_intermediaria)
    )} &rarr; Def. <strong>${escapeHtml(fmtPena(c.pena_definitiva))}</strong></div>`;
  });
  const revisaoP9 = (d.crimes_calculados || []).some((c) =>
    c.crime_spec?.code === "art_129_p9_cp" && c.crime_spec?.especie_pena === "detencao" &&
    (c.data_fato || state.activeCase?.metadados?.data_fato || "") >= "2024-10-10"
  );
  if (revisaoP9) lines.unshift('<p class="campo-pendente">Cálculo anterior do art. 129, § 9º: revisar pela Lei nº 14.994/2024.</p>');
  if (d.fundamentacao_regime) lines.push(`<div class="muted" style="margin-top:6px;">Regime: ${escapeHtml(d.fundamentacao_regime)}</div>`);
  if (state.simulacaoPresume) lines.push(`<div style="margin-top:6px; color:var(--semantic-danger); font-weight:600;">Simulação: condenação presumida para crime(s) sem veredicto.</div>`);
  $("calcMemoryContent").innerHTML = lines.join("") || "Sem crimes calculados.";
}

function bindDosimetriaEvents() {
  $("btnSimulateDosimetry")?.addEventListener("click", () => simulateDosimetry(false));
  $("selCriterio1Fase")?.addEventListener("change", (e) => {
    e.target.dataset.alteradoPeloJuiz = "1";
  });
  $("btnGoToSentenceFromDos")?.addEventListener("click", () => setActiveStation("sentenca"));
  ["selCriterio1Fase", "selTipoConcurso", "selFracaoConcurso", "inputDataSentenca", "chkReuMenor21", "chkReuMaior70"].forEach((id) => {
    $(id)?.addEventListener("change", () => simularAposEdicao());
  });
  ["inputValorDanoMoral", "inputDiasPrisaoCautelar"].forEach((id) => {
    $(id)?.addEventListener("input", () => simularAposEdicao());
  });
  $("btnDadosBannerComplementar")?.addEventListener("click", () => {
    hooks.abrirComplementacao((state.dadosFaltantes || {}).campos || [], (state.dadosFaltantes || {}).detail || "", () => simulateDosimetry(false));
  });
}

export {
  CHAVE_DECISOES_SESSAO,
  agendarSalvarDecisoes,
  atualizarHintDanoMoral,
  bindDosimetriaEvents,
  buildCrimesConfig,
  buildDosimetryPayload,
  chaveCaso,
  crimeIncompleto,
  exigirVeredictos,
  garantirDecisaoJudicial,
  initCrimeConfigsFromCase,
  lerDecisoesSessao,
  limparDosimetria,
  novoCrimeConfig,
  renderCalcMemoryDrawer,
  renderDadosBanner,
  renderDosimetryAlerts,
  renderDosimetryCrimeTabsAndEditor,
  renderDosimetryResults,
  renderVeredictoBanner,
  salvarDecisoesSessao,
  salvarDecisoesTimer,
  simularAposEdicao,
  simulateDosimetry,
};
