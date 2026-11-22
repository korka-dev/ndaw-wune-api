"""App tuteur — propositions de changement de groupe (lot L7, RET07).

Le tuteur propose ; l'équipe programme décide (dashboard). L'ancien groupe
et la date sont conservés ; l'assignation de recherche (traitement/contrôle)
n'est jamais modifiée par cette opération.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import DB, TeacherUser
from app.models.terrain import PropositionGroupe
from app.services.audit_service import journaliser
from app.services.terrain_service import eleve_du_tuteur

router = APIRouter(prefix="/propositions-groupe", tags=["App — Propositions de groupe"])


class PropositionIn(BaseModel):
    eleve_id: uuid.UUID
    domaine: Literal["lecture", "maths"]
    nouveau_groupe: str = Field(..., min_length=1, max_length=30)
    motif: str = Field(..., min_length=3, max_length=1000)


class PropositionOut(BaseModel):
    id: uuid.UUID
    eleve_id: uuid.UUID
    domaine: str
    ancien_groupe: Optional[str]
    nouveau_groupe: str
    motif: str
    statut: str
    motif_refus: Optional[str] = None
    decide_le: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.post("", response_model=PropositionOut, status_code=status.HTTP_201_CREATED)
async def proposer(body: PropositionIn, current_user: TeacherUser, db: DB) -> PropositionOut:
    if not settings.PROPOSITION_GROUPE_ACTIVE:
        raise HTTPException(status_code=403, detail="Les propositions de changement de groupe ne sont pas ouvertes.")
    eleve = await eleve_du_tuteur(db, current_user, body.eleve_id)
    ancien = eleve.groupe_lecture if body.domaine == "lecture" else eleve.groupe_maths
    nouveau = body.nouveau_groupe.strip()
    if (ancien or "").strip().lower() == nouveau.lower():
        raise HTTPException(status_code=422, detail="L'élève est déjà dans ce groupe.")
    deja = (await db.execute(select(PropositionGroupe.id).where(
        PropositionGroupe.eleve_id == eleve.id, PropositionGroupe.domaine == body.domaine,
        PropositionGroupe.statut == "proposee").limit(1))).scalar_one_or_none()
    if deja:
        raise HTTPException(status_code=409, detail="Une proposition est déjà en attente pour cet élève et ce domaine.")
    p = PropositionGroupe(eleve_id=eleve.id, domaine=body.domaine, ancien_groupe=ancien,
                          nouveau_groupe=nouveau, motif=body.motif.strip(), propose_par_id=current_user.id)
    db.add(p)
    journaliser(db, acteur=current_user, action="create", entity="Propositions de groupe",
                path="/api/v1/app/propositions-groupe",
                description=f"Proposition de groupe {body.domaine} : {ancien or 'aucun'} → {nouveau}",
                cible_type="eleve", cible_id=eleve.id, motif=body.motif)
    await db.commit()
    await db.refresh(p)
    return PropositionOut.model_validate(p)


@router.get("", response_model=list[PropositionOut])
async def mes_propositions(current_user: TeacherUser, db: DB) -> list[PropositionOut]:
    rows = (await db.execute(
        select(PropositionGroupe).where(PropositionGroupe.propose_par_id == current_user.id)
        .order_by(PropositionGroupe.created_at.desc()).limit(200)
    )).scalars().all()
    return [PropositionOut.model_validate(r) for r in rows]
