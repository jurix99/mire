"""Évaluation de référence avec le VRAI Gemini (hors tests unitaires : appels réseau, coût).

Rejoue la boucle d'apprentissage de docs/processus-recherche.md (§7) sur une base SQLite temporaire
(la base de l'application n'est pas touchée) :
1. profil factuel (proche de docs/exemple-profil.txt, sans les règles apprises) ;
2. retours réels : Scoreplay, Kuli, Ooak Data, Fabriq, Bluesand, Ecair, Waniwani (pas intéressé),
   Remanence, Parallel, Alta Ares (aimées) ;
3. synthèse des règles -> on vérifie que les thèmes du profil d'exemple sont couverts, avec exemples cités ;
4. on accepte toutes les règles, puis on évalue les offres : Remanence doit être mieux notée qu'Ooak Data
   et Bluesand.

Lancer (dans le conteneur, la clé Gemini vient de .env) :
  docker compose exec backend uv run python scripts/eval_reference.py
Code de retour 0 si les critères sont atteints, 1 sinon.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = Path(tempfile.mkdtemp(prefix="mire-eval-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'eval.db').as_posix()}"
os.environ.setdefault("SESSION_SECRET", "eval")

from app.assessments import assess_user  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.feedback import REASONS  # noqa: E402
from app.llm import get_llm  # noqa: E402
from app.models import Company, Job, JobAssessment, JobFeedback, User  # noqa: E402
from app.profile import ProfileData, get_profile, profile_data, save_fields  # noqa: E402
from app.proposals import apply_items  # noqa: E402
from app.rules_synthesis import synthesize  # noqa: E402

PROFILE = {
    "headline": "Platform / DevOps engineer",
    "years_experience": 5,
    "location": "Paris",
    "skills": [{"name": n} for n in ("Python", "Terraform", "CI/CD", "Docker", "Kubernetes", "ArgoCD", "Helm",
                                     "Azure")] + [{"name": "React", "kind": "a_developper"}],
    "experiences": [
        {"company": "Startup IA santé", "title": "Platform Engineer", "start": "2023-01",
         "description": "Industrialisation de prototypes IA en production : Kubernetes, ArgoCD, Helm, Terraform, Azure"},
        {"company": "ESN", "title": "DevOps Engineer", "start": "2021-01", "end": "2022-12",
         "description": "CI/CD, Docker, Kubernetes sur Azure"},
    ],
    "preferences": {"locations": ["Paris"], "work_modes": ["hybride", "sur_site", "remote"],
                    "company_stages": ["Pré-seed", "Seed", "Série A"]},
}

# (entreprise, titre, stack / description, sentiment, raisons, commentaire)
FEEDBACK = [
    ("Scoreplay", "Platform Engineer", "Deep AWS expertise required (multi-region, EKS)", "dislike", ["cloud_impose"],
     "deep AWS expertise, or je n'ai pas du tout AWS"),
    ("Kuli", "AI Engineer", "Agents, LangGraph, evals au cœur du poste", "dislike", ["trop_ia"], "trop IA"),
    ("Ooak Data", "Fullstack Engineer", "TypeScript, React, PostgreSQL ; salaire 55-80 k€", "dislike",
     ["trop_front", "salaire_bas"], "trop front TypeScript et base de données + pas assez payé"),
    ("Fabriq", "Senior Platform Engineer", "7+ years of experience, AWS mandatory", "dislike",
     ["trop_senior", "cloud_impose"], "trop haut en XP et AWS obligatoire"),
    ("Bluesand", "Data Engineer", "SQL, dbt, BigQuery, modélisation de données", "dislike", ["trop_sql"],
     "SQL à fond"),
    ("Ecair", "Fullstack Engineer", "TypeScript, Next.js", "dislike", ["trop_front"], "TypeScript"),
    ("Waniwani", "Forward Deployed Engineer", "Déploiement chez les clients, nombreux déplacements", "dislike",
     ["deplacements"], "mitigé, peur des déplacements"),
    ("Remanence", "Member of Technical Staff, Platform Engineering",
     "Kubernetes en prod, Terraform, Helm, Argo CD, GitHub Actions, Python ; jeune startup d'agents IA ; Paris hybride",
     "like", [], "l'offre est top"),
    ("Parallel", "Senior Fullstack Engineer", "Terraform, K8s, Docker, AWS + agents IA en santé", "like", [],
     "stack infra identique"),
    ("Alta Ares", "MLOps Engineer", "Possède la plateforme IA, de la recherche à la prod", "like", [], ""),
]

# Thèmes attendus (docs/exemple-profil.txt) : au moins un mot-clé dans le texte d'une règle proposée
THEMES = {
    "front / TypeScript": ("typescript", "front"),
    "SQL / data": ("sql", "data engineering"),
    "trop IA (Kuli)": ("kuli", "langgraph", "métier principal", "trop ia", "agents"),
    "cloud imposé / AWS": ("aws", "cloud"),
    "expérience 7+ ans": ("7+", "7 ans", "expérience", "senior"),
    "déplacements (FDE)": ("déplacement", "forward deployed", "fde"),
    "salaire": ("salaire", "55", "payé"),
    "préférer plateforme K8s / GitOps": ("gitops", "kubernetes", "plateforme", "remanence"),
}


def main() -> int:
    llm = get_llm()
    if llm is None:
        print("GEMINI_API_KEY absente : évaluation impossible.")
        return 1
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        user = User(linkedin_sub="eval", name="Évaluation")
        db.add(user)
        db.commit()
        profile = get_profile(db, user)
        save_fields(profile, ProfileData.model_validate(PROFILE))
        jobs = {}
        for company, title, text, sentiment, reasons, comment in FEEDBACK:
            job = Job(company=Company(name=company, slug=company.lower().replace(" ", "-")), title=title,
                      stack=text, summary=text, location="Paris")
            db.add(job)
            db.flush()
            jobs[company] = job
            db.add(JobFeedback(user_id=user.id, job_id=job.id, sentiment=sentiment, reasons=reasons,
                               text=comment or None))
        # Comme la route « J'aime » : les offres aimées deviennent des offres de référence
        data = profile_data(profile)
        refs = [{"job_id": jobs[c].id, "title": t, "company": c, "why": com or None}
                for c, t, _, s, _, com in FEEDBACK if s == "like"]
        save_fields(profile, data.model_copy(update={"reference_jobs": ProfileData.model_validate(
            {"reference_jobs": refs}).reference_jobs}), {"reference_jobs"})
        db.commit()

        # --- Synthèse des règles ---
        items, used = synthesize(db, user, profile_data(profile), llm, REASONS)
        db.commit()
        print(f"\n{used} retours -> {len(items)} règles proposées :")
        for it in items:
            print(f"  [{it['op']}] {it['display']}\n      d'après : {', '.join(it['examples'])} — {it['reason']}")
        texts = " ".join(it["display"].lower() for it in items)
        covered = {name: any(k in texts for k in keys) for name, keys in THEMES.items()}
        print("\nThèmes du profil d'exemple couverts :")
        for name, ok in covered.items():
            print(f"  {'OK ' if ok else '-- '} {name}")
        all_cited = all(it["examples"] for it in items)

        # --- Règles acceptées puis évaluation ---
        merged, touched = apply_items(profile_data(profile), items)
        save_fields(profile, merged, touched)
        db.commit()
        db.query(JobAssessment).delete()
        db.commit()
        assess_user(db, user, llm, limit=20)
        scores = {}
        print("\nÉvaluation des offres avec les règles acceptées :")
        for a in db.query(JobAssessment).all():
            company = a.job.company.name
            scores[company] = float(a.score) if a.score is not None else 0.0
            print(f"  {scores[company]:>4} {a.verdict:<8} {company:<11} {a.exclusion or a.why or ''}"[:160])

    ok_themes = sum(covered.values())
    ranking_ok = scores.get("Remanence", 0) > max(scores.get("Ooak Data", 0), scores.get("Bluesand", 0))
    print(f"\nRésultat : {ok_themes}/{len(THEMES)} thèmes, exemples cités : {all_cited}, "
          f"Remanence mieux notée qu'Ooak Data et Bluesand : {ranking_ok}")
    return 0 if ok_themes >= len(THEMES) - 2 and all_cited and ranking_ok else 1


if __name__ == "__main__":
    sys.exit(main())
