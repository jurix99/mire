"""Embeddings des offres et des profils, pour la présélection par similarité (matching.py) et les offres
similaires (GET /api/jobs/{id}/similar).

Modèle : sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 via fastembed (ONNX, sur CPU, sans torch).
- multilingue (~50 langues, dont français et anglais : les offres sont dans les deux) ;
- léger : 0,22 Go, 384 dimensions (models.EMBEDDING_DIM) ;
- pas de préfixe attendu (contrairement à la famille e5 : « query: » / « passage: ») ;
- textes tronqués à 128 jetons : tout texte plus long est découpé en morceaux dont les vecteurs sont moyennés
  (sinon seuls le titre et la présentation de l'entreprise comptaient, jamais les exigences du poste).
Le modèle est téléchargé au build de l'image worker (FASTEMBED_CACHE_PATH, voir Dockerfile), jamais au
premier appel. fastembed n'est installé que dans l'image worker (groupe uv « worker ») et importé à la demande :
l'API ne le charge jamais. Sans fastembed (API, docker compose local), get_embedder() renvoie None et la
présélection se rabat sur les offres les plus récentes.

Texte d'une offre : titre, type de rôle, stack, lieu (en-tête, qui pèse plus), puis toute la description. Un seul
calcul par offre (partagé entre utilisateurs), refait seulement si un de ces champs change
(models.JOB_EMBEDDED_FIELDS remet embedded_at à None).
Texte d'un profil : positionnement, titre, rôles visés, compétences fortes, expériences, offres de référence et
offres aimées. Recalculé quand la version du profil change. Les J'aime récents sont pris en compte à part, au
moment du classement (ranking.py), sans recalculer le profil.
"""

import logging
import math
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import EMBEDDING_DIM, Job, JobFeedback, UserProfile
from .profile import ProfileData

log = logging.getLogger("mire.embeddings")

MODEL_NAME = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
QUERY_PREFIX = ""  # profil (requête) ; « query: » pour un modèle e5
PASSAGE_PREFIX = ""  # offre (document) ; « passage: » pour un modèle e5
BATCH = 64
CHUNK_CHARS = 450  # environ 128 jetons de français ou d'anglais
MAX_CHUNKS = 8  # au-delà (environ 3 600 caractères), le début et la fin sont gardés


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class FastEmbedder:
    def __init__(self, model_name: str = MODEL_NAME, cache_dir: str | None = None):
        from fastembed import TextEmbedding  # import paresseux : jamais chargé par l'API

        self.model = TextEmbedding(model_name, cache_dir=cache_dir or os.environ.get("FASTEMBED_CACHE_PATH"))

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(x) for x in v] for v in self.model.embed(list(texts), batch_size=32)]


_embedder: Embedder | None = None


def get_embedder() -> Embedder | None:
    """Modèle chargé une fois par processus ; None sans FASTEMBED_CACHE_PATH (dossier du modèle téléchargé au
    build de l'image worker) ou sans fastembed (image API, docker compose local, tests : aucun téléchargement)."""
    global _embedder
    if _embedder is None:
        if not os.environ.get("FASTEMBED_CACHE_PATH"):
            return None
        try:
            import fastembed  # noqa: F401
        except ImportError:
            return None
        _embedder = FastEmbedder()
    return _embedder


def _join(parts) -> str:
    return "\n".join(p.strip() for p in parts if p and p.strip())


def chunks(text: str | None, size: int = CHUNK_CHARS, limit: int = MAX_CHUNKS) -> list[str]:
    """Découpe un texte en morceaux d'environ `size` caractères, entre deux mots. Trop long : le début et la fin
    sont gardés (les exigences d'une offre sont souvent à la fin)."""
    out: list[str] = []
    current = ""
    for word in (text or "").split():
        if current and len(current) + len(word) + 1 > size:
            out.append(current)
            current = ""
        current = f"{current} {word}".strip()
    if current:
        out.append(current)
    if len(out) > limit:
        head = limit // 3
        out = out[:head] + out[-(limit - head):]
    return out


def job_texts(job: Job) -> list[str]:
    """En-tête de l'offre, puis sa description en morceaux."""
    head = _join([job.title, job.role_type, job.stack, job.location]) or job.title
    return [PASSAGE_PREFIX + t for t in [head, *chunks(job.summary)]]


def _pool(vectors: list[list[float]]) -> list[float]:
    """Moyenne normalisée ; l'en-tête (premier morceau) pèse la moitié de tous les autres réunis."""
    weights = [max(1.0, (len(vectors) - 1) / 2)] + [1.0] * (len(vectors) - 1)
    mean = [sum(w * v[i] for w, v in zip(weights, vectors, strict=True)) for i in range(len(vectors[0]))]
    length = math.sqrt(sum(x * x for x in mean)) or 1.0
    return [x / length for x in mean]


def embed_documents(embedder: Embedder, documents: list[list[str]]) -> list[list[float]]:
    """Un vecteur par document, chaque document étant une liste de morceaux (un seul appel au modèle)."""
    flat = [text for doc in documents for text in doc]
    vectors = embedder.embed(flat)
    _check(vectors, len(flat))
    out, start = [], 0
    for doc in documents:
        out.append(_pool(vectors[start:start + len(doc)]))
        start += len(doc)
    return out


def profile_text(db: Session, profile: UserProfile, data: ProfileData) -> str:
    liked = db.scalars(select(Job).join(JobFeedback, JobFeedback.job_id == Job.id).where(
        JobFeedback.user_id == profile.user_id, JobFeedback.sentiment == "like")
        .order_by(JobFeedback.id.desc()).limit(10)).all()
    strong = [s.name for s in data.skills if s.kind == "fort"] or [s.name for s in data.skills]
    return QUERY_PREFIX + _join([
        data.positioning, data.headline,
        "Rôles visés : " + ", ".join(data.preferences.roles) if data.preferences.roles else None,
        "Compétences : " + ", ".join(strong[:30]) if strong else None,
        *[f"{r.title} ({r.company})" for r in data.reference_jobs[:10]],
        *[f"{j.title} {j.stack or ''}" for j in liked],
        *[_join([e.title, (e.description or "")[:600]]) for e in data.experiences[:4]],
    ])


def _check(vectors: list[list[float]], expected: int) -> None:
    if len(vectors) != expected or any(len(v) != EMBEDDING_DIM for v in vectors):
        raise ValueError(f"Embeddings inattendus (attendu {expected} x {EMBEDDING_DIM})")


def embed_pending_jobs(db: Session, embedder: Embedder | None = None, limit: int | None = None) -> int:
    """Calcule les embeddings des offres ouvertes nouvelles ou modifiées. Retourne le nombre d'offres traitées."""
    embedder = embedder or get_embedder()
    if embedder is None:
        return 0
    done = 0
    while limit is None or done < limit:
        size = BATCH if limit is None else min(BATCH, limit - done)
        jobs = db.scalars(select(Job).where(Job.embedded_at.is_(None), Job.closed_at.is_(None))
                          .order_by(Job.id).limit(size)).all()
        if not jobs:
            break
        vectors = embed_documents(embedder, [job_texts(j) for j in jobs])
        now = datetime.now(UTC)
        for job, vector in zip(jobs, vectors, strict=True):
            job.embedding, job.embedded_at = vector, now
        db.commit()
        done += len(jobs)
    return done


def ensure_profile_embedding(db: Session, profile: UserProfile, data: ProfileData,
                             embedder: Embedder | None = None) -> list[float] | None:
    """Embedding du profil, recalculé si la version du profil a changé (None : pas de modèle)."""
    if profile.embedding_version == profile.version:
        current = db.scalar(select(UserProfile.embedding).where(UserProfile.id == profile.id))
        if current is not None:
            return [float(x) for x in current]
    embedder = embedder or get_embedder()
    if embedder is None:
        return None
    [vector] = embed_documents(embedder, [chunks(profile_text(db, profile, data)) or [""]])
    profile.embedding, profile.embedding_version = vector, profile.version
    db.commit()
    return vector


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def nearest_jobs(db: Session, vector: Sequence[float], job_ids: list[int] | None = None, limit: int | None = 50,
                 exclude_id: int | None = None) -> list[tuple[int, float]]:
    """Offres ouvertes les plus proches du vecteur : [(job_id, similarité cosinus)], meilleures d'abord.

    PostgreSQL : distance calculée par pgvector. L'index HNSW ne sert qu'à la recherche sans filtre et avec une
    limite courte (offres similaires) : il ne rend pas plus de hnsw.ef_search (40) lignes, et un filtre appliqué
    après lui en laisse encore moins. Sinon : distance exacte, triée ici (quelques milliers d'offres au plus).
    SQLite (tests) : en Python.
    """
    vector = [float(x) for x in vector]
    conditions = [Job.closed_at.is_(None), Job.embedding.is_not(None)]
    if job_ids is not None:
        conditions.append(Job.id.in_(job_ids or [-1]))
    if exclude_id is not None:
        conditions.append(Job.id != exclude_id)
    if db.get_bind().dialect.name == "postgresql":
        distance = Job.embedding.cosine_distance(vector)
        query = select(Job.id, distance).where(*conditions)
        if job_ids is None and limit is not None and limit <= 20:
            query = query.order_by(distance).limit(limit)
        rows = db.execute(query).all()
        return sorted(((job_id, round(1 - float(d), 4)) for job_id, d in rows), key=lambda pair: -pair[1])[:limit]
    rows = db.execute(select(Job.id, Job.embedding).where(*conditions)).all()
    scored = [(job_id, round(_cosine(vector, [float(x) for x in emb]), 4)) for job_id, emb in rows]
    return sorted(scored, key=lambda pair: -pair[1])[:limit]


def similar_jobs(db: Session, job_id: int, limit: int = 5) -> list[tuple[int, float]]:
    """Offres ouvertes les plus proches d'une offre (sans LLM) ; [] si l'offre n'a pas encore d'embedding."""
    vector = db.scalar(select(Job.embedding).where(Job.id == job_id))
    if vector is None:
        return []
    return nearest_jobs(db, vector, limit=limit, exclude_id=job_id)
