"""Données de démonstration (reprises de la maquette Framer).

- seed_catalog : entreprises + postes, insérés si la table companies est vide (au démarrage).
- seed_demo_applications : quelques process pour un nouvel utilisateur (si SEED_DEMO est activé).
"""

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Application, ApplicationEvent, Company, Job, ProcessStatus

TO_CHECK = "Exemple à vérifier ; aucune vérification automatique effectuée."

COMPANIES = [
    dict(slug="dust", name="Dust", website="https://dust.tt", sector="SaaS & IA métier", city="Paris",
         funding_stage="Série A", funding_amount_meur=16, investors="Sequoia Capital, Seedcamp",
         description="Des agents IA connectés aux outils et aux connaissances des équipes.", days_ago=12),
    dict(slug="mistral-ai", name="Mistral AI", website="https://mistral.ai", sector="Infra IA & dev", city="Paris",
         funding_stage="Série B", funding_amount_meur=600, investors="General Catalyst, Lightspeed",
         description="Des modèles d'IA ouverts et une plateforme pour les déployer.", days_ago=20),
    dict(slug="nabla", name="Nabla", website="https://www.nabla.com", sector="Santé", city="Paris",
         funding_stage="Série B", funding_amount_meur=24, investors="Cathay Innovation, Zebox",
         description="Un copilote clinique qui libère les soignants des tâches administratives.", days_ago=27),
    dict(slug="adaptive-ml", name="Adaptive ML", website="https://www.adaptive-ml.com", sector="Infra IA & dev",
         city="Paris", funding_stage="Seed", funding_amount_meur=18, investors="Index Ventures, ICONIQ",
         description="Une infrastructure pour adapter les modèles aux données des entreprises.", days_ago=41),
    dict(slug="bioptimus", name="Bioptimus", website="https://www.bioptimus.com", sector="Santé", city="Paris",
         funding_stage="Seed", funding_amount_meur=32, investors="Sofinnova Partners, Bpifrance",
         description="Des modèles de fondation pour comprendre et accélérer la biologie.", days_ago=55),
    dict(slug="raiku", name="Raiku", website="https://raiku.com", sector="Infra IA & dev", city="Paris",
         funding_stage="Seed", funding_amount_meur=8, investors=None,
         description="Une plateforme de calcul distribué pour les équipes techniques.", days_ago=70),
]

COMMON_JOB = dict(
    category="Platform / MLOps",
    location="Paris · Hybride",
    seniority="6 ans",
    stack="Python, Kubernetes, Terraform, CI/CD, AWS avancé, React",
    summary="Industrialiser des produits IA, avec une responsabilité de bout en bout.",
)

JOBS = [
    ("dust", "Platform Engineer", 8.7, "Site carrières"),
    ("adaptive-ml", "ML Infrastructure Engineer", 8.2, "Welcome to the Jungle"),
    ("nabla", "MLOps Engineer", 7.9, "Site carrières"),
    ("mistral-ai", "Software Engineer, Infrastructure", 7.6, "Welcome to the Jungle"),
    ("bioptimus", "Senior Platform Engineer", 8.0, "Site carrières"),
    ("dust", "DevOps / SRE Engineer", 7.4, "Site carrières"),
    ("adaptive-ml", "Infrastructure Engineer", 7.2, "Welcome to the Jungle"),
    ("raiku", "Backend Engineer, Distributed Systems", 6.8, "Site carrières"),
]

# (entreprise, poste, statut, étape suivante, jours avant l'étape, candidature il y a N jours)
DEMO_APPLICATIONS = [
    ("bioptimus", "Senior Platform Engineer", ProcessStatus.entretien, "Entretien technique", 2, 14),
    ("dust", "DevOps / SRE Engineer", ProcessStatus.postule, "Candidature envoyée", None, 6),
    ("adaptive-ml", "Infrastructure Engineer", ProcessStatus.relance, "Relancer l'équipe recrutement", 2, 18),
]


def seed_catalog(db: Session) -> None:
    if db.scalar(select(func.count(Company.id))):
        return
    today = date.today()
    by_slug: dict[str, Company] = {}
    for data in COMPANIES:
        data = dict(data)
        days_ago = data.pop("days_ago")
        company = Company(**data, funding_announced_on=today - timedelta(days=days_ago))
        if company.investors:
            company.investors = f"{company.investors} ({TO_CHECK})"
        db.add(company)
        by_slug[company.slug] = company
    for slug, title, score, source in JOBS:
        company = by_slug[slug]
        db.add(Job(company=company, title=title, score=score, source=source, url=company.website, **COMMON_JOB))
    db.commit()


def seed_demo_applications(db: Session, user) -> None:
    today = date.today()
    for slug, title, status, next_step, next_in, applied_ago in DEMO_APPLICATIONS:
        job = db.scalar(select(Job).join(Company).where(Company.slug == slug, Job.title == title))
        if job is None:
            continue
        applied_on = today - timedelta(days=applied_ago)
        app = Application(
            user=user,
            job=job,
            status=status,
            next_step=next_step,
            next_step_date=today + timedelta(days=next_in) if next_in is not None else None,
            applied_on=applied_on,
            notes="Candidature de démonstration.",
        )
        app.events.append(ApplicationEvent(from_status=None, to_status=ProcessStatus.postule))
        if status != ProcessStatus.postule:
            app.events.append(ApplicationEvent(from_status=ProcessStatus.postule, to_status=status))
        db.add(app)
    db.commit()


# Sources de départ (docs/processus-recherche.md, §2). URL renseignée seulement quand elle est sûre :
# l'agent complète les autres. (nom, type, requête / URL, lecture automatique, active, notes)
SOURCES = [
    ("Ashby (API publique)", "ats", "https://api.ashbyhq.com/posting-api/job-board/<slug>", True, True, None),
    ("Lever (API publique)", "ats", "https://api.lever.co/v0/postings/<slug>", True, True, None),
    ("Greenhouse (API publique)", "ats", "https://boards-api.greenhouse.io/v1/boards/<slug>/jobs", True, True, None),
    ("Teamtailor", "ats", "https://<entreprise>.teamtailor.com/jobs", True, True, None),
    ("Recruitee", "ats", None, True, True, None),
    ("Welcome to the Jungle", "job_board", "https://www.welcometothejungle.com/fr/jobs", True, True, None),
    ("YC Work at a Startup", "job_board", "https://www.workatastartup.com/jobs", True, True, None),
    ("Station F (HAL)", "job_board", None, True, True, None),
    ("Licorne Society", "job_board", None, True, True, None),
    ("Bluecoders (sur WTTJ)", "job_board", None, True, True, None),
    ("Index Ventures (job board)", "vc_board", None, True, True, None),
    ("Seedcamp (job board)", "vc_board", None, True, True, None),
    ("Frst (job board)", "vc_board", None, True, True, None),
    ("Kima Ventures (job board)", "vc_board", None, True, True, None),
    ("Partech (job board)", "vc_board", None, True, True, None),
    ("Elaia (job board)", "vc_board", None, True, True, None),
    ("Serena (job board)", "vc_board", None, True, True, None),
    ("Entrepreneurs First (job board)", "vc_board", None, True, True, None),
    ("Northzone (job board)", "vc_board", None, True, True, None),
    ("SV Angel (job board)", "vc_board", None, True, True, None),
    ("Breega (job board)", "vc_board", None, True, False, "Désactivé par l'utilisateur."),
    ("Les Pépites Tech (récap hebdo)", "funding_radar", "https://lespepitestech.com", True, True,
     "Sert à découvrir des ENTREPRISES, pas des offres."),
    ("Screeningpass", "funding_radar", None, True, True, "Découverte d'entreprises."),
    ("Multeam", "funding_radar", None, True, True, "Découverte d'entreprises."),
    ("Maddyness", "funding_radar", "https://www.maddyness.com", True, True, "Découverte d'entreprises."),
    ("Sifted", "funding_radar", "https://sifted.eu", True, True, "Découverte d'entreprises."),
    ("Flaire (pages Notion)", "recruiter", None, False, True,
     "Pages Notion publiques : lecture automatique impossible (JavaScript), à faire à la main."),
    ("Google : Ashby platform / founding Paris", "google_query",
     'site:jobs.ashbyhq.com ("platform engineer" OR "founding engineer") Paris', True, True, None),
    ("Google : member of technical staff Paris", "google_query", '"member of technical staff" Paris', True, True, None),
]


def seed_sources(db: Session) -> None:
    from .models import Source

    if db.scalar(select(func.count(Source.id))):
        return
    for name, kind, query, auto, active, notes in SOURCES:
        db.add(Source(name=name, kind=kind, query=query, auto_readable=auto, active=active, notes=notes,
                      added_by="user", jobs_found=0))
    db.commit()
