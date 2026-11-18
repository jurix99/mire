"""Propositions élément par élément : opérations rejouées sur le profil actuel, sans perte."""

import pytest
from fastapi import HTTPException

from app.profile import ProfileData
from app.proposals import apply_items, item, number, select_items


def base() -> ProfileData:
    return ProfileData.model_validate({
        "skills": [{"name": "Python", "kind": "fort"}, {"name": "React", "kind": "fort"}],
        "rules": [{"kind": "eviter", "text": "Postes très front"}],
        "preferences": {"roles": ["Backend"]},
    })


def test_update_deactivate_and_preferences():
    items = number([
        item("skills", "update", {"kind": "a_developper"}, match="react", inferred=True, reason="Peu pratiqué"),
        item("rules", "deactivate", match="Postes très front"),
        item("rules", "add", {"kind": "eviter", "text": "SQL au cœur du poste", "source": "feedback", "examples": [4]}),
        item("preferences.roles", "add", "backend"),  # déjà présent (casse ignorée)
        item("preferences.roles", "add", "Platform"),
        item("positioning", "set", "Industrialiser des prototypes IA", inferred=True),
    ])
    data, touched = apply_items(base(), items)
    assert [(s.name, s.kind) for s in data.skills] == [("Python", "fort"), ("React", "a_developper")]
    assert [(r.text, r.active) for r in data.rules] == [("Postes très front", False), ("SQL au cœur du poste", True)]
    assert data.rules[1].examples == [4] and data.rules[1].source == "feedback"
    assert data.preferences.roles == ["Backend", "Platform"]
    assert data.positioning == "Industrialiser des prototypes IA"
    assert touched == {"skills", "rules", "preferences", "positioning"}


def test_missing_target_is_ignored_and_nothing_deleted():
    items = number([item("skills", "update", {"kind": "a_developper"}, match="cobol")])
    data, _ = apply_items(base(), items)
    assert data == base()


def test_invalid_value_is_rejected():
    with pytest.raises(HTTPException) as e:
        apply_items(base(), number([item("rules", "add", {"kind": "peut-etre", "text": "x"})]))
    assert e.value.status_code == 422


def test_select_items_defaults_to_checked():
    items = number([item("headline", "set", "A"), item("summary", "set", "B", checked=False)])
    assert [i["id"] for i in select_items(items, None)] == ["p1"]
    assert [i["id"] for i in select_items(items, ["p2"])] == ["p2"]
