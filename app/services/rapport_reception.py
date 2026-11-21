"""Contexte figé à la réception d'un rapport journalier (lot L4, Partie II D4.2/D5.1).

À la réception, le serveur fixe une fois pour toutes :
- `ecole_id` : l'école de rattachement de l'auteur ;
- `superviseur_id` : le superviseur responsable de l'auteur à cette date
  (affectation explicite ; à défaut, l'unique superviseur de l'école). NULL si
  aucun ou plusieurs : l'ambiguïté apparaît dans le bilan de qualité au lieu
  d'être tranchée arbitrairement ;
- `effectif_attendu` : élèves actifs et non archivés des classes du tuteur
  (référence de calcul conservée, distincte des effectifs déclarés).

Ces valeurs ne sont jamais recalculées : un changement d'affectation en cours
de campagne ne réécrit pas l'historique (REC07).
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.eleve import Eleve
from app.models.user import User, UserRole, UserStatus
from app.services.school_service import get_matching_school_ids


def _classes_normalisees(user: User) -> list[str]:
    return [" ".join(c.strip().split()).lower() for c in (user.classes or []) if c and c.strip()]


def _clause_classes(user: User, school_ids: list[uuid.UUID]):
    return (
        Eleve.school_id.in_(school_ids),
        func.lower(func.regexp_replace(Eleve.classe, r"\s+", " ", "g")).in_(_classes_normalisees(user)),
    )


async def superviseur_responsable(db: AsyncSession, auteur: User) -> Optional[uuid.UUID]:
    if auteur.role == UserRole.superviseur:
        return auteur.id
    actifs = (User.role == UserRole.superviseur, User.status == UserStatus.actif, User.archived_at.is_(None))
    explicites = (await db.execute(
        select(User.id).where(*actifs, User.classes.any(str(auteur.id)))
    )).scalars().all()
    if len(explicites) == 1:
        return explicites[0]
    if explicites or auteur.school is None:
        return None  # plusieurs superviseurs explicites : ambigu, signalé en qualité
    school_ids = await get_matching_school_ids(db, auteur.school)
    # Repli identique au périmètre superviseur : superviseurs SANS affectation
    # explicite de la même école.
    candidats = (await db.execute(
        select(User.id).where(
            *actifs, User.school_id.in_(school_ids),
            func.coalesce(func.cardinality(User.classes), 0) == 0,
        )
    )).scalars().all()
    return candidats[0] if len(candidats) == 1 else None


async def effectif_attendu(db: AsyncSession, auteur: User) -> Optional[int]:
    if auteur.role != UserRole.enseignant or auteur.school is None or not _classes_normalisees(auteur):
        return None
    school_ids = await get_matching_school_ids(db, auteur.school)
    return (await db.execute(
        select(func.count()).select_from(Eleve).where(
            *_clause_classes(auteur, school_ids), Eleve.statut == "actif", Eleve.archived_at.is_(None),
        )
    )).scalar_one()


async def verifier_absents(db: AsyncSession, ids: Optional[list[uuid.UUID]]) -> Optional[list[str]]:
    """Les identifiants d'absents doivent désigner des élèves connus (archivés
    compris : une saisie hors ligne peut précéder un archivage)."""
    if not ids:
        return None
    connus = set((await db.execute(select(Eleve.id).where(Eleve.id.in_(ids)))).scalars().all())
    inconnus = [str(i) for i in ids if i not in connus]
    if inconnus:
        raise HTTPException(
            status_code=422,
            detail={"code": "ABSENTS_INCONNUS", "message": "Élève(s) absent(s) inconnu(s).", "ids": inconnus},
        )
    return [str(i) for i in ids]


async def contexte_reception(db: AsyncSession, auteur: User) -> dict:
    return {
        "ecole_id": auteur.school_id,
        "superviseur_id": await superviseur_responsable(db, auteur),
        "effectif_attendu": await effectif_attendu(db, auteur),
    }
