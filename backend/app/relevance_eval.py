"""Mesure de la pertinence de la notation, sur des offres déjà jugées par un utilisateur.

C'est le juge de toute modification du moteur (présélection, prompt, modèle, barème) : on mesure avant et après.

1. Jeu de référence (export_dataset), une fois, sur la vraie base : les offres jugées par l'utilisateur, avec
   un libellé (label_for) :
   - positive : process postulé / relance / entretien / offre / refusé (il a voulu y aller), ou « J'aime » ;
   - négative : « Pas intéressé » (raisons et commentaire conservés) ;
   - neutre (hors métriques) : « À regarder », « Abandonné ».
   L'offre et son entreprise sont FIGÉES (texte complet) : la mesure ne dépend plus de la base vivante (offres
   fermées, descriptions modifiées). Le fichier contient le profil (données personnelles) : hors du dépôt.

2. Rejeu (load_dataset, run_scoring) dans une base SQLite temporaire, avec le vrai pipeline de notation
   (exclusions dures + Gemini, assessments.assess_user).

   Validation croisée (folds, 2 par défaut) : les offres sont réparties en plis ; pour noter un pli, le profil
   perd tout ce qui vient des jugements de CE pli (offres de référence, exemples des règles, entreprises exclues
   de ces offres). Sinon une offre aimée serait aussi « offre de référence » et jamais écartée : la mesure
   serait faussée. Limite : le TEXTE des règles apprises reste (il a été synthétisé à partir de tous les retours).
   folds=1 : profil tel quel (ce que l'utilisateur voit, mais optimiste).

3. Métriques (compute_metrics) : precision@k (part de positives parmi les k offres les mieux notées : « sur les
   10 premières, combien j'en ai retenu »), AUC (probabilité qu'une positive soit mieux notée qu'une négative),
   positives écartées (et pourquoi), négatives retenues, stabilité des notes d'un passage à l'autre.
"""

import enum
import hashlib
import json
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
from statistics import mean, pstdev
from typing import Any

from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session

from . import taxonomy
from .models import (
    Application,
    Company,
    Job,
    JobAssessment,
    JobFeedback,
    JobSkill,
    ProcessStatus,
    User,
    UserProfile,
)
from .profile import ProfileData, _norm, get_profile, profile_data, save_fields

DATASET_VERSION = 2  # 2 : compétences classées de chaque offre (job_skills)
READABLE_VERSIONS = (1, 2)

POSITIVE_STATUSES = {ProcessStatus.prete, ProcessStatus.postule, ProcessStatus.relance, ProcessStatus.entretien, ProcessStatus.offre,
                     ProcessStatus.refuse}
NEUTRAL_STATUSES = {ProcessStatus.a_postuler, ProcessStatus.abandonne}

# Colonnes jamais exportées : dérivées (clés de dédoublonnage, embeddings), ou propres à la base d'origine
JOB_SKIP = {"embedding", "embedded_at", "url_key", "title_key", "source_id", "score", "created_at", "updated_at"}
COMPANY_SKIP = {"logo_data", "logo_content_type", "logo_url", "logo_fetched_at", "created_at", "updated_at"}


# --- Libellés -------------------------------------------------------------------------------


def label_for(status: ProcessStatus | None, sentiments: list[str]) -> tuple[str, str]:
    """(libellé, origine) : positive | negative | neutral. sentiments : retours de l'offre, du plus ancien au
    plus récent. Le statut du process prime (« Pas intéressé » posé après un « J'aime » l'emporte, une
    candidature après un « Pas intéressé » aussi), puis le dernier retour."""
    if status in POSITIVE_STATUSES:
        return "positive", f"statut:{status.value}"
    if status == ProcessStatus.pas_interesse:
        return "negative", "statut:pas_interesse"
    if sentiments:
        return ("positive" if sentiments[-1] == "like" else "negative"), f"retour:{sentiments[-1]}"
    return "neutral", f"statut:{status.value}" if status else "aucun"


# --- Export (vraie base) ------------------------------------------------------------------------


def _dump(obj, skip: set[str]) -> dict:
    out = {}
    for attr in inspect(type(obj)).column_attrs:
        if attr.key in skip:
            continue
        value = getattr(obj, attr.key)
        if isinstance(value, Decimal):
            value = float(value)
        elif isinstance(value, datetime | date):
            value = value.isoformat()
        elif isinstance(value, enum.Enum):
            value = value.value
        out[attr.key] = value
    return out


def _restore(cls, data: dict):
    """Inverse de _dump : dates et décimaux reconvertis d'après le type des colonnes."""
    kwargs = {}
    for attr in inspect(cls).column_attrs:
        if attr.key not in data:
            continue
        value = data[attr.key]
        if value is not None:
            try:
                kind = attr.columns[0].type.python_type
            except NotImplementedError:  # JSON, vecteur…
                kind = None
            if kind is datetime:
                value = datetime.fromisoformat(value)
            elif kind is date:
                value = date.fromisoformat(value)
            elif kind is Decimal:
                value = Decimal(str(value))
        kwargs[attr.key] = value
    return cls(**kwargs)


def judged_users(db: Session) -> list[tuple[int, int]]:
    """[(user_id, nombre d'offres jugées)], les plus fournis d'abord."""
    pairs = set(db.execute(select(JobFeedback.user_id, JobFeedback.job_id)).all())
    pairs |= set(db.execute(select(Application.user_id, Application.job_id).where(
        Application.status.in_([*POSITIVE_STATUSES, ProcessStatus.pas_interesse]))).all())
    counts = Counter(user_id for user_id, _ in pairs)
    return counts.most_common()


def export_dataset(db: Session, user: User) -> dict:
    """Jeu de référence de l'utilisateur (dict sérialisable en JSON). Le nom de l'utilisateur n'est pas exporté."""
    feedback: dict[int, list[JobFeedback]] = {}
    for fb in db.scalars(select(JobFeedback).where(JobFeedback.user_id == user.id).order_by(JobFeedback.id)):
        feedback.setdefault(fb.job_id, []).append(fb)
    statuses = {a.job_id: a.status for a in db.scalars(select(Application).where(Application.user_id == user.id))}
    profile = profile_data(get_profile(db, user))
    items = []
    for job_id in sorted(set(feedback) | set(statuses)):
        fbs = feedback.get(job_id, [])
        label, origin = label_for(statuses.get(job_id), [f.sentiment for f in fbs])
        if label == "neutral" and not fbs:
            continue  # simple process « nouveau / à regarder » sans avis : rien à mesurer
        job = db.get(Job, job_id)
        if job is None:
            continue
        reasons = list(dict.fromkeys(r for f in fbs for r in (f.reasons or [])))
        comments = [f.text for f in fbs if f.text]
        items.append({
            "job_id": job.id, "label": label, "label_source": origin,
            "status": statuses[job_id].value if job_id in statuses else None,
            "reasons": reasons, "comment": " / ".join(comments) or None,
            "job": _dump(job, JOB_SKIP), "company": _dump(job.company, COMPANY_SKIP),
            "skills": [[s.skill_id, s.required] for s in db.scalars(
                select(JobSkill).where(JobSkill.job_id == job.id).order_by(JobSkill.skill_id))],
        })
    return {
        "version": DATASET_VERSION, "exported_at": datetime.now(UTC).isoformat(), "source_user_id": user.id,
        "profile": profile.model_dump(mode="json"), "items": items,
        "counts": dict(Counter(i["label"] for i in items)),
    }


# --- Rejeu (base temporaire) -------------------------------------------------------------------


def load_dataset(db: Session, dataset: dict) -> User:
    """Offres et entreprises du jeu (identifiants d'origine conservés : les exemples des règles et les offres
    de référence pointent toujours au bon endroit), et un utilisateur avec le profil exporté."""
    if dataset.get("version") not in READABLE_VERSIONS:
        raise ValueError(f"Version de jeu inattendue : {dataset.get('version')} (attendu {DATASET_VERSION})")
    taxonomy.sync(db)  # liste fermée (familles de postes, compétences) : référencée par les offres classées
    companies: dict[int, Company] = {}
    for it in dataset["items"]:
        c = it["company"]
        if c["id"] not in companies:
            companies[c["id"]] = _restore(Company, c)
            db.add(companies[c["id"]])
    db.flush()
    for it in dataset["items"]:
        job = _restore(Job, {**it["job"], "closed_at": None})  # tout est « ouvert » ici
        if job.role_family not in taxonomy.FAMILY_LABEL:
            job.role_family = None
        db.add(job)
        db.add_all(JobSkill(job_id=job.id, skill_id=sid, required=req)
                   for sid, req in it.get("skills", []) if sid in taxonomy.SKILL_LABEL)
    user = User(linkedin_sub="relevance-eval", name=None)
    db.add(user)
    db.commit()
    profile = get_profile(db, user)
    save_fields(profile, ProfileData.model_validate(dataset["profile"]))
    db.commit()
    return user


def fold_of(job_id: int, folds: int) -> int:
    """Pli stable d'une offre (même répartition d'un passage à l'autre)."""
    if folds <= 1:
        return 0
    return int(hashlib.sha256(str(job_id).encode()).hexdigest(), 16) % folds


def profile_without(data: ProfileData, held_out: set[int], held_out_companies: list[tuple[int | None, str]]) \
        -> ProfileData:
    """Profil privé de ce qui vient des jugements des offres tenues à l'écart."""
    names = {_norm(n) for _, n in held_out_companies}
    ids = {i for i, _ in held_out_companies if i is not None}
    rules = [r.model_copy(update={"examples": [j for j in r.examples if j not in held_out]}) for r in data.rules]
    return data.model_copy(update={
        "reference_jobs": [r for r in data.reference_jobs if r.job_id not in held_out],
        "rules": rules,
        "excluded_companies": [c for c in data.excluded_companies
                               if not (c.company_id in ids or _norm(c.name) in names)],
    })


def run_scoring(db: Session, user: User, dataset: dict, llm, *, folds: int = 2, models: list[str] | None = None,
                batch_limit: int | None = None) -> dict[int, dict]:
    """Note toutes les offres jugées (neutres comprises) avec le pipeline de production.
    Retourne {job_id: {"score", "verdict", "exclusion", "model", "fold"}}."""
    from .assessments import assess_user

    base = ProfileData.model_validate(dataset["profile"])
    items = {it["job_id"]: it for it in dataset["items"]}
    out: dict[int, dict] = {}
    for k in range(max(folds, 1)):
        fold_ids = [j for j in items if fold_of(j, folds) == k]
        if not fold_ids:
            continue
        held_out = set(fold_ids) if folds > 1 else set()
        held_companies = [(items[j]["company"]["id"], items[j]["company"]["name"]) for j in held_out]
        profile: UserProfile = get_profile(db, user)
        save_fields(profile, profile_without(base, held_out, held_companies))
        db.commit()
        db.execute(delete(JobAssessment).where(JobAssessment.user_id == user.id, JobAssessment.job_id.in_(fold_ids)))
        db.commit()
        assess_user(db, user, llm, limit=batch_limit or len(fold_ids), models=models, job_ids=fold_ids)
        for a in db.scalars(select(JobAssessment).where(JobAssessment.user_id == user.id,
                                                        JobAssessment.job_id.in_(fold_ids))):
            out[a.job_id] = {"score": float(a.score) if a.score is not None else None, "verdict": a.verdict,
                             "exclusion": a.exclusion, "model": a.model, "fold": k}
    return out


# --- Métriques ------------------------------------------------------------------------------


def _rank_key(result: dict | None) -> float:
    """Offre écartée par une exclusion (sans note) ou non notée : en bas du classement."""
    if not result or result.get("score") is None:
        return -1.0
    return result["score"]


def auc(pos: list[float], neg: list[float]) -> float | None:
    """Probabilité qu'une positive soit mieux classée qu'une négative (égalités : 1/2)."""
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def compute_metrics(dataset: dict, results: dict[int, dict], ks: tuple[int, ...] = (5, 10, 20)) -> dict:
    labelled = [it for it in dataset["items"] if it["label"] in ("positive", "negative")]
    ranked = sorted(labelled, key=lambda it: (-_rank_key(results.get(it["job_id"])), it["job_id"]))
    positives = [it for it in labelled if it["label"] == "positive"]
    negatives = [it for it in labelled if it["label"] == "negative"]
    base_rate = len(positives) / len(labelled) if labelled else 0.0

    def name(it: dict) -> str:
        return f"{it['company']['name']} – {it['job']['title']}"

    missed = [{"offre": name(it), "note": results.get(it["job_id"], {}).get("score"),
               "raison": results.get(it["job_id"], {}).get("exclusion") or "non notée"}
              for it in positives if results.get(it["job_id"], {}).get("verdict") != "nouveau"]
    kept_neg = [{"offre": name(it), "note": results[it["job_id"]]["score"], "raisons": it["reasons"],
                 "commentaire": it["comment"]}
                for it in negatives if results.get(it["job_id"], {}).get("verdict") == "nouveau"]
    # Raisons de « Pas intéressé » : part des offres correctement écartées, par raison
    by_reason: dict[str, list[bool]] = {}
    for it in negatives:
        for r in it["reasons"] or ["(sans raison)"]:
            by_reason.setdefault(r, []).append(results.get(it["job_id"], {}).get("verdict") == "ecartee")
    return {
        "jugees": len(labelled), "positives": len(positives), "negatives": len(negatives),
        "taux_de_base": round(base_rate, 3),
        "precision": {k: round(sum(it["label"] == "positive" for it in ranked[:k]) / k, 3)
                      for k in ks if k <= len(ranked)},
        "auc": auc([_rank_key(results.get(it["job_id"])) for it in positives],
                   [_rank_key(results.get(it["job_id"])) for it in negatives]),
        "positives_retenues": round(1 - len(missed) / len(positives), 3) if positives else None,
        "negatives_ecartees": round(1 - len(kept_neg) / len(negatives), 3) if negatives else None,
        "ecartees_par_raison": {r: f"{sum(v)}/{len(v)}" for r, v in sorted(by_reason.items())},
        "top": [{"offre": name(it), "note": _rank_key(results.get(it["job_id"])), "libelle": it["label"]}
                for it in ranked[:max(ks)]],
        "positives_manquees": missed,
        "negatives_retenues": kept_neg,
    }


def stability(runs: list[dict[int, dict]]) -> dict | None:
    """Écart type moyen et maximal de la note d'une même offre entre plusieurs passages, et part des offres
    dont le verdict change."""
    if len(runs) < 2:
        return None
    common = set.intersection(*(set(r) for r in runs))
    spreads, flips = [], 0
    for job_id in common:
        scores = [r[job_id]["score"] for r in runs if r[job_id]["score"] is not None]
        if len(scores) == len(runs):
            spreads.append(pstdev(scores))
        flips += len({r[job_id]["verdict"] for r in runs}) > 1
    return {"ecart_type_moyen": round(mean(spreads), 2) if spreads else None,
            "ecart_type_max": round(max(spreads), 2) if spreads else None,
            "verdicts_changeants": round(flips / len(common), 3) if common else None}


def summarize_runs(dataset: dict, runs: list[dict[int, dict]], ks: tuple[int, ...] = (5, 10, 20)) -> dict:
    """Métriques de chaque passage, leur moyenne, et la stabilité."""
    per_run = [compute_metrics(dataset, r, ks) for r in runs]
    first = per_run[0]
    avg: dict[str, Any] = {
        "precision": {k: round(mean(m["precision"][k] for m in per_run), 3) for k in first["precision"]},
        "auc": round(mean(m["auc"] for m in per_run), 3) if first["auc"] is not None else None,
    }
    for key in ("positives_retenues", "negatives_ecartees"):
        avg[key] = round(mean(m[key] for m in per_run), 3) if first[key] is not None else None
    return {"moyenne": avg, "stabilite": stability(runs), "passages": per_run}


def format_report(summary: dict) -> str:
    m = summary["passages"][0]
    avg = summary["moyenne"]
    lines = [f"Offres jugées : {m['jugees']} ({m['positives']} positives, {m['negatives']} négatives ; "
             f"une note au hasard donnerait {m['taux_de_base']:.0%} de positives)"]
    lines.append("Précision : " + " · ".join(f"@{k} {v:.0%}" for k, v in avg["precision"].items())
                 + (f"   AUC {avg['auc']:.2f}" if avg["auc"] is not None else ""))
    if avg["positives_retenues"] is not None:
        lines.append(f"Positives retenues (verdict « nouveau ») : {avg['positives_retenues']:.0%}")
    if avg["negatives_ecartees"] is not None:
        lines.append(f"Négatives écartées : {avg['negatives_ecartees']:.0%}")
    if s := summary["stabilite"]:
        lines.append(f"Stabilité ({len(summary['passages'])} passages) : écart type moyen {s['ecart_type_moyen']}, "
                     f"max {s['ecart_type_max']} ; verdicts qui changent : {s['verdicts_changeants']:.0%}")
    if m["ecartees_par_raison"]:
        lines.append("« Pas intéressé » bien écartées, par raison : "
                     + ", ".join(f"{r} {v}" for r, v in m["ecartees_par_raison"].items()))
    lines.append("\nPremières offres (passage 1) :")
    lines += [f"  {t['note']:>5} {'+' if t['libelle'] == 'positive' else '-'} {t['offre']}"[:120] for t in m["top"]]
    if m["positives_manquees"]:
        lines.append("\nPositives écartées (passage 1) :")
        lines += [f"  {p['note']} {p['offre']} : {p['raison']}"[:160] for p in m["positives_manquees"]]
    if m["negatives_retenues"]:
        lines.append("\nNégatives retenues (passage 1) :")
        lines += [f"  {n['note']} {n['offre']} ({', '.join(n['raisons']) or n['commentaire'] or 'sans raison'})"[:160]
                  for n in m["negatives_retenues"]]
    return "\n".join(lines)


def compare(a: dict, b: dict) -> str:
    """Deux résultats enregistrés (fichiers de scripts/eval_relevance.py) : différences des métriques moyennes."""
    ma, mb = a["resume"]["moyenne"], b["resume"]["moyenne"]
    lines = [f"{'':<22}{a['nom']:>14}{b['nom']:>14}"]
    for k in ma["precision"]:
        lines.append(f"{'precision@' + str(k):<22}{ma['precision'][k]:>14.0%}{mb['precision'].get(k, 0):>14.0%}")
    for key in ("auc", "positives_retenues", "negatives_ecartees"):
        if ma.get(key) is not None and mb.get(key) is not None:
            lines.append(f"{key:<22}{ma[key]:>14.2f}{mb[key]:>14.2f}")
    return "\n".join(lines)


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1, default=str)
