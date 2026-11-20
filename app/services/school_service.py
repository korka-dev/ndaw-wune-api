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


def _compatible(a: School, b: School) -> bool:
    """APP-23 : deux fiches de même nom ne désignent la même école que si rien
    ne les distingue : codes, communes et IEF égaux quand ils sont renseignés
    des deux côtés. Deux homonymes de communes différentes restent isolées."""
    if a.code_ecole is not None and b.code_ecole is not None and a.code_ecole != b.code_ecole:
        return False
    for champ in ("city", "region"):
        va, vb = getattr(a, champ), getattr(b, champ)
        if va and vb and normalize_school_name(va) != normalize_school_name(vb):
            return False
    return True


async def get_matching_school_ids(db: AsyncSession, school: School) -> list[uuid.UUID]:
    """IDs des fiches qui désignent la même école que `school` — couvre les
    doublons créés à l'import, sur lesquels enseignants, élèves et superviseur
    peuvent être rattachés séparément.

    Rapport V2, APP-23 : jamais sur le seul nom. Même nom normalisé ET aucun
    élément géographique contradictoire (code école, commune, IEF). Les
    groupes ambigus sont listés par scripts/doublons_ecoles.py pour arbitrage.
    """
    target = normalize_school_name(school.name or "")
    if not target:
        return [school.id]
    rows = (await db.execute(select(School))).scalars().all()
    matches = [
        s.id for s in rows
        if s.id == school.id or (normalize_school_name(s.name or "") == target and _compatible(school, s))
    ]
    return matches or [school.id]
