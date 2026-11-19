"""Collecte quotidienne des offres (processus 2), faite par Mire lui-même, sans LLM.

Pour chaque entreprise du référentiel :
1. ATS inconnu : détection automatique (URL carrières / site, identifiants probables), au plus une fois par
   semaine par entreprise ;
2. ATS lisible (Ashby, Lever, Greenhouse, Recruitee) : lecture de la liste actuelle des offres ;
   offres connues -> ouverture vérifiée (et champs vides complétés), absentes -> fermées ;
   nouvelles offres -> ajoutées avec leur description complète (publiées il y a plus de 2 mois : ignorées,
   doublons : jamais recréés).
Chaque collecte est un passage (search_runs, kind « collecte ») ; les nouvelles offres sont ensuite évaluées
pour chaque utilisateur par la tâche de fond (processus 3).

Planification : tous les jours à COLLECT_TIME (heure de Paris, défaut 06:30) ; au démarrage, rattrapage si la
dernière collecte date de plus de 20 h. Désactivable : COLLECTOR=false.
"""

import logging
import os
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ats
from .models import Company, Job, SearchRun
from .services import find_duplicate

log = logging.getLogger("mire.collector")

MAX_AGE_DAYS = 61
PROBE_EVERY = timedelta(days=7)


def posting_to_job(company: Company, p: ats.Posting, now: datetime) -> Job:
    return Job(
        company_id=company.id, title=p.title[:200], url=(p.url or "")[:1000] or None,
        location=(p.location or "")[:200] or None, ats_job_id=p.ats_job_id, published_on=p.published_on,
        summary=p.description, work_mode=p.work_mode, salary=(p.salary or "")[:200] or None,
        category=(p.department or "")[:200] or None, verified_open_at=now, source=f"ATS {company.ats}",
    )


def collect_company(db: Session, company: Company, fetch=ats.fetch_postings, detect=ats.detect_ats,
                    now: datetime | None = None) -> dict:
    """Collecte les offres d'une entreprise. Retourne des compteurs (sans commit final)."""
    now = now or datetime.now(UTC)
    stats = {"company_id": company.id, "detected": None, "added": [], "closed": 0, "verified": 0,
             "too_old": 0, "duplicates": 0, "error": None}
    if company.ats not in ats.SUPPORTED or not company.ats_slug:
        probed = company.ats_probed_at
        if probed is not None and probed.tzinfo is None:
            probed = probed.replace(tzinfo=UTC)
        if company.ats and company.ats not in ats.SUPPORTED:
            stats["error"] = f"ATS {company.ats} : lecture automatique impossible"
            return stats
        if probed is not None and now - probed < PROBE_EVERY:
            stats["error"] = "ATS inconnu (détection déjà tentée cette semaine)"
            return stats
        company.ats_probed_at = now
        found = detect(company, fetch)
        if not found:
            stats["error"] = "ATS introuvable automatiquement"
            return stats
        company.ats, company.ats_slug = found
        stats["detected"] = f"{found[0]}/{found[1]}"
    result = ats.check_company(db, company, fetch)
    if result.error:
        stats["error"] = result.error
        return stats
    stats["closed"], stats["verified"] = len(result.closed), len(result.verified)
    for p in result.new_postings:
        if p.published_on and (now.date() - p.published_on).days > MAX_AGE_DAYS:
            stats["too_old"] += 1
            continue
        if find_duplicate(db, company.id, p.title, p.url):
            stats["duplicates"] += 1
            continue
        job = posting_to_job(company, p, now)
        db.add(job)
        db.flush()
        stats["added"].append(job.id)
    return stats


def collect_all(db: Session, fetch=ats.fetch_postings, detect=ats.detect_ats) -> SearchRun:
    """Collecte pour tout le référentiel ; enregistre et retourne le passage."""
    run = SearchRun(kind="collecte", status="en_cours", details={}, jobs_seen=0, jobs_added=0, jobs_duplicates=0,
                    jobs_closed=0, companies_checked=0, sources_added=0)
    db.add(run)
    db.commit()
    added: list[int] = []
    errors: list[str] = []
    detected: list[str] = []
    try:
        for company in db.scalars(select(Company).order_by(Company.name)).all():
            try:
                s = collect_company(db, company, fetch, detect)
                db.commit()
            except Exception as exc:  # une entreprise en échec ne bloque pas les autres
                db.rollback()
                log.warning("Collecte %s : %s", company.name, exc)
                errors.append(f"{company.name} : erreur inattendue")
                continue
            run.companies_checked += 1
            run.jobs_seen += s["verified"] + len(s["added"]) + s["too_old"] + s["duplicates"]
            run.jobs_added += len(s["added"])
            run.jobs_duplicates += s["duplicates"]
            run.jobs_closed += s["closed"]
            added += s["added"]
            if s["detected"]:
                detected.append(f"{company.name} : {s['detected']}")
            if s["error"]:
                errors.append(f"{company.name} : {s['error']}")
        run.status = "termine"
    except Exception as exc:
        run.status = "echec"
        errors.append(f"Collecte interrompue : {exc}")
        raise
    finally:
        run.finished_at = datetime.now(UTC)
        run.details = {"jobs_added": added, "ats_detected": detected, "errors": errors}
        run.summary = (f"Collecte : {run.companies_checked} entreprises, {run.jobs_added} offres ajoutées, "
                       f"{run.jobs_closed} fermées" + (f", ATS détectés : {len(detected)}" if detected else "")
                       + (f", {len(errors)} entreprises sans lecture automatique" if errors else ""))
        db.commit()
    return run


# --- Planification -----------------------------------------------------------------------------


def _paris():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Europe/Paris")
    except Exception:  # base de fuseaux absente
        return UTC


def next_run_at(now: datetime, at: str) -> datetime:
    """Prochaine occurrence de l'heure `at` (HH:MM, heure de Paris), en UTC."""
    tz = _paris()
    hour, minute = (int(x) for x in at.split(":"))
    local = now.astimezone(tz)
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return target.astimezone(UTC)


class DailyCollector:
    def __init__(self, session_factory, on_done=None, at: str | None = None):
        self.session_factory = session_factory
        self.on_done = on_done
        self.at = at or os.environ.get("COLLECT_TIME", "06:30")
        self.event = threading.Event()
        self.stop_event = threading.Event()
        self.running = False
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, name="mire-collector", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.event.set()

    def trigger(self) -> bool:
        """Collecte immédiate (bouton « Collecter maintenant »). False si une collecte est déjà en cours."""
        if self.running:
            return False
        self.event.set()
        return True

    def _due_at_startup(self) -> bool:
        with self.session_factory() as db:
            # Collecte interrompue (redémarrage pendant le passage) : marquée en échec
            for run in db.scalars(select(SearchRun).where(SearchRun.kind == "collecte", SearchRun.status == "en_cours")):
                run.status, run.finished_at = "echec", datetime.now(UTC)
                run.summary = (run.summary or "") + "Collecte interrompue (redémarrage de Mire)."
            db.commit()
            last = db.scalar(select(SearchRun.started_at).where(SearchRun.kind == "collecte", SearchRun.status == "termine")
                             .order_by(SearchRun.id.desc()).limit(1))
        if last is None:
            return True
        last = last if last.tzinfo else last.replace(tzinfo=UTC)
        return datetime.now(UTC) - last > timedelta(hours=20)

    def run_now(self) -> None:
        self.running = True
        try:
            with self.session_factory() as db:
                run = collect_all(db)
                log.info("%s", run.summary)
            if self.on_done:
                self.on_done()
        except Exception as exc:
            log.warning("Collecte en échec : %s", exc)
        finally:
            self.running = False

    def _loop(self) -> None:
        if self._due_at_startup():  # PC éteint à l'heure prévue : rattrapage
            self.run_now()
        while not self.stop_event.is_set():
            wait = (next_run_at(datetime.now(UTC), self.at) - datetime.now(UTC)).total_seconds()
            self.event.wait(max(1.0, wait))
            self.event.clear()
            if self.stop_event.is_set():
                return
            self.run_now()


collector: DailyCollector | None = None
