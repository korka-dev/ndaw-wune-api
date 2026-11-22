"""Admin — décisions terrain du lot L7 (retours tuteurs et superviseurs).

- RET07 : propositions de changement de groupe (valider applique le groupe,
  l'ancien reste dans la proposition ; l'assignation de recherche n'est pas touchée) ;
- règles terrain en vigueur (lecture seule : elles se changent dans `.env`).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import DB, AdminUser
from app.models.eleve import Eleve
from app.models.terrain import PropositionGroupe
from app.models.user import User
from app.services.audit_service import journaliser
from app.services.terrain_service import parametres_terrain

router = APIRouter(tags=["Admin — Terrain (L7)"])


class PropositionAdminOut(BaseModel):
    id: uuid.UUID
    eleve_id: uuid.UUID
    eleve_nom: Optional[str] = None
    classe: Optional[str] = None
    domaine: str
    ancien_groupe: Optional[str]
    nouveau_groupe: str
    motif: str
    statut: str
    propose_par: Optional[str] = None
    motif_refus: Optional[str] = None
    decide_le: Optional[datetime] = None
    created_at: datetime


class DecisionIn(BaseModel):
    decision: Literal["valider", "refuser"]
    motif: Optional[str] = None


async def _sortie(db, rows: list[PropositionGroupe]) -> list[PropositionAdminOut]:
    eleves = {e.id: e for e in (await db.execute(select(Eleve).where(Eleve.id.in_({r.eleve_id for r in rows})))).scalars()} if rows else {}
    ids = {r.propose_par_id for r in rows if r.propose_par_id}
    noms = dict((await db.execute(select(User.id, User.name).where(User.id.in_(ids)))).all()) if ids else {}
    out = []
    for r in rows:
        e = eleves.get(r.eleve_id)
        out.append(PropositionAdminOut(
            id=r.id, eleve_id=r.eleve_id,
            eleve_nom=(f"{e.prenom} {e.nom}".strip() if e and e.prenom else (e.nom if e else None)),
            classe=e.classe if e else None, domaine=r.domaine, ancien_groupe=r.ancien_groupe,
            nouveau_groupe=r.nouveau_groupe, motif=r.motif, statut=r.statut,
            propose_par=noms.get(r.propose_par_id), motif_refus=r.motif_refus, decide_le=r.decide_le,
            created_at=r.created_at,
        ))
    return out


@router.get("/propositions-groupe", response_model=list[PropositionAdminOut])
async def lister(db: DB, _: AdminUser, statut: Optional[str] = None) -> list[PropositionAdminOut]:
    stmt = select(PropositionGroupe)
    if statut:
        stmt = stmt.where(PropositionGroupe.statut == statut)
    rows = (await db.execute(stmt.order_by(PropositionGroupe.created_at.desc()).limit(500))).scalars().all()
    return await _sortie(db, list(rows))


@router.post("/propositions-groupe/{proposition_id}/decision", response_model=PropositionAdminOut)
async def decider(proposition_id: uuid.UUID, body: DecisionIn, db: DB, admin: AdminUser) -> PropositionAdminOut:
    p = await db.get(PropositionGroupe, proposition_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Proposition introuvable.")
    if p.statut != "proposee":
        raise HTTPException(status_code=409, detail="Cette proposition a déjà été traitée.")
    if body.decision == "refuser":
        if not (body.motif or "").strip():
            raise HTTPException(status_code=422, detail="Indiquez le motif du refus.")
        p.statut, p.motif_refus = "refusee", body.motif.strip()
    else:
        eleve = await db.get(Eleve, p.eleve_id, with_for_update=True)
        if eleve is None:
            raise HTTPException(status_code=409, detail="L'élève n'existe plus.")
        actuel = eleve.groupe_lecture if p.domaine == "lecture" else eleve.groupe_maths
        if (actuel or None) != (p.ancien_groupe or None):
            raise HTTPException(status_code=409, detail="Le groupe de l'élève a changé depuis la proposition : refusez-la ou demandez-en une nouvelle.")
        if p.domaine == "lecture":
            eleve.groupe_lecture = p.nouveau_groupe
        else:
            eleve.groupe_maths = p.nouveau_groupe
        p.statut = "validee"
    p.decide_par_id = admin.id
    p.decide_le = datetime.now(timezone.utc)
    journaliser(db, acteur=admin, action="update", entity="Propositions de groupe",
                path=f"/api/v1/admin/propositions-groupe/{proposition_id}/decision",
                description=(f"Proposition de groupe {p.domaine} {'validée' if body.decision == 'valider' else 'refusée'} : "
                             f"{p.ancien_groupe or 'aucun'} → {p.nouveau_groupe}"),
                cible_type="eleve", cible_id=p.eleve_id, motif=body.motif)
    await db.commit()
    await db.refresh(p)
    return (await _sortie(db, [p]))[0]


@router.get("/terrain/parametres")
async def regles_en_vigueur(_: AdminUser) -> dict:
    """Règles terrain transmises aux téléphones (modifiables dans `.env` du serveur)."""
    return parametres_terrain()
