<#
Registra o watchdog do servidor "Teste de Frete" no Agendador de Tarefas do Windows,
para que ele inicie automaticamente no logon do usuario e, caso o Task Scheduler
detecte que a tarefa parou, ele mesmo reinicie.

Execute este script uma unica vez (pode precisar rodar como Administrador).
#>

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$BatPath    = Join-Path $ProjectDir "scripts\iniciar_servidor.bat"
$TaskName   = "TesteDeFrete-Servidor"

$action = New-ScheduledTaskAction -Execute $BatPath -WorkingDirectory $ProjectDir

$trigger1 = New-ScheduledTaskTrigger -AtLogOn
$trigger2 = New-ScheduledTaskTrigger -AtStartup

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask -TaskName $TaskName `
    -Action $action `
    -Trigger @($trigger1, $trigger2) `
    -Settings $settings `
    -Principal $principal `
    -Description "Mantem o servidor Teste de Frete rodando automaticamente, com auto-reinicio via watchdog."

Write-Host "Tarefa '$TaskName' registrada com sucesso."
Write-Host "O servidor ira iniciar automaticamente no proximo logon/boot."
Write-Host ""
Write-Host "Para iniciar agora sem reiniciar o computador, rode:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
