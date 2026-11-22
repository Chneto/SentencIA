"""Painel de revisão (conferência pelo art. 381/manual + pendências do laudo) e pasta de exportação."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.export.destino import copiar_exportacao, validar_pasta_exportacao
from app.generation.revisor_estilo import montar_revisao, revisar_estilo

TEXTO_OK = (
    "TRIBUNAL\n\nSENTENÇA\n\nI – RELATÓRIO\n\n"
    "O Ministério Público ofereceu denúncia em face de JOÃO DA SILVA. A denúncia foi recebida em 10 de março de 2025. "
    "Citado, o réu apresentou resposta. Em alegações finais, a defesa pediu a absolvição.\n\n"
    "II – FUNDAMENTAÇÃO\n\nNos termos do art. 155 do CPP, a prova é suficiente.\n\n"
    "III – DISPOSITIVO\n\nÀ vista das expostas razões, julgo procedente o pedido para CONDENAR o réu, art. 129, § 9º, do CP.\n\n"
    "Publique-se. Registre-se. Intimem-se.\n\nCOMARCA, 20 de novembro de 2026.\n\nJuiz(a) de Direito"
)


def _tipos(itens):
    return {i["tipo"] for i in itens}


class TestRevisorEstilo:
    def test_texto_conforme_nao_tem_itens_obrigatorios(self):
        itens = revisar_estilo(TEXTO_OK, reu="JOÃO DA SILVA")
        assert not [i for i in itens if i["severidade"] == "obrigatorio"], itens

    def test_secao_ausente_e_ordem(self):
        sem_disp = TEXTO_OK.replace("III – DISPOSITIVO", "NADA")
        assert "falta_dispositivo" in _tipos(revisar_estilo(sem_disp))
        invertido = "I – RELATÓRIO\n\nx\n\nIII – DISPOSITIVO\n\ny\n\nII – FUNDAMENTAÇÃO\n\nz"
        assert "ordem_secoes" in _tipos(revisar_estilo(invertido))

    def test_autenticacao_e_reu_no_relatorio(self):
        assert "autenticacao" in _tipos(revisar_estilo(TEXTO_OK.split("COMARCA,")[0]))
        assert "reu_no_relatorio" in _tipos(revisar_estilo(TEXTO_OK, reu="MARIA DE TAL"))

    def test_recomendacoes_do_relatorio(self):
        pobre = "I – RELATÓRIO\n\nVistos.\n\nII – FUNDAMENTAÇÃO\n\nart. 1\n\nIII – DISPOSITIVO\n\nart. 2\n\nCOMARCA, 1 de maio de 2026.\nJuiz"
        t = _tipos(revisar_estilo(pobre))
        assert {"recebimento_denuncia", "citacao", "pedidos_finais"} <= t

    def test_expressoes_do_manual_com_sugestao(self):
        txt = TEXTO_OK.replace("À vista das expostas razões,", "Ante o exposto, e por tudo mais que dos autos consta,").replace(
            "julgo procedente o pedido", "JULGO PROCEDENTE A AÇÃO"
        )
        itens = {i["tipo"]: i for i in revisar_estilo(txt)}
        assert itens["tudo_mais_autos"]["trecho"] in txt and itens["tudo_mais_autos"]["sugestao"].startswith("À vista")
        assert itens["procedente_acao"]["sugestao"] == "PROCEDENTE O PEDIDO"
        assert "Manual" in itens["tudo_mais_autos"]["base"]

    def test_condenacao_sem_artigos_e_dosimetria_na_fundamentacao(self):
        txt = TEXTO_OK.replace(", art. 129, § 9º, do CP", "").replace(
            "a prova é suficiente.", "a prova é suficiente. 3. Da Dosimetria da Pena: pena-base fixada."
        )
        t = _tipos(revisar_estilo(txt))
        assert "dispositivo_sem_artigos" in t and "dosimetria_na_fundamentacao" in t

    def test_forma(self):
        t = _tipos(revisar_estilo(TEXTO_OK.replace("o réu", "o  réu", 1).replace("a prova", "a prova prova")))
        assert "espacos_duplos" in t and "palavra_repetida" in t


class TestPendenciasDoLaudo:
    def test_marcadores_e_achados_do_laudo(self):
        aud = SimpleNamespace(ids_inexistentes=["ID-999"], citacoes_nao_autorizadas=["Súmula 1/XYZ"], teses_omitidas=["MER-02"],
                              penas_divergentes=["art. 147: 1 ano x 2 anos"], secoes_fallback=["dosimetria"])
        sent = SimpleNamespace(alertas=["Simulação"], reu="X")
        texto = TEXTO_OK + "\n\n[COMPLETAR PELO JUÍZO: data da audiência]"
        r = montar_revisao(texto, reu="JOÃO DA SILVA", auditoria=aud, sentenca=sent)
        t = _tipos(r["itens"])
        assert {"marcador", "id_inexistente", "citacao_nao_autorizada", "tese_omitida", "pena_divergente", "secao_fallback", "alerta"} <= t
        assert r["resumo"]["obrigatorios"] >= 5
        assert r["itens"][0]["severidade"] == "obrigatorio"  # ordenado por gravidade
        ids = [i["id"] for i in r["itens"]]
        assert len(ids) == len(set(ids))  # chaves estáveis e únicas para o checklist


class TestPastaDeExportacao:
    def test_valida_cria_e_avisa_nuvem(self, tmp_path: Path):
        pasta, avisos = validar_pasta_exportacao(str(tmp_path / "saida"), bloqueadas=[tmp_path / "data"])
        assert pasta.is_dir() and avisos == []
        _, avisos = validar_pasta_exportacao(str(tmp_path / "OneDrive" / "Sentenças"), bloqueadas=[])
        assert avisos and "nuvem" in avisos[0]

    def test_recusa_relativa_raiz_e_pasta_do_programa(self, tmp_path: Path):
        with pytest.raises(ValueError):
            validar_pasta_exportacao("saida/relativa", bloqueadas=[])
        with pytest.raises(ValueError):
            validar_pasta_exportacao(str(Path(tmp_path.anchor)), bloqueadas=[])
        dados = tmp_path / "data"
        dados.mkdir()
        with pytest.raises(ValueError):
            validar_pasta_exportacao(str(dados / "exports"), bloqueadas=[dados])
        with pytest.raises(ValueError):
            validar_pasta_exportacao("", bloqueadas=[])

    def test_copia_por_processo_sem_sobrescrever_conteudo_diferente(self, tmp_path: Path):
        origem = tmp_path / "Sentença - X.docx"
        origem.write_bytes(b"versao 1")
        destino = copiar_exportacao(origem, str(tmp_path / "saida"), "0804590-12.2025.8.00.0001")
        assert destino.parent.name == "Proc. 0804590-12.2025.8.00.0001" and destino.read_bytes() == b"versao 1"
        # mesmo conteúdo: reaproveita
        assert copiar_exportacao(origem, str(tmp_path / "saida"), "0804590-12.2025.8.00.0001") == destino
        # usuário editou o arquivo no Word; nova exportação não pode destruir a edição dele
        destino.write_bytes(b"editado pelo usuario")
        origem.write_bytes(b"versao 2")
        novo = copiar_exportacao(origem, str(tmp_path / "saida"), "0804590-12.2025.8.00.0001")
        assert novo != destino and novo.name.endswith("(2).docx")
        assert destino.read_bytes() == b"editado pelo usuario" and novo.read_bytes() == b"versao 2"

    def test_sem_subpasta_e_numero_malicioso(self, tmp_path: Path):
        origem = tmp_path / "a.docx"
        origem.write_bytes(b"x")
        d = copiar_exportacao(origem, str(tmp_path / "s"), "../../x", por_processo=True)
        assert (tmp_path / "s") in d.parents and ".." not in str(d.relative_to(tmp_path))
        assert copiar_exportacao(origem, str(tmp_path / "s2"), "1", por_processo=False).parent == tmp_path / "s2"
