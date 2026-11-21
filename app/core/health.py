"""État de santé (rapport V2, APP-45).

- disponibilité (`/health`, `/health/live`) : le processus répond, sans
  toucher aux dépendances — un incident base ou Redis ne doit pas faire
  redémarrer l'API en boucle ;
- préparation (`/health/ready`) : chaque dépendance nécessaire au service est
  contrôlée séparément (base, migrations, Redis, stockage des médias), avec
  un délai borné. 503 dès qu'une dépendance requise manque.

Aucune information sensible (URL, mot de passe, chemin complet, trace) n'est
renvoyée : seulement le nom du contrôle, son état, sa durée et un code court.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine

logger = logging.getLogger(__name__)

DELAI_CONTROLE_S = 3.0


@lru_cache(maxsize=1)
def revision_attendue() -> str | None:
    """Révision Alembic « head » livrée avec le code (lue une fois)."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        racine = Path(__file__).resolve().parents[2]
        cfg = Config(str(racine / "alembic.ini"))
        cfg.set_main_option("script_location", str(racine / "alembic"))
        heads = ScriptDirectory.from_config(cfg).get_heads()
        return heads[0] if len(heads) == 1 else None
    except Exception:  # pragma: no cover - environnement sans alembic.ini
        logger.warning("Révision Alembic attendue illisible : contrôle des migrations ignoré.")
        return None


async def _base() -> dict:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        attendue = revision_attendue()
        if attendue is None:
            return {"migrations": "non_controle"}
        actuelle = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
    if actuelle != attendue:
        # La base répond mais n'est pas au schéma du code livré.
        raise _Echec("MIGRATION_EN_RETARD", {"migrations": "en_retard"})
    return {"migrations": "a_jour"}


async def _redis() -> dict:
    from app.core.redis import get_redis

    r = await get_redis()
    await r.ping()
    return {}


async def _medias() -> dict:
    """Le volume des médias existe et accepte une écriture (photos, audios)."""
    base = Path(settings.UPLOADS_DIR)

    def essai() -> None:
        base.mkdir(parents=True, exist_ok=True)
        sonde = base / f".sonde-sante-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        sonde.write_bytes(b"ok")
        sonde.unlink()

    await asyncio.to_thread(essai)
    return {}


class _Echec(Exception):
    def __init__(self, code: str, extra: dict | None = None) -> None:
        super().__init__(code)
        self.code, self.extra = code, extra or {}


CONTROLES: dict[str, Callable[[], Awaitable[dict]]] = {
    "base": _base,
    "redis": _redis,
    "medias": _medias,
}


async def _executer(nom: str, fn: Callable[[], Awaitable[dict]]) -> dict:
    debut = time.perf_counter()
    try:
        extra = await asyncio.wait_for(fn(), timeout=DELAI_CONTROLE_S)
        etat = {"etat": "ok", **extra}
    except asyncio.TimeoutError:
        etat = {"etat": "echec", "code": "DELAI_DEPASSE"}
    except _Echec as e:
        etat = {"etat": "echec", "code": e.code, **e.extra}
    except Exception as e:  # le détail reste dans les journaux serveur
        logger.warning("Contrôle de santé « %s » en échec : %s", nom, type(e).__name__)
        etat = {"etat": "echec", "code": "INDISPONIBLE"}
    etat["duree_ms"] = round((time.perf_counter() - debut) * 1000, 1)
    return etat


async def preparation() -> tuple[bool, dict]:
    noms = list(CONTROLES)
    resultats = await asyncio.gather(*(_executer(n, CONTROLES[n]) for n in noms))
    controles = dict(zip(noms, resultats))
    pret = all(c["etat"] == "ok" for c in controles.values())
    if not pret:
        logger.error("Préparation KO : %s", {n: c.get("code") for n, c in controles.items() if c["etat"] != "ok"})
    return pret, {
        "status": "ready" if pret else "not_ready",
        "app": settings.APP_NAME, "env": settings.APP_ENV,
        "version": os.environ.get("APP_COMMIT") or None,
        "controles": controles,
    }
