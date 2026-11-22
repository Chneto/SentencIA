"""Histórico de versões da minuta, comparador e banco de trechos."""

from __future__ import annotations

from pathlib import Path

import pytest

import app.core.config as core_config
from app.core import session_state
from app.export import trechos
from app.export.comparador import comparar


# ------------------------------------------------------------------------------
# Comparador
# ------------------------------------------------------------------------------
class TestComparador:
    def test_textos_iguais(self):
        r = comparar("A\n\nB", "A\n\nB")
        assert r["resumo"]["identicos"] is True
        assert all(b["tipo"] == "igual" for b in r["blocos"])

    def test_palavra_alterada_em_paragrafo(self):
        r = comparar("O réu foi denunciado pelo crime.\n\nFim.", "O acusado foi denunciado pelo crime.\n\nFim.")
        alterado = next(b for b in r["blocos"] if b["tipo"] == "alterado")
        ops = [(s["op"], s["t"].strip()) for s in alterado["segmentos"] if s["op"] != "="]
        assert ("-", "réu") in ops and ("+", "acusado") in ops
        assert r["resumo"] == {**r["resumo"], "paragrafos_alterados": 1, "palavras_adicionadas": 1, "palavras_removidas": 1}

    def test_paragrafo_adicionado_e_removido(self):
        r = comparar("A\n\nB\n\nC", "A\n\nC\n\nNovo paragrafo totalmente diferente aqui")
        tipos = [b["tipo"] for b in r["blocos"]]
        assert "removido" in tipos and "adicionado" in tipos
        assert r["resumo"]["identicos"] is False

    def test_reconstroi_os_dois_textos(self):
        a = "Primeiro parágrafo do texto.\n\nSegundo parágrafo com mais palavras aqui."
        b = "Primeiro parágrafo do texto original.\n\nSegundo parágrafo com menos palavras."
        segs = [s for bl in comparar(a, b)["blocos"] for s in bl["segmentos"]]
        # no bloco alterado, juntar "=" e "-" devolve o original; "=" e "+" devolve o novo
        for bl in comparar(a, b)["blocos"]:
            if bl["tipo"] == "alterado":
                orig = "".join(s["t"] for s in bl["segmentos"] if s["op"] in "=-")
                novo = "".join(s["t"] for s in bl["segmentos"] if s["op"] in "=+")
                assert orig in a and novo in b
        assert segs


# ------------------------------------------------------------------------------
# Histórico de versões (estado da sessão)
# ------------------------------------------------------------------------------
class _Sent:
    def __init__(self, texto, html=""):
        self.texto_completo, self.texto_html, self.formatacao = texto, html, {}


class _Aud:
    score_confiabilidade = 91.0
    aprovada_sem_alucinacao = True


@pytest.fixture()
def estado_limpo():
    antes = list(session_state.ACTIVE_CASE_STATE.get("historico_versoes") or [])
    session_state.ACTIVE_CASE_STATE["historico_versoes"] = []
    yield
    session_state.ACTIVE_CASE_STATE["historico_versoes"] = antes


class TestHistoricoDeVersoes:
    def test_geracao_reinicia_e_edicao_acrescenta(self, estado_limpo):
        session_state.register_generation(_Sent("v um"), _Aud(), origem="monolitica")
        session_state.register_generation(_Sent("v dois editado", "<p>x</p>"), _Aud(), origem="edicao_direta")
        session_state.register_generation(_Sent("v tres editado"), _Aud(), origem="edicao_direta")
        v = session_state.listar_versoes()
        assert [x["id"] for x in v] == ["v3", "v2", "v1"]  # mais recente primeiro
        assert v[-1]["rotulo"] == "Gerada pelo motor local" and v[0]["rotulo"] == "Edição salva e re-auditada"
        assert "texto_completo" not in v[0]
        assert session_state.obter_versao("v2")["texto_completo"] == "v dois editado"
        # nova geração começa do zero
        session_state.register_generation(_Sent("outra"), _Aud(), origem="seccional")
        assert [x["id"] for x in session_state.listar_versoes()] == ["v1"]

    def test_limite_de_versoes(self, estado_limpo):
        session_state.register_generation(_Sent("base"), _Aud(), origem="monolitica")
        for i in range(session_state.HISTORICO_MAXIMO + 5):
            session_state.register_generation(_Sent(f"edicao {i}"), _Aud(), origem="edicao_direta")
        assert len(session_state.listar_versoes()) == session_state.HISTORICO_MAXIMO

    def test_limpar_producao_zera_o_historico(self, estado_limpo):
        session_state.register_generation(_Sent("x"), _Aud(), origem="monolitica")
        session_state.clear_production_state()
        assert session_state.listar_versoes() == []


# ------------------------------------------------------------------------------
# Banco de trechos
# ------------------------------------------------------------------------------
class TestBancoDeTrechos:
    @pytest.fixture(autouse=True)
    def _pasta(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(core_config, "TEMPLATES_DIR", tmp_path)

    def test_exemplos_e_usuario(self):
        base = trechos.listar_trechos()
        assert base and all(t["padrao"] for t in base)
        t = trechos.salvar_trecho({"titulo": "  Minha  tese ", "texto": "Texto\r\ndo trecho", "atalho": "/MinhaTese", "categoria": "Mérito"})
        assert t["titulo"] == "Minha tese" and t["atalho"] == "minhatese" and t["texto"] == "Texto\ndo trecho"
        assert trechos.trecho_por_atalho("/minhatese")["id"] == t["id"]
        # atualizar mantendo o id
        t2 = trechos.salvar_trecho({**t, "texto": "novo texto"})
        assert t2["id"] == t["id"] and len([x for x in trechos.listar_trechos() if not x["padrao"]]) == 1
        assert trechos.excluir_trecho(t["id"]) is True and trechos.excluir_trecho(t["id"]) is False

    def test_validacoes(self):
        with pytest.raises(ValueError):
            trechos.salvar_trecho({"titulo": "", "texto": "x"})
        with pytest.raises(ValueError):
            trechos.salvar_trecho({"titulo": "a", "texto": "  "})
        with pytest.raises(ValueError):
            trechos.salvar_trecho({"titulo": "a", "texto": "x", "atalho": "com espaço"})
        with pytest.raises(ValueError):
            trechos.salvar_trecho({"titulo": "a", "texto": "x" * 20000})

    def test_atalho_duplicado_e_exemplo_protegido(self):
        trechos.salvar_trecho({"titulo": "A", "texto": "x", "atalho": "abc"})
        with pytest.raises(ValueError):
            trechos.salvar_trecho({"titulo": "B", "texto": "y", "atalho": "abc"})
        with pytest.raises(ValueError):  # atalho de exemplo
            trechos.salvar_trecho({"titulo": "C", "texto": "y", "atalho": "fecho"})
        with pytest.raises(ValueError):  # alterar exemplo
            trechos.salvar_trecho({"id": "padrao-vistos", "titulo": "C", "texto": "y"})

    def test_arquivo_corrompido_nao_derruba(self, tmp_path: Path):
        (tmp_path / "trechos_gabinete.json").write_text("[[[", encoding="utf-8")
        assert trechos.listar_trechos()
