# Installe (ou met à jour) les tâches planifiées Windows de l'agent de Mire, rattrapées au réveil si le PC était
# éteint ou en veille :
# - « Mire - découverte des entreprises » : chaque lundi à 7h43 (entreprises qui ont levé des fonds) ;
# - « Mire - offres hors ATS » : chaque jeudi à 7h43 (offres publiées sur Welcome to the Jungle, Notion, pages
#   carrières : ce que Mire ne lit pas seul).
# (La collecte quotidienne sur les ATS lisibles et l'évaluation des offres sont faites par Mire lui-même.)
#
#   powershell -ExecutionPolicy Bypass -File agent\install-task.ps1
#   powershell -ExecutionPolicy Bypass -File agent\install-task.ps1 -Remove    # supprimer les tâches
param([switch]$Remove)

$Tasks = @(
    @{ Name = "Mire - découverte des entreprises"; Kind = "decouverte"; Day = "Monday"; Hours = 2
       Description = "Découverte hebdomadaire des entreprises qui ont levé des fonds (agent Claude Code, voir agent/prompt.md)" },
    @{ Name = "Mire - offres hors ATS"; Kind = "offres"; Day = "Thursday"; Hours = 2
       Description = "Offres des entreprises dont Mire ne lit pas l'ATS : Welcome to the Jungle, Notion… (voir agent/prompt-offres.md)" }
)
Unregister-ScheduledTask -TaskName "Mire - recherche d'offres" -Confirm:$false -ErrorAction SilentlyContinue  # ancien nom

if ($Remove) {
    foreach ($t in $Tasks) {
        Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "Tâche supprimée : $($t.Name)"
    }
    exit 0
}

$Script = Join-Path $PSScriptRoot "run-agent.ps1"
foreach ($t in $Tasks) {
    $Action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`" -Kind $($t.Kind)" `
        -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
    $Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $t.Day -At "07:43"
    $Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours $t.Hours) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $t.Name -Action $Action -Trigger $Trigger -Settings $Settings `
        -Description $t.Description -Force | Out-Null
    Write-Host "Tâche installée : $($t.Name) (chaque $($t.Day) à 7h43, rattrapée si le PC était éteint)"
}
Write-Host "Lancer tout de suite : Start-ScheduledTask -TaskName `"Mire - offres hors ATS`""
