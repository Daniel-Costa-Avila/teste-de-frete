<#
Watchdog do servidor "Teste de Frete".
Mantem o servidor Flask rodando: inicia o processo, monitora http://HOST:PORT/health
e reinicia automaticamente caso o processo caia ou pare de responder.
#>

$ErrorActionPreference = "SilentlyContinue"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python     = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$Script     = Join-Path $ProjectDir "_run_server.py"
$LogFile    = Join-Path $ProjectDir "scripts\servidor.log"
$EnvFile    = Join-Path $ProjectDir ".env"

$Host_ = $env:HOST
$Port  = $env:PORT
if ((Test-Path $EnvFile) -and (-not $Host_ -or -not $Port)) {
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match '^\s*HOST\s*=\s*(.+?)\s*$' -and -not $Host_) { $Host_ = $Matches[1] }
        if ($line -match '^\s*PORT\s*=\s*(.+?)\s*$' -and -not $Port)  { $Port  = $Matches[1] }
    }
}
if (-not $Host_) { $Host_ = "127.0.0.1" }
if (-not $Port)  { $Port  = "5000" }
$HealthHost = if ($Host_ -eq "0.0.0.0") { "127.0.0.1" } else { $Host_ }
$HealthUrl  = "http://$HealthHost`:$Port/health"

function Write-Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $LogFile -Value $line
    Write-Host $line
}

function Test-ServerHealthy {
    try {
        $resp = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec 5 -UseBasicParsing
        return $resp.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Start-ServerProcess {
    Write-Log "Iniciando servidor..."
    $proc = Start-Process -FilePath $Python -ArgumentList "`"$Script`"" `
        -WorkingDirectory $ProjectDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $ProjectDir "scripts\servidor.out.log") `
        -RedirectStandardError  (Join-Path $ProjectDir "scripts\servidor.err.log") `
        -PassThru
    return $proc
}

Write-Log "===== Watchdog iniciado ====="

$proc = Start-ServerProcess

while ($true) {
    Start-Sleep -Seconds 10

    $processAlive = $proc -and (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)

    if (-not $processAlive) {
        Write-Log "Processo do servidor caiu. Reiniciando..."
        $proc = Start-ServerProcess
        Start-Sleep -Seconds 5
        continue
    }

    if (-not (Test-ServerHealthy)) {
        Write-Log "Servidor nao respondeu ao /health. Encerrando processo e reiniciando..."
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        $proc = Start-ServerProcess
    }
}
