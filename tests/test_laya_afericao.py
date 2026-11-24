"""Aferição determinística com autos e nomes inteiramente fictícios."""

import json
from collections import Counter
from pathlib import Path

from app.ingestion.laya_classifier import classificar_pecas_autos, extrair_metadados_processo


CORPUS = Path(__file__).parent / "fixtures" / "laya_afericao"


def aferir():
    pecas = json.loads((CORPUS / "pecas.json").read_text(encoding="utf-8"))
    nomes = json.loads((CORPUS / "nomes.json").read_text(encoding="utf-8"))
    matriz = Counter()
    sondas = {}
    for item in pecas:
        doc = {"doc_id": "ID-AFERICAO", "titulo": item["titulo"], "conteudo": item["conteudo"]}
        resultado = classificar_pecas_autos([doc])[0]
        matriz[item["esperado"], resultado.categoria] += 1
        if item.get("sonda"):
            sondas[item["id"]] = (item["esperado"], resultado.categoria)
    exatos = []
    for item in nomes:
        meta = extrair_metadados_processo(classificar_pecas_autos(item["docs"]))
        exatos.append((meta.nome_reu == item["reu"], meta.nome_ofendida == item["vit"]))
    return matriz, exatos, sondas


def resumo(matriz, exatos, sondas):
    categorias = sorted({referencia for referencia, _ in matriz})
    linhas = ["Categoria esperada | Acertos/total | Categorias produzidas"]
    for categoria in categorias:
        contagens = {prevista: n for (real, prevista), n in matriz.items() if real == categoria}
        distribuicao = ", ".join(f"{prevista} {n}" for prevista, n in sorted(contagens.items()))
        linhas.append(f"{categoria} | {contagens.get(categoria, 0)}/{sum(contagens.values())} | {distribuicao}")
    acertos = sum(n for (real, prevista), n in matriz.items() if real == prevista)
    total = sum(matriz.values())
    linhas.append(f"Geral: {acertos}/{total} ({100 * acertos / total:.2f}%)")
    reu, vit = map(sum, zip(*exatos))
    ambos = sum(a and b for a, b in exatos)
    linhas.append(f"Nomes exatos: réu {reu}/{len(exatos)}, ofendida {vit}/{len(exatos)}, ambos {ambos}/{len(exatos)}")
    linhas.append("Sonda: " + ", ".join(f"{id_}={prevista}" for id_, (_, prevista) in sondas.items()))
    return "\n".join(linhas)


def test_corpus_laya_tem_cobertura_e_exibe_matriz(pytestconfig):
    matriz, exatos, sondas = aferir()
    if pytestconfig.getoption("verbose") > 0:
        pytestconfig.pluginmanager.get_plugin("terminalreporter").write_line("\n" + resumo(matriz, exatos, sondas))
    assert sum(matriz.values()) >= 80
    assert len(exatos) >= 20
    assert len(sondas) >= 10


def test_classificacao_e_nomes_respeitam_os_limites_do_corpus():
    matriz, exatos, sondas = aferir()
    acertos = sum(n for (real, prevista), n in matriz.items() if real == prevista)
    assert acertos / sum(matriz.values()) >= 0.95
    por_categoria = Counter()
    for (real, _), n in matriz.items():
        por_categoria[real] += n
    for real, total in por_categoria.items():
        assert matriz[real, real] / total >= 0.90, real
    assert sum(a and b for a, b in exatos) / len(exatos) >= 0.95
    assert all(prevista in (esperada, "NAO_IDENTIFICADA") for esperada, prevista in sondas.values())


def test_sentenca_anterior_e_peticao_nao_geram_fatos_ou_pedidos():
    pecas = classificar_pecas_autos([
        {"doc_id": "ID-DEN", "titulo": "Denúncia", "conteudo": "O Ministério Público oferece denúncia pelo art. 147 do Código Penal."},
        {"doc_id": "ID-ANTERIOR", "titulo": "Sentença", "conteudo": "O fato ocorreu em 10/01/2026. A defesa requer absolvição. O MP pede condenação e reparação."},
        {"doc_id": "ID-JUNTADA", "titulo": "Petição", "conteudo": "A defesa requer absolvição e argui inépcia da denúncia."},
    ])
    meta = extrair_metadados_processo(pecas)
    assert meta.data_fato == ""
    assert meta.teses_defesa == meta.pedidos_defesa == meta.pedidos_mp == []


def test_empate_e_pontuacao_baixa_geram_alertas_com_id():
    pecas = classificar_pecas_autos([
        {"doc_id": "ID-AMBIGUA", "titulo": "Memoriais", "conteudo": "O Promotor de Justiça requer condenação. A defesa requer absolvição."},
        {"doc_id": "ID-BAIXA", "titulo": "", "conteudo": "O Ministério Público oferece denúncia."},
    ])
    assert pecas[0].categoria == "NAO_IDENTIFICADA"
    assert set(pecas[0].laya_candidatas) == {"ALEGACOES_FINAIS_MP", "ALEGACOES_FINAIS_DEFESA"}
    avisos = extrair_metadados_processo(pecas).alertas_extracao
    assert any("ID-AMBIGUA" in aviso and "Candidatas" in aviso for aviso in avisos)
    assert any("ID-BAIXA" in aviso and "pontuação baixa" in aviso for aviso in avisos)


if __name__ == "__main__":
    print(resumo(*aferir()))
