"""Profil : fusion des imports (sans perte des saisies manuelles) et API."""

from app.profile import ProfileData, apply_import, merge_profile
from app.users import normalize_locale


def manual_profile() -> ProfileData:
    return ProfileData.model_validate({
        "headline": "Développeur backend senior",
        "skills": [{"name": "Python", "level": "expert", "kind": "fort"}, {"name": "Go", "kind": "a_developper"}],
        "experiences": [{"company": "Acme", "title": "Backend Engineer", "start": "2021-03"}],
        "rules": [{"kind": "eviter", "text": "Postes où TypeScript est au cœur du poste"}],
        "preferences": {"roles": ["Backend"], "work_modes": ["remote"]},
    })


def imported_profile() -> ProfileData:
    return ProfileData.model_validate({
        "headline": "Backend Engineer chez Acme",
        "summary": "J'aime les systèmes distribués.",
        "skills": [{"name": "python"}, {"name": "PostgreSQL"}],
        "experiences": [
            {"company": "ACME", "title": "backend engineer", "start": "2021-03", "description": "API et data"},
            {"company": "Globex", "title": "Stagiaire", "start": "2019-01", "end": "2019-06"},
        ],
        "preferences": {"roles": ["backend", "Platform"], "min_salary_keur": 70},
    })


def test_merge_adds_without_losing_manual_data():
    merged, changes = merge_profile(manual_profile(), imported_profile())

    # Compétence existante : niveau et type saisis à la main conservés, pas de doublon
    assert [(s.name, s.level, s.kind) for s in merged.skills] == [
        ("Python", "expert", "fort"), ("Go", None, "a_developper"), ("PostgreSQL", None, "fort"),
    ]
    # Expérience existante complétée (description), nouvelle expérience ajoutée
    assert len(merged.experiences) == 2
    assert merged.experiences[0].company == "Acme"
    assert merged.experiences[0].description == "API et data"
    # Préférences : union des listes, champ vide rempli
    assert merged.preferences.roles == ["Backend", "Platform"]
    assert merged.preferences.min_salary_keur == 70
    assert merged.preferences.work_modes == ["remote"]
    # Les règles saisies restent
    assert len(merged.rules) == 1

    by_field = {c["field"]: c for c in changes}
    assert by_field["headline"]["action"] == "modification"
    assert by_field["summary"]["action"] == "ajout"
    assert "rules" not in by_field


def test_apply_import_keeps_manual_values_unless_accepted():
    current, incoming = manual_profile(), imported_profile()

    result, touched = apply_import(current, incoming)
    assert result.headline == "Développeur backend senior"  # modification non acceptée
    assert result.summary == "J'aime les systèmes distribués."
    assert "headline" not in touched

    result, _ = apply_import(current, incoming, accept={"headline"})
    assert result.headline == "Backend Engineer chez Acme"
    assert result.summary is None  # seul le champ accepté est appliqué


def test_merge_same_data_is_noop():
    p = manual_profile()
    _, changes = merge_profile(p, p)
    assert changes == []


def test_normalize_locale():
    assert normalize_locale({"language": "fr", "country": "FR"}) == "fr_FR"
    assert normalize_locale("en-US") == "en_US"
    assert normalize_locale(None) is None


def test_profile_api_created_on_the_fly_and_patch(alice, as_user):
    client = as_user(alice)
    r = client.get("/api/profile")
    assert r.status_code == 200
    assert r.json()["skills"] == []

    r = client.patch("/api/profile", json={
        "headline": "  Dev   backend ",
        "skills": [{"name": "Python"}, {"name": "Rust", "kind": "a_developper"}],
        "preferences": {"clouds_avoided": ["Azure", "azure"]},
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["headline"] == "Dev backend"
    assert [s["name"] for s in data["skills"]] == ["Python", "Rust"]
    assert data["preferences"]["clouds_avoided"] == ["Azure"]

    # Un PATCH partiel ne touche pas aux autres champs
    data = client.patch("/api/profile", json={"location": "Paris"}).json()
    assert data["headline"] == "Dev backend" and data["location"] == "Paris"


def test_profile_is_private(alice, bob, as_user):
    as_user(alice).patch("/api/profile", json={"headline": "Profil d'Alice"})
    assert as_user(bob).get("/api/profile").json()["headline"] is None


def test_profile_validation(alice, as_user):
    r = as_user(alice).patch("/api/profile", json={"rules": [{"kind": "peut-etre", "text": "x"}]})
    assert r.status_code == 422
