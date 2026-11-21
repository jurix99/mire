"""File de tâches de fond dans PostgreSQL (table tasks), sans dépendance de file (ni Redis, ni Celery).

- enqueue(db, kind, payload, dedup_key) : ajoute une tâche ; si une tâche EN ATTENTE porte déjà la même
  dedup_key (ex. « match_user:12 »), aucune n'est ajoutée (index unique partiel sur status = 'queued').
- Un worker prend une tâche avec SELECT … FOR UPDATE SKIP LOCKED (plusieurs workers en parallèle ne prennent
  jamais la même), l'exécute avec le handler enregistré pour son type, puis la marque done.
- Échec : nouvel essai avec un délai croissant (1 min, puis 4 min), au plus MAX_ATTEMPTS tentatives -> failed.
- Tâche restée running plus de STALE_AFTER (worker tué, timeout du job) : remise en file.

Production : le job Container Apps « mire-worker » est déclenché par KEDA quand des tâches sont en attente
(SELECT count(*) FROM tasks WHERE status = 'queued' AND run_after <= now()) et lance run_worker() jusqu'à
ce que la file soit vide. En local (RUN_BACKGROUND_THREADS, défaut), un thread LocalWorker fait de même.

Les handlers sont enregistrés par @handler("type") dans task_handlers.py (importé à la demande : l'API n'en a
besoin que pour mettre en file).
"""

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import Task

log = logging.getLogger("mire.tasks")

MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=30)
RETRY_BASE = timedelta(minutes=1)  # 1 min, 4 min (x4 à chaque tentative)

Handler = Callable[[Session, dict], None]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    """Enregistre la fonction (db, payload) qui exécute les tâches de ce type."""

    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return register


def now() -> datetime:
    return datetime.now(UTC)


def enqueue(db: Session, kind: str, payload: dict | None = None, dedup_key: str | None = None,
            delay: timedelta | None = None) -> Task | None:
    """Met une tâche en file et VALIDE la transaction (à appeler après les écritures métier).

    Retourne la tâche, ou None si une tâche en attente porte déjà cette dedup_key.
    delay : exécution différée (ex. utilisateurs Free servis après les Pro).
    """
    if dedup_key and db.scalar(select(Task.id).where(Task.dedup_key == dedup_key, Task.status == "queued")):
        return None
    task = Task(kind=kind, payload=payload or {}, status="queued", attempts=0, dedup_key=dedup_key,
                run_after=now() + (delay or timedelta()))
    db.add(task)
    try:
        db.commit()
    except IntegrityError:  # course avec un autre enqueue de la même clé : la tâche existe déjà
        db.rollback()
        return None
    _wake_local()
    return task


# --- Prise et exécution ------------------------------------------------------------------------


def requeue_stale(db: Session) -> int:
    """Remet en file les tâches running depuis plus de STALE_AFTER (ou les marque failed si épuisées)."""
    limit = now() - STALE_AFTER
    failed = db.execute(
        update(Task).where(Task.status == "running", Task.locked_at < limit, Task.attempts >= MAX_ATTEMPTS)
        .values(status="failed", locked_at=None, error="Interrompue (plus de 30 min) après la dernière tentative")
    ).rowcount
    requeued = db.execute(
        update(Task).where(Task.status == "running", Task.locked_at < limit)
        .values(status="queued", locked_at=None, run_after=now(), error="Interrompue (plus de 30 min) : remise en file")
    ).rowcount
    db.commit()
    if requeued or failed:
        log.warning("Tâches bloquées : %s remises en file, %s abandonnées", requeued, failed)
    return requeued


def claim(db: Session) -> Task | None:
    """Prend la prochaine tâche due (SKIP LOCKED : ignorée par SQLite, utilisé seulement en tests)."""
    task = db.scalars(
        select(Task).where(Task.status == "queued", Task.run_after <= now())
        .order_by(Task.run_after, Task.id).limit(1).with_for_update(skip_locked=True)
    ).first()
    if task is None:
        db.rollback()
        return None
    task.status, task.locked_at, task.attempts = "running", now(), task.attempts + 1
    db.commit()
    return task


def retry_delay(attempts: int) -> timedelta:
    return RETRY_BASE * (4 ** max(0, attempts - 1))


def execute(session_factory, task_id: int) -> bool:
    """Exécute une tâche déjà prise (running). True si elle a réussi."""
    with session_factory() as db:
        task = db.get(Task, task_id)
        kind, payload = task.kind, dict(task.payload or {})
    fn = HANDLERS.get(kind)
    started = time.monotonic()
    error = None
    if fn is None:
        error = f"Type de tâche inconnu : {kind}"
    else:
        try:
            with session_factory() as db:
                fn(db, payload)
        except Exception as exc:  # noqa: BLE001  (toute erreur est consignée sur la tâche)
            error = f"{type(exc).__name__}: {exc}"[:2000]
    with session_factory() as db:
        task = db.get(Task, task_id)
        task.locked_at = None
        if error is None:
            task.status, task.error = "done", None
        elif task.attempts >= MAX_ATTEMPTS or fn is None:
            task.status, task.error = "failed", error
        else:
            task.status, task.error = "queued", error
            task.run_after = now() + retry_delay(task.attempts)
        status, attempts = task.status, task.attempts
        db.commit()
    log.info("Tâche %s (%s) : %s", task_id, kind, status,
             extra={"task_id": task_id, "kind": kind, "status": status, "attempts": attempts,
                    "duration_s": round(time.monotonic() - started, 2)})
    if error:
        log.warning("Tâche %s (%s) en échec (tentative %s) : %s", task_id, kind, attempts, error)
    return error is None


def run_worker(until_empty: bool = True, max_seconds: float | None = None, session_factory=None,
               idle_wait: float = 5.0) -> dict:
    """Traite la file. until_empty : s'arrête quand plus aucune tâche n'est due ; max_seconds : budget de temps
    (aucune nouvelle tâche n'est prise au-delà). Retourne {done, failed}."""
    from . import task_handlers  # noqa: F401  (enregistre les handlers)

    if session_factory is None:
        from .db import SessionLocal as session_factory
    deadline = time.monotonic() + max_seconds if max_seconds else None
    stats = {"done": 0, "failed": 0}
    with session_factory() as db:
        requeue_stale(db)
    while deadline is None or time.monotonic() < deadline:
        with session_factory() as db:
            task = claim(db)
            task_id = task.id if task else None
        if task_id is None:
            if until_empty:
                break
            time.sleep(idle_wait)
            continue
        stats["done" if execute(session_factory, task_id) else "failed"] += 1
    return stats


# --- Worker local (docker compose : un thread dans le processus de l'API) ---------------------


class LocalWorker:
    """Thread qui vide la file dès qu'une tâche est ajoutée (enqueue le réveille), sinon toutes les 15 s."""

    def __init__(self, session_factory, interval: float = 15.0):
        self.session_factory = session_factory
        self.interval = interval
        self.event = threading.Event()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, name="mire-tasks", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.event.set()

    def wake(self) -> None:
        self.event.set()

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                run_worker(until_empty=True, session_factory=self.session_factory)
            except Exception as exc:  # base indisponible… : on réessaie au prochain tour
                log.warning("Worker local : %s", exc)
            self.event.wait(self.interval)
            self.event.clear()


local_worker: LocalWorker | None = None


def _wake_local() -> None:
    if local_worker is not None:
        local_worker.wake()


# --- Raccourcis utilisés par l'API ---------------------------------------------------------------


def jobs_changed(db: Session) -> None:
    """Offres ajoutées ou modifiées : embeddings, puis présélection et évaluation pour chaque utilisateur ;
    classement sur la liste fermée et faits du marché (analytics)."""
    enqueue(db, "embed_jobs", dedup_key="embed_jobs")
    enqueue(db, "classify_jobs", dedup_key="classify_jobs")
    _wake_assessor()


def profile_changed(db: Session, user_id: int) -> None:
    """Profil modifié (version incrémentée) ou nouveau J'aime : nouvelle présélection pour l'utilisateur."""
    enqueue(db, "match_user", {"user_id": user_id}, dedup_key=f"match_user:{user_id}")
    _wake_assessor()


def _wake_assessor() -> None:
    # En local, la tâche de fond d'évaluation historique (assessments.Assessor) tourne aussi : réveillée
    # comme avant. En production (RUN_BACKGROUND_THREADS=false) elle n'existe pas : sans effet.
    from . import assessments

    assessments.wake()
