"""Circuit des demandes d'accompagnement (lot L6, D9.3).

ouverte → assignee (responsable) → en_cours → resolue | abandonnee.
Toute étape écrit un événement daté et attribué. Résoudre ou abandonner
exige un commentaire ; une demande close peut être rouverte (en_cours) avec
un commentaire. Une demande issue d'une difficulté de rapport journalier met
à jour sa résolution (`RapportDifficulteResolution`) : les deux suivis restent
alignés.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.demande_appui import STATUTS_DEMANDE_OUVERTS, DemandeAppui, DemandeAppuiEvenement
from app.models.rapport_journalier import RapportJournalier
from app.models.school import School
from app.models.user import User, UserRole, UserStatus
from app.schemas.visite import DemandeAction, DemandeOut, EvenementOut
from app.services.audit_service import journaliser
from app.services.difficulte_resolution_service import set_resolution
from app.services.supervisor_service import get_supervised_teacher_ids

STATUTS_CLOS = ("resolue", "abandonnee")


def _evenement(db: AsyncSession, d: DemandeAppui, acteur: User, type_: str, **champs) -> None:
    db.add(DemandeAppuiEvenement(demande_id=d.id, type=type_, auteur_id=acteur.id, **champs))


async def verifier_responsable(db: AsyncSession, responsable_id: Optional[uuid.UUID]) -> None:
    if responsable_id is None:
        return
    u = (await db.execute(select(User).where(User.id == responsable_id))).scalar_one_or_none()
    if u is None or u.status != UserStatus.actif or u.archived_at is not None:
        raise HTTPException(422, {"code": "RESPONSABLE_INVALIDE", "message": "Responsable inconnu, inactif ou archivé."})


async def creer(
    db: AsyncSession, acteur: User, *, origine: str, titre: str, description: Optional[str] = None,
    domaine: Optional[str] = None, beneficiaire_id: Optional[uuid.UUID] = None,
    school_id: Optional[uuid.UUID] = None, session_id: Optional[uuid.UUID] = None,
    responsable_id: Optional[uuid.UUID] = None, echeance: Optional[date] = None,
    visite_id: Optional[uuid.UUID] = None, rapport_id: Optional[uuid.UUID] = None,
    libelle_source: Optional[str] = None,
) -> DemandeAppui:
    await verifier_responsable(db, responsable_id)
    if beneficiaire_id is not None and school_id is None:
        school_id = (await db.execute(select(User.school_id).where(User.id == beneficiaire_id))).scalar_one_or_none()
    d = DemandeAppui(
        origine=origine, titre=titre, description=description, domaine=domaine,
        beneficiaire_id=beneficiaire_id, school_id=school_id, session_id=session_id,
        responsable_id=responsable_id, echeance=echeance, visite_id=visite_id, rapport_id=rapport_id,
        libelle_source=libelle_source, statut="assignee" if responsable_id else "ouverte", cree_par_id=acteur.id,
    )
    db.add(d)
    await db.flush()
    _evenement(db, d, acteur, "creation", nouveau_statut=d.statut, responsable_id=responsable_id, commentaire=description)
    return d


async def depuis_rapport(
    db: AsyncSession, acteur: User, rapport: RapportJournalier, type_: str, libelle: str,
    responsable_id: Optional[uuid.UUID], echeance: Optional[date], description: Optional[str],
) -> tuple[DemandeAppui, bool]:
    """Demande issue d'une difficulté ou d'un besoin d'appui d'un rapport.
    Idempotent : la même source ouverte deux fois renvoie la même demande."""
    existante = (await db.execute(select(DemandeAppui).where(
        DemandeAppui.rapport_id == rapport.id, DemandeAppui.origine == type_,
        DemandeAppui.libelle_source == libelle,
    ))).scalar_one_or_none()
    if existante is not None:
        return existante, False
    d = await creer(
        db, acteur, origine=type_, titre=f"{'Difficulté' if type_ == 'difficulte' else 'Besoin d’appui'} : {libelle}"[:300],
        description=description, domaine=libelle if type_ == "besoin" else None,
        beneficiaire_id=rapport.teacher_id, school_id=rapport.ecole_id, session_id=rapport.session_id,
        responsable_id=responsable_id, echeance=echeance, rapport_id=rapport.id, libelle_source=libelle,
    )
    return d, True


async def appliquer_action(db: AsyncSession, d: DemandeAppui, acteur: User, body: DemandeAction) -> DemandeAppui:
    commentaire = (body.commentaire or "").strip() or None
    rien = True

    if body.retirer_responsable or (body.responsable_id and body.responsable_id != d.responsable_id):
        await verifier_responsable(db, body.responsable_id)
        d.responsable_id = None if body.retirer_responsable else body.responsable_id
        _evenement(db, d, acteur, "affectation", responsable_id=d.responsable_id, commentaire=commentaire)
        if d.statut == "ouverte" and d.responsable_id:
            _evenement(db, d, acteur, "statut", ancien_statut="ouverte", nouveau_statut="assignee")
            d.statut = "assignee"
        rien = False

    if body.echeance is not None and body.echeance != d.echeance:
        d.echeance = body.echeance
        _evenement(db, d, acteur, "echeance", commentaire=f"Échéance : {body.echeance:%d/%m/%Y}")
        rien = False

    if body.statut and body.statut != d.statut:
        nouveau = body.statut
        if nouveau in STATUTS_CLOS and not commentaire:
            raise HTTPException(422, {"code": "COMMENTAIRE_REQUIS",
                                      "message": "Un commentaire est requis pour résoudre ou abandonner une demande."})
        if d.statut in STATUTS_CLOS and nouveau not in ("en_cours", "assignee", "ouverte"):
            raise HTTPException(409, {"code": "DEMANDE_CLOSE", "message": "Demande close : rouvrez-la d'abord."})
        if d.statut in STATUTS_CLOS and not commentaire:
            raise HTTPException(422, {"code": "COMMENTAIRE_REQUIS", "message": "Un commentaire est requis pour rouvrir une demande."})
        if nouveau == "assignee" and not d.responsable_id:
            raise HTTPException(422, {"code": "RESPONSABLE_REQUIS", "message": "Affectez un responsable."})
        _evenement(db, d, acteur, "statut", ancien_statut=d.statut, nouveau_statut=nouveau, commentaire=commentaire)
        d.statut = nouveau
        d.cloturee_le = datetime.now(timezone.utc) if nouveau in STATUTS_CLOS else None
        if d.origine == "difficulte" and d.rapport_id and d.libelle_source:
            await set_resolution(db, rapport_id=d.rapport_id, difficulte_label=d.libelle_source,
                                 resolue=nouveau == "resolue", commentaire_resolution=commentaire, current_user=acteur)
        journaliser(db, acteur=acteur, action="statut", entity="Demandes d'appui",
                    description=f"Demande « {d.titre[:80]} » : {_evenement_label(nouveau)}",
                    cible_type="demande_appui", cible_id=d.id, motif=commentaire, method="PATCH",
                    path=f"/demandes-appui/{d.id}")
        rien = False
        commentaire = None  # déjà porté par l'événement de statut

    if commentaire:
        _evenement(db, d, acteur, "commentaire", commentaire=commentaire)
        rien = False

    if rien:
        raise HTTPException(422, "Aucune modification demandée.")
    d.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return d


def _evenement_label(statut: str) -> str:
    return {"ouverte": "rouverte", "assignee": "affectée", "en_cours": "en cours",
            "resolue": "résolue", "abandonnee": "abandonnée"}.get(statut, statut)


async def condition_superviseur(db: AsyncSession, superviseur: User):
    teachers = await get_supervised_teacher_ids(db, superviseur)
    return or_(
        DemandeAppui.beneficiaire_id.in_(teachers),
        DemandeAppui.responsable_id == superviseur.id,
        DemandeAppui.cree_par_id == superviseur.id,
    )


async def serialiser(db: AsyncSession, demandes: list[DemandeAppui], avec_evenements: bool = False) -> list[DemandeOut]:
    if not demandes:
        return []
    ids_users = {i for d in demandes for i in (d.beneficiaire_id, d.responsable_id) if i}
    evts: dict[uuid.UUID, list[DemandeAppuiEvenement]] = {}
    if avec_evenements:
        for e in (await db.execute(select(DemandeAppuiEvenement).where(
            DemandeAppuiEvenement.demande_id.in_([d.id for d in demandes])
        ).order_by(DemandeAppuiEvenement.created_at))).scalars():
            evts.setdefault(e.demande_id, []).append(e)
            ids_users |= {i for i in (e.auteur_id, e.responsable_id) if i}
    noms = dict((await db.execute(select(User.id, User.name).where(User.id.in_(ids_users)))).all()) if ids_users else {}
    ids_ecoles = {d.school_id for d in demandes if d.school_id}
    ecoles = dict((await db.execute(select(School.id, School.name).where(School.id.in_(ids_ecoles)))).all()) if ids_ecoles else {}
    today = date.today()
    out = []
    for d in demandes:
        o = DemandeOut.model_validate(d)
        o.beneficiaire_nom = noms.get(d.beneficiaire_id)
        o.responsable_nom = noms.get(d.responsable_id)
        o.ecole = ecoles.get(d.school_id)
        o.en_retard = bool(d.echeance and d.echeance < today and d.statut in STATUTS_DEMANDE_OUVERTS)
        if avec_evenements:
            o.evenements = []
            for e in evts.get(d.id, []):
                eo = EvenementOut.model_validate(e)
                eo.auteur_nom = noms.get(e.auteur_id)
                eo.responsable_nom = noms.get(e.responsable_id)
                o.evenements.append(eo)
        out.append(o)
    return out


def est_admin(u: User) -> bool:
    return u.role in (UserRole.admin, UserRole.coordonnateur)
