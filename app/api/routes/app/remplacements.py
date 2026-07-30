"""Endpoint App mobile — Remplacement d'élève par le tuteur.

Route :
  POST /app/remplacements

Effet immédiat : le nouvel élève est créé actif dans la classe du tuteur,
l'ancien élève (s'il est identifié) passe en statut "inactif". L'action est
aussi journalisée dans l'historique des modifications (audit log) afin de
remonter sur le dashboard admin, puisque cette route n'est pas sous /admin/
(seul préfixe surveillé automatiquement par le middleware d'audit).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.core.deps import DB, TeacherUser
from app.models.audit_log import AuditLog
from app.models.eleve import Eleve
from app.models.eleve_remplacement import EleveRemplacement
from app.schemas.eleve_remplacement import EleveRemplacementCreate, EleveRemplacementResponse

router = APIRouter(prefix="/remplacements", tags=["App — Remplacement élève"])


def _split_nom_prenom(full_name: str) -> tuple[str, str | None]:
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0], None
    return parts[-1], " ".join(parts[:-1])


@router.post("", response_model=EleveRemplacementResponse, status_code=201)
async def create_remplacement(
    body: EleveRemplacementCreate, current_user: TeacherUser, db: DB
) -> EleveRemplacementResponse:
    ancien: Eleve | None = None
    if body.ancien_eleve_id is not None:
        ancien = (
            await db.execute(select(Eleve).where(Eleve.id == body.ancien_eleve_id))
        ).scalar_one_or_none()
        if ancien is None:
            raise HTTPException(status_code=404, detail="Élève à remplacer introuvable.")
        if ancien.school_id != current_user.school_id:
            raise HTTPException(status_code=403, detail="Cet élève n'appartient pas à votre école.")
        ancien.statut = "inactif"

    classe = ancien.classe if ancien else body.classe
    nom, prenom = _split_nom_prenom(body.nouveau_eleve_nom)

    nouveau = Eleve(
        id=uuid.uuid4(),
        nom=nom,
        prenom=prenom,
        classe=classe,
        statut="actif",
        school_id=current_user.school_id,
    )
    db.add(nouveau)
    await db.flush()

    remplacement = EleveRemplacement(
        ancien_eleve_id=ancien.id if ancien else None,
        ancien_eleve_nom=body.ancien_eleve_nom or (ancien.nom if ancien else None),
        nouveau_eleve_id=nouveau.id,
        nouveau_eleve_nom=body.nouveau_eleve_nom.strip(),
        motif=body.motif.strip(),
        teacher_id=current_user.id,
        school_id=current_user.school_id,
        classe=classe,
    )
    db.add(remplacement)

    ancien_label = remplacement.ancien_eleve_nom or "élève inconnu"
    db.add(AuditLog(
        user_id=current_user.id,
        user_name=current_user.name,
        user_role=current_user.role.value,
        action="create",
        entity="Remplacements élève",
        method="POST",
        path="/api/v1/app/remplacements",
        description=(
            f"Remplacement — {ancien_label} → {body.nouveau_eleve_nom.strip()} "
            f"(classe {classe}) — motif : {body.motif.strip()}"
        ),
    ))

    await db.commit()
    await db.refresh(remplacement)
    return remplacement
