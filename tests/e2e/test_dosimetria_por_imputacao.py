"""TASK-051. Decisões independentes para dois fatos fictícios no mesmo tipo penal."""

import base64

import pymupdf

from app.generation.hallucination_auditor import auditar_sentenca
from tests.e2e.test_layout_transbordo import _LocalAppServer, _launch_chromium


def _autos_ficticios(tmp_path, tipo_penal="ameaça, art. 147", certidao="", termo="", custodia=False):
    destino = tmp_path / "duas-imputacoes-ficticias.pdf"
    pecas = [
        ("Capa e Autuação", "9900101", "Processo nº 0001234-56.2024.8.09.0001."),
        (
            "Denúncia Ministerial", "9900102",
            "Processo nº 0001234-56.2024.8.09.0001. O Ministério Público oferece denúncia.\n"
            "Acusado: TICIO FICTICIO, brasileiro. Ofendida: MEVIA FICTICIA, brasileira.\n"
            f"Fato 1: No dia 10/03/2024, o acusado teria praticado {tipo_penal} do Código Penal.\n"
            f"Fato 2: No dia 21/03/2024, o acusado teria praticado outra {tipo_penal} do Código Penal.",
        ),
        (
            "Decisão de Recebimento da Denúncia", "9900103",
            "Decisão judicial. Recebo a denúncia quanto aos fatos 1 e 2. Cite-se a defesa. Cumpra-se.",
        ),
    ]
    if certidao:
        pecas.append(("Certidão de Antecedentes Criminais", "9900104", certidao))
    if termo:
        pecas.append(("Termo de Audiência de Instrução", "9900105", termo))
    if custodia:
        pecas.extend([
            ("Petição Inicial", "9900106", "Auto de Prisão em Flagrante. O acusado foi preso em flagrante em 28/05/2024."),
            ("Termo de Audiência", "9900107", "Audiência de Custódia. O termo registra que o acusado permanece preso em 30/05/2024."),
        ])
    with pymupdf.open() as pdf:
        toc = []
        for numero, (titulo, id_, conteudo) in enumerate(pecas, 1):
            pagina = pdf.new_page()
            pagina.insert_textbox(pymupdf.Rect(40, 40, 550, 780), conteudo, fontsize=10)
            toc.append([1, f"{titulo} | NUM: {id_} | 12/04/2024", numero])
        pdf.set_toc(toc)
        pdf.save(str(destino))
    return base64.b64encode(destino.read_bytes()).decode("ascii")


def test_dois_fatos_do_mesmo_artigo_da_tela_ate_a_minuta(_live_server: _LocalAppServer, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")
            page.wait_for_timeout(2500)  # gravação agrupada do rascunho anterior (TASK-092) antes da injeção
            ids = page.evaluate(
                """async (pdf) => {
                    const { apiJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                    const { chaveCaso, CHAVE_DECISOES_SESSAO } = await import('/static/js/modulos/estacao-dosimetria.js?v=2.4.0');
                    const bytes = Uint8Array.from(atob(pdf), (c) => c.charCodeAt(0));
                    const form = new FormData();
                    form.append("files", new Blob([bytes], {type: "application/pdf"}), "autos-ficticios.pdf");
                    await apiJson('/api/cases/upload-pdfs', {method: "POST", body: form});
                    const caso = await apiJson('/api/cases/active');
                    sessionStorage.setItem(CHAVE_DECISOES_SESSAO, JSON.stringify({
                      caseKey: chaveCaso(caso),
                      crimeConfigs: [{ codigo: "art_147_cp", veredicto: "condenar" }],
                    }));
                    return caso.metadados.imputacoes.map((i) => i.id_imputacao);
                }""",
                _autos_ficticios(tmp_path),
            )
            assert len(ids) == 2 and ids[0] != ids[1]
            page.reload(wait_until="networkidle")
            page.click('.station-btn[data-station="dosimetria"]')
            abas = page.locator("#dosimetryCrimeTabs [data-crime-tab]")
            abas.nth(1).wait_for()
            assert abas.count() == 2
            assert "Fato 1" in abas.nth(0).inner_text()
            assert "Fato 2" in abas.nth(1).inner_text()
            assert "decisões antigas" in page.locator("#dosimetryVeredictoBannerText").inner_text()
            assert page.input_value("#selCrimeVeredicto") == ""

            page.select_option("#selCrimeVeredicto", "condenar")
            abas.nth(1).click()
            assert page.input_value("#selCrimeVeredicto") == ""
            page.select_option("#selCrimeVeredicto", "absolver")
            page.select_option("#selFundArt386", "VII")

            resultado = page.evaluate(
                """async () => {
                    const { apiFetch, postJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                    const { buildDosimetryPayload } = await import('/static/js/modulos/estacao-dosimetria.js?v=2.4.0');
                    const legado = await apiFetch('/api/dosimetria/simulate', {
                      method: 'POST', headers: {'Content-Type': 'application/json'},
                      body: JSON.stringify({crimes_config: [{codigo: "art_147_cp", veredicto: "condenar"}]})
                    });
                    const payload = {...buildDosimetryPayload(), data_sentenca: "2024-05-30", use_ollama: false};
                    const simulacao = await postJson('/api/dosimetria/simulate', payload);
                    const geracao = await postJson('/api/sentence/generate', payload);
                    return {payload, simulacao, geracao, legado: await legado.json()};
                }"""
            )
        finally:
            browser.close()

    assert resultado["legado"]["erro"] == "decisao_judicial_pendente"
    assert resultado["legado"]["crimes_pendentes"] == ids
    assert [i["id_imputacao"] for i in resultado["payload"]["crimes_config"]] == ids
    assert all("reincidente" not in item for item in resultado["payload"]["crimes_config"])
    calculo = resultado["simulacao"]["dosimetria"]["crimes_calculados"]
    assert len(calculo) == 1 and calculo[0]["id_imputacao"] == ids[0]
    prescricao = resultado["simulacao"]["prescricao"]["por_crime"]
    assert prescricao[0]["id_imputacao"] == ids[0]
    assert prescricao[0]["data_fato"] == "2024-03-10"
    assert prescricao[0]["data_recebimento_denuncia"] == "2024-04-12"
    sentenca = resultado["geracao"]["sentenca"]
    assert [(d["id_imputacao"], d["veredicto"]) for d in sentenca["decisoes_crimes"]] == [
        (ids[0], "condenar"), (ids[1], "absolver"),
    ]
    dispositivo = sentenca["secao_dispositivo"].split("\n\n")
    assert any("CONDENAR o réu" in p and "Fato 1" in p for p in dispositivo)
    assert any("ABSOLVER o réu" in p and "Fato 2" in p for p in dispositivo)

    trocado = sentenca["secao_dispositivo"].replace("Fato 1", "<reserva>").replace("Fato 2", "Fato 1").replace("<reserva>", "Fato 2")
    laudo = auditar_sentenca(trocado, [], [], dosimetria_dados=sentenca["dosimetria_dados"])
    assert any("incompatível com a imputação" in d for d in laudo.penas_divergentes)


def test_arbitramentos_e_majorante_da_tela_ate_a_api(_live_server: _LocalAppServer, tmp_path):
    """Lei 14.132/2021, vigente em 01/04/2021, e art. 49 do CP.
    https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14132.htm
    https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm#art49
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")
            # O caso ativo deixado por outro teste agenda a gravação do rascunho (agrupada em ~1,5 s, TASK-092);
            # espera-se que ela ocorra antes de injetar o rascunho de sessão, senão a gravação ao sair o sobrescreve.
            page.wait_for_timeout(2500)
            page.evaluate(
                """async (pdf) => {
                    const { apiJson, postJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                    const { chaveCaso, CHAVE_DECISOES_SESSAO } = await import('/static/js/modulos/estacao-dosimetria.js?v=2.4.0');
                    const bytes = Uint8Array.from(atob(pdf), (c) => c.charCodeAt(0));
                    const form = new FormData();
                    form.append("files", new Blob([bytes], {type: "application/pdf"}), "autos-ficticios.pdf");
                    await apiJson('/api/cases/upload-pdfs', {method: "POST", body: form});
                    await postJson('/api/cases/metadata-override', {metadados: {
                        pedido_dano_moral_expresso_mp: true, valor_dano_moral_pedido_mp: 2500
                    }});
                    const caso = await apiJson('/api/cases/active');
                    const imputacao = caso.metadados.imputacoes.find((item) => item.codigo === "art_147a_cp");
                    sessionStorage.setItem(CHAVE_DECISOES_SESSAO, JSON.stringify({
                      caseKey: chaveCaso(caso),
                      crimeConfigs: [{
                        id_imputacao: imputacao.id_imputacao, codigo: "art_147a_cp",
                        veredicto: "condenar", aplicar_aumento_art147a_p1_ii: true
                      }],
                    }));
                }""",
                _autos_ficticios(tmp_path, "perseguição, art. 147-A"),
            )
            page.reload(wait_until="networkidle")
            page.click('.station-btn[data-station="dosimetria"]')
            majorante = page.locator("#chkCrime147aAumento")
            majorante.wait_for()
            assert not majorante.is_checked()
            page.locator("#inputValorDanoMoral").fill("0")
            page.locator("#inputCrimeMulta").fill("30")
            page.locator("#inputCrimeMultaUnidade").fill("1/15")
            payload_inicial = page.evaluate(
                """async () => {
                    const { buildDosimetryPayload } = await import('/static/js/modulos/estacao-dosimetria.js?v=2.4.0');
                    return buildDosimetryPayload();
                }"""
            )
            assert payload_inicial["crimes_config"][0]["aplicar_aumento_art147a_p1_ii"] is False
            majorante.check()
            resultado = page.evaluate(
                """async () => {
                    const { postJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                    const { buildDosimetryPayload } = await import('/static/js/modulos/estacao-dosimetria.js?v=2.4.0');
                    const payload = buildDosimetryPayload();
                    const simulacao = await postJson('/api/dosimetria/simulate', payload);
                    return {payload, dosimetria: simulacao.dosimetria};
                }"""
            )
        finally:
            browser.close()

    payload = resultado["payload"]
    assert payload["valor_dano_moral"] == 0
    crime = payload["crimes_config"][0]
    assert crime["aplicar_aumento_art147a_p1_ii"] is True
    assert crime["dias_multa"] == 30
    assert crime["valor_unitario_multa"] == "1/15"
    dosimetria = resultado["dosimetria"]
    assert dosimetria["valor_dano_moral_pedido"] == 2500
    assert dosimetria["valor_dano_moral_decidido"] == 0
    calculo = dosimetria["crimes_calculados"][0]
    assert calculo["aumento_art147a_aplicado"] is True
    assert calculo["dias_multa"] == 30
    assert calculo["valor_unitario_multa"] == "1/15"


def test_registros_da_acao_ficam_na_imputacao_selecionada(_live_server: _LocalAppServer, tmp_path):
    """Arts. 16 e 16-A da LMP. Leis 15.380/2026 (07/04/2026) e 15.438/2026 (19/06/2026).
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2026/lei/l15380.htm
    https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2026/lei/l15438.htm
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")
            page.evaluate(
                """async (pdf) => {
                    const { apiJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                    const bytes = Uint8Array.from(atob(pdf), (c) => c.charCodeAt(0));
                    const form = new FormData();
                    form.append("files", new Blob([bytes], {type: "application/pdf"}), "autos-ficticios.pdf");
                    await apiJson('/api/cases/upload-pdfs', {method: "POST", body: form});
                }""",
                _autos_ficticios(tmp_path),
            )
            page.reload(wait_until="networkidle")
            page.click('.station-btn[data-station="dosimetria"]')
            abas = page.locator("#dosimetryCrimeTabs [data-crime-tab]")
            abas.nth(1).wait_for()
            page.locator("[data-registro-condicoes] summary").click()
            page.select_option('[data-condicao-acao="violencia_sexual_contra_mulher"]', "nao")
            page.select_option('[data-condicao-acao="violencia_domestica_familiar_contra_mulher"]', "sim")
            page.fill('[data-condicao-acao="data_ciencia_autoria"]', "2024-03-10")
            page.select_option('[data-condicao-acao="direito_exercido"]', "sim")
            page.fill('[data-condicao-acao="data_exercicio_direito"]', "2024-03-11")
            page.select_option('[data-condicao-acao="pedido_expresso_retratacao"]', "nao")

            abas.nth(1).click()
            page.locator("[data-registro-condicoes] summary").click()
            assert page.input_value('[data-condicao-acao="data_ciencia_autoria"]') == ""
            assert page.input_value('[data-condicao-acao="pedido_expresso_retratacao"]') == ""

            resultados = page.evaluate(
                """async () => {
                    const { postJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                    const { buildDosimetryPayload } = await import('/static/js/modulos/estacao-dosimetria.js?v=2.4.0');
                    const payload = {...buildDosimetryPayload(), data_sentenca: "2024-05-30"};
                    const sim = await postJson('/api/dosimetria/simulate', payload);
                    return {payload, decisoes: sim.decisoes_crimes};
                }"""
            )
        finally:
            browser.close()

    primeira, segunda = resultados["payload"]["crimes_config"]
    assert primeira["id_imputacao"] != segunda["id_imputacao"]
    assert primeira["violencia_sexual_contra_mulher"] is False
    assert primeira["condicoes_acao_penal"]["direito_exercido"] is True
    assert "condicoes_acao_penal" not in segunda
    decisoes = {d["id_imputacao"]: d["analise_acao_penal"] for d in resultados["decisoes"]}
    assert decisoes[primeira["id_imputacao"]]["decadencia"]["estado"] == "tempestivo"
    assert decisoes[primeira["id_imputacao"]]["retratacao"]["estado"] == "sem_pedido_expresso"
    assert decisoes[segunda["id_imputacao"]]["decadencia"]["estado"] == "pendente"


def test_marco_de_recebimento_na_tela_fica_na_imputacao_com_fonte(_live_server: _LocalAppServer, tmp_path):
    """CP, arts. 117, I, e 119, redação vigente desde a Lei 7.209/1984.
    https://www.planalto.gov.br/ccivil_03/leis/1980-1988/l7209.htm
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")
            fonte = page.evaluate(
                """async (pdf) => {
                    const { apiJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                    const bytes = Uint8Array.from(atob(pdf), (c) => c.charCodeAt(0));
                    const form = new FormData();
                    form.append("files", new Blob([bytes], {type: "application/pdf"}), "autos-ficticios.pdf");
                    await apiJson('/api/cases/upload-pdfs', {method: "POST", body: form});
                    const caso = await apiJson('/api/cases/active');
                    return caso.pecas.find(p => p.doc_id.endsWith("9900103")).doc_id;
                }""",
                _autos_ficticios(tmp_path),
            )
            page.reload(wait_until="networkidle")
            page.click('.station-btn[data-station="dosimetria"]')
            abas = page.locator("#dosimetryCrimeTabs [data-crime-tab]")
            abas.nth(1).wait_for()
            page.locator("[data-registro-prescricao] summary").click()
            page.locator('[data-presc-adicionar="interrupcoes"]').click()
            linha = page.locator('[data-presc-linha="interrupcoes"]')
            linha.locator('[data-presc-campo="tipo"]').select_option("recebimento_denuncia_ou_queixa")
            linha.locator('[data-presc-campo="data"]').fill("2024-04-12")
            linha.locator('[data-presc-campo="fonte_id"]').fill(fonte)
            abas.nth(1).click()
            assert not page.locator('[data-presc-linha="interrupcoes"]').count()
            resultado = page.evaluate(
                """async () => {
                    const { postJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                    const { buildDosimetryPayload } = await import('/static/js/modulos/estacao-dosimetria.js?v=2.4.0');
                    const payload = {...buildDosimetryPayload(), data_sentenca: "2024-05-30"};
                    const sim = await postJson('/api/dosimetria/simulate', payload);
                    return {payload, prescricao: sim.prescricao.por_crime};
                }"""
            )
        finally:
            browser.close()

    primeira, segunda = resultado["payload"]["crimes_config"]
    marco = primeira["linha_tempo_prescricao"]["interrupcoes"][0]
    assert marco == {"tipo": "recebimento_denuncia_ou_queixa", "data": "2024-04-12", "fonte_id": fonte}
    assert "linha_tempo_prescricao" not in segunda
    por_id = {item["id_imputacao"]: item for item in resultado["prescricao"]}
    assert por_id[primeira["id_imputacao"]]["cronologia"]["interrupcoes"][0] == marco
    assert por_id[primeira["id_imputacao"]]["estado"] == "sem_indicio"
    assert por_id[segunda["id_imputacao"]]["cronologia"]["interrupcoes"] == []



def test_antecedentes_da_tela_ate_o_calculo_por_imputacao(_live_server: _LocalAppServer, tmp_path):
    """CP, arts. 59, 61, I, 63 e 64 (Lei 7.209/1984, vigência 13/01/1985).
    https://www.planalto.gov.br/ccivil_03/leis/1980-1988/l7209.htm
    """
    from playwright.sync_api import sync_playwright

    from tests.robustez.test_antecedentes_por_registro import CERTIDAO

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")
            page.wait_for_timeout(2500)  # gravação agrupada do rascunho anterior (TASK-092) antes da injeção
            ids = page.evaluate(
                """async (pdf) => {
                    const { apiJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                    const bytes = Uint8Array.from(atob(pdf), c => c.charCodeAt(0));
                    const form = new FormData();
                    form.append('files', new Blob([bytes], {type: 'application/pdf'}), 'autos-antecedentes-ficticios.pdf');
                    await apiJson('/api/cases/upload-pdfs', {method: 'POST', body: form});
                    const caso = await apiJson('/api/cases/active');
                    return caso.metadados.registros_antecedentes;
                }""",
                _autos_ficticios(tmp_path, certidao=CERTIDAO),
            )
            assert len(ids) == 3
            assert ids[2]["pena_em_curso"] and ids[2]["data_transito"] == "2022-01-05"
            assert ids[0]["data_cumprimento_extincao"] == "2019-02-15"
            ids = [r["id_registro"] for r in ids]
            page.reload(wait_until="networkidle")
            page.click('.station-btn[data-station="dosimetria"]')
            abas = page.locator("#dosimetryCrimeTabs [data-crime-tab]")
            abas.nth(1).wait_for()
            page.select_option("#selCrimeVeredicto", "condenar")
            page.select_option(f"#uso-{ids[2]}", "reincidencia")
            assert page.locator(f'#uso-{ids[1]} option[value="reincidencia"]').count() == 0
            abas.nth(1).click()
            page.select_option("#selCrimeVeredicto", "condenar")
            assert page.input_value(f"#uso-{ids[2]}") == ""
            page.select_option(f"#uso-{ids[0]}", "antecedentes")
            saida = page.evaluate(
                """async () => {
                    const { postJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                    const { buildDosimetryPayload } = await import('/static/js/modulos/estacao-dosimetria.js?v=2.4.0');
                    const payload = {...buildDosimetryPayload(), data_sentenca: '2024-05-30'};
                    const calculo = await postJson('/api/dosimetria/simulate', payload);
                    return {payload, crimes: calculo.dosimetria.crimes_calculados};
                }"""
            )
        finally:
            browser.close()

    c1, c2 = saida["crimes"]
    assert c1["reincidente"] and "antecedentes" not in c1["vetores_negativos_efetivos"]
    assert not c2["reincidente"] and "antecedentes" in c2["vetores_negativos_efetivos"]
    assert c1["registros_valorados"] == {"reincidencia": [ids[2]]}
    assert c2["registros_valorados"] == {"antecedentes": [ids[0]]}
    assert all("reincidente" not in item for item in saida["payload"]["crimes_config"])


def test_confissao_confirmada_na_analise_altera_somente_a_imputacao_escolhida(_live_server: _LocalAppServer, tmp_path):
    """CP art. 65, III, d, Lei 7.209/1984, vigente em 13/01/1985.
    https://www.planalto.gov.br/ccivil_03/leis/1980-1988/l7209.htm
    """
    from playwright.sync_api import expect, sync_playwright

    termo = "Fato 1: O réu confessou que ameaçou a vítima.\nFato 2: O réu negou a ameaça."
    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")
            page.wait_for_timeout(2500)  # gravação agrupada do rascunho anterior (TASK-092) antes da injeção
            ids = page.evaluate(
                """async (pdf) => {
                    const { apiJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                    const form = new FormData();
                    const bytes = Uint8Array.from(atob(pdf), c => c.charCodeAt(0));
                    form.append('files', new Blob([bytes], {type: 'application/pdf'}), 'autos-confissao-ficticios.pdf');
                    await apiJson('/api/cases/upload-pdfs', {method: 'POST', body: form});
                    const caso = await apiJson('/api/cases/active');
                    return caso.metadados.imputacoes.map(i => i.id_imputacao);
                }""",
                _autos_ficticios(tmp_path, termo=termo),
            )
            assert len(ids) == 2
            page.reload(wait_until="networkidle")
            page.click('.station-btn[data-station="analise"]')
            primeiro = page.locator(f'[data-confissao-imputacao="{ids[0]}"]')
            segundo = page.locator(f'[data-confissao-imputacao="{ids[1]}"]')
            expect(primeiro).to_contain_text("Indício: Confissão integral")
            expect(segundo).to_contain_text("Indício: Negativa do fato")
            simular = """async (ids) => {
                const { postJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                const crimes_config = ids.map(id_imputacao => ({
                    id_imputacao, codigo: 'art_147_cp', veredicto: 'condenar',
                    vetores_art59: { culpabilidade: { desfavoravel: true, fundamentacao: 'Fundamento fictício.' } },
                }));
                const resposta = await postJson('/api/dosimetria/simulate', {crimes_config, data_sentenca: '2024-05-30'});
                return resposta.dosimetria.crimes_calculados.map(c => [c.pena_intermediaria, c.atenuantes_ativas]);
            }"""
            antes = page.evaluate(simular, ids)
            assert not any("confissão" in x for x in antes[0][1])

            primeiro.locator("select").select_option("total")
            with page.expect_response("**/api/cases/metadata-override") as confirmou:
                primeiro.get_by_role("button", name="Registrar decisão").click()
            assert confirmou.value.ok
            depois = page.evaluate(simular, ids)
            assert any("confissão" in x for x in depois[0][1]) and antes[0][0] != depois[0][0]
            assert depois[1] == antes[1]

            primeiro.locator("select").select_option("negativa")
            with page.expect_response("**/api/cases/metadata-override") as corrigiu:
                primeiro.get_by_role("button", name="Registrar decisão").click()
            assert corrigiu.value.ok
            assert page.evaluate(simular, ids) == antes
        finally:
            browser.close()


def test_intervalo_de_custodia_da_tela_usa_o_mesmo_total_e_periodo(_live_server: _LocalAppServer, tmp_path):
    """Lei 12.736/2012, vigente em 03/12/2012.
    https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2012/lei/l12736.htm
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page()
            page.goto(_live_server.base_url, wait_until="networkidle")
            fontes = page.evaluate(
                """async (pdf) => {
                    const { apiJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                    const bytes = Uint8Array.from(atob(pdf), c => c.charCodeAt(0));
                    const form = new FormData();
                    form.append('files', new Blob([bytes], {type: 'application/pdf'}), 'autos-custodia-ficticios.pdf');
                    await apiJson('/api/cases/upload-pdfs', {method: 'POST', body: form});
                    const caso = await apiJson('/api/cases/active');
                    return caso.pecas.map(p => p.doc_id);
                }""",
                _autos_ficticios(tmp_path, custodia=True),
            )
            aguardadas = ["ID-9900106", "ID-9900107"]
            assert all(fonte in fontes for fonte in aguardadas), fontes
            fontes = aguardadas
            page.reload(wait_until="networkidle")
            page.click('.station-btn[data-station="dosimetria"]')
            page.select_option("#selCrimeVeredicto", "condenar")
            page.check("#usarIntervalosCustodia")
            if not page.locator("[data-intervalo-custodia]").count():
                page.locator("[data-custodia-adicionar]").click()
            linha = page.locator("[data-intervalo-custodia]").first
            linha.locator('[name="inicio"]').fill("2024-05-28")
            linha.locator('[name="fonte_inicio"]').select_option(fontes[0])
            linha.locator('[name="em_curso"]').check()
            linha.locator('[name="fonte_fim"]').select_option(fontes[1])
            saida = page.evaluate(
                """async () => {
                    const { postJson } = await import('/static/js/modulos/api.js?v=2.4.0');
                    const { buildDosimetryPayload } = await import('/static/js/modulos/estacao-dosimetria.js?v=2.4.0');
                    const payload = {...buildDosimetryPayload(), data_sentenca: "2024-05-30"};
                    const res = await postJson('/api/dosimetria/simulate', payload);
                    return {payload, detra: res.dosimetria.detracao, dias: res.dosimetria.detracao_dias, texto: res.detracao_periodo};
                }"""
            )
            assert "dias_prisao_cautelar" not in saida["payload"]
            assert saida["detra"]["periodos"][0]["fontes"] == fontes
            assert saida["detra"]["dias"] == saida["dias"] == 3
            assert "28/05/2024 e 30/05/2024" in saida["texto"]
        finally:
            browser.close()
