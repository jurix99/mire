"""Analyse du profil : déductions proposées (Gemini simulé), jamais appliquées sans confirmation."""

from datetime import date

from app.analysis import ProfileAnalysis, compute_years, missing_topics
from app.profile import Experience, ProfileData

FACTS = {
    "headline": "Platform Engineer",
    "experiences": [
        {"company": "Acme", "title": "Platform Engineer", "start": "2023-01",
         "description": "Mise en production de modèles IA sur Kubernetes, ArgoCD, Helm, Terraform"},
        {"company": "Globex", "title": "DevOps", "start": "2020-09", "end": "2022-12",
         "description": "CI/CD, Docker, Azure"},
        {"company": "Initech", "title": "Stage dev web", "start": "2020-03", "end": "2020-08",
         "description": "Front React / TypeScript"},
    ],
    "skills": [{"name": "Python"}, {"name": "React"}],
    "preferences": {"roles": ["Platform engineer"]},
}


def analysis() -> ProfileAnalysis:
    return ProfileAnalysis.model_validate({
        "positioning": {"text": "Industrialiser des prototypes IA en production", "confidence": "haute",
                        "reason": "Déduit de 2 postes où tu as mis en production des modèles sur Kubernetes"},
        "roles": [
            {"role": "platform engineer", "reason": "déjà dans le profil", "confidence": "haute"},  # doublon
            {"role": "MLOps engineer", "reason": "Kubernetes + mise en prod de modèles", "confidence": "haute"},
            {"role": "Founding engineer", "reason": "postes transverses", "confidence": "basse"},
        ],
        "skills": [
            {"name": "Kubernetes", "kind": "fort", "reason": "3 ans, poste actuel", "confidence": "haute"},
            {"name": "React", "kind": "a_developper", "reason": "6 mois de stage en 2020", "confidence": "moyenne"},
            {"name": "Python", "kind": "fort", "reason": "inchangé", "confidence": "haute"},  # pas de changement
        ],
        "questions": ["Quel salaire minimum vises-tu ?", "Quels clouds préfères-tu ?"],
    })


def test_compute_years_merges_overlaps():
    exps = [Experience(start="2020-01", end="2020-12"), Experience(start="2020-06", end="2021-06"),
            Experience(start="2023-01")]
    assert compute_years(exps, today=date(2024, 12, 15)) == 3.5  # 18 mois + 24 mois
    assert compute_years([Experience(start="2019", end="2019")]) == 1.0
    assert compute_years([Experience(title="sans dates")]) is None
    # Stages et alternances non comptés
    exps = [Experience(title="Stage développeur", start="2019-01", end="2019-06"),
            Experience(title="Alternant DevOps", start="2019-09", end="2020-08"),
            Experience(title="DevOps", start="2020-09", end="2022-08")]
    assert compute_years(exps) == 2.0


def test_analysis_is_a_preview_only(alice, as_user, fake_llm):
    client = as_user(alice)
    client.patch("/api/profile", json=FACTS)
    before = client.get("/api/profile").json()
    fake_llm.analysis = analysis()

    r = client.post("/api/profile/analyze")
    assert r.status_code == 200, r.text
    preview = r.json()
    assert preview["kind"] == "analyse"
    assert all(i["inferred"] and i["reason"] for i in preview["items"])
    assert preview["questions"] == ["Quel salaire minimum vises-tu ?", "Quels clouds préfères-tu ?"]
    # Le modèle reçoit le profil structuré, sans barème
    assert '"experiences"' in fake_llm.last_analysis_input and "scoring" not in fake_llm.last_analysis_input

    # Rien n'est modifié avant confirmation
    after = client.get("/api/profile").json()
    assert after == before

    by = {(i["field"], i["display"]): i for i in preview["items"]}
    roles = [i["display"] for i in preview["items"] if i["field"] == "preferences.roles"]
    assert roles == ["MLOps engineer", "Founding engineer"]  # le rôle déjà présent n'est pas reproposé
    assert by[("preferences.roles", "Founding engineer")]["checked"] is False  # confiance basse
    assert ("skills", "React → à développer") in by
    assert by[("skills", "React → à développer")]["checked"] is False  # reclasser une saisie : à cocher soi-même
    assert not any(i["field"] == "skills" and "Python" in i["display"] for i in preview["items"])
    years = next(i for i in preview["items"] if i["field"] == "years_experience")
    assert years["value"] > 5 and years["confidence"] == "haute"


def test_accept_suggestions_one_by_one(alice, as_user, fake_llm):
    client = as_user(alice)
    client.patch("/api/profile", json=FACTS)
    fake_llm.analysis = analysis()
    preview = client.post("/api/profile/analyze").json()
    wanted = [i["id"] for i in preview["items"]
              if i["field"] == "positioning" or i["display"] == "React → à développer"]
    profile = client.post(f"/api/profile/proposals/{preview['import_id']}/apply", json={"accept": wanted}).json()

    assert profile["positioning"] == "Industrialiser des prototypes IA en production"
    assert {s["name"]: s["kind"] for s in profile["skills"]} == {"Python": "fort", "React": "a_developper"}
    assert profile["preferences"]["roles"] == ["Platform engineer"]  # suggestions de rôles refusées
    assert profile["years_experience"] is None


def test_analysis_needs_a_profile_and_gemini(alice, as_user, fake_llm):
    assert as_user(alice).post("/api/profile/analyze").status_code == 422


def test_analysis_without_gemini(alice, as_user):
    as_user(alice).patch("/api/profile", json=FACTS)
    assert as_user(alice).post("/api/profile/analyze").status_code == 503


def test_missing_topics_skip_filled_fields():
    full = ProfileData.model_validate({
        "preferences": {"locations": ["Paris"], "work_modes": ["hybride"], "company_stages": ["Seed"],
                        "min_salary_keur": 70, "clouds_preferred": ["Azure"], "travel": "occasionnel",
                        "sectors": ["Santé"], "roles": ["Platform"]},
        "rules": [{"kind": "eviter", "text": "SQL au cœur"}],
        "reference_jobs": [{"company": "Remanence", "title": "MTS"}],
    })
    assert missing_topics(full) == []
    assert "salaire minimum" in missing_topics(ProfileData())
