# -*- coding: utf-8 -*-
"""
Testes automatizados da árvore de publicação (`scripts/publicacao.py`) e do
verificador de sigilo (`scripts/verificar_sigilo.py`) — TASK-032.
"""

from __future__ import annotations

import importlib.util
import json
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


publicacao = _carregar_modulo("sentencia_publicacao", RAIZ / "scripts" / "publicacao.py")
verificar_sigilo = _carregar_modulo("sentencia_verificar_sigilo", RAIZ / "scripts" / "verificar_sigilo.py")


@pytest.mark.parametrize(
    "rel",
    [
        "app/main.py",
        "app/static/index.html",
        "instalador/bootstrap.ps1",
        "instalador/bootstrap.sh",
        "scripts/build_release.py",
        "scripts/publicacao.py",
        "scripts/verificar_sigilo.py",
        "SentencIA.app/Contents/Info.plist",
        "SentencIA.app/Contents/MacOS/sentencia",
        ".github/workflows/ci.yml",
        "tests/test_api_v2.py",
        "docs/DESIGN.md",
        "docs/context.md",
        "docs/roadmap.md",
        "docs/pje/teste-colagem.html",
        "docs/identidade/manual_de_marca.md",
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "CHANGELOG.md",
        "LICENSE",
        ".gitignore",
        "launcher.py",
        "Iniciar SentencIA.bat",
        "Iniciar SentencIA.command",
        "Iniciar SentencIA.vbs",
        "data/knowledge_base/acervo_completo.json",
    ],
)
def test_publicacao_inclui_lista_branca(rel: str):
    assert publicacao.arquivo_na_lista_branca(rel) is True


@pytest.mark.parametrize(
    "rel",
    [
        "data/cases/processo.pdf",
        "data/cases/_arquivados/20261120.zip",
        "data/secrets/secrets.json",
        "data/user_settings.json",
        "data/logs/app.log",
        "data/exports/minuta.docx",
        "data/templates_gabinete/amostras/MOD-01.txt",
        "data/templates_gabinete/style_profile.json",
        "data/templates_gabinete/timbre_tjrn.docx",
        "data/knowledge_base/custom_precedentes.json",
        "Modelos de Sentenças/Sentenca.docx",
        "Modelos de Sentenças/manual.pdf",
        "python/python.exe",
        ".venv/Scripts/python.exe",
        "dist/SentencIA.zip",
        ".ai-collab/BOARD.md",
        ".agent/workflows/executar.md",
        "CLAUDE.md",
        "SentencIA.lnk",
        "docs/memory.md",
        "docs/TEST_READY.md",
        "docs/agents.md",
        "docs/v2/PLANO_V2.md",
        "docs/design/screenshots/tela.png",
        "docs/identidade/canva/sentencia-apresentacao.pdf",
        "qualquer/autos.pdf.pje_cache_v2.json",
        "qualquer/midia.mp4.transcript_v2.json",
    ],
)
def test_publicacao_exclui_arquivos_sigilosos_e_internos(rel: str):
    assert publicacao.arquivo_na_lista_branca(rel) is False


def test_publicacao_listar_e_exportar_em_pasta_temporaria(tmp_path: Path, capsys):
    rc_listar = publicacao.main(["--raiz", str(RAIZ), "--listar"])
    assert rc_listar == 0
    saida_listar = capsys.readouterr().out.splitlines()
    assert "app/main.py" in saida_listar
    assert "LICENSE" in saida_listar
    assert "scripts/verificar_sigilo.py" in saida_listar
    assert "data/knowledge_base/acervo_completo.json" in saida_listar
    assert not any(x.startswith(("Modelos de Sentenças/", "data/cases/", ".ai-collab/", "docs/design/")) for x in saida_listar)
    assert "docs/memory.md" not in saida_listar
    assert "CLAUDE.md" not in saida_listar

    # Recusa exportar dentro da própria raiz
    with pytest.raises(ValueError):
        publicacao.exportar_arvore_publicacao(RAIZ / "subpasta_invalida", raiz=RAIZ)

    destino = tmp_path / "publicacao_teste"
    exportados = publicacao.exportar_arvore_publicacao(destino, raiz=RAIZ)
    assert len(exportados) == len(saida_listar)
    assert (destino / "LICENSE").is_file()
    assert "Copyright (c) 2026 FChNeto" in (destino / "LICENSE").read_text(encoding="utf-8")
    assert "Modelos de Sentenças/" in (destino / ".gitignore").read_text(encoding="utf-8")
    assert (destino / "data" / "knowledge_base" / "acervo_completo.json").is_file()
    assert not (destino / "Modelos de Sentenças").exists()
    assert not (destino / "data" / "cases").exists()
    assert not (destino / "docs" / "memory.md").exists()

    # Normalização de fins de linha na árvore exportada
    sh_bytes = (destino / "instalador" / "bootstrap.sh").read_bytes()
    assert b"\r\n" not in sh_bytes
    ps1_bytes = (destino / "instalador" / "bootstrap.ps1").read_bytes()
    assert ps1_bytes.startswith(b"\xef\xbb\xbf") and b"\r\n" in ps1_bytes
    # O instalador da linha única (irm | iex) sai sem BOM: o PowerShell 5.1 não o honra no texto baixado.
    instalar = (destino / "instalador" / "instalar.ps1").read_bytes()
    assert not instalar.startswith(b"\xef\xbb\xbf") and b"\r\n" in instalar
    instalar.decode("ascii")


def test_verificador_sigilo_acusa_nomes_cpf_cnj_segredos_plantados_sem_vazar_texto(tmp_path: Path, capsys):
    """
    Critério obrigatório da TASK-032:
    Planta nomes, CPF, CNJ, caminho de usuário, chave de API e segredo do cofre num arquivo
    temporário e verifica que `verificar_sigilo.py`:
    1. Acusa todos os achados (código de saída != 0 e linhas `arquivo:linha: TIPO`);
    2. NUNCA imprime na saída os nomes, o CPF, o CNJ ou o segredo plantados.
    """
    # 1. Monta uma fonte local simulada com dados "reais" que jamais podem vazar
    raiz_local_falsa = tmp_path / "fonte_local"
    cases_dir = raiz_local_falsa / "data" / "cases"
    cases_dir.mkdir(parents=True)
    secrets_dir = raiz_local_falsa / "data" / "secrets"
    secrets_dir.mkdir(parents=True)

    nome_parte_plantado = "Astrogildo Epaminondas de Albuquerque"
    cpf_plantado = ".".join(("432", "765", "891")) + "-23"
    cnj_plantado = "0891234-77" + ".2024.8.20.5999"
    segredo_cofre_plantado = "segredo_super_confidencial_cofre_987654"
    chave_api_plantada = "AIza" + "SyChavePlantadaDeTeste99887766"
    caminho_pessoal_plantado = "C:\\Users\\" + "usuario_secreto_xyz\\Documentos"
    email_plantado = "parte.sigilosa" + "@" + "exemplo.jus.br"
    telefone_plantado = "(" + "84) " + "99123-4567"

    cache_processo = {
        "numero_processo": cnj_plantado,
        "ofendida": nome_parte_plantado,
        "cpf": cpf_plantado,
    }
    (cases_dir / f"{cnj_plantado}.pdf.pje_cache_v2.json").write_text(
        json.dumps(cache_processo, ensure_ascii=False),
        encoding="utf-8",
    )
    (secrets_dir / "secrets.json").write_text(
        json.dumps({"gemini_api_key": segredo_cofre_plantado}),
        encoding="utf-8",
    )

    # 2. Monta uma árvore exportada contendo um arquivo contaminado
    arvore = tmp_path / "arvore_contaminada"
    arvore.mkdir(parents=True)
    arq_contaminado = arvore / "tests" / "test_contaminado.py"
    arq_contaminado.parent.mkdir(parents=True)
    arq_contaminado.write_text(
        "\n".join([
            "# Arquivo de teste com vazamentos plantados",
            f"parte = '{nome_parte_plantado}'",
            f"documento = '{cpf_plantado}'",
            f"processo = '{cnj_plantado}'",
            f"cofre = '{segredo_cofre_plantado}'",
            f"api = '{chave_api_plantada}'",
            f"pasta = r'{caminho_pessoal_plantado}'",
            f"contato = '{email_plantado} / {telefone_plantado}'",
        ]) + "\n",
        encoding="utf-8",
    )

    rc = verificar_sigilo.main([
        str(arvore),
        "--raiz-dados-locais",
        str(raiz_local_falsa),
    ])
    capturado = capsys.readouterr()
    saida = capturado.out + capturado.err

    assert rc != 0
    assert "tests/test_contaminado.py:2: NOME_PARTE_REAL" in saida
    assert "tests/test_contaminado.py:3: CPF_REAL" in saida
    assert "tests/test_contaminado.py:4: NUMERO_CNJ_REAL" in saida
    assert "tests/test_contaminado.py:5: SEGREDO_COFRE" in saida
    assert "tests/test_contaminado.py:6: CHAVE_API_OU_TOKEN" in saida
    assert "tests/test_contaminado.py:7: CAMINHO_USUARIO" in saida
    assert "tests/test_contaminado.py:8: EMAIL" in saida
    assert "tests/test_contaminado.py:8: TELEFONE" in saida

    # Garantias inegociáveis: NENHUM valor plantado aparece na saída
    for valor_proibido in (
        "Astrogildo",
        "Epaminondas",
        cpf_plantado,
        cnj_plantado,
        segredo_cofre_plantado,
        chave_api_plantada,
        "usuario_secreto_xyz",
        email_plantado,
        telefone_plantado,
    ):
        assert valor_proibido not in saida


def test_arvore_exportada_do_repositorio_tem_zero_achados(tmp_path: Path):
    destino = tmp_path / "arvore_limpa"
    publicacao.exportar_arvore_publicacao(destino, raiz=RAIZ)
    achados, total_arqs, _ = verificar_sigilo.verificar_arvore(destino)
    assert total_arqs > 100
    assert achados == [], [a.formatar() for a in achados]


def _criar_pdf_ficticio(destino: Path, texto: str = "Guia de instalacao ficticio do SentencIA.") -> None:
    import pymupdf

    destino.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    pag = doc.new_page()
    pag.insert_text((72, 72), texto)
    doc.save(str(destino))
    doc.close()


def test_task045_pasta_raiz_pacote_release_aceita_instalacao_pdf(tmp_path: Path, capsys):
    extraido = tmp_path / "zip_extraido"
    _criar_pdf_ficticio(extraido / "SentencIA-v9.9.9" / "docs" / "INSTALACAO.pdf")

    rc = verificar_sigilo.main([str(extraido), "--apenas-padroes"])
    saida = capsys.readouterr().out

    assert rc == 0, saida
    assert "SentencIA-v9.9.9/" in saida
    assert "VERIFICACAO DE SIGILO APROVADA: 0 achados" in saida


def test_task045_arquivo_proibido_dentro_da_pasta_pacote_continua_acusado(tmp_path: Path, capsys):
    extraido = tmp_path / "zip_extraido"
    raiz_pacote = extraido / "SentencIA-v9.9.9"
    _criar_pdf_ficticio(raiz_pacote / "docs" / "INSTALACAO.pdf")
    _criar_pdf_ficticio(raiz_pacote / "docs" / "anexo_indevido.pdf")
    (raiz_pacote / "CLAUDE.md").write_text("interno\n", encoding="utf-8")

    rc = verificar_sigilo.main([str(extraido), "--apenas-padroes"])
    saida = capsys.readouterr().out

    assert rc == 1
    assert "SentencIA-v9.9.9/" in saida
    assert "docs/anexo_indevido.pdf:0: ARQUIVO_PROIBIDO_NA_PUBLICACAO" in saida
    assert "CLAUDE.md:0: ARQUIVO_PROIBIDO_NA_PUBLICACAO" in saida
    assert "docs/INSTALACAO.pdf:0: ARQUIVO_PROIBIDO_NA_PUBLICACAO" not in saida


def test_task045_pasta_com_duas_subpastas_nao_troca_raiz(tmp_path: Path, capsys):
    extraido = tmp_path / "zip_duas_pastas"
    _criar_pdf_ficticio(extraido / "SentencIA-v9.9.9" / "docs" / "INSTALACAO.pdf")
    (extraido / "outra_pasta").mkdir(parents=True)
    (extraido / "outra_pasta" / "nota.txt").write_text("ok\n", encoding="utf-8")

    raiz_resolvida, trocou = verificar_sigilo.resolver_raiz_verificacao(extraido)
    assert trocou is False
    assert raiz_resolvida == extraido.resolve()

    rc = verificar_sigilo.main([str(extraido), "--apenas-padroes"])
    saida = capsys.readouterr().out

    assert rc == 1
    assert "SentencIA-v9.9.9/docs/INSTALACAO.pdf:0: ARQUIVO_PROIBIDO_NA_PUBLICACAO" in saida
