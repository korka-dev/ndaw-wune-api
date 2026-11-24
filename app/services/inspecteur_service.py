"""Périmètre d'un inspecteur (lot L8) : les écoles de son IEF.

L'IEF d'une école est le libellé `schools.region` ; celle d'un inspecteur est
`users.ief`. La comparaison ignore la casse et les espaces de bord, et le
périmètre est recalculé à chaque requête : une école rattachée ou retirée de
l'IEF par l'admin entre ou sort aussitôt du périmètre.

Rapports visibles : ceux rattachés (à la réception) à une école de l'IEF,
plus les rapports antérieurs sans rattachement dont le champ `ief` saisi
correspond (D10.2 : pas de rattachement deviné, seulement le libellé transmis).
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rapport_journalier import RapportJournalier
from app.models.school import School
from app.models.user import User


def cle_ief(valeur):
    """Expression SQL normalisée d'un libellé d'IEF."""
    return func.lower(func.trim(valeur))


def normaliser(ief: Optional[str]) -> str:
    return (ief or "").strip().lower()


async def ief_canonique(db: AsyncSession, saisie: Optional[str]) -> str:
    """Libellé d'IEF tel qu'enregistré sur les écoles ; 422 si aucune école ne le porte."""
    cle = normaliser(saisie)
    if not cle:
        raise HTTPException(status_code=422, detail="L'IEF de l'inspecteur est obligatoire.")
    libelle = (await db.execute(
        select(func.min(func.trim(School.region))).where(cle_ief(School.region) == cle)
    )).scalar_one_or_none()
    if not libelle:
        raise HTTPException(status_code=422, detail="Aucune école n'est rattachée à cette IEF.")
    return libelle


async def liste_iefs(db: AsyncSession) -> list[dict]:
    """IEF connues (libellés `schools.region`) et nombre d'écoles actives de chacune."""
    rows = (await db.execute(
        select(func.min(func.trim(School.region)).label("ief"), func.count(School.id).label("nb"))
        .where(School.region.is_not(None), func.trim(School.region) != "", School.archived_at.is_(None))
        .group_by(cle_ief(School.region))
        .order_by(func.min(func.trim(School.region)))
    )).all()
    return [{"ief": r.ief, "nb_ecoles": int(r.nb)} for r in rows]


async def ecole_ids(db: AsyncSession, inspecteur: User, archives: bool = True) -> set[uuid.UUID]:
    cle = normaliser(inspecteur.ief)
    if not cle:
        return set()
    stmt = select(School.id).where(cle_ief(School.region) == cle)
    if not archives:
        stmt = stmt.where(School.archived_at.is_(None))
    return set((await db.execute(stmt)).scalars().all())


def filtre_rapports(ids: set[uuid.UUID], inspecteur: User):
    """Condition SQL : rapport dans le périmètre de l'inspecteur."""
    cle = normaliser(inspecteur.ief)
    if not cle:
        return RapportJournalier.id.is_(None)  # aucun rapport
    conditions = [and_(RapportJournalier.ecole_id.is_(None), cle_ief(RapportJournalier.ief) == cle)]
    if ids:
        conditions.append(RapportJournalier.ecole_id.in_(ids))
    return or_(*conditions)
