<#
.SYNOPSIS
    Instalador do SentencIA para Windows (sem certificado, sem administrador).
.DESCRIPTION
    Uso recomendado no PowerShell (sem aviso do SmartScreen):
      irm https://github.com/<dono>/SentencIA/releases/latest/download/instalar.ps1 | iex

    O que este instalador faz (sem privilegios de administrador):
      1. Baixa SentencIA-vX.Y.Z.zip e SHA256SUMS.txt (ou usa -PacoteLocal) e confere o SHA-256;
      2. Instala ou atualiza o programa em %LOCALAPPDATA%\Programs\SentencIA;
      3. Mantem os dados do gabinete fora do programa em %LOCALAPPDATA%\SentencIA\data (JVDFM_DATA_DIR);
      4. Executa instalador\bootstrap.ps1 (uv + Python 3.12 + dependencias);
      5. Cria atalhos na Area de Trabalho e no Menu Iniciar;
      6. Registra a desinstalacao em HKCU\...\Uninstall\SentencIA (padrao: manter dados).
#>
[CmdletBinding()]
param(
    [string]$Repo = $(if ($env:SENTENCIA_REPO) { $env:SENTENCIA_REPO } else { 'Chneto/SentencIA' }),
    [string]$Versao = $(if ($env:SENTENCIA_VERSAO) { $env:SENTENCIA_VERSAO } else { 'latest' }),
    [string]$PacoteLocal = '',
    [string]$Sha256Local = '',
    [string]$Destino = $(if ($env:SENTENCIA_DESTINO) { $env:SENTENCIA_DESTINO } else { Join-Path $env:LOCALAPPDATA 'Programs\SentencIA' }),
    [string]$Dados = $(if ($env:JVDFM_DATA_DIR) { $env:JVDFM_DATA_DIR } else { Join-Path $env:LOCALAPPDATA 'SentencIA\data' }),
    [switch]$SemPerguntas,
    [switch]$SemIniciar,
    [switch]$SemNavegador,
    [switch]$SemAtalhos,
    [switch]$SemRegistro,
    [switch]$SemNuvem
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

function Write-Etapa([string]$Msg) { Write-Host "`n==> $Msg" -ForegroundColor Cyan }
function Write-Ok([string]$Msg)    { Write-Host "    OK: $Msg" -ForegroundColor Green }
function Write-Aviso([string]$Msg) { Write-Host "    AVISO: $Msg" -ForegroundColor Yellow }

function Get-VersaoInstalada([string]$Pasta) {
    $pyproj = Join-Path $Pasta 'pyproject.toml'
    if (Test-Path -LiteralPath $pyproj) {
        $m = [regex]::Match((Get-Content -LiteralPath $pyproj -Raw -Encoding UTF8), '(?m)^version\s*=\s*"([^"]+)"')
        if ($m.Success) { return $m.Groups[1].Value }
    }
    return '2.1.0'
}

function Invoke-DownloadSeguro([string]$Url, [string]$Saida) {
    try {
        Invoke-WebRequest -Uri $Url -OutFile $Saida -UseBasicParsing -Headers @{ 'User-Agent' = 'SentencIA-Installer' }
    } catch {
        throw "Nao foi possivel baixar '$Url'. Verifique sua conexao com a internet ou use -PacoteLocal. Detalhe: $($_.Exception.Message)"
    }
}

Write-Host "`n  SentencIA - Instalador para Windows (sem administrador)" -ForegroundColor White
Write-Host "  Programa: $Destino"
Write-Host "  Dados:    $Dados"

$TmpDir = Join-Path ([IO.Path]::GetTempPath()) ("sentencia-win-" + [Guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null

try {
    # 1. Obter pacote (.zip) e SHA256SUMS.txt
    Write-Etapa 'Obtendo o pacote do SentencIA'
    $ZipPath = Join-Path $TmpDir 'SentencIA.zip'
    $ShaPath = Join-Path $TmpDir 'SHA256SUMS.txt'
    $NomeZipOriginal = ''

    if ($PacoteLocal) {
        if (-not (Test-Path -LiteralPath $PacoteLocal)) {
            throw "Pacote local nao encontrado: $PacoteLocal"
        }
        Copy-Item -LiteralPath $PacoteLocal -Destination $ZipPath -Force
        $NomeZipOriginal = Split-Path -Leaf $PacoteLocal
        Write-Ok "Usando pacote local: $NomeZipOriginal"
        if ($Sha256Local) {
            if (-not (Test-Path -LiteralPath $Sha256Local)) { throw "Arquivo SHA-256 nao encontrado: $Sha256Local" }
            Copy-Item -LiteralPath $Sha256Local -Destination $ShaPath -Force
        } else {
            $shaVizinho = Join-Path (Split-Path -Parent $PacoteLocal) 'SHA256SUMS.txt'
            if (Test-Path -LiteralPath $shaVizinho) { Copy-Item -LiteralPath $shaVizinho -Destination $ShaPath -Force }
        }
    } else {
        $apiUrl = if ($Versao -eq 'latest') {
            "https://api.github.com/repos/$Repo/releases/latest"
        } else {
            "https://api.github.com/repos/$Repo/releases/tags/$Versao"
        }
        $release = Invoke-RestMethod -Uri $apiUrl -Headers @{ 'User-Agent' = 'SentencIA-Installer' }
        $assetZip = $release.assets | Where-Object { $_.name -match '^SentencIA-v[0-9]+\.[0-9]+\.[0-9]+\.zip$' } | Select-Object -First 1
        $assetSha = $release.assets | Where-Object { $_.name -eq 'SHA256SUMS.txt' } | Select-Object -First 1
        if (-not $assetZip) { throw "Arquivo SentencIA-vX.Y.Z.zip nao encontrado na release '$Versao' de $Repo." }
        if (-not $assetSha) { throw "Arquivo SHA256SUMS.txt nao encontrado na release. Instalacao interrompida por seguranca." }
        $NomeZipOriginal = $assetZip.name
        Write-Host "    Baixando $NomeZipOriginal..."
        Invoke-DownloadSeguro $assetZip.browser_download_url $ZipPath
        Invoke-DownloadSeguro $assetSha.browser_download_url $ShaPath
    }

    # 2. Conferir SHA-256
    if (Test-Path -LiteralPath $ShaPath) {
        Write-Etapa 'Conferindo integridade (SHA-256)'
        $linhas = Get-Content -LiteralPath $ShaPath -Encoding UTF8 | Where-Object { $_.Trim() -ne '' }
        $esperado = $null
        foreach ($l in $linhas) {
            $partes = $l.Trim() -split '\s+', 2
            if ($partes.Count -ge 2) {
                $arq = $partes[1].TrimStart('*')
                if ($arq -eq $NomeZipOriginal) { $esperado = $partes[0].ToLowerInvariant(); break }
            }
        }
        if (-not $esperado -and $linhas.Count -gt 0) {
            $esperado = ($linhas[0].Trim() -split '\s+')[0].ToLowerInvariant()
        }
        $calculado = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if (-not $esperado -or $calculado -ne $esperado) {
            throw "Falha na verificacao SHA-256 (esperado: $esperado; obtido: $calculado)."
        }
        Write-Ok 'Integridade confirmada (SHA-256 valido).'
    } else {
        Write-Aviso 'SHA256SUMS.txt nao fornecido para o pacote local; pulando verificacao.'
    }

    # 3. Extrair e instalar preservando .venv e sem tocar em $Dados
    Write-Etapa "Instalando em $Destino"
    $Extraido = Join-Path $TmpDir 'extraido'
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $Extraido -Force
    $subPastas = @(Get-ChildItem -LiteralPath $Extraido -Directory)
    $RaizPacote = if ($subPastas.Count -eq 1 -and (Test-Path -LiteralPath (Join-Path $subPastas[0].FullName 'pyproject.toml'))) {
        $subPastas[0].FullName
    } else {
        $Extraido
    }
    if (-not (Test-Path -LiteralPath (Join-Path $RaizPacote 'pyproject.toml'))) {
        throw 'O pacote baixado nao contem pyproject.toml.'
    }

    New-Item -ItemType Directory -Path $Destino -Force | Out-Null
    New-Item -ItemType Directory -Path $Dados -Force | Out-Null

    # Encerrar servidor anterior desta instalacao, se estiver aberto
    $pyExistente = Join-Path $Destino '.venv\Scripts\python.exe'
    $launcherExistente = Join-Path $Destino 'launcher.py'
    if ((Test-Path -LiteralPath $pyExistente) -and (Test-Path -LiteralPath $launcherExistente)) {
        $env:JVDFM_DATA_DIR = $Dados
        try { & $pyExistente $launcherExistente --parar 2>$null | Out-Null } catch { }
    }

    # Atualiza os arquivos do programa sem apagar .venv, data/ nem .pasta_dados
    Get-ChildItem -LiteralPath $Destino -Force | Where-Object { $_.Name -notin @('.venv', 'data', '.pasta_dados') } | ForEach-Object {
        Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
    Get-ChildItem -LiteralPath $RaizPacote -Force | Where-Object { $_.Name -notin @('.venv') } | ForEach-Object {
        $alvo = Join-Path $Destino $_.Name
        if ($_.PSIsContainer -and $_.Name -eq 'data' -and (Test-Path -LiteralPath $alvo)) {
            # Nao sobrescreve dados existentes dentro de data/ se houver instalacao legada
            Copy-Item -Path (Join-Path $_.FullName '*') -Destination $alvo -Recurse -Force -ErrorAction SilentlyContinue
        } else {
            Copy-Item -LiteralPath $_.FullName -Destination $alvo -Recurse -Force
        }
    }
    # Persiste a pasta de dados fora do programa em <Destino>\.pasta_dados (lido por config.py, launcher.py e bootstrap.ps1)
    $dadosAbs = [IO.Path]::GetFullPath($Dados)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Join-Path $Destino '.pasta_dados'), $dadosAbs + "`n", $utf8NoBom)
    Write-Ok "Arquivos instalados em $Destino (pasta de dados persistida em .pasta_dados)"

    # 4. Rodar bootstrap.ps1 com JVDFM_DATA_DIR apontando para fora da pasta do programa
    Write-Etapa 'Configurando Python e bibliotecas (bootstrap)'
    $env:JVDFM_DATA_DIR = $Dados
    $bootstrap = Join-Path $Destino 'instalador\bootstrap.ps1'
    $argsBootstrap = @('-ExecutionPolicy', 'Bypass', '-NoProfile', '-File', $bootstrap, '-SemIniciar')
    if ($SemPerguntas) { $argsBootstrap += '-SemPerguntas' }
    if (-not $SemNuvem) { $argsBootstrap += '-Nuvem' }

    $psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $psExe)) { $psExe = 'powershell.exe' }
    $proc = Start-Process -FilePath $psExe -ArgumentList $argsBootstrap -WorkingDirectory $Destino -Wait -NoNewWindow -PassThru
    if ($proc.ExitCode -ne 0) {
        throw "O bootstrap encerrou com codigo $($proc.ExitCode). Veja os logs em $Dados\logs\bootstrap.log."
    }
    Write-Ok 'Ambiente Python configurado.'

    # 5. Migracao segura de data/ legado (se existir dentro da pasta do programa)
    $dataLegado = Join-Path $Destino 'data'
    $pyVenv = Join-Path $Destino '.venv\Scripts\python.exe'
    if ((Test-Path -LiteralPath $dataLegado) -and (Test-Path -LiteralPath $pyVenv)) {
        try {
            & $pyVenv -c "from pathlib import Path; from app.core.migracao_dados import migrar_dados_legados; migrar_dados_legados(Path(r'$dataLegado'), Path(r'$Dados'))" 2>$null | Out-Null
        } catch { }
    }

    # 6. Atalhos (Area de Trabalho e Menu Iniciar)
    $versaoAtual = Get-VersaoInstalada $Destino
    if (-not $SemAtalhos) {
        Write-Etapa 'Criando atalhos'
        $shell = New-Object -ComObject WScript.Shell
        $icone = Join-Path $Destino 'app\static\img\SentencIA.ico'
        $alvoBat = Join-Path $Destino 'Iniciar SentencIA.bat'
        $menuDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\SentencIA'
        New-Item -ItemType Directory -Path $menuDir -Force | Out-Null

        $atalhos = @(
            (Join-Path $menuDir 'SentencIA.lnk'),
            (Join-Path ([Environment]::GetFolderPath('Desktop')) 'SentencIA.lnk')
        )
        foreach ($lnkPath in $atalhos) {
            try {
                $sc = $shell.CreateShortcut($lnkPath)
                $sc.TargetPath = $alvoBat
                $sc.WorkingDirectory = $Destino
                $sc.Description = "SentencIA v$versaoAtual"
                if (Test-Path -LiteralPath $icone) { $sc.IconLocation = "$icone,0" }
                $sc.Save()
            } catch {
                Write-Aviso "Nao foi possivel criar o atalho em $lnkPath."
            }
        }
        Write-Ok 'Atalhos criados no Menu Iniciar e na Area de Trabalho.'
    }

    # 7. Registro de desinstalacao (HKCU, sem administrador)
    if (-not $SemRegistro) {
        Write-Etapa 'Registrando desinstalacao no Windows (usuario atual)'
        $regPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SentencIA'
        New-Item -Path $regPath -Force | Out-Null
        $desinst = Join-Path $Destino 'instalador\desinstalar.ps1'
        Set-ItemProperty -Path $regPath -Name 'DisplayName' -Value 'SentencIA'
        Set-ItemProperty -Path $regPath -Name 'DisplayVersion' -Value $versaoAtual
        Set-ItemProperty -Path $regPath -Name 'Publisher' -Value 'FChNeto'
        Set-ItemProperty -Path $regPath -Name 'InstallLocation' -Value $Destino
        Set-ItemProperty -Path $regPath -Name 'UninstallString' -Value "powershell.exe -ExecutionPolicy Bypass -NoProfile -File `"$desinst`" -Destino `"$Destino`" -Dados `"$Dados`""
        Set-ItemProperty -Path $regPath -Name 'NoModify' -Value 1 -Type DWord
        Set-ItemProperty -Path $regPath -Name 'NoRepair' -Value 1 -Type DWord
        Write-Ok 'Registro de desinstalacao criado (HKCU).'
    }

    Write-Etapa "SentencIA v$versaoAtual instalado com sucesso!"
    Write-Host "    Programa: $Destino"
    Write-Host "    Dados:    $Dados"

    if (-not $SemIniciar -and (Test-Path -LiteralPath $pyVenv)) {
        $launcherArgs = @((Join-Path $Destino 'launcher.py'))
        if ($SemNavegador) { $launcherArgs += '--sem-navegador' }
        $env:JVDFM_DATA_DIR = $Dados
        & $pyVenv @launcherArgs
    }
} finally {
    if (Test-Path -LiteralPath $TmpDir) {
        Remove-Item -LiteralPath $TmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
