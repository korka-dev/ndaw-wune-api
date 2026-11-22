"""Purge selon les durées de conservation fixées par ARED (rapport V2, APP-07, APP-13).

Durées lues dans l'environnement (`.env`) ; une durée vide ne purge rien :
    RETENTION_AUDIOS_JOURS   audios d'évaluation d'enfants (fichier + référence)
    RETENTION_AUDIT_JOURS    entrées de l'historique des modifications

Aperçu par défaut. `--appliquer` supprime, journalise le bilan (sans nom ni
contenu) et ne touche jamais aux autres données. Faire une sauvegarde avant.

    docker compose exec -T backend python -m scripts.purge_retention
    docker compose exec -T backend python -m scripts.purge_retention --appliquer
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.services.audit_service import journaliser

TABLES_AUDIO = ("evaluation_resultats", "evaluation_resultat_historique", "evaluation_tirages")


def audios_expires(jours: int) -> list[Path]:
    limite = datetime.now(timezone.utc) - timedelta(days=jours)
    dossier = Path(settings.UPLOADS_DIR)
    if not dossier.is_dir():
        return []
    return sorted(p for p in dossier.glob("eval_audio_*")
                  if p.is_file() and datetime.fromtimestamp(p.stat().st_mtime, timezone.utc) < limite)


async def traiter(appliquer: bool) -> dict:
    bilan: dict = {"audios": None, "audit": None}
    async with AsyncSessionLocal() as db:
        if settings.RETENTION_AUDIOS_JOURS:
            fichiers = audios_expires(settings.RETENTION_AUDIOS_JOURS)
            bilan["audios"] = {"duree_jours": settings.RETENTION_AUDIOS_JOURS, "fichiers": len(fichiers)}
            if appliquer and fichiers:
                noms = [p.name for p in fichiers]
                for t in TABLES_AUDIO:
                    await db.execute(text(f"UPDATE {t} SET audio_filename = NULL WHERE audio_filename = ANY(:n)"), {"n": noms})
                await db.commit()          # références retirées avant les fichiers : jamais de lien mort servi
                for p in fichiers:
                    p.unlink(missing_ok=True)
        if settings.RETENTION_AUDIT_JOURS:
            limite = datetime.now(timezone.utc) - timedelta(days=settings.RETENTION_AUDIT_JOURS)
            n = (await db.execute(text("SELECT count(*) FROM audit_logs WHERE created_at < :l"), {"l": limite})).scalar_one()
            bilan["audit"] = {"duree_jours": settings.RETENTION_AUDIT_JOURS, "entrees": n}
            if appliquer and n:
                await db.execute(text("SET LOCAL ndawwune.audit_purge = 'on'"))
                await db.execute(text("DELETE FROM audit_logs WHERE created_at < :l"), {"l": limite})
                await db.commit()
        if appliquer and any(bilan.values()):
            journaliser(db, acteur=None, action="suppression", entity="Purge de conservation",
                        description="Purge de conservation : " + ", ".join(
                            f"{k} {v}" for k, v in bilan.items() if v), path="scripts/purge_retention")
            await db.commit()
    return bilan


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--appliquer", action="store_true")
    a = p.parse_args()
    bilan = asyncio.run(traiter(a.appliquer))
    if not any(bilan.values()):
        print("Aucune durée de conservation fixée (RETENTION_*_JOURS vides) : rien à purger.")
        return
    for k, v in bilan.items():
        print(f"{k:7} : {v if v else 'durée non fixée'}")
    print("Appliqué." if a.appliquer else "Aperçu seul : relancer avec --appliquer (après sauvegarde).")


if __name__ == "__main__":
    main()
