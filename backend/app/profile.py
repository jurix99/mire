"""Profil utilisateur : schémas (Pydantic), lecture / écriture, fusion des imports, résumé pour le chat.

Fusion d'un import (merge_profile) : on ne supprime jamais ce qui existe.
- listes (compétences, expériences…) : ajout des éléments absents ; un élément déjà présent garde
  les valeurs saisies et ne reçoit que les sous-champs vides ;
- champs simples (headline, résumé…) : remplis s'ils sont vides ; s'ils ont déjà une valeur différente,
  le changement est proposé comme « modification », appliqué seulement si l'utilisateur l'accepte.
"""

import os
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


Url = Annotated[str | None, Field(default=None, max_length=300), AfterValidator(_clean)]


class ApplicationDefaults(Model):
    """Réponses types pour les candidatures (Mon profil), reprises TELLES QUELLES dans les formulaires (origine
    « Réponse type ») : jamais envoyées à Gemini, jamais déduites. Une valeur absente laisse le champ « À toi »."""

    first_name: Short
    last_name: Short
    email: Annotated[str | None, Field(default=None, max_length=320), AfterValidator(_clean)]
    phone: Annotated[str | None, Field(default=None, max_length=40), AfterValidator(_clean)]
    linkedin_url: Url
    github_url: Url
    portfolio_url: Url
    city: Short
    # Autorisé à travailler en France / UE sans visa (oui : pas de parrainage nécessaire)
    work_authorization: Literal["oui", "non"] | None = None
    availability: Short = Field(default=None, description="Préavis ou disponibilité, ex. « Préavis de 3 mois »")
    available_from: DateText = Field(default=None, description="Date de disponibilité, AAAA-MM-JJ")
    salary_min_keur: Annotated[int | None, Field(default=None, ge=0, le=1000)] = None
    salary_max_keur: Annotated[int | None, Field(default=None, ge=0, le=1000)] = None
    work_mode: WorkMode | None = None
    languages: StrList = Field(description="Ex. Français (natif), Anglais (courant)")


class SearchPreferences(Model):
    """Préférences de recherche (aussi le schéma d'extraction d'un import, sans les réponses types)."""

    roles: StrList = Field(description="Rôles visés")
    locations: StrList = Field(description="Lieux souhaités")
    work_modes: list[WorkMode] = Field(default_factory=list, description="remote, hybride, sur_site")
    company_stages: StrList = Field(description="Stades d'entreprise visés (seed, série A…)")
    min_salary_keur: Annotated[int | None, Field(default=None, ge=0, le=1000)] = None
    clouds_preferred: StrList
    clouds_avoided: StrList
    sectors: StrList
    travel: Annotated[Literal["aucun", "occasionnel", "frequent"] | None,
                      Field(default=None, description="Tolérance aux déplacements")] = None


class Preferences(SearchPreferences):
    # Réponses types des candidatures : sans effet sur la notation (ne change pas la version du profil)
    application_defaults: ApplicationDefaults = Field(default_factory=ApplicationDefaults)


RuleKind = Literal["eviter", "preferer"]
RuleSource = Literal["manuel", "import", "analyse", "feedback", "chat"]
RuleText = Annotated[str, Field(min_length=1, max_length=500), AfterValidator(lambda v: " ".join(v.split()))]


class FactualRule(Model):
    kind: RuleKind
    text: RuleText


class Rule(FactualRule):
    source: RuleSource = "manuel"
    examples: list[int] = Field(default_factory=list, max_length=20, description="Postes qui ont fait naître la règle")
    active: bool = True  # une règle inactive est gardée mais ignorée (évaluation, chat)
    created_at: Annotated[str | None, Field(default=None, max_length=30)] = None  # date ISO


class ExcludedCompany(Model):
    name: Name
    company_id: int | None = None
    reason: Short


class ReferenceJob(Model):
    """Offre de référence (« j'aime ») : à rapprocher des nouvelles offres."""

    job_id: int | None = None
    title: Short
    company: Short
    why: Annotated[str | None, Field(default=None, max_length=1000), AfterValidator(_clean)]


# Barème : clé -> (libellé, poids par défaut). Total par défaut : 10.
CRITERIA: dict[str, tuple[str, float]] = {
    "competences": ("Correspondance des compétences", 3),
    "stade": ("Stade de l'entreprise et qualité des investisseurs", 2),
    "ownership": ("Transversalité / ownership", 2),
    "ia_concrete": ("Produit IA concret avec impact réel", 1),
    "xp": ("Expérience demandée compatible", 1),
    "references": ("Ressemblance avec les offres de référence", 1),
}
DEFAULT_THRESHOLD = 6.0

# --- Critères personnels (notation v2, voir assessments.py) -----------------------------------------------------
# En v1, stade / ownership / ia_concrete encodent un goût unique (amorçage, premier ingénieur, produit IA). En v2,
# chacun a une DÉFINITION propre à l'utilisateur : la sienne (scoring.definitions), sinon déduite de ses
# préférences (stades, secteurs visés), sinon aucune : le critère est alors neutre (0,5 pour toutes les offres)
# au lieu d'imposer le goût de quelqu'un d'autre.
VARIANTS = ("v1", "v2")
PERSONAL_CRITERIA = ("stade", "ownership", "ia_concrete")
CRITERIA_V2_LABELS = {
    "stade": "Stade et taille de l'entreprise",
    "ownership": "Type de rôle",
    "ia_concrete": "Produit et secteur",
}
DEFINITION_HINTS = {
    "stade": "Ex. : startup en amorçage ou Série A ; ou scale-up de plus de 200 personnes ; ou grand groupe",
    "ownership": "Ex. : parmi les premiers ingénieurs, rôle transverse ; ou équipe structurée avec un périmètre clair",
    "ia_concrete": "Ex. : produit IA concret avec des clients réels ; ou santé, climat ; ou outils pour développeurs",
}


def scoring_variant() -> str:
    """Variante de notation en cours (SCORING_VARIANT, v1 par défaut ; lue à chaque appel)."""
    variant = os.environ.get("SCORING_VARIANT", "v1").strip() or "v1"
    if variant not in VARIANTS:
        raise ValueError(f"SCORING_VARIANT inconnue : {variant} (attendu : {', '.join(VARIANTS)})")
    return variant


def _check_weights(weights: dict[str, float]) -> dict[str, float]:
    unknown = set(weights) - set(CRITERIA)
    if unknown:
        raise ValueError(f"Critères inconnus : {', '.join(sorted(unknown))}")
    if any(not 0 <= w <= 10 for w in weights.values()):
        raise ValueError("Chaque poids doit être entre 0 et 10")
    merged = {k: float(weights.get(k, default)) for k, (_, default) in CRITERIA.items()}
    if sum(merged.values()) <= 0:
        raise ValueError("Au moins un critère doit avoir un poids")
    return merged


def _check_definitions(definitions: dict[str, str]) -> dict[str, str]:
    unknown = set(definitions) - set(PERSONAL_CRITERIA)
    if unknown:
        raise ValueError(f"Critères sans définition personnelle : {', '.join(sorted(unknown))}")
    cleaned = {k: " ".join(str(v).split()) for k, v in definitions.items()}
    if any(len(v) > 300 for v in cleaned.values()):
        raise ValueError("Une définition de critère fait au plus 300 caractères")
    return {k: v for k, v in cleaned.items() if v}


class Scoring(Model):
    weights: Annotated[dict[str, float], AfterValidator(_check_weights)] = Field(
        default_factory=lambda: {k: float(w) for k, (_, w) in CRITERIA.items()}
    )
    threshold: Annotated[float, Field(ge=0, le=10)] = DEFAULT_THRESHOLD
    # Notation v2 : ce que « 1 » veut dire pour l'utilisateur, par critère personnel (vide : déduit des préférences)
    definitions: Annotated[dict[str, str], AfterValidator(_check_definitions)] = Field(default_factory=dict)


class _ProfileFacts(Model):
    headline: Annotated[str | None, Field(default=None, max_length=300), AfterValidator(_clean)]
    summary: Long
    location: Short
    years_experience: Annotated[float | None, Field(default=None, ge=0, le=60)] = None
    languages: list[Language] = Field(default_factory=list, max_length=30)
    experiences: list[Experience] = Field(default_factory=list, max_length=60)
    education: list[Education] = Field(default_factory=list, max_length=30)
    skills: list[Skill] = Field(default_factory=list, max_length=200)
    preferences: Preferences = Field(default_factory=Preferences)


class FactualProfile(_ProfileFacts):
    """Schéma de sortie de l'EXTRACTION (import PDF / texte) : uniquement des faits."""

    preferences: SearchPreferences = Field(default_factory=SearchPreferences)  # sans les réponses types
    rules: list[FactualRule] = Field(default_factory=list, max_length=50)


class ProfileData(_ProfileFacts):
    """Contenu éditable du profil : faits + déductions validées + règles apprises + barème."""

    rules: list[Rule] = Field(default_factory=list, max_length=100)
    positioning: Annotated[str | None, Field(default=None, max_length=1000), AfterValidator(_clean)]
    excluded_companies: list[ExcludedCompany] = Field(default_factory=list, max_length=100)
    reference_jobs: list[ReferenceJob] = Field(default_factory=list, max_length=50)
    scoring: Scoring = Field(default_factory=Scoring)

    @classmethod
    def from_facts(cls, facts: "FactualProfile", source: str = "import") -> "ProfileData":
        dumped = facts.model_dump()
        dumped["rules"] = [{**r, "source": source} for r in dumped["rules"]]
        return cls.model_validate(dumped)


class ProfilePatch(ProfileData):
    """PATCH /api/profile : seuls les champs envoyés sont remplacés."""


class ImportTrace(Model):
    source: str
    filename: str | None = None
    imported_at: datetime


class CriterionDef(Model):
    """Critère du barème tel que l'utilisateur le voit (libellé, et en v2 sa définition)."""

    key: str
    label: str
    personal: bool = False  # définition modifiable par l'utilisateur (v2)
    definition: str | None = None  # celle qui sert à noter (la sienne, sinon déduite des préférences)
    source: Literal["utilisateur", "preferences", "aucune"] | None = None
    hint: str | None = None


class ProfileOut(ProfileData):
    raw_imports: list[ImportTrace] = []
    updated_at: datetime | None = None
    version: int = 1
    criteria: list[CriterionDef] = []  # barème de la variante de notation en cours


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
    "positioning": "Positionnement",
    "excluded_companies": "Entreprises exclues",
    "reference_jobs": "Offres de référence",
    "scoring": "Barème",
}


# --- Lecture / écriture ------------------------------------------------------


def get_profile(db: Session, user: User) -> UserProfile:
    """Profil de l'utilisateur, créé à la volée s'il n'existe pas encore."""
    profile = db.query(UserProfile).filter_by(user_id=user.id).one_or_none()
    if profile is None:
        profile = UserProfile(
            user_id=user.id, languages=[], experiences=[], education=[], skills=[], preferences={}, rules=[],
            raw_imports=[], excluded_companies=[], reference_jobs=[], scoring={}, version=1,
        )
        db.add(profile)
        db.commit()
    return profile


def profile_data(profile: UserProfile) -> ProfileData:
    return ProfileData.model_validate({
        f: (float(v) if f == "years_experience" and v is not None else v)
        for f in DATA_FIELDS
        if (v := getattr(profile, f)) not in (None, {})  # scoring {} -> barème par défaut
    })


def profile_out(profile: UserProfile) -> ProfileOut:
    out = ProfileOut.model_validate({**profile_data(profile).model_dump(), "raw_imports": profile.raw_imports or []})
    out.updated_at = profile.updated_at
    out.version = profile.version or 1
    out.criteria = criteria_defs(profile_data(profile))
    return out


def criterion_definition(data: "ProfileData", key: str) -> tuple[str | None, str]:
    """(définition, source) d'un critère personnel : celle de l'utilisateur, sinon déduite de ses préférences."""
    if text := data.scoring.definitions.get(key):
        return text, "utilisateur"
    p = data.preferences
    if key == "stade" and p.company_stages:
        return "Stades visés : " + ", ".join(p.company_stages), "preferences"
    if key == "ia_concrete" and p.sectors:
        return "Produit dans les secteurs visés : " + ", ".join(p.sectors), "preferences"
    return None, "aucune"


def criteria_labels(variant: str | None = None) -> dict[str, str]:
    labels = {k: label for k, (label, _) in CRITERIA.items()}
    if (variant or scoring_variant()) == "v2":
        labels.update(CRITERIA_V2_LABELS)
    return labels


def criteria_defs(data: "ProfileData", variant: str | None = None) -> list[CriterionDef]:
    variant = variant or scoring_variant()
    out = []
    for key, label in criteria_labels(variant).items():
        if variant == "v2" and key in PERSONAL_CRITERIA:
            definition, source = criterion_definition(data, key)
            out.append(CriterionDef(key=key, label=label, personal=True, definition=definition, source=source,
                                    hint=DEFINITION_HINTS[key]))
        else:
            out.append(CriterionDef(key=key, label=label))
    return out


def criteria_block(data: "ProfileData") -> str:
    """Barème personnel envoyé au modèle (notation v2)."""
    lines = ["Barème personnel (critères notés de 0 à 1, poids entre parenthèses) :"]
    for c in criteria_defs(data, "v2"):
        weight = data.scoring.weights.get(c.key, 0)
        if c.personal:
            meaning = c.definition or "non défini : note 0,5 pour toutes les offres"
            lines.append(f"- {c.key} ({c.label}, poids {weight:g}) : {meaning}")
        else:
            lines.append(f"- {c.key} ({c.label}, poids {weight:g})")
    return "\n".join(lines)


def _for_version(field: str, value: Any) -> Any:
    """Valeur qui compte pour la version du profil : les réponses types des candidatures n'en font pas partie
    (les changer ne réévalue aucune offre)."""
    if field == "preferences" and isinstance(value, dict):
        return {k: v for k, v in value.items() if k != "application_defaults"}
    return value


def save_fields(profile: UserProfile, data: ProfileData, fields: set[str] | None = None) -> bool:
    """Copie les champs de `data` dans le profil (nouvelles valeurs JSON : SQLAlchemy détecte le changement).

    Date les nouvelles règles, et incrémente la version du profil si quelque chose a changé (les offres
    non traitées seront réévaluées). Retourne True si le profil a changé.
    """
    today = date.today().isoformat()
    data = data.model_copy(update={"rules": [r if r.created_at else r.model_copy(update={"created_at": today})
                                             for r in data.rules]})
    current = profile_data(profile).model_dump(mode="json")
    dumped = data.model_dump(mode="json")
    changed = False
    for f in fields if fields is not None else DATA_FIELDS:
        if _for_version(f, dumped[f]) != _for_version(f, current.get(f)):
            changed = True
        setattr(profile, f, dumped[f])
    if changed:
        profile.version = (profile.version or 1) + 1
    return changed


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
        case "excluded_companies":
            return (_norm(item.name),)
        case "reference_jobs":
            return (str(item.job_id or ""), _norm(item.company), _norm(item.title))
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
        case "excluded_companies":
            return item.name + (f" ({item.reason})" if item.reason else "")
        case "reference_jobs":
            label = " – ".join(filter(None, [item.company, item.title])) or f"poste {item.job_id}"
            return label + (f" : {item.why}" if item.why else "")
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
        if field == "scoring":  # le barème n'est jamais modifié par un import
            continue
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


def profile_summary(user: User, data: ProfileData, job_labels: dict[int, str] | None = None,
                    max_chars: int = 5000, include_name: bool = True) -> str:
    """Résumé compact du profil (contexte du chat et de l'évaluation des offres).

    job_labels : {job_id: "Entreprise – Titre"} pour citer les offres d'exemple des règles.
    include_name : False pour les traitements qui n'en ont pas besoin (notation, synthèse des règles) : le nom
    n'est pas envoyé à Gemini (minimisation des données).
    """
    job_labels = job_labels or {}
    lines = [f"Nom : {user.name or user.given_name or 'inconnu'}"] if include_name else []
    if data.headline:
        lines.append(f"Titre : {data.headline}")
    if data.positioning:
        lines.append(f"Point fort / positionnement : {data.positioning}")
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
    if p.travel:
        pref_text += f"; déplacements acceptés : {p.travel}"
    if pref_text:
        lines.append("Préférences : " + pref_text.strip("; "))
    for rule in data.rules:
        if not rule.active:
            continue
        examples = [job_labels.get(j, f"poste #{j}") for j in rule.examples[:4]]
        lines.append("Règle : " + _describe("rules", rule) + (f" (ex. {', '.join(examples)})" if examples else ""))
    if data.excluded_companies:
        lines.append("Entreprises exclues : " + ", ".join(_describe("excluded_companies", c) for c in data.excluded_companies))
    for ref in data.reference_jobs[:8]:
        lines.append("Offre de référence (aimée) : " + _describe("reference_jobs", ref))
    weights = data.scoring.weights
    labels = criteria_labels()
    lines.append("Barème : " + " · ".join(f"{labels[k]} /{w:g}" for k, w in weights.items())
                 + f" ; seuil {data.scoring.threshold:g}")
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def today_label() -> str:
    return date.today().isoformat()


def job_labels(db: Session, data: ProfileData) -> dict[int, str]:
    """{job_id: "Entreprise – Titre"} pour les offres citées par les règles (exemples)."""
    from .models import Job

    ids = {j for r in data.rules for j in r.examples}
    if not ids:
        return {}
    jobs = db.query(Job).filter(Job.id.in_(ids)).all()
    return {j.id: f"{j.company.name} – {j.title}" for j in jobs}
