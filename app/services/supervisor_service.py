"""Service — périmètre d'un superviseur : enseignants supervisés et langue
d'enseignement.

Les enseignants d'un superviseur sont stockés dans `supervisor.classes` (liste
d'UUIDs d'enseignants) et alimentés manuellement depuis le dashboard admin.
Beaucoup de superviseurs n'ont jamais été assignés — souvent parce que leur
école existe en doublon (préfixes « EE » / « EFA »), ce que le script
link_superviseurs_teachers.py corrige après coup. Résultat : leurs écrans
mobiles (difficultés, élèves, progression) restaient vides.

`get_supervised_teacher_ids` conserve l'assignation explicite quand elle existe
et retombe sinon sur tous les enseignants actifs de l'école du superviseur, pour
que le suivi fonctionne pour tout le monde sans intervention manuelle.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.langue import canonical_langue, strip_accents
from app.models.school import School
from app.models.user import User, UserRole, UserStatus

logger = logging.getLogger(__name__)


def _parse_assigned_ids(supervisor: User) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    for raw in (supervisor.classes or []):
        try:
            ids.append(uuid.UUID(str(raw)))
        except (ValueError, AttributeError, TypeError):
            continue
    return ids


_SCHOOL_PREFIXES = ("EE ", "EFA ", "ECOLE ELEMENTAIRE ", "ECOLE ")


def _normalize_school_name(name: str) -> str:
    """Nom d'école comparable : majuscules, espaces normalisés, préfixe retiré.
    Même règle que le script d'exploitation link_superviseurs_teachers.py."""
    s = re.sub(r"\s+", " ", strip_accents(name).upper().strip())
    for prefix in _SCHOOL_PREFIXES:
        if s.startswith(prefix):
            return s[len(prefix):]
    return s


async def _same_name_school_ids(db: AsyncSession, school: School) -> list[uuid.UUID]:
    """IDs des écoles portant le même nom normalisé — couvre les doublons créés
    à l'import (« EE Ndiaganiao » et « Ndiaganiao »), sur lesquels superviseur
    et enseignants peuvent être rattachés séparément."""
    target = _normalize_school_name(school.name or "")
    if not target:
        return [school.id]
    rows = (await db.execute(select(School.id, School.name))).all()
    return [sid for sid, name in rows if _normalize_school_name(name or "") == target]


async def get_supervised_teacher_ids(db: AsyncSession, supervisor: User) -> list[uuid.UUID]:
    """UUIDs des enseignants suivis par ce superviseur.

    1. Assignation explicite (`supervisor.classes`) — cas nominal.
    2. Repli : enseignants actifs de son école de rattachement, doublons de nom
       inclus (superviseur et enseignants sont fréquemment rattachés à deux
       fiches école distinctes portant le même nom).
    """
    assigned = _parse_assigned_ids(supervisor)
    if assigned:
        return assigned

    if supervisor.school_id is None or supervisor.school is None:
        logger.warning(
            "[Superviseur] %s n'a ni enseignant assigné ni école de rattachement — "
            "aucun suivi possible.", supervisor.id,
        )
        return []

    school_ids = await _same_name_school_ids(db, supervisor.school)
    rows = (
        await db.execute(
            select(User.id).where(
                User.role == UserRole.enseignant,
                User.school_id.in_(school_ids),
                User.status == UserStatus.actif,
            )
        )
    ).scalars().all()

    logger.info(
        "[Superviseur] %s sans assignation explicite — repli sur les %d enseignant(s) "
        "de son école (%s).", supervisor.id, len(rows), supervisor.school.name,
    )
    return list(rows)


async def is_supervised_teacher(
    db: AsyncSession, supervisor: User, teacher_id: uuid.UUID
) -> bool:
    """Contrôle d'accès : cet enseignant relève-t-il de ce superviseur ?
    Suit exactement le même périmètre que get_supervised_teacher_ids."""
    return teacher_id in set(await get_supervised_teacher_ids(db, supervisor))


async def resolve_supervisor_langue(db: AsyncSession, supervisor: User) -> Optional[str]:
    """Langue d'enseignement du superviseur, sous forme canonique.

    1. Langue de son école de rattachement (cas normal).
    2. À défaut, langue des écoles des enseignants qu'il supervise, si elle est
       unique — couvre un superviseur sans école rattachée.

    None si indéterminable, ou si le superviseur couvre plusieurs langues : dans
    ce cas les appelants ne doivent rien imposer plutôt que risquer la mauvaise
    langue.
    """
    if supervisor.school and supervisor.school.langue:
        return canonical_langue(supervisor.school.langue)

    teacher_ids = await get_supervised_teacher_ids(db, supervisor)
    if not teacher_ids:
        return None

    langues = (
        await db.execute(
            select(School.langue)
            .join(User, User.school_id == School.id)
            .where(User.id.in_(teacher_ids), School.langue.isnot(None))
            .distinct()
        )
    ).scalars().all()

    valeurs = {c for c in (canonical_langue(l) for l in langues) if c}
    if len(valeurs) == 1:
        return valeurs.pop()
    if len(valeurs) > 1:
        logger.warning(
            "[Superviseur] %s supervise plusieurs langues (%s) — rattachez-le à "
            "une école pour lever l'ambiguïté.",
            supervisor.id, ", ".join(sorted(valeurs)),
        )
    return None
