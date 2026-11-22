#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verificador de sigilo pré-publicação do SentencIA (`scripts/verificar_sigilo.py`).

Regras de segurança:
1. Carrega **exclusivamente em memória** nomes de partes, números de processos (CNJ),
   CPFs e segredos a partir das pastas locais (`data/cases/`, `Modelos de Sentenças/`,
   `data/secrets/`, `data/user_settings.json`, `data/templates_gabinete/`).
2. **NUNCA imprime, grava em log ou salva o texto encontrado** nem qualquer termo
   carregado das pastas locais. A saída exibe exclusivamente:
       `<arquivo>:<linha>: <TIPO_DO_ACHADO>`
3. Além dos termos reais em memória, varre a árvore por expressões regulares:
   - CPF (`NNN.NNN.NNN-NN` e 11 dígitos precedidos de `CPF`);
   - Número CNJ fora da lista documentada de números fictícios/públicos;
   - Endereços de e-mail;
   - Telefones;
   - Caminhos pessoais (`C:\\Users\\<nome>`, `/Users/<nome>`);
   - Chaves de API e tokens (`AIza...`, `ghp_...`, `github_pat_...`, `sk-...`, `AQ....`);
   - Arquivos ou pastas proibidos na árvore publicada (`Modelos de Sentenças/`,
     `data/cases/`, `data/secrets/`, `CLAUDE.md`, `docs/memory.md`, etc.).
4. Termina com código de saída != 0 se houver qualquer achado.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import fnmatch
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import List, Optional, Sequence, Set, Tuple
import unicodedata
import zipfile

RAIZ_SCRIPT = Path(__file__).resolve().parent.parent

# Números CNJ públicos (precedentes oficiais do TJRN no ACERVO_OFICIAL) e fictícios documentados
# nos casos de demonstração e cenários sintéticos da suíte.
CNJS_PUBLICOS_OFICIAIS: Set[str] = {
    "0801498-50.2023.8.20.5114",
    "0800504-82.2024.8.20.5115",
    "0859338-03.2022.8.20.5001",
    "0804590-12.2025.8.00.0001",
    "0801122-44.2025.8.00.0001",
}

CNJS_FICTICIOS_E_PUBLICOS_DOCUMENTADOS: Set[str] = CNJS_PUBLICOS_OFICIAIS | {
    "0000000-00.0000.0.00.0000",
    "0000001-11.2025.8.20.0001",
    "0001234-55.2021.8.20.5001",
    "0001234-56.2024.8.09.0001",
    "0800001-00.2025.8.20.0001",
    "0800001-00.2025.8.20.5001",
    "0800001-00.2026.8.20.0001",
    "0800123-45.2025.8.20.5001",
    "0800998-11.2025.8.20.5001",
    "0801111-11.2025.8.20.0001",
    "0801234-56.2024.8.20.0001",
    "0801245-78.2025.8.20.5001",
    "0802100-33.2025.8.20.5001",
    "0802222-22.2025.8.20.0001",
    "0803111-11.2025.8.20.0001",
    "0803222-22.2025.8.20.0001",
    "0803333-33.2025.8.20.0001",
    "0803456-12.2025.8.20.5001",
    "0803999-55.2024.8.20.5001",
    "0804444-11.2025.8.20.5001",
    "0804567-89.2024.8.20.5001",
    "0804590-12.2025.8.20.5001",
    "0805122-88.2025.8.20.5001",
    "0805678-90.2025.8.20.5001",
    "0806111-22.2025.8.20.5001",
    "0806789-01.2025.8.20.5001",
    "0807111-11.2025.8.20.0001",
    "0807222-22.2025.8.20.0001",
    "0807333-33.2025.8.20.0001",
    "0807777-11.2025.8.20.0001",
    "0809999-00.2025.8.20.0001",
    "0809999-11.2024.8.20.5001",
    "0809999-11.2025.8.20.5001",
    "0809999-99.2025.8.20.0001",
    "0809999-99.2025.8.20.5001",
    "0900001-11.2025.8.20.0001",
    "0900123-45.2025.8.20.0001",
    "9999999-99.2023.8.20.5001",
}

# Vocabulário comum / institucional / personagens fictícios documentados do caso demo e fixtures
# que não devem gerar falso positivo como token isolado.
TOKENS_COMUNS_OU_FICTICIOS: Set[str] = {
    "carlos", "eduardo", "ferreira", "marina", "lopes", "roberto", "nascimento", "prado",
    "juliana", "costa", "azevedo", "gabriel", "henrique", "santos", "silva", "oliveira",
    "souza", "pereira", "almeida", "rodrigues", "alves", "lima", "gomes", "ribeiro",
    "martins", "carvalho", "araujo", "melo", "barbosa", "cardoso", "rocha", "dias",
    "nunes", "mendes", "castro", "pinto", "vieira", "monte", "monteiro", "moreira",
    "freitas", "teixeira", "correia", "cavalcanti", "bezerra", "medeiros", "dantas",
    "silveira", "machado", "borges", "campos", "farias", "ramos", "duarte", "moraes",
    "ministerio", "publico", "estado", "grande", "norte", "justica", "juizo", "comarca",
    "natal", "parnamirim", "mossoro", "caico", "assu", "apodi", "macaiba", "ceara",
    "delegacia", "policia", "civil", "militar", "plantao", "central", "flagrante",
    "vitima", "ofendida", "acusado", "autora", "testemunha", "informante", "declarante",
    "processo", "criminal", "juizado", "violencia", "domestica", "familiar", "contra",
    "mulher", "sentenca", "relatorio", "fundamentacao", "dispositivo", "apelacao",
    "camara", "tribunal", "relator", "desembargador", "direito", "defesa", "defensoria",
    "advogado", "promotor", "promotoria", "secretaria", "audiencia", "depoimento",
    "interrogatorio", "boletim", "ocorrencia", "inquerito", "policial", "medida",
    "protetiva", "urgencia", "prisao", "preventiva", "liberdade", "provisoria",
    "maria", "penha", "joao", "pedro", "paulo", "jose", "marcos", "lucas", "rafael",
    "bruno", "gustavo", "daniel", "fabio", "helena", "beatriz", "camila", "carla",
    "elisa", "vitoria", "antonio", "francisco", "ricardo", "procopio", "bandeira",
    "gilson", "glauber", "rego", "saraiva", "taipina", "manual", "condenatoria",
    "fulano", "beltrano", "ciclano", "sicrano", "ficticio", "ficticia", "partes",
    "mariana", "joana", "alberto", "clara", "fernandes", "senten",
}

STOPWORDS_NOME: Set[str] = {"de", "da", "do", "das", "dos", "e"}
TERMOS_INSTITUCIONAIS: Set[str] = {
    "ministerio", "publico", "estado", "poder", "judiciario", "defensoria",
    "delegacia", "policia", "juizo", "vara", "juizado", "comarca", "tribunal",
    "camara", "secretaria", "promotoria",
}

RE_CNJ = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
RE_CNJ_FLEX = re.compile(r"\b(\d{7}-\d{2})[._](\d{4})[._](\d)[._](\d{2})[._](\d{4})\b")
RE_CPF_FORMATADO = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
RE_CPF_CONTEXTO = re.compile(r"(?i)\bcpf\b[^\n\d]{0,15}\b\d{11}\b")
RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
RE_TELEFONE = re.compile(
    r"(?:(?:\+55\s*)?\(\d{2}\)\s*9?\d{4}[-\s]\d{4}\b|\b(?:\+55\s*)?\d{2}\s+9\d{4}-\d{4}\b)"
)
RE_CAMINHO_USUARIO = re.compile(
    r"(?:[A-Za-z]:\\+Users\\+(?!(?:Public|Default|All Users)\b|<|\$)[A-Za-z0-9._-]+"
    r"|(?<![A-Za-z0-9_])/Users/(?!Shared\b|<|\$)[A-Za-z0-9._-]+)"
)
RE_CHAVE_API = re.compile(
    r"\b(?:AIza[0-9A-Za-z_-]{10,}"
    r"|ghp_[0-9A-Za-z]{10,}"
    r"|github_pat_[0-9A-Za-z_]{15,}"
    r"|sk-(?:proj-)?[0-9A-Za-z_-]{15,}"
    r"|AQ\.[0-9A-Za-z_-]{10,}"
    r"|ya29\.[0-9A-Za-z_-]{15,})\b"
)

PASTAS_IGNORADAS_VARREDURA: Set[str] = {
    ".git",
    ".venv",
    "venv",
    "ENV",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
}

PREFIXOS_PROIBIDOS_PUBLICACAO: Tuple[str, ...] = (
    "Modelos de Sentenças/",
    "data/cases/",
    "data/lote/",
    "data/secrets/",
    "data/logs/",
    "data/exports/",
    "data/jobs/",
    "data/templates_gabinete/amostras/",
    "data/templates_gabinete/style_profile.json",
    "data/knowledge_base/custom_precedentes.json",
    "data/user_settings.json",
    "data/.instalado",
    "data/.servidor.json",
    ".ai-collab/",
    ".agent/",
    ".claude/",
    "CLAUDE.md",
    "docs/memory.md",
    "docs/TEST_READY.md",
    "docs/agents.md",
    "docs/v2/",
    "docs/design/",
    "python/",
    "dist/",
)

PADROES_PROIBIDOS_PUBLICACAO: Tuple[str, ...] = (
    "*.pje_cache_v2.json",
    "*.transcript_v2.json",
    "*.lnk",
    "*.zip",
    "*.log",
    "*.log.*",
    ".env",
    ".env.*",
    "secrets.json",
    "user_settings*.json",
)

EXTENSOES_TEXTO_VARREDURA: Set[str] = {
    ".py", ".pyw", ".md", ".txt", ".toml", ".json", ".html", ".css", ".js",
    ".sh", ".command", ".bat", ".cmd", ".vbs", ".ps1", ".plist", ".cfg",
    ".ini", ".yml", ".yaml", ".lock", ".gitignore", "",
}

EXTENSOES_BINARIAS_SIGILOSAS: Set[str] = {
    ".pdf", ".mp4", ".mp3", ".m4a", ".wav", ".ogg", ".mkv", ".docx",
}

ARQUIVOS_BINARIOS_PUBLICOS_PERMITIDOS: Set[str] = {
    "docs/instalacao.pdf",
}


def normalizar_texto(texto: str) -> str:
    """Remove acentos, converte para minúsculas e colapsa espaços."""
    nfd = unicodedata.normalize("NFD", texto or "")
    sem_acentos = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", sem_acentos).strip().lower()


@dataclass
class AchadoSigilo:
    arquivo: str
    linha: int
    tipo: str

    def formatar(self) -> str:
        return f"{self.arquivo}:{self.linha}: {self.tipo}"


@dataclass
class DicionarioSigilo:
    """Conjunto de termos sigilosos mantido apenas em memória."""

    cnjs_reais: Set[str] = field(default_factory=set)
    prefixos_cnj_reais: Set[str] = field(default_factory=set)
    cpfs_reais: Set[str] = field(default_factory=set)
    cpfs_digitos_reais: Set[str] = field(default_factory=set)
    nomes_completos_reais: Set[str] = field(default_factory=set)
    bigramas_nomes_reais: Set[str] = field(default_factory=set)
    tokens_nomes_reais: Set[str] = field(default_factory=set)
    segredos_reais: Set[str] = field(default_factory=set)
    _regex_tokens: Optional[re.Pattern[str]] = field(default=None, init=False, repr=False)

    def __repr__(self) -> str:  # pragma: no cover - proteção contra impressão acidental
        return (
            f"<DicionarioSigilo cnjs={len(self.cnjs_reais)} "
            f"cpfs={len(self.cpfs_reais)} nomes={len(self.nomes_completos_reais)} "
            f"tokens={len(self.tokens_nomes_reais)} segredos={len(self.segredos_reais)}>"
        )

    __str__ = __repr__

    @property
    def total_termos(self) -> int:
        return (
            len(self.cnjs_reais)
            + len(self.cpfs_reais)
            + len(self.nomes_completos_reais)
            + len(self.bigramas_nomes_reais)
            + len(self.tokens_nomes_reais)
            + len(self.segredos_reais)
        )

    def adicionar_cnj(self, cnj: str) -> None:
        limpo = (cnj or "").strip()
        if not RE_CNJ.fullmatch(limpo):
            return
        if limpo in CNJS_PUBLICOS_OFICIAIS:
            return
        self.cnjs_reais.add(limpo)
        prefixo = limpo[:10]
        if prefixo not in {c[:10] for c in CNJS_PUBLICOS_OFICIAIS}:
            self.prefixos_cnj_reais.add(prefixo)

    def adicionar_cpf(self, cpf: str) -> None:
        digitos = re.sub(r"\D", "", cpf or "")
        if len(digitos) != 11 or len(set(digitos)) == 1:
            return
        formatado = f"{digitos[:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:]}"
        self.cpfs_reais.add(formatado)
        self.cpfs_digitos_reais.add(digitos)

    def adicionar_nome(self, nome: str, *, forcar_tokens: bool = False) -> None:
        if not isinstance(nome, str):
            return
        limpo = re.sub(r"\s+", " ", nome).strip(" .,;:-()[]\"'_0123456789")
        if len(limpo) < 4:
            return
        norm = normalizar_texto(limpo)
        partes = [p for p in re.split(r"[^a-z]+", norm) if p and p not in STOPWORDS_NOME]
        if not partes:
            return
        if any(p in TERMOS_INSTITUCIONAIS for p in partes):
            return
        if len(partes) >= 2:
            nome_unido = " ".join(partes)
            # Não adiciona se todos os tokens forem do caso demo/fixtures conhecidos
            if not all(p in TOKENS_COMUNS_OU_FICTICIOS for p in partes) or forcar_tokens:
                self.nomes_completos_reais.add(nome_unido)
            for a, b in zip(partes, partes[1:]):
                if len(a) >= 3 and len(b) >= 3:
                    if forcar_tokens or not (a in TOKENS_COMUNS_OU_FICTICIOS and b in TOKENS_COMUNS_OU_FICTICIOS):
                        self.bigramas_nomes_reais.add(f"{a} {b}")
        for p in partes:
            if len(p) >= 6 and (forcar_tokens or p not in TOKENS_COMUNS_OU_FICTICIOS):
                self.tokens_nomes_reais.add(p)
        self._regex_tokens = None

    def adicionar_segredo(self, valor: str) -> None:
        if not isinstance(valor, str):
            return
        limpo = valor.strip()
        if len(limpo) >= 8:
            self.segredos_reais.add(limpo)

    def regex_tokens(self) -> Optional[re.Pattern[str]]:
        if not self.tokens_nomes_reais:
            return None
        if self._regex_tokens is None:
            ordenados = sorted(self.tokens_nomes_reais, key=len, reverse=True)
            padrao = r"\b(?:" + "|".join(re.escape(t) for t in ordenados) + r")\b"
            self._regex_tokens = re.compile(padrao)
        return self._regex_tokens


def _coletar_texto_forense_em_memoria(texto: str, dic: DicionarioSigilo) -> None:
    if not texto:
        return
    for m in RE_CNJ.finditer(texto):
        dic.adicionar_cnj(m.group(0))
    for m in RE_CNJ_FLEX.finditer(texto):
        dic.adicionar_cnj(f"{m.group(1)}.{m.group(2)}.{m.group(3)}.{m.group(4)}.{m.group(5)}")
    for m in RE_CPF_FORMATADO.finditer(texto):
        dic.adicionar_cpf(m.group(0))
    padroes_nomes = (
        r"(?:Acusad[oa]|R[ée]u|Autor(?:a)? do Fato|Investigad[oa]|Indiciad[oa]|Denunciad[oa]|V[íi]tima|Ofendid[oa]|Testemunha|Depoente)\s*[:\-]\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-záéíóúâêôãõç]+(?:\s+(?:de|da|do|das|dos|e|[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-záéíóúâêôãõç]+)){1,6})",
        r"(?:den[úu]ncia contra|em desfavor de|em face de|contra o r[ée]u|contra o acusado)\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,}(?:\s+(?:DE|DA|DO|DAS|DOS|E|[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,})){1,6})",
        r"Depoimento da V[íi]tima\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-záéíóúâêôãõç]+(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-záéíóúâêôãõç]+){0,5})",
        r"Depoimento da Testemunha(?:\s+PM)?\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-záéíóúâêôãõç]+(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-záéíóúâêôãõç]+){0,5})",
        r"Interrogat[óo]rio(?:\s+d[oa]\s+(?:R[ée]u|Acusad[oa]))?\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-záéíóúâêôãõç]+(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-záéíóúâêôãõç]+){0,5})",
    )
    for pat in padroes_nomes:
        for m in re.finditer(pat, texto):
            dic.adicionar_nome(m.group(1))


def _extrair_json_em_memoria(obj: object, dic: DicionarioSigilo) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if any(
                chave in kl
                for chave in (
                    "nome", "vitima", "ofendida", "reu", "acusado", "autor",
                    "investigado", "depoente", "parte", "polo_ativo", "polo_passivo",
                    "testemunha",
                )
            ):
                if isinstance(v, str):
                    dic.adicionar_nome(v)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, str):
                            dic.adicionar_nome(item)
                        else:
                            _extrair_json_em_memoria(item, dic)
            elif "cpf" in kl and isinstance(v, str):
                dic.adicionar_cpf(v)
            _extrair_json_em_memoria(v, dic)
    elif isinstance(obj, list):
        for item in obj:
            _extrair_json_em_memoria(item, dic)
    elif isinstance(obj, str):
        _coletar_texto_forense_em_memoria(obj, dic)


def descobrir_raizes_locais(raiz_alvo: Optional[Path] = None, extras: Sequence[Path] = ()) -> List[Path]:
    """Localiza pastas locais que possam conter `data/cases/` e `Modelos de Sentenças/`."""
    candidatas: List[Path] = []
    for p in extras:
        if p:
            candidatas.append(Path(p).resolve())
    if raiz_alvo:
        alvo = Path(raiz_alvo).resolve()
        candidatas.extend([alvo, alvo.parent / "v2.1.0", alvo.parent.parent / "v2.1.0"])
    candidatas.extend([
        RAIZ_SCRIPT,
        RAIZ_SCRIPT.parent / "v2.1.0",
        RAIZ_SCRIPT.parent.parent / "v2.1.0",
    ])
    try:
        proc = subprocess.run(
            ["git", "-C", str(RAIZ_SCRIPT), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        for linha in proc.stdout.splitlines():
            if linha.startswith("worktree "):
                candidatas.append(Path(linha[len("worktree ") :].strip()).resolve())
    except Exception:
        pass

    vistas: Set[Path] = set()
    resultado: List[Path] = []
    for c in candidatas:
        try:
            r = c.resolve()
        except OSError:
            continue
        if r not in vistas and r.is_dir():
            vistas.add(r)
            resultado.append(r)
    return resultado


def carregar_dicionario_sigilo_local(
    raizes_locais: Optional[Sequence[Path]] = None,
) -> DicionarioSigilo:
    """Monta em memória o conjunto de nomes, números de processo, CPFs e segredos reais."""
    dic = DicionarioSigilo()
    raizes = list(raizes_locais) if raizes_locais is not None else descobrir_raizes_locais()

    for raiz in raizes:
        raiz = Path(raiz)
        # 1. data/cases (incluindo caches JSON, nomes de arquivo e zips arquivados)
        cases_dir = raiz / "data" / "cases"
        if cases_dir.is_dir():
            for p in cases_dir.rglob("*"):
                _coletar_texto_forense_em_memoria(p.name, dic)
                if not p.is_file():
                    continue
                suf = p.suffix.lower()
                if suf == ".json":
                    try:
                        bruto = p.read_text(encoding="utf-8", errors="replace")
                        _coletar_texto_forense_em_memoria(bruto, dic)
                        _extrair_json_em_memoria(json.loads(bruto), dic)
                    except Exception:
                        pass
                elif suf == ".txt":
                    try:
                        bruto = p.read_text(encoding="utf-8", errors="replace")
                        _coletar_texto_forense_em_memoria(bruto, dic)
                    except Exception:
                        pass
                elif suf == ".zip":
                    try:
                        with zipfile.ZipFile(p) as zf:
                            for nome_interno in zf.namelist():
                                _coletar_texto_forense_em_memoria(nome_interno, dic)
                                if nome_interno.lower().endswith(".json"):
                                    bruto = zf.read(nome_interno).decode("utf-8", errors="replace")
                                    _coletar_texto_forense_em_memoria(bruto, dic)
                                    _extrair_json_em_memoria(json.loads(bruto), dic)
                    except Exception:
                        pass

        # 2. Modelos de Sentenças (.docx, .txt, .json e nomes de arquivos)
        modelos_dir = raiz / "Modelos de Sentenças"
        if modelos_dir.is_dir():
            for p in modelos_dir.rglob("*"):
                _coletar_texto_forense_em_memoria(p.name, dic)
                if not p.is_file():
                    continue
                suf = p.suffix.lower()
                if suf == ".docx":
                    try:
                        import docx

                        doc = docx.Document(str(p))
                        blocos = [par.text for par in doc.paragraphs]
                        for tab in doc.tables:
                            for row in tab.rows:
                                for cell in row.cells:
                                    blocos.append(cell.text)
                        _coletar_texto_forense_em_memoria("\n".join(blocos), dic)
                    except Exception:
                        pass
                elif suf in (".txt", ".json"):
                    try:
                        bruto = p.read_text(encoding="utf-8", errors="replace")
                        _coletar_texto_forense_em_memoria(bruto, dic)
                    except Exception:
                        pass
                elif suf == ".pdf" and "manual" not in p.name.lower():
                    try:
                        import pymupdf

                        doc_pdf = pymupdf.open(str(p))
                        for pg in doc_pdf:
                            _coletar_texto_forense_em_memoria(pg.get_text("text"), dic)
                        doc_pdf.close()
                    except Exception:
                        pass

        # 3. Amostras do gabinete
        amostras_dir = raiz / "data" / "templates_gabinete" / "amostras"
        if amostras_dir.is_dir():
            for p in amostras_dir.rglob("*.txt"):
                try:
                    _coletar_texto_forense_em_memoria(p.read_text(encoding="utf-8", errors="replace"), dic)
                except Exception:
                    pass

        # 4. Cofre de segredos local
        secrets_dir = raiz / "data" / "secrets"
        if secrets_dir.is_dir():
            for p in secrets_dir.rglob("*.json"):
                try:
                    dados = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                    if isinstance(dados, dict):
                        for v in dados.values():
                            if isinstance(v, str):
                                dic.adicionar_segredo(v)
                except Exception:
                    pass

    return dic


def _relativo_posix(caminho: Path, raiz: Path) -> str:
    rel = caminho.resolve().relative_to(raiz.resolve()).as_posix()
    return unicodedata.normalize("NFC", rel)


def _eh_arquivo_proibido_na_publicacao(rel_posix: str) -> bool:
    rel_lower = rel_posix.lower()
    for pref in PREFIXOS_PROIBIDOS_PUBLICACAO:
        pl = unicodedata.normalize("NFC", pref).lower().rstrip("/")
        if rel_lower == pl or rel_lower.startswith(pl + "/"):
            return True
    nome = rel_posix.rsplit("/", 1)[-1].lower()
    for pat in PADROES_PROIBIDOS_PUBLICACAO:
        if fnmatch.fnmatchcase(nome, pat.lower()):
            return True
    # Em data/, somente data/knowledge_base/acervo_completo.json é permitido na publicação
    if rel_lower.startswith("data/") and rel_lower != "data/knowledge_base/acervo_completo.json":
        return True
    if rel_lower not in ARQUIVOS_BINARIOS_PUBLICOS_PERMITIDOS:
        for ext in EXTENSOES_BINARIAS_SIGILOSAS:
            if nome.endswith(ext):
                return True
    return False


def _linha_define_regex_caminho(linha: str) -> bool:
    """Evita que a própria definição da regex em build_release/verificar_sigilo se autoacuse."""
    return (
        "(?!(?:Public|Default" in linha
        or "(?!Shared\\b" in linha
        or "<nome>" in linha
        or "<usuario>" in linha
    )


def analisar_linha(
    linha: str,
    *,
    dicionario: DicionarioSigilo,
    rel_posix: str = "",
) -> List[str]:
    """Devolve a lista de tipos de achados na linha (sem retornar o trecho encontrado)."""
    tipos: List[str] = []
    if not linha:
        return tipos

    # 1. Segredos do cofre local
    for seg in dicionario.segredos_reais:
        if seg in linha:
            tipos.append("SEGREDO_COFRE")
            break

    # 2. Chaves de API e tokens
    if RE_CHAVE_API.search(linha):
        tipos.append("CHAVE_API_OU_TOKEN")

    # 3. CPF (real em memória ou padrão regex)
    achou_cpf = False
    for cpf in dicionario.cpfs_reais:
        if cpf in linha:
            tipos.append("CPF_REAL")
            achou_cpf = True
            break
    if not achou_cpf:
        for dig in dicionario.cpfs_digitos_reais:
            if dig in linha:
                tipos.append("CPF_REAL")
                achou_cpf = True
                break
    if not achou_cpf and (RE_CPF_FORMATADO.search(linha) or RE_CPF_CONTEXTO.search(linha)):
        tipos.append("CPF")

    # 4. Número CNJ (real em memória ou CNJ não documentado como fictício/público)
    achou_cnj_real = False
    for pref in dicionario.prefixos_cnj_reais:
        if pref in linha:
            tipos.append("NUMERO_CNJ_REAL")
            achou_cnj_real = True
            break
    if not achou_cnj_real:
        for m in RE_CNJ.finditer(linha):
            cnj = m.group(0)
            if cnj in dicionario.cnjs_reais:
                tipos.append("NUMERO_CNJ_REAL")
                break
            if cnj not in CNJS_FICTICIOS_E_PUBLICOS_DOCUMENTADOS:
                tipos.append("NUMERO_CNJ_NAO_FICTICIO")
                break

    # 5. Nome de parte real (nome completo, bigrama ou token distintivo)
    ln_norm = normalizar_texto(linha)
    achou_nome = False
    for nm in dicionario.nomes_completos_reais:
        if nm in ln_norm:
            achou_nome = True
            break
    if not achou_nome:
        for bg in dicionario.bigramas_nomes_reais:
            if bg in ln_norm:
                achou_nome = True
                break
    if not achou_nome:
        rx_tok = dicionario.regex_tokens()
        if rx_tok is not None and rx_tok.search(ln_norm):
            achou_nome = True
    if achou_nome:
        tipos.append("NOME_PARTE_REAL")

    # 6. E-mail
    if RE_EMAIL.search(linha):
        tipos.append("EMAIL")

    # 7. Telefone
    if RE_TELEFONE.search(linha):
        tipos.append("TELEFONE")

    # 8. Caminho de usuário (C:\Users\<nome> ou /Users/<nome>)
    if not _linha_define_regex_caminho(linha) and RE_CAMINHO_USUARIO.search(linha):
        tipos.append("CAMINHO_USUARIO")

    return tipos


def verificar_arvore(
    pasta: Path,
    *,
    dicionario: Optional[DicionarioSigilo] = None,
    raizes_dados_locais: Optional[Sequence[Path]] = None,
    apenas_padroes: bool = False,
) -> Tuple[List[AchadoSigilo], int, int]:
    """Varre ``pasta`` e devolve ``(achados, arquivos_verificados, total_termos_memoria)``.

    Nunca inclui o texto encontrado em ``AchadoSigilo``.
    """
    pasta = Path(pasta).resolve()
    if dicionario is None:
        if apenas_padroes:
            dicionario = DicionarioSigilo()
        else:
            raizes = (
                list(raizes_dados_locais)
                if raizes_dados_locais is not None
                else descobrir_raizes_locais(pasta)
            )
            dicionario = carregar_dicionario_sigilo_local(raizes)

    achados: List[AchadoSigilo] = []
    arquivos_verificados = 0

    for caminho in sorted(pasta.rglob("*")):
        if caminho.is_dir():
            continue
        rel_partes = caminho.relative_to(pasta).parts
        if any(parte in PASTAS_IGNORADAS_VARREDURA for parte in rel_partes):
            continue

        rel_posix = _relativo_posix(caminho, pasta)
        arquivos_verificados += 1

        # 1. Verifica se o próprio caminho é proibido na árvore de publicação
        if _eh_arquivo_proibido_na_publicacao(rel_posix):
            achados.append(AchadoSigilo(rel_posix, 0, "ARQUIVO_PROIBIDO_NA_PUBLICACAO"))

        # 2. Verifica se o nome do arquivo contém número CNJ real, CPF ou nome de parte real
        for tipo_nome in analisar_linha(caminho.name, dicionario=dicionario, rel_posix=rel_posix):
            achados.append(AchadoSigilo(rel_posix, 0, tipo_nome))

        # 3. Varre o conteúdo linha a linha
        suf = caminho.suffix.lower()
        if suf in EXTENSOES_TEXTO_VARREDURA or caminho.name in (".gitignore", "LICENSE", "README", "Makefile"):
            try:
                texto = caminho.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for num_linha, linha in enumerate(texto.splitlines(), start=1):
                for tipo in analisar_linha(linha, dicionario=dicionario, rel_posix=rel_posix):
                    achados.append(AchadoSigilo(rel_posix, num_linha, tipo))
        elif suf == ".pdf":
            try:
                import pymupdf

                doc_pdf = pymupdf.open(str(caminho))
                linhas_pdf = "\n".join(pg.get_text("text") for pg in doc_pdf).splitlines()
                doc_pdf.close()
                for num_linha, linha in enumerate(linhas_pdf, start=1):
                    for tipo in analisar_linha(linha, dicionario=dicionario, rel_posix=rel_posix):
                        achados.append(AchadoSigilo(rel_posix, num_linha, tipo))
            except Exception:
                # Falha fechada: um PDF que não pode ser lido não pode ser considerado verificado.
                achados.append(AchadoSigilo(rel_posix, 0, "PDF_NAO_VERIFICAVEL"))

    return achados, arquivos_verificados, dicionario.total_termos


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verifica sigilo e ausência de dados de processos reais/segredos na árvore."
    )
    parser.add_argument(
        "pasta",
        nargs="?",
        default=str(RAIZ_SCRIPT),
        help="Pasta a verificar (padrão: raiz do projeto onde o script está).",
    )
    parser.add_argument(
        "--raiz-dados-locais",
        action="append",
        default=[],
        help="Pasta local adicional de onde carregar termos sigilosos em memória.",
    )
    parser.add_argument(
        "--apenas-padroes",
        action="store_true",
        help="Executa apenas a verificação por padrões/regex e lista proibida (modo CI).",
    )
    args = parser.parse_args(argv)

    pasta_alvo = Path(args.pasta).resolve()
    if not pasta_alvo.is_dir():
        print(f"ERRO: pasta inexistente: {pasta_alvo}", file=sys.stderr)
        return 2

    extras = [Path(p) for p in args.raiz_dados_locais]
    raizes = None if args.apenas_padroes else descobrir_raizes_locais(pasta_alvo, extras)
    achados, total_arqs, total_termos = verificar_arvore(
        pasta_alvo,
        raizes_dados_locais=raizes,
        apenas_padroes=args.apenas_padroes,
    )

    for achado in achados:
        print(achado.formatar())

    if achados:
        arqs_com_achado = len({a.arquivo for a in achados})
        print(
            f"VERIFICACAO DE SIGILO REPROVADA: {len(achados)} achado(s) em "
            f"{arqs_com_achado} arquivo(s) ({total_arqs} verificados, "
            f"{total_termos} termos locais em memoria)."
        )
        return 1

    print(
        f"VERIFICACAO DE SIGILO APROVADA: 0 achados "
        f"({total_arqs} arquivos verificados, {total_termos} termos locais em memoria)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
