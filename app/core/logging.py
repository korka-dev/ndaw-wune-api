"""Configuration du logging pour l'application."""
from __future__ import annotations

import logging
import sys
from typing import Any


def setup_logging(is_production: bool = False) -> None:
    """
    Configure le logging global.
    - En développement : format lisible avec couleurs.
    - En production    : format JSON structuré pour les agrégateurs (Datadog, Loki…).
    """
    log_level = logging.WARNING if is_production else logging.INFO

    if is_production:
        formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers = [handler]

    # Réduire le bruit des bibliothèques tierces
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "passlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class _JsonFormatter(logging.Formatter):
    """Formatter JSON minimaliste sans dépendance externe."""

    import json as _json

    def format(self, record: logging.LogRecord) -> str:
        import json
        doc: dict[str, Any] = {
            "time":    self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level":   record.levelname,
            "logger":  record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            doc["exception"] = self.formatException(record.exc_info)
        return json.dumps(doc, ensure_ascii=False)
