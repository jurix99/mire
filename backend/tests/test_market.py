"""Agrégats du marché (market.py), masquage Free / Pro, baromètre public."""

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from app import market, public_api
from app import taxonomy as tx
from app.models import Company, Job, JobAssessment, JobSkill

TODAY = date.today()


@pytest.fixture(autouse=True)
def ref(db):
    tx.sync(db)
    db.commit()
    public_api._BAROMETER.update(value=None, at=0.0)


@pytest.fixture
def billing_on(monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "true")
    monkeypatch.setenv("PRO_USER_EMAILS", "alice@example.com")


def company(db, name, **kw):
    c = Company(name=name, slug=name.lower().replace(" ", "-"), **kw)
    db.add(c)
    db.flush()
    return c


def job(db, c, family, skills=(), days_ago=10, **kw):
    """Offre déjà classée (sans passer par classify) : famille, compétences exigées, champs normalisés."""
    j = Job(company=c, title=f"{family} {c.name}", published_on=TODAY - timedelta(days=days_ago), role_family=family,
            seniority_level=kw.pop("seniority", "confirme"), contract_type=kw.pop("contract", "cdi"),
            classified_at=datetime.now(UTC), taxonomy_version=tx.TAXONOMY_VERSION, **kw)
    db.add(j)
    db.flush()
    for s in skills:
        db.add(JobSkill(job_id=j.id, skill_id=s, required=True))
    return j


def overview(db, **kw):
    market.refresh_facts(db)
    return market.market_overview(market.load_facts(db), **kw)


def item(items, key):
    return next((i for i in items if i["key"] == key), None)


def test_count_by_company_counts_a_big_poster_once(db):
    big = company(db, "Big")
    for _ in range(25):
        job(db, big, "backend", ["python"])
    for n in range(5):
        job(db, company(db, f"Small {n}"), "backend", ["go"])
    db.commit()
    by_company = overview(db, count_by="entreprise")["indicators"]["skills"]
    assert item(by_company["top"], "python")["units"] == 1 and item(by_company["top"], "go")["units"] == 5
    assert by_company["top"][0]["key"] == "go"  # la boîte qui publie 25 offres ne fausse pas tout
    by_job = overview(db, count_by="offre")["indicators"]["skills"]
    assert item(by_job["top"], "python")["units"] == 25 and by_job["top"][0]["key"] == "python"


def test_thresholds_hide_small_categories_and_flag_low_data(db):
    for n in range(6):
        c = company(db, f"Co {n}")
        job(db, c, "data", ["sql", "dbt"] if n < 4 else ["sql"])  # dbt : 4 offres, sous le seuil de 5
    db.commit()
    skills = overview(db, count_by="offre")["indicators"]["skills"]
    assert item(skills["top"], "sql")["jobs"] == 6 and item(skills["top"], "dbt") is None
    assert item(skills["top"], "sql")["low_data"] is True  # base de 6 offres < 20 : « peu de données »
    for n in range(20):
        job(db, company(db, f"More {n}"), "data", ["sql"])
    db.commit()
    assert item(overview(db, count_by="offre")["indicators"]["skills"]["top"], "sql")["low_data"] is False


def test_trends_compare_last_30_days_with_previous_90(db):
    for n in range(25):  # avant : Kubernetes rare
        job(db, company(db, f"Old {n}"), "platform_devops_sre", ["kubernetes"] if n < 5 else ["linux"], days_ago=60)
    for n in range(25):  # récent : Kubernetes partout
        job(db, company(db, f"New {n}"), "platform_devops_sre", ["kubernetes"], days_ago=5)
    db.commit()
    trends = overview(db, period="6m", count_by="offre")["indicators"]["trends"]
    k8s = item(trends["up"], "kubernetes")
    assert k8s and k8s["pp"] == 80.0 and k8s["share_before"] == 0.2 and k8s["share_recent"] == 1.0
    assert item(trends["down"], "linux")["pp"] == -80.0
    assert "Kubernetes gagne 80 points" in trends["headline"]


def test_salaries_separate_fixed_and_ote(db):
    for n in range(6):
        c = company(db, f"Sales {n}")
        job(db, c, "sales", salary_min_keur=50, salary_max_keur=60, salary_kind="fixe")
        job(db, c, "sales", salary_min_keur=90, salary_max_keur=110, salary_kind="ote")
    db.commit()
    items = overview(db)["indicators"]["salaries"]["items"]
    by_kind = {i["kind"]: i for i in items if i["family"] == "sales"}
    assert by_kind["fixe"]["median"] == 55 and by_kind["ote"]["median"] == 100
    assert by_kind["fixe"]["jobs"] == 6 and by_kind["ote"]["jobs"] == 6


def test_delay_between_funding_and_first_job(db):
    for n, delay in enumerate([10, 20, 30, 40, 50]):
        funded = TODAY - timedelta(days=100)
        c = company(db, f"Funded {n}", funding_announced_on=funded, funding_stage="Série A")
        job(db, c, "backend", days_ago=100 - delay)  # première offre après la levée
        job(db, c, "sales", days_ago=100 - delay - 5)
        job(db, c, "data", days_ago=150)  # avant la levée : ignorée pour le délai
    db.commit()
    after = overview(db, period="6m")["indicators"]["after_funding"]
    assert after["median_delay_days"] == 30 and after["companies"] == 5
    assert after["first_families"][0]["key"] == "backend"
    serie_a = item(after["by_round"], "serie_a")
    assert serie_a["tech_jobs"] == 10 and serie_a["gtm_jobs"] == 5  # backend + data / sales


def test_investors_are_split_and_normalized(db):
    for n in range(6):
        c = company(db, f"VC {n}", investors="Bpi France, Partech Partners et quelques business angels")
        job(db, c, "backend")
    db.commit()
    names = {i["label"] for i in overview(db)["indicators"]["investors"]["items"]}
    assert names == {"bpifrance", "Partech"}


# --- Masquage Free / Pro ----------------------------------------------------------------------------------------

SECRET_FUND = "Fonds Ultra Secret Capital"


def rich_radar(db):
    for n in range(30):
        c = company(db, f"Startup {n}", investors=SECRET_FUND, funding_stage="Seed",
                    funding_announced_on=TODAY - timedelta(days=60))
        fam = ["backend", "sales", "data"][n % 3]
        skills = {"backend": ["python", "postgresql", "aws"], "sales": ["hubspot", "closing", "anglais"],
                  "data": ["sql", "dbt", "python"]}[fam]
        job(db, c, fam, skills, days_ago=n % 40, salary_min_keur=61, salary_max_keur=77, salary_kind="fixe",
            work_mode="hybride")
    db.commit()
    market.refresh_facts(db)


def test_free_user_gets_no_real_value_of_pro_indicators(db, alice, bob, as_user, billing_on):
    alice.email = "alice@example.com"
    db.commit()
    rich_radar(db)
    free = as_user(bob).get("/api/analytics/market?period=6m").json()
    text = json.dumps(free, ensure_ascii=False)
    assert free["plan"] == "free" and free["period_locked"] is True and free["filters"]["period"] == "90j"
    for key in ("trends", "stacks", "salaries", "work_modes", "after_funding", "lifetimes", "markets", "investors"):
        ind = free["indicators"][key]
        assert ind["locked"] is True and "items" not in ind and "up" not in ind
    assert SECRET_FUND not in text and "69 k€" not in text and '"median"' not in text
    assert len(free["indicators"]["skills"]["top"]) <= 5  # en clair, top 5
    assert free["indicators"]["roles"]["families"]
    pro = as_user(alice).get("/api/analytics/market?period=6m").json()
    assert pro["plan"] == "pro" and SECRET_FUND in json.dumps(pro, ensure_ascii=False)
    assert pro["indicators"]["salaries"]["items"][0]["median"] == 69


def test_free_user_me_tab_is_a_generic_preview(db, alice, bob, as_user, billing_on):
    rich_radar(db)
    as_user(bob).patch("/api/profile", json={"skills": [{"name": "Python"}], "preferences": {"roles": ["Backend"]},
                                              "positioning": "Backend"})
    db.add(JobAssessment(user_id=bob.id, job_id=1, score=8.5, verdict="nouveau", profile_version=1,
                         skills_missing=["Rustacean-secret"], assessed_at=datetime.now(UTC)))
    db.commit()
    me = as_user(bob).get("/api/analytics/me").json()
    assert me["locked"] is True and me["family"] is None
    text = json.dumps(me, ensure_ascii=False)
    assert "Rustacean-secret" not in text and "Python" not in text and "Backend" not in text


def test_me_tab_for_pro(db, alice, as_user):
    rich_radar(db)
    as_user(alice).patch("/api/profile", json={
        "skills": [{"name": "Python"}, {"name": "PostgreSQL"}], "preferences": {"roles": ["Backend engineer"],
                                                                                "min_salary_keur": 70},
        "years_experience": 3, "positioning": "Backend"})
    for jid in range(1, 5):
        db.add(JobAssessment(user_id=alice.id, job_id=jid, score=8, verdict="nouveau", profile_version=1,
                             skills_missing=["AWS"], skills_have=["Python"], assessed_at=datetime.now(UTC)))
    db.commit()
    me = as_user(alice).get("/api/analytics/me").json()
    assert me["locked"] is False and me["family"] == "backend"
    assert me["missing"]["items"][0]["key"] == "aws" and me["missing"]["items"][0]["share"] == 1.0
    assert me["coverage"]["share"] == 0.0  # python + postgresql sur 3 exigées : 67 % < 70 %
    assert {i["key"] for i in me["strengths"]["items"]} == {"python", "postgresql"}


def test_admin_taxonomy_health(db, alice, as_user):
    rich_radar(db)
    h = as_user(alice).get("/api/admin/taxonomy-health").json()  # BILLING_ENABLED=false : tout le monde est admin
    assert h["classified"] == 30 and h["autre_share"] == 0.0
    assert any(s["key"] == "rust" for s in h["never_seen_skills"])


# --- Baromètre public ---------------------------------------------------------------------------------------------


def test_public_barometer_has_no_company_name_and_respects_thresholds(db, client_anon):
    rich_radar(db)
    job(db, company(db, "Rare Corp"), "juridique", ["droit_contrats"], days_ago=3)  # 1 offre : sous le seuil
    db.commit()
    market.refresh_facts(db)
    r = client_anon.get("/api/public/barometer")
    assert r.status_code == 200
    b = r.json()
    text = json.dumps(b, ensure_ascii=False)
    assert "Startup" not in text and "Rare Corp" not in text and SECRET_FUND not in text
    assert b["jobs"] == 31 and b["companies"] == 31  # 30 derniers jours (les 30 du radar + Rare Corp)
    assert all(i["jobs"] >= market.MIN_JOBS for i in b["top_skills"]["items"])
    assert "droit_contrats" not in {i["key"] for i in b["top_skills"]["items"]}
    assert len(b["top_skills"]["items"]) <= market.BAROMETER_TOP


@pytest.fixture
def client_anon():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_tracking_tab_unchanged(alice, as_user, catalog):
    data = as_user(alice).get("/api/analytics").json()
    assert {"by_status", "funnel", "weekly", "by_company"} <= set(data)
