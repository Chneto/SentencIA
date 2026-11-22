<#
.SYNOPSIS
    Desinstalador do SentencIA para Windows (sem administrador).
.DESCRIPTION
    Remove o programa, atalhos e entrada de registro em HKCU.
    Padrao seguro: MANTEM os dados do gabinete em %LOCALAPPDATA%\SentencIA\data.
#>
[CmdletBinding()]
param(
    [string]$Destino = $(Join-Path $env:LOCALAPPDATA 'Programs\SentencIA'),
    [string]$Dados = '',
    [switch]$ApagarDados,
    [switch]$Sim
)

$ErrorActionPreference = 'Stop'

if (-not $Dados) {
    $marcadorDados = Join-Path $Destino '.pasta_dados'
    if (Test-Path -LiteralPath $marcadorDados) {
        $linha = (Get-Content -LiteralPath $marcadorDados -Encoding UTF8 | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') } | Select-Object -First 1)
        if ($linha) { $Dados = [Environment]::ExpandEnvironmentVariables($linha.Trim()) }
    }
    if (-not $Dados) {
        $Dados = Join-Path $env:LOCALAPPDATA 'SentencIA\data'
    }
}

Write-Host "`n  Desinstalador do SentencIA (Windows)" -ForegroundColor White
Write-Host "  Programa: $Destino"
Write-Host "  Dados:    $Dados"

if (-not $Sim) {
    $resp = Read-Host '  Deseja remover o programa SentencIA deste computador? [s/N]'
    if ($resp -notmatch '^(s|sim|y|yes)$') {
        Write-Host '  Operacao cancelada. Nada foi removido.'
        exit 0
    }
}

$removerDados = $false
if ($ApagarDados) {
    if ($Sim) {
        $removerDados = $true
    } else {
        $respD = Read-Host '  Deseja APAGAR tambem os dados do gabinete (autos, configuracoes e precedentes)? [s/N]'
        if ($respD -match '^(s|sim|y|yes)$') { $removerDados = $true }
    }
} elseif (-not $Sim) {
    $respM = Read-Host "  Manter seus dados do gabinete em '$Dados'? [S/n]"
    if ($respM -match '^(n|nao|no)$') { $removerDados = $true }
}

# Encerrar servidor se estiver ativo
$pyVenv = Join-Path $Destino '.venv\Scripts\python.exe'
$launcher = Join-Path $Destino 'launcher.py'
if ((Test-Path -LiteralPath $pyVenv) -and (Test-Path -LiteralPath $launcher)) {
    $env:JVDFM_DATA_DIR = $Dados
    try { & $pyVenv $launcher --parar 2>$null | Out-Null } catch { }
}

# Remover atalhos
# So remove atalhos que apontam para ESTA instalacao (nunca um atalho de outra copia do SentencIA).
function Test-AtalhoDestaInstalacao([string]$Caminho) {
    try {
        $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($Caminho)
        $alvo = ($sc.TargetPath + ' ' + $sc.Arguments + ' ' + $sc.WorkingDirectory)
        return $alvo.ToLowerInvariant().Contains($Destino.TrimEnd('\').ToLowerInvariant())
    } catch { return $false }
}
$atalhoDesk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'SentencIA.lnk'
$menuDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\SentencIA'
if ((Test-Path -LiteralPath $atalhoDesk) -and (Test-AtalhoDestaInstalacao $atalhoDesk)) { Remove-Item -LiteralPath $atalhoDesk -Force -ErrorAction SilentlyContinue }
$atalhoMenu = Join-Path $menuDir 'SentencIA.lnk'
if ((Test-Path -LiteralPath $atalhoMenu) -and (Test-AtalhoDestaInstalacao $atalhoMenu)) { Remove-Item -LiteralPath $menuDir -Recurse -Force -ErrorAction SilentlyContinue }

# Remover registro HKCU (so se for desta instalacao)
$regPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SentencIA'
if (Test-Path -LiteralPath $regPath) {
    $local = (Get-ItemProperty -Path $regPath -ErrorAction SilentlyContinue).InstallLocation
    if ($local -and ($local.TrimEnd('\') -ieq $Destino.TrimEnd('\'))) { Remove-Item -Path $regPath -Recurse -Force -ErrorAction SilentlyContinue }
}

# Remover programa
if (Test-Path -LiteralPath $Destino) {
    Remove-Item -LiteralPath $Destino -Recurse -Force -ErrorAction SilentlyContinue
}

if ($removerDados -and (Test-Path -LiteralPath $Dados)) {
    Remove-Item -LiteralPath $Dados -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "  Dados removidos: $Dados" -ForegroundColor Yellow
} else {
    Write-Host "  Seus dados foram mantidos com seguranca em: $Dados" -ForegroundColor Green
}

Write-Host "`n  Desinstalacao concluida." -ForegroundColor Green
