"""Client minimal de l'API de l'agent de Mire (bibliothèque standard uniquement).

Usage (depuis la racine du dépôt) :
  uv run --no-project python agent/mire_api.py GET context
  uv run --no-project python agent/mire_api.py POST runs '{"kind": "planifie"}'
  uv run --no-project python agent/mire_api.py POST jobs @agent/tmp/job.json
  (chemin SANS « / » initial : sous Git Bash, « /context » serait converti en chemin Windows)

- Seules les routes /api/agent/... sont accessibles (le chemin est relatif à cette base).
- URL : variable MIRE_URL (défaut http://localhost:8080 ; production : https://<domaine>).
- Jeton : variable MIRE_AGENT_TOKEN (ou AGENT_TOKEN) ; à défaut, et seulement pour un Mire local, AGENT_TOKEN
  lu dans le fichier .env à la racine du dépôt (le jeton de production est dans Key Vault : agent-token).
Affiche la réponse JSON ; code de retour 0 si succès HTTP, 1 sinon (le corps de l'erreur est affiché).
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METHODS = {"GET", "POST", "PATCH"}


def base_url() -> str:
    return os.environ.get("MIRE_URL", "http://localhost:8080").rstrip("/")


def is_local(url: str) -> bool:
    return re.match(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$", url) is not None


def token() -> str:
    value = (os.environ.get("MIRE_AGENT_TOKEN") or os.environ.get("AGENT_TOKEN") or "").strip()
    if value:
        return value
    if not is_local(base_url()):  # le .env contient le jeton LOCAL : jamais envoyé à un autre serveur
        sys.exit("Jeton introuvable : définissez MIRE_AGENT_TOKEN (jeton agent-token de la production)")
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("AGENT_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"')
    sys.exit("Jeton introuvable : définissez MIRE_AGENT_TOKEN ou AGENT_TOKEN dans .env")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0].upper() not in METHODS:
        print(__doc__)
        return 2
    method, path = argv[0].upper(), argv[1].replace("\\", "/")
    if "Program Files/Git" in path or re.match(r"^[A-Za-z]:/", path):
        print(json.dumps({"error": "Chemin converti par Git Bash : écrivez-le sans « / » initial (ex. context, jobs)"}))
        return 2
    path = "/" + path.lstrip("/")
    # Chemin relatif à /api/agent : pas de schéma, pas de « .. », caractères simples uniquement
    if not re.fullmatch(r"/[A-Za-z0-9_/\-]*(\?[A-Za-z0-9_=&\-]*)?", path) or ".." in path:
        print(json.dumps({"error": "Chemin invalide (attendu : context, jobs, runs/12/finish…)"}))
        return 2
    body = None
    if len(argv) > 2:
        raw = argv[2]
        if raw.startswith("@"):
            file = (ROOT / raw[1:]).resolve()
            if (ROOT / "agent" / "tmp").resolve() not in file.parents:
                print(json.dumps({"error": "Fichier de corps attendu dans agent/tmp/"}))
                return 2
            raw = file.read_text(encoding="utf-8")
        body = json.dumps(json.loads(raw)).encode()
    base = base_url()
    if not base.startswith("https://") and not is_local(base):
        print(json.dumps({"error": "MIRE_URL distante : https:// obligatoire (le jeton ne circule jamais en clair)"}))
        return 2
    req = urllib.request.Request(f"{base}/api/agent{path}", data=body, method=method, headers={
        "Authorization": f"Bearer {token()}", "Content-Type": "application/json", "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            print(resp.read().decode("utf-8"))
            return 0
    except urllib.error.HTTPError as exc:
        print(json.dumps({"status": exc.code, "error": json.loads(exc.read() or b"null")}, ensure_ascii=False))
        return 1
    except urllib.error.URLError as exc:
        print(json.dumps({"error": f"Mire injoignable : {exc.reason}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
