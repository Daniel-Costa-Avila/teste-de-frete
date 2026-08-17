<#
Remove a tarefa agendada do watchdog do servidor "Teste de Frete"
e encerra o processo do servidor, se estiver rodando.
#>

$TaskName = "TesteDeFrete-Servidor"

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "Tarefa '$TaskName' removida."

Get-Process -Name "python", "powershell" -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like "*Teste de frete*" -or $_.MainWindowTitle -like "*watchdog_servidor*" } |
    Stop-Process -Force -ErrorAction SilentlyContinue

Write-Host "Processos relacionados encerrados (se havia algum rodando)."
