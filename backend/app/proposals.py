"""Propositions de modification du profil, validées ÉLÉMENT PAR ÉLÉMENT par l'utilisateur.

Un même format sert à :
- l'import (fichier LinkedIn, PDF, texte) : faits extraits, fusionnés sans perte ;
- l'analyse du profil : déductions (badge « déduit », justification, confiance) ;
- la synthèse des règles à partir des retours sur les offres (exemples cités).

Élément proposé (dict JSON) :
  id          identifiant dans la proposition ("p1", "p2"…)
  field       champ du profil (skills, rules, preferences.roles, positioning…)
  op          add (ajout dans une liste) | set (valeur simple) | update (remplace l'élément `match`)
              | deactivate (règle `match` désactivée)
  value       nouvelle valeur (élément de liste, ou valeur simple)
  match       clé de l'élément visé (update / deactivate), ex. "python" ou le texte d'une règle
  label       libellé du champ ; display : texte lisible ; before : valeur actuelle (lisible)
  inferred    True si c'est une déduction (analyse, synthèse) et non un fait
  reason      justification courte ; confidence : haute | moyenne | basse
  examples    offres citées (« Entreprise – Titre »), pour les règles issues des retours
  checked     coché par défaut dans l'aperçu (False quand on remplacerait une saisie)

À l'application, les éléments acceptés sont rejoués sur le profil ACTUEL (qui a pu changer) :
un ajout déjà présent est ignoré, rien n'est jamais supprimé (une règle n'est que désactivée).
"""

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from .profile import (
    FIELD_LABELS,
    Preferences,
    ProfileData,
    _describe,
    _fill_empty,
    _item_key,
    _norm,
)

LIST_FIELDS = ("languages", "experiences", "education", "skills", "rules", "excluded_companies", "reference_jobs")
SCALAR_FIELDS = ("headline", "summary", "location", "years_experience", "positioning")
PREF_LIST_FIELDS = ("roles", "locations", "work_modes", "company_stages", "clouds_preferred", "clouds_avoided",
                    "sectors")
PREF_SCALAR_FIELDS = ("min_salary_keur", "travel")
assert set(PREF_LIST_FIELDS) | set(PREF_SCALAR_FIELDS) == set(Preferences.model_fields)
PREF_LABELS = {
    "roles": "Rôles visés", "locations": "Lieux", "work_modes": "Mode de travail",
    "company_stages": "Stades d'entreprise", "clouds_preferred": "Clouds préférés",
    "clouds_avoided": "Clouds à éviter", "sectors": "Secteurs", "min_salary_keur": "Salaire minimum (k€)",
    "travel": "Déplacements",
}
ITEM_MODELS = {name: ProfileData.model_fields[name].annotation.__args__[0] for name in LIST_FIELDS}


class ProposalItem(BaseModel):
    id: str
    field: str
    op: str
    value: Any = None
    match: str | None = None
    label: str
    display: str
    before: str | None = None
    inferred: bool = False
    reason: str | None = None
    confidence: str | None = None
    examples: list[str] = []
    checked: bool = True


def field_label(field: str) -> str:
    if field.startswith("preferences."):
        return PREF_LABELS.get(field.split(".", 1)[1], field)
    return FIELD_LABELS.get(field, field)


def display_value(field: str, value: Any) -> str:
    base = field.split(".", 1)[0]
    if base in LIST_FIELDS and isinstance(value, dict):
        try:
            return _describe(base, ITEM_MODELS[base].model_validate(value))
        except ValidationError:  # valeur partielle (update) ou invalide : validée à l'application
            return ", ".join(f"{k} : {v}" for k, v in value.items() if v not in (None, "", []))
    if isinstance(value, float):
        return f"{value:g}"
    text = str(value)
    return text if len(text) <= 400 else text[:399] + "…"


def number(items: list[dict]) -> list[dict]:
    for i, item in enumerate(items, 1):
        item["id"] = f"p{i}"
    return items


def item(field: str, op: str, value: Any = None, *, match: str | None = None, before: Any = None,
         checked: bool = True, **extra) -> dict:
    return {
        "id": "", "field": field, "op": op, "value": value, "match": match, "label": field_label(field),
        "display": display_value(field, value) if value is not None else (match or ""),
        "before": display_value(field, before) if before not in (None, "", []) else None,
        "checked": checked, **extra,
    }


# --- Import : diff factuel -> propositions -----------------------------------------


def import_proposals(current: ProfileData, incoming: ProfileData) -> list[dict]:
    """Propositions élément par élément pour fusionner un import (sans perte)."""
    items: list[dict] = []
    for field in SCALAR_FIELDS:
        cur, new = getattr(current, field), getattr(incoming, field)
        if new not in (None, "") and new != cur:
            # Remplacer une valeur déjà saisie : proposé mais pas coché par défaut
            items.append(item(field, "set", new, before=cur, checked=cur in (None, "")))
    for field in LIST_FIELDS:
        cur_items = getattr(current, field)
        index = {_item_key(field, x): x for x in cur_items}
        for new in getattr(incoming, field):
            key = _item_key(field, new)
            if not any(key):
                continue
            if key not in index:
                items.append(item(field, "add", new.model_dump(mode="json")))
                index[key] = new
            else:
                completed = _fill_empty(index[key], new)
                if completed != index[key]:
                    items.append(item(field, "update", completed.model_dump(mode="json"),
                                      match="|".join(key), before=index[key].model_dump(mode="json")))
    cur_p, new_p = current.preferences, incoming.preferences
    for name in PREF_LIST_FIELDS:
        known = {_norm(v) for v in getattr(cur_p, name)}
        for v in getattr(new_p, name):
            if _norm(v) not in known:
                items.append(item(f"preferences.{name}", "add", v))
                known.add(_norm(v))
    for name in PREF_SCALAR_FIELDS:
        cur, new = getattr(cur_p, name), getattr(new_p, name)
        if new is not None and new != cur:
            items.append(item(f"preferences.{name}", "set", new, before=cur, checked=cur is None))
    return number(items)


# --- Application -------------------------------------------------------------------


def _find(field: str, items: list[dict], match: str) -> int | None:
    model = ITEM_MODELS[field]
    for i, x in enumerate(items):
        key = "|".join(_item_key(field, model.model_validate(x)))
        if key == match or (field == "rules" and _norm(x.get("text")) == _norm(match)) \
                or (field == "skills" and _norm(x.get("name")) == _norm(match)):
            return i
    return None


def apply_items(current: ProfileData, items: list[dict]) -> tuple[ProfileData, set[str]]:
    """Rejoue les éléments acceptés sur le profil actuel. Retourne (profil, champs modifiés)."""
    try:
        return _apply_items(current, items)
    except ValidationError as exc:
        err = exc.errors()[0]
        raise HTTPException(422, f"Proposition invalide ({'.'.join(map(str, err['loc']))}) : {err['msg']}") from None


def _apply_items(current: ProfileData, items: list[dict]) -> tuple[ProfileData, set[str]]:
    data = current.model_dump(mode="json")
    touched: set[str] = set()
    for it in items:
        field, op, value = it["field"], it["op"], it.get("value")
        if field.startswith("preferences."):
            name = field.split(".", 1)[1]
            prefs = data["preferences"]
            if op == "add" and name in PREF_LIST_FIELDS:
                if _norm(value) not in {_norm(v) for v in prefs[name]}:
                    prefs[name] = [*prefs[name], value]
                    touched.add("preferences")
            elif op == "set" and name in PREF_SCALAR_FIELDS:
                prefs[name] = value
                touched.add("preferences")
            continue
        if field in SCALAR_FIELDS and op == "set":
            data[field] = value
            touched.add(field)
        elif field in LIST_FIELDS:
            lst = list(data[field])
            model = ITEM_MODELS[field]
            if op == "add":
                new_key = _item_key(field, model.model_validate(value))
                if all(_item_key(field, model.model_validate(x)) != new_key for x in lst):
                    lst.append(value)
            elif op in ("update", "deactivate"):
                i = _find(field, lst, it.get("match") or "")
                if i is None:
                    continue  # l'élément a disparu entre-temps : rien à faire
                lst[i] = {**lst[i], "active": False} if op == "deactivate" else {**lst[i], **value}
            data[field] = lst
            touched.add(field)
    return ProfileData.model_validate(data), touched


def select_items(items: list[dict], accept: list[str] | None) -> list[dict]:
    """Éléments acceptés : ceux listés dans `accept`, ou ceux cochés par défaut si `accept` est absent."""
    if accept is None:
        return [i for i in items if i.get("checked", True)]
    wanted = set(accept)
    return [i for i in items if i["id"] in wanted]
