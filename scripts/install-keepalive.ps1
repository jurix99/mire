# Garde Mire allumé : tâche planifiée « Mire - garder WSL allumé », lancée à l'ouverture de session Windows.
# Elle démarre Mire (docker compose dans WSL) puis maintient un processus WSL caché (« sleep infinity ») :
# sans lui, WSL éteint sa machine virtuelle environ une minute après la dernière commande, et Mire avec.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install-keepalive.ps1            # installer + démarrer
#   powershell -ExecutionPolicy Bypass -File scripts\install-keepalive.ps1 -Remove    # supprimer
param([switch]$Remove)

$Name = "Mire - garder WSL allumé"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Tâche supprimée : $Name (le processus en cours s'arrêtera à la fin de la session)"
    exit 0
}

$Root = Split-Path -Parent $PSScriptRoot
$wslRoot = (wsl -e wslpath -a ($Root -replace '\\', '/')).Trim()
# 1. démarre Mire ; 2. garde WSL allumé (la commande ne se termine jamais)
$command = "wsl.exe -e sh -c 'cd \""$wslRoot\"" && docker compose up -d >/dev/null 2>&1; exec sleep infinity'"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"$command`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Name -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Démarre Mire et garde WSL allumé tant que la session est ouverte" -Force | Out-Null
Start-ScheduledTask -TaskName $Name
Write-Host "Tâche installée et démarrée : $Name (à chaque ouverture de session)"
