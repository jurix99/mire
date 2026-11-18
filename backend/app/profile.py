"""Profil utilisateur : schémas (Pydantic), lecture / écriture, fusion des imports, résumé pour le chat.

Fusion d'un import (merge_profile) : on ne supprime jamais ce qui existe.
- listes (compétences, expériences…) : ajout des éléments absents ; un élément déjà présent garde
  les valeurs saisies et ne reçoit que les sous-champs vides ;
- champs simples (headline, résumé…) : remplis s'ils sont vides ; s'ils ont déjà une valeur différente,
  le changement est proposé comme « modification », appliqué seulement si l'utilisateur l'accepte.
"""

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from .models import User, UserProfile


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split()) if "\n" not in value else value.strip()
    return value or None


def _clean_list(values: list[str]) -> list[str]:
    """Supprime les vides et les doublons (sans tenir compte de la casse), garde l'ordre."""
    seen: set[str] = set()
    out = []
    for v in values:
        v = " ".join(v.split())
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


Short = Annotated[str | None, Field(default=None, max_length=200), AfterValidator(_clean)]
Long = Annotated[str | None, Field(default=None, max_length=5000), AfterValidator(_clean)]
DateText = Annotated[str | None, Field(default=None, max_length=30), AfterValidator(_clean)]
Name = Annotated[str, Field(min_length=1, max_length=120), AfterValidator(lambda v: " ".join(v.split()))]
StrList = Annotated[list[Annotated[str, Field(max_length=120)]], Field(default_factory=list, max_length=50),
                    AfterValidator(_clean_list)]


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Experience(Model):
    company: Short
    title: Short
    start: DateText = Field(default=None, description="Début, ex. 2021-03")
    end: DateText = Field(default=None, description="Fin, ex. 2023-06 ; vide si poste actuel")
    location: Short
    description: Long


class Education(Model):
    school: Short
    degree: Short
    field: Short
    start: DateText
    end: DateText


SkillKind = Literal["fort", "a_developper"]


class Skill(Model):
    name: Name
    level: Annotated[str | None, Field(default=None, max_length=40), AfterValidator(_clean)]
    kind: SkillKind = "fort"


class Language(Model):
    name: Name
    level: Annotated[str | None, Field(default=None, max_length=60), AfterValidator(_clean)]


WorkMode = Literal["remote", "hybride", "sur_site"]


class Preferences(Model):
    roles: StrList = Field(description="Rôles visés")
    locations: StrList = Field(description="Lieux souhaités")
    work_modes: list[WorkMode] = Field(default_factory=list, description="remote, hybride, sur_site")
    company_stages: StrList = Field(description="Stades d'entreprise visés (seed, série A…)")
    min_salary_keur: Annotated[int | None, Field(default=None, ge=0, le=1000)] = None
    clouds_preferred: StrList
    clouds_avoided: StrList
    sectors: StrList


RuleKind = Literal["eviter", "preferer"]


class Rule(Model):
    kind: RuleKind
    text: Annotated[str, Field(min_length=1, max_length=300), AfterValidator(lambda v: " ".join(v.split()))]


class ProfileData(Model):
    """Contenu éditable du profil (aussi le schéma de sortie demandé à Gemini pour les imports)."""

    headline: Annotated[str | None, Field(default=None, max_length=300), AfterValidator(_clean)]
    summary: Long
    location: Short
    years_experience: Annotated[float | None, Field(default=None, ge=0, le=60)] = None
    languages: list[Language] = Field(default_factory=list, max_length=30)
    experiences: list[Experience] = Field(default_factory=list, max_length=60)
    education: list[Education] = Field(default_factory=list, max_length=30)
    skills: list[Skill] = Field(default_factory=list, max_length=200)
    preferences: Preferences = Field(default_factory=Preferences)
    rules: list[Rule] = Field(default_factory=list, max_length=50)


class ProfilePatch(ProfileData):
    """PATCH /api/profile : seuls les champs envoyés sont remplacés."""


class ImportTrace(Model):
    source: str
    filename: str | None = None
    imported_at: datetime


class ProfileOut(ProfileData):
    raw_imports: list[ImportTrace] = []
    updated_at: datetime | None = None


DATA_FIELDS = tuple(ProfileData.model_fields)

FIELD_LABELS = {
    "headline": "Titre",
    "summary": "Résumé",
    "location": "Localisation",
    "years_experience": "Années d'expérience",
    "languages": "Langues",
    "experiences": "Expériences",
    "education": "Formation",
    "skills": "Compétences",
    "preferences": "Préférences",
    "rules": "Règles",
}


# --- Lecture / écriture ------------------------------------------------------


def get_profile(db: Session, user: User) -> UserProfile:
    """Profil de l'utilisateur, créé à la volée s'il n'existe pas encore."""
    profile = db.query(UserProfile).filter_by(user_id=user.id).one_or_none()
    if profile is None:
        profile = UserProfile(
            user_id=user.id, languages=[], experiences=[], education=[], skills=[], preferences={}, rules=[],
            raw_imports=[],
        )
        db.add(profile)
        db.commit()
    return profile


def profile_data(profile: UserProfile) -> ProfileData:
    return ProfileData.model_validate({
        f: (float(v) if f == "years_experience" and v is not None else v)
        for f in DATA_FIELDS
        if (v := getattr(profile, f)) is not None
    })


def profile_out(profile: UserProfile) -> ProfileOut:
    out = ProfileOut.model_validate({**profile_data(profile).model_dump(), "raw_imports": profile.raw_imports or []})
    out.updated_at = profile.updated_at
    return out


def save_fields(profile: UserProfile, data: ProfileData, fields: set[str] | None = None) -> None:
    """Copie les champs de `data` dans le profil (nouvelles valeurs JSON : SQLAlchemy détecte le changement)."""
    dumped = data.model_dump(mode="json")
    for f in fields if fields is not None else DATA_FIELDS:
        setattr(profile, f, dumped[f])


def add_import_trace(profile: UserProfile, source: str, filename: str | None) -> None:
    trace = {"source": source, "filename": filename, "imported_at": datetime.now(UTC).isoformat()}
    profile.raw_imports = [*(profile.raw_imports or []), trace][-50:]


# --- Fusion d'un import ------------------------------------------------------


def _norm(v: Any) -> str:
    return " ".join(str(v or "").lower().split())


def _item_key(field: str, item: BaseModel) -> tuple:
    match field:
        case "experiences":
            return (_norm(item.company), _norm(item.title), _norm(item.start))
        case "education":
            return (_norm(item.school), _norm(item.degree))
        case "rules":
            return (_norm(item.text),)
        case _:  # skills, languages
            return (_norm(item.name),)


def _fill_empty(current: BaseModel, incoming: BaseModel) -> BaseModel:
    """Complète les sous-champs vides de `current` avec ceux de `incoming`."""
    updates = {
        k: v for k, v in incoming.model_dump().items()
        if v not in (None, "", []) and getattr(current, k) in (None, "", [])
    }
    return current.model_copy(update=updates) if updates else current


def _merge_list(field: str, current: list, incoming: list) -> tuple[list, list, list]:
    """Retourne (liste fusionnée, éléments ajoutés, éléments complétés)."""
    merged = list(current)
    index = {_item_key(field, item): i for i, item in enumerate(merged)}
    added, completed = [], []
    for item in incoming:
        key = _item_key(field, item)
        if not any(key):
            continue
        if key in index:
            before = merged[index[key]]
            after = _fill_empty(before, item)
            if after != before:
                merged[index[key]] = after
                completed.append(after)
        else:
            index[key] = len(merged)
            merged.append(item)
            added.append(item)
    return merged, added, completed


def _merge_preferences(current: Preferences, incoming: Preferences) -> tuple[Preferences, dict]:
    updates, changes = {}, {}
    for name in Preferences.model_fields:
        cur, new = getattr(current, name), getattr(incoming, name)
        if isinstance(cur, list):
            extra = [v for v in new if _norm(v) not in {_norm(c) for c in cur}]
            if extra:
                updates[name] = [*cur, *extra]
                changes[name] = extra
        elif cur is None and new is not None:
            updates[name] = new
            changes[name] = new
    return current.model_copy(update=updates), changes


def _describe(field: str, item: BaseModel) -> str:
    match field:
        case "experiences":
            dates = " – ".join(filter(None, [item.start, item.end or ("aujourd'hui" if item.start else None)]))
            return " · ".join(filter(None, [item.title, item.company, dates]))
        case "education":
            return " · ".join(filter(None, [item.degree, item.field, item.school]))
        case "skills":
            return f"{item.name} ({'à développer' if item.kind == 'a_developper' else 'fort'})"
        case "languages":
            return " · ".join(filter(None, [item.name, item.level]))
        case "rules":
            return f"{'Éviter' if item.kind == 'eviter' else 'Préférer'} : {item.text}"
    return str(item)


def merge_profile(current: ProfileData, incoming: ProfileData) -> tuple[ProfileData, list[dict]]:
    """Fusionne `incoming` dans `current`.

    Retourne le profil fusionné (toutes modifications acceptées) et la liste des changements :
    [{field, label, action: "ajout" | "modification", before?, after?, items?}]
    Une « modification » remplace une valeur saisie : elle n'est appliquée que si l'utilisateur l'accepte
    (voir apply_import).
    """
    merged = current.model_copy()
    changes: list[dict] = []
    for field in DATA_FIELDS:
        cur, new = getattr(current, field), getattr(incoming, field)
        label = FIELD_LABELS[field]
        if field == "preferences":
            value, diff = _merge_preferences(cur, new)
            if diff:
                merged.preferences = value
                items = [f"{k} : {', '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in diff.items()]
                changes.append({"field": field, "label": label, "action": "ajout", "items": items})
        elif isinstance(cur, list):
            value, added, completed = _merge_list(field, cur, new)
            if added or completed:
                setattr(merged, field, value)
                items = [_describe(field, i) for i in added] + [f"{_describe(field, i)} (complété)" for i in completed]
                changes.append({"field": field, "label": label, "action": "ajout", "items": items})
        elif new not in (None, "") and new != cur:
            setattr(merged, field, new)
            action = "ajout" if cur in (None, "") else "modification"
            changes.append({"field": field, "label": label, "action": action, "before": cur, "after": new})
    return merged, changes


def apply_import(current: ProfileData, incoming: ProfileData, accept: set[str] | None = None) -> tuple[ProfileData, set[str]]:
    """Applique un import : par défaut tous les ajouts, et les modifications seulement si listées dans `accept`.

    `accept` = noms de champs acceptés (None = ajouts uniquement). Retourne (profil, champs modifiés).
    """
    merged, changes = merge_profile(current, incoming)
    result = current.model_copy()
    touched = set()
    for change in changes:
        field = change["field"]
        wanted = field in accept if accept is not None else change["action"] == "ajout"
        if wanted:
            setattr(result, field, getattr(merged, field))
            touched.add(field)
    return result, touched


# --- Résumé compact pour l'instruction système du chat ------------------------


def profile_summary(user: User, data: ProfileData, max_chars: int = 2500) -> str:
    lines = [f"Nom : {user.name or user.given_name or 'inconnu'}"]
    if data.headline:
        lines.append(f"Titre : {data.headline}")
    if data.location:
        lines.append(f"Localisation : {data.location}")
    if data.years_experience is not None:
        lines.append(f"Années d'expérience : {data.years_experience:g}")
    if data.languages:
        lines.append("Langues : " + ", ".join(_describe("languages", lang) for lang in data.languages))
    strong = [s.name for s in data.skills if s.kind == "fort"]
    grow = [s.name for s in data.skills if s.kind == "a_developper"]
    if strong:
        lines.append("Compétences fortes : " + ", ".join(strong[:40]))
    if grow:
        lines.append("Compétences à développer : " + ", ".join(grow[:20]))
    for exp in data.experiences[:4]:
        lines.append("Expérience : " + _describe("experiences", exp))
    p = data.preferences
    prefs = [
        ("rôles", p.roles), ("lieux", p.locations), ("modes", p.work_modes), ("stades", p.company_stages),
        ("clouds préférés", p.clouds_preferred), ("clouds à éviter", p.clouds_avoided), ("secteurs", p.sectors),
    ]
    pref_text = "; ".join(f"{k} : {', '.join(v)}" for k, v in prefs if v)
    if p.min_salary_keur:
        pref_text += f"; salaire min : {p.min_salary_keur} k€"
    if pref_text:
        lines.append("Préférences : " + pref_text.strip("; "))
    for rule in data.rules:
        lines.append("Règle : " + _describe("rules", rule))
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def today_label() -> str:
    return date.today().isoformat()
