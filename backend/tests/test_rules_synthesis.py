"""Synthèse des règles à partir des retours (Gemini simulé) : exemples cités, validation une par une."""

import json
import re

from sqlalchemy import select

from app.models import Company, Job, JobFeedback


def make_jobs(db) -> dict[str, Job]:
    specs = {
        "ooak": ("Ooak Data", "Fullstack Engineer", "TypeScript, React, PostgreSQL", "55-80 k€"),
        "bluesand": ("Bluesand", "Data Engineer", "SQL, dbt, BigQuery", None),
        "kuli": ("Kuli", "AI Engineer", "LangGraph, evals, agents", None),
        "remanence": ("Remanence", "Member of Technical Staff, Platform", "Kubernetes, Terraform, Helm, Argo CD", None),
    }
    jobs = {}
    for key, (company, title, stack, salary) in specs.items():
        job = Job(company=Company(name=company, slug=key), title=title, stack=stack,
                  summary=f"Salaire {salary}" if salary else None)
        db.add(job)
        jobs[key] = job
    db.commit()
    return jobs


def give_feedbacks(client, jobs):
    client.post(f"/api/jobs/{jobs['ooak'].id}/feedback",
                json={"sentiment": "dislike", "reasons": ["trop_front", "salaire_bas"],
                      "text": "trop front TypeScript et base de données + pas assez payé"})
    client.post(f"/api/jobs/{jobs['bluesand'].id}/feedback",
                json={"sentiment": "dislike", "reasons": ["trop_sql"], "text": "SQL à fond"})
    client.post(f"/api/jobs/{jobs['kuli'].id}/feedback", json={"sentiment": "dislike", "reasons": ["trop_ia"]})
    client.post(f"/api/jobs/{jobs['remanence'].id}/feedback",
                json={"sentiment": "like", "text": "l'offre est top : stack GitOps exacte"})


def reference_synthesis(contents: str) -> dict:
    """Ce qu'un bon modèle répondrait (règles proches de docs/exemple-profil.txt), en citant les job_id reçus."""
    payload = json.loads(re.search(r"<retours>\n(.*)\n</retours>", contents, re.S)[1])
    ids = {fb["offre"].split(" – ")[0]: fb["job_id"] for fb in payload}
    return {"rules": [
        {"action": "ajouter", "kind": "eviter", "confidence": "haute",
         "text": "Baisser la note des rôles où le front TypeScript et la base de données sont le cœur du poste "
                 "(ex. Ooak Data). Acceptable si secondaire.",
         "examples": [ids["Ooak Data"]], "reason": "Ooak Data : « trop front TypeScript et base de données »"},
        {"action": "ajouter", "kind": "eviter", "confidence": "haute",
         "text": "Écarter les rôles où le SQL / la data engineering poussée est le cœur du poste (ex. Bluesand)",
         "examples": [ids["Bluesand"], 99999], "reason": "Bluesand : « SQL à fond »"},
        {"action": "modifier", "kind": "eviter", "confidence": "moyenne",
         "replaces": "IA trop pointue",
         "text": "L'IA doit rester le produit, pas le métier principal : écarter agents / LangGraph / evals au cœur "
                 "du poste (ex. Kuli)",
         "examples": [ids["Kuli"]], "reason": "Kuli jugé « trop IA »"},
        {"action": "ajouter", "kind": "eviter", "confidence": "moyenne",
         "text": "Salaire de 55-80 k€ jugé trop bas (ex. Ooak Data)", "examples": [ids["Ooak Data"]],
         "reason": "« pas assez payé »"},
        {"action": "ajouter", "kind": "preferer", "confidence": "haute",
         "text": "Chercher en priorité des postes plateforme / infra Kubernetes + GitOps dans une startup IA récente "
                 "(ex. Remanence)",
         "examples": [ids["Remanence"]], "reason": "Remanence : « l'offre est top »"},
        {"action": "ajouter", "kind": "eviter", "confidence": "haute",
         "text": "Règle inventée sans offre réelle", "examples": [123456], "reason": "aucune"},
    ]}


def test_reference_scenario_proposes_rules_citing_offers(db, alice, as_user, fake_llm):
    """Scénario de référence : retours Ooak Data, Bluesand, Kuli, Remanence -> règles du profil d'exemple."""
    client = as_user(alice)
    client.patch("/api/profile", json={"skills": [{"name": "Python"}, {"name": "Kubernetes"}],
                                       "rules": [{"kind": "eviter", "text": "IA trop pointue"}]})
    jobs = make_jobs(db)
    give_feedbacks(client, jobs)
    assert client.get("/api/feedback").json()["new"] == 4
    fake_llm.synthesis = reference_synthesis

    before = client.get("/api/profile").json()
    r = client.post("/api/profile/rules/synthesize")
    assert r.status_code == 200, r.text
    preview = r.json()
    assert preview["kind"] == "regles"

    # Le modèle a reçu les retours avec leurs raisons et commentaires
    assert "Trop front / TypeScript" in fake_llm.last_synthesis_input
    assert "SQL à fond" in fake_llm.last_synthesis_input

    items = preview["items"]
    texts = [i["value"]["text"] for i in items if i["value"]]
    assert len(items) == 5  # la règle inventée (aucune offre réelle) est écartée
    assert not any("inventée" in t for t in texts)
    # Chaque règle cite les offres qui l'ont fait naître (et seulement de vraies offres)
    for it in items:
        assert it["inferred"] and it["examples"]
        assert all(j in {job.id for job in jobs.values()} for j in it["value"]["examples"])
    by_text = {i["value"]["text"]: i for i in items}
    sql = next(i for t, i in by_text.items() if "SQL" in t)
    assert sql["examples"] == ["Bluesand – Data Engineer"] and sql["value"]["examples"] == [jobs["bluesand"].id]
    ia = next(i for t, i in by_text.items() if "Kuli" in t)
    assert ia["op"] == "update" and ia["match"] == "IA trop pointue"
    assert any(i["value"]["kind"] == "preferer" and "Remanence" in i["display"] for i in items)

    # Rien n'est appliqué sans validation ; les retours sont marqués synthétisés
    assert client.get("/api/profile").json()["rules"] == before["rules"]
    assert client.get("/api/feedback").json()["new"] == 0

    # Validation une par une : on n'accepte que la règle SQL et la modification IA
    profile = client.post(f"/api/profile/proposals/{preview['import_id']}/apply",
                          json={"accept": [sql["id"], ia["id"]]}).json()
    rules = {r["text"]: r for r in profile["rules"]}
    assert len(rules) == 2
    new_sql = next(r for t, r in rules.items() if "SQL" in t)
    assert new_sql["source"] == "feedback" and new_sql["examples"] == [jobs["bluesand"].id]
    assert any("Kuli" in t for t in rules)  # règle existante enrichie, pas de doublon
    assert profile["version"] > before["version"]  # -> réévaluation des offres non traitées


def test_synthesis_requires_feedback(alice, as_user, fake_llm):
    r = as_user(alice).post("/api/profile/rules/synthesize")
    assert r.status_code == 422


def test_unknown_target_becomes_an_addition_and_duplicates_are_dropped(db, alice, as_user, fake_llm):
    client = as_user(alice)
    client.patch("/api/profile", json={"rules": [{"kind": "eviter", "text": "Rôles centrés SQL"}]})
    jobs = make_jobs(db)
    give_feedbacks(client, jobs)
    bluesand = jobs["bluesand"].id
    fake_llm.synthesis = {"rules": [
        {"action": "ajouter", "kind": "eviter", "text": "rôles centrés sql", "examples": [bluesand],
         "reason": "doublon", "confidence": "haute"},
        {"action": "modifier", "kind": "eviter", "replaces": "règle qui n'existe pas", "text": "Nouvelle règle data",
         "examples": [bluesand], "reason": "x", "confidence": "haute"},
        {"action": "desactiver", "kind": "eviter", "replaces": "inexistante", "text": "x", "examples": [bluesand],
         "reason": "x", "confidence": "haute"},
    ]}
    items = client.post("/api/profile/rules/synthesize").json()["items"]
    assert [(i["op"], i["value"]["text"]) for i in items] == [("add", "Nouvelle règle data")]


def test_synthesis_only_reads_own_feedback(db, alice, bob, as_user, fake_llm):
    jobs = make_jobs(db)
    give_feedbacks(as_user(alice), jobs)
    assert as_user(bob).post("/api/profile/rules/synthesize").status_code == 422
    assert db.scalar(select(JobFeedback).where(JobFeedback.user_id == bob.id)) is None
