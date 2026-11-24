"""Endpoints App mobile — Remarques / signalement de problèmes hors application.

L'assistant de signalement de l'app (tuteurs, superviseurs et inspecteurs) permet de
remonter des problèmes qui ne relèvent pas du rapport journalier
(ex: manque de matériel, problème de local…).

Routes :
  POST /app/remarques → soumettre une remarque
  GET  /app/remarques → mes remarques déjà envoyées
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import DB, UtilisateurApp
from app.models.remarque import Remarque

router = APIRouter(prefix="/remarques", tags=["App — Remarques"])

CATEGORIES = {"materiel", "local", "eleves", "securite", "autre", "pedagogie", "application"}
# RET13 : la question pédagogique (superviseur) et l'assistance technique n'ont
# pas le même destinataire. ARED fixe qui répond ; « equipe_programme » = dashboard.
DESTINATAIRES = {"equipe_programme", "superviseur", "support_technique"}
CONTEXTES = {"classe", "rapport", "difficulte", "eleve", "ecran"}   # ecran : signalement RET10


class RemarqueIn(BaseModel):
    categorie: str
    message:   str
    ecole:     Optional[str] = None
    destinataire:  str = "equipe_programme"
    contexte_type: Optional[str] = None
    contexte_id:   Optional[str] = None


class RemarqueOut(BaseModel):
    id:         str
    categorie:  str
    message:    str
    ecole:      Optional[str] = None
    statut:     str
    created_at: str
    destinataire:     str = "equipe_programme"
    contexte_type:    Optional[str] = None
    contexte_id:      Optional[str] = None
    reponse:          Optional[str] = None
    reponse_le:       Optional[str] = None
    reponse_par_nom:  Optional[str] = None


def _out(r: Remarque) -> RemarqueOut:
    return RemarqueOut(
        id=str(r.id), categorie=r.categorie, message=r.message, ecole=r.ecole, statut=r.statut,
        created_at=r.created_at.isoformat(), destinataire=r.destinataire or "equipe_programme",
        contexte_type=r.contexte_type, contexte_id=r.contexte_id, reponse=r.reponse_admin,
        reponse_le=r.reponse_admin_at.isoformat() if r.reponse_admin_at else None,
        reponse_par_nom=r.reponse_par_nom,
    )


@router.post("", response_model=RemarqueOut, status_code=status.HTTP_201_CREATED)
async def create_remarque(body: RemarqueIn, current_user: UtilisateurApp, db: DB) -> RemarqueOut:
    categorie = (body.categorie or "").strip().lower()
    if categorie not in CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Catégorie invalide. Valeurs possibles : {', '.join(sorted(CATEGORIES))}.",
        )
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="Le message ne peut pas être vide.")

    ecole = (body.ecole or "").strip() or None
    if not ecole and current_user.school:
        ecole = current_user.school.name
    if not ecole and current_user.ief:
        ecole = f"IEF {current_user.ief}"  # inspecteur : rattaché à une IEF, pas à une école

    destinataire = (body.destinataire or "equipe_programme").strip().lower()
    if destinataire not in DESTINATAIRES:
        raise HTTPException(status_code=422, detail="Destinataire invalide.")
    if destinataire == "superviseur" and current_user.role.value != "enseignant":
        raise HTTPException(status_code=422, detail="Seul un tuteur peut écrire à son superviseur.")
    contexte_type = (body.contexte_type or "").strip().lower() or None
    if contexte_type is not None and contexte_type not in CONTEXTES:
        raise HTTPException(status_code=422, detail="Objet de l'échange invalide.")
    contexte_id = ((body.contexte_id or "").strip()[:64] or None) if contexte_type else None

    remarque = Remarque(
        id=uuid.uuid4(),
        user_id=current_user.id,
        user_name=current_user.name,
        user_role=current_user.role.value,
        ecole=ecole,
        categorie=categorie,
        message=message,
        destinataire=destinataire,
        contexte_type=contexte_type,
        contexte_id=contexte_id,
    )
    db.add(remarque)
    await db.commit()
    await db.refresh(remarque)

    return _out(remarque)


@router.get("", response_model=list[RemarqueOut])
async def list_my_remarques(current_user: UtilisateurApp, db: DB) -> list[RemarqueOut]:
    rows = (await db.execute(
        select(Remarque)
        .where(Remarque.user_id == current_user.id)
        .order_by(Remarque.created_at.desc())
        .limit(100)
    )).scalars().all()

    return [_out(r) for r in rows]
