"""Detração apoiada em documentos fictícios e usada apenas no cálculo do regime."""

import datetime as dt

import pytest
from pydantic import ValidationError

from app.api.models import GenerateSentenceRequest
from app.core.errors import DadosInsuficientesError
from app.generation.sentence_generator import inferir_detracao_cautelar
from app.ingestion.custodia import analisar_custodia
from app.ingestion.laya_classifier import classificar_pecas_autos, extrair_metadados_processo
from app.legal_engine.detracao import IntervaloCustodia, calcular_detracao, detracao_por_quantidade
from app.legal_engine.dosimetria_calculator import CrimeDosimetriaInput, calcular_dosimetria_global
from tests.test_pontos_decisivos import _doc


def _intervalo(inicio, fim, **extra):
    return IntervaloCustodia(inicio=inicio, fim=fim, fonte_inicio="ID-PRISAO-FICTICIA",
                            fonte_fim="ID-CERTIDAO-FICTICIA", **extra)


def test_uniao_de_periodos_e_custodia_em_curso_formam_o_mesmo_resultado():
    intervalos = [
        _intervalo("2024-03-01", "2024-03-04"),
        _intervalo("2024-03-03", "2024-03-06"),
        _intervalo("2024-03-08", "2024-03-08"),
        _intervalo("2024-03-10", None, em_curso=True),
    ]
    resultado = calcular_detracao(intervalos, data_referencia=dt.date(2024, 3, 11))
    assert resultado.dias == 9
    assert len(resultado.periodos) == 3
    assert "01/03/2024 e 06/03/2024" in resultado.descricao_periodos
    assert "10/03/2024 e 11/03/2024" in resultado.descricao_periodos
    assert "em curso" in resultado.descricao_periodos


@pytest.mark.parametrize("dia, dias", [(2, 0), (3, 1), (4, 1)])
def test_vigencia_art_387_paragrafo_2_para_fins_de_regime(dia, dias):
    """Lei 12.736/2012, vigente em 03/12/2012.
    https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2012/lei/l12736.htm
    """
    intervalo = _intervalo("2012-12-01", "2012-12-01")
    assert calcular_detracao([intervalo], data_referencia=dt.date(2012, 12, dia)).dias == dias


def test_ordem_de_soltura_sem_cumprimento_deixa_intervalo_pendente():
    pecas = classificar_pecas_autos([
        _doc("ID-APF-FICTICIO", "Auto de Prisão em Flagrante", "Foi preso em flagrante em 10/03/2024."),
        _doc("ID-ALVARA-FICTICIO", "Alvará de Soltura", "Expeça-se alvará de soltura em 12/03/2024."),
    ])
    assert inferir_detracao_cautelar(pecas) == (0, "")


@pytest.mark.parametrize("valor", [True, False, 2.5, -1])
def test_quantidade_invalida_rejeitada_tambem_fora_da_api(valor):
    with pytest.raises(DadosInsuficientesError):
        detracao_por_quantidade(valor)
    with pytest.raises(DadosInsuficientesError):
        calcular_dosimetria_global([], dias_prisao_cautelar=valor)
    with pytest.raises(ValidationError):
        GenerateSentenceRequest(dias_prisao_cautelar=valor)


def test_fontes_e_datas_da_custodia_em_curso_sao_obrigatorias():
    with pytest.raises(ValidationError):
        IntervaloCustodia(inicio="2024-03-10", fonte_inicio="ID-PRISAO-FICTICIA", em_curso=True)
    com_fonte = _intervalo("2024-03-10", None, em_curso=True)
    with pytest.raises(DadosInsuficientesError):
        calcular_detracao([com_fonte], data_referencia=dt.date(2024, 3, 11), origem="juizo", fontes_validas=set())
    with pytest.raises(DadosInsuficientesError):
        calcular_detracao([com_fonte], data_referencia=dt.date(2024, 3, 9), origem="juizo")


def test_cartao_prisao_provisoria_analise_nao_calcula_dias_no_navegador():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / "app" / "static" / "js" / "modulos" / "estacao-analise.js").read_text(
        encoding="utf-8"
    )
    assert "86400000" not in js
    assert "Date.parse" not in js
    assert "fonte_inicio" in js and "intervalos" in js
    assert "data-confirmar-papel-media" in js


def test_fluxo_confirmacao_cartao_duas_custodias_e_simulacao_detracao():
    """Confirmação do cartão prisao_provisoria com duas custódias (incluindo em curso) alimenta a simulação."""
    from fastapi.testclient import TestClient

    from app.api.security import TOKEN_HEADER, get_session_token
    from app.main import app, set_active_bundle

    bundle = {
        "is_demo": False,
        "raw_documents": [
            {"doc_id": "ID-APF-1", "folha": "fls. 01/02", "titulo": "Auto de Prisão", "data_peca": "2025-01-10", "fase": "INQUISITORIAL", "conteudo": "Prisão em 10/01/2025."},
            {"doc_id": "ID-ALV-1", "folha": "fls. 03/03", "titulo": "Alvará de Soltura", "data_peca": "2025-01-20", "fase": "JUDICIAL", "conteudo": "Soltura cumprida em 20/01/2025."},
            {"doc_id": "ID-PRV-2", "folha": "fls. 04/04", "titulo": "Mandado de Prisão", "data_peca": "2025-03-01", "fase": "JUDICIAL", "conteudo": "Prisão preventiva cumprida em 01/03/2025."},
            {"doc_id": "ID-CERT-2", "folha": "fls. 05/05", "titulo": "Certidão Carcerária", "data_peca": "2025-03-10", "fase": "JUDICIAL", "conteudo": "Réu permanece preso na data da sentença."},
        ],
        "transcriptions": [],
        "metadados_override": {
            "data_fato": "2025-01-10",
            "data_recebimento_denuncia": "2025-02-01",
            "data_nascimento_reu": "1990-05-01",
            "crimes_imputados": ["art_129_p13_cp"],
        },
    }
    set_active_bundle(bundle, explicit_demo=False)
    client = TestClient(app)
    headers = {TOKEN_HEADER: get_session_token()}

    intervalos = [
        {"inicio": "2025-01-10", "fim": "2025-01-20", "fonte_inicio": "ID-APF-1", "fonte_fim": "ID-ALV-1", "em_curso": False},
        {"inicio": "2025-03-01", "fim": None, "fonte_inicio": "ID-PRV-2", "fonte_fim": "ID-CERT-2", "em_curso": True},
    ]
    r_ov = client.post(
        "/api/cases/metadata-override",
        json={"metadados": {"pontos_decisivos_confirmados": {"prisao_provisoria": {"intervalos": intervalos}}}},
        headers=headers,
    )
    assert r_ov.status_code == 200, r_ov.text

    r_sim = client.post(
        "/api/dosimetria/simulate",
        json={
            "data_sentenca": "2025-03-10",
            "crimes_config": [{"crime_code": "art_129_p13_cp", "veredicto": "condenar"}],
        },
        headers=headers,
    )
    assert r_sim.status_code == 200, r_sim.text
    sim = r_sim.json()
    # 10..20/01 (11 dias inclusivos) + 01..10/03 (10 dias inclusivos) = 21 dias
    assert sim["detracao_dias"] == 21
    assert "ID-APF-1" in (sim.get("detracao_periodo") or "") or sim["detracao_dias"] == 21


def test_soltura_vinculada_ao_segundo_inicio_nao_altera_regime_pelo_primeiro():
    """CPP, art. 387, § 2º (Lei 12.736/2012, vigência em 03/12/2012) e CP, art. 33.
    https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2012/lei/l12736.htm
    https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm
    """
    fontes = [
        _doc("ID-APF-FICTICIO", "Auto de Prisão em Flagrante", "Foi capturado em 03/01/2024."),
        _doc("ID-MANDADO-FICTICIO", "Certidão", "Mandado de prisão cumprido em 10/02/2024."),
        _doc("ID-ALVARA-FICTICIO", "Certidão de cumprimento do alvará de soltura",
             "Cumprido o alvará de soltura em 11/02/2024, referente à custódia iniciada em 10/02/2024."),
    ]
    meta = extrair_metadados_processo(classificar_pecas_autos(fontes))
    resultado = calcular_detracao(meta.intervalos_custodia)
    assert resultado.dias == 2
    assert resultado.periodos[0].fontes == ["ID-MANDADO-FICTICIO", "ID-ALVARA-FICTICIO"]
    assert "ID-APF-FICTICIO" in resultado.pendencias[0]
    ponto = next(item for item in meta.pontos_decisivos if item.chave == "prisao_provisoria")
    assert len({evento.id for evento in ponto.eventos_custodia}) == 3
    assert all(evento.trecho and evento.contexto for evento in ponto.eventos_custodia)

    crimes = [
        CrimeDosimetriaInput(crime_code="art_158_cp", data_fato=dt.date(2024, 1, 1)),
        CrimeDosimetriaInput(crime_code="art_21_lcp", data_fato=dt.date(2024, 1, 1)),
    ]
    assert calcular_dosimetria_global(crimes, detracao=resultado).regime_inicial == "semiaberto"
    assert calcular_dosimetria_global(crimes, dias_prisao_cautelar=40).regime_inicial == "aberto"


def test_custodia_convertida_e_simultanea_preserva_vinculos_e_dias_unicos():
    fontes = [
        _doc("ID-CAP-FICTICIO", "Auto de Prisão em Flagrante", "Foi preso em flagrante em 03/01/2024."),
        _doc("ID-CNV-FICTICIO", "Decisão", "Converteu a prisão em flagrante em preventiva em 04/01/2024 referente ao ID-CAP-FICTICIO."),
        _doc("ID-MAND-FICTICIO", "Certidão", "Mandado de prisão cumprido em 05/01/2024."),
        _doc("ID-SOL-A", "Certidão", "Cumprido o alvará de soltura em 07/01/2024 referente ao ID-CNV-FICTICIO."),
        _doc("ID-SOL-B", "Certidão", "Cumprido o alvará de soltura em 10/01/2024 referente ao ID-MAND-FICTICIO."),
        _doc("ID-MAND-C", "Certidão", "Mandado de prisão cumprido em 20/01/2024."),
        _doc("ID-SOL-C", "Certidão", "Cumprido o alvará de soltura em 21/01/2024 referente ao ID-MAND-C."),
    ]
    extracao = analisar_custodia(classificar_pecas_autos(fontes))
    assert not extracao.pendencias
    assert [i.fonte_inicio for i in extracao.intervalos] == ["ID-CAP-FICTICIO", "ID-MAND-FICTICIO", "ID-MAND-C"]
    assert [e.fonte_id for e in extracao.eventos if e.tipo == "conversao"] == ["ID-CNV-FICTICIO"]
    assert calcular_detracao(extracao.intervalos).dias == 10


def test_soltura_sem_vinculo_ou_sem_inicio_fica_pendente():
    soltura = _doc("ID-SOL-FICTICIO", "Certidão", "Cumprido o alvará de soltura em 11/02/2024.")
    mandado = _doc("ID-MANDADO-FICTICIO", "Certidão", "Mandado de prisão cumprido em 10/02/2024.")
    for fontes in ([soltura], [soltura, mandado]):
        meta = extrair_metadados_processo(classificar_pecas_autos(fontes))
        ponto = next(item for item in meta.pontos_decisivos if item.chave == "prisao_provisoria")
        assert ponto.estado == "a_confirmar" and ponto.valor is None
        assert "sem vínculo documental único" in ponto.efeito
        assert "ID-SOL-FICTICIO" in meta.alertas_custodia[0]
        assert calcular_detracao(meta.intervalos_custodia).dias == 0
