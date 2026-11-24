import { escapeHtml, rotuloCrime } from "./dom.js?v=2.2.0";

const ROTULO_PEDIDO = {
  condenacao_total: "Condenação total",
  condenacao_parcial: "Condenação parcial",
  absolvicao: "Absolvição",
  desclassificacao: "Desclassificação",
  dano_moral: "Dano moral",
  agravante_qualificadora: "Agravante ou qualificadora",
  atenuante: "Atenuante",
  regime: "Regime inicial",
  outro: "Outro pedido",
};
const TESE_HONRA = /leg[ií]tima\s+defesa\s+da\s+honra/i;

function teseHonra(pedido) {
  return TESE_HONRA.test([pedido.descricao, pedido.titulo, pedido.descricao_defesa, pedido.trecho].join(" "));
}

export function renderPedidos(c, meta, trechoBotao, rotuloFonte) {
  const grupos = new Map();
  for (const [parte, pedidos] of [["mp", meta.pedidos_mp || []], ["defesa", meta.pedidos_defesa || []]]) {
    for (const pedido of pedidos) {
      const chave = pedido.id_imputacao || pedido.numero_fato || pedido.crime || "";
      const fato = pedido.numero_fato ? "Fato " + pedido.numero_fato : "";
      const crime = pedido.crime ? rotuloCrime(pedido.crime) : "";
      if (!grupos.has(chave)) grupos.set(chave, { rotulo: [fato, crime].filter(Boolean).join(" · "), mp: [], defesa: [] });
      grupos.get(chave)[parte].push(pedido);
    }
  }

  const honraEmPedido = (meta.pedidos_defesa || []).some(teseHonra);
  const tese = (meta.teses_defesa || []).find(teseHonra);
  const peca = (c.pecas || []).find((p) =>
    ["RESPOSTA_ACUSACAO", "ALEGACOES_FINAIS_DEFESA"].includes(p.categoria) && TESE_HONRA.test(p.conteudo || ""));
  const alerta = '<strong class="pd-honra">Tese vedada (ADPF 779/STF): repudiar expressamente</strong>';
  const alertaGeral = !honraEmPedido && (tese || peca)
    ? '<div class="pd-alerta-honra">' + alerta + ' ' +
      trechoBotao(tese?.fonte_id ? tese : { fonte_id: peca?.doc_id, folha: peca?.folha, trecho: peca?.conteudo?.match(TESE_HONRA)?.[0] }) + '</div>'
    : "";
  const coluna = (pedidos, defesa) => {
    if (!pedidos.length) return '<p class="muted">Sem pedido identificado.</p>';
    const agrupados = new Map();
    for (const pedido of pedidos) {
      const chave = JSON.stringify([pedido.tipo, pedido.natureza, pedido.descricao]);
      if (!agrupados.has(chave)) agrupados.set(chave, []);
      agrupados.get(chave).push(pedido);
    }
    return '<ul class="pd-pedidos">' + [...agrupados.values()].map((ocorrencias) => {
      const pedido = ocorrencias[0];
      const vedada = defesa && teseHonra(pedido);
      const tipo = ROTULO_PEDIDO[pedido.tipo] || String(pedido.tipo || "Pedido").replaceAll("_", " ");
      const natureza = pedido.natureza === "subsidiario" ? " · Subsidiário" : "";
      const fontes = ocorrencias.map((item) =>
        '<div class="pd-fonte">' + escapeHtml(rotuloFonte(item)) + trechoBotao(item) + '</div>'
      ).join("");
      const ocorrenciasHtml = ocorrencias.length === 1 ? fontes :
        '<details><summary>' + ocorrencias.length + ' ocorrências</summary>' + fontes + '</details>';
      return '<li' + (vedada ? ' class="pd-alerta-honra"' : '') + '><span class="pd-tipo">' +
        escapeHtml(tipo + natureza) + '</span><p>' + escapeHtml(pedido.descricao) + '</p>' +
        (vedada ? alerta : "") + ocorrenciasHtml + '</li>';
    }).join("") + '</ul>';
  };
  const linhas = [...grupos].sort(([a], [b]) => Number(!a) - Number(!b)).map(([, partes]) =>
    '<tr><th scope="rowgroup" colspan="2">' + escapeHtml(partes.rotulo || "Pedidos sem imputação individualizada") +
    '</th></tr><tr><td>' + coluna(partes.mp, false) + '</td><td>' + coluna(partes.defesa, true) + '</td></tr>');
  return alertaGeral + '<table class="pd-quadro" aria-label="MP pede × Defesa pede"><thead><tr>' +
    '<th scope="col">MP pede</th><th scope="col">Defesa pede</th></tr></thead>' +
    (linhas.length ? linhas.map((linha) => "<tbody>" + linha + "</tbody>").join("") :
      '<tbody><tr><td colspan="2">Nenhum pedido identificado nas alegações finais.</td></tr></tbody>') +
    '</table>';
}
