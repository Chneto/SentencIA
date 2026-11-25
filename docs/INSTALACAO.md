# Guia de Instalação — SentencIA 2.3.1 (Windows e macOS)

> **Aplicação web local — 100% no seu computador.**
> O SentencIA abre no seu navegador (Chrome, Edge, Brave ou Safari), mas o servidor roda **exclusivamente
> na sua máquina** (`127.0.0.1`). Nenhum processo judicial é hospedado na internet nem sai do computador.

---

## Sumário

1. [Requisitos mínimos](#1-requisitos-mínimos)
2. [Instalação no Windows (Comando recomendado ou instalador `.exe`)](#2-instalação-no-windows)
3. [Instalação no macOS (Comando único no Terminal, sem conta Apple)](#3-instalação-no-macos)
4. [Primeira execução e Assistente de Configuração](#4-primeira-execução-e-assistente-de-configuração)
5. [IA Local (Ollama)](#5-ia-local-ollama)
6. [OCR Nativo (páginas digitalizadas do PJe)](#6-ocr-nativo-páginas-digitalizadas-do-pje)
7. [Onde ficam os dados do gabinete](#7-onde-ficam-os-dados-do-gabinete)
8. [Como atualizar e como desinstalar](#8-como-atualizar-e-como-desinstalar)
9. [Privacidade e Segredo de Justiça (o que nunca sai da máquina)](#9-privacidade-e-segredo-de-justiça)

---

## 1. Requisitos mínimos

| Item | Windows | macOS |
| :--- | :--- | :--- |
| **Sistema operacional** | Windows 10 ou 11 (64 bits — x64 ou ARM64) | macOS 13 (Ventura) ou superior — Apple Silicon (M1/M2/M3/M4) ou Intel |
| **Memória RAM** | 8 GB (16 GB recomendados para transcrição e IA local) | 8 GB (16 GB recomendados) |
| **Espaço em disco** | ~3 GB para o programa e dependências (+2 a 5 GB se usar Ollama) | ~3 GB para o programa e dependências (+2 a 5 GB se usar Ollama) |
| **Permissão de administrador** | **Não é necessária** (instala no perfil do usuário) | **Não é necessária** (sem `sudo`) |
| **Internet** | Apenas na instalação inicial (e ao baixar modelo do Ollama, opcional) | Apenas na instalação inicial |

Você não precisa instalar Python, bibliotecas ou ferramentas antes: o instalador configura automaticamente o gerenciador `uv`, o **Python 3.12** isolado e todas as dependências do SentencIA.

---

## 2. Instalação no Windows

O SentencIA oferece **dois caminhos de instalação** no Windows (ambos sem exigir conta de administrador):

### Caminho A (Recomendado): 1 comando no PowerShell (sem aviso do SmartScreen)

1. No menu Iniciar do Windows, digite **PowerShell** e abra o **Windows PowerShell** (não precisa ser como Administrador).
2. Copie e cole o comando abaixo e pressione **Enter**:

```powershell
irm https://github.com/Chneto/SentencIA/releases/latest/download/instalar.ps1 | iex
```

**O que este comando faz automaticamente:**
- Baixa o pacote oficial da versão mais recente (`SentencIA-v2.3.1.zip`) e o arquivo `SHA256SUMS.txt`;
- Confere a integridade criptográfica do pacote (**SHA-256**) antes de extrair;
- Instala o programa em `%LOCALAPPDATA%\Programs\SentencIA`;
- Configura a pasta de dados do gabinete fora do programa, em `%LOCALAPPDATA%\SentencIA\data`;
- Executa o `bootstrap.ps1` (`uv` + Python 3.12 + OCR nativo `winocr` + transcrição `faster-whisper`);
- Cria os atalhos **SentencIA** na Área de Trabalho e no Menu Iniciar e registra o desinstalador no Windows.

### Caminho B: Instalador gráfico `SentencIA-Setup-2.3.1.exe` (com aviso do SmartScreen)

Se preferir baixar o instalador `.exe` na página de *Releases* do GitHub:

1. Baixe `SentencIA-Setup-2.3.1.exe` e dê dois cliques nele.
2. **Aviso do Windows SmartScreen:** como o instalador é distribuído gratuitamente sem certificado comercial pago (EV/OV), o Windows exibirá a janela azul *"O Windows protegeu o computador"*:
   - Passo 1: clique no link sublinhado **Mais informações**;
   - Passo 2: confira o nome `SentencIA-Setup-2.3.1.exe` e clique no botão **Executar assim mesmo**.
3. Siga o assistente do instalador. Ele instala o programa no seu perfil de usuário (`%LOCALAPPDATA%\Programs\SentencIA`), preserva seus dados em `%LOCALAPPDATA%\SentencIA\data` e cria o atalho na Área de Trabalho.

### Alternativa portátil (pacote `.zip`)

Caso a rede do tribunal bloqueie downloads por linha de comando:
1. Baixe `SentencIA-v2.3.1.zip`, clique com o botão direito e escolha **Extrair Tudo...** (prefira uma pasta fora do OneDrive, como `C:\SentencIA`).
2. Abra a pasta extraída e dê dois cliques em **`Iniciar SentencIA.bat`**.

---

## 3. Instalação no macOS

Sem conta paga de desenvolvedor da Apple, pacotes `.app` ou `.dmg` baixados pelo navegador recebem a marca de quarentena do **Gatekeeper**. Para evitar o bloqueio e instalar sem `sudo`:

### Passo a passo em 1 comando no Terminal

1. Pressione **`Command (⌘) + Espaço`**, digite **Terminal** e aperte **Enter**.
2. Cole o comando abaixo e aperte **Enter**:

```bash
curl -fsSL https://github.com/Chneto/SentencIA/releases/latest/download/instalar-mac.sh | bash
```

**O que o instalador faz no Mac:**
- Verifica a arquitetura do Mac (**Apple Silicon arm64** ou **Intel x86_64**, saindo automaticamente do Rosetta se necessário);
- Baixa `SentencIA-v2.3.1.zip` e `SHA256SUMS.txt` e valida o hash **SHA-256**;
- Instala o programa em `~/Applications/SentencIA` e configura os dados em `~/Library/Application Support/SentencIA`;
- Configura `uv` + Python 3.12 + OCR nativo da Apple (`ocrmac`) + transcrição local (`faster-whisper`);
- Monta localmente o aplicativo **`~/Applications/SentencIA.app`** (com ícone `AppIcon.icns`), que abre pelo **Launchpad**, **Dock** ou **Finder** sem aviso de quarentena do Gatekeeper.

> **Instalação a partir de `.zip` já baixado (offline):**
> ```bash
> bash instalar-mac.sh --pacote-local ~/Downloads/SentencIA-v2.3.1.zip
> ```
> Para mais detalhes e solução de problemas específicos do macOS, consulte também [`docs/INSTALACAO_MAC.md`](INSTALACAO_MAC.md).

---

## 4. Primeira execução e Assistente de Configuração

Ao concluir a instalação (ou ao clicar no atalho **SentencIA**), o servidor local sobe em `127.0.0.1` e o navegador abre automaticamente:

- **Abertura limpa:** o estúdio inicia pronto para receber um processo real (ou reabre apenas o último processo real em que você estava trabalhando). Nenhum caso fictício é misturado aos seus autos.
- **Caso de demonstração opcional:** se quiser explorar o sistema antes de enviar autos reais, clique em **Carregar demonstração** (ou pelo menu do logo no canto superior esquerdo → **Carregar caso de demonstração**), que utiliza dados 100% fictícios.
- **Assistente de primeira execução:** na primeira abertura (e sempre que desejar pelo menu do logo → **Ajuda / Assistente**), o SentencIA mostra:
  1. O estado da **IA local (Ollama)** e o modelo recomendado para a sua memória/placa de vídeo;
  2. A disponibilidade do **motor de OCR nativo** (`winocr` no Windows / `ocrmac` no macOS);
  3. O resumo prático das **4 estações de trabalho** (*I. Autos & Mídias*, *II. Análise & Provas*, *III. Dosimetria* e *IV. Sentença*) e o link para o **[Guia do Usuário](GUIA_DO_USUARIO.md)**.

---

## 5. IA Local (Ollama)

O uso do **Ollama é opcional**:
- **Sem Ollama:** o SentencIA funciona integralmente com seu **motor determinístico forense**, gerando relatório, fundamentação, dosimetria trifásica e dispositivo auditados em 4 camadas.
- **Com Ollama:** a redação das seções pode ser refinada por modelos locais rodando 100% na sua máquina, sem enviar nenhum dado para a internet.

**Como instalar e configurar o Ollama:**
1. Baixe e instale pelo site oficial: [https://ollama.com/download](https://ollama.com/download).
2. No SentencIA, abra o menu do logo → **Configurações** (ou o assistente de primeira execução):
   - Placas de vídeo / memória unificada de **4 a 6 GB**: `gemma4:e2b` ou `qwen2.5:3b-instruct`;
   - Placas de vídeo / memória unificada de **8 GB ou mais**: `qwen2.5:7b-instruct`.
3. Se o seu computador apresentar picos de uso na placa de vídeo durante transcrições longas ou geração de texto, ative a opção **Modo GPU moderado** em **Configurações** (reduz o contexto para 4096 tokens, limita o uso de VRAM a ~70% e serializa Whisper e Ollama em fila única).

---

## 6. OCR Nativo (páginas digitalizadas do PJe)

Quando um PDF do PJe possui páginas digitalizadas como imagem (laudos, certidões ou termos escaneados), o SentencIA aplica **OCR seletivo local** com barra de progresso página a página e botão **Refazer OCR**:

- **No Windows (`winocr` / `Windows.Media.Ocr`):**
  Utiliza o reconhecimento óptico nativo do Windows para o idioma **Português (Brasil)**. Caso a faixa amarela *"OCR desativado"* apareça na Estação 1:
  1. Abra **Configurações do Windows → Hora e idioma → Idioma e região**;
  2. Em **Português (Brasil)**, clique nos três pontos (`...`) → **Opções de idioma** e instale **Reconhecimento óptico de caracteres**;
  3. *Alternativa rápida (PowerShell como Administrador):*
     ```powershell
     Add-WindowsCapability -Online -Name "Language.OCR~~~pt-BR~0.0.1.0"
     ```
- **No macOS (`ocrmac` / Apple Vision Framework):**
  Utiliza o motor Vision integrado ao macOS 13+; não requer pacote adicional.

---

## 7. Onde ficam os dados do gabinete

Para proteger os processos e permitir atualizações limpas, **os dados do usuário ficam separados da pasta do programa**:

| Item | Windows | macOS |
| :--- | :--- | :--- |
| **Pasta do programa** | `%LOCALAPPDATA%\Programs\SentencIA` | `~/Applications/SentencIA` e `~/Applications/SentencIA.app` |
| **Pasta de dados (`SENTENCIA_DATA_DIR`)** | `%LOCALAPPDATA%\SentencIA\data` | `~/Library/Application Support/SentencIA` |
| **Autos ativos e arquivo morto** | `<pasta de dados>\cases\` e `cases\_arquivados\` | `<pasta de dados>/cases/` e `cases/_arquivados/` |
| **Precedentes e estilo do gabinete** | `<pasta de dados>\knowledge_base\custom_precedentes.json` e `templates_gabinete\` | `<pasta de dados>/knowledge_base/custom_precedentes.json` e `templates_gabinete/` |
| **Configurações e logs** | `<pasta de dados>\user_settings.json` e `logs\` | `<pasta de dados>/user_settings.json` e `logs/` |
| **Chave da API Gemini (opcional)** | Gerenciador de Credenciais do Windows (*Credential Manager*) | Acesso às Chaves (*Keychain*) do macOS |

- **Migração segura de versões anteriores:** se o sistema encontrar uma pasta `data/` dentro de uma instalação antiga do programa, ele **copia** os arquivos para a nova pasta de dados, confere o hash SHA-256 de cada arquivo e mantém a pasta original intacta até que você decida apagá-la.
- **Fora da nuvem:** tanto `%LOCALAPPDATA%` quanto `~/Library/Application Support` ficam fora do OneDrive e do iCloud Drive, evitando sincronização acidental de autos sigilosos.

---

## 8. Como atualizar e como desinstalar

### Como atualizar para uma nova versão

- **Windows:** execute novamente o comando `irm .../instalar.ps1 | iex` no PowerShell ou rode o novo `SentencIA-Setup-<versão>.exe`.
- **macOS:** execute novamente o comando `curl -fsSL .../instalar-mac.sh | bash` no Terminal.

A atualização substitui apenas os arquivos do programa e sincroniza as bibliotecas, **preservando integralmente** seus autos, arquivo morto, precedentes do gabinete, modelos de formatação, configurações e chave do cofre.

### Como desinstalar

- **No Windows:**
  - Abra **Configurações do Windows → Aplicativos → Aplicativos instalados**, localize **SentencIA** e clique em **Desinstalar** (ou execute `instalador\desinstalar.ps1`).
  - A opção **"Manter meus dados do gabinete"** vem marcada por padrão para que nenhum processo ou configuração seja perdido.
- **No macOS:**
  - Execute `~/Applications/SentencIA/desinstalar.command` (dois cliques no Finder ou pelo Terminal).
  - O desinstalador encerra o servidor local, remove `~/Applications/SentencIA` e `~/Applications/SentencIA.app` e **mantém seus dados** em `~/Library/Application Support/SentencIA` por padrão.

---

## 9. Privacidade e Segredo de Justiça

O SentencIA foi projetado sob o princípio de **privacidade por padrão (segredo de justiça / LGPD)**:

| Dado / Operação | Sai do computador? | Onde é processado / armazenado |
| :--- | :---: | :--- |
| PDFs dos autos do PJe e OCR de páginas digitalizadas | **NUNCA** | 100% local (PyMuPDF + `winocr` / `ocrmac`) |
| Vídeos e áudios de audiências (`.mp4`, `.mp3`, `.m4a`, `.wav`) | **NUNCA** | 100% local (`faster-whisper` em CPU ou GPU NVIDIA) |
| Dosimetria trifásica, prescrição e auditoria em 4 camadas | **NUNCA** | 100% local (motores determinísticos Python) |
| Geração de minuta pelo motor determinístico ou via Ollama | **NUNCA** | 100% local (`127.0.0.1`) |
| Precedentes, trechos, modelos de formatação e arquivo morto | **NUNCA** | 100% local na pasta de dados do usuário |
| Chave da API Gemini | **NUNCA** vai para arquivos de configuração | Cofre nativo do sistema operacional (Credential Manager / Keychain) |
| Geração opcional via API Gemini em nuvem | **Somente com dupla autorização explícita** | Exige ativar *"Permitir envio externo"* nas Configurações **e** marcar o consentimento na operação; aplica **pseudonimização reversível** (`[OFENDIDA]`, `[ACUSADO]`, `[CPF_01]`, `[PROCESSO]`) por padrão antes do envio |

- O servidor local escuta exclusivamente em `127.0.0.1`, exige um **token de sessão aleatório** a cada inicialização (`X-SentencIA-Token`) e bloqueia requisições de outras origens ou hosts (`DNS rebinding protection`).
- Toda minuta gerada é um **documento de trabalho para revisão e decisão exclusiva do magistrado**.
