# Instalação no macOS — SentencIA

> **Aplicação web local — 100% no seu Mac.**
> O SentencIA abre no seu navegador (Safari, Chrome, Edge ou Brave), mas o servidor roda **exclusivamente
> no seu computador** (`127.0.0.1`). Nenhum processo judicial vai para a internet.

---

## Por que instalar por um comando no Terminal?

Sem conta paga de desenvolvedor da Apple, aplicativos `.app` ou `.dmg` baixados pelo navegador recebem a
marca de quarentena do **Gatekeeper** e são bloqueados ao abrir.

Quando você instala pelo comando único abaixo no **Terminal**:

1. Os arquivos baixados pelo `curl` **não** recebem marca de quarentena;
2. O instalador confere a integridade do pacote pelo hash oficial (**SHA-256**);
3. O aplicativo **`SentencIA.app`** é montado **localmente no seu Mac** em `~/Applications` — por isso
   abre normalmente pelo **Launchpad**, **Dock** ou **Finder**, sem precisar de senha de administrador
   (`sudo`) nem conta Apple.

Funciona em **macOS 13 (Ventura) ou mais recente**, tanto em **Apple Silicon (M1/M2/M3/M4)** quanto em
**Intel**.

---

## Passo a passo (1 comando)

### 1. Abra o Terminal

Pressione **`Command (⌘) + Espaço`**, digite **Terminal** e aperte **Enter**.

### 2. Cole o comando de instalação e aperte Enter

```bash
curl -fsSL https://github.com/<dono>/SentencIA/releases/latest/download/instalar-mac.sh | bash
```

*(Substitua `<dono>` pelo usuário/organização oficial do repositório onde a versão foi publicada.)*

### 3. Acompanhe o progresso no Terminal

A saída no Terminal mostra cada etapa em português claro:

```text
  SentencIA — instalador para macOS
  Aplicação web local (100% no seu computador — nenhum processo vai para a internet).

==> Verificando este Mac
    OK: macOS 14.x (Apple Silicon — arm64).

==> Obtendo o pacote do SentencIA
    Consultando a versão disponível em github.com/<dono>/SentencIA (latest)...
    Baixando SentencIA-v2.5.0.zip...
    Baixando SHA256SUMS.txt para conferência de integridade...

==> Conferindo a integridade do arquivo (SHA-256)
    OK: Integridade confirmada (SHA-256 válido).

==> Instalando o SentencIA em /Users/<você>/Applications/SentencIA
    OK: Arquivos instalados em /Users/<você>/Applications/SentencIA
    OK: Pasta de dados configurada em /Users/<você>/Library/Application Support/SentencIA

==> Configurando Python e bibliotecas locais (uv + Python 3.12)
    ...
    OK: Ambiente Python pronto.

==> Montando o aplicativo local em /Users/<você>/Applications/SentencIA.app (sem quarentena)
    OK: Aplicativo criado em /Users/<você>/Applications/SentencIA.app

==> Instalação concluída!
    Para abrir o SentencIA, use 'SentencIA' no Launchpad, na pasta ~/Applications ou no Dock.
    Seus autos, configurações e modelos ficam em: /Users/<você>/Library/Application Support/SentencIA
```

Ao final, o SentencIA abre automaticamente no navegador.

---

## Como abrir no dia a dia

- Abra o **Launchpad** ou a pasta **`~/Applications`** e clique em **SentencIA**.
- Você pode arrastar o ícone do **SentencIA** para o **Dock** para acesso em 1 clique.
- Para atualizar no futuro, basta rodar o mesmo comando no Terminal: seus dados e configurações são
  preservados automaticamente.

---

## Onde ficam os seus dados

- **Programa:** `~/Applications/SentencIA` (e atalho `~/Applications/SentencIA.app`)
- **Dados do gabinete** (autos, transcrições, configurações, precedentes e logs):
  `~/Library/Application Support/SentencIA`
- **Chave do Gemini (opcional):** guardada no **Acesso às Chaves (Keychain)** do macOS.

Os dados ficam **fora** da pasta do programa e **fora** do iCloud Drive, protegendo o sigilo dos autos.

---

## Como desinstalar

Abra no Finder (ou no Terminal):

```bash
~/Applications/SentencIA/desinstalar.command
```

O desinstalador encerra o servidor (se estiver aberto), remove o programa e o atalho `SentencIA.app` e
pergunta se você deseja **manter seus dados do gabinete** (o padrão é **Sim — manter**).

---

## Solução de problemas no Mac

- **Erro ao baixar (`curl` falhou):** verifique a conexão com a internet ou se a VPN/rede do tribunal
  bloqueia o GitHub. Se você já baixou o `.zip` por outro caminho, instale localmente:
  ```bash
  bash instalar-mac.sh --pacote-local ~/Downloads/SentencIA-v2.5.0.zip
  ```
- **Aviso de Xcode Command Line Tools:** na maioria dos Macs não é necessário. Se alguma biblioteca pedir
  compilação, rode `xcode-select --install` no Terminal, aguarde concluir e execute o instalador novamente.
- **IA local (Ollama) no Mac:** instale pelo site oficial ([ollama.com/download/mac](https://ollama.com/download/mac));
  o SentencIA detecta o Ollama automaticamente e sugere o modelo adequado à memória unificada do seu Mac.
