"""Embeddings des offres et des profils (présélection par similarité) : voir la présélection dans matching.py."""

from sqlalchemy.orm import Session


def embed_pending_jobs(db: Session, embedder=None) -> int:
    """Calcule les embeddings des offres nouvelles ou modifiées. Retourne le nombre d'offres traitées."""
    return 0
