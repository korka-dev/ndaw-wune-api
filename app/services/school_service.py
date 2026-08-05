"""Service — résolution des doublons de fiche école.

Les imports ont créé des fiches école dupliquées pour un même établissement
physique (préfixes « EE » / « EFA » / « ECOLE » incohérents selon la source,
ex. « EE BATTAL » et « EFA BATTAL »). Un enseignant peut être rattaché à l'une
des deux fiches pendant que ses élèves sont répartis sur les deux — auquel cas
toute requête qui filtre strictement par `school_id` ne voit qu'une partie
des élèves réels, silencieusement.

`normalize_school_name` et `get_matching_school_ids` centralisent la règle
déjà utilisée pour les superviseurs (`supervisor_service.py`) et par le script
d'exploitation `link_superviseurs_teachers.py`, pour que tout code qui
rattache des élèves à un enseignant (ou à un superviseur) via son école
bénéficie de la même résilience aux doublons.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.langue import strip_accents
from app.models.school import School

_SCHOOL_PREFIXES = ("EE ", "EFA ", "ECOLE ELEMENTAIRE ", "ECOLE ")


def normalize_school_name(name: str) -> str:
    """Nom d'école comparable : majuscules, espaces normalisés, préfixe retiré."""
    s = re.sub(r"\s+", " ", strip_accents(name or "").upper().strip())
    for prefix in _SCHOOL_PREFIXES:
        if s.startswith(prefix):
            return s[len(prefix):]
    return s


async def get_matching_school_ids(db: AsyncSession, school: School) -> list[uuid.UUID]:
    """IDs de toutes les écoles portant le même nom normalisé que `school` —
    couvre les doublons créés à l'import, sur lesquels enseignants, élèves et
    superviseur peuvent être rattachés séparément."""
    target = normalize_school_name(school.name or "")
    if not target:
        return [school.id]
    rows = (await db.execute(select(School.id, School.name))).all()
    matches = [sid for sid, name in rows if normalize_school_name(name or "") == target]
    return matches or [school.id]
