<#
.SYNOPSIS
    Instalador e iniciador do SentencIA para Windows (idempotente, sem administrador).

.DESCRIPTION
    1. Confere a pasta do programa (gravável; aviso se estiver no OneDrive).
    2. Instala o gerenciador "uv" no perfil do usuário, se ausente (instalador oficial astral.sh).
    3. Garante o Python 3.12 gerenciado pelo uv.
    4. Instala as dependências (uv sync) com os extras da plataforma; se um extra opcional falhar,
       segue sem ele e avisa. A instalação fica marcada em data\.instalado e só é refeita quando
       pyproject.toml/uv.lock mudarem (ou com -Reinstalar).
    5. Oferece (S/n) instalar o Ollama (winget) e baixar o modelo configurado — opcional.
    6. Inicia o servidor e abre o navegador (launcher.py).
    Registro completo em data\logs\bootstrap.log.

.PARAMETER SemPerguntas
    Não faz perguntas (modo silencioso): não instala Ollama nem componentes de nuvem.
.PARAMETER Reinstalar
    Refaz a instalação das dependências mesmo que já esteja marcada como feita.
.PARAMETER Nuvem
    Inclui os componentes opcionais de nuvem (Gemini/SpeechRecognition) sem perguntar.
.PARAMETER SemIniciar
    Apenas instala/verifica; não inicia o sistema.
.PARAMETER SemNavegador
    Inicia o servidor sem abrir o navegador.
#>
[CmdletBinding()]
param(
    [switch]$SemPerguntas,
    [switch]$Reinstalar,
    [switch]$Nuvem,
    [switch]$SemIniciar,
    [switch]$SemNavegador
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:UV_LINK_MODE = 'copy'          # evita avisos de hardlink entre unidades / OneDrive
$env:UV_NO_PROGRESS = '1'
$env:SENTENCIA_LAUNCHER_CONSOLE = '1'   # o launcher.py escreve aqui (e no log) em vez de abrir caixas de diálogo

$VersaoApp = '2.3.0'   # conferido por tests/test_versao_coerente.py (igual ao pyproject.toml)
$VersaoPython = '3.12'
$UvVersaoMinima = [version]'0.8.0'   # lê o uv.lock (revision 3) e o [tool.uv] do pyproject.toml
# Windows ARM64: várias dependências do núcleo (pymupdf, ctranslate2...) não têm wheel win_arm64; usa o
# Python x64 (emulado pelo Windows 11) para que o uv.lock resolva para wheels win_amd64.
$EhArm64 = ("$env:PROCESSOR_ARCHITECTURE" -eq 'ARM64') -or ("$env:PROCESSOR_ARCHITEW6432" -eq 'ARM64')
if ($EhArm64) { $PythonAlvo = 'cpython-3.12-windows-x86_64-none' } else { $PythonAlvo = $VersaoPython }
$Raiz = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).ProviderPath
Set-Location -LiteralPath $Raiz
[Environment]::CurrentDirectory = $Raiz

$PastaDadosProjeto = Join-Path $Raiz 'data'
$ArquivoPastaDados = Join-Path $Raiz '.pasta_dados'
if ($env:SENTENCIA_DATA_DIR) {
    $PastaDados = [Environment]::ExpandEnvironmentVariables($env:SENTENCIA_DATA_DIR)
} elseif (Test-Path -LiteralPath $ArquivoPastaDados) {
    $linhaDados = (Get-Content -LiteralPath $ArquivoPastaDados -Encoding UTF8 | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') } | Select-Object -First 1)
    if ($linhaDados) { $PastaDados = [Environment]::ExpandEnvironmentVariables($linhaDados.Trim()) } else { $PastaDados = $PastaDadosProjeto }
} else {
    $PastaDados = $PastaDadosProjeto
}
$PastaLogs = Join-Path $PastaDados 'logs'
$ArquivoLog = Join-Path $PastaLogs 'bootstrap.log'
$ArquivoMarca = Join-Path $PastaDadosProjeto '.instalado'
$PythonVenv = Join-Path $Raiz '.venv\Scripts\python.exe'
$Utf8SemBom = New-Object System.Text.UTF8Encoding($false)

# ------------------------------------------------------------------------------------------------
# Mensagens e registro
# ------------------------------------------------------------------------------------------------
function Escrever-Log([string]$Mensagem, [string]$Nivel = 'INFO') {
    try {
        if (-not (Test-Path -LiteralPath $PastaLogs)) { New-Item -ItemType Directory -Force -Path $PastaLogs | Out-Null }
        $linha = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Nivel, $Mensagem
        [System.IO.File]::AppendAllText($ArquivoLog, $linha + [Environment]::NewLine, $Utf8SemBom)
    } catch { }
}
function Etapa([string]$m) { Write-Host ''; Write-Host "==> $m" -ForegroundColor Cyan; Escrever-Log "ETAPA: $m" }
function Info([string]$m) { Write-Host "    $m"; Escrever-Log $m }
function Ok([string]$m) { Write-Host "    OK: $m" -ForegroundColor Green; Escrever-Log "OK: $m" }
function Aviso([string]$m) { Write-Host "    AVISO: $m" -ForegroundColor Yellow; Escrever-Log $m 'AVISO' }

function Encerrar-Com-Erro([string]$m) {
    Write-Host ''
    Write-Host "    ERRO: $m" -ForegroundColor Red
    Write-Host "    Registro completo: $ArquivoLog" -ForegroundColor Red
    Escrever-Log $m 'ERRO'
    if (-not $SemPerguntas) { [void](Read-Host '    Pressione Enter para fechar') }
    exit 1
}

function Perguntar([string]$Pergunta, [bool]$PadraoSim) {
    # Modo silencioso: nunca instala nada opcional sem o usuário ver.
    if ($SemPerguntas) { return $false }
    if ($PadraoSim) { $sufixo = '(S/n)' } else { $sufixo = '(s/N)' }
    $resposta = Read-Host "    $Pergunta $sufixo"
    Escrever-Log "PERGUNTA: $Pergunta -> '$resposta'"
    if ([string]::IsNullOrWhiteSpace($resposta)) { return $PadraoSim }
    return ($resposta.Trim().ToLowerInvariant() -in @('s', 'sim', 'y', 'yes'))
}

function Executar([string]$Exe, [string[]]$Argumentos) {
    # Mostra e registra a saída (stdout+stderr) de um programa externo; devolve o código de saída.
    Escrever-Log ('$ "{0}" {1}' -f $Exe, ($Argumentos -join ' '))
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Argumentos 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) { $t = $_.Exception.Message } else { $t = [string]$_ }
            if ($t) { Write-Host "      $t" -ForegroundColor DarkGray; Escrever-Log "      $t" }
        }
        $codigo = $LASTEXITCODE
    } catch {
        Escrever-Log ("Falha ao executar {0}: {1}" -f $Exe, $_.Exception.Message) 'ERRO'
        $codigo = 9009
    } finally {
        $ErrorActionPreference = $anterior
    }
    if ($null -eq $codigo) { $codigo = 0 }
    return [int]$codigo
}

# ------------------------------------------------------------------------------------------------
# Marca de instalação (data\.instalado, formato chave=valor)
# ------------------------------------------------------------------------------------------------
function Ler-Marca {
    $dados = @{}
    if (Test-Path -LiteralPath $ArquivoMarca) {
        foreach ($linha in [System.IO.File]::ReadAllLines($ArquivoMarca, $Utf8SemBom)) {
            $i = $linha.IndexOf('=')
            if ($i -gt 0) { $dados[$linha.Substring(0, $i).Trim()] = $linha.Substring($i + 1).Trim() }
        }
    }
    return $dados
}
function Gravar-Marca([hashtable]$Dados) {
    $linhas = foreach ($k in ($Dados.Keys | Sort-Object)) { '{0}={1}' -f $k, $Dados[$k] }
    if (-not (Test-Path -LiteralPath $PastaDadosProjeto)) { New-Item -ItemType Directory -Force -Path $PastaDadosProjeto | Out-Null }
    [System.IO.File]::WriteAllLines($ArquivoMarca, [string[]]$linhas, $Utf8SemBom)
}
function Valor-Marca([hashtable]$Marca, [string]$Chave) {
    if ($Marca.ContainsKey($Chave)) { return [string]$Marca[$Chave] }
    return ''
}
function Hash-Dependencias {
    $a = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Raiz 'pyproject.toml')).Hash
    $b = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Raiz 'uv.lock')).Hash
    return "$a-$b"
}

# ------------------------------------------------------------------------------------------------
# uv, Python, dependências, Ollama
# ------------------------------------------------------------------------------------------------
function Encontrar-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidatos = @(
        (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
        (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe')
    )
    if ($env:LOCALAPPDATA) { $candidatos += (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\uv.exe') }
    foreach ($c in $candidatos) { if (Test-Path -LiteralPath $c) { return $c } }
    return $null
}

function Versao-Uv([string]$Uv) {
    # Devolve [version] do uv ou $null se não conseguir ler.
    $anterior = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        $saida = (& $Uv --version 2>$null) -join ' '
        if ($saida -match '(\d+\.\d+\.\d+)') { return [version]$Matches[1] }
    } catch { } finally { $ErrorActionPreference = $anterior }
    return $null
}

function Garantir-Uv {
    $uv = Encontrar-Uv
    if ($uv) {
        $v = Versao-Uv $uv
        if ($v -and $v -lt $UvVersaoMinima) {
            Aviso "uv $v é antigo demais para este projeto (mínimo $UvVersaoMinima); atualizando..."
            $codigo = Executar $uv @('self', 'update')
            $v = Versao-Uv $uv
            if ($v -and $v -lt $UvVersaoMinima) {
                Aviso "Não foi possível atualizar o uv ($codigo). Será instalada uma cópia própria no perfil do usuário."
                $uv = $null
            }
        }
    }
    if ($uv) { Ok "uv encontrado ($uv)"; return $uv }
    Info 'Instalando o uv (gerenciador de Python) no seu perfil de usuário — não requer administrador...'
    $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    # TLS 1.2 precisa ser ativado NO processo filho que baixa o instalador (o ajuste não é herdado).
    $comandoUv = '[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; irm https://astral.sh/uv/install.ps1 | iex'
    $codigo = Executar $ps @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $comandoUv)
    $proprio = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    if (Test-Path -LiteralPath $proprio) { $uv = $proprio } else { $uv = Encontrar-Uv }
    if (-not $uv) {
        Encerrar-Com-Erro "Não foi possível instalar o uv (código $codigo). Verifique a conexão com a internet e tente novamente, ou instale manualmente: https://docs.astral.sh/uv/"
    }
    $pastaUv = Split-Path -Parent $uv
    if (($env:Path -split ';') -notcontains $pastaUv) { $env:Path = "$pastaUv;$env:Path" }
    Ok "uv instalado ($uv)"
    return $uv
}

function Garantir-Python([string]$Uv) {
    if ($EhArm64) { Info 'Windows ARM64 detectado: usando o Python x64 (emulado), pois há dependências sem versão ARM64.' }
    $codigo = Executar $Uv @('python', 'find', $PythonAlvo)
    if ($codigo -eq 0) { Ok "Python $VersaoPython disponível"; return }
    Info "Baixando o Python $VersaoPython (uma única vez)..."
    $codigo = Executar $Uv @('python', 'install', $PythonAlvo)
    if ($codigo -eq 0) { $codigo = Executar $Uv @('python', 'find', $PythonAlvo) }
    if ($codigo -ne 0) {
        # Instalação anterior corrompida (pasta apagada/sincronizada pela metade): reinstala.
        Aviso "O Python $VersaoPython gerenciado pelo uv parece danificado; reinstalando..."
        $codigo = Executar $Uv @('python', 'install', '--reinstall', $PythonAlvo)
    }
    if ($codigo -ne 0) { Encerrar-Com-Erro "Falha ao instalar o Python $VersaoPython (código $codigo). Verifique a conexão com a internet." }
    Ok "Python $VersaoPython instalado"
}

function Parar-Servidor-Em-Execucao {
    if (Test-Path -LiteralPath $PythonVenv) {
        [void](Executar $PythonVenv @((Join-Path $Raiz 'launcher.py'), '--parar'))
    }
}

function Sincronizar-Dependencias([string]$Uv, [bool]$ComNuvem) {
    $plataforma = @('seguranca', 'ocr-windows')
    $opcionais = @('transcricao')
    if ($ComNuvem) { $opcionais += 'nuvem' }
    # Tentativas, da mais completa para o núcleo mínimo.
    $tentativas = @(
        ,($plataforma + $opcionais)
    )
    $semTranscricao = @($opcionais | Where-Object { $_ -ne 'transcricao' })
    $tentativas += ,($plataforma + $semTranscricao)
    $tentativas += ,@('seguranca')
    $tentativas += ,@()
    foreach ($extras in $tentativas) {
        $argumentos = @('sync', '--frozen', '--no-dev', '--python', $PythonAlvo)
        foreach ($e in $extras) { $argumentos += @('--extra', $e) }
        if ($extras.Count -gt 0) { $rotulo = ($extras -join ', ') } else { $rotulo = 'somente o núcleo' }
        Info "Instalando dependências ($rotulo)... a primeira vez pode levar alguns minutos."
        # Falhas transitórias de rede não podem reduzir a configuração: repete a MESMA tentativa antes.
        $codigo = 1
        for ($rep = 1; $rep -le 2 -and $codigo -ne 0; $rep++) {
            if ($rep -gt 1) { Aviso "Repetindo a instalação com ($rotulo) (tentativa $rep de 2)..."; Start-Sleep -Seconds 3 }
            $codigo = Executar $Uv $argumentos
        }
        if ($codigo -eq 0) { return ,$extras }
        Aviso "A instalação com ($rotulo) falhou (código $codigo); tentando uma configuração menor."
    }
    Encerrar-Com-Erro 'Não foi possível instalar as dependências do sistema. Veja o registro: pode ser falha de conexão com a internet, versão antiga do uv ou plataforma sem suporte.'
}

function Encontrar-Ollama {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    if ($env:LOCALAPPDATA) {
        $c = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
        if (Test-Path -LiteralPath $c) { return $c }
    }
    return $null
}

function Ollama-Responde([string]$Url) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "$Url/api/tags" -TimeoutSec 3
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Configurar-Ollama([hashtable]$Marca) {
    $exe = Encontrar-Ollama
    if (-not $exe) {
        if ((Valor-Marca $Marca 'ollama') -eq 'recusado' -and -not $Reinstalar) {
            Info 'Ollama não instalado (opcional; você recusou antes). A minuta usará o motor determinístico.'
            return 'recusado'
        }
        Info 'O Ollama é OPCIONAL: um modelo de IA local (nada sai do computador) que refina a redação.'
        Info 'Sem ele, o sistema funciona com o motor determinístico. Ocupa ~1 a 3 GB.'
        if (-not (Perguntar 'Deseja instalar o Ollama agora?' $true)) { return 'recusado' }
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($winget) {
            $codigo = Executar $winget.Source @('install', '--id', 'Ollama.Ollama', '-e', '--silent', '--accept-package-agreements', '--accept-source-agreements')
            $exe = Encontrar-Ollama
            if (-not $exe) { Aviso "Não foi possível instalar o Ollama pelo winget (código $codigo)." }
        }
        if (-not $exe) {
            Aviso 'Abrindo a página oficial de download do Ollama; instale-o e execute este iniciador de novo.'
            Start-Process 'https://ollama.com/download/windows'
            return 'ausente'
        }
        Ok "Ollama instalado ($exe)"
    } else {
        Ok "Ollama encontrado ($exe)"
    }

    # Modelo configurado (data\user_settings.json -> ollama_model)
    $modelo = ''
    $url = 'http://127.0.0.1:11434'
    if (Test-Path -LiteralPath $PythonVenv) {
        $anterior = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        try {
            $saida = & $PythonVenv -c "from app.core.config import load_settings as l; s=l(); print(s.ollama_model); print(s.ollama_url)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $saida) { $modelo = [string]@($saida)[0]; if (@($saida).Count -gt 1) { $url = ([string]@($saida)[1]).TrimEnd('/') } }
        } catch { } finally { $ErrorActionPreference = $anterior }
    }
    if (-not $modelo) { Aviso 'Não foi possível ler o modelo configurado; pulando o download.'; return 'instalado' }

    if (-not (Ollama-Responde $url)) {
        Start-Process -FilePath $exe -ArgumentList 'serve' -WindowStyle Hidden
        for ($i = 0; $i -lt 20 -and -not (Ollama-Responde $url); $i++) { Start-Sleep -Milliseconds 750 }
    }
    $lista = ''
    $anterior = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $lista = (& $exe list 2>$null) -join "`n" } catch { } finally { $ErrorActionPreference = $anterior }
    $nomeBase = $modelo
    if ($nomeBase -notmatch ':') { $nomeBase = "$nomeBase`:latest" }
    if ($lista -match ('(?m)^' + [regex]::Escape($nomeBase) + '\s')) {
        Ok "Modelo '$modelo' já baixado"
        return 'instalado'
    }
    if ((Valor-Marca $Marca 'modelo_recusado') -eq $modelo -and -not $Reinstalar) {
        Info "Modelo '$modelo' não baixado (você recusou antes)."
        return 'instalado'
    }
    if (Perguntar "Baixar agora o modelo '$modelo' (download de alguns GB)?" $true) {
        Info "Baixando '$modelo'... (pode demorar conforme a internet)"
        Escrever-Log "$ ollama pull $modelo"
        $anterior = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        try { & $exe pull $modelo 2>&1 | Out-Host } finally { $ErrorActionPreference = $anterior }
        if ($LASTEXITCODE -eq 0) { Ok "Modelo '$modelo' pronto" } else { Aviso "Falha ao baixar '$modelo' (código $LASTEXITCODE). Tente depois: ollama pull $modelo" }
    } else {
        $Marca['modelo_recusado'] = $modelo
    }
    return 'instalado'
}

function Configurar-Atalho([hashtable]$Marca) {
    if ($Marca.ContainsKey('atalho') -and -not [string]::IsNullOrEmpty([string]$Marca['atalho'])) { return }
    Etapa 'Atalho na Área de Trabalho'
    if (Perguntar 'Criar um atalho do SentencIA na Área de Trabalho?' $true) {
        $codigo = Executar $PythonVenv @((Join-Path $Raiz 'scripts\criar_atalho_desktop.py'))
        if ($codigo -eq 0) { $Marca['atalho'] = 'sim' } else { Aviso 'Não foi possível criar o atalho (veja o registro).'; $Marca['atalho'] = 'falhou' }
    } else {
        $Marca['atalho'] = 'recusado'
    }
    Gravar-Marca $Marca
}

function Venv-Funciona {
    # O venv só é válido se o Python base ainda existe e o núcleo do servidor importa.
    if (-not (Test-Path -LiteralPath $PythonVenv)) { return $false }
    $anterior = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        & $PythonVenv -c 'import fastapi, uvicorn' 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false } finally { $ErrorActionPreference = $anterior }
}

# ------------------------------------------------------------------------------------------------
# Principal
# ------------------------------------------------------------------------------------------------
try {
    Write-Host ''
    Write-Host "  SentencIA $VersaoApp — instalador/iniciador (Windows)" -ForegroundColor White
    Escrever-Log "===== bootstrap $VersaoApp | Windows $([Environment]::OSVersion.Version) | $env:PROCESSOR_ARCHITECTURE | PowerShell $($PSVersionTable.PSVersion) ====="
    Escrever-Log "Pasta do programa: $Raiz"

    Etapa 'Conferindo a pasta do programa'
    try {
        $teste = Join-Path $Raiz ('.teste_gravacao_' + [guid]::NewGuid().ToString('N'))
        [System.IO.File]::WriteAllText($teste, 'ok')
        Remove-Item -LiteralPath $teste -Force
    } catch {
        Encerrar-Com-Erro "A pasta '$Raiz' não permite gravação. Copie a pasta do programa para Documentos (ou C:\SentencIA) e tente de novo."
    }
    foreach ($od in @($env:OneDrive, $env:OneDriveCommercial, $env:OneDriveConsumer)) {
        if ($od -and $Raiz.StartsWith($od, [System.StringComparison]::OrdinalIgnoreCase)) {
            Aviso 'A pasta está dentro do OneDrive: os autos sigilosos (data\cases) e o ambiente Python (.venv) serão sincronizados com a nuvem.'
            Aviso 'Para sigilo e desempenho, prefira uma pasta fora do OneDrive (ex.: C:\SentencIA).'
            break
        }
    }
    Ok $Raiz

    $marca = Ler-Marca
    $hash = Hash-Dependencias
    $plataformaAtual = "windows-$env:PROCESSOR_ARCHITECTURE".ToLowerInvariant()
    if ($EhArm64) { $plataformaAtual = 'windows-arm64-x64emu' }
    $instalado = (Test-Path -LiteralPath $PythonVenv) -and ((Valor-Marca $marca 'hash') -eq $hash) -and `
        ((Valor-Marca $marca 'plataforma') -eq $plataformaAtual) -and ((Valor-Marca $marca 'versao') -eq $VersaoApp)
    # O python.exe do venv é um trampolim: se o Python base sumiu/ficou inacessível, só a existência do
    # arquivo engana. Exige que o interpretador realmente importe o núcleo do servidor.
    if ($instalado -and -not (Venv-Funciona)) {
        Aviso 'O ambiente Python existente não funciona (interpretador base ausente ou inacessível); será recriado.'
        $instalado = $false
    }
    # Extra opcional que ficou de fora (rede instável): tenta de novo nas próximas execuções (até 3 vezes).
    $tentativasExtras = 0
    [void][int]::TryParse((Valor-Marca $marca 'tentativas_extras'), [ref]$tentativasExtras)
    if ($instalado -and ((Valor-Marca $marca 'extras_pendentes') -ne '') -and $tentativasExtras -lt 3 -and -not $SemPerguntas) {
        Aviso ("Componente opcional pendente (" + (Valor-Marca $marca 'extras_pendentes') + "); tentando instalar de novo.")
        $instalado = $false
    }
    $querNuvem = $Nuvem.IsPresent -or ((Valor-Marca $marca 'nuvem') -eq 'sim')
    if ($Nuvem.IsPresent -and (Valor-Marca $marca 'nuvem') -ne 'sim') { $instalado = $false }

    if ($instalado -and -not $Reinstalar) {
        Etapa 'Ambiente já instalado'
        Ok ("extras: " + (Valor-Marca $marca 'extras'))
        # Etapas opcionais que ficaram pendentes (Ollama 'ausente'/não configurado, atalho nunca respondido).
        if (-not $SemPerguntas) {
            if ((Valor-Marca $marca 'ollama') -in @('', 'ausente')) {
                Etapa 'IA local opcional (Ollama)'
                $marca['ollama'] = Configurar-Ollama $marca
                Gravar-Marca $marca
            }
            Configurar-Atalho $marca
        }
    } else {
        Etapa 'Gerenciador de Python (uv)'
        $uv = Garantir-Uv

        Etapa "Python $VersaoPython"
        Garantir-Python $uv

        if (-not $Nuvem.IsPresent -and -not $marca.ContainsKey('nuvem') -and -not $SemPerguntas) {
            Info 'Componentes de NUVEM (Gemini) são opcionais e só enviam dados com sua autorização expressa.'
            $querNuvem = Perguntar 'Instalar os componentes opcionais de nuvem?' $false
        }

        Etapa 'Dependências do sistema'
        Parar-Servidor-Em-Execucao
        $extras = Sincronizar-Dependencias $uv $querNuvem
        if ($extras -notcontains 'transcricao') {
            Aviso 'Transcrição local (Whisper) indisponível nesta máquina; as mídias ficarão como FALHOU até ser instalada.'
        }
        if ($querNuvem -and ($extras -notcontains 'nuvem')) { Aviso 'Componentes de nuvem não instalados.' }
        $marca['versao'] = $VersaoApp
        $marca['hash'] = $hash
        $marca['plataforma'] = $plataformaAtual
        $marca['extras'] = ($extras -join ',')
        if ($extras -notcontains 'transcricao') {
            $marca['extras_pendentes'] = 'transcricao'
            if ((Valor-Marca $marca 'extras_pendentes') -ne '' -and $tentativasExtras -ge 0) { $marca['tentativas_extras'] = [string]($tentativasExtras + 1) }
        } else {
            $marca['extras_pendentes'] = ''
            $marca['tentativas_extras'] = '0'
        }
        if ($querNuvem) { $marca['nuvem'] = 'sim' } else { $marca['nuvem'] = 'nao' }
        $marca['python'] = $VersaoPython
        $marca['instalado_em'] = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss')
        Gravar-Marca $marca
        Ok 'Dependências instaladas'

        Etapa 'IA local opcional (Ollama)'
        $marca['ollama'] = Configurar-Ollama $marca
        Gravar-Marca $marca

        if (-not $SemPerguntas) { Configurar-Atalho $marca }
    }

    if ($SemIniciar) {
        Ok 'Instalação conferida (sem iniciar, a pedido).'
        exit 0
    }

    Etapa 'Iniciando o SentencIA'
    $argsLauncher = @((Join-Path $Raiz 'launcher.py'))
    if ($SemNavegador) { $argsLauncher += '--sem-navegador' }
    $codigo = Executar $PythonVenv $argsLauncher
    if ($codigo -ne 0) {
        Encerrar-Com-Erro "O sistema não iniciou (código $codigo). Veja também data\logs\launcher.log e data\logs\servidor.log."
    }
    if ($SemNavegador) { Ok 'Servidor ativo em segundo plano (endereço acima).' } else { Ok 'Sistema aberto no navegador. O servidor continua ativo em segundo plano.' }
    Info 'Para encerrar o servidor: .venv\Scripts\python.exe launcher.py --parar'
    if (-not $SemPerguntas) {
        Write-Host ''
        Write-Host '    Esta janela fecha sozinha em alguns segundos.' -ForegroundColor DarkGray
        Start-Sleep -Seconds 5
    }
    exit 0
} catch {
    Escrever-Log ($_ | Out-String) 'ERRO'
    Encerrar-Com-Erro ("Falha inesperada: " + $_.Exception.Message)
}
