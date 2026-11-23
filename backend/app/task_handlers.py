"""Handlers des tâches de fond (voir tasks.py). Chaque handler reçoit (db, payload) ; une exception = échec
(nouvel essai avec délai). Importé par tasks.run_worker, jamais par l'API au démarrage.

collect        collecte sur les ATS (collector.collect_all), puis embed_jobs
check_company  collecte d'une seule entreprise {company_id} (ex. ATS renseigné par l'agent), puis embed_jobs
embed_jobs     embeddings des offres nouvelles ou modifiées, puis match_user pour chaque utilisateur
match_user     présélection {user_id} (exclusions dures + similarité), puis assess_user
assess_user    évaluation Gemini {user_id, job_ids?, similarity?}
classify_jobs  classement sur la liste fermée des offres en attente (CLASSIFY_MAX_PER_RUN), relancée tant
               qu'il en reste ; sur quota Gemini, reprise 30 min plus tard
daily          enchaînement complet (comme `python -m app.jobs daily`)
"""

import logging

from sqlalchemy.orm import Session

from . import assessments, collector, matching, tasks
from .llm import get_llm
from .models import Company, User

log = logging.getLogger("mire.tasks")


@tasks.handler("collect")
def collect(db: Session, payload: dict) -> None:
    run = collector.collect_all(db)
    log.info("%s", run.summary)
    if run.jobs_added or run.jobs_closed:
        tasks.jobs_changed(db)
    else:
        from . import classify

        if classify.count_pending(db):  # stock pas encore classé (ex. après une migration de la liste)
            tasks.enqueue(db, "classify_jobs", dedup_key="classify_jobs")


@tasks.handler("check_company")
def check_company(db: Session, payload: dict) -> None:
    company = db.get(Company, int(payload["company_id"]))
    if company is None:
        return
    stats = collector.collect_company(db, company)
    db.commit()
    if stats["added"]:
        tasks.jobs_changed(db)


@tasks.handler("embed_jobs")
def embed_jobs(db: Session, payload: dict) -> None:
    from . import embeddings

    embeddings.embed_pending_jobs(db)
    matching.enqueue_matches(db)


@tasks.handler("match_user")
def match_user(db: Session, payload: dict) -> None:
    user = db.get(User, int(payload["user_id"]))
    if user is None:
        return
    candidates = matching.match_user(db, user)
    if assessments.assessor is not None:
        # En local, la tâche de fond historique évalue toutes les offres en attente : on la réveille
        # (deux évaluations en parallèle pour le même utilisateur se marcheraient dessus)
        assessments.wake()
        return
    if candidates["job_ids"]:
        scope = assessments.scope_of(db, user)
        tasks.enqueue(db, "assess_user", {"user_id": user.id, **candidates},
                      delay=matching.FREE_DELAY if scope.priority > 0 else None)


@tasks.handler("assess_user")
def assess_user(db: Session, payload: dict) -> None:
    llm = get_llm()
    user = db.get(User, int(payload["user_id"]))
    if llm is None or user is None:
        return
    if assessments.assessor is not None:
        assessments.wake()
        return
    if payload.get("job_ids") is None:
        stats = assessments.assess_within_plan(db, user, llm, matching.MATCH_TOP_K)
    else:
        stats = matching.assess_candidates(db, user, llm, payload)
    log.info("Évaluation utilisateur %s : %s", user.id, stats)


@tasks.handler("classify_jobs")
def classify_jobs(db: Session, payload: dict) -> None:
    from . import classify
    from .jobs import CLASSIFY_RETRY_DELAY

    stats = classify.classify_pending_jobs(db, get_llm())
    if stats["remaining"]:
        tasks.enqueue(db, "classify_jobs", dedup_key="classify_jobs",
                      delay=CLASSIFY_RETRY_DELAY if stats["stopped"] else None)
    elif stats["classified"]:
        # Tout est classé : faits du marché à jour (en local, il n'y a pas de job daily pour le faire)
        from . import market

        market.refresh_facts(db)


@tasks.handler("daily")
def daily(db: Session, payload: dict) -> None:
    from . import jobs

    jobs.daily(db)
