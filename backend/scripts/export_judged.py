"""Exporte le jeu de référence d'un utilisateur : ses offres jugées, figées (voir app/relevance_eval.py).

Sur la vraie base (lecture seule). Le fichier contient le profil de l'utilisateur : il reste dans eval_data/
(ignoré par git) et ne doit pas être partagé.

  docker compose exec backend uv run python scripts/export_judged.py            # utilisateur le plus fourni
  docker compose exec backend uv run python scripts/export_judged.py --user-id 3 --out eval_data/moi.json
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402
from app.relevance_eval import export_dataset, judged_users, to_json  # noqa: E402

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "eval_data"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user-id", type=int, help="utilisateur (défaut : celui qui a jugé le plus d'offres)")
    parser.add_argument("--out", type=Path, help="fichier de sortie (défaut : eval_data/user-<id>.json)")
    args = parser.parse_args()
    with SessionLocal() as db:
        users = judged_users(db)
        if not users:
            print("Aucune offre jugée dans cette base.")
            return 1
        print("Offres jugées par utilisateur : " + ", ".join(f"#{u} : {n}" for u, n in users[:10]))
        user_id = args.user_id or users[0][0]
        user = db.get(User, user_id)
        if user is None:
            print(f"Utilisateur {user_id} introuvable.")
            return 1
        dataset = export_dataset(db, user)
    out = args.out or DEFAULT_DIR / f"user-{user_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_json(dataset), encoding="utf-8")
    print(f"{len(dataset['items'])} offres exportées ({dataset['counts']}) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
