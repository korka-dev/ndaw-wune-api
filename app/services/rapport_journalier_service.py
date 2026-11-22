"""Cycle de vie des rapports journaliers (rapport V2, APP-31).

soumis → corrige (v2, v3…) → annule. Aucun rapport n'est supprimé : avant
chaque correction ou annulation, l'état courant est figé dans
`rapport_journalier_versions` avec le motif et l'auteur du changement.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rapport_journalier import RapportJournalier, RapportJournalierVersion
from app.models.user import User
from app.schemas.rapport_journalier import CHAMPS_CORRIGEABLES, RapportAnnulation, RapportCorrection
from app.services.audit_service import journaliser

# Contenu figé dans l'historique (les photos restent sur le rapport).
CHAMPS_FIGES = (
    "date_rapport", "ief", "commune", "ecole", "superviseur", "nom_tuteur", *CHAMPS_CORRIGEABLES,
    "soumis_en_offline", "session_id",
)


def _snapshot(r: RapportJournalier) -> dict:
    out = {}
    for f in CHAMPS_FIGES:
        v = getattr(r, f)
        out[f] = v.isoformat() if hasattr(v, "isoformat") else (str(v) if isinstance(v, uuid.UUID) else v)
    return out


async def charger(db: AsyncSession, rapport_id: uuid.UUID, auteur_id: Optional[uuid.UUID] = None) -> RapportJournalier:
    q = select(RapportJournalier).where(RapportJournalier.id == rapport_id).with_for_update()
    if auteur_id is not None:
        q = q.where(RapportJournalier.teacher_id == auteur_id)
    r = (await db.execute(q)).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rapport introuvable.")
    return r


def _figer(db: AsyncSession, r: RapportJournalier, action: str, motif: str, acteur: User) -> None:
    db.add(RapportJournalierVersion(
        rapport_id=r.id, version=r.version, statut=r.statut, contenu=_snapshot(r),
        action=action, motif=motif, auteur_id=acteur.id,
    ))


def _refuser_si_annule(r: RapportJournalier) -> None:
    if r.statut == "annule":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "RAPPORT_ANNULE", "message": "Ce rapport est annulé : il ne peut plus être modifié."},
        )


async def annuler(db: AsyncSession, r: RapportJournalier, body: RapportAnnulation, acteur: User) -> RapportJournalier:
    if r.statut == "annule":
        return r  # rejeu : idempotent
    _figer(db, r, "annulation", body.motif, acteur)
    r.statut = "annule"
    # Lot L6 : un rapport annulé ne compte plus dans l'assiduité.
    from app.services.presences_service import retirer_pour_rapport
    await retirer_pour_rapport(db, r.id)
    journaliser(db, acteur=acteur, action="annulation", entity="Rapports journaliers",
                description=f"Annulation du rapport du {r.date_rapport:%d/%m/%Y} (version {r.version})",
                cible_type="rapport_journalier", cible_id=r.id, motif=body.motif,
                path=f"/rapports/journalier/{r.id}/annuler")
    r.statut_motif = body.motif
    r.statut_par_id = acteur.id
    r.statut_at = datetime.now(timezone.utc)
    await db.flush()
    return r


async def corriger(db: AsyncSession, r: RapportJournalier, body: RapportCorrection, acteur: User) -> RapportJournalier:
    _refuser_si_annule(r)
    changes = {
        k: v for k, v in body.model_dump(exclude_unset=True).items()
        if k in CHAMPS_CORRIGEABLES and getattr(r, k) != v
    }
    if not changes:
        return r  # rien à corriger (ou rejeu)
    nb = changes.get("nb_absences", r.nb_absences)
    absents = changes.get("absents", r.absents)
    if nb == 0 and absents and absents.strip():
        raise HTTPException(status_code=422, detail="absents renseignés alors que nb_absences vaut 0.")
    _figer(db, r, "correction", body.motif, acteur)
    journaliser(db, acteur=acteur, action="correction", entity="Rapports journaliers",
                description=(f"Correction du rapport du {r.date_rapport:%d/%m/%Y} (v{r.version} → v{r.version + 1}) — "
                             f"champs : {', '.join(sorted(changes))}"),
                cible_type="rapport_journalier", cible_id=r.id, motif=body.motif,
                method="PUT", path=f"/rapports/journalier/{r.id}")
    for k, v in changes.items():
        setattr(r, k, v)
    r.version += 1
    r.statut = "corrige"
    r.statut_motif = body.motif
    r.statut_par_id = acteur.id
    r.statut_at = datetime.now(timezone.utc)
    await db.flush()
    return r


async def historique(db: AsyncSession, rapport_id: uuid.UUID) -> list[RapportJournalierVersion]:
    return list((await db.execute(
        select(RapportJournalierVersion)
        .where(RapportJournalierVersion.rapport_id == rapport_id)
        .order_by(RapportJournalierVersion.version)
    )).scalars().all())
