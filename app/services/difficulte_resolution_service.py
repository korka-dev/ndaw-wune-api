"""Service partagé — résolution des difficultés signalées (app superviseur + admin web)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rapport_difficulte_resolution import RapportDifficulteResolution
from app.models.user import User


async def get_resolutions_map(db: AsyncSession, rapport_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, bool]]:
    """Retourne {rapport_id: {difficulte_label: resolue}} pour les rapports donnés."""
    if not rapport_ids:
        return {}
    rows = (
        await db.execute(
            select(RapportDifficulteResolution).where(
                RapportDifficulteResolution.rapport_id.in_(rapport_ids)
            )
        )
    ).scalars().all()
    result: dict[uuid.UUID, dict[str, bool]] = {}
    for row in rows:
        result.setdefault(row.rapport_id, {})[row.difficulte_label] = row.resolue
    return result


async def set_resolution(
    db: AsyncSession,
    *,
    rapport_id: uuid.UUID,
    difficulte_label: str,
    resolue: bool,
    commentaire_resolution: str | None,
    current_user: User,
) -> RapportDifficulteResolution:
    """Upsert de l'état de résolution d'une difficulté d'un rapport."""
    obj = (
        await db.execute(
            select(RapportDifficulteResolution).where(
                RapportDifficulteResolution.rapport_id == rapport_id,
                RapportDifficulteResolution.difficulte_label == difficulte_label,
            )
        )
    ).scalar_one_or_none()

    if obj is None:
        obj = RapportDifficulteResolution(
            rapport_id=rapport_id,
            difficulte_label=difficulte_label,
        )
        db.add(obj)

    obj.resolue = resolue
    obj.commentaire_resolution = commentaire_resolution
    if resolue:
        obj.resolue_par = current_user.id
        obj.resolue_le = datetime.now(timezone.utc)
    else:
        obj.resolue_par = None
        obj.resolue_le = None

    await db.flush()
    return obj
