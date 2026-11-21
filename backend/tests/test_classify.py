"""Classement des offres sur la liste fermée (classify.py) : règles, synonymes, validation de la sortie Gemini."""

import pytest
from sqlalchemy import select

from app import classify, market
from app import taxonomy as tx
from app.llm import LLMUnavailable
from app.models import Company, Job, JobSkill, TaxonomyMiss


@pytest.fixture
def ref(db):
    tx.sync(db)
    db.commit()


def add(db, title, summary="", category=None, salary=None, company=None):
    company = company or db.scalar(select(Company).where(Company.slug == "acme")) or Company(
        name="Acme", slug="acme", sector="SaaS B2B")
    job = Job(company=company, title=title, summary=summary, category=category, salary=salary)
    db.add(job)
    db.commit()
    return job


def skills_of(db, job):
    return {s.skill_id: s.required for s in db.scalars(select(JobSkill).where(JobSkill.job_id == job.id))}


class FakeClassifier:
    """Faux Gemini : réponses scriptées par job_id ; compte les appels."""

    def __init__(self, answers=None, fail=None):
        self.answers, self.fail, self.calls = answers or {}, fail, 0

    def classify_jobs(self, instruction, jobs, schema):
        self.calls += 1
        if self.fail:
            raise self.fail
        assert schema["properties"]["jobs"]["items"]["properties"]["role_family"]["enum"] == tx.role_family_ids()
        return {"jobs": [{"job_id": j["job_id"], **self.answers.get(j["titre"], {})} for j in jobs]}


@pytest.mark.parametrize("title,family", [
    ("Account Executive Mid-Market", "sales"),
    ("SRE", "platform_devops_sre"),
    ("Senior Site Reliability Engineer", "platform_devops_sre"),
    ("Growth Marketer", "growth_acquisition"),
    ("Data Product Manager", "product_manager"),
    ("Founder's Associate", "founders_associate_chief_of_staff"),
    ("Customer Success Manager", "customer_success_account_management"),
])
def test_title_rules(db, ref, title, family):
    job = add(db, title)
    classify.classify_pending_jobs(db)
    assert job.role_family == family


def test_ats_department_when_title_is_vague(db, ref):
    job = add(db, "Ninja (H/F)", category="Sales")
    classify.classify_pending_jobs(db)
    assert job.role_family == "sales"


def test_synonyms_group_under_one_skill(db, ref):
    job = add(db, "Platform Engineer", "Nous utilisons k8s sur GCP. Expérience LLM Ops appréciée. "
                                       "Account based marketing en lien avec le marketing.")
    classify.classify_pending_jobs(db)
    assert {"kubernetes", "gcp", "mlops", "abm"} <= set(skills_of(db, job))


def test_language_counts_only_when_required(db, ref):
    english_text = add(db, "Backend Engineer", "We are building a great product in Python. Join us!")
    required = add(db, "Backend Engineer II", "Python. Fluent English required.")
    classify.classify_pending_jobs(db)
    assert "anglais" not in skills_of(db, english_text)
    assert "anglais" in skills_of(db, required)


def test_salary_and_ote(db, ref):
    ae = add(db, "Account Executive", "Package : OTE 80-110 k€ (50/50 fixe + variable)")
    be = add(db, "Backend Engineer", "Salaire : 70 000 € - 90 000 € selon profil")
    classify.classify_pending_jobs(db)
    assert (ae.salary_min_keur, ae.salary_max_keur, ae.salary_kind) == (80, 110, "ote")
    assert (be.salary_min_keur, be.salary_max_keur, be.salary_kind) == (70, 90, "fixe")


def test_llm_value_outside_the_list_becomes_autre(db, ref):
    job = add(db, "Wizard of Things", "Rust, Python et du quantique")
    llm = FakeClassifier({"Wizard of Things": {
        "role_family": "quantum_wizard", "seniority": "senior", "contract": "cdi", "product_sector": "deeptech",
        "skills": [{"id": "rust", "required": True}, {"id": "qiskit", "required": True},
                   {"id": "python", "required": False}],
        "other_skills": ["Qiskit"], "salary_kind": "inconnu", "market_regions": ["mars"]}})
    stats = classify.classify_pending_jobs(db, llm)
    assert stats["llm_calls"] == 1 and stats["autre"] == 1
    assert job.role_family == "autre" and job.seniority_level == "senior" and job.product_sector == "deeptech"
    assert skills_of(db, job) == {"rust": True, "python": False}
    assert job.market_regions == []
    misses = {(m.kind, m.label) for m in db.scalars(select(TaxonomyMiss))}
    assert ("role_family", "quantum_wizard") in misses and ("skill", "qiskit") in misses
    assert ("market_region", "mars") in misses


def test_title_rule_wins_over_the_model_for_the_family(db, ref):
    job = add(db, "Account Executive")
    classify.classify_pending_jobs(db, FakeClassifier({"Account Executive": {"role_family": "growth_acquisition"}}))
    assert job.role_family == "sales"


def test_classified_once_until_title_or_summary_changes(db, ref):
    job = add(db, "Data Engineer", "SQL, dbt")
    llm = FakeClassifier()
    classify.classify_pending_jobs(db, llm)
    assert llm.calls == 1
    job.verified_open_at = job.classified_at  # autre champ : pas de reclassement
    db.commit()
    classify.classify_pending_jobs(db, llm)
    assert llm.calls == 1
    job.summary = "SQL, dbt, Airflow"
    db.commit()
    assert job.classified_at is None
    classify.classify_pending_jobs(db, llm)
    assert llm.calls == 2 and "airflow" in skills_of(db, job)


def test_taxonomy_version_bump_reclassifies(db, ref, monkeypatch):
    job = add(db, "Data Engineer", "SQL")
    classify.classify_pending_jobs(db)
    assert job.taxonomy_version == tx.TAXONOMY_VERSION and classify.count_pending(db) == 0
    monkeypatch.setattr(tx, "TAXONOMY_VERSION", tx.TAXONOMY_VERSION + 1)
    assert classify.count_pending(db) == 1
    classify.classify_pending_jobs(db)
    assert job.taxonomy_version == tx.TAXONOMY_VERSION


def test_quota_stops_cleanly_and_resumes(db, ref):
    for i in range(classify.CLASSIFY_BATCH + 2):
        add(db, f"Backend Engineer {i}")
    stats = classify.classify_pending_jobs(db, FakeClassifier(fail=LLMUnavailable("Gemini 429")))
    assert stats["stopped"] and stats["classified"] == 0 and stats["remaining"] == classify.CLASSIFY_BATCH + 2
    stats = classify.classify_pending_jobs(db, FakeClassifier())
    assert stats["classified"] == classify.CLASSIFY_BATCH + 2 and stats["remaining"] == 0


def test_internship_excluded_from_statistics(db, ref):
    intern = add(db, "Stage - Data Analyst", "SQL")
    cdi = add(db, "Data Analyst", "SQL")
    classify.classify_pending_jobs(db)
    assert intern.contract_type == "stage"
    market.refresh_facts(db)
    ids = {r.job_id for r in market.load_facts(db).rows}
    assert cdi.id in ids and intern.id not in ids
    assert intern.id in {r.job_id for r in market.load_facts(db, include_excluded=True).rows}


def test_taxonomy_sizes():
    assert len(tx.ROLE_FAMILIES) <= 20 and len(tx.DOMAINS) == 6
    assert 100 <= len(tx.SKILLS) <= 120
    assert len({s[0] for s in tx.SKILLS}) == len(tx.SKILLS)


def test_early_stage_startup_is_not_an_internship(db, ref):
    job = add(db, "Backend Engineer", "Join an early stage startup (late-stage investors). Permanent contract.")
    real = add(db, "Data Analyst", "Internship of 6 months, convention de stage requise.")
    classify.classify_pending_jobs(db)
    assert job.contract_type == "cdi" and real.contract_type == "stage"


def test_refused_request_classifies_nothing(db, ref):
    """Requête refusée (clé, modèle, schéma) : on ne classe rien par les seules règles, tout reste à classer."""
    from app.llm import LLMError

    add(db, "Backend Engineer")
    stats = classify.classify_pending_jobs(db, FakeClassifier(fail=LLMError("Requête refusée par Gemini (400)")))
    assert stats["stopped"] and stats["classified"] == 0 and stats["remaining"] == 1


def test_new_output_format_required_and_preferred_skills(db, ref):
    job = add(db, "Data Engineer", "SQL et dbt")
    classify.classify_pending_jobs(db, FakeClassifier({"Data Engineer": {
        "role_family": "data", "required_skills": ["sql", "spark"], "preferred_skills": ["dbt", "pas_dans_la_liste"],
        "salary_min_keur": 0, "salary_max_keur": 0}}))
    assert skills_of(db, job) == {"sql": True, "spark": True, "dbt": False}
    assert job.salary_min_keur is None  # 0 = pas de salaire affiché
    assert "pas_dans_la_liste" in {m.label for m in db.scalars(select(TaxonomyMiss))}
