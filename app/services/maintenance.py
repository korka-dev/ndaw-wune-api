"""Tâches de fond périodiques (rapport V2, APP-21).

La clôture des séances oubliées ne dépend plus de l'ouverture d'une page
d'administration : elle tourne en tâche de fond dans chaque processus. Les
lignes sont verrouillées (`FOR UPDATE SKIP LOCKED`) : plusieurs workers
gunicorn peuvent la lancer en même temps sans double traitement.
"""
from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.services.seance_service import close_stale_seances

logger = logging.getLogger(__name__)


async def run_once() -> int:
    async with AsyncSessionLocal() as db:
        n = await close_stale_seances(db)
        await db.commit()
        return n


async def periodic_maintenance() -> None:
    interval = settings.SEANCE_CLEANUP_INTERVAL_SECONDS
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # une panne de base ne doit jamais tuer la boucle
            logger.exception("Maintenance périodique : échec de la clôture des séances oubliées.")
        await asyncio.sleep(interval)
