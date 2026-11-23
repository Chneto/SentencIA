"""Testes de erros visíveis e falha fechada nos scripts (TASK-076 — S110)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def _carregar_modulo(nome: str, caminho: Path):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)
    return mod


verificar_sigilo = _carregar_modulo("sentencia_verificar_sigilo_t076", RAIZ / "scripts" / "verificar_sigilo.py")


def _preparar_arvore_limpa(tmp_path: Path) -> Path:
    arvore = tmp_path / "arvore_publica"
    arvore.mkdir()
    (arvore / "README.md").write_text("# SentencIA ficticio\n", encoding="utf-8")
    return arvore


@pytest.mark.parametrize(
    ("subcaminho", "conteudo", "categoria_esperada"),
    [
        ("data/cases/caso_sigiloso_fulano.zip", b"nao-eh-zip", "cases_zip"),
        ("Modelos de Sentenças/modelo_fulano.docx", b"nao-eh-docx", "modelos_docx"),
        ("Modelos de Sentenças/modelo_fulano.pdf", b"nao-eh-pdf", "modelos_pdf"),
        ("data/secrets/cofre_segredo_xyz.json", "{json invalido", "secrets_json"),
    ],
)
def test_verificar_sigilo_falha_visivel_em_arquivos_corrompidos_sem_vazar_caminho(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    subcaminho: str,
    conteudo: str | bytes,
    categoria_esperada: str,
):
    """Cada fonte corrompida deve emitir erro em stderr (sem caminho/nome) e falhar o script (rc != 0)."""
    arvore = _preparar_arvore_limpa(tmp_path)
    fonte_local = tmp_path / "fonte_local_restrita"
    alvo = fonte_local / subcaminho
    alvo.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(conteudo, bytes):
        alvo.write_bytes(conteudo)
    else:
        alvo.write_text(conteudo, encoding="utf-8")

    monkeypatch.setattr(verificar_sigilo, "descobrir_raizes_locais", lambda *_a, **_kw: [fonte_local])

    rc = verificar_sigilo.main([str(arvore)])
    capturado = capsys.readouterr()

    assert rc != 0
    assert categoria_esperada in capturado.err
    assert "FALHA_LEITURA_FONTE_SIGILO" in capturado.out
    # Regra de sigilo: nunca expor caminho, nome de arquivo sigiloso ou segredo na mensagem
    saida_total = capturado.out + capturado.err
    assert "fulano" not in saida_total.lower()
    assert "segredo_xyz" not in saida_total.lower()
    assert str(fonte_local) not in saida_total


@pytest.mark.parametrize(
    ("subcaminho", "categoria_esperada"),
    [
        ("data/cases/nota_sigilosa_beltrano.txt", "cases_txt"),
        ("data/cases/caso_sigiloso_beltrano.json", "cases_json"),
        ("Modelos de Sentenças/rascunho_beltrano.txt", "modelos_texto"),
        ("data/templates_gabinete/amostras/amostra_beltrano.txt", "amostras_txt"),
    ],
)
def test_verificar_sigilo_falha_visivel_em_oserror_de_leitura_texto(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    subcaminho: str,
    categoria_esperada: str,
):
    """OSError ao ler fontes .txt/.json de sigilo deve ser reportado em stderr e reprovar o verificador."""
    arvore = _preparar_arvore_limpa(tmp_path)
    fonte_local = tmp_path / "fonte_local_restrita"
    alvo = fonte_local / subcaminho
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text("conteudo ficticio protegido", encoding="utf-8")

    read_text_original = Path.read_text

    def _falhar_no_alvo(self: Path, *args, **kwargs):
        if self.resolve() == alvo.resolve():
            raise PermissionError("acesso negado simulado")
        return read_text_original(self, *args, **kwargs)

    monkeypatch.setattr(verificar_sigilo, "descobrir_raizes_locais", lambda *_a, **_kw: [fonte_local])
    monkeypatch.setattr(Path, "read_text", _falhar_no_alvo)

    rc = verificar_sigilo.main([str(arvore)])
    capturado = capsys.readouterr()

    assert rc != 0
    assert categoria_esperada in capturado.err
    assert "PermissionError" in capturado.err
    assert "FALHA_LEITURA_FONTE_SIGILO" in capturado.out
    saida_total = capturado.out + capturado.err
    assert "beltrano" not in saida_total.lower()
    assert str(fonte_local) not in saida_total


def _gravar_zip_com_json(alvo: Path, conteudo_json: str) -> None:
    import zipfile

    with zipfile.ZipFile(alvo, "w") as zf:
        zf.writestr("caso_ficticio/bundle.json", conteudo_json)


@pytest.mark.parametrize(
    ("subcaminho", "categoria_esperada"),
    [
        ("data/cases/caso_sigiloso_ciclano.json", "cases_json"),
        ("data/cases/_arquivados/caso_sigiloso_ciclano.zip", "cases_zip_json"),
    ],
)
def test_verificar_sigilo_json_invalido_apos_coleta_textual_so_avisa(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    subcaminho: str,
    categoria_esperada: str,
):
    """TASK-080: JSON corrompido (lido com sucesso) vira aviso em stderr e não bloqueia a publicação."""
    arvore = _preparar_arvore_limpa(tmp_path)
    fonte_local = tmp_path / "fonte_local_restrita"
    alvo = fonte_local / subcaminho
    alvo.parent.mkdir(parents=True, exist_ok=True)
    conteudo = '{"reu": "Ciclano Ficticio de Tal", "processo": '  # JSON truncado
    if alvo.suffix == ".zip":
        _gravar_zip_com_json(alvo, conteudo)
    else:
        alvo.write_text(conteudo, encoding="utf-8")

    monkeypatch.setattr(verificar_sigilo, "descobrir_raizes_locais", lambda *_a, **_kw: [fonte_local])

    rc = verificar_sigilo.main([str(arvore)])
    capturado = capsys.readouterr()

    assert rc == 0, capturado.out
    assert "AVISO" in capturado.err
    assert categoria_esperada in capturado.err
    assert "JSONDecodeError" in capturado.err
    assert "FALHA_LEITURA_FONTE_SIGILO" not in capturado.out
    saida_total = capturado.out + capturado.err
    assert "ciclano" not in saida_total.lower()
    assert str(fonte_local) not in saida_total


def test_verificar_sigilo_json_invalido_ainda_alimenta_dicionario_pelo_texto_bruto(tmp_path: Path):
    """O texto bruto do JSON corrompido continua no dicionário (a verificação não fica cega)."""
    fonte_local = tmp_path / "fonte_local_restrita"
    alvo = fonte_local / "data" / "cases" / "bundle.json"
    alvo.parent.mkdir(parents=True)
    alvo.write_text('{"processo": "0000001-11.2025.8.20.0001", "reu": ', encoding="utf-8")

    dic = verificar_sigilo.carregar_dicionario_sigilo_local([fonte_local])

    assert not dic.falhas_coleta
    assert dic.cnjs_reais, "o número de processo fictício do texto bruto deveria estar no dicionário"
