"""TASK-050. Casos fictícios para a extração, sem decisões inferidas pelo teste."""

from app.ingestion.laya_classifier import classificar_pecas_autos, extrair_metadados_processo


_DENUNCIA = (
    "O Ministério Público oferece denúncia. Acusado: TICIO FICTICIO, brasileiro.\n"
    "Fato 1 — No dia 10/03/2024, o acusado teria praticado ameaça (art. 147 do CP).\n"
    "2º Fato — No dia 21/03/2024, o acusado teria praticado outra ameaça (art. 147 do CP).\n"
    "Dos pedidos:\nO Ministério Público requer análise do art. 147 do CP."
)


def _peca(doc_id, titulo, conteudo, *, data="", folha="fl. 1"):
    return {"doc_id": doc_id, "titulo": titulo, "conteudo": conteudo, "data_peca": data, "folha": folha}


def _denuncia(texto=_DENUNCIA):
    return _peca("ID-DEN-FICTICIA", "Denúncia Ministerial", texto, folha="fl. 2")


def _julgado(doc_id, texto, data):
    return _peca(doc_id, "Decisão judicial", texto, data=data, folha="fl. 3")


def _meta(*raw):
    return extrair_metadados_processo(classificar_pecas_autos(list(raw)))


def test_duas_ameacas_no_mesmo_artigo_tem_id_data_e_origem_proprios():
    meta = _meta(_denuncia())
    assert meta.crimes_imputados == ["art_147_cp"]
    assert len(meta.imputacoes) == 2
    primeiro, segundo = meta.imputacoes
    assert primeiro.id_imputacao != segundo.id_imputacao
    assert [i.data_fato for i in meta.imputacoes] == ["2024-03-10", "2024-03-21"]
    for indice, item in enumerate(meta.imputacoes, start=1):
        assert item.numero_fato == str(indice)
        assert item.estado == "extraida"
        assert item.codigo == "art_147_cp"
        assert item.fonte_id == "ID-DEN-FICTICIA" and item.folha == "fl. 2"
        assert item.pessoa_acusada == "TICIO FICTICIO"
        assert "Dos pedidos" not in item.fato
    reimportado = _meta(_denuncia(_DENUNCIA.replace("o acusado", "o  acusado")))
    assert [i.id_imputacao for i in reimportado.imputacoes] == [i.id_imputacao for i in meta.imputacoes]


def test_recebimento_e_rejeicao_parciais_na_mesma_decisao_ficam_no_fato_certo():
    rec = _julgado(
        "ID-REC-FICTICIA",
        "Recebo parcialmente a denúncia somente quanto ao Fato 2; rejeito a denúncia quanto ao primeiro fato.",
        "2024-04-12",
    )
    meta = _meta(_denuncia(), rec)
    primeiro, segundo = meta.imputacoes
    assert [e.tipo for e in primeiro.eventos] == ["rejeicao"]
    assert [e.tipo for e in segundo.eventos] == ["recebimento"]
    assert segundo.eventos[0].data_decisao == "2024-04-12"
    assert meta.data_recebimento_denuncia == "2024-04-12"
    assert segundo.eventos[0].fonte_id == "ID-REC-FICTICIA"
    assert not meta.eventos_imputacao_pendentes


def test_aditamento_reutiliza_numero_sem_substituir_a_imputacao_da_denuncia():
    aditamento = _peca(
        "ID-ADIT-FICTICIO", "Aditamento à denúncia",
        "Fato 1: Em 30/04/2024, o acusado teria praticado ameaça, art. 147, caput.",
    )
    recepcao_aditamento = _julgado(
        "ID-ADM-ADIT", "Recebo o aditamento ID-ADIT-FICTICIO quanto ao Fato 1.", "2024-05-06",
    )
    ato_ambiguo = _julgado(
        "ID-DEC-AMBIGUA", "Declaro extinta a punibilidade quanto ao Fato 1.", "2024-07-02",
    )
    meta = _meta(_denuncia(), aditamento, recepcao_aditamento, ato_ambiguo)
    primeiro, segundo, adicional = meta.imputacoes
    assert adicional.origem == "aditamento" and adicional.fonte_id == "ID-ADIT-FICTICIO"
    assert adicional.numero_fato == primeiro.numero_fato
    assert adicional.id_imputacao != primeiro.id_imputacao
    assert adicional.data_fato == "2024-04-30"
    assert [e.fonte_id for e in adicional.eventos] == ["ID-ADM-ADIT"]
    assert not primeiro.eventos and not segundo.eventos
    assert [e.fonte_id for e in meta.eventos_imputacao_pendentes] == ["ID-DEC-AMBIGUA"]


def test_extincao_por_fato_nao_remove_todos_os_itens_do_mesmo_codigo():
    rec = _julgado("ID-REC-FICTICIA", "Recebo a denúncia.", "2024-04-12")
    ext = _julgado("ID-EXT-FICTICIA", "Declaro extinta a punibilidade quanto ao Fato 2.", "2024-07-21")
    meta = _meta(_denuncia(), rec, ext)
    primeiro, segundo = meta.imputacoes
    assert [e.tipo for e in primeiro.eventos] == ["recebimento"]
    assert [e.tipo for e in segundo.eventos] == ["recebimento", "extincao"]
    assert meta.crimes_imputados == ["art_147_cp"]


def test_imputacao_nominal_fica_como_sugestao_sem_entrar_nos_tipos_confirmados():
    den = _denuncia("Fato 1: No dia 10/03/2024, o acusado teria praticado lesão corporal.")
    meta = _meta(den)
    assert meta.crimes_imputados == []
    assert len(meta.imputacoes) == 1
    item = meta.imputacoes[0]
    assert (item.codigo, item.estado) == ("art_129_p13_cp", "sugestao_a_confirmar")
    assert item.data_fato == "2024-03-10"
    assert any("referência nominal" in alerta for alerta in meta.alertas_extracao)


def test_ato_por_artigo_repetido_aguarda_confirmacao_em_vez_de_selecionar_um_fato():
    ext = _julgado(
        "ID-EXT-FICTICIA", "Declaro extinta a punibilidade quanto ao delito do art. 147 do CP.", "2024-07-21",
    )
    meta = _meta(_denuncia(), ext)
    assert all(not item.eventos for item in meta.imputacoes)
    assert [e.tipo for e in meta.eventos_imputacao_pendentes] == ["extincao"]
    assert meta.crimes_imputados == ["art_147_cp"]


def test_dois_paragrafos_datados_sem_numeracao_mantem_imputacoes_distintas():
    den = _denuncia(
        "O Ministério Público oferece denúncia. Acusado: TICIO FICTICIO, brasileiro.\n"
        "No dia 10/03/2024, ameaça do art. 147 do CP.\n"
        "Em 21 de março de 2024, ameaça do art. 147 do CP."
    )
    meta = _meta(den)
    assert len(meta.imputacoes) == 2
    assert [i.data_fato for i in meta.imputacoes] == ["2024-03-10", "2024-03-21"]


def test_referencia_a_peca_ausente_nao_transfere_recebimento_para_denuncia_local():
    rec = _julgado("ID-REC-FICTICIA", "Recebo a denúncia ID-ACUSACAO-AUSENTE.", "2024-04-12")
    meta = _meta(_denuncia(), rec)
    assert all(not item.eventos for item in meta.imputacoes)
    assert [e.fonte_id for e in meta.eventos_imputacao_pendentes] == ["ID-REC-FICTICIA"]


def test_recebimento_limitado_na_frase_seguinte_nao_se_estende_a_todos_os_fatos():
    rec = _julgado("ID-REC-FICTICIA", "Recebo a denúncia. Somente quanto ao Fato 1.", "2024-04-12")
    meta = _meta(_denuncia(), rec)
    assert [e.tipo for e in meta.imputacoes[0].eventos] == ["recebimento"]
    assert not meta.imputacoes[1].eventos
    assert meta.data_recebimento_denuncia == ""


def test_extincao_vinculada_pela_data_exata_do_fato():
    ext = _julgado(
        "ID-EXT-FICTICIA", "Declaro extinta a punibilidade quanto ao fato de 21/03/2024.", "2024-07-21",
    )
    meta = _meta(_denuncia(), ext)
    assert not meta.imputacoes[0].eventos
    assert [e.tipo for e in meta.imputacoes[1].eventos] == ["extincao"]


def test_data_do_ato_na_frase_de_recebimento_nao_vira_data_do_fato():
    rec = _julgado(
        "ID-REC-FICTICIA",
        "Recebo a denúncia em 12/04/2024 quanto ao Fato 2.",
        "2024-04-12",
    )
    meta = _meta(_denuncia(), rec)
    assert not meta.imputacoes[0].eventos
    assert [e.tipo for e in meta.imputacoes[1].eventos] == ["recebimento"]
    assert not meta.eventos_imputacao_pendentes


def test_pdf_preserva_aditamento_e_ato_parcial_ate_os_metadados(tmp_path):
    import pymupdf
    from app.ingestion.pdf_ingestor import segmentar_autos_pdf_pje

    destino = tmp_path / "autos-ficticios.pdf"
    paginas = [
        ("Denúncia Ministerial", "9900001", _DENUNCIA),
        ("Aditamento à denúncia", "9900002",
         "Aditamento à denúncia.\nFato 3: Em 30/04/2024, o acusado teria praticado ameaça, art. 147 do CP."),
        ("Decisão", "9900003", "Recebo parcialmente o aditamento quanto ao Fato 3."),
    ]
    with pymupdf.open() as doc:
        toc = []
        for indice, (titulo, id_, corpo) in enumerate(paginas, start=1):
            pagina = doc.new_page()
            pagina.insert_textbox(pymupdf.Rect(40, 40, 550, 780), corpo, fontsize=10)
            toc.append([1, f"{titulo} | NUM: {id_} | 06/05/2024", indice])
        doc.set_toc(toc)
        doc.save(str(destino))

    meta = _meta(*segmentar_autos_pdf_pje(destino, usar_cache=False))
    extraidas = {i.numero_fato: i for i in meta.imputacoes}
    assert set(extraidas) == {"1", "2", "3"}
    assert extraidas["3"].origem == "aditamento"
    assert extraidas["3"].fonte_id == "ID-9900002"
    assert [e.fonte_id for e in extraidas["3"].eventos] == ["ID-9900003"]
    assert not extraidas["1"].eventos and not extraidas["2"].eventos
