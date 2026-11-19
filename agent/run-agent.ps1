# Lance l'agent de découverte de Mire (Claude Code en mode non interactif) : recenser les entreprises
# françaises qui ont levé des fonds (les offres sont collectées par Mire, chaque jour).
#
#   powershell -ExecutionPolicy Bypass -File agent\run-agent.ps1                       # 2 dernières semaines
#   powershell -ExecutionPolicy Bypass -File agent\run-agent.ps1 -Kind rattrapage      # 6 mois, mois par mois
#   powershell -ExecutionPolicy Bypass -File agent\run-agent.ps1 -Kind rattrapage -Months 12
#
# rattrapage : une exécution de l'agent par tranche d'un mois, de la plus récente à la plus ancienne (chaque
# tranche est un passage distinct : une interruption ne fait pas perdre les précédentes).
# L'agent n'a accès qu'à la recherche web, à la lecture de pages, à l'écriture de fichiers dans agent/tmp/
# et au client de l'API de Mire (agent/mire_api.py) : aucune autre commande.
# Journal : agent\logs\run-AAAAMMJJ-HHmm.log
param(
    [ValidateSet("decouverte", "rattrapage")]
    [string]$Kind = "decouverte",
    [int]$Months = 6,
    [string]$Model = ""
)

$ErrorActionPreference = "Stop"
if ($Months -lt 1 -or $Months -gt 24) { Write-Host "-Months doit être compris entre 1 et 24"; exit 2 }
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

# 2. Passages de l'agent
$basePrompt = Get-Content -Raw -Encoding utf8 (Join-Path $PSScriptRoot "prompt.md")
# Sous Windows, Claude Code exécute les commandes via son outil PowerShell (ou Bash) : même règle pour les deux.
# La lecture de pages peut passer par un sous-agent (outil Agent), soumis aux mêmes règles.
$api = "uv run --no-project python agent/mire_api.py:*"
$allowed = "WebSearch,WebFetch,Agent,Edit(agent/tmp/**),Bash($api),PowerShell($api)"
$env:MSYS_NO_PATHCONV = "1"  # Git Bash : ne pas convertir les arguments en chemins Windows
New-Item -ItemType Directory -Force (Join-Path $PSScriptRoot "tmp") | Out-Null
# Les messages de claude sur stderr ne doivent pas interrompre le script (PowerShell 5.1 + 2>&1)
$ErrorActionPreference = "Continue"
# claude écrit en UTF-8 : sans cela, PowerShell lit sa sortie dans la page de code de la console
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

function Invoke-Slice([datetime]$From, [datetime]$To, [string]$RunKind) {
    $period = "du {0} au {1}" -f $From.ToString("d MMMM yyyy"), $To.ToString("d MMMM yyyy")
    $prompt = $basePrompt + "`n`n## Cette exécution`nPériode à couvrir : $period (dates des annonces de levée).`n" +
        "TYPE du passage : $RunKind. Date du jour : $(Get-Date -Format 'dddd d MMMM yyyy')."
    $claudeArgs = @("-p", $prompt, "--allowedTools", $allowed, "--no-session-persistence", "--output-format", "text")
    if ($Model) { $claudeArgs += @("--model", $Model) }
    Write-Log "Début : $period ($RunKind)"
    & claude @claudeArgs 2>&1 | ForEach-Object {
        $line = "$_"
        Add-Content -Path $Log -Value $line -Encoding utf8  # Tee-Object écrirait en UTF-16 sous PowerShell 5.1
        $line
    }
    Write-Log ("Fin : {0} (code {1})" -f $period, $LASTEXITCODE)
    return $LASTEXITCODE
}

$today = Get-Date
$worst = 0
if ($Kind -eq "decouverte") {
    $worst = Invoke-Slice $today.AddDays(-14) $today "decouverte"
} else {
    for ($i = 0; $i -lt $Months; $i++) {
        $code = Invoke-Slice $today.AddMonths(-($i + 1)) $today.AddMonths(-$i) "approfondi"
        if ($code -ne 0) { $worst = $code }
    }
}
exit $worst
