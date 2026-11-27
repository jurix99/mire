"""Classement des offres et des entreprises POUR UN UTILISATEUR : calculé à la lecture, sans appel LLM.

C'est l'étage de reclassement : il rassemble tout ce qu'on sait, au lieu de ne trier que sur la note Gemini.

pertinence (0-100) = adéquation + goût + fraîcheur
- adéquation : critères comparables (métier visé, compétences exigées couvertes, niveau, lieu et télétravail,
  salaire, secteur, stade) et proximité sémantique (embeddings calculés par le worker) ; mêlée à la note de Gemini
  quand l'offre est évaluée ;
- goût : J'aime / Pas intéressé sur les offres et les entreprises (feedback.py) ; effet immédiat, réversible ;
- fraîcheur : petit bonus aux offres récentes.
Règle : une information absente (de l'offre ou du profil) ne pénalise pas, le critère n'est simplement pas compté.

`hidden` : pourquoi l'offre n'est pas proposée (écartée par l'utilisateur, exclusion dure, écartée par
l'évaluation, fermée) ; une offre aimée n'est jamais masquée par une exclusion automatique.

Sert aussi à choisir les offres envoyées à Gemini (matching.py, assessments.assess_user).

Offres Free : la visibilité (entitlements.rank_jobs) NE dépend PAS de ce classement ni des retours, sinon écarter
des offres en dévoilerait d'autres.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import taxonomy as tx
from .assessments import LANGUAGE_SKILLS, TECH_HINT, hard_exclusion, profile_skill_ids
from .classify import FAMILY_RULES, SECTOR_RULES, norm
from .models import (
    Application,
    Company,
    CompanyFeedback,
    Job,
    JobAssessment,
    JobFeedback,
    JobSkill,
    ProcessStatus,
    User,
    UserProfile,
)
from .profile import ProfileData, get_profile, profile_data

# --- Réglages -------------------------------------------------------------------------------------------------

PRIOR = 2.0  # poids d'un « 0,5 » ajouté à la moyenne : avec peu de critères connus, on reste prudent
LLM_SHARE = 0.6  # part de la note Gemini dans l'adéquation d'une offre évaluée
UNASSESSED_FACTOR = 0.9  # sans note Gemini, l'adéquation reste un peu en retrait
TASTE_MAX = 12.0  # points ajoutés ou retirés par les retours (critères)
TASTE_SCALE = 30.0  # proximité moyenne des critères (-1 à 1) -> points
TASTE_SEMANTIC_MAX = 10.0  # idem, proximité des offres aimées / écartées
SEMANTIC_PATIENCE = 3  # la proximité sémantique ne pèse pleinement qu'avec plusieurs retours : n / (n + 3)
COMPANY_LIKE_BOOST = 10.0
SMOOTHING = 1.5  # un seul retour ne suffit pas à trancher
# Critères assez parlants pour être cités (« Proche de vos J'aime : … ») ; niveau, mode et stade comptent sans
EXPLAINED = ("family:", "skill:", "sector:", "company:")

WEIGHTS = {"family": 4.0, "skills": 3.0, "semantic": 2.0, "location": 2.0, "level": 1.5, "salary": 1.0,
           "sector": 0.75, "stage": 0.75}

# Familles proches : une offre « fullstack » reste intéressante pour qui vise « backend »
ADJACENT = {
    "backend": {"fullstack", "platform_devops_sre", "data"},
    "fullstack": {"backend", "front_mobile"},
    "front_mobile": {"fullstack"},
    "platform_devops_sre": {"backend", "data"},
    "data": {"ml_ia", "backend", "platform_devops_sre"},
    "ml_ia": {"data"},
    "sales": {"partenariats", "customer_success_account_management"},
    "partenariats": {"sales"},
    "customer_success_account_management": {"sales"},
    "growth_acquisition": {"marketing_produit_contenu"},
    "marketing_produit_contenu": {"growth_acquisition", "brand_communication"},
    "brand_communication": {"marketing_produit_contenu"},
    "founders_associate_chief_of_staff": {"ops_business_ops", "founding_toutes_fonctions"},
    "ops_business_ops": {"founders_associate_chief_of_staff"},
}

LEVELS = {"junior": 0, "confirme": 1, "senior": 2, "lead": 3}
LEVEL_LABEL = {"junior": "Junior", "confirme": "Confirmé", "senior": "Senior", "lead": "Lead"}
MODE_LABEL = {"sur_site": "Sur site", "hybride": "Hybride", "remote": "Remote"}
LATE_STAGES = ("serie c", "serie d", "serie e", "serie f", "growth", "pre-ipo", "cotee")

# « Pas intéressé » avec une raison : le reproche porte sur ces critères, pas sur tout le profil de l'offre
REASON_FEATURES = {
    "trop_front": ["family:front_mobile", "skill:react", "skill:typescript_javascript", "skill:vue", "skill:nextjs",
                   "skill:react_native_flutter"],
    "trop_sql": ["family:data", "skill:sql", "skill:dbt"],
    "trop_ia": ["family:ml_ia", "skill:llm", "skill:deep_learning"],
    "trop_senior": ["level:senior", "level:lead"],
    "trop_grosse": ["stage:late"],
}

# Qui cherche à Paris accepte en pratique la proche couronne et les pôles d'Île-de-France
PARIS_AREA = ("ile-de-france", "ile de france", "boulogne", "montreuil", "issy", "nanterre", "la defense",
              "courbevoie", "puteaux", "levallois", "saint-denis", "saint-ouen", "ivry", "montrouge", "malakoff",
              "clichy", "neuilly", "vincennes", "pantin", "villejuif", "massy", "saclay", "palaiseau", "orsay",
              "gif-sur-yvette", "kremlin", "bagneux", "arcueil", "gentilly")

# « Candidature spontanée » : pas un poste à pourvoir
SPONTANEOUS = re.compile(r"candidatures? spontanee|spontaneous application|open application|talent pool|vivier")
SPONTANEOUS_PENALTY = 25.0

DISLIKED_JOB = "Vous l'avez écartée"
DISLIKED_COMPANY = "Entreprise que vous avez écartée"


# --- Outils ---------------------------------------------------------------------------------------------------


def city_of(location: str | None) -> str | None:
    """Ville principale d'un lieu libre : « Hybrid Paris », « Paris, France; London » -> « Paris »."""
    if not location:
        return None
    first = location.split(";")[0].split("|")[0].split("/")[0].strip()
    lowered = norm(first)
    if lowered.startswith("anywhere in"):
        return first.split(" ", 2)[2].split(",")[0].strip() or None
    city = first.split(",")[0].split("(")[0].strip()
    for prefix in ("hybrid ", "hybride ", "remote ", "on-site ", "onsite "):
        if city.lower().startswith(prefix):
            city = city[len(prefix):]
    for suffix in (" office", " hq"):
        if city.lower().endswith(suffix):
            city = city[: -len(suffix)]
    return city.strip(" -–") or None


def target_families(data: ProfileData) -> set[str]:
    """Familles de postes visées, d'après les rôles du profil (libellés de la liste ou texte libre), à défaut
    d'après le titre professionnel."""
    by_label = {norm(label): fid for fid, label in tx.FAMILY_LABEL.items() if fid != tx.OTHER}

    def match(text: str) -> set[str]:
        n = norm(text)
        if fid := by_label.get(n) or (n if n in tx.FAMILY_LABEL and n != tx.OTHER else None):
            return {fid}
        return {fid for fid, rx in FAMILY_RULES if rx is not None and rx.search(n)}

    found: set[str] = set()
    for role in data.preferences.roles:
        found |= match(role)
    if not found and data.headline:
        found = match(data.headline)
    return found


def target_sectors(data: ProfileData) -> set[str]:
    by_label = {norm(label): sid for sid, label in tx.SECTOR_LABEL.items() if sid != tx.OTHER}
    found: set[str] = set()
    for text in data.preferences.sectors:
        n = norm(text)
        if n in by_label:
            found.add(by_label[n])
        else:
            found |= {sid for sid, rx in SECTOR_RULES if rx is not None and rx.search(n)}
    return found


def company_sector(company: Company) -> str | None:
    n = norm(f"{company.sector or ''} {company.description or ''}")
    return next((sid for sid, rx in SECTOR_RULES if rx is not None and rx.search(n)), None)


def stage_key(stage: str | None) -> str | None:
    n = norm(stage).strip()
    return n or None


def _my_level(years: float | None) -> int | None:
    if years is None:
        return None
    return 0 if years < 2 else 1 if years < 5 else 2 if years < 8 else 3


def _percentiles(values: dict[int, float]) -> dict[int, float]:
    """Rang de chaque valeur entre 0 et 1 : les cosinus bruts ne sont pas comparables d'un profil à l'autre."""
    if len(values) < 2:
        return dict.fromkeys(values, 0.5)
    ordered = sorted(values, key=lambda k: values[k])
    return {k: i / (len(ordered) - 1) for i, k in enumerate(ordered)}


def _mean(vectors: list[list[float]]) -> list[float]:
    return [sum(column) / len(vectors) for column in zip(*vectors, strict=True)]


def _join(items: list[str], limit: int = 3) -> str:
    return ", ".join(list(dict.fromkeys(items))[:limit])


# --- Résultats ------------------------------------------------------------------------------------------------


@dataclass
class Match:
    score: float  # 0-100, non arrondi (tri)
    reasons: list[str] = field(default_factory=list)  # ce qui joue pour
    warnings: list[str] = field(default_factory=list)  # ce qui joue contre
    hidden: str | None = None  # pourquoi l'offre n'est pas proposée

    @property
    def value(self) -> int:
        return round(self.score)


@dataclass
class CompanyMatch(Match):
    open_jobs: int = 0
    top: list[tuple[Job, "Match"]] = field(default_factory=list)  # meilleures offres ouvertes proposées


# --- Classement ------------------------------------------------------------------------------------------------


class Ranker:
    """Contexte de classement d'un utilisateur, chargé une fois puis appliqué à autant d'offres qu'on veut."""

    def __init__(self, db: Session, user: User):
        profile = get_profile(db, user)
        self.data = data = profile_data(profile)
        self.families = target_families(data)
        self.sectors = target_sectors(data)
        self.stages = {k for s in data.preferences.company_stages if (k := stage_key(s))}
        self.skills = profile_skill_ids(data)
        self.level = _my_level(data.years_experience)
        self.locations = [norm(x) for x in (data.preferences.locations or ([data.location] if data.location else []))]
        self.locations = [x.split(",")[0].strip() for x in self.locations if x.strip()]
        self.modes = set(data.preferences.work_modes)
        self.liked_refs = {r.job_id for r in data.reference_jobs if r.job_id}

        self.job_skills: dict[int, list[str]] = {}
        for job_id, sid in db.execute(select(JobSkill.job_id, JobSkill.skill_id).where(JobSkill.required.is_(True))):
            if sid not in LANGUAGE_SKILLS:
                self.job_skills.setdefault(job_id, []).append(sid)
        self.assessments = {a.job_id: a for a in db.scalars(
            select(JobAssessment).where(JobAssessment.user_id == user.id))}
        self.status = dict(db.execute(select(Application.job_id, Application.status)
                                      .where(Application.user_id == user.id)).all())
        # Dernier retour par offre ; une offre « pas intéressé » sans retour (statut posé à la main) compte aussi
        self.reactions: dict[int, tuple[str, list[str]]] = {}
        for job_id, status in self.status.items():
            if status == ProcessStatus.pas_interesse:
                self.reactions[job_id] = ("dislike", [])
        for fb in db.scalars(select(JobFeedback).where(JobFeedback.user_id == user.id).order_by(JobFeedback.id)):
            self.reactions[fb.job_id] = (fb.sentiment, list(fb.reasons or []))
        self.liked_refs -= {j for j, (s, _) in self.reactions.items() if s == "dislike"}
        for job_id in self.liked_refs:  # offre de référence ajoutée à la main ou avant les retours
            self.reactions.setdefault(job_id, ("like", []))
        self.company_reactions: dict[int, str] = dict(db.execute(
            select(CompanyFeedback.company_id, CompanyFeedback.sentiment)
            .where(CompanyFeedback.user_id == user.id)).all())

        self._learn(db)
        self._semantic(db, profile)

    # --- Goût (retours) -------------------------------------------------------------------------------------

    def job_features(self, job: Job) -> list[tuple[str, float, str | None]]:
        """(critère, importance, libellé) d'une offre, pour comparer aux offres aimées et écartées."""
        out = []
        if job.role_family and job.role_family != tx.OTHER:
            out.append((f"family:{job.role_family}", 1.0, tx.FAMILY_LABEL.get(job.role_family)))
        skills = self.job_skills.get(job.id, [])
        out += [(f"skill:{s}", 1.0 / max(3, len(skills)), tx.SKILL_LABEL.get(s)) for s in skills]
        if job.seniority_level in LEVELS:
            out.append((f"level:{job.seniority_level}", 0.2, f"niveau {LEVEL_LABEL[job.seniority_level].lower()}"))
        if job.work_mode:
            out.append((f"mode:{job.work_mode}", 0.15, MODE_LABEL.get(job.work_mode)))
        out.append((f"company:{job.company_id}", 0.8, job.company.name))
        return out + self._company_traits(job.company, job.product_sector)

    @staticmethod
    def _company_traits(company: Company, sector: str | None = None) -> list[tuple[str, float, str | None]]:
        out = []
        sector = sector if sector and sector != tx.OTHER else company_sector(company)
        if sector:
            out.append((f"sector:{sector}", 0.4, f"secteur {tx.SECTOR_LABEL.get(sector, sector)}"))
        if stage := stage_key(company.funding_stage):
            out.append((f"stage:{stage}", 0.25, company.funding_stage))
            if stage.startswith(LATE_STAGES):
                out.append(("stage:late", 0.25, "entreprise déjà grande"))
        return out

    def _learn(self, db: Session) -> None:
        """Poids appris de chaque critère, entre -1 et 1 : (aimés - écartés) / (aimés + écartés + lissage)."""
        pos: dict[str, float] = {}
        neg: dict[str, float] = {}
        jobs = db.scalars(select(Job).where(Job.id.in_(self.reactions or [-1]))).all()
        for job in jobs:
            sentiment, reasons = self.reactions[job.id]
            features = [key for key, _, _ in self.job_features(job)]
            if sentiment == "like":
                for key in features:
                    pos[key] = pos.get(key, 0) + 1
                continue
            targeted = {k for r in reasons for k in REASON_FEATURES.get(r, [])}
            for key in features:
                # Raison précise : le reste de l'offre n'est que légèrement visé
                weight = 1.0 if key in targeted else (0.3 if reasons else 1.0)
                neg[key] = neg.get(key, 0) + weight
            for key in targeted - set(features):
                neg[key] = neg.get(key, 0) + 0.5
        for company in db.scalars(select(Company).where(Company.id.in_(self.company_reactions or [-1]))):
            liked = self.company_reactions[company.id] == "like"
            bucket = pos if liked else neg
            for key, _, _ in [(f"company:{company.id}", 0, None), *self._company_traits(company)]:
                bucket[key] = bucket.get(key, 0) + (1.0 if liked else 0.5)
        self.taste = {k: (pos.get(k, 0) - neg.get(k, 0)) / (pos.get(k, 0) + neg.get(k, 0) + SMOOTHING)
                      for k in pos.keys() | neg.keys()}

    def _affinity(self, features) -> tuple[float, list[str], list[str]]:
        """(points, critères qui plaisent, critères qui déplaisent) d'après les retours."""
        total = sum(importance for _, importance, _ in features)
        if not total or not self.taste:
            return 0.0, [], []
        contributions = [(importance * self.taste.get(key, 0.0), key, label) for key, importance, label in features]
        raw = sum(c for c, _, _ in contributions) / total
        points = max(-TASTE_MAX, min(TASTE_MAX, raw * TASTE_SCALE))
        explained = [(c, label) for c, key, label in contributions if label and key.startswith(EXPLAINED)]
        plus = [label for c, label in sorted(explained, key=lambda x: -x[0]) if c > 0.05]
        minus = [label for c, label in sorted(explained, key=lambda x: x[0]) if c < -0.05]
        return points, plus, minus

    # --- Proximité sémantique (embeddings stockés ; aucun modèle chargé ici) --------------------------------

    def _semantic(self, db: Session, profile: UserProfile) -> None:
        from . import embeddings

        self.sem_profile: dict[int, float] = {}
        self.sem_taste: dict[int, float] = {}
        vector = db.scalar(select(UserProfile.embedding).where(UserProfile.id == profile.id))
        if vector is not None:
            self.sem_profile = _percentiles(dict(embeddings.nearest_jobs(db, vector, limit=None)))
        if not self.reactions:
            return
        stored = dict(db.execute(select(Job.id, Job.embedding)
                                 .where(Job.id.in_(self.reactions), Job.embedding.is_not(None))).all())
        raw: dict[int, float] = {}
        used = 0
        for sentiment, sign in (("like", 1), ("dislike", -1)):
            vectors = [[float(x) for x in v] for j, v in stored.items() if self.reactions[j][0] == sentiment]
            if not vectors:
                continue
            used += 1
            for job_id, sim in embeddings.nearest_jobs(db, _mean(vectors), limit=None):
                raw[job_id] = raw.get(job_id, 0.0) + sign * sim
        if used:
            confidence = len(stored) / (len(stored) + SEMANTIC_PATIENCE)
            self.sem_taste = {j: (p - 0.5) * 2 * confidence for j, p in _percentiles(raw).items()}

    # --- Adéquation au profil -------------------------------------------------------------------------------

    def _signals(self, job: Job) -> list[tuple[str, float, str | None, str | None]]:
        """(critère, valeur de 0 à 1, atout affiché, réserve affichée) : seulement les critères connus."""
        out: list[tuple[str, float, str | None, str | None]] = []
        family = job.role_family
        if self.families and family and family != tx.OTHER:
            label = tx.FAMILY_LABEL.get(family, family)
            if family in self.families:
                out.append(("family", 1.0, f"Métier visé : {label}", None))
            elif any(family in ADJACENT.get(f, ()) for f in self.families) or (
                    family == "founding_toutes_fonctions" and TECH_HINT.search(job.title or "")
                    and any(tx.FAMILY_DOMAIN.get(f) == "tech" for f in self.families)):
                out.append(("family", 0.6, None, None))
            else:
                out.append(("family", 0.05, None, f"Hors de vos métiers visés ({label})"))
        elif self.families:
            out.append(("family", 0.25, None, None))  # métier non reconnu : probablement pas le cœur de cible

        required = self.job_skills.get(job.id, [])
        if required and self.skills:
            have = [s for s in required if self.skills.get(s) == "fort"]
            growing = [s for s in required if self.skills.get(s) == "a_developper"]
            missing = [s for s in required if s not in self.skills]
            value = (len(have) + 0.5 * len(growing)) / len(required)
            plus = _join([tx.SKILL_LABEL.get(s, s) for s in have]) if have else None
            minus = f"À acquérir : {_join([tx.SKILL_LABEL.get(s, s) for s in missing], 2)}" if missing else None
            out.append(("skills", value, plus, minus if value < 0.5 else None))

        if self.level is not None and job.seniority_level in LEVELS:
            gap = LEVELS[job.seniority_level] - self.level
            if gap == 0:
                out.append(("level", 1.0, None, None))
            elif gap == 1:
                out.append(("level", 0.5, None, "Demande un peu plus d'expérience"))
            elif gap > 1:
                out.append(("level", 0.05, None, "Demande nettement plus d'expérience"))
            else:
                out.append(("level", 0.8 if gap == -1 else 0.4, None, "En dessous de votre niveau" if gap < -1 else None))

        if location := self._location(job):
            out.append(location)

        minimum = self.data.preferences.min_salary_keur
        offered = job.salary_max_keur or job.salary_min_keur
        if minimum and offered:
            if offered >= minimum:
                out.append(("salary", 1.0, f"Salaire jusqu'à {offered} k€", None))
            else:
                out.append(("salary", 0.5 if offered >= 0.9 * minimum else 0.0, None,
                            f"Salaire affiché sous vos {minimum} k€"))

        sector = job.product_sector if job.product_sector not in (None, tx.OTHER) else company_sector(job.company)
        if self.sectors and sector:
            wanted = sector in self.sectors
            out.append(("sector", 1.0 if wanted else 0.3,
                        f"Secteur {tx.SECTOR_LABEL.get(sector, sector)}" if wanted else None, None))
        stage = stage_key(job.company.funding_stage)
        if self.stages and stage:
            wanted = any(stage.startswith(s) or s.startswith(stage) for s in self.stages)
            out.append(("stage", 1.0 if wanted else 0.3, job.company.funding_stage if wanted else None, None))

        if job.id in self.sem_profile:
            out.append(("semantic", self.sem_profile[job.id], None, None))
        return out

    def _location(self, job: Job) -> tuple[str, float, str | None, str | None] | None:
        mode, text = job.work_mode, norm(job.location)
        mode_ok = not self.modes or not mode or mode in self.modes
        if mode == "remote" and mode_ok:
            return ("location", 1.0, "Télétravail", None)
        if not self.locations:
            if self.modes and mode:
                return ("location", 1.0 if mode_ok else 0.2, None, None if mode_ok else f"{MODE_LABEL[mode]} seulement")
            return None
        if not text:
            return None
        here = any(loc in text for loc in self.locations)
        if not here and "paris" in self.locations and any(area in text for area in PARIS_AREA):
            here = True
        if here:
            if mode_ok:
                return ("location", 1.0, city_of(job.location), None)
            return ("location", 0.5, None, f"{MODE_LABEL.get(mode, mode)} à {city_of(job.location)}")
        if "anywhere in france" in text or text.strip() == "france":
            return ("location", 0.6, None, None)
        return ("location", 0.05, None, f"Lieu : {job.location}")

    # --- Offre ----------------------------------------------------------------------------------------------

    def _hidden(self, job: Job, liked: bool) -> str | None:
        reaction = self.reactions.get(job.id)
        if reaction and reaction[0] == "dislike":
            return DISLIKED_JOB
        if self.company_reactions.get(job.company_id) == "dislike":
            return DISLIKED_COMPANY
        if job.closed_at is not None:
            return "Offre fermée"
        if liked:
            return None
        # Recalculée ici : un changement de profil s'applique tout de suite, sans attendre une réévaluation
        if reason := hard_exclusion(job, self.data):
            return reason
        a = self.assessments.get(job.id)
        if a is not None and a.model != "regles" and a.verdict == "ecartee" and a.manual_score is None:
            return a.exclusion or "Écartée par l'analyse"
        return None

    def _llm_score(self, job: Job) -> float | None:
        a = self.assessments.get(job.id)
        if a is not None:
            value = a.manual_score if a.manual_score is not None else a.score
            if value is not None:
                return float(value) / 10
            if a.model != "regles":
                return None
        return float(job.score) / 10 if job.score is not None else None  # ancienne note partagée

    def match(self, job: Job) -> Match:
        signals = self._signals(job)
        total = sum(WEIGHTS[name] for name, *_ in signals)
        content = (sum(WEIGHTS[name] * value for name, value, *_ in signals) + 0.5 * PRIOR) / (total + PRIOR)
        llm = self._llm_score(job)
        fit = LLM_SHARE * llm + (1 - LLM_SHARE) * content if llm is not None else UNASSESSED_FACTOR * content

        liked = self.reactions.get(job.id, ("",))[0] == "like"
        points, plus, minus = self._affinity(self.job_features(job))
        points += self.sem_taste.get(job.id, 0.0) * TASTE_SEMANTIC_MAX
        if liked:  # un J'aime remonte les offres qui ressemblent ; il ne gonfle pas la sienne (elle se ressemble)
            points, plus, minus = 0.0, [], []
        if self.company_reactions.get(job.company_id) == "like":
            points += COMPANY_LIKE_BOOST
        age = (datetime.now(UTC).date() - (job.published_on or _created_day(job))).days
        fresh = 4 if age <= 7 else 1 if age <= 21 else -4 if age > 60 else 0

        reasons, warnings = [], []
        if self.company_reactions.get(job.company_id) == "like":
            reasons.append("Entreprise que vous aimez")
        elif points > 3 and plus and not liked:
            reasons.append(f"Proche de vos J'aime : {_join(plus, 2)}")
        if points < -3 and minus:
            warnings.append(f"Ressemble à ce que vous écartez : {_join(minus, 2)}")
        if SPONTANEOUS.search(norm(job.title)):
            points -= SPONTANEOUS_PENALTY
            warnings.insert(0, "Candidature spontanée, pas un poste ouvert")
        ordered = sorted(signals, key=lambda s: -WEIGHTS[s[0]])
        reasons += [text for _, value, text, _ in ordered if text and value >= 0.5]
        warnings += [text for _, value, _, text in ordered if text and value <= 0.5]

        score = max(0.0, min(99.0, fit * 100 + points + fresh))  # jamais « parfait »
        return Match(score, reasons[:3], warnings[:2], self._hidden(job, liked))

    def order(self, jobs: list[Job]) -> list[Job]:
        """Les offres les plus prometteuses d'abord (à égalité : les plus récentes)."""
        scores = {j.id: self.match(j).score for j in jobs}
        return sorted(jobs, key=lambda j: (-scores[j.id], -j.id))

    # --- Entreprise -----------------------------------------------------------------------------------------

    def company_match(self, company: Company, matches: list[tuple[Job, Match]]) -> CompanyMatch:
        """matches : (offre, classement) des offres de l'entreprise visibles par l'utilisateur."""
        open_jobs = [(j, m) for j, m in matches if j.closed_at is None]
        proposed = sorted((pair for pair in open_jobs if pair[1].hidden is None), key=lambda p: -p[1].score)
        liked = self.company_reactions.get(company.id) == "like"
        hidden = None
        if self.company_reactions.get(company.id) == "dislike":
            hidden = DISLIKED_COMPANY
        elif any((ex.company_id == company.id) or norm(ex.name) == norm(company.name)
                 for ex in self.data.excluded_companies):
            hidden = "Entreprise exclue dans votre profil"

        traits = self._company_traits(company)
        points, plus, minus = self._affinity([(f"company:{company.id}", 0.8, company.name), *traits])
        reasons, warnings = [], []
        if proposed:
            good = sum(1 for _, m in proposed if m.score >= 60)
            score = proposed[0][1].score + min(6, 2 * max(0, good - 1))
            if good:
                reasons.append(f"{good} offre{'s' if good > 1 else ''} pertinente{'s' if good > 1 else ''}")
            reasons += proposed[0][1].reasons
            warnings = proposed[0][1].warnings
        else:
            sector = company_sector(company)
            wanted = bool(sector and sector in self.sectors)
            score = max(0.0, min(40.0, 20 + (15 if wanted else 0) + points))
            if wanted:
                reasons.append(f"Secteur {tx.SECTOR_LABEL.get(sector, sector)}")
            if plus:
                reasons.append(f"Proche de vos J'aime : {_join(plus, 2)}")
            warnings.append("Aucune offre pertinente ouverte" if open_jobs else "Pas d'offre ouverte")
        if liked:
            score += COMPANY_LIKE_BOOST
        return CompanyMatch(max(0.0, min(99.0, score)), list(dict.fromkeys(reasons))[:3], warnings[:2], hidden,
                            open_jobs=len(open_jobs), top=proposed[:3])


def _created_day(job: Job) -> date:
    return job.created_at.date() if job.created_at else datetime.now(UTC).date()
