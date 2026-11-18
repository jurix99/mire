"""API de l'évaluation des offres (/api/assessments, /api/jobs/{id}/assessment)."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import assessments, services
from .db import get_db
from .llm import LLM, get_llm, require_llm
from .models import Application, Job, JobAssessment, User
from .profile import get_profile, profile_data
from .schemas import JobOut, ManualScoreIn
from .users import current_user

router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])

DB = Annotated[Session, Depends(get_db)]
Me = Annotated[User, Depends(current_user)]
Gemini = Annotated[LLM | None, Depends(get_llm)]


class AssessmentStatus(BaseModel):
    enabled: bool  # évaluation automatique possible (Gemini configuré)
    profile_ready: bool  # profil assez rempli pour évaluer
    pending: int  # offres à (ré)évaluer
    kept: int
    excluded: int
    last_error: str | None = None


class RunResult(BaseModel):
    evaluated: int
    excluded: int
    kept: int
    remaining: int


@router.get("/assessments/status")
def assessment_status(db: DB, user: Me, llm: Gemini) -> AssessmentStatus:
    profile = get_profile(db, user)
    rows = list(db.scalars(select(JobAssessment.verdict).where(JobAssessment.user_id == user.id)))
    worker = assessments.assessor
    return AssessmentStatus(
        enabled=llm is not None,
        profile_ready=assessments.profile_is_usable(profile_data(profile)),
        pending=len(assessments.jobs_to_assess(db, user, profile)),
        kept=rows.count("nouveau"), excluded=rows.count("ecartee"),
        last_error=worker.last_error if worker else None,
    )


@router.post("/assessments/run")
def run_assessments(db: DB, user: Me, llm: Gemini) -> RunResult:
    """Évalue tout de suite jusqu'à 10 offres en attente (sinon la tâche de fond s'en charge)."""
    stats = assessments.assess_user(db, user, require_llm(llm), limit=10)
    assessments.wake()
    return RunResult(**stats)


@router.put("/jobs/{job_id}/assessment/manual")
def set_manual_score(job_id: int, body: ManualScoreIn, db: DB, user: Me) -> JobOut:
    """Correction de la note par l'utilisateur : conservée, jamais écrasée par une réévaluation."""
    services.get_or_404(db, Job, job_id)
    a = db.scalar(select(JobAssessment).where(JobAssessment.user_id == user.id, JobAssessment.job_id == job_id))
    if a is None:
        a = JobAssessment(user_id=user.id, job_id=job_id, criteria={}, skills_have=[], skills_missing=[],
                          verdict="nouveau", profile_version=get_profile(db, user).version,
                          assessed_at=datetime.now(UTC), model="manuel")
        db.add(a)
    a.manual_score = body.score
    db.commit()
    return services.get_job(db, user, job_id)


def invalidate_job(db: Session, job_id: int) -> None:
    """Le contenu d'une offre a changé : réévaluer chez les utilisateurs qui ne l'ont pas traitée
    et n'ont pas corrigé la note (règle d'or)."""
    treated = select(Application.user_id).where(Application.job_id == job_id)
    db.execute(
        update(JobAssessment)
        .where(JobAssessment.job_id == job_id, JobAssessment.manual_score.is_(None),
               JobAssessment.user_id.not_in(treated))
        .values(profile_version=0)
    )
