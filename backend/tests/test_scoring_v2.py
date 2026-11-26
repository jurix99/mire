"""Variante v2 de la notation : données classées de l'offre, couverture des compétences, description début + fin,
analyse avant les notes (Gemini simulé)."""

import pytest

from app import taxonomy
from app.assessments import (
    EvaluationBatchV2,
    assess_user,
    excerpt,
    profile_skill_ids,
    scoring_variant,
    skill_coverage,
)
from app.llm import inline_json_schema
from app.models import Company, Job, JobSkill
from app.profile import ProfileData, get_profile, save_fields

PROFILE = {
    "years_experience": 5,
    "skills": [{"name": "Kubernetes"}, {"name": "Terraform"}, {"name": "Python"},
               {"name": "React", "kind": "a_developper"}, {"name": "K8s", "kind": "a_developper"}],
    "preferences": {"company_stages": ["Seed", "Série A"]},
}


def test_variant_from_environment(monkeypatch):
    monkeypatch.delenv("SCORING_VARIANT", raising=False)
    assert scoring_variant() == "v1"  # production inchangée tant qu'on n'a pas mesuré
    monkeypatch.setenv("SCORING_VARIANT", "v2")
    assert scoring_variant() == "v2"
    monkeypatch.setenv("SCORING_VARIANT", "v3")
    with pytest.raises(ValueError):
        scoring_variant()


def test_excerpt_keeps_head_and_tail():
    text = "Entreprise. " * 200 + "Exigences : 5 ans de Kubernetes."
    out = excerpt(text, limit=1000, head=300)
    assert out.startswith("Entreprise.") and out.endswith("5 ans de Kubernetes.")
    assert "[…]" in out and len(out) <= 1010
    assert excerpt("court") == "court" and excerpt(None) is None


def test_profile_skills_on_closed_list():
    mine = profile_skill_ids(ProfileData.model_validate(PROFILE))
    assert mine["kubernetes"] == "fort"  # « K8s » à développer ne rétrograde pas « Kubernetes » fort
    assert mine["terraform"] == "fort" and mine["python"] == "fort"
    react = next(sid for sid, label in taxonomy.SKILL_LABEL.items() if "React" in label)
    assert mine[react] == "a_developper"


def test_skill_coverage():
    mine = {"kubernetes": "fort", "python": "a_developper"}
    cov = skill_coverage([("kubernetes", True), ("python", True), ("go", True), ("terraform", False),
                          ("anglais", True)], mine)
    assert cov["exigees_maitrisees"] == ["Kubernetes"]
    assert cov["exigees_a_developper"] == ["Python"]
    assert cov["exigees_absentes"] == ["Go"]  # langue exigée : jugée à part, hors couverture
    assert cov["part_maitrisee"] == 0.33
    assert skill_coverage([("terraform", False)], mine) is None  # aucune exigence
    assert skill_coverage([("go", True)], {}) is None  # profil sans compétence reconnue


def test_analysis_comes_before_scores_in_schema():
    schema = inline_json_schema(EvaluationBatchV2.model_json_schema())
    props = list(schema["properties"]["evaluations"]["items"]["properties"])
    assert props[:3] == ["job_id", "analyse", "competences"]
    batch = EvaluationBatchV2.model_validate({"evaluations": [
        {"job_id": 1, "analyse": "x" * 2000, "competences": 1, "stade": 1, "ownership": 1, "ia_concrete": 1,
         "xp": 1, "references": 1}, {"job_id": "invalide"}]})
    assert len(batch.evaluations) == 1 and len(batch.evaluations[0].analyse) == 800


def test_v2_payload_sent_to_gemini(db, alice, fake_llm, monkeypatch):
    monkeypatch.setenv("SCORING_VARIANT", "v2")
    taxonomy.sync(db)
    company = Company(name="Remanence", slug="remanence", funding_stage="Seed")
    job = Job(company=company, title="Platform Engineer", role_family="platform_devops_sre",
              seniority_level="confirme", contract_type="cdi", salary_min_keur=60, salary_max_keur=75,
              salary_kind="fixe", work_mode="hybride",
              summary="Nous sommes une startup. " * 300 + "Must have: Kubernetes, Go.")
    db.add(job)
    db.flush()
    db.add_all([JobSkill(job_id=job.id, skill_id="kubernetes", required=True),
                JobSkill(job_id=job.id, skill_id="go", required=True),
                JobSkill(job_id=job.id, skill_id="terraform", required=False)])
    save_fields(get_profile(db, alice), ProfileData.model_validate(PROFILE))
    db.commit()

    assess_user(db, alice, fake_llm)
    [call] = fake_llm.eval_calls
    assert call["variant"] == "v2"
    [payload] = call["jobs"]
    assert payload["competences_exigees"] == ["Go", "Kubernetes"]
    assert payload["competences_appreciees"] == ["Terraform / IaC"]
    assert payload["couverture_competences"]["exigees_absentes"] == ["Go"]
    assert payload["salaire_keur"] == "60-75" and payload["seniorite"] == "confirme"
    assert payload["famille"] == taxonomy.FAMILY_LABEL["platform_devops_sre"]
    assert payload["description"].endswith("Must have: Kubernetes, Go.")  # la fin de l'offre n'est plus perdue
    assert "Alice" not in call["context"]


# --- Critères personnels (v2) -------------------------------------------------------------------------


def test_personal_criteria_definitions(monkeypatch):
    from app.profile import criteria_defs, criteria_labels

    data = ProfileData.model_validate({
        "preferences": {"company_stages": ["Scale-up"], "sectors": []},
        "scoring": {"definitions": {"ownership": "  Équipe structurée,   périmètre clair "}}})
    monkeypatch.delenv("SCORING_VARIANT", raising=False)
    assert criteria_labels()["ia_concrete"] == "Produit IA concret avec impact réel"  # v1 inchangée
    assert not any(c.personal for c in criteria_defs(data))
    defs = {c.key: c for c in criteria_defs(data, "v2")}
    assert defs["stade"].definition == "Stades visés : Scale-up" and defs["stade"].source == "preferences"
    assert defs["ownership"].definition == "Équipe structurée, périmètre clair" and defs["ownership"].source == "utilisateur"
    assert defs["ia_concrete"].definition is None and defs["ia_concrete"].source == "aucune"  # neutre, pas l'IA imposée
    assert defs["ia_concrete"].label == "Produit et secteur" and not defs["competences"].personal


def test_definitions_validation():
    with pytest.raises(ValueError):
        ProfileData.model_validate({"scoring": {"definitions": {"competences": "x"}}})  # critère non personnel
    with pytest.raises(ValueError):
        ProfileData.model_validate({"scoring": {"definitions": {"stade": "x" * 301}}})
    assert ProfileData.model_validate({"scoring": {"definitions": {"stade": "   "}}}).scoring.definitions == {}


def test_v2_sends_personal_criteria_and_labels(db, alice, fake_llm, monkeypatch, as_user, catalog):
    monkeypatch.setenv("SCORING_VARIANT", "v2")
    client = as_user(alice)
    client.patch("/api/profile", json={**PROFILE, "scoring": {"definitions": {"ia_concrete": "Outils pour développeurs"}}})
    profile = client.get("/api/profile").json()
    crit = {c["key"]: c for c in profile["criteria"]}
    assert crit["ia_concrete"]["definition"] == "Outils pour développeurs" and crit["ia_concrete"]["personal"]
    assert crit["stade"]["source"] == "preferences"

    assess_user(db, alice, fake_llm)
    context = fake_llm.eval_calls[0]["context"]
    assert "Barème personnel" in context
    assert "ia_concrete (Produit et secteur, poids 1) : Outils pour développeurs" in context
    assert "ownership (Type de rôle, poids 2) : non défini" in context
    # Fiche d'une offre : libellés de la variante en cours
    job = client.get(f"/api/jobs/{catalog['jobs'][0].id}").json()
    labels = {c["key"]: c["label"] for c in job["assessment"]["criteria"]}
    assert labels["ia_concrete"] == "Produit et secteur"
