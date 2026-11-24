# CONTEXT.MD — Contexto Técnico, Operacional e de Domínio Jurídico
## Sistema Especialista de Sentenças Penais — JVDFM (v2.0.0)

Este documento descreve o ambiente de execução suportado, a arquitetura do repositório, a superfície
HTTP local e o domínio jurídico. Caminhos são relativos à raiz do programa.

---

### 1. Ambiente de execução

- **Sistemas suportados:** Windows 10/11 (x64) e macOS 13+ (Apple Silicon e Intel). Linux funciona
  pelo mesmo `instalador/bootstrap.sh`, sem garantia de suporte.
- **Python:** 3.12 gerenciado pelo `uv` (instalado pelo iniciador no perfil do usuário; o projeto aceita
  `>=3.11,<3.15`). Dependências travadas em `uv.lock`, ambiente em `.venv`.
- **Extras opcionais** (`pyproject.toml`): `transcricao` (faster-whisper), `ocr-windows` (winocr),
  `ocr-macos` (ocrmac), `nuvem` (google-genai, SpeechRecognition, imageio-ffmpeg) e `seguranca`
  (keyring). Todos são importados de forma preguiçosa; na ausência, o recurso fica indisponível de
  forma explícita (nunca simulado).
- **GPU:** opcional. `app/core/platform_utils.get_gpu_status()` mede via `nvidia-smi` quando existe;
  no Mac informa `apple_silicon`/`cpu` sem números inventados.
- **IA local (Ollama):** opcional, em `http://127.0.0.1:11434`; o modelo vem de
  `data/user_settings.json` (`ollama_model`, padrão `gemma4:e2b`). Sem Ollama, o motor
  determinístico redige a minuta.
- **Servidor web local:** FastAPI + Uvicorn somente em `127.0.0.1`, porta `SENTENCIA_PORT` (padrão 8000;
  o iniciador escolhe a próxima livre se estiver ocupada).
- **Iniciadores:**
  - Windows: `Iniciar SentencIA.bat` → `instalador/bootstrap.ps1` → `launcher.py`;
    `Iniciar_SentencIA.vbs` (usado pelo atalho) roda o mesmo fluxo sem janela quando já instalado.
  - macOS: `Iniciar SentencIA.command` ou `SentencIA.app` → `instalador/bootstrap.sh` →
    `launcher.py`.
  - `launcher.py` usa o Python do `.venv`, aguarda `GET /api/health` e abre o navegador (Chrome/Edge/
    Brave em modo aplicativo quando houver). `launcher.pyw` é só um shim.
- **Registros:** `data/logs/` — `bootstrap.log`, `launcher.log`, `servidor.log`, `app.log` (rotativo),
  `ollama.log`.
- **Variáveis de ambiente:** `SENTENCIA_PORT`, `SENTENCIA_DATA_DIR` (reloca `data/`), `SENTENCIA_OCR=desligado`,
  `SENTENCIA_SECRETS_BACKEND=arquivo`, `SENTENCIA_ALLOWED_HOSTS`, `SENTENCIA_REAL_CASES_DIR` (testes com autos
  reais), `SENTENCIA_DISABLE_TOKEN=1` (só sob pytest). Prefixos legados `JVDFM_*` seguem aceitos com aviso.

---

### 2. Estrutura de diretórios

```text
<raiz do programa>/
├── app/
│   ├── api/                   # Camada HTTP: token/Host/Origin (security), modelos de requisição,
│   │                          # mapeamento de erros, jobs de upload e filtro de mídias
│   ├── core/                  # Configurações, estado da sessão, CaseContext, cofre de segredos,
│   │                          # cliente Ollama, utilitários de plataforma e de texto, erros de domínio
│   ├── demo/                  # Caso demonstrativo (flag is_demo) e texto único da sentença demo
│   ├── export/                # Word (.docx ABNT), HTML PJe e PDF, com marca d'água de rascunho
│   ├── generation/            # Gerador monolítico, pipeline seccional, duplo crivo, crítico
│   │                          # adversarial, auditor anti-alucinação, Gemini e script de caso
│   ├── ingestion/             # Segmentação do PDF do PJe (OCR por plataforma) e classificador Laya
│   ├── knowledge/             # Base fechada de precedentes e clonador de estilo do gabinete
│   ├── legal_engine/          # Dosimetria trifásica, regime, prescrição por crime e catálogo penal
│   ├── static/                # Interface (index.html, js/app.js, css/design-tokens.css, assets)
│   └── transcription/         # Transcrição (Whisper local primeiro) e análise da prova oral
├── data/
│   ├── cases/                 # Autos ativos, mídias e caches (SIGILOSO; nunca distribuído)
│   │   └── _arquivados/       # Arquivo morto: <timestamp>.zip + indice_hashes.json
│   ├── exports/ logs/ secrets/ jobs/   # Gerados em uso (nunca distribuídos)
│   ├── knowledge_base/        # Base fechada de jurisprudência (acervo_completo.json)
│   └── templates_gabinete/    # Timbre e perfil de estilo (amostras/ não é distribuída)
├── instalador/                # bootstrap.ps1 (Windows) e bootstrap.sh (macOS/Linux)
├── SentencIA.app/        # Bundle macOS que abre o iniciador no Terminal
├── scripts/                   # build_release.py (pacote) e criar_atalho_desktop.py
├── tests/                     # Suíte pytest (unitários, integração, adversariais e e2e/)
├── docs/                      # context.md, agents.md, roadmap.md, DESIGN.md
├── Iniciar SentencIA.bat / .command, Iniciar_SentencIA.vbs
├── launcher.py, launcher.pyw
├── pyproject.toml, uv.lock
├── README.md, CHANGELOG.md
└── Modelos de Sentenças/      # Material de referência do gabinete (NÃO distribuído)
```

O pacote de distribuição (`scripts/build_release.py`) exclui `data/cases`, `data/user_settings.json`,
`data/secrets`, `data/logs`, `data/exports`, `data/jobs`, `data/templates_gabinete/amostras`,
`Modelos de Sentenças`, `docs/design`, `tests`, caches e ambientes virtuais.

---

### 2.1. Superfície HTTP local (FastAPI em `127.0.0.1:<porta>`)

Todas as rotas `/api/*` exigem o cabeçalho `X-SentencIA-Token` (lido de
`<meta name="sentencia-token">` em `GET /`), exceto `GET /api/health` (aceita `X-JVDFM-Token` como legado).

| Método | Rota | Função |
|:---:|---|---|
| GET | `/api/health` | Público e mínimo: `{status, versao}` |
| GET | `/api/status` | Hardware medido, Ollama, OCR, transcrição, privacidade, ingestão e sessão |
| GET/POST | `/api/settings` | Preferências (POST parcial; a chave do Gemini vai para o cofre) |
| GET/POST/DELETE | `/api/secrets/gemini` | Chave do Gemini no cofre (GET só informa se está configurada) |
| GET | `/api/cases/active` | Caso ativo, sem efeitos colaterais |
| POST | `/api/cases/load-demo` | Carrega o caso demonstrativo (`is_demo: true`) |
| POST | `/api/cases/load-from-disk` | Segmenta/transcreve os autos já presentes em `data/cases` |
| POST | `/api/cases/upload-files` · `/upload-pdfs` · `/upload-medias` | Upload em streaming (limite 413, lock 409, modo assíncrono 202) |
| GET | `/api/cases/upload-status` · `/upload-progress` · `/api/jobs/{id}` | Progresso e resultado da ingestão |
| POST | `/api/cases/metadata-override` | Complementação de metadados pelo juiz |
| POST | `/api/cases/reset` | Zerar produção ou limpar tudo (arquiva em zip; exclusão definitiva só com confirmação) |
| POST | `/api/dosimetria/simulate` | Simulação trifásica, regime por espécie e prescrição por crime |
| POST | `/api/sentence/generate` · `/generate-sectional` | Geração (caso real exige veredicto por crime) |
| POST | `/api/sentence/regenerate-section` · `/update-and-audit` | Regeneração de seção e edição direta auditada |
| GET | `/api/sentence/last` | Última minuta e auditoria |
| GET | `/api/export/docx` · `/pdf` · `/html` | Exportação (auditoria aprovada ou `?rascunho=true` com marca d'água) |
| POST | `/api/gemini/build-script` · `/generate` · `/import-sentence` | Fluxo Gemini (consentimento obrigatório; pseudonimização com mapa e hash) |
| GET/POST | `/api/knowledge` · `/api/knowledge/precedente` · `/style-sample` | Base fechada e cofre estilístico |

Erros de domínio: 422 `dados_insuficientes` / `decisao_judicial_pendente` (com `campos` e
`crimes_pendentes`), 403 `envio_externo_nao_autorizado`, 503 `gemini_indisponivel`, 409 para
concorrência/sessão alterada, 401 `token_invalido`.

### 2.2. Reset de produção

- **Zerar produção (manter autos):** elimina minuta, auditoria, dosimetria, telemetria e (opcionalmente)
  exportações; permite nova minuta para o mesmo processo.
- **Limpar tudo — novo processo:** além do anterior, arquiva autos, mídias, transcrições e caches em
  `data/cases/_arquivados/<timestamp>.zip`, com deduplicação por SHA-256. A exclusão definitiva (sem
  arquivar) e a remoção do arquivo morto exigem `confirmar_exclusao_definitiva=true`.
- Invariantes: `data/knowledge_base/*`, `data/templates_gabinete/*` e `data/user_settings.json` nunca
  são tocados; `generation_id` é monotônico; upload inválido não destrói a produção em curso.

---

### 3. Contexto do Domínio Jurídico Especializado

#### Competência Jurisdicional
- **Juizado da Violência Doméstica e Familiar contra a Mulher (JVDFM):** Competência híbrida/especializada definida pelos artigos 14 e seguintes da Lei nº 11.340/2006 (Lei Maria da Penha).
- **Circunscrição Exemplar:** Comarca de Natal / Tribunal de Justiça do Estado do Rio Grande do Norte (TJRN).

#### Legislação de Regência Aplicada
1. **Constituição Federal de 1988:** Art. 5º, XLVI (individualização da pena), art. 93, IX (dever de fundamentação), art. 226, § 8º (dever do Estado de coibir a violência no âmbito familiar).
2. **Lei nº 11.340/2006 (Lei Maria da Penha):** Art. 5º e 7º (formas de violência), art. 21 (intimação obrigatória da vítima sobre todos os atos processuais), art. 24-A (crime de descumprimento de medidas protetivas de urgência), art. 41 (afastamento da Lei nº 9.099/1995).
3. **Lei nº 14.994/2024 (Pacote Antifeminicídio):** Alterações substanciais em vigor a partir de 10/10/2024:
   - Art. 129, § 13, CP: reclusão de 2 a 5 anos;
   - Art. 24-A da Lei 11.340/06: reclusão de 2 a 5 anos e multa;
   - Art. 147, § 2º, CP: pena de ameaça duplicada se contra mulher em razão do gênero e ação incondicionada.
4. **Código Penal (Decreto-Lei nº 2.848/1940):** Art. 59 (8 circunstâncias judiciais na 1ª fase), Art. 68 (método trifásico Nelson Hungria), Art. 61, II, 'f' (agravante genérica de violência de gênero com trava de bis in idem), Art. 67 (concurso de agravantes e atenuantes), Art. 69 (concurso material e cúmulo de penas), Art. 33 (regime inicial, por espécie de pena), Art. 44 (substituição da pena privativa de liberdade), Art. 77 (sursis, exigindo circunstâncias judiciais favoráveis), Arts. 109, 110, § 1º, 115 e 119 (prescrição por crime, com a Lei nº 12.234/2010).
5. **Código de Processo Penal (Decreto-Lei nº 3.689/1941):** Art. 381 (requisitos intrínsecos da sentença: relatório, fundamentação com enfrentamento de todas as teses da defesa e dispositivo), Art. 386 (hipóteses de absolvição, com o inciso indicado pelo juízo), Art. 387, IV (fixação de valor mínimo para reparação dos danos; pedido sem valor fica com arbitramento pendente).
6. **Resolução CNJ nº 492/2023:** Protocolo para Julgamento com Perspectiva de Gênero obrigatório no Poder Judiciário brasileiro. Proíbe expressamente estereótipos de gênero, vitimização secundária, desqualificação moral da palavra da mulher e valoração negativa do comportamento da vítima.
