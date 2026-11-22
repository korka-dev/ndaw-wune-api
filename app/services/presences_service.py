"""Présences individuelles des élèves (lot L6, Partie II D8 « Assiduité »).

Règles :
- un rapport journalier ne produit des présences que s'il transmet la liste
  identifiée des absents (`absents_ids`, même vide). Sans cette liste, le jour
  reste « non renseigné » : on ne déduit jamais « aucune absence » (D4.4) ;
- les élèves concernés sont ceux qui sont actifs et non archivés dans les
  classes du tuteur à la réception (même population que `effectif_attendu`) ;
- une saisie explicite (`appel`, par exemple une absence excusée) n'est jamais
  écrasée par un rapport ;
- l'annulation d'un rapport retire les présences qu'il avait produites.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.eleve import Eleve
from app.models.presence_eleve import STATUTS_PRESENCE, PresenceEleve
from app.models.rapport_journalier import RapportJournalier
from app.models.user import User, UserRole
from app.services.rapport_reception import _classes_normalisees, _clause_classes
from app.services.school_service import get_matching_school_ids


async def eleves_du_tuteur(db: AsyncSession, tuteur: User) -> list[uuid.UUID]:
    """Élèves actifs et non archivés des classes du tuteur."""
    if tuteur.role != UserRole.enseignant or tuteur.school is None or not _classes_normalisees(tuteur):
        return []
    school_ids = await get_matching_school_ids(db, tuteur.school)
    return list((await db.execute(
        select(Eleve.id).where(*_clause_classes(tuteur, school_ids), Eleve.statut == "actif", Eleve.archived_at.is_(None))
    )).scalars().all())


async def generer_depuis_rapport(db: AsyncSession, rapport: RapportJournalier, auteur: User) -> int:
    """Crée les présences du jour à partir d'un rapport. Retourne le nombre de lignes écrites."""
    if rapport.absents_ids is None:
        return 0
    eleves = await eleves_du_tuteur(db, auteur)
    if not eleves:
        return 0
    absents = {str(i) for i in rapport.absents_ids}
    lignes = [
        {
            "id": uuid.uuid4(), "eleve_id": e, "date_jour": rapport.date_rapport, "session_id": rapport.session_id,
            "teacher_id": auteur.id, "statut": "absent" if str(e) in absents else "present",
            "source": "rapport", "rapport_id": rapport.id,
        }
        for e in eleves
    ]
    stmt = insert(PresenceEleve).values(lignes)
    # Un appel explicite ou une reprise ne sont jamais écrasés ; un nouveau
    # rapport du même jour remplace les présences issues d'un rapport.
    stmt = stmt.on_conflict_do_update(
        constraint="uq_presences_eleves_eleve_date",
        set_={"statut": stmt.excluded.statut, "rapport_id": stmt.excluded.rapport_id,
              "teacher_id": stmt.excluded.teacher_id, "session_id": stmt.excluded.session_id,
              "updated_at": stmt.excluded.updated_at},
        where=PresenceEleve.source == "rapport",
    )
    await db.execute(stmt)
    return len(lignes)


async def retirer_pour_rapport(db: AsyncSession, rapport_id: uuid.UUID) -> None:
    await db.execute(delete(PresenceEleve).where(
        PresenceEleve.rapport_id == rapport_id, PresenceEleve.source == "rapport",
    ))


async def enregistrer_appel(
    db: AsyncSession, *, auteur: User, date_jour: date, lignes: Iterable[dict],
    perimetre: Optional[set[uuid.UUID]], session_id: Optional[uuid.UUID],
) -> int:
    """Saisie explicite (source « appel »). `perimetre` = élèves autorisés (None = admin)."""
    lignes = list(lignes)
    for l in lignes:
        if l["statut"] not in STATUTS_PRESENCE:
            raise HTTPException(422, f"statut inconnu : {l['statut']}")
    ids = [l["eleve_id"] for l in lignes]
    connus = set((await db.execute(select(Eleve.id).where(Eleve.id.in_(ids)))).scalars().all())
    inconnus = [str(i) for i in ids if i not in connus]
    if inconnus:
        raise HTTPException(422, {"code": "ELEVES_INCONNUS", "ids": inconnus})
    if perimetre is not None:
        hors = [str(i) for i in ids if i not in perimetre]
        if hors:
            raise HTTPException(403, {"code": "HORS_PERIMETRE", "ids": hors})
    n = 0
    for l in lignes:
        stmt = insert(PresenceEleve).values(
            id=uuid.uuid4(), eleve_id=l["eleve_id"], date_jour=date_jour, session_id=session_id,
            teacher_id=auteur.id if auteur.role == UserRole.enseignant else None,
            statut=l["statut"], source="appel", motif=l.get("motif"), modifie_par_id=auteur.id,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_presences_eleves_eleve_date",
            set_={"statut": stmt.excluded.statut, "source": "appel", "motif": stmt.excluded.motif,
                  "modifie_par_id": stmt.excluded.modifie_par_id, "updated_at": stmt.excluded.updated_at},
        )
        await db.execute(stmt)
        n += 1
    return n
