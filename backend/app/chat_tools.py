"""Outils de l'assistant (function calling Gemini).

- Lectures : exécutées directement, résultat compact renvoyé au modèle.
- Écritures : jamais exécutées par le modèle. L'outil valide la demande, calcule ce qui va changer et
  enregistre une PROPOSITION (chat_actions, status = pending). L'action n'est exécutée qu'après
  POST /api/chat/actions/{id}/confirm, en appelant les mêmes fonctions métier que l'API (services.py).
- Tous les outils sont limités à l'utilisateur courant (son id vient de la session, jamais du modèle).
- Aucun outil de suppression des entreprises ou des postes partagés.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import services
from .llm import ToolSpec, to_json
from .models import STATUS_LABELS, Application, ChatAction, Company, Job, ProcessStatus, User
from .profile import ProfileData, get_profile, profile_data, save_fields
from .schemas import ApplicationPatch, JobIn
from .services import ServiceError

log = logging.getLogger("mire.chat.actions")

ACTION_TTL = timedelta(days=1)
STATUS_KEYS = [s.value for s in ProcessStatus]
STATUS_HELP = ", ".join(f"{s.value} ({label})" for s, label in STATUS_LABELS.items())
PREF_LISTS = ("roles", "locations", "work_modes", "company_stages", "clouds_preferred", "clouds_avoided", "sectors")


class ToolError(Exception):
    """Demande invalide : le message est renvoyé au modèle pour qu'il corrige ou explique."""


def _short(text: str | None, n: int = 300) -> str | None:
    if not text:
        return text
    return text if len(text) <= n else text[: n - 1] + "…"


def _status_label(status: ProcessStatus | str) -> str:
    return STATUS_LABELS[ProcessStatus(status)]


# --- Schémas des outils (JSON Schema) -------------------------------------------

_str = {"type": "string"}
_str_list = {"type": "array", "items": {"type": "string"}}
_status = {"type": "string", "enum": STATUS_KEYS, "description": f"Statut : {STATUS_HELP}"}
_date = {"type": "string", "description": "Date au format AAAA-MM-JJ"}

TOOLS: list[ToolSpec] = [
    # Lectures
    ToolSpec("get_profile", "Lit le profil complet de l'utilisateur (identité, expériences, compétences, préférences, règles).",
             {"type": "object", "properties": {}}),
    ToolSpec("list_jobs", "Liste les postes suivis (partagés), avec le statut de l'utilisateur pour chacun, triés par note décroissante.",
             {"type": "object", "properties": {
                 "status": {**_status, "enum": [*STATUS_KEYS, "ecartee"],
                            "description": f"Statut affiché : {STATUS_HELP}, ou ecartee (écartée par l'évaluation)"},
                 "company": {"type": "string", "description": "Filtre sur le nom de l'entreprise (partiel)"},
                 "min_score": {"type": "number", "description": "Note minimale (0 à 10)"},
                 "limit": {"type": "integer", "description": "Nombre maximum de résultats (défaut 30)"},
             }}),
    ToolSpec("get_job", "Détail d'un poste (description, stack…) et du process de l'utilisateur pour ce poste.",
             {"type": "object", "properties": {"job_id": {"type": "integer"}}, "required": ["job_id"]}),
    ToolSpec("list_applications", "Liste les process de candidature de l'utilisateur (hors postes « nouveau »).",
             {"type": "object", "properties": {"status": _status}}),
    ToolSpec("get_dashboard", "Tableau de bord : compteurs, postes prioritaires, process actifs, levées de fonds récentes.",
             {"type": "object", "properties": {}}),
    # Écritures (propositions à confirmer)
    ToolSpec(
        "update_profile",
        "PROPOSE une modification partielle du profil (exécutée seulement après confirmation de l'utilisateur). "
        "Ne renseigne que les champs à changer.",
        {"type": "object", "properties": {
            "add_skills": {"type": "array", "items": {"type": "object", "properties": {
                "name": _str, "level": _str,
                "kind": {"type": "string", "enum": ["fort", "a_developper"], "description": "fort (défaut) ou a_developper"},
            }, "required": ["name"]}},
            "remove_skills": {**_str_list, "description": "Noms des compétences à retirer"},
            "add_rules": {"type": "array", "items": {"type": "object", "properties": {
                "kind": {"type": "string", "enum": ["eviter", "preferer"]}, "text": _str,
            }, "required": ["kind", "text"]}},
            "remove_rules": {**_str_list, "description": "Texte (ou extrait) des règles à retirer"},
            "headline": _str, "location": _str, "summary": _str,
            "years_experience": {"type": "number"},
            "min_salary_keur": {"type": "integer", "description": "Salaire minimum en k€ brut annuel"},
            "preferences_add": {"type": "object", "description": "Valeurs à ajouter aux listes de préférences",
                                "properties": {k: _str_list for k in PREF_LISTS}},
            "preferences_remove": {"type": "object", "description": "Valeurs à retirer des listes de préférences",
                                   "properties": {k: _str_list for k in PREF_LISTS}},
        }},
    ),
    ToolSpec(
        "set_job_status",
        "PROPOSE de changer le statut d'un poste pour l'utilisateur (crée le process si besoin). Exécuté après confirmation.",
        {"type": "object", "properties": {
            "job_id": {"type": "integer"}, "status": _status,
            "note": {"type": "string", "description": "Note facultative sur ce changement"},
        }, "required": ["job_id", "status"]},
    ),
    ToolSpec(
        "update_application",
        "PROPOSE de mettre à jour un process existant : notes, prochaine étape, date de la prochaine étape. "
        "Pour changer le statut, utiliser set_job_status. Exécuté après confirmation.",
        {"type": "object", "properties": {
            "application_id": {"type": "integer"},
            "job_id": {"type": "integer", "description": "Alternative à application_id"},
            "next_step": _str,
            "next_step_date": _date,
            "append_note": {"type": "string", "description": "Texte ajouté à la fin des notes (recommandé)"},
            "notes": {"type": "string", "description": "Remplace TOUTES les notes (seulement si demandé explicitement)"},
        }},
    ),
    ToolSpec(
        "create_job",
        "PROPOSE d'ajouter un poste au radar (et l'entreprise si elle n'existe pas encore). Exécuté après confirmation.",
        {"type": "object", "properties": {
            "company_name": _str, "title": _str, "url": _str, "location": _str, "seniority": _str,
            "category": _str, "stack": _str, "summary": _str, "source": _str,
            "score": {"type": "number", "description": "Note d'intérêt de 0 à 10"},
        }, "required": ["company_name", "title"]},
    ),
]

READ_TOOLS = {"get_profile", "list_jobs", "get_job", "list_applications", "get_dashboard"}
WRITE_TOOLS = {"update_profile", "set_job_status", "update_application", "create_job"}


# --- Lectures ---------------------------------------------------------------


def _compact(data: dict) -> dict:
    return {k: v for k, v in data.items() if v not in (None, "", [], {})}


def _job_row(job) -> dict:
    return _compact({
        "job_id": job.id, "title": job.title, "company": job.company.name, "score": job.score,
        "status": job.display_status, "location": job.location, "stack": _short(job.stack, 120),
        "pourquoi": _short(job.assessment.why, 200) if job.assessment else None,
        "raison_ecart": job.assessment.exclusion if job.assessment and job.display_status == "ecartee" else None,
    })


def _app_row(app) -> dict:
    return _compact({
        "application_id": app.id, "job_id": app.job.id, "title": app.job.title, "company": app.company.name,
        "status": app.status.value, "next_step": app.next_step, "next_step_date": app.next_step_date,
        "applied_on": app.applied_on, "notes": _short(app.notes, 200),
    })


def _read(db: Session, user: User, name: str, args: dict) -> Any:
    match name:
        case "get_profile":
            return _compact(profile_data(get_profile(db, user)).model_dump(exclude_none=True))
        case "list_jobs":
            status = args.get("status") or None  # statut affiché : statut posé, ou verdict « ecartee »
            if status and status not in (*STATUS_KEYS, "ecartee"):
                raise ToolError(f"Statut inconnu : {status}")
            limit = max(1, min(int(args.get("limit") or 30), 100))
            jobs = services.list_jobs(db, user, company_name=args.get("company"), status=status,
                                      min_score=args.get("min_score"))
            return {"total": len(jobs), "jobs": [_job_row(j) for j in jobs[:limit]]}
        case "get_job":
            job = services.get_job(db, user, int(args["job_id"]))
            out = _job_row(job) | _compact({
                "url": job.url, "seniority": job.seniority, "category": job.category, "source": job.source,
                "stack": job.stack, "summary": _short(job.summary, 4000),
            })
            if job.application_id:
                out["application"] = _app_row(services.load_application(db, job.application_id, user))
            return out
        case "list_applications":
            status = ProcessStatus(args["status"]) if args.get("status") else None
            return {"applications": [_app_row(a) for a in services.list_applications(db, user, status)]}
        case "get_dashboard":
            d = services.dashboard(db, user)
            return {
                "stats": d.stats.model_dump(),
                "priority_jobs": [_job_row(j) for j in d.priority_jobs],
                "processes": [_app_row(a) for a in d.processes],
                "recent_fundings": [_compact({"company": c.name, "stage": c.funding_stage,
                                              "amount_meur": c.funding_amount_meur,
                                              "announced_on": c.funding_announced_on}) for c in d.recent_fundings],
            }
    raise ToolError(f"Outil inconnu : {name}")


# --- Écritures : préparation (proposition) et exécution ------------------------


@dataclass
class Plan:
    summary: str
    changes: list[dict]  # [{label, before, after}]
    run: Callable[[], dict]  # exécute l'action (après confirmation) et renvoie un résultat JSON
    refresh: tuple[str, ...]  # données à rafraîchir côté front


def _norm(v: str) -> str:
    return " ".join(v.lower().split())


def _plan_update_profile(db: Session, user: User, args: dict) -> Plan:
    profile = get_profile(db, user)
    current = profile_data(profile)
    data = current.model_dump()
    changes: list[dict] = []

    skills = data["skills"]
    names = {_norm(s["name"]) for s in skills}
    for s in args.get("add_skills") or []:
        if s.get("name") and _norm(s["name"]) not in names:
            skills.append({"name": s["name"], "level": s.get("level"), "kind": s.get("kind") or "fort"})
            names.add(_norm(s["name"]))
            kind = "à développer" if s.get("kind") == "a_developper" else "point fort"
            changes.append({"label": "Compétence ajoutée", "before": None, "after": f"{s['name']} ({kind})"})
    for name in args.get("remove_skills") or []:
        kept = [s for s in skills if _norm(s["name"]) != _norm(name)]
        if len(kept) < len(skills):
            changes.append({"label": "Compétence retirée", "before": name, "after": None})
        skills = kept
    data["skills"] = skills

    rules = data["rules"]
    for r in args.get("add_rules") or []:
        if r.get("text") and _norm(r["text"]) not in {_norm(x["text"]) for x in rules}:
            rules.append({"kind": r.get("kind") or "eviter", "text": r["text"], "source": "chat"})
            label = "Règle ajoutée (éviter)" if r.get("kind") != "preferer" else "Règle ajoutée (préférer)"
            changes.append({"label": label, "before": None, "after": r["text"]})
    for text in args.get("remove_rules") or []:
        removed = [x for x in rules if _norm(text) in _norm(x["text"])]
        if len(removed) > 1:
            raise ToolError(f"Plusieurs règles correspondent à « {text} » : précise le texte exact.")
        for x in removed:
            changes.append({"label": "Règle retirée", "before": x["text"], "after": None})
        rules = [x for x in rules if x not in removed]
    data["rules"] = rules

    for field, label in (("headline", "Titre"), ("location", "Localisation"), ("summary", "Résumé"),
                         ("years_experience", "Années d'expérience")):
        if field in args and args[field] is not None and args[field] != data[field]:
            changes.append({"label": label, "before": _short(str(data[field])) if data[field] is not None else None,
                            "after": _short(str(args[field]))})
            data[field] = args[field]

    prefs = data["preferences"]
    if args.get("min_salary_keur") is not None and args["min_salary_keur"] != prefs["min_salary_keur"]:
        changes.append({"label": "Salaire minimum (k€)", "before": prefs["min_salary_keur"], "after": args["min_salary_keur"]})
        prefs["min_salary_keur"] = args["min_salary_keur"]
    for key in PREF_LISTS:
        for value in (args.get("preferences_add") or {}).get(key) or []:
            if _norm(value) not in {_norm(v) for v in prefs[key]}:
                prefs[key] = [*prefs[key], value]
                changes.append({"label": f"Préférence ajoutée ({key})", "before": None, "after": value})
        for value in (args.get("preferences_remove") or {}).get(key) or []:
            kept = [v for v in prefs[key] if _norm(v) != _norm(value)]
            if len(kept) < len(prefs[key]):
                changes.append({"label": f"Préférence retirée ({key})", "before": value, "after": None})
            prefs[key] = kept

    if not changes:
        raise ToolError("Aucun changement : le profil contient déjà ces informations (ou les éléments à retirer n'existent pas).")
    try:
        new = ProfileData.model_validate(data)
    except ValidationError as exc:
        raise ToolError(f"Valeurs invalides : {exc.errors()[0]['msg']}") from None

    def run() -> dict:
        # Le plan est recalculé sur le profil actuel au moment de la confirmation (voir confirm_action)
        save_fields(profile, new)
        db.commit()
        return {"ok": True}

    return Plan(summary="Mettre à jour votre profil", changes=changes, run=run, refresh=("profile",))


def _plan_set_job_status(db: Session, user: User, args: dict) -> Plan:
    try:
        status = ProcessStatus(args.get("status"))
    except ValueError:
        raise ToolError(f"Statut inconnu. Valeurs possibles : {STATUS_HELP}") from None
    job = services.get_job(db, user, int(args["job_id"]))
    if job.status == status:
        raise ToolError(f"Le poste est déjà au statut « {_status_label(status)} ».")
    note = (args.get("note") or "").strip()[:1000] or None
    changes = [{"label": "Statut", "before": _status_label(job.status), "after": _status_label(status)}]
    if status == ProcessStatus.postule:
        changes.append({"label": "Date de candidature", "before": None, "after": "aujourd'hui (si non renseignée)"})
    if note:
        changes.append({"label": "Note", "before": None, "after": note})

    def run() -> dict:
        out = services.set_job_status(db, user, job.id, status, note)
        return {"job_id": out.id, "status": out.status.value, "application_id": out.application_id}

    return Plan(summary=f"{job.title} · {job.company.name}", changes=changes, run=run,
                refresh=("jobs", "applications", "dashboard"))


def _find_application(db: Session, user: User, args: dict) -> Application:
    if args.get("application_id"):
        return services.load_application(db, int(args["application_id"]), user)
    if args.get("job_id"):
        app = services.user_apps(db, user).get(int(args["job_id"]))
        if app is None:
            raise ToolError("Aucun process pour ce poste. Utilise d'abord set_job_status pour en créer un.")
        return app
    raise ToolError("Indique application_id ou job_id.")


def _plan_update_application(db: Session, user: User, args: dict) -> Plan:
    app = _find_application(db, user, args)
    patch: dict = {}
    changes: list[dict] = []
    if args.get("next_step") is not None and args["next_step"] != app.next_step:
        patch["next_step"] = args["next_step"]
        changes.append({"label": "Prochaine étape", "before": app.next_step, "after": args["next_step"]})
    if args.get("next_step_date"):
        try:
            when = date.fromisoformat(args["next_step_date"])
        except ValueError:
            raise ToolError("next_step_date doit être au format AAAA-MM-JJ") from None
        if when != app.next_step_date:
            patch["next_step_date"] = when
            changes.append({"label": "Date de la prochaine étape",
                            "before": app.next_step_date.isoformat() if app.next_step_date else None,
                            "after": when.isoformat()})
    if args.get("notes") is not None:
        patch["notes"] = args["notes"]
        changes.append({"label": "Notes (remplacées)", "before": _short(app.notes), "after": _short(args["notes"])})
    elif args.get("append_note"):
        line = f"{date.today().isoformat()} : {args['append_note'].strip()}"
        patch["notes"] = f"{app.notes.rstrip()}\n{line}" if app.notes else line
        changes.append({"label": "Note ajoutée", "before": None, "after": line})
    if not changes:
        raise ToolError("Aucun changement à proposer pour ce process.")
    try:
        body = ApplicationPatch.model_validate(patch)
    except ValidationError as exc:
        raise ToolError(f"Valeurs invalides : {exc.errors()[0]['msg']}") from None
    app_id = app.id

    def run() -> dict:
        out = services.update_application(db, user, app_id, body)
        return {"application_id": out.id, "status": out.status.value}

    return Plan(summary=f"{app.job.title} · {app.company.name}", changes=changes, run=run,
                refresh=("applications", "jobs", "dashboard"))


def _plan_create_job(db: Session, user: User, args: dict) -> Plan:
    company_name = " ".join((args.get("company_name") or "").split())
    if not company_name:
        raise ToolError("Le nom de l'entreprise est obligatoire.")
    company = db.scalar(select(Company).where(func.lower(Company.name) == company_name.lower()))
    fields = {k: args.get(k) for k in ("title", "url", "location", "seniority", "category", "stack", "summary",
                                         "source", "score")}
    fields["source"] = fields["source"] or "Assistant Mire"
    try:
        body = JobIn.model_validate({**fields, "company_id": company.id if company else 0})
    except ValidationError as exc:
        err = exc.errors()[0]
        raise ToolError(f"Champ {err['loc'][-1]} invalide : {err['msg']}") from None
    if company:
        dup = db.scalar(select(Job).where(Job.company_id == company.id, func.lower(Job.title) == body.title.lower()))
        if dup:
            raise ToolError(f"Ce poste existe déjà (job_id {dup.id}).")
    changes = [{"label": "Entreprise", "before": None,
                "after": company.name if company else f"{company_name} (nouvelle entreprise)"}]
    labels = {"title": "Intitulé", "url": "Lien", "location": "Lieu", "seniority": "Séniorité", "category": "Catégorie",
              "stack": "Stack", "summary": "Résumé", "score": "Note"}
    changes += [{"label": label, "before": None, "after": _short(str(v), 200)}
                for k, label in labels.items() if (v := getattr(body, k)) not in (None, "")]
    company_id = company.id if company else None

    def run() -> dict:
        cid = company_id
        if cid is None:
            existing = db.scalar(select(Company).where(func.lower(Company.name) == company_name.lower()))
            if existing is None:
                existing = Company(name=company_name, slug=services.slugify(db, company_name))
                db.add(existing)
                db.flush()
            cid = existing.id
        out = services.create_job(db, body.model_copy(update={"company_id": cid}))
        return {"job_id": out.id, "company_id": cid}

    return Plan(summary=f"Nouveau poste : {body.title} · {company.name if company else company_name}",
                changes=changes, run=run, refresh=("jobs", "companies", "dashboard"))


PLANNERS: dict[str, Callable[[Session, User, dict], Plan]] = {
    "update_profile": _plan_update_profile,
    "set_job_status": _plan_set_job_status,
    "update_application": _plan_update_application,
    "create_job": _plan_create_job,
}


# --- Point d'entrée des appels d'outils du modèle ----------------------------------


class ToolRunner:
    """Exécute les appels d'outils d'un tour de conversation pour l'utilisateur courant."""

    def __init__(self, db: Session, user: User):
        self.db = db
        self.user = user
        self.actions: list[ChatAction] = []  # propositions créées pendant ce tour

    def __call__(self, name: str, args: dict) -> dict:
        try:
            if name in READ_TOOLS:
                return {"data": to_json(_read(self.db, self.user, name, args))}
            if name in WRITE_TOOLS:
                return self._propose(name, args)
            return {"error": f"Outil inconnu : {name}"}
        except (ToolError, ServiceError) as exc:
            return {"error": exc.detail if isinstance(exc, ServiceError) else str(exc)}
        except (KeyError, TypeError, ValueError) as exc:
            log.info("Outil %s : arguments invalides %s (%s)", name, args, exc)
            return {"error": f"Arguments invalides pour {name}."}

    def _propose(self, name: str, args: dict) -> dict:
        plan = PLANNERS[name](self.db, self.user, args)  # valide et calcule l'aperçu, sans rien modifier
        action = ChatAction(
            user_id=self.user.id, tool=name, args=to_json(args), summary=plan.summary,
            changes=to_json(plan.changes), status="pending",
        )
        self.db.add(action)
        self.db.flush()
        self.actions.append(action)
        return {
            "status": "proposition_en_attente",
            "action_id": action.id,
            "resume": plan.summary,
            "changements": action.changes,
            "important": "Rien n'est encore modifié : l'utilisateur doit cliquer sur « Confirmer » dans la carte affichée.",
        }


# --- Confirmation / annulation ----------------------------------------------------


def _load_action(db: Session, user: User, action_id: int) -> ChatAction:
    action = db.scalar(select(ChatAction).where(ChatAction.id == action_id, ChatAction.user_id == user.id))
    if action is None:
        raise ServiceError(404, "Action introuvable")
    if action.status != "pending":
        raise ServiceError(409, "Cette action a déjà été traitée")
    created = action.created_at if action.created_at.tzinfo else action.created_at.replace(tzinfo=UTC)
    if created < datetime.now(UTC) - ACTION_TTL:
        action.status = "expired"
        db.commit()
        raise ServiceError(410, "Proposition expirée : redemandez-la à l'assistant")
    return action


def confirm_action(db: Session, user: User, action_id: int) -> tuple[ChatAction, tuple[str, ...]]:
    """Exécute une proposition. Le plan est recalculé sur l'état actuel des données (qui a pu changer
    depuis la proposition), avec les mêmes contrôles ; s'il n'est plus applicable, l'action échoue."""
    action = _load_action(db, user, action_id)
    try:
        plan = PLANNERS[action.tool](db, user, dict(action.args))
        result = plan.run()
    except (ToolError, ServiceError) as exc:
        db.rollback()
        action = db.get(ChatAction, action_id)
        action.status = "failed"
        action.error = exc.detail if isinstance(exc, ServiceError) else str(exc)
        db.commit()
        log.info("Action %s (%s) de l'utilisateur %s refusée : %s", action.id, action.tool, user.id, action.error)
        raise ServiceError(409, f"Action impossible : {action.error}") from None
    action = db.get(ChatAction, action_id)
    action.status = "confirmed"
    action.result = to_json(result)
    action.executed_at = datetime.now(UTC)
    db.commit()
    log.info("Action %s exécutée : utilisateur %s, %s %s -> %s", action.id, user.id, action.tool, action.args, result)
    return action, plan.refresh


def cancel_action(db: Session, user: User, action_id: int) -> ChatAction:
    action = _load_action(db, user, action_id)
    action.status = "cancelled"
    db.commit()
    return action
