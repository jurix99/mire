"""Mesure de pertinence : libellés, export figé, rejeu avec validation croisée, métriques (Gemini simulé)."""

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app import taxonomy
from app.db import Base, engine
from app.models import Application, Company, Job, JobFeedback, JobSkill, ProcessStatus
from app.profile import ProfileData, get_profile, save_fields
from app.relevance_eval import (
    auc,
    compute_metrics,
    export_dataset,
    fold_of,
    judged_users,
    label_for,
    load_dataset,
    run_scoring,
    stability,
    summarize_runs,
)


def test_labels():
    assert label_for(ProcessStatus.entretien, [])[0] == "positive"
    assert label_for(ProcessStatus.refuse, ["dislike"])[0] == "positive"  # a postulé malgré tout
    assert label_for(ProcessStatus.pas_interesse, ["like"])[0] == "negative"  # changé d'avis
    assert label_for(None, ["dislike", "like"])[0] == "positive"  # dernier retour
    assert label_for(ProcessStatus.nouveau, ["like"])[0] == "positive"
    assert label_for(ProcessStatus.a_postuler, [])[0] == "neutral"
    assert label_for(ProcessStatus.abandonne, [])[0] == "neutral"


def test_auc():
    assert auc([9, 8], [1, 2]) == 1.0
    assert auc([1], [9]) == 0.0
    assert auc([5], [5]) == 0.5
    assert auc([], [1]) is None


def _judged(db, alice):
    """Six offres jugées par Alice, une non jugée, et une offre jugée par Bob (hors export)."""
    acme = Company(name="Acme", slug="acme", funding_stage="Seed", funding_amount_meur=Decimal("4.5"),
                   funding_announced_on=date(2026, 3, 1))
    globex = Company(name="Globex", slug="globex")
    jobs = {t: Job(company=acme if i % 2 else globex, title=t, summary=f"Description {t} " * 200,
                   published_on=date(2026, 9, 1))
            for i, t in enumerate(["Platform A", "Platform B", "Platform C", "Front A", "Front B", "Data A",
                                   "Inconnue"])}
    db.add_all(jobs.values())
    db.commit()
    for t in ("Platform A", "Platform B"):
        db.add(JobFeedback(user_id=alice.id, job_id=jobs[t].id, sentiment="like"))
    db.add(Application(user_id=alice.id, job_id=jobs["Platform C"].id, status=ProcessStatus.entretien))
    for t, reasons in (("Front A", ["trop_front"]), ("Front B", ["trop_front"]), ("Data A", ["trop_sql"])):
        db.add(JobFeedback(user_id=alice.id, job_id=jobs[t].id, sentiment="dislike", reasons=reasons))
        db.add(Application(user_id=alice.id, job_id=jobs[t].id, status=ProcessStatus.pas_interesse))
    profile = get_profile(db, alice)
    save_fields(profile, ProfileData.model_validate({
        "headline": "Platform engineer", "years_experience": 5, "skills": [{"name": "Kubernetes"}],
        "reference_jobs": [{"job_id": jobs[t].id, "title": t, "company": jobs[t].company.name}
                           for t in ("Platform A", "Platform B")],
        "rules": [{"kind": "eviter", "text": "Front TypeScript", "source": "feedback",
                   "examples": [jobs["Front A"].id, jobs["Front B"].id]}],
        "excluded_companies": [{"name": "Globex", "reason": "déjà passé en entretien"}],
    }))
    db.commit()
    return jobs


def test_export_freezes_judged_jobs(db, alice, bob):
    jobs = _judged(db, alice)
    db.add(JobFeedback(user_id=bob.id, job_id=jobs["Inconnue"].id, sentiment="like"))
    taxonomy.sync(db)
    jobs["Platform A"].role_family = "platform_devops_sre"
    db.add_all([JobSkill(job_id=jobs["Platform A"].id, skill_id="kubernetes", required=True),
                JobSkill(job_id=jobs["Platform A"].id, skill_id="go", required=False)])
    db.commit()
    assert judged_users(db)[0] == (alice.id, 6)

    dataset = export_dataset(db, alice)
    assert dataset["counts"] == {"positive": 3, "negative": 3}
    assert "Alice" not in str(dataset)  # ni nom ni e-mail
    by_title = {it["job"]["title"]: it for it in dataset["items"]}
    assert by_title["Platform C"]["label_source"] == "statut:entretien"
    assert by_title["Front A"]["reasons"] == ["trop_front"]
    assert len(by_title["Platform A"]["job"]["summary"]) > 3000  # texte complet, non tronqué

    # Rejeu dans une base vide : identifiants, dates et décimaux restaurés
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db.expunge_all()
    user = load_dataset(db, dataset)
    restored = db.get(Job, jobs["Platform A"].id)
    assert restored.title == "Platform A" and restored.published_on == date(2026, 9, 1)
    assert db.scalar(select(Company).where(Company.name == "Acme")).funding_amount_meur == Decimal("4.5")
    assert len(get_profile(db, user).reference_jobs) == 2
    # Classement de l'offre (compétences exigées / appréciées, famille) conservé pour la notation v2
    assert restored.role_family == "platform_devops_sre"
    assert {(s.skill_id, s.required) for s in db.query(JobSkill).filter_by(job_id=restored.id)} == {
        ("kubernetes", True), ("go", False)}


def test_scoring_hides_held_out_judgements(db, alice, fake_llm):
    jobs = _judged(db, alice)
    dataset = export_dataset(db, alice)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db.expunge_all()
    user = load_dataset(db, dataset)

    fake_llm.grade = lambda j: {"competences": 0.9 if "Platform" in j["titre"] else 0.1}
    results = run_scoring(db, user, dataset, fake_llm, folds=2)
    assert set(results) == {it["job_id"] for it in dataset["items"]}

    # Pour chaque pli, le profil envoyé ne cite ni les offres de référence ni les exemples de ce pli
    for call in fake_llm.eval_calls:
        for job in call["jobs"]:
            label = f"{job['entreprise']['nom']} – {job['titre']}"
            assert f"Offre de référence (aimée) : {label}" not in call["context"]
            assert f"(ex. {label}" not in call["context"] and f", {label}" not in call["context"]
    # L'exclusion « Globex » vient des jugements : retirée quand une offre Globex est notée
    assert all(r["exclusion"] is None or "Globex" not in r["exclusion"] for r in results.values())
    # Les folds sont stables et l'offre exclue n'est jamais transmise avec son pli
    assert {fold_of(j.id, 2) for j in jobs.values()} <= {0, 1}

    metrics = compute_metrics(dataset, results)
    assert metrics["precision"] == {5: 0.6}  # 3 positives en tête, puis des négatives ; @10 : trop peu d'offres
    assert metrics["auc"] == 1.0
    assert metrics["positives_retenues"] == 1.0
    assert metrics["negatives_ecartees"] == 1.0
    assert metrics["ecartees_par_raison"] == {"trop_front": "2/2", "trop_sql": "1/1"}

    summary = summarize_runs(dataset, [results, results])
    assert summary["stabilite"]["ecart_type_moyen"] == 0
    assert summary["moyenne"]["auc"] == 1.0


def test_folds_1_keeps_profile_as_is(db, alice, fake_llm):
    _judged(db, alice)
    dataset = export_dataset(db, alice)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db.expunge_all()
    user = load_dataset(db, dataset)
    results = run_scoring(db, user, dataset, fake_llm, folds=1)
    # Contrôle : sans validation croisée, les offres aimées sont citées comme références
    assert any("Offre de référence (aimée) : Acme – Platform B" in c["context"]
               or "Offre de référence (aimée) : Globex – Platform A" in c["context"] for c in fake_llm.eval_calls)
    # Profil tel quel : l'entreprise exclue l'est toujours (exclusion dure, sans Gemini)
    globex = [r for r in results.values() if r["model"] == "regles"]
    assert globex and all("Globex" in r["exclusion"] for r in globex)


def test_stability_counts_verdict_flips():
    a = {1: {"score": 7.0, "verdict": "nouveau"}, 2: {"score": 4.0, "verdict": "ecartee"}}
    b = {1: {"score": 5.0, "verdict": "ecartee"}, 2: {"score": 4.0, "verdict": "ecartee"}}
    s = stability([a, b])
    assert s["ecart_type_max"] == 1.0
    assert s["verdicts_changeants"] == 0.5
