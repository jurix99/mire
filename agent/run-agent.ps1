# Lance l'agent de découverte de Mire (Claude Code en mode non interactif) : recenser les entreprises
# françaises qui ont levé des fonds (les offres sont collectées par Mire, chaque jour).
#
#   powershell -ExecutionPolicy Bypass -File agent\run-agent.ps1                       # 2 dernières semaines
#   powershell -ExecutionPolicy Bypass -File agent\run-agent.ps1 -Kind rattrapage      # 6 mois, mois par mois
#   powershell -ExecutionPolicy Bypass -File agent\run-agent.ps1 -Kind rattrapage -Months 12
#   powershell -ExecutionPolicy Bypass -File agent\run-agent.ps1 -Kind offres            # offres hors ATS lisible
#   powershell -ExecutionPolicy Bypass -File agent\run-agent.ps1 -Kind recherche         # entreprises trouvées par leurs offres
#
# offres : l'agent lit les pages d'offres que Mire ne lit pas seul (Welcome to the Jungle, Notion, page
# carrières…) et enregistre les offres tech ouvertes (consignes : agent/prompt-offres.md).
# recherche : l'agent part des offres qui correspondent aux profils pour trouver des entreprises que Mire ne
# connaît pas (consignes : agent/prompt-recherche.md).
# Plafond de dépense par passage (facultatif) : $env:AGENT_MAX_BUDGET_USD = "5" (--max-budget-usd de claude).
# rattrapage : une exécution de l'agent par tranche d'un mois, de la plus récente à la plus ancienne (chaque
# tranche est un passage distinct : une interruption ne fait pas perdre les précédentes).
# L'agent n'a accès qu'à la recherche web, à la lecture de pages, à l'écriture de fichiers dans agent/tmp/
# et au client de l'API de Mire (agent/mire_api.py) : aucune autre commande.
# Journal : agent\logs\run-AAAAMMJJ-HHmm.log
param(
    [ValidateSet("decouverte", "rattrapage", "offres", "recherche")]
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

# 1. Mire doit répondre. URL : variable MIRE_URL (défaut http://localhost:8080, docker compose dans WSL).
#    Production : $env:MIRE_URL = "https://<domaine>" et $env:MIRE_AGENT_TOKEN = "<agent-token de Key Vault>".
#    Mire local injoignable : on lance docker compose et on attend ; Mire distant injoignable : échec.
$MireUrl = if ($env:MIRE_URL) { $env:MIRE_URL.TrimEnd('/') } else { "http://localhost:8080" }
$IsLocal = $MireUrl -match '^https?://(localhost|127\.0\.0\.1)(:\d+)?$'
Write-Log "Mire : $MireUrl"
function Test-Mire {
    try {
        $r = & uv run --no-project python agent/mire_api.py GET context 2>$null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}
if (-not (Test-Mire) -and -not $IsLocal) {
    # L'API distante peut être à zéro réplique (démarrage à froid) : une deuxième chance
    Start-Sleep -Seconds 20
    if (-not (Test-Mire)) { Write-Log "Échec : Mire ne répond pas à $MireUrl (MIRE_AGENT_TOKEN correct ?)"; exit 1 }
} elseif (-not (Test-Mire)) {
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

function Invoke-Claude([string]$Prompt, [string]$Label) {
    $claudeArgs = @("-p", $Prompt, "--allowedTools", $allowed, "--no-session-persistence", "--output-format", "text")
    if ($Model) { $claudeArgs += @("--model", $Model) }
    if ($env:AGENT_MAX_BUDGET_USD) { $claudeArgs += @("--max-budget-usd", $env:AGENT_MAX_BUDGET_USD) }
    Write-Log "Début : $Label"
    & claude @claudeArgs 2>&1 | ForEach-Object {
        $line = "$_"
        Add-Content -Path $Log -Value $line -Encoding utf8  # Tee-Object écrirait en UTF-16 sous PowerShell 5.1
        $line
    }
    Write-Log ("Fin : {0} (code {1})" -f $Label, $LASTEXITCODE)
    return $LASTEXITCODE
}

function Invoke-Slice([datetime]$From, [datetime]$To, [string]$RunKind) {
    $period = "du {0} au {1}" -f $From.ToString("d MMMM yyyy"), $To.ToString("d MMMM yyyy")
    $prompt = $basePrompt + "`n`n## Cette exécution`nPériode à couvrir : $period (dates des annonces de levée).`n" +
        "TYPE du passage : $RunKind. Date du jour : $(Get-Date -Format 'dddd d MMMM yyyy')."
    return Invoke-Claude $prompt "$period ($RunKind)"
}

$today = Get-Date
$worst = 0
if ($Kind -eq "decouverte") {
    $worst = Invoke-Slice $today.AddDays(-14) $today "decouverte"
} elseif ($Kind -eq "offres") {
    # Offres des entreprises dont Mire ne lit pas l'ATS (Welcome to the Jungle, Notion, page carrières…)
    $prompt = (Get-Content -Raw -Encoding utf8 (Join-Path $PSScriptRoot "prompt-offres.md")) +
        "`n`n## Cette exécution`nDate du jour : $(Get-Date -Format 'dddd d MMMM yyyy')."
    $worst = Invoke-Claude $prompt "offres hors ATS"
} elseif ($Kind -eq "recherche") {
    # Entreprises inconnues, trouvées à partir des offres qui correspondent aux profils
    $prompt = (Get-Content -Raw -Encoding utf8 (Join-Path $PSScriptRoot "prompt-recherche.md")) +
        "`n`n## Cette exécution`nDate du jour : $(Get-Date -Format 'dddd d MMMM yyyy')."
    $worst = Invoke-Claude $prompt "recherche partant des offres"
} else {
    for ($i = 0; $i -lt $Months; $i++) {
        $code = Invoke-Slice $today.AddMonths(-($i + 1)) $today.AddMonths(-$i) "approfondi"
        if ($code -ne 0) { $worst = $code }
    }
}
exit $worst
