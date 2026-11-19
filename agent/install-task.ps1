# Installe (ou met à jour) la tâche planifiée Windows « Mire - recherche d'offres » :
# lundi et jeudi à 7h43, rattrapée au réveil si le PC était éteint ou en veille.
#
#   powershell -ExecutionPolicy Bypass -File agent\install-task.ps1
#   powershell -ExecutionPolicy Bypass -File agent\install-task.ps1 -Remove    # supprimer la tâche
param([switch]$Remove)

$Name = "Mire - recherche d'offres"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Tâche supprimée : $Name"
    exit 0
}

$Script = Join-Path $PSScriptRoot "run-agent.ps1"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`" -Kind planifie" `
    -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Thursday -At "07:43"
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Name -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Passage de recherche d'offres de Mire (agent Claude Code, voir agent/prompt.md)" -Force | Out-Null
Write-Host "Tâche installée : $Name (lundi et jeudi à 7h43, rattrapée si le PC était éteint)"
Write-Host "Lancer tout de suite : Start-ScheduledTask -TaskName `"$Name`""
