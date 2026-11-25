"""Lance l'agent de recherche de Mire (Claude Code non interactif), avec un plafond de dépense.

Sert au job planifié côté serveur (Container Apps, image « agent » du Dockerfile) ; marche aussi en local.
agent/run-agent.ps1 reste le lanceur Windows (démarrage de docker compose dans WSL, journal dans agent/logs).

  python agent/run_agent.py decouverte              # annonces de levée des 2 dernières semaines
  python agent/run_agent.py rattrapage --months 6   # 6 mois, une tranche d'un mois par passage
  python agent/run_agent.py offres                  # offres hors ATS lisible (Welcome to the Jungle, Notion…)
  python agent/run_agent.py recherche               # entreprises inconnues, trouvées par leurs offres

Variables d'environnement :
  MIRE_URL, MIRE_AGENT_TOKEN   API de l'agent (client agent/mire_api.py)
  ANTHROPIC_API_KEY            clé de l'API Anthropic utilisée par Claude Code
  AGENT_MODEL                  modèle (défaut : celui de Claude Code)
  AGENT_MAX_BUDGET_USD         plafond PAR PASSAGE, en dollars (défaut 5), appliqué par Claude Code
                               (--max-budget-usd)
  AGENT_TOTAL_BUDGET_USD       plafond de l'exécution entière, rattrapage compris (défaut : 4 x par passage) ;
                               une tranche n'est lancée que s'il reste au moins 0,50 $
  AGENT_CLAUDE_BIN             commande claude (défaut « claude » ; tests : faux binaire)

Les outils de l'agent sont limités à la recherche web, la lecture de pages, l'écriture dans agent/tmp/ et le
client de l'API de Mire : aucune autre commande. Sortie : journal sur la sortie standard (journaux du job) ;
code de retour 0 si tous les passages ont réussi.
"""

import argparse
import calendar
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent"
API_CMD = "uv run --no-project python agent/mire_api.py"
MIN_SLICE_BUDGET = 0.5
KINDS = ("decouverte", "rattrapage", "offres", "recherche")
# Passages en une seule exécution : type -> (consignes, libellé)
SINGLE_PASS = {
    "offres": ("prompt-offres.md", "offres hors ATS"),
    "recherche": ("prompt-recherche.md", "recherche partant des offres"),
}

DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre"]


def log(text: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}  {text}", flush=True)


def fr_date(d: date, weekday: bool = False) -> str:
    text = f"{d.day} {MONTHS[d.month - 1]} {d.year}"
    return f"{DAYS[d.weekday()]} {text}" if weekday else text


def months_back(d: date, n: int) -> date:
    """Même jour n mois plus tôt (borné à la fin du mois)."""
    year, month0 = divmod(d.year * 12 + d.month - 1 - n, 12)
    return date(year, month0 + 1, min(d.day, calendar.monthrange(year, month0 + 1)[1]))


def allowed_tools() -> str:
    tools = ["WebSearch", "WebFetch", "Agent", "Edit(agent/tmp/**)", f"Bash({API_CMD}:*)"]
    if os.name == "nt":  # sous Windows, Claude Code exécute les commandes via PowerShell (ou Bash)
        tools.append(f"PowerShell({API_CMD}:*)")
    return ",".join(tools)


def mire_reachable(attempts: int = 6, pause: float = 15) -> bool:
    """L'API peut être à zéro réplique (démarrage à froid) : quelques essais espacés."""
    for i in range(attempts):
        r = subprocess.run([sys.executable, str(AGENT / "mire_api.py"), "GET", "context"], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8")
        if r.returncode == 0:
            return True
        if i + 1 < attempts:
            time.sleep(pause)
    log(f"Échec : Mire ne répond pas ({(r.stdout or r.stderr).strip()[:300]})")
    return False


def run_claude(prompt: str, label: str, budget: float, model: str | None) -> tuple[int, float]:
    """Un passage de l'agent. Retourne (code, coût en dollars)."""
    cmd = shlex.split(os.environ.get("AGENT_CLAUDE_BIN", "claude")) + [
        "-p", prompt, "--allowedTools", allowed_tools(), "--no-session-persistence",
        "--output-format", "json", "--max-budget-usd", f"{budget:.2f}"]
    if model:
        cmd += ["--model", model]
    log(f"Début : {label} (plafond {budget:.2f} $)")
    started = time.monotonic()
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
    except (json.JSONDecodeError, IndexError):
        out = {}
    cost = float(out.get("total_cost_usd") or 0)
    if out.get("result"):
        print(out["result"], flush=True)
    elif r.stdout.strip():
        print(r.stdout.strip()[-4000:], flush=True)
    if r.stderr.strip():
        print(r.stderr.strip()[-4000:], file=sys.stderr, flush=True)
    code = r.returncode or (1 if out.get("is_error") else 0)
    log(f"Fin : {label} (code {code}, {out.get('subtype', '?')}, {out.get('num_turns', '?')} tours, "
        f"{cost:.2f} $, {round(time.monotonic() - started)} s)")
    return code, cost


def prompts(kind: str, months: int, today: date) -> list[tuple[str, str]]:
    """[(prompt, libellé)] des passages à lancer, dans l'ordre."""
    if kind in SINGLE_PASS:
        prompt_file, label = SINGLE_PASS[kind]
        base = (AGENT / prompt_file).read_text(encoding="utf-8")
        return [(f"{base}\n\n## Cette exécution\nDate du jour : {fr_date(today, weekday=True)}.", label)]
    base = (AGENT / "prompt.md").read_text(encoding="utf-8")

    def slice_(start: date, end: date, run_kind: str) -> tuple[str, str]:
        period = f"du {fr_date(start)} au {fr_date(end)}"
        return (f"{base}\n\n## Cette exécution\nPériode à couvrir : {period} (dates des annonces de levée).\n"
                f"TYPE du passage : {run_kind}. Date du jour : {fr_date(today, weekday=True)}.",
                f"{period} ({run_kind})")

    if kind == "decouverte":
        return [slice_(today - timedelta(days=14), today, "decouverte")]
    # rattrapage : une tranche d'un mois par passage, de la plus récente à la plus ancienne (une interruption ne
    # fait pas perdre les tranches déjà faites)
    return [slice_(months_back(today, i + 1), months_back(today, i), "approfondi") for i in range(months)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=KINDS, nargs="?", default="decouverte")
    parser.add_argument("--months", type=int, default=6, help="rattrapage : nombre de mois (1 à 24)")
    args = parser.parse_args(argv)
    if not 1 <= args.months <= 24:
        parser.error("--months doit être compris entre 1 et 24")
    per_run = float(os.environ.get("AGENT_MAX_BUDGET_USD") or 5)
    total = float(os.environ.get("AGENT_TOTAL_BUDGET_USD") or per_run * 4)
    model = os.environ.get("AGENT_MODEL", "").strip() or None

    log(f"Mire : {os.environ.get('MIRE_URL', 'http://localhost:8080')} ; passage {args.kind} ; "
        f"plafonds {per_run:.2f} $ par passage, {total:.2f} $ au total")
    if not mire_reachable():
        return 1
    (AGENT / "tmp").mkdir(exist_ok=True)
    spent, worst = 0.0, 0
    for prompt, label in prompts(args.kind, args.months, date.today()):
        budget = min(per_run, total - spent)
        if budget < MIN_SLICE_BUDGET:
            log(f"Plafond total atteint ({spent:.2f} $ sur {total:.2f} $) : passages suivants annulés")
            worst = worst or 3
            break
        code, cost = run_claude(prompt, label, budget, model)
        spent += cost
        worst = worst or code
    log(f"Terminé : {spent:.2f} $ dépensés")
    return worst


if __name__ == "__main__":
    sys.exit(main())
