"""Mesure la pertinence de la notation sur un jeu de référence (voir app/relevance_eval.py).

Rejoue le pipeline de notation de production (exclusions dures + Gemini) dans une base SQLite temporaire : la
base de l'application n'est pas touchée. Coût : une évaluation Gemini par offre jugée et par passage.

  # mesure de référence, enregistrée sous eval_data/runs/<nom>.json
  docker compose exec backend uv run python scripts/eval_relevance.py eval_data/user-1.json --name avant
  # variante v2 de la notation (assessments.py), même jeu
  docker compose exec backend uv run python scripts/eval_relevance.py eval_data/user-1.json --name v2 --variant v2
  # autre modèle, 3 passages pour mesurer la stabilité des notes
  docker compose exec backend uv run python scripts/eval_relevance.py eval_data/user-1.json --name flash \\
      --models gemini-3.8-flash --repeat 3
  # comparer deux mesures enregistrées (sans appel Gemini)
  docker compose exec backend uv run python scripts/eval_relevance.py --compare avant flash
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "eval_data" / "runs"


def _load_run(name: str) -> dict:
    path = Path(name) if name.endswith(".json") else RUNS / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dataset", nargs="?", type=Path, help="jeu exporté par scripts/export_judged.py")
    parser.add_argument("--name", help="nom de la mesure (défaut : date et heure)")
    parser.add_argument("--models", help="modèles Gemini, séparés par des virgules (défaut : GEMINI_EVAL_MODELS)")
    parser.add_argument("--variant", choices=("v1", "v2"),
                        help="variante de notation (défaut : SCORING_VARIANT, sinon v1)")
    parser.add_argument("--folds", type=int, default=2,
                        help="validation croisée (défaut 2) ; 1 = profil tel quel, résultat optimiste")
    parser.add_argument("--repeat", type=int, default=1, help="passages (stabilité des notes)")
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"), help="compare deux mesures enregistrées")
    args = parser.parse_args()

    # Base temporaire AVANT d'importer l'application (app.db lit DATABASE_URL à l'import) : la base de
    # l'application n'est jamais touchée
    tmp = Path(tempfile.mkdtemp(prefix="mire-relevance-"))
    os.environ["DATABASE_URL"] = f"sqlite:///{(tmp / 'eval.db').as_posix()}"
    os.environ.setdefault("SESSION_SECRET", "eval")
    if args.variant:
        os.environ["SCORING_VARIANT"] = args.variant
    variant = os.environ.get("SCORING_VARIANT") or "v1"
    sys.path.insert(0, str(ROOT))
    from app.db import Base, SessionLocal, engine
    from app.llm import get_llm
    from app.relevance_eval import compare, format_report, load_dataset, run_scoring, summarize_runs, to_json

    if args.compare:
        print(compare(*(_load_run(n) for n in args.compare)))
        return 0
    if not args.dataset:
        parser.error("jeu de référence manquant")

    llm = get_llm()
    if llm is None:
        print("GEMINI_API_KEY absente : mesure impossible.")
        return 1
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    models = [m.strip() for m in args.models.split(",")] if args.models else None
    Base.metadata.create_all(engine)
    runs = []
    with SessionLocal() as db:
        user = load_dataset(db, dataset)
        for i in range(args.repeat):
            print(f"Passage {i + 1}/{args.repeat} (variante {variant}) : {len(dataset['items'])} offres…", flush=True)
            runs.append(run_scoring(db, user, dataset, llm, folds=args.folds, models=models))
    summary = summarize_runs(dataset, runs)
    print()
    print(format_report(summary))

    name = args.name or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    RUNS.mkdir(parents=True, exist_ok=True)
    out = RUNS / f"{name}.json"
    out.write_text(to_json({
        "nom": name, "date": datetime.now(UTC).isoformat(), "jeu": str(args.dataset), "variante": variant, "modeles": models,
        "folds": args.folds, "resume": summary, "notes": runs,
    }), encoding="utf-8")
    print(f"\nMesure enregistrée : {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
