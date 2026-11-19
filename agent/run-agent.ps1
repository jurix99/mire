# Lance un passage de recherche d'offres pour Mire (Claude Code en mode non interactif).
#
#   powershell -ExecutionPolicy Bypass -File agent\run-agent.ps1                 # passage « planifie »
#   powershell -ExecutionPolicy Bypass -File agent\run-agent.ps1 -Kind approfondi
#
# L'agent n'a accès qu'à la recherche web, à la lecture de pages, à l'écriture de fichiers dans agent/tmp/
# (corps JSON longs) et au client de l'API de Mire (agent/mire_api.py) : aucune autre commande. Journal : agent\logs\run-AAAAMMJJ-HHmm.log
param(
    [ValidateSet("planifie", "manuel", "approfondi")]
    [string]$Kind = "planifie",
    [string]$Model = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Logs = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force $Logs | Out-Null
$Log = Join-Path $Logs ("run-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmm"))

function Write-Log($Text) {
    $line = "{0}  {1}" -f (Get-Date -Format "HH:mm:ss"), $Text
    Add-Content -Path $Log -Value $line -Encoding utf8
    Write-Host $line
}

# 1. Mire doit être démarré (Docker dans WSL) : sinon on le lance et on attend
function Test-Mire {
    try {
        $r = & uv run --no-project python agent/mire_api.py GET context 2>$null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}
if (-not (Test-Mire)) {
    Write-Log "Mire injoignable : démarrage de docker compose dans WSL"
    $wslPath = (wsl -e wslpath -a ($Root -replace '\\', '/')).Trim()
    wsl -e sh -c "cd '$wslPath' && docker compose up -d" | Out-Null
    $ok = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 5
        if (Test-Mire) { $ok = $true; break }
    }
    if (-not $ok) { Write-Log "Échec : Mire ne répond pas (AGENT_TOKEN configuré ?)"; exit 1 }
}

# 2. Passage de recherche
$prompt = Get-Content -Raw -Encoding utf8 (Join-Path $PSScriptRoot "prompt.md")
$prompt += "`n`nType de ce passage : $Kind. Date : $(Get-Date -Format 'dddd d MMMM yyyy')."
$allowed = "WebSearch,WebFetch,Write(agent/tmp/**),Bash(uv run --no-project python agent/mire_api.py:*)"
$env:MSYS_NO_PATHCONV = "1"  # Git Bash : ne pas convertir les arguments en chemins Windows
New-Item -ItemType Directory -Force (Join-Path $PSScriptRoot "tmp") | Out-Null
$claudeArgs = @("-p", $prompt, "--allowedTools", $allowed, "--no-session-persistence", "--output-format", "text")
if ($Model) { $claudeArgs += @("--model", $Model) }

Write-Log "Début du passage ($Kind)"
& claude @claudeArgs 2>&1 | Tee-Object -FilePath $Log -Append
Write-Log ("Fin du passage (code {0})" -f $LASTEXITCODE)
exit $LASTEXITCODE
