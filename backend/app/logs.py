"""Journalisation : un objet JSON par ligne sur la sortie standard (production : lu par Log Analytics).

LOG_FORMAT=json (défaut) ou text (docker compose local, plus lisible). Les journaux ne contiennent jamais de
secret ni de contenu de profil : seulement des identifiants, des compteurs et des messages d'erreur.
Les URL des journaux d'accès sont coupées avant « ? » (ex. le code OAuth de /auth/callback?code=…).
"""

import json
import logging
import os
import sys
from datetime import UTC, datetime

# Attributs standard d'un LogRecord : tout le reste vient de `extra=` et est ajouté à l'objet JSON
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        out.update({k: v for k, v in vars(record).items() if k not in _STANDARD and not k.startswith("_")})
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


class StripQueryString(logging.Filter):
    """Journal d'accès d'uvicorn : chemin sans les paramètres (ils peuvent contenir un code OAuth, un jeton…)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3 and isinstance(record.args[2], str):
            args = list(record.args)
            args[2] = args[2].split("?", 1)[0]
            record.args = tuple(args)
        return True


_configured = False


def configure() -> None:
    """Configure les journaux de Mire et d'uvicorn (idempotent)."""
    global _configured
    if _configured:
        return
    _configured = True
    handler = logging.StreamHandler(sys.stdout)
    if os.environ.get("LOG_FORMAT", "json").lower() == "text":
        handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    else:
        handler.setFormatter(JsonFormatter())
    mire = logging.getLogger("mire")
    mire.handlers[:] = [handler]
    mire.setLevel(logging.INFO)
    mire.propagate = False
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        if logger.handlers or name == "uvicorn":
            logger.handlers[:] = [handler]
    access = logging.getLogger("uvicorn.access")
    access.addFilter(StripQueryString())
